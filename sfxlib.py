"""효과음·배경음악 (MSG 자동 편집용, D-024).

- 실어 둔 효과음: 저장소 `sfx/` 의 Kenney CC0 소리 (출처는 sfx/LICENSE.txt)
- PC에서 만드는 효과음: 휙·띠용·바둠츠·띠로리·두둥·두구두구·찰칵·라이저·팡·박수 (numpy · 정해진 시드 → 늘 같은 소리)
- PC에서 만드는 배경음악: 분위기 5가지(신남·잔잔·경쾌·감성·코믹) · 이어 붙여도 이음새가 없는 마디 묶음
처음 쓸 때 편집실 미디어 폴더(edit_media)에 `효과음_<이름>.wav` · `배경음악_<분위기>_<시드>.wav` 로 만들어 둔다
(편집실 파일 주소·내보내기가 그대로 씀 · 인터넷을 쓰지 않음).
"""
import io
import math
import os
import subprocess
import threading
import time
import wave
from pathlib import Path

import core

SR = 48000
SHIP = core.APP_DIR / "sfx"
PEAK_DB = -3.0          # 효과음·배경음악 최대 크기 (dBFS)
BGM_RMS_DB = -21.0      # 배경음악 평균 크기 (dBFS · 대략 -20 LUFS) — 편집본에서 레벨로 다시 맞춤
_LOCK = threading.Lock()

# 효과음 목록: 이름 → (실은 파일 | 만드는 함수 이름, 쓰임 설명)
CATALOG = {
    "딩동": ("dingdong_1.ogg", "성공·정답"),
    "딩동2": ("dingdong_2.ogg", "성공·정답 (다른 소리)"),
    "뽁": ("pop_1.ogg", "자막이 톡 튀어나올 때"),
    "뽁2": ("pop_2.ogg", "자막이 톡 튀어나올 때 (다른 소리)"),
    "틱": ("tick_1.ogg", "숫자 세기"),
    "틱2": ("tick_2.ogg", "숫자 세기 (다른 소리)"),
    "삑": ("beep_error.ogg", "틀림·실수"),
    "지지직": ("glitch.ogg", "멈춤·되감기"),
    "긁기": ("scratch.ogg", "레코드 긁는 소리"),
    "물음표": ("question.ogg", "질문"),
    "딸깍": ("click.ogg", "화면 바뀜"),
    "뻥": ("kick_1.ogg", "공 차는 소리"),
    "뻥2": ("kick_2.ogg", "공 차는 소리 (다른 소리)"),
    "퍽": ("thud.ogg", "부딪힘"),
    "쿵": ("bell.ogg", "묵직한 강조"),
    "레벨업": ("levelup_1.ogg", "잘했을 때"),
    "레벨업2": ("levelup_2.ogg", "잘했을 때 (다른 소리)"),
    "슝": ("phase.ogg", "빠르게 지나감"),
    "짠": ("sting_hit_1.ogg", "제목 등장"),
    "짠2": ("sting_hit_2.ogg", "제목 등장 (다른 소리)"),
    "짠3": ("sting_hit_3.ogg", "제목 등장 (다른 소리)"),
    "경쾌 짧은 음악": ("sting_pizz_1.ogg", "경쾌한 마무리"),
    "경쾌 짧은 음악2": ("sting_pizz_2.ogg", "경쾌한 마무리 (다른 소리)"),
    "경쾌 짧은 음악3": ("sting_pizz_3.ogg", "경쾌한 마무리 (다른 소리)"),
    "맑은 짧은 음악": ("sting_steel_1.ogg", "맑은 마무리"),
    "맑은 짧은 음악2": ("sting_steel_2.ogg", "맑은 마무리 (다른 소리)"),
    "맑은 짧은 음악3": ("sting_steel_3.ogg", "맑은 마무리 (다른 소리)"),
    # ---- PC에서 만드는 소리 ----
    "휙": ("@whoosh", "장면이 휙 넘어갈 때"),
    "띠용": ("@boing", "황당·놀람"),
    "바둠츠": ("@rimshot", "농담 뒤"),
    "띠로리": ("@sad", "실패·아쉬움"),
    "두둥": ("@dudung", "놀람·긴장"),
    "두구두구": ("@drumroll", "결과 발표 전"),
    "찰칵": ("@shutter", "정지 화면"),
    "라이저": ("@riser", "점점 고조"),
    "팡": ("@pang", "작은 강조"),
    "박수": ("@claps", "박수"),
}
MOODS = {  # 배경음악 분위기: 빠르기(bpm), 장조/단조, 설명
    "신남": (120, "major", "인트로·하이라이트용 신나는 음악"),
    "잔잔": (88, "major", "설명 장면 밑에 까는 잔잔한 음악"),
    "경쾌": (110, "major", "시범·연습 장면용 경쾌한 음악"),
    "감성": (72, "minor", "감성·다큐 느낌 음악"),
    "코믹": (132, "major", "웃긴 장면 짧은 음악 (2초)"),
}


