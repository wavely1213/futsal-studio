"""편집 스타일 배우기 — 잘 나가는 유튜버 영상(레퍼런스)을 분석해 '스타일 프로필'로 저장.

AI 비용 없는 규칙 기반 분석:
- 컷 리듬: 장면 전환 횟수·평균 컷 길이·컷 길이 분포
- 줌 컷(펀치인): 컷 직후 화면이 같은 장면을 확대한 것인지
- 말 사이 공백: 무음 길이 분포 → 우리 가편집에서 몇 초 이상 쉬면 자를지
- 자막: 화면에 글자가 떠 있는 비율·위치(위/가운데/아래)·주 색(흰/노랑…)
- 소리 크기(LUFS)·말 빠르기(받아쓰기가 있으면)
영상마다 살펴본 기록(스타일 이벤트)은 analysis/<영상>/style_events.json 에 남기고 (파일이 그대로면 다시 안 봄),
기록을 요약해 프로필을 만든다. 여러 영상을 배우면 기록을 이어 붙여 하나의 스타일로 합친다.
스타일 일치 점수: 배운 스타일 ↔ 자동 가편집(편집본 JSON)을 내보내지 않고 바로 비교 (profile_from_sequence · distance).
"""
import bisect
import json
import math
import os
import re
import subprocess
import threading
import time

import core

STYLES = core.WORK / "styles"
FW, FH, FPS = 320, 180, 4
TEXT_WH, TEXT_WW = 12, 96            # 자막 한 줄 높이, 화면 폭의 30% 정도 (저해상도 기준)
EV_VER = 2                           # 기록 형식이 바뀌면 올림 (예전 기록은 다시 살펴봄) · 2: 작은 흑백 화면(look) 추가
EV_FPS = 2                           # 글자 밀도·움직임은 1초에 2번만 남김
RMS_STEP = 0.1                       # 소리 세기 기록 간격(초)
LOOK_W, LOOK_H = 32, 18              # 작은 흑백 화면 (1초 2장) — 편집본의 점프 컷이 화면 분석에 컷으로 보일지 가늠
LOOK_CUT = 15.0                      # 작은 화면 기준 컷 문턱 (화면 분석의 80×45 기준 16과 같은 뜻 · 작게 줄이면 차이가 6% 정도 줄어듦)
QS = [round(0.05 + 0.1 * k, 2) for k in range(10)]  # 분포 10칸(각 칸 가운데: 5%·15%…95%) → shotDeciles·pauseDeciles
POS_BANDS = {"top": (0, 2), "middle": (2, 4), "bottom": (4, 6)}  # 가로 띠 6개 중 위치별
PARTS = ("컷 리듬", "확대", "공백", "자막", "소리")
BASE_W = {"컷 리듬": 0.3, "확대": 0.2, "공백": 0.2, "자막": 0.2, "소리": 0.1}  # 레퍼런스가 하나뿐일 때 무게
STD_FLOOR = 0.08                     # 레퍼런스끼리 똑같아도 무게가 끝없이 커지지 않게
W_CAP = 0.35                         # 한 부분이 전체 점수를 혼자 정하지 않게 (무게 상한)
CURVE_HEAD, CURVE_TAIL = 30.0, 20.0   # 컷 리듬 3구간 (#7): 도입 30초 · 마무리 20초 · 그 사이 본론
TEMPO_MAX = 1.12                     # 말 빠르기 맞추기: 최대 12% 빠르게
FEW_CUTS, RATE_FLOOR = 3, 0.5        # 일치 점수: 컷이 3번 안 되면 1분당 컷 수로 비교 · 1분에 0.5번 밑은 '컷 거의 없음'으로 같게 봄
FAIL_TTL = 6 * 3600                  # 원본 살펴보기 실패 기록은 6시간 뒤 잊음 (잠깐 잠긴 파일 등)


