"""올리기 키트의 제목 후보 — 우리 채널에서 반응이 좋았던 제목 패턴 + 영상 주제어 + 편집실 쇼츠 훅(editor._hook).

AI 없이 규칙으로:
- '소재 찾기'에서 우리 채널을 불러오면 목록을 작업 폴더의 channel_cache.json 에 남겨 둠
- 조회수 높은 제목에서 주제어 자리만 비운 틀(예: '{주제}를 잘하는 3가지 방법')과 자주 쓰는 말투(꿀팁·이유·실수…)를 찾음
- 저장된 목록이 없으면 기본 틀을 씀
"""
import json
import os
import re
import time
from collections import Counter

import core

# 풋살 레슨에서 주제가 되는 말 (긴 것부터 찾음 · 2글자 이하는 앞뒤가 다른 낱말에 붙어 있으면 안 셈: '턴' ≠ '패턴')
TERMS = [
    "퍼스트 터치", "볼 컨트롤", "볼 키핑", "볼 터치", "오프 더 볼", "세트피스", "로테이션", "포지셔닝", "빌드업", "스텝오버",
    "인사이드", "아웃사이드", "골키퍼", "코너킥", "프리킥", "트래핑", "스트레칭", "워밍업", "개인기", "발바닥", "드리블",
    "월패스", "슈팅", "패스", "토킥", "칩슛", "킥인", "돌파", "페인트", "수비", "압박", "전환", "역습", "침투", "마무리",
    "크로스", "헤딩", "스크린", "피보", "아라", "픽소", "체력", "스피드", "밸런스", "시야", "턴", "슛", "킥", "1대1", "2대1",
]
_JOSA_TAIL = r"(?:이|가|을|를|은|는|도|만|의|에|에서|으로|로|와|과|이랑|랑|이나|나|할|하|해|했)?"
STOP = {
    "그리고", "그래서", "그러면", "그런데", "근데", "이렇게", "저렇게", "그렇게", "이제", "지금", "여기", "거기", "저기", "우리",
    "여러분", "이거", "그거", "저거", "이것", "그것", "진짜", "정말", "너무", "조금", "많이", "그냥", "이런", "그런", "어떤",
    "때문", "경우", "생각", "사람", "오늘", "다음", "이번", "정도", "부분", "하나", "두번째", "첫번째", "세번째", "안녕하세요",
    "감사합니다", "있어요", "없어요", "합니다", "됩니다", "있습니다", "없습니다", "하세요", "그래요", "맞아요", "좋아요", "보세요",
    "같아요", "거예요", "건데", "할게요", "해볼게요", "있는", "없는", "하는", "되는", "이게", "그게", "제가", "저는", "내가",
    "나는", "우리가", "영상", "구독", "알림", "설정", "선수", "감독", "풋살", "축구",
}
_VERBISH = re.compile(r"(습니다|니다|어요|아요|해요|세요|는데|면서|해서|했어|하면|하는|하게|지만|거든|네요|게요|래요|죠|고요|어서|아서|다가|려고|려면|겠다|잖아)$")
_STRIP_JOSA = ("에서", "으로", "이랑", "까지", "부터", "처럼", "보다", "에게", "한테", "하고", "이", "가", "을", "를", "은", "는",
               "도", "만", "의", "에", "와", "과", "로")

# 기본 제목 틀: (말투 이름, 우리 채널 제목에서 이 말투를 찾는 식, 긴 영상 틀, 쇼츠 틀)
# {주제} 주제어 · {주제:이} 받침에 맞춰 이/가 (을/를 · 은/는 · 와/과 · 으로/로 같음) · {주제1}~{주제3} 여러 주제
FAMILIES = [
    ("정리", r"정리|총정리|한\s?번에|모음", "{주제1}부터 {주제3}까지 한 번에 정리", None),
    ("이것만", r"이것만|이거\s?하나|하나만|이것\s?하나", "{주제}, 이것만 알면 달라집니다", "{주제}, 이것만 바꾸세요"),
    ("이유", r"이유|왜", "{주제:이} 안 되는 진짜 이유", "{주제:이} 안 된다면?"),
    ("프로", r"프로|선수|국가대표|레전드", "프로는 {주제:을} 이렇게 합니다", "프로처럼 {주제} 하는 법"),
    ("방법", r"방법|하는\s?법|잘하는", "{주제} 제대로 하는 방법 (최경진 감독)", "{주제} 제대로 하는 법"),
    ("꿀팁", r"꿀팁|팁|비법|비결", "{주제} 실력이 바로 느는 꿀팁", "{주제} 1분 꿀팁"),
    ("실수", r"실수|하지\s?마|절대|금지", "{주제}에서 가장 많이 하는 실수", "{주제} 이렇게 하면 안 돼요"),
]
NO_TITLE_CHARS = re.compile(r"[<>]")  # 유튜브 제목·설명에 못 쓰는 글자


