"""작업 오류 → 쉬운 한국어 한 줄 + 할 일 (화면의 실패 카드 버튼).

yt-dlp·ffmpeg·Windows 의 영어 원문(예: 'ERROR: [youtube:tab] … HTTP Error 404')을 해요체 한 줄로 바꾸고, 사용자가 그 화면에서
혼자 해 볼 수 있는 일(actions)을 붙인다. 원문은 화면에 보이지 않고 studio.log 에만 남는다(studiolog).
- explain(오류, browser, blocked) → {"kind", "msg", "actions"[, "browser", "suggest"]}
- actions: retry(다시 하기) · cookies(로그인 정보로 다시 받기: 브라우저 고르기) · update(업데이트 확인) · folder(폴더 열기)
  · url(주소 다시 넣기) · log(작업 기록 보기) — 어느 버튼을 실제로 보일지는 화면이 작업 종류를 보고 정한다.
- 로그인 정보(쿠키)를 읽을 브라우저: BROWSERS (사용자가 고를 때만 씀 · D-009)
표준 라이브러리만 쓴다 (core·app·editor 가 함수 안에서 불러 씀).
"""
import re

# 로그인 정보(쿠키)를 읽을 수 있게 고르는 브라우저 (yt-dlp cookiesfrombrowser 이름 → 화면 이름)
BROWSERS = {"chrome": "크롬", "edge": "엣지", "whale": "웨일", "firefox": "파이어폭스"}
HANGUL = re.compile(r"[가-힣]")
OURS = re.compile(r"요[.!]?\)?(?:\s*\([^()]*\))?[.!]?$")  # 해요체로 끝나는 우리 안내


class Trouble(RuntimeError):
    """이미 쉬운 말로 바꾼 오류 (화면에 그대로 보여 줌 · kind·actions 포함)."""

    def __init__(self, kind, msg, actions=("retry",)):
        super().__init__(msg)
        self.info = {"kind": kind, "msg": msg, "actions": list(actions)}


def browser(value):
    """화면이 보낸 브라우저 값 → yt-dlp 이름 (없으면 None) · 목록에 없는 값이면 ValueError (프로필 경로 같은 꼴은 받지 않음)."""
    if value in (None, "", False):
        return None
    if isinstance(value, str) and value in BROWSERS:
        return value
    raise ValueError("로그인 정보를 읽을 브라우저를 다시 골라 주세요")


def _j(word, with_final, without):
    """조사: 받침이 있으면 with_final (을·이·은·으로), 없으면 without. 'ㄹ' 받침 + 으로 → 로."""
    c = word[-1] if word else ""
    if not ("가" <= c <= "힣"):
        return word + without
    final = (ord(c) - 0xAC00) % 28
    if with_final == "으로" and final == 8:
        return word + "로"
    return word + (with_final if final else without)


def _name(b):
    return BROWSERS.get(b or "", "브라우저")


def _cookie_locked(b, _t):
    n = _name(b)
    return (f"{_j(n, '이', '가')} 켜져 있어서 로그인 정보를 읽지 못했어요. {n} 창을 모두 닫고 다시 해 보세요 "
            f"(그래도 안 되면 PC를 다시 켠 뒤 {_j(n, '을', '를')} 열지 말고 바로 받아 보세요)")


def _cookie_dpapi(b, _t):
    n = _name(b if b in BROWSERS else "chrome")
    return (f"{n}의 새 보안 기능 때문에 로그인 정보를 읽을 수 없어요. 엣지나 파이어폭스에서 YouTube에 로그인한 뒤 "
            "그 브라우저를 골라 다시 받아 보세요")


def _cookie_missing(b, _t):
    n = _name(b)
    return (f"{n}에서 로그인 정보를 찾지 못했어요. 이 PC에 {_j(n, '이', '가')} 없거나 아직 YouTube에 로그인하지 않았을 수 있어요. "
            "YouTube에 로그인해 둔 브라우저를 골라 주세요")


def _blocked_msg(b, _t):
    if b:
        n = _name(b)
        return (f"{n} 로그인 정보로도 YouTube가 막고 있어요. {n}에서 YouTube에 로그인돼 있는지 확인하고, 잠시 뒤 다시 해 보세요. "
                "다른 브라우저를 골라 봐도 돼요")
    return ("YouTube가 받기를 막고 있어요. 다운로드 엔진을 최신으로 바꿔 다시 해 봤지만 안 됐어요. "
            "YouTube에 로그인해 둔 브라우저를 골라 다시 받아 보세요")


