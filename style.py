"""편집 스타일 배우기 — 잘 나가는 유튜버 영상(레퍼런스)을 분석해 '스타일 프로필'로 저장.

AI 비용 없는 규칙 기반 분석:
- 컷 리듬: 장면 전환 횟수·평균 컷 길이
- 줌 컷(펀치인): 컷 직후 화면이 같은 장면을 확대한 것인지
- 말 사이 공백: 무음 길이 분포 → 우리 가편집에서 몇 초 이상 쉬면 자를지
- 자막: 화면에 글자가 떠 있는 비율·위치(위/가운데/아래)·주 색(흰/노랑…)
- 소리 크기(LUFS)·말 빠르기(받아쓰기가 있으면)
여러 영상을 배우면 평균을 내서 하나의 스타일로 합친다.
"""
import json
import re
import subprocess
import time

import core

STYLES = core.WORK / "styles"
FW, FH, FPS = 320, 180, 4


def _probe_duration(path):
    r = core.run([core.ffmpeg(), "-hide_banner", "-i", str(path)])
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def _frames(path):
    """저해상도 RGB 프레임을 1초에 4장씩 흘려보냄 (메모리에 다 올리지 않음)."""
    import numpy as np
    cmd = [core.ffmpeg(), "-v", "error", "-i", str(path), "-vf", f"fps={FPS},scale={FW}:{FH}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    kw = getattr(core, "NO_WINDOW", {})
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kw)
    n = FW * FH * 3
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield np.frombuffer(b, np.uint8).reshape(FH, FW, 3)
    finally:
        p.kill()


def _small(g):
    return g.reshape(45, 4, 80, 4).mean(axis=(1, 3))


def _zoom_score(a, b):
    """b가 a를 확대한 화면인지: (최소 오차, 확대 배율). a·b는 회색 FH×FW. 거칠게 찾고 근처를 촘촘히 다시 찾음."""
    import numpy as np
    from PIL import Image
    ia = Image.fromarray(a.astype(np.uint8))

    def err(s, ox, oy):
        cw, ch = FW / s, FH / s
        x0, y0 = (FW - cw) / 2 + ox * FW / 2, (FH - ch) / 2 + oy * FH / 2
        if x0 < 0 or y0 < 0 or x0 + cw > FW or y0 + ch > FH:
            return 1e9
        z = np.asarray(ia.resize((FW, FH), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch)), np.float32)
        return float(np.abs(z - b).mean())

    best = min((err(s, ox, oy), s, ox, oy) for s in (1.12, 1.2, 1.3, 1.45, 1.6) for ox in (-0.12, 0, 0.12) for oy in (-0.12, 0, 0.08))
    _, s0, ox0, oy0 = best
    best = min([best] + [(err(s0 + ds, ox0 + dx, oy0 + dy), s0 + ds, ox0 + dx, oy0 + dy)
                         for ds in (-0.05, 0, 0.05) for dx in (-0.05, 0, 0.05) for dy in (-0.05, -0.025, 0, 0.025, 0.05)])
    return best[0], round(best[1], 2)


def _text_bands(rgb):
    """가로 띠 6개마다 '글자 같은 강한 세로 경계' 비율 + 밝은 글자색 표본."""
    import numpy as np
    g = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    gx = np.abs(np.diff(g, axis=1))
    strong = gx > 70
    bands = [float(strong[i * 30:(i + 1) * 30].mean()) for i in range(6)]
    return g, bands, strong


