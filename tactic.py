"""편집실 전술 그림 (D-180): 영상 위 형광 화살표·곡선 화살표·패스 길(점선)·발밑 원·스포트라이트·선수 이름표.

- ops_at(tc, t, W, H)   그 순간 그릴 것 (채운 다각형·글자) — editor.html 의 tacOps 와 같은 계산이라 미리보기 = 내보내기
                         (그리는 법: 모든 선은 둥근 끝 다각형으로 바꿔 '채우기' 하나로 · 겹치는 곳은 같은 방향(nonzero) · 구멍은 반대 방향)
- ass_lines(...)        내보내기 자막(ASS) 그림 줄 — 프레임마다 모양을 계산하고 같은 모양이 이어지면 한 줄로
- track(...)            선수 따라가기 — detect.py(YOLOX)로 고른 선수 상자를 구간 동안 쫓고 부드럽게 (원본 기준 0~1)
- normalize(tc)         저장본 정리 (모르는 값·이상한 값은 기본값 · 예전 편집본엔 tactics 가 없어도 그대로 열림)
그림 모양·색은 썸네일 전술 그래픽(thumb_src/parts/p1_core.js TAC_DEF · p8_ai.js tactics)과 같은 비율을 씀.
"""
import math
import subprocess
import threading

import core

KINDS = ("arrow", "curve", "pass", "ring", "spot", "label")
NAMES = {"arrow": "형광 화살표", "curve": "곡선 화살표", "pass": "패스 길 (점선)", "ring": "발밑 원", "spot": "스포트라이트", "label": "선수 이름표"}
# 썸네일 브랜드 색(p8_ai.js BRAND_DEF · p1_core.js PRESETS)의 형광 색들
PALETTE = (("#00D1FF", "하늘 네온"), ("#A6FF00", "형광 연두"), ("#FFE14D", "노랑"), ("#FF3B30", "빨강"), ("#FF3EA5", "분홍"), ("#FFFFFF", "흰색"))
# 종류별 기본값 · width 선 두께, size 원 반지름/스포트라이트 반지름/이름표 글자 크기 — 모두 화면 짧은 변 1080 기준 px
# (썸네일 1280 기준: 화살표 19 · 원 두께 10 · 원 너비 = 선수 너비 × 1.7)
DEFAULTS = {
    "arrow": {"color": "#FFE14D", "width": 14, "draw": 0.6},
    "curve": {"color": "#FFE14D", "width": 14, "draw": 0.7},
    "pass": {"color": "#A6FF00", "width": 9, "draw": 0.8},
    "ring": {"color": "#00D1FF", "width": 10, "size": 110, "draw": 0.5},
    "spot": {"color": "#FFFFFF", "size": 210, "dim": 0.6, "draw": 0.5},
    "label": {"color": "#FFE14D", "size": 40, "text": "선수", "draw": 0.35},
}
NPTS = {"arrow": 2, "curve": 3, "pass": 3, "ring": 1, "spot": 1, "label": 1}
BASE = 1080.0       # 두께·크기 기준 (화면 짧은 변)
SEG = 48            # 곡선을 이만큼의 곧은 조각으로
CAP_N = 8           # 둥근 끝 반원 조각 수
ELL_N = 48          # 타원 한 바퀴 조각 수 (반지름 200px 에서도 조각 사이 휨 0.9px 미만)
FADE_OUT = 0.25     # 끝나기 전 이만큼(초) 동안 흐려지며 사라짐
GLOW_W = 2.2        # 빛 번짐: 선 두께 × 이만큼 넓은 선을 흐리게
GLOW_A = 0.55
GLOW_BLUR = 0.75    # 빛 번짐 흐림 = 선 두께 × 이만큼 (가우스 표준편차 px)
CORE_W = 0.34       # 가운데 흰 심선 (썸네일 core = 두께 × 0.34)
CORE_A = 0.9
HEAD_K = 3.4        # 화살촉 길이 = 선 두께 × 이만큼 (썸네일 19 → 70)
DASH_ON, DASH_OFF = 1.4, 1.5   # 패스 점선 (썸네일 dash [1.4, 1.5] × 두께)
MARCH = 0.6         # 점선이 한 칸 흘러가는 시간(초) — 패스 방향으로 움직임
RING_RY = 0.32      # 발밑 원 납작함 (세로 반지름 = 가로 × 이만큼 · 썸네일 0.3)
RING_BACK = 0.45    # 원 뒤쪽 반(선수 뒤로 돌아가는 쪽)은 이만큼 흐리게
RING_FILL = 0.22
SPOT_FEATHER = 0.1  # 스포트라이트 가장자리 흐림 = 반지름 × 이만큼
ASS_BLUR = 1.1774   # libass \blur 값 = 가우스 표준편차 × √(2 ln 2) (실측: \blur10 → σ 8.47)
LIMIT_FOLLOW = 30.0  # 선수 따라가기는 한 번에 이만큼(초)까지
TRACK_FPS = 8.0      # 따라가기에서 1초에 보는 장면 수


def r2(v):
    """소수 둘째 자리 (JS Math.round 와 같게 .5 는 올림 — 미리보기와 숫자가 똑같이)."""
    return math.floor(v * 100 + 0.5) / 100


