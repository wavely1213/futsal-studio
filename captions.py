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
FLASH = 0.5       # 이보다 짧게 스치는 자막은 한도를 조금 넘더라도 앞뒤와 합침
GAP_FILL = 0.3    # 자막 사이 빈틈이 이보다 짧으면 앞 자막을 다음 자막까지 늘림 (깜빡임 방지)

# 이렇게 끝나는 말 뒤는 끊어 읽기 좋은 곳 (조사·이음말) · 문장 끝
_NICE = re.compile(r"(?:은|는|이|가|을|를|에|도|만|로|고|서|면|데|며|게|와|과|요|다|죠|까|네|야)[,.?!…~]*$")  # '~지 말고'·'~의' 뒤는 안 끊음
_EOW = re.compile(r"(?:요|고|서|데|다|죠|면|며|니까|는데|지만|[어아해워여]도)[,.?!…~]*$")  # 말 덩어리가 끝나는 말끝 — 조사 뒤보다 더 끊기 좋음
_END = re.compile(r"[.?!…]$")
# 문장부호 없이 끝난 문장 끝 (받아쓰기에 마침표가 없을 때가 많음) — 그 뒤에서 끊고, 한 자막 안에 두 문장을 되도록 안 넣음
_FIN = re.compile(r"(?<!필)(?<!중)요[~]*$|(?:니다|습니까|[었았였했겠있없같]다|[는한된간온]다|[이였]죠|죠)[~]*$")
# 앞말에 기대는 말(의존 명사) — 이 앞에서는 되도록 안 끊음 ('할 수 / 있는', '등지게 되기 / 때문에', '하는 / 게')
_BOUND = re.compile(r"^(?:것|거|건|걸|게|수|때문|줄|데|뿐|듯|적|만큼|때)"
                    r"(?:이|은|는|을|를|가|에|에는|도|만|예요|에요|이에요|야|죠|지|다|입니다|이다|에서|라|라서|요)?[,.?!…~]*$")
# 뒤 낱말을 꾸미는 말 — 이 뒤에서는 되도록 안 끊음 ('첫 / 터치를', '말씀드렸던 / 게', '갈 / 방향으로')
_MOD = re.compile(r"^(?:첫|한|두|세|네|그|이|저|이런|그런|저런|어떤|무슨|모든|각|새|몇|딴|온갖|안|못|잘|더|좀|꼭|제일|가장)$"
                  r"|(?:던|[하되있없가오보주받치차쓰놓]는)$")
# 이어 주는 말 — 이 앞에서 끊기 좋음 ('늦어요 / 그래서')
_CONJ = re.compile(r"^(?:그래서|그러면|그럼|그런데|근데|그리고|그러니까|그니까|하지만|그래도|그러나|왜냐하면|어쨌든|아무튼|자|이제|그다음에?)[,]?$")
# 붙여 읽는 풀이말 ('해야 / 되고'·'등지게 / 되기'·'하고 / 있어요'·'받고 / 나서'·'오기 / 전에'·'해 / 주세요')
_AUX = ((re.compile(r"야$"), re.compile(r"^[되돼하할한]")), (re.compile(r"게$"), re.compile(r"^[되돼하해만]")),
        (re.compile(r"고$"), re.compile(r"^(?:있|싶|계|나서|나면|난|나니)")), (re.compile(r"기$"), re.compile(r"^(?:전|후|위해|위한|시작)")), (re.compile(r"지$"), re.compile(r"^(?:않|마|말|못)")),
        (re.compile(r"[어아여해워와봐줘]$"), re.compile(r"^(?:주|줘|보|봐|놓|버|있|가|오|와|드리|드릴|드려|야)")))
