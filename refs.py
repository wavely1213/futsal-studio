"""학습용 영상(레퍼런스) 보관함 — 스타일 배우기에만 쓰는 다른 채널 영상을 편집용 보관함(videos)과 따로 둔다.

- 파일: 작업 폴더의 refs/<채널 폴더>/<영상 파일> · 분석 기록은 refs/<채널 폴더>/_analysis/<영상 이름>/
  (style_events.json·plan_events.json·옮겨 온 받아쓰기). 받는 중인 파일은 refs/_받는 중/ 에 받았다가 채널 폴더로 옮김
- 기록: refs/refs.json (임시 파일에 다 쓴 뒤 바꿔 끼움) — channels(채널 묶음 · source.py 와 같은 열쇠·색 규칙 + folder)
  · files(파일 이름별: channelKey·folder·videoId·title·how·pruned·sig) · ui(보관함 옮기기 제안을 닫았는지)
- 보관함·편집점·편집실·썸네일·올리기는 videos 폴더만 보므로 여기 영상은 나오지 않는다.
  core.video_file·core.adir 가 보관함에 없는 이름을 여기서 찾아 줌 → 스타일 배우기·기획 분석은 그대로 동작
- 받기: 채널 인기 영상 N개(core.list_videos → core.download 를 받는 곳만 바꿔) → '<채널명> 스타일' 배우기(선택)
- 배운 뒤 영상 파일 지우기(prune): 파일만 지우고 분석 기록과 그때 파일 지문(sig: 크기·수정 시각)을 남김
  → 다시 배울 때 그 기록을 그대로 씀 (style.extract_events·plan.extract_plan 이 core.kept_sig 로 확인)
- 보관함 → 학습용으로 옮기기: 영상 파일 + 분석 폴더를 옮김 (Windows 잠김은 조금 뒤 다시 · 실패하면 되돌림)
"""
import json
import os
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path

import core
import source

STORE = "refs.json"
ANALYSIS_DIR = "_analysis"      # 채널 폴더 안의 분석 기록 폴더
INCOMING = "_받는 중"           # 받는 동안 잠깐 두는 폴더 (채널을 알면 채널 폴더로 옮김)
UNKNOWN = "채널 모름"           # 채널 정보가 없는 영상의 폴더 이름
COUNT_MAX = 30                  # 채널 하나에서 한 번에 받는 최대 영상 수
SUGGEST_MIN = 3                 # 보관함에 다른 채널 영상이 이만큼 있으면 옮기기를 제안
TRIES, WAIT = 20, 0.1           # Windows: 백신·탐색기·편집실이 잠깐 잡고 있으면 조금 뒤 다시
SETTLE_SECS = 60                # 막 받은 큰 영상은 백신(Defender)이 오래 검사함 → 받는 폴더에서 옮길 때는 더 오래 기다림
ORIGINAL_NAMES = ("풋살사관학교", "내 촬영본")  # 보관함에서 확인하고 옮긴 원본(우리 채널·촬영본) 채널 이름
READ_TRIES = 5
BLOCKED_MSG = ("다운로드 엔진을 최신으로 바꿔 다시 해 봤지만 YouTube가 계속 막고 있어요. YouTube에 로그인해 둔 브라우저(파이어폭스·엣지·웨일·"
               "크롬)를 이 화면 '채널 추가' 칸의 '로그인 정보로 받기'에서 고른 뒤 다시 받아 보세요.")
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_LOCK = threading.RLock()
_CACHE = {"key": None, "data": None}


class RefsError(ValueError):
    """화면에 그대로 보여 줄 수 있는 안내 (한국어)."""


class StoreBusy(OSError):
    """refs.json 을 지금은 읽을 수 없음 (잠김) — 덮어쓰면 안 됨."""


def root():
    return core.WORK / "refs"


def store_path():
    return root() / STORE


def channels_file():
    """추천 채널 목록 (앱과 함께 배포 · 읽기만)."""
    return core.APP_DIR / "ref_channels.json"


# ---------- 기록 (refs.json) ----------

def _empty():
    return {"version": 1, "channels": {}, "files": {}, "ids": {}, "ui": {}}


def _clean(d):
    """잘못된 칸은 빼고 나머지는 살림 (영상 하나 때문에 전체가 사라지지 않게)."""
    out = _empty()
    for k in ("channels", "ids"):
        out[k] = {n: r for n, r in (d.get(k) or {}).items() if isinstance(n, str) and isinstance(r, dict)}
    out["files"] = {n: r for n, r in (d.get("files") or {}).items()
                    if isinstance(n, str) and n and _BAD.search(n) is None and n not in (".", "..")
                    and isinstance(r, dict) and isinstance(r.get("folder"), str) and _safe_folder(r["folder"])}
    for c in out["channels"].values():
        if not (isinstance(c.get("folder"), str) and _safe_folder(c["folder"])):
            c.pop("folder", None)
    out["ui"] = d["ui"] if isinstance(d.get("ui"), dict) else {}
    return out


def _safe_folder(f):
    return bool(f) and _BAD.search(f) is None and f not in (".", "..") and f.strip(" .") == f


