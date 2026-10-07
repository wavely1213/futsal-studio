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
STICKERS = core.APP_DIR / "stickers"  # 썸네일 스티커 그림 (Fluent Emoji 3D · MIT, stickers/NOTICE.md)
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


def _gray_crop(rgb, box, h=128):
    """사람 상자(0~1)를 원본 화질로 잘라 높이 h 흑백 (선명도·흔들림 재기용)."""
    import numpy as np
    from PIL import Image
    W, H = rgb.size
    x, y, w, hh = box[:4]
    c = rgb.crop((int(x * W), int(y * H), max(int(x * W) + 2, int((x + w) * W)), max(int(y * H) + 2, int((y + hh) * H)))).convert("L")
    k = h / c.size[1]
    return np.asarray(c.resize((max(8, int(c.size[0] * k)), h), Image.BILINEAR), np.float32)


def _blur_of(g):
    """흔들림 0(또렷)~1(뭉개짐): 선명도(라플라시안 분산)가 낮을수록, 한 방향 그래디언트만 남을수록(모션 블러) 큼."""
    import numpy as np
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    soft = 1 - np.clip((np.log1p(lap.var()) - np.log1p(25)) / (np.log1p(500) - np.log1p(25)), 0, 1)
    ex, ey = float((np.diff(g, axis=1) ** 2).mean()), float((np.diff(g, axis=0) ** 2).mean())
    aniso = max(ex, ey) / max(1e-6, min(ex, ey))
    return float(np.clip(0.65 * soft + 0.35 * np.clip((aniso - 1.8) / 2.2, 0, 1), 0, 1))


