"""보관함에 들어오는 영상 살피기 — 복사 중인 촬영본 · 아직 못 쓰는 형식 · 편집점을 찾은 뒤 바뀐 파일.

탐색기로 몇 GB 촬영본을 보관함 폴더에 옮기면 복사가 끝나기 전부터 파일이 보인다. 그때 편집점을 찾으면 앞부분만 받아쓰고
'편집점 준비됨'이 붙어 버린다 (복사가 끝나도 분석·가편집은 그대로). 그래서
- copying: 크기·수정 시각이 STABLE 초 동안 그대로이고, Windows 에서는 그 파일을 쓰기로 연 프로그램도 없어야 다 들어온 것.
  보관함 목록(/api/state, 1초마다)이 부를 때마다 본 값을 기억해 비교한다 (처음 본 파일은 기다리지 않음 · 자라면 다음 관찰에서).
- busy: 편집점 찾기 직전 확인 (쓰는 프로그램이 있거나, 방금 바뀐 파일이 PROBE 초 사이에 또 바뀌면 복사 중)
- unusable: 캠코더 .MTS 처럼 영상이지만 아직 못 쓰는 형식 (예전에는 말없이 목록에서 빠졌음)
- remember·changed: 편집점을 찾을 때의 파일 크기·수정 시각을 분석 폴더에 남겨, 나중에 파일이 바뀌면(덜 복사된 채 찾았음) 알려 줌
  · 바뀐 파일로 편집점을 다시 찾으면 반쪽 파일로 만든 파형·썸네일·미리보기 파일을 지움(clear_media_cache)
- convert: 못 쓰는 형식(.MTS 등)을 ffmpeg 로 MP4 로 바꿔 보관함에 넣음 (원본은 보관함 안 '바꾸기 전 원본' 폴더로 옮겨 그대로)
  · ffmpeg 는 core.run/core.popen 으로 (검은 창 없음 · 앱이 꺼지면 Job Object 로 같이 꺼짐) · 마지막 파일은 updater.replace_retry
- nested·unnest: 폴더째 넣은 촬영본(휴대폰·SD카드 날짜 폴더) — 하위 폴더별 영상 수를 알리고, 누르면 보관함 바로 안으로 꺼냄 (D-460)
- media·quality_flag: 보관함 줄의 해상도·비트레이트(뒤에서 ffmpeg 로 한 번씩 읽어 기억) · 카카오톡으로 받아 압축된 사본 알림 (D-462)
표준 라이브러리 + 앱 공통 도구(core·updater, 함수 안에서 불러옴)만 쓴다 (경로를 받아 판단 · core 가 함수 안에서, app 이 불러 씀 ·
ffmpeg 경로는 부르는 쪽이 줌).
"""
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

STABLE = 5.0      # 크기·수정 시각이 이만큼(초) 그대로면 다 들어온 것으로 봄
PROBE = 1.5       # 편집점 찾기 직전, 방금 바뀐 파일을 지켜보는 시간
# 영상이지만 아직 못 쓰는 형식 (편집실 미리보기(WebView2)가 열지 못함) — 쓸 수 있는 형식은 core.VIDEO_EXTS
UNUSABLE_EXTS = {".mts", ".m2ts", ".m2t", ".ts", ".avi", ".wmv", ".asf", ".mpg", ".mpeg", ".vob", ".mod", ".tod",
                 ".3gp", ".3g2", ".flv", ".f4v", ".mxf", ".dv", ".rm", ".rmvb", ".divx", ".ogv", ".insv", ".360"}
SIG_FILE = "file_sig.json"  # 분석 폴더 안: 편집점을 찾을 때의 영상 크기·수정 시각
MTIME_SLACK_NS = 2_000_000_000  # 수정 시각은 2초까지 같은 것으로 (FAT·exFAT 외장 디스크는 2초 단위 · 폴더 옮기기)
# 바뀐 영상으로 편집점을 다시 찾을 때 지울 미리보기용 캐시 (editor.waveform·thumbs·poster·proxy_path·rev_audio 가 다시 만듦)
MEDIA_CACHE = ("waveform_*.json", "thumbs2.*", "poster.jpg", "proxy.mp4", "proxy.part.mp4")
CONVERTED = "바꾸기 전 원본"  # 보관함 안: MP4 로 바꾼 뒤 원본(.MTS 등)을 옮겨 두는 폴더 (지우지 않음)
_SEEN = {}        # 파일 경로 → {"key": (크기, 수정 시각 ns), "t": 그 값을 처음 본 때(monotonic), "ok": 쓰는 프로그램 없음 확인}
_SIGS = {}        # 분석 폴더 → (file_sig.json 수정 시각 ns, 크기)
_LOCK = threading.Lock()
_mono, _wall, _sleep = time.monotonic, time.time, time.sleep


