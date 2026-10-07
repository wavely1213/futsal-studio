"""보관함에 들어오는 영상 살피기 — 복사 중인 촬영본 · 아직 못 쓰는 형식 · 편집점을 찾은 뒤 바뀐 파일.

탐색기로 몇 GB 촬영본을 보관함 폴더에 옮기면 복사가 끝나기 전부터 파일이 보인다. 그때 편집점을 찾으면 앞부분만 받아쓰고
'편집점 준비됨'이 붙어 버린다 (복사가 끝나도 분석·가편집은 그대로). 그래서
- copying: 크기·수정 시각이 STABLE 초 동안 그대로이고, Windows 에서는 그 파일을 쓰기로 연 프로그램도 없어야 다 들어온 것.
  보관함 목록(/api/state, 1초마다)이 부를 때마다 본 값을 기억해 비교한다 (처음 본 파일은 기다리지 않음 · 자라면 다음 관찰에서).
- busy: 편집점 찾기 직전 확인 (쓰는 프로그램이 있거나, 방금 바뀐 파일이 PROBE 초 사이에 또 바뀌면 복사 중)
- unusable: 캠코더 .MTS 처럼 영상이지만 아직 못 쓰는 형식 (예전에는 말없이 목록에서 빠졌음)
- remember·changed: 편집점을 찾을 때의 파일 크기를 분석 폴더에 남겨, 나중에 파일이 바뀌면(덜 복사된 채 찾았음) 알려 줌
표준 라이브러리만 쓴다 (경로를 받아 판단 · core 가 함수 안에서, app 이 불러 씀).
"""
import json
import sys
import threading
import time
from pathlib import Path

STABLE = 5.0      # 크기·수정 시각이 이만큼(초) 그대로면 다 들어온 것으로 봄
PROBE = 1.5       # 편집점 찾기 직전, 방금 바뀐 파일을 지켜보는 시간
# 영상이지만 아직 못 쓰는 형식 (편집실 미리보기(WebView2)가 열지 못함) — 쓸 수 있는 형식은 core.VIDEO_EXTS
UNUSABLE_EXTS = {".mts", ".m2ts", ".m2t", ".ts", ".avi", ".wmv", ".asf", ".mpg", ".mpeg", ".vob", ".mod", ".tod",
                 ".3gp", ".3g2", ".flv", ".f4v", ".mxf", ".dv", ".rm", ".rmvb", ".divx", ".ogv", ".insv", ".360"}
SIG_FILE = "file_sig.json"  # 분석 폴더 안: 편집점을 찾을 때의 영상 크기
_SEEN = {}        # 파일 경로 → {"key": (크기, 수정 시각 ns), "t": 그 값을 처음 본 때(monotonic), "ok": 쓰는 프로그램 없음 확인}
_SIGS = {}        # 분석 폴더 → (file_sig.json 수정 시각 ns, 크기)
_LOCK = threading.Lock()
_mono, _wall, _sleep = time.monotonic, time.time, time.sleep