def _clamp(v, a, b):
    return a if v < a else b if v > b else v


def _num(v, d, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    if not math.isfinite(x):
        return d
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def _hex(c, d):
    s = str(c or "")
    return s.upper() if len(s) == 7 and s[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in s[1:]) else d


def normalize(tc):
    """저장본의 전술 그림 하나 → 정리한 사본 (못 쓰는 것이면 None)."""
    if not isinstance(tc, dict) or tc.get("kind") not in KINDS:
        return None
    k = tc["kind"]
    d = DEFAULTS[k]
    pts = []
    for p in (tc.get("pts") or [])[:NPTS[k]]:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            pts.append([_num(p[0], 0.5, -0.5, 1.5), _num(p[1], 0.5, -0.5, 1.5)])
    if len(pts) < NPTS[k]:
        return None
    out = dict(tc, pts=pts, start=_num(tc.get("start"), 0.0, 0.0), dur=_num(tc.get("dur"), 3.0, 0.1, 3600.0),
               color=_hex(tc.get("color"), d["color"]), draw=_num(tc.get("draw"), d["draw"], 0.0, 10.0), glow=tc.get("glow", True) is not False)
    if "width" in d:
        out["width"] = _num(tc.get("width"), d["width"], 2.0, 80.0)
    if "size" in d:
        out["size"] = _num(tc.get("size"), d["size"], 10.0, 1500.0)
    if k == "spot":
        out["dim"] = _num(tc.get("dim"), d["dim"], 0.0, 0.95)
    if k == "label":
        out["text"] = str(tc.get("text") if tc.get("text") is not None else d["text"])[:30]
    fol = []
    for f in tc.get("follow") or []:
        if isinstance(f, (list, tuple)) and len(f) >= 3:
            fol.append([_num(f[0], 0.0, 0.0), _num(f[1], 0.0, -2.0, 2.0), _num(f[2], 0.0, -2.0, 2.0)])
    out["follow"] = sorted(fol) or None
    return out


# ---------- 모양 계산 (editor.html tacOps 와 짝 — 한쪽을 고치면 다른 쪽도) ----------

def _ease(x):
    x = _clamp(x, 0.0, 1.0)
    return 1 - (1 - x) * (1 - x) * (1 - x)


def follow_at(fol, e):
    """따라가기 옮김 [dx, dy] (화면 비율) — 시각 사이는 곧게 이음 · 처음·끝 밖은 그 값 그대로."""
    if not fol:
        return 0.0, 0.0
    if e <= fol[0][0]:
        return fol[0][1], fol[0][2]
    for a, b in zip(fol, fol[1:]):
        if e <= b[0]:
            x = (e - a[0]) / ((b[0] - a[0]) or 1)
            return a[1] + (b[1] - a[1]) * x, a[2] + (b[2] - a[2]) * x
    return fol[-1][1], fol[-1][2]


def _quad(P):
    """점 2개(직선) 또는 3개(2차 곡선: 시작·휘는 곳·끝) → SEG+1 개의 점."""
    a, c, b = (P[0], [(P[0][0] + P[1][0]) / 2, (P[0][1] + P[1][1]) / 2], P[1]) if len(P) < 3 else (P[0], P[1], P[2])
    out = []
    for i in range(SEG + 1):
        t = i / SEG
        u = 1 - t
        out.append([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]])
    return out


def _cum(S):
    c = [0.0]
    for i in range(1, len(S)):
        c.append(c[-1] + math.hypot(S[i][0] - S[i - 1][0], S[i][1] - S[i - 1][1]))
    return c


def _point(S, C, l):
    """길이 l 자리의 점과 그 자리 방향 (단위 벡터)."""
    l = _clamp(l, 0.0, C[-1])
    i = 1
    while i < len(S) - 1 and C[i] < l:
        i += 1
    seg = (C[i] - C[i - 1]) or 1.0
    x = (l - C[i - 1]) / seg
    dx, dy = S[i][0] - S[i - 1][0], S[i][1] - S[i - 1][1]
    d = math.hypot(dx, dy) or 1.0
    return [S[i - 1][0] + dx * x, S[i - 1][1] + dy * x], [dx / d, dy / d]


def _part(S, C, a, b):
    """길이 a~b 사이의 꺾은선."""
    p0, _ = _point(S, C, a)
    out = [p0]
    for i in range(len(S)):
        if a < C[i] < b:
            out.append(S[i])
    p1, _ = _point(S, C, b)
    out.append(p1)
    return out


def _area(ct):
    s = 0.0
    for i in range(len(ct)):
        x0, y0 = ct[i]
        x1, y1 = ct[(i + 1) % len(ct)]
        s += x0 * y1 - x1 * y0
    return s


def _orient(ct, hole=False):
    """채우는 윤곽은 늘 같은 방향, 구멍은 반대 방향 (겹쳐도 비지 않게 · 두 그리기(캔버스·libass) 모두 nonzero)."""
    if (_area(ct) < 0) != hole:
        ct = ct[::-1]
    return ct


def _circle(cx, cy, r, n=CAP_N * 2):
    return [[cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)] for k in range(n)]


