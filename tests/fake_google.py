"""가짜 Google — OAuth 2.0 설치형 앱(루프백 + PKCE) + YouTube Data API v3 (시험 전용 · 표준 라이브러리만 · 배포 안 됨).

공식 문서(2026-10-07 확인)의 동작을 그대로 흉내 냄:
- OAuth: developers.google.com/identity/protocols/oauth2/native-app — /o/oauth2/v2/auth(루프백 redirect_uri · S256) →
  계정 고르기 → '확인하지 않은 앱' 경고(고급 → 이동) → 동의(권한 칸 체크 · 세분화 동의) → 302 redirect_uri?code&state&scope
  (취소면 error=access_denied · 테스트 상태에서 테스트 사용자가 아니면 '액세스 차단됨' 화면에서 멈춤)
  /token: authorization_code(client_secret 필수 · redirect_uri 같아야 · PKCE S256 확인 · 코드 한 번만) · refresh_token
  (테스트 상태면 동의한 지 7일 뒤 invalid_grant · 취소된 토큰 invalid_grant) · /revoke
  · refresh_token_expires_in 은 '시간 제한 액세스'(time_based 모드)일 때만 줌 — 테스트 상태 7일은 응답에 없음 (native-app 문서)
- YouTube: Bearer 확인(401 authError) · 범위 확인(403 insufficientPermissions) · Google 형식 오류
  {"error": {"code", "message", "errors": [{"reason", "domain", "message"}], "status"}} · 할당량(업로드 100회 · 그 밖 10,000 단위)
  - 재개 가능한 업로드: POST …/videos?uploadType=resumable → 200 + Location · PUT 마다 Authorization: Bearer (문서의 3·4단계 예시
    그대로 · 401 authError · lenient_put_auth 모드면 안 봄) · PUT Content-Range bytes a-b/total
    (마지막이 아닌 조각은 256 KiB 배수) → 308 Resume Incomplete + Range: bytes=0-N (0바이트면 Range 없음) → 끝 201(또는 200)
    · 빈 PUT bytes */total = 상태 묻기 · 끊긴 요청은 256 KiB 단위로 내림해 받은 데까지 남김 · 만료·모르는 세션은 404
  - thumbnails.set(JPEG/PNG 확인) · captions.insert(multipart/related) · playlists list/insert · playlistItems.insert ·
    videos.list(status, processingDetails · 올린 직후 processing_calls 번은 uploaded/processing · 거절은 처리 뒤) · channels.list(mine)
  - 할당량은 인증 뒤·내용 확인 전에 셈 (실패한 요청도 셈) · 단 videos.insert 의 '업로드 수'는 세션을 만들 때만
- 조종: POST /_fake/mode (JSON 합침) · /_fake/reset · /_fake/clock {"advance": 초} · GET /_fake/state · /_fake/secrets
  모드: testing · verified · test_users · client_type · auto{account, grant, deny} · unverified_lock · thumb_forbidden ·
  thumb_rate · quota_exhausted_at(작업 이름) · upload_limit · api_disabled · fail_put_once · drop_after(바이트) ·
  expire_sessions(지금 세션 모두 만료) · rate(바이트/초) · final200 · reject_length · playlist_full ·
  time_based · lenient_put_auth · processing_calls(기본 1) · fail_ops{작업: [HTTP 코드…]}(503 backendError · 404 videoNotFound) ·
  stuck_put(그 수만큼 308 을 받은 데 그대로 돌려줌) · redirect_api(API GET 을 다른 곳으로 302)

실행: python3 tests/fake_google.py --port 8941 [--rate 1000000]
"""
import argparse
import base64
import calendar
import hashlib
import html
import io
import json
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

SCOPE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPE_YT = "https://www.googleapis.com/auth/youtube"
SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_PARTNER = "https://www.googleapis.com/auth/youtubepartner"
SCOPE_TEXT = {SCOPE_SSL: "YouTube 동영상, 평가, 댓글, 자막 보기, 수정 및 영구 삭제",
              SCOPE_YT: "YouTube 계정 관리", SCOPE_UPLOAD: "YouTube 동영상 관리"}
UNIT = 256 * 1024
CATEGORIES = {"1", "2", "10", "15", "17", "19", "20", "22", "23", "24", "25", "26", "27", "28", "29"}
COST = {"videos.insert": 1, "thumbnails.set": 50, "captions.insert": 400, "playlistItems.insert": 50, "playlists.insert": 50,
        "playlists.list": 1, "channels.list": 1, "videos.list": 1}
LIMITS = {"uploads": 100, "units": 10000}
# 범위: 끝점마다 받아 주는 범위 (문서의 Authorization 표)
NEED = {"videos.insert": {SCOPE_UPLOAD, SCOPE_YT, SCOPE_SSL, SCOPE_PARTNER},
        "captions.insert": {SCOPE_SSL, SCOPE_PARTNER},
        "thumbnails.set": {SCOPE_UPLOAD, SCOPE_YT, SCOPE_SSL, SCOPE_PARTNER},
        "playlistItems.insert": {SCOPE_YT, SCOPE_SSL, SCOPE_PARTNER},
        "playlists.insert": {SCOPE_YT, SCOPE_SSL, SCOPE_PARTNER},
        "playlists.list": {SCOPE_YT, SCOPE_SSL, SCOPE_PARTNER, "https://www.googleapis.com/auth/youtube.readonly"},
        "channels.list": {SCOPE_YT, SCOPE_SSL, SCOPE_PARTNER, "https://www.googleapis.com/auth/youtube.readonly", SCOPE_UPLOAD},
        "videos.list": {SCOPE_YT, SCOPE_SSL, SCOPE_PARTNER, "https://www.googleapis.com/auth/youtube.readonly"}}

