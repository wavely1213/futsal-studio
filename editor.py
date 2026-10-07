"""편집실: 영상 정보·파형·썸네일·프로젝트 저장(멀티 트랙)·자동 추천·내보내기(영상/프리미어 XML/SRT)."""
import hashlib
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
import uuid
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import quote

import captions
import core
import takes

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


def probe(path):
    """ffmpeg 만으로 길이·크기·소리 유무 확인 (ffprobe 는 없을 수 있음)."""
    path = Path(path)
    err = core.run([core.ffmpeg(), "-hide_banner", "-i", str(path)]).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
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
            "audio": "Audio:" in err, "kind": kind, "hdr": hdr}


def media_info(name):
    i = probe(video_path(name))
    return {k: i[k] for k in ("duration", "width", "height", "fps", "hdr")}


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
    return e


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


def make_proxy(f, src="videos", log=print):
    """4K·HEVC(아이폰) 영상도 편집실에서 부드럽게: 540p H.264, 짧은 키프레임 간격. 내보내기는 항상 원본으로."""
    p, out = media_path(f, src), proxy_path(f, src)
    out.parent.mkdir(parents=True, exist_ok=True)
    CANCEL.clear()
    info = probe(p)
    dur = info["duration"] or 1
    tmp = out.with_suffix(".part.mp4")
    vf = (tonemap_chain(info["hdr"], 1920) if info.get("hdr") else []) + ["scale=-2:540", "fps=30"]
    cmd = [core.ffmpeg(), "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", "-i", str(p), "-vf", ",".join(vf),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-ac", "2", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
    _PROCS.add(proc)
    try:
        for line in proc.stdout:
            if line.startswith(b"out_time_us="):
                try:
                    t = int(line[12:].strip() or 0) / 1e6
                    core.set_progress(label="미리보기 파일 만드는 중", item=f, pct=min(99, int(t * 100 / dur)), detail=f"{int(t)}초 / {int(dur)}초")
                except ValueError:
                    pass
        proc.wait()
    finally:
        _PROCS.discard(proc)
        proc.stdout.close()
    if CANCEL.is_set():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("미리보기 파일 만들기를 멈췄어요")
    if proc.returncode or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("미리보기 파일을 만들지 못했어요")
    os.replace(tmp, out)
    log(f"  미리보기 파일 완료 · {Path(f).name}")
    return {"src": src, "file": Path(f).name}


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
        os.replace(tmp, dest)
    except Exception:
        pass


def save_upload(name, stream, length):
    """가져오기: 음악·이미지·영상 파일을 그대로 받아 저장."""
    name = re.sub(r'[\\/:*?"<>|]', "_", Path(name).name).strip(" .") or "media"
    if Path(name).suffix.lower() not in MEDIA_EXTS:
        raise ValueError("영상·음악·이미지 파일만 가져올 수 있어요")
    dest = ASSETS / name
    k = 1
    while dest.exists():
        dest = ASSETS / f"{Path(name).stem}_{k}{Path(name).suffix}"
        k += 1
    left = length
    with open(dest, "wb") as f:
        while left > 0:
            chunk = stream.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
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
    tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def waveform(name, per_sec=50, src="videos"):
    """소리 크기 (0~1) 를 1초에 per_sec 개씩."""
    cache = _cache_dir(name, src) / f"waveform_{per_sec}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    with _gen_lock(str(cache)):
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        import numpy as np
        sr = 8000
        with _GEN_SEM:
            p = subprocess.run([core.ffmpeg(), "-v", "error", "-i", str(media_path(name, src)), "-vn", "-ac", "1", "-ar", str(sr),
                                "-f", "s16le", "-"], capture_output=True, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
        a = np.abs(np.frombuffer(p.stdout, np.int16).astype(np.float32)) / 32768.0
        hop = sr // per_sec
        n = len(a) // hop
        peaks = a[: n * hop].reshape(n, hop).max(axis=1) if n else np.zeros(0)
        top = float(np.percentile(peaks, 99)) if n else 1.0
        data = {"per_sec": per_sec, "peaks": [round(min(1.0, float(x) / (top or 1)), 3) for x in peaks], "top": round(top, 4)}
        cache.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(cache, json.dumps(data))
        return data


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
            os.replace(tmp, jpg)
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
            os.replace(tmp, out)
        return out


def media_bundle(f, src="videos"):
    """편집실에 미디어를 올릴 때 필요한 정보 한 번에 (파형·썸네일 포함)."""
    e = media_entry(f, src)
    out = {"media": e, "waveform": None, "thumbs": None}
    if e["audio"]:
        out["waveform"] = waveform(e["file"], 50, src)
    if e["kind"] in ("video", "image"):
        out["thumbs"] = thumbs(e["file"], src)
    return out


def rev_audio(src, f, a, b, sp):
    """거꾸로 재생 클립의 소리 (미리보기용): 내보내기와 같은 areverse + 속도로 미리 만들어 둔 WAV."""
    a, b, sp = round(float(a), 3), round(float(b), 3), round(max(0.05, float(sp)), 4)
    p = media_path(f, src)
    if not p.exists():
        raise FileNotFoundError(f"파일을 찾지 못했어요 · {f}")
    d = _cache_dir(f, src) / "rev"
    out = d / f"rev_{a:.3f}_{b:.3f}_{sp:.4f}.wav"
    if out.exists():
        return out
    with _gen_lock(str(out)):
        if out.exists():
            return out
        d.mkdir(parents=True, exist_ok=True)
        old = sorted(d.glob("rev_*.wav"), key=lambda x: x.stat().st_mtime)
        for o in old[:-40]:  # 오래된 것 정리
            o.unlink(missing_ok=True)
        tmp = d / f"rev.{threading.get_ident()}.tmp.wav"
        af = ",".join(["areverse"] + _atempo(sp))
        with _GEN_SEM:
            r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, a):.3f}", "-t", f"{max(0.05, b - a):.3f}", "-i", str(p),
                          "-vn", "-af", af, "-ac", "2", "-ar", "48000", str(tmp)])
        if r.returncode or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise RuntimeError("거꾸로 재생 소리를 만들지 못했어요")
        os.replace(tmp, out)
        return out


def run_killable(cmd):
    """멈추기(✕)로 끌 수 있는 ffmpeg 실행 (검수 등). 멈추면 RuntimeError."""
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
                         encoding="utf-8", errors="replace", **core.NO_WINDOW)
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
        sp = float(c.get("speed") or 1.0)  # 말 빠르기 맞추기 (#7) — 영상·소리 같이
        if abs(sp - 1) > 1e-6:
            for x in pr:
                x["speed"] = sp
        items += pr
        pos += (c["out"] - c["in"]) / sp
    return items


def _punch(cuts, segs, every):
    """약 every 초마다 말이 시작하는 곳(또는 원래 컷)에서 나눠, 번갈아 확대 표시."""
    if not every or every <= 0:
        return cuts
    starts = sorted(s["start"] for s in segs)
    out, pos, last, zoomed = [], 0.0, 0.0, False
    for c in cuts:
        a = c["in"]
        if pos > 0 and pos - last >= every:  # 점프 컷 자리에서 확대/원래 크기 바꾸기
            zoomed, last = not zoomed, pos
        for s in starts:
            if a + 0.6 < s < c["out"] - 0.6 and pos + (s - c["in"]) - last >= every:
                out.append({"in": round(a, 3), "out": round(s, 3), "zoom": zoomed})
                a, zoomed, last = s, not zoomed, pos + (s - c["in"])
        out.append({"in": round(a, 3), "out": c["out"], "zoom": zoomed})
        pos += c["out"] - c["in"]
    return out


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


