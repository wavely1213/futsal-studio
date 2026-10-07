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
GAMMA_RANGE = (0.8, 1.8)  # 어두운 실내·저녁 경기장도 평균 밝기 0.38 이상으로 (1.25 로는 모자랐음)


def auto_grade(src, close=False):
    """장면 한 장의 자동 보정 숫자 (화면 thumb.html 의 applyGrade 와 짝: 레벨 lo/hi·색온도 → 감마(밝기에만) → 자연 채도 → 클래리티 → 샤픈).
    - 레벨: 채널별 0.5%/99.5% 지점 (밝기 기준에서 ±12 안쪽으로 — 잔디 초록에 끌려 색이 틀어지지 않게, 이동량 ≤ MAX_SHIFT)
    - 감마: 보정 뒤 평균 밝기가 0.48 이 되게 (0.8~1.8) · 자연 채도: 보정 뒤 채도가 약 1.2배 되게 · 색온도: 회색 부분이 푸르면(형광등) 따뜻하게
    - 클래리티 35 (얼굴 클로즈업이면 20) · 샤픈 25"""
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
    g = {"on": True, "amt": 1, "lo": lo, "hi": hi, "gamma": round(float(gamma), 3), "vib": 0, "clarity": 20 if close else 35, "temp": temp, "sharpen": 25}
    # 자연 채도: 레벨만으로도 채도가 오르므로, 보정 뒤 채도가 원본의 약 1.2배가 되는 값을 고름 (화면 applyGrade 와 같은 계산으로 어림)
    s0 = float(sat.mean())
    best = None
    for v in range(-40, 85, 5):
        r = _sat_after(a, dict(g, vib=v)) / max(1e-6, s0)
        if best is None or abs(r - SAT_GAIN) < abs(best[1] - SAT_GAIN):
            best = (v, r)
    g["vib"] = best[0]
    return g


SAT_GAIN = 1.2   # 자동 보정 뒤 채도 목표 (원본 대비 · 기준표 +5~35%)


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


CANDIDATES = "candidates4.json"
N_EVEN = 36         # 고르게 나눠 보는 장면 수
TOP_N = 16          # 후보로 남길 장면 수
SAME_HASH = 10      # 지문 해밍 거리 이하면 같은 장면
PER_SCENE = 3       # 같은 장면에서는 많아야 3장 (말하는 얼굴의 다른 표정은 남게)
# 대사 핵심어(editor.KEYWORDS)에 더해, 반응 얼굴이 잘 나오는 감탄사
REACTIONS = {"대박": 3, "우와": 3, "미쳤": 3, "깜짝": 3, "헐": 2, "ㅋㅋ": 2, "웃기": 2}


def _cand_sig(name):
    """장면 후보를 다시 골라야 하는지 판단하는 지문: 영상 크기·수정 시각, 편집점 분석·받아쓰기 시각, 얼굴 모델 유무."""
    vs = (core.VIDEOS / name).stat()
    d = core.adir(name)
    mt = [int(f.stat().st_mtime) if f.exists() else 0 for f in (d / "analysis.json", d / "transcript.json")]
    import face
    import detect
    return [vs.st_size, int(vs.st_mtime), *mt, face.ready(), detect.ready()]


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
            sc *= face.boost(fs)
        sc *= blur_penalty(blur) * action_score(persons, ball)
        m = face.main(fs) if fs else None
        band, band_y = _band(p)
        info = {"persons": persons or [], "ball": ball, "blur": round(blur, 3), "hash": dhash(rgb), "band": band, "bandY": band_y,
                "kind": scene_kind(m["box"][3] if m else 0, persons or []), "det": det is not None}
        info["grade"] = auto_grade(rgb, close=info["kind"] == "close")
        return sc, fs, info

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


