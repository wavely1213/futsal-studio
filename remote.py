"""원격 접속 '휴대폰으로 보기' — 원격 경계 (D-024).

PC 에서 하는 작업을 휴대폰(https://mulgyeol.kr/futsal)에서 보고, 정해진 몇 가지 일을 시키는 곳. 바깥(터널)에서 온 요청은 이 모듈만 받는다.
  - 원격 리스너: 켜 둔 동안만 127.0.0.1:<임의 포트> 에 따로 뜨는 작은 서버(/r/* 만). 화면용 로컬 서버(8765)는 터널 뒤에 두지 않는다.
  - 확인 순서: Host(remote.futsal.invalid) → 메서드 → 요청 수 제한 → CORS·Origin·JSON → 서명/표(ticket) → 허용 동작 목록 → 파일 이름·허용 폴더.
  - 짝짓기: PC 가 만든 10분짜리 한 번 쓰는 코드 → 휴대폰이 기기 열쇠 2개(서명용·비콘용)를 받음 → ~/.futsal-studio/remote.json.
  - 비콘: 빠른 터널 주소는 켤 때마다 바뀜 → 지금 주소를 기기마다 AES-GCM 으로 잠가 ntfy 주제에 올림 (휴대폰 페이지가 찾아옴).
  - 알림: 정해진 문장만 ntfy 알림 주제로 (파일 이름·제목·경로·오류 글 없음).
app 을 import 하지 않는다 — app 이 Bridge 로 기록·작업 시작 같은 기능을 넘겨준다. 암호 부품(pycryptodomex)은 함수 안에서만 불러온다
(없으면 원격 접속만 켜지지 않음 · 앱과 업데이트 import 확인은 그대로).
"""
import base64
import collections
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import traceback
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
import style
import tunnel
import updater

API = 1
SENTINEL = "remote.futsal.invalid"  # cloudflared 가 원격 리스너로 보낼 때 쓰는 Host (.invalid 는 어떤 브라우저도 만들 수 없음)
SITE = "https://mulgyeol.kr/futsal"
ORIGINS = ("https://mulgyeol.kr", "https://www.mulgyeol.kr")
NTFY_DEFAULT = "https://ntfy.sh"
MAX_DEVICES = 5
DEVICE_TTL = 90 * 86400      # 90일 동안 안 쓴 휴대폰은 저절로 끊음
CODE_TTL = 600               # 연결 코드는 10분
CODE_FAILS = 5               # 한 코드에 틀린 시도 5번이면 그 코드는 버림
PAIR_FAILS_HOUR = 20         # 한 시간에 짝짓기 실패가 이만큼이면 PC 에서 새 코드를 만들 때까지 짝짓기 막음
PAIR_SALT, PAIR_ITER = b"futsal-remote/pair/v1", 200_000
SKEW = 300                   # 서명 시각 허용 차이(초)
NONCE_TTL = 600
NONCE_MAX = 2000             # 기기마다 기억하는 nonce 수
TICKET_TTL, TICKET_MAX = 7200, 4000
BODY_MAX = 64 * 1024
MEDIA_CHUNK = 4 << 20        # 구간 요청 한 번에 내주는 최대 크기
CONCURRENCY = 24
MEDIA_PER_DEVICE, MEDIA_TOTAL = 6, 10
ACTION_GAP = 2.0             # 기기마다 동작 요청 간격(초)
AUTH_FAIL_MAX, AUTH_FAIL_WINDOW, LOCKOUT = 10, 600, 900
LASTSEEN_SAVE = 300          # 마지막 사용 시각은 5분마다만 파일에 씀
LOG_TAIL = 200
BEACON_GAP, HEARTBEAT = 60, 1200
DAILY_BUDGET, BUDGET_SOFT = 200, 180   # ntfy.sh 무료는 하루 약 250개 · 180개를 넘으면 상태 바뀜·확인 필요만
RETRY_DELAYS = (2.0, 5.0)              # 보내기 실패 → 이만큼 쉬고 다시 (모두 3번)
AUTO_OFF_CHOICES = (0, 1, 3, 12, 24)
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
              "학습용 영상 지우기", "배운 영상 파일 지우기", "보관함으로 되돌리기", "학습용으로 옮기기"}