def _split_rhythm(cuts, words, target, units=None, cps=None):
    """말하는 컷 중 목표 컷 길이(targetShot)보다 긴 것을 단어 경계에서 나눔 — 나눈 곳마다 원래 크기 ↔ 확대(zoomScale)를 번갈아
    (카메라 두 대처럼 · zoom 표시는 _punch 와 같음: _items_from_cuts 가 확대). 지우는 곳은 없음 (나누기만).
    target: 초, 또는 타임라인 위치 → 초 (도입·본론·마무리). 시범처럼 말이 드문 컷은 그대로."""
    tf = target if callable(target) else (lambda pos, t=float(target or 0): t)
    units = words if units is None else units
    cps = cps or 0.0  # 모르면 말이 드문 컷도 나눔
    out, pos = [], 0.0
    for c in cuts:
        a, b = float(c["in"]), float(c["out"])
        cur, k0 = a, len(out)
        if tf(pos) > 0 and b - a > tf(pos) and (not cps or _is_talk(a, b, units, cps)):
            bs = _word_bounds(words, a, b)
            while True:
                t = tf(pos + cur - a)
                if t <= 0 or b - cur <= 1.5 * t:
                    break
                half = max(RHYTHM_MIN, 0.5 * t)
                cand = [x for x in bs if cur + half <= x <= b - half]
                if not cand:
                    break
                x = min(cand, key=lambda v: abs(v - (cur + t)))
                out.append({"in": round(cur, 3), "out": x, "zoom": bool((len(out) - k0) % 2)})
                cur = x
        # 나눈 조각만 원래 크기 ↔ 확대 번갈아 (안 나눈 컷·시범 장면은 원래 화면 그대로)
        out.append({"in": round(cur, 3), "out": c["out"], "zoom": bool((len(out) - k0) % 2)})
        pos += b - a
    return out


def _curve_fn(curve3, total):
    """컷 리듬 3구간 [도입 30초, 본론, 마무리 20초] → 타임라인 위치별 목표 컷 길이."""
    c = [float(x or 0) for x in curve3]
    return lambda pos: c[0] if pos < 30.0 else c[2] if pos >= total - 20.0 else c[1]


def _apply_tempo(cuts, units, cps, factor):
    """말이 촘촘한 컷만 factor 배 빠르게 (1.0~1.12) · 시범처럼 말이 드문 곳은 1.0 그대로."""
    f = round(min(TEMPO_MAX, max(1.0, float(factor or 1.0))), 3)
    return [dict(c, speed=f) if f > 1.0 and _is_talk(c["in"], c["out"], units, cps) else dict(c) for c in cuts]


def _rhythm(cuts, segs, st, every):
    """스타일 가편집의 컷 목록 → 컷 리듬(나누기·번갈아 확대) + 말 빠르기. 스타일 값이 없으면 예전처럼 줌 컷만."""
    words = _rhythm_words(segs)
    units = words or [(float(s["start"]), float(s["end"]), len(str(s["text"]).replace(" ", ""))) for s in segs]
    cps = _coach_cps(segs)
    split = float(st.get("splitShot") or 0)
    c3 = st.get("curve3") if isinstance(st.get("curve3"), list) and len(st["curve3"]) == 3 else [split] * 3
    if split > 0 and words:
        out = _split_rhythm(cuts, words, _curve_fn(c3, sum(c["out"] - c["in"] for c in cuts)), units, cps)
    else:
        out = _punch(cuts, segs, every)
    ref = float(st.get("tempo") or 0)
    return _apply_tempo(out, units, cps, ref / cps) if ref > 0 and cps > 0 else out


def _new_seq(name, fmt, items, **kw):
    s = {"id": _nid(), "name": name, "format": fmt, "v": 2, "tracks": default_tracks(), "items": items, "trans": [],
         "markers": [], "captionsOn": True, "titles": [], "shapes": [], "master": {"volume": 1.0, "normalize": True},
         "duck": {"on": True, "amount": -14.0}}
    s.update(kw)
    return s


GENERIC = {"팁", "꿀팁", "중요", "핵심", "강조", "비결", "방법", "포인트", "무조건", "절대", "실수", "차이", "첫 번째", "첫번째",
           "잘하", "어떻게", "비밀", "원리", "기술", "프로", "힘들"}


def _hook(rec_item):
    """쇼츠 제목: '주제어 꿀팁' / '주제어!' / 첫 문장."""
    kws = rec_item["keywords"]
    topic = next((k for k in kws if k not in GENERIC), None)
    tip = any(k in kws for k in ("꿀팁", "팁", "비결", "방법", "포인트"))
    first = re.sub(r"^\(테스트\)\s*", "", rec_item["title"]).strip()
    if topic and tip:
        return f"{topic} 꿀팁"
    if topic:
        return f"{topic}!"
    return first[:16]


CAP_Y = {"bottom": 0.85, "middle": 0.55, "top": 0.15}

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


def emphasis_label(txt, terms=None):
    """말 한 줄 → 화면에 띄울 강조 글자 (없으면 None). 짧은 말은 그대로, 길면 '기술 이름 + 강조 낱말!' ('인사이드 패스 핵심!').
    기술 이름도 강조 낱말도 낱말 전체가 맞을 때만 — 낱말 조각('잘하!'·'턴!')은 만들지 않음."""
    terms = terms if terms is not None else _emph_terms()
    toks = _tokens(txt)
    found = _find_terms(toks, terms)
    emph = _find_emph(toks)
    if not found and not emph:
        return None, 0
    flat = re.sub(r"\s+", " ", str(txt or "")).strip()
    score = len(found) + (2 if emph else 0)
    if len(flat.replace(" ", "")) <= EMPH_SHORT:
        return flat, score
    if found and emph:
        return f"{found[0][2]} {emph}!", score
    if found and len(found[0][2].replace(" ", "")) >= 3:  # 기술 이름만 있으면 이름이 충분히 길 때만 ('인사이드 패스!', '트래핑!')
        return f"{found[0][2]}!", score
    return None, 0


def _intro_teaser(items, rec, tidy, sec, segs=None, peaks=None):
    """롱폼 맨 앞에 미리 보기 sec 초 + 제목 → (items, titles). 원래 컷은 그만큼 뒤로 밂 (지우지 않음).
    고르는 곳: 정리 컷 안의 sec 초 창 중에서 말이 적고(감독님 시범) 현장 소리가 큰(공 차는 소리·환호) 곳 — 첫 쇼츠 추천 구간이면 조금 더 점수.
    말이 많은 곳밖에 없으면 그 소리는 줄여서 (제목과 겹쳐 말이 끊겨 들리지 않게)."""
    r = (rec.get("shorts") or [None])[0]
    pool = list(tidy or []) or list((r or {}).get("cuts") or [])
    if not pool or sec <= 0:
        return items, []
    talk = [(float(s["start"]), float(s["end"])) for s in segs or [] if float(s.get("end", 0)) > float(s.get("start", 0))]
    rr = (float(r["start"]), float(r["end"])) if r and "start" in r and "end" in r else None
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
                if b - a >= min(sec, b0 - a0) - 1e-6:
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
    text = _hook(r) if r else "오늘의 하이라이트"
    return _pair(a, b, start=0.0, vol=vol) + moved, [{"id": _nid(), "text": text, "start": 0.0, "dur": round(ln, 2), "style": dict(TITLE_STYLE),
                                                    "plan": "teaser"}]


def _src_to_tl(items, t):
    """원본 t 초 → 타임라인 시각 (V1 클립 안일 때만, 잘린 곳이면 None)."""
    for it in items:
        if it.get("track") == "V1" and it.get("media", "main") == "main" and not it.get("noCaps") and float(it["in"]) <= t < float(it["out"]):
            return float(it["start"]) + (t - float(it["in"])) / i_sp(it)
    return None


