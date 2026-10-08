"""썸네일 스타일 배우기 — 고른 유튜버(스타일)의 썸네일에서 '썸네일 버릇'을 재서 스타일 파일에 두고,
썸네일 편집기의 자동 후보·AI 추천이 그 버릇(강조색·글자 위치·크기·테두리·얼굴 크기·배경 밝기·누끼)을 따르게 한다.
그리고 YouTube '테스트 및 비교'(A/B)에서 이긴 썸네일을 한 번 눌러 적어 두면 우리 채널에서 이긴 틀·색·문구 틀에 가산점을 준다.

- 썸네일 받기: 스타일을 배운 영상(학습용·보관함)의 영상 id + 그 채널의 인기 영상 id(영상은 받지 않고 목록만)
  → https://i.ytimg.com/vi/<id>/maxresdefault.jpg (없으면 sddefault·hqdefault) → 스타일 폴더의 _thumbs/<id>.jpg 에 기억 (다시 받지 않음).
- 재기(measure): 규칙 기반 · AI 비용 없음. 글자 줄은 글자 읽기 모델(PP-OCR, 있을 때만) 상자 → 상자 안에서 바깥 띠보다 많은 색(글자색)·
  글자 높이·테두리(글자 바로 둘레가 더 먼 둘레보다 얼마나 더 어두운지) · 얼굴 크기(얼굴 모델 · 있을 때만) · 밝기·채도(HSV 평균) ·
  누끼(글자 밖의 가는 흰 선 비율 — 흰 테두리를 두른 인물 누끼).
- 버릇(summarize): 여러 장을 합친 값 → 스타일 파일의 thumb.habits. 편집기용 값(params): 강조색 2개·글자 크기 배율·위치 무게·테두리 두께·
  배경 밝기·채도 배율·누끼 가산·얼굴 크기·줄 수.
- 썸네일에 쓸 스타일: thumbnails/thumb_style.json {style} (스타일 카드·썸네일 편집기에서 고름 · 고르지 않았으면 기본 — 배우기만 해서는 바뀌지 않음).
- A/B: A/B 묶음을 저장할 때 장마다 틀·색·문구 틀을 thumbnails/ab_tests.json 에 적어 두고, 이긴 장을 누르면 winner 로 표시 →
  ours()(이긴 횟수) → 편집기가 그 틀·문구 틀에 가산점 · '우리 채널' 스타일은 이긴 색을 씀. 사용자가 브랜드 키트에서 직접 바꾼 색이 늘 먼저.
"""
import json
import re
import statistics as st
import threading
import time
import urllib.error
import urllib.request

import core
import updater

VER = 2                                   # 재는 방식이 바뀌면 올림 (예전에 잰 값은 다시 잼) · 2: 색 역할·네 쪽 테두리 (D-130 검토)
ID_RX = re.compile(r"^[A-Za-z0-9_-]{11}$")
YTIMG = "https://i.ytimg.com/vi/{}/{}"
SIZES = ("maxresdefault.jpg", "sddefault.jpg", "hqdefault.jpg")   # 큰 것부터 (maxres 가 없는 영상이 있음)
IMG_MAX = 3_000_000                       # 썸네일 한 장 최대 크기 (바이트)
IMG_MIN = 4_000                           # 이보다 작으면 '썸네일 없음' 회색 그림 (120×90)
TIMEOUT = 10
MORE_N = 12                               # 그 채널 인기 영상 썸네일을 몇 장 더 볼지
IDS_MAX = 30                              # 한 스타일에서 많아야 이만큼
MW, MH = 640, 360                         # 재는 크기
TITLE_MIN_H = 0.045                       # 제목 글자로 볼 글자 높이 (화면 높이 대비 · 작은 간판·채널 이름 빼고)
TITLE_REL = 0.45                          # 가장 큰 줄의 이 비율 넘는 줄만 제목 줄로 셈
BASE_TEXT_H = 0.147                       # 레퍼런스 47개 채널 제목 글자 높이(화면 높이 대비) 중앙값 — 이 값이면 글자 크기 배율 1 (보통 채널 = 우리 기본 크기 · D-130 검토)
BASE_BRIGHT, BASE_SAT = 0.45, 0.36        # 레퍼런스 162장 평균 밝기·채도 (refstats) — 이 값이면 배경 배율 1
CUT_THIN = 0.006                          # 글자 밖 가는 흰 선이 화면의 이 비율 넘으면 누끼(흰 테두리 인물)로 봄
STORE = "thumb_style.json"                # 썸네일에 쓸 스타일
AB_STORE = "ab_tests.json"                # A/B 묶음·이긴 장
AB_KEEP = 200
OURS = "__ours__"                         # '우리 채널 (A/B 이긴 것)' 스타일 이름
JOB = "썸네일 버릇 배우기"                  # 스타일 카드 [썸네일 버릇 배우기] 작업 이름 (휴대폰에는 진행·알림 이름만 · remote.JOB_LABELS)
_LOCK = threading.RLock()

# 글자색 후보 (편집기 기본 브랜드 색과 같은 값 — 새 색을 지어내지 않음) · 이름은 화면용
PALETTE = {"white": "#FFFFFF", "yellow": "#FFE14D", "orange": "#FF8A00", "red": "#FF3B30", "pink": "#FF5FA2",
           "cyan": "#00D1FF", "blue": "#2F6BFF", "green": "#2BD96B", "black": "#111111"}
COLOR_KO = {"#FFFFFF": "흰색", "#FFE14D": "노랑", "#FF8A00": "주황", "#FF3B30": "빨강", "#FF5FA2": "분홍",
            "#00D1FF": "하늘", "#2F6BFF": "파랑", "#2BD96B": "초록", "#111111": "검정"}
CLS = ("none",) + tuple(PALETTE)          # 화소 분류 번호 → 이름 (0 = 어느 색도 아님)


class ThumbStyleError(ValueError):
    """화면에 그대로 보여 줄 수 있는 안내 (한국어)."""


class Offline(OSError):
    """i.ytimg.com 에 닿지 않음 (인터넷·방화벽) — 남은 썸네일은 받지 않음 (한 장마다 기다리지 않게)."""


