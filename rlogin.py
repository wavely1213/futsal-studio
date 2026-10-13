"""아이디·비밀번호로 연결 (D-250~D-257) — 원격 접속의 세 번째 연결 길에 쓰는 순수 계산.

PC 화면을 볼 수 없는 곳(사무실 PC·노트북·남의 휴대폰)에서 PC 스튜디오에서 정한 아이디·비밀번호로 연결한다.
  - 글자 맞추기(canon v1): 한글 자판으로 친 것 = 같은 글쇠를 영문 자판으로 친 것 · 대문자 = 소문자 (휴대폰 페이지 proto.js 와 같은 규칙).
  - 아이디·비밀번호 규칙과 쉬운 비밀번호 추천 (정해진 한국어 문장).
  - PC 찾기: 아이디에서 만든 ntfy 주제·열쇠(derive_login) → 암호로 잠근 '지금 주소' 기록 (seal_lookup/open_lookup).
  - 비밀번호 확인: SRP-6a (RFC 5054 3072비트 묶음 · SHA-256) — 비밀번호도 비밀번호에서 나온 값도 터널로 보내지 않는다.
    PC 의 remote.json 에는 검증값(verifier)만 둔다. 이 층은 소유자 승인으로 직접 만든 것이다 (D-257 · 검증된 부품
    hashlib·hmac·pow·pycryptodomex HKDF 위 · RFC 5054 부록 B 시험값과 pysrp(개발 때만) 교차 확인).
  - 틀린 시도 막기(Guard) · 주고받는 중인 확인(Pending) · 알림에 넣는 정해진 낱말(ua_vocab·country) · [끊기] 표.
  - 아이디만(idOnly · D-290 · 소유자 결정): 소유자 아이디(PRESET_ID)에서만 · 비밀번호 없이 아이디로 연결 — SRP 비밀 = 맞춘 아이디(id_secret) · 나머지 약속은 같음.
표준 라이브러리만 import 한다 (HKDF 는 함수 안에서 Cryptodome) · remote·app 을 import 하지 않는다 (AES-GCM 은 부르는 쪽이 넘겨줌).
"""
import base64
import collections
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata

CANON = 1
LOGIN_ITERS = 600_000            # 새 검증값의 PBKDF2 횟수 (PC 에서 약 0.5초 · 휴대폰 WebCrypto 약 0.4초) · 검증값마다 저장
ITERS_MIN, ITERS_MAX = 100_000, 2_000_000   # 페이지가 받아들이는 범위 (PC 가 고를 수 없게 아래·위를 막음)
LOOKUP_ITER = 100_000
LOOKUP_SALT = b"futsal-remote/login/v1"
PENDING_TTL, PENDING_PER_NET, PENDING_MAX = 60, 2, 256
NET_LADDER = ((5, 900), (3, 3600), (3, 14400), (3, 86400))   # (이만큼 틀리면, 이만큼 막음) · 마지막 칸에 머묾
NET_RESET = 86400                # 막힘이 끝나고(또는 마지막 시도 뒤) 하루 조용하면 처음 칸으로
GLOBAL_HOUR, GLOBAL_DAY = (10, 3600), (30, 86400)
TOTAL_STOP = 50                  # 맞힐 때까지 모두 50번 틀리면 PC·QR 휴대폰이 다시 켤 때까지 잠금 (저절로 줄지 않음)
NOTIFY_AT = (5, 20, 40)
NOTIFY_GAP = 3600
NETS_MAX, RECENT_MAX = 64, 200
EVENTS_MAX = 20
KILL_TTL = 86400
NAME_MAX = 60
GROUP = "5054-3072"
MODES = ("password", "idOnly")   # 로그인 방식 (블록의 mode · 없으면 password · D-290)
ID_ITERS = ITERS_MIN             # 아이디만 방식의 PBKDF2 횟수 (비밀이 아이디라 늘려도 지켜 주는 것이 없음 · 휴대폰에서 빨리)

# ---------- canon v1 (proto.js 와 같은 표 · 두벌식) ----------
CHO = "rRseEfaqQtTdwWczxvg"
JUNG = ["k", "o", "i", "O", "j", "p", "u", "P", "h", "hk", "ho", "hl", "y", "n", "nj", "np", "nl", "b", "m", "ml", "l"]
JONG = ["", "r", "R", "rt", "s", "sw", "sg", "e", "f", "fr", "fa", "fq", "ft", "fx", "fv", "fg", "a", "q", "qt", "t", "T", "d", "w", "c",
        "z", "x", "v", "g"]
COMPAT = ["r", "R", "rt", "s", "sw", "sg", "e", "E", "f", "fr", "fa", "fq", "ft", "fx", "fv", "fg", "a", "q", "Q", "qt", "t", "T", "d", "w",
          "W", "c", "z", "x", "v", "g",
          "k", "o", "i", "O", "j", "p", "u", "P", "h", "hk", "ho", "hl", "y", "n", "nj", "np", "nl", "b", "m", "ml", "l"]