def _writer_open(path):
    """Windows: 다른 프로그램(탐색기 복사·카메라 가져오기·녹화)이 이 파일을 쓰기로 열어 두었는지.
    읽기·지우기만 허용(FILE_SHARE_READ | FILE_SHARE_DELETE)으로 열어 보면, 쓰는 손잡이가 있을 때만 공유 위반(32)으로 실패한다
    (미리보기·ffmpeg·백신처럼 읽기만 하는 프로그램은 방해하지 않음 · 열어 보는 그 순간에도 다른 프로그램이 이름 바꾸기·지우기를
    할 수 있게 FILE_SHARE_DELETE). 다른 운영체제는 False (크기·시각으로만 봄)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 0x5, None, 3, 0x80, None)  # GENERIC_READ · FILE_SHARE_READ|DELETE · OPEN_EXISTING
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
    # 파일 이름 끝(…mp4)은 받침을 알 수 없어 '파일은'으로 (조사 '은(는)'을 기계적으로 붙이지 않게)
    return (f"'{name}' 파일은 아직 복사 중이거나 다른 프로그램이 쓰고 있어요. 복사가 끝나면(보관함의 '복사 중' 표시가 사라지면) "
            "다시 눌러 주세요")


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
    """편집점을 찾기 시작할 때의 파일 크기·수정 시각 (기록용). 탐색기는 복사를 시작할 때 크기를 다 잡아 두고, 끝날 때 원래
    파일의 수정 시각을 붙이므로 — 복사 중에 찾았으면 크기가 같아도 수정 시각이 달라진다."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def remember(outdir, s):
    """편집점 찾기를 마친 분석 폴더에 그때의 파일 크기를 남김 (실패해도 그만)."""
    if not s:
        return
    import updater
    try:
        updater.write_atomic(Path(outdir, SIG_FILE), json.dumps(s))  # 쓰다 꺼져도 반쪽 기록이 '바뀜'으로 보이지 않게
    except OSError:
        pass


def _stored(outdir):
    """분석 폴더에 남긴 (크기, 수정 시각 ns 또는 None) · 기록이 없거나 깨졌으면 None (읽은 값은 파일 시각으로 캐시)."""
    p = Path(outdir, SIG_FILE)
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return None
    k = str(p)
    with _LOCK:
        hit = _SIGS.get(k)
    if hit is None or hit[0] != mt:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            size, mns = d.get("size"), d.get("mtime_ns")
        except (OSError, ValueError, AttributeError):
            size = mns = None
        hit = (mt, (size, mns if isinstance(mns, int) else None) if isinstance(size, int) else None)
        with _LOCK:
            _SIGS[k] = hit
    return hit[1]


def changed(outdir, st):
    """편집점을 찾은 뒤 영상 파일이 바뀌었는지 — 크기가 다르거나 수정 시각이 2초 넘게 다르면.
    st: 지금 파일의 os.stat 결과 (예전처럼 크기 숫자만 주면 크기만 비교) · 기록이 없는 예전 분석은 모름 → False ·
    크기만 남긴 예전 기록(수정 시각 없음)은 크기만 비교."""
    rec = _stored(outdir)
    if rec is None:
        return False
    size, mns = (st, None) if isinstance(st, int) else (st.st_size, st.st_mtime_ns)
    if rec[0] != size:
        return True
    return rec[1] is not None and mns is not None and abs(rec[1] - mns) > MTIME_SLACK_NS


def clear_media_cache(outdir):
    """반쪽 파일로 만든 미리보기용 캐시(파형·썸네일 띠·포스터·미리보기 파일·거꾸로 소리)를 지움 → 편집실이 다시 만듦.
    지운 개수 (Windows 에서 편집실이 열어 둔 파일은 못 지울 수 있음 → 그 파일은 그대로 두고 센 개수만)."""
    d, n = Path(outdir), 0
    for pat in MEDIA_CACHE:
        for f in d.glob(pat):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    shutil.rmtree(d / "rev", ignore_errors=True)
    return n


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
                if changed(adir(r["name"]), st):
                    r["changed"] = True
            except Exception:  # noqa: BLE001 — 곁가지: 목록은 그대로
                pass
    forget(paths)
    return rows