def cache_path():
    return core.WORK / "channel_cache.json"


# ---------- 우리 채널 목록 저장 ----------

def _own_channel(url):
    """비워 두고 불러왔거나(우리 채널) 설정의 우리 채널 주소면 True. 영상·재생목록 주소는 False."""
    url = (url or "").strip().lower()
    if not url:
        return True
    if re.search(r"watch\?|youtu\.be/|/shorts/[\w-]{11}|/live/|playlist\?", url):
        return False
    key = (core.CONFIG.get("channel_url") or "").rstrip("/").split("/")[-1].lower()
    return bool(key) and key in url


def remember_listing(rows, kind="videos", url=None):
    """채널 불러오기 결과는 그대로 돌려주고, 우리 채널이면 제목·조회수를 channel_cache.json 에 남김 (실패해도 목록엔 영향 없음)."""
    try:
        if rows and kind in ("videos", "shorts") and _own_channel(url):
            data = load_cache() or {}
            data[kind] = [{"title": str(r.get("title") or ""), "views": int(r.get("views") or 0), "duration": r.get("duration") or 0,
                           **({"id": str(r["id"])} if r.get("id") else {})}  # id: 예전에 받은 영상이 우리 채널 것인지 (source.from_channel_cache)
                          for r in rows if r.get("title")][:300]
            data["saved"] = time.strftime("%Y-%m-%d %H:%M")
            _write_json(cache_path(), data)
    except Exception:
        pass
    return rows


def load_cache():
    try:
        d = json.loads(cache_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for i in range(20):  # Windows: 백신·OneDrive 가 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


# ---------- 한국어 조사 ----------

def _batchim(word):
    """마지막 글자 받침 번호 (0 없음 · 8 ㄹ) · 한글/숫자가 아니면 None."""
    w = re.sub(r"[^0-9A-Za-z가-힣]+$", "", word or "")
    if not w:
        return None
    c = w[-1]
    if "가" <= c <= "힣":
        return (ord(c) - 0xAC00) % 28
    if c.isdigit():  # 영 일 이 삼 사 오 육 칠 팔 구
        return {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}[c]
    return None


_JOSA = {"이": ("이", "가"), "을": ("을", "를"), "은": ("은", "는"), "와": ("과", "와"), "으로": ("으로", "로")}
_JOSA_OF = {"이": "이", "가": "이", "을": "을", "를": "을", "은": "은", "는": "은", "와": "와", "과": "와", "으로": "으로", "로": "으로"}


def josa(word, kind):
    """josa('패스', '을') → '를' · josa('슈팅', '이') → '이' · 영어처럼 모르면 '을(를)'."""
    b = _batchim(word)
    if kind == "으로":
        return "(으)로" if b is None else ("로" if b in (0, 8) else "으로")
    a, n = _JOSA[kind]
    return f"{a}({n})" if b is None else (a if b else n)


def fill(tpl, topics, hook=""):
    """제목 틀 채우기: {주제} {주제:이} {주제1}~{주제3} {훅}."""
    topics = [t for t in topics if t] or ["풋살"]
    vals = {"주제": topics[0], "훅": hook or topics[0]}
    for i in range(3):
        vals[f"주제{i + 1}"] = topics[min(i, len(topics) - 1)]

    def rep(m):
        v = vals.get(m[1])
        if v is None:
            return m[0]
        return v + (josa(v, m[2]) if m[2] else "")

    return re.sub(r"\{(주제\d?|훅)(?::(이|을|은|와|으로))?\}", rep, tpl)


# ---------- 주제어 ----------

def _term_re(term):
    body = r"\s?".join(re.escape(p) for p in term.split())
    if len(term.replace(" ", "")) <= 2:
        return re.compile(rf"(?<![가-힣0-9]){body}(?={_JOSA_TAIL}(?![가-힣]))")
    return re.compile(body)


_TERM_RES = [(t, _term_re(t)) for t in TERMS]


def _strip_josa(w):
    for j in _STRIP_JOSA:
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[: -len(j)]
    return w


def topic_keywords(texts, n=6):
    """대사에서 주제어 (많이 나온 순). 풋살 용어가 없으면 자주 나온 낱말."""
    text = " ".join(t for t in texts if t)
    hits = []
    for term, rx in _TERM_RES:
        found = list(rx.finditer(text))
        if found:
            hits.append((len(found), -found[0].start(), term))
    if hits:
        hits.sort(reverse=True)
        floor = 2 if hits[0][0] >= 2 else 1
        out = [t for c, _, t in hits if c >= floor]
        # 같은 뜻이 겹치면 많이 나온 쪽만 (슛/슈팅)
        if "슛" in out and "슈팅" in out:
            out.remove("슛" if out.index("슈팅") < out.index("슛") else "슈팅")
        return out[:n]
    cnt = Counter()
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,10}", text):
        w = _strip_josa(w)
        if 2 <= len(w) <= 6 and w not in STOP and not _VERBISH.search(w) and not w.isdigit():
            cnt[w] += 1
    return [w for w, c in cnt.most_common(n) if c >= 3]