def _emphasis_titles(items, segs, per_min, color, after=0.0):
    """받아쓰기에서 기술 이름·강조 낱말이 든 말을 골라 1.5초 큰 색 글씨(titles)로 — 1분에 per_min 개까지, 서로 EMPH_GAP 초 넘게 떨어뜨림.
    teaser 가 있으면 그 뒤(after)부터. 글자는 emphasis_label (짧은 말은 그대로, 길면 '기술 이름 + 강조 낱말!', 맞는 게 없으면 건너뜀)."""
    total = max([i_end(it) for it in items] or [0.0])
    cap = int(per_min * total / 60.0 + 1e-9)
    if cap <= 0 or not segs:
        return []
    terms = _emph_terms()
    cands = []
    for s in segs:
        txt = str(s.get("text") or "").strip()
        label, score = emphasis_label(txt, terms)
        if not label:
            continue
        t = float(s["start"])
        key = label.rstrip("!").split()[0]
        for w in s.get("words") or ():  # 낱말 시각이 있으면 그 낱말이 나오는 때에
            if str(w.get("w") or "").replace(" ", "").startswith(key):
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
        if all(abs(tl - p) >= EMPH_GAP for p, _ in picked):
            picked.append((tl, label))
    fill = str(color or "").upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(color or "")) else "#FFE14D"
    sty = dict(TITLE_STYLE, fill=fill, size=88, y=0.3)
    return [{"id": _nid(), "text": label, "start": round(tl, 2), "dur": EMPH_DUR, "style": dict(sty), "plan": "emphasis"}
            for tl, label in sorted(picked)]


def auto_sequences(name, info, style=None, kinds=("long", "shorts")):
    """1차 가편집: 롱폼 군더더기 정리본 + 쇼츠 추천 구간별 편집본.
    style: style.edit_params() 결과 (말 사이 공백·줌 컷·자막 위치/색·소리 크기·컷 리듬·말 빠르기) — 있으면 그 스타일대로."""
    st = style or {}
    rec = recommend(name, keep_pause=st.get("keepPause"))
    segs = _segments_of(name)
    every, zoom = float(st.get("zoomEvery") or 0), min(1.6, max(1.0, float(st.get("zoomScale") or 1.0)))
    if every <= 0:  # 컷 리듬 (#7): 확대 컷이 거의 없는 스타일 → 나눈 곳만 살짝
        zoom = min(zoom, SOFT_ZOOM)
    master = {"volume": 1.0, "normalize": True, "lufs": round(min(-9.0, max(-24.0, float(st.get("lufs") or -14.0))), 1)}

    def cap(base):
        c = dict(base)
        if st.get("captionPos") in CAP_Y:
            c["y"] = CAP_Y[st["captionPos"]]
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(st.get("captionColor") or "")):
            c["fill"] = st["captionColor"].upper()
        return c

    caps_on = bool(st.get("captions", True))
    seqs = []
    if "long" in kinds:
        tidy = rec["tidy"] or [{"in": 0.0, "out": info["duration"]}]
        items, extra = _items_from_cuts(_rhythm(tidy, segs, st, every), zoom=zoom), {}
        tz, em = st.get("introTeaser") or {}, st.get("emphasisTitles") or {}
        titles = []
        if tz.get("on") and float(tz.get("sec") or 0) > 0:  # 기획 분석: 티저형 인트로 스타일
            try:  # 소리 크기(편집실 파형, 없으면 만듦 · 몇 초) — 못 읽으면 말이 적은 곳만 보고 고름
                peaks = waveform(name)
            except Exception:  # noqa: BLE001
                peaks = None
            items, titles = _intro_teaser(items, rec, tidy, min(6.0, max(3.0, float(tz["sec"]))), segs, peaks)
        if float(em.get("perMin") or 0) > 0:  # 기획 분석: 핵심 낱말 강조 자막
            titles += _emphasis_titles(items, segs, min(4.0, float(em["perMin"])), em.get("color"), titles[0]["dur"] if titles else 0.0)
        if titles:
            extra["titles"] = titles
        seqs.append(_new_seq("롱폼 가편집", "long", items, captionStyle=cap(LONG_STYLE),
                             layout={"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0},
                             master=dict(master), captionsOn=caps_on, **extra))
    if "shorts" in kinds:
        for i, r in enumerate(rec["shorts"], 1):
            items = _items_from_cuts(_rhythm(r["cuts"], segs, st, every), 0.3, zoom=zoom)
            length = max([i_end(it) for it in items] or [0.0])  # 말 빠르기를 맞추면 조금 짧아짐
            seqs.append(_new_seq(_short_name(i, r), "shorts", items, captionStyle=cap(SHORTS_STYLE),
                                 layout=dict(BOX_LAYOUT), master=dict(master), captionsOn=caps_on,
                                 titles=[{"id": _nid(), "text": _hook(r), "start": 0.0, "dur": round(length, 2), "style": dict(TITLE_STYLE)}]))
    for q in seqs:  # 자동으로 만든 가편집 표시 ('가편집 다시 만들기'는 이것만 바꿈 · 스타일 가편집은 app.py 에서 따로 표시)
        q["auto"] = "style" if style else "rough"
    return seqs


def _short_name(i, r):
    return f"쇼츠 {i} · {r['title'][:14]}"


def _segments_of(name):
    t = core.adir(name) / "transcript.json"
    return [s for s in json.loads(t.read_text(encoding="utf-8")) if s["text"].strip()] if t.exists() else []


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
    for s in proj["sequences"]:
        migrate_seq(s)
        lm = {}  # 자르기를 반복해 길어진 연결 표시(link)를 짧게 (같은 것끼리는 계속 같게)
        for it in s.get("items", []):
            ln = it.get("link")
            if ln and len(ln) > 16:
                it["link"] = lm.setdefault(ln, _nid())
    media = proj.setdefault("media", [])
    if not any(m["id"] == "main" for m in media):
        i = proj["info"]
        media.insert(0, {"id": "main", "kind": "video", "src": "videos", "file": name, "dur": i["duration"], "w": i["width"],
                         "h": i["height"], "fps": i.get("fps", 30.0), "audio": True})
    for m in media:  # 미리보기 파일이 있는지 매번 다시 확인
        if m.get("kind") == "video":
            try:
                m["proxy"] = proxy_path(m["file"], m.get("src", "videos")).exists()
            except ValueError:
                m["proxy"] = False
    proj["v"] = 2
    return proj


# 프로젝트 저장: 한 번에 하나씩(잠금) · 임시 파일에 다 쓴 뒤 바꿔 끼우기(중간에 꺼져도 깨지지 않음) · 판(rev) 번호로 다른 창 덮어쓰기 방지
_SAVE_LOCK = threading.RLock()
_REVC = {}
_WRITER = {}  # 파일별 마지막으로 저장한 편집실 창: (창 id, 그 창이 이어서 저장하기 시작한 판, 마지막 판, 편집 번호)


class Conflict(Exception):
    def __init__(self, rev):
        super().__init__("다른 창에서 이 편집본이 바뀌었어요")
        self.rev = rev


def _read_json(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _replace_retry(src, dst, tries=20):
    for i in range(tries):  # Windows: 백신·OneDrive·탐색기가 잠깐 잡고 있으면 실패 → 잠깐 뒤 다시
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


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


BK_TAGS = {"재분석전": "편집점 다시 찾기 전", "덮어쓰기전": "다른 창 내용으로 덮어쓰기 전", "변환전": "새 형식으로 바꾸기 전"}


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


def _captions_of(segs, info):
    """받아쓰기 → 편집실 자막. 단어 시각이 있으면 읽기 좋게 나눔 (세로 영상은 쇼츠형 한 줄, 가로는 롱폼형 두 줄까지 ·
    용어 사전의 여러 단어 용어는 안 나눔) · 없으면(예전 받아쓰기) 구간 그대로.
    단어 시각은 작게 wt (1/100초 정수: 첫 단어 시작, 그 뒤로는 앞 값과의 차이 · 낱말은 자막 글 그대로) · 가로 영상 자막은 쇼츠 편집본에서 쓸
    쇼츠형 나눌 곳 sh(몇 번째 낱말부터 새 자막인지)도 함께."""
    fmt = "shorts" if info.get("height", 0) > info.get("width", 0) else "long"
    terms = captions.load_dict(core.dict_path())["terms"] if any(s.get("words") for s in segs) else ()
    out = []
    for c in captions.from_segments(segs, fmt, terms):
        ws = c.pop("words", None)
        cap = {"id": _nid(), **c}
        if ws:
            cs = [int(round(float(x) * 100)) for w in ws for x in (w["s"], w["e"])]
            cap["wt"] = cs[:1] + [b - a for a, b in zip(cs, cs[1:])]
            parts = captions.chunk(ws, "shorts", terms=terms) if fmt == "long" else []
            if len(parts) > 1 and sum(len(q["words"]) for q in parts) == len(ws):
                cap["sh"], k = [], 0
                for q in parts[:-1]:
                    k += len(q["words"])
                    cap["sh"].append(k)
        out.append(cap)
    return out


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
    segs = []
    t = core.adir(name) / "transcript.json"
    if t.exists():
        segs = json.loads(t.read_text(encoding="utf-8"))
    seqs = auto_sequences(name, info)
    proj = {
        "source": name,
        "info": info,
        "captions": _captions_of(segs, info),
        "sequences": seqs,
        "active": seqs[0]["id"],
    }
    proj = migrate_project(name, proj)
    save_project(name, proj)
    return proj


def save_project(name, proj, base_rev=None, force=False, client=None, seq=None):
    """base_rev: 편집실이 열 때 받은 판 번호. 그 사이 다른 창·작업이 저장했으면 Conflict (덮어쓰지 않음).
    force: 그래도 이 내용으로 (덮어쓰기 전 상태는 백업).
    client·seq: 편집실 창 id·편집 번호 — 저장 응답을 받기 전에 창을 닫아(신호 저장) 판 번호가 뒤처져도
    그 사이 저장한 게 같은 창뿐이면 이어서 저장 (늦게 도착한 예전 내용은 버림)."""
    p = _ppath(name)
    client = str(client) if client else None
    seq = int(seq) if isinstance(seq, (int, float)) and not isinstance(seq, bool) else None
    with _SAVE_LOCK:
        disk = _disk_rev(p) if p.exists() else 0
        w = _WRITER.get(str(p))
        mine = client is not None and w is not None and w[0] == client and w[2] == disk
        if base_rev is not None and not force and disk >= 0 and int(base_rev) != disk:
            if not (mine and w[1] <= int(base_rev) < disk):
                raise Conflict(disk)
            if seq is not None and w[3] is not None and seq <= w[3]:
                return disk  # 같은 창의 더 최근 내용이 이미 저장됨
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
        # 자동 백업: 5분마다 한 벌씩, 최근 10개까지
        regs = [x for x in _backup_files(p.stem) if not x[1]]
        if not regs or time.time() - _bk_epoch(regs[-1][0]) > 300:
            _backup_copy(p)
        _prune_backups(p.stem)
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


def reanalyze_project(name):
    """편집점 다시 찾기 뒤: 사용자가 만든 편집본은 하나도 지우지 않고, 자막·영상 정보만 새로 받아쓴 내용으로 +
    새 가편집은 옆에 추가 (이전 상태는 '편집점 다시 찾기 전' 백업으로 남김)."""
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
    caps = _captions_of(segs, info)
    changed_caps = [(c["start"], c["end"], c["text"]) for c in proj.get("captions") or []] != [(c["start"], c["end"], c["text"]) for c in caps]
    proj["captions"] = caps
    for m in proj.get("media") or []:
        if m.get("id") == "main":
            m.update(dur=info["duration"], w=info["width"], h=info["height"], fps=info.get("fps", 30.0))
    names = {q["name"] for q in proj["sequences"]}
    fresh = auto_sequences(name, info)
    for q in fresh:
        q["name"] = _uniq_name(q["name"] + " (새로 찾음)", names)
        names.add(q["name"])
    proj["sequences"] += fresh
    save_project(name, proj)
    return proj, len(fresh), changed_caps


def freeze_frame(src, f, t):
    """정지 화면(프레임 고정): 그 순간을 PNG 로 저장해 가져온 미디어로 (원래 영상과 같은 크기로 보이게 freeze 표시)."""
    p = media_path(f, src)
    tag = hashlib.sha1(f"{src}|{p.name}".encode("utf-8")).hexdigest()[:6]
    out = ASSETS / f"정지_{Path(f).stem[:30].strip(' .')}_{tag}_{t:.2f}.png"
    if not out.exists():
        info = probe(p)
        vf = ["-vf", ",".join(tonemap_chain(info["hdr"]))] if info.get("hdr") else []
        r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(p), "-frames:v", "1", *vf, str(out)])
        if r.returncode or not out.exists():
            raise RuntimeError("정지 화면을 만들지 못했어요")
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


