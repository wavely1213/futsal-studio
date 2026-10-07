"""MSG 자동 편집 (D-023): 재미없는 원본 → 예능 자막·효과음·확대·다시 보기·배경음악을 넣은 편집본 후보.

흐름 (모두 PC에서 규칙으로 · AI 사용료 없음):
1. signals(name)  — 원본 신호: 받아쓰기 낱말 · 정리할 곳 · 소리 세기 · 움직임 · 공 차는 소리(순간 큰 소리) · YAMNet 웃음/환호 · 얼굴
                    (분석 폴더의 msg_signals.json 에 남김 · 영상이 그대로면 다시 안 봄)
2. moments(sig)   — 재미 순간: 시범·펀치라인·강조·놀람·성공·실패·숫자 세기·질문·장 나눔·훅·마무리·리액션
3. resolve(...)   — 스타일 다섯 부분(인트로·컷 리듬·자막·재미·음악/소리)을 기본 스타일·배운 스타일·섞은 스타일에서 가져와 합침
4. plan_events    — 스타일·양(담백·보통·듬뿍)에 맞춰 사건을 고름 (간격·동시에 보이는 글자 수·말 자막 자리 피하기 · 시드 고정)
5. compile_seq    — 사건 → 편집실 재료(titles·shapes·items·trans·markers) + seq.msg 기록 (사건마다 만든 재료 id)
build_variants(...) 가 위를 묶어 새 편집본 후보 여러 개를 돌려준다 (사용자 편집본은 건드리지 않음).
"""
import hashlib
import json
import math
import os
import random
import re
import threading
import time

import core
import editor
import sfxlib

SIG_VER = 5
MIX_DIR_NAME = "섞기"           # styles/섞기/<이름>.json — 배운 스타일 목록(list_styles)에 섞이지 않게 따로
DRAFT_NAME = "풋살사관학교 스타일(초안)"
ASPECTS = ("intro", "rhythm", "captions", "fun", "sound")
ASPECT_KO = {"intro": "인트로", "rhythm": "컷 리듬", "captions": "자막", "fun": "재미(MSG)", "sound": "음악·소리"}
INTENSITY = {"담백": 0.45, "보통": 1.0, "듬뿍": 1.7}
SPACING = {"담백": 5.0, "보통": 3.0, "듬뿍": 1.5}    # MSG 사건끼리 최소 간격(초)
LABEL = "재미 요소 찾는 중"
FACE_SAMPLES = 30               # 얼굴은 이만큼의 장면만 봄 (느린 PC)
DEMO_GAP = editor.DEMO_GAP      # 단어 사이가 이보다 길면 말 대신 보여 주는 중
ONSET_DB = 9.0                  # 0.1초 안에 이만큼 커지면 순간 큰 소리 (공 차는 소리·부딪힘)

# ---- 낱말 사전 (말을 듣고 판단) ----
PRAISE = re.compile(r"좋아요|좋습니다|나이스|이거죠|그렇죠|그렇지|들어갔|완벽|잘했|훌륭|오케이|굿")
SUCCESS = re.compile(r"성공|됐다|됐어요|들어갔|골인|나이스|완벽|좋아요")
FAIL = re.compile(r"아깝|놓쳤|실수|안 ?돼|아이고|아이구|빗나|안 ?들어|아쉽|틀렸|망했")
SURPRISE = re.compile(r"(?:^|[\s,.!?])(?:와|우와|와우|대박|헐|미쳤|오오+|어\?!|어머)(?:[\s,.!?~]|$)")
JOKE = re.compile(r"농담|ㅋㅋ|웃기|장난(?:이|입|이에|이고)|제가 원래|저도 .{0,12}못|저도 몰라")
LAUGH_THR, CHEER_THR = 0.12, 0.1   # YAMNet 웃음·환호 점수: 현장 웃음은 말소리에 섞여 낮게 나옴 (말만 있는 곳은 0.01 안팎)
EMPH_MORE = ("무조건", "절대", "생명", "핵심", "중요", "차이", "비밀", "비결", "꼭", "달라", "완벽", "정확", "제일", "가장", "포인트")  # 앞의 것부터
COUNT = re.compile(r"하나[,\s]+둘[,\s]+셋")
SECTION = re.compile(r"^(?:자[,\s]*)?(?:첫 ?번째|두 ?번째|세 ?번째|네 ?번째|다음은|다음으로|이번엔|이번에는|그 ?다음|마지막으로|자 이제)")
CLOSING = re.compile(r"오늘은 여기까지|오늘 영상은 여기|감사합니다|구독|다음 시간|다음 영상")
DEMO_W = re.compile(r"보여 ?드릴|시범|한 ?번 (?:해 ?)?볼게요|다시 (?:한 ?번 )?해 ?볼게요|직접 해 ?볼게요|해 ?보겠습니다")
HOOK_Q = re.compile(r"\?|(?<!니)까요|을까|할까|일까|될까|있을까|없을까|왜 ")
ROUTINE_Q = re.compile(r"(해|배워|가|시작해|알아|살펴|만나|들어가|차|연습해|보러 가)\s?(볼까요|봅시다|볼게요|보죠|보겠습니다)|시작할게요|시작합니다")
HOOK_W = re.compile(r"하나면|만 ?알면|달라(집니다|져요)|바뀝니다|바뀌어요|무조건|절대|이것만|핵심|비결|비밀|왜 ")
# 속마음·효과 자막은 감독님을 깎아내리지 않는 정해 둔 문구만 (외모·몸 농담 없음)
INNER_TEXTS = {"punchline": ["(머쓱)", "(뿌듯)", "(웃음 참는 중)"], "fail": ["(당황)", "(아까비…)"], "question": ["(생각 중…)"],
               "demo": ["(집중)"], "success": ["(뿌듯)"]}
FX_TEXTS = {"kick": ["뻥!", "퍽!", "슝~"], "success": ["나이스!!", "들어갔다!", "굿!"], "surprise": ["두둥!", "오오!"], "fail": ["아깝다…", "앗!"]}


LEAD = re.compile(r"^(?:자|어|음|그|아|네|예|그래서|그리고)[,.\s]+")  # 화면 글자에서 뺄 군말


class MsgError(RuntimeError):
    """화면에 그대로 보여 줄 안내."""


def _np():
    import numpy as np
    return np


def _cancelled():
    return editor.CANCEL.is_set()


def _check():
    if _cancelled():
        raise MsgError("MSG 후보 만들기를 멈췄어요")


def mmss(t):
    t = max(0, int(round(t)))
    return f"{t // 60:02d}:{t % 60:02d}"


# ---------- 1. 신호 ----------

def _sig_file(name):
    return core.adir(name) / "msg_signals.json"


def _file_sig(path):
    s = os.stat(path)
    return [s.st_size, s.st_mtime_ns]


def _words(segs):
    """받아쓰기 → 낱말 [(시작, 끝, 낱말, 구간 번호)] (낱말 시각이 없으면 구간을 글자 수로 나눠 어림)."""
    out = []
    for k, s in enumerate(segs):
        ws = [w for w in s.get("words") or () if str(w.get("w") or "").strip()]
        if ws:
            out += [(float(w["s"]), float(w["e"]), str(w["w"]).strip(), k) for w in ws]
            continue
        toks = str(s.get("text") or "").split()
        a, b = float(s["start"]), float(s["end"])
        tot = sum(len(x) for x in toks) or 1
        t = a
        for x in toks:
            d = (b - a) * len(x) / tot
            out.append((t, t + d, x, k))
            t += d
    return sorted(out)


LINE_GAP = 0.7   # 낱말 사이가 이만큼 비면 다른 문장


def lines_of(segs):
    """받아쓰기 → 문장 [{start, end, text, words}] — 받아쓰기 구간이 여러 문장을 한데 묶어도(작은 모델·빠른 말) 문장 끝·쉼에서 나눔."""
    out = []
    for s in segs:
        ws = [w for w in s.get("words") or () if str(w.get("w") or "").strip()]
        if not ws:
            out.append({"start": float(s["start"]), "end": float(s["end"]), "text": str(s.get("text") or "").strip(), "words": []})
            continue
        cur = []
        for i, w in enumerate(ws):
            cur.append(w)
            nxt = ws[i + 1] if i + 1 < len(ws) else None
            end = re.search(r"[.?!…]$", str(w["w"]).strip()) or nxt is None or float(nxt["s"]) - float(w["e"]) > LINE_GAP
            if end:
                out.append({"start": float(cur[0]["s"]), "end": float(cur[-1]["e"]), "text": " ".join(str(x["w"]).strip() for x in cur),
                            "words": [dict(x) for x in cur]})
                cur = []
    return [x for x in out if x["text"]]


def _onsets(wave, words, sr=16000):
    """순간 큰 소리 (공 차는 소리·부딪힘): 10ms 소리 세기가 0.1초 전보다 ONSET_DB 넘게 커졌다가 0.15초 안에 다시 줄어듦(탁 하고 끝나는 소리)
    · 말하는 중이 아닐 때 (말 첫소리는 이어지므로 빠짐)."""
    np = _np()
    hop = sr // 100
    n = len(wave) // hop
    if n < 20:
        return []
    x = wave[:n * hop].astype(np.float32).reshape(n, hop) / 32768.0
    db = 20 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-6)
    base = np.array([db[max(0, i - 10):i].min() if i else db[0] for i in range(n)])
    hit = np.where((db - base >= ONSET_DB) & (db > -38))[0]
    talk = [(s - 0.25, e + 0.2) for s, e, _, _ in words]
    out, last = [], -9.0
    for i in hit:
        t = i / 100.0
        if t - last < 0.25:
            continue
        pk = float(db[i:i + 4].max())
        tail = db[i + 12:i + 20]
        if len(tail) and float(tail.mean()) > pk - 8.0:  # 소리가 이어짐 (말·음악) → 공 소리 아님
            continue
        if any(a <= t <= b for a, b in talk):
            continue
        out.append(round(t, 2))
        last = t
    return out


def _levels(wave, segs, sr=16000):
    """말할 때 소리 크기(dBFS, 중앙값)와 최대 크기 — 효과음·배경음악 레벨을 이 원본에 맞추려고."""
    np = _np()
    if not len(wave):
        return -24.0, -6.0
    x = wave.astype(np.float32) / 32768.0
    vals = []
    for s in segs:
        a, b = int(float(s["start"]) * sr), int(float(s["end"]) * sr)
        if b - a > sr // 4:
            seg = x[a:b]
            vals.append(float(20 * np.log10(np.sqrt((seg * seg).mean()) + 1e-6)))
    rms = float(np.median(vals)) if vals else float(20 * np.log10(np.sqrt((x * x).mean()) + 1e-6))
    pk = float(20 * np.log10(np.percentile(np.abs(x), 99.9) + 1e-6))
    return round(rms, 1), round(pk, 1)


