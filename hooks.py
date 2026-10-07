"""올리기 키트의 제목 후보 — 우리 채널에서 반응이 좋았던 제목 패턴 + 영상 주제어 + 편집실 쇼츠 훅(editor._hook).

AI 없이 규칙으로:
- '소재 찾기'에서 우리 채널을 불러오면 목록을 작업 폴더의 channel_cache.json 에 남겨 둠
- 조회수 높은 제목에서 주제어 자리만 비운 틀(예: '{주제}를 잘하는 3가지 방법')과 자주 쓰는 말투(꿀팁·이유·실수…)를 찾음
  (회차 번호·행사 이름·괄호 속 영어 풀이는 빼고, 그 영상만의 기술 이름은 통째로 주제 자리로 · D-027)
- 저장된 목록이 없으면 기본 틀을 씀
- 주제어는 TERMS + 기술 이름(SKILLS) + 용어 사전(작업 폴더 dict.json · 없으면 기본 사전)에서 긴 이름부터 찾음 (topic_terms)
"""
import json
import os
import re
import time
from collections import Counter
from functools import lru_cache

import core

# 풋살 레슨에서 주제가 되는 말 (긴 것부터 찾음 · 2글자 이하는 앞뒤가 다른 낱말에 붙어 있으면 안 셈: '턴' ≠ '패턴')
# 편집실 강조 자막(editor)·기획 분석(plan)·채널 전략(strategy)도 이 목록을 씀 — 제목·태그용 넓은 목록은 topic_terms()
TERMS = [
    "퍼스트 터치", "볼 컨트롤", "볼 키핑", "볼 터치", "오프 더 볼", "세트피스", "로테이션", "포지셔닝", "빌드업", "스텝오버",
    "인사이드", "아웃사이드", "골키퍼", "코너킥", "프리킥", "트래핑", "스트레칭", "워밍업", "개인기", "발바닥", "드리블",
    "월패스", "슈팅", "패스", "토킥", "칩슛", "킥인", "돌파", "페인트", "수비", "압박", "전환", "역습", "침투", "마무리",
    "크로스", "헤딩", "스크린", "피보", "아라", "픽소", "체력", "스피드", "밸런스", "시야", "턴", "슛", "킥", "1대1", "2대1",
]
# 기술·전술 이름 (잘 된 쇼츠는 '팬텀 드리블'·'바디 페인팅' 같은 이름을 제목에 씀 · 띄어 쓴 이름은 붙여 써도 찾음)
SKILLS = [
    "팬텀 드리블", "스네이크 드리블", "각 드리블", "바디 페인팅", "바디 페인트", "숄더 페인트", "헛다리", "시저스", "엘라스티코",
    "플립플랩", "영재 플랩", "라크로케타", "마르세유 턴", "크루이프 턴", "피보 턴", "룰렛", "드래그백", "체크백", "스쿱턴", "슬립백",
    "솔 턴", "솔 롤", "사포", "레인보우 플릭", "넛메그", "알까기", "인앤아웃", "더블 터치", "방향전환", "등지기", "피벗 플레이", "피보 플레이",
    "인스텝 킥", "인사이드 킥", "아웃사이드 킥", "힐킥", "힐패스", "로빙", "발리", "롭 패스", "스루패스", "킬패스", "원투 패스",
    "백패스", "골클리어런스", "맨투맨", "지역 방어", "세컨볼",
]
# 용어 사전에 있어도 주제어가 아닌 말 (채널·사람 이름 · 기본 사전의 '풋살사관학교'·'최경진 감독')
_NOT_TOPIC = re.compile(r"사관학교|최경진|(?:감독|선수|코치|님)$")
# 낱말 뒤에 붙어도 같은 낱말로 세는 말: 조사 + '하고·하면서·주고·받아' 같은 풀이말 ('패스하고 바로 움직이세요')
_JOSA_TAIL = (r"(?:이|가|을|를|은|는|도|만|의|에|에서|으로|로|와|과|이랑|랑|이나|나|이란|란|이라고|라고|이라는|라는|이라|라|까지|부터|처럼|보다|이요|요|"
              r"(?:하|해|했|할|한|합|함|되|돼|됐|된|될|주|줘|줬|준|줄|받)[가-힣]{0,4})?")
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


_JOSA = {"이": ("이", "가"), "을": ("을", "를"), "은": ("은", "는"), "와": ("과", "와"), "으로": ("으로", "로"), "이란": ("이란", "란")}
_JOSA_OF = {"이": "이", "가": "이", "을": "을", "를": "을", "은": "은", "는": "은", "와": "와", "과": "와", "으로": "으로", "로": "으로",
            "이란": "이란", "란": "이란"}


