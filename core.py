"""풋살사관학교 편집도우미 — 핵심 기능 (목록·다운로드·분석·러프컷·업데이트)."""
import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import updater

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


_FF = None


def ffmpeg():
    """앱과 함께 설치된 ffmpeg(imageio-ffmpeg)를 먼저 씀 — PATH 의 오래된 ffmpeg 는 xfade·자막 등이 없을 수 있음.
    FUTSAL_FFMPEG 환경 변수로 직접 지정 가능."""
    global _FF
    if _FF is None:
        import os
        exe = os.environ.get("FUTSAL_FFMPEG")
        if not exe:
            try:
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("ffmpeg를 찾지 못했어요")
        _FF = exe
    return _FF


NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # Windows: 검은 창 안 띄움


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **NO_WINDOW)


# ---------- 목록·다운로드 ----------

def list_videos(kind="videos", cookies_browser=None, url=None, log=None):
    """채널 영상 목록 (조회수 순). url을 주면 다른 유튜버 채널, 영상 주소 하나면 그 영상만."""
    log = log or print
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

    def extract():
        opts.update(_js_opts())
        with _yt(log).YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False)

    with _engine(log):
        try:
            info = extract()
        except Exception as e:
            if not _blocked(e):
                raise
            log("YouTube가 막아서 다운로드 엔진을 최신으로 바꾼 뒤 한 번 더 불러올게요")
            update_engine(log)
            try:
                info = extract()
            except Exception as e2:
                if _blocked(e2):
                    raise RuntimeError(BLOCKED_MSG) from e2
                raise
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
    failed, retried = [], False  # 막히면 엔진 최신화 + 다시 받기는 한 번만
    # YouTube 해석 도구: 켤 때 받기 시작한 설치가 있으면 (진행률을 보여 주며) 끝날 때까지 기다림 · 엔진 잠금 밖에서
    ensure_deno(log, install=_since("deno_install", "t_fail") > ENGINE_RETRY)
    with _engine(log):
        opts.update(_js_opts())
        ydl = _yt(log).YoutubeDL(opts)
        try:
            for k, vid in enumerate(ids, 1):
                log(f"보관함에 담는 중 · {vid}")
                for attempt in (1, 2):
                    cur.update(i=k, vid=vid, streams={})
                    set_progress(label="보관함에 담는 중", item=vid, step=f"{k}/{len(ids)}", pct=None, detail="연결하는 중")
                    try:
                        ydl.download([vid if vid.startswith("http") else f"https://www.youtube.com/watch?v={vid}"])
                        log("  담기 완료")
                    except Exception as e:  # 한 개 실패해도 나머지 계속
                        if attempt == 1 and not retried and _blocked(e):
                            retried = True
                            log("  YouTube가 막았어요 · 다운로드 엔진을 최신으로 바꾼 뒤 한 번 더 받아 볼게요")
                            ydl.close()
                            set_progress(label="다운로드 엔진 준비 중", item=vid, step=f"{k}/{len(ids)}", pct=None, detail="최신으로 바꾸는 중이에요")
                            update_engine(log)
                            opts.update(_js_opts())
                            ydl = _yt(log).YoutubeDL(opts)
                            continue
                        msg = str(e)
                        if _blocked_text(msg):
                            msg = BLOCKED_MSG
                        log(f"  담지 못했어요 · {msg}")
                        failed.append(vid)
                    break
        finally:
            ydl.close()
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


# ---------- 다운로드 엔진 (yt-dlp · YouTube 해석 도구 Deno) ----------
# YouTube 는 자주 바뀜 → 켤 때 3일마다 엔진을 최신으로, 막히면 그 자리에서 한 번 최신으로 바꾸고 다시 시도.
# yt-dlp 공식 안내: YouTube 를 제대로 받으려면 yt-dlp-ejs(= yt-dlp[default]) + 자바스크립트 실행기(Deno 권장, 2.3 이상)가 필요.

ENGINE_HOME = Path.home() / ".futsal-studio"  # 사용자별 (앱 폴더를 지워도 남음)
ENGINE_EVERY = 3 * 86400
ENGINE_RETRY = 86400  # 켤 때 저절로 하다 실패했으면 하루 뒤에 다시 (인터넷이 막힌 곳에서 켤 때마다 붙잡지 않게)
ENGINE_PKG = "yt-dlp[default]"
DENO_MIN = (2, 3, 0)
DENO_ZIP = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
DENO_ZIP_ALT = "https://dl.deno.land/release/{ver}/deno-x86_64-pc-windows-msvc.zip"
DENO_AUTO = sys.platform == "win32"  # 자동 설치는 Windows 만
BLOCKED_MSG = ("다운로드 엔진을 최신으로 바꿔 다시 해 봤지만 YouTube가 계속 막고 있어요. 크롬에서 YouTube에 로그인한 뒤 "
               "소재 찾기의 '다운로드가 계속 실패하나요?' → '크롬 로그인 정보로 받기'를 켜고 다시 해 보세요.")
