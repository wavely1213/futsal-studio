"""밖에서 작업 (원격 스튜디오 · D-230~D-234) — 터널로 들어온 '스튜디오 화면' 요청의 경계.

휴대폰 페이지(remote.SITE)에서 연결한 기기가 PC 스튜디오 화면(보관함·편집점·편집실·썸네일·채널 전략·올리기)을 밖에서 그대로 쓴다.
처리는 모두 PC 에서 하고, 원격 리스너(remote.RemoteHandler)가 /r/* 가 아닌 모든 요청을 여기(gate)로 넘긴다.
  - 넘겨주기: 휴대폰 페이지가 서명한 /r/handoff → 한 번 쓰는 표(60초) → <터널>/s/hello#t=… → 같은 출처 POST /s/handoff →
    세션 쿠키(__Host-fs_s · HttpOnly · Secure · SameSite=Strict · 메모리에만) → 스튜디오 화면.
  - 경계 순서 (Host·본문 방식·잠금·요청 수는 remote 가 먼저): 메서드 → 밖에서 작업 허용 → Sec-Fetch-Site → 세션 쿠키 →
    (POST) Origin·CSRF → 길 분류(POLICY · 분류 안 된 길은 막음) → 본문(종류·크기·쿠키 칸) → 처리 (앱의 화면 처리기를 그대로 부름).
  - 응답은 늘 private·no-store(+ Vary: Cookie · 정적 파일만 private 7일 + Vary: Accept-Encoding) · 쪽(HTML)은 nonce CSP · 화면 쪽 스크립트는 remote_ui.js(심)가 CSRF 머리글을 붙임.
app·remote 를 import 하지 않는다 — remote.Service 를 svc 로, app 의 화면 처리기(StudioHandler)를 svc.bridge.studio 로 받는다.
"""
import collections
import gzip
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import socket
import struct
import threading
import time
import unicodedata
from email.utils import formatdate
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import core
import editor
import epfont
import intake
import source
import updater
import upload
import youtube_upload

# ---------- 숫자 ----------
COOKIE_PROD, COOKIE_DEV = "__Host-fs_s", "fs_s"   # 개발(http 루프백)은 Secure 를 못 붙여 __Host- 를 쓸 수 없음
IDLE = 30 * 60                 # 30분 동안 아무 요청이 없으면 끝 (요청마다 늘어남)
ABSOLUTE = 12 * 3600           # 시작부터 12시간이면 끝
HANDOFF_TTL = 60               # 넘겨주기 표 1분 · 한 번
HANDOFF_RATE = (6, 60)         # 기기마다 1분에 6번
CONFIRM_TTL = 120
GONE_MAX, GONE_KEEP = 100, 24 * 3600   # 끝난 세션 기억 (401 에 '끊었어요'·'시간이 지났어요'를 구분하려고)
ACTIVE_SECS = 120              # 마지막 요청이 2분 안이면 'PC 화면에 밖에서 작업 중'
NOTICE_QUIET = 30 * 60         # 그 기기가 30분 동안 세션이 없었으면 새 세션을 ntfy 로 알림
JSON_MAX = 32 << 20
EDIT_UPLOAD_MAX = 64 << 20     # 편집실 바로 가져오기(/api/edit/upload) — 밖에서는 이보다 크면 조각 올리기로
CHUNK = 8 << 20                # 조각 올리기 기본 조각 (Cloudflare 빠른 터널 요청 본문 100MB 보다 훨씬 작게)
CHUNK_MAX = 16 << 20
HANDOFF_BODY_MAX = 1024
FONT_BODY_MAX = epfont.FONT_MAX * 4 // 3 + (1 << 20)   # 은평 스타일 '글꼴 파일 고르기'(/api/style/card_font) — 글꼴 한도 40MB 의 base64(4/3) + JSON 여유
MEDIA_WAIT = 15.0              # 미디어 동시 수가 꽉 찼으면 이만큼 기다려 봄 (편집실이 영상 여러 개를 한꺼번에 열 때)
MEDIA_SEND_TIMEOUT = 120       # 미디어를 보내는 동안의 소켓 기다림 (PC 화면 app.Handler.timeout 과 같게 · 요청을 읽을 때는 RemoteHandler 20초 그대로 · D-368)
FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".woff2": "font/woff2"}   # app.FONT_TYPES 와 같음
STATIC_AGE = "private, max-age=604800"
NO_STORE = "private, no-store"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
NEXT_RE = re.compile(r"^/(editor|thumb)?(\?[A-Za-z0-9%._~&=+\-!'()*]{0,600})?$")   # 넘겨준 뒤 갈 곳 (휴대폰 페이지 proto.nextOk 와 같음)
DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
HELLO_CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; "
             "base-uri 'none'; form-action 'none'")
FEATURES = ["studio", "upload", "download"]
PC_ONLY_LIST = ["폴더·파일 열기", "업데이트·다시 시작", "클로드 설치·로그인", "유튜브 계정 연결·Google 설정",
                "로그인 정보 브라우저(쿠키)", "실행기 설정·기기 연결", "앱 끄기"]

# ---------- 화면 문장 (정해진 것만) ----------
MSG = {
    "none": "PC와 연결이 끊겼어요 · 원격 홈에서 다시 열어 주세요",
    "expired": "오래 쓰지 않아 연결이 끝났어요 · 원격 홈에서 다시 열어 주세요",
    "revoked": "PC에서 연결을 끊었어요 · 다시 쓰려면 원격 홈에서 다시 열어 주세요",
    "logout": "이 브라우저에서 연결을 끝냈어요 · 다시 쓰려면 원격 홈에서 [PC 스튜디오 열기]를 눌러 주세요",
    "studio_off": "PC에서 '밖에서 작업 허용'이 꺼져 있어요 · PC의 '실행기'에서 켜 주세요",
    "pc_only": "PC에서만 할 수 있어요",
    "not_remote": "아직 밖에서 쓸 수 없는 기능이에요 · PC에서 해 주세요",
    "csrf": "화면이 오래됐어요 · 새로 고친 뒤 다시 해 주세요",
    "origin": "허용되지 않은 곳에서 온 요청이에요",
    "cross": "다른 사이트에서 온 요청은 받지 않아요",
    "handoff": "연결 표가 맞지 않거나 시간이 지났어요 · 원격 홈에서 다시 열어 주세요",
    "need_proxy": "밖에서는 가벼운 미리보기 파일로 봐요 · [미리보기 파일 만들기]를 눌러 주세요",
    "busy_media": "동시에 여는 영상이 많아요 · 잠시 뒤 다시 해 주세요",
    "open_pc": "PC에서만 열 수 있어요",
}
LOCK_TITLE = {"none": "다시 연결해 주세요", "expired": "연결이 끝났어요", "revoked": "PC에서 연결을 끊었어요",
              "studio_off": "밖에서 작업이 꺼져 있어요", "handoff": "다시 연결해 주세요", "logout": "연결을 끝냈어요"}
# '이번만' 연결한 기기 (아이디·비밀번호 · D-256): 원격 홈에 [PC 스튜디오 열기]가 남지 않음 → 아이디·비밀번호로 다시 (remote_ui.js·hello.js 와 같은 문장)
MSG_GUEST = {"revoked": "PC에서 연결을 끊었어요 · 다시 쓰려면 원격 홈에서 아이디·비밀번호로 다시 연결해 주세요",
             "logout": "이 기기에서 나갔어요 · 다시 쓰려면 원격 홈에서 아이디·비밀번호로 다시 연결해 주세요"}
CONFIRM_YT = "유튜브에 올릴까요?\n\n제목: {title}\n공개 범위: {privacy}\n편집본: {video}\n\n밖에서 누른 올리기예요 · 맞으면 [확인]을 눌러 주세요"
CONFIRM_RESUME = "멈춘 유튜브 올리기를 이어 할까요?\n\n제목: {title}\n공개 범위: {privacy}\n파일: {video}\n\n밖에서 누른 올리기예요 · 맞으면 [확인]을 눌러 주세요"
CONFIRM_FINISH = "유튜브에 올린 영상의 남은 것(썸네일·자막·재생목록)을 마저 할까요?\n\n제목: {title}\n\n밖에서 누른 마무리예요 · 맞으면 [확인]을 눌러 주세요"

# 줄바꿈·제어·방향 바꾸는 글자 (remote._CTRL 과 같은 규칙 · 기록·파일 이름에 가짜 줄을 못 만들게)
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f؜​-‏ -‮⁠-⁩﻿]")


def clean(s, n=80):
    return _CTRL.sub("", str(s or "").replace("\r", " ").replace("\n", " ")).strip()[:n]


def _h(tok):
    return hashlib.sha256(str(tok).encode("utf-8")).hexdigest()


def _lru_put(d, k, v, cap):
    d[k] = v
    d.move_to_end(k)
    while len(d) > cap:
        d.popitem(last=False)


# ---------- 세션 (메모리에만 · 앱을 끄거나 터널 주소가 바뀌면 모두 끝) ----------

