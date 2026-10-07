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
REF_EXAMPLES = ["영상만 봐도 / 실력이 늘어요", "오프더볼의 / 비밀", "드리블 / 무조건 봐", "플랩 레벨업 / 바로 됩니다", "수비를 속이는 / 발바닥 드래그", "ALA / 움직임",
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


def rule_candidates(title, texts):
    """규칙으로 만든 후보 전부 (점수 전)."""
    tp = topics(title, texts)
    X = tp[0] if tp else (title_phrase(title) or "풋살")
    Y = tp[1] if len(tp) > 1 else X
    body = " ".join(texts) + " " + (title or "")
    has = lambda *ks: any(k in body for k in ks)  # noqa: E731
    C = []
    C.append(_cand(X, "무조건 봐", 1, "무조건 봐", "1분만 투자하세요", "must", 1.0, "레퍼런스 쪼살형 '무조건 봐'"))
    C.append(_cand("고수만 아는", f"{X}의 비밀", 1, X, "실전에서 바로 써먹는", "secret", 0.95, "'~의 비밀' 궁금증"))
    C.append(_cand("영상만 봐도", "실력이 늘어요", 1, "실력이 늘어요", f"{X} 1분 강좌", "grow", 0.9, "쪼살형 약속 문구"))
    C.append(_cand(X, "이렇게 하세요", 0, X, "감독이 직접 알려줘요", "howto", 0.8, "주제 + 해결"))
    C.append(_cand("이것만 알면", f"{X} 끝!", 1, X, "1분만 투자하세요", "only", 0.85, "'이것만 알면' 단순화"))
    C.append(_cand("플랩 레벨업", "바로 됩니다", 0, "레벨업", X, "levelup", 0.8, "쪼살형 결과 약속"))
    C.append(_cand(X, "진짜 쉽게", 1, "진짜 쉽게", "초보도 바로 따라 해요", "easy", 0.7, "쉬움 강조"))
    C.append(_cand("왜 나만", f"{X} 안 될까?", 1, X, "이유는 딱 하나예요", "why", 0.8, "질문형 훅"))
    if has("실수", "안 돼", "하지 마", "틀린", "많은데"):
        C.append(_cand(X, "이 실수 하지 마세요", 1, "실수", "다들 여기서 틀려요", "dont", 0.95, "대사에 실수 이야기 → 경고형"))
    if has("첫 번째", "두 번째", "세 가지", "3가지", "몇 가지", "하나 더"):
        C.append(_cand(f"{X} 3가지", "총정리!", 1, "총정리!", "이것만 기억하세요", "list", 0.75, "모음형"))
    if has("국가대표", "프로", "감독"):  # 이력은 모르므로 '감독이 직접' 까지만 (과장 없이)
        C.append(_cand("감독이 직접", f"알려주는 {X}", 1, X, "", "pro", 0.75, "권위형 (대사에 감독·프로)"))
    if has("대박", "속았", "들어갔", "와 "):
        C.append(_cand("이게", "된다고?", 1, "된다고?", f"{X} 실전 장면", "wow", 0.85, "놀람 질문형 (대사에 감탄)"))
    if Y != X and has("보다", "차이", "vs", "VS", "대신", "비교"):
        C.append(_cand(f"{X} vs {Y}", "정답은?", 1, "정답은?", "직접 비교해 봤어요", "versus", 0.7, "두 주제 비교 (대사에 비교)"))
    # 반전 O/X: 한 문장에 기술어 두 개 + '척·대신·말고' → '슛? X / 드래그 O'
    for sent in texts:
        if re.search(r"척|대신|말고|아니라", sent or ""):
            found = sorted({t for t in TERMS if _term_rx(t).search(sent)}, key=lambda t: sent.find(t.split()[0]))
            found = [t for t in found if not any(nospace(t) in nospace(o) and t != o for o in found)]
            if len(found) >= 2:
                a, b = found[0], found[-1]
                C.append(_cand(f"{a}?", f"{b}!", 1, b, f"{a} 대신 {b}", "ox", 0.9, "반전 O/X (대사)", ox=[a, b]))
                break
    # 대사에서 바로 (핵심어 많은 문장 2개 · 문장 안에서 주제가 든 마디만)
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
            C.append(_cand(l1, l2, 1 if e in l2 or not e else 0, e or l2.split()[-1], "", f"line{k}", 0.75, "대사에서 핵심 문장"))
            k += 1
    tl = split2(title or "")
    if tl and tl[1] and not re.search(r"테스트|세로|영상$", title or ""):
        C.append(_cand(tl[0], tl[1], 0, X if X in tl[0] else "", "", "title", 0.6, "영상 제목"))
    ctx = context_boost(body)
    for c in C:
        c["prior"] = round(c["prior"] + ctx.get(c["pid"], 0), 3)
    return C, tp


CONTEXT = (  # 영상 성격 → 잘 맞는 문구 틀에 더할 값
    (("공간", "오프더볼", "전술", "압박", "프레스", "전환", "움직임", "시야", "패스 앤 무브"), {"secret": 0.15, "levelup": 0.12, "grow": 0.05}),
    (("드리블", "드래그", "턴", "페인트", "터치", "개인기", "돌파", "스텝오버"), {"howto": 0.12, "must": 0.05, "easy": 0.1, "ox": 0.05}),
    (("슈팅", "슛", "골", "킥"), {"wow": 0.12, "must": 0.05, "line0": 0.05}),
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
    if tp and any(nospace(t) in nospace(text) for t in tp[:2]):
        s += 1.5
    elif tp and any(nospace(t) in nospace(c.get("sub", "")) for t in tp[:2]):
        s += 0.8  # 주제는 작은 줄에
    if c.get("emph"):
        ln, a, b = c["emph"]
        s += 0.6 if 1 <= len(nospace((c["l1"], c["l2"])[ln][a:b])) <= 5 else 0.2
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

def _sig(name):
    try:
        mt = int((core.adir(name) / "transcript.json").stat().st_mtime)
    except OSError:
        mt = 0
    return [mt, nice_title(name)]


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
    L = ["당신은 한국 풋살·축구 유튜브 썸네일 문구 전문가예요. 아래 영상의 썸네일에 크게 들어갈 2줄 헤드라인을 만들어 주세요.",
         "채널: '풋살사관학교'(최경진 감독의 풋살 레슨 채널). 시청자: 풋살 동호인·플랩(FLAB) 참가자.", "",
         f"[영상 제목] {title}", f"[주제 낱말] {', '.join(tp) or '없음'}", "[대사 요약 (앞부분)]", body[:2500], "[핵심 문장]"] + [f"- {s}" for s in key] + [
         "", "[잘 되는 문구 틀 — 레퍼런스 채널 예시 (낱말은 이 영상에 맞게 바꾸세요)]"] + [f"- {x}" for x in REF_EXAMPLES] + [
         "", "[규칙]", "- 한 줄은 띄어쓰기 빼고 3~9자 (많아야 12자), 두 줄 합쳐 20자 이내. 작은 화면(휴대폰 목록)에서도 읽혀야 해요.",
         "- 두 줄 중 한 낱말(1~5자)을 강조 낱말로 고르세요 (노랑·크게 칠할 곳). 강조 낱말은 l1 또는 l2 안에 글자 그대로 있어야 해요.",
         "- 영상 내용과 맞는 말만. 과장·낚시(충격·경악·실화냐·100%)는 쓰지 마세요. 해요체·반말 질문형 모두 좋아요.",
         "- sub 는 작은 보조 문구(선택, 12자 이내).",
         f"- 서로 다른 틀로 {AI_N}개.", "", "[대답 형식] JSON 배열만 (설명 없이):",
         '[{"l1": "첫 줄", "l2": "둘째 줄", "emph": "강조 낱말", "sub": "보조 문구"}]']
    return "\n".join(L)


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


def run_ai(name, log=print, cancel=None):
    """내 클로드 계정으로 문구 10개 → 검사해서 기억 → {"ok", "items"} (실패하면 규칙 문구만 쓰게 ok False + 안내)."""
    import claude_cli
    core.set_progress(label="클로드로 문구 만들기", item=name, pct=None, detail="내 클로드 계정 사용 · 문구 만드는 중")

    def tick(sec):
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
