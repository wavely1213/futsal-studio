"""편집실: 영상 정보·파형·썸네일·프로젝트 저장(멀티 트랙)·자동 추천·내보내기(영상/프리미어 XML/SRT)."""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import core

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

def media_path(f, src="videos"):
    return (core.VIDEOS if src == "videos" else ASSETS) / Path(f).name


def _cache_dir(f, src="videos"):
    return core.adir(f) if src == "videos" else core.ANALYSIS / "_media" / (Path(f).stem.rstrip(" .") or "media")


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
    return {"duration": round(dur, 3), "width": w, "height": h, "fps": float(f[1]) if f else 30.0,
            "audio": "Audio:" in err, "kind": kind}


def media_info(name):
    i = probe(core.VIDEOS / name)
    return {k: i[k] for k in ("duration", "width", "height", "fps")}


def media_entry(f, src="videos", mid=None):
    p = media_path(f, src)
    if not p.exists():
        raise FileNotFoundError(f"파일을 찾지 못했어요 · {f}")
    i = probe(p)
    return {"id": mid or _nid(), "kind": i["kind"], "src": src, "file": p.name, "dur": i["duration"] if i["kind"] != "image" else 0,
            "w": i["width"], "h": i["height"], "fps": i["fps"], "audio": i["audio"] and i["kind"] != "image",
            "proxy": proxy_path(p.name, src).exists()}


# ---------- 미리보기용 가벼운 파일 (프록시) ----------

def proxy_path(f, src="videos"):
    return _cache_dir(f, src) / "proxy.mp4"


