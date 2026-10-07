"""영상 기획 분석용 모델 — 화면 글자 읽기(OCR)와 소리 종류 듣기(웃음·음악·효과음).

- 글자 읽기: PaddleOCR PP-OCRv5 mobile (글자 찾기 det + 한국어 읽기 rec, RapidOCR 의 ONNX 변환본, Apache-2.0)
- 소리 듣기: Google YAMNet (521가지 소리, ONNX 변환본, Apache-2.0)
처음 한 번 ~/.futsal-studio/models 로 내려받음 (약 35MB, 고정 주소·크기·sha256 확인 — face.py 와 같은 방식).
못 받거나 실행이 안 되면 조용히 False/None 을 돌려주고, 기획 분석은 화면·소리 어림 규칙으로 계속해요."""
import threading
import time

import core
import thumb

_MS = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
_HF_OCR = "https://huggingface.co/monkt/paddleocr-onnx/resolve/7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65/languages/korean/"
_HF_YAM = "https://huggingface.co/zeropointnine/yamnet-onnx/resolve/ac2ca3bd45d12ec1f19f1144205ea529b4e9dedf/"
# 이름: (파일, [(주소, 크기, sha256) …]) — 고정된 판(tag·커밋) 먼저, 같은 파일의 다른 서버를 뒤에
_DET = ("ocr-ppocrv5-det-mobile.onnx", 4819576, "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae")
_REC = ("ocr-ppocrv5-korean-rec-mobile.onnx", 13488748, "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b")
_DICT = ("ocr-ppocrv5-korean-dict.txt", 47451, "a88071c68c01707489baa79ebe0405b7beb5cca229f4fc94cc3ef992328802d7")
SPECS = {
    "ocr": {
        "det": (_DET[0], [(_MS + rev + "/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx", _DET[1], _DET[2]) for rev in ("v3.9.2", "master")]),
        # 허깅페이스 쪽은 변환기가 달라 크기·지문이 다름 (글자 목록은 같은 사전 파일)
        "rec": (_REC[0], [(_MS + rev + "/onnx/PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile.onnx", _REC[1], _REC[2]) for rev in ("v3.9.2", "master")]
                + [(_HF_OCR + "rec.onnx", 13401252, "322f140154c820fcb83c3d24cfe42c9ec70dd1a1834163306a7338136e4f1eaa")]),
        "dict": (_DICT[0], [(_MS + "v3.9.2/paddle/PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile/ppocrv5_korean_dict.txt", _DICT[1], _DICT[2]),
                            (_HF_OCR + "dict.txt", _DICT[1], _DICT[2])]),
    },
    "audio": {
        "yamnet": ("yamnet.onnx", [(_HF_YAM + "yamnet.onnx", 16093603, "1510041dce24a2e9e84ec546807ac408ae496da6d1ed41bc3ccba649623f8e19")]),
    },
}
LABEL = {"ocr": "자막 글자 읽기", "audio": "웃음소리·효과음 듣기"}
RETRY = 600       # 못 받았으면 10분 동안은 다시 시도하지 않음 (앱을 다시 켜도)
TIMEOUT = 10      # 대답 없는 인터넷(방화벽)에서 오래 붙잡히지 않게
DETAIL = "영상 분석 모델 내려받는 중 (처음 한 번, 약 35MB)"

# YAMNet 소리 종류 번호 (yamnet_class_map.csv) — 묶음별 가장 큰 값만 씀
Y_SPEECH = (0,)
Y_LAUGH = (13, 14, 15, 16, 17, 18)            # Laughter · Baby laughter · Giggle · Snicker · Belly laugh · Chuckle
Y_CHEER = (61, 62)                            # Cheering · Applause
Y_MUSIC = (132,)
# 편집 효과음: Rimshot·Drum roll·Cymbal·Gong·Bell·Chime·Ding-dong·Explosion·Burst/pop·Boom·Whoosh·Slap·Whip·Beep·Ding·Clang·Zing·Boing·Sound effect
# (공 차는 소리와 헷갈리는 Thump·Bang 은 뺌)
Y_SFX = (161, 162, 166, 172, 195, 200, 350, 420, 428, 430, 453, 461, 466, 475, 477, 478, 491, 492, 498)
Y_STEP = 0.48     # YAMNet 한 칸 간격(초)
Y_SR = 16000
Y_CHUNK = 60      # 한 번에 듣는 길이(초) — 메모리를 적게

DET_W = 640       # 글자 찾기 입력 폭 (960 보다 빠르고 잡음 상자가 적었음)
DET_THR = 0.3     # 글자일 확률
REC_H, REC_MAXW = 48, 960
MIN_BOX_H = 0.025  # 화면 높이의 2.5% 보다 낮은 글자 상자는 버림 (멀리 있는 간판 등)
MIN_CONF = 0.6

_LOCK = threading.Lock()
_SESS = {}
_FAIL = {}


def _mark(kind):
    return thumb.MODELS / f".plan-{kind}-download-failed"