def _stroke(pts, h):
    """꺾은선 → 두께 2h 의 둥근 끝 윤곽 하나."""
    P = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - P[-1][0], p[1] - P[-1][1]) > 1e-6:
            P.append(p)
    if len(P) < 2:
        return _orient(_circle(P[0][0], P[0][1], h))
    n = len(P)
    N = []
    for i in range(n):
        a, b = P[max(0, i - 1)], P[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy) or 1.0
        N.append([-dy / d, dx / d])
    left = [[P[i][0] + N[i][0] * h, P[i][1] + N[i][1] * h] for i in range(n)]
    right = [[P[i][0] - N[i][0] * h, P[i][1] - N[i][1] * h] for i in range(n)]
    ne, de = N[-1], [N[-1][1], -N[-1][0]]
    ns, ds = N[0], [N[0][1], -N[0][0]]
    endcap, startcap = [], []
    cn = max(2, min(CAP_N, int(math.ceil(h / 2))))   # 가는 선은 둥근 끝 조각을 적게 (점선 칸이 많아도 ASS 가 커지지 않게)
    for k in range(1, cn):
        f = math.pi * k / cn
        c, s = math.cos(f), math.sin(f)
        endcap.append([P[-1][0] + h * (ne[0] * c + de[0] * s), P[-1][1] + h * (ne[1] * c + de[1] * s)])
        startcap.append([P[0][0] + h * (-ns[0] * c - ds[0] * s), P[0][1] + h * (-ns[1] * c - ds[1] * s)])
    return _orient(left + endcap + right[::-1] + startcap)


def _arc_band(cx, cy, rx, ry, h, a0, a1):
    """타원 띠 (a0~a1 라디안 · 두께 2h)."""
    n = max(2, int(math.ceil(ELL_N * (a1 - a0) / (2 * math.pi))))
    outer, inner = [], []
    for k in range(n + 1):
        a = a0 + (a1 - a0) * k / n
        c, s = math.cos(a), math.sin(a)
        outer.append([cx + (rx + h) * c, cy + (ry + h) * s])
        inner.append([cx + max(0.5, rx - h) * c, cy + max(0.5, ry - h) * s])
    return _orient(outer + inner[::-1])


def _ellipse(cx, cy, rx, ry, hole=False):
    return _orient([[cx + rx * math.cos(2 * math.pi * k / ELL_N), cy + ry * math.sin(2 * math.pi * k / ELL_N)] for k in range(ELL_N)], hole)


def _head(tip, d, size, k=1.0):
    """끝점이 꼭짓점인 화살촉 삼각형 (썸네일 headPoly 와 같은 비율 · k: 안쪽 심선용으로 줄임)."""
    ux, uy = d
    ln, half = size * k, size * 0.58 * k
    tx, ty = tip[0] - ux * size * (1 - k) * 0.35, tip[1] - uy * size * (1 - k) * 0.35
    bx, by = tx - ux * ln, ty - uy * ln
    return _orient([[tx, ty], [bx - uy * half, by + ux * half], [bx + uy * half, by - ux * half]])


def _grow(ct, s):
    cx = sum(p[0] for p in ct) / len(ct)
    cy = sum(p[1] for p in ct) / len(ct)
    return [[cx + (p[0] - cx) * s, cy + (p[1] - cy) * s] for p in ct]


def _ivs(a0, a1, ivs):
    """[a0, a1] 과 겹치는 구간들."""
    out = []
    for lo, hi in ivs:
        a, b = max(a0, lo), min(a1, hi)
        if b - a > 1e-6:
            out.append((a, b))
    return out


def em_width(text):
    """글자 폭 어림 (글자 크기 배) — editor.py _em(검은 굵기) · editor.html emOf 와 같은 값."""
    w = 0.0
    for ch in str(text):
        o = ord(ch)
        w += 0.24 if ch == " " else 0.95 if o >= 0x1100 else 0.6 if ("0" <= ch <= "9" or "a" <= ch <= "z") else 0.78 if "A" <= ch <= "Z" else 0.42
    return w