# 목적어 뒤 짧은 풀이말 — '집중을 / 해야' 처럼 갈라지지 않게
_PRED = re.compile(r"^(?:해|하|되|돼|들|보|받|주|줘|치|차|써|쓰|만들|가져|잡|넣|놓)")
# 뒤 낱말을 꾸미는 말: 자막 끝에 혼자 남으면 어색함 ('패스하고 그' / '자리에 서 있으면' · '항상 두' / '개쯤') → 뒤 낱말과 같은 자막에
_MODSET = {"그", "이", "저", "이런", "그런", "저런", "어떤", "무슨", "몇", "첫", "각", "모든", "매", "온", "새", "다른", "딴"}
_NUM = {"한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "스무"}
_COUNTER = re.compile(r"^(?:번|개|명|걸음|가지|골|바퀴|시간|분|초|살|발|판|세트|회|차|군데|마리|잔|장|줄|칸|박자)")
# 이음말·말끝 뒤(…인데 / …하고 / …해요)가 조사 뒤(공은 / 항상)보다 끊어 읽기 좋은 곳
_CLAUSE = re.compile(r"(?:데|고|서|면|며|요|다|죠|까|네|야|지만|니까|거나|든지)[,.?!…~]*$")  # ('정확하게' 같은 -게 는 뒤 움직씨를 꾸밈)
# 꾸밈을 자주 받는 이름씨: 그 앞의 움직씨 꾸밈꼴('받는 사람'·'하는 거'·'좋은 방법')은 따로 떼면 어색함
_HEAD_N = ("사람", "것", "거", "게", "때", "곳", "쪽", "방향", "순간", "동작", "선수", "상황", "자리", "방법", "경우", "이유", "부분", "거리",
           "느낌", "타이밍", "친구", "분들")
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
    """사전 파일 읽기 · 없거나 깨졌으면 기본 사전 (사용자가 다 지우고 저장한 빈 사전은 그대로).
    메모장으로 고친 파일(BOM·ANSI)도 읽음. 그래도 못 읽으면 그 내용을 dict.json.bad 로 남겨 둠
    (기본 사전이 보인 채로 저장하면 사용자가 만든 용어 목록이 사라지므로)."""
    import updater
    try:
        raw = path.read_bytes()
    except OSError:
        return default_dict()
    try:
        return normalize_dict(updater.loads_tolerant(raw))
    except (ValueError, TypeError, AttributeError):
        bad = path.with_name(path.name + ".bad")
        try:
            if not bad.exists() or bad.read_bytes() != raw:
                bad.write_bytes(raw)
        except OSError:
            pass
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

def _rieul(t):
    """ㄹ 받침으로 끝나는 낱말 (조사 을·를은 빼고) — 대개 뒤 낱말을 꾸밈 ('할'·'갈'·'강조할')."""
    t = re.sub(r"[,.?!…~\"')]+$", "", t)
    if not t or re.search(r"[을를]$", t):
        return False
    o = ord(t[-1]) - 0xAC00
    return 0 <= o < 11172 and o % 28 == 8


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


def _adnominal(w, nxt):
    """w 가 뒤 이름씨를 꾸미는 꼴('받는 사람'·'하는 거'·'좋은 방법'·'할 때')인지 — '-는·-은·-을'이나 받침 ㄴ·ㄹ로 끝나고
    뒤가 꾸밈을 자주 받는 이름씨일 때만 (판정: '그리고 마지막으로 받는 / 사람 발 쪽으로…' 처럼 말 덩어리 가운데서 끊김)."""
    h = re.sub(r"[^가-힣]", "", w or "")
    n = re.sub(r"[^가-힣]", "", nxt or "")
    if not h or not n or re.search(r"[,.?!…~]$", w) or not any(n == x or (n.startswith(x) and len(n) <= len(x) + 2) for x in _HEAD_N):
        return False
    jong = (ord(h[-1]) - 0xAC00) % 28 if "가" <= h[-1] <= "힣" else 0
    return h[-1] in "는은을" or jong in (4, 8)  # 받침 ㄴ(4)·ㄹ(8)


def _modifier(w, nxt):
    """w 가 바로 뒤 낱말 nxt 를 꾸미는 말인지 (지시·수 관형사 · 수는 뒤에 단위가 올 때만 — '네' 대답·'세' 같은 다른 뜻과 가름 ·
    움직씨·그림씨 꾸밈꼴은 _adnominal)."""
    if not w or re.search(r"[,.?!…~]$", w):
        return False
    return w in _MODSET or (w in _NUM and bool(_COUNTER.match(nxt or ""))) or _adnominal(w, nxt)


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
    mod = [_modifier(texts[k], texts[k + 1]) for k in range(n - 1)] + [False]
    join, cut = [0.0], [0.0] * n  # join: k 와 k+1 을 한 자막에 둘 때 · cut: k 뒤에서 나눌 때 (클수록 나쁨)
    for k in range(n - 1):  # 문장 끝(마침표 등)이 가장 강한 끊을 곳 · 받아쓰기 구간 끝은 문장 중간일 때도 있어 그보다 약하게
        t, nx, seg, g = texts[k], texts[k + 1], k in brk, gap[k]
        end, fin = bool(_END.search(t)), bool(_FIN.search(t))
        join.append(join[-1] + (10 if end else 6 if fin else 4 if seg else 3 if t.endswith(",") else 0)
                    + (1.5 if _CONJ.match(nx) else 0) + (0.0 if mod[k] else 12 * max(0.0, g - 0.25)))
        c = (0.0 if end or fin or g >= 0.4 else 0.3 if _CONJ.match(nx) else 0.4 if seg or t.endswith(",") or _EOW.search(t)
             else 1.2 if _NICE.search(t) or g >= 0.2 else 2.5)
        if not (end or g >= 0.4):  # 문장 끝이나 말을 멈춘 곳이 아니면: 기대는 말 앞·꾸미는 말 뒤·목적어와 짧은 풀이말 사이는 피함
            if _BOUND.match(nx):
                c += 3.0
            if _MOD.search(t):
                c += 3.0
            elif _rieul(t):  # '갈 / 방향으로' — ㄹ 받침으로 끝나는 꾸미는 말
                c += 1.5
            if any(a.search(t) and b.match(nx) for a, b in _AUX):
                c += 3.0
            if re.search(r"[을를]$", t):
                c += 3.0 if _PRED.match(nx) and _chars(nx) <= 4 else 0.6
        cut[k] = c + (1000 if k in inside else 0) + (12 if mod[k] else 0)  # '그'·'두 개' 같은 꾸미는 말 뒤는 끊지 않음

    best, prev = [0.0] + [float("inf")] * n, [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            chars, dur = cum[j] - cum[i], ws[j - 1]["e"] - ws[i]["s"]
            if j - i > 1 and (gap[i] >= GAP_MAX and not mod[i] or chars > mc or dur > md):
                break
            short = max(0.0, 1 - chars / (0.6 * mc))  # 너무 짧은 자막만 조금 피함 (어색한 곳에서 끊느니 짧은 게 나음)
            c = best[i] + 1.5 + join[j - 1] - join[i] + (cut[j - 1] if j < n else 0.0) + 2.5 * short * short
            if j - i == 1 and chars <= ORPHAN and n > 1:  # 낱말 하나짜리 짧은 자막 ('요.'·'그래서')
                c += 6
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
    spans.reverse()
    # 그래도 0.5초도 안 되게 스치는 자막(아주 긴 낱말 옆 자투리·빠른 말)은 앞이나 뒤와 합침 — 한도의 1.5배까지는 넘어도 됨
    # (한 줄이 화면보다 넓으면 editor.cap_fit 이 글자를 조금 줄임) · 문장 끝을 넘거나 오래 쉰 곳은 안 합침
    def shown(m):
        i, j = spans[m]
        return (ws[spans[m + 1][0]]["s"] if m + 1 < len(spans) else ws[j - 1]["e"]) - ws[i]["s"]

    def can(a):  # spans[a] 와 spans[a + 1] 을 합쳐도 되나 → 합친 글자 수 (안 되면 None)
        i, k = spans[a][0], spans[a + 1][1]
        c = cum[k] - cum[i]
        if c > 1.5 * mc or any(gap[x] >= GAP_MAX or _END.search(texts[x]) for x in range(i, spans[a][1])):
            return None
        return c

    m = 0
    while m < len(spans):
        if len(spans) < 2 or shown(m) >= FLASH:
            m += 1
            continue
        opts = [(c, a) for a in (m - 1, m) if 0 <= a < len(spans) - 1 for c in [can(a)] if c is not None]
        if not opts:
            m += 1
            continue
        a = min(opts)[1]
        spans[a:a + 2] = [(spans[a][0], spans[a + 1][1])]
        m = max(0, a - 1)
    out = []
    for i, j in spans:
        out.append({"start": ws[i]["s"], "end": ws[j - 1]["e"], "text": " ".join(texts[i:j]),
                    "words": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in ws[i:j]]})
    return out


def _limit(fmt):
    return LIMITS["shorts" if fmt in ("shorts", "short") else "long"]


# 예전(v2.1.1까지) 롱폼 자막은 이 글자보다 길 때만 두 줄로 나눴음 → 이보다 짧은데 줄바꿈이 있으면 사용자가 일부러 넣은 것
OLD_LINE = 12


def needs_split(text, fmt="long"):
    """한 줄 자막으로 다시 나눠야 하는지 — 글자 수가 한도보다 많거나, 예전 두 줄 자막(12글자 넘는데 줄바꿈).
    짧은 자막에 사용자가 직접 넣은 줄바꿈('안녕\n하세요')은 그대로 둠."""
    t = str(text or "")
    if len(t.split()) < 2:
        return False
    return _chars(t) > _limit(fmt)[0] or ("\n" in t.strip() and _chars(t) > OLD_LINE)


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


def weight(t):
    """단어 시각이 없을 때 낱말에 줄 시간 몫 — 한글 등은 글자마다 1, 영어·숫자는 0.5 (빨리 읽힘), 문장부호·띄어쓰기는 0 ·
    최소 1, 최대 12 (아주 긴 낱말·주소가 시간을 다 가져가지 않게). editor.html capWeight 와 같은 규칙."""
    k = 0.0
    for ch in str(t):
        if ch.isspace() or ch in ",.?!…~\"'()[]:;-_/·":
            continue
        k += 0.5 if ch.isascii() and ch.isalnum() else 1.0
    return min(12.0, max(1.0, k))


def even_words(toks, start, end, silences=()):
    """단어 시각이 없을 때: 낱말마다 글자 수대로 시간을 나눔 → [{w, s, e}].
    조용한 곳(silences)이 있으면 그 시간은 빼고 나눈 뒤, 조용한 곳에서 가장 가까운 낱말 사이를 조용한 곳 앞뒤에 맞춤
    (말을 멈춘 곳 = 낱말 사이 → 그 앞뒤 낱말은 그 안에서 다시 글자 수대로)."""
    n = len(toks)
    wts = [weight(t) for t in toks]
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
    start, end = float(start), float(end)
    if not toks:
        return []
    if end - start < MIN_DUR:  # 길이가 없거나 거꾸로 된 자막 → 나누지 않음 (나누면 끝이 시작보다 앞서는 자막이 생김)
        return [{"start": start, "end": max(start, end), "text": " ".join(toks)}]
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
        b["start"] = min(max(b["start"], a["start"] + 0.01), end)
        a["end"] = min(max(a["end"], a["start"] + 0.01), b["start"])
    _tidy(out, end)
    # 그래도 MIN_DUR 보다 짧게 스치는 자막은 붙어 있는 앞·뒤 자막에서 남는 시간을 빌려 옴 (전체 시작·끝은 그대로)
    for k, c in enumerate(out):
        need = MIN_DUR - (c["end"] - c["start"])
        if need <= 1e-9:
            continue
        if k + 1 < len(out) and abs(out[k + 1]["start"] - c["end"]) < 1e-6:
            q = out[k + 1]
            take = min(need, max(0.0, q["end"] - q["start"] - MIN_DUR))
            c["end"] = q["start"] = round(c["end"] + take, 2)
            need -= take
        if need > 1e-9 and k and abs(out[k - 1]["end"] - c["start"]) < 1e-6:
            p = out[k - 1]
            take = min(need, max(0.0, p["end"] - p["start"] - MIN_DUR))
            c["start"] = p["end"] = round(c["start"] - take, 2)
    for c in out:  # 언제나 원래 자막 안에서, 끝이 시작보다 앞서지 않게
        c["start"] = min(max(c["start"], start), end)
        c["end"] = min(max(c["end"], c["start"]), end)
    return out


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


# ---------- 문장 단위로 나누기 (E12 · D-101) ----------
# 받아쓰기가 앞 구간 글에 기대지 않으면(condition_on_previous_text=False) 문장부호가 줄고 한 구간이 30초 넘게 길어짐 →
# 가편집·쇼츠 경계·영상 밖 말 찾기는 단어 시각으로 문장마다 나눠서 봄 (구간을 문장 끝(마침표가 없어도 '~요·~니다·~죠')·1초 넘게 쉰 곳에서).
SENT_GAP = 1.0
LONG_SEG = 8.0  # 단어 시각이 없는 구간은 이보다 길 때만 글자 수로 어림해서 나눔
ZERO_WORD = 0.05  # 이보다 짧은 낱말은 받아쓰기가 시각을 못 맞춘 것 (문장 끝으로 안 봄)
GHOST = 0.4       # 같은 말이 바로 이어 나올 때 이보다 짧은(또는 다른 쪽의 GHOST_PART 이하) 쪽은 받아쓰기가 겹쳐 쓴 그림자
GHOST_PART = 0.4
_LIKE = re.compile(r"^좋아요[,]?$")
_SUB = re.compile(r"구독(?:이랑|하고|과|도|이나|랑)?[,]?$")


def ends_sentence(w):
    """낱말 하나가 문장 끝인지 ('있어요' · '됩니다' · '봤죠?' · '나이스!' — '필요'·'중요'·'~는데,' 는 아님)."""
    t = re.sub(r"[\"'”’)\]]+$", "", str(w or "").strip())
    return bool(_END.search(t)) or bool(_FIN.search(re.sub(r"[~]+$", "", t)))


def split_sentences(segs, gap=SENT_GAP, guess=True):
    """받아쓰기 구간 → 문장 구간 [{start, end, text(, words)}] (시간 순).
    단어 시각이 있고 한 구간에 문장이 둘 넘으면 문장 끝 낱말 뒤·gap 초 넘게 쉰 곳에서 나눔 (글은 그 문장의 낱말을 이은 것 ·
    첫 문장 시작·마지막 문장 끝은 원래 구간 그대로). 한 문장뿐인 구간은 그대로 · 단어 시각이 없는 구간(예전 받아쓰기)은
    guess 일 때만, LONG_SEG 초보다 길면 글자 수대로 시각을 어림해서 나눔 (나눈 조각에는 words 를 넣지 않음 · 글만 쓰는 키트용 —
    가편집은 guess=False: 어림한 시각으로 구간 안을 자르면 몇 초씩 어긋남).
    길이가 0 인 낱말(받아쓰기가 시각을 못 맞춘 '빗나갔어요.' 61.23–61.23)은 문장 끝으로 보지 않음.
    나눈 둘째 문장부터는 cont=True (원래 한 구간이었음)."""
    out = []
    for s in segs or ():
        if not str(s.get("text") or "").strip():
            continue
        ws = [w for w in s.get("words") or () if isinstance(w, dict) and str(w.get("w") or "").strip()]
        est = guess and not ws and float(s["end"]) - float(s["start"]) > LONG_SEG  # 단어 시각이 없는 긴 구간: 글자 수대로 어림해서 나눔
        if est:
            ws = even_words(str(s["text"]).split(), float(s["start"]), float(s["end"]))
        parts, cur = [], []
        for i, w in enumerate(ws):
            cur.append(w)
            nxt = ws[i + 1] if i + 1 < len(ws) else None
            liked = i and _LIKE.match(str(w["w"]).strip()) and _SUB.search(str(ws[i - 1]["w"]))  # '구독이랑 좋아요 눌러 주세요'의 '좋아요'는 이름씨
            ghost = not est and float(w["e"]) - float(w["s"]) < ZERO_WORD  # 길이 0 낱말 (지어낸 말일 수 있음): 뒤 말과 이어 봄
            if nxt is None or (ends_sentence(w["w"]) and not liked and not ghost) or float(nxt["s"]) - float(w["e"]) >= gap:
                parts.append(cur)
                cur = []
        for k in range(len(parts) - 2, -1, -1):  # 쉼으로만 떨어진 한두 글자('세' … '번째 포인트')는 뒤 문장에 붙임 (단어 시각이 앞으로 쏠린 것)
            p = parts[k]
            if len(re.sub(r"\s+", "", "".join(str(w["w"]) for w in p))) <= 2 and not ends_sentence(p[-1]["w"]):
                parts[k:k + 2] = [p + parts[k + 1]]
        if len(parts) < 2:
            out.append(dict(s))
            continue
        for k, p in enumerate(parts):
            a = float(s["start"]) if k == 0 else float(p[0]["s"])
            b = float(s["end"]) if k == len(parts) - 1 else float(p[-1]["e"])
            piece = {"start": round(a, 2), "end": round(max(a, b), 2), "text": " ".join(str(w["w"]).strip() for w in p)}
            if k or s.get("cont"):  # (첫 문장은 구간 자체가 앞 구간에 이어진 것일 때만 — 가편집이 나눈 문장 줄 · E2)
                piece["cont"] = True  # 같은 받아쓰기 구간의 뒷 문장 (가편집은 예전처럼 사이를 자르지 않음 — 말 없는 시범이 그 사이에 있을 수 있음)
            if not est:
                piece["words"] = p
            out.append(piece)
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


def _plain(t):
    return re.sub(r"[\s.,!?~…]+", "", str(t or ""))


def merge_ghosts(sents, gap=1.0):
    """문장들(시간 순) → 받아쓰기가 창 경계에서 겹쳐 쓴 '그림자' 문장을 진짜 문장에 합친 것.
    같은 글의 문장이 gap 초 안에 이어 나오고 한쪽이 GHOST 초보다 짧거나 다른 쪽의 GHOST_PART 이하 길이면
    (예: '감사합니다' 157.43–158.23 뒤 '감사합니다' 158.23–158.49) 긴 쪽 글·낱말에 두 시각을 합친 구간 하나로 —
    말더듬 규칙이 진짜 말을 지우고 그림자를 남겨 낱말이 잘리지 않게 (E12 · I-102)."""
    out = []
    for s in sents or ():
        p = out[-1] if out else None
        if p is not None and _plain(p.get("text")) and _plain(p.get("text")) == _plain(s.get("text")) \
                and float(s["start"]) - float(p["end"]) <= gap:
            dp, ds = float(p["end"]) - float(p["start"]), float(s["end"]) - float(s["start"])
            if min(dp, ds) < GHOST or min(dp, ds) <= GHOST_PART * max(dp, ds):
                keep = dict(p if dp >= ds else s)
                keep["start"], keep["end"] = round(min(float(p["start"]), float(s["start"])), 2), round(max(float(p["end"]), float(s["end"])), 2)
                if p.get("cont"):
                    keep["cont"] = True
                else:
                    keep.pop("cont", None)
                out[-1] = keep
                continue
        out.append(s)
    return out


# ---------- 받아쓰기 헛것 거르기 (E2 · BR-090) ----------
# 받아쓰기(Whisper)가 소리 없이 지어낸 낱말: 한 점에 몰린 낱말(길이 0) · 같은 낱말 줄줄이 · 한국어 영상 속 영어 찌꺼기 · 조용한 곳 위의 말.
# 자막·가편집·쇼츠·제목 후보에 쓰기 전에 뺌 (받아쓰기 파일은 그대로 · 빠진 낱말은 '확인 필요'로 추천 탭에 보여 줌).
HALLU_CPS = 0.05     # 글자당 이보다 짧게 받아쓴 낱말은 시각이 무너진 것 (한 점에 몰림)
HALLU_P = 0.15       # 무너진 낱말이 이보다 확신이 낮으면 지어낸 말
HALLU_RUN = 3        # 무너진 낱말이 이만큼 넘게 한 점에 이어지면 — 가까운 곳(HALLU_NEAR 초)에 같은 글이 있으면 겹쳐 쓴 그림자
HALLU_NEAR = 20.0
HALLU_COVER = 0.8    # 그림자 낱말 글자의 이만큼이 가까운 말에 (앞뒤 낱말과 이어진 채로) 그대로 있으면 그림자
HALLU_REPEAT = 4     # 같은 낱말이 이보다 많이 줄줄이 나오면 (구령 '하나, 둘' 은 다른 낱말이라 상관없음) 앞 2개만 남김
HALLU_KEEP = 2
HALLU_SIL = 0.95     # 낱말 길이의 이만큼이 조용한 곳(silencedetect) 안이고 (가장자리에 걸친 낱말은 받아쓰기 시각이 조금 어긋난 진짜 말일 수 있음)
HALLU_SIL_P = 0.6    # 확신이 이보다 낮으면 소리 없이 지어낸 말
HALLU_LATIN_P = 0.5  # 한글 없는 영어 낱말(3글자 넘게)이 이보다 확신이 낮으면 찌꺼기 ('functioning.' · 'paced...Pacific...')
HALLU_WHY = "받아쓰기 헛것(확인 필요)"
HALLU_MAYBE = "작은 말소리?(확인 필요 · 남겨 둠)"


def _wplain(w):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(w or ""))


