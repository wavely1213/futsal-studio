"""자막 나누기·용어 사전 — 받아쓰기의 단어 시각(words)으로 읽기 좋은 한 줄 자막을 만들고, 잘못 받아쓴 풋살 용어를 고침.
chunk(words, fmt) → [{start, end, text, words}] (단어 사이에서만 나눔 · 언제나 한 줄) · from_segments(받아쓰기 구간) → 편집실 자막
split_text(글, 시작, 끝) → 이미 있는 자막(예전 받아쓰기·고친 글)을 한 줄씩 (단어 시각이 없으면 글자 수대로 시간을 나눔)
apply_dict(text, fixmap) · fix_words(words, fixmap) · 사전 파일 읽기/쓰기(load_dict·save_dict)
표준 라이브러리만 씀 (core 가 받아쓰는 중에, editor 가 자막을 만들 때 부름)."""
import json
import os
import re
import time
from functools import lru_cache

# 자막 한 줄: 최대 글자 수(띄어쓰기 빼고) · 최대 길이(초) — 쇼츠 13글자는 기본 크기(72)에서 1080 화면 90% 안에 한 줄로 들어감
LIMITS = {"shorts": (13, 2.2), "long": (17, 3.0)}
MIN_DUR = 0.6     # 이보다 짧게 스치는 자막은 앞뒤와 합침 (못 합치면 화면에 이만큼은 남김)
ORPHAN = 3        # 이 글자 이하 낱말 하나만 있는 자막은 되도록 안 만듦 (앞뒤에 붙임)
GAP_MAX = 1.2     # 단어 사이가 이만큼 비면 한 자막으로 묶지 않음
GAP_FILL = 0.3    # 자막 사이 빈틈이 이보다 짧으면 앞 자막을 다음 자막까지 늘림 (깜빡임 방지)

# 이렇게 끝나는 말 뒤는 끊어 읽기 좋은 곳 (조사·이음말) · 문장 끝
_NICE = re.compile(r"(?:은|는|이|가|을|를|에|도|만|로|고|서|면|데|며|게|와|과|요|다|죠|까|네|야)[,.?!…~]*$")  # '~지 말고'·'~의' 뒤는 안 끊음
_EOW = re.compile(r"(?:요|고|서|데|다|죠|면|며|니까|는데|지만)[,.?!…~]*$")  # 말 덩어리가 끝나는 말끝 — 조사 뒤보다 더 끊기 좋음
_END = re.compile(r"[.?!…]$")
# 사전 고치기: 낱말 뒤에 붙어도 되는 조사 (최대 두 개 · '피버를'은 고치고 '피버트'는 안 고침)
_JOSA = "(?:이에요|예요|입니다|이랑|에서|에게|한테|으로|부터|까지|처럼|보다|하고|은|는|이|가|을|를|의|에|로|와|과|도|만|랑|씩)"
# 요·야·죠는 낱말 바로 뒤에 혼자 붙을 때만 ('피버요'는 고치고, '가요'·'가야' 같은 말끝이 붙은 '피버가요'는 그대로)
_TAIL = "(?:이요|요|이야|야|이죠|죠)"