def _luma(hexc):
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def ops_at(tc, t, W, H):
    """시각 t(타임라인 초)에 그릴 것 → [{"k": "poly", "c": 윤곽들, "col", "a": 진하기, "b": 흐림 px} | {"k": "text", ...}] (W×H px 기준).
    보이지 않는 때면 []. (editor.html tacOps 와 같은 계산)"""
    e = t - tc["start"]
    if e < 0 or e >= tc["dur"]:
        return []
    k = tc["kind"]
    u = min(W, H) / BASE
    A = _clamp((tc["dur"] - e) / FADE_OUT, 0.0, 1.0)
    dr = tc.get("draw") or 0.0
    p = 1.0 if dr <= 0 else _ease(e / dr)
    fx, fy = follow_at(tc.get("follow"), e)
    ox, oy = fx * W, fy * H
    pts = [[x * W, y * H] for x, y in tc["pts"]]
    if k in ("arrow", "curve", "pass"):  # 화살표·패스: 출발점은 선수를 따라가고 끝(가리키는 곳)은 그대로 · 휘는 곳은 반만
        pts[0] = [pts[0][0] + ox, pts[0][1] + oy]
        if len(pts) > 2:
            pts[1] = [pts[1][0] + ox / 2, pts[1][1] + oy / 2]
    else:
        pts = [[x + ox, y + oy] for x, y in pts]
    col = tc["color"]
    w = (tc.get("width") or 10) * u
    glow = tc.get("glow", True)
    ops = []

    def poly(cts, c, a, blur=0.0):
        if a <= 0.004 or not cts:
            return
        ops.append({"k": "poly", "c": [[[r2(x), r2(y)] for x, y in ct] for ct in cts], "col": c, "a": r2(min(1.0, a)), "b": r2(blur)})

    if k in ("arrow", "curve"):
        S = _quad(pts if k == "curve" else pts[:2])
        C = _cum(S)
        ln = C[-1] * p
        if ln <= 0.5:
            return []
        full = w * HEAD_K
        hs = full * min(1.0, ln / (full * 1.5))
        tip, d = _point(S, C, ln)
        se = ln - hs * 0.72
        shaft = _part(S, C, 0.0, se) if se > 0.5 else None
        head = _head(tip, d, hs)
        if glow:
            if shaft:
                poly([_stroke(shaft, w / 2 * GLOW_W)], col, GLOW_A * A, w * GLOW_BLUR)
            poly([_grow(head, 1.35)], col, GLOW_A * A, w * GLOW_BLUR)
        if shaft:
            poly([_stroke(shaft, w / 2)], col, A)
        poly([head], col, A)
        if shaft:
            poly([_stroke(shaft, w / 2 * CORE_W)], "#FFFFFF", CORE_A * A)
        poly([_head(tip, d, hs, 0.42)], "#FFFFFF", CORE_A * A)
    elif k == "pass":
        S = _quad(pts)
        C = _cum(S)
        L = C[-1]
        ln = L * p
        if ln <= 0.5:
            return []
        end_r, start_r = w * 1.25, w * 0.8
        period = (DASH_ON + DASH_OFF) * w
        phase = (e / MARCH) % 1.0 * period
        lo, hi = start_r * 1.8, min(ln, L - end_r * 1.6)
        dashes = []
        j = -1
        while True:
            a = j * period + phase
            if a >= hi:
                break
            b = a + DASH_ON * w
            a2, b2 = max(a, lo), min(b, hi)
            if b2 - a2 > 0.5:
                dashes.append(_part(S, C, a2, b2))
            j += 1
        dots = [_orient(_circle(S[0][0], S[0][1], start_r))]
        er = end_r * _clamp((p - 0.85) / 0.15, 0.0, 1.0)
        if er > 0.5:
            dots.append(_orient(_circle(S[-1][0], S[-1][1], er)))
        if glow and hi > lo:   # 빛 번짐은 점선 칸마다가 아니라 길 하나로 (흐리면 같아 보이고 내보내기 ASS 가 몇 배 작음)
            poly([_stroke(_part(S, C, lo, hi), w / 2 * GLOW_W)] + [_grow(c, GLOW_W) for c in dots], col, GLOW_A * A, w * GLOW_BLUR)
        elif glow:
            poly([_grow(c, GLOW_W) for c in dots], col, GLOW_A * A, w * GLOW_BLUR)
        poly([_stroke(x, w / 2) for x in dashes] + dots, col, A)
    elif k == "ring":
        cx, cy = pts[0]
        rx = tc.get("size", 110) * u
        ry = rx * RING_RY
        h = w / 2
        a0 = math.pi / 2
        a1 = a0 + 2 * math.pi * p
        front, back = ((0.5 * math.pi, math.pi), (2 * math.pi, 2.5 * math.pi)), ((math.pi, 2 * math.pi),)
        poly([_ellipse(cx, cy, max(0.5, rx - h), max(0.5, ry - h))], col, RING_FILL * A * p)
        if glow:
            poly([_arc_band(cx, cy, rx, ry, h * GLOW_W, a, b) for a, b in _ivs(a0, a1, ((a0, a0 + 2 * math.pi),))], col, GLOW_A * A, w * GLOW_BLUR)
        poly([_arc_band(cx, cy, rx, ry, h, a, b) for a, b in _ivs(a0, a1, back)], col, RING_BACK * A)
        poly([_arc_band(cx, cy, rx, ry, h, a, b) for a, b in _ivs(a0, a1, front)], col, A)
        poly([_arc_band(cx, cy, rx, ry, h * 0.3, a, b) for a, b in _ivs(a0, a1, ((0.5 * math.pi, 0.92 * math.pi), (2.08 * math.pi, 2.5 * math.pi)))],
             "#FFFFFF", CORE_A * A)
    elif k == "spot":
        cx, cy = pts[0]
        r = tc.get("size", 210) * u
        rr = r * (1 + 0.6 * (1 - p))
        fe = rr * SPOT_FEATHER
        pad = fe * 4 + 2
        rect = _orient([[-pad, -pad], [W + pad, -pad], [W + pad, H + pad], [-pad, H + pad]])
        poly([rect, _ellipse(cx, cy, rr, rr, hole=True)], "#000000", tc.get("dim", 0.6) * A * p, fe)
        if glow:
            poly([_arc_band(cx, cy, rr, rr, 5 * u, 0.0, 2 * math.pi)], col, 0.5 * A * p, 6 * u)
        poly([_arc_band(cx, cy, rr, rr, 2 * u, 0.0, 2 * math.pi)], col, 0.85 * A * p)
    elif k == "label":
        tx, ty = pts[0]
        text = str(tc.get("text") or "")
        if not text:
            return []
        fs = tc.get("size", 40) * u
        s = 0.7 + 0.3 * p
        bw, bh, ph = em_width(text) * fs + fs * 1.1, fs * 1.45, fs * 0.45
        m = 10 * u
        cx = W / 2 if bw > W - 2 * m else _clamp(tx, bw / 2 + m, W - bw / 2 - m)
        y1 = ty - ph
        y0 = y1 - bh
        rad = bh * 0.3
        box = []
        for qx, qy, a in ((cx + bw / 2 - rad, y0 + rad, -0.5), (cx + bw / 2 - rad, y1 - rad, 0.0), (cx - bw / 2 + rad, y1 - rad, 0.5), (cx - bw / 2 + rad, y0 + rad, 1.0)):
            for j in range(7):
                ang = math.pi * (a + 0.5 * j / 6)
                box.append([qx + rad * math.cos(ang), qy + rad * math.sin(ang)])
        hw = fs * 0.38
        px = _clamp(tx, cx - bw / 2 + rad + hw, cx + bw / 2 - rad - hw)
        tri = [[tx, ty], [px + hw, y1 - u], [px - hw, y1 - u]]

        def sc(ct):
            return [[tx + (x - tx) * s, ty + (y - ty) * s] for x, y in ct]
        poly([_orient(sc(box)), _orient(sc(tri))], col, A * p)
        if A * p > 0.004:
            ops.append({"k": "text", "x": r2(tx + (cx - tx) * s), "y": r2(ty + ((y0 + y1) / 2 - ty) * s), "s": r2(fs * s), "t": text,
                        "col": "#111111" if _luma(col) > 0.55 else "#FFFFFF", "a": r2(min(1.0, A * p))})
    return ops