class Sessions:
    """기기마다 세션 하나 (같은 브라우저의 탭들은 쿠키 하나를 같이 씀). 토큰은 쿠키에만, 여기는 sha256 만."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = {}                          # 토큰 해시 → 세션
        self.gone = collections.OrderedDict()    # 끝난 토큰 해시 → (까닭 코드, 시각, 이번만 기기였는지)
        self.seen = {}                           # 기기 → 마지막으로 세션이 있던 때 (새 세션 알림을 30분에 한 번만)

    def _expired(self, rec, now):
        return now - rec["last"] > IDLE or now - rec["created"] > ABSOLUTE or now >= rec.get("hardEnd", float("inf"))

    def _forget(self, rec, code, why):
        self.items.pop(rec["hash"], None)
        _lru_put(self.gone, rec["hash"], (code, self.clock(), bool(rec.get("guest"))), GONE_MAX)
        rec["endedWhy"], rec["endedCode"] = why, code
        self.seen[rec["device"]] = self.clock()
        for c in list(rec.get("conns") or ()):  # 보내는 중인 미디어도 바로 끊음 (로그아웃·PC 에서 끊기·새 연결 · D-368 검토 고침)
            _cut(c)

    def create(self, dev, net):
        """→ (토큰, 세션, 끝낸 옛 세션들). 같은 기기의 옛 세션은 끝냄 (탭들은 새 쿠키를 같이 씀)."""
        now = self.clock()
        tok = secrets.token_urlsafe(32)
        rec = {"id": secrets.token_hex(8), "hash": _h(tok), "device": dev["id"], "name": clean(dev.get("name"), 40) or "기기",
               "csrf": secrets.token_urlsafe(32), "created": now, "last": now, "nets": [net], "prefs": {"fullRes": False},
               "quietBefore": now - self.seen.get(dev["id"], -1e12) >= NOTICE_QUIET}
        if dev.get("guest"):  # '이번만' 기기 (D-256): 그 기기가 끝나는 때(12시간)를 넘지 않음
            rec["guest"] = True
            rec["hardEnd"] = min(now + ABSOLUTE, float(dev.get("exp") or now + ABSOLUTE))
        with self.lock:
            old = [r for r in self.items.values() if r["device"] == dev["id"]]
            for r in old:
                self._forget(r, "expired", "새로 연결")
            self.items[rec["hash"]] = rec
            self.seen[dev["id"]] = now
        return tok, rec, old

    def lookup(self, tok):
        """쿠키 값 → (세션, None) · (None, 'none'|'expired'|'revoked')."""
        if not tok or not TOKEN_RE.fullmatch(tok):
            return None, "none"
        hh, now = _h(tok), self.clock()
        with self.lock:
            rec = self.items.get(hh)
            if rec is not None:
                if self._expired(rec, now):
                    self._forget(rec, "expired", "오래 안 씀" if now - rec["last"] > IDLE else "12시간 지남")
                    return None, "expired"
                return rec, None
            g = self.gone.get(hh)
            if g and now - g[1] < GONE_KEEP:
                return None, g[0]
        return None, "none"

    def gone_guest(self, tok):
        """끝난 쿠키가 '이번만' 기기의 것이었는지 (잠금 쪽 문장을 '아이디·비밀번호로 다시 연결'로)."""
        if not tok or not TOKEN_RE.fullmatch(tok):
            return False
        with self.lock:
            g = self.gone.get(_h(tok))
        return bool(g and len(g) > 2 and g[2])

    def touch(self, rec, net):
        """요청마다: 마지막 시각 · 새 인터넷 대역이면 True (기록에 한 번)."""
        rec["last"] = self.clock()
        self.seen[rec["device"]] = rec["last"]
        if net not in rec["nets"]:
            rec["nets"] = (rec["nets"] + [net])[-5:]
            return True
        return False

    def end(self, pred, code="revoked", why="PC에서 끊음"):
        """조건에 맞는 세션을 끝냄 → 끝낸 세션들."""
        with self.lock:
            gone = [r for r in list(self.items.values()) if pred(r)]
            for r in gone:
                self._forget(r, code, why)
        return gone

    def sweep(self):
        """오래 안 쓴·12시간 지난 세션 정리 → 끝낸 세션들."""
        now = self.clock()
        with self.lock:
            gone = [r for r in list(self.items.values()) if self._expired(r, now)]
            for r in gone:
                self._forget(r, "expired", "오래 안 씀" if now - r["last"] > IDLE else "12시간 지남")
        return gone

    def all(self):
        now = self.clock()
        with self.lock:
            return [r for r in self.items.values() if not self._expired(r, now)]

    def active(self):
        now = self.clock()
        return [r for r in self.all() if now - r["last"] <= ACTIVE_SECS]


class TooMany(Exception):
    pass


def _cut(sock):
    """끝난 세션의 미디어 연결을 끊음: 멈춘 재생기라 막혀 있던 보내기는 곧 오류로 끝나고(자리·스레드 돌려줌), 닫을 때 남은 보낼 몫은
    버림(SO_LINGER 0 → RST · 예전처럼 120초 동안 다시 읽으면 8MB 조각 나머지가 가던 것을 막음 · I-365)."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH" if os.name == "nt" else "ii", 1, 0))
    except (OSError, AttributeError, ValueError):
        pass
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except (OSError, AttributeError, ValueError):
        pass


class _SessWriter:
    """미디어를 보내는 동안의 wfile: 조각(256KB)마다 세션이 살아 있는지 봄 — 끝났으면 ConnectionAbortedError (app._file 이 '화면이 끊음'처럼
    조용히 멈춤 · _cut 이 소켓을 못 끊는 경우에도 더 보내지 않음 · D-368 검토 고침)."""
    PIECE = 256 << 10

    def __init__(self, w, sess):
        self._w, self._sess = w, sess

    def write(self, b):
        mv = memoryview(b)
        for i in range(0, len(mv), self.PIECE):
            if "endedWhy" in self._sess:
                raise ConnectionAbortedError("세션이 끝났어요")
            try:
                self._w.write(mv[i:i + self.PIECE])
            except OSError:
                if "endedWhy" in self._sess:  # _cut 이 끊은 것 (Windows 는 WSAESHUTDOWN 등) → '끊김'으로
                    raise ConnectionAbortedError("세션이 끝났어요") from None
                raise
        return len(mv)

    def __getattr__(self, k):
        return getattr(self._w, k)


class Handoffs:
    """넘겨주기 표: 32바이트 · 60초 · 한 번 · 기기·받은 곳(IP 대역)·갈 곳에 묶임 · 기기마다 하나만 살아 있음."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = {}
        self.rate = {}

    def issue(self, device, net, path):
        now = self.clock()
        with self.lock:
            q = [t for t in self.rate.get(device, []) if now - t < HANDOFF_RATE[1]]
            if len(q) >= HANDOFF_RATE[0]:
                self.rate[device] = q
                raise TooMany("잠시 뒤 다시 열어 주세요")
            self.rate[device] = q + [now]
            self.items = {k: v for k, v in self.items.items() if v["device"] != device and v["exp"] > now}
            tok = secrets.token_urlsafe(32)
            self.items[_h(tok)] = {"device": device, "net": net, "path": path, "exp": now + HANDOFF_TTL}
            if len(self.rate) > 1000:
                self.rate = {k: v for k, v in self.rate.items() if v and now - v[-1] < HANDOFF_RATE[1]}
        return tok

    def take(self, tok, net):
        """한 번만: 맞든 틀리든 그 표는 버림 → {device, path} 또는 None."""
        if not isinstance(tok, str) or not TOKEN_RE.fullmatch(tok):
            return None
        with self.lock:
            v = self.items.pop(_h(tok), None)
        if not v or v["exp"] < self.clock() or v["net"] != net:
            return None
        return v

    def drop(self, devices=None):
        with self.lock:
            self.items = {} if devices is None else {k: v for k, v in self.items.items() if v["device"] not in devices}


class Confirms:
    """유튜브 올리기 확인 (428): 세션·길·본문 sha256 에 묶인 한 번 쓰는 표 (2분)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = collections.OrderedDict()

    def issue(self, sid, route, bh):
        now = self.clock()
        tok = secrets.token_urlsafe(18)
        with self.lock:
            for k in [k for k, v in self.items.items() if v[3] < now]:
                del self.items[k]
            _lru_put(self.items, tok, (sid, route, bh, now + CONFIRM_TTL), 200)
        return tok

    def take(self, tok, sid, route, bh):
        if not isinstance(tok, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24}", tok):
            return False
        with self.lock:
            it = self.items.pop(tok, None)
        return bool(it and it[0] == sid and it[1] == route and hmac.compare_digest(it[2], bh) and it[3] > self.clock())


class Studio:
    """원격 서비스 하나에 하나 (remote.Service.studio)."""

    def __init__(self, svc):
        self.svc = svc
        clock = svc.clock
        self.sessions = Sessions(clock)
        self.handoffs = Handoffs(clock)
        self.confirms = Confirms(clock)
        self.uploads = Uploads(svc)
        self._static = {}

    def drop_all(self, code="none", why="실행기를 끔"):
        self.handoffs.drop()
        return self.sessions.end(lambda r: True, code, why)


# ---------- 길 분류 (POLICY · 분류 안 된 길은 막음) ----------

PUBLIC, OWN, PAGE, STATIC, READ, MEDIA, WRITE, CONFIRM, ADAPT, PC_ONLY = (
    "public", "own", "page", "static", "read", "media", "write", "confirm", "adapt", "pc_only")
