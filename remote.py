"""원격 접속 '휴대폰으로 보기' — 원격 경계 (D-027).

PC 에서 하는 작업을 휴대폰(https://mulgyeol.kr/futsal)에서 보고, 정해진 몇 가지 일을 시키는 곳. 바깥(터널)에서 온 요청은 이 모듈만 받는다.
  - 원격 리스너: 켜 둔 동안만 127.0.0.1:<임의 포트> 에 따로 뜨는 작은 서버(/r/* 만 · 앱 화면 포트 창 app_ports 밖). 화면용 로컬 서버(8765)는
    터널 뒤에 두지 않는다.
  - 확인 순서: Host(remote.futsal.invalid) → 메서드 → 요청 수 제한 → CORS·Origin → 서명/표(ticket) → JSON → 허용 동작 목록 → 파일 이름·허용 폴더.
  - 짝짓기: PC 가 만든 10분짜리 한 번 쓰는 코드 → 휴대폰은 코드 대신 코드로 만든 증명(HMAC)만 보냄 → 기기 열쇠 2개(서명용·비콘용)를
    코드로 만든 열쇠로 잠가 돌려줌 (터널·Cloudflare 는 코드도 열쇠도 못 봄) → ~/.futsal-studio/remote.json.
  - 비콘: 빠른 터널 주소는 켤 때마다 바뀜 → 지금 주소를 기기마다 AES-GCM 으로 잠가 ntfy 주제에 올림 (휴대폰 페이지가 찾아옴).
  - 알림: 정해진 문장만 ntfy 알림 주제로 (파일 이름·제목·경로·오류 글 없음).
app 을 import 하지 않는다 — app 이 Bridge 로 기록·작업 시작 같은 기능을 넘겨준다. 암호 부품(pycryptodomex)은 함수 안에서만 불러온다
(없으면 원격 접속만 켜지지 않음 · 앱과 업데이트 import 확인은 그대로).
"""
import base64
import collections
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import core
import editor
import qa
import refs
import source
import strategy
import style
import trouble
import tunnel
import updater
import youtube_upload

API = 1
SENTINEL = "remote.futsal.invalid"  # cloudflared 가 원격 리스너로 보낼 때 쓰는 Host (.invalid 는 어떤 브라우저도 만들 수 없음)
SITE = "https://mulgyeol.kr/futsal"
ORIGINS = ("https://mulgyeol.kr", "https://www.mulgyeol.kr", "https://futsal.mulgyeol.kr")  # 마지막은 따로 떼어 낼 자리 (D-027 소유자 결정 a)
NTFY_DEFAULT = "https://ntfy.sh"
MAX_DEVICES = 5
DEVICE_TTL = 90 * 86400      # 90일 동안 안 쓴 휴대폰은 저절로 끊음
CODE_TTL = 600               # 연결 코드는 10분
CODE_FAILS = 5               # 한 코드에 틀린 시도 5번이면 그 코드는 버림
PAIR_FAILS_HOUR = 20         # 한 시간에 짝짓기 실패가 이만큼이면 PC 에서 새 코드를 만들 때까지 짝짓기 막음
PAIR_SALT, PAIR_ITER = b"futsal-remote/pair/v1", 200_000
SKEW = 300                   # 서명 시각 허용 차이(초)
NONCE_TTL = 600
NONCE_MAX = 4000             # 기기마다 기억하는 nonce 수 (꽉 차면 지우지 않고 거절 · 서명 길 초당 5개 × 10분보다 큼)
TICKET_TTL, TICKET_REUSE, TICKET_MAX = 3600, 1200, 4000  # 표는 1시간 · 20분 넘게 남았으면 같은 표 · 받은 곳(IP 대역)에 묶음
BODY_MAX = 64 * 1024
MEDIA_CHUNK = 4 << 20        # 구간 요청 한 번에 내주는 최대 크기
CONCURRENCY = 24
MEDIA_PER_DEVICE, MEDIA_TOTAL = 6, 10
ACTION_GAP = 2.0             # 기기마다 동작 요청 간격(초)
NOTIFY_TEST_GAP = 60         # 휴대폰의 '알림 시험'은 1분에 한 번
STALE_DAYS = 14              # PC 목록에 '오래 안 씀'
RETRY_AFTER = (120, 300, 900, 1800)  # 터널 오류 뒤 저절로 다시 켜기 (그 뒤로는 30분마다)
AUTH_FAIL_MAX, AUTH_FAIL_WINDOW, LOCKOUT = 10, 600, 900
LASTSEEN_SAVE = 300          # 마지막 사용 시각은 5분마다만 파일에 씀
LOG_TAIL = 200
BEACON_GAP, HEARTBEAT = 60, 1200
DAILY_BUDGET, BUDGET_SOFT = 200, 180   # ntfy.sh 무료는 하루 약 250개 · 180개를 넘으면 상태 바뀜·확인 필요만
RETRY_DELAYS = (2.0, 5.0)              # 보내기 실패 → 이만큼 쉬고 다시 (모두 3번)
AUTO_OFF_CHOICES = (0, 1, 3, 12, 24)
INSTALL_RE = re.compile(r"[A-Za-z0-9_-]{16,43}")  # 휴대폰 브라우저마다 하나 (비밀 아님 · 다시 연결하면 옛 항목을 바꿔 끼움)
PREVIEW_KEEP = 10
CROCK = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (I·L·O·U 없음)
CONFIRM_TTL = 120
BUCKETS = {"open": (10, 1 / 6), "auth": (60, 5.0)}  # (최대, 초당 채움)
LRU_MAX = 10000
MISSING_MSG = "원격 접속에 필요한 구성요소가 없어요 · 왼쪽 아래 '업데이트 확인' → 다운로드 엔진 최신으로 바꾼 뒤 다시 켜 주세요"
BUSY_MSG = "다른 작업이 끝난 뒤에 다시 눌러 주세요"
MEDIA_TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".txt": "text/plain; charset=utf-8", ".srt": "text/plain; charset=utf-8"}
OUT_KINDS = {".mp4": "video", ".jpg": "image", ".jpeg": "image", ".png": "image", ".txt": "text", ".srt": "srt"}
# 알림에 넣어도 되는 작업 이름 (나머지는 '작업') — 파일 이름·제목은 절대 넣지 않음
JOB_LABELS = {"보관함에 담기", "편집점 찾기", "학습용 영상 받기", "학습용 스타일 배우기", "자동 가편집", "내보내기", "영상 검수",
              "작은 미리보기 만들기", "스타일 배우기", "채널 불러오기", "한 영상으로 묶기", "장면 고르기", "누끼 따기",
              "미리보기 파일 만들기", "업데이트", "스타일 일치 점수", "클로드로 더 깊게 보기", "러프컷 만들기",
              "학습용 영상 지우기", "배운 영상 파일 지우기", "보관함으로 되돌리기", "학습용으로 옮기기",
              # 채널 전략 (PC 에서만 시작 · 휴대폰에는 진행·알림·멈추기만 · D-028)
              strategy.JOB_REFRESH, strategy.JOB_OWN, strategy.JOB_CHECK, strategy.JOB_AI,
              # 유튜브에 바로 올리기 (PC 7단계에서만 시작 · 휴대폰에는 진행('유튜브에 올리는 중')·알림·멈추기만 · D-048)
              youtube_upload.JOB_NAME, youtube_upload.JOB_FINISH}
OFF_REASONS = {"app": "앱을 껐어요", "user": "원격 접속을 껐어요", "idle": "오래 쓰지 않아서 껐어요", "error": "연결이 끊겼어요"}
NOTE_TEXT = {
    "paired": "새 휴대폰이 연결됐어요 · 내가 한 게 아니면 PC의 '휴대폰으로 보기'에서 [끊기]를 눌러 주세요",
    "test": "알림 시험이에요 · 잘 받았다면 준비 끝!",
    "tunnel": "확인이 필요해요 · 원격 연결이 자꾸 끊겨요",
    "blocked": "확인이 필요해요 · YouTube가 막았어요",
    "missed": "확인이 필요해요 · 받지 못한 영상이 있어요",
    "partial": "확인이 필요해요 · 편집점을 찾지 못한 영상이 있어요",
    "net": "확인이 필요해요 · PC 인터넷이 끊겨 다 끝내지 못했어요",
    "moved": "알림 주제가 바뀌었어요 · 이 주제로는 더 이상 알림이 오지 않아요 · mulgyeol.kr/futsal 에서 새 주제로 다시 구독해 주세요 "
             "(스튜디오 알림은 무엇을 설치하라고 하지 않아요)",
}
MISSED_MSG = ("받지 못한 영상이 {n}개 있어요 · YouTube가 막았을 수 있어요 · PC에서 '로그인 정보로 받기'에서 YouTube에 로그인해 둔 "
              "브라우저를 골라 다시 받아 주세요")
MISSED_WHY_MSG = "받지 못한 영상이 {n}개 있어요 · {why}"  # 까닭을 아는 받기 (trouble.explain · 막힘이면 PC 에서 할 일까지 들어 있음)
PARTIAL_MSG = "편집점을 찾지 못한 영상이 {n}개 있어요 · {why}"  # 여러 영상 중 그 파일만의 문제(깨짐 등)로 건너뜀 (core.analyze_many)
NOT_STOPPABLE_MSG = "이 작업은 중간에 멈출 수 없어요 · 끝나면 알려 드릴게요"
BLOCKED_PHONE_MSG = ("YouTube가 막았어요 · PC 스튜디오에서 '업데이트 확인'으로 다운로드 엔진을 최신으로 바꾸거나 "
                     "'로그인 정보로 받기'에서 YouTube에 로그인해 둔 브라우저를 골라 다시 받아 주세요")
# 채널 전략 새로 고침·점검은 멈추거나 막혀도 ok:true (끝난 채널은 저장) → 휴대폰에는 stopped·blocked·net 으로 '멈췄어요'·'확인이 필요해요' (D-028)
STRATEGY_REFRESHING = {strategy.JOB_REFRESH, strategy.JOB_OWN, strategy.JOB_CHECK}
STRATEGY_MSG = {
    "stopped": "멈췄어요 · 끝난 채널은 저장했어요 · 나머지는 PC 8단계 '채널 전략'에서 다시 새로 고쳐 주세요",
    "stopped_check": "멈췄어요 · 그때까지 받은 숫자로 점검했어요 · PC 8단계 '채널 전략'에서 확인해 주세요",
    "blocked": "YouTube가 잠시 막아 6시간 쉬어요 · 남은 채널은 최근 날짜·조회수만 새로 고쳤어요 · PC 8단계 '채널 전략'에서 확인해 주세요",
    "net": "PC 인터넷이 끊겨 일부 채널만 새로 고쳤어요 · 인터넷을 확인한 뒤 PC 8단계 '채널 전략'에서 다시 새로 고쳐 주세요",
}
# 유튜브 올리기 끝 — 휴대폰 '마지막 작업' 칸·알림에는 이 정해진 문장만 (영상 주소·번호·토큰·Google 오류 글은 넣지 않음 · D-048)
YOUTUBE_JOBS = {youtube_upload.JOB_NAME, youtube_upload.JOB_FINISH}
YOUTUBE_MSG = {
    "done": "유튜브에 올렸어요 · PC 7단계에서 확인해 주세요",
    "finish": "유튜브 마무리를 끝냈어요 · PC 7단계에서 확인해 주세요",
    "warn": "유튜브에 올렸지만 확인이 필요해요 · PC 7단계에서 남은 것을 봐 주세요",
    "paused": "유튜브 올리기를 멈췄어요 · PC 7단계에서 [이어 올리기]를 눌러 주세요",
    "relogin": "유튜브 연결이 끊겼어요 · PC 7단계에서 다시 연결해 주세요",
    "failed": "유튜브에 올리지 못했어요 · PC 7단계에서 확인해 주세요",
    # 실패 카드 종류별 (trouble.YT_CARDS · D-049) — 이것도 정해진 문장만 (남은 시각·주소·Google 원문 없음)
    "quota": "유튜브 올리기를 멈췄어요 · 오늘 쓸 수 있는 유튜브 API 양을 다 썼어요 · 한국 시각 오후 4~5시가 지나면 PC 7단계에서 [이어 올리기]를 눌러 주세요",
    "net": "유튜브 올리기를 멈췄어요 · PC 인터넷이 끊겼거나 유튜브 서버가 불안정해요 · PC 7단계에서 [이어 올리기]를 눌러 주세요",
    "thumb": "유튜브에 올렸지만 썸네일은 막혔어요 · 채널 인증(전화번호 확인)이 필요해요 · PC 7단계에서 확인해 주세요",
    "quota_left": "유튜브에 올렸지만 오늘 API 양을 다 써서 남은 것이 있어요 · 한국 시각 오후 4~5시가 지나면 PC 7단계에서 [마저 하기]를 눌러 주세요",
}
# trouble 카드 종류 → 휴대폰 문장 열쇠 (실패: 영상이 안 올라감 · 남음: 영상은 올라가고 마무리가 남음)
YOUTUBE_FAIL_KEY = {"yt_quota": "quota", "yt_limit": "quota", "yt_relogin": "relogin", "yt_forbidden": "relogin",
                    "yt_relogin_left": "relogin", "yt_net": "net", "yt_server": "net"}