# ---------- 재기 ----------

def _hsv(a):
    """RGB(0~255 float) → H(0~1)·S·V."""
    import numpy as np
    r, g, b = a[..., 0] / 255.0, a[..., 1] / 255.0, a[..., 2] / 255.0
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0.0)
    dd = np.maximum(d, 1e-6)
    h = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) / 6.0
    h = np.where(d > 1e-6, h, 0.0)
    return h, s, mx


def classify(a):
    """화소마다 글자색 후보 번호 (CLS) — 흰색: 채도 낮고 밝음 · 검정: 어두움 · 나머지: 채도·밝기가 충분할 때 색상(H)으로."""
    import numpy as np
    h, s, v = _hsv(np.asarray(a, np.float32))
    out = np.zeros(h.shape, np.uint8)
    col = (s >= 0.4) & (v >= 0.55)
    bands = (("red", 0.0, 0.03), ("orange", 0.03, 0.105), ("yellow", 0.105, 0.19), ("green", 0.19, 0.44), ("cyan", 0.44, 0.56),
             ("blue", 0.56, 0.74), ("pink", 0.74, 0.97), ("red", 0.97, 1.01))
    for name, lo, hi in bands:
        out[col & (h >= lo) & (h < hi)] = CLS.index(name)
    out[(s < 0.13) & (v > 0.9)] = CLS.index("white")  # 베이지·밝은 회색 배경(0.84)은 흰 글자로 보지 않음
    out[v < 0.22] = CLS.index("black")
    return out


def _shift_or(m, k):
    """참/거짓 지도를 k 화소만큼 넓힘 (네모 모양 · numpy 만)."""
    out = m.copy()
    H, W = m.shape
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            if dy == 0 and dx == 0:
                continue
            ys, yd = (slice(dy, H), slice(0, H - dy)) if dy >= 0 else (slice(0, H + dy), slice(-dy, H))
            xs, xd = (slice(dx, W), slice(0, W - dx)) if dx >= 0 else (slice(0, W + dx), slice(-dx, W))
            out[yd, xd] |= m[ys, xs]
    return out


def dilate(m, k):
    """k 화소 넓히기 (k 가 크면 1 씩 여러 번 — 빠름)."""
    for _ in range(int(k)):
        m = _shift_or(m, 1)
    return m


def erode(m, k):
    return ~dilate(~m, k)


def _open(src):
    """경로·PIL 이미지 → 재는 크기 RGB (hqdefault 480×360 의 위아래 검은 띠는 잘라 냄)."""
    from PIL import Image, ImageOps
    if isinstance(src, Image.Image):
        im = ImageOps.exif_transpose(src).convert("RGB")
    else:
        with Image.open(src) as f:  # 파일을 바로 닫음 (Windows 잠김)
            im = ImageOps.exif_transpose(f).convert("RGB")
    w, h = im.size
    if abs(w / h - 4 / 3) < 0.02:  # 4:3 그림 안의 16:9 (위아래 검은 띠)
        bh = round((h - w * 9 / 16) / 2)
        im = im.crop((0, bh, w, h - bh))
    return im.resize((MW, MH) if im.size[0] >= im.size[1] else (MH, MW), Image.BILINEAR)


def _shift(m, dy, dx):
    """참/거짓 지도를 (dy, dx) 화소 옮김 (밖으로 나간 곳은 거짓)."""
    import numpy as np
    out = np.zeros_like(m)
    H, W = m.shape
    ys, yd = (slice(0, H - dy), slice(dy, H)) if dy >= 0 else (slice(-dy, H), slice(0, H + dy))
    xs, xd = (slice(0, W - dx), slice(dx, W)) if dx >= 0 else (slice(-dx, W), slice(0, W + dx))
    out[yd, xd] = m[ys, xs]
    return out


OL_DARK_BG = 0.6                          # 글자 먼 둘레가 이만큼 넘게 어두우면 (어두운 배경) 테두리를 잴 수 없음 → None