CLIENT_ID = "123456789012-fakeclient0abc9def.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-FakeSecretForTests_0001xyz"
PROJECT_ID = "futsal-studio-test"
OWN_CHANNEL = "UCRYziLOw2T6BF6fXtpUby-g"
OTHER_CHANNEL = "UCx0therChannelForTests0"
ACCOUNTS = [
    {"id": "acc-me", "email": "owner.futsal@gmail.com", "name": "최경진", "label": "최경진 (owner.futsal@gmail.com)", "channel": None},
    {"id": "acc-brand", "email": "owner.futsal@gmail.com", "name": "풋살사관학교", "label": "풋살사관학교 (브랜드 계정)",
     "channel": {"id": OWN_CHANNEL, "title": "풋살사관학교", "customUrl": "@futsalacademy"}},
    {"id": "acc-other", "email": "other.channel@gmail.com", "name": "다른 채널", "label": "다른 채널 (other.channel@gmail.com)",
     "channel": {"id": OTHER_CHANNEL, "title": "다른 채널", "customUrl": "@otherchannel"}},
]
DEFAULT_MODES = {"testing": True, "verified": False, "test_users": ["owner.futsal@gmail.com", "other.channel@gmail.com"],
                 "client_type": "installed", "auto": None, "unverified_lock": False, "thumb_forbidden": False, "thumb_rate": False,
                 "quota_exhausted_at": None, "upload_limit": False, "api_disabled": False, "fail_put_once": False,
                 "drop_after": None, "rate": None, "final200": False, "reject_length": False, "playlist_full": False,
                 "access_ttl": 3599, "time_based": False, "lenient_put_auth": False, "processing_calls": 1, "fail_ops": {},
                 "stuck_put": 0, "redirect_api": None}
SESSION_TTL = 7 * 86400
TESTING_TTL = 7 * 86400


def gerror(code, reason, message, domain="youtube.api", status=None, info=None):
    """Google API 오류 몸통 (문서의 JSON 꼴)."""
    status = status or {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
                        409: "ALREADY_EXISTS", 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 503: "UNAVAILABLE"}.get(code, "UNKNOWN")
    e = {"code": code, "message": message, "errors": [{"message": message, "domain": domain, "reason": reason}], "status": status}
    if info:
        e["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": info, "domain": "googleapis.com"}]
    return code, {"error": e}


class ApiFail(Exception):
    def __init__(self, code, reason, message, domain="youtube.api", info=None):
        super().__init__(message)
        self.resp = gerror(code, reason, message, domain, info=info)