_G, _P = "GET", "POST"
POLICY = {
    # 열린 길 (세션 없이) — 정적 글·넘겨주기 표 바꾸기
    (_G, "/s/hello"): PUBLIC, (_G, "/s/hello.js"): PUBLIC, (_P, "/s/handoff"): PUBLIC,
    # 이 모듈이 직접 하는 길 (세션)
    (_G, "/s/session"): OWN, (_P, "/s/logout"): OWN, (_P, "/s/prefs"): OWN,
    (_G, "/s/outputs"): OWN, (_G, "/s/dl"): OWN, (_G, "/s/preview"): OWN, (_P, "/s/outputs/preview"): OWN,
    (_G, "/s/upload/status"): OWN, (_G, "/s/upload/pending"): OWN,
    (_P, "/s/upload/init"): OWN, (_P, "/s/upload/chunk"): OWN, (_P, "/s/upload/finish"): OWN, (_P, "/s/upload/cancel"): OWN,
    (_G, "/s/remote.js"): STATIC, (_G, "/s/remote.css"): STATIC,
    # 화면 (심을 끼워 넣음)
    (_G, "/"): PAGE, (_G, "/editor"): PAGE, (_G, "/thumb"): PAGE,
    # 읽기 (GET)
    **{(_G, p): READ for p in (
        "/api/state", "/api/progress", "/api/style/list", "/api/style/presets", "/api/style/mix", "/api/style/plan_prompt",
        "/api/style/thumb_img", "/api/refs", "/api/refs/recommended", "/api/claude/status",
        "/api/thumb/open", "/api/thumb/brand", "/api/thumb/style",
        "/api/edit/library", "/api/edit/media", "/api/edit/backups", "/api/edit/autoseq", "/api/edit/open", "/api/timeline",
        "/api/upload/kit", "/api/upload/thumb",
        "/api/strategy", "/api/strategy/forecast", "/api/strategy/prompt", "/api/strategy/todos", "/api/strategy/remind",
        "/api/strategy/preview", "/api/youtube/status", "/api/youtube/plan", "/api/youtube/history", "/api/dict",
        "/frame", "/thumbs.jpg", "/api/edit/thumbs.jpg", "/api/edit/poster.jpg", "/api/edit/revaudio",
        "/api/auto", "/api/auto/pending",  # 끝까지 자동으로: 마지막 기록·기본 설정 · 그려 줄 썸네일 요청
        "/api/style/shorts_topic", "/api/style/shorts_layout", "/api/style/card_extras",  # 쇼츠 첫머리 주제 줄 설정 (BR-120 · D-221) · 쇼츠 화면 배치 (BR-240) · 덧붙임 (BR-245)
        "/api/style/card_pic",  # 은평 스타일 띠 그림·로고 그림 (BR-280 · BR-281)
        "/api/style/card_font", "/api/style/ep_font")},  # 은평 스타일 글꼴 줄 · 글꼴 파일 (편집실 미리보기 · BR-311 · BR-312)
    (_G, "/media"): MEDIA, (_G, "/api/edit/file"): MEDIA,
    # 바꾸기·작업 시작 (POST · CSRF)
    **{(_P, p): WRITE for p in (
        "/api/analyze", "/api/bundle", "/api/convert", "/api/download", "/api/list", "/api/render", "/api/whisper/estimate",
        "/api/source", "/api/source/check", "/api/rename", "/api/rename/attach", "/api/crash/dismiss", "/api/dict",
        "/api/unnest",  # 폴더째 넣은 촬영본을 보관함 바로 안으로 꺼내기 (D-460)
        "/api/style/learn", "/api/style/thumbs", "/api/style/delete", "/api/style/rough", "/api/style/tempo", "/api/style/score",
        "/api/style/mix", "/api/style/mix_pick", "/api/style/plan_ai", "/api/style/plan_paste",
        "/api/refs/add", "/api/refs/direction", "/api/refs/learn", "/api/refs/delete", "/api/refs/prune", "/api/refs/restore",
        "/api/refs/move", "/api/refs/banner",
        "/api/thumb/frames", "/api/thumb/cut", "/api/thumb/analyze", "/api/thumb/copy", "/api/thumb/brand", "/api/thumb/ocr",
        "/api/thumb/ab", "/api/thumb/style", "/api/thumb/ab_win", "/api/thumb/judge", "/api/thumb/upload", "/api/thumb/export",
        "/api/thumb/save",
        "/api/edit/save", "/api/edit/oneline", "/api/edit/resplit", "/api/edit/export", "/api/edit/restore", "/api/edit/freeze", "/api/edit/track",
        "/api/edit/qa", "/api/edit/proxy", "/api/edit/cancel", "/api/edit/msg", "/api/edit/autoseq", "/api/edit/upload",
        "/api/edit/srt", "/api/edit/frame", "/api/edit/rescue",  # 저장이 계속 안 될 때 따로 남기기 (D-412) · 자막 파일(SRT) 가져오기 (읽기만 · D-345) · 지금 화면 그림으로 저장 (완성본 폴더 · D-349)
        "/api/upload/kit",
        "/api/strategy/draft", "/api/strategy/save", "/api/strategy/channels", "/api/strategy/refresh", "/api/strategy/pause",
        "/api/strategy/checkup", "/api/strategy/todo", "/api/strategy/takeaway", "/api/strategy/settings", "/api/strategy/ai",
        "/api/strategy/ai_paste",
        "/api/youtube/settings", "/api/youtube/playlists", "/api/youtube/check", "/api/youtube/discard", "/api/youtube/pause",
        "/api/claude/save",  # 클로드 아껴 쓰기 켬/끔 (D-390)
        # 끝까지 자동으로 (D-200~D-207): 시작·이어서 하기 · 편집실 MSG 설정 · 썸네일 그리기 맡기/풀기/보내기 · 효과음 꺼내기
        "/api/auto", "/api/auto/prefs", "/api/auto/claim", "/api/auto/release", "/api/auto/thumb", "/api/edit/sfx",
        # 쇼츠 첫머리 주제 줄 켜기·끄기 (BR-120 · D-220) · 쇼츠 화면 배치 고르기 (BR-240) · 시리즈 이름표·전체 강좌 줄 (BR-245 · BR-246)
        "/api/style/shorts_topic", "/api/style/shorts_layout", "/api/style/card_extras",
        "/api/style/card_pics", "/api/style/card_pic",  # 은평 스타일 띠 그림·로고 켜기/끄기 · 그림 바꾸기 (BR-282)
        "/api/style/card_font")},  # 은평 스타일 '글꼴 파일 고르기'·'처음으로' (BR-312)
    # 유튜브에 실제로 올리는 것: PC 가 만든 확인 문장 (428) 을 한 번 더
    (_P, "/api/youtube/upload"): CONFIRM, (_P, "/api/youtube/resume"): CONFIRM, (_P, "/api/youtube/finish"): CONFIRM,
    # 여기서 대신 답함 (PC 에서 폴더·브라우저를 열지 않음)
    (_P, "/api/open"): ADAPT, (_P, "/api/upload/open"): ADAPT, (_P, "/api/youtube/open"): ADAPT,
    # PC 에서만
    **{(_G, p): PC_ONLY for p in ("/api/ping", "/api/remote", "/api/browsers", "/api/update/check")},
    **{(_P, p): PC_ONLY for p in (
        "/api/update", "/api/restart", "/api/focus", "/api/claude/install", "/api/claude/login", "/api/claude/token",
        "/api/youtube/client", "/api/youtube/login", "/api/youtube/login_cancel", "/api/youtube/logout")},
}
PREFIX = ((_G, "/fonts/", STATIC), (_G, "/stickers/", STATIC), (_G, "/asset/", READ), (_P, "/api/remote/", PC_ONLY))
# 상태를 바꾸지만 기록(studio.log)에는 남기지 않는 것 (자주 저절로 저장됨)
QUIET = {"/api/edit/save", "/api/thumb/save", "/api/strategy/draft", "/api/whisper/estimate", "/api/edit/oneline", "/api/edit/resplit", "/api/thumb/ocr", "/api/edit/srt",
         "/api/youtube/check", "/s/upload/chunk", "/s/upload/init", "/s/prefs", "/api/crash/dismiss",
         "/api/auto/prefs", "/api/auto/claim", "/api/auto/release", "/api/auto/thumb"}
LABELS = {"/api/analyze": "편집점 찾기", "/api/bundle": "한 영상으로 묶기", "/api/convert": "MP4로 바꾸기", "/api/download": "보관함에 담기",
          "/api/list": "채널 불러오기", "/api/edit/export": "내보내기", "/api/edit/proxy": "미리보기 파일 만들기", "/api/edit/qa": "영상 검수",
          "/api/edit/msg": "MSG 후보 만들기", "/api/thumb/export": "썸네일 내보내기", "/api/thumb/ab": "A/B 썸네일",
          "/api/upload/kit": "올리기 키트", "/api/youtube/upload": "유튜브에 올리기", "/api/youtube/resume": "유튜브 이어 올리기",
          "/api/youtube/finish": "유튜브 마무리", "/api/rename": "이름 바꾸기", "/api/unnest": "폴더 안 영상 꺼내기", "/api/style/learn": "스타일 배우기",
          "/api/refs/add": "학습용 영상 받기", "/api/strategy/refresh": "채널 전략 새로 고침", "/api/strategy/save": "채널 전략 저장",
          "/s/upload/finish": "보관함에 올림", "/s/outputs/preview": "작은 미리보기 만들기", "/s/logout": "연결 끝내기",
          "/api/auto": "끝까지 자동으로", "/api/edit/sfx": "효과음 꺼내기",
          "/api/edit/frame": "지금 화면 그림으로 저장"}


def classify(method, path):
    """→ 분류 또는 None (분류 안 된 길: /api/ 아래면 '아직 밖에서 쓸 수 없어요', 그 밖은 없는 주소)."""
    k = POLICY.get((method, path))
    if k:
        return k
    for m, pre, kind in PREFIX:
        if m == method and path.startswith(pre) and len(path) > len(pre):
            return kind
    return None


YT_GUARD = ("/api/download", "/api/list", "/api/refs/add")
YT_IDS_MAX = 2000
_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def yt_guard(svc, path, b):
    """밖에서 온 YouTube 받기(/api/download ids)·채널 불러오기(/api/list url)·학습용 영상 받기(/api/refs/add url) →
    휴대폰 원격과 같은 규칙(remote.youtube_id · remote.listing_url)으로 YouTube 영상 id·표준 주소로만 바꾼 본문 · 그 밖은 ValueError.
    앱(core.download·core._listing_target)은 'http…' 를 그대로 yt-dlp 에 넘기므로, 밖에서 온 주소가 PC 에서 집 안 공유기·내부망·
    클라우드 메타데이터 같은 곳으로 요청을 보내지 못하게 (SSRF · D-235). 다른 칸은 그대로 둠."""
    b = dict(b)
    if path == "/api/download":
        ids = b.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > YT_IDS_MAX:
            raise ValueError("받을 영상을 골라 주세요")
        out = []
        for v in ids:
            if not isinstance(v, str):
                raise ValueError("YouTube 영상만 받을 수 있어요")
            v = v.strip()
            out.append(v if _YT_ID_RE.fullmatch(v) else svc.youtube_id(v))
        b["ids"] = out
        return b
    url = b.get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError("YouTube 채널·영상·재생목록 주소만 넣을 수 있어요")
    b["url"] = svc.listing_url(url or "")
    return b


