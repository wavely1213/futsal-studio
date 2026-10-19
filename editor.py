"""편집실: 영상 정보·파형·썸네일·프로젝트 저장(멀티 트랙)·자동 추천·내보내기(영상/프리미어 XML/SRT)."""
import bisect
import hashlib
import html as _html
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
import wave
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import captions
import core
import epfont
import exportplan
import hwdec
import idle
import studiolog
import tactic
import takes
import updater

PROJECTS = core.WORK / "projects"
ASSETS = core.WORK / "edit_media"  # 편집실에서 가져온 음악·이미지·영상
for _d in (PROJECTS, ASSETS):
    _d.mkdir(parents=True, exist_ok=True)
FONTS = core.APP_DIR / "fonts"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
MEDIA_EXTS = IMAGE_EXTS | AUDIO_EXTS | core.VIDEO_EXTS

DEFAULT_STYLE = {
    "weight": "Bold",          # Bold | Black
    "size": 64,                # 기준 해상도(가로 1080 / 세로 1920 기준) 픽셀
    "fill": "#FFFFFF",
    "stroke": "#000000",
    "strokeW": 6,
    "bgOn": False,
    "bg": "#000000",
    "bgOpacity": 0.55,
    "y": 0.82,                 # 화면 세로 위치 (0 위 ~ 1 아래)
    "effect": "pop",           # none | fade | pop | slide | karaoke
    "highlight": "#FFD400",
}


def _nid():
    return uuid.uuid4().hex[:8]


# ---------- 미디어 ----------

def safe_name(n):
    """파일 이름만 허용 (경로·드라이브·네트워크 경로(\\\\server)·'..' 는 거절)."""
    from pathlib import PurePosixPath, PureWindowsPath
    n = str(n or "")
    if (not n or n in (".", "..") or "\x00" in n or ":" in n
            or PureWindowsPath(n).name != n or PurePosixPath(n).name != n):
        raise ValueError("잘못된 파일 이름이에요")
    return n


def video_path(name):
    """보관함 영상 경로 (이름 검사 포함)."""
    p = core.VIDEOS / safe_name(name)
    if not p.is_file():
        raise FileNotFoundError(f"영상을 찾지 못했어요 · {name}")
    return p


def media_path(f, src="videos"):
    return (core.VIDEOS if src == "videos" else ASSETS) / safe_name(Path(str(f)).name)


def _cache_dir(f, src="videos"):
    """미디어별 캐시 폴더 (파형·썸네일·미리보기 파일). 가져온 파일은 확장자까지 포함한 전체 이름으로 구분
    (bgm.mp3 / bgm.wav, Logo.png / logo.jpg 가 섞이지 않게 · Windows 폴더 이름은 대소문자를 안 가림)."""
    if src == "videos":
        return core.adir(f)
    n = Path(str(f)).name
    tag = hashlib.sha1(n.lower().encode("utf-8")).hexdigest()[:10]
    base = Path(n).stem.strip(" .")[:40].rstrip(" .") or "media"
    return core.ANALYSIS / "_media" / f"{base}_{tag}"


# 소리 줄기가 영상보다 늦게 시작하는 파일(소리 시작 0.5초 등): 앞을 조용히 채워 파일 시작 기준 제자리로 — 미리보기(브라우저)는 제자리로 틀어 주는데
# 파형·받아쓰기·내보내기는 첫 소리를 0초로 읽어 그만큼 일렀음 (D-324) · 보통 파일(같이 시작)은 같은 소리 그대로 (core·plan·msg 도 같은 필터)
AUDIO_T0 = "aresample=async=1:first_pts=0"


def probe(path):
    """ffmpeg 만으로 길이·크기·소리 유무 확인 (ffprobe 는 없을 수 있음)."""
    path = Path(path)
    err = core.run([core.ffmpeg(), "-hide_banner", "-i", str(path)]).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    st = re.search(r"Duration: [^\n]*?start: (-?[\d.]+)", err)  # 파일 시작 시각 (보통 0 · D-324)
    vids = [ln for ln in err.splitlines() if "Video:" in ln and "attached pic" not in ln]
    v = re.search(r", (\d{2,5})x(\d{2,5})", vids[0]) if vids else None
    f = re.search(r"([\d.]+) fps", vids[0]) if vids else None
    w, h = (int(v[1]), int(v[2])) if v else (1920, 1080)
    rot = re.search(r"rotation of (-?[\d.]+)", err)
    if rot and abs(abs(float(rot[1])) - 90) < 1:  # 휴대폰 세로 영상
        w, h = h, w
    ext = path.suffix.lower()
    kind = "image" if ext in IMAGE_EXTS else ("video" if vids else "audio")
    hdr = None  # 아이폰 HDR(HLG)·PQ 영상 → 내보낼 때 일반(SDR) 색으로 바꿔야 함
    if vids and kind == "video":
        hdr = "pq" if "smpte2084" in vids[0] else "hlg" if "arib-std-b67" in vids[0] else None
    if kind == "image":  # 휴대폰 사진: 사진 속 회전 정보(EXIF)대로 세운 크기 (ffmpeg·브라우저 모두 세워서 보여줌)
        try:
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
                if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):
                    w, h = h, w
        except Exception:
            pass
    return {"duration": round(dur, 3), "width": w, "height": h, "fps": float(f[1]) if f else 30.0,
            "audio": "Audio:" in err, "kind": kind, "hdr": hdr, "start": float(st[1]) if st else 0.0}


_T0 = {}


def media_t0(f, src="videos"):
    """원본 파일의 시작 시각(초 · ffmpeg 'start:') — 0 이 아닌 드문 파일은 브라우저 미리보기가 그만큼 뒤 시각(파일 안 PTS)을 씀.
    내보내기·파형·받아쓰기(ffmpeg -ss)는 파일 시작 기준이라 미리보기 재생기 시각에만 더함 (editor.html elOff · D-324). 못 읽으면 0 · 파일 크기·시각으로 기억."""
    try:
        p = media_path(f, src)
        s = p.stat()
    except (OSError, ValueError):
        return 0.0
    k = (str(p), s.st_size, s.st_mtime_ns)
    if k not in _T0:
        try:
            _T0[k] = float(probe(p).get("start") or 0.0)
        except Exception:  # noqa: BLE001 — 못 읽으면 예전처럼 0
            _T0[k] = 0.0
    return _T0[k]


def media_info(name):
    i = probe(video_path(name))
    return {k: i[k] for k in ("duration", "width", "height", "fps", "hdr")}


def _has_audio(name):
    """원본 영상에 소리 줄기가 있는지 (화면 녹화·마이크를 끈 휴대폰 영상은 없음 · 못 읽으면 있다고 봄)."""
    try:
        return bool(probe(video_path(name))["audio"])
    except (OSError, ValueError, KeyError, RuntimeError):
        return True


def media_entry(f, src="videos", mid=None):
    p = media_path(f, src)
    if not p.exists():
        raise FileNotFoundError(f"파일을 찾지 못했어요 · {f}")
    i = probe(p)
    e = {"id": mid or _nid(), "kind": i["kind"], "src": src, "file": p.name, "dur": i["duration"] if i["kind"] != "image" else 0,
         "w": i["width"], "h": i["height"], "fps": i["fps"], "audio": i["audio"] and i["kind"] != "image",
         "proxy": proxy_path(p.name, src).exists()}
    if i.get("hdr"):
        e["hdr"] = i["hdr"]
    if i["kind"] == "video" and (i.get("start") or 0.0) > 0.005:  # 시작 시각이 0 보다 큰 파일만 (미리보기 재생기에 더함 · D-324)
        e["t0"] = round(i["start"], 3)  # 0 보다 작은 시작(.webm·Opus 의 -0.007 등)은 브라우저가 0 으로 당겨 읽음 → 더하면 한 프레임 일찍 보임
    return e


def _wav_dur(p):
    """WAV 파일 길이(초) · 읽지 못하면 None."""
    try:
        with wave.open(str(p), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return None


def refresh_sfx_lengths(seqs_items, media):
    """예전 판 편집본의 효과음 클립 (D-370 · D-379): 효과음 파일(edit_media/효과음_*.wav)을 원본에서 다시 만들어 예전 캐시보다 길어졌으면
    예전 파일 끝까지 쓰던 클립(자르지 않은 클립)을 새 길이까지 늘림 (같은 트랙 다음 클립 앞까지 · 1배속만) · 미디어 길이도 새 파일 길이로.
    예전: 다시 만든 레벨업2(0.324초)를 예전 길이(0.175초)까지만 냄. seqs_items: 편집본마다 클립 목록 [[…], …] (미디어는 프로젝트에 하나라
    모든 편집본을 한 번에) · media: 미디어 기록 목록(제자리에서 고침) → 늘린 클립 수."""
    import sfxlib   # (늦게 · sfxlib 이 core 만 씀)
    n = 0
    for m in media:
        if not isinstance(m, dict) or m.get("src") != "assets" or not str(m.get("file") or "").startswith("효과음_"):
            continue
        nm = str(m["file"])[4:-4] if str(m["file"]).endswith(".wav") else None
        if nm in sfxlib.CATALOG:
            try:   # 예전 판 캐시(잘린 원본 · 원본 지문 없음)면 여기서 한 번 다시 만듦 — 예전 편집본을 열거나 내보내기만 해도 제 소리
                sfxlib.ensure_sfx(nm, ASSETS)
            except (OSError, RuntimeError, ValueError):
                pass
        new = _wav_dur(media_path(m["file"], "assets"))
        old = float(m.get("dur") or 0.0)
        if new is None or new <= old + 0.005:
            continue
        for items in seqs_items:
            for it in items:
                if it.get("media") != m["id"] or abs(float(it.get("speed") or 1.0) - 1.0) > 1e-3 or abs(float(it["out"]) - old) > 0.01:
                    continue
                st = float(it["start"])
                nxt = min([float(o["start"]) for o in items if o is not it and o.get("track") == it.get("track") and float(o["start"]) > st + 1e-3] or [1e9])
                end = min(new, float(it["in"]) + (nxt - st))
                if end > float(it["out"]) + 1e-3:
                    it["out"] = round(end, 4)
                    n += 1
        m["dur"] = round(new, 3)
    return n


def tonemap_chain(hdr, max_w=None):
    """HDR(HLG/PQ, 10비트) → 일반 BT.709 화면 (아이폰 영상이 내보내면 허옇게 뜨지 않게)."""
    tin = "smpte2084" if hdr == "pq" else "arib-std-b67"
    pre = [f"scale=w='min(iw,{int(max_w)})':h=-2"] if max_w else []  # 4K 를 미리 줄여 빠르게
    return pre + [f"zscale=tin={tin}:pin=bt2020:min=bt2020nc:t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709",
                  "tonemap=tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv", "format=yuv420p"]


_HDR_CACHE = {}


def media_hdr(md):
    """미디어의 HDR 여부 (예전 프로젝트엔 표시가 없으니 한 번 확인해 둠)."""
    if md.get("kind") != "video":
        return None
    if "hdr" in md:
        return md["hdr"]
    p = media_path(md["file"], md.get("src", "videos"))
    k = str(p)
    if k not in _HDR_CACHE:
        try:
            _HDR_CACHE[k] = probe(p).get("hdr") if p.exists() else None
        except Exception:
            _HDR_CACHE[k] = None
    return _HDR_CACHE[k]


# ---------- 미리보기용 가벼운 파일 (프록시) ----------

def proxy_path(f, src="videos"):
    return _cache_dir(f, src) / "proxy.mp4"


def _encode_540p(p, tmp, label, item):
    """540p H.264 가벼운 파일 (편집실 미리보기 파일·휴대폰 '작은 미리보기'가 같이 씀) → ffmpeg 종료 코드. 멈추기(✕)로 끌 수 있음."""
    info = probe(p)
    dur = info["duration"] or 1
    # fps 를 먼저 (60fps 의 버릴 프레임은 색 바꾸기를 안 함 · 남는 프레임 그림은 예전과 같음)
    # (960 으로 더 줄이면 CPU 가 조금 더 줄지만 예전과 SSIM 0.9797 이라 예전 크기 1920 그대로)
    vf = (["fps=30"] + tonemap_chain(info["hdr"], 1920) + ["scale=-2:540"]) if info.get("hdr") else ["scale=-2:540", "fps=30"]
    cmd = [core.ffmpeg(), "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", "-i", str(p), "-vf", ",".join(vf),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "1600k", "-bufsize", "3200k",  # 복잡한 장면(잔디·흔들림)도 1.6Mb/s 안 (밖에서 6Mb/s 망 · D-367)
           "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-ac", "2", "-movflags", "+faststart", str(tmp)]
    proc = core.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    _PROCS.add(proc)
    try:
        for line in proc.stdout:
            if line.startswith(b"out_time_us="):
                try:
                    t = int(line[12:].strip() or 0) / 1e6
                    core.set_progress(label=label, item=item, pct=min(99, int(t * 100 / dur)), detail=f"{int(t)}초 / {int(dur)}초")
                except ValueError:
                    pass
        proc.wait()
    finally:
        _PROCS.discard(proc)
        proc.stdout.close()
    return proc.returncode


def make_proxy(f, src="videos", log=print):
    """4K·HEVC(아이폰) 영상도 편집실에서 부드럽게: 540p H.264, 짧은 키프레임 간격. 내보내기는 항상 원본으로."""
    p, out = media_path(f, src), proxy_path(f, src)
    out.parent.mkdir(parents=True, exist_ok=True)
    CANCEL.clear()
    tmp = out.with_suffix(".part.mp4")
    rc = _encode_540p(p, tmp, "미리보기 파일 만드는 중", f)
    if CANCEL.is_set():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("미리보기 파일 만들기를 멈췄어요")
    if rc or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("미리보기 파일을 만들지 못했어요")
    try:  # 막 만든 큰 파일: 백신이 검사하는 동안 잠깐 잠김
        updater.replace_retry(tmp, out, updater.SETTLE_SECS)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"미리보기 파일을 만들었지만 제자리에 두지 못했어요 (백신이 검사 중일 수 있어요). 잠시 뒤 다시 눌러 주세요 · {e}")
    log(f"  미리보기 파일 완료 · {Path(f).name}")
    return {"src": src, "file": Path(f).name}


# ---------- 밖에서 작업용 가벼운 소리·그림 (D-363 · D-366) ----------
# 영상은 미리보기 파일(proxy.mp4)로 보는데 배경음악·효과음은 원본 WAV(48kHz 16비트 스테레오 = 1.5Mb/s · 한 곡이 영상 미리보기의 1.5배)를
# 그대로 흘려보내, 느린 인터넷(6Mb/s 쯤)에서는 처음 30초에 망이 꽉 차 소리가 끊겼음 (I-360). 원격 화면이 lite=… 로 달라고 할 때만
# PC 가 한 번 만들어 미리보기 파일 옆에 둠 — PC 화면·내보내기는 늘 원본 그대로.
#   opus · aac: 소리 128k 를 MP4 에 (목차를 앞에 · 어디로 옮겨도 바로 찾아감 — Ogg 는 찾기가 느림) · Opus 를 못 푸는 브라우저는 AAC
#   webp: 400KB 넘는 그림 (정지 화면 PNG 3MB → 수백 KB) · 긴 변 2560 까지 · 투명도 그대로
LITE_KINDS = ("opus", "aac", "webp")
LITE_TYPES = {"opus": "audio/mp4", "aac": "audio/mp4", "webp": "image/webp"}
LITE_AUDIO = {"opus": ["-c:a", "libopus", "-b:a", "128k"], "aac": ["-c:a", "aac", "-b:a", "160k"]}
LITE_IMAGE_MIN = 400 << 10   # 이보다 작은 그림은 원본 그대로
LITE_IMAGE_SIDE = 2560
LITE_AUDIO_KEEP = 200_000    # 이미 압축된 소리(mp3·m4a·aac)가 이 비트레이트(bps) 이하면 원본 그대로
_LITE_SKIP = {}              # (파일, 크기, 바뀐 시각, 종류) → True: 원본이 더 가볍거나 못 만듦 (다시 안 해 봄)


def lite_path(f, src, kind):
    return _cache_dir(f, src) / (f"lite_{kind}." + ("webp" if kind == "webp" else "mp4"))


def lite_file(f, src, kind):
    """원격 미리보기용 가벼운 파일 경로 (없으면 만듦 · 같은 파일을 여러 요청이 동시에 달라고 해도 한 번만) · 원본 그대로 보내면 될 때는 None."""
    if kind not in LITE_KINDS:
        return None
    p = media_path(f, src)
    if not p.is_file():
        raise FileNotFoundError(f"파일을 찾지 못했어요 · {f}")
    ext = p.suffix.lower()
    if ext not in (IMAGE_EXTS if kind == "webp" else AUDIO_EXTS):  # 영상 파일은 늘 미리보기 파일(proxy) 길로 (원본을 흘려보내지 않음 · D-232)
        return None
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns, kind)
    if _LITE_SKIP.get(key):
        return None
    out = lite_path(f, src, kind)

    def fresh(o):
        try:
            return o.stat().st_mtime >= st.st_mtime and o.stat().st_size > 0
        except OSError:
            return False
    if fresh(out):
        return out
    if kind == "opus" and fresh(lite_path(f, src, "aac")):  # Opus 를 못 만드는 ffmpeg 였으면 AAC (Opus 를 푸는 브라우저는 AAC 도 풂)
        return lite_path(f, src, "aac")
    with _gen_lock(str(out)):
        if fresh(out):
            return out
        if _LITE_SKIP.get(key):
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f"lite.{threading.get_ident()}.part" + out.suffix)
        try:
            ok = _lite_image(p, tmp, st.st_size) if kind == "webp" else _lite_audio(p, tmp, kind, ext, st.st_size)
            if ok == "aac":  # Opus 를 못 만듦 → AAC 로 만든 것을 그 자리(lite_aac)에
                out = lite_path(f, src, "aac")
            if ok:
                updater.replace_retry(tmp, out)
                return out
        except Exception as e:  # 만들지 못하면 원본 그대로 (재생은 되게)
            studiolog.trace(e, "가벼운 미리보기 파일")
        finally:
            tmp.unlink(missing_ok=True)
        _LITE_SKIP[key] = True
        return None


def _lite_audio(p, tmp, kind, ext, size):
    if ext in (".mp3", ".m4a", ".aac"):  # 이미 가벼운 소리는 그대로 (다시 압축하면 음질만 나빠짐)
        dur = probe(p)["duration"]
        if dur > 0 and size * 8 / dur <= LITE_AUDIO_KEEP:
            return False
    for k in ([kind, "aac"] if kind == "opus" else [kind]):
        cmd = [core.ffmpeg(), "-y", "-v", "error", "-i", str(p), "-map", "0:a:0", "-vn", "-af", AUDIO_T0, *LITE_AUDIO[k],
               "-ac", "2", "-ar", "48000", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
        with _GEN_SEM:
            r = core.run(cmd, stdin=subprocess.DEVNULL)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            return True if k == kind else "aac"
        tmp.unlink(missing_ok=True)
    return False


def _lite_image(p, tmp, size):
    if size < LITE_IMAGE_MIN:
        return False
    from PIL import Image, ImageOps, features
    if not features.check("webp"):
        return False
    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im)  # 브라우저가 원본을 세워 보여주는 방향 그대로
        alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if alpha else "RGB")
        if max(im.size) > LITE_IMAGE_SIDE:
            im.thumbnail((LITE_IMAGE_SIDE, LITE_IMAGE_SIDE), Image.LANCZOS)
        im.save(tmp, "WEBP", quality=88, method=4)
    return tmp.stat().st_size < size * 0.8  # 크게 줄지 않으면 원본 그대로


def lite_warm(items, kinds):
    """원격 편집실을 열 때: 그 편집본의 소리·그림 미리보기를 뒤에서 미리 만들어 둠 (재생하려 할 때 기다리지 않게). items = [(src, file, kind)]."""
    def run():
        for src, f, k in items:
            want = kinds.get(k)
            if want:
                try:
                    lite_file(f, src, want)
                except Exception:  # noqa: BLE001  (없는 파일 등 — 열 때 따로 알려 줌)
                    pass
    threading.Thread(target=run, daemon=True, name="lite-warm").start()


# ---------- 원격 실행기 (remote.py) 가 쓰는 것 ----------

PREVIEW_KEEP = 10  # 휴대폰용 작은 미리보기는 최근 10개만 (오래된 것부터 지움)


def remote_previews():
    """휴대폰용 작은 미리보기 폴더 (완성본 폴더 out/ 에는 넣지 않음)."""
    return core.ANALYSIS / "_remote"


def out_preview_path(name):
    n = safe_name(name)
    tag = hashlib.sha1(n.encode("utf-8")).hexdigest()[:10]
    base = Path(n).stem.strip(" .")[:40].rstrip(" .") or "video"
    return remote_previews() / f"{base}_{tag}" / "preview.mp4"


def out_preview(name, log=print):
    """완성본(out/*.mp4) → 휴대폰에서 데이터 적게 볼 540p 미리보기 (analysis/_remote/…/preview.mp4)."""
    src = (core.OUT / safe_name(name)).resolve()
    if src.parent != core.OUT.resolve() or not src.is_file() or src.suffix.lower() != ".mp4":
        raise RuntimeError("완성본을 찾지 못했어요")
    out = out_preview_path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    CANCEL.clear()
    tmp = out.with_suffix(".part.mp4")
    rc = _encode_540p(src, tmp, "작은 미리보기 만드는 중", name)
    if CANCEL.is_set():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("작은 미리보기 만들기를 멈췄어요")
    if rc or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("작은 미리보기를 만들지 못했어요")
    try:  # 막 만든 큰 파일: 백신이 검사하는 동안 잠깐 잠김 (make_proxy 와 같게)
        updater.replace_retry(tmp, out, updater.SETTLE_SECS)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"작은 미리보기를 만들었지만 제자리에 두지 못했어요 (백신이 검사 중일 수 있어요). 잠시 뒤 다시 해 주세요 · {e}")
    try:
        dirs = sorted((d for d in remote_previews().iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[PREVIEW_KEEP:]:
            shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass
    log(f"  작은 미리보기 완료 · {name}")
    return {"file": name}


def export_saved(name, seq_id, preset="youtube", log=print):
    """저장된 편집본 하나 내보내기 (휴대폰에서) — 편집실 화면 exportProject() 와 같은 묶음 {편집본, 자막, 정보, 원본, 미디어}."""
    if preset not in ("youtube", "small"):
        raise RuntimeError("내보내기 설정을 다시 골라 주세요")
    proj = load_project(name)
    seq = next((q for q in proj.get("sequences") or [] if isinstance(q, dict) and q.get("id") == seq_id), None)
    if not seq:
        raise RuntimeError("편집본을 찾지 못했어요 · 편집실에서 지웠는지 확인해 주세요")
    merged = {**seq, "captions": proj.get("captions"), "speech": proj.get("speech"), "info": proj.get("info"), "source": proj.get("source"),
              "media": proj.get("media")}
    return export(name, merged, {"preset": preset, "fps": 30, "video": True, "srt": True, "xml": False, "hw": True, "range": None}, log)


def add_style_sequences(name, style_name, params, kinds=("long", "shorts"), log=print, client="remote"):
    """배운 스타일로 자동 가편집을 만들어 프로젝트 편집본에 바로 더함 (휴대폰에서 · 이름 규칙은 /api/edit/autoseq 와 같음).
    편집실이 그 사이 저장했으면 한 번 다시 읽어 더함 · 열려 있는 편집실은 다음 저장 때 기존 '다른 창에서 저장됨' 안내를 받는다."""
    core.set_progress(label="자동 가편집 만드는 중", item=name, pct=None, detail=f"{style_name} 스타일")
    for attempt in (1, 2):
        proj = load_project(name)
        seqs = auto_sequences(name, proj.get("info") or media_info(name), params, tuple(kinds))
        names = {q.get("name") for q in proj.get("sequences") or []}
        for q in seqs:
            q["name"] = _uniq_name(f"{style_name} 스타일 가편집" + ("" if q["format"] == "long" else " · " + q["name"]), names)
            names.add(q["name"])
        proj["sequences"] = list(proj.get("sequences") or []) + seqs
        try:
            save_project(name, proj, proj.get("rev"), client=client)
            break
        except Conflict:
            if attempt == 2:
                raise RuntimeError("편집실에서 저장하는 중이라 더하지 못했어요 · 잠시 뒤 다시 해 주세요") from None
    log(f"  가편집 {len(seqs)}개를 편집본에 더했어요 · " + ", ".join(q["name"] for q in seqs))
    return {"added": [q["name"] for q in seqs]}


def library():
    """가져올 수 있는 미디어: 보관함 영상 + 편집실로 가져온 파일."""
    vids = [{"src": "videos", "file": v["name"], "size_mb": v["size_mb"], "kind": "video"} for v in core.local_videos()]
    assets = [{"src": "assets", "file": p.name, "size_mb": round(p.stat().st_size / 1e6, 1),
               "kind": "image" if p.suffix.lower() in IMAGE_EXTS else ("audio" if p.suffix.lower() in AUDIO_EXTS else "video")}
              for p in sorted(ASSETS.iterdir()) if p.is_file() and p.suffix.lower() in MEDIA_EXTS]
    return {"videos": vids, "assets": assets}


def _upright_image(dest):
    """휴대폰 사진의 회전 정보(EXIF)를 실제 픽셀에 적용해 저장 → ffmpeg·미리보기·썸네일이 모두 같은 방향."""
    try:
        from PIL import Image, ImageOps
        with Image.open(dest) as im:
            if im.getexif().get(0x0112, 1) == 1:
                return
            up = ImageOps.exif_transpose(im)
            fmt = (im.format or "").upper()
        tmp = dest.with_name(dest.stem + ".rot" + dest.suffix)
        if fmt in ("JPEG", "MPO") or dest.suffix.lower() in (".jpg", ".jpeg"):
            up.convert("RGB").save(tmp, "JPEG", quality=95)
        else:
            up.save(tmp, fmt or None)
        updater.replace_retry(tmp, dest)
    except Exception:
        pass


def unique_name(folder, name):
    """folder 안에 아직 없는 이름: 같은 이름이 있으면 '이름_1.확장자'·'이름_2.확장자' … (덮어쓰지 않게 · 가져오기·밖에서 올리기가 같이 씀)."""
    folder = Path(folder)
    dest, k = folder / name, 1
    while dest.exists():
        dest = folder / f"{Path(name).stem}_{k}{Path(name).suffix}"
        k += 1
    return dest


def save_upload(name, stream, length):
    """가져오기: 음악·이미지·영상 파일을 그대로 받아 저장. 끝까지 받은 뒤에만 제 이름으로 (끊기거나 디스크가 차면
    반쪽 파일이 미디어 목록에 남아 내보내기가 깨졌음 · Windows 는 끊김이 ConnectionAbortedError)."""
    name = unicodedata.normalize("NFC", core.clean_text(Path(name).name))  # 맥·아이폰의 풀어 쓴 한글(NFD) → 모아 쓴 글자 (rstudio.upload_name 과 같음)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "media"
    if Path(name).suffix.lower() not in MEDIA_EXTS:
        raise ValueError("영상·음악·이미지 파일만 가져올 수 있어요")
    dest = unique_name(ASSETS, name)
    part = ASSETS / f".{dest.name}.{os.getpid()}_{threading.get_ident()}.part"  # 미디어 목록(MEDIA_EXTS)에 안 잡히는 이름
    left = length
    try:
        with open(part, "wb") as f:
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left > 0:
            raise ValueError("파일을 끝까지 받지 못했어요. 다시 가져와 주세요")
        updater.replace_retry(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if dest.suffix.lower() in IMAGE_EXTS:
        _upright_image(dest)
    return media_entry(dest.name, "assets")


_GEN_LOCK = threading.Lock()
_GEN_LOCKS = {}
_GEN_SEM = threading.Semaphore(2)  # 썸네일·파형 만들기는 한 번에 2개까지 (내보내기·미리보기를 방해하지 않게)


def _gen_lock(key):
    with _GEN_LOCK:
        return _GEN_LOCKS.setdefault(key, threading.Lock())


def _write_atomic(path, text):
    updater.write_atomic(path, text)


def waveform(name, per_sec=50, src="videos"):
    """소리 크기 (0~1) 를 1초에 per_sec 개씩."""
    cache = _cache_dir(name, src) / f"waveform_v2_{per_sec}.json"  # v2: 늦게 시작하는 소리 줄기도 제자리 (AUDIO_T0 · D-324 · 예전 캐시는 다시 만듦)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    with _gen_lock(str(cache)):
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        import numpy as np
        sr = 8000
        with _GEN_SEM:
            with core.popen([core.ffmpeg(), "-v", "error", "-i", str(media_path(name, src)), "-vn", "-af", AUDIO_T0, "-ac", "1", "-ar", str(sr),
                             "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL) as proc:
                out, _ = proc.communicate()
            p = subprocess.CompletedProcess(proc.args, proc.returncode, out, b"")
        a = np.abs(np.frombuffer(p.stdout, np.int16).astype(np.float32)) / 32768.0
        hop = sr // per_sec
        n = len(a) // hop
        peaks = a[: n * hop].reshape(n, hop).max(axis=1) if n else np.zeros(0)
        top = float(np.percentile(peaks, 99)) if n else 1.0
        data = {"per_sec": per_sec, "peaks": [round(min(1.0, float(x) / (top or 1)), 3) for x in peaks], "top": round(top, 4)}
        cache.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(cache, json.dumps(data))
        return data


# 한쪽만 녹음된 마이크 (D-404): 무선 마이크 수신기를 나눔(split) 모드로 꽂거나 카메라 마이크 입력 1번만 쓰면 왼쪽만 녹음됨
CHAN_FILL_DB = 20.0    # 대사 클립 원본의 한쪽 채널이 다른 쪽보다 이만큼(dB) 넘게 작으면 큰 쪽 소리를 양쪽에 (카메라 내장 마이크 좌우 차이는 몇 dB)
CHAN_FLOOR_DB = -60.0  # 큰 쪽도 이보다 작으면(소리가 거의 없음) 그대로
_CHAN_MEMO = {}


def channel_levels(name, src="videos", abort=None):
    """원본 소리의 [왼쪽, 오른쪽] 크기 (dBFS · 8kHz 로 읽어 잼 · 캐시 — 파일 크기·시각이 바뀌면 다시) — 소리가 없거나 못 읽으면 None.
    멈추기(✕)·abort 면 ffmpeg 를 끄고 Cancelled (캐시에 남기지 않음 · D-405)."""
    cache = _cache_dir(name, src) / "chan_v1.json"
    st = media_path(name, src).stat()
    sig = [st.st_size, round(st.st_mtime, 3)]
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("sig") == sig:
            return c.get("lr")
    except (OSError, ValueError, AttributeError):
        pass
    with _gen_lock(str(cache)):
        import numpy as np
        acc, cnt = np.zeros(2), 0
        with _GEN_SEM:
            with core.popen([core.ffmpeg(), "-v", "error", "-i", str(media_path(name, src)), "-vn", "-af", AUDIO_T0, "-ac", "2", "-ar", "8000",
                             "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL) as proc:
                _PROCS.add(proc)  # 긴 원본은 다 읽는 데 오래 걸림 → 멈추기(✕)가 끌 수 있게
                try:
                    while True:
                        if CANCEL.is_set() or (abort is not None and abort.is_set()):
                            proc.kill()
                            break
                        raw = proc.stdout.read(8000 * 4 * 10)
                        if not raw:
                            break
                        x = np.frombuffer(raw[: len(raw) // 4 * 4], np.int16).reshape(-1, 2).astype(np.float64) / 32768.0
                        acc += (x * x).sum(axis=0)
                        cnt += len(x)
                finally:
                    _PROCS.discard(proc)
        if CANCEL.is_set() or (abort is not None and abort.is_set()):
            raise Cancelled()
        lr = [round(10 * math.log10(max(1e-12, float(v) / cnt)), 1) for v in acc] if cnt and proc.returncode == 0 else None
        try:  # 캐시를 못 써도(디스크 가득·잠긴 파일) 잰 값은 씀
            cache.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(cache, json.dumps({"lr": lr, "sig": sig}))
        except OSError as e:
            studiolog.trace(e, "소리 채널 캐시")
        return lr


def one_sided(lr):
    """[왼쪽, 오른쪽] 크기 → 양쪽에 채울 채널 번호 (0 왼쪽 · 1 오른쪽) · 고르면 None (D-404)."""
    if not lr or len(lr) != 2 or max(lr) < CHAN_FLOOR_DB or abs(lr[0] - lr[1]) < CHAN_FILL_DB:
        return None
    return 0 if lr[0] > lr[1] else 1


def _chan_fill(md, abort=None):
    """대사 클립 원본이 한쪽만 녹음됐으면 큰 쪽을 양쪽에 채우는 소리 필터 목록 (아니면 []) — 원본마다 한 번만 잼 (못 재도 한 번만)."""
    f, src = md.get("file"), md.get("src", "videos")
    key, k = None, None
    try:
        st = media_path(f, src).stat()
        key = (f, src, st.st_size, st.st_mtime)
        if key not in _CHAN_MEMO:
            _CHAN_MEMO[key] = one_sided(channel_levels(f, src, abort))
        k = _CHAN_MEMO[key]
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001 — 못 재면 예전처럼 그대로 (캐시를 못 써도 같은 원본 클립마다 다시 읽지 않게 · D-405)
        studiolog.trace(e, "소리 채널 재기")
        if key is not None:
            _CHAN_MEMO[key] = None
    return [] if k is None else ["aformat=channel_layouts=stereo", f"pan=stereo|c0=c{k}|c1=c{k}"]


def thumbs(name, src="videos"):
    """타임라인용 썸네일 띠 (가로로 이어붙인 한 장). 키프레임만 읽어 빠르게 (전체 디코딩의 수십 분의 1)."""
    d = _cache_dir(name, src)
    meta = d / "thumbs2.json"  # 2: 가로 16000px 이하로 제한한 버전
    jpg = d / "thumbs2.jpg"
    if meta.exists() and jpg.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    with _gen_lock(str(d)):
        if meta.exists() and jpg.exists():
            return json.loads(meta.read_text(encoding="utf-8"))
        path = media_path(name, src)
        info = probe(path)
        count = max(1, min(110, int(info["duration"] // 2) or 1))
        interval = max(0.5, info["duration"] / count) if info["duration"] else 1.0
        h = 72
        w = int(round(h * info["width"] / info["height"] / 2) * 2) or 128
        d.mkdir(parents=True, exist_ok=True)
        tm = (",".join(tonemap_chain(info["hdr"], 640)) + ",") if info.get("hdr") else ""
        tmp = d / f"thumbs2.{threading.get_ident()}.tmp.jpg"
        with _GEN_SEM:
            if info["kind"] == "video":
                dur = info["duration"] or interval * count
                vf = f"{tm}tpad=stop_mode=clone:stop_duration={dur:.3f},fps=1/{interval:.3f},scale={w}:{h},tile={count}x1"
                r = core.run([core.ffmpeg(), "-y", "-v", "verbose", "-discard:v", "nokey", "-i", str(path), "-an", "-sn", "-dn", "-vf", vf,
                              "-frames:v", "1", "-q:v", "5", str(tmp)])
                m = re.search(r"\(video\): [^\n]*?(\d+) frames decoded", r.stderr or "")
                few = bool(m) and int(m[1]) * 4 < count  # 키프레임이 아주 드문 영상 → 같은 그림만 반복되니 전체 디코딩
                if r.returncode or not tmp.exists() or few:  # 키프레임만으로 안 되는 파일은 전체 디코딩
                    core.run([core.ffmpeg(), "-y", "-v", "error", "-i", str(path), "-an", "-vf", f"{tm}fps=1/{interval:.3f},scale={w}:{h},tile={count}x1",
                              "-frames:v", "1", "-q:v", "5", str(tmp)])
            else:
                core.run([core.ffmpeg(), "-y", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-frames:v", "1", "-q:v", "5", str(tmp)])
        if tmp.exists():
            updater.replace_retry(tmp, jpg)
        data = {"interval": interval, "w": w, "h": h, "count": count if info["kind"] == "video" else 1}
        _write_atomic(meta, json.dumps(data))
        return data


def thumbs_file(name, src="videos"):
    return _cache_dir(name, src) / "thumbs2.jpg"


def poster(name, src="videos"):
    """미디어 탭 목록용 작은 그림 한 장 (영상 앞부분 한 장면만 빠르게)."""
    d = _cache_dir(name, src)
    out = d / "poster.jpg"
    if out.exists():
        return out
    with _gen_lock(str(out)):
        if out.exists():
            return out
        path = media_path(name, src)
        if not path.exists() or path.suffix.lower() in AUDIO_EXTS:
            return out
        d.mkdir(parents=True, exist_ok=True)
        info = probe(path)
        tm = (",".join(tonemap_chain(info["hdr"], 640)) + ",") if info.get("hdr") else ""
        tmp = d / f"poster.{threading.get_ident()}.tmp.jpg"
        ss = ["-ss", f"{min(5.0, (info['duration'] or 0) * 0.1):.2f}"] if info["kind"] == "video" else []
        with _GEN_SEM:
            core.run([core.ffmpeg(), "-y", "-v", "error", *ss, "-i", str(path), "-frames:v", "1", "-an", "-vf", f"{tm}scale=-2:72", "-q:v", "5", str(tmp)])
        if tmp.exists():
            updater.replace_retry(tmp, out)
        return out


def media_bundle(f, src="videos"):
    """편집실에 미디어를 올릴 때 필요한 정보 한 번에 (파형·썸네일 포함) · 보관함의 다른 영상이면 그 영상 자막도 (captions ·
    대사 트랙에 놓은 그 영상 소리에 자막 · 내보내기 timeline_captions 와 같은 media_captions · D-284)."""
    e = media_entry(f, src)
    out = {"media": e, "waveform": None, "thumbs": None}
    if e["audio"]:
        out["waveform"] = waveform(e["file"], 50, src)
    if e["kind"] in ("video", "image"):
        out["thumbs"] = thumbs(e["file"], src)
    if src == "videos" and e["kind"] == "video" and e["audio"]:
        out["captions"] = media_captions(e["file"], src)
    return out


def rev_audio(src, f, a, b, sp):
    """거꾸로 재생 클립의 소리 (미리보기용): 내보내기와 같은 areverse + 속도로 미리 만들어 둔 WAV."""
    a, b, sp = round(float(a), 3), round(float(b), 3), round(max(0.05, float(sp)), 4)
    p = media_path(f, src)
    if not p.exists():
        raise FileNotFoundError(f"파일을 찾지 못했어요 · {f}")
    d = _cache_dir(f, src) / "rev"
    # rev2_: 소리 줄기가 늦게 시작하는 파일도 제자리(AUDIO_T0)로 푼 판 — 예전 rev_ 파일(앞당겨진 소리)은 다시 만듦 (waveform_v2_ 와 같은 까닭 · D-324)
    out = d / f"rev2_{a:.3f}_{b:.3f}_{sp:.4f}.wav"
    if out.exists():
        return out
    with _gen_lock(str(out)):
        if out.exists():
            return out
        d.mkdir(parents=True, exist_ok=True)
        old = sorted(d.glob("rev_*.wav"), key=lambda x: x.stat().st_mtime)  # 예전 판은 모두 정리
        old = old + sorted(d.glob("rev2_*.wav"), key=lambda x: x.stat().st_mtime)[:-40]
        for o in old:  # 오래된 것 정리 (미리보기가 들고 있으면 Windows 는 못 지움 → 다음에)
            try:
                o.unlink(missing_ok=True)
            except OSError:
                pass
        tmp = d / f"rev.{threading.get_ident()}.tmp.wav"
        af = ",".join([AUDIO_T0, "areverse"] + _atempo(sp))
        with _GEN_SEM:
            r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, a):.3f}", "-t", f"{max(0.05, b - a):.3f}", "-i", str(p),
                          "-vn", "-af", af, "-ac", "2", "-ar", "48000", str(tmp)])
        if r.returncode or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise RuntimeError("거꾸로 재생 소리를 만들지 못했어요")
        updater.replace_retry(tmp, out)
        return out


def run_killable(cmd):
    """멈추기(✕)로 끌 수 있는 ffmpeg 실행 (검수 등). 멈추면 RuntimeError."""
    p = core.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
                   encoding="utf-8", errors="replace")
    _PROCS.add(p)
    try:
        _, err = p.communicate()
    finally:
        _PROCS.discard(p)
    if CANCEL.is_set():
        raise RuntimeError("멈췄어요")
    return subprocess.CompletedProcess(cmd, p.returncode, "", err)


# ---------- 프로젝트 (시퀀스 v2: 멀티 트랙) ----------

def _ppath(name):
    return PROJECTS / f"{core.adir(name).name}.json"


LONG_STYLE = dict(DEFAULT_STYLE, size=52, y=0.9, effect="fade")
SHORTS_STYLE = dict(DEFAULT_STYLE, weight="Black", size=72, fill="#FFFFFF", stroke="#000000", strokeW=8, y=0.8, effect="pop")
TITLE_STYLE = dict(DEFAULT_STYLE, weight="Black", size=100, fill="#FFE14D", stroke="#111111", strokeW=9, y=0.22, effect="pop")
BOX_LAYOUT = {"mode": "box", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}


def default_tracks():
    v = [{"id": f"V{i}", "k": "v", "lock": False, "hide": False, "target": i == 1, "h": 1} for i in (1, 2, 3)]
    a = [{"id": f"A{i}", "k": "a", "lock": False, "mute": False, "solo": False, "target": i == 1, "h": 1, "vol": 0.0,
          "role": "dialog" if i == 1 else "music"} for i in (1, 2, 3)]
    return v + a


def _db(v):
    return round(20 * math.log10(v), 2) if v > 1e-4 else -60.0


def _pair(a, b, media="main", vol=1.0, fade_in=0.0, fade_out=0.0, mute=False, start=0.0, reframe=0.5, vid=None):
    """원본 구간 [a, b] → V1 영상 + A1 소리 (연결된 한 쌍)."""
    link = _nid()
    base = {"media": media, "start": round(start, 4), "in": round(a, 3), "out": round(b, 3), "speed": 1.0, "rev": False, "link": link}
    v = dict(base, id=vid or _nid(), track="V1", reframe=reframe, fit="auto", fx={}, color={})
    au = dict(base, id=_nid(), track="A1", fx={"level": {"v": _db(vol), "k": []}} if abs(vol - 1) > 1e-3 else {},
              gain=0.0, fadeIn=fade_in, fadeOut=fade_out, mute=mute)
    return [v, au]


def _items_from_cuts(cuts, fade_last=0.0, zoom=1.0):
    """컷 목록 → V1/A1 쌍. 컷에 zoom 표시가 있으면 영상 크기를 키움 (줌 컷·펀치인)."""
    items, pos = [], 0.0
    for k, c in enumerate(cuts):
        pr = _pair(c["in"], c["out"], start=pos, fade_out=fade_last if k == len(cuts) - 1 else 0.0)
        if c.get("zoom") and zoom > 1.001:
            pr[0]["fx"] = {"scale": {"v": round(zoom * 100, 1), "k": []}}
            if c.get("anc"):  # 얼굴 상자로 정한 기준점 (BR-294 · 없으면 가운데)
                pr[0]["fx"]["anchor"] = {"v": [float(c["anc"][0]), float(c["anc"][1])], "k": []}
        sp = float(c.get("speed") or 1.0)  # 말 빠르기 맞추기 (#7) — 영상·소리 같이
        if abs(sp - 1) > 1e-6:
            for x in pr:
                x["speed"] = sp
        items += pr
        pos += (c["out"] - c["in"]) / sp
    return items


def _punch(cuts, segs, every, caps=None, demo=None):
    """약 every 초마다 말이 시작하는 곳에서 나눠 확대 ↔ 원래 크기를 번갈아 (잘라 낸 자리도 뒤집음 · 자막 가운데서는 안 나눔).
    단어 시각이 없는 예전 받아쓰기의 컷 리듬 (_rhythm 이 부름)."""
    starts = sorted(float(s["start"]) for s in segs or ())
    spans = _cap_spans(caps, segs)
    return _shots(cuts, (lambda pos, t=float(every or 0): t) if every and every > 0 else None,
                  lambda a, b: [x for x in starts if a + 0.6 < x < b - 0.6 and not _in_cap(x, spans)], lambda a, b: True, demo)


# ---------- 컷 리듬 맞추기 (#7): 배운 컷 길이로 긴 말 컷 나누기 · 말 빠르기 ----------
RHYTHM_MIN = 0.6     # 나눈 조각이 이보다 짧으면 안 나눔(초)
WORD_GAP = 0.1       # 단어끼리 이만큼까지 겹쳐도 그 사이를 단어 경계로 봄 (가운데는 두 단어에서 0.05초 안)
TALK_DENSE = 0.5     # 1초 글자 수가 영상 평균의 이 비율 밑이면 시범·보여 주기 (나누지도 빠르게 하지도 않음)
DEMO_GAP = 1.0       # 단어 사이가 이보다 길면 말 대신 보여 주는 중 → 그 사이에서는 안 나눔 (시범 장면이 끊기지 않게)
TEMPO_MAX = 1.12
SOFT_ZOOM = 1.08     # 확대 컷이 거의 없는 스타일은 나눈 곳만 살짝 당김 (카메라 두 대 느낌만)


def _rhythm_words(segs):
    """받아쓰기 → 단어 [(시작, 끝, 글자 수)] (시작 순)."""
    out = []
    for s in segs or ():
        for w in s.get("words") or ():
            try:
                a, b, n = float(w["s"]), float(w["e"]), len(str(w.get("w") or "").replace(" ", ""))
            except (KeyError, TypeError, ValueError):
                continue
            if b >= a and n:
                out.append((a, b, n))
    return sorted(out)


def _coach_cps(segs):
    """영상 전체 말 빠르기 (1초 글자 수 · 스타일 배우기와 같은 셈: 받아쓴 구간 길이로 나눔). 모르면 0."""
    talk = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segs or ())
    chars = sum(len(str(s.get("text") or "").replace(" ", "")) for s in segs or ())
    return chars / talk if talk > 5 else 0.0


def _chars_in(a, b, units):
    """[a, b] 안에 든 글자 수 (걸친 단어·구간은 걸친 만큼)."""
    n = 0.0
    for s, e, c in units:
        if e > a and s < b:
            n += c * ((min(b, e) - max(a, s)) / (e - s) if e - s > 1e-6 else 1.0)
    return n


def _is_talk(a, b, units, cps):
    return b - a > 1e-6 and cps > 0 and _chars_in(a, b, units) / (b - a) >= TALK_DENSE * cps


def _word_bounds(words, a, b):
    """[a, b] 안의 단어 경계 — 앞 단어들이 다 끝나고(0.1초 겹침까지) 다음 단어가 시작하는 곳의 가운데.
    말이 오래 끊긴 곳(DEMO_GAP 넘게)은 빼서, 말 없이 보여 주는 장면은 한 컷으로 둠."""
    out, me = [], None
    for s, e, _ in words:
        if e <= a or s >= b:
            continue
        if me is not None and me - WORD_GAP <= s <= me + DEMO_GAP:
            out.append(round((me + s) / 2, 3))
        me = e if me is None else max(me, e)
    return out


JUMP_EPS = 0.02      # 이웃 컷의 원본이 이만큼 넘게 안 이어지면 잘라 낸 자리(점프 컷)
FLIP_MIN = 1.5      # 화면 크기를 뒤집은 뒤 이 초(타임라인)가 지나야 다음 점프 컷에서 또 뒤집음 (1초도 안 되는 확대 ↔ 원래 크기 껌뻑임 막기)
CAP_EDGE = 0.05      # 자막 가장자리에서 이만큼 안쪽부터 '자막 가운데' (단어끼리 살짝 겹친 경계는 가장자리 · timeline_captions 가 0.05초 이하 조각은 안 띄움)


def _cap_spans(caps, segs=None):
    """나누면 안 되는 곳 [(시작, 끝)] — 자막(없으면 받아쓴 구간)이 떠 있는 동안. 여기서 컷을 나누면 같은 자막이 다시 나타나 깜빡임."""
    src = caps if caps else segs or ()
    out = []
    for c in src:
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            out.append((a, b))
    return sorted(out)


def _in_cap(x, spans):
    return any(a + CAP_EDGE < x < b - CAP_EDGE for a, b in spans)


def _cap_cuts(xs, spans, words):
    """단어 경계 xs 중 자막이 바뀌는 곳만 — 자막 경계가 단어 사이 가운데와 조금 다르면(다음 낱말 시작에 맞춘 자막) 그 경계로 옮김.
    옮긴 곳도 낱말 가운데는 아니어야 함."""
    edges = sorted({e for ab in spans for e in ab})
    out = []
    for x in xs:
        if not _in_cap(x, spans):
            out.append(x)
            continue
        near = [y for y in edges if abs(y - x) <= 0.15 and not _in_cap(y, spans)
                and not any(s + 0.05 < y < e - 0.05 for s, e, _ in words if s < x + 1 and e > x - 1)]
        if near:
            out.append(round(min(near, key=lambda y: abs(y - x)), 3))
    return sorted(set(out))


def _shots(cuts, tf, cands, talk, demo=None):
    """컷 목록 → 화면 크기 표시(zoom)가 붙은 조각. 잘라 낸 자리(점프 컷)마다 원래 크기 ↔ 확대를 뒤집어 머리 위치가 튀지 않게 하고,
    tf(타임라인 위치 → 목표 컷 길이)가 있으면 말하는 컷(talk) 중 그보다 긴 것만 cands(나눌 수 있는 곳) 중 목표에 가까운 곳에서 나눠 또 뒤집음.
    컷 안의 이어진 말은 나누지 않는 한 크기가 그대로 · 지우는 곳은 없음 (zoom 표시는 _items_from_cuts 가 확대).
    한 문장 안에서 '음'·'어'를 여러 번 잘라 내도 화면이 껌뻑이지 않게, 마지막으로 뒤집은 뒤 FLIP_MIN 초가 안 됐으면 그 점프 컷은 그대로 둠.
    demo(a, b) 가 참인 조각(말이 드문 시범 · BR-294)은 늘 원래 크기 — 그 앞 점프 컷·나눈 곳은 크기가 같을 수 있고, 다음 곳에서 다시 뒤집음."""
    out, pos, zoomed, prev, flipped = [], 0.0, False, None, 0.0

    def calm(a_, b_, z):  # 시범 조각은 화면 가득 (확대하지 않음)
        return False if z and demo is not None and demo(a_, b_) else z

    def put(a_, b_, z, first):  # 시범이라 크기를 안 바꾼 나눈 곳은 나누지 않음 (같은 컷 안에서 앞 조각과 같은 크기면 이어 붙임)
        if not first and out and out[-1]["zoom"] == z and abs(float(out[-1]["out"]) - a_) < 1e-9:
            out[-1]["out"] = b_
        else:
            out.append({"in": round(a_, 3), "out": b_, "zoom": z})
    for c in cuts:
        a, b = float(c["in"]), float(c["out"])
        if prev is not None and abs(a - prev) > JUMP_EPS and pos - flipped >= FLIP_MIN - 1e-9:
            zoomed, flipped = not zoomed, pos
        cur = a
        if tf is not None and tf(pos) > 0 and b - a > tf(pos) and talk(a, b) and not (demo is not None and demo(a, b)):
            bs = cands(a, b)
            while True:
                t = tf(pos + cur - a)
                if t <= 0 or b - cur <= 1.5 * t:
                    break
                half = max(RHYTHM_MIN, 0.5 * t)
                cand = [x for x in bs if cur + half <= x <= b - half]
                if not cand:
                    break
                x = min(cand, key=lambda v: abs(v - (cur + t)))
                z = calm(cur, x, zoomed)
                put(cur, x, z, cur == a)
                cur, zoomed, flipped = x, not z, pos + x - a
        zoomed = calm(cur, b, zoomed)
        put(cur, c["out"], zoomed, cur == a)
        pos, prev = pos + b - a, b
    return out


def _split_rhythm(cuts, words, target, units=None, cps=None, caps=None, segs=None, demo=None):
    """말하는 컷 중 목표 컷 길이(targetShot)보다 긴 것을 단어 경계에서 나눔 — 자막이 바뀌는 곳에서만 (자막 가운데서 나누면 같은 자막이 깜빡임).
    나눈 곳·잘라 낸 자리마다 원래 크기 ↔ 확대(zoomScale)를 뒤집음 (카메라 두 대처럼 · _shots).
    target: 초, 또는 타임라인 위치 → 초 (도입·본론·마무리). 시범처럼 말이 드문 컷은 나누지 않음."""
    tf = target if callable(target) else (lambda pos, t=float(target or 0): t)
    units = words if units is None else units
    cps = cps or 0.0  # 모르면 말이 드문 컷도 나눔
    spans = _cap_spans(caps, segs)
    return _shots(cuts, tf, lambda a, b: [x for x in _cap_cuts(_word_bounds(words, a, b), spans, words) if a < x < b],
                  lambda a, b: not cps or _is_talk(a, b, units, cps), demo)


def _curve_fn(curve3, total):
    """컷 리듬 3구간 [도입 30초, 본론, 마무리 20초] → 타임라인 위치별 목표 컷 길이."""
    c = [float(x or 0) for x in curve3]
    return lambda pos: c[0] if pos < 30.0 else c[2] if pos >= total - 20.0 else c[1]


def _ov(a, b, spans):
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)


def _apply_tempo(cuts, units, cps, factor, spans=(), still=()):
    """말이 촘촘한 곳만 factor 배 빠르게 (1.0~1.12) · 시범처럼 말이 드문 곳·구령이 대부분인 곳(still: 구령 줄 [(시작, 끝)] — 시범 박자)은 1.0 그대로.
    빠르기는 원본이 이어진 구간 안에서 말이 1초(DEMO_GAP) 넘게 쉬는 곳(자막 밖)에서만 바뀜 — 나눈 조각마다 따로 정하면
    문장 가운데서 1.12배 ↔ 1.0배로 말소리가 갑자기 늘어짐. 바뀌는 곳이 조각 안이면 그 자리에서 조각을 나눔 (화면 크기는 그대로)."""
    f = round(min(TEMPO_MAX, max(1.0, float(factor or 1.0))), 3)
    if f <= 1.0:
        return [dict(c) for c in cuts]
    runs = []
    for c in cuts:  # 원본이 이어진 조각끼리 묶음
        if runs and abs(float(c["in"]) - float(runs[-1][-1]["out"])) <= JUMP_EPS:
            runs[-1].append(c)
        else:
            runs.append([c])
    out, last, prev_out = [], None, None
    for run in runs:
        a, b = float(run[0]["in"]), float(run[-1]["out"])
        # 잘라 낸 자리가 이어 말하는 한 자막 가운데면(앞뒤가 같은 자막 · 양쪽 1초 안에 말) 앞 구간 빠르기를 이어 감
        # — 한 자막 안에서 1.12배 ↔ 1.0배로 바뀌지 않게 (말이 드문 시범 가운데를 잘라 낸 곳은 그대로)
        carry = last if last is not None and prev_out is not None and any(
            s0 + CAP_EDGE < prev_out < s1 and s0 < a < s1 - CAP_EDGE for s0, s1 in spans) and any(
            prev_out - DEMO_GAP < u[1] and u[0] < prev_out for u in units) and any(
            a < u[1] and u[0] < a + DEMO_GAP for u in units) else None
        us = sorted(u for u in units if u[1] > a and u[0] < b)
        pts, gaps, me = [a], [], None
        for s, e, _ in us:  # 말이 1초 넘게 쉬는 곳 (자막이 떠 있는 곳은 빼고) — 우선 가운데로 나눠 말이 촘촘한지 봄
            if me is not None and s - me > DEMO_GAP:
                m = round((me + s) / 2, 3)
                if a + 0.2 < m < b - 0.2 and not _in_cap(m, spans):
                    pts.append(m)
                    gaps.append((me, s))
            me = e if me is None else max(me, e)
        pts.append(b)
        blocks = []
        for k, (p, q) in enumerate(zip(pts, pts[1:])):
            talk = _is_talk(p, q, units, cps) and not (still and _ov(p, q, still) > 0.5 * _ov(p, q, [u[:2] for u in units]))
            sp = carry if k == 0 and carry is not None else f if talk else 1.0
            if blocks and blocks[-1][2] == sp:
                blocks[-1][1] = q
            else:
                if blocks:  # 빠르기가 바뀌는 쉼: 쉬는 동안은 느린 쪽(시범)으로 — 바뀌는 곳은 말 바로 뒤·바로 앞 0.15초
                    me, s0 = gaps[k - 1]
                    x = round(me + 0.15 if blocks[-1][2] > sp else s0 - 0.15, 3)
                    blocks[-1][1], p = x, x
                blocks.append([p, q, sp])
        last, prev_out = blocks[-1][2], b
        for c in run:
            x0, x1 = float(c["in"]), float(c["out"])
            parts = [(max(x0, p), min(x1, q), sp) for p, q, sp in blocks if min(x1, q) - max(x0, p) > 1e-6]
            merged = []
            for p, q, sp in parts:  # 0.2초도 안 되는 자투리는 옆 조각 빠르기로
                if merged and (q - p < 0.2 or merged[-1][1] - merged[-1][0] < 0.2):
                    merged[-1] = (merged[-1][0], q, merged[-1][2] if q - p < 0.2 else sp)
                else:
                    merged.append((p, q, sp))
            for k, (p, q, sp) in enumerate(merged):
                d = dict(c, **{"in": c["in"] if k == 0 else round(p, 3), "out": c["out"] if k == len(merged) - 1 else round(q, 3)})
                if sp > 1.0:
                    d["speed"] = sp
                else:
                    d.pop("speed", None)
                out.append(d)
    return out


def _rhythm(cuts, segs, st, every, caps=None, calm=False):
    """스타일 가편집의 컷 목록 → 컷 리듬(잘라 낸 자리·나눈 곳마다 화면 크기 뒤집기) + 말 빠르기.
    caps: 이 편집본에 쓸 자막 [{start, end}] — 그 가운데서는 나누지도 빠르기를 바꾸지도 않음 (없으면 받아쓴 구간).
    스타일 값이 없으면 나누지 않고 잘라 낸 자리에서만 뒤집음. calm: 말이 드문 시범 컷은 확대하지 않음 (BR-294 · 말 빠르기를 알 때만)."""
    words = _rhythm_words(segs)
    units = words or [(float(s["start"]), float(s["end"]), len(str(s["text"]).replace(" ", ""))) for s in segs]
    cps = _coach_cps(segs)
    demo = (lambda a, b: not _is_talk(a, b, units, cps)) if calm and cps > 0 else None
    split = float(st.get("splitShot") or 0)
    c3 = st.get("curve3") if isinstance(st.get("curve3"), list) and len(st["curve3"]) == 3 else [split] * 3
    if split > 0 and words:
        out = _split_rhythm(cuts, words, _curve_fn(c3, sum(c["out"] - c["in"] for c in cuts)), units, cps, caps, segs, demo)
    else:
        out = _punch(cuts, segs, every, caps, demo)
    ref = float(st.get("tempo") or 0)
    if not (ref > 0 and cps > 0):
        return out
    still = [(float(s["start"]), float(s["end"])) for s in segs if takes.is_chant(s.get("text"))]
    return _apply_tempo(out, units, cps, ref / cps, _cap_spans(caps, segs), still)


# ---------- 확대 컷 기준점 (BR-294 · D-484): 그 컷 가운데 장면의 얼굴 상자로 · 시범 장면은 확대 안 함 ----------
ZOOM_FACE_Y = 1 / 3      # 확대 컷에서 얼굴 가운데를 이 높이(화면 위 1/3)까지 올림 (이미 그보다 위면 그 자리 그대로)
ZOOM_HEAD_PAD = 0.02     # 확대한 뒤 머리 위(얼굴 상자 위 + 머리카락 여유 FACE_PAD)가 화면 위 끝에서 이만큼은 남게 (화면 높이 비율)
ZOOM_FACE_MAX = 48       # 확대 컷 얼굴 찾기에 볼 장면 수 한도 — 넘으면 고르게 골라 가까운 장면 얼굴을 씀 (긴 레슨도 가편집이 오래 안 걸리게)
ZOOM_FACE_NEAR = 6.0     # 고르게 고른 장면이 그 컷 가운데에서 이 초 안일 때만 그 얼굴을 씀 (아니면 가운데)


def zoom_anchor(box, s):
    """얼굴 상자 box [x, y, w, h] (원본 화면 0~1) · 확대 배율 s → 기준점 [ax, ay] (클립 상자 0~1 · 이 점 둘레로 키움).
    가로는 얼굴 자리 그대로 · 세로는 얼굴 가운데가 화면 위 1/3(ZOOM_FACE_Y)보다 아래면 거기까지 올림 — 머리 위가 화면 밖으로 나가지 않는 만큼만 ·
    0~1 안 (확대한 영상이 화면을 다 덮게). 예전: 늘 가운데 [0.5, 0.5] → 말하는 장면에서 머리 위가 잘리고 가슴이 가운데로."""
    if s <= 1.001:
        return [0.5, 0.5]
    x, y, w, h = (float(v) for v in box)
    fx, fy = x + w / 2, y + h / 2
    ay = (s * fy - min(fy, ZOOM_FACE_Y)) / (s - 1)
    ay = min(ay, (s * max(0.0, y - FACE_PAD[1]) - ZOOM_HEAD_PAD) / (s - 1))
    return [round(min(1.0, max(0.0, fx)), 3), round(min(1.0, max(0.0, ay)), 3)]


def face_anchors(name, cuts, zoom, cache=None):
    """확대 컷(원본이 이어진 확대 조각 묶음)마다 그 가운데 장면(원본)의 가장 큰 얼굴로 기준점("anc") (BR-294) — 강조 글씨 자리와 같은 얼굴 찾기(_faces_at · 640px 장면 ·
    임시 폴더 · 4장씩)를 씀. 얼굴 모델이 없거나(내려받지 않음) 영상·얼굴을 못 보면 그 컷은 그대로(가운데). 확대 컷이 ZOOM_FACE_MAX 개를 넘으면
    고르게 고른 장면만 보고 ZOOM_FACE_NEAR 초 안의 가장 가까운 장면 얼굴을 씀."""
    if zoom <= 1.001:
        return cuts
    runs = []  # 원본이 이어진 확대 조각끼리 한 묶음 (말 빠르기 조각마다 기준점이 바뀌면 이어진 장면에서 화면이 튐) → 묶음 하나 = 기준점 하나
    for k, c in enumerate(cuts):
        if not c.get("zoom"):
            continue
        if runs and runs[-1][-1] == k - 1 and abs(float(c["in"]) - float(cuts[k - 1]["out"])) <= JUMP_EPS:
            runs[-1].append(k)
        else:
            runs.append([k])
    if not runs or not (core.VIDEOS / str(name)).is_file():
        return cuts
    mid = {r[0]: round((float(cuts[r[0]]["in"]) + float(cuts[r[-1]]["out"])) / 2, 2) for r in runs}
    look = sorted(set(mid.values()))
    if len(look) > ZOOM_FACE_MAX:
        look = [look[round(i * (len(look) - 1) / (ZOOM_FACE_MAX - 1))] for i in range(ZOOM_FACE_MAX)]
    cache = {} if cache is None else cache
    with idle.using():  # 가편집은 바로 답하는 요청 — 쉬는 동안 내려놓기가 얼굴 모델을 지우지 않게 (emphasis_placer 와 같음)
        if not _face_ready(name):
            return cuts
        _faces_at(name, look, cache)
    seen = sorted(t for t in look if cache.get(t))
    if not seen:
        return cuts
    out = [dict(c) for c in cuts]
    for r in runs:
        t = min(seen, key=lambda x: abs(x - mid[r[0]]))
        if abs(t - mid[r[0]]) > ZOOM_FACE_NEAR + 1e-9:
            continue
        f = max(cache[t], key=lambda b: float(b[2]) * float(b[3]))
        for k in r:
            out[k]["anc"] = zoom_anchor(f, zoom)
    return out


def _new_seq(name, fmt, items, **kw):
    s = {"id": _nid(), "name": name, "format": fmt, "v": 2, "tracks": default_tracks(), "items": items, "trans": [],
         "markers": [], "captionsOn": True, "titles": [], "shapes": [], "master": {"volume": 1.0, "normalize": True},
         "duck": {"on": True, "amount": -14.0}}
    s.update(kw)
    return s


GENERIC = {"팁", "꿀팁", "중요", "핵심", "강조", "비결", "방법", "포인트", "무조건", "절대", "실수", "차이", "첫 번째", "첫번째",
           "잘하", "어떻게", "비밀", "원리", "기술", "프로", "힘들"}


HOOK_MAX = 16          # 쇼츠 위 큰 제목 글자 수 (띄어쓰기 빼고) — 넘는 문장은 낱말 경계에서 자름
HOOK_MIN = 5          # 이보다 짧은 문장 조각은 제목 후보에서 뺌
HOOK_ASK = 20          # 묻는 말('…왜 안될까요?')은 이 글자까지 통째로 (잘린 질문은 뜻이 없음)
HOOK_TIP = ("꿀팁", "팁", "비결", "방법", "포인트", "핵심", "중요")
HOOK_NOT_TOPIC = {"공", "골대", "슛", "킥", "턴", "패스", "수비", "공격"}   # 혼자서는 쇼츠 주제가 못 되는 흔한 말 ('슛!' · '골대!')
ORDINAL = re.compile(r"(?:첫|두|세|네|다섯|여섯|일곱)\s?번째\s?(?:포인트|동작|기술|방법|팁|단계)")


def _hook_cut(text, limit=HOOK_MAX):
    """문장 → 큰 제목 글 (끝 마침표 뗌 · limit 글자를 넘으면 낱말 경계에서 자르고 끝 쉼표 뗌)."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"^(?:자|어|음|아|네|그)[,.…]+\s*", "", t)  # 앞 군말('자, …') — '네 번째'의 '네'는 그대로
    t = re.sub(r"[.,。、…~\s]+$", "", t)
    if len(_norm(t)) <= limit or (t.endswith(("?", "!")) and len(_norm(t)) <= HOOK_ASK):  # 묻는 말·외친 말은 조금 길어도 통째로
        return t
    out = []
    for w in t.split():
        if out and len(_norm(" ".join(out + [w]))) > limit:
            break
        out.append(w)
    return re.sub(r"[.,…\s]+$", "", " ".join(out)) if out else t[:limit]


HOOK_COUNT = re.compile(r"^(?:자[,\s]*)?(?:마지막,?\s*)?(?:첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열|한|\d+)\s?(?:번째|개|번|골)|"
                        r"(?:한|두|세|네|다섯|\d)\s?개(?:에요|예요|째|\W|$)")


def _hook_sentence_ok(t):
    """제목으로 쓸 문장인지 — 순서·개수로 시작하거나 개수를 말하는 문장('마지막 다섯번째, 이거 넣으면 세개에요' · 결과를 미리 알려 줌)과
    반응만 있는 짧은 문장('이것도 들어갔어요' · 혼자서는 뜻이 없음)은 아님 (E2 검토)."""
    t = str(t or "").strip()
    if not t or HOOK_COUNT.search(t):
        return False
    return not (REACTION.match(t) or (re.search(REACT_STRONG, t) and len(_norm(t)) <= 10))


def short_hooks(shorts, segs):
    """쇼츠 후보들의 위 큰 제목 (E2 · BR-093) — 쇼츠마다 다르게:
    ① 그 쇼츠 대사의 풋살 용어(hooks.topic_keywords · 흔한 말 하나는 빼고) → '디딤발 꿀팁'(포인트·핵심 같은 말이 있으면) / '트래핑!'
    ② 영상 전체 주제 + 순서 말 → '퍼스트 터치 첫 번째 포인트'
    ③ 첫 내용 문장 (인사·반응·자기소개 빼고 · 16자 안 낱말 경계)."""
    import hooks
    texts = [str(s.get("text") or "") for s in segs]
    try:
        whole = [t for t in hooks.topic_keywords(texts, n=3, lesson=False) if hooks._compact(t) not in HOOK_NOT_TOPIC - {"패스"}]
    except Exception:  # noqa: BLE001 — 용어 사전을 못 읽어도 제목은 만듦
        whole = []
    challenge = any("챌린지" in t for t in texts)
    used, out = set(), []
    for r in shorts:
        inside = [t for t in texts_in(segs, r["start"], r["end"])]
        body = " ".join(inside)
        cands = []
        try:
            own = [t for t in hooks.topic_keywords(inside, n=3, lesson=False) if len(hooks._compact(t)) >= 2 and hooks._compact(t) not in HOOK_NOT_TOPIC]
        except Exception:  # noqa: BLE001
            own = []
        tip = any(k in body for k in HOOK_TIP)
        head = " ".join(inside[:2])   # 쇼츠 첫 두 문장에서 소개한 말이 그 쇼츠의 주제 (뒤에 지나가며 한 말 '수비가 …' 말고)
        lead = [t for t in own if hooks._compact(t) in hooks._compact(head)]
        cands += [f"{t} 꿀팁" if tip else f"{t}!" for t in lead]
        m = ORDINAL.search(head)
        if m and whole:
            cands.append(f"{whole[0]}\n" + re.sub(r"(첫|두|세|네|다섯|여섯|일곱)\s?번째\s?", r"\1 번째 ", m.group(0)))  # 두 줄로 ('첫 / 번째'처럼 갈라지지 않게)
        cands += [f"{t} 꿀팁" if tip else f"{t}!" for t in own if t not in lead]
        sent = _hook_cut(r["title"]) if r.get("title") and len(_norm(_hook_cut(r["title"]))) >= HOOK_MIN else ""  # 너무 짧은 첫 문장('네 번째')은 안 씀
        sent = sent if _hook_sentence_ok(sent) else ""
        if sent.endswith("?"):  # 묻는 말 첫 문장은 주제어보다 앞 ('패스하고 그 자리에 서 있으면 왜 안될까요?')
            cands.append(sent)
        if whole:  # 문장보다 영상 주제 ('슈팅 챌린지' · '슈팅 꿀팁' · '슈팅!') — 쇼츠마다 다르게 고르도록 여럿
            cands += ([f"{whole[0]} 챌린지"] if challenge else []) + ([f"{whole[0]} 꿀팁"] if tip else []) + [f"{whole[0]}!"]
        cands.append(sent)
        cands.append(_old_hook(r))
        h = next((c for c in cands if c and _norm(c) not in used and _norm(c) not in {_norm(g) for g in GENERIC}), cands[0] if cands else "")
        used.add(_norm(h))
        out.append(h)
    return out


def texts_in(segs, a, b):
    """[a, b] 안에서 시작하는 말 글들."""
    return [str(s.get("text") or "") for s in segs if a - 0.05 <= float(s["start"]) < b]


def _hook(rec_item):
    """쇼츠 제목: 추천이 고른 제목(short_hooks · 'hook') — 없으면 예전 규칙 ('주제어 꿀팁' / '주제어!' / 첫 문장)."""
    if rec_item.get("hook"):
        return rec_item["hook"]
    return _old_hook(rec_item)


def _old_hook(rec_item):
    kws = rec_item["keywords"]
    topic = next((k for k in kws if k not in GENERIC), None)
    tip = any(k in kws for k in ("꿀팁", "팁", "비결", "방법", "포인트"))
    first = re.sub(r"^\(테스트\)\s*", "", rec_item["title"]).strip()
    if topic and tip:
        return f"{topic} 꿀팁"
    if topic:
        return f"{topic}!"
    return _hook_cut(first)


# ---------- 쇼츠 첫머리 주제 줄 (BR-120 · D-220) ----------
# 자동으로 만든 쇼츠의 처음 TOPIC_SEC 초에 '영상 주제 + 이 쇼츠 포인트'를 한 줄로 (글자만 · 소리·길이는 그대로).
# 가편집 쇼츠: 내내 떠 있는 위 큰 제목(BR-093) 바로 위에 작은 머리 줄 — 위 큰 제목과 같은 낱말은 넣지 않아 두 줄이 한 소개로 읽힘.
# 영상 주제·포인트는 사용자가 붙인 영상 이름·용어 사전에 있는 말·순서 말만 씀 (받아쓰기 헛말·문장 조각은 안 씀) · 없으면 넣지 않음.
TOPIC_SEC = 2.5        # 주제 줄 시간(초) — MSG 쇼츠 첫 훅 글자(msg.SHORTS_HOOK_SEC)와 같음
TOPIC_MAX = 14         # 주제 줄 글자 수 (띄어쓰기·'·' 빼고) — 휴대폰에서 한 줄
TOPIC_NAME_MAX = 12    # 영상 이름을 주제로 쓸 수 있는 길이 (msg._topic 과 같음)
TOPIC_TOP = 0.07       # 쇼츠 위쪽 안전 영역 (유튜브 쇼츠 위 메뉴 아래)
TOPIC_ONE = 0.9        # 한 줄로 확실히 들어가는 폭 (어림 글자 폭이 실제보다 조금 좁아도 두 줄로 넘치지 않게)
TOPIC_SIZES = (72, 64)  # 주제 줄 글자 크기 (큰 것부터 · 위 큰 제목 100 · 말 자막 72)
TOPIC_SWAP_SIZES = (100, 90, 80, 72)  # 바꿔 끼울 때(위 큰 제목 자리에 처음 TOPIC_SEC 초) 글자 크기 — 위 큰 제목 크기부터
TOPIC_STYLE = dict(TITLE_STYLE, size=TOPIC_SIZES[0], fill="#FFFFFF", stroke="#111111", strokeW=8, effect="pop")
ORD_N = {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7}
POINT_AT = re.compile(r"(첫|두|세|네|다섯|여섯|일곱)\s?번째\s?(포인트|동작|기술|방법|팁|단계)(?:는|은|가|이)?[\s,.:!]*")
ORD_ANY = re.compile(r"(?:첫|두|세|네|다섯|여섯|일곱)\s?번째")
# 요점 말 뒤에 붙어 하나의 요점이 되는 낱말 ('디딤발 방향' · '패스 방향') — 이 목록 밖의 낱말은 받아쓰기가 잘못 들은 말일 수 있어 안 붙임
POINT_NOUNS = ("방향", "거리", "위치", "각도", "타이밍", "자세", "높이", "속도", "세기", "시선", "중심", "체중", "모양", "순서", "리듬", "간격")
KEY_AT = re.compile(r"(?<![가-힣A-Za-z0-9])([가-힣A-Za-z0-9]{1,8}(?:\s[가-힣]{2,5})?)(?:이|가|은|는)\s?(?:진짜\s|정말\s|제일\s|가장\s)?(?:핵심|생명|포인트|중요)")
TOPIC_NAME_JUNK = re.compile(r"IMG|DSC|MOV|VID|GOPR|PXL|KakaoTalk|\d{3,}|\d+\s?(?:월|일|시|분)(?![가-힣])|촬영|녹화|원본|편집|최종|클립|복사본|"
                             r"^새\s|무제|제목\s?없음|테스트", re.I)
TOPIC_NAME_PLAIN = {"오늘", "연습", "영상", "레슨", "풋살", "축구", "수업", "훈련", "클래스", "브이로그", "쇼츠", "기초", "강의", "꿀팁", "챌린지"}
TOPIC_SAME = {"슛": "슈팅"}   # 같은 말로 보는 낱말 (위 큰 제목 '슛!' 과 주제 줄 '슈팅 챌린지')


def _topic_words(t):
    """같은 낱말 비교용 낱말 (조사 뗌 · 두 글자 이상 · '슛'='슈팅') — '패스'·'공' 같은 흔한 말(HOOK_NOT_TOPIC)은 뺌."""
    import hooks
    out = set()
    for w in re.findall(r"[가-힣A-Za-z0-9]+", str(t or "")):
        w = hooks._strip_josa(w).lower()
        w = TOPIC_SAME.get(w, w)
        if len(w) >= 2 and w not in HOOK_NOT_TOPIC:
            out.add(w)
    return out


def _letters(t):
    """글자만 이은 글 (띄어쓰기·줄바꿈·문장 부호·'·' 뺌 · 소문자)."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(t or "")).lower()


def _shares_word(a, b):
    """두 글이 같은 말을 하는지 — 한쪽 글 전체가 다른 쪽에 그대로 들어 있거나('패스' ↔ '2대1 패스!' · '슈팅 챌린지' ↔ '슈팅 챌린지'),
    흔한 말이 아닌 같은 낱말이 있음('퍼스트 터치 레슨' ↔ '퍼스트 터치\\n첫 번째 포인트' · '슛'='슈팅' · '드리블' ↔ '드리블하고' 처럼
    한쪽이 다른 쪽으로 시작해도 같은 낱말). '패스'·'슛' 같은 흔한 말 하나만 같으면 다른 말 ('2대1 패스' ↔ '리턴 패스' ·
    '패스 앤 무브 드릴' ↔ '패스하고 그 자리에 …' — 검토: 흔한 말 하나로 영상 주제가 이미 있다고 보면 주제 줄이 빠지거나 '포인트 2'만 남음)."""
    ca, cb = _letters(a), _letters(b)
    if min(len(ca), len(cb)) >= 2 and (ca in cb or cb in ca):
        return True
    A, B = _topic_words(a), _topic_words(b)
    return any(x == y or x.startswith(y) or y.startswith(x) for x in A for y in B)


def _has_term(words, term):
    """낱말 목록(조사 뗀)에 용어가 낱말 그대로 이어서 있는지 ('퍼스트 터치' ⊂ ['퍼스트', '터치', '레슨'] · '원터치'의 '터치'는 아님)."""
    import hooks
    tw = [hooks._strip_josa(w).lower() for w in re.findall(r"[가-힣A-Za-z0-9]+", term)]
    return bool(tw) and any(words[i:i + len(tw)] == tw for i in range(len(words) - len(tw) + 1))


def _name_topic(name, texts, ranked):
    """사용자가 붙인 영상 이름이 영상 주제로 쓸 만하면 그 이름 ('20261008_MSGRAW01_퍼스트 터치 레슨.mp4' → '퍼스트 터치 레슨'), 아니면 ''.
    12자 안 한국어 · 카메라·날짜·'촬영' 같은 이름 빼고 · 이름이 대사와 맞을 때만 — 대사에서 가장 많이 나온 용어(흔한 말이 아니면 3위까지)가
    이름에 낱말 그대로 있거나, 이름의 낱말('오늘'·'연습'·'패스' 같은 흔한 말 말고)이 대사에 낱말 그대로 두 번 넘게 나옴
    (검토: 낱말 속 글자로만 보면 '슈팅 연습'이 '바로 슈팅까지' 한 번으로 · '…퍼스트 터치 레슨'이 '원터치'로 2대1 패스 영상의 주제가 됨)."""
    import hooks
    import msg
    t = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", msg._title_of(name))).strip(" -_.,")
    if not (t and re.search(r"[가-힣]", t) and 2 <= len(_norm(t)) <= TOPIC_NAME_MAX and not TOPIC_NAME_JUNK.search(t)
            and re.fullmatch(r"[가-힣A-Za-z0-9 &+'!?-]+", t)):
        return ""
    words = [hooks._strip_josa(w).lower() for w in re.findall(r"[가-힣A-Za-z0-9]+", t)]
    if any(_has_term(words, term) and (k == 0 or hooks._compact(term) not in HOOK_NOT_TOPIC) for k, term in enumerate(ranked or ())):
        return t
    from collections import Counter
    said = Counter(hooks._strip_josa(w).lower() for w in re.findall(r"[가-힣A-Za-z0-9]+", " ".join(texts)))
    return t if any(said[w] >= 2 for w in words if len(w) >= 2 and w not in TOPIC_NAME_PLAIN and w not in HOOK_NOT_TOPIC) else ""


def video_topic(name, segs):
    """쇼츠 주제 줄의 영상 주제 (BR-120) — 못 정하면 ''.
    ① 사용자가 붙인 짧은 한국어 영상 이름이 대사와 맞으면 그 이름 (_name_topic)
    ② 대사에서 가장 많이 나온 풋살 용어 (용어 사전에 있는 말만 — 받아쓰기 헛말·조각은 안 씀) · '챌린지' 영상이면 '슈팅 챌린지'
       ('패스'·'슛' 같은 흔한 말 하나는 챌린지일 때만)."""
    import hooks
    texts = [str(s.get("text") or "") for s in segs or ()]
    try:
        ranked = hooks._ranked_terms(texts, hooks.topic_terms(), 3) or hooks._ranked_terms(texts, hooks.LESSON_SKILLS, 3)
    except Exception:  # noqa: BLE001 — 용어 사전을 못 읽으면 영상 이름(대사에 두 번 넘게 나온 낱말)으로만 · 그것도 아니면 주제 없음
        ranked = []
    if name:
        t = _name_topic(name, texts, ranked)
        if t:
            return t
    challenge = any("챌린지" in x for x in texts)
    if not ranked:
        return ""
    t = ranked[0]
    if hooks._compact(t) in HOOK_NOT_TOPIC:
        return f"{t} 챌린지" if challenge else ""
    return f"{t} 챌린지" if challenge and "챌린지" not in t else t


def _point_terms():
    import hooks
    return tuple(sorted(set(hooks.topic_terms()) | set(hooks.LESSON_SKILLS), key=lambda w: -len(hooks._compact(w))))


def _point_phrase(txt, terms=None):
    """글 머리가 용어 사전의 말로 시작하면 그 말 (+ 바로 뒤 '방향'·'거리' 같은 요점 낱말) — '디딤발 방향' · '트래핑' · '리턴 패스'.
    '패스'·'슛' 같은 흔한 말 하나뿐이면 요점이 아님 ('패스 방향'은 요점) · 아니면 None."""
    import hooks
    t = re.sub(r"^(?:자|어|음|아|네|그|이제|그리고)[,.\s]+", "", str(txt or "").strip())
    if not t:
        return None
    hit = next(((a, b, term) for a, b, term in hooks._find_terms(t, terms or _point_terms()) if a == 0), None)
    if not hit:
        return None
    a, b, term = hit
    nxt = re.match(r"\s+([가-힣]+)", t[b:])
    noun = nxt and hooks._strip_josa(re.sub(r"(?:이에요|예요|입니다|이죠|이고|이랑|으로|에서)$", "", nxt.group(1)))
    if noun in POINT_NOUNS and not re.match(r"[가-힣]", t[b:]):
        return f"{term} {noun}"
    if hooks._compact(term) in HOOK_NOT_TOPIC:
        return None
    return term


def short_point(r, segs):
    """이 쇼츠의 포인트 (BR-120) → {"n": 순서 번호|None, "unit": '포인트'·'동작' 같은 순서 낱말, "phrase": 요점 말|None}.
    순서: 쇼츠 첫 두 문장의 '두 번째 포인트는 디딤발이에요'(번호 + 그 뒤 요점 말) → 쇼츠 안 'X이 핵심이에요'의 X('디딤발 방향')
    → 첫 두 문장에서 소개한 풋살 용어(BR-093 ① 과 같음). 모두 용어 사전에 있는 말만 ('고개 들기'처럼 사전에 없는 말은 못 씀 · I-161)."""
    import hooks
    inside = texts_in(segs, float(r["start"]), float(r["end"]))
    terms = _point_terms()
    n = phrase = None
    unit = "포인트"
    for t in inside[:2]:
        m = POINT_AT.search(t)
        if m and (m.start() == 0 or re.fullmatch(r"(?:자|그리고|마지막,?|이제)[,\s]*", t[:m.start()])):
            n, unit = ORD_N[m.group(1)], m.group(2)
            phrase = _point_phrase(t[m.end():], terms)
            break
    if not phrase:
        for t in inside:
            for m in KEY_AT.finditer(t):
                ws = [w for w in m.group(1).split() if len(w) >= 2]
                p = _point_phrase(" ".join(ws), terms) if ws else None
                if p and len(p.split()) == len(ws):  # 'X' 전체가 요점 말일 때만 ('이 차이가 중요' · '월패스는 이 타이밍이' 는 아님)
                    phrase = p
                    break
            if phrase:
                break
    if not phrase:
        head = " ".join(inside[:2])
        try:
            own = [t for t in hooks.topic_keywords(inside[:2], n=3, lesson=False) if hooks._compact(t) not in HOOK_NOT_TOPIC]
        except Exception:  # noqa: BLE001
            own = []
        phrase = next((t for t in own if len(hooks._compact(t)) >= 2 and hooks._compact(t) in hooks._compact(head) and t in terms), None)
    return {"n": n, "unit": unit, "phrase": phrase}


def topic_line(vt, point, main=""):
    """주제 줄 글 (BR-120) — 영상 주제 vt + 이 쇼츠 포인트(short_point) 중 위 큰 줄(main)에 없는 것만:
    '퍼스트 터치 레슨 · 포인트 2'(위 큰 제목 '디딤발 꿀팁') · '슈팅 챌린지 · 디딤발 방향' · '디딤발 방향'(위 큰 제목 '슈팅 꿀팁') ·
    '2대1 패스 · 포인트 2'(위 큰 제목 '리턴 패스 꿀팁' — '패스' 하나 같은 것은 같은 말이 아님 · _shares_word).
    순서 번호는 영상 주제에만 붙임 (번호 하나는 뜻이 없음 · 동그라미 숫자 '②'는 휴대폰에서 뭉개져 안 씀 — MSG 판정 round5) —
    번호만('포인트 1')은 큰 줄에 영상 주제가 그대로 있을 때만('첫 터치 레슨' 위) · 넣을 것이 없으면 '' (위 큰 제목이 이미 주제·포인트를 말함)."""
    point = point or {}
    n, p = point.get("n"), point.get("phrase")
    shown = bool(vt) and _shares_word(vt, main)          # 큰 줄이 이미 영상 주제를 말함
    v = vt if vt and not shown else ""
    whole = bool(vt) and len(_letters(vt)) >= 2 and _letters(vt) in _letters(main)   # 큰 줄에 영상 주제가 글자 그대로
    mark = f"{point.get('unit') or '포인트'} {n}" if n and (v or whole) and not ORD_ANY.search(str(main or "")) else ""
    p = p if p and not _shares_word(p, main) and not (v and _shares_word(p, v)) else ""
    cands = ([f"{v} · {p}"] if v and p else []) + ([f"{v} · {mark}"] if v and mark else []) + ([v] if v else []) + ([p] if p else []) + \
        ([mark] if mark and not v else [])
    return next((c for c in cands if 2 <= len(_norm(c.replace("·", ""))) <= TOPIC_MAX and not _shares_word(c, main)), "")


def hook_said(r, segs, hook=None):
    """위 큰 제목(hook)이 쇼츠 첫머리(처음 TOPIC_SEC + 1초)에 하는 말을 그대로 옮긴 글인지 ('패스하고 그 자리에 서 있으면 왜 안될까요?') —
    그러면 주제 줄을 그 위에 더하지 않고 처음 TOPIC_SEC 초 동안 바꿔 끼움 (큰 제목·말 자막·주제 줄이 같은 때 같은 말을 하지 않게 · 판정 pair_20)."""
    h = _norm(str(hook if hook is not None else r.get("hook") or ""))
    if len(h) < 5:
        return False
    a = float(r["start"])
    return h in _norm(" ".join(texts_in(segs, a, a + TOPIC_SEC + 1.0)))


def shorts_topic_on():
    """'쇼츠 첫머리에 주제 한 줄 넣기' 설정 (스타일 화면 · 기본 켬) — 못 읽으면 켬."""
    try:
        import style as _style
        return _style.shorts_topic_on()
    except Exception:  # noqa: BLE001
        return True


def _text_w(text, st):
    """글 한 줄 폭 어림 (화소 · text_box 와 같은 글자 폭 · 테두리 포함 · 은평 글꼴을 쓰는 중이면 그 글꼴 폭 표)."""
    black, fm = st.get("weight") == "Black", ep_font_now()
    return max([sum(_em(ch, black, fm) for ch in ln) for ln in str(text or "").replace("\r", "").split("\n")] or [0.0]) * float(st.get("size") or 0) \
        + 2 * float(st.get("strokeW") or 0)


def _one_line(text, st, W):
    """줄바꿈 없이 화면 폭(글 상자 92%) 안에 넉넉히 한 줄로 들어가는지 (어림이 조금 틀려도 두 줄로 넘치지 않게 TOPIC_ONE)."""
    return "\n" not in str(text or "") and _text_w(text, st) <= TOPIC_ONE * 0.92 * W


def _topic_fill(cap_style):
    """주제 줄 색: 말 자막 색(배운 스타일 색) · 어두운 색이면 흰 테두리 (위 큰 제목은 노랑 그대로)."""
    fill = str((cap_style or {}).get("fill") or "#FFFFFF").upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", fill):
        fill = "#FFFFFF"
    lum = sum(k * int(fill[i:i + 2], 16) / 255 for k, i in ((0.299, 1), (0.587, 3), (0.114, 5)))
    return fill, ("#111111" if lum >= 0.4 else "#FFFFFF")


def video_top(layout, items, src_w=None, src_h=None, until=TOPIC_SEC, fmt="shorts"):
    """쇼츠 위아래 박스·흐린 배경 배치에서 처음 until 초 동안 본 영상(얼굴)이 닿는 가장 위 (화면 높이 비율) — 확대 컷이면 그만큼 위로
    (기준점(anchor) 둘레로 키움 · 키프레임은 가장 큰 값). 꽉 채우기(영상이 화면 전체)·가로 화면이면 None (검사 안 함).
    _fit 의 위아래 박스 셈과 같음 · editor.html videoTop 과 같은 셈."""
    lay = dict(BOX_LAYOUT, **(layout or {}))
    if fmt != "shorts" or lay.get("mode") not in ("box", "blur"):
        return None
    W, H = frame_size(fmt)
    sw, sh = float(src_w or 1920), float(src_h or 1080)
    keep = max(0.2, 1 - float(lay.get("cropTop") or 0) - float(lay.get("cropBottom") or 0))
    ch = min(sh * keep * W * max(1.0, float(lay.get("zoom") or 1)) / sw, H)
    by = (H - ch) * float(lay.get("vpos", 0.5))
    top = by
    for it in items or ():
        if it.get("track") != "V1" or float(it.get("start") or 0) >= until or (it.get("fit") or "auto") != "auto":
            continue
        sc = param(it, "scale")
        s = max([float(sc["v"])] + [float(k["v"]) for k in sc["k"]]) / 100
        if s > 1:
            top = min(top, by + float(param(it, "anchor")["v"][1]) * ch * (1 - s))
    return top / H


def topic_title(text, top, cap_style=None, length=TOPIC_SEC, fmt="shorts", said=False, vtop=None):
    """주제 줄 title (BR-120) — 처음 TOPIC_SEC 초 · 한 줄 · 위쪽 안전 영역(TOPIC_TOP) 안 · 말 자막 띠(+틈)와 안 겹침 ·
    영상 위 끝(vtop · video_top) 위. 놓는 곳:
    ① 위 큰 제목(top)이 한 줄이고 대사를 그대로 옮긴 글이 아니면(said 아님) 그 바로 위 작은 머리 줄 (72 · 안 들어가면 64 · 말 자막 색)
    ② 아니면(두 줄·긴 질문·대사 그대로 · 위에 자리가 없음) 바꿔 끼우기: 위 큰 제목 자리·모양으로 처음 TOPIC_SEC 초 ("swap": True —
       부르는 쪽이 위 큰 제목을 그 뒤부터로 옮김 · 쇼츠가 TOPIC_SEC + 1초보다 짧으면 안 함 · 검토: 두 줄 큰 제목 쇼츠는 주제 줄이 없었음)
    자리가 없으면 None (위 큰 제목만). "under": 맞춘 위 큰 제목 id. editor.html topicTitle 과 같은 규칙."""
    if not text or not top:
        return None
    W, H = frame_size(fmt)
    ts = top.get("style") or {}
    cb = _cap_band(cap_style, H) if cap_style else None

    def clear(b):
        return b[1] >= TOPIC_TOP - 1e-9 and not (cb and _overlap(cb, (b[1], b[3]), TXT_GAP)) and not (vtop is not None and b[3] > vtop + 1e-9)
    dur = round(max(0.5, min(TOPIC_SEC, float(length))), 2)
    if not said and _one_line(top.get("text"), ts, W):
        tb = text_box(top["text"], ts, W, H)
        fill, stroke = _topic_fill(cap_style)
        for size in TOPIC_SIZES:
            st = dict(TOPIC_STYLE, size=size, fill=fill, stroke=stroke, x=float(ts.get("x", 0.5)), align=ts.get("align", "center"))
            if not _one_line(text, st, W):
                continue
            st["y"] = math.floor((tb[1] - TXT_GAP) * 1000 + 1e-6) / 1000  # 내림: 반올림으로 틈이 줄지 않게
            if clear(text_box(text, st, W, H)):
                return {"id": _nid(), "text": text, "start": 0.0, "dur": dur, "style": st, "plan": "topic", "under": top.get("id")}
    if float(length) < TOPIC_SEC + 1.0:
        return None
    for size in TOPIC_SWAP_SIZES:
        if size > float(ts.get("size") or TOPIC_SWAP_SIZES[0]) + 1e-9:
            continue
        st = dict(ts, size=size, effect="pop")
        if _one_line(text, st, W) and clear(text_box(text, st, W, H)):
            return {"id": _nid(), "text": text, "start": 0.0, "dur": TOPIC_SEC, "style": st, "plan": "topic", "under": top.get("id"), "swap": True}
    return None


def swap_in(top, kt):
    """바꿔 끼운 주제 줄(kt · swap) 뒤로 위 큰 제목(top)을 옮김 — 주제 줄이 끝난 때부터 원래 끝까지."""
    if kt and kt.get("swap") and float(top["start"]) < float(kt["start"]) + float(kt["dur"]):
        end = float(top["start"]) + float(top["dur"])
        top["start"] = round(float(kt["start"]) + float(kt["dur"]), 3)
        top["dur"] = round(end - top["start"], 3)


# ---------- 은평 스타일 쇼츠 (BR-240~BR-243 · D-420~D-423) ----------
# 위아래 파란 띠 + 가운데 영상 창(16:9 원본을 1.32배 · 화면 높이 0.28~0.70) + 위 띠의 흰 상자(폭 고정) 안 검은 큰 제목 한 줄(처음부터 끝까지)
# + 그 아래 작은 흰 사실 줄(주제 줄 글 · 끝까지) · 말 자막은 아래 띠. 스타일 화면 '쇼츠 화면 배치'에서 고름(기본은 지금처럼) ·
# 편집실 템플릿 '은평 스타일'(editor.html EP · epCard)과 같은 값·셈 — 한쪽을 고치면 다른 쪽도.
EP_KEY = "eunpyeong"
EP_BAR = "#1F4FD1"     # 띠 색 (진한 파랑 · 편집실 '박스 색'으로 바꿀 수 있음)
EP_LAYOUT = {"mode": "box", "bar": EP_BAR, "zoom": 1.32, "vpos": 0.48, "cropTop": 0.0, "cropBottom": 0.0,
             "win": True}   # win: 확대·이동해도 영상은 영상 창 안에서만 (띠·위 글자·말 자막 띠로 안 넘침 · D-424)
EP_BOX = {"x": 0.05, "y": 0.095, "w": 0.9, "h": 0.06}   # 흰 상자 (화면 비율 · 아래 끝 0.155)
EP_PAD = 28            # 상자 안 글 양옆 여백 (화소 · 1080 기준)
EP_SIZES = (72, 68, 64, 60, 56, 52, 48)   # 큰 제목 글자 크기 (한 줄로 상자에 들어가는 가장 큰 것)
EP_TITLE = dict(DEFAULT_STYLE, weight="Black", size=72, fill="#111111", stroke="#111111", strokeW=0, bgOn=False, x=0.5, align="center",
                effect="none")
EP_FACT = dict(DEFAULT_STYLE, weight="Bold", size=42, fill="#FFFFFF", stroke="#000000", strokeW=0, bgOn=False, x=0.5, align="center",
               effect="none", y=0.195)   # 사실 줄 (글 아래 끝 0.195 · 상자 아래 끝 + 틈 1.2% + 한 줄)
EP_CAP_Y = 0.8         # 말 자막은 아래 띠 (이보다 위면 여기로)
# 띠 그림·로고 (BR-280~BR-282 · D-470~D-474 · editor.html EP.LOGO·EP.LINK_LOGO_Y·epPics 와 같은 값·셈): layout.band·logo = 그림 이름 (켠 것만 ·
# 스타일 화면에서 끄거나 바꿈) · 띠 그림은 위·아래 띠마다 꽉 차게(가운데 기준 자름) 영상·글자 아래 · 로고는 아래 띠 가운데 (말 자막·글자 아래)
EP_PICS = core.APP_DIR / "eunpyeong"   # 앱과 함께 싣는 기본 그림 (band.png · logo.png · 출처 eunpyeong/NOTICE.md)
EP_LOGO = {"h": 0.151, "cy": 0.892}    # 로고 높이·세로 가운데 (화면 높이 비율 · 위 끝 0.8165 · 아래 끝 0.9675 · 유튜브 쇼츠 아래 글자보다 더 내리지 않음)
EP_LOGO_TOP = EP_LOGO["cy"] - EP_LOGO["h"] / 2
EP_LINK_LOGO_Y = 0.804  # 로고가 있으면 전체 강좌 줄(글 아래 끝)은 로고 바로 위 (로고 위 끝 - 틈 TXT_GAP · 내림) · 말 자막은 그 줄 위 (D-472)


def ep_layout():
    """은평 스타일 화면 구성 (EP_LAYOUT) + 스타일 화면에서 켠 띠 그림·로고 이름 (layout.band · layout.logo · 못 읽으면 기본 = 둘 다 켬)."""
    lay = dict(EP_LAYOUT)
    try:
        import style as _style
        lay.update(_style.card_pic_refs())
    except Exception:  # noqa: BLE001 — 설정을 못 읽으면 띠 색만
        pass
    return lay


def ep_pic_path(ref):
    """layout.band·logo 의 그림 이름 → 파일 (앱과 함께 실은 band.png·logo.png · 작업 폴더의 바꾼 그림) · 이상한 이름·없는 파일은 None."""
    if not isinstance(ref, str) or not ref or Path(ref).name != ref or not ref.lower().endswith(".png") or ref.startswith("."):
        return None
    try:
        import style as _style
        p = (EP_PICS if ref in _style.CARD_PIC_SHIP.values() else _style.card_pic_dir()) / ref
    except Exception:  # noqa: BLE001
        return None
    return p if p.is_file() else None


_EP_IMG = {}   # 읽은 띠 그림·로고 (경로, 고친 시각, 크기) → PIL 그림 (구간마다 다시 읽지 않게)


def _ep_img(p, mode):
    from PIL import Image
    st = p.stat()
    k = (str(p), st.st_mtime_ns, st.st_size, mode)
    if k not in _EP_IMG:
        with Image.open(p) as im:
            im.load()
            if len(_EP_IMG) > 8:
                _EP_IMG.clear()
            _EP_IMG[k] = im.convert(mode)
    return _EP_IMG[k]


def ep_band_rects(by, bh, H):
    """띠 그림을 까는 두 띠 (위 끝, 아래 끝 · 정수 화소) — 위 띠 0 ~ 영상 창 위 끝 · 아래 띠 영상 창 아래 끝 ~ 화면 끝 (빈 띠는 뺌).
    영상 창은 내보내기가 실제로 그리는 칸: 위 끝 = int(by) 를 짝수로 내림 (ffmpeg yuv420 겹치기 · 창 자르기 판도 같은 칸) · 높이 bh
    → 소수 by 로 띠를 나누면 창 아래 끝과 띠 사이에 영상도 그림도 없는 한 줄(검정·띠 색)이 남음 (D-471)."""
    wy = int(float(by)) & ~1
    we = wy + int(round(float(bh)))
    return [(a, b) for a, b in ((0, wy), (we, int(H))) if b > a]


def ep_cover(iw, ih, W, hb):
    """띠 그림을 W×hb 띠에 꽉 차게 (CSS background-size: cover · position: center 와 같음) → (그린 폭, 높이, 왼쪽, 위 · 띠 기준)."""
    s = max(W / iw, hb / ih)
    return iw * s, ih * s, (W - iw * s) / 2, (hb - ih * s) / 2


def ep_logo_box(lw, lh, W, H):
    """로고 자리 (왼쪽, 위, 폭, 높이 · 화소) — 높이 EP_LOGO h · 세로 가운데 EP_LOGO cy · 가로 가운데 · 폭은 그림 비율대로."""
    h = EP_LOGO["h"] * H
    w = h * lw / lh
    return (W - w) / 2, EP_LOGO["cy"] * H - h / 2, w, h


def ep_logo_rect(lay, W, H):
    """화면 구성(layout.logo)의 로고 자리 [왼쪽, 위, 오른쪽, 아래] (화소 · 그림 비율대로 = 내보내기 ep_bg_image 와 같은 자리) — 로고가 없거나
    그림을 못 읽으면(내보내기도 로고 없음) None · MSG 글자 자리 정리가 이 자리를 피함 (BR-283)."""
    p = ep_pic_path((lay or {}).get("logo"))
    if not p:
        return None
    try:
        lw, lh = _ep_img(p, "RGBA").size
    except Exception:  # noqa: BLE001 — 못 읽으면 내보내기에도 로고가 없음
        return None
    x, y, w, h = ep_logo_box(lw, lh, W, H)
    return [x, y, x + w, y + h]


def ep_bg_image(W, H, by, bh, bar, band=None, logo=None, log=None):
    """은평 스타일 바탕 그림 (W×H RGB · PIL): 영상 창 = 띠 색 · 위아래 띠 = 띠 그림(띠마다 꽉 차게 · 가운데 기준) · 아래 띠 가운데 로고.
    띠 그림·로고를 못 읽으면 그것만 빼고(띠 색) 기록 한 줄 → 둘 다 못 쓰면 None (예전 색 바탕 그대로) — editor.html epPics 와 같은 셈."""
    from PIL import Image
    im = Image.new("RGB", (W, H), "#" + str(bar or "#000000").lstrip("#"))
    used = False
    if band:
        try:
            src = _ep_img(band, "RGB")
            for a, b in ep_band_rects(by, bh, H):
                hb = b - a
                dw, dh, dx, dy = ep_cover(src.size[0], src.size[1], W, hb)
                # 띠보다 작아지지 않게 올림으로 줄여 가운데를 W×hb 로 잘라냄 → 띠의 모든 칸이 그림 (반올림으로 한 줄이 비지 않음)
                rw, rh = max(W, math.ceil(dw - 1e-6)), max(hb, math.ceil(dh - 1e-6))
                ox, oy = min(rw - W, max(0, round(-dx))), min(rh - hb, max(0, round(-dy)))
                im.paste(src.resize((rw, rh), Image.LANCZOS).crop((ox, oy, ox + W, oy + hb)), (0, a))
            used = True
        except Exception as e:  # noqa: BLE001 — 깨진 그림이면 띠 색 (내보내기는 멈추지 않음)
            (log or studiolog.write)(f"은평 스타일 띠 그림을 읽지 못해 띠 색으로 만들어요 · {Path(band).name} · {type(e).__name__}")
    if logo:
        try:
            src = _ep_img(logo, "RGBA")
            x, y, w, h = ep_logo_box(src.size[0], src.size[1], W, H)
            im = im.convert("RGBA")
            im.alpha_composite(src.resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS), (round(x), round(y)))
            im = im.convert("RGB")
            used = True
        except Exception as e:  # noqa: BLE001
            (log or studiolog.write)(f"은평 스타일 로고를 읽지 못해 로고 없이 만들어요 · {Path(logo).name} · {type(e).__name__}")
    return im if used else None


def _ep_bg_file(fit, W, H, tmp):
    """내보내기 구간의 은평 스타일 바탕 그림 PNG (임시 폴더 · 같은 배치면 한 번만 만듦) → 경로 · 띠 그림·로고가 없거나 못 쓰면 None."""
    band, logo = ep_pic_path(fit.get("band")), ep_pic_path(fit.get("logo"))
    if not band and not logo:
        return None
    sig = [W, H, round(float(fit["by"]), 3), round(float(fit["bh"]), 3), str(fit.get("bar"))]
    for p in (band, logo):
        st = p.stat() if p else None
        sig += [str(p), st.st_mtime_ns if st else 0, st.st_size if st else 0]
    out = Path(tmp) / f"epbg_{hashlib.sha1(json.dumps(sig).encode('utf-8')).hexdigest()[:16]}.png"
    if out.is_file():
        return out
    try:
        im = ep_bg_image(W, H, fit["by"], fit["bh"], fit.get("bar"), band, logo)
        if im is None:
            return None
        import io
        b = io.BytesIO()
        im.save(b, "PNG", compress_level=1)
        updater.write_atomic(out, b.getvalue())
    except Exception as e:  # noqa: BLE001 — PIL 이 없거나 못 쓰면 예전 색 바탕
        studiolog.write(f"은평 스타일 바탕 그림을 만들지 못해 띠 색으로 만들어요 · {type(e).__name__}: {e}")
        return None
    return out


# 은평 스타일 글꼴 (BR-310~BR-313 · D-500~D-505 · editor.html EPF·epFontOn·fmNow·emOf 와 같은 셈): 은평 스타일 화면(layout box + win)의 모든 글자
# (흰 상자 제목·사실 줄·이름표·강좌 줄·말 자막·MSG 글자)는 이 PC 의 KFA 글꼴로 고정 — 배운 스타일·타이틀 글꼴(style.font)보다 먼저 ·
# 글꼴은 앱에 싣지 않음(epfont) · 못 찾으면 지금 글꼴 그대로. 글자 폭 셈(text_box·cap_fit 등)은 ep_font_scope 안에서 그 글꼴 폭 표를 씀 (스레드마다 따로).
_FONT_CTX = threading.local()


def ep_font(wait=True):
    """지금 쓸 은평 스타일 글꼴 (epfont.find · 못 찾거나 실패하면 None = 지금 글꼴)."""
    try:
        return epfont.find(wait=wait)
    except Exception:  # noqa: BLE001 — 부가 기능: 실패해도 지금 글꼴로
        return None


def ep_font_on(seq):
    """은평 스타일 화면인지 — 쇼츠 · 위아래 박스 · 영상 창(layout.win · 은평 스타일만 켬 · editor.html epFontOn)."""
    lay = (seq or {}).get("layout") or {}
    return (seq or {}).get("format") == "shorts" and lay.get("mode") == "box" and bool(lay.get("win"))


@contextmanager
def ep_font_scope(on=True):
    """이 안에서 셈하는 글자 폭·ASS 글꼴은 은평 글꼴 (on 이고 이 PC 에서 찾았을 때 · on 이 글꼴 정보(dict)면 그것 · 아니면 지금처럼 — 바깥 범위도 끔)."""
    prev = getattr(_FONT_CTX, "f", None)
    _FONT_CTX.f = on if isinstance(on, dict) else ep_font() if on else None
    try:
        yield _FONT_CTX.f
    finally:
        _FONT_CTX.f = prev


def ep_font_now():
    """지금 범위(ep_font_scope)의 은평 글꼴 · 없으면 None."""
    return getattr(_FONT_CTX, "f", None)


def shorts_card_on():
    """스타일 화면 '쇼츠 화면 배치'가 은평 스타일인지 (못 읽으면 지금처럼 = False)."""
    try:
        import style as _style
        return _style.shorts_layout() == EP_KEY
    except Exception:  # noqa: BLE001
        return False


def ep_title_style(text):
    """은평 스타일 큰 제목 모양 — 흰 상자 안(양옆 EP_PAD) 한 줄로 들어가는 가장 큰 글자 · 상자 세로 가운데 (y = 글 아래 끝) ·
    글자 폭은 이 PC 의 은평 글꼴(KFA · 없으면 Pretendard 어림)로 셈 (BR-311)."""
    W, H = frame_size("shorts")
    room = EP_BOX["w"] * W - 2 * EP_PAD
    with ep_font_scope():
        size = next((z for z in EP_SIZES if _text_w(text, dict(EP_TITLE, size=z)) <= room), EP_SIZES[-1])
    return dict(EP_TITLE, size=size, y=round(EP_BOX["y"] + EP_BOX["h"] / 2 + TXT_LINE * size / 2 / H, 4))


def ep_box(length):
    """은평 스타일 흰 제목 상자 (도형 · 처음부터 끝까지 · plan "card" — 편집실이 다른 템플릿으로 바꾸면 지움)."""
    return {"id": _nid(), "name": "제목 상자", "x": EP_BOX["x"], "y": EP_BOX["y"], "w": EP_BOX["w"], "h": EP_BOX["h"], "color": "#FFFFFF",
            "opacity": 1.0, "radius": 0, "start": 0.0, "dur": round(float(length), 3), "full": True, "plan": "card"}


def ep_card(top, kt, length):
    """가편집 쇼츠를 은평 스타일로: 위 큰 제목(top)은 한 줄·흰 상자 안·처음부터 끝까지, 주제 줄(kt · 없어도 됨)은 그 아래 사실 줄
    (바꿔 끼우기 없음 · 끝까지) → 더할 도형 [흰 상자]."""
    top["text"] = " ".join(str(top.get("text") or "").split())
    top["start"], top["dur"] = 0.0, round(float(length), 2)
    top["style"] = ep_title_style(top["text"])
    if kt:
        kt.pop("swap", None)
        kt.update(start=0.0, dur=round(float(length), 2), style=dict(EP_FACT), under=top.get("id"))
    return [ep_box(length)]


def ep_caption(cs):
    """은평 스타일 말 자막: 아래 띠 (위쪽·가운데 자막 스타일이면 EP_CAP_Y 로 · 아래쪽이면 그대로) · 상자 없는 어두운 글씨(배운 스타일 색)는
    파란 띠 위에서 안 읽혀 흰 글씨 + 검은 테두리 (_topic_fill 과 같은 밝기 셈)."""
    c = dict(cs)
    if float(c.get("y", EP_CAP_Y)) < EP_CAP_Y:
        c["y"] = EP_CAP_Y
    if not c.get("bgOn") and _topic_fill(c)[1] == "#FFFFFF":
        c.update(fill="#FFFFFF", stroke="#000000", strokeW=max(6, int(c.get("strokeW") or 0)))
    return c


# 은평 스타일 덧붙임 (BR-245 · BR-246 · D-428~D-430 · 스타일 화면에서 고름 · 기본 끔) — editor.html EP.TAG·EP.LINK·epSeries·epSeriesFit·epExtras 와 같은 값·셈
# 시리즈 이름표: 흰 상자 위 작은 검은 상자(도형 plan "series" · 폭은 글에 맞춤) 안 흰 글씨 '1분 레슨 (n)' · 처음부터 끝까지
# 전체 강좌 줄: 아래 띠 말 자막 아래 작은 흰 글씨 '전체 강좌는 채널에서 ▶' (plan "longline") · 처음부터 끝까지
EP_TAG = dict(DEFAULT_STYLE, weight="Black", size=40, fill="#FFFFFF", stroke="#000000", strokeW=0, bgOn=False, x=0.5, align="center", effect="none")
EP_TAG_BOX = {"y": 0.052, "h": 0.036}   # 이름표 상자 (화면 비율 · 아래 끝 0.088 = 흰 상자 위 끝 0.095 위 틈)
EP_TAG_PAD = 22        # 이름표 상자 안 글 양옆 여백 (화소 · 1080 기준)
EP_LINK = dict(DEFAULT_STYLE, weight="Bold", size=36, fill="#FFFFFF", stroke="#000000", strokeW=0, bgOn=False, x=0.5, align="center", effect="none",
               y=0.865)   # 글 아래 끝 (말 자막 0.8 아래 · 두 줄 자막과도 안 겹침)
LONG_LINE = "전체 강좌는 채널에서 ▶"
_SER_NO = re.compile(r"\(\s*(\d{1,6})\s*\)\s*$")   # 이름표 끝 '(n)'


def ep_series_text(name, n):
    return f"{name} ({n})"


def ep_series_fit(t, g):
    """이름표 상자 폭을 글에 맞춤 (가운데 · 양옆 EP_TAG_PAD · 은평 글꼴 폭 · BR-311)."""
    W, _ = frame_size("shorts")
    with ep_font_scope():
        w = min(0.9, (_text_w(t["text"], t["style"]) + 2 * EP_TAG_PAD) / W)
    g["w"], g["x"] = round(w, 4), round(0.5 - w / 2, 4)


def ep_series(name, n, length):
    """시리즈 이름표 (타이틀, 도형) — seriesN: 글에 넣은 번호 (내보낼 때 정한 번호와 다르면 사용자가 고친 번호로 봄 · series_apply)."""
    _, H = frame_size("shorts")
    st = dict(EP_TAG, y=round(EP_TAG_BOX["y"] + EP_TAG_BOX["h"] / 2 + TXT_LINE * EP_TAG["size"] / 2 / H, 4))
    t = {"id": _nid(), "text": ep_series_text(name, n), "start": 0.0, "dur": round(float(length), 2), "style": st, "plan": "series", "seriesN": int(n)}
    g = {"id": _nid(), "name": "이름표 상자", "x": 0.0, "y": EP_TAG_BOX["y"], "w": 0.0, "h": EP_TAG_BOX["h"], "color": "#000000", "opacity": 1.0, "radius": 0,
         "start": 0.0, "dur": round(float(length), 3), "full": True, "plan": "series"}
    ep_series_fit(t, g)
    return t, g


def ep_longline(length, logo=False):
    """전체 강좌 줄 · logo: 아래 띠에 로고가 있으면 로고 바로 위 (EP_LINK_LOGO_Y · D-472)."""
    st = dict(EP_LINK, y=EP_LINK_LOGO_Y) if logo else dict(EP_LINK)
    return {"id": _nid(), "text": LONG_LINE, "start": 0.0, "dur": round(float(length), 2), "style": st, "plan": "longline"}


def ep_extras(length, ahead=0, logo=False):
    """스타일 화면에서 고른 덧붙임 → (더할 타이틀, 더할 도형) · 이름표 번호는 아직 정하지 않은 보기 번호(다음 번호 + ahead · 내보낼 때 정함) ·
    logo: 로고가 있는 화면 구성 (전체 강좌 줄을 로고 위로)."""
    try:
        import style as _style
        ex = _style.card_extras()
        n = _style.series_peek(ahead=ahead) if ex["series"] else 0
    except Exception:  # noqa: BLE001 — 설정을 못 읽으면 덧붙이지 않음
        return [], []
    titles, shapes = [], []
    if ex["series"]:
        t, g = ep_series(ex["seriesName"], n, length)
        titles.append(t)
        shapes.append(g)
    if ex["longLine"]:
        titles.append(ep_longline(length, logo))
    return titles, shapes


def ep_line_on():
    """스타일 화면에서 '전체 강좌는 채널에서 ▶' 줄을 골랐는지 (못 읽으면 False)."""
    try:
        import style as _style
        return bool(_style.card_extras()["longLine"])
    except Exception:  # noqa: BLE001
        return False


def ep_cap_lim(line=None, logo=False):
    """은평 스타일 말 자막 아래 끝의 한계 — 전체 강좌 줄(line · 타이틀) 위 끝 - 틈 · 로고 위 끝 - 틈 중 위쪽 (내림: 반올림으로 틈이 줄지 않게) ·
    둘 다 없으면 None — editor.html epCapClear 와 같은 셈."""
    _, H = frame_size("shorts")
    lims = []
    if line:
        lims.append(math.floor((_cap_band(line["style"], H)[0] - TXT_GAP) * 1000 + 1e-6) / 1000)
    if logo:
        lims.append(math.floor((EP_LOGO_TOP - TXT_GAP) * 1000 + 1e-6) / 1000)
    return min(lims) if lims else None


def ep_cap_clear(cs, titles, logo=False):
    """전체 강좌 줄(plan longline)·로고(logo)가 있으면 말 자막 아래 끝을 그 위 끝에서 TXT_GAP 위로 (배운 스타일 '아래' 자막 0.85 는 줄·로고와 겹침 ·
    0.8 처럼 이미 위면 그대로) — editor.html epCapClear 와 같은 셈."""
    ln = next((t for t in titles or [] if t.get("plan") == "longline"), None)
    lim = ep_cap_lim(ln, logo)
    if lim is not None and float(cs.get("y", EP_CAP_Y)) > lim:
        cs["y"] = lim
    return cs


EP_ROOM_MIN = 0.5   # 로고 화면 말 자막을 아래 띠에 맞춰 줄일 때 가장 작은 글자 배율 (세 줄 넘는 긴 자막도 읽히게 · 그보다 줄여야 하면 이만큼만)


def ep_win_bottom(seq, W, H):
    """로고가 있는 은평 스타일 화면(layout.logo · box)의 영상 창 아래 끝 (화소 · 16:9 원본 = 은평 스타일 영상 창 기준) · 아니면 None —
    말 자막(자막 1)을 이 아래 띠에 맞추는 기준 (ep_room_fit · editor.html epWinB 와 같은 셈)."""
    lay = seq.get("layout") or {}
    if seq.get("format") != "shorts" or lay.get("mode") != "box" or not lay.get("logo"):
        return None
    f = fit_rect({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080}, seq, W, H)
    return f["by"] + f["bh"]


def ep_room_fit(text, st, W, H, wb):
    """로고 화면 말 자막이 아래 띠(영상 창 아래 끝 wb ~ 글 아래 끝 y)에 들게 하는 글자 크기 배율 (1 = 그대로 · wb None 이면 1) —
    강좌 줄 + 로고면 글 아래 끝 0.768 이라 두 줄 자막(72)이 창을 덮음 → 아래 띠에 안 들어가는 여러 줄 자막만 줄임 (한 줄은 그대로 · BR-283 · I-472) ·
    한 줄도 안 들어가는 자리(창 안으로 옮긴 자막)는 그대로 — editor.html epRoomFit 과 같은 셈."""
    size, stroke = float(st.get("size") or 0), float(st.get("strokeW") or 0)
    if wb is None or size <= 0:
        return 1.0
    room = float(st.get("y", 0.85)) * H - wb
    if room < TXT_LINE * size + 2 * stroke or cap_fit(text, st, W) < 1:   # 한 줄(글자만 조금 줄여 한 줄)이면 이미 들어감
        return 1.0
    for i in range(100, round(EP_ROOM_MIN * 100), -1):   # 가장 큰 배율부터 (줄이면 한 줄에 더 들어가 줄 수도 다시 셈)
        f = size * i / 100
        if cap_wrap_lines(text, dict(st, size=f), W) * TXT_LINE * f + 2 * stroke <= room:
            return i / 100
    return EP_ROOM_MIN


# ---------- 풋살사관학교 스킨 쇼츠 (BR-320~BR-325 · D-520~D-528) ----------
# 소유자 채널 스킨(9:16 투명 창 그림 · skins/academy.png · 바꾼 스킨은 작업 폴더 styles/설정/스킨/)을 화면 전체에 얹음 — 영상 트랙(V1~V3 · 박스 바탕) 위,
# 글자·도형·전술 그림(ASS) 아래 · 16:9 원본은 스킨의 투명한 영상 창과 그 위아래 찢어진 종이 홈(비치는 줄 · style.skin_cover_of)까지 꽉 채우게
# (그 위아래로 SKIN_BLEED 화소 더 · D-525) · 위 칠판 띠에 흰 둥근 상자 안 주황 큰 제목 +
# 그 아래 흰 주제 줄 · 말 자막은 창 아래 끝 바로 위. 스타일 화면 '쇼츠 화면 배치'에서 고름(기본은 지금처럼) · 은평 스타일 것(KFA 글꼴 고정·띠 그림·로고·
# 이름표·강좌 줄·영상 창 자르기 win)은 쓰지 않음. 편집실 템플릿 '풋살사관학교 스킨'(editor.html SKIN · skinCard)과 같은 값·셈 — 한쪽을 고치면 다른 쪽도.
SKIN_KEY = "academy"
SKIN_BLEED = 4         # 원본이 덮을 곳(창 + 비치는 종이 홈 · style.skin_cover_of) 위아래로 더 덮는 화소 (1920 높이 기준 · 반올림 뒤에도 · D-525)
SKIN_PAD = 14          # 흰 상자 안 글 양옆 여백 (화소 · 1080 기준 · 소유자 쇼츠 14·13화소 · D-532)
SKIN_SIZES = (96, 88, 80, 72, 64, 56, 48)        # 큰 제목 글자 크기 (한 줄로 화면 폭 90% - 양옆 여백 안에 들어가는 가장 큰 것 · 48 에도 안 들어가면 더 작게 · D-531)
SKIN_TITLE_FLOOR = 24  # 손으로 고친 아주 긴 큰 제목도 한 줄로 — 48 에도 안 들어가면 들어가는 크기까지 줄임 (이보다 작게는 안 줄이고 그 크기로 · D-531)
SKIN_AUTO_MIN = 80     # 가편집·MSG 가 만드는 큰 제목은 한 줄로 이 크기 이상 (안 들어가면 앞 구절·포인트 말·영상 주제로 · skin_headline · D-531)
SKIN_TOPIC_SIZES = (96, 88, 80, 72, 64, 56, 48)  # 주제 줄 글자 크기 (한 줄로 화면 폭 90% 안 가장 큰 것 · 큰 제목보다 크지 않게 · 소유자 쇼츠 약 97 · D-532)
SKIN_BOX = {"y": 0.0922, "h": 0.0625}            # 흰 상자 위 끝·높이 (화면 비율 · 소유자 쇼츠에서 잰 177~297화소)
SKIN_BOX_MAX = 0.9     # 흰 상자 가장 넓게 (화면 폭 비율)
SKIN_RADIUS = 17       # 흰 상자 모서리 둥글기 (% · 짧은 변 절반 기준 → 높이 120화소 상자에서 10화소)
# hug "skin": 이 글은 Pretendard Black 실제 글자 폭 표(SKIN_HUG)로 한 줄인지 셈하고 한 줄이면 줄을 바꾸지 않음 (ASS \q2 · 미리보기 nowrap · D-531 · D-533)
SKIN_TITLE = dict(DEFAULT_STYLE, weight="Black", size=96, fill="#CE4F2F", stroke="#CE4F2F", strokeW=0, bgOn=False, x=0.5, align="center",
                  effect="none", hug="skin")
SKIN_TOPIC = dict(DEFAULT_STYLE, weight="Black", size=96, fill="#FFFFFF", stroke="#000000", strokeW=0, bgOn=False, x=0.5, align="center",
                  effect="none")
SKIN_TOPIC_CY = 0.179  # 주제 줄 가운데 (화면 비율 · 소유자 쇼츠 글자 303~386화소) — 글 상자 아래 끝은 영상 창 위 끝 - SKIN_TOPIC_GAP 위까지
SKIN_TOPIC_GAP = 0.008    # 주제 줄 글 상자 아래 끝 ~ 영상 창 위 끝 틈 (96 이면 글자 301~386화소 = 소유자 쇼츠 · 흰 상자 아래 5화소 · D-532)
SKIN_CAP_UP = 0.046    # 말 자막 아래 끝 = 영상 창 아래 끝 - 이만큼 (소유자 쇼츠 글자 아래 끝 1362화소 · 배운 자막 자리와 상관없이 · 여러 줄은 창 안에서 위로 · D-532)
# 말 자막 모양 — 소유자 쇼츠 '애들 목소리가 다 똑같아...'에서 잰 값 (흰 Pretendard Black · 글자 크기 96 · 검은 테두리 10 · 상자 없음 · D-529 · D-532) ·
# 나타나기(effect)는 배운 스타일 그대로 (가편집 쇼츠 pop · MSG none·karaoke) · 한 줄 셈은 실제 글자 폭 표(hug · 화면 폭 95% · D-533) · 화면보다 긴 한 줄은
# cap_fit 이 글자만 줄여 한 줄로 · editor.html SKIN.CAP 과 같은 값 — 한쪽을 고치면 다른 쪽도
SKIN_CAP = {"weight": "Black", "size": 96, "fill": "#FFFFFF", "stroke": "#000000", "strokeW": 10, "bgOn": False, "hug": "skin"}
SKIN_CAP_ROOM = 0.95   # 스킨 말 자막 한 줄 폭 한도 (화면 폭 비율 · 테두리 포함 · 소유자 줄 1000화소 = 93% · 다른 자막은 90% · D-533)
# 흰 상자 폭·스킨 말 자막 한 줄 셈에 쓰는 Pretendard Black 실제 글자 폭 (글자 크기 1 기준 · fonts/Pretendard-Black.otf 의 글자 나아감 · 천분의 일 · D-526 · D-533) —
# 큰 제목 글자 크기 고르기는 넉넉한 어림(_text_w · 한글 0.95)을 그대로 씀 · 표에 없는 글자(이모지 등)는 그 어림 · editor.html SKIN_HUG 와 같은 표
_SKIN_HUG_ASCII = "218 340 375 642 646 1024 674 195 423 423 584 671 307 460 301 396 703 488 626 654 679 647 663 592 668 663 301 301 671 671 671 577 871 767 647 744 711 603 567 749 728 281 568 690 554 903 704 771 636 772 646 646 664 702 767 1055 734 737 671 423 396 423 483 477 482 572 625 586 625 589 388 628 625 283 283 586 283 910 624 605 627 627 417 567 391 627 585 847 576 585 572 423 389 423 671"   # ' '(0x20) ~ '~'(0x7E)
SKIN_HUG = {"hangul": 0.864, "em": dict({chr(32 + i): int(v) / 1000 for i, v in enumerate(_SKIN_HUG_ASCII.split())},
                                        **{"·": 0.301, "…": 0.902, "‘": 0.291, "’": 0.291, "“": 0.516, "”": 0.516})}
SKIN_SIZES_EXPORT = ((1080, 1920), (720, 1280), (2160, 3840))   # 영상 창을 덮는지 확인할 내보내기 크기 (PRESETS 쇼츠 · 1080 이 기준)


def shorts_skin_on():
    """스타일 화면 '쇼츠 화면 배치'가 풋살사관학교 스킨인지 (못 읽으면 지금처럼 = False)."""
    try:
        import style as _style
        return _style.shorts_layout() == SKIN_KEY
    except Exception:  # noqa: BLE001
        return False


def skin_path(ref):
    """layout.skin 의 스킨 이름 → 파일 (style.skin_path · 이상한 이름·없는 파일은 None)."""
    try:
        import style as _style
        return _style.skin_path(ref)
    except Exception:  # noqa: BLE001
        return None


def skin_window(ref):
    """스킨 이름 → 영상 창 {"top", "bottom"} (화면 높이 비율 · style.skin_window · 파일을 읽은 시각·크기로 기억) · 못 읽으면 None."""
    p = skin_path(ref)
    if not p:
        return None
    try:
        import style as _style
        return _style.skin_window(p)
    except Exception:  # noqa: BLE001
        return None


def _skin_src(info=None):
    """편집본 원본 크기 (가로, 세로) — info(media_info · 편집본 info)의 width·height · 모르면 16:9 (1920, 1080)."""
    try:
        sw, sh = float((info or {}).get("width") or 0), float((info or {}).get("height") or 0)
    except (TypeError, ValueError):
        sw = sh = 0.0
    return (sw, sh) if sw > 0 and sh > 0 and math.isfinite(sw) and math.isfinite(sh) else (1920, 1080)


def _skin_covers(zoom, vpos, top, bottom, sw=1920, sh=1080):
    """화면 구성(zoom·vpos)의 원본(sw×sh) 칸이 덮을 곳 [top, bottom) 을 덮는지 — 내보내기(_fit 짝수 크기 · yuv420 겹치기는 위 끝을 짝수로 내림)는
    위아래 SKIN_BLEED(화면 높이에 맞춰 · 1920 에서 4화소 · 720 에서 2 · 4K 에서 8)까지 내보내기 크기마다, 미리보기(fit_rect · 반올림 없음)는 그 곳까지 (D-525)."""
    lay = {"mode": "box", "bar": "#000000", "zoom": zoom, "vpos": vpos, "cropTop": 0.0, "cropBottom": 0.0}
    seq, it, md = {"format": "shorts", "layout": lay}, {"track": "V1", "fit": "auto"}, {"kind": "video", "w": sw, "h": sh}
    for W, H in SKIN_SIZES_EXPORT:
        bl = math.floor(SKIN_BLEED * H / 1920 + 1e-9)
        f = _fit(it, md, seq, W, H)
        y0 = int(f["by"]) & ~1
        if y0 > max(0.0, top * H - bl) + 1e-6 or y0 + f["bh"] < min(float(H), bottom * H + bl) - 1e-6:
            return False
        r = fit_rect(it, md, seq, W, H)
        if r["by"] > top * H + 1e-6 or r["by"] + r["bh"] < bottom * H - 1e-6:
            return False
    return True


def skin_fit(top, bottom, sw=1920, sh=1080):
    """덮을 곳 [top, bottom) (화면 높이 비율 · 스킨의 coverTop·coverBottom) · 원본 sw×sh → (zoom, vpos) — 원본이 [위 - SKIN_BLEED, 아래 + SKIN_BLEED] 를
    꼭 덮게 하는 가장 작은 확대 (원본 비율마다 · D-530): 높이 ch = 덮을 곳 높이 + 2·BLEED 화소 · zoom = ch / (1080·sh/sw) (16:9 는 607.5 로 나눔 = 1.8436 ·
    4:3 은 약 1.38 · 세로 9:16 은 1 = 화면 가득) · vpos = (위 - BLEED) / (1920 - ch) (넷째 자리) — 확대 1 로도 덮을 곳보다 높은 원본(세로·4:5)은 덮을 곳
    가운데에 맞춤 · 반올림 뒤에도 덮는지 확인해 모자라면 1화소씩 더 덮음 (_skin_covers)."""
    W, H = frame_size("shorts")
    t, b = float(top) * H, float(bottom) * H
    base = W * sh / sw   # 확대 1 일 때 원본 높이 (16:9: 1080 × 1080/1920 = 607.5)
    zoom = vpos = None
    for extra in range(0, 33):
        bl = SKIN_BLEED + extra
        ch = (b - t) + 2 * bl
        zoom = round(max(1.0, ch / base), 4)
        if ch / base >= 1.0:
            vpos = round(min(1.0, max(0.0, (t - bl) / (H - ch))), 4) if H - ch > 1e-6 else 0.5
        else:   # 확대 1 로도 덮을 곳보다 높음 → 원본 가운데를 덮을 곳 가운데에 (화면보다 높으면 위아래를 똑같이 자름)
            vpos = round(min(1.0, max(0.0, ((t + b) / 2 - base / 2) / (H - base))), 4) if H - base > 1e-6 else 0.5
        if _skin_covers(zoom, vpos, top, bottom, sw, sh):
            break
    return zoom, vpos


def skin_layout(ref=None, info=None):
    """풋살사관학교 스킨 화면 구성 {"mode": "box", "bar", "zoom", "vpos", "cropTop", "cropBottom", "skin"} — ref 없으면 스타일 화면에서 고른 스킨 ·
    info: 편집본 원본(media_info · width·height) — 확대는 그 원본 비율로 창을 덮는 만큼 (모르면 16:9 · D-530) · 스킨을 못 읽으면 None (부른 쪽은 지금처럼 만듦)."""
    try:
        import style as _style
        ref = ref or _style.skin_ref()
    except Exception:  # noqa: BLE001
        return None
    win = skin_window(ref)
    if not win:
        return None
    zoom, vpos = skin_fit(win["coverTop"], win["coverBottom"], *_skin_src(info))   # 영상은 창보다 넓게 — 찢어진 종이 홈으로 비치는 곳까지 (D-525)
    return {"mode": "box", "bar": "#000000", "zoom": zoom, "vpos": vpos, "cropTop": 0.0, "cropBottom": 0.0, "skin": ref}


def skin_info(info=None):
    """편집실 템플릿 '풋살사관학교 스킨'이 쓸 값 (/api/edit/open epx.skin) {"ref", "top", "bottom", "layout"} · info: 그 영상(편집본 info) · 스킨을 못 읽으면 None."""
    lay = skin_layout(info=info)
    if not lay:
        return None
    win = skin_window(lay["skin"])
    return {"ref": lay["skin"], "top": win["top"], "bottom": win["bottom"], "layout": lay}   # (창은 반올림하지 않음 · 가편집 셈과 같은 값)


def skin_on(seq):
    """편집본에 스킨을 얹는지 — 쇼츠 · 화면 구성에 스킨 이름 (그림 파일이 있는지는 skin_path 로 따로)."""
    return (seq or {}).get("format") == "shorts" and bool(((seq or {}).get("layout") or {}).get("skin"))


def skin_title_style(text):
    """스킨 큰 제목 모양 — 한 줄로 (화면 폭 90% - 양옆 SKIN_PAD) 안에 들어가는 가장 큰 글자 (SKIN_SIZES · 48 에도 안 들어가면 들어가는 크기까지 줄임 ·
    SKIN_TITLE_FLOOR 까지 · 줄을 바꾸지 않음 hug · D-531) · 흰 상자 세로 가운데 (y = 글 아래 끝) · Pretendard Black 폭 (은평 글꼴 고정은 쓰지 않음 ·
    editor.html skinTitleStyle). 예전: 48 에서 멈춰 긴 글은 libass 가 두 줄로 바꿔 한 줄 높이 흰 상자 위아래로 넘침 (검토)."""
    W, H = frame_size("shorts")
    room = SKIN_BOX_MAX * W - 2 * SKIN_PAD
    with ep_font_scope(False):
        size = next((z for z in SKIN_SIZES if _text_w(text, dict(SKIN_TITLE, size=z)) <= room), None)
        if size is None:
            per = _text_w(text, dict(SKIN_TITLE, size=1, strokeW=0))
            size = max(SKIN_TITLE_FLOOR, min(SKIN_SIZES[-1], math.floor(room / per))) if per > 0 else SKIN_SIZES[-1]
    return dict(SKIN_TITLE, size=size, y=round(SKIN_BOX["y"] + SKIN_BOX["h"] / 2 + TXT_LINE * size / 2 / H, 4))


def _cut_words(text, lim):
    """글 → 낱말 경계에서 lim 글자(띄어쓰기·문장 부호 빼고)까지 · 끝 쉼표·마침표·물음표 뗌 (_hook_cut 과 달리 묻는 말도 자름)."""
    out = []
    for w in str(text or "").split():
        if out and len(_norm(" ".join(out + [w]))) > lim:
            break
        out.append(w)
    return re.sub(r"[.,…?!\s]+$", "", " ".join(out))


def skin_headline(text, alts=()):
    """가편집·MSG·끝까지 자동으로가 만드는 스킨 큰 제목 글 (BR-321 · D-531) — 흰 상자 한 줄에 SKIN_AUTO_MIN(80) 이상으로 들어가는 첫 글:
    그대로(여러 줄은 한 줄로) → 앞 구절('…, …' 의 앞 · 4글자 이상) → alts(이 쇼츠 포인트 말 · 영상 주제 …) → 낱말 경계에서 자른 글. 예전: 묻는 말 큰 제목
    ('패스하고 그 자리에 서 있으면 왜 안될까요?')이 48 로 줄어 소유자 96 의 절반 · 그 아래 주제 줄(88)보다 작았음 (검토). 묻는 말은 말 자막에 그대로 나옴."""
    t = " ".join(str(text or "").split())
    if not t:
        return t

    def ok(x):
        return bool(x) and skin_title_style(x)["size"] >= SKIN_AUTO_MIN
    if ok(t):
        return t
    head = re.split(r"[,.?!…]", t)[0].strip()
    cands = ([head] if head != t and len(_norm(head)) >= 4 else []) + [" ".join(str(a).split()) for a in alts if a and str(a).strip()]
    for c in cands:
        if ok(c):
            return c
    for lim in range(len(_norm(t)) - 1, 3, -1):
        c = _cut_words(t, lim)
        if ok(c) and len(_norm(c)) >= 4:
            return c
    return max([t] + cands, key=lambda x: skin_title_style(x)["size"])


def skin_hug_w(text, st):
    """흰 상자가 감쌀 큰 제목 한 줄 폭 (화소 · Pretendard Black 실제 글자 폭 표 SKIN_HUG · 테두리 포함 · editor.html textW(…, SKIN_HUG)) —
    넉넉한 어림(_text_w)은 실제보다 10%쯤 넓어 상자가 글보다 훨씬 넓었음 (D-526)."""
    black = st.get("weight") == "Black"
    return max([sum(_em(ch, black, SKIN_HUG) for ch in ln) for ln in str(text or "").replace("\r", "").split("\n")] or [0.0]) * float(st.get("size") or 0) \
        + 2 * float(st.get("strokeW") or 0)


def skin_box_fit(t, g):
    """흰 상자 폭을 큰 제목(t)에 맞춤 (가운데 · 양옆 SKIN_PAD · 가장 넓게 SKIN_BOX_MAX · 글 폭은 skin_hug_w · editor.html skinBoxFit)."""
    W, _ = frame_size("shorts")
    w = min(SKIN_BOX_MAX, (skin_hug_w(t["text"], t["style"]) + 2 * SKIN_PAD) / W)
    g["w"], g["x"] = round(w, 4), round(0.5 - w / 2, 4)


def skin_box(top, length):
    """스킨 흰 제목 상자 (도형 · 둥근 모서리 · 처음부터 끝까지 · plan "card" — 편집실이 다른 템플릿으로 바꾸면 지움 · 폭은 큰 제목(top)에 맞춤)."""
    g = {"id": _nid(), "name": "제목 상자", "x": 0.05, "y": SKIN_BOX["y"], "w": SKIN_BOX_MAX, "h": SKIN_BOX["h"], "color": "#FFFFFF",
         "opacity": 1.0, "radius": SKIN_RADIUS, "start": 0.0, "dur": round(float(length), 3), "full": True, "plan": "card"}
    skin_box_fit(top, g)
    return g


def skin_topic_style(text, top, most=None):
    """스킨 주제 줄 모양 — 한 줄로 화면 폭 90% 안에 들어가는 가장 큰 글자 (most: 큰 제목 글자 크기 — 그보다 크지 않게 · D-531) · 가운데 SKIN_TOPIC_CY ·
    글 상자 아래 끝은 영상 창 위 끝(top) - SKIN_TOPIC_GAP 위까지 (넘으면 그만큼 위로 · 내림) — editor.html skinTopicStyle 과 같은 셈."""
    W, H = frame_size("shorts")
    sizes = [z for z in SKIN_TOPIC_SIZES if most is None or z <= float(most)] or [SKIN_TOPIC_SIZES[-1]]
    with ep_font_scope(False):
        size = next((z for z in sizes if _text_w(text, dict(SKIN_TOPIC, size=z)) <= SKIN_BOX_MAX * W), sizes[-1])
    y = SKIN_TOPIC_CY + TXT_LINE * size / 2 / H
    lim = math.floor((float(top) - SKIN_TOPIC_GAP) * 10000 + 1e-6) / 10000
    return dict(SKIN_TOPIC, size=size, y=round(min(y, lim), 4))


def skin_cap_y(bottom):
    """말 자막 아래 끝 = 영상 창 아래 끝(bottom) - SKIN_CAP_UP (넷째 자리 · editor.html skinCapY)."""
    return round(float(bottom) - SKIN_CAP_UP, 4)


def skin_caption(cs, bottom):
    """스킨 말 자막: 소유자 쇼츠 모양(SKIN_CAP · 흰 Black 96 · 검은 테두리 10 · 상자 없음 · 실제 글자 폭으로 한 줄 셈 hug) · 나타나기는 배운 스타일 그대로 ·
    아래 끝은 영상 창 아래 끝 바로 위 (배운 자막 자리와 상관없이 · editor.html skinCard 와 같은 값)."""
    c = dict(cs)
    c.update(SKIN_CAP, y=skin_cap_y(bottom))
    return c


def skin_card(top, kt, length, win, alts=()):
    """가편집 쇼츠를 풋살사관학교 스킨으로: 위 큰 제목(top)은 한 줄·흰 상자 안·처음부터 끝까지 (80 밑으로 줄어야 하는 긴 글은 skin_headline 이
    앞 구절·alts(포인트 말·영상 주제)로), 주제 줄(kt · 없어도 됨)은 그 아래 흰 줄 (큰 제목보다 크지 않게 · 큰 제목과 같은 글이면 뺌 · 바꿔 끼우기 없음 · 끝까지) ·
    win: 영상 창 {top, bottom} → (더할 도형 [흰 상자], 주제 줄 — 뺐으면 None)."""
    top["text"] = skin_headline(top.get("text"), alts)
    top["start"], top["dur"] = 0.0, round(float(length), 2)
    top["style"] = skin_title_style(top["text"])
    if kt:
        kt.pop("swap", None)
        kt["text"] = " ".join(str(kt.get("text") or "").split())
        if _norm(kt["text"]) == _norm(top["text"]):
            kt = None
        else:
            kt.update(start=0.0, dur=round(float(length), 2), style=skin_topic_style(kt["text"], win["top"], top["style"]["size"]), under=top.get("id"))
    return [skin_box(top, length)], kt


def skin_bands(lay, W, H):
    """스킨 화면의 위·아래 띠 [[왼, 위, 오른, 아래], …] (화소 · 영상 창 밖) — MSG 글자 자리 정리가 피함 (BR-323) · 스킨을 못 읽으면 []."""
    win = skin_window((lay or {}).get("skin"))
    if not win:
        return []
    return [[0.0, 0.0, float(W), win["top"] * H], [0.0, win["bottom"] * H, float(W), float(H)]]


# ---------- 스킨 화면 좌우 얼굴 따라가기 (BR-326 · D-530) ----------
# 스킨 창은 16:9 원본 가로의 가운데 54% 만 보여(확대 1.84) 가운데에서 벗어난 감독님(원본 0.13·0.85)이 4~8초씩 창 밖이었음 (검토) — 기본 배치(확대 1)는 다 보임.
# 원본 V1 클립(원본이 이어진 묶음)마다 0.5초 칸으로 얼굴을 보고, 한 자리로 다 보이면 그 자리(reframe) · 아니면 얼굴을 따라 천천히 움직이는 키프레임(rfk ·
# 원본 시각 · 직선 · 장면이 바뀌면 바로) · 확대 컷 기준점은 창 좌표로 (원본 좌표를 그대로 쓰면 창 밖 얼굴을 기준으로 키웠음). 얼굴을 못 보면 가운데(0.5) 그대로.
SKIN_FOLLOW_FPS = 2       # 원본을 1초에 이만큼 봄 (한 구간을 ffmpeg 하나로 차례로 풀어 · 30초 구간 약 3초)
SKIN_FOLLOW_PAD = 0.03    # 얼굴 상자 양옆이 창 가장자리에서 이만큼(원본 폭 비율) 안쪽
SKIN_FOLLOW_MIN_H = 0.06  # 이보다 작은 얼굴(멀리 선 선수)은 안 따라감 (화면 높이 비율)
SKIN_FOLLOW_JUMP = 0.15   # 이웃 칸 얼굴 가운데가 이만큼(원본 폭 비율) 넘게 뛰면 장면이 바뀐 것 → 미끄러지지 않고 바로 옮김
SKIN_FOLLOW_SMOOTH = 2    # 따라갈 때 앞뒤 이만큼 칸(±1초)의 평균 (얼굴 찾기 흔들림을 걸러 천천히)
SKIN_FOLLOW_TOL = 0.015   # 키프레임 줄이기 허용 (reframe 값 · 원본 폭 0.7% 쯤)
SKIN_FOLLOW_SPAN = 240.0  # 한 번에 볼 원본 길이 한도 (초 · 넘는 묶음은 가운데 그대로)
SKIN_CUT_SCENE = 0.2      # 장면이 바뀐 두 칸 사이에서 이만큼 넘게 그림이 바뀐 장면(ffmpeg scene 값)이 있으면 그 장면에서 바로 옮김 (D-537)
_SKIN_FACES = {}          # (영상 경로, 크기, 고친 시각) → {원본 시각(0.5초 칸): [[x, y, w, h], …] 큰 것부터 | None}
_SKIN_CUTS = {}           # (영상 경로, 크기, 고친 시각, a, b) → 바뀐 장면 원본 시각 | None
_SKIN_FACES_LOCK = threading.Lock()


def skin_view(lay, W=1080):
    """스킨(박스) 화면에서 V1 원본 가로 중 보이는 몫 v (0~1 · 1 = 다 보임) — _fit 의 짝수 크기와 같은 셈 (원본 폭 → W·zoom)."""
    zw = _even(W * max(1.0, float((lay or {}).get("zoom") or 1)))
    return min(1.0, W / zw)


def rf_at(it, m):
    """클립의 화면 좌우(reframe 0~1) — 얼굴 따라가기 키프레임(rfk · 원본 시각 m)이 있으면 그 값, 없으면 reframe (editor.html rfAt)."""
    k = it.get("rfk")
    if isinstance(k, list) and k:
        return float(kf_at({"v": 0.5, "k": sorted(k, key=lambda x: x["t"])}, m))
    v = it.get("reframe")
    return float(0.5 if v is None else v)


def _skin_grid(a, b):
    """원본 [a, b] 를 덮는 0.5초 칸 (양 끝 칸 바깥 하나까지 · 클립 끝 0.5초도 계획 안)."""
    step = 1.0 / SKIN_FOLLOW_FPS
    return [round(k * step, 2) for k in range(max(0, math.floor(a / step + 1e-6)), math.ceil(b / step - 1e-6) + 1)]


def skin_faces(name, spans):
    """원본 구간들 [(a, b)] 의 0.5초 칸마다 얼굴 상자 {시각: [[x, y, w, h], …] (큰 것부터) | None} — 아직 못 본 칸만 이어진 칸끼리 ffmpeg 하나로 차례로 풀어
    (FACE_W 크기 · 칸마다 다시 찾아가는 것보다 3배쯤 빠름) 4장씩 얼굴 찾기 · 영상마다 기억 · 얼굴 모델이 없거나(내려받지 않음) 못 보면 None."""
    p = core.VIDEOS / str(name)
    want = sorted({t for a, b in spans for t in _skin_grid(float(a), float(b))})
    try:
        stt = p.stat()
    except OSError:
        return {t: None for t in want}
    key = (str(p), stt.st_size, stt.st_mtime_ns)
    with _SKIN_FACES_LOCK:
        cache = _SKIN_FACES.setdefault(key, {})
        todo = [t for t in want if t not in cache]
    if todo:
        step = 1.0 / SKIN_FOLLOW_FPS
        groups = []
        for t in todo:
            if groups and t - groups[-1][-1] <= step + 1e-6:
                groups[-1].append(t)
            else:
                groups.append([t])
        with idle.using():  # 가편집은 바로 답하는 요청 — 쉬는 동안 내려놓기가 얼굴 모델을 지우지 않게 (face_anchors 와 같음)
            if not _face_ready(name):
                return {t: cache.get(t) for t in want}
            import face
            for g in groups:
                with tempfile.TemporaryDirectory(prefix="skin_faces_") as d:
                    # round=up: 칸 t 의 그림 = t 또는 바로 앞 장면 (기본 near 는 t+0.23초까지의 장면이라 바뀐 장면을 0.25초 먼저 봤음 · D-537)
                    r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{g[0]:.3f}", "-t", f"{g[-1] - g[0] + step / 2:.3f}", "-i", str(p),
                                  "-vf", f"fps={SKIN_FOLLOW_FPS}:round=up,scale='min({FACE_W},iw)':-2", "-q:v", "3", str(Path(d) / "f_%05d.jpg")])
                    files = sorted(Path(d).glob("f_*.jpg")) if r.returncode == 0 else []

                    def one(f):
                        try:
                            fs = face.faces(f)
                        except Exception:  # noqa: BLE001 — 장면 하나를 못 봐도 계속
                            fs = None
                        return None if fs is None else [list(x["box"]) for x in fs]
                    with ThreadPoolExecutor(4, thread_name_prefix="model-face") as ex:
                        res = list(ex.map(one, files[:len(g)]))
                with _SKIN_FACES_LOCK:
                    for i, t in enumerate(g):
                        cache[t] = res[i] if i < len(res) else None
    with _SKIN_FACES_LOCK:
        return {t: cache.get(t) for t in want}


def skin_cut(name, a, b):
    """원본 (a, b] 안에서 그림이 확 바뀐 장면(컷)의 원본 시각 — 얼굴이 뛴 두 칸 사이를 작게 풀어 ffmpeg scene 값이 가장 큰 장면 (SKIN_CUT_SCENE 넘을 때) ·
    없거나(같은 장면에서 다른 사람이 커짐) 못 풀면 None · 영상마다 기억."""
    p = core.VIDEOS / str(name)
    try:
        stt = p.stat()
    except OSError:
        return None
    key = (str(p), stt.st_size, stt.st_mtime_ns, round(float(a), 3), round(float(b), 3))
    with _SKIN_FACES_LOCK:
        if key in _SKIN_CUTS:
            return _SKIN_CUTS[key]
    r = core.run([core.ffmpeg(), "-v", "error", "-ss", f"{float(a):.3f}", "-t", f"{float(b) - float(a) + 0.02:.3f}", "-i", str(p), "-an", "-sn",
                  "-vf", "scale=160:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score:file=-", "-f", "null", "-"])
    best, cur = None, None
    for ln in (r.stdout or "").splitlines() if r.returncode == 0 else ():
        if ln.startswith("frame:") and "pts_time:" in ln:
            try:
                cur = float(ln.split("pts_time:")[1].split()[0])
            except ValueError:
                cur = None
        elif "lavfi.scene_score=" in ln and cur is not None:
            try:
                s = float(ln.split("=", 1)[1])
            except ValueError:
                continue
            t = float(a) + cur
            if cur > 1e-6 and s >= SKIN_CUT_SCENE and float(a) < t <= float(b) + 0.01 and (best is None or s > best[1]):
                best = (round(t, 3), s)
    out = best[0] if best else None
    with _SKIN_FACES_LOCK:
        _SKIN_CUTS[key] = out
    return out


def _rdp(pts, tol):
    """꺾은선 [(t, v)] 줄이기 (Ramer–Douglas–Peucker · 처음·끝은 그대로)."""
    if len(pts) < 3:
        return list(pts)
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    far, fi = -1.0, 0
    for i in range(1, len(pts) - 1):
        t, v = pts[i]
        d = abs(v - (v0 + (v1 - v0) * (t - t0) / ((t1 - t0) or 1e-9)))
        if d > far:
            far, fi = d, i
    if far <= tol:
        return [pts[0], pts[-1]]
    return _rdp(pts[:fi + 1], tol)[:-1] + _rdp(pts[fi:], tol)


def skin_follow(samples, v, cut=None):
    """얼굴 따라가기 계획 (BR-326) — samples [(원본 시각, 얼굴 상자들 | None)] (시각 차례) · v: 창에 보이는 원본 가로 몫 →
    (rf, keys): 한 자리로 모든 칸의 얼굴이 창 안에 들면 (그 자리, None) · 아니면 (가운데 값, [(시각, rf)] 직선 키프레임 — 장면 안은 앞뒤 1초 평균으로 천천히 ·
    얼굴이 SKIN_FOLLOW_JUMP 넘게 뛴 곳은 cut(a, b) 가 두 칸 사이 컷 시각을 주면 그 컷에서 바로, 아니면 두 칸 사이를 미끄러지듯) ·
    얼굴을 한 번도 못 보거나 다 보이는 화면(v ≥ 1)이면 (None, None).
    얼굴은 가장 큰 것 — 앞 칸 얼굴 가까이(0.1)에 절반 넘는 크기의 얼굴이 있으면 그것 (두 사람이 번갈아 커져도 화면이 튀지 않게) ·
    한 칸만 딴 데로 뛰고 앞뒤 칸이 같은 자리면 그 칸은 잘못 본 것(지나가는 사람·얼굴 아닌 것) → 앞 칸 자리 (0.5초 동안 창이 갔다 오지 않게 · D-537) ·
    맨 앞·끝 칸은 안쪽 칸과 사이에 컷이 없을 때만 (컷 너머 다음 장면이면 진짜)."""
    if v >= 1 - 1e-6 or not samples:
        return None, None
    span = 1 - v
    area = lambda f: float(f[2]) * float(f[3])  # noqa: E731
    cx = lambda f: float(f[0]) + float(f[2]) / 2  # noqa: E731
    pick, cands, prev = [], [], None
    for t, fs in samples:
        fs = [f for f in fs or () if float(f[3]) >= SKIN_FOLLOW_MIN_H]
        cands.append(fs)
        if not fs:
            pick.append(None)
            continue
        f = max(fs, key=area)
        if prev is not None:
            near = [g for g in fs if area(g) >= 0.5 * area(f) and abs(cx(g) - prev) <= 0.1]
            if near:
                f = min(near, key=lambda g: abs(cx(g) - prev))
        prev = cx(f)
        pick.append((cx(f), float(f[0]), float(f[0]) + float(f[2])))
    if not any(pick):
        return None, None
    seen = [i for i, p in enumerate(pick) if p]   # 한 칸만 뛴 것 걸러내기 (얼굴을 본 칸끼리 · 차례로 · 양 끝은 안쪽 두 칸이 같은 자리일 때)
    for j, i in enumerate(seen):
        nb = [seen[j - 1], seen[j + 1]] if 0 < j < len(seen) - 1 else [seen[1], seen[2]] if j == 0 and len(seen) > 2 \
            else [seen[-2], seen[-3]] if j == len(seen) - 1 and len(seen) > 2 else None
        if not nb:
            continue
        a, b = pick[nb[0]], pick[nb[1]]
        if abs(pick[i][0] - a[0]) > SKIN_FOLLOW_JUMP and abs(b[0] - a[0]) <= SKIN_FOLLOW_JUMP:
            edge = (float(samples[i][0]), float(samples[nb[0]][0])) if j == 0 else (float(samples[nb[0]][0]), float(samples[i][0])) if 0 < j == len(seen) - 1 else None
            if edge and cut and cut(*edge) is not None:   # 묶음 맨 앞·끝 칸이 컷 너머 다음 장면이면 진짜 (클립이 새 장면으로 이어짐)
                continue
            near = [g for g in cands[i] if abs(cx(g) - a[0]) <= 0.1]
            g = min(near, key=lambda g: abs(cx(g) - a[0])) if near else None
            pick[i] = (cx(g), float(g[0]), float(g[0]) + float(g[2])) if g else a
    last = next(p for p in pick if p)   # 못 본 칸은 앞 칸 얼굴 그대로 (맨 앞은 처음 본 얼굴)
    for i, p in enumerate(pick):
        if p:
            last = p
        else:
            pick[i] = last
    times = [float(t) for t, _ in samples]

    def want(p):
        return min(1.0, max(0.0, (p[0] - v / 2) / span))

    def allowed(p):   # 얼굴 상자(+여유)가 창 안에 드는 reframe 범위 · 얼굴이 창보다 넓으면 가운데
        lo, hi = max(0.0, (p[2] + SKIN_FOLLOW_PAD - v) / span), min(1.0, (p[1] - SKIN_FOLLOW_PAD) / span)
        return (lo, hi) if lo <= hi else (want(p), want(p))
    scenes = [[0]]
    for i in range(1, len(pick)):
        if abs(pick[i][0] - pick[i - 1][0]) > SKIN_FOLLOW_JUMP:
            scenes.append([i])
        else:
            scenes[-1].append(i)
    plans = []
    for sc in scenes:
        lo = max(allowed(pick[i])[0] for i in sc)
        hi = min(allowed(pick[i])[1] for i in sc)
        if lo <= hi + 1e-9:   # 한 자리로 이 장면 얼굴이 다 보임 → 그 자리 (얼굴 가운데값에 가장 가깝게)
            ws = sorted(want(pick[i]) for i in sc)
            rf = min(hi, max(lo, ws[len(ws) // 2]))
            plans.append([(times[sc[0]], rf), (times[sc[-1]], rf)])
            continue
        pts = []
        for j, i in enumerate(sc):
            win = [want(pick[k]) for k in sc[max(0, j - SKIN_FOLLOW_SMOOTH):j + SKIN_FOLLOW_SMOOTH + 1]]
            a, b = allowed(pick[i])
            pts.append((times[i], min(b, max(a, sum(win) / len(win)))))
        plans.append(_rdp(pts, SKIN_FOLLOW_TOL))
    if len(plans) == 1 and abs(plans[0][0][1] - plans[0][-1][1]) < 1e-9 and len(plans[0]) == 2:
        return round(plans[0][0][1], 4), None
    keys = []
    for k, pl in enumerate(plans):
        if k:   # 얼굴이 뛴 곳: 두 칸 사이에 컷이 있으면 앞 장면 값 그대로 컷까지 → 컷에서 바로 다음 값 (컷 앞 장면 한 장도 옮긴 창으로 안 보이게 컷 0.001초 앞) ·
            t0, t1 = keys[-1][0], pl[0][0]   # 컷이 없으면(같은 장면에서 빨리 움직임·다른 사람) 두 칸 사이 0.5초 동안 미끄러지듯 (한 장 만에 휙 튀지 않게 · D-537)
            c = cut(t0, t1) if cut else None
            if c is not None and t0 + 0.012 < float(c) <= t1 + 1e-6:
                keys += [(round(float(c) - 0.011, 3), keys[-1][1]), (round(float(c) - 0.001, 3), pl[0][1])]
        keys += pl if not k or pl[0][0] > keys[-1][0] + 1e-6 else pl[1:]
    keys = [(round(t, 3), round(x, 4)) for t, x in keys]
    out = [keys[0]]
    for t, x in keys[1:]:   # 같은 시각·같은 값이 이어지면 하나로
        if t > out[-1][0] + 1e-6 and not (len(out) >= 2 and abs(x - out[-1][1]) < 1e-9 and abs(out[-1][1] - out[-2][1]) < 1e-9):
            out.append((t, x))
        elif t > out[-1][0] + 1e-6:
            out[-1] = (t, x)
    mids = sorted(x for _, x in out)
    return round(mids[len(mids) // 2], 4), out


def _rf_keys_at(keys, m):
    return float(kf_at({"v": 0.5, "k": [{"t": t, "v": x} for t, x in keys]}, m))


def _skin_runs(items):
    """V1 원본 클립(맞춤 auto · 원본 main) 중 원본이 이어진 것끼리 묶음 [[클립, …]] (타임라인 차례 · 거꾸로·빠르기가 다르면 따로)."""
    v1 = sorted([it for it in items if it.get("track") == "V1" and it.get("media", "main") == "main" and (it.get("fit") or "auto") == "auto"],
                key=lambda it: float(it["start"]))
    runs = []
    for it in v1:
        if runs:
            q = runs[-1][-1]
            if not it.get("rev") and not q.get("rev") and abs(float(it["in"]) - float(q["out"])) <= JUMP_EPS and abs(i_end(q) - float(it["start"])) <= 0.05:
                runs[-1].append(it)
                continue
        runs.append([it])
    return runs


def skin_reframe(name, items, lay):
    """스킨 편집본의 V1 원본 클립마다 화면 좌우를 얼굴에 맞춤 (BR-326 · items 를 고침): 한 자리면 reframe · 움직이면 rfk(원본 시각 키프레임 ·
    reframe 은 클립 가운데 값) · 확대 컷 기준점(fx.anchor)은 원본 좌표 → 창 좌표 (그 클립 가운데 얼굴이 있으면 그 얼굴) · 정지 화면(사진)은 바로 앞 원본 클립 끝 자리.
    얼굴 모델이 없거나 못 보면 그대로. → 얼굴 상자 {원본 시각: …} (강조 글씨 자리 찾기에 씀)."""
    v = skin_view(lay)
    if v >= 1 - 1e-6:
        return {}
    runs = [r for r in _skin_runs(items) if min(float(x["in"]) for x in r) < max(float(x["out"]) for x in r)]
    spans = [(min(float(x["in"]) for x in r), max(float(x["out"]) for x in r)) for r in runs]
    spans = [sp if sp[1] - sp[0] <= SKIN_FOLLOW_SPAN else None for sp in spans]
    faces = skin_faces(name, [sp for sp in spans if sp]) if any(spans) else {}
    if not any(faces.values()):
        return faces
    for run, sp in zip(runs, spans):
        if not sp:
            continue
        rf, keys = skin_follow([(t, faces.get(t)) for t in _skin_grid(*sp)], v, cut=lambda a, b: skin_cut(name, a, b))
        if rf is None:
            continue
        for it in run:
            a, b = float(it["in"]), float(it["out"])
            if keys:
                ks = [(a, _rf_keys_at(keys, a))] + [k for k in keys if a + 1e-3 < k[0] < b - 1e-3] + [(b, _rf_keys_at(keys, b))]
                vals = [x for _, x in ks]
                if max(vals) - min(vals) > 0.005:
                    it["rfk"] = [{"t": round(t, 3), "v": round(x, 4), "e": "lin"} for t, x in ks]
                else:
                    it.pop("rfk", None)
                it["reframe"] = round(_rf_keys_at(keys, (a + b) / 2), 4)
            else:
                it.pop("rfk", None)
                it["reframe"] = rf
    zw = _even(1080 * max(1.0, float(lay.get("zoom") or 1)))
    for it in [x for x in items if x.get("track") == "V1" and x.get("media", "main") == "main" and (x.get("fit") or "auto") == "auto"]:
        anc = (it.get("fx") or {}).get("anchor")
        if not anc:
            continue
        a, b = float(it["in"]), float(it["out"])
        m = (a + b) / 2
        x0 = rf_at(it, m) * (zw - 1080) / zw   # 창 왼쪽 끝 (원본 폭 비율)
        fs = [f for f in faces.get(min(faces, key=lambda t: abs(t - m))) or () if float(f[3]) >= SKIN_FOLLOW_MIN_H] if faces else []
        near = min(abs(t - m) for t in faces) if faces else 99
        to_box = lambda x: round(min(1.0, max(0.0, (float(x) - x0) / v)), 3)  # noqa: E731
        if fs and near <= 1.0:   # 그 클립 가운데 얼굴 (가장 큰 것)
            f = max(fs, key=lambda f: float(f[2]) * float(f[3]))
            anc["v"] = [to_box(float(f[0]) + float(f[2]) / 2), anc["v"][1]]
        else:
            anc["v"] = [to_box(anc["v"][0]), anc["v"][1]]
        for k in anc.get("k") or []:
            k["v"] = [to_box(k["v"][0]), k["v"][1]]
    prev = None   # 정지 화면(그 순간 그림): 바로 앞 원본 클립 끝 자리 (MSG 정지 화면·끝 화면은 바로 앞 장면을 멈춘 것)
    for it in sorted([x for x in items if x.get("track") == "V1"], key=lambda x: float(x["start"])):
        if it.get("media", "main") == "main":
            prev = it
        elif prev is not None and (it.get("fit") or "auto") == "auto" and it.get("reframe", 0.5) == 0.5:
            it["reframe"] = round(rf_at(prev, float(prev["out"])), 4)
    return faces


def skin_screen_faces(items, t, seq, faces, W=1080, H=1920):
    """스킨 편집본 타임라인 t 초 화면의 얼굴 상자 [[x, y, w, h]] (화면 비율 · 화면 좌우·확대 반영) — faces: skin_reframe 이 본 얼굴 (가까운 칸 · 0.6초 안) · 없으면 None."""
    it = next((x for x in items if x.get("track") == "V1" and float(x["start"]) - 1e-6 <= t < i_end(x)), None)
    if it is None or it.get("media", "main") != "main" or not faces:
        return None
    m = i_mt(it, t)
    tk = min(faces, key=lambda x: abs(x - m))
    if abs(tk - m) > 0.6 or faces.get(tk) is None:
        return None
    md = {"kind": "video", "w": (seq.get("info") or {}).get("width") or 1920, "h": (seq.get("info") or {}).get("height") or 1080}
    f = fit_rect(it, md, seq, W, H, m)
    s = float(kf_at(param(it, "scale"), m)) / 100
    ax, ay = (float(v) for v in kf_at(param(it, "anchor"), m))
    ox, oy = f["bx"] + ax * f["bw"], f["by"] + ay * f["bh"]
    out = []
    for x, y, w, h in faces[tk]:
        px, py = f["bx"] + f["mx"] + float(x) * f["mw"], f["by"] + f["my"] + float(y) * f["mh"]
        out.append([(ox + (px - ox) * s) / W, (oy + (py - oy) * s) / H, float(w) * f["mw"] * s / W, float(h) * f["mh"] * s / H])
    return out


SKIN_EMPH_SIZES = (80, 72, 64)   # 스킨 가편집 쇼츠 강조 큰 글씨 크기 (한 줄로 들어가는 가장 큰 것 · 쇼츠와 같음)


def skin_emph_placer(items, seq, cap_style, win, faces):
    """스킨 가편집 쇼츠 강조 큰 글씨 자리 (BR-327 · D-534) — 영상 창 안: 먼저 창 맨 위 줄(글 상자 위 끝 = 창 위 끝 + 틈 · MSG 상황 자막과 같은 줄) →
    머리 위·옆 → 말 자막 바로 위 · 위 띠(큰 제목·주제 줄)·말 자막 줄·아래 띠와 그 장면 얼굴(skin_screen_faces · 못 보면 흔한 얼굴 자리)은 피함."""
    W, H = frame_size("shorts")
    cb = _cap_band(cap_style, H)
    avoid = [(0.0, float(win["top"]) + TXT_GAP), (cb[0], 1.0), (float(win["bottom"]), 1.0)]

    def size(text, st):
        return next((z for z in SKIN_EMPH_SIZES if _one_line(text, dict(st, size=z), W)), SKIN_EMPH_SIZES[-1])

    def place(text, st, a, b):
        st = dict(st, size=size(text, st))
        bh = text_box(text, dict(st, y=0.5), W, H)
        st["y"] = math.ceil((float(win["top"]) + 2 * TXT_GAP + (bh[3] - bh[1])) * 1000 - 1e-6) / 1000
        fs = []
        for t in (a + 0.2, (a + b) / 2, b - 0.2):
            fs += skin_screen_faces(items, t, seq, faces, W, H) or []
        if not fs:
            fs = [[0.35, float(win["top"]) + 0.08, 0.30, 0.22]]
        return emphasis_spot(text, st, W, H, fs, avoid)
    place.size = size
    return place


def series_apply(name, seq):
    """쇼츠 편집본의 시리즈 이름표 번호를 정해 글에 넣음 (처음 내보낼 때·올리기 키트를 만들 때 · seq 를 고침) → {seq, id, n, text, x, w} · 이름표가 없으면 None.
    글의 번호가 넣어 둔 번호(seriesN)와 다르면 사용자가 고친 번호로 그대로 씀 · 같으면 정해 둔 번호(없으면 새 번호)."""
    if (seq or {}).get("format") != "shorts" or not seq.get("id"):
        return None
    t = next((x for x in seq.get("titles") or [] if x.get("plan") == "series"), None)
    if not t:
        return None
    import style as _style
    m = _SER_NO.search(str(t.get("text") or ""))
    shown = int(m.group(1)) if m else None
    want = shown if shown and shown != t.get("seriesN") else None
    n = _style.series_assign(name, seq["id"], want)
    base = _SER_NO.sub("", str(t.get("text") or "")).strip() or _style.card_extras()["seriesName"]
    t["text"], t["seriesN"] = ep_series_text(base, n), n
    g = next((x for x in seq.get("shapes") or [] if x.get("plan") == "series"), None)
    if g:
        ep_series_fit(t, g)
    return {"seq": seq["id"], "id": t["id"], "n": n, "text": t["text"], **({"x": g["x"], "w": g["w"]} if g else {})}


def series_view(name, proj):
    """편집실을 열 때: 다른 곳(휴대폰 내보내기·끝까지 자동으로·올리기 키트)에서 정한 번호를 이름표 글에 보여 줌 (사용자가 고친 번호는 그대로) ·
    저장은 편집실이 고칠 때 함께."""
    try:
        import style as _style
        nums = _style.series_numbers(name)
    except Exception:  # noqa: BLE001
        return proj
    for q in proj.get("sequences") or []:
        t = next((x for x in q.get("titles") or [] if x.get("plan") == "series"), None) if isinstance(q, dict) else None
        n = nums.get(q.get("id")) if t else None
        m = _SER_NO.search(str(t.get("text") or "")) if n else None
        if m and int(m.group(1)) == t.get("seriesN") and n != t.get("seriesN"):
            t["text"], t["seriesN"] = ep_series_text(_SER_NO.sub("", t["text"]).strip(), n), n
            g = next((x for x in q.get("shapes") or [] if x.get("plan") == "series"), None)
            if g:
                ep_series_fit(t, g)
    return proj


CAP_Y = {"bottom": 0.85, "middle": 0.55, "top": 0.15}
TXT_LINE = 1.25   # 글 상자 한 줄 높이 = 글자 크기 × 1.25 (editor.html .txt line-height · 내보내기 ASS 도 같은 크기)
TXT_GAP = 0.012   # 자동으로 놓는 글 상자끼리 띄울 틈 (화면 높이 비율)


def frame_size(fmt):
    """글자 크기·자리의 기준 화면 (내보내기 BW×BH 와 같음)."""
    return (1080, 1920) if fmt == "shorts" else (1920, 1080)


def text_box(text, st, W, H):
    """글 상자 (왼, 위, 오른, 아래) — 화면 비율 0~1. y 는 글 아래 끝 (editor.html styleText · ASS \\an2·\\pos 와 같음).
    화면 폭 92% 안에서 띄어쓰기마다 줄을 바꿔 셈 (editor.html .txt max-width 92%) · 글자 폭은 cap_fit 과 같은 어림."""
    size, stroke = float(st.get("size") or 0), float(st.get("strokeW") or 0)
    black, fm = st.get("weight") == "Black", ep_font_now()
    avail, sp = 0.92 * W - 2 * stroke, _em(" ", fm=fm) * size
    widths = []
    for para in str(text or "").replace("\r", "").split("\n"):
        cur = 0.0
        for w in para.split(" "):
            ww = sum(_em(ch, black, fm) for ch in w) * size
            if cur and cur + sp + ww > avail:
                widths.append(cur)
                cur = ww
            else:
                cur = cur + (sp if cur else 0.0) + ww
        widths.append(cur)
    wd = min(avail, max(widths or [0.0])) + 2 * stroke
    h = len(widths or [0]) * TXT_LINE * size + 2 * stroke
    x, al = float(st.get("x", 0.5)) * W, st.get("align", "center")
    left = x if al == "left" else x - wd if al == "right" else x - wd / 2
    y = float(st.get("y", 0.5)) * H
    return (left / W, (y - h) / H, (left + wd) / W, y / H)


def _cap_band(st, H, lines=1):
    """자막 글줄이 차지하는 높이 (위, 아래) 0~1 — 한 줄 자막 기준 (넘치면 cap_fit 이 글자를 줄여 한 줄로)."""
    h = (lines * TXT_LINE * float(st.get("size") or 0) + 2 * float(st.get("strokeW") or 0)) / H
    return float(st.get("y", 0.85)) - h, float(st.get("y", 0.85))


def _stack_below(top_bottom, st, H, lines=1):
    """st 글 상자를 위 글 상자 아래 끝(top_bottom) 밑으로 — 새 y (글 아래 끝)."""
    h = (lines * TXT_LINE * float(st.get("size") or 0) + 2 * float(st.get("strokeW") or 0)) / H
    return math.ceil((top_bottom + TXT_GAP + h) * 1000 - 1e-6) / 1000  # 올림: 반올림으로 틈이 조금이라도 줄지 않게


def _overlap(a, b, gap=0.0):
    """두 (위, 아래) 띠가 gap 안으로 겹치는지."""
    return a[0] < b[1] + gap and b[0] < a[1] + gap

# ---------- 영상 기획 분석 (plan) 반영: 인트로 티저 · 강조 자막 — 스타일 가편집(롱폼)에만, 원본 장면은 지우지 않음 ----------
# 강조 낱말: 명사·부사만 (낱말 줄기 '잘하'·'힘들', 흔한 말 '진짜'·'어떻게' 는 뺌) — 낱말 전체가 같을 때만 (부분 일치 금지: '패턴'의 '턴')
EMPH_WORDS = ("무조건", "절대", "대박", "100%", "이것만", "핵심", "꿀팁", "비결", "중요", "실수", "포인트", "비밀", "원리", "차이")
EMPH_GAP = 15.0       # 강조 자막끼리 최소 간격(초)
EMPH_DUR = 1.5
EMPH_SHORT = 12       # 말 전체를 그대로 띄울 수 있는 길이 (띄어쓰기 뺀 글자 수)
_EMPH_TAIL = re.compile(r"^(이에요|예요|입니다|이죠|이고|해요|합니다|하는|하면|하고|해서|할|한|해|하죠|하게|이|가|을|를|은|는|도|만|의|에|에서|으로|로|와|과)?[!~.?]*$")


def _emph_terms():
    """강조로 띄울 기술 이름 (hooks.TERMS 중 두 글자 넘는 것 — '턴'·'슛'·'킥' 같은 한 글자는 다른 낱말 속에 흔해서 뺌)."""
    import hooks
    return [t for t in hooks.TERMS if len(t.replace(" ", "")) >= 2]


def _tokens(txt):
    return re.findall(r"[가-힣A-Za-z0-9%]+", str(txt or ""))


def _find_terms(toks, terms):
    """낱말 목록에서 기술 이름 찾기 (조사만 떼고 낱말 전체가 같아야 함 · 여러 낱말 이름은 이어진 낱말로) → [(시작 번호, 끝 번호, 이름)]."""
    out, i = [], 0
    multi = sorted((t.split() for t in terms), key=len, reverse=True)
    while i < len(toks):
        hit = None
        for parts in multi:
            k = len(parts)
            if i + k > len(toks):
                continue
            seg = toks[i:i + k - 1] + [toks[i + k - 1]]
            last = seg[-1]
            if seg[:-1] == parts[:-1] and (last == parts[-1] or (last.startswith(parts[-1]) and _EMPH_TAIL.match(last[len(parts[-1]):]))):
                hit = (i, i + k, " ".join(parts))
                break
        if hit:
            out.append(hit)
            i = hit[1]
        else:
            i += 1
    # 이어진 기술 이름은 하나로 ('인사이드' '패스' → '인사이드 패스')
    merged = []
    for a, b, t in out:
        if merged and merged[-1][1] == a:
            merged[-1] = (merged[-1][0], b, merged[-1][2] + " " + t)
        else:
            merged.append((a, b, t))
    return merged


def _find_emph(toks):
    for tk in toks:
        for w in EMPH_WORDS:
            if tk == w or (tk.startswith(w) and _EMPH_TAIL.match(tk[len(w):])):
                return w
    return None


def emphasis_label(txt, terms=None, short=True):
    """말 한 줄 → 화면에 띄울 강조 글자 (없으면 None). 짧은 말은 그대로(끝 마침표·쉼표는 뗌), 길면 '기술 이름 + 강조 낱말!' ('인사이드 패스 핵심!').
    short=False(대사 자막이 켜짐): 짧은 말도 '기술 이름 + 강조 낱말!' · '강조 낱말!' 로만 — 자막과 같은 글이면 None.
    기술 이름도 강조 낱말도 낱말 전체가 맞을 때만 — 낱말 조각('잘하!'·'턴!')은 만들지 않음."""
    terms = terms if terms is not None else _emph_terms()
    toks = _tokens(txt)
    found = _find_terms(toks, terms)
    emph = _find_emph(toks)
    if not found and not emph:
        return None, 0
    flat = re.sub(r"\s+", " ", str(txt or "")).strip()
    score = len(found) + (2 if emph else 0)
    is_short = len(flat.replace(" ", "")) <= EMPH_SHORT
    if is_short and short:
        return re.sub(r"[\s.,。、…]+$", "", flat) or flat, score
    lab = None
    if found and emph:
        lab = f"{found[0][2]} {emph}!"
    elif found and len(found[0][2].replace(" ", "")) >= 3:  # 기술 이름만 있으면 이름이 충분히 길 때만 ('인사이드 패스!', '트래핑!')
        lab = f"{found[0][2]}!"
    elif is_short and emph:  # 자막이 켜진 짧은 말: 말 전체 대신 강조 낱말만 ('세번째 포인트.' → '포인트!')
        lab = f"{emph}!"
    if lab is None or (is_short and _norm(lab) == _norm(flat)):  # 자막과 같은 글은 한 화면에 두 번 띄우지 않음
        return None, 0
    return lab, score


# 감독님이 '핵심이에요·중요해요·무조건·절대'라고 콕 집은 말 (BR-293 · D-483) — 강조 큰 글씨는 이 문장의 핵심 구절을 먼저
KEY_SAY = re.compile(r"핵심|중요|무조건|절대|생명")
KEY_WORD = re.compile(r"(핵심|생명|포인트|중요)")
KEY_HEAD = re.compile(r"(?:가장\s|제일\s|진짜\s|정말\s)?(?:중요한\s?(?:건|것은|거는|게|거)|핵심은|포인트는)\s+(.+)$")
KEY_THIS = {"이게", "이거", "이것", "그게", "그거", "그것", "저게", "요게", "이건", "그건", "여기", "거기", "이", "그", "저", "오늘", "이번", "제일", "가장",
            "진짜", "정말", "다", "또", "이부분", "이부분이", "부분"}
KEY_SCORE = 10        # 콕 집은 말의 강조 글씨는 기술 이름만 든 말보다 먼저 (점수)
KEY_MAX = 12          # 핵심 구절 글자 수 (띄어쓰기·느낌표 빼고)
KEY_DUP = 8           # 말 자막이 켜져 있으면 말을 그대로 옮긴 이 글자 수 넘는 구절은 안 띄움 (msg.EMPH_DUP 과 같음)


def _key_phrase_words(x):
    """'디딤발 위치' · '돌파는 타이밍' → ['디딤발', '위치'] · ['돌파', '타이밍'] — 가리키는 말('이게'·'여기')·꾸밈말은 빼고 앞 낱말의 은/는 은 뗌."""
    out = []
    for w in str(x or "").split():
        if w in KEY_THIS:
            continue
        if len(w) >= 3 and w[-1] in "은는":
            w = w[:-1]
        if w in KEY_THIS or len(w) < 2:
            continue
        out.append(w)
    return out


KEY_DEM = re.compile(r"^(?:(?:자|그래서|그러니까|그리고|네)[,.\s]+)*(?:이게|이거|이건|그게|그거|그건|저게|요게|여기가|여기는|이|그)\s+")
# 콕 집은 말 앞뒤에 감독님이 늘 붙이는 이끄는 말 (리뷰: '중요한 게 뭐냐면요' → '뭐냐면요!' · '핵심은 세 가지예요' → '세 가지!' ·
# '자 오늘 핵심 정리할게요' → '자 오늘 핵심!') — 이런 말만 남으면 강조 큰 글씨를 만들지 않고 다음 문장·기술 이름 규칙(emphasis_label)으로
KEY_LEAD = re.compile(r"^(?:뭐|뭔|뭘|무엇|무슨|어떤|어떻게|왜|이거|이것|이건|요거|저거|그거|그것|여기|저기|거기|이렇게|이런|그런|저런|하나|한\s?가지|"
                      r"두\s?가지|세\s?가지|네\s?가지|몇|다음|가지|번째)")
KEY_TALK = re.compile(r"말씀|얘기|이야기|설명|알려|보여|정리|드릴|드려|할게|볼게|볼까|하겠|해\s?볼|시작")
KEY_FILL = re.compile(r"^(?:(?:자|네|그래서|그러니까|그리고|그럼|그러면|결국|오늘|오늘의|이번|이번에|여기서|거기서|지금|다시|정리하면|정리하자면)[,.\s]+)+")


def _key_vague(x, generic=True):
    """강조 큰 글씨로 띄울 만한 내용이 없는 말 ('뭐냐면요' · '이거' · '여기 있어요' · '세 가지' · '하나만 말씀드릴게요' · '포인트').
    generic=False: 이끄는 말·말하기 낱말만 봄 (④ 앞 구절 '차이가 정말 중요'는 콕 집은 낱말 앞이라 GENERIC 이어도 씀)."""
    ws = [w for w in re.split(r"[\s,.!?~…]+", str(x or "")) if w]
    if not ws:
        return True
    if KEY_TALK.search(x) or any(KEY_LEAD.match(w) for w in ws):
        return True
    return generic and all(w in KEY_THIS or w in GENERIC or re.sub(r"(?:이에요|예요|이죠|에요|이|가|은|는)$", "", w) in GENERIC for w in ws)


def key_label(txt, terms=None):
    """감독님이 콕 집은 말 한 문장(KEY_SAY) → 강조 큰 글씨 (없으면 None). 차례:
    ① 'X이/가/은/는 (진짜) 핵심·중요·포인트·생명' 의 X → 'X 핵심!' ('디딤발 위치 핵심!' · '돌파 타이밍 중요!') —
       '이 타이밍이 생명'처럼 가리키는 말 + 요점 낱말(POINT_NOUNS)이면 그 문장의 기술 이름을 붙임('패스 타이밍 생명!') · '이 차이가'는 ④
    ② '핵심은 / 중요한 건 Y' 의 Y 전체가 짧을 때 ('공을 끝까지 보는 거!' · 자르면 조각이라 안 씀)
    ③ '무조건·절대 …' 한 구절 (msg._emph_clause 와 같은 셈 · 콕 집은 낱말·꾸밈말만 남으면 아님)
    ④ 그 문장의 기술 이름 + 콕 집은 낱말 ('인사이드 패스 핵심!') · 없으면 앞 가리키는 말을 뗀 그 구절 ('이게 진짜 핵심이에요' → '진짜 핵심!' ·
       msg 강조 글자와 같음 · '이 차이가 정말 중요해요' → '차이가 정말 중요!').
    글자는 KEY_MAX 자 안 · 맞는 게 없으면 None (기술 이름·강조 낱말 규칙 emphasis_label 로)."""
    t = re.sub(r"\s+", " ", str(txt or "")).strip()
    if not t or not KEY_SAY.search(t):
        return None
    terms = terms if terms is not None else _emph_terms()

    def ok(lab):
        return lab if lab and 3 <= len(_norm(lab)) <= KEY_MAX + 1 else None
    found = _find_terms(_tokens(t), terms)
    for m in KEY_AT.finditer(t):
        ws = _key_phrase_words(m.group(1))
        w = KEY_WORD.search(t[m.end(1):]).group(1)
        if any(x in KEY_THIS for x in m.group(1).split()[:1]):
            if not ws or not all(x in POINT_NOUNS for x in ws):
                continue
            ws = ([found[0][2]] if found else []) + ws
        else:
            while ws and KEY_FILL.match(ws[0] + " "):  # '여기서 디딤발이 핵심' → '디딤발 핵심!' (앞 이끄는 말은 뗌)
                ws = ws[1:]
            if ws and _key_vague(" ".join(ws)):  # '두 가지가 핵심' · '세 번째가 중요' → 이끄는 말이라 다음 규칙으로
                continue
        lab = ok(f"{' '.join(ws)} {w}!") if ws else None
        if lab:
            return lab
    m = KEY_HEAD.search(t)
    if m:  # 뒤 말 전체가 짧을 때만 (자르면 '… 멈추지 않는!' 같은 조각)
        parts = [x.strip() for x in re.split(r",", re.split(r"[.!?]", m.group(1))[0]) if x.strip()]
        while parts and _key_vague(parts[0]):  # '중요한 게 뭐냐면요, 디딤발 위치예요' → '디딤발 위치' (앞 이끄는 말은 뗌)
            parts.pop(0)
        rest = re.sub(r"[\s,.~…]+$", "", ", ".join(parts))
        rest = re.sub(r"\s*(?:이에요|예요|입니다|이죠|거든요|에요)$", "", rest).strip()
        lab = (rest + "!") if 2 <= len(_norm(rest)) <= KEY_MAX and not _key_vague(rest) else None
        if lab:
            return lab
    if re.search(r"무조건|절대", t):
        import msg
        lab, _ = msg._emph_clause(t)
        left = re.sub(r"무조건|절대|핵심|중요|진짜|정말|이게|이거|그게|제일|가장|꼭|이에요|예요|해요|합니다|입니다|[!.,?~\s]", "", lab or "")
        if lab and len(left) >= 3:
            return ok(lab)
    w = KEY_WORD.search(t)
    if w and w.group(1) != "포인트":
        if found:
            return ok(f"{found[0][2]} {w.group(1)}!")
        head = re.split(r"[.!?,]\s*", t[:w.end()])[-1].strip()
        head = re.sub(r"^(?:부분이|부분은|부분|거|것이|것은|게)\s+", "", KEY_DEM.sub("", KEY_FILL.sub("", head))).strip()   # '이 부분이 정말 중요' → '정말 중요'
        if head[:-len(w.group(1))].strip() and _key_vague(head[:-len(w.group(1))], generic=False):
            head = ""  # '자 정리하면 핵심' · '뭐가 중요' → 이끄는 말만 남음 (KEY_FILL 로 '자·오늘·여기서'를 뗀 뒤)
        if not re.sub(r"(?:진짜|정말|제일|가장|완전|꼭|너무)\s*", "", head[:-len(w.group(1))]).strip() and len(head) == len(w.group(1)):
            head = ""
        if 2 <= len(_norm(head)) <= KEY_MAX - 2:
            return head + "!"
    return None


def _sentences(seg):
    """받아쓴 구간 하나 → 문장 [(글, 원본 시작 초)] — 낱말 시각이 있으면 문장 끝(. ? !) 낱말로 나누고, 없으면 글자 수 비율로 시각을 어림."""
    t0, t1 = float(seg.get("start", 0)), float(seg.get("end", 0))
    ws = [w for w in seg.get("words") or () if str(w.get("w") or "").strip()]
    out = []
    if ws:
        cur, start = [], None
        for w in ws:
            if start is None:
                start = float(w.get("s", t0))
            cur.append(str(w["w"]).strip())
            if re.search(r"[.?!]$", cur[-1]):
                out.append((" ".join(cur), start))
                cur, start = [], None
        if cur:
            out.append((" ".join(cur), start))
        return out
    txt = str(seg.get("text") or "").strip()
    parts = [x for x in re.split(r"(?<=[.?!])\s+", txt) if x.strip()]
    pos, n = 0, max(1, len(txt))
    for x in parts:
        k = txt.find(x, pos)
        out.append((x, t0 + (t1 - t0) * max(0, k) / n))
        pos = max(pos, k) + len(x)
    return out


def _intro_teaser(items, rec, tidy, sec, segs=None, peaks=None, tag=True):
    """롱폼 맨 앞에 미리 보기 sec 초 + 제목 → (items, titles). 원래 컷은 그만큼 뒤로 밂 (지우지 않음).
    고르는 곳: 정리 컷 안의 sec 초 창 중에서 말이 적고(감독님 시범) 현장 소리가 큰(공 차는 소리·환호) 곳 — 첫 쇼츠 추천 구간이면 조금 더 점수.
    말이 많은 곳밖에 없으면 그 소리는 줄여서 (제목과 겹쳐 말이 끊겨 들리지 않게).
    제목은 그 장면에서 하는 말(teaser_title) · 미리 보기 클립은 noCaps (말 자막·SRT 에 안 들어감) · tag 면 작은 '미리보기' 표시 (BR-292)."""
    r = (rec.get("shorts") or [None])[0]
    pool = list(tidy or []) or list((r or {}).get("cuts") or [])
    if not pool or sec <= 0:
        return items, []
    talk = [(float(s["start"]), float(s["end"])) for s in segs or [] if float(s.get("end", 0)) > float(s.get("start", 0))]
    rr = (float(r["start"]), float(r["end"])) if r and "start" in r and "end" in r else None
    away = [(float(o["a"]), float(o["b"])) for o in rec.get("offscript") or ()]  # 구독·홍보 안내 말은 티저로 안 씀 (E12 · BR-060)
    pk, per = None, 50
    if peaks and peaks.get("peaks"):
        pk, per = peaks["peaks"], int(peaks.get("per_sec") or 50)

    def speech(a, b):
        return sum(max(0.0, min(b, e) - max(a, s)) for s, e in talk) / max(1e-6, b - a)

    def loud(a, b):
        if not pk:
            return 0.0
        seg = pk[int(a * per):int(b * per)]
        if not seg:
            return 0.0
        top = sorted(seg)[-max(1, len(seg) // 10):]  # 창 안에서 가장 큰 10% (공 차는 소리·환호)
        return sum(top) / len(top)
    def pick(full):
        """full: sec 초를 다 채우는 창만 (없을 때만 더 짧은 컷도)."""
        best = None
        for c in pool:
            a0, b0 = float(c["in"]), float(c["out"])
            if b0 - a0 < 1.0 or (full and b0 - a0 < sec - 1e-6):
                continue
            t = a0
            while True:
                a, b = t, min(b0, t + sec)
                if b - a >= min(sec, b0 - a0) - 1e-6 and not any(x < b and a < y for x, y in away):
                    sc = -2.0 * speech(a, b) + loud(a, b) + (0.3 if rr and rr[0] <= a and b <= rr[1] else 0.0)
                    if best is None or sc > best[0] + 1e-9:
                        best = (sc, a, b)
                if b >= b0 - 1e-6:
                    break
                t = min(t + 1.0, b0 - sec) if b0 - sec > t + 1e-6 else b0
        return best
    best = pick(True) or pick(False)
    if best is None:
        return items, []
    _, a, b = best
    if b - a < 1.0:
        return items, []
    ln = round(b - a, 3)
    moved = [dict(it, start=round(float(it["start"]) + ln, 4)) for it in items]
    vol = 0.3 if speech(a, b) > 0.3 else 1.0
    pr = _pair(a, b, start=0.0, vol=vol)
    for x in pr:  # 미리 보기는 본론 장면을 한 번 더 쓴 것 → 말 자막·SRT 에 그 말을 다시 넣지 않음 (noCaps · BR-292)
        x["noCaps"] = True
    titles = [{"id": _nid(), "text": teaser_title(segs, a, b), "start": 0.0, "dur": round(ln, 2), "style": dict(TITLE_STYLE), "plan": "teaser"}]
    if tag:  # 작은 '미리보기' 표시 (본론이 아니라는 것)
        titles.append({"id": _nid(), "text": TEASER_TAG_TEXT, "start": 0.0, "dur": round(ln, 2), "style": dict(TEASER_TAG), "plan": "teaserTag"})
    return pr + moved, titles


TEASER_TAG_TEXT = "미리보기"
TEASER_TAG = dict(DEFAULT_STYLE, weight="Black", size=40, fill="#FFFFFF", stroke="#000000", strokeW=6, bgOn=True, bg="#000000", bgOpacity=0.55,
                  x=0.04, align="left", y=0.075, effect="none")   # 왼쪽 위 작은 글 (아래 끝 0.075 · 티저 큰 제목 위 끝 0.09 보다 위)
TEASER_FALLBACK = "오늘의 하이라이트"


def teaser_title(segs, a, b):
    """티저 큰 제목 (BR-292) — 그 미리 보기 장면(원본 a~b 초)에서 하는 말에서: 그 말의 풋살 용어('디딤발 꿀팁' · '트래핑!') →
    그 말 한 문장(16자 · 인사·끝인사·반응만인 문장·순서 말은 빼고) → 말이 없으면(시범 장면) 영상 전체 주제('퍼스트 터치!') → '오늘의 하이라이트'.
    예전: 첫 쇼츠 추천의 제목 — 다른 장면의 말이라 화면과 제목이 서로 다른 이야기를 함."""
    import hooks
    segs = list(segs or ())
    inside = []
    for x in segs:
        s0, e0 = float(x.get("start", 0)), float(x.get("end", 0))
        ov = max(0.0, min(b, e0) - max(a, s0))
        if e0 > s0 and ov >= 0.5 * (e0 - s0) or (e0 <= s0 and a <= s0 < b):  # 그 장면 안에서 반 넘게 하는 말
            inside.append(str(x.get("text") or "").strip())
    inside = [t for t in inside if t]
    cands = []
    if inside:
        body = " ".join(inside)
        try:
            own = [t for t in hooks._ranked_terms(inside, hooks.topic_terms(), 3) if len(hooks._compact(t)) >= 2 and hooks._compact(t) not in HOOK_NOT_TOPIC]
        except Exception:  # noqa: BLE001 — 용어 사전을 못 읽어도 문장으로
            own = []
        tip = any(k in body for k in HOOK_TIP)
        cands += [f"{t} 꿀팁" if tip else f"{t}!" for t in own]
        for t in inside:
            if GREETING.search(t) or takes.CLOSING.search(t):
                continue
            sent = _hook_cut(t)
            m = re.match(r"^[^?!.,]{1,8}[?!.,]+\s*(.+)$", t)
            if not _hook_sentence_ok(sent) and m and REACTION.match(t):  # 반응 말로 시작하면 그 뒤 말 ('봤죠? 회전이 하나도 없어요' → '회전이 …')
                sent = _hook_cut(m.group(1))
            if len(_norm(sent)) >= HOOK_MIN and _hook_sentence_ok(sent):
                cands.append(sent)
    try:
        whole = [t for t in hooks.topic_keywords([str(x.get("text") or "") for x in segs], n=3, lesson=False)
                 if hooks._compact(t) not in HOOK_NOT_TOPIC - {"패스"}]
    except Exception:  # noqa: BLE001
        whole = []
    cands += [f"{whole[0]}!"] if whole else []
    generic = {_norm(g) for g in GENERIC}
    return next((c for c in cands if c and _norm(c) not in generic), TEASER_FALLBACK)


def _src_to_tl(items, t):
    """원본 t 초 → 타임라인 시각 (V1 클립 안일 때만, 잘린 곳이면 None)."""
    for it in items:
        if it.get("track") == "V1" and it.get("media", "main") == "main" and not it.get("noCaps") and float(it["in"]) <= t < float(it["out"]):
            return float(it["start"]) + (t - float(it["in"])) / i_sp(it)
    return None


def _emph_skip(s, t_end):
    """강조 글씨를 띄우지 않을 말 — 인사·자기소개('안녕하세요 … 오늘은 퍼스트 터치에 대해서')·끝인사('오늘은 여기까지 · 감사합니다') (BR-293).
    t_end: 영상 길이 (첫머리 '오늘은 … 알려 드릴게요' 소개는 앞 SHORT_EDGE 안에서만)."""
    t = str(s.get("text") or "")
    if GREETING.search(t) or takes.CLOSING.search(t):
        return True
    return bool(takes.OPENING.search(t)) and float(s.get("start", 0)) <= SHORT_EDGE * max(1.0, t_end)


def _emphasis_titles(items, segs, per_min, color, after=0.0, place=None, captions_on=False, most=None, gap=EMPH_GAP, avoid=()):
    """받아쓰기에서 기술 이름·강조 낱말이 든 말을 골라 1.5초 큰 색 글씨(titles)로 — 1분에 per_min 개까지, 서로 EMPH_GAP 초 넘게 떨어뜨림.
    teaser 가 있으면 그 뒤(after)부터. 글자는 emphasis_label (짧은 말은 그대로, 길면 '기술 이름 + 강조 낱말!', 맞는 게 없으면 건너뜀).
    place(글, 모양, 시작, 끝) → (x, y): 자리 고르기 (emphasis_placer · 없으면 예전처럼 화면 위 30%).
    captions_on: 대사 자막이 켜져 있으면 짧은 말을 그대로 띄우지 않음 (같은 글이 자막과 강조 글씨로 한 화면에 두 번) — 강조 낱말만 ('포인트!').
    감독님이 '핵심·중요·무조건·절대'라고 콕 집은 말은 그 핵심 구절(key_label)을 먼저 · 인사·끝인사 말에는 안 띄움 (BR-293).
    most: 개수 한도 (쇼츠 1~2개 · 없으면 per_min 으로) · gap: 서로 떨어뜨릴 초 · avoid: 띄우지 않을 글 (쇼츠 위 큰 제목과 같은 글)."""
    total = max([i_end(it) for it in items] or [0.0])
    cap = int(per_min * total / 60.0 + 1e-9) if most is None else int(most)
    if cap <= 0 or not segs:
        return []
    terms = _emph_terms()
    t_end = max([float(s.get("end", 0)) for s in segs] or [0.0])
    skip = {_norm(x) for x in avoid if x}
    cands = []
    for s in segs:
        txt = str(s.get("text") or "").strip()
        label, t = None, float(s["start"])
        for sent, at in _sentences(s):  # 콕 집은 말은 문장마다 (받아쓴 구간 하나에 여러 문장 · 인사·끝인사 문장은 건너뜀)
            if _emph_skip({"text": sent, "start": at}, t_end):
                continue
            lab = key_label(sent, terms)
            if lab and captions_on and (_norm(lab) == _norm(sent) or (len(_norm(lab)) > KEY_DUP and _norm(lab) in _norm(sent))):
                lab = None  # 말 한 구절을 그대로 옮긴 긴 글 → 아래 말 자막과 같은 글이 두 번 (msg.EMPH_DUP 과 같은 규칙)
            if lab:
                label, t, score = lab, at, KEY_SCORE + len(_norm(lab)) / 100.0
                break
        if label is None:
            if _emph_skip(s, t_end):
                continue
            label, score = emphasis_label(txt, terms, short=not captions_on)
        if not label or _norm(label) in skip:
            continue
        key = label.rstrip("!").split()[0]
        for w in s.get("words") or ():  # 낱말 시각이 있으면 그 낱말이 나오는 때에
            if float(w.get("s", 0)) >= t - 1e-6 and str(w.get("w") or "").replace(" ", "").startswith(key):
                t = float(w.get("s", t))
                break
        tl = _src_to_tl(items, t)
        if tl is None or tl < after + 1.0 or tl + EMPH_DUR > total:
            continue
        cands.append((-score, tl, label))
    picked = []
    for _, tl, label in sorted(cands):
        if len(picked) >= cap:
            break
        if all(abs(tl - p) >= gap for p, _ in picked):
            picked.append((tl, label))
    fill = str(color or "").upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(color or "")) else "#FFE14D"
    sty = dict(TITLE_STYLE, fill=fill, size=88, y=0.3)
    out = []
    if place is not None and hasattr(place, "prefetch"):  # 볼 장면을 한꺼번에 (하나씩 보면 10분 영상에 1분 넘게 걸림)
        place.prefetch([(tl, tl + EMPH_DUR) for tl, _ in picked])
    for tl, label in sorted(picked):
        st1 = dict(sty)
        if place is not None:  # 그 장면 얼굴·자막을 피한 빈자리
            if hasattr(place, "size"):  # 쇼츠: 한 줄로 들어가는 글자 크기 (shorts_emph_placer)
                st1["size"] = place.size(label, st1)
            st1["x"], st1["y"] = place(label, st1, tl, tl + EMPH_DUR)
        out.append({"id": _nid(), "text": label, "start": round(tl, 2), "dur": EMPH_DUR, "style": st1, "plan": "emphasis"})
    return out


# ---------- 자동 강조 글씨 자리: 얼굴(눈)을 가리지 않게 머리 위·옆·아래 빈자리로 ----------
EMPH_SAFE = (0.03, 0.04, 0.97, 0.96)   # 글 상자가 들어갈 화면 안쪽 (왼, 위, 오른, 아래)
FACE_PAD = (0.02, 0.06, 0.02, 0.02)    # 얼굴 상자 둘레 여유 (왼, 위(머리카락), 오른, 아래)
FACE_GUESS = [[0.35, 0.12, 0.30, 0.26]]  # 얼굴을 못 찾을 때(모델 없음) 말하는 장면의 흔한 얼굴 자리 (가운데 · 화면 높이 12~38% — 실측 얼굴 15~35% + 여유)


def emphasis_spot(text, st, W, H, faces, avoid=()):
    """강조 글씨를 놓을 자리 (x, y) — 얼굴 상자 faces [[x, y, w, h] 0~1] 와 피할 띠 avoid [(위, 아래)] (대사 자막·쇼츠 훅)와
    겹치지 않는 곳 중 먼저: 원래 자리(스타일 x·y) → 머리 위 → 옆(오른쪽·왼쪽) → 화면 위쪽 빈 띠 → 자막 위 빈 띠 → 얼굴 아래(몸·공).
    다 겹치면 얼굴과 가장 적게 겹치는 곳."""
    fb = [(f[0] - FACE_PAD[0], f[1] - FACE_PAD[1], f[0] + f[2] + FACE_PAD[2], f[1] + f[3] + FACE_PAD[3]) for f in faces or ()]
    x0, y0 = float(st.get("x", 0.5)), float(st.get("y", 0.3))
    b0 = text_box(text, dict(st, x=0.5, y=0.5), W, H)
    bw, bh = b0[2] - b0[0], b0[3] - b0[1]
    cands = [(x0, y0)]
    if fb:
        f = max(fb, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))  # 주인공 얼굴 (가장 큰 얼굴)
        cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
        cands += [(cx, f[1] - TXT_GAP), (f[2] + TXT_GAP + bw / 2, cy + bh / 2), (f[0] - TXT_GAP - bw / 2, cy + bh / 2)]
    low = min([a for a, b in avoid if a > 0.5] or [EMPH_SAFE[3]]) - TXT_GAP
    high = max([b for a, b in avoid if b < 0.5] or [EMPH_SAFE[1]]) + TXT_GAP + bh
    cands += [(0.5, high), (0.5, low)]
    if fb:  # 얼굴 바로 아래(가슴·공 — 시범 동작을 가림)는 마지막에
        cands.append((cx, f[3] + TXT_GAP + bh))

    def fit(x, y):  # 화면 안으로 (가로만 밀어 넣음)
        return min(max(x, EMPH_SAFE[0] + bw / 2), EMPH_SAFE[2] - bw / 2), y

    def bad(x, y):
        box = (x - bw / 2, y - bh, x + bw / 2, y)
        face = sum(max(0.0, min(box[2], r[2]) - max(box[0], r[0])) * max(0.0, min(box[3], r[3]) - max(box[1], r[1])) for r in fb)
        out = box[1] < EMPH_SAFE[1] - 1e-6 or box[3] > EMPH_SAFE[3] + 1e-6 or box[0] < EMPH_SAFE[0] - 1e-6 or box[2] > EMPH_SAFE[2] + 1e-6
        band = any(_overlap((box[1], box[3]), b, TXT_GAP / 2) for b in avoid)
        return out or band, face

    best = None
    for x, y in cands:
        x, y = fit(x, y)
        hard, face = bad(x, y)
        if not hard and face <= 1e-9:
            return round(x, 3), round(y, 3)
        key = (hard, face)
        if best is None or key < best[0]:
            best = (key, (round(x, 3), round(y, 3)))
    return best[1]


FACE_W = 640           # 얼굴 찾기용 장면 가로 크기 (썸네일 장면 캐시와 따로 · 임시 폴더에 만들고 지움)
FACE_FRAMES_MAX = 48   # 강조 글씨 자리 찾기에 볼 장면 수 한도 — 넘으면 글씨마다 가운데 한 장면만 (긴 레슨도 가편집이 오래 안 걸리게)
FACE_LABEL = "자동 가편집 만드는 중"


def _face_ready(name):
    """얼굴 모델이 이미 있으면 불러 둠 (없으면 내려받지 않고 False). 부르는 동안 띄운 진행 표시는 원래대로 돌려놓음
    (가편집은 작업(job)이 아니라 바로 답하는 요청이라 진행 표시를 지워 줄 곳이 없음)."""
    try:
        import face
        if not face.ready():
            return False
        before = dict(core.PROGRESS)
        try:
            return bool(face.ensure(item=name, label=FACE_LABEL))
        finally:
            if core.PROGRESS.get("label") == FACE_LABEL:
                core.set_progress(**before)
    except Exception:  # noqa: BLE001
        return False


def _face_samples(items, a, b, one=False):
    """타임라인 a~b 초 동안 볼 장면 [(원본 시각, 확대 배율, 기준점 [x, y])] — 처음·끝 (one 이면 가운데 하나). 원본이 아닌 클립(사진 등)은 뺌."""
    out = []
    for t in (((a + b) / 2,) if one else (a + 0.2, b - 0.2)):
        it = next((it for it in items if it.get("track") == "V1" and float(it["start"]) - 1e-6 <= t < i_end(it)), None)
        if it is None or it.get("media", "main") != "main":
            continue
        src = i_mt(it, t)
        out.append((round(src, 2), float(kf_at(param(it, "scale"), src)) / 100.0, [float(v) for v in param(it, "anchor")["v"]]))
    return out


def _grab_small(name, t, folder):
    """원본 t 초 장면 → folder 안 FACE_W 크기 그림 (못 만들면 None) · 썸네일 장면 캐시(frames/w_*.jpg 1920 · s_*.jpg 원본)와 섞이지 않게 따로."""
    out = Path(folder) / f"f_{t:09.3f}.jpg"
    r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(core.VIDEOS / name), "-frames:v", "1",
                  "-vf", f"scale='min({FACE_W},iw)':-2", "-q:v", "3", str(out)])
    return out if r.returncode == 0 and out.is_file() and out.stat().st_size > 0 else None


def _faces_at(name, srcs, cache):
    """원본 시각들의 얼굴 상자를 cache {시각: [[x, y, w, h]] | None} 에 채움 — 없는 것만, 4장씩 함께 (ffmpeg 장면 뽑기가 대부분의 시간)."""
    todo = sorted({t for t in srcs if t not in cache})
    if not todo:
        return cache
    import face
    from concurrent.futures import ThreadPoolExecutor

    def one(t, folder):
        try:
            p = _grab_small(name, t, folder)
            fs = face.faces(p) if p else None
        except Exception:  # noqa: BLE001 — 장면 하나를 못 봐도 계속
            fs = None
        return t, None if fs is None else [list(f["box"]) for f in fs]
    with tempfile.TemporaryDirectory(prefix="emph_faces_") as folder, ThreadPoolExecutor(4, thread_name_prefix="model-face") as ex:
        for t, fs in ex.map(lambda x: one(x, folder), todo):
            cache[t] = fs
    return cache


def _faces_on_screen(name, items, a, b, cache=None, one=False):
    """타임라인 a~b 초 동안 화면의 얼굴 상자 [[x, y, w, h]] (롱폼 · 클립 확대 반영) — 처음·끝 두 장면 (one 이면 가운데 하나).
    얼굴 모델이 없거나(내려받지 않음) 장면을 하나도 못 보면 None. cache: 장면별로 찾은 얼굴 (emphasis_placer 가 미리 채움)."""
    if not _face_ready(name):
        return None
    cache = {} if cache is None else cache
    smp = _face_samples(items, a, b, one)
    _faces_at(name, [t for t, _, _ in smp], cache)
    out, seen = [], 0
    for t, s, (ax, ay) in smp:
        fs = cache.get(t)
        if fs is None:
            continue
        seen += 1
        for x, y, w, h in fs:  # 기준점 둘레로 키운 자리 (확대 컷 얼굴 기준점 · BR-294 · 없으면 가운데)
            out.append([ax + (x - ax) * s, ay + (y - ay) * s, w * s, h * s])
    return out if seen else None


def emphasis_placer(name, items, fmt, cap_style=None, captions_on=True, hook=None):
    """_emphasis_titles 에 넘길 자리 고르기 (롱폼만) — 그 장면 얼굴(못 보면 흔한 얼굴 자리)·대사 자막 줄·hook(제목 상자)을 피함.
    place.prefetch([(a, b)]) 로 볼 장면을 한꺼번에(4장씩 함께) 찾아 둠 · 장면이 FACE_FRAMES_MAX 넘으면 글씨마다 가운데 한 장면만.
    쇼츠는 영상이 9:16 화면 안 상자(BOX_LAYOUT)에 들어가 얼굴 좌표를 그대로 못 써서 아직 안 받음."""
    if fmt != "long":
        raise ValueError("emphasis_placer: 롱폼만 (쇼츠는 상자 배치 좌표 변환이 없음)")
    W, H = frame_size(fmt)
    avoid = []
    if captions_on and cap_style:
        avoid.append(_cap_band(cap_style, H))
    if hook:
        hb = text_box(hook["text"], hook["style"], W, H)
        avoid.append((hb[1], hb[3]))
    state = {"cache": {}, "one": False}

    # 스타일 가편집(/api/edit/autoseq)은 작업이 아니라 바로 답하는 요청 → 얼굴 모델을 부르고(ensure) 쓰는(faces) 동안
    # 쉬는 동안 내려놓기(idle)가 지우지 않게 idle.using() 으로 감쌈 (지워지면 조용히 흔한 얼굴 자리로 돌아감 · D-068)
    def prefetch(spans):
        spans = list(spans)
        state["one"] = 2 * len(spans) > FACE_FRAMES_MAX
        if not spans:
            return
        with idle.using():
            if _face_ready(name):
                _faces_at(name, [t for a, b in spans for t, _, _ in _face_samples(items, a, b, state["one"])], state["cache"])

    def place(text, st, a, b):
        with idle.using():
            fs = _faces_on_screen(name, items, a, b, cache=state["cache"], one=state["one"])
        return emphasis_spot(text, st, W, H, FACE_GUESS if fs is None else fs, avoid)
    place.prefetch = prefetch
    return place


# 자동 가편집의 소리 크기 목표 범위 — 유튜브는 -14 LUFS 로 맞춰 틀고 작은 소리는 키워 주지 않음 (검수는 -16 밑이면 경고).
# 아래 끝을 -16 이 아니라 -14 로 둔 것은 한 번 맞추기(loudnorm)가 0.6~1.1dB 모자라게 나와도 -16 밑으로 안 가게
# (실측: 목표 -16 → -16.6 · -15 → -16.1 · -14 → -14.7 LUFS — I-056 · 2패스 맞추기가 들어오면 다시 볼 것)
AUTO_LUFS = (-14.0, -13.0)


def auto_lufs(v, nd=1):
    """배운 스타일의 소리 크기(레퍼런스 원본을 잰 값, 예: -27) → 가편집 목표 (-14~-13 · 모르면 -14).
    레퍼런스가 작게 올라가 있어도 우리 영상까지 유튜브에서 8~10dB 작게 들리면 안 되므로 범위 안으로만. nd: 반올림 자리 (None 이면 그대로)."""
    try:
        x = float(v) if v is not None else -14.0
    except (TypeError, ValueError):
        x = -14.0
    x = min(AUTO_LUFS[1], max(AUTO_LUFS[0], x if math.isfinite(x) else -14.0))
    return x if nd is None else round(x, nd)


def default_style_params():
    """첫 가편집에 쓰는 스타일 값 (style.rough_params · 정하지 않았으면 None — 기본 가편집) (E2 · BR-094)."""
    try:
        import style as _style
        n, p = _style.rough_params()
    except Exception:  # noqa: BLE001
        return None
    return dict(p, styleName=n) if p else None


EDGE_LOOK = 0.15   # 가편집 컷 끝·시작에서 아직 소리가 나면 이 초 안에서 바깥쪽 조용한 곳으로 옮김 (말끝·첫소리 잘림 막기 · D-402)
EDGE_QUIET = 0.06  # 조용하다고 볼 소리 크기 (편집실 파형 값 · 그 영상 큰 소리의 약 -24dB)


def _quiet_edges(cuts, wf, units, dur=None):
    """가편집 컷 [{in, out}] 의 끝이 아직 소리가 나는 곳이면 EDGE_LOOK 초 안의 바깥쪽 첫 조용한 곳으로 옮김 (D-402) —
    컷 끝은 늦게, 컷 시작은 일찍만 (말을 더 자르지 않음) · 받아쓴 다른 낱말(units: [(시작, 끝, …)])·옆 컷·파일 끝을 넘지 않음 ·
    조용한 곳을 못 찾으면 그대로. wf: waveform() 결과 (1초에 per_sec 개 · 0~1)."""
    pk, per = (wf or {}).get("peaks") or [], float((wf or {}).get("per_sec") or 0)
    if not pk or per <= 0:
        return cuts
    n = len(pk)
    lvl = [float(x) for x in pk]
    out = [dict(c) for c in cuts]
    for c in out:
        a, b = float(c["in"]), float(c["out"])
        kb = int(b * per)
        if 0 <= kb < n and lvl[kb] > EDGE_QUIET:
            lim = min([b + EDGE_LOOK, n / per] + ([dur] if dur else []) + [u[0] - 0.02 for u in units if u[0] >= b - 0.02]
                      + [float(x["in"]) for x in out if float(x["in"]) >= b - 1e-6 and x is not c])
            k = next((k for k in range(kb + 1, min(n, int(lim * per) + 1)) if lvl[k] <= EDGE_QUIET and (k + 0.5) / per <= lim), None)
            if k is not None:
                c["out"] = round((k + 0.5) / per, 3)
        ka = int(a * per) - 1
        if 0 <= ka < n and lvl[ka] > EDGE_QUIET:
            lim = max([a - EDGE_LOOK, 0.0] + [u[1] + 0.02 for u in units if u[1] <= a + 0.02]
                      + [float(x["out"]) for x in out if float(x["out"]) <= a + 1e-6 and x is not c])
            k = next((k for k in range(ka - 1, max(-1, int(lim * per) - 1), -1) if lvl[k] <= EDGE_QUIET and (k + 0.5) / per >= lim), None)
            if k is not None:
                c["in"] = round((k + 0.5) / per, 3)
    return out


def auto_sequences(name, info, style=None, kinds=("long", "shorts"), rec=None):
    """1차 가편집: 롱폼 군더더기 정리본 + 쇼츠 추천 구간별 편집본.
    rec: 이미 고른 추천({shorts, tidy}) — 끝까지 자동으로가 MSG 를 못 넣은 쇼츠 하나를 가편집으로 만들 때 (없으면 recommend).
    style: style.edit_params() 결과 (말 사이 공백·줌 컷·자막 위치/색·소리 크기·컷 리듬·말 빠르기) — 있으면 그 스타일대로.
    쇼츠·긴 영상을 섞어 배운 스타일(byFormat)이면 롱폼은 긴 영상 값, 쇼츠는 쇼츠 값으로 (E12 · D-102)."""
    by = (style or {}).get("byFormat")
    if isinstance(by, dict) and by:
        sn = {"styleName": style["styleName"]} if style.get("styleName") else {}
        return [q for k in kinds for q in auto_sequences(name, info, dict(by.get(k) or next(iter(by.values())), **sn), (k,), rec)]
    st = style or {}
    rec = rec or recommend(name, keep_pause=st.get("keepPause"))
    segs = _segments_of(name)
    # 화면 크기: 잘라 낸 자리(점프 컷)·나눈 곳마다 뒤집음 — 스타일 zoomScale, 스타일이 없으면 살짝(1.08배)
    every, zoom = float(st.get("zoomEvery") or 0), min(1.6, max(1.0, float(st.get("zoomScale") or SOFT_ZOOM)))
    if every <= 0:  # 컷 리듬 (#7): 확대 컷이 거의 없는 스타일 → 살짝만
        zoom = min(zoom, SOFT_ZOOM)
    try:  # 이 영상의 기본 자막 (편집실이 처음 만드는 것과 같음) — 그 가운데서는 컷을 나누거나 말 빠르기를 바꾸지 않음
        caps = _captions_of(segs, info, silences_of(name))
    except Exception:  # noqa: BLE001 — 용어 사전을 못 읽는 등: 받아쓴 구간 단위로
        caps = None
    master = {"volume": 1.0, "normalize": True, "lufs": auto_lufs(st.get("lufs"))}

    def cap(base):
        c = dict(base)
        if st.get("captionPos") in CAP_Y:
            c["y"] = CAP_Y[st["captionPos"]]
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(st.get("captionColor") or "")):
            c["fill"] = st["captionColor"].upper()
        return c

    # 말 자막 (BR-290): 쇼츠는 늘 켬 (소리 끄고 봄) · 롱폼은 스타일 카드에서 고른 것 — 고르지 않았으면 배운 자막 비율이 낮아도 '작게 켜기'
    # (예전: 배운 자막 비율 25% 밑이면 롱폼·쇼츠 모두 끔)
    lc = st.get("longCaps") if st.get("longCaps") in LONG_CAPS else ("on" if st.get("captions", True) else "small")
    caps_on = lc != "off"
    try:  # 컷 끝·시작이 아직 소리 나는 곳이면 가까운 조용한 곳으로 (D-402) — 소리 크기를 못 읽으면 그대로
        wf = waveform(name)
    except Exception:  # noqa: BLE001
        wf = None
    units = _rhythm_words(segs) or [(float(s["start"]), float(s["end"]), 0) for s in segs]
    seqs = []
    if "long" in kinds:
        tidy = _quiet_edges(rec["tidy"], wf, units, info.get("duration")) or [{"in": 0.0, "out": info["duration"]}]
        # 확대 컷: 시범 장면은 원래 크기 · 기준점은 그 컷 얼굴 상자로 (BR-294)
        items, extra = _items_from_cuts(face_anchors(name, _rhythm(tidy, segs, st, every, caps, calm=True), zoom), zoom=zoom), {}
        tz, em = st.get("introTeaser") or {}, st.get("emphasisTitles") or {}
        titles = []
        if tz.get("on") and float(tz.get("sec") or 0) > 0:  # 기획 분석: 티저형 인트로 스타일
            items, titles = _intro_teaser(items, rec, tidy, min(6.0, max(3.0, float(tz["sec"]))), segs, wf)
        cl = cap(LONG_STYLE)
        if lc == "small":
            cl["size"] = LONG_SMALL
        if float(em.get("perMin") or 0) > 0:  # 기획 분석: 핵심 낱말 강조 자막 (그 장면 얼굴·대사 자막을 피한 자리)
            titles += _emphasis_titles(items, segs, min(4.0, float(em["perMin"])), em.get("color"), titles[0]["dur"] if titles else 0.0,
                                       emphasis_placer(name, items, "long", cl, caps_on), captions_on=caps_on)
        W, H = frame_size("long")
        for t in titles:  # 위쪽 자막 스타일: 티저 제목이 대사 자막과 같은 자리면 제목을 자막 아래로
            tb = text_box(t["text"], t["style"], W, H)
            if t.get("plan") == "teaser" and _overlap(_cap_band(cl, H), (tb[1], tb[3]), TXT_GAP):
                t["style"]["y"] = math.ceil((_cap_band(cl, H)[1] + TXT_GAP + (tb[3] - tb[1])) * 1000 - 1e-6) / 1000
        if titles:
            extra["titles"] = titles
        seqs.append(_new_seq("롱폼 가편집", "long", items, captionStyle=cl,
                             layout={"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0},
                             master=dict(master), captionsOn=caps_on, **extra))
    if "shorts" in kinds:
        card = shorts_card_on()  # 스타일 화면 '쇼츠 화면 배치' (기본 지금처럼)
        ep_lay = ep_layout() if card else None   # 띠 그림·로고 (스타일 화면에서 켠 것 · BR-280 · BR-281)
        sk_lay = skin_layout(info=info) if not card and shorts_skin_on() else None   # 풋살사관학교 스킨 (BR-320 · 원본 비율로 창을 덮음 · 못 읽으면 지금처럼)
        sk_win = skin_window(sk_lay["skin"]) if sk_lay else None
        sk_vt = video_topic(name, segs) if sk_lay else ""   # 스킨 큰 제목이 길면 대신 쓸 영상 주제 (skin_headline · D-531)
        for i, r in enumerate(rec["shorts"], 1):
            # 확대 컷 기준점·시범 장면 (BR-294) — 은평 스타일 쇼츠는 영상 창 배치가 달라 그대로
            cuts = _rhythm(_quiet_edges(r["cuts"], wf, units, info.get("duration")), segs, st, every, caps, calm=not card)
            if not card:
                cuts = face_anchors(name, cuts, zoom)
            items = _items_from_cuts(cuts, 0.3, zoom=zoom)
            length = max([i_end(it) for it in items] or [0.0])  # 말 빠르기를 맞추면 조금 짧아짐
            hook = {"id": _nid(), "text": _hook(r), "start": 0.0, "dur": round(length, 2), "style": dict(TITLE_STYLE)}
            cs = cap(SHORTS_STYLE)
            if card:  # 은평 스타일 (BR-240): 흰 상자 큰 제목 · 그 아래 사실 줄 · 말 자막은 아래 띠
                cs = ep_caption(cs)
                kt = {"id": _nid(), "text": r["topic"], "plan": "topic"} if r.get("topic") else None
                shapes = ep_card(hook, kt, length)
                xt, xg = ep_extras(length, ahead=i - 1, logo=bool(ep_lay.get("logo")))   # 시리즈 이름표·전체 강좌 줄 (고른 것만 · BR-245 · BR-246)
                ep_cap_clear(cs, xt, bool(ep_lay.get("logo")))   # 말 자막은 전체 강좌 줄·로고 위로 (D-428 · D-472)
                seqs.append(_new_seq(_short_name(i, r), "shorts", items, captionStyle=cs, layout=dict(ep_lay), master=dict(master),
                                     captionsOn=True, titles=[hook] + ([kt] if kt else []) + xt, shapes=shapes + xg))
                continue
            if sk_lay:  # 풋살사관학교 스킨 (BR-321 · BR-322): 흰 둥근 상자 주황 큰 제목 · 그 아래 흰 주제 줄 · 말 자막은 영상 창 아래 끝 바로 위
                cs = skin_caption(cs, sk_win["bottom"])
                kt = {"id": _nid(), "text": r["topic"], "plan": "topic"} if r.get("topic") else None
                shapes, kt = skin_card(hook, kt, length, sk_win, alts=[(r.get("point") or {}).get("phrase"), sk_vt])
                faces = skin_reframe(name, items, sk_lay)   # 화면 좌우는 얼굴을 따라 · 확대 기준점은 창 좌표로 (BR-326)
                titles = [hook] + ([kt] if kt else [])
                em = st.get("emphasisTitles") or {}
                if float(em.get("perMin") or 0) > 0:  # 배운 강조 큰 글씨도 (창 안 · 위 띠·말 자막·얼굴을 피함 · BR-327 · D-534)
                    titles += _emphasis_titles(items, segs, 0.0, em.get("color"), TOPIC_SEC,
                                               skin_emph_placer(items, {"format": "shorts", "layout": sk_lay, "info": info}, cs, sk_win, faces),
                                               captions_on=True, most=1 if length < SHORTS_EMPH_TWO else 2,
                                               gap=min(EMPH_GAP, max(6.0, length / 3)), avoid=[t["text"] for t in titles])
                seqs.append(_new_seq(_short_name(i, r), "shorts", items, captionStyle=cs, layout=dict(sk_lay), master=dict(master),
                                     captionsOn=True, titles=titles, shapes=shapes))
                continue
            W, H = frame_size("shorts")
            tb = text_box(hook["text"], hook["style"], W, H)
            if _overlap(_cap_band(cs, H), (tb[1], tb[3]), TXT_GAP):  # 위쪽 자막 스타일: 쇼츠 내내 떠 있는 훅 제목 아래(영상 위 칸)로
                cs["y"] = _stack_below(tb[3], cs, H)
            titles, kt = [hook], None
            if r.get("topic"):  # 첫머리 주제 줄 (BR-120 · 위 큰 제목 바로 위 · 자리가 없거나 대사 그대로인 큰 제목이면 처음 2.5초 바꿔 끼우기)
                kt = topic_title(r["topic"], hook, cs, length, said=bool(r.get("hookSaid")),
                                 vtop=video_top(BOX_LAYOUT, items, info.get("width"), info.get("height")))
                if kt:
                    swap_in(hook, kt)
                    titles.append(kt)
            em = st.get("emphasisTitles") or {}
            if float(em.get("perMin") or 0) > 0:  # 강조 큰 글씨 1~2개 (콕 집은 말의 핵심 구절 먼저 · 인사·끝인사 빼고 · BR-293)
                titles += _emphasis_titles(items, segs, 0.0, em.get("color"), TOPIC_SEC, shorts_emph_placer(items, hook, cs, info),
                                           captions_on=True, most=1 if length < SHORTS_EMPH_TWO else 2,
                                           gap=min(EMPH_GAP, max(6.0, length / 3)), avoid=[hook["text"]] + ([kt["text"]] if kt else []))
            seqs.append(_new_seq(_short_name(i, r), "shorts", items, captionStyle=cs,
                                 layout=dict(BOX_LAYOUT), master=dict(master), captionsOn=True, titles=titles))
    for q in seqs:  # 자동으로 만든 가편집 표시 ('가편집 다시 만들기'는 이것만 바꿈 · 스타일 가편집은 app.py 에서 따로 표시)
        q["auto"] = "style" if style and not (style or {}).get("styleName") else "rough"
        if (style or {}).get("styleName"):  # 첫 가편집에 쓰는 스타일로 만든 1차 가편집 (E2) — 다시 만들기 대상 그대로
            q["style"] = style["styleName"]
    return seqs


LONG_SMALL = 44          # 롱폼 말 자막 '작게 켜기' 글자 크기 (보통 LONG_STYLE 52 · BR-290)
LONG_CAPS = ("on", "small", "off")   # style.LONG_CAPS 와 같음
SHORTS_EMPH_TWO = 30.0   # 쇼츠가 이 초 이상이면 강조 큰 글씨 2개까지 (짧으면 1개 · BR-293)
SHORTS_EMPH_SIZES = (80, 72, 64)   # 쇼츠 강조 큰 글씨 크기 — 가로 1080 에 한 줄로 들어가는 가장 큰 것 (롱폼 88)


def shorts_emph_placer(items, hook, cap_style, info):
    """쇼츠(위아래 박스 배치) 강조 큰 글씨 자리 — 먼저 영상 창 바로 아래 · 위 큰 제목·말 자막 줄·영상 속 흔한 얼굴 자리(FACE_GUESS)는 피함 (BR-293).
    (x, y) 를 돌려줌 · 글자 크기는 한 줄로 들어가는 SHORTS_EMPH_SIZES (place.size(글) · 같은 셈)."""
    W, H = frame_size("shorts")
    sw, sh = float(info.get("width") or 1920), float(info.get("height") or 1080)
    ch = min(H, sh * W / sw)
    by = (H - ch) * float(BOX_LAYOUT["vpos"])
    hb = text_box(hook["text"], hook["style"], W, H)
    avoid = [(hb[1], hb[3]), _cap_band(cap_style, H)]
    faces = [[f[0], (by + f[1] * ch) / H, f[2], f[3] * ch / H] for f in FACE_GUESS]

    def size(text, st):
        return next((z for z in SHORTS_EMPH_SIZES if _one_line(text, dict(st, size=z), W)), SHORTS_EMPH_SIZES[-1])

    def place(text, st, a, b):
        st = dict(st, size=size(text, st))
        bh = text_box(text, dict(st, y=0.5), W, H)
        bottom = by + ch
        for it in items:  # 확대 컷이면 영상 아래 끝도 그만큼 내려옴 (기준점 둘레로 키움)
            if it.get("track") == "V1" and float(it["start"]) < b and i_end(it) > a:
                sc = float(param(it, "scale")["v"]) / 100
                if sc > 1:
                    ay = float(param(it, "anchor")["v"][1])
                    bottom = max(bottom, by + (ay + (1 - ay) * sc) * ch)
        st["y"] = math.ceil((min(H, bottom) / H + TXT_GAP + (bh[3] - bh[1])) * 1000 - 1e-6) / 1000
        return emphasis_spot(text, st, W, H, faces, avoid)
    place.size = size
    return place


def _short_name(i, r):
    return f"쇼츠 {i} · {r['title'][:14]}"


def _segments_of(name):
    """받아쓰기 (글이 있는 구간 · 지어낸 낱말은 뺌 — captions.drop_hallucinations)."""
    return _clean_transcript(name)[0]


def migrate_seq(s):
    """v1(클립 한 줄) → v2(멀티 트랙). 예전 프로젝트도 그대로 열리게."""
    if s.get("v") == 2 and "items" in s:
        s.setdefault("trans", [])
        s.setdefault("markers", [])
        s.setdefault("duck", {"on": True, "amount": -14.0})
        return s
    items, pos = [], 0.0
    for c in s.pop("clips", []) or []:
        items += _pair(c["in"], c["out"], vol=c.get("volume", 1.0), fade_in=c.get("fadeIn", 0.0), fade_out=c.get("fadeOut", 0.0),
                       mute=c.get("mute", False), start=pos, reframe=c.get("reframe", 0.5), vid=c.get("id"))
        pos += c["out"] - c["in"]
    s.update(v=2, tracks=default_tracks(), items=items, trans=[], markers=s.get("markers") or [])
    s.setdefault("duck", {"on": True, "amount": -14.0})
    s.setdefault("titles", [])
    s.setdefault("shapes", [])
    s.setdefault("master", {"volume": 1.0, "normalize": True})
    return s


def migrate_project(name, proj):
    if "sequences" not in proj:  # v1.3.0 저장본 → 시퀀스 구조로 변환
        seq = {"id": _nid(), "name": "시퀀스 1"}
        for k in ("format", "clips", "captionsOn", "captionStyle", "titles", "master"):
            if k in proj:
                seq[k] = proj.pop(k)
        seq.setdefault("format", "shorts")
        proj["sequences"] = [seq] + auto_sequences(name, proj["info"])
        proj["active"] = seq["id"]
    bare = [s for s in proj["sequences"] if "auto" not in s]
    if bare:  # 예전 프로젝트엔 자동 가편집 표시가 없음 → 처음 만든 이름 그대로인 것만 자동, 복사본·이름 바꾼 것·직접 만든 것은 내 것
        auto = {"롱폼 가편집"}
        if any(str(s.get("name", "")).startswith("쇼츠 ") for s in bare):
            try:
                auto |= {_short_name(i, r) for i, r in enumerate(recommend(name)["shorts"], 1)}
            except Exception:
                pass
        for s in bare:
            s["auto"] = "rough" if s.get("name") in auto else "user"
    if "speech" not in proj:  # E2 검토: 받아쓴 말 자리 (배경음악 줄이기) — 예전 프로젝트는 받아쓰기에서 한 번 만듦
        try:
            proj["speech"] = speech_words(_segments_of(name))
        except Exception:  # noqa: BLE001 — 받아쓰기를 못 읽어도 프로젝트는 열림 (자막만으로)
            proj["speech"] = []
    for s in proj["sequences"]:
        migrate_seq(s)
        lm = {}  # 자르기를 반복해 길어진 연결 표시(link)를 짧게 (같은 것끼리는 계속 같게)
        for it in s.get("items", []):
            ln = it.get("link")
            if ln and len(ln) > 16:
                it["link"] = lm.setdefault(ln, _nid())
    media = proj.setdefault("media", [])
    refresh_sfx_lengths([s.get("items") or [] for s in proj["sequences"]], media)   # 다시 만든 효과음 길이 (D-379)
    if not any(m["id"] == "main" for m in media):
        i = proj["info"]
        media.insert(0, {"id": "main", "kind": "video", "src": "videos", "file": name, "dur": i["duration"], "w": i["width"],
                         "h": i["height"], "fps": i.get("fps", 30.0), "audio": _has_audio(name)})
    for m in media:  # 미리보기 파일이 있는지 매번 다시 확인
        if m.get("kind") == "video":
            try:
                m["proxy"] = proxy_path(m["file"], m.get("src", "videos")).exists()
            except ValueError:
                m["proxy"] = False
            t0 = media_t0(m.get("file") or "", m.get("src", "videos"))  # 시작 시각이 0 보다 큰 파일만 (D-324 · 보통 파일은 칸 없음 = 예전 그대로)
            if t0 > 0.005:  # 0 보다 작은 시작(.webm·Opus -0.007 등)은 브라우저가 0 으로 당겨 읽음 — 예전에 적힌 음수 t0 도 여기서 지움
                m["t0"] = round(t0, 3)
            else:
                m.pop("t0", None)
    proj["v"] = 2
    return proj


# 프로젝트 저장: 한 번에 하나씩(잠금) · 임시 파일에 다 쓴 뒤 바꿔 끼우기(중간에 꺼져도 깨지지 않음) · 판(rev) 번호로 다른 창 덮어쓰기 방지
_SAVE_LOCK = threading.RLock()
_REVC = {}
_WRITER = {}  # 파일별 마지막으로 저장한 편집실 창: (창 id, 그 창이 이어서 저장하기 시작한 판, 마지막 판, 편집 번호)
AUTO_CLIENT = "auto"  # 끝까지 자동으로(autorun.add_to_project)가 편집본을 더할 때의 저장 표시 (화면 창 id 로는 못 씀)
_AUTO_ADDS = {}  # 파일별 끝까지 자동으로가 더한 것 [(판, 편집본 id 들, 미디어 id 들, 바꾸기 전 자막 글 또는 None)] (최근 AUTO_ADDS_MAX 개 · D-207)
AUTO_ADDS_MAX = 40


class Conflict(Exception):
    def __init__(self, rev):
        super().__init__("다른 창에서 이 편집본이 바뀌었어요")
        self.rev = rev


def _read_json(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _replace_retry(src, dst, secs=None):
    """Windows: 백신·OneDrive·탐색기가 잠깐 잡고 있으면 실패 → 점점 길게 기다리며 다시 (updater.replace_retry)."""
    updater.replace_retry(src, dst, secs)


def _disk_rev(p):
    try:
        st = p.stat()
    except OSError:
        return 0
    c = _REVC.get(str(p))
    if c and c[0] == st.st_mtime_ns:
        return c[1]
    try:
        r = int(_read_json(p).get("rev") or 0)
    except Exception:
        return -1  # 깨진 파일 → 덮어써도 됨
    _REVC[str(p)] = (st.st_mtime_ns, r)
    return r


BK_TAGS = {"재분석전": "편집점 다시 찾기 전", "덮어쓰기전": "다른 창 내용으로 덮어쓰기 전", "변환전": "새 형식으로 바꾸기 전",
           "저장못함": "저장하지 못한 창 내용"}


def rescue_copy(name, proj):
    """편집실 저장이 계속 안 될 때 '편집본 파일로 내려받기' (D-412): 그 창의 내용을 저장 못 한 편집본 파일 대신 다른 곳에 —
    자동 백업('기록' 탭에서 되돌릴 수 있음 · 표시 '저장하지 못한 창 내용')과 다운로드 폴더 둘 다, 둘 다 안 되면 완성본 폴더 → 쓴 파일 [경로].
    한 곳도 못 쓰면 마지막 OSError (디스크가 가득 차 있으면 어디에도 못 씀 → 화면이 그 기기로 내려받기를 한 번 더 해 봄)."""
    if not isinstance(proj, dict) or not isinstance(proj.get("sequences"), list):
        raise ValueError("편집본 내용이 아니에요")
    stem, ts = _ppath(name).stem, time.strftime("%Y%m%d_%H%M%S")
    data = core.clean_text(json.dumps(proj, ensure_ascii=False))
    out, err = [], None
    for d, fn, last in ((PROJECTS / "backup", f"{stem}__{ts}_저장못함.json", False), (Path.home() / "Downloads", f"{stem}_편집본_{ts}.json", False),
                        (core.OUT, f"{stem}_편집본_{ts}.json", True)):
        if last and out:
            break
        try:
            d.mkdir(parents=True, exist_ok=True)
            updater.write_atomic(d / fn, data)
            out.append(d / fn)
        except OSError as e:
            err = e
    if not out:
        raise err
    try:
        _prune_backups(stem)
    except OSError:
        pass
    return out


def _backup_files(stem):
    """이 프로젝트의 백업 [(시각, 표시, 경로)] — 이름이 [꿀팁] 처럼 대괄호를 가져도 정확히 (glob 를 쓰지 않음)."""
    bdir = PROJECTS / "backup"
    if not bdir.is_dir():
        return []
    pre, out = stem + "__", []
    for f in bdir.iterdir():
        n = f.name
        if n.startswith(pre) and n.endswith(".json"):
            m = re.fullmatch(r"(\d{8}_\d{6})(?:_(.+))?", n[len(pre):-5])
            if m:
                out.append((m[1], m[2] or "", f))
    return sorted(out, key=lambda x: (x[0], x[1]))


def _bk_epoch(ts):
    try:
        return time.mktime(time.strptime(ts, "%Y%m%d_%H%M%S"))
    except ValueError:
        return 0.0


def _backup_copy(p, tag=""):
    bdir = PROJECTS / "backup"
    bdir.mkdir(exist_ok=True)
    dst = bdir / f"{p.stem}__{time.strftime('%Y%m%d_%H%M%S')}{'_' + tag if tag else ''}.json"
    shutil.copy2(p, dst)
    return dst


def _prune_backups(stem):
    fs = _backup_files(stem)
    for f in [x[2] for x in fs if not x[1]][:-10] + [x[2] for x in fs if x[1]][:-10]:
        f.unlink(missing_ok=True)


def _recover(p):
    """깨졌거나 없어진 프로젝트를 가장 최근의 멀쩡한 사본으로."""
    cands = [(p.with_name(p.name + ".tmp"), "저장 중이던 파일")]
    cands += [(f, f"{ts[4:6]}월 {ts[6:8]}일 {ts[9:11]}:{ts[11:13]} 백업") for ts, _, f in reversed(_backup_files(p.stem))]
    for f, label in cands:
        try:
            d = _read_json(f)
            if isinstance(d, dict) and ("sequences" in d or "info" in d):
                return d, label
        except Exception:
            continue
    return None, None


def _wt(ws):
    """단어 시각을 작게: 1/100초 정수 (첫 단어 시작, 그 뒤로는 앞 값과의 차이)."""
    cs = [int(round(float(x) * 100)) for w in ws for x in (w["s"], w["e"])]
    return cs[:1] + [b - a for a, b in zip(cs, cs[1:])]


def _cap_fmt(info):
    return "shorts" if (info or {}).get("height", 0) > (info or {}).get("width", 0) else "long"


def _make_cap(part, fmt, terms, base=None):
    """한 줄 자막 하나 → 편집실 자막 {id, start, end, text(, wt, sh, shn)} · base 의 다른 값(위치·모양 등)은 그대로.
    가로 영상 자막은 쇼츠 편집본에서 쓸 쇼츠형 나눌 곳 sh(몇 번째 낱말부터 새 자막인지)와 그때 낱말 수 shn 도 함께."""
    cap = {k: v for k, v in (base or {}).items() if k not in ("wt", "sh", "shn", "words")}
    cap.update(id=cap.get("id") or _nid(), start=round(float(part["start"]), 3), end=round(float(part["end"]), 3), text=part["text"])
    ws = part.get("words")
    if ws:
        cap["wt"] = _wt(ws)
    if fmt == "long":
        sh = _shorts_cuts(part["text"], part["start"], part["end"], terms, ws)
        if sh:
            cap["sh"], cap["shn"] = sh, len(part["text"].split())
    return cap


def _shorts_cuts(text, start, end, terms, ws=None):
    """가로 영상 자막을 쇼츠형 한 줄로 나눌 곳 sh (몇 번째 낱말부터 새 자막인지) — 안 나눠도 되면 [].
    단어 시각이 없으면 _shorts_parts·capParts 가 보여 줄 때와 같은 시각(captions.weight 비율 · 조용한 곳 없이)으로 골라서
    고를 때 확인한 길이(2.2초까지·0.6초 넘게)가 화면에서도 그대로."""
    sub = captions.split_text(text, start, end, "shorts", terms, words=ws)
    sh, k = [], 0
    for q in sub[:-1]:
        k += len(q["text"].split())
        sh.append(k)
    return sh


def _terms():
    return captions.load_dict(core.dict_path())["terms"]


def silences_of(name):
    try:
        return json.loads((core.adir(name) / "analysis.json").read_text(encoding="utf-8")).get("silences") or []
    except (OSError, ValueError, AttributeError):
        return []


def _captions_of(segs, info, silences=()):
    """받아쓰기 → 편집실 자막 (모두 한 줄). 세로 영상은 쇼츠형(13글자·2.2초), 가로는 롱폼형(17글자·3초) ·
    용어 사전의 여러 단어 용어는 안 나눔 · 단어 시각이 없으면(예전 받아쓰기) 띄어쓰기에서 나누고 시간은 글자 수대로 (silences: 조용한 곳).
    단어 시각은 작게 wt (1/100초 정수: 첫 단어 시작, 그 뒤로는 앞 값과의 차이 · 낱말은 자막 글 그대로) · 가로 영상 자막은 쇼츠 편집본에서 쓸
    쇼츠형 나눌 곳 sh 도 함께."""
    fmt, terms = _cap_fmt(info), _terms()
    return [_make_cap(c, fmt, terms) for c in captions.from_segments(segs, fmt, terms, silences)]


def oneline_captions(caps, info, silences=(), terms=None, src_of=None):
    """편집실 '자막 한 줄씩 나누기': 프로젝트 자막을 모두 한 줄 자막으로 → (새 자막 목록, 나눈 자막 수).
    지금 글(사용자가 고친 글) 그대로 나누고, 단어 시각(wt)이 글과 맞으면 그 시각대로, 아니면 글자 수대로 (silences: 조용한 곳).
    길지 않아도 따로 한 말이 한 자막에 든 것('혼난다. 시작!' · 단어 시각이 있으면 말 경계 · 없으면 글 속 . ? !)도 나눔 (D-387) —
    단, 손으로 맞추거나 쓴 자막(tm · SRT · Shift+T)과 손으로 나누거나 합친 자막(ms · Ctrl+K · Ctrl+Enter · Ctrl+J)은 길 때만 (D-387 검토 고침).
    이미 한 줄이고 짧은 자막은 그대로 (두 번 눌러도 같음) · 나뉜 첫 자막은 원래 id, 다른 값(위치·모양 등)은 나뉜 자막마다 그대로.
    가로 영상 자막은 쇼츠 편집본에서 나눌 곳(sh)도 지금 규칙으로 다시 (글·단어 시각은 그대로 · tm·ms 자막은 쇼츠 한 줄에 안 들 때만).
    src_of(dict)를 주면 새 id 를 받은 조각 → 원래 자막 id 를 적어 줌 (편집본마다 둔 '이 자막만' 자리 capPos 를 조각마다 옮기게 · D-321)."""
    fmt = _cap_fmt(info)
    terms = _terms() if terms is None else terms
    out, n = [], 0
    for c in sorted((c for c in caps or () if isinstance(c, dict)), key=lambda c: float(c.get("start") or 0)):
        text = str(c.get("text") or "")
        ws = _cap_words(c)
        hand = _cap_hand(c)  # 손으로 맞춘·쓴·나눈·합친 자막: 따로 한 말 규칙으로는 안 나눔 (길 때만 · D-387)
        if not captions.needs_split(text, fmt) and (hand or not captions.merged_utterances(text, ws, terms)):
            if fmt == "long" and text.strip() and len(text.split()) > 1:  # 쇼츠 편집본에서 나눌 곳만 새로 (글·단어 시각·줄바꿈은 그대로)
                n_tok = len(text.split())
                try:
                    if hand and isinstance(c.get("sh"), list) and c.get("sh") and c.get("shn") == n_tok:
                        sh = None  # 손으로 나누거나 합칠 때 정한 쇼츠 나눔 그대로
                    elif hand and not captions.needs_split(text, "shorts"):
                        sh = []  # 쇼츠 한 줄에 들면 안 나눔 ('혼난다. 시작!' 을 Ctrl+J 로 합친 것)
                    else:
                        sh = _shorts_cuts(text, float(c["start"]), float(c["end"]), terms, ws)
                except (TypeError, ValueError, KeyError):
                    sh = None
                if sh is not None and (sh != (c.get("sh") or []) or (sh and c.get("shn") != len(text.split()))):
                    c = {k: v for k, v in c.items() if k not in ("sh", "shn")}
                    if sh:
                        c.update(sh=sh, shn=len(text.split()))
            out.append(c)
            continue
        try:
            parts = captions.split_text(text, c["start"], c["end"], fmt, terms, words=ws, silences=() if ws else silences)
        except (TypeError, ValueError, KeyError):
            parts = []
        if len(parts) < 2:  # 나눌 수 없음 (길이가 없거나 거꾸로 된 자막 · 낱말 하나) → 그대로
            out.append(c)
            continue
        n += 1
        for k, q in enumerate(parts):
            out.append(_make_cap(q, fmt, terms, dict(c, id=c.get("id") if k == 0 else None)))
            if k and src_of is not None and c.get("id"):
                src_of[out[-1]["id"]] = c["id"]
    return out, n


def long_captions(caps, info):
    """한 줄로 나누면 좋을 자막 수 (편집실이 열 때 '한 줄씩 나눌까요?' 안내를 띄울지)."""
    fmt = _cap_fmt(info)
    return sum(1 for c in caps or () if isinstance(c, dict) and captions.needs_split(c.get("text"), fmt))


def _cap_hand(c):
    """손으로 맞추거나 쓴 자막(tm · SRT · Shift+T · 시간 고침) · 손으로 나누거나 합친 자막(ms · Ctrl+K · Ctrl+Enter · Ctrl+J · D-384 · D-386)
    — '자막 다시 나누기'·'한 줄씩 나누기'가 따로 한 말 규칙으로 다시 나누거나 이웃과 다시 묶지 않음."""
    return bool(c.get("tm") or c.get("ms"))


def merged_captions(caps):
    """따로 한 말이 한 자막에 합쳐진 자막 수 (편집실이 열 때 '자막 다시 나누기' 안내를 띄울지 · D-387) — 손으로 맞추거나 나누거나 합친 자막(tm · ms)은 뺌."""
    n = 0
    for c in caps or ():
        if isinstance(c, dict) and not _cap_hand(c):
            try:
                n += captions.merged_utterances(c.get("text"), _cap_words(c))
            except (TypeError, ValueError, KeyError):
                continue
    return n


def _seg_end_times(segs):
    """받아쓰기 구간마다 마지막 낱말 끝 시각(1/100초) — 자막 단어 중 받아쓰기 구간 끝을 찾을 때 (자막 다시 나누기)."""
    out = set()
    for s in segs or ():
        ws = [w for w in s.get("words") or () if isinstance(w, dict) and str(w.get("w") or "").strip()]
        if ws:
            try:
                out.add(int(round(float(ws[-1]["e"]) * 100)))
            except (TypeError, ValueError, KeyError):
                continue
    return out


def resplit_captions(caps, info, ids=None, terms=None, src_of=None, seg_ends=()):
    """편집실 '자막 다시 나누기' (D-380 · D-387): 고른 자막(ids · None 이면 모두)을 새 규칙(따로 한 말은 따로 자막)으로 다시 나눔
    → (새 자막 목록, 바뀐 묶음 수). 같은 자막 트랙에서 차례로 이어지는 자막들(단어 시각이 글과 맞고 손으로 시간을 맞추지 않은 것)의
    낱말을 이어 받아쓴 자막을 만들 때와 같은 규칙(captions.segment · 그 뒤 _tidy)으로 다시 나눔 — 고친 글·단어 시각 그대로, 짧은 대답도 따로,
    한 말은 길이 한도 안에서 다시 묶음. 단어 시각이 없는 자막은 '자막 한 줄씩 나누기'처럼 글 속 문장부호에서만 나누고
    (oneline_captions) 그 앞뒤로는 잇지 않음 · 손으로 맞추거나 나누거나 합친 자막(tm · ms · D-387)은 그대로 두고 잇지 않음 ·
    시간이 겹쳐 둔 자막(두 사람이 겹쳐 말함)은 낱말을 섞지 않게 따로 묶음. 조각은 첫 낱말이 있던 자막의 다른 값(트랙·모양 등)과 id 를 이어받고(이미 쓴 id 면 새 id ·
    src_of[새 id] = 원래 id), 가로 영상 자막은 쇼츠 편집본에서 나눌 곳(sh)도 새로. 두 번 눌러도 같음.
    seg_ends: 받아쓰기 구간 끝 낱말의 끝 시각(1/100초 정수 · _seg_end_times) — 그 낱말 뒤를 조금 쉬었으면 말 경계로 봄."""
    fmt = _cap_fmt(info)
    terms = _terms() if terms is None else terms
    allc = [c for c in caps or () if isinstance(c, dict)]
    want = None if ids is None else {str(i) for i in ids}
    used, out, n = set(), [], 0
    by_trk = {}
    for c in allc:
        trk = c.get("trk") if isinstance(c.get("trk"), str) and re.fullmatch(r"C[2-9]", c.get("trk")) else "C1"
        by_trk.setdefault(trk, []).append(c)

    def take(c, words=None):
        if words is None:
            words = _cap_words(c)
        ok = (want is None or str(c.get("id")) in want) and not _cap_hand(c) and words is not None
        return ok, words

    for trk, lane in by_trk.items():
        lane.sort(key=lambda c: (float(c.get("start") or 0), float(c.get("end") or 0)))
        chains, cur = [], []
        for c in lane:
            ok, ws = take(c)
            if ok:
                if cur and float(c.get("start") or 0) < max(float(x.get("end") or 0) for x, _ in cur) - 0.05:
                    chains.append(cur)  # 앞 자막과 시간이 겹침 (두 사람이 겹쳐 말함): 낱말을 섞지 않게 여기서 끊음
                    cur = []
                cur.append((c, ws))
                continue
            if cur:
                chains.append(cur)
                cur = []
            if (want is None or str(c.get("id")) in want) and not _cap_hand(c):  # 단어 시각이 없는 자막: 글 속 문장부호에서만 (한 줄씩 나누기와 같음)
                part, k = oneline_captions([c], info, terms=terms, src_of=src_of)
                n += k
                out.extend(part)
                used.update(x.get("id") for x in part)
            else:
                out.append(c)
                used.add(c.get("id"))
        if cur:
            chains.append(cur)
        for k, ch in enumerate(chains):
            flat, owner = [], []
            for c, ws in ch:
                for w in ws:
                    flat.append({"w": w["w"], "s": float(w["s"]), "e": float(w["e"])})
                    owner.append(c)
            order = sorted(range(len(flat)), key=lambda i: (flat[i]["s"], i))  # 살짝(0.05초 안) 겹친 낱말도 시간 순으로
            flat, owner = [flat[i] for i in order], [owner[i] for i in order]
            ends = {i for i, w in enumerate(flat) if int(round(w["e"] * 100)) in seg_ends}
            parts = captions.segment(flat, fmt, ends, terms)
            lo = min(float(c["start"]) for c, _ in ch)
            hi = max(float(c["end"]) for c, _ in ch)
            nxt = [float(c["start"]) for c in lane if float(c["start"]) >= hi - 1e-6 and all(c is not x for x, _ in ch)]
            parts = [dict(q) for q in parts]
            if parts:
                parts[0]["start"] = min(parts[0]["start"], lo)
                parts[-1]["end"] = max(parts[-1]["end"], hi)
            captions._tidy(parts, min(nxt) if nxt else float("inf"))
            if [(q["text"]) for q in parts] != [str(c.get("text") or "") for c, _ in ch]:
                n += 1
            i = 0
            for q in parts:
                base = owner[i]
                i += len(q["words"])
                cid = base.get("id")
                fresh = not cid or cid in used
                cap = _make_cap(q, fmt, terms, dict(base, id=None if fresh else cid))
                cap["start"], cap["end"] = round(float(q["start"]), 3), round(float(q["end"]), 3)
                if fresh and src_of is not None and cid:
                    src_of[cap["id"]] = cid
                used.add(cap["id"])
                out.append(cap)
    out.sort(key=lambda c: float(c.get("start") or 0))
    return out, n


def load_project(name):
    p = _ppath(name)
    with _SAVE_LOCK:
        proj, recovered = None, None
        if p.exists():
            try:
                proj = _read_json(p)
                if not isinstance(proj, dict) or ("sequences" not in proj and "info" not in proj):
                    raise ValueError("프로젝트 형식이 아니에요")
            except (ValueError, OSError, UnicodeDecodeError):
                try:  # 깨진 파일은 지우지 않고 옆에 남겨 둠
                    os.replace(p, p.with_name(f"{p.stem}.손상-{time.strftime('%Y%m%d_%H%M%S')}.json.bad"))
                except OSError:
                    pass
                proj, recovered = _recover(p)
                if proj is None:
                    raise RuntimeError("프로젝트 파일이 손상됐고 백업도 없어요")
        elif p.with_name(p.name + ".tmp").exists():
            proj, recovered = _recover(p)
        if proj is not None:
            was = proj.get("v")
            if was != 2 and p.exists():
                _backup_copy(p, "변환전")
            proj = migrate_project(name, proj)
            if was != 2 or recovered:
                save_project(name, proj)
            if recovered:
                proj["_recovered"] = recovered
            return proj
    info = media_info(name)
    segs = _segments_of(name)  # 받아쓰기 헛것은 뺀 것 (E2)
    seqs = auto_sequences(name, info, default_style_params())
    proj = {
        "source": name,
        "info": info,
        "captions": _captions_of(segs, info, silences_of(name)),
        "speech": speech_words(segs),
        "sequences": seqs,
        "active": seqs[0]["id"],
    }
    proj = migrate_project(name, proj)
    save_project(name, proj)
    return proj


def _cap_text(cs):
    """자막 글 묶음 서명 — 글 + 자막 줄(trk · C1 이면 없음) + 손으로 맞춘 시간(tm · D-322). 줄만 옮기거나 시간만 고친 자막도 '고친 자막'으로 봄
    → 자동 작업(다시 들은 받아쓰기)이 몰래 바꾸지 않음 (autorun.add_to_project · editor.html msgCaps 와 같은 서명 · D-296)."""
    return "\n".join(str(c.get("text") or "") + ("\t" + str(c["trk"]) if c.get("trk") else "") + ("\t시간" if c.get("tm") else "")
                     for c in cs or [] if isinstance(c, dict))


def _auto_diff(p, proj):
    """끝까지 자동으로가 이번 저장으로 더하는 것 → (편집본 id 들, 미디어 id 들, 바꾸기 전 자막 글 또는 None)."""
    try:
        old = _read_json(p)
    except (OSError, ValueError):
        old = {}
    old = old if isinstance(old, dict) else {}
    have_s = {q.get("id") for q in old.get("sequences") or [] if isinstance(q, dict)}
    have_m = {m.get("id") for m in old.get("media") or [] if isinstance(m, dict)}
    seqs = [q.get("id") for q in proj.get("sequences") or [] if isinstance(q, dict) and q.get("id") not in have_s]
    media = [m.get("id") for m in proj.get("media") or [] if isinstance(m, dict) and m.get("id") not in have_m]
    was = _cap_text(old.get("captions"))
    return seqs, media, (was if was != _cap_text(proj.get("captions")) else None)


def _merge_auto(p, proj, adds, merged):
    """편집실 창이 모르는 사이 끝까지 자동으로가 더한 편집본·미디어를 이 창 내용에 붙임 (창이 지운 것은 그대로 · 그 판 뒤에 더한 것만).
    자막은 자동 작업이 바꾸기 전 글 그대로일 때만 새 자막으로 (autorun.add_to_project 와 같은 규칙). merged: 붙인 것 (창에 알려 줌)."""
    try:
        disk = _read_json(p)
    except (OSError, ValueError):
        return
    want_s = {i for a in adds for i in a[1]}
    want_m = {i for a in adds for i in a[2]}
    have_s = {q.get("id") for q in proj.get("sequences") or [] if isinstance(q, dict)}
    have_m = {m.get("id") for m in proj.get("media") or [] if isinstance(m, dict)}
    seqs = [q for q in disk.get("sequences") or [] if isinstance(q, dict) and q.get("id") in want_s and q.get("id") not in have_s]
    media = [m for m in disk.get("media") or [] if isinstance(m, dict) and m.get("id") in want_m and m.get("id") not in have_m]
    was = next((a[3] for a in adds if a[3] is not None), None)
    caps = disk.get("captions") if was is not None and _cap_text(proj.get("captions")) == was else None
    if seqs:
        proj["sequences"] = list(proj.get("sequences") or []) + seqs
        merged["sequences"] = seqs
    if media:
        proj["media"] = list(proj.get("media") or []) + media
        merged["media"] = media
    if caps is not None:
        proj["captions"] = caps
        merged["captions"] = caps


def save_project(name, proj, base_rev=None, force=False, client=None, seq=None, merged=None):
    """base_rev: 편집실이 열 때 받은 판 번호. 그 사이 다른 창·작업이 저장했으면 Conflict (덮어쓰지 않음).
    force: 그래도 이 내용으로 (덮어쓰기 전 상태는 백업).
    client·seq: 편집실 창 id·편집 번호 — 저장 응답을 받기 전에 창을 닫아(신호 저장) 판 번호가 뒤처져도
    그 사이 저장한 게 같은 창뿐이면 이어서 저장 (늦게 도착한 예전 내용은 버림).
    끝까지 자동으로(client=AUTO_CLIENT)가 base_rev 뒤에 더한 편집본·미디어는 창 내용에 합쳐 저장 — 그 사이 저장이 모두 자동 작업이면
    충돌 창 없이, force 여도 그 편집본은 남김 (작업 중 편집실을 열어 두라는 안내대로 열어 둔 창이 MSG 편집본을 지우지 않게 · D-207).
    merged(dict): 합친 것 {sequences, media, captions} (편집실 창에 알려 줌)."""
    p = _ppath(name)
    client = str(client) if client else None
    seq = int(seq) if isinstance(seq, (int, float)) and not isinstance(seq, bool) else None
    merged = {} if merged is None else merged
    with _SAVE_LOCK:
        disk = _disk_rev(p) if p.exists() else 0
        w = _WRITER.get(str(p))
        mine = client is not None and w is not None and w[0] == client and w[2] == disk
        adds = [a for a in _AUTO_ADDS.get(str(p)) or () if a[0] > int(base_rev)] if base_rev is not None and client != AUTO_CLIENT else []
        if base_rev is not None and not force and disk >= 0 and int(base_rev) != disk:
            if mine and w[1] <= int(base_rev) < disk:
                if seq is not None and w[3] is not None and seq <= w[3]:
                    return disk  # 같은 창의 더 최근 내용이 이미 저장됨
            elif not (adds and int(base_rev) < disk and {a[0] for a in adds} >= set(range(int(base_rev) + 1, disk + 1))):
                raise Conflict(disk)
        if adds:
            _merge_auto(p, proj, adds, merged)
        auto = _auto_diff(p, proj) if client == AUTO_CLIENT else None
        if force and p.exists():
            _backup_copy(p, "덮어쓰기전")
        proj.pop("_recovered", None)
        proj["rev"] = max(disk, 0) + 1
        data = json.dumps(proj, ensure_ascii=False)
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace_retry(tmp, p)
        try:
            _REVC[str(p)] = (p.stat().st_mtime_ns, proj["rev"])
        except OSError:
            pass
        if client:
            _WRITER[str(p)] = (client, w[1] if mine else proj["rev"] - 1, proj["rev"], seq)
        else:
            _WRITER.pop(str(p), None)
        if auto is not None:
            _AUTO_ADDS[str(p)] = (_AUTO_ADDS.get(str(p)) or [])[-(AUTO_ADDS_MAX - 1):] + [(proj["rev"], *auto)]
        # 자동 백업: 5분마다 한 벌씩, 최근 10개까지 · 이미 저장은 끝났으므로 백업이 실패해도(디스크 가득·잠긴 옛 백업) 저장 실패가 아님
        try:
            regs = [x for x in _backup_files(p.stem) if not x[1]]
            if not regs or time.time() - _bk_epoch(regs[-1][0]) > 300:
                _backup_copy(p)
            _prune_backups(p.stem)
        except OSError:
            pass
        return proj["rev"]


def wait_saves(timeout=3.0):
    """앱을 닫기 전에 저장 중인 것이 끝나길 잠깐 기다림."""
    if _SAVE_LOCK.acquire(timeout=timeout):
        _SAVE_LOCK.release()


_BK_MEMO = {}


def backups(name):
    """자동 백업 목록 (최근 것부터). 편집본 수는 파일마다 한 번만 읽어 기억."""
    stem = _ppath(name).stem
    out = []
    for ts, tag, f in reversed(_backup_files(stem)):
        try:
            key = (f.name, f.stat().st_mtime_ns)
        except OSError:
            continue
        if key not in _BK_MEMO:
            try:
                _BK_MEMO[key] = len(_read_json(f).get("sequences") or [])
            except Exception:
                _BK_MEMO[key] = 0
        when = f"{ts[4:6]}월 {ts[6:8]}일 {ts[9:11]}:{ts[11:13]}"
        out.append({"file": f.name, "time": when + (f" · {BK_TAGS.get(tag, tag)}" if tag else ""), "sequences": _BK_MEMO[key]})
    return out


def restore_backup(name, fname):
    f = (PROJECTS / "backup" / Path(fname).name)
    if not f.exists() or not f.name.startswith(_ppath(name).stem + "__"):
        raise FileNotFoundError("백업을 찾지 못했어요")
    with _SAVE_LOCK:
        d = _read_json(f)
    d.pop("rev", None)
    return migrate_project(name, d)


def _uniq_name(n, names):
    if n not in names:
        return n
    k = 2
    while f"{n} {k}" in names:
        k += 1
    return f"{n} {k}"


CAP_AUTO_KEYS = {"id", "start", "end", "text", "wt", "sh", "shn", "words"}  # 받아쓰기로 만든 자막이 가진 값 (_make_cap) — 그 밖의 값(색·자리·줄 trk·손 시간 tm·손 나눔 ms)이 있으면 사용자가 고친 자막
CAP_KEEP_OVERLAP = 0.1  # (단어 시각이 없는 새 자막) 남겨 둔(고친) C1 자막과 이만큼(초) 넘게 겹치면 넣지 않음 (같은 말이 두 번 보이지 않게)
CAP_DELETED_PART = 0.5  # (단어 시각이 없는 새 자막) 길이의 이만큼 넘게가 사용자가 지운 말 자리면 넣지 않음 (지운 자막이 되살아나지 않게)
CAP_GONE_GAP = 1.0  # 지운 말 자리: 이어진 지운 낱말 사이가 이만큼(초) 안이면 한 구간 (그 틈에 걸친 새 낱말이 조각 자막으로 되살아나지 않게 · 긴 쉼은 새로 들린 말 자리)


def caption_baseline(name):
    """편집점 다시 찾기 전 받아쓰기의 말 조각 [(시작, 끝, 글, 낱말인지)] — 다시 찾은 뒤 어느 자막이 받아쓴 그대로이고 어느 자막을
    사용자가 고치거나 지웠는지 가릴 때 (app._analyze 가 받아쓰기를 새로 하기 전에 읽어 둠 · D-413). 단어 시각이 없는 구간(예전 받아쓰기)은
    구간 통째로 · 받아쓰기가 없거나 못 읽으면 None."""
    try:
        segs = _segments_of(name)
    except Exception:  # noqa: BLE001 — 깨진 받아쓰기: 가릴 수 없으면 예전처럼 자막을 모두 새로
        return None
    out = []
    for s in segs:
        ws = [w for w in s.get("words") or () if isinstance(w, dict) and str(w.get("w") or "").strip()]
        try:
            out += [(float(w["s"]), float(w["e"]), str(w["w"]), True) for w in ws] if ws else [(float(s["start"]), float(s["end"]), str(s["text"]), False)]
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def _flat(t):
    return "".join(str(t or "").split())


def _said(before):
    """다시 찾기 전 받아쓴 낱말: (시작, 끝) 1/100초 → 그 시각의 낱말들 (받아쓰기가 시각을 못 맞춘 낱말은 여럿이 같은 시각)."""
    said = {}
    for u in before or ():
        if u[3]:
            said.setdefault((int(round(u[0] * 100)), int(round(u[1] * 100))), set()).add(_flat(u[2]))
    return said


def _cap_untouched(c, before, marked, said=None):
    """받아쓴 그대로인 자막인지: 받아쓰기로 만든 값만 있고(CAP_AUTO_KEYS) · 어느 편집본에서도 지우거나('이 편집본에서 지운' capOff)
    자리를 옮기지('이 자막만' capPos) 않았고 · 낱말마다 단어 시각(wt)이 다시 찾기 전 받아쓴 낱말과 시각·글이 같음
    (글을 고쳐 낱말 수가 바뀌면 wt 가 안 맞음 → 고친 자막) · 단어 시각이 없는 자막(예전 받아쓰기)은 겹치는 받아쓰기 구간 글 안의 글이면.
    said: _said(before) (여러 자막을 볼 때 한 번만 만들어 넘김)."""
    if not isinstance(c, dict) or set(c) - CAP_AUTO_KEYS or c.get("id") in marked:
        return False
    txt = _flat(c.get("text"))
    if not txt:
        return False
    ws = _cap_words(c)
    if ws:
        said = _said(before) if said is None else said
        return all(_flat(w["w"]) in said.get((int(round(float(w["s"]) * 100)), int(round(float(w["e"]) * 100))), ()) for w in ws)
    if c.get("wt") is not None or c.get("words"):
        return False
    try:
        a, b = float(c["start"]), float(c["end"])
    except (KeyError, TypeError, ValueError):
        return False
    segs = sorted(u for u in before if not u[3] and min(b, u[1]) - max(a, u[0]) > 0)
    return bool(segs) and txt in "".join(_flat(u[2]) for u in segs)


def _spans(xs):
    """구간들 → 겹치거나 맞닿은 것을 합친 차례대로의 구간 + 시작 시각 목록 (bisect 로 찾게)."""
    out = []
    for a, b in sorted(xs):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out, [x[0] for x in out]


def _in_spans(t, sp):
    k = bisect.bisect_right(sp[1], t) - 1
    return k >= 0 and t <= sp[0][k][1]


def _span_overlap(a, b, sp):
    """a~b 가 합친 구간들과 겹치는 길이 (초)."""
    tot, k = 0.0, max(0, bisect.bisect_right(sp[1], a) - 1)
    while k < len(sp[0]) and sp[0][k][0] < b:
        tot += max(0.0, min(b, sp[0][k][1]) - max(a, sp[0][k][0]))
        k += 1
    return tot


def keep_edited_captions(old, new, before, seqs=(), fmt="long", terms=()):
    """편집점 다시 찾기 뒤 자막 합치기 (D-413 · D-415): 받아쓴 그대로인 자막만 새로 받아쓴 자막으로 바꾸고, 사용자가 고친 자막(글·색·자리·줄·시간·
    나눔 · 편집본에서 지움)은 그대로 둠 · 사용자가 목록에서 지운 말 자리(받아쓴 말인데 C1 줄에 자막이 없음 — 다른 자막 줄 C2~C9 로 옮긴 말도)에는
    새 자막을 넣지 않음. 새 자막은 늘 C1 이라 C1 만 봄. 단어 시각이 있는 새 자막은 낱말마다 가려서, 남긴 자막·지운 말 자리에 든 낱말만 빼고
    나머지 낱말로 자막을 다시 만듦 (나눈 곳이 예전과 달라도 고치지 않은 말이 자막 없이 남지 않게 · fmt·terms 는 그때 _make_cap 에).
    before: caption_baseline (다시 찾기 전 받아쓰기) · seqs: 편집본들 (capOff·capPos 를 봄).
    새 자막이 예전과 시간·글이 같으면 예전 id 를 그대로 (편집본마다 둔 자리 등이 이어지게). → (자막, 남긴 고친 자막 수, 다 빼거나 줄인 새 자막 수)."""
    marked = set()
    for q in seqs or ():
        if isinstance(q, dict):
            marked |= {x for x in q.get("capOff") or () if isinstance(x, str)} if isinstance(q.get("capOff"), list) else set()
            marked |= set(q["capPos"]) if isinstance(q.get("capPos"), dict) else set()
    old = [c for c in old or () if isinstance(c, dict)]
    said = _said(before)
    auto = [c for c in old if _cap_untouched(c, before, marked, said)]
    autoid = {id(c) for c in auto}
    kept = [c for c in old if id(c) not in autoid]
    wins, keepw = [], []  # C1 줄 예전 자막 자리 (앞뒤 0.05초 여유) · 그중 남긴 자막 자리
    for c in old:
        if cap_trk(c) != "C1":
            continue
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        wins.append((a - 0.05, b + 0.05))
        if id(c) not in autoid:
            keepw.append((a, b))
    wins, keepw = _spans(wins), _spans(keepw)
    gone, run = [], None  # 받아쓴 말인데 C1 줄 그 자리에 자막이 하나도 없음 = 사용자가 지운(또는 다른 줄로 옮긴) 말 · 이어진 낱말은 한 구간으로
    for u in sorted(before or ()):
        miss = not (_in_spans((u[0] + u[1]) / 2, wins) if u[3] else _span_overlap(u[0], u[1], wins) > 0)
        if run and (not miss or u[0] - run[1] > CAP_GONE_GAP):
            gone.append(tuple(run))
            run = None
        if miss:
            run = [run[0], max(run[1], u[1])] if run else [u[0], u[1]]
    if run:
        gone.append(tuple(run))
    gone = _spans(gone)
    same = {(round(float(c["start"]), 3), round(float(c["end"]), 3), c.get("text")): c["id"] for c in auto if c.get("id")}
    out, skipped = list(kept), 0
    for n in new or ():
        a, b = float(n["start"]), float(n["end"])
        ws = _cap_words(n)
        if ws:
            mids = [(float(w["s"]) + float(w["e"])) / 2 for w in ws]
            keep = [not _in_spans(m, keepw) and not _in_spans(m, gone) for m in mids]
            if all(keep):
                parts = [n]
            else:
                skipped += 1
                parts, cur = [], []
                for w, ok in zip(ws + [None], keep + [False]):
                    if ok:
                        cur.append(w)
                    elif cur:
                        parts.append(_make_cap({"start": float(cur[0]["s"]), "end": float(cur[-1]["e"]), "text": " ".join(x["w"] for x in cur),
                                                "words": cur}, fmt, terms))
                        cur = []
        else:
            lost = _span_overlap(a, b, gone)
            if _span_overlap(a, b, keepw) > CAP_KEEP_OVERLAP or (b > a and lost > CAP_DELETED_PART * (b - a)):
                skipped += 1
                continue
            parts = [n]
        for c in parts:
            k = (round(float(c["start"]), 3), round(float(c["end"]), 3), c.get("text"))
            out.append(dict(c, id=same.pop(k)) if k in same else c)
    out.sort(key=lambda c: float(c.get("start") or 0))
    return out, len(kept), skipped


def reanalyze_project(name, before=None):
    """편집점 다시 찾기 뒤: 사용자가 만든 편집본은 하나도 지우지 않고, 영상 정보 + 받아쓴 그대로인 자막만 새로 받아쓴 내용으로
    (before: 다시 찾기 전 받아쓰기 caption_baseline — 있으면 고치거나 지운 자막은 그대로 · 없으면 예전처럼 자막을 모두 새로) +
    새 가편집은 옆에 추가하고 새 롱폼 가편집을 열 편집본(active)으로 (fresh: 이번에 더한 편집본 id · 편집실이 한 번 알리고 예전 '(새로 찾음)'
    정리를 물어봄 · D-414) · 이전 상태는 '편집점 다시 찾기 전' 백업으로 남김."""
    p = _ppath(name)
    if not p.exists():
        return load_project(name), 0, False
    proj = load_project(name)
    with _SAVE_LOCK:
        if p.exists():
            _backup_copy(p, "재분석전")
    info = media_info(name)
    proj["info"] = info
    segs = _segments_of(name)
    caps = _captions_of(segs, info, silences_of(name))
    kept = 0
    if before:
        caps, kept, _ = keep_edited_captions(proj.get("captions"), caps, before, proj.get("sequences"), _cap_fmt(info), _terms())
    changed_caps = [(c["start"], c["end"], c["text"]) for c in proj.get("captions") or []] != [(c["start"], c["end"], c["text"]) for c in caps]
    proj["captions"] = caps
    proj["speech"] = speech_words(segs)
    for m in proj.get("media") or []:
        if m.get("id") == "main":
            m.update(dur=info["duration"], w=info["width"], h=info["height"], fps=info.get("fps", 30.0))
    names = {q["name"] for q in proj["sequences"]}
    fresh = auto_sequences(name, info, default_style_params())
    for q in fresh:
        q["name"] = _uniq_name(q["name"] + " (새로 찾음)", names)
        names.add(q["name"])
    proj["sequences"] += fresh
    if fresh:  # 편집실은 새 롱폼 가편집으로 열림 (예전: 처음 열던 예전 편집본 그대로라 고친 사전이 '안 바뀐' 것처럼 보였음)
        proj["active"] = next((q["id"] for q in fresh if q.get("format") == "long"), fresh[0]["id"])
        proj["fresh"] = {"ids": [q["id"] for q in fresh], "kept": kept}
    save_project(name, proj)
    return proj, len(fresh), changed_caps


def freeze_frame(src, f, t):
    """정지 화면(프레임 고정): 그 순간을 PNG 로 저장해 가져온 미디어로 (원래 영상과 같은 크기로 보이게 freeze 표시)."""
    p = media_path(f, src)
    tag = hashlib.sha1(f"{src}|{p.name}".encode("utf-8")).hexdigest()[:6]
    # 이름은 NFC 로 (맥·아이폰에서 온 풀어 쓴 한글이 30자에서 잘려 글자 반쪽이 남지 않게) · '%'는 뺌 (ffmpeg 가 '%d'를 번호 자리로 읽음)
    stem = unicodedata.normalize("NFC", Path(f).stem).replace("%", "")[:30].strip(" .")
    out = ASSETS / f"정지_{stem}_{tag}_{t:.2f}.png"
    if not out.exists():  # 임시 파일에 다 만든 뒤에만 제 이름으로 (디스크가 차서 반쪽이 된 그림이 '있는 파일'로 남지 않게)
        info = probe(p)
        vf = ["-vf", ",".join(tonemap_chain(info["hdr"]))] if info.get("hdr") else []
        tmp = ASSETS / f".{out.stem}.{os.getpid()}_{threading.get_ident()}.tmp.png"
        try:
            r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(p), "-frames:v", "1", *vf, str(tmp)])
            if r.returncode or not tmp.is_file() or not tmp.stat().st_size:
                raise RuntimeError("정지 화면을 만들지 못했어요")
            updater.replace_retry(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
    return {**media_entry(out.name, "assets"), "freeze": True}


# ---------- 자동 추천 (AI 없이 규칙 기반) ----------

FILLERS = {"아", "어", "음", "자", "네", "예", "그", "막", "뭐", "응", "이제", "그냥", "흠", "아니", "저기"}
KEYWORDS = {"팁": 4, "꿀팁": 6, "중요": 4, "핵심": 4, "강조": 3, "비결": 4, "방법": 3, "포인트": 3, "무조건": 3,
            "절대": 3, "실수": 3, "차이": 2, "첫 번째": 2, "첫번째": 2, "잘하": 2, "어떻게": 2, "비밀": 4, "원리": 3,
            "퍼스트 터치": 3, "기술": 2, "프로": 1, "국가대표": 3, "레전드": 2, "힘들": 1, "?": 1}


def _norm(t):
    return re.sub(r"[\s.,!?~…]+", "", t)


# ---------- NG 테이크·슬레이트·말더듬 정리 (takes.py) ----------

def _minus(cuts, ivs, pre=0.0):
    """컷 목록에서 정리할 구간 [a, b) 빼기 · 남길 말 바로 앞 여유(pre)는 남김 · 잘려서 0.2초도 안 남는 조각은 버림.
    슬레이트 말 구간은 그 말 끝에서 끝나므로 여유 없이 끝까지 뺌."""
    ivs = [(a, round(b - (0.0 if w[:1] == ["슬레이트 말"] else pre), 2)) for a, b, *w in ivs
           if b - (0.0 if w[:1] == ["슬레이트 말"] else pre) > a]
    out = []
    for c in cuts:
        pieces = [(c["in"], c["out"])]
        for a, b in ivs:
            nxt = []
            for x, y in pieces:
                if b <= x or a >= y:
                    nxt.append((x, y))
                else:
                    nxt += [q for q in ((x, a), (b, y)) if q[1] - q[0] >= 0.2]
            pieces = nxt
        out += [c] if pieces == [(c["in"], c["out"])] else [dict(c, **{"in": x, "out": y}) for x, y in pieces]
    return out


# 쇼츠 문장 경계 · 반응 앞 시범 (E12 · BR-061)
SHORT_END_MAX = 5.0    # 쇼츠 끝이 문장 중간이면 문장 끝(captions.ends_sentence)까지 이만큼(초)까지 늘림 · 안 되면 앞 문장 끝에서 자름
SHORT_LEAD_MAX = 10.0  # 반응 말 앞에 넣는 시범은 이만큼(초)까지
REACT_GAP = 1.5        # 반응 말 앞이 이만큼 넘게 말 없이 비어야 시범을 봄
REACT_LOOK = 15.0      # 시범은 반응 말 이만큼(초) 안의 것만 (더 앞 소리는 원인이 아닐 수 있음)
DEMO_TAIL = 6.0        # 마지막 공 소리 뒤 반응 말까지 이보다 길게 비면(걸어 돌아옴 등) 시범만 넣고 그 사이는 자름
PEAK_RUN = 8.0         # 공 소리 봉우리 사이가 이 초 안이면 같은 시범 (봉우리는 5초 넘게 떨어져 잡힘 · core._peaks)
DEMO_PAD = 1.0         # 첫 공 소리(1초 창) 앞 여유 · 시범을 자를 때 마지막 공 소리 뒤 여유는 DEMO_PAD + 1.5
NOPEAK_LEAD = 8.0      # 공 소리를 못 잡은 영상(봉우리 없음): 확실한 반응 말 앞 3~15초 빈 곳에서 이만큼까지만
# 시범을 보고 하는 반응 말 — 첫마디(앞에 짧은 말 두 개까지: '아 씨, 또 놓쳤네')가 반응 낱말 그 자체여야 함 ('완벽하게 하려면' · '깔끔하게 차는' · '그렇지 않으면'은 아님)
# 확실한 반응: 공 소리를 못 잡은 영상에서도 시범을 넣음 / 약한 반응('좋아요' · '오케이' · '그렇죠'): 공 소리(큰 소리 봉우리)가 있을 때만 ·
# 둘 다 반응 낱말 바로 뒤에 부호가 있거나('나이스!' · '아이고, …') 줄이 짧을 때만 ('좋아요 그럼 시작해 볼게요' · '좋습니다 그럼 두 번째는…'은 아님)
_REACT_LEAD = r"^(?:(?:아+|오+|와+|어+|우와|와우|자)[\s,!.~…]+|\S{1,2}[\s,!.~…]+){0,2}"
REACT_STRONG = (r"나이스|아이고|아이구|아이쿠|(?:안\s?)?들어갔(?:어요|어|다|네요|네|습니다)?|아깝(?:다|네요|네|습니다)?|아까워(?:요)?|아까비|아쉽(?:다|네요|네)?|"
                r"아쉬워(?:요)?|봤죠|보셨죠|됐다|대박|완벽(?:해요|해|합니다|하네요)?|깔끔(?:해요|하네요|합니다)?|놓쳤(?:네요|네|어요|어|다)?|"
                r"빗나갔(?:네요|네|어요|어|다|습니다)?|이거죠|이거예요|성공(?:입니다|이에요|이다|했어요|했다)?|괜찮아(?:요)?")
REACT_WEAK = r"좋습니다|좋아요|좋아|굿|오케이|그렇지(?!\s?않)|그렇죠|됐어(?:요)?"
REACTION = re.compile(_REACT_LEAD + r"(?:(?P<s>%s)|(?P<w>%s))(?=[\s,.!?~…]|$)" % (REACT_STRONG, REACT_WEAK))
REACT_LINE = 16        # 부호 없는 반응 줄은 이 글자 이하 (약한 반응은 6자 이하)
# 앞 말에 기대는 첫마디 — 쇼츠 첫 문장이면 점수를 덜 줌 ('그 다음에'·'자'·'그리고 마지막으로'처럼 순서를 여는 말은 아님)
LEANS = re.compile(r"^(?:그래야|그래서|그러니까|그니까|왜냐하면|그러면|그럼|근데|그런데|하지만|그래도|그러나|그렇게|이렇게|그게|그거)(?![가-힣])")
OPENER = re.compile(r"^(?:자[,\s]*)?(?:(?:첫|두|세|네|다섯)\s?번째|마지막으로)")
FRESH = re.compile(r"^(?:자[\s,]|오늘은?\s|이제\s|그리고\s|(?:자[,\s]*)?(?:(?:첫|두|세|네|다섯)\s?번째|마지막으로))")  # 새 문장을 여는 첫마디
REACT_ONLY = 16        # 앞에 시범이 없으면 이 글자 이하의 반응 말 줄은 쇼츠 첫 줄에서 뺌 ('좋아요, 그럼 두 번째는…' 같은 긴 줄은 그대로)
# 여담 ('오늘 진짜 물 좀 마시고 할게요' · '아 오늘 바람 진짜 많이 부네요') — 쇼츠에서는 뺌 (롱폼은 그대로 · 짧은 줄만)
ASIDE_V2160 = r"물\s?(?:좀\s?)?마시고|바람\s?(?:이\s?)?(?:진짜\s?|정말\s?|너무\s?)?(?:많이\s?)?부네|(?:^|\s)(?:진짜\s?|너무\s?)?(?:덥네요|춥네요)|잠깐\s?쉬(?:었다|고\s?(?:할|갈))"
ASIDE_OLD = re.compile(ASIDE_V2160)  # MSG(segs 를 줌) 쇼츠는 v2.16.0 여담 말 그대로 (E2 검토 · 공 줍기 말은 기본 가편집에서만)
ASIDE = re.compile(r"공\s?(?:좀\s?)?가져(?:올|오|갈)|공\s?(?:좀\s?)?(?:주우|주워|줍)|물\s?(?:좀\s?)?마시고|바람\s?(?:이\s?)?(?:진짜\s?|정말\s?|너무\s?)?(?:많이\s?)?부네|(?:^|\s)(?:진짜\s?|너무\s?)?(?:덥네요|춥네요)|잠깐\s?쉬(?:었다|고\s?(?:할|갈))")
ASIDE_LEN = 20
# 쇼츠 끝에 두면 매달리는 말: 다음 일을 예고하는 말('정리해 볼게요' · '보여 드릴게요' · '첫 번째 갑니다') · 여담
DANGLING = re.compile(r"(?:(?:할|갈|볼|드릴|줄|올)게요|하겠습니다|가겠습니다|보겠습니다|드리겠습니다|(?:^|\s)갑니다|시작(?:할게요|합니다))\W*$|"
                      r"(?:^|\s)(?:첫|두|세|네|다섯|마지막)\s?번째\W*$|" + ASIDE.pattern)
SECTION = re.compile(r"^(?:자[,\s]*)?(?:(?:마지막,?\s*)?(?:첫|두|세|네|다섯|여섯|일곱) ?번째|(?:그리고\s)?마지막으로|"
                     r"오늘\s?배운\s?(?:거|것)|정리(?:해\s?볼게요|할게요))")  # 포인트를 나누는 분명한 말만 ('N번째 …' · '마지막으로' · 마무리 정리) —
# '다음으로'·'그 다음'·'이번엔'·'정리하면'은 한 동작 안 순서 말로도 늘 쓰여서 장 나눔으로 안 봄 (E2 검토)
_GREET_END = r"(?:입니다|이에요|예요)(?:[.!~,\s]|$)"
GREETING = re.compile(r"안녕하세요|안녕하십니까|반갑습니다|사관학교\s?\S*입니다|최경진(?:\s?감독)?입니다|"
                      r"(?:^|[.!?]\s*)(?:저는|제가)\s?(?:[" + "".join(sorted(set("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용"))) +
                      r"][가-힣]{1,2}\s?)?(?:감독|코치|선생님?|강사)?" + _GREET_END)
# 인사·자기소개만 ('안녕하세요' · '풋살사관학교 최경진입니다' · '저는 김민수 코치예요') — '오늘의 핵심입니다' · '여기는 디딤발이에요' ·
# '제가 시범 보여줄 거예요' 같은 레슨 말은 아님 (E2 검토)
# 포인트를 여는 말 ('자, 첫 번째 포인트' · '그럼 두 번째는' · '네, 세 번째' · '그리고 마지막으로') — SECTION 에서 마무리 정리 말을 뺀 것 ·
# 쇼츠 수·쇼츠마다 포인트 하나를 정함 (D-260) · 앞 이음말은 short_point 처럼 '자'·'그럼'·'이제'·'네'·'좋아요'… 세 개까지 (D-267 — '자'만 받으면
# '그럼 두 번째 포인트는'을 못 세어 그 포인트가 쇼츠에서 빠짐) · group(1) = 순서 낱말('마지막으로'는 None)
POINT_LEAD = r"(?:(?:자|그럼|그러면|이제|네|예|좋아요|좋습니다|그리고|그\s?다음(?:에|으로)?|다음(?:으로)?)[,.!\s]*){0,3}"
POINT_OPEN = re.compile(r"^" + POINT_LEAD + r"(?:(?:마지막,?\s*)?(첫|두|세|네|다섯|여섯|일곱)\s?번째|마지막으로)")
SECTION_CROSS = 4.0    # 쇼츠 후보가 장 나눔 말을 하나 넘을 때마다 뺄 점수 (포인트 하나 = 쇼츠 하나)
SECTION_START = 3.0    # 장 나눔 말로 시작하는 후보에 더할 점수
SHORT_EDGE = 0.25      # 첫 인사는 영상 앞 이 비율 안, 끝인사는 뒤 이 비율 안에서만 찾음
GREET_LEN = 30         # 인사·소개 줄은 이 글자 이하만 (긴 줄은 내용이 섞임)
TITLE_MIN = 8          # 쇼츠 제목·훅 글은 이 글자 이상인 내용 문장에서 (반응·인사·자기소개 줄은 건너뜀)


def _sent_end(seg):
    toks = str(seg.get("text") or "").split()
    return bool(toks) and captions.ends_sentence(toks[-1])


def _reaction(text):
    """반응 말이면 'strong' / 'weak' · 아니면 None."""
    t = str(text or "").strip()
    m = REACTION.match(t)
    if not m:
        return None
    n = len(_norm(t))
    tight = bool(re.match(r"[,.!?~…]", t[m.end():]))  # 반응 낱말 바로 뒤 부호 ('나이스!' · '아이고, …')
    if m.group("s"):
        return "strong" if tight or n <= REACT_LINE else None
    return "weak" if tight or n <= 6 else None


def _quiet(silences, a, b):
    return sum(max(0.0, min(b, float(x["end"])) - max(a, float(x["start"])))
               for x in silences or () if isinstance(x, dict) and "start" in x and "end" in x)


def _demo_before(segs, k, peaks=(), silences=()):
    """문장 k 가 반응 말이고 바로 앞이 말 없는 시범이면 넣을 시범 구간 (a, b) · 아니면 None.
    공 소리(큰 소리 봉우리 · 1초 창)가 그 빈 곳에 있으면: 첫 봉우리 DEMO_PAD 초 앞부터 — 마지막 봉우리 뒤 반응 말까지 DEMO_TAIL 초 안이면
    반응 말까지 이어서(b = 반응 말 시작), 더 길게 비면(걸어 돌아옴) 마지막 봉우리 뒤 조금까지만(b < 반응 말 시작 → 그 사이는 자름) · 길이는 SHORT_LEAD_MAX 초까지.
    봉우리가 없으면 확실한 반응 말이고 빈 곳이 3~15초이며 대부분 조용하지 않을 때만(조용한 곳 = silencedetect) 반응 말 NOPEAK_LEAD 초 앞부터."""
    kind = _reaction(segs[k]["text"])
    if not kind:
        return None
    st = float(segs[k]["start"])
    prev_end = max([float(s["end"]) for s in segs[max(0, k - 30):k] if float(s["end"]) <= st + 0.05] or [0.0])  # 바로 앞 말 (문장은 시작 순서)
    gap = st - prev_end
    if gap < REACT_GAP:
        return None
    pk = sorted(float(p) for p in peaks or () if prev_end <= float(p) and float(p) + 1.0 <= st + 0.5)
    if pk and st - (pk[-1] + 1.0) > REACT_LOOK:  # 마지막 공 소리가 너무 앞이면 원인이 아닐 수 있음
        pk = []
    k0 = len(pk) - 1
    while k0 > 0 and pk[k0] - pk[k0 - 1] <= PEAK_RUN:  # 마지막 공 소리와 이어진 봉우리들만 (한 시범)
        k0 -= 1
    pk = pk[max(0, k0):]
    if pk:
        b = st if st - (pk[-1] + 1.0) <= DEMO_TAIL else pk[-1] + 1.0 + DEMO_PAD + 1.5
        a = max(prev_end + 0.1, pk[0] - DEMO_PAD, b - SHORT_LEAD_MAX)
        return (round(a, 2), round(b, 2)) if b - a >= 1.0 else None
    if kind != "strong" or not 2 * REACT_GAP <= gap <= REACT_LOOK or _quiet(silences, prev_end, st) > 0.5 * gap:
        return None
    return round(max(prev_end + 0.1, st - NOPEAK_LEAD), 2), round(st, 2)


def _short_edges(segs, junk, i, j, peaks=(), silences=(), min_len=0.0):
    """쇼츠 후보 [i..j] (문장 번호, junk 는 뺄 문장) → (i, j, lead): 문장 경계에 맞춘 첫·끝 문장 번호와 앞에 넣을 시범 구간 (a, b)(없으면 None).
    끝: 마지막 남길 문장이 문장 끝이 아니면 이어지는 문장 끝까지 SHORT_END_MAX 초 안에서 늘림(군더더기·1.5초 넘게 쉰 곳은 넘지 않음) ·
    안 되면 그 앞 문장 끝에서 자름 · 끝 문장이 예고·여담(DANGLING)이면 min_len 을 넘는 한 그 앞 문장 끝까지 줄임.
    시작: 첫 줄이 반응 말이면 그 앞 시범부터 (반응 말이 이어지면 거슬러 올라가 시범이 있는 반응 말부터 — '아이고 …' → '괜찮아요') —
    시범이 없으면 짧은 반응 줄은 뺌."""
    last = next((k for k in range(j, i - 1, -1) if k not in junk), None)
    if last is None:
        return i, j, None
    if not _sent_end(segs[last]):
        end0, prev, got = segs[last]["end"], segs[last]["end"], None
        for k in range(last + 1, len(segs)):
            if k in junk or segs[k]["end"] - end0 > SHORT_END_MAX or (segs[k]["start"] - prev > 1.5 and not segs[k].get("cont")):
                break
            if _sent_end(segs[k]):
                got = k
                break
            prev = segs[k]["end"]
        if got is None:
            got = next((k for k in range(last - 1, i - 1, -1) if k not in junk and _sent_end(segs[k])), None)
        if got is not None:
            j = got
    first = next((k for k in range(i, j + 1) if k not in junk), i)
    k, lead = first, None
    while _reaction(segs[k]["text"]):  # 반응 말이 이어지면 거슬러 올라가 시범이 있는 반응 말부터 (1.5초 안으로 붙은 반응 말만 · 둘까지)
        lead = _demo_before(segs, k, peaks, silences)
        if lead is not None or k == 0 or first - k >= 2 or k - 1 in junk or not _reaction(segs[k - 1]["text"]) \
                or segs[k]["start"] - segs[k - 1]["end"] > REACT_GAP:
            break
        k -= 1
    begin = i
    if lead is not None:
        begin = first = k
    elif _reaction(segs[first]["text"]) and len(_norm(segs[first]["text"])) <= REACT_ONLY:  # 원인 장면이 없는 반응 줄은 빼고 다음 문장부터
        nxt = next((k for k in range(first + 1, j + 1) if k not in junk and not _reaction(segs[k]["text"])), None)
        if nxt is not None:
            begin = first = nxt
    start = lead[0] if lead else segs[first]["start"]
    while True:  # 매달리는 끝 문장 (예고·여담) → 그 앞 문장 끝까지 (길이가 모자라면 그대로)
        last = next((k for k in range(j, first - 1, -1) if k not in junk), None)
        if last is None or not DANGLING.search(str(segs[last]["text"]).strip()):
            break
        prev = next((k for k in range(last - 1, first - 1, -1) if k not in junk and _sent_end(segs[k])), None)
        if prev is None or segs[prev]["end"] - start < min_len:
            break
        j = prev
    return begin, j, lead


def _short_title(segs, junk, i, j):
    """쇼츠 제목(훅 글): 반응·인사·자기소개가 아닌 TITLE_MIN 글자 넘는 첫 문장 (없으면 첫 문장)."""
    for k in range(i, j + 1):
        t = str(segs[k]["text"]).strip()
        if k in junk or len(_norm(t)) < TITLE_MIN or _reaction(t) or (takes.OPENING.search(t) and "오늘은" not in t):
            continue
        return t[:28]
    return str(segs[i]["text"])[:28]


def _join(cuts, cur, a, b, gap):
    """컷 목록 쌓기: [a, b] 가 지금 컷(cur) 끝에서 gap 초 안이면 이어 붙이고, 아니면 cur 를 닫고 새 컷."""
    if cur and a - cur["out"] <= gap:
        cur["out"] = max(cur["out"], b)  # (예전처럼 반올림 없이 — 같은 받아쓰기의 컷 값이 그대로)
        return cur
    if cur:
        cuts.append(cur)
    return {"in": round(a, 2), "out": round(b, 2)}


def _covered(ivs, a, b):
    """[a, b) 가운데 정리할 구간에 덮인 길이(초) — 서로 겹친 곳은 한 번만 셈."""
    got, end = 0.0, a
    for x, y, *_ in sorted(ivs):
        x, y = max(x, end), min(y, b)
        if y > x:
            got, end = got + y - x, y
    return got


# 말 없는 틈의 시범·환호 (E2 · BR-091): 순간 큰 소리(공 차는 소리·박수 · msg._onsets 와 같은 규칙을 편집실 파형으로)·큰 소리 봉우리
ONSET_DB = 9.0         # 0.1초 안에 이만큼 커지면 순간 큰 소리
ONSET_FLOOR = -40.0    # 이보다 작은 소리는 안 봄 (dBFS)
ONSET_TAIL = 6.0       # 소리가 0.12~0.2초 뒤에도 이만큼 안으로 이어지면(말·음악) 순간 소리가 아님
GAP_DEMO_PRE = 3.0     # 시범: 첫 큰 소리 앞 준비 동작(초) — 시험 영상 시범 20개: 첫 공 소리 앞 0.9~4.7초(중앙값 2.1)
GAP_DEMO_POST = 2.0    # 마지막 큰 소리 뒤 공이 굴러가는 것까지(초) — 같은 시범들: 0.8~3.4초(중앙값 2.0)
GAP_DEMO_RUN = 8.0     # 큰 소리끼리 이 초 안이면 한 시범


def _clean_transcript(name, silences=None, soft=None):
    """받아쓰기 파일 → (헛것을 뺀 구간들, 뺀 것 [(시작, 끝, 글)]) — 글이 빈 구간도 뺌 (E2 · BR-090).
    soft(목록을 주면): 빼지는 않고 '확인 필요'로만 보일 낱말 (조용한 곳 위 확신 낮은 말)."""
    t = core.adir(name) / "transcript.json"
    if not t.exists():
        return [], []
    raw = [s for s in json.loads(t.read_text(encoding="utf-8")) if str(s.get("text") or "").strip()]
    segs, flags = captions.drop_hallucinations(raw, silences_of(name) if silences is None else silences, soft)
    return [s for s in segs if str(s.get("text") or "").strip()], flags


def _sound_onsets(name, words):
    """편집실 파형(1초 50칸)에서 말 밖의 순간 큰 소리 시각들 · 소리를 못 읽으면 None."""
    try:
        wf = waveform(name)
    except Exception:  # noqa: BLE001 — 영상이 없거나 못 읽음 (예전 받아쓰기만 있는 시험 등) → None
        return None
    per, top, pk = int(wf.get("per_sec") or 50), float(wf.get("top") or 1.0), wf.get("peaks") or []
    if len(pk) < 20:
        return None
    db = [20 * math.log10(max(1e-5, float(x) * top)) for x in pk]
    look, tail0, tail1 = max(1, per // 10), max(1, int(0.12 * per)), max(2, int(0.2 * per))
    talk = [(a - 0.25, b + 0.2) for a, b in words]
    out, k = [], 0
    for i in range(look, len(db) - tail1):
        if db[i] < ONSET_FLOOR or db[i] - min(db[i - look:i]) < ONSET_DB:
            continue
        t = i / per
        if out and t - out[-1] < 0.25:
            continue
        tail = db[i + tail0:i + tail1]
        if sum(tail) / len(tail) > max(db[i:i + 3]) - ONSET_TAIL:
            continue
        while k < len(talk) and talk[k][1] < t:
            k += 1
        if k < len(talk) and talk[k][0] <= t:
            continue
        out.append(round(t, 2))
    return out


def gap_demos(words, acts):
    """낱말 [(시작, 끝)] (시간 순) · 순간 큰 소리 시각 acts → 말 없는 틈(DEMO_GAP 넘게) 안의 시범·환호 구간 [(a, b)].
    큰 소리 첫 것 GAP_DEMO_PRE 초 앞(준비 동작)부터 마지막 것 GAP_DEMO_POST 초 뒤까지, 틈 안으로 (앞뒤 말과 0.1초 띄움).
    첫 말 앞·마지막 말 뒤는 안 봄 (촬영 준비·끝). 큰 소리가 없는 틈(카메라만 켜진 쉼)은 그대로 자름."""
    out, acts = [], sorted(acts)
    for (_, e0), (s1, _) in zip(words, words[1:]):
        if s1 - e0 < DEMO_GAP:
            continue
        a, b = e0 + 0.1, s1 - 0.1
        hit = [t for t in acts if a <= t <= b]
        while hit:
            grp = [hit[0]]
            for t in hit[1:]:
                if t - grp[-1] > GAP_DEMO_RUN:
                    break
                grp.append(t)
            hit = hit[len(grp):]
            lo, hi = max(a, grp[0] - GAP_DEMO_PRE), min(b, grp[-1] + GAP_DEMO_POST)
            if hi - lo >= 1.0:
                out.append((round(lo, 2), round(hi, 2)))
    return out


STRAY_GAP = 8.0   # 받아쓰기가 앞으로 떼어 놓은 한두 글자('세' … '번째 포인트' · '오늘' … '배운 거 정리해 볼게요')를 다음 말에 붙이는 거리(초)


def _join_strays(lines, spans, silences=()):
    """문장 줄 → 같은 받아쓰기 구간 안에서 홀로 떨어진 한두 글자 줄(문장 끝·추임새·구령이 아님)을 다음 줄 앞에 붙인 것.
    낱말 시각이 앞으로 쏠려 '세'만 4초 앞 조용한 곳에 적히면, 줄로 나눈 가편집이 '세'와 '번째 포인트' 사이를 잘라 '세'가 엉뚱한 곳에 남음 →
    그 낱말을 다음 줄 바로 앞(0.3초)으로 옮겨 붙임 (split_sentences 의 같은 규칙을 줄 사이에도).
    시각이 틀린 낱말만 — 그 낱말 길이의 절반 넘게 조용한 곳(silencedetect) 위일 때 (말 없는 시범 사이 짧은 구령 '자!' 같은 진짜 말은 그대로)."""
    def mistimed(w):
        a, b = float(w["s"]), float(w["e"])
        return b > a and _quiet(silences, a, b) > 0.5 * (b - a)
    seg_of = lambda x: next((i for i, (a, b) in enumerate(spans) if a - 0.01 <= float(x["start"]) < b), None)  # noqa: E731
    lines = [dict(x) for x in lines]
    for k in range(len(lines) - 1):  # 줄 끝에 붙은 한두 글자 ('… 할게요 세' + '번째 포인트 …') → 다음 줄로 옮김
        ln, nx = lines[k], lines[k + 1]
        ws = ln.get("words") or []
        if len(ws) >= 2 and nx.get("words") and 0 < len(_norm(ws[-1]["w"])) <= 2 and captions.ends_sentence(ws[-2]["w"]) and mistimed(ws[-1]) \
                and _norm(ws[-1]["w"]) not in FILLERS and not takes.is_chant(ws[-1]["w"]) and float(nx["start"]) - float(ws[-1]["e"]) <= STRAY_GAP \
                and not captions.ends_sentence(ws[-1]["w"]):
            s0 = float(nx["start"])
            tail = dict(ws[-1], s=round(max(float(ws[-1]["s"]), s0 - 0.3), 2), e=round(s0 - 0.02, 2))
            lines[k] = dict(ln, words=ws[:-1], end=float(ws[-2]["e"]), text=" ".join(str(w["w"]).strip() for w in ws[:-1]))
            lines[k + 1] = dict(nx, words=[tail] + list(nx["words"]), start=tail["s"], text=str(tail["w"]).strip() + " " + nx["text"])
    out = []
    k = 0
    while k < len(lines):
        ln = lines[k]
        nx = lines[k + 1] if k + 1 < len(lines) else None
        t = _norm(ln["text"])
        if nx is not None and 0 < len(t) <= 2 and ln.get("words") and nx.get("words") and not captions.ends_sentence(ln["text"].split()[-1]) \
                and all(mistimed(w) for w in ln["words"]) \
                and t not in FILLERS and not takes.is_chant(ln["text"]) and float(nx["start"]) - float(ln["end"]) <= STRAY_GAP \
                and seg_of(ln) is not None and seg_of(ln) == seg_of(nx):
            s0 = float(nx["start"])
            moved = [dict(w, s=round(max(float(ln["start"]), s0 - 0.3), 2), e=round(s0 - 0.02, 2)) for w in ln["words"]]
            out.append(dict(nx, start=moved[0]["s"], text=ln["text"] + " " + nx["text"], words=moved + list(nx["words"])))
            k += 2
            continue
        out.append(ln)
        k += 1
    return out


def _keep_demos(cuts, demos):
    """컷 목록에 시범 구간을 더해 합침 (겹치거나 0.3초 안이면 하나로 · msg.keep_cuts 와 같은 규칙) · 시범이 없으면 그대로."""
    if not demos:
        return cuts
    ivs = sorted([(float(c["in"]), float(c["out"])) for c in cuts or []] + [(float(a), float(b)) for a, b in demos])
    out = []
    for a, b in ivs:
        if out and a <= out[-1][1] + 0.3:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [{"in": round(a, 3), "out": round(b, 3)} for a, b in out if b - a >= 0.2]


def _point_opens(segs, sjunk):
    """포인트를 여는 줄 [(줄 번호, 순서 번호)] (BR-092 · D-260 · D-267) · 포인트 수(top) · 셈이 비었는지(gap).
    'N번째'로 둘 이상 세어 나가는 영상일 때만 — 아니면 ([], None, False) (한 번만 나온 '마지막으로'·'N번째'는 포인트로 세는 영상이 아님).
    같은 번호를 되풀이하거나 앞 번호로 돌아간 줄(정리하며 다시 부름)은 새 포인트가 아님 · '마지막으로'는 지금까지 센 다음 번호 ·
    '마지막으로 정리해 볼게요'·끝인사는 포인트가 아님. top: 들은 가장 큰 번호 (줄 가운데의 'N번째 포인트'도 셈 — 줄 첫머리에서 못 찾은 포인트가 있으면
    gap=True · 그때는 포인트를 줄로 나눌 수 없어 쇼츠를 점수로 고름 · 셈이 모자라 포인트 하나가 쇼츠에서 빠지지 않게 · D-267)."""
    opens, last, ordn = [], 0, False
    for i, s in enumerate(segs):
        t = str(s.get("text") or "").strip()
        m = POINT_OPEN.match(t) if i not in sjunk else None
        if not m:
            continue
        if m.group(1):
            k = ORD_N[m.group(1)]
        elif re.search(r"정리|오늘\s?배운", t) or takes.CLOSING.search(t):
            continue
        else:
            k = last + 1
        if k <= last:
            continue
        ordn = ordn or bool(m.group(1))
        opens.append((i, k))
        last = k
    if len(opens) < 2 or not ordn:
        return [], None, False
    top = last
    for i, s in enumerate(segs):
        if i not in sjunk:
            top = max([top] + [ORD_N[x.group(1)] for x in POINT_AT.finditer(str(s.get("text") or ""))])
    return opens, top, [k for _, k in opens] != list(range(1, top + 1))


def shorts_len():
    """자동 쇼츠 후보 길이 (가장 짧게, 가장 길게) 초 — 스타일 화면 '쇼츠 길이' (BR-291 · 못 읽으면 지금처럼 20~55초)."""
    try:
        import style as _style
        lo, hi = _style.shorts_len()
        return float(lo), float(hi)
    except Exception:  # noqa: BLE001
        return 20.0, 55.0


def recommend(name, min_len=None, max_len=None, n=3, keep_pause=None, segs=None, points=False):
    """min_len·max_len: 쇼츠 후보 길이(초) — 주지 않으면 스타일 화면 '쇼츠 길이'(shorts_len · 기본 20~55초).
    keep_pause: 말 사이 이보다 길게 쉬면 자름 (스타일). 없으면 기본(약 1.2초).
    segs: 받아쓰기 대신 쓸 말 목록 (MSG 는 문장 단위로 나누고 깨진 말을 뺀 것을 줌). 없으면 받아쓰기 파일.
    points: segs 를 줄 때도 쇼츠 = 포인트 하나(BR-092 · 인사·끝인사 빼기 · 장 나눔 점수 · 뺄 말로 끝나지 않기 · 여담) — MSG 가 켜는 것
    (msg.SHORTS_POINTS · D-260). 받아쓰기 파일을 읽을 때(segs 없음)는 늘 켬.
    쇼츠 수 = 포인트 수 ('N번째'·'마지막으로'로 센 가장 큰 번호 · 없으면 n · 많으면 n 까지 · BR-092 규칙이 켜졌을 때만 · D-260) ·
    포인트가 줄로 다 나뉘면 쇼츠마다 다른 포인트 하나 (_point_opens · D-267)."""
    if min_len is None or max_len is None:
        lo, hi = shorts_len()
        min_len, max_len = lo if min_len is None else min_len, hi if max_len is None else max_len
    if keep_pause:
        kp = max(0.08, float(keep_pause))
        pre, post = min(0.15, kp * 0.4), min(0.25, kp * 0.6)
        gap_s = gap_l = max(0.0, kp - pre - post)
    else:
        pre, post, gap_s, gap_l = 0.15, 0.25, 0.6, 0.8
    extra_p = core.adir(name) / "analysis.json"
    extra = json.loads(extra_p.read_text(encoding="utf-8")) if extra_p.exists() else {"silences": [], "loud_peaks": []}
    peaks = [p["time"] for p in extra.get("loud_peaks", [])]
    word_mode, hallu, line_junk, demos, maybe = segs is None, [], [], [], []
    pt = word_mode or bool(points)   # 쇼츠 = 포인트 하나 규칙 (BR-092)
    if segs is not None:
        segs = [s for s in segs if str(s.get("text") or "").strip()]
    else:
        segs, hallu = _clean_transcript(name, extra.get("silences"), maybe)
    hallu_iv = [(a, b, captions.HALLU_WHY) for a, b, _ in hallu if b > a]  # 빈 길이(한 점에 몰린 낱말)는 자를 소리가 없음 · 표시만
    hallu_show = [(a, max(b, a + 0.01), captions.HALLU_WHY) for a, b, _ in hallu if b <= a]
    maybe_show = [(a, max(b, a + 0.01), captions.HALLU_MAYBE) for a, b, _ in maybe]  # 조용한 곳 위 낮은 확신: 자르지 않고 표시만 · 군더더기 수에 안 셈 (E2 검토)
    if word_mode and segs:
        # 낱말 단위 (E2 · BR-091): 받아쓰기 구간이 문장 여럿·말 없는 틈을 한데 묶어도 문장·쉼에서 나눠 그 사이 빈 곳은 자르고
        # (MSG 와 같은 msg.clean_lines — 한 줄 안의 다시 하기·끊긴 말·슬레이트 말·영어 찌꺼기도 뺌), 말 없는 틈의 시범·환호(순간 큰 소리)는 살림
        import msg
        raw_words = sorted((float(w["s"]), float(w["e"])) for s in segs for w in s.get("words") or () if str(w.get("w") or "").strip())
        spans = [(float(x["start"]), float(x["end"])) for x in segs]
        segs_raw = segs
        segs, line_junk = msg.clean_lines(segs)
        segs = _join_strays(segs, spans, extra.get("silences") or [])
        ons = _sound_onsets(name, raw_words) if raw_words else None
        if ons is None:  # 소리를 못 읽음(영상 없음): 예전처럼 같은 받아쓰기 구간 안의 빈 곳은 그대로 (시범을 못 찾으니 자르지 않음)
            prev = None
            for ln in segs:
                k = next((i for i, (a, b) in enumerate(spans) if a - 0.01 <= float(ln["start"]) < b), None)
                if k is not None and k == prev:
                    ln["cont"] = True
                prev = k
        else:
            # 순간 큰 소리(파형 onset)만 — 분석의 loud_peaks 는 카메라만 켜진 쉼(바람·발소리)에도 걸려 빈 곳을 살림 (E2 검토)
            demos = gap_demos(raw_words, ons)
        # '다시 해볼게요' 뒤에 다음 말보다 공 소리·시범이 먼저 오면 다시 찍기가 아니라 시범 예고 → 남김 (msg._recommend 와 같은 규칙 ·
        # 그 말을 받아쓰기가 한 번 더 겹쳐 쓴 줄('해볼게요.')은 '다음 말'로 보지 않음)
        acts = [float(p) + 0.5 for p in peaks] + (ons or [])
        slate_txt = {(a, b): " ".join(str(w["w"]) for s in segs_raw for w in s.get("words") or () if a - 0.1 <= (float(w["s"]) + float(w["e"])) / 2 <= b + 0.1)
                     for a, b, why in line_junk if why == "슬레이트 말"}
        starts = sorted(float(x["start"]) for x in segs if not any(_norm(x["text"]) and _norm(x["text"]) in _norm(t) and abs(float(x["start"]) - b) < 1.0
                                                                   for (_, b), t in slate_txt.items()))
        back = msg._demo_calls(line_junk, msg._words(segs_raw), starts, acts, demos)
        back += [j for j in line_junk if j[2] == "슬레이트 말" and j not in back
                 and any(0 <= j[0] - b <= 2.5 for _, b, _ in back)]  # 쉼으로 쪼개진 짝('다시' … '해볼게요.')도 함께
        if back:
            line_junk = [j for j in line_junk if j not in back]
            demos = sorted(demos + [(max(0.0, a - 0.15), b + 0.25) for a, b, _ in back])
    # 문장 단위 (받아쓰기 한 구간이 문장 여럿을 이으면 나눔 · 단어 시각이 없는 예전 받아쓰기는 구간 그대로) · 겹쳐 쓴 그림자 문장은 합침 (E12)
    segs = captions.merge_ghosts(captions.split_sentences(segs, guess=False))
    sil = [x for x in extra.get("silences", []) if isinstance(x, dict)]
    # 영상 밖 말 (E12 · BR-060): 촬영 준비 말·촬영 끝 말은 가편집·쇼츠에서 자르고, 구독·홍보 안내 말은 쇼츠·티저에서만 뺌
    off = takes.find_offscript(segs)
    edge_iv = [(o["a"], o["b"], o["why"]) for o in off if o["kind"] in ("pre", "post")]
    post_a = next((o["a"] for o in off if o["kind"] == "post"), None)  # 촬영 끝 말 시작 (마지막 끝인사 + 0.5초)
    off_iv = [(o["a"], o["b"], o["why"]) for o in off]

    # 군더더기 표시: 추임새 · 같은 말 연속 반복(마지막 것만 남김) — 구령·환호('셋!' · '골!' · '나이스! 나이스!')는 그대로
    junk = set()
    for i, s in enumerate(segs):
        if _norm(s["text"]) in FILLERS or (len(_norm(s["text"])) <= 1 and not takes.is_chant(s["text"])):
            junk.add(i)
        if i + 1 < len(segs) and _norm(s["text"]) == _norm(segs[i + 1]["text"]) and not takes.repeat_ok(s["text"], segs[i + 1]["text"]):
            junk.add(i)
        # 받아쓰기가 앞 말 둘을 한 점에 겹쳐 다시 쓴 그림자 ('네 번째' '빗나갔어요.' 뒤 '네번째 빗나갔어요.'(길이 0 낱말)) → 그림자를 뺌 (E2)
        if word_mode and i >= 2 and any(captions._collapsed(w) for w in s.get("words") or ()) and len(_norm(s["text"])) >= 4 \
                and _norm(segs[i - 2]["text"] + segs[i - 1]["text"]) == _norm(s["text"]) and float(s["start"]) - float(segs[i - 2]["start"]) <= 15:
            junk.add(i)
    junk_iv = takes.find_junk(segs, extra.get("silences", []), peaks)  # NG 테이크·슬레이트·말더듬 구간 (사이에 시범 소리가 있으면 같은 설명도 그대로)
    junk |= {i for i, s in enumerate(segs) if any(a <= (s["start"] + s["end"]) / 2 < b for a, b, _ in junk_iv)}  # 그 안에 든 말도 군더더기
    # 단어 시각이 있으면: 말 사이에 홀로 떨어진 '음'·'어' 같은 추임새 단어도 뺌 (이미 빠지는 곳에 든 것은 셈하지 않음)
    gone = [(segs[k]["start"], segs[k]["end"]) for k in junk] + [(a, b) for a, b, _ in junk_iv]
    fill_all = takes.find_fillers(segs)
    fill_iv = [f for f in fill_all if not any(a <= (f[0] + f[1]) / 2 <= b for a, b in gone)]
    # 자를 때는 이미 빠지는 곳에 든 추임새도 함께 — 군더더기 문장('음...')을 뺀 뒤 다음 말 앞 여유(pre)에 남는 꼬리까지 (D-403)
    sjunk = junk | {i for i, s in enumerate(segs) if any(a <= (s["start"] + s["end"]) / 2 < b for a, b, _ in off_iv)}  # 쇼츠는 영상 밖 말도 뺌
    aside = ASIDE if pt else ASIDE_OLD
    sjunk |= {i for i, s in enumerate(segs) if aside.search(s["text"]) and len(_norm(s["text"])) <= ASIDE_LEN}  # 여담도 쇼츠에서만 뺌
    # 쇼츠 = 포인트 하나 (E2 · BR-092): 영상 첫머리 인사·자기소개와 끝인사(그 뒤 모두)는 쇼츠에서 빼고 ('오늘은 … 알려 드릴게요' 주제 소개는 둠),
    # 장 나눔 말('자, 두 번째 포인트는 …')을 넘어가는 후보는 점수를 덜 줌 · 장 나눔 말로 시작하면 조금 더
    # MSG(segs 를 줌)는 points 를 켤 때만 (D-260 · 예전에는 v2.16.0 그대로 — MSG 쇼츠는 따로 판정하며 다듬는 중이라 E2 검토)
    if segs and pt:
        t_end = float(segs[-1]["end"])
        sjunk |= {i for i, s in enumerate(segs) if float(s["start"]) <= SHORT_EDGE * t_end and GREETING.search(s["text"])
                  and len(_norm(s["text"])) <= GREET_LEN}
        bye = next((i for i, s in enumerate(segs) if float(s["start"]) >= (1 - SHORT_EDGE) * t_end and takes.CLOSING.search(s["text"])), None)
        if bye is not None:
            sjunk |= set(range(bye, len(segs)))
    section = [pt and bool(SECTION.match(str(s["text"]).strip()) or POINT_OPEN.match(str(s["text"]).strip())) for s in segs]
    # 쇼츠 수 = 포인트 수 (들은 가장 큰 번호 · 'N번째'로 둘 이상 세어 나갈 때만 · 아니면 예전처럼 n · D-260) · 쇼츠마다 포인트 하나 (D-267):
    # 포인트가 줄로 다 나뉘고(번호가 비지 않음) 반 넘게 쇼츠 길이를 채우면 포인트마다 그 안에서 가장 좋은 구간 하나씩 —
    # 점수로만 고르면 강조 말이 많은 포인트에서 둘을 뽑고 다른 포인트는 빠짐 (n 을 포인트 수로 줄였을 때 · 검토)
    opens, n_pts, gap = _point_opens(segs, sjunk) if pt and segs else ([], None, False)
    pidx, per_point = [], False
    if n_pts:
        n = min(n, n_pts)
        firsts = [i for i, _ in opens]
        pidx = [sum(1 for f in firsts if f <= x) - 1 for x in range(len(segs))]   # 줄마다 포인트 번호 (첫 포인트 앞 = -1)
        tail = next((x for x in sorted(sjunk) if x > firsts[-1] and all(y in sjunk for y in range(x, len(segs)))), len(segs))
        ends = [float(segs[f]["start"]) for f in firsts[1:]] + [float(segs[tail - 1]["end"]) if tail > firsts[-1] else float(segs[firsts[-1]]["end"])]
        long_pts = sum(1 for f, e in zip(firsts, ends) if e - float(segs[f]["start"]) >= min_len)
        per_point = not gap and 2 * long_pts >= len(firsts)

    def seg_score(s):
        sc = sum(w for k, w in KEYWORDS.items() if k in s["text"])
        sc += 0.15 * len(_norm(s["text"]))
        return sc

    # 끝 바로 뒤(남길 문장 둘 안 · 15초 안)가 보여 줄 장면(구령 '하나, 둘, 셋' · 시범 뒤 반응 말 — '다시 한번 갈게요' 를 사이에 둬도)이면
    # 결과를 못 보고 끝나는 쇼츠 → 점수를 덜 줌 (E12 검토)
    cliff = []
    for j in range(len(segs)):
        nxs = [x for x in range(j + 1, min(len(segs), j + 6)) if x not in sjunk][:2]
        cliff.append(any(segs[x]["start"] - segs[j]["end"] <= REACT_LOOK
                         and (takes.is_chant(segs[x]["text"]) or _demo_before(segs, x, peaks, sil) is not None) for x in nxs))
    cands = []
    for i in range(len(segs)):
        start = segs[i]["start"]
        if i in sjunk or (segs[i].get("cont") and not _sent_end(segs[i - 1]) and not FRESH.match(segs[i]["text"])):
            continue  # 문장 가운데(쉼으로만 나뉜 곳)에서는 시작하지 않음 ('…멈추는 게 아니라' / '다음 방향으로 보내는 거예요')
        j, sc, chars, junk_n = i, 0.0, 0, 0
        while j < len(segs) and segs[j]["end"] - start <= max_len:
            if j in sjunk:
                junk_n += 0 if pt and len(_norm(segs[j]["text"])) <= 1 else 1  # 한 글자 추임새 줄('다'·'음')은 감점 없이 빼기만
            else:
                sc += seg_score(segs[j])
                chars += len(_norm(segs[j]["text"]))
            end = segs[j]["end"]
            if end - start >= min_len and not (pt and j in sjunk):  # 뺄 말(추임새·헛것 줄)에서 끝나는 후보는 안 셈 (E2)
                dur = end - start
                score = sc + 2.5 * sum(1 for p in peaks if start <= p <= end) + 2.0 * (chars / dur) - 1.5 * junk_n
                # 첫 문장이 강한 말로 시작하면 가산 (훅)
                if any(k in segs[i]["text"] for k in ("팁", "중요", "자 여기서", "비결", "어떻게", "?")):
                    score += 4
                elif LEANS.match(segs[i]["text"]):  # 앞 말에 기대는 첫 문장('그래야 …' · '그래서 …')은 덜 (E12: 문장 단위라 어디서나 시작할 수 있어서)
                    score -= 3
                elif OPENER.match(segs[i]["text"]):  # 순서를 여는 첫 문장('자, 두 번째 포인트는 …')은 조금 더
                    score += 2
                if cliff[j]:  # 시범·구령 바로 앞에서 끝남 (E12 검토)
                    score -= 3
                score -= SECTION_CROSS * sum(1 for x in range(i + 1, j + 1) if section[x])  # 다른 포인트로 넘어감 (E2)
                if section[i]:
                    score += SECTION_START
                cands.append((score / (dur ** 0.35), start, end, i, j))
            j += 1
    cands.sort(reverse=True)
    picked = []

    def owns(i, j):  # 후보 [i..j] 가 걸친 포인트 번호 (첫 포인트 앞 소개 말은 안 셈)
        return {pidx[x] for x in range(i, j + 1) if x not in sjunk} - {-1}

    def make(score, i, j, only=None):
        """후보 하나 → 쇼츠 {start, end, cuts, …} (안 되면 None) · only: 이 포인트 안에만 (문장 경계를 맞춘 뒤에도)."""
        # 문장 경계 맞추기 (E12 · BR-061): 끝은 문장 끝까지 늘리거나 앞 문장 끝에서 자름(예고·여담 끝은 줄임) · 반응 말로 시작하면 앞 시범부터
        i, j, lead = _short_edges(segs, sjunk, i, j, peaks, sil, min_len)
        s0, e0 = (lead[0] if lead else segs[i]["start"]), segs[j]["end"]
        if only is not None and owns(i, j) != {only}:
            return None
        if not all(e0 <= p["start"] or s0 >= p["end"] for p in picked):
            return None
        # 구간 안에서 군더더기·긴 무음을 빼고 컷 목록 생성
        cuts, cur, held = [], None, []
        for k in range(i, j + 1):
            s = segs[k]
            if k in sjunk:
                if cur and s.get("cont"):  # 같은 받아쓰기 구간 안의 뺄 문장: 컷은 이어 두고 그 문장만 뺌 (뒤 시범·빈 곳은 예전처럼 남김)
                    held.append(k)
                    continue
                if cur:
                    cuts.append(cur)
                    cur = None
                continue
            a, b = max(0.0, s["start"] - pre), s["end"] + min(0.2, post)
            demo = lead if k == i else (_demo_before(segs, k, peaks, sil) if k > i else None)  # 원인 → 반응 ('두 번째 슛.' → 공 소리 → '들어갔어요.')
            if k == j and k + 1 < len(segs):  # 끝 문장 바로 뒤에 이어 붙은 말(같은 받아쓰기 구간의 다음 문장)은 안 들어가게
                b = min(b, max(s["end"], (s["end"] + segs[k + 1]["start"]) / 2))
            if k == i and k and demo is None:
                a = max(a, min(s["start"], (segs[k - 1]["end"] + s["start"]) / 2))
            if demo:
                if demo[1] >= s["start"] - 0.05:
                    a = min(a, demo[0])
                else:  # 시범 뒤 오래 빈 곳은 자르고 시범만
                    cur = _join(cuts, cur, demo[0], demo[1], 0.2)
            elif cur is None and k > i and s.get("cont") and k - 1 in sjunk:  # 구간 첫 문장만 뺄 말: 그 뒤 같은 구간 안 빈 곳(시범)은 남김
                a = min(a, segs[k - 1]["end"])
            cur = _join(cuts, cur, a, b, float("inf") if cur and s.get("cont") else gap_s)  # 같은 받아쓰기 구간 안의 문장 사이는 예전처럼 그대로
        if cur:
            cuts.append(cur)
        cuts = _keep_demos(cuts, [d for d in demos if s0 - 0.05 <= d[0] and d[1] <= e0 + 0.05])  # 쇼츠 안 말 없는 시범·환호 (E2)
        cuts = _minus(_minus(_minus(cuts, junk_iv + off_iv + line_junk + hallu_iv, pre), fill_all), [(segs[k]["start"], segs[k]["end"]) for k in held])
        if cuts and post_a is not None and post_a - 0.6 <= cuts[-1]["out"] < post_a:  # 끝인사로 끝나면 촬영 끝 말 바로 앞까지 (끝 낱말 꼬리)
            cuts[-1] = dict(cuts[-1], out=round(post_a, 2))
        if not cuts or e0 - s0 - _covered(junk_iv + off_iv, s0, e0) < min_len:  # NG 구간을 빼면 너무 짧아지는 후보는 버림
            return None
        hits = [k for k in KEYWORDS if k != "?" and any(k in segs[x]["text"] for x in range(i, j + 1))]
        hits.sort(key=lambda k: (k in GENERIC, -KEYWORDS[k], -len(k)))  # 주제어 먼저
        return {"start": s0, "end": e0, "score": round(score, 1), "title": _short_title(segs, sjunk, i, j), "keywords": hits[:4],
                "cuts": cuts, "length": round(sum(c["out"] - c["in"] for c in cuts), 1)}

    if per_point:
        by_pt = {}
        for c in cands:
            o = owns(c[3], c[4])
            if len(o) == 1:
                by_pt.setdefault(next(iter(o)), []).append(c)
        for q in sorted(by_pt, key=lambda q: -by_pt[q][0][0]):  # 가장 좋은 구간 점수가 높은 포인트부터 (n 이 포인트 수보다 적으면)
            for score, s0, e0, i, j in by_pt[q]:
                r = make(score, i, j, q) if all(e0 <= x["start"] or s0 >= x["end"] for x in picked) else None
                if r:
                    picked.append(r)
                    break
            if len(picked) >= n:
                break
    else:
        for score, s0, e0, i, j in cands:
            if all(e0 <= x["start"] or s0 >= x["end"] for x in picked):
                r = make(score, i, j)
                if r:
                    picked.append(r)
            if len(picked) >= n:
                break
    picked.sort(key=lambda p: -p["score"])
    by_time = sorted(picked, key=lambda r: r["start"])  # 제목은 영상 순서대로 고름 (앞 쇼츠가 '주제 챌린지' · 뒤 쇼츠가 그다음 후보)
    for r, h in zip(by_time, short_hooks(by_time, segs)):
        r["hook"] = h
    # 쇼츠 첫머리 주제 줄 (BR-120): 영상 주제·이 쇼츠 포인트(MSG 쇼츠도 씀) → 위 큰 제목(hook)에 없는 것만 topic · 설정을 끄면 ''
    topic_on = shorts_topic_on()
    vt = video_topic(name, segs) if by_time else ""
    for r in by_time:
        r["point"] = short_point(r, segs)
        r["topicText"] = topic_line(vt, r["point"], r["hook"])   # 설정과 상관없이 (편집실 '새 쇼츠 편집본으로 추가'가 그때 설정을 다시 봄)
        r["topic"] = r["topicText"] if topic_on else ""
        r["hookSaid"] = hook_said(r, segs)

    # 롱폼용: 전체에서 군더더기·긴 무음 정리한 컷 (반응 말 앞 시범은 남김 · 같은 받아쓰기 구간 안 빈 곳은 예전처럼 그대로)
    tidy, cur, held = [], None, []
    for k, s in enumerate(segs):
        if k in junk:
            if cur and s.get("cont"):  # 같은 구간 안의 군더더기 문장('다시 할게요.' · '음...'): 컷은 이어 두고 그 문장만 뺌
                held.append(k)
                continue
            if cur:
                tidy.append(cur)
                cur = None
            continue
        a, b = max(0.0, s["start"] - pre), s["end"] + post
        demo = None if cur and s.get("cont") else _demo_before(segs, k, peaks, sil)
        if demo:
            if demo[1] >= s["start"] - 0.05:
                a = min(a, demo[0])
            else:
                cur = _join(tidy, cur, demo[0], demo[1], gap_l)
        elif cur is None and s.get("cont") and k and k - 1 in junk:
            a = min(a, segs[k - 1]["end"])
        cur = _join(tidy, cur, a, b, float("inf") if cur and s.get("cont") else gap_l)
    if cur:
        tidy.append(cur)
    tidy = _keep_demos(tidy, demos)
    tidy = _minus(_minus(_minus(tidy, junk_iv + edge_iv + line_junk + hallu_iv, pre), fill_all), [(segs[k]["start"], segs[k]["end"]) for k in held])
    if post_a is not None and tidy and post_a - 0.6 <= tidy[-1]["out"] < post_a:  # 마지막 끝인사는 촬영 끝 말 바로 앞까지 (끝 낱말 꼬리가 잘리지 않게)
        tidy[-1] = dict(tidy[-1], out=round(post_a, 2))
    lj = line_junk + hallu_iv + hallu_show
    out = {"shorts": picked, "tidy": tidy, "junk": len(junk) + len(fill_iv) + len(edge_iv) + len(lj), "segments": len(segs), "videoTopic": vt,
            "junk_list": [{"a": a, "b": b, "why": why} for a, b, why in sorted(junk_iv + edge_iv + fill_iv + lj + maybe_show, key=lambda x: (x[0], -x[1]))],
            "demos": [[round(a, 2), round(b, 2)] for a, b in demos],
            # 영상 밖 말 (추천 탭에 보여 줌) · cut: long 은 가편집(롱폼)에서도 자른 것, shorts 는 쇼츠·티저에서만 뺀 것 (롱폼은 표시만)
            "offscript": [dict(o, cut="long" if o["kind"] in ("pre", "post") else "shorts") for o in off]}
    if per_point:  # 포인트 수 — 포인트마다 쇼츠 하나로 고를 때만 (끝까지 자동으로가 '포인트 N개 중 M개만' 안내 · D-267)
        out["points"] = n_pts   # (챌린지의 'N번째 슛'처럼 짧은 시도로 센 영상·셈이 빈 영상은 없음)
    return out


# ---------- 타임라인 계산 (editor.html 과 같은 규칙) ----------

DEFAULTS = {"pos": [0.5, 0.5], "scale": 100.0, "rot": 0.0, "anchor": [0.5, 0.5], "opacity": 100.0, "level": 0.0}


def i_sp(it):
    return max(0.05, float(it.get("speed") or 1.0))


def i_end(it):
    return it["start"] + (it["out"] - it["in"]) / i_sp(it)


def i_mt(it, t):
    """타임라인 시간 → 미디어 시간."""
    return it["out"] - (t - it["start"]) * i_sp(it) if it.get("rev") else it["in"] + (t - it["start"]) * i_sp(it)


def i_tl(it, m):
    """미디어 시간 → 타임라인 시간."""
    return it["start"] + (it["out"] - m) / i_sp(it) if it.get("rev") else it["start"] + (m - it["in"]) / i_sp(it)


def param(it, key):
    p = (it.get("fx") or {}).get(key) or {}
    return {"v": p.get("v", DEFAULTS[key]), "k": sorted(p.get("k") or [], key=lambda k: k["t"])}


def _lerp(a, b, x):
    if isinstance(a, (list, tuple)):
        return [u + (v - u) * x for u, v in zip(a, b)]
    return a + (b - a) * x


def kf_at(p, m):
    k = p["k"]
    if not k:
        return p["v"]
    if m <= k[0]["t"]:
        return k[0]["v"]
    if m >= k[-1]["t"]:
        return k[-1]["v"]
    for a, b in zip(k, k[1:]):
        if a["t"] <= m <= b["t"]:
            x = (m - a["t"]) / ((b["t"] - a["t"]) or 1)
            if a.get("e") == "ease":
                x = x * x * (3 - 2 * x)
            return _lerp(a["v"], b["v"], x)
    return k[-1]["v"]


def seq_total(seq):
    ends = [i_end(it) for it in seq["items"]]
    if ends:
        return max(ends)
    g = [t["start"] + t["dur"] for t in seq.get("titles", [])] + [s["start"] + s["dur"] for s in seq.get("shapes", []) if not s.get("full")]
    g += [tactic.end_of(seq)] if seq.get("tactics") else []
    return max(g) if g else 0.0


def i_len(it):
    return (it["out"] - it["in"]) / i_sp(it)


def max_trans_dur(seq, tr, by=None):
    """전환이 쓸 수 있는 최대 길이 (클립을 줄이면 전환도 저절로 짧아짐 · editor.html maxTransDur 과 같음)."""
    by = by if by is not None else {it["id"]: it for it in seq["items"]}
    a, b = by.get(tr.get("a")), by.get(tr.get("b"))
    if a and b:
        al = tr.get("align", "center")
        return 2 * min(i_len(a), i_len(b)) if al == "center" else i_len(b) if al == "start" else i_len(a)
    x = a or b
    return i_len(x) if x else 0.0


def trans_range(seq, tr, by=None):
    by = by if by is not None else {it["id"]: it for it in seq["items"]}
    a, b = by.get(tr.get("a")), by.get(tr.get("b"))
    d = max(0.04, min(float(tr.get("dur") or 1.0), max_trans_dur(seq, tr, by)))
    if a and b:
        cut = b["start"]
        al = tr.get("align", "center")
        return (cut - d / 2, cut + d / 2) if al == "center" else (cut, cut + d) if al == "start" else (cut - d, cut)
    if b:
        return b["start"], b["start"] + d
    if a:
        return i_end(a) - d, i_end(a)
    return None


def valid_trans(seq):
    by = {it["id"]: it for it in seq["items"]}
    out = []
    for tr in seq.get("trans", []):
        a, b = by.get(tr.get("a")), by.get(tr.get("b"))
        if not a and not b:
            continue
        if a and b and (a["track"] != b["track"] or abs(i_end(a) - b["start"]) > 0.02):
            continue
        if (a or b)["track"] != tr["track"]:
            continue
        if max_trans_dur(seq, tr, by) < 0.066:  # 2프레임도 안 남으면 전환 없음
            continue
        out.append(tr)
    return out


def track_state(seq, track_id, t, trans):
    """시간 t 에 트랙에서 보이는 것: None | ("item", it) | ("trans", tr, a, b, t0, t1)."""
    by = {it["id"]: it for it in seq["items"]}
    for tr in trans:
        if tr["track"] != track_id:
            continue
        r = trans_range(seq, tr, by)
        if r and r[0] - 1e-6 <= t < r[1] - 1e-6:
            return ("trans", tr, by.get(tr.get("a")), by.get(tr.get("b")), r[0], r[1])
    for it in seq["items"]:
        if it["track"] == track_id and it["start"] - 1e-6 <= t < i_end(it) - 1e-6:
            return ("item", it)
    return None


CAP_JOIN = 0.06  # 같은 자막 조각 사이가 이만큼 안이면 이어진 것 (프레임 반올림 여유)
CAP_MIN = 0.7    # 타임라인 자막 조각은 (다음 자막 전까지) 적어도 이만큼(초) 보임 — 읽을 틈
CAP_CPS = 14.0   # 자막 1초에 읽을 글자 수 한도 (띄어쓰기 빼고) — 넘으면 다음 자막 전까지 조금 더 보임


def caption_items(proj):
    """자막이 따라갈 클립 = 말소리가 나오는 대사 소리 클립 (시작 순) — A1 이나 '대사' 역할 트랙의 소리 클립(영상과 짝이 없어도).
    소리를 끈 클립, 효과음 트랙(대사 역할 + voiceFx false), 거꾸로 재생, 다시 보기·티저(noCaps)는 뺌 (B롤 덮어쓰기·J/L 컷에서도
    들리는 말에 자막 · D-283). 트랙의 소리 끄기(M)·이 트랙만 듣기(S)는 듣기용이라 자막과 상관없음 (D-285).
    그 시간에 대사 역할 트랙에 원본 소리 클립(소리 끈 것·거꾸로·noCaps 도)이 하나도 없는 V1 원본 영상 구간도 자막을 따라감 — 영상만 있는
    편집본, 소리 없는 원본(화면 녹화), 대사 소리를 지우고 음악을 깐 곳은 예전처럼 영상을 따라감 (D-285 · 부분만이면 그 구간만 자른 사본).
    트랙 목록이 없으면(MSG 가 만들며 부를 때) 기본 트랙. editor.html capItems 와 같은 규칙."""
    items = proj.get("items") or ()
    tracks = {t["id"]: t for t in (proj.get("tracks") or default_tracks()) if t.get("k") == "a"}
    out, cover = [], []
    for it in items:
        tr = tracks.get(it.get("track"))
        if tr is None or not (it["track"] == "A1" or tr.get("role") == "dialog"):
            continue
        if it.get("media", "main") == "main" and i_end(it) - float(it["start"]) > 1e-6:
            cover.append((float(it["start"]), i_end(it)))
        if tr.get("voiceFx") is not False and not it.get("mute") and not it.get("rev") and not it.get("noCaps"):
            out.append(it)
    cv = _merge_spans(cover)
    for it in items:
        if it.get("track") != "V1" or it.get("media", "main") != "main" or it.get("rev") or it.get("noCaps"):
            continue
        s, e = float(it["start"]), i_end(it)
        for a, b in _minus_spans([[s, e]], cv):
            if b - a > 0.04:
                out.append(it if a <= s + 1e-9 and b >= e - 1e-9 else
                           dict(it, start=a, **{"in": it["in"] + (a - s) * i_sp(it), "out": it["in"] + (b - s) * i_sp(it)}))
    return sorted(out, key=lambda x: x["start"])


_MCAPS = {}


def media_captions(file, src="videos"):
    """보관함의 다른 영상(원본이 아닌 미디어)을 이어 붙였을 때 그 클립에 띄울 자막 (그 영상 원본 시각) — 그 영상의 편집실 프로젝트
    자막(고친 글)이 있으면 그것, 없으면 받아쓰기로 만든 자막, 받아쓰기도 없으면 []. 가져온 파일(assets)은 받아쓰기가 없어 [].
    파일이 그대로면 다시 안 읽음 → 편집실 미리보기(media_bundle 의 captions)와 내보내기(timeline_captions)가 같은 자막을 씀 (D-284)."""
    if src != "videos":
        return []
    name = Path(str(file)).name
    try:
        if not (core.VIDEOS / name).exists():
            return []
        pp, tp = _ppath(name), core.adir(name) / "transcript.json"
        key = tuple(f.stat().st_mtime_ns if f.exists() else None for f in (pp, tp))
        hit = _MCAPS.get(name)
        if hit and hit[0] == key:
            return hit[1]
        caps = None
        if pp.exists():
            try:
                c = _read_json(pp).get("captions")
                caps = [x for x in c if isinstance(x, dict)] if isinstance(c, list) else None
            except (OSError, ValueError, AttributeError):
                caps = None
        if caps is None:
            segs = _segments_of(name)
            caps = _captions_of(segs, media_info(name), silences_of(name)) if segs else []
    except Exception:  # noqa: BLE001 — 못 읽는 영상·받아쓰기는 자막 없이 (미리보기·내보내기는 그대로 됨)
        return []
    _MCAPS[name] = (key, caps)
    return caps


def timeline_captions(proj):
    """자막을 타임라인 시간으로 — 대사 소리 클립(caption_items)을 따라감, 잘린 부분은 빠지고 여러 클립에 걸치면 나뉨.
    원본 클립은 프로젝트 자막, 보관함의 다른 영상 클립은 그 영상 자막(media_captions · proj 에 media 가 있을 때).
    자막 줄(D-296): 원본 자막의 trk(C2~C9)를 따라 조각마다 "trk" (C1 이면 키 없음 → 예전과 같은 dict) ·
    이어 붙이기·최소 보이는 시간은 자막 줄마다 따로 (editor.html capsTL 과 같은 규칙)."""
    res = []
    total = seq_total(proj)
    # noCaps: 다시 보기·티저처럼 같은 장면을 한 번 더 쓴 클립은 말 자막을 다시 띄우지 않음 (editor.html capsTL 과 같은 규칙)
    its = caption_items(proj)
    shorts = SHORTS_SPLIT and proj.get("format") == "shorts"
    mds = {m.get("id"): m for m in proj.get("media") or () if isinstance(m, dict)}
    groups = {}
    for it in its:
        groups.setdefault(it.get("media", "main"), []).append(it)
    alive = []  # 쇼츠형으로 나눈 자막 조각을 끝까지 붙잡아 둠 — _k(id) 가 다른 조각에 다시 쓰여 다른 자막끼리 합쳐지지 않게 (editor.html 은 객체 자체로 견줌)
    cpos = proj.get("capPos") if isinstance(proj.get("capPos"), dict) else {}  # '이 자막만' 자리 (편집본마다 · D-321)
    # 이 편집본에서 지운 자막 (capOff · D-344) — 자막 글은 모든 편집본이 함께 쓰므로 편집실의 지우기는 이 편집본만 (editor.html capsTL 과 같은 규칙)
    off = {x for x in proj.get("capOff") or () if isinstance(x, str)} if isinstance(proj.get("capOff"), list) else set()
    for mid, vids in groups.items():
        if mid == "main":
            # msgCaps: MSG 후보가 함께 가져온 자막 (다시 들은·다듬은 받아쓰기) — 편집실이 프로젝트 자막에 반영하면 지움 (editor.html capsTL 과 같은 규칙)
            caps = proj.get("msgCaps") or proj.get("captions") or []
            if off:
                caps = [c for c in caps if not (isinstance(c, dict) and c.get("id") in off)]
        else:
            md = mds.get(mid)
            caps = media_captions(md.get("file"), md.get("src", "videos")) if md else []
        for cap0 in caps:
            trk = cap_trk(cap0, mid)
            pos = cap_pos(cpos.get(cap0.get("id"))) if mid == "main" and isinstance(cap0, dict) else None
            tm = mid == "main" and isinstance(cap0, dict) and bool(cap0.get("tm"))
            for cap, ws in (_shorts_parts(cap0) if shorts else [(cap0, _cap_words(cap0))]):
                alive.append(cap)
                toks = str(cap.get("text") or "").split()
                for it in vids:
                    a, b = max(cap["start"], it["in"]), min(cap["end"], it["out"])
                    if b - a > 0.05:
                        s, e = i_tl(it, a), min(i_tl(it, b), total)
                        if e - s > 0.04:
                            # 단어 시각이 있으면 이 클립에 실제로 든 낱말만 (잘라 낸 말·고쳐 다시 한 말이 자막에 남지 않게 · editor.html capsTL 과 같은 규칙)
                            keep = [k for k, w in enumerate(ws) if it["in"] <= (float(w["s"]) + float(w["e"])) / 2 < it["out"]] if ws else None
                            if keep is not None and not keep:
                                continue
                            c = {"start": s, "end": e, "text": cap["text"] if keep is None or len(keep) == len(ws) else " ".join(toks[k] for k in keep)}
                            if ws:  # 노래방 자막용 단어 시각도 타임라인으로
                                c["words"] = [{"w": ws[k].get("w", ""), "s": i_tl(it, float(ws[k]["s"])), "e": i_tl(it, float(ws[k]["e"]))} for k in keep]
                                c["_keep"] = keep
                                c["_n"] = len(ws)
                                c["_toks"] = toks
                                c["_full"] = cap["text"]
                            c["_k"] = id(cap)
                            if trk != "C1":
                                c["trk"] = trk
                            if pos:
                                c["pos"] = dict(pos)
                            if tm:
                                c["_tm"] = True
                            res.append(c)
    # 같은 자막이 컷(확대 컷·말 빠르기 나누기)으로만 나뉘어 바로 이어지면 한 덩어리로 — 컷마다 효과가 다시 시작돼 깜빡이지 않게
    # 같은 소리를 두 대사 트랙에 겹쳐 둔 것(같은 자막이 겹침)도 한 덩어리 (editor.html capsTL 과 같은 규칙)
    out, last = [], {}  # last: 자막 줄마다 마지막 조각 (다른 줄 자막이 사이에 있어도 같은 자막은 이어 붙임)
    for c in sorted(res, key=lambda x: x["start"]):
        p = last.get(c.get("trk", "C1"))
        if p is not None and p["_k"] == c["_k"] and abs(c["start"] - p["end"]) <= CAP_JOIN:
            p["end"] = max(p["end"], c["end"])
            if "words" in p and "words" in c:
                p["words"] = p["words"] + c["words"]
            else:
                p.pop("words", None)
            if "_keep" in p and "_keep" in c:
                p["_keep"] = sorted(set(p["_keep"]) | set(c["_keep"]))
                p["text"] = " ".join(p["_toks"][k] for k in p["_keep"]) if len(p["_keep"]) < p["_n"] else p["_full"]
            continue
        if p is not None and p["_k"] == c["_k"] and c["start"] < p["end"]:  # 겹친 같은 자막: 아직 없는 낱말만 더함
            p["end"] = max(p["end"], c["end"])
            if "words" in p and "words" in c:
                have = dict(zip(p["_keep"], p["words"]))
                for k, w in zip(c["_keep"], c["words"]):
                    have.setdefault(k, w)
                p["_keep"] = sorted(have)
                p["words"] = [have[k] for k in p["_keep"]]
                p["text"] = " ".join(p["_toks"][k] for k in p["_keep"]) if len(p["_keep"]) < p["_n"] else p["_full"]
            else:
                p.pop("words", None)
            continue
        out.append(c)
        last[c.get("trk", "C1")] = c
    # 컷으로 잘려 너무 짧게 스치는 자막은 다음 자막이 나오기 전까지 CAP_MIN 초는 보이게 (editor.html capsTL 과 같은 규칙)
    # (바로 이어지는 다음 자막이 넉넉하면 그 시작을 조금 늦춰 자리를 만듦)
    # 글자가 많으면 1초에 CAP_CPS 글자를 넘지 않게 읽을 시간도 (다음 자막 전까지)
    # 자막 줄마다 따로 — 다른 줄의 짧은 자막이 이 줄 자막을 줄이거나 늦추지 않음
    lanes = {}
    for c in out:
        lanes.setdefault(c.get("trk", "C1"), []).append(c)
    # 손으로 시간을 맞춘 자막(tm · D-322)은 맞춘 그대로 — 늘리지도, 앞 자막 때문에 늦추지도 않음
    for lane in lanes.values():
        for k, c in enumerate(lane):
            if c.get("_tm"):
                continue
            need = max(CAP_MIN, len(re.sub(r"\s+", "", c["text"])) / CAP_CPS)
            if c["end"] - c["start"] < need:
                n = lane[k + 1] if k + 1 < len(lane) else None
                if n is not None and not n.get("_tm") and n["start"] - c["start"] < need and n["end"] - c["start"] >= need + CAP_MIN:
                    n["start"] = c["start"] + need
                c["end"] = max(c["end"], min(c["start"] + need, n["start"] if n is not None else total))
    for c in out:
        for k in ("_k", "_keep", "_n", "_toks", "_full", "_tm"):
            c.pop(k, None)
    return out


def cap_pos(v):
    """'이 자막만' 자리 (편집본 capPos[자막 id] · D-321) → {"x", "y"} 중 바르게 적힌 것만 (화면 비율 · x 0~1 · y 0.03~1) · 없으면 None.
    editor.html capPosOf 와 같은 규칙."""
    if not isinstance(v, dict):
        return None
    out = {}
    for k, lo in (("x", 0.0), ("y", 0.03)):
        x = v.get(k)
        if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x):
            out[k] = min(1.0, max(lo, float(x)))
    return out or None


# ---------- 단어 시각 · 쇼츠형 자막 나누기 (#5) ----------

# 쇼츠 편집본에서 가로 영상 자막을 sh 로 나눠 보여 주고 내보냄 — 편집실 미리보기(editor.html capParts)도 같은 방식으로 나눔
SHORTS_SPLIT = True


def _cap_words(cap):
    """자막의 단어 시각 [{w, s, e}] — 자막 안에 든 단어 수가 지금 글의 낱말 수와 같을 때만.
    편집실에서 글을 고쳐 낱말 수가 바뀌었거나 자막을 나눠서 안 맞으면 None (글자 수 비례로).
    wt(1/100초 · 첫 값 뒤로는 차이) 또는 예전 모양 words [{w, s, e}] 둘 다 읽음. editor.html capWords 와 같은 규칙."""
    toks = str(cap.get("text") or "").split()
    try:
        if cap.get("wt") is not None:
            t = list(itertools.accumulate(int(x) for x in cap["wt"]))
            if len(t) != 2 * len(toks):
                return None
            ws = [{"w": w, "s": float(t[2 * k]) / 100, "e": float(t[2 * k + 1]) / 100} for k, w in enumerate(toks)]
        else:
            ws = cap.get("words") or ()
        ws = [w for w in ws if cap["start"] - 0.05 <= (float(w["s"]) + float(w["e"])) / 2 <= cap["end"] + 0.05]
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        return None
    return ws if ws and len(ws) == len(toks) else None


def _shorts_parts(cap):
    """쇼츠 편집본에서 가로 영상 자막을 쇼츠형(한 줄 13글자·2.2초까지)으로 — 만들 때 정해 둔 나눌 곳(sh)에서.
    단어 시각이 맞으면 그 시각에서, 없으면(예전 받아쓰기) 글자 수대로 나눈 시각에서 (낱말 수가 shn 과 같을 때만).
    나눈 자막은 이어서 보임 (다음 자막 시작까지) · 글을 고쳐 낱말 수가 바뀌었으면 한 덩어리 그대로. [(자막, 단어 시각)]
    editor.html capParts 와 같은 규칙."""
    ws = _cap_words(cap)
    sh, toks = cap.get("sh"), str(cap.get("text") or "").split()
    if not isinstance(sh, list) or not sh or (cap.get("shn") is not None and cap.get("shn") != len(toks)):
        return [(cap, ws)]
    tm = ws
    if not tm:
        if cap.get("shn") is None:
            return [(cap, ws)]
        tot, acc, tm = sum(captions.weight(t) for t in toks), 0, []
        for t in toks:  # 나눌 곳을 고를 때(captions.even_words)와 같은 비율 (editor.html capWeight 와 같은 식)
            tm.append({"w": t, "s": cap["start"] + (cap["end"] - cap["start"]) * acc / tot})
            acc += captions.weight(t)
    try:
        cuts = [0] + [int(k) for k in sh] + [len(tm)]
    except (TypeError, ValueError):
        return [(cap, ws)]
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        return [(cap, ws)]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        s = cap["start"] if a == 0 else max(cap["start"], float(tm[a]["s"]))
        e = cap["end"] if b == len(tm) else min(cap["end"], float(tm[b]["s"]))
        if e - s <= 0.04:
            return [(cap, ws)]
        out.append(({"start": s, "end": e, "text": " ".join(w["w"] for w in tm[a:b])}, ws[a:b] if ws else None))
    return out


# 자막 한 줄이 화면 밖으로 넘치지 않게: 글자 폭을 어림해 (Pretendard: 한글 0.864em) 넘치면 글자를 조금 줄임 (0.7배까지).
# 그보다 더 줄여야 하는 긴 자막(예전 자막)은 크기 그대로 두고 줄을 바꿈 · editor.html capFit 과 같은 규칙
FIT_MIN = 0.7


def _em(ch, black=False, fm=None):
    """글자 하나의 폭 (글자 크기 1 기준) — fm: 은평 글꼴 폭 표(epfont.metrics · 그 글꼴에 있는 글자·한글만 · editor.html emOf 와 같은 셈) ·
    없거나 표에 없는 글자는 Pretendard 어림."""
    if fm:
        w = fm["em"].get(ch)
        if w is not None:
            return w
        if epfont.is_hangul(ch):
            return fm["hangul"]
    o = ord(ch)
    if ch == " ":
        return 0.24
    if o >= 0x1100:
        return 0.95 if black else 0.9  # Pretendard Black 은 한글이 조금 더 넓음
    if "0" <= ch <= "9" or "a" <= ch <= "z":
        return 0.6
    if "A" <= ch <= "Z":
        return 0.78
    return 0.42


def _fit_need(text, st, W):
    """(한 줄 폭 한도, 가장 긴 줄 폭) 화소 — 스킨 말 자막(hug "skin")은 Pretendard Black 실제 글자 폭 표(SKIN_HUG)로 화면 폭 95%까지 (D-533) ·
    그 밖은 넉넉한 어림(은평 글꼴이면 그 폭 표)으로 90%까지."""
    x, al = float(st.get("x", 0.5)), st.get("align", "center")
    room = (1 - x) if al == "left" else x if al == "right" else 2 * min(x, 1 - x)
    hug = st.get("hug") == "skin" and not ep_font_now()
    avail = W * min(SKIN_CAP_ROOM if hug else 0.9, room - 0.05)  # 화면 90%까지 (미리보기 글 상자 92%·내보내기 양옆 여백 안쪽)
    black, fm = st.get("weight") == "Black", SKIN_HUG if hug else ep_font_now()
    em = max([sum(_em(ch, black, fm) for ch in ln) for ln in str(text).replace("\r", "").split("\n")] or [0])
    return avail, em * float(st["size"]) + 2 * float(st.get("strokeW") or 0)


def cap_fit(text, st, W):
    """자막 글자 크기 배율 (1 = 그대로) — 가장 긴 줄이 화면 폭의 90%(왼쪽/오른쪽 정렬이면 남은 쪽, 그래도 90%까지 · 스킨 말 자막은 실제 글자 폭으로 95%) 안에 들게."""
    avail, need = _fit_need(text, st, W)
    if need <= avail or avail <= 0:
        return 1.0
    k = math.floor(avail / need * 100) / 100
    return k if k >= FIT_MIN else 1.0


def hug_one(text, st, W):
    """스킨 글(hug "skin")이 글자를 cap_fit 만큼 줄여 글 속 줄 그대로 들어가는지 — 그러면 줄을 바꾸지 않음 (ASS \\q2 · 미리보기 nowrap · D-533) · 아니면 False."""
    if st.get("hug") != "skin":
        return False
    avail, need = _fit_need(text, st, W)
    return avail > 0 and need * cap_fit(text, st, W) <= avail + 1e-6


# ---------- 자막 (ASS) ----------

def _ass_color(hexc, alpha=0.0):
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H{int(alpha * 255):02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_secs(s):
    """ASS 시각 'h:mm:ss.cc' → 초."""
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)


def _ass_ftime(t, fps=30):
    """자막·타이틀·도형의 시작·끝 → ASS 시각 — 미리보기와 같은 프레임에서 나타나고 사라지게 (D-328).
    미리보기는 재생 위치 ≥ 시작인 첫 프레임 k = ceil(t·fps) 부터 그림 · 내보내기 subtitles 필터는 프레임 시각을 ms 로 잘라(trunc)
    libass 에 넘기고 libass 는 시작 ≤ 지금 < 끝 일 때 그림 → 프레임 k 의 (잘린) ms 를 넘지 않는 가장 큰 1/100초.
    예전 _ass_time(반올림)은 x.x67초처럼 반올림한 값이 프레임 시각을 넘는 곳(약 36%)에서 한 프레임 늦게 나오고 늦게 사라졌음.
    (전술 그림은 반 프레임씩 비켜 둔 시각이라 그대로 _ass_time)"""
    t = max(0.0, float(t))
    # 여유는 미리보기(editor.html TEPS)와 같은 1e-6 '초' — 손으로 맞춘 시각은 1e-6 초로 반올림해 둬서(r6c) 프레임 시각보다 5e-7 초까지 클 수 있음
    # (예전 1e-6 '프레임' 여유는 그런 시각을 한 프레임 늦게 그렸음 · 약 1/3)
    k = max(0, math.ceil((t - 1e-6) * fps))
    ms = int(k * (1.0 / fps) * 1000)  # ffmpeg vf_subtitles: pts · av_q2d(1/fps) · 1000 → (long long)
    cs = ms // 10
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _effect_tags(effect, x, y, dur, rz=0.0):
    """타이틀·자막 효과 → ASS 명령 (editor.html effect() 와 같은 움직임). rz: ASS 기울기(\\frz, 반시계 방향 +)."""
    if effect == "stamp":  # 쾅 찍기: 크게(165%) 나타나 0.12초 만에 제자리로
        return r"{\fscx165\fscy165\t(0,120,\fscx100\fscy100)}"
    if effect == "shake":  # 흔들기: 살짝 크게 시작 + 좌우로 기울며 흔들림 0.24초
        r = _num(round(rz, 2))
        return (rf"{{\fscx125\fscy125\t(0,80,\fscx100\fscy100)\t(0,60,\frz{_num(round(rz + 6, 2))})\t(60,120,\frz{_num(round(rz - 6, 2))})"
                rf"\t(120,180,\frz{_num(round(rz + 3, 2))})\t(180,240,\frz{r})}}")
    if effect == "fade":
        return r"{\fad(150,120)}"
    if effect == "pop":
        return r"{\fscx70\fscy70\t(0,110,\fscx108\fscy108)\t(110,190,\fscx100\fscy100)}"
    if effect == "slide":
        return rf"{{\move({x},{y + 40},{x},{y},0,180)\fad(120,0)}}"
    return ""


def _ass_text(s):
    """글자 그대로 보이게: { } 는 효과 명령, \\n·\\N·\\h 는 줄바꿈 명령으로 읽히지 않게 막음."""
    s = str(s).replace("\\", "\\\u2060")  # 역슬래시 뒤에 보이지 않는 글자 → 명령이 안 됨
    s = s.replace("{", "\\{").replace("}", "\\}")
    return s.replace("\r", "").replace("\n", r"\N")


def _karaoke(text, dur, words=None, t0=0.0):
    """노래방 효과: 줄바꿈은 그대로 두고 글자 수에 비례해 시간을 나눔.
    words(타임라인 기준 단어 시각, t0 = 자막 시작)가 낱말 수와 맞으면 실제로 말한 때에 채움:
    단어마다 그 단어 시작 ~ 다음 단어 시작 (첫 단어는 자막 시작부터, 마지막은 자막 끝까지) · 합은 자막 길이와 같음."""
    rows = [ln.split() for ln in str(text).replace("\r\n", "\n").split("\n")]
    if words and len(words) == sum(len(r) for r in rows):
        cs = int(round(dur * 100))
        marks = [0] + [min(cs, max(0, int(round((w["s"] - t0) * 100)))) for w in words[1:]] + [cs]
        for k in range(1, len(marks)):
            marks[k] = max(marks[k], marks[k - 1])
        ks = iter(b - a for a, b in zip(marks, marks[1:]))
        return r"\N".join(" ".join(rf"{{\kf{next(ks)}}}" + _ass_text(w) for w in r) for r in rows)
    total = sum(len(w) for r in rows for w in r) or 1
    cs = int(dur * 100)
    return r"\N".join(" ".join(rf"{{\kf{max(1, int(cs * len(w) / total))}}}" + _ass_text(w) for w in r) for r in rows)


# ASS 글자 크기는 줄 높이(위+아래 여백) 기준이라 화면(CSS) 글자보다 작게 나옴 → Pretendard 비율만큼 키워 미리보기와 같게
FONT_K = 1.194
# 타이틀 글꼴 (style.font): 이름 → (ASS 글꼴 이름, 크기 비율) · 비율은 글꼴의 줄 높이(win 위+아래)/em — libass 로 그려 재어 확인 (Pretendard 1.19 · 검은고딕 1.02 · 도현 1.0)
TITLE_FONTS = {"Black Han Sans": ("Black Han Sans", 1.02), "Do Hyeon": ("Do Hyeon", 1.0)}


def _rot(s):
    """타이틀 기울기(도, 시계 방향 +) — 숫자가 아니면 0."""
    try:
        r = float(s.get("rot") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(-45.0, min(45.0, r)) if math.isfinite(r) else 0.0


# ---------- 글자·그림 줄 (C 자막 · T 타이틀 · G 도형·전술 그림 · D-296) ----------
# 편집본마다 seq.otracks = 줄 목록 (아래 → 위 = 뒤 → 앞). 목록 차례가 미리보기 겹침 · 내보내기 ASS 층(Layer) · 타임라인 줄 차례.
# 항목(자막·타이틀·도형·전술 그림)의 trk 가 없으면 그 종류 첫 줄(C1·T1·G1) · 첫 줄 이름은 저장하지 않음.
# 예전 겹침 순서 G1 < C1 < T1 (도형·전술 그림 → 자막 → 타이틀) 이 기본 → 예전 프로젝트는 내보낸 화면이 그대로.
# editor.html otList · trkOf · capTrk · capTrackDefault · capStyleOf 와 같은 규칙.
OT_BASE = ({"id": "G1", "k": "g"}, {"id": "C1", "k": "c"}, {"id": "T1", "k": "t"})
OT_MAX = 9       # 종류마다 줄 수 한도
OT_EPS = 1e-3    # 시간 겹침 판단 (가장자리가 닿는 것은 겹침 아님)
_OT_ID = re.compile(r"[CTG][1-9]")
_CN_ID = re.compile(r"C[2-9]")


def _ot_insert(out, o):
    """같은 종류 맨 위 줄 바로 위에 새 줄 (그 종류가 없으면 맨 위)."""
    idx = max([i for i, x in enumerate(out) if x["k"] == o["k"]], default=len(out) - 1)
    out.insert(idx + 1, o)


def overlay_tracks(seq):
    """글자·그림 줄 목록 (아래 → 위) — seq.otracks 를 정리: 틀린 줄·겹친 id 는 버림 · C1 의 style/hide 는 뺌(C1 은 captionStyle·captionsOn) ·
    빠진 기본 줄(G1 맨 아래 · C1 · T1 맨 위) · 자막이 가리키는 C 줄이 없으면 만듦(모양은 아직 안 정함 · 숨김은 captionsOn 따라) ·
    줄 목록이 있으면 타이틀·도형·전술 그림이 가리키는 없는 줄도 만듦. seq 는 바꾸지 않음 (editor.html otList 와 같은 규칙)."""
    out, seen = [], set()
    raw = seq.get("otracks")
    for o in raw if isinstance(raw, list) else ():
        tid = o.get("id") if isinstance(o, dict) else None
        if not (isinstance(tid, str) and _OT_ID.fullmatch(tid) and o.get("k") == tid[0].lower() and tid not in seen):
            continue
        seen.add(tid)
        o = dict(o)
        if tid == "C1":
            o.pop("style", None)
            o.pop("hide", None)
        out.append(o)
    if "G1" not in seen:
        out.insert(0, dict(OT_BASE[0]))
    for b in OT_BASE[1:]:
        if b["id"] not in seen:
            out.append(dict(b))
    have = {o["id"] for o in out}
    caps = seq.get("msgCaps") or seq.get("captions") or ()
    for tid in sorted({c.get("trk") for c in caps if isinstance(c, dict) and isinstance(c.get("trk"), str) and _CN_ID.fullmatch(c["trk"])} - have):
        _ot_insert(out, {"id": tid, "k": "c", "hide": not seq.get("captionsOn", True)})
        have.add(tid)
    if isinstance(raw, list):
        want = set()
        for kind, key in (("T", "titles"), ("G", "shapes"), ("G", "tactics")):
            for x in seq.get(key) or ():
                tid = x.get("trk") if isinstance(x, dict) else None
                if isinstance(tid, str) and _OT_ID.fullmatch(tid) and tid[0] == kind:
                    want.add(tid)
        for tid in sorted(want - have):
            _ot_insert(out, {"id": tid, "k": tid[0].lower()})
    return out


def track_of(x, kind, ids):
    """타이틀·도형·전술 그림이 놓인 줄 — trk 가 이 편집본 줄이고 종류(T·G)가 맞으면 그 줄, 아니면 그 종류 첫 줄 (editor.html trkOf)."""
    tid = x.get("trk") if isinstance(x, dict) else None
    return tid if isinstance(tid, str) and tid in ids and tid[0] == kind else kind + "1"


def cap_trk(cap, mid="main"):
    """자막이 놓인 줄 — 원본 자막의 trk(C2~C9) · 보관함의 다른 영상 자막은 늘 C1 (editor.html capTrk)."""
    tid = cap.get("trk") if isinstance(cap, dict) else None
    return tid if mid == "main" and isinstance(tid, str) and _CN_ID.fullmatch(tid) else "C1"


def cap_track_default(c1, n, fmt, more=0, own=None):
    """높이(y)를 정하지 않은 자막 트랙(Cn)의 높이: C1 에서 자막 한 줄 높이씩 (n-1)번 — C1 이 화면 아래쪽(y ≥ 0.5)이면 위로(0.1 까지),
    위쪽이면 아래로(0.95 까지). more: 글줄 몇 개 더 (위로 갈 때는 그때 나오는 자막 1 이 여러 줄인 만큼 · 아래로 갈 때는 자기 글 · cap_row_y · D-308) ·
    own: 그 트랙 모양 (아래로 갈 때 띠 높이). 반올림 없이 editor.html capTrackDefault 와 같은 계산 차례 (두 쪽 값이 비트까지 같음 · more 0 이면 예전 값과도 같음)."""
    own = c1 if own is None else own
    H = 1920 if fmt == "shorts" else 1080
    y = float(c1["y"])
    if y >= 0.5:
        band = (1.25 * float(c1["size"]) + 2 * float(c1["strokeW"])) / H + 0.012
        return max(0.1, y - ((n - 1) * band + more * 1.25 * float(c1["size"]) / H))
    band = (1.25 * float(own["size"]) + 2 * float(own["strokeW"])) / H + 0.012
    return min(0.95, y + (n - 1) * band + more * 1.25 * float(own["size"]) / H)


def cap_style_of(seq, o):
    """자막 트랙의 글자 모양 — C1 은 captionStyle · 다른 트랙은 C1 모양 위에 자기 style(고친 값만 · D-308) ·
    높이(y)를 안 정했으면 C1 에서 한 줄씩 옮긴 높이 (색만 바꾼 자막 2 도 자막 1 크기·자리를 따라감 · editor.html capStyleOf)."""
    st = seq["captionStyle"]
    if o["id"] == "C1":
        return st
    own = o["style"] if isinstance(o.get("style"), dict) else {}
    m = {**st, **own}
    if "y" not in own:
        m["y"] = cap_track_default(st, int(o["id"][1]), seq.get("format"), 0, m)
    return m


def cap_lines(text, st, W):
    """자막 하나가 화면에서 몇 줄인지 어림 (editor.html capLines) — cap_fit 으로 글자를 줄여 들어가면 글 속 줄 수 그대로 ·
    아니면 화면 폭 92% 에서 띄어쓰기마다 바꿈 (text_box 와 같은 셈)."""
    paras = str(text if text is not None else "").replace("\r", "").split("\n")
    if cap_fit(text, st, W) < 1 or hug_one(text, st, W):   # (스킨 말 자막: 실제 글자 폭으로 한 줄이면 줄을 바꾸지 않음 · D-533)
        return len(paras)
    return cap_wrap_lines(text, st, W)


def cap_wrap_lines(text, st, W):
    """글 속 줄마다 화면 폭 92% 에서 띄어쓰기마다 바꾼 줄 수 (글자 크기 그대로 · 스킨 말 자막은 실제 글자 폭 표 · editor.html capWrapLines)."""
    paras = str(text if text is not None else "").replace("\r", "").split("\n")
    size, stroke = float(st.get("size") or 0), float(st.get("strokeW") or 0)
    black, fm = st.get("weight") == "Black", ep_font_now() or (SKIN_HUG if st.get("hug") == "skin" else None)
    avail, sp = 0.92 * W - 2 * stroke, _em(" ", fm=fm) * size
    n = 0
    for para in paras:
        cur = 0.0
        n += 1
        for w in para.split(" "):
            ww = sum(_em(ch, black, fm) for ch in w) * size
            if cur and cur + sp + ww > avail:
                n += 1
                cur = ww
            else:
                cur = cur + (sp if cur else 0.0) + ww
    return n


def cap_row_y(seq, o, st, ev, c1s, W):
    """높이(y)를 정하지 않은 자막 트랙(Cn)의 자막 하나가 놓일 높이 (D-308 · editor.html capRowY) — 트랙 기본 높이에서
    자막 1 이 화면 아래쪽이면 그 자막이 나오는 동안 보이는 자막 1 이 두 줄 이상인 만큼 더 위로 · 위쪽이면 자기 글이 두 줄 이상인 만큼 더 아래로.
    ev·c1s 는 timeline_captions 항목 (start·end·text) · c1s 는 보이는 자막 1 자막들."""
    if o["id"] == "C1" or (isinstance(o.get("style"), dict) and "y" in o["style"]):
        return st["y"]
    c1 = seq["captionStyle"]
    more = 0
    if float(c1["y"]) >= 0.5:
        for c in c1s:
            if c["start"] < ev["end"] - OT_EPS and ev["start"] < c["end"] - OT_EPS:
                more = max(more, cap_lines(c["text"], c1, W) - 1)
    else:
        more = cap_lines(ev["text"], st, W) - 1
    return cap_track_default(c1, int(o["id"][1]), seq.get("format"), more, st) if more > 0 else st["y"]


def track_visible(seq, o):
    """줄을 보이는지 — C1 은 '영상에 자막 넣기'(captionsOn) · 다른 줄은 hide 가 아니면. 숨긴 줄은 미리보기·내보내기에서 빠짐 (SRT 도 기본으로 빠짐 · srt_default_tracks · D-346)."""
    return seq.get("captionsOn", True) if o["id"] == "C1" else not o.get("hide")


def build_ass(proj, W, H, fps=30, frame_grid=True):
    """frame_grid: 시작·끝을 프레임 격자로 (D-328 · 미리보기와 같은 프레임) — False 는 예전 반올림 (시험 golden 비교용).
    은평 스타일 화면이면 모든 글자를 이 PC 의 KFA 글꼴로 (ep_font_scope · 못 찾으면·다른 화면은 예전과 같은 바이트 · BR-311)."""
    with ep_font_scope(ep_font_on(proj)):
        return _build_ass(proj, W, H, fps, frame_grid)


EP_FONT_FILE = "ep_font"   # 내보내기 임시 글꼴 폴더(fonts/)에 넣는 은평 글꼴 파일 이름 (영문 · libass 가 폴더째 읽어 깔리지 않은 고른 글꼴도 씀)


def _write_subs(tmp, proj, W, H, fps):
    """내보내기 임시 폴더에 fonts/(앱 글꼴 + 은평 스타일이면 KFA 글꼴 파일) + subs.ass — 글꼴 파일을 못 넣으면 ASS 도 지금 글꼴로
    (libass 가 없는 글꼴 이름을 받으면 엉뚱한 글꼴로 그림)."""
    shutil.copytree(FONTS, tmp / "fonts", dirs_exist_ok=True)
    fx = ep_font() if ep_font_on(proj) else None
    if fx:
        try:
            shutil.copyfile(fx["path"], tmp / "fonts" / (EP_FONT_FILE + fx["ext"]))
        except OSError as e:
            studiolog.write(f"은평 스타일 글꼴을 내보내기에 넣지 못해 지금 글꼴로 만들어요 · {Path(fx['path']).name} · {e}")
            fx = None
    with ep_font_scope(fx or False):
        (tmp / "subs.ass").write_text(_build_ass(proj, W, H, fps, True), encoding="utf-8")


def ass_font(s):
    """글자 모양 → (ASS 글꼴 이름, 크기 비율, 굵게) — 은평 글꼴 범위면 그 글꼴 (배운 스타일·타이틀 글꼴보다 먼저 · 굵기는 그 글꼴 파일의 굵기
    그대로 = libass 가 그 글꼴을 고르고 굵게 흉내 내지 않음 · 미리보기 font-weight 400)."""
    fx = ep_font_now()
    if fx:
        return fx["family"], fx["k"], fx["weight"] if fx["weight"] > 400 else 0
    if s.get("font") in TITLE_FONTS:  # 굵기가 하나뿐인 글꼴 → 굵게 흉내 없이 (미리보기도 font-weight 400)
        font, k = TITLE_FONTS[s["font"]]
        return font, k, 0
    return ("Pretendard Black", FONT_K, 0) if s["weight"] == "Black" else ("Pretendard", FONT_K, -1)


def _build_ass(proj, W, H, fps, frame_grid):
    st = proj["captionStyle"]
    at = (lambda t: _ass_ftime(t, fps)) if frame_grid else _ass_time

    def style_line(name, s):
        font, k, bold = ass_font(s)
        if s.get("bgOn"):
            border, outline, ocol = 3, max(6, s["strokeW"]), _ass_color(s["bg"], 1 - s["bgOpacity"])
        else:
            border, outline, ocol = 1, s["strokeW"], _ass_color(s["stroke"])
        prim = _ass_color(s["highlight"] if s["effect"] == "karaoke" else s["fill"])
        sec = _ass_color(s["fill"])
        return (f"Style: {name},{font},{round(s['size'] * k)},{prim},{sec},{ocol},{ocol},{bold},0,0,0,100,100,0,0,"
                f"{border},{outline},0,2,40,40,0,1")

    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
             "MarginL, MarginR, MarginV, Encoding",
             style_line("Cap", st)]
    # 글자·그림 줄 (D-296) — 자막 줄마다 스타일 Cap(C1) · Cap2~Cap9 (예전 프로젝트는 Cap 하나 → 스타일 묶음이 예전과 같음)
    ots = overlay_tracks(proj)
    ids = {o["id"] for o in ots}
    cst = {o["id"]: cap_style_of(proj, o) for o in ots if o["k"] == "c"}
    cname = {tid: "Cap" if tid == "C1" else "Cap" + tid[1:] for tid in cst}
    for tid in sorted(cst):
        if tid != "C1":
            lines.append(style_line(cname[tid], cst[tid]))
    for t in proj["titles"]:
        lines.append(style_line(f"T{t['id']}", t["style"]))
    lines.append("Style: Shape,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1")
    lines.append(tactic.STYLE_LINE)
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    tl_total = seq_total(proj)

    def shape_line(sh, layer):
        x, y = int(sh["x"] * W), int(sh["y"] * H)
        w, h = max(2, int(sh["w"] * W)), max(2, int(sh["h"] * H))
        r = int(min(sh.get("radius", 0) / 100 * min(w, h) / 2, min(w, h) / 2))
        if r > 0:  # 둥근 모서리 (원에 가까운 베지어, 미리보기 border-radius 와 같게)
            c = round(r * 0.448)
            path = (f"m {r} 0 l {w - r} 0 b {w - c} 0 {w} {c} {w} {r} l {w} {h - r} b {w} {h - c} {w - c} {h} {w - r} {h} "
                    f"l {r} {h} b {c} {h} 0 {h - c} 0 {h - r} l 0 {r} b 0 {c} {c} 0 {r} 0")
        else:
            path = f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"
        start = 0.0 if sh.get("full") else sh["start"]
        end = tl_total if sh.get("full") else min(tl_total, sh["start"] + sh["dur"])
        col, a = _ass_color(sh["color"]), int((1 - sh.get("opacity", 1.0)) * 255)
        return (f"Dialogue: {layer},{at(start)},{at(end)},Shape,,0,0,0,,"
                rf"{{\an7\pos({x},{y})\p1\bord0\shad0\1c{col[:2]}{col[4:]}&\1a&H{a:02X}&}}{path}{{\p0}}")

    def event(style, s, start, end, text, words=None, fit=False, layer=1, wb=None):
        al = s.get("align", "center")
        an = {"left": 1, "right": 3}.get(al, 2)
        x, y = int(W * s.get("x", 0.5)), int(H * s["y"])
        k = min(cap_fit(text, s, W), ep_room_fit(text, s, W, H, wb)) if fit else 1.0  # 로고 화면 말 자막은 아래 띠 안 (wb · BR-283)
        fs = rf"\fs{round(s['size'] * k * (ep_font_now() or {}).get('k', FONT_K))}" if k < 1 else ""  # 화면보다 긴 한 줄 자막은 글자만 조금 작게 (은평 글꼴은 그 비율)
        rz = -_rot(s)  # 화면은 시계 방향 + · ASS \frz 는 반시계 방향 + (기준점은 \pos 자리 = 미리보기 transform-origin)
        fr = rf"\frz{_num(round(rz, 2))}" if rz else ""
        q2 = r"\q2" if s.get("hug") == "skin" and (not fit or hug_one(text, s, W)) else ""   # 스킨 큰 제목·한 줄 말 자막은 libass 가 줄을 바꾸지 않게 (D-531 · D-533)
        tags = rf"{{\an{an}\pos({x},{y}){fs}{fr}{q2}}}" if s["effect"] != "slide" else rf"{{\an{an}{fs}{fr}{q2}}}"
        if frame_grid:  # 노래방 \kf 는 실제 ASS 시작·끝 기준 (합 = 그 길이 · 프레임 격자로 옮긴 만큼)
            k0, k1 = _ass_secs(at(start)), _ass_secs(at(end))
        else:
            k0, k1 = start, end
        body = _karaoke(text, k1 - k0, words, k0) if s["effect"] == "karaoke" else _ass_text(text)
        eff = _effect_tags(s["effect"], x, y, end - start, rz)
        lines.append(f"Dialogue: {layer},{at(start)},{at(end)},{style},,0,0,0,,{tags}{eff}{body}")

    # 글자·그림 줄을 아래(뒤)부터 — 층(Layer) = 쌓는 차례 (editor.html 미리보기 .olay z-index 와 같음) · 숨긴 줄은 빠짐
    # 예전 프로젝트(G1 < C1 < T1): 도형·전술 그림 0 · 자막 1 · 타이틀 2 → 화면은 예전과 같음 (예전에도 타이틀은 자막 뒤에 써서 위)
    caps = timeline_captions(proj) if any(o["k"] == "c" and track_visible(proj, o) for o in ots) else []
    c1s = [c for c in caps if c.get("trk", "C1") == "C1"] if track_visible(proj, {"id": "C1"}) else []  # 두 줄 자막 1 과 안 겹치게 (cap_row_y · D-308)
    wb = ep_win_bottom(proj, W, H)   # 로고 화면이면 말 자막(자막 1)은 영상 창 아래 끝 밑 (ep_room_fit · 아니면 None = 예전 그대로)
    for layer, o in enumerate(ots):
        tid, k = o["id"], o["k"]
        if not track_visible(proj, o):
            continue
        if k == "g":  # 도형 → 같은 줄 전술 그림 (프레임마다 미리보기와 같은 모양 — tactic.ops_at · 스포트라이트는 먼저)
            for sh in proj.get("shapes") or []:
                if track_of(sh, "G", ids) == tid:
                    lines.append(shape_line(sh, layer))
            lines += tactic.ass_lines([x for x in proj.get("tactics") or [] if isinstance(x, dict) and track_of(x, "G", ids) == tid],
                                      W, H, fps, tl_total, _ass_time, layer=layer)
        elif k == "c":
            for c in caps:
                if c.get("trk", "C1") == tid:
                    s1 = cst[tid]
                    if tid != "C1":
                        yy = cap_row_y(proj, o, s1, c, c1s, W)
                        if yy != s1["y"]:
                            s1 = dict(s1, y=yy)
                    if c.get("pos"):  # '이 자막만' 옮긴 자리 (트랙 높이보다 먼저 · D-321)
                        s1 = dict(s1, **c["pos"])
                    event(cname[tid], s1, c["start"], c["end"], c["text"], c.get("words"), fit=True, layer=layer, wb=wb if tid == "C1" else None)
        else:
            for t in proj["titles"]:
                if track_of(t, "T", ids) == tid and t["start"] < tl_total:
                    event(f"T{t['id']}", t["style"], t["start"], min(tl_total, t["start"] + t["dur"]), t["text"], layer=layer)
    return "\n".join(lines) + "\n"


def _srt_ts(sec):
    """SRT 시각 (밀리초로 먼저 반올림 → 59.9996초가 '60,000' 이 되지 않게, 음수는 0)."""
    ms = int(round(max(0.0, float(sec)) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt(caps):
    out = []
    for i, c in enumerate(caps, 1):
        out.append(f"{i}\n{_srt_ts(c['start'])} --> {_srt_ts(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


# ---------- 자막 파일(SRT) 가져오기 (D-345) ----------

SRT_MAX = 5 << 20      # 가져올 자막 파일 크기 한도 (바이트 · 두 시간짜리 자막도 1MB 안팎)
SRT_TRIM = 0.5         # 앞 자막 끝이 다음 자막 시작을 이만큼(초)까지 넘으면 앞 자막 끝을 줄임 (자막 프로그램이 흔히 남기는 겹침)
SRT_LANES = OT_MAX     # 더 길게 겹치면(두 사람이 같이 말함) 겹치지 않는 다음 자막 트랙으로 · 9개까지
_SRT_TIME = r"(?:\d+:)?\d{1,2}:\d{1,2}(?:[,.:]\d{1,3})?"
_SRT_ARROW = re.compile(rf"^\s*({_SRT_TIME})\s*-+>\s*({_SRT_TIME})")
_SRT_TAG = re.compile(r"</?[A-Za-z][^<>]*>|\{\\[^{}]*\}")


def srt_decode(data):
    """자막 파일 바이트 → (글, 인코딩) — BOM(UTF-8·UTF-16) → UTF-8 → 한국어 Windows(CP949 · 메모장 'ANSI' 로 저장한 자막)."""
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace"), "utf-16"
    head = data[:400]
    if head and head.count(b"\0") > len(head) // 4:  # BOM 없는 UTF-16 (글자마다 0 바이트)
        return data.decode("utf-16-le" if head[1:2] == b"\0" else "utf-16-be", "replace"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp949"), "cp949"
    except UnicodeDecodeError:
        return data.decode("cp949", "replace"), "cp949"


def _srt_secs(s):
    """'00:01:02,500' · '01:02.5' · '00:01:02:500' → 초."""
    s = s.strip()
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?", s)
    if m:
        h, mi, se, ms = m.groups()
    else:
        p = s.split(":")
        if len(p) != 4:
            raise ValueError(s)
        h, mi, se, ms = p
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + (int(ms) / 10 ** len(ms) if ms else 0.0)


def parse_srt(text):
    """SRT 글 → (자막 [{start, end, text}] 시작 순, 못 읽어 버린 수).
    줄바꿈 CRLF·CR · 번호 없는 파일 · 번호와 시각 사이 빈 줄도 읽음 · 꾸밈(<i> <b> <font …> {\\an8})과 &amp; 같은 글자는 풀고 줄바꿈은 그대로 ·
    시각이 거꾸로이거나 글이 없는 자막은 버림."""
    lines = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    hits = [(i, m) for i, m in ((i, _SRT_ARROW.match(ln)) for i, ln in enumerate(lines)) if m]
    # 번호를 단 파일이면 시각 줄 바로 앞 줄이 숫자 → 앞 자막 글 끝의 숫자 한 줄은 다음 자막 번호 (번호 없는 파일의 '3' 같은 글은 그대로)
    numbered = sum(1 for i, _ in hits if i > 0 and lines[i - 1].strip().isdigit()) * 2 >= len(hits)
    cues, bad = [], 0
    for k, (i, m) in enumerate(hits):
        body = lines[i + 1:hits[k + 1][0] if k + 1 < len(hits) else len(lines)]
        while body and not body[-1].strip():
            body.pop()
        if numbered and k + 1 < len(hits) and body and body[-1].strip().isdigit():
            body.pop()
        out = []
        for ln in body:
            ln = re.sub(r"[ \t 　]+", " ", _html.unescape(_SRT_TAG.sub("", ln))).strip()
            if ln:
                out.append(ln)
        try:
            a, b = _srt_secs(m[1]), _srt_secs(m[2])
        except ValueError:
            bad += 1
            continue
        if not out or b <= a:
            bad += 1
            continue
        cues.append({"start": a, "end": b, "text": "\n".join(out)})
    cues.sort(key=lambda c: c["start"])
    return cues, bad


def _srt_frame(t, fps=30):
    """SRT 시각(초) → 시퀀스 프레임: 그 시각에 처음 보이는 프레임 — 미리보기·내보내기(_ass_ftime)와 같은 ceil 규칙.
    SRT 는 밀리초라 프레임 시각을 반올림(+0.5ms)·버림(−1ms)해 적은 파일도 같은 프레임 (0.5ms 여유 · 예전 반올림은 내보낸 SRT 를
    다시 가져오면 7개 중 7개가 한 프레임 앞으로 왔고, ,150 은 4 · ,050 은 2 처럼 반 프레임 시각이 짝수 쪽으로 갈렸음)."""
    return max(0, math.ceil((float(t) - 0.0005) * fps - 1e-9))


def srt_lanes(cues, fps=30):
    """가져온 자막을 프레임에 맞추고(프리미어처럼 시퀀스 프레임) 겹침 정리 → (자막 [{start, end, text, lane}], 끝을 줄인 수, 다른 트랙으로 보낸 수).
    겹침이 SRT_TRIM 초 이하이고 앞 자막 길이의 절반 이하면 앞 자막 끝을 다음 자막 시작으로 · 더 길면 겹치지 않는 다음 트랙(lane 1, 2 …)."""
    lanes, out, trimmed, moved = [], [], 0, 0
    lim = SRT_TRIM * fps + 1e-6
    for c in cues:
        s = _srt_frame(c["start"], fps)
        x = {"s": s, "e": max(s + 1, _srt_frame(c["end"], fps)), "text": c["text"]}
        k = 0
        while k < len(lanes):
            p = lanes[k][-1]
            if p["e"] <= s:
                break
            ov = p["e"] - s
            if ov <= lim and ov * 2 <= p["e"] - p["s"] and s > p["s"]:
                p["e"] = s
                trimmed += 1
                break
            k += 1
        if k >= len(lanes):
            if len(lanes) < SRT_LANES:
                lanes.append([])
            k = len(lanes) - 1  # 9개가 다 차면 마지막 트랙에 겹친 채로
        moved += 1 if k else 0
        x["lane"] = k
        lanes[k].append(x)
        out.append(x)
    return ([{"start": round(x["s"] / fps, 6), "end": round(x["e"] / fps, 6), "text": x["text"], "lane": x["lane"]} for x in out],
            trimmed, moved)


def srt_import(data, fps=30):
    """자막 파일(바이트) → {cues, encoding, bad, trimmed, moved, lanes} — 편집실 'SRT 가져오기'가 씀 (어디에·어느 트랙에 놓을지는 편집실이 정함)."""
    if len(data) > SRT_MAX:
        raise ValueError("자막 파일이 너무 커요 (5MB 까지)")
    text, enc = srt_decode(data)
    cues, bad = parse_srt(text)
    if not cues:
        raise ValueError("자막을 하나도 읽지 못했어요 · SRT 자막 파일인지 확인해 주세요")
    try:
        fps = int(fps)
    except (TypeError, ValueError):
        fps = 30
    out, trimmed, moved = srt_lanes(cues, fps if 1 <= fps <= 240 else 30)
    return {"cues": out, "encoding": enc, "bad": bad, "trimmed": trimmed, "moved": moved, "lanes": 1 + max(c["lane"] for c in out)}


# ---------- 자막 파일(SRT) 내보내기 — 트랙마다 (D-346) ----------

def srt_track_list(proj):
    """자막 트랙 [{id, name, show}] (번호 차례 C1, C2 …) — 이름은 editor.html otLabel 과 같음 · show: 영상에 보이는지(숨김·'영상에 자막 넣기')."""
    out = [{"id": o["id"], "name": str(o.get("name") or f"자막 {o['id'][1:]}"), "show": track_visible(proj, o)}
           for o in overlay_tracks(proj) if o["k"] == "c"]
    return sorted(out, key=lambda o: int(o["id"][1:]))


def _fname(s):
    """파일 이름에 못 쓰는 글자를 뺌 (Windows)."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", str(s)).strip(" .")


def srt_default_tracks(proj):
    """내보낼 자막 트랙 기본값: 보이는 자막 트랙 — 하나도 안 보이면 자막 1 (영상엔 안 넣고 자막 파일만 유튜브에 올리는 경우 · D-301)."""
    return [t["id"] for t in srt_track_list(proj) if t["show"]] or ["C1"]


def srt_files(proj, stem, t_lo, t_hi, fps, tracks=None, split=False):
    """내보낼 자막 파일 [(파일 이름, 내용, 트랙 id 들)] (D-346).
    tracks: 넣을 자막 트랙 id (None 이면 srt_default_tracks — 숨긴 트랙은 빠짐) · split: 트랙마다 따로 (프리미어처럼 트랙 하나 = 파일 하나) —
    첫 트랙이 '<이름>.srt'(올리기 키트·유튜브 자막이 이 파일을 씀), 다음 트랙은 '<이름>_<트랙 이름>.srt' · 합치면 '<이름>.srt' 하나에 시작 순.
    구간 내보내기는 구간 안으로 잘라 0초부터 · 잘려서 2프레임도 안 남은 조각은 뺌 (예전: 첫 자막이 2프레임짜리로 남음)."""
    info = srt_track_list(proj)
    want = srt_default_tracks(proj) if tracks is None else [t["id"] for t in info if t["id"] in set(tracks)]
    names = {t["id"]: t["name"] for t in info}
    # 시각은 영상에 실제로 보이는 프레임으로 (태운 영상·미리보기·투명 영상과 같은 _ass_ftime 의 ceil · 프레임 시각을 적음) —
    # 예전엔 받아쓴 시각(프레임 사이)을 그대로 적어 프리미어·다시 가져오기가 한 프레임 앞으로 맞췄음
    k0, kmax = int(round(t_lo * fps)), int(round((t_hi - t_lo) * fps))
    fk = lambda t: math.ceil((t - 1e-6) * fps) - k0  # noqa: E731
    by = {}
    for c in timeline_captions(proj):
        tid = c.get("trk", "C1")
        if tid not in want:
            continue
        ks, ke = max(0, fk(c["start"])), min(kmax, fk(c["end"]))
        cut = c["start"] < t_lo - 1e-6 or c["end"] > t_hi + 1e-6
        if ke - ks < (2 if cut else 1):
            continue
        by.setdefault(tid, []).append(dict(c, start=ks / fps, end=ke / fps))
    if not split:
        caps = sorted((c for t in want for c in by.get(t, ())), key=lambda c: c["start"])
        return [(f"{stem}.srt", _srt(caps), [t for t in want if by.get(t)])] if caps else []
    out, used = [], set()
    for t in want:
        if by.get(t):
            base = stem if not out else f"{stem}_{_fname(names.get(t) or t) or t}"
            fn, n = f"{base}.srt", 2
            if fn.lower() in used:  # 트랙 이름이 같으면 (이름은 마음대로 바꿀 수 있음) 트랙 번호를 붙여 덮어쓰지 않게 (Windows 는 대소문자 같음)
                fn = f"{base} ({t}).srt"
            while fn.lower() in used:
                fn, n = f"{base} ({n}).srt", n + 1
            used.add(fn.lower())
            out.append((fn, _srt(by[t]), [t]))
    return out


# ---------- 프리미어 XML (FCP7 xmeml · D-347) ----------

PPRO_TICKS = 254016000000   # 프리미어 시간 단위 (1초) — transitionitem cutPointTicks
OVERLAY_TAG = "_글자도형"    # 글자·도형만 투명 영상 파일 이름 꼬리 (D-348)
REPORT_TAG = "_프리미어_빠진 것"  # 프리미어 XML 에 못 넣은 것 목록 (.txt)
SKIN_TAG = "_스킨.png"    # 프리미어 XML 옆에 복사하는 풋살사관학교 스킨 그림 (영상 트랙 위에 직접 얹음 · D-536)
_ACH = {}


def _audio_channels(p):
    """소리 채널 수 (모노 1 · 스테레오 2 · 못 읽으면 2) — 파일 크기·시각으로 기억."""
    try:
        st = Path(p).stat()
    except OSError:
        return 2
    k = (str(p), st.st_size, st.st_mtime_ns)
    if k not in _ACH:
        n = 2
        try:
            err = core.run([core.ffmpeg(), "-hide_banner", "-i", str(p)]).stderr
            m = re.search(r"Audio:[^\n]*?Hz, (mono|stereo|(\d+) channels|[\d.]+\(?)", err)
            if m:
                n = 1 if m[1] == "mono" else int(m[2]) if m[2] else 2
        except Exception:  # noqa: BLE001 — 못 읽으면 스테레오로
            n = 2
        _ACH[k] = max(1, n)
    return _ACH[k]


def fit_rect(it, md, seq, W, H, m=None):
    """미디어를 화면에 놓는 자리 (효과 컨트롤 100% 기준 · 반올림 없음) — editor.html fitRect 와 같은 셈 (미리보기 화면 그대로).
    bx·by·bw·bh: 보이는 상자 · mx·my·mw·mh: 상자 안 원본 전체 그림(잘린 곳 포함) · bg: 쇼츠 위아래 박스(box)·흐린 배경(blur) ·
    m: 원본 시각 — 화면 좌우 따라가기 키프레임(rfk · BR-326)이 있으면 그때 자리 (없으면 reframe)."""
    sw, sh = md.get("w") or W, md.get("h") or H
    ref = W / (1080 if seq.get("format") == "shorts" else 1920)
    lay = {"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0, **(seq.get("layout") or {})}
    rf = rf_at(it, m) if m is not None else float(it.get("reframe", 0.5) if it.get("reframe") is not None else 0.5)
    fit = it.get("fit") or "auto"
    if seq.get("format") == "shorts" and it["track"] == "V1" and fit == "auto":
        ct, cb = float(lay.get("cropTop") or 0), float(lay.get("cropBottom") or 0)
        vh = sh * max(0.2, 1 - ct - cb)
        if lay["mode"] not in ("box", "blur"):
            s = max(W / sw, H / vh)
            scw, sch = max(W, sw * s), max(H, vh * s)
            return {"bx": 0.0, "by": 0.0, "bw": W, "bh": H, "mw": sw * s, "mh": sh * s, "mx": -(scw - W) * rf, "my": -(sch - H) / 2 - ct * sh * s, "bg": None}
        zw = W * max(1.0, float(lay["zoom"]))
        s = zw / sw
        zh = vh * s
        ch = min(zh, H)
        return {"bx": 0.0, "by": (H - ch) * float(lay["vpos"]), "bw": W, "bh": ch, "mw": zw, "mh": sh * s, "mx": -(zw - W) * rf,
                "my": -(zh - ch) / 2 - ct * sh * s, "bg": lay["mode"], "bar": lay["bar"],
                "pics": lay["mode"] == "box" and bool(lay.get("band") or lay.get("logo"))}
    if fit == "auto":
        fit = "native" if md.get("kind") == "image" and not md.get("freeze") and sw * ref <= W and sh * ref <= H else "contain"
    s = ref if fit == "native" else (max if fit == "cover" else min)(W / sw, H / sh)
    scw, sch = sw * s, sh * s
    if fit == "cover":
        return {"bx": 0.0, "by": 0.0, "bw": W, "bh": H, "mw": scw, "mh": sch, "mx": -(scw - W) * rf, "my": -(sch - H) / 2, "bg": None}
    return {"bx": (W - scw) / 2, "by": (H - sch) / 2, "bw": scw, "bh": sch, "mw": scw, "mh": sch, "mx": 0.0, "my": 0.0, "bg": None}


def premiere_motion(it, md, seq, W, H):
    """클립 자리 → 프리미어 Basic Motion·Crop (D-347) — 미리보기·내보내기와 같은 그림이 되게:
    원본 가운데가 놓일 자리(center · 시퀀스 가운데에서 떨어진 거리를 시퀀스 너비·높이로 나눈 값 · 아래가 +) · 확대(원본 픽셀 100% 기준) ·
    회전(시계 방향 +) · Crop(원본 기준 % — 쇼츠 위아래 자르기·화면 채우기로 잘린 곳 · 확대를 줄이거나 돌려도 보이는 곳이 같게).
    키프레임이 있으면 클립 가운데 시각의 값 하나 (animated True — 빠진 것 목록에 적음)."""
    m = i_mt(it, (float(it["start"]) + i_end(it)) / 2)
    f = fit_rect(it, md, seq, W, H, m)
    sw, sh = md.get("w") or W, md.get("h") or H
    s = f["mw"] / sw
    pos = kf_at(param(it, "pos"), m)
    sc = float(kf_at(param(it, "scale"), m)) / 100
    rot = float(kf_at(param(it, "rot"), m))
    anc = param(it, "anchor")["v"]
    ax, ay = f["bx"] + float(anc[0]) * f["bw"], f["by"] + float(anc[1]) * f["bh"]
    dx, dy = f["bx"] + f["mx"] + f["mw"] / 2 - ax, f["by"] + f["my"] + f["mh"] / 2 - ay
    r = math.radians(rot)
    cx = ax + (float(pos[0]) - 0.5) * W + sc * (math.cos(r) * dx - math.sin(r) * dy)
    cy = ay + (float(pos[1]) - 0.5) * H + sc * (math.sin(r) * dx + math.cos(r) * dy)
    l, rr = max(0.0, -f["mx"] / s), min(float(sw), (f["bw"] - f["mx"]) / s)
    t, b = max(0.0, -f["my"] / s), min(float(sh), (f["bh"] - f["my"]) / s)
    crop = [max(0.0, x) for x in (l / sw * 100, (sw - rr) / sw * 100, t / sh * 100, (sh - b) / sh * 100)]
    anim = any(len({json.dumps(k["v"]) for k in param(it, key)["k"]}) > 1 for key in ("pos", "scale", "rot", "opacity")) \
        or len({round(float(k.get("v", 0.5)), 4) for k in it.get("rfk") or [] if isinstance(k, dict)}) > 1   # 얼굴 따라가기 화면 좌우 (BR-326)
    return {"scale": s * sc * 100, "rot": rot, "center": ((cx - W / 2) / W, (cy - H / 2) / H), "crop": [0.0 if x < 1e-3 else x for x in crop],
            "opacity": float(kf_at(param(it, "opacity"), m)), "animated": anim, "bg": f["bg"], "bar": f.get("bar"), "pics": f.get("pics")}


def _xnum(v, nd=6):
    s = f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _xsub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def _xrate(parent, fps):
    r = _xsub(parent, "rate")
    tb = int(round(float(fps)))
    _xsub(r, "timebase", tb)
    _xsub(r, "ntsc", "TRUE" if abs(float(fps) - tb) > 0.01 else "FALSE")
    return r


def _xparam(eff, pid, name, value=None, lo=None, hi=None, point=None):
    p = _xsub(eff, "parameter", authoringApp="PremierePro")
    _xsub(p, "parameterid", pid)
    _xsub(p, "name", name)
    if lo is not None:
        _xsub(p, "valuemin", lo)
        _xsub(p, "valuemax", hi)
    v = _xsub(p, "value", None if point else value)
    if point:
        _xsub(v, "horiz", _xnum(point[0]))
        _xsub(v, "vert", _xnum(point[1]))
    return p


def _xeffect(parent, name, eid, cat, typ, media):
    e = _xsub(_xsub(parent, "filter"), "effect") if typ != "transition" else _xsub(parent, "effect")
    _xsub(e, "name", name)
    _xsub(e, "effectid", eid)
    if cat:
        _xsub(e, "effectcategory", cat)
    _xsub(e, "effecttype", typ)
    _xsub(e, "mediatype", media)
    return e


def _xmeml(proj, W, H, fps, stem, t_lo=0.0, t_hi=None, overlay=None, report=None):
    """프리미어 프로에서 가져오기(Ctrl+I)로 여는 XML (FCP7 xmeml) — 프리미어가 내보내는 XML 과 같은 꼴 (D-347):
    시퀀스 크기(쇼츠 1080×1920 · 롱폼 1920×1080)·초당 장면 · 영상 클립마다 Basic Motion(확대·자리·회전)+Crop 으로 미리보기와 같은 화면 채우기 ·
    불투명도 · 속도·거꾸로(Time Remap) · 디졸브(transitionitem · 붙은 클립은 start/end -1 · 손잡이만큼 in/out 을 넓힘) · 영상·소리 연결(link) ·
    소리는 트랙마다 스테레오 한 쌍(L·R 두 줄 — 프리미어가 내보내는 꼴) + 소리 크기·페이드 · 마커 · 숨긴 트랙은 꺼짐 · 잠근 트랙은 잠김.
    overlay: 글자·도형만 투명 영상(.mov) 파일 경로 → 맨 위 영상 트랙 (D-348) · report: 못 넣은 것을 모음 (_xml_report).
    t_lo~t_hi: 구간 내보내기면 그 구간만 (0 프레임부터)."""
    rep = report if report is not None else {}
    media = {m["id"]: m for m in proj["media"]}
    t_hi = seq_total(proj) if t_hi is None else t_hi
    total_f = int(round((t_hi - t_lo) * fps))
    F = lambda t: int(round(t * fps))  # noqa: E731  (구간 시작 기준 초 → 시퀀스 프레임)
    tracks = proj["tracks"]
    by_id = {it["id"]: it for it in proj["items"]}
    solo = any(t.get("solo") for t in tracks if t["k"] == "a")

    def cut(it):
        """구간 안쪽만 남긴 클립 (밖이면 None) · 시각은 구간 시작 기준."""
        s0, e0 = it["start"], i_end(it)
        a, b = max(s0, t_lo), min(e0, t_hi)
        if b - a < 0.5 / fps:
            return None
        x, sp = dict(it), i_sp(it)
        if it.get("rev"):
            x["out"], x["in"] = it["out"] - (a - s0) * sp, it["in"] + (e0 - b) * sp
        else:
            x["in"], x["out"] = it["in"] + (a - s0) * sp, it["out"] - (e0 - b) * sp
        x["start"] = a - t_lo
        return x

    BIG = 10 ** 9

    def room(it):
        """디졸브 손잡이로 더 쓸 수 있는 원본 (앞, 뒤 · 시퀀스 프레임) — timing() 과 같은 셈 · 사진은 끝없음 · 길이를 모르면 끝없음.
        앞 = in 앞(거꾸로면 out 뒤) · 뒤 = out 뒤(거꾸로면 in 앞) · 속도가 빠르면 그만큼 적게."""
        x, md = cut(it), media.get(it["media"]) or {}
        if not x or md.get("kind") == "image":
            return BIG, BIG
        sp = i_sp(x)
        i = int(round(x["in"] * fps))
        o = i + int(round((F(i_end(x)) - F(x["start"])) * sp))
        mf = int(round(float(md.get("dur") or 0) * fps)) or BIG
        h, t = (mf - o, i) if x.get("rev") else (i, mf - o)
        return max(0, int(h / sp + 1e-9)), max(0, int(t / sp + 1e-9))

    def fit(ts, te, cf, ah, at):
        """원본 여유(ah: 들어오는 클립 앞 · at: 나가는 클립 뒤)에 맞춘 전환 [ts, te] — 모자라면 프리미어·FCP7·OTIO 가 같게 읽는 꼴
        (편집점 가운데(같은 폭) · 편집점에서 시작 · 편집점에서 끝) 중 원래 자리와 가장 많이 겹치는 것 (같으면 긴 것) · 하나도 안 되면 None.
        예전엔 in 을 0 으로 깎아(max) 클립이 원본 다른 프레임에 놓여 영상이 자기 소리보다 앞섰고, 뒤 손잡이는 파일 길이를 넘었음."""
        if cf - ts <= ah and te - cf <= at:
            return ts, te
        d, cands = te - ts, []
        m = min(ah, at, d // 2)
        if m > 0:
            cands.append((cf - m, cf + m))
        if min(d, at) > 0:
            cands.append((cf, cf + min(d, at)))
        if min(d, ah) > 0:
            cands.append((cf - min(d, ah), cf))
        if not cands:
            return None
        return max(cands, key=lambda c: (max(0, min(c[1], te) - max(c[0], ts)), c[1] - c[0]))

    # 전환 → 프레임 (구간 안에 다 들어간 것만) · head[b id] / tail[a id]
    head, tail = {}, {}
    for tr in valid_trans(proj):
        r = trans_range(proj, tr, by_id)
        if not r:
            continue
        if r[0] < t_lo - 1e-6 or r[1] > t_hi + 1e-6:
            rep.setdefault("trans_cut", []).append(r[0])
            continue
        a, b = by_id.get(tr.get("a")), by_id.get(tr.get("b"))
        ts, te = F(r[0] - t_lo), F(r[1] - t_lo)
        if te <= ts:
            continue
        if a and b:
            cf = F(b["start"] - t_lo)
            al = tr.get("align", "center")
            align = al if al in ("start", "end") else "center"
            if align == "center":  # 편집점을 가운데에 (FCP7·OTIO 는 (start+end)/2 를 버림한 자리를 편집점으로 읽음)
                d = te - ts
                ts = cf - d // 2
                te = ts + d
            cf = min(max(cf, ts), te)
            # 원본 앞뒤 여유가 모자라면 (in 이 0 근처인 B롤·영상 끝 근처) 전환을 줄이거나 편집점 한쪽으로 — in/out 은 늘 실제 원본 프레임 (영상·소리 싱크)
            ah, at = room(b)[0], room(a)[1]
            nw = fit(ts, te, cf, ah, at)
            if nw is None:
                rep.setdefault("trans_short", []).append((r[0] - t_lo, (te - ts) / fps, 0.0, ""))
                continue
            if nw != (ts, te):
                ts, te = nw
                align = "start" if ts == cf else "end" if te == cf else "center"
                rep.setdefault("trans_short", []).append((r[0] - t_lo, (r[1] - r[0]), (te - ts) / fps, align))
        elif b:
            cf, align = ts, "start-black"
        else:
            cf, align = te, "end-black"
        x = {"tr": tr, "ts": ts, "te": te, "cut": cf, "align": align, "a": a and a["id"], "b": b and b["id"]}
        if b:
            head[b["id"]] = x
        if a:
            tail[a["id"]] = x
        typ = tr.get("type", "dissolve")
        tk = next((t for t in tracks if t["id"] == tr["track"]), {})
        if tk.get("k") == "v" and typ != "dissolve" and not (typ == "black" and not (a and b)):
            rep.setdefault("trans_changed", []).append((typ, r[0] - t_lo))

    def trans_el(parent, x, kind):
        t = _xsub(parent, "transitionitem")
        _xsub(t, "start", x["ts"])
        _xsub(t, "end", x["te"])
        _xsub(t, "alignment", x["align"])
        _xsub(t, "cutPointTicks", round((x["cut"] - x["ts"]) / fps * PPRO_TICKS))
        _xrate(t, fps)
        if kind == "v":
            e = _xeffect(t, "Cross Dissolve", "Cross Dissolve", "Dissolve", "transition", "video")
            for k, v in (("wipecode", 0), ("wipeaccuracy", 100), ("startratio", 0), ("endratio", 1), ("reverse", "FALSE")):
                _xsub(e, k, v)
        else:
            cg = x["tr"] is None or x["tr"].get("type") == "cgain"  # 페이드·같은 크기로 섞기 = 0dB · 기본(일정한 크기로 들림) = +3dB
            _xeffect(t, "Cross Fade (0dB)" if cg else "Cross Fade (+3dB)", "KGAudioTransCrossFade0dB" if cg else "KGAudioTransCrossFade3dB",
                     "", "transition", "audio")
        return t

    files, masters = {}, {}

    def file_el(parent, md, path=None, w=None, h=None, dur_f=None):
        key = md["id"] if md else str(path)
        if key in files:
            return _xsub(parent, "file", id=files[key])
        fid = files[key] = f"file-{len(files) + 1}"
        p = Path(path) if path else media_path(md["file"], md.get("src", "videos"))
        img = bool(md) and md.get("kind") == "image"
        mf = fps if (not md or img) else float(md.get("fps") or fps)
        f = _xsub(parent, "file", id=fid)
        _xsub(f, "name", p.name)
        _xsub(f, "pathurl", _pathurl(p))
        _xrate(f, mf)
        _xsub(f, "duration", dur_f if dur_f is not None else max(1, int(round(float(md.get("dur") or 0) * mf))))
        tc = _xsub(f, "timecode")
        _xrate(tc, mf)
        _xsub(tc, "string", "00:00:00:00")
        _xsub(tc, "frame", 0)
        _xsub(tc, "displayformat", "NDF")
        me = _xsub(f, "media")
        if not md or md.get("kind") in ("video", "image"):
            sc = _xsub(_xsub(me, "video"), "samplecharacteristics")
            _xrate(sc, mf)
            _xsub(sc, "width", w or md.get("w") or W)
            _xsub(sc, "height", h or md.get("h") or H)
            _xsub(sc, "anamorphic", "FALSE")
            _xsub(sc, "pixelaspectratio", "square")
            _xsub(sc, "fielddominance", "none")
        if md and md.get("audio"):
            au = _xsub(me, "audio")
            sc = _xsub(au, "samplecharacteristics")
            _xsub(sc, "depth", 16)
            _xsub(sc, "samplerate", 48000)
            _xsub(au, "channelcount", min(2, _audio_channels(p)))
        return f

    def timing(x, kind):
        """→ (start, end, in, out) 시퀀스 프레임 · 전환이 붙은 쪽은 -1 이고 그 손잡이만큼 in/out 이 넓어짐 (프리미어·FCP7 규칙)."""
        sp = i_sp(x)
        s, e = F(x["start"]), F(i_end(x))
        i = int(round(x["in"] * fps))
        o = i + int(round((e - s) * sp))
        xs, xe = s, e
        h, t = head.get(x["id"]), tail.get(x["id"])
        if h:
            xs = -1
            d = int(round((h["cut"] - h["ts"]) * sp))
            if x.get("rev"):
                o += d
            else:
                i -= d
        if t:
            xe = -1
            d = int(round((t["te"] - t["cut"]) * sp))
            if x.get("rev"):
                i -= d
            else:
                o += d
        if i < 0:  # 사진(어느 프레임이나 같음)은 in/out 을 함께 밀어 길이를 지킴 · 영상은 위 fit 이 손잡이를 원본 안으로 맞춰 둠
            o, i = o - i, 0
        return xs, xe, i, o

    root = ET.Element("xmeml", version="4")
    sq = _xsub(root, "sequence", id="sequence-1")
    _xsub(sq, "duration", total_f)
    _xrate(sq, fps)
    _xsub(sq, "name", stem)
    sm = _xsub(sq, "media")
    vid = _xsub(sm, "video")
    fmt = _xsub(_xsub(vid, "format"), "samplecharacteristics")
    _xrate(fmt, fps)
    for k, v in (("width", W), ("height", H), ("anamorphic", "FALSE"), ("pixelaspectratio", "square"), ("fielddominance", "none"), ("colordepth", 24)):
        _xsub(fmt, k, v)
    aud = _xsub(sm, "audio")
    _xsub(aud, "numOutputChannels", 2)
    asc = _xsub(_xsub(aud, "format"), "samplecharacteristics")
    _xsub(asc, "depth", 16)
    _xsub(asc, "samplerate", 48000)
    outs = _xsub(aud, "outputs")
    for ch in (1, 2):
        g = _xsub(outs, "group")
        _xsub(g, "index", ch)
        _xsub(g, "numchannels", 1)
        _xsub(g, "downmix", 0)
        _xsub(_xsub(g, "channel"), "index", ch)

    links = {}   # 연결(link id) → [(clipitem, mediatype, trackindex, clipindex)]
    seen_color, seen_anim, seen_bg = [], [], set()
    n = 0
    vi = ai = 0
    for tr in tracks:
        its = sorted([x for x in (cut(it) for it in proj["items"] if it["track"] == tr["id"]) if x and media.get(x["media"])],
                     key=lambda x: x["start"])
        kind = tr["k"]
        if kind == "v":
            vi += 1
            tel = _xsub(vid, "track")
            for x in its:
                md = media[x["media"]]
                h = head.get(x["id"])
                if h:  # 들어오는 전환은 이 클립 앞에 (구간에 다 들어간 전환만 있음 → 나가는 클립도 이 트랙에 있음)
                    trans_el(tel, h, "v")
                n += 1
                ci_n = 1 + sum(1 for c_ in tel if c_.tag in ("clipitem", "transitionitem"))  # 프리미어처럼 전환도 세는 트랙 안 차례 (link clipindex)
                s, e, i, o = timing(x, "v")
                ci = _xsub(tel, "clipitem", id=f"clipitem-{n}")
                _xsub(ci, "masterclipid", masters.setdefault(md["id"], f"masterclip-{len(masters) + 1}"))
                _xsub(ci, "name", Path(md["file"]).name)
                _xsub(ci, "enabled", "TRUE")
                dur = max(o, int(round(float(md.get("dur") or 0) * fps)), 1)
                _xsub(ci, "duration", dur)
                _xrate(ci, fps)
                for k, v in (("start", s), ("end", e), ("in", i), ("out", o)):
                    _xsub(ci, k, v)
                _xsub(ci, "alphatype", "none")
                _xsub(ci, "pixelaspectratio", "square")
                _xsub(ci, "anamorphic", "FALSE")
                file_el(ci, md, dur_f=None if md.get("kind") != "image" else dur)
                sp = i_sp(x)
                if abs(sp - 1) > 1e-3 or x.get("rev"):
                    e_ = _xeffect(ci, "Time Remap", "timeremap", "motion", "motion", "video")
                    _xparam(e_, "variablespeed", "variablespeed", 0, 0, 1)
                    _xparam(e_, "speed", "speed", f"{sp * 100:.2f}", -100000, 100000)
                    _xparam(e_, "reverse", "reverse", "TRUE" if x.get("rev") else "FALSE")
                    _xparam(e_, "frameblending", "frameblending", "FALSE")
                mo = premiere_motion(x, md, proj, W, H)
                e_ = _xeffect(ci, "Basic Motion", "basic", "motion", "motion", "video")
                _xparam(e_, "scale", "Scale", _xnum(mo["scale"], 4), 0, 1000)
                _xparam(e_, "rotation", "Rotation", _xnum(mo["rot"], 4), -8640, 8640)
                _xparam(e_, "center", "Center", point=mo["center"])
                _xparam(e_, "centerOffset", "Anchor Point", point=(0, 0))
                if any(mo["crop"]):
                    e_ = _xeffect(ci, "Crop", "crop", "motion", "motion", "video")
                    for pid, v in zip(("left", "right", "top", "bottom"), mo["crop"]):
                        _xparam(e_, pid, pid, _xnum(v, 4), 0, 100)
                if mo["opacity"] < 99.95:
                    e_ = _xeffect(ci, "Opacity", "opacity", "motion", "motion", "video")
                    _xparam(e_, "opacity", "opacity", _xnum(mo["opacity"], 2), 0, 100)
                if mo["animated"]:
                    seen_anim.append(x["start"])
                if mo["bg"] == "blur" or (mo["bg"] == "box" and str(mo.get("bar") or "#000000").lower() not in ("#000000", "#000")):
                    seen_bg.add((mo["bg"], mo.get("bar")))
                if mo.get("pics"):   # 은평 스타일 띠 그림·로고 (BR-280 · BR-281)
                    seen_bg.add(("pics", ""))
                if not _color_default(x.get("color")):
                    seen_color.append(x["start"])
                if x.get("link"):
                    links.setdefault(x["link"], []).append((ci, "video", vi, ci_n))
                t_ = tail.get(x["id"])
                if t_ and t_["b"] is None:
                    trans_el(tel, t_, "v")
            _xsub(tel, "enabled", "FALSE" if tr.get("hide") else "TRUE")
            _xsub(tel, "locked", "TRUE" if tr.get("lock") else "FALSE")
        else:
            off = (tr.get("mute") or (solo and not tr.get("solo")))
            tvol = 10 ** (float(tr.get("vol") or 0) / 20)
            for side in (0, 1):
                ai += 1
                tel = _xsub(aud, "track", currentExplodedTrackIndex=side, totalExplodedTrackCount=2, premiereTrackType="Stereo")
                for x in its:
                    md = media[x["media"]]
                    if not md.get("audio"):
                        continue
                    h, t_ = head.get(x["id"]), tail.get(x["id"])
                    fi = F(min(float(x.get("fadeIn") or 0), i_len(x)))
                    fo = F(min(float(x.get("fadeOut") or 0), i_len(x)))
                    s0, e0 = F(x["start"]), F(i_end(x))
                    if not h and fi > 0:  # 페이드 인 = 소리 없음에서 0dB 섞기 (start-black)
                        h = head[x["id"]] = {"tr": None, "ts": s0, "te": s0 + fi, "cut": s0, "align": "start-black", "a": None, "b": x["id"]}
                    if not t_ and fo > 0:
                        t_ = tail[x["id"]] = {"tr": None, "ts": e0 - fo, "te": e0, "cut": e0, "align": "end-black", "a": x["id"], "b": None}
                    if h:
                        trans_el(tel, h, "a")
                    n += 1
                    ci_n = 1 + sum(1 for c_ in tel if c_.tag in ("clipitem", "transitionitem"))
                    s, e, i, o = timing(x, "a")
                    ci = _xsub(tel, "clipitem", id=f"clipitem-{n}", premiereChannelType="stereo")
                    _xsub(ci, "masterclipid", masters.setdefault(md["id"], f"masterclip-{len(masters) + 1}"))
                    _xsub(ci, "name", Path(md["file"]).name)
                    _xsub(ci, "enabled", "TRUE")
                    _xsub(ci, "duration", max(o, int(round(float(md.get("dur") or 0) * fps)), 1))
                    _xrate(ci, fps)
                    for k, v in (("start", s), ("end", e), ("in", i), ("out", o)):
                        _xsub(ci, k, v)
                    file_el(ci, md)
                    st_ = _xsub(ci, "sourcetrack")
                    _xsub(st_, "mediatype", "audio")
                    p_ = media_path(md["file"], md.get("src", "videos"))
                    _xsub(st_, "trackindex", side + 1 if _audio_channels(p_) >= 2 else 1)
                    sp = i_sp(x)
                    if abs(sp - 1) > 1e-3 or x.get("rev"):
                        e_ = _xeffect(ci, "Time Remap", "timeremap", "motion", "motion", "audio")
                        _xparam(e_, "variablespeed", "variablespeed", 0, 0, 1)
                        _xparam(e_, "speed", "speed", f"{sp * 100:.2f}", -100000, 100000)
                        _xparam(e_, "reverse", "reverse", "TRUE" if x.get("rev") else "FALSE")
                    lv = 0.0 if x.get("mute") else 10 ** (float(kf_at(param(x, "level"), i_mt(x, x["start"] + i_len(x) / 2))) / 20) * 10 ** (float(x.get("gain") or 0) / 20) * tvol
                    if param(x, "level")["k"]:
                        seen_anim.append(x["start"])
                    e_ = _xeffect(ci, "Audio Levels", "audiolevels", "audiolevels", "audiolevels", "audio")
                    _xparam(e_, "level", "Level", _xnum(min(3.98109, lv), 5), 0, 3.98109)
                    if x.get("link"):
                        links.setdefault(x["link"], []).append((ci, "audio", ai, ci_n))
                    if t_ and t_["b"] is None:
                        trans_el(tel, t_, "a")
                _xsub(tel, "enabled", "FALSE" if off else "TRUE")
                _xsub(tel, "locked", "TRUE" if tr.get("lock") else "FALSE")
                _xsub(tel, "outputchannelindex", side + 1)
            for x in its:  # 페이드로 만든 전환은 이 트랙에서만
                for d in (head, tail):
                    if d.get(x["id"]) and d[x["id"]]["tr"] is None:
                        d.pop(x["id"])
    if overlay:  # 글자·도형만 투명 영상 → 맨 위 영상 트랙 (D-348)
        tel = _xsub(vid, "track")
        n += 1
        ci = _xsub(tel, "clipitem", id=f"clipitem-{n}")
        p = Path(overlay)
        _xsub(ci, "masterclipid", f"masterclip-{len(masters) + 1}")
        _xsub(ci, "name", p.name)
        _xsub(ci, "enabled", "TRUE")
        _xsub(ci, "duration", total_f)
        _xrate(ci, fps)
        for k, v in (("start", 0), ("end", total_f), ("in", 0), ("out", total_f)):
            _xsub(ci, k, v)
        _xsub(ci, "alphatype", "straight")
        _xsub(ci, "pixelaspectratio", "square")
        _xsub(ci, "anamorphic", "FALSE")
        file_el(ci, None, path=p, w=W, h=H, dur_f=total_f)
        e_ = _xeffect(ci, "Basic Motion", "basic", "motion", "motion", "video")
        _xparam(e_, "scale", "Scale", 100, 0, 1000)
        _xparam(e_, "rotation", "Rotation", 0, -8640, 8640)
        _xparam(e_, "center", "Center", point=(0, 0))
        _xparam(e_, "centerOffset", "Anchor Point", point=(0, 0))
        _xsub(tel, "enabled", "TRUE")
        _xsub(tel, "locked", "FALSE")
    for grp in links.values():  # 영상·소리 연결 (프리미어가 내보내는 꼴: 묶음의 모든 클립을 자기 자신까지 적음)
        if len(grp) < 2:
            continue
        for ci, *_ in grp:
            for ref, mt, ti, cn in grp:
                ln = _xsub(ci, "link")
                _xsub(ln, "linkclipref", ref.get("id"))
                _xsub(ln, "mediatype", mt)
                _xsub(ln, "trackindex", ti)
                _xsub(ln, "clipindex", cn)
                if mt == "audio":
                    _xsub(ln, "groupindex", 1)
    tc = _xsub(sq, "timecode")
    _xrate(tc, fps)
    _xsub(tc, "string", "00:00:00:00")
    _xsub(tc, "frame", 0)
    _xsub(tc, "displayformat", "NDF")
    for mk in sorted(proj.get("markers") or [], key=lambda m: float(m.get("t") or 0)):
        t = float(mk.get("t") or 0)
        if t_lo - 1e-6 <= t <= t_hi + 1e-6:
            me = _xsub(sq, "marker")
            _xsub(me, "comment", "")
            _xsub(me, "name", str(mk.get("name") or ""))
            _xsub(me, "in", F(t - t_lo))
            _xsub(me, "out", -1)
    if skin_on(proj) and skin_path(proj["layout"]["skin"]):   # 풋살사관학교 스킨 (BR-320 · 프리미어 XML 에는 안 들어감 → '빠진 것' 목록)
        seen_bg.add(("skin", ""))
    rep.update(color=seen_color, anim=seen_anim, bg=sorted(seen_bg, key=str), clips=n)
    if hasattr(ET, "indent"):
        ET.indent(root, "  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _color_default(c):
    """색 보정을 안 했는지 (editor.html colorIsDefault)."""
    if not c:
        return True
    return all(abs(float(c.get(k, 0) or 0)) < 1e-6 for k in ("exp", "con", "hi", "sh", "vib", "temp", "tint")) and \
        abs(float(c["sat"] if c.get("sat") is not None else 100) - 100) < 1e-6


def _tc(t, fps=30):
    """초 → '00:00:12:05' (시:분:초:프레임)."""
    f = int(round(max(0.0, float(t)) * fps))
    s, ff = divmod(f, int(round(fps)))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


def _xml_report(proj, rep, stem, fps, t_lo, t_hi, overlay=None, overlay_note=None, srt=()):
    """프리미어 XML 에 못 넣은 것 목록 (프리미어의 '번역 보고서'처럼 · D-347) — 무엇이 빠지고 어떻게 하면 되는지 쉬운 말로."""
    span = t_hi - t_lo
    caps = [c for c in timeline_captions(proj) if c["end"] > t_lo and c["start"] < t_hi]
    titles = [t for t in proj.get("titles") or [] if t["start"] < t_hi and t["start"] + t["dur"] > t_lo]
    shapes = [s for s in proj.get("shapes") or [] if s.get("full") or (s["start"] < t_hi and s["start"] + s["dur"] > t_lo)]
    tacts = [x for x in proj.get("tactics") or [] if isinstance(x, dict)]
    rows = []
    gfx = [f"{k} {v}개" for k, v in (("자막", len(caps)), ("타이틀", len(titles)), ("도형", len(shapes)), ("전술 그림", len(tacts))) if v]
    if gfx:
        if overlay:
            rows.append(f"■ 자막·타이틀·도형·전술 그림 ({' · '.join(gfx)})\n"
                        f"  맨 위 영상 트랙의 '{Path(overlay).name}'(투명 배경 영상)으로 들어가요. 화면은 같지만 프리미어에서 글은 고칠 수 없어요(그림이에요).")
        else:
            rows.append(f"■ 자막·타이틀·도형·전술 그림이 빠져요 ({' · '.join(gfx)})\n"
                        + (f"  {overlay_note}\n" if overlay_note else "")
                        + "  편집실 내보내기에서 '글자·도형만 투명 영상'을 같이 켜면 맨 위 트랙에 똑같이 얹혀요.")
        if caps and srt:
            rows.append("■ 자막을 프리미어 자막 트랙으로 고치려면\n  " + " · ".join(f"'{s}'" for s in srt)
                        + " 을(를) 가져오기(Ctrl+I) 한 뒤 시퀀스 처음(00:00:00:00)에 끌어 놓으세요.")
    if rep.get("bg"):
        for bg, bar in rep["bg"]:
            rows.append("■ 흐린 배경이 빠져요\n  쇼츠 위아래의 흐린 배경 대신 검은 바탕이 보여요." if bg == "blur"
                        else "■ 위아래 띠 그림·로고가 빠져요\n  은평 스타일 띠 그림과 아래 띠 로고 대신 검은 바탕이 보여요." if bg == "pics"
                        else "■ 풋살사관학교 스킨이 빠져요\n  위아래 칠판·캐릭터 그림 대신 검은 바탕이 보여요. "
                        + (f"같은 폴더의 '{rep['skinFile']}'(스킨 그림)을 가져오기(Ctrl+I) 한 뒤 영상 트랙 위(글자·도형 투명 영상 아래)에 시퀀스 처음부터 끝까지 얹으면 같아져요."
                           if rep.get("skinFile") else "스킨 그림(PNG)을 영상 트랙 위(글자·도형 투명 영상 아래)에 직접 얹으면 같아져요.") if bg == "skin"
                        else f"■ 위아래 띠 색({bar})이 빠져요\n  검은 바탕이 보여요.")
    if rep.get("color"):
        rows.append(f"■ 색 보정 {len(rep['color'])}개 클립이 빠져요\n  " + ", ".join(_tc(t, fps) for t in rep["color"][:12])
                    + (" …" if len(rep["color"]) > 12 else ""))
    if rep.get("anim"):
        rows.append(f"■ 움직임(키프레임) {len(rep['anim'])}개 클립은 클립 가운데 값 하나로 들어가요\n  "
                    + ", ".join(_tc(t, fps) for t in rep["anim"][:12]) + (" …" if len(rep["anim"]) > 12 else ""))
    if rep.get("trans_changed"):
        nm = {"black": "검정 거치기", "white": "흰색 거치기", "wipe": "닦아내기", "push": "밀어내기"}
        rows.append(f"■ 장면 전환 {len(rep['trans_changed'])}개는 디졸브(Cross Dissolve)로 바뀌어요\n  "
                    + ", ".join(f"{nm.get(k, k)} {_tc(t, fps)}" for k, t in rep["trans_changed"][:12]))
    if rep.get("trans_cut"):
        rows.append(f"■ 구간 끝에 걸린 장면 전환 {len(rep['trans_cut'])}개는 빠져요 (그 자리는 바로 바뀜)")
    if rep.get("trans_short"):
        al = {"center": "편집점 가운데", "start": "편집점에서 시작", "end": "편집점에서 끝"}
        rows.append(f"■ 원본 앞뒤 여유가 모자란 장면 전환 {len(rep['trans_short'])}개는 줄이거나 편집점 한쪽으로 옮겼어요\n"
                    "  편집실은 모자란 곳을 첫·마지막 장면을 멈춰 보여 줘요. 프리미어 XML 에는 멈춘 장면을 넣을 수 없어서, 있는 원본만큼만 섞어요 (영상·소리 싱크는 그대로).\n  "
                    + ", ".join(f"{_tc(t, fps)} {d0:.1f}초 → " + (f"{d1:.1f}초({al.get(a, a)})" if d1 > 0 else "빠짐(바로 바뀜)") for t, d0, d1, a in rep["trans_short"][:12])
                    + (" …" if len(rep["trans_short"]) > 12 else ""))
    sound = []
    music = {t["id"] for t in proj["tracks"] if t["k"] == "a" and t.get("role") == "music"}
    if (proj.get("duck") or {}).get("on", True) and any(it["track"] in music and it["start"] < t_hi and i_end(it) > t_lo for it in proj["items"]):
        sound.append("배경음악 자동 줄이기(말할 때 음악 작게)")
    if voice_filters(proj.get("voice")):
        sound.append("목소리 보정")
    if (proj.get("master") or {}).get("normalize", True):
        sound.append(f"소리 크기 맞추기({float((proj.get('master') or {}).get('lufs') or -14):.0f} LUFS)")
    if sound:
        rows.append("■ 소리 처리가 빠져요: " + " · ".join(sound) + "\n  프리미어에서는 원래 소리 그대로예요. 완성 MP4 와 소리가 다를 수 있어요.")
    miss = rep.get("missing_media") or []
    if miss:
        rows.append("■ 파일이 없어서 프리미어에서 '미디어 오프라인'으로 보여요\n  " + ", ".join(miss[:10]))
    head = (f"프리미어로 넘기기 · {stem}\n"
            f"시퀀스 {rep.get('W')}×{rep.get('H')} · {int(fps)}fps · 길이 {_tc(span, fps)} · 클립 {rep.get('clips', 0)}개\n\n"
            "프리미어에서: 파일 > 가져오기(Ctrl+I) → '" + f"{stem}_premiere.xml" + "' 를 고르면 같은 이름의 시퀀스가 생겨요.\n"
            "그대로 넘어가는 것: 컷 자리 · 원본 구간 · 트랙 · 화면 채우기(확대·자리·자르기) · 속도·거꾸로 · 디졸브 · 소리 크기·페이드 · 영상·소리 연결 · 마커\n\n")
    return head + ("못 넣은 것\n\n" + "\n\n".join(rows) if rows else "빠진 것이 없어요.") + "\n"


def _pathurl(p):
    """프리미어 XML 의 파일 주소: 드라이브 경로는 file://localhost/C%3A/… (프리미어가 쓰는 꼴), 네트워크 폴더(NAS)는
    file://서버/공유/… (예전에는 서버 이름이 빠져 모든 클립이 '미디어 오프라인')."""
    s = str(p)
    if s.startswith("\\\\") and not s.startswith("\\\\?\\"):
        host, _, rest = s[2:].replace("\\", "/").partition("/")
        return f"file://{quote(host)}/" + quote(rest)
    return "file://localhost/" + quote(s.replace("\\", "/").lstrip("/"))


def _xml_esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- 내보내기 (구간별 합성 렌더) ----------

CANCEL = threading.Event()
_PROCS = set()


def cancel_export():
    """멈추기(✕): 내보내기·미리보기 파일·검수 모두 (실행 중인 ffmpeg 를 끔)."""
    CANCEL.set()
    for p in list(_PROCS):
        try:
            p.kill()
        except OSError:
            pass
    hwdec.kill_all()  # 그래픽카드 풀기 확인 중인 ffmpeg 도


class Cancelled(Exception):
    pass


class HwEncError(RuntimeError):
    """그래픽카드 인코더 문제 (이때만 일반 방식으로 다시 만듦)."""


# 그래픽카드 인코더 자체의 오류만 (Stream mapping 줄의 인코더 이름, 필터 오류 뒤에 붙는 'Could not open encoder before EOF',
# 디스크 가득 참 같은 다른 오류는 아님 → 그런 오류로 그래픽카드를 끄지 않게)
HW_ERR = re.compile(r"OpenEncodeSession|No capable devices|No NVENC|incompatible client key|\bMFX\b|Device creation failed|"
                    r"InitializeEncoder|Error while opening encoder|AVHWDeviceContext", re.I)
HW_SESSION = re.compile(r"OpenEncodeSession|incompatible client key|out of memory|session", re.I)


def _hw_fail(msg, enc):
    """ffmpeg 오류 글이 그래픽카드 인코더(enc) 문제인지: 인코더 자신이 남긴 줄([h264_nvenc @ …]) 이나 알려진 그래픽카드 오류."""
    return bool(re.search(r"^\[" + re.escape(enc) + r" @ ", msg, re.M) or HW_ERR.search(msg))


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def _fit(it, md, seq, W, H, rfx=None):
    """미디어를 화면에 놓는 기본 크기·위치 (효과 컨트롤 100% 기준). editor.html 의 fitRect 와 같은 계산.
    rfx: 쇼츠 V1 화면 좌우를 구간 안 시간 t 의 식으로 (얼굴 따라가기 키프레임 rfk · BR-326 · 없으면 reframe 값)."""
    sw, sh = md.get("w") or W, md.get("h") or H
    ref = W / (1080 if seq.get("format") == "shorts" else 1920)  # 기준 해상도 대비 배율 (4K 등)
    lay = {"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0, **(seq.get("layout") or {})}
    if lay["mode"] not in ("fill", "box", "blur"):
        lay["mode"] = "fill"
    fit = it.get("fit") or "auto"
    rf = float(it.get("reframe", 0.5))
    if seq.get("format") == "shorts" and it["track"] == "V1" and fit == "auto":
        ct, cb = float(lay.get("cropTop") or 0), float(lay.get("cropBottom") or 0)
        keep = max(0.2, 1 - ct - cb)
        vh = sh * keep
        pre = f"crop=iw:ih*{keep:.4f}:0:ih*{ct:.4f}," if ct or cb else ""
        if lay["mode"] == "fill":
            s = max(W / sw, H / vh)
            scw, sch = max(W, _even(sw * s)), max(H, _even(vh * s))
            crop = (f"crop=w={W}:h={H}:x='{scw - W}*({rfx})':y={(sch - H) / 2:.1f}" if rfx is not None and scw > W   # (이름 붙인 값 뒤에는 순서 값을 못 씀)
                    else f"crop={W}:{H}:{(scw - W) * rf:.1f}:{(sch - H) / 2:.1f}")
            return {"chain": pre + f"scale={scw}:{sch},{crop}", "bw": W, "bh": H, "bx": 0, "by": 0, "bg": None}
        zw = _even(W * max(1.0, float(lay["zoom"])))
        zh = _even(vh * zw / sw)
        ch = min(zh, H)
        crop = (f"crop=w={W}:h={ch}:x='{zw - W}*({rfx})':y={(zh - ch) / 2:.1f}" if rfx is not None and zw > W   # (이름 붙인 값 뒤에는 순서 값을 못 씀)
                else f"crop={W}:{ch}:{(zw - W) * rf:.1f}:{(zh - ch) / 2:.1f}")
        out = {"chain": pre + f"scale={zw}:{zh},{crop}", "bw": W, "bh": ch, "bx": 0,
               "by": (H - ch) * float(lay["vpos"]), "bg": lay["mode"], "bar": lay["bar"], "win": lay["mode"] == "box" and bool(lay.get("win"))}
        if lay["mode"] == "box" and (lay.get("band") or lay.get("logo")):   # 은평 스타일 띠 그림·로고 (BR-280 · BR-281 · 없으면 예전과 같은 값)
            out.update(band=lay.get("band"), logo=lay.get("logo"))
        return out
    if fit == "auto":  # 위 트랙: 영상은 화면에 맞춤, 이미지는 작으면 원래 크기 (정지 화면은 원래 영상처럼 화면에 맞춤)
        fit = "native" if md["kind"] == "image" and not md.get("freeze") and sw * ref <= W and sh * ref <= H else "contain"
    cover = fit == "cover"
    s = ref if fit == "native" else (max if cover else min)(W / sw, H / sh)
    scw, sch = _even(sw * s), _even(sh * s)
    if cover:
        scw, sch = max(scw, W), max(sch, H)
        crop = (f"crop=w={W}:h={H}:x='{scw - W}*({rfx})':y={(sch - H) / 2:.1f}" if rfx is not None and scw > W
                else f"crop={W}:{H}:{(scw - W) * rf:.1f}:{(sch - H) / 2:.1f}")
        return {"chain": f"scale={scw}:{sch},{crop}", "bw": W, "bh": H, "bx": 0, "by": 0, "bg": None}
    return {"chain": f"scale={scw}:{sch}", "bw": scw, "bh": sch, "bx": (W - scw) / 2, "by": (H - sch) / 2, "bg": None}


def _hdr_pre_w(it, md, seq, W, H):
    """HDR 영상을 일반 색으로 바꾸기 전에 줄일 가로 크기: 화면에 놓일 크기(맞춤 확대 포함)까지만, 최대 2W (예전 값).
    고정 확대(E6 점프 컷 펀치인 108% 등 · 키프레임 없음)는 놓일 크기 × 배율까지 — 가편집 클립의 절반쯤이 이것이라 2W 로 두면
    줄이는 이득을 거의 못 봄. 확대·이동 키프레임이 있으면 예전처럼 2W (확대해도 흐려지지 않게)."""
    ps = param(it, "scale")
    if ps.get("k") or param(it, "pos").get("k"):
        return 2 * W
    m = re.search(r"scale=(\d+):", _fit(it, md, seq, W, H)["chain"])
    need = int(m.group(1)) if m else 2 * W
    z = max(1.0, float(ps.get("v", 100)) / 100)
    return min(2 * W, max(W, math.ceil(need * z - 1e-6)))


def _num(v):
    return f"{v:.5f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _kf_expr(it, key, t0, dur, comp=None, scale=1.0, offset=0.0, p=None):
    """키프레임 값 → ffmpeg 식 (구간 안 시간 t 기준). comp: 위치처럼 [x,y] 값이면 몇 번째인지 · p: 효과 값 대신 쓸 {v, k} (화면 좌우 따라가기 rfk)."""
    p = p or param(it, key)

    def val(v):
        x = v[comp] if comp is not None else v
        return float(x) * scale + offset

    if not p["k"]:
        return _num(val(p["v"])), False
    ks = p["k"]
    segs = []
    for a, b in zip(ks, ks[1:]):
        ta, tb = i_tl(it, a["t"]) - t0, i_tl(it, b["t"]) - t0
        va, vb = val(a["v"]), val(b["v"])
        if tb < ta:
            ta, tb, va, vb = tb, ta, vb, va
        segs.append((ta, tb, va, vb, a.get("e") == "ease"))
    segs.sort()
    vis = [s for s in segs if s[1] >= -1e-3 and s[0] <= dur + 1e-3]
    if not vis or all(abs(s[2] - s[3]) < 1e-9 for s in vis):
        return _num(val(kf_at(p, i_mt(it, t0 + dur / 2)))), False
    first_t = min(i_tl(it, k["t"]) for k in ks) - t0
    v0 = val(kf_at(p, i_mt(it, t0 + first_t - 1e-3)))
    v_end = val(kf_at(p, i_mt(it, t0 + max(i_tl(it, k["t"]) for k in ks) - t0 + 1e-3)))
    e = _num(v_end)
    for ta, tb, va, vb, ease in reversed(vis):
        x = f"((t-({ta:.4f}))/{max(1e-4, tb - ta):.4f})"
        if ease:
            x = f"({x}*{x}*(3-2*{x}))"
        e = f"if(lt(t,{tb:.4f}),{_num(va)}+({_num(vb - va)})*{x},{e})"
    lead = vis[0][0]
    e = f"if(lt(t,{lead:.4f}),{_num(val(kf_at(p, i_mt(it, t0 + lead - 1e-3))) if lead > first_t else v0)},{e})"
    return e, True


def _color_filters(it):
    """색보정 → ffmpeg (미리보기 SVG 필터와 같은 식)."""
    c = it.get("color") or {}
    exp, con, hi, sh = (float(c.get(k, 0) or 0) for k in ("exp", "con", "hi", "sh"))
    sat = float(c["sat"]) if c.get("sat") is not None else 100.0
    vib, temp, tint = (float(c.get(k) or 0) for k in ("vib", "temp", "tint"))
    out = []
    if any(abs(x) > 1e-6 for x in (exp, con, hi, sh, temp, tint)):
        g = 2 ** (exp / 2.2)
        wb = {"r": 1 + temp * 0.0025 + tint * 0.0012, "g": 1 - tint * 0.0025, "b": 1 - temp * 0.0025 + tint * 0.0012}
        k = 1 + con / 100
        chans = []
        for ch in "rgb":
            v1 = f"(val/255*{g * wb[ch]:.5f})"
            v2 = f"(({v1}-0.5)*{k:.4f}+0.5)"
            v3 = f"({v2}+{0.15 * sh / 100:.5f}*6.75*{v2}*(1-{v2})*(1-{v2})+{0.15 * hi / 100:.5f}*6.75*{v2}*{v2}*(1-{v2}))"
            chans.append(f"{ch}='clip({v3}*255,0,255)'")
        out.append("format=rgb24,lutrgb=" + ":".join(chans))
    s = sat / 100 * (1 + vib / 100 * 0.35)  # 활기는 미리보기(colorFilter)와 같은 식으로 채도에 합침
    if abs(s - 1) > 1e-6:
        m = [0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s, 0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s,
             0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]
        out.append("format=rgb24,colorchannelmixer=" + ":".join(f"{n}={v:.4f}" for n, v in zip(
            ("rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb"), m)))
    return out


# 느리게 한 클립 (D-400): 원본 프레임이 모자라면 fps 가 같은 장면을 두 번씩 보여 줘 뚝뚝 끊김 (30fps 촬영본 0.5배 = 초당 15장)
MOTION_MAX = 6.0  # '아주 부드럽게'(움직임 보간)는 타임라인 길이 이 초 이하 클립만 — 1080p 3초에 1분 넘게 걸림 (더 길면 장면 섞기로)
MOTION_TAIL = 3   # 움직임 보간 앞에 마지막 원본 장면을 이만큼 더 붙임 (끝 장면을 먹어도 n 프레임을 다 채우게 · D-405)


def slow_mode(it, md, fps):
    """느리게 한 영상 클립을 내보내는 방법 — None: 원래 프레임 그대로 (1배·빠르게 · 60fps 촬영본 0.5배처럼 프레임이 넉넉함) ·
    "blend": 앞뒤 장면을 섞어 사이 장면을 만듦 (기본) · "motion": 움직임을 따라 사이 장면을 새로 그림 (클립 smooth 가 "motion" 이고
    MOTION_MAX 초 이하일 때만 · 오래 걸림). editor.html smoothRow 와 같은 규칙."""
    sp = i_sp(it)
    if sp >= 1 - 1e-6 or md.get("kind") == "image" or float(md.get("fps") or 30.0) * sp >= fps - 0.5:
        return None
    return "motion" if it.get("smooth") == "motion" and i_len(it) <= MOTION_MAX + 1e-6 else "blend"


def _rate_filter(mode, fps):
    """slow_mode → 프레임 수를 fps 로 맞추는 필터 (framerate·minterpolate 는 장면이 바뀌는 곳에서는 섞지 않음).
    framerate·minterpolate 는 원본 시간 단위(예 1/15360)를 그대로 두어 전환(xfade)이 '시간 단위가 달라요'로 멈춤 → fps 처럼 1/fps 로 맞춤 (D-417)."""
    if mode == "blend":
        return f"framerate=fps={fps},settb=1/{fps}"
    if mode == "motion":
        return f"minterpolate=fps={fps}:mi_mode=mci,settb=1/{fps}"
    return f"fps={fps}"


def _segments(seq, t_lo, t_hi, fps, trans, media=None):
    """장면이 바뀌는 지점마다 나눈 구간 목록 [(f0, f1)].
    같은 트랙 전환 안쪽의 편집점은 나누지 않음 → 전환 하나가 한 구간 (빠른 xfade)."""
    vt = {t["id"] for t in seq["tracks"] if t["k"] == "v" and not t.get("hide")}
    by = {it["id"]: it for it in seq["items"]}
    pts = {t_lo, t_hi}
    rng = {}
    for tr in trans:
        r = trans_range(seq, tr, by)
        if r and tr["track"] in vt:
            pts.update(r)
            rng.setdefault(tr["track"], []).append(r)
            if tr.get("type") in ("black", "white"):  # 가운데(색이 가장 진한 때)에서도 나눔 → 절반씩 빠른 xfade
                pts.add((r[0] + r[1]) / 2)

    def inside(track, p):
        return any(r0 + 1e-4 < p < r1 - 1e-4 for r0, r1 in rng.get(track, ()))

    rev_items = []
    for it in seq["items"]:
        if it["track"] in vt:
            for p in (it["start"], i_end(it)):
                if not inside(it["track"], p):
                    pts.add(p)
            if it.get("rev"):
                rev_items.append(it)
    for it in rev_items:  # 거꾸로 재생은 원본 장면을 모아 두고 뒤집어서 메모리를 많이 씀 → 원본 약 2초(1080p 기준)씩
        md = (media or {}).get(it["media"]) or {}
        px = max(1, (md.get("w") or 1920) * (md.get("h") or 1080))
        budget = 2.0 * min(1.0, (1920 * 1080) / px)  # 4K 는 0.5초
        step = max(1.0 / fps, budget / max(1.0, i_sp(it)))
        t = it["start"]
        while t < i_end(it):
            pts.add(t)
            t += step
    fr = sorted({int(round(p * fps)) for p in pts if t_lo - 1e-6 <= p <= t_hi + 1e-6})
    out = []
    for a, b in zip(fr, fr[1:]):
        while b - a > 20 * fps:  # 긴 구간은 나눠서 진행률·병렬 처리
            out.append((a, a + 20 * fps))
            a += 20 * fps
        if b > a:
            out.append((a, b))
    return out


def _build_segment(seq, media, W, H, fps, f0, f1, trans, tmp, k_seg):
    """구간 하나를 만드는 ffmpeg 인자 (입력들 + 필터 그래프 파일)."""
    t0, n = f0 / fps, f1 - f0
    dur = n / fps
    tmid = t0 + dur / 2
    inputs, fc = [], []
    cnt = {"l": 0}

    def lab(p="l"):
        cnt["l"] += 1
        return f"{p}{cnt['l']}"

    def add_input(it, t_a, t_b, pre=None, bg_pre=None):
        """아이템의 [t_a, t_b] (타임라인) 를 정확히 n 프레임으로 → 라벨. pre: (사진) 프레임을 늘리기 전에 한 번만 할 필터.
        bg_pre: (사진·흐린 배경) 배경 필터도 늘리기 전에 한 번만 → (라벨, 배경 라벨)."""
        md = media[it["media"]]
        p = media_path(md["file"], md["src"])
        if not p.exists():
            raise RuntimeError(f"미디어 파일이 없어요 · {md['file']}")
        idx = len(inputs)
        sp = i_sp(it)
        chain = []
        if md["kind"] == "image":
            # 사진은 1초에 한 번만 읽고(같은 장면) 크기 맞추기도 한 번만 → fps 로 늘림 (예전엔 매 프레임 다시 읽고 줄임)
            # (-pattern_type none: 이름의 '%d'·'%['·'*' 를 번호·찾기 무늬로 읽지 않게 · 영상 이름이 든 정지 화면 PNG)
            pat = ["-f", "image2", "-pattern_type", "none"] if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff") else []
            inputs.append(["-loop", "1", "-framerate", "1", "-t", f"{dur + 1.5:.3f}", *pat, "-i", str(p)])
            chain += ["setpts=PTS-STARTPTS"] + _color_filters(it)
        else:
            ma, mb = i_mt(it, t_a), i_mt(it, t_b)
            lo, hi = min(ma, mb), max(ma, mb)
            mdur = md.get("dur") or 1e9
            sfps = float(md.get("fps") or 30.0)
            # 반 프레임 일찍 찾아가야 가장 가까운 원본 프레임이 첫 장면이 됨 (소리와 싱크)
            ss = min(max(0.0, lo - 0.5 / sfps), max(0.0, mdur - 0.05))
            pre_pad = max(0.0, -lo) / sp if not it.get("rev") else max(0.0, hi - mdur) / sp
            inputs.append(hwdec.input_opts(media_hdr(md), p, ss) + ["-ss", f"{ss:.6f}", "-t", f"{max(0.05, hi - ss) + 0.3:.3f}", "-i", str(p)])
            chain.append("setpts=PTS-STARTPTS")
            hdr = media_hdr(md)
            # HDR → 일반 색: 쓸 크기로 먼저 줄이고, fps 로 버릴 프레임은 바꾸지 않게 fps 뒤에서 (60fps 4K 가 3배쯤 빠름)
            # 거꾸로 재생은 모든 프레임을 메모리에 쌓으니 예전처럼 앞에서 바꿔 8비트로 쌓음
            tm = tonemap_chain(hdr, _hdr_pre_w(it, md, seq, W, H)) if hdr else []
            if it.get("rev"):
                chain += tm + [f"trim=duration={max(0.04, hi - ss):.4f}", "reverse", "setpts=PTS-STARTPTS"]
                tm = []
            mode = slow_mode(it, md, fps)
            if mode == "motion":  # 움직임 보간은 끝 몇 장을 내보내지 않고, 원본이 2장 이하인 구간은 0장 → 뒤 장면이 소리보다 일찍 (D-405)
                chain.append(f"tpad=stop_mode=clone:stop={MOTION_TAIL}")
            if abs(sp - 1) > 1e-6:
                chain.append(f"setpts=PTS/{sp:.5f}")
            chain.append(_rate_filter(mode, fps))
            chain += tm
            if pre_pad > 1e-3:
                chain.append(f"tpad=start_mode=clone:start_duration={pre_pad:.4f}")
        # fps 뒤에는 setpts 를 두지 않음: 시각은 이미 0부터이고, ffmpeg 7 은 setpts 가 프레임 수(30fps)를 지워서
        # 전환(xfade)이 'current rate of 1/0 is invalid' 로 실패함
        rep = [f"tpad=stop_mode=clone:stop_duration={dur + 1:.3f}", f"trim=end_frame={n}"]
        out = lab("s")
        if md["kind"] == "image":
            rep = [f"fps={fps}"] + rep
            if bg_pre:  # 사진 → 흐린 배경과 화면 맞춤을 둘 다 한 장에서 먼저 만들고 프레임은 그다음에 늘림
                s0, b0, bgl = lab("i"), lab("i"), lab("g")
                fc.append(f"[{idx}:v]" + ",".join(chain) + f",split[{s0}][{b0}]")
                fc.append(f"[{b0}]" + ",".join(list(bg_pre) + rep) + f"[{bgl}]")
                fc.append(f"[{s0}]" + ",".join(list(pre or []) + rep) + f"[{out}]")
                return out, bgl
            chain += list(pre or []) + rep
        else:
            chain += rep + _color_filters(it)
        fc.append(f"[{idx}:v]" + ",".join(chain) + f"[{out}]")
        return out

    def layer(it, t_a, t_b):
        """아이템 → ('full', 라벨) 화면 전체 / ('ov', 라벨, x식, y식, 겹치기 형식) 겹치기."""
        md = media[it["media"]]
        rfx = None
        if it.get("rfk") and md["kind"] != "image":   # 화면 좌우 얼굴 따라가기 (BR-326): 구간 안에서 움직이면 crop x 를 시간 식으로 · 아니면 그 구간 값
            e, anim = _kf_expr(it, "reframe", t0, dur, p={"v": float(it.get("reframe", 0.5)), "k": sorted(it["rfk"], key=lambda k: k["t"])})
            rfx = e if anim else None
            if not anim:
                it = dict(it, reframe=float(e))
        fit = _fit(it, md, seq, W, H, rfx)
        bw, bh, bx, by = fit["bw"], fit["bh"], fit["bx"], fit["by"]
        bg = fit["bg"]
        img_pre = md["kind"] == "image"  # 사진: 크기 맞추기·흐린 배경을 한 번만 하고 프레임을 늘림
        if bg == "blur":
            qw, qh = _even(W / 4), _even(H / 4)  # 1/4 크기에서 흐리게 → 키움 (훨씬 빠름)
            br = max(1, round(6 * min(qw, qh) / 270))  # 흐림 정도는 화면 크기에 비례 (720p·1080p·4K 모두 같게 보이게)
            blur = [f"scale={qw}:{qh}:force_original_aspect_ratio=increase", f"crop={qw}:{qh}", f"boxblur={br}:3", f"scale={W}:{H}",
                    "eq=brightness=-0.08", "setsar=1"]
        if img_pre and bg == "blur":
            src, bgl = add_input(it, t_a, t_b, [fit["chain"], "setsar=1"], blur)
        else:
            src = add_input(it, t_a, t_b, [fit["chain"], "setsar=1"] if img_pre else None)
        sx, s_anim = _kf_expr(it, "scale", t0, dur, scale=0.01)  # 키프레임 → 구간 안 시간 t 의 식
        rx, r_anim = _kf_expr(it, "rot", t0, dur, scale=math.pi / 180)
        px, px_anim = _kf_expr(it, "pos", t0, dur, comp=0, scale=W, offset=-0.5 * W)
        py, py_anim = _kf_expr(it, "pos", t0, dur, comp=1, scale=H, offset=-0.5 * H)
        op = param(it, "opacity")
        op_anim = bool(op["k"]) and len({round(float(k["v"]), 3) for k in op["k"]}) > 1
        op_const = float(kf_at(op, i_mt(it, tmid))) / 100
        anc = param(it, "anchor")["v"]
        ax, ay = bx + float(anc[0]) * bw, by + float(anc[1]) * bh
        cx, cy = bx + bw / 2, by + bh / 2
        rot = r_anim or abs(float(rx)) > 1e-6
        motion = s_anim or r_anim or px_anim or py_anim or rot or abs(float(sx) - 1) > 1e-6 or abs(float(px)) > 0.5 or abs(float(py)) > 0.5
        has_op = op_anim or op_const < 0.999
        fg = lab("f")
        if bg == "blur" and not img_pre:
            s1, s2 = lab("b"), lab("c")
            fc.append(f"[{src}]split[{s1}][{s2}]")
            bgl = lab("g")
            fc.append(f"[{s1}]" + ",".join(blur) + f"[{bgl}]")
            src = s2
        elif bg == "box":
            bgl = lab("g")
            pic = _ep_bg_file(fit, W, H, tmp) if fit.get("band") or fit.get("logo") else None
            if pic:  # 은평 스타일 띠 그림·로고: 한 장 그림을 한 번만 읽어 n 프레임으로 (사진 클립과 같은 방법 · 못 만들면 아래 색 바탕)
                idx = len(inputs)
                inputs.append(["-loop", "1", "-framerate", "1", "-t", f"{dur + 1.5:.3f}", "-f", "image2", "-pattern_type", "none", "-i", str(pic)])
                fc.append(f"[{idx}:v]setpts=PTS-STARTPTS,fps={fps},tpad=stop_mode=clone:stop_duration={dur + 1:.3f},trim=end_frame={n},format=yuv420p,setsar=1[{bgl}]")
            else:
                fc.append(f"color=c=0x{fit['bar'].lstrip('#')}:s={W}x{H}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n}[{bgl}]")
        chain = [] if img_pre else [fit["chain"], "setsar=1"]
        full_plain = not motion and (bg is not None or (bw == W and bh == H and it["track"] == "V1" and md["kind"] != "image"))
        if not motion and bg is None and it["track"] == "V1" and md["kind"] != "image" and not (bw == W and bh == H):
            chain.append(f"pad={W}:{H}:{int(bx)}:{int(by)}:color=black")  # 가로 영상 맞춤(위아래/좌우 검정)
            full_plain = True
        S = f"({sx})"
        R = f"({rx})"
        if motion and not (s_anim or r_anim or px_anim or py_anim or rot or has_op) and bg is None and md["kind"] != "image":
            # 고정 확대(줌 컷·펀치인): 화면을 다 덮으면 겹치기 없이 키우고 잘라내기만 (2~3배 빠름, 같은 그림)
            S_ = float(sx)
            zw, zh = _even(bw * S_), _even(bh * S_)
            xo = ax + float(px) + S_ * (cx - ax) - bw * S_ / 2
            yo = ay + float(py) + S_ * (cy - ay) - bh * S_ / 2
            if xo <= 0 and yo <= 0 and xo + zw >= W and yo + zh >= H:
                # 미리보기·예전 RGBA 겹치기와 같은 자리 (홀수 칸도 그대로 · exact=1 이 없으면 짝수로 내려서 1칸 어긋남)
                ox, oy = min(int(-xo), zw - W), min(int(-yo), zh - H)
                chain += [f"scale={zw}:{zh}", f"crop={W}:{H}:{ox}:{oy}:exact=1"]
                fc.append(f"[{src}]" + ",".join(chain) + f"[{fg}]")
                return ("full", fg)
        dx, dy = cx - ax, cy - ay
        # 움직이는 확대·이동(켄 번스·옆으로 흐르기): 짝수 크기 + 반올림한 자리 → 가운데가 반 칸씩 흔들리지 않고 1칸씩 부드럽게
        ev = (s_anim or px_anim or py_anim) and not rot
        CX, CY = f"({ax:.4f}+({px})+{S}*{dx:.4f})", f"({ay:.4f}+({py})+{S}*{dy:.4f})"  # 그림 중심 = 기준점 + 이동 + 배율·(중심-기준점)
        if ev and not has_op and bg is None and md["kind"] != "image":
            # 매 프레임 화면을 다 덮으면 겹치지 않고 프레임마다 키우고 잘라냄 (yuv420 겹치기는 2칸씩 건너뛰어 떨림 · 더 빠름)
            ps, pp = param(it, "scale"), param(it, "pos")
            zw0, zh0 = (0, 0) if s_anim else (_even(bw * float(sx)), _even(bh * float(sx)))
            cover = s_anim or (zw0 >= W and zh0 >= H)
            for f in range(n):
                m = i_mt(it, t0 + f / fps)
                s_ = float(kf_at(ps, m)) / 100 if s_anim else float(sx)
                p_ = kf_at(pp, m)
                qx = float(p_[0]) * W - W / 2 if px_anim else float(px)
                qy = float(p_[1]) * H - H / 2 if py_anim else float(py)
                zw, zh = (2 * math.floor(max(W, bw * s_) / 2 + 0.5), 2 * math.floor(max(H, bh * s_) / 2 + 0.5)) if s_anim else (zw0, zh0)
                ox = -math.floor(ax + qx + s_ * dx - zw / 2 + 0.5)
                oy = -math.floor(ay + qy + s_ * dy - zh / 2 + 0.5)
                if not (cover and bw * s_ >= W - 0.01 and bh * s_ >= H - 0.01 and -1 <= ox <= zw - W + 1 and -1 <= oy <= zh - H + 1):
                    cover = False
                    break
            if cover:
                if s_anim:  # 화면보다 작아지지 않게 (crop 이 화면 밖을 읽지 않음 · 1칸 넘치면 crop 이 안쪽으로 맞춤)
                    zw, zh = f"2*floor(max({W},{bw}*{S})/2+0.5)", f"2*floor(max({H},{bh}*{S})/2+0.5)"
                    chain.append(f"scale=w='{zw}':h='{zh}':eval=frame")
                else:
                    zw, zh = zw0, zh0
                    chain.append(f"scale={zw}:{zh}")
                chain.append(f"crop={W}:{H}:x='-floor({CX}-({zw})/2+0.5)':y='-floor({CY}-({zh})/2+0.5)':exact=1")
                fc.append(f"[{src}]" + ",".join(chain) + f"[{fg}]")
                return ("full", fg)
        if motion:
            if rot:  # 회전할 때만 투명 바탕이 필요
                chain.append("format=rgba")
                D = _even(math.hypot(bw, bh) + 2)
                chain.append(f"rotate=a='{rx}':ow={D}:oh={D}:c=black@0")
                fw, fh = D, D
            else:
                fw, fh = bw, bh
            ww, hh = fw, fh
            if s_anim:
                ww, hh = (f"2*floor(max(2,{fw}*{S})/2+0.5)", f"2*floor(max(2,{fh}*{S})/2+0.5)") if ev else (f"max(2,{fw}*{S})", f"max(2,{fh}*{S})")
                chain.append(f"scale=w='{ww}':h='{hh}':eval=frame")
            elif abs(float(sx) - 1) > 1e-6:
                ww, hh = _even(fw * float(sx)), _even(fh * float(sx))
                chain.append(f"scale={ww}:{hh}")
            # 기준점이 위치에 오도록: 중심 = 기준점 + 이동 + R·S·(중심-기준점)
            if rot:
                X = f"({ax:.2f}+({px})+{S}*(cos({R})*{dx:.2f}-sin({R})*{dy:.2f})-{fw}*{S}/2)"
                Y = f"({ay:.2f}+({py})+{S}*(sin({R})*{dx:.2f}+cos({R})*{dy:.2f})-{fh}*{S}/2)"
            elif ev:  # 위 잘라내기와 같은 자리
                X, Y = f"floor({CX}-({ww})/2+0.5)", f"floor({CY}-({hh})/2+0.5)"
            else:
                X = f"({ax:.2f}+({px})+{S}*{dx:.2f}-{fw}*{S}/2)"
                Y = f"({ay:.2f}+({py})+{S}*{dy:.2f}-{fh}*{S}/2)"
        else:
            X, Y = f"{bx:.2f}", f"{by:.2f}"
        if has_op:
            if "format=rgba" not in chain:
                chain.append("format=rgba")
            if op_anim:  # 프레임마다 불투명도 명령 (반 프레임 앞 시각 → 반올림 때문에 한 프레임 늦게 적용되는 일 없게)
                cmd = f"op{k_seg}_{cnt['l']}.cmd"
                rows = [f"{max(0.0, (f - 0.5) / fps):.6f} colorchannelmixer@o{cnt['l']} aa {float(kf_at(op, i_mt(it, t0 + f / fps))) / 100:.4f};" for f in range(n)]
                (tmp / cmd).write_text("\n".join(rows) + "\n", encoding="utf-8")
                chain += [f"sendcmd=f={cmd}", f"colorchannelmixer@o{cnt['l']}=aa={op_const:.4f}"]
            else:
                chain.append(f"colorchannelmixer=aa={op_const:.4f}")
        fc.append(f"[{src}]" + (",".join(chain) or "null") + f"[{fg}]")
        # 크기·위치가 움직이면 yuv444 로 겹침 → 1칸씩 움직임 (yuv420 은 2칸씩 건너뛰어 느린 이동·켄 번스가 떨려 보임 · RGBA 보다는 빠름)
        ofmt = "yuv444" if (s_anim or px_anim or py_anim) and "format=rgba" not in chain else "auto"
        if bg:
            ly = lab("y")
            if fit.get("win") and motion:
                # 은평 스타일(layout.win · D-424): 확대·이동·회전한 그림은 영상 창(by ~ by+bh) 안에서만 — 창 크기 띠 색 바탕에 먼저 얹고 그 창을 제자리에
                # (창 위 끝은 yuv420 겹치기처럼 짝수 칸 = 움직이지 않는 클립의 창과 같은 자리 · 글 아래 끝 floor 로 같은 칸)
                wy, wl, lw = int(by) & ~1, lab("g"), lab("y")
                fc.append(f"color=c=0x{fit['bar'].lstrip('#')}:s={W}x{int(bh)}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n}[{wl}]")
                overlay(wl, fg, X, f"floor({Y})-{wy}", ofmt, lw)
                overlay(bgl, lw, "0", str(wy), "auto", ly)
            else:
                overlay(bgl, fg, X, Y, ofmt, ly)
            return ("full", ly)
        if full_plain and not has_op:
            return ("full", fg)
        return ("ov", fg, X, Y, ofmt)

    def put(comp, lay, push=None):
        """comp 위에 레이어를 올림. push: x 를 더 미는 식."""
        if lay[0] == "full" and comp is None and not push:
            return lay[1]
        if comp is None:
            comp = lab("k")
            fc.append(f"color=c=black:s={W}x{H}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n},format=yuv420p[{comp}]")
        out = lab("o")
        x, y = ("0", "0") if lay[0] == "full" else (lay[2], lay[3])
        if push:
            x = f"({x})+({push})"
        overlay(comp, lay[1], x, y, lay[4] if len(lay) > 4 else "auto", out)
        return out

    def overlay(main, top, x, y, ofmt, out):
        if ofmt == "yuv444":
            # 바탕은 이웃값으로 늘리고(neighbor) 평균으로 줄임(area) → 겹친 곳 밖은 원래 값 그대로 (기본 변환은 색이 살짝 번짐)
            m = lab("m")
            fc.append(f"[{main}]scale=flags=neighbor,format=yuv444p[{m}]")
            fc.append(f"[{m}][{top}]overlay=x='{x}':y='{y}':eval=frame:format=yuv444,scale=flags=area,format=yuv420p[{out}]")
        else:
            fc.append(f"[{main}][{top}]overlay=x='{x}':y='{y}':eval=frame:format={ofmt},format=yuv420p[{out}]")

    def base():
        b = lab("k")
        fc.append(f"color=c=black:s={W}x{H}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n},format=yuv420p[{b}]")
        return b

    def solid(c):
        b = lab("w")
        fc.append(f"color=c={c}:s={W}x{H}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n},format=yuv420p,setsar=1[{b}]")
        return b

    def norm(x):
        y = lab("p")
        fc.append(f"[{x}]format=yuv420p,setsar=1[{y}]")
        return y

    def exact(x):
        # xfade 는 입력이 끝날 때 마지막 프레임을 하나 빠뜨리기도 함 → 마지막 장면으로 채워 정확히 n 프레임 (뒤 구간과 소리 싱크 유지)
        y = lab("e")
        fc.append(f"[{x}]tpad=stop_mode=clone:stop_duration={2 / fps:.4f},trim=end_frame={n}[{y}]")
        return y

    near = lambda t, f: int(round(t * fps)) == f  # noqa: E731  (구간 경계와 같은 프레임 반올림 → 홀수 프레임 전환도 맞음)
    comp = None
    for tr_ in [t for t in seq["tracks"] if t["k"] == "v"]:
        if tr_.get("hide"):
            continue
        st = track_state(seq, tr_["id"], tmid, trans)
        if not st:
            continue
        if st[0] == "item":
            comp = put(comp, layer(st[1], t0, t0 + dur))
            continue
        _, tr, a, b, ts, te = st
        d = max(1e-3, te - ts)
        P = f"clip((T+{t0 - ts:.4f})/{d:.4f},0,1)"
        typ = tr.get("type", "dissolve")
        if typ == "push":
            Pt = P.replace("T", "t")
            if a:
                comp = put(comp, layer(a, t0, t0 + dur), f"-{W}*{Pt}")
            if b:
                comp = put(comp, layer(b, t0, t0 + dur), f"{W}*(1-{Pt})")
            continue
        out = lab("t")
        whole = near(ts, f0) and near(te, f1)
        mid = (ts + te) / 2
        if typ in ("black", "white") and (near(ts, f0) and near(mid, f1) or near(mid, f0) and near(te, f1)):
            # 검정/흰색 거치기: 앞 절반은 A→색, 뒤 절반은 색→B 를 빠른 xfade 로 (미리보기의 1-|2p-1| 과 같은 직선)
            # 한쪽만 있는 전환(맨 앞·맨 끝)은 없는 쪽 자리에 아래 트랙(없으면 검정)이 보임
            if near(ts, f0):
                X = norm(put(comp, layer(a, t0, t0 + dur)) if a else comp or base())
                fc.append(f"[{X}][{solid(typ)}]xfade=transition=fade:duration={dur:.4f}:offset=0[{out}]")
            else:
                Y = norm(put(comp, layer(b, t0, t0 + dur)) if b else comp or base())
                fc.append(f"[{solid(typ)}][{Y}]xfade=transition=fade:duration={dur:.4f}:offset=0[{out}]")
            comp = exact(out)
            continue
        below = comp or base()
        c1, c2 = lab("x"), lab("x")
        fc.append(f"[{below}]split[{c1}][{c2}]")
        X = put(c1, layer(a, t0, t0 + dur)) if a else c1
        Y = put(c2, layer(b, t0, t0 + dur)) if b else c2
        Xf, Yf = norm(X), norm(Y)
        if whole and typ in ("dissolve", "wipe"):  # 전환 전체가 한 구간이면 빠른 xfade
            fc.append(f"[{Xf}][{Yf}]xfade=transition={'fade' if typ == 'dissolve' else 'wipeleft'}:duration={dur:.4f}:offset=0[{out}]")
        elif typ == "wipe":
            fc.append(f"[{Xf}][{Yf}]blend=all_expr='if(gte(X,W*(1-{P})),B,A)'[{out}]")
        elif typ in ("black", "white"):
            mid_l, col = lab("m"), solid(typ)
            fc.append(f"[{Xf}][{Yf}]blend=all_expr='if(lt({P},0.5),A,B)'[{mid_l}]")
            fc.append(f"[{mid_l}][{col}]blend=all_expr='A*(1-(1-abs(2*{P}-1)))+B*(1-abs(2*{P}-1))'[{out}]")
        else:
            fc.append(f"[{Xf}][{Yf}]blend=all_expr='A*(1-{P})+B*{P}'[{out}]")
        comp = exact(out)
    if comp is None:
        comp = base()
    skin = skin_path((seq.get("layout") or {}).get("skin")) if skin_on(seq) else None
    if skin:
        # 풋살사관학교 스킨 (BR-320 · D-521): 영상 트랙(V1~V3 · 박스 바탕 · 전환·빈 곳 포함) 위 · 자막·타이틀·도형·전술 그림(ASS) 아래에 화면 전체로 —
        # 한 장 그림을 한 번만 읽어 화면 크기로 줄이고(4K 도) 투명 그대로 한 번만 yuva420p 로 바꿔 n 프레임으로 늘려 yuv420 그대로 겹침
        # (예전 rgba + overlay=format=auto 는 프레임마다 바꿔 기본 배치보다 1.9배 오래 걸림 · D-535)
        idx, sk, ko = len(inputs), lab("q"), lab("o")
        inputs.append(["-loop", "1", "-framerate", "1", "-t", f"{dur + 1.5:.3f}", "-f", "image2", "-pattern_type", "none", "-i", str(skin)])
        fc.append(f"[{idx}:v]setpts=PTS-STARTPTS,scale={W}:{H}:flags=lanczos,format=yuva420p,fps={fps},tpad=stop_mode=clone:stop_duration={dur + 1:.3f},"
                  f"trim=end_frame={n},setsar=1[{sk}]")
        fc.append(f"[{comp}][{sk}]overlay=x=0:y=0:format=yuv420,format=yuv420p[{ko}]")
        comp = ko
    final = lab("v")
    # 자막·타이틀·도형 (구간 시작 프레임만큼 밀어서 같은 ASS 사용) — 시각 단위를 1/fps 로 맞추고 정수 프레임만큼 밂
    # (예전 'PTS+시작초/TB' 는 7.3333*30=219.999 를 버림해 자막이 한 프레임 일찍 나오는 구간이 있었음)
    fc.append(f"[{comp}]format=yuv420p,setsar=1,settb=1/{fps},setpts=PTS+{int(f0)},subtitles=subs.ass:fontsdir=fonts,setpts=PTS-STARTPTS[{final}]")
    (tmp / f"fc{k_seg}.txt").write_text(";\n".join(fc), encoding="utf-8")
    args = []
    for a in inputs:
        args += a
    return args, final, n


_FC = {}


def _fc_opt():
    """필터 그래프 파일 옵션 (ffmpeg 7.1 부터 -/filter_complex)."""
    if "o" not in _FC:
        h = core.run([core.ffmpeg(), "-hide_banner", "-h", "long"]).stdout
        _FC["o"] = "-filter_complex_script" if "filter_complex_script" in h else "-/filter_complex"
    return _FC["o"]


RESERVE_MB = 1500  # 내보내기 동안 앱·브라우저·소리 만들기 몫으로 남겨 둘 메모리


def _mem_workers(default, media, W, H, log=None):
    """구간을 동시에 몇 개 만들지: 남은 메모리 ÷ ffmpeg 하나 예상 메모리 (모자라면 줄임 · 적어도 1).
    예상: 아이폰 HDR·출력의 2배보다 큰 원본(4K → 1080p 등) 약 1.1GB · 출력이 1080p 보다 크면 0.9GB · 그 밖 0.5GB (측정값 기준)."""
    if default <= 1:
        return default
    import worker
    free = worker.avail_mb()
    if free is None:
        return default
    vids = [m for m in media.values() if m.get("kind") == "video"]
    big = any(media_hdr(m) or (m.get("w") or 0) * (m.get("h") or 0) > 2 * W * H for m in vids)
    est = 1100 if big else (900 if W * H > 1920 * 1080 else 500)
    n = max(1, min(default, (free - RESERVE_MB) // est))
    if n < default and log:
        log(f"  메모리가 넉넉하지 않아 {'한 번에 하나씩' if n == 1 else f'한 번에 {n}개씩'} 만들어요 (남은 메모리 {free}MB)")
    return n


def _run_ff(args, cwd, on_frame=None, on_time=None, procs=None, abort=None, enc=None, want_err=False):
    """ffmpeg 실행 (진행률·취소 지원). abort: 이 작업만 멈추는 신호. enc: 그래픽카드 인코더면 그 문제인지 구분.
    want_err: 끝나면 ffmpeg 가 남긴 글(측정값 등)을 돌려줌."""
    if CANCEL.is_set() or (abort is not None and abort.is_set()):
        raise Cancelled()
    with tempfile.TemporaryFile(dir=cwd) as errf:
        p = core.popen([core.ffmpeg(), "-hide_banner", "-nostats", "-progress", "pipe:1"] + args, cwd=str(cwd),
                       stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL)
        _PROCS.add(p)
        if procs is not None:
            procs.add(p)
        try:
            for line in p.stdout:
                try:
                    if on_frame and line.startswith(b"frame="):
                        on_frame(int(line[6:].strip() or 0))
                    elif on_time and line.startswith(b"out_time_us="):
                        on_time(int(line[12:].strip() or 0) / 1e6)
                except ValueError:
                    pass
            p.wait()
        finally:
            _PROCS.discard(p)
            if procs is not None:
                procs.discard(p)
            p.stdout.close()
        if CANCEL.is_set() or (abort is not None and abort.is_set()):
            raise Cancelled()
        if p.returncode:
            errf.seek(0)
            msg = errf.read().decode("utf-8", "replace")
            if enc and _hw_fail(msg, enc):
                e = HwEncError(msg[-600:])
                e.session = bool(HW_SESSION.search(msg))
                raise e
            raise RuntimeError(msg[-600:])
        if want_err:
            errf.seek(0)
            return errf.read().decode("utf-8", "replace")


# 그래픽카드 인코더 (있으면 내보내기가 몇 배 빨라짐) — 실제로 짧게 인코딩해 보고 되는 것만 씀
_HW = {}


def _venc(enc, pr, W, H, fps):
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", str(pr["crf"] + 2), "-b:v", "0", "-pix_fmt", "yuv420p"]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-preset", "medium", "-global_quality", str(pr["crf"] + 3), "-pix_fmt", "nv12"]
    if enc == "h264_amf":
        return ["-c:v", enc, "-quality", "balanced", "-rc", "cqp", "-qp_i", str(pr["crf"] + 2), "-qp_p", str(pr["crf"] + 4), "-pix_fmt", "yuv420p"]
    if enc == "h264_videotoolbox":
        return ["-c:v", enc, "-b:v", f"{max(4, int(W * H * fps * 0.17 / 1e6))}M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", pr["preset"], "-crf", str(pr["crf"]), "-pix_fmt", "yuv420p"]


def hw_encoder():
    if "enc" in _HW:
        return _HW["enc"]
    import sys
    cands = ["h264_videotoolbox"] if sys.platform == "darwin" else ["h264_nvenc", "h264_qsv", "h264_amf"] if sys.platform == "win32" else ["h264_nvenc"]
    _HW["enc"] = None
    pr = PRESETS["youtube"]
    for enc in cands:
        r = core.run([core.ffmpeg(), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=1"] + _venc(enc, pr, 1280, 720, 30) + ["-f", "null", "-"])
        if r.returncode == 0:
            _HW["enc"] = enc
            break
    return _HW["enc"]


def _hw_works(enc, pr, W, H, fps):
    r = core.run([core.ffmpeg(), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={fps}:d=0.2"]
                 + _venc(enc, pr, W, H, fps) + ["-f", "null", "-"])
    return r.returncode == 0


def _atempo(sp):
    out = []
    while sp > 2.0 + 1e-9:
        out.append("atempo=2.0")
        sp /= 2.0
    while sp < 0.5 - 1e-9:
        out.append("atempo=0.5")
        sp /= 0.5
    if abs(sp - 1) > 1e-6:
        out.append(f"atempo={sp:.5f}")
    return out


def _kf_np(p, m, np):
    ks = p["k"]
    if not ks:
        return np.full(m.shape, float(p["v"]), np.float32)
    ts = np.array([k["t"] for k in ks], np.float64)
    vs = np.array([float(k["v"]) for k in ks], np.float64)
    if len(ks) == 1:
        return np.full(m.shape, vs[0], np.float32)
    idx = np.clip(np.searchsorted(ts, m, side="right") - 1, 0, len(ts) - 2)
    ta, tb = ts[idx], ts[idx + 1]
    x = np.clip((m - ta) / np.maximum(1e-6, tb - ta), 0, 1)
    ease = np.array([k.get("e") == "ease" for k in ks])[idx]
    x = np.where(ease, x * x * (3 - 2 * x), x)
    v = vs[idx] + (vs[idx + 1] - vs[idx]) * x
    return np.where(m <= ts[0], vs[0], np.where(m >= ts[-1], vs[-1], v)).astype(np.float32)


SR = 48000
ATEMPO_PAD = 0.1  # 빠르기 바꾼 소리는 이만큼(초) 더 읽음 — atempo 가 끝을 1~3ms 덜 내보내 생기는 무음 틈 메우기
AFFTDN_DELAY = int(round(SR * 0.025))  # 잡음 줄이기(afftdn)가 소리를 25ms(1200샘플) 늦게 내보냄 → 그만큼 당김
EDGE_FADE = 0.012  # 컷 자리 소리 페이드(초) — 이어 붙인 곳 파형이 끊겨 '틱' 하는 소리 막기 (D-401 · 10~20ms 는 말소리에서 들리지 않음)


def _cut_edges(it, md, track_items):
    """소리 클립의 (앞 끝, 뒤 끝)이 원본 소리 한가운데를 자른 컷인지 — 원본 처음·끝 그대로이거나, 같은 트랙 바로 옆 클립이 같은 원본을
    끊김 없이 이어 가면(나누기만 한 곳 · 확대 컷·말 빠르기 나누기) 페이드하지 않음 (D-401)."""
    mdur = float(md.get("dur") or 1e9)
    st, en = it["start"], i_end(it)

    def cuts_source(m):
        return 1e-3 < m < mdur - 1e-3

    def joined(t, other_t):
        for x in track_items:
            if x is it or abs(other_t(x) - t) > 1e-3:
                continue
            # 빠르기는 달라도 됨 — 말 빠르기 맞추기(_apply_tempo)·나눈 뒤 한쪽만 빠르기를 바꾼 곳도 원본은 이어짐 (D-405)
            if x.get("media") == it.get("media") and bool(x.get("rev")) == bool(it.get("rev")) \
                    and abs(i_mt(x, other_t(x)) - i_mt(it, t)) < 2e-3:
                return True
        return False
    head = cuts_source(i_mt(it, st)) and not joined(st, i_end)
    tail = cuts_source(i_mt(it, en)) and not joined(en, lambda x: x["start"])
    return head, tail


DUCK_PAD = 0.2   # 배경음악 줄이기: 받아쓴 말(자막) 앞뒤 이만큼(초)도 말하는 중으로 봄 (E2 · BR-095)


def _rms20(buf, n, win):
    """소리 버퍼 (n×2) → 20ms 창마다 RMS (n // win + 1 칸)."""
    import numpy as np
    rms = np.zeros(n // win + 1, np.float32)
    for b in range(0, n, win * 3000):
        seg = np.asarray(buf[b: b + win * 3000])
        k = len(seg) // win
        if k:
            rms[b // win: b // win + k] = np.sqrt((seg[: k * win] ** 2).mean(axis=(1,)).reshape(k, win).mean(axis=1))
    return rms


def _merge_spans(ivs, gap=0.0):
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def speech_words(segs, gap=0.3):
    """받아쓰기(헛것 뺀 것) → 말소리 자리 [[a, b]] (원본 시각 · 낱말 사이 gap 초 안은 합침 · 낱말 시각이 없는 구간은 통째로).
    프로젝트 'speech' 로 저장 — 자막을 지워도(일부러 뺀 말·지운 자막) 배경음악 줄이기는 그 말 자리를 앎 (E2 검토)."""
    ivs = []
    for sg in segs or ():
        ws = [w for w in sg.get("words") or () if str(w.get("w") or "").strip()]
        try:
            ivs += [(float(w["s"]), float(w["e"])) for w in ws] if ws else [(float(sg["start"]), float(sg["end"]))]
        except (KeyError, TypeError, ValueError):
            continue
    return [[round(a, 2), round(b, 2)] for a, b in _merge_spans([(a, b) for a, b in ivs if b > a], gap)]


def speech_spans(seq):
    """배경음악을 줄일 '말하는 중' 타임라인 구간 — 대사 트랙의 원본 클립에서 받아쓴 말(프로젝트 자막 captions 와 받아쓴 말 자리 speech,
    원본 시각) 자리 ± DUCK_PAD (자막을 지운 말도 줄임 · 받아쓰기가 아예 놓친 말은 못 앎 — I-161).
    (말 없는 시범·환호·공 소리는 줄이지 않음 · 미리보기 editor.html speechSpans 와 같은 규칙)
    → (구간 [[a, b]], 소리 크기로 볼 다른 영상 대사 클립 구간 [[a, b]]) · 자막이 하나도 없으면 None (예전처럼 소리 크기로)."""
    caps = []
    for c in seq.get("captions") or ():
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a and str(c.get("text") or "").strip():
            caps.append((a, b))
    for c in seq.get("speech") or ():
        try:
            a, b = float(c[0]), float(c[1])
        except (IndexError, TypeError, ValueError):
            continue
        if b > a:
            caps.append((a, b))
    if not caps:
        return None
    caps.sort()
    tracks = {t["id"]: t for t in seq.get("tracks") or () if t.get("k") == "a"}
    talk, other = [], []
    for it in seq.get("items") or ():
        tr = tracks.get(it.get("track"))
        if not tr or tr.get("role", "dialog") != "dialog" or tr.get("mute") or it.get("mute") or tr.get("voiceFx") is False:
            continue
        if it.get("media", "main") != "main":
            other.append([float(it["start"]), i_end(it)])
            continue
        a, b, sp, st = float(it["in"]), float(it["out"]), i_sp(it), float(it["start"])
        for x, y in caps:
            x, y = max(a, x), min(b, y)
            if y <= x:
                continue
            t0, t1 = (st + (b - y) / sp, st + (b - x) / sp) if it.get("rev") else (st + (x - a) / sp, st + (y - a) / sp)
            talk.append((t0 - DUCK_PAD, t1 + DUCK_PAD))
    nd = no_duck_spans(seq)  # '여기는 줄이지 않기' 구간은 말이 있어도 줄이지 않음 (D-282)
    return _minus_spans(_merge_spans(talk), nd), _minus_spans(_merge_spans(other), nd)


def no_duck_spans(seq):
    """'여기는 줄이지 않기' 구간 (편집본 noDuck · 타임라인 시각 [[a, b]] · 겹친 것은 합침) — 이 안에서는 배경음악을 줄이지 않음.
    받아쓴 말 자리·소리 크기 어느 쪽으로 판단해도 같음 (미리보기 editor.html noDuckSpans 와 같은 규칙 · D-282)."""
    ivs = []
    for r in seq.get("noDuck") or ():
        try:
            a, b = float(r[0]), float(r[1])
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        if math.isfinite(a) and math.isfinite(b) and b > a:
            ivs.append((max(0.0, a), b))
    return _merge_spans(ivs)


def _minus_spans(spans, cuts):
    """구간 목록에서 cuts 구간을 뺌 (둘 다 시작 순 · 겹치지 않음) — cuts 가 없으면 그대로."""
    if not cuts:
        return spans
    out = []
    for a, b in spans:
        for x, y in cuts:
            if y <= a or x >= b:
                continue
            if x > a:
                out.append([a, x])
            a = max(a, y)
            if a >= b:
                break
        if a < b:
            out.append([a, b])
    return out


def _mix_audio(seq, media, t_lo, t_hi, tmp, trans, progress, abort=None):
    """소리 섞기 (실패·멈춤 때는 열린 임시 파일을 바로 놓아 Windows 에서도 지워지게)."""
    try:
        return _mix_audio_impl(seq, media, t_lo, t_hi, tmp, trans, progress, abort)
    except BaseException as e:
        traceback.clear_frames(e.__traceback__)
        raise


def voice_filters(voice):
    """목소리 보정 설정({hp, nr, comp}) → ffmpeg 소리 필터 목록 (대사 트랙들을 섞은 소리에 · 48kHz 스테레오 기준).
    MSG 가 효과음 크기를 '보정한 말소리'에 맞출 때도 같은 것을 씀 (msg._voice_peak)."""
    voice = voice or {}
    D = AFFTDN_DELAY
    return (["highpass=f=80"] if voice.get("hp") else []) + \
        ([f"apad=pad_len={D}", f"afftdn=nr={[0, 8, 14, 20][min(3, int(voice.get('nr') or 0))]}:nf=-40", f"atrim=start_sample={D}"] if voice.get("nr") else []) + \
        (["acompressor=threshold=0.08:ratio=3:attack=8:release=160:makeup=2"] if voice.get("comp") else [])


def _mix_audio_impl(seq, media, t_lo, t_hi, tmp, trans, progress, abort):
    """소리 섞기: 트랙별 볼륨·키프레임·페이드·전환·음소거/솔로 → 대사 + 배경음악(말할 때 자동 줄임).
    클립마다 ffmpeg 로 읽는 것은 몇 개씩 동시에 (컷이 많은 롱폼이 훨씬 빨라짐)."""
    import numpy as np
    n = max(1, int(round((t_hi - t_lo) * SR)))
    tracks = {t["id"]: t for t in seq["tracks"] if t["k"] == "a"}
    solo = any(t.get("solo") for t in tracks.values())
    by = {it["id"]: it for it in seq["items"]}
    jobs, on_track = [], {}
    for it in seq["items"]:
        on_track.setdefault(it["track"], []).append(it)
    for it in seq["items"]:
        if it["track"] not in tracks:
            continue
        tr = tracks[it["track"]]
        md = media.get(it["media"])
        if not md or not md.get("audio") or it.get("mute") or tr.get("mute") or (solo and not tr.get("solo")):
            continue
        # 전환 때문에 앞뒤로 늘어나는 범위
        a_lo, a_hi = it["start"], i_end(it)
        tgain = []
        for tt in trans:
            if tt["track"] != it["track"] or it["id"] not in (tt.get("a"), tt.get("b")):
                continue
            r = trans_range(seq, tt, by)
            if not r:
                continue
            a_lo, a_hi = min(a_lo, r[0]), max(a_hi, r[1])
            tgain.append((r, "out" if tt.get("a") == it["id"] else "in", tt.get("type", "cpower")))
        a_lo, a_hi = max(a_lo, t_lo), min(a_hi, t_hi)
        if a_hi - a_lo < 1e-3:
            continue
        ma, mb = i_mt(it, a_lo), i_mt(it, a_hi)
        lo, hi = max(0.0, min(ma, mb)), min(md.get("dur") or 1e9, max(ma, mb))
        if hi - lo < 1e-3:
            continue
        p_in = media_path(md["file"], md["src"])
        if not p_in.exists():
            raise RuntimeError(f"미디어 파일이 없어요 · {md['file']}")
        head, tail = _cut_edges(it, md, on_track[it["track"]])
        edges = (head and not any(sd == "in" for _, sd, _ in tgain), tail and not any(sd == "out" for _, sd, _ in tgain))  # 전환 쪽은 전환이 섞음
        music = tr.get("role", "dialog") != "dialog"
        fill = _chan_fill(md, abort) if not music and tr.get("voiceFx") is not False else []  # 한쪽만 녹음된 대사 → 양쪽에 (D-404 · 음악·효과음은 그대로)
        jobs.append((it, tr, md, p_in, a_lo, a_hi, lo, hi, tgain, edges, fill, music))
    # 목소리 보정 (대사 트랙들): 웅웅거림 제거 · 잡음 줄이기 · 크기 고르게 — 'voiceFx: false' 트랙(효과음)은 보정 없이 그 뒤에 더함
    # (효과음이 압축기를 지나면 말과 함께 눌렸다 키워져 정한 크기보다 커지고 말소리도 같이 눌림 · 판정: 띠로리가 말보다 큼)
    vf = voice_filters(seq.get("voice"))
    dia = mus = fxb = None
    # 보정하지 않는 트랙(효과음)은 짧은 소리 조각으로 메모리에 모았다가 보정한 대사에 더함 — 예전처럼 영상 길이만큼의 버퍼(fx.f32)를
    # 따로 만들면 49분 영상에서 임시 파일이 1GB 더 필요했음 · 조각이 FX_MEM 을 넘으면(긴 소리를 그 트랙에 넣음) 그때만 파일 버퍼로
    # 목소리 보정을 끈 편집본도 효과음은 따로 모음 — 배경음악 줄이기가 효과음을 더하기 전 대사 소리로 판단하게 (D-281)
    fx_parts, fx_bytes = [], [0]
    fx_on = any(not j[-1] and j[1].get("voiceFx") is False for j in jobs)
    try:
        dia = np.memmap(tmp / "dialog.f32", np.float32, "w+", shape=(n, 2))
        mus = np.memmap(tmp / "music.f32", np.float32, "w+", shape=(n, 2)) if any(j[-1] for j in jobs) else None
        lock = threading.Lock()
        my = set()
        stop = threading.Event()

        def halted():
            return CANCEL.is_set() or stop.is_set() or (abort is not None and abort.is_set())

        def one(job):
            it, tr, md, p_in, a_lo, a_hi, lo, hi, tgain, edges, fill, music = job
            if halted():
                raise Cancelled()
            sp = i_sp(it)
            tl_start = i_tl(it, hi if it.get("rev") else lo)  # 디코딩 시작점이 타임라인 어디인지
            af = [AUDIO_T0] + fill + (["areverse"] if it.get("rev") else []) + _atempo(sp)  # 늦게 시작하는 소리 줄기는 앞을 비워 제자리 (D-324)
            d_lo, d_hi = lo, hi
            if abs(sp - 1) > 1e-6:  # atempo 는 끝이 몇 ms 모자라게 나옴 → 조금 더 읽고 남는 건 아래에서 버림 (빠르게 한 컷 사이 '틱' 막기)
                if it.get("rev"):
                    d_lo = max(0.0, lo - ATEMPO_PAD)
                else:
                    d_hi = min(md.get("dur") or 1e9, hi + ATEMPO_PAD)
            cmd = [core.ffmpeg(), "-v", "error", "-ss", f"{d_lo:.4f}", "-t", f"{d_hi - d_lo:.4f}", "-i", str(p_in),
                   "-vn", "-af", ",".join(af) or "anull", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
            lvl = param(it, "level")
            g_static = 10 ** ((float(it.get("gain") or 0) + float(tr.get("vol") or 0)) / 20)
            to_fx = fx_on and not music and tr.get("voiceFx") is False
            bus = mus if music else None if to_fx else dia
            pos = int(round((tl_start - t_lo) * SR))
            end_i = int(round((a_hi - t_lo) * SR))
            st, en = it["start"], i_end(it)
            fi, fo = float(it.get("fadeIn") or 0), float(it.get("fadeOut") or 0)
            got = 0
            with tempfile.TemporaryFile(dir=tmp) as errf:
                p = core.popen(cmd, stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL)
                _PROCS.add(p)
                my.add(p)
                try:
                    while True:
                        if halted():
                            raise Cancelled()
                        raw = p.stdout.read(SR * 8 * 2)
                        if not raw:
                            break
                        x = np.frombuffer(raw[: len(raw) // 8 * 8], np.float32).reshape(-1, 2)
                        got += len(x)
                        a0 = pos
                        pos += len(x)
                        if a0 >= end_i:
                            continue
                        lo_i = max(0, -a0)
                        hi_i = min(len(x), end_i - a0, n - a0)
                        if hi_i <= lo_i:
                            continue
                        x = x[lo_i:hi_i]
                        tau = t_lo + (a0 + lo_i + np.arange(len(x))) / SR
                        g = (10 ** (_kf_np(lvl, i_mt(it, tau), np) / 20)) * g_static
                        if fi > 0:
                            g = g * np.clip((tau - st) / fi, 0, 1)
                        if fo > 0:
                            g = g * np.clip((en - tau) / fo, 0, 1)
                        if edges[0]:
                            g = g * np.clip((tau - st) / EDGE_FADE, 0, 1)
                        if edges[1]:
                            g = g * np.clip((en - tau) / EDGE_FADE, 0, 1)
                        for (r0, r1), side, typ in tgain:
                            pr = np.clip((tau - r0) / max(1e-4, r1 - r0), 0, 1)
                            if typ == "cgain":
                                w = 1 - pr if side == "out" else pr
                            else:
                                w = np.cos(pr * np.pi / 2) if side == "out" else np.sin(pr * np.pi / 2)
                            g = g * w
                        # 아이템 범위(전환 포함) 밖은 0
                        g = g * ((tau >= a_lo - 1e-6) & (tau < a_hi + 1e-6))
                        y = x * g[:, None].astype(np.float32)
                        with lock:
                            if to_fx and fxb is None and fx_bytes[0] + y.nbytes > FX_MEM:
                                fx_spill()
                            if to_fx and fxb is None:
                                fx_parts.append((a0 + lo_i, y.copy()))
                                fx_bytes[0] += y.nbytes
                            else:
                                (fxb if to_fx else bus)[a0 + lo_i: a0 + hi_i] += y
                finally:
                    p.stdout.close()
                    p.wait()
                    _PROCS.discard(p)
                    my.discard(p)
                    bus = x = y = None  # noqa: F841 — 이 스레드의 흔적이 메모리 매핑을 붙잡지 않게
                if halted():
                    raise Cancelled()
                if p.returncode and not got:
                    errf.seek(0)
                    raise RuntimeError(f"소리를 읽지 못했어요 · {md['file']} · {errf.read().decode('utf-8', 'replace')[-200:]}")

        def fx_spill():  # (잠금 안에서) 모은 조각이 많으면 파일 버퍼로 옮김
            nonlocal fxb
            fxb = np.memmap(tmp / "fx.f32", np.float32, "w+", shape=(n, 2))
            for a, y in fx_parts:
                fxb[a:a + len(y)] += y
            fx_parts.clear()

        workers = max(1, min(4, os.cpu_count() or 2))
        if jobs:
            ex = ThreadPoolExecutor(workers)
            try:
                futs = [ex.submit(one, j) for j in jobs]
                for k, f in enumerate(futs):
                    try:
                        f.result()
                    except BaseException:
                        stop.set()
                        for p in list(my):
                            try:
                                p.kill()
                            except OSError:
                                pass
                        raise
                    progress((k + 1) / len(futs))
            finally:
                ex.shutdown(wait=True, cancel_futures=True)
        if vf:
            progress(0.98)
            dia.flush()
            ok = True
            try:
                _run_ff(["-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "dialog.f32",
                         "-af", ",".join(vf), "-f", "f32le", "-ar", str(SR), "-ac", "2", "dialog2.f32"], tmp, abort=abort)
            except RuntimeError:
                ok = False  # 보정이 안 되면 원래 소리로
            if ok and (tmp / "dialog2.f32").exists():
                cl = np.memmap(tmp / "dialog2.f32", np.float32, "r")
                m = min(n, len(cl) // 2)
                for b in range(0, m, SR * 10):
                    e = min(m, b + SR * 10)
                    dia[b:e] = np.asarray(cl[b * 2:e * 2]).reshape(-1, 2)
                del cl
        # 말할 때 배경음악 자동 줄이기 (덕킹) — 소리 크기 판단은 효과음을 더하기 전 대사 소리로 (자막이 없는 편집본·다른 영상 대사 클립에서
        # 효과음 '휙·팡'마다 음악이 -14dB 로 꺼졌다 살아나지 않게 · D-281) · 결과는 대사 버퍼에 그대로 더함 (임시 파일 하나 덜 씀)
        duck = seq.get("duck") or {}
        win = SR // 50  # 20ms
        nw = n // win + 1
        act = _rms20(dia, n, win) > 10 ** (-40 / 20) if mus is not None and duck.get("on") else None
        for a, y in fx_parts:  # 보정하지 않는 트랙(효과음)을 보정한 대사에 더함
            dia[a:a + len(y)] += y
        fx_parts.clear()
        if fxb is not None:
            fxb.flush()
            for b in range(0, n, SR * 10):
                e = min(n, b + SR * 10)
                dia[b:e] = dia[b:e] + fxb[b:e]
        if act is not None:
            tw = t_lo + np.arange(nw) * 0.02
            sp_tl = speech_spans(seq)
            if sp_tl is not None:  # 받아쓴 말 자리만 (말 없는 시범·환호에서는 음악이 살아남) · 다른 영상 대사 클립은 소리 크기로 (E2)
                on = np.zeros(nw, bool)
                for a, b in sp_tl[0]:
                    on |= (tw >= a) & (tw < b)
                oth = np.zeros(nw, bool)
                for a, b in sp_tl[1]:
                    oth |= (tw >= a) & (tw < b)
                act = on | (act & oth)
            hold = int(0.35 / 0.02)  # 말 사이 짧은 쉼은 그대로 줄인 상태
            act_h = act.copy()
            last = -10 ** 9
            for i in range(nw):
                if act[i]:
                    last = i
                act_h[i] = i - last <= hold
            for a, b in no_duck_spans(seq):  # '여기는 줄이지 않기' 구간은 말 뒤 쉼(hold)·소리 크기 판단과 상관없이 그대로 (D-282)
                act_h &= ~((tw >= a) & (tw < b))
            target = 10 ** (float(duck.get("amount", -14)) / 20)
            gw = np.ones(nw, np.float32)
            g = 1.0
            ca, cr = math.exp(-1 / (0.08 / 0.02)), math.exp(-1 / (0.45 / 0.02))
            for i in range(nw):
                tg = target if act_h[i] else 1.0
                c = ca if tg < g else cr
                g = tg + (g - tg) * c
                gw[i] = g
            for b in range(0, n, SR * 10):
                e = min(n, b + SR * 10)
                gi = np.interp(np.arange(b, e) / win, np.arange(nw), gw).astype(np.float32)
                dia[b:e] = dia[b:e] + mus[b:e] * gi[:, None]
        elif mus is not None:
            for b in range(0, n, SR * 10):
                dia[b: b + SR * 10] = dia[b: b + SR * 10] + mus[b: b + SR * 10]
        dia.flush()
        peak = 0.0
        if n <= SR * 4:  # 짧은 영상: 완전히 조용한지 (소리 크기 맞추기가 오류 나지 않게)
            peak = float(np.abs(np.asarray(dia)).max()) if n else 0.0
        return tmp / "dialog.f32", peak
    finally:
        # 메모리 매핑을 바로 놓음: 멈추거나 오류가 나도 (작업 스레드의 흔적이 남아 있어도) Windows 에서 임시 파일이 지워지게
        dia = mus = fxb = None  # noqa: F841


FX_MEM = 256 << 20   # 효과음 조각을 메모리에 모으는 한도 (바이트)
LOUD_TP_FLOOR = -4.0  # AAC 로 줄인 뒤 다시 맞출 때 최대 크기를 이보다 낮게 잡지 않음
LOUD_TP = -2.0    # 소리 크기 맞추기의 최대 크기(dBTP) — AAC 로 줄이면 순간 최대가 0.5dB 남짓 커져 -1.5 로 맞춘 소리가 -0.9 dBTP 로 나옴
#                   (판정: 유튜브 기준 -1 dBTP 넘음) → 여유를 둠
LOUD_TP_OUT = -1.2  # 다 줄인(AAC) 소리의 순간 최대가 이보다 크면 LOUD_TP 를 그만큼 더 낮춰 다시 만듦


def _true_peak(path, tmp, abort=None):
    """소리 파일의 순간 최대(dBTP · ebur128) — 못 재면 None."""
    try:
        r = _run_ff(["-v", "info", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], tmp, abort=abort, want_err=True)
    except RuntimeError:
        return None
    found = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r or "")
    return float(found[-1]) if found else None


LOUD_PEAK = -3.5  # 소리 크기 맞추기 전 미리 누르는 최대 크기(dBFS) — 공 차는 소리·효과음처럼 순간만 큰 소리가 있으면
#                   loudnorm 한 번(실시간)으로는 목표까지 못 올림 (최대 크기 제한에 걸려 -17 LUFS 처럼 작게 남음)


def _loud_measure(mix, tmp, af, lufs, abort=None):
    """loudnorm 첫 번째 재기 (print_format=json) → 잰 값 dict 또는 None (못 재면 한 번 재기로)."""
    try:
        r = _run_ff(["-v", "info", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", mix.name, "-af",
                     ",".join(list(af) + [f"loudnorm=I={lufs:.1f}:TP={LOUD_TP:.2f}:LRA=11:print_format=json"]), "-f", "null", "-"],
                    tmp, abort=abort, want_err=True)
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r or "", re.S)
        d = json.loads(m.group(0)) if m else None
        keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        if not d or any(k not in d for k in keys) or any(not re.fullmatch(r"-?[\d.]+", str(d[k]).strip()) for k in keys):
            return None   # (-inf 같은 값: 조용한 소리 → 한 번 재기)
        return {k: str(d[k]).strip() for k in keys}
    except (RuntimeError, ValueError):
        return None


def _loud_pre(mix, tmp, lufs, vol, abort=None):
    """소리 크기 맞추기 전처리: 섞은 소리를 한 번 재서(ebur128) 목표까지 모자란 만큼 미리 키우고, 그때 넘치는 순간 소리만 리미터로 누름.
    리미터가 누른 만큼 다시 한 번 재서 더 키움(2번 재기, 소리만이라 빠름). 그다음 loudnorm 은 남은 1dB 안팎만 맞춤 →
    목표 LUFS·최대 LOUD_TP 를 함께 지킴. 재지 못하거나 이미 충분하면 [] (예전과 같음)."""
    def measure(extra):
        try:
            r = _run_ff(["-v", "info", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", mix.name, "-af",
                         ",".join([f"volume={vol:.3f}"] + extra + ["ebur128=peak=true"]), "-f", "null", "-"], tmp, abort=abort, want_err=True)
        except RuntimeError:
            return None
        found = re.findall(r"I:\s+(-?[\d.]+) LUFS", r or "")
        return float(found[-1]) if found else None

    def chain(g):
        return [f"volume={g:.2f}dB", f"alimiter=limit={10 ** (LOUD_PEAK / 20):.4f}:attack=2:release=80:level=0:asc=1"]

    i_in = measure([])
    if i_in is None or i_in < -60 or lufs - i_in <= 0.5:  # 조용한 영상 · 이미 충분히 크면 loudnorm 만
        return []
    gain = min(lufs - i_in, 24.0)
    for _ in range(3):  # 리미터가 눌러 모자란 만큼 더 (공 차는 소리가 잦은 짧은 영상은 두 번으로 모자라 -15.7 LUFS · 판정 round6 U3)
        i2 = measure(chain(gain))
        if i2 is None or lufs - i2 <= 0.4 or gain >= 30.0:
            break
        gain = min(gain + (lufs - i2) * 1.15, 30.0)
    return chain(gain)


PRESETS = {
    "youtube": {"label": "유튜브 1080p", "long": (1920, 1080), "shorts": (1080, 1920), "crf": 19, "preset": "veryfast"},
    "hq": {"label": "고화질 4K", "long": (3840, 2160), "shorts": (2160, 3840), "crf": 18, "preset": "veryfast"},
    "small": {"label": "가벼운 720p", "long": (1280, 720), "shorts": (720, 1280), "crf": 23, "preset": "veryfast"},
}


def _is_render_tmp(d):
    """내보내기·러프컷이 쓰던 임시 폴더인지 (이름 + 안의 파일로 확인 → 사용자 폴더는 건드리지 않음).
    제자리로 못 옮겨 남겨 둔 결과(표시·옮길 이름 기록 · 묶기 '.render_bundle_' 포함)는 아님 → 다음에 켤 때 place_kept 가 옮김."""
    if not d.is_dir() or any((d / m).exists() for m in (KEEP_MARK, KEEP_INFO, "옮기지 못한 묶음.txt")):  # 마지막 = bundle.KEEP_MARK
        return False
    if d.name.startswith(".render_"):
        return True
    if not re.fullmatch(r"tmp[a-z0-9_]{8}", d.name):
        return False
    try:
        names = [x.name for x in d.iterdir()]
    except OSError:
        return False
    return any(x in ("list.txt", "subs.ass", "dialog.f32", "mix.f32", "music.f32") or re.fullmatch(r"(seg\d{4}|p\d{3})\.mp4", x) for x in names)


KEEP_PREFIX = "내보낸 영상_옮기지 못함_"  # 완성본을 제자리로 못 옮기면 임시 폴더를 이 이름으로 (정리 대상 아님 · 사용자에게 보임)
KEEP_MARK = "옮기지 못한 완성본.txt"  # 폴더 이름도 못 바꿨으면 이 표시를 넣음 → 정리(sweep_temp)에서 빼고 지우지 않음
KEEP_INFO = ".옮길 이름.json"  # 그 폴더 안: 완성본 파일 이름·옮길 이름 → 다음에 켤 때 완성본 폴더로 옮김 (place_kept)


def _strip_dir(d, keep=()):
    """우리 임시 폴더(렌더·묶기)에서 keep 말고 모두 지움 (구간 영상·소리 버퍼·자막·글꼴 — 실패 한 번에 GB 단위가 남지 않게).
    잠겨서 못 지운 것은 그대로 (다음에 켤 때 place_kept 가 다시)."""
    try:
        items = list(d.iterdir())
    except OSError:
        return
    for x in items:
        if x.name in keep:
            continue
        try:
            if x.is_dir() and not x.is_symlink():
                shutil.rmtree(x, ignore_errors=True)
            else:
                x.unlink()
        except OSError:
            pass


def _free_path(p):
    """같은 이름 파일이 있으면 ' (2)'·' (3)'… (덮어쓰지 않게)."""
    if not p.exists():
        return p
    for i in range(2, 1000):
        alt = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not alt.exists():
            return alt
    return p.with_name(f"{p.stem}_{uuid.uuid4().hex[:6]}{p.suffix}")


def kept_dirs(prefix=KEEP_PREFIX):
    """완성본 폴더 안에서 제자리로 못 옮긴 결과가 든 우리 폴더들 (이름 또는 표시로 · 표시도 못 썼으면 렌더 임시 폴더의 옮길 이름 기록)."""
    def ours(d):
        return d.name.startswith(prefix) or (d / KEEP_MARK).exists() or (
            d.name.startswith(".render_") and not d.name.startswith(".render_bundle_") and (d / KEEP_INFO).exists())  # 묶기 폴더는 bundle.place_kept
    try:
        return sorted(d for d in core.OUT.iterdir() if d.is_dir() and ours(d))
    except OSError:
        return []


def _keep_info(d):
    try:
        info = json.loads((d / KEEP_INFO).read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


def place_kept(log=print):
    """지난번에 제자리로 못 옮긴 완성본 → 완성본 폴더로 옮기고 그 임시 폴더를 지움 (앱을 켤 때 한 번 · 백신 검사는 끝났을 때).
    옮길 이름은 그때 기록(KEEP_INFO) · 예전 판이 남긴 폴더(기록 없음)는 '내보낸 영상_<그때 시각>.mp4'. 같은 이름이 있으면 (2)…
    그래도 못 옮기면 그대로 두고 다음에 다시. → 옮긴 파일 이름 목록."""
    moved = []
    for d in kept_dirs():
        info = _keep_info(d)
        src = d / Path(str(info.get("file") or "final.mp4")).name
        if not src.is_file():  # 사용자가 이미 꺼내 감 → 우리 임시 파일만 지우고(다른 mp4 는 그대로) 비었으면 폴더도
            _strip_dir(d, keep={x.name for x in d.glob("*.mp4") if not _RENDER_PART.fullmatch(x.name)})
            _rmdir(d)
            continue
        stamp = d.name[len(KEEP_PREFIX):] if d.name.startswith(KEEP_PREFIX) else time.strftime("%Y%m%d_%H%M%S", time.localtime(src.stat().st_mtime))
        want = Path(str(info.get("out") or "")).name
        if not want.lower().endswith(".mp4") or re.search(r'[\\/:*?"<>|\x00-\x1f]', want):
            want = f"내보낸 영상_{stamp}.mp4"
        dst = _free_path(core.OUT / want)
        try:
            updater.replace_retry(src, dst, updater.REPLACE_SECS)
        except OSError as e:
            log(f"지난번에 옮기지 못한 완성본을 아직 옮기지 못했어요 · '{src}' · {e}")
            continue
        _strip_dir(d)
        _rmdir(d)
        moved.append(dst.name)
        log(f"지난번에 옮기지 못한 완성본을 완성본 폴더로 옮겼어요 · {dst.name}")
    return moved


def _rmdir(d):
    try:
        d.rmdir()
    except OSError:
        pass


_RENDER_PART = re.compile(r"(seg\d{4}|p\d{3}|part\d*)\.mp4", re.I)  # 렌더·묶기 중간 영상 (완성본 아님)


class KeptFinal(RuntimeError):
    """완성본을 만들었지만 제자리로 못 옮김 (임시 폴더에 그대로 남겨 두고 위치를 알려 줌)."""


def _place_final(src, out, log):
    """완성본(src)을 out 으로 → 실제로 둔 경로.
    막 만든 큰 mp4 는 백신(Defender·V3·알약)이 한동안 잡고 검사함 → 최대 1분 기다리며 다시 (예전에는 0.25초 뒤 포기하고 임시 폴더째
    지워서 렌더링 전체를 잃었음). 같은 이름 파일이 다른 프로그램에 열려 있을 때(대상 쪽 문제)만 ' (2)' 같은 다른 이름으로.
    그래도 못 옮기면 완성본을 지우지 않고 그 폴더를 보이는 이름으로 바꿔 KeptFinal 로 위치를 알려 줌."""
    try:
        updater.replace_retry(src, out, updater.SETTLE_SECS)
        return out
    except OSError as e:
        err = e
    if out.exists():  # 대상 쪽: 같은 이름 파일이 열려 있음 → 다른 이름으로라도 꼭 남김
        for i in range(2, 100):
            alt = out.with_name(f"{out.stem} ({i}){out.suffix}")
            if alt.exists():
                continue
            try:
                updater.replace_retry(src, alt)
            except OSError as e:
                err = e
                break
            log(f"  같은 이름 파일이 다른 프로그램에 열려 있어서 '{alt.name}'(으)로 저장했어요")
            return alt
    log(f"  완성본을 옮기지 못했어요 · {err}")
    d = src.parent
    _strip_dir(d, keep=(src.name,))  # 완성본만 남김 (예전: 구간 영상·소리 버퍼까지 통째로 남아 2.7배 · 50분 편집본이면 약 3.5GB)
    try:  # 다음에 켤 때 이 이름으로 완성본 폴더에 옮김 (place_kept)
        updater.write_atomic(d / KEEP_INFO, json.dumps({"file": src.name, "out": out.name}, ensure_ascii=False))
    except OSError:
        pass
    keep = d.with_name(KEEP_PREFIX + time.strftime("%Y%m%d_%H%M%S"))
    try:
        os.replace(d, keep)
        d = keep
    except OSError:
        try:
            (d / KEEP_MARK).write_text("완성본을 옮기지 못했어요. 이 폴더의 final.mp4 가 완성본이에요.", encoding="utf-8")
        except OSError:
            pass
    raise KeptFinal(f"완성본은 만들었지만 제자리로 옮기지 못했어요 (백신이 검사 중일 수 있어요). '{d / src.name}' 에 그대로 있어요 · "
                    f"다음에 앱을 켜면 완성본 폴더로 옮겨 드려요")


def sweep_temp(min_age=600):
    """멈췄거나 앱이 갑자기 꺼져 남은 임시 폴더 정리 (완성본 폴더 안)."""
    now = time.time()
    try:
        ds = list(core.OUT.iterdir())
    except OSError:
        return
    for d in ds:
        try:
            if _is_render_tmp(d) and now - d.stat().st_mtime > min_age:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _missing_media(proj, media, t_lo, t_hi):
    """내보낼 구간에 쓰이는데 파일이 없는 미디어 이름들."""
    tracks = {t["id"]: t for t in proj["tracks"]}
    solo = any(t.get("solo") for t in tracks.values() if t["k"] == "a")
    out = set()
    for it in proj["items"]:
        if it["start"] >= t_hi or i_end(it) <= t_lo:
            continue
        tr = tracks.get(it["track"]) or {}
        md = media.get(it["media"])
        if not md:
            continue
        if tr.get("k") == "v" and tr.get("hide"):
            continue
        if tr.get("k") == "a" and (tr.get("mute") or it.get("mute") or not md.get("audio") or (solo and not tr.get("solo"))):
            continue
        try:
            ok = media_path(md["file"], md.get("src", "videos")).exists()
        except ValueError:
            ok = False
        if not ok:
            out.add(md["file"])
    return sorted(out)


EXPORT_META = {}  # 완성본 파일 이름 → 만들 때 설정 (자동 검수가 씀)


def _tlabel(t):
    return f"{int(t // 60)}m{int(t % 60):02d}s"


def _disk_need(span, W, H, proj):
    """내보내기에 필요한 임시·결과 디스크 크기 어림(바이트): 소리 버퍼(48kHz 스테레오 float · 대사, 보정한 대사, 배경음악) +
    화면(구간 파일 + 이어 붙인 결과 · 해상도에 맞춘 넉넉한 비트레이트) + 여유 200MB."""
    n = span * SR * 2 * 4
    music = any(t.get("role") == "music" for t in proj.get("tracks") or [] if t.get("k") == "a")
    bufs = 1 + (1 if voice_filters(proj.get("voice")) else 0) + (1 if music else 0)
    vbps = 2.2e6 * (W * H) / (1920 * 1080)   # 바이트/초
    return int(n * bufs + span * vbps * 2 + 200e6)


def _check_disk(span, W, H, proj):
    """디스크가 모자라면 내보내기 전에 쉬운 안내 (예전에는 한참 만들다 '[Errno 28]'·'[WinError 112]'로 실패)."""
    need = _disk_need(span, W, H, proj)
    try:
        free = shutil.disk_usage(core.OUT).free
    except OSError:
        return
    if free < need:
        raise RuntimeError(f"디스크 공간이 모자라요 · 이 영상을 내보내려면 약 {need / 1e9:.1f}GB 가 필요한데 {free / 1e9:.1f}GB 남았어요 "
                           "(작업 폴더가 있는 드라이브에서 필요 없는 파일을 지워 주세요)")


def _export_prep(name, proj, opts, log):
    """내보내기·지금 화면 저장(D-349)이 함께 쓰는 준비 → (편집본, 형식, 자막·도형 기준 크기 BW·BH, 출력 크기 W·H, fps, 미디어, 설정)."""
    proj = migrate_seq(dict(proj))
    fmt = proj.get("format", "shorts")
    BW, BH = (1080, 1920) if fmt == "shorts" else (1920, 1080)  # 자막·도형 기준 해상도
    pr = PRESETS.get(opts.get("preset") or "youtube", PRESETS["youtube"])
    W, H = pr["shorts" if fmt == "shorts" else "long"]
    fps = int(opts.get("fps") or 30)
    media = {m["id"]: m for m in proj.get("media") or []}
    if "main" not in media:
        i = proj["info"]
        media["main"] = {"id": "main", "kind": "video", "src": "videos", "file": proj.get("source") or name, "dur": i["duration"],
                         "w": i["width"], "h": i["height"], "fps": i.get("fps", 30.0), "audio": True}
    if media["main"].get("audio") and media["main"].get("src", "videos") == "videos" and not _has_audio(media["main"]["file"]):
        # 소리 줄기가 없는 원본(화면 녹화 등): 대사 클립을 읽지 않음 — 예전 프로젝트는 늘 '소리 있음'으로 적혀 있어 내보내기가 실패했음
        media["main"] = dict(media["main"], audio=False)
    proj["media"] = [dict(m) for m in media.values()]
    media = {m["id"]: m for m in proj["media"]}
    proj["items"] = [dict(it) for it in proj["items"]]
    refresh_sfx_lengths([proj["items"]], proj["media"])   # 예전 편집본도 다시 만든 효과음을 제 길이로 (D-379)
    bad = [it for it in proj["items"] if it.get("media") not in media or it["out"] - it["in"] <= 1e-3]
    if bad:  # 프로젝트에 없는 미디어·길이가 0인 클립은 빼고 진행
        log(f"  빈 클립 {len(bad)}개는 건너뛰어요")
        drop = {id(x) for x in bad}
        proj["items"] = [it for it in proj["items"] if id(it) not in drop]
    return proj, fmt, BW, BH, W, H, fps, media, pr


def _export_stem(name, proj, fmt):
    """완성본 이름 앞부분 '<영상>_<편집본 이름>'."""
    label = re.sub(r'[\\/:*?"<>|]', "", proj.get("name") or ("쇼츠" if fmt == "shorts" else "롱폼")).strip(" .")
    return f"{core.adir(name).name}_{label}"


def export(name, proj, opts, log):
    """편집본 내보내기 — opts: video(mp4) · srt(+ srtTracks 넣을 자막 트랙 · srtSplit 트랙마다 따로 · D-346) · xml(프리미어 XML + 빠진 것 목록 · D-347) ·
    overlay(글자·도형만 투명 영상 · D-348) · preset · fps · hw · range([시작, 끝] 초)."""
    CANCEL.clear()
    proj, fmt, BW, BH, W, H, fps, media, pr = _export_prep(name, proj, opts, log)
    try:  # 시리즈 이름표 번호: 처음 내보낼 때 정하고 다시 내보내면 그대로 (BR-245 · 편집실에서 시킨 것은 app 이 이미 정함 · 같은 번호)
        proj["titles"] = [dict(t) for t in proj.get("titles") or []]
        proj["shapes"] = [dict(g) for g in proj.get("shapes") or []]
        series_apply(name, proj)
    except OSError as e:
        log(f"  시리즈 번호를 저장하지 못해서 이름표 글 그대로 내보내요 · {e}")
    if skin_on(proj) and not skin_path(proj["layout"]["skin"]):   # 스킨 그림이 없어졌으면 스킨 없이 (멈추지 않음 · BR-320)
        log("  스킨 그림을 찾지 못해서 스킨 없이 내보내요 · 스타일 화면에서 스킨을 다시 골라 편집실 템플릿 '풋살사관학교 스킨'을 눌러 주세요")
    stem = _export_stem(name, proj, fmt)
    outputs = []
    total = seq_total(proj)
    t_lo, t_hi = 0.0, total
    rng = opts.get("range")
    if rng and rng[1] - rng[0] > 0.1:
        t_lo, t_hi = max(0.0, float(rng[0])), min(total, float(rng[1]))
        stem += f"_구간_{_tlabel(t_lo)}-{_tlabel(t_hi)}"
    if t_hi - t_lo < 0.05:
        raise RuntimeError("내보낼 내용이 없어요 (타임라인이 비어 있어요)")
    want_v, want_srt, want_xml, want_ov = opts.get("video", True), opts.get("srt", True), opts.get("xml", True), bool(opts.get("overlay"))
    # 자막 파일(SRT)은 '영상에 자막 넣기'와 상관없이 만듦 (유튜브 자막 올리기용) · 숨긴 트랙은 빠짐 · 구간 내보내기면 구간 안으로 잘라 0초부터
    srt_tr = opts.get("srtTracks") if isinstance(opts.get("srtTracks"), list) else None
    srts = srt_files(proj, "", t_lo, t_hi, fps, srt_tr, bool(opts.get("srtSplit"))) if want_srt else []
    # 같은 이름이 있으면 덮어쓰지 않고 (2), (3)… 을 붙임 (이번에 만들 파일 이름 모두로 봄)
    skin_png = skin_path(proj["layout"]["skin"]) if want_xml and skin_on(proj) else None   # 프리미어에 직접 얹을 스킨 그림 (XML 옆에 복사 · D-536)
    exts = ([".mp4"] if want_v else []) + ([fn for fn, _, _ in srts] or [".srt"] if want_srt else []) + \
        (["_premiere.xml", REPORT_TAG + ".txt"] if want_xml else []) + ([OVERLAY_TAG + ".mov", OVERLAY_TAG + "_png.zip"] if want_ov else []) + \
        ([SKIN_TAG] if skin_png else [])
    base, k = stem, 1
    while any((core.OUT / f"{stem}{e}").exists() for e in exts):
        k += 1
        stem = f"{base} ({k})"
    if want_v:
        miss = _missing_media(proj, media, t_lo, t_hi)
        if miss:
            raise RuntimeError("미디어 파일이 없어요 · " + ", ".join(miss[:5]) + " (옮기거나 지웠다면 미디어 탭에서 다시 넣어 주세요)")
    span = t_hi - t_lo
    side = [(stem + fn, txt) for fn, txt, _ in srts]  # (파일 이름, 내용) — 영상을 만들면 영상이 다 된 뒤에 씀 (멈추거나 실패하면 짝 잃은 자막·XML 이 안 남음)
    if want_srt and not srts:
        log("  자막이 없어서 SRT 파일은 만들지 않았어요")

    def write_side(ov=None, ov_note=None):
        files = list(side)
        if want_xml:  # XML 은 투명 영상을 만든 뒤 (맨 위 트랙에 걸 파일 이름이 정해진 뒤)
            rep = {"W": BW, "H": BH, "missing_media": [] if want_v else _missing_media(proj, media, t_lo, t_hi)}
            if skin_png:   # 스킨 그림을 XML 옆에 '<이름>_스킨.png' 로 (빠진 것 목록이 그 파일을 가리킴 · 못 넣으면 예전 안내 · D-536)
                try:
                    shutil.copyfile(skin_png, core.OUT / f"{stem}{SKIN_TAG}")
                    outputs.append(f"{stem}{SKIN_TAG}")
                    rep["skinFile"] = f"{stem}{SKIN_TAG}"
                except OSError as e:
                    log(f"  스킨 그림을 프리미어 파일 옆에 넣지 못했어요 · {e}")
            files.append((f"{stem}_premiere.xml", _xmeml(proj, BW, BH, fps, stem, t_lo, t_hi, overlay=ov, report=rep)))
            files.append((f"{stem}{REPORT_TAG}.txt", _xml_report(proj, rep, stem, fps, t_lo, t_hi, ov, ov_note, [fn for fn, _ in side])))
        for fn, txt in files:  # 자막(.srt)·빠진 것 목록(.txt)은 BOM 을 붙임 (프리미어·메모장·자막 프로그램이 한글을 안 깨지게 읽음 · core.SRT_ENCODING)
            updater.write_atomic(core.OUT / fn, txt, encoding=core.SRT_ENCODING if fn.endswith((".srt", ".txt")) else "utf-8")
            outputs.append(fn)

    def overlay_step():
        """글자·도형만 투명 영상 → (파일 경로 또는 None, 못 만든 까닭) · 멈추면 내보내기도 멈춤."""
        if not want_ov:
            return None, None
        try:
            ov, note = _overlay_export(name, proj, BW, BH, fps, t_lo, t_hi, stem, log)
        except Cancelled:
            log("  내보내기를 멈췄어요")
            raise RuntimeError("내보내기를 멈췄어요")
        if ov:
            outputs.append(ov.name)
        return (ov if ov and ov.suffix == ".mov" else None), note

    if not want_v:
        write_side(*overlay_step())
    else:
        sweep_temp(0)
        _check_disk(span, W, H, proj)
        tmp = Path(tempfile.mkdtemp(prefix=".render_", dir=core.OUT))
        t_start = time.time()
        astate = {"frac": 0.0}
        abort_a = threading.Event()
        ex_a, fut, err = None, None, None

        def prog(pct, detail):
            el = time.time() - t_start
            eta = el / pct * (100 - pct) if pct > 2 else None
            core.set_progress(label="내보내는 중", item=name, step="", pct=int(pct), detail=detail,
                              eta=int(eta) if eta is not None else None)

        try:
            trans = valid_trans(proj)
            _write_subs(tmp, proj, BW, BH, fps)
            segs = _segments(proj, t_lo, t_hi, fps, trans, media)
            tot_f = sum(b - a for a, b in segs) or 1
            done = {}
            lock = threading.Lock()
            prog(1, "준비 중")

            def audio_job():
                """소리: 섞기 → 소리 크기 맞추기 → AAC (영상 화면을 만드는 동안 같이 진행)."""
                mix, peak = _mix_audio(proj, media, t_lo, t_hi, tmp, trans, lambda x: astate.update(frac=0.4 * x), abort_a)
                m = proj.get("master") or {}
                norm = m.get("normalize", True)
                if norm and span < 3.05 and peak < 1e-4:  # 3초 안 되는 무음은 소리 크기 맞추기가 오류 → 건너뜀
                    norm = False

                pre = {}

                def enc(nm, tp=LOUD_TP):
                    af = [f"volume={float(m.get('volume', 1.0)):.3f}"]
                    if nm:
                        lufs = min(-9.0, max(-24.0, float(m.get("lufs") or -14.0)))
                        if "chain" not in pre:  # (다시 만들 때는 잰 값 그대로)
                            pre["chain"] = _loud_pre(mix, tmp, lufs, float(m.get("volume", 1.0)), abort_a)
                        af += pre["chain"]
                        if pre["chain"] and "meas" not in pre:
                            pre["meas"] = _loud_measure(mix, tmp, af, lufs, abort_a)
                        ms = pre.get("meas")
                        if ms:  # 두 번 재기(선형): 한 번 재기 loudnorm 은 짧은 영상에서 끝까지 덜 키워 -15.9 LUFS (판정 round6 U3 · 쇼츠)
                            af.append(f"loudnorm=I={lufs:.1f}:TP={tp:.2f}:LRA=11:measured_I={ms['input_i']}:measured_TP={ms['input_tp']}:"
                                      f"measured_LRA={ms['input_lra']}:measured_thresh={ms['input_thresh']}:offset={ms['target_offset']}:linear=true")
                        else:
                            af.append(f"loudnorm=I={lufs:.1f}:TP={tp:.2f}:LRA=11")
                    af += ["aresample=48000", "asetpts=N/SR/TB"]  # 소리 크기 맞추기 뒤 시각을 다시 매겨 끝이 잘리거나 길어지지 않게
                    _run_ff(["-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", mix.name, "-af", ",".join(af), "-c:a", "aac", "-b:a", "192k",
                             "-ar", "48000", "audio.m4a"], tmp, on_time=lambda t: astate.update(frac=0.4 + 0.6 * min(1.0, t / max(0.1, span))),
                            abort=abort_a)
                    astate["norm"] = bool(nm)  # 실제로 소리 크기를 맞췄는지 (검수가 이 기준으로 봄)
                try:
                    enc(norm)
                except RuntimeError:
                    if not norm or CANCEL.is_set() or abort_a.is_set():
                        raise
                    log("  소리 크기 맞추기를 건너뛰고 다시 만들어요")
                    enc(False)
                    return tmp / "audio.m4a"
                if norm:
                    # AAC 로 줄이면 순간 최대가 커짐 (공 차는 소리·효과음처럼 아주 짧은 소리는 1.5dB 까지 · 판정: -0.4 dBTP) →
                    # 줄인 소리를 재서 넘으면 그만큼 더 낮춘 최대로 한 번 더 (loudnorm 이 순간만 눌러 평균 크기는 그대로) ·
                    # 다시 만들기가 실패하면 처음 맞춘 소리를 그대로 씀 (예전에는 소리 크기를 안 맞춘 소리로 덮어씀)
                    tp = _true_peak(tmp / "audio.m4a", tmp, abort_a)
                    if tp is not None and tp > LOUD_TP_OUT:
                        os.replace(tmp / "audio.m4a", tmp / "audio_first.m4a")
                        try:
                            enc(norm, max(LOUD_TP_FLOOR, LOUD_TP - (tp - LOUD_TP_OUT) - 0.5))
                        except RuntimeError:
                            if CANCEL.is_set() or abort_a.is_set():
                                raise
                            os.replace(tmp / "audio_first.m4a", tmp / "audio.m4a")
                            astate["norm"] = True
                            log(f"  순간 최대 크기를 한 번 더 낮추지 못해 처음 맞춘 소리를 써요 ({tp:.1f} dBTP)")
                return tmp / "audio.m4a"

            ex_a = ThreadPoolExecutor(1)
            fut = ex_a.submit(audio_job)

            hw = hw_encoder() if opts.get("hw", True) else None
            if hw and (hw, W, H) in _HW.get("bad", set()):
                hw = None

            # 같은 원본에서 이어지는 짧은 구간들은 ffmpeg 하나로 (exportplan · 그림은 구간마다 만든 것과 같음)
            if hwdec.will_probe() and any(media_hdr(m) for m in media.values()):
                prog(2, "준비 중 · 그래픽카드로 영상 풀기 확인 중")
            builds = [_build_segment(proj, media, W, H, fps, f0, f1, trans, tmp, k) for k, (f0, f1) in enumerate(segs)]
            units = exportplan.units(builds, tmp, fps)

            def render(k, enc, abort, procs):
                if abort.is_set():
                    return
                args, final, n, fcf = exportplan.unit_args(units[k], builds, tmp, fps)

                def on_frame(fr):
                    with lock:
                        done[k] = min(n, fr)
                        prog(2 + 85 * sum(done.values()) / tot_f, f"화면 만드는 중 · 구간 {k + 1}/{len(units)}" + (" · 그래픽카드" if enc else ""))

                def go(a):
                    _run_ff(["-y", "-v", "error"] + a + [_fc_opt(), fcf, "-map", f"[{final}]", "-an", "-frames:v", str(n)] + _venc(enc, pr, W, H, fps)
                            + ["-r", str(fps), "-video_track_timescale", str(fps * 1000), f"seg{k:04d}.mp4"], tmp, on_frame,
                            procs=procs, abort=abort, enc=enc)
                if hwdec.is_bad():  # 앞 구간에서 그래픽카드 풀기가 실패했으면 처음부터 일반 방식
                    args = hwdec.strip(args)
                try:
                    go(args)
                except HwEncError:  # 그래픽카드 '인코더' 문제는 풀기 탓이 아님 → 원래 처리(하나씩·일반 인코딩)로
                    raise
                except RuntimeError:
                    if "-hwaccel" not in args or CANCEL.is_set() or abort.is_set():
                        raise
                    hwdec.mark_bad()  # 그래픽카드로 풀기가 안 됨 → 이번 실행 동안은 일반 방식 (이 구간도 다시)
                    log("  그래픽카드로 영상 풀기가 안 돼서 일반 방식으로 다시 만들어요")
                    go(hwdec.strip(args))
                on_frame(n)

            def render_all(enc, workers=None):
                """구간들을 동시에 만들고, 하나라도 실패하면 나머지를 바로 멈춤 (다 기다리지 않음)."""
                done.clear()
                if workers is None:
                    workers = (3 if enc else 2) if (os.cpu_count() or 2) >= 4 and len(units) > 1 else 1
                    workers = _mem_workers(workers, media, W, H, log)
                abort, procs = threading.Event(), set()
                ex = ThreadPoolExecutor(workers)
                try:
                    futs = [ex.submit(render, k, enc, abort, procs) for k in range(len(units))]
                    wait(futs, return_when=FIRST_EXCEPTION)
                    errs = [f.exception() for f in futs if f.done() and f.exception() is not None]
                    if errs:
                        abort.set()
                        for p in list(procs):
                            try:
                                p.kill()
                            except OSError:
                                pass
                        real = [e for e in errs if not isinstance(e, Cancelled)]
                        raise (real[0] if real else errs[0])
                finally:
                    ex.shutdown(wait=True, cancel_futures=True)

            def to_cpu():
                bad = _HW.setdefault("bad", set())
                bad.add((hw, W, H))  # 이 크기에서만 이번 실행 동안 그래픽카드를 안 씀
                log(f"  그래픽카드 인코딩({hw})이 안 돼서 일반 방식으로 다시 만들어요")
                for f in tmp.glob("seg*.mp4"):
                    f.unlink(missing_ok=True)
                try:
                    render_all(None)
                except Cancelled:
                    raise
                except Exception:
                    bad.discard((hw, W, H))  # 일반 방식도 안 되면 그래픽카드 탓이 아니었음 → 다음엔 다시 그래픽카드로
                    raise

            try:
                render_all(hw)
            except HwEncError as e:
                if CANCEL.is_set():
                    raise Cancelled()
                if getattr(e, "session", False) and len(units) > 1:  # 동시에 여는 개수 제한 → 하나씩 다시
                    log(f"  그래픽카드({hw})로 한 번에 하나씩 다시 만들어요")
                    try:
                        render_all(hw, 1)
                    except HwEncError:
                        to_cpu()
                else:
                    to_cpu()
            except RuntimeError:
                # 처음 보는 오류: 그래픽카드가 이 크기를 못 하는 것인지 짧게 확인 → 그렇다면 일반 방식, 아니면 그대로 오류
                if not hw or CANCEL.is_set() or _hw_works(hw, pr, W, H, fps):
                    raise
                to_cpu()
            (tmp / "list.txt").write_text("".join(f"file 'seg{k:04d}.mp4'\n" for k in range(len(units))), encoding="utf-8")
            # 영상 화면이 다 되면 소리 마무리를 기다림 (wait 로 확인 → 파이썬 3.9·3.10 에서도 시간 초과 예외가 안 남)
            while not wait([fut], timeout=0.4).done:
                prog(87 + 11 * astate["frac"], "소리 마무리 중 · 소리 크기 맞추는 중")
            audio = fut.result()
            prog(98, "마무리 중")
            # 영상·소리를 그대로 합치기만 (다시 인코딩 없음)
            _run_ff(["-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-i", audio.name, "-map", "0:v", "-map", "1:a", "-c", "copy",
                     "-movflags", "+faststart", "final.mp4"], tmp)
            try:  # 다 만들어진 뒤에만 완성본 폴더로 (멈추면 반쪽 파일이 안 남음)
                out = _place_final(tmp / "final.mp4", core.OUT / f"{stem}.mp4", log)
            except KeptFinal:
                tmp = None  # 옮기지 못한 완성본이 든 폴더는 지우지 않음
                raise
            outputs.insert(0, out.name)
            m = proj.get("master") or {}  # 검수용: 이 파일을 만들 때의 형식·소리 크기 (편집실을 새로 고쳐도 남음)
            EXPORT_META[out.name] = {"format": fmt, "master": {"normalize": astate.get("norm", False),
                                                               "lufs": min(-9.0, max(-24.0, float(m.get("lufs") or -14.0)))}}
            if isinstance(proj.get("msg"), dict):  # MSG 편집본: 무엇을 넣었는지 (검수·평가가 씀)
                EXPORT_META[out.name]["msg"] = (proj["msg"].get("summary") or {}).get("text")
            log(f"  영상 길이 {span:.1f}초 · {W}×{H} · {fps}fps · {time.time() - t_start:.0f}초 걸림" + (f" · 그래픽카드({hw})" if hw and (hw, W, H) not in _HW.get("bad", set()) else ""))
        except Cancelled as e:
            traceback.clear_frames(e.__traceback__)
            log("  내보내기를 멈췄어요")
            err = "내보내기를 멈췄어요"
        except Exception as e:  # noqa: BLE001 — 열린 임시 파일을 놓기 위해 오류 정보만 남기고 정리
            if not isinstance(e, RuntimeError):
                studiolog.trace(e)  # 오류 위치를 studio.log 에 (pythonw 는 콘솔이 없음)
            traceback.clear_frames(e.__traceback__)
            err = str(e) if isinstance(e, KeptFinal) else f"영상을 만들지 못했어요 · {str(e)[-300:]}"
        finally:
            if fut is not None:
                if not fut.done():  # 실패·멈춤이면 소리 쪽도 바로 멈춤
                    abort_a.set()
                    for p in list(_PROCS):
                        try:
                            p.kill()
                        except OSError:
                            pass
                try:
                    fut.result(timeout=60)
                except BaseException as e:  # noqa: BLE001
                    traceback.clear_frames(e.__traceback__)
                ex_a.shutdown(wait=True)
            for _ in range(5 if tmp else 0):  # Windows: 잠깐 잡혀 있으면 조금 뒤 다시
                shutil.rmtree(tmp, ignore_errors=True)
                if not tmp.exists():
                    break
                time.sleep(0.3)
        if err:
            raise RuntimeError(err)
        write_side(*overlay_step())
    log(f"  내보내기 완료 · {', '.join(outputs)}")
    return outputs


# ---------- 글자·도형만 투명 영상 (D-348) · 지금 화면 그림으로 저장 (D-349) ----------

OVERLAY_BPS = 12e6  # 글자·도형 투명 영상(ProRes 4444) 어림 크기(바이트/초 · 1080×1920 · 글자가 많은 쇼츠에서 잰 54Mbps ≈ 6.8MB/초의 두 배쯤 여유)
OVERLAY_CODECS = ("prores_ks", "prores_aw")  # 먼저 prores_ks (알파 표준 구현) · 안 되면 prores_aw (2.5배쯤 빠르고 알파 같음 · I-342) · 둘 다 안 되면 PNG


def _overlay_graph(BW, BH, fps, f0, n):
    """자막·타이틀·도형·전술 그림만 투명 바탕으로 그리는 필터 — 내보내기와 같은 ASS·같은 프레임 시각(settb·setpts+f0 · _build_segment 끝과 같음).
    알파는 검정·흰 바탕에 한 번씩 그린 두 화면의 차(1 − (흰 − 검정))로 구함: subtitles 필터의 alpha 처리는 반투명(도형 불투명도·글 상자·
    나타나기 효과)이 두 번 곱해져 흐려짐 → 검정 바탕 그림(= 알파를 곱한 색)을 알파로 나눠 곱하기 전 색(straight)으로."""
    src = (lambda c: f"color=c={c}:s={BW}x{BH}:r={fps}:d={n / fps + 1:.3f},format=rgb24,trim=end_frame={n},"
                     f"settb=1/{fps},setpts=PTS+{int(f0)},subtitles=subs.ass:fontsdir=fonts,setpts=PTS-STARTPTS")
    return (f"{src('black')}[b];\n{src('white')}[w];\n[b]split[b1][b2];\n[w]extractplanes=g[wg];\n[b1]extractplanes=g[bg];\n"
            "[wg][bg]lut2=c0='clip(255-(x-y),0,255)'[a];\n[b2][a]alphamerge,unpremultiply=inplace=1")


def _overlay_export(name, proj, BW, BH, fps, t_lo, t_hi, stem, log):
    """글자·도형만 투명 영상 (프리미어 맨 위 트랙용 · D-348) → (완성본 폴더의 파일, 못 만들었을 때 까닭).
    ProRes 4444 (.mov · yuva444p10le · BT.709) — 그 인코더가 안 되면 PNG 연속 그림 묶음(.zip · 그림마다 알파) · 둘 다 안 되면 (None, 까닭)."""
    f0, f1 = int(round(t_lo * fps)), int(round(t_hi * fps))
    n = max(1, f1 - f0)
    try:
        free = shutil.disk_usage(core.OUT).free
    except OSError:
        free = None
    need = int(n / fps * OVERLAY_BPS + 100e6)
    if free is not None and free < need:
        note = f"디스크 공간이 모자라서 글자·도형 투명 영상은 만들지 않았어요 (약 {need / 1e9:.1f}GB 필요)"
        log("  " + note)
        return None, note
    tmp = Path(tempfile.mkdtemp(prefix=".render_", dir=core.OUT))
    t_start = time.time()

    def prog(fr, what):
        pct = 100 * min(1.0, fr / n)
        el = time.time() - t_start
        core.set_progress(label="내보내는 중", item=name, step="", pct=int(pct), detail=f"{what} · {fr}/{n}",
                          eta=int(el / pct * (100 - pct)) if pct > 2 else None)

    try:
        _write_subs(tmp, proj, BW, BH, fps)
        g = _overlay_graph(BW, BH, fps, f0, n)
        (tmp / "ov.txt").write_text(g + ",scale=out_color_matrix=bt709:out_range=tv,format=yuva444p10le[v]", encoding="utf-8")
        src, out, why = None, None, ""
        for codec in OVERLAY_CODECS:
            try:
                prog(0, "글자·도형 투명 영상 만드는 중")
                _run_ff(["-y", "-v", "error", _fc_opt(), "ov.txt", "-map", "[v]", "-frames:v", str(n), "-c:v", codec, "-profile:v",
                         "4444" if codec == "prores_ks" else "4", "-pix_fmt", "yuva444p10le", *(["-alpha_bits", "16"] if codec == "prores_ks" else []),
                         "-vendor", "apl0", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-r", str(fps),
                         "-video_track_timescale", str(fps * 1000), "ov.mov"], tmp, lambda fr: prog(fr, "글자·도형 투명 영상 만드는 중"))
                src, out = tmp / "ov.mov", core.OUT / f"{stem}{OVERLAY_TAG}.mov"
                break
            except Cancelled:
                raise
            except RuntimeError as e:
                why = str(e)[-160:].strip()
                log(f"  ProRes 4444({codec})로 못 만들었어요 · {why}")
        if src is None:  # ProRes 인코더가 없거나 실패 → PNG 연속 그림 (그림마다 알파 · 프리미어 '이미지 시퀀스'로 가져옴)
            log("  PNG 연속 그림으로 만들어요")
            (tmp / "png").mkdir()
            (tmp / "ovp.txt").write_text(g + ",format=rgba[v]", encoding="utf-8")
            _run_ff(["-y", "-v", "error", _fc_opt(), "ovp.txt", "-map", "[v]", "-frames:v", str(n), "-r", str(fps), "-start_number", "0", "png/f_%06d.png"], tmp,
                    lambda fr: prog(fr, "글자·도형 PNG 연속 그림 만드는 중"))
            import zipfile
            src, out = tmp / "ov.zip", core.OUT / f"{stem}{OVERLAY_TAG}_png.zip"
            tag = OVERLAY_TAG.lstrip("_")
            with zipfile.ZipFile(src, "w", zipfile.ZIP_STORED) as z:  # PNG 는 이미 줄인 것 → 그대로 담음
                for p in sorted((tmp / "png").glob("f_*.png")):
                    z.write(p, f"{tag}_{p.stem[2:]}.png")
        try:
            placed = _place_final(src, out, log)
        except KeptFinal as e:
            tmp = None  # 옮기지 못한 파일이 든 폴더는 지우지 않음 (다음에 켤 때 옮김)
            return None, str(e)
        log(f"  글자·도형 투명 영상 · {placed.name} · {time.time() - t_start:.0f}초 걸림")
        return placed, None if placed.suffix == ".mov" else "PNG 연속 그림 묶음(.zip)으로 만들었어요 · 풀어서 첫 그림을 '이미지 시퀀스'로 가져오세요"
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001 — 완성 영상·자막·XML 은 그대로 두고 까닭만 알림
        if not isinstance(e, RuntimeError):
            studiolog.trace(e)
        note = f"글자·도형 투명 영상을 만들지 못했어요 · {str(e)[-200:].strip()}"
        log("  " + note)
        return None, note
    finally:
        for _ in range(5 if tmp else 0):
            shutil.rmtree(tmp, ignore_errors=True)
            if not tmp.exists():
                break
            time.sleep(0.3)


def _ff_once(args, cwd, timeout=180):
    """ffmpeg 한 번 (멈추기 ✕ 표시와 상관없이 · 진행률 없음) — 지금 화면 저장처럼 금방 끝나는 일. 멈추기는 _PROCS 로 끔."""
    p = core.popen([core.ffmpeg(), "-hide_banner"] + args, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _PROCS.add(p)
    try:
        _, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise RuntimeError("그림을 만드는 데 너무 오래 걸려요")
    finally:
        _PROCS.discard(p)
    if p.returncode:
        raise RuntimeError(err.decode("utf-8", "replace")[-600:])


_FRAME_LOCK = threading.Lock()


def export_frame(name, proj, t, log=print):
    """지금 화면 그림으로 저장 (프리미어 '프레임 내보내기' · Ctrl+Shift+E · 📷 · D-349) — 재생 위치 한 프레임을 자막·타이틀·도형·전술 그림까지
    내보내기와 같은 그리기(_build_segment · 같은 ASS · 한 프레임 구간)로 시퀀스 크기 그대로(쇼츠 1080×1920 · 롱폼 1920×1080) PNG 로 완성본 폴더에.
    이름: '<영상>_<편집본>_프레임 00-00-12-05.png' (같은 이름이 있으면 ' (2)' …) → {file, w, h, t}."""
    if not _FRAME_LOCK.acquire(blocking=False):
        raise RuntimeError("지금 화면을 저장하는 중이에요 · 잠시 뒤 다시 눌러 주세요")
    try:
        proj, fmt, BW, BH, _W, _H, fps, media, _pr = _export_prep(name, proj, {"preset": "youtube", "fps": 30}, log)
        total = seq_total(proj)
        if total < 0.5 / fps:
            raise RuntimeError("타임라인이 비어 있어요")
        f = min(max(0, int(round(float(t) * fps))), max(0, int(round(total * fps)) - 1))
        miss = _missing_media(proj, media, f / fps, (f + 1) / fps)
        if miss:
            raise RuntimeError("미디어 파일이 없어요 · " + ", ".join(miss[:5]) + " (옮기거나 지웠다면 미디어 탭에서 다시 넣어 주세요)")
        base = f"{_export_stem(name, proj, fmt)}_프레임 {_tc(f / fps, fps).replace(':', '-')}"
        out, k = core.OUT / f"{base}.png", 1
        while out.exists():
            k += 1
            out = core.OUT / f"{base} ({k}).png"
        core.OUT.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".render_", dir=core.OUT))
        try:
            _write_subs(tmp, proj, BW, BH, fps)
            args, final, _n = _build_segment(proj, media, BW, BH, fps, f, f + 1, valid_trans(proj), tmp, 0)
            run = lambda a: _ff_once(["-y", "-v", "error"] + a + [_fc_opt(), "fc0.txt", "-map", f"[{final}]", "-frames:v", "1", "-update", "1", "frame.png"], tmp)  # noqa: E731
            if hwdec.is_bad():
                args = hwdec.strip(args)
            try:
                run(args)
            except RuntimeError:
                if "-hwaccel" not in args:
                    raise
                hwdec.mark_bad()  # 그래픽카드로 풀기가 안 됨 → 일반 방식으로 다시
                run(hwdec.strip(args))
            updater.replace_retry(tmp / "frame.png", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        log(f"지금 화면을 그림으로 저장했어요 · {out.name}")
        return {"file": out.name, "w": BW, "h": BH, "t": round(f / fps, 6)}
    finally:
        _FRAME_LOCK.release()