class StyleError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 안내 (한국어)."""


class StyleMissing(StyleError):
    """그 이름의 스타일이 없음."""


class NeedsAnalysis(Exception):
    """점수를 매기기 전에 원본 화면을 한 번 살펴봐야 함 (작업으로 돌림)."""


class StyleCancelled(StyleError):
    """멈추기(✕)를 눌러 영상 살펴보기를 그만둠."""


def _cancelled():
    """멈추기(✕) — 편집실과 같은 신호(editor.CANCEL)를 봄. 작업을 시작할 때마다 지워짐."""
    import sys
    ed = sys.modules.get("editor")
    return bool(ed is not None and getattr(ed, "CANCEL", None) is not None and ed.CANCEL.is_set())


def _probe_duration(path):
    r = core.run([core.ffmpeg(), "-hide_banner", "-i", str(path)])
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def _frames(path):
    """저해상도 RGB 프레임을 1초에 4장씩 흘려보냄 (메모리에 다 올리지 않음)."""
    import numpy as np
    cmd = [core.ffmpeg(), "-v", "error", "-i", str(path), "-vf", f"fps={FPS},scale={FW}:{FH}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    kw = getattr(core, "NO_WINDOW", {})
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kw)
    n = FW * FH * 3
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield np.frombuffer(b, np.uint8).reshape(FH, FW, 3)
    finally:
        p.kill()
        p.wait()
        p.stdout.close()


def _small(g):
    return g.reshape(45, 4, 80, 4).mean(axis=(1, 3))


def _zoom_score(a, b):
    """b가 a를 확대한 화면인지: (최소 오차, 확대 배율, 가로·세로 치우침). 반 해상도로 넓게 찾고, 후보 몇 개를 원 해상도에서 촘촘히 다듬음."""
    import numpy as np
    from PIL import Image

    def mk(a, b, W, H):
        ia = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

        def err(s, ox, oy):
            cw, ch = W / s, H / s
            x0, y0 = (W - cw) / 2 + ox * W / 2, (H - ch) / 2 + oy * H / 2
            if x0 < -1e-6 or y0 < -1e-6 or x0 + cw > W + 1e-6 or y0 + ch > H + 1e-6:
                return 1e9
            z = np.asarray(ia.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch)), np.float32)
            return float(np.abs(z - b).mean())
        return err

    half = lambda g: g.reshape(FH // 2, 2, FW // 2, 2).mean(axis=(1, 3))
    errh, errf = mk(half(a), half(b), FW // 2, FH // 2), mk(a, b, FW, FH)
    S = (1.06, 1.1, 1.14, 1.18, 1.22, 1.27, 1.33, 1.4, 1.48, 1.56)
    O = (-0.15, -0.1, -0.05, 0, 0.05, 0.1, 0.15)
    cand = sorted((errh(s, ox, oy), s, ox, oy) for s in S for ox in O for oy in (-0.1, -0.05, 0, 0.05, 0.1))[:3]
    res = []
    for _, s0, ox0, oy0 in cand:
        best = (errf(s0, ox0, oy0), s0, ox0, oy0)
        for st in (0.02, 0.01, 0.005):
            _, s0, ox0, oy0 = best
            best = min([best] + [(errf(s0 + ds, ox0 + dx, oy0 + dy), s0 + ds, ox0 + dx, oy0 + dy)
                                 for ds in (-st, 0, st) for dx in (-st, 0, st) for dy in (-st, 0, st)])
        res.append(best)
    best = min(res)
    return best[0], round(best[1], 2), round(best[2], 3), round(best[3], 3)


def _text_bands(rgb):
    """가로 띠 6개마다 '글자 같은 강한 세로 경계'가 가장 촘촘한 작은 창의 밀도 (짧은 자막도 잡히게).
    ys·dens: 줄(위치)마다 가장 촘촘한 창의 밀도 → 자막 색을 글자 줄에서 뽑을 때 씀."""
    import numpy as np
    g = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    strong = np.abs(np.diff(g, axis=1)) > 70
    WH, WW = TEXT_WH, TEXT_WW
    ii = np.zeros((FH + 1, FW), np.int32)
    ii[1:, 1:] = strong.cumsum(0).cumsum(1)
    ys = np.arange(0, FH - WH + 1, 3)
    xs = np.arange(0, FW - WW, 16)
    a, b = ii[ys][:, xs], ii[ys][:, xs + WW]
    c, d = ii[ys + WH][:, xs], ii[ys + WH][:, xs + WW]
    dens = (d - c - b + a).max(axis=1) / (WH * WW)
    ctr = ys + WH / 2
    bands = [float(dens[(ctr >= i * 30) & (ctr < (i + 1) * 30)].max()) for i in range(6)]
    return g, bands, strong, ys, dens


def _cap_colors(rgb, bands, strong, ys, dens):
    """글자가 있어 보이는 위치(위/가운데/아래)마다, 그 위치에서 글자가 가장 촘촘한 줄의 밝은 획 색 (위치, [R,G,B])."""
    import numpy as np
    out, ctr = [], ys + TEXT_WH / 2
    for pos, (a, b) in POS_BANDS.items():
        if max(bands[a:b]) <= 0.10:
            continue
        m = (ctr >= a * 30) & (ctr < b * 30)
        y0 = int(ys[m][int(np.argmax(dens[m]))])
        rows, st = rgb[y0:y0 + TEXT_WH], strong[y0:y0 + TEXT_WH]
        px = np.concatenate([rows[:, :-1][st], rows[:, 1:][st]])  # 경계 양쪽 중 밝은 쪽이 글자 획
        br = px[px.max(axis=1) > 170]
        if len(br) > 20:
            out.append((pos, [int(round(x)) for x in np.median(br, axis=0)]))
    return out


def _snap(rgb):
    """뽑은 색 → 자막 색 (노랑·흰색은 대표 색으로).
    노랑은 파랑이 빨강·초록보다 확실히 적으면: 가는 글자 획은 화질·색 압축(4:2:0)으로 색이 묽어져 파랑이 120 넘게 떠도 노랑."""
    r, g, b = (float(x) for x in rgb)
    return ("#FFE14D" if (r > 170 and g > 150 and (b < 120 or b < 0.75 * min(r, g))) else "#FFFFFF" if min(r, g, b) > 170
            else "#%02X%02X%02X" % (int(r), int(g), int(b)))


# ---------- 스타일 이벤트 기록 (영상 하나를 살펴본 결과 · 다시 쓰기) ----------

def _sig(path):
    s = os.stat(path)
    return [s.st_size, s.st_mtime_ns]


def _events_file(name):
    return core.adir(name) / "style_events.json"


def _load_events(name, path=None):
    """저장해 둔 기록 — 영상 파일(크기·수정 시각)이 그대로일 때만. 아니면 None."""
    try:
        ev = json.loads(_events_file(name).read_text(encoding="utf-8"))
        if isinstance(ev, dict) and ev.get("v") == EV_VER and ev.get("sig") == _sig(path or core.VIDEOS / name):
            return ev
    except (OSError, ValueError):
        pass
    return None


def _save_events(name, ev):
    f = _events_file(name)
    tmp = f.with_name(f"{f.name}.{os.getpid()}_{threading.get_ident()}.tmp")
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(ev, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for k in range(20):  # Windows: 백신·탐색기가 잠깐 잡고 있으면 조금 뒤 다시
            try:
                os.replace(tmp, f)
                return
            except PermissionError:
                if k == 19:
                    raise
                time.sleep(0.05)
    except OSError:  # 기록을 못 남겨도 배우기는 계속 (다음에 다시 살펴볼 뿐)
        try:
            tmp.unlink()
        except OSError:
            pass


def _speech(name):
    """받아쓰기가 있으면 말한 시간·글자 수 (말 빠르기용)."""
    try:
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        return {"talk": sum(max(0.0, s["end"] - s["start"]) for s in segs), "chars": sum(len(s["text"].replace(" ", "")) for s in segs)}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _audio_events(path):
    """말 사이 공백(0.15초 넘는 무음)·소리 크기(LUFS)·0.1초마다 소리 세기(dBFS) — 소리를 한 번만 풀어서."""
    import numpy as np
    sr = 8000
    step = int(sr * RMS_STEP)

    def once(eb):
        cmd = [core.ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", f"silencedetect=noise=-32dB:d=0.15,{eb}",
               "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
        err = []
        th = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
        th.start()
        rms, buf = [], b""
        try:
            while True:
                b = p.stdout.read(step * 2 * 50)
                if not b:
                    break
                buf += b
                n = len(buf) // (step * 2) * step * 2
                if n:
                    x = np.frombuffer(buf[:n], np.int16).astype(np.float32).reshape(-1, step)
                    rms += np.sqrt((x * x).mean(axis=1)).tolist()
                    buf = buf[n:]
            if len(buf) >= 2:
                x = np.frombuffer(buf[:len(buf) // 2 * 2], np.int16).astype(np.float32)
                rms.append(float(np.sqrt((x * x).mean())))
            p.wait()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            th.join(5)
            p.stdout.close()
            p.stderr.close()
        return p.returncode, (err[0] if err else b"").decode("utf-8", "replace"), rms

    code, text, rms = once("ebur128=framelog=quiet")
    if code and "framelog" in text:  # 아주 예전 ffmpeg: 구간별 기록 끄는 옵션이 없음
        code, text, rms = once("ebur128")
    sil, start = [], None
    for ln in text.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", ln)
        if m:
            start = max(0.0, float(m[1]))
        m = re.search(r"silence_end: (-?[\d.]+)", ln)
        if m and start is not None:
            sil.append([round(start, 3), round(float(m[1]), 3)])
            start = None
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", text)
    db = [max(-90, int(round(20 * math.log10(max(v, 1e-6) / 32768)))) for v in rms]
    return {"silences": sil, "lufs": float(m[-1]) if m else None, "rms": db}


def _pack_looks(looks):
    import base64
    import zlib
    import numpy as np
    raw = np.clip(np.asarray(looks, np.float32), 0, 255).astype(np.uint8).tobytes() if looks else b""
    return {"w": LOOK_W, "h": LOOK_H, "data": base64.b64encode(zlib.compress(raw, 6)).decode("ascii")}


def _looks(ev):
    """기록 속 작은 흑백 화면들 → (장 수, 18, 32) 배열. 없거나 깨졌으면 None."""
    import base64
    import zlib
    import numpy as np
    lk = (ev or {}).get("look")
    try:
        if not lk or (lk.get("w"), lk.get("h")) != (LOOK_W, LOOK_H):
            return None
        a = np.frombuffer(zlib.decompress(base64.b64decode(lk["data"])), np.uint8)
        return a.reshape(-1, LOOK_H, LOOK_W).astype(np.float32) if a.size and a.size % (LOOK_W * LOOK_H) == 0 else None
    except (ValueError, TypeError, KeyError, zlib.error):
        return None


def _render_look(L, s, px=0.5, py=0.5):
    """작은 화면에 편집실의 크기(s)·위치(px,py) 적용 — 내보낸 화면을 작게 줄인 것과 거의 같음 (밖은 검정)."""
    import numpy as np
    h, w = L.shape
    if abs(s - 1) < 1e-6 and abs(px - 0.5) < 1e-6 and abs(py - 0.5) < 1e-6:
        return L
    s = max(s, 1e-3)
    u = (0.5 + ((np.arange(w) + 0.5) / w - px) / s) * w - 0.5
    v = (0.5 + ((np.arange(h) + 0.5) / h - py) / s) * h - 0.5
    x0, y0 = np.floor(u), np.floor(v)
    fx, fy = u - x0, v - y0
    xa, xb = np.clip(x0.astype(int), 0, w - 1), np.clip(x0.astype(int) + 1, 0, w - 1)
    ya, yb = np.clip(y0.astype(int), 0, h - 1), np.clip(y0.astype(int) + 1, 0, h - 1)
    top = L[ya][:, xa] * (1 - fx) + L[ya][:, xb] * fx
    bot = L[yb][:, xa] * (1 - fx) + L[yb][:, xb] * fx
    out = top * (1 - fy)[:, None] + bot * fy[:, None]
    inside = ((u > -0.5 - 1e-6) & (u < w - 0.5 + 1e-6))[None, :] & ((v > -0.5 - 1e-6) & (v < h - 0.5 + 1e-6))[:, None]
    return np.where(inside, out, 0.0)


def _look_index(n, cuts, m, before):
    """원본 m 초(내용 시각)에 해당하는 작은 화면 번호 (1초 2장 · n 장).
    화면 분석의 장면 칸은 내용보다 1/8초쯤 앞선 시각으로 적히므로 그만큼 맞추고, 원본 속 장면 전환(cuts)을 건너지 않는 칸으로.
    before: m 까지 보이던 화면(클립의 끝) · 아니면 m 부터 보이는 화면(클립의 시작)."""
    # 화면 분석 칸(1초 4칸) — 칸 L 에는 L+1/8초까지의 마지막 장면이 보임: 끝 화면은 m 전의 마지막 칸, 시작 화면은 m 이 처음 보이는 칸
    x = (float(m) - 0.5 / FPS) * FPS
    lab = (math.floor(x + 1e-6) if before else math.ceil(x - 1e-6)) / FPS
    j = bisect.bisect_right(cuts, lab + 1e-6)
    lo, hi = (cuts[j - 1] if j else -1e9), (cuts[j] if j < len(cuts) else 1e9)  # 같은 장면의 처음 칸 · 다음 장면 칸
    if before:
        k = math.floor(lab * EV_FPS + 1e-6)
        if k / EV_FPS < lo - 1e-6:  # 장면이 막 바뀐 뒤라 앞 칸은 다른 장면 → 그 장면의 첫 칸
            k = math.ceil(lo * EV_FPS - 1e-6)
    else:
        k = math.ceil(lab * EV_FPS - 1e-6)
        if k / EV_FPS >= hi - 1e-6:  # 곧 장면이 바뀜 → 그 전 칸
            k = math.floor(lab * EV_FPS + 1e-6)
    return min(n - 1, max(0, k))


def extract_events(name, log=print, label="스타일 배우는 중"):
    """영상 하나를 살펴본 기록(스타일 이벤트): 컷·확대 컷·띠별 글자 밀도(1초 2번)·자막 색 표본·무음·소리 세기(0.1초)·움직임(1초 2번)
    ·작은 흑백 화면(1초 2번 · 편집본 점수에서 점프 컷이 화면에 티가 나는지 볼 때).
    analysis/<영상>/style_events.json 에 남기고, 영상 파일(크기·수정 시각)이 그대로면 다시 살펴보지 않음."""
    import numpy as np
    path = core.VIDEOS / name
    if not path.is_file():
        raise FileNotFoundError(f"영상을 찾지 못했어요 · {name}")
    log(f"스타일 분석 · {name}")
    ev = _load_events(name, path)
    if ev is not None:
        sp = _speech(name)
        if sp != ev.get("speech"):  # 그 사이 받아쓰기를 했으면 말 빠르기만 새로
            ev["speech"] = sp
            _save_events(name, ev)
        log("  예전에 살펴본 기록을 그대로 써요")
        return ev
    sig = _sig(path)
    probe = _probe_duration(path)
    dur = probe or 1.0
    core.set_progress(label=label, item=name, pct=0, detail="화면 컷·줌·자막 살펴보는 중")
    prev_g = prev_s = None
    diffs, cuts, zooms, band_hist, motion, colors, looks = [], [], [], [], [], [], []
    last_cut, i = -99, 0
    for rgb in _frames(path):
        if i % FPS == 0 and _cancelled():
            raise StyleCancelled("영상 살펴보기를 멈췄어요")
        t = i / FPS
        g, bands, strong, ys, dens = _text_bands(rgb)
        band_hist.append(bands)
        s = _small(g)
        d = 0.0
        if prev_s is not None:
            d = float(np.abs(s - prev_s).mean())
            med = float(np.median(diffs[-24:])) if len(diffs) >= 4 else 4.0
            diffs.append(d)
            if d > 16 and d > med * 3 and t - last_cut >= 0.5:
                cuts.append(t)
                last_cut = t
                e0 = float(np.abs(g - prev_g).mean())
                e1, k1, x1, y1 = _zoom_score(prev_g, g)   # 확대(펀치인)
                e2, k2, x2, y2 = _zoom_score(g, prev_g)   # 축소(줌아웃)
                if min(e1, e2) < 0.55 * e0 and min(e1, e2) < 16:
                    zooms.append({"t": round(t, 2), "scale": k1, "ox": x1, "oy": y1} if e1 <= e2 else
                                 {"t": round(t, 2), "scale": round(1 / k2, 3), "ox": x2, "oy": y2})
        motion.append(d)
        if i % 2 == 0:  # 자막 색 표본: 글자가 있어 보이는 위치마다 (1초에 2번) · 작은 흑백 화면도 같은 장면에서
            colors += [{"t": t, "pos": pos, "rgb": c} for pos, c in _cap_colors(rgb, bands, strong, ys, dens)]
            looks.append(np.round(g.reshape(LOOK_H, FH // LOOK_H, LOOK_W, FW // LOOK_W).mean(axis=(1, 3))))
        prev_g, prev_s = g, s
        i += 1
        if i % (FPS * 20) == 0:
            core.set_progress(label=label, item=name, pct=min(90, int(t * 90 / dur)) if probe else None, detail=f"화면 살펴보는 중 · {int(t)}초" + (f" / {int(dur)}초" if probe else ""))
    if i == 0:
        raise RuntimeError(f"영상 화면을 읽지 못했어요 · {name}")
    dur = probe if probe > 0 else i / FPS  # 길이를 모르는 파일(녹화본 등)은 프레임 수로
    dur = max(dur, (cuts[-1] if cuts else 0) + 1 / FPS)
    # 1초에 2번: 글자 밀도는 짝수 번째 장면 그대로 (자막 색 표본과 같은 장면), 움직임은 두 장 중 큰 값
    text = np.minimum(255, np.round(np.array(band_hist, np.float32)[0::2] * 255)).astype(np.uint8).tolist()
    mo = np.array(motion + motion[-1:] * (len(motion) % 2), np.float32)

    core.set_progress(label=label, item=name, pct=92, detail="말 사이 공백·소리 크기 재는 중")
    au = _audio_events(path)
    ev = {"v": EV_VER, "sig": sig, "source": name, "duration": round(dur, 3), "analyzed": time.strftime("%Y-%m-%d %H:%M"),
          "cuts": cuts, "zooms": zooms, "fps": EV_FPS, "text": text, "capColors": colors,
          "silences": au["silences"], "rmsStep": RMS_STEP, "rms": au["rms"],
          "motion": np.round(mo.reshape(-1, 2).max(axis=1), 1).tolist(), "lufs": au["lufs"], "speech": _speech(name),
          "look": _pack_looks(looks)}
    _save_events(name, ev)
    core.set_progress(label=label, item=name, pct=100, detail="완료")
    return ev


# ---------- 기록 → 프로필 ----------

def _deciles(x):
    import numpy as np
    return [round(float(v), 3) for v in np.quantile(np.asarray(x, np.float64), QS)] if len(x) else []


def _captions(ev):
    """(위치별 자막 비율, 자막 위치, 자막 색)."""
    import numpy as np
    if ev.get("captionBands") is not None:  # 편집본에서 만든 기록: 자막이 떠 있는 시간 비율을 바로 씀
        bands = {k: float(ev["captionBands"].get(k) or 0.0) for k in POS_BANDS}
        pos = max(bands, key=bands.get) if max(bands.values()) > 0 else "bottom"
        return bands, pos, ev.get("captionColor") or "#FFFFFF"
    bh = np.array(ev.get("text") or [], np.float32).reshape(-1, 6) / 255.0
    if not len(bh):
        bh = np.zeros((1, 6), np.float32)
    base = float(np.median(bh[:, 1:4]))
    thr = max(0.10, base * 2.2)
    on = {pos: bh[:, a:b].max(axis=1) > thr for pos, (a, b) in POS_BANDS.items()}
    bands = {pos: float(v.mean()) for pos, v in on.items()}
    pos = max(bands, key=bands.get) if max(bands.values()) > 0 else "bottom"
    # 자막 색: 자막이 있다고 본 위치(captionPos)에서, 실제로 글자가 있던 순간에 뽑은 표본만
    last = len(bh) - 1
    cols = [c["rgb"] for c in ev.get("capColors") or [] if c.get("pos") == pos and on[pos][min(last, int(round(c["t"] * EV_FPS)))]]
    return bands, pos, _snap(np.median(np.array(cols, np.float32), axis=0)) if cols else "#FFFFFF"


def summarize(ev):
    """기록 → 스타일 프로필 (예전 프로필과 같은 값 이름 그대로 + 컷 길이·공백 길이 분포 shotDeciles·pauseDeciles)."""
    import numpy as np
    dur = float(ev.get("duration") or 0) or 1.0
    cuts = sorted(float(c) for c in ev.get("cuts") or [])
    zooms = ev.get("zooms") or []
    shots, by3 = [], ([], [], [])
    for off, ln in ev.get("parts") or [[0.0, dur]]:  # 여러 영상을 이어 붙인 기록이면 영상마다 따로 (경계는 컷이 아님)
        cs = [c - off for c in cuts if off < c < off + ln]
        edges = [0.0] + cs + [ln]
        shots += np.diff(edges).tolist()
        for a, b in zip(edges, edges[1:]):  # 컷 리듬 3구간 (#7): 컷 가운데가 도입 30초·마무리 20초·본론 중 어디인지
            m = (a + b) / 2
            by3[0 if m < CURVE_HEAD else 2 if m > ln - CURVE_TAIL else 1].append(b - a)
    shots = np.array(shots or [dur])
    sil = [max(0.0, float(b) - float(a)) for a, b in ev.get("silences") or []]
    ss = sorted(sil)
    bands, cap_pos, color = _captions(ev)
    sp = ev.get("speech") or {}
    zs = [z["scale"] for z in zooms if z["scale"] > 1]
    return {
        "source": ev.get("source"), "duration": round(dur, 1), "analyzed": time.strftime("%Y-%m-%d %H:%M"),
        "cutsPerMin": round(len(cuts) * 60 / dur, 2), "avgShot": round(float(shots.mean()), 2), "medianShot": round(float(np.median(shots)), 2),
        "zoomCutsPerMin": round(len(zooms) * 60 / dur, 2), "zoomRatio": round(len(zooms) / max(1, len(cuts)), 2),
        "avgZoom": round(float(np.mean(zs or [1.2])), 2),
        "silenceRatio": round(sum(sil) / dur, 3), "pauseP75": round(ss[int(len(ss) * 0.75)] if ss else 0.4, 2), "pauses": len(sil),
        "captionRatio": round(ev["capRatio"] if ev.get("capRatio") is not None else max(bands.values()), 2),
        "captionPos": cap_pos, "captionColor": color,
        "captionBands": {k: round(v, 2) for k, v in bands.items()},
        "lufs": ev.get("lufs"), "charsPerSec": round(sp["chars"] / sp["talk"], 2) if (sp.get("talk") or 0) > 5 else None,
        "shotDeciles": _deciles(shots), "pauseDeciles": _deciles(sil),
        "curve3": [round(float(np.median(g)) if g else float(np.median(shots)), 2) for g in by3],
    }


def analyze_style(name, log=print):
    """영상 하나 → 프로필 (기록이 있으면 그대로 씀)."""
    prof = summarize(extract_events(name, log))
    _log_prof(prof, log)
    return prof


def _log_prof(prof, log):
    log(f"  컷 {prof['cutsPerMin']}번/분 · 평균 컷 {prof['avgShot']}초 · 줌컷 {prof['zoomCutsPerMin']}번/분 · 자막 {int(prof['captionRatio'] * 100)}% ({prof['captionPos']})")


def _concat(evs):
    """여러 영상의 기록을 한 줄로 이어 붙임 (시각은 앞 영상 길이만큼 밀고, 영상 경계는 parts 로 남김).
    자막 비율은 영상마다 잰 값(자막이 가장 많은 위치)을 길이만큼 무게 두고 평균 — 영상마다 자막 위치가 달라도 자막을 덜 쓴 것으로 안 보이게."""
    out = {"source": [e.get("source") for e in evs], "parts": [], "cuts": [], "zooms": [], "text": [], "capColors": [],
           "silences": [], "rms": [], "motion": []}
    off = dur = 0.0
    en = ew = talk = chars = capw = 0.0
    has_sp = False
    for e in evs:
        d = float(e.get("duration") or 0)
        n = max(int(math.ceil(d * EV_FPS - 1e-9)), len(e.get("text") or []))
        step = n / EV_FPS  # 글자 밀도 기록과 시각이 어긋나지 않게 기록 칸 단위로 밂
        out["parts"].append([off, d])
        out["cuts"] += [off + c for c in e.get("cuts") or []]
        out["zooms"] += [dict(z, t=off + z["t"]) for z in e.get("zooms") or []]
        out["capColors"] += [dict(c, t=off + c["t"]) for c in e.get("capColors") or []]
        out["silences"] += [[off + a, off + b] for a, b in e.get("silences") or []]
        tx = list(e.get("text") or [])
        out["text"] += tx + [[0] * 6] * (n - len(tx))
        mo = list(e.get("motion") or [])
        out["motion"] += mo + [0.0] * (n - len(mo))
        k = int(round(step / RMS_STEP))
        out["rms"] += (list(e.get("rms") or []) + [-90] * k)[:k]
        if e.get("lufs") is not None and d > 0:  # 소리 크기는 길이만큼 무게를 둔 에너지 평균
            en += d * 10 ** (float(e["lufs"]) / 10)
            ew += d
        capw += d * max(_captions(e)[0].values())
        if e.get("speech"):
            has_sp = True
            talk += e["speech"]["talk"]
            chars += e["speech"]["chars"]
        off += step
        dur += d
    out.update(duration=dur, lufs=round(10 * math.log10(en / ew), 1) if ew else None, speech={"talk": talk, "chars": chars} if has_sp else None,
               capRatio=capw / dur if dur > 0 else None)
    return out


def merge(profiles, events=None):
    """여러 레퍼런스를 하나의 스타일로.
    events(영상마다 기록)가 다 있으면 기록을 이어 붙여 분포부터 다시 계산.
    기록이 없는 프로필(예전에 배운 것)은 평균 — 자막 위치·색·확대 배율·공백은 증거(자막 비율·확대 컷 수·공백 수)만큼 무게."""
    import statistics as st
    if len(profiles) == 1:
        return dict(profiles[0])
    if events and len(events) == len(profiles) and all(events):
        out = summarize(_concat(events))
        out["source"] = [p["source"] for p in profiles]
        return out
    out = {"source": [p["source"] for p in profiles], "duration": round(sum(p["duration"] for p in profiles), 1), "analyzed": time.strftime("%Y-%m-%d %H:%M")}
    for k in ("cutsPerMin", "avgShot", "medianShot", "zoomCutsPerMin", "zoomRatio", "avgZoom", "silenceRatio", "pauseP75", "captionRatio"):
        out[k] = round(st.mean(p[k] for p in profiles), 2)
    for k in ("lufs", "charsPerSec"):
        v = [p[k] for p in profiles if p.get(k) is not None]
        out[k] = round(st.mean(v), 2) if v else None
    capd = [p for p in profiles if p.get("captionRatio", 0) >= 0.1]  # 자막이 실제로 있던 영상만 위치·색 투표 (자막 비율만큼 무게)

    def _wpick(key, default):
        w = {}
        for p in capd:
            w[p[key]] = w.get(p[key], 0.0) + p["captionRatio"]
        return max(w, key=w.get) if w else default
    out["captionPos"] = _wpick("captionPos", "bottom")
    out["captionColor"] = _wpick("captionColor", "#FFFFFF")
    zc = [(p["zoomCutsPerMin"] * p["duration"] / 60, p["avgZoom"]) for p in profiles]
    tot = sum(c for c, _ in zc)
    out["avgZoom"] = round(sum(c * z for c, z in zc) / tot, 2) if tot else 1.2
    pp = [p["pauseP75"] for p in profiles if p.get("pauses")]
    out["pauseP75"] = round(st.mean(pp), 2) if pp else 0.4
    out["pauses"] = sum(p["pauses"] for p in profiles)
    # 분포가 있는 프로필끼리는 분위수를 증거(컷 수·공백 수)만큼 무게 두고 평균 (1차원 바서슈타인 무게중심)
    for key, wt in (("shotDeciles", lambda p: p["cutsPerMin"] * p["duration"] / 60 + 1), ("pauseDeciles", lambda p: p.get("pauses") or 0)):
        if any(key in p for p in profiles):
            qs = [(wt(p), p[key]) for p in profiles if len(p.get(key) or []) == len(QS) and wt(p) > 0]
            tw = sum(w for w, _ in qs)
            out[key] = [round(sum(w * q[j] for w, q in qs) / tw, 3) for j in range(len(QS))] if tw else []
    cv = [(p["duration"], p["curve3"]) for p in profiles if _ok3(p.get("curve3"))]  # 컷 리듬 3구간: 영상 길이만큼 무게
    if cv:
        tw = sum(w for w, _ in cv) or 1.0
        out["curve3"] = [round(sum(w * c[j] for w, c in cv) / tw, 2) for j in range(3)]
    return out


def _ok3(c):
    return isinstance(c, list) and len(c) == 3 and all(isinstance(x, (int, float)) and x > 0 for x in c)


def edit_params(prof):
    """스타일 프로필 → 자동 가편집에 쓸 값."""
    keep_pause = round(min(0.8, max(0.12, prof["pauseP75"] * 0.9)), 2)
    zoom_every = round(60 / prof["zoomCutsPerMin"], 1) if prof["zoomCutsPerMin"] >= 0.3 else 0
    # 컷 리듬 맞추기 (#7): 긴 말 컷을 이 길이로 나눔 (도입·본론·마무리) · 말 빠르기 목표(1초 글자 수, 0이면 안 맞춤)
    split = round(min(30.0, max(1.0, _num(prof, "medianShot", 0))), 2) if _num(prof, "medianShot", 0) > 0 else 0
    c3 = [round(min(30.0, max(1.0, float(x))), 2) for x in prof["curve3"]] if _ok3(prof.get("curve3")) else [split] * 3
    cps = _num(prof, "charsPerSec", 0)
    return {
        "keepPause": keep_pause,                       # 말 사이 이보다 길게 쉬면 자름
        "targetShot": prof["medianShot"],              # 한 컷 길이 목표(초)
        "zoomEvery": zoom_every,                       # 몇 초마다 줌 컷(펀치인) — 0이면 안 함
        "zoomScale": min(1.6, max(1.08, prof["avgZoom"])),
        "captions": prof["captionRatio"] >= 0.25,
        "captionPos": prof["captionPos"], "captionColor": prof["captionColor"],
        "lufs": prof["lufs"] if prof.get("lufs") is not None else -14.0,
        "splitShot": split, "curve3": c3 if split else [0, 0, 0],
        "tempo": round(cps, 2) if cps > 0 and prof.get("tempoOn", True) is not False else 0,
    }


def describe(prof):
    """사람이 읽기 쉬운 한 줄 설명."""
    p = edit_params(prof)
    pos = {"bottom": "아래", "middle": "가운데", "top": "위"}[prof["captionPos"]]
    return (f"컷이 {prof['avgShot']}초마다 바뀌고(1분에 {prof['cutsPerMin']}번), "
            + (f"{p['zoomEvery']}초마다 확대 컷(약 {p['zoomScale']}배)이 나와요. " if p["zoomEvery"] else "확대 컷은 거의 없어요. ")
            + (f"말 사이 {p['keepPause']}초 넘게 쉬면 잘라요. ")
            + (f"자막이 화면의 {int(prof['captionRatio'] * 100)}%에 {pos}쪽으로 깔려요." if p["captions"] else "자막은 적게 써요.")
            + (f" 도입 30초는 {p['curve3'][0]}초마다 컷, 본론은 {p['curve3'][1]}초, 마무리 20초는 {p['curve3'][2]}초마다 컷이에요."
               + ("" if p["zoomEvery"] else " 긴 말을 나눈 곳만 살짝(1.08배) 당겨 바꿔 보여요.")
               if _ok3(prof.get("curve3")) and p["splitShot"] else ""))


def list_styles():
    STYLES.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(STYLES.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.stem, "profile": d, "params": edit_params(d), "desc": describe(d)})
        except Exception:
            pass
    return out


def learn(style_name, names, log=print):
    style_name = re.sub(r'[\\/:*?"<>|]', "", style_name).strip(" .") or "내 스타일"
    profs, evs = [], []
    fails = []
    for k, n in enumerate(names, 1):
        core.set_progress(label="스타일 배우는 중", item=n, step=f"{k}/{len(names)}", pct=0, detail="준비 중")
        try:
            ev = extract_events(n, log)
            p = summarize(ev)
        except Exception as e:  # 한 영상이 깨져도 나머지로 배움
            fails.append(n)
            log(f"  배우지 못했어요 · {n} · {e}")
            continue
        _log_prof(p, log)
        profs.append(p)
        evs.append(ev)
    if not profs:
        raise RuntimeError("고른 영상에서 배울 수 있는 게 없었어요 (파일이 깨졌거나 화면이 없어요)")
    prof = merge(profs, evs)
    prof["refs"] = profs
    try:  # 같은 이름으로 다시 배워도 '말 빠르기 맞추기'를 꺼 둔 것은 그대로
        if json.loads((STYLES / f"{style_name}.json").read_text(encoding="utf-8")).get("tempoOn") is False:
            prof["tempoOn"] = False
    except (OSError, ValueError, AttributeError):
        pass
    STYLES.mkdir(parents=True, exist_ok=True)
    (STYLES / f"{style_name}.json").write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"스타일 저장 · {style_name} · {describe(prof)}")
    return {"name": style_name, "profile": prof, "params": edit_params(prof), "desc": describe(prof)}


def set_tempo(style_name, on):
    """스타일 카드의 '말 빠르기 맞추기' 켜기/끄기 → 스타일 파일(tempoOn)에 저장 (#7)."""
    f = (STYLES / f"{os.path.basename(str(style_name or ''))}.json").resolve()
    if STYLES.resolve() not in f.parents or not f.is_file():
        raise StyleMissing("스타일을 찾지 못했어요")
    d = json.loads(f.read_text(encoding="utf-8"))
    d["tempoOn"] = bool(on)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    for k in range(10):  # 윈도우: 다른 프로그램(백신 등)이 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, f)
            break
        except PermissionError:
            if k == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)
    return d