def bucket_of(path):
    """요청 수 바구니: 넘겨주기(표 바꾸기)는 열린 길 바구니(좁게) · 그 밖은 스튜디오 바구니 — 정적인 인사 쪽(/s/hello·hello.js)도
    스튜디오 바구니 (다시 연결할 때마다 열린 길 몫을 셋씩 쓰면 몇 번 만에 막힘)."""
    return "open" if path == "/s/handoff" else "studio"


# ---------- 응답 ----------

def hardening(h):
    """스튜디오 응답에 늘 붙는 머리글 (app.StudioHandler.end_headers 가 아직 안 보낸 것만 붙임)."""
    if getattr(h, "_studio_cache", None) == STATIC_AGE:
        # 정적 파일(글꼴·스티커·심·hello.js)은 어느 세션에나 같은 바이트이고 비밀이 없음 → 쿠키로 나누지 않음. 'Vary: Cookie' 면 새 연결(넘겨주기마다
        # 새 쿠키)마다 브라우저가 7일 보관한 글꼴 2.45MB 를 다시 받았음 (D-366 검토 고침) · 받을 때는 늘 세션 검사 · private 이라 Cloudflare 는 안 둠
        vary = "Accept-Encoding"
    else:
        vary = "Cookie, Accept-Encoding" if getattr(h, "_studio_vary_ae", False) else "Cookie"
    return [("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "SAMEORIGIN" if getattr(h, "_studio_frame_self", False) else "DENY"),
            ("Cross-Origin-Opener-Policy", "same-origin"), ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ("Cache-Control", getattr(h, "_studio_cache", None) or NO_STORE), ("Vary", vary),
            ("Content-Security-Policy", getattr(h, "_studio_csp", None) or DEFAULT_CSP)]


def _wants_gzip(h):
    return "gzip" in (h.headers.get("Accept-Encoding") or "").lower()


def send(h, code, body, ctype="application/json; charset=utf-8", extra=None, gz=False):
    if isinstance(body, (bytes, bytearray)):
        data = bytes(body)
    else:
        try:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            data = json.dumps(body, ensure_ascii=True).encode("ascii")
    hdr = dict(extra or {})
    if gz and len(data) >= 2048 and _wants_gzip(h):
        data = gzip.compress(data, 5)
        hdr["Content-Encoding"] = "gzip"
        h._studio_vary_ae = True
    try:
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(data)))
        for k, v in hdr.items():
            h.send_header(k, v)
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(data)
    except (ConnectionError, TimeoutError):
        pass


def err(h, code, key, status=None, **more):
    """정해진 문장 오류 (JSON · 추적 없음)."""
    send(h, status or 403, dict({"ok": False, "code": code, "error": MSG.get(key or code, MSG["pc_only"])}, **more))


def _navigation(h):
    dest = (h.headers.get("Sec-Fetch-Dest") or "").lower()
    if dest:
        return dest == "document"
    return h.command == "GET" and "text/html" in (h.headers.get("Accept") or "")


def lock_page(h, svc, code, status=401, guest=False):
    """세션 없이 화면을 열었을 때: 정적 안내 쪽 (스크립트는 /s/hello.js 하나 · 원격 홈으로 · none·expired 는 저절로 다시 연결).
    guest: 끝난 세션이 '이번만' 기기의 것 → 문장을 '아이디·비밀번호로 다시 연결'로 (fs-guest 로 hello.js 에도)."""
    site = svc.site()
    title = LOCK_TITLE.get(code, LOCK_TITLE["none"])
    msg = (guest and MSG_GUEST.get(code)) or MSG.get(code, MSG["none"])
    page = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="referrer" content="no-referrer"><meta name="fs-lock" content="{html.escape(code)}">'
            + ('<meta name="fs-guest" content="1">' if guest else '') +
            f'<meta name="fs-site" content="{html.escape(site)}"><title>풋살 스튜디오 · {html.escape(title)}</title>'
            f'<script src="/s/hello.js"></script></head><body><main id="box"><h1>{html.escape(title)}</h1><p id="msg">{html.escape(msg)}</p>'
            f'<p><a id="home" href="{html.escape(site)}">원격 홈으로 가기</a></p></main></body></html>').encode("utf-8")
    h._studio_csp = HELLO_CSP
    send(h, status, page, "text/html; charset=utf-8")


def hello_page(site):
    """연결 쪽 (/s/hello): 원격 홈 주소(svc.site())를 meta 로 — 표가 없거나·쿠키가 막혔거나·끊겼을 때도 [원격 홈으로 가기]와 그 주소를 보여 줌."""
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="referrer" content="no-referrer"><meta name="fs-site" content="{html.escape(site)}"><title>풋살 스튜디오 · 연결 중</title>'
            '<script src="/s/hello.js"></script></head>'
            '<body><main id="box"><h1>PC 스튜디오에 연결하는 중이에요…</h1><p id="msg">잠시만 기다려 주세요</p></main></body></html>').encode("utf-8")


def _static_file(st, name):
    """앱 폴더의 정적 파일 → (바이트, 짧은 지문) · 바뀌면 다시 읽음."""
    p = core.APP_DIR / name
    stt = p.stat()
    key = (stt.st_mtime_ns, stt.st_size)
    hit = st._static.get(name)
    if not hit or hit[0] != key:
        data = p.read_bytes()
        hit = (key, data, hashlib.sha256(data).hexdigest()[:12])
        st._static[name] = hit
    return hit[1], hit[2]


def _font_gz(st, path):
    """글꼴(/fonts/<이름>) gzip 바이트 · 종류 — 한 번 줄여 두고 다시 씀 (파일이 바뀌면 다시) · 없는 글꼴·다른 확장자는 None (앱이 404)."""
    p = (editor.FONTS / Path(path).name).resolve()
    ct = FONT_TYPES.get(p.suffix.lower())
    if not ct or ct == "font/woff2" or p.parent != editor.FONTS.resolve() or not p.is_file():  # woff2 는 이미 압축됨
        return None
    stt = p.stat()
    key = ("font", p.name)
    hit = st._static.get(key)
    if not hit or hit[0] != (stt.st_mtime_ns, stt.st_size):
        hit = ((stt.st_mtime_ns, stt.st_size), gzip.compress(p.read_bytes(), 6), ct)
        st._static[key] = hit
    return hit[1], hit[2]


PAGES = {"/": "ui.html", "/editor": "editor.html", "/thumb": "thumb.html"}


def page_csp(nonce):
    return ("default-src 'self'; script-src 'self' 'nonce-%s'; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' data: https://cdn.jsdelivr.net; img-src 'self' data: blob: https://i.ytimg.com; media-src 'self' blob:; "
            "connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'") % nonce


def inject(raw, nonce, csrf, site, device, js_ver, css_ver):
    """화면 HTML → 밖에서 쓰는 모양: <html data-remote="1"> · CSRF·주소 meta · 심 스크립트를 head 의 첫 스크립트로 (fetch·sendBeacon 을
    화면 스크립트보다 먼저 감쌈) · 모든 '<script>' 에 nonce (요청에서 온 글은 넣지 않음)."""
    s = raw.decode("utf-8")
    s = s.replace("<html", '<html data-remote="1"', 1)
    meta = (f'<meta name="fs-csrf" content="{html.escape(csrf)}"><meta name="fs-site" content="{html.escape(site)}">'
            f'<meta name="fs-device" content="{html.escape(device)}">'
            f'<script nonce="{nonce}" src="/s/remote.js?v={js_ver}"></script>\n')
    anchor = '<meta charset="utf-8">\n'
    s = s.replace(anchor, anchor + meta, 1) if anchor in s else s.replace("<head>", "<head>" + meta, 1)
    s = s.replace("<script>", f'<script nonce="{nonce}">')
    s = s.replace("</head>", f'<link rel="stylesheet" href="/s/remote.css?v={css_ver}">\n</head>', 1)
    return s.encode("utf-8")


def scrub_state(d, home=None):
    """/api/state 를 밖으로: 사용자 폴더 경로(Windows 사용자 이름이 들어감)는 '~' 로 (모든 글자 칸)."""
    home = str(home or Path.home())
    if len(home) <= 3:
        return d
    alts = {home, home.replace("\\", "/")}

    def fix(v):
        if isinstance(v, str):
            for a in alts:
                if a in v:
                    v = v.replace(a, "~")
            return v
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v
    return fix(d)


# ---------- 쿠키 ----------

def cookie_name(svc):
    return COOKIE_DEV if svc.dev() else COOKIE_PROD


def read_cookie(h, name):
    raw = h.headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v.strip()
    return None


def set_cookie(svc, tok, max_age):
    """max_age None: 세션 쿠키 (브라우저를 닫으면 사라짐 · '이번만' 기기 · D-256)."""
    attrs = ["Path=/", "HttpOnly", "SameSite=Strict"] + ([f"Max-Age={max(0, int(max_age))}"] if max_age is not None else [])
    if not svc.dev():
        attrs.insert(1, "Secure")
    return f"{cookie_name(svc)}={tok}; " + "; ".join(attrs)


# ---------- 경계 ----------

def expected_origin(h, svc):
    """이 요청이 와야 하는 출처: 터널 주소 (개발 때는 Host 그대로의 http 주소)."""
    if svc.dev():
        host = h.headers.get("Host") or ""
        if host.startswith(("127.0.0.1:", "localhost:")):
            return "http://" + host
    u = urlparse(svc.url or "")
    return f"{u.scheme}://{u.netloc}".lower() if u.scheme and u.netloc else None