def in_order(topics, texts):
    """주제어를 영상에 처음 나오는 순서로 ('퍼스트 터치부터 슈팅까지' 같은 제목·소개용)."""
    text = " ".join(t for t in texts if t)
    pos = {}
    for t in topics:
        m = _term_re(t).search(text) if t in TERMS else re.search(re.escape(t), text)
        pos[t] = m.start() if m else len(text)
    return sorted(topics, key=lambda t: pos[t])


# ---------- 제목 패턴 ----------

def _clean_title(t):
    t = re.sub(r"#\S+", "", t or "")  # 제목 끝의 해시태그는 빼고 봄
    return re.sub(r"\s+", " ", t).strip(" |ㅣ-")


def _top(cache, kind, k=30):
    rows = (cache or {}).get(kind) or []
    return sorted((r for r in rows if r.get("title")), key=lambda r: -int(r.get("views") or 0))[:k]


def skeletons(rows, n=3):
    """조회수 높은 제목에서 주제어 자리를 비운 틀 (주제어가 딱 하나 있는 제목만)."""
    out = []
    for r in rows:
        t = _clean_title(r["title"])
        found = []
        for term, rx in _TERM_RES:
            for m in rx.finditer(t):
                if not any(m.start() < b and a < m.end() for a, b, _ in found):
                    found.append((m.start(), m.end(), term))
        if len(found) != 1:
            continue
        a, b, _ = found[0]
        rest = t[b:]
        j = re.match(r"(으로|이|가|을|를|은|는|와|과|로)(?![가-힣])", rest)
        sk = t[:a] + ("{주제:" + _JOSA_OF[j[1]] + "}" + rest[len(j[1]):] if j else "{주제}" + rest)
        if 6 <= len(sk) <= 90 and sk not in out:
            out.append(sk)
        if len(out) >= n:
            break
    return out


def _style(rows):
    """우리 채널이 자주 쓰는 머리말 '[…]'·꼬리말 '| …' (조회수 상위 제목의 40% 이상이 같을 때만)."""
    if len(rows) < 5:
        return "", ""
    heads = Counter(m[0] for m in (re.match(r"^\s*[\[【]([^\]】]{1,15})[\]】]", r["title"]) for r in rows) if m)
    tails = Counter(m[1].strip() for m in (re.search(r"\s[|ㅣ│]\s?([^|ㅣ│]{2,20})$", r["title"]) for r in rows) if m)
    head = next((h for h, c in heads.most_common(1) if c >= 0.4 * len(rows)), "")
    tail = next((t for t, c in tails.most_common(1) if c >= 0.4 * len(rows)), "")
    return head.strip(), tail