_ENGINE_LOCK = threading.RLock()  # pip 로 엔진을 바꾸는 동안만 목록·다운로드가 기다림 (같은 작업 안의 재시도는 통과)
_DENO_LOCK = threading.Lock()     # Deno 설치는 한 번에 하나 (엔진 잠금과 따로: 받는 동안 목록 불러오기는 기다리지 않음)
_DENO_PROG = {}                   # 받는 중인 Deno 진행률 (기다리는 다운로드 작업이 화면에 보여 줌)
_DENO = None


def _blocked_text(msg):
    return "not a bot" in msg or "Sign in" in msg or re.search(r"(?<![\w-])403(?![\w-])", msg) is not None


def _blocked(e):
    """YouTube 가 막았다는 다운로드 오류인지 (엔진을 최신으로 바꾸면 풀리는 경우가 많음)."""
    return any(c.__name__ == "DownloadError" for c in type(e).__mro__) and _blocked_text(str(e))


@contextlib.contextmanager
def _engine(log):
    if not _ENGINE_LOCK.acquire(blocking=False):
        set_progress(label="다운로드 엔진 준비 중", pct=None, detail="최신으로 바꾸는 중이에요 · 잠시만 기다려 주세요")
        _ENGINE_LOCK.acquire()
        set_progress()  # 기다림 끝 → 원래 작업 이름이 다시 보이게
    try:
        ensure_deno(log, install=False)
        yield
    finally:
        _ENGINE_LOCK.release()


def _js_opts():
    return {"js_runtimes": {"deno": {"path": _DENO}}} if _DENO else {}


def _yt(log=print):
    try:
        import yt_dlp
        return yt_dlp
    except Exception as e:  # 엔진 업데이트가 중간에 끊겨 망가진 경우 → 다시 설치
        log(f"다운로드 엔진이 없거나 망가져서 다시 설치해요 · {e}")
    update_engine(log)
    try:
        import yt_dlp
        return yt_dlp
    except Exception as e:
        raise RuntimeError("다운로드 엔진을 설치하지 못했어요. 인터넷 연결을 확인한 뒤 '시작하기 (Windows).bat'을 "
                           f"다시 실행해 주세요 · {e}")


def _forget_ytdlp():
    """pip 로 바꾼 뒤 새 yt-dlp 를 처음부터 다시 읽게 (예전 것과 섞이지 않게)."""
    for m in [m for m in sys.modules if m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
        sys.modules.pop(m, None)
    importlib.invalidate_caches()


def _ytdlp_version():
    try:
        from importlib import metadata
        return metadata.version("yt-dlp")
    except Exception:
        return None


def _tail(text, n=200):
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    return lines[-1][-n:] if lines else ""


def _stamp(name, **kw):
    """ENGINE_HOME/<name>.json 기록 고치기 (값이 None 이면 그 항목을 지움)."""
    p = ENGINE_HOME / f"{name}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    d.update(kw)
    try:
        ENGINE_HOME.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({k: v for k, v in d.items() if v is not None}), encoding="utf-8")
    except OSError:
        pass


def _since(name, key):
    """기록된 시각 뒤 지난 초 (기록이 없거나 이상하면 아주 큼)."""
    try:
        age = time.time() - float(json.loads((ENGINE_HOME / f"{name}.json").read_text(encoding="utf-8"))[key])
    except (OSError, ValueError, KeyError, TypeError):
        return float("inf")
    return age if age >= 0 else float("inf")


def engine_age():
    """마지막으로 엔진을 최신으로 바꾼 뒤 지난 초."""
    return _since("engine_upgrade", "t")


def update_engine(log, deno=True):
    """다운로드 엔진(yt-dlp + YouTube 해석 부품)을 최신으로 바꾸고 새 버전을 다시 읽음. 성공하면 True.
    잠금은 pip 동안만 (Deno 는 그 밖에서 확인·설치)."""
    with _ENGINE_LOCK:
        before = _ytdlp_version()
        log("다운로드 엔진을 최신으로 바꾸는 중")
        r = run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-U", ENGINE_PKG])
        _forget_ytdlp()
        ok = r.returncode == 0
        if ok:
            after = _ytdlp_version()
            _stamp("engine_upgrade", t=time.time(), version=after, t_fail=None)
            log(f"  완료 · {after or '최신'}" + (" (이미 최신이에요)" if after and after == before else ""))
        else:
            _stamp("engine_upgrade", t_fail=time.time())
            log(f"  최신으로 바꾸지 못했어요 · 인터넷 연결을 확인해 주세요 · {_tail(r.stderr)}")
    if deno:
        ensure_deno(log)
    return ok