def josa(word, kind):
    """josa('패스', '을') → '를' · josa('슈팅', '이') → '이' · 영어처럼 모르면 '을(를)'."""
    b = _batchim(word)
    if kind == "으로":
        return "(으)로" if b is None else ("로" if b in (0, 8) else "으로")
    if kind == "이란" and b is None:
        return "(이)란"
    a, n = _JOSA[kind]
    return f"{a}({n})" if b is None else (a if b else n)


def fill(tpl, topics, hook=""):
    """제목 틀 채우기: {주제} {주제:이} {주제1}~{주제3} {훅} (조사: 이·을·은·와·으로·이란)."""
    topics = [t for t in topics if t] or ["풋살"]
    vals = {"주제": topics[0], "훅": hook or topics[0]}
    for i in range(3):
        vals[f"주제{i + 1}"] = topics[min(i, len(topics) - 1)]

    def rep(m):
        v = vals.get(m[1])
        if v is None:
            return m[0]
        return v + (josa(v, m[2]) if m[2] else "")

    return re.sub(r"\{(주제\d?|훅)(?::(이란|이|을|은|와|으로))?\}", rep, tpl)


# ---------- 주제어 ----------

def _term_re(term):
    body = r"\s?".join(re.escape(p) for p in term.split())
    if len(term.replace(" ", "")) <= 2:
        return re.compile(rf"(?<![가-힣0-9]){body}(?={_JOSA_TAIL}(?![가-힣]))")
    return re.compile(body)


def _compact(t):
    return re.sub(r"\s+", "", t or "").lower()


def topic_terms():
    """주제어로 찾을 말: TERMS + 기술 이름(SKILLS) + 용어 사전(작업 폴더 dict.json · 없으면 기본 사전 · 용어와 '고칠 말'의 바른 말).
    채널·사람 이름과 흔한 말은 빼고, 띄어쓰기만 다른 말은 하나로, 긴 이름부터 ('팬텀 드리블'이 '드리블'보다 먼저)."""
    words = list(TERMS) + list(SKILLS)
    try:
        import captions
        d = captions.load_dict(core.dict_path())
        words += list(d.get("terms") or []) + list((d.get("fix") or {}).values())
    except Exception:  # 사전을 못 읽어도 키트는 만듦 (기본 목록으로)
        pass
    out, seen = [], set()
    for w in words:
        w = re.sub(r"\s+", " ", str(w or "")).strip()
        k = _compact(w)
        if not k or k in seen or w in STOP or _NOT_TOPIC.search(w):
            continue
        seen.add(k)
        out.append(w)
    return sorted(out, key=lambda w: -len(_compact(w)))


@lru_cache(maxsize=8)
def _term_res(terms):
    return [(t, _term_re(t)) for t in terms]


def _find_terms(text, terms):
    """글 속 주제어 자리 [(시작, 끝, 용어)] — 긴 이름부터 찾고, 이미 찾은 자리 안의 짧은 말은 따로 세지 않음."""
    found, used = [], bytearray(len(text))
    for term, rx in _term_res(tuple(terms)):
        for m in rx.finditer(text):
            if not any(used[m.start():m.end()]):
                used[m.start():m.end()] = b"\1" * (m.end() - m.start())
                found.append((m.start(), m.end(), term))
    return sorted(found)


def _strip_josa(w):
    for j in _STRIP_JOSA:
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[: -len(j)]
    return w


def topic_keywords(texts, n=6, terms=None):
    """대사에서 주제어 (많이 나온 순). 풋살 용어가 없으면 자주 나온 낱말.
    terms: 찾을 말 (없으면 topic_terms()). 기술 이름이 그 안의 흔한 말('팬텀 드리블' ⊃ '드리블') 횟수의 1/3 이상 나오면 기술 이름 하나로 셈."""
    text = " ".join(t for t in texts if t)
    hits = {}
    for a, _, term in _find_terms(text, topic_terms() if terms is None else terms):
        c, first = hits.get(term, (0, a))
        hits[term] = (c + 1, min(first, a))
    for big in sorted(hits, key=lambda t: -len(_compact(t))):
        for small in [t for t in hits if t != big and _compact(t) in _compact(big)]:
            if big in hits and hits[big][0] * SKILL_SHARE >= hits[small][0]:
                hits[big] = (hits[big][0] + hits[small][0], min(hits[big][1], hits[small][1]))
                del hits[small]
    if hits:
        ranked = sorted(hits, key=lambda t: (-hits[t][0], hits[t][1]))
        floor = 2 if hits[ranked[0]][0] >= 2 else 1
        out = [t for t in ranked if hits[t][0] >= floor]
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
        m = _term_re(t).search(text)
        pos[t] = m.start() if m else len(text)
    return sorted(topics, key=lambda t: pos[t])


# ---------- 제목 패턴 ----------

