"""NG 테이크·슬레이트 말·말더듬 찾기 — 받아쓴 대사 구간만 보고 규칙으로 판단 (AI 없이 PC에서 계산).
find_junk(segs, silences) → [(시작, 끝, 이유)] · editor.recommend 가 가편집·쇼츠 후보에서 이 구간을 뺌.
한 구간(segment) 안의 강조 반복('빠르게, 빠르게!')은 건드리지 않음 · 받아쓰기가 줄마다 나눈 구령·환호('하나, 둘, 셋!' · '나이스!')도 그대로 (is_chant).
find_fillers(segs) → 단어 시각이 있으면 말 사이에 홀로 떨어진 추임새 단어('음' · '어') 구간 (recommend 가 함께 뺌)."""
import difflib
import functools
import re

RETAKE_MIN, RETAKE_MAX = 3.0, 60.0  # 앞 테이크 시작 ~ 다시 찍은 테이크 시작 (초)
SIMILAR, MIN_CHARS = 0.7, 6         # 글자 비슷한 정도 · 이보다 짧은 말은 비교 안 함
LOOSE_MAX = 20                      # 두 테이크 사이에 '다시 한 말'로 설명 안 되는 글자가 이보다 많으면 다른 내용 (슬레이트 말이 없으면 0)
COVER = 0.8                         # 슬레이트 말이 없으면 뒤 테이크가 앞 테이크 말을 이만큼 이상 다시 해야 함 (나란한 설명은 그대로)
QUIET_MAX = 15.0                    # 두 테이크 사이에 말 없이 이보다 길게 비면(시범 장면일 수 있음) 슬레이트 말이 있을 때만
DEMO_MIN = 4.0                      # 두 테이크 사이 말 없는 곳이 이보다 길고 그 안에 큰 소리 봉우리(공 차는 소리·환호)가 있으면 시범
DEMO_LONG = 6.0                     # 봉우리가 없으면: 조용하지 않은데 말이 없는 곳이 이보다 길어야 시범 (체육관·운동장은 늘 시끄러워서 3~5초 숨 고르기와 구별)
SLATE_SIL = 1.0                     # 슬레이트 말 앞의 이만큼 이상 조용한 곳부터 지움
STUTTER_GAP, STUTTER_MAX = 1.0, 10  # 말더듬: 이 초 안에 다시 시작한, 이 글자 이하의 짧은 말
CUTOFF_MAX = 0.6                    # 끊긴 첫마디로 볼 최대 길이 (초)
# 테이크 사이에 있어도 '다른 내용'으로 안 치는 고쳐 말하기 ('아 아니다', '그게 아니라' …)
FIX_WORDS = re.compile(r"(?:아니(?:다|야|지|고|라)?|(?:그게|이게)아니(?:라|고|야)?|잠깐|잠시|뭐더라|어떡하지)+")
UNDONE = 0.85                       # 슬레이트 말이 없으면 앞 테이크가 끊긴 말이어야 함: 뒤 테이크의 이 비율 이하 길이 (또는 끝 머뭇거림·뒤 말의 앞부분·같은 말)
# 두 테이크에서 바뀐 낱말이 이런 말이면 '안 되는 예 → 되는 예' 같은 대조 설명 (부정·숫자·방향)
CONTRAST = re.compile(r"안|못|않\w*|안(?:돼|되|됩|해|하|좋)\w*|못(?:해|하|돼|되)\w*|아니\w*|말고\w*|\w*지마\w*"
                      r"|오른\w*|왼\w*|안쪽\w*|바깥\w*|앞\w*|뒤\w*|[한두세네]|[한두세네]번\w*|첫\w*|둘\w*|셋\w*|넷\w*|\d\w*")