# ---------- 내보내기 (ASS 그림) ----------

def _ass_col(hexc):
    h = hexc.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _fmt(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _ass_text(s):
    s = str(s).replace("\\", "\\\u2060")
    return s.replace("{", "\\{").replace("}", "\\}").replace("\r", "").replace("\n", " ")


FONT_K = 1.194   # editor.FONT_K 와 같은 값 (CSS px → libass 글자 크기)
TEXT_DY = 0.0    # 글자 세로 자리 보정 (글자 크기 배 · 미리보기 캔버스 textBaseline=middle 과 맞춤)


def op_ass(op):
    """그릴 것 하나 → ASS 대사 본문."""
    al = f"&H{int(round((1 - op['a']) * 255)):02X}&"
    if op["k"] == "text":
        return (rf"{{\an5\pos({_fmt(op['x'])},{_fmt(r2(op['y'] + op['s'] * TEXT_DY))})\fs{round(op['s'] * FONT_K)}\bord0\shad0"
                rf"\1c{_ass_col(op['col'])}\1a{al}}}{_ass_text(op['t'])}")
    q = lambda v: str(int(math.floor(v * 2 + 0.5)))  # noqa: E731 — \p2: 좌표 ×2 정수 (0.25px 안 · 소수점 글자보다 ASS 가 절반쯤)
    draw = " ".join("m " + q(ct[0][0]) + " " + q(ct[0][1]) + " l " + " ".join(q(x) + " " + q(y) for x, y in ct[1:]) for ct in op["c"])
    bl = rf"\blur{_fmt(op['b'] * ASS_BLUR)}" if op["b"] > 0 else ""
    return rf"{{\an7\pos(0,0)\p2\bord0\shad0\1c{_ass_col(op['col'])}\1a{al}{bl}}}{draw}{{\p0}}"


def ordered(tactics):
    """정리한 전술 그림들을 그리는 차례로 — 스포트라이트(화면 어둡게)는 늘 다른 그림 아래 (editor.html tacOrder 와 같게)."""
    ts = [x for x in (normalize(t) for t in tactics or []) if x is not None]
    return [t for t in ts if t["kind"] == "spot"] + [t for t in ts if t["kind"] != "spot"]


def ass_lines(tactics, W, H, fps, total, ass_time, layer=0):
    """전술 그림들 → ASS 대사 줄 (스타일 이름 Board — 타이틀 스타일 T<id> 와 안 겹치게). 프레임 i 의 모양은 [i-½, i+½) 프레임 동안 (반올림해도 그 프레임에 걸리게) ·
    같은 모양이 이어지는 프레임은 한 줄로 묶음."""
    out = []
    fps = float(fps or 30)
    for tc in ordered(tactics):
        end = min(tc["start"] + tc["dur"], total)
        i = int(math.ceil(tc["start"] * fps - 1e-6))
        runs = []
        while i / fps < end - 1e-9:
            body = [op_ass(o) for o in ops_at(tc, i / fps, W, H)]
            if runs and runs[-1][2] == body:
                runs[-1][1] = i
            else:
                runs.append([i, i, body])
            i += 1
        for a, b, body in runs:
            s, t2 = max(0.0, (a - 0.5) / fps), (b + 0.5) / fps
            for x in body:
                out.append(f"Dialogue: {layer},{ass_time(s)},{ass_time(t2)},Board,,0,0,0,,{x}")
    return out


STYLE_LINE = "Style: Board,Pretendard Black,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"


def end_of(seq):
    """편집본 전술 그림이 끝나는 가장 늦은 시각."""
    return max([float(t.get("start") or 0) + float(t.get("dur") or 0) for t in seq.get("tactics") or [] if isinstance(t, dict)] or [0.0])


# ---------- 원본 자리 ↔ 화면 자리 (editor.html fitRect·motionAt 과 같은 계산) ----------

def media_rect(it, md, seq, W, H):
    """클립 미디어를 화면에 놓는 상자 {bx, by, bw, bh, mx, my, mw, mh} (효과 컨트롤 100% 기준) — editor.html fitRect 와 같은 계산."""
    sw, sh = float(md.get("w") or W), float(md.get("h") or H)
    lay = {"mode": "fill", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0, **(seq.get("layout") or {})}
    rf = float(it.get("reframe", 0.5) if it.get("reframe") is not None else 0.5)
    fit = it.get("fit") or "auto"
    if seq.get("format") == "shorts" and it.get("track") == "V1" and fit == "auto":
        ct, cb = float(lay.get("cropTop") or 0), float(lay.get("cropBottom") or 0)
        keep = max(0.2, 1 - ct - cb)
        vh = sh * keep
        if lay.get("mode") not in ("box", "blur"):
            s = max(W / sw, H / vh)
            scw, sch = max(W, sw * s), max(H, vh * s)
            return {"bx": 0.0, "by": 0.0, "bw": W, "bh": H, "mw": sw * s, "mh": sh * s, "mx": -(scw - W) * rf, "my": -(sch - H) / 2 - ct * sh * s}
        zw = W * max(1.0, float(lay.get("zoom") or 1))
        s = zw / sw
        zh = vh * s
        ch = min(zh, H)
        return {"bx": 0.0, "by": (H - ch) * float(lay.get("vpos", 0.5)), "bw": W, "bh": ch, "mw": zw, "mh": sh * s, "mx": -(zw - W) * rf, "my": -(zh - ch) / 2 - ct * sh * s}
    if fit == "auto":
        fit = "native" if md.get("kind") == "image" and not md.get("freeze") and sw <= W and sh <= H else "contain"
    s = 1.0 if fit == "native" else max(W / sw, H / sh) if fit == "cover" else min(W / sw, H / sh)
    scw, sch = sw * s, sh * s
    if fit == "cover":
        return {"bx": 0.0, "by": 0.0, "bw": W, "bh": H, "mw": scw, "mh": sch, "mx": -(scw - W) * rf, "my": -(sch - H) / 2}
    return {"bx": (W - scw) / 2, "by": (H - sch) / 2, "bw": scw, "bh": sch, "mw": scw, "mh": sch, "mx": 0.0, "my": 0.0}


def _motion(it, tl):
    import editor
    m = editor.i_mt(it, tl)
    pos = editor.kf_at(editor.param(it, "pos"), m)
    return pos, float(editor.kf_at(editor.param(it, "scale"), m)) / 100, float(editor.kf_at(editor.param(it, "rot"), m)), editor.param(it, "anchor")["v"]


def src_to_frame(it, md, seq, W, H, u, v, tl):
    """원본 (u, v)(0~1) → 타임라인 tl 초의 화면 px (확대·위치·회전 효과까지)."""
    f = media_rect(it, md, seq, W, H)
    pos, sc, rot, anc = _motion(it, tl)
    px, py = f["bx"] + f["mx"] + u * f["mw"], f["by"] + f["my"] + v * f["mh"]
    ox, oy = f["bx"] + anc[0] * f["bw"], f["by"] + anc[1] * f["bh"]
    a = math.radians(rot)
    dx, dy = (px - ox) * sc, (py - oy) * sc
    return ox + (pos[0] - 0.5) * W + dx * math.cos(a) - dy * math.sin(a), oy + (pos[1] - 0.5) * H + dx * math.sin(a) + dy * math.cos(a)


def frame_to_src(it, md, seq, W, H, x, y, tl):
    """화면 px → 원본 (u, v)(0~1) — src_to_frame 의 거꾸로."""
    f = media_rect(it, md, seq, W, H)
    pos, sc, rot, anc = _motion(it, tl)
    ox, oy = f["bx"] + anc[0] * f["bw"], f["by"] + anc[1] * f["bh"]
    a = math.radians(rot)
    dx, dy = x - ox - (pos[0] - 0.5) * W, y - oy - (pos[1] - 0.5) * H
    rx, ry = (dx * math.cos(a) + dy * math.sin(a)) / (sc or 1), (-dx * math.sin(a) + dy * math.cos(a)) / (sc or 1)
    return (ox + rx - f["bx"] - f["mx"]) / (f["mw"] or 1), (oy + ry - f["by"] - f["my"]) / (f["mh"] or 1)


ANCHOR = {"ring": 1.0, "arrow": 1.0, "curve": 1.0, "pass": 1.0, "spot": 0.5, "label": 0.0}   # 선수 상자의 어느 높이를 따라갈지 (1 발 · 0.5 몸 가운데 · 0 머리 위)


def box_anchor(kind, b):
    """선수 상자 [x, y, w, h](원본 0~1) → 그 그림이 붙는 점 (발밑 원은 발 · 이름표는 머리 위)."""
    return b[0] + b[2] / 2, b[1] + b[3] * ANCHOR.get(kind, 1.0)


def follow_offsets(points, t_first):
    """[(타임라인 시각, 화면 x, 화면 y)](0~1) → 따라가기 기록 [[처음부터 초, dx, dy]] (첫 점에서 얼마나 옮겼는지)."""
    if not points:
        return None
    x0, y0 = points[0][1], points[0][2]
    return [[round(t - t_first, 3), round(x - x0, 4), round(y - y0, 4)] for t, x, y in points]


# ---------- 선수 따라가기 ----------

_TRACK_LOCK = threading.Lock()


class Busy(RuntimeError):
    pass


def _frames(path, t0, t1, fps, width=640):
    """원본 t0~t1 초 장면들 (PIL 그림, 시각) — ffmpeg 한 번으로 줄여서 받음."""
    from PIL import Image
    import editor
    md = editor.probe(path)
    sw, sh = int(md.get("width") or 1920), int(md.get("height") or 1080)
    w = min(width, sw)
    w -= w % 2
    h = int(round(sh * w / sw / 2)) * 2
    cmd = [core.ffmpeg(), "-v", "error", "-ss", f"{t0:.3f}", "-i", str(path), "-t", f"{max(0.05, t1 - t0):.3f}",
           "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = core.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = w * h * 3
    out = []
    try:
        k = 0
        while True:
            buf = p.stdout.read(n)
            if not buf or len(buf) < n:
                break
            out.append((t0 + k / fps, Image.frombytes("RGB", (w, h), buf)))
            k += 1
    finally:
        p.stdout.close()
        p.wait()
    return out


def _iou(a, b):
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0.0, min(ax1, bx1) - max(a[0], b[0])), max(0.0, min(ay1, by1) - max(a[1], b[1]))
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-9)


