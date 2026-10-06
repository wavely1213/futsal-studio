"""편집실: 영상 정보·파형·썸네일·프로젝트 저장·자동 추천·내보내기(영상/프리미어 XML/SRT)."""
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

import core

PROJECTS = core.WORK / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)
FONTS = core.APP_DIR / "fonts"

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


# ---------- 영상 정보 ----------

def media_info(name):
    err = core.run([core.ffmpeg(), "-hide_banner", "-i", str(core.VIDEOS / name)]).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    v = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", err)
    f = re.search(r"([\d.]+) fps", err)
    return {"duration": round(dur, 3), "width": int(v[1]) if v else 1920, "height": int(v[2]) if v else 1080,
            "fps": float(f[1]) if f else 30.0}


def waveform(name, per_sec=50):
    """소리 크기 (0~1) 를 1초에 per_sec 개씩."""
    cache = core.adir(name) / f"waveform_{per_sec}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import numpy as np
    sr = 8000
    p = subprocess.run([core.ffmpeg(), "-v", "error", "-i", str(core.VIDEOS / name), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True, **core.NO_WINDOW)
    a = np.abs(np.frombuffer(p.stdout, np.int16).astype(np.float32)) / 32768.0
    hop = sr // per_sec
    n = len(a) // hop
    peaks = a[: n * hop].reshape(n, hop).max(axis=1) if n else np.zeros(0)
    top = float(np.percentile(peaks, 99)) if n else 1.0
    data = {"per_sec": per_sec, "peaks": [round(min(1.0, float(x) / (top or 1)), 3) for x in peaks]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def thumbs(name):
    """타임라인용 썸네일 띠 (가로로 이어붙인 한 장)."""
    d = core.adir(name)
    meta = d / "thumbs.json"
    if meta.exists() and (d / "thumbs.jpg").exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    info = media_info(name)
    count = max(1, min(240, int(info["duration"] // 2) or 1))
    interval = max(0.5, info["duration"] / count)
    h = 72
    w = int(round(h * info["width"] / info["height"] / 2) * 2) or 128
    d.mkdir(parents=True, exist_ok=True)
    core.run([core.ffmpeg(), "-y", "-v", "error", "-i", str(core.VIDEOS / name), "-vf",
              f"fps=1/{interval:.3f},scale={w}:{h},tile={count}x1", "-frames:v", "1", "-q:v", "5", str(d / "thumbs.jpg")])
    data = {"interval": interval, "w": w, "h": h, "count": count}
    meta.write_text(json.dumps(data), encoding="utf-8")
    return data


# ---------- 프로젝트 ----------

def _ppath(name):
    return PROJECTS / f"{core.adir(name).name}.json"


def load_project(name):
    p = _ppath(name)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    info = media_info(name)
    segs = []
    t = core.adir(name) / "transcript.json"
    if t.exists():
        segs = json.loads(t.read_text(encoding="utf-8"))
    return {
        "source": name,
        "info": info,
        "format": "shorts",
        "clips": [{"id": _nid(), "in": 0.0, "out": info["duration"], "volume": 1.0, "fadeIn": 0.0, "fadeOut": 0.0,
                   "mute": False, "reframe": 0.5}],
        "captions": [{"id": _nid(), "start": s["start"], "end": s["end"], "text": s["text"]} for s in segs if s["text"]],
        "captionsOn": True,
        "captionStyle": dict(DEFAULT_STYLE),
        "titles": [],
        "master": {"volume": 1.0, "normalize": True},
    }


def save_project(name, proj):
    _ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")


# ---------- 자동 추천 (AI 없이 규칙 기반) ----------

FILLERS = {"아", "어", "음", "자", "네", "예", "그", "막", "뭐", "응", "이제", "그냥", "흠", "아니", "저기"}
KEYWORDS = {"팁": 4, "꿀팁": 6, "중요": 4, "핵심": 4, "강조": 3, "비결": 4, "방법": 3, "포인트": 3, "무조건": 3,
            "절대": 3, "실수": 3, "차이": 2, "첫 번째": 2, "첫번째": 2, "잘하": 2, "어떻게": 2, "비밀": 4, "원리": 3,
            "퍼스트 터치": 3, "기술": 2, "프로": 1, "국가대표": 3, "레전드": 2, "힘들": 1, "?": 1}


def _norm(t):
    return re.sub(r"[\s.,!?~…]+", "", t)


def recommend(name, min_len=20.0, max_len=55.0, n=3):
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
                a, b = max(0.0, s["start"] - 0.15), s["end"] + 0.2
                if cur and a - cur["out"] <= 0.6:
                    cur["out"] = b
                else:
                    if cur:
                        cuts.append(cur)
                    cur = {"in": round(a, 2), "out": round(b, 2)}
            if cur:
                cuts.append(cur)
            hits = [k for k in KEYWORDS if k != "?" and any(k in segs[x]["text"] for x in range(i, j + 1))]
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
        a, b = max(0.0, s["start"] - 0.15), s["end"] + 0.25
        if cur and a - cur["out"] <= 0.8:
            cur["out"] = b
        else:
            if cur:
                tidy.append(cur)
            cur = {"in": round(a, 2), "out": round(b, 2)}
    if cur:
        tidy.append(cur)
    return {"shorts": picked, "tidy": tidy, "junk": len(junk), "segments": len(segs)}


# ---------- 내보내기 ----------

def _ass_color(hexc, alpha=0.0):
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H{int(alpha * 255):02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _seq_map(proj):
    """원본 시간 → 타임라인 시간 변환용 (클립별 시작 위치)."""
    out, pos = [], 0.0
    for c in proj["clips"]:
        out.append((c, pos))
        pos += c["out"] - c["in"]
    return out, pos


def timeline_captions(proj):
    """자막을 타임라인 시간으로 (잘린 부분은 빠지고, 여러 클립에 걸치면 나뉨)."""
    seq, _ = _seq_map(proj)
    res = []
    for cap in proj["captions"]:
        for c, pos in seq:
            a, b = max(cap["start"], c["in"]), min(cap["end"], c["out"])
            if b - a > 0.05:
                res.append({"start": pos + a - c["in"], "end": pos + b - c["in"], "text": cap["text"]})
    return sorted(res, key=lambda x: x["start"])


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
        return (f"Style: {name},{font},{s['size']},{prim},{sec},{ocol},{ocol},{bold},0,0,0,100,100,0,0,"
                f"{border},{outline},0,2,40,40,0,1")

    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
             "MarginL, MarginR, MarginV, Encoding",
             style_line("Cap", st)]
    for t in proj["titles"]:
        lines.append(style_line(f"T{t['id']}", t["style"]))
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def event(style, s, start, end, text):
        x, y = W // 2, int(H * s["y"])
        tags = rf"{{\an2\pos({x},{y})}}" if s["effect"] != "slide" else r"{\an2}"
        body = _karaoke(text, end - start) if s["effect"] == "karaoke" else text.replace("\n", r"\N")
        eff = _effect_tags(s["effect"], x, y, end - start)
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{tags}{eff}{body}")

    if proj.get("captionsOn", True):
        for c in timeline_captions(proj):
            event("Cap", st, c["start"], c["end"], c["text"])
    for t in proj["titles"]:
        event(f"T{t['id']}", t["style"], t["start"], t["start"] + t["dur"], t["text"])
    return "\n".join(lines) + "\n"


def _srt(caps):
    out = []
    for i, c in enumerate(caps, 1):
        out.append(f"{i}\n{core._ts(c['start'])} --> {core._ts(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


def _xmeml(proj, W, H, fps, stem):
    """프리미어 프로에서 '가져오기'로 여는 XML (FCP7 형식)."""
    src = core.VIDEOS / proj["source"]
    tb = round(fps)
    ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    dur_f = int(proj["info"]["duration"] * fps)
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    url = "file://localhost/" + quote(str(src).replace("\\", "/").lstrip("/"))
    vitems, aitems, pos = [], [], 0
    for k, c in enumerate(proj["clips"]):
        i, o = int(c["in"] * fps), int(c["out"] * fps)
        ln = o - i
        fileref = (f'<file id="f1"><name>{src.name}</name><pathurl>{url}</pathurl>{rate}<duration>{dur_f}</duration>'
                   f'<media><video/><audio><channelcount>2</channelcount></audio></media></file>') if k == 0 else '<file id="f1"/>'
        common = f"<name>{src.name}</name><duration>{dur_f}</duration>{rate}<start>{pos}</start><end>{pos + ln}</end><in>{i}</in><out>{o}</out>"
        vitems.append(f'<clipitem id="v{k}">{common}{fileref}</clipitem>')
        gain = 0 if c.get("mute") else c.get("volume", 1.0)
        aitems.append(f'<clipitem id="a{k}">{common}<file id="f1"/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>'
                      f'<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effecttype>audiolevels</effecttype><mediatype>audio</mediatype>'
                      f'<parameter><parameterid>level</parameterid><name>Level</name><valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{gain:.4f}</value></parameter></effect></filter></clipitem>')
        pos += ln
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq1"><name>{stem}</name>'
            f'<duration>{pos}</duration>{rate}<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>'
            f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format><track>{"".join(vitems)}</track></video>'
            f'<audio><track>{"".join(aitems)}</track></audio></media></sequence></xmeml>\n')


def export(name, proj, opts, log):
    fmt = proj.get("format", "shorts")
    W, H = (1080, 1920) if fmt == "shorts" else (1920, 1080)
    fps = proj["info"].get("fps", 30.0)
    src = core.VIDEOS / name
    stem = f"{core.adir(name).name}_{'쇼츠' if fmt == 'shorts' else '롱폼'}"
    outputs = []
    caps = timeline_captions(proj) if proj.get("captionsOn", True) else []

    if opts.get("srt", True):
        (core.OUT / f"{stem}.srt").write_text(_srt(caps), encoding="utf-8")
        outputs.append(f"{stem}.srt")
    if opts.get("xml", True):
        (core.OUT / f"{stem}_premiere.xml").write_text(_xmeml(proj, W, H, fps, stem), encoding="utf-8")
        outputs.append(f"{stem}_premiere.xml")

    if opts.get("video", True):
        tmp = Path(tempfile.mkdtemp(dir=core.OUT))
        try:
            parts, n = [], len(proj["clips"])
            for k, c in enumerate(proj["clips"]):
                core.set_progress(label="내보내는 중", item=name, step=f"{k + 1}/{n}", pct=int(k * 80 / n), detail=f"컷 {k + 1} 자르는 중")
                ln = c["out"] - c["in"]
                if fmt == "shorts":
                    vf = f"scale=-2:{H},crop={W}:{H}:(iw-{W})*{c.get('reframe', 0.5):.3f}:0,setsar=1"
                else:
                    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
                vol = 0.0 if c.get("mute") else c.get("volume", 1.0) * proj["master"].get("volume", 1.0)
                af = [f"volume={vol:.3f}"]
                if c.get("fadeIn"):
                    af.append(f"afade=t=in:st=0:d={c['fadeIn']}")
                if c.get("fadeOut"):
                    af.append(f"afade=t=out:st={max(0, ln - c['fadeOut']):.3f}:d={c['fadeOut']}")
                part = tmp / f"p{k:03d}.mp4"
                r = core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{c['in']:.3f}", "-t", f"{ln:.3f}", "-i", str(src),
                              "-vf", vf + ",fps=30", "-af", ",".join(af), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                              "-c:a", "aac", "-ar", "48000", "-ac", "2", str(part)])
                if r.returncode:
                    raise RuntimeError(f"컷 {k + 1}을 만들지 못했어요 · {r.stderr[-300:]}")
                parts.append(part)
            (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
            core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                      "-c", "copy", str(tmp / "seq.mp4")])
            core.set_progress(label="내보내는 중", item=name, step="마무리", pct=85, detail="자막·효과 입히는 중")
            (tmp / "subs.ass").write_text(build_ass(proj, W, H), encoding="utf-8")
            shutil.copytree(FONTS, tmp / "fonts", dirs_exist_ok=True)
            af = "loudnorm=I=-14:TP=-1.5:LRA=11" if proj["master"].get("normalize", True) else "anull"
            out = core.OUT / f"{stem}.mp4"
            # 자막 파일 경로 문제(Windows 드라이브 문자 등)를 피하려고 작업 폴더에서 상대 경로로 실행
            r = subprocess.run([core.ffmpeg(), "-y", "-v", "error", "-i", "seq.mp4", "-vf", "subtitles=subs.ass:fontsdir=fonts",
                                "-af", af, "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-c:a", "aac", "-ar", "48000",
                                "-movflags", "+faststart", str(out)], cwd=str(tmp), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", **core.NO_WINDOW)
            if r.returncode:
                raise RuntimeError(f"자막을 입히지 못했어요 · {r.stderr[-300:]}")
            outputs.insert(0, out.name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    log(f"  내보내기 완료 · {', '.join(outputs)}")
    return outputs