_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def keys_of(s):
    """친 글 → 누른 영문 글쇠 (작은 글자): NFKC → 한글 음절·첫가끝 자모·호환 자모를 두벌식 글쇠로 · ₩ → \\ · A-Z 만 작게
    (ㄲ·ㄸ·ㅃ·ㅆ·ㅉ·ㅒ·ㅖ 는 Shift 글쇠라 대소문자와 함께 접힘). 그 밖의 글자(옛 자모·이모지·한자)는 그대로 (규칙에서 거절)."""
    out = []
    for ch in unicodedata.normalize("NFKC", str(s or "")):
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            i = o - 0xAC00
            out.append(CHO[i // 588] + JUNG[(i % 588) // 28] + JONG[i % 28])
        elif 0x1100 <= o <= 0x1112:
            out.append(CHO[o - 0x1100])
        elif 0x1161 <= o <= 0x1175:
            out.append(JUNG[o - 0x1161])
        elif 0x11A8 <= o <= 0x11C2:
            out.append(JONG[o - 0x11A7])
        elif 0x3131 <= o <= 0x3163:
            out.append(COMPAT[o - 0x3131])
        elif o == 0x20A9:
            out.append("\\")
        else:
            out.append(ch)
    return "".join(out).translate(_LOWER)


def canon_pw(s):
    """비밀번호 → 맞춘 꼴 (앞뒤 빈칸만 뺌) · 쓸 수 있는지는 pw_problems."""
    return keys_of(s).strip(" \t\n\r")


def canon_id(s):
    """아이디 → 맞춘 꼴 (빈칸·점·-·_ 를 모두 뺌) · 쓸 수 있는지는 id_problem."""
    return re.sub(r"[\t\n\r .\-_]", "", keys_of(s))


def _canon_set(words):
    return frozenset(canon_id(w) for w in words)


# 아이디로 쓸 수 없는 흔한 낱말 (맞춘 꼴로 비교 · 한글은 같은 글쇠)
RESERVED_IDS = _canon_set((
    "futsal", "futsalstudio", "studio", "admin1", "tester", "password", "football", "soccer", "korea", "academy", "mulgyeol",
    "wavely", "administrator", "admin123", "manager", "master", "superuser", "support", "official", "account", "default",
    "guest1", "user01", "test01", "test123", "qwerty", "abcdef", "abc123", "asdfgh", "iloveyou", "youtube", "google", "naver1",
    "kakao1", "coach1", "player", "futsal1", "soccer1", "remote", "server", "system", "studio1", "123456a",
    "풋살코치", "축구코치", "풋살사관학교", "관리자", "테스트", "아이디", "풋살스튜디오", "운영자", "사용자", "축구교실", "풋살교실",
    "사관학교", "감독님", "코치님", "선생님", "비밀번호", "유튜브"))

# 흔한 비밀번호 (맞춘 꼴 · 끝의 숫자·기호 4개까지 떼고도 봄)
COMMON = frozenset(canon_pw(w) for w in (
    "password", "passw0rd", "p@ssword", "password1", "qwerty", "qwerty123", "qwer1234", "qwerasdf", "1q2w3e4r", "1q2w3e4r5t",
    "1qaz2wsx", "q1w2e3r4", "asdf1234", "asdfqwer", "zxcv1234", "zxcvbnm", "asdfghjkl", "qwertyuiop", "iloveyou", "letmein",
    "welcome", "monkey", "dragon", "football", "soccer", "baseball", "basketball", "master", "superman", "batman", "princess",
    "sunshine", "shadow", "michael", "jennifer", "trustno1", "abc123", "abcd1234", "abcdefg", "abcdefgh", "aaaa1111",
    "a1b2c3d4", "admin", "administrator", "login", "hello", "hello123", "freedom", "whatever", "starwars", "computer",
    "internet", "samsung", "galaxy", "iphone", "apple", "google", "naver", "kakao", "youtube", "korea", "seoul", "busan",
    "futsal", "futsalstudio", "studio", "coach", "player", "goal", "manchester", "chelsea", "liverpool", "arsenal", "barcelona",
    "realmadrid", "messi", "ronaldo", "sonheungmin", "heungmin", "parkjisung", "redsdevil", "redsdevils", "minecraft",
    "pokemon", "lovelove", "loveyou", "mylove", "happy", "happyday", "sky", "blue", "green", "orange", "summer", "winter",
    "spring", "autumn", "flower", "angel", "family", "friend", "friends", "secret", "test", "tester", "guest", "user",
    "changeme", "temp", "default", "pass", "passwd", "mypassword", "qazwsx", "zaq12wsx", "1qazxsw2", "q1w2e3", "qwe123",
    "asd123", "zxc123", "qweasd", "qweasdzxc", "wavely", "mulgyeol", "academy", "pass1234", "love1234", "korea1234",
    "aaaaaa", "abcabc", "asdasd", "qweqwe", "zxczxc", "ilovekorea", "sarang", "saranghae", "saranghaeyo", "annyeong",
    "gamsahamnida", "fighting", "hwaiting", "oppa", "unni", "chingu", "hangul", "daehanminguk",
    # 한글 (같은 글쇠로 바꿔 넣음)
    "사랑해", "사랑해요", "사랑", "안녕하세요", "안녕", "풋살", "축구", "비밀번호", "대한민국", "하늘", "행복", "자기야", "여보",
    "감사합니다", "고맙습니다", "바보", "우리집", "아이디", "관리자", "테스트", "풋살사관학교", "사관학교", "축구공", "운동장",
    "풋살장", "축구화", "손흥민", "박지성", "이강인", "김민재", "대한민국축구", "화이팅", "파이팅", "엄마", "아빠", "가족",
    "우리가족", "친구", "사랑합니다", "보고싶어", "행복해", "좋아해", "보물", "공주", "왕자", "천사", "하나님", "주님", "예수",
    "부처님", "감독", "코치", "선생님", "학생", "회사", "서울", "부산", "한국", "가나다라", "가나다라마바사", "ㅁㄴㅇㄹ", "ㅂㅈㄷㄱ",
    "ㅋㅋㅋㅋ", "ㅎㅎㅎㅎ"))

_COMMON_TAIL = re.compile(r"[0-9!@#$%^&*.?~]")
_ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm", "abcdefghijklmnopqrstuvwxyz")

ID_MSG = {
    "short": "아이디는 영문·숫자면 6글자 이상, 한글이면 3글자 이상으로 정해 주세요",
    "long": "아이디가 너무 길어요 · 영문·숫자 32글자 안으로 정해 주세요",
    "chars": "아이디는 한글·영문·숫자로만 정해 주세요",
    "digits": "아이디를 숫자만으로 정할 수는 없어요",
    "reserved": "너무 흔한 아이디예요 · 남이 짐작하기 어려운 아이디로 골라 주세요",
}
PW_MSG = {
    "chars": "쓸 수 없는 글자가 있어요 (한글·영문·숫자·기호만)",
    "short": "비밀번호를 조금 더 길게 해 주세요 (영문·숫자 섞어 8자 이상 · 한글은 5글자 이상)",
    "letters": "비밀번호를 조금 더 길게 해 주세요 (영문·숫자 섞어 8자 이상 · 한글은 5글자 이상)",
    "long": "비밀번호가 너무 길어요 · 64글자 안으로 정해 주세요",
    "digits": "숫자만으로는 안 돼요",
    "id": "비밀번호에 아이디가 들어 있어요",
    "common": "너무 흔한 비밀번호예요",
    "run": "같은 글자 반복이나 1234·qwer 같은 줄은 쉽게 맞혀져요",
    "personal": "전화번호·생일은 남이 알기 쉬워요",
    "mismatch": "두 비밀번호가 달라요",
    "empty": "비밀번호를 넣어 주세요",
}


def id_problem(s):
    """아이디 → 문제 코드 ('short'·'long'·'chars'·'digits'·'reserved') 또는 None (문장은 ID_MSG)."""
    c = canon_id(s)
    if not re.fullmatch(r"[\x20-\x7e]*", c) or not re.fullmatch(r"[a-z0-9]*", c):
        return "chars"
    if len(c) < 6:
        return "short"
    if len(c) > 32:
        return "long"
    if c.isdigit():
        return "digits"
    if c in RESERVED_IDS:
        return "reserved"
    return None


def _longest_row_run(s):
    """키보드 줄·알파벳 순서(앞으로·뒤로)로 이어진 가장 긴 토막의 길이."""
    best = 1 if s else 0
    for row in _ROWS:
        for seq in (row, row[::-1]):
            pos = {ch: i for i, ch in enumerate(seq)}
            run = 1
            for a, b in zip(s, s[1:]):
                run = run + 1 if a in pos and b in pos and pos[b] == pos[a] + 1 else 1
                best = max(best, run)
    return best


def _is_common(c):
    t = c
    for _ in range(5):
        if t in COMMON:
            return True
        if not t or not _COMMON_TAIL.fullmatch(t[-1]):
            return False
        t = t[:-1]
    return False


def hangul_syllables(s):
    """친 글 속 한글 음절(가~힣) 수 (NFKC 뒤) — '한글 5글자 이상이면 숫자 없어도 돼요'를 셀 때만 (맞춘 꼴에는 한글이 없음)."""
    return sum(1 for ch in unicodedata.normalize("NFKC", str(s or "")) if 0xAC00 <= ord(ch) <= 0xD7A3)


def pw_problems(pw, id_canon=None, pw2=None):
    """비밀번호 → 문제 코드 목록 (차례: 글자 · R1 길이 · R2 숫자만 · R3 아이디 · R4 흔함 · R5 반복·줄 · R6 전화·생일 · 두 번 다름).
    문장은 PW_MSG. 모두 맞춘 꼴(canon_pw)로 본다 — 다만 R1 의 '글쇠만(숫자·기호 없이)은 12글쇠 이상'은 한글 음절을 5개 이상 친
    비밀번호에는 묻지 않음 (화면 문장 '한글 5글자 이상이면 숫자 없어도 돼요'와 같게 · 받침 없는 다섯 글자는 10글쇠뿐이라)."""
    c = canon_pw(pw)
    if not c:
        return ["empty"]
    out = []
    if not re.fullmatch(r"[\x20-\x7e]+", c):
        out.append("chars")
    if len(c) < 8:
        out.append("short")
    elif len(c) > 64:
        out.append("long")
    elif re.fullmatch(r"[a-z]+", c) and len(c) < 12 and hangul_syllables(pw) < 5:
        out.append("letters")
    if c.isdigit():
        out.append("digits")
    if id_canon and len(id_canon) >= 3 and id_canon in re.sub(r"[\t\n\r .\-_]", "", c):
        out.append("id")
    if _is_common(c):
        out.append("common")
    if re.search(r"(.)\1{3}", c) or (len(c) >= 5 and _longest_row_run(c) >= 5 and len(c) - _longest_row_run(c) < 4):
        out.append("run")
    flat = re.sub(r"[-. ]", "", c)
    if re.search(r"01\d{8,9}", flat) or re.search(r"(?:19|20)\d{6}", flat):
        out.append("personal")
    if pw2 is not None and canon_pw(pw2) != c:
        out.append("mismatch")
    return out


# ---------- 쉬운 비밀번호 추천 (흔한 두·세 글자 낱말 256개 중 셋 + 숫자 4개 · 약 37비트) ----------
NOUNS = (
    "하늘", "바다", "노을", "구름", "바람", "햇살", "별빛", "달빛", "나무", "꽃잎", "들판", "언덕", "호수", "강물", "폭포", "모래", "파도", "소나무", "단풍", "눈꽃", "봄비", "무지개",
    "새벽", "아침", "저녁", "한낮", "여름", "가을", "겨울", "봄날", "사과", "포도", "딸기", "수박", "참외", "복숭아", "바나나", "감자", "고구마", "옥수수", "당근", "호박", "김밥",
    "떡볶이", "만두", "라면", "국수", "비빔밥", "된장", "김치", "두부", "우유", "치즈", "빵집", "과자", "사탕", "호두", "땅콩", "레몬", "자두", "강아지", "고양이", "토끼", "거북이",
    "다람쥐", "코끼리", "기린", "사자", "호랑이", "펭귄", "돌고래", "고래", "참새", "비둘기", "독수리", "부엉이", "나비", "꿀벌", "잠자리", "개구리", "오리", "병아리", "송아지", "망아지",
    "여우", "곰돌이", "판다", "원숭이", "사슴", "너구리", "연필", "지우개", "공책", "가방", "우산", "모자", "신발", "양말", "장갑", "시계", "안경", "거울", "의자", "책상", "침대", "이불",
    "베개", "창문", "열쇠", "자전거", "기차", "버스", "택시", "비행기", "배낭", "등대", "풍선", "종이", "편지", "사진", "학교", "공원", "시장", "도서관", "병원", "우체국", "놀이터",
    "정류장", "기차역", "항구", "마을", "골목", "광장", "다리", "시골", "도시", "산책로", "체육관", "수영장", "카페", "식당", "극장", "박물관", "미술관", "동물원", "식물원", "캠핑장",
    "섬마을", "등산로", "바닷가", "축구공", "골대", "잔디", "응원", "유니폼", "주장", "선수", "연습", "훈련", "드리블", "패스", "슈팅", "수비", "공격", "골키퍼", "코너킥", "헤딩", "득점",
    "전반전", "후반전", "연장전", "승리", "우승", "트로피", "메달", "관중", "박수", "경기장", "운동화", "음악", "노래", "피아노", "북소리", "리듬", "박자", "그림", "물감", "도화지", "동화",
    "소설", "시인", "일기", "달력", "휴일", "소풍", "여행", "모험", "보물섬", "지도", "나침반", "등산", "캠핑", "낚시", "웃음", "희망", "용기", "우정", "미소", "기쁨", "이웃", "선물",
    "축제", "잔치", "생일", "약속", "추억", "꿈나무", "별자리", "은하수", "우주선", "로켓", "행성", "지구", "태양", "구슬", "단추", "바구니", "접시", "컵라면", "도시락", "고추", "마늘",
    "양파", "버섯", "오이", "토마토", "가지", "새우", "꽃게", "오징어", "문어", "조개", "진주", "보석", "왕관", "초가집", "정원", "울타리", "대문", "계단", "지붕", "마당", "텃밭", "꽃밭",
    "벤치", "분수",
)


def suggest(id_canon=None, rnd=None):
    """쉬운 비밀번호 하나: 서로 다른 낱말 셋 + 숫자 4개 (예: 노을축구공바다4827) · secrets 로만 고름 · 규칙에 늘 맞음."""
    rnd = rnd or secrets.SystemRandom()
    while True:
        words = rnd.sample(NOUNS, 3)
        pw = "".join(words) + "".join(str(rnd.randrange(10)) for _ in range(4))
        if not pw_problems(pw, id_canon):
            return pw


# ---------- PC 찾기 (아이디 → ntfy 주제·열쇠 · 페이지 proto.deriveLogin 과 같음) ----------

def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64u(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def derive_login(cid):
    """맞춘 아이디 → {topic: 'fsl'+24hex, bkey: 32바이트(AES-GCM), idc: 16바이트 base64url (터널로 아이디 대신 보냄)}.
    PBKDF2-SHA256(아이디, 'futsal-remote/login/v1', 100,000번, 64바이트) — 아이디는 비밀이 아니다 (D-252)."""
    dk = hashlib.pbkdf2_hmac("sha256", str(cid).encode("ascii"), LOOKUP_SALT, LOOKUP_ITER, 64)
    return {"topic": "fsl" + dk[:12].hex(), "bkey": dk[16:48], "idc": _b64u(dk[48:64])}


def seal_lookup(bkey, topic, inner, gcm_seal):
    """'지금 주소' 기록 → ntfy 글 ('fsl1.' + AES-GCM · aad 에 주제)."""
    return "fsl1." + gcm_seal(bkey, json.dumps(inner, ensure_ascii=False), "fsl1|" + topic)


def open_lookup(bkey, topic, msg, gcm_open):
    """ntfy 글 → 기록 dict · 틀리거나 모양이 다르면 None (proto.openLookup 과 같은 검사)."""
    if not isinstance(msg, str) or not msg.startswith("fsl1."):
        return None
    try:
        m = json.loads(gcm_open(bkey, msg[5:], "fsl1|" + topic))
    except Exception:  # noqa: BLE001 — 남이 올린 글·깨진 글
        return None
    ok = (isinstance(m, dict) and m.get("v") == 1 and isinstance(m.get("pc"), str) and re.fullmatch(r"[0-9a-f]{16}", m["pc"])
          and isinstance(m.get("seq"), int) and isinstance(m.get("ts"), int) and m.get("state") in ("on", "off"))
    return m if ok else None


# ---------- SRP-6a (RFC 5054 · D-251) ----------
_N3072 = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33"
    "A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864"
    "D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF", 16)
_N1024 = int(
    "EEAF0AB9ADB38DD69C33F80AFA8FC5E86072618775FF3C0B9EA2314C9C256576D674DF7496EA81D3383B4813D692C6E0E0D5D8E250B98BE48E495C1D6089DAD1"
    "5DC7D7B46154D6B6CE8EF4AD69B15D4982559B297BCF1885C529F566660E57EC68EDBC3C05726CC02FD4CBF4976EAA9AFD5138FE8376435B9FC61D2FC0EB06E3", 16)
# 실제로 쓰는 묶음은 5054-3072 하나 (페이지에 박혀 있음 · PC 가 고를 수 없음) · 5054-1024-sha1 은 RFC 시험값 확인용
GROUPS = {"5054-3072": (_N3072, 5, hashlib.sha256), "5054-1024-sha1": (_N1024, 2, hashlib.sha1)}


def group(grp=GROUP):
    return GROUPS[grp]


def nlen(N):
    return (N.bit_length() + 7) // 8


def pad(n, N):
    return int(n).to_bytes(nlen(N), "big")


def _H(hf, *parts):
    h = hf()
    for p in parts:
        h.update(p)
    return h.digest()


def srp_k(grp=GROUP):
    N, g, hf = group(grp)
    return int.from_bytes(_H(hf, pad(N, N), pad(g, N)), "big")


def srp_x(canon_password, salt, iters):
    """x = PBKDF2-SHA256(맞춘 비밀번호, 'fsl-x/v1|' + b64u(salt), iters, 32) — 아이디는 넣지 않음 (아이디를 바꿔도 검증값 그대로)."""
    dk = hashlib.pbkdf2_hmac("sha256", canon_password.encode("ascii"), ("fsl-x/v1|" + _b64u(salt)).encode("ascii"), int(iters), 32)
    return int.from_bytes(dk, "big")


def srp_x_rfc(user, password, salt, grp="5054-1024-sha1"):
    """RFC 5054 의 x = H(s | H(I | ':' | P)) — 부록 B 시험값 확인용 (실제 연결에는 srp_x)."""
    hf = group(grp)[2]
    return int.from_bytes(_H(hf, salt, _H(hf, user.encode(), b":", password.encode())), "big")


def srp_verifier(x, grp=GROUP):
    N, g, _ = group(grp)
    return pow(g, x, N)


def srp_u(A, B, grp=GROUP):
    N, _, hf = group(grp)
    return int.from_bytes(_H(hf, pad(A, N), pad(B, N)), "big")


def a_ok(A, grp=GROUP):
    N = group(grp)[0]
    return isinstance(A, int) and 0 < A < N and A % N != 0


def server_start(v, A, grp=GROUP, b=None):
    """PC: (b, B, u) — A 는 a_ok 로 먼저 확인 · u 가 0 이면 b 를 다시 고름."""
    N, g, _ = group(grp)
    if not a_ok(A, grp):
        raise ValueError("bad A")
    k = srp_k(grp)
    while True:
        bb = b if b is not None else int.from_bytes(secrets.token_bytes(32), "big")
        B = (k * v + pow(g, bb, N)) % N
        u = srp_u(A, B, grp)
        if u != 0 and B % N != 0:
            return bb, B, u
        if b is not None:  # 시험값으로 준 b 가 맞지 않음
            raise ValueError("u == 0")


def server_S(A, b, v, u, grp=GROUP):
    N = group(grp)[0]
    return pow(A * pow(v, u, N) % N, b, N)


def client_start(grp=GROUP, a=None):
    """시험·node 교차 확인용 클라이언트 (페이지는 proto.srpStart): (a, A)."""
    N, g, _ = group(grp)
    a = a if a is not None else int.from_bytes(secrets.token_bytes(32), "big")
    return a, pow(g, a, N)


def client_S(B, a, x, u, grp=GROUP):
    N, g, _ = group(grp)
    if B % N == 0 or u == 0:
        raise ValueError("bad B")
    k = srp_k(grp)
    return pow((B - k * pow(g, x, N)) % N, a + u * x, N)


def session_key(S, grp=GROUP):
    N, _, hf = group(grp)
    return _H(hf, pad(S, N))


def transcript(host, pc_id, idc, login, grp, iters, salt, A, B):
    """T: 받는 곳(터널 host)·PC·아이디 표(idc)·이번 확인 번호·묶음·횟수·salt·A·B 를 모두 묶음 → 다른 터널로 옮긴 대화는 안 맞음."""
    N = group(grp)[0]
    text = "\n".join(("fsl-srp/v1", str(host).lower(), pc_id, idc, login, grp, str(int(iters)), _b64u(salt), _b64u(pad(A, N)), _b64u(pad(B, N))))
    return hashlib.sha256(text.encode("utf-8")).digest()


def m1(K, T, remember, name, install):
    """M1: 기억하기·기기 이름·설치 번호까지 묶음 (가운데에서 '이번만'↔'기억하기'를 바꾸면 안 맞음)."""
    msg = f"fsl-m1\n{T.hex()}\n{'1' if remember else '0'}\n{name}\n{install or ''}"
    return hmac.new(K, msg.encode("utf-8"), hashlib.sha256).digest()


def m2(K, T, M1, sealed):
    msg = f"fsl-m2\n{T.hex()}\n{_b64u(M1)}\n{hashlib.sha256(sealed.encode('ascii')).hexdigest()}"
    return hmac.new(K, msg.encode("utf-8"), hashlib.sha256).digest()


def seal_key(K, T):
    """KE = HKDF-SHA256(K, salt=T, info='fsl-seal/v1', 32) — 기기 열쇠를 잠가 보내는 열쇠 (페이지는 WebCrypto HKDF)."""
    from Cryptodome.Hash import SHA256
    from Cryptodome.Protocol.KDF import HKDF
    return HKDF(K, 32, T, SHA256, context=b"fsl-seal/v1")


def sealed_aad(login, pc_id):
    return f"fsl2|{login}|{pc_id}"


_FAKE_CACHE = collections.OrderedDict()
_FAKE_LOCK = threading.Lock()


def fake_params(fake, idc, grp=GROUP):
    """틀린 아이디(idc)에도 같은 모양의 대답: salt·검증값을 PC 비밀(fake)에서 정해진 대로 만듦 (아이디가 맞는지 알 수 없게)."""
    key = (fake, idc, grp)
    with _FAKE_LOCK:
        hit = _FAKE_CACHE.get(key)
        if hit:
            _FAKE_CACHE.move_to_end(key)
            return hit
    salt = hmac.new(fake, ("s|" + idc).encode("utf-8"), hashlib.sha256).digest()[:16]
    x = int.from_bytes(hmac.new(fake, ("x|" + idc).encode("utf-8"), hashlib.sha256).digest(), "big")
    out = (salt, srp_verifier(x, grp))
    with _FAKE_LOCK:
        _FAKE_CACHE[key] = out
        while len(_FAKE_CACHE) > 64:
            _FAKE_CACHE.popitem(last=False)
    return out


def make_verifier(password, iters=None, salt=None):
    """비밀번호 → {salt, iters, verifier} (base64url) · 약 0.5초. 비밀번호는 여기서만 지나가고 남지 않는다."""
    iters = iters or LOGIN_ITERS
    salt = salt or secrets.token_bytes(16)
    v = srp_verifier(srp_x(canon_pw(password), salt, iters))
    return {"grp": GROUP, "salt": _b64u(salt), "iters": int(iters), "verifier": _b64u(pad(v, group()[0]))}


# ---------- 아이디만 (idOnly · D-290 · 소유자 결정) ----------

def id_only_ok(cid):
    """아이디만 방식을 쓸 수 있는 아이디인지 — 소유자 아이디(PRESET_ID) 하나뿐 (D-290: 이 사이트는 소유자만 씀 ·
    다른 아이디에는 페이지도 '아이디만' 표시를 믿지 않아서, 남이 올린 가짜 기록으로 비밀번호 PC 사용자를 속일 수 없음)."""
    return canon_id(cid) == PRESET_ID


def id_secret(cid):
    """아이디만 방식의 SRP 비밀 = 맞춘 아이디(canon v1 · 영문 소문자·숫자) 그대로 — 페이지 proto.idSecret 과 같음.
    x = PBKDF2-SHA256(id_secret, 'fsl-x/v1|' + b64u(salt), iters, 32) (srp_x 그대로). 맞춘 아이디는 canon_pw 를 거쳐도 같아서
    예전 페이지(비밀번호 칸만)에 아이디를 비밀번호로 쳐도 맞는다 (8글쇠 이상 · wavely1213 은 10글쇠)."""
    return canon_id(cid)


def make_id_verifier(cid, iters=None, salt=None):
    """아이디 → 아이디만 방식의 {grp, salt, iters, verifier} (비밀번호 없음 · 횟수 ID_ITERS)."""
    return make_verifier(id_secret(cid), iters or ID_ITERS, salt)


def is_id_only(b):
    """로그인 블록(또는 PRESET)이 아이디만 방식인지."""
    return isinstance(b, dict) and b.get("mode") == "idOnly"


# ---------- 틀린 시도 막기 (D-254) ----------

def time_scale():
    """개발 때만(FUTSAL_REMOTE_DEV=1) FUTSAL_LOGIN_TIME_SCALE 로 시간을 줄임 (e2e) · 그 밖에는 늘 1."""
    if os.environ.get("FUTSAL_REMOTE_DEV") != "1":
        return 1.0
    try:
        v = float(os.environ.get("FUTSAL_LOGIN_TIME_SCALE") or 1)
    except ValueError:
        return 1.0
    return v if 0 < v <= 1 else 1.0


def fresh_guard():
    return {"nets": {}, "recent": [], "allUntil": 0, "total": 0, "noticeAt": 0, "notified": []}


class Guard:
    """로그인 블록(remote.json 'login')의 guard·suspended 를 직접 고침 · 틀릴 때마다 persist() (파일에 바로).
    확인(check)과 세기(fail)는 부르는 쪽이 같은 self.lock 안에서 한다 (동시에 들어와도 넘치지 않게)."""

    def __init__(self, login, persist=None, clock=time.time, scale=None, lock=None):
        self.login, self.persist, self.clock = login, persist, clock
        self.scale = time_scale() if scale is None else scale
        self.lock = lock or threading.RLock()   # remote.Service 는 로그인 블록 전체를 지키는 잠금 하나를 넘겨줌
        g = login.get("guard") if isinstance(login.get("guard"), dict) else {}
        login["guard"] = dict(fresh_guard(), **g)
        login.setdefault("suspended", None)

    @property
    def g(self):
        return self.login["guard"]

    def _d(self, secs):
        return secs * self.scale

    def _net(self, net, now):
        st = self.g["nets"].get(net)
        if st and now - max(st.get("last", 0), st.get("until", 0)) >= self._d(NET_RESET):
            del self.g["nets"][net]
            st = None
        return st or {"lv": 0, "n": 0, "until": 0, "last": 0}

    def _recent(self, now):
        keep = [t for t in self.g["recent"] if now - t < self._d(GLOBAL_DAY[1])]
        self.g["recent"] = keep[-RECENT_MAX:]
        return self.g["recent"]

    def _hour(self, now):
        return sum(1 for t in self._recent(now) if now - t < self._d(GLOBAL_HOUR[1]))

    def _all_until(self, now):
        rec = sorted(self._recent(now))
        u = self.g.get("allUntil") or 0
        if len(rec) >= GLOBAL_DAY[0]:
            u = max(u, rec[len(rec) - GLOBAL_DAY[0]] + self._d(GLOBAL_DAY[1]))
        return u if u > now else 0

    def check(self, net):
        """막혀 있으면 {'code': 'suspended', 'why'} · {'code': 'locked', 'scope': 'net'|'all', 'until'} · 아니면 None."""
        now = self.clock()
        s = self.login.get("suspended")
        if s:
            return {"code": "suspended", "why": s.get("why")}
        st = self._net(net, now)
        if st["until"] > now:
            return {"code": "locked", "scope": "net", "until": int(st["until"] + 0.999)}
        au = self._all_until(now)
        if au:
            return {"code": "locked", "scope": "all", "until": int(au + 0.999)}
        return None

    def left(self, net):
        now = self.clock()
        if self.check(net):
            return 0
        st = self._net(net, now)
        return max(0, min(NET_LADDER[st["lv"]][0] - st["n"], GLOBAL_HOUR[0] - self._hour(now),
                          GLOBAL_DAY[0] - len(self._recent(now)), TOTAL_STOP - int(self.g["total"])))

    def fail(self, net, real=True):
        """틀린 시도 하나 세고 저장 → {'left', 'until', 'notes': [(알림 종류, 모두 틀린 수)]}.
        real=False(틀린 아이디 · 가짜 대답)는 그 인터넷 대역(net)만 셈."""
        now = self.clock()
        nets = self.g["nets"]
        st = self._net(net, now)
        st["n"] += 1
        st["last"] = now
        allow, dur = NET_LADDER[st["lv"]]
        if st["n"] >= allow:
            st["until"] = now + self._d(dur)
            st["lv"], st["n"] = min(st["lv"] + 1, len(NET_LADDER) - 1), 0
        nets.pop(net, None)
        nets[net] = st
        while len(nets) > NETS_MAX:
            nets.pop(next(iter(nets)))
        notes = []
        if real:
            self._recent(now).append(now)
            if self._hour(now) >= GLOBAL_HOUR[0]:
                self.g["allUntil"] = max(self.g.get("allUntil") or 0, now + self._d(GLOBAL_HOUR[1]))
            self.g["total"] = int(self.g["total"]) + 1
            total = self.g["total"]
            if total >= TOTAL_STOP and not self.login.get("suspended"):
                self.login["suspended"] = {"why": "fails", "at": int(now)}
                notes.append(("login_stop", total))
            elif not self.login.get("suspended"):
                due = [t for t in NOTIFY_AT if total >= t and t not in self.g["notified"]]
                if due and now - (self.g.get("noticeAt") or 0) >= self._d(NOTIFY_GAP):
                    self.g["notified"] = sorted(set(self.g["notified"]) | set(due))
                    self.g["noticeAt"] = now
                    notes.append(("login_fail", total))
        if self.persist:
            self.persist()
        v = self.check(net)
        return {"left": self.left(net), "until": v.get("until") if v and v["code"] == "locked" else None, "notes": notes}

    def success(self, net):
        self.g["nets"].pop(net, None)
        self.g["total"], self.g["notified"], self.g["noticeAt"] = 0, [], 0
        if self.persist:
            self.persist()

    def unlock(self):
        """PC·QR 휴대폰의 [다시 켜기]·[지금 풀기]: 막힘·모두 틀린 수·잠금을 모두 지움."""
        self.login["guard"] = fresh_guard()
        self.login["suspended"] = None
        if self.persist:
            self.persist()

    def view(self):
        """PC 화면용: {lock, netLocks, failsToday, total, suspended}."""
        now = self.clock()
        au = self._all_until(now)
        net_until = [st["until"] for st in self.g["nets"].values() if st.get("until", 0) > now]
        lock = {"scope": "all", "until": int(au)} if au else ({"scope": "net", "until": int(max(net_until))} if net_until else None)
        lt = time.localtime(now)
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        return {"lock": lock, "netLocks": len(net_until), "failsToday": sum(1 for t in self._recent(now) if t >= midnight),
                "total": int(self.g["total"]), "suspended": self.login.get("suspended")}


class Busy(Exception):
    pass


class Pending:
    """주고받는 중인 확인 (start → finish): 인터넷 대역마다 2개(셋째는 그 대역의 가장 오래된 것을 바꿔 끼움) · 모두 256개(넘으면 Busy ·
    남의 것을 밀어내지 않음) · 60초 · 한 번만."""

    def __init__(self, clock=time.time, ttl=PENDING_TTL):
        self.clock, self.ttl = clock, ttl
        self.lock = threading.Lock()
        self.items = {}
        self.n = 0   # 넣은 차례 (같은 시각이면 먼저 넣은 것이 '가장 오래된 것')

    def _sweep(self, now):
        for k in [k for k, v in self.items.items() if now - v["created"] > self.ttl]:
            del self.items[k]

    def put(self, net, entry, lid=None):
        """→ 확인 번호 (22글자 · lid 를 주면 그것 — T 에 미리 넣으려고)."""
        now = self.clock()
        with self.lock:
            self._sweep(now)
            mine = sorted((v["created"], v["n"], k) for k, v in self.items.items() if v["net"] == net)
            if len(mine) >= PENDING_PER_NET:
                del self.items[mine[0][2]]
            elif len(self.items) >= PENDING_MAX:
                raise Busy()
            lid = lid or _b64u(secrets.token_bytes(16))
            self.n += 1
            self.items[lid] = dict(entry, net=net, created=now, n=self.n)
            return lid

    def take(self, lid):
        if not isinstance(lid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{22}", lid):
            return None
        with self.lock:
            v = self.items.pop(lid, None)
        if not v or self.clock() - v["created"] > self.ttl:
            return None
        return v

    def clear(self):
        with self.lock:
            self.items.clear()

    def sweep(self):
        with self.lock:
            self._sweep(self.clock())

    def __len__(self):
        with self.lock:
            return len(self.items)


# ---------- 알림 낱말 (정해진 것만 · 기기가 보낸 이름은 쓰지 않음) ----------

def ua_vocab(ua):
    """User-Agent → 'Windows · Chrome' 꼴 (OS 8가지 × 브라우저 9가지 정해진 낱말)."""
    u = str(ua or "")[:512]
    if "iPhone" in u:
        os_ = "iPhone"
    elif "iPad" in u:
        os_ = "iPad"
    elif "Android" in u:
        os_ = "Android"
    elif "CrOS" in u:
        os_ = "ChromeOS"
    elif "Windows" in u:
        os_ = "Windows"
    elif "Macintosh" in u or "Mac OS X" in u:
        os_ = "Mac"   # iPadOS 사파리도 Mac 으로 보냄
    elif "Linux" in u or "X11" in u:
        os_ = "Linux"
    else:
        os_ = "기타"
    if re.search(r"KAKAOTALK", u, re.I):
        br = "카카오톡"
    elif re.search(r"NAVER\(inapp|NAVER/", u):
        br = "네이버"
    elif "SamsungBrowser" in u:
        br = "삼성 인터넷"
    elif "Whale/" in u:
        br = "웨일"
    elif "Edg/" in u or "EdgiOS" in u or "EdgA/" in u:
        br = "Edge"
    elif "Firefox/" in u or "FxiOS" in u:
        br = "Firefox"
    elif "Chrome/" in u or "CriOS" in u:
        br = "Chrome"
    elif "Safari/" in u:
        br = "Safari"
    else:
        br = "기타"
    return f"{os_} · {br}"


def country(cc):
    """Cf-Ipcountry → '한국' · '해외(US)' · 모르면 None."""
    c = str(cc or "").strip().upper()
    if c == "KR":
        return "한국"
    if re.fullmatch(r"[A-Z]{2}", c) and c not in ("XX", "T1"):
        return f"해외({c})"
    return None


def kill_token():
    return secrets.token_urlsafe(18)


def kill_hash(tok):
    return hashlib.sha256(str(tok).encode("ascii", "replace")).hexdigest()


KILL_RE = re.compile(r"[A-Za-z0-9_-]{24}")


# ---------- remote.json 'login' 블록 확인 (따로 · 깨져도 기기 목록은 그대로) ----------

def _b64_len(s, n):
    try:
        return isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9_-]+", s) is not None and len(_unb64u(s)) == n
    except (ValueError, TypeError):
        return False


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def valid_login(b):
    """로그인 블록 모양 확인 → bool (깨졌으면 부르는 쪽이 꺼 둠)."""
    try:
        if not isinstance(b, dict) or b.get("v") != 1 or b.get("canon") != CANON or not isinstance(b.get("enabled"), bool):
            return False
        if not (isinstance(b.get("id"), str) and re.fullmatch(r"[a-z0-9]{6,32}", b["id"]) and isinstance(b.get("idShow"), str)
                and 0 < len(b["idShow"]) <= 32):
            return False
        if not (isinstance(b.get("topic"), str) and re.fullmatch(r"fsl[0-9a-f]{24}", b["topic"]) and _b64_len(b.get("bkey"), 32)
                and _b64_len(b.get("idc"), 16)):
            return False
        if b.get("grp") != GROUP or not _b64_len(b.get("salt"), 16) or not _int(b.get("iters")) or not ITERS_MIN <= b["iters"] <= ITERS_MAX:
            return False
        N = group()[0]
        if not _b64_len(b.get("verifier"), nlen(N)) or not 1 < int.from_bytes(_unb64u(b["verifier"]), "big") < N:
            return False
        if not _b64_len(b.get("fake"), 32) or not (isinstance(b.get("control"), str) and re.fullmatch(r"fsc[0-9a-f]{24}", b["control"])):
            return False
        if not (_int(b.get("gen")) and b["gen"] >= 1 and _int(b.get("setAt")) and _int(b.get("seq")) and b["seq"] >= 0):
            return False
        if "guard" in b and not isinstance(b["guard"], dict):
            return False
        s = b.get("suspended")
        if s is not None and not (isinstance(s, dict) and s.get("why") in ("fails", "kick")):
            return False
        ev = b.get("events", [])
        if not isinstance(ev, list) or len(ev) > EVENTS_MAX:
            return False
        for e in ev:
            if not (isinstance(e, dict) and isinstance(e.get("ref"), str) and _int(e.get("at")) and isinstance(e.get("kh"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", e["kh"]) and e.get("kind") in ("guest", "remember")):
                return False
        c = b.get("collision")
        if c is not None and not isinstance(c, (int, float)):
            return False
        if b.get("mode", "password") not in MODES or (is_id_only(b) and not id_only_ok(b["id"])):
            return False
        return True
    except (TypeError, ValueError, AttributeError):
        return False


# 미리 정해 둔 로그인 (D-258 · D-291): 공개 저장소에는 검증값만 둔다 · tools/make_preset.py 가 만든 한 줄을 붙여 넣음. None 이면 끔.
# 지금 값은 아이디만 방식(mode idOnly · 소유자 결정 D-290): 검증값은 아이디에서 나온 비밀(id_secret)로 만든 것 — 비밀번호는 어디에도 없다.
# 정해진 채널(최경진 풋살사관학교 · 모든 설치의 기본값이라 그것만으로는 안 됨) + 소유자 PC 표시(이 판 전에 QR 로 연결해 둔 기기 ·
# 그 채널로 연결한 유튜브)가 있고, 아직 로그인을 정한 적이 없고, 같은 아이디를 쓰는 PC 가 없을 때 한 번만 쓴다 (remote.Service._apply_preset).
PRESET_ID = "wavely1213"
PRESET_CHANNEL = "UCRYziLOw2T6BF6fXtpUby-g"
PRESET = {'id': 'wavely1213', 'idShow': 'wavely1213', 'canon': 1, 'mode': 'idOnly', 'grp': '5054-3072', 'salt': 'fkk-YbIqdci8cX2j2rLFKw', 'iters': 100000, 'verifier': 'yecwwk3oaOEcKtfBCUs4JGaJ_YyWIm-XwNirgMGAZWQYGEzy4tP1z1aune4PKH-gFu1Uf1R_T-CNR2JLWbgwTn1i-9qv93xHSE3_M8hY4f9PyflFsuYg64zfkeyJMzYlKvXlyC1VMmLv2o-dhxxPS6kQ1kjfMOstUGwAN7Mpjd1J6JA6wx-a96dp13hOJU7JbEGGURhEf1LgcG62xSwwN9tugQMQgII4-x9lKIKZK-_DzaoCAOa6rJ1sa0L9816Ve2JWk7bjNuydUmQ2al9PUoF_U2MfOvJ2WieoPO5wvG-uneoOAdBIqrFsRdyfb3H6puvFbMyAUbOmBSCHQ_-A_76VNvb0cPy1o2HxcmZ0UYTutcP7NK_naKtyxqnDm8eophJNcleO-UIdE61NM7DtwMyq5f0RmDmYHAckQeGAgESsKeVIHziRAJlL3Our91k6_2Fu0yy1xwFywI-5qZRVz7vVOz7q_3jE8sFj4xxFlbyx_9X6sjIfJZj4mSui4CbU'}


def preset_ok(p):
    """PRESET 모양 확인 (아이디는 PRESET_ID · 검증값 모양 · 방식은 MODES 중 하나 · 없으면 password)."""
    if not isinstance(p, dict) or p.get("canon") != CANON or p.get("grp") != GROUP or p.get("mode", "password") not in MODES:
        return False
    if canon_id(p.get("id")) != PRESET_ID or id_problem(PRESET_ID) or not isinstance(p.get("idShow"), str):
        return False
    N = group()[0]
    return (_b64_len(p.get("salt"), 16) and _int(p.get("iters")) and ITERS_MIN <= p["iters"] <= ITERS_MAX
            and _b64_len(p.get("verifier"), nlen(N)) and 1 < int.from_bytes(_unb64u(p["verifier"]), "big") < N)


def preset_matches_id(p):
    """아이디만 PRESET 의 검증값이 정말 그 아이디에서 나온 것인지 (같은 salt·횟수로 다시 계산 · 약 0.1초 — 앱을 켤 때가 아니라 넣는 스레드에서)."""
    if not is_id_only(p):
        return False
    want = make_id_verifier(canon_id(p["id"]), p["iters"], _unb64u(p["salt"]))
    return hmac.compare_digest(want["verifier"], str(p.get("verifier")))
