"""유튜브에 바로 올리기 (7단계 '올리기' · D-045) — 올리기 키트의 제목·설명(챕터 포함)·태그와 썸네일·자막(.srt)·재생목록을
사용자 본인 채널에 그대로 올림. 카테고리 스포츠(17) · 언어 한국어 · 아동용은 늘 직접 정해서 보냄.

- 연결: 사용자가 만든 Google Cloud OAuth 클라이언트(데스크톱 앱)로 로그인 (youtube_api.LoginFlow · 범위 youtube.force-ssl 하나).
  client.bin·token.bin 은 ~/.futsal-studio/youtube/ (사용자 폴더 · 앱 폴더·config.json 밖 · Windows 는 DPAPI · 기록에 남기지 않음).
- 올리기는 작업 하나(start_job · JOB_NAME): 재개 가능한 업로드 → 썸네일 → 자막 → 재생목록 → 상태 확인(잠긴 비공개).
  올리던 세션은 WORK/youtube/uploads/<열쇠>.json(비밀 아닌 것만) + 세션 주소는 ~/.futsal-studio/youtube/sessions/<열쇠>.bin
  (DPAPI·600 · 작업 폴더가 OneDrive·NAS 여도 주소를 바꿔 넣을 수 없게) → 멈추기·인터넷 끊김·앱을 껐다 켜도 [이어 올리기].
  videoId 를 받으면 바로 기록(history.json)에 남김 → 뒤 단계가 실패해도 [마저 하기](JOB_FINISH).
- 할당량: 이 앱이 쓴 양을 Google 하루(미국 태평양 시각 자정 · tzdata 없이 계산)마다 셈 (quota.json · Google 콘솔 숫자와 다를 수 있음).
- 기록(log)에는 파일 이름·종류·videoId·% 만. 제목·토큰·코드·보안 비밀번호·세션 주소·이메일은 남기지 않음.
- 키트 파일(*_올리기.json) 형식은 바꾸지 않음 (DEVELOPMENT_RULES 6) · 상태는 모두 새 파일에.
- 실패 카드: 결과의 fail = trouble.youtube_card(…) (할당량·연결 끊김·권한·썸네일 막힘·인터넷·서버 → 정해진 문장 + 할 일 ·
  주소·토큰·Google 원문 없음) → 7단계 화면 카드 · 휴대폰은 remote.YOUTUBE_MSG 의 정해진 문장 (D-048).
  작업 밖으로 나온 예외는 app.start_job(ctx={"youtube": True}) 이 trouble.explain(youtube=True) 로 같은 카드를 만든다.
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core
import editor
import trouble
import updater
import upload
import youtube_api as yt

JOB_NAME = "유튜브에 올리기"
JOB_FINISH = "유튜브 마무리"
CATEGORY, LANG = "17", "ko"   # 스포츠 · 한국어
PRIVACY = ("private", "unlisted", "public")
PRIVACY_KO = {"private": "비공개", "unlisted": "일부 공개", "public": "공개", "scheduled": "예약 공개"}
# 할당량 (developers.google.com/youtube/v3/determine_quota_cost · revision history 2025-12-04·2026-06-01 · 2026-10-07 확인):
# videos.insert 는 따로 '하루 100번'(한 번에 1) · 나머지는 '하루 10,000 단위'를 함께 씀 · 미국 태평양 시각 자정에 다시 채워짐 (D-045)
COST = {"videos.insert": ("uploads", 1), "thumbnails.set": ("units", 50), "captions.insert": ("units", 400),
        "playlistItems.insert": ("units", 50), "playlists.insert": ("units", 50), "playlists.list": ("units", 1),
        "channels.list": ("units", 1), "videos.list": ("units", 1)}
LIMITS = {"uploads": 100, "units": 10000}
TITLE_MAX, DESC_BYTES, TAGS_MAX, HASHTAG_MAX = 100, 5000, 500, 15   # 설명은 '5000바이트' (videos 문서) — 한글은 한 글자 3바이트
SHORTS_MAX, VERIFY_LEN = 180, 900          # 쇼츠: 세로·정사각형 3분까지 (2024-10-15~) · 15분 넘으면 채널 인증 필요
THUMB_API_MAX = 50 * 1024 * 1024           # thumbnails.set 50MB (2026-09-14~ · BR-006 의 2MB 는 스튜디오 기준, I-057)
SESSION_DAYS = 6                           # Google 세션은 약 1주 → 6일 넘으면 새로
HISTORY_MAX = 500
SCHEDULE_MIN, SCHEDULE_MAX = 15 * 60, 365 * 86400   # 예약 공개: 이 앱의 여유(15분 · Google 규칙은 '지금보다 뒤')
POST_RETRY = (5, 15, 45)                   # 올린 직후 썸네일·자막·재생목록이 5xx·videoNotFound 면 기다렸다 다시 (처리 중일 수 있음)
FRESH_SECS = 600                           # 올린 지 이만큼 안이면 '처리 중'으로 봄
CHECK_UNTIL = 30 * 60                      # 올린 뒤 이만큼은 화면이 처리 상태를 다시 물음 (1단위씩)
HOME = core.ENGINE_HOME / "youtube"
GUIDE_URLS = {
    "console": "https://console.cloud.google.com/",
    "project": "https://console.cloud.google.com/projectcreate",
    "api": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    "branding": "https://console.cloud.google.com/auth/overview",
    "audience": "https://console.cloud.google.com/auth/audience",
    "clients": "https://console.cloud.google.com/auth/clients",
    "quotas": "https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas",
    "audit": "https://support.google.com/youtube/contact/yt_api_form",
    "verify": "https://www.youtube.com/verify",
    "permissions": "https://myaccount.google.com/connections",
    "studio": "https://studio.youtube.com",
}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
KEY_RE = re.compile(r"^[0-9a-f]{16}$")
PLAYLIST_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
CHANNEL_RE = re.compile(r"/channel/(UC[0-9A-Za-z_-]{22})")
HANDLE_RE = re.compile(r"/(@[^/?#\s]+)")
STEP_ORDER = ("thumbnail", "captions", "playlist")
STEP_OP = {"thumbnail": "thumbnails.set", "captions": "captions.insert", "playlist": "playlistItems.insert"}
LOCKED_MSG = ("Google이 이 프로젝트를 아직 확인(감사)하지 않아 영상이 '비공개(잠김)'로 올라갔어요 · 잠긴 영상은 공개로 바꿀 수 없어요 · "
              "감사를 받은 뒤 다시 올려 주세요 (안내 9단계)")
LENGTH_MSG = "15분이 넘는 영상은 채널 인증(전화번호 확인)이 있어야 올라가요 · youtube.com/verify 에서 인증한 뒤 다시 올려 주세요"
PAST_SCHEDULE = "예약 공개 시각이 지났어요 · 새 시각을 골라 [처음부터 다시]로 올려 주세요"
RELOGIN_STEP = "유튜브 연결이 끊겼어요 · 위에서 [다시 연결하기]를 누른 뒤 [마저 하기]를 눌러 주세요"
TESTING_HINT = "Google Cloud 앱이 '테스트' 상태라 7일마다 끊겨요 · 안내 5단계의 [앱 게시]를 하면 더 끊기지 않아요"
# privacy 는 기억하지 않음 (늘 비공개로 시작 · 예전 파일의 값은 읽지 않음) · madeForKids 는 처음 한 번 직접 고르기 전엔 None
SETTINGS_DEFAULT = {"v": 1, "madeForKids": None, "notify": True, "thumbnail": True, "captions": True,
                    "playlistId": "", "playlistTitle": "", "audited": False, "consentMode": None, "channelOk": "", "preauditAck": False}
REJECT_MSG = {"duplicate": "유튜브가 '이미 올린 영상과 같다'며 거절했어요 · 스튜디오에서 확인해 주세요",
              "length": LENGTH_MSG}
PROCESS_FAIL_MSG = "유튜브가 영상을 처리하지 못했어요 ({reason}) · 편집실에서 다시 내보낸 뒤 올려 주세요"

LOGIN = yt.LoginFlow()
_LOCK = threading.RLock()
_ACTIVE = {"key": None}
_JOB = {"cancel": None}   # 지금 도는 유튜브 작업의 멈추기 Event (연결 끊기·설정 지우기 때 먼저 멈춤)
_STATE = {"last": None, "lastAt": None}
_PROBE, _QUICK = {}, {}


class UploadError(Exception):
    """화면에 그대로 보여 줄 한국어 안내 (잘못된 입력·준비 안 됨)."""


# ---------- 파일 ----------

def _home():
    return Path(HOME)


def _client_path():
    return _home() / "client.bin"


def _token_path():
    return _home() / "token.bin"


def _wdir():
    return core.WORK / "youtube"


def _session_path(key):
    return _wdir() / "uploads" / f"{key}.json"


def _uri_path(key):
    return _home() / "sessions" / f"{key}.bin"


def _read_json(p, default=None, fix=False):
    """JSON 읽기 · 없으면 default · 깨졌으면 default (fix 면 <이름>.bad 로 옮겨 둠 — 쓰기 직전에만, GET 은 아무것도 바꾸지 않게)
    · 잠깐 잠겨 있으면 몇 번 다시."""
    for _ in range(5):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return default
        except PermissionError:
            time.sleep(0.05)
        except (ValueError, UnicodeDecodeError):
            if fix:
                _bad(p)
            return default
        except OSError:
            return default
    else:
        return default
    if not isinstance(d, dict):
        if fix:
            _bad(p)
        return default
    return d


def _bad(p):
    try:
        os.replace(p, Path(p).with_name(Path(p).name + ".bad"))
    except OSError:
        pass


def _write_json(p, obj):
    """임시 파일에 다 쓴 뒤 바꿔 끼움 (updater.write_atomic: Windows 잠금은 잠깐 뒤 다시 · 실패하면 임시 파일 지움).
    비밀은 여기 쓰지 않음 (yt.save_secret)."""
    p = Path(p)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        updater.write_atomic(p, json.dumps(obj, ensure_ascii=False, indent=1))


def _mb(n):
    return f"{n / 1048576:.1f}" if n < 100 * 1048576 else f"{n / 1048576:.0f}"


def _dur_text(sec):
    sec = int(round(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


def _eta_text(sec):
    sec = int(sec)
    if sec < 60:
        return f"{max(1, sec)}초"
    if sec < 3600:
        return f"{round(sec / 60)}분"
    return f"{sec // 3600}시간 {sec % 3600 // 60}분"


# ---------- 미국 태평양 시각 (할당량 하루) · tzdata 없이 ----------

def _sunday(y, m, nth):
    first = datetime(y, m, 1, tzinfo=timezone.utc).weekday()  # 월 0 … 일 6
    return 1 + (6 - first) % 7 + 7 * (nth - 1)


def _pt_offset(ts):
    """미국 규칙: 3월 둘째 일요일 10:00 UTC ~ 11월 첫째 일요일 09:00 UTC 는 -7 (PDT), 그 밖 -8 (PST)."""
    t = datetime.fromtimestamp(ts, timezone.utc)
    y = t.year
    start = datetime(y, 3, _sunday(y, 3, 2), 10, tzinfo=timezone.utc)
    end = datetime(y, 11, _sunday(y, 11, 1), 9, tzinfo=timezone.utc)
    return -7 if start <= t < end else -8


def pt_day(ts=None):
    ts = time.time() if ts is None else ts
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=_pt_offset(ts))).strftime("%Y-%m-%d")


def next_reset(ts=None):
    """다음 태평양 시각 자정 (UTC 초)."""
    ts = time.time() if ts is None else ts
    local = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=_pt_offset(ts))
    nd = (local + timedelta(days=1)).date()
    cand = None
    for off in (-7, -8):
        cand = (datetime(nd.year, nd.month, nd.day, tzinfo=timezone.utc) - timedelta(hours=off)).timestamp()
        if _pt_offset(cand) == off:
            return cand
    return cand


def reset_text(ts=None):
    """다시 채워지는 때를 이 PC 시각으로: '오늘 오후 4시' · '내일 오후 5시'."""
    now = time.time() if ts is None else ts
    r = next_reset(now)
    a, b = datetime.fromtimestamp(now), datetime.fromtimestamp(r)
    days = (b.date() - a.date()).days
    day = "오늘" if days == 0 else "내일" if days == 1 else f"{b.month}월 {b.day}일"
    h = b.hour
    return f"{day} {'오전' if h < 12 else '오후'} {h % 12 or 12}시" + (f" {b.minute}분" if b.minute else "")


yt.reset_text = reset_text  # 할당량 안내 문장의 '다시 채워지는 때'


# ---------- 할당량 (이 앱이 쓴 양) ----------

def _quota(fix=False):
    q = _read_json(_wdir() / "quota.json", {}, fix) or {}
    day = pt_day()
    if q.get("day") != day:
        q = {"v": 1, "day": day, "uploads": 0, "units": 0, "exhausted": {}}
    q.setdefault("exhausted", {})
    return q


def charge(op):
    """API 를 부르기 전에 셈 (실패한 요청도 Google 은 셈)."""
    bucket, n = COST.get(op, ("units", 1))
    with _LOCK:
        q = _quota(True)
        q[bucket] = int(q.get(bucket) or 0) + n
        try:
            _write_json(_wdir() / "quota.json", q)
        except OSError:
            pass


def _exhausted(op):
    bucket = COST.get(op, ("units", 1))[0]
    with _LOCK:
        q = _quota(True)
        q["exhausted"][bucket] = True
        try:
            _write_json(_wdir() / "quota.json", q)
        except OSError:
            pass


def _clear_exhausted(op):
    """그 묶음으로 부른 것이 잘 됐으면 '다 씀' 표시를 지움 (Google 이 다시 채웠거나 한도를 늘림)."""
    bucket = COST.get(op, ("units", 1))[0]
    with _LOCK:
        q = _quota(True)
        if q["exhausted"].pop(bucket, None):
            try:
                _write_json(_wdir() / "quota.json", q)
            except OSError:
                pass


def quota_view():
    q = _quota()
    used = {"uploads": int(q.get("uploads") or 0), "units": int(q.get("units") or 0)}
    ex = {k: bool(q["exhausted"].get(k)) for k in LIMITS}
    remaining = {k: 0 if ex[k] else max(0, LIMITS[k] - used[k]) for k in LIMITS}
    return {"used": used, "limits": dict(LIMITS), "remaining": remaining, "exhausted": ex, "resetText": reset_text(),
            "resetAt": int(next_reset()), "costs": {k: v[1] for k, v in COST.items()}}


# ---------- 설정 ----------

def _valid(k, v):
    if k in ("notify", "thumbnail", "captions", "audited", "preauditAck"):
        return isinstance(v, bool)
    if k == "madeForKids":
        return isinstance(v, bool)
    if k == "playlistId":
        return isinstance(v, str) and (v == "" or bool(PLAYLIST_RE.match(v)))
    if k == "playlistTitle":
        return isinstance(v, str) and len(v) <= 150
    if k == "consentMode":
        return v in (None, "testing", "production")
    if k == "channelOk":
        return isinstance(v, str) and (v == "" or bool(re.fullmatch(r"UC[0-9A-Za-z_-]{22}", v)))
    return False


def get_settings(fix=False):
    d = _read_json(_wdir() / "settings.json", {}, fix) or {}
    out = dict(SETTINGS_DEFAULT)
    for k in SETTINGS_DEFAULT:
        if k != "v" and k in d and _valid(k, d[k]):
            out[k] = d[k]
    return out


def save_settings(d):
    if not isinstance(d, dict):
        raise UploadError("설정 값이 잘못됐어요")
    with _LOCK:
        cur = get_settings(True)
        for k, v in d.items():
            if k == "v" or k not in SETTINGS_DEFAULT:
                continue
            if not _valid(k, v):
                raise UploadError("설정 값이 잘못됐어요")
            cur[k] = v
        _write_json(_wdir() / "settings.json", cur)
    return cur


# ---------- 연결 ----------

def _creds():
    return yt.load_secret(_client_path()), yt.load_secret(_token_path())


def _save_token(tok, force=False):
    """새로 받은 access_token 저장. force 가 아니면 token.bin 이 '같은 연결'(같은 refresh_token)일 때만 —
    그사이 [연결 끊기]를 했거나 다른 계정으로 다시 연결했으면 옛 작업의 토큰은 버림 (끊은 연결이 되살아나지 않게)."""
    with _LOCK:
        if not force:
            cur = yt.load_secret(_token_path()) or {}
            if not cur.get("refresh_token") or cur.get("refresh_token") != tok.get("refresh_token"):
                return False
        yt.save_secret(_token_path(), tok)
        return True


def _mark_relogin(expected=None):
    """연결이 끊김(invalid_grant·401): 열쇠는 지우고 채널 정보만 남겨 '다시 연결하기'를 보여 줌.
    expected(그 작업이 쓰던 refresh_token)를 주면 token.bin 이 그 연결일 때만 (새로 연결한 다른 계정을 지우지 않게)."""
    with _LOCK:
        tok = yt.load_secret(_token_path())
        if not tok:
            return
        if expected is not None and tok.get("refresh_token") and tok.get("refresh_token") != expected:
            return
        keep = {k: v for k, v in tok.items() if k not in ("refresh_token", "access_token", "expires_at")}
        keep["relogin"] = True
        yt.save_secret(_token_path(), keep)


def _rt(c):
    """Client 가 쓰던 refresh_token (_mark_relogin 의 expected · 없으면 '')."""
    return str(((c.token if c is not None else None) or {}).get("refresh_token") or "")


def own_channel():
    """config.json 의 우리 채널 주소 → {"id": UC…} 또는 {"handle": "@…"} 또는 None."""
    url = str(core.CONFIG.get("channel_url") or "")
    m = CHANNEL_RE.search(url)
    if m:
        return {"id": m.group(1)}
    m = HANDLE_RE.search(url)
    return {"handle": m.group(1)} if m else None


def _match(ch, st):
    """연결한 채널이 우리 채널인지: True · False · None(모름). [이 채널에 올릴게요]로 고른 채널이면 True."""
    if not ch:
        return None
    if st.get("channelOk") and st["channelOk"] == ch.get("id"):
        return True
    own = own_channel()
    if not own:
        return None
    if own.get("id"):
        return ch.get("id") == own["id"]
    return (ch.get("handle") or "").lower() == own["handle"].lower()


def _relogin_msg(tok=None, st=None):
    """'다시 연결해 주세요' + (테스트 상태라고 했거나, 모르는데 6.5일 넘게 쓴 연결이면) 7일 안내.
    Google 은 테스트 상태 7일을 토큰 응답에 알려 주지 않음 (refresh_token_expires_in 은 '시간 제한 액세스' 때만) → 설정 안내 5단계의 고른 값으로."""
    tok = tok if tok is not None else (yt.load_secret(_token_path()) or {})
    st = st or get_settings()
    msg = yt.MSG["relogin"]
    age = time.time() - float(tok.get("connected_at") or tok.get("obtained_at") or time.time())
    if st.get("consentMode") == "testing" or (st.get("consentMode") is None and age >= 6.5 * 86400):
        msg += " · " + TESTING_HINT
    return msg


def _testing_warn(tok, st, connected):
    """연결 카드의 알림: 테스트 상태(5단계에서 고름)면 처음부터 'N일 뒤 끊겨요' · 모르면 6일 넘었을 때 · Google 이 기한을 알려 줬으면 그 날짜로."""
    if not connected:
        return None
    since = float(tok.get("connected_at") or tok.get("obtained_at") or time.time())
    mode = st.get("consentMode")
    tail = "안내 5단계의 [앱 게시]를 하면 더 끊기지 않아요"
    if mode == "testing":
        left = (since + 7 * 86400 - time.time()) / 86400
        return (f"테스트 상태라 약 {max(1, int(left + 0.999))}일 뒤 연결이 끊겨요 · " if left < 3 else "테스트 상태라 7일마다 연결이 끊겨요 · ") + tail
    exp = tok.get("refresh_expires_at")
    if exp:
        left = (float(exp) - time.time()) / 86400
        return f"Google이 정한 연결 기한까지 약 {max(1, int(left + 0.999))}일 남았어요 · 그 뒤에는 [다시 연결하기]를 눌러 주세요"
    if mode is None and time.time() - since > 6 * 86400:
        return "테스트 상태라면 곧 연결이 끊겨요 · " + tail
    return None


def explain(e):
    """ApiError → 화면 문장 (연결 끊김이면 테스트 상태 안내를 붙임)."""
    if isinstance(e, yt.ApiError) and e.kind == "relogin" and str(e) == yt.MSG["relogin"]:
        return _relogin_msg()
    return str(e)


def _api(cancel=None):
    client, tok = _creds()
    if not client:
        raise UploadError("먼저 Google 설정을 해 주세요 · [설정 안내 열기] 7단계")
    if not tok or not tok.get("refresh_token"):
        raise yt.ApiError("relogin")
    return yt.Client(client, tok, on_token_saved=_save_token, on_call=charge, cancel=cancel)


def _relogin_guard(fn):
    """바로 Google 에 묻는 것(재생목록·상태 확인): 연결이 끊겼으면 그 연결만 '다시 연결하기'로."""
    c = _api()
    try:
        return fn(c)
    except yt.ApiError as e:
        if e.kind == "relogin":
            _mark_relogin(_rt(c))
        raise


def status():
    """7단계 카드 상태 (인터넷 안 씀 · 아무것도 바꾸지 않음 · 토큰·보안 비밀번호·세션 주소는 절대 넣지 않음)."""
    client, tok = _creds()
    st = get_settings()
    tok = tok or {}
    ch = tok.get("channel") if isinstance(tok.get("channel"), dict) else None
    connected = bool(tok.get("refresh_token"))
    since = tok.get("connected_at") or tok.get("obtained_at")
    warn, exp = _testing_warn(tok, st, connected), tok.get("refresh_expires_at")
    return {"configured": bool(client), "clientHint": (client["client_id"][:12] + "…") if client and client.get("client_id") else None,
            "projectId": (client or {}).get("project_id") or None, "connected": connected, "needsRelogin": bool(tok.get("relogin")),
            "reloginMsg": _relogin_msg(tok, st) if tok.get("relogin") else None, "channel": ch, "own": own_channel(),
            "match": _match(ch, st), "connectedAt": since, "testingWarn": warn, "refreshExpiresAt": exp,
            "login": _login_snapshot(), "settings": st, "quota": quota_view(), "pending": pending(), "active": _ACTIVE["key"],
            "last": _STATE["last"], "lastAt": _STATE["lastAt"], "scheduleMinMinutes": SCHEDULE_MIN // 60,
            "running": bool(_JOB["cancel"])}


def _login_snapshot():
    """로그인 상태 (주소는 넣지 않음 — state·PKCE challenge 가 든 주소는 [유튜브 계정 연결하기]의 응답으로만 · 원격으로 새지 않게)."""
    s = LOGIN.snapshot()
    return {k: s.get(k) for k in ("state", "error", "channel")}


def save_client(text=None, client_id=None, client_secret=None, log=None):
    """설정 안내 7단계: JSON 파일 내용 또는 두 칸 → client.bin. 다른 클라이언트로 바꾸면 예전 연결은 끊음."""
    try:
        c, warn = yt.parse_client(text, client_id, client_secret)
    except yt.ApiError as e:  # 넣은 값이 잘못됨 → 400
        raise UploadError(str(e)) from None
    with _LOCK:
        old = yt.load_secret(_client_path())
        yt.save_secret(_client_path(), dict(c, v=1, savedAt=time.time()))
        if old and old.get("client_id") != c["client_id"]:
            tok = yt.load_secret(_token_path())
            if tok:
                yt.revoke(tok)
                yt.delete_secret(_token_path())
    if log:
        log("유튜브 바로 올리기 · Google 클라이언트를 저장했어요")
    return {"ok": True, "clientHint": c["client_id"][:12] + "…", "warn": warn}


def _stop_job(wait=10.0):
    """도는 유튜브 작업을 멈추고 끝날 때까지 조금 기다림 (그 작업이 끊은 연결의 토큰을 다시 쓰지 않게)."""
    ev = _JOB["cancel"]
    if ev is None:
        return
    ev.set()
    t = time.monotonic() + wait
    while _JOB["cancel"] is not None and time.monotonic() < t:
        time.sleep(0.05)


def _forget_sessions():
    """올리던 세션 주소(채널에 올릴 권한이 담김)를 모두 지움 → 다음에는 처음부터 (작업 폴더의 기록은 남아 '처음부터 다시'로 보임)."""
    try:
        for p in (_home() / "sessions").glob("*.bin"):
            yt.delete_secret(p)
    except OSError:
        pass


def _disconnect(log, msg, clear_client):
    LOGIN.cancel()
    _stop_job()
    with _LOCK:
        tok = yt.load_secret(_token_path())
        revoked = bool(tok) and yt.revoke(tok)
        yt.delete_secret(_token_path())
        if clear_client:
            yt.delete_secret(_client_path())
        _forget_sessions()
    if log:
        log(msg + ("" if revoked or not tok else " · Google 쪽 연결은 끊지 못함 (권한 화면에서 직접)"))
    return {"ok": True, "revoked": revoked, "hadToken": bool(tok)}


def clear_all(log=None):
    """Google 설정 지우기: 도는 작업 멈춤 → 연결 끊기(되도록 Google 에도) → client.bin·token.bin·세션 주소 지움.
    → {"ok", "revoked": Google 이 끊었는지 (False 면 화면이 myaccount.google.com/connections 를 권함)}."""
    return _disconnect(log, "유튜브 바로 올리기 · Google 설정을 지웠어요", True)


def logout(log=None):
    return _disconnect(log, "유튜브 연결을 끊었어요", False)


def _on_token(client, tok, log):
    """로그인 결과: 채널 확인(channels.list) → token.bin. 채널이 없거나 API 를 안 켰으면 연결을 되돌림."""
    c = yt.Client(client, tok, on_call=charge)
    try:
        ch = c.channel_mine()
    except yt.ApiError:
        yt.revoke(tok)
        raise
    now = time.time()
    rec = dict(c.token, v=1, connected_at=now, channel=ch)
    rec.pop("relogin", None)
    _save_token(rec, force=True)
    if log:
        log(f"유튜브 계정을 연결했어요 · {ch['title'] or ch['id']}")
    return ch


def start_login(log=None):
    client = yt.load_secret(_client_path())
    if not client:
        raise UploadError("먼저 Google 설정을 해 주세요 · [설정 안내 열기] 7단계")
    url = LOGIN.start(client, lambda tok: _on_token(client, tok, log))
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 — 브라우저가 안 열리면 화면의 [주소 복사]로
        pass
    return {"ok": True, "url": url}


def cancel_login():
    LOGIN.cancel()
    return {"ok": True}


def playlists():
    return {"ok": True, "items": _relogin_guard(lambda c: c.playlists())}


def create_playlist(title, privacy="public"):
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not title or len(title) > 150 or re.search(r"[<>]", title):
        raise UploadError("재생목록 이름을 넣어 주세요 (150자까지 · < > 안 됨)")
    if privacy not in PRIVACY:
        privacy = "public"
    p = _relogin_guard(lambda c: c.create_playlist(title, privacy))
    save_settings({"playlistId": p["id"], "playlistTitle": p["title"][:150]})
    return {"ok": True, "playlist": p}


# ---------- 올릴 것 확인 (읽기만) ----------

def session_key(name, seq):
    return hashlib.sha1(f"{name}\n{seq or ''}".encode("utf-8")).hexdigest()[:16]


def _probe(path):
    try:
        st = path.stat()
    except OSError:
        return None
    k = (str(path), st.st_size, st.st_mtime_ns)
    if k not in _PROBE:
        try:
            _PROBE.clear() if len(_PROBE) > 64 else None
            _PROBE[k] = editor.probe(path)
        except Exception:  # noqa: BLE001 — 못 읽으면 크기·길이를 모름
            return None
    return _PROBE[k]


def quick_hash(path):
    """같은 영상인지 빠르게: 크기 + 앞 1MiB + 뒤 1MiB 의 sha1."""
    st = path.stat()
    k = (str(path), st.st_size, st.st_mtime_ns)
    if k in _QUICK:
        return _QUICK[k]
    h = hashlib.sha1(str(st.st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if st.st_size > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    if len(_QUICK) > 64:
        _QUICK.clear()
    _QUICK[k] = h.hexdigest()[:20]
    return _QUICK[k]


def _plan(name, seq=None, privacy=None):
    """(화면용 dict, 안에서 쓰는 값). 막는 문제(problems)와 알림(warnings)."""
    seq = seq or None
    kit = upload.load_kit(name, seq)
    files = upload.files_for(name, seq)
    client, tok = _creds()
    tok = tok or {}
    st = get_settings()
    problems, warnings, codes = [], [], []
    pub = {"ok": True, "name": name, "seq": seq or "", "problems": problems, "warnings": warnings, "kit": None, "files": None,
           "costParts": None, "pending": None, "last": None, "duplicate": None, "defaults": {}, "audited": st["audited"], "codes": codes}
    inner = {"kit": kit, "files": files, "codes": codes}

    def bad(code, msg, first=False):
        i = 0 if first else len(problems)
        problems.insert(i, msg)
        codes.insert(i, code)

    if not client:
        bad("setup", "먼저 Google 설정을 해 주세요 · [설정 안내 열기]")
    elif tok.get("relogin") or (tok and not tok.get("refresh_token")):
        bad("relogin", _relogin_msg(tok, st))
    elif not tok:
        bad("connect", "유튜브 계정을 먼저 연결해 주세요 · [유튜브 계정 연결하기]")
    elif _match(tok.get("channel"), st) is False:
        bad("channel", "연결한 채널이 설정한 '풋살사관학교' 채널이 아니에요 · [이 채널에 올릴게요]를 누르거나 다시 연결해 주세요")
    if kit is None:
        bad("kit", "먼저 '올리기 키트 만들기'를 눌러 주세요 · 키트의 제목·설명·태그를 그대로 올려요", True)
        return pub, inner
    title = str(kit.get("title") or "")
    desc = str(kit.get("description") or "")
    tags = [str(t) for t in kit.get("tags") or []]
    db, tl = len(desc.encode("utf-8")), upload.tags_len(tags)
    pub["kit"] = {"title": title, "titleLen": len(title), "descBytes": db, "descChars": len(desc), "tagsLen": tl, "tags": len(tags),
                  "format": kit.get("format"), "chapters": len(kit.get("chapters") or [])}
    if not title.strip():
        bad("meta", "제목이 비어 있어요 · 위 '제목' 칸을 채워 주세요")
    elif len(title) > TITLE_MAX:
        bad("meta", f"제목이 {len(title)}자예요 · 유튜브 제목은 {TITLE_MAX}자까지예요")
    if re.search(r"[<>]", title):
        bad("meta", "제목에 < > 는 쓸 수 없어요")
    if db > DESC_BYTES:
        bad("meta", f"설명이 {db:,}바이트예요 · 유튜브 설명은 {DESC_BYTES:,}바이트까지예요 (한글은 한 글자에 3바이트라 약 1,600자) · 설명을 줄여 주세요")
    if re.search(r"[<>]", desc):
        bad("meta", "설명에 < > 는 쓸 수 없어요")
    if tl > TAGS_MAX or any(re.search(r"[<>]", t) for t in tags):
        bad("meta", f"태그가 합계 {tl}자예요 · 유튜브 태그는 {TAGS_MAX}자까지예요 (< > 안 됨)")
    if len(upload._HASHTAG.findall(desc)) > HASHTAG_MAX:
        warnings.append(f"해시태그가 {HASHTAG_MAX}개를 넘으면 유튜브가 해시태그를 모두 무시해요")
    warnings.extend(kit.get("alerts") or [])
    video = files.get("video")
    if seq and video is None:
        bad("export", "이 편집본은 아직 내보내지 않았어요 · 편집실에서 내보낸 뒤 올려 주세요")
        return pub, inner
    try:
        size = video.stat().st_size
    except (OSError, AttributeError):
        bad("file", yt.MSG["file"])
        return pub, inner
    if size <= 0:
        bad("file", yt.MSG["file"])
        return pub, inner
    info = _probe(video) or {}
    w, h, dur = info.get("width"), info.get("height"), float(info.get("duration") or 0)
    shorts = bool(w and h and h >= w and 0 < dur <= SHORTS_MAX)
    fmt = kit.get("format") or "long"
    if fmt == "shorts" and not shorts:
        warnings.append("쇼츠 키트인데 영상이 " + ("가로예요" if w and h and w > h else "3분이 넘어요") +
                        " · 유튜브가 긴 영상으로 올려요")
    if fmt != "shorts" and shorts:
        warnings.append(f"세로·{_dur_text(dur)} 영상이라 유튜브가 쇼츠로 올려요")
    if dur > VERIFY_LEN:
        warnings.append("15분이 넘는 영상은 채널 인증(전화번호 확인)이 있어야 올라가요 · 인증하지 않았다면 youtube.com/verify 에서 먼저 해 주세요")
    th = kit.get("thumbnail") or {}
    thumb = core.OUT / th["file"] if th.get("file") else None
    tbytes = th.get("bytes") or 0
    if not thumb:
        warnings.append("저장한 썸네일이 없어요 · 유튜브가 영상에서 고른 장면을 써요")
    elif tbytes > THUMB_API_MAX:
        warnings.append("썸네일이 50MB를 넘어서 올릴 수 없어요 · 썸네일 편집기에서 JPG로 다시 저장해 주세요")
        thumb = None
    if shorts and thumb:
        warnings.append("쇼츠 맞춤 썸네일은 유튜브 앱·스튜디오에서만 바꿀 수 있어서, 기본으로는 올리지 않아요")
    srt = files.get("srt")
    if not srt:
        warnings.append("자막 파일(.srt)이 없어서 자막은 올리지 않아요" + (" · 편집실에서 자막을 넣고 내보내면 함께 올라가요" if seq else ""))
    try:
        qh = quick_hash(video)
    except OSError:
        qh = None
    hist = _history()
    last = next((x for x in hist if x.get("name") == name and (x.get("seq") or None) == seq), None)
    dup = next((x for x in hist if qh and x.get("quick") == qh), None)
    if dup:
        lt = time.localtime(dup.get("at") or 0)
        when = f"{lt.tm_mon}월 {lt.tm_mday}일 {lt.tm_hour:02d}:{lt.tm_min:02d}"
        warnings.append(f"이 영상은 {when}에 이미 올렸어요 ({dup.get('url')}) · 또 올리면 같은 영상이 두 개가 돼요")
        pub["duplicate"] = {"at": dup.get("at"), "url": dup.get("url"), "videoId": dup.get("videoId")}
    if privacy and privacy != "private" and not st["audited"]:
        warnings.append("Google 감사(안내 9단계)를 받기 전에는 '" + PRIVACY_KO.get(privacy, privacy) + "'로 올려도 영상이 '비공개(잠김)'가 되고, "
                        "나중에 공개로 바꿀 수 없어요 · 감사 전에는 '비공개'로 시험해 주세요")
    pub["files"] = {"video": video.name, "where": "out" if seq else "videos", "size": size, "sizeText": f"{_mb(size)}MB",
                    "w": w, "h": h, "duration": round(dur, 1), "durationText": _dur_text(dur), "shorts": shorts,
                    "srt": srt.name if srt else None, "thumbnail": thumb.name if thumb else None, "thumbOk": bool(th.get("ok"))}
    pub["costParts"] = {"thumbnail": COST["thumbnails.set"][1] if thumb else 0, "captions": COST["captions.insert"][1] if srt else 0,
                        "playlist": COST["playlistItems.insert"][1], "check": COST["videos.list"][1]}
    pub["defaults"] = {"thumbnail": bool(thumb) and not shorts and st["thumbnail"], "captions": bool(srt) and st["captions"]}
    sess = _read_json(_session_path(session_key(name, seq)))
    if sess:
        pub["pending"] = _pending_view(sess)
    pub["last"] = last
    inner.update(video=video, size=size, shorts=shorts, thumb=thumb, srt=srt, quick=qh, info=info)
    return pub, inner


def plan(name, seq=None, privacy=None):
    return _plan(name, seq, privacy)[0]


# ---------- 세션 (올리던 것) ----------

def _store_uri(sess, uri):
    """세션 주소는 사용자 폴더의 비밀 파일에만 (작업 폴더 JSON 에는 '시작함' 표시와 짝 번호 sid 만)."""
    sess["sid"] = secrets.token_hex(8)
    yt.save_secret(_uri_path(sess["key"]), {"v": 1, "sid": sess["sid"], "uri": uri})
    sess["uri"] = True


def _load_uri(sess):
    """저장해 둔 세션 주소 → 문자열 또는 None (없음·다른 PC·짝 번호가 다름·Google 업로드 주소가 아님 → '처음부터')."""
    if not sess.get("uri") or not sess.get("sid"):
        return None
    d = yt.load_secret(_uri_path(sess["key"])) or {}
    uri = d.get("uri")
    if d.get("sid") != sess.get("sid") or not isinstance(uri, str) or not yt.valid_session_uri(uri):
        return None
    return uri


def _drop_uri(sess):
    yt.delete_secret(_uri_path(sess["key"]))
    sess.update(uri=None, sid=None)


def _save_session(s):
    s["updatedAt"] = time.time()
    _write_json(_session_path(s["key"]), s)


def _delete_session(key):
    for p in (_session_path(key), _uri_path(key)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _pending_view(s):
    size = int(s.get("size") or 0)
    state = s.get("state") or "paused"
    if state in ("uploading", "starting") and _ACTIVE["key"] != s.get("key"):
        state = "paused"  # 앱을 껐다 켬 · 작업이 끝남
    return {"key": s.get("key"), "name": s.get("name"), "seq": s.get("seq") or "", "file": s.get("file"),
            "title": (s.get("meta") or {}).get("title"), "privacy": (s.get("meta") or {}).get("privacy"),
            "pct": int(int(s.get("offset") or 0) * 100 / size) if size else 0, "offset": int(s.get("offset") or 0), "size": size,
            "sizeText": f"{_mb(size)}MB", "state": state, "at": s.get("updatedAt") or s.get("createdAt"), "error": s.get("error"),
            "kind": s.get("kind"), "started": bool(s.get("uri"))}


def pending():
    try:
        files = sorted((_wdir() / "uploads").glob("*.json"))
    except OSError:
        return []
    out = []
    for p in files:
        s = _read_json(p)
        if s and KEY_RE.match(str(s.get("key") or "")):
            out.append(_pending_view(s))
    return out


def discard(key):
    if not KEY_RE.match(str(key or "")):
        raise UploadError("잘못된 요청이에요")
    if _ACTIVE["key"] == key:
        raise UploadError("올리는 중에는 지울 수 없어요 · 먼저 [멈추기]를 눌러 주세요")
    _delete_session(key)
    return {"ok": True}


def _stale(s, inner, ch):
    """이어 올릴 수 없는 까닭 (없으면 None)."""
    v = inner.get("video")
    try:
        st = v.stat()
    except (OSError, AttributeError):
        return yt.MSG["file"]
    if v.name != s.get("file") or st.st_size != s.get("size") or int(st.st_mtime) != int(s.get("mtime") or 0) or \
            inner.get("quick") != s.get("quick"):
        return "영상 파일이 바뀌어서 처음부터 다시 올려요"
    if time.time() - float(s.get("createdAt") or 0) > SESSION_DAYS * 86400:
        return "올리던 연결이 오래돼서(6일 넘음) 처음부터 다시 올려요"
    if (s.get("channel") or {}).get("id") != (ch or {}).get("id"):
        return "연결한 채널이 바뀌어서 처음부터 다시 올려요"
    if s.get("uri") and not _load_uri(s):
        return "올리던 연결 정보를 읽지 못해서 처음부터 다시 올려요"
    pa = (s.get("meta") or {}).get("publishAt")
    if pa and not s.get("uri") and _iso_ts(pa) < time.time() + 60:
        return PAST_SCHEDULE
    return None


def _iso_ts(iso):
    try:
        return datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


# ---------- 기록 ----------

def _history(fix=False):
    d = _read_json(_wdir() / "history.json", {}, fix) or {}
    items = d.get("items") if isinstance(d.get("items"), list) else []
    return [x for x in items if isinstance(x, dict) and VIDEO_ID.match(str(x.get("videoId") or ""))]


def history(limit=10):
    return {"ok": True, "items": _history()[:max(1, min(int(limit or 10), HISTORY_MAX))]}


def _history_put(entry):
    with _LOCK:
        items = [x for x in _history(True) if x.get("videoId") != entry["videoId"]]
        items.insert(0, entry)
        _write_json(_wdir() / "history.json", {"v": 1, "items": items[:HISTORY_MAX]})


def links(vid, shorts=False):
    return {"url": f"https://www.youtube.com/shorts/{vid}" if shorts else f"https://youtu.be/{vid}",
            "studio": f"https://studio.youtube.com/video/{vid}/edit"}


def open_link(what, video_id=None, key=None):
    """정해진 주소만 엶 (화면이 보낸 주소는 쓰지 않음)."""
    if what in ("watch", "shorts", "studio"):
        if not VIDEO_ID.match(str(video_id or "")):
            raise UploadError("잘못된 영상이에요")
        url = {"watch": f"https://youtu.be/{video_id}", "shorts": f"https://www.youtube.com/shorts/{video_id}",
               "studio": f"https://studio.youtube.com/video/{video_id}/edit"}[what]
    elif what == "guide" and key in GUIDE_URLS:
        url = GUIDE_URLS[key]
    else:
        raise UploadError("무엇을 열지 모르겠어요")
    webbrowser.open(url)
    return url


# ---------- 작업 (start_job) ----------

def _options(opts, shorts):
    st = get_settings()
    o = opts if isinstance(opts, dict) else {}
    priv = o.get("privacy", "private")
    if priv not in PRIVACY + ("scheduled",):
        raise UploadError("공개 설정을 골라 주세요")
    pa = None
    if priv == "scheduled":
        raw = str(o.get("publishAt") or "")
        try:
            ts = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            if not raw.endswith("Z"):
                raise ValueError
        except ValueError:
            raise UploadError("예약 공개 시각을 골라 주세요") from None
        if not time.time() + SCHEDULE_MIN <= ts <= time.time() + SCHEDULE_MAX:
            raise UploadError(f"예약 공개 시각은 지금부터 {SCHEDULE_MIN // 60}분 뒤 ~ 1년 안으로 골라 주세요")
        pa = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    mfk = o.get("madeForKids", st["madeForKids"])
    if not isinstance(mfk, bool):
        raise UploadError("아동용 영상인지 골라 주세요 (꼭 정해야 해요)")
    out = {"privacy": priv, "publishAt": pa, "madeForKids": mfk}
    for k in ("notify", "thumbnail", "captions"):
        v = o.get(k, st[k] if not (k == "thumbnail" and shorts) else False)
        if not isinstance(v, bool):
            raise UploadError("설정 값이 잘못됐어요")
        out[k] = v
    pid = o.get("playlistId", st["playlistId"]) or ""
    if not isinstance(pid, str) or (pid and not PLAYLIST_RE.match(pid)):
        raise UploadError("재생목록을 다시 골라 주세요")
    ptitle = str(o.get("playlistTitle", st["playlistTitle"] if pid == st["playlistId"] else "") or "")[:150]
    out.update(playlistId=pid, playlistTitle=ptitle)
    return out


def _body(meta):
    st = {"privacyStatus": "private" if meta["privacy"] == "scheduled" else meta["privacy"],
          "selfDeclaredMadeForKids": bool(meta["madeForKids"])}   # 아동용 여부는 늘 직접 보냄 (COPPA)
    if meta.get("publishAt"):
        st["publishAt"] = meta["publishAt"]
    return {"snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"], "categoryId": CATEGORY,
                        "defaultLanguage": LANG, "defaultAudioLanguage": LANG}, "status": st}


def _new_session(key, name, seq, kit, inner, o, ch):
    v, thumb, srt = inner["video"], inner.get("thumb"), inner.get("srt")
    st = v.stat()
    return {"v": 1, "key": key, "name": name, "seq": seq or "", "file": v.name, "where": "out" if seq else "videos", "size": st.st_size,
            "mtime": int(st.st_mtime), "quick": inner.get("quick"), "mime": yt._mime(v),
            "meta": {"title": kit["title"], "description": kit["description"], "tags": list(kit.get("tags") or []), "categoryId": CATEGORY,
                     "privacy": o["privacy"], "publishAt": o["publishAt"], "madeForKids": o["madeForKids"], "notify": o["notify"],
                     "shorts": inner.get("shorts", False)},
            "want": _want(o, thumb, srt, seq), "channel": {"id": (ch or {}).get("id"), "title": (ch or {}).get("title")},
            "createdAt": time.time(), "offset": 0, "state": "starting", "uri": None, "error": None, "kind": None}


def _want(o, thumb, srt, seq):
    return {"thumbnail": {"file": thumb.name} if o["thumbnail"] and thumb else None,
            "captions": {"file": srt.name, "where": "out" if seq else "analysis"} if o["captions"] and srt else None,
            "playlist": {"id": o["playlistId"], "title": o["playlistTitle"]} if o["playlistId"] else None}


def _video_path(s):
    if s.get("where") == "out":
        return core.OUT / editor.safe_name(s["file"])
    return editor.video_path(s["file"])


def _job(at):
    """유튜브 작업 하나 (run_upload·resume·finish): 도는 동안 멈추기 Event(at 번째 인자 · cancel=)를 알려 둠 (연결 끊기가 먼저 멈추게)."""
    def deco(fn):
        def wrap(*a, **kw):
            cancel = kw.get("cancel", a[at] if len(a) > at else None)
            outer = _JOB["cancel"] is None
            if outer:
                _JOB["cancel"] = cancel if cancel is not None else threading.Event()
            try:
                return fn(*a, **kw)
            finally:
                if outer:
                    _JOB["cancel"] = None
        wrap.__name__, wrap.__doc__ = fn.__name__, fn.__doc__
        return wrap
    return deco


@_job(4)
def run_upload(name, seq, opts, log, cancel, again=False):
    """[유튜브에 올리기] 작업: 확인 → 세션(이어 올리기면 그대로) → 올리기 → 썸네일·자막·재생목록 → 상태 확인."""
    seq = seq or None
    try:
        pub, inner = _plan(name, seq, (opts or {}).get("privacy") if isinstance(opts, dict) else None)
    except (LookupError, ValueError, FileNotFoundError) as e:  # 그사이 편집본·영상을 지움
        return _done({"ok": False, "error": str(e) or yt.MSG["file"]})
    if pub["problems"]:
        return _done({"ok": False, "error": pub["problems"][0], "problems": pub["problems"]})
    try:
        o = _options(opts, inner.get("shorts"))
    except UploadError as e:
        return _done({"ok": False, "error": str(e)})
    keep = {k: o[k] for k in ("madeForKids", "notify", "captions", "playlistId", "playlistTitle")}
    if not inner.get("shorts"):
        keep["thumbnail"] = o["thumbnail"]
    if not get_settings()["audited"]:
        keep["preauditAck"] = True  # 감사 전 업로드를 한 번 확인함 (화면이 처음 한 번 물음)
    try:
        save_settings(keep)  # 고른 값이 다음 기본값 · 공개 설정은 기억하지 않음 (늘 비공개로 시작 · 실수로 공개되지 않게)
    except (UploadError, OSError):
        pass
    _, tok = _creds()
    ch = (tok or {}).get("channel")
    key = session_key(name, seq)
    kit = inner["kit"]
    sess = None if again else _read_json(_session_path(key))
    notes = []
    if sess:
        why = _stale(sess, inner, ch)
        if why:
            log(f"  {why}")
            notes.append(why)
            sess = None
        else:
            sess["want"] = _want(o, inner.get("thumb"), inner.get("srt"), seq)  # 썸네일·자막·재생목록은 지금 고른 대로
            m = sess.get("meta") or {}
            if (m.get("title"), m.get("description"), m.get("tags"), m.get("privacy")) != \
                    (kit["title"], kit["description"], list(kit.get("tags") or []), o["privacy"]):
                notes.append("이미 보낸 제목·설명·태그·공개 설정으로 이어 올려요 · 바꾼 내용은 올린 뒤 스튜디오에서 고쳐 주세요")
    if sess is None:
        _delete_session(key)
        sess = _new_session(key, name, seq, kit, inner, o, ch)
    return _drive(sess, log, cancel, notes)


@_job(2)
def resume(key, log, cancel):
    """[이어 올리기]: 저장해 둔 세션으로 (파일이 바뀌었으면 키트로 처음부터)."""
    if not KEY_RE.match(str(key or "")):
        return _done({"ok": False, "error": "잘못된 요청이에요"})
    sess = _read_json(_session_path(key))
    if not sess:
        return _done({"ok": False, "error": "이어 올릴 것이 없어요 · 다시 [유튜브에 올리기]를 눌러 주세요"})
    name, seq = sess.get("name"), sess.get("seq") or None
    try:
        pub, inner = _plan(name, seq)
    except (LookupError, ValueError, FileNotFoundError) as e:
        return _done({"ok": False, "error": str(e) or yt.MSG["file"]})
    stop = {"setup", "relogin", "connect", "channel", "file", "export"} | (set() if sess.get("uri") else {"meta"})  # 보낸 제목 등은 그대로
    blocking = [p for p, c in zip(pub["problems"], pub["codes"]) if c in stop]
    if blocking:
        relog = "relogin" in pub["codes"]
        return _done({"ok": False, "error": blocking[0], "problems": blocking, "relogin": relog, "key": key,
                      "fail": trouble.youtube_card("relogin") if relog else None})
    _, tok = _creds()
    why = _stale(sess, inner, (tok or {}).get("channel"))
    notes = []
    pa = (sess.get("meta") or {}).get("publishAt")
    if why and pa and _iso_ts(pa) < time.time() + SCHEDULE_MIN:  # 처음부터 다시 올려야 하는데 예약 시각이 이미 지났거나 너무 가까움
        why = PAST_SCHEDULE
    if why:
        log(f"  {why}")
        if pub["kit"] is None or why == PAST_SCHEDULE:
            return _done({"ok": False, "error": why, "key": key})
        o = {"privacy": sess["meta"]["privacy"], "madeForKids": sess["meta"]["madeForKids"], "notify": sess["meta"]["notify"]}
        if sess["meta"].get("privacy") == "scheduled":
            o["publishAt"] = sess["meta"].get("publishAt")
        w = sess.get("want") or {}
        o.update(thumbnail=bool(w.get("thumbnail")), captions=bool(w.get("captions")),
                 playlistId=(w.get("playlist") or {}).get("id") or "", playlistTitle=(w.get("playlist") or {}).get("title") or "")
        r = run_upload(name, seq, o, log, cancel, again=True)
        r.setdefault("notes", []).insert(0, why)
        return r
    kit = upload.load_kit(name, seq) or {}
    m = sess.get("meta") or {}
    if kit and (m.get("title"), m.get("description"), m.get("tags")) != (kit.get("title"), kit.get("description"), list(kit.get("tags") or [])):
        notes.append("올리기 시작한 뒤 키트를 고쳤어요 · 이미 보낸 제목·설명·태그로 올라가요 · 바꾼 내용은 스튜디오에서 고쳐 주세요")
    return _drive(sess, log, cancel, notes)


def _done(r):
    _STATE["last"], _STATE["lastAt"] = r, time.time()
    return r


def _fail(sess, e, log, state="failed", c=None):
    """올리기 실패: 세션은 남겨 두고(이어 올리기) 쉬운 말로."""
    kind = e.kind if isinstance(e, yt.ApiError) else "error"
    msg = explain(e)
    if kind == "relogin":
        _mark_relogin(_rt(c) if c is not None else None)
        msg = _relogin_msg()
    if kind == "quota":
        _exhausted("videos.insert")
    sess.update(state=state, error=msg, kind=kind)
    card = None if state == "paused" else trouble.youtube_card(kind)  # 7단계 실패 카드 (정해진 문장 · 할당량·연결 끊김·인터넷·서버 · D-048)
    try:
        _save_session(sess)
    except OSError:
        pass
    log(f"  유튜브에 올리지 못했어요 · {kind}" + (f" ({e.reason})" if isinstance(e, yt.ApiError) and e.reason else "") +
        f" · {int(int(sess.get('offset') or 0) * 100 / max(1, sess['size']))}%")
    return _done({"ok": False, "kind": kind, "error": msg, "key": sess["key"], "relogin": kind == "relogin",
                  "paused": state == "paused", "pct": int(int(sess.get("offset") or 0) * 100 / max(1, sess["size"])), "fail": card})


def _drive(sess, log, cancel, notes=None, restarted=False):
    notes = list(notes or [])
    try:
        path = _video_path(sess)
    except (ValueError, FileNotFoundError):
        return _done({"ok": False, "error": yt.MSG["file"]})
    try:
        c = _api(cancel)
    except UploadError as e:
        return _done({"ok": False, "error": str(e)})
    except yt.ApiError as e:
        return _fail(sess, e, log)
    size = int(sess["size"])
    _ACTIVE["key"] = sess["key"]
    state = {"saved": 0.0}
    t0 = [time.monotonic(), int(sess.get("offset") or 0)]

    def on_offset(n):
        sess["offset"] = int(n)
        if time.monotonic() - state["saved"] > 2:
            state["saved"] = time.monotonic()
            try:
                _save_session(sess)
            except OSError:
                pass

    def progress(sent):
        el, done = time.monotonic() - t0[0], sent - t0[1]
        speed = done / el if el > 1 and done > 0 else None
        eta = (size - sent) / speed if speed else None
        core.set_progress(label="유튜브에 올리는 중", item=sess["name"], step="1/4", pct=min(99, int(sent * 100 / size)),
                          detail=f"{_mb(sent)} / {_mb(size)}MB" + (f" · 남은 시간 약 {_eta_text(eta)}" if eta else ""),
                          eta=int(eta) if eta else None)

    def on_wait(n, confirmed, why):
        """끊겨서 기다리는 동안: 막대는 Google 이 받았다고 한 데까지로 · 남은 시간은 빼고 '다시 연결하는 중'."""
        t0[0], t0[1] = time.monotonic(), int(confirmed)
        head = "인터넷이 끊겼어요" if why == "network" else "유튜브 서버가 대답하지 않아요"
        core.set_progress(label="유튜브에 올리는 중", item=sess["name"], step="1/4", pct=min(99, int(confirmed * 100 / size)),
                          detail=f"{head} · 다시 연결하는 중 ({n}번째) · 올라간 양 {_mb(confirmed)} / {_mb(size)}MB", eta=None)
        if n == 1:
            log(f"  유튜브 올리기 · {why} · 기다렸다 이어서 ({int(confirmed * 100 / size)}%)")

    try:
        video = None
        uri = _load_uri(sess) if sess.get("uri") else None
        if not uri:
            pa = sess["meta"].get("publishAt")
            if pa and _iso_ts(pa) < time.time() + 60:  # 오래 멈췄다가 처음부터 다시 → 예약 시각이 이미 지남
                sess.update(state="failed", error=PAST_SCHEDULE, kind="schedule")
                _save_session(sess)
                return _done({"ok": False, "error": PAST_SCHEDULE, "key": sess["key"]})
            core.set_progress(label="유튜브에 올리는 중", item=sess["name"], step="1/4", pct=0, detail="유튜브에 연결하는 중")
            uri = c.start_upload(_body(sess["meta"]), size, sess.get("mime") or yt._mime(path), bool(sess["meta"].get("notify", True)))
            _store_uri(sess, uri)
            sess.update(offset=0, state="uploading", error=None, kind=None, startedAt=time.time())
            _save_session(sess)
            _clear_exhausted("videos.insert")
            log(f"유튜브에 올리기 시작 · {sess['file']} ({_mb(size)}MB · {PRIVACY_KO.get(sess['meta']['privacy'], '')})")
        else:
            core.set_progress(label="유튜브에 올리는 중", item=sess["name"], step="1/4", pct=None, detail="올린 데까지 확인하는 중")
            kind, val = c.upload_status(uri, size)
            if kind == "done":
                video = val
            else:
                sess.update(offset=val, state="uploading", error=None, kind=None)
                _save_session(sess)
                log(f"유튜브에 이어 올려요 · {sess['file']} · {int(val * 100 / size)}%부터")
        if video is None:
            t0[0], t0[1] = time.monotonic(), int(sess.get("offset") or 0)
            progress(t0[1])
            video = c.upload_file(uri, path, size, int(sess.get("offset") or 0), cancel=cancel, progress=progress,
                                  on_offset=on_offset, mime=sess.get("mime"), on_wait=on_wait)
    except yt.Cancelled:
        r = _fail(sess, yt.Cancelled(), log, "paused", c)
        r["error"] = yt.MSG["cancelled"]
        log(f"  유튜브 올리기를 멈췄어요 · {r['pct']}%")
        return r
    except yt.ApiError as e:
        if e.kind == "session_expired" and not restarted:
            log("  올리던 연결이 만료돼서 처음부터 다시 올려요")
            _drop_uri(sess)
            sess.update(offset=0, state="starting")
            notes.append(yt.MSG["session_expired"])
            _ACTIVE["key"] = None
            return _drive(sess, log, cancel, notes, restarted=True)
        return _fail(sess, e, log, c=c)
    except FileNotFoundError:
        return _fail(sess, yt.ApiError("file"), log, c=c)
    finally:
        _ACTIVE["key"] = None
    return _after_upload(c, sess, video, log, cancel, notes)


def _after_upload(c, sess, video, log, cancel, notes):
    vid = str((video or {}).get("id") or "")
    if not VIDEO_ID.match(vid):
        _drop_uri(sess)
        sess.update(state="failed", error="유튜브가 영상 번호를 돌려주지 않았어요 · 스튜디오에서 올라갔는지 확인해 주세요")
        _save_session(sess)
        return _done({"ok": False, "error": sess["error"], "key": sess["key"]})
    meta = sess["meta"]
    shorts = bool(meta.get("shorts"))
    lk = links(vid, shorts)
    entry = {"at": time.time(), "name": sess["name"], "seq": sess.get("seq") or "", "file": sess["file"], "title": meta["title"],
             "videoId": vid, "privacy": meta["privacy"], "publishAt": meta.get("publishAt"), "shorts": shorts,
             "madeForKids": meta.get("madeForKids"), "channel": sess.get("channel"), "url": lk["url"], "studio": lk["studio"],
             "quick": sess.get("quick"), "size": sess["size"], "want": sess.get("want") or {},
             "steps": {k: {"state": "todo" if (sess.get("want") or {}).get(k) else "skip"} for k in STEP_ORDER},
             "locked": False, "status": {}, "preAudit": not get_settings()["audited"], "processing": True, "problem": None}
    _history_put(entry)  # 영상 번호를 받자마자 기록 (뒤 단계가 실패·앱이 꺼져도 남게)
    _delete_session(sess["key"])
    log(f"유튜브에 올렸어요 · {sess['file']}")  # 영상 번호는 기록에 넣지 않음 — 이 기록은 휴대폰 /r/status 로도 감 (주소는 7단계 기록에만 · D-047)
    warnings = _post(c, entry, log, cancel)
    return _done(_result(entry, warnings, notes))


def privacy_text(entry):
    """기록·결과의 공개 설정 글자: 감사 전에 올린 것은 '공개 불가'까지 (Google 이 잠금 · 비공개로 올렸어도)."""
    if entry.get("locked"):
        return "비공개(잠김)"
    t = PRIVACY_KO.get(entry.get("privacy"), "")
    return f"{t}(감사 전 · 공개 불가)" if entry.get("preAudit") else t


def step_card(steps):
    """마무리 단계(썸네일·자막·재생목록)에서 사용자가 할 일이 남았으면 7단계 실패 카드 (영상은 올라감 · 정해진 문장 · D-048):
    연결 끊김(남은 단계를 못 함) > 썸네일 막힘(채널 인증 · thumbnails.set 403) > 할당량. 없으면 None."""
    st = [x for x in (steps or {}).values() if isinstance(x, dict)]
    if any(x.get("state") == "todo" and x.get("msg") == RELOGIN_STEP for x in st):
        return trouble.youtube_card("yt_relogin_left")
    if any(x.get("state") == "needs_verify" for x in st):
        return trouble.youtube_card("yt_thumb")
    if any(x.get("state") == "quota" for x in st):
        return trouble.youtube_card("yt_quota_left")
    return None


def _result(entry, warnings, notes=None):
    return {"ok": True, "videoId": entry["videoId"], "url": entry["url"], "studio": entry["studio"], "privacy": entry["privacy"],
            "fail": step_card(entry.get("steps")),  # 영상은 올라갔지만 할 일이 남음 → 7단계 카드 (D-048)
            "privacyText": privacy_text(entry), "publishAt": entry.get("publishAt"), "shorts": entry["shorts"],
            "steps": entry["steps"], "warnings": warnings, "notes": list(notes or []), "locked": entry["locked"],
            "lockedMsg": LOCKED_MSG if entry["locked"] else None, "channel": entry.get("channel"), "preAudit": bool(entry.get("preAudit")),
            "processing": bool(entry.get("processing")), "problem": entry.get("problem")}


def _srt_path(w, name):
    if w.get("where") == "analysis":
        return core.adir(name) / "subtitles.srt"
    return core.OUT / editor.safe_name(w["file"])


def _thumb_path(w, entry):
    p = core.OUT / editor.safe_name(w["file"])
    if p.is_file():
        return p
    try:  # 지웠거나 이름이 바뀜 → 이 영상의 가장 최근 썸네일
        th = upload.thumbnail_check(entry["name"], "shorts" if entry.get("shorts") else "long")
    except Exception:  # noqa: BLE001
        th = {}
    return core.OUT / th["file"] if th.get("file") else p


def _step_call(fn, entry, cancel):
    """썸네일·자막·재생목록 한 번: 올린 지 FRESH_SECS 안이면 5xx·videoNotFound 는 처리 중일 수 있어 POST_RETRY 만큼 기다렸다 다시."""
    for i in range(len(POST_RETRY) + 1):
        try:
            return fn()
        except yt.ApiError as e:
            fresh = time.time() - float(entry.get("at") or 0) < FRESH_SECS
            transient = e.kind == "server" or (e.kind == "not_found" and e.reason != "playlistNotFound")
            if not (fresh and transient):
                raise
            if i == len(POST_RETRY):
                if e.kind == "not_found":
                    raise yt.ApiError("processing", status=e.status, reason=e.reason) from None
                raise
            if cancel is not None:
                if cancel.wait(POST_RETRY[i]):
                    raise yt.Cancelled() from None
            else:
                time.sleep(POST_RETRY[i])


def _apply_status(entry, v):
    """videos.list 결과 → 기록 (처리 중 · 잠김 · 거절·처리 실패 안내)."""
    s = (v or {}).get("status") or {}
    pd = (v or {}).get("processingDetails") or {}
    entry["status"] = {k: s.get(k) for k in ("uploadStatus", "privacyStatus", "publishAt", "rejectionReason", "failureReason")}
    entry["status"]["processingStatus"] = pd.get("processingStatus")
    entry["checkedAt"] = time.time()
    up = s.get("uploadStatus")
    entry["processing"] = up == "uploaded" or pd.get("processingStatus") == "processing"
    want_priv, got = entry["privacy"], s.get("privacyStatus")
    if (want_priv in ("public", "unlisted") and got == "private") or (want_priv == "scheduled" and got and not s.get("publishAt")):
        entry["locked"] = True
    if up == "rejected":
        r = str(s.get("rejectionReason") or "")
        entry["problem"] = REJECT_MSG.get(r) or f"유튜브가 이 영상을 거절했어요 ({yt._clean_reason(r) or '이유 모름'}) · 스튜디오에서 확인해 주세요"
    elif up == "failed" or pd.get("processingStatus") == "failed":
        entry["problem"] = PROCESS_FAIL_MSG.format(reason=yt._clean_reason(s.get("failureReason")) or "이유 모름")
    elif up in ("processed", "uploaded"):
        entry["problem"] = None


def _post(c, entry, log, cancel, only=None, check=True):
    """썸네일 → 자막 → 재생목록 → 상태 확인. 단계마다 따로 (하나가 실패해도 다음으로) · 끝날 때마다 기록."""
    vid, want, steps = entry["videoId"], entry.get("want") or {}, entry["steps"]
    warnings, dead = [], False  # dead: 연결이 끊김 → 남은 단계는 부르지 않고 '다시 연결한 뒤 마저 하기'로
    label = {"thumbnail": "썸네일", "captions": "자막", "playlist": "재생목록"}
    for i, name in enumerate(STEP_ORDER, 2):
        w = want.get(name)
        if only is not None and name not in only:
            continue
        if not w:
            steps[name] = {"state": "skip"}
            continue
        if cancel is not None and cancel.is_set():
            steps[name] = {"state": "todo", "msg": "멈췄어요 · [마저 하기]를 눌러 주세요"}
            continue
        if dead:
            steps[name] = {"state": "todo", "msg": RELOGIN_STEP}
            continue
        core.set_progress(label="유튜브 마무리 중", item=entry["name"], step=f"{i}/4", pct=None,
                          detail={"thumbnail": "썸네일 올리는 중", "captions": "자막 올리는 중", "playlist": "재생목록에 넣는 중"}[name])
        try:
            if name == "thumbnail":
                p = _thumb_path(w, entry)
                _step_call(lambda: c.set_thumbnail(vid, p), entry, cancel)
            elif name == "captions":
                raw = _srt_path(w, entry["name"]).read_bytes()
                _step_call(lambda: c.insert_caption(vid, raw, LANG, "한국어"), entry, cancel)
            else:
                _step_call(lambda: c.add_to_playlist(w["id"], vid), entry, cancel)
            steps[name] = {"state": "ok"}
            _clear_exhausted(STEP_OP[name])
        except yt.Cancelled:
            steps[name] = {"state": "todo", "msg": "멈췄어요 · [마저 하기]를 눌러 주세요"}
        except yt.ApiError as e:
            if e.kind == "exists":
                steps[name] = {"state": "ok", "msg": "이미 올라가 있어요"}
            elif e.kind == "quota":
                _exhausted(STEP_OP[name])
                steps[name] = {"state": "quota", "msg": explain(e)}
            elif e.kind == "thumb_verify":
                steps[name] = {"state": "needs_verify", "msg": str(e)}
            elif e.kind == "relogin":
                _mark_relogin(_rt(c))
                dead = True
                steps[name] = {"state": "todo", "msg": RELOGIN_STEP}
            elif e.kind == "processing":
                steps[name] = {"state": "todo", "msg": str(e)}
            else:
                steps[name] = {"state": "error", "msg": explain(e)}
            log(f"  {label[name]} · {steps[name]['state']}" + (f" ({e.reason})" if e.reason else ""))
        except (OSError, ValueError):
            steps[name] = {"state": "error", "msg": "파일을 읽지 못했어요 · " +
                           ("썸네일 편집기에서 다시 저장해 주세요" if name == "thumbnail" else "편집실에서 다시 내보내 주세요")}
            log(f"  {label[name]} · 파일을 읽지 못함")
        _history_put(entry)
    _history_put(entry)  # 건너뛴 단계(연결 끊김·멈춤 · continue)의 안내도 기록에
    if check and not dead and not (cancel is not None and cancel.is_set()):
        core.set_progress(label="유튜브 마무리 중", item=entry["name"], step="4/4", pct=None, detail="올라간 상태 확인하는 중")
        try:
            _apply_status(entry, c.video_status(vid) or {})
            if entry["locked"]:
                log("  비공개(잠김)로 올라갔어요 · Google 감사 전 프로젝트")
            if entry.get("problem"):
                warnings.append(entry["problem"])
        except yt.ApiError as e:
            if e.kind == "relogin":
                _mark_relogin(_rt(c))
            warnings.append("올라간 상태를 확인하지 못했어요 · 스튜디오에서 확인해 주세요 (" + explain(e) + ")")
        _history_put(entry)
    return warnings


def check(video_id):
    """[상태 다시 확인] (작업 아님 · videos.list 1단위): 처리 중이던 영상이 끝났는지 · 잠김 · 거절(15분 넘음 등) → 기록을 고침."""
    if not VIDEO_ID.match(str(video_id or "")):
        raise UploadError("잘못된 영상이에요")
    entry = next((x for x in _history() if x.get("videoId") == video_id), None)
    if not entry:
        raise UploadError("올린 기록을 찾지 못했어요")
    v = _relogin_guard(lambda c: c.video_status(video_id))
    if v is None:
        entry["problem"] = yt.MSG["not_found"]
        entry["processing"] = False
        entry["checkedAt"] = time.time()
    else:
        _apply_status(entry, v)
    with _LOCK:
        cur = next((x for x in _history(True) if x.get("videoId") == video_id), None)
        if cur:  # 그사이 [마저 하기]가 단계를 고쳤을 수 있으니 상태만 바꿔 넣음
            for k in ("status", "processing", "problem", "locked", "checkedAt"):
                cur[k] = entry.get(k)
            _history_put(cur)
            entry = cur
    return dict(_result(entry, [entry["problem"]] if entry.get("problem") else []), at=entry.get("at"))


@_job(3)
def finish(video_id, which, log, cancel):
    """[마저 하기]·[썸네일 다시 올리기]: 기록에 남은 영상의 썸네일·자막·재생목록 중 끝나지 않은 것 (which 를 주면 그것만)."""
    if not VIDEO_ID.match(str(video_id or "")):
        return _done({"ok": False, "error": "잘못된 영상이에요"})
    entry = next((x for x in _history() if x.get("videoId") == video_id), None)
    if not entry:
        return _done({"ok": False, "error": "올린 기록을 찾지 못했어요"})
    only = [w for w in (which or []) if w in STEP_ORDER] or \
        [k for k in STEP_ORDER if (entry.get("steps") or {}).get(k, {}).get("state") not in ("ok", "skip")]
    if not only:
        return _done(_result(entry, [], ["마저 할 것이 없어요"]))
    try:
        c = _api(cancel)
    except UploadError as e:
        return _done({"ok": False, "error": str(e)})
    except yt.ApiError as e:
        if e.kind == "relogin":
            _mark_relogin(None)
        return _done({"ok": False, "error": explain(e), "kind": e.kind, "relogin": e.kind == "relogin", "videoId": video_id,
                      "fail": trouble.youtube_card("yt_relogin_left" if e.kind == "relogin" else e.kind)})
    entry.setdefault("steps", {})
    log(f"유튜브 마무리 · {entry.get('file') or entry.get('title') or ''} · {', '.join(only)}")  # 영상 번호는 넣지 않음 (위와 같음)
    warnings = _post(c, entry, log, cancel, only=only, check=False)
    return _done(_result(entry, warnings))