def frame_candidates(name, n=TOP_N):
    """썸네일 배경 후보 장면 n개(최대 16) — 좋은 순.
    점수 = 선명도·밝기·인물(_sharpness) × 흔들림 감점 × 얼굴 크기·웃음/놀람·얼굴 선명도(얼굴이 안 보이면 ×0.6) × 액션(선수 수·크기·공).
    고르게 나눈 36장 + 하이라이트 + 대사 핵심어 순간 + 장면 바뀐 직후 0.5초를 보고, 같은 장면(지문)은 3장까지만,
    뽑힌 장면은 ±0.2·0.4초 옆 장면 중 표정이 가장 좋은 것으로. 영상·편집점 분석·모델 유무가 바뀌면 다시 고름.
    항목: {t, url, score, kind, persons[[x,y,w,h,확률]], ball, main, blur, band, bandY, grade, hash, (faces, emo, face)}"""
    from editor import media_info  # 순환 import 피함
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

    def pick(cands, chosen):
        out = list(chosen)
        for t in sorted(cands, key=lambda x: -scored[x][0]):
            if t in out or any(abs(t - q) <= gap for q in out):
                continue
            if sum(hamming(scored[q][2]["hash"], scored[t][2]["hash"]) <= SAME_HASH for q in out) >= PER_SCENE:
                continue
            out.append(t)
            if len(out) >= n:
                break
        return out
    picked = pick(scored, [])
    if use_faces and picked:  # 뽑힌 장면 앞뒤 0.2·0.4초 중 표정(점수)이 가장 좋은 순간으로
        def near(t):
            return [x for x in (round(t + d, 1) for d in (-0.4, -0.2, 0.2, 0.4)) if 0 < x < dur]
        todo = sorted({x for t in picked if scored[t][1] for x in near(t)} - set(scored))  # 얼굴이 있는 장면만 다듬음
        scored.update(_score_frames(name, todo, True, prog(span, 100, "표정 좋은 순간 찾는 중"), use_det))
        best = []
        for k, t in enumerate(picked):  # 옮겨도 다른 장면과 너무 가까워지지 않게 (짧은 영상)
            others = best + picked[k + 1:]
            opts = sorted([t] + [x for x in near(t) if x in scored and hamming(scored[x][2]["hash"], scored[t][2]["hash"]) <= SAME_HASH],
                          key=lambda x: -scored[x][0])  # 다른 장면으로 넘어가지 않게 (장면 경계 근처)
            best += [x for x in opts if x == t or all(abs(x - q) > gap for q in others)][:1]
        picked = best
    items = []
    for t in sorted(picked, key=lambda x: -scored[x][0]):
        sc, fs, info = scored[t]
        it = {"t": t, "url": f"/frame?name={name}&t={t}", "score": round(sc, 3), "kind": info["kind"], "persons": info["persons"], "ball": info["ball"],
              "main": main_person(info["persons"], info["ball"]), "blur": info["blur"], "band": info["band"], "bandY": info["bandY"],
              "grade": info["grade"], "hash": info["hash"]}
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


def cut_auto(name, t, box=None, kind="fast"):
    """자동 추천용 누끼: 장면 t 에서 주인공 상자(0~1) 주변만 잘라 배경을 지우고, 원본 크기 투명 PNG 에 다시 붙임.
    상자가 없으면 장면 전체. (영상, 시각, 종류, 상자)가 같으면 만들어 둔 파일을 그대로 씀."""
    from PIL import Image
    dst = _cut_path(name, t, box, kind)
    box = [round(float(v), 3) for v in box[:4]] if box else None
    if dst.is_file():
        return dst
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
    out = img.convert("RGBA")
    out.putalpha(full)
    tmp = dst.with_suffix(".tmp")
    out.save(tmp, "PNG")
    updater._replace(tmp, dst)
    return dst


AUTO_CUTS = 3   # 분석할 때 미리 딸 누끼 (좋은 장면 순, 사람·얼굴이 있는 장면)


def _cut_targets(items):
    """미리 딸 누끼 대상: 사람이나 얼굴이 있는 좋은 장면 3개 → [(장면, 주인공 상자 또는 None)]."""
    out = []
    for it in items:
        box = it["persons"][it["main"]][:4] if it.get("main", -1) >= 0 and it.get("persons") else None
        if box is None and not it.get("faces"):
            continue
        out.append((it, box))
        if len(out) >= AUTO_CUTS:
            break
    return out


def _cut_path(name, t, box, kind="fast"):
    import hashlib
    box = [round(float(v), 3) for v in box[:4]] if box else None
    key = hashlib.sha1(f"{core.adir(name).name}|{float(t):.3f}|{kind}|{box}".encode("utf-8")).hexdigest()[:16]
    return ASSETS / f"cut_auto_{key}.png"


def cached_analysis(name):
    """썸네일 분석(장면 후보·자동 누끼·문구)이 다 돼 있으면 바로 → dict, 아니면 None."""
    items = cached_candidates(name)
    if items is None:
        return None
    cuts = {}
    for it, box in _cut_targets(items):
        p = _cut_path(name, it["t"], box)
        if not p.is_file():
            return None
        cuts[str(it["t"])] = {"cut": asset_url(p), "src": it["url"], "box": box}
    import thumbcopy  # 문구 (가벼운 규칙 · 지연 import: thumbcopy → hooks)
    return {"frames": items, "cuts": cuts, "copy": thumbcopy.suggest(name)}


def analyze(name, log=print):
    """'AI 추천 썸네일' 분석 작업: 장면·선수 후보(v4) → 주인공 누끼 3장(빠른 모델) → 문구 후보. 누끼를 못 따도 나머지는 그대로."""
    items = frame_candidates(name)
    cuts = {}
    todo = _cut_targets(items)
    for i, (it, box) in enumerate(todo):
        core.set_progress(label="썸네일 분석", item=name, pct=int(i * 100 / max(1, len(todo))), detail=f"주인공 누끼 따는 중 {i + 1}/{len(todo)}")
        try:
            p = cut_auto(name, it["t"], box)
        except Exception as e:  # 모델을 못 받는 등 — 누끼 없는 추천으로 계속
            log(f"  자동 누끼를 따지 못했어요 · {e}")
            break
        cuts[str(it["t"])] = {"cut": asset_url(p), "src": it["url"], "box": box}
    import thumbcopy
    core.set_progress(label="썸네일 분석", item=name, pct=99, detail="제목 문구 만드는 중")
    return {"frames": items, "cuts": cuts, "copy": thumbcopy.suggest(name)}


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
                 "font": "Black Han Sans", "series": "풋사관 강좌", "seriesOn": False, "handle": "@풋살사관학교", "apply": True}
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
    for k in ("seriesOn", "apply"):
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