# ---------- 스타일 일치 점수 ----------

def _lr(a, b):
    return math.log(max(float(a), 0.05) / max(float(b), 0.05))


def _rel(a, b, floor):
    """비율 값의 상대 오차 (0~1). 둘 다 아주 작으면 floor 로 나눠 너무 박하지 않게."""
    a, b = float(a or 0), float(b or 0)
    return min(1.0, abs(a - b) / max(abs(a), abs(b), floor))


def _w1log(qa, qb):
    """분위수(10칸)로 잰 1차원 바서슈타인 거리 — 길이는 곱으로 달라지므로 로그 눈금 (1.1배 길면 ≈ 0.1)."""
    return sum(abs(_lr(x, y)) for x, y in zip(qa, qb)) / len(qa)


def _okq(q):
    return isinstance(q, list) and len(q) == len(QS)


def _hex_rgb(h):
    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})", str(h or ""))
    return tuple(int(m[1][k:k + 2], 16) for k in (0, 2, 4)) if m else None


def _num(p, k, dv=0.0):
    v = p.get(k)
    try:
        return float(v) if v is not None else dv
    except (TypeError, ValueError):
        return dv


def _part_d(a, b, params=None):
    """부분별 거리 0(같음)~1(전혀 다름). 비교할 수 없으면 None. 분포(분위수)가 둘 다 있으면 분포로, 없으면(예전 프로필) 평균값으로.
    params: b 가 a 의 edit_params 로 만든 가편집이면 — 스타일이 '안 함'으로 정한 자막·확대 컷을 가편집이 안 한 것은 맞은 것으로 봄."""
    out = {}
    ra, rb = _num(a, "cutsPerMin"), _num(b, "cutsPerMin")
    if min(ra * _num(a, "duration") / 60, rb * _num(b, "duration") / 60) < FEW_CUTS:
        # 컷이 거의 없는 쪽이 있으면 컷 길이 = 영상 길이(처음·끝에서 잘린 컷)라 리듬이 아님 → 1분당 컷 수로 (한 번 차이는 봐줌)
        db = _num(b, "duration")
        slack = 60.0 / db if db > 0 else 0.0
        rb = ra if abs(rb - ra) <= slack else rb - math.copysign(slack, rb - ra)
        out["컷 리듬"] = abs(math.log(max(ra, RATE_FLOOR) / max(rb, RATE_FLOOR)))
    elif _okq(a.get("shotDeciles")) and _okq(b.get("shotDeciles")):
        out["컷 리듬"] = _w1log(a["shotDeciles"], b["shotDeciles"])
    else:
        out["컷 리듬"] = (abs(_lr(_num(a, "avgShot", 1), _num(b, "avgShot", 1))) + abs(_lr(_num(a, "medianShot", 1), _num(b, "medianShot", 1)))) / 2
    za, zb = _num(a, "zoomCutsPerMin"), _num(b, "zoomCutsPerMin")
    db = _num(b, "duration")
    slack = 60.0 / db if db > 0 else 0.0  # 한 번 차이는 봐줌 (짧은 영상은 확대 컷이 0번이냐 1번이냐로 갈림)
    f = min(1.0, max(0.0, abs(za - zb) - slack) / max(abs(za), abs(zb), 0.5))
    if params is not None and not params.get("zoomEvery") and zb <= 0:
        f = 0.0
    out["확대"] = 0.7 * f + 0.3 * _rel(_num(a, "avgZoom", 1.2) - 1, _num(b, "avgZoom", 1.2) - 1, 0.1) if za >= 0.3 and zb >= 0.3 else f
    na, nb = _num(a, "pauses"), _num(b, "pauses")
    if na <= 0 or nb <= 0:
        w = 0.0 if na <= 0 and nb <= 0 else 1.0
    elif _okq(a.get("pauseDeciles")) and _okq(b.get("pauseDeciles")):
        w = _w1log(a["pauseDeciles"], b["pauseDeciles"])
    else:
        w = abs(_lr(_num(a, "pauseP75", 0.4), _num(b, "pauseP75", 0.4)))
    out["공백"] = 0.7 * min(1.0, w) + 0.3 * _rel(_num(a, "silenceRatio"), _num(b, "silenceRatio"), 0.05)
    ca, cb = _num(a, "captionRatio"), _num(b, "captionRatio")
    r = _rel(ca, cb, 0.1)
    if ca >= 0.1 and cb >= 0.1:  # 둘 다 자막을 쓰면 위치·색까지
        x, y = _hex_rgb(a.get("captionColor")), _hex_rgb(b.get("captionColor"))
        col = min(1.0, math.dist(x, y) / 255) if x and y else 0.0
        out["자막"] = 0.5 * r + 0.25 * (a.get("captionPos") != b.get("captionPos")) + 0.25 * col
    else:
        out["자막"] = 0.0 if params is not None and not params.get("captions") and cb < 0.1 else r
    la, lb = a.get("lufs"), b.get("lufs")
    out["소리"] = abs(float(la) - float(lb)) / 10 if la is not None and lb is not None else None  # 10 LU 차이면 0점
    return {k: None if v is None else min(1.0, max(0.0, float(v))) for k, v in out.items()}


