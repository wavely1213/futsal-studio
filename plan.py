"""영상 기획 분석 — 레퍼런스 영상이 '어떤 공식'으로 만든 영상인지 판단 (인트로 구성·장르·형식·자막 처리·재미 요소).

화면에는 판단 문장만 보여 주고, 시각·프레임·글자 상자는 판단을 만드는 내부 근거로만 씀 (소유자 피드백 2026-10-07).
1) extract_plan: 영상 하나를 1초에 한 장(640px)씩 보고 화면 지문(dHash)·복잡도·잔디 비율·화면 글자(OCR)를,
   소리는 16kHz 로 웃음·환호·음악·효과음(YAMNet)·화자 수(MFCC)를 남김 → analysis/<영상>/plan_events.json
   (영상 파일 크기·수정 시각이 그대로이고 모델 상태가 그때보다 나쁘지 않으면 다시 안 봄).
2) detect: 남긴 신호 + style_events(컷·확대 컷·움직임·글자 밀도·소리 세기) + 받아쓰기 → 사건(티저·타이틀·리플레이·자막 종류…).
3) judge: 사건 → 판단 (intro·genre·format·captions·fun·summary·apply). merge_plans: 여러 영상 → 스타일 하나 (길이×확신 투표).
4) plan_params: 판단 → 가편집에 바로 쓸 값 (인트로 티저·강조 자막, 기본 꺼짐). claude_prompt: Claude 에게 물어볼 내용.
모델(OCR·YAMNet)을 못 받으면 화면·소리 어림 규칙으로 계속하고, 판단 문장에 '어림'이라고 적음."""
import base64
import bisect
import difflib
import json
import math
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import unicodedata
import zlib
from pathlib import Path

import core

PLAN_VER = 1
PW = 640                     # 1초 한 장 화면 폭
OCR_CAP, OCR_CAP_SLOW, OCR_SLOW = 200, 120, 0.3   # 영상 하나에 글자 읽기 최대 장 수 (한 장이 0.3초 넘으면 250장)
OCR_SAME = 6                 # 글자 띠 지문이 이만큼 이하로 다르면 같은 자막 (다시 안 읽음)
INTRO_MAX, INTRO_FRAC, INTRO_MIN = 60.0, 0.15, 20.0
FULL_DECODE_MAX, FULL_DECODE_HEAD = 240.0, 120.0  # 4분이 넘으면 앞 2분만 전부 풀고 나머지는 키프레임만
TEASER_HAM, REPLAY_HAM, INSERT_HAM = 10, 8, 24
TEASER_BLOCK_MAX = 15      # 티저 한 토막은 15초까지 (더 길면 그냥 인트로 장면)
TEASER_START_MAX = 10      # 첫 티저 토막은 10초 안에 시작
TEASER_CHAIN_MAX = 30      # 티저 토막을 이어 붙여도 30초 안에서
REPLAY_MAX, REPLAY_SLOW_MAX = 12, 4.0   # 리플레이는 12초까지 · 4배보다 느리면 리플레이가 아님
COMMON_FRAC = 0.08           # 영상의 8% 넘게 닮은 화면은 '늘 보이는 화면'(고정 카메라) → 티저·리플레이로 안 봄
BIG_H, TITLE_H, SMALL_H = 0.07, 0.08, 0.045
# 인사: '여러분' 같은 부르는 말만으로는 인사로 보지 않음 (예: '여러분 이 기술 하나면 수비 다 뚫습니다' 는 훅)
GREETING = re.compile(r"안녕하세요|안녕하십니까|구독|채널입니다|채널에 오신|(저는|제가|여기는) .{1,10}(입니다|이에요|예요)|.{2,10}의 .{2,6}입니다")
HOOK_Q = re.compile(r"\?|까요|을까|할까|ㄹ까|일까|될까|있을까|없을까")
# 그냥 진행하는 말 ('해 볼까요?', '시작해 볼게요') 은 질문형 훅이 아님
ROUTINE_Q = re.compile(r"(해|배워|가|시작해|알아|살펴|만나|들어가|차|연습해|보러 가)\s?(볼까요|봅시다|볼게요|보죠|보겠습니다)|시작할게요|시작합니다")
HOOK_W = re.compile(r"하나면|만 ?알면|다 뚫|끝(입니다|나요|납니다)|달라(집니다|져요)|바뀝니다|바뀌어요|진짜|과연|가능|최초|역대|충격|도전|vs|VS|이기면|지면|만약|벌칙|끝판왕|무조건|절대|100%|이것만")
INFO_NAME = re.compile(r"^[가-힣]{2,4}\s?(감독|선수|코치|님|씨|대표|프로)(님)?(\s?\(.{1,12}\))?$")
INFO_NUM = re.compile(r"\d+\s*(초|m|km|개|점|%|회|골|번|kg|cm)|\d+\s*[:：]\s*\d+")
SFX_WORD = re.compile(r"^(쾅|퍽|뻥|슝|두둥|띠용|휙|탁|짠|팡|빵|쿵|툭|탕|휘익|두근|부들)+[!~?]*$")
REPEAT = re.compile(r"^(.)\1{2,}$|^[!?.~]+$|^(ㅋ|ㅎ|ㅠ|ㅜ)+$")
INNER = re.compile(r"^\(.*\)$|^（.*）$|…|\.\.\.|(ㅠㅠ|ㅜㅜ|;;|ㅋㅋ)$|속마음|\((당황|민망|뿌듯|머쓱|충격|분노|감동|씁쓸)\)")
SITU = re.compile(r"(중|함|했음|하는 중|등장|시작|완료|성공|실패|도착|결과)$|^(그때|결국|그리고|한편|잠시 후|드디어)")
EMPH_WORDS = ("무조건", "절대", "진짜", "대박", "100%", "이것만", "핵심", "꿀팁", "비결", "중요", "실수", "충격", "레전드")
COLORS = {"white": "#FFFFFF", "yellow": "#FFE14D", "red": "#FF4D4D", "orange": "#FF9F1C", "blue": "#4DA3FF",
          "green": "#3DDC84", "pink": "#FF6FB5", "black": "#111111"}
CNAME_KO = {"white": "흰", "yellow": "노랑", "red": "빨강", "orange": "주황", "blue": "파랑", "green": "초록", "pink": "분홍", "black": "검정"}
POS_KO = {"top": "위", "middle": "가운데", "bottom": "아래"}
SOCCER = re.compile(r"축구|풋살|슛|슈팅|골키퍼|드리블|패스|프리킥|코너킥|킥|골|리그|국가대표|손흥민|메시|호날두")

# 장르 사전: (키, 라벨, 어디서나 볼 낱말, 제목에서만 볼 낱말)
# 대사에 흔히 나오는 말(같이·하루·선수·질문·만나·성공·실패·진짜로·따라)은 제목에서만 봄 — 레슨 영상이 '브이로그'·'인터뷰'로 보이지 않게
GENRES = [
    ("lesson", "레슨·강의", r"방법|하는 ?법|자세|연습|꿀팁|기본기|훈련|교정|강의|레슨|알려 ?드|배워 ?보|배우",
     r"[는은] ?법|비법|\d+ ?가지|이것만|초보|기초|팁|포인트|원리|제대로|정확도|알려|배우|잘 ?(차|하)는"),
    ("challenge", "챌린지·대결", r"챌린지|대결|vs|VS|이기면|벌칙|미션|내기|1대1|1:1|승부|상금",
     r"도전|성공|실패|몇 ?개|기록|지면|맞추기|\d+ ?번 ?(하면|안에|만에)|\d+ ?만 ?원"),
    ("review", "리뷰·언박싱", r"리뷰|언박싱|개봉|장단점|후기|추천템", r"축구화|신어|착용|가격|비교"),
    ("variety_talk", "예능·토크", r"토크|썰|몰카|상황극|예능", r"반응|ㅋㅋ|웃긴|레전드"),
    ("vlog", "브이로그", r"브이로그|vlog|VLOG|일상|먹방", r"하루|여행|같이"),
    ("match", "경기 하이라이트·분석", r"하이라이트|골 ?모음|전술|득점", r"경기|분석|리그|국가대표"),
    ("experiment", "실험", r"실험|측정", r"과연|진짜로|가능할까|테스트|속도|km"),
    ("interview", "인터뷰", r"인터뷰|게스트|초대", r"만나|질문|선수"),
]
GENRE_LABEL = {k: lb for k, lb, _, _ in GENRES}
COMBO = {frozenset(("variety_talk", "challenge")): "예능 챌린지", frozenset(("challenge", "experiment")): "챌린지 실험",
         frozenset(("lesson", "challenge")): "레슨·챌린지", frozenset(("variety_talk", "interview")): "예능 토크·인터뷰",
         frozenset(("lesson", "experiment")): "실험으로 보여 주는 레슨", frozenset(("review", "experiment")): "실험 리뷰",
         frozenset(("variety_talk", "vlog")): "예능 브이로그", frozenset(("match", "variety_talk")): "경기·예능 리액션"}
FUN_LABEL = {"slowmo_replay": "결정적 장면 슬로모션 리플레이", "replay": "결정적 장면 다시 보여 주기", "punch_zoom": "리액션·펀치라인 클로즈업 줌",
             "sfx": "효과음", "laugh": "현장 웃음소리 살림", "freeze": "정지 화면에 효과", "meme": "밈·짤 이미지 삽입",
             "shake": "화면 흔들기", "montage": "빠른 몽타주 전환", "reaction_cut": "리액션 컷", "cheer": "환호·박수 소리 살림"}


class PlanCancelled(Exception):
    pass


# ---------- 작은 도우미 ----------

def _np():
    import numpy as np
    return np


def pack(a, dtype="uint8"):
    np = _np()
    raw = np.ascontiguousarray(np.asarray(a).astype(dtype)).tobytes()
    return base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def unpack(s, dtype="uint8"):
    np = _np()
    if not s:
        return np.zeros(0, dtype)
    try:
        return np.frombuffer(zlib.decompress(base64.b64decode(s)), dtype).copy()
    except (ValueError, TypeError, zlib.error):
        return np.zeros(0, dtype)


def mmss(t):
    t = max(0, int(round(float(t))))
    return f"{t // 60:02d}:{t % 60:02d}"


def _norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", str(s or "")))


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _contained(cap, text):
    """자막 글자가 대사 안에 얼마나 들어 있는지 (0~1, 자막 길이 기준)."""
    a, b = _norm(cap), _norm(text)
    if not a or not b:
        return 0.0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(x.size for x in m.get_matching_blocks()) / len(a)


_POP = None


def _popcount8():
    global _POP
    if _POP is None:
        np = _np()
        _POP = np.array([bin(i).count("1") for i in range(256)], np.uint8)
    return _POP


def ham(a, b):
    """dHash 두 묶음(uint64) 사이 해밍 거리 행렬 (len(a)×len(b), uint8) — 큰 영상도 메모리를 적게 (줄 묶음씩)."""
    np = _np()
    a, b = np.asarray(a, np.uint64), np.asarray(b, np.uint64)
    out = np.zeros((len(a), len(b)), np.uint8)
    lut = _popcount8()
    for s in range(0, len(a), 256):
        x = np.bitwise_xor(a[s:s + 256, None], b[None, :])
        out[s:s + 256] = lut[x.view(np.uint8).reshape(x.shape + (8,))].sum(axis=-1, dtype=np.uint16).astype(np.uint8)
    return out