# 회차·시리즈 표시: 새 영상에 옛 번호가 붙지 않게 뺌 — 숫자가 든 머리말 [1차시]·[풋린이들 과외하기#3]·[풋살사관학교 1기],
# (1부)·(Part 1)·"1탄"·2편·#3·ep.2 (회차 자리는 남기지 않음: 새 영상이 몇 번째인지 모름)
_SERIES = re.compile(r"[\[【][^\]】]*\d[^\]】]*[\]】]"
                     r"|\(\s*(?:\d+\s*(?:부|탄|편|화|차시|기)|(?:part|ep|episode|vol)\.?\s*\d+)\s*\)"
                     r"|[\"'“”‘’]?(?<![0-9])\d+\s*(?:부|탄|편|화|차시|기)(?![가-힣])[\"'“”‘’]?"
                     r"|#\d+|(?<![A-Za-z])(?:part|ep|episode|vol)\.?\s*\d+", re.I)
# 그 영상에만 맞는 군말: 괄호 속 영어 풀이 '(Sole Turn)' · 조회수 자랑 '600만뷰' (주제 자리 밖의 다른 괄호도 틀에서 뺌)
_EXTRA = re.compile(r"\(\s*[A-Za-z][A-Za-z0-9 .,'&/+\-]*\)")
_VIEWS = re.compile(r"\d+(?:\.\d+)?\s?(?:만|천|억)\s?(?:뷰|조회수?|회)\s?|\d+\s?뷰\s?")
# 행사·대회·모집·팀 이름·초대 손님(feat.)이 든 제목은 틀로 안 씀 ('아쉽게 {주제}된 2019 FK CUP 대회…')
_EVENT = re.compile(r"(?<![0-9])(?:19|20)\d{2}(?![0-9])|대회|(?<![A-Za-z])cup(?![A-Za-z])|챔피언십|리그|이벤트|당첨|모집|수강신청|개교|출사표"
                    r"|(?<![A-Za-z])(?:feat|ft)\.?(?![A-Za-z])"
                    r"|브이로그|v-?log|(?<![A-Za-z])live(?![A-Za-z])|라이브|결승|예선|조추첨|우승|대결|초대석|(?<![A-Za-z])vs\.?(?![A-Za-z])"
                    r"|(?<![A-Za-z])(?:FC|FS|FK)(?![A-Za-z])", re.I)
# 주제어 앞에 붙은 이 말들은 기술 이름의 일부가 아니라 꾸밈말이라 그대로 둠 ('풋살 시저스 드리블' → '풋살 {주제}')
_KEEP_LEFT = {"풋살", "축구", "국가대표", "프로", "현역", "레전드", "감독", "선수", "초보", "초보자", "기본", "실전", "필수", "최고", "완벽",
              "쉬운", "간단", "오늘", "이번", "모든", "무조건", "꿀팁", "역대급", "최강", "전", "前", "현", "現", "풋린이", "1분", "진짜",
              "잘", "더", "꼭", "왜", "안", "못", "바로", "다시", "그냥", "정말", "완전", "딱", "단", "그", "이", "저", "새", "첫"}
# 주제어 뒤에 와도 되는 제목 낱말 ('{주제} 꿀팁' · '{주제} 배우기') — 다른 이름말이 오면 '발바닥 방향전환'처럼 주제어가 꾸밈말이라 틀로 안 씀
_TITLE_WORDS = {"꿀팁", "팁", "강좌", "강의", "속성강의", "레슨", "배우기", "연습", "훈련", "방법", "비법", "비결", "기술", "기초", "기본기", "핵심",
                "원리", "차이", "차이점", "실수", "이유", "정리", "총정리", "모음", "마스터", "완성", "특강", "클래스", "루틴", "드릴", "챌린지",
                "공식", "노하우", "포인트", "요령", "가이드", "입문", "실력", "능력", "타이밍", "자세", "동작", "시범", "설명", "강습", "교실",
                "필살기", "끝판왕", "튜토리얼", "안", "못", "잘", "꼭", "더", "왜", "바로", "그냥", "진짜", "정말", "다시", "절대", "혼자", "같이"}
# 조사·말끝으로 끝나는 낱말 = 이름말이 아님 (꾸밈말·풀이말)
_ENDING = re.compile(r"(?:처럼|보다|까지|부터|만큼|대로|마다|밖에|이|가|을|를|은|는|의|에|도|만|와|과|로|랑|서|고|며|면|게|지|요|다|까|죠|"
                     r"한|된|할|될|운|인|던|린|진|른|쁜|적|들)$")
_INVARIANT_JOSA = re.compile(r"(?:에서|까지|부터|처럼|보다|의|에|도|만)(?![가-힣])")
SKELETON_MAX, SKELETON_TRY = 2, 4  # 제목 후보에 쓰는 우리 채널 틀 수 · 주제어가 겹쳐 버릴 때를 위해 더 찾아 둘 수
SKILL_SHARE = 3  # 기술 이름이 그 안의 흔한 말 횟수의 1/3 이상 나오면 기술 이름으로 셈 ('팬텀 드리블' 1번 · '드리블' 3번 → '팬텀 드리블')