def _tags(path, wave, name):
    """YAMNet 소리 종류 (웃음·환호·음악) · 모델을 못 쓰면 None (웃음 순간은 말로만 찾음)."""
    import avmodels
    import plan
    try:
        ok = avmodels.ensure("audio", item=name, label=LABEL, cancel=_cancelled)
    except Exception:  # noqa: BLE001 — 받기 실패·멈춤: 소리 종류 없이 계속
        ok = False
    if not ok:
        return None

    def prog(pct, detail):
        core.set_progress(label=LABEL, item=name, pct=min(60, 30 + pct // 3), detail="웃음소리·환호 듣는 중")
    try:
        r = plan._audio_tags(wave, prog)
    except plan.PlanCancelled:
        raise MsgError("MSG 후보 만들기를 멈췄어요") from None
    if r is None:
        return None
    return {k: [round(float(v), 2) for v in r[k]] for k in ("laugh", "cheer", "music", "speech")}


def _faces_at(name, ts):
    """장면 몇 개에서 얼굴 [{box, emo}] (모델이 없으면 {})."""
    import face
    import thumb
    try:
        if not face.ensure(item=name, label=LABEL):
            return {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for t in ts[:FACE_SAMPLES]:
        _check()
        try:
            p = thumb.grab(name, t, 640)
            fs = face.faces(p) or []
        except Exception:  # noqa: BLE001 — 장면 하나가 안 돼도 계속
            continue
        out[f"{t:.2f}"] = [{"box": f["box"], "happy": round(f["emo"].get("happiness", 0), 2), "surprise": round(f["emo"].get("surprise", 0), 2)}
                           for f in fs[:2]]
    return out


def signals(name, log=print, use_faces=True):
    """원본 신호 모으기 (캐시: analysis/<영상>/msg_signals.json · 영상 지문·판이 같으면 다시 안 봄)."""
    import plan
    import style
    path = editor.video_path(name)
    fsig = _file_sig(path)
    cf = _sig_file(name)
    tr = core.adir(name) / "transcript.json"
    tsig = _file_sig(tr) if tr.exists() else None
    try:
        old = json.loads(cf.read_text(encoding="utf-8"))
        if old.get("v") == SIG_VER and old.get("sig") == fsig and old.get("tsig") == tsig:
            return old
    except (OSError, ValueError):
        pass
    if not tr.exists():
        raise MsgError("받아쓰기가 없어요. 스튜디오의 보관함에서 '편집점 찾기'를 먼저 해 주세요")
    t0 = time.time()
    log(f"재미 요소 찾기 · {name}")
    core.set_progress(label=LABEL, item=name, pct=1, detail="화면 움직임 살펴보는 중")
    info = editor.media_info(name)
    segs = editor._segments_of(name)
    words = _words(segs)
    try:
        ev = style.extract_events(name, lambda m: None, label=LABEL)
    except style.StyleCancelled:
        raise MsgError("MSG 후보 만들기를 멈췄어요") from None
    _check()
    core.set_progress(label=LABEL, item=name, pct=30, detail="소리 듣는 중")
    try:
        wave = plan._audio16(path)
    except plan.PlanCancelled:
        raise MsgError("MSG 후보 만들기를 멈췄어요") from None
    rms_db, peak_db = _levels(wave, segs)
    tags = _tags(path, wave, name)
    noisy = _spans((tags or {}).get("laugh"), 0.48, LAUGH_THR) + _spans((tags or {}).get("cheer"), 0.48, CHEER_THR)
    noisy += _spans((tags or {}).get("speech"), 0.48, 0.6, 0.3)  # 받아쓰기가 놓친 말(짧은 말·작은 소리)의 첫소리도 공 소리가 아님
    onsets = [t for t in _onsets(wave, words) if not any(a - 0.2 <= t <= b + 0.2 for a, b in noisy)]  # 웃음·환호 소리는 공 소리가 아님
    _check()
    rec = editor.recommend(name)
    sig = {"v": SIG_VER, "sig": fsig, "tsig": tsig, "name": name, "duration": float(info["duration"]),
           "w": info["width"], "h": info["height"], "fps": info.get("fps", 30.0),
           "motion": ev.get("motion") or [], "motionStep": 1.0 / float(ev.get("fps") or 2), "cuts": ev.get("cuts") or [],
           "onsets": onsets, "dialogDb": rms_db, "peakDb": peak_db, "tags": tags, "tagStep": 0.48,
           "junk": [[j["a"], j["b"], j["why"]] for j in rec.get("junk_list") or []], "tidy": rec.get("tidy") or [],
           "faces": {}}
    sig["demo"] = demo_windows(sig, words)
    sig["faces"] = _faces_at(name, _face_times(sig, segs, words)) if use_faces else {}
    tmp = cf.with_name(cf.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(sig, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cf)
    except OSError:
        tmp.unlink(missing_ok=True)
    log(f"  재미 요소 신호 · 공 차는 소리 {len(onsets)}번 · " + ("웃음·환호 들음" if tags else "소리 모델 없이") + f" · {time.time() - t0:.0f}초")
    return sig


def demo_windows(sig, words):
    """말 없는 틈 가운데 화면이 움직이거나 공 소리가 나는 곳(시범) → 남길 구간 [[a, b]].
    말로만 정리하는 가편집(tidy)은 말 없는 곳을 다 지우지만, 풋살 레슨에서는 시범이 가장 중요한 장면이라 살림.
    움직임도 소리도 없는 쉼(카메라만 켜진 곳)은 그대로 지움."""
    dur = sig["duration"]
    mz = _motion_z(sig)
    junk = sig.get("junk") or []
    out, prev = [], 0.0
    for s, e, _, _ in sorted(words) + [(dur, dur, "", -1)]:
        if s - prev >= DEMO_GAP:
            a, b = prev + 0.1, s - 0.1
            act = [t for t, z in mz if a <= t <= b and z >= 1.0] + [t for t in sig.get("onsets") or () if a <= t <= b]
            if act and not any(_in_spans((a + b) / 2, [j]) for j in junk):
                lo, hi = max(a, min(act) - 0.8), min(b, max(act) + 1.2)
                if hi - lo >= 1.2:
                    out.append([round(float(lo), 2), round(float(hi), 2)])
        prev = max(prev, e)
    return out


def keep_cuts(tidy, demo):
    """말 정리 구간 + 시범 구간을 합친 컷 목록 (겹치거나 0.3초 안으로 붙으면 하나로)."""
    ivs = sorted([(float(c["in"]), float(c["out"])) for c in tidy or []] + [(float(a), float(b)) for a, b in demo or []])
    out = []
    for a, b in ivs:
        if out and a <= out[-1][1] + 0.3:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [{"in": round(a, 3), "out": round(b, 3)} for a, b in out if b - a >= 0.2]


def _face_times(sig, segs, words):
    """얼굴을 볼 장면: 말하는 장면 고르게 12곳 + 웃음·놀람 말 직후."""
    dur = sig["duration"]
    talk = [((float(s["start"]) + float(s["end"])) / 2) for s in segs]
    even = [talk[int(k * (len(talk) - 1) / 11)] for k in range(12)] if len(talk) >= 12 else talk
    hot = [float(s["end"]) + 0.3 for s in segs if JOKE.search(s["text"]) or SURPRISE.search(" " + s["text"] + " ") or SUCCESS.search(s["text"])]
    ts = sorted({round(min(dur - 0.2, max(0.1, t)), 2) for t in even + hot})
    return ts[:FACE_SAMPLES]


# ---------- 2. 재미 순간 ----------

def _in_spans(t, spans):
    return any(a <= t < b for a, b, *_ in spans)


def _kept(t, tidy):
    return any(float(c["in"]) <= t < float(c["out"]) for c in tidy)


def _spans(arr, step, thr, gap=1.0):
    out, a, last = [], None, None
    for i, v in enumerate(arr or ()):
        if v >= thr:
            if a is None:
                a = i
            last = i
        elif a is not None and (i - last) * step > gap:
            out.append((round(a * step, 2), round((last + 1) * step, 2)))
            a = None
    if a is not None:
        out.append((round(a * step, 2), round((last + 1) * step, 2)))
    return out


def _motion_z(sig):
    """움직임 → 강도(중앙값 기준 z) 목록 [(시각, z)]."""
    np = _np()
    m = np.asarray(sig.get("motion") or [0.0], np.float64)
    st = float(sig.get("motionStep") or 0.5)
    med = float(np.median(m))
    mad = float(np.median(np.abs(m - med))) * 1.4826 + 1e-3
    return [(i * st, (float(v) - med) / mad) for i, v in enumerate(m)]


BOUND = re.compile(r"^(?:번째|째|개|번|거|것|수|때|데|건|게|지)(?:\s|$|[.!?,])")
INTENS = ("진짜", "정말", "완전", "제일", "가장", "무조건", "꼭")


def _norm_txt(t):
    return re.sub(r"[\s.,!?~…]+", "", str(t or ""))


def _emph_short(lab, line):
    """강조 글자가 말 한 줄을 통째로 옮긴 것이면(아래 말 자막과 같은 글이 두 번) 핵심 낱말만 ('진짜 핵심!' · '인사이드!')."""
    if len(_norm_txt(lab)) < 0.8 * max(1, len(_norm_txt(line))):
        return lab
    toks = re.findall(r"[가-힣A-Za-z0-9%]+", str(line or ""))
    found = editor._find_terms(toks, editor._emph_terms())
    for w in EMPH_MORE + tuple(editor.EMPH_WORDS):
        for i, tk in enumerate(toks):
            if tk.startswith(w) and w not in INTENS:
                pre = toks[i - 1] if i and toks[i - 1] in INTENS else ""
                head = f"{found[0][2]} " if found and found[0][2] not in (w,) else ""
                return (head + (pre + " " if pre and not head else "") + w + "!").strip()
    if found and len(found[0][2].replace(" ", "")) >= 2:
        return f"{found[0][2]}!"
    return lab


def _emph_clause(txt):
    """기술 이름은 없지만 강조 말(무조건·핵심·생명…)이 든 말 → 그 말부터 서술어까지 짧은 구절 ('터치가 무조건 길어져요!').
    서술어(~요·~다)로 끝나지 않거나 14글자 넘으면 None (말 조각을 띄우지 않게)."""
    toks = re.findall(r"\S+", str(txt or ""))
    for w in EMPH_MORE:
        for i, tk in enumerate(toks):
            if not tk.startswith(w):
                continue
            a = i - 1 if i > 0 and not re.search(r"[,.!?]$", toks[i - 1]) and not LEAD.match(toks[i - 1] + " ") else i
            b = i
            while b < len(toks) and b - a <= 3 and not re.search(r"(요|다|죠)[.!?~]*$", toks[b]):
                b += 1
            if b >= len(toks) or b - a > 3:
                continue
            out = re.sub(r"[,.~…]+$", "", " ".join(toks[a:b + 1])).strip()
            if 4 <= len(out.replace(" ", "")) <= 14:
                return (out if out.endswith(("!", "?")) else out + "!"), 1
    return None, 0


def _word_at(words, pat, k):
    """구간 k 안에서 pat 이 처음 나오는 낱말의 시각."""
    for s, e, w, j in words:
        if j == k and re.search(pat, w):
            return s
    return None


def moments(sig, segs=None):
    """재미 순간 목록 [{kind, a, b, t, score(0~1), text, why}] — 정리할 곳(NG·추임새) 안의 것은 뺌."""
    segs = lines_of(segs if segs is not None else editor._segments_of(sig["name"]))
    words = _words(segs)
    tidy = keep_cuts(sig.get("tidy") or [{"in": 0.0, "out": sig["duration"]}], sig.get("demo"))
    junk = sig.get("junk") or []
    dur = sig["duration"]
    out = []

    def add(kind, a, b, t, score, text="", why=""):
        if t is None or _in_spans(t, junk) or not _kept(t, tidy):
            return
        out.append({"kind": kind, "a": round(a, 2), "b": round(b, 2), "t": round(t, 2), "score": float(score), "text": text, "why": why})

    # 시범(플레이): 말 없는 틈(DEMO_GAP 넘게) 안의 움직임 봉우리·순간 큰 소리
    mz = _motion_z(sig)
    onsets = sig.get("onsets") or []
    laughs = _spans((sig.get("tags") or {}).get("laugh"), sig.get("tagStep", 0.48), LAUGH_THR)
    cheers = _spans((sig.get("tags") or {}).get("cheer"), sig.get("tagStep", 0.48), CHEER_THR)
    gaps = []
    prev = 0.0
    for s, e, _, _ in words + [(dur, dur, "", -1)]:
        if s - prev >= DEMO_GAP:
            gaps.append((prev, s))
        prev = max(prev, e)
    for a, b in gaps:
        a2, b2 = a + 0.15, b - 0.1
        ons = [t for t in onsets if a2 <= t <= b2]
        mot = [(t, z) for t, z in mz if a2 <= t <= b2]
        peak = max(mot, key=lambda x: x[1]) if mot else (None, 0.0)
        if not ons and peak[1] < 1.5:
            continue
        t = ons[0] if ons else peak[0]
        if ons:  # 공 소리가 여러 번이면 화면이 가장 크게 움직이는 때의 소리
            zat = lambda o: max([z for tm, z in mot if abs(tm - o) <= 0.75] or [0.0])  # noqa: E731
            t = max(ons, key=lambda o: (zat(o), -o))
        score = max(0.0, peak[1]) * 0.4 + 1.0 * min(3, len(ons))
        after = [w for s, e, w, _ in words if b <= s <= b + 3.0]
        praise = bool(PRAISE.search(" ".join(after)))
        react = any(b - 0.5 <= x <= b + 2.0 for x, _ in laughs + cheers)
        score += (1.5 if praise else 0) + (1.0 if react else 0)
        lo, hi = max(a2, t - 2.5), min(b2, t + 3.5)
        add("play", lo, hi, t, score, "", f"말 없는 {b - a:.1f}초 · 공 소리 {len(ons)}번" + (" · 칭찬" if praise else "") + (" · 반응" if react else ""))
        for o in ons[:6]:
            add("kick", o, o + 0.3, o, 1.0, "", "공 차는 소리")
    # 낱말로 찾는 순간
    first_q = None
    for k, s in enumerate(segs):
        txt = str(s.get("text") or "").strip()
        a, b = float(s["start"]), float(s["end"])
        sp = " " + txt + " "
        lab, sc = editor.emphasis_label(txt)
        if not lab:
            lab, sc = _emph_clause(txt)
        if lab:
            lab = _emph_short(LEAD.sub("", lab).strip() or lab, txt)
        if lab and BOUND.match(lab):  # '번째 슈팅.'처럼 앞말이 잘린 조각은 띄우지 않음
            lab = None
        if lab:
            key = lab.rstrip("!").split()[0]
            t = next((w0 for w0, _, w, j in words if j == k and w.replace(" ", "").startswith(key[:2])), a)
            add("emphasis", a, b, t, 1.0 + sc, lab, "기술 이름·강조 낱말")
        if SURPRISE.search(sp):
            add("surprise", a, b, _word_at(words, SURPRISE.pattern, k) or a, 1.5, txt, "놀람 말")
        if SUCCESS.search(txt):
            add("success", a, b, _word_at(words, SUCCESS.pattern, k) or a, 2.0, txt, "성공 말")
        if FAIL.search(txt):
            add("fail", a, b, _word_at(words, FAIL.pattern, k) or a, 2.0, txt, "아쉬움 말")
        if COUNT.search(txt):
            add("count", a, b, a, 1.0, txt, "숫자 세기")
        if SECTION.search(txt):
            add("section", a, b, a, 1.0, txt, "다음 순서로 넘어감")
        if CLOSING.search(txt) and a > dur * 0.6:
            add("closing", a, b, a, 1.0, txt, "마무리 말")
        if DEMO_W.search(txt):
            add("demo_call", a, b, a, 1.0, txt, "시범 예고")
        if HOOK_Q.search(txt) and not ROUTINE_Q.search(txt) and len(txt.replace(" ", "")) >= 6:
            add("question", a, b, a, 1.5 + (1.0 if HOOK_W.search(txt) else 0.0), txt, "질문")
            if first_q is None and a < dur * 0.3:
                first_q = (a, b, txt)
        if JOKE.search(txt):
            add("punchline", a, b, b - 0.1, 2.0, txt, "농담 말")  # (말 끝 바로 앞 · 정리 컷이 말 끝에서 끝나도 남음)
        for la, lb in laughs:  # 말이 끝나고 1.5초 안에 웃음 → 그 말이 펀치라인
            if 0 <= la - b <= 1.5:
                add("punchline", a, b, b - 0.1, 3.0, txt, "말 끝에 웃음소리")
                break
    # 훅 문장: 앞 30% 안의 첫 질문, 없으면 강한 낱말이 든 말
    if first_q:
        add("hook_line", first_q[0], first_q[1], first_q[0], 3.0, first_q[2], "앞부분 질문")
    else:
        for s in segs:
            if float(s["start"]) < dur * 0.3 and HOOK_W.search(str(s["text"])):
                add("hook_line", float(s["start"]), float(s["end"]), float(s["start"]), 2.0, str(s["text"]).strip(), "앞부분 강한 말")
                break
    # 리액션: 크게 웃거나 놀란 얼굴
    for ts, fs in (sig.get("faces") or {}).items():
        for f in fs[:1]:
            if f["box"][3] >= 0.15 and max(f["happy"], f["surprise"]) >= 0.6:
                t = float(ts)
                add("reaction", t - 0.5, t + 1.0, t, 1.0 + max(f["happy"], f["surprise"]), "", "웃거나 놀란 얼굴")
    # 같은 종류가 겹치면 점수 높은 것만 · 종류마다 점수 0~1 로
    out.sort(key=lambda m: (m["kind"], m["t"]))
    dedup = []
    for m in out:
        if dedup and dedup[-1]["kind"] == m["kind"] and abs(dedup[-1]["t"] - m["t"]) < {"closing": 6.0, "section": 3.0, "question": 3.0}.get(m["kind"], 1.0):
            if m["score"] > dedup[-1]["score"]:
                dedup[-1] = m
            continue
        dedup.append(m)
    by = {}
    for m in dedup:
        by.setdefault(m["kind"], []).append(m)
    for ms in by.values():
        hi = max(m["score"] for m in ms) or 1.0
        for m in ms:
            m["score"] = round(m["score"] / hi, 3)
    return sorted(dedup, key=lambda m: m["t"])


def face_center(sig):
    """말하는 장면의 주인공 얼굴 가운데 (0~1) — 확대할 때 기준점 · 모르면 None."""
    np = _np()
    pts = [(f["box"][0] + f["box"][2] / 2, f["box"][1] + f["box"][3] / 2) for fs in (sig.get("faces") or {}).values() for f in fs[:1] if f["box"][3] >= 0.1]
    if len(pts) < 3:
        return None
    a = np.median(np.array(pts), axis=0)
    return [round(float(a[0]), 3), round(float(a[1]), 3)]


# ---------- 3. 스타일 (다섯 부분) ----------
# 사건 1분당 기준 개수 (보통 · 무게 1) — 무게·양(INTENSITY)을 곱해 이 영상의 예산이 됨
BASE_PER_MIN = {"emphasis": 2.0, "situ": 1.0, "inner": 1.0, "fx": 1.5, "punch_zoom": 2.0, "slowmo_replay": 0.67, "freeze": 0.33,
                "shake": 0.5, "sfx": 3.0, "reaction": 1.0}

PRESETS = {
    "담백 레슨형": {
        "desc": "첫 질문으로 시작 → 작은 제목 · 흰 말 자막 + 노란 강조 자막 조금 · 딩동·휙 정도 · 잔잔한 배경음악",
        "intro": {"type": "hook_line", "clips": 1, "teaserSec": 3.0, "titleCard": True, "flash": False, "letterbox": False},
        "rhythm": {"keepPause": 0.5, "splitShot": 0, "curve3": [0, 0, 0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.08},
        "captions": {"on": True, "pos": "bottom", "color": "#FFFFFF", "karaoke": False, "emphColor": "#FFE14D", "emphFont": "Black Han Sans",
                     "emphEffect": "stamp", "situLook": "box", "perMin": {"emphasis": 1.6, "situ": 0.8, "inner": 0.0, "fx": 0.0}},
        "fun": {"punch_zoom": 0.6, "slowmo_replay": 0.5, "freeze": 0.0, "shake": 0.0, "montage": 0.0, "sfx": 0.45, "reaction": 0.3, "replayLook": "plain"},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "잔잔", "lesson": "잔잔", "demo": "경쾌", "outro": "잔잔"}, "duck": -18.0, "bgmDb": -11.0,
                  "palette": "clean"},
    },
    "예능 MSG형": {
        "desc": "명장면 3개 티저 + 번쩍 → 제목 카드 · 여러 색 예능 자막 · 확대·효과음·슬로모 다시 보기·정지 화면 · 분위기 따라 바뀌는 배경음악",
        "intro": {"type": "teaser", "clips": 3, "teaserSec": 4.5, "titleCard": True, "flash": True, "letterbox": False},
        "rhythm": {"keepPause": 0.35, "splitShot": 6.0, "curve3": [4.0, 6.0, 5.0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.12},
        "captions": {"on": True, "pos": "bottom", "color": "#FFFFFF", "karaoke": False, "emphColor": "#FFE14D", "emphFont": "Black Han Sans",
                     "emphEffect": "stamp", "situLook": "box", "perMin": {"emphasis": 2.4, "situ": 1.2, "inner": 1.0, "fx": 1.4}},
        "fun": {"punch_zoom": 1.0, "slowmo_replay": 1.0, "freeze": 1.0, "shake": 1.0, "montage": 1.0, "sfx": 1.0, "reaction": 1.0, "replayLook": "plain"},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "신남", "lesson": "잔잔", "demo": "경쾌", "outro": "신남"}, "duck": -14.0, "bgmDb": -10.0,
                  "palette": "variety"},
    },
    "쇼츠 하이텐션형": {
        "desc": "2.5초마다 컷 + 번갈아 확대 · 가운데 노래방 자막 · 효과 글자 흔들기 · 처음부터 신나는 배경음악",
        "intro": {"type": "teaser", "clips": 2, "teaserSec": 3.0, "titleCard": False, "flash": True, "letterbox": False},
        "rhythm": {"keepPause": 0.25, "splitShot": 2.5, "curve3": [2.0, 2.5, 2.5], "tempo": 0, "zoomEvery": 3.0, "zoomScale": 1.15},
        "captions": {"on": True, "pos": "middle", "color": "#FFFFFF", "karaoke": True, "emphColor": "#FFD400", "emphFont": "Black Han Sans",
                     "emphEffect": "pop", "situLook": "box", "perMin": {"emphasis": 2.0, "situ": 0.8, "inner": 0.8, "fx": 2.2}},
        "fun": {"punch_zoom": 1.3, "slowmo_replay": 0.8, "freeze": 0.6, "shake": 1.5, "montage": 1.0, "sfx": 1.4, "reaction": 1.0, "replayLook": "plain"},
        "sound": {"lufs": -13.0, "bgm": True, "moods": {"intro": "신남", "lesson": "신남", "demo": "신남", "outro": "신남"}, "duck": -12.0, "bgmDb": -9.0,
                  "palette": "variety"},
    },
    "다큐 감성형": {
        "desc": "제목 화면으로 시작 + 위아래 검은 띠 · 왼쪽 위 상황 자막 · 명장면 슬로모·흑백 정지 · 효과음은 묵직하게 조금 · 감성 배경음악",
        "intro": {"type": "title_first", "clips": 1, "teaserSec": 3.0, "titleCard": True, "flash": False, "letterbox": True},
        "rhythm": {"keepPause": 0.6, "splitShot": 0, "curve3": [0, 0, 0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.06},
        "captions": {"on": True, "pos": "bottom", "color": "#FFFFFF", "karaoke": False, "emphColor": "#FFFFFF", "emphFont": "Do Hyeon",
                     "emphEffect": "fade", "situLook": "plain", "perMin": {"emphasis": 1.0, "situ": 1.2, "inner": 0.0, "fx": 0.0}},
        "fun": {"punch_zoom": 0.4, "slowmo_replay": 1.4, "freeze": 1.2, "shake": 0.0, "montage": 0.6, "sfx": 0.35, "reaction": 0.3, "replayLook": "letterbox"},
        "sound": {"lufs": -15.0, "bgm": True, "moods": {"intro": "감성", "lesson": "감성", "demo": "감성", "outro": "감성"}, "duck": -18.0, "bgmDb": -10.0,
                  "palette": "cinematic"},
    },
}
# 배운 값을 묶어 둘 범위 (이상한 레퍼런스 하나가 터무니없는 양을 만들지 않게) — 기본 스타일 범위의 바깥쪽 조금까지
BANDS = {"keepPause": (0.2, 0.8), "splitShot": (0.0, 12.0), "zoomEvery": (0.0, 20.0), "zoomScale": (1.0, 1.3), "tempo": (0.0, 9.0),
         "perMin": (0.0, 3.0), "weight": (0.0, 1.6), "duck": (-20.0, -10.0), "lufs": (-16.0, -12.0), "bgmDb": (-14.0, -8.0)}
CAP_Y = {"bottom": 0.9, "middle": 0.62, "top": 0.16}


def _clamp(v, band):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return band[0]
    return round(min(band[1], max(band[0], v)), 3) if math.isfinite(v) else band[0]


def _copy(d):
    return json.loads(json.dumps(d, ensure_ascii=False))


def styles_dir():
    import style
    return style.STYLES


def mix_dir():
    return styles_dir() / MIX_DIR_NAME


def _learned(name):
    """배운 스타일 하나 → list_styles 의 한 줄 (없으면 None)."""
    import style
    return next((s for s in style.list_styles() if s["name"] == name), None)


def learned_aspects(st):
    """배운 스타일(list_styles 한 줄) → 다섯 부분 값 (기본 스타일 범위로 묶음)."""
    p = st.get("params") or {}
    pl = st.get("plan") or {}
    base = _copy(PRESETS["예능 MSG형"])
    intro = pl.get("intro") or {}
    it = intro.get("type") if intro.get("type") in ("teaser", "hook_line", "title_first", "greeting", "cold_open") else "cold_open"
    base["intro"] = {"type": it, "clips": 3 if it == "teaser" else 1, "teaserSec": _clamp(intro.get("teaserSec") or 4, (3.0, 6.0)),
                     "titleCard": bool(intro.get("titleCard")) or it == "title_first", "flash": it == "teaser", "letterbox": False}
    every = _clamp(p.get("zoomEvery") or 0, BANDS["zoomEvery"])
    base["rhythm"] = {"keepPause": _clamp(p.get("keepPause") or 0.4, BANDS["keepPause"]), "splitShot": _clamp(p.get("splitShot") or 0, BANDS["splitShot"]),
                      "curve3": [_clamp(x, BANDS["splitShot"]) for x in (p.get("curve3") or [0, 0, 0])][:3],
                      "tempo": _clamp(p.get("tempo") or 0, BANDS["tempo"]), "zoomEvery": every if every >= 2 else 0,
                      "zoomScale": _clamp(p.get("zoomScale") or 1.1, BANDS["zoomScale"])}
    cj = pl.get("captions") or {}
    pm = cj.get("perMin") or {}
    colors = [c for c in (cj.get("colors") or []) if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(c)) and str(c).upper() not in ("#FFFFFF", "#111111", "#000000")]
    sty = cj.get("style") or "speech"
    base["captions"] = {"on": bool(p.get("captions", True)), "pos": p.get("captionPos") if p.get("captionPos") in CAP_Y else "bottom",
                        "color": str(p.get("captionColor") or "#FFFFFF").upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(p.get("captionColor") or "")) else "#FFFFFF",
                        "karaoke": False, "emphColor": colors[0].upper() if colors else "#FFE14D", "emphFont": "Black Han Sans",
                        "emphEffect": "stamp" if sty in ("variety", "mixed") else "pop", "situLook": "box",
                        "perMin": {"emphasis": _clamp(cj.get("keywordPerMin") or pm.get("emph") or (1.0 if sty in ("variety", "mixed") else 0.6), BANDS["perMin"]),
                                   "situ": _clamp(pm.get("situ") or 0.5, BANDS["perMin"]), "inner": _clamp(pm.get("inner") or 0, BANDS["perMin"]),
                                   "fx": _clamp(pm.get("sfx") or 0, BANDS["perMin"])}}
    fun = {x["key"]: float(x.get("perMin") or 0) for x in ((pl.get("fun") or {}).get("items") or []) if isinstance(x, dict) and x.get("key")}
    w = lambda k, base_pm: _clamp(fun.get(k, 0) / base_pm, BANDS["weight"])  # noqa: E731
    base["fun"] = {"punch_zoom": max(w("punch_zoom", 1.0), 0.3), "slowmo_replay": max(w("slowmo_replay", 0.3), w("replay", 0.3)), "freeze": w("freeze", 0.2),
                   "shake": w("shake", 0.3), "montage": 1.0 if fun.get("montage") else 0.0, "sfx": max(w("sfx", 1.5), 0.3),
                   "reaction": w("reaction_cut", 0.5), "replayLook": "plain"}
    snd = _copy(PRESETS["예능 MSG형"]["sound"])
    snd["lufs"] = _clamp(p.get("lufs") if p.get("lufs") is not None else -14, BANDS["lufs"])
    if not intro.get("bgmAtStart"):
        snd["moods"]["intro"] = "잔잔"
    base["sound"] = snd
    base["desc"] = st.get("desc") or ""
    return base


def preset_list():
    return [{"name": k, "desc": v["desc"], "kind": "preset"} for k, v in PRESETS.items()]


def _mix_path(name):
    n = re.sub(r'[\\/:*?"<>|]', "", str(name or "")).strip(" .")
    if not n:
        raise ValueError("이름을 적어 주세요")
    return mix_dir() / f"{n}.json"


def load_mix(name):
    """섞은 스타일 파일 → {name, aspects{부분: {kind, name}}, intensity, history[]} (없으면 기본: 모든 부분 '예능 MSG형')."""
    p = _mix_path(name)
    d = {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    asp = d.get("aspects") if isinstance(d.get("aspects"), dict) else {}
    out = {"name": p.stem, "aspects": {}, "intensity": d.get("intensity") if d.get("intensity") in INTENSITY else "보통",
           "history": [h for h in (d.get("history") or []) if isinstance(h, dict)][-20:], "exists": p.exists()}
    for a in ASPECTS:
        src = asp.get(a) if isinstance(asp.get(a), dict) else {}
        kind, nm = src.get("kind"), str(src.get("name") or "")
        out["aspects"][a] = {"kind": kind, "name": nm} if kind in ("preset", "style") and nm else {"kind": "preset", "name": "예능 MSG형"}
    return out


def save_mix(name, aspects=None, intensity=None, pick=None):
    """섞은 스타일 저장 (임시 파일 → 바꿔 끼우기). pick=(부분, {kind, name}) 이면 그 부분만 바꾸고 '최근 바꾼 것'에 남김."""
    m = load_mix(name)
    if aspects:
        for a in ASPECTS:
            src = aspects.get(a)
            if isinstance(src, dict) and src.get("kind") in ("preset", "style") and src.get("name"):
                m["aspects"][a] = {"kind": src["kind"], "name": str(src["name"])}
    if intensity in INTENSITY:
        m["intensity"] = intensity
    if pick:
        a, src = pick
        if a not in ASPECTS or not isinstance(src, dict) or src.get("kind") not in ("preset", "style") or not src.get("name"):
            raise ValueError("고를 수 없는 부분이에요")
        m["aspects"][a] = {"kind": src["kind"], "name": str(src["name"])}
        m["history"] = (m["history"] + [{"t": time.strftime("%Y-%m-%d %H:%M"), "aspect": a, "source": src["name"]}])[-20:]
    p = _mix_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"v": 1, "name": p.stem, "aspects": m["aspects"], "intensity": m["intensity"], "history": m["history"]}
    tmp = p.with_name(p.name + f".{os.getpid()}_{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    for k in range(10):  # Windows: 백신·OneDrive 가 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, p)
            break
        except PermissionError:
            if k == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)
    return load_mix(p.stem)


def list_mixes():
    d = mix_dir()
    if not d.is_dir():
        return []
    return [load_mix(p.stem) for p in sorted(d.glob("*.json"))]


def sources_listing():
    """스타일 고르기 목록: 기본 스타일 4개 · 섞은 스타일 · 배운 스타일 (부분마다 한 줄 설명)."""
    import style
    pre = [{"kind": "preset", "name": k, "desc": v["desc"], "aspects": {a: describe_aspect(a, v[a]) for a in ASPECTS}} for k, v in PRESETS.items()]
    learned = []
    for st in style.list_styles():
        asp = learned_aspects(st)
        learned.append({"kind": "style", "name": st["name"], "desc": st.get("plan_desc") or st.get("desc") or "", "aspects": {a: describe_aspect(a, asp[a]) for a in ASPECTS}})
    mixes = [{"kind": "mix", "name": m["name"], "desc": " · ".join(f"{ASPECT_KO[a]} {m['aspects'][a]['name']}" for a in ASPECTS), "intensity": m["intensity"]}
             for m in list_mixes()]
    return {"presets": pre, "learned": learned, "mixes": mixes, "aspects": ASPECT_KO, "draft": DRAFT_NAME, "intensities": list(INTENSITY)}


def mix_view(name):
    """섞은 스타일 + 부분마다 설명 (원래 스타일이 지워졌으면 그 표시)."""
    m = load_mix(name)
    notes = []
    rows = {}
    for a in ASPECTS:
        val, used = _aspect_from(m["aspects"][a], a, notes)
        rows[a] = {"source": m["aspects"][a], "used": used, "desc": describe_aspect(a, val),
                   "fallback": used != m["aspects"][a]["name"]}
    return {"name": m["name"], "exists": m["exists"], "intensity": m["intensity"], "aspects": rows, "history": m["history"][-5:][::-1], "notes": notes}


def _aspect_from(src, aspect, notes):
    """{kind, name} 의 한 부분 값. 배운 스타일이 지워졌으면 기본 스타일 값 + 알림."""
    kind, nm = (src or {}).get("kind"), (src or {}).get("name")
    if kind == "preset" and nm in PRESETS:
        return _copy(PRESETS[nm][aspect]), nm
    if kind == "style":
        st = _learned(nm)
        if st:
            return learned_aspects(st)[aspect], nm
        notes.append(f"{ASPECT_KO[aspect]}: '{nm}' 스타일이 지워져 기본값(예능 MSG형)으로 했어요")
    return _copy(PRESETS["예능 MSG형"][aspect]), "예능 MSG형"


def resolve(spec):
    """후보 스타일 {kind: preset|style|mix, name} → 다섯 부분을 합친 MSG 스타일."""
    kind, nm = spec.get("kind"), str(spec.get("name") or "")
    notes = []
    if kind == "mix":
        m = load_mix(nm)
        out = {"label": m["name"], "kind": "mix", "sources": {}, "notes": notes}
        for a in ASPECTS:
            out[a], out["sources"][a] = _aspect_from(m["aspects"][a], a, notes)
        return out
    if kind == "style":
        st = _learned(nm)
        if not st:
            raise MsgError(f"'{nm}' 스타일을 찾지 못했어요")
        asp = learned_aspects(st)
        return dict({a: asp[a] for a in ASPECTS}, label=nm, kind="style", sources={a: nm for a in ASPECTS}, notes=notes)
    if nm not in PRESETS:
        raise MsgError(f"'{nm}' 스타일을 찾지 못했어요")
    return dict({a: _copy(PRESETS[nm][a]) for a in ASPECTS}, label=nm, kind="preset", sources={a: nm for a in ASPECTS}, notes=notes)


def describe_aspect(aspect, val):
    """부분 값 → 한 줄 설명 (스타일 섞기 화면)."""
    if aspect == "intro":
        t = {"teaser": f"명장면 {val.get('clips', 3)}개 미리 보기", "hook_line": "첫 질문(훅)으로 시작", "title_first": "제목 화면으로 시작",
             "greeting": "인사로 시작", "cold_open": "바로 본론"}.get(val.get("type"), "바로 본론")
        return t + (" → 제목 카드" if val.get("titleCard") else "") + (" · 번쩍 전환" if val.get("flash") else "") + (" · 위아래 검은 띠" if val.get("letterbox") else "")
    if aspect == "rhythm":
        s = f"말 사이 {val.get('keepPause', 0.4)}초 넘게 쉬면 자름"
        if val.get("splitShot"):
            s += f" · 긴 말은 {val['splitShot']}초마다 나눠 번갈아 확대"
        if val.get("zoomEvery"):
            s += f" · {val['zoomEvery']}초마다 확대 컷"
        return s
    if aspect == "captions":
        pm = val.get("perMin") or {}
        parts = [f"강조 1분 {pm.get('emphasis', 0):g}개"]
        if pm.get("situ"):
            parts.append(f"상황 {pm['situ']:g}")
        if pm.get("inner"):
            parts.append(f"속마음 {pm['inner']:g}")
        if pm.get("fx"):
            parts.append(f"효과 글자 {pm['fx']:g}")
        return ("노래방 말 자막 · " if val.get("karaoke") else f"말 자막 {({'bottom': '아래', 'middle': '가운데', 'top': '위'}).get(val.get('pos'), '아래')} · ") + " · ".join(parts)
    if aspect == "fun":
        names = {"punch_zoom": "확대", "slowmo_replay": "슬로모 다시 보기", "freeze": "정지 화면", "shake": "흔들기", "montage": "몽타주", "sfx": "효과음"}
        on = [f"{v}" + ("↑" if float(val.get(k) or 0) >= 1.2 else "") for k, v in names.items() if float(val.get(k) or 0) >= 0.3]
        return " · ".join(on) or "재미 요소 거의 없음"
    m = val.get("moods") or {}
    return (f"배경음악 {m.get('intro', '')}→{m.get('lesson', '')}·{m.get('demo', '')}" if val.get("bgm") else "배경음악 없음") + \
        f" · 말할 때 {abs(int(val.get('duck', -14)))}dB 줄임 · 효과음 {({'clean': '깔끔', 'variety': '예능', 'cinematic': '묵직'}).get(val.get('palette'), '예능')}"


# ---------- 4. 사건 고르기 ----------
SFX_PALETTE = {  # 사건 → 효과음 (없으면 소리 없음)
    "clean": {"success": "딩동", "section": "딸깍", "replay": "휙", "title": "짠", "count": "틱", "question": "물음표", "teaser": "휙", "emphasis": "팡",
              "freeze": "찰칵", "montage": "휙", "end": "맑은 짧은 음악"},
    "variety": {"success": "레벨업", "fail": "띠로리", "surprise": "두둥", "punchline": "바둠츠", "kick": "뻥", "emphasis": "뽁", "question": "물음표",
                "section": "휙", "count": "틱", "freeze": "찰칵", "replay": "휙", "title": "짠", "teaser": "휙", "inner": "띠용", "montage": "휙",
                "end": "경쾌 짧은 음악"},
    "cinematic": {"kick": "퍽", "replay": "라이저", "freeze": "찰칵", "title": "쿵", "success": "맑은 짧은 음악", "teaser": "휙", "section": "딸깍",
                  "end": "맑은 짧은 음악"},
}
CLEAN_ONLY = {"휙", "딩동", "짠", "틱", "딸깍", "팡", "맑은 짧은 음악", "찰칵", "물음표"}   # 담백: 이 소리들만
TEXT_KINDS = ("emphasis", "situ", "inner", "fx", "count")


def _seed(*parts):
    return int(hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:8], 16)