def analyze_style(name, log=print):
    import numpy as np
    path = core.VIDEOS / name
    dur = _probe_duration(path) or 1.0
    core.set_progress(label="스타일 배우는 중", item=name, pct=0, detail="화면 컷·줌·자막 살펴보는 중")
    log(f"스타일 분석 · {name}")
    prev_g = prev_s = None
    diffs, cuts, zooms, band_hist, cap_colors = [], [], [], [], []
    last_cut, i = -99, 0
    for rgb in _frames(path):
        t = i / FPS
        g, bands, strong = _text_bands(rgb)
        band_hist.append(bands)
        s = _small(g)
        if prev_s is not None:
            d = float(np.abs(s - prev_s).mean())
            med = float(np.median(diffs[-24:])) if len(diffs) >= 4 else 4.0
            diffs.append(d)
            if d > 16 and d > med * 3 and t - last_cut >= 0.5:
                cuts.append(t)
                last_cut = t
                e0 = float(np.abs(g - prev_g).mean())
                e1, k1 = _zoom_score(prev_g, g)   # 확대(펀치인)
                e2, k2 = _zoom_score(g, prev_g)   # 축소(줌아웃)
                if min(e1, e2) < 0.55 * e0 and min(e1, e2) < 16:
                    zooms.append({"t": round(t, 2), "scale": k1 if e1 <= e2 else 1 / k2})
        # 아래쪽 띠에 글자가 있어 보이면 밝은 획 색을 표본으로
        if bands[5] > 0.05 or bands[4] > 0.05:
            sl = slice(120, 180)
            px = rgb[sl][:, 1:][strong[sl]]
            br = px[px.max(axis=1) > 170]
            if len(br) > 30:
                cap_colors.append(br.mean(axis=0))
        prev_g, prev_s = g, s
        i += 1
        if i % (FPS * 20) == 0:
            core.set_progress(label="스타일 배우는 중", item=name, pct=min(90, int(t * 90 / dur)), detail=f"화면 살펴보는 중 · {int(t)}초 / {int(dur)}초")
    n = max(1, i)
    bh = np.array(band_hist) if band_hist else np.zeros((1, 6))
    base = float(np.median(bh[:, 1:4])) if len(bh) else 0.0
    thr = max(0.045, base * 2.2)
    cap_frames = {pos: float(((bh[:, a:b].max(axis=1)) > thr).mean()) for pos, (a, b) in {"top": (0, 2), "middle": (2, 4), "bottom": (4, 6)}.items()}
    cap_pos = max(cap_frames, key=cap_frames.get)
    color = "#FFFFFF"
    if cap_colors:
        r, g_, b = np.median(np.array(cap_colors), axis=0)
        color = "#FFE14D" if (r > 170 and g_ > 150 and b < 120) else "#FFFFFF" if min(r, g_, b) > 170 else "#%02X%02X%02X" % (int(r), int(g_), int(b))

    core.set_progress(label="스타일 배우는 중", item=name, pct=92, detail="말 사이 공백·소리 크기 재는 중")
    sil = []
    r = core.run([core.ffmpeg(), "-hide_banner", "-i", str(path), "-vn", "-af", "silencedetect=noise=-32dB:d=0.15,ebur128", "-f", "null", "-"])
    err = r.stderr or ""
    for a, b in re.findall(r"silence_start: ([\d.]+)[\s\S]*?silence_end: ([\d.]+)", err):
        sil.append(float(b) - float(a))
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    lufs = float(m[-1]) if m else None
    sil_sorted = sorted(sil)
    pause_p75 = sil_sorted[int(len(sil_sorted) * 0.75)] if sil_sorted else 0.4

    cps = None
    tj = core.adir(name) / "transcript.json"
    if tj.exists():
        segs = json.loads(tj.read_text(encoding="utf-8"))
        talk = sum(max(0.0, s["end"] - s["start"]) for s in segs)
        chars = sum(len(s["text"].replace(" ", "")) for s in segs)
        cps = round(chars / talk, 2) if talk > 5 else None

    shots = np.diff([0.0] + cuts + [dur]) if cuts else np.array([dur])
    prof = {
        "source": name, "duration": round(dur, 1), "analyzed": time.strftime("%Y-%m-%d %H:%M"),
        "cutsPerMin": round(len(cuts) * 60 / dur, 2), "avgShot": round(float(shots.mean()), 2), "medianShot": round(float(np.median(shots)), 2),
        "zoomCutsPerMin": round(len(zooms) * 60 / dur, 2), "zoomRatio": round(len(zooms) / max(1, len(cuts)), 2),
        "avgZoom": round(float(np.mean([z["scale"] for z in zooms if z["scale"] > 1] or [1.2])), 2),
        "silenceRatio": round(sum(sil) / dur, 3), "pauseP75": round(pause_p75, 2), "pauses": len(sil),
        "captionRatio": round(max(cap_frames.values()), 2), "captionPos": cap_pos, "captionColor": color, "captionBands": {k: round(v, 2) for k, v in cap_frames.items()},
        "lufs": lufs, "charsPerSec": cps,
    }
    core.set_progress(label="스타일 배우는 중", item=name, pct=100, detail="완료")
    log(f"  컷 {prof['cutsPerMin']}번/분 · 평균 컷 {prof['avgShot']}초 · 줌컷 {prof['zoomCutsPerMin']}번/분 · 자막 {int(prof['captionRatio'] * 100)}% ({cap_pos})")
    return prof