def _ctype(h):
    return (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()


def _length(h):
    try:
        n = int(h.headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def gate(h, method, svc, ip):
    """/r/* 가 아닌 모든 요청 (remote.RemoteHandler._handle 가 Host·Transfer-Encoding·잠금·요청 수를 본 뒤 부름)."""
    st = svc.studio
    h._studio_req, h._studio_sent, h._studio_cache, h._studio_csp, h._studio_vary_ae = True, set(), None, None, False
    h._studio_frame_self = False
    h.close_connection = True
    u = urlparse(h.path)
    path = u.path
    if method not in (_G, _P):
        return err(h, "method", "pc_only", 405)
    if method == _G and path == "/s/hello.js":  # 안내 쪽의 정적 스크립트 (비밀 없음 · 꺼져 있어도 안내 쪽이 돌게)
        return _public(h, svc, method, path, ip, None)
    if not svc.studio_allowed():
        if _navigation(h) and method == _G:
            return lock_page(h, svc, "studio_off", 403)
        return err(h, "studio_off", None, 403, site=svc.site())
    kind = classify(method, path)
    sfs = (h.headers.get("Sec-Fetch-Site") or "").lower()
    if sfs and sfs not in ("same-origin", "none") and not (kind == PUBLIC and path == "/s/hello" and method == _G):
        if _navigation(h) and method == _G:
            return lock_page(h, svc, "none")  # 다른 사이트의 링크로 화면을 열려 함 → 원격 홈으로 (쿠키도 안 옴)
        return err(h, "cross", None, 403)
    net = svc.net_of(ip)
    if kind == PUBLIC:
        return _public(h, svc, method, path, ip, net)
    tok = read_cookie(h, cookie_name(svc))
    sess, why = st.sessions.lookup(tok)
    if not sess:  # 모르는·지난 쿠키는 잠금 횟수에 세지 않음 (열어 둔 탭의 1초 묻기가 스스로를 잠그지 않게)
        guest = st.sessions.gone_guest(tok)
        if _navigation(h) and method == _G:
            return lock_page(h, svc, why, guest=guest)
        return send(h, 401, dict({"ok": False, "code": why, "error": (guest and MSG_GUEST.get(why)) or MSG[why], "site": svc.site()},
                                 **({"guest": True} if guest else {})))
    dev = svc.store.device(sess["device"])
    if not dev:  # 기기 연결을 끊었으면 (열쇠가 없음) 세션도 끝
        st.sessions.end(lambda r: r is sess, "revoked", "기기 연결 끊음")
        guest = bool(sess.get("guest"))
        return send(h, 401, dict({"ok": False, "code": "revoked", "error": MSG_GUEST["revoked"] if guest else MSG["revoked"], "site": svc.site()},
                                 **({"guest": True} if guest else {})))
    if st.sessions.touch(sess, net):
        svc.log(f"밖에서 작업 · {sess['name']} · 인터넷 연결이 바뀌었어요")
    svc.touch(dev)
    if method == _P:
        want = expected_origin(h, svc)
        if not want or (h.headers.get("Origin") or "").lower() != want:
            return err(h, "origin", None, 403)
        if not hmac.compare_digest((h.headers.get("X-FS-CSRF") or "").encode(), sess["csrf"].encode()):
            svc.auth_failed(ip)
            return err(h, "csrf", None, 403)
    if kind is None:
        if path.startswith("/api/"):
            return err(h, "not_remote", None, 403)
        return send(h, 404, {"ok": False, "error": "없는 주소예요"})
    if kind == PC_ONLY:
        return err(h, "pc_only", None, 403)
    body, b = b"", {}
    if method == _P:
        n = _length(h)
        if n is None:
            return send(h, 411, {"ok": False, "error": "요청을 보내는 방식이 맞지 않아요 · 새로 고친 뒤 다시 해 주세요"})
        raw_ok = path in ("/api/edit/upload", "/s/upload/chunk")
        limit = (EDIT_UPLOAD_MAX if path == "/api/edit/upload" else CHUNK_MAX if path == "/s/upload/chunk"
                 else FONT_BODY_MAX if path == "/api/style/card_font" else JSON_MAX)
        if n > limit:
            h.close_connection = True
            if path == "/api/style/card_font":   # 글꼴 고르기에는 조각 올리기가 없음 → 크기 안내만 (ui.html CARD_FONT_MAX 와 같은 문장)
                return send(h, 413, {"ok": False, "code": "too_big", "error": f"글꼴 파일이 너무 커요 · {epfont.FONT_MAX >> 20}MB 보다 작은 파일을 골라 주세요"})
            return send(h, 413, {"ok": False, "code": "too_big", "error": "한 번에 보내기에 너무 커요 · 조각 올리기로 다시 해 주세요"})
        ct = _ctype(h)
        if raw_ok:
            if ct != "application/octet-stream":
                return send(h, 415, {"ok": False, "error": "잘못된 요청이에요"})
        else:
            if ct != "application/json":
                return send(h, 415, {"ok": False, "error": "잘못된 요청이에요"})
            body = h.rfile.read(n) if n else b""
            if len(body) != n:
                return send(h, 400, {"ok": False, "error": "요청을 끝까지 받지 못했어요 · 다시 해 주세요"})
            try:
                b = json.loads(body or b"{}")
            except (ValueError, RecursionError):
                return send(h, 400, {"ok": False, "error": "잘못된 요청이에요"})
            if not isinstance(b, dict):
                return send(h, 400, {"ok": False, "error": "잘못된 요청이에요"})
            if b.get("cookies"):  # 로그인 정보(쿠키) 브라우저 읽기는 PC 화면에서만 (D-009 · D-044)
                return err(h, "pc_only", None, 403)
            if path in YT_GUARD:  # YouTube 받기·불러오기: YouTube 주소만 · 표준 주소로 다시 쓴 본문을 앱에 (D-235)
                try:
                    b = yt_guard(svc, path, b)
                except ValueError as e:
                    return send(h, 400, {"ok": False, "code": "yt_only", "error": str(e)})
                body = json.dumps(b, ensure_ascii=False).encode("utf-8")
                h.headers.replace_header("Content-Length", str(len(body)))
            h.rfile = io.BytesIO(body)  # 앱 처리기가 같은 본문을 다시 읽음 (_body)
    if kind == OWN:
        return own(h, svc, sess, method, path, u, b)
    if kind == PAGE:
        return _page(h, svc, sess, path)
    if kind == STATIC:
        h._studio_cache = STATIC_AGE
        if path.startswith("/fonts/") and _wants_gzip(h) and not h.headers.get("Range"):
            hit = _font_gz(st, path)
            if hit:  # 자막·타이틀 글꼴 4MB → gzip 2.4MB (처음 열 때 첫 소리·첫 화면과 망을 덜 다툼 · 7일 보관은 그대로 · D-366)
                h._studio_vary_ae = True
                return send(h, 200, hit[0], hit[1], extra={"Content-Encoding": "gzip"})
        if path in ("/s/remote.js", "/s/remote.css"):
            data, _ = _static_file(st, "remote_ui.js" if path.endswith(".js") else "remote_ui.css")
            return send(h, 200, data, "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8", gz=True)  # 심 42KB → 12KB (D-366)
        return _dispatch(h, svc, sess, method, path, b)
    if kind == ADAPT:
        return _adapt(h, svc, sess, path, b)
    if kind == MEDIA:
        return _media(h, svc, sess, path, u)
    if kind == CONFIRM:
        bh = hashlib.sha256(body).hexdigest()
        tokc = h.headers.get("X-FS-Confirm")
        if not (tokc and st.confirms.take(tokc, sess["id"], path, bh)):
            text = _confirm_text(path, b)
            if text:
                return send(h, 428, {"ok": False, "confirm": {"text": text, "token": st.confirms.issue(sess["id"], path, bh)}})
    if method == _P and path not in QUIET:
        _log_action(svc, sess, path, b)
    return _dispatch(h, svc, sess, method, path, b)


def _dispatch(h, svc, sess, method, path, b):
    """앱의 화면 처리기를 그대로 (이 요청 동안 '밖에서 · <기기>' 표시 · 다운로드 엔진 스스로 고치기 없음)."""
    h._studio_gz = True
    return h.studio_call(method, sess["name"])


def _log_action(svc, sess, path, b):
    label = LABELS.get(path) or path.replace("/api/", "")
    target = b.get("name") if isinstance(b.get("name"), str) else (b.get("names") or [None])[0] if isinstance(b.get("names"), list) else None
    svc.log(f"원격 · {sess['name']} · {label}" + (f" · {clean(target, 80)}" if isinstance(target, str) and target else ""))


def _public(h, svc, method, path, ip, net):
    st = svc.studio
    if path == "/s/hello":
        h._studio_csp = HELLO_CSP
        return send(h, 200, hello_page(svc.site()), "text/html; charset=utf-8")
    if path == "/s/hello.js":
        h._studio_cache = STATIC_AGE
        data, _ = _static_file(st, "hello.js")
        return send(h, 200, data, "text/javascript; charset=utf-8")
    # POST /s/handoff — 같은 출처 · JSON · 1KB 안 · 표는 한 번
    want = expected_origin(h, svc)
    sfs = (h.headers.get("Sec-Fetch-Site") or "").lower()
    if not want or (h.headers.get("Origin") or "").lower() != want or (sfs and sfs != "same-origin"):
        return err(h, "origin", None, 403)
    n = _length(h)
    if _ctype(h) != "application/json" or n is None or n > HANDOFF_BODY_MAX:
        return send(h, 400, {"ok": False, "error": "잘못된 요청이에요"})
    try:
        b = json.loads(h.rfile.read(n) or b"{}")
    except (ValueError, RecursionError):
        b = None
    v = st.handoffs.take(b.get("t") if isinstance(b, dict) else None, net)
    dev = svc.store.device(v["device"]) if v else None
    if not v or not dev or not svc.studio_allowed():
        svc.auth_failed(ip)
        return send(h, 403, {"ok": False, "code": "handoff", "error": MSG["handoff"], "site": svc.site()})
    tok, rec, old = st.sessions.create(dev, net)
    svc.touch(dev)
    svc.studio_started(rec, bool(old))
    hdr = {"Set-Cookie": set_cookie(svc, tok, None if dev.get("guest") else ABSOLUTE)}
    return send(h, 200, {"ok": True, "next": v["path"]}, extra=hdr)


def _page(h, svc, sess, path):
    st = svc.studio
    raw, _ = _static_file(st, PAGES[path])
    _, jsv = _static_file(st, "remote_ui.js")
    _, cssv = _static_file(st, "remote_ui.css")
    nonce = secrets.token_urlsafe(18)
    data = inject(raw, nonce, sess["csrf"], svc.site(), sess["name"], jsv, cssv)
    h._studio_csp = page_csp(nonce)
    h._studio_frame_self = True  # 화면만 같은 출처 틀 허용 (끝까지 자동으로의 보이지 않는 썸네일 틀 · ui·편집실·썸네일 화면이 띄움 · D-201)
    return send(h, 200, data, "text/html; charset=utf-8", gz=True)


def session_view(svc, sess):
    return {"ok": True, "device": sess["name"], "pc": svc.store.data["pc"]["name"], "csrf": sess["csrf"],
            "expiresAt": int(min(sess["created"] + ABSOLUTE, sess.get("hardEnd", float("inf")))), "guest": bool(sess.get("guest")),
            "idleSec": IDLE, "site": svc.site(), "pcOnly": PC_ONLY_LIST, "features": FEATURES, "prefs": dict(sess["prefs"]),
            "chunk": CHUNK, "chunkMax": CHUNK_MAX, "upMax": UP_PER_DEVICE, "editUploadMax": EDIT_UPLOAD_MAX}


def own(h, svc, sess, method, path, u, b):
    st = svc.studio
    q = parse_qs(u.query)
    if path == "/s/session":
        return send(h, 200, session_view(svc, sess))
    if path == "/s/logout":
        # 'logout' 으로 끝냄 · 쿠키는 지우지 않음 (서버에서 이미 죽은 표) → 같은 브라우저의 다른 탭도 다음 요청에 'logout' 을 받아
        # 저절로 다시 연결하지 않고 '연결을 끝냈어요'로 잠김 (쿠키를 지우면 'none' 이라 원격 홈이 다시 넘겨줘 버림 · D-236)
        st.sessions.end(lambda r: r is sess, "logout", "밖에서 연결 끝냄")
        svc.studio_ended([sess])
        if sess.get("guest"):  # 이번만 기기: [이 기기에서 나가기] → 기기 열쇠까지 버림 (D-256)
            svc.guest_logout(sess["device"])
        return send(h, 200, {"ok": True, "site": svc.site()})
    if path == "/s/prefs":
        if "fullRes" in b:
            if not isinstance(b["fullRes"], bool):
                return send(h, 400, {"ok": False, "error": "잘못된 요청이에요"})
            sess["prefs"]["fullRes"] = b["fullRes"]
        return send(h, 200, {"ok": True, "prefs": dict(sess["prefs"])})
    if path.startswith("/s/upload/"):
        return st.uploads.route(h, svc, sess, path, q, b)
    if path in ("/s/outputs", "/s/dl", "/s/preview", "/s/outputs/preview"):
        return outputs_route(h, svc, sess, path, q, b)
    return send(h, 404, {"ok": False, "error": "없는 주소예요"})


# ---------- 미디어 (밖에서는 가벼운 미리보기 파일만 · 원본 화질은 켰을 때만) ----------

def _video_ext(name):
    return Path(str(name or "")).suffix.lower() in core.VIDEO_EXTS


def media_slot(svc, sess):
    """미디어 동시 수 (기기마다 · 전체) — 꽉 찼으면 잠깐 기다려 봄 → 들어갔으면 True."""
    end = time.monotonic() + MEDIA_WAIT
    while True:
        if svc.limiter.media_enter(sess["device"]):
            return True
        if time.monotonic() > end:
            return False
        time.sleep(0.05)


def _media(h, svc, sess, path, u):
    q = parse_qs(u.query)
    full = sess["prefs"].get("fullRes")
    if path == "/media":
        name = (q.get("name") or [""])[0]
        try:
            editor.video_path(name)
        except (ValueError, FileNotFoundError):
            return send(h, 404, {"ok": False, "error": "not found"})
        if not full:
            p = editor.proxy_path(name, "videos")
            if not p.is_file():
                return err(h, "need_proxy", None, 403)
            return _media_file(h, svc, sess, lambda: h._file(p, "video/mp4"))
    else:  # /api/edit/file
        f, src = (q.get("f") or [""])[0], (q.get("src") or ["videos"])[0]
        if _video_ext(f) and not full:
            try:
                p = editor.proxy_path(editor.safe_name(Path(f).name), src)
            except ValueError:
                return send(h, 404, {"ok": False, "error": "not found"})
            if not p.is_file():
                return err(h, "need_proxy", None, 403)
            if q.get("proxy") != ["1"]:
                h.path = path + "?" + urlencode({"src": src, "f": f, "proxy": "1"})
    return _media_file(h, svc, sess, lambda: _dispatch(h, svc, sess, _G, path, {}))


def _media_file(h, svc, sess, fn):
    if not media_slot(svc, sess):
        return send(h, 503, {"ok": False, "error": MSG["busy_media"]}, extra={"Retry-After": "3"})
    conn, conns, w = getattr(h, "connection", None), sess.setdefault("conns", set()), h.wfile
    if conn is not None:
        conns.add(conn)  # 세션이 끝나면 _forget 이 끊음 (보내는 동안만 올려 둠)
    try:
        if "endedWhy" in sess:  # 자리를 기다리는 사이 연결이 끝남
            code = sess.get("endedCode") or "none"
            return send(h, 401, {"ok": False, "code": code, "error": (sess.get("guest") and MSG_GUEST.get(code)) or MSG.get(code) or MSG["none"],
                                 "site": svc.site()})
        try:  # 보내는 동안은 느린 망(멈춘 재생기)에서도 20초에 끊지 않음 — 예전엔 8MB 중 3~4MB 만 보내고 끊겨 다시 받아야 했음 (D-368)
            conn.settimeout(MEDIA_SEND_TIMEOUT)
        except (AttributeError, OSError):
            pass
        h.wfile = _SessWriter(w, sess)  # 대신 세션이 끝나면 120초를 기다리지 않고 바로 멈춤 (D-368 검토 고침)
        return fn()
    finally:
        h.wfile = w
        conns.discard(conn)
        svc.limiter.media_leave(sess["device"])


# ---------- 대신 답하기 (PC 에서 폴더·브라우저를 열지 않음) ----------

def _adapt(h, svc, sess, path, b):
    if path == "/api/open":
        w = b.get("which")
        m = {"out": "outputs", "videos": "upload", "log": "log"}
        if isinstance(w, str) and w in m:
            return send(h, 200, {"ok": True, "remote": m[w]})
        return send(h, 200, {"ok": False, "error": MSG["open_pc"]})
    if path == "/api/upload/open":
        what = b.get("what")
        if what == "studio":
            url = upload.studio_url()  # 우리 채널 업로드 창 (모르면 스튜디오 첫 화면)
            return send(h, 200, {"ok": True, "openUrl": url, "channel": url != upload.STUDIO_URL})
        if what == "file":
            try:
                n = editor.safe_name(b.get("file"))
            except (ValueError, TypeError):
                return send(h, 400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            if not out_file(n):
                return send(h, 404, {"ok": False, "error": "파일을 찾지 못했어요"})
            return send(h, 200, {"ok": True, "download": "/s/dl?" + urlencode({"f": n})})
        if what == "template":
            return err(h, "pc_only", None, 403)
        return send(h, 400, {"ok": False, "error": "무엇을 열지 모르겠어요"})
    # /api/youtube/open
    try:
        url = youtube_upload.link_of(str(b.get("what") or ""), b.get("videoId"), b.get("key"))
    except youtube_upload.UploadError as e:
        return send(h, 400, {"ok": False, "error": str(e)})
    return send(h, 200, {"ok": True, "openUrl": url})


# ---------- 유튜브 확인 문장 (PC 가 만듦) ----------

def _confirm_text(path, b):
    """→ 확인 문장 · None 이면 묻지 않고 앱이 답하게 (막는 문제가 있으면 앱이 400 으로 알려 줌)."""
    yu = youtube_upload
    try:
        if path == "/api/youtube/upload":
            n = editor.safe_name(b.get("name"))
            editor.video_path(n)
            seq = b.get("seq") or None
            if seq is not None and not isinstance(seq, str):
                return None
            opts = b.get("opts") if isinstance(b.get("opts"), dict) else {}
            priv = opts.get("privacy") if opts.get("privacy") in yu.PRIVACY_KO else "private"
            p = yu.plan(n, seq, priv if priv in yu.PRIVACY_KO else None)
            if p.get("problems"):
                return None
            vid = upload.files_for(n, seq).get("video")
            return CONFIRM_YT.format(title=clean((p.get("kit") or {}).get("title"), 100) or "(제목 없음)",
                                     privacy=yu.PRIVACY_KO.get(priv, priv), video=clean(Path(str(vid)).name if vid else n, 100))
        if path == "/api/youtube/resume":
            key = str(b.get("key") or "")
            s = next((x for x in yu.pending() if x.get("key") == key), None)
            if not s:
                return None
            return CONFIRM_RESUME.format(title=clean(s.get("title"), 100) or "(제목 없음)",
                                         privacy=yu.PRIVACY_KO.get(s.get("privacy"), s.get("privacy") or "-"),
                                         video=clean(s.get("file") or s.get("name"), 100))
        if path == "/api/youtube/finish":
            vid = str(b.get("videoId") or "")
            h = next((x for x in yu.history(50).get("items") or [] if x.get("videoId") == vid), None)
            return CONFIRM_FINISH.format(title=clean((h or {}).get("title") or (h or {}).get("name"), 100) or "(이 영상)")
    except (ValueError, TypeError, FileNotFoundError, LookupError, OSError):
        return None
    return None


# ---------- 완성본 받기 (밖에서 내 기기로) ----------

DL_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".txt": "text/plain; charset=utf-8", ".srt": "text/plain; charset=utf-8", ".xml": "application/xml"}
OUT_KINDS = {".mp4": "video", ".mov": "video", ".jpg": "image", ".jpeg": "image", ".png": "image", ".txt": "text", ".srt": "srt",
             ".xml": "xml"}


def out_file(name):
    """완성본 폴더 바로 안의 파일만 (점으로 시작하는 이름·허용 안 된 확장자·폴더 밖은 None)."""
    try:
        n = editor.safe_name(name)
    except (ValueError, TypeError):
        return None
    if n.startswith(".") or Path(n).suffix.lower() not in DL_TYPES:
        return None
    p = (core.OUT / n).resolve()
    if p.parent != core.OUT.resolve() or not p.is_file():
        return None
    return p


def _preview_of(name):
    try:
        src = out_file(name)
        pv = editor.out_preview_path(name)
    except ValueError:
        return None
    if not src or not pv.is_file() or pv.stat().st_mtime < src.stat().st_mtime:  # 같은 이름으로 다시 만든 완성본이면 옛 미리보기는 안 씀
        return None
    return pv


def outputs_list(svc):
    try:
        files = [p for p in core.OUT.iterdir() if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in OUT_KINDS]
    except OSError:
        files = []

    def mt(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0
    files.sort(key=mt, reverse=True)
    out = []
    for p in files[:200]:
        try:
            stt = p.stat()
        except OSError:
            continue
        kind = OUT_KINDS[p.suffix.lower()]
        item = {"name": p.name, "kind": kind, "size": stt.st_size, "mtime": int(stt.st_mtime)}
        if kind == "video":
            item["preview"] = bool(_preview_of(p.name))
            item["qa"] = svc.qa.get(p.name)
        out.append(item)
    return out


def _disposition(name, inline=False):
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name).strip() or "file"
    return f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name, safe="")}'


