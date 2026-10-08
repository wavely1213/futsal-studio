"""보관함 영상 이름 바꾸기 (D-073) — 영상 파일과 그 영상의 작업을 함께 옮김.

분석·편집본·썸네일 디자인은 영상 파일 이름으로 찾는다(core.adir · editor._ppath · thumb._doc_path). 그래서 탐색기에서
'IMG_4830.mp4'를 '패스 앤 무브 레슨.mp4'로 바꾸면 받아쓰기·가편집·손본 편집본이 모두 끊기고 '아직 안 함'으로 돌아갔다.
- rename: 앱 안 [이름 바꾸기] → 영상 · 분석 폴더(받아쓰기·파형·장면) · 편집본(+자동 백업) · 썸네일 디자인 · 출처 기록을 함께,
  안의 영상 이름(편집본 미디어·장면 주소·타임라인 첫 줄)도 새 이름으로. 앞의 셋 중 하나라도 못 옮기면 모두 되돌림.
- orphans·annotate·attach: 탐색기에서 이미 바꿨으면 크기·수정 시각(file_sig)이 같은 옛 이름 작업을 찾아 [이어 붙이기]
  (붙일 때 편집본에 적힌 영상 길이도 확인).
완성본 폴더의 내보낸 영상·올리기 키트·썸네일 그림 파일 이름은 그대로 둔다 (KNOWN_ISSUES I-070).
"""
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import core
import editor
import intake
import source
import thumb
import updater

BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}  # Windows 가 못 쓰는 이름
NAME_MAX = 120  # 확장자 빼고 (작업 폴더 경로가 길어도 Windows 260자 안에 들게)
CACHE_ONLY = ("frames", "waveform_", "thumbs2.", "poster.jpg", "proxy.mp4", "proxy.part.mp4", "rev_")  # 다시 만들 수 있는 캐시
_LOCK = threading.Lock()
_ORPH = {"key": None, "t": 0.0, "list": []}


class RenameError(ValueError):
    """이름을 바꿀 수 없음 (화면에 그대로 보이는 해요체 한 줄)."""


def clean_new(old, new):
    """사용자가 적은 새 이름 → 파일 이름 (확장자는 원래 것 · Windows 규칙: 못 쓰는 글자·끝의 점과 공백·예약된 이름)."""
    ext = Path(old).suffix
    t = re.sub(r"\s+", " ", str(new or "")).strip()
    if ext and t.lower().endswith(ext.lower()):
        t = t[: -len(ext)]
    t = t.strip(" .")
    if not t:
        raise RenameError("새 이름을 적어 주세요")
    if BAD.search(t):
        raise RenameError('이름에 \\ / : * ? " < > | 는 쓸 수 없어요')
    if len(t) > NAME_MAX:
        raise RenameError(f"이름이 너무 길어요 · {NAME_MAX}자 안으로 줄여 주세요")
    if t.split(".")[0].upper() in RESERVED:
        raise RenameError("Windows 가 쓰는 이름이라 쓸 수 없어요 · 다른 이름을 골라 주세요")
    return t + ext


def _frame_fix(s, old, new):
    """편집·썸네일 안의 장면 주소 '/frame?name=<옛 이름>&t=…' → 새 이름 (화면은 encodeURIComponent 로, 서버는 그대로 씀)."""
    if not s.startswith("/frame?"):
        return s
    q = parse_qs(urlparse(s).query)
    if (q.get("name") or [None])[0] != old:
        return s
    raw = s.replace(f"name={old}&", f"name={new}&", 1) if f"name={old}&" in s else None
    return raw or s.replace("name=" + quote(old, safe="-_.!~*'()"), "name=" + quote(new, safe="-_.!~*'()"), 1)