def _covered(ivs, a, b):
    """[a, b) 가운데 정리할 구간에 덮인 길이(초) — 서로 겹친 곳은 한 번만 셈."""
    got, end = 0.0, a
    for x, y, *_ in sorted(ivs):
        x, y = max(x, end), min(y, b)
        if y > x:
            got, end = got + y - x, y
    return got


def recommend(name, min_len=20.0, max_len=55.0, n=3, keep_pause=None, segs=None):
    """keep_pause: 말 사이 이보다 길게 쉬면 자름 (스타일). 없으면 기본(약 1.2초).
    segs: 받아쓰기 대신 쓸 말 목록 (MSG 는 문장 단위로 나누고 깨진 말을 뺀 것을 줌). 없으면 받아쓰기 파일."""
    if keep_pause:
        kp = max(0.08, float(keep_pause))
        pre, post = min(0.15, kp * 0.4), min(0.25, kp * 0.6)
        gap_s = gap_l = max(0.0, kp - pre - post)
    else:
        pre, post, gap_s, gap_l = 0.15, 0.25, 0.6, 0.8
    if segs is not None:
        segs = [s for s in segs if str(s.get("text") or "").strip()]
    else:
        segs = []
        t = core.adir(name) / "transcript.json"
        if t.exists():
            segs = [s for s in json.loads(t.read_text(encoding="utf-8")) if s["text"].strip()]
    extra_p = core.adir(name) / "analysis.json"
    extra = json.loads(extra_p.read_text(encoding="utf-8")) if extra_p.exists() else {"silences": [], "loud_peaks": []}
    peaks = [p["time"] for p in extra.get("loud_peaks", [])]

    # 군더더기 표시: 추임새 · 같은 말 연속 반복(마지막 것만 남김)
    junk = set()
    for i, s in enumerate(segs):
        if _norm(s["text"]) in FILLERS or len(_norm(s["text"])) <= 1:
            junk.add(i)
        if i + 1 < len(segs) and _norm(s["text"]) == _norm(segs[i + 1]["text"]):
            junk.add(i)
    junk_iv = takes.find_junk(segs, extra.get("silences", []))  # NG 테이크·슬레이트·말더듬 구간
    junk |= {i for i, s in enumerate(segs) if any(a <= (s["start"] + s["end"]) / 2 < b for a, b, _ in junk_iv)}  # 그 안에 든 말도 군더더기
    # 단어 시각이 있으면: 말 사이에 홀로 떨어진 '음'·'어' 같은 추임새 단어도 뺌 (이미 빠지는 곳에 든 것은 셈하지 않음)
    gone = [(segs[k]["start"], segs[k]["end"]) for k in junk] + [(a, b) for a, b, _ in junk_iv]
    fill_iv = [f for f in takes.find_fillers(segs) if not any(a <= (f[0] + f[1]) / 2 <= b for a, b in gone)]

    def seg_score(s):
        sc = sum(w for k, w in KEYWORDS.items() if k in s["text"])
        sc += 0.15 * len(_norm(s["text"]))
        return sc

    cands = []
    for i in range(len(segs)):
        start = segs[i]["start"]
        if i in junk:
            continue
        j, sc, chars, junk_n = i, 0.0, 0, 0
        while j < len(segs) and segs[j]["end"] - start <= max_len:
            if j in junk:
                junk_n += 1
            else:
                sc += seg_score(segs[j])
                chars += len(_norm(segs[j]["text"]))
            end = segs[j]["end"]
            if end - start >= min_len:
                dur = end - start
                score = sc + 2.5 * sum(1 for p in peaks if start <= p <= end) + 2.0 * (chars / dur) - 1.5 * junk_n
                # 첫 문장이 강한 말로 시작하면 가산 (훅)
                if any(k in segs[i]["text"] for k in ("팁", "중요", "자 여기서", "비결", "어떻게", "?")):
                    score += 4
                cands.append((score / (dur ** 0.35), start, end, i, j))
            j += 1
    cands.sort(reverse=True)
    picked = []
    for score, s0, e0, i, j in cands:
        if all(e0 <= p["start"] or s0 >= p["end"] for p in picked):
            # 구간 안에서 군더더기·긴 무음을 빼고 컷 목록 생성
            cuts, cur = [], None
            for k in range(i, j + 1):
                s = segs[k]
                if k in junk:
                    if cur:
                        cuts.append(cur)
                        cur = None
                    continue
                a, b = max(0.0, s["start"] - pre), s["end"] + min(0.2, post)
                if cur and a - cur["out"] <= gap_s:
                    cur["out"] = b
                else:
                    if cur:
                        cuts.append(cur)
                    cur = {"in": round(a, 2), "out": round(b, 2)}
            if cur:
                cuts.append(cur)
            cuts = _minus(_minus(cuts, junk_iv, pre), fill_iv)
            if not cuts or e0 - s0 - _covered(junk_iv, s0, e0) < min_len:  # NG 구간을 빼면 너무 짧아지는 후보는 버림
                continue
            hits = [k for k in KEYWORDS if k != "?" and any(k in segs[x]["text"] for x in range(i, j + 1))]
            hits.sort(key=lambda k: (k in GENERIC, -KEYWORDS[k], -len(k)))  # 주제어 먼저
            title = segs[i]["text"][:28]
            picked.append({"start": s0, "end": e0, "score": round(score, 1), "title": title, "keywords": hits[:4],
                           "cuts": cuts, "length": round(sum(c["out"] - c["in"] for c in cuts), 1)})
        if len(picked) >= n:
            break
    picked.sort(key=lambda p: -p["score"])

    # 롱폼용: 전체에서 군더더기·긴 무음 정리한 컷
    tidy, cur = [], None
    for k, s in enumerate(segs):
        if k in junk:
            if cur:
                tidy.append(cur)
                cur = None
            continue
        a, b = max(0.0, s["start"] - pre), s["end"] + post
        if cur and a - cur["out"] <= gap_l:
            cur["out"] = b
        else:
            if cur:
                tidy.append(cur)
            cur = {"in": round(a, 2), "out": round(b, 2)}
    if cur:
        tidy.append(cur)
    tidy = _minus(_minus(tidy, junk_iv, pre), fill_iv)
    return {"shorts": picked, "tidy": tidy, "junk": len(junk) + len(fill_iv), "segments": len(segs),
            "junk_list": [{"a": a, "b": b, "why": why} for a, b, why in sorted(junk_iv + fill_iv, key=lambda x: (x[0], -x[1]))]}


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