def dhash(img):
    """64비트 장면 지문 (9×8 흑백에서 옆 칸보다 밝은지) → 16자리 16진수."""
    import numpy as np
    from PIL import Image
    a = np.asarray(img.convert("L").resize((9, 8), Image.BILINEAR), np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return "%016x" % int("".join("1" if b else "0" for b in bits), 2)


def hamming(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


MAX_SHIFT = 40     # 자동 보정: 레벨로 한 채널을 많아야 이만큼만 늘림 (클리핑·색 틀어짐 막기)
GRADE_MEAN = 0.48  # 보정 뒤 평균 밝기 목표
GAMMA_RANGE = (0.8, 2.2)  # 어두운 실내·저녁 경기장도 평균 밝기 0.38 이상으로 (1.25·1.8 로는 모자랐음 — 판정 B6 밤 장면 0.31)


def auto_grade(src, close=False):
    """장면 한 장의 자동 보정 숫자 (화면 thumb.html 의 applyGrade 와 짝: 레벨 lo/hi·색온도 → 감마(밝기에만) → 자연 채도 → 클래리티 → 샤픈).
    - 레벨: 채널별 0.5%/99.5% 지점 (밝기 기준에서 ±12 안쪽으로 — 잔디 초록에 끌려 색이 틀어지지 않게, 이동량 ≤ MAX_SHIFT)
    - 감마: 보정 뒤 평균 밝기가 0.48 이 되게 (0.8~1.8) · 자연 채도: 보정 뒤 채도가 약 1.2배 되게 · 색온도: 회색 부분이 푸르면(형광등) 따뜻하게
    - 클래리티 25 (얼굴 클로즈업이면 15) · 샤픈 18"""
    import numpy as np
    from PIL import Image
    im = src if isinstance(src, Image.Image) else Image.open(src)
    im = im.convert("RGB")
    W, H = im.size
    a = np.asarray(im.resize((320, max(8, int(320 * H / W)))), np.float32)
    lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
    llo, lhi = np.percentile(lum, [0.5, 99.5])
    lo, hi = [], []
    for ch in range(3):
        c_lo, c_hi = np.percentile(a[..., ch], [0.5, 99.5])
        lo.append(int(round(np.clip(np.clip(c_lo, llo - 12, llo + 12), 0, MAX_SHIFT))))
        hi.append(int(round(np.clip(np.clip(c_hi, lhi - 12, lhi + 12), 255 - MAX_SHIFT, 255))))
    lv = np.clip((a - np.array(lo, np.float32)) / np.maximum(1, np.array(hi, np.float32) - np.array(lo, np.float32)), 0, 1) @ np.array([0.299, 0.587, 0.114], np.float32)
    g0, g1 = GAMMA_RANGE  # 평균 밝기는 감마에 따라 늘기만 함 → 반으로 나눠 찾기
    if (lv ** (1 / g1)).mean() < GRADE_MEAN:
        gamma = g1
    elif (lv ** (1 / g0)).mean() > GRADE_MEAN:
        gamma = g0
    else:
        for _ in range(18):
            gm = (g0 + g1) / 2
            g0, g1 = (gm, g1) if (lv ** (1 / gm)).mean() < GRADE_MEAN else (g0, gm)
        gamma = (g0 + g1) / 2
    mx, mn = a.max(2), a.min(2)
    sat = (mx - mn) / np.maximum(mx, 1)
    neutral = (sat < 0.18) & (lum > 64) & (lum < 217)
    temp = 0
    if neutral.mean() > 0.02:
        d = float((a[..., 0] - a[..., 2])[neutral].mean())
        if d < -4:
            temp = int(round(min(30, -d * 1.5)))     # 푸른 형광등 → 따뜻하게
        elif d > 14:
            temp = -int(round(min(20, d - 14)))      # 너무 누런 조명 → 조금 차갑게
    g = {"on": True, "amt": 1, "lo": lo, "hi": hi, "gamma": round(float(gamma), 3), "vib": 0, "clarity": 15 if close else 25, "temp": temp, "sharpen": 18}  # 판정: '과하게 보정돼 기계로 만든 느낌' → 클래리티·샤픈을 낮춤
    # 자연 채도: 레벨만으로도 채도가 오르므로, 보정 뒤 채도가 원본의 약 1.2배가 되는 값을 고름 (화면 applyGrade 와 같은 계산으로 어림)
    s0 = float(sat.mean())
    best = None
    for v in range(-40, 85, 5):
        r = _sat_after(a, dict(g, vib=v)) / max(1e-6, s0)
        if best is None or abs(r - SAT_GAIN) < abs(best[1] - SAT_GAIN):
            best = (v, r)
    g["vib"] = best[0]
    return g


SAT_GAIN = 1.24  # 자동 보정 뒤 채도 목표 (원본 대비 · 기준표 +5~35% · 판정 '무보정 캡처 같다' 뒤 1.2 → 1.24, 화면 보정의 선명하게가 조금 더 올림)


def _sat_after(a, g):
    """applyGrade(thumb.html)의 레벨·색온도·감마(밝기에만)·자연 채도까지를 numpy 로 어림 → 평균 채도 (자연 채도 값 고르기용)."""
    import numpy as np
    lo, hi = np.array(g["lo"], np.float32), np.array(g["hi"], np.float32)
    x = np.clip((a - lo) / np.maximum(1, hi - lo), 0, 1) * 255 * (1 + np.array([1, 0, -1], np.float32) * g["temp"] / 260)
    y = x @ np.array([0.299, 0.587, 0.114], np.float32)
    x = np.clip(x * ((np.clip(y, 0, 255) / 255) ** (1 / g["gamma"]) * 255 / np.maximum(0.5, y))[..., None], 0, 255)  # 감마는 밝기에만
    if g["vib"]:
        mx, mn = x.max(2, keepdims=True), x.min(2, keepdims=True)
        st = np.clip((mx - mn) / np.maximum(1, mx), 0, 1)
        k = g["vib"] / 100 * (1 - st)
        r_, gg, b_ = x[..., 0:1], x[..., 1:2], x[..., 2:3]
        hr = (gg - b_) / np.maximum(1e-6, r_ - b_)
        skin = (r_ == mx) & (gg >= b_) & (hr > 0.3) & (hr < 0.85)
        k = np.where(skin, k * 0.5, k)
        y = x @ np.array([0.299, 0.587, 0.114], np.float32)
        x = np.clip(y[..., None] + (x - y[..., None]) * (1 + k), 0, 255)
    mx, mn = x.max(2), x.min(2)
    return float(((mx - mn) / np.maximum(mx, 1)).mean())


def _band(path):
    """영상에 박힌 자막·방송 띠: ('bottom'|'top'|None, 띠가 시작(끝)하는 높이 0~1).
    글자 줄 = 세로 경계가 촘촘한(15% 넘는) 줄이 2~12줄 이어지고 그 위아래는 한산함. 띠 경계선(가로 경계)이 가까이 있거나 아주 한산해야 인정
    (철망·관중석처럼 넓게 촘촘한 무늬는 글자로 보지 않음)."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        g = np.asarray(im.convert("L").resize((320, 180)), np.float32)
    dens = (np.abs(np.diff(g, axis=1)) > 60).mean(1)
    edge = np.abs(np.diff(g, axis=0)).mean(1)

    def runs(lo, hi):
        y = lo
        while y < hi:
            if dens[y] > 0.15:
                z = y
                while z + 1 < hi and dens[z + 1] > 0.15:
                    z += 1
                yield y, z
                y = z + 1
            y += 1
    for y0, y1 in runs(126, 178):  # 아래쪽
        n = y1 - y0 + 1
        around = max(float(dens[max(0, y0 - 3):y0].mean()), float(dens[y1 + 1:y1 + 4].mean()) if y1 + 1 < 179 else 0.0)
        cand = list(range(max(100, y0 - 14), y0))
        sep = max(cand, key=lambda y: edge[y]) if cand and edge[cand].max() > 40 else None  # 띠 경계 = 가장 뚜렷한 가로 경계
        if 2 <= n <= 12 and around < 0.07 and (sep is not None or around < 0.04):
            return "bottom", round((sep if sep is not None else y0 - 4) / 180, 3)
    for y0, y1 in runs(1, 54):  # 위쪽
        n = y1 - y0 + 1
        around = max(float(dens[max(0, y0 - 3):y0].mean()) if y0 > 0 else 0.0, float(dens[y1 + 1:y1 + 4].mean()))
        cand = list(range(y1 + 1, min(80, y1 + 14)))
        sep = max(cand, key=lambda y: edge[y]) if cand and edge[cand].max() > 40 else None
        if 2 <= n <= 12 and around < 0.07 and (sep is not None or around < 0.04):
            return "top", round(((sep if sep is not None else y1 + 4) + 1) / 180, 3)
    return None, None


def blur_penalty(blur):
    """흔들린 장면 감점: 0.35 까지는 그대로, 0.6 을 넘으면 절반."""
    return 0.5 if blur > 0.6 else 1 - 0.3 * max(0.0, blur - 0.35)


def action_score(persons, ball):
    """선수·공으로 본 '액션 장면' 배율 (약 0.85~1.9): 선수 2~6명 · 화면에 알맞은 크기(높이 15~60%) · 공이 보이고 선수 발 가까이 · 몸싸움(겹침)."""
    if persons is None:
        return 1.0  # 선수 찾기 모델이 없으면 예전 점수 그대로
    n = len(persons)
    if not n:
        return 0.85
    k = {1: 1.0, 2: 1.12, 3: 1.18, 4: 1.2, 5: 1.18, 6: 1.15}.get(n, 1.05)
    hmax = max(p[3] for p in persons)
    k *= 1.15 if 0.15 <= hmax <= 0.6 else 1.05 if hmax > 0.6 else 0.9
    if ball:
        k *= 1.25
        bx, by = ball[0] + ball[2] / 2, ball[1] + ball[3] / 2
        d = min(((p[0] + p[2] / 2 - bx) ** 2 + (p[1] + p[3] - by) ** 2) ** 0.5 for p in persons)
        k *= 1 + 0.15 * max(0.0, 1 - d / 0.15)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = persons[i], persons[j]
            ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            if ix > 0 and abs((a[1] + a[3]) - (b[1] + b[3])) < max(a[3], b[3]) * 0.35:
                return round(k * 1.08, 4)  # 경합 장면
    return round(k, 4)


def main_person(persons, ball):
    """주인공: 화면을 크게 차지한 사람(높이 60% 이상 · 인터뷰·클로즈업)이 있으면 그 사람, 아니면 공과 가장 가까운 선수(발 기준),
    공이 없으면 가장 큰 선수 → 번호 또는 -1."""
    if not persons:
        return -1
    big = max(range(len(persons)), key=lambda i: persons[i][3])
    if persons[big][3] >= 0.6:
        return big
    if ball:
        bx, by = ball[0] + ball[2] / 2, ball[1] + ball[3] / 2
        return min(range(len(persons)), key=lambda i: (persons[i][0] + persons[i][2] / 2 - bx) ** 2 + (persons[i][1] + persons[i][3] - by) ** 2)
    return max(range(len(persons)), key=lambda i: persons[i][3])


HEADLESS = 0.7   # 주인공 머리가 화면 위로 잘렸는데 얼굴도 안 보이는 장면(다리·몸통만) 배율


def headless(persons, ball, faces):
    """주인공이 위로 잘려 다리·몸통만 보이는지 (얼굴을 찾았으면 아님)."""
    if faces or not persons:
        return False
    m = persons[main_person(persons, ball)]
    return m[1] < 0.01 and m[3] < 0.97


SIT_AR = 0.45     # 사람 상자 가로/세로(픽셀 기준)가 이 이상이고 화면 높이 70% 안쪽이면 앉았거나 웅크린 자세로 봄
BENCH_K = 0.5     # 벤치·관중석(앉은 사람 둘 이상이 한 줄, 공은 멀리) 배율 — 판정 '레슨과 상관없는 장면'
BACK_K = 0.6      # 크게 나온 주인공인데 그 사람 얼굴이 안 보임(뒷모습·뒤통수) 배율
EDGE_K = 0.75     # 주인공이 화면 왼쪽·오른쪽 끝에 걸려 몸이 잘림
CROWD_K = 0.7     # 상반신 여럿이 나란히(관중석·벤치 뒤) · 공 없음
SMALL_K = 0.85    # 주인공이 아주 작음(화면 높이 18% 아래) — 목록 크기에서 누가 주인공인지 안 보임
LESSON_K = 1.3    # 레슨 액션: 공이 주인공 발 가까이 · 주인공 크기 알맞음 (레퍼런스는 거의 다 '공을 다루는 순간')


def _feet_d(p, ball, ar):
    """사람 발(상자 아래 가운데)과 공 가운데 거리 (화면 높이 기준 — 가로·세로 영상 모두 같은 눈금)."""
    bx, by = ball[0] + ball[2] / 2, ball[1] + ball[3] / 2
    return (((p[0] + p[2] / 2 - bx) * ar) ** 2 + (p[1] + p[3] - by) ** 2) ** 0.5


def scene_flags(persons, ball, faces, ar=16 / 9):
    """썸네일감인지 보는 장면 표시 (선수 찾기 결과로):
    bench 앉은 사람이 한 줄로 둘 이상이고 공은 그 발 근처에 없음 · crowd 아래가 잘린 상반신 셋 이상·공 없음 · back 크게 나온(높이 55% 이상) 주인공의 얼굴이 안 보임(얼굴 모델이 돌았을 때만)
    · edge 주인공이 화면 좌우 끝에 걸림 · small 주인공 높이 18% 미만 · lesson 공이 주인공 발 가까이(화면 높이 18% 안)이고 주인공 높이 22~80%."""
    out = {"bench": False, "back": False, "edge": False, "small": False, "lesson": False, "crowd": False}
    if not persons:
        return out
    upper = [p for p in persons if p[1] + p[3] > 0.97 and p[3] >= 0.5]  # 아래가 잘린 상반신 셋 이상 + 공 없음 = 관중·벤치 뒤 (레슨 장면 아님)
    out["crowd"] = len(upper) >= 3 and not ball
    sit = [p for p in persons if p[2] * ar / max(1e-6, p[3]) >= SIT_AR and p[3] < 0.86]
    for i, a in enumerate(sit):  # 나란히 붙어 앉은 사람 (발 높이·키가 같고 옆 사람과 거의 붙음) — 공 다투는 선수는 공이 발 근처라 뺌
        row = [b for b in sit if b is a or (max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2]) < 0.5 * min(a[2], b[2])
                                             and abs((a[1] + a[3]) - (b[1] + b[3])) < 0.05 and 0.75 < b[3] / max(1e-6, a[3]) < 1.33)]
        cut = a[1] + a[3] > 0.97  # 아래가 화면에 잘린 상반신 줄 (관중석·벤치 뒤) 은 셋 이상일 때만 (상반신 둘은 인터뷰일 수 있음)
        if len(row) >= (3 if cut else 2) and not (ball and min(_feet_d(b, ball, ar) for b in row) < 0.12):
            out["bench"] = True
            break
    mi = main_person(persons, ball)
    m = persons[mi]
    if faces is not None and m[3] >= 0.55 and m[1] > 0.02:  # 머리가 화면 안에 있는데 그 얼굴이 없음 (다리만 나온 장면은 headless 가 따로 봄)
        cx = m[0] + m[2] / 2  # 그 사람 얼굴 = 상자 가운데 쪽(±28%) 위 42% 안 (옆에 걸친 다른 사람 얼굴은 아님)
        top = (cx - m[2] * 0.28, m[1] - 0.02, cx + m[2] * 0.28, m[1] + m[3] * 0.42)
        out["back"] = not any(top[0] <= f["box"][0] + f["box"][2] / 2 <= top[2] and top[1] <= f["box"][1] + f["box"][3] / 2 <= top[3] for f in faces)
    out["edge"] = (m[0] < 0.006 or m[0] + m[2] > 0.994) and m[3] < 0.95 and m[2] * ar / max(1e-6, m[3]) < 0.32
    out["small"] = m[3] < 0.18
    out["lesson"] = bool(ball) and 0.22 <= m[3] <= 0.8 and _feet_d(m, ball, ar) < 0.18
    return out


def flags_mult(fl):
    """장면 표시 → 점수 배율."""
    k = 1.0
    if fl.get("bench"):
        k *= BENCH_K
    if fl.get("back"):
        k *= BACK_K if not fl.get("lesson") else 0.8
    if fl.get("edge"):
        k *= EDGE_K
    if fl.get("small"):
        k *= SMALL_K
    if fl.get("crowd"):
        k *= CROWD_K
    if fl.get("lesson"):
        k *= LESSON_K
    return k


def scene_kind(face_h, persons):
    """장면 종류 (템플릿 궁합): close 얼굴 크게 · mid 사람 크게 · wide 선수 여럿 작게 · scene 사람 없음."""
    if face_h and face_h >= 0.18:
        return "close"
    if persons:
        hmax = max(p[3] for p in persons)
        if hmax >= 0.5:
            return "mid"
        if len(persons) >= 2 and hmax < 0.45:
            return "wide"
        return "mid" if hmax >= 0.3 else "wide"
    return "scene"


CANDIDATES = "candidates6.json"
N_EVEN = 36         # 고르게 나눠 보는 장면 수
TOP_N = 16          # 후보로 남길 장면 수
SAME_HASH = 10      # 지문 해밍 거리 이하면 같은 장면
PER_SCENE = 3       # 같은 장면에서는 많아야 3장 (말하는 얼굴의 다른 표정은 남게)
CLOSE_MAX = 6       # 얼굴 클로즈업(인터뷰)은 후보 16개 중 많아야 6개 — 다른 장면이 있으면 (판정: 6장 모두 같은 인터뷰 얼굴)
REFINE_N = 8        # 표정 좋은 순간 찾기(앞뒤 ±0.2·0.4초)는 얼굴 장면 8개까지
FACE_CAP = 2.2      # 얼굴·표정 배율 상한 (예전 3.1 — 인터뷰 얼굴이 공 다루는 장면을 늘 이기지 않게)
# 대사 핵심어(editor.KEYWORDS)에 더해, 반응 얼굴이 잘 나오는 감탄사
REACTIONS = {"대박": 3, "우와": 3, "미쳤": 3, "깜짝": 3, "헐": 2, "ㅋㅋ": 2, "웃기": 2}


def _cand_sig(name):
    """장면 후보를 다시 골라야 하는지 판단하는 지문: 영상 크기·수정 시각, 편집점 분석·받아쓰기 시각, 얼굴 모델 유무."""
    vs = (core.VIDEOS / name).stat()
    d = core.adir(name)
    mt = [int(f.stat().st_mtime) if f.exists() else 0 for f in (d / "analysis.json", d / "transcript.json")]
    import face
    import detect
    import avmodels  # 지연 import: avmodels → thumb
    return [vs.st_size, int(vs.st_mtime), *mt, face.ready(), detect.ready(), avmodels.ready("ocr")]


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


def _scene_times(name, dur):
    """장면이 바뀌는 시각 (스타일 배우기 기록이 있으면 그것, 없으면 ffmpeg 장면 감지 — 긴 영상은 키프레임만 봐서 빠르게)."""
    try:
        ev = json.loads((core.adir(name) / "style_events.json").read_text(encoding="utf-8"))
        if isinstance(ev.get("cuts"), list):
            return [float(c) for c in ev["cuts"] if isinstance(c, (int, float))]
    except (OSError, ValueError, AttributeError):
        pass
    cmd = [core.ffmpeg(), "-hide_banner", "-nostats"] + (["-skip_frame", "nokey"] if dur > 180 else []) + \
          ["-i", str(core.VIDEOS / name), "-an", "-sn", "-vf", "scale=96:-2,select='gt(scene,0.32)',showinfo", "-f", "null", "-"]
    try:
        r = core.run(cmd)
    except Exception:
        return []
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr or "")][:200]


def _score_frames(name, ts, use_faces, progress, use_det=False):
    """장면 여러 개를 동시에(4개씩) 뽑아 점수 매김 → {t: (점수, 얼굴 목록 또는 None, 장면 정보)}.
    점수 = 선명도·밝기·인물·자막 감점(_sharpness) × 흔들림 감점 × 얼굴·표정(face.boost) × 액션(선수·공)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import face
    import detect
    from PIL import Image, ImageOps

    def one(t):
        p = grab(name, t)
        if not p.exists():
            return None
        sc, fs = float(_sharpness(p)), None
        with Image.open(p) as im:
            rgb = ImageOps.exif_transpose(im).convert("RGB")
        det = None
        if use_det:
            try:
                det = detect.people(rgb)
            except Exception:
                det = None
        persons = det["persons"] if det else None
        ball = det["ball"] if det else None
        boxes = (persons or [])[:3]
        if boxes:  # 선수 몸이 흔들렸는지 (가장 큰 선수 셋 중 가장 또렷한 쪽)
            blur = min(_blur_of(_gray_crop(rgb, b)) for b in boxes)
        else:
            blur = _blur_of(_gray_crop(rgb, (0.2, 0.15, 0.6, 0.7), 160))
        if use_faces:
            try:
                fs = face.faces(rgb) or []
            except Exception:
                fs = []
            sc *= min(face.boost(fs), FACE_CAP)
        fl = scene_flags(persons or [], ball, fs if use_faces else None, rgb.size[0] / max(1, rgb.size[1]))
        sc *= blur_penalty(blur) * action_score(persons, ball) * (HEADLESS if headless(persons, ball, fs) else 1.0) * flags_mult(fl)
        m = face.main(fs) if fs else None
        info = {"persons": persons or [], "ball": ball, "blur": round(blur, 3), "hash": dhash(rgb),
                "kind": scene_kind(m["box"][3] if m else 0, persons or []), "det": det is not None, "flags": [k for k, v in fl.items() if v]}
        return sc, fs, info  # 자막 띠·자동 보정은 뽑힌 장면에만 (frame_candidates 끝에서 — 세로 영상 첫 분석이 102초 걸린 원인)

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


TEXT_K = 6.0        # 영상에 이미 박힌 큰 글자(다른 썸네일·타이틀 화면) 넓이 1% 마다 6% 감점
TEXT_FLOOR = 0.35   # 감점은 여기까지 (글자 있는 장면만 있는 영상도 후보는 남게)
TEXT_MIN_H = 0.06   # 글자 상자 높이가 화면의 6% 보다 낮으면(간판·등번호·작은 자막) 셈하지 않음


def text_boxes(rgb, band=None, band_y=None):
    """장면에 이미 박혀 있는 큰 글자 상자들 [[x, y, w, h]] (0~1). 글자 읽기 모델이 받아져 있을 때만 (여기서 내려받지 않음) · 없으면 None.
    템플릿이 잘라 내는 자막·방송 띠 안 글자와 작은 글자(간판·등번호)는 뺌."""
    import numpy as np
    import avmodels  # 지연 import: avmodels → thumb
    if not (avmodels.available("ocr") or (avmodels.ready("ocr") and avmodels.ensure("ocr", label="썸네일 분석"))):
        return None
    out = []
    for x in avmodels.ocr(np.asarray(rgb)) or []:
        x0, y0, x1, y1 = x["box"]
        cy = (y0 + y1) / 2
        if x["h"] < TEXT_MIN_H or (band == "bottom" and band_y and cy > band_y) or (band == "top" and band_y and cy < band_y):
            continue
        out.append([round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)])
    return out


def text_area(rgb, band=None, band_y=None):
    """장면에 이미 박혀 있는 큰 글자 넓이(0~1) — text_boxes 의 넓이 합 · 모델이 없으면 None."""
    bs = text_boxes(rgb, band, band_y)
    return None if bs is None else round(min(1.0, sum(b[2] * b[3] for b in bs)), 4)


def text_penalty(area):
    """박힌 글자 넓이 → 점수 배율 (글자 위에 제목을 또 얹으면 겹쳐 지저분함)."""
    return 1.0 if not area else max(TEXT_FLOOR, 1 - TEXT_K * area)


def frame_candidates(name, n=TOP_N):
    """썸네일 배경 후보 장면 n개(최대 16) — 좋은 순.
    점수 = 선명도·밝기·인물(_sharpness) × 흔들림 감점 × 얼굴 크기·웃음/놀람·얼굴 선명도(얼굴이 안 보이면 ×0.6) × 액션(선수 수·크기·공).
    고르게 나눈 36장 + 하이라이트 + 대사 핵심어 순간 + 장면 바뀐 직후 0.5초를 보고, 같은 장면(지문)은 3장까지만,
    뽑힌 장면은 ±0.2·0.4초 옆 장면 중 표정이 가장 좋은 것으로. 영상·편집점 분석·모델 유무가 바뀌면 다시 고름.
    항목: {t, url, score, kind, persons[[x,y,w,h,확률]], ball, main, blur, band, bandY, grade, hash, (faces, emo, face)}"""
    from editor import media_info  # 순환 import 피함
    from PIL import Image, ImageOps
    import face
    import detect
    items = cached_candidates(name)
    if items is not None:
        return items
    use_faces = face.ensure(item=name)  # 처음 한 번 모델 내려받기 (실패하면 조용히 예전 점수로)
    use_det = detect.ensure(item=name)
    sig = _cand_sig(name)
    dur = media_info(name)["duration"]
    ts = [dur * (0.03 + 0.94 * k / (N_EVEN - 1)) for k in range(N_EVEN)]
    extra = core.adir(name) / "analysis.json"
    if extra.exists():
        try:
            ts += [p["time"] for p in json.loads(extra.read_text(encoding="utf-8")).get("loud_peaks", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    ts += _keyword_times(name)
    ts += [c + 0.5 for c in _scene_times(name, dur)][:24]
    uniq = sorted(set(round(x, 1) for x in ts if 0 < x < dur))
    span = 80 if use_faces else 100  # 진행률: 장면 고르기 80% · 표정 다듬기 20%

    def prog(lo, hi, what):
        return lambda i, k: core.set_progress(label="장면 고르는 중", item=name, pct=lo + int(i * (hi - lo) / k), detail=f"{what} {i}/{k}")

    scored = _score_frames(name, uniq, use_faces, prog(0, span, "장면·선수 살펴보는 중"), use_det)
    gap = dur * 0.05  # 후보끼리 영상 길이의 5% 이상 떨어지게 (예전 규칙 그대로)

    def pick(cands, chosen, close_max=n):
        out = list(chosen)
        for t in sorted(cands, key=lambda x: -scored[x][0]):
            if t in out or any(abs(t - q) <= gap for q in out):
                continue
            if sum(hamming(scored[q][2]["hash"], scored[t][2]["hash"]) <= SAME_HASH for q in out) >= PER_SCENE:
                continue
            if scored[t][2]["kind"] == "close" and sum(scored[q][2]["kind"] == "close" for q in out) >= close_max:
                continue
            out.append(t)
            if len(out) >= n:
                break
        return out
    picked = pick(scored, [], CLOSE_MAX)
    if len(picked) < n:  # 다른 장면이 모자라면 얼굴 클로즈업으로 채움
        picked = pick(scored, picked)
    if use_faces and picked:  # 뽑힌 장면 앞뒤 0.2·0.4초 중 표정(점수)이 가장 좋은 순간으로
        def near(t):
            return [x for x in (round(t + d, 1) for d in (-0.4, -0.2, 0.2, 0.4)) if 0 < x < dur]
        faced = [t for t in picked if scored[t][1]][:REFINE_N]  # 얼굴이 있는 좋은 장면만 다듬음 (많아야 REFINE_N 개)
        todo = sorted({x for t in faced for x in near(t)} - set(scored))
        scored.update(_score_frames(name, todo, True, prog(span, 100, "표정 좋은 순간 찾는 중"), use_det))
        best = []
        for k, t in enumerate(picked):  # 옮겨도 다른 장면과 너무 가까워지지 않게 (짧은 영상)
            others = best + picked[k + 1:]
            opts = sorted([t] + [x for x in near(t) if x in scored and hamming(scored[x][2]["hash"], scored[t][2]["hash"]) <= SAME_HASH],
                          key=lambda x: -scored[x][0])  # 다른 장면으로 넘어가지 않게 (장면 경계 근처)
            best += [x for x in opts if x == t or all(abs(x - q) > gap for q in others)][:1]
        picked = best
    items = []
    for k, t in enumerate(sorted(picked, key=lambda x: -scored[x][0])):
        sc, fs, info = scored[t]
        core.set_progress(label="장면 고르는 중", item=name, pct=99, detail=f"글자가 박힌 장면 살피는 중 {k + 1}/{len(picked)}")
        info["band"], info["bandY"] = _band(grab(name, t))
        tb = None
        try:  # 다른 썸네일·타이틀 화면처럼 큰 글자가 이미 있는 장면은 뒤로 (상자는 화면이 피해서 자르거나 가리는 데 씀)
            with Image.open(grab(name, t)) as im:
                rgb = ImageOps.exif_transpose(im).convert("RGB")
            info["grade"] = auto_grade(rgb, close=info["kind"] == "close")
            tb = text_boxes(rgb, info["band"], info["bandY"])
        except Exception:
            info.setdefault("grade", None)
        ta = None if tb is None else round(min(1.0, sum(b[2] * b[3] for b in tb)), 4)
        sc *= text_penalty(ta)
        it = {"t": t, "url": f"/frame?name={name}&t={t}", "score": round(sc, 3), "kind": info["kind"], "persons": info["persons"], "ball": info["ball"],
              "main": main_person(info["persons"], info["ball"]), "blur": info["blur"], "band": info["band"], "bandY": info["bandY"],
              "grade": info["grade"], "hash": info["hash"], "text": ta, "flags": info.get("flags", [])}
        if tb:
            it["tboxes"] = tb[:12]
        if fs:
            m = face.main(fs)
            it.update(faces=fs, emo=m["emo"], face=m["box"][3])  # face: 주인공 얼굴 크기 (화면 높이 대비)
        items.append(it)
    items.sort(key=lambda x: -x["score"])
    cache = _frames_dir(name) / CANDIDATES
    try:  # 임시 파일에 쓴 뒤 바꿔치기 (중간에 꺼져도 깨진 캐시가 남지 않게)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sig": sig, "items": items}, ensure_ascii=False), encoding="utf-8")
        updater._replace(tmp, cache)
    except OSError:
        pass
    return items


_DL_LOCK = threading.Lock()
_SAVE_LOCK = threading.Lock()  # 디자인·브랜드 키트 저장


def _timed_out(e):
    """대답이 없어 시간이 다 됨 (방화벽이 막은 회사·학교 인터넷 등)."""
    return isinstance(e, (socket.timeout, TimeoutError)) or isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError))


class DownloadCancelled(Exception):
    """내려받는 중에 멈추기(✕)를 누름."""


def fetch_model(fname, urls, label, detail, size=None, sha256=None, item=None, timeout=30, cancel=None):
    """모델 파일을 MODELS 에 (없으면) 내려받아 경로 반환. 주소를 차례로 시도하고, 받은 파일은 크기·지문(sha256)을
    확인한 뒤에만 제자리로 (중간에 끊겨도 반쪽 파일이 남지 않게). 모두 실패하면 마지막 오류를 냄.
    대답 없이 시간이 다 되면 다른 주소도 마찬가지라서 더 기다리지 않음.
    urls 의 한 항목은 주소 하나이거나 (주소, 크기, sha256) — 서버마다 변환본이 달라 지문이 다를 때 그 주소만의 값으로 확인."""
    MODELS.mkdir(parents=True, exist_ok=True)
    path = MODELS / fname
    with _DL_LOCK:
        if path.is_file():
            return path
        tmp, err = path.with_suffix(".part"), None
        for entry in urls:
            url, want_size, want_sha = (entry if isinstance(entry, (tuple, list)) else (entry, size, sha256))

            def hook(got, total, want_size=want_size):
                if cancel is not None and cancel():
                    raise DownloadCancelled()
                total = total or want_size or 0
                if total > 0:
                    core.set_progress(label=label, item=item, pct=min(99, int(got * 100 / total)), detail=detail)
            try:
                updater.download(url, tmp, hook, timeout=timeout)
                if want_size and tmp.stat().st_size != want_size:
                    raise OSError("받은 파일 크기가 달라요")
                if want_sha and updater.sha256(tmp) != want_sha:
                    raise OSError("받은 파일 확인(sha256)에 실패했어요")
                updater._replace(tmp, path)
                return path
            except Exception as e:
                err = e
                try:
                    tmp.unlink()
                except OSError:
                    pass
                if isinstance(e, DownloadCancelled):
                    raise
                if _timed_out(e):
                    break
        raise err or OSError("내려받을 주소가 없어요")


def _model(kind):
    fname, size, mean, std, logits, mb = BG_MODELS[kind]
    path = fetch_model(f"{fname}.onnx", [MODEL_URL.format(fname)], "누끼 준비 중", f"배경 제거 모델 내려받는 중 (처음 한 번, {mb})")
    return path, size, mean, std, logits


_SESS = {}


def _bg_mask(img, kind):
    """PIL RGB → 같은 크기의 사람·사물 마스크(L, 0~255)."""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageFilter
    path, size, mean, std, logits = _model(kind)
    if kind not in _SESS:
        _SESS[kind] = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    sess = _SESS[kind]
    x = np.asarray(img.resize((size, size), Image.LANCZOS), dtype=np.float32) / 255.0
    x = (x - np.array(mean, np.float32)) / np.array(std, np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    pred = sess.run(None, {sess.get_inputs()[0].name: x})[0][0][0]
    pred = 1 / (1 + np.exp(-pred)) if logits else (pred - pred.min()) / (pred.max() - pred.min() + 1e-8)
    mask = Image.fromarray((pred * 255).astype(np.uint8)).resize(img.size, Image.LANCZOS)
    return mask.filter(ImageFilter.GaussianBlur(0.6))


def remove_bg(src_path, kind="hq"):
    """배경을 지운 PNG 경로 반환 (원본과 같은 크기 — 브러시로 복원할 때 위치가 맞도록)."""
    from PIL import Image, ImageOps
    core.set_progress(label="누끼 따는 중", pct=None, detail="인물·사물만 남기는 중 (20~40초)")
    img = ImageOps.exif_transpose(Image.open(src_path)).convert("RGB")  # 휴대폰 사진 회전 정보 반영 (브라우저와 같은 방향)
    out = img.convert("RGBA")
    out.putalpha(_bg_mask(img, kind))
    dst = ASSETS / f"cut_{int(time.time() * 1000)}.png"
    out.save(dst)
    return dst


CUT_MARGIN = 1.6   # 자동 누끼: 주인공 상자를 이만큼 넓혀 잘라서 모델에 넣음 (넓은 장면의 작은 선수도 또렷하게)
CUT_VER = 3        # 자동 누끼 판 (마스크 다듬기·품질 검사가 바뀌면 올림 → 다시 땀)
CUT_LO, CUT_HI = 0.2, 0.8   # 다듬기: 알파 0.2 아래는 버리고 0.8 위는 꽉 채움 (흐린 잔상·반투명 번짐이 외곽선·광선을 끌고 다니지 않게)
# 누끼 품질 합격선 (판정: 작은·흐린 선수 누끼가 얼굴이 하얗게 번지고 외곽선이 벽으로 샘)
CUT_Q = {"leak": 0.12, "fill": 0.22, "comp": 0.8, "face": 0.8, "soft": 0.35}


def _components(b):
    """흑백(참/거짓) 2차원 배열의 4-연결 덩어리 → (번호 배열(0 = 바탕), 덩어리별 크기 목록). 작은 그림에서만 씀 (순수 파이썬)."""
    import numpy as np
    h, w = b.shape
    lab = np.zeros((h, w), np.int32)
    sizes = [0]
    flat = b.ravel()
    lf = lab.ravel()
    for start in np.flatnonzero(flat):
        if lf[start]:
            continue
        k = len(sizes)
        stack, n = [start], 0
        lf[start] = k
        while stack:
            i = stack.pop()
            n += 1
            y, x = divmod(i, w)
            for j in (i - w if y else -1, i + w if y < h - 1 else -1, i - 1 if x else -1, i + 1 if x < w - 1 else -1):
                if j >= 0 and flat[j] and not lf[j]:
                    lf[j] = k
                    stack.append(j)
        sizes.append(n)
    return lab, sizes


def clean_mask(mask, box=None, erase=None, keep=None):
    """배경 제거 마스크(L) 다듬기: 반투명 번짐을 끊고(CUT_LO~HI), 영상에 박힌 큰 글자 상자(erase, 0~1)는 지우고(얼굴 상자 keep 은 남김),
    주인공 상자와 닿는 덩어리만 남김 (벽·다른 사람 조각·글자 조각 버림) → L."""
    import numpy as np
    from PIL import Image
    a = np.asarray(mask, np.float32) / 255
    a = np.clip((a - CUT_LO) / (CUT_HI - CUT_LO), 0, 1)
    H, W = a.shape
    for b in erase or []:  # 누끼 모델은 박힌 글자도 '앞의 것'으로 남김 ('(진정해ㅎ' 같은 글자 조각이 누끼에 붙어 나옴)
        x0, y0, x1, y1 = int(max(0, (b[0] - 0.01) * W)), int(max(0, (b[1] - 0.01) * H)), int(min(W, (b[0] + b[2] + 0.01) * W)), int(min(H, (b[1] + b[3] + 0.01) * H))
        if x1 <= x0 or y1 <= y0:
            continue
        saved = [(k, a[max(0, int(k[1] * H)):int((k[1] + k[3]) * H), max(0, int(k[0] * W)):int((k[0] + k[2]) * W)].copy()) for k in keep or []]
        a[y0:y1, x0:x1] = 0
        for k, v in saved:
            a[max(0, int(k[1] * H)):int((k[1] + k[3]) * H), max(0, int(k[0] * W)):int((k[0] + k[2]) * W)] = v
    k = 160 / max(1, H)
    sw, sh = max(8, int(W * k)), 160
    small = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((sw, sh), Image.BILINEAR), np.float32) / 255 > 0.5
    lab, sizes = _components(small)
    if len(sizes) > 1:
        keep = np.zeros(len(sizes), bool)
        if box:
            x, y, w, h = box[:4]
            x0, y0, x1, y1 = int((x - w * 0.08) * sw), int((y - h * 0.05) * sh), int((x + w * 1.08) * sw) + 1, int((y + h * 1.05) * sh) + 1
            ids = np.unique(lab[max(0, y0):max(0, y1), max(0, x0):max(0, x1)])
            big = max(sizes[1:])
            for i in ids:
                if i and sizes[i] >= max(4, big * 0.03):
                    keep[i] = True
        else:
            keep[int(np.argmax(sizes[1:])) + 1] = True
        km = Image.fromarray((keep[lab] * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
        from PIL import ImageFilter
        km = np.asarray(km.filter(ImageFilter.MaxFilter(5)), np.float32) / 255  # 덩어리 가장자리 반 칸을 잃지 않게 조금 넓혀서 곱함
        a = a * np.clip(km * 1.5, 0, 1)
    return Image.fromarray((a * 255).astype(np.uint8))


def cut_quality(mask, box=None, faces=None):
    """다듬은 누끼 마스크의 품질 → {leak, fill, comp, face, soft, ok, q}.
    leak 주인공 상자 밖으로 나간 몫 · fill 상자 안을 채운 몫 · comp 가장 큰 덩어리 몫 · face 주인공 얼굴 안 평균 알파 · soft 반투명 테두리 비율.
    q 0~1 (합격선에 가까울수록 낮음) · ok = 합격선을 모두 넘음."""
    import numpy as np
    from PIL import Image
    a = np.asarray(mask, np.float32) / 255
    H, W = a.shape
    tot = float(a.sum()) + 1e-6
    r = {"leak": 0.0, "fill": 1.0, "comp": 1.0, "face": 1.0, "soft": 0.0}
    k = 160 / max(1, H)
    small = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((max(8, int(W * k)), 160), Image.BILINEAR), np.float32) / 255 > 0.5
    _, sizes = _components(small)
    r["comp"] = round(max(sizes[1:]) / max(1, sum(sizes[1:])), 3) if len(sizes) > 1 else 0.0
    solid = float((a > 0.5).sum()) + 1e-6
    r["soft"] = round(float(((a > 0.08) & (a < 0.92)).sum()) / solid, 3)
    if box:
        x, y, w, h = box[:4]
        x0, y0, x1, y1 = int(max(0, (x - w * 0.08) * W)), int(max(0, (y - h * 0.05) * H)), int(min(W, (x + w * 1.08) * W)), int(min(H, (y + h * 1.05) * H))
        r["leak"] = round(1 - float(a[y0:y1, x0:x1].sum()) / tot, 3)
        bx0, by0, bx1, by1 = int(x * W), int(y * H), max(int(x * W) + 1, int((x + w) * W)), max(int(y * H) + 1, int((y + h) * H))
        r["fill"] = round(float((a[by0:by1, bx0:bx1] > 0.5).mean()), 3)
        for f in faces or []:
            fx, fy, fw, fh = f["box"][:4]
            if not (x <= fx + fw / 2 <= x + w and y <= fy + fh / 2 <= y + h * 0.5):
                continue
            px0, py0, px1, py1 = int((fx + fw * 0.2) * W), int((fy + fh * 0.2) * H), int((fx + fw * 0.8) * W) + 1, int((fy + fh * 0.8) * H) + 1
            r["face"] = round(min(r["face"], float(a[py0:py1, px0:px1].mean()) if py1 > py0 and px1 > px0 else 1.0), 3)
    elif tot < 1:
        r["fill"] = 0.0
    lim = CUT_Q
    r["ok"] = bool(r["leak"] <= lim["leak"] and r["fill"] >= lim["fill"] and r["comp"] >= lim["comp"] and r["face"] >= lim["face"] and r["soft"] <= lim["soft"])
    q = min(1.0, (1 - r["leak"]) / (1 - lim["leak"])) * min(1.0, r["fill"] / 0.4) * min(1.0, r["comp"]) * min(1.0, r["face"] / 0.95) * min(1.0, (1 - r["soft"]) / (1 - lim["soft"] / 2))
    r["q"] = round(max(0.0, q), 3)
    return r


def cut_auto(name, t, box=None, kind="fast", faces=None, tboxes=None):
    """자동 추천용 누끼: 장면 t 에서 주인공 상자(0~1) 주변만 잘라 배경을 지우고, 다듬어(clean_mask) 원본 크기 투명 PNG 에 다시 붙임.
    상자가 없으면 장면 전체. 품질(cut_quality)은 옆 JSON 에 기억. (영상, 시각, 종류, 상자, 판)이 같으면 만들어 둔 파일을 그대로 씀 → (PNG 경로, 품질)."""
    from PIL import Image
    dst = _cut_path(name, t, box, kind)
    box = [round(float(v), 3) for v in box[:4]] if box else None
    qf = dst.with_suffix(".json")
    if dst.is_file() and qf.is_file():
        try:
            return dst, json.loads(qf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    with Image.open(grab(name, t)) as im:
        img = im.convert("RGB")
    W, H = img.size
    if box:
        x, y, w, h = box
        cx, cy = (x + w / 2) * W, (y + h / 2) * H
        cw, ch = max(w * CUT_MARGIN * W, 0.25 * W), max(h * CUT_MARGIN * H, 0.3 * H)
        x0, y0 = int(max(0, cx - cw / 2)), int(max(0, cy - ch / 2))
        x1, y1 = int(min(W, cx + cw / 2)), int(min(H, cy + ch / 2))
        full = Image.new("L", (W, H), 0)
        full.paste(_bg_mask(img.crop((x0, y0, x1, y1)), kind), (x0, y0))
    else:
        full = _bg_mask(img, kind)
    full = clean_mask(full, box, tboxes, [f["box"] for f in faces or []])
    q = cut_quality(full, box, faces)
    out = img.convert("RGBA")
    out.putalpha(full)
    tmp = dst.with_suffix(".tmp")
    out.save(tmp, "PNG")
    updater._replace(tmp, dst)
    try:
        qt = qf.with_suffix(".jtmp")
        qt.write_text(json.dumps(q), encoding="utf-8")
        updater._replace(qt, qf)
    except OSError:
        pass
    return dst, q


AUTO_CUTS = 4   # 분석할 때 미리 딸 누끼 (좋은 장면 순, 사람·얼굴이 있는 장면) — 추천에 쓰는 장면 6개 중 앞쪽
CUT_MIN_H = 0.3   # 누끼 딸 주인공은 원본 화면 높이의 30% 이상 (작은 선수 누끼는 깨짐 — 판정)
CUT_MAX_BLUR = 0.35


def _cut_targets(items):
    """미리 딸 누끼 대상: 주인공이 크고(높이 30% 이상) 또렷한 좋은 장면 3개 (얼굴 클로즈업 포함) → [(장면, 주인공 상자 또는 None)].
    뒷모습·좌우 끝에 걸린 사람·벤치 장면은 뺌."""
    ok = []
    for it in items:
        box = it["persons"][it["main"]][:4] if it.get("main", -1) >= 0 and it.get("persons") else None
        if box is None and not it.get("faces"):
            continue
        if (box and box[3] < CUT_MIN_H and not it.get("faces")) or (it.get("blur") or 0) > CUT_MAX_BLUR or set(it.get("flags") or []) & {"back", "edge", "bench"}:
            continue
        ok.append((it, box))
    out = []
    for it, box in ok:  # 서로 다른 장면 먼저 (같은 화면 세 장을 따면 추천도 한 장면만 누끼를 씀)
        if not any(hamming(it.get("hash"), o.get("hash")) <= SAME_HASH for o, _ in out):
            out.append((it, box))
    out += [x for x in ok if x not in out]
    return out[:AUTO_CUTS]


def _cut_path(name, t, box, kind="fast"):
    import hashlib
    box = [round(float(v), 3) for v in box[:4]] if box else None
    key = hashlib.sha1(f"{core.adir(name).name}|{float(t):.3f}|{kind}|{box}|v{CUT_VER}".encode("utf-8")).hexdigest()[:16]
    return ASSETS / f"cut_auto_{key}.png"


def _cut_q(p):
    """옆 JSON 에 기억한 누끼 품질 (없으면 None)."""
    try:
        return json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cached_analysis(name):
    """썸네일 분석(장면 후보·자동 누끼·문구)이 다 돼 있으면 바로 → dict, 아니면 None."""
    items = cached_candidates(name)
    if items is None or need_ai_frames(name, items):  # 클로드 장면 고르기만 남았어도 분석 작업으로 (장면·누끼는 캐시라 금방)
        return None
    cuts = {}
    for it, box in _cut_targets(items):
        p = _cut_path(name, it["t"], box)
        q = _cut_q(p)
        if not p.is_file() or q is None:
            return None
        cuts[str(it["t"])] = {"cut": asset_url(p), "src": it["url"], "box": box, "q": q}
    import thumbcopy  # 문구 (가벼운 규칙 · 지연 import: thumbcopy → hooks)
    return {"frames": _with_ai_frames(name, items), "cuts": cuts, "copy": thumbcopy.suggest(name)}


AI_COPY_WAIT = 160   # 분석 끝에 클로드 문구를 기다리는 최대 시간(초) — 장면·누끼와 동시에 시작함


def _ai_on(name):
    """클로드 자동 (문구·장면 고르기): 브랜드 키트에서 켜져 있고 클로드 프로그램이 로그인돼 있을 때만."""
    import thumbcopy
    try:
        return bool(load_brand().get("aiCopy", True)) and thumbcopy.ai_ready()
    except Exception:
        return False


def _start_ai_copy(name, log):
    """클로드 문구를 장면 분석과 동시에 (브랜드 키트 '클로드 자동'이 켜져 있고, 클로드 프로그램이 로그인돼 있고, 기억한 문구가 없을 때만) → 스레드 또는 None."""
    import thumbcopy
    try:
        if thumbcopy.load_ai(name) is not None or not _ai_on(name):
            return None
    except Exception:
        return None
    import editor  # 멈추기(✕) — 지연 import (무거운 모듈)

    def go():
        try:
            thumbcopy.run_ai(name, log, editor.CANCEL, progress=False)
        except Exception as e:  # 클로드가 안 돼도 규칙 문구로 계속
            log(f"  클로드 문구를 받지 못했어요 · {type(e).__name__}")
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


# ---------- 클로드 장면 고르기 (선택 · 내 클로드 계정): 후보 장면 시트를 보여 주고 썸네일 배경으로 1~10점 ----------
# 판정에서 가장 큰 감점이 '장면이 약함·레슨과 무관'(61/84) 이었고 규칙(선수·공·얼굴)만으로는 관중·앵커·잡지 같은 장면을 못 거름 → 같은 판단을 사용자 클로드에게
AI_FRAMES = "thumb_frames_ai.json"
AI_FRAMES_PROMPT = """당신은 구독자 수십만 한국 풋살·축구 레슨 채널(쪼살·쌈바 풋살 클래스·풋살해주호)의 썸네일 디자이너예요.
같은 폴더의 frames.jpg 를 Read 도구로 열어 보세요. 영상 '{title}'(주제: {topics})에서 뽑은 장면 후보 {n}개에 노란 번호(1~{n})가 붙어 있어요.
각 장면을 이 영상 썸네일의 배경 사진으로 쓰기에 얼마나 좋은지 1~10점으로 매기세요 (5 = 그럭저럭, 8 = 프로 레슨 채널이 쓸 만함). 엄격하게:
- 좋은 장면: 공을 다루는 순간(드리블·슈팅·패스·1대1·트래핑)이 또렷함 · 주인공이 크고 얼굴이나 몸 전체가 보임 · 영상 주제와 맞음 · 설명하는 코치/선수의 표정이 살아 있는 얼굴
- 나쁜 장면: 흐림·흔들림 · 뒷모습·뒤통수 · 벤치·관중·구경꾼 · 사람이 아주 작고 멂 · 빈 벽·바닥 · 다른 썸네일이나 큰 글자가 박힌 화면 · 주제와 상관없는 사람(앵커·관중)이나 물건
대답은 JSON 하나만 (설명 없이): {{"frames": [{{"n": 1, "score": 0, "why": "한 줄"}}, ...]}}"""


def _frames_sig(items):
    return [round(float(it["t"]), 2) for it in items]


AI_RETRY = 3600   # 클로드 장면 고르기가 실패했으면 1시간 동안은 다시 부르지 않음 (한도·로그인 문제로 열 때마다 기다리지 않게)


def _ai_frames_file(name):
    try:
        return json.loads((core.adir(name) / AI_FRAMES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_ai_frames(name, items):
    """기억해 둔 클로드 장면 점수 {t(문자): {score, why}} (후보 장면이 그대로일 때만) 또는 None."""
    d = _ai_frames_file(name)
    if isinstance(d, dict) and d.get("sig") == _frames_sig(items) and isinstance(d.get("items"), dict):
        return d["items"]
    return None


def need_ai_frames(name, items):
    """클로드 장면 고르기를 (다시) 해야 하는지: 클로드 자동이 켜져 있고, 이 후보로 받은 점수가 없고, 최근에 실패하지 않았음."""
    if load_ai_frames(name, items) is not None:
        return False
    d = _ai_frames_file(name)
    if isinstance(d, dict) and d.get("failSig") == _frames_sig(items) and 0 <= time.time() - float(d.get("failAt") or 0) < AI_RETRY:
        return False
    return _ai_on(name)


def _save_ai_frames(name, data):
    p = core.adir(name) / AI_FRAMES
    try:
        tmpf = p.with_suffix(".tmp")
        tmpf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        updater._replace(tmpf, p)
    except OSError:
        pass


def _frame_sheet(name, items, path):
    """후보 장면 시트 (4열 격자, 왼쪽 위 노란 번호) → path."""
    from PIL import Image, ImageDraw, ImageFont
    tiles = []
    for it in items:
        with Image.open(grab(name, it["t"])) as im:
            rgb = im.convert("RGB")
        rgb.thumbnail((440, 440))
        tiles.append(rgb)
    tw, th = max(t.size[0] for t in tiles), max(t.size[1] for t in tiles)
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 8) + 8, rows * (th + 8) + 8), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(str(core.APP_DIR / "fonts" / "Pretendard-Black.otf"), 34)
    except OSError:
        font = ImageFont.load_default()
    for i, t in enumerate(tiles):
        x, y = 8 + (i % cols) * (tw + 8), 8 + (i // cols) * (th + 8)
        sheet.paste(t, (x + (tw - t.size[0]) // 2, y + (th - t.size[1]) // 2))
        d.rectangle([x, y, x + 52, y + 44], fill=(255, 210, 0))
        d.text((x + 8, y + 2), str(i + 1), fill=(0, 0, 0), font=font)
    sheet.save(path, quality=88)


def parse_ai_frames(text, n):
    """클로드 대답 → {번호(1~n): (점수 1~10, 이유)}. 형식이 아니면 ValueError."""
    s = str(text or "")
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("장면 점수 형식을 찾지 못했어요")
    try:
        d = json.loads(s[a:b + 1])
    except ValueError:
        raise ValueError("장면 점수 형식이 깨져 있어요") from None
    out = {}
    for x in (d.get("frames") if isinstance(d, dict) else None) or []:
        try:
            k, sc = int(x.get("n")), float(x.get("score"))
        except (AttributeError, TypeError, ValueError):
            continue
        if 1 <= k <= n:
            out[k] = (max(1.0, min(10.0, sc)), re.sub(r"\s+", " ", str(x.get("why") or "")).strip()[:80])
    if not out:
        raise ValueError("장면 점수가 없었어요")
    return out


def run_ai_frames(name, items, log=print, cancel=None):
    """후보 장면 시트를 내 클로드 계정에 보여 주고 장면마다 1~10점 → 기억 (analysis/<영상>/thumb_frames_ai.json) → {t: {score, why}} 또는 None."""
    import tempfile
    import claude_cli
    import thumbcopy
    items = items[:16]
    if not items:
        return None
    with tempfile.TemporaryDirectory(prefix="futsal-frames-", ignore_cleanup_errors=True) as tmp:
        sheet = Path(tmp) / "frames.jpg"
        _frame_sheet(name, items, sheet)
        title = thumbcopy.nice_title(name)
        tp = thumbcopy.topics(title, thumbcopy._texts(name))
        prompt = AI_FRAMES_PROMPT.format(title=title, topics=", ".join(tp) or "풋살", n=len(items))
        try:
            res = claude_cli.run(prompt, images=[(sheet, "frames.jpg")], cancel=cancel, timeout=thumbcopy.AI_TIMEOUT)
        except claude_cli.ClaudeError as e:
            log(f"  클로드 장면 고르기 · {e.kind}")
            if e.kind != "cancel":
                _save_ai_frames(name, {"failSig": _frames_sig(items), "failAt": int(time.time())})
            return None
    try:
        sc = parse_ai_frames(res.get("text"), len(items))
    except ValueError:
        log("  클로드 장면 고르기 · 형식 다름")
        _save_ai_frames(name, {"failSig": _frames_sig(items), "failAt": int(time.time())})
        return None
    out = {str(it["t"]): {"score": sc[i + 1][0], "why": sc[i + 1][1]} for i, it in enumerate(items) if i + 1 in sc}
    _save_ai_frames(name, {"sig": _frames_sig(items), "items": out})
    log(f"  클로드 장면 고르기 · {len(out)}장")
    return out


def _with_ai_frames(name, items):
    """장면 후보에 클로드 장면 점수(ai 1~10)·이유를 붙인 사본."""
    sc = load_ai_frames(name, items) or {}
    out = []
    for it in items:
        x = dict(it)
        a = sc.get(str(it["t"]))
        if a:
            x["ai"], x["aiWhy"] = a.get("score"), a.get("why", "")
        out.append(x)
    return out


def _wait(threads, name, what):
    t0 = time.time()
    for th in threads:
        while th is not None and th.is_alive() and time.time() - t0 < AI_COPY_WAIT:
            core.set_progress(label="썸네일 분석", item=name, pct=99, detail=f"클로드가 {what} (내 클로드 계정 사용 · {int(time.time() - t0)}초)")
            th.join(0.5)


def analyze(name, log=print):
    """'AI 추천 썸네일' 분석 작업: (클로드 문구 · 동시에) 장면·선수 후보(v6) → (클로드 장면 고르기 · 동시에) 주인공 누끼 4장(빠른 모델, 다듬기·품질 검사) → 문구 후보.
    누끼·클로드를 못 써도 나머지는 그대로."""
    ai = _start_ai_copy(name, log)
    items = frame_candidates(name)
    af = None
    if need_ai_frames(name, items):
        import editor  # 멈추기(✕)
        af = threading.Thread(target=lambda: run_ai_frames(name, items, log, editor.CANCEL), daemon=True)
        af.start()
    cuts = {}
    todo = _cut_targets(items)
    for i, (it, box) in enumerate(todo):
        core.set_progress(label="썸네일 분석", item=name, pct=int(i * 100 / max(1, len(todo))), detail=f"주인공 누끼 따는 중 {i + 1}/{len(todo)}")
        try:
            p, q = cut_auto(name, it["t"], box, faces=it.get("faces"), tboxes=it.get("tboxes"))
        except Exception as e:  # 모델을 못 받는 등 — 누끼 없는 추천으로 계속
            log(f"  자동 누끼를 따지 못했어요 · {e}")
            break
        cuts[str(it["t"])] = {"cut": asset_url(p), "src": it["url"], "box": box, "q": q}
    import thumbcopy
    _wait([ai, af], name, "제목 문구·장면을 고르는 중…")
    core.set_progress(label="썸네일 분석", item=name, pct=99, detail="제목 문구 만드는 중")
    return {"frames": _with_ai_frames(name, items), "cuts": cuts, "copy": thumbcopy.suggest(name)}


OCR_MAX = 400_000   # 검수용 글자 읽기에 받는 그림 크기 상한 (dataURL 글자 수)


def read_text(data_url):
    """검수: 작게 줄인 썸네일 그림 → 읽힌 글자 줄 [{text, conf}] · 글자 읽기 모델이 아직 없으면 None (여기서는 내려받지 않음)."""
    import numpy as np
    from PIL import Image
    import avmodels  # 지연 import: avmodels → thumb
    if not (avmodels.available("ocr") or (avmodels.ready("ocr") and avmodels.ensure("ocr", label="썸네일 검수"))):
        return None
    raw = base64.b64decode(str(data_url).split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        rgb = im.convert("RGB")
    if rgb.size[0] < 640:  # 휴대폰 목록 크기(168px) 그대로 → 4배로 키워 읽기 (모델 입력 폭에 맞춤)
        rgb = rgb.resize((rgb.size[0] * 4, rgb.size[1] * 4), Image.BICUBIC)
    lines = avmodels.ocr(np.asarray(rgb)) or []
    return [{"text": x["text"], "conf": x["conf"], "box": x["box"]} for x in lines]


# ---------- 브랜드 키트 (채널 로고·색·글꼴·시리즈 이름 — 영상과 상관없이 하나) ----------

BRAND_COLORS = ("hl", "hl2", "accent", "neon", "box")   # 강조 글자 · 기본 글자 · 포인트(빨강) · 네온(전술) · 상자
BRAND_FONTS = ("Black Han Sans", "Do Hyeon", "Jua", "Pretendard Black", "Pretendard Bold", "Dokdo")
BRAND_DEFAULT = {"logo": "", "logoPos": "tr", "colors": {"hl": "#FFE14D", "hl2": "#FFFFFF", "accent": "#FF3B30", "neon": "#00D1FF", "box": "#111111"},
                 "font": "Black Han Sans", "series": "풋사관 강좌", "seriesOn": False, "handle": "@풋살사관학교", "apply": True, "aiCopy": True}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _brand_path():
    return THUMBS / "brand.json"


def check_brand(d):
    """브랜드 키트 검사 → 고친 사본. 잘못된 값이면 ValueError(화면에 보일 한국어)."""
    if not isinstance(d, dict):
        raise ValueError("브랜드 키트 형식이 잘못됐어요")
    out = json.loads(json.dumps(BRAND_DEFAULT))
    cols = d.get("colors", {})
    if not isinstance(cols, dict):
        raise ValueError("색 형식이 잘못됐어요")
    for k, v in cols.items():
        if k not in BRAND_COLORS or not isinstance(v, str) or not _HEX.match(v):
            raise ValueError(f"색 '{k}' 값이 잘못됐어요 (#RRGGBB)")
        out["colors"][k] = v.upper()
    logo = d.get("logo", "")
    if logo:
        if not isinstance(logo, str) or not logo.startswith("/asset/") or Path(logo).name != logo[len("/asset/"):]:
            raise ValueError("로고는 썸네일에 올린 그림만 쓸 수 있어요")
        f = (ASSETS / Path(logo).name).resolve()
        if f.parent != ASSETS.resolve() or not f.is_file():
            raise ValueError("로고 그림을 찾지 못했어요. 다시 올려 주세요")
    out["logo"] = logo or ""
    if d.get("logoPos", "tr") not in ("tr", "tl", "off"):
        raise ValueError("로고 위치가 잘못됐어요")
    out["logoPos"] = d.get("logoPos", "tr")
    if d.get("font", out["font"]) not in BRAND_FONTS:
        raise ValueError("고를 수 없는 글꼴이에요")
    out["font"] = d.get("font", out["font"])
    for k, n in (("series", 20), ("handle", 30)):
        v = d.get(k, out[k])
        if not isinstance(v, str) or len(v) > n or any(c in v for c in "<>\r\n"):
            raise ValueError(f"{'시리즈 이름' if k == 'series' else '채널 이름'}은 {n}자 안쪽으로 써 주세요")
        out[k] = v.strip()
    for k in ("seriesOn", "apply", "aiCopy"):
        out[k] = bool(d.get(k, out[k]))
    return out


def load_brand():
    """저장한 브랜드 키트 (없거나 깨졌으면 바로 전 저장본 → 기본값)."""
    p = _brand_path()
    for f in (p, p.with_suffix(".json.bak")):
        try:
            return check_brand(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return json.loads(json.dumps(BRAND_DEFAULT))


def save_brand(d):
    """검사한 뒤 안전하게 저장 (임시 파일 → 바꿔치기, 바로 전 저장본은 .bak) → 저장한 값."""
    b = check_brand(d)
    p = _brand_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _SAVE_LOCK:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        if p.exists():
            try:
                os.replace(p, p.with_suffix(".json.bak"))
            except OSError:
                pass
        updater._replace(tmp, p)
    return b


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


AB_TAGS = "ABCDEF"
AB_MAX = 2_900_000   # 한 장 dataURL 글자 수 상한 (JPG 2MB 안쪽)


def export_ab(name, items, mobile=None):
    """A/B 묶음 저장: 썸네일 여러 장(dataURL)을 '<영상>_썸네일_A.jpg'·'_B'·… 와 '<영상>_모바일 비교.jpg' 로.
    같은 이름이 있으면 묶음 전체를 ' (2)'·' (3)'… 로 (예전 묶음을 덮어쓰지 않음) → 저장한 파일 이름들."""
    if not isinstance(items, list) or not 2 <= len(items) <= len(AB_TAGS):
        raise ValueError("A/B 로 저장할 썸네일을 2~6개 골라 주세요")
    datas = list(items) + ([mobile] if mobile else [])
    for d in datas:
        if not isinstance(d, str) or not d.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")) or len(d) > AB_MAX:
            raise ValueError("그림 형식이 다르거나 너무 커요")
    base = core.adir(name).name
    k = 1
    while True:
        suf = "" if k == 1 else f" ({k})"
        files = [core.OUT / f"{base}_썸네일_{AB_TAGS[i]}{suf}.jpg" for i in range(len(items))] + ([core.OUT / f"{base}_모바일 비교{suf}.jpg"] if mobile else [])
        if not any(f.exists() for f in files):
            break
        k += 1
    core.OUT.mkdir(parents=True, exist_ok=True)
    for f, d in zip(files, datas):
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_bytes(base64.b64decode(d.split(",", 1)[1]))
        updater._replace(tmp, f)
    return [f.name for f in files]