def send_file(h, p, ctype, name, inline=False, data=None):
    """파일 보내기: ETag·Last-Modified · 구간 요청 하나(이어 받기) — 크기 상한 없음 (받는 쪽 브라우저가 이어 받을 수 있게)."""
    stt = p.stat() if data is None else None
    size = len(data) if data is not None else stt.st_size
    etag = '"%x-%x"' % ((stt.st_mtime_ns if stt else 0), size) if data is None else '"%s"' % hashlib.sha256(data).hexdigest()[:16]
    hdr = {"Accept-Ranges": "bytes", "ETag": etag, "Content-Disposition": _disposition(name, inline)}
    if stt:
        hdr["Last-Modified"] = formatdate(stt.st_mtime, usegmt=True)
    start, end, code = 0, size - 1, 200
    rng = (h.headers.get("Range") or "").strip()
    ifr = (h.headers.get("If-Range") or "").strip()
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng) if rng and "," not in rng and (not ifr or ifr == etag) else None
    if m and (m.group(1) or m.group(2)):
        a, b = m.group(1), m.group(2)
        if a:
            start, end = int(a), (min(int(b), size - 1) if b else size - 1)
        else:
            start, end = max(0, size - int(b)), size - 1
        if start >= size or start > end:
            return send(h, 416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
        code = 206
        hdr["Content-Range"] = f"bytes {start}-{end}/{size}"
    try:
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(end - start + 1 if size else 0))
        for k, v in hdr.items():
            h.send_header(k, v)
        h.end_headers()
        if h.command == "HEAD" or not size:
            return
        if data is not None:
            h.wfile.write(data[start:end + 1])
            return
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                h.wfile.write(chunk)
                left -= len(chunk)
    except (ConnectionError, TimeoutError):
        pass