def ready(kind):
    """그 묶음의 모델 파일이 다 있는지 (내려받지 않음)."""
    return all((thumb.MODELS / f).is_file() for f, _ in SPECS[kind].values())


def _recent(t):
    return 0 <= time.time() - t < RETRY


def _mark_time(kind):
    try:
        return _mark(kind).stat().st_mtime
    except OSError:
        return 0.0


def usable(kind):
    """지금 쓸 수 있거나 쓸 수 있을 것 같은지 (불러 둔 모델 · 또는 파일이 있고 최근에 불러오기가 실패하지 않음) — 기획 기록 다시 쓰기 판단용."""
    return kind in _SESS or (ready(kind) and not _recent(_FAIL.get(kind, 0.0)))


def _drop_files(kind):
    """파일은 있는데 불러오지 못함(깨진 파일·글자 목록이 안 맞는 변환본) → 지워서 다음에 고정 주소에서 새로 받게."""
    for fname, _ in SPECS[kind].values():
        try:
            (thumb.MODELS / fname).unlink()
        except OSError:
            pass


def ensure(kind, item=None, label="스타일 배우는 중", threads=2, cancel=None):
    """kind('ocr' | 'audio') 모델을 (없으면) 내려받고 불러 둠. 쓸 수 있으면 True, 아니면 조용히 False.
    cancel(): 참이면 내려받기를 멈추고 thumb.DownloadCancelled (반쪽 파일·실패 표시는 남기지 않음)."""
    with _LOCK:
        if kind in _SESS:
            return True
        if _recent(_FAIL.get(kind, 0.0)) or (not ready(kind) and _recent(_mark_time(kind))):
            return False
        core.set_progress(label=label, item=item, pct=None, detail=f"{LABEL[kind]} 준비 중…" if ready(kind) else DETAIL)
        try:
            paths = {}
            for k, (fname, urls) in SPECS[kind].items():
                try:
                    paths[k] = thumb.fetch_model(fname, urls, label, DETAIL, item=item, timeout=TIMEOUT, cancel=cancel)
                except thumb.DownloadCancelled:
                    raise
                except Exception:
                    try:  # 앱을 다시 켜도 10분 동안은 다시 기다리지 않게
                        _mark(kind).write_text(str(int(time.time())), encoding="utf-8")
                    except OSError:
                        pass
                    raise
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3       # 모델 경고 글은 숨김
            so.intra_op_num_threads = threads
            so.inter_op_num_threads = 1
            # 파일 대신 내용을 넘김: 사용자 폴더 이름이 한글이어도 안전
            sess = {k: ort.InferenceSession(p.read_bytes(), so, providers=["CPUExecutionProvider"]) for k, p in paths.items() if k != "dict"}
            if kind == "ocr":
                meta = sess["rec"].get_modelmeta().custom_metadata_map.get("character")
                chars = (meta or paths["dict"].read_text(encoding="utf-8")).splitlines()
                n_out = sess["rec"].get_outputs()[0].shape[-1]
                sess["chars"] = ["<blank>"] + chars + [" "]
                if isinstance(n_out, int) and n_out != len(sess["chars"]):
                    raise ValueError("글자 목록과 모델이 맞지 않아요")
        except thumb.DownloadCancelled:
            raise
        except Exception:
            _FAIL[kind] = time.time()
            if ready(kind):  # 다 받았는데 불러오지 못함 → 깨진 파일을 지우고 10분 뒤 새로 받음
                _drop_files(kind)
                try:
                    _mark(kind).write_text(str(int(time.time())), encoding="utf-8")
                except OSError:
                    pass
            return False
        try:
            _mark(kind).unlink()
        except OSError:
            pass
        _SESS[kind] = sess
        return True


def available(kind):
    return kind in _SESS


# ---------- 글자 읽기 ----------

def _components(mask):
    """글자 확률 지도(참/거짓) → 이어진 덩어리 상자 [(y0, x0, y1, x1, 화소 수)] — 줄마다 이어진 구간(run)을 묶는 union-find (8방향)."""
    import numpy as np
    H, W = mask.shape
    m = np.zeros((H, W + 2), bool)
    m[:, 1:-1] = mask
    d = np.diff(m.astype(np.int8), axis=1)
    runs_y, runs_a, runs_b = [], [], []
    for y in range(H):
        st = np.flatnonzero(d[y] == 1)
        en = np.flatnonzero(d[y] == -1)
        runs_y += [y] * len(st)
        runs_a += st.tolist()
        runs_b += (en - 1).tolist()
    n = len(runs_y)
    if not n:
        return []
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    prev = []  # 윗줄 구간 번호
    cur_y = -1
    cur = []
    for i in range(n):
        y = runs_y[i]
        if y != cur_y:
            prev = cur if y == cur_y + 1 else []
            cur, cur_y = [], y
        a, b = runs_a[i], runs_b[i]
        for j in prev:
            if runs_a[j] <= b + 1 and runs_b[j] >= a - 1:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
        cur.append(i)
    boxes = {}
    for i in range(n):
        r = find(i)
        y, a, b = runs_y[i], runs_a[i], runs_b[i]
        bx = boxes.get(r)
        if bx is None:
            boxes[r] = [y, a, y, b, b - a + 1]
        else:
            bx[0], bx[1], bx[2], bx[3], bx[4] = min(bx[0], y), min(bx[1], a), max(bx[2], y), max(bx[3], b), bx[4] + b - a + 1
    return [tuple(v) for v in boxes.values()]


