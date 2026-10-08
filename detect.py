"""사람·공 찾기 (썸네일 장면 고르기 · 전술 그래픽 자리 잡기용).
YOLOX-nano (github.com/Megvii-BaseDetection/YOLOX 릴리스 0.1.1rc0 의 yolox_nano.onnx, Apache-2.0, 3.6MB, 입력 416×416, COCO: 사람 0 · 공 32)
— 처음 쓸 때 한 번 받음. 못 받거나 실행이 안 되면 None 을 돌려주고, 장면 고르기는 얼굴·피부 어림으로 계속해요 (face.py 와 같은 실패 정책).
입력은 0~255 BGR 그대로(정규화 없음), 왼쪽 위에 붙인 레터박스(빈 곳 114)."""
import threading
import time

import core
import studiolog
import thumb
import updater

URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx"
FILE, SIZE_B, SHA = "yolox_nano.onnx", 3659407, "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"
INPUT = 416
STRIDES = (8, 16, 32)
PERSON, BALL = 0, 32
P_THR = 0.35      # 사람일 확률
B_THR = 0.2       # 공은 작아서 낮게
NMS_THR = 0.45
MIN_PERSON_H = 0.06  # 화면 높이 6% 보다 작은 사람(관중석·먼 곳)은 버림
RETRY = 600       # 못 받았으면 10분 동안은 다시 시도하지 않음
TIMEOUT = 10
_MARK = ".detect-download-failed"

_LOCK = threading.Lock()
_SESS = {}
_FAIL = {"t": 0.0}


def ready():
    """모델 파일이 온전히 있는지 (내려받지 않음 — 장면 캐시 확인용). 받다 끊긴 반쪽 ONNX 는 없는 것으로 (thumb.model_whole · D-071) ·
    크기도 SIZE_B 와 같아야 (맨 위 칸 경계에서 끊겨 모양만 맞는 파일 — model_whole 은 모양만 봄 · D-078)
    → 다음 ensure 의 thumb.fetch_model 이 지우고 다시 받음 (받다 끊기면 .part 로 남겨 이어받기)."""
    p = thumb.MODELS / FILE
    return thumb._size(p) == SIZE_B and thumb.model_whole(p)


def _recent(t):
    return 0 <= time.time() - t < RETRY


def _mark_time():
    try:
        return (thumb.MODELS / _MARK).stat().st_mtime
    except OSError:
        return 0.0


def ensure(item=None, label="장면 고르는 중"):
    """모델을 (없으면) 받고 불러 둠. 쓸 수 있으면 True, 아니면 조용히 False."""
    with _LOCK:
        if _SESS:
            return True
        if _recent(_FAIL["t"]) or (not ready() and _recent(_mark_time())):
            return False
        core.set_progress(label=label, item=item, pct=None, detail="선수·공 찾기 모델 준비 중…" if ready() else "선수·공 찾기 모델 확인 중… (처음 한 번)")
        try:
            try:
                path = thumb.fetch_model(FILE, [URL], label, "선수·공 찾기 모델 내려받는 중 (처음 한 번, 약 4MB)", SIZE_B, SHA, item, timeout=TIMEOUT)
            except Exception:
                try:  # 앱을 다시 켜도 10분 동안은 다시 기다리지 않게
                    thumb.MODELS.mkdir(parents=True, exist_ok=True)
                    updater.write_atomic(thumb.MODELS / _MARK, str(int(time.time())))
                except OSError:
                    pass
                raise
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3
            so.intra_op_num_threads = 1   # 장면 4개를 동시에 보므로 장면 하나는 1코어로
            so.inter_op_num_threads = 1
            sess = ort.InferenceSession(path.read_bytes(), so, providers=["CPUExecutionProvider"])  # 내용으로 넘김: 한글 사용자 폴더도 안전
        except Exception as e:  # 못 받거나(인터넷) 실행이 안 됨 — 장면 고르기는 얼굴·피부 어림으로 계속 (studio.log 에 한 줄)
            _FAIL["t"] = time.time()
            studiolog.write(f"선수·공 찾기 모델을 쓰지 못했어요 · {type(e).__name__}: {' '.join(str(e).split())[:200]}")
            return False
        try:
            (thumb.MODELS / _MARK).unlink()
        except OSError:
            pass
        _SESS["det"] = sess
        return True


def letterbox(rgb, size=INPUT):
    """PIL RGB → (모델 입력 1×3×size×size float32, 줄인 비율). 왼쪽 위에 붙이고 남는 곳은 114."""
    import numpy as np
    from PIL import Image
    W, H = rgb.size
    r = min(size / W, size / H)
    nw, nh = max(1, int(W * r)), max(1, int(H * r))
    c = np.full((size, size, 3), 114, np.float32)
    c[:nh, :nw] = np.asarray(rgb.resize((nw, nh), Image.BILINEAR), np.float32)
    return np.ascontiguousarray(c[..., ::-1].transpose(2, 0, 1)[None], np.float32), r