# (패턴, 강함) — 강한 말은 남는 글자 3자까지('요' 등), 약한 말('처음부터', '잠깐만')은 2자까지일 때만 슬레이트로 봄
SLATE = [(re.compile(p, re.I), strong) for p, strong in (
    (r"처음부터\s*(?:다시\s*(?:(?:할|갈|찍을|해\s*볼|가\s*볼)\s*(?:게|께)|(?:하|가|찍|해\s*보|가\s*보)\s*겠습|(?:하|가|찍)자)?"
     r"|(?:할|갈)\s*(?:게|께)|(?:하|가)\s*겠습|(?:하|가)자)", True),
    (r"다시\s*(?:한\s*번\s*)?(?:할|갈|찍을|해\s*볼|가\s*볼)\s*(?:게|께)", True),
    (r"다시\s*(?:한\s*번\s*)?(?:하|가|찍|해\s*보|가\s*보)\s*겠습", True),  # 존댓말 ('다시 하겠습니다' — '니다'는 남는 글자로 셈)
    (r"다시\s*(?:하|가|찍)자", True),
    (r"^[\s,.!?~…]*(?:(?:아|앗|어)[\s,.!?~…]*다시|다시요)[\s,.!?~…]*$", True),  # 한 줄 전체가 '아 다시' · '다시요'
    (r"(?<![a-z])ng(?![a-z])", True),
    (r"엔지(?!니)", True),
    (r"(?:^|(?<=[\s,.!?~…]))(?:아|앗|아이)\s*틀렸", True),
    (r"말이\s*꼬", True),
    (r"잘못\s*말했", True),
    (r"처음부터", False),
    (r"잠깐만|잠시만", False),
)]
# 시범을 부르는 '다시 해 볼게요' · '다시 한번 갈게요' (한 번 더 해 보는 말 · 실수 말·'아 다시'·'찍'·'처음부터' 없이) —
# 바로 뒤에 시범(공 소리·말 없는 긴 장면)이 오면 슬레이트가 아님 (E12 · I-100) · '다시 할게요' · '아 다시 갈게요'는 그대로 슬레이트
RETRY = re.compile(r"다시\s*(?:한\s*번\s*)?(?:해\s*볼|가\s*볼|해\s*보겠|가\s*보겠)|다시\s*한\s*번\s*(?:할|갈|하겠|가겠|가\s*볼|해\s*볼)")
NOT_RETRY = re.compile(r"틀렸|꼬였|꼬여|잘못|처음부터|엔지|(?<![a-z])ng(?![a-z])|찍|아니다|아니야|아닌데|아차|아이고|^\W*(?:아|앗|어)\W", re.I)
SORRY = {"죄송합니다", "죄송해요", "죄송", "미안합니다", "미안해요", "미안"}
# 슬레이트 말에 붙은 실수 말 ('아 이거 아닌데 다시 할게요') — 남는 글자로 안 셈
OOPS = re.compile(r"(?:이거|이게|그게)?(?:아닌데|아니네|아니다|아니야|아니지)|아이고|아이구|어이쿠|아차|이런")
# 구령·리듬 말·환호 — 받아쓰기가 줄마다 나눠도 말더듬이 아님 ('하나, 둘, 셋!' · '왼발, 오른발' · '나이스! 나이스!' · '골!')
# 숫자 세기·소리 흉내·환호는 그 낱말만으로 구령 (문장 첫마디로 잘 안 씀)
CHANT_STRONG = (
    "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "원", "투", "쓰리", "포", "파이브",  # ('일, 이, 삼'은 '이'·'사' 같은 흔한 말과 겹쳐 뺌 · 숫자로 받아쓴 '1, 2, 3'은 됨)
    "탁", "톡", "툭", "퉁", "쿵", "짝", "착", "팡", "뻥",
    "나이스", "나이스샷", "좋아", "좋다", "좋습니다", "굿", "굳", "오케이", "오케", "예스", "골", "고올", "그렇지", "그렇죠", "그거지", "그거죠",
    "잘했어", "잘한다", "잘하네", "우와", "브라보", "대박", "최고", "화이팅", "파이팅", "가자", "가즈아", "들어갔다", "들어갔어",
    "됐다", "됐어", "완벽", "완벽해", "멋지다", "멋있다", "퍼펙트", "박수")
# 방향·동작·재촉 말은 문장 첫마디로도 흔해서('앞으로' → '앞으로 나가면서 …' · '패스' → '패스할 때는 …') 그 낱말만으로는 구령이 아님:
# 서로 다른 두 낱말이 이어지거나('왼발, 오른발' · '안쪽, 바깥쪽') 외친 줄('빠르게!' · '패스!')일 때만
CHANT_WEAK = (
    "왼발", "오른발", "왼쪽", "오른쪽", "안쪽", "바깥쪽", "인사이드", "아웃사이드", "앞", "뒤", "옆", "위", "아래", "앞으로", "뒤로", "옆으로",
    "스텝", "점프", "터치", "원터치", "투터치",
    "빠르게", "천천히", "강하게", "세게", "약하게", "빨리", "계속", "더", "멈춰", "스톱", "턴", "돌아", "패스", "슛", "슈팅")
CHANT_WORDS = CHANT_STRONG + CHANT_WEAK
_CHANT_TOK = re.compile("(?:%s)" % "|".join(sorted(map(re.escape, CHANT_WORDS), key=len, reverse=True)))
_CHANT = re.compile(r"(?:%s)+요?" % _CHANT_TOK.pattern)
SHOUT = re.compile(r"!\s*$")
# 다 말한 테이크인지 볼 때 '끊긴·더듬은' 표시로 보는 머뭇거림 (앞뒤에 붙은 '자', '네', '이제' 같은 말버릇은 괜찮음)
HESITATE = {"아", "어", "음", "그", "으", "엄", "흠", "뭐", "저기", "아니", "그러니까", "그니까"}
LEAD_TAIL = {"자", "네", "예", "응", "이제", "그냥", "막"}


def _ed():
    import editor  # editor 가 이 파일을 쓰므로 필요할 때 가져옴 (_norm·FILLERS 를 똑같이 씀)
    return editor


def _words(text, ed):
    """앞쪽 추임새('자', '아' …)를 뗀 낱말들."""
    words = text.split()
    while words and (ed._norm(words[0]) in ed.FILLERS or not ed._norm(words[0])):
        words.pop(0)
    return words


def _core(text, ed):
    """앞쪽 추임새를 뗀 정규화 글자."""
    return ed._norm(" ".join(_words(text, ed)))


def _trimmed(text, ed):
    """끝의 머뭇거림('… 어 그러니까', '… 아 아니')까지 뗀 정규화 글자 — 끊긴 테이크를 얼마나 다시 했는지 볼 때."""
    words = _words(text, ed)
    while words and (not ed._norm(words[-1]) or ed._norm(words[-1]) in ed.FILLERS | {"그러니까", "그니까"}
                     or FIX_WORDS.fullmatch(ed._norm(words[-1]))):
        words.pop()
    return ed._norm(" ".join(words))


