"""썸네일 제목 문구(2줄 헤드라인) 후보 만들기.
- 규칙: 영상 제목·받아쓰기의 풋살 기술어(주제) + 잘 되는 유튜브 썸네일 문구 틀('무조건 봐', '~의 비밀', '1분만 투자하세요', '이것만 알면',
  질문형, 반전 O/X …) → 줄 길이·주제·강조 낱말·금지어로 점수 → 서로 다른 틀로 8개.
- 클로드(선택): 사용자 PC 의 Claude Code(내 클로드 계정)에 제목·대사 요약·틀·예시를 주고 JSON 10개를 받아 검사한 뒤 analysis/<영상>/thumb_copy.json 에 기억.
항목: {l1, l2, emph: [줄 0|1, 시작, 끝], sub, why, score, tag(놀람·보기·정답·오답·불·질문), pid(틀), src(rule|ai), ox?: [아니오, 예]}"""
import json
import re
import threading
import time

import core
import hooks

LINE_MAX = 12        # 한 줄 글자 수(띄어쓰기 빼고) 상한 — 넘으면 버림
LINE_GOOD = (3, 9)   # 작게 봐도 읽히는 한 줄 길이
TOTAL_MAX = 20
N_OUT = 8
AI_N = 10
AI_TIMEOUT = 150
CACHE = "thumb_copy.json"
# 풋살·축구 기술어 (긴 것부터 · 띄어쓰기는 있어도 없어도 찾음) — hooks.TERMS 에 썸네일용 낱말을 더함
TERMS = sorted(set(hooks.TERMS) | {"발바닥 드래그", "오프더볼", "체크백", "프레스", "패스 앤 무브", "수비 전환", "콘 드리블", "디딤발", "발등", "아라", "피보",
                                   "ALA", "세트피스", "킥인", "1대1 돌파", "개인기", "볼 키핑", "턴 동작", "마무리 슈팅", "공간", "드래그"}, key=lambda t: (-len(t), t))
GENERIC = {"풋살", "축구", "영상", "오늘", "기술", "선수", "감독", "코치", "경기", "훈련", "연습", "레슨", "강좌"}
BANNED = ("충격", "경악", "실화냐", "미쳤", "사망", "폭로", "인생 역전", "100%", "역대급", "사기캐", "참교육", "개꿀", "ㅋㅋ")
FILLER_HEAD = ("진짜", "이거", "자", "그리고", "오늘은", "여기서", "이게", "그래서", "근데", "그냥", "여러분", "제가", "저는", "이제", "자 ", "또")
EMO = (("놀람", ("대박", "속았", "깜짝", "와 ", "헐", "말도 안")), ("보기", ("봐", "보세요", "보면", "주목")), ("오답", ("하지 마", "실수", "안 돼", "틀린", "금지")),
       ("정답", ("정답", "이렇게", "제대로")), ("불", ("무조건", "레벨업", "필살기", "확", "바로")), ("질문", ("?", "왜", "어떻게")))
# 레퍼런스 채널(쪼살·쌈바·해주호·JK 등)에서 자주 보이는 짧은 문구 형식 — 클로드에게 보여 주는 예시 (낱말은 바꿔 씀)
REF_EXAMPLES = ["영상만 봐도 / 실력이 늘어요", "수비가 못 막는 / 1가지", "수비 전환 / 3초 법칙", "플랩 레벨업 / 바로 됩니다", "수비를 속이는 / 발바닥 드래그", "ALA / 움직임",
                "풋살 국가대표가 / 알려주는 드리블", "이거 하나로 / 필살기!!", "풋살 고수가 / 쓰는 기술", "기본기 / 총정리!", "안 배운 게 / 맞아?", "영상에선 / 못해 보이지?",
                "슛? X / 드래그 O", "1분만 / 투자하세요", "떨지 마세요!", "이것만 알면 / 1대1 끝", "프로는 / 이렇게 찹니다", "왜 나만 / 뺏길까?", "3가지 / 총정리", "진짜 쉽게 / 알려줄게요"]
_LOCK = threading.Lock()


def nospace(t):
    return re.sub(r"\s+", "", t or "")


def _term_rx(term):
    """기술어 찾기: 두 글자 이하는 앞에 다른 글자가 붙지 않은 것만 ('턴' ≠ '패턴'), 띄어쓰기는 있어도 없어도."""
    body = r"\s?".join(re.escape(p) for p in term.split())
    return re.compile(("(?<![가-힣A-Za-z0-9])" if len(nospace(term)) <= 2 else "") + body, re.I)


