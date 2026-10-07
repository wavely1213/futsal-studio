"""자막 나누기·용어 사전 — 받아쓰기의 단어 시각(words)으로 읽기 좋은 자막 덩어리를 만들고, 잘못 받아쓴 풋살 용어를 고침.
chunk(words, fmt) → [{start, end, text, words}] (단어 사이에서만 나눔) · from_segments(받아쓰기 구간) → 편집실 자막
apply_dict(text, fixmap) · fix_words(words, fixmap) · 사전 파일 읽기/쓰기(load_dict·save_dict)
표준 라이브러리만 씀 (core 가 받아쓰는 중에, editor 가 자막을 만들 때 부름)."""
import json
import os
import re
import time
from functools import lru_cache

LIMITS = {"shorts": (12, 2.0), "long": (22, 3.5)}  # 자막 한 덩어리: 최대 글자 수(띄어쓰기 빼고) · 최대 길이(초)
MIN_DUR = 0.6     # 이보다 짧게 스치는 자막은 앞뒤와 합침 (못 합치면 화면에 이만큼은 남김)
LINE = 12         # 롱폼 자막이 이 글자보다 길면 가장 고르게 나뉘는 단어 사이에서 두 줄로
GAP_MAX = 1.2     # 단어 사이가 이만큼 비면 한 자막으로 묶지 않음
GAP_FILL = 0.3    # 자막 사이 빈틈이 이보다 짧으면 앞 자막을 다음 자막까지 늘림 (깜빡임 방지)

# 이렇게 끝나는 말 뒤는 끊어 읽기 좋은 곳 (조사·이음말) · 문장 끝
_NICE = re.compile(r"(?:은|는|이|가|을|를|에|도|만|로|고|서|면|데|며|게|와|과|요|다|죠|까|네|야)[,.?!…~]*$")  # '~지 말고'·'~의' 뒤는 안 끊음
_END = re.compile(r"[.?!…]$")
# 뒤 낱말을 꾸미는 말: 자막 끝에 혼자 남으면 어색함 ('패스하고 그' / '자리에 서 있으면' · '항상 두' / '개쯤') → 뒤 낱말과 같은 자막에
_MOD = {"그", "이", "저", "이런", "그런", "저런", "어떤", "무슨", "몇", "첫", "각", "모든", "매", "온", "새", "다른", "딴"}
_NUM = {"한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "스무"}
_COUNTER = re.compile(r"^(?:번|개|명|걸음|가지|골|바퀴|시간|분|초|살|발|판|세트|회|차|군데|마리|잔|장|줄|칸|박자)")
# 사전 고치기: 낱말 뒤에 붙어도 되는 조사 (최대 두 개 · '피버를'은 고치고 '피버트'는 안 고침)
_JOSA = "(?:이에요|예요|입니다|이랑|에서|에게|한테|으로|부터|까지|처럼|보다|하고|은|는|이|가|을|를|의|에|로|와|과|도|만|랑|씩)"
# 요·야·죠는 낱말 바로 뒤에 혼자 붙을 때만 ('피버요'는 고치고, '가요'·'가야' 같은 말끝이 붙은 '피버가요'는 그대로)
_TAIL = "(?:이요|요|이야|야|이죠|죠)"

DEFAULT_TERMS = ["풋살사관학교", "최경진 감독", "피벗", "픽소", "아라", "고레이로", "토킥", "인사이드 패스", "아웃사이드 패스",
                 "볼 컨트롤", "트래핑", "퍼스트 터치", "디딤발", "골대", "2대1 패스", "스위칭", "프레스", "킥인", "코너킥", "골키퍼", "수비 라인",
                 "파라렐라", "디아고날", "오버래핑", "빌드업", "파워플레이", "세트피스", "로테이션", "발바닥 터치", "드리블",
                 "페인팅", "터닝", "슈팅", "리턴 패스"]
DEFAULT_FIX = {"피버": "피벗", "픽쏘": "픽소", "고레이루": "고레이로", "풋살 사관학교": "풋살사관학교", "퍼스트터치": "퍼스트 터치"}
TERM_MAX, FIX_MAX, ITEMS_MAX = 30, 40, 200  # 용어·고칠 말 글자 수, 개수 한도


def _chars(t):
    return len(re.sub(r"\s+", "", t))


def _clean(t, limit):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if 0 < len(t) <= limit else ""


# ---------- 용어 사전 (작업 폴더 dict.json) ----------

def default_dict():
    return {"terms": list(DEFAULT_TERMS), "fix": dict(DEFAULT_FIX)}


def normalize_dict(d):
    """화면·파일에서 온 사전을 정리 (빈 말·너무 긴 말·중복·같은 말로 고치기는 뺌). 사전 모양이 아니면 ValueError."""
    if not isinstance(d, dict) or not isinstance(d.get("terms", []), list) or not isinstance(d.get("fix", {}), dict):
        raise ValueError("용어 사전 모양이 아니에요")
    terms = []
    for t in d.get("terms") or []:
        t = _clean(t, TERM_MAX) if isinstance(t, str) else ""
        if t and t not in terms and len(terms) < ITEMS_MAX:
            terms.append(t)
    fix = {}
    for a, b in (d.get("fix") or {}).items():
        a, b = _clean(a, FIX_MAX), _clean(b, FIX_MAX) if isinstance(b, str) else ""
        if a and b and a != b and len(fix) < ITEMS_MAX:
            fix[a] = b
    return {"terms": terms, "fix": fix}


def load_dict(path):
    """사전 파일 읽기 · 없거나 깨졌으면 기본 사전 (사용자가 다 지우고 저장한 빈 사전은 그대로)."""
    try:
        return normalize_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return default_dict()


def save_dict(path, d):
    """정리한 사전을 임시 파일에 다 쓴 뒤 바꿔 끼움 (Windows: 백신 등이 잠깐 잡고 있으면 잠깐 뒤 다시)."""
    d = normalize_dict(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(d, v=1), ensure_ascii=False, indent=1), encoding="utf-8")
    for i in range(20):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)
    return d


