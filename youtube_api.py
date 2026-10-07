"""유튜브 바로 올리기의 연결 부품 — Google OAuth 2.0(설치형 앱: 루프백 + PKCE)과 YouTube Data API v3 를 표준 라이브러리(urllib)로만.

- 앱 모듈은 updater 하나만 import (표준 라이브러리만 쓰는 실행기 · 저장 write_atomic·인증서 설정) · youtube_upload 가 씀 ·
  불러올 때 아무것도 실행하지 않음 (Windows DPAPI·HTTPS 설정도 쓸 때만).
- 로그인: 사용자가 만든 Google Cloud 'OAuth 클라이언트(데스크톱 앱)'로, 127.0.0.1 의 아무 포트에 한 번만 여는 작은 서버가 결과를 받음
  (state·Host 확인, 한 번만, 10분 제한). 범위는 youtube.force-ssl 하나 — captions.insert 가 force-ssl(또는 youtubepartner)만 받고,
  videos.insert·thumbnails.set·playlistItems.insert·playlists.list 도 이것으로 됨 (D-035).
- 토큰·클라이언트 보안 비밀번호·업로드 세션 주소는 비밀 → Windows 는 DPAPI(이 사용자만 풂), 그 밖은 권한 600 파일.
  기록·오류 문장·화면 응답에 넣지 않음 (오류 문장은 한국어 틀 + Google 의 reason 낱말만).
- 업로드: 재개 가능한 업로드(8 MiB 조각 · 308 Resume Incomplete + Range · 끊기면 기다렸다가 상태를 물어 이어서 · 404 = 세션 만료).
문서(2026-10-07): developers.google.com/identity/protocols/oauth2/native-app · developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
· developers.google.com/youtube/v3/docs/{videos/insert, thumbnails/set, captions/insert, playlistItems/insert, playlists, channels, errors}
"""
import base64
import hashlib
import hmac
import html
import http.client
import json
import os
import random
import re
import secrets
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import updater

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
ENV = "FUTSAL_GOOGLE_API"  # 시험용: http://127.0.0.1:<포트> (가짜 Google) — 개발 폴더(tests/fake_google.py 가 있음)에서만 · 127.0.0.1 http 만
ENDPOINTS = {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
             "revoke": "https://oauth2.googleapis.com/revoke", "api": "https://www.googleapis.com/youtube/v3",
             "upload": "https://www.googleapis.com/upload/youtube/v3"}