def topics(title, texts, n=4):
    """주제(기술어) — 제목에 있는 기술어 먼저, 다음은 대사에 많이 나온 것. 짧은 낱말이 긴 낱말 안에 있으면 긴 것만."""
    body = " ".join(texts)
    scored = []
    for t in TERMS:
        rx = _term_rx(t)
        k = len(rx.findall(body)) + (4 if rx.search(title or "") else 0)
        if k:
            scored.append((k, len(nospace(t)), t))
    scored.sort(reverse=True)
    out = []
    for _, _, t in scored:
        if any(nospace(t) in nospace(o) for o in out):
            continue
        out = [o for o in out if nospace(o) not in nospace(t)] + [t]
    for t in hooks.topic_keywords([title or ""] + list(texts), n=6):
        if t not in GENERIC and not any(nospace(t) in nospace(o) or nospace(o) in nospace(t) for o in out):
            out.append(t)
    phrase = title_phrase(title)
    if phrase and not any(nospace(phrase) == nospace(o) for o in out):
        out.insert(0 if any(nospace(o) in nospace(phrase) for o in out[:1]) else min(1, len(out)), phrase)
    return [o for o in out if 1 <= len(nospace(o)) <= 7][:n]


_VERB = re.compile(r"(하세요|해요|합니다|됩니다|이에요|예요|이다|하기|하는|이렇게|저렇게|제대로|진짜|무조건|법|방법|꿀팁|팁|해설|영상|세로|테스트)$")


def title_phrase(title):
    """제목 앞부분의 명사구 ('1대1 돌파 이렇게 하세요' → '1대1 돌파', '발바닥 드래그 기본기' → '발바닥 드래그')."""
    words = [w for w in re.split(r"\s+", re.sub(r"[()!?.,~·|]", " ", re.sub(r"\[[^\]]*\]", " ", title or ""))) if w]  # [꿀팁] 같은 말머리는 빼고
    out = []
    for w in words:
        if _VERB.search(w) or w in ("이렇게", "하세요"):
            break
        out.append(w)
        if len(out) == 2 or len(nospace(" ".join(out))) >= 6:
            break
    p = " ".join(out)
    if len(out) == 2 and out[1] in GENERIC | {"기본기", "하이라이트", "인터뷰"}:
        p = out[0]
    return p if 2 <= len(nospace(p)) <= 7 and p not in GENERIC else ""


def _tag(text):
    for tag, keys in EMO:
        if any(k in text for k in keys):
            return tag
    return "불"


def _cand(l1, l2, emph_line, emph_text, sub, pid, prior, why, ox=None):
    l1, l2 = (l1 or "").strip(), (l2 or "").strip()
    line = (l1, l2)[emph_line] if emph_line in (0, 1) else ""
    s = line.find(emph_text) if emph_text else -1
    emph = [emph_line, s, s + len(emph_text)] if s >= 0 else None
    c = {"l1": l1, "l2": l2, "emph": emph, "sub": sub or "", "pid": pid, "prior": prior, "why": why, "src": "rule"}
    if ox:
        c["ox"] = ox
    return c


def _sentences(texts):
    """대사 문장 (핵심어 점수 높은 순) → [(점수, 문장)]."""
    try:
        import editor  # 핵심어 목록 (지연 import: 무거운 모듈)
        kw = {k: w for k, w in editor.KEYWORDS.items() if k != "?"}
    except Exception:
        kw = {}
    out = []
    for t in texts:
        t = re.sub(r"\s+", " ", str(t or "")).strip()
        if not t:
            continue
        w = sum(v for k, v in kw.items() if k in t)
        out.append((w, t))
    out.sort(key=lambda x: -x[0])
    return out


def _compress(sent):
    """대사 한 문장 → 썸네일 두 줄 (앞 군말 빼고 띄어쓰기에서 반으로) · 안 되면 None."""
    t = sent
    changed = True
    while changed:
        changed = False
        for f in FILLER_HEAD:
            if t.startswith(f + " "):
                t, changed = t[len(f) + 1:].strip(), True
    t = re.sub(r"\s*(요|죠)\s*$", lambda m: m[1], t)
    if len(nospace(t)) > 2 * LINE_GOOD[1] + 2:
        return None
    return split2(t)


def split2(t):
    """두 줄로 (띄어쓰기에서 · 줄 길이 차이가 가장 작게 · 조사·어미는 앞 낱말에 붙은 채로) → (l1, l2) 또는 None."""
    w = t.split()
    if len(w) < 2:
        return (t, "") if len(nospace(t)) <= LINE_GOOD[1] else None
    best = None
    for i in range(1, len(w)):
        a, b = " ".join(w[:i]), " ".join(w[i:])
        d = abs(len(nospace(a)) - len(nospace(b)))
        if max(len(nospace(a)), len(nospace(b))) <= LINE_MAX and (best is None or d < best[0]):
            best = (d, a, b)
    return (best[1], best[2]) if best else None