def _fit(items, budget, count, sep=", "):
    """앞에서부터 budget(토큰) 안에 들어가는 만큼 (모자라면 그 뒤는 뺌)."""
    out = []
    for t in items:
        if count(sep.join(out + [t])) > budget:
            break
        out.append(t)
    return sep.join(out)


def prompt(terms, budget=180, count=len):
    """받아쓰기 첫머리 힌트 (initial_prompt): 풋살 강의라는 것 + 용어들."""
    body = _fit(terms, budget - count("풋살 강의예요. ."), count)
    return f"풋살 강의예요. {body}." if body else ""


def hotwords(terms, budget=60, count=len):
    """매 구간마다 알려 줄 용어 (hotwords) — 길면 받아쓸 자리가 줄어서 짧게.
    쉼표·마침표를 붙임: 받아쓰기는 힌트 글의 모양을 따라 써서, 띄어쓰기만으로 이으면 문장 부호 없는 받아쓰기가 나옴."""
    body = _fit(terms, budget - count("."), count)
    return body + "." if body else ""


def echo(words, terms, run=6):
    """말소리가 불분명한 곳에서 힌트(용어 목록)를 그대로 따라 쓴 구간인지 ('풋살사관학교 최경진 감독 피벗 픽소 …').
    용어 낱말이 사전 순서 그대로 run개 넘게 이어지고, 그 단어들 확신(p)이 낮고(평균 0.5 미만), 구간의 2/3 넘게 차지할 때만.
    코치가 포지션을 늘어놓는 말('포지션은 피벗 픽소 아라 고레이로 네 가지예요')은 확신이 낮아도 그대로 (구간을 통째로 버리므로 엄하게)."""
    hint = [x for t in terms or () for x in t.split()]
    toks = [re.sub(r"[,.?!…~]+$", "", str(w.get("w") or "")) for w in words or ()]
    at = {}
    for h, x in enumerate(hint):
        at.setdefault(x, []).append(h)
    for i, t in enumerate(toks):
        for h in at.get(t, ()):
            n = 0
            while i + n < len(toks) and h + n < len(hint) and toks[i + n] == hint[h + n]:
                n += 1
            ps = [float(w.get("p", 1.0)) for w in words[i:i + n]]
            if n >= run and 3 * n >= 2 * len(toks) and sum(ps) / n < 0.5:
                return True
    return False