def _situ_text(m):
    txt = m.get("text") or ""
    mm = re.search(r"(첫|두|세|네|다섯) ?번째", txt)
    if mm:
        return f"{mm[1]} 번째 포인트"
    if re.search(r"마지막", txt):
        return "마지막 포인트"
    if m["kind"] == "demo_call":
        return "시범 들어갑니다"
    if m["kind"] == "play":
        return "실전 시범"
    return "다음 순서"


def caption_y(st, fmt):
    pos = st["captions"].get("pos") or "bottom"
    if st["captions"].get("karaoke"):
        return 0.62 if fmt == "long" else 0.6
    return {"bottom": 0.9 if fmt == "long" else 0.8, "middle": 0.62 if fmt == "long" else 0.6, "top": 0.16}[pos]


def text_looks(st, fmt, cap_y):
    """사건별 글자 모양 (위치는 말 자막 자리에서 0.15 넘게 떨어뜨림)."""
    c = st["captions"]
    sh = fmt == "shorts"
    low = cap_y < 0.5  # 말 자막이 위쪽이면 MSG 글자는 아래쪽으로
    T = editor.TITLE_STYLE
    emph = dict(T, font=c.get("emphFont") or "Black Han Sans", weight="Black", size=110 if sh else 96, fill=c.get("emphColor") or "#FFE14D",
                stroke="#111111", strokeW=9, y=(0.66 if low else 0.3) if not sh else (0.66 if low else 0.36), x=0.5, align="center",
                effect=c.get("emphEffect") or "stamp")
    if c.get("situLook") == "plain":
        situ = dict(T, weight="Bold", size=58 if sh else 50, fill="#FFFFFF", stroke="#000000", strokeW=5, bgOn=False, x=0.06, y=(0.86 if low else 0.14) if not sh else 0.18,
                    align="left", effect="fade", font="Do Hyeon")
    else:
        situ = dict(T, weight="Bold", size=58 if sh else 54, fill="#FFFFFF", stroke="#000000", strokeW=10, bgOn=True, bg="#0F7A3D", bgOpacity=0.9,
                    x=0.06, y=(0.86 if low else 0.14) if not sh else 0.18, align="left", effect="slide")
    inner = dict(T, font="Do Hyeon", weight="Bold", size=70 if sh else 62, fill="#9AD7FF", stroke="#000000", strokeW=6, x=0.74 if not sh else 0.7,
                 y=0.45 if not low else 0.6, align="center", effect="fade", rot=-4)
    fx = dict(T, font="Black Han Sans", weight="Black", size=150 if sh else 132, fill="#FF9F1C", stroke="#111111", strokeW=10, x=0.5,
              y=(0.5 if not low else 0.6) if not sh else 0.48, align="center", effect="shake", rot=0)
    return {"emphasis": emph, "situ": situ, "inner": inner, "fx": fx, "count": dict(fx, fill="#FFFFFF", size=140 if sh else 120, effect="stamp")}