OFF_REASONS = {"app": "앱을 껐어요", "user": "원격 접속을 껐어요", "idle": "오래 쓰지 않아서 껐어요", "error": "연결이 끊겼어요"}
NOTE_TEXT = {
    "paired": "새 휴대폰이 연결됐어요",
    "test": "알림 시험이에요 · 잘 받았다면 준비 끝!",
    "tunnel": "확인이 필요해요 · 원격 연결이 자꾸 끊겨요",
    "blocked": "확인이 필요해요 · YouTube가 막았어요",
    "missed": "확인이 필요해요 · 받지 못한 영상이 있어요",
}
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‎‏‪-‮⁦-⁩]")


class CryptoMissing(RuntimeError):
    pass


class AuthError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class PairError(Exception):
    pass


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


def scrub(s):
    """휴대폰에 보내는 기록·오류 글: 사용자 폴더 경로(Windows 사용자 이름이 들어감)를 '~' 로, 제어 글자는 뺌."""
    s = str(s if s is not None else "")
    home = str(Path.home())
    if len(home) > 3:
        s = s.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return _CTRL.sub("", s.replace("\r", ""))


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
    """코드 → (만남 주제, 32바이트 열쇠). 페이지(proto.js)와 같은 계산."""
    dk = hashlib.pbkdf2_hmac("sha256", code.encode("ascii"), PAIR_SALT, PAIR_ITER, 48)
    return "fsp" + dk[:12].hex(), dk[16:48]


def canonical(method, path, ts, nonce, body):
    return f"FSR1\n{method}\n{path}\n{ts}\n{nonce}\n{hashlib.sha256(body or b'').hexdigest()}".encode("utf-8")


def sign(key, method, path, ts, nonce, body=b""):
    return _b64u(hmac.new(key, canonical(method, path, ts, nonce, body), hashlib.sha256).digest())


# ---------- 연결 코드 ----------

def new_code():
    return "".join(secrets.choice(CROCK) for _ in range(10))


def fmt_code(c):
    return f"{c[:4]}-{c[4:8]}-{c[8:]}"


def norm_code(s):
    """사람이 친 코드 → 10글자 (대문자, -·띄어쓰기 빼고, O→0, I·L→1) · 틀린 꼴이면 None."""
    s = re.sub(r"[\s\-]", "", str(s or "")).upper().replace("O", "0").replace("I", "1").replace("L", "1")
    return s if len(s) == 10 and all(ch in CROCK for ch in s) else None


def _code_hash(c):
    return hashlib.sha256(("futsal-pair|" + c).encode("ascii")).digest()


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
            "settings": {"autoOffHours": 12, "keepAwake": "job", "notify": {"done": True, "failed": True, "attention": True}},
            "devices": [], "topics": {"beacon": "fsb" + secrets.token_hex(12), "notify": "fsn" + secrets.token_hex(12)}, "seq": 0}


def _valid_store(d):
    try:
        ok = (d.get("v") == 1 and re.fullmatch(r"[0-9a-f]{16}", d["pc"]["id"]) and isinstance(d["devices"], list)
              and re.fullmatch(r"fsb[0-9a-f]{24}", d["topics"]["beacon"]) and re.fullmatch(r"fsn[0-9a-f]{24}", d["topics"]["notify"])
              and isinstance(d.get("seq"), int) and isinstance(d.get("settings"), dict))
        for x in d["devices"]:
            ok = ok and re.fullmatch(r"[0-9a-f]{16}", x["id"]) and len(_unb64u(x["auth"])) == 32 and len(_unb64u(x["beacon"])) == 32
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
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.data, ensure_ascii=False, indent=1))
            try:
                os.chmod(tmp, 0o600)  # Windows 는 읽기 전용 표시만 바뀜 → 무시
            except OSError:
                pass
            updater._replace(tmp, self.path)

    def device(self, did):
        return next((x for x in self.data["devices"] if x["id"] == did), None)

    def add_device(self, name):
        with self.lock:
            now = int(self.clock())
            dev = {"id": secrets.token_hex(8), "name": clean(name, 40) or "휴대폰", "created": now, "lastSeen": now,
                   "auth": _b64u(secrets.token_bytes(32)), "beacon": _b64u(secrets.token_bytes(32))}
            self.data["devices"].append(dev)
            self.save()
            return dev

    def remove(self, ids):
        with self.lock:
            before = len(self.data["devices"])
            self.data["devices"] = [x for x in self.data["devices"] if x["id"] not in ids]
            if len(self.data["devices"]) != before:
                self.save()
            return before - len(self.data["devices"])

    def rotate_topics(self):
        with self.lock:
            old = dict(self.data["topics"])
            self.data["topics"] = {"beacon": "fsb" + secrets.token_hex(12), "notify": "fsn" + secrets.token_hex(12)}
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