def timeline_captions(proj):
    """자막을 타임라인 시간으로 (V1 의 원본 클립을 따라감, 잘린 부분은 빠지고 여러 클립에 걸치면 나뉨)."""
    res = []
    total = seq_total(proj)
    # noCaps: 다시 보기·티저처럼 같은 장면을 한 번 더 쓴 클립은 말 자막을 다시 띄우지 않음 (editor.html capsTL 과 같은 규칙)
    vids = sorted([it for it in proj["items"] if it["track"] == "V1" and it["media"] == "main" and not it.get("rev") and not it.get("noCaps")],
                  key=lambda x: x["start"])
    shorts = SHORTS_SPLIT and proj.get("format") == "shorts"
    for cap0 in proj["captions"]:
        for cap, ws in (_shorts_parts(cap0) if shorts else [(cap0, _cap_words(cap0))]):
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
                        res.append(c)
    # 같은 자막이 컷(확대 컷·말 빠르기 나누기)으로만 나뉘어 바로 이어지면 한 덩어리로 — 컷마다 효과가 다시 시작돼 깜빡이지 않게
    # (editor.html capsTL 과 같은 규칙)
    out = []
    for c in sorted(res, key=lambda x: x["start"]):
        p = out[-1] if out else None
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
        out.append(c)
    for c in out:
        for k in ("_k", "_keep", "_n", "_toks", "_full"):
            c.pop(k, None)
    return out


# ---------- 단어 시각 · 쇼츠형 자막 나누기 (#5) ----------

# 쇼츠 편집본에서 가로 영상 자막을 sh 로 나눠 내보낼지 — 편집실 미리보기(editor.html)가 아직 안 나누므로 꺼 둠
# (켜면 미리보기와 내보낸 영상의 자막이 달라짐 · 미리보기가 같은 방식으로 나누게 되면 True)
SHORTS_SPLIT = False

def _cap_words(cap):
    """자막의 단어 시각 [{w, s, e}] — 자막 안에 든 단어 수가 지금 글의 낱말 수와 같을 때만.
    편집실에서 글을 고쳐 낱말 수가 바뀌었거나 자막을 나눠서 안 맞으면 None (글자 수 비례로).
    wt(1/100초 · 첫 값 뒤로는 차이) 또는 예전 모양 words [{w, s, e}] 둘 다 읽음."""
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
    """쇼츠 편집본에서 가로 영상 자막을 쇼츠형(한 줄 12글자·2초까지)으로 — 만들 때 정해 둔 나눌 곳(sh)에서.
    나눈 자막은 이어서 보임 (다음 자막 시작까지) · 글을 고쳐 낱말 수가 바뀌었으면 한 덩어리 그대로. [(자막, 단어 시각)]"""
    ws = _cap_words(cap)
    sh = cap.get("sh")
    if not ws or not isinstance(sh, list) or not sh:
        return [(cap, ws)]
    try:
        cuts = [0] + [int(k) for k in sh] + [len(ws)]
    except (TypeError, ValueError):
        return [(cap, ws)]
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        return [(cap, ws)]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        s = cap["start"] if a == 0 else max(cap["start"], float(ws[a]["s"]))
        e = cap["end"] if b == len(ws) else min(cap["end"], float(ws[b]["s"]))
        if e - s <= 0.04:
            return [(cap, ws)]
        out.append(({"start": s, "end": e, "text": " ".join(w["w"] for w in ws[a:b])}, ws[a:b]))
    return out


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


def build_ass(proj, W, H):
    st = proj["captionStyle"]

    def style_line(name, s):
        font = "Pretendard Black" if s["weight"] == "Black" else "Pretendard"
        bold = 0 if s["weight"] == "Black" else -1
        k = FONT_K
        if s.get("font") in TITLE_FONTS:  # 굵기가 하나뿐인 글꼴 → 굵게 흉내 없이 (미리보기도 font-weight 400)
            font, k = TITLE_FONTS[s["font"]]
            bold = 0
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
    for t in proj["titles"]:
        lines.append(style_line(f"T{t['id']}", t["style"]))
    lines.append("Style: Shape,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1")
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    # 도형 (자막·타이틀 아래 층)
    tl_total = seq_total(proj)
    for sh in proj.get("shapes", []):
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
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Shape,,0,0,0,,"
                     rf"{{\an7\pos({x},{y})\p1\bord0\shad0\1c{col[:2]}{col[4:]}&\1a&H{a:02X}&}}{path}{{\p0}}")

    def event(style, s, start, end, text, words=None):
        al = s.get("align", "center")
        an = {"left": 1, "right": 3}.get(al, 2)
        x, y = int(W * s.get("x", 0.5)), int(H * s["y"])
        rz = -_rot(s)  # 화면은 시계 방향 + · ASS \frz 는 반시계 방향 + (기준점은 \pos 자리 = 미리보기 transform-origin)
        fr = rf"\frz{_num(round(rz, 2))}" if rz else ""
        tags = rf"{{\an{an}\pos({x},{y}){fr}}}" if s["effect"] != "slide" else rf"{{\an{an}{fr}}}"
        body = _karaoke(text, end - start, words, start) if s["effect"] == "karaoke" else _ass_text(text)
        eff = _effect_tags(s["effect"], x, y, end - start, rz)
        lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{tags}{eff}{body}")

    if proj.get("captionsOn", True):
        for c in timeline_captions(proj):
            event("Cap", st, c["start"], c["end"], c["text"], c.get("words"))
    for t in proj["titles"]:
        if t["start"] < tl_total:
            event(f"T{t['id']}", t["style"], t["start"], min(tl_total, t["start"] + t["dur"]), t["text"])
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