def _rid(n=11, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"):
    return "".join(secrets.choice(alphabet) for _ in range(n))


class FakeGoogle:
    """가짜 Google 서버 하나 (상태는 메모리 · 잠금 하나)."""

    def __init__(self, rate=None):
        self.lock = threading.RLock()
        self.srv = None
        self.base = None
        self._init_rate = rate
        self.reset()

    # ---------- 상태 ----------
    def reset(self):
        with self.lock:
            self.offset = 0.0
            self.modes = json.loads(json.dumps(DEFAULT_MODES))
            if self._init_rate:
                self.modes["rate"] = self._init_rate
            self.clients = {CLIENT_ID: {"secret": CLIENT_SECRET}}
            self.flows, self.codes, self.access, self.refresh = {}, {}, {}, {}
            self.videos, self.sessions, self.playlists = {}, {}, {}
            self.quota = {"uploads": 0, "units": 0}
            self.insert_calls = 0
            self.last_auth = None
            self.revoked = []
            self.log = []

    def now(self):
        return time.time() + self.offset

    def start(self, port=0):
        self.srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.srv.daemon_threads = True
        self.srv.fake = self
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        return self.base

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    def set_mode(self, d):
        with self.lock:
            for k, v in (d or {}).items():
                if k == "expire_sessions":
                    if v:
                        for s in self.sessions.values():
                            s["expired"] = True
                    continue
                self.modes[k] = v

    def snapshot(self):
        with self.lock:
            vids = []
            for vid, v in self.videos.items():
                vids.append({"id": vid, "channelId": v["channel"], "title": v["snippet"].get("title"),
                             "description": v["snippet"].get("description"), "tags": v["snippet"].get("tags") or [],
                             "categoryId": v["snippet"].get("categoryId"), "defaultLanguage": v["snippet"].get("defaultLanguage"),
                             "defaultAudioLanguage": v["snippet"].get("defaultAudioLanguage"),
                             "privacy": v["status"]["privacyStatus"], "requestedPrivacy": v["requested"].get("privacyStatus"),
                             "publishAt": v["status"].get("publishAt"), "selfDeclaredMadeForKids": v["requested"].get("selfDeclaredMadeForKids", "missing"),
                             "notifySubscribers": v["notify"], "sha256": v["sha256"], "size": v["size"], "locked": v["locked"],
                             "thumbnail": v["thumbnail"], "captions": [dict(c, content=None) for c in v["captions"]],
                             "playlists": [pid for pid, p in self.playlists.items() if vid in p["items"]]})
            return {"videos": vids, "insert_calls": self.insert_calls, "quota": dict(self.quota), "modes": dict(self.modes),
                    "sessions": [{"id": k, "received": len(s["data"]), "total": s["total"], "expired": s["expired"],
                                  "done": s.get("video")} for k, s in self.sessions.items()],
                    "playlists": [{"id": k, "title": p["title"], "privacy": p["privacy"], "items": list(p["items"]), "channel": p["channel"]}
                                  for k, p in self.playlists.items()],
                    "last_auth": self.last_auth, "revoked": len(self.revoked), "tokens": {"access": len(self.access), "refresh": len(self.refresh)},
                    "log": self.log[-200:]}

    def secret_values(self):
        """시크릿 위생 시험용: 지금까지 내준 모든 비밀 값 (앱 기록·작업 폴더에 남으면 안 됨)."""
        with self.lock:
            return {"client_secret": CLIENT_SECRET, "access": list(self.access), "refresh": list(self.refresh),
                    "codes": list(self.codes), "upload_ids": list(self.sessions), "revoked": list(self.revoked)}

    # ---------- OAuth ----------
    def _page_error(self, title, msg, code=400):
        return code, "text/html", _html(title, f"<h1>{html.escape(title)}</h1><p>{html.escape(msg)}</p>")

    def auth_start(self, q):
        """GET /o/oauth2/v2/auth — 요청 확인 후 계정 고르기로 (auto 모드면 바로 결과로)."""
        g = {k: v[0] for k, v in q.items()}
        with self.lock:
            self.last_auth = {k: g.get(k) for k in ("client_id", "redirect_uri", "response_type", "scope", "code_challenge_method",
                                                     "access_type", "prompt", "state")}
            self.last_auth["code_challenge_len"] = len(g.get("code_challenge") or "")
        cid = g.get("client_id")
        if cid not in self.clients:
            return self._page_error("액세스 차단됨: 승인 오류", "The OAuth client was not found. 오류 401: invalid_client", 401)
        red = g.get("redirect_uri") or ""
        u = urlsplit(red)
        loop = u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1") and u.port and u.path in ("", "/")
        if self.modes.get("client_type") != "installed" or not loop:
            return self._page_error("액세스 차단됨: 이 앱의 요청이 잘못되었습니다", "오류 400: redirect_uri_mismatch")
        if g.get("response_type") != "code" or g.get("code_challenge_method") != "S256" or not 43 <= len(g.get("code_challenge") or "") <= 128:
            return self._page_error("액세스 차단됨: 이 앱의 요청이 잘못되었습니다", "오류 400: invalid_request")
        scopes = (g.get("scope") or "").split()
        if not scopes or any(not s.startswith("https://www.googleapis.com/auth/") for s in scopes):
            return self._page_error("액세스 차단됨: 이 앱의 요청이 잘못되었습니다", "오류 400: invalid_scope")
        fid = _rid(16)
        with self.lock:
            self.flows[fid] = {"client_id": cid, "redirect": red, "state": g.get("state"), "challenge": g["code_challenge"],
                               "scopes": scopes, "account": None, "at": self.now()}
        auto = self.modes.get("auto")
        if isinstance(auto, dict):
            if auto.get("deny"):
                return self._finish(fid, False, [])
            acc = self._account(auto.get("account") or "acc-brand")
            self.flows[fid]["account"] = acc["id"]
            if self.modes.get("testing") and acc["email"] not in (self.modes.get("test_users") or []):
                return self._blocked()
            return self._finish(fid, True, scopes if auto.get("grant", True) else [])
        return 302, f"/_auth/choose?f={fid}", None

    def _account(self, aid):
        return next((a for a in ACCOUNTS if a["id"] == aid), ACCOUNTS[1])

    def _blocked(self):
        return 403, "text/html", _html("액세스 차단됨", "<h1>액세스 차단됨: 풋살 스튜디오은(는) Google 인증 절차를 완료하지 않았습니다</h1>"
                                       "<p>풋살 스튜디오은(는) 현재 테스트 중이며 개발자가 승인한 테스터만 액세스할 수 있습니다.</p>"
                                       "<p id='err'>오류 403: access_denied</p>")

    def auth_page(self, path, q):
        g = {k: v[0] for k, v in q.items()}
        fid = g.get("f") or ""
        with self.lock:
            flow = self.flows.get(fid)
        if not flow:
            return self._page_error("오류", "요청이 만료됐어요")
        if path == "/_auth/choose":
            items = "".join(f"<li><a class='acct' data-acct='{a['id']}' href='/_auth/pick?f={fid}&a={a['id']}'>{html.escape(a['label'])}</a></li>"
                            for a in ACCOUNTS)
            return 200, "text/html", _html("계정 선택", f"<h1>계정 선택</h1><p>풋살 스튜디오(으)로 이동</p><ul>{items}</ul>")
        if path == "/_auth/pick":
            acc = self._account(g.get("a"))
            with self.lock:
                flow["account"] = acc["id"]
            if self.modes.get("testing") and acc["email"] not in (self.modes.get("test_users") or []):
                return self._blocked()
            if not self.modes.get("verified"):
                return 302, f"/_auth/warn?f={fid}", None
            return 302, f"/_auth/consent?f={fid}", None
        if path == "/_auth/warn":
            return 200, "text/html", _html("Google에서 확인하지 않은 앱", f"""
<h1>Google에서 확인하지 않은 앱</h1><p>이 앱은 Google에서 확인하지 않았습니다. 개발자를 알고 신뢰하는 경우에만 계속하세요.</p>
<button id="adv" onclick="document.getElementById('more').hidden=false">고급</button>
<a id="back" href="/_auth/decide?f={fid}&ok=0">안전한 페이지로 돌아가기</a>
<div id="more" hidden><a id="goUnsafe" href="/_auth/consent?f={fid}">풋살 스튜디오(으)로 이동(안전하지 않음)</a></div>""")
        if path == "/_auth/consent":
            boxes = "".join(f"<label><input type='checkbox' name='s' value='{html.escape(s)}'> {html.escape(SCOPE_TEXT.get(s, s))}</label><br>"
                            for s in flow["scopes"])
            return 200, "text/html", _html("풋살 스튜디오에서 추가 액세스를 요청합니다", f"""
<h1>풋살 스튜디오에서 Google 계정에 대한 추가 액세스를 요청합니다</h1>
<form method="get" action="/_auth/decide"><input type="hidden" name="f" value="{fid}"><input type="hidden" name="ok" value="1">
<button type="button" id="all" onclick="document.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=true)">모두 선택</button><br>
{boxes}<button type="submit" id="allow">계속</button></form>
<a id="cancel" href="/_auth/decide?f={fid}&ok=0">취소</a>""")
        if path == "/_auth/decide":
            granted = [s for s in q.get("s", []) if s in flow["scopes"]]
            return self._finish(fid, g.get("ok") == "1", granted)
        return 404, "text/html", _html("없음", "없음")

    def _finish(self, fid, ok, granted):
        with self.lock:
            flow = self.flows.pop(fid, None)
            if not flow:
                return self._page_error("오류", "요청이 만료됐어요")
            q = {"state": flow["state"]} if flow["state"] is not None else {}
            if not ok:
                q["error"] = "access_denied"
            else:
                code = "4/0AFAKE" + _rid(40)
                self.codes[code] = {"client_id": flow["client_id"], "redirect": flow["redirect"], "challenge": flow["challenge"],
                                    "scopes": granted, "account": flow["account"], "at": self.now()}
                q.update(code=code, scope=" ".join(granted))
        sep = "&" if "?" in flow["redirect"] else "?"
        return 302, flow["redirect"] + sep + urlencode(q, quote_via=quote), None

    def token(self, form):
        f = {k: v[0] for k, v in form.items()}
        cid = f.get("client_id")
        if cid not in self.clients:
            return 401, {"error": "invalid_client", "error_description": "The OAuth client was not found."}
        if f.get("client_secret") != self.clients[cid]["secret"]:
            return 401, {"error": "invalid_client", "error_description": "Unauthorized"}
        gt = f.get("grant_type")
        with self.lock:
            if gt == "authorization_code":
                c = self.codes.pop(f.get("code") or "", None)
                if not c or c["client_id"] != cid or self.now() - c["at"] > 600:
                    return 400, {"error": "invalid_grant", "error_description": "Malformed auth code."}
                if f.get("redirect_uri") != c["redirect"]:
                    return 400, {"error": "redirect_uri_mismatch", "error_description": "Bad Request"}
                v = f.get("code_verifier") or ""
                ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode("ascii", "replace")).digest()).rstrip(b"=").decode()
                if not 43 <= len(v) <= 128 or ch != c["challenge"]:
                    return 400, {"error": "invalid_grant", "error_description": "Invalid code verifier."}
                rt = "1//FAKE" + _rid(48)
                self.refresh[rt] = {"client_id": cid, "account": c["account"], "scopes": c["scopes"], "at": self.now(), "revoked": False}
                at = self._issue(rt)
                out = {"access_token": at, "expires_in": self.modes.get("access_ttl") or 3599, "refresh_token": rt,
                       "scope": " ".join(c["scopes"]), "token_type": "Bearer"}
                if self.modes.get("time_based"):  # 사용자가 '시간 제한 액세스'를 고른 때만 (테스트 상태 7일은 알려 주지 않음)
                    out["refresh_token_expires_in"] = TESTING_TTL - 1
                self.log.append("token:code")
                return 200, out
            if gt == "refresh_token":
                rt = self.refresh.get(f.get("refresh_token") or "")
                if not rt or rt["revoked"] or rt["client_id"] != cid or \
                        (self.modes.get("testing") and self.now() - rt["at"] > TESTING_TTL):
                    return 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
                at = self._issue(f["refresh_token"])
                self.log.append("token:refresh")
                return 200, {"access_token": at, "expires_in": self.modes.get("access_ttl") or 3599, "scope": " ".join(rt["scopes"]),
                             "token_type": "Bearer"}
        return 400, {"error": "unsupported_grant_type", "error_description": "Invalid grant_type"}

    def _issue(self, rt):
        at = "ya29.FAKE" + _rid(60)
        self.access[at] = {"refresh": rt, "exp": self.now() + (self.modes.get("access_ttl") or 3599)}
        return at

    def revoke(self, form):
        tok = (form.get("token") or [""])[0]
        with self.lock:
            if tok in self.refresh:
                if self.refresh[tok]["revoked"]:  # 이미 끊긴 토큰
                    return 400, {"error": "invalid_token", "error_description": "Token expired or revoked"}
                self.refresh[tok]["revoked"] = True
                for a in [a for a, v in self.access.items() if v["refresh"] == tok]:
                    del self.access[a]
                self.revoked.append(tok)
                return 200, {}
            if tok in self.access:
                rt = self.access[tok]["refresh"]
                if rt in self.refresh:
                    self.refresh[rt]["revoked"] = True
                self.access.pop(tok, None)
                self.revoked.append(tok)
                return 200, {}
        return 400, {"error": "invalid_token", "error_description": "Token expired or revoked"}

    # ---------- YouTube ----------
    def auth(self, headers, op):
        """Bearer 확인 → (계정, 채널)."""
        h = headers.get("Authorization") or ""
        if not h.startswith("Bearer "):
            raise ApiFail(401, "required", "Login Required.", "global")
        with self.lock:
            a = self.access.get(h[7:])
            if not a or a["exp"] < self.now():
                raise ApiFail(401, "authError", "Invalid Credentials", "global")
            rt = self.refresh.get(a["refresh"])
            if not rt or rt["revoked"]:
                raise ApiFail(401, "authError", "Invalid Credentials", "global")
        if self.modes.get("api_disabled"):
            raise ApiFail(403, "accessNotConfigured",
                          "YouTube Data API v3 has not been used in project 123456789012 before or it is disabled. Enable it by visiting "
                          "https://console.developers.google.com/apis/api/youtube.googleapis.com/overview?project=123456789012 then retry.",
                          "usageLimits", info="SERVICE_DISABLED")
        if not set(rt["scopes"]) & NEED[op]:
            raise ApiFail(403, "insufficientPermissions", "Request had insufficient authentication scopes.", "global",
                          info="ACCESS_TOKEN_SCOPE_INSUFFICIENT")
        acc = self._account(rt["account"])
        return acc, acc["channel"]

    def fail_op(self, op):
        """fail_ops 모드: 그 작업의 다음 호출을 정해 둔 코드로 실패 (올린 직후 처리 중 흉내)."""
        with self.lock:
            codes = (self.modes.get("fail_ops") or {}).get(op) or []
            if not codes:
                return
            code = codes.pop(0)
        if code == 404:
            raise ApiFail(404, "videoNotFound", "The video that you are trying to update cannot be found.")
        raise ApiFail(code, "backendError", "Backend Error", "global")

    def charge(self, op):
        with self.lock:
            if self.modes.get("quota_exhausted_at") == op:
                raise ApiFail(403, "quotaExceeded", "The request cannot be completed because you have exceeded your "
                              "<a href=\"/youtube/v3/getting-started#quota\">quota</a>.", "youtube.quota")
            if op == "videos.insert":
                if self.quota["uploads"] >= LIMITS["uploads"]:
                    raise ApiFail(403, "quotaExceeded", "The request cannot be completed because you have exceeded your quota.", "youtube.quota")
                self.quota["uploads"] += 1
            else:
                if self.quota["units"] + COST[op] > LIMITS["units"]:
                    raise ApiFail(403, "quotaExceeded", "The request cannot be completed because you have exceeded your quota.", "youtube.quota")
                self.quota["units"] += COST[op]
            self.log.append(op)

    def channels(self, headers, q):
        acc, ch = self.auth(headers, "channels.list")
        self.charge("channels.list")
        items = [] if not ch else [{"kind": "youtube#channel", "id": ch["id"], "snippet": {"title": ch["title"], "customUrl": ch["customUrl"]}}]
        return 200, {"kind": "youtube#channelListResponse", "pageInfo": {"totalResults": len(items), "resultsPerPage": 5}, "items": items}

    def playlists_list(self, headers, q):
        acc, ch = self.auth(headers, "playlists.list")
        self.charge("playlists.list")
        if not ch:
            raise ApiFail(404, "channelNotFound", "Channel not found.")
        mine = [(k, p) for k, p in self.playlists.items() if p["channel"] == ch["id"]]
        n = max(1, min(50, int((q.get("maxResults") or ["5"])[0])))
        start = int((q.get("pageToken") or ["0"])[0] or 0)
        page = mine[start:start + n]
        out = {"kind": "youtube#playlistListResponse", "pageInfo": {"totalResults": len(mine), "resultsPerPage": n},
               "items": [{"kind": "youtube#playlist", "id": k, "snippet": {"title": p["title"]}, "status": {"privacyStatus": p["privacy"]},
                          "contentDetails": {"itemCount": len(p["items"])}} for k, p in page]}
        if start + n < len(mine):
            out["nextPageToken"] = str(start + n)
        return 200, out

    def playlists_insert(self, headers, q, body):
        acc, ch = self.auth(headers, "playlists.insert")
        self.charge("playlists.insert")
        sn = (body or {}).get("snippet") or {}
        title = str(sn.get("title") or "").strip()
        if not title:
            raise ApiFail(400, "playlistTitleRequired", "The request must specify a playlist title.")
        if len(title) > 150:
            raise ApiFail(400, "invalidPlaylistSnippet", "The request provides an invalid playlist snippet.")
        privacy = ((body or {}).get("status") or {}).get("privacyStatus") or "public"
        pid = "PL" + _rid(32)
        with self.lock:
            self.playlists[pid] = {"title": title, "privacy": privacy, "items": [], "channel": ch["id"] if ch else None}
        return 200, {"kind": "youtube#playlist", "id": pid, "snippet": {"title": title}, "status": {"privacyStatus": privacy}}

    def playlist_items(self, headers, q, body):
        acc, ch = self.auth(headers, "playlistItems.insert")
        self.charge("playlistItems.insert")
        self.fail_op("playlistItems.insert")
        sn = (body or {}).get("snippet") or {}
        pid, vid = sn.get("playlistId"), ((sn.get("resourceId") or {}).get("videoId"))
        with self.lock:
            p = self.playlists.get(pid)
            if not p or p["channel"] != (ch or {}).get("id"):
                raise ApiFail(404, "playlistNotFound", "The playlist identified with the request's playlistId parameter cannot be found.")
            if vid not in self.videos:
                raise ApiFail(404, "videoNotFound", "The video that you are trying to add to the playlist cannot be found.")
            if self.modes.get("playlist_full") or len(p["items"]) >= 5000:
                raise ApiFail(403, "playlistContainsMaximumNumberOfVideos", "The playlist already contains the maximum allowed number of items.")
            p["items"].append(vid)
        return 200, {"kind": "youtube#playlistItem", "id": _rid(40), "snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid},
                                                                                  "position": len(p["items"]) - 1}}

    def videos_list(self, headers, q):
        acc, ch = self.auth(headers, "videos.list")
        self.charge("videos.list")
        ids = ",".join(q.get("id") or []).split(",")
        items = []
        with self.lock:
            for vid in ids:
                v = self.videos.get(vid)
                if not v or v["channel"] != (ch or {}).get("id"):
                    continue
                v["checks"] = v.get("checks", 0) + 1
                if v["checks"] <= int(self.modes.get("processing_calls") or 0):  # 올린 직후: 아직 처리 중
                    items.append({"kind": "youtube#video", "id": vid, "status": dict(v["status"], uploadStatus="uploaded"),
                                  "processingDetails": {"processingStatus": "processing"}})
                    continue
                st = dict(v["status"], uploadStatus="processed")
                if self.modes.get("reject_length"):
                    st.update(uploadStatus="rejected", rejectionReason="length")
                items.append({"kind": "youtube#video", "id": vid, "status": st, "processingDetails": {"processingStatus": "succeeded"}})
        return 200, {"kind": "youtube#videoListResponse", "pageInfo": {"totalResults": len(items), "resultsPerPage": len(items)}, "items": items}

    def insert_start(self, headers, q, body, host):
        acc, ch = self.auth(headers, "videos.insert")
        if q.get("uploadType", [""])[0] != "resumable":
            raise ApiFail(400, "badRequest", "uploadType must be resumable here")
        parts = set(",".join(q.get("part") or []).split(","))
        if not {"snippet", "status"} <= parts:
            raise ApiFail(400, "unexpectedPart", "part must include snippet,status")
        try:
            total = int(headers.get("X-Upload-Content-Length") or "")
        except ValueError:
            raise ApiFail(400, "badRequest", "X-Upload-Content-Length is required")
        mime = headers.get("X-Upload-Content-Type") or ""
        if total <= 0 or not (mime.startswith("video/") or mime == "application/octet-stream"):
            raise ApiFail(400, "badRequest", "Invalid X-Upload headers")
        if not ch:
            raise ApiFail(401, "youtubeSignupRequired", "This request requires the user to have a YouTube channel.", "youtube.header")
        sn, st = (body or {}).get("snippet") or {}, (body or {}).get("status") or {}
        title, desc = sn.get("title"), sn.get("description") or ""
        if not isinstance(title, str) or not title.strip() or len(title) > 100 or re.search(r"[<>]", title):
            raise ApiFail(400, "invalidTitle", "The request metadata specifies an invalid or empty video title.")
        if len(desc.encode("utf-8")) > 5000 or re.search(r"[<>]", desc):
            raise ApiFail(400, "invalidDescription", "The request metadata specifies an invalid video description.")
        tags = sn.get("tags") or []
        if sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1) > 500 or any(re.search(r"[<>]", t) for t in tags):
            raise ApiFail(400, "invalidTags", "The request metadata specifies invalid video keywords.")
        if str(sn.get("categoryId")) not in CATEGORIES:
            raise ApiFail(400, "invalidCategoryId", "The snippet.categoryId property specifies an invalid category ID.")
        if st.get("privacyStatus") not in ("private", "unlisted", "public"):
            raise ApiFail(400, "invalidVideoMetadata", "The request metadata is invalid.")
        if st.get("publishAt"):
            try:
                pa = calendar.timegm(time.strptime(st["publishAt"][:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pa = 0
            if st.get("privacyStatus") != "private" or pa <= self.now():
                raise ApiFail(400, "invalidPublishAt", "The request metadata specifies an invalid scheduled publishing time.")
        if self.modes.get("upload_limit"):
            raise ApiFail(400, "uploadLimitExceeded", "The user has exceeded the number of videos they may upload.")
        self.charge("videos.insert")
        uid = "AFAKEuid" + _rid(40)
        with self.lock:
            self.insert_calls += 1
            notify = (q.get("notifySubscribers") or ["true"])[0] != "false"
            self.sessions[uid] = {"total": total, "data": bytearray(), "body": body, "notify": notify, "channel": ch["id"],
                                  "created": self.now(), "expired": False, "video": None, "mime": mime}
        loc = f"http://{host}/upload/youtube/v3/videos?uploadType=resumable&part=snippet%2Cstatus&upload_id={uid}"
        return uid, loc

    def finalize(self, uid):
        s = self.sessions[uid]
        body, ch = s["body"], s["channel"]
        sn, st = dict(body.get("snippet") or {}), dict(body.get("status") or {})
        vid = _rid(11)
        locked = bool(self.modes.get("unverified_lock"))
        status = {"uploadStatus": "uploaded", "privacyStatus": "private" if locked else st.get("privacyStatus"), "license": "youtube",
                  "embeddable": True, "publicStatsViewable": True, "madeForKids": bool(st.get("selfDeclaredMadeForKids")),
                  "selfDeclaredMadeForKids": bool(st.get("selfDeclaredMadeForKids"))}
        if st.get("publishAt") and not locked:
            status["publishAt"] = st["publishAt"]
        self.videos[vid] = {"channel": ch, "snippet": sn, "status": status, "requested": st, "notify": s["notify"],
                            "sha256": hashlib.sha256(bytes(s["data"])).hexdigest(), "size": len(s["data"]), "locked": locked,
                            "thumbnail": None, "captions": []}
        s["video"] = vid
        s["data"] = bytearray()  # 메모리 정리 (지문은 남김)
        s["received"] = s["total"]
        return vid

    def video_resource(self, vid):
        v = self.videos[vid]
        return {"kind": "youtube#video", "id": vid, "snippet": dict(v["snippet"], channelId=v["channel"]), "status": v["status"]}

    def thumbnail(self, headers, q, raw, ctype):
        acc, ch = self.auth(headers, "thumbnails.set")
        self.charge("thumbnails.set")
        self.fail_op("thumbnails.set")
        vid = (q.get("videoId") or [""])[0]
        with self.lock:
            v = self.videos.get(vid)
        if not v or v["channel"] != (ch or {}).get("id"):
            raise ApiFail(404, "videoNotFound", "The video that you are trying to update cannot be found.")
        if self.modes.get("thumb_forbidden"):
            raise ApiFail(403, "forbidden", "The authenticated user doesn't have permissions to upload and set custom video thumbnails.")
        if self.modes.get("thumb_rate"):
            raise ApiFail(429, "uploadRateLimitExceeded", "The user has uploaded too many thumbnails recently. Please try the request later.")
        if ctype.split(";")[0].strip() not in ("image/jpeg", "image/png", "application/octet-stream"):
            raise ApiFail(400, "mediaBodyRequired", "The request does not include the image content.")
        if not (raw[:2] == b"\xff\xd8" or raw[:8] == b"\x89PNG\r\n\x1a\n"):
            raise ApiFail(400, "invalidImage", "The provided image content is invalid.")
        if len(raw) > 50 * 1024 * 1024:
            raise ApiFail(413, "mediaBodyTooLarge", "The image is too large.")
        with self.lock:
            v["thumbnail"] = {"sha256": hashlib.sha256(raw).hexdigest(), "type": "png" if raw[:4] == b"\x89PNG" else "jpeg", "bytes": len(raw)}
        url = f"https://i.ytimg.com/vi/{vid}/default.jpg"
        return 200, {"kind": "youtube#thumbnailSetResponse", "items": [{"default": {"url": url, "width": 120, "height": 90}}]}

    def caption(self, headers, q, raw, ctype):
        acc, ch = self.auth(headers, "captions.insert")
        self.charge("captions.insert")
        self.fail_op("captions.insert")
        if (q.get("uploadType") or [""])[0] != "multipart":
            raise ApiFail(400, "badRequest", "uploadType must be multipart here")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not ctype.startswith("multipart/related") or not m:
            raise ApiFail(400, "badContent", "multipart/related body expected")
        parts = _multipart(raw, m.group(1).encode())
        if len(parts) < 2:
            raise ApiFail(400, "contentRequired", "The caption track's content is missing.")
        (_, b1), (h2, b2) = parts[0], parts[1]
        try:
            meta = json.loads(b1.decode("utf-8"))
        except ValueError:
            raise ApiFail(400, "invalidMetadata", "The request contains invalid metadata values.")
        sn = (meta or {}).get("snippet") or {}
        if not sn.get("videoId") or not sn.get("language") or "name" not in sn:
            raise ApiFail(400, "invalidMetadata", "The request contains invalid metadata values, which prevent the track from being created.")
        if len(str(sn.get("name"))) > 150:
            raise ApiFail(400, "nameTooLong", "The snippet.name specified in the request is too long.")
        with self.lock:
            v = self.videos.get(sn["videoId"])
        if not v or v["channel"] != (ch or {}).get("id"):
            raise ApiFail(404, "videoNotFound", "The video identified by the videoId parameter could not be found.")
        if not b2.strip():
            raise ApiFail(400, "contentRequired", "The caption track's content is missing.")
        if b"-->" not in b2:
            raise ApiFail(400, "invalidMetadata", "The caption file could not be read.")
        if any(c["language"] == sn["language"] and c["name"] == sn["name"] for c in v["captions"]):
            raise ApiFail(409, "captionExists", "The specified video already has a caption track with the given snippet.language and snippet.name values.")
        cid = "AUieDa" + _rid(30)
        with self.lock:
            v["captions"].append({"id": cid, "language": sn["language"], "name": sn["name"], "isDraft": bool(sn.get("isDraft")),
                                  "bytes": len(b2), "sha256": hashlib.sha256(b2).hexdigest(), "mime": h2.get("content-type"),
                                  "content": b2.decode("utf-8", "replace")})
        return 200, {"kind": "youtube#caption", "id": cid, "snippet": {"videoId": sn["videoId"], "language": sn["language"], "name": sn["name"],
                                                                         "trackKind": "standard", "isDraft": bool(sn.get("isDraft")), "status": "serving"}}


def _multipart(raw, boundary):
    """multipart/related 몸통 → [(헤더 dict, 내용 bytes)]."""
    out = []
    for chunk in raw.split(b"--" + boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk[2:] if chunk.startswith(b"\r\n") else chunk
        head, _, body = chunk.partition(b"\r\n\r\n")
        hd = {}
        for ln in head.decode("utf-8", "replace").split("\r\n"):
            k, _, v = ln.partition(":")
            if k.strip():
                hd[k.strip().lower()] = v.strip()
        out.append((hd, body[:-2] if body.endswith(b"\r\n") else body))
    return out


def _html(title, body):
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            "<style>body{font:15px sans-serif;margin:40px;max-width:640px}a,button{display:inline-block;margin:6px 0}</style>"
            f"</head><body>{body}</body></html>")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def fake(self):
        return self.server.fake

    def _reply(self, code, body=None, ctype="application/json; charset=UTF-8", headers=None):
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body or b""
        self.send_response(code)
        if data or code not in (204, 308):
            self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def _send_api(self, resp):
        code, body = resp
        self._reply(code, body)

    def _read_all(self, limit=200 * 1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiFail(413, "mediaBodyTooLarge", "too large")
        buf = bytearray()
        while len(buf) < n:
            b = self.rfile.read(min(65536, n - len(buf)))
            if not b:
                break
            buf += b
        return bytes(buf)

    def _page(self, r):
        code, a, b = r
        if code == 302:
            self.send_response(302)
            self.send_header("Location", a)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._reply(code, b, "text/html; charset=utf-8")

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        f = self.fake
        try:
            if u.path == "/o/oauth2/v2/auth":
                return self._page(f.auth_start(q))
            if u.path.startswith("/_auth/"):
                return self._page(f.auth_page(u.path, q))
            if u.path == "/_fake/state":
                return self._reply(200, f.snapshot())
            if u.path == "/_fake/secrets":
                return self._reply(200, f.secret_values())
            if f.modes.get("redirect_api") and u.path.startswith("/youtube/v3/"):  # 이상한 프록시 흉내: 다른 곳으로 보냄
                self.send_response(302)
                self.send_header("Location", f.modes["redirect_api"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if u.path == "/youtube/v3/channels":
                return self._send_api(f.channels(self.headers, q))
            if u.path == "/youtube/v3/playlists":
                return self._send_api(f.playlists_list(self.headers, q))
            if u.path == "/youtube/v3/videos":
                return self._send_api(f.videos_list(self.headers, q))
        except ApiFail as e:
            return self._send_api(e.resp)
        self._reply(404, {"error": {"code": 404, "message": "Not Found", "status": "NOT_FOUND"}})

    def do_POST(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        f = self.fake
        try:
            raw = self._read_all()
            ctype = self.headers.get("Content-Type") or ""
            if u.path == "/token":
                code, body = f.token(parse_qs(raw.decode("utf-8", "replace")))
                return self._reply(code, body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
            if u.path == "/revoke":
                form = parse_qs(raw.decode("utf-8", "replace"))
                form.update(q)
                return self._reply(*f.revoke(form))
            if u.path == "/_fake/mode":
                f.set_mode(json.loads(raw or b"{}"))
                return self._reply(200, {"ok": True, "modes": f.modes})
            if u.path == "/_fake/reset":
                f.reset()
                return self._reply(200, {"ok": True})
            if u.path == "/_fake/clock":
                with f.lock:
                    f.offset += float(json.loads(raw or b"{}").get("advance") or 0)
                return self._reply(200, {"ok": True, "offset": f.offset})
            if u.path == "/upload/youtube/v3/videos":
                body = json.loads(raw or b"{}") if raw else {}
                uid, loc = f.insert_start(self.headers, q, body, self.headers.get("Host") or f.base[7:])
                self.send_response(200)
                self.send_header("Location", loc)
                self.send_header("X-GUploader-UploadID", uid)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if u.path == "/upload/youtube/v3/thumbnails/set":
                return self._send_api(f.thumbnail(self.headers, q, raw, ctype))
            if u.path == "/upload/youtube/v3/captions":
                return self._send_api(f.caption(self.headers, q, raw, ctype))
            if u.path == "/youtube/v3/playlists":
                return self._send_api(f.playlists_insert(self.headers, q, json.loads(raw or b"{}")))
            if u.path == "/youtube/v3/playlistItems":
                return self._send_api(f.playlist_items(self.headers, q, json.loads(raw or b"{}")))
        except ApiFail as e:
            return self._send_api(e.resp)
        except ValueError:
            return self._send_api(gerror(400, "parseError", "Parse Error", "global"))
        self._reply(404, {"error": {"code": 404, "message": "Not Found", "status": "NOT_FOUND"}})

    def do_PUT(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        f = self.fake
        uid = (q.get("upload_id") or [""])[0]
        n = int(self.headers.get("Content-Length") or 0)
        with f.lock:
            s = f.sessions.get(uid)
            expired = not s or s["expired"] or f.now() - s["created"] > SESSION_TTL
        if expired:
            self._drain(n)
            return self._reply(404, "Not Found", "text/plain")
        if not f.modes.get("lenient_put_auth"):  # 문서의 PUT 예시마다 Authorization: Bearer
            try:
                f.auth(self.headers, "videos.insert")
            except ApiFail as e:
                self._drain(n)
                return self._send_api(e.resp)
        if s.get("video"):
            self._drain(n)
            return self._done(s["video"])
        cr = self.headers.get("Content-Range") or ""
        m = re.fullmatch(r"bytes (?:(\d+)-(\d+)|\*)/(\d+)", cr.strip())
        if not m or int(m.group(3)) != s["total"]:
            self._drain(n)
            return self._send_api(gerror(400, "badRequest", "Failed to parse Content-Range header."))
        if m.group(1) is None:  # 상태 묻기
            self._drain(n)
            return self._incomplete(s)
        a, b = int(m.group(1)), int(m.group(2))
        if b < a or b - a + 1 != n:
            self._drain(n)
            return self._send_api(gerror(400, "badRequest", "Content-Range does not match Content-Length."))
        if b + 1 < s["total"] and n % UNIT:
            self._drain(n)
            return self._send_api(gerror(400, "badRequest", "Invalid request. The chunk size must be a multiple of 256 KiB."))
        if f.modes.get("fail_put_once"):
            with f.lock:
                f.modes["fail_put_once"] = False
            self._drain(n)
            return self._reply(503, "Service Unavailable", "text/plain")
        if f.modes.get("stuck_put"):  # 받았다고 하지 않음 (내용을 버리는 프록시 흉내)
            with f.lock:
                f.modes["stuck_put"] = int(f.modes["stuck_put"]) - 1
            self._drain(n)
            return self._incomplete(s)
        if a != len(s["data"]):  # 받은 데와 다른 곳부터 → 지금 받은 데를 알려 줌 (내용은 버림)
            self._drain(n)
            return self._incomplete(s)
        rate = f.modes.get("rate")
        got, t0 = bytearray(), time.time()
        while len(got) < n:
            drop = f.modes.get("drop_after")  # 보내는 도중에 켜도 듣게 (조각마다 다시 봄)
            try:
                blk = self.rfile.read(min(65536, n - len(got)))
            except (OSError, ValueError):
                blk = b""
            if not blk:  # 앱이 끊음 (멈추기·인터넷 끊김) → 256 KiB 단위로 받은 데까지만
                self._keep(s, a, got)
                self.close_connection = True
                return
            got += blk
            if drop is not None and len(s["data"]) + len(got) >= drop:  # 서버 쪽에서 끊김 흉내
                with f.lock:
                    f.modes["drop_after"] = None
                self._keep(s, a, got)
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.wfile = io.BytesIO()
                self.close_connection = True
                return
            if rate:
                ahead = len(got) / float(rate) - (time.time() - t0)
                if ahead > 0:
                    time.sleep(ahead)
        with f.lock:
            if len(s["data"]) != a:  # 그사이 다른 요청이 이어 받음 → 이 내용은 버리고 지금 받은 데를 알려 줌
                return self._incomplete(s)
            s["data"] += got
            if len(s["data"]) >= s["total"]:
                vid = f.finalize(uid)
                done = True
            else:
                done = False
        if done:
            return self._done(vid)
        return self._incomplete(s)

    def _keep(self, s, a, got):
        with self.fake.lock:
            if len(s["data"]) != a or s.get("video"):  # 이어지지 않으면 버림 (다른 요청이 먼저 받음)
                return
            keep = ((a + len(got)) // UNIT) * UNIT
            s["data"] += got[:max(0, keep - a)]

    def _drain(self, n):
        left = n
        while left > 0:
            try:
                b = self.rfile.read(min(65536, left))
            except OSError:
                return
            if not b:
                return
            left -= len(b)

    def _incomplete(self, s):
        self.send_response(308, "Resume Incomplete")
        if len(s["data"]):
            self.send_header("Range", f"bytes=0-{len(s['data']) - 1}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _done(self, vid):
        with self.fake.lock:
            res = self.fake.video_resource(vid)
        self._reply(200 if self.fake.modes.get("final200") else 201, res)


def main():
    ap = argparse.ArgumentParser(description="가짜 Google (OAuth + YouTube Data API v3) — 시험 전용")
    ap.add_argument("--port", type=int, default=8941)
    ap.add_argument("--rate", type=int, default=None, help="업로드 받는 속도 (바이트/초)")
    a = ap.parse_args()
    fg = FakeGoogle(rate=a.rate)
    fg.start(a.port)
    print(f"fake google on {fg.base}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fg.stop()


if __name__ == "__main__":
    main()