PICK_NEAR = 0.12    # 처음 고를 때: 누른 곳이 선수 상자에서 이만큼(화면 비율) 안이어야
GATE_H = 0.15       # 한 장면(1/8초) 사이 머리 자리가 선수 키의 이만큼 넘게 튀면 다른 사람 (놓친 장면마다 25%씩 넓혀 다시 찾음)
GATE_MIN = 0.04
H_RANGE = (0.5, 1.4)  # 앞 상자 키 대비 이 범위 밖이면 다른 사람 (0.5 까지는 앞 사람에 다리가 가린 것으로 봄)
HIDDEN = 0.85       # 키가 평소의 이 비율보다 작으면 다리가 가린 것 → 머리는 그대로 두고 키를 평소대로 (발밑 원이 허리에 뜨지 않게)
SMOOTH = 2          # 앞뒤 이만큼의 장면과 평균 (덜덜 떨림 없애기)
MAX_MISS = 4        # 이만큼(0.5초) 이어서 놓치면 그 뒤로는 찾지 않음
TAIL_LOST = 3       # 끝에서 이만큼 넘게 이어서 놓치면 (0.4초 · 장면 바뀜) 거기서 따라가기도 그림도 끝냄


def pick_start(persons, x, y, kind=None):
    """누른 점(원본 0~1)에 해당하는 선수 상자 (멀면 None).
    kind 가 있으면 그 그림이 붙는 점(발밑 원 = 발 · 이름표 = 머리 위 · 스포트라이트 = 몸 가운데)이 가장 가까운 선수 — 머리 위 점은
    상자 경계라 '상자 안' 으로 고르면 옆 사람이 잡힘. 없으면 상자 안이면 그 중 가장 작은 것, 아니면 몸에서 가장 가까운 것."""
    best, bd = None, PICK_NEAR
    for b in persons:
        if kind is not None:
            ax, ay = box_anchor(kind, b)
            d = math.hypot(ax - x, ay - y)
        else:
            d = math.hypot(max(b[0] - x, 0.0, x - (b[0] + b[2])), max(b[1] - y, 0.0, y - (b[1] + b[3] * 1.15)))
            d = d if d > 0 else -1.0 / max(b[2] * b[3], 1e-6)   # 상자 안: 작은(앞에 선) 사람 먼저
        if d < bd:
            best, bd = b[:4], d
    return best