def decode(out, size=INPUT):
    """모델 출력(N×85: 칸 기준 중심·크기 로짓, 물체 점수, 종류 80) → (상자 x1,y1,x2,y2 입력 px, 종류별 점수 N×80)."""
    import numpy as np
    grids, strides = [], []
    for st in STRIDES:
        g = size // st
        yv, xv = np.meshgrid(np.arange(g), np.arange(g), indexing="ij")
        grids.append(np.stack([xv, yv], -1).reshape(-1, 2))
        strides.append(np.full((g * g, 1), st))
    grids, strides = np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)
    xy = (out[:, :2] + grids) * strides
    wh = np.exp(np.clip(out[:, 2:4], -10, 10)) * strides
    boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1)
    return boxes, out[:, 4:5] * out[:, 5:]


def nms(b, s, thr=NMS_THR):
    """겹친 상자 정리: 점수 높은 순으로 남기고 IoU > thr 인 것은 버림 → 남길 번호."""
    import numpy as np
    order, keep = np.argsort(-s, kind="stable"), []
    area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    while order.size:
        i, rest = order[0], order[1:]
        keep.append(int(i))
        w = np.clip(np.minimum(b[i, 2], b[rest, 2]) - np.maximum(b[i, 0], b[rest, 0]), 0, None)
        h = np.clip(np.minimum(b[i, 3], b[rest, 3]) - np.maximum(b[i, 1], b[rest, 1]), 0, None)
        inter = w * h
        order = rest[inter / (area[i] + area[rest] - inter + 1e-9) <= thr]
    return keep


def _run(rgb):
    """한 장(또는 일부분)을 모델에 넣고 → 원래 그림 px 기준 상자·점수."""
    x, r = letterbox(rgb)
    s = _SESS["det"]
    out = s.run(None, {s.get_inputs()[0].name: x})[0][0]
    boxes, sc = decode(out)
    return boxes / r, sc


def _pick(boxes, sc, cls, thr):
    m = sc[:, cls] > thr
    return boxes[m], sc[m, cls]


def people(src, ball_retry=True):
    """사진(경로나 PIL 이미지) 속 사람·공 → {"persons": [[x, y, w, h, 확률]...](0~1, 큰 사람부터), "ball": [x, y, w, h, 확률] 또는 None}.
    공을 못 찾으면 왼쪽·오른쪽 반쪽을 크게 다시 봄(공이 작게 보이는 넓은 장면). 모델을 쓸 수 없으면 None (먼저 ensure())."""
    if "det" not in _SESS:
        return None
    import numpy as np
    from PIL import Image, ImageOps
    if isinstance(src, Image.Image):
        rgb = ImageOps.exif_transpose(src).convert("RGB")
    else:
        with Image.open(src) as im:  # 파일을 바로 닫음 (Windows 에서 장면 파일이 잠기지 않게)
            rgb = ImageOps.exif_transpose(im).convert("RGB")
    W, H = rgb.size
    boxes, sc = _run(rgb)
    pb, ps = _pick(boxes, sc, PERSON, P_THR)
    bb, bs = _pick(boxes, sc, BALL, B_THR)
    if ball_retry and not len(bb):
        parts = [(bb, bs)]
        for x0, x1 in ((0, int(W * 0.55)), (int(W * 0.45), W)):
            b2, s2 = _run(rgb.crop((x0, 0, x1, H)))
            b2, s2 = _pick(b2, s2, BALL, B_THR)
            parts.append((b2 + np.array([x0, 0, x0, 0], np.float32), s2))
        bb, bs = np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])
    persons = []
    if len(pb):
        for i in nms(pb, ps):
            x1, y1, x2, y2 = np.clip(pb[i], [0, 0, 0, 0], [W, H, W, H])
            if (y2 - y1) / H < MIN_PERSON_H or x2 - x1 < 4:
                continue
            persons.append([round(float(x1 / W), 4), round(float(y1 / H), 4), round(float((x2 - x1) / W), 4), round(float((y2 - y1) / H), 4), round(float(ps[i]), 3)])
    persons.sort(key=lambda p: -p[3])
    ball = None
    if len(bb):
        i = nms(bb, bs)[0]
        x1, y1, x2, y2 = np.clip(bb[i], [0, 0, 0, 0], [W, H, W, H])
        if x2 > x1 and y2 > y1:
            ball = [round(float(x1 / W), 4), round(float(y1 / H), 4), round(float((x2 - x1) / W), 4), round(float((y2 - y1) / H), 4), round(float(bs[i]), 3)]
    return {"persons": persons[:12], "ball": ball}