def plan_events(sig, moms, st, intensity, fmt, seed, kept, words):
    """재미 순간 → 사건 (원본 시각 기준) · 예산·간격·동시에 보이는 글자 수 지키기 · 시드 고정."""
    rng = random.Random(seed)
    mult = INTENSITY[intensity]
    gap = SPACING[intensity]
    minutes = max(0.5, sum(float(c["out"]) - float(c["in"]) for c in kept) / 60.0)
    if fmt == "shorts":
        mult *= 1.5
    cap, fun = st["captions"], st["fun"]
    pal = SFX_PALETTE.get(st["sound"].get("palette"), SFX_PALETTE["variety"])
    mild = intensity == "담백"

    def kept_at(t):
        return any(float(c["in"]) + 0.05 <= t < float(c["out"]) - 0.05 for c in kept)

    def budget(per_min, cap_n=None):
        n = int(per_min * minutes * mult + 0.5)
        return min(n, cap_n) if cap_n is not None else n

    pm = cap.get("perMin") or {}
    B = {"emphasis": budget(pm.get("emphasis", 0)), "situ": budget(pm.get("situ", 0)),
         "inner": 0 if mild else budget(pm.get("inner", 0)), "fx": 0 if mild else budget(pm.get("fx", 0)),
         "punch": budget(BASE_PER_MIN["punch_zoom"] * fun.get("punch_zoom", 0), int(minutes + 0.99) if mild else None),
         "replay": budget(BASE_PER_MIN["slowmo_replay"] * fun.get("slowmo_replay", 0) / 1.0, 1 if mild else max(1, int(minutes / 1.5)) if fun.get("slowmo_replay", 0) > 0 else 0),
         "freeze": 0 if mild else budget(BASE_PER_MIN["freeze"] * fun.get("freeze", 0), max(1, int(minutes / 3)) if fun.get("freeze", 0) > 0 else 0),
         "shake": 0 if mild else budget(BASE_PER_MIN["shake"] * fun.get("shake", 0), 1 if intensity == "보통" else None),
         "sfx": budget(BASE_PER_MIN["sfx"] * fun.get("sfx", 0)), "count": 99}
    if fun.get("slowmo_replay", 0) > 0 and B["replay"] == 0 and any(m["kind"] == "play" for m in moms):
        B["replay"] = 1
    cands = []

    def cand(kind, m, pri, **kw):
        t = kw.pop("t", m["t"])
        if not kept_at(t):
            return
        cands.append(dict({"kind": kind, "t": round(t, 2), "pri": pri, "src": m["kind"], "why": m.get("why", ""), "mt": m.get("text", "")}, **kw))

    plays = sorted([m for m in moms if m["kind"] == "play"], key=lambda m: -m["score"])
    for m in moms:
        k, sc = m["kind"], m["score"]
        if k == "emphasis":
            cand("emphasis", m, 2.0 + sc, text=m["text"], dur=1.6)
        elif k in ("section", "demo_call"):
            cand("situ", m, 1.6 + sc, text=_situ_text(m), dur=2.2)
        elif k == "count" and re.search(r"하나[,\s]+둘", m.get("text") or ""):
            cand("count", m, 1.5, text="하나 둘 셋", dur=0.7)
        elif k == "punchline":
            cand("inner", m, 1.8 + sc, text=rng.choice(INNER_TEXTS["punchline"]), dur=1.6, t=m["t"])
            cand("punch", m, 1.7 + sc, dur=2.2, t=max(m["a"], m["b"] - 1.2))
        elif k == "fail":
            cand("fx", m, 1.6 + sc, text=rng.choice(FX_TEXTS["fail"]), dur=1.0, look="fail")
            cand("inner", m, 1.4 + sc, text=rng.choice(INNER_TEXTS["fail"]), dur=1.5, t=m["t"] + 0.6)
        elif k == "success":
            cand("fx", m, 1.9 + sc, text=rng.choice(FX_TEXTS["success"]), dur=1.1, look="success")
        elif k == "surprise":
            cand("fx", m, 1.3 + sc, text=rng.choice(FX_TEXTS["surprise"]), dur=1.0, look="surprise")
            cand("shake", m, 1.0 + sc, dur=0.35)
        elif k == "reaction":
            cand("punch", m, 1.5 + sc, dur=2.0, t=m["a"])
        elif k == "question":
            cand("inner", m, 1.0 + sc, text=INNER_TEXTS["question"][0], dur=1.5, t=m["b"] + 0.1)
        elif k == "kick":
            pass
        if k == "emphasis" and not mild:
            cand("punch", m, 1.2 + sc, dur=2.4, t=m["t"])
    for i, m in enumerate(plays):
        ons = [o for o in sig.get("onsets") or () if m["a"] <= o <= m["b"]]
        if ons:
            cand("fx", m, 1.2 + m["score"], text=rng.choice(FX_TEXTS["kick"]), dur=0.9, look="kick", t=ons[0])
            if i < 3:
                cand("shake", m, 0.9 + m["score"], dur=0.35, t=ons[0])
        cand("replay", m, 2.0 + m["score"], a=m["a"], b=m["b"], at=m["b"])
        after = [x for x in moms if x["kind"] in ("fail", "success") and m["b"] - 0.5 <= x["t"] <= m["b"] + 5.0]
        if after:
            cand("freeze", m, 1.8 + m["score"], at=m["a"], turn=after[0]["kind"], t=m["a"] + 0.05)
        if i < 4 and not any(x["kind"] in ("demo_call", "section") and abs(x["t"] - m["a"]) < 6 for x in moms):
            cand("situ", m, 0.8 + m["score"] * 0.5, text="실전 시범", dur=2.0, t=max(m["a"], m["t"] - 1.0))
    cands.sort(key=lambda c: (-c["pri"], c["t"]))
    used = {k: 0 for k in B}
    picked = []
    texts = []  # (a, b, 자리)

    def free_text(a, b, slot):
        on = [x for x in texts if x[0] < b and a < x[1]]
        return len(on) < 2 and not any(x[2] == slot for x in on)

    for c in cands:
        k = c["kind"]
        bk = "fx" if k == "fx" else k
        if used.get(bk, 0) >= B.get(bk, 0):
            continue
        t = c["t"]
        if k in TEXT_KINDS:
            a, b = t, t + c["dur"]
            if not free_text(a, b, k):
                continue
            near = [p for p in picked if p["kind"] in TEXT_KINDS and abs(p["t"] - t) < gap]
            if near:
                continue
        elif k in ("punch", "shake"):
            if any(p["kind"] in ("punch", "shake") and abs(p["t"] - t) < max(gap, 3.0) for p in picked):
                continue
        elif k in ("replay", "freeze"):
            if any(p["kind"] in ("replay", "freeze") and abs(p["t"] - t) < 20.0 for p in picked):
                continue
        picked.append(c)
        used[bk] = used.get(bk, 0) + 1
        if k in TEXT_KINDS:
            texts.append((t, t + c["dur"], k))
    # 효과음: 사건에 붙임 (담백은 깔끔한 소리만 · 간격 1.2초)
    sfx_budget = B["sfx"]
    order = sorted(picked, key=lambda c: -c["pri"])
    sfx_t = []
    for c in order:
        key = {"fx": c.get("look") or "kick", "inner": "inner", "situ": "section", "emphasis": "emphasis", "count": "count",
               "replay": "replay", "freeze": "freeze", "shake": None, "punch": None}.get(c["kind"])
        if c["kind"] == "fx" and c.get("look") == "kick":
            key = "kick"
        if c["kind"] == "inner" and c["src"] == "punchline":
            key = "punchline"
        name = pal.get(key) if key else None
        if not name or (mild and name not in CLEAN_ONLY):
            continue
        if c["kind"] not in ("replay", "freeze", "count"):
            if sfx_budget <= 0:
                continue
            sfx_budget -= 1
        if any(abs(c["t"] - x) < 1.2 for x in sfx_t) and c["kind"] not in ("replay", "freeze"):
            continue
        c["sfx"] = name
        sfx_t.append(c["t"])
    # 몽타주: 시범이 4개 넘을 때 (담백 제외)
    mont = None
    if not mild and fmt == "long" and fun.get("montage", 0) >= 0.5 and len(plays) >= 4:
        mont = sorted(plays[:8], key=lambda m: m["t"])
    return sorted(picked, key=lambda c: c["t"]), mont