# (종류, 찾을 글, 쉬운 한 줄(글 또는 함수), 할 일, 한국어 원문에 덧붙일지 볼 열쇠말) — 위에서부터 먼저 맞는 것
_RULES = [
    ("cookie_locked", r"[Cc]ould not copy .{0,20}cookie database", _cookie_locked, ["retry", "cookies"], None),
    ("cookie_dpapi", r"DPAPI|App-?Bound|app_bound_encrypted", _cookie_dpapi, ["cookies"], None),
    ("cookie_missing", r"could not find \w+ cookies database|could not find firefox|could not find local state file"
                       r"|unsupported browser|unknown browser|could not find .{0,40}profile",
     _cookie_missing, ["cookies"], None),
    ("disk", r"No space left|Errno 28\b|WinError 112\b|not enough space|디스크 공간이 부족|저장 공간이 부족",
     "저장 공간이 부족해요. 필요 없는 영상을 지워 공간을 비운 뒤 다시 해 주세요", ["folder", "retry"], "저장 공간"),
    ("locked", r"WinError (32|33|5)\b|being used by another process|다른 프로세스가 파일을 사용|Access is denied|액세스가 거부"
               r"|Permission denied|PermissionError",
     "다른 프로그램이 파일을 쓰고 있어서 열지 못했어요. 그 영상을 연 프로그램(동영상 플레이어·탐색기 미리 보기)을 닫고 "
     "다시 해 주세요 (백신이 막았을 수도 있어요)", ["folder", "retry"], "다른 프로그램"),
    ("empty", r"downloaded file is empty",
     "받은 파일이 비어 있어요. YouTube가 잠깐 막은 것 같아요. 잠시 뒤 다시 하거나, [업데이트 확인]으로 다운로드 엔진을 "
     "최신으로 바꾼 뒤 다시 받아 보세요", ["retry", "update", "cookies"], None),
    ("login", r"confirm your age|age[- ]restricted|inappropriate for some users",
     "나이 제한이 있는 영상이라 로그인 정보가 필요해요. YouTube에 로그인해 둔 브라우저를 골라 다시 받아 보세요", ["cookies"], None),
    ("unavailable", r"[Pp]rivate video|video is private|has been removed|members[- ]only|Join this channel|"
                    r"not available in your country|blocked it in your country|live event will begin|Premieres in|"
                    r"no longer available|account .{0,60}terminated|"
                    r"[Vv]ideo unavailable(?!.*try again later)|This video is not available",
     "이 영상은 지금 받을 수 없어요 (비공개·삭제·회원 전용·지역 제한이거나 아직 공개 전이에요)", [], None),
    ("blocked", r"try again later|HTTP Error 429|Too Many Requests|not a bot|Sign in|(?<![\w-])403(?![\w-])|"
                r"YouTube가 (?:계속 |받기를 )?막고",
     _blocked_msg, ["cookies", "update", "retry"], None),
    ("url", r"HTTP Error 404|does not exist|is not a valid URL|Unsupported URL|Incomplete YouTube ID|"
            r"Unable to recognize|not a valid (?:channel|playlist)|This channel is not available",
     "채널이나 영상을 찾지 못했어요. 주소가 맞는지 확인해 주세요 (예: https://www.youtube.com/@채널이름)", ["url", "retry"], None),
    ("server", r"HTTP Error 5\d\d|Internal Server Error|Service Unavailable|Bad Gateway",
     "YouTube 서버에 잠깐 문제가 있어요. 잠시 뒤 다시 해 주세요", ["retry"], None),
    ("cert", r"CERTIFICATE_VERIFY_FAILED|certificate verify failed|SSLCertVerificationError",
     "인터넷 보안 확인(인증서)에 실패했어요. 백신의 'HTTPS 검사'를 잠시 끄거나 다른 인터넷(휴대폰 핫스팟 등)으로 다시 해 보세요",
     ["retry", "log"], "인증서"),
    ("net", r"getaddrinfo|Name or service not known|name resolution|nodename nor servname|Failed to resolve|"
            r"No address associated|timed out|[Tt]imeout|Connection (?:refused|reset|aborted)|Network is unreachable|"
            r"WinError 100(?:51|53|54|60|61|65)\b|WinError 11001|Errno 11001|urlopen error|Unable to download (?:webpage|API page)|"
            r"IncompleteRead|Remote end closed|RemoteDisconnected|TransportError",
     "인터넷에 연결하지 못했어요. 인터넷 연결을 확인한 뒤 다시 해 주세요", ["retry"], "인터넷"),
    ("engine", r"Requested format is not available|No video formats found|Unable to extract|nsig extraction failed",
     "YouTube가 바뀌어서 지금 엔진으로는 받을 수 없어요. [업데이트 확인]으로 다운로드 엔진을 최신으로 바꾼 뒤 다시 해 주세요",
     ["update", "retry"], None),
    ("broken", r"moov atom not found|Invalid data found when processing input|could not find codec parameters|"
               r"End of file|Truncated|invalid STSD",
     "영상 파일이 끝까지 없거나 깨졌어요 (복사가 덜 됐을 수 있어요). 파일을 다시 넣은 뒤 다시 해 주세요", ["folder", "retry"], "깨졌"),
    ("ffmpeg", r"ffmpeg|ffprobe|Conversion failed|Postprocessing|Error while decoding|Error opening (?:input|output)",
     "영상 변환 도구(ffmpeg)가 이 영상을 처리하지 못했어요. 다른 동영상 플레이어에서 열리는지 확인하고, [업데이트 확인]으로 "
     "프로그램을 최신으로 바꾼 뒤 다시 해 주세요", ["retry", "update", "folder"], "ffmpeg"),
]
_COMPILED = [(k, re.compile(p), m, a, key) for k, p, m, a, key in _RULES]
OTHER = "예상하지 못한 문제가 생겼어요. 다시 해 보고, 계속되면 작업 폴더의 studio.log 파일을 관리자에게 보내 주세요"
CANCELLED = re.compile(r"멈췄어요|멈출게요")
MEMORY = "메모리가 부족해요. 다른 프로그램을 닫고 다시 해 주세요"