def ocr(rgb):
    """화면(RGB numpy, H×W×3) 속 글자 줄 → [{text, box[x0,y0,x1,y1](0~1), h(0~1), conf}]. 모델이 없으면 None."""
    import numpy as np
    from PIL import Image
    s = _SESS.get("ocr")
    if not s:
        return None
    H, W = rgb.shape[:2]
    img = Image.fromarray(rgb)
    sc = DET_W / W
    nh = max(32, int(round(H * sc / 32)) * 32)
    x = np.asarray(img.resize((DET_W, nh), Image.BILINEAR), np.float32) / 255.0
    x = ((x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)).transpose(2, 0, 1)[None]
    det = s["det"]
    prob = det.run(None, {det.get_inputs()[0].name: x.astype(np.float32)})[0][0, 0]
    sy, sx = H / nh, W / DET_W
    out = []
    rec, chars = s["rec"], s["chars"]
    for y0, x0, y1, x1, area in _components(prob > DET_THR):
        bh = y1 - y0 + 1
        if area < 20 or bh * sy / H < MIN_BOX_H * 0.6:
            continue
        pad = 0.6 * bh  # 글자 확률 지도는 글자보다 조금 안쪽만 잡음 (unclip 대신 높이 비율만큼 넓힘)
        X0, Y0 = max(0, int((x0 - pad) * sx)), max(0, int((y0 - pad) * sy))
        X1, Y1 = min(W, int((x1 + 1 + pad) * sx)), min(H, int((y1 + 1 + pad) * sy))
        if (Y1 - Y0) / H < MIN_BOX_H or X1 - X0 < 8:
            continue
        crop = img.crop((X0, Y0, X1, Y1))
        rw = max(16, min(REC_MAXW, int(REC_H * (X1 - X0) / (Y1 - Y0))))
        r = (np.asarray(crop.resize((rw, REC_H), Image.BILINEAR), np.float32) / 255.0 - 0.5) / 0.5
        o = rec.run(None, {rec.get_inputs()[0].name: r.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
        idx = o.argmax(1)
        txt, prev, ps = [], 0, []
        for k, i in enumerate(idx):
            if i != prev and i != 0 and i < len(chars):
                txt.append(chars[i])
                ps.append(float(o[k, i]))
            prev = i
        t = "".join(txt).strip()
        conf = float(np.mean(ps)) if ps else 0.0
        n_al = sum(ch.isalnum() for ch in t)
        # 한 글자 효과 자막('쾅!' '헉')은 한글이고 또렷할 때만 (무늬를 글자로 잘못 읽은 것과 구별)
        if conf < MIN_CONF or n_al < 1 or (n_al < 2 and not (conf >= 0.85 and any("가" <= ch <= "힣" for ch in t))):
            continue
        out.append({"text": t, "box": [round(X0 / W, 4), round(Y0 / H, 4), round(X1 / W, 4), round(Y1 / H, 4)],
                    "h": round((Y1 - Y0) / H, 4), "conf": round(conf, 3), "px": (X0, Y0, X1, Y1)})
    return out


# ---------- 소리 듣기 ----------

def tags(wave):
    """16kHz 흑백 소리(float32 numpy) → 0.48초마다 묶음별 점수 {speech, laugh, cheer, music, sfx} (0~1 numpy). 모델이 없으면 None."""
    import numpy as np
    s = _SESS.get("audio")
    if not s:
        return None
    y = s["yamnet"]
    name = y.get_inputs()[0].name
    out = []
    n = len(wave)
    step = Y_CHUNK * Y_SR
    for a in range(0, max(1, n), step):
        seg = np.asarray(wave[a:a + step], np.float32)
        if len(seg) < 15600:  # 한 칸(0.96초)보다 짧은 끝 조각은 조용한 소리로 채움
            seg = np.pad(seg, (0, 15600 - len(seg)))
        sc = y.run(None, {name: seg})[0]
        want = int(np.ceil(min(step, n - a) / (Y_STEP * Y_SR)))
        out.append(sc[:max(1, want)])
    sc = np.concatenate(out, axis=0) if out else np.zeros((0, 521), np.float32)
    grp = lambda ix: sc[:, list(ix)].max(axis=1) if len(sc) else np.zeros(0, np.float32)  # noqa: E731
    return {"speech": grp(Y_SPEECH), "laugh": grp(Y_LAUGH), "cheer": grp(Y_CHEER), "music": grp(Y_MUSIC), "sfx": grp(Y_SFX)}