def _np():
    import numpy as np
    return np


# ---------- 파일 쓰기 ----------

def _wav_bytes(x):
    """float(-1~1) [n, 2] → 16비트 스테레오 WAV 바이트 (늘 같은 바이트)."""
    np = _np()
    y = np.clip(np.round(np.asarray(x, np.float64) * 32767.0), -32767, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(y.tobytes())
    return buf.getvalue()


def _write_atomic(path, data):
    tmp = path.with_name(path.name + f".{os.getpid()}_{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    for k in range(10):  # Windows: 백신·OneDrive 가 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def _finish(x, peak_db=PEAK_DB, stereo=True):
    """직류 빼기 → 끝 2ms 페이드 → 최대 크기 peak_db → [n, 2]."""
    np = _np()
    x = np.asarray(x, np.float64)
    if x.ndim == 1:
        x = x - x.mean()
        x = np.stack([x, x], axis=1) if stereo else x[:, None]
    else:
        x = x - x.mean(axis=0)
    n = len(x)
    f = min(n // 4, int(0.002 * SR))
    if f > 0:
        ramp = np.linspace(0, 1, f)
        x[:f] *= ramp[:, None]
        x[-f:] *= ramp[::-1, None]
    pk = float(np.abs(x).max()) if n else 0.0
    if pk > 1e-9:
        x = x * (10 ** (peak_db / 20) / pk)
    return x


# ---------- PC에서 만드는 효과음 (numpy) ----------

def _t(sec):
    np = _np()
    return np.arange(int(round(sec * SR))) / SR


def _noise(rng, n):
    return rng.standard_normal(n)


def _fast(n):
    """FFT 가 빠른 길이 (2의 거듭제곱 · 큰 소수가 든 길이는 수십 배 느림)."""
    return 1 << max(1, int(math.ceil(math.log2(max(2, n)))))


def _band(x, lo, hi):
    """FFT 로 [lo, hi] Hz 만 남기기 (부드러운 가장자리)."""
    np = _np()
    n0 = len(x)
    n = _fast(n0)
    X = np.fft.rfft(x, n)
    f = np.fft.rfftfreq(n, 1 / SR)
    w = np.ones_like(f)
    if lo > 0:
        w *= 1 / (1 + (lo / np.maximum(f, 1e-3)) ** 4)
    if hi < SR / 2:
        w *= 1 / (1 + (f / hi) ** 4)
    return np.fft.irfft(X * w, n)[:n0]


def _sweep_band(x, f0, f1, q=2.5, steps=24):
    """가운데 주파수가 f0 → f1 로 움직이는 띠 필터 (짧은 조각마다 FFT 띠 · 겹쳐 이어 붙임)."""
    np = _np()
    n = len(x)
    out = np.zeros(n)
    hop = max(1, n // steps)
    win = np.hanning(2 * hop)
    for k in range(steps + 1):
        a = max(0, k * hop - hop)
        seg = x[a:a + 2 * hop]
        if len(seg) < 8:
            continue
        fc = f0 * (f1 / f0) ** min(1.0, k / steps)
        y = _band(seg, fc / q, fc * q) * win[:len(seg)]
        out[a:a + len(seg)] += y
    return out


def _env(t, a, d):
    """올라가는 시간 a · 지수로 줄어드는 시간 d."""
    np = _np()
    return np.minimum(1.0, t / max(a, 1e-4)) * np.exp(-np.maximum(0.0, t - a) / max(d, 1e-4))


def _whoosh(rng):
    np = _np()
    t = _t(0.42)
    x = _sweep_band(_noise(rng, len(t)), 400, 4000)
    env = np.sin(np.pi * np.clip(t / t[-1], 0, 1)) ** 1.6
    pan = np.clip(t / t[-1], 0, 1)
    m = x * env
    return np.stack([m * (1.1 - 0.6 * pan), m * (0.5 + 0.6 * pan)], axis=1)


def _boing(rng):
    np = _np()
    t = _t(0.55)
    f = 220 * (1 + 0.9 * np.exp(-t * 9)) * (1 + 0.06 * np.sin(2 * np.pi * 12 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    return (np.sin(ph) + 0.25 * np.sin(2 * ph)) * _env(t, 0.005, 0.22)


def _tom(t, f0):
    np = _np()
    f = f0 * (1 + 0.5 * np.exp(-t * 25))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t, 0.002, 0.12)


def _rimshot(rng):
    np = _np()
    out = np.zeros(int(1.1 * SR))
    for at, f0 in ((0.0, 180), (0.16, 140)):
        tt = _t(0.4)
        a = int(at * SR)
        out[a:a + len(tt)] += _tom(tt, f0)
    tc = _t(0.75)
    cym = _band(_noise(rng, len(tc)), 5000, 16000) * _env(tc, 0.002, 0.22) * 0.9
    a = int(0.32 * SR)
    out[a:a + len(tc)] += cym + 0.7 * _tom(tc, 160) * np.exp(-tc * 8)
    return out


def _saw(ph, n_harm=18, bright=1.0):
    np = _np()
    out = np.zeros_like(ph)
    for k in range(1, n_harm + 1):
        out += np.sin(k * ph) / k * (bright ** (k - 1))
    return out


def _sad(rng):
    np = _np()
    notes = ((233.08, 0.34), (220.0, 0.34), (207.65, 0.34), (196.0, 1.0))
    parts = []
    for k, (f0, d) in enumerate(notes):
        t = _t(d)
        vib = 1 + (0.02 * np.sin(2 * np.pi * 6 * t) * np.clip((t - 0.15) / 0.2, 0, 1) if k == 3 else 0)
        ph = 2 * np.pi * np.cumsum(f0 * vib) / SR
        wah = 0.55 + 0.35 * np.sin(np.pi * np.clip(t / d, 0, 1))  # '와와' 닫혔다 열림
        x = np.zeros_like(t)
        for h in range(1, 14):
            x += np.sin(h * ph) / h * (wah ** (h - 1))
        x *= _env(t, 0.02, d * (0.8 if k < 3 else 0.6)) * np.minimum(1, (d - t) / 0.03)
        parts.append(x)
    return np.concatenate(parts)


def _dudung(rng):
    np = _np()
    out = np.zeros(int(1.7 * SR))
    tr = _t(0.28)
    riser = _band(_noise(rng, len(tr)), 1500, 9000) * (tr / tr[-1]) ** 2 * 0.5
    out[:len(tr)] += riser
    for at in (0.3, 0.72):
        tb = _t(0.9)
        f = 55 * (1 + 0.8 * np.exp(-tb * 18))
        boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(tb, 0.003, 0.32)
        boom += 0.3 * np.tanh(3 * boom)
        a = int(at * SR)
        out[a:a + len(tb)] += boom[:len(out) - a]
    return out


def _snare(rng, sec=0.12, d=0.035):
    np = _np()
    t = _t(sec)
    body = np.sin(2 * np.pi * 190 * t) * _env(t, 0.001, 0.03) * 0.5
    return (_band(_noise(rng, len(t)), 1200, 9000) * _env(t, 0.001, d) + body)


def _drumroll(rng):
    np = _np()
    n = int(1.6 * SR)
    out = np.zeros(n)
    t, k = 0.0, 0
    while t < 1.45:
        rate = 10 + 16 * (t / 1.45)
        hit = _snare(rng) * (0.25 + 0.75 * (t / 1.45)) * (0.85 + 0.15 * rng.random())
        a = int(t * SR)
        out[a:a + len(hit)] += hit[:n - a]
        t += 1 / rate
        k += 1
    return out


def _shutter(rng):
    np = _np()
    out = np.zeros(int(0.22 * SR))
    for at, f in ((0.0, 3500), (0.06, 2600)):
        tc = _t(0.03)
        c = _band(_noise(rng, len(tc)), f * 0.5, f * 1.6) * _env(tc, 0.002, 0.008)  # 2ms 로 열고 너무 높은 소리는 뺌 (AAC 에서 튀지 않게)
        a = int(at * SR)
        out[a:a + len(tc)] += c
    tm = _t(0.1)
    out[int(0.01 * SR):int(0.01 * SR) + len(tm)] += _band(_noise(rng, len(tm)), 300, 1500) * _env(tm, 0.002, 0.02) * 0.3
    return out


def _riser(rng):
    np = _np()
    t = _t(1.25)
    f = 200 * (10 ** (t / t[-1]))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = 0.35 * _saw(ph, 8, 0.8) + 0.6 * _sweep_band(_noise(rng, len(t)), 500, 6000, q=2.0)
    return x * (t / t[-1]) ** 1.8 * np.minimum(1, (t[-1] - t) / 0.02 + 0.0)


def _pang(rng):
    np = _np()
    t = _t(0.09)
    f = 1000 * (0.3 ** (t / t[-1]))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t, 0.001, 0.03)


def _claps(rng):
    np = _np()
    n = int(1.8 * SR)
    out = np.zeros((n, 2))
    for _ in range(26):
        at = rng.random() * 1.4
        tc = _t(0.08)
        c = _band(_noise(rng, len(tc)), 800 + 600 * rng.random(), 5000) * _env(tc, 0.001, 0.012) * (0.4 + 0.6 * rng.random())
        a = int(at * SR)
        pan = 0.2 + 0.6 * rng.random()  # 박수는 여러 사람 → 좌우로 퍼지게
        m = min(len(tc), n - a)
        out[a:a + m, 0] += c[:m] * (1 - pan)
        out[a:a + m, 1] += c[:m] * pan
    return out * np.minimum(1, (n - np.arange(n)) / (0.3 * SR))[:, None]


_GEN = {"whoosh": _whoosh, "boing": _boing, "rimshot": _rimshot, "sad": _sad, "dudung": _dudung, "drumroll": _drumroll,
        "shutter": _shutter, "riser": _riser, "pang": _pang, "claps": _claps}


SOFTER = {"shutter": -5.0}  # 아주 짧은 딸깍 소리는 크게 들려 조금 작게 (소리 크기 맞추기에서 리미터가 세게 누르지 않게)


def synth(kind, seed=7):
    """만드는 효과음 하나 → WAV 바이트 (같은 kind·seed 면 늘 같은 바이트)."""
    np = _np()
    rng = np.random.default_rng(seed)
    return _wav_bytes(_finish(_GEN[kind](rng), PEAK_DB + SOFTER.get(kind, 0.0)))


# ---------- 효과음 준비 ----------

def sfx_file_name(name):
    return f"효과음_{name}.wav"


def ensure_sfx(name, assets):
    """효과음 하나를 편집실 미디어 폴더에 준비 → 파일 이름. 실은 소리는 WAV 로 바꿔 둠 (모든 PC에서 미리 듣기가 되게)."""
    if name not in CATALOG:
        raise KeyError(f"모르는 효과음이에요 · {name}")
    assets = Path(assets)
    assets.mkdir(parents=True, exist_ok=True)
    out = assets / sfx_file_name(name)
    if out.exists() and out.stat().st_size > 44:
        return out.name
    src = CATALOG[name][0]
    with _LOCK:
        if out.exists() and out.stat().st_size > 44:
            return out.name
        if src.startswith("@"):
            _write_atomic(out, synth(src[1:]))
        else:
            p = SHIP / src
            if not p.is_file():
                raise FileNotFoundError(f"효과음 파일이 없어요 · sfx/{src}")
            pr = subprocess.run([core.ffmpeg(), "-v", "error", "-i", str(p), "-ac", "2", "-ar", str(SR), "-f", "s16le", "-"],
                                capture_output=True, **core.NO_WINDOW)
            if pr.returncode or not pr.stdout:
                raise RuntimeError(f"효과음을 준비하지 못했어요 · {name}")
            data = pr.stdout
            np = _np()
            x = np.frombuffer(data[:len(data) // 4 * 4], "<i2").reshape(-1, 2).astype(np.float64) / 32767.0
            _write_atomic(out, _wav_bytes(x))
    return out.name


def ensure_assets(names, assets):
    """여러 효과음 → {이름: 파일 이름}."""
    return {n: ensure_sfx(n, assets) for n in dict.fromkeys(names)}


# ---------- 배경음악 (PC에서 만듦) ----------

PROGS = {  # 화음 진행 (도수: 0=I … 장조 / 단조 따로)
    "major": [[0, 4, 5, 3], [0, 5, 3, 4], [5, 3, 0, 4], [0, 3, 5, 4], [3, 4, 0, 5]],
    "minor": [[0, 5, 2, 6], [0, 3, 6, 2], [5, 6, 0, 0], [0, 6, 5, 4]],
}
SCALE = {"major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10]}


def _hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _chord(key, mode, deg):
    sc = SCALE[mode]
    return [key + sc[(deg + k) % 7] + 12 * ((deg + k) // 7) for k in (0, 2, 4, 6)]  # 7화음 (네 번째 음은 약하게)


def _add(buf, a, x):
    n = len(buf)
    if a >= n:
        return
    if a < 0:
        x, a = x[-a:], 0
    m = min(len(x), n - a)
    buf[a:a + m] += x[:m]


def _note(f, sec, kind, vel=1.0):
    """악기 한 음 (덧셈 합성): pluck · marimba · piano · bass · pad · bell."""
    np = _np()
    t = _t(sec)
    ph = 2 * np.pi * f * t
    if kind == "pluck":
        x = (np.sin(ph) + 0.45 * np.sin(2 * ph) * np.exp(-t * 9) + 0.2 * np.sin(3 * ph) * np.exp(-t * 14)) * _env(t, 0.003, 0.18)
    elif kind == "marimba":
        x = (np.sin(ph) + 0.35 * np.sin(4.0 * ph) * np.exp(-t * 30) + 0.12 * np.sin(9.2 * ph) * np.exp(-t * 60)) * _env(t, 0.002, 0.14)
    elif kind == "piano":
        x = sum(np.sin(h * ph * (1 + 0.0004 * h * h)) / h ** 1.3 * np.exp(-t * (1.6 + 0.9 * h)) for h in range(1, 7)) * _env(t, 0.004, 1.2)
    elif kind == "bass":
        x = (np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.08 * np.sin(3 * ph)) * _env(t, 0.006, 0.35) * np.minimum(1, (sec - t) / 0.02)
    elif kind == "bell":
        x = (np.sin(ph) + 0.5 * np.sin(2.76 * ph) * np.exp(-t * 4) + 0.25 * np.sin(5.4 * ph) * np.exp(-t * 8)) * _env(t, 0.002, 0.6)
    else:  # pad: 살짝 어긋난 두 음 + 느린 올라감/내려감
        x = (np.sin(ph) + np.sin(ph * 1.004 + 1.0) + 0.3 * np.sin(2 * ph * 0.998) + 0.12 * np.sin(3 * ph)) * 0.5
        x *= np.minimum(1, t / 0.35) * np.minimum(1, (sec - t) / 0.45)
    return x * vel


def _kick(sec=0.32, punch=1.0):
    np = _np()
    t = _t(sec)
    f = 48 + 90 * np.exp(-t * 32)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t, 0.001, 0.11 * punch)


def _hat(rng, sec=0.05, d=0.012):
    t = _t(sec)
    return _band(_noise(rng, len(t)), 7000, 18000) * _env(t, 0.0005, d)


def _clap(rng):
    np = _np()
    t = _t(0.18)
    x = _band(_noise(rng, len(t)), 900, 7000)
    e = sum(_env(np.maximum(0, t - k * 0.011), 0.0005, 0.006) * (t >= k * 0.011) for k in range(3)) + 0.6 * _env(np.maximum(0, t - 0.033), 0.001, 0.05) * (t >= 0.033)
    return x * e


def _shaker(rng):
    t = _t(0.07)
    return _band(_noise(rng, len(t)), 4000, 14000) * _env(t, 0.01, 0.02)


def _lowpass(x, fc):
    """FFT 저역 통과 (부드럽게) — 패드·베이스를 둥글게."""
    np = _np()
    n0 = len(x)
    n = _fast(n0)
    X = np.fft.rfft(x, n, axis=0)
    f = np.fft.rfftfreq(n, 1 / SR)
    w = 1 / (1 + (f / fc) ** 4)
    return np.fft.irfft(X * (w[:, None] if X.ndim == 2 else w), n, axis=0)[:n0]


def _reverb(x, rng, mix=0.18, sec=1.4):
    """짧은 잔향 (지수로 줄어드는 잡음 응답과 FFT 합성곱) — 끝은 앞으로 감아 이음새를 없앰 (이어 붙이기용)."""
    np = _np()
    n = len(x)
    L = int(sec * SR)
    t = np.arange(L) / SR
    out = np.zeros_like(x)
    for c in range(x.shape[1]):
        ir = rng.standard_normal(L) * np.exp(-t * 4.5) * (t > 0.012)
        ir = _band(ir, 300, 9000)
        ir /= np.sqrt((ir ** 2).sum()) + 1e-9
        m = 1 << int(math.ceil(math.log2(n + L)))
        y = np.fft.irfft(np.fft.rfft(x[:, c], m) * np.fft.rfft(ir, m), m)[:n + L]
        y[:L] += y[n:n + L]  # 고리처럼 감음 → 끝에서 처음으로 이어 붙여도 잔향이 끊기지 않음
        out[:, c] = y[:n]
    return x + mix * out


def bgm_render(mood, seed=1, bars=16):
    """배경음악 한 묶음 (bars 마디 · 처음과 끝이 이어지게) → float [n, 2]."""
    np = _np()
    bpm, mode, _ = MOODS[mood]
    rng = np.random.default_rng(seed * 1009 + sum(map(ord, mood)))
    if mood == "코믹":
        return _comic(rng)
    beat = 60.0 / bpm
    bar = 4 * beat
    n = int(round(bars * bar * SR))
    key = 48 + int(rng.integers(0, 7)) * (1 if mode == "major" else 1) % 12 + (2 if mood == "감성" else 0)
    prog = PROGS[mode][int(rng.integers(0, len(PROGS[mode])))]
    prog_b = PROGS[mode][int(rng.integers(0, len(PROGS[mode])))]
    L, R = np.zeros(n), np.zeros(n)
    pad, bass, lead, drums = np.zeros(n), np.zeros(n), np.zeros((n, 2)), np.zeros(n)
    arp_pat = [[0, 1, 2, 3, 2, 1, 0, 1], [0, 2, 1, 3, 0, 2, 1, 3], [0, 1, 2, 1, 3, 2, 1, 2]][int(rng.integers(0, 3))]
    for b in range(bars):
        part_b = (b // 8) % 2 == 1  # 앞 8마디 / 뒤 8마디는 진행을 바꿔 덜 반복적으로
        deg = (prog_b if part_b else prog)[b % 4]
        ch = _chord(key, mode, deg)
        a = int(round(b * bar * SR))
        # 패드 (마디 내내)
        if mood in ("잔잔", "감성", "신남"):
            for k, m in enumerate(ch[:3] + ([ch[3]] if mood != "신남" else [])):
                _add(pad, a, _note(_hz(m + 12), bar + 0.05, "pad", 0.16 if k < 3 else 0.08))
        # 베이스
        root = _hz(ch[0] - 12)
        if mood == "신남":
            for k in range(8):
                _add(bass, a + int(k * beat / 2 * SR), _note(root * (2 if k % 4 == 3 else 1), beat / 2 * 0.9, "bass", 0.5))
        elif mood == "경쾌":
            for k, (o, v) in enumerate(((0, 0.55), (1.5, 0.4), (2, 0.5), (3, 0.35))):
                _add(bass, a + int(o * beat * SR), _note(root * (1.5 if k == 3 else 1), beat * 0.45, "bass", v))
        elif mood == "잔잔":
            _add(bass, a, _note(root, beat * 2.8, "bass", 0.38))
            _add(bass, a + int(2.5 * beat * SR), _note(root, beat * 1.4, "bass", 0.25))
        else:  # 감성
            _add(bass, a, _note(root, bar * 0.95, "bass", 0.3))
        # 멜로디 악기 (아르페지오)
        inst = {"신남": "pluck", "잔잔": "pluck", "경쾌": "marimba", "감성": "piano"}[mood]
        steps = {"신남": 16, "잔잔": 8, "경쾌": 8, "감성": 4}[mood]
        for k in range(steps):
            if mood == "잔잔" and k % 4 == 3 and rng.random() < 0.5:
                continue
            m = ch[arp_pat[k % len(arp_pat)] % 4] + (24 if mood != "감성" else 12) - (12 if mood == "경쾌" and k % 2 else 0)
            v = (0.22 if k % 4 == 0 else 0.15) * (0.9 + 0.2 * rng.random())
            x = _note(_hz(m), {"감성": 2.4, "잔잔": 0.9}.get(mood, 0.5), inst, v * (1.6 if mood == "감성" else 1.0))
            p = 0.5 + 0.3 * math.sin(k * 1.7 + b)
            off = a + int(k * bar / steps * SR)
            _add(lead[:, 0], off, x * (1.2 - p))
            _add(lead[:, 1], off, x * (0.4 + p))
        # 드럼
        if mood == "신남":
            for k in range(4):
                _add(drums, a + int(k * beat * SR), _kick() * 0.9)
                _add(drums, a + int((k + 0.5) * beat * SR), _hat(rng) * 0.35)
                if k % 2 == 1:
                    _add(drums, a + int(k * beat * SR), _clap(rng) * 0.45)
            if b % 4 == 3:  # 4마디마다 작은 꾸밈
                for k in range(4):
                    _add(drums, a + int((3 + k / 4) * beat * SR), _snare(rng) * 0.18 * (k + 1))
        elif mood == "경쾌":
            for k in range(4):
                _add(drums, a + int(k * beat * SR), _kick(0.25, 0.7) * (0.75 if k % 2 == 0 else 0.4))
                for s in (0.5,):
                    _add(drums, a + int((k + s) * beat * SR), _hat(rng) * 0.3)
                if k % 2 == 1:
                    _add(drums, a + int(k * beat * SR), _clap(rng) * 0.3)
        elif mood == "잔잔":
            _add(drums, a, _kick(0.3, 0.8) * 0.45)
            for k in range(8):
                _add(drums, a + int(k * beat / 2 * SR), _shaker(rng) * (0.18 if k % 2 else 0.1))
    pad = _lowpass(pad, 1800 if mood != "감성" else 1400)
    bass = _lowpass(bass, 600)
    if mood == "신남":  # 킥에 맞춰 패드·베이스가 살짝 숨쉬게 (사이드체인 느낌)
        t = (np.arange(n) / SR) % beat
        duck = 1 - 0.45 * np.exp(-t / 0.09)
        pad *= duck
        bass *= 0.6 + 0.4 * (1 - np.exp(-t / 0.05))
    L = pad * 0.95 + bass + lead[:, 0] + drums
    R = pad * 1.05 + bass + lead[:, 1] + drums
    x = np.stack([L, R], axis=1)
    x = _reverb(x, rng, {"감성": 0.3, "잔잔": 0.24, "경쾌": 0.14, "신남": 0.12}[mood])
    x = np.tanh(x / (np.abs(x).max() + 1e-9) * 1.4) / math.tanh(1.4)  # 부드러운 포화 (가청 크기 고르게)
    rms = float(np.sqrt((x ** 2).mean()))
    x *= 10 ** (BGM_RMS_DB / 20) / max(rms, 1e-9)
    pk = float(np.abs(x).max())
    if pk > 10 ** (PEAK_DB / 20):
        x *= 10 ** (PEAK_DB / 20) / pk
    return x - x.mean(axis=0)


def _comic(rng):
    """코믹 짧은 음악 (약 2초): 뚱땅뚱땅 내려가다 '띠용' 마무리."""
    np = _np()
    n = int(2.1 * SR)
    out = np.zeros(n)
    notes = [67, 64, 65, 62, 64, 60]
    for k, m in enumerate(notes):
        _add(out, int(k * 0.2 * SR), _note(_hz(m), 0.22, "marimba", 0.7))
        _add(out, int(k * 0.2 * SR), _note(_hz(m - 12), 0.2, "bass", 0.35))
    _add(out, int(1.25 * SR), _boing(rng) * 0.6)
    x = _finish(out, PEAK_DB)
    return x


def bgm_file_name(mood, seed):
    return f"배경음악_{mood}_{int(seed)}.wav"


def ensure_bgm(mood, seed, assets, bars=16):
    """배경음악 한 묶음을 미디어 폴더에 → (파일 이름, 길이 초). 같은 분위기·시드면 한 번만 만듦."""
    if mood not in MOODS:
        raise KeyError(f"모르는 분위기예요 · {mood}")
    assets = Path(assets)
    assets.mkdir(parents=True, exist_ok=True)
    out = assets / bgm_file_name(mood, seed)
    with _LOCK:
        if not (out.exists() and out.stat().st_size > 44):
            _write_atomic(out, _wav_bytes(bgm_render(mood, seed, bars)))
    with wave.open(str(out), "rb") as w:
        return out.name, w.getnframes() / float(w.getframerate())


def shipped_files():
    """실어 둔 효과음 파일 이름들 (LICENSE 확인용)."""
    return sorted({v[0] for v in CATALOG.values() if not v[0].startswith("@")})