_AUTH_RE = re.compile(r"FSR1 ([0-9a-f]{16})\.(\d{1,12})\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{43})")


class Auth:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.nonces = {}
        self.lock = threading.Lock()

    def verify(self, header, method, path, body):
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
        want = sign(_unb64u(dev["auth"]), method, path, ts, nonce, body)
        if not hmac.compare_digest(want, sig):
            raise AuthError("bad_sig", "연결 정보가 맞지 않아요")
        with self.lock:
            seen = self.nonces.setdefault(did, collections.OrderedDict())
            while seen and next(iter(seen.values())) < now:
                seen.popitem(last=False)
            if nonce in seen:
                raise AuthError("replay", "같은 요청이 두 번 왔어요")
            _lru_put(seen, nonce, now + NONCE_TTL, NONCE_MAX)
        return dev

    def forget(self, ids):
        with self.lock:
            for i in ids:
                self.nonces.pop(i, None)


# ---------- 짝짓기 ----------

class Pairing:
    """연결 코드 하나 (10분 · 한 번 · 5번 틀리면 버림). 코드 글은 PC 화면에 보여 주려고 메모리에만 (기록·파일에 안 남김)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.cur = None
        self.fails = collections.deque()
        self.blocked = False

    def create(self):
        with self.lock:
            code = new_code()
            topic, key = derive_pair(code)
            self.cur = {"code": code, "hash": _code_hash(code), "exp": self.clock() + CODE_TTL, "fails": 0, "topic": topic, "key": key}
            self.blocked = False
            return dict(self.cur)

    def cancel(self):
        with self.lock:
            self.cur = None

    def live(self):
        with self.lock:
            if self.cur and self.clock() > self.cur["exp"]:
                self.cur = None
            return dict(self.cur) if self.cur else None

    def check(self, code_in):
        """맞으면 코드를 써 버리고 True · 아니면 PairError."""
        msg = "코드가 맞지 않거나 시간이 지났어요 · PC 화면의 새 코드로 다시 해 주세요"
        with self.lock:
            now = self.clock()
            while self.fails and now - self.fails[0] > 3600:
                self.fails.popleft()
            if self.blocked:
                raise PairError("지금은 연결할 수 없어요 · PC에서 새 코드를 만들어 주세요")
            cur, c = self.cur, norm_code(code_in)
            if cur and now > cur["exp"]:
                self.cur = cur = None
            if not cur or c is None or not hmac.compare_digest(_code_hash(c), cur["hash"]):
                self.fails.append(now)
                if cur:
                    cur["fails"] += 1
                    if cur["fails"] >= CODE_FAILS:
                        self.cur = None
                if len(self.fails) >= PAIR_FAILS_HOUR:
                    self.blocked, self.cur = True, None
                raise PairError(msg)
            self.cur = None
            return True


# ---------- 미디어 표 (ticket) ----------

class Tickets:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = collections.OrderedDict()
        self.rev = {}

    def issue(self, device, kind, path):
        now = self.clock()
        with self.lock:
            k = (device, kind, str(path))
            t = self.rev.get(k)
            if t and t in self.items and self.items[t][3] - now > 1800:  # 30분 넘게 남았으면 같은 표 (목록을 자주 새로 해도 표가 쌓이지 않게)
                return t
            t = secrets.token_urlsafe(16)
            _lru_put(self.items, t, (device, kind, str(path), now + TICKET_TTL), TICKET_MAX)
            self.rev[k] = t
            if len(self.rev) > TICKET_MAX * 2:
                self.rev = {kk: vv for kk, vv in self.rev.items() if vv in self.items}
            return t

    def get(self, t):
        with self.lock:
            it = self.items.get(t)
            if not it or it[3] < self.clock():
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

    def notify(self, kind, text, prio=3, tags="bell"):
        self.q.put(("notify", kind, text, prio, tags))

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
                    _, kind, text, prio, tags = item
                    if self._budget(kind in ("attention", "failed", "paired")):
                        self._post_notify(text, prio, tags)
                elif item[0] == "raw":
                    if self._budget(True):
                        self._post(f"{self.svc.ntfy()}/{item[1]}", item[2].encode("utf-8"), {"Content-Type": "text/plain"})
            except Exception:  # noqa: BLE001 — 보내기 실패가 앱을 멈추지 않게
                traceback.print_exc()

    def _send_beacon(self, important, moved, topic):
        if not self._budget(important or bool(moved)):
            return
        self.last_beacon = self.svc.clock()
        body = self.svc.beacon_body(moved=moved)
        if body:
            self._post(f"{self.svc.ntfy()}/{topic or self.svc.store.data['topics']['beacon']}", body.encode("ascii"),
                       {"Content-Type": "text/plain"})

    def _post_notify(self, text, prio, tags):
        msg = {"topic": self.svc.store.data["topics"]["notify"], "title": "풋살 스튜디오", "message": text,
               "tags": [tags], "priority": prio, "click": self.svc.site()}
        self._post(self.svc.ntfy() + "/", json.dumps(msg, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"})

    def _post(self, url, data, headers):
        for k in range(len(RETRY_DELAYS) + 1):
            try:
                req = urllib.request.Request(url, data=data, method="POST", headers={**updater.UA, **headers})
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read(256)
                return True
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429:
                    break
            except updater.NET_ERRORS:
                pass
            if k < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[k])
        if self.svc.clock() - self.warned > 3600:
            self.warned = self.svc.clock()
            self.svc.log("  휴대폰 알림을 보내지 못했어요 · 인터넷 연결을 확인해 주세요")
        return False

    def post_now(self, url, data, headers, timeout=2.0):
        """앱을 끌 때 마지막 비콘: 기다리지 않고 한 번만."""
        try:
            req = urllib.request.Request(url, data=data, method="POST", headers={**updater.UA, **headers})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read(256)
            return True
        except Exception:  # noqa: BLE001
            return False

    def stop(self):
        self.q.put(("stop",))


# ---------- 원격 서비스 ----------

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
        self._stopping = threading.Event()
        self._job_seen = None
        self._lockout_logged = 0.0
        self._started = False
        self._off_reason = "user"

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
        return self.listener.server_address[1] if self.listener else None

    # ----- PC 화면 (/api/remote*) -----
    def brief(self):
        return {"state": self.state, "devices": len(self.store.data["devices"]) if self.store else 0}

    def pc_status(self):
        d = self.store.data
        p = self.pairing.live()
        pair = None
        if p and self.state == "on":
            pair = {"code": fmt_code(p["code"]), "link": self.pair_link(p["code"]), "expiresAt": int(p["exp"])}
        return {"state": self.state, "error": self.error, "prep": self.prep, "dev": _dev(),
                "devices": [{"id": x["id"], "name": x["name"], "created": x["created"], "lastSeen": x["lastSeen"]} for x in d["devices"]],
                "maxDevices": MAX_DEVICES, "pair": pair, "settings": d["settings"], "pc": d["pc"],
                "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]}, "site": self.site(),
                "autoOffAt": self._auto_off_at(), "crypto": crypto_ok()}

    def pair_link(self, code):
        hint = ""
        if self.url:
            m = re.fullmatch(r"https://([a-z0-9-]{1,63})\.trycloudflare\.com", self.url)
            hint = m.group(1) if m else (urlparse(self.url).netloc if _dev() else "")
        return f"{self.site()}#pair={code}" + (f"&u={hint}" if hint else "")

    def turn_on(self):
        with self.lock:
            if self.state in ("preparing", "starting", "on", "restarting"):
                return True
            if not crypto_ok():
                self.state, self.error = "error", MISSING_MSG
                return False
            self._teardown()  # 실패한 앞의 시도(터널·리스너)가 남아 있으면 정리
            self.state, self.error, self.prep = "preparing", None, 0
            self._stopping.clear()
        threading.Thread(target=self._start, daemon=True, name="remote-start").start()
        return True

    def _start(self):
        try:
            exe = None
            if not _dev():
                exe = tunnel.ensure(progress=self._prep_progress, cancel=self._stopping.is_set)
            if self._stopping.is_set():
                return
            self._open_listener()
            self.store.set(enabled=True)
            self.last_auth = self.clock()
            if _dev():
                self._tunnel_event("on", url=f"http://127.0.0.1:{self.port()}")
            else:
                with self.lock:
                    self.state, self.prep = "starting", None
                self.tun = tunnel.Tunnel(exe, self.port(), self._tunnel_event)
                self.tun.start()
            self.log("원격 접속을 켰어요")
        except Exception as e:  # noqa: BLE001
            if self._stopping.is_set():  # 받는 중에 [끄기]를 누름
                return
            if not isinstance(e, (RuntimeError, OSError)):
                traceback.print_exc()
            self._close_listener()
            with self.lock:
                self.state, self.error, self.prep = "error", scrub(e) or "원격 접속을 켜지 못했어요", None
            self.log(f"원격 접속을 켜지 못했어요 · {scrub(e)}")

    def _prep_progress(self, got, total):
        self.prep = min(99, int(got * 100 / total)) if total else None

    def _tunnel_event(self, kind, url=None, error=None):
        """tunnel.Tunnel → 상태 바뀜. 터널 주소(url)는 기록에 남기지 않는다."""
        with self.lock:
            if self._stopping.is_set():
                return
            if kind == "on":
                changed = url != self.url
                self.state, self.error, self.url = "on", None, url
            elif kind == "restarting":
                self.state, self.url = "restarting", None
                return
            elif kind == "error":
                self.state, self.error, self.url = "error", error or "연결이 끊겼어요", None
                self._off_reason = "error"
            elif kind == "attention":
                self._note("attention", NOTE_TEXT["tunnel"])
                return
            else:
                return
        if kind == "on" and changed:
            self.pub.beacon(important=True)
            self._publish_pair()
        if kind == "error":
            self.log(f"원격 접속 · {error}")
            self.pub.beacon(important=True)

    def _teardown(self):
        with self.lock:
            self._stopping.set()
            tun, self.tun = self.tun, None
        if tun:
            tun.stop()
        self._close_listener()

    def turn_off(self, why="user", keep_enabled=False):
        was = self.state
        self._off_reason = why
        self._teardown()
        self.tickets.drop()
        self.pairing.cancel()
        with self.lock:
            self.state, self.error, self.prep, self.url = "off", None, None, None
        if not keep_enabled:
            self.store.set(enabled=False)
        if was != "off":
            self.pub.beacon(important=True)
            self.log(f"원격 접속을 껐어요 · {OFF_REASONS.get(why, why)}")
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

    def revoke(self, ids=None, everyone=False, why="PC에서"):
        d = self.store.data
        gone = [x for x in d["devices"] if everyone or x["id"] in (ids or [])]
        if not gone:
            return 0
        old = self.store.rotate_topics()
        self.store.remove({x["id"] for x in gone})
        self.tickets.drop({x["id"] for x in gone})
        self.auth.forget([x["id"] for x in gone])
        if everyone:
            self.pairing.cancel()
        if self.store.data["devices"]:  # 남은 휴대폰에만 새 주제를 옛 주제로 알려 줌 (끊은 휴대폰은 열쇠가 없어 못 읽음)
            self.pub.beacon(important=True, moved=dict(self.store.data["topics"]), topic=old["beacon"])
        self.pub.beacon(important=True)
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

    def shutdown(self, timeout=2.0):
        """앱을 끌 때: 마지막 비콘(앱을 껐어요)을 한 번 보내고 터널을 끔 · 다음에 켜면 다시 켜지도록 enabled 는 그대로."""
        if not self.store or self.state == "off":
            return
        try:
            self._off_reason = "app"
            body = self.beacon_body(state="off", reason=OFF_REASONS["app"])
            with self.lock:
                self._stopping.set()
                tun, self.tun = self.tun, None
            if body:
                self.pub.post_now(f"{self.ntfy()}/{self.store.data['topics']['beacon']}", body.encode("ascii"), {"Content-Type": "text/plain"}, timeout)
            if tun:
                tun.stop(timeout=timeout)
            self._close_listener()
            self.state = "off"
        except Exception:  # noqa: BLE001 — 끄는 길은 막히면 안 됨
            traceback.print_exc()

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
        ok, err, blocked = True, None, False
        if error:
            ok, err = False, scrub(error)
        elif isinstance(result, dict) and result.get("ok") is False:
            ok, err, blocked = False, scrub(result.get("error")), bool(result.get("blocked"))
        self.last = {"name": name, "ok": ok, "error": err, "blocked": blocked, "endedAt": int(self.clock()), "by": by}
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
        if blocked or (name == "보관함에 담기" and isinstance(result, list) and result):
            if n.get("attention"):
                self._note("attention", NOTE_TEXT["blocked" if blocked else "missed"])
        elif not ok:
            if n.get("failed") and (by or secs >= 5):
                self.pub.notify("failed", f"작업이 멈췄어요 · {label} — 휴대폰에서 자세히 보기", 4, "warning")
        elif n.get("done") and (by or secs >= 60):
            self.pub.notify("done", f"작업이 끝났어요 · {label} ({_dur_text(secs)})", 3, "white_check_mark")

    def _note(self, kind, text):
        if self.pub and self.store.data["settings"]["notify"].get("attention", True):
            self.pub.notify(kind, text, 4, "warning")

    # ----- 뒤에서: 하트비트·자동 끄기·오래된 기기 -----
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
                traceback.print_exc()

    def tick(self, last_hb=None):
        """5초마다 (시험은 직접 부름): 작업이 바뀌면 비콘 · 20분 하트비트 · 오래 안 쓰면 끄기 · 90일 안 쓴 기기 끊기."""
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
        old = self.store.expired()
        if old:
            self.revoke(old, why="90일 동안 안 씀")

    def _keep_awake(self):
        """Windows: 원격이 켜져 있고 (작업 중이거나 '항상'이면) 절전 막기. SetThreadExecutionState 는 스레드마다라 한 스레드에서만."""
        import ctypes
        try:
            f = ctypes.windll.kernel32.SetThreadExecutionState
        except AttributeError:
            return
        f.restype, f.argtypes = ctypes.c_uint, [ctypes.c_uint]
        on = False
        while True:
            try:
                want = self.state == "on" and (bool((self.bridge.job() or {}).get("name")) or self.store.data["settings"].get("keepAwake") == "always")
                if want != on:
                    f(core.ES_CONTINUOUS | core.ES_SYSTEM_REQUIRED if want else core.ES_CONTINUOUS)
                    on = want
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)

    # ----- 리스너 -----
    def _open_listener(self):
        if self.listener:
            return
        port = int(os.environ.get("FUTSAL_REMOTE_PORT") or 0)
        srv = None
        for _ in range(20):
            try:
                srv = RemoteServer(("127.0.0.1", port), self)
                break
            except OSError:
                if not port:
                    raise
                time.sleep(0.25)
        if srv is None:
            raise RuntimeError("원격 접속용 포트를 열지 못했어요 · 잠시 뒤 다시 켜 주세요")
        self.listener = srv
        threading.Thread(target=srv.serve_forever, daemon=True, name="remote-listener").start()

    def _close_listener(self):
        srv, self.listener = self.listener, None
        if srv:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:  # noqa: BLE001
                pass

    # ----- 휴대폰 요청 처리 -----
    def touch(self, dev):
        self.last_auth = self.clock()
        self.store.touch(dev)

    def r_ping(self):
        return {"api": API, "pc": self.store.data["pc"], "time": int(self.clock())}

    def r_pair(self, b):
        if len(self.store.data["devices"]) >= MAX_DEVICES:
            raise PairError(f"휴대폰은 {MAX_DEVICES}대까지 연결할 수 있어요 · PC에서 안 쓰는 휴대폰을 먼저 끊어 주세요")
        self.pairing.check(b.get("code"))
        dev = self.store.add_device(b.get("name"))
        self.last_auth = self.clock()
        d = self.store.data
        self.log(f"휴대폰이 연결됐어요 · {clean(dev['name'], 40)}")
        self._note("paired", NOTE_TEXT["paired"])
        self.pub.beacon(important=True)
        return {"api": API, "pc": d["pc"], "device": {"id": dev["id"], "name": dev["name"]},
                "keys": {"auth": dev["auth"], "beacon": dev["beacon"]},
                "beacon": {"server": self.ntfy(), "topic": d["topics"]["beacon"]},
                "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]}, "time": int(self.clock())}

    def r_status(self, dev, since):
        snap = self.bridge.job()
        job = None
        if snap.get("name"):
            pr = snap.get("progress") or {}
            job = {"name": snap["name"], "by": snap.get("by"), "startedAt": int(snap["t0"]) if snap.get("t0") else None,
                   "progress": {"label": scrub(pr.get("label") or ""), "item": scrub(pr.get("item") or ""), "pct": pr.get("pct"),
                                "detail": scrub(pr.get("detail") or ""), "eta": pr.get("eta")}}
        new, total = self.bridge.logs(max(0, since))
        if since <= 0 or since > total:  # 처음이거나 앱이 다시 켜져 번호가 줄었음 → 마지막 200줄만
            new, total = self.bridge.logs(max(0, total - LOG_TAIL))
        new = new[-LOG_TAIL:]
        d = self.store.data
        return {"api": API, "ver": self.bridge.version, "time": int(self.clock()), "pc": d["pc"], "job": job, "last": self.last,
                "log": [scrub(x) for x in new], "logTotal": total,
                "remote": {"autoOffAt": self._auto_off_at(), "devices": len(d["devices"]), "device": {"id": dev["id"], "name": dev["name"]}},
                "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]},
                "beacon": {"server": self.ntfy(), "topic": d["topics"]["beacon"]}}

    def _projects(self, name):
        try:
            p = editor._ppath(name)
            if not p.exists():
                return []
            proj = json.loads(p.read_text(encoding="utf-8"))
            return [{"id": str(q.get("id")), "name": clean(q.get("name"), 80), "format": "shorts" if q.get("format") == "shorts" else "long"}
                    for q in proj.get("sequences") or [] if isinstance(q, dict) and q.get("id")]
        except (OSError, ValueError, AttributeError):
            return []

    def r_library(self, dev):
        vids = source.annotate(core.local_videos())
        out = []
        for v in vids:
            s = v.get("source") or {}
            out.append({"name": v["name"], "title": source._title_of(v["name"]), "sizeMb": v.get("size_mb"), "analyzed": bool(v.get("analyzed")),
                        "source": {"kind": s.get("kind") or "unknown", "channel": s.get("channel") or ""},
                        "poster": "/r/m/" + self.tickets.issue(dev["id"], "poster", v["name"]), "sequences": self._projects(v["name"])})
        return {"videos": out}

    def r_outputs(self, dev):
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
                    "url": "/r/m/" + self.tickets.issue(dev["id"], "file", p.resolve())}
            if kind == "video":
                pv = editor.out_preview_path(p.name)
                fresh = pv.is_file() and pv.stat().st_mtime >= st.st_mtime  # 같은 이름으로 다시 만든 완성본이면 옛 미리보기는 안 씀
                item["preview"] = {"exists": fresh, "url": "/r/m/" + self.tickets.issue(dev["id"], "file", pv.resolve()) if fresh else None}
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
        if action not in ACTIONS:
            raise ActionError("할 수 없는 동작이에요")
        if action == "cancel":
            return self.r_cancel(dev)
        return ACTIONS[action](self, dev, args, b.get("confirm"))

    def _go(self, dev, label, fn, target):
        if not self.limiter.action_ok(dev["id"]):  # 작업을 시작하는 요청만 2초에 한 번 (확인 묻기는 세지 않음)
            raise ActionError("조금 뒤에 다시 눌러 주세요", 429)
        who = clean(dev["name"], 40)
        ok = self.bridge.start_job(label, fn, by=f"휴대폰 · {who}")
        if not ok:
            raise ActionError(BUSY_MSG, 409)
        self.log(f"원격 · {who} · {label}" + (f" · {clean(target, 80)}" if target else ""))
        return {"ok": True, "job": label}

    def r_cancel(self, dev):
        editor.cancel_export()
        self.log(f"원격 · {clean(dev['name'], 40)} · 멈추기")
        return {"ok": True}

    def r_forget(self, dev):
        self.revoke([dev["id"]], why="휴대폰에서")
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
        failed = core.download([vid], log, None)  # 원격에서는 쿠키를 쓰지 않음 (D-009) — 막히면 PC 에서 '크롬 로그인 정보로 받기'
        if failed:
            log("  PC에서 '크롬 로그인 정보로 받기'로 다시 받아 주세요")
        return failed
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
    return svc._go(dev, "내보내기", lambda: editor.export_saved(n, seq, preset, svc.bridge.log), n)


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

    def __init__(self, addr, svc):
        self.svc = svc
        super().__init__(addr, RemoteHandler)


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
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
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
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:  # noqa: BLE001 — 오류 글·추적은 응답에 넣지 않음
            traceback.print_exc()
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
        # 5. 확인 (표 · 서명) → 6. 허용 목록
        if media:
            return self._media(svc, path[5:])
        if path == "/r/ping" and method == "GET":
            return self._json(200, svc.r_ping())
        try:
            b = json.loads(body or b"{}")
            if not isinstance(b, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "잘못된 요청이에요"})
        if path == "/r/pair" and method == "POST":
            try:
                return self._json(200, svc.r_pair(b))
            except PairError as e:
                self._fail(svc, ip)
                return self._json(403, {"error": str(e)})
        try:
            dev = svc.auth.verify(self.headers.get("Authorization"), method, self.path, body)
        except AuthError as e:
            self._fail(svc, ip)
            return self._json(401, {"error": str(e), "code": e.code, "time": int(svc.clock())})
        svc.touch(dev)
        routes = {("GET", "/r/status"): lambda: svc.r_status(dev, _int((parse_qs(u.query).get("since") or ["0"])[0])),
                  ("GET", "/r/library"): lambda: svc.r_library(dev), ("GET", "/r/outputs"): lambda: svc.r_outputs(dev),
                  ("GET", "/r/choices"): lambda: svc.r_choices(dev), ("POST", "/r/action"): lambda: svc.r_action(dev, b),
                  ("POST", "/r/cancel"): lambda: svc.r_cancel(dev), ("POST", "/r/forget"): lambda: svc.r_forget(dev)}
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

    def _media(self, svc, tid):
        it = svc.tickets.get(tid) if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", tid or "") else None
        if not it:
            return self._json(404, {"error": "다시 불러와 주세요"})
        device, kind, ref, _ = it
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


# ---------- app 이 부르는 곳 ----------

SVC = Service()


def init(bridge):
    SVC.init(bridge)
    if SVC.store.data.get("enabled"):
        SVC.turn_on()  # 켜 둔 채로 앱을 껐거나 업데이트로 다시 켜졌으면 이어서 켬
    return SVC
