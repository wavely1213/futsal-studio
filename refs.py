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
READ_TRIES = 5
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


def _retry(fn, tries=TRIES):
    """Windows: 다른 프로그램이 파일을 잠깐 잡고 있으면(PermissionError) 조금 뒤 다시."""
    for k in range(tries):
        try:
            return fn()
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(WAIT)


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


def _events_ok(name, rec=None):
    """배운 기록(style_events.json)이 있는지."""
    d = adir_of(name, rec)
    return bool(d) and (d / "style_events.json").is_file()


def usable(name):
    """스타일 배우기에 쓸 수 있는지: 파일이 있거나, 파일을 지웠어도 배운 기록이 남아 있음."""
    rec = find(name)
    if not rec:
        return False
    p = path_of(name, rec)
    return p.is_file() or (rec.get("pruned") and _events_ok(name, rec))


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
    """채널 폴더에 기록 없이 있는 영상(옮기다 끊긴 것 등)을 그 채널 영상으로 기록 → 고친 게 있으면 True."""
    found = []
    folders = {c["folder"]: k for k, c in data["channels"].items() if c.get("folder")}
    for folder, key in folders.items():
        try:
            items = list((root() / folder).iterdir())
        except OSError:
            continue
        for p in items:
            if p.is_file() and p.suffix.lower() in core.VIDEO_EXTS and p.name not in data["files"]:
                found.append((p.name, key, folder))
    if not found:
        return False

    def fn(d):
        for n, key, folder in found:
            d["files"].setdefault(n, {"channelKey": key, "folder": folder, "videoId": source.video_id(n), "how": "found",
                                      "saved": time.strftime("%Y-%m-%d %H:%M")})
    _update(fn)
    return True


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
                 styles=uses.get(n, []), kind=rec.get("kind") or "", how=rec.get("how") or "")
        v["usable"] = present or (v["pruned"] and learned)
        vids.append(v)
        g = groups.setdefault(key, dict(_view(key, data), count=0, size_mb=0.0, learned=0))
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

def _move(src, dst):
    """같은 드라이브면 바로 이름 바꾸기 · 다른 드라이브면 복사 후 지우기. 잠겨 있으면 몇 번 다시."""
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
    _retry(go)


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


def move_from_library(names, log=print, allow_own=False):
    """편집용 보관함의 영상 → 학습용 영상 (파일 + 분석 폴더). 다른 채널 영상만 옮기고,
    풋살사관학교·내 촬영본·출처 모르는 영상은 allow_own(사용자가 확인함)일 때만. → {moved, skipped, failed}"""
    sdata = source.load()
    moved, skipped, failed = [], [], []
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
        log(f"학습용으로 옮김 · {n}")
    return {"moved": moved, "skipped": skipped, "failed": failed}


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
        if desc["kind"] == "own":  # 사용자가 확인하고 옮긴 우리 채널·내 촬영본 영상은 그 이름의 폴더로
            k = _register(data, {"channel": "풋살사관학교"})
        elif desc["kind"] == "footage":
            k = _register(data, {"channel": "내 촬영본"})
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
    try:
        _update(put)
    except OSError:
        pass  # 다음 목록에서 _adopt 가 채널 폴더의 파일을 다시 기록함


def delete(names=None, channel=None, log=print):
    """학습용 영상 지우기 (파일 + 분석 기록 + 기록) · channel 을 주면 그 채널 폴더째. → {removed, failed}"""
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
                _rmtree(root() / c["folder"])
    if gone or channel is not None:
        _update(fn)
    if removed:
        log(f"학습용 영상 {len(removed)}개를 지웠어요")
    return {"removed": removed, "failed": failed}


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


def _settle(vid, meta, extra, log):
    """받는 폴더의 영상 하나 → 채널 폴더로 옮기고 기록 → 파일 이름 (못 찾으면 None)."""
    p = _staged(vid)
    if p is None:
        return None
    n = p.name

    def fn(data):
        if n in data["files"]:
            old = data["files"][n]
            if path_of(n, old).is_file():  # 이미 있음 → 받은 것은 버림
                _unlink(p)
                return
        key = _register(data, meta) if (meta.get("channel") or meta.get("channelId") or meta.get("uploaderId")) else _register(data, {}, key="")
        folder = data["channels"][key]["folder"]
        _move(p, root() / folder / n)
        data["files"][n] = dict({"channelKey": key, "folder": folder, "videoId": vid, "title": extra.get("title") or source._title_of(n),
                                 "how": "download", "saved": time.strftime("%Y-%m-%d %H:%M")},
                                **{k: v for k, v in extra.items() if k in ("url", "kind", "views") and v})
    try:
        _update(fn)
    except (OSError, RefsError) as e:
        log(f"  학습용 폴더로 옮기지 못했어요 · {n} · {e}")
        return None
    return n


def _have_ids(data=None):
    data = data or load()
    return {r.get("videoId") or source.video_id(n) for n, r in data["files"].items()} - {None}


def fetch(items, log=print, cookies=None, label="학습용 영상 받는 중"):
    """items [{id, channel…(힌트), title, kind, url}] → 학습용으로 받기. 이미 학습용에 있거나 편집용 보관함에 있는 영상은 건너뜀.
    → {"got": [파일 이름], "failed": [영상 id], "skipped": [영상 id], "library": [보관함에 있는 영상 id]}"""
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
    got, failed = [], []
    if not todo:
        return {"got": got, "failed": failed, "skipped": skipped, "library": lib}
    hints = {it["id"]: it for it in todo}
    (root() / INCOMING).mkdir(parents=True, exist_ok=True)

    def remember(infos):
        for info in infos:
            vid = info.get("id")
            h = hints.get(vid) or {}
            meta = {k: h[k] for k in ("channel", "channelId", "channelUrl", "uploaderId", "uploaderUrl") if isinstance(h.get(k), str) and h[k]}
            meta.update(source.meta_of(info, channel_only=True))
            n = _settle(vid, meta, dict(h, title=info.get("title") or h.get("title"), url=info.get("webpage_url") or h.get("url")), log)
            if n:
                got.append(n)
    failed = core.download([it["id"] for it in todo], log, cookies, dest=root() / INCOMING, archive=False, label=label, remember=remember)
    for it in todo:  # 정보 없이 끝난 영상(훅이 안 불림)도 힌트로 정리
        vid = it["id"]
        if vid not in failed and not any(source.video_id(n) == vid for n in got):
            meta = {k: it[k] for k in ("channel", "channelId", "channelUrl", "uploaderId", "uploaderUrl") if isinstance(it.get(k), str) and it[k]}
            n = _settle(vid, meta, it, log)
            if n:
                got.append(n)
    return {"got": got, "failed": failed, "skipped": skipped, "library": lib}


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
            items.append({"id": v.get("id"), "title": v.get("title"), "kind": "shorts" if v.get("shorts") else "videos",
                          "url": v.get("url"), "channel": ch.get("channel"), "uploaderUrl": ch.get("url")})
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