def _cap_w(w, use):
    """무게를 합 1로 맞추고 한 부분이 W_CAP 을 넘지 않게 (넘친 만큼 나머지에 비율대로 나눔)."""
    tot = sum(w[k] for k in use)
    if not use or tot <= 0:
        return {k: 1.0 / len(use) for k in use}
    cap, done = max(W_CAP, 1.0 / len(use)), {}
    while True:
        rest = [k for k in use if k not in done]
        left = 1.0 - sum(done.values())
        rw = sum(w[k] for k in rest)
        out = dict(done, **{k: (w[k] / rw * left if rw > 0 else left / len(rest)) for k in rest})
        over = [k for k in rest if out[k] > cap + 1e-9]
        if not over:
            return out
        done.update({k: cap for k in over})


def distance(target, cand, params=None, fixed=()):
    """배운 스타일(target) ↔ 다른 프로필(cand)이 얼마나 닮았는지.
    → {score 0~100, parts {컷 리듬, 확대, 공백, 자막, 소리} 0~100 (모르면 None), distance 0~1, weights, basis, fixed}.
    부분 점수 = 100 × (1 − 거리). 전체 점수 = 무게 평균 — 무게는 레퍼런스 영상끼리 그 부분이 얼마나 한결같은지(1/표준편차),
    레퍼런스가 하나뿐이면 기본 무게. 한 부분 무게는 W_CAP 까지.
    params: cand 가 target 의 edit_params 로 만든 가편집일 때 (스타일이 '안 함'으로 정한 것은 안 해도 맞음).
    fixed: 가편집이 스타일 값을 그대로 옮겨 써서 늘 맞는 부분 (예: 소리 크기) — 부분 점수는 보여 주되 전체 점수에서는 뺌."""
    d = _part_d(target, cand, params)
    refs = [r for r in target.get("refs") or [] if isinstance(r, dict)]
    w = dict(BASE_W)
    if len(refs) >= 2:
        spread = {k: [] for k in PARTS}
        for r in refs:
            for k, v in _part_d(target, r).items():
                if v is not None:
                    spread[k].append(v)
        inv = {k: 1.0 / max(STD_FLOOR, math.sqrt(sum(v * v for v in vs) / len(vs))) for k, vs in spread.items() if vs}
        w = {k: inv.get(k, sum(inv.values()) / len(inv) if inv else 1.0) for k in PARTS}
    fixed = [k for k in PARTS if k in set(fixed or ())]
    use = [k for k in PARTS if d[k] is not None and k not in fixed]
    nw = _cap_w(w, use)
    dist = sum(nw[k] * d[k] for k in use) if use else 0.0
    both = all(_okq(target.get(k)) and _okq(cand.get(k)) for k in ("shotDeciles", "pauseDeciles"))
    return {"score": int(round(100 * (1 - dist))), "parts": {k: None if d[k] is None else int(round(100 * (1 - d[k]))) for k in PARTS},
            "distance": round(dist, 4), "weights": {k: round(nw[k], 3) if k in use else 0.0 for k in PARTS}, "fixed": fixed,
            "basis": "분포" if both else "평균"}