def _family_order(rows):
    """조회수가 높은 제목에 많이 쓰인 말투부터 (목록이 없으면 기본 순서)."""
    if not rows:
        return list(FAMILIES)
    top = max(int(r.get("views") or 0) for r in rows) or 1
    w = {f[0]: sum(max(0.05, int(r.get("views") or 0) / top) for r in rows if re.search(f[1], r["title"])) for f in FAMILIES}
    return sorted(FAMILIES, key=lambda f: -w[f[0]])  # 같으면 기본 순서 유지


def tidy_title(t, limit=100):
    t = NO_TITLE_CHARS.sub("", re.sub(r"\s+", " ", t or "")).strip()
    if len(t) > limit:
        cut = t[:limit]
        sp = cut.rfind(" ")
        t = (cut[:sp] if sp >= limit * 0.6 else cut).rstrip(" ,·|-")
    return t


def one_minute(dur):
    """1분 이하 영상인지 (내보낸 영상은 60.0x초가 되기도 함) · 길이를 모르면 False."""
    try:
        return 0 < float(dur) < 61
    except (TypeError, ValueError):
        return False


def title_candidates(topics, hook=None, fmt="long", cache=None, n=5, flow=None, dur=None):
    """제목 후보 3~5개: 편집실 훅 → 우리 채널 인기 제목 틀 → 기본 틀 (조회수 높은 말투 먼저).
    topics: 많이 나온 순 · flow: 영상에 나오는 순서 (여러 주제를 정리하는 제목에 씀) · dur: 영상 길이(초)."""
    topics = [t for t in (topics or []) if t] or ["풋살"]
    flow = [t for t in (flow or []) if t in topics[:3]]
    flow += [t for t in topics[:3] if t not in flow]
    cache = load_cache() if cache is None else cache
    kind = "shorts" if fmt == "shorts" else "videos"
    rows = _top(cache, kind) or _top(cache, "shorts" if kind == "videos" else "videos")
    head, tail = _style(rows)
    out = []

    def add(t, styled=False):
        t = tidy_title(t)
        if styled and fmt != "shorts":  # 우리 채널 머리말·꼬리말 (100자 넘으면 붙이지 않음)
            if tail:
                t = t.replace(" (최경진 감독)", "")
            if head and not t.startswith(head) and len(head) + 1 + len(t) <= 100:
                t = f"{head} {t}"
            if tail and tail not in t and len(t) + 3 + len(tail) <= 100:
                t = f"{t} | {tail}"
        key = re.sub(r"\W+", "", t)
        if len(t) >= 4 and key and key not in {re.sub(r"\W+", "", x) for x in out}:
            out.append(t)

    used = set()
    if hook:
        add(hook if fmt == "shorts" or len(hook) > 20 else f"{hook} (최경진 감독)", styled=True)
        used |= {f[0] for f in FAMILIES if re.search(f[1], hook)}
    for sk in skeletons(rows, 2):
        add(fill(sk, topics, hook))
        used |= {f[0] for f in FAMILIES if re.search(f[1], sk)}  # 같은 말투의 기본 틀은 건너뜀 (비슷한 제목 두 번 X)
    for name, _, long_tpl, short_tpl in _family_order(rows):
        if len(out) >= n:
            break
        if name in used:
            continue
        tpl = short_tpl if fmt == "shorts" else long_tpl
        if not tpl or (name == "정리" and len(flow) < 2):
            continue
        if name == "정리":
            tpl = "{주제1}부터 {주제" + str(len(flow)) + "}까지 한 번에 정리"
        if "1분" in tpl and not one_minute(dur):  # 쇼츠는 3분까지 · 1분 넘거나 길이를 모르면 '1분'이라 하지 않음
            tpl = tpl.replace("1분 꿀팁", "꿀팁 하나")
        add(fill(tpl, flow if name == "정리" else topics, hook), styled=True)
    for name, _, long_tpl, _s in FAMILIES[1:]:  # 그래도 3개가 안 되면 (틀이 겹칠 때) 긴 영상 틀로 채움
        if len(out) >= 3:
            break
        add(fill(long_tpl, topics, hook))
    return out[:n]