def outputs_route(h, svc, sess, path, q, b):
    if path == "/s/outputs":
        return send(h, 200, {"ok": True, "outputs": outputs_list(svc)}, gz=True)
    if path == "/s/dl":
        if q.get("log") == ["1"]:
            lp = core.WORK / "studio.log"
            try:
                text = lp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return send(h, 404, {"ok": False, "error": "기록 파일이 아직 없어요"})
            data = scrub_state(text).encode("utf-8")
            return send_file(h, lp, "text/plain; charset=utf-8", "studio.log", data=data)
        p = out_file((q.get("f") or [""])[0])
        if not p:
            return send(h, 404, {"ok": False, "error": "파일을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 해 주세요"})
        return _media_file(h, svc, sess, lambda: send_file(h, p, DL_TYPES[p.suffix.lower()], p.name))
    if path == "/s/preview":
        name = (q.get("f") or [""])[0]
        pv = _preview_of(name)
        if not pv:
            return send(h, 404, {"ok": False, "error": "작은 미리보기가 아직 없어요"})
        return _media_file(h, svc, sess, lambda: send_file(h, pv, "video/mp4", Path(name).stem + "_미리보기.mp4", inline=True))
    # POST /s/outputs/preview
    p = out_file(b.get("f"))
    if not p or p.suffix.lower() != ".mp4":
        return send(h, 404, {"ok": False, "error": "완성본을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 해 주세요"})
    _log_action(svc, sess, path, {"name": p.name})
    res, code = start(svc, sess, "작은 미리보기 만들기", lambda: editor.out_preview(p.name, svc.log))
    return send(h, code, res)