def _union(ivs, lo, hi):
    out = []
    for a, b in sorted((max(lo, float(a)), min(hi, float(b))) for a, b in ivs):
        if b - a <= 1e-6:
            continue
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _minus(lo, hi, ivs):
    out, cur = [], lo
    for a, b in _union(ivs, lo, hi):
        if a > cur:
            out.append([cur, a])
        cur = max(cur, b)
    if hi > cur:
        out.append([cur, hi])
    return out


def _pos_of_y(y):
    y = float(y)
    return "top" if y < 1 / 3 else "middle" if y < 2 / 3 else "bottom"


def _cached_events(name):
    """원본 영상의 기록 (있고 파일이 그대로일 때만) — 편집본의 말 사이 공백을 더 정확히."""
    try:
        name = os.path.basename(str(name or ""))
        p = core.VIDEOS / name
        return _load_events(name, p) if name and p.is_file() else None
    except (OSError, ValueError):
        return None


def profile_from_sequence(proj, seq=None):
    """편집본(시퀀스 JSON)을 내보내지 않고 바로 스타일 프로필로 (값 이름은 summarize 와 같음).
    컷: V1 클립 경계(원본이 이어지지 않거나 크기가 바뀌는 곳)·위 트랙 영상(B롤)의 시작과 끝·0.25초 안에 확 바뀌는 크기 키프레임
    — 단, 화면 분석에도 컷으로 보일 곳만 (스타일은 화면 분석으로 배웠으니 같은 눈으로 셈): 원본 기록의 작은 화면이 있으면
    경계 앞뒤 화면에 같은 규칙(차이가 문턱보다 크고 최근 움직임의 3배 넘음)을 적용하고, 없으면 한 자리에서 계속 찍은 촬영본으로 봄
    (같은 영상 안의 점프 컷은 크기가 바뀔 때만 컷).
    확대: 경계·키프레임에서 바뀐 크기(scale) 비율. 자막: 자막(받아쓰기)·제목이 떠 있는 시간과 위치·색.
    공백: 소리 클립 사이 빈 곳 + 원본의 무음(기록이 있으면) 또는 받아쓰기 말 사이. 소리 크기: 마스터 소리 맞추기 목표.
    seq 를 주면 proj 의 받아쓰기·미디어와 합쳐서 봄 (편집실 내보내기와 같은 방식), 없으면 proj 가 이미 합쳐진 편집본."""
    import editor as ed
    flat = dict(proj) if seq is None else dict(seq, captions=proj.get("captions") or [], info=proj.get("info"),
                                                source=proj.get("source"), media=proj.get("media") or [])
    flat = ed.migrate_seq(flat)
    items = [it for it in flat.get("items") or [] if float(it.get("out", 0)) - float(it.get("in", 0)) > 1e-3]
    flat["items"] = items
    total = ed.seq_total(flat)
    if total <= 0.05:
        raise StyleError("편집본이 비어 있어요")
    tracks = {t.get("id"): t for t in flat.get("tracks") or []}
    hidden = {k for k, t in tracks.items() if t.get("hide")}
    solo = {k for k, t in tracks.items() if t.get("solo")}
    media = {m.get("id"): m for m in flat.get("media") or []}
    src = flat.get("source") or (media.get("main") or {}).get("file")
    cached = _cached_events(src) if src else None  # 원본을 스타일 분석한 기록 (있으면 장면 전환·무음·소리 크기에 씀)

    def scale_at(it, t):
        return float(ed.kf_at(ed.param(it, "scale"), ed.i_mt(it, t))) / 100.0

    def pos_at(it, t):
        p = ed.kf_at(ed.param(it, "pos"), ed.i_mt(it, t))
        return float(p[0]), float(p[1])

    evs, scenes, lks = {}, {}, {}

    def media_ev(mid):
        """미디어마다 원본을 스타일 분석한 기록 (보관함 영상이고, 기록이 있고, 파일이 그대로일 때만)."""
        if mid not in evs:
            m = media.get(mid) or {}
            evs[mid] = cached if mid == "main" else _cached_events(m.get("file")) if m.get("src", "videos") == "videos" else None
        return evs[mid]

    def same_scene(a, b, ma, mb):
        """같은 원본에서 원본 속 장면 전환을 건너지 않았는지 (그래야 크기 차이가 '확대 컷'으로 보임).
        원본 기록이 없으면 같은 장면으로 봄 (말하는 사람을 계속 찍은 촬영본)."""
        if a.get("media") != b.get("media"):
            return False
        mid = a.get("media")
        if mid not in scenes:
            ev = media_ev(mid)
            scenes[mid] = sorted(ev.get("cuts") or []) if ev else None
        sc = scenes[mid]
        return sc is None or bisect.bisect(sc, min(ma, mb)) == bisect.bisect(sc, max(ma, mb))

    def look(mid, mt, before):
        """원본 mt 초 무렵의 작은 흑백 화면과 그 앞 6초 움직임의 가운데 값 (화면 분석의 '최근 움직임'). 기록이 없으면 None."""
        import numpy as np
        if mid not in lks:
            ev = media_ev(mid)
            lks[mid] = (_looks(ev), ev.get("motion") or [], sorted(float(c) for c in ev.get("cuts") or [])) if ev else (None, [], [])
        L, mo, sc = lks[mid]
        if L is None:
            return None
        k = _look_index(len(L), sc, mt, before)
        w = [float(x) for x in mo[max(0, k - 12):max(0, k)]]
        return L[k], (float(np.median(w)) if len(w) >= 2 else 4.0)

    def seen(a, ta, ma, b, tb, mb):
        """a 의 마지막 장면(타임라인 ta · 원본 ma) → b 의 첫 장면(tb · mb) 이 화면 분석에 컷으로 보일지."""
        import numpy as np
        sa, sb = scale_at(a, ta), scale_at(b, tb)
        la, lb = look(a.get("media"), ma, not a.get("rev")), look(b.get("media"), mb, bool(b.get("rev")))
        if la is None or lb is None:  # 원본 기록 없음: 다른 영상이면 컷, 같은 영상이면 크기가 바뀔 때만
            return a.get("media") != b.get("media") or (sa > 1e-6 and abs(sb / sa - 1) >= 0.03)
        d = float(np.abs(_render_look(la[0], sa, *pos_at(a, ta)) - _render_look(lb[0], sb, *pos_at(b, tb))).mean())
        return d > LOOK_CUT and d > 3 * la[1]

    cuts, zooms = [], []

    def zoom(t, it, ratio):
        p, s = ed.kf_at(ed.param(it, "pos"), ed.i_mt(it, t)), max(1e-3, scale_at(it, t))
        zooms.append({"t": round(t, 3), "scale": round(ratio, 3), "ox": round(2 * (0.5 - float(p[0])) / s, 3), "oy": round(2 * (0.5 - float(p[1])) / s, 3)})

    v1 = sorted((it for it in items if it.get("track") == "V1" and "V1" not in hidden), key=lambda x: x["start"])
    if v1 and v1[0]["start"] > 0.04:  # 검은 화면으로 시작
        cuts.append(v1[0]["start"])
    for a, b in zip(v1, v1[1:]):
        ea, t = ed.i_end(a), b["start"]
        if t - ea > 0.04:  # 빈 곳(검은 화면)
            cuts.append(ea)
            if t - ea >= 0.5:
                cuts.append(t)
            continue
        ta = max(a["start"], ea - 1e-3)
        sa = scale_at(a, ta)
        ratio = scale_at(b, t) / sa if sa > 1e-6 else 1.0
        ma, mb = ed.i_mt(a, ea), ed.i_mt(b, t)
        same = (a.get("media") == b.get("media") and bool(a.get("rev")) == bool(b.get("rev"))
                and abs(ma - mb) <= 0.1)  # 원본이 그대로 이어지면 자른 티가 안 남
        if (same and abs(ratio - 1) < 0.03) or not seen(a, ta, ma, b, t, mb):
            continue
        cuts.append(t)
        if abs(ratio - 1) >= 0.03 and same_scene(a, b, ma, mb):
            zoom(t, b, ratio)
    for it in v1:  # 키프레임으로 순간 확대 (0.25초 안에 3% 넘게 바뀜 · 천천히 당기는 줌은 화면 분석도 컷으로 안 봄)
        ks = sorted(((ed.i_tl(it, k["t"]), float(k["v"])) for k in ed.param(it, "scale")["k"]), key=lambda x: x[0])
        for (t1, s1), (t2, s2) in zip(ks, ks[1:]):
            if 0 <= t2 - t1 <= 0.25 and it["start"] + 0.04 < t2 < ed.i_end(it) - 0.04 and s1 > 0 and abs(s2 / s1 - 1) >= 0.03:
                lk = look(it.get("media"), ed.i_mt(it, t2), False)
                if lk is not None:  # 원본 화면이 밋밋하면 확대해도 화면 분석에는 안 보임
                    import numpy as np
                    d = float(np.abs(_render_look(lk[0], s1 / 100, *pos_at(it, t1)) - _render_look(lk[0], s2 / 100, *pos_at(it, t2))).mean())
                    if not (d > LOOK_CUT and d > 3 * lk[1]):
                        continue
                cuts.append(t2)
                zoom(t2, it, s2 / s1)
    for it in v1:  # 원본 속 장면 전환(원본 기록의 컷·확대 컷)은 클립 안에 있으면 편집본에도 그대로 보임
        ev = media_ev(it.get("media"))
        lo, hi = sorted((float(it["in"]), float(it["out"])))
        zm = {round(float(z["t"]), 2): z for z in (ev or {}).get("zooms") or []}
        for c in (ev or {}).get("cuts") or []:
            if lo + 0.1 < float(c) < hi - 0.1:
                t = ed.i_tl(it, float(c))
                cuts.append(t)
                z = zm.get(round(float(c), 2))
                if z:
                    zooms.append({"t": round(t, 3), "scale": round(1 / z["scale"], 3) if it.get("rev") else z["scale"], "ox": z.get("ox", 0.0), "oy": z.get("oy", 0.0)})
    for it in items:  # 위 트랙에 올린 영상(B롤): 들어올 때·나갈 때 화면이 바뀜 (로고·사진은 제외)
        tid = str(it.get("track") or "")
        if tid.startswith("V") and tid != "V1" and tid not in hidden and (media.get(it.get("media")) or {}).get("kind", "video") == "video":
            cuts += [it["start"], ed.i_end(it)]

    def thin(ts):  # 화면 분석과 같게: 0.5초 안의 컷은 하나로, 맨 처음·끝은 컷이 아님
        out = []
        for t in sorted(ts):
            if 0.04 < t < total - 0.04 and (not out or t - out[-1] >= 0.5):
                out.append(round(t, 3))
        return out
    cuts = thin(cuts)
    kept, zs = set(cuts), []
    for z in sorted(zooms, key=lambda z: z["t"]):  # 확대는 남은 컷 자리에서만 (컷마다 하나)
        if z["t"] in kept:
            kept.discard(z["t"])
            zs.append(z)
    zooms = zs

    # 자막: 받아쓰기 자막(편집본 자막 스타일 위치·색) + 제목 — 위치별로 떠 있는 시간
    cov, fills = {p: [] for p in POS_BANDS}, {p: {} for p in POS_BANDS}

    def add(pos, ivs, fill):
        cov[pos] += ivs
        fills[pos][fill] = fills[pos].get(fill, 0.0) + sum(max(0.0, min(total, b) - max(0.0, a)) for a, b in ivs)
    cs = flat.get("captionStyle") or ed.DEFAULT_STYLE
    if flat.get("captionsOn", True) and flat.get("captions"):
        add(_pos_of_y(cs.get("y", 0.82)), [(c["start"], c["end"]) for c in ed.timeline_captions(flat)], str(cs.get("fill") or "#FFFFFF").upper())
    for tt in flat.get("titles") or []:
        s = tt.get("style") or {}
        if str(tt.get("text") or "").strip():
            add(_pos_of_y(s.get("y", 0.22)), [(float(tt["start"]), float(tt["start"]) + float(tt["dur"]))], str(s.get("fill") or "#FFFFFF").upper())
    bands = {p: sum(b - a for a, b in _union(ivs, 0.0, total)) / total for p, ivs in cov.items()}
    pos = max(bands, key=bands.get) if max(bands.values()) > 0 else "bottom"
    fill = max(fills[pos], key=fills[pos].get) if fills[pos] else "#FFFFFF"

    # 공백: 소리가 나는 곳을 타임라인에 모으고 나머지 (0.15초 넘는 곳 = 화면 분석의 무음 찾기와 같은 기준)
    caps = [(float(c["start"]), float(c["end"]), str(c.get("text") or "")) for c in flat.get("captions") or []
            if float(c.get("end", 0)) > float(c.get("start", 0))]
    src_sil = cached.get("silences") if cached else None

    def to_tl(it, a, b):
        x, y = max(a, it["in"]), min(b, it["out"])
        if y - x <= 1e-6:
            return None
        p, q = ed.i_tl(it, x), ed.i_tl(it, y)
        return (min(p, q), max(p, q))
    sound, talk, chars = [], 0.0, 0.0
    for it in items:
        tid = str(it.get("track") or "")
        if not tid.startswith("A") or it.get("mute") or (tracks.get(tid) or {}).get("mute") or (solo and tid not in solo):
            continue
        a0, a1 = it["start"], ed.i_end(it)
        m = media.get(it.get("media")) or {}
        if not (it.get("media") == "main" or (src and m.get("file") == src and m.get("src", "videos") == "videos")):
            sound.append((a0, a1))  # 음악·효과음 → 계속 소리가 남
            continue
        if src_sil is not None:
            sound += [tuple(x) for x in _minus(a0, a1, [r for r in (to_tl(it, a, b) for a, b in src_sil) if r])]
        elif caps:
            sound += [r for r in (to_tl(it, a, b) for a, b, _ in caps) if r]
        else:
            sound.append((a0, a1))
        for a, b, txt in caps:  # 말 빠르기: 남은 말 길이·글자 수 (잘린 만큼 비율로)
            r = to_tl(it, a, b)
            if r:
                talk += r[1] - r[0]
                chars += len(txt.replace(" ", "")) * (min(b, it["out"]) - max(a, it["in"])) / (b - a)
    sil = [[round(a, 3), round(b, 3)] for a, b in _minus(0.0, total, sound) if b - a >= 0.15]

    mst = flat.get("master") or {}
    if mst.get("normalize", True):
        lufs = round(min(-9.0, max(-24.0, float(mst.get("lufs") or -14.0))), 1)
    elif cached and cached.get("lufs") is not None:
        lufs = round(float(cached["lufs"]) + 20 * math.log10(max(1e-3, float(mst.get("volume", 1.0) or 1.0))), 1)
    else:
        lufs = None
    return summarize({"source": src, "duration": total, "cuts": cuts, "zooms": zooms, "silences": sil, "lufs": lufs,
                      "speech": {"talk": talk, "chars": chars} if talk else None,
                      "captionBands": bands, "captionColor": "#%02X%02X%02X" % (_hex_rgb(fill) or (255, 255, 255))})