# ---------- 못 쓰는 형식(.MTS 등) → MP4 ----------

def _probe_codecs(ffmpeg, src):
    """ffmpeg -i 출력에서 (영상 코덱, 소리 코덱, 길이 초) — 모르면 None."""
    import core
    import subprocess
    r = core.run([ffmpeg, "-hide_banner", "-i", str(src)], timeout=120, stdin=subprocess.DEVNULL)  # 검은 창 없음 · Job Object
    err = r.stderr or ""
    v = re.search(r"Stream #\S+.*?: Video: (\w+)", err)
    a = re.search(r"Stream #\S+.*?: Audio: (\w+)", err)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    return (v[1] if v else None), (a[1] if a else None), dur


def convert_target(folder, name):
    """바꿀 MP4 이름 (같은 이름의 MP4 가 이미 있으면 ' (MP4)'·' (MP4 2)'…)."""
    folder, stem = Path(folder), Path(name).stem
    out = folder / f"{stem}.mp4"
    k = 1
    while out.exists():
        out = folder / (f"{stem} (MP4).mp4" if k == 1 else f"{stem} (MP4 {k}).mp4")
        k += 1
    return out


def convert(folder, name, ffmpeg, log=print, progress=None, cancel=None):
    """보관함의 못 쓰는 형식 영상 하나를 MP4(H.264 + AAC)로 바꿔 넣음 → 새 파일 이름.
    영상이 H.264 면 화질 그대로 다시 담기(-c:v copy, 빠름), 아니면 다시 인코딩(CRF 18). 소리는 늘 AAC
    (캠코더 AVCHD 의 AC-3 소리는 편집실 미리보기(WebView2)가 못 틂). 끝나면 원본을 보관함 안 CONVERTED 폴더로 옮김 (지우지 않음).
    progress(pct, detail) · cancel(): True 면 멈춤 (만들던 파일은 지움)."""
    folder = Path(folder)
    src = folder / Path(name).name
    if src.suffix.lower() not in UNUSABLE_EXTS or not src.is_file():
        raise FileNotFoundError(f"바꿀 영상을 찾지 못했어요 · {name}")
    vcodec, acodec, dur = _probe_codecs(ffmpeg, src)
    if not vcodec:
        raise RuntimeError(f"'{src.name}'에서 영상을 찾지 못했어요 (파일이 깨졌거나 영상이 아닐 수 있어요)")
    out = convert_target(folder, src.name)
    keep = folder / CONVERTED
    keep.mkdir(exist_ok=True)
    tmp = keep / (out.stem + ".part.mp4")  # 만드는 동안은 보관함 목록에 안 보이게 (하위 폴더는 목록에서 빠짐)
    copy = vcodec == "h264"
    log(f"MP4로 바꾸는 중 · {src.name} → {out.name}" + (" (화질 그대로)" if copy else " (다시 인코딩 · 시간이 좀 걸려요)"))
    vargs = ["-c:v", "copy"] if copy else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    aargs = ["-c:a", "aac", "-b:a", "192k", "-ac", "2"] if acodec else ["-an"]
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1", "-i", str(src),
           "-map", "0:v:0", *(["-map", "0:a:0"] if acodec else []), *vargs, *aargs, "-movflags", "+faststart", str(tmp)]
    import core
    import subprocess
    import updater
    err, stopped = [], False
    with core.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                    stdin=subprocess.DEVNULL) as p:  # 검은 창 없음 · 앱이 꺼지면 같이 꺼짐 (Job Object)
        t = threading.Thread(target=lambda: err.extend(p.stderr), daemon=True)
        t.start()
        for line in p.stdout:
            if cancel and cancel():
                p.kill()
                stopped = True
                break
            m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
            if m and dur and progress:
                sec = int(m[1]) / 1e6
                progress(min(99, int(sec * 100 / dur)), f"{int(sec // 60)}:{int(sec % 60):02d} / {int(dur // 60)}:{int(dur % 60):02d}")
        p.wait()
        t.join(5)
    if stopped or p.returncode or not tmp.exists() or tmp.stat().st_size == 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        if stopped:
            raise RuntimeError("MP4로 바꾸기를 멈췄어요")
        raise RuntimeError(f"'{src.name}'을 MP4로 바꾸지 못했어요 · {' '.join(''.join(err).split())[-300:]}")
    try:  # 막 만든 큰 영상은 백신이 오래 검사함 → 잠겨 있으면 기다렸다 다시 (내보내기·묶기와 같게)
        updater.replace_retry(tmp, out, updater.SETTLE_SECS)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        updater.replace_retry(src, convert_target_keep(keep, src.name))  # 같은 보관함 안 하위 폴더로 옮김 (잠겼으면 잠깐 기다림)
    except OSError as e:  # Windows: 다른 프로그램이 원본을 잡고 있음 → 원본은 그 자리에 그대로 (MP4 는 이미 넣었음)
        log(f"  원본은 옮기지 못해 그 자리에 두었어요 · {e}")
    log(f"  MP4로 바꿨어요 · {out.name} (원본은 보관함 폴더 안 '{CONVERTED}' 폴더에 그대로 있어요)")
    return out.name


