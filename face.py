"""얼굴·표정 점수 (썸네일 장면 고르기용).
얼굴 찾기 UltraFace(RFB-320) · 표정 FER+ — 둘 다 ONNX 모델 모음(github.com/onnx/models, MIT)에서 처음 한 번 내려받음.
모델을 못 받거나 실행이 안 되면 None 을 돌려주고, 장면 고르기는 예전 점수로 계속해요."""
import threading
import time

import core
import thumb

_ZOO = "validated/vision/body_analysis/"
# 모델 모음 저장소의 고정된 판(커밋)을 먼저 — main 의 폴더 구성이 바뀌어도 받을 수 있게. main 은 마지막 시도
_REV = "c5cae0f1942c8d7727372c7b1f6b485aa39e4421"
_HOSTS = tuple(h + rev + "/" for rev in (_REV, "main")
               for h in ("https://media.githubusercontent.com/media/onnx/models/", "https://github.com/onnx/models/raw/"))
# 이름: (파일, 모델 모음 안 경로, 크기(바이트), sha256)
SPECS = {
    "detect": ("version-RFB-320.onnx", "ultraface/models/version-RFB-320.onnx", 1270727,
               "34cd7e60aeff28744c657de7a3dc64e872d506741de66987f3426f2b79f88017"),
    "emotion": ("emotion-ferplus-8.onnx", "emotion_ferplus/model/emotion-ferplus-8.onnx", 35040571,
                "a2a2ba6a335a3b29c21acb6272f962bd3d47f84952aaffa03b60986e04efa61c"),
}
EMOTIONS = ("neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt")
MIN_H = 0.08      # 화면 높이의 8% 보다 작은 얼굴(멀리 있는 선수)은 무시
THRESH = 0.8      # 얼굴일 확률
RETRY = 600       # 못 받았으면 10분 동안은 다시 시도하지 않음 (장면 고를 때마다 기다리지 않게 — 앱을 다시 켜도)
TIMEOUT = 10      # 대답 없는 인터넷(방화벽)에서 오래 붙잡히지 않게
_MARK = ".face-download-failed"  # 마지막으로 못 받은 시각 (모델 폴더 안)

_LOCK = threading.Lock()
_SESS = {}
_FAIL = {"t": 0.0}


def ready():
    """두 모델 파일이 다 있는지 (내려받지 않음 — 장면 캐시 확인용)."""
    return all((thumb.MODELS / f).is_file() for f, *_ in SPECS.values())


def _recent(t):
    return 0 <= time.time() - t < RETRY


def _mark_time():
    try:
        return (thumb.MODELS / _MARK).stat().st_mtime
    except OSError:
        return 0.0


def ensure(item=None, label="장면 고르는 중"):
    """모델을 (없으면) 내려받고 불러 둠. 쓸 수 있으면 True, 아니면 조용히 False."""
    with _LOCK:
        if _SESS:
            return True
        if _recent(_FAIL["t"]) or (not ready() and _recent(_mark_time())):
            return False
        core.set_progress(label=label, item=item, pct=None, detail="얼굴·표정 모델 준비 중…" if ready() else "얼굴·표정 모델 확인 중… (처음 한 번)")
        try:
            paths = {}
            for k, (fname, rel, size, sha) in SPECS.items():
                try:
                    paths[k] = thumb.fetch_model(fname, [h + _ZOO + rel for h in _HOSTS], label,
                                                 "얼굴·표정 모델 내려받는 중 (처음 한 번, 약 36MB)", size, sha, item, timeout=TIMEOUT)
                except Exception:
                    try:  # 앱을 다시 켜도 10분 동안은 다시 기다리지 않게
                        (thumb.MODELS / _MARK).write_text(str(int(time.time())), encoding="utf-8")
                    except OSError:
                        pass
                    raise
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3       # 모델 경고 글은 숨김
            so.intra_op_num_threads = 1     # 장면 4개를 동시에 보므로 장면 하나는 1코어로
            so.inter_op_num_threads = 1
            # 파일 대신 내용을 넘김: 사용자 폴더 이름이 한글이어도 안전
            sess = {k: ort.InferenceSession(p.read_bytes(), so, providers=["CPUExecutionProvider"]) for k, p in paths.items()}
        except Exception:
            _FAIL["t"] = time.time()
            return False
        try:
            (thumb.MODELS / _MARK).unlink()
        except OSError:
            pass
        _SESS.update(sess)
        return True


def _nms(b, s, thr=0.3):
    import numpy as np
    order, keep = s.argsort()[::-1], []
    area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    while order.size:
        i, rest = order[0], order[1:]
        keep.append(i)
        w = np.clip(np.minimum(b[i, 2], b[rest, 2]) - np.maximum(b[i, 0], b[rest, 0]), 0, None)
        h = np.clip(np.minimum(b[i, 3], b[rest, 3]) - np.maximum(b[i, 1], b[rest, 1]), 0, None)
        inter = w * h
        order = rest[inter / (area[i] + area[rest] - inter + 1e-9) <= thr]
    return keep


