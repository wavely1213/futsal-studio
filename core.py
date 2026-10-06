"""풋살사관학교 편집도우미 — 핵심 기능 (목록·다운로드·분석·러프컷·업데이트)."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG = json.loads((APP_DIR / "config.json").read_text(encoding="utf-8"))
VERSION = (APP_DIR / "version.txt").read_text(encoding="utf-8").strip()
WORK = Path(CONFIG.get("workspace") or (Path.home() / "풋살사관학교_작업")).expanduser()
VIDEOS, ANALYSIS, OUT = WORK / "videos", WORK / "analysis", WORK / "out"
DEFAULT_MANIFEST = "https://raw.githubusercontent.com/wavely1213/futsal-studio/main/manifest.json"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
for d in (VIDEOS, ANALYSIS, OUT):
    d.mkdir(parents=True, exist_ok=True)


# 화면에 보여줄 진행 상태 (app.py 가 /api/state 로 내보냄)
PROGRESS = {}


def set_progress(**kw):
    PROGRESS.clear()
    PROGRESS.update(kw)


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # Windows: 검은 창 안 띄움


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **NO_WINDOW)


# ---------- 목록·다운로드 ----------

def list_videos(kind="videos", cookies_browser=None, url=None):
    """채널 영상 목록 (조회수 순). url을 주면 다른 유튜버 채널, 영상 주소 하나면 그 영상만."""
    import yt_dlp
    opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    from urllib.parse import parse_qs, urlsplit
    opts["noplaylist"] = True  # 영상 주소에 붙은 재생목록(list=)은 펼치지 않음
    url = (url or "").strip()
    if not url:
        target = f"{CONFIG['channel_url'].rstrip('/')}/{kind}"
    else:
        if not re.match(r"https?://", url, re.I):
            if re.match(r"(?:(?:www|m|music)\.)?(?:youtube\.com|youtu\.be)/", url, re.I):
                url = "https://" + url
            else:
                url = "https://www.youtube.com/" + (url if url.startswith("@") else "@" + url)
        p = urlsplit(url)
        host, q = p.netloc.lower(), parse_qs(p.query)
        if host.endswith("youtu.be"):
            vid = p.path.strip("/").split("/")[0] or None
        elif p.path.rstrip("/") == "/watch":
            vid = (q.get("v") or [None])[0]
        else:
            m = re.match(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})", p.path)
            vid = m.group(1) if m else None
        if vid:
            target = f"https://www.youtube.com/watch?v={vid}"  # list=/si=/t= 제거
        elif p.path.rstrip("/") == "/playlist":
            target = url
        else:
            path = re.sub(r"/(videos|shorts|streams|featured|playlists|about|community)$", "", p.path.rstrip("/"))
            target = f"https://www.youtube.com{path}/{kind}"  # ?si= 같은 공유 꼬리 제거
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    ents = info.get("entries") if info.get("entries") is not None else [info]
    flat = []
    for e in ents:  # 탭이 여러 개로 묶여 오면 안쪽 영상까지 펼침
        if e and e.get("_type") == "playlist" and e.get("entries"):
            flat += [x for x in e["entries"] if x]
        elif e:
            flat.append(e)
    rows = [{"id": e["id"], "title": e.get("title") or "", "views": e.get("view_count") or 0,
             "duration": e.get("duration") or 0, "kind": kind} for e in flat if e.get("id") and e.get("_type") != "playlist"]
    return sorted(rows, key=lambda r: r["views"], reverse=True)


def download(ids, log, cookies_browser=None, max_height=1080):
    import yt_dlp

    cur = {"i": 0, "n": len(ids), "vid": None, "streams": {}}

    def hook(d):
        info = d.get("info_dict") or {}
        fmts = info.get("requested_formats") or [info]
        sizes = [f.get("filesize") or f.get("filesize_approx") or 0 for f in fmts]
        fid = info.get("format_id")
        idx = next((k for k, f in enumerate(fmts) if f.get("format_id") == fid), 0)
        if d["status"] == "downloading":
            got = d.get("downloaded_bytes") or 0
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or sizes[idx] or 0
            cur["streams"][idx] = (got, tot)
            done = sum(g for g, _ in cur["streams"].values())
            whole = sum(max(t, sizes[k] if k < len(sizes) else 0) for k, (_, t) in cur["streams"].items())
            whole += sum(sz for k, sz in enumerate(sizes) if k not in cur["streams"])
            pct = min(99, int(done * 100 / whole)) if whole else None
            set_progress(label="보관함에 담는 중", item=cur["vid"], step=f"{cur['i']}/{cur['n']}", pct=pct,
                         detail=f"{done / 1e6:.1f}MB / {whole / 1e6:.1f}MB" + (f" · {d['_speed_str'].strip()}" if d.get("_speed_str") else ""))
        elif d["status"] == "finished" and idx == len(fmts) - 1:
            set_progress(label="보관함에 담는 중", item=cur["vid"], step=f"{cur['i']}/{cur['n']}", pct=99, detail="영상과 소리를 합치는 중")

    opts = {
        "format": f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(VIDEOS / "%(upload_date)s_%(id)s_%(title).60B.%(ext)s"),
        "trim_file_name": 120,
        "download_archive": str(VIDEOS / "archive.txt"),
        "ffmpeg_location": ffmpeg(),
        "quiet": True, "no_warnings": True, "noprogress": True,
        "progress_hooks": [hook],
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    failed = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        for k, vid in enumerate(ids, 1):
            cur.update(i=k, vid=vid, streams={})
            set_progress(label="보관함에 담는 중", item=vid, step=f"{k}/{len(ids)}", pct=None, detail="연결하는 중")
            log(f"보관함에 담는 중 · {vid}")
            try:
                ydl.download([vid if vid.startswith("http") else f"https://www.youtube.com/watch?v={vid}"])
                log("  담기 완료")
            except Exception as e:  # 한 개 실패해도 나머지 계속
                msg = str(e)
                if "not a bot" in msg or "403" in msg:
                    msg = "YouTube가 다운로드를 막았어요. 왼쪽 아래 '업데이트 확인'으로 엔진을 최신으로 바꾸거나, '크롬 로그인 정보로 받기'를 켜고 다시 담아 보세요."
                log(f"  담지 못했어요 · {msg}")
                failed.append(vid)
    return failed


def adir(name):
    """영상별 분석 폴더. Windows 는 폴더 이름 끝의 공백·점을 지워버리므로 미리 제거."""
    return ANALYSIS / (Path(name).stem.rstrip(" .") or "video")


def local_videos():
    out = []
    for v in sorted(VIDEOS.iterdir()):
        if v.suffix.lower() in VIDEO_EXTS:
            done = (adir(v.name) / "transcript_timeline.md").exists()
            out.append({"name": v.name, "size_mb": round(v.stat().st_size / 1e6, 1), "analyzed": done})
    return out


def add_local(paths, log):
    for p in paths:
        src = Path(p)
        if src.suffix.lower() in VIDEO_EXTS and src.exists():
            shutil.copy2(src, VIDEOS / src.name)
            log(f"추가 · {src.name}")


# ---------- 분석 ----------

def _ts(sec):
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def _short(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def _silences(wav, noise_db=-35, min_dur=0.8):
    err = run([ffmpeg(), "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"]).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return [{"start": round(a, 2), "end": round(b, 2)} for a, b in zip(starts, ends)]


def _peaks(wav, top=10):
    err = run([ffmpeg(), "-i", str(wav), "-af",
               "asetnsamples=n=16000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
               "-f", "null", "-"]).stderr
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]
    levels = [float(x) if "inf" not in x else -120.0 for x in re.findall(r"RMS_level=(-?[\d.inf]+)", err)]
    if not levels:
        return []
    median = sorted(levels)[len(levels) // 2]
    picked = []
    for lvl, t in sorted(zip(levels, times), reverse=True):
        if lvl < median + 6:
            break
        if all(abs(t - p["time"]) > 5 for p in picked):
            picked.append({"time": round(t, 1), "rms_db": round(lvl, 1)})
        if len(picked) >= top:
            break
    return sorted(picked, key=lambda p: p["time"])


def analyze_many(names, log, model="large-v3-turbo"):
    return [str(analyze(n, log, model, f"{k}/{len(names)}")) for k, n in enumerate(names, 1)]


def analyze(name, log, model="large-v3-turbo", step="1/1"):
    video = VIDEOS / name
    outdir = adir(name)
    outdir.mkdir(parents=True, exist_ok=True)
    wav = outdir / "audio.wav"
    log(f"편집점 찾는 중 · {name}")
    set_progress(label="편집점 찾는 중", item=name, step=step, pct=None, detail="소리 추출 중")
    r = run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    if r.returncode or not wav.exists():
        raise RuntimeError(f"영상에서 소리를 꺼내지 못했어요 (파일이 깨졌거나 소리가 없는 영상일 수 있어요) · {r.stderr.strip()[-200:]}")

    log("  대사를 받아쓰는 중이에요 (처음 한 번은 준비에 몇 분 걸려요)")
    set_progress(label="편집점 찾는 중", item=name, step=step, pct=None, detail="받아쓰기 준비 중")
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type="int8")
    # PyAV 버전 차이로 인한 오류를 피하려고, ffmpeg로 뽑은 wav를 직접 읽어 넘긴다
    import wave
    import numpy as np
    with wave.open(str(wav), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    segs, info = m.transcribe(audio, language="ko", vad_filter=True)
    total = info.duration or 0
    segments = []
    for s in segs:
        segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        if total:
            set_progress(label="편집점 찾는 중", item=name, step=step, pct=min(99, int(s.end * 100 / total)),
                         detail=f"대사 받아쓰는 중 · {_short(s.end)} / {_short(total)}")
        if len(segments) % 20 == 0:
            log(f"  {_short(s.end)}까지 받아씀")
    set_progress(label="편집점 찾는 중", item=name, step=step, pct=99, detail="컷 후보·하이라이트 찾는 중")
    sil, peaks = _silences(wav), _peaks(wav)
    wav.unlink()

    (outdir / "transcript.json").write_text(json.dumps(segments, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "analysis.json").write_text(json.dumps({"silences": sil, "loud_peaks": peaks}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(outdir / "subtitles.srt", "w", encoding="utf-8") as f:
        for i, s in enumerate(segments, 1):
            f.write(f"{i}\n{_ts(s['start'])} --> {_ts(s['end'])}\n{s['text']}\n\n")
    events = [(s["start"], f"[{_short(s['start'])}] {s['text']}") for s in segments]
    events += [(x["start"], f"[{_short(x['start'])}] ── 무음 {x['end'] - x['start']:.1f}초 (컷 후보)") for x in sil]
    events += [(p["time"], f"[{_short(p['time'])}] ▲ 음량 피크 {p['rms_db']}dB (하이라이트 후보)") for p in peaks]
    with open(outdir / "transcript_timeline.md", "w", encoding="utf-8") as f:
        f.write(f"# 타임라인: {name}\n\n")
        for _, line in sorted(events):
            f.write(line + "\n")
    log(f"  완료 · 대사 {len(segments)}줄 · 컷 후보 {len(sil)}곳 · 하이라이트 {len(peaks)}곳")
    return outdir


def timeline_events(name):
    """화면 표시용: 대사·컷 후보·하이라이트를 시간순 목록으로."""
    d = adir(name)
    if not (d / "transcript.json").exists():
        return []
    segs = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    extra = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
    ev = [{"t": s["start"], "end": s["end"], "type": "speech", "text": s["text"]} for s in segs]
    ev += [{"t": x["start"], "end": x["end"], "type": "silence", "text": f"{x['end'] - x['start']:.1f}초 동안 조용함"} for x in extra["silences"]]
    ev += [{"t": p["time"], "end": p["time"] + 1, "type": "peak", "text": "소리가 갑자기 커짐 (환호·슈팅·웃음 가능성)"} for p in extra["loud_peaks"]]
    return sorted(ev, key=lambda e: e["t"])


def timeline(name):
    p = adir(name) / "transcript_timeline.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------- 러프컷 ----------

def _tc(sec, fps=30):
    frames = int(round(sec * fps))
    h, rem = divmod(frames, 3600 * fps)
    m, rem = divmod(rem, 60 * fps)
    s, f = divmod(rem, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def render(name, spec, log):
    video = VIDEOS / name
    cuts, fmt = spec["cuts"], spec.get("format", "long")
    stem = f"{video.stem.rstrip(' .')}_{fmt}"
    vf = "scale=-2:1920,crop=1080:1920" if fmt == "shorts" else "null"
    tmp = Path(tempfile.mkdtemp(dir=OUT))
    parts = []
    for i, c in enumerate(cuts):
        set_progress(label="러프컷 만드는 중", item=name, step=f"{i + 1}/{len(cuts)}", pct=int(i * 100 / len(cuts)), detail=f"컷 {i + 1} 자르는 중")
        log(f"  컷 {i + 1}/{len(cuts)} · {c['start']}초~{c['end']}초 {c.get('note', '')}")
        part = tmp / f"p{i:03d}.mp4"
        r = run([ffmpeg(), "-y", "-loglevel", "error", "-ss", str(c["start"]), "-to", str(c["end"]), "-i", str(video),
                 "-vf", vf, "-af", "loudnorm=I=-14:TP=-1.5", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-ar", "48000", str(part)])
        if r.returncode:
            raise RuntimeError(r.stderr[-500:])
        parts.append(part)
    (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    out = OUT / f"{stem}.mp4"
    run([ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-c", "copy", str(out)])
    shutil.rmtree(tmp)

    rec, lines = 0.0, [f"TITLE: {stem}", "FCM: NON-DROP FRAME", ""]
    for i, c in enumerate(cuts, 1):
        dur = c["end"] - c["start"]
        lines.append(f"{i:03d}  AX       AA/V  C        {_tc(c['start'])} {_tc(c['end'])} {_tc(rec)} {_tc(rec + dur)}")
        lines.append(f"* FROM CLIP NAME: {video.name}")
        if c.get("note"):
            lines.append(f"* COMMENT: {c['note']}")
        rec += dur
    (OUT / f"{stem}.edl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"  완성 · {out.name} ({rec:.1f}초) · 편집 프로그램용 {stem}.edl")
    return out


# ---------- 업데이트 ----------

def _newer(a, b):
    return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))


def check_update():
    url = CONFIG.get("update_manifest_url") or DEFAULT_MANIFEST
    if not url:
        return {"current": VERSION, "available": False, "note": "업데이트 주소가 설정되지 않았어요"}
    with urllib.request.urlopen(url, timeout=10) as r:
        m = json.loads(r.read().decode("utf-8"))
    return {"current": VERSION, "latest": m["version"], "available": _newer(m["version"], VERSION),
            "notes": m.get("notes", ""), "zip": m.get("zip")}


def update_engine(log):
    log("다운로드 엔진을 최신으로 바꾸는 중")
    r = run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"])
    log("  완료" if r.returncode == 0 else f"  실패 · {r.stderr[-300:]}")


def update_app(log):
    info = check_update()
    if "note" in info:
        log(f"프로그램 업데이트 건너뜀 · {info['note']}")
        return False
    if not info.get("available"):
        log(f"이미 최신 버전이에요 (v{VERSION})")
        return False
    log(f"프로그램 업데이트 · v{VERSION} → v{info['latest']}")
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "app.zip"
        urllib.request.urlretrieve(info["zip"], zpath)
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            root = names[0].split("/")[0] + "/" if all(n.startswith(names[0].split("/")[0] + "/") for n in names) else ""
            for n in names:
                rel = n[len(root):]
                if not rel or rel == "config.json":  # 사용자 설정은 보존
                    continue
                dest = (APP_DIR / rel).resolve()
                if APP_DIR not in dest.parents:  # 압축 경로 조작 방지
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(n))
    req = APP_DIR / "requirements.txt"
    run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)])
    log("  완료 · 프로그램을 다시 시작해요")
    return True