def convert_target_keep(folder, name):
    """원본을 옮길 자리 (같은 이름이 있으면 ' (2)'…)."""
    folder, p = Path(folder), Path(name)
    out, k = folder / p.name, 2
    while out.exists():
        out = folder / f"{p.stem} ({k}){p.suffix}"
        k += 1
    return out


# ---------- 폴더째 넣은 촬영본 (휴대폰·SD카드 날짜 폴더) ----------
# 보관함 목록(core.local_videos)·분석 폴더·이름 바꾸기·편집 프로젝트는 모두 '보관함 바로 안의 파일 이름'으로 영상을 찾는다.
# 그래서 하위 폴더 영상을 목록에 바로 섞지 않고(같은 이름 IMG_0001.MOV 가 폴더마다 있을 수 있음), 폴더별로 'N개 영상이 폴더 안에
# 있어요 · [밖으로 꺼내기]' 줄만 보여 주고, 누르면 보관함 바로 안으로 옮긴다(같은 드라이브 안 이름 바꾸기 · 복사 없음 · D-460).
NEST_DEPTH = 6     # 하위 폴더를 이만큼 깊이까지 (소니 캠코더 PRIVATE/AVCHD/BDMV/STREAM/00001.MTS 까지)
NEST_MAX = 5000    # 한 번에 살펴볼 항목 수 (사진 수천 장이 든 DCIM 폴더째 넣어도 목록이 느려지지 않게)
NEST_TTL = 3.0     # 살펴본 결과를 이만큼(초) 기억 (보관함 목록은 1초마다 부름)
_NEST = {}         # 보관함 폴더 → (살펴본 때 monotonic, 결과)


def _skip_dir(name):
    """하위 폴더 중 살펴보지 않을 것: 앱이 쓰는 '바꾸기 전 원본' · 숨김(.으로 시작) · Windows 시스템 폴더($RECYCLE.BIN 등)."""
    return name.casefold() == CONVERTED.casefold() or name.startswith((".", "$")) or name == "System Volume Information"


def _nest_file(name):
    import core
    return core.is_video_file(name) or Path(name).suffix.lower() in UNUSABLE_EXTS


def _nest_walk(top, limit):
    """하위 폴더 top 아래 영상 파일 경로 (이름 순 · 깊이 NEST_DEPTH · 바로 가기(심볼릭 링크·정션)는 따라가지 않음) · 남은 살펴볼 수."""
    out, stack = [], [(Path(top), 1)]
    while stack and limit > 0:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                items = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        subs = []
        for e in items:
            limit -= 1
            if limit <= 0:
                break
            try:
                if e.is_symlink() or (getattr(e, "is_junction", None) and e.is_junction()):
                    continue
                if e.is_dir(follow_symlinks=False):
                    if depth < NEST_DEPTH and not _skip_dir(e.name):
                        subs.append((Path(e.path), depth + 1))
                elif _nest_file(e.name) and e.is_file(follow_symlinks=False):
                    out.append(Path(e.path))
            except OSError:
                continue
        stack.extend(reversed(subs))
    return out, limit


def nested(folder, fresh=False):
    """보관함 폴더 바로 안 하위 폴더별로 그 안(더 깊은 폴더 포함)에 든 영상 [{folder, count, size_mb, copying}] (이름 순).
    copying: 아직 들어오는 중인 것 수 (방금 바뀌었거나 Windows 에서 쓰는 프로그램이 있음) · 영상이 없는 폴더는 뺌."""
    folder = Path(folder)
    k = str(folder)
    hit = _NEST.get(k)
    if hit and not fresh and _mono() - hit[0] < NEST_TTL:
        return [dict(r) for r in hit[1]]
    out, limit = [], NEST_MAX
    try:
        with os.scandir(folder) as it:
            tops = [e for e in it if not _skip_dir(e.name) and not e.is_symlink() and e.is_dir(follow_symlinks=False)]
    except OSError:
        tops = []
    for e in sorted(tops, key=lambda e: e.name):
        if limit <= 0:
            break
        files, limit = _nest_walk(e.path, limit)
        size = busy_n = n = 0
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            n += 1
            size += st.st_size
            if 0 <= _wall() - st.st_mtime < STABLE or _writer_open(p):
                busy_n += 1
        if n:
            out.append({"folder": e.name, "count": n, "size_mb": round(size / 1e6, 1), "copying": busy_n})
    _NEST[k] = (_mono(), out)
    return [dict(r) for r in out]