# ---------- 프리미어 XML (FCP7) ----------

def _xmeml(proj, W, H, fps, stem, t_lo=0.0, t_hi=None):
    """프리미어 프로에서 '가져오기'로 여는 XML (FCP7 형식, 트랙·속도 포함). t_lo~t_hi: 구간 내보내기면 그 구간만."""
    tb = round(fps)
    ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    media = {m["id"]: m for m in proj["media"]}
    seen, fid = set(), {}
    t_hi = seq_total(proj) if t_hi is None else t_hi
    total_f = int(round((t_hi - t_lo) * fps))

    def cut(it):
        """구간 안쪽만 남긴 클립 (밖이면 None)."""
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

    def fileref(m):
        k = m["id"]
        if k not in fid:
            fid[k] = f"f{len(fid) + 1}"
        if k in seen:
            return f'<file id="{fid[k]}"/>'
        seen.add(k)
        p = media_path(m["file"], m["src"])
        url = "file://localhost/" + quote(str(p).replace("\\", "/").lstrip("/"))
        dur_f = int((m.get("dur") or 10) * fps)
        med = ""
        if m["kind"] in ("video", "image"):
            med += f"<video><samplecharacteristics><width>{m['w']}</width><height>{m['h']}</height></samplecharacteristics></video>"
        if m.get("audio"):
            med += "<audio><channelcount>2</channelcount></audio>"
        return (f'<file id="{fid[k]}"><name>{_xml_esc(p.name)}</name><pathurl>{url}</pathurl>{rate}<duration>{dur_f}</duration>'
                f"<media>{med}</media></file>")

    def clipitem(it, k, kind):
        m = media[it["media"]]
        sp = i_sp(it)
        s, e = int(round(it["start"] * fps)), int(round(i_end(it) * fps))
        i, o = int(round(it["in"] * fps)), int(round(it["out"] * fps))
        dur_f = int((m.get("dur") or 10) * fps)
        x = (f'<clipitem id="{kind}{k}"><name>{_xml_esc(Path(m["file"]).name)}</name><enabled>TRUE</enabled><duration>{dur_f}</duration>{rate}'
             f"<start>{s}</start><end>{e}</end><in>{i}</in><out>{o}</out>{fileref(m)}")
        if kind == "a":
            x += "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>"
        if abs(sp - 1) > 1e-3 or it.get("rev"):
            x += ("<filter><effect><name>Time Remap</name><effectid>timeremap</effectid><effectcategory>motion</effectcategory>"
                  "<effecttype>motion</effecttype><mediatype>video</mediatype>"
                  "<parameter><parameterid>variablespeed</parameterid><name>variablespeed</name><valuemin>0</valuemin><valuemax>1</valuemax><value>0</value></parameter>"
                  f"<parameter><parameterid>speed</parameterid><name>speed</name><valuemin>-100000</valuemin><valuemax>100000</valuemax><value>{sp * 100:.2f}</value></parameter>"
                  f"<parameter><parameterid>reverse</parameterid><name>reverse</name><value>{'TRUE' if it.get('rev') else 'FALSE'}</value></parameter>"
                  "</effect></filter>")
        if kind == "a":
            lv = 10 ** (param(it, "level")["v"] / 20) * 10 ** ((it.get("gain") or 0) / 20)
            gain = 0 if it.get("mute") else lv
            x += ("<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effecttype>audiolevels</effecttype><mediatype>audio</mediatype>"
                  f"<parameter><parameterid>level</parameterid><name>Level</name><valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{min(3.98, gain):.4f}</value></parameter></effect></filter>")
        return x + "</clipitem>"

    vt, at, k = [], [], 0
    for tr in proj["tracks"]:
        its = sorted([x for x in (cut(it) for it in proj["items"] if it["track"] == tr["id"]) if x], key=lambda x: x["start"])
        body = ""
        for it in its:
            if media.get(it["media"]) is None:
                continue
            k += 1
            body += clipitem(it, k, "v" if tr["k"] == "v" else "a")
        (vt if tr["k"] == "v" else at).append(f"<track>{body}</track>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq1"><name>{_xml_esc(stem)}</name>'
            f'<duration>{total_f}</duration>{rate}<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>'
            f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>{"".join(vt)}</video>'
            f'<audio>{"".join(at)}</audio></media></sequence></xmeml>\n')


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


def _fit(it, md, seq, W, H):
    """미디어를 화면에 놓는 기본 크기·위치 (효과 컨트롤 100% 기준). editor.html 의 fitRect 와 같은 계산."""
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
            return {"chain": pre + f"scale={scw}:{sch},crop={W}:{H}:{(scw - W) * rf:.1f}:{(sch - H) / 2:.1f}", "bw": W, "bh": H, "bx": 0, "by": 0, "bg": None}
        zw = _even(W * max(1.0, float(lay["zoom"])))
        zh = _even(vh * zw / sw)
        ch = min(zh, H)
        return {"chain": pre + f"scale={zw}:{zh},crop={W}:{ch}:{(zw - W) * rf:.1f}:{(zh - ch) / 2:.1f}", "bw": W, "bh": ch, "bx": 0,
                "by": (H - ch) * float(lay["vpos"]), "bg": lay["mode"], "bar": lay["bar"]}
    if fit == "auto":  # 위 트랙: 영상은 화면에 맞춤, 이미지는 작으면 원래 크기 (정지 화면은 원래 영상처럼 화면에 맞춤)
        fit = "native" if md["kind"] == "image" and not md.get("freeze") and sw * ref <= W and sh * ref <= H else "contain"
    cover = fit == "cover"
    s = ref if fit == "native" else (max if cover else min)(W / sw, H / sh)
    scw, sch = _even(sw * s), _even(sh * s)
    if cover:
        scw, sch = max(scw, W), max(sch, H)
        return {"chain": f"scale={scw}:{sch},crop={W}:{H}:{(scw - W) * rf:.1f}:{(sch - H) / 2:.1f}", "bw": W, "bh": H, "bx": 0, "by": 0, "bg": None}
    return {"chain": f"scale={scw}:{sch}", "bw": scw, "bh": sch, "bx": (W - scw) / 2, "by": (H - sch) / 2, "bg": None}