def merge(profiles):
    """여러 레퍼런스를 평균 내 하나의 스타일로."""
    import statistics as st
    if len(profiles) == 1:
        return dict(profiles[0])
    out = {"source": [p["source"] for p in profiles], "duration": round(sum(p["duration"] for p in profiles), 1), "analyzed": time.strftime("%Y-%m-%d %H:%M")}
    for k in ("cutsPerMin", "avgShot", "medianShot", "zoomCutsPerMin", "zoomRatio", "avgZoom", "silenceRatio", "pauseP75", "captionRatio"):
        out[k] = round(st.mean(p[k] for p in profiles), 2)
    for k in ("lufs", "charsPerSec"):
        v = [p[k] for p in profiles if p.get(k) is not None]
        out[k] = round(st.mean(v), 2) if v else None
    out["captionPos"] = st.mode(p["captionPos"] for p in profiles)
    out["captionColor"] = st.mode(p["captionColor"] for p in profiles)
    out["pauses"] = sum(p["pauses"] for p in profiles)
    return out


def edit_params(prof):
    """스타일 프로필 → 자동 가편집에 쓸 값."""
    keep_pause = round(min(0.8, max(0.12, prof["pauseP75"] * 0.9)), 2)
    zoom_every = round(60 / prof["zoomCutsPerMin"], 1) if prof["zoomCutsPerMin"] >= 0.3 else 0
    return {
        "keepPause": keep_pause,                       # 말 사이 이보다 길게 쉬면 자름
        "targetShot": prof["medianShot"],              # 한 컷 길이 목표(초)
        "zoomEvery": zoom_every,                       # 몇 초마다 줌 컷(펀치인) — 0이면 안 함
        "zoomScale": min(1.6, max(1.08, prof["avgZoom"])),
        "captions": prof["captionRatio"] >= 0.25,
        "captionPos": prof["captionPos"], "captionColor": prof["captionColor"],
        "lufs": prof["lufs"] if prof.get("lufs") is not None else -14.0,
    }


def describe(prof):
    """사람이 읽기 쉬운 한 줄 설명."""
    p = edit_params(prof)
    pos = {"bottom": "아래", "middle": "가운데", "top": "위"}[prof["captionPos"]]
    return (f"컷이 {prof['avgShot']}초마다 바뀌고(1분에 {prof['cutsPerMin']}번), "
            + (f"{p['zoomEvery']}초마다 확대 컷(약 {p['zoomScale']}배)이 나와요. " if p["zoomEvery"] else "확대 컷은 거의 없어요. ")
            + (f"말 사이 {p['keepPause']}초 넘게 쉬면 잘라요. ")
            + (f"자막이 화면의 {int(prof['captionRatio'] * 100)}%에 {pos}쪽으로 깔려요." if p["captions"] else "자막은 적게 써요."))


def list_styles():
    STYLES.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(STYLES.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.stem, "profile": d, "params": edit_params(d), "desc": describe(d)})
        except Exception:
            pass
    return out


def learn(style_name, names, log=print):
    style_name = re.sub(r'[\\/:*?"<>|]', "", style_name).strip(" .") or "내 스타일"
    profs = []
    for k, n in enumerate(names, 1):
        core.set_progress(label="스타일 배우는 중", item=n, step=f"{k}/{len(names)}", pct=0, detail="준비 중")
        profs.append(analyze_style(n, log))
    prof = merge(profs)
    prof["refs"] = profs
    STYLES.mkdir(parents=True, exist_ok=True)
    (STYLES / f"{style_name}.json").write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"스타일 저장 · {style_name} · {describe(prof)}")
    return {"name": style_name, "profile": prof, "params": edit_params(prof), "desc": describe(prof)}