SKILLS = ("드래그", "드리블", "페인트", "턴", "돌파", "개인기", "스텝오버")  # 수비를 속이는 기술 (속이는 틀은 이 기술 + 대사에 '속이' 가 있을 때만)


def rule_candidates(title, texts):
    """규칙으로 만든 후보 전부 (점수 전).
    레퍼런스(쪼살·쌈바·해주호·JK)의 '결과·대상이 있는 문구' 틀: 무조건 봐(큰 노란 줄) · ~의 비밀 · 영상만 봐도 늘어요 · 플랩 레벨업 · 수비를 속이는 X ·
    N초면 끝 · 이렇게 하면 안 돼요 · 못하는 진짜 이유 · 질문형 · 반전 O/X · 대사 핵심 문장."""
    tp = topics(title, texts)
    X = tp[0] if tp else (title_phrase(title) or "풋살")
    Y = tp[1] if len(tp) > 1 else X
    body = " ".join(texts) + " " + (title or "")
    has = lambda *ks: any(k in body for k in ks)  # noqa: E731
    C = []
    # 쪼살형 'V자 어려우면 / 무조건 봐 / 1분안에 알려줄게': '무조건 봐'가 가장 큰 노란 줄 (작은 흰 꼬리표로 두면 훅이 아님 — 판정)
    if len(nospace(X)) <= 4:  # 판정 2회차: '퍼스트 터치 어려우면'(9자)은 쇼츠 한 줄을 넘음
        C.append(_cand(f"{X} 어려우면", "무조건 봐", 1, "무조건 봐", "1분 만에 알려줄게요", "must", 0.85, "쪼살형 '무조건 봐' (가장 큰 노란 줄 · 흔한 말이라 조금 낮게)"))
    C.append(_cand("고수만 아는", f"{X}의 비밀", 1, X, "실전에서 바로 써먹는", "secret", 0.95, "'~의 비밀' 궁금증"))
    C.append(_cand("영상만 봐도", "실력이 늘어요", 1, "실력이 늘어요", f"{X} 1분 강좌", "grow", 0.9, "쪼살형 약속 문구"))
    C.append(_cand(X, "이렇게 하세요", 0, X, "감독이 직접 알려줘요", "howto", 0.75, "주제 + 해결"))
    C.append(_cand("이것만 알면", f"{X} 끝!", 1, X, "1분만 투자하세요", "only", 0.8, "'이것만 알면' 단순화"))
    C.append(_cand(f"{X} 하나로", "플랩 레벨업", 1, "레벨업", "바로 됩니다", "levelup", 0.75, "쪼살형 결과 약속 (플랩 레벨업)"))
    C.append(_cand(X, "진짜 쉽게", 0, X, "초보도 바로 따라 해요", "easy", 0.65, "쉬움 강조 (주제를 크게)"))
    C.append(_cand("왜 나만", f"{X} 안 될까?", 1, X, "이유는 딱 하나예요", "why", 0.8, "질문형 훅"))
    C.append(_cand(f"{X} 못하는", "진짜 이유", 1, "진짜 이유", "이것만 고치세요", "reason", 0.8, "궁금증 (못하는 진짜 이유)"))
    if any(k in X for k in SKILLS) and has("속이", "속여", "속았", "페인트"):
        C.append(_cand("수비를 속이는", X, 1, X, "1분 강좌", "deceive", 0.95, "쌈바형 '수비를 속이는 X' (대상·결과)"))
    m = re.search(r"(\d{1,2})\s*초", body)
    if m and 1 <= int(m[1]) <= 10:
        C.append(_cand(f"{m[1]}초면 끝나는", X, 1, X, "", "secs", 0.85, "숫자 훅 (대사의 N초)"))
    if has("실수", "안 돼", "하지 마", "틀린", "많은데"):
        C.append(_cand(X, "이렇게 하면 안 돼요", 1, "안 돼요", "다들 여기서 틀려요", "dont", 0.95, "대사에 실수 이야기 → 경고형"))
    if has("첫 번째", "두 번째", "세 가지", "3가지", "몇 가지", "하나 더"):
        C.append(_cand(f"{X} 3가지", "총정리!", 1, "총정리!", "이것만 기억하세요", "list", 0.75, "모음형"))
    if has("국가대표", "프로", "감독"):  # 이력은 모르므로 '감독이 직접' 까지만 (과장 없이)
        C.append(_cand("감독이 직접", f"알려주는 {X}", 1, X, "", "pro", 0.75, "권위형 (대사에 감독·프로)"))
    if has("동호인") and has("차이", "국가대표", "프로"):
        C.append(_cand("국대와 동호인", f"{X} 차이", 1, X, "이것 하나 달라요", "gap", 0.85, "비교 궁금증 (대사에 차이)"))
    if has("대박", "속았", "들어갔", "와 "):
        C.append(_cand("이게", "된다고?", 1, "된다고?", f"{X} 실전 장면", "wow", 0.85, "놀람 질문형 (대사에 감탄)"))
    if Y != X and has("보다", "차이", "vs", "VS", "대신", "비교"):
        C.append(_cand(f"{X} vs {Y}", "정답은?", 1, "정답은?", "직접 비교해 봤어요", "versus", 0.7, "두 주제 비교 (대사에 비교)"))
    # 구체적인 약속 (판정 2회차: 문구 이유 44/84 '막연함' — 숫자·결과·대상이 있는 약속이 프로 채널 문구) · 숫자는 대사에 있을 때만
    kind = _kind(body + " " + X)
    C.append(_cand(f"{X} 핵심은", "딱 1가지", 1, "딱 1가지", "이것만 기억하세요", "one", 0.95, "구체적 약속 (핵심 1가지)"))
    C.append(_cand(X, "이 순서대로!", 0, X, "따라 하면 바로 돼요", "order", 0.85, "구체적 약속 (순서)"))
    res = RESULTS.get(kind)
    if res:
        C.append(_cand(f"{X} 하나로", res[0], 1, res[1], "", "result", 1.0, "결과 약속 (보면 무엇이 좋아지는지)"))
    if kind in ("skill", "dribble"):
        C.append(_cand("수비가 못 막는", X, 1, X, "1분 강좌", "beat", 0.95, "대상이 보이는 약속 (수비를 이김)"))
    mn = re.search(r"(\d{1,2}|한|두|세|네|다섯)\s*(가지|단계|초|번|스텝)", body)
    if mn:
        num = NUMS.get(mn[1], mn[1])
        if str(num).isdigit() and 1 <= int(num) <= 10:
            unit = mn[2]
            if unit in ("가지", "단계", "스텝"):
                C.append(_cand(X, f"{num}{unit}면 끝", 1, f"{num}{unit}", "이것만 기억하세요", "numlist", 1.0, f"숫자 약속 (대사의 {num}{unit})"))
            elif unit == "초":
                C.append(_cand(X, f"{num}초 법칙", 1, f"{num}초 법칙", "", "numsec", 1.05, f"숫자 약속 (대사의 {num}초)"))
    # 반전 O/X: 한 문장에 기술어 두 개 + '척·대신·말고' → '슛? X / 드래그 O'
    for sent in texts:
        if re.search(r"척|대신|말고|아니라", sent or ""):
            found = sorted({t for t in TERMS if _term_rx(t).search(sent)}, key=lambda t: sent.find(t.split()[0]))
            found = [t for t in found if not any(nospace(t) in nospace(o) and t != o for o in found)]
            if len(found) >= 2:
                a, b = found[0], found[-1]
                C.append(_cand(f"{a}?", f"{b}!", 1, b, f"{a} 대신 {b}", "ox", 0.9, "반전 O/X (대사)", ox=[a, b]))
                break
    # 대사에서 바로 (핵심어 많은 문장 2개 · 문장 안에서 주제가 든 마디만) — 주제가 든 줄을 크게
    k = 0
    for w, sent in _sentences(texts):
        if w <= 0 or k >= 2:
            break
        parts = [x for x in re.split(r"(?<=요|다|죠)\s+", sent) if len(nospace(x)) >= 5]
        sent = max(parts, key=lambda x: (any(t in x for t in tp), -abs(len(nospace(x)) - 10))) if parts else sent
        sp = _compress(sent)
        if sp and sp[1]:
            l1, l2 = sp
            e = next((t for t in tp if t in l1 + l2), "")
            C.append(_cand(l1, l2, 1 if e in l2 or not e else 0, e or l2.split()[-1], "", f"line{k}", 0.7, "대사에서 핵심 문장"))
            k += 1
    tl = split2(title or "")
    if tl and tl[1] and not re.search(r"테스트|세로|영상$", title or ""):
        C.append(_cand(tl[0], tl[1], 0, X if X in tl[0] else "", "", "title", 0.6, "영상 제목"))
    ctx = context_boost(body)
    for c in C:
        c["prior"] = round(c["prior"] + ctx.get(c["pid"], 0), 3)
    return C, tp


