"""썸네일 도구: 장면 후보 추출 · 장면 캡처 · 누끼(배경 제거) · 디자인 저장 · 이미지 내보내기."""
import base64
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import core

THUMBS = core.WORK / "thumbnails"
ASSETS = THUMBS / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)
MODELS = Path.home() / ".futsal-studio" / "models"
# (파일 이름, 입력 크기, 평균, 표준편차, 출력이 로짓인지, 크기 안내)
BG_MODELS = {
    "hq": ("BiRefNet-general-bb_swin_v1_tiny-epoch_232", 1024, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225), True, "약 220MB"),
    "fast": ("u2net_human_seg", 320, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225), False, "약 170MB"),
}
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/{}.onnx"


def _frames_dir(name):
    d = core.adir(name) / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def grab(name, t, w=1280):
    """영상의 t초 장면을 이미지로."""
    out = _frames_dir(name) / f"f_{t:09.3f}.jpg"
    if not out.exists():
        core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(core.VIDEOS / name), "-frames:v", "1",
                  "-vf", f"scale={w}:-2", "-q:v", "2", str(out)])
    return out


def _sharpness(path):
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(path).convert("L").resize((320, 180)), dtype=np.float32)
    lap = im[1:-1, 1:-1] * 4 - im[:-2, 1:-1] - im[2:, 1:-1] - im[1:-1, :-2] - im[1:-1, 2:]
    bright = im.mean()
    # 선명할수록, 너무 어둡거나 밝지 않을수록 좋은 장면
    return float(lap.var()) * (1.0 - abs(bright - 120) / 160)


def frame_candidates(name, n=8):
    """선명한 장면 n개 (하이라이트 근처 우선, 고르게 분포)."""
    from editor import media_info  # 순환 import 피함
    cache = _frames_dir(name) / "candidates.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    dur = media_info(name)["duration"]
    ts = [dur * (0.03 + 0.94 * k / 23) for k in range(24)]
    extra = core.adir(name) / "analysis.json"
    if extra.exists():
        ts += [p["time"] for p in json.loads(extra.read_text(encoding="utf-8")).get("loud_peaks", [])]
    scored = []
    for i, t in enumerate(sorted(set(round(x, 1) for x in ts if 0 < x < dur))):
        core.set_progress(label="장면 고르는 중", item=name, pct=int(i * 100 / len(ts)), detail=f"{i + 1}/{len(ts)}")
        p = grab(name, t)
        if p.exists():
            scored.append((_sharpness(p), t))
    scored.sort(reverse=True)
    picked = []
    for sc, t in scored:
        if all(abs(t - q) > dur * 0.05 for q in picked):
            picked.append(t)
        if len(picked) >= n:
            break
    res = [{"t": t, "url": f"/frame?name={name}&t={t}"} for t in sorted(picked)]
    cache.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def _model(kind):
    fname, size, mean, std, logits, mb = BG_MODELS[kind]
    MODELS.mkdir(parents=True, exist_ok=True)
    path = MODELS / f"{fname}.onnx"
    if not path.exists():
        tmp = path.with_suffix(".part")

        def hook(blocks, bs, total):
            if total > 0:
                core.set_progress(label="누끼 준비 중", pct=min(99, int(blocks * bs * 100 / total)),
                                  detail=f"배경 제거 모델 내려받는 중 (처음 한 번, {mb})")
        urllib.request.urlretrieve(MODEL_URL.format(fname), tmp, hook)
        tmp.rename(path)
    return path, size, mean, std, logits


_SESS = {}


def remove_bg(src_path, kind="hq"):
    """배경을 지운 PNG 경로 반환 (원본과 같은 크기 — 브러시로 복원할 때 위치가 맞도록)."""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageFilter
    path, size, mean, std, logits = _model(kind)
    core.set_progress(label="누끼 따는 중", pct=None, detail="인물·사물만 남기는 중 (20~40초)")
    if kind not in _SESS:
        _SESS[kind] = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    sess = _SESS[kind]
    img = Image.open(src_path).convert("RGB")
    x = np.asarray(img.resize((size, size), Image.LANCZOS), dtype=np.float32) / 255.0
    x = (x - np.array(mean, np.float32)) / np.array(std, np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    pred = sess.run(None, {sess.get_inputs()[0].name: x})[0][0][0]
    pred = 1 / (1 + np.exp(-pred)) if logits else (pred - pred.min()) / (pred.max() - pred.min() + 1e-8)
    mask = Image.fromarray((pred * 255).astype(np.uint8)).resize(img.size, Image.LANCZOS)
    mask = mask.filter(ImageFilter.GaussianBlur(0.6))
    out = img.convert("RGBA")
    out.putalpha(mask)
    dst = ASSETS / f"cut_{int(time.time() * 1000)}.png"
    out.save(dst)
    return dst


def save_upload(data_url, ext="png"):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    dst = ASSETS / f"img_{int(time.time() * 1000)}.{ext}"
    dst.write_bytes(raw)
    return dst


def asset_url(p):
    p = Path(p)
    if p.parent == ASSETS:
        return f"/asset/{p.name}"
    return None


# ---------- 디자인 저장 · 내보내기 ----------

def _doc_path(name):
    return THUMBS / f"{core.adir(name).name}.json"


def load_docs(name):
    p = _doc_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"designs": []}


def save_docs(name, docs):
    _doc_path(name).write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")


def export_image(name, data_url, fmt="jpg", label="썸네일"):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    label = re.sub(r'[\\/:*?"<>|]', "", label).strip(" .") or "썸네일"
    base = f"{core.adir(name).name}_{label}"
    k = 1
    while (core.OUT / f"{base}_{k}.{fmt}").exists():
        k += 1
    out = core.OUT / f"{base}_{k}.{fmt}"
    out.write_bytes(raw)
    return out