# ---------- 5. 편집본 만들기 ----------
TEASER_FLASH = 0.24     # 티저 사이 흰 번쩍 길이(초)
TITLE_CARD = 2.0        # 제목 카드 길이
END_SEC = 8.0           # 엔드 화면 (유튜브 최종 화면 5~20초)
REPLAY_SPEED = 0.5
REPLAY_SRC = 3.0        # 다시 보기에 쓰는 원본 길이(초)
FREEZE_SEC = 1.7
MONTAGE_CLIP = 0.65
GREEN = "#0F7A3D"


def _freeze(name, t):
    """정지 화면 PNG (영상 끝 근처라 장면을 못 뽑으면 조금씩 앞에서 다시)."""
    last = None
    for back in (0.0, 0.5, 1.5, 3.0):
        try:
            return editor.freeze_frame("videos", name, round(max(0.0, t - back), 2))
        except RuntimeError as e:
            last = e
    raise last


def _mid(file):
    return "msg_" + hashlib.sha1(file.encode("utf-8")).hexdigest()[:8]


class _Build:
    """타임라인을 앞에서부터 차례로 쌓는 도우미 (V1/A1 쌍·정지 화면·전환·효과음·사건 기록)."""

    def __init__(self, name, info, fmt):
        self.name, self.info, self.fmt = name, info, fmt
        self.items, self.trans, self.titles, self.shapes, self.markers = [], [], [], [], []
        self.pos = 0.0
        self.media = {}
        self.sfx = []      # (타임라인 시각, 이름, 레벨 dB, 사건)
        self.events = []
        self.main = []     # 본편 V1 클립 (원본 → 타임라인 바꾸기용)

    def clip(self, a, b, speed=1.0, fx=None, nocaps=False, mute=False, vol_db=0.0, main=False, color=None):
        v, au = editor._pair(a, b, start=round(self.pos, 4))
        if fx:
            v["fx"] = fx
        if color:
            v["color"] = color
        if abs(speed - 1) > 1e-6:
            v["speed"] = au["speed"] = round(speed, 4)
        if nocaps:
            v["noCaps"] = au["noCaps"] = True
        if mute:
            au["mute"] = True
        if abs(vol_db) > 0.05:
            au["fx"] = {"level": {"v": round(vol_db, 2), "k": []}}
        self.items += [v, au]
        if main:
            self.main.append(v)
        self.pos = editor.i_end(v)
        return v, au

    def still(self, mid, dur, zoom=(100.0, 106.0), color=None):
        v = {"id": editor._nid(), "track": "V1", "media": mid, "start": round(self.pos, 4), "in": 0.0, "out": round(dur, 3), "speed": 1.0, "rev": False,
             "link": None, "reframe": 0.5, "fit": "auto", "color": color or {},
             "fx": {"scale": {"v": zoom[0], "k": [{"t": 0.0, "v": zoom[0], "e": "lin"}, {"t": round(dur, 3), "v": zoom[1], "e": "lin"}]}}}
        self.items.append(v)
        self.pos += dur
        return v

    def flash(self, a, b, typ="white", dur=TEASER_FLASH):
        if a and b:
            tr = {"id": editor._nid(), "track": "V1", "a": a["id"], "b": b["id"], "type": typ, "dur": round(dur, 3), "align": "center"}
            self.trans.append(tr)
            return tr
        return None

    def title(self, text, start, dur, look, **extra):
        t = {"id": editor._nid(), "text": text, "start": round(start, 3), "dur": round(dur, 3), "style": dict(look, **extra), "msg": True}
        self.titles.append(t)
        return t

    def shape(self, x, y, w, h, color, start, dur, opacity=1.0, radius=0, name="도형"):
        s = {"id": editor._nid(), "name": name, "x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4), "color": color,
             "opacity": opacity, "radius": radius, "start": round(start, 3), "dur": round(dur, 3), "full": False}
        self.shapes.append(s)
        return s

    def event(self, kind, t, text="", why="", src=None, refs=None, ins=None):
        e = {"id": editor._nid(), "kind": kind, "t": round(t, 2), "text": text, "why": why, "src": None if src is None else round(src, 2),
             "refs": refs or {}}
        if ins:
            e["ins"] = ins
        self.events.append(e)
        return e

    def src_to_tl(self, t):
        for it in self.main:
            if float(it["in"]) <= t < float(it["out"]):
                return float(it["start"]) + (t - float(it["in"])) / editor.i_sp(it)
        return None


def _split(pieces, t, min_len=0.25):
    """조각 목록에서 원본 t 초를 경계로 나눔 (너무 짧은 조각이 생기면 안 나눔) → 그 경계 뒤 조각 번호 또는 None."""
    for i, p in enumerate(pieces):
        if p["in"] + min_len <= t <= p["out"] - min_len:
            a, b = dict(p, out=round(t, 3)), dict(p, **{"in": round(t, 3)})
            for k in ("punch", "shake"):
                a.pop(k, None)
            pieces[i:i + 1] = [a, b]
            return i + 1
        if abs(p["in"] - t) < min_len:
            return i
    return None


def _shake_keys(t0, speed, seed):
    rng = random.Random(seed)
    ks = []
    for k in range(9):
        dx = 0.0 if k in (0, 8) else rng.uniform(-0.012, 0.012)
        dy = 0.0 if k in (0, 8) else rng.uniform(-0.01, 0.01)
        ks.append({"t": round(t0 + k * 0.045 * speed, 4), "v": [round(0.5 + dx, 4), round(0.5 + dy, 4)], "e": "lin"})
    return ks