def _collapsed(w):
    n = len(_wplain(w.get("w")))
    return n > 0 and float(w["e"]) - float(w["s"]) < HALLU_CPS * n


def _covered_by(toks, near):
    """낱말들 toks 중 앞이나 뒤 낱말과 이어 붙인 채로 near(가까운 말을 이은 글) 안에 그대로 있는 낱말의 글자 비율 0~1."""
    tot = sum(len(t) for t in toks)
    if not tot:
        return 0.0
    got = 0
    for k, t in enumerate(toks):
        pair = [toks[k - 1] + t] if k else []
        pair += [t + toks[k + 1]] if k + 1 < len(toks) else []
        if any(x in near for x in pair) or (len(toks) == 1 and t in near):
            got += len(t)
    return got / tot


def hallucinations(words, silences=(), soft=None):
    """낱말 [{w, s, e, p}] (시간 순, 영상 전체) → 지어낸 낱말 번호 집합 (규칙은 위 HALLU_* · 말은 한국어 영상 기준).
    조용한 곳 위 확신 낮은 낱말은 멀리서 작게 찍힌 진짜 말일 수도 있어서 — 낱말 시각이 무너졌을 때만 지어낸 말로 보고,
    아니면 soft(집합을 주면)에 넣어 '확인 필요'로 표시만 함 (자막·소리는 그대로 · E2 검토)."""
    ws = [w for w in words or ()]
    bad = set()
    sil = [(float(x["start"]), float(x["end"])) for x in silences or () if isinstance(x, dict) and "start" in x and "end" in x]
    for i, w in enumerate(ws):
        try:
            s, e, p = float(w["s"]), float(w["e"]), float(w.get("p", 1.0))
        except (KeyError, TypeError, ValueError):
            continue
        t = _wplain(w.get("w"))
        if not t:
            continue
        if _collapsed(w) and p < HALLU_P:
            bad.add(i)
        elif re.fullmatch(r"[A-Za-z0-9]*[A-Za-z]{3,}[A-Za-z0-9]*", t) and p < HALLU_LATIN_P:
            bad.add(i)
        elif sil and p < HALLU_SIL_P:
            d = max(e - s, 1e-3)
            inside = sum(max(0.0, min(e, b) - max(s, a)) for a, b in sil)
            if (e - s < 1e-3 and any(a <= s <= b for a, b in sil)) or inside >= HALLU_SIL * d:
                if _collapsed(w):
                    bad.add(i)
                elif soft is not None:
                    soft.add(i)
    i = 0
    while i < len(ws):  # 한 점에 몰린 낱말 줄 — 가까운 곳에 같은 글이 있으면 그림자
        j = i
        while j < len(ws) and _collapsed(ws[j]) and abs(float(ws[j]["s"]) - float(ws[i]["s"])) <= 0.1:
            j += 1
        if j - i >= HALLU_RUN:
            run = [_wplain(w.get("w")) for w in ws[i:j]]
            t0 = float(ws[i]["s"])
            near = "".join(_wplain(w.get("w")) for k, w in enumerate(ws)
                           if not (i <= k < j) and abs(float(w["s"]) - t0) <= HALLU_NEAR and not _collapsed(w))
            if _covered_by(run, near) >= HALLU_COVER:
                bad.update(range(i, j))
            i = j
        else:
            i += 1
    i = 0
    while i < len(ws):  # 같은 낱말 줄줄이 ('다섯, 다섯, 다섯, …')
        j = i
        while j + 1 < len(ws) and _wplain(ws[j + 1].get("w")) and _wplain(ws[j + 1].get("w")) == _wplain(ws[i].get("w")):
            j += 1
        if j - i + 1 > HALLU_REPEAT:
            bad.update(range(i + HALLU_KEEP, j + 1))
        i = j + 1
    return bad


