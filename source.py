"""영상 출처 구분 — 보관함 영상마다 풋살사관학교(우리 채널) · 다른 채널 · 내 촬영본을 기억한다.

- 저장: 보관함 폴더의 sources.json (`files` 파일 이름별 · `ids` 유튜브 영상 id별 기록). 임시 파일에 다 쓴 뒤 바꿔 끼움
- 받을 때(core.download) yt-dlp 정보에서 채널을 기록하고, 소재 찾기에서 고른 영상의 출처(hints)를 먼저 받아 둠
- 예전에 받은 영상: 우리 채널 목록 기억(channel_cache.json) → 인터넷으로 영상 정보만 조회 (뒤에서 천천히 · 멈출 수 있음)
- 촬영 원본·묶은 영상·유튜브 이름 꼴이 아닌 파일은 '내 촬영본'
- 사용자가 직접 고른 출처(manual)가 자동 판단보다 우선
- 다른 채널은 채널별로 묶음(`channels` 기록): 열쇠는 채널 id(UC…) · 없으면 @핸들 · 그것도 없으면 채널 이름.
  이름이 바뀌어도 채널 id 가 같으면 한 묶음(이름은 마지막에 본 것) · 채널마다 고정 색 번호(PALETTE 중 하나)
"""
import json
import os
import re
import socket
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import core

KINDS = ("own", "other", "footage")  # 풋살사관학교 · 다른 채널 · 내 촬영본 (모르면 "unknown")
STORE = "sources.json"
YT_NAME = re.compile(r"^\d{8}_([A-Za-z0-9_-]{11})_")  # core.download 의 파일 이름 꼴 (받은 날짜_영상id_제목)
LOOKUP_GAP = 2.0       # 영상 정보 조회 사이 쉬는 시간(초) — YouTube 에 몰아서 묻지 않게
LOOKUP_RETRY = 86400   # 조회에 실패한 영상은 하루 뒤에 다시
LOOKUP_MAX = 40        # 보관함을 한 번 열 때 조회하는 최대 영상 수
LOOKUP_FAILS = 3       # 연달아 이만큼 실패하면 (막혔거나 끊김) 이번에는 그만
LOOKUP_PAUSE = 6 * 3600  # 그렇게 멈췄거나 YouTube 가 막았으면 이 시간 동안은 조회하지 않음 (영상 받기용 쿠키가 필요할 때 더 찌르지 않게)
GONE = re.compile(r"unavailable|private|removed|deleted|terminated|members|no longer|not available|copyright", re.I)  # 영상 하나만의 실패
PAUSE_KEY = "_pause"     # lookup 기록 안의 '잠시 멈춤' 칸 (영상 id 는 11글자라 겹치지 않음)
TITLE_MIN = 6          # 제목으로 우리 채널 목록과 맞춰 볼 때 최소 글자 수 (짧으면 엉뚱한 영상과 겹침)
READ_TRIES = 5         # sources.json 이 잠겨 있을 때 다시 읽어 보는 횟수
TEXT_MAX = 300         # 화면에서 받은 글자 길이 한도
PALETTE = 8           # 다른 채널 배지 색 가짓수 (ui.html 의 .src.c0 … .src.c7)
INFO_KEYS = ("id", "channel", "uploader", "channel_id", "channel_url", "uploader_id", "uploader_url", "webpage_url")  # yt-dlp 정보에서 쓰는 것

_LOCK = threading.RLock()
_CACHE = {"key": None, "data": None}  # 1초마다 부르는 /api/state 가 매번 파일을 읽지 않게 (경로·수정 시각이 같으면 그대로)
_BF = {"thread": None, "stop": None, "offline": False}


def store_path():
    return core.VIDEOS / STORE


def _empty():
    return {"version": 1, "files": {}, "ids": {}, "own": {"ids": [], "handles": []}, "lookup": {}, "channels": {}}


def _valid(d):
    return isinstance(d, dict) and all(isinstance(d.get(k, {}), dict) for k in ("files", "ids", "own", "lookup", "channels"))


def _clean(d):
    """기록 안의 잘못된 칸(사람이 고쳤거나 반쯤 동기화된 것)은 빼고 나머지는 살림 → 영상 하나 때문에 전체 출처가 사라지지 않게."""
    for k in ("files", "ids", "channels", "lookup"):
        d[k] = {n: r for n, r in (d.get(k) or {}).items() if isinstance(n, str) and isinstance(r, dict)}
    own = d.get("own") or {}
    d["own"] = dict(own, **{k: [x for x in own.get(k) or [] if isinstance(x, str)] if isinstance(own.get(k), list) else []
                            for k in ("ids", "handles")})
    return d


class StoreBusy(OSError):
    """sources.json 을 지금은 읽을 수 없음 (백신·OneDrive 가 잡고 있음 등) — 깨진 것과 달리 덮어쓰면 안 됨."""