def _follow_one(seq_persons, first):
    """first 상자에서 시작해 장면마다 같은 선수 상자를 이어 고름 (머리 자리로 이음 — 축구 장면은 앞 선수에 다리·몸이 가려도 머리는 보임) →
    [상자 또는 None]. 다리가 가려 키가 줄면 머리는 그대로 두고 평소 키로 고침."""
    out, prev, href, miss = [first], first, first[3], 0
    for _, ps in seq_persons[1:]:
        px, py = prev[0] + prev[2] / 2, prev[1]
        gate = max(GATE_MIN, GATE_H * href) * (1 + 0.25 * miss)
        best, bs = None, -1e9
        for b in ps or []:
            r = b[3] / href
            d = math.hypot(b[0] + b[2] / 2 - px, b[1] - py)
            if d > gate or not (H_RANGE[0] <= r <= H_RANGE[1]):
                continue
            c = [b[0], b[1], b[2], href if r < HIDDEN else b[3]]   # 다리가 가린 상자는 평소 키로 고쳐서 견줌 (작다고 밀리지 않게)
            sc = _iou(prev, c) - d / href - 0.5 * abs(math.log(c[3] / href))
            if sc > bs:
                best, bs = c, sc
        if best is None or miss >= MAX_MISS:   # 오래 놓친 뒤에 잡힌 상자는 다른 사람일 수 있음 → 거기서 따라가기 끝
            out.append(None)
            miss += 1
            continue
        if best[3] != href:
            href = 0.8 * href + 0.2 * best[3]
        out.append(best)
        prev, miss = best, 0
    return out