NUMS = {"한": "1", "두": "2", "세": "3", "네": "4", "다섯": "5"}
KINDS = (("shoot", ("슈팅", "슛", "킥", "디딤발", "골")), ("dribble", ("드리블", "드래그", "돌파", "페인트", "개인기", "턴", "스텝오버")),
         ("touch", ("터치", "트래핑", "볼 키핑", "퍼스트")), ("tactic", ("오프더볼", "공간", "전술", "전환", "프레스", "압박", "움직임", "패스 앤 무브", "시야", "체크백")))
# 결과 약속: (둘째 줄, 강조 낱말) — 기술 종류별로 보면 무엇이 좋아지는지
RESULTS = {"shoot": ("골이 늘어요", "골이 늘어요"), "dribble": ("수비가 속아요", "수비가 속아요"), "touch": ("공 안 뺏겨요", "안 뺏겨요"), "tactic": ("패스가 보여요", "패스가 보여요")}


def _kind(text):
    for k, keys in KINDS:
        if any(x in text for x in keys):
            return k
    return ""


# 구체적인 약속(숫자·결과·대상) vs 막연한 문구 — 판정 2회차: '어려우면 무조건 봐'·'~의 비밀'·'이렇게 하세요'·'진짜 쉽게'가 절반 → 프로답지 않음
CONCRETE = re.compile(r"\d|한 가지|1가지|가지|단계|법칙|순서|늘어요|레벨업|속아요|뺏겨|못 막|못 따라|보여요|달라져|바뀌어|됩니다|끝$|끝!|vs|차이")
VAGUE_PIDS = {"must", "secret", "howto", "easy", "only", "title"}
VAGUE = re.compile(r"무조건 봐|의 비밀|이렇게 하세요|진짜 쉽게|총정리|기본기|제대로 배웠|중요해요")


