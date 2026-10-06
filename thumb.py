"""썸네일 도구: 장면 후보 추출 · 장면 캡처 · 누끼(배경 제거) · 디자인 저장 · 이미지 내보내기."""
import base64
import io
import json
import os
import re
import subprocess
import threading
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


def grab(name, t, w=1920):
    """영상의 t초 장면을 이미지로 (원본 화질, 가로 최대 1920 — 쇼츠 9:16 확대에도 덜 뭉개지게)."""
    out = _frames_dir(name) / f"h_{t:09.3f}.jpg"
    if not out.exists():
        core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(core.VIDEOS / name), "-frames:v", "1",
                  "-vf", f"scale='min({w},iw)':-2", "-q:v", "1", str(out)])
    return out


def _sharpness(path):
    """썸네일 배경으로 좋은 장면 점수.
    - 가운데가 선명할수록(위·아래 띠는 빼고 잼 — 영상에 박힌 자막 경계가 '선명'으로 잡히지 않게)
    - 너무 어둡거나 밝지 않을수록
    - 사람 피부가 가운데에 크게 보일수록 (인물 클로즈업)
    - 아래쪽에 자막 같은 글자 띠가 있으면 감점"""
    import numpy as np
    from PIL import Image
    rgb = np.asarray(Image.open(path).convert("RGB").resize((320, 180)), dtype=np.float32)
    im = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    c = im[27:126, 40:280]
    lap = c[1:-1, 1:-1] * 4 - c[:-2, 1:-1] - c[2:, 1:-1] - c[1:-1, :-2] - c[1:-1, 2:]
    expo = 1.0 - min(1.0, abs(im.mean() - 120) / 160)
    r, g, b = rgb[27:153, 60:260, 0], rgb[27:153, 60:260, 1], rgb[27:153, 60:260, 2]
    cr = 0.5 * r - 0.4187 * g - 0.0813 * b + 128
    cb = -0.1687 * r - 0.3313 * g + 0.5 * b + 128
    skin = float(((cr > 135) & (cr < 175) & (cb > 85) & (cb < 130) & (r > 60)).mean())
    gx = np.abs(np.diff(im, axis=1)) > 70
    text = max(float(gx[120:180].mean()), float(gx[0:30].mean()))
    penalty = 0.35 if text > 0.06 else 0.7 if text > 0.04 else 1.0
    return float(np.log1p(lap.var())) * expo * (1 + 2.5 * min(skin, 0.35)) * penalty


def frame_candidates(name, n=8):
    """선명한 장면 n개 (하이라이트 근처 우선, 고르게 분포)."""
    from editor import media_info  # 순환 import 피함
    cache = _frames_dir(name) / "candidates2.json"
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
    from PIL import ImageOps
    path, size, mean, std, logits = _model(kind)
    core.set_progress(label="누끼 따는 중", pct=None, detail="인물·사물만 남기는 중 (20~40초)")
    if kind not in _SESS:
        _SESS[kind] = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    sess = _SESS[kind]
    img = ImageOps.exif_transpose(Image.open(src_path)).convert("RGB")  # 휴대폰 사진 회전 정보 반영 (브라우저와 같은 방향)
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
    """올린 그림 저장. 휴대폰 사진의 회전 정보(EXIF)를 실제로 적용하고, 아주 큰 사진은 긴 변 3000px로 줄임."""
    raw = base64.b64decode(data_url.split(",", 1)[1])
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        rotated = im.getexif().get(0x0112, 1) != 1
        if rotated or max(im.size) > 3000:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((3000, 3000), Image.LANCZOS)
            b = io.BytesIO()
            if ext == "png" or im.mode in ("RGBA", "LA", "P"):
                ext = "png"
                im.save(b, "PNG")
            else:
                im.convert("RGB").save(b, "JPEG", quality=94)
            raw = b.getvalue()
    except Exception:
        pass
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
    for f in (p, p.with_suffix(".json.bak")):  # 파일이 깨졌으면 바로 전 저장본으로
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(d, dict) and isinstance(d.get("designs"), list):
                    return d
            except Exception:
                pass
    return {"designs": []}


_SAVE_LOCK = threading.Lock()


def save_docs(name, docs):
    """안전하게 저장: 빈 목록은 거절, 임시 파일에 쓴 뒤 바꿔치기, 바로 전 저장본은 .bak으로."""
    if not isinstance(docs, dict) or not docs.get("designs"):
        return False
    p = _doc_path(name)
    with _SAVE_LOCK:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
        if p.exists():
            try:
                os.replace(p, p.with_suffix(".json.bak"))
            except OSError:
                pass
        os.replace(tmp, p)
    return True


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