def _score_inputs(style_name, name):
    """점수 매기기 전 확인 — 문제가 있으면 사용자에게 그대로 보여 줄 한국어 안내(StyleError)."""
    import editor as ed
    st = next((x for x in list_styles() if x["name"] == style_name), None)
    if st is None:
        raise StyleMissing("스타일을 찾지 못했어요")
    try:
        ed.video_path(name)  # 보관함 안의 파일 이름만 (안내는 이미 한국어)
    except (ValueError, FileNotFoundError) as e:
        raise StyleError(str(e)) from None
    d = core.adir(name)
    if not (d / "transcript.json").exists():
        raise StyleError("이 영상은 아직 '편집점 찾기'를 하지 않았어요. 편집점 찾기를 한 뒤 다시 눌러 주세요")
    try:  # 편집점 찾기가 쓰는 중이거나 깨진 파일
        segs = [{"start": float(x["start"]), "end": float(x["end"]), "text": str(x["text"])}
                for x in json.loads((d / "transcript.json").read_text(encoding="utf-8"))]
        if (d / "analysis.json").exists() and not isinstance(json.loads((d / "analysis.json").read_text(encoding="utf-8")), dict):
            raise ValueError("analysis.json")
    except (OSError, ValueError, KeyError, TypeError):
        raise StyleError("받아쓰기 파일을 읽지 못했어요. 편집점 찾기를 다시 해 주세요") from None
    return st, segs