def _teaser_windows(moms, n, dur, junk):
    """티저에 쓸 장면: 시범(점수 순) → 펀치라인 → 성공 → 놀람 → 리액션 (서로 6초 넘게 떨어진 곳 · NG 밖)."""
    pri = {"play": 0, "punchline": 1, "success": 2, "surprise": 3, "reaction": 4}
    cands = sorted([m for m in moms if m["kind"] in pri], key=lambda m: (pri[m["kind"]], -m["score"]))
    out = []
    for m in cands:
        if m["kind"] == "play":
            a, b = m["t"] - 0.6, m["t"] + 1.1
        elif m["kind"] == "punchline":
            a, b = m["b"] - 1.0, m["b"] + 0.8
        else:
            a, b = m["t"] - 0.4, m["t"] + 1.3
        a, b = max(0.0, a), min(dur, b)
        if b - a < 1.0 or any(_in_spans(x, junk) for x in (a, b, (a + b) / 2)):
            continue
        if all(abs(a - o[0]) > 6.0 for o in out):
            out.append((round(a, 2), round(b, 2), m))
        if len(out) >= n:
            break
    order = {"play": 1, "success": 2, "surprise": 0, "punchline": 3, "reaction": 0}
    return sorted(out, key=lambda x: order.get(x[2]["kind"], 0))


def _topic(segs):
    import hooks
    tk = hooks.topic_keywords([s.get("text", "") for s in segs], 3)
    return tk[0] if tk else "오늘의 레슨"


def _hook_text(moms, segs, fmt):
    """훅 자막: 앞부분 질문을 짧게, 없으면 '주제어 + 이것만 알면!'."""
    h = next((m for m in moms if m["kind"] == "hook_line"), None)
    if h:
        t = re.sub(r"^(자|어|음|그|네|아)[,\s]+", "", h["text"].strip())
        t = re.sub(r"\s+", " ", t)
        if len(t.replace(" ", "")) <= (14 if fmt == "shorts" else 18):
            return t
        m = re.search(r"([^,.]*\?)", t)
        if m and len(m[1].replace(" ", "")) <= 18:
            return m[1].strip()
    return f"{_topic(segs)}, 이것만 알면 달라져요!"


def _short_text(t, limit):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    if len(t.replace(" ", "")) <= limit:
        return t
    cut, n = "", 0
    for w in t.split():
        if n + len(w) > limit:
            break
        cut, n = (cut + " " + w).strip(), n + len(w)
    return (cut or t[:limit]).rstrip(" ,.") + "…"


def _sfx_level(sig, name, kind):
    """효과음 레벨(dB): 원본 말 최대 크기보다 3~5dB 작게 (효과음 파일은 -3 dBFS 로 맞춰 둠)."""
    tgt = float(sig.get("peakDb") or -6.0) - (5.0 if kind in ("emphasis", "count", "section", "inner") else 3.0)
    if name in ("짠", "짠2", "짠3", "경쾌 짧은 음악", "맑은 짧은 음악", "라이저", "두구두구"):
        tgt -= 3.0
    return round(min(4.0, max(-24.0, tgt + 3.0)), 1)