# ---------- 잘못 받아쓴 말 고치기 ----------

# 조사 짝 (받침 없을 때, 받침 있을 때) — 고친 말의 끝소리에 맞춤 ('피버를' → '피벗을')
_PAIRS = sorted(((c, v, k) for v, k in (("를", "을"), ("가", "이"), ("는", "은"), ("와", "과"), ("랑", "이랑"),
                                       ("예요", "이에요"), ("로", "으로"), ("요", "이요"), ("야", "이야"), ("죠", "이죠"))
                    for c in (v, k)), key=lambda x: -len(x[0]))


def _josa(word, j):
    c = ord(word[-1]) - 0xAC00 if word else -1
    if not j or not 0 <= c < 11172:  # 한글로 끝나지 않으면 그대로
        return j
    fin = c % 28
    for cur, v, k in _PAIRS:
        if j.startswith(cur):
            return (v if fin == 0 or (v == "로" and fin == 8) else k) + j[len(cur):]
    return j


@lru_cache(maxsize=32)
def _fix_rx(keys):
    alts = "|".join(r"\s+".join(map(re.escape, k.split())) for k in sorted(keys, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?P<k>{alts})(?P<j>{_JOSA}{{0,2}}|{_TAIL})(?!\w)")


def apply_dict(text, fixmap):
    """fixmap {틀린 말: 바른 말} 을 낱말 경계에서만 고침 — 뒤에 조사가 붙은 것('피버를')은 고치고 조사도 맞춤('피벗을'),
    다른 말 안에 든 것('피버트'·'풋살피버')은 그대로. 한 글자 말('아')은 낱말 그대로일 때만 ('아이'는 그대로)."""
    if not text or not fixmap:
        return text

    def sub(m):
        key = " ".join(m["k"].split())
        right = fixmap.get(key)
        if right is None or (m["j"] and _chars(key) < 2):
            return m.group(0)
        return right + _josa(right, m["j"])
    return _fix_rx(tuple(sorted(fixmap))).sub(sub, text)


def _spread(w, parts):
    """한 단어를 여러 단어로 나눌 때 그 단어 시간을 글자 수대로 나눔."""
    tot = sum(max(1, _chars(p)) for p in parts)
    out, t = [], w["s"]
    for p in parts:
        e = t + (w["e"] - w["s"]) * max(1, _chars(p)) / tot
        out.append(dict(w, w=p, s=round(t, 2), e=round(e, 2)))
        t = e
    out[-1]["e"] = w["e"]
    return out


def fix_words(words, fixmap):
    """단어 목록에 사전 고치기 → (새 단어 목록, 고친 곳 수). 시각은 그대로 두고, 여러 단어로 된 틀린 말('풋살 사관학교')은
    한 단어로 합치며, 고쳐서 띄어쓰기가 생긴 단어('퍼스트터치' → '퍼스트 터치')는 나눔 → 단어마다 띄어쓰기 없음."""
    ws = [dict(w) for w in words]
    if not fixmap:
        return ws, 0
    n = 0
    for w in ws:
        t = apply_dict(w["w"], fixmap)
        if t != w["w"]:
            w["w"], n = t, n + 1
    for key in sorted((k for k in fixmap if " " in k), key=lambda k: -len(k.split())):
        size, i, head = len(key.split()), 0, key.split()[0]
        while i + size <= len(ws):
            if head not in ws[i]["w"]:  # 빠르게 거르기 (첫 낱말이 없으면 볼 것 없음)
                i += 1
                continue
            win = " ".join(x["w"] for x in ws[i:i + size])
            t = apply_dict(win, {key: fixmap[key]})
            if t == win:
                i += 1
                continue
            n += 1
            grp, parts = ws[i:i + size], t.split()
            if len(parts) == size:  # 단어 수가 같으면 단어마다 시각 그대로
                for x, p in zip(grp, parts):
                    x["w"] = p
                i += size
                continue
            merged = dict(grp[0], w=t, s=grp[0]["s"], e=grp[-1]["e"])
            if "p" in merged:
                merged["p"] = min(x.get("p", 1.0) for x in grp)
            ws[i:i + size] = [merged]
            i += 1
    out = []
    for w in ws:
        parts = w["w"].split()
        out += [w] if len(parts) == 1 else _spread(w, parts)
    return out, n


# ---------- 자막 나누기 ----------

def _term_joints(texts, terms):
    """여러 단어로 된 용어('퍼스트 터치') 안쪽 이음매 번호 — 그 사이에서는 되도록 안 나눔."""
    norm = [re.sub(r"[,.?!…~\"'()]+", "", t) for t in texts]
    inside = set()
    for term in terms or ():
        parts = term.split()
        if len(parts) < 2:
            continue
        for i in range(len(norm) - len(parts) + 1):
            if norm[i:i + len(parts) - 1] == parts[:-1] and norm[i + len(parts) - 1].startswith(parts[-1]):
                inside.update(range(i, i + len(parts) - 1))
    return inside


def _modifier(w, nxt):
    """w 가 바로 뒤 낱말 nxt 를 꾸미는 말인지 (지시·수 관형사 · 수는 뒤에 단위가 올 때만 — '네' 대답·'세' 같은 다른 뜻과 가름)."""
    if not w or re.search(r"[,.?!…~]$", w):
        return False
    return w in _MOD or (w in _NUM and bool(_COUNTER.match(nxt or "")))


def _lines(texts, inside):
    """롱폼 두 줄: 가장 고르게 나뉘는 단어 사이 (끊어 읽기 좋은 곳 · 아래 줄이 같거나 길게 · 용어 안쪽은 피함)."""
    if len(texts) < 2 or sum(_chars(t) for t in texts) <= LINE:
        return " ".join(texts)
    best, at = None, None
    for m in range(1, len(texts)):
        a, b = sum(_chars(t) for t in texts[:m]), sum(_chars(t) for t in texts[m:])
        sc = abs(a - b) + (0 if _NICE.search(texts[m - 1]) or texts[m - 1][-1] in ",.?!…" else 2.5) \
            + (8 if m - 1 in inside or _modifier(texts[m - 1], texts[m]) else 0) + (0.1 if a > b else 0)
        if best is None or sc < best:
            best, at = sc, m
    return " ".join(texts[:at]) + "\n" + " ".join(texts[at:])


def chunk(words, fmt="long", breaks=(), terms=()):
    """단어 [{w, s, e}] → 자막 덩어리 [{start, end, text, words}] — 단어 사이에서만 나눔.
    쇼츠: 12글자(띄어쓰기 빼고)·2초까지 한 줄 / 롱폼: 22글자·3.5초까지, 12글자가 넘으면 두 줄('\\n').
    한 단어가 혼자 그보다 길면 그 단어만 따로. 0.6초보다 짧은 자투리는 되도록 앞뒤와 합침 (짧은 한 문장은 뒤가 비어 있으면 혼자 둠).
    뒤 낱말을 꾸미는 말('그'·'두' + 단위)은 자막·줄 끝에 남기지 않음.
    breaks: 이 번호(words 기준)의 단어 뒤에서 되도록 나눔 (받아쓰기 구간 끝) · terms: 안 나눌 여러 단어 용어.
    시작·끝은 첫 단어 시작·마지막 단어 끝 그대로."""
    ws, brk = [], set()
    for k, w in enumerate(words or ()):
        t = re.sub(r"\s+", " ", str(w.get("w") or "")).strip()
        if not t:
            continue
        s = float(w.get("s") or 0.0)
        ws.append({"w": t, "s": s, "e": max(s, float(w["e"] if w.get("e") is not None else s))})
        if k in breaks:
            brk.add(len(ws) - 1)
    n = len(ws)
    if not n:
        return []
    shorts = fmt in ("shorts", "short")
    mc, md = LIMITS["shorts" if shorts else "long"]
    texts = [w["w"] for w in ws]
    inside = _term_joints(texts, terms)
    cum = [0]
    for t in texts:
        cum.append(cum[-1] + _chars(t))
    gap = [ws[k + 1]["s"] - ws[k]["e"] for k in range(n - 1)]
    mod = [_modifier(texts[k], texts[k + 1]) for k in range(n - 1)] + [False]
    join, cut = [0.0], [0.0] * n  # join: k 와 k+1 을 한 자막에 둘 때 · cut: k 뒤에서 나눌 때 (클수록 나쁨)
    for k in range(n - 1):  # 문장 끝(마침표 등)이 가장 강한 끊을 곳 · 받아쓰기 구간 끝은 문장 중간일 때도 있어 그보다 약하게
        end, seg, g = bool(_END.search(texts[k])), k in brk, gap[k]
        join.append(join[-1] + (10 if end else 4 if seg else 3 if texts[k].endswith(",") else 0) + (0.0 if mod[k] else 12 * max(0.0, g - 0.25)))
        cut[k] = (0.0 if end or g >= 0.4 else 0.5 if seg else 1.0 if _NICE.search(texts[k]) or g >= 0.2 else 2.5) \
            + (8 if k in inside else 0) + (12 if mod[k] else 0)

    best, prev = [0.0] + [float("inf")] * n, [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            chars, dur = cum[j] - cum[i], ws[j - 1]["e"] - ws[i]["s"]
            if j - i > 1 and (gap[i] >= GAP_MAX and not mod[i] or chars > mc or dur > md):
                break
            short = max(0.0, 1 - chars / (0.7 * mc))
            c = best[i] + 3.0 + join[j - 1] - join[i] + (cut[j - 1] if j < n else 0.0) + 4 * short * short
            if dur < MIN_DUR:
                # 짧은 한 문장('좋습니다.')은 뒤가 비어 있으면 MIN_DUR 까지 늘려 보여 줄 수 있어(from_segments) 혼자 둬도 됨 —
                # 다음 문장 가운데를 잘라 앞 문장에 붙이는 것('좋습니다. 이렇게 패스와' / '동시에 …')보다 나음
                room = gap[j - 1] if j < n else MIN_DUR
                whole = _END.search(texts[j - 1]) and (i == 0 or _END.search(texts[i - 1]) or gap[i - 1] >= 0.4)
                c += 8 + 30 * (MIN_DUR - dur) if whole and dur + room >= MIN_DUR else 40 + 100 * (MIN_DUR - dur)
            if c < best[j]:
                best[j], prev[j] = c, i
    spans, j = [], n
    while j > 0:
        spans.append((prev[j], j))
        j = prev[j]
    out = []
    for i, j in reversed(spans):
        part = texts[i:j]
        text = " ".join(part) if shorts else _lines(part, {k - i for k in inside if i <= k < j - 1})
        out.append({"start": ws[i]["s"], "end": ws[j - 1]["e"], "text": text,
                    "words": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in ws[i:j]]})
    return out


def from_segments(segs, fmt="long", terms=()):
    """받아쓰기 구간 → 편집실 자막 [{start, end, text(, words)}].
    단어 시각이 있는 구간은 chunk 로 읽기 좋게 나누고, 없는 구간(예전 받아쓰기)은 예전처럼 구간 그대로.
    단어 시각이 하나도 없으면 예전과 똑같이."""
    segs = [s for s in segs or () if str(s.get("text") or "").strip()]
    if not any(s.get("words") for s in segs):
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segs]
    out, run, brk = [], [], set()

    def flush():
        if run:
            out.extend(chunk(run, fmt, brk, terms))
            run.clear()
            brk.clear()

    for s in segs:
        ws = [w for w in s.get("words") or () if isinstance(w, dict) and str(w.get("w") or "").strip()]
        if ws:
            run.extend(ws)
            brk.add(len(run) - 1)
        else:
            flush()
            out.append({"start": s["start"], "end": s["end"], "text": s["text"].strip()})
    flush()
    out.sort(key=lambda c: c["start"])
    for k, c in enumerate(out):  # 짧은 빈틈은 메우고, 너무 짧게 스치는 자막은 다음 자막 전까지 조금 더 보여 줌
        nxt = out[k + 1]["start"] if k + 1 < len(out) else float("inf")
        if 0 < nxt - c["end"] < GAP_FILL:
            c["end"] = nxt
        if c["end"] - c["start"] < MIN_DUR:
            c["end"] = round(min(c["start"] + MIN_DUR, max(c["end"], nxt)), 2)
    return out