def load(strict=False):
    """sources.json (읽기 전용으로 씀 · 고칠 때는 _update).
    - 내용이 깨졌으면(JSON·형식 오류) .bad 로 남겨 두고 빈 기록으로 계속
    - 잠깐 읽을 수 없으면(잠김 등) 몇 번 다시 해 보고, 그래도 안 되면 빈 기록을 기억해 두지 않음.
      strict 면 StoreBusy 를 냄 → _update 가 좋은 파일을 빈 기록으로 덮어쓰지 않게"""
    p = store_path()
    with _LOCK:
        try:
            st = p.stat()
            key = (str(p), st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            key = (str(p), None, None)
        except OSError as e:
            if strict:
                raise StoreBusy(f"영상 출처 기록을 읽지 못했어요 · {e}") from e
            return _empty()
        if _CACHE["key"] == key and _CACHE["data"] is not None:
            return _CACHE["data"]
        data = _empty()
        if key[1] is not None:
            raw, err = None, None
            for i in range(READ_TRIES):
                try:
                    raw = p.read_bytes()
                    break
                except FileNotFoundError:  # 그사이 지워짐 → 없는 것과 같음 (다음 stat 이 달라서 다시 읽음)
                    return data
                except OSError as e:  # 잠김: 조금 뒤 다시
                    err = e
                    time.sleep(0.05 * (i + 1))
            if raw is None:
                if strict:
                    raise StoreBusy(f"영상 출처 기록을 읽지 못했어요 · {err}") from err
                return data  # 기억해 두지 않음 → 다음에 다시 읽음
            try:
                d = json.loads(raw.decode("utf-8"))
                if not _valid(d):
                    raise ValueError("형식이 달라요")
                data.update(_clean(d))
            except ValueError:
                try:  # 사람이 살펴볼 수 있게 깨진 파일을 남겨 둠 (다음 저장이 새 파일로 바꿔 끼움)
                    p.with_name(STORE + ".bad").write_bytes(raw)
                except OSError:
                    pass
        _CACHE.update(key=key, data=data)
        return data


def _write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    for i in range(20):  # Windows: 백신·OneDrive 가 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


def _update(fn):
    """기록을 읽어 fn(data) 로 고친 뒤 저장 (한 번에 하나씩)."""
    with _LOCK:
        data = json.loads(json.dumps(load(strict=True)))  # 읽지 못했으면 StoreBusy (덮어쓰지 않음)
        out = fn(data)
        _write(store_path(), data)
        _CACHE["key"] = None
        return out


# ---------- 채널 알아보기 ----------

def _url_key(url):
    """채널 주소 → ("id", "UC…") / ("handle", "@이름") / ("path", "/c/이름" · "/user/이름") · 모르면 None.
    /channel/UC… · /@handle · /c/name · /user/name · 옛 youtube.com/name 꼴과 앞뒤 공유 꼬리를 모두 받음."""
    url = str(url or "").strip()
    if not url:
        return None
    if re.fullmatch(r"UC[\w-]{22}", url):
        return ("id", url)
    if not re.match(r"https?://", url, re.I):
        if re.match(r"(?:(?:www|m)\.)?youtube\.com/", url, re.I):
            url = "https://" + url
        else:
            url = "https://www.youtube.com/" + (url if url.startswith("@") else "@" + url)
    path = unquote(urlsplit(url).path).rstrip("/")
    m = re.match(r"/channel/(UC[\w-]{22})(?:/|$)", path)
    if m:
        return ("id", m.group(1))
    m = re.match(r"/(@[^/]+)", path)
    if m:
        return ("handle", m.group(1).lower())
    m = re.match(r"/(c|user)/([^/]+)", path, re.I)
    if m:
        return ("path", f"/{m.group(1).lower()}/{m.group(2).lower()}")
    m = re.match(r"/([^/@]+)(?:/(?:videos|shorts|streams|featured|about))?$", path)
    if m and m.group(1).lower() not in ("watch", "playlist", "shorts", "live", "embed", "results", "feed", "channel"):
        return ("path", "/c/" + m.group(1).lower())  # 옛 맞춤 주소 youtube.com/이름 = /c/이름
    return None


def _keys(meta):
    """기록(채널 정보) 하나에서 채널을 가리키는 열쇠들."""
    out = set()
    if isinstance(meta.get("channelId"), str) and meta["channelId"]:
        out.add(("id", meta["channelId"]))
    u = meta.get("uploaderId")
    if isinstance(u, str) and u:
        k = _url_key(u) if u.startswith("@") or re.fullmatch(r"UC[\w-]{22}", u) else ("path", "/user/" + u.lower())
        if k:
            out.add(k)
    for f in ("channelUrl", "uploaderUrl"):
        k = _url_key(meta.get(f)) if meta.get(f) else None
        if k:
            out.add(k)
    return out


def own_keys(data=None):
    """우리 채널 열쇠: 설정의 channel_url + 우리 채널 목록을 불러올 때 알게 된 채널 id·핸들."""
    data = data or load()
    out = set()
    k = _url_key(core.CONFIG.get("channel_url"))
    if k:
        out.add(k)
    own = data.get("own") or {}
    out |= {("id", x) for x in own.get("ids") or [] if isinstance(x, str)}
    out |= {("handle", x.lower()) for x in own.get("handles") or [] if isinstance(x, str)}
    return out


def classify(meta, data=None):
    """채널 정보로 "own"(우리 채널) / "other"(다른 채널) · 채널 정보가 없으면 None."""
    keys = _keys(meta or {})
    if keys & own_keys(data):
        return "own"
    if keys or (meta or {}).get("channel"):
        return "other"
    return None


def meta_of(info, channel_only=False):
    """yt-dlp 정보(dict)에서 남길 것만 (camelCase · 글자만)."""
    pick = {"channel": info.get("channel") or info.get("uploader"), "channelId": info.get("channel_id"),
            "channelUrl": info.get("channel_url"), "uploaderId": info.get("uploader_id"), "uploaderUrl": info.get("uploader_url")}
    if not channel_only:
        pick.update(videoId=info.get("id"), url=info.get("webpage_url"))
    return {k: v[:TEXT_MAX] for k, v in pick.items() if isinstance(v, str) and v}


# ---------- 다른 채널 채널별로 묶기 ----------

def _norm_name(n):
    """채널 이름 비교용 (대소문자·띄어쓰기 무시)."""
    return re.sub(r"\s+", "", str(n or "")).lower()


def _handle(meta):
    u = meta.get("uploaderId")
    if isinstance(u, str) and u.startswith("@") and len(u) > 1:
        return u.lower()
    k = _url_key(meta.get("uploaderUrl")) if meta.get("uploaderUrl") else None
    return k[1] if k and k[0] == "handle" else None


def _cid(meta):
    c = meta.get("channelId")
    if isinstance(c, str) and re.fullmatch(r"UC[\w-]{22}", c):
        return c
    k = _url_key(meta.get("channelUrl")) if meta.get("channelUrl") else None
    return k[1] if k and k[0] == "id" else None


def channel_key(meta, data=None):
    """채널 묶음 열쇠 (기록하지 않음): 채널 id → 이미 아는 채널의 @핸들·이름 → "h:@핸들" → "n:이름" · 모르면 None."""
    data = data or load()
    chans = data.get("channels") or {}
    cid, handle, name = _cid(meta), _handle(meta), _norm_name(meta.get("channel"))
    if cid:
        return cid
    if handle:
        for k, c in chans.items():
            if handle in (c.get("handles") or []):
                return k
    if name:
        for k, c in chans.items():
            if name in {_norm_name(x) for x in c.get("names") or []}:
                return k
    if handle:
        return "h:" + handle
    return "n:" + name if name else None


def _pick_color(key, chans):
    """채널마다 고정 색 번호: 열쇠로 정한 자리 → 이미 다른 채널이 쓰면 가장 덜 쓴 색."""
    used = [0] * PALETTE
    for k, c in chans.items():
        if k != key and isinstance(c.get("color"), int) and 0 <= c["color"] < PALETTE:
            used[c["color"]] += 1
    first = zlib.crc32(key.encode("utf-8")) % PALETTE
    return min(((first + i) % PALETTE for i in range(PALETTE)), key=lambda i: (used[i], (i - first) % PALETTE))


def _register(data, meta):
    """다른 채널 하나를 channels 기록에 넣고(이름은 마지막 것으로) 열쇠를 돌려줌.
    채널 id 를 처음 알게 된 채널이 전에 이름·핸들로만 묶여 있었으면 그 묶음을 id 열쇠로 옮김 (색·직접 고른 것 유지)."""
    chans = data.setdefault("channels", {})
    key = channel_key(meta, data)
    if not key:
        return None
    cid, handle, name = _cid(meta), _handle(meta), str(meta.get("channel") or "").strip()[:TEXT_MAX]
    if cid and cid not in chans:
        old = channel_key({k: v for k, v in meta.items() if k not in ("channelId", "channelUrl")}, data)
        if old and old in chans and not re.fullmatch(r"UC[\w-]{22}", old):
            chans[cid] = chans.pop(old)
            for box in (data["files"], data["ids"]):
                for rec in box.values():
                    for f in ("channelKey", "manualChannel"):
                        if rec.get(f) == old:
                            rec[f] = cid
    c = chans.setdefault(key, {})
    if name:
        c["name"] = name
        c["names"] = ([n for n in c.get("names") or [] if n != name] + [name])[-10:]
    if handle and handle not in (c.get("handles") or []):
        c["handles"] = ((c.get("handles") or []) + [handle])[-10:]
    url = meta.get("channelUrl") or meta.get("uploaderUrl")
    if isinstance(url, str) and url:
        c["url"] = url[:TEXT_MAX]
    if not isinstance(c.get("color"), int):
        c["color"] = _pick_color(key, chans)
    return key


def channel_view(key, meta=None, data=None):
    """화면용 {channelKey, channel, color} — 이름은 channels 기록의 마지막 이름 (없으면 영상 기록의 이름)."""
    data = data or load()
    c = (data.get("channels") or {}).get(key) or {}
    name = c.get("name") or (meta or {}).get("channel") or ""
    color = c["color"] if isinstance(c.get("color"), int) else zlib.crc32(key.encode("utf-8")) % PALETTE
    out = {"channelKey": key, "color": color}
    if name:
        out["channel"] = name
    return out


def channels(data=None):
    """알고 있는 다른 채널 목록 [{channelKey, channel, color}] (보관함에서 출처를 직접 고를 때)."""
    data = data or load()
    return [channel_view(k, None, data) for k in (data.get("channels") or {})]


def video_id(name):
    m = YT_NAME.match(str(name or ""))
    return m.group(1) if m else None


# ---------- 보관함 영상 하나의 출처 ----------

def _record(name, data):
    vid = video_id(name)
    frec = data["files"].get(name) or {}
    if frec.get("kind") == "footage" and frec.get("how") in ("local", "bundle"):
        return vid, dict(frec)  # 직접 넣은·묶은 파일: 이름이 유튜브 꼴이어도 그 영상 id 기록은 쓰지 않음
    rec = dict(data["ids"].get(vid) or {}) if vid else {}
    rec.update(frec)  # 파일 이름 기록이 먼저 (이름을 바꿨으면 영상 id 기록으로)
    return vid, rec


def _failed_recently(vid, data, now=None):
    f = (data.get("lookup") or {}).get(vid) or {}
    return bool(f.get("t")) and (now or time.time()) - f["t"] < LOOKUP_RETRY


def describe(name, data=None, running=None):
    """화면에 보여 줄 출처 {kind, auto, manual, channel?, channelUrl?, channelKey?, color?, checking?}
    kind 는 KINDS 또는 "unknown" · 다른 채널이면 channelKey(채널 묶음 열쇠)·color(색 번호)·channel(마지막 이름)."""
    data = data or load()
    vid, rec = _record(name, data)
    auto = classify(rec, data) or (rec.get("kind") if rec.get("kind") in KINDS else None)
    if auto is None and not vid:
        auto = "footage"  # 유튜브에서 받은 이름 꼴이 아님 = 직접 넣은 촬영 원본
    manual = rec.get("manual") if rec.get("manual") in KINDS else None
    out = {"kind": manual or auto or "unknown", "auto": auto or "unknown", "manual": bool(manual)}
    for k in ("channel", "channelUrl"):
        if isinstance(rec.get(k), str) and rec[k]:
            out[k] = rec[k]
    if out["kind"] == "other":
        key = rec.get("manualChannel") if manual == "other" and rec.get("manualChannel") else None
        if key:
            out.pop("channelUrl", None)  # 직접 고른 채널: 영상 정보의 채널 주소는 다른 채널 것일 수 있음
        else:
            key = rec.get("channelKey") or channel_key(rec, data)
        if key:
            out.update(channel_view(key, rec, data))
            url = ((data.get("channels") or {}).get(key) or {}).get("url")
            if url and "channelUrl" not in out:
                out["channelUrl"] = url
    if out["kind"] == "unknown":
        out["checking"] = backfill_running() if running is None else running
    elif not manual and auto == "own" and rec.get("how") == "guess":
        out["guess"] = True  # 제목으로 짐작한 풋살사관학교 (인터넷으로 확인되면 바뀜)
    return out


def annotate(videos):
    """core.local_videos() 목록에 "source" 를 붙여 돌려줌 (기록을 못 읽어도 목록은 그대로)."""
    try:
        data, running = load(), backfill_running()
    except Exception:  # noqa: BLE001 — 출처는 곁가지: 실패해도 보관함 목록은 보여야 함
        return videos
    for v in videos:
        try:  # 영상 하나의 기록이 이상해도 나머지 영상의 출처는 보여 줌
            v["source"] = describe(v["name"], data, running)
        except Exception:  # noqa: BLE001
            v["source"] = {"kind": "unknown", "auto": "unknown", "manual": False, "checking": False}
    return videos


def summary(videos):
    """보관함 고르기 칩용 개수: {all, own, other, footage, unknown, channels: [{channelKey, channel, color, count}]}.
    channels 는 다른 채널을 채널 묶음 열쇠별로 센 것 (많은 순 · 같으면 이름 순) · 이름 모르는 다른 채널은 channelKey ""."""
    out = {"all": 0, "own": 0, "other": 0, "footage": 0, "unknown": 0, "channels": []}
    groups = {}
    try:
        for v in videos:
            s = v.get("source") or {"kind": "unknown"}
            k = s.get("kind") if s.get("kind") in out else "unknown"
            out["all"] += 1
            out[k] += 1
            if k == "other":
                key = s.get("channelKey") or ""
                g = groups.setdefault(key, {"channelKey": key, "channel": s.get("channel") or "", "color": s.get("color"), "count": 0})
                g["count"] += 1
    except Exception:  # noqa: BLE001 — 곁가지
        pass
    out["channels"] = sorted(groups.values(), key=lambda g: (-g["count"], not g["channelKey"], g["channel"]))
    return out


def set_manual(name, kind, channel=None, channel_key_=None):
    """사용자가 고른 출처 저장 (자동 판단보다 우선) · kind 가 "auto"·None 이면 자동으로 되돌림.
    다른 채널이면 채널도 고를 수 있음: channel_key_(이미 아는 채널) 또는 channel(새로 적은 이름 · 아는 이름이면 그 채널로).
    둘 다 없으면 영상 정보의 채널을 그대로 씀."""
    if kind in ("auto", "", None):
        kind = None
    elif kind not in KINDS:
        raise ValueError("출처는 풋살사관학교·다른 채널·내 촬영본 중에서 골라 주세요")
    if Path(name).suffix.lower() not in core.VIDEO_EXTS or not (core.VIDEOS / name).is_file():
        raise FileNotFoundError(name)
    channel = re.sub(r"\s+", " ", str(channel or "")).strip()[:80]
    channel_key_ = str(channel_key_ or "").strip()
    if kind != "other":
        channel, channel_key_ = "", ""
    elif channel_key_ and channel_key_ not in (load().get("channels") or {}):
        raise ValueError("그 채널을 찾지 못했어요. 채널 이름을 적어 주세요")
    vid = video_id(name)
    # 직접 넣은·묶은 파일은 파일 이름 기록만 (같은 영상 id 의 다른 파일에 번지지 않게)
    frec = load()["files"].get(name) or {}
    only_file = frec.get("how") in ("local", "bundle")

    def fn(data):
        ck = channel_key_ or (_register(data, {"channel": channel}) if channel else None)
        for box, key in ((data["files"], name), (data["ids"], None if only_file else vid)):
            if key is None:
                continue
            rec = box.setdefault(key, {})
            if kind:
                rec["manual"] = kind
            else:
                rec.pop("manual", None)
            if ck:
                rec["manualChannel"] = ck
            else:
                rec.pop("manualChannel", None)
    _update(fn)
    return describe(name)


def mark_footage(name, how="local"):
    """촬영 원본 넣기·촬영본 묶기로 생긴 파일 → 내 촬영본 (실패해도 그 작업은 계속)."""
    try:
        _update(lambda data: data["files"].setdefault(name, {}).update(kind="footage", how=how, saved=time.strftime("%Y-%m-%d %H:%M")))
    except (OSError, ValueError):
        pass


def _merge(rec, meta, kind, how):
    rec.update(meta)
    if how != "guess" and rec.get("how") == "guess" and not kind:
        rec.pop("kind", None)  # 짐작은 확실한 정보가 오면 버림
    if kind:
        rec["kind"] = kind
    rec["how"] = how
    rec["saved"] = time.strftime("%Y-%m-%d %H:%M")


def remember_hints(hints):
    """소재 찾기에서 고른 영상의 출처 {영상id: {kind, channel, channelId, channelUrl}} 를 받기 전에 먼저 기록.
    받는 동안 yt-dlp 정보가 오면 그쪽이 덮어씀 · 이미 받아 둔 영상(정보가 있음)은 그대로."""
    if not isinstance(hints, dict) or not hints:
        return

    def fn(data):
        for vid, h in list(hints.items())[:500]:
            if not (isinstance(vid, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) and isinstance(h, dict)):
                continue
            rec = data["ids"].setdefault(vid, {})
            if rec.get("how") in ("download", "lookup"):
                continue
            meta = {k: str(h[k])[:TEXT_MAX] for k in ("channel", "channelId", "channelUrl", "uploaderId", "uploaderUrl")
                    if isinstance(h.get(k), str) and h[k]}
            kind = h.get("kind") if h.get("kind") in ("own", "other") else None
            _merge(rec, meta, kind, "listing")
            if kind == "other":
                ck = _register(data, meta)
                if ck:
                    rec["channelKey"] = ck
    try:
        _update(fn)
    except (OSError, ValueError):
        pass


def _find_file(vid):
    for p in sorted(core.VIDEOS.glob(f"*_{vid}_*")):
        if p.suffix.lower() in core.VIDEO_EXTS and video_id(p.name) == vid:
            return p.name
    return None


def record_download(infos, how="download"):
    """받은 영상의 yt-dlp 정보 [dict] → 영상 id·파일 이름별로 채널 정보 기록 (core.download 가 영상마다 부름)."""
    metas = [meta_of(i) for i in infos if isinstance(i, dict) and i.get("id")]
    if not metas:
        return

    def fn(data):
        for m in metas:
            vid = m["videoId"]
            kind = classify(m, data)
            rec = data["ids"].setdefault(vid, {})
            _merge(rec, m, kind, how)
            ck = _register(data, m) if kind == "other" else None
            if ck:
                rec["channelKey"] = ck
            else:
                rec.pop("channelKey", None)
            f = _find_file(vid)
            if f:
                frec = data["files"].setdefault(f, {})
                if frec.get("how") in ("local", "bundle"):
                    continue  # 같은 이름 꼴로 직접 넣은 파일은 그대로 내 촬영본
                _merge(frec, m, kind or rec.get("kind"), how)
                if ck:
                    frec["channelKey"] = ck
                else:
                    frec.pop("channelKey", None)
                if rec.get("manualChannel") and not frec.get("manualChannel") and not frec.get("manual"):
                    frec["manualChannel"] = rec["manualChannel"]
                if rec.get("manual") and not frec.get("manual"):
                    frec["manual"] = rec["manual"]
    _update(fn)


def annotate_listing(rows, url=None):
    """소재 찾기 목록에 "source" {kind, channel} 를 붙임 (같은 dict 를 고침).
    비워 두고 불러온 목록(우리 채널)이면 그 채널 id·핸들을 우리 채널로 배워 둠 → 주소를 @핸들·/c/ 로 넣어 둔 경우에도 맞춤."""
    import hooks
    own_listing = not (url or "").strip()
    try:
        if own_listing:
            ids = {r["channelId"] for r in rows if isinstance(r.get("channelId"), str) and r["channelId"]}
            handles = {r["uploaderId"].lower() for r in rows if isinstance(r.get("uploaderId"), str) and r["uploaderId"].startswith("@")}

            def learn(data):
                own = data["own"]
                own["ids"] = sorted(set(own.get("ids") or []) | ids)[:20]
                own["handles"] = sorted(set(own.get("handles") or []) | handles)[:20]
            if ids - set(load()["own"].get("ids") or []) or handles - set(load()["own"].get("handles") or []):
                _update(learn)
        data = load()
        url_own = own_listing or hooks._own_channel(url) or bool(_url_key(url) and _url_key(url) in own_keys(data))
        for r in rows:
            kind = "own" if own_listing else classify(r, data) or ("own" if url_own else "other")
            r["source"] = {"kind": kind, **({"channel": r["channel"]} if r.get("channel") else {})}
            if kind == "other":
                key = channel_key(r, data)
                if key:
                    r["source"].update(channel_view(key, r, data))
    except Exception:  # noqa: BLE001 — 출처 표시는 곁가지: 목록은 그대로 보여 줌
        pass
    return rows


# ---------- 예전에 받은 영상 출처 찾기 (뒤에서 천천히) ----------

def _norm_title(t):
    return re.sub(r"[\W_]+", "", unquote(str(t or "")).lower())


def _title_of(name):
    return re.sub(r"\.[^.]+$", "", YT_NAME.sub("", name))


def from_channel_cache(names, data=None):
    """우리 채널 목록 기억(channel_cache.json)과 맞춰 봄 → {파일 이름: "id" | "title"}.
    "id": 영상 id 가 목록에 있음 = 확실히 우리 채널.
    "title": 제목 앞부분이 맞음 = 짐작 (영상 id 가 없던 예전 기억의 줄하고만 맞춤 · 새 기억은 id 가 있어서 제목으로 맞추지 않음)."""
    import hooks
    cache = hooks.load_cache() or {}
    rows = [r for k in ("videos", "shorts") for r in cache.get(k) or [] if isinstance(r, dict)]
    ids = {r.get("id") for r in rows if r.get("id")}
    titles = [_norm_title(r.get("title")) for r in rows if not r.get("id")]
    out = {}
    for n in names:
        vid, t = video_id(n), _norm_title(_title_of(n))
        if not vid:
            continue
        if vid in ids:
            out[n] = "id"
        elif len(t) >= TITLE_MIN and any(x.startswith(t) for x in titles if x):
            out[n] = "title"
    return out


def _online(timeout=3):
    """인터넷이 되는지 (YouTube 에 잠깐 연결해 봄)."""
    try:
        socket.create_connection(("www.youtube.com", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def _lookup(vid):
    """영상 하나의 정보만 조회 (받지 않음 · 화질 목록도 안 만듦) → yt-dlp 정보 dict."""
    import yt_dlp  # 없거나 망가졌으면 실패로 끝냄 (다시 설치는 영상 받기 쪽에서)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15, "noplaylist": True}
    opts.update(core._js_opts())
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False, process=False)


def _library():
    try:
        return [p.name for p in sorted(core.VIDEOS.iterdir()) if p.suffix.lower() in core.VIDEO_EXTS]
    except OSError:
        return []


def pending(data=None):
    """출처를 아직 모르는 보관함 영상 이름."""
    data = data or load()
    return [n for n in _library() if describe(n, data, False)["kind"] == "unknown"]


def guessed(data=None):
    """제목으로만 짐작해 둔 풋살사관학교 영상 이름 (인터넷이 되면 다시 확인)."""
    data = data or load()
    return [n for n in _library() if describe(n, data, False).get("guess")]


def _paused(data, now=None):
    p = (data.get("lookup") or {}).get(PAUSE_KEY) or {}
    return isinstance(p.get("until"), (int, float)) and (now or time.time()) < p["until"]


def _own_url():
    """설정의 우리 채널 주소가 /c/이름 · /user/이름 · 옛 youtube.com/이름 꼴이면 그 주소 (채널 id 로 바꿔 둬야 맞출 수 있음)."""
    url = str(core.CONFIG.get("channel_url") or "").strip()
    k = _url_key(url)
    return url if k and k[0] == "path" else None


def _own_unresolved(data):
    """우리 채널 주소를 아직 채널 id 로 바꾸지 못했으면 그 주소 (하루 안에 실패했으면 None)."""
    url = _own_url()
    if not url or ((data.get("own") or {}).get("resolved") or {}).get("url") == url:
        return None
    return None if _failed_recently("_own:" + url, data) else url


def _channel_info(url):
    """채널 주소의 정보만 조회 (영상 목록은 첫 1개만 · 받지 않음) → yt-dlp 정보 dict."""
    import yt_dlp
    u = url if re.match(r"https?://", url, re.I) else "https://www.youtube.com/" + url.lstrip("/")
    p = urlsplit(u)
    path = re.sub(r"/(videos|shorts|streams|featured|about)$", "", p.path.rstrip("/"))
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15,
            "extract_flat": True, "playlistend": 1}
    opts.update(core._js_opts())
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com{path}/videos", download=False)


def _resolve_own(url):
    """/c/·/user/ 꼴 우리 채널 주소 → 채널 id·@핸들을 알아내 우리 채널로 기억 (요즘 yt-dlp 정보에는 /c/ 주소가 없음). 오류는 그대로 냄."""
    info = _channel_info(url)
    cid = info.get("channel_id") if isinstance(info, dict) else None
    if not (isinstance(cid, str) and re.fullmatch(r"UC[\w-]{22}", cid)):
        raise ValueError("채널 id 를 찾지 못했어요")
    handle = info.get("uploader_id") if isinstance(info.get("uploader_id"), str) and info["uploader_id"].startswith("@") else None

    def fn(data):
        own = data["own"]
        own["ids"] = sorted(set(own.get("ids") or []) | {cid})[:20]
        if handle:
            own["handles"] = sorted(set(own.get("handles") or []) | {handle.lower()})[:20]
        own["resolved"] = {"url": url, "id": cid}
        data["lookup"].pop("_own:" + url, None)
    _update(fn)
    return cid


def backfill_running():
    t = _BF["thread"]
    return bool(t and t.is_alive())


def _has_work(data):
    if pending(data):
        return True
    if _paused(data):
        return False
    return bool(_own_unresolved(data)) or any(not _failed_recently(video_id(n), data) for n in guessed(data))


def start_backfill(log=print):
    """모르는 영상(또는 짐작만 해 둔 영상 · 아직 채널 id 로 못 바꾼 우리 채널 주소)이 있으면 뒤에서 출처 찾기 시작
    (이미 하는 중이면 그대로). 시작했으면 True."""
    with _LOCK:
        if backfill_running() or not _has_work(load()):
            return False
        stop = threading.Event()
        t = threading.Thread(target=_backfill, args=(stop, log), daemon=True)
        _BF.update(thread=t, stop=stop, offline=False)
        t.start()
        return True


def stop_backfill():
    if _BF["stop"]:
        _BF["stop"].set()


def _engine_free(stop, wait=30):
    """다운로드 엔진을 쓰는 다른 일(받기·불러오기·엔진 바꾸기)이 없는지. wait 초 넘게 계속 쓰고 있으면 False.
    조회하는 동안 잠금을 들고 있지는 않음: 그사이 영상 받기를 누르면 '엔진 준비 중'으로 기다리게 하지 않으려고 (받기를 시작하면 조회는 멈춤)."""
    end = time.time() + wait
    while not stop.is_set():
        if core._ENGINE_LOCK.acquire(blocking=False):
            core._ENGINE_LOCK.release()
            return True
        if time.time() > end:
            return False
        stop.wait(1)
    return False


def _pause(log, reason):
    """YouTube 가 막았거나 연달아 실패 → LOOKUP_PAUSE 동안 조회 안 함 (알림도 이때 한 번만)."""
    now = time.time()
    _update(lambda data: data["lookup"].__setitem__(PAUSE_KEY, {"t": now, "until": now + LOOKUP_PAUSE, "why": reason[:200]}))
    log(f"  영상 출처 확인을 잠시 멈췄어요 (YouTube 에서 정보를 받지 못했어요 · {LOOKUP_PAUSE // 3600}시간 뒤 다시) · {reason[:120]}")


def _backfill(stop, log):
    try:
        _backfill_run(stop, log)
    except Exception as e:  # noqa: BLE001 — 뒤에서 하는 곁가지 일: 기록을 못 읽는 등 실패해도 앱은 그대로
        log(f"  영상 출처 확인을 이번에는 하지 못했어요 · {e}")


def _backfill_run(stop, log):
    """1) 우리 채널 목록 기억에 영상 id 가 있으면 우리 채널 (확실)
    2) 인터넷이 되면 영상 정보 조회 (제목으로 짐작해 둔 영상도 다시 확인) · /c/ 꼴 우리 채널 주소는 채널 id 로 바꿔 둠
    3) 조회하지 못한 영상 중 제목 앞부분이 우리 채널 목록과 맞는 것 → 풋살사관학교(짐작 · guess)"""
    data = load()
    names = pending(data)
    matched = from_channel_cache(names)
    found = [n for n, how in matched.items() if how == "id"]
    guesses = [n for n, how in matched.items() if how == "title"]
    if found:
        def keep(data):
            for n in found:
                _merge(data["files"].setdefault(n, {}), {}, "own", "cache")
        _update(keep)
        data = load()
    todo = [n for n in names if n not in found] + guessed(data)
    todo = [n for n in dict.fromkeys(todo) if not _failed_recently(video_id(n), data)][:LOOKUP_MAX]
    own_url = _own_unresolved(data)
    looked, got = set(), 0
    try:
        if _paused(data) or not (todo or own_url):
            return
        if not _online():
            _BF["offline"] = True
            return
        if own_url and _engine_free(stop):
            try:
                _resolve_own(own_url)
            except Exception as e:  # noqa: BLE001 — 못 바꾸면 하루 뒤 다시 (목록 불러오기로도 배움)
                why = str(e)
                _update(lambda data: data["lookup"].__setitem__("_own:" + own_url, {"t": time.time(), "why": why[:200]}))
                if core._blocked_text(why):
                    return _pause(log, why)
        fails = 0
        for k, n in enumerate(todo):
            if stop.is_set() or (k and stop.wait(LOOKUP_GAP)):
                break
            vid = video_id(n)
            if not _engine_free(stop):
                break
            err = ""
            try:
                info = _lookup(vid)
            except Exception as e:  # noqa: BLE001 — 지운 영상·비공개·막힘·인터넷 끊김 모두 '알 수 없음'으로 두고 다음 영상
                info, err = None, str(e)
            if isinstance(info, dict) and (info.get("channel_id") or info.get("channel") or info.get("uploader_id")):
                fails = 0
                got += 1
                looked.add(n)
                record_download([dict(info, id=vid)], how="lookup")
                continue
            reason = err or "채널 정보가 없어요"
            _update(lambda data: data["lookup"].__setitem__(vid, {"t": time.time(), "why": reason[:200]}))
            if core._blocked_text(reason):
                return _pause(log, reason)
            if not GONE.search(reason):  # 지운·비공개 영상은 그 영상만의 일 → 멈출 이유가 아님
                fails += 1
            if fails >= LOOKUP_FAILS:
                return _pause(log, reason)
    finally:
        new = [n for n in guesses if n not in looked]
        if new:
            def guess(data):
                for n in new:
                    rec = data["files"].setdefault(n, {})
                    if rec.get("how") in (None, "guess"):  # 그사이 받거나 조회된 영상은 그대로
                        _merge(rec, {}, "own", "guess")
            try:
                _update(guess)
            except OSError:
                pass
        _done(log, len(found) + got, len(new))


def _done(log, known, guessed_):
    if known:
        log(f"영상 출처 확인 · {known}개 영상의 채널을 알아냈어요")
    if guessed_:
        log(f"영상 출처 확인 · {guessed_}개 영상은 제목으로 풋살사관학교 같다고 짐작했어요 (틀리면 보관함에서 배지를 눌러 고쳐 주세요)")