def _resaid(a, b):
    """앞 테이크 말(a) 중 뒤 테이크(b)에서 다시 한 글자의 비율."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in sm.get_matching_blocks()) / max(1, len(a))


@functools.lru_cache(maxsize=1 << 16)  # 겹치는 범위를 여러 번 비교하므로 기억해 둠
def _similar(a, b, cut=SIMILAR):
    """같은 말로 시작하고(첫 두 글자) 앞 8~15자나 전체가 cut 이상 비슷하면 같은 말."""
    if min(len(a), len(b)) < MIN_CHARS or a[:2] != b[:2]:
        return False
    n = max(8, min(15, len(a), len(b)))
    for x, y in ((a[:n], b[:n]), (a, b)):
        sm = difflib.SequenceMatcher(None, x, y, autojunk=False)
        if sm.real_quick_ratio() >= cut and sm.quick_ratio() >= cut and sm.ratio() >= cut:
            return True
    return False


def is_slate(text, ed=None):
    """'아 다시 할게요' · '잠깐만요' · 'NG' 같은 촬영용 말인지 (다른 내용이 거의 없을 때만)."""
    ed = ed or _ed()
    rest, hit, strong = text, False, False
    for rx, st in SLATE:
        if rx.search(rest):
            hit, strong = True, strong or st
            rest = rx.sub(" ", rest)
    if not hit:
        return False
    left = OOPS.sub("", "".join(w for w in (ed._norm(x) for x in rest.split()) if w not in ed.FILLERS and w not in SORRY))
    return len(left) <= (3 if strong else 2)


def is_chant(text, ed=None):
    """한 줄 전체가 숫자 세기·리듬 말·짧은 구령·환호뿐인지 ('하나, 둘, 셋!' · '왼발, 오른발,' · '나이스!' · '좋아요.' · '골!').
    받아쓰기가 이런 말을 줄마다 나눠도 말더듬·군더더기로 지우지 않으려고 씀 (앞쪽·사이 추임새는 빼고 봄).
    방향·동작 말(CHANT_WEAK)만 있으면 서로 다른 두 낱말이 있거나 외친 줄('!')일 때만 ('앞으로' · '패스' 혼자는 문장 첫마디일 수 있음)."""
    ed = ed or _ed()
    words = [w for w in (ed._norm(x) for x in str(text or "").split()) if w and w not in ed.FILLERS]
    if not words or not all(_CHANT.fullmatch(w) or w.isdigit() for w in words):
        return False
    toks = {t for w in words for t in _CHANT_TOK.findall(w)}
    return (any(w.isdigit() for w in words) or bool(toks & set(CHANT_STRONG)) or len(toks) >= 2
            or bool(SHOUT.search(str(text or ""))))


def repeat_ok(a, b, ed=None):
    """줄마다 나뉜 같은 말 a → b 가 일부러 한 반복인지: 둘 다 구령·환호이거나, 짧은 말을 외치며 다시 함('빠르게,' → '빠르게!').
    똑같이 외친 말 둘('그러니까!' '그러니까!')·긴 문장('오늘은 슈팅 챌린지예요!' 두 번)은 반복으로 봄 (Whisper 가 '!'를 흔히 붙임)."""
    ed = ed or _ed()
    if is_chant(a, ed) and is_chant(b, ed):
        return True
    a, b = str(a or ""), str(b or "")
    return (len(_core(a, ed)) <= STUTTER_MAX and len(_core(b, ed)) <= STUTTER_MAX
            and bool(SHOUT.search(a)) != bool(SHOUT.search(b)))


def _plain(text, ed):
    """추임새를 모두 빼고 끝 머뭇거림도 뗀 정규화 낱말들."""
    words = [w for w in (ed._norm(x) for x in text.split()) if w and w not in ed.FILLERS]
    while words and (words[-1] in {"그러니까", "그니까"} or FIX_WORDS.fullmatch(words[-1])):
        words.pop()
    return words


def _contrast(a, b):
    """슬레이트 말 없는 두 줄이 대조 설명인지: 앞 줄이 끊긴 말이 아니거나(같은 길이로 다 말함), 가운데 바뀐 낱말이 부정·숫자·방향."""
    ed = _ed()
    wa, wb = _plain(a, ed), _plain(b, ed)
    ea, eb = "".join(wa), "".join(wb)
    if not eb.startswith(ea) and _trimmed(a, ed) == _core(a, ed) and len(ea) > UNDONE * len(eb):  # 이어 말하다 끊긴 것도 아님
        return True
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if tag == "equal" or (i2 == len(wa) and j2 == len(wb)):  # 끝에서 이어 말한 부분은 새 내용
            continue
        if any(CONTRAST.fullmatch(w) for w in wa[i1:i2] + wb[j1:j2]):
            return True
    return False


def _finished(a, b, ed):
    """앞 테이크 a 를 다 말했는지: 머뭇거림('어' · '음' · 끝의 '그러니까')·고쳐 말하기 없이 뒤 테이크 b 만큼(UNDONE) 말함 — 끊긴·더듬은 테이크가 아님.
    앞뒤에 붙은 말버릇('자, …' · '…, 네.')은 다 말한 테이크로 봄 (코치가 거의 모든 말을 '자,'로 시작함)."""
    words = [w for w in (ed._norm(x) for x in str(a or "").split()) if w]
    while words and (words[0] in ed.FILLERS or words[0] in HESITATE):  # 앞쪽 추임새는 무엇이든
        words.pop(0)
    while words and words[-1] in LEAD_TAIL:  # 끝의 말버릇 ('네.')
        words.pop()
    if not words or any(w in HESITATE or FIX_WORDS.fullmatch(w) for w in words):
        return False
    return len("".join(_plain(a, ed))) > UNDONE * len("".join(_plain(b, ed)))


def _demo_between(segs, i, j, silences, peaks):
    """i 끝 ~ j 시작 사이 말 없는 곳에 시범이 있는지: DEMO_MIN 초 넘게 말이 없고 그 안에 큰 소리 봉우리(공 차는 소리·환호 —
    1초 창의 시작 시각, 창이 말 사이에 다 들어감)가 있거나, 조용하지 않은데 말이 없는 곳이 DEMO_LONG 초 넘게 이어짐
    (조용한 곳 정보가 없으면 빈 곳 길이 그대로). 짧은 빈 곳의 큰 소리(카메라에 공이 맞음 등)는 다시 찍는 이유일 수도 있어 안 봄."""
    for k in range(i, j):
        a, b = segs[k]["end"], segs[k + 1]["start"]
        if b - a <= 0:
            continue
        if b - a > DEMO_MIN and any(a <= p and p + 1.0 <= b for p in peaks):
            return True
        quiet = sum(max(0.0, min(b, x["end"]) - max(a, x["start"])) for x in silences)
        if b - a - quiet > DEMO_LONG:
            return True
    return False


def _retake_ok(segs, cores, slates, i, j, silences=(), peaks=()):
    """i(앞 테이크) ~ j(다시 찍은 테이크) 사이가 정말 NG 인지: 사이의 말이 다시 한 말·슬레이트·추임새뿐이고, 오래 비지 않음.
    슬레이트 말이 없으면 더 엄격히: 사이에 다른 말이 하나도 없고 뒤 테이크가 앞 말을 거의 다 다시 함 (같은 말로 감싼 팁·나란한 설명은 그대로).
    슬레이트 말 없이 다 말한 앞 테이크 뒤에 시범(큰 소리·말 없는 긴 장면)이 있으면 '같은 설명 → 시범 → 같은 설명' 드릴이라 NG 아님."""
    ed = _ed()
    slate = any(slates[i + 1:j])
    if not slate and (_resaid(_trimmed(segs[i]["text"], ed), cores[j]) < COVER
                      or _contrast(segs[i]["text"], segs[j]["text"])):
        return False
    if not slate and _finished(segs[i]["text"], segs[j]["text"], ed) and _demo_between(segs, i, j, silences, peaks):
        return False
    win, loose = range(j, min(len(segs), j + (j - i) + 2)), 0
    for k in range(i + 1, j):
        if len(cores[k]) > 1 and not slates[k] and not FIX_WORDS.fullmatch(cores[k]) \
                and not any(_similar(cores[k], cores[w], 0.6) for w in win):
            loose += len(cores[k])
            if loose > (LOOSE_MAX if slate else 0):
                return False
    quiet = max(segs[k + 1]["start"] - segs[k]["end"] for k in range(i, j))
    return quiet <= QUIET_MAX or slate


def find_junk(segs, silences=(), peaks=()):
    """받아쓴 구간 → 지울 구간 [(a, b, 이유)] (a 순서, 다른 구간 안에 다 들어가는 건 뺌).
    · 다시 찍기: 3~60초 앞에 같은 말로 시작한 구간이 있으면 [앞 구간 시작, 지금 구간 시작)
      (슬레이트 말 없이 다 말한 설명 사이에 시범이 있으면 빼지 않음 · peaks: 큰 소리 봉우리 시각 — analysis.json loud_peaks)
    · 슬레이트 말: 바로 앞 1초 이상 조용한 곳(없으면 그 구간 시작)부터 그 구간 끝까지
    · 말더듬: 같은 짧은 말(또는 끊긴 첫마디)을 1초 안에 다시 하면 마지막 것만 남김 (구령·환호·외친 말은 그대로)"""
    ed = _ed()
    segs = sorted((s for s in segs if str(s.get("text") or "").strip()), key=lambda s: (s["start"], s["end"]))
    cores = [_core(s["text"], ed) for s in segs]
    slates = [is_slate(s["text"], ed) for s in segs]
    chants = [is_chant(s["text"], ed) for s in segs]
    sil = [x for x in silences or () if isinstance(x, dict) and x.get("end", 0) > x.get("start", 0)]
    pk = [float(p["time"] if isinstance(p, dict) else p) for p in peaks or ()]
    for k in range(len(segs) - 1):  # '다시 해 볼게요.' → 시범 → '나이스!' : 시범을 부르는 말 (슬레이트 아님 · 문장 단위로 나누면 따로 떨어짐)
        if slates[k] and RETRY.search(segs[k]["text"]) and not NOT_RETRY.search(segs[k]["text"]) and _demo_between(segs, k, k + 1, sil, pk):
            slates[k] = False
    out, pairs = [], []

    for j, later in enumerate(segs):  # 다시 찍은 앞 테이크
        if len(cores[j]) < MIN_CHARS or slates[j] or chants[j]:  # 슬레이트 말끼리·구령끼리는 테이크 기준으로 안 씀
            continue
        for i in range(j - 1, -1, -1):
            d = later["start"] - segs[i]["start"]
            if d > RETAKE_MAX:
                break
            if d >= RETAKE_MIN and not slates[i] and not chants[i] and _similar(cores[i], cores[j]):
                if _retake_ok(segs, cores, slates, i, j, sil, pk):
                    pairs.append((i, j))
                break
    keep = []  # 앞 테이크 순서로 고르되, 고른 테이크를 가로지르는 짝(여러 줄을 다시 찍었을 때 둘째 줄끼리)은 버림 · 안에 든 짝은 그대로
    for i, j in sorted(pairs):
        if not any(i2 < i < j2 < j for i2, j2 in keep):
            keep.append((i, j))
    out += [(segs[i]["start"], segs[j]["start"], "다시 찍은 앞 테이크") for i, j in keep]

    quiet = [x for x in silences or () if x["end"] - x["start"] >= SLATE_SIL]
    for k, s in enumerate(segs):  # 슬레이트 말
        if not slates[k]:
            continue
        lo = segs[k - 1]["end"] if k else 0.0
        pre = [x for x in quiet if x["start"] < s["start"] and x["end"] > lo]
        a = min(max(max(x["start"] for x in pre), lo), s["start"]) if pre else s["start"]  # 앞 말은 건드리지 않음
        out.append((a, s["end"], "슬레이트 말"))

    for k in range(len(segs) - 1):  # 말더듬 (구간 단위)
        a, b = cores[k], cores[k + 1]
        if not a or len(a) > STUTTER_MAX or segs[k + 1]["start"] - segs[k]["end"] > STUTTER_GAP:
            continue
        same = a == b or (len(a) == len(b) and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.8)
        same = same and not repeat_ok(segs[k]["text"], segs[k + 1]["text"], ed)  # 구령·환호·외친 말을 줄마다 나눈 것은 그대로
        # 끊긴 첫마디('퍼스트' → '퍼스트 터치는 발 안쪽으로 받아요')도 말더듬 · 한 마디씩 늘려 가는 말('받고' → '받고 돌고')은 그대로
        wa, wb = len(_words(segs[k]["text"], ed)), len(_words(segs[k + 1]["text"], ed))
        # 끊긴 첫마디는 짧고(0.6초 미만) 문장부호 없이 끝남 ('포인트!' → '포인트는 …' 같은 강조·감탄은 그대로)
        cut_off = (wa == 1 and len(a) <= 4 and wb - wa >= 3 and b.startswith(a) and segs[k]["end"] - segs[k]["start"] < CUTOFF_MAX
                   and not re.search(r"[.!?~…]\s*$", segs[k]["text"]))
        if same or cut_off:
            out.append((segs[k]["start"], segs[k + 1]["start"], "말더듬"))

    res = []
    for a, b, why in sorted({(round(a, 2), round(b, 2), w) for a, b, w in out if b > a}, key=lambda x: (x[0], -x[1])):
        if not any(x <= a and b <= y for x, y, _ in res):
            res.append((a, b, why))
    return res


# ---------- 단어 단위 추임새 ('음' · '어' …) — 받아쓰기에 단어 시각(words)이 있을 때만 ----------
FILLER_WORDS = {"음", "어", "아", "그", "으", "엄", "흠", "에"}  # 늘인 소리('어어', '음~')도 같은 말로 봄
FILLER_GAP = 0.12  # 앞뒤로 이만큼 이상 비어 홀로 떨어진 것만 ('그 공을'처럼 말에 붙은 '그'는 그대로)


def find_fillers(segs):
    """단어 시각이 있는 받아쓰기 → 홀로 떨어진 추임새 단어 구간 [(a, b, '추임새')] (단어 시각이 없으면 []).
    단어 앞뒤 빈 곳의 절반(0.1초까지)도 함께 빼서, 남은 말 사이가 자연스럽게 이어지게 함."""
    words = [w for s in segs or () for w in s.get("words") or () if str(w.get("w") or "").strip()]
    words.sort(key=lambda w: w["s"])
    out = []
    for k, w in enumerate(words):
        t = re.sub(r"(.)\1+", r"\1", re.sub(r"[\s.,!?~…·\-]+", "", w["w"]))
        if t not in FILLER_WORDS:
            continue
        before = w["s"] - words[k - 1]["e"] if k else float("inf")
        after = words[k + 1]["s"] - w["e"] if k + 1 < len(words) else float("inf")
        if before >= FILLER_GAP and after >= FILLER_GAP:
            out.append((round(max(0.0, w["s"] - min(0.1, before / 2)), 2), round(w["e"] + min(0.1, after / 2), 2), "추임새"))
    return out


# ---------- 영상 밖 말: 촬영 준비 말 · 촬영 끝 말 · 구독/홍보 안내 (E12 · D-100 · BR-060) ----------
# 문장 단위(captions.split_sentences)로 봄. 가편집·티저는 첫 인사 앞·마지막 끝인사 뒤를 자르고, 쇼츠는 안내 말까지 모두 뺌 (롱폼 중간 안내 말은 표시만).
PRE, POST, CTA, PROMO = "촬영 준비 말", "촬영 끝 말", "영상 밖 안내 말", "영상 밖 안내 말"
PRE_MAX = 90.0       # 촬영 준비 말은 첫 인사 앞, 이 초 안에서만 찾음
PRE_LEN = 16         # 촬영 준비 말은 짧은 줄(띄어쓰기·부호 뺀 글자 수)이거나 묻는 말('…맞아?')만 — 긴 줄은 훅일 수 있음
POST_PAD = 0.5       # 촬영 끝 말은 마지막 끝인사 끝 이만큼 뒤부터 자름 (받아쓰기 낱말 끝 시각은 소리보다 이르고 다음 말 시각은 당겨 써질 때가 있음)
POST_SHORT = 6.0     # 끝인사 뒤 말이 촬영 끝 낱말 없이 이 초 이하 대답·인사뿐이면 그것도 촬영 끝 말
NEAR = 3.0           # 안내 말 바로 옆(이 초 안)에 이어지는 말도 같은 안내 말로 봄
# 첫 인사·주제 소개 (plan.GREETING 과 같은 갈래 · '오늘은 … 알려 드릴게요/해 볼게요')
OPENING = re.compile(r"안녕하세요|안녕하십니까|반갑습니다|(?:저는|제가|여기는)\s?.{1,12}(?:입니다|이에요|예요)|\S{2,10}의\s?\S{2,6}입니다|"
                     r"사관학교\s?\S*입니다|최경진입니다|오늘은\s.{2,40}(?:알려\s?드|배워\s?(?:볼|보)|해\s?(?:볼|보)|연습|알아\s?(?:볼|보))")
# 촬영 준비 말: 녹화·찍힘·빨간불·마이크·'됐어?'(그 말뿐인 줄만 — '이게 왜 안 되죠?' 같은 훅은 아님)·'레디 액션'·'슛 들어갈게요'(그 말뿐인 줄)
# 축구 말과 겹치는 낱말만으로는 아님 ('페이크 액션' · '슛 들어가는 타이밍' · '공이 찍히는 각도' · '하나 둘 셋 하면 뛰어요')
PREROLL = re.compile(r"녹화|빨간\s?불|녹음\s?(?:되|돼|중)|레코딩|찍히고\s?있|찍히는\s?(?:거|중)|찍혀(?:요|\s?있|\W*$)|잘\s?찍|찍고\s?있|찍는\s?(?:중|거\s?맞)|"
                     r"카메라\s?(?:켜|돌|됐|돼|켰|괜찮)|마이크\s?(?:켜|됐|돼|테스트|체크|괜찮)|소리\s?(?:들어가|잘\s?들|나와)|"
                     r"레디\s?(?:액션|고|큐)|큐\s?(?:사인|줄게|들어|주세요)|"
                     r"^\W*(?:(?:자|네|그럼|이제)\W*)?슛\s?(?:들어갈게요|들어갑니다|들어가요|들어간다|갈게요|갑니다)\W*$|하나\s*,?\s*둘\s*,?\s*셋\W*(?:큐|액션)", re.I)
# '됐어?' · '준비 됐지?' (그 말뿐인 줄) — 존댓말('준비 됐나요?' · '됐어요?')은 시청자에게 하는 말일 수 있어 바로 뒤에 대답('네')이 올 때만
READY_ASK = re.compile(r"^\W*(?:(?:준비|이제|다|지금)\s*)?(?:됐(?:어|나|지|니|죠|어요|나요)?|돼(?:요)?)\W*\?\W*$")
# 짧은 대답 ('네' · '네, 좋아요' · '오케이') — 준비 말·끝 말 사이에 끼면 같이 자름
REPLY = re.compile(r"^(?:네|예|넵|응|어|오케이|ok|좋아요|좋습니다|알겠어요|알겠습니다|그래요|됐어요|돼요|네네|그럼요)(?:\s+(?:네|좋아요|좋습니다|됐어요|돼요|알겠어요))*$", re.I)
# 끝인사
CLOSING = re.compile(r"오늘은\s?여기까지|여기까지\s?(?:입니다|할게요|하겠습니다|예요)|다음\s?(?:영상|시간|편|주)(?:에서|에|으로)?\s?(?:또\s?)?(?:만나|봬|뵙|뵐|찾아)|"
                     r"시청해\s?주셔서|봐\s?주셔서|감사합니다|고맙습니다|안녕히\s?(?:계세요|가세요)|다음에\s?(?:또\s?)?(?:만나|봬|뵐|뵙)")
# 촬영 끝 말 — 확실한 말: 외친 '컷'(그 말로 시작하거나 짧은 줄 · 축구 말 '컷백'·'컷 백'·'인사이드 컷'·'컷 드리블'은 아님) · '한 번 더 찍을까' ·
# '녹화 끝' · '카메라 꺼' · '물 좀 마시고' / 약한 말: '수고했어요' · '고생했어요' · '촬영 끝났어요' (영상 끝인사로도 써서, 뒤에 끝인사가 또 오면 안 자름)
POST_STRONG = re.compile(r"^\W*(?:(?:자|오케이|좋아|좋아요|네|예|그래|됐어|됐다)\W*)?컷(?:\W*컷)*(?:[\s,.!?~]|$)|"
                         r"(?:한\s?번|다시)\s?더?\s?찍(?:을까|을게|자|읍시다|어야|을\s?(?:까요|게요))|다시\s?찍(?:을까|을게|자|읍시다|어야|을\s?(?:까요|게요))|"
                         r"녹화\s?(?:끝|꺼|멈|그만)|카메라\s?(?:꺼|끄)|물\s?(?:좀\s?)?마시|쉬었다\s?(?:할|갈|찍)|쉬고\s?(?:할|갈|다시)|끊을게|끊어\s?(?:주|요)", re.I)
POST_WEAK = re.compile(r"수고|고생\s?(?:했|하셨|많)|(?:촬영|녹화)\s?(?:이\s?)?(?:다\s?)?끝났")
POST_CALL = 10       # '컷'은 줄이 이 글자 이하로 짧거나 '컷'으로 시작할 때만 촬영 끝 말 ('자 컷 수고했어요' · '컷!')
# 구독·좋아요·알림 부탁 ('좋아요'만으로는 아님 — '좋아요!' · '좋아요 한 번 더 해 볼게요'는 칭찬 · '구독자 여러분'은 인사) ·
# 설명란·고정 댓글 안내도 영상 밖 말 (키트 설명에는 안 넣음)
CTA_RX = re.compile(r"구독(?!자)|좋아요[^.!?]{0,12}(?:눌러|부탁|꾹|버튼)|(?:눌러|꾹)[^.!?]{0,8}좋아요|알림\s?(?:설정|버튼|까지)|"
                    r"댓글\s?(?:로\s?)?(?:남겨|달아|많이)|(?:설명란|고정\s?댓글|더보기란)\S*\s?(?:에\s?)?(?:링크|참고|확인|남겨|적어|걸어)")
# 레슨·수강 홍보 — 연락할 곳(인스타 DM·카톡·프로필 링크)이나 '레슨 문의·수강 신청'에 부탁·안내 꼴('주세요' · '주시면' · '신청 받아요')이 함께 있을 때만
# ('인스타 DM으로 많이 물어보셔서' · '레슨 받는 친구들이' · '카톡으로 영상 보내 주신 분도' · '문의하신 분이 있어서'는 레슨 내용)
CHANNEL = re.compile(r"인스타(?:그램)?|디엠|(?<![A-Za-z])dm(?![A-Za-z])|카톡|카카오\s?(?:톡|채널)|오픈\s?채팅|프로필\s?(?:에\s?)?링크|이메일|메일로|문자로|전화로", re.I)
LESSON_ASK = re.compile(r"(?:레슨|수강|강습|클래스|수업|원데이)\s?(?:문의|신청|상담|등록)(?!하신|한\s|했|하셨|하는\s?(?:친구|분))|문의\s?(?:는|사항은?|하실\s?분)")
REQUEST = re.compile(r"주세요|주시면|주셔도|주십시오|주시길|남겨\s?주|연락\s?(?:주|하세요|하시면|드리)|(?:신청|문의|등록)\s?(?:하세요|하시면|해\s?주|받|가능)|"
                     r"하시면\s?(?:돼|됩)|(?:으|)로\s?(?:주|와|오)세요|오세요|모집\s?(?:중|합니다|해요)")
PROMO_PAST = re.compile(r"물어보셔서|보내\s?주신|문의하신|신청하신|받는\s?(?:친구|분)|하셨던|주셨던|주셔서\s?(?:감사|고맙)")
PROMO_WEAK = re.compile(r"(?:주말|평일|저녁|오전|오후|성인|유소년|초등|원데이)\s?(?:반|클래스|수업)|자리\s?(?:있|남|없)|모집|신청\s?(?:받|하)|마감|등록")
CONT = re.compile(r"^(?:한\s?번씩\s?)?(?:눌러|꾹|부탁|해\s?주|해주|남겨|구독)")  # 앞 안내 말에 이어지는 말 ('구독이랑 좋아요' / '한 번씩 눌러 주시고요')


def _reply(t):
    return bool(REPLY.match(re.sub(r"[.,!?~…]+", "", str(t or "")).strip()))


def _plain_t(t):
    return re.sub(r"[\s.,!?~…]+", "", str(t or ""))


def is_promo(t):
    """레슨·수강 홍보 말 (부탁·안내 꼴이 있는 줄만 · 지난 일·남 이야기는 아님)."""
    t = str(t or "")
    if PROMO_PAST.search(t) and not re.search(r"주세요|주시면|오세요", t):
        return False
    return bool((CHANNEL.search(t) or LESSON_ASK.search(t)) and REQUEST.search(t))


def _postroll(t, strong=True):
    t = str(t or "")
    if not strong:
        return bool(POST_WEAK.search(t))
    m = POST_STRONG.search(t)
    if not m:
        return False
    if "컷" in m.group(0) and not m.group(0).lstrip("!?.,~ ").startswith(("한", "다시")):  # 외친 '컷': 짧은 줄 · '컷'으로 시작
        return len(_plain_t(t)) <= POST_CALL or bool(re.match(r"^\W*(?:(?:자|오케이|좋아|좋아요|네|예|그래|됐어|됐다)\W*)?컷(?:\W*컷)*(?:[,.!?~]|\s+(?:수고|고생|오케이|좋아|됐|네|다시|한\s?번|끝)|$)", t))
    return True


def _span(sents, k, lo=None, hi=None):
    """문장 k 를 뺄 구간: 앞뒤 말과의 빈 곳 절반(0.3초까지)을 함께."""
    s = sents[k]
    a = float(s["start"]) - min(0.3, max(0.0, (float(s["start"]) - float(sents[k - 1]["end"])) / 2) if k else 0.3)
    b = float(s["end"]) + min(0.3, max(0.0, (float(sents[k + 1]["start"]) - float(s["end"])) / 2) if k + 1 < len(sents) else 0.3)
    return round(max(0.0, a if lo is None else lo), 2), round(b if hi is None else hi, 2)


def _clean(sents):
    return sorted((s for s in sents or () if _plain_t(s.get("text"))), key=lambda s: (float(s["start"]), float(s["end"])))


def notice_kinds(sents, lo=0, hi=None):
    """문장들(시간 순) → {번호: 'cta' | 'promo'} — 구독·좋아요 부탁 / 레슨 홍보 말과 바로 옆(3초 안)에 이어진 말
    ('한 번씩 눌러 주시고요' · '주말반 아직 자리 있어요'). lo~hi 번 문장만 봄."""
    hi = len(sents) if hi is None else hi
    kinds = {}
    for i in range(lo, hi):
        t = str(sents[i]["text"])
        if CTA_RX.search(t):
            kinds[i] = "cta"
        elif is_promo(t):
            kinds[i] = "promo"
    for i in sorted(kinds):
        for j in (i - 1, i + 1):
            if not (lo <= j < hi) or j in kinds:
                continue
            gap = float(sents[j]["start"]) - float(sents[i]["end"]) if j > i else float(sents[i]["start"]) - float(sents[j]["end"])
            if gap <= NEAR:
                t = str(sents[j]["text"]).strip()
                if (kinds[i] == "cta" and j > i and CONT.match(t)) or (kinds[i] == "promo" and PROMO_WEAK.search(t)):
                    kinds[j] = kinds[i]
    return kinds


def _outro(t):
    """끝 무렵 마무리 묶음에 드는 말인지: 끝인사·구독/홍보 안내·짧은 대답·촬영 끝 말 (레슨 내용이 아닌 말)."""
    return bool(CLOSING.search(t) or CTA_RX.search(t) or is_promo(t) or PROMO_WEAK.search(t) or _reply(t)
                or _postroll(t) or _postroll(t, False))


def find_offscript(sents, dur=None):
    """문장들 → 영상 밖 말 [{a, b, why, kind, text}] (a 순서).
    kind: pre(첫 인사·주제 소개 앞의 촬영 준비 말과 대답 — 0초부터 그 말 끝(0.3초 여유)까지) · post(마지막 끝인사 뒤 촬영 끝 말 — 영상 끝까지) ·
    cta(구독·좋아요 부탁) · promo(레슨·인스타 홍보). 훅처럼 내용이 있는 말이 나오면 준비 말 찾기는 거기서 멈춤.
    촬영 끝 말은 끝에서부터 거꾸로: 마지막 레슨 내용 문장 뒤의 마무리 묶음(끝인사·안내·대답·촬영 끝 말)에서
    첫 촬영 끝 말 바로 앞의 마지막 끝인사 뒤만 (덤 설명·두 번째 끝인사·도움 준 사람에게 한 '감사합니다' 뒤 레슨은 그대로)."""
    sents = _clean(sents)
    if not sents:
        return []
    out = []
    # 1) 촬영 준비 말 (짧은 줄·묻는 말만 · 강조어·기술 말이 든 줄에서 멈춤)
    kw = [k for k in _ed().KEYWORDS if k != "?"]
    k, hit = 0, False
    while k < len(sents) and float(sents[k]["start"]) < PRE_MAX:
        t = str(sents[k]["text"])
        if OPENING.search(t) or any(w in t for w in kw):
            break
        polite = bool(re.search(r"(?:요|죠)\W*$", t))
        if (PREROLL.search(t) and (len(_plain_t(t)) <= PRE_LEN or t.rstrip().endswith("?"))) or \
                (READY_ASK.search(t) and (not polite or (k + 1 < len(sents) and _reply(sents[k + 1]["text"])))):
            hit = True
        elif not _reply(t):
            break
        k += 1
    pre_end = 0
    if hit and 0 < k < len(sents):
        pre_end = k
        b = min(float(sents[k]["start"]), float(sents[k - 1]["end"]) + 0.3)  # 준비 말 끝까지만 (그 뒤 말 없는 시범·콜드 오픈은 가편집이 판단)
        out.append({"a": 0.0, "b": round(b, 2), "why": PRE, "kind": "pre",
                    "text": " ".join(str(s["text"]) for s in sents[:k])})
    # 2) 촬영 끝 말: 끝에서 거꾸로 마무리 묶음 → 그 안 첫 촬영 끝 말 앞의 마지막 끝인사 뒤
    b0 = len(sents)
    while b0 - 1 >= pre_end and _outro(str(sents[b0 - 1]["text"])):
        b0 -= 1
    block = range(b0, len(sents))
    p = next((i for i in block if _postroll(sents[i]["text"]) or _postroll(sents[i]["text"], False)), None)
    c = next((i for i in range(p if p is not None else len(sents), b0 - 1, -1) if i < len(sents) and CLOSING.search(str(sents[i]["text"]))
              and not _postroll(sents[i]["text"])), None)
    post_from = len(sents)
    if c is not None:
        e = c  # 끝인사 바로 뒤 구독 부탁('구독이랑 좋아요 눌러 주세요')은 마무리로 남김
        while e + 1 < len(sents) and (CTA_RX.search(str(sents[e + 1]["text"])) or CONT.match(str(sents[e + 1]["text"]).strip())) \
                and not _postroll(sents[e + 1]["text"]) and not _postroll(sents[e + 1]["text"], False):
            e += 1
        after = sents[e + 1:]
        strong = any(_postroll(s["text"]) for s in after)
        weak = any(_postroll(s["text"], False) for s in after)
        talk = sum(float(s["end"]) - float(s["start"]) for s in after)
        replies = all(_reply(s["text"]) for s in after)
        more_closing = any(CLOSING.search(str(s["text"])) and not _postroll(s["text"]) and not _postroll(s["text"], False) for s in after)
        if after and (strong or (weak and not more_closing) or (replies and talk <= POST_SHORT)):
            post_from = e + 1
            # 끝인사 뒤 0.5초는 남김 ('감사합니다'의 '다'가 잘리지 않게 · 다음 말이 그보다 먼저 끝나면 거기까지)
            a = min(float(sents[e]["end"]) + POST_PAD, max(float(sents[e]["end"]), float(after[0]["end"])))
            end = max(float(dur or 0), float(sents[-1]["end"])) + 1.0
            out.append({"a": round(a, 2), "b": round(end, 2), "why": POST, "kind": "post", "text": " ".join(str(s["text"]) for s in after)})
    # 3) 구독·홍보 안내 (준비 말·끝 말 안은 빼고 — 끝 말 안의 홍보 말도 키트 설명에는 들어감: notice_kinds 로 따로 봄)
    kinds = notice_kinds(sents, pre_end, post_from)
    for i in sorted(kinds):
        a, b = _span(sents, i)
        o = out[-1] if out and out[-1]["kind"] == kinds[i] and out[-1]["b"] >= a - 0.05 else None
        if o:  # 이어진 안내 말은 한 덩어리로
            o["b"], o["text"] = b, o["text"] + " " + str(sents[i]["text"])
        else:
            out.append({"a": a, "b": b, "why": CTA if kinds[i] == "cta" else PROMO, "kind": kinds[i], "text": str(sents[i]["text"])})
    return sorted(out, key=lambda o: (o["a"], o["b"]))