def _num(v):
    return f"{v:.5f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _kf_expr(it, key, t0, dur, comp=None, scale=1.0, offset=0.0):
    """키프레임 값 → ffmpeg 식 (구간 안 시간 t 기준). comp: 위치처럼 [x,y] 값이면 몇 번째인지."""
    p = param(it, key)

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
            inputs.append(["-loop", "1", "-framerate", "1", "-t", f"{dur + 1.5:.3f}", "-i", str(p)])
            chain += ["setpts=PTS-STARTPTS"] + _color_filters(it)
        else:
            ma, mb = i_mt(it, t_a), i_mt(it, t_b)
            lo, hi = min(ma, mb), max(ma, mb)
            mdur = md.get("dur") or 1e9
            sfps = float(md.get("fps") or 30.0)
            # 반 프레임 일찍 찾아가야 가장 가까운 원본 프레임이 첫 장면이 됨 (소리와 싱크)
            ss = min(max(0.0, lo - 0.5 / sfps), max(0.0, mdur - 0.05))
            pre_pad = max(0.0, -lo) / sp if not it.get("rev") else max(0.0, hi - mdur) / sp
            inputs.append(["-ss", f"{ss:.6f}", "-t", f"{max(0.05, hi - ss) + 0.3:.3f}", "-i", str(p)])
            chain.append("setpts=PTS-STARTPTS")
            hdr = media_hdr(md)
            if hdr:
                chain += tonemap_chain(hdr, 2 * W)
            if it.get("rev"):
                chain += [f"trim=duration={max(0.04, hi - ss):.4f}", "reverse", "setpts=PTS-STARTPTS"]
            if abs(sp - 1) > 1e-6:
                chain.append(f"setpts=PTS/{sp:.5f}")
            chain.append(f"fps={fps}")
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
        fit = _fit(it, md, seq, W, H)
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
    final = lab("v")
    # 자막·타이틀·도형 (구간 시작 시각만큼 밀어서 같은 ASS 사용)
    fc.append(f"[{comp}]format=yuv420p,setsar=1,setpts=PTS+{t0:.4f}/TB,subtitles=subs.ass:fontsdir=fonts,setpts=PTS-STARTPTS[{final}]")
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