def korean_head(text):
    """우리 한국어 안내 + ' · 영어 원문' 꼴에서 우리 안내만 ('…요'로 끝나는 앞 토막들 · 없으면 '').
    Windows 의 한국어 오류('[WinError 32] … 없습니다: 경로')나 한글 파일 이름이 든 영어 원문은 우리 안내가 아님."""
    out = []
    for p in (x.strip() for x in str(text or "").split(" · ")):
        if not (HANGUL.search(p) and OURS.search(p)) or p.startswith(("[", "ERROR", "WARNING")):
            break
        out.append(p)
    return " · ".join(out).strip()


def _one_line(s, n=400):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def explain(err, browser=None, blocked=None):
    """작업 오류(예외 또는 글) → {"kind", "msg"(해요체 한 줄), "actions"[...]} (+ YouTube·쿠키 오류면 "browser"·"suggest").
    browser: 사용자가 고른 로그인 정보 브라우저(있으면 안내에 이름을 넣음) · blocked: 이 화면용 'YouTube가 막음' 안내 문구.
    우리 한국어 안내 + 영어 원인(' · ' 뒤)이면: 원인 종류의 할 일을 붙이고, 원인 안내가 우리 안내에 없으면 뒤에 덧붙인다."""
    if isinstance(err, Trouble):
        return dict(err.info)
    if isinstance(err, MemoryError):
        return {"kind": "memory", "msg": MEMORY, "actions": ["retry"]}
    text = (str(err) or type(err).__name__) if isinstance(err, BaseException) else str(err or "")
    if isinstance(err, KeyError) and not HANGUL.search(text):
        text = f"KeyError {text}"
    head = korean_head(text)
    if head and CANCELLED.search(head):  # 사용자가 멈춤(✕) — 실패가 아님
        return {"kind": "cancelled", "msg": _one_line(head), "actions": []}
    for kind, rx, msg, actions, key in _COMPILED:
        if not rx.search(text):
            continue
        if kind == "blocked" and blocked and not browser:
            line = blocked
        else:
            line = msg(browser, text) if callable(msg) else msg
        if head and key:  # 파일·인터넷 같은 환경 원인: 우리 안내를 앞에 두고, 원인 안내가 없으면 덧붙임
            line = head if key in head else f"{head} · {line}"
        info = {"kind": kind, "msg": _one_line(line), "actions": list(actions)}
        if kind.startswith("cookie_") or kind in ("blocked", "login", "empty"):
            info["browser"] = browser
            if kind == "cookie_dpapi":  # 다른 브라우저를 권함 (엣지는 Windows 에 늘 있음)
                info["suggest"] = "firefox" if browser == "edge" else "edge"
        return info
    if head:  # 이미 한국어 안내 (예: 소리를 꺼내지 못했어요 · 멈췄어요)
        return {"kind": "other", "msg": _one_line(head), "actions": ["retry", "log"]}
    return {"kind": "other", "msg": OTHER, "actions": ["retry", "log"]}