def compile_seq(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log=print):
    """스타일·양 → 새 편집본 하나 (+ 필요한 효과음·배경음악·정지 화면 미디어)."""
    dur = float(info["duration"])
    words = _words(segs)
    rh = st["rhythm"]
    rec = editor.recommend(name, keep_pause=rh.get("keepPause") or None)
    junk = [(j["a"], j["b"], j["why"]) for j in rec.get("junk_list") or []]
    if fmt == "shorts":
        base = _shorts_window(rec, moms, dur, sig.get("demo") or [])
    else:
        base = keep_cuts(rec["tidy"], sig.get("demo")) or [{"in": 0.0, "out": dur}]
    every = float(rh.get("zoomEvery") or 0)
    zoom = min(1.6, max(1.0, float(rh.get("zoomScale") or 1.0)))
    if every <= 0:
        zoom = min(zoom, editor.SOFT_ZOOM)
    cuts = editor._rhythm([dict(c) for c in base], segs, {"splitShot": rh.get("splitShot") or 0, "curve3": rh.get("curve3"), "tempo": rh.get("tempo") or 0}, every)
    picked, mont = plan_events(sig, moms, st, intensity, fmt, seed, base, words)
    fc = face_center(sig) or [0.5, 0.38]
    B = _Build(name, info, fmt)
    cap_y = caption_y(st, fmt)
    looks = text_looks(st, fmt, cap_y)
    intro = st["intro"]
    snd = st["sound"]
    pal = SFX_PALETTE.get(snd.get("palette"), SFX_PALETTE["variety"])
    mild = intensity == "담백"
    sections = []   # 배경음악 구간 (타임라인 시작, 끝, 분위기)
    topic = _topic(segs)
    hook = _hook_text(moms, segs, fmt)

    # --- 인트로 ---
    if fmt == "long" and intro.get("type") == "teaser":
        wins = _teaser_windows(moms, int(intro.get("clips") or 3), dur, junk)
        prev = None
        made = []
        for a, b, m in wins:
            v, au = B.clip(a, b, nocaps=True, vol_db=-3.0)
            if prev is not None and intro.get("flash"):
                tr = B.flash(prev, v)
                B.sfx.append((v["start"] - 0.12, pal.get("teaser", "휙"), None, "teaser"))
                made.append(tr["id"]) if tr else None
            prev = v
            made += [v["id"], au["id"]]
        if wins:
            hk = B.title(hook, 0.0, min(B.pos, 3.2), dict(looks["emphasis"], y=0.2 if cap_y > 0.5 else 0.7, size=84, fill="#FFFFFF", effect="pop"))
            B.event("teaser", 0.0, hook, f"명장면 {len(wins)}개 미리 보기", refs={"items": made, "titles": [hk["id"]]}, ins={"start": 0.0, "len": round(B.pos, 3)})
    elif fmt == "long" and intro.get("type") == "hook_line":
        h = next((m for m in moms if m["kind"] == "hook_line"), None)
        if h and h["b"] - h["a"] <= 6.0:
            v, au = B.clip(max(0.0, h["a"] - 0.1), h["b"] + 0.2, nocaps=True)
            hk = B.title(_short_text(h["text"], 18), 0.0, B.pos, dict(looks["emphasis"], y=0.2 if cap_y > 0.5 else 0.7, size=80, fill="#FFFFFF", effect="pop"))
            B.event("teaser", 0.0, hk["text"], "첫 질문으로 시작", refs={"items": [v["id"], au["id"]], "titles": [hk["id"]]}, ins={"start": 0.0, "len": round(B.pos, 3)})
    shorts_hook = fmt == "shorts"  # 쇼츠: 티저 없이 맨 위에 훅 자막을 처음 2.5초 (본편을 다 만든 뒤 넣음)
    first_main = cuts[0]["in"] if cuts else 0.0
    if fmt == "long" and intro.get("titleCard"):
        ff = _freeze(name, first_main + 0.2)
        ff["id"] = _mid(ff["file"])
        B.media[ff["id"]] = ff
        s0 = B.pos
        prev = B.items[-2] if len(B.items) >= 2 and B.items[-2]["track"] == "V1" else None
        v = B.still(ff["id"], TITLE_CARD, (100.0, 105.0), {"exp": -0.8, "sat": 85})
        refs = {"items": [v["id"]], "titles": [], "shapes": []}
        if prev is not None:
            tr = B.flash(prev, v, "white" if intro.get("flash") else "dissolve", 0.3)
            refs["trans"] = [tr["id"]] if tr else []
        sh = B.shape(0.06, 0.6, 0.5, 0.25, GREEN, s0, TITLE_CARD, 0.92, 24, "제목 카드") if fmt == "long" else None  # 왼쪽 아래 (얼굴을 가리지 않게)
        if intro.get("letterbox"):
            refs["shapes"] += [B.shape(0, 0, 1, 0.1, "#000000", s0, TITLE_CARD, 1.0, 0, "검은 띠")["id"], B.shape(0, 0.9, 1, 0.1, "#000000", s0, TITLE_CARD, 1.0, 0, "검은 띠")["id"]]
        if sh:
            refs["shapes"].append(sh["id"])
        t1 = B.title("오늘의 주제", s0 + 0.1, TITLE_CARD - 0.1, dict(editor.TITLE_STYLE, weight="Bold", size=44, fill="#FFFFFF", strokeW=0, x=0.09, y=0.69,
                                                                align="left", effect="fade"))
        t2 = B.title(_short_text(topic, 10), s0 + 0.25, TITLE_CARD - 0.25, dict(editor.TITLE_STYLE, font="Black Han Sans", size=112, fill="#FFE14D", stroke="#111111",
                                                                               strokeW=8, x=0.09, y=0.82, align="left", effect="stamp"))
        refs["titles"] += [t1["id"], t2["id"]]
        B.sfx.append((s0 + 0.2, pal.get("title", "짠"), None, "title"))
        B.event("title", s0, topic, "제목 카드", refs=refs, ins={"start": round(s0, 3), "len": TITLE_CARD})
    intro_end = B.pos

    # --- 본편 조각 (확대·흔들기 표시 · 끼워 넣을 곳) ---
    pieces = [{"in": float(c["in"]), "out": float(c["out"]), "zoom": bool(c.get("zoom")), "speed": float(c.get("speed") or 1.0)} for c in cuts]
    inserts = {}   # 조각 번호 → 그 앞에 넣을 것 [(종류, 사건)]
    for c in picked:
        if c["kind"] == "punch":
            i = _split(pieces, c["t"])
            if i is None:
                continue
            _split(pieces, min(pieces[i]["out"], c["t"] + c["dur"]))
            pieces[i]["punch"] = c
        elif c["kind"] == "shake":
            i = _split(pieces, c["t"] - 0.03, 0.15)
            if i is None:
                continue
            _split(pieces, c["t"] + 0.4, 0.15)
            pieces[i]["shake"] = c
    lines = lines_of(segs)
    for c in picked:
        if c["kind"] == "replay":  # 시범 뒤 감독님 반응 말(나이스!)이 끝난 다음 쉬는 틈에 (말 중간에 끊지 않게)
            c["at"] = _after_reaction(lines, c["b"])
    for c in picked:
        if c["kind"] in ("replay", "freeze"):
            at = c["at"]
            i = _split(pieces, at, 0.2)
            if i is None:
                i = next((k for k, p in enumerate(pieces) if p["in"] >= at), None)
            if i is not None:
                inserts.setdefault(i, []).append(c)
    # 고친 조각 번호가 바뀌었으므로 다시 찾기: 끼워 넣을 곳은 원본 시각으로
    ins_at = sorted(((c["at"], c) for cs in inserts.values() for c in cs), key=lambda x: x[0])
    main_start = B.pos
    prev_v = None
    for p in pieces:
        while ins_at and ins_at[0][0] <= p["in"] + 0.21:
            _, c = ins_at.pop(0)
            _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y)
        fx = None
        if p.get("punch"):
            c = p["punch"]
            S = 1.18 if mild else 1.25 if intensity == "보통" else 1.3
            anc = {"v": fc, "k": []}
            if mild:
                fx = {"scale": {"v": round(S * 100, 1), "k": []}, "anchor": anc}
            else:
                sp = p["speed"]
                fx = {"scale": {"v": 100.0, "k": [{"t": round(p["in"], 4), "v": 100.0, "e": "ease"}, {"t": round(p["in"] + 0.25 * sp, 4), "v": round(S * 100, 1), "e": "lin"}]},
                      "anchor": anc}
        elif p.get("shake"):
            c = p["shake"]
            fx = {"scale": {"v": 106.0, "k": []}, "pos": {"v": [0.5, 0.5], "k": _shake_keys(p["in"], p["speed"], seed + int(p["in"] * 100))}}
        elif p["zoom"] and zoom > 1.001:
            fx = {"scale": {"v": round(zoom * 100, 1), "k": []}, "anchor": {"v": fc, "k": []}}
        v, au = B.clip(p["in"], p["out"], p["speed"], fx, main=True)
        if prev_v is None and B.items and len(B.items) > 2:
            before = [x for x in B.items[:-2] if x["track"] == "V1"]
            if before and abs(editor.i_end(before[-1]) - v["start"]) < 0.02 and intro.get("titleCard"):
                B.flash(before[-1], v, "dissolve", 0.4)
        prev_v = v
        if p.get("punch"):
            c = p["punch"]
            B.event("punch", v["start"], "", c.get("why") or "확대", src=p["in"], refs={"items": [v["id"]], "fx": "punch"})
        if p.get("shake"):
            B.event("shake", v["start"], "", "화면 흔들기", src=p["in"], refs={"items": [v["id"]], "fx": "shake"})
    for _, c in ins_at:
        _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y)
    main_end = B.pos

    # --- 글자·효과음 사건 (본편 시각으로 옮김) ---
    for c in picked:
        if c["kind"] not in TEXT_KINDS and not (c.get("sfx") and c["kind"] not in ("replay", "freeze")):
            continue
        tl = B.src_to_tl(c["t"])
        if tl is None:
            continue
        refs = {"titles": []}
        text = c.get("text") or ""
        if c["kind"] in TEXT_KINDS:
            look = dict(looks[c["kind"]])
            if c["kind"] == "fx":
                look["rot"] = random.Random(seed + int(c["t"] * 10)).choice((-8, -6, 6, 8))
                look["fill"] = {"fail": "#4DA3FF", "success": "#FFE14D", "surprise": "#FF4D4D"}.get(c.get("look"), "#FF9F1C")
            if c["kind"] == "emphasis":
                tl -= 0.12  # 낱말이 들리기 바로 앞에 (효과음이 말 첫소리를 가리지 않게)
            d = c["dur"]
            if c["kind"] == "count":
                ws = [s for s, e, w, j in words if c["t"] - 0.1 <= s <= c["t"] + 3.0 and re.fullmatch(r"(하나|둘|셋|넷)[,.!]*", w)]
                nums = {"하나": "1", "둘": "2", "셋": "3", "넷": "4"}
                for s, e, w, j in [x for x in words if x[0] in ws][:4]:
                    t2 = B.src_to_tl(s)
                    if t2 is None:
                        continue
                    tt = B.title(nums[re.sub(r"[,.!]", "", w)], t2, 0.6, look)
                    refs["titles"].append(tt["id"])
                    B.sfx.append((t2, c.get("sfx") or "틱", None, "count"))
                if refs["titles"]:
                    B.event("count", tl, "하나 둘 셋", c["why"], src=c["t"], refs=refs)
                continue
            tt = B.title(text, max(0.0, tl), d, look)
            refs["titles"].append(tt["id"])
        if c.get("sfx"):
            st_t = tl - 0.12 if c["kind"] == "emphasis" else tl
            st_t = _gap_time(B, words, c["t"], st_t)
            B.sfx.append((st_t, c["sfx"], None, c["kind"]))
            refs["sfx"] = [len(B.sfx) - 1]
        B.event(c["kind"] if c["kind"] in TEXT_KINDS else "sfx", tl, text or c.get("sfx") or "", c["why"], src=c["t"], refs=refs)

    if shorts_hook and B.pos > 3.0:
        hk = B.title(_short_text(hook, 14), 0.0, 2.5, dict(looks["emphasis"], y=0.22, size=96, fill="#FFFFFF", effect="pop"))
        B.sfx.append((0.0, pal.get("title", "짠"), None, "title"))
        B.event("hook", 0.0, hk["text"], "쇼츠 첫 2.5초 훅 자막", refs={"titles": [hk["id"]], "sfx": [len(B.sfx) - 1]})

    # --- 몽타주 (끝나기 전 · 시범 4개 넘을 때) ---
    if mont and fmt == "long":
        s0 = B.pos
        made, prev = [], [x for x in B.items if x["track"] == "V1"][-1] if B.items else None
        for k, m in enumerate(mont[:7]):
            a = max(0.0, m["t"] - 0.2)
            v, au = B.clip(a, min(dur, a + MONTAGE_CLIP), nocaps=True, vol_db=-6.0)
            tr = B.flash(prev, v, "white", 0.14) if prev is not None else None
            made += [v["id"], au["id"]] + ([tr["id"]] if tr else [])
            B.sfx.append((v["start"], pal.get("montage", "휙"), -6.0, "montage"))
            prev = v
        tt = B.title("오늘의 명장면", s0, B.pos - s0, dict(looks["emphasis"], y=0.2 if cap_y > 0.5 else 0.75, size=80, fill="#FFFFFF", effect="pop"))
        B.event("montage", s0, "오늘의 명장면", f"시범 {len(mont[:7])}개를 빠르게", refs={"items": made, "titles": [tt["id"]]},
                ins={"start": round(s0, 3), "len": round(B.pos - s0, 3)})
        sections.append((s0, B.pos, snd.get("moods", {}).get("outro", "신남")))

    # --- 엔드 화면 ---
    if fmt == "long" and B.pos >= 25.0:
        last = min(dur - 0.6, pieces[-1]["out"] - 0.4) if pieces else dur - 0.6
        ff = _freeze(name, max(0.0, last))
        ff["id"] = _mid(ff["file"])
        B.media[ff["id"]] = ff
        s0 = B.pos
        prev = [x for x in B.items if x["track"] == "V1"][-1] if B.items else None
        v = B.still(ff["id"], END_SEC, (100.0, 112.0), {"exp": -1.1, "sat": 70})
        tr = B.flash(prev, v, "dissolve", 0.6) if prev is not None else None
        sh = [B.shape(0.08, 0.36, 0.38, 0.38, "#FFFFFF", s0 + 0.4, END_SEC - 0.4, 0.16, 8, "다음 영상 자리"),
              B.shape(0.54, 0.36, 0.38, 0.38, "#FFFFFF", s0 + 0.4, END_SEC - 0.4, 0.16, 8, "다음 영상 자리"),
              B.shape(0.445, 0.79, 0.11, 0.11 * 16 / 9 * 9 / 16, "#E5484D", s0 + 0.8, END_SEC - 0.8, 1.0, 100, "구독 버튼")]
        t1 = B.title("다음 영상도 같이 봐요!", s0 + 0.3, END_SEC - 0.3, dict(editor.TITLE_STYLE, font="Black Han Sans", size=92, fill="#FFFFFF", stroke="#111111",
                                                                        strokeW=8, y=0.25, effect="pop"))
        t2 = B.title("구독", s0 + 0.8, END_SEC - 0.8, dict(editor.TITLE_STYLE, weight="Black", size=40, fill="#FFFFFF", strokeW=0, y=0.875, effect="pop"))
        B.sfx.append((s0 + 0.3, pal.get("end", "경쾌 짧은 음악"), None, "end"))
        B.event("end", s0, "다음 영상도 같이 봐요!", "엔드 화면 (유튜브 최종 화면 자리)", refs={"items": [v["id"]], "trans": [tr["id"]] if tr else [], "shapes": [x["id"] for x in sh],
                                                                         "titles": [t1["id"], t2["id"]]}, ins={"start": round(s0, 3), "len": END_SEC})
    total = B.pos

    # --- 배경음악 구간 ---
    moods = snd.get("moods") or {}
    if snd.get("bgm", True):
        if intro_end > 0.5:
            sections.append((0.0, intro_end, moods.get("intro", "신남")))
        sections += _body_sections(B, picked, moms, main_start, main_end, moods)
        if total - main_end > 0.5 and not any(s[0] >= main_end - 0.01 for s in sections):
            sections.append((main_end, total, moods.get("outro", "신남")))
        elif total - main_end > 0.5:
            sections.append((max(s[1] for s in sections), total, moods.get("outro", "신남")))
        sections = _merge_sections(sorted(sections), total)
    seq_items_audio = _audio_items(B, sig, sections, seed, snd, intensity)

    # --- 챕터 (3분 넘을 때) · 썸네일 장면 ---
    if fmt == "long" and total >= 180:
        _chapters(B, moms, topic)
    thumbs = _thumb_picks(moms, hook, B)

    tracks = editor.default_tracks()
    for tr in tracks:
        if tr["id"] == "A2":
            tr.update(role="dialog", name="효과음")
        if tr["id"] == "A3":
            tr["name"] = "배경음악"
    if any(it["track"] == "A4" for it in B.items):
        tracks.append({"id": "A4", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0.0, "role": "music", "name": "배경음악"})
    if any(it["track"] == "A5" for it in B.items):
        tracks.append({"id": "A5", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0.0, "role": "dialog", "name": "효과음"})
    capst = _caption_style(st, fmt, cap_y)
    layout = {"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}
    if fmt == "shorts":
        layout = {"mode": "blur", "bar": "#000000", "zoom": 1.35, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}
    seq = editor._new_seq(label, fmt, B.items, trans=B.trans, titles=B.titles, shapes=B.shapes, markers=B.markers, captionStyle=capst,
                          captionsOn=bool(st["captions"].get("on", True)), layout=layout,
                          master={"volume": 1.0, "normalize": True, "lufs": float(snd.get("lufs") or -14.0)},
                          duck={"on": True, "amount": float(snd.get("duck") or -14.0)})
    seq["tracks"] = tracks
    seq["auto"] = "msg"
    summ = summary_of(B.events, sections, total, seq_items_audio)
    seq["msg"] = {"v": 1, "style": {"label": st.get("label"), "kind": st.get("kind"), "sources": st.get("sources")}, "intensity": intensity, "seed": seed,
                  "events": B.events, "thumb": thumbs, "summary": summ, "notes": st.get("notes") or [], "hook": hook, "topic": topic,
                  "bgm": [{"a": round(a, 2), "b": round(b, 2), "mood": md} for a, b, md in sections]}
    return seq, list(B.media.values())


def _shorts_window(rec, moms, dur, sig_demo=()):
    """쇼츠: 추천 구간 중 재미 순간이 가장 많은 곳 (없으면 앞쪽 정리 구간 50초)."""
    best, bs = None, -1.0
    for r in rec.get("shorts") or []:
        sc = sum(m["score"] for m in moms if r["start"] <= m["t"] <= r["end"] and m["kind"] in ("play", "punchline", "success", "emphasis", "surprise"))
        if sc > bs:
            best, bs = r, sc
    if best:  # 그 구간 안의 시범 장면도 살림 (말 정리만 하면 시범이 빠짐)
        demo = [d for d in sig_demo if best["start"] <= d[0] and d[1] <= best["end"]]
        return keep_cuts(best["cuts"], demo)
    out, tot = [], 0.0
    for c in rec.get("tidy") or [{"in": 0.0, "out": min(dur, 50.0)}]:
        if tot >= 50:
            break
        b = min(float(c["out"]), float(c["in"]) + 50 - tot)
        out.append({"in": float(c["in"]), "out": b})
        tot += b - float(c["in"])
    return out


def _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y):
    """슬로모 다시 보기 · 정지 화면을 지금 자리에 끼워 넣기."""
    prev = [x for x in B.items if x["track"] == "V1"]
    prev = prev[-1] if prev else None
    s0 = B.pos
    refs = {"items": [], "titles": [], "shapes": [], "trans": []}
    if c["kind"] == "replay":
        a, b = max(0.0, c["a"] - 0.3), min(float(sig["duration"]), c["b"] + 0.3)
        if b - a > REPLAY_SRC:  # 다시 보기는 원본 3초까지 (공 차는 순간 조금 앞부터 · 느리게 6초)
            a = max(a, c["t"] - 1.2)
            b = min(b, a + REPLAY_SRC)
        fx = None
        if intensity == "듬뿍":  # 다른 각도 느낌: 살짝 당겨서
            fx = {"scale": {"v": 118.0, "k": []}, "anchor": {"v": [0.5, 0.55], "k": []}}
        v, au = B.clip(a, b, REPLAY_SPEED, fx, nocaps=True, mute=True)
        refs["items"] += [v["id"], au["id"]]
        tr = B.flash(prev, v, "white", 0.2) if prev is not None and st["sound"].get("palette") != "cinematic" else B.flash(prev, v, "dissolve", 0.3)
        if tr:
            refs["trans"].append(tr["id"])
        ln = B.pos - s0
        lt = dict(editor.TITLE_STYLE, weight="Black", size=44, fill="#FFFFFF", stroke="#000000", strokeW=10, bgOn=True, bg="#000000", bgOpacity=0.6,
                  x=0.95, y=0.12 if cap_y > 0.5 else 0.8, align="right", effect="fade")
        if B.fmt == "shorts":
            lt.update(size=50, y=0.2)
        refs["titles"].append(B.title("다시 보기 ▶", s0 + 0.15, ln - 0.15, lt)["id"])
        if st["fun"].get("replayLook") == "letterbox":
            refs["shapes"] += [B.shape(0, 0, 1, 0.1, "#000000", s0, ln, 1.0, 0, "검은 띠")["id"], B.shape(0, 0.9, 1, 0.1, "#000000", s0, ln, 1.0, 0, "검은 띠")["id"]]
        B.sfx.append((s0 - 0.1, pal.get("replay", "휙"), None, "replay"))
        k = pal.get("kick")
        if k:
            for o in [o for o in sig.get("onsets") or () if a <= o <= b][:2]:
                B.sfx.append((s0 + (o - a) / REPLAY_SPEED, k, None, "kick"))
        B.event("replay", s0, "다시 보기", f"{mmss(c['t'])} 시범을 느리게 한 번 더", src=c["a"], refs=refs, ins={"start": round(s0, 3), "len": round(ln, 3)})
    else:
        ff = _freeze(name, c["at"] + 0.05)
        ff["id"] = _mid(ff["file"])
        B.media[ff["id"]] = ff
        v = B.still(ff["id"], FREEZE_SEC, (100.0, 104.0), {"sat": 0, "exp": -0.3})
        refs["items"].append(v["id"])
        txt = "이때까지만 해도…" if c.get("turn") == "fail" else "잠깐! 여기 주목"
        lt = dict(editor.TITLE_STYLE, font="Do Hyeon", size=76 if B.fmt == "long" else 84, fill="#FFFFFF", stroke="#000000", strokeW=12, bgOn=True, bg="#000000",
                  bgOpacity=0.6, x=0.5, y=0.26 if cap_y > 0.5 else 0.7, align="center", effect="fade")
        refs["titles"].append(B.title(txt, s0 + 0.1, FREEZE_SEC - 0.1, lt)["id"])
        B.sfx.append((s0, pal.get("freeze", "찰칵"), None, "freeze"))
        if intensity == "듬뿍" and pal.get("fail"):
            B.sfx.append((s0 + 0.35, "긁기", -4.0, "freeze"))
        B.event("freeze", s0, txt, f"{mmss(c['at'])} 결정적 순간 직전 멈춤", src=c["at"], refs=refs, ins={"start": round(s0, 3), "len": FREEZE_SEC})


def _after_reaction(lines, b):
    """시범이 끝난 b 뒤에 바로 반응 말이 있으면 그 말이 끝난 뒤, 없으면 b+0.3 (늘 말과 말 사이)."""
    for k, ln in enumerate(lines):
        if b - 0.3 <= ln["start"] <= b + 2.0 or ln["start"] <= b < ln["end"]:  # 반응 말 (또는 시범 끝이 말 중간)
            end = ln["end"]
            for nx in lines[k + 1:]:  # 숨 안 쉬고 이어지는 말까지 ('나이스! 들어갔어요.')
                if nx["start"] - end > 0.35:
                    break
                end = nx["end"]
            return round(end + 0.15, 3)
    return round(b + 0.3, 3)