def _outline(gb, dark, gh):
    """테두리 두께(÷ 글자 높이) · 잴 수 없으면 None (D-130 검토).
    - 네 쪽(위·아래·왼쪽·오른쪽)마다 글자에서 1화소씩 바깥으로 가며 '먼 둘레보다 뚜렷이 어두운' 띠의 폭을 직접 셈
      (글자 가장자리 흐림 때문에 처음 몇 화소는 밝아도 넘어감) → 네 쪽 중 가장 얇은 쪽 = 테두리.
      그림자는 한쪽(보통 아래·오른쪽)만 어둡게 하므로 위쪽 폭이 0 이 되어 테두리로 세지 않음.
    - 배경이 어두우면 (먼 둘레 어두운 비율 > OL_DARK_BG) 테두리와 배경을 가를 수 없어 None (예전: '없음'으로 셈)."""
    k = max(2, int(round(gh * 0.16)))
    far = dilate(gb, 2 * k) & ~dilate(gb, k)
    if not far.any():
        return None
    df = float(dark[far].mean())
    if df > OL_DARK_BG:
        return None
    ws = []
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        w, prev, lead = 0, gb.copy(), max(2, k // 3)
        for i in range(1, k + 1):
            sh = _shift(gb, dy * i, dx * i)
            ring = sh & ~prev
            prev |= sh
            if not ring.any():
                break
            dn = float(dark[ring].mean())
            if dn - df >= 0.2 and dn >= 0.55:
                w += 1
            elif w or i >= lead:  # 어두운 띠가 끝났거나, 글자 가장자리 흐림(lead 화소) 뒤에도 어둡지 않음
                break
        ws.append(w)
    return min(0.3, min(ws) / max(1.0, float(gh)))


def _row(cls, v, box):
    """글자 상자 하나 → {color(가장 많은 글자색), cmix({색: 넓이}), h(글자 높이 0~1), y(가운데 0~1), outline(테두리 두께 ÷ 글자 높이), area}
    · 글자색을 못 찾으면 None. 한 줄 안에 두 색(흰 글자 + 노란 강조 낱말)이 있으면 cmix 에 둘 다."""
    import numpy as np
    H, W = cls.shape
    x0, y0, x1, y1 = box
    X0, Y0, X1, Y1 = max(0, int(x0 * W)), max(0, int(y0 * H)), min(W, int(round(x1 * W))), min(H, int(round(y1 * H)))
    if X1 - X0 < 6 or Y1 - Y0 < 6:
        return None
    pad = max(2, int((Y1 - Y0) * 0.3))
    A0, B0, A1, B1 = max(0, X0 - pad), max(0, Y0 - pad), min(W, X1 + pad), min(H, Y1 + pad)
    inner = cls[Y0:Y1, X0:X1]
    outer = cls[B0:B1, A0:A1].copy()
    ring = np.ones(outer.shape, bool)
    ring[Y0 - B0:Y1 - B0, X0 - A0:X1 - A0] = False
    ex = {}
    for c in range(1, len(CLS)):
        if CLS[c] == "black":
            continue
        fi = float((inner == c).mean())
        fr = float((outer[ring] == c).mean()) if ring.any() else 0.0
        if fi - fr > 0.04:
            ex[c] = fi - fr
    if not ex:
        return None
    best = max(ex, key=ex.get)
    sel = [c for c in ex if ex[c] >= 0.2 * ex[best]]  # 둘째 색(강조 낱말)도 글자 넓이의 1/5 넘으면 글자색으로
    g = np.isin(inner, sel)
    base = sum(float((outer[ring] == c).mean()) for c in sel) if ring.any() else 0.0  # 배경에도 그 색이 있으면 그만큼은 글자 줄로 보지 않음
    rows = np.flatnonzero(g.mean(axis=1) > base + 0.03)
    if not len(rows):
        return None
    gh = rows[-1] - rows[0] + 1
    gb = np.zeros(outer.shape, bool)
    gb[Y0 - B0:Y1 - B0, X0 - A0:X1 - A0] = g
    outline = _outline(gb, v[B0:B1, A0:A1] < 0.3, gh)
    tot = float(g.size)
    cmix = {PALETTE[CLS[c]]: round(ex[c] * tot / (H * W), 5) for c in sel}
    return {"color": PALETTE[CLS[best]], "cmix": cmix, "h": round(float(gh) / H, 4), "y": round(float(Y0 + (rows[0] + rows[-1]) / 2) / H, 4),
            "outline": None if outline is None else round(float(outline), 4), "area": round(float(g.sum()) / (H * W), 5), "x": [round(float(x0), 3), round(float(x1), 3)]}


def _merge_rows(rows):
    """같은 높이의 글자 상자(한 줄이 낱말마다 나뉜 것)를 한 줄로 셈 → 줄 목록 (위에서 아래로) · 색 넓이(cmix)는 더함."""
    out = []
    for r in sorted(rows, key=lambda r: r["y"]):
        if out and abs(r["y"] - out[-1]["y"]) < 0.5 * max(r["h"], out[-1]["h"]):
            o = out[-1]
            o["h"] = max(o["h"], r["h"])
            o["area"] = round(o["area"] + r["area"], 5)
            ol = [x for x in (o["outline"], r["outline"]) if x is not None]
            o["outline"] = max(ol) if ol else None
            for c, a in r["cmix"].items():
                o["cmix"][c] = round(o["cmix"].get(c, 0) + a, 5)
            o["color"] = max(o["cmix"], key=o["cmix"].get)
        else:
            out.append(dict(r, cmix=dict(r["cmix"])))
    return out


def _line_cols(r):
    """한 줄 → (바탕 글자색, 강조 낱말 색 | None) — 강조 색은 그 줄 글자 넓이의 15% 넘을 때만."""
    cm = sorted(((a, c) for c, a in r["cmix"].items() if c != PALETTE["black"]), reverse=True)
    if not cm:
        return r["color"], None
    tot = sum(a for a, _ in cm) or 1.0
    return cm[0][1], (cm[1][1] if len(cm) > 1 and cm[1][0] / tot >= 0.15 else None)


def roles_of(title):
    """제목 줄들 → 색 역할 {mode, base, accent, top} (D-130 검토: 색을 '역할'로 적어 편집기의 같은 역할에 씀 · 바탕·강조를 뒤바꾸지 않음)
    base = 바탕 글자색(편집기 hl2 '기본 글자') · accent = 강조색(편집기 hl '강조 글자'). 흰색이 있으면 흰색이 바탕, 다른 색이 강조
    (흰색이 없으면 넓은 색이 바탕). mode — 큰 줄이 어떻게 칠해졌나:
    - word : 큰 줄 하나에 바탕색 + 강조 낱말 (쌈바 '‘풋살’의 기본기' — 강조 낱말만 노랑)
    - line : 큰 줄 전체가 강조색 (쌈바 'ALA 움직임' 노랑 한 줄)
    - plain: 큰 줄 전체가 바탕색 (흰 큰 글자 · 작은 줄이 강조색이기도)
    - stack: 같은 크기 두 줄이 다른 색 — top: 위 줄의 역할 ('hl2' 바탕 · 'hl' 강조) (lcs '이스타. 선수 시절(흰) / 이천수랑 패싸움 한 썰(노랑)')"""
    if not title:
        return None
    hmax = max(r["h"] for r in title)
    bigs = [r for r in title if r["h"] >= 0.85 * hmax]
    area = {}
    for r in title:
        for c, a in r["cmix"].items():
            if c != PALETTE["black"]:
                area[c] = area.get(c, 0) + a
    cs = sorted(area, key=lambda c: -area[c])
    if not cs:
        return None
    if PALETTE["white"] in cs and len(cs) > 1:
        base, accent = PALETTE["white"], next(c for c in cs if c != PALETTE["white"])
    elif len(cs) > 1:
        base, accent = cs[0], cs[1]
    elif cs[0] == PALETTE["white"]:
        base, accent = cs[0], None
    else:
        base, accent = None, cs[0]
    role = lambda c: "hl" if c == accent else "hl2"  # noqa: E731
    if len(bigs) >= 2:
        t0, t1 = _line_cols(bigs[0])[0], _line_cols(bigs[-1])[0]
        if t0 != t1:
            return {"mode": "stack", "base": base, "accent": accent, "top": role(t0)}
    big = bigs[0] if len(bigs) >= 2 else max(title, key=lambda r: r["h"])
    bc, ba = _line_cols(big)
    mode = "word" if ba else "line" if bc == accent else "plain"
    return {"mode": mode, "base": base, "accent": accent}


def _cutout(cls, boxes):
    """누끼(흰·노란 테두리를 두른 인물): 글자 상자 밖의 '가는' 흰·노란 선 비율 (두꺼운 면 — 하늘·옷·벽 — 은 뺌)."""
    H, W = cls.shape
    w = (cls == CLS.index("white")) | (cls == CLS.index("yellow"))
    for x0, y0, x1, y1 in boxes:
        w[max(0, int(y0 * H) - 6):min(H, int(y1 * H) + 6), max(0, int(x0 * W) - 6):min(W, int(x1 * W) + 6)] = False
    thick = dilate(erode(w, 2), 2)
    thin = w & ~thick
    dark = dilate(cls == CLS.index("black"), 3)  # 흰 선 옆이 어두움 (배경과 인물 사이 경계)
    return round(float((thin & dark).sum()) / (H * W), 5)


def ocr_lines(im):
    """글자 읽기 모델(있을 때만 · 여기서 내려받지 않음) → [{box, h, text}] · 없으면 None."""
    try:
        import thumb
        return thumb.ocr_lines(im)
    except Exception:  # noqa: BLE001 — 모델이 없거나 깨졌으면 글자 없이 잼
        return None


def face_boxes(im):
    """얼굴 모델(받아져 있을 때만) → [[x, y, w, h]] · 없으면 None."""
    try:
        import face
        if not face.ready() or not face.ensure(label="썸네일 버릇 배우는 중"):
            return None
        return [f["box"] for f in face.faces(im, min_h=0.06) or []]
    except Exception:  # noqa: BLE001
        return None


def measure(src, lines=None, faces=None):
    """썸네일 한 장 → 버릇 재기 값. lines: 글자 상자 [{box: [x0,y0,x1,y1] 0~1}] (없으면 글자 읽기 모델) ·
    faces: 얼굴 상자 [[x, y, w, h]] (없으면 얼굴 모델). 모델이 없으면 그 값만 None."""
    import numpy as np
    im = _open(src)
    a = np.asarray(im, np.float32)
    _, s, v = _hsv(a)
    cls = classify(a)
    if lines is None:
        lines = ocr_lines(im)
    if faces is None:
        faces = face_boxes(im)
    rows, boxes = [], []
    for ln in lines or []:
        b = ln.get("box") if isinstance(ln, dict) else ln
        if not (isinstance(b, (list, tuple)) and len(b) == 4):
            continue
        boxes.append(b)
        r = _row(cls, v, b)
        if r and r["h"] >= TITLE_MIN_H * 0.6:
            rows.append(r)
    rows = _merge_rows(rows)
    top = max((r["h"] for r in rows), default=0.0)
    title = [r for r in rows if r["h"] >= max(TITLE_MIN_H, TITLE_REL * top)]
    if title:  # 제목 묶음: 위아래로 붙어 있는 줄끼리 묶어 글자가 가장 많은 묶음만 (구석의 '100만' 배지·채널 이름은 뺌)
        blocks = [[title[0]]]
        for r in title[1:]:
            p = blocks[-1][-1]
            if r["y"] - p["y"] < 1.6 * max(r["h"], p["h"]):
                blocks[-1].append(r)
            else:
                blocks.append([r])
        title = max(blocks, key=lambda b: sum(r["area"] for r in b))
    out = {"v": VER, "bright": round(float(v.mean()), 3), "sat": round(float(s.mean()), 3),
           "yellow": round(float((cls == CLS.index("yellow")).mean()), 4), "white": round(float((cls == CLS.index("white")).mean()), 4),
           "ocr": lines is not None, "lines": len(title) if lines is not None else None, "textH": None, "pos": None, "colors": [], "roles": None, "outline": None,
           "face": None, "cutout": _cutout(cls, boxes)}
    if title:
        out["textH"] = round(float(max(r["h"] for r in title)), 4)
        wsum = sum(r["area"] for r in title) or 1.0
        cy = sum(r["y"] * r["area"] for r in title) / wsum
        out["pos"] = "top" if cy < 0.42 else "bottom" if cy > 0.58 else "middle"
        ro = roles_of(title)
        out["roles"] = ro
        rest = sorted({c: a for r in title for c, a in r["cmix"].items()}.items(), key=lambda x: -x[1])
        out["colors"] = list(dict.fromkeys([c for c in (ro["accent"], ro["base"]) if c] + [c for c, _ in rest]))[:3]  # 강조색 → 바탕색 → 나머지 (화면용)
        ol = sorted((r for r in title if r["outline"] is not None), key=lambda r: -r["h"])
        out["outline"] = round(float(ol[0]["outline"]), 4) if ol else None  # 가장 큰 줄의 테두리 (어두운 배경 위 글자뿐이면 잴 수 없음)
    if faces is not None:
        hs = [float(f[3]) for f in faces if isinstance(f, (list, tuple)) and len(f) == 4]
        out["face"] = round(max(hs), 3) if hs else 0.0
    return out


# ---------- 여러 장 → 버릇 → 편집기 값 ----------

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _num(x):
    """숫자면 float · 아니면 None (스타일 파일을 메모장으로 고쳐 깨진 값이 있어도 스타일 목록이 멈추지 않게 · D-130 검토)."""
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def _top(cnt, skip=()):
    c = {k: v for k, v in cnt.items() if k and k not in skip}
    return max(c, key=lambda k: (c[k], k == PALETTE["white"])) if c else None


def summarize(ms):
    """잰 값 여러 장 → 썸네일 버릇 (없으면 None)."""
    ms = [m for m in ms or [] if isinstance(m, dict) and m.get("v") == VER]
    if not ms:
        return None
    tx = [m for m in ms if m.get("textH")]
    acc, base, modes, tops, anyc = {}, {}, {}, {}, {}
    for m in tx:
        ro = m.get("roles") if isinstance(m.get("roles"), dict) else {}
        if ro.get("accent") in COLOR_KO:
            acc[ro["accent"]] = acc.get(ro["accent"], 0) + 1
        if ro.get("base") in COLOR_KO:
            base[ro["base"]] = base.get(ro["base"], 0) + 1
        if ro.get("mode"):
            modes[ro["mode"]] = modes.get(ro["mode"], 0) + 1
        if ro.get("mode") == "stack" and ro.get("top") in ("hl", "hl2"):
            tops[ro["top"]] = tops.get(ro["top"], 0) + 1
        for c in m.get("colors") or []:
            if c in COLOR_KO:
                anyc[c] = anyc.get(c, 0) + 1
    c_acc = _top(acc, (PALETTE["black"],))
    c_base = _top(base, (PALETTE["black"], c_acc))
    if c_acc is None and c_base is None:  # 역할을 못 잰 옛 값 — 많이 나온 색 둘
        c_acc = _top(anyc, (PALETTE["black"],))
    pos = {k: 0 for k in ("top", "middle", "bottom")}
    for m in tx:
        if m.get("pos") in pos:
            pos[m["pos"]] += 1
    share = {k: round(v / len(tx), 2) for k, v in pos.items()} if tx else None
    lines = [m["lines"] for m in tx if isinstance(m.get("lines"), int) and m["lines"] > 0]
    ols = [m["outline"] for m in tx if _num(m.get("outline")) is not None]
    fs = [m for m in ms if m.get("face") is not None]
    fh = [m["face"] for m in fs if m["face"] > 0]
    mode = max(modes, key=modes.get) if modes else None
    return {
        "n": len(ms), "text": len(tx), "colors": [c for c in (c_acc, c_base) if c],  # [강조색, 바탕 글자색]
        "accent": c_acc, "base": c_base, "mode": mode, "modeShare": round(modes[mode] / len(tx), 2) if mode else None,
        "stackTop": _top(tops) if mode == "stack" else None,
        "lines": int(round(st.median(lines))) if lines else None,
        "textH": round(float(st.median(m["textH"] for m in tx)), 3) if tx else None,
        "pos": max(share, key=share.get) if share and any(share.values()) else None, "posShare": share,
        "outline": round(float(st.median(ols)), 3) if ols else None, "outlineN": len(ols),
        "face": round(float(st.median(fh)), 3) if fh else (0.0 if fs else None), "faceShare": round(len(fh) / len(fs), 2) if fs else None,
        "bright": round(float(st.mean(float(m["bright"]) for m in ms)), 3), "sat": round(float(st.mean(float(m["sat"]) for m in ms)), 3),
        "yellow": round(float(st.mean(float(m.get("yellow") or 0) for m in ms)), 4), "white": round(float(st.mean(float(m.get("white") or 0) for m in ms)), 4),
        "cutout": round(sum(1 for m in ms if (m.get("cutout") or 0) >= CUT_THIN) / len(ms), 2),
    }


OL_THIN, OL_THICK = 0.015, 0.045          # 테두리 두께(÷ 글자 높이) 나눔 — 0.015 밑 없음 · 0.045 넘으면 두껍게


def outline_tier(o):
    """테두리 두께(÷ 글자 높이) → 0 없음 · 1 얇게 · 2 두껍게 · 잴 수 없으면 None."""
    o = _num(o)
    return None if o is None else 0 if o < OL_THIN else 1 if o < OL_THICK else 2


SW_OF_TIER = (0.07, 0.09, 0.13)   # 편집기 바깥 획(글자 크기 대비) — 0.07 은 부드러운 그림자와 함께 대비 통과 하한, 0.09 는 프리텐다드 블랙 기본(THIN_SW), 0.13 은 '굵은 획'
OL_STRONG_N = 2                   # 두꺼운 획(0.13)은 테두리를 잰 썸네일이 이만큼 넘을 때만 (한 장만 재면 얇게 — 프리텐다드 블랙 얇은 획 상한 유지)


def eff_tier(h):
    """편집기에 쓰는 테두리 단계 — 두껍게(2)는 테두리를 잰 썸네일이 OL_STRONG_N 장 넘을 때만 (아니면 얇게)."""
    t = outline_tier(h.get("outline"))
    return 1 if t == 2 and (_num(h.get("outlineN")) or 0) < OL_STRONG_N else t


def params(h):
    """버릇 → 편집기(p8_ai.js styleBrand·headline·styleBonus)가 쓰는 값. 없는 칸은 빼서 기본값 그대로.
    색은 역할로: hl = 강조색(강조 낱말·큰 줄) · hl2 = 바탕 글자색 · big = 'line' 제목 큰 줄을 어느 역할 색으로 칠할지 ·
    stack = 같은 크기 두 줄일 때 먼저 읽히는 줄의 역할. 바탕·강조를 뒤바꾸지 않음 (강조 낱말은 늘 hl)."""
    if not isinstance(h, dict):
        return None
    out = {}
    acc = h.get("accent") if h.get("accent") in COLOR_KO else None
    base = h.get("base") if h.get("base") in COLOR_KO else None
    if acc is None and base is None and "accent" not in h:  # 옛 버릇(색 목록만)
        cs = [c for c in h.get("colors") or [] if c in COLOR_KO and c != PALETTE["black"]]
        acc = cs[0] if cs else None
    if acc or base:
        out["hl"] = acc or (PALETTE["yellow"] if base != PALETTE["yellow"] else PALETTE["white"])
        out["hl2"] = base if base and base != out["hl"] else (PALETTE["white"] if out["hl"] != PALETTE["white"] else PALETTE["yellow"])
    mode = h.get("mode")
    if mode == "plain":
        out["big"] = "hl2"   # 큰 줄은 바탕색(흰 큰 글자) · 작은 줄이 강조색
    elif mode == "line":
        out["big"] = "hl"    # 큰 줄 전체가 강조색 ('word' 제목도 큰 줄 바탕을 강조색으로) · 'word' 버릇은 기본 역할 그대로 (바탕 + 강조 낱말)
    elif mode == "stack" and h.get("stackTop") in ("hl", "hl2"):
        out["stack"] = h["stackTop"]
    th = _num(h.get("textH"))
    if th:
        out["textScale"] = round(float(_clamp(th / BASE_TEXT_H, 0.85, 1.25)), 2)
        out["textH"] = round(float(_clamp(th, 0.08, 0.35)), 3)  # 글자 높이 가산점(styleBonus · 롱폼) — 크게 쓰는 채널은 큰 글자 후보를 앞으로
    if isinstance(h.get("posShare"), dict):
        out["posW"] = {k: _num(h["posShare"].get(k)) or 0.0 for k in ("top", "middle", "bottom")}
    t = eff_tier(h)
    if t is not None:
        out["sw"] = SW_OF_TIER[t]
    br, sa = _num(h.get("bright")), _num(h.get("sat"))
    if br is not None:
        out["bgBright"] = round(_clamp(1 + (br - BASE_BRIGHT) * 1.3, 0.8, 1.22), 3)
    if sa is not None:
        out["bgSat"] = round(_clamp(1 + (sa - BASE_SAT) * 1.2, 0.85, 1.2), 3)
    if _num(h.get("cutout")) is not None:
        out["cut"] = _num(h["cutout"])
    if _num(h.get("face")) is not None and (_num(h.get("faceShare")) or 0) >= 0.34:
        out["face"] = _num(h["face"])
    if _num(h.get("lines")):
        out["lines"] = max(1, min(2, int(_num(h["lines"]))))  # 편집기 제목은 한 줄 또는 두 줄
    return out


def _pct(x):
    return f"{round(x * 100)}%"


MODE_KO = {"word": "강조 낱말만 강조색", "line": "큰 줄 전체 강조색", "plain": "큰 줄은 흰 글자", "stack": "두 줄 색을 나눔"}


def describe(h):
    """버릇 → 쉬운 한 줄."""
    if not isinstance(h, dict):
        return ""
    bits = []
    acc, base = h.get("accent"), h.get("base")
    if "accent" not in h:  # 옛 버릇(색 목록만)
        cs = [c for c in h.get("colors") or [] if isinstance(c, str)]
        acc, base = (cs[0] if cs else None), (cs[1] if len(cs) > 1 else None)
    cb = ([f"강조색 {COLOR_KO.get(acc, acc)}"] if isinstance(acc, str) else []) + ([f"바탕 글자 {COLOR_KO.get(base, base)}"] if isinstance(base, str) else [])
    if cb:
        bits.append(" · ".join(cb) + (f"({MODE_KO[h['mode']]})" if h.get("mode") in MODE_KO else ""))
    th, ln = _num(h.get("textH")), _num(h.get("lines"))
    if th:
        bits.append(f"큰 글자 {int(ln or 1)}줄(글자 높이 화면의 {_pct(th)})")
    if h.get("pos") in ("top", "middle", "bottom"):
        bits.append({"top": "위쪽", "middle": "가운데", "bottom": "아래쪽"}[h["pos"]])
    t = eff_tier(h)
    if t is not None:
        bits.append(("테두리 없음", "얇은 테두리", "두꺼운 테두리")[t])
    fc = _num(h.get("face"))
    if fc and (_num(h.get("faceShare")) or 0) >= 0.34:
        bits.append(f"얼굴 {'크게' if fc >= 0.3 else '보통' if fc >= 0.18 else '작게'}({_pct(fc)})")
    br = _num(h.get("bright"))
    if br is not None:
        bits.append(f"{'어두운' if br < 0.4 else '밝은' if br > 0.52 else '보통 밝기'} 배경({_pct(br)})")
    cu = _num(h.get("cutout"))
    if cu is not None:
        bits.append("누끼 " + ("자주" if cu >= 0.5 else "가끔" if cu > 0 else "안 씀"))
    return " · ".join(bits)


# ---------- 썸네일 받기 (i.ytimg.com · 한 번 받으면 기억) ----------

def cache_dir():
    import style
    return style.STYLES / "_thumbs"


def thumb_path(vid):
    """기억해 둔 썸네일 파일 (영상 id 꼴이 아니면 None)."""
    if not (isinstance(vid, str) and ID_RX.match(vid)):
        return None
    return cache_dir() / f"{vid}.jpg"


def _get(url, timeout=TIMEOUT):
    """주소 → 바이트 (테스트에서 바꿔 끼움)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with updater.urlopen(req, timeout) as r:
        return r.read(IMG_MAX + 1)


def fetch(vid):
    """영상 id → 썸네일 파일 (이미 있으면 그것) · 못 받으면 None."""
    p = thumb_path(vid)
    if p is None:
        return None
    if p.is_file() and p.stat().st_size >= IMG_MIN:
        return p
    for size in SIZES:
        try:
            data = _get(YTIMG.format(vid, size))
        except urllib.error.HTTPError:
            continue  # 그 크기가 없음 (404) → 다음 크기
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise Offline(str(e)) from None
        except (OSError, ValueError):
            continue
        if not (IMG_MIN <= len(data) <= IMG_MAX):
            continue
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                if im.size[0] < 320:
                    continue
        except Exception:  # noqa: BLE001 — 그림이 아님
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        updater.write_atomic(p, data)
        return p
    return None


# ---------- 스타일에 배우기 ----------

def _ids_of(names):
    """배운 영상 이름 → 영상 id (학습용 기록 → 파일 이름 꼴)."""
    import refs
    import source
    out = []
    for n in names:
        try:
            vid = (refs.find(n) or {}).get("videoId") or source.video_id(n)
        except Exception:  # noqa: BLE001
            vid = source.video_id(n)
        if vid and ID_RX.match(vid):
            out.append(vid)
    return list(dict.fromkeys(out))


def _channel_url(names):
    """배운 영상들이 한 채널이면 그 채널 주소 (학습용 기록에 있을 때만) · 아니면 None."""
    try:
        import refs
        data = refs.load()
        keys = {(data["files"].get(n) or {}).get("channelKey") for n in names if n in data["files"]} - {None, ""}
        if len(keys) != 1:
            return None
        return (data["channels"].get(keys.pop()) or {}).get("url") or None
    except Exception:  # noqa: BLE001
        return None


def _sources(d):
    src = d.get("source")
    return [x for x in (src if isinstance(src, list) else [src]) if isinstance(x, str)]


def learn(style_name, log=print, more=True, count=MORE_N):
    """스타일이 배운 영상(+ 그 채널 인기 영상 count 개)의 썸네일 → 버릇 → 스타일 파일 thumb 에 저장. → {name, thumb, desc}."""
    import style
    f = style.style_file(style_name)
    d = json.loads(f.read_text(encoding="utf-8"))
    names = _sources(d)
    ids = _ids_of(names)
    url = _channel_url(names) if more else None
    if url:
        try:
            core.set_progress(label="썸네일 버릇 배우는 중", item=style_name, pct=None, detail="채널 인기 영상 목록을 불러오는 중")
            rows = core.list_videos("videos", None, url, log)
            ids += [r["id"] for r in rows[:count] if isinstance(r.get("id"), str)]
        except Exception as e:  # noqa: BLE001 — 목록을 못 받아도 배운 영상 썸네일로
            log(f"  채널 인기 영상 목록을 못 받았어요 · {e}")
    ids = [i for i in dict.fromkeys(ids) if ID_RX.match(i)][:IDS_MAX]
    if not ids:
        raise ThumbStyleError("이 스타일은 유튜브 영상으로 배우지 않아서 썸네일을 찾을 수 없어요 (학습용 영상으로 배운 스타일만 돼요)")
    old = {r.get("id"): r for r in ((d.get("thumb") or {}).get("refs") or []) if isinstance(r, dict) and r.get("v") == VER}
    ms, miss = [], 0
    offline = False
    for k, vid in enumerate(ids, 1):
        if style._cancelled():
            raise style.StyleCancelled("썸네일 버릇 배우기를 멈췄어요")
        core.set_progress(label="썸네일 버릇 배우는 중", item=style_name, step=f"{k}/{len(ids)}", pct=int(k * 100 / len(ids)), detail="썸네일 살펴보는 중")
        if vid in old:
            ms.append(old[vid])
            continue
        p = thumb_path(vid)
        if offline:
            p = p if p and p.is_file() and p.stat().st_size >= IMG_MIN else None
        else:
            try:
                p = fetch(vid)
            except Offline as e:
                offline, p = True, None
                log(f"  썸네일을 받을 수 없어요 (인터넷 연결 확인) · {e}")
        if p is None:
            miss += 1
            continue
        try:
            ms.append(dict(measure(p), id=vid))
        except Exception as e:  # noqa: BLE001 — 한 장이 깨져도 나머지로
            miss += 1
            log(f"  썸네일을 살펴보지 못했어요 · {vid} · {e}")
    h = summarize(ms)
    if not h:
        raise ThumbStyleError("썸네일을 받지 못했어요. 인터넷 연결을 확인한 뒤 다시 눌러 주세요")
    th = {"v": VER, "habits": h, "refs": ms, "learned": time.strftime("%Y-%m-%d %H:%M"), "missing": miss}
    style.update_style(style_name, lambda d: d.__setitem__("thumb", th))
    desc = describe(h)
    log(f"썸네일 버릇 · {style_name} · {len(ms)}장 · {desc}")
    return {"name": style_name, "thumb": th, "desc": desc}


def learn_quiet(style_names, log=print):
    """스타일 배우기 끝에 이어서 (실패해도 스타일 배우기는 그대로 · 로그에 한 줄)."""
    out = {}
    for n in dict.fromkeys(x for x in style_names if isinstance(x, str) and x):
        try:
            out[n] = learn(n, log)["desc"]
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "StyleCancelled":
                raise
            log(f"  썸네일 버릇은 배우지 못했어요 · {n} · {e}")
    return out


def card(d):
    """스타일 파일 → 스타일 카드·편집기에 보낼 썸네일 버릇 {habits, params, desc, n, learned, ids} · 없으면 None."""
    th = d.get("thumb") if isinstance(d, dict) else None
    if not (isinstance(th, dict) and isinstance(th.get("habits"), dict)):
        return None
    h = th["habits"]
    try:
        return {"habits": h, "params": params(h), "desc": describe(h), "n": int(_num(h.get("n")) or 0), "learned": th.get("learned"),
                "ids": [r["id"] for r in th.get("refs") or [] if isinstance(r, dict) and isinstance(r.get("id"), str) and ID_RX.match(r["id"])][:8]}
    except Exception:  # noqa: BLE001 — 메모장으로 고쳐 깨진 버릇: 이 스타일만 '썸네일 버릇 없음'으로 (스타일 목록은 그대로 · D-130 검토)
        return None


# ---------- 썸네일에 쓸 스타일 ----------

def _thumbs_dir():
    import thumb
    return thumb.THUMBS


def _read(name, default):
    try:
        d = json.loads((_thumbs_dir() / name).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else default
    except (OSError, ValueError):
        return default


def _write(name, d):
    p = _thumbs_dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    updater.write_atomic(p, json.dumps(d, ensure_ascii=False))


def styles_with_thumbs():
    """썸네일 버릇이 있는 스타일 [{name, ...card}] (가장 최근에 배운 것부터)."""
    import style
    out = []
    style.STYLES.mkdir(parents=True, exist_ok=True)
    for f in sorted(style.STYLES.glob("*.json")):
        try:
            c = card(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if c:
            out.append(dict(c, name=f.stem))
    return sorted(out, key=lambda c: c.get("learned") or "", reverse=True)


def get_pick():
    """썸네일에 쓸 스타일 이름 · '' = 스타일 안 씀 · None = 고른 적 없음."""
    v = _read(STORE, {}).get("style")
    return v if isinstance(v, str) else None


def set_pick(name):
    name = "" if name is None else str(name)
    if name and name != OURS:
        import style
        style.style_file(name)  # 없는 스타일이면 StyleMissing
    with _LOCK:
        _write(STORE, {"style": name})
    return name


def editor_view():
    """썸네일 편집기용: {styles: [{name, desc, params}], pick, active: {name, params, desc} | None, ours}.
    사장님이 [썸네일에 이 스타일 쓰기]로 고른 스타일만 씀 — 고른 적이 없거나 고른 스타일이 지워졌으면 기본 (None)."""
    sts = styles_with_thumbs()
    o = ours()
    pick = get_pick()
    names = {s["name"] for s in sts}
    eff = pick if (pick == OURS and o["n"]) or pick in names else ""  # 고른 적 없음·고른 스타일이 지워짐 → 기본 (배우기만 해서는 바뀌지 않음 · D-130 검토)
    act = None
    if eff == OURS:
        act = {"name": OURS, "params": o["params"], "desc": o["desc"]}
    elif eff:
        s = next(s for s in sts if s["name"] == eff)
        act = {"name": eff, "params": s["params"], "desc": s["desc"]}
    return {"styles": [{"name": s["name"], "desc": s["desc"], "params": s["params"], "n": s["n"]} for s in sts], "pick": pick,
            "active": act, "ours": o}


# ---------- A/B (YouTube '테스트 및 비교') ----------

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _meta_ok(m):
    if not isinstance(m, dict):
        return None
    out = {}
    for k, n in (("tpl", 60), ("pid", 40), ("l1", 40), ("l2", 40), ("style", 60)):
        v = m.get(k)
        if isinstance(v, str) and v and len(v) <= n:
            out[k] = v
    cs = m.get("colors") if isinstance(m.get("colors"), dict) else {}
    out["colors"] = {k: cs[k].upper() for k in ("hl", "hl2") if isinstance(cs.get(k), str) and _HEX.match(cs[k])}
    ro = m.get("roles") if isinstance(m.get("roles"), dict) else {}
    out["roles"] = {k: ro[k] for k in ("big", "stack") if ro.get(k) in ("hl", "hl2")}  # 그 장을 그린 색 역할 (없으면 기본 역할)
    return out


def record_ab(name, files, metas):
    """A/B 묶음을 저장할 때: 장마다 틀·색·문구 틀 (이긴 장은 나중에 win)."""
    metas = metas if isinstance(metas, list) else []
    items = []
    for k, f in enumerate(files):
        m = _meta_ok(metas[k] if k < len(metas) else None) or {}
        items.append(dict(m, tag="ABCDEF"[k], file=str(f)))
    with _LOCK:
        d = _read(AB_STORE, {})
        sets = [s for s in d.get("sets") or [] if isinstance(s, dict)]
        sid, used = int(time.time() * 1000), {s.get("id") for s in sets}
        while str(sid) in used:  # 같은 1/1000초에 둘 (겹치면 이긴 장이 다른 묶음에 적힘)
            sid += 1
        sid = str(sid)
        sets.append({"id": sid, "name": str(name), "at": time.strftime("%Y-%m-%d %H:%M"), "items": items, "winner": None})
        d["sets"] = sets[-AB_KEEP:]
        _write(AB_STORE, d)
    return sid


def ab_sets(name=None):
    """A/B 묶음 목록 (새 것부터) · name 을 주면 그 영상 것만."""
    sets = [s for s in _read(AB_STORE, {}).get("sets") or [] if isinstance(s, dict) and isinstance(s.get("items"), list)]
    if name is not None:
        sets = [s for s in sets if s.get("name") == name]
    return list(reversed(sets))


def set_winner(sid, tag):
    """이긴 장 적기 (tag '' 이면 지움) → 그 묶음."""
    tag = str(tag or "")
    with _LOCK:
        d = _read(AB_STORE, {})
        for s in d.get("sets") or []:
            if isinstance(s, dict) and s.get("id") == str(sid):
                if tag and tag not in {i.get("tag") for i in s.get("items") or []}:
                    raise ThumbStyleError("그 썸네일을 찾지 못했어요")
                s["winner"] = tag or None
                s["wonAt"] = time.strftime("%Y-%m-%d %H:%M") if tag else None
                _write(AB_STORE, d)
                return s
    raise ThumbStyleError("그 A/B 묶음을 찾지 못했어요")


def ours():
    """우리 채널에서 이긴 것 모음 → {n, tpl: {틀: 횟수}, pid: {문구 틀: 횟수}, colors: {hl, hl2}, params, desc}.
    params 는 가장 많이 이긴 '모양'(색 + 색 역할) 그대로 — 기본 모양으로 이긴 장이면 기본과 같게 그림 (D-130 검토: 색만 따로 모으면 다른 그림이 됨)."""
    tpl, pid, looks = {}, {}, {}
    n = 0
    for s in ab_sets():
        w = next((i for i in s["items"] if isinstance(i, dict) and i.get("tag") == s.get("winner")), None)
        if not w:
            continue
        n += 1
        for dct, v in ((tpl, w.get("tpl")), (pid, w.get("pid"))):
            if v:
                dct[v] = dct.get(v, 0) + 1
        cs = w.get("colors") if isinstance(w.get("colors"), dict) else {}
        ro = w.get("roles") if isinstance(w.get("roles"), dict) else {}
        if cs.get("hl") and cs.get("hl2"):
            key = (cs["hl"], cs["hl2"], ro.get("big") or "", ro.get("stack") or "")
            looks[key] = looks.get(key, 0) + 1
    top = lambda dct: max(dct, key=dct.get) if dct else None  # noqa: E731
    lk = top(looks)
    cols = {"hl": lk[0], "hl2": lk[1]} if lk else {}
    prm = dict(cols)
    if lk and lk[2]:
        prm["big"] = lk[2]
    if lk and lk[3]:
        prm["stack"] = lk[3]
    desc = ""
    if n:
        desc = f"A/B {n}번 이긴 것" + (f" · 틀 '{top(tpl)}'" if tpl else "") + (" · 강조색 " + "·".join(COLOR_KO.get(c, c) for c in cols.values()) if cols else "")
    return {"n": n, "tpl": tpl, "pid": pid, "colors": cols, "params": prm, "desc": desc}
