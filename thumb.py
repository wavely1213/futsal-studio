"""썸네일 도구: 장면 후보 추출 · 장면 캡처 · 누끼(배경 제거) · 디자인 저장 · 이미지 내보내기."""
import base64
import io
import json
import os
import re
import socket
import threading
import time
from pathlib import Path

import core
import updater

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
    src, vf = str(core.VIDEOS / name), f"scale='min({w},iw)':-2"
    if not out.exists():
        core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-vf", vf, "-q:v", "1", str(out)])
    if not out.exists():  # 영상 끝(또는 소리가 영상보다 긴 꼬리)이면 마지막 장면으로
        core.run([core.ffmpeg(), "-y", "-v", "error", "-sseof", "-3", "-i", src, "-vf", vf, "-update", "1", "-q:v", "1", str(out)])
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


CANDIDATES = "candidates3.json"
# 대사 핵심어(editor.KEYWORDS)에 더해, 반응 얼굴이 잘 나오는 감탄사
REACTIONS = {"대박": 3, "우와": 3, "미쳤": 3, "깜짝": 3, "헐": 2, "ㅋㅋ": 2, "웃기": 2}


def _cand_sig(name):
    """장면 후보를 다시 골라야 하는지 판단하는 지문: 영상 크기·수정 시각, 편집점 분석·받아쓰기 시각, 얼굴 모델 유무."""
    vs = (core.VIDEOS / name).stat()
    d = core.adir(name)
    mt = [int(f.stat().st_mtime) if f.exists() else 0 for f in (d / "analysis.json", d / "transcript.json")]
    import face
    return [vs.st_size, int(vs.st_mtime), *mt, face.ready()]


def cached_candidates(name):
    """골라 둔 장면 목록 (영상·분석·얼굴 모델이 그대로일 때만). 없거나 낡았으면 None."""
    try:
        c = json.loads((core.adir(name) / "frames" / CANDIDATES).read_text(encoding="utf-8"))
        if isinstance(c, dict) and isinstance(c.get("items"), list) and c.get("sig") == _cand_sig(name):
            return c["items"]
    except Exception:
        pass
    return None


def _keyword_times(name, top=8):
    """대사에 핵심어(꿀팁·중요…)·감탄사가 나온 순간 — 말하는 얼굴·반응이 잘 잡히는 곳."""
    try:
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        import editor
        kw = {k: w for k, w in editor.KEYWORDS.items() if k != "?"}
    except Exception:
        return []
    kw.update(REACTIONS)
    hits = []
    for s in segs if isinstance(segs, list) else []:
        try:
            text, a, b = str(s.get("text") or ""), float(s["start"]), float(s["end"])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        w = sum(v for k, v in kw.items() if k in text)
        if w > 0:
            hits.append((-w, a, b))
    hits.sort()
    return [x for _, a, b in hits[:top] for x in (a + 0.3, (a + b) / 2)]