def concrete(c):
    """숫자·결과·대상이 있는 약속인지 (큰 줄·작은 줄 어디든)."""
    return bool(CONCRETE.search(re.sub(r"\d+\s*(대|vs)\s*\d+", "", (c.get("l1") or "") + " " + (c.get("l2") or ""))))  # '1대1' 은 주제 낱말이라 숫자 약속이 아님


def vague(c):
    """막연한 낚시 문구인지 (묶음마다 하나까지만 쓰게)."""
    return c.get("pid") in VAGUE_PIDS or bool(VAGUE.search((c.get("l1") or "") + " " + (c.get("l2") or "")))


HOOKS = ("무조건", "비밀", "레벨업", "끝", "이유", "?", "!", "안 돼", "늘어요", "총정리", "차이", "속이는", "속아요", "보여요", "뺏겨요", "가지", "법칙", "못 막는")


CONTEXT = (  # 영상 성격 → 잘 맞는 문구 틀에 더할 값
    (("공간", "오프더볼", "전술", "압박", "프레스", "전환", "움직임", "시야", "패스 앤 무브"), {"secret": 0.15, "levelup": 0.12, "grow": 0.05, "gap": 0.05}),
    (("드리블", "드래그", "턴", "페인트", "터치", "개인기", "돌파", "스텝오버"), {"howto": 0.08, "must": 0.05, "easy": 0.05, "ox": 0.05, "deceive": 0.1}),
    (("슈팅", "슛", "골", "킥"), {"wow": 0.12, "must": 0.05, "line0": 0.05, "reason": 0.08}),
    (("유소년", "아이들", "초보", "기본기"), {"easy": 0.15, "grow": 0.15, "list": 0.05}),
    (("실수", "안 돼", "하지 마"), {"dont": 0.12}),
)


def context_boost(body):
    out = {}
    for keys, add in CONTEXT:
        if any(k in body for k in keys):
            for pid, v in add.items():
                out[pid] = out.get(pid, 0) + v
    return out


def score(c, tp):
    """후보 점수: 틀의 기본값 + 줄 길이 + 주제 포함 + 강조 낱말 길이 − 금지어."""
    text = c["l1"] + " " + c["l2"]
    if any(b in text for b in BANNED):
        return -99.0
    lens = [len(nospace(c["l1"])), len(nospace(c["l2"]))]
    if max(lens) > LINE_MAX:
        return -99.0
    s = c.get("prior", 0.7) * 2
    for n in lens:
        if n == 0:
            continue
        s += 1.0 if LINE_GOOD[0] <= n <= LINE_GOOD[1] else 0.3 if n < LINE_GOOD[0] else -0.6
    if sum(lens) > TOTAL_MAX:
        s -= 2
    if not lens[1]:
        s -= 0.5
    if concrete(c) and not vague(c):
        s += 0.9
    elif vague(c):
        s -= 0.6
    if tp and any(nospace(t) in nospace(text) for t in tp[:2]):
        s += 1.5
    elif tp and any(nospace(t) in nospace(c.get("sub", "")) for t in tp[:2]):
        s += 0.8  # 주제는 작은 줄에
    if c.get("emph"):
        ln, a, b = c["emph"]
        s += 0.6 if 1 <= len(nospace((c["l1"], c["l2"])[ln][a:b])) <= 5 else 0.2
        big, other = (c["l1"], c["l2"])[ln], (c["l1"], c["l2"])[1 - ln]
        if tp and other and any(nospace(t) in nospace(other) for t in tp[:2]) and not any(nospace(t) in nospace(big) for t in tp[:2]) \
                and not any(h in big for h in HOOKS):
            s -= 1.5  # 핵심 낱말(주제)이 작은 줄로 가고 큰 줄은 밋밋함 ('디딤발 위치가 / 핵심이에요' — 판정)
    return round(s, 3)