def engine_autoupdate(log):
    """앱을 켤 때 뒤에서: 3일이 지났으면 엔진을 최신으로, Deno 가 없으면 설치. 실패했으면 하루 동안은 다시 하지 않음.
    화면 진행률은 건드리지 않음 (그사이 시작한 작업의 진행률을 덮지 않게)."""
    try:
        if engine_age() > ENGINE_EVERY and _since("engine_upgrade", "t_fail") > ENGINE_RETRY:
            update_engine(log, deno=False)
        ensure_deno(log, install=_since("deno_install", "t_fail") > ENGINE_RETRY, show=False)
    except Exception as e:
        log(f"다운로드 엔진 확인을 건너뛰었어요 · {e}")


def _deno_ok(path):
    if not path or not os.path.isfile(path):
        return False
    try:
        r = subprocess.run([str(path), "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"deno (\d+)\.(\d+)\.(\d+)", r.stdout or "")
    return bool(m) and tuple(int(x) for x in m.groups()) >= DENO_MIN


def _use_deno(path):
    """찾은 Deno 를 yt-dlp 가 쓰게: 경로를 기억하고 이 프로그램의 PATH 맨 앞에 넣음."""
    global _DENO
    _DENO = str(path)
    d = os.path.dirname(_DENO)
    if d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    return _DENO


def _fetch_text(url):
    req = urllib.request.Request(url, headers=updater.UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _deno_urls():
    yield DENO_ZIP
    try:  # GitHub 이 안 되면 Deno 공식 배포 서버
        yield DENO_ZIP_ALT.format(ver=_fetch_text("https://dl.deno.land/release-latest.txt").strip())
    except updater.NET_ERRORS:
        return


def _install_deno(dest, progress=None):
    """공식 Windows 용 deno.exe 를 받아 확인(sha256·실행)한 뒤 dest 에 넣음."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    err = None
    for url in _deno_urls():
        try:
            with tempfile.TemporaryDirectory(dir=ENGINE_HOME, ignore_cleanup_errors=True) as td:
                zp = Path(td) / "deno.zip"
                updater.download(url, zp, progress, timeout=60)
                want = re.search(r"\b[0-9a-fA-F]{64}\b", _fetch_text(url + ".sha256sum"))
                if not want or updater.sha256(zp) != want.group(0).lower():
                    raise RuntimeError("받은 파일 확인(sha256)에 실패했어요")
                new = Path(td) / dest.name
                with zipfile.ZipFile(zp) as z:
                    new.write_bytes(z.read("deno.exe"))
                if sys.platform != "win32":
                    new.chmod(0o755)
                if not _deno_ok(new):
                    raise RuntimeError("받은 Deno가 실행되지 않아요")
                updater._replace(new, dest)
                return
        except Exception as e:
            err = e
    raise RuntimeError(updater._why(err) if isinstance(err, updater.NET_ERRORS) else str(err))


def _deno_home():
    return ENGINE_HOME / "bin" / ("deno.exe" if sys.platform == "win32" else "deno")


def _find_deno():
    if _DENO and os.path.isfile(_DENO):
        return _DENO
    for cand in (_deno_home(), shutil.which("deno")):
        if _deno_ok(cand):
            return _use_deno(cand)
    return None


def _deno_progress(show):
    def cb(got, total):
        pct = min(99, int(got * 100 / total)) if total else None
        _DENO_PROG.update(pct=pct, detail=f"{got / 1e6:.0f}MB" + (f" / {total / 1e6:.0f}MB" if total else "") + " · 처음 한 번만 받아요")
        if show:
            set_progress(label="YouTube 해석 도구 받는 중", pct=pct, detail=_DENO_PROG["detail"])
    return cb


def ensure_deno(log, install=True, show=True):
    """YouTube 해석에 쓰는 자바스크립트 실행기(Deno)를 찾고, 없으면 (Windows) 이 사용자 폴더에 설치.
    실패해도 멈추지 않고 None (영상 일부가 안 받아질 수 있음). 다른 쪽이 설치하는 중이면 끝날 때까지 기다렸다 그 결과를 씀.
    show: 받는 진행률을 화면에 (작업 안에서 부를 때만)."""
    found = _find_deno()
    if found or not (install and DENO_AUTO):
        return found
    while not _DENO_LOCK.acquire(timeout=0.5):
        if show:
            set_progress(label="YouTube 해석 도구 받는 중", pct=_DENO_PROG.get("pct"),
                         detail=_DENO_PROG.get("detail") or "처음 한 번만 받아요 · 잠시만 기다려 주세요")
    try:
        found = _find_deno()  # 기다리는 동안 다른 쪽이 설치했으면 그대로 씀
        if found:
            return found
        log("YouTube 해석 도구(Deno)를 설치하는 중이에요 · 처음 한 번만, 1~2분 걸려요")
        _DENO_PROG.clear()
        try:
            _install_deno(_deno_home(), _deno_progress(show))
        except Exception as e:
            _stamp("deno_install", t_fail=time.time())
            log(f"  YouTube 해석 도구(Deno)를 설치하지 못했어요 · 일부 영상이 안 받아질 수 있어요. "
                f"인터넷 연결을 확인한 뒤 왼쪽 아래 '업데이트 확인'을 눌러 주세요 ({e})")
            return None
        _stamp("deno_install", t_fail=None)
        log("  YouTube 해석 도구 설치 완료")
        return _use_deno(_deno_home())
    finally:
        _DENO_PROG.clear()
        _DENO_LOCK.release()


# ---------- 업데이트 (실제 설치·되돌리기는 updater.py) ----------

def _newer(a, b):
    return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))


def _skip_note(ver):
    return f"새 버전(v{ver})이 이 PC에서 열리지 않아 이전 버전(v{VERSION})을 쓰고 있어요. 관리자에게 알려 주세요."


def check_update():
    out = {"current": VERSION, "available": False}
    n = updater.take_notice()  # 방금 업데이트·되돌림 결과 → 화면에 한 번 알림
    if n:
        out.update(notice=n["text"], notice_warn=n["warn"])
    url = CONFIG.get("update_manifest_url") or DEFAULT_MANIFEST
    if not url:
        return dict(out, note="업데이트 주소가 설정되지 않았어요")
    try:
        m, _ = updater.fetch_manifest(url)
    except updater.UpdateError as e:
        return dict(out, note=str(e))
    out.update(latest=m["version"], available=_newer(m["version"], VERSION), notes=m.get("notes", ""), zip=m.get("zip"))
    if out["available"] and updater.skipped(APP_DIR) == m["version"]:  # 되돌렸던 그 버전은 더 새 버전이 나올 때까지 권하지 않음
        out.update(available=False, note=_skip_note(m["version"]))
    return out


def update_app(log):
    """새 버전을 확인하며 설치 (받기 → 검사 → 되돌리기 복사본 → 교체). 다시 시작해야 하면 True."""
    url = CONFIG.get("update_manifest_url") or DEFAULT_MANIFEST
    if not url:
        log("프로그램 업데이트 건너뜀 · 업데이트 주소가 설정되지 않았어요")
        return False
    m, raw = updater.fetch_manifest(url)
    if not _newer(m["version"], VERSION):
        log(f"이미 최신 버전이에요 (v{VERSION})")
        return False
    if updater.skipped(APP_DIR) == m["version"]:
        log(f"프로그램 업데이트 건너뜀 · {_skip_note(m['version'])}")
        return False
    log(f"프로그램 업데이트 · v{VERSION} → v{m['version']}")

    def prog(got, total):
        set_progress(label="업데이트 받는 중", pct=min(99, int(got * 100 / total)) if total else None,
                     detail=f"{got / 1e6:.1f}MB" + (f" / {total / 1e6:.1f}MB" if total else ""))

    res = updater.download_and_install(m, raw, APP_DIR, log, prog)
    if res["req_changed"]:  # 구성요소 목록이 바뀐 버전만 pip (안 바뀌면 건너뜀)
        set_progress(label="업데이트 설치 중", pct=None, detail="새 구성요소를 설치하는 중이에요 · 몇 분 걸릴 수 있어요")
        log("  새 구성요소를 설치하는 중이에요 (몇 분 걸릴 수 있어요)")
        r = run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(APP_DIR / "requirements.txt")])
        if r.returncode:
            updater.mark_requirements(APP_DIR, ok=False)
            back = updater.rollback(APP_DIR, log)
            raise RuntimeError("새 버전에 필요한 구성요소를 설치하지 못해 업데이트를 취소했어요"
                               + (" (이전 버전 그대로예요)" if back else " (프로그램을 껐다 켜면 이전 버전으로 돌아가요)")
                               + f". 인터넷 연결을 확인하고 다시 시도해 주세요 · {_tail(r.stderr)}")
        updater.mark_requirements(APP_DIR)
    log("  완료 · 프로그램을 다시 시작해요")
    return True