YOUTUBE_LEFT_KEY = {"yt_thumb": "thumb", "yt_quota_left": "quota_left", "yt_relogin_left": "relogin"}
STALE_CANCEL_MSG = "보던 작업은 이미 끝났어요 · 지금 작업은 멈추지 않았어요"
# 멈추기(editor.CANCEL·ffmpeg 끄기)를 보는 작업 — 그 밖의 작업은 휴대폰에서 [멈추기]를 보여 주지 않음
STOPPABLE = {"내보내기", "미리보기 파일 만들기", "작은 미리보기 만들기", "영상 검수", "스타일 배우기", "학습용 스타일 배우기",
             "스타일 일치 점수", "클로드로 더 깊게 보기",
             strategy.JOB_REFRESH, strategy.JOB_OWN, strategy.JOB_CHECK, strategy.JOB_AI,  # 채널 전략: 모두 editor.CANCEL 을 봄 (PC 8단계 [멈추기]와 같음)
             youtube_upload.JOB_NAME, youtube_upload.JOB_FINISH}  # 유튜브: 조각마다 editor.CANCEL 을 보고 기다리던 응답도 끊음 → 멈춘 데부터 [이어 올리기]
# 줄바꿈·제어·방향 바꾸는 글자 + 줄을 나누는 유니코드(NEL·줄/문단 구분)·폭 없는 글자 → 기록에 가짜 줄을 못 만들게
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")


class CryptoMissing(RuntimeError):
    pass


class AuthError(Exception):
    def __init__(self, code, msg, status=401):
        super().__init__(msg)
        self.code, self.status = code, status


class PairError(Exception):
    def __init__(self, msg, code="pair"):
        super().__init__(msg)
        self.code = code


class ActionError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ---------- 작은 도구 ----------

def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64u(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _dev():
    return os.environ.get("FUTSAL_REMOTE_DEV") == "1"


def clean(s, n=80):
    """기기 이름·파일 이름을 기록·화면용으로: 줄바꿈·제어·방향 바꾸는 글자를 빼고 n 글자까지."""
    return _CTRL.sub("", str(s or "").replace("\r", " ").replace("\n", " ")).strip()[:n]


# 기록에 남으면 안 되는 것: 터널 주소 · 비콘/알림 주제 · 영상·그림 표(ticket) · 서명 머리글 · 연결 코드와 터널 힌트
_SECRETS = (
    (re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I), "https://….trycloudflare.com"),
    (re.compile(r"\bfs[bnp][0-9a-f]{24}\b"), "fs…"),  # 비콘(fsb)·알림(fsn)·짝짓기 만남(fsp, derive_pair) 주제
    (re.compile(r"/r/m/[A-Za-z0-9_-]+"), "/r/m/…"),
    (re.compile(r"FSR2 [^\s'\"]+"), "FSR2 …"),
    (re.compile(r"([#&?]pair=)[^&\s'\"]+"), r"\1…"),
    (re.compile(r"([#&?]u=)[^&\s'\"]+"), r"\1…"),
    # 유튜브 바로 올리기 (D-048): 업로드 세션 주소(upload_id)·로그인 코드·state·PKCE·토큰·클라이언트 보안 비밀번호
    (re.compile(r"([?&](?:upload_id|code|state|code_verifier|access_token|refresh_token|client_secret|token)=)[^&\s'\"]+"), r"\1…"),
    # 주소 밖에 놓인 같은 이름(기록 글·예외 글 · 'upload_id=…') — code·state·token 처럼 흔한 낱말은 주소 안에서만 (D-049)
    (re.compile(r"\b((?:upload_id|code_verifier|access_token|refresh_token|client_secret)\s*[=:]\s*)[^&\s'\",}]+"), r"\1…"),
    (re.compile(r"\bya29\.[A-Za-z0-9._~+/=-]+"), "ya29.…"),
    (re.compile(r"\b1//[A-Za-z0-9._~+/=-]+"), "1//…"),
    (re.compile(r"\bGOCSPX-[A-Za-z0-9_-]+"), "GOCSPX-…"),
    (re.compile(r"\b(Bearer )[^\s'\"]+"), r"\1…"),
    # JSON('"…": "…"')·파이썬 사전 repr("{'access_token': '…'}") 둘 다 — 요청 값·응답을 통째로 찍은 예외 글 (D-049 보강)
    (re.compile(r'("(?:access_token|refresh_token|client_secret|code_verifier|uri)"\s*:\s*")[^"]*'), r"\1…"),
    (re.compile(r"('(?:access_token|refresh_token|client_secret|code_verifier|uri)'\s*:\s*')[^']*"), r"\1…"),
    # Google 로그인 코드(4/0…) — 주소 밖(요청 값 repr 등)에 놓여도
    (re.compile(r"\b4/0[A-Za-z0-9_-]{10,}"), "4/0…"),
)


def redact(s):
    """오류·추적 글에서 비밀(터널 주소·주제·표·서명·연결 코드 · 유튜브 토큰·세션 주소·로그인 코드)을 '…'로
    (studio.log·studio-error.log·휴대폰 기록 모두 · app.log·start_job 도 이것을 거침)."""
    s = str(s if s is not None else "")
    for rx, to in _SECRETS:
        s = rx.sub(to, s)
    return s


def scrub(s):
    """휴대폰에 보내는 기록·오류 글: 사용자 폴더 경로(Windows 사용자 이름이 들어감)를 '~' 로, 제어 글자는 뺌, 비밀은 '…'로."""
    s = redact(s)
    home = str(Path.home())
    if len(home) > 3:
        s = s.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return _CTRL.sub("", s.replace("\r", ""))


def _trace():
    """방금 난 오류를 기록 — studiolog.trace 하나로 (D-044): studio.log 에 '휴대폰 연결 오류 위치' 한 줄 + 오류 출력(pythonw 면
    app._error_log 의 studio-error.log)에 traceback 전체. 둘 다 비밀(redact)은 지움 (예전에는 버려졌던 출력이라 원격 쪽 추적에
    표·주소가 섞여도 몰랐음 · D-034)."""
    try:
        import studiolog
        studiolog.trace(sys.exc_info()[1], "휴대폰 연결 오류 위치")
    except Exception:  # noqa: BLE001 — 기록 실패가 요청·끄기를 막지 않게
        pass


def _missed(result):
    """받기 작업 결과 → 못 받은 영상 id 목록 (휴대폰 받기는 목록 · PC 화면 받기는 {failed, why})."""
    if isinstance(result, dict):
        result = result.get("failed")
    return result if isinstance(result, list) else []


def _first_why(result, failed):
    """{failed, why} 결과 → 처음 실패한 영상의 쉬운 까닭 한 줄 (없으면 '')."""
    why = result.get("why") if isinstance(result, dict) else None
    if not isinstance(why, dict) or not failed:
        return ""
    first = why.get(failed[0])
    return str(first.get("msg") or "") if isinstance(first, dict) else ""


def _lru_put(d, k, v, cap=LRU_MAX):
    d[k] = v
    d.move_to_end(k)
    while len(d) > cap:
        d.popitem(last=False)


# ---------- 암호 (WebCrypto 와 같은 꼴: nonce(12) ‖ ct ‖ tag(16), base64url) ----------

def _aes():
    try:
        from Cryptodome.Cipher import AES  # pycryptodomex — yt-dlp[default] 와 함께 설치됨 (DEPENDENCY_POLICY 6번)
    except ImportError:
        raise CryptoMissing(MISSING_MSG) from None
    return AES


def crypto_ok():
    try:
        _aes()
        return True
    except CryptoMissing:
        return False


def gcm_seal(key, pt, aad, nonce=None):
    AES = _aes()
    nonce = nonce or secrets.token_bytes(12)
    c = AES.new(key, AES.MODE_GCM, nonce=nonce)
    c.update(aad if isinstance(aad, bytes) else aad.encode("utf-8"))
    ct, tag = c.encrypt_and_digest(pt if isinstance(pt, bytes) else pt.encode("utf-8"))
    return _b64u(nonce + ct + tag)


def gcm_open(key, blob, aad):
    AES = _aes()
    raw = _unb64u(blob)
    if len(raw) < 28:
        raise ValueError("짧은 암호문")
    c = AES.new(key, AES.MODE_GCM, nonce=raw[:12])
    c.update(aad if isinstance(aad, bytes) else aad.encode("utf-8"))
    return c.decrypt_and_verify(raw[12:-16], raw[-16:])


def derive_pair(code):
    """코드 → (만남 주제, 잠금 열쇠 32바이트, 증명 열쇠 16바이트). 페이지(proto.js)와 같은 계산
    (PBKDF2-SHA256 64바이트 = 블록 2개 · 블록을 늘리면 휴대폰·PC 계산이 그만큼 길어짐)."""
    dk = hashlib.pbkdf2_hmac("sha256", code.encode("ascii"), PAIR_SALT, PAIR_ITER, 64)
    return "fsp" + dk[:12].hex(), dk[16:48], dk[48:64]


def pair_proof(proof_key, nonce, ts):
    """휴대폰이 코드를 안다는 증명 (코드 글은 터널로 보내지 않음)."""
    return _b64u(hmac.new(proof_key, f"fsp2|{nonce}|{ts}".encode("ascii"), hashlib.sha256).digest())


def pair_aad(topic, pc_id, dev_id):
    return f"fsp2|{topic}|{pc_id}|{dev_id}"


def topics_aad(pc_id, dev_id):
    return f"fst1|{pc_id}|{dev_id}"


def canonical(host, method, path, ts, nonce, body):
    """서명할 글: 받는 곳(터널 주소의 host) · 메서드 · 경로+쿼리 · 시각 · nonce · 본문 sha256 — 다른 주소로 보낸 서명은 여기서 안 맞음."""
    return f"FSR2\n{host}\n{method}\n{path}\n{ts}\n{nonce}\n{hashlib.sha256(body or b'').hexdigest()}".encode("utf-8")


def sign(key, host, method, path, ts, nonce, body=b""):
    return _b64u(hmac.new(key, canonical(host, method, path, ts, nonce, body), hashlib.sha256).digest())


def net_of(ip):
    """표를 묶는 받은 곳: IPv4 /24 · IPv6 /48 (휴대폰 IP 가 조금 바뀌어도 되게) · 이상한 글은 그대로."""
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return str(ip)[:64]
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return str(ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 48}", strict=False))


# ---------- 연결 코드 ----------

def new_code():
    return "".join(secrets.choice(CROCK) for _ in range(10))


def fmt_code(c):
    return f"{c[:4]}-{c[4:8]}-{c[8:]}"


def norm_code(s):
    """사람이 친 코드 → 10글자 (대문자, -·띄어쓰기 빼고, O→0, I·L→1) · 틀린 꼴이면 None."""
    s = re.sub(r"[\s\-]", "", str(s or "")).upper().replace("O", "0").replace("I", "1").replace("L", "1")
    return s if len(s) == 10 and all(ch in CROCK for ch in s) else None


# ---------- 주소 검사 (원격으로 받을 수 있는 것만) ----------

_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


def youtube_id(url):
    """YouTube 영상 주소 → 영상 id 11글자 (그 밖은 ValueError). 원래 글은 yt-dlp 에 넘기지 않는다."""
    s = str(url or "").strip()
    if not s or len(s) > 300:
        raise ValueError("YouTube 영상 주소를 넣어 주세요")
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    u = urlparse(s)
    host = (u.hostname or "").lower()
    if u.scheme.lower() not in ("http", "https") or u.username or u.password or u.port not in (None, 80, 443):
        raise ValueError("YouTube 영상 주소를 넣어 주세요")
    vid = None
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif host in _YT_HOSTS:
        if u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [""])[0]
        else:
            m = re.fullmatch(r"/(shorts|live|embed)/([^/]+)/?", u.path)
            vid = m.group(2) if m else None
    if not vid or not _YT_ID.fullmatch(vid):
        raise ValueError("YouTube 영상 하나의 주소만 받을 수 있어요 (재생목록·채널 주소는 안 돼요)")
    return vid


def channel_url(s):
    """학습용 채널 주소: @핸들 · youtube.com/@핸들 · /channel/UC… · /c/이름 · /user/이름 만 → 새로 만든 표준 주소."""
    s = str(s or "").strip()
    if not s or len(s) > 300:
        raise ValueError("채널 주소나 @핸들을 넣어 주세요")
    name_re = r"[\w.\-·]{1,100}"
    if s.startswith("@"):
        h = unquote(s[1:])
        if re.fullmatch(name_re, h):
            return "https://www.youtube.com/@" + quote(h)
        raise ValueError("채널 주소나 @핸들을 넣어 주세요")
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    u = urlparse(s)
    if (u.hostname or "").lower() not in _YT_HOSTS or u.username or u.password or u.port not in (None, 80, 443):
        raise ValueError("YouTube 채널 주소만 받을 수 있어요")
    p = unquote(u.path).rstrip("/")
    for pat, fmt in ((r"/@(" + name_re + r")(/(videos|shorts|featured))?", "@{}"), (r"/channel/(UC[A-Za-z0-9_-]{22})(/\w+)?", "channel/{}"),
                     (r"/c/(" + name_re + r")(/\w+)?", "c/{}"), (r"/user/(" + name_re + r")(/\w+)?", "user/{}")):
        m = re.fullmatch(pat, p)
        if m:
            return "https://www.youtube.com/" + fmt.format(quote(m.group(1)))
    raise ValueError("YouTube 채널 주소만 받을 수 있어요")