UNIT = 256 * 1024                 # 마지막이 아닌 조각은 이 배수
CHUNK = 8 * 1024 * 1024           # 한 번에 보내는 양 (8 MiB)
BACKOFF = (1, 2, 4, 8, 16, 32, 60, 60)   # 끊기거나 5xx 일 때 기다리는 초 (+ 흔들기)
JITTER = 1.0
NET_PATIENCE = 30 * 60            # 인터넷이 끊기면 이만큼은 1분마다 다시 해 봄 (세션 주소는 며칠 유효 · [멈추기]는 바로 들음)
STUCK_MAX = 3                     # 308 인데 받은 데가 늘지 않는 일이 이만큼 이어지면 쉬었다가 상태를 물음 (끝없이 같은 조각을 보내지 않게)
RATE_WAIT = 60                    # rateLimitExceeded 면 한 번만 이만큼 쉬고 다시
TIMEOUT = 60
MAX_JSON = 2 * 1024 * 1024
LOGIN_TIMEOUT = 600
CLIENT_MAX = 64 * 1024
_HDR = {"dpapi": b"FSY1D\n", "plain": b"FSY1P\n"}
_ENTROPY = b"futsal-studio/youtube/v1"
_CID = re.compile(r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$")
_REASON = re.compile(r"[^A-Za-z0-9_.-]")


_FAKE_MARK = Path(__file__).resolve().parent / "tests" / "fake_google.py"   # 배포본(manifest·업데이트)에는 tests/ 가 없음
_NOTED = []


def _test_base():
    """시험 스위치: FUTSAL_GOOGLE_API=http://127.0.0.1:<포트> 이고 개발 폴더(tests/fake_google.py 가 있음)일 때만. 그 밖은 None.
    (배포한 앱에서는 환경 변수가 있어도 진짜 Google https 만 씀 · localhost 는 hosts 파일을 타서 받지 않음)"""
    base = (os.environ.get(ENV) or "").strip().rstrip("/")
    if not base or not _FAKE_MARK.is_file():
        return None
    try:
        p = urllib.parse.urlsplit(base)
        port = p.port
    except ValueError:
        return None
    if p.scheme != "http" or p.hostname != "127.0.0.1" or not port or p.path or p.query or p.username or p.password:
        return None
    if not _NOTED:
        _NOTED.append(1)
        print("유튜브 바로 올리기 · 시험용 가짜 Google 주소를 써요 (FUTSAL_GOOGLE_API)", flush=True)
    return base


def endpoints():
    """Google 주소들. 시험 스위치(_test_base)가 켜졌으면 가짜 Google 로."""
    base = _test_base()
    if base:
        return {"auth": base + "/o/oauth2/v2/auth", "token": base + "/token", "revoke": base + "/revoke",
                "api": base + "/youtube/v3", "upload": base + "/upload/youtube/v3"}
    return dict(ENDPOINTS)


def valid_session_uri(uri):
    """업로드 세션 주소가 Google 업로드 주소인지 (토큰·영상을 다른 곳으로 보내지 않게 · 보낼 때마다 확인).
    https + www.googleapis.com 또는 *.googleapis.com (기본 포트) + /upload/youtube/v3/videos… · 시험 때는 가짜 Google 주소만."""
    try:
        a = urllib.parse.urlsplit(str(uri or ""))
        port = a.port
    except ValueError:
        return False
    if a.username or a.password or not a.hostname or not a.path.startswith("/upload/youtube/v3/videos"):
        return False
    b = urllib.parse.urlsplit(endpoints()["upload"])
    if (a.scheme, a.netloc) == (b.scheme, b.netloc):
        return True
    if b.scheme != "https":  # 시험 중에는 가짜 Google 말고는 안 됨
        return False
    host = a.hostname.lower()
    return a.scheme == "https" and port in (None, 443) and (host == "googleapis.com" or host.endswith(".googleapis.com"))


# ---------- 오류 ----------

MSG = {
    "relogin": "유튜브 연결이 끊겼어요 · [다시 연결하기]를 눌러 주세요",
    "client": "클라이언트 ID나 보안 비밀번호가 맞지 않아요 · '데스크톱 앱'으로 만든 JSON 파일을 다시 넣어 주세요",
    "redirect": "이 클라이언트는 '데스크톱 앱' 종류가 아니에요 · 안내 6단계대로 새로 만들어 주세요",
    "denied": "로그인을 취소했거나 테스트 사용자로 넣지 않은 계정이에요 · 안내 5단계를 확인해 주세요",
    "consent": "유튜브 권한 칸에 체크하지 않았어요 · 다시 연결하면서 꼭 체크해 주세요",
    "api_off": "Google Cloud에서 'YouTube Data API v3'를 켜지 않았어요 · 안내 3단계 (켠 뒤 몇 분 걸릴 수 있어요)",
    "no_channel": "이 Google 계정에는 유튜브 채널이 없어요 · 브랜드 계정이면 연결할 때 '풋살사관학교'를 골라 주세요",
    "quota": "오늘 쓸 수 있는 유튜브 API 양을 다 썼어요 · {reset}에 다시 채워져요 · 그 뒤에 [이어 올리기]/[마저 하기]를 눌러 주세요",
    "rate": "유튜브가 잠깐 쉬어 가래요 · 조금 뒤 [이어 올리기]를 눌러 주세요",
    "upload_limit": "이 채널이 하루에 올릴 수 있는 영상 수를 넘었어요 (유튜브 채널 한도) · 내일 다시 올려 주세요",
    "invalidTitle": "제목이 비었거나 쓸 수 없는 글자가 있어요 (100자까지 · < > 안 됨)",
    "invalidDescription": "설명은 5000바이트(한글 약 1,600자)까지예요 · < > 안 됨",
    "invalidTags": "태그는 합계 500자까지예요 (< > 안 됨)",
    "invalidCategoryId": "카테고리(스포츠)를 유튜브가 받지 않았어요 · 계속되면 스튜디오에서 골라 주세요",
    "invalidPublishAt": "예약 시각이 지났거나 잘못됐어요 · 새 시각을 골라 다시 올려 주세요",
    "defaultLanguageNotSet": "영상 언어(한국어) 설정을 유튜브가 받지 않았어요 · 잠시 뒤 다시 올리고, 계속되면 studio.log를 보내 주세요",
    "forbiddenPrivacySetting": "이 공개 설정으로는 올릴 수 없어요 · '비공개'로 올린 뒤 스튜디오에서 바꿔 주세요",
    "invalidVideoMetadata": "영상 정보(제목·설명·태그·공개 설정) 중 유튜브가 받지 않은 것이 있어요 · 고친 뒤 다시 올려 주세요",
    "forbidden": "유튜브에 올릴 권한이 없어요 · [다시 연결하기]에서 모든 권한에 체크해 주세요",
    "thumb_verify": "맞춤 썸네일을 올리려면 채널 인증(전화번호 확인)이 필요해요 · youtube.com/verify 에서 인증한 뒤 [썸네일 다시 올리기]를 눌러 주세요 · 영상은 이미 올라갔어요 "
                    "(이미 인증했다면 이 채널에 썸네일 권한이 없는 계정일 수 있어요 · [다시 연결하기]로 채널을 확인해 주세요)",
    "thumb_rate": "썸네일을 최근에 너무 많이 바꿨어요 · 나중에 [썸네일 다시 올리기]를 눌러 주세요",
    "thumb_bad": "썸네일 그림을 유튜브가 읽지 못했어요 · 썸네일 편집기에서 JPG로 다시 저장해 주세요",
    "caption_bad": "자막 파일(.srt)을 유튜브가 받지 않았어요 · 편집실에서 다시 내보내 주세요",
    "playlist_missing": "재생목록을 찾지 못했어요 · 지웠다면 다른 것을 골라 주세요",
    "playlist_full": "재생목록이 꽉 찼어요 · 다른 재생목록을 골라 주세요",
    "playlist_series": "이 영상은 이미 다른 시리즈 재생목록에 있어요 · 한 영상은 시리즈 하나에만 넣을 수 있어요 · 스튜디오에서 바꿔 주세요",
    "playlist_sort": "이 재생목록은 순서를 직접 정하게 되어 있지 않아요 · 스튜디오에서 넣어 주세요",
    "playlist_denied": "이 재생목록에는 넣을 수 없어요 (권한이 없거나 넣을 수 없는 종류) · 다른 재생목록을 골라 주세요",
    "playlist_title": "재생목록 이름을 넣어 주세요 (150자까지)",
    "not_found": "올린 영상을 찾지 못했어요 · 스튜디오에서 지웠는지 확인해 주세요",
    "processing": "유튜브가 아직 영상을 처리하는 중이에요 · 몇 분 뒤 [마저 하기]를 눌러 주세요",
    "redirect3xx": "유튜브가 이상한 응답(다른 주소로 보냄)을 줬어요 · 회사·학교 인터넷이나 백신 프로그램이 막고 있을 수 있어요",
    "network": "인터넷 연결이 끊겼어요 · 연결되면 [이어 올리기]를 눌러 주세요 (올린 데까지는 남아 있어요)",
    "network_long": "인터넷이 30분 넘게 끊겨서 멈췄어요 · 연결되면 [이어 올리기]를 눌러 주세요 (올린 데까지는 남아 있어요)",
    "server": "유튜브 서버가 지금 불안정해요 · 잠시 뒤 [이어 올리기]를 눌러 주세요",
    "session_expired": "올리던 연결이 만료됐어요 · 처음부터 다시 올려요",
    "cancelled": "멈췄어요 · [이어 올리기]로 올린 데부터 이어서 올려요",
    "file": "올릴 영상 파일이 없어요 · 편집실에서 다시 내보내 주세요",
    "login_timeout": "연결을 기다리다 시간이 지났어요 · [유튜브 계정 연결하기]를 다시 눌러 주세요",
    "login_state": "잘못된 연결 요청이에요 · [유튜브 계정 연결하기]를 다시 눌러 주세요",
    "unknown": "유튜브가 요청을 거절했어요 ({reason}) · 잠시 뒤 다시 해 보고, 계속되면 studio.log를 보내 주세요",
}


def reset_text():
    """할당량이 다시 채워지는 때 (youtube_upload 가 PC 시각으로 바꿔 끼움)."""
    return "Google 기준 자정(한국 시간 오후 4~5시)"


class ApiError(Exception):
    """사용자에게 보여 줄 한국어 안내(str) + 종류(kind) + HTTP 상태 + Google 의 reason 낱말. 토큰·주소·응답 원문은 담지 않음."""

    def __init__(self, kind, msg=None, status=None, reason=None):
        if msg is None and kind == "quota":
            msg = MSG["quota"].format(reset=reset_text())
        super().__init__(msg or MSG.get(kind) or MSG["unknown"].format(reason=reason or kind))
        self.kind, self.status, self.reason = kind, status, reason


class Cancelled(ApiError):
    def __init__(self):
        super().__init__("cancelled")


class _Net(Exception):
    """연결 실패 (인터넷 끊김·시간 초과·서버가 끊음)."""


def _clean_reason(r):
    return _REASON.sub("", str(r or ""))[:60] or None


def parse_google_error(body):
    """Google 오류 몸통 → (reason, 짧은 설명). API 꼴({"error": {...}})과 OAuth 꼴({"error": "invalid_grant"}) 모두."""
    try:
        d = json.loads((body or b"{}").decode("utf-8", "replace"))
    except ValueError:
        return None, None
    if not isinstance(d, dict):
        return None, None
    e = d.get("error")
    if isinstance(e, str):
        return _clean_reason(e), None
    if not isinstance(e, dict):
        return None, None
    errs = e.get("errors") if isinstance(e.get("errors"), list) else []
    reason = errs[0].get("reason") if errs and isinstance(errs[0], dict) else None
    info = None
    for det in e.get("details") if isinstance(e.get("details"), list) else []:
        if isinstance(det, dict) and det.get("reason"):
            info = det["reason"]
    if info == "SERVICE_DISABLED":  # API 를 켜지 않음 (errors[].reason 이 forbidden 으로 오는 때도 있음)
        reason = "accessNotConfigured"
    return _clean_reason(reason or info or e.get("status")), None


def classify(op, status, reason):
    """(작업, HTTP 상태, reason) → ApiError (한국어 안내)."""
    r = reason or ""
    if r in ("quotaExceeded", "dailyLimitExceeded"):
        return ApiError("quota", status=status, reason=r)
    if r in ("rateLimitExceeded", "userRateLimitExceeded") or (status == 429 and op != "thumbnails.set"):
        return ApiError("rate", status=status, reason=r)
    if r in ("accessNotConfigured", "SERVICE_DISABLED"):
        return ApiError("api_off", status=status, reason=r)
    if r == "youtubeSignupRequired":
        return ApiError("no_channel", status=status, reason=r)
    if r in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
        return ApiError("forbidden", status=status, reason=r)
    if status == 401:
        return ApiError("relogin", status=status, reason=r)
    if op == "thumbnails.set":
        if status == 403 and r in ("forbidden", ""):
            return ApiError("thumb_verify", status=status, reason=r)
        if r == "uploadRateLimitExceeded" or status == 429:
            return ApiError("rate", MSG["thumb_rate"], status, r)
        if r in ("invalidImage", "mediaBodyRequired", "mediaBodyTooLarge"):
            return ApiError("bad_meta", MSG["thumb_bad"], status, r)
    if op == "captions.insert":
        if r == "captionExists":
            return ApiError("exists", "이미 같은 자막이 올라가 있어요", status, r)
        if r in ("contentRequired", "invalidMetadata", "nameTooLong"):
            return ApiError("bad_meta", MSG["caption_bad"], status, r)
        if status == 403:
            return ApiError("forbidden", MSG["forbidden"], status, r)
    if op in ("playlistItems.insert", "playlists.insert"):
        if r == "playlistNotFound":
            return ApiError("not_found", MSG["playlist_missing"], status, r)
        if r == "playlistContainsMaximumNumberOfVideos":
            return ApiError("bad_meta", MSG["playlist_full"], status, r)
        if r == "videoAlreadyInAnotherSeriesPlaylist":
            return ApiError("bad_meta", MSG["playlist_series"], status, r)
        if r == "manualSortRequired":
            return ApiError("bad_meta", MSG["playlist_sort"], status, r)
        if r in ("playlistItemsNotAccessible", "playlistOperationUnsupported"):
            return ApiError("forbidden", MSG["playlist_denied"], status, r)
        if r in ("playlistTitleRequired", "invalidPlaylistSnippet"):
            return ApiError("bad_meta", MSG["playlist_title"], status, r)
    if r == "uploadLimitExceeded":
        return ApiError("upload_limit", status=status, reason=r)
    if r in ("invalidTitle", "invalidDescription", "invalidTags", "invalidCategoryId", "invalidPublishAt",
             "forbiddenPrivacySetting", "invalidVideoMetadata", "defaultLanguageNotSet"):
        return ApiError("bad_meta", MSG[r], status, r)
    if r == "forbiddenLicenseSetting":
        return ApiError("bad_meta", MSG["invalidVideoMetadata"], status, r)
    if r == "mediaBodyRequired" and op in ("upload", "videos.insert"):
        return ApiError("file", status=status, reason=r)
    if r == "videoNotFound" or (status == 404 and op != "upload"):
        return ApiError("not_found", status=status, reason=r)
    if status == 403:
        return ApiError("forbidden", status=status, reason=r)
    if status and status >= 500:
        return ApiError("server", status=status, reason=r)
    if status and 300 <= status < 400:  # Google API 는 다른 주소로 보내지 않음 (따라가지 않음 · _NoRedirect)
        return ApiError("server", MSG["redirect3xx"], status, r)
    return ApiError("error", MSG["unknown"].format(reason=r or f"HTTP {status}"), status, r)


# ---------- HTTP ----------

class _Keep308(urllib.request.BaseHandler):
    """308 Resume Incomplete 를 그대로 응답으로 (Location 없는 308 을 리디렉트로 보지 않게)."""
    handler_order = 400

    def http_error_308(self, req, fp, code, msg, hdrs):
        return fp


class _Track:
    """요청에 _futsal_box(목록)가 있으면 그 요청의 연결을 담아 둠 → [멈추기] 때 기다리던 응답을 바로 끊을 수 있게."""

    def do_open(self, http_class, req, **kw):
        box = getattr(req, "_futsal_box", None)
        if box is None:
            return super().do_open(http_class, req, **kw)

        def make(*a, **k):
            c = http_class(*a, **k)
            box.append(c)
            return c
        return super().do_open(make, req, **kw)


class _HTTP(_Track, urllib.request.HTTPHandler):
    pass


class _HTTPS(_Track, urllib.request.HTTPSHandler):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """다른 주소로 보내는 응답(301·302·303·307·308 Location)을 따라가지 않음 — urllib 은 따라갈 때 Authorization 도 그대로 옮겨서
    (다른 곳·http 로도) 토큰이 새어 나갈 수 있음. Google API 는 다른 주소로 보내지 않으므로 그 응답은 오류로 (classify)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = []


def _opener():
    """Google 요청용 opener (처음 쓸 때 한 번): 시스템 프록시(회사망)는 그대로 · 다른 주소로 보내는 응답은 따라가지 않음(_NoRedirect) ·
    HTTPS 인증서 설정은 updater.urlopen 과 같음(updater._ssl_context — 백신 'HTTPS 검사'에도 받게, 사슬·주소 확인은 그대로 · I-047).
    updater.urlopen 은 리디렉트를 따라가므로 그대로 쓰지 않고 같은 인증서 설정만 합침 (D-037)."""
    if not _OPENER:
        _OPENER.append(urllib.request.build_opener(_Keep308, _NoRedirect, _HTTP, _HTTPS(context=updater._ssl_context())))
    return _OPENER[0]


def _abort(box):
    """[멈추기]: 아직 못 보낸 내용은 버리고(SO_LINGER 0 → 닫을 때 RST) 기다리던 보내기·응답을 깨움."""
    for c in list(box):
        sock = getattr(c, "sock", None)
        if sock is None:
            continue
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except (OSError, struct.error):
            pass
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _send(method, url, data=None, headers=None, timeout=TIMEOUT, limit=MAX_JSON, box=None):
    """요청 하나 → (상태, 헤더, 몸통 앞부분). 연결 실패는 _Net. Cancelled(조각 읽다 멈춤)는 그대로 올라감."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    if box is not None:
        req._futsal_box = box
    try:
        r = _opener().open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(limit + 1)
        except (OSError, http.client.HTTPException):
            body = b""
        finally:
            e.close()
        return e.code, e.headers, body
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        raise _Net(type(e).__name__)
    try:
        with r:
            body = r.read(limit + 1)
            return r.status, r.headers, body
    except (OSError, http.client.HTTPException) as e:
        raise _Net(type(e).__name__)


def _json(body):
    try:
        d = json.loads((body or b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


# ---------- 비밀 저장 (DPAPI · 권한 600) ----------

def _use_dpapi():
    return sys.platform == "win32"


_CRYPT = {}


def _dpapi(data, encrypt):
    """Windows DPAPI (CryptProtectData/CryptUnprotectData · 이 Windows 사용자만 풂 · 화면 묻기 없음). 이 모듈 전용 WinDLL 이라
    다른 모듈의 ctypes.windll 함수 설정을 바꾸지 않음."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    if not _CRYPT:
        c32 = ctypes.WinDLL("crypt32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        c32.CryptProtectData.argtypes = [ctypes.POINTER(BLOB), wintypes.LPCWSTR, ctypes.POINTER(BLOB), ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
        c32.CryptUnprotectData.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB), ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
        for fn in (c32.CryptProtectData, c32.CryptUnprotectData):
            fn.restype = wintypes.BOOL
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p
        _CRYPT.update(c32=c32, k32=k32, BLOB=BLOB)
    BLOB = _CRYPT["BLOB"]
    src = ctypes.create_string_buffer(bytes(data), len(data))
    ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    bin_ = BLOB(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    bent = BLOB(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if encrypt:
        ok = _CRYPT["c32"].CryptProtectData(ctypes.byref(bin_), "futsal-studio youtube", ctypes.byref(bent), None, None,
                                            0x1, ctypes.byref(out))  # CRYPTPROTECT_UI_FORBIDDEN
    else:
        ok = _CRYPT["c32"].CryptUnprotectData(ctypes.byref(bin_), None, ctypes.byref(bent), None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI 실패")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _CRYPT["k32"].LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(raw):
    """비밀 bytes → (방식, 저장할 bytes). Windows 는 DPAPI, 그 밖은 그대로(파일 권한 600 으로 지킴)."""
    if _use_dpapi():
        return "dpapi", _dpapi(raw, True)
    return "plain", bytes(raw)


def unprotect(kind, data):
    if kind == "dpapi":
        return _dpapi(data, False)
    if kind == "plain":
        return bytes(data)
    raise ValueError("모르는 저장 방식")


def save_secret(path, obj):
    """비밀 dict 를 파일로 (머리 FSY1D/FSY1P + 내용 · updater.write_atomic: 임시 파일 → 바꿔 끼우기 · 권한 600 · 폴더 700)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    kind, data = protect(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    updater.write_atomic(path, _HDR[kind] + data, mode=0o600)  # 처음부터 600 · Windows 잠금은 기다렸다 다시 · 실패하면 임시 파일 지움


def platform_kind():
    """이 PC 가 쓰는 저장 방식 (Windows 'dpapi' · 그 밖 'plain'). 읽을 때도 이 방식만 받음 — Windows 에 놓인 평문 파일을 쓰지 않게."""
    return "dpapi" if _use_dpapi() else "plain"


def load_secret(path):
    """비밀 파일 → dict. 없거나 깨졌거나(다른 PC·다른 사용자 DPAPI 포함) 머리가 이 PC 방식이 아니면 None ('다시 연결해 주세요')."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    kind = platform_kind()
    hdr = _HDR[kind]
    if not raw.startswith(hdr):
        return None
    try:
        d = json.loads(unprotect(kind, raw[len(hdr):]).decode("utf-8"))
    except Exception:  # noqa: BLE001 — DPAPI 실패·깨진 내용 모두 '없음'
        return None
    return d if isinstance(d, dict) else None


def delete_secret(path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


# ---------- 클라이언트 (사용자가 만든 OAuth 데스크톱 앱) ----------

def parse_client(text=None, client_id=None, client_secret=None):
    """내려받은 client_secret JSON({"installed": {...}}) · 그 안쪽 dict · 두 칸(ID·보안 비밀번호) → ({client_id, client_secret, project_id}, 알림 또는 None).
    JSON 에 보안 비밀번호가 없으면(2025년부터 만든 뒤에는 다시 보여 주지 않음) 칸에 넣은 비밀번호를 씀."""
    project = None
    if text is not None and str(text).strip():
        text = str(text)
        if len(text) > CLIENT_MAX:
            raise ApiError("client", "파일이 너무 커요 · Google Cloud에서 받은 클라이언트 JSON 파일을 골라 주세요")
        try:
            d = json.loads(text)
        except ValueError:
            raise ApiError("client", "JSON 파일을 읽지 못했어요 · Google Cloud에서 [JSON 다운로드]로 받은 파일을 그대로 넣어 주세요") from None
        if not isinstance(d, dict):
            raise ApiError("client", "JSON 파일 모양이 달라요 · Google Cloud에서 받은 클라이언트 JSON 파일을 넣어 주세요")
        if "web" in d and "installed" not in d:
            raise ApiError("client", "'웹 애플리케이션'이 아니라 '데스크톱 앱'으로 만들어 주세요 (안내 6단계)")
        inner = d.get("installed") if isinstance(d.get("installed"), dict) else d
        client_id = inner.get("client_id") or client_id
        client_secret = inner.get("client_secret") or client_secret
        project = inner.get("project_id") if isinstance(inner.get("project_id"), str) else None
    cid, sec = str(client_id or "").strip(), str(client_secret or "").strip()
    if not _CID.match(cid):
        raise ApiError("client", "클라이언트 ID 모양이 달라요 · '숫자-글자.apps.googleusercontent.com' 꼴이에요 (안내 6·7단계)")
    if not sec:
        raise ApiError("client", "보안 비밀번호가 없어요 · 클라이언트를 만들 때 바로 받은 JSON 파일을 넣거나, 클라이언트 화면의 "
                                 "[보안 비밀번호 추가]로 새로 만든 비밀번호를 아래 칸에 붙여 넣어 주세요")
    if not (10 <= len(sec) <= 200 and re.fullmatch(r"[\x21-\x7e]+", sec)):
        raise ApiError("client", "보안 비밀번호 모양이 달라요 · 복사할 때 앞뒤가 빠지지 않았는지 확인해 주세요")
    warn = None if sec.startswith("GOCSPX-") else "보안 비밀번호는 보통 'GOCSPX-'로 시작해요 · 맞는지 한 번 더 확인해 주세요"
    return {"client_id": cid, "client_secret": sec, "project_id": (project or "")[:100]}, warn


def pkce_pair():
    """PKCE (RFC 7636): 64자 verifier(영문·숫자·-_) + S256 challenge."""
    v = secrets.token_urlsafe(48)
    return v, challenge(v)


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def auth_url(client, redirect, chal, state):
    q = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
         "code_challenge": chal, "code_challenge_method": "S256", "state": state, "access_type": "offline",
         "prompt": "select_account consent"}
    return endpoints()["auth"] + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def _token_call(form, timeout=30):
    body = urllib.parse.urlencode(form).encode("ascii")
    try:
        st, _, raw = _send("POST", endpoints()["token"], body, {"Content-Type": "application/x-www-form-urlencoded"}, timeout)
    except _Net:
        raise ApiError("network", "인터넷 연결을 확인해 주세요 · Google에 연결하지 못했어요") from None
    d = _json(raw)
    if st == 200 and d.get("access_token"):
        return d
    reason, _ = parse_google_error(raw)
    if reason in ("invalid_client", "unauthorized_client"):
        raise ApiError("client", status=st, reason=reason)
    if reason == "redirect_uri_mismatch":
        raise ApiError("client", MSG["redirect"], st, reason)
    if reason == "invalid_grant":
        raise ApiError("relogin", status=st, reason=reason)
    if st and st >= 500:
        raise ApiError("server", status=st, reason=reason)
    raise ApiError("error", MSG["unknown"].format(reason=reason or f"HTTP {st}"), st, reason)


def _token_record(d, prev=None):
    now = time.time()
    out = dict(prev or {})
    out.update(access_token=d["access_token"], expires_at=now + max(60, int(d.get("expires_in") or 3600)))
    if d.get("refresh_token"):
        out["refresh_token"] = d["refresh_token"]
    if d.get("scope"):
        out["scope"] = d["scope"]
    if d.get("refresh_token_expires_in"):  # '시간 제한 액세스'를 고른 때만 옴 (테스트 상태 앱의 7일과는 다름 · 그것은 알려 주지 않음)
        out["refresh_expires_at"] = now + int(d["refresh_token_expires_in"])
    return out


def exchange_code(client, code, verifier, redirect):
    d = _token_call({"code": code, "client_id": client["client_id"], "client_secret": client["client_secret"],
                     "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"})
    if not d.get("refresh_token"):
        raise ApiError("relogin", "Google이 오래 쓰는 연결 열쇠를 주지 않았어요 · [유튜브 계정 연결하기]를 다시 눌러 주세요")
    rec = _token_record(d)
    rec["obtained_at"] = time.time()
    return rec


def refresh(client, tok):
    """access_token 새로 받기. 취소됐거나 만료된 연결(invalid_grant: 테스트 상태 7일·6개월 안 씀·직접 해제)이면 relogin."""
    if not tok.get("refresh_token"):
        raise ApiError("relogin")
    d = _token_call({"client_id": client["client_id"], "client_secret": client["client_secret"],
                     "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    return _token_record(d, tok)


def revoke(tok, timeout=5):
    """연결 끊기 (되도록 · 실패해도 계속) → Google 이 끊었으면 True (인터넷 끊김·시간 초과·거절이면 False)."""
    t = (tok or {}).get("refresh_token") or (tok or {}).get("access_token")
    if not t:
        return False
    try:
        st, _, _ = _send("POST", endpoints()["revoke"], urllib.parse.urlencode({"token": t}).encode("ascii"),
                         {"Content-Type": "application/x-www-form-urlencoded"}, timeout)
        return st == 200
    except _Net:
        return False


# ---------- 로그인 (루프백 한 번만) ----------

_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px/1.6 -apple-system,"Malgun Gothic",sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;background:#f3f4f6;color:#111827}}
main{{background:#fff;border-radius:14px;padding:32px 36px;max-width:520px;box-shadow:0 4px 16px rgba(16,24,40,.08)}}h1{{font-size:20px;margin:0 0 8px}}
p{{margin:0;color:#4b5563}}@media (prefers-color-scheme:dark){{body{{background:#0d1117;color:#e6edf3}}main{{background:#161b22}}p{{color:#a8b3bf}}}}</style>
</head><body><main><h1>{title}</h1><p>{msg}</p></main></body></html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    timeout = 30  # 브라우저가 미리 열어 두고 쓰지 않는 연결이 결과 요청을 막지 않게

    def log_message(self, *a):
        pass

    def _page(self, code, title, msg):
        data = _PAGE.format(title=html.escape(title), msg=html.escape(msg)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.flow._callback(self)

    def do_POST(self):
        self._page(405, "잘못된 요청", "이 주소는 쓰지 않아요")


class _LoopbackServer(ThreadingHTTPServer):
    """로그인 결과를 받는 한 번짜리 서버: 같은 포트를 다른 프로그램이 함께 잡지 못하게 (SO_REUSEADDR 끔 · Windows 는 SO_EXCLUSIVEADDRUSE)."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class LoginFlow:
    """'유튜브 계정 연결하기' 한 번: 127.0.0.1 의 빈 포트에 작은 서버를 열고 Google 이 돌려보내는 결과를 한 번만 받음."""

    def __init__(self):
        self._lock = threading.Lock()
        self._srv = None
        self._snap = {"state": "idle", "url": None, "error": None}

    def snapshot(self):
        with self._lock:
            return dict(self._snap)

    def start(self, client, on_token, timeout=LOGIN_TIMEOUT):
        """로그인 주소를 돌려줌 (브라우저로 여는 것은 부르는 쪽). 앞의 로그인은 그만둠."""
        self.cancel()
        verifier, chal = pkce_pair()
        state = secrets.token_urlsafe(24)
        srv = _LoopbackServer(("127.0.0.1", 0), _CallbackHandler)
        srv.flow = self
        port = srv.server_address[1]
        redirect = f"http://127.0.0.1:{port}/"
        url = auth_url(client, redirect, chal, state)
        ctx = {"srv": srv, "client": client, "verifier": verifier, "state": state, "redirect": redirect, "port": port,
               "on_token": on_token, "used": False, "deadline": time.time() + timeout}
        srv.ctx = ctx
        with self._lock:
            self._srv = srv
            self._snap = {"state": "waiting", "url": url, "error": None}
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        t = threading.Timer(timeout, self._expire, args=(srv,))
        t.daemon = True
        t.start()
        return url

    def _expire(self, srv):
        with self._lock:
            if self._srv is not srv:
                return
            self._snap = {"state": "error", "url": None, "error": MSG["login_timeout"]}
        self._close(srv)

    def cancel(self):
        with self._lock:
            srv, self._srv = self._srv, None
            if self._snap.get("state") == "waiting":
                self._snap = {"state": "idle", "url": None, "error": None}
        if srv:
            self._close(srv)

    def _close(self, srv):
        def go():
            try:
                srv.shutdown()
                srv.server_close()
            except OSError:
                pass
        threading.Thread(target=go, daemon=True).start()
        with self._lock:
            if self._srv is srv:
                self._srv = None

    def _finish(self, srv, state, error=None, channel=None):
        with self._lock:
            if self._srv is srv:
                self._snap = {"state": state, "url": None, "error": error, "channel": channel}
        self._close(srv)

    def _callback(self, h):
        srv = h.server
        ctx = srv.ctx
        if (h.headers.get("Host") or "") != f"127.0.0.1:{ctx['port']}":
            return h._page(400, "잘못된 요청", "이 주소로는 열 수 없어요")
        u = urllib.parse.urlsplit(h.path)
        if u.path != "/":
            return h._page(404, "없는 주소", "이 창은 닫아도 돼요")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        if not hmac.compare_digest(str(q.get("state") or "").encode("utf-8"), ctx["state"].encode("utf-8")):
            return h._page(400, "잘못된 요청", MSG["login_state"])
        with self._lock:
            if ctx["used"] or self._srv is not srv:
                return h._page(400, "이미 처리했어요", "이 창은 닫고 스튜디오로 돌아가 주세요")
            ctx["used"] = True
        if q.get("error"):
            msg = MSG["denied"] if q["error"] == "access_denied" else MSG["unknown"].format(reason=_clean_reason(q["error"]) or "error")
            h._page(200, "연결하지 못했어요", msg + " · 이 창은 닫아도 돼요")
            return self._finish(srv, "error", msg)
        if not q.get("code"):
            h._page(400, "연결하지 못했어요", MSG["login_state"])
            return self._finish(srv, "error", MSG["login_state"])
        try:
            tok = exchange_code(ctx["client"], q["code"], ctx["verifier"], ctx["redirect"])
            if SCOPE not in str(tok.get("scope") or "").split():
                revoke(tok)
                raise ApiError("consent")
            channel = ctx["on_token"](tok)
        except ApiError as e:
            h._page(200, "연결하지 못했어요", f"{e} · 이 창은 닫고 스튜디오에서 다시 해 주세요")
            return self._finish(srv, "error", str(e))
        except Exception as e:  # noqa: BLE001 — 예상 못한 오류도 화면에는 쉬운 말로 (오류 출력에는 위치와 종류만)
            import traceback
            # 오류 글은 남기지 않음 — 토큰 교환·저장 중이라 글에 비밀이 섞일 수 있고 이 모듈은 remote.redact 를 못 씀 (D-037)
            sys.stderr.write("".join(traceback.format_tb(e.__traceback__)) + f"{type(e).__name__} (내용은 비밀이 섞일 수 있어 뺌)\n")
            msg = "연결하는 중에 문제가 생겼어요 · 스튜디오에서 다시 눌러 주세요"
            h._page(200, "연결하지 못했어요", msg)
            return self._finish(srv, "error", msg)
        title = (channel or {}).get("title") or ""
        h._page(200, "연결됐어요", f"{title + ' 채널과 ' if title else ''}연결됐어요 · 이 창은 닫고 스튜디오로 돌아가 주세요")
        self._finish(srv, "done", None, channel)


# ---------- API 손님 ----------

class _ChunkReader:
    """파일의 [start, start+length) 를 조금씩 읽어 보냄 · 읽을 때마다 멈추기(✕) 확인 · 진행률 알림 (1초에 4번까지)."""

    def __init__(self, f, start, length, cancel=None, progress=None):
        self.f, self.left, self.cancel, self.progress = f, length, cancel, progress
        self.sent, self._t = 0, 0.0
        f.seek(start)

    def read(self, n=-1):
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left)
        b = self.f.read(n)
        if not b:
            return b""
        self.left -= len(b)
        self.sent += len(b)
        now = time.monotonic()
        if self.progress and (now - self._t >= 0.25 or self.left <= 0):
            self._t = now
            self.progress(self.sent)
        return b


def _range_end(hdrs):
    """308 의 Range: bytes=0-N → N+1 (없으면 0)."""
    m = re.match(r"bytes=0-(\d+)", (hdrs.get("Range") or "") if hdrs else "")
    return int(m.group(1)) + 1 if m else 0


def _mime(path):
    ext = Path(path).suffix.lower()
    return {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
            ".webm": "video/webm"}.get(ext, "application/octet-stream")


class Client:
    """토큰 하나로 YouTube Data API 부르기. access_token 은 남은 시간이 1분 안쪽이면 새로 받고(잠금), 401 이면 한 번 새로 받아 다시.
    on_call(작업 이름): 부르기 전에 (할당량 세기) · on_token_saved(토큰): 새로 받은 토큰 저장 · cancel: 멈추기 Event."""

    def __init__(self, client, token, on_token_saved=None, on_call=None, cancel=None):
        self.client, self.token = client, dict(token)
        self._lock = threading.Lock()
        self.on_token_saved, self.on_call, self.cancel = on_token_saved, on_call, cancel

    def access_token(self, force=False):
        with self._lock:
            if force or not self.token.get("access_token") or float(self.token.get("expires_at") or 0) - time.time() < 60:
                self.token = refresh(self.client, self.token)
                if self.on_token_saved:
                    self.on_token_saved(dict(self.token))
            return self.token["access_token"]

    def _wait(self, secs):
        if self.cancel is not None:
            if self.cancel.wait(secs):
                raise Cancelled()
        else:
            time.sleep(secs)

    def request(self, op, method, url, data=None, headers=None, ok=(200,), timeout=TIMEOUT, charge=True):
        """Google API 한 번 (401 → 새 토큰으로 한 번 더 · rateLimit → RATE_WAIT 쉬고 한 번 더) → (상태, 헤더, JSON).
        할당량은 보내는 요청마다 셈 (rateLimit 뒤 다시 보내는 것도 Google 이 셈 · 401 은 인증 전에 막혀 세지 않음)."""
        rated, was401 = False, False
        for attempt in range(3):
            if charge and self.on_call and not was401:
                self.on_call(op)
            was401 = False
            h = dict(headers or {})
            h["Authorization"] = "Bearer " + self.access_token(force=attempt > 0 and not rated)
            try:
                st, hdrs, raw = _send(method, url, data, h, timeout)
            except _Net:
                raise ApiError("network") from None
            if st in ok:
                return st, hdrs, _json(raw)
            reason, _ = parse_google_error(raw)
            err = classify(op, st, reason)
            if st == 401 and err.kind == "relogin" and attempt == 0:
                was401 = True
                continue
            if err.kind == "rate" and not rated and op != "thumbnails.set":
                rated = True
                self._wait(RATE_WAIT)
                continue
            raise err
        raise ApiError("relogin")

    # --- 읽기 ---
    def channel_mine(self):
        _, _, d = self.request("channels.list", "GET", endpoints()["api"] + "/channels?part=snippet&mine=true")
        items = d.get("items") or []
        if not items:
            raise ApiError("no_channel")
        it = items[0]
        sn = it.get("snippet") or {}
        return {"id": str(it.get("id") or ""), "title": str(sn.get("title") or "")[:100], "handle": str(sn.get("customUrl") or "")[:100]}

    def playlists(self, pages=4):
        out, token = [], ""
        for _ in range(pages):
            url = endpoints()["api"] + "/playlists?part=snippet,status,contentDetails&mine=true&maxResults=50" + \
                (f"&pageToken={urllib.parse.quote(token)}" if token else "")
            _, _, d = self.request("playlists.list", "GET", url)
            for it in d.get("items") or []:
                out.append({"id": str(it.get("id") or ""), "title": str((it.get("snippet") or {}).get("title") or "")[:150],
                            "privacy": (it.get("status") or {}).get("privacyStatus"),
                            "count": (it.get("contentDetails") or {}).get("itemCount")})
            token = d.get("nextPageToken") or ""
            if not token:
                break
        return out

    def video_status(self, vid):
        _, _, d = self.request("videos.list", "GET", endpoints()["api"] + "/videos?part=status,processingDetails&id=" + urllib.parse.quote(vid))
        items = d.get("items") or []
        return items[0] if items else None

    # --- 쓰기 ---
    def create_playlist(self, title, privacy="public"):
        body = {"snippet": {"title": title, "defaultLanguage": "ko"}, "status": {"privacyStatus": privacy}}
        _, _, d = self.request("playlists.insert", "POST", endpoints()["api"] + "/playlists?part=snippet,status",
                               json.dumps(body, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json; charset=UTF-8"})
        return {"id": str(d.get("id") or ""), "title": str((d.get("snippet") or {}).get("title") or title)}

    def add_to_playlist(self, pid, vid):
        body = {"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}}
        _, _, d = self.request("playlistItems.insert", "POST", endpoints()["api"] + "/playlistItems?part=snippet",
                               json.dumps(body).encode("utf-8"), {"Content-Type": "application/json; charset=UTF-8"})
        return d.get("id")

    def set_thumbnail(self, vid, path):
        raw = Path(path).read_bytes()
        ctype = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        url = endpoints()["upload"] + "/thumbnails/set?videoId=" + urllib.parse.quote(vid) + "&uploadType=media"
        _, _, d = self.request("thumbnails.set", "POST", url, raw, {"Content-Type": ctype}, timeout=120)
        return d

    def insert_caption(self, vid, srt_bytes, lang="ko", name="한국어"):
        boundary = "futsal" + secrets.token_hex(16)
        meta = json.dumps({"snippet": {"videoId": vid, "language": lang, "name": name, "isDraft": False}}, ensure_ascii=False).encode("utf-8")
        body = (b"--" + boundary.encode() + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + meta +
                b"\r\n--" + boundary.encode() + b"\r\nContent-Type: application/octet-stream\r\n\r\n" + bytes(srt_bytes) +
                b"\r\n--" + boundary.encode() + b"--\r\n")
        url = endpoints()["upload"] + "/captions?uploadType=multipart&part=snippet"
        _, _, d = self.request("captions.insert", "POST", url, body, {"Content-Type": f"multipart/related; boundary={boundary}"}, timeout=120)
        return d

    # --- 재개 가능한 업로드 ---
    def start_upload(self, meta, size, mime, notify=True):
        """세션 시작 → 세션 주소(비밀처럼 다룸). 돌려받은 주소가 업로드 주소와 같은 곳인지 확인 (다른 곳으로는 보내지 않음)."""
        up = endpoints()["upload"]
        url = up + "/videos?uploadType=resumable&part=snippet,status&notifySubscribers=" + ("true" if notify else "false")
        _, hdrs, _ = self.request("videos.insert", "POST", url, json.dumps(meta, ensure_ascii=False).encode("utf-8"),
                                  {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(int(size)),
                                   "X-Upload-Content-Type": mime}, ok=(200, 201))
        loc = (hdrs.get("Location") or "") if hdrs else ""
        if not valid_session_uri(loc):
            try:  # 주인 PC 확인용: 주소 전체(세션 번호)는 남기지 않고 호스트만
                host = urllib.parse.urlsplit(loc).hostname
            except ValueError:
                host = None
            print(f"유튜브 업로드 세션 주소가 예상과 달라요 · host={_REASON.sub('', str(host or '-'))[:80]}", flush=True)
            raise ApiError("server", "유튜브가 이상한 업로드 주소를 돌려줬어요 · 잠시 뒤 다시 올려 주세요")
        return loc

    @staticmethod
    def _check_uri(uri):
        """보낼 때마다: 저장해 둔 세션 주소가 Google 업로드 주소가 아니면 토큰을 붙이지 않고 '새로 시작'(session_expired)."""
        if not valid_session_uri(uri):
            raise ApiError("session_expired", reason="badSessionUri")

    def upload_status(self, uri, size, _again=False):
        """올린 데까지 묻기 → ("incomplete", 받은 바이트) | ("done", 영상 정보). 404·410 = 세션 만료."""
        self._check_uri(uri)
        h = {"Authorization": "Bearer " + self.access_token(force=_again), "Content-Range": f"bytes */{int(size)}", "Content-Length": "0"}
        try:
            st, hdrs, raw = _send("PUT", uri, b"", h, TIMEOUT)
        except _Net:
            raise ApiError("network") from None
        if st == 308:
            return "incomplete", _range_end(hdrs)
        if st in (200, 201):
            return "done", _json(raw)
        if st in (404, 410):
            raise ApiError("session_expired", status=st)
        if st == 401 and not _again:
            return self.upload_status(uri, size, True)
        reason, _ = parse_google_error(raw)
        raise classify("upload", st, reason)

    def upload_file(self, uri, path, size, offset=0, *, chunk=None, cancel=None, progress=None, on_offset=None, mime=None, on_wait=None):
        """offset 부터 끝까지 조각으로 보냄 → 영상 정보(dict). 끊기면 BACKOFF 만큼 기다리고 상태를 물어 이어서.
        인터넷이 끊기면 NET_PATIENCE(30분) 동안은 1분마다 다시 해 봄 · 5xx 는 BACKOFF 까지.
        308 인데 받은 데가 늘지 않으면(STUCK_MAX 번) 쉬었다가 상태를 물음 → 계속 그러면 server 오류 (세션은 남아 이어 올리기).
        on_wait(n번째, Google 이 받은 바이트, "network"|"server"): 기다리기 전에 (화면에 '다시 연결하는 중')."""
        self._check_uri(uri)
        chunk = int(chunk or CHUNK)
        if chunk % UNIT:
            raise ValueError("조각 크기는 256 KiB 배수여야 해요")
        mime = mime or _mime(path)
        cancel = cancel or self.cancel
        fails, refreshed, stuck, down_since = 0, False, 0, None
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            end = min(size, offset + chunk)
            length = end - offset
            st, hdrs, raw = None, None, b""
            box, done = [], threading.Event()
            if cancel is not None:  # 보낸 뒤 응답을 기다리는 동안에도 [멈추기]가 바로 듣게: 연결을 끊음
                threading.Thread(target=self._watch, args=(cancel, done, box), daemon=True).start()
            try:
                with open(path, "rb") as f:
                    base = offset
                    rd = _ChunkReader(f, offset, length, cancel, (lambda n: progress(base + n)) if progress else None)
                    h = {"Authorization": "Bearer " + self.access_token(), "Content-Length": str(length),
                         "Content-Range": f"bytes {offset}-{end - 1}/{size}" if length else f"bytes */{size}", "Content-Type": mime}
                    st, hdrs, raw = _send("PUT", uri, rd if length else b"", h, TIMEOUT, box=box)
            except _Net:
                st = None
            except FileNotFoundError:
                raise ApiError("file") from None
            finally:
                done.set()
            if cancel is not None and cancel.is_set() and st not in (200, 201):
                raise Cancelled()
            if st in (200, 201):
                return _json(raw)
            reason = None
            if st == 308:
                new = _range_end(hdrs)
                down_since = None
                if new > offset:
                    fails, stuck = 0, 0
                else:
                    stuck += 1
                if new != offset and on_offset:
                    on_offset(new)
                offset = new
                if stuck < STUCK_MAX:
                    continue
            elif st in (404, 410):
                raise ApiError("session_expired", status=st)
            elif st == 401 and not refreshed:
                refreshed = True
                self.access_token(force=True)
                kind, val = self.upload_status(uri, size)
                if kind == "done":
                    return val
                offset = val
                continue
            elif st is not None:
                reason, _ = parse_google_error(raw)
                retry = st >= 500 or st == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded", "backendError")
                if not retry:
                    raise classify("upload", st, reason)
            why = "network" if st is None else "server"
            if st is None:
                down_since = down_since or time.monotonic()
                if fails >= len(BACKOFF) and time.monotonic() - down_since >= NET_PATIENCE:
                    raise ApiError("network", MSG["network_long"] if NET_PATIENCE >= 600 else None)
            elif fails >= len(BACKOFF):
                raise ApiError("server", status=st if st != 308 else None, reason=reason or ("stuck" if st == 308 else None))
            if on_wait:
                on_wait(fails + 1, offset, why)
            self._wait_or_cancel(cancel, BACKOFF[min(fails, len(BACKOFF) - 1)] + random.random() * JITTER)
            fails += 1
            try:
                kind, val = self.upload_status(uri, size)
            except ApiError as e:
                if e.kind in ("network", "server"):
                    continue
                raise
            if kind == "done":
                return val
            if val > offset:
                stuck = 0
            if val != offset and on_offset:
                on_offset(val)
            offset = val

    @staticmethod
    def _watch(cancel, done, box):
        while not done.wait(0.1):
            if cancel.is_set():
                _abort(box)
                return

    def _wait_or_cancel(self, cancel, secs):
        if cancel is not None:
            if cancel.wait(secs):
                raise Cancelled()
        else:
            time.sleep(secs)