def load(strict=False):
    """refs.json (읽기 전용으로 씀 · 고칠 때는 _update). 깨졌으면 .bad 로 남기고 빈 기록,
    잠깐 못 읽으면 몇 번 다시 해 보고 strict 면 StoreBusy (좋은 파일을 빈 기록으로 덮어쓰지 않게)."""
    p = store_path()
    with _LOCK:
        try:
            st = p.stat()
            key = (str(p), st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            key = (str(p), None, None)
        except OSError as e:
            if strict:
                raise StoreBusy(f"학습용 영상 기록을 읽지 못했어요 · {e}") from e
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
                except FileNotFoundError:
                    return data
                except OSError as e:
                    err = e
                    time.sleep(0.05 * (i + 1))
            if raw is None:
                if strict:
                    raise StoreBusy(f"학습용 영상 기록을 읽지 못했어요 · {err}") from err
                return data
            try:
                d = json.loads(raw.decode("utf-8"))
                if not isinstance(d, dict):
                    raise ValueError("형식이 달라요")
                data = _clean(d)
            except ValueError:
                try:
                    p.with_name(STORE + ".bad").write_bytes(raw)
                except OSError:
                    pass
        _CACHE.update(key=key, data=data)
        return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}_{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    try:
        _retry(lambda: os.replace(tmp, path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _update(fn):
    """기록을 읽어 fn(data) 로 고친 뒤 저장 (한 번에 하나씩). fn 이 오류를 내면 저장하지 않음."""
    with _LOCK:
        data = json.loads(json.dumps(load(strict=True)))
        out = fn(data)
        _write(store_path(), data)
        _CACHE["key"] = None
        return out


def _retry(fn, tries=TRIES, secs=None):
    """Windows: 다른 프로그램이 파일을 잠깐 잡고 있으면(PermissionError) 조금 뒤 다시.
    secs 를 주면 그 시간(초)까지 점점 길게 기다리며 다시 (막 받은 영상을 백신이 검사하는 동안)."""
    if secs is None:
        for k in range(tries):
            try:
                return fn()
            except PermissionError:
                if k == tries - 1:
                    raise
                time.sleep(WAIT)
        return None
    end, wait = time.monotonic() + secs, WAIT
    while True:
        try:
            return fn()
        except PermissionError:
            if time.monotonic() + wait > end:
                raise
            time.sleep(wait)
            wait = min(wait * 2, 5.0)


# ---------- 이름 → 파일·분석 폴더 (core.video_file · core.adir 가 부름) ----------

def find(name):
    """학습용 영상 기록 {channelKey, folder, …} · 없으면 None."""
    name = os.path.basename(str(name or ""))
    return load()["files"].get(name) if name else None


def _stem(name):
    return Path(name).stem.rstrip(" .") or "video"


def path_of(name, rec=None):
    rec = rec or find(name)
    return root() / rec["folder"] / os.path.basename(name) if rec else None


def adir_of(name, rec=None):
    rec = rec or find(name)
    return root() / rec["folder"] / ANALYSIS_DIR / _stem(name) if rec else None


def kept_sig(name):
    """'배운 뒤 영상 파일 지우기'로 파일만 지운 영상이면 그때의 파일 지문 [크기, 수정 시각] · 아니면 None."""
    rec = find(name)
    sig = rec.get("sig") if rec and rec.get("pruned") else None
    return sig if isinstance(sig, list) and len(sig) == 2 and all(isinstance(x, int) for x in sig) else None


def _events_ok(name, rec=None, kept=False):
    """배운 기록(style_events.json)이 있는지. kept: 파일을 지운 영상 — 그 기록을 지금 앱이 그대로 쓸 수 있는지까지
    (기록 형식 EV_VER·지울 때 남긴 지문이 맞아야 style._load_events 가 씀 · 앱이 바뀌어 형식이 달라지면 다시 받아야 함)."""
    d = adir_of(name, rec)
    if not d:
        return False
    f = d / "style_events.json"
    if not kept:
        return f.is_file()
    try:
        import style
        ev = json.loads(f.read_text(encoding="utf-8"))
        rec = rec or find(name) or {}
        return isinstance(ev, dict) and ev.get("v") == style.EV_VER and ev.get("sig") == rec.get("sig")
    except (OSError, ValueError):
        return False


def usable(name):
    """스타일 배우기에 쓸 수 있는지: 파일이 있거나, 파일을 지웠어도 지금 쓸 수 있는 배운 기록이 남아 있음."""
    rec = find(name)
    if not rec:
        return False
    p = path_of(name, rec)
    return p.is_file() or bool(rec.get("pruned") and _events_ok(name, rec, kept=True))


def is_original(rec, data=None):
    """보관함에서 확인하고 옮긴 원본(풋살사관학교·내 촬영본·출처 모름) — 다시 받을 수 없으니 자동으로 지우지 않음."""
    if not rec:
        return False
    if rec.get("original"):
        return True
    c = ((data or load())["channels"].get(rec.get("channelKey") or "") or {})
    return bool(c.get("original"))


def originals(names=None, channel=None):
    """지우려는 것 중 원본인 영상 이름 (지우기 전에 한 번 더 확인할 것)."""
    data = load()
    if channel is not None:
        names = [n for n, r in data["files"].items() if (r.get("channelKey") or "") == channel]
    return [n for n in names or [] if is_original(data["files"].get(n), data)]


# ---------- 채널 ----------

def _folder_name(name):
    n = unicodedata.normalize("NFC", _BAD.sub("", str(name or "")))
    n = re.sub(r"\s+", " ", n).strip(" .").lstrip("_").strip(" .")[:40].strip(" .")
    if not n or n.split(".")[0].upper() in _RESERVED:
        n = (n + " 채널") if n else UNKNOWN
    return n


def _new_folder(data, name):
    used = {(c.get("folder") or "").lower() for c in data["channels"].values()}
    used |= {r["folder"].lower() for r in data["files"].values()}
    base = _folder_name(name)
    f, k = base, 2
    while f.lower() in used or f.lower() in (INCOMING.lower(), ANALYSIS_DIR.lower()):
        f, k = f"{base} ({k})", k + 1
    return f


def _register(data, meta, key=None):
    """채널 하나를 channels 에 넣고 열쇠를 돌려줌 (모르면 ""). 보관함(sources.json)이 같은 채널을 알면 색을 맞춤."""
    chans = data["channels"]
    if key is None:
        key = source._register(data, meta) or ""
    c = chans.setdefault(key, {})
    if key:
        try:
            sc = (source.load().get("channels") or {}).get(key) or {}
        except Exception:  # noqa: BLE001 — 색 맞추기는 곁가지
            sc = {}
        for f in ("name", "url"):
            if not c.get(f) and isinstance(sc.get(f), str) and sc[f]:
                c[f] = sc[f]
        if isinstance(sc.get("color"), int):
            c["color"] = sc["color"]
        elif not isinstance(c.get("color"), int):
            c["color"] = source._pick_color(key, chans)
    if not c.get("folder"):
        c["folder"] = _new_folder(data, c.get("name") or (key[2:] if key[:2] in ("h:", "n:") else "") or UNKNOWN)
    return key


def _view(key, data):
    c = data["channels"].get(key) or {}
    out = {"channelKey": key, "channel": c.get("name") or "", "color": c.get("color") if isinstance(c.get("color"), int) else None}
    if c.get("url"):
        out["url"] = c["url"]
    if c.get("handles"):
        out["handles"] = [h for h in c["handles"] if isinstance(h, str)]
    return out


# ---------- 목록 ----------

def _size(p):
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _tree_size(d):
    total = 0
    for dp, _, fs in os.walk(d):
        for f in fs:
            total += _size(Path(dp) / f)
    return total


def _adopt(data):
    """refs 폴더에 기록 없이 있는 영상(옮기다 끊김·기록 파일이 깨짐 등)을 그 폴더의 채널 영상으로 기록하고,
    받는 폴더에 남은 영상(백신이 오래 잡고 있었음·앱이 꺼짐)도 채널 폴더로 옮김 → 고친 게 있으면 True."""
    found = []
    folders = {c["folder"]: k for k, c in data["channels"].items() if c.get("folder")}
    try:
        subs = [p for p in root().iterdir() if p.is_dir()]
    except OSError:
        subs = []
    for d in subs:
        if d.name in (INCOMING, ANALYSIS_DIR) or not _safe_folder(d.name):
            continue
        try:
            items = list(d.iterdir())
        except OSError:
            continue
        for p in items:
            if p.is_file() and p.suffix.lower() in core.VIDEO_EXTS and p.name not in data["files"]:
                found.append((p.name, d.name, folders.get(d.name)))
    gone = [n for n, r in data["files"].items()  # 보관함으로 되돌렸는데 기록을 못 지운 것
            if r.get("how") == "move" and not r.get("pruned") and not path_of(n, r).is_file() and (core.VIDEOS / n).is_file()]
    changed = False
    if found or gone:
        def fn(d):
            for n in gone:
                d["files"].pop(n, None)
            for n, folder, key in found:
                if n in d["files"]:
                    continue
                if key is None:
                    key = _register_folder(d, folder)
                d["files"][n] = {"channelKey": key, "folder": folder, "videoId": source.video_id(n), "how": "found",
                                 "saved": time.strftime("%Y-%m-%d %H:%M")}
        _update(fn)
        changed = True
    inc = root() / INCOMING
    if not _FETCHING[0] and inc.is_dir():
        for p in sorted(inc.iterdir()):
            vid = source.video_id(p.name)
            if p.is_file() and p.suffix.lower() in core.VIDEO_EXTS and vid:
                st, _ = _settle(vid, {}, {}, lambda *a: None)
                changed = changed or st == "new"
    return changed


def _register_folder(d, folder):
    """기록에 없는 채널 폴더 → 그 폴더 이름의 채널로 등록 (폴더는 그대로 씀)."""
    key = source._register(d, {"channel": folder}) or ""
    c = d["channels"].setdefault(key, {})
    c.setdefault("folder", folder)
    c.setdefault("name", folder)
    if folder in ORIGINAL_NAMES:
        c["original"] = True
    _register(d, {}, key=key)
    return key


def _style_uses():
    """영상 이름 → 그 영상으로 배운 스타일 이름들."""
    out = {}
    try:
        import style
        for f in sorted(style.STYLES.glob("*.json")):
            try:
                src = json.loads(f.read_text(encoding="utf-8")).get("source")
            except (OSError, ValueError, AttributeError):
                continue
            for n in src if isinstance(src, list) else [src]:
                if isinstance(n, str):
                    out.setdefault(n, []).append(f.stem)
    except Exception:  # noqa: BLE001 — 곁가지
        pass
    return out


def listing():
    """화면용: {videos:[…], channels:[…], disk:{…}, bannerDismissed}."""
    try:
        _adopt(load())
    except OSError:
        pass
    data, uses = load(), _style_uses()
    vids, groups = [], {}
    for n, rec in sorted(data["files"].items(), key=lambda x: (x[1].get("saved") or "", x[0]), reverse=True):
        p = path_of(n, rec)
        present, learned = p.is_file(), _events_ok(n, rec)
        size = _size(p) if present else 0
        key = rec.get("channelKey") or ""
        v = dict(_view(key, data), name=n, title=rec.get("title") or source._title_of(n), videoId=rec.get("videoId") or source.video_id(n),
                 present=present, pruned=bool(rec.get("pruned")) and not present, learned=learned, size_mb=round(size / 1e6, 1),
                 styles=uses.get(n, []), kind=rec.get("kind") or "", how=rec.get("how") or "", original=is_original(rec, data))
        v["stale"] = v["pruned"] and not _events_ok(n, rec, kept=True)  # 앱이 바뀌어 남긴 기록을 못 씀 → 다시 받아야 함
        v["usable"] = present or (v["pruned"] and not v["stale"])
        vids.append(v)
        g = groups.setdefault(key, dict(_view(key, data), count=0, size_mb=0.0, learned=0, original=False))
        g["original"] = g["original"] or v["original"]
        g["count"] += 1
        g["size_mb"] = round(g["size_mb"] + v["size_mb"], 1)
        g["learned"] += int(learned)
    chans = sorted(groups.values(), key=lambda g: (-g["count"], not g["channelKey"], g["channel"]))
    disk = {"videos_mb": round(sum(v["size_mb"] for v in vids), 1), "total_mb": round(_tree_size(root()) / 1e6, 1) if root().exists() else 0.0}
    disk["analysis_mb"] = round(max(0.0, disk["total_mb"] - disk["videos_mb"]), 1)
    return {"videos": vids, "channels": chans, "disk": disk, "bannerDismissed": bool(data["ui"].get("bannerDismissed")),
            "suggestMin": SUGGEST_MIN, "folder": str(root())}


def recommended():
    """추천 채널 목록 (ref_channels.json · 깨졌으면 빈 목록)."""
    try:
        d = json.loads(channels_file().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("channels"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "groups": [], "channels": [], "directions": []}


def dismiss_banner(on=True):
    _update(lambda d: d["ui"].__setitem__("bannerDismissed", bool(on)))


# ---------- 파일 옮기기·지우기 (Windows 잠김은 조금 뒤 다시) ----------

def _move(src, dst, secs=None):
    """같은 드라이브면 바로 이름 바꾸기 · 다른 드라이브면 복사 후 지우기. 잠겨 있으면 몇 번 다시 (secs: _retry)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise RefsError(f"같은 이름이 이미 있어요 · {dst.name}")

    def go():
        try:
            os.replace(src, dst)
        except OSError as e:
            if isinstance(e, PermissionError) or getattr(e, "errno", None) != 18:  # 18 = 다른 드라이브(EXDEV)
                raise
            shutil.move(str(src), str(dst))
    _retry(go, secs=secs)


def _unlink(p):
    try:
        _retry(p.unlink)
    except FileNotFoundError:
        pass


def _rmtree(d):
    for _ in range(TRIES):
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            return True
        time.sleep(WAIT)
    return False


def _locked_msg(name):
    return (f"'{source._title_of(name)[:30]}' 파일을 다른 프로그램(편집실·탐색기·백신 등)이 쓰고 있어서 옮기지 못했어요. "
            "그 창을 닫은 뒤 다시 해 주세요")


def projects_using(names):
    """편집실 프로젝트(projects/*.json)에서 원본·가져온 영상으로 쓰는 보관함 영상 → {이름: [프로젝트 이름]}.
    옮기면 그 프로젝트가 영상을 못 찾으니 옮기지 않음."""
    want = {os.path.basename(str(n)) for n in names or []}
    out = {}
    if not want:
        return out
    stems = {_stem(n): n for n in want}
    try:
        files = sorted((core.WORK / "projects").glob("*.json"))
    except OSError:
        return out

    def walk(x, used):
        if isinstance(x, dict):
            if x.get("src", "videos") == "videos" and isinstance(x.get("file"), str):
                used.add(os.path.basename(x["file"]))
            for v in x.values():
                walk(v, used)
        elif isinstance(x, list):
            for v in x:
                walk(v, used)
    for f in files:
        used = set()
        if f.stem in stems:  # 그 영상의 편집실 프로젝트 (파일 이름 = 영상 이름)
            used.add(stems[f.stem])
        try:
            pj = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pj = None
        if isinstance(pj, dict):
            if isinstance(pj.get("source"), str):
                used.add(os.path.basename(pj["source"]))
            walk(pj.get("media"), used)
            walk(pj.get("sequences"), used)
        for n in used & want:
            out.setdefault(n, []).append(f.stem)
    return out


def _archive(vid, add):
    """보관함 받기 기록(videos/archive.txt)에서 영상 id 빼기·넣기 — 학습용으로 옮긴 영상을 소재 찾기로 다시 받을 수 있게.
    실패해도 괜찮음 (임시 파일에 다 쓴 뒤 바꿔 끼움)."""
    if not vid:
        return
    p, line = core.VIDEOS / "archive.txt", f"youtube {vid}"
    try:
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        has = any(x.strip() == line for x in lines)
        if has == add:
            return
        new = (lines + [line]) if add else [x for x in lines if x.strip() != line]
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(x + "\n" for x in new), encoding="utf-8")
        _retry(lambda: os.replace(tmp, p))
    except OSError:
        pass


def move_from_library(names, log=print, allow_own=False):
    """편집용 보관함의 영상 → 학습용 영상 (파일 + 분석 폴더). 다른 채널 영상만 옮기고,
    풋살사관학교·내 촬영본·출처 모르는 영상은 allow_own(사용자가 확인함)일 때만.
    편집실 프로젝트에서 쓰는 영상은 옮기지 않음(프로젝트가 깨짐). → {moved, skipped, failed, inUse}"""
    sdata = source.load()
    moved, skipped, failed = [], [], []
    busy = projects_using(names)
    for k, n in enumerate(names, 1):
        core.set_progress(label="학습용으로 옮기는 중", item=n, step=f"{k}/{len(names)}", pct=None, detail="영상과 분석 기록을 옮기는 중")
        src = core.VIDEOS / os.path.basename(n)
        if not src.is_file():
            failed.append({"name": n, "error": "보관함에서 영상을 찾지 못했어요"})
            continue
        desc = source.describe(n, sdata, False)
        if desc["kind"] != "other" and not allow_own:
            skipped.append(n)
            continue
        if n in busy:
            skipped.append(n)
            log(f"  편집실 프로젝트({', '.join(busy[n][:3])})에서 쓰는 영상이라 보관함에 두었어요 · {n}")
            continue
        try:
            _move_one(n, src, desc, sdata)
        except (RefsError, StoreBusy) as e:
            failed.append({"name": n, "error": str(e)})
            log(f"  옮기지 못했어요 · {n} · {e}")
            continue
        except PermissionError:
            failed.append({"name": n, "error": _locked_msg(n)})
            log(f"  옮기지 못했어요 (파일이 잠겨 있어요) · {n}")
            continue
        except OSError as e:
            failed.append({"name": n, "error": f"옮기지 못했어요 · {e}"})
            log(f"  옮기지 못했어요 · {n} · {e}")
            continue
        moved.append(n)
        _archive(source.video_id(n), add=False)
        log(f"학습용으로 옮김 · {n}")
    return {"moved": moved, "skipped": skipped, "failed": failed, "inUse": {n: v for n, v in busy.items() if n in skipped}}


def _move_one(n, src, desc, sdata):
    """영상 하나 옮기기: 분석 폴더 → 영상 파일 → 기록. 영상을 못 옮기면 분석 폴더를 되돌림.
    기록을 못 남겨도 파일은 채널 폴더에 있어 다음 목록에서 그 채널 영상으로 다시 기록됨(_adopt)."""
    if n in load()["files"]:
        raise RefsError("같은 이름의 학습용 영상이 이미 있어요")
    _, rec = source._record(n, sdata)
    meta = {k: rec[k] for k in ("channel", "channelId", "channelUrl", "uploaderId", "uploaderUrl") if isinstance(rec.get(k), str) and rec[k]}
    if desc.get("channel"):
        meta["channel"] = desc["channel"]
    key = desc.get("channelKey") if desc["kind"] == "other" else None
    schan = (sdata.get("channels") or {}).get(key) if key else None

    def chan(data):  # 채널(폴더)을 먼저 정해 기록
        if schan is not None:
            c = data["channels"].setdefault(key, {})
            for f in ("name", "names", "handles", "url", "color"):
                if f in schan and f not in c:
                    c[f] = json.loads(json.dumps(schan[f]))
            return _register(data, meta, key=key), data["channels"][key]["folder"]
        if desc["kind"] in ("own", "footage"):  # 사용자가 확인하고 옮긴 우리 채널·내 촬영본 영상은 그 이름의 폴더로 (원본 → 자동으로 지우지 않음)
            k = _register(data, {"channel": ORIGINAL_NAMES[0 if desc["kind"] == "own" else 1]})
            data["channels"][k]["original"] = True
        else:
            k = _register(data, meta) if meta else _register(data, {}, key="")
        return k, data["channels"][k]["folder"]
    key, folder = _update(chan)
    adir = core.ANALYSIS / _stem(n)
    dst, adst = root() / folder / n, root() / folder / ANALYSIS_DIR / _stem(n)
    if dst.exists():
        raise RefsError("학습용 폴더에 같은 이름의 파일이 이미 있어요")
    moved_dir = False
    if adir.is_dir():
        if adst.exists():
            raise RefsError("학습용 폴더에 같은 이름의 분석 기록이 이미 있어요")
        _move(adir, adst)
        moved_dir = True
    try:
        _move(src, dst)
    except BaseException:
        if moved_dir:
            try:
                _move(adst, adir)
            except OSError:
                pass
        raise

    def put(data):
        data["files"][n] = {"channelKey": key, "folder": folder, "videoId": source.video_id(n), "title": source._title_of(n),
                            "how": "move", "saved": time.strftime("%Y-%m-%d %H:%M")}
        if desc["kind"] != "other":
            data["files"][n].update(original=True, origin=desc["kind"])
    try:
        _update(put)
    except OSError:
        pass  # 다음 목록에서 _adopt 가 채널 폴더의 파일을 다시 기록함


def delete(names=None, channel=None, log=print, allow_original=False):
    """학습용 영상 지우기 (파일 + 분석 기록 + 기록) · channel 을 주면 그 채널 영상 모두(빈 채널 폴더도).
    원본(보관함에서 옮긴 우리 채널·촬영본)은 allow_original(사용자가 한 번 더 확인함)일 때만. → {removed, failed}"""
    data = load()
    removed, failed = [], []
    if channel is not None:
        if channel not in data["channels"] and not any((r.get("channelKey") or "") == channel for r in data["files"].values()):
            raise RefsError("그 채널을 찾지 못했어요")
        names = [n for n, r in data["files"].items() if (r.get("channelKey") or "") == channel]
    for n in names or []:
        rec = data["files"].get(n)
        if not rec:
            failed.append({"name": n, "error": "학습용 영상에서 찾지 못했어요"})
            continue
        if is_original(rec, data) and not allow_original:
            failed.append({"name": n, "error": "보관함에서 옮긴 원본 영상이에요 (다시 받을 수 없어요). 지우려면 한 번 더 확인해 주세요"})
            continue
        try:
            _unlink(path_of(n, rec))
        except PermissionError:
            failed.append({"name": n, "error": _locked_msg(n).replace("옮기지", "지우지")})
            continue
        except OSError as e:
            failed.append({"name": n, "error": str(e)})
            continue
        _rmtree(adir_of(n, rec))
        removed.append(n)
    gone = set(removed)

    def fn(d):
        for n in gone:
            d["files"].pop(n, None)
        if channel is not None and not any((r.get("channelKey") or "") == channel for r in d["files"].values()):
            c = d["channels"].pop(channel, None)
            if c and c.get("folder"):
                _rmdir_empty(root() / c["folder"])  # 기록에 없는 파일이 있으면 폴더는 남김 (다음 목록에서 다시 보임)
    if gone or channel is not None:
        _update(fn)
    if removed:
        log(f"학습용 영상 {len(removed)}개를 지웠어요")
    return {"removed": removed, "failed": failed}


def _rmdir_empty(d):
    for x in (d / ANALYSIS_DIR, d):
        try:
            x.rmdir()
        except OSError:
            pass


def restore(names, log=print):
    """학습용 영상 → 편집용 보관함으로 되돌리기 (파일 + 분석 폴더). → {restored, failed}"""
    data = load()
    restored, failed = [], []
    for k, n in enumerate(names or [], 1):
        core.set_progress(label="보관함으로 되돌리는 중", item=n, step=f"{k}/{len(names)}", pct=None, detail="영상과 분석 기록을 옮기는 중")
        rec = data["files"].get(n)
        src = path_of(n, rec) if rec else None
        if not src or not src.is_file():
            failed.append({"name": n, "error": "영상 파일이 없어요 (파일을 지운 영상은 되돌릴 수 없어요)"})
            continue
        dst, adir, adst = core.VIDEOS / n, adir_of(n, rec), core.ANALYSIS / _stem(n)
        if dst.exists():
            failed.append({"name": n, "error": "보관함에 같은 이름의 영상이 이미 있어요"})
            continue
        moved_dir = False
        try:
            if adir.is_dir() and not adst.exists():
                _move(adir, adst)
                moved_dir = True
            try:
                _move(src, dst)
            except BaseException:
                if moved_dir:
                    try:
                        _move(adst, adir)
                    except OSError:
                        pass
                raise
        except PermissionError:
            failed.append({"name": n, "error": _locked_msg(n)})
            continue
        except (OSError, RefsError) as e:
            failed.append({"name": n, "error": f"옮기지 못했어요 · {e}"})
            continue
        if not moved_dir:
            _rmtree(adir)  # 보관함 쪽에 예전 분석 폴더가 있으면 그쪽을 씀
        restored.append(n)
        vid = rec.get("videoId") or source.video_id(n)
        _archive(vid, add=True)
        c = data["channels"].get(rec.get("channelKey") or "") or {}
        if vid and not is_original(rec, data) and c.get("name"):  # 보관함 목록에서도 그 채널로 보이게 (이미 기록이 있으면 그대로)
            key = rec.get("channelKey") or ""
            try:
                source.remember_hints({vid: {k2: v for k2, v in {"kind": "other", "channel": c["name"], "channelUrl": c.get("url"),
                                                                 "channelId": key if key.startswith("UC") else None,
                                                                 "uploaderId": (c.get("handles") or [None])[0]}.items() if isinstance(v, str) and v}})
            except Exception:  # noqa: BLE001 — 출처는 곁가지
                pass
        log(f"보관함으로 되돌림 · {n}")
    if restored:
        try:
            _update(lambda d: [d["files"].pop(n, None) for n in restored])
        except OSError:
            pass  # 다음 목록에서 _adopt 가 정리 (보관함에 파일이 있으면 기록을 뺌)
    return {"restored": restored, "failed": failed}


def prune(names, log=print):
    """배운 영상의 파일만 지움 (분석 기록·스타일은 그대로). 이 파일 그대로 배운 기록(style_events)이 있는 영상만.
    → {pruned, kept(배운 기록이 없어 남김), failed}"""
    import style
    pruned, kept, failed = [], [], []
    for n in names:
        rec = find(n)
        p = path_of(n, rec) if rec else None
        if not p or not p.is_file():
            continue
        if is_original(rec):  # 보관함에서 옮긴 원본은 다시 받을 수 없으니 자동으로 지우지 않음
            kept.append(n)
            continue
        try:
            sig = style._sig(p)
            ev = json.loads((adir_of(n, rec) / "style_events.json").read_text(encoding="utf-8"))
            ok = isinstance(ev, dict) and ev.get("sig") == sig
        except (OSError, ValueError):
            ok = False
        if not ok:
            kept.append(n)
            continue

        def mark(d, n=n, sig=sig, on=True):  # 지문을 먼저 기록 → 파일을 지운 뒤 기록을 못 남겨 배운 기록을 못 쓰게 되는 일이 없게
            r = d["files"].get(n)
            if r is not None:
                if on:
                    r.update(pruned=True, sig=sig)
                else:
                    r.pop("pruned", None)
                    r.pop("sig", None)
        try:
            _update(mark)
        except OSError as e:
            failed.append({"name": n, "error": str(e)})
            continue
        try:
            _unlink(p)
        except OSError as e:
            failed.append({"name": n, "error": str(e)})
            try:
                _update(lambda d, n=n: mark(d, n, on=False))
            except OSError:
                pass  # 파일이 그대로 있으면 지문은 쓰이지 않음 (core.kept_sig 는 파일이 없을 때만)
            continue
        pruned.append(n)
    if pruned:
        log(f"배운 영상 파일 {len(pruned)}개를 지웠어요 (배운 기록은 남겨 둬서 스타일은 그대로예요)")
    return {"pruned": pruned, "kept": kept, "failed": failed}


# ---------- 받기 ----------

def _staged(vid):
    inc = root() / INCOMING
    for p in sorted(inc.glob(f"*_{vid}_*")) if inc.is_dir() else []:
        if p.suffix.lower() in core.VIDEO_EXTS and source.video_id(p.name) == vid:
            return p
    return None


def _settle(vid, meta, extra, log, secs=None):
    """받는 폴더의 영상 하나 → 채널 폴더로 옮기고 기록 → (상태, 파일 이름).
    상태: "new"(새로 받음) · "dup"(이미 있어 받은 것은 버림) · "none"(받는 폴더에 없음) · "locked"(다른 프로그램이 잡고 있어 못 옮김) · "error"."""
    p = _staged(vid)
    if p is None:
        return "none", None
    n = p.name

    def chan(data):  # 채널(폴더)을 먼저 정해 기록 · 이미 있으면 "dup"
        if n in data["files"] and path_of(n, data["files"][n]).is_file():
            return None
        key = _register(data, meta) if (meta.get("channel") or meta.get("channelId") or meta.get("uploaderId")) else _register(data, {}, key="")
        return key, data["channels"][key]["folder"]

    def put(data):
        data["files"][n] = dict({"channelKey": key, "folder": folder, "videoId": vid, "title": extra.get("title") or source._title_of(n),
                                 "how": "download", "saved": time.strftime("%Y-%m-%d %H:%M")},
                                **{k: v for k, v in extra.items() if k in ("url", "kind", "views") and v})
    try:
        kf = _update(chan)
        if kf is None:  # 이미 있음 → 받은 것은 버림
            _unlink(p)
            return "dup", n
        key, folder = kf
        if (root() / folder / n).is_file():  # 기록만 없고 파일은 이미 있음 → 받은 것은 버림 (다음 목록에서 기록됨)
            _unlink(p)
            return "dup", n
        _move(p, root() / folder / n, secs=secs)  # 기록 잠금 밖에서 (오래 기다려도 목록·다른 작업이 멈추지 않게)
    except PermissionError as e:
        log(f"  학습용 폴더로 옮기지 못했어요 (다른 프로그램이 파일을 쓰는 중) · {n} · {e}")
        return "locked", n
    except (OSError, RefsError) as e:
        log(f"  학습용 폴더로 옮기지 못했어요 · {n} · {e}")
        return "error", n
    try:
        _update(put)
    except OSError as e:  # 파일은 채널 폴더에 있음 → 다음 목록에서 _adopt 가 다시 기록
        log(f"  기록은 다음에 남길게요 · {n} · {e}")
    _take_orphan_analysis(n)
    return "new", n


def _take_orphan_analysis(n):
    """보관함에서 지운 같은 이름 영상의 옛 분석 폴더(analysis/<이름>)가 남아 있으면 학습용 쪽으로 옮김 (받아쓰기 등을 이어 씀 ·
    남겨 두면 헷갈림). 보관함에 그 파일이 다시 있거나 학습용 쪽에 이미 분석 폴더가 있으면 그대로 둠. 실패해도 괜찮음."""
    old = core.ANALYSIS / _stem(n)
    new = adir_of(n)
    if new is None or not old.is_dir() or (core.VIDEOS / n).exists() or new.exists():
        return
    try:
        _move(old, new)
    except (OSError, RefsError):
        pass


def _have_ids(data=None):
    data = data or load()
    return {r.get("videoId") or source.video_id(n) for n, r in data["files"].items()} - {None}


_FETCHING = [0]  # 받는 중인 작업 수 (그동안은 받는 폴더의 파일을 '남은 파일'로 보지 않음)


def fetch(items, log=print, cookies=None, label="학습용 영상 받는 중"):
    """items [{id, channel…(힌트), title, kind, url}] → 학습용으로 받기. 이미 학습용에 있거나 편집용 보관함에 있는 영상은 건너뜀.
    → {"got": [파일 이름], "failed": [영상 id], "skipped": [영상 id], "library": [보관함에 있는 영상 id], "locked": [영상 id],
       "why": {영상 id: 받지 못한 까닭(쉬운 안내 · 막히면 이 화면용 BLOCKED_MSG)}}"""
    have = _have_ids()
    todo, skipped, lib = [], [], []
    for it in items:
        vid = it.get("id")
        if not (isinstance(vid, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid)):
            continue
        if vid in have:
            skipped.append(vid)
        elif source._find_file(vid):
            lib.append(vid)
        elif all(t.get("id") != vid for t in todo):
            todo.append(it)
    if lib:
        log(f"  편집용 보관함에 이미 있는 영상 {len(lib)}개는 건너뛰어요 (보관함에서 '학습용으로 옮기기'로 옮길 수 있어요)")
    got, failed, locked, why = [], [], [], {}
    if not todo:
        return {"got": got, "failed": failed, "skipped": skipped, "library": lib, "locked": locked, "why": why}
    hints = {it["id"]: it for it in todo}
    done = {}  # 영상 id → 상태 (new·dup·locked·error)

    def take(vid, meta, extra, secs=None):
        st, n = _settle(vid, meta, extra, log, secs)
        if st != "none":
            done[vid] = st
        if st == "new":
            got.append(n)

    def meta_of(h):
        return {k: h[k] for k in ("channel", "channelId", "channelUrl", "uploaderId", "uploaderUrl") if isinstance(h.get(k), str) and h[k]}

    def remember(infos):
        for info in infos:
            vid = info.get("id")
            h = hints.get(vid) or {}
            meta = meta_of(h)
            meta.update(source.meta_of(info, channel_only=True))
            take(vid, meta, dict(h, title=info.get("title") or h.get("title"), url=info.get("webpage_url") or h.get("url")))
    (root() / INCOMING).mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _FETCHING[0] += 1
    try:
        failed = core.download([it["id"] for it in todo], log, cookies, dest=root() / INCOMING, archive=False, label=label, remember=remember,
                               why=why, blocked_msg=BLOCKED_MSG)
        for it in todo:  # 정보 없이 끝났거나(훅이 안 불림) 잠겨서 못 옮긴 영상 → 더 오래 기다리며 다시
            vid = it["id"]
            if vid not in failed and done.get(vid) in (None, "locked", "error"):
                take(vid, meta_of(it), it, SETTLE_SECS)
    finally:
        with _LOCK:
            _FETCHING[0] -= 1
    failed = list(failed)
    for it in todo:
        vid = it["id"]
        st = done.get(vid)
        if st == "dup":
            skipped.append(vid)
        elif st == "locked":
            locked.append(vid)
            log(f"  받은 영상을 다른 프로그램(백신 등)이 잡고 있어 아직 학습용 폴더로 옮기지 못했어요 · {vid} · 잠시 뒤 목록을 새로 고치면 들어와요")
        elif st != "new" and vid not in failed:
            failed.append(vid)
            log(f"  받은 영상 파일을 찾지 못했어요 · {vid}")
    return {"got": got, "failed": failed, "skipped": skipped, "library": lib, "locked": locked, "why": why}


def channel_names(key):
    """그 채널의 배울 수 있는 학습용 영상 이름 (파일이 있거나 지웠어도 배운 기록이 남은 것)."""
    data = load()
    return sorted(n for n, r in data["files"].items() if (r.get("channelKey") or "") == key and usable(n))


def _learn(keys, log, style_name=None, prune_after=False):
    """채널마다 '<채널명> 스타일' 배우기(같은 이름이 있으면 예전에 배운 영상까지 함께 다시 배움) → [{style, names, error?}]."""
    import style
    out = []
    data = load()
    for key in keys:
        names = channel_names(key)
        if not names:
            continue
        cname = (data["channels"].get(key) or {}).get("name") or "학습용"
        sname = re.sub(r'[\\/:*?"<>|]', "", style_name or f"{cname} 스타일").strip(" .") or "내 스타일"
        try:  # 같은 이름의 스타일이 있으면 그때 배운 영상 중 아직 쓸 수 있는 것도 함께
            old = json.loads((style.STYLES / f"{sname}.json").read_text(encoding="utf-8")).get("source")
            old = [x for x in (old if isinstance(old, list) else [old]) if isinstance(x, str)]
        except (OSError, ValueError, AttributeError):
            old = []
        names = list(dict.fromkeys([x for x in old if (core.VIDEOS / x).is_file() or usable(x)] + names))
        log(f"'{sname}' 배우는 중 · 영상 {len(names)}개")
        try:
            style.learn(sname, names, log)
        except style.StyleCancelled:
            raise
        except Exception as e:  # noqa: BLE001 — 받은 영상은 그대로 두고 알려 줌
            log(f"  '{sname}'을 배우지 못했어요 · {e}")
            out.append({"style": sname, "names": names, "error": str(e)})
            continue
        r = {"style": sname, "names": names}
        if prune_after:
            r["prune"] = prune([n for n in names if find(n)], log)
        out.append(r)
    return out


def _keys_of(names):
    data = load()
    return list(dict.fromkeys((data["files"].get(n) or {}).get("channelKey") or "" for n in names if n in data["files"]))


def add_channel(url, count=5, kind="videos", log=print, cookies=None, learn=True, prune_after=False, style_name=None):
    """채널 주소(또는 @핸들) → 인기 영상 count 개(이미 받은 것은 빼고 다음 순서)를 학습용으로 받고, 원하면 '<채널명> 스타일' 배우기."""
    url = str(url or "").strip()
    if not url:
        raise RefsError("채널 주소나 @핸들을 넣어 주세요")
    try:
        count = max(1, min(COUNT_MAX, int(count or 5)))
    except (TypeError, ValueError):
        raise RefsError("받을 영상 수를 숫자로 골라 주세요") from None
    kind = "shorts" if kind == "shorts" else "videos"
    core.set_progress(label="학습용 영상 찾는 중", item=url, pct=None, detail="채널 인기 영상을 불러오는 중")
    log(f"학습용 영상 찾는 중 · {url} · {'쇼츠' if kind == 'shorts' else '긴 영상'} 인기 {count}개")
    rows = [r for r in core.list_videos(kind, cookies, url, log) if r.get("id")]
    if not rows:
        raise RefsError("이 채널에서 영상을 찾지 못했어요. 주소를 확인해 주세요")
    have = _have_ids()
    pick = [r for r in rows if r["id"] not in have and not source._find_file(r["id"])][:count]
    lib = [r for r in rows[:count] if source._find_file(r["id"])]
    if not pick:
        res = {"got": [], "failed": [], "skipped": [r["id"] for r in rows[:count]], "library": [r["id"] for r in lib]}
        log("  받을 새 영상이 없어요 (인기 영상을 이미 다 받았어요)")
    else:
        res = fetch([dict(r, kind=kind) for r in pick], log, cookies)
    keys = _keys_of(res["got"]) or _channel_of_rows(rows)
    res["channels"] = [_view(k, load()) for k in keys]
    if learn and keys:
        res["learned"] = _learn(keys, log, style_name, prune_after)
    return res


def _channel_of_rows(rows):
    """목록의 채널이 이미 학습용에 있으면 그 열쇠 (새로 받은 게 없어도 다시 배울 수 있게)."""
    data = load()
    for r in rows[:1]:
        key = source.channel_key({k: r[k] for k in ("channel", "channelId", "channelUrl", "uploaderId", "uploaderUrl")
                                  if isinstance(r.get(k), str) and r[k]}, data)
        if key and key in data["channels"]:
            return [key]
    return []


def add_direction(key, log=print, cookies=None, learn=True, prune_after=False):
    """추천 방향(A/B/C)의 '먼저 받을 영상'을 한 번에 받고, 원하면 채널마다 '<채널명> 스타일' 배우기."""
    d = next((x for x in recommended().get("directions") or [] if x.get("key") == key), None)
    if not d:
        raise RefsError("그 방향을 찾지 못했어요")
    items = []
    for ch in d.get("channels") or []:
        for v in ch.get("videos") or []:
            it = {"id": v.get("id"), "title": v.get("title"), "kind": "shorts" if v.get("shorts") else "videos",
                  "url": v.get("url"), "channel": ch.get("channel"), "uploaderUrl": ch.get("url")}
            if ch.get("channelId"):  # 추천 채널 목록과 같은 열쇠 (정보 훅이 안 불려도 칸이 둘로 갈리지 않게)
                it["channelId"] = ch["channelId"]
            if ch.get("handle"):
                it["uploaderId"] = ch["handle"]
            items.append(it)
    log(f"방향 {key} · {d.get('title')} 추천 영상 {len(items)}개를 학습용으로 받아요")
    res = fetch(items, log, cookies)
    res["direction"] = key
    keys = _keys_of(res["got"])
    res["channels"] = [_view(k, load()) for k in keys]
    if learn and keys:
        res["learned"] = _learn(keys, log, None, prune_after)
    return res


def learn_channel(key, log=print, prune_after=False, style_name=None):
    """학습용에 있는 그 채널 영상으로 '<채널명> 스타일' 다시 배우기."""
    if not channel_names(key):
        raise RefsError("이 채널에는 배울 수 있는 영상이 없어요")
    return {"learned": _learn([key], log, style_name, prune_after)}