def _score_frames(name, ts, use_faces, progress):
    """장면 여러 개를 동시에(4개씩) 뽑아 점수 매김 → {t: (점수, 얼굴 목록 또는 None)}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import face

    def one(t):
        p = grab(name, t)
        if not p.exists():
            return None
        sc, fs = float(_sharpness(p)), None
        if use_faces:
            try:
                fs = face.faces(p) or []
            except Exception:
                fs = []
            sc *= face.boost(fs)
        return sc, fs

    out = {}
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2)) as ex:
        futs = {ex.submit(one, t): t for t in ts}
        for i, fu in enumerate(as_completed(futs)):
            progress(i + 1, len(ts))
            try:
                r = fu.result()
                if r:
                    out[futs[fu]] = r
            except Exception:
                pass
    return out


def frame_candidates(name, n=8):
    """썸네일 배경 후보 장면 n개 — 좋은 순.
    점수 = 선명도·밝기·인물(_sharpness) × 얼굴 크기·웃음/놀람·얼굴 선명도 (얼굴이 안 보이면 ×0.6).
    고르게 나눈 장면 + 하이라이트 + 대사 핵심어 순간을 보고, 뽑힌 장면은 ±0.2·0.4초 옆 장면 중 표정이 가장 좋은 것으로.
    영상·편집점 분석이 바뀌면 다시 고름. 얼굴 모델을 못 받으면 예전 점수 그대로."""
    from editor import media_info  # 순환 import 피함
    import face
    items = cached_candidates(name)
    if items is not None:
        return items
    use_faces = face.ensure(item=name)  # 처음 한 번 모델 내려받기 (실패하면 조용히 예전 점수로)
    sig = _cand_sig(name)
    dur = media_info(name)["duration"]
    ts = [dur * (0.03 + 0.94 * k / 23) for k in range(24)]
    extra = core.adir(name) / "analysis.json"
    if extra.exists():
        try:
            ts += [p["time"] for p in json.loads(extra.read_text(encoding="utf-8")).get("loud_peaks", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    ts += _keyword_times(name)
    uniq = sorted(set(round(x, 1) for x in ts if 0 < x < dur))
    span = 80 if use_faces else 100  # 진행률: 장면 고르기 80% · 표정 다듬기 20%

    def prog(lo, hi, what):
        return lambda i, k: core.set_progress(label="장면 고르는 중", item=name, pct=lo + int(i * (hi - lo) / k), detail=f"{what} {i}/{k}")

    scored = _score_frames(name, uniq, use_faces, prog(0, span, "장면 살펴보는 중"))
    picked = []
    for t in sorted(scored, key=lambda x: -scored[x][0]):
        if all(abs(t - q) > dur * 0.05 for q in picked):
            picked.append(t)
        if len(picked) >= n:
            break
    if use_faces and picked:  # 뽑힌 장면 앞뒤 0.2·0.4초 중 표정(점수)이 가장 좋은 순간으로
        def near(t):
            return [x for x in (round(t + d, 1) for d in (-0.4, -0.2, 0.2, 0.4)) if 0 < x < dur]
        todo = sorted({x for t in picked for x in near(t)} - set(scored))
        scored.update(_score_frames(name, todo, True, prog(span, 100, "표정 좋은 순간 찾는 중")))
        best = []
        for k, t in enumerate(picked):  # 옮겨도 다른 장면과 너무 가까워지지 않게 (짧은 영상)
            others = best + picked[k + 1:]
            opts = sorted([t] + [x for x in near(t) if x in scored], key=lambda x: -scored[x][0])
            best += [x for x in opts if x == t or all(abs(x - q) > dur * 0.05 for q in others)][:1]
        picked = best
    items = []
    for t in sorted(picked, key=lambda x: -scored[x][0]):
        sc, fs = scored[t]
        it = {"t": t, "url": f"/frame?name={name}&t={t}", "score": round(sc, 3)}
        if fs:
            m = face.main(fs)
            it.update(faces=fs, emo=m["emo"], face=m["box"][3])  # face: 주인공 얼굴 크기 (화면 높이 대비)
        items.append(it)
    cache = _frames_dir(name) / CANDIDATES
    try:  # 임시 파일에 쓴 뒤 바꿔치기 (중간에 꺼져도 깨진 캐시가 남지 않게)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sig": sig, "items": items}, ensure_ascii=False), encoding="utf-8")
        updater._replace(tmp, cache)
    except OSError:
        pass
    return items


_DL_LOCK = threading.Lock()


def _timed_out(e):
    """대답이 없어 시간이 다 됨 (방화벽이 막은 회사·학교 인터넷 등)."""
    return isinstance(e, (socket.timeout, TimeoutError)) or isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError))


def fetch_model(fname, urls, label, detail, size=None, sha256=None, item=None, timeout=30):
    """모델 파일을 MODELS 에 (없으면) 내려받아 경로 반환. 주소를 차례로 시도하고, 받은 파일은 크기·지문(sha256)을
    확인한 뒤에만 제자리로 (중간에 끊겨도 반쪽 파일이 남지 않게). 모두 실패하면 마지막 오류를 냄.
    대답 없이 시간이 다 되면 다른 주소도 마찬가지라서 더 기다리지 않음."""
    MODELS.mkdir(parents=True, exist_ok=True)
    path = MODELS / fname
    with _DL_LOCK:
        if path.is_file():
            return path
        tmp, err = path.with_suffix(".part"), None
        for url in urls:
            def hook(got, total):
                total = total or size or 0
                if total > 0:
                    core.set_progress(label=label, item=item, pct=min(99, int(got * 100 / total)), detail=detail)
            try:
                updater.download(url, tmp, hook, timeout=timeout)
                if size and tmp.stat().st_size != size:
                    raise OSError("받은 파일 크기가 달라요")
                if sha256 and updater.sha256(tmp) != sha256:
                    raise OSError("받은 파일 확인(sha256)에 실패했어요")
                updater._replace(tmp, path)
                return path
            except Exception as e:
                err = e
                try:
                    tmp.unlink()
                except OSError:
                    pass
                if _timed_out(e):
                    break
        raise err or OSError("내려받을 주소가 없어요")


def _model(kind):
    fname, size, mean, std, logits, mb = BG_MODELS[kind]
    path = fetch_model(f"{fname}.onnx", [MODEL_URL.format(fname)], "누끼 준비 중", f"배경 제거 모델 내려받는 중 (처음 한 번, {mb})")
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