def _fail_file(name):
    return core.adir(name) / "style_events_fail.json"


def _failed_before(name):
    """예전에 이 파일(크기·수정 시각 그대로)을 살펴보다 실패했는지 — 그러면 다시 긴 작업을 돌리지 않음."""
    try:
        j = json.loads(_fail_file(name).read_text(encoding="utf-8"))
        return j.get("sig") == _sig(core.VIDEOS / os.path.basename(name)) and 0 <= time.time() - float(j.get("at") or 0) < FAIL_TTL
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def _mark_failed(name):
    try:
        _fail_file(name).parent.mkdir(parents=True, exist_ok=True)
        _fail_file(name).write_text(json.dumps({"sig": _sig(core.VIDEOS / os.path.basename(name)), "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def score_video(style_name, name, log=None, analyze=False):
    """배운 스타일로 만든 자동 가편집(롱폼)이 그 스타일과 얼마나 닮았는지 — 내보내지 않고 편집본 JSON 으로 바로.
    원본을 아직 안 살펴봤으면(점프 컷이 화면에 티가 나는지 알 수 없음) NeedsAnalysis — analyze=True(작업)면 먼저 살펴봄.
    소리 크기는 가편집이 스타일 값으로 맞추기만 하므로 보여 주기만 하고 전체 점수에서는 뺌 (fixed)."""
    import editor as ed
    st, segs = _score_inputs(style_name, name)
    if _cached_events(name) is None and not _failed_before(name):
        if not analyze:
            raise NeedsAnalysis(name)
        try:
            extract_events(name, log or (lambda m: None), label="원본 화면 살펴보는 중")
        except StyleCancelled:
            raise
        except Exception as e:  # noqa: BLE001  못 살펴봐도 점수는 매김 (한 자리에서 계속 찍은 촬영본으로 보고)
            (log or print)(f"  원본 화면을 살펴보지 못해 어림으로 매겨요 · {e}")
            if not isinstance(e, OSError):  # 파일이 잠겼거나 권한 문제(OSError)는 잠깐일 수 있어 다음에 다시 살펴봄
                _mark_failed(name)  # 같은 파일이면 한동안(FAIL_TTL) 다시 살펴보지 않음
    info = ed.media_info(name)
    params = st["params"]
    seq = ed.auto_sequences(name, info, params, ("long",))[0]
    proj = {"source": name, "info": info, "captions": [x for x in segs if x["text"].strip()],
            "media": [{"id": "main", "kind": "video", "src": "videos", "file": name, "dur": info["duration"], "w": info["width"],
                       "h": info["height"], "fps": info.get("fps", 30.0), "audio": True}]}
    prof = profile_from_sequence(proj, seq)
    mst = seq.get("master") or {}
    # 소리 크기를 맞추면(normalize) 가편집은 스타일 값(-24~-9 안으로)을 그대로 씀 → 늘 '스타일대로'
    fixed = ["소리"] if (mst.get("normalize", True) and prof.get("lufs") is not None) else []
    res = distance(st["profile"], prof, params=params, fixed=fixed)
    res.update(style=style_name, name=name, guessed=_cached_events(name) is None, rough={k: prof[k] for k in ("cutsPerMin", "avgShot", "zoomCutsPerMin", "pauseP75",
                                                                          "captionRatio", "captionPos", "captionColor", "lufs")})
    return res