def _writer_open(path):
    """Windows: 다른 프로그램(탐색기 복사·카메라 가져오기·녹화)이 이 파일을 쓰기로 열어 두었는지.
    읽기만 허용(FILE_SHARE_READ)으로 열어 보면, 쓰는 손잡이가 있을 때만 공유 위반(32)으로 실패한다
    (미리보기·ffmpeg·백신처럼 읽기만 하는 프로그램은 방해하지 않음). 다른 운영체제는 False (크기·시각으로만 봄)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)  # GENERIC_READ · FILE_SHARE_READ · OPEN_EXISTING
    if not h or h == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
        return ctypes.get_last_error() == 32  # ERROR_SHARING_VIOLATION
    k32.CloseHandle(h)
    return False


def copying(path, st=None):
    """보관함 목록용: 이 파일이 아직 들어오는 중(복사 중)인지. 부를 때마다 본 크기·수정 시각을 기억해 비교한다."""
    path = Path(path)
    try:
        st = st or path.stat()
    except OSError:
        return False
    key, now, k = (st.st_size, st.st_mtime_ns), _mono(), str(path)
    with _LOCK:
        rec = _SEEN.get(k)
        if rec is None:  # 처음 봄: 기다리지 않음 (켤 때·막 받은·막 묶은 영상이 '복사 중'이 되지 않게) — 쓰는 중이면 Windows 는
            rec = _SEEN[k] = {"key": key, "t": now - STABLE, "ok": False}  # 쓰기 손잡이로, 그 밖은 다음 관찰(1초 뒤)에 크기가 바뀌어서 앎
        elif rec["key"] != key:
            rec = _SEEN[k] = {"key": key, "t": now, "ok": False}
        if now - rec["t"] < STABLE:
            return True
        if rec["ok"]:
            return False
    if _writer_open(path):  # 크기는 그대로여도(탐색기는 처음에 크기를 다 잡아 둠) 쓰는 중
        return True
    with _LOCK:
        if _SEEN.get(k) is rec:
            rec["ok"] = True
    return False


def forget(paths):
    """목록에 없는 파일의 기억은 지움 (지웠거나 이름을 바꾼 파일)."""
    keep = {str(Path(p)) for p in paths}
    with _LOCK:
        for k in [k for k in _SEEN if k not in keep]:
            del _SEEN[k]


def busy(path):
    """편집점 찾기 직전: 다른 프로그램이 지금 이 파일을 쓰는 중인지 (쓰는 프로그램이 있거나, 방금 바뀐 파일이 잠깐 사이에 또 바뀜)."""
    path = Path(path)
    try:
        a = path.stat()
        if _writer_open(path):
            return True
        if not 0 <= _wall() - a.st_mtime < STABLE:
            return False
        _sleep(PROBE)
        b = path.stat()
    except OSError:
        return False
    return (a.st_size, a.st_mtime_ns) != (b.st_size, b.st_mtime_ns)


def copying_msg(name):
    return (f"'{name}'은(는) 아직 복사 중이에요. 복사가 끝나면(보관함의 '복사 중' 표시가 사라지면) 다시 눌러 주세요")


def unusable(folder):
    """보관함 폴더의 아직 못 쓰는 형식 영상 [{name, size_mb, ext}] (이름 순)."""
    out = []
    try:
        items = sorted(Path(folder).iterdir())
    except OSError:
        return out
    for p in items:
        if p.suffix.lower() in UNUSABLE_EXTS:
            try:
                if not p.is_file():
                    continue
                size = p.stat().st_size
            except OSError:
                continue
            out.append({"name": p.name, "size_mb": round(size / 1e6, 1), "ext": p.suffix.lstrip(".").upper()})
    return out


# ---------- 편집점을 찾은 뒤 파일이 바뀌었는지 ----------

def sig(path):
    """편집점을 찾기 시작할 때의 파일 크기 (기록용)."""
    try:
        return {"size": Path(path).stat().st_size}
    except OSError:
        return None


def remember(outdir, s):
    """편집점 찾기를 마친 분석 폴더에 그때의 파일 크기를 남김 (실패해도 그만)."""
    if not s:
        return
    try:
        Path(outdir, SIG_FILE).write_text(json.dumps(s), encoding="utf-8")
    except OSError:
        pass


def changed(outdir, size):
    """편집점을 찾은 뒤 영상 파일 크기가 바뀌었는지 (기록이 없는 예전 분석은 모름 → False)."""
    p = Path(outdir, SIG_FILE)
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return False
    k = str(p)
    with _LOCK:
        hit = _SIGS.get(k)
    if hit is None or hit[0] != mt:
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("size")
        except (OSError, ValueError, AttributeError):
            got = None
        hit = (mt, got if isinstance(got, int) else None)
        with _LOCK:
            _SIGS[k] = hit
    return hit[1] is not None and hit[1] != size


def annotate(rows, folder, adir):
    """보관함 목록(core.local_videos)에 copying(복사 중)·changed(편집점을 찾은 뒤 파일이 바뀜)를 붙임.
    adir: 영상 이름 → 분석 폴더 (core.adir)."""
    folder = Path(folder)
    paths = []
    for r in rows:
        p = folder / r["name"]
        paths.append(p)
        try:
            st = p.stat()
        except OSError:
            continue
        if copying(p, st):
            r["copying"] = True
        elif r.get("analyzed"):
            try:
                if changed(adir(r["name"]), st.st_size):
                    r["changed"] = True
            except Exception:  # noqa: BLE001 — 곁가지: 목록은 그대로
                pass
    forget(paths)
    return rows