def _clean_title(t):
    t = re.sub(r"#[^\s#\d][^\s#]*", "", t or "")  # 제목 끝의 해시태그는 빼고 봄 (#3 같은 회차는 _SERIES)
    t = _VIEWS.sub("", _EXTRA.sub(" ", _SERIES.sub(" ", t)))
    t = re.sub(r"[\[【(]\s*[\]】)]", "", t)  # 비어 버린 괄호
    return re.sub(r"\s+", " ", t).strip(" |ㅣ-/:·")


def _top(cache, kind, k=30):
    rows = (cache or {}).get(kind) or []
    return sorted((r for r in rows if r.get("title")), key=lambda r: -int(r.get("views") or 0))[:k]


def _bare_noun(tok):
    """조사·말끝이 붙지 않은 이름말 (기술 이름의 앞뒤 낱말인지 볼 때)."""
    return bool(re.fullmatch(r"[A-Za-z가-힣][A-Za-z0-9가-힣\-]*", tok)) and not _ENDING.search(tok)


def _slot(t, a, b):
    """주제어 자리 [a, b)를 그 기술 이름 전체로 넓힘: 붙어 있는 앞말('각드리블') + 꾸밈말이 아닌 앞 이름말 두 개까지('시저스 드리블').
    뒤에 다른 이름말이 붙어 주제어가 꾸밈말이면('발바닥 방향전환' · '드리블러') None."""
    while a > 0 and re.match(r"[A-Za-z0-9가-힣]", t[a - 1]):
        a -= 1
    for _ in range(2):
        m = re.search(r"(?:^|\s)[^\sA-Za-z0-9가-힣]*([^\s]+) $", t[:a])
        if not m or not _bare_noun(m[1]) or m[1] in _KEEP_LEFT or m[1] in STOP:
            break
        a = m.start(1)
    rest = t[b:]
    j = re.match(r"(으로|이란|이|가|을|를|은|는|와|과|로|란)(?![가-힣])", rest)  # 받침에 맞춰 바꿀 조사 ('바디 페인팅이란?')
    if not j and not _INVARIANT_JOSA.match(rest):
        if re.match(r"[A-Za-z0-9가-힣]", rest):
            return None
        nxt = re.match(r" ([^\s]+)", rest)
        word = re.sub(r"[^A-Za-z0-9가-힣\-]+$", "", nxt[1]) if nxt else ""
        if word and _bare_noun(word) and _strip_josa(word) not in _TITLE_WORDS:
            return None
    return a, b, j


def skeletons(rows, n=3, terms=None):
    """조회수 높은 제목에서 주제어 자리를 비운 틀 (주제어가 딱 하나 있는 제목만).
    회차 표시·괄호 속 영어 풀이·초대 손님·조회수 자랑은 빼고, 행사·대회·팀 이름이 든 제목은 안 씀 (D-027)."""
    terms = topic_terms() if terms is None else terms
    out = []
    for r in rows:
        if _EVENT.search(r["title"]):
            continue
        t = _clean_title(r["title"])
        found = []
        for a, b, term in _find_terms(t, terms):  # 띄어 쓴 용어 둘은 한 기술 이름 ('시저스 드리블' · '발바닥 방향전환')
            if found and not t[found[-1][1]:a].strip():
                found[-1] = (found[-1][0], b, term)
            else:
                found.append((a, b, term))
        if len(found) != 1:
            continue
        s = _slot(t, found[0][0], found[0][1])
        if not s:
            continue
        a, b, j = s
        if j:
            sk = t[:a] + "{주제:" + _JOSA_OF[j[1]] + "}" + t[b + len(j[1]):]
        else:
            sk = t[:a] + "{주제}" + t[b:]
        sk = re.sub(r"\s+", " ", re.sub(r"\s*\([^(){}]*\)", " ", sk)).strip(" |ㅣ-/:·")  # 주제 자리 밖 괄호 '(1인칭 시점)'은 그 영상 이야기
        words = len(re.sub(r"\{[^}]*\}|[^가-힣A-Za-z]", "", sk)) + (2 if "{주제:이란}" in sk else 0)  # 주제어 말고 남은 글자 ('{주제} 꿀팁')
        if 6 <= len(sk) <= 90 and words >= 2 and sk not in out:
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
    picked = 0
    for sk in skeletons(rows, SKELETON_TRY):
        t = fill(sk, topics, hook)
        if picked >= SKELETON_MAX or _compact(t).count(_compact(topics[0])) > 1:  # 주제어 겹침('풋살 풋살'·'풋살기술 "풋살"')은 버림
            continue
        picked += 1
        add(t)
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