# ---------- 저장 (~/.futsal-studio/remote.json) ----------

def _fresh():
    return {"v": 1, "pc": {"id": secrets.token_hex(8), "name": "내 PC"}, "enabled": False,
            # PC 잠들지 않게: 기본은 '켜 둔 동안 항상' (잠들면 밖에서 새 작업을 못 시킴 · 원격 접속 자체가 소유자가 켜는 것)
            "settings": {"autoOffHours": 12, "keepAwake": "always", "notify": {"done": True, "failed": True, "attention": True}},
            "devices": [], "topics": {"beacon": "fsb" + secrets.token_hex(12), "notify": "fsn" + secrets.token_hex(12)}, "seq": 0}


def _valid_store(d):
    try:
        ok = (d.get("v") == 1 and re.fullmatch(r"[0-9a-f]{16}", d["pc"]["id"]) and isinstance(d["devices"], list)
              and re.fullmatch(r"fsb[0-9a-f]{24}", d["topics"]["beacon"]) and re.fullmatch(r"fsn[0-9a-f]{24}", d["topics"]["notify"])
              and isinstance(d.get("seq"), int) and isinstance(d.get("settings"), dict))
        for x in d["devices"]:
            ok = ok and re.fullmatch(r"[0-9a-f]{16}", x["id"]) and len(_unb64u(x["auth"])) == 32 and len(_unb64u(x["beacon"])) == 32
            ok = ok and (x.get("install") is None or bool(INSTALL_RE.fullmatch(str(x["install"]))))
        return bool(ok)
    except (KeyError, TypeError, AttributeError, ValueError):
        return False


class Store:
    """기기 열쇠·알림 주제가 든 비밀 파일. 앱 폴더·config.json 밖(사용자 폴더), 바꿔 끼우기로 저장, 깨지면 .bad 로 남기고 새로."""

    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.lock = threading.RLock()
        self._saved_seen = 0.0
        self.data = self._load()

    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not _valid_store(d):
                raise ValueError
        except FileNotFoundError:
            return _fresh()
        except (OSError, ValueError):
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass
            return _fresh()
        base = _fresh()
        base["settings"].update({k: v for k, v in d["settings"].items() if k in base["settings"]})
        d["settings"] = base["settings"]
        return d

    def save(self):
        """공통 저장 도구(updater.write_atomic): 임시 파일을 나만 읽게(0600 · Windows 는 무시) 해 둔 뒤 바꿔 끼움 ·
        백신·OneDrive 가 잠깐 잡으면 기다렸다 다시 · 실패하면 열쇠가 든 임시 파일을 지우고 오류를 올려 보냄."""
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            updater.write_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=1), mode=0o600)

    def device(self, did):
        return next((x for x in self.data["devices"] if x["id"] == did), None)

    def add_device(self, name, install=None):
        """새 기기 → (기기, 바꿔 끼운 옛 기기들). 같은 브라우저(install)가 다시 연결하면 옛 항목을 지우고 새것으로."""
        with self.lock:
            now = int(self.clock())
            dev = {"id": secrets.token_hex(8), "name": clean(name, 40) or "휴대폰", "created": now, "lastSeen": now,
                   "auth": _b64u(secrets.token_bytes(32)), "beacon": _b64u(secrets.token_bytes(32))}
            old = []
            if install and INSTALL_RE.fullmatch(install):
                dev["install"] = install
                old = [x for x in self.data["devices"] if x.get("install") == install]
            self.data["devices"] = [x for x in self.data["devices"] if x not in old] + [dev]
            self.save()
            return dev, old

    def remove(self, ids):
        with self.lock:
            before = len(self.data["devices"])
            self.data["devices"] = [x for x in self.data["devices"] if x["id"] not in ids]
            if len(self.data["devices"]) != before:
                self.save()
            return before - len(self.data["devices"])

    def rotate_topics(self, notify=True):
        """비콘 주제는 늘, 알림 주제는 notify 일 때만 새로 → 옛 주제들."""
        with self.lock:
            old = dict(self.data["topics"])
            self.data["topics"] = {"beacon": "fsb" + secrets.token_hex(12),
                                   "notify": "fsn" + secrets.token_hex(12) if notify else old["notify"]}
            self.save()
            return old

    def touch(self, dev):
        now = int(self.clock())
        dev["lastSeen"] = now
        if now - self._saved_seen >= LASTSEEN_SAVE:
            self._saved_seen = now
            try:
                self.save()
            except OSError:
                pass

    def expired(self):
        now = self.clock()
        return [x["id"] for x in self.data["devices"] if now - (x.get("lastSeen") or x.get("created") or 0) > DEVICE_TTL]

    def next_seq(self):
        with self.lock:
            self.data["seq"] = int(self.data.get("seq") or 0) + 1
            self.save()
            return self.data["seq"]

    def set(self, **kw):
        with self.lock:
            self.data.update(kw)
            self.save()


# ---------- 서명 확인 ----------

_AUTH_RE = re.compile(r"FSR2 ([0-9a-f]{16})\.(\d{1,12})\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{43})")


class Auth:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.nonces = {}
        self.lock = threading.Lock()

    def verify(self, header, method, path, body, host=""):
        """host: 이 PC 의 지금 터널 주소 host (서명에 들어 있음 → 다른 주소로 보낸 서명을 옮겨 와도 안 맞음)."""
        m = _AUTH_RE.fullmatch(str(header or "").strip())
        if not m:
            raise AuthError("bad_header", "연결 정보가 없거나 잘못됐어요")
        did, ts, nonce, sig = m.groups()
        dev = self.store.device(did)
        if not dev:
            raise AuthError("unknown_device", "이 휴대폰은 연결이 끊겼어요 · PC에서 다시 연결해 주세요")
        now = self.clock()
        if abs(now - int(ts)) > SKEW:
            raise AuthError("skew", "휴대폰 시계가 PC와 너무 달라요")
        want = sign(_unb64u(dev["auth"]), host or "", method, path, ts, nonce, body)
        if not hmac.compare_digest(want, sig):
            raise AuthError("bad_sig", "연결 정보가 맞지 않아요")
        with self.lock:
            seen = self.nonces.setdefault(did, collections.OrderedDict())
            while seen and next(iter(seen.values())) < now:
                seen.popitem(last=False)
            if nonce in seen:
                raise AuthError("replay", "같은 요청이 두 번 왔어요")
            if len(seen) >= NONCE_MAX:  # 아직 살아 있는 nonce 는 지우지 않음 (지우면 그 요청을 다시 쓸 수 있음) → 잠깐 거절
                raise AuthError("busy", "요청이 너무 많아요 · 잠시 뒤 다시 해 주세요", 429)
            seen[nonce] = now + NONCE_TTL
        return dev

    def forget(self, ids):
        with self.lock:
            for i in ids:
                self.nonces.pop(i, None)


# ---------- 짝짓기 ----------

PAIR_NOTES = {"killed": "틀린 연결 코드가 5번 들어와서 그 코드를 버렸어요 · 다른 사람이 코드를 맞히려 했을 수 있어요 · "
                        "[휴대폰 연결하기]로 새 코드를 만들어 주세요",
              "blocked": "한 시간 동안 틀린 연결 시도가 많아서 연결을 잠시 막았어요 · [휴대폰 연결하기]로 새 코드를 만들면 다시 돼요"}


class Pairing:
    """연결 코드 하나 (10분 · 한 번 · 5번 틀리면 버림). 코드 글은 PC 화면에 보여 주려고 메모리에만 (기록·파일에 안 남김).
    휴대폰은 코드 대신 증명(pair_proof)을 보낸다 — 터널 쪽은 코드를 모른다. note: 코드를 버리거나 막은 까닭 (PC 화면에 보여 줌)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.cur = None
        self.fails = collections.deque()
        self.blocked = False
        self.note = None

    def create(self):
        with self.lock:
            code = new_code()
            topic, key, proof = derive_pair(code)
            self.cur = {"code": code, "exp": self.clock() + CODE_TTL, "fails": 0, "topic": topic, "key": key, "proof": proof}
            self.blocked, self.note = False, None
            return dict(self.cur)

    def cancel(self):
        with self.lock:
            self.cur = None

    def live(self):
        with self.lock:
            if self.cur and self.clock() > self.cur["exp"]:
                self.cur = None
            return dict(self.cur) if self.cur else None

    def check(self, nonce, ts, proof):
        """증명이 맞으면 코드를 써 버리고 그 코드의 {topic, key} · 아니면 PairError (틀린 시도는 셈)."""
        msg = "코드가 맞지 않거나 시간이 지났어요 · PC 화면의 새 코드로 다시 해 주세요"
        with self.lock:
            now = self.clock()
            while self.fails and now - self.fails[0] > 3600:
                self.fails.popleft()
            if self.blocked:
                raise PairError("지금은 연결할 수 없어요 · PC에서 새 코드를 만들어 주세요")
            cur = self.cur
            if cur and now > cur["exp"]:
                self.cur = cur = None
            shape = (isinstance(nonce, str) and re.fullmatch(r"[A-Za-z0-9_-]{22}", nonce) and isinstance(ts, int) and not isinstance(ts, bool)
                     and isinstance(proof, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", proof))
            skew = bool(cur and shape) and abs(now - ts) > SKEW  # 살아 있는 코드가 없으면 그냥 '맞지 않거나 시간이 지났어요'
            if not cur or not shape or skew or not hmac.compare_digest(pair_proof(cur["proof"], nonce, ts), proof):
                self.fails.append(now)
                if cur:
                    cur["fails"] += 1
                    if cur["fails"] >= CODE_FAILS:
                        self.cur, self.note = None, "killed"
                if len(self.fails) >= PAIR_FAILS_HOUR:
                    self.blocked, self.cur, self.note = True, None, "blocked"
                if skew:
                    raise PairError("휴대폰 시계가 PC와 너무 달라요 · 휴대폰 시간을 '자동'으로 맞춘 뒤 다시 해 주세요", "skew")
                raise PairError(msg)
            self.cur = None
            return {"topic": cur["topic"], "key": cur["key"]}


# ---------- 미디어 표 (ticket) ----------

class Tickets:
    """영상·그림 주소의 표: 기기 · 종류 · 파일 · 만료(1시간) · 받은 곳(목록을 받은 IP 의 /24·/48).
    <video>·<img> 는 머리글을 못 보내므로 표를 가진 것만으로 받는데, 표 주소를 다른 곳(다른 대역)으로 옮겨 가면 안 받아 준다."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = collections.OrderedDict()
        self.rev = {}

    def issue(self, device, kind, path, ip=""):
        now, net = self.clock(), net_of(ip)
        with self.lock:
            k = (device, kind, str(path), net)
            t = self.rev.get(k)
            if t and t in self.items and self.items[t][3] - now > TICKET_REUSE:  # 넉넉히 남았으면 같은 표 (목록을 자주 새로 해도 표가 쌓이지 않게)
                return t
            t = secrets.token_urlsafe(16)
            _lru_put(self.items, t, (device, kind, str(path), now + TICKET_TTL, net), TICKET_MAX)
            self.rev[k] = t
            if len(self.rev) > TICKET_MAX * 2:
                self.rev = {kk: vv for kk, vv in self.rev.items() if vv in self.items}
            return t

    def get(self, t, ip=""):
        with self.lock:
            it = self.items.get(t)
            if not it or it[3] < self.clock() or it[4] != net_of(ip):
                return None
            return it

    def drop(self, devices=None):
        with self.lock:
            if devices is None:
                self.items.clear()
                self.rev.clear()
                return
            for t in [t for t, it in self.items.items() if it[0] in devices]:
                del self.items[t]
            self.rev = {k: v for k, v in self.rev.items() if v in self.items}


# ---------- 요청 수 제한 ----------