def drop_hallucinations(segs, silences=(), soft=None, strict=False):
    """받아쓰기 구간 → (지어낸 낱말을 뺀 구간들, 뺀 것 [(시작, 끝, 글)]).
    soft(목록을 주면): 조용한 곳 위 확신 낮은 낱말(빼지 않고 둠)을 [(시작, 끝, 글)] 로 더함 — 추천 탭 '확인 필요' 표시용.
    strict: 그런 낱말도 뺌 (글로만 쓰는 곳 — 올리기 키트 제목·설명 · 소리를 자르지 않으니 놓쳐도 덜 아픔).
    낱말 시각이 있는 구간만 봄 · 남은 낱말로 글을 다시 쓰고 구간 시작·끝을 남은 낱말에 맞춤 · 다 빠지면 그 구간도 뺌.
    낱말 시각이 없는 예전 받아쓰기 구간은 영어 찌꺼기 한 줄('paced...Pacific...')만 뺌."""
    segs = [s for s in segs or () if isinstance(s, dict)]
    flat = [(k, j, w) for k, s in enumerate(segs) for j, w in enumerate(s.get("words") or ()) if isinstance(w, dict) and str(w.get("w") or "").strip()]
    flat.sort(key=lambda x: (float(x[2].get("s", 0) or 0), x[0], x[1]))
    maybe = set()
    bad = hallucinations([w for _, _, w in flat], silences, maybe)
    if strict:
        bad, maybe = bad | maybe, set()
    gone = {(flat[i][0], flat[i][1]) for i in bad}
    if soft is not None:
        soft.extend((round(float(flat[i][2]["s"]), 2), round(float(flat[i][2]["e"]), 2), str(flat[i][2]["w"]).strip()) for i in sorted(maybe))
    out, flags = [], []
    for k, s in enumerate(segs):
        ws = [w for w in s.get("words") or () if isinstance(w, dict) and str(w.get("w") or "").strip()]
        if not ws:
            t = _wplain(s.get("text"))
            if t and len(re.findall(r"[A-Za-z]", t)) / len(t) > 0.7 and len(t) - len(re.findall(r"[A-Za-z]", t)) <= 3 and len(t) >= 4:
                flags.append((float(s["start"]), float(s["end"]), str(s.get("text") or "").strip()))
                continue
            out.append(s)
            continue
        keep = [w for j, w in enumerate(s.get("words") or ()) if isinstance(w, dict) and str(w.get("w") or "").strip() and (k, j) not in gone]
        drop = [w for j, w in enumerate(s.get("words") or ()) if (k, j) in gone]
        if drop:
            a, b = float(drop[0]["s"]), float(drop[-1]["e"])
            flags.append((round(a, 2), round(max(a, b), 2), " ".join(str(w["w"]).strip() for w in drop)))
        if not keep:
            continue
        if not drop:
            out.append(s)
            continue
        n = dict(s, words=keep, text=" ".join(str(w["w"]).strip() for w in keep))
        n["start"] = round(max(float(s["start"]), min(float(w["s"]) for w in keep)) if float(s["start"]) < float(keep[0]["s"]) - 0.5 else float(s["start"]), 2)
        last = max(float(w["e"]) for w in keep)
        n["end"] = round(min(float(s["end"]), last + 0.3) if float(s["end"]) > last + 0.5 else float(s["end"]), 2)
        out.append(n)
    return out, flags