def fix_refs(obj, old, new):
    """JSON 안의 이 영상 이름을 새 이름으로 (편집본 미디어 {src: videos, file} · source · 장면 주소) → 고친 곳 수."""
    n = 0
    if isinstance(obj, dict):
        if obj.get("file") == old and obj.get("src", "videos") == "videos" and ("kind" in obj or "src" in obj):
            obj["file"] = new
            n += 1
        if obj.get("source") == old:
            obj["source"] = new
            n += 1
        for k, v in list(obj.items()):
            if isinstance(v, str):
                f = _frame_fix(v, old, new)
                if f != v:
                    obj[k] = f
                    n += 1
            else:
                n += fix_refs(v, old, new)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                f = _frame_fix(v, old, new)
                if f != v:
                    obj[i] = f
                    n += 1
            else:
                n += fix_refs(v, old, new)
    return n


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _rewrite(src, dst, old, new, bump=False):
    """JSON 파일을 고쳐 dst 에 다 쓴 뒤 src 를 지움 (src == dst 면 제자리) · bump: 편집본 판 번호 올림(열린 편집실이 덮어쓰지 않게)."""
    data = _read(src)
    fix_refs(data, old, new)
    if bump and isinstance(data, dict):
        data["rev"] = int(data.get("rev") or 0) + 1
    updater.write_atomic(dst, json.dumps(data, ensure_ascii=False), fsync=True)
    if Path(src) != Path(dst):
        try:
            Path(src).unlink()
        except OSError:
            pass


def _seqs(proj_path):
    try:
        return len(_read(proj_path).get("sequences") or [])
    except (OSError, ValueError, AttributeError):
        return 0


def _plan(old, new, old_dir):
    """옮길 곳들 (영상 이름을 바꾸기 전에 정해 미리 겹침 확인) → dict."""
    new_dir = core.ANALYSIS / core._stem_key(new)
    return {"old_dir": old_dir, "new_dir": new_dir,
            "old_proj": editor.PROJECTS / f"{old_dir.name}.json", "new_proj": editor.PROJECTS / f"{new_dir.name}.json",
            "old_doc": thumb.THUMBS / f"{old_dir.name}.json", "new_doc": thumb.THUMBS / f"{new_dir.name}.json"}


def _same_path(a, b):
    return str(a).casefold() == str(b).casefold()


def _only_cache(d):
    """그 폴더에 다시 만들 수 있는 캐시만 있는지 (장면·파형·띠 그림·미리보기 파일)."""
    try:
        return all(x.name.startswith(CACHE_ONLY) for x in d.iterdir())
    except OSError:
        return False


def _check(pl, attach=False):
    """새 이름 자리에 다른 작업이 있으면 RenameError (덮어쓰지 않음)."""
    if pl["new_dir"].exists() and not _same_path(pl["new_dir"], pl["old_dir"]):
        if not (attach and _only_cache(pl["new_dir"])):
            raise RenameError("새 이름으로 된 예전 작업(받아쓰기·편집본)이 이미 있어요 · 다른 이름을 골라 주세요")
    for k in ("proj", "doc"):
        if pl["new_" + k].exists() and not _same_path(pl["new_" + k], pl["old_" + k]):
            raise RenameError("새 이름으로 된 예전 편집본·썸네일이 이미 있어요 · 다른 이름을 골라 주세요")