def _pick(cands, n=N_OUT):
    """점수 순 + 같은 틀·같은 두 줄은 하나만."""
    out, seen = [], set()
    for c in sorted(cands, key=lambda c: -c["score"]):
        key = (c["l1"], c["l2"])
        if c["score"] < 0 or key in seen or any(o["pid"] == c["pid"] for o in out if c["src"] == "rule"):
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= n:
            break
    return out


def _texts(name):
    try:
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        return [str(s.get("text") or "") for s in segs if isinstance(s, dict)]
    except (OSError, ValueError):
        return []


def nice_title(name):
    """보관함 이름 'YYYYMMDD_영상ID_제목.mp4' → 제목 (화면 niceName 과 같은 규칙)."""
    return re.sub(r"\.[^.]+$", "", re.sub(r"^\d{8}_[A-Za-z0-9_-]{11}_", "", name or ""))


def finish(c, tp):
    c["score"] = score(c, tp)
    c["tag"] = _tag(c["l1"] + " " + c["l2"])
    c["concrete"], c["vague"] = concrete(c) and not vague(c), vague(c)
    c.pop("prior", None)
    return c


def suggest(name, with_ai=True):
    """영상의 문구 후보 → {"items": [...8~10], "topics": [...], "ai": 클로드 결과가 있는지}. 규칙은 바로, 클로드는 기억해 둔 것만."""
    title, texts = nice_title(name), _texts(name)
    C, tp = rule_candidates(title, texts)
    items = [finish(c, tp) for c in C]
    ai = load_ai(name) if with_ai else None
    if ai:
        items += [finish(dict(c, prior=1.05), tp) for c in ai]
    picked = _pick(items, N_OUT + (2 if ai else 0))
    return {"items": picked, "topics": tp, "ai": bool(ai)}


# ---------- 클로드 (선택 · 내 클로드 계정) ----------

PROMPT_VER = 2   # 클로드 문구 질문이 바뀌면 올림 → 기억해 둔 클로드 문구를 다시 받음 (2: 구체적 약속·줄 8자)


def _sig(name):
    try:
        mt = int((core.adir(name) / "transcript.json").stat().st_mtime)
    except OSError:
        mt = 0
    return [mt, nice_title(name), PROMPT_VER]


def load_ai(name):
    """기억해 둔 클로드 문구 (받아쓰기·제목이 그대로일 때만) → 목록 또는 None."""
    try:
        d = json.loads((core.adir(name) / CACHE).read_text(encoding="utf-8"))
        if d.get("sig") == _sig(name) and isinstance(d.get("items"), list):
            return [c for c in (_check_ai(x) for x in d["items"]) if c]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def prompt(name):
    title, texts = nice_title(name), _texts(name)
    tp = topics(title, texts)
    body = " ".join(texts)
    key = [s for _, s in _sentences(texts)[:8]]
    L = ["당신은 구독자 수십만 한국 풋살·축구 레슨 채널(쪼살·쌈바 풋살 클래스·풋살해주호·JK 아트사커)의 썸네일 카피라이터예요.",
         "아래 영상의 썸네일에 크게 들어갈 2줄 헤드라인을 만들어 주세요. 채널: '풋살사관학교'(최경진 감독의 풋살 레슨 채널). 시청자: 풋살 동호인·플랩(FLAB) 참가자.", "",
         f"[영상 제목] {title}", f"[주제 낱말] {', '.join(tp) or '없음'}", "[대사 요약 (앞부분)]", body[:2500], "[핵심 문장]"] + [f"- {s}" for s in key] + [
         "", "[잘 되는 문구 — 레퍼런스 채널 실제 예시 (낱말은 이 영상에 맞게 바꾸세요)]"] + [f"- {x}" for x in REF_EXAMPLES] + [
         "", "[좋은 썸네일 문구의 조건 — 가장 중요]",
         "- 10개 중 7개 이상은 '구체적인 약속'이어야 해요: 숫자·시간·결과·대상이 들어간 문구. 예: '수비 전환 / 3초 법칙', '수비가 못 막는 / 1가지', "
         "'이 순서만 지키면 / 슈팅이 낮아져요', '디딤발 하나로 / 골이 늘어요', '첫 터치 / 수비 반대쪽으로'. 숫자는 대사에 나온 것만 쓰고, 없으면 '1가지'·결과로.",
         "- 결과나 대상이 보여야 해요: '실력이 늘어요', '플랩 레벨업 바로 됩니다', '수비를 속이는 발바닥 드래그'처럼 보면 무엇이 좋아지는지·누구를 이기는지.",
         "- 금지(막연한 낚시 문구): '무조건 봐', '~의 비밀', '이렇게 하세요', '진짜 쉽게', '총정리', '제대로 배웠어?', '이게 진짜 중요해요'. 이런 말은 많아야 1개.",
         "- 질문형은 답이 궁금한 구체적인 질문만: '수비 전환, 몇 초 걸려?', '슛이 뜨는 이유는?'.",
         "- 같은 틀 반복은 피하세요. 10개가 서로 다른 틀이어야 해요.",
         "- 영상 내용과 맞는 말만. 과장·낚시(충격·경악·실화냐·100%·역대급)는 쓰지 마세요. 해요체·반말 질문형 모두 좋아요.",
         "", "[형식 규칙]", "- 한 줄은 띄어쓰기를 넣고 8자 이내(쇼츠 세로 화면 기준 · 많아야 9자), 두 줄 합쳐 16자 이내. 작은 화면(휴대폰 목록)에서도 읽혀야 해요.",
         "- emph = 노랗고 가장 크게 칠할 낱말(1~6자). l1 또는 l2 안에 글자 그대로 있어야 하고, 그 줄이 가장 큰 줄이 돼요. 보통 주제 낱말이나 결과·훅 낱말.",
         "- sub = 어두운 상자 안에 작게 들어갈 보조 문구(선택, 12자 이내, 예: '1분만 투자하세요', '1분 강좌', '감독이 직접 알려줘요').",
         f"- 서로 다른 틀로 {AI_N}개.", "", "[대답 형식] JSON 배열만 (설명 없이):",
         '[{"l1": "첫 줄", "l2": "둘째 줄", "emph": "강조 낱말", "sub": "보조 문구"}]']
    return "\n".join(L)