def make_proxy(f, src="videos", log=print):
    """4K·HEVC(아이폰) 영상도 편집실에서 부드럽게: 540p H.264, 짧은 키프레임 간격. 내보내기는 항상 원본으로."""
    p, out = media_path(f, src), proxy_path(f, src)
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = probe(p)["duration"] or 1
    tmp = out.with_suffix(".part.mp4")
    cmd = [core.ffmpeg(), "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", "-i", str(p), "-vf", "scale=-2:540,fps=30",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-ac", "2", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **core.NO_WINDOW)
    for line in proc.stdout:
        if line.startswith(b"out_time_us="):
            try:
                t = int(line[12:].strip() or 0) / 1e6
                core.set_progress(label="미리보기 파일 만드는 중", item=f, pct=min(99, int(t * 100 / dur)), detail=f"{int(t)}초 / {int(dur)}초")
            except ValueError:
                pass
    proc.wait()
    if proc.returncode or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("미리보기 파일을 만들지 못했어요")
    tmp.replace(out)
    log(f"  미리보기 파일 완료 · {Path(f).name}")
    return {"src": src, "file": Path(f).name}


def library():
    """가져올 수 있는 미디어: 보관함 영상 + 편집실로 가져온 파일."""
    vids = [{"src": "videos", "file": v["name"], "size_mb": v["size_mb"], "kind": "video"} for v in core.local_videos()]
    assets = [{"src": "assets", "file": p.name, "size_mb": round(p.stat().st_size / 1e6, 1),
               "kind": "image" if p.suffix.lower() in IMAGE_EXTS else ("audio" if p.suffix.lower() in AUDIO_EXTS else "video")}
              for p in sorted(ASSETS.iterdir()) if p.is_file() and p.suffix.lower() in MEDIA_EXTS]
    return {"videos": vids, "assets": assets}


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
    return media_entry(dest.name, "assets")


def waveform(name, per_sec=50, src="videos"):
    """소리 크기 (0~1) 를 1초에 per_sec 개씩."""
    cache = _cache_dir(name, src) / f"waveform_{per_sec}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import numpy as np
    sr = 8000
    p = subprocess.run([core.ffmpeg(), "-v", "error", "-i", str(media_path(name, src)), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True, **core.NO_WINDOW)
    a = np.abs(np.frombuffer(p.stdout, np.int16).astype(np.float32)) / 32768.0
    hop = sr // per_sec
    n = len(a) // hop
    peaks = a[: n * hop].reshape(n, hop).max(axis=1) if n else np.zeros(0)
    top = float(np.percentile(peaks, 99)) if n else 1.0
    data = {"per_sec": per_sec, "peaks": [round(min(1.0, float(x) / (top or 1)), 3) for x in peaks], "top": round(top, 4)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def thumbs(name, src="videos"):
    """타임라인용 썸네일 띠 (가로로 이어붙인 한 장)."""
    d = _cache_dir(name, src)
    meta = d / "thumbs2.json"  # 2: 가로 16000px 이하로 제한한 버전
    if meta.exists() and (d / "thumbs2.jpg").exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    info = probe(media_path(name, src))
    count = max(1, min(110, int(info["duration"] // 2) or 1))
    interval = max(0.5, info["duration"] / count) if info["duration"] else 1.0
    h = 72
    w = int(round(h * info["width"] / info["height"] / 2) * 2) or 128
    d.mkdir(parents=True, exist_ok=True)
    vf = f"fps=1/{interval:.3f},scale={w}:{h},tile={count}x1" if info["kind"] == "video" else f"scale={w}:{h}"
    core.run([core.ffmpeg(), "-y", "-v", "error", "-i", str(media_path(name, src)), "-vf", vf, "-frames:v", "1", "-q:v", "5",
              str(d / "thumbs2.jpg")])
    data = {"interval": interval, "w": w, "h": h, "count": count if info["kind"] == "video" else 1}
    meta.write_text(json.dumps(data), encoding="utf-8")
    return data


def thumbs_file(name, src="videos"):
    return _cache_dir(name, src) / "thumbs2.jpg"


def media_bundle(f, src="videos"):
    """편집실에 미디어를 올릴 때 필요한 정보 한 번에 (파형·썸네일 포함)."""
    e = media_entry(f, src)
    out = {"media": e, "waveform": None, "thumbs": None}
    if e["audio"]:
        out["waveform"] = waveform(e["file"], 50, src)
    if e["kind"] in ("video", "image"):
        out["thumbs"] = thumbs(e["file"], src)
    return out


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
        items += pr
        pos += c["out"] - c["in"]
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


def auto_sequences(name, info, style=None, kinds=("long", "shorts")):
    """1차 가편집: 롱폼 군더더기 정리본 + 쇼츠 추천 구간별 편집본.
    style: style.edit_params() 결과 (말 사이 공백·줌 컷·자막 위치/색·소리 크기) — 있으면 그 스타일대로."""
    st = style or {}
    rec = recommend(name, keep_pause=st.get("keepPause"))
    segs = _segments_of(name)
    every, zoom = float(st.get("zoomEvery") or 0), min(1.6, max(1.0, float(st.get("zoomScale") or 1.0)))
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
        seqs.append(_new_seq("롱폼 가편집", "long", _items_from_cuts(_punch(tidy, segs, every), zoom=zoom), captionStyle=cap(LONG_STYLE),
                             layout={"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0},
                             master=dict(master), captionsOn=caps_on))
    if "shorts" in kinds:
        for i, r in enumerate(rec["shorts"], 1):
            items = _items_from_cuts(_punch(r["cuts"], segs, every), 0.3, zoom=zoom)
            length = sum(c["out"] - c["in"] for c in r["cuts"])
            seqs.append(_new_seq(f"쇼츠 {i} · {r['title'][:14]}", "shorts", items, captionStyle=cap(SHORTS_STYLE),
                                 layout=dict(BOX_LAYOUT), master=dict(master), captionsOn=caps_on,
                                 titles=[{"id": _nid(), "text": _hook(r), "start": 0.0, "dur": round(length, 2), "style": dict(TITLE_STYLE)}]))
    return seqs


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
            seq[k] = proj.pop(k)
        proj["sequences"] = [seq] + auto_sequences(name, proj["info"])
        proj["active"] = seq["id"]
    for s in proj["sequences"]:
        migrate_seq(s)
    media = proj.setdefault("media", [])
    if not any(m["id"] == "main" for m in media):
        i = proj["info"]
        media.insert(0, {"id": "main", "kind": "video", "src": "videos", "file": name, "dur": i["duration"], "w": i["width"],
                         "h": i["height"], "fps": i.get("fps", 30.0), "audio": True})
    for m in media:  # 미리보기 파일이 있는지 매번 다시 확인
        if m.get("kind") == "video":
            m["proxy"] = proxy_path(m["file"], m.get("src", "videos")).exists()
    proj["v"] = 2
    return proj


def load_project(name):
    p = _ppath(name)
    if p.exists():
        proj = json.loads(p.read_text(encoding="utf-8"))
        was = proj.get("v")
        proj = migrate_project(name, proj)
        if was != 2:
            save_project(name, proj)
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
        "captions": [{"id": _nid(), "start": s["start"], "end": s["end"], "text": s["text"]} for s in segs if s["text"]],
        "sequences": seqs,
        "active": seqs[0]["id"],
    }
    proj = migrate_project(name, proj)
    save_project(name, proj)
    return proj


def save_project(name, proj):
    p = _ppath(name)
    p.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
    # 자동 백업: 5분마다 한 벌씩, 최근 10개까지
    bdir = PROJECTS / "backup"
    bdir.mkdir(exist_ok=True)
    olds = sorted(bdir.glob(f"{p.stem}__*.json"))
    if not olds or time.time() - olds[-1].stat().st_mtime > 300:
        shutil.copy2(p, bdir / f"{p.stem}__{time.strftime('%Y%m%d_%H%M%S')}.json")
        for o in olds[:-9]:
            o.unlink(missing_ok=True)


def backups(name):
    """자동 백업 목록 (최근 것부터)."""
    stem = _ppath(name).stem
    out = []
    for f in sorted((PROJECTS / "backup").glob(f"{stem}__*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            n = len(d.get("sequences") or [])
        except Exception:
            n = 0
        out.append({"file": f.name, "time": time.strftime("%m월 %d일 %H:%M", time.localtime(f.stat().st_mtime)), "sequences": n})
    return out


def restore_backup(name, fname):
    f = (PROJECTS / "backup" / Path(fname).name)
    if not f.exists() or not f.name.startswith(_ppath(name).stem + "__"):
        raise FileNotFoundError("백업을 찾지 못했어요")
    return migrate_project(name, json.loads(f.read_text(encoding="utf-8")))


def freeze_frame(src, f, t):
    """정지 화면(프레임 고정): 그 순간을 PNG 로 저장해 가져온 미디어로."""
    p = media_path(f, src)
    out = ASSETS / f"정지_{Path(f).stem[:30].strip(' .')}_{t:.2f}.png"
    if not out.exists():
        r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(p), "-frames:v", "1", str(out)])
        if r.returncode or not out.exists():
            raise RuntimeError("정지 화면을 만들지 못했어요")
    return media_entry(out.name, "assets")


# ---------- 자동 추천 (AI 없이 규칙 기반) ----------

FILLERS = {"아", "어", "음", "자", "네", "예", "그", "막", "뭐", "응", "이제", "그냥", "흠", "아니", "저기"}
KEYWORDS = {"팁": 4, "꿀팁": 6, "중요": 4, "핵심": 4, "강조": 3, "비결": 4, "방법": 3, "포인트": 3, "무조건": 3,
            "절대": 3, "실수": 3, "차이": 2, "첫 번째": 2, "첫번째": 2, "잘하": 2, "어떻게": 2, "비밀": 4, "원리": 3,
            "퍼스트 터치": 3, "기술": 2, "프로": 1, "국가대표": 3, "레전드": 2, "힘들": 1, "?": 1}


def _norm(t):
    return re.sub(r"[\s.,!?~…]+", "", t)


def recommend(name, min_len=20.0, max_len=55.0, n=3, keep_pause=None):
    """keep_pause: 말 사이 이보다 길게 쉬면 자름 (스타일). 없으면 기본(약 1.2초)."""
    if keep_pause:
        kp = max(0.08, float(keep_pause))
        pre, post = min(0.15, kp * 0.4), min(0.25, kp * 0.6)
        gap_s = gap_l = max(0.0, kp - pre - post)
    else:
        pre, post, gap_s, gap_l = 0.15, 0.25, 0.6, 0.8
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
    return {"shorts": picked, "tidy": tidy, "junk": len(junk), "segments": len(segs)}


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


def trans_range(seq, tr):
    by = {it["id"]: it for it in seq["items"]}
    a, b, d = by.get(tr.get("a")), by.get(tr.get("b")), max(0.04, float(tr.get("dur") or 1.0))
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
        out.append(tr)
    return out


def track_state(seq, track_id, t, trans):
    """시간 t 에 트랙에서 보이는 것: None | ("item", it) | ("trans", tr, a, b, t0, t1)."""
    by = {it["id"]: it for it in seq["items"]}
    for tr in trans:
        if tr["track"] != track_id:
            continue
        r = trans_range(seq, tr)
        if r and r[0] - 1e-6 <= t < r[1] - 1e-6:
            return ("trans", tr, by.get(tr.get("a")), by.get(tr.get("b")), r[0], r[1])
    for it in seq["items"]:
        if it["track"] == track_id and it["start"] - 1e-6 <= t < i_end(it) - 1e-6:
            return ("item", it)
    return None


def timeline_captions(proj):
    """자막을 타임라인 시간으로 (V1 의 원본 클립을 따라감, 잘린 부분은 빠지고 여러 클립에 걸치면 나뉨)."""
    res = []
    total = seq_total(proj)
    vids = sorted([it for it in proj["items"] if it["track"] == "V1" and it["media"] == "main" and not it.get("rev")],
                  key=lambda x: x["start"])
    for cap in proj["captions"]:
        for it in vids:
            a, b = max(cap["start"], it["in"]), min(cap["end"], it["out"])
            if b - a > 0.05:
                s, e = i_tl(it, a), min(i_tl(it, b), total)
                if e - s > 0.04:
                    res.append({"start": s, "end": e, "text": cap["text"]})
    return sorted(res, key=lambda x: x["start"])


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


def _effect_tags(effect, x, y, dur):
    if effect == "fade":
        return r"{\fad(150,120)}"
    if effect == "pop":
        return r"{\fscx70\fscy70\t(0,110,\fscx108\fscy108)\t(110,190,\fscx100\fscy100)}"
    if effect == "slide":
        return rf"{{\move({x},{y + 40},{x},{y},0,180)\fad(120,0)}}"
    return ""


def _karaoke(text, dur):
    words = text.split()
    total = sum(len(w) for w in words) or 1
    cs = int(dur * 100)
    return " ".join(rf"{{\kf{max(1, int(cs * len(w) / total))}}}{w}" for w in words)


# ASS 글자 크기는 줄 높이(위+아래 여백) 기준이라 화면(CSS) 글자보다 작게 나옴 → Pretendard 비율만큼 키워 미리보기와 같게
FONT_K = 1.194


def build_ass(proj, W, H):
    st = proj["captionStyle"]

    def style_line(name, s):
        font = "Pretendard Black" if s["weight"] == "Black" else "Pretendard"
        bold = 0 if s["weight"] == "Black" else -1
        if s.get("bgOn"):
            border, outline, ocol = 3, max(6, s["strokeW"]), _ass_color(s["bg"], 1 - s["bgOpacity"])
        else:
            border, outline, ocol = 1, s["strokeW"], _ass_color(s["stroke"])
        prim = _ass_color(s["highlight"] if s["effect"] == "karaoke" else s["fill"])
        sec = _ass_color(s["fill"])
        return (f"Style: {name},{font},{round(s['size'] * FONT_K)},{prim},{sec},{ocol},{ocol},{bold},0,0,0,100,100,0,0,"
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

    def event(style, s, start, end, text):
        al = s.get("align", "center")
        an = {"left": 1, "right": 3}.get(al, 2)
        x, y = int(W * s.get("x", 0.5)), int(H * s["y"])
        tags = rf"{{\an{an}\pos({x},{y})}}" if s["effect"] != "slide" else rf"{{\an{an}}}"
        body = _karaoke(text, end - start) if s["effect"] == "karaoke" else text.replace("\n", r"\N")
        eff = _effect_tags(s["effect"], x, y, end - start)
        lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{tags}{eff}{body}")

    if proj.get("captionsOn", True):
        for c in timeline_captions(proj):
            event("Cap", st, c["start"], c["end"], c["text"])
    for t in proj["titles"]:
        if t["start"] < tl_total:
            event(f"T{t['id']}", t["style"], t["start"], min(tl_total, t["start"] + t["dur"]), t["text"])
    return "\n".join(lines) + "\n"


def _srt(caps):
    out = []
    for i, c in enumerate(caps, 1):
        out.append(f"{i}\n{core._ts(c['start'])} --> {core._ts(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


# ---------- 프리미어 XML (FCP7) ----------

def _xmeml(proj, W, H, fps, stem):
    """프리미어 프로에서 '가져오기'로 여는 XML (FCP7 형식, 트랙·속도 포함)."""
    tb = round(fps)
    ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    media = {m["id"]: m for m in proj["media"]}
    seen, fid = set(), {}
    total_f = int(round(seq_total(proj) * fps))

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
        its = sorted([it for it in proj["items"] if it["track"] == tr["id"]], key=lambda x: x["start"])
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
    CANCEL.set()
    for p in list(_PROCS):
        try:
            p.kill()
        except OSError:
            pass


class Cancelled(Exception):
    pass


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
    if fit == "auto":  # 위 트랙: 영상은 화면에 맞춤, 이미지는 작으면 원래 크기
        fit = "native" if md["kind"] == "image" and sw * ref <= W and sh * ref <= H else "contain"
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
    if abs(sat - 100) > 1e-6:
        s = sat / 100  # SVG feColorMatrix saturate 와 같은 행렬
        m = [0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s, 0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s,
             0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]
        out.append("format=rgb24,colorchannelmixer=" + ":".join(f"{n}={v:.4f}" for n, v in zip(
            ("rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb"), m)))
    if abs(vib) > 1e-6:
        out.append(f"vibrance=intensity={vib / 100:.3f}")
    return out


def _segments(seq, t_lo, t_hi, fps, trans):
    """장면이 바뀌는 지점마다 나눈 구간 목록 [(f0, f1)]."""
    vt = {t["id"] for t in seq["tracks"] if t["k"] == "v" and not t.get("hide")}
    pts = {t_lo, t_hi}
    rev_items = []
    for it in seq["items"]:
        if it["track"] in vt:
            pts.update((it["start"], i_end(it)))
            if it.get("rev"):
                rev_items.append(it)
    for tr in trans:
        r = trans_range(seq, tr)
        if r and tr["track"] in vt:
            pts.update(r)
            if tr.get("type") in ("black", "white") and tr.get("a") and tr.get("b"):
                pts.add((r[0] + r[1]) / 2)
    for it in rev_items:  # 거꾸로 재생은 메모리를 많이 써서 2초씩 나눔
        t = it["start"]
        while t < i_end(it):
            pts.add(t)
            t += 2.0
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

    def add_input(it, t_a, t_b):
        """아이템의 [t_a, t_b] (타임라인) 를 정확히 n 프레임으로 → 라벨."""
        md = media[it["media"]]
        p = media_path(md["file"], md["src"])
        if not p.exists():
            raise RuntimeError(f"미디어 파일이 없어요 · {md['file']}")
        idx = len(inputs)
        sp = i_sp(it)
        chain = []
        if md["kind"] == "image":
            inputs.append(["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 0.5:.3f}", "-i", str(p)])
            chain += ["setpts=PTS-STARTPTS", f"fps={fps}"]
        else:
            ma, mb = i_mt(it, t_a), i_mt(it, t_b)
            lo, hi = min(ma, mb), max(ma, mb)
            mdur = md.get("dur") or 1e9
            ss = min(max(0.0, lo), max(0.0, mdur - 0.05))
            pre = max(0.0, -lo) / sp if not it.get("rev") else max(0.0, hi - mdur) / sp
            inputs.append(["-ss", f"{ss:.3f}", "-t", f"{max(0.05, hi - ss) + 0.3:.3f}", "-i", str(p)])
            chain.append("setpts=PTS-STARTPTS")
            if it.get("rev"):
                chain += [f"trim=duration={max(0.04, hi - ss):.4f}", "reverse", "setpts=PTS-STARTPTS"]
            if abs(sp - 1) > 1e-6:
                chain.append(f"setpts=PTS/{sp:.5f}")
            chain.append(f"fps={fps}")
            if pre > 1e-3:
                chain.append(f"tpad=start_mode=clone:start_duration={pre:.4f}")
        chain += [f"tpad=stop_mode=clone:stop_duration={dur + 1:.3f}", f"trim=end_frame={n}", "setpts=PTS-STARTPTS"]
        chain += _color_filters(it)
        out = lab("s")
        fc.append(f"[{idx}:v]" + ",".join(chain) + f"[{out}]")
        return out

    def layer(it, t_a, t_b):
        """아이템 → ('full', 라벨) 화면 전체 / ('ov', 라벨, x식, y식) 겹치기."""
        md = media[it["media"]]
        src = add_input(it, t_a, t_b)
        fit = _fit(it, md, seq, W, H)
        bw, bh, bx, by = fit["bw"], fit["bh"], fit["bx"], fit["by"]
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
        bg = fit["bg"]
        fg = lab("f")
        if bg == "blur":
            s1, s2 = lab("b"), lab("c")
            fc.append(f"[{src}]split[{s1}][{s2}]")
            bgl = lab("g")
            qw, qh = _even(W / 4), _even(H / 4)  # 1/4 크기에서 흐리게 → 키움 (훨씬 빠름)
            fc.append(f"[{s1}]scale={qw}:{qh}:force_original_aspect_ratio=increase,crop={qw}:{qh},boxblur=6:3,scale={W}:{H},eq=brightness=-0.08,setsar=1[{bgl}]")
            src = s2
        elif bg == "box":
            bgl = lab("g")
            fc.append(f"color=c=0x{fit['bar'].lstrip('#')}:s={W}x{H}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n}[{bgl}]")
        chain = [fit["chain"], "setsar=1"]
        full_plain = not motion and (bg is not None or (bw == W and bh == H and it["track"] == "V1" and md["kind"] != "image"))
        if not motion and bg is None and it["track"] == "V1" and md["kind"] != "image" and not (bw == W and bh == H):
            chain.append(f"pad={W}:{H}:{int(bx)}:{int(by)}:color=black")  # 가로 영상 맞춤(위아래/좌우 검정)
            full_plain = True
        S = f"({sx})"
        R = f"({rx})"
        if motion:
            chain.append("format=rgba")
            if rot:
                D = _even(math.hypot(bw, bh) + 2)
                chain.append(f"rotate=a='{rx}':ow={D}:oh={D}:c=black@0")
                fw, fh = D, D
            else:
                fw, fh = bw, bh
            if s_anim:
                chain.append(f"scale=w='max(2,{fw}*{S})':h='max(2,{fh}*{S})':eval=frame")
            elif abs(float(sx) - 1) > 1e-6:
                chain.append(f"scale={_even(fw * float(sx))}:{_even(fh * float(sx))}")
            # 기준점이 위치에 오도록: 중심 = 기준점 + 이동 + R·S·(중심-기준점)
            dx, dy = cx - ax, cy - ay
            if rot:
                X = f"({ax:.2f}+({px})+{S}*(cos({R})*{dx:.2f}-sin({R})*{dy:.2f})-{fw}*{S}/2)"
                Y = f"({ay:.2f}+({py})+{S}*(sin({R})*{dx:.2f}+cos({R})*{dy:.2f})-{fh}*{S}/2)"
            else:
                X = f"({ax:.2f}+({px})+{S}*{dx:.2f}-{fw}*{S}/2)"
                Y = f"({ay:.2f}+({py})+{S}*{dy:.2f}-{fh}*{S}/2)"
        else:
            X, Y = f"{bx:.2f}", f"{by:.2f}"
        if has_op:
            if "format=rgba" not in chain:
                chain.append("format=rgba")
            if op_anim:  # 프레임마다 불투명도 명령
                cmd = f"op{k_seg}_{cnt['l']}.cmd"
                rows = [f"{f / fps:.4f} colorchannelmixer@o{cnt['l']} aa {float(kf_at(op, i_mt(it, t0 + f / fps))) / 100:.4f};" for f in range(n)]
                (tmp / cmd).write_text("\n".join(rows) + "\n", encoding="utf-8")
                chain += [f"sendcmd=f={cmd}", f"colorchannelmixer@o{cnt['l']}=aa={op_const:.4f}"]
            else:
                chain.append(f"colorchannelmixer=aa={op_const:.4f}")
        fc.append(f"[{src}]" + ",".join(chain) + f"[{fg}]")
        if bg:
            ly = lab("y")
            fc.append(f"[{bgl}][{fg}]overlay=x='{X}':y='{Y}':eval=frame:format=auto,format=yuv420p[{ly}]")
            return ("full", ly)
        if full_plain and not has_op:
            return ("full", fg)
        return ("ov", fg, X, Y)

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
        fc.append(f"[{comp}][{lay[1]}]overlay=x='{x}':y='{y}':eval=frame:format=auto,format=yuv420p[{out}]")
        return out

    def base():
        b = lab("k")
        fc.append(f"color=c=black:s={W}x{H}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n},format=yuv420p[{b}]")
        return b

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
        below = comp or base()
        c1, c2 = lab("x"), lab("x")
        fc.append(f"[{below}]split[{c1}][{c2}]")
        X = put(c1, layer(a, t0, t0 + dur)) if a else c1
        Y = put(c2, layer(b, t0, t0 + dur)) if b else c2
        Xf, Yf = lab("p"), lab("q")
        fc.append(f"[{X}]format=yuv420p,setsar=1[{Xf}]")
        fc.append(f"[{Y}]format=yuv420p,setsar=1[{Yf}]")
        out = lab("t")
        whole = abs(t0 - ts) < 0.5 / fps and abs(t0 + dur - te) < 0.5 / fps
        if whole and typ in ("dissolve", "wipe"):  # 전환 전체가 한 구간이면 빠른 xfade
            fc.append(f"[{Xf}][{Yf}]xfade=transition={'fade' if typ == 'dissolve' else 'wipeleft'}:duration={dur:.4f}:offset=0[{out}]")
        elif typ == "wipe":
            fc.append(f"[{Xf}][{Yf}]blend=all_expr='if(gte(X,W*(1-{P})),B,A)'[{out}]")
        elif typ in ("black", "white"):
            mid, col = lab("m"), lab("w")
            fc.append(f"[{Xf}][{Yf}]blend=all_expr='if(lt({P},0.5),A,B)'[{mid}]")
            fc.append(f"color=c={typ}:s={W}x{H}:r={fps}:d={dur + 0.1:.3f},trim=end_frame={n},format=yuv420p[{col}]")
            fc.append(f"[{mid}][{col}]blend=all_expr='A*(1-(1-abs(2*{P}-1)))+B*(1-abs(2*{P}-1))'[{out}]")
        else:
            fc.append(f"[{Xf}][{Yf}]blend=all_expr='A*(1-{P})+B*{P}'[{out}]")
        comp = out
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


def _run_ff(args, cwd, on_frame=None):
    """ffmpeg 실행 (진행률·취소 지원)."""
    if CANCEL.is_set():
        raise Cancelled()
    errf = tempfile.TemporaryFile(dir=cwd)
    p = subprocess.Popen([core.ffmpeg(), "-hide_banner", "-nostats", "-progress", "pipe:1"] + args, cwd=str(cwd),
                         stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
    _PROCS.add(p)
    try:
        for line in p.stdout:
            if on_frame and line.startswith(b"frame="):
                try:
                    on_frame(int(line[6:].strip() or 0))
                except ValueError:
                    pass
        p.wait()
    finally:
        _PROCS.discard(p)
    if CANCEL.is_set():
        raise Cancelled()
    if p.returncode:
        errf.seek(0)
        msg = errf.read().decode("utf-8", "replace")
        raise RuntimeError(msg[-600:])
    errf.close()


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


def _mix_audio(seq, media, t_lo, t_hi, tmp, trans, progress):
    """소리 섞기: 트랙별 볼륨·키프레임·페이드·전환·음소거/솔로 → 대사 + 배경음악(말할 때 자동 줄임)."""
    import numpy as np
    n = max(1, int(round((t_hi - t_lo) * SR)))
    dia = np.memmap(tmp / "dialog.f32", np.float32, "w+", shape=(n, 2))
    mus = np.memmap(tmp / "music.f32", np.float32, "w+", shape=(n, 2))
    tracks = {t["id"]: t for t in seq["tracks"] if t["k"] == "a"}
    solo = any(t.get("solo") for t in tracks.values())
    items = [it for it in seq["items"] if it["track"] in tracks]
    used = {"music": False}
    for k, it in enumerate(items):
        progress(k / max(1, len(items)))
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
            r = trans_range(seq, tt)
            if not r:
                continue
            a_lo, a_hi = min(a_lo, r[0]), max(a_hi, r[1])
            tgain.append((r, "out" if tt.get("a") == it["id"] else "in", tt.get("type", "cpower")))
        a_lo, a_hi = max(a_lo, t_lo), min(a_hi, t_hi)
        if a_hi - a_lo < 1e-3:
            continue
        sp = i_sp(it)
        ma, mb = i_mt(it, a_lo), i_mt(it, a_hi)
        lo, hi = max(0.0, min(ma, mb)), min(md.get("dur") or 1e9, max(ma, mb))
        if hi - lo < 1e-3:
            continue
        # 디코딩 시작점이 타임라인 어디인지
        tl_start = i_tl(it, hi if it.get("rev") else lo)
        af = (["areverse"] if it.get("rev") else []) + _atempo(sp)
        cmd = [core.ffmpeg(), "-v", "error", "-ss", f"{lo:.4f}", "-t", f"{hi - lo:.4f}", "-i",
               str(media_path(md["file"], md["src"])), "-vn", "-af", ",".join(af) or "anull", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **core.NO_WINDOW)
        _PROCS.add(p)
        lvl = param(it, "level")
        g_static = 10 ** ((float(it.get("gain") or 0) + float(tr.get("vol") or 0)) / 20)
        bus = dia if tr.get("role", "dialog") == "dialog" else mus
        if bus is mus:
            used["music"] = True
        pos = int(round((tl_start - t_lo) * SR))
        end_i = int(round((a_hi - t_lo) * SR))
        st, en = it["start"], i_end(it)
        try:
            while True:
                if CANCEL.is_set():
                    raise Cancelled()
                raw = p.stdout.read(SR * 8 * 5)
                if not raw:
                    break
                x = np.frombuffer(raw[: len(raw) // 8 * 8], np.float32).reshape(-1, 2)
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
                fi, fo = float(it.get("fadeIn") or 0), float(it.get("fadeOut") or 0)
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
                bus[a0 + lo_i: a0 + hi_i] += x * g[:, None].astype(np.float32)
        finally:
            p.stdout.close()
            p.wait()
            _PROCS.discard(p)
    # 목소리 보정 (대사 트랙들): 웅웅거림 제거 · 잡음 줄이기 · 크기 고르게
    voice = seq.get("voice") or {}
    vf = (["highpass=f=80"] if voice.get("hp") else []) + \
         ([f"afftdn=nr={[0, 8, 14, 20][min(3, int(voice.get('nr') or 0))]}:nf=-40"] if voice.get("nr") else []) + \
         (["acompressor=threshold=0.08:ratio=3:attack=8:release=160:makeup=2"] if voice.get("comp") else [])
    if vf:
        progress(0.98)
        dia.flush()
        r = core.run([core.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(tmp / "dialog.f32"),
                      "-af", ",".join(vf), "-f", "f32le", "-ar", str(SR), "-ac", "2", str(tmp / "dialog2.f32")])
        if r.returncode == 0:
            cl = np.memmap(tmp / "dialog2.f32", np.float32, "r")
            m = min(n, len(cl) // 2)
            for b in range(0, m, SR * 10):
                e = min(m, b + SR * 10)
                dia[b:e] = np.asarray(cl[b * 2:e * 2]).reshape(-1, 2)
            del cl
    # 말할 때 배경음악 자동 줄이기 (덕킹)
    duck = seq.get("duck") or {}
    out = np.memmap(tmp / "mix.f32", np.float32, "w+", shape=(n, 2))
    win = SR // 50  # 20ms
    if duck.get("on") and used["music"]:
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
            out[b:e] = dia[b:e] + mus[b:e] * gi[:, None]
    else:
        for b in range(0, n, SR * 10):
            out[b: b + SR * 10] = dia[b: b + SR * 10] + mus[b: b + SR * 10]
    out.flush()
    del dia, mus, out
    return tmp / "mix.f32"


PRESETS = {
    "youtube": {"label": "유튜브 1080p", "long": (1920, 1080), "shorts": (1080, 1920), "crf": 19, "preset": "veryfast"},
    "hq": {"label": "고화질 4K", "long": (3840, 2160), "shorts": (2160, 3840), "crf": 18, "preset": "veryfast"},
    "small": {"label": "가벼운 720p", "long": (1280, 720), "shorts": (720, 1280), "crf": 23, "preset": "veryfast"},
}


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
                         "w": i["width"], "h": i["height"], "audio": True}
    proj["media"] = list(media.values())
    bad = [it for it in proj["items"] if it.get("media") not in media or it["out"] - it["in"] <= 1e-3]
    if bad:  # 미디어가 없거나 길이가 0인 클립은 빼고 진행
        log(f"  빠진 미디어·빈 클립 {len(bad)}개는 건너뛰어요")
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
        stem += "_구간"
    if t_hi - t_lo < 0.05:
        raise RuntimeError("내보낼 내용이 없어요 (타임라인이 비어 있어요)")
    caps = [dict(c, start=c["start"] - t_lo, end=c["end"] - t_lo) for c in timeline_captions(proj)
            if c["end"] > t_lo and c["start"] < t_hi] if proj.get("captionsOn", True) else []

    if opts.get("srt", True):
        (core.OUT / f"{stem}.srt").write_text(_srt(caps), encoding="utf-8")
        outputs.append(f"{stem}.srt")
    if opts.get("xml", True):
        (core.OUT / f"{stem}_premiere.xml").write_text(_xmeml(proj, BW, BH, fps, stem), encoding="utf-8")
        outputs.append(f"{stem}_premiere.xml")

    if opts.get("video", True):
        tmp = Path(tempfile.mkdtemp(dir=core.OUT))
        t_start = time.time()
        state = {"pct": 0.0}

        def prog(pct, detail):
            state["pct"] = pct
            el = time.time() - t_start
            eta = el / pct * (100 - pct) if pct > 2 else None
            core.set_progress(label="내보내는 중", item=name, step="", pct=int(pct), detail=detail,
                              eta=int(eta) if eta is not None else None)

        try:
            trans = valid_trans(proj)
            (tmp / "subs.ass").write_text(build_ass(proj, BW, BH), encoding="utf-8")
            shutil.copytree(FONTS, tmp / "fonts", dirs_exist_ok=True)
            segs = _segments(proj, t_lo, t_hi, fps, trans)
            tot_f = sum(b - a for a, b in segs) or 1
            done = {}
            lock = threading.Lock()
            prog(1, "준비 중")

            hw = hw_encoder() if opts.get("hw", True) else None

            def render(k, enc):
                f0, f1 = segs[k]
                args, final, n = _build_segment(proj, media, W, H, fps, f0, f1, trans, tmp, k)

                def on_frame(fr):
                    with lock:
                        done[k] = min(n, fr)
                        prog(2 + 83 * sum(done.values()) / tot_f, f"화면 만드는 중 · 구간 {k + 1}/{len(segs)}" + (" · 그래픽카드" if enc else ""))

                _run_ff(["-y"] + args + [_fc_opt(), f"fc{k}.txt", "-map", f"[{final}]", "-an", "-frames:v", str(n)] + _venc(enc, pr, W, H, fps)
                        + ["-r", str(fps), "-video_track_timescale", str(fps * 1000), f"seg{k:04d}.mp4"], tmp, on_frame)
                on_frame(n)

            def render_all(enc):
                done.clear()
                workers = (3 if enc else 2) if (os.cpu_count() or 2) >= 4 and len(segs) > 1 else 1
                with ThreadPoolExecutor(workers) as ex:
                    for f in [ex.submit(render, k, enc) for k in range(len(segs))]:
                        f.result()

            try:
                render_all(hw)
            except RuntimeError:
                if not hw or CANCEL.is_set():
                    raise
                log(f"  그래픽카드 인코딩({hw})이 안 돼서 일반 방식으로 다시 만들어요")
                _HW["enc"] = None
                for f in tmp.glob("seg*.mp4"):
                    f.unlink(missing_ok=True)
                render_all(None)
            (tmp / "list.txt").write_text("".join(f"file 'seg{k:04d}.mp4'\n" for k in range(len(segs))), encoding="utf-8")
            prog(86, "소리 섞는 중")
            mix = _mix_audio(proj, media, t_lo, t_hi, tmp, trans, lambda x: prog(86 + 8 * x, "소리 섞는 중"))
            prog(95, "마무리 · 소리 크기 맞추는 중")
            af = [f"volume={float(proj['master'].get('volume', 1.0)):.3f}"]
            if proj["master"].get("normalize", True):
                lufs = min(-9.0, max(-24.0, float(proj["master"].get("lufs") or -14.0)))
                af.append(f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11")
            out = core.OUT / f"{stem}.mp4"
            _run_ff(["-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", mix.name,
                     "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", ",".join(af), "-c:a", "aac", "-b:a", "192k",
                     "-ar", "48000", "-t", f"{t_hi - t_lo:.3f}", "-movflags", "+faststart", "final.mp4"], tmp)
            try:  # 다 만들어진 뒤에만 완성본 폴더로 (멈추면 반쪽 파일이 안 남음)
                os.replace(tmp / "final.mp4", out)
            except OSError:
                raise RuntimeError("같은 이름의 완성본 파일이 다른 프로그램에서 열려 있어요. 닫고 다시 내보내 주세요")
            outputs.insert(0, out.name)
            log(f"  영상 길이 {t_hi - t_lo:.1f}초 · {W}×{H} · {fps}fps · {time.time() - t_start:.0f}초 걸림" + (f" · 그래픽카드({_HW['enc']})" if hw and _HW.get("enc") else ""))
        except Cancelled:
            log("  내보내기를 멈췄어요")
            raise RuntimeError("내보내기를 멈췄어요")
        except RuntimeError as e:
            raise RuntimeError(f"영상을 만들지 못했어요 · {str(e)[-300:]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    log(f"  내보내기 완료 · {', '.join(outputs)}")
    return outputs