def _run_ff(args, cwd, on_frame=None, on_time=None, procs=None, abort=None, enc=None, want_err=False):
    """ffmpeg 실행 (진행률·취소 지원). abort: 이 작업만 멈추는 신호. enc: 그래픽카드 인코더면 그 문제인지 구분.
    want_err: 끝나면 ffmpeg 가 남긴 글(측정값 등)을 돌려줌."""
    if CANCEL.is_set() or (abort is not None and abort.is_set()):
        raise Cancelled()
    with tempfile.TemporaryFile(dir=cwd) as errf:
        p = subprocess.Popen([core.ffmpeg(), "-hide_banner", "-nostats", "-progress", "pipe:1"] + args, cwd=str(cwd),
                             stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
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


def _mix_audio(seq, media, t_lo, t_hi, tmp, trans, progress, abort=None):
    """소리 섞기 (실패·멈춤 때는 열린 임시 파일을 바로 놓아 Windows 에서도 지워지게)."""
    try:
        return _mix_audio_impl(seq, media, t_lo, t_hi, tmp, trans, progress, abort)
    except BaseException as e:
        traceback.clear_frames(e.__traceback__)
        raise


def _mix_audio_impl(seq, media, t_lo, t_hi, tmp, trans, progress, abort):
    """소리 섞기: 트랙별 볼륨·키프레임·페이드·전환·음소거/솔로 → 대사 + 배경음악(말할 때 자동 줄임).
    클립마다 ffmpeg 로 읽는 것은 몇 개씩 동시에 (컷이 많은 롱폼이 훨씬 빨라짐)."""
    import numpy as np
    n = max(1, int(round((t_hi - t_lo) * SR)))
    tracks = {t["id"]: t for t in seq["tracks"] if t["k"] == "a"}
    solo = any(t.get("solo") for t in tracks.values())
    by = {it["id"]: it for it in seq["items"]}
    jobs = []
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
        jobs.append((it, tr, md, p_in, a_lo, a_hi, lo, hi, tgain, tr.get("role", "dialog") != "dialog"))
    dia = mus = None
    try:
        dia = np.memmap(tmp / "dialog.f32", np.float32, "w+", shape=(n, 2))
        mus = np.memmap(tmp / "music.f32", np.float32, "w+", shape=(n, 2)) if any(j[-1] for j in jobs) else None
        lock = threading.Lock()
        my = set()
        stop = threading.Event()

        def halted():
            return CANCEL.is_set() or stop.is_set() or (abort is not None and abort.is_set())

        def one(job):
            it, tr, md, p_in, a_lo, a_hi, lo, hi, tgain, music = job
            if halted():
                raise Cancelled()
            sp = i_sp(it)
            tl_start = i_tl(it, hi if it.get("rev") else lo)  # 디코딩 시작점이 타임라인 어디인지
            af = (["areverse"] if it.get("rev") else []) + _atempo(sp)
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
            bus = mus if music else dia
            pos = int(round((tl_start - t_lo) * SR))
            end_i = int(round((a_hi - t_lo) * SR))
            st, en = it["start"], i_end(it)
            fi, fo = float(it.get("fadeIn") or 0), float(it.get("fadeOut") or 0)
            got = 0
            with tempfile.TemporaryFile(dir=tmp) as errf:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
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
                            bus[a0 + lo_i: a0 + hi_i] += y
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
        # 목소리 보정 (대사 트랙들): 웅웅거림 제거 · 잡음 줄이기 · 크기 고르게
        voice = seq.get("voice") or {}
        D = AFFTDN_DELAY
        vf = (["highpass=f=80"] if voice.get("hp") else []) + \
             ([f"apad=pad_len={D}", f"afftdn=nr={[0, 8, 14, 20][min(3, int(voice.get('nr') or 0))]}:nf=-40", f"atrim=start_sample={D}"] if voice.get("nr") else []) + \
             (["acompressor=threshold=0.08:ratio=3:attack=8:release=160:makeup=2"] if voice.get("comp") else [])
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
        # 말할 때 배경음악 자동 줄이기 (덕킹) → 결과는 대사 버퍼에 그대로 더함 (임시 파일 하나 덜 씀)
        duck = seq.get("duck") or {}
        win = SR // 50  # 20ms
        if mus is not None and duck.get("on"):
            nw = n // win + 1
            rms = np.zeros(nw, np.float32)
            for b in range(0, n, win * 3000):
                seg = np.asarray(dia[b: b + win * 3000])
                k = len(seg) // win
                if k:
                    r = np.sqrt((seg[: k * win] ** 2).mean(axis=(1,)).reshape(k, win).mean(axis=1))
                    rms[b // win: b // win + k] = r
            act = rms > 10 ** (-40 / 20)
            hold = int(0.35 / 0.02)  # 말 사이 짧은 쉼은 그대로 줄인 상태
            act_h = act.copy()
            last = -10 ** 9
            for i in range(nw):
                if act[i]:
                    last = i
                act_h[i] = i - last <= hold
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
        dia = mus = None  # noqa: F841


LOUD_PEAK = -3.5  # 소리 크기 맞추기 전 미리 누르는 최대 크기(dBFS) — 공 차는 소리·효과음처럼 순간만 큰 소리가 있으면
#                   loudnorm 한 번(실시간)으로는 목표까지 못 올림 (최대 크기 제한에 걸려 -17 LUFS 처럼 작게 남음)


def _loud_pre(mix, tmp, lufs, vol, abort=None):
    """소리 크기 맞추기 전처리: 섞은 소리를 한 번 재서(ebur128) 목표까지 모자란 만큼 미리 키우고, 그때 넘치는 순간 소리만 리미터로 누름.
    리미터가 누른 만큼 다시 한 번 재서 더 키움(2번 재기, 소리만이라 빠름). 그다음 loudnorm 은 남은 1dB 안팎만 맞춤 →
    목표 LUFS·최대 -1.5 dBTP 를 함께 지킴. 재지 못하거나 이미 충분하면 [] (예전과 같음)."""
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
    i2 = measure(chain(gain))
    if i2 is not None and lufs - i2 > 0.4:  # 리미터가 눌러 모자란 만큼 한 번 더
        gain = min(gain + (lufs - i2) * 1.15, 30.0)
    return chain(gain)


PRESETS = {
    "youtube": {"label": "유튜브 1080p", "long": (1920, 1080), "shorts": (1080, 1920), "crf": 19, "preset": "veryfast"},
    "hq": {"label": "고화질 4K", "long": (3840, 2160), "shorts": (2160, 3840), "crf": 18, "preset": "veryfast"},
    "small": {"label": "가벼운 720p", "long": (1280, 720), "shorts": (720, 1280), "crf": 23, "preset": "veryfast"},
}


def _is_render_tmp(d):
    """내보내기·러프컷이 쓰던 임시 폴더인지 (이름 + 안의 파일로 확인 → 사용자 폴더는 건드리지 않음)."""
    if not d.is_dir():
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


def export(name, proj, opts, log):
    CANCEL.clear()
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
    proj["media"] = list(media.values())
    bad = [it for it in proj["items"] if it.get("media") not in media or it["out"] - it["in"] <= 1e-3]
    if bad:  # 프로젝트에 없는 미디어·길이가 0인 클립은 빼고 진행
        log(f"  빈 클립 {len(bad)}개는 건너뛰어요")
        drop = {id(x) for x in bad}
        proj["items"] = [it for it in proj["items"] if id(it) not in drop]
    label = re.sub(r'[\\/:*?"<>|]', "", proj.get("name") or ("쇼츠" if fmt == "shorts" else "롱폼")).strip(" .")
    stem = f"{core.adir(name).name}_{label}"
    outputs = []
    total = seq_total(proj)
    t_lo, t_hi = 0.0, total
    rng = opts.get("range")
    if rng and rng[1] - rng[0] > 0.1:
        t_lo, t_hi = max(0.0, float(rng[0])), min(total, float(rng[1]))
        stem += f"_구간_{_tlabel(t_lo)}-{_tlabel(t_hi)}"
    if t_hi - t_lo < 0.05:
        raise RuntimeError("내보낼 내용이 없어요 (타임라인이 비어 있어요)")
    # 같은 이름이 있으면 덮어쓰지 않고 (2), (3)… 을 붙임
    exts = ([".mp4"] if opts.get("video", True) else []) + ([".srt"] if opts.get("srt", True) else []) + (["_premiere.xml"] if opts.get("xml", True) else [])
    base, k = stem, 1
    while any((core.OUT / f"{stem}{e}").exists() for e in exts):
        k += 1
        stem = f"{base} ({k})"
    if opts.get("video", True):
        miss = _missing_media(proj, media, t_lo, t_hi)
        if miss:
            raise RuntimeError("미디어 파일이 없어요 · " + ", ".join(miss[:5]) + " (옮기거나 지웠다면 미디어 탭에서 다시 넣어 주세요)")
    # 자막 파일(SRT)은 '영상에 자막 넣기'와 상관없이 만듦 (유튜브 자막 올리기용) · 구간 내보내기면 구간 안으로 잘라 0초부터
    span = t_hi - t_lo
    caps = []
    for c in timeline_captions(proj):
        s, e = max(0.0, c["start"] - t_lo), min(span, c["end"] - t_lo)
        if e - s >= 1.0 / fps:
            caps.append(dict(c, start=s, end=e))
    side = []  # (파일 이름, 내용) — 영상을 만들면 영상이 다 된 뒤에 씀 (멈추거나 실패하면 짝 잃은 자막·XML 이 안 남음)
    if opts.get("srt", True):
        if caps:
            side.append((f"{stem}.srt", _srt(caps)))
        else:
            log("  자막이 없어서 SRT 파일은 만들지 않았어요")
    if opts.get("xml", True):
        side.append((f"{stem}_premiere.xml", _xmeml(proj, BW, BH, fps, stem, t_lo, t_hi)))

    def write_side():
        for fn, txt in side:
            (core.OUT / fn).write_text(txt, encoding="utf-8")
            outputs.append(fn)

    if not opts.get("video", True):
        write_side()
    else:
        sweep_temp(0)
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
            (tmp / "subs.ass").write_text(build_ass(proj, BW, BH), encoding="utf-8")
            shutil.copytree(FONTS, tmp / "fonts", dirs_exist_ok=True)
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

                def enc(nm):
                    af = [f"volume={float(m.get('volume', 1.0)):.3f}"]
                    if nm:
                        lufs = min(-9.0, max(-24.0, float(m.get("lufs") or -14.0)))
                        af += _loud_pre(mix, tmp, lufs, float(m.get("volume", 1.0)), abort_a)
                        af.append(f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11")
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

            ex_a = ThreadPoolExecutor(1)
            fut = ex_a.submit(audio_job)

            hw = hw_encoder() if opts.get("hw", True) else None
            if hw and (hw, W, H) in _HW.get("bad", set()):
                hw = None

            def render(k, enc, abort, procs):
                if abort.is_set():
                    return
                f0, f1 = segs[k]
                args, final, n = _build_segment(proj, media, W, H, fps, f0, f1, trans, tmp, k)

                def on_frame(fr):
                    with lock:
                        done[k] = min(n, fr)
                        prog(2 + 85 * sum(done.values()) / tot_f, f"화면 만드는 중 · 구간 {k + 1}/{len(segs)}" + (" · 그래픽카드" if enc else ""))

                _run_ff(["-y", "-v", "error"] + args + [_fc_opt(), f"fc{k}.txt", "-map", f"[{final}]", "-an", "-frames:v", str(n)] + _venc(enc, pr, W, H, fps)
                        + ["-r", str(fps), "-video_track_timescale", str(fps * 1000), f"seg{k:04d}.mp4"], tmp, on_frame,
                        procs=procs, abort=abort, enc=enc)
                on_frame(n)

            def render_all(enc, workers=None):
                """구간들을 동시에 만들고, 하나라도 실패하면 나머지를 바로 멈춤 (다 기다리지 않음)."""
                done.clear()
                if workers is None:
                    workers = (3 if enc else 2) if (os.cpu_count() or 2) >= 4 and len(segs) > 1 else 1
                abort, procs = threading.Event(), set()
                ex = ThreadPoolExecutor(workers)
                try:
                    futs = [ex.submit(render, k, enc, abort, procs) for k in range(len(segs))]
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
                if getattr(e, "session", False) and len(segs) > 1:  # 동시에 여는 개수 제한 → 하나씩 다시
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
            (tmp / "list.txt").write_text("".join(f"file 'seg{k:04d}.mp4'\n" for k in range(len(segs))), encoding="utf-8")
            # 영상 화면이 다 되면 소리 마무리를 기다림 (wait 로 확인 → 파이썬 3.9·3.10 에서도 시간 초과 예외가 안 남)
            while not wait([fut], timeout=0.4).done:
                prog(87 + 11 * astate["frac"], "소리 마무리 중 · 소리 크기 맞추는 중")
            audio = fut.result()
            prog(98, "마무리 중")
            # 영상·소리를 그대로 합치기만 (다시 인코딩 없음)
            _run_ff(["-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-i", audio.name, "-map", "0:v", "-map", "1:a", "-c", "copy",
                     "-movflags", "+faststart", "final.mp4"], tmp)
            out = core.OUT / f"{stem}.mp4"
            try:  # 다 만들어진 뒤에만 완성본 폴더로 (멈추면 반쪽 파일이 안 남음)
                _replace_retry(tmp / "final.mp4", out, 5)
            except OSError:  # 같은 이름 파일이 다른 프로그램에 열려 있음 → 다른 이름으로라도 꼭 남김
                for i in range(2, 100):
                    alt = core.OUT / f"{stem} ({i}).mp4"
                    if alt.exists():
                        continue
                    try:
                        os.replace(tmp / "final.mp4", alt)
                    except OSError:
                        continue
                    log(f"  같은 이름 파일이 다른 프로그램에 열려 있어서 '{alt.name}'(으)로 저장했어요")
                    out = alt
                    break
                else:
                    raise RuntimeError("완성본을 저장하지 못했어요 (같은 이름 파일이 다른 프로그램에 열려 있어요)")
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
                traceback.print_exc()
            traceback.clear_frames(e.__traceback__)
            err = f"영상을 만들지 못했어요 · {str(e)[-300:]}"
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
            for _ in range(5):  # Windows: 잠깐 잡혀 있으면 조금 뒤 다시
                shutil.rmtree(tmp, ignore_errors=True)
                if not tmp.exists():
                    break
                time.sleep(0.3)
        if err:
            raise RuntimeError(err)
        write_side()
    log(f"  내보내기 완료 · {', '.join(outputs)}")
    return outputs