class Limiter:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.buckets = collections.OrderedDict()
        self.fails = collections.OrderedDict()
        self.locked_until = collections.OrderedDict()
        self.last_action = {}
        self.media = collections.Counter()
        self.media_total = 0

    def take(self, kind, key):
        cap, rate = BUCKETS[kind]
        now = self.clock()
        with self.lock:
            tokens, last = self.buckets.get((kind, key), (cap, now))
            tokens = min(cap, tokens + (now - last) * rate)
            ok = tokens >= 1
            _lru_put(self.buckets, (kind, key), (tokens - 1 if ok else tokens, now))
            return ok

    def locked(self, ip):
        with self.lock:
            return self.locked_until.get(ip, 0) > self.clock()

    def failed(self, ip):
        """→ 이번에 막기 시작했으면 True."""
        now = self.clock()
        with self.lock:
            q = self.fails.get(ip) or collections.deque()
            q.append(now)
            while q and now - q[0] > AUTH_FAIL_WINDOW:
                q.popleft()
            _lru_put(self.fails, ip, q)
            if len(q) >= AUTH_FAIL_MAX and self.locked_until.get(ip, 0) <= now:
                _lru_put(self.locked_until, ip, now + LOCKOUT)
                q.clear()
                return True
            return False

    def action_ok(self, device):
        now = self.clock()
        with self.lock:
            if now - self.last_action.get(device, -1e9) < ACTION_GAP:
                return False
            self.last_action[device] = now
            return True

    def media_enter(self, device):
        with self.lock:
            if self.media[device] >= MEDIA_PER_DEVICE or self.media_total >= MEDIA_TOTAL:
                return False
            self.media[device] += 1
            self.media_total += 1
            return True

    def media_leave(self, device):
        with self.lock:
            self.media[device] = max(0, self.media[device] - 1)
            self.media_total = max(0, self.media_total - 1)


# ---------- app 이 넘겨주는 기능 ----------

class Bridge:
    """app → remote: log(msg) · start_job(name, fn, by) · job() 지금 작업 모습 · logs(since) → (줄, 전체 수) ·
    analyze(b) 편집점 찾기 + 가편집 · refs_job(fn) 학습용 작업 안내 처리 · version."""

    def __init__(self, log, start_job, job, logs, analyze, refs_job, version):
        self.log, self.start_job, self.job, self.logs = log, start_job, job, logs
        self.analyze, self.refs_job, self.version = analyze, refs_job, version


def _dur_text(secs):
    secs = int(secs or 0)
    if secs < 60:
        return f"{max(1, secs)}초"
    if secs < 3600:
        return f"{secs // 60}분"
    return f"{secs // 3600}시간 {secs % 3600 // 60}분"


def _label(name):
    return name if name in JOB_LABELS else "작업"


# ---------- ntfy 로 보내기 (비콘·알림 · 작업·HTTP 를 막지 않게 따로 도는 스레드) ----------