def _taken(folder, name, used):
    """보관함 바로 안에 이 이름을 쓸 수 없는지: 같은 이름(대소문자 무시 · Windows) · 같은 분석 폴더 이름의 영상
    (IMG_0001.MOV ↔ IMG_0001.mp4) · 지운 영상의 분석 폴더·편집 프로젝트가 남은 이름 (남의 받아쓰기·편집본을 이어 받지 않게)."""
    import core
    key, stem = name.casefold(), core._stem_key(name).casefold()
    if key in used["names"] or stem in used["stems"]:
        return True
    return (core.ANALYSIS / core._stem_key(name)).exists() or (core.WORK / "projects" / f"{core._stem_key(name)}.json").exists()


def nest_target(folder, path, used):
    """밖으로 꺼낼 때의 새 이름: 원래 이름 → 겹치면 '이름 (폴더 이름).MOV' → '이름 (폴더 이름 2).MOV'…"""
    p = Path(path)
    if not _taken(folder, p.name, used):
        return p.name
    tag = (p.parent.name or "폴더").strip(" .")[:40].rstrip(" .") or "폴더"
    k = 1
    while True:
        cand = f"{p.stem} ({tag}){p.suffix}" if k == 1 else f"{p.stem} ({tag} {k}){p.suffix}"
        if not _taken(folder, cand, used):
            return cand
        k += 1


def unnest(folder, sub, log=print):
    """하위 폴더 sub 안(더 깊은 폴더 포함)의 영상을 보관함 바로 안으로 옮김 → {moved: [{name, from}], kept: [{name, why}]}.
    같은 드라이브 안 옮기기라 복사하지 않음 · 겹치는 이름은 폴더 이름을 붙임 · 아직 복사 중이거나 다른 프로그램이 잡고 있는 파일은
    그대로 둠 · 다 비운 폴더만 지움(사진 등 다른 파일이 남은 폴더는 그대로)."""
    import core
    import updater
    folder = Path(folder)
    if not sub or sub != Path(sub).name or _skip_dir(sub) or not (folder / sub).is_dir():
        raise FileNotFoundError("그 폴더를 보관함에서 찾지 못했어요. 목록을 새로 고친 뒤 다시 눌러 주세요")
    used = {"names": set(), "stems": set()}
    try:
        with os.scandir(folder) as it:
            names = [e.name for e in it]
        for n in names:
            used["names"].add(n.casefold())
            if core.is_video_file(n):
                used["stems"].add(core._stem_key(n).casefold())
    except OSError:
        pass
    files, _ = _nest_walk(folder / sub, NEST_MAX)
    moved, kept, dirs = [], [], set()
    for p in files:
        rel = p.relative_to(folder).as_posix()
        if busy(p):
            kept.append({"name": rel, "why": "copying"})
            continue
        new = nest_target(folder, p, used)
        dst = folder / new
        if dst.exists():  # 그사이 같은 이름이 생김 → 덮어쓰지 않음
            kept.append({"name": rel, "why": "exists"})
            continue
        try:
            updater.replace_retry(p, dst, 3.0)  # Windows: 백신·미리보기가 잠깐 잡고 있으면 조금 기다렸다 다시
        except OSError as e:
            kept.append({"name": rel, "why": "locked"})
            log(f"  옮기지 못했어요 · {rel} · {e}")
            continue
        used["names"].add(new.casefold())
        used["stems"].add(core._stem_key(new).casefold())
        moved.append({"name": new, "from": rel})
        dirs.add(p.parent)
        log(f"  보관함으로 꺼냄 · {rel}" + (f" → {new}" if new != p.name else ""))
    top = (folder / sub).resolve()
    for d in sorted(dirs | {folder / sub}, key=lambda x: len(x.parts), reverse=True):  # 비었으면 위로 올라가며 지움
        cur = Path(d)
        while True:
            try:
                cur.rmdir()  # 빈 폴더만 지워짐 (파일이 남았으면 OSError)
            except OSError:
                break
            if cur.resolve() == top:
                break
            cur = cur.parent
    _NEST.pop(str(folder), None)
    return {"moved": moved, "kept": kept}