def ai_ready():
    """클로드 프로그램이 이 PC에 있고 로그인돼 있는지 (상태는 60초 기억 · 실패하면 False)."""
    try:
        import claude_cli
        return bool(claude_cli.find_exe()) and claude_cli.status().get("state") == "ready"
    except Exception:
        return False


def _check_ai(x):
    """클로드 항목 하나 검사 → 후보 또는 None (문자열만 · 줄 길이 · 강조 낱말은 줄 안에)."""
    if not isinstance(x, dict):
        return None
    l1, l2, em, sub = (x.get(k, "") for k in ("l1", "l2", "emph", "sub"))
    if not all(isinstance(v, str) for v in (l1, l2, em, sub)):
        return None
    l1, l2, em, sub = (re.sub(r"\s+", " ", v).strip() for v in (l1, l2, em, sub))
    if not l1 or len(nospace(l1)) > LINE_MAX or len(nospace(l2)) > LINE_MAX or len(sub) > 16 or any(c in l1 + l2 + sub for c in "<>{}\\"):
        return None
    line = 1 if em and em in l2 else 0
    c = _cand(l1, l2, line, em if em and em in (l1, l2)[line] else "", sub, "ai", 0.75, "클로드 제안")
    c["src"] = "ai"
    return c


def parse_ai(text):
    """클로드 대답 → 검사한 후보 목록. 형식이 아니면 ValueError(한국어)."""
    s = str(text or "")
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.S)
    if m:
        s = m[1]
    else:
        a, b = s.find("["), s.rfind("]")
        if a < 0 or b <= a:
            raise ValueError("클로드 대답에서 문구 목록을 찾지 못했어요")
        s = s[a:b + 1]
    try:
        d = json.loads(s)
    except ValueError:
        raise ValueError("클로드 대답 형식이 깨져 있어요") from None
    if not isinstance(d, list):
        raise ValueError("클로드 대답 형식이 달라요")
    out = [c for c in (_check_ai(x) for x in d[:20]) if c]
    if not out:
        raise ValueError("쓸 수 있는 문구가 없었어요")
    return out