class Publisher:
    def __init__(self, svc):
        self.svc = svc
        self.q = queue.Queue()
        self.day, self.used = None, 0
        self.last_beacon = 0.0
        self.pending = None
        self.warned = 0.0
        self.thread = threading.Thread(target=self._loop, daemon=True, name="remote-publisher")
        self.thread.start()

    # 보낼 것 넣기
    def beacon(self, important=False, moved=None, topic=None):
        self.q.put(("beacon", important, moved, topic))

    def notify(self, kind, text, prio=3, tags="bell", topic=None):
        self.q.put(("notify", kind, text, prio, tags, topic))

    def rendezvous(self, topic, body):
        self.q.put(("raw", topic, body))

    def _budget(self, important):
        today = time.strftime("%Y-%m-%d", time.localtime(self.svc.clock()))
        if today != self.day:
            self.day, self.used = today, 0
        if self.used >= DAILY_BUDGET or (self.used >= BUDGET_SOFT and not important):
            return False
        self.used += 1
        return True

    def _loop(self):
        while True:
            try:
                wait = None
                if self.pending is not None:
                    wait = max(0.05, BEACON_GAP - (self.svc.clock() - self.last_beacon))
                try:
                    item = self.q.get(timeout=wait)
                except queue.Empty:
                    item = None
                if item is None:
                    if self.pending is not None and self.svc.clock() - self.last_beacon >= BEACON_GAP:
                        p, self.pending = self.pending, None
                        self._send_beacon(*p)
                    continue
                if item[0] == "stop":
                    return
                if item[0] == "beacon":
                    _, important, moved, topic = item
                    if important or moved or self.svc.clock() - self.last_beacon >= BEACON_GAP:
                        self.pending = None
                        self._send_beacon(important, moved, topic)
                    else:
                        self.pending = (False, None, None)  # 같은 꼴은 마지막 것만 (60초에 한 번)
                elif item[0] == "notify":
                    _, kind, text, prio, tags, topic = item
                    if self._budget(kind in ("attention", "failed", "paired", "moved")):
                        self._post_notify(text, prio, tags, topic)
                elif item[0] == "raw":
                    if self._budget(True):
                        self._post(f"{self.svc.ntfy()}/{item[1]}", item[2].encode("utf-8"), {"Content-Type": "text/plain"})
            except Exception:  # noqa: BLE001 — 보내기 실패가 앱을 멈추지 않게
                _trace()

    def _send_beacon(self, important, moved, topic):
        if not self._budget(important or bool(moved)):
            return
        self.last_beacon = self.svc.clock()
        body = self.svc.beacon_body(moved=moved)
        if body:
            self._post(f"{self.svc.ntfy()}/{topic or self.svc.store.data['topics']['beacon']}", body.encode("ascii"),
                       {"Content-Type": "text/plain"})

    def _post_notify(self, text, prio, tags, topic=None):
        msg = {"topic": topic or self.svc.store.data["topics"]["notify"], "title": "풋살 스튜디오", "message": text,
               "tags": [tags], "priority": prio, "click": self.svc.site()}
        self._post(self.svc.ntfy() + "/", json.dumps(msg, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"})

    def _post(self, url, data, headers):
        cert = False
        for k in range(len(RETRY_DELAYS) + 1):
            try:
                req = urllib.request.Request(url, data=data, method="POST", headers={**updater.UA, **headers})
                with updater.urlopen(req, 10) as r:  # 업데이트와 같은 인증서 설정 (Python 3.13+ · 백신 'HTTPS 검사' · D-029)
                    r.read(256)
                return True
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429:
                    break
            except updater.NET_ERRORS as e:
                cert = cert or "CERTIFICATE_VERIFY_FAILED" in updater._why(e)
            if k < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[k])
        if self.svc.clock() - self.warned > 3600:
            self.warned = self.svc.clock()  # 정해진 문장만 (주소·주제가 든 오류 글은 기록하지 않음)
            self.svc.log("  휴대폰 알림을 보내지 못했어요 · " + ("백신 프로그램의 'HTTPS 검사'·'웹 보호'를 잠시 끄고 다시 해 보세요" if cert
                                                         else "인터넷 연결을 확인해 주세요"))
        return False

    def post_now(self, url, data, headers, timeout=2.0):
        """앱을 끌 때 마지막 비콘: 기다리지 않고 한 번만."""
        try:
            req = urllib.request.Request(url, data=data, method="POST", headers={**updater.UA, **headers})
            with updater.urlopen(req, timeout) as r:
                r.read(256)
            return True
        except Exception:  # noqa: BLE001
            return False

    def stop(self):
        self.q.put(("stop",))


def app_ports():
    """앱 화면(로컬 서버)이 쓸 수 있는 포트 창 — app._bind(8765 → 8766~8799 · .port)·updater._app_ports(.port 는 8766~8804)와 같은 규칙.
    FUTSAL_PORT 로 정해 켜면 그 포트 하나뿐 (다른 포트로 가지 않음)."""
    try:
        fixed = int(os.environ.get("FUTSAL_PORT") or 0)
    except ValueError:
        fixed = 0
    return {fixed} if fixed else range(8765, 8765 + 40)


# ---------- 원격 서비스 ----------

def _close_server(srv):
    try:
        srv.shutdown()
        srv.server_close()
    except Exception:  # noqa: BLE001
        pass


class Service:
    def __init__(self, home=None, clock=time.time, ntfy=None):
        self.home = Path(home) if home else None
        self.clock = clock
        self._ntfy = ntfy
        self.lock = threading.RLock()
        self.bridge = None
        self.store = None
        self.state, self.error, self.prep = "off", None, None
        self.url = None
        self.listener = None
        self.tun = None
        self.pub = None
        self.sem = threading.BoundedSemaphore(CONCURRENCY)
        self.last_auth = clock()
        self.last = None
        self.qa = collections.OrderedDict()
        self.confirms = {}
        # 켜기·끄기: 시도마다 번호(_gen)와 그만두기 Event 를 새로 — 늦게 끝난 옛 시도는 지금 상태·enabled·터널을 못 바꾼다.
        # Event 는 지우지(clear) 않는다 (옛 시도 스레드가 아직 보고 있을 수 있음).
        self._gen = 0
        self._cancel = threading.Event()
        self._tunnels = set()  # 띄운 Tunnel 전부 → 끄기·앱 끄기에서 하나도 남기지 않음
        self._job_seen = None
        self._lockout_logged = 0.0
        self._started = False
        self._off_reason = "user"
        self._err_kind = None              # "tunnel"·"start" 오류는 저절로 다시 켜 봄 · "crypto" 는 안 함
        self._retry = {"n": 0, "at": None}
        self._last_test = -1e9
        self._proj_cache = collections.OrderedDict()

    # ----- 준비 -----
    def init(self, bridge):
        self.bridge = bridge
        home = self.home or core.ENGINE_HOME
        self.store = Store(home / "remote.json", self.clock)
        self.auth = Auth(self.store, self.clock)
        self.pairing = Pairing(self.clock)
        self.tickets = Tickets(self.clock)
        self.limiter = Limiter(self.clock)
        if not self._started:
            self._started = True
            self.pub = Publisher(self)
            threading.Thread(target=self._watch, daemon=True, name="remote-watch").start()
            if sys.platform == "win32":
                threading.Thread(target=self._keep_awake, daemon=True, name="remote-awake").start()
        return self

    def log(self, msg):
        if self.bridge:
            self.bridge.log(msg)

    def ntfy(self):
        return (self._ntfy or os.environ.get("FUTSAL_NTFY") or NTFY_DEFAULT).rstrip("/")

    def site(self):
        return (os.environ.get("FUTSAL_REMOTE_SITE") if _dev() else None) or SITE

    def origins(self):
        extra = [o.strip() for o in (os.environ.get("FUTSAL_REMOTE_ORIGINS") or "").split(",") if o.strip()] if _dev() else []
        return ORIGINS + tuple(extra)

    def port(self):
        lst = self.listener
        return lst.server_address[1] if lst else None

    def host(self):
        """서명에 들어가는 이 PC 주소의 host (터널 주소 · 개발 때는 127.0.0.1:<포트>) · 주소가 없으면 ''."""
        u = self.url
        return urlparse(u).netloc.lower() if u else ""

    # ----- PC 화면 (/api/remote*) -----
    def brief(self):
        return {"state": self.state, "devices": len(self.store.data["devices"]) if self.store else 0}

    def pc_status(self):
        d = self.store.data
        p = self.pairing.live()
        pair = None
        if p and self.state == "on":
            pair = {"code": fmt_code(p["code"]), "link": self.pair_link(p["code"]), "expiresAt": int(p["exp"])}
        now = self.clock()
        return {"state": self.state, "error": self.error, "prep": self.prep, "dev": _dev(),
                "devices": [{"id": x["id"], "name": x["name"], "created": x["created"], "lastSeen": x["lastSeen"],
                             "stale": now - (x.get("lastSeen") or x.get("created") or 0) > STALE_DAYS * 86400} for x in d["devices"]],
                "maxDevices": MAX_DEVICES, "pair": pair, "pairNote": PAIR_NOTES.get(self.pairing.note) if not pair else None,
                "settings": d["settings"], "pc": d["pc"],
                "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]}, "site": self.site(),
                "autoOffAt": self._auto_off_at(), "crypto": crypto_ok(),
                "retryAt": int(self._retry["at"]) if self.state == "error" and self._retry["at"] and d.get("enabled") else None}

    def pair_link(self, code):
        hint = ""
        if self.url:
            m = re.fullmatch(r"https://([a-z0-9-]{1,63})\.trycloudflare\.com", self.url)
            hint = m.group(1) if m else (urlparse(self.url).netloc if _dev() else "")
        return f"{self.site()}#pair={code}" + (f"&u={hint}" if hint else "")

    def turn_on(self, auto=False):
        """원격 접속 켜기 (PC 화면에서만 · auto 는 오류 뒤 저절로 다시)."""
        with self.lock:
            if self.state in ("preparing", "starting", "on", "restarting"):
                return True
            if not crypto_ok():
                self.state, self.error, self._err_kind = "error", MISSING_MSG, "crypto"
                return False
            if not auto:
                self._retry = {"n": 0, "at": None}
            self._cancel.set()  # 앞의 시도는 그만 (그 스레드는 자기 Event 를 계속 봄)
            self._gen += 1
            gen, cancel = self._gen, threading.Event()
            self._cancel = cancel
            tuns, lst = self._take_resources()
            self.state, self.error, self.prep, self.url, self._err_kind = "preparing", None, 0, None, None
        self._stop_resources(tuns, lst)  # 실패한 앞의 시도(터널·리스너)가 남아 있으면 정리
        threading.Thread(target=self._start, args=(gen, cancel), daemon=True, name="remote-start").start()
        return True

    def _current(self, gen, cancel):
        return gen == self._gen and not cancel.is_set()

    def _start(self, gen, cancel):
        lst = None
        try:
            exe = None
            if not _dev():
                # 지난번에 남은 cloudflared 를 먼저 (판을 올릴 때 실행 중인 파일이라 바꿔 끼우기가 막히지 않게 · 지금 띄운 것은 안 건드림)
                tunnel.kill_stale(os.environ.get("FUTSAL_CLOUDFLARED") or tunnel.bin_path())
                exe = tunnel.ensure(progress=lambda got, total: self._prep_progress(gen, got, total), cancel=cancel.is_set)
            with self.lock:
                if not self._current(gen, cancel):
                    return
            lst = self._new_listener()
            dev_url = None
            with self.lock:
                if not self._current(gen, cancel):  # 리스너를 여는 사이 [끄기]·다시 켜기 → 이 시도는 아무것도 남기지 않음
                    return
                self.listener, lst = lst, None
                self.store.set(enabled=True)  # 지금 시도일 때만 (끄기와 같은 잠금 안에서)
                self.last_auth = self.clock()
                if _dev():
                    dev_url = f"http://127.0.0.1:{self.port()}"
                else:
                    self.state, self.prep = "starting", None
                    holder = {}
                    tun = tunnel.Tunnel(exe, self.port(), lambda kind, **kw: self._tunnel_event(gen, holder.get("t"), kind, **kw))
                    holder["t"] = tun
                    self.tun = tun
                    self._tunnels.add(tun)
                    tun.start()
            if dev_url:
                self._tunnel_event(gen, None, "on", url=dev_url)
            self.log("원격 접속을 켰어요")
        except Exception as e:  # noqa: BLE001
            with self.lock:
                if not self._current(gen, cancel):  # 받는 중에 [그만두기]·[끄기]
                    return
                tuns, lst2 = self._take_resources()
                self.state, self.error, self.prep, self._err_kind = "error", scrub(e) or "원격 접속을 켜지 못했어요", None, "start"
                self._schedule_retry()
            self._stop_resources(tuns, lst2)
            if not isinstance(e, (RuntimeError, OSError)):
                _trace()
            self.log(f"원격 접속을 켜지 못했어요 · {scrub(e)}")
        finally:
            if lst is not None:  # 띄웠지만 넘겨주지 못한 리스너 (그만둔 시도·오류) → 닫음
                _close_server(lst)

    def _prep_progress(self, gen, got, total):
        if gen == self._gen:
            self.prep = min(99, int(got * 100 / total)) if total else None

    def _schedule_retry(self):
        n = self._retry["n"]
        self._retry = {"n": n + 1, "at": self.clock() + RETRY_AFTER[min(n, len(RETRY_AFTER) - 1)]}

    def _tunnel_event(self, gen, tun, kind, url=None, error=None):
        """tunnel.Tunnel → 상태 바뀜 (지금 시도·지금 터널의 소식만). 터널 주소(url)는 기록에 남기지 않는다."""
        lst = dead = None
        with self.lock:
            if gen != self._gen or self._cancel.is_set() or (tun is not None and tun is not self.tun):
                return
            if kind == "on":
                changed = url != self.url
                self.state, self.error, self.url = "on", None, url
                self._retry = {"n": 0, "at": None}
            elif kind == "restarting":
                self.state, self.url = "restarting", None
                return
            elif kind == "error":  # 터널이 멈춤 → 리스너도 닫고(다시 켤 때 새로) 잠시 뒤 저절로 다시
                self.state, self.error, self.url = "error", error or "연결이 끊겼어요", None
                self._off_reason, self._err_kind = "error", "tunnel"
                lst, self.listener = self.listener, None
                dead, self.tun = self.tun, None
                if dead is not None:
                    self._tunnels.discard(dead)
                self._schedule_retry()
            elif kind == "attention":
                self._note("attention", NOTE_TEXT["tunnel"])
                return
            else:
                return
        if kind == "on" and changed:
            self.pub.beacon(important=True)
            self._publish_pair()
        if kind == "error":
            self._stop_resources([dead], lst, timeout=2)  # 멈춘 터널이 (혹시라도) 남긴 프로세스까지 · 이 스레드가 터널 스레드여도 됨
            self.log(f"원격 접속 · {error}")
            self.pub.beacon(important=True)

    def _take_resources(self):
        """(잠금 안에서) 지금 터널·추적 중인 모든 터널·리스너를 떼어 냄 → 끄는 것은 _stop_resources 가 잠금 밖에서.
        번호(_gen)를 올린 같은 잠금 안에서 떼어 내므로, 그 뒤 새 시도가 띄운 터널은 건드리지 않는다."""
        tuns = [t for t in [self.tun] + list(self._tunnels) if t is not None]
        self._tunnels.clear()
        lst, self.listener, self.tun = self.listener, None, None
        return tuns, lst

    def _stop_resources(self, tuns, lst, timeout=5):
        """터널들과 리스너를 끔 — 잠금 밖에서 (터널 스레드가 _tunnel_event 로 잠금을 기다릴 수 있음)."""
        for t in dict.fromkeys(x for x in tuns if x is not None):
            try:
                t.stop(timeout=timeout)
            except Exception:  # noqa: BLE001 — 끄는 길은 막히면 안 됨
                _trace()
        if lst is not None:
            _close_server(lst)

    def turn_off(self, why="user", keep_enabled=False):
        err = None
        with self.lock:
            was = self.state
            self._off_reason = why
            self._cancel.set()
            self._gen += 1  # 켜는 중인 시도가 있으면 이제 '옛 시도' (그 시도는 enabled·터널·리스너를 못 바꿈)
            tuns, lst = self._take_resources()
            self.state, self.error, self.prep, self.url, self._err_kind = "off", None, None, None, None
            self._retry = {"n": 0, "at": None}
            if not keep_enabled:
                try:
                    self.store.set(enabled=False)  # 같은 잠금 안 → 바로 뒤의 새 켜기가 쓴 True 를 덮지 않음
                except OSError as e:
                    err = e
        self._stop_resources(tuns, lst)
        self.tickets.drop()
        self.pairing.cancel()
        if was != "off":
            self.pub.beacon(important=True)
            self.log(f"원격 접속을 껐어요 · {OFF_REASONS.get(why, why)}")
        if err:
            raise err
        return True

    def pair_start(self):
        if self.state != "on":
            raise PairError("먼저 원격 접속을 켜 주세요")
        if len(self.store.data["devices"]) >= MAX_DEVICES:
            raise PairError(f"휴대폰은 {MAX_DEVICES}대까지 연결할 수 있어요 · 안 쓰는 휴대폰을 먼저 끊어 주세요")
        if not crypto_ok():
            raise PairError(MISSING_MSG)
        self.pairing.create()
        self._publish_pair()
        return self.pc_status()["pair"]

    def _publish_pair(self):
        p = self.pairing.live()
        if not p or not self.url:
            return
        d = self.store.data
        msg = json.dumps({"v": 1, "url": self.url, "pc": d["pc"], "exp": int(p["exp"])}, ensure_ascii=False)
        self.pub.rendezvous(p["topic"], "fsp1." + gcm_seal(p["key"], msg, "fsp1|" + p["topic"]))

    def revoke(self, ids=None, everyone=False, why="PC에서", rotate_notify=True):
        """끊기: 열쇠·표·nonce 를 바로 버리고 비콘 주제를 바꿈 (rotate_notify 면 알림 주제도 → 옛 알림 주제에 한 번 안내를 남김)."""
        d = self.store.data
        gone = [x for x in d["devices"] if everyone or x["id"] in (ids or [])]
        if not gone:
            return 0
        old = self.store.rotate_topics(notify=rotate_notify)
        self.store.remove({x["id"] for x in gone})
        self.tickets.drop({x["id"] for x in gone})
        self.auth.forget([x["id"] for x in gone])
        if everyone:
            self.pairing.cancel()
        new = dict(self.store.data["topics"])
        if self.store.data["devices"]:  # 남은 휴대폰에만 새 주제를 옛 주제로 알려 줌 (끊은 휴대폰은 열쇠가 없어 못 읽음)
            self.pub.beacon(important=True, moved=new, topic=old["beacon"])
        self.pub.beacon(important=True)
        if new["notify"] != old["notify"]:  # ntfy 앱은 옛 주제를 계속 구독 중 → 조용히 끊기지 않게 정해진 안내를 한 번
            self.pub.notify("moved", NOTE_TEXT["moved"], 4, "warning", topic=old["notify"])
        for x in gone:
            self.log(f"휴대폰 연결을 끊었어요 · {clean(x['name'], 40)} ({why})")
        return len(gone)

    def set_settings(self, b):
        s = dict(self.store.data["settings"])
        if "autoOffHours" in b:
            if b["autoOffHours"] not in AUTO_OFF_CHOICES or isinstance(b["autoOffHours"], bool):
                raise ValueError("자동으로 끄기 시간을 다시 골라 주세요")
            s["autoOffHours"] = int(b["autoOffHours"])
        if "keepAwake" in b:
            if b["keepAwake"] not in ("job", "always"):
                raise ValueError("PC 잠들지 않게 설정을 다시 골라 주세요")
            s["keepAwake"] = b["keepAwake"]
        if isinstance(b.get("notify"), dict):
            s["notify"] = {k: bool(b["notify"].get(k, s["notify"].get(k, True))) for k in ("done", "failed", "attention")}
        self.store.set(settings=s)
        return s

    def test_notify(self):
        self.pub.notify("test", NOTE_TEXT["test"], 3, "bell")

    def shutdown(self, timeout=1.5):
        """앱을 끌 때: 터널·리스너를 먼저 끄고(무엇이 켜져 있든) → 휴대폰에 '앱을 껐어요' 비콘을 한 번, timeout 안에서만
        (느린 DNS 에도 앱 끄기·업데이트 다시 시작이 기다리지 않게 따로 스레드). 다음에 켜면 다시 켜지도록 enabled 는 그대로."""
        if not self.store:
            return
        try:
            with self.lock:
                was = self.state
                self._off_reason = "app"
                self._cancel.set()
                self._gen += 1
                tuns, lst = self._take_resources()
                self.state, self.url, self.prep = "off", None, None
            self._stop_resources(tuns, lst, timeout=1.0)
            if was != "off" and self.pub:
                body = self.beacon_body(state="off", reason=OFF_REASONS["app"])
                if body:
                    t = threading.Thread(target=self.pub.post_now, daemon=True, name="remote-last-beacon",
                                         args=(f"{self.ntfy()}/{self.store.data['topics']['beacon']}", body.encode("ascii"),
                                               {"Content-Type": "text/plain"}, timeout))
                    t.start()
                    t.join(timeout)
        except Exception:  # noqa: BLE001 — 끄는 길은 막히면 안 됨
            _trace()

    # ----- 비콘 -----
    def beacon_body(self, state=None, reason=None, moved=None):
        d = self.store.data
        devs = list(d["devices"])
        if not devs or not crypto_ok():
            return None
        seq = self.store.next_seq()
        st = state or ("on" if self.state == "on" else "off")
        if reason is None and st == "off":
            reason = OFF_REASONS.get(getattr(self, "_off_reason", "user"), OFF_REASONS["user"])
        snap = self.bridge.job() if self.bridge else {}
        job = {"name": _label(snap.get("name")), "pct": (snap.get("progress") or {}).get("pct")} if snap.get("name") else None
        inner = {"v": 1, "pc": d["pc"]["id"], "seq": seq, "ts": int(self.clock()), "state": st, "reason": reason,
                 "url": self.url if st == "on" else None, "job": job, "ver": self.bridge.version if self.bridge else "", "api": API}
        if moved:
            inner["moved"] = moved
        items = []
        for x in devs:
            pt = json.dumps(inner, ensure_ascii=False)
            items.append({"d": x["id"], "c": gcm_seal(_unb64u(x["beacon"]), pt, f"fsb1|{d['pc']['id']}|{x['id']}|{seq}")})
        body = "fsb1." + _b64u(json.dumps({"v": 1, "pc": d["pc"]["id"], "seq": seq, "items": items}).encode("utf-8"))
        if len(body) > 3900 and job:  # ntfy 는 4KB 가 넘으면 첨부 파일로 바꿈 → 작업 칸을 빼고 다시
            inner["job"] = None
            items = [{"d": x["id"], "c": gcm_seal(_unb64u(x["beacon"]), json.dumps(inner, ensure_ascii=False), f"fsb1|{d['pc']['id']}|{x['id']}|{seq}")}
                     for x in devs]
            body = "fsb1." + _b64u(json.dumps({"v": 1, "pc": d["pc"]["id"], "seq": seq, "items": items}).encode("utf-8"))
        return body

    # ----- 작업이 끝났을 때 (app.JOB_HOOKS) -----
    def job_hook(self, name, error, result, by, secs):
        """last: 휴대폰 '마지막 작업' 칸 — ok(끝)·warn(확인이 필요해요: 막힘·못 받은 영상)·실패. 알림은 정해진 문장만."""
        ok, err, blocked, warn, note = True, None, False, False, "missed"
        yt_msg, yt_quiet = None, False
        if name in YOUTUBE_JOBS:  # 유튜브: 결과의 오류 글·주소 대신 정해진 문장만 (D-048)
            r = result if isinstance(result, dict) else {}
            card = (r.get("fail") or {}).get("kind") if isinstance(r.get("fail"), dict) else None
            card = card or (trouble.youtube_kind_of(error) if error else None)  # 작업 밖으로 나온 예외: start_job 이 만든 카드 문장
            # 멈춤(✕)이 작업 밖으로 예외로 나온 경우(start_job 의 cancelled 안내 '멈췄어요 · …')도 멈춤 — 실패 알림 안 울림
            paused = r.get("paused") or bool(error and not card and trouble.CANCELLED.search(error))
            if error or r.get("ok") is False:
                key = "paused" if paused else YOUTUBE_FAIL_KEY.get(card) or ("relogin" if r.get("relogin") else "failed")
                ok, err, yt_msg = False, YOUTUBE_MSG[key], YOUTUBE_MSG[key]
                yt_quiet = key == "paused"  # 멈춤은 PC·휴대폰에서 사람이 직접 누른 것 → '마지막 작업' 칸만, 알림은 안 울림
            else:
                key = YOUTUBE_LEFT_KEY.get(card) or ("warn" if r.get("warnings") or r.get("locked") or r.get("problem") else
                                                     "finish" if name == youtube_upload.JOB_FINISH else "done")
                yt_msg = YOUTUBE_MSG[key]
                if key not in ("done", "finish"):  # 영상은 올라갔지만 썸네일·자막·재생목록·잠김·처리 문제 → '확인이 필요해요'
                    ok, warn, err = False, True, yt_msg
            if name == youtube_upload.JOB_FINISH and not by and secs < 60:
                yt_quiet = True  # PC 에서 누른 짧은 [마저 하기]·[썸네일 다시 올리기]는 사람이 PC 앞에 있음 → 알림 안 울림 (다른 작업과 같은 기준)
        elif error:
            ok, err = False, scrub(error)
        elif isinstance(result, dict) and result.get("ok") is False:
            ok, err, blocked = False, scrub(result.get("error")), bool(result.get("blocked"))
            if blocked:
                warn, err, note = True, BLOCKED_PHONE_MSG, "blocked"
        elif name in STRATEGY_REFRESHING and isinstance(result, dict):  # 멈춤 → '멈췄어요' · 막힘·인터넷 끊김 → '확인이 필요해요' (D-028)
            if result.get("stopped"):
                ok, err = False, STRATEGY_MSG["stopped_check" if name == strategy.JOB_CHECK else "stopped"]
            elif result.get("blocked") or result.get("net"):
                blocked = bool(result.get("blocked"))
                ok, warn, note = False, True, "blocked" if blocked else "net"
                err = STRATEGY_MSG[note]
        elif name == "보관함에 담기" and _missed(result):  # 받기: 못 받은 영상 id 목록 · {failed, why} 면 첫 영상의 쉬운 까닭으로
            missed = _missed(result)
            first = _first_why(result, missed)
            ok, warn = False, True
            err = scrub(MISSED_WHY_MSG.format(n=len(missed), why=first)) if first else MISSED_MSG.format(n=len(missed))
        elif name == "편집점 찾기" and isinstance(result, dict) and isinstance(result.get("failed"), list) and result["failed"] \
                and isinstance(result.get("why"), dict):  # 일부 영상만 건너뜀 → 쉬운 한 줄 (trouble.explain) · 학습용 받기 결과는 해당 없음
            ok, warn, note = False, True, "partial"
            err = scrub(PARTIAL_MSG.format(n=len(result["failed"]), why=_first_why(result, result["failed"]) or "까닭은 PC 작업 기록에 있어요"))
        self.last = {"name": name, "ok": ok, "warn": warn, "error": err, "blocked": blocked, "endedAt": int(self.clock()), "by": by}
        if name == "영상 검수" and isinstance(result, dict) and result.get("file"):  # 검수 결과는 완성본 목록에 같이 보여 줌
            summary = {k: result.get(k) for k in ("score", "bad", "warn", "duration", "width", "height")}
            summary["items"] = [{k: scrub(it.get(k)) if isinstance(it.get(k), str) else it.get(k) for k in ("lv", "title", "msg", "t")}
                                for it in (result.get("items") or [])[:30] if isinstance(it, dict)]
            _lru_put(self.qa, Path(str(result["file"])).name, summary, 200)
        if self.state != "on" or not self.store.data["devices"]:
            return
        self.pub.beacon()
        n = self.store.data["settings"]["notify"]
        label = _label(name)
        if yt_msg:  # 유튜브: PC 에서 시킨 올리기라도 끝·실패는 알림 (자리를 비운 사이 올리기가 끝났는지 알게) · 멈춤·짧은 마무리는 조용히
            if yt_quiet:
                pass
            elif warn:
                self._note("attention", yt_msg)
            elif n.get("done" if ok else "failed"):
                self.pub.notify("done" if ok else "failed", yt_msg, 3 if ok else 4, "white_check_mark" if ok else "warning")
            return
        if warn:
            if n.get("attention"):
                self._note("attention", NOTE_TEXT[note])
        elif not ok:
            if n.get("failed") and (by or secs >= 5):
                self.pub.notify("failed", f"작업이 멈췄어요 · {label} — 휴대폰에서 자세히 보기", 4, "warning")
        elif n.get("done") and (by or secs >= 60):
            self.pub.notify("done", f"작업이 끝났어요 · {label} ({_dur_text(secs)})", 3, "white_check_mark")

    def _note(self, kind, text):
        if self.pub and self.store.data["settings"]["notify"].get("attention", True):
            self.pub.notify(kind, text, 4, "warning")

    # ----- 뒤에서: 하트비트·자동 끄기·오래된 기기·오류 뒤 다시 켜기 -----
    def _auto_off_at(self):
        h = self.store.data["settings"].get("autoOffHours") or 0
        return int(self.last_auth + h * 3600) if h and self.state == "on" else None

    def _watch(self):
        last_hb = self.clock()
        while True:
            time.sleep(5)
            try:
                self.tick(last_hb)
                if self.clock() - last_hb >= HEARTBEAT:
                    last_hb = self.clock()
            except Exception:  # noqa: BLE001
                _trace()

    def tick(self, last_hb=None):
        """5초마다 (시험은 직접 부름): 작업이 바뀌면 비콘 · 20분 하트비트 · 오래 안 쓰면 끄기 · 90일 안 쓴 기기 끊기 ·
        켜 두기로 한(enabled) 원격이 터널 오류로 멈췄으면 2분·5분·15분·그 뒤 30분마다 저절로 다시 켜 봄 (PC 앞에 아무도 없어도)."""
        if not self.store:
            return
        if self.state == "on":
            snap = self.bridge.job() if self.bridge else {}
            cur = snap.get("name")
            if cur != self._job_seen:
                self._job_seen = cur
                self.pub.beacon()
            elif last_hb is not None and self.clock() - last_hb >= HEARTBEAT:
                self.pub.beacon()
            h = self.store.data["settings"].get("autoOffHours") or 0
            if h and not cur and self.clock() - self.last_auth >= h * 3600:
                self.turn_off("idle")
                self._note("attention", f"원격 접속을 껐어요 ({h}시간 동안 안 써서)")
        elif (self.state == "error" and self._err_kind in ("tunnel", "start") and self.store.data.get("enabled")
              and self._retry["at"] is not None and self.clock() >= self._retry["at"]):
            self._retry["at"] = None
            self.log("원격 접속을 다시 켜 볼게요")
            self.turn_on(auto=True)
        old = self.store.expired()
        if old:  # 오래 안 쓴 기기: 비콘 주제만 바꿈 (알림 주제까지 바꾸면 쓰고 있는 휴대폰의 ntfy 알림이 조용히 끊김)
            self.revoke(old, why="90일 동안 안 씀", rotate_notify=False)

    def _awake_wanted(self):
        return self.state in ("on", "restarting") and (bool((self.bridge.job() or {}).get("name"))
                                                       or self.store.data["settings"].get("keepAwake") == "always")

    def _keep_awake(self):
        """Windows: 원격이 켜져 있고 (작업 중이거나 '항상'이면) 절전 막기 (remote-awake 스레드)."""
        self._awake_loop()

    def _awake_loop(self, stop=None, every=5):
        """이 스레드에서 core.keep_awake() 를 쥐고 있다가 원하지 않게 되면 놓음 (SetThreadExecutionState 는 core·updater 에서만).
        Windows 는 그 상태를 스레드마다 세므로, 여기서 놓아도 작업 스레드(app.start_job 의 keep_awake · 편집점 찾기)가 쥔 것은
        그대로이고 반대로 작업이 끝나도 여기서 쥔 '켜 둔 동안 항상'은 그대로 — 서로를 일찍 풀지 않는다 (D-034)."""
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                if self._awake_wanted():
                    with core.keep_awake():
                        while not stop.wait(every) and self._awake_wanted():
                            pass
                    continue
            except Exception:  # noqa: BLE001
                pass
            stop.wait(every)

    # ----- 리스너 -----
    def _new_listener(self):
        """새 원격 리스너 (띄우기만 · self.listener 에 넣는 것은 부르는 쪽이 잠금 안에서).
        앱 화면 포트 창(app_ports: 8765 와 넘칠 때 쓰는 8766~ · .port)은 쓰지 않는다 — 두 번째 실행·실행기가 그 창을 '이 앱'인지 묻고,
        다음에 켤 때 앱이 그 포트를 써야 할 수 있으므로 (D-034)."""
        port = int(os.environ.get("FUTSAL_REMOTE_PORT") or 0)
        if port and port in app_ports():
            raise RuntimeError("원격 접속용 포트가 앱 화면 포트와 겹쳐요 · FUTSAL_REMOTE_PORT 를 바꿔 주세요")
        srv = None
        for _ in range(20):
            try:
                srv = RemoteServer(("127.0.0.1", port), self)
            except OSError:
                if not port:
                    raise
                time.sleep(0.25)
                continue
            if not port and srv.server_address[1] in app_ports():  # 임의 포트가 (드물게) 앱 포트 창에 걸림 → 닫고 다시
                srv.server_close()
                srv = None
                continue
            break
        if srv is None:
            raise RuntimeError("원격 접속용 포트를 열지 못했어요 · 잠시 뒤 다시 켜 주세요")
        threading.Thread(target=srv.serve_forever, daemon=True, name="remote-listener").start()
        return srv

    def _open_listener(self):  # 시험 도우미 (켜기 흐름은 _start)
        if not self.listener:
            self.listener = self._new_listener()

    def _close_listener(self):
        with self.lock:
            srv, self.listener = self.listener, None
        if srv is not None:
            _close_server(srv)

    # ----- 휴대폰 요청 처리 -----
    def touch(self, dev):
        self.last_auth = self.clock()
        self.store.touch(dev)

    def r_ping(self):
        return {"api": API, "pc": self.store.data["pc"], "time": int(self.clock())}

    def r_pair(self, b):
        """짝짓기: 코드 증명이 맞으면 기기 열쇠·주제·지금 주소를 코드에서 만든 열쇠로 잠가 돌려줌 (터널 쪽은 아무것도 못 읽음)."""
        if not crypto_ok():
            raise PairError(MISSING_MSG)
        install = b.get("install") if isinstance(b.get("install"), str) and INSTALL_RE.fullmatch(b["install"]) else None
        devs = self.store.data["devices"]
        if len(devs) >= MAX_DEVICES and not (install and any(x.get("install") == install for x in devs)):
            raise PairError(f"휴대폰은 {MAX_DEVICES}대까지 연결할 수 있어요 · PC에서 안 쓰는 휴대폰을 먼저 끊어 주세요")
        p = self.pairing.check(b.get("nonce"), b.get("ts"), b.get("proof"))
        dev, old = self.store.add_device(b.get("name"), install)
        if old:  # 같은 브라우저가 다시 연결 → 옛 열쇠·표는 바로 버림 (주제는 그대로: 같은 휴대폰)
            self.tickets.drop({x["id"] for x in old})
            self.auth.forget([x["id"] for x in old])
        self.last_auth = self.clock()
        d = self.store.data
        self.log(f"휴대폰이 {'다시 ' if old else ''}연결됐어요 · {clean(dev['name'], 40)}")
        self._note("paired", NOTE_TEXT["paired"])
        self.pub.beacon(important=True)
        inner = {"v": 2, "url": self.url, "pc": d["pc"], "device": {"id": dev["id"], "name": dev["name"]},
                 "keys": {"auth": dev["auth"], "beacon": dev["beacon"]},
                 "beacon": {"server": self.ntfy(), "topic": d["topics"]["beacon"]},
                 "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]}}
        sealed = "fsp2." + gcm_seal(p["key"], json.dumps(inner, ensure_ascii=False), pair_aad(p["topic"], d["pc"]["id"], dev["id"]))
        return {"api": API, "v": 2, "pc": {"id": d["pc"]["id"]}, "device": {"id": dev["id"]}, "sealed": sealed, "time": int(self.clock())}

    def _item_title(self, item):
        """진행 칸의 파일 이름 → 보관함과 같은 제목 (날짜_영상id_ 와 확장자를 뺌)."""
        s = scrub(item or "")
        return source._title_of(s) if re.search(r"\.[A-Za-z0-9]{2,4}$", s) else s

    def r_status(self, dev, since):
        snap = self.bridge.job()
        job = None
        if snap.get("name"):
            pr = snap.get("progress") or {}
            job = {"name": snap["name"], "id": snap.get("id"), "by": snap.get("by"), "startedAt": int(snap["t0"]) if snap.get("t0") else None,
                   "stoppable": snap["name"] in STOPPABLE,
                   "progress": {"label": scrub(pr.get("label") or ""), "item": self._item_title(pr.get("item")), "pct": pr.get("pct"),
                                "detail": scrub(pr.get("detail") or ""), "eta": pr.get("eta")}}
        new, total = self.bridge.logs(max(0, since))
        if since <= 0 or since > total:  # 처음이거나 앱이 다시 켜져 번호가 줄었음 → 마지막 200줄만
            new, total = self.bridge.logs(max(0, total - LOG_TAIL))
        new = new[-LOG_TAIL:]
        d = self.store.data
        topics = json.dumps({"beacon": {"server": self.ntfy(), "topic": d["topics"]["beacon"]},
                             "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]}})
        return {"api": API, "ver": self.bridge.version, "time": int(self.clock()), "pc": d["pc"], "job": job, "last": self.last,
                "log": [scrub(x) for x in new], "logTotal": total,
                "remote": {"autoOffAt": self._auto_off_at(), "devices": len(d["devices"]), "device": {"id": dev["id"], "name": dev["name"]}},
                # 주제 이름은 곧 비밀(알림 주제 = 보내기 열쇠) → 기기 비콘 열쇠로 잠가서 (Cloudflare 가 못 봄)
                "topics": gcm_seal(_unb64u(dev["beacon"]), topics, topics_aad(d["pc"]["id"], dev["id"]))}

    def _projects(self, name):
        """편집본 목록 (id·이름·형식) — 프로젝트 파일이 바뀌지 않았으면 기억한 것 (보관함 목록이 큰 파일을 매번 다 읽지 않게)."""
        try:
            p = editor._ppath(name)
            st = p.stat()
        except (OSError, ValueError):
            return []
        key, sig = str(p), (st.st_mtime_ns, st.st_size)
        hit = self._proj_cache.get(key)
        if hit and hit[0] == sig:
            return [dict(x) for x in hit[1]]
        try:
            proj = json.loads(p.read_text(encoding="utf-8"))
            out = [{"id": str(q.get("id")), "name": clean(q.get("name"), 80), "format": "shorts" if q.get("format") == "shorts" else "long"}
                   for q in proj.get("sequences") or [] if isinstance(q, dict) and q.get("id")]
        except (OSError, ValueError, AttributeError):
            return []
        _lru_put(self._proj_cache, key, (sig, out), 500)
        return [dict(x) for x in out]

    def r_library(self, dev, ip=""):
        vids = source.annotate(core.local_videos())
        out = []
        for v in vids:
            s = v.get("source") or {}
            out.append({"name": v["name"], "title": source._title_of(v["name"]), "sizeMb": v.get("size_mb"), "analyzed": bool(v.get("analyzed")),
                        "source": {"kind": s.get("kind") or "unknown", "channel": s.get("channel") or ""},
                        "poster": "/r/m/" + self.tickets.issue(dev["id"], "poster", v["name"], ip), "sequences": self._projects(v["name"])})
        return {"videos": out}

    def r_outputs(self, dev, ip=""):
        try:
            files = [p for p in core.OUT.iterdir() if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in OUT_KINDS]
        except OSError:
            files = []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for p in files[:100]:
            st = p.stat()
            kind = OUT_KINDS[p.suffix.lower()]
            item = {"name": p.name, "kind": kind, "sizeMb": round(st.st_size / 1e6, 1), "mtime": int(st.st_mtime),
                    "url": "/r/m/" + self.tickets.issue(dev["id"], "file", p.resolve(), ip)}
            if kind == "video":
                pv = editor.out_preview_path(p.name)
                fresh = pv.is_file() and pv.stat().st_mtime >= st.st_mtime  # 같은 이름으로 다시 만든 완성본이면 옛 미리보기는 안 씀
                item["preview"] = {"exists": fresh, "url": "/r/m/" + self.tickets.issue(dev["id"], "file", pv.resolve(), ip) if fresh else None}
                item["qa"] = self.qa.get(p.name)
            out.append(item)
        return {"outputs": out}

    def r_choices(self, dev):
        styles = [s["name"] for s in style.list_styles()]
        try:
            chans = [{"key": c["channelKey"], "name": clean(c.get("channel"), 60), "count": c.get("count", 0)}
                     for c in refs.listing()["channels"] if c.get("channelKey")]
        except OSError:
            chans = []
        return {"styles": styles, "refChannels": chans}

    # 확인 (한 번 쓰는 표 · 2분 · 기기·동작·인자에 묶음)
    def _confirm(self, dev, action, args, text):
        h = hashlib.sha256(json.dumps([action, args], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        now = self.clock()
        self.confirms = {k: v for k, v in self.confirms.items() if v[3] > now}
        tok = secrets.token_urlsafe(16)
        self.confirms[tok] = (dev["id"], action, h, now + CONFIRM_TTL)
        return {"ok": False, "confirm": {"text": text, "token": tok}}

    def _confirmed(self, dev, action, args, tok):
        if not isinstance(tok, str):
            return False
        it = self.confirms.pop(tok, None)
        h = hashlib.sha256(json.dumps([action, args], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return bool(it and it[0] == dev["id"] and it[1] == action and it[2] == h and it[3] > self.clock())

    def r_action(self, dev, b):
        action, args = b.get("action"), b.get("args") if isinstance(b.get("args"), dict) else {}
        if not isinstance(action, str) or action not in ACTIONS:  # 목록·객체(해시 안 됨)도 400 (500·오류 기록 없이)
            raise ActionError("할 수 없는 동작이에요")
        if action == "cancel":
            return self.r_cancel(dev, args.get("job"))
        return ACTIONS[action](self, dev, args, b.get("confirm"))

    def _go(self, dev, label, fn, target):
        if not self.limiter.action_ok(dev["id"]):  # 작업을 시작하는 요청만 2초에 한 번 (확인 묻기는 세지 않음)
            raise ActionError("조금 뒤에 다시 눌러 주세요", 429)
        who = clean(dev["name"], 40)

        def run():  # 휴대폰에서 시킨 작업은 엔진·Deno 를 스스로 설치·업데이트하지 않음 (PC 에서만 · D-027)
            with core.no_self_update():
                return fn()
        ok = self.bridge.start_job(label, run, by=f"휴대폰 · {who}")
        if not ok:
            raise ActionError(BUSY_MSG, 409)
        self.log(f"원격 · {who} · {label}" + (f" · {clean(target, 80)}" if target else ""))
        return {"ok": True, "job": label}

    def r_cancel(self, dev, want=None):
        """멈추기. want: 휴대폰이 보고 있던 작업 번호(/r/status 의 job.id · 없어도 됨) — 그 작업이 끝나고 다음 작업이 돌면 409 (다른 작업을 멈추지 않게)."""
        if want is not None and (not isinstance(want, int) or isinstance(want, bool)):
            raise ActionError("잘못된 요청이에요")
        snap = self.bridge.job() if self.bridge else {}
        name = snap.get("name")
        if not name:
            raise ActionError("지금 하는 작업이 없어요", 409)
        if want is not None and want != snap.get("id"):
            raise ActionError(STALE_CANCEL_MSG, 409)
        if name not in STOPPABLE:
            raise ActionError(NOT_STOPPABLE_MSG, 409)
        editor.cancel_export()
        self.log(f"원격 · {clean(dev['name'], 40)} · 멈추기")
        return {"ok": True}

    def r_forget(self, dev):
        self.revoke([dev["id"]], why="휴대폰에서")
        return {"ok": True}

    def r_notify_test(self, dev):
        now = self.clock()
        if now - self._last_test < NOTIFY_TEST_GAP:
            raise ActionError("알림 시험은 1분에 한 번만 보낼 수 있어요 · 잠시 뒤 다시 눌러 주세요", 429)
        self._last_test = now
        self.test_notify()
        self.log(f"원격 · {clean(dev['name'], 40)} · 알림 시험")
        return {"ok": True}


# ---------- 허용 동작 (그 밖은 400) ----------

def _names_arg(v, n_max=10):
    if not isinstance(v, list) or not v or len(v) > n_max or not all(isinstance(x, str) for x in v):
        raise ActionError("영상을 1~10개 골라 주세요")
    out = []
    for n in dict.fromkeys(v):
        try:
            editor.video_path(n)
        except (ValueError, FileNotFoundError):
            raise ActionError("보관함에서 찾지 못한 영상이 있어요 · 목록을 새로 고친 뒤 다시 골라 주세요", 404) from None
        out.append(n)
    return out


def _one_video(v):
    if not isinstance(v, str):
        raise ActionError("영상을 골라 주세요")
    return _names_arg([v], 1)[0]


def _out_file(v, video=True):
    try:
        n = editor.safe_name(v)
    except ValueError:
        raise ActionError("잘못된 파일 이름이에요") from None
    p = (core.OUT / n).resolve()
    if p.parent != core.OUT.resolve() or not p.is_file() or (video and p.suffix.lower() != ".mp4"):
        raise ActionError("완성본을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요", 404)
    return p


def _a_download(svc, dev, a, tok):
    try:
        vid = youtube_id(a.get("url"))
    except ValueError as e:
        raise ActionError(str(e)) from None
    source.stop_backfill()
    log = svc.bridge.log

    def run():
        why = {}
        failed = core.download([vid], log, None, why=why)  # 원격에서는 쿠키를 쓰지 않음 (D-009 · 브라우저 고르기는 PC 화면에서만)
        if any((why.get(v) or {}).get("kind") in ("login", "empty") for v in failed):  # 막힘(REMOTE_BLOCKED_MSG)은 할 일이 이미 들어 있음
            log("  PC에서 '로그인 정보로 받기'로 브라우저를 골라 다시 받아 주세요")
        return {"failed": failed, "why": why}
    return svc._go(dev, "보관함에 담기", run, vid)


def _a_analyze(svc, dev, a, tok):
    names = _names_arg(a.get("names"))
    again = sum((core.adir(n) / "transcript_timeline.md").exists() for n in names)
    if again and not svc._confirmed(dev, "analyze", {"names": names}, tok):
        return svc._confirm(dev, "analyze", {"names": names},
                            f"이미 편집점을 찾은 영상이 {again}개 있어요. 다시 찾으면 편집실에서 만든 내 편집본은 그대로 두고, 새 가편집을 옆에 추가해요. "
                            "자막은 새로 받아쓴 것으로 바뀌어요 (이전 상태는 백업돼요). 계속할까요?")
    return svc._go(dev, "편집점 찾기", lambda: svc.bridge.analyze({"names": names, "model": "large-v3-turbo"}),
                   names[0] + (f" 외 {len(names) - 1}개" if len(names) > 1 else ""))


def _a_add_refs(svc, dev, a, tok):
    try:
        url = channel_url(a.get("url"))
    except ValueError as e:
        raise ActionError(str(e)) from None
    try:
        count = int(a.get("count") or 5)
    except (TypeError, ValueError):
        raise ActionError("받을 영상 수를 1~10 사이로 골라 주세요") from None
    if not 1 <= count <= 10 or isinstance(a.get("count"), bool):
        raise ActionError("받을 영상 수를 1~10 사이로 골라 주세요")
    args = {"url": url, "count": count}
    if count > 5 and not svc._confirmed(dev, "add_refs", args, tok):
        return svc._confirm(dev, "add_refs", args, f"학습용 영상 {count}개를 받고 스타일을 배워요 · 시간이 오래 걸릴 수 있어요. 시작할까요?")
    source.stop_backfill()
    log = svc.bridge.log
    return svc._go(dev, "학습용 영상 받기", lambda: svc.bridge.refs_job(lambda: refs.add_channel(url, count, "videos", log, None, True, False, None)), url)


def _a_learn_refs(svc, dev, a, tok):
    key = a.get("channel")
    keys = {c["key"]: c["name"] for c in svc.r_choices(dev)["refChannels"]}
    if not isinstance(key, str) or key not in keys:
        raise ActionError("그 채널을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요", 404)
    log = svc.bridge.log
    return svc._go(dev, "학습용 스타일 배우기", lambda: svc.bridge.refs_job(lambda: refs.learn_channel(key, log, False)), keys[key])


def _a_autoseq(svc, dev, a, tok):
    n = _one_video(a.get("name"))
    if not (core.adir(n) / "transcript.json").exists():
        raise ActionError("먼저 '편집점 찾기'를 해 주세요")
    sname = a.get("style")
    if not isinstance(sname, str) or sname not in [s["name"] for s in style.list_styles()]:
        raise ActionError("스타일을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요", 404)
    kinds = a.get("kinds") or ["long", "shorts"]
    if not isinstance(kinds, list) or not kinds or not set(kinds) <= {"long", "shorts"}:
        raise ActionError("롱폼·쇼츠 중에서 골라 주세요")
    kinds = tuple(k for k in ("long", "shorts") if k in kinds)
    params = next(s["params"] for s in style.list_styles() if s["name"] == sname)
    return svc._go(dev, "자동 가편집", lambda: editor.add_style_sequences(n, sname, params, kinds, svc.bridge.log), f"{n} · {sname}")


def _a_export(svc, dev, a, tok):
    n = _one_video(a.get("name"))
    seq, preset = a.get("seq"), a.get("preset") or "youtube"
    if preset not in ("youtube", "small"):
        raise ActionError("내보내기 설정을 다시 골라 주세요")
    if not isinstance(seq, str) or seq not in [q["id"] for q in svc._projects(n)]:
        raise ActionError("편집본을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요", 404)
    log = svc.bridge.log

    def run():  # 휴대폰에서 내보냈으면 같은 작업 안에서 540p 작은 미리보기까지 (LTE 로 볼 때 데이터를 아끼게)
        res = editor.export_saved(n, seq, preset, log)
        mp4 = next((x for x in res if isinstance(x, str) and x.lower().endswith(".mp4")), None) if isinstance(res, list) else None
        if mp4 and not editor.CANCEL.is_set():
            log("  휴대폰에서 보기 좋게 작은 미리보기도 만들어요")
            try:
                editor.out_preview(mp4, log)
            except Exception as e:  # noqa: BLE001 — 내보낸 영상은 그대로
                log(f"  작은 미리보기는 만들지 못했어요 · {scrub(e)}")
        return res
    return svc._go(dev, "내보내기", run, n)


def _a_qa(svc, dev, a, tok):
    f = _out_file(a.get("file"))
    meta = editor.EXPORT_META.get(f.name) or {}
    return svc._go(dev, "영상 검수", lambda: qa.check_video(f, meta.get("format"), meta.get("master"), editor.run_killable), f.name)


def _a_preview(svc, dev, a, tok):
    f = _out_file(a.get("file"))
    return svc._go(dev, "작은 미리보기 만들기", lambda: editor.out_preview(f.name, svc.bridge.log), f.name)


ACTIONS = {"download": _a_download, "analyze": _a_analyze, "add_refs": _a_add_refs, "learn_refs": _a_learn_refs, "autoseq": _a_autoseq,
           "export": _a_export, "qa": _a_qa, "preview": _a_preview, "cancel": None}


# ---------- 원격 리스너 (HTTP) ----------

class RemoteServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    # app._Server 와 같은 규칙 (D-030): Windows 의 SO_REUSEADDR 는 다른 프로세스가 듣고 있는 포트도 같이 잡음 →
    # FUTSAL_REMOTE_PORT 가 쓰이는 중이면 조용히 같이 듣지 않고 _new_listener 가 기다렸다 '열지 못했어요'로
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, addr, svc):
        self.svc = svc
        super().__init__(addr, RemoteHandler)

    def handle_error(self, request, client_address):
        """처리 밖으로 나온 오류: socketserver 기본은 보낸 곳 주소와 추적을 그대로 오류 출력(→ studio-error.log)에 → 비밀을 지우고 추적만."""
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):  # 휴대폰·터널이 연결을 끊음 — 오류가 아님 (app._Server 와 같게)
            return
        _trace()


class RemoteHandler(BaseHTTPRequestHandler):
    server_version = "futsal-remote"
    sys_version = ""
    timeout = 20  # 느리게 보내는 연결이 스레드를 오래 잡지 않게
    error_content_type = "application/json; charset=utf-8"
    error_message_format = '{"error": "잘못된 요청이에요"}'
    _origin = None

    def log_message(self, *a):  # 주소·표(ticket)가 표준 출력에 남지 않게
        pass

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_OPTIONS(self):
        self._route("OPTIONS")

    def _no(self):
        self._json(405, {"error": "할 수 없는 요청이에요"})

    do_PUT = do_DELETE = do_PATCH = _no

    def do_HEAD(self):
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # 응답
    def _common(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        if self._origin:
            self.send_header("Access-Control-Allow-Origin", self._origin)
        self.send_header("Vary", "Origin")

    def _json(self, code, obj, extra=None):
        try:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:  # 짝 없는 대리 문자(반쪽 이모지)가 든 파일 이름 등 → \uXXXX 로 (app.Handler._send 와 같게)
            data = json.dumps(obj, ensure_ascii=True).encode("ascii")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self._common()
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method):
        svc = self.server.svc
        if not svc.sem.acquire(blocking=False):
            return self._json(503, {"error": "지금은 바빠요 · 잠시 뒤 다시 해 주세요"}, {"Retry-After": "2"})
        try:
            self._handle(method, svc)
        except (ConnectionError, TimeoutError):  # 휴대폰이 먼저 끊음 (영상 앞뒤로 옮기기 · Windows 는 ConnectionAbortedError 10053 도)
            pass
        except Exception:  # noqa: BLE001 — 오류 글·추적은 응답에 넣지 않음
            _trace()
            try:
                self._json(500, {"error": "문제가 생겼어요 · 잠시 뒤 다시 해 주세요"})
            except OSError:
                pass
        finally:
            svc.sem.release()

    def _handle(self, method, svc):
        # 1. Host: 터널(cloudflared)이 붙이는 표시만 · 개발 때만 루프백 주소
        host, port = self.headers.get("Host") or "", svc.port()
        if not (host == SENTINEL or (_dev() and host in (f"127.0.0.1:{port}", f"localhost:{port}"))):
            return self._json(403, {"error": "forbidden"})
        if self.headers.get("Transfer-Encoding"):  # 본문은 Content-Length 로만 (조각 본문은 읽지 않고 거절 · 잠금 횟수에 안 셈)
            self.close_connection = True
            return self._json(411, {"error": "요청을 보내는 방식이 맞지 않아요 · 페이지를 새로 고친 뒤 다시 해 주세요"})
        u = urlparse(self.path)
        path = u.path
        # 3. 요청 수 (Cloudflare 가 붙이는 실제 주소 기준)
        ip = (self.headers.get("Cf-Connecting-Ip") or "").strip()[:64] or self.client_address[0]
        if svc.limiter.locked(ip):
            return self._json(429, {"error": "잠시 막았어요 · 15분 뒤 다시 해 주세요"}, {"Retry-After": str(LOCKOUT)})
        if not svc.limiter.take("open" if path in ("/r/ping", "/r/pair") else "auth", ip):
            return self._json(429, {"error": "너무 자주 눌렀어요 · 잠시 뒤 다시 해 주세요"}, {"Retry-After": "5"})
        # 4. CORS · Origin · JSON
        origin = self.headers.get("Origin")
        self._origin = origin if origin in svc.origins() else None
        if method == "OPTIONS":
            if not self._origin:
                return self._json(403, {"error": "forbidden"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self._common()
            self.end_headers()
            return
        media = path.startswith("/r/m/")
        if not media and path != "/r/ping" and not self._origin:
            return self._json(403, {"error": "허용되지 않은 곳에서 온 요청이에요"})
        body = b""
        if method == "POST":
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._json(415, {"error": "잘못된 요청이에요"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(400, {"error": "잘못된 요청이에요"})
            if n < 0 or n > BODY_MAX:
                self.close_connection = True
                return self._json(413, {"error": "요청이 너무 커요"})
            body = self.rfile.read(n) if n else b""
        # 5. 확인 (표 · 서명) → JSON → 6. 허용 목록
        if media:
            return self._media(svc, path[5:], ip)
        if path == "/r/ping" and method == "GET":
            return self._json(200, svc.r_ping())
        if path == "/r/pair" and method == "POST":  # 열린 길: 증명만 (JSON 은 깊이 폭탄도 400)
            b = _json_body(body)
            if b is None:
                return self._json(400, {"error": "잘못된 요청이에요"})
            try:
                return self._json(200, svc.r_pair(b))
            except PairError as e:
                self._fail(svc, ip)
                return self._json(403, {"error": str(e), "code": e.code, "time": int(svc.clock())})
        try:  # 서명은 받은 본문 바이트 그대로 확인 → 그 뒤에만 JSON 을 읽음
            dev = svc.auth.verify(self.headers.get("Authorization"), method, self.path, body, svc.host())
        except AuthError as e:
            if e.status == 401:
                self._fail(svc, ip)
            return self._json(e.status, {"error": str(e), "code": e.code, "time": int(svc.clock())})
        b = _json_body(body)
        if b is None:
            return self._json(400, {"error": "잘못된 요청이에요"})
        svc.touch(dev)
        routes = {("GET", "/r/status"): lambda: svc.r_status(dev, _int((parse_qs(u.query).get("since") or ["0"])[0])),
                  ("GET", "/r/library"): lambda: svc.r_library(dev, ip), ("GET", "/r/outputs"): lambda: svc.r_outputs(dev, ip),
                  ("GET", "/r/choices"): lambda: svc.r_choices(dev), ("POST", "/r/action"): lambda: svc.r_action(dev, b),
                  ("POST", "/r/cancel"): lambda: svc.r_cancel(dev, b.get("job")), ("POST", "/r/forget"): lambda: svc.r_forget(dev),
                  ("POST", "/r/notify-test"): lambda: svc.r_notify_test(dev)}
        if (method, path) == ("POST", "/r/remote-off"):  # 더 안전하게만 (휴대폰에서 켜는 길은 없음)
            svc.log(f"원격 · {clean(dev['name'], 40)} · 원격 접속 끄기")
            self._json(200, {"ok": True})
            threading.Timer(0.3, svc.turn_off, args=("user",)).start()
            return
        fn = routes.get((method, path))
        if not fn:
            return self._json(404, {"error": "없는 주소예요"})
        try:
            return self._json(200, fn())
        except ActionError as e:
            return self._json(e.status, {"ok": False, "error": str(e)})

    def _fail(self, svc, ip):
        if svc.limiter.failed(ip) and svc.clock() - svc._lockout_logged > 600:
            svc._lockout_logged = svc.clock()
            svc.log("원격 접속 · 잘못된 연결 시도가 많아 잠시 막았어요")

    def _media(self, svc, tid, ip):
        it = svc.tickets.get(tid, ip) if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", tid or "") else None
        if not it:  # 없거나·지났거나·다른 곳(IP 대역)에서 온 표 → 휴대폰 화면이 목록을 다시 받아 새 표로
            return self._json(404, {"error": "다시 불러와 주세요"})
        device, kind, ref = it[:3]
        try:
            if kind == "poster":
                p = editor.poster(editor.safe_name(ref)).resolve()
                allowed = p.suffix.lower() == ".jpg" and core.ANALYSIS.resolve() in p.parents
            else:
                p = Path(ref).resolve()
                allowed = p.parent in (core.OUT.resolve(), core.VIDEOS.resolve()) or p.parent.parent == editor.remote_previews().resolve()
        except Exception:  # noqa: BLE001 — 그림을 못 만들었거나 파일이 사라짐
            return self._json(404, {"error": "파일을 찾지 못했어요"})
        ctype = MEDIA_TYPES.get(p.suffix.lower())
        if not allowed or not ctype or not p.is_file():
            return self._json(404, {"error": "파일을 찾지 못했어요"})
        if not svc.limiter.media_enter(device):
            return self._json(503, {"error": "동시에 여는 영상이 많아요 · 잠시 뒤 다시 해 주세요"}, {"Retry-After": "3"})
        try:
            self._send_range(p, ctype)
        finally:
            svc.limiter.media_leave(device)

    def _send_range(self, p, ctype):
        size = p.stat().st_size
        rng = (self.headers.get("Range") or "").strip()
        start, end, part = 0, size - 1, False
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng) if rng and "," not in rng else None  # 여러 구간 요청은 무시하고 전체
        if m and (m.group(1) or m.group(2)):
            a, b = m.group(1), m.group(2)
            if a:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            else:
                n = int(b)
                start, end = max(0, size - n), size - 1
                if n == 0:
                    start = size
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self._common()
                self.end_headers()
                return
            end = min(end, start + MEDIA_CHUNK - 1)
            part = True
        self.send_response(206 if part else 200)
        if part:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1 if size else 0))
        self.send_header("Content-Disposition", "inline")
        self._common()
        self.end_headers()
        if not size:
            return
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 18, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _json_body(body):
    """POST 본문 → dict · 아니면 None (깊이 폭탄의 RecursionError 도 400 으로). 반쪽 이모지('\\ud83d')는 '�'로
    (app.Handler._body 와 같은 core.clean_json · 기기 이름이 remote.json·기록에 못 쓰여 짝짓기가 멈추지 않게)."""
    try:
        b = json.loads(body or b"{}")
    except (ValueError, RecursionError):
        return None
    if not isinstance(b, dict):
        return None
    try:
        return core.clean_json(b)
    except RecursionError:
        return None


# ---------- app 이 부르는 곳 ----------

SVC = Service()


def init(bridge):
    SVC.init(bridge)
    if SVC.store.data.get("enabled"):
        SVC.turn_on()  # 켜 둔 채로 앱을 껐거나 업데이트로 다시 켜졌으면 이어서 켬
    return SVC