def _move_work(old, new, pl, log):
    """분석 폴더 → 편집본 → (여기까지 실패하면 되돌리고 오류) → 백업·썸네일·타임라인 첫 줄·출처·다른 편집본 (곁가지: 실패하면 기록만).
    → 옮긴 것 {"analysis", "project", "backups", "thumbs", "refs"}."""
    out = {"analysis": False, "project": False, "backups": 0, "thumbs": False, "refs": 0}
    od, nd = pl["old_dir"], pl["new_dir"]
    if od.exists() and not od == nd:
        if nd.exists() and _only_cache(nd) and not _same_path(nd, od):
            shutil.rmtree(nd, ignore_errors=True)
        os.rename(od, nd)
        out["analysis"] = True
    try:
        if pl["old_proj"].exists():
            with editor._SAVE_LOCK:
                _rewrite(pl["old_proj"], pl["new_proj"], old, new, bump=True)
            out["project"] = True
    except Exception:
        if out["analysis"]:
            os.rename(nd, od)
        raise
    # ---- 곁가지: 실패해도 이름 바꾸기는 그대로 (기록만) ----
    bdir = editor.PROJECTS / "backup"
    pre, npre = pl["old_proj"].stem + "__", pl["new_proj"].stem + "__"
    try:
        for f in sorted(bdir.iterdir()) if bdir.is_dir() and pre != npre else []:
            if f.name.startswith(pre) and f.name.endswith(".json"):
                try:
                    _rewrite(f, bdir / (npre + f.name[len(pre):]), old, new)
                    out["backups"] += 1
                except (OSError, ValueError) as e:
                    log(f"  편집본 백업 하나는 옮기지 못했어요 · {f.name} · {e}")
    except OSError:
        pass
    try:
        for suf in (".json", ".json.bak"):
            s, d = pl["old_doc"].with_suffix(suf), pl["new_doc"].with_suffix(suf)
            if s.exists():
                _rewrite(s, d, old, new)
                out["thumbs"] = True
        for f in (nd / "frames").glob("*.json") if (nd / "frames").is_dir() else []:  # 장면 후보 캐시의 장면 주소
            _rewrite(f, f, old, new)
    except (OSError, ValueError) as e:
        log(f"  썸네일 디자인은 옮기지 못했어요 · {e}")
    tl = nd / "transcript_timeline.md"
    try:  # 첫 줄 '# 타임라인: <이름>' = 이 폴더의 주인 (core._folder_owner)
        if tl.exists():
            lines = tl.read_text(encoding="utf-8").split("\n", 1)
            if lines[0].startswith("# 타임라인: "):
                updater.write_atomic(tl, f"# 타임라인: {new}" + ("\n" + lines[1] if len(lines) > 1 else ""))
    except OSError:
        pass
    try:
        source.rename_file(old, new)
    except (OSError, ValueError) as e:
        log(f"  출처 기록은 옮기지 못했어요 (보관함의 출처 표시를 다시 골라 주세요) · {e}")
    try:  # 다른 편집본이 이 영상을 미디어로 쓰면 그 이름도
        for f in editor.PROJECTS.glob("*.json"):
            if f == pl["new_proj"]:
                continue
            try:
                if json.dumps(old, ensure_ascii=False)[1:-1] not in f.read_text(encoding="utf-8"):
                    continue
                data = _read(f)
                if fix_refs(data, old, new):
                    data["rev"] = int(data.get("rev") or 0) + 1
                    with editor._SAVE_LOCK:
                        updater.write_atomic(f, json.dumps(data, ensure_ascii=False), fsync=True)
                    out["refs"] += 1
            except (OSError, ValueError) as e:
                log(f"  다른 편집본 하나는 고치지 못했어요 · {f.name} · {e}")
    except OSError:
        pass
    _ORPH["key"] = None
    return out


def _say(old, new, out, log, how="이름을 바꿨어요"):
    what = [x for x, ok in (("받아쓰기", out["analysis"]), ("편집본", out["project"]), ("썸네일", out["thumbs"])) if ok]
    log(f"{how} · {old} → {new}" + (f" · {'·'.join(what)}도 함께 옮겼어요" if what else ""))


def rename(old, new, log=print):
    """보관함 영상 old 의 이름을 new 로 (확장자는 그대로) + 그 작업을 함께 → {"name", "analysis", "project", "backups", "thumbs", "refs"}."""
    old = editor.safe_name(old)
    src = core.VIDEOS / old
    if not src.is_file():
        raise FileNotFoundError("보관함에서 영상을 찾지 못했어요. 목록을 새로 고친 뒤 다시 골라 주세요")
    if intake.copying(src):
        raise RenameError("아직 복사 중인 영상이에요 · 다 들어온 뒤에 바꿔 주세요")
    new = clean_new(old, new)
    if new == old:
        return {"name": old, "same": True}
    with _LOCK:
        others = [p.name for p in core.VIDEOS.iterdir() if p.name != old]
        if any(n.casefold() == new.casefold() for n in others):
            raise RenameError("같은 이름 영상이 이미 있어요 · 다른 이름을 골라 주세요")
        if any(core._stem_key(n).casefold() == core._stem_key(new).casefold() and core.is_video_file(n) for n in others):
            raise RenameError("확장자만 다른 같은 이름 영상이 있어요 · 다른 이름을 골라 주세요")
        pl = _plan(old, new, core.adir(old))
        _check(pl)
        try:
            os.rename(src, core.VIDEOS / new)
        except OSError as e:
            raise RenameError("다른 프로그램(플레이어·편집 프로그램·탐색기 미리 보기)이 이 영상을 쓰고 있어요 · 닫고 다시 해 주세요") from e
        core._STEMS["key"] = None
        try:
            out = _move_work(old, new, pl, log)
        except Exception as e:
            try:
                os.rename(core.VIDEOS / new, src)
            except OSError:
                pass
            core._STEMS["key"] = None
            raise RenameError("받아쓰기·편집본 폴더를 옮기지 못해서 이름을 되돌렸어요 · 편집실·썸네일 창과 탐색기를 닫고 다시 해 주세요") from e
    _say(old, new, out, log)
    return dict(out, name=new)


# ---------- 탐색기에서 이름을 바꾼 영상: 옛 이름 작업 이어 붙이기 ----------