# ---------- 화질 (해상도·비트레이트) — 카카오톡으로 받아 압축된 촬영본 알리기 ----------
# 메신저로 보낸 영상은 720p·낮은 비트레이트 사본이 온다. 그대로 쓰면 1080p 로 키워 내보내 업로드 화질이 되돌릴 수 없게 상함 →
# 보관함 줄에 해상도·비트레이트를 보이고, 'KakaoTalk_' 이름이거나 720p 이하·낮은 비트레이트면 원본으로 다시 옮기라고 알린다 (D-462).
LOW_SIDE = 720     # 짧은 변이 이 이하이고
LOW_KBPS = 4000    # 전체 비트레이트가 이보다 낮으면 '압축된 사본 같음' (폰 원본 720p 도 6Mbps 쯤 · 카톡 사본은 1~3Mbps)
TINY_SIDE = 480    # 짧은 변이 이보다 작으면 비트레이트와 상관없이 낮은 화질
_KAKAO = re.compile(r"^kakaotalk_", re.I)
_INFO = {}         # (경로, 크기, 수정 시각 ns) → {w, h, kbps, dur, created} 또는 {} (못 읽음)
_INFO_Q = []       # 뒤에서 하나씩 읽을 파일 (경로, 열쇠)
_INFO_RUN = {"on": False}


def quality_flag(name, info):
    """'kakao'(카카오톡 이름) · 'low'(720p 이하 + 낮은 비트레이트 · 480p 미만) · None. info 를 아직 모르면 이름만 봄."""
    if _KAKAO.match(Path(name).name):
        return "kakao"
    w, h, kbps = (info or {}).get("w") or 0, (info or {}).get("h") or 0, (info or {}).get("kbps") or 0
    if not (w and h):
        return None
    side = min(w, h)
    if side < TINY_SIDE or (side <= LOW_SIDE and 0 < kbps < LOW_KBPS):
        return "low"
    return None


def _probe_info(path):
    """영상 하나의 화면 크기(보이는 방향)·전체 비트레이트(kbps)·길이·촬영 시각 (bundle.probe 와 같은 읽기 · 못 읽으면 {})."""
    import bundle
    try:
        c = bundle.probe(path)
    except Exception:  # noqa: BLE001 — 안내용 · 깨진 파일은 편집점 찾기가 따로 알림
        return {}
    dur = c.get("duration") or 0
    kbps = int(round(c["bytes"] * 8 / dur / 1000)) if dur > 0 else 0
    return {"w": c.get("dw") or 0, "h": c.get("dh") or 0, "kbps": kbps, "dur": round(dur, 1), "created": c.get("created")}


def _info_worker():
    while True:
        with _LOCK:
            if not _INFO_Q:
                _INFO_RUN["on"] = False
                return
            path, key = _INFO_Q.pop(0)
        if key in _INFO:
            continue
        got = _probe_info(path)
        with _LOCK:
            if len(_INFO) > 3000:
                _INFO.clear()
            _INFO[key] = got


def media(rows, folder):
    """보관함 목록에 'media' {w, h, kbps, dur, created}(아는 것만) · 'lowq'(quality_flag) 를 붙임.
    처음 보는 파일은 뒤에서 하나씩 ffmpeg 로 읽고(목록은 기다리지 않음 · 다음 목록부터 보임) · 복사 중인 파일은 다 들어온 뒤 읽음."""
    folder, want = Path(folder), []
    for r in rows:
        p = folder / r["name"]
        flag = quality_flag(r["name"], None)
        try:
            st = p.stat()
        except OSError:
            st = None
        info = None
        if st is not None and not r.get("copying"):
            key = (str(p), st.st_size, st.st_mtime_ns)
            with _LOCK:
                info = _INFO.get(key)
            if info is None:
                want.append((p, key))
        if info:
            r["media"] = dict(info)
            flag = quality_flag(r["name"], info)
        if flag:
            r["lowq"] = flag
    if want:
        with _LOCK:
            queued = {k for _, k in _INFO_Q}
            _INFO_Q.extend(x for x in want if x[1] not in queued)
            start = not _INFO_RUN["on"]
            _INFO_RUN["on"] = True
        if start:
            threading.Thread(target=_info_worker, daemon=True, name="media-info").start()
    return rows