DEFAULT_TERMS = ["풋살사관학교", "최경진 감독", "피벗", "픽소", "아라", "고레이로", "토킥", "인사이드 패스", "아웃사이드 패스",
                 "볼 컨트롤", "트래핑", "퍼스트 터치", "2대1 패스", "스위칭", "프레스", "킥인", "코너킥", "골키퍼", "수비 라인",
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
    """매 구간마다 알려 줄 용어 (hotwords) — 길면 받아쓸 자리가 줄어서 짧게."""
    return _fit(terms, budget, count, " ")


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


def chunk(words, fmt="long", breaks=(), terms=()):
    """단어 [{w, s, e}] → 자막 덩어리 [{start, end, text, words}] — 단어 사이에서만 나누고, 언제나 한 줄.
    쇼츠: 13글자(띄어쓰기 빼고)·2.2초까지 / 롱폼: 17글자·3초까지. 한 단어가 혼자 그보다 길면 그 단어만 따로.
    0.6초보다 짧은 자투리와 짧은 낱말 하나만 남는 자막은 되도록 앞뒤와 합침. 말끝(~요·~고·~서·~는데·~다)·문장부호 뒤에서 끊음.
    breaks: 이 번호(words 기준)의 단어 뒤에서 되도록 나눔 (받아쓰기 구간 끝) · terms: 안 나눌 여러 단어 용어 (용어가 혼자 한도보다 길 때만 나눔).
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
    join, cut = [0.0], [0.0] * n  # join: k 와 k+1 을 한 자막에 둘 때 · cut: k 뒤에서 나눌 때 (클수록 나쁨)
    for k in range(n - 1):  # 문장 끝(마침표 등)이 가장 강한 끊을 곳 · 받아쓰기 구간 끝은 문장 중간일 때도 있어 그보다 약하게
        end, seg, g = bool(_END.search(texts[k])), k in brk, gap[k]
        join.append(join[-1] + (10 if end else 4 if seg else 3 if texts[k].endswith(",") else 0) + 12 * max(0.0, g - 0.25))
        cut[k] = (0.0 if end or g >= 0.4 else 0.4 if seg or texts[k].endswith(",") or _EOW.search(texts[k])
                  else 1.2 if _NICE.search(texts[k]) or g >= 0.2 else 2.5) + (1000 if k in inside else 0)

    best, prev = [0.0] + [float("inf")] * n, [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            chars, dur = cum[j] - cum[i], ws[j - 1]["e"] - ws[i]["s"]
            if j - i > 1 and (gap[i] >= GAP_MAX or chars > mc or dur > md):
                break
            short = max(0.0, 1 - chars / (0.7 * mc))
            c = best[i] + 2.0 + join[j - 1] - join[i] + (cut[j - 1] if j < n else 0.0) + 4 * short * short
            if j - i == 1 and chars <= ORPHAN and n > 1:  # 낱말 하나짜리 짧은 자막 ('요.'·'그래서')
                c += 6
            if dur < MIN_DUR:
                c += 40 + 100 * (MIN_DUR - dur)
            if c < best[j]:
                best[j], prev[j] = c, i
    spans, j = [], n
    while j > 0:
        spans.append((prev[j], j))
        j = prev[j]
    out = []
    for i, j in reversed(spans):
        out.append({"start": ws[i]["s"], "end": ws[j - 1]["e"], "text": " ".join(texts[i:j]),
                    "words": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in ws[i:j]]})
    return out


def _limit(fmt):
    return LIMITS["shorts" if fmt in ("shorts", "short") else "long"]


def needs_split(text, fmt="long"):
    """한 줄 자막으로 다시 나눠야 하는지 — 줄바꿈이 있거나 글자 수가 한도보다 많음."""
    t = str(text or "")
    return "\n" in t.strip() or (len(t.split()) > 1 and _chars(t) > _limit(fmt)[0])


def _speech(start, end, silences):
    """[start, end] 에서 조용한 곳(analysis.json silences)을 뺀 말소리 구간들 — 말소리가 30%도 안 남으면 통째로."""
    ivs = [(start, end)]
    for x in silences or ():
        try:
            a, b = max(start, float(x["start"])), min(end, float(x["end"]))
        except (TypeError, ValueError, KeyError):
            continue
        if b - a <= 0.05:
            continue
        ivs = [q for p, r in ivs for q in ((p, min(r, a)), (max(p, b), r)) if q[1] - q[0] > 1e-6]
    if sum(b - a for a, b in ivs) < 0.3 * (end - start) or not ivs:
        return [(start, end)]
    return ivs


def _at(ivs, p, late):
    """말소리 구간들을 이어 붙인 시간 p → 실제 시각. late: 구간 끝에 딱 걸리면 다음 구간 시작으로 (낱말 시작)."""
    for a, b in ivs:
        d = b - a
        if p < d - 1e-9 or (not late and p <= d + 1e-9):
            return a + max(0.0, p)
        p -= d
    return ivs[-1][1]


def even_words(toks, start, end, silences=()):
    """단어 시각이 없을 때: 낱말마다 글자 수대로 시간을 나눔 → [{w, s, e}].
    조용한 곳(silences)이 있으면 그 시간은 빼고 나눈 뒤, 조용한 곳에서 가장 가까운 낱말 사이를 조용한 곳 앞뒤에 맞춤
    (말을 멈춘 곳 = 낱말 사이 → 그 앞뒤 낱말은 그 안에서 다시 글자 수대로)."""
    n = len(toks)
    wts = [max(1, _chars(t)) for t in toks]
    ivs = _speech(start, end, silences)
    total, tot = sum(b - a for a, b in ivs), sum(wts) or 1
    cum = [0]
    for x in wts:
        cum.append(cum[-1] + x)
    est = [_at(ivs, total * c / tot, True) for c in cum]  # 낱말 k 앞 경계의 어림 시각
    anchors, last = [(0, start, start)], 0
    for (_, a), (b, _) in zip(ivs, ivs[1:]):  # 말소리 구간 사이 = 쓰는 조용한 곳 [a, b]
        if n < 2:
            break
        k = min(range(1, n), key=lambda k: abs(est[k] - (a + b) / 2))
        if k > last:
            anchors.append((k, a, b))
            last = k
    anchors.append((n, end, end))
    out = []
    for (k0, _, t0), (k1, t1, _) in zip(anchors, anchors[1:]):
        sub = cum[k1] - cum[k0] or 1
        for k in range(k0, k1):
            out.append({"w": toks[k], "s": round(t0 + (t1 - t0) * (cum[k] - cum[k0]) / sub, 2),
                        "e": round(t0 + (t1 - t0) * (cum[k + 1] - cum[k0]) / sub, 2)})
    return out


def _tidy(out, limit=float("inf")):
    """짧은 빈틈은 메우고, 너무 짧게 스치는 자막은 다음 자막 전까지 조금 더 보여 줌 (limit 을 넘지 않게)."""
    for k, c in enumerate(out):
        nxt = out[k + 1]["start"] if k + 1 < len(out) else limit
        if 0 < nxt - c["end"] < GAP_FILL:
            c["end"] = nxt
        if c["end"] - c["start"] < MIN_DUR:
            c["end"] = round(min(c["start"] + MIN_DUR, max(c["end"], nxt)), 2)
    return out


def split_text(text, start, end, fmt="long", terms=(), words=None, silences=()):
    """이미 있는 자막 하나(글 · 시작 · 끝)를 한 줄 자막들로 [{start, end, text(, words)}] — 사용자가 고친 글 그대로 나눔.
    words: 그 글의 낱말마다 실제 말한 시각 [{s, e}] (낱말 수가 같을 때만 씀 → 나눈 자막에도 words) ·
    없으면 띄어쓰기에서 나누고 시간은 글자 수대로 (silences 가 있으면 조용한 곳에서 끊기 좋게).
    첫 자막은 원래 시작, 마지막 자막은 원래 끝 그대로 · 자막 사이는 빈틈 없이 이어짐 (원래 쉬던 곳은 그대로 빔)."""
    toks = str(text or "").split()
    start, end = float(start), max(float(start), float(end))
    if not toks:
        return []
    real = bool(words) and len(words) == len(toks)
    if real:
        try:
            ws = [{"w": t, "s": float(w["s"]), "e": float(w["e"])} for t, w in zip(toks, words)]
        except (TypeError, ValueError, KeyError):
            real = False
    if not real:
        ws = even_words(toks, start, end, silences)
    parts = chunk(ws, fmt, terms=terms)
    if not parts:
        return []
    out = [{"start": q["start"], "end": q["end"], "text": q["text"], **({"words": q["words"]} if real else {})} for q in parts]
    out[0]["start"], out[-1]["end"] = start, end
    for a, b in zip(out, out[1:]):  # 원래 한 자막이었으니 앞 자막 시작이 뒤 자막보다 늦지 않게
        b["start"] = max(b["start"], a["start"] + 0.01)
        a["end"] = min(max(a["end"], a["start"] + 0.01), b["start"])
    return _tidy(out, end)


def from_segments(segs, fmt="long", terms=(), silences=()):
    """받아쓰기 구간 → 편집실 자막 [{start, end, text(, words)}] — 모두 한 줄.
    단어 시각이 있는 구간은 chunk 로 읽기 좋게 나누고, 없는 구간(예전 받아쓰기)은 split_text 로 글자 수대로 시간을 나눠 한 줄씩."""
    segs = [s for s in segs or () if str(s.get("text") or "").strip()]
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
            out.extend(split_text(s["text"], s["start"], s["end"], fmt, terms, silences=silences))
    flush()
    out.sort(key=lambda c: c["start"])
    return _tidy(out)