def run_ai(name, log=print, cancel=None, progress=True):
    """내 클로드 계정으로 문구 10개 → 검사해서 기억 → {"ok", "items"} (실패하면 규칙 문구만 쓰게 ok False + 안내).
    progress=False: 진행 표시를 바꾸지 않음 (썸네일 분석 작업 안에서 동시에 돌 때)."""
    import claude_cli
    if progress:
        core.set_progress(label="클로드로 문구 만들기", item=name, pct=None, detail="내 클로드 계정 사용 · 문구 만드는 중")

    def tick(sec):
        if progress:
            core.set_progress(label="클로드로 문구 만들기", item=name, pct=None, detail=f"클로드가 썸네일 문구를 만드는 중… (내 클로드 계정 사용 · {sec}초)")
    try:
        res = claude_cli.run(prompt(name), cancel=cancel, on_tick=tick, timeout=AI_TIMEOUT)
    except claude_cli.ClaudeError as e:
        log(f"클로드 문구 · {e.kind}")  # 종류만 (대답·프롬프트는 남기지 않음)
        return {"ok": False, "error": str(e), "kind": e.kind}
    try:
        items = parse_ai(res.get("text"))
    except ValueError as e:
        log("클로드 문구 · 형식 다름")
        return {"ok": False, "error": f"{e}. 다시 눌러 주세요 (규칙 문구는 그대로 있어요)", "kind": "format"}
    def plain(c):  # 기억할 때는 클로드가 준 모양 그대로 (강조는 낱말로)
        e = c["emph"]
        return {"l1": c["l1"], "l2": c["l2"], "sub": c["sub"], "emph": (c["l1"], c["l2"])[e[0]][e[1]:e[2]] if e else ""}
    data = {"sig": _sig(name), "items": [plain(c) for c in items], "model": res.get("model"), "at": time.strftime("%Y-%m-%d %H:%M")}
    p = core.adir(name) / CACHE
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        import updater
        updater._replace(tmp, p)
    log(f"클로드 문구 · {len(items)}개 저장했어요")
    return {"ok": True, "items": suggest(name)["items"]}


# ---------- 클로드에게 평가받기 (선택 · 썸네일 검수 창) ----------

JUDGE_KEYS = ("readability", "contrast", "hierarchy", "appeal", "pro_level")
JUDGE_PROMPT = """당신은 한국 축구·풋살 유튜브 썸네일을 평가하는 전문가예요. 같은 폴더의 그림 두 장은 같은 썸네일이에요:
small.jpg = 휴대폰 목록 크기(가로 168px 또는 쇼츠 110px), full.jpg = 원래 크기. Read 도구로 두 그림을 열어 보세요.
기준(1~10점): readability(작게 봐도 제목이 읽히는지) · contrast(글자와 배경이 또렷이 구분되는지) · hierarchy(무엇을 먼저 읽는지 분명한지)
· appeal(클릭하고 싶은지) · pro_level(구독자 수만~수십만 축구·풋살 레슨 채널 썸네일 수준인지 — 큰 2줄 제목·굵은 테두리·강조 색·선명한 장면·전술 그래픽 같은 요소).
고칠 점은 이 썸네일 편집기에서 바로 할 수 있는 구체적인 일 3개 (예: '둘째 줄을 20% 키우기', '배경을 조금 어둡게').
대답은 JSON 하나만: {"readability": 0, "contrast": 0, "hierarchy": 0, "appeal": 0, "pro_level": 0, "fixes": ["", "", ""], "summary": "한 줄 평"}"""


def parse_judge(text):
    s = str(text or "")
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("평가 형식을 찾지 못했어요")
    try:
        d = json.loads(s[a:b + 1])
    except ValueError:
        raise ValueError("평가 형식이 깨져 있어요") from None
    out = {}
    for k in JUDGE_KEYS:
        try:
            out[k] = max(1, min(10, int(round(float(d.get(k))))))
        except (TypeError, ValueError):
            raise ValueError("점수가 빠졌어요") from None
    out["fixes"] = [re.sub(r"\s+", " ", str(x)).strip()[:120] for x in (d.get("fixes") or []) if str(x).strip()][:3]
    out["summary"] = re.sub(r"\s+", " ", str(d.get("summary") or "")).strip()[:160]
    return out


def judge(small_data, full_data, cancel=None):
    """썸네일 그림(작게·원래 크기 dataURL)을 내 클로드 계정에 보여 주고 점수·고칠 점 → {"ok", ...}. 레퍼런스 그림은 보내지 않음(저작권)."""
    import base64
    import tempfile
    import claude_cli
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="futsal-judge-", ignore_cleanup_errors=True) as tmp:
        imgs = []
        for nm, d in (("small.jpg", small_data), ("full.jpg", full_data)):
            f = Path(tmp) / nm
            f.write_bytes(base64.b64decode(str(d).split(",", 1)[1]))
            imgs.append((f, nm))

        def tick(sec):
            core.set_progress(label="클로드에게 평가받기", pct=None, detail=f"클로드가 썸네일을 보는 중… (내 클로드 계정 사용 · {sec}초)")
        try:
            res = claude_cli.run(JUDGE_PROMPT, images=imgs, cancel=cancel, on_tick=tick, timeout=AI_TIMEOUT)
        except claude_cli.ClaudeError as e:
            return {"ok": False, "error": str(e), "kind": e.kind}
    try:
        return dict(parse_judge(res.get("text")), ok=True)
    except ValueError as e:
        return {"ok": False, "error": f"{e}. 다시 눌러 주세요", "kind": "format"}