def _detect(rgb):
    """얼굴 상자 [(x1, y1, x2, y2, 확률)] (0~1). 가로 화면은 320×240 에 맞춰 넣고, 세로 화면은 비율 유지(남는 곳 회색)."""
    import numpy as np
    from PIL import Image
    W, H = rgb.size
    if W >= H * 1.2:
        canvas, ox, oy, nw, nh = rgb.resize((320, 240), Image.BILINEAR), 0, 0, 320, 240
    else:
        sc = min(320 / W, 240 / H)
        nw, nh = max(1, round(W * sc)), max(1, round(H * sc))
        canvas, ox, oy = Image.new("RGB", (320, 240), (127, 127, 127)), (320 - nw) // 2, (240 - nh) // 2
        canvas.paste(rgb.resize((nw, nh), Image.BILINEAR), (ox, oy))
    x = ((np.asarray(canvas, np.float32) - 127.0) / 128.0).transpose(2, 0, 1)[None]
    det = _SESS["detect"]
    scores, boxes = det.run(None, {det.get_inputs()[0].name: x})
    p = scores[0][:, 1]
    ok = p > THRESH
    if not ok.any():
        return []
    b = (boxes[0][ok] * [320, 240, 320, 240] - [ox, oy, ox, oy]) / [nw, nh, nw, nh]
    b, p = np.clip(b, 0.0, 1.0), p[ok]
    return [(*b[i].tolist(), float(p[i])) for i in _nms(b, p)]


def _expr(gray, box):
    """얼굴을 정사각형으로 잘라 표정 확률과 선명도(0~1)."""
    import numpy as np
    from PIL import Image
    W, H = gray.size
    x1, y1, x2, y2 = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    cx, cy, half = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 0.55
    crop = (max(0, round(cx - half)), max(0, round(cy - half)), min(W, round(cx + half)), min(H, round(cy + half)))
    a = np.asarray(gray.crop(crop).resize((64, 64), Image.BILINEAR), np.float32)  # 화면 밖은 검게 채우지 않고 안쪽만
    emo = _SESS["emotion"]
    o = emo.run(None, {emo.get_inputs()[0].name: a[None, None]})[0][0]  # FER+: 흑백 64×64, 0~255 그대로
    e = np.exp(o - o.max())
    e /= e.sum()
    lap = a[1:-1, 1:-1] * 4 - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    # 흔들려 뭉개진 얼굴(분산 20 이하)=0 · 또렷한 얼굴(500 이상)=1
    sharp = float(np.clip((np.log1p(lap.var()) - np.log1p(20)) / (np.log1p(500) - np.log1p(20)), 0, 1))
    return {k: round(float(v), 3) for k, v in zip(EMOTIONS, e)}, round(sharp, 3)


def faces(src, min_h=MIN_H, top=6):
    """사진(경로나 PIL 이미지) 속 얼굴 — 큰 얼굴부터
    [{box: [x, y, w, h] (0~1), score, emo: {happiness, surprise, ...}, sharp: 0~1}].
    모델을 쓸 수 없으면 None (내려받지 않음 — 먼저 ensure())."""
    if not _SESS:
        return None
    from PIL import Image, ImageOps
    if isinstance(src, Image.Image):
        rgb = ImageOps.exif_transpose(src).convert("RGB")
    else:
        with Image.open(src) as img:  # 파일을 바로 닫음 (Windows 에서 장면 파일이 잠기지 않게)
            rgb = ImageOps.exif_transpose(img).convert("RGB")
    # 작은 얼굴·화면 밖으로 거의 나간 얼굴은 빼고, 큰 얼굴부터
    found = sorted((f for f in _detect(rgb) if f[3] - f[1] >= min_h and f[2] - f[0] >= 0.02), key=lambda f: f[1] - f[3])[:top]
    gray, out = rgb.convert("L"), []
    for x1, y1, x2, y2, p in found:
        emo, sharp = _expr(gray, (x1, y1, x2, y2))
        out.append({"box": [round(x1, 4), round(y1, 4), round(x2 - x1, 4), round(y2 - y1, 4)],
                    "score": round(p, 3), "emo": emo, "sharp": sharp})
    return out


def expression(f):
    """웃음·놀람 정도 (0~1)."""
    e = f.get("emo") or {}
    return min(1.0, e.get("happiness", 0) + e.get("surprise", 0))


def weight(f):
    """얼굴 하나의 장면 점수 배율: 클수록(화면 높이 45%까지) · 웃거나 놀랄수록 · 또렷할수록 큼 (약 0.56~3.1)."""
    return (1 + 1.6 * min(f["box"][3], 0.45)) * (1 + 0.8 * expression(f)) * (0.5 + 0.5 * f.get("sharp", 1))


def boost(fs):
    """장면 점수에 곱할 값. 얼굴이 없으면 0.6."""
    return max(map(weight, fs)) if fs else 0.6


def main(fs):
    """장면의 주인공 얼굴 (배율이 가장 큰 얼굴) 또는 None."""
    return max(fs, key=weight) if fs else None