def orphans():
    """분석 폴더 중 그 영상(옛 이름)이 보관함에 없고 찾을 때의 크기·수정 시각 기록(file_sig)이 있는 것 →
    [{"dir": 폴더, "old": 옛 이름, "size", "mtime_ns"}]. 폴더 목록이 바뀌었을 때만 다시 (화면이 1초마다 물어도 가볍게)."""
    try:
        key = (str(core.ANALYSIS), core.ANALYSIS.stat().st_mtime_ns, core.VIDEOS.stat().st_mtime_ns, editor.PROJECTS.stat().st_mtime_ns)
    except OSError:
        return []
    with _LOCK:
        if _ORPH["key"] == key and time.monotonic() - _ORPH["t"] < 30:
            return list(_ORPH["list"])
    have = {p.name.casefold() for p in core.VIDEOS.iterdir() if core.is_video_file(p.name)}
    out = []
    for d in sorted(core.ANALYSIS.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or not (d / "transcript_timeline.md").exists():
            continue
        sig = intake._stored(d)
        old = core._folder_owner(d)
        if not sig or not old or old.casefold() in have:
            continue
        out.append({"dir": d, "old": old, "size": sig[0], "mtime_ns": sig[1]})
    with _LOCK:
        _ORPH.update(key=key, t=time.monotonic(), list=out)
    return list(out)


def _match(name, orph):
    """보관함 영상 name 이 옛 작업 orph 의 영상과 같은 파일인지: 크기가 같고 수정 시각이 2초 안 (이름만 바꾸면 그대로)."""
    try:
        st = (core.VIDEOS / name).stat()
    except OSError:
        return False
    if st.st_size != orph["size"]:
        return False
    return orph["mtime_ns"] is None or abs(st.st_mtime_ns - orph["mtime_ns"]) <= intake.MTIME_SLACK_NS


def annotate(rows):
    """보관함 목록(core.local_videos)에서 아직 편집점을 안 찾은 영상에 옛 이름 작업이 있으면 renamedFrom {old, seqs}."""
    try:
        orph = orphans()
    except OSError:
        return rows
    if not orph:
        return rows
    for r in rows:
        if r.get("analyzed") or r.get("copying"):
            continue
        hit = next((o for o in orph if _match(r["name"], o)), None)
        if hit:
            r["renamedFrom"] = {"old": hit["old"], "seqs": _seqs(editor.PROJECTS / f"{hit['dir'].name}.json")}
    return rows


def attach(name, old, log=print):
    """탐색기에서 old → name 으로 바꾼 영상에 옛 이름 작업(받아쓰기·편집본·썸네일·출처)을 이어 붙임."""
    name = editor.safe_name(name)
    if not (core.VIDEOS / name).is_file():
        raise FileNotFoundError("보관함에서 영상을 찾지 못했어요. 목록을 새로 고친 뒤 다시 골라 주세요")
    with _LOCK:
        _ORPH["key"] = None
    hit = next((o for o in orphans() if o["old"] == old), None)
    if hit is None or not _match(name, hit):
        raise RenameError("이어 붙일 예전 작업을 찾지 못했어요 · 크기나 수정 시각이 다른 파일이에요")
    proj = editor.PROJECTS / f"{hit['dir'].name}.json"
    try:  # 길이도 같은지 (편집본에 적힌 영상 길이 · 크기·시각이 우연히 같은 다른 영상을 붙이지 않게)
        want = float((_read(proj).get("info") or {}).get("duration") or 0) if proj.exists() else 0.0
    except (OSError, ValueError, AttributeError):
        want = 0.0
    if want:
        got = float(editor.probe(core.VIDEOS / name).get("duration") or 0)
        if abs(got - want) > max(1.0, want * 0.01):
            raise RenameError(f"예전 작업의 영상 길이({want:.0f}초)와 이 영상 길이({got:.0f}초)가 달라요 · 다른 영상이라 이어 붙이지 않았어요")
    with _LOCK:
        pl = _plan(old, name, hit["dir"])
        if (pl["new_dir"] / "transcript_timeline.md").exists():
            raise RenameError("이 영상은 이미 편집점을 찾았어요 · 예전 작업을 붙이면 덮어쓰게 돼요")
        _check(pl, attach=True)
        out = _move_work(old, name, pl, log)
    _say(old, name, out, log, "예전 이름의 작업을 이어 붙였어요")
    return dict(out, name=name)