def start(svc, sess, label, fn):
    """이 모듈이 직접 시키는 작업: '밖에서 · <기기>' · 다운로드 엔진 스스로 고치기 없음."""
    def run():
        with core.no_self_update():
            return fn()
    ok = svc.bridge.start_job(label, run, by=f"밖에서 · {sess['name']}")
    return {"ok": bool(ok), "jobId": ok or None, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"}, (200 if ok else 409)


# ---------- 내 기기에서 올리기 (조각 · 이어 올리기 · 수 GB) ----------

UP_MAX = 100 * (1 << 30)     # 한 파일 100GB 까지
UP_PER_DEVICE, UP_TOTAL = 2, 4
UP_ACTIVE = 5 * 60           # 5분 안에 조각이 온 올리기만 '올리는 중'으로 셈 (멈춰 두거나 그만 둔 것은 곧 수에서 빠짐 · 아주 느린 망도
                             # 조각 하나(기기가 속도에 맞춰 1~16MB · 약 8초)는 5분 안에 옴 · D-237)
UP_KEEP = 7 * 86400          # 7일 안 이어 올리면 지움
UP_RECHECK = 64 << 20        # 이만큼 받을 때마다 디스크 남은 곳을 다시 봄
_UP_NAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


UP_BUSY_MSG = "PC가 다른 파일을 받는 중이에요 · 앞의 파일이 끝나면 이어서 올려요 (안 올릴 파일은 [그만두기])"


class UploadError(Exception):
    def __init__(self, msg, status=400, **more):
        super().__init__(msg)
        self.status, self.more = status, more


def upload_name(name):
    """올린 파일 이름 → 보관함에 쓸 이름 (디렉터리 없이 · 못 쓰는 글자·제어 글자 · 예약 이름 · 길이).
    맥·아이폰은 한글 이름을 풀어 쓴(NFD) 글자로 보냄 → 모아 쓴(NFC) 글자로 (탐색기에서 자모가 갈라져 보이거나 같은 제목이 둘이 되지 않게)."""
    n = unicodedata.normalize("NFC", core.clean_text(Path(str(name or "").replace("\\", "/")).name))
    n = _UP_NAME_BAD.sub("_", clean(n, 200)).strip(" .")
    stem, ext = Path(n).stem.strip(" ."), Path(n).suffix.lower()
    if not stem or not ext:
        raise UploadError("파일 이름을 읽지 못했어요 · 이름을 바꾼 뒤 다시 올려 주세요")
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    n = stem[:120].rstrip(" .") + Path(n).suffix
    editor.safe_name(n)
    return n


def disk_free(path):
    return shutil.disk_usage(str(path))


class Uploads:
    """WORK/uploads/<id>.part · <id>.json (앱을 껐다 켜도 · 주소가 바뀌어도 이어 올림). 다 받으면 제자리(보관함·편집실 가져온 파일)로 한 번에."""

    def __init__(self, svc):
        self.svc = svc
        self.lock = threading.RLock()
        self.items = None          # id → 기록 (처음 쓸 때 파일에서 읽음)
        self.locks = {}            # id → 조각 잠금 (한 올리기에 조각 하나씩)

    def root(self):
        return core.WORK / "uploads"

    def _dest(self, target):
        return core.VIDEOS if target == "videos" else editor.ASSETS

    def _load(self):
        if self.items is not None:
            return
        self.items = {}
        try:
            files = list(self.root().glob("*.json"))
        except OSError:
            files = []
        for f in files:
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                if not (isinstance(r, dict) and re.fullmatch(r"[0-9a-f]{32}", str(r.get("id"))) and f.stem == r["id"]):
                    continue
                part = self.root() / f"{r['id']}.part"
                r["received"] = part.stat().st_size if part.exists() else 0
                self.items[r["id"]] = r
            except (OSError, ValueError, KeyError):
                continue

    def _save(self, r):
        self.root().mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in r.items() if k != "received"}
        updater.write_atomic(self.root() / f"{r['id']}.json", json.dumps(keep, ensure_ascii=False))

    def _drop(self, r):
        for suf in (".part", ".json"):
            try:
                (self.root() / f"{r['id']}{suf}").unlink()
            except FileNotFoundError:
                pass
        self.items.pop(r["id"], None)
        self.locks.pop(r["id"], None)

    def view(self, r):
        return {"id": r["id"], "name": r["name"], "size": r["size"], "received": r["received"], "target": r["target"],
                "lastModified": r.get("lastModified"), "chunk": CHUNK, "chunkMax": CHUNK_MAX, "updated": int(r.get("updated") or 0)}

    def _need_free(self, extra_left):
        """다른 올리기가 아직 받을 양 + 이번에 받을 양 + 남겨 둘 여유 (2GB 또는 디스크의 3% 중 큰 것)."""
        du = disk_free(self.root() if self.root().exists() else core.WORK)
        others = sum(max(0, x["size"] - x["received"]) for x in self.items.values())
        reserve = max(2 << 30, int(du.total * 0.03))
        if self.svc.dev() and os.environ.get("FUTSAL_UPLOAD_RESERVE", "").isdigit():  # 시험 PC 용 (개발 모드에서만 · 거의 찬 개발 디스크)
            reserve = int(os.environ["FUTSAL_UPLOAD_RESERVE"])
        return du.free, extra_left + others + reserve

    def init(self, sess, b):
        name, size, lm, probe, target = b.get("name"), b.get("size"), b.get("lastModified"), b.get("probe"), b.get("target") or "videos"
        if target not in ("videos", "assets"):
            raise UploadError("어디에 올릴지 다시 골라 주세요")
        if not isinstance(name, str) or not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= UP_MAX:
            raise UploadError("파일 크기를 읽지 못했어요 (100GB 까지 올릴 수 있어요)")
        if not isinstance(probe, str) or not re.fullmatch(r"[0-9a-f]{64}", probe):
            raise UploadError("잘못된 요청이에요")
        lm = int(lm) if isinstance(lm, (int, float)) and not isinstance(lm, bool) else 0
        n = upload_name(name)
        ext = Path(n).suffix.lower()
        ok_ext = (core.VIDEO_EXTS | intake.UNUSABLE_EXTS) if target == "videos" else editor.MEDIA_EXTS
        if ext not in ok_ext:
            raise UploadError("영상 파일만 올릴 수 있어요" if target == "videos" else "영상·음악·이미지 파일만 가져올 수 있어요", 415)
        key = hashlib.sha256(f"{sess['device']}|{target}|{n}|{size}|{lm}".encode("utf-8")).hexdigest()
        with self.lock:
            self._load()
            old = next((r for r in self.items.values() if r["key"] == key), None)
            if old and old["probe"] == probe:
                old["updated"] = time.time()
                return self.view(old)
            if old:  # 같은 이름·크기·시각인데 다른 파일 → 처음부터 (옛 조각은 버림)
                self._drop(old)
            now = time.time()
            live = [r for r in self.items.values() if now - (r.get("updated") or 0) < UP_ACTIVE]
            if len([r for r in live if r["device"] == sess["device"]]) >= UP_PER_DEVICE or len(live) >= UP_TOTAL:
                raise UploadError(UP_BUSY_MSG, 429, code="queue")
            free, need = self._need_free(size)
            if free < need:
                raise UploadError(f"PC 저장 공간이 모자라요 · {round((need - free) / (1 << 30), 1)}GB 더 비워 주세요", 507, code="disk")
            r = {"v": 1, "id": secrets.token_hex(16), "key": key, "device": sess["device"], "deviceName": sess["name"], "target": target,
                 "name": n, "size": size, "lastModified": lm, "probe": probe, "created": now, "updated": now, "received": 0, "checked": 0}
            self.root().mkdir(parents=True, exist_ok=True)
            (self.root() / f"{r['id']}.part").touch()
            self._save(r)
            self.items[r["id"]] = r
            return self.view(r)

    def _get(self, sess, uid):
        with self.lock:
            self._load()
            r = self.items.get(uid) if isinstance(uid, str) else None
        if not r or r["device"] != sess["device"]:
            raise UploadError("올리던 파일을 찾지 못했어요 · 처음부터 다시 올려 주세요", 404)
        return r

    def chunk(self, h, sess, uid, offset):
        r = self._get(sess, uid)
        n = _length(h)
        want = (h.headers.get("X-FS-Sha256") or "").lower()
        if n is None or n < 1 or n > CHUNK_MAX or not re.fullmatch(r"[0-9a-f]{64}", want):
            raise UploadError("잘못된 요청이에요")
        lk = self.locks.setdefault(r["id"], threading.Lock())
        if not lk.acquire(timeout=30):
            raise UploadError("같은 파일을 두 곳에서 올리고 있어요", 409, received=r["received"])
        try:
            if offset != r["received"]:
                raise UploadError("이어 올릴 자리가 달라요", 409, received=r["received"])
            if r["received"] + n > r["size"]:
                raise UploadError("파일 크기보다 많이 보냈어요", 400, received=r["received"])
            data = h.rfile.read(n)  # 다 받은 뒤 확인하고 씀 → 끊긴 조각은 아무것도 안 씀
            if len(data) != n:
                raise UploadError("조각을 끝까지 받지 못했어요 · 다시 보낼게요", 400, received=r["received"])
            if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), want):
                raise UploadError("조각이 깨졌어요 · 다시 보낼게요", 400, received=r["received"])
            if r["received"] - r.get("checked", 0) >= UP_RECHECK:
                with self.lock:
                    free, need = self._need_free(0)
                if free < need:
                    raise UploadError("PC 저장 공간이 모자라요 · 공간을 비운 뒤 이어 올려 주세요", 507, code="disk", received=r["received"])
                r["checked"] = r["received"]
            part = self.root() / f"{r['id']}.part"
            with open(part, "r+b") as f:
                f.seek(r["received"])
                f.write(data)
                f.truncate()
                f.flush()
            r["received"] += n
            r["updated"] = time.time()
            if r["received"] - r.get("savedAt", 0) >= (256 << 20):
                r["savedAt"] = r["received"]
                try:
                    self._save(r)
                except OSError:
                    pass
            return self.view(r)
        finally:
            lk.release()

    def finish(self, sess, uid):
        r = self._get(sess, uid)
        part = self.root() / f"{r['id']}.part"
        try:
            got = part.stat().st_size
        except OSError:
            got = -1
        if got != r["size"] or r["received"] != r["size"]:
            raise UploadError("아직 다 올리지 못했어요 · 이어 올려 주세요", 409, received=max(0, got))
        dest = self._dest(r["target"])
        with self.lock:
            dest.mkdir(parents=True, exist_ok=True)
            final = editor.unique_name(dest, r["name"])
            updater.replace_retry(part, final)
            self._drop(r)
        if r["target"] == "videos":
            source.mark_footage(final.name, "local")  # 내 촬영본
            out = {"ok": True, "name": final.name, "target": "videos"}
        else:
            try:
                out = dict(editor.media_entry(final.name, "assets"), ok=True)
            except Exception:  # noqa: BLE001 — 파일은 이미 제자리 · 편집실이 다시 읽음
                out = {"ok": True, "src": "assets", "file": final.name}
        self.svc.log(f"원격 · {sess['name']} · {'보관함에 올림' if r['target'] == 'videos' else '편집실로 가져옴'} · {clean(final.name, 80)}")
        return out

    def cancel(self, sess, uid):
        r = self._get(sess, uid)
        with self.lock:
            self._drop(r)
        return {"ok": True}

    def pending(self, sess):
        with self.lock:
            self._load()
            return [self.view(r) for r in self.items.values() if r["device"] == sess["device"]]

    def active(self, within, now=None):
        """마지막 조각이 within 초 안에 온 올리기 수 — 조각 사이에도 '올리는 중' (앱 다시 시작·자동 업데이트가 끊지 않게 · D-311)."""
        now = time.time() if now is None else now
        with self.lock:
            return sum(1 for r in (self.items or {}).values() if now - (r.get("updated") or 0) < within)

    def sweep(self, now=None):
        """7일 넘게 안 이어 올린 것 · 기록 없는 조각 파일 정리."""
        now = now or time.time()
        with self.lock:
            self._load()
            for r in [r for r in self.items.values() if now - (r.get("updated") or 0) > UP_KEEP]:
                self._drop(r)
            try:
                for f in self.root().glob("*.part"):
                    if f.stem not in self.items and now - f.stat().st_mtime > 3600:
                        f.unlink()
            except OSError:
                pass

    def drop_devices(self, ids):
        """기기 연결을 끊으면 그 기기가 올리던 조각도 지움."""
        with self.lock:
            self._load()
            for r in [r for r in self.items.values() if r["device"] in ids]:
                self._drop(r)

    def count(self, device):
        with self.lock:
            if self.items is None:
                return 0
            return len([r for r in self.items.values() if r["device"] == device])

    def route(self, h, svc, sess, path, q, b):
        try:
            if path == "/s/upload/init":
                return send(h, 200, dict(self.init(sess, b), ok=True))
            if path == "/s/upload/chunk":
                try:
                    off = int((q.get("offset") or [""])[0])
                except ValueError:
                    raise UploadError("잘못된 요청이에요") from None
                return send(h, 200, dict(self.chunk(h, sess, (q.get("id") or [""])[0], off), ok=True))
            if path == "/s/upload/status":
                return send(h, 200, dict(self.view(self._get(sess, (q.get("id") or [""])[0])), ok=True))
            if path == "/s/upload/pending":
                return send(h, 200, {"ok": True, "uploads": self.pending(sess)})
            if path == "/s/upload/finish":
                return send(h, 200, self.finish(sess, b.get("id")))
            if path == "/s/upload/cancel":
                return send(h, 200, self.cancel(sess, b.get("id")))
        except UploadError as e:
            if path == "/s/upload/chunk":
                h.close_connection = True
            return send(h, e.status, dict({"ok": False, "error": str(e)}, **e.more))
        except OSError as e:
            svc.log(f"원격 · {sess['name']} · 올리기 중 문제가 생겼어요 · {type(e).__name__}")
            return send(h, 500, {"ok": False, "error": "PC에 저장하지 못했어요 · 잠시 뒤 다시 해 주세요"})
        return send(h, 404, {"ok": False, "error": "없는 주소예요"})