def follow_boxes(seq_persons, x, y, kind=None, start=0):
    """장면마다의 사람 상자들 [(시각, [[x,y,w,h,p]...])] + start 번째 장면에서 누른 점 → [(시각, 상자 또는 None)].
    start 장면에서 고른 선수를 앞뒤로 이어 따라감 (MSG 는 공 차는 순간에서 고름) · 못 찾으면 None (놓침)."""
    first = pick_start(seq_persons[start][1] or [], x, y, kind)
    if first is None:
        raise LookupError("그 자리에서 선수를 찾지 못했어요. 원·이름표를 선수 발밑(머리 위)에 놓고 다시 눌러 주세요")
    fwd = _follow_one(seq_persons[start:], list(first))
    back = _follow_one(seq_persons[start::-1], list(first))[1:][::-1]
    return [(t, b) for (t, _), b in zip(seq_persons, back + fwd)]


def smooth(track):
    """놓친 장면은 앞뒤 상자로 메우고(처음·끝은 가까운 값) 앞뒤 SMOOTH 장면과 평균 → [(시각, [x,y,w,h])]."""
    known = [(t, b) for t, b in track if b is not None]
    if not known:
        return []
    filled = []
    for t, b in track:
        if b is None:
            a = max((k for k in known if k[0] <= t), key=lambda k: k[0], default=None)
            c = min((k for k in known if k[0] >= t), key=lambda k: k[0], default=None)
            if a and c and c[0] > a[0]:
                x = (t - a[0]) / (c[0] - a[0])
                b = [a[1][i] + (c[1][i] - a[1][i]) * x for i in range(4)]
            else:
                b = list((a or c)[1])
        filled.append((t, list(b)))
    out = []
    for i, (t, _) in enumerate(filled):
        r = min(SMOOTH, i, len(filled) - 1 - i)   # 처음·끝은 창을 좁혀 (한쪽 장면만 섞이면 첫 자리가 밀림)
        win = filled[i - r: i + r + 1]
        out.append((round(t, 3), [round(sum(b[j] for _, b in win) / len(win), 4) for j in range(4)]))
    return out


def track(path, t0, t1, x, y, detect_fn=None, fps=TRACK_FPS, log=print, kind=None):
    """원본 path 의 t0~t1 초 동안 (x, y)(원본 0~1 · kind 그림이 붙는 점)에 있는 선수를 따라감 → {"boxes": [[시각, x, y, w, h], ...], "lost": 놓친 장면 수}.
    detect_fn(PIL 그림) → {"persons": [...]} | None (시험용 · 기본은 detect.people)."""
    if not _TRACK_LOCK.acquire(blocking=False):
        raise Busy("다른 선수 따라가기가 끝난 뒤에 다시 눌러 주세요")
    try:
        t0, t1 = float(t0), float(t1)
        if not (t1 > t0):
            raise ValueError("따라갈 구간이 비어 있어요")
        t1 = min(t1, t0 + LIMIT_FOLLOW)
        if detect_fn is None:
            import detect
            if not detect.ensure(label="선수 따라가기"):
                raise RuntimeError("선수 찾기 모델을 쓰지 못했어요 · 인터넷 연결을 확인하고 잠시 뒤 다시 눌러 주세요 (처음 한 번 약 4MB 내려받아요)")
            detect_fn = detect.people
        frames = _frames(path, t0, t1, fps)
        if not frames:
            raise RuntimeError("영상에서 장면을 읽지 못했어요")
        seq = []
        for t, im in frames:
            r = detect_fn(im)
            seq.append((t, (r or {}).get("persons") or []))
        raw = follow_boxes(seq, float(x), float(y), kind if kind in KINDS else None)
        lost = sum(1 for _, b in raw if b is None)
        last = max(i for i, (_, b) in enumerate(raw) if b is not None)
        log(f"  선수 따라가기 · {len(raw)}장면 중 {len(raw) - lost}장면에서 찾음")
        # 끝에서 선수를 계속 놓쳤으면(장면이 바뀜·화면 밖) 마지막으로 본 곳까지만 — 화면은 그림도 거기서 끝냄 (빈 곳에 원이 떠 있지 않게)
        tail = len(raw) - 1 - last >= TAIL_LOST
        return {"boxes": [[t] + b for t, b in smooth(raw)[: last + 1 if tail else len(raw)]], "lost": lost, "n": len(raw),
                "lostTail": tail, "seenUntil": round(raw[last][0], 3)}
    finally:
        _TRACK_LOCK.release()