def _gap_time(B, words, src_t, tl):
    """효과음이 낱말 첫소리(0.15초)를 가리지 않게 — 그 안이면 낱말 바로 앞 틈으로."""
    for s, e, w, j in words:
        if s - 0.02 <= src_t <= s + 0.15:
            prev_end = max([e2 for s2, e2, w2, j2 in words if e2 <= s] or [s - 1.0])
            if s - prev_end >= 0.1:
                return tl - (src_t - s) - 0.08
            return tl
    return tl


def _body_sections(B, picked, moms, a0, a1, moods):
    """본편 배경음악 구간: 시범이 몰린 곳은 '시범' 분위기, 나머지는 '설명' 분위기 (15초 안 되는 구간은 합침)."""
    if a1 - a0 < 1.0:
        return []
    demo = []
    for m in moms:
        if m["kind"] != "play":
            continue
        t = B.src_to_tl(m["t"])
        if t is None:
            continue
        lo, hi = max(a0, t - 6.0), min(a1, t + 8.0)
        if demo and lo <= demo[-1][1] + 6.0:
            demo[-1][1] = hi
        else:
            demo.append([lo, hi])
    for e in B.events:  # 다시 보기·정지 화면은 시범 분위기 안으로
        if e["kind"] in ("replay", "freeze") and e.get("ins"):
            lo, hi = e["ins"]["start"], e["ins"]["start"] + e["ins"]["len"]
            demo.append([max(a0, lo - 3), min(a1, hi + 3)])
    demo.sort()
    merged = []
    for lo, hi in demo:
        if merged and lo <= merged[-1][1] + 6.0:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    merged = [d for d in merged if d[1] - d[0] >= 15.0]
    out, t = [], a0
    for lo, hi in merged:
        if lo - t >= 1.0:
            out.append((t, lo, moods.get("lesson", "잔잔")))
        out.append((lo, hi, moods.get("demo", "경쾌")))
        t = hi
    if a1 - t >= 1.0:
        out.append((t, a1, moods.get("lesson", "잔잔")))
    return out


MAX_SECTIONS = 5   # 배경음악은 한 영상에 3~5곡 (너무 자주 바뀌면 산만함)


def _merge_sections(secs, total):
    """같은 분위기·짧은 구간(8초 미만)은 앞 구간에 합치고, 많으면 가장 짧은 본편 구간부터 이웃과 합침 (첫 인트로 음악은 그대로)."""
    out = []
    for a, b, m in secs:
        if b - a <= 0.05:
            continue
        if out and (out[-1][2] == m or b - a < 8.0) and a <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], b), out[-1][2])
        else:
            out.append((a, b, m))
    while len(out) > MAX_SECTIONS:
        k = min(range(1, len(out) - 1), key=lambda i: out[i][1] - out[i][0])  # 첫(인트로)·끝(엔드) 음악은 남김
        j = k - 1 if k > 1 and out[k - 1][1] - out[k - 1][0] <= out[k + 1][1] - out[k + 1][0] else k + 1
        if j < k:
            out[j] = (out[j][0], out[k][1], out[j][2])
        else:
            out[j] = (out[k][0], out[j][1], out[j][2])
        del out[k]
        merged = []
        for x in out:
            if merged and merged[-1][2] == x[2]:
                merged[-1] = (merged[-1][0], x[1], x[2])
            else:
                merged.append(x)
        out = merged
    return [(round(a, 3), round(min(b, total), 3), m) for a, b, m in out]


def _audio_items(B, sig, sections, seed, snd, intensity):
    """효과음(A2·겹치면 A5) · 배경음악(A3/A4 번갈아 · 1초 겹쳐 바꿈) 클립."""
    assets = editor.ASSETS

    def media_for(fn):
        mid = _mid(fn)
        if mid not in B.media:
            e = editor.media_entry(fn, "assets", mid)
            B.media[mid] = e
        return mid, B.media[mid]

    def aitem(track, mid, start, a, b, lvl, fi=0.0, fo=0.0):
        it = {"id": editor._nid(), "track": track, "media": mid, "start": round(start, 4), "in": round(a, 4), "out": round(b, 4), "speed": 1.0, "rev": False,
              "link": None, "fx": {"level": {"v": round(lvl, 2), "k": []}} if abs(lvl) > 0.05 else {}, "gain": 0.0, "fadeIn": round(fi, 3),
              "fadeOut": round(fo, 3), "mute": False}
        B.items.append(it)
        return it

    n_sfx = 0
    ends = {"A2": -1.0, "A5": -1.0}
    sfx_ids = []
    for t, name, lvl, kind in sorted(B.sfx, key=lambda x: x[0]):
        t = max(0.0, t)
        fn = sfxlib.ensure_sfx(name, assets)
        mid, md = media_for(fn)
        d = float(md.get("dur") or 0.5)
        tr = "A2" if t >= ends["A2"] else "A5" if t >= ends["A5"] else None
        if tr is None:
            sfx_ids.append(None)
            continue
        it = aitem(tr, mid, t, 0.0, d, lvl if lvl is not None else _sfx_level(sig, name, kind))
        ends[tr] = t + d
        sfx_ids.append(it["id"])
        n_sfx += 1
    # 사건 기록의 효과음 번호 → 클립 id
    order = sorted(range(len(B.sfx)), key=lambda k: B.sfx[k][0])
    idmap = {k: sfx_ids[i] for i, k in enumerate(order)}
    for e in B.events:
        if "sfx" in e["refs"]:
            e["refs"]["items"] = e["refs"].get("items", []) + [idmap[k] for k in e["refs"].pop("sfx") if idmap.get(k)]
    # 배경음악
    music_db = float(sig.get("dialogDb") or -24.0) + float(snd.get("bgmDb") or -10.0) + 3.0
    lvl = round(min(6.0, max(-30.0, music_db - sfxlib.BGM_RMS_DB)), 1)
    tracks = ("A3", "A4")
    seeds = {}
    for k, (a, b, mood) in enumerate(sections):
        sd = seeds.setdefault(mood, _seed(sig.get("sig"), mood) % 1000)  # 같은 영상이면 후보들이 같은 음악을 씀 (파일 하나 · 비교하기 쉽게)
        fn, L = sfxlib.ensure_bgm(mood, sd, assets)
        mid, md = media_for(fn)
        if mood == "코믹":
            continue
        tr = tracks[k % 2]
        x0 = max(0.0, a - (0.5 if k else 0.0))
        x1 = b + (0.5 if k < len(sections) - 1 else 0.0)
        t, first = x0, True
        mine = []
        while t < x1 - 0.05:
            ln = min(L, x1 - t)
            it = aitem(tr, mid, t, 0.0, ln, lvl, 1.0 if first and k else (0.4 if first else 0.0), 0.0)
            mine.append(it)
            first = False
            t += ln
        if mine:
            mine[-1]["fadeOut"] = 1.0 if k < len(sections) - 1 else 2.5
        B.event("bgm", a, mood, f"배경음악 {mood} {mmss(a)}~{mmss(b)}", refs={"items": [x["id"] for x in mine]})
    if intensity == "듬뿍":  # 펀치라인 바로 앞에서 음악을 0.4초 멈춤 (웃음 포인트를 살리는 예능 편집)
        for e in [e for e in B.events if e["kind"] == "inner" and re.search(r"웃음|농담", e.get("why") or "")][:2]:
            _music_gap(B, e["t"] - 0.45, 0.45)
    return {"sfx": n_sfx, "bgm": len(sections), "musicDb": lvl}


def _music_gap(B, t, ln):
    """배경음악 클립을 t 에서 ln 초 비움 (앞은 짧게 줄이고 뒤는 서서히 다시)."""
    for it in [x for x in B.items if x["track"] in ("A3", "A4") and x["start"] + 0.3 < t < editor.i_end(x) - ln - 0.3]:
        cut = t - it["start"]
        tail = dict(it, id=editor._nid(), start=round(t + ln, 4), **{"in": round(it["in"] + cut + ln, 4)}, fadeIn=0.3)
        it["out"] = round(it["in"] + cut, 4)
        it["fadeOut"] = 0.12
        B.items.append(tail)
        for e in B.events:
            if e["kind"] == "bgm" and it["id"] in e["refs"].get("items", []):
                e["refs"]["items"].append(tail["id"])


def _chapters(B, moms, topic):
    """챕터용 마커: 미리 보기 · 장 나눔 말 · 시범 묶음 · 마무리 (이름 20자 안)."""
    import upload
    pts = []
    first = next((e for e in B.events if e["kind"] == "title"), None)
    if B.main:
        pts.append((0.0, "미리 보기" if B.main[0]["start"] > 1 else f"{topic} 시작"))
        pts.append((first["t"] if first else B.main[0]["start"], f"{topic} 레슨"))
    for m in moms:
        if m["kind"] not in ("section", "closing"):
            continue
        t = B.src_to_tl(m["a"])
        if t is None:
            continue
        nm = "마무리" if m["kind"] == "closing" else (_situ_text(m) if re.search(r"번째|마지막", m["text"]) else upload.first_sentence(m["text"], 20))
        pts.append((t, nm))
    for e in B.events:
        if e["kind"] in ("montage", "end"):
            pts.append((e["t"], "오늘의 명장면" if e["kind"] == "montage" else "다음 영상"))
    pts.sort()
    out = []
    for t, nm in pts:
        if not nm or (out and t - out[-1][0] < 12.0):
            continue
        out.append((t, nm))
    for t, nm in out:
        B.markers.append({"t": round(t, 3), "name": nm[:20], "msg": True})
    if out:
        B.event("chapters", 0.0, f"챕터 {len(out)}개", "유튜브 챕터용 마커", refs={"markers": [round(t, 3) for t, _ in out]})


def _thumb_picks(moms, hook, B):
    """썸네일 추천 장면 (원본 시각): 최고 시범 +0.2초 · 웃거나 놀란 얼굴 · 강조 말."""
    out = []
    for kind, why, dt in (("play", "최고 시범 장면", 0.2), ("reaction", "웃거나 놀란 얼굴", 0.0), ("success", "성공한 순간", 0.3),
                          ("emphasis", "강조하는 말", 0.2), ("punchline", "웃음 터진 순간", 0.4)):
        for m in sorted([m for m in moms if m["kind"] == kind], key=lambda m: -m["score"])[:2]:
            t = round(m["t"] + dt, 2)
            if all(abs(t - o["t_src"]) > 3 for o in out):
                out.append({"t_src": t, "why": why, "text": hook})
    return out[:6]


def _caption_style(st, fmt, cap_y):
    c = st["captions"]
    base = dict(editor.LONG_STYLE if fmt == "long" else editor.SHORTS_STYLE)
    base["y"] = cap_y
    if fmt == "long":  # 휴대폰으로 보는 사람이 많아 가편집(52)보다 조금 크게
        base.update(size=58, strokeW=7)
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(c.get("color") or "")):
        base["fill"] = c["color"].upper()
    if c.get("karaoke"):
        base.update(weight="Black", size=76 if fmt == "long" else 80, fill="#FFFFFF", stroke="#000000", strokeW=8, effect="karaoke",
                    highlight=c.get("emphColor") or "#FFD400")
    return base


def summary_of(events, sections, total, audio):
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    caps = sum(kinds.get(k, 0) for k in ("emphasis", "situ", "inner", "fx", "count"))
    s = {"captions": caps, "sfx": audio.get("sfx", 0), "punch": kinds.get("punch", 0) + kinds.get("shake", 0), "replay": kinds.get("replay", 0),
         "freeze": kinds.get("freeze", 0), "montage": kinds.get("montage", 0), "bgm": len(sections), "length": round(total, 2), "kinds": kinds}
    s["text"] = (f"예능 자막 {caps} · 효과음 {s['sfx']} · 확대 {s['punch']} · 다시 보기 {s['replay']}" + (f" · 정지 {s['freeze']}" if s["freeze"] else "")
                 + f" · 배경음악 {len(sections)}곡 · 길이 {mmss(total)}")
    return s


# ---------- 6. 후보 여러 개 ----------

def build_variants(name, specs, intensity="보통", kinds=("long",), log=print):
    """스타일 여러 개(최대 3) × 형식 → 새 편집본 후보 + 필요한 미디어. 같은 원본·스타일·양이면 늘 같은 결과."""
    if intensity not in INTENSITY:
        raise ValueError("MSG 양은 담백·보통·듬뿍 중에서 골라 주세요")
    specs = [s for s in (specs or []) if isinstance(s, dict) and s.get("name")][:3]
    if not specs:
        raise ValueError("스타일을 하나 이상 골라 주세요")
    info = editor.media_info(name)
    sig = signals(name, log)
    segs = editor._segments_of(name)
    moms = moments(sig, segs)
    seqs, media = [], {}
    letters = "ABC"
    for k, spec in enumerate(specs):
        _check()
        st = resolve(spec)
        for fmt in kinds:
            core.set_progress(label=LABEL, item=name, pct=70 + int(28 * k / len(specs)), detail=f"후보 만드는 중 ({k + 1}/{len(specs)})")
            label = f"MSG 후보 {letters[k]} · {st['label']} · {intensity}" + (" · 쇼츠" if fmt == "shorts" else "")
            seed = _seed(sig["sig"], st["label"], intensity, fmt)
            seq, md = compile_seq(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log)
            seqs.append(seq)
            for m in md:
                media[m["id"]] = m
            log(f"  {label} · {seq['msg']['summary']['text']}")
    return {"sequences": seqs, "media": list(media.values()), "moments": len(moms)}