def dhash(gray):
    """흑백(H×W float) → 64비트 차이 지문 (9×8 로 줄여 옆 칸과 비교)."""
    np = _np()
    from PIL import Image
    im = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).resize((9, 8), Image.BOX)
    a = np.asarray(im, np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    v = 0
    for bit in bits:
        v = (v << 1) | int(bit)
    return v


def _runs(mask, gap=0):
    """참인 칸이 이어진 구간 [(a, b)] (b 는 마지막 칸 다음) — gap 칸까지 끊겨도 이어 붙임."""
    out = []
    for i, m in enumerate(mask):
        if not m:
            continue
        if out and i - out[-1][1] <= gap:
            out[-1][1] = i + 1
        else:
            out.append([i, i + 1])
    return [tuple(x) for x in out]


def _union_len(ivs):
    tot, cur = 0.0, None
    for a, b in sorted((float(a), float(b)) for a, b in ivs if b > a):
        if cur and a <= cur[1]:
            cur[1] = max(cur[1], b)
        else:
            if cur:
                tot += cur[1] - cur[0]
            cur = [a, b]
    return tot + (cur[1] - cur[0] if cur else 0.0)


def color_name(rgb):
    """RGB → 8색 이름 (흰/노랑/빨강/주황/파랑/초록/분홍/검정)."""
    import colorsys
    r, g, b = (max(0.0, min(255.0, float(x))) / 255 for x in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.22:
        return "black"
    # 노랑은 화질·색 압축으로 묽어져도 노랑 (style._snap 과 같은 뜻을 넓힘)
    if r > 0.66 and g > 0.58 and (b < 0.47 or b < 0.75 * min(r, g)) and 0.105 <= h <= 0.2:
        return "yellow"
    if s < 0.25:
        return "white" if v > 0.55 else "black"
    deg = h * 360
    if deg < 15 or deg >= 340:
        return "red"
    if deg < 40:
        return "orange"
    if deg < 72:
        return "yellow"
    if deg < 165:
        return "green"
    if deg < 260:
        return "blue"
    return "pink"


def text_color(rgb, px):
    """글자 상자(px: x0,y0,x1,y1) 안의 채움색 → (이름, hex, 상자 바탕 여부).
    글자 획의 경계(강한 가로 밝기 차이) 양쪽 화소만 보고 밝은 쪽·어두운 쪽으로 나눔 (뒤 배경 색에 덜 휘둘리게).
    테두리 자막은 획 안쪽이 밝으므로 밝은 쪽이 글자색 — 어두운 쪽이 확실히 더 진한 색이면 그쪽 (흰 상자 위 빨간 글씨 등).
    상자 안 화소 대부분이 한 가지 고른 색이면 상자 자막(boxed)."""
    np = _np()
    x0, y0, x1, y1 = (int(v) for v in px)
    c = np.asarray(rgb[max(0, y0):y1, max(0, x0):x1], np.float32)
    if c.size < 60 or c.shape[1] < 4:
        return "white", COLORS["white"], False
    g = c @ np.array([0.299, 0.587, 0.114], np.float32)
    d = np.diff(g, axis=1)
    st = np.abs(d) > 60
    if st.sum() < 12:
        nm = color_name(np.median(c.reshape(-1, 3), axis=0))
        return nm, COLORS[nm], False
    left, right = c[:, :-1][st], c[:, 1:][st]
    up = (d[st] > 0)[:, None]          # 오른쪽이 더 밝으면 오른쪽이 밝은 쪽
    br = np.where(up, right, left)
    dk = np.where(up, left, right)

    def sat(p):
        mx, mn = p.max(axis=1), p.min(axis=1)
        return float(np.median((mx - mn) / np.maximum(mx, 1)))
    allp = c.reshape(-1, 3)
    med = np.median(allp, axis=0)
    near = np.abs(allp - med).max(axis=1) < 28
    boxed = bool(near.mean() > 0.55 and float(g.std()) > 25)
    fill = dk if (sat(dk) > sat(br) + 0.3 and float(np.median(dk.max(axis=1))) > 110) else br
    nm = color_name(np.median(fill, axis=0))
    return nm, COLORS[nm], boxed


# ---------- 영상 하나 살펴보기 ----------

def _plan_file(name):
    return core.adir(name) / "plan_events.json"


def _model_state():
    """지금 쓸 수 있는 모델 (파일이 있어도 최근에 불러오기가 실패했으면 아님)."""
    import avmodels
    import face
    return {"ocr": avmodels.usable("ocr"), "audio": avmodels.usable("audio"), "faces": face.ready()}


def load_events(name, path=None, sig=None):
    """저장해 둔 기획 기록 — 파일이 그대로이고 판(PLAN_VER)이 같으며, 그때 없던 모델이 지금도 없을 때만. 아니면 None.
    sig 를 주면(학습용 영상을 배운 뒤 파일만 지움 · core.kept_sig) 그 지문과 비교하고, 다시 살펴볼 파일이 없으니 모델 확인은 건너뜀."""
    import style
    try:
        pe = json.loads(_plan_file(name).read_text(encoding="utf-8"))
        if not (isinstance(pe, dict) and pe.get("v") == PLAN_VER and pe.get("sig") == (sig if sig is not None else style._sig(path or core.video_file(name)))):
            return None
        if sig is not None:
            return pe
        # models 는 '그때 쓴 모델' (소리 없는 영상이어도 소리 모델을 썼으면 참 · 결과가 있었는지는 hasAudio)
        had, now = pe.get("models") or {}, _model_state()
        if any(now.get(k) and not had.get(k) for k in ("ocr", "audio")):
            return None  # 그때는 모델이 없었는데 지금은 있음 → 다시 살펴봄
        return pe
    except (OSError, ValueError):
        return None


def _save(name, pe):
    f = _plan_file(name)
    tmp = f.with_name(f"{f.name}.{os.getpid()}_{threading.get_ident()}.tmp")
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(pe, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for k in range(20):
            try:
                os.replace(tmp, f)
                return
            except PermissionError:
                if k == 19:
                    raise
                time.sleep(0.05)
    except OSError:  # 기록을 못 남겨도 배우기는 계속
        try:
            tmp.unlink()
        except OSError:
            pass


def _probe(path):
    """(길이 초, 화면 가로, 세로) — 세로 촬영(회전 정보)이면 가로·세로를 바꿈."""
    r = core.run([core.ffmpeg(), "-hide_banner", "-i", str(path)])
    err = r.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    v = re.search(r"Stream #[^\n]*Video:[^\n]*?(\d{2,5})x(\d{2,5})", err)
    w, h = (int(v[1]), int(v[2])) if v else (1920, 1080)
    if re.search(r"rotate\s*:\s*-?(90|270)|rotation of -?(90|270)", err):
        w, h = h, w
    return dur, w, h


def _title(name):
    try:
        import source
        return source._title_of(name)
    except Exception:  # noqa: BLE001
        return re.sub(r"\.[^.]+$", "", name)


def _transcript(name):
    try:
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        return [s for s in segs if isinstance(s, dict) and str(s.get("text") or "").strip()]
    except (OSError, ValueError):
        return []


def _frame_feats(rgb):
    """1초 한 장 → (dHash, 복잡도 0~1, 잔디·하늘 비율 0~1, 채도 0~1, 색 히스토그램 27칸, 흑백)."""
    np = _np()
    from PIL import Image
    g = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    h = dhash(g)
    im = Image.fromarray(rgb)
    sw = 160
    sh = max(8, int(round(rgb.shape[0] * sw / rgb.shape[1])))
    sm = np.asarray(im.resize((sw, sh), Image.BOX), np.float32)
    sg = sm @ np.array([0.299, 0.587, 0.114], np.float32)
    edge = (np.abs(np.diff(sg, axis=1))[:-1] + np.abs(np.diff(sg, axis=0))[:, :-1]) > 40
    flat = float(edge.mean())
    hsv = np.asarray(im.resize((sw, sh), Image.BOX).convert("HSV"), np.float32)
    H, S, V = hsv[..., 0] * 360 / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
    bot = slice(sh // 2, sh)
    grass = float(((H[bot] >= 70) & (H[bot] <= 160) & (S[bot] > 0.25) & (V[bot] > 0.15)).mean())
    top = slice(0, sh // 3)
    sky = float(((H[top] >= 185) & (H[top] <= 250) & (S[top] > 0.15) & (V[top] > 0.5)).mean())
    green = min(1.0, grass + 0.5 * sky)
    sat = float(S.mean())
    q = np.minimum(2, (sm / 86).astype(np.int32))
    hist = np.bincount((q[..., 0] * 9 + q[..., 1] * 3 + q[..., 2]).ravel(), minlength=27).astype(np.float32)
    return h, flat, green, sat, hist / max(1.0, hist.sum()), g


def _text_thr(sev):
    """style_events 의 글자 밀도(1초 2번 · 띠 6개) → (밀도 배열, 자막 문턱) — style._captions 와 같은 문턱."""
    np = _np()
    bh = np.array((sev or {}).get("text") or [], np.float32).reshape(-1, 6) / 255.0
    if not len(bh):
        return bh, 1.0
    base = float(np.median(bh[:, 1:4]))
    return bh, max(0.10, base * 2.2)


def _ocr_worker(q, out, stat):
    import avmodels
    while True:
        job = q.get()
        if job is None:
            return
        t, rgb = job
        t0 = time.time()
        try:
            lines = avmodels.ocr(rgb) or []
        except Exception:  # noqa: BLE001 — 한 장이 깨져도 계속
            lines = []
        stat["n"] += 1
        stat["sec"] += time.time() - t0
        keep = []
        for ln in lines:
            if not ocr_line_ok(ln):
                continue
            cn, hx, boxed = text_color(rgb, ln["px"])
            keep.append({"text": unicodedata.normalize("NFC", ln["text"]), "box": ln["box"], "h": ln["h"], "conf": ln["conf"],
                         "color": hx, "cname": cn, "boxed": boxed})
        out.append({"t": t, "d": 1, "lines": keep})


HANGUL = re.compile(r"[가-힣]")


def ocr_line_ok(ln):
    """무늬·간판을 글자로 잘못 읽은 줄 거르기: 화면 높이 35% 넘는 줄, 한글 없는 짧은 줄(확신 낮음)."""
    t = _norm(ln.get("text"))
    h = float(ln.get("h") or 0)
    if h > 0.35 or not t:
        return False
    if HANGUL.search(t):
        return True
    alnum = sum(ch.isalnum() for ch in t)
    if re.fullmatch(r"[\d:.,%/-]+", t):
        return alnum >= 2 and float(ln.get("conf") or 0) >= 0.8
    return alnum >= 2 and len(t) >= 2 and float(ln.get("conf") or 0) >= 0.85 and alnum >= 0.6 * len(t)


def _cancelled():
    import style
    return style._cancelled()


def _video_pass(path, dur, W, H, sev, use_ocr, use_faces, prog):
    """화면을 1초 한 장(640px)씩 한 번만 풀어서: 지문·복잡도·잔디·채도·색 묶음·(얼굴)·(글자 읽기)."""
    np = _np()
    ph = max(2, int(round(PW * H / max(1, W) / 2)) * 2)
    n = PW * ph * 3
    # 빠르게: 앞부분(인트로)만 1초마다 정확히 풀고, 본편은 키프레임만 풀어서 1초 간격으로 채움
    # (긴 영상에서 전부 푸는 데 대부분의 시간이 들었음 · 키프레임은 보통 2~5초마다 있음)
    full = dur if dur <= FULL_DECODE_MAX else FULL_DECODE_HEAD
    vf = ["-vf", f"fps=1,scale={PW}:{ph}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    parts = [[core.ffmpeg(), "-v", "error", "-i", str(path), "-t", f"{full:.3f}", *vf]]
    if dur > full:
        parts.append([core.ffmpeg(), "-v", "error", "-skip_frame", "nokey", "-ss", f"{full:.3f}", "-i", str(path), *vf])
    state = {"p": None}

    def frames():
        for cmd in parts:
            state["p"] = core.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            try:
                while True:
                    b = state["p"].stdout.read(n)
                    if len(b) < n:
                        break
                    yield b
            finally:
                state["p"].kill()
                state["p"].wait()
                state["p"].stdout.close()
    hashes, flats, greens, sats, hists, faces, nface = [], [], [], [], [], [], []
    bh, thr = _text_thr(sev)
    ocr_out, stat = [], {"n": 0, "sec": 0.0}
    q = th = None
    if use_ocr:
        q = queue.Queue(maxsize=6)  # 화면 풀기와 글자 읽기를 겹쳐서 (꽉 차면 풀기가 기다림 → 메모리 적게)
        th = threading.Thread(target=_ocr_worker, args=(q, ocr_out, stat), daemon=True)
        th.start()
    cap, sent, last_th, last_t, ext = OCR_CAP, 0, None, None, {}
    import face
    t = 0
    gen = frames()
    prev_face = None
    try:
        for b in gen:
            if _cancelled():
                raise PlanCancelled()
            rgb = np.frombuffer(b, np.uint8).reshape(ph, PW, 3)
            hsh, fl, gr, sa, hi, g = _frame_feats(rgb)
            hashes.append(hsh)
            flats.append(fl)
            greens.append(gr)
            sats.append(sa)
            hists.append(hi)
            if use_faces:
                if prev_face is not None and prev_face[0] == hsh:  # 같은 화면(키프레임을 채운 초)은 다시 안 찾음
                    faces.append(prev_face[1])
                    nface.append(prev_face[2])
                else:
                    try:
                        from PIL import Image
                        fs = [f for f in face._detect(Image.fromarray(rgb)) if f[3] - f[1] >= 0.08]
                        faces.append(max([f[3] - f[1] for f in fs] or [0.0]))
                        nface.append(len(fs))
                    except Exception:  # noqa: BLE001
                        faces.append(0.0)
                        nface.append(0)
                    prev_face = (hsh, faces[-1], nface[-1])
            if use_ocr:
                if stat["n"] >= 20 and stat["sec"] / stat["n"] > OCR_SLOW:
                    cap = OCR_CAP_SLOW  # 느린 PC: 덜 읽음
                if len(bh):
                    rows = bh[2 * t:2 * t + 2]
                    mx = rows.max(axis=0) if len(rows) else np.zeros(6, np.float32)
                    mask = mx > thr
                    if not mask.any() and t % 2 == 0:  # 획이 굵은 큰 글씨(예능 자막)는 경계가 성겨 밀도가 낮음 → 절반 문턱으로 2초마다
                        mask = mx > thr * 0.5
                else:  # 글자 밀도 기록이 없으면 모든 초가 후보 (상한·같은 화면 건너뛰기는 그대로)
                    mask = np.ones(6, bool)
                if mask.any():
                    bands = [k for k in range(6) if mask[k]]
                    y0, y1 = int(min(bands) * ph / 6), int((max(bands) + 1) * ph / 6)
                    tkey = (tuple(bands), dhash(g[y0:y1]))
                    same = last_th is not None and last_th[0] == tkey[0] and bin(last_th[1] ^ tkey[1]).count("1") <= OCR_SAME
                    budget = cap * min(1.0, (t + 1) / max(1.0, dur)) + 15  # 앞에서 다 쓰지 않게 고르게
                    if same and last_t is not None:
                        ext[last_t] += 1  # 같은 자막이 이어짐 → 마지막으로 읽은 장을 늘림
                    elif sent < cap and sent < budget:
                        q.put((t, rgb.copy()))
                        ext[t], last_t, last_th = 1, t, tkey
                        sent += 1
                    else:
                        last_t = last_th = None
                else:
                    last_t = last_th = None
            t += 1
            if t % 15 == 0:
                prog(min(70, int(t * 70 / max(1.0, dur))), f"기획 분석 · 화면 살펴보는 중 ({t}/{int(dur)}초)"
                     + (f" · 화면 글자 {stat['n']}/{sent}장" if use_ocr else ""))
    finally:
        gen.close()
        if q is not None:
            if _cancelled():  # 남은 글자 읽기는 버림
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
            q.put(None)
            while th.is_alive():
                th.join(0.5)
                if use_ocr and not _cancelled():
                    prog(72, f"기획 분석 · 화면 글자 읽는 중 ({stat['n']}/{sent})")
    for o in ocr_out:  # 같은 자막이라 건너뛴 초만큼 늘림
        o["d"] = ext.get(o["t"], o["d"])
    ocr_out.sort(key=lambda o: o["t"])
    return {"hash": hashes, "flat": flats, "green": greens, "sat": sats, "hist": hists,
            "faces": faces if use_faces else None, "nface": nface if use_faces else None,
            "ocr": ocr_out if use_ocr else None, "ocr_sec": stat["sec"], "ocr_n": stat["n"]}



def _audio16(path):
    """16kHz 흑백 소리(int16 numpy) — 없으면 빈 배열."""
    np = _np()
    cmd = [core.ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    p = core.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    chunks = []
    try:
        while True:
            b = p.stdout.read(16000 * 2 * 10)
            if not b:
                break
            chunks.append(b)
            if _cancelled():
                raise PlanCancelled()
    finally:
        p.kill()
        p.wait()
        p.stdout.close()
    raw = b"".join(chunks)
    return np.frombuffer(raw[:len(raw) // 2 * 2], np.int16)


def _audio_tags(wave, prog):
    """YAMNet 으로 0.48초마다 묶음 점수 — 60초씩 (멈추기·진행 표시)."""
    np = _np()
    import avmodels
    step = avmodels.Y_CHUNK * avmodels.Y_SR
    parts = {k: [] for k in ("speech", "laugh", "cheer", "music", "sfx")}
    n = len(wave)
    for a in range(0, max(1, n), step):
        if _cancelled():
            raise PlanCancelled()
        seg = wave[a:a + step].astype(np.float32) / 32768.0
        r = avmodels.tags(seg)
        if r is None:
            return None
        for k in parts:
            parts[k].append(r[k])
        prog(75 + int(15 * min(1.0, (a + step) / max(1, n))), f"기획 분석 · 웃음소리·효과음 듣는 중 ({min(n, a + step) // 16000}/{n // 16000}초)")
    return {k: np.concatenate(v) if v else np.zeros(0, np.float32) for k, v in parts.items()}


# ---------- 화자 수 (MFCC + 피치, 어림) ----------

_MEL = {}


def _melfb(sr=16000, nfft=512, nmel=26):
    np = _np()
    key = (sr, nfft, nmel)
    if key not in _MEL:
        mel = lambda f: 2595 * np.log10(1 + f / 700)  # noqa: E731
        imel = lambda m: 700 * (10 ** (m / 2595) - 1)  # noqa: E731
        pts = imel(np.linspace(mel(60), mel(sr / 2 - 200), nmel + 2))
        bins = np.floor((nfft + 1) * pts / sr).astype(int)
        fb = np.zeros((nmel, nfft // 2 + 1), np.float32)
        for m in range(1, nmel + 1):
            a, c, b = bins[m - 1], bins[m], bins[m + 1]
            for k in range(a, c):
                fb[m - 1, k] = (k - a) / max(1, c - a)
            for k in range(c, b):
                fb[m - 1, k] = (b - k) / max(1, b - c)
        n = np.arange(nmel)
        dct = np.cos(np.pi / nmel * (n[None, :] + 0.5) * np.arange(13)[:, None]).astype(np.float32)
        _MEL[key] = (fb, dct)
    return _MEL[key]


def voice_feats(x, sr=16000):
    """말 한 토막(float) → {m: MFCC 1~12 평균, s: 표준편차, f0: 피치 중앙값(Hz, 모르면 0)}. 너무 짧거나 조용하면 None."""
    np = _np()
    fl, hop = 400, 160
    if len(x) < fl + hop * 30:
        return None
    n = 1 + (len(x) - fl) // hop
    idx = np.arange(fl)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx] * np.hamming(fl).astype(np.float32)
    spec = np.abs(np.fft.rfft(fr, 512)) ** 2
    fb, dct = _melfb(sr)
    lm = np.log(spec @ fb.T + 1e-8)
    e = lm.mean(axis=1)
    keep = e > np.percentile(e, 40)  # 말소리가 있는 칸만
    if keep.sum() < 15:
        return None
    cc = (lm[keep] @ dct.T)[:, 1:13]
    f0s = []
    for f in fr[keep][::3][:60]:
        r = np.fft.irfft(np.abs(np.fft.rfft(f, 1024)) ** 2)[:240]
        if r[0] <= 0:
            continue
        lo, hi = sr // 400, sr // 80
        k = lo + int(np.argmax(r[lo:hi]))
        if r[k] / r[0] > 0.3:
            f0s.append(sr / k)
    return {"m": cc.mean(axis=0), "s": cc.std(axis=0), "f0": float(np.median(f0s)) if len(f0s) >= 3 else 0.0}


SPK_THR = 0.9      # 묶음 사이 거리(토막 안 흔들림 단위의 평균 제곱근)가 이보다 크면 다른 사람
F0_UNIT = 0.15     # 피치 15% 차이 ≈ 거리 1


def voice_matrix(vf):
    """토막별 목소리 특징 → 거리 계산용 행렬: MFCC 평균을 '토막 안 흔들림(표준편차)의 중앙값'으로 나눔 (데이터에 따라 늘어나지 않는 고정 눈금)
    + log 피치 / F0_UNIT (피치를 모르는 토막은 다른 토막들의 중앙값)."""
    np = _np()
    S = np.median(np.array([v["s"] for v in vf]), axis=0)
    M = np.array([v["m"] for v in vf]) / np.maximum(S, 1e-3)
    f0 = np.array([v["f0"] for v in vf], np.float64)
    ok = f0 > 0
    lf = np.log(np.where(ok, f0, 1.0))
    lf[~ok] = float(np.median(lf[ok])) if ok.any() else 0.0
    return np.concatenate([M, (lf / F0_UNIT)[:, None]], axis=1)


def cluster(X, thr=SPK_THR, min_share=0.08, weights=None):
    """토막 특징 행렬 → 묶음 번호 목록 (평균 제곱근 거리 · 평균 연결 응집 군집, 거리 thr 에서 자름).
    weights(토막 길이) 합의 min_share 가 안 되는 묶음은 가장 가까운 큰 묶음으로 보냄 (기침·효과음 같은 잡음). 번호는 큰 묶음부터 0."""
    np = _np()
    X = np.asarray(X, np.float64)
    n = len(X)
    if n == 0:
        return []
    if n == 1:
        return [0]
    w = np.asarray(weights if weights is not None else np.ones(n), np.float64)
    D = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).mean(axis=-1))
    np.fill_diagonal(D, np.inf)
    groups = {i: [i] for i in range(n)}
    size = {i: 1 for i in range(n)}
    active = list(range(n))
    while len(active) > 1:
        sub = D[np.ix_(active, active)]
        k = int(np.argmin(sub))
        if not np.isfinite(sub.flat[k]) or sub.flat[k] > thr:
            break
        i, j = active[k // len(active)], active[k % len(active)]
        D[i, :] = (D[i, :] * size[i] + D[j, :] * size[j]) / (size[i] + size[j])
        D[:, i] = D[i, :]
        D[i, i] = np.inf
        D[j, :] = np.inf
        D[:, j] = np.inf
        groups[i] += groups.pop(j)
        size[i] += size.pop(j)
        active.remove(j)
    lab = np.zeros(n, int)
    for g, (_, mem) in enumerate(sorted(groups.items(), key=lambda kv: -w[kv[1]].sum())):
        lab[mem] = g
    tot = w.sum()
    big = [g for g in range(lab.max() + 1) if w[lab == g].sum() >= min_share * tot] or [0]
    cent = {g: X[lab == g].mean(axis=0) for g in big}
    for i in range(n):
        if lab[i] not in big:
            lab[i] = min(big, key=lambda g: float(((X[i] - cent[g]) ** 2).mean()))
    rem = {g: k for k, g in enumerate(sorted(set(lab.tolist()), key=lambda g: -w[lab == g].sum()))}
    return [rem[g] for g in lab.tolist()]


def speakers(wave, segs, sr=16000):
    """받아쓰기 구간마다 목소리 특징 → 화자 수 어림 {n, share, turnsPerMin, method}. 구간이 적으면 None."""
    np = _np()
    if wave is None or not len(wave) or not segs:
        return None
    vf, wts, order = [], [], []
    for k, s in enumerate(segs):
        a, b = float(s.get("start", 0)), float(s.get("end", 0))
        if b - a < 0.8:
            continue
        m = (a + b) / 2
        a, b = max(a, m - 3), min(b, m + 3)  # 가운데 6초까지만
        x = wave[int(a * sr):int(b * sr)].astype(np.float32) / 32768.0
        f = voice_feats(x, sr)
        if f is not None:
            vf.append(f)
            wts.append(float(s["end"]) - float(s["start"]))
            order.append(k)
        if len(vf) >= 400:
            break
    if len(vf) < 4:
        return None
    lab = cluster(voice_matrix(vf), weights=wts)
    n = max(lab) + 1
    tot = sum(wts)
    share = [round(sum(w for w, g in zip(wts, lab) if g == i) / tot, 2) for i in range(n)]
    turns = sum(1 for x, y in zip(lab, lab[1:]) if x != y)
    span = max(1.0, float(segs[order[-1]]["end"]) - float(segs[order[0]]["start"]))
    return {"n": n, "share": share, "turnsPerMin": round(turns * 60 / span, 2), "method": "mfcc"}


def extract_plan(name, sev=None, log=print, label="스타일 배우는 중", step=None):
    """영상 하나의 기획 신호 기록 (analysis/<영상>/plan_events.json, 파일이 그대로면 다시 안 봄)."""
    import avmodels
    import face
    import style
    import thumb
    path = core.video_file(name)  # 편집용 보관함 → 학습용 영상(refs)
    if not path.is_file():
        kept = core.kept_sig(name)  # 배운 뒤 파일만 지운 학습용 영상: 남겨 둔 기록을 그대로
        pe = load_events(name, sig=kept) if kept else None
        if pe is not None:
            log("  기획 분석: 영상 파일은 지웠지만 예전에 살펴본 기록을 그대로 써요")
            return pe
        raise FileNotFoundError(f"영상을 찾지 못했어요 · {name}")
    pe = load_events(name, path)
    if pe is not None:
        log("  기획 분석: 예전에 살펴본 기록을 그대로 써요")
        return pe
    sev = sev or style.extract_events(name, log, label)

    def prog(pct, detail):
        kw = {"label": label, "item": name, "pct": pct, "detail": detail}
        if step:
            kw["step"] = step
        core.set_progress(**kw)
    prog(0, "기획 분석 준비 중")
    try:  # 처음 한 번 내려받는 중에도 ✕ 로 멈춤
        use_ocr = avmodels.ensure("ocr", item=name, label=label, cancel=_cancelled)
        use_audio = avmodels.ensure("audio", item=name, label=label, cancel=_cancelled)
    except thumb.DownloadCancelled:
        raise PlanCancelled() from None
    use_faces = face.ready() and face.ensure(item=name, label=label)
    dur, W, H = _probe(path)
    dur = dur or float(sev.get("duration") or 0) or 1.0
    t0 = time.time()
    vp = _video_pass(path, dur, W, H, sev, use_ocr, use_faces, prog)
    if not vp["hash"]:
        raise RuntimeError(f"영상 화면을 읽지 못했어요 · {name}")
    segs = _transcript(name)
    audio, spk = None, None
    if use_audio or segs:
        prog(74, "기획 분석 · 소리 풀어 보는 중")
        wave = _audio16(path)
        if use_audio and len(wave):
            tg = _audio_tags(wave, prog)
            if tg is not None:
                audio = {"step": 0.48, **{k: pack(_np().clip(v * 255, 0, 255)) for k, v in tg.items()}}
        if segs:
            prog(92, "기획 분석 · 말하는 사람 수 어림 중")
            try:
                spk = speakers(wave, segs)
            except Exception:  # noqa: BLE001 — 어림이라 실패해도 계속
                spk = None
        del wave
    np = _np()
    pe = {"v": PLAN_VER, "sig": style._sig(path), "source": name, "duration": round(dur, 3), "analyzed": time.strftime("%Y-%m-%d %H:%M"),
          "models": {"ocr": bool(use_ocr), "audio": bool(use_audio), "faces": bool(use_faces)}, "hasAudio": audio is not None,
          "title": _title(name),
          "size": [W, H],
          "hash": {"fps": 1, "data": pack(np.array(vp["hash"], np.uint64), "uint64")},
          "flat": pack(np.clip(np.array(vp["flat"]) * 255, 0, 255)), "green": pack(np.clip(np.array(vp["green"]) * 255, 0, 255)),
          "sat": pack(np.clip(np.array(vp["sat"]) * 255, 0, 255)),
          "hist": pack(np.clip(np.array(vp["hist"]) * 255, 0, 255).reshape(-1)),
          "ocr": vp["ocr"], "audio": audio, "speakers": spk,
          "faces": pack(np.clip(np.array(vp["faces"]) * 255, 0, 255)) if vp["faces"] is not None else None,
          "nface": pack(np.clip(np.array(vp["nface"]), 0, 255)) if vp["nface"] is not None else None,
          "took": round(time.time() - t0, 1), "ocrSec": round(vp["ocr_sec"], 1), "ocrN": vp["ocr_n"]}
    try:
        d = detect(pe, sev, segs)
        pe["ev"], pe["caps"] = d["ev"], d["caps"]
    except Exception:  # noqa: BLE001 — 사건 기록은 확인용 (판단은 다시 계산함)
        pass
    _save(name, pe)
    log(f"  기획 분석 · 화면 {len(vp['hash'])}장 · 글자 읽기 {vp['ocr_n']}장 · {pe['took']}초"
        + ("" if use_ocr else " · 글자 읽기 모델 없음(어림)")
        + ("" if audio is not None else " · 소리 모델 없음(어림)" if not use_audio else " · 소리 없는 영상"))
    prog(100, "기획 분석 완료")
    return pe


# ---------- 신호 → 사건 ----------

def _signals(pe):
    np = _np()
    n = len(unpack((pe.get("hash") or {}).get("data"), "uint64"))
    hs = unpack((pe.get("hash") or {}).get("data"), "uint64")
    f = lambda k: unpack(pe.get(k)).astype(np.float32) / 255.0 if pe.get(k) else None  # noqa: E731
    hist = unpack(pe.get("hist")).astype(np.float32) / 255.0
    hist = hist.reshape(-1, 27) if hist.size and hist.size % 27 == 0 else np.zeros((n, 27), np.float32)
    au = pe.get("audio") or None
    audio = None
    if au:
        audio = {k: unpack(au.get(k)).astype(np.float32) / 255.0 for k in ("speech", "laugh", "cheer", "music", "sfx")}
        audio["step"] = float(au.get("step") or 0.48)
    nf = unpack(pe.get("nface")).astype(np.int32) if pe.get("nface") else None
    return {"n": n, "hash": hs, "flat": f("flat"), "green": f("green"), "sat": f("sat"), "hist": hist,
            "faces": f("faces"), "nface": nf, "audio": audio}


def _aud_at(audio, key, a, b):
    """소리 묶음 점수의 [a, b] 초 평균 (모르면 None)."""
    if not audio or key not in audio or not len(audio[key]):
        return None
    st = audio["step"]
    i, j = int(a / st), max(int(a / st) + 1, int(math.ceil(b / st)))
    seg = audio[key][i:j]
    return float(seg.mean()) if len(seg) else None


def _spans(arr, step, thr, gap=1.0, min_len=0.0):
    out = []
    for a, b in _runs(arr >= thr, gap=int(round(gap / step))):
        if (b - a) * step >= min_len:
            out.append((round(a * step, 2), round(b * step, 2)))
    return out


def _talk_ivs(segs):
    return [(float(s["start"]), float(s["end"])) for s in segs if float(s.get("end", 0)) > float(s.get("start", 0))]


def _text_near(segs, a, b):
    """[a, b] 근처 받아쓰기 글자 (단어 시각이 있으면 단어, 없으면 구간)."""
    out = []
    for s in segs:
        if float(s["end"]) < a or float(s["start"]) > b:
            continue
        ws = s.get("words") or []
        if ws:
            out += [str(w.get("w") or "") for w in ws if a <= float(w.get("s", 0)) <= b or a <= float(w.get("e", 0)) <= b]
        else:
            out.append(str(s["text"]))
    return " ".join(out)


QUOTE_BIG = 0.10   # 대사를 옮긴 글자가 이만큼 크면 (아래쪽 흰 글씨여도) 강조 — 보통 말 자막은 화면 높이의 5~8%


def _emph_look(t, h, cname, pos):
    """대사를 옮긴 글자의 모양이 예능 강조 자막인지: 아주 큰 글씨 · 아래가 아닌 곳의 색 글씨/중간 크기 · 큰 색 글씨 또는 색 글씨에 '!?'.
    아래쪽의 보통 크기 흰 글씨(또는 늘 같은 색의 작은 글씨)는 말 자막."""
    colored = cname not in ("white", "black")
    return (h >= QUOTE_BIG or (pos != "bottom" and (colored or h >= SMALL_H))
            or (colored and (h >= BIG_H + 0.02 or "!" in t or "?" in t)))


def classify_caption(text, h=0.05, cname="white", pos="bottom", box=None, dur=1.0, speech_sim=None, has_transcript=True):
    """자막 사건 하나 → 종류 (info · speech · sfx · inner · situ · emph). 모양을 먼저 보고 그다음 말과 같은지를 봄:
    말을 그대로 옮겼어도 크게·색으로·가운데에 띄운 글자는 예능 강조(emph) — 한국 예능 자막의 대부분이 이런 '따라 말하기' 강조라서.
    'speech' 는 아래쪽의 작은(대개 흰) 줄이 대사를 따라갈 때만."""
    t = unicodedata.normalize("NFC", str(text or "")).strip()
    tn = _norm(t)
    x0, y0, x1, y1 = box or (0.3, 0.8, 0.7, 0.9)
    corner = (x0 < 0.25 or x1 > 0.75) and (y0 < 0.25 or y1 > 0.75) and (x1 - x0) < 0.5
    if INFO_NAME.search(t):
        return "info"
    if (len(tn) <= 4 and SFX_WORD.match(tn)) or REPEAT.match(tn):
        return "sfx"
    if speech_sim is not None and speech_sim >= 0.6:
        return "emph" if _emph_look(t, h, cname, pos) else "speech"
    if (INFO_NUM.search(t) and len(tn) <= 14) or re.fullmatch(r"[\d:.,%/~-]+", tn) or (h < SMALL_H and corner and dur >= 3):
        return "info"
    if not has_transcript and pos == "bottom" and cname == "white" and dur >= 2 and h < BIG_H:
        return "speech"
    if INNER.search(t):
        return "inner"
    if SITU.search(tn) and len(tn) >= 3:
        return "situ"
    if h >= BIG_H or cname != "white" or "!" in t or "?" in t:
        return "emph"
    return "speech" if pos == "bottom" else "situ"


def group_captions(ocr, segs, duration):
    """OCR 줄들 → 자막 사건 [{a, b, text, kind, h, cname, pos, box, sim}] (이어지는 초의 같은 글자·겹치는 상자는 하나로)."""
    events = []
    for o in sorted(ocr or [], key=lambda o: o["t"]):
        t, d = float(o["t"]), float(o.get("d") or 1)
        for ln in o.get("lines") or []:
            txt = str(ln.get("text") or "").strip()
            if len(_norm(txt)) < 1:
                continue
            bx = ln.get("box") or [0, 0, 1, 1]
            hit = None
            for e in reversed(events[-40:]):
                if t - e["b"] > 2.5:
                    continue
                ob = e["box"]
                ov = min(bx[2], ob[2]) - max(bx[0], ob[0]) > 0 and min(bx[3], ob[3]) - max(bx[1], ob[1]) > 0
                if ov and _sim(txt, e["text"]) >= 0.8:
                    hit = e
                    break
            if hit:
                hit["b"] = max(hit["b"], t + d)
                hit["obs"].append(ln)
                if len(_norm(txt)) > len(_norm(hit["text"])):
                    hit["text"] = txt
            else:
                events.append({"a": t, "b": t + d, "text": txt, "box": list(bx), "obs": [ln]})
    out = []
    for e in events:
        hs = sorted(float(x.get("h") or 0) for x in e["obs"])
        names = [x.get("cname") or "white" for x in e["obs"]]
        cn = max(set(names), key=names.count)
        cy = (e["box"][1] + e["box"][3]) / 2
        pos = "top" if cy < 0.33 else "middle" if cy < 0.66 else "bottom"
        h = hs[len(hs) // 2]
        sim = round(_contained(e["text"], _text_near(segs, e["a"] - 2, e["b"] + 2)), 2) if segs else None
        boxed = sum(bool(x.get("boxed")) for x in e["obs"]) > len(e["obs"]) / 2
        kind = classify_caption(e["text"], h, cn, pos, e["box"], e["b"] - e["a"], sim, bool(segs))
        out.append({"a": round(e["a"], 2), "b": round(min(duration or e["b"], e["b"]), 2), "text": e["text"][:40], "kind": kind,
                    "h": round(h, 4), "cname": cn, "pos": pos, "box": [round(v, 3) for v in e["box"]], "sim": sim, "boxed": boxed,
                    "quoted": bool(sim is not None and sim >= 0.6)})
    return out


def detect(pe, sev, segs):
    """기록(신호) → 사건 {ev, caps, aux}. 순수 계산 (시험하기 쉽게)."""
    np = _np()
    S = _signals(pe)
    n = S["n"]
    dur = float(pe.get("duration") or n or 1)
    sev = sev or {}
    cuts = sorted(float(c) for c in sev.get("cuts") or [])
    zooms = [float(z["t"]) for z in sev.get("zooms") or []]
    motion = np.array(sev.get("motion") or [], np.float32)   # 1초 2번
    rms = np.array(sev.get("rms") or [], np.float32)         # 0.1초마다 dB
    rstep = float(sev.get("rmsStep") or 0.1)
    flat = S["flat"] if S["flat"] is not None else np.full(n, 0.2, np.float32)
    sat = S["sat"] if S["sat"] is not None else np.full(n, 0.2, np.float32)
    H = ham(S["hash"], S["hash"]) if n else np.zeros((0, 0), np.uint8)
    idx = np.arange(n)
    far = np.abs(idx[:, None] - idx[None, :]) > 3
    common = ((H <= TEASER_HAM) & far).mean(axis=1) if n else np.zeros(0)
    simple = flat <= max(0.02, float(np.percentile(flat, 10)) if n else 0.02)
    usable = (common < COMMON_FRAC) & ~simple
    caps = group_captions(pe.get("ocr"), segs, dur) if pe.get("ocr") is not None else None
    ev = {k: [] for k in ("teaser", "title", "freeze", "replay", "insert", "shake", "montage", "laugh", "cheer", "sfx", "punchZoom", "reaction")}
    # 인트로 창
    W = int(max(INTRO_MIN, min(INTRO_MAX, dur * INTRO_FRAC)))
    W = min(W, n)
    # 같은 장면(컷 하나 안)인지: style_events 의 컷이 있으면 그것으로, 없으면 그 사이에 확 다른 화면이 있었는지로
    def shot_of(t):
        return bisect.bisect_right(cuts, t + 0.5)

    def jumped(i, j):
        mid = H[i, i + 1:j] if j > i else H[i, j + 1:i]
        return bool(len(mid) and (mid > 20).any())

    def separated(i, j, loose=False):
        """i 초와 j 초 화면 사이에 진짜 장면 전환이 있었는지 (한 장면이 이어진 것을 '다시 나옴'으로 보지 않게).
        컷 기록이 있으면 컷으로 (loose 면 컷을 놓쳤어도 사이에 확 다른 화면이 있었으면 인정), 없으면 화면 지문으로."""
        if cuts:
            return shot_of(i) != shot_of(j) or (loose and jumped(i, j))
        return jumped(i, j)
    greet0 = next((float(s["start"]) for s in segs or [] if float(s["start"]) <= 40 and GREETING.search(str(s["text"]))), None)
    outro = dur - min(30.0, 0.08 * dur)   # 끝인사 자리(처음과 같은 자리에서 찍는 경우가 많음)는 티저 원본으로 보지 않음
    # 티저: 인트로 첫머리의 짧은 화면이 장면 전환 뒤 본편에 다시 나옴
    #  (한 장면이 인트로 창 너머로 이어진 것 · 처음과 끝을 같은 자리에서 찍은 것(A-B-A)은 티저가 아님)
    tz = np.zeros(n, bool)
    tz_src = {}
    for i in range(W):
        if not usable[i]:
            continue
        lo = max(i + 20, W)
        if lo >= n:
            continue
        js = [int(j) for j in np.flatnonzero(H[i, lo:] <= TEASER_HAM) + lo if j < outro]
        js = [j for j in js if separated(i, j)]
        if js:
            tz[i] = True
            tz_src[i] = min(js, key=lambda j: int(H[i, j]))
    chain_end = None
    for a, b in _runs(tz, gap=2):
        if b - a < 2 or b - a > TEASER_BLOCK_MAX:
            continue
        first = a <= TEASER_START_MAX or (chain_end is not None and a - chain_end <= 3)
        if not first or a > TEASER_CHAIN_MAX:
            continue
        if greet0 is not None and a >= greet0 - 0.5:  # 인사 뒤에 나오면 티저(맨 앞 미리 보기)가 아님
            continue
        if greet0 is not None and b > greet0 + 1:  # 인사하는 동안 이어진 화면 → 인사 장면 (처음·끝을 같은 자리에서 찍은 것)
            continue
        ev["teaser"].append([a, b, tz_src.get(a, tz_src.get(b - 1, 0))])
        chain_end = b
    tz = np.zeros(n, bool)
    for a, b, _ in ev["teaser"]:
        tz[a:b] = True
    # 리플레이(+슬로): 앞에서 본 장면을 장면 전환 뒤 순서대로 다시 (짧게 · 너무 느리면 정지한 화면이 같은 것뿐)
    rp = np.zeros(n, bool)
    rp_src = {}
    for i in range(W, n):
        if not usable[i]:
            continue
        js = np.flatnonzero(H[i, :max(0, i - 5)] <= REPLAY_HAM)
        for j in js[::-1]:
            if tz[j]:  # 인트로 티저는 원본이 아니라 미리 보기 (본편 원본을 리플레이로 잘못 보지 않게)
                continue
            if separated(int(j), i, loose=True):  # 리플레이 앞에는 짧은 전환 효과가 많아 컷을 놓칠 수 있음 (길이·느리기 제한은 따로)
                rp[i] = True
                rp_src[i] = int(j)
                break
    for a, b in _runs(rp, gap=1):
        if b - a < 2 or b - a > REPLAY_MAX:
            continue
        src = [rp_src[k] for k in range(a, b) if k in rp_src]
        span = max(src) - min(src) + 1
        ratio = round((b - a) / span, 2)
        if ratio > REPLAY_SLOW_MAX:  # 같은 화면 한두 장이 길게 이어짐 → 같은 자리로 돌아온 고정 화면이지 느린 리플레이가 아님
            continue
        ev["replay"].append([a, b, min(src), max(src) + 1, ratio])
    # 타이틀 카드: 큰 글씨가 가운데에 · 움직임이 적게 1초 이상 / 단순한 그래픽 화면
    mot_at = lambda t: float(motion[min(len(motion) - 1, int(t * 2))]) if len(motion) else 0.0  # noqa: E731
    for o in pe.get("ocr") or []:
        for ln in o.get("lines") or []:
            bx = ln.get("box") or [0, 0, 0, 0]
            cx = (bx[0] + bx[2]) / 2
            span = motion[int(o["t"] * 2) + 1:int((o["t"] + max(1, o.get("d") or 1)) * 2)] if len(motion) else None
            still = float(np.median(span)) < 6 if span is not None and len(span) else mot_at(o["t"]) < 6
            if float(ln.get("h") or 0) >= TITLE_H and 0.3 <= cx <= 0.7 and still and o["t"] < max(W, 30):
                ev["title"].append([o["t"], o["t"] + max(1, int(o.get("d") or 1)), ln["text"][:30]])
                break
    gfx = (flat < 0.04) & (sat > 0.35)
    for a, b in _runs(gfx[:W], gap=0):
        if b - a >= 1 and not any(t[0] <= a < t[1] for t in ev["title"]):
            ev["title"].append([a, b, ""])
    ev["title"].sort()
    # 정지 화면: 움직이다 멈춤 (컷 아님) · 그동안 소리는 계속
    if len(motion):
        still = motion < 0.3
        for a, b in _runs(still):
            if b - a < 2 or a == 0:
                continue
            ta, tb = a / 2, b / 2
            if motion[a - 1] < 3 or any(abs(c - ta) <= 0.6 for c in cuts):
                continue
            loud = rms[int(ta / rstep):int(tb / rstep)]
            if len(loud) and float(loud.mean()) > -45:
                ev["freeze"].append([ta, tb])
    # 화면 흔들기: 컷 없이 0.5~2초 크게 움직였다가 처음 화면으로 돌아옴
    if len(motion) and n:
        for a, b in _runs(motion > 12):
            ta, tb = a / 2, b / 2
            if not 0.5 <= tb - ta <= 2.0 or any(ta - 0.5 <= c <= tb + 0.5 for c in cuts):
                continue
            i, j = int(ta) - 1, int(math.ceil(tb)) + 1
            if 0 <= i < n and j < n and H[i, j] <= REPLAY_HAM:
                ev["shake"].append([ta, tb])
    # 소리: 웃음·환호·음악·효과음
    au = S["audio"]
    talk = _talk_ivs(segs)
    if au:
        st = au["step"]
        ev["laugh"] = [list(x) for x in _spans(au["laugh"], st, 0.3, gap=1.0)]
        ev["cheer"] = [list(x) for x in _spans(au["cheer"], st, 0.3, gap=1.0)]
        m = (au["sfx"] >= 0.25) & (au["speech"] < 0.5)
        ev["sfx"] = [round(a * st, 2) for a, b in _runs(m, gap=1)]
    elif len(rms):
        k = int(round(2 / rstep))
        pad = np.pad(rms, k, mode="edge")
        from numpy.lib.stride_tricks import sliding_window_view
        med = np.median(sliding_window_view(pad, 2 * k + 1), axis=1)[:len(rms)]
        hot = (rms - med >= 12) & (rms > -35)
        for a, b in _runs(hot):
            ta = a * rstep
            if (b - a) * rstep < 0.6 and not any(s - 0.1 <= ta <= e + 0.1 for s, e in talk):
                ev["sfx"].append(round(ta, 2))
    # 빠른 몽타주: 6초 안에 컷 5번 이상 · 음악 · 말이 적음
    for i, c in enumerate(cuts):
        win = [x for x in cuts[i:] if x - c <= 6]
        if len(win) < 5:
            continue
        a, b = c, c + 6
        if ev["montage"] and a < ev["montage"][-1][1]:
            ev["montage"][-1][1] = max(ev["montage"][-1][1], win[-1])
            continue
        mus = _aud_at(au, "music", a, b)
        talk_cov = _union_len([(max(a, s), min(b, e)) for s, e in talk]) / 6
        musical = mus > 0.3 if mus is not None else (float(rms[int(a / rstep):int(b / rstep)].mean()) > -30 if len(rms) else False)
        if musical and talk_cov < 0.3:
            ev["montage"].append([round(a, 2), round(win[-1], 2)])
    # 밈·외부 이미지 삽입: 짧은 컷이 영상의 나머지와 전혀 다른 화면
    if n:
        mean_hist = S["hist"].mean(axis=0) if len(S["hist"]) else None
        edges = [0.0] + cuts + [dur]
        for a, b in zip(edges, edges[1:]):
            if b - a > 4 or b - a < 0.4:
                continue
            secs = [s for s in range(int(math.ceil(a)), int(math.floor(b)) + 1) if s < n and a <= s < b]
            if not secs:
                continue
            good = 0
            for s in secs:
                outside = np.ones(n, bool)
                outside[max(0, int(a) - 2):min(n, int(b) + 3)] = False
                if not outside.any():
                    continue
                far_ok = int(H[s, outside].min()) >= INSERT_HAM
                chi = 0.0
                if mean_hist is not None:
                    p, q2 = S["hist"][s], mean_hist
                    chi = float(0.5 * (((p - q2) ** 2) / (p + q2 + 1e-6)).sum())
                big_text = any(o["t"] == s and any(float(x.get("h") or 0) >= TITLE_H for x in o.get("lines") or []) for o in pe.get("ocr") or [])
                graphic = (flat[s] < 0.06 and sat[s] > 0.35) or (big_text and flat[s] < 0.1)
                if far_ok and chi > 0.5 and graphic:
                    good += 1
            if good * 2 >= len(secs) and not (a < W and any(t[0] <= a < t[1] for t in ev["title"])):
                ev["insert"].append([round(a, 2), round(b, 2)])
    # 리액션·펀치라인 줌 / 리액션 컷
    moments = [x[0] for x in ev["laugh"]] + list(ev["sfx"]) + [c["a"] for c in caps or [] if c["kind"] in ("emph", "sfx")]
    moments.sort()
    for z in zooms:
        if any(m - 1 <= z <= m + 3 for m in moments):
            ev["punchZoom"].append(round(z, 2))
    if S["faces"] is not None and len(S["faces"]):
        fh = S["faces"]
        done = set()
        for m in moments:
            for c in cuts:
                if m < c <= m + 3 and c not in done:
                    s = min(len(fh) - 1, int(c) + 1)
                    quiet = not any(x0 <= c + 1 <= x1 for x0, x1 in talk)
                    if fh[s] >= 0.3 and quiet:
                        ev["reaction"].append(round(c, 2))
                        done.add(c)
    return {"ev": ev, "caps": caps, "aux": {"W": W, "usable": usable, "common": common}}


# ---------- 판단 ----------

def _conf(x, lo=0.3, hi=0.95):
    return round(min(hi, max(lo, float(x))), 2)


def _level(pm):
    return "자주" if pm >= 1 else "가끔" if pm >= 0.2 else "한두 번" if pm > 0 else ""


def judge_intro(pe, d, segs, sev):
    """인트로 구성 판단."""
    np = _np()
    ev = d["ev"]
    W = d["aux"]["W"]
    blocks = []  # (시작, 끝, 종류, 설명)
    tz = [t for t in ev["teaser"] if t[0] < W]
    tsec = sum(b - a for a, b, _ in tz)
    if tz:
        blocks.append((tz[0][0], tz[-1][1], "teaser", f"{mmss(tz[0][2])} 장면이 인트로 {mmss(tz[0][0])}에 먼저 나와요"))
    titles = [t for t in ev["title"] if t[0] < W]
    if titles:
        tt = titles[0]
        blocks.append((tt[0], tt[1], "title", f"{mmss(tt[0])} 타이틀 화면" + (f" '{tt[2]}'" if tt[2] else "")))
    greet_t = None
    for s in segs:
        if float(s["start"]) > 40:
            break
        if GREETING.search(str(s["text"])):
            greet_t = float(s["start"])
            blocks.append((greet_t, float(s["end"]), "greeting", f"{mmss(greet_t)} 인사 \"{str(s['text']).strip()[:24]}\""))
            break
    # 훅 멘트: 영상의 첫 말(인사보다 앞)이 질문·도발·단언일 때만 — 인사 뒤의 '해 볼까요?' 같은 진행 말은 훅이 아님
    hook_t, hook_kind = None, None
    for s in segs:
        a0 = float(s["start"])
        if a0 > 15 or (greet_t is not None and a0 >= greet_t - 0.1):
            break
        tx = str(s["text"])
        if GREETING.search(tx):
            break
        if HOOK_Q.search(tx) and not ROUTINE_Q.search(tx):
            hook_t, hook_kind = a0, "질문형"
        elif HOOK_W.search(tx) and not ROUTINE_Q.search(tx):
            hook_t, hook_kind = a0, "도발형" if re.search(r"이기면|지면|벌칙|vs|VS|도전|가능", tx) else "단언형"
        if hook_t is not None:
            blocks.append((hook_t, float(s["end"]), "hook", f"{mmss(hook_t)} 훅 멘트 \"{tx.strip()[:24]}\""))
            break
        if len(_norm(tx)) >= 6:  # 첫 말이 훅이 아니면 더 보지 않음 (첫머리에 오는 것만 훅)
            break
    au = _signals(pe)["audio"]
    bgm_t = None
    if au and len(au["music"]):
        on = np.flatnonzero(au["music"] > 0.3)
        bgm_t = float(on[0] * au["step"]) if len(on) else None
    # 본론 시작: 마지막 인트로 블록(티저·타이틀·인사)이 끝난 뒤 말이 10초 넘게 이어지는 첫 시각 (최대 60초)
    end_intro = max([b for a, b, k, _ in blocks if k in ("teaser", "title", "greeting")] or [0.0])
    main_t = None
    for s in segs:
        a = float(s["start"])
        if a < end_intro - 0.5:
            continue
        run_end, last = a, a
        for s2 in segs:
            if float(s2["start"]) < a:
                continue
            if float(s2["start"]) - last > 1.5:
                break
            last = run_end = float(s2["end"])
        if run_end - a >= 10:
            main_t = a
            break
    if main_t is None:
        main_t = end_intro
    main_t = round(min(60.0, max(end_intro, main_t)), 1)
    # 유형: 실제 순서대로 놓인 첫 블록 (같은 때면 티저 > 훅 > 타이틀 > 인사)
    pri = {"teaser": 0, "hook": 1, "title": 2, "greeting": 3}
    order = sorted([x for x in blocks if not (x[2] == "teaser" and tsec < 2)], key=lambda x: (round(x[0]), pri[x[2]]))
    if order and order[0][2] == "title" and segs and order[0][0] > float(segs[0]["start"]) + 1:
        order = [x for x in order if x[2] != "title"] + [order[0]]  # 말이 먼저 시작하고 타이틀은 그 뒤
    first = order[0][2] if order else None
    typ = {"teaser": "teaser", "hook": "hook_line", "title": "title_first", "greeting": "greeting", None: "cold_open"}[first]
    if typ == "greeting" and greet_t is not None and greet_t > 10:
        typ = "cold_open"  # 인사가 10초 넘어서야 나오면 '바로 본론' 에 가까움
    greeting = "none" if greet_t is None else "early" if greet_t <= 10 else "late"
    steps = []
    for a, b, k, _ in order:
        lab = {"teaser": "티저", "title": "타이틀", "greeting": "인사", "hook": "훅 멘트"}[k]
        if lab not in steps:
            steps.append(lab)
    steps.append("본론")
    head = {"teaser": "본편 하이라이트를 먼저 보여 주는 티저형 훅",
            "hook_line": f"{hook_kind or '질문형'} 훅 멘트로 시작",
            "title_first": "타이틀 화면으로 시작",
            "greeting": "인사·자기소개로 시작",
            "cold_open": "인사·타이틀 없이 바로 본론으로 시작"}[typ]
    name = {"티저": "티저", "타이틀": "타이틀", "인사": "인사·자기소개", "훅 멘트": f"{hook_kind or '질문형'} 훅 멘트"}
    head_lab = {"teaser": "티저", "hook_line": "훅 멘트", "title_first": "타이틀", "greeting": "인사"}.get(typ)
    rest = [name[s] for s in steps if s not in ("본론", head_lab)] if typ != "cold_open" else []
    when = "10초 이내" if main_t <= 10 else f"약 {int(round(main_t / 5) * 5) or int(main_t)}초"
    note = {"none": "인사 없음", "late": "인사는 뒤에 짧게", "early": ""}[greeting]
    label = head + (" → " + " → ".join(rest) if rest else "") + (" → 본론" if typ != "cold_open" else "")
    tail = ", ".join(x for x in (note if typ != "greeting" else "", f"본론까지 {when}") if x)
    label += f" ({tail})" if tail else ""
    n_sig = sum(bool(x) for x in (tsec >= 2, titles, greet_t is not None, hook_t is not None))
    conf = (0.8 if n_sig >= 2 else 0.6 if n_sig == 1 else 0.5) - (0.1 if not segs else 0.0)
    if typ == "cold_open" and not segs:
        conf = 0.35
    evs = [{"t": round(a, 1), "why": why} for a, b, k, why in sorted(blocks)][:2]
    if bgm_t is not None and bgm_t <= 5 and len(evs) < 2:
        evs.append({"t": round(bgm_t, 1), "why": f"{mmss(bgm_t)}부터 배경 음악"})
    return {"type": typ, "label": label, "greeting": greeting, "lenSec": main_t, "titleCard": bool(titles),
            "bgmAtStart": bool(bgm_t is not None and bgm_t <= 5), "steps": steps, "teaserSec": int(tsec),
            "hook": hook_kind, "conf": _conf(conf), "ev": evs}


def judge_captions(pe, d, segs, sev):
    """자막 처리 판단."""
    import style
    dur = float(pe.get("duration") or 1)
    mins = max(dur / 60, 0.25)
    caps = d["caps"]
    if caps is None:  # 글자 읽기 모델 없음 → 예전 글자 밀도·색 표본으로 양·위치·색만
        bands, pos, color = style._captions(sev or {})
        ratio = max(bands.values()) if bands else 0.0
        if ratio < 0.08:
            label, sty = "자막을 거의 쓰지 않아요", "minimal"
        else:
            nm = color_name(style._hex_rgb(color) or (255, 255, 255))
            label, sty = f"화면의 {int(ratio * 100)}%에 {POS_KO[pos]}쪽 {CNAME_KO[nm]} 글씨 자막이 깔려요", "unknown"
        return {"style": sty, "label": label + " (글자 읽기 모델이 없어 자막 종류는 몰라요·어림)", "perMin": None, "colors": [color],
                "multiColor": False, "sizeRange": None, "bigRatio": None, "followsSpeech": None, "pos": pos, "conf": 0.3, "ev": [], "approx": True}
    kinds = ("speech", "emph", "inner", "situ", "sfx", "info")
    cnt = {k: sum(1 for c in caps if c["kind"] == k) for k in kinds}
    pm = {k: round(cnt[k] / mins, 2) for k in kinds}
    variety = [c for c in caps if c["kind"] in ("emph", "inner", "situ", "sfx")]
    var_pm = sum(pm[k] for k in ("emph", "inner", "situ", "sfx"))
    hs = sorted(c["h"] for c in caps)
    size = [round(hs[int(len(hs) * 0.1)], 3), round(hs[min(len(hs) - 1, int(len(hs) * 0.9))], 3)] if hs else None
    big = round(sum(c["h"] >= BIG_H for c in variety) / len(variety), 2) if variety else 0.0
    cn = {}
    for c in variety:
        if c["cname"] not in ("white", "black"):
            cn[c["cname"]] = cn.get(c["cname"], 0) + 1
    colors = [k for k, v in sorted(cn.items(), key=lambda kv: -kv[1]) if variety and v / len(variety) >= 0.1]
    talk = _union_len(_talk_ivs(segs))
    sp_cov = _union_len([(c["a"], c["b"]) for c in caps if c["kind"] == "speech"])
    follows = round(min(1.0, sp_cov / talk), 2) if talk > 5 else None
    kw = [c for c in caps if c["kind"] == "emph" and (c["h"] >= BIG_H or c["cname"] != "white") and len(_norm(c["text"])) <= 10]
    kw_pm = round(len(kw) / mins, 2)
    poss = [c["pos"] for c in caps if c["kind"] == "speech"] or [c["pos"] for c in caps] or ["bottom"]
    pos = max(set(poss), key=poss.count)
    sp_names = [c["cname"] for c in caps if c["kind"] == "speech"]
    sp_color = max(set(sp_names), key=sp_names.count) if sp_names else "white"
    total = sum(pm.values())
    if total < 0.5:
        sty = "minimal"
    elif var_pm >= 2 and var_pm > pm["speech"] * 0.5 and not (follows is not None and follows >= 0.6 and pm["speech"] >= var_pm):
        sty = "variety"
    elif follows is not None and follows >= 0.6 and var_pm < 1:
        sty = "speech"
    elif pm["speech"] >= 1 and var_pm >= 1:
        sty = "mixed"
    elif pm["info"] >= max(pm["speech"], var_pm):
        sty = "info"
    elif var_pm >= pm["speech"]:
        sty = "variety"
    else:
        sty = "speech"
    parts = []
    head = {"minimal": "자막을 거의 쓰지 않아요", "variety": "말 자막보다 예능 자막 위주", "speech": "말을 그대로 받아쓴 자막 위주",
            "mixed": "말 자막과 예능 자막을 함께", "info": "이름표·숫자 같은 정보 자막 위주"}[sty]
    parts.append(head)
    if sty != "minimal":
        if kw_pm >= 0.3:
            cs = "·".join(CNAME_KO[c] for c in colors[:2]) or "흰"
            parts.append(f"핵심 단어를 {cs} {'큰 ' if big >= 0.4 else ''}글씨로 강조(1분에 약 {max(1, int(round(kw_pm)))}번)")
        elif len(colors) >= 2:
            parts.append(f"{'·'.join(CNAME_KO[c] for c in colors[:3])} 여러 색 자막")
        if pm["inner"] >= 0.2:
            parts.append(f"속마음 자막 {_level(pm['inner'])}")
        if pm["situ"] >= 0.2:
            parts.append(f"상황 설명 자막 {_level(pm['situ'])}")
        if pm["sfx"] >= 0.2:
            parts.append(f"효과 글자(의성어) {_level(pm['sfx'])}")
        if pm["speech"] >= 0.5:
            parts.append(f"말 자막은 {POS_KO[pos]} {CNAME_KO.get(sp_color, '흰')} 글씨")
        if pm["info"] >= 0.2 and sty != "info":
            parts.append("이름표·숫자 정보 자막도 써요")
    parts = parts[:3]  # 한 줄 판단: 머리 + 두 가지까지 (나머지는 근거·수치에)
    conf = 0.75 if len(caps) >= 8 else 0.55 if len(caps) >= 3 else 0.4
    if not segs:
        conf -= 0.1
    ex = sorted(kw or variety, key=lambda c: (-c["h"], c["a"]))[:2]
    evs = [{"t": c["a"], "why": f"{mmss(c['a'])} {CNAME_KO.get(c['cname'], '')} {'큰 ' if c['h'] >= BIG_H else ''}글씨 \"{c['text'][:20]}\""} for c in ex]
    return {"style": sty, "label": ", ".join(parts), "perMin": pm, "colors": [COLORS[c] for c in colors], "multiColor": len(colors) >= 2,
            "sizeRange": size, "bigRatio": big, "followsSpeech": follows, "pos": pos, "keywordPerMin": kw_pm, "conf": _conf(conf), "ev": evs}


def judge_fun(pe, d, segs, sev):
    dur = float(pe.get("duration") or 1)
    mins = max(dur / 60, 0.25)
    ev = d["ev"]
    S = _signals(pe)
    slow = [r for r in ev["replay"] if r[4] >= 1.5]
    plain = [r for r in ev["replay"] if r[4] < 1.5]
    raw = {"slowmo_replay": [(r[0], f"{mmss(r[2])} 장면을 {mmss(r[0])}에 느리게 다시 보여 줘요") for r in slow],
           "replay": [(r[0], f"{mmss(r[2])} 장면을 {mmss(r[0])}에 다시 보여 줘요") for r in plain],
           "punch_zoom": [(t, f"{mmss(t)} 웃음·효과 직후 확대") for t in ev["punchZoom"]],
           "sfx": [(t, f"{mmss(t)} 효과음") for t in ev["sfx"]],
           "freeze": [(a, f"{mmss(a)} 화면을 멈추고 소리는 계속") for a, b in ev["freeze"]],
           "meme": [(a, f"{mmss(a)} 다른 영상과 전혀 다른 이미지 {round(b - a, 1)}초") for a, b in ev["insert"]],
           "shake": [(a, f"{mmss(a)} 화면 흔들기") for a, b in ev["shake"]],
           "montage": [(a, f"{mmss(a)} 6초 안에 컷 5번 넘게") for a, b in ev["montage"]],
           "reaction_cut": [(t, f"{mmss(t)} 얼굴 클로즈업 리액션 컷") for t in ev["reaction"]]}
    if S["audio"]:
        raw["laugh"] = [(a, f"{mmss(a)} 웃음소리") for a, b in ev["laugh"]]
        raw["cheer"] = [(a, f"{mmss(a)} 환호·박수") for a, b in ev["cheer"]]
    items = []
    for k, xs in raw.items():
        if not xs:
            continue
        pm = round(len(xs) / mins, 2)
        items.append({"key": k, "label": FUN_LABEL[k], "perMin": pm, "level": _level(pm), "ev": [{"t": round(t, 1), "why": w} for t, w in xs[:2]]})
    items.sort(key=lambda x: -x["perMin"])
    shown = [x for x in items if x["perMin"] >= 0.2][:5]
    if shown:
        label = ", ".join(f"{x['label']}({x['level']})" for x in shown)
    else:
        label = "눈에 띄는 편집 재미 요소는 적어요 (내용·말 위주)"
    notes = []
    if not S["audio"]:
        notes.append("소리가 없는 영상이라 웃음소리는 몰라요" if (pe.get("models") or {}).get("audio") else "웃음소리는 소리 모델이 없어 몰라요")
    if S["faces"] is None:
        notes.append("리액션 컷은 얼굴 모델이 있을 때만 봐요")
    if notes:
        label += f" ({' · '.join(notes)})"
    conf = 0.65 if S["audio"] else 0.45
    return {"items": items, "label": label, "conf": conf, "ev": [e for x in shown[:2] for e in x["ev"][:1]], "approx": not S["audio"]}


def judge_genre(pe, d, segs, title, fun, caps_j, spk):
    dur = float(pe.get("duration") or 1)
    ocr_txt = " ".join(ln.get("text", "") for o in pe.get("ocr") or [] for ln in o.get("lines") or [])
    tr = " ".join(str(s["text"]) for s in segs)
    sc, why = {}, {}
    fpm = {x["key"]: x["perMin"] for x in fun["items"]}
    laugh_pm = fpm.get("laugh", 0) + fpm.get("cheer", 0)
    n_spk = (spk or {}).get("n") or 0
    share1 = max((spk or {}).get("share") or [0])
    try:
        import hooks
        terms = [t for t in hooks.TERMS if len(t.replace(" ", "")) >= 2]
    except Exception:  # noqa: BLE001
        terms = []
    title_s = title or ""
    tech_title = any(t in title_s for t in terms)
    src = {}  # 장르마다 점수가 어디서 왔는지 (제목이 없으면 확신을 낮춤)
    for key, _, rx, trx in GENRES:
        r, rt = re.compile(rx), re.compile(f"{rx}|{trx}")
        t_hits = [m.group(0) for m in rt.finditer(title_s)]
        o_hits = [m.group(0) for m in r.finditer(ocr_txt)]
        x_all = [m.group(0) for m in r.finditer(tr)]
        x_cnt = {}
        for w in x_all:
            x_cnt[w] = x_cnt.get(w, 0) + 1
        # 제목(한 번에 3) > 화면 글자(낱말마다 1, 2까지) > 대사(낱말마다 횟수의 로그, 모두 2.5까지) — 대사에 자주 나오는 낱말 하나가 이기지 않게
        t_sc = 3.0 * len(set(t_hits))
        o_sc = min(2.0, 1.0 * len(set(o_hits)))
        x_sc = min(2.5, sum(0.5 * min(2.0, math.log2(1 + c)) for c in x_cnt.values()))
        s = t_sc + o_sc + x_sc
        x_hits = sorted(x_cnt, key=lambda w: -x_cnt[w])
        if key == "lesson" and tech_title and not re.search(GENRES[1][2] + "|" + GENRES[1][3], title_s):
            t_sc += 1.5  # 제목에 기술 이름(인사이드·트래핑…)이 있고 대결 단서가 없으면 레슨 쪽
            s += 1.5
        src[key] = t_sc
        bonus = 0.0
        if key == "lesson":
            bonus += (1.0 if n_spk == 1 or share1 > 0.75 else 0) + (0.5 if (caps_j.get("perMin") or {}).get("info", 0) >= 0.3 else 0)
        elif key == "challenge":
            bonus += (1.0 if laugh_pm >= 0.5 else 0) + (0.5 if n_spk >= 2 else 0) + (0.5 if re.search(r"\d+\s*[:：]\s*\d+|\d+점", ocr_txt) else 0)
        elif key == "variety_talk":
            bonus += (1.0 if fpm.get("laugh", 0) >= 1 else 0) + (1.0 if caps_j.get("style") == "variety" else 0) + (0.5 if n_spk >= 2 else 0)
        elif key == "vlog":
            bonus += (0.5 if fpm.get("meme", 0) >= 0.3 else 0) + (0.3 if n_spk == 1 else 0)
        elif key == "match":
            mus = _signals(pe)["audio"]
            talk = _union_len(_talk_ivs(segs)) / dur
            if mus is not None and len(mus["music"]) and float((mus["music"] > 0.3).mean()) > 0.5 and talk < 0.3:
                bonus += 1.5
        elif key == "interview":
            if n_spk == 2 and (spk or {}).get("turnsPerMin", 0) < 4 and min((spk or {}).get("share") or [0]) > 0.25:
                bonus += 1.0
        sc[key] = round(s + bonus, 2)
        why[key] = (list(dict.fromkeys(t_hits))[:2], o_hits[:1], x_hits[:2])
    order = sorted(sc, key=lambda k: -sc[k])
    top, second = order[0], order[1]
    keys = []
    if sc[top] <= 0.5:
        lab, key, conf = "주제를 가늠하기 어려워요 (제목·대사 단서가 적음)", "unknown", 0.3
    else:
        key = top
        conf = _conf((sc[top] - sc[second]) / sc[top], 0.4, 0.9)
        combo = COMBO.get(frozenset((top, second))) if sc[second] > 0 and sc[second] >= 0.6 * sc[top] else None
        lab = combo or GENRE_LABEL[top]
        keys = [top, second] if combo else [top]
        if combo:
            conf = _conf(conf + 0.15, 0.4, 0.9)
        if src.get(top, 0) <= 0 or sc[top] < 4:  # 제목에 단서가 없거나 단서가 적으면 → '아마도'까지
            conf = min(conf, 0.6)
        soccer = SOCCER.search((title or "") + " " + tr[:4000] + " " + ocr_txt[:2000])
        if soccer and not lab.startswith("축구"):
            lab = "축구 " + lab
    evs = []
    th, oh, xh = why.get(key, ([], [], []))
    if th:
        evs.append({"t": None, "why": f"제목에 '{th[0]}'" + (f"·'{th[1]}'" if len(th) > 1 else "")})
    if xh and len(evs) < 2:
        evs.append({"t": None, "why": f"대사에 '{xh[0]}'" + (f"·'{xh[1]}'" if len(xh) > 1 else "")})
    if oh and len(evs) < 2:
        evs.append({"t": None, "why": f"화면 글자에 '{oh[0]}'"})
    alt = [{"key": k, "label": GENRE_LABEL[k], "conf": round(sc[k] / max(sc[top], 1e-6), 2)} for k in order[1:3] if sc[k] > 0]
    return {"key": key, "keys": keys, "label": lab, "conf": conf, "alt": alt, "scores": sc, "ev": evs}


def judge_format(pe, d, segs, sev, genre, fun, caps_j, spk):
    np = _np()
    dur = float(pe.get("duration") or 1)
    S = _signals(pe)
    n_spk = (spk or {}).get("n")
    two_face = float((S["nface"] >= 2).mean()) if S["nface"] is not None and len(S["nface"]) else None
    if n_spk is None and two_face is not None:
        n_spk = 2 if two_face > 0.3 else 1
    people = None if not n_spk else "1인" if n_spk == 1 else "2인" if n_spk == 2 else "3인 이상"
    if two_face is not None and n_spk == 1 and two_face > 0.4:
        people = "2인"
    talk = _union_len(_talk_ivs(segs)) / dur if segs else None
    if talk is None and S["audio"] and len(S["audio"]["speech"]):
        talk = float((S["audio"]["speech"] > 0.5).mean())
    motion = np.array((sev or {}).get("motion") or [0], np.float32)
    mov = float(np.median(motion)) if len(motion) else 0.0
    labels = []
    gkeys = set(genre.get("keys") or [genre.get("key")])
    title = str(pe.get("title") or "")
    duel = "challenge" in gkeys or "챌린지" in str(genre.get("label") or "") or bool(re.search(r"1대1|1:1|vs|VS|대결", title))
    if duel:
        # 대결·챌린지: 목소리 묶음이 하나여도(어림) '1인 진행'이라고 하지 않음 — 제목·장르와 어긋나지 않게
        labels.append("2~3인 대결" if people == "2인" else "여럿이 대결" if people == "3인 이상" else "대결·챌린지 진행")
    elif people:
        labels.append({"1인": "1인 진행", "2인": "2인 대화", "3인 이상": "3인 이상 대화"}[people])
    fpm = {x["key"]: x["perMin"] for x in fun["items"]}
    big_face = S["faces"] is not None and len(S["faces"]) and float((S["faces"] >= 0.3).mean()) > 0.05
    react = fpm.get("laugh", 0) + fpm.get("cheer", 0)
    if react >= 0.5 and (big_face or S["faces"] is None and react >= 1):
        labels.append("현장 리액션")
    cap_secs = len({int(o["t"]) + k for o in pe.get("ocr") or [] for k in range(int(o.get("d") or 1)) if o.get("lines")})
    if pe.get("ocr") is not None and cap_secs / max(1.0, dur) >= 0.7 and (talk or 0) <= 0.3:
        labels.append("자막 위주")
    demo = None
    if talk is not None:
        if talk >= 0.65:
            labels.append("말 위주")
            demo = round(1 - talk, 2)
        elif talk <= 0.35 and mov >= 4:
            labels.append("시범·플레이 위주")
            demo = round(1 - talk, 2)
        else:
            labels.append("말과 시범 반반")
            demo = round(1 - talk, 2)
    # 장소: 화면 아래쪽 초록 비율은 '잔디 구장'만 말해 줌 (인조잔디 실내 풋살장도 초록) — 야외/실내는 판단하지 않음
    g = S["green"]
    place = None
    if g is not None and len(g) and float((g >= 0.4).mean()) >= 0.5:
        place = "잔디 구장"
        labels.append("잔디 구장 위주")
    labels = labels[:3]
    conf = 0.5 if spk else 0.45  # 목소리 묶음·잔디 비율은 실제 영상으로 확인 전이라 '아마도/어림'까지
    evs = []
    if spk:
        evs.append({"t": None, "why": f"목소리 묶음 {spk['n']}개 (말 차례 1분에 {spk['turnsPerMin']}번 바뀜, 어림)"})
    if talk is not None:
        evs.append({"t": None, "why": f"말하는 시간이 영상의 {int(talk * 100)}%"})
    return {"labels": labels or ["형식을 가늠하기 어려워요"], "people": people, "place": place, "talkRatio": round(talk, 2) if talk is not None else None,
            "demoRatio": demo, "conf": conf, "ev": evs[:2]}


def _auto_flags(intro, caps_j):
    """가편집에 바로 넣을지: 티저형 인트로 · 예능 강조 자막 — 레퍼런스 영상들이 절반 넘게 같은 판단일 때만 (갈리면 '직접 해 보세요')."""
    teaser = intro.get("type") == "teaser" and not intro.get("mixed") and float(intro.get("share", 1.0)) > 0.5
    emph = caps_j.get("style") in ("variety", "mixed") and not caps_j.get("mixedStyles") and float(caps_j.get("share", 1.0)) > 0.5
    return teaser, emph


def _apply(intro, caps_j, fun):
    out = []
    teaser_on, emph_on = _auto_flags(intro, caps_j)
    if intro.get("type") == "teaser":
        sec = max(3, min(6, intro.get("teaserSec") or 4))
        out.append({"tip": f"인트로: 감독님 말이 적고 현장 소리가 큰 시범 장면 {sec}초를 맨 앞에 미리 보여 주고 제목을 띄워요"
                    + ("" if teaser_on else " (레퍼런스마다 인트로가 달라 가편집에는 넣지 않았어요)"), "auto": teaser_on})
    if caps_j.get("style") in ("variety", "mixed"):
        cs = color_name(_hex(caps_j["colors"][0])) if caps_j.get("colors") else "yellow"
        n = max(1, min(4, int(round(caps_j.get("keywordPerMin") or 2))))
        out.append({"tip": f"기술 이름·핵심 단어를 {CNAME_KO[cs]} 큰 글씨로 1분에 {n}번쯤 띄워요"
                    + ("" if emph_on else " (레퍼런스마다 자막이 달라 가편집에는 넣지 않았어요)"), "auto": emph_on})
    if intro.get("greeting") in ("none", "late") and intro.get("type") != "greeting" and not intro.get("mixed"):
        out.append({"tip": "인사는 빼거나 뒤로 미루고, 첫 10초 안에 오늘 배울 것(결과)을 먼저 보여 줘요", "auto": False})
    if intro.get("type") == "hook_line" and not intro.get("mixed"):
        out.append({"tip": "첫 문장을 질문으로 시작해 보세요 (예: \"이 슈팅, 왜 안 들어갈까요?\")", "auto": False})
    tip = {"slowmo_replay": "실패하거나 잘 들어간 장면은 느리게 한 번 더 보여 주면 이 채널처럼 돼요",
           "replay": "결정적인 장면은 바로 한 번 더 보여 줘요",
           "punch_zoom": "웃기거나 중요한 말 직후에 얼굴을 확 당겨(줌) 보여 줘요",
           "sfx": "실수·성공 순간에 짧은 효과음을 넣어요",
           "laugh": "현장 웃음소리를 줄이지 말고 살려요",
           "freeze": "결정적인 순간에 화면을 멈추고 글자를 띄워요",
           "meme": "어울리는 짤·밈 이미지를 1~2초 끼워 넣어요",
           "montage": "시범 장면 여러 개를 음악에 맞춰 빠르게 이어 붙여요",
           "reaction_cut": "시범 직후 수강생 반응 얼굴을 짧게 붙여요"}
    for x in fun.get("items") or []:
        if len(out) >= 4:
            break
        if x["perMin"] >= 0.2 and x["key"] in tip:
            out.append({"tip": tip[x["key"]], "auto": False})
    return out[:4]


def _hex(h):
    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})", str(h or ""))
    return tuple(int(m[1][k:k + 2], 16) for k in (0, 2, 4)) if m else (255, 255, 255)


def _ro(word):
    """'…로/으로' 조사 (받침이 있으면 으로, ㄹ 받침은 로)."""
    ch = (word or " ").strip()[-1:]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return "으로" if jong not in (0, 8) else "로"
    return "로"


def _eul(word):
    """'…을/를' 조사."""
    ch = (word or " ").strip()[-1:]
    if "가" <= ch <= "힣":
        return "을" if (ord(ch) - 0xAC00) % 28 else "를"
    return "를"


INTRO_SHORT = {"teaser": "티저형", "hook_line": "훅 멘트형", "title_first": "타이틀형", "greeting": "인사형", "cold_open": "바로 본론형"}
CAPS_SHORT = {"variety": "예능 자막 위주", "mixed": "말·예능 자막 함께", "speech": "말 자막 위주", "info": "정보 자막 위주",
              "minimal": "자막 거의 없음", "unknown": "종류 모름"}
INTRO_PHRASE = {"teaser": "결과 장면을 먼저 보여 주며 시작해", "hook_line": "첫마디 훅으로 시선을 잡고", "title_first": "타이틀 화면으로 열고",
                "greeting": "인사로 열고", "cold_open": "인사 없이 바로 본론으로 들어가"}


FMT_PHRASE = {"1인 진행": "혼자 설명하는 1인 진행", "2인 대화": "두 사람의 대화", "3인 이상 대화": "여럿의 대화",
              "여럿이 대결": "여럿의 대결", "대결·챌린지 진행": "대결·챌린지 구도"}


def _mixed_note(counts, names):
    return ", ".join(f"{names.get(k, k)} {v}개" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def headline(p):
    """채널 공식 한 문장 (아래 다섯 줄을 되풀이하지 않고 '어떻게 시작해 무엇으로 끌고 가며 무엇으로 재미를 만드는지')."""
    intro, genre, fmt, caps_j, fun = p["intro"], p["genre"], p["format"], p["captions"], p["fun"]
    ip = "영상마다 다른 방식으로 시작해" if intro.get("mixed") else INTRO_PHRASE.get(intro.get("type"), "")
    labs = [x for x in fmt.get("labels") or [] if "가늠" not in x]
    core_lab = next((x for x in labs if re.search(r"진행|대화|대결", x)), labs[0] if labs else "")
    core_ph = FMT_PHRASE.get(core_lab, core_lab)
    fp = f" {core_ph}{_ro(core_ph)} 끌고 가고," if core_ph else ","
    top = [x["label"] for x in fun.get("items") or [] if x["perMin"] >= 0.2][:2]
    if top:
        funp = f"{'·'.join(top)}{_ro(top[-1])} 재미를 만드는"
    elif caps_j.get("style") in ("variety", "mixed"):
        funp = "예능 자막으로 재미를 더하는"
    else:
        funp = "편집 효과보다 내용 설명에 집중하는"
    if genre.get("key") == "unknown" or genre.get("mixed"):
        g = "채널"
    else:
        g = f"{genre['label']} 채널"
    return f"이 채널은 {ip}{fp} {funp} {g}이에요."


def _summary(p):
    """판단 → 요약: 공식 한 문장 + 갈린 판단·자막 처리 한두 문장 (시각·숫자 목록 없이). 영상마다 갈리면 표시 글이 아니라 구조 값으로 씀."""
    intro, genre, caps_j = p["intro"], p["genre"], p["captions"]
    s = [headline(p)]
    if genre.get("mixed"):
        s.append(f"장르는 영상마다 달라요 ({_mixed_note(genre['mixed'], GENRE_LABEL)}).")
    if intro.get("mixed"):
        s.append(f"인트로는 영상마다 달라요 ({_mixed_note(intro['mixed'], INTRO_SHORT)}).")
    else:
        s.append(f"인트로는 '{intro['label'].split(' (')[0]}' 식이에요.")
    if caps_j.get("mixedStyles"):
        s.append(f"자막은 영상마다 달라요 ({_mixed_note(caps_j['mixedStyles'], CAPS_SHORT)}).")
    else:
        cl = caps_j["label"].split(" (")[0]
        s.append(f"{cl}." if cl.startswith("자막을") else f"자막은 {cl}.")
    return " ".join(s)


def judge(pe, sev, segs=None, title=None):
    """영상 하나의 기획 판단 (plan 한 개)."""
    segs = segs if segs is not None else _transcript(pe.get("source") or "")
    title = title if title is not None else pe.get("title") or ""
    d = detect(pe, sev, segs)
    spk = pe.get("speakers")
    intro = judge_intro(pe, d, segs, sev)
    caps_j = judge_captions(pe, d, segs, sev)
    fun = judge_fun(pe, d, segs, sev)
    genre = judge_genre(pe, d, segs, title, fun, caps_j, spk)
    fmt = judge_format(pe, d, segs, sev, genre, fun, caps_j, spk)
    p = {"v": PLAN_VER, "source": pe.get("source"), "title": title, "duration": pe.get("duration"),
         "models": pe.get("models") or {}, "intro": intro, "genre": genre, "format": fmt, "captions": caps_j, "fun": fun}
    p["summary"] = _summary(p)
    p["headline"] = headline(p)
    p["apply"] = _apply(intro, caps_j, fun)
    p["ai"] = None
    return p


# ---------- 여러 영상 합치기 ----------

def _vote(items):
    """[(값, 무게)] → (1위 값, 1위 무게 비율, 2위 값)."""
    w = {}
    for v, wt in items:
        w[v] = w.get(v, 0.0) + wt
    if not w:
        return None, 0.0, None
    order = sorted(w, key=lambda k: -w[k])
    tot = sum(w.values()) or 1.0
    return order[0], w[order[0]] / tot, order[1] if len(order) > 1 else None


def merge_plans(plans):
    """영상마다의 판단 → 스타일 하나의 판단 (범주: 길이×확신 투표 · 비율: 길이 무게 평균)."""
    plans = [p for p in plans if isinstance(p, dict) and p.get("intro")]
    if not plans:
        return None
    if len(plans) == 1:
        out = json.loads(json.dumps(plans[0]))
        out["agree"] = {}
        out["refs"] = 1
        return out
    n = len(plans)
    wt = lambda p, part: float(p.get("duration") or 60) * float(p[part].get("conf") or 0.5)  # noqa: E731
    out = {"v": PLAN_VER, "refs": n, "models": {k: all((p.get("models") or {}).get(k) for p in plans) for k in ("ocr", "audio", "faces")}}
    agree = {}

    def cnt(part, key):
        top = _vote([(p[part].get(key), 1) for p in plans])[0]
        return f"{n}개 중 {sum(1 for p in plans if p[part].get(key) == top)}개"
    # 인트로
    typ, share, typ2 = _vote([(p["intro"]["type"], wt(p, "intro")) for p in plans])
    best = max((p for p in plans if p["intro"]["type"] == typ), key=lambda p: p["intro"]["conf"])
    intro = dict(best["intro"])
    intro["lenSec"] = round(sum(p["intro"]["lenSec"] * float(p.get("duration") or 60) for p in plans) / sum(float(p.get("duration") or 60) for p in plans), 1)
    def counts(part, key):
        c = {}
        for p in plans:
            c[p[part].get(key)] = c.get(p[part].get(key), 0) + 1
        return c

    def head_of(lab):
        return str(lab).split(" (")[0].split(" → ")[0]
    intro["share"] = round(share, 2)
    intro.pop("mixed", None)
    if share <= 0.5 and typ2:  # 1위가 절반을 못 넘으면 (둘이 같으면 포함)
        other = next(p for p in plans if p["intro"]["type"] == typ2)
        intro["label"] = f"영상마다 달라요: {head_of(best['intro']['label'])} 또는 {head_of(other['intro']['label'])}"
        intro["conf"] = _conf(intro["conf"] * 0.7)
        intro["mixed"] = counts("intro", "type")
    else:
        intro["conf"] = _conf(intro["conf"] * (0.7 + 0.3 * share))
    intro["ev"] = [e for p in plans for e in p["intro"].get("ev") or []][:2]
    agree["intro"] = cnt("intro", "type")
    # 장르
    gkey, gshare, g2 = _vote([(p["genre"]["key"], wt(p, "genre")) for p in plans])
    gb = max((p for p in plans if p["genre"]["key"] == gkey), key=lambda p: p["genre"]["conf"])
    genre = dict(gb["genre"])
    genre["share"] = round(gshare, 2)
    genre.pop("mixed", None)
    if gshare <= 0.5 and g2:
        other = next(p for p in plans if p["genre"]["key"] == g2)
        genre["label"] = f"영상마다 달라요: {gb['genre']['label']} 또는 {other['genre']['label']}"
        genre["conf"] = _conf(genre["conf"] * 0.7)
        genre["mixed"] = counts("genre", "key")
    genre["ev"] = [e for p in plans for e in p["genre"].get("ev") or []][:2]
    agree["genre"] = cnt("genre", "key")
    # 형식: 라벨마다 투표 (절반 넘는 영상에 있으면)
    lw = {}
    for p in plans:
        for lb in p["format"]["labels"]:
            lw[lb] = lw.get(lb, 0.0) + float(p.get("duration") or 60)
    totd = sum(float(p.get("duration") or 60) for p in plans)
    labels = [lb for lb in sorted(lw, key=lambda k: -lw[k]) if lw[lb] / totd >= 0.5][:3] or [max(lw, key=lw.get)] if lw else ["형식을 가늠하기 어려워요"]
    fb = dict(plans[0]["format"])
    for k in ("talkRatio", "demoRatio"):
        v = [(p["format"].get(k), float(p.get("duration") or 60)) for p in plans if p["format"].get(k) is not None]
        fb[k] = round(sum(a * b for a, b in v) / sum(b for _, b in v), 2) if v else None
    fb["labels"] = labels
    fb["conf"] = _conf(sum(p["format"]["conf"] for p in plans) / n)
    fb["ev"] = [e for p in plans for e in p["format"].get("ev") or []][:2]
    agree["format"] = f"{n}개 중 {sum(1 for p in plans if p['format']['labels'][:1] == labels[:1])}개"
    # 자막
    cs, cshare, c2 = _vote([(p["captions"]["style"], wt(p, "captions")) for p in plans])
    cb = max((p for p in plans if p["captions"]["style"] == cs), key=lambda p: p["captions"]["conf"])
    caps_j = dict(cb["captions"])
    pms = [p["captions"].get("perMin") for p in plans if p["captions"].get("perMin")]
    if pms:
        caps_j["perMin"] = {k: round(sum(float(x.get(k) or 0) for x in pms) / len(pms), 2) for k in pms[0]}
    kws = [p["captions"].get("keywordPerMin") for p in plans if p["captions"].get("keywordPerMin") is not None]
    if kws:
        caps_j["keywordPerMin"] = round(sum(kws) / len(kws), 2)
    colw = {}
    for p in plans:
        for i, c in enumerate(p["captions"].get("colors") or []):
            colw[c] = colw.get(c, 0.0) + float(p.get("duration") or 60) / (i + 1)
    caps_j["colors"] = sorted(colw, key=lambda k: -colw[k])[:3]
    caps_j["multiColor"] = len(caps_j["colors"]) >= 2
    caps_j["share"] = round(cshare, 2)
    caps_j.pop("mixedStyles", None)
    if cshare <= 0.5 and c2:
        caps_j["label"] = f"영상마다 달라요: {CAPS_SHORT.get(cs, cs)} 또는 {CAPS_SHORT.get(c2, c2)}"
        caps_j["conf"] = _conf(caps_j["conf"] * 0.7)
        caps_j["mixedStyles"] = counts("captions", "style")
    caps_j["ev"] = [e for p in plans for e in p["captions"].get("ev") or []][:2]
    agree["captions"] = cnt("captions", "style")
    # 재미: 항목별 길이 무게 평균
    fw = {}
    for p in plans:
        for x in p["fun"].get("items") or []:
            fw.setdefault(x["key"], []).append((x["perMin"], float(p.get("duration") or 60), x.get("ev") or []))
    items = []
    for k, xs in fw.items():
        pm = round(sum(a * b for a, b, _ in xs) / totd, 2)
        items.append({"key": k, "label": FUN_LABEL.get(k, k), "perMin": pm, "level": _level(pm), "ev": [e for _, _, ev in xs for e in ev][:2],
                      "agree": f"{n}개 중 {len(xs)}개"})
    items.sort(key=lambda x: -x["perMin"])
    shown = [x for x in items if x["perMin"] >= 0.2][:5]
    fun = {"items": items, "label": ", ".join(f"{x['label']}({x['level']})" for x in shown) or "눈에 띄는 편집 재미 요소는 적어요 (내용·말 위주)",
           "conf": _conf(sum(p["fun"]["conf"] for p in plans) / n), "ev": [e for x in shown[:2] for e in x["ev"][:1]],
           "approx": any(p["fun"].get("approx") for p in plans)}
    if fun["approx"]:
        fun["label"] += " (웃음소리는 소리 모델이 없어 몰라요)"
    tz = [p["intro"].get("teaserSec") or 0 for p in plans if p["intro"]["type"] == "teaser"]
    if tz:
        intro["teaserSec"] = sorted(tz)[len(tz) // 2]
    out.update(intro=intro, genre=genre, format=fb, captions=caps_j, fun=fun, agree=agree)
    out["summary"] = _summary(out)
    out["headline"] = headline(out)
    out["apply"] = _apply(intro, caps_j, fun)
    out["ai"] = None
    return out


# ---------- 가편집에 쓸 값 ----------

def plan_params(plan):
    """판단 → 가편집 값 (기본 꺼짐 · plan 이 없으면 예전과 똑같이)."""
    p = plan if isinstance(plan, dict) else {}
    intro, caps_j = p.get("intro") or {}, p.get("captions") or {}
    teaser_on, emph_on = _auto_flags(intro, caps_j)
    tz = {"on": False, "sec": 0}
    if teaser_on:
        tz = {"on": True, "sec": int(max(3, min(6, intro.get("teaserSec") or 4)))}
    em = {"perMin": 0, "color": "#FFE14D"}
    if emph_on:
        pm = float(caps_j.get("keywordPerMin") or ((caps_j.get("perMin") or {}).get("emph") or 0) or 0)
        col = (caps_j.get("colors") or ["#FFE14D"])[0]
        em = {"perMin": round(min(4.0, max(0.5, pm or 1.0)), 2),
              "color": col.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(col)) and col.upper() not in ("#FFFFFF", "#111111") else "#FFE14D"}
    return {"introTeaser": tz, "emphasisTitles": em}


def describe_plan(plan):
    return (plan or {}).get("summary") or ""


# ---------- Claude 에게 물어볼 내용 ----------

AI_KEYS = ("intro", "genre", "format", "captions", "fun", "summary")
AI_FORMAT = ('{"intro": "인트로 구성 한 줄 (예: 결과 장면을 먼저 보여 주는 티저형 훅 → 바로 본론, 인사 없음)", "genre": "장르·주제 한 구절", '
             '"format": "형식 한 구절", "captions": "자막 처리 한 문장", "fun": "재미 요소 한 문장", "summary": "채널 공식 3~5문장", '
             '"apply": ["풋살사관학교에 적용할 방법 1", "방법 2", "방법 3"], "fix": "내 PC 판단에서 틀린 점 (없으면 빈 글)"}')
TIMECODE = re.compile(r"\s*\(?(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)(?:\s*(?:쯤|무렵|께))?(?:의|에서|에|부터|까지|쯤)?\)?")


def _ref_caps(source):
    """레퍼런스 영상의 자막 사건 (plan_events.json 의 caps · 없으면 [])."""
    try:
        pe = json.loads(_plan_file(source).read_text(encoding="utf-8"))
        return [c for c in pe.get("caps") or [] if isinstance(c, dict)]
    except (OSError, ValueError, TypeError):
        return []


def _caps_digest(caps, limit=12):
    """화면 자막 → 자주·길게 나온 글자 목록 (종류·색·크기) — 같은 글자는 하나로."""
    seen, rows = {}, []
    for c in sorted(caps, key=lambda c: -(float(c.get("b", 0)) - float(c.get("a", 0)))):
        t = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()
        k = _norm(t)
        if len(k) < 2 or k in seen:
            continue
        seen[k] = 1
        size = "큰 글씨" if float(c.get("h") or 0) >= BIG_H else "보통" if float(c.get("h") or 0) >= SMALL_H else "작은 글씨"
        kind = {"speech": "말", "emph": "강조", "inner": "속마음", "situ": "상황", "sfx": "효과", "info": "정보"}.get(c.get("kind"), "?")
        rows.append(f"'{t[:24]}' ({kind}·{CNAME_KO.get(c.get('cname'), '흰')}·{size}·{POS_KO.get(c.get('pos'), '아래')})")
        if len(rows) >= limit:
            break
    return rows


def _body_windows(segs, dur, n=8, per=2):
    """본편(60초 뒤)에서 고르게 n 군데 · 군데마다 말 per 줄."""
    body = [s for s in segs if float(s["start"]) >= 60]
    if not body:
        return []
    end = max(float(dur or 0), float(body[-1]["end"]))
    out, used = [], set()
    for k in range(n):
        t = 60 + (end - 60) * (k + 0.5) / n
        i = min(range(len(body)), key=lambda j: abs(float(body[j]["start"]) - t))
        w = [j for j in range(i, min(len(body), i + per)) if j not in used]  # 짧은 영상에서 같은 줄을 되풀이하지 않게
        if w:
            used.update(w)
            out.append([body[j] for j in w])
    return out


def claude_prompt(prof, images=None, max_chars=6000):
    """스타일 하나의 기획 판단(힌트) + 근거 + 영상 전체에서 고른 대사·화면 자막·통계 → Claude 에게 물어볼 내용 (복사·CLI 공통)."""
    plan = (prof or {}).get("plan") or {}
    refs = [r for r in (prof or {}).get("refs") or [] if isinstance(r, dict)]
    rplans = [r.get("plan") for r in refs if isinstance(r.get("plan"), dict)] or ([plan] if plan else [])
    L = ["당신은 경력 많은 유튜브 PD예요. 아래 레퍼런스 유튜브 영상들을 보고 이 채널의 '공식'(인트로 구성·장르·형식·자막 처리·재미 요소)을 판단해 주세요.",
         "목표: 같은 공식을 '풋살사관학교'(최경진 감독의 풋살 레슨 채널) 영상 편집에 적용하는 방법 3~4개를 찾는 것이에요.", "",
         "[레퍼런스 영상]"]
    for p in rplans:
        m = int(float(p.get("duration") or 0))
        L.append(f"- {p.get('title') or p.get('source')} ({m // 60}분 {m % 60:02d}초)")
    L += ["", "[내 PC 자동 분석 — 규칙으로 어림한 힌트라 틀릴 수 있어요. 아래 대사·화면 자막·그림을 보고 직접 판단해 주세요 (확신 0~1)]"]
    for k, nm in (("intro", "인트로"), ("genre", "장르"), ("format", "형식"), ("captions", "자막"), ("fun", "재미 요소")):
        x = plan.get(k) or {}
        lab = " · ".join(x.get("labels") or []) if k == "format" else x.get("label", "")
        L.append(f"- {nm}: {lab} (확신 {x.get('conf', '?')})")
    L += ["", "[영상마다 숫자 (1분에 몇 번)]"]
    for p in rplans:
        nm = p.get("title") or p.get("source")
        pm = (p.get("captions") or {}).get("perMin") or {}
        cap_s = ", ".join(f"{k} {v}" for k, v in (("말 자막", pm.get("speech")), ("강조", pm.get("emph")), ("속마음", pm.get("inner")),
                                                    ("상황", pm.get("situ")), ("효과 글자", pm.get("sfx")), ("정보", pm.get("info"))) if v)
        fun_s = ", ".join(f"{x.get('label')} {x.get('perMin')}" for x in (p.get("fun") or {}).get("items") or [])
        fm = p.get("format") or {}
        talk = f"말하는 시간 {int(float(fm['talkRatio']) * 100)}%" if fm.get("talkRatio") is not None else ""
        L.append(f"· {nm}: 자막 [{cap_s or '읽은 자막 없음'}] · 재미 [{fun_s or '없음'}]" + (f" · {talk}" if talk else ""))
    L += ["", "[근거 요약]"]
    for p in rplans:
        nm = p.get("title") or p.get("source")
        ex = []
        for k in ("intro", "captions", "fun", "genre"):
            ex += [e["why"] for e in (p.get(k) or {}).get("ev") or []]
        if ex:
            L.append(f"· {nm}: " + " / ".join(ex[:8]))
    caps_rows = []
    for r in refs:
        if isinstance(r.get("source"), str):
            rows = _caps_digest(_ref_caps(r["source"]))
            if rows:
                caps_rows.append(f"· {_title(r['source'])}: " + ", ".join(rows))
    if caps_rows:
        L += ["", "[화면 자막 (글자 읽기로 읽은 것 · 길게 나온 순 · 종류는 PC 어림)]"] + caps_rows
    budget = max_chars
    tx = []
    for r in refs:
        segs = _transcript(r.get("source") or "") if isinstance(r.get("source"), str) else []
        if not segs:
            continue
        head = [f"[{mmss(s['start'])}] {str(s['text']).strip()}" for s in segs if float(s["start"]) < 60]
        tx.append(f"· {_title(r['source'])} 처음 60초:")
        for ln in head:
            if budget - len(ln) < max_chars // 2:  # 절반은 본편 몫으로 남김
                break
            tx.append("  " + ln)
            budget -= len(ln) + 3
        dur = float((r.get("plan") or {}).get("duration") or 0)
        wins = _body_windows(segs, dur)
        if wins:
            tx.append("  (본편에서 고르게 고른 대사)")
        for w in wins:
            ln = " / ".join(f"[{mmss(s['start'])}] {str(s['text']).strip()}" for s in w)
            if budget - len(ln) < 0:
                break
            tx.append("  " + ln)
            budget -= len(ln) + 3
    if tx:
        L += ["", "[대사 발췌 (받아쓰기)]"] + tx
    if images:
        L += ["", "[장면 그림 — Read 도구로 열어 보세요 (근거 장면 + 영상 곳곳에서 고르게 고른 장면)]"] + [f"- {f}: {why}" for f, why in images]
    L += ["", "[부탁해요]",
          "- 영상을 토막(시각 목록·타임라인)으로 나열하지 말고, 경력 많은 PD가 채널의 공식을 설명하듯 판단만 써 주세요.",
          "- 판단 글에는 시각(00:00)이나 '몇 초 장면' 같은 위치를 쓰지 마세요. 짧게: intro·genre·format 은 한 줄, captions·fun 은 한두 문장.",
          "- 예: 인트로 '결과 장면을 먼저 보여 주는 티저형 훅 → 바로 본론 (인사 없음, 10초 이내)', 장르 '축구 예능 챌린지'.",
          "- 내 PC 판단이 틀렸으면 판단 글에서 바로잡고, 무엇이 틀렸는지는 fix 에만 써 주세요 (summary 에는 쓰지 않음). 한국어 해요체로.",
          "- 아래 JSON 형식으로만 답해 주세요 (다른 글 없이):", AI_FORMAT]
    return "\n".join(L)


def parse_ai(text, by="paste", model=None):
    """Claude 대답 → 검증한 plan.ai (글자 길이 자름). 형식이 아니면 ValueError(한국어)."""
    s = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        s = m[1]
    else:
        a, b = s.find("{"), s.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("대답에서 JSON 형식을 찾지 못했어요. 대답 전체를 그대로 붙여 넣어 주세요")
        s = s[a:b + 1]
    try:
        d = json.loads(s)
    except ValueError:
        raise ValueError("대답의 JSON 형식이 깨져 있어요. 대답 전체를 그대로 붙여 넣어 주세요") from None
    if not isinstance(d, dict):
        raise ValueError("대답 형식이 달라요")
    out = {}
    for k in AI_KEYS:
        v = d.get(k)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        if isinstance(v, dict):
            v = " ".join(str(x) for x in v.values())
        if v is not None and str(v).strip():  # 판단 글에 시각이 섞여 오면 지움 (화면 본문에는 시각을 쓰지 않음)
            out[k] = re.sub(r"\s+", " ", TIMECODE.sub(" ", str(v))).strip()[:600 if k == "summary" else 300]
    ap = d.get("apply")
    if isinstance(ap, str):
        ap = [ap]
    out["apply"] = [re.sub(r"\s+", " ", TIMECODE.sub(" ", str(x))).strip()[:200] for x in (ap or []) if str(x).strip()][:5]
    if str(d.get("fix") or "").strip():
        out["fix"] = re.sub(r"\s+", " ", str(d["fix"])).strip()[:400]
    if sum(1 for k in AI_KEYS if k in out) < 3:
        raise ValueError("대답에 판단이 거의 없어요. 대답 전체를 그대로 붙여 넣어 주세요")
    out.update(by=by, model=model, at=time.strftime("%Y-%m-%d %H:%M"))
    return out


# ---------- Claude 판단 저장 (계정 CLI · 붙여 넣기 공통) ----------

def _ai_images(prof, folder, per_video=4, total=8):
    """장면 그림을 folder 에 640px 로 → [(경로, 이름, 설명)]: 영상마다 근거 장면(티저·타이틀·예능 자막·재미 요소) 2장 +
    영상 곳곳(25%·50%·75%)에서 고르게 고른 장면 — 영상마다 per_video 장, 모두 total 장까지 (여러 영상이면 고르게 나눔).
    썸네일 장면 캐시(thumb.grab)는 원본 크기라 건드리지 않고 따로 뽑음."""
    refs = [r for r in (prof or {}).get("refs") or [] if isinstance(r, dict) and isinstance(r.get("plan"), dict)
            and isinstance(r.get("source"), str) and core.video_file(r["source"]).is_file()]
    if not refs:
        return []
    per = max(2, min(per_video, total // len(refs)))
    out = []
    for r in refs:
        pl, src = r["plan"], r["source"]
        dur = float(pl.get("duration") or 0)
        evs = [(float(e["t"]), e["why"]) for k in ("intro", "captions", "fun") for e in (pl.get(k) or {}).get("ev") or []
               if isinstance(e.get("t"), (int, float))][:max(1, per // 2)]
        even = [(dur * f, f"영상 {int(f * 100)}% 지점 장면") for f in (0.25, 0.5, 0.75)] if dur > 20 else []
        picks = []
        for t, why in evs + even:
            if len(picks) >= per or any(abs(t - x) < 5 for x, _ in picks):
                continue
            picks.append((t, why))
        for t, why in picks:
            if len(out) >= total:
                break
            nm = f"scene{len(out) + 1}.jpg"
            f = Path(folder) / nm
            core.run([core.ffmpeg(), "-y", "-v", "error", "-ss", f"{t + 0.3:.3f}", "-i", str(core.video_file(src)),
                      "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(f)])
            if f.is_file():
                out.append((f, nm, f"{_title(src)[:30]} · {why}"))
    return out


def save_ai(style_name, ai):
    """plan.ai 저장 (다른 값은 그대로 · 임시 파일 → 교체)."""
    import style

    def put(d):
        if not isinstance(d.get("plan"), dict):
            raise style.StyleError("이 스타일은 아직 기획 분석이 없어요. 먼저 '다시 배우기'를 눌러 주세요")
        d["plan"]["ai"] = ai
    return style.update_style(style_name, put)


def run_ai(style_name, log=print, cancel=None):
    """사용자 PC 의 Claude Code(내 클로드 계정)로 스타일의 기획을 더 깊게 판단 → plan.ai. 결과 dict (화면 문구 포함)."""
    import style
    d = json.loads(style.style_file(style_name).read_text(encoding="utf-8"))
    if not isinstance(d.get("plan"), dict):
        return {"ok": False, "error": "이 스타일은 아직 기획 분석이 없어요. 먼저 '다시 배우기'를 눌러 주세요"}
    core.set_progress(label="클로드로 더 깊게 보기", item=style_name, pct=None, detail="장면 그림 고르는 중")
    with tempfile.TemporaryDirectory(prefix="futsal-scenes-", ignore_cleanup_errors=True) as tmp:
        return _run_ai(style_name, d, _ai_images(d, tmp), log, cancel)


def _run_ai(style_name, d, imgs, log, cancel):
    import claude_cli
    prompt = claude_prompt(d, images=[(nm, why) for _, nm, why in imgs])

    def tick(sec):
        core.set_progress(label="클로드로 더 깊게 보기", item=style_name, pct=None,
                          detail=f"클로드가 영상 기획을 판단하는 중… (내 클로드 계정 사용 · {sec}초)")
    try:
        res = claude_cli.run(prompt, images=[(f, nm) for f, nm, _ in imgs], cancel=cancel, on_tick=tick)
    except claude_cli.ClaudeError as e:
        log(f"클로드 판단 · {e.kind}")  # 종류만 (프롬프트·대답은 남기지 않음)
        return {"ok": False, "error": str(e), "kind": e.kind}
    try:
        ai = parse_ai(res["text"], by="claude-cli", model=res.get("model"))
    except ValueError:
        log("클로드 판단 · 형식 다름")
        return {"ok": False, "error": "클로드 대답 형식이 달라요. 다시 눌러 주세요 (내 PC 분석 결과는 그대로 있어요)", "kind": "format"}
    save_ai(style_name, ai)
    log(f"클로드 판단 · 저장했어요 · {style_name}")
    return {"ok": True, "ai": ai}
