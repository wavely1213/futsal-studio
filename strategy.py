"""채널 전략 (8단계) — 비슷한 채널(경쟁 채널)의 공개 숫자로 우리 채널의 계획·가능성·점검을 돕는다 (D-024·D-025).

- 데이터: yt-dlp 목록(구독자·제목·길이·조회수 · 날짜 없음, core.channel_listing) + 공개 RSS(최근 15개의 정확한 날짜·
  조회수·좋아요·원제). 작업 폴더 strategy/ 에 시각과 함께 저장한다:
  state.json(사용자 데이터) · channels/<sha1(열쇠)>.json(채널마다 최신 자료) · history.jsonl(새로 고칠 때마다 한 줄 · 추세)
  · checkups.json(점검 기록) · forecast.json(가능성 캐시). 아무것도 새로 고치기 전에는 앱과 함께 배포하는
  비교 데이터(strategy_seed.json, 2026-10-07)를 쓴다 (실제로 새로 고친 자료가 먼저).
- 새로 고침은 작업(start_job) 하나 · 요청 사이 0.75초 · 채널 사이 2초+흔들기 · 3일 안에 받은 채널은 건너뜀 ·
  막히면 6시간 쉼(RSS 는 계속) (BR-016). 자동으로 하는 것은 우리 채널 숫자 기록(하루 한 번)뿐.
- 분석은 규칙: 채널 통계 · 제목 공식·주제어·시리즈 찾기 · 가져올 점 8분류(맞춤 점수·품) · 30/60/90 계획 · 점검 피드백.
  가능성(%)은 forecast.py (BR-015). 클로드는 버튼을 눌렀을 때만 숫자만 보낸다(claude_cli).
- style·claude_cli 는 함수 안에서 지연 import 한다: style → plan → core 순환을 피하고, 앱 시작을 가볍게.
"""
import functools
import hashlib
import http.client
import json
import math
import os
import random
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import core
import forecast
import hooks
import refs
import source

GROUPS = ("풋살 특화", "축구 레슨·기술", "축구 예능·챌린지", "리뷰·브이로그·분석")
OWN = "own"                       # 우리 채널 열쇠 (주소는 config.json 의 channel_url)
OWN_GROUP = "풋살 특화"
LIMIT, FULL_LIMIT = 60, 400       # 보통 새로 고침은 최근 60개, 전체 훑기는 400개까지
KEEP = 400                        # 형식마다 기억하는 최근 영상 수
FULL_EVERY = 30 * 86400           # 전체 훑기(평생 조회 합·한국어 원제)는 채널마다 처음 한 번, 그 뒤 30일마다
STALE = 3 * 86400                 # 받은 지 3일이 안 된 채널은 건너뜀 ('모두 다시'가 아니면)
OWN_STALE = 86400                 # 우리 채널 숫자 기록: 하루 한 번
OWN_MIN_GAP = 600                 # 점검·기록: 10분 안에 받았으면 다시 받지 않음
REQ_SLEEP = 0.75                  # yt-dlp 요청 사이 (초)
GAP, JITTER = 2.0, 0.5            # 채널 사이 2초 + 0~0.5초 흔들기
PAUSE_SECS = 6 * 3600             # YouTube 가 막으면(봇 확인·403·429) 이 시간 동안 yt-dlp 단계를 쉼 (RSS 는 계속)
FAIL_PAUSE = 3600                 # 까닭 모를 실패가 연달아 나면 1시간 쉼 (화면에서 [지금 다시 시도]로 풀 수 있음)
FAIL_STREAK = 3                   # 같은 새로 고침에서 까닭 모를 실패가 이만큼 연달아 나면 쉼 (인터넷 끊김·없는 채널은 세지 않음)
NET_STREAK = 2                    # 인터넷이 끊긴 채널이 이만큼 연달아 나면 이번 새로 고침을 멈춤 (쉬지는 않음)
KO_MAX = 60                       # 한국어 원제 목록은 최근 60개까지
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
RSS_MAX = 2 << 20                 # RSS 응답 2MB 까지
RSS_TIMEOUT = 20
RSS_RETRY_WAIT = 5                # 429·5xx 면 5초 뒤 한 번만 다시
NET_MSG = "인터넷 연결을 확인해 주세요"
SECS_NORMAL, SECS_FULL = 6, 20    # 채널당 걸리는 시간 어림 (화면 '약 M분' · 실제 처음 12곳 225~320초 → 전체 훑기 20초)
RECENT = 30                       # 통계는 최근 30개 (15개 기준도 함께)
ACTIVE = ((14, "활발"), (45, "보통"), (180, "쉬는 중"))  # 마지막 업로드 며칠 전 → 활동 상태 (넘으면 '멈춤')
REVIVE_GAP = 90                   # 90일 넘게 쉰 뒤 첫 업로드 = 다시 시작한 날
REMIND_DAYS = 7                   # 지난 점검이 이만큼 지나면 알림
CHECKUPS_MAX, TODOS_MAX = 200, 300
TEXT_MAX, LONG_MAX = 500, 2000    # 사용자가 넣은 글 길이 한도
MAX_PER_WEEK, MAX_FORMATS, MAX_SERIES = 14, 12, 20
READ_TRIES = 5
SEED_FILE = "strategy_seed.json"
JOB_REFRESH, JOB_OWN, JOB_CHECK, JOB_AI = "채널 전략 새로 고침", "우리 채널 숫자 기록", "채널 점검", "클로드로 전략 보기"
JOBS = (JOB_REFRESH, JOB_OWN, JOB_CHECK, JOB_AI)
CATS = ("기획·형식", "썸네일·제목", "편집 스타일", "쇼츠 운영", "업로드 주기", "시리즈·코너", "시청자 참여", "수익화·레슨 연계")
EFFORT_W = {"쉬움": 1.0, "보통": 0.65, "큼": 0.35}
GROUP_W = {"풋살 특화": 1.0, "축구 레슨·기술": 0.85, "축구 예능·챌린지": 0.6, "리뷰·브이로그·분석": 0.55}
USE = {"썸네일·제목": ["thumb", "title"], "편집 스타일": ["edit"], "쇼츠 운영": ["edit", "shorts"], "시리즈·코너": ["title", "plan"],
       "시청자 참여": ["title", "edit"], "수익화·레슨 연계": ["upload"], "기획·형식": ["plan"], "업로드 주기": ["plan"]}
TARGETS = {"풋살 입문자": ("입문", "초보", "기초", "기본기"), "플랩 레벨업": ("플랩", "레벨", "세미"), "동호인 팀": ("동호인", "아마추어", "팀", "생체"),
           "유소년": ("유소년", "초등", "어린이", "U-"), "학부모": ("학부모", "부모"), "지도자": ("지도자", "코치", "감독")}
DIR_TAGS = {"A": {"레슨", "시리즈", "권위", "예능", "편집", "쇼츠", "제목", "입문", "참여"}, "B": {"대결", "챌린지", "시리즈", "쇼츠", "예능", "참여"},
            "C": {"쇼츠", "총정리", "주기", "제목"}}
# 처음 고르는 경쟁 채널 (ref_channels.json 이름): 풋살 특화 8 + 축구 레슨 5 + 요청에서 직접 말한 채널(★) 2
DEFAULT_PICKS = ("쌈바 풋살 클래스", "샌드박스 풋살", "쪼살", "풋살해주호", "명싸커", "아이콘 풋살", "주재파악 TV", "영타",
                 "JK 아트사커", "축구도사 메기", "축정원", "강코치 풋볼", "지니풋볼", "슛포러브", "도블락")
NO_TAB = re.compile(r"does not have an? (shorts|videos)\b|no (shorts|videos) tab|This channel does not have", re.I)
# yt-dlp 오류 글 → 종류: 인터넷 끊김(쉬지 않음) · 없는 채널(실패지만 연달아 세지 않음) · 너무 많이 물음(막힘과 같게)
NET_ERR = re.compile(r"urlopen error|name resolution|Name or service not known|getaddrinfo|Failed to resolve|Network is unreachable|"
                     r"No route to host|Connection (?:refused|reset|aborted)|timed out|RemoteDisconnected|Remote end closed|"
                     r"TransportError|ConnectionError|Temporary failure|Errno -?\d+", re.I)
GONE_ERR = re.compile(r"does not exist|HTTP Error 404|404: Not Found|This channel is not available|has been terminated|"
                      r"channel was removed|not a valid URL|Unsupported URL", re.I)
RATE_ERR = re.compile(r"HTTP Error 429|Too Many Requests|(?<![\w-])429(?![\w-])", re.I)
UC_RE = re.compile(r"UC[\w-]{22}")
VID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "m": "http://search.yahoo.com/mrss/"}

_LOCK = threading.RLock()
_CACHE = {}                      # 파일 경로 → ((수정 시각, 크기), 내용) — 1초마다 부르는 화면이 매번 읽지 않게 (채널 파일은 넣지 않음)
_SLIM = {}                       # 채널 파일 → ((수정 시각, 크기), 분석용으로 줄인 자료) — 큰 채널 원본을 메모리에 들고 있지 않게
SLIM_IDS = 120                   # 분석용 자료는 형식마다 최근 120개 + RSS 영상만
_ANA = {}                        # 채널 분석 캐시 (열쇠·받은 시각 같으면 그대로)
_sleep = time.sleep              # 시험에서 바꿔 끼움 (예절 대기)
_jitter = random.random


class StoreBusy(OSError):
    """기록 파일을 지금은 읽을 수 없음 (잠김) — 덮어쓰면 안 됨."""


class StrategyError(ValueError):
    """화면에 그대로 보여 줄 한국어 안내."""


# ---------- 저장 (WORK/strategy) ----------

def root():
    return core.WORK / "strategy"


def _path(name):
    return root() / name


def fid(key):
    """채널 파일 이름: Windows 는 대소문자를 구분하지 않아 대소문자만 다른 UC id 가 부딪히고, 한글 핸들은 cp949 문제(I-030)가 있어 해시로."""
    return hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:16]


def _read(path, empty, strict=False, cache=True):
    """JSON 읽기 (수정 시각으로 기억 · cache=False 면 기억하지 않음): 없으면 empty() · 깨졌으면 .bad 로 남기고 empty() ·
    잠겨서 못 읽으면 몇 번 다시, 그래도 안 되면 strict 일 때 StoreBusy (좋은 파일을 빈 기록으로 덮어쓰지 않게)."""
    with _LOCK:
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return empty()
        except OSError as e:
            if strict:
                raise StoreBusy(f"채널 전략 기록을 읽지 못했어요 · {e}") from e
            return empty()
        hit = _CACHE.get(str(path))
        if hit and hit[0] == key:
            return hit[1]
        raw, err = None, None
        for i in range(READ_TRIES):
            try:
                raw = path.read_bytes()
                break
            except FileNotFoundError:
                return empty()
            except OSError as e:
                err = e
                _sleep(0.05 * (i + 1))
        if raw is None:
            if strict:
                raise StoreBusy(f"채널 전략 기록을 읽지 못했어요 · {err}") from err
            return empty()
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("형식이 달라요")
        except ValueError:
            try:
                path.with_name(path.name + ".bad").write_bytes(raw)
            except OSError:
                pass
            data = empty()
        if cache:
            _CACHE[str(path)] = (key, data)
        return data


def _write(path, data):
    """임시 파일에 다 쓴 뒤 바꿔 끼움 (Windows: 백신·OneDrive 가 잠깐 잡고 있으면 조금 뒤 다시)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}_{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    for i in range(20):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if i == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            _sleep(0.1)
    with _LOCK:
        _CACHE.pop(str(path), None)
        _SLIM.pop(str(path), None)


def _copy(d):
    return json.loads(json.dumps(d, ensure_ascii=False))


# ---- state.json (사용자 데이터) ----

def _empty_state():
    return {"v": 1, "own": {}, "competitors": None, "removed": [], "strategy": None, "hidden": [], "todos": [],
            "settings": {"remind": True, "ownAuto": True}, "pause": None, "ai": None}


def _clean_state(d):
    out = _empty_state()
    if not isinstance(d, dict):
        return out
    out["own"] = d["own"] if isinstance(d.get("own"), dict) else {}
    if isinstance(d.get("competitors"), list):
        out["competitors"] = [c for c in d["competitors"] if isinstance(c, dict) and isinstance(c.get("key"), str) and c["key"] and c["key"] != OWN]
    out["removed"] = [k for k in d.get("removed") or [] if isinstance(k, str)]
    out["strategy"] = d["strategy"] if isinstance(d.get("strategy"), dict) else None
    out["hidden"] = [k for k in d.get("hidden") or [] if isinstance(k, str)]
    out["todos"] = [t for t in d.get("todos") or [] if isinstance(t, dict) and isinstance(t.get("id"), str)]
    if isinstance(d.get("settings"), dict):
        out["settings"].update({k: bool(v) for k, v in d["settings"].items() if k in ("remind", "ownAuto")})
    for k in ("pause", "ai"):
        out[k] = d[k] if isinstance(d.get(k), dict) else None
    return out


def load_state(strict=False):
    """state.json → 정리한 사본 (경쟁 채널이 아직 없으면 추천 채널에서 기본 15곳)."""
    d = _clean_state(_read(_path("state.json"), _empty_state, strict))
    d = _copy(d)
    if d["competitors"] is None:
        d["competitors"] = default_competitors()
    return d


def _update_state(fn):
    """state.json 을 읽어 fn(data) 로 고친 뒤 저장 (한 번에 하나씩). fn 이 오류를 내면 저장하지 않음."""
    with _LOCK:
        d = load_state(strict=True)
        out = fn(d)
        _write(_path("state.json"), d)
        return out


# ---- channels/<fid>.json · history.jsonl · checkups.json ----

def _chan_path(key):
    return root() / "channels" / f"{fid(key)}.json"


def load_channel(key, slim=False):
    """채널 자료 하나: 새로 고침·점검은 원본 전체(기억하지 않음) · slim=True 면 분석용으로 줄인 것(기억함)."""
    d = _read_slim(_chan_path(key)) if slim else _read(_chan_path(key), dict, cache=False)
    return d if d.get("key") == key else None


def _slim(ch):
    """분석용으로 줄인 채널 자료: 형식마다 최근 120개 + RSS 영상, 안 쓰는 칸(본 시각·번역 제목) 뺌 · 탭의 전체 개수는 nIds 로."""
    tabs, keep = {}, set((ch.get("rss") or {}).get("ids") or [])
    for name, t in (ch.get("tabs") or {}).items():
        if isinstance(t, dict):
            ids = t.get("ids") or []
            tabs[name] = dict(t, ids=ids[:SLIM_IDS], nIds=len(ids))
            keep |= set(ids[:SLIM_IDS])
    vids = {i: {k: x for k, x in v.items() if k not in ("seen", "vAt") and not (k == "te" and v.get("t"))}
            for i, v in (ch.get("videos") or {}).items() if i in keep and isinstance(v, dict)}
    return dict(ch, tabs=tabs, videos=vids)


def _read_slim(path):
    with _LOCK:
        try:
            st = path.stat()
        except OSError:
            return {}
        key = (st.st_mtime_ns, st.st_size)
        hit = _SLIM.get(str(path))
        if hit and hit[0] == key:
            return hit[1]
    d = _read(path, dict, cache=False)
    sl = _slim(d) if isinstance(d.get("videos"), dict) else d
    with _LOCK:
        _SLIM[str(path)] = (key, sl)
    return sl


def save_channel(data):
    _write(_chan_path(data["key"]), data)


def live_channels():
    """새로 고친 채널 자료 전부 {열쇠: 자료}."""
    out = {}
    d = root() / "channels"
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for f in files:
        x = _read_slim(f)
        if isinstance(x.get("key"), str) and isinstance(x.get("videos"), dict):
            out[x["key"]] = x
    return out


def append_history(rec):
    p = _path("history.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_history():
    """{열쇠: [기록…]} (시각 순) — 깨진 줄은 건너뜀."""
    out = {}
    try:
        with open(_path("history.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("k"), str) and isinstance(r.get("at"), (int, float)):
                    out.setdefault(r["k"], []).append(r)
    except OSError:
        return {}
    for v in out.values():
        v.sort(key=lambda r: r["at"])
    return out


def load_checkups():
    d = _read(_path("checkups.json"), lambda: {"v": 1, "items": []})
    return [x for x in d.get("items") or [] if isinstance(x, dict) and isinstance(x.get("at"), (int, float))]


def _save_checkups(items):
    _write(_path("checkups.json"), {"v": 1, "items": items[-CHECKUPS_MAX:]})


# ---- 함께 배포하는 비교 데이터 ----

def seed():
    """strategy_seed.json → {at, channels{열쇠: 자료}} (없거나 깨졌으면 빈 것)."""
    d = _read(core.APP_DIR / SEED_FILE, dict)
    chans = {}
    for c in d.get("channels") or []:
        if isinstance(c, dict) and isinstance(c.get("key"), str) and isinstance(c.get("videos"), dict):
            chans[c["key"]] = c
    return {"at": d.get("at"), "channels": chans}


# ---------- 채널 목록 ----------

def recommended():
    return refs.recommended()


def _rec_key(c):
    if isinstance(c.get("channelId"), str) and UC_RE.fullmatch(c["channelId"]):
        return c["channelId"]
    h = c.get("handle")
    return "h:" + h.lower() if isinstance(h, str) and h.startswith("@") else None


def default_competitors():
    out = []
    for c in recommended().get("channels") or []:
        k = _rec_key(c)
        if k and c.get("name") in DEFAULT_PICKS:
            out.append({"key": k, "name": c["name"], "url": c.get("url"), "handle": c.get("handle"), "channelId": c.get("channelId"),
                        "group": c.get("group") if c.get("group") in GROUPS else GROUPS[0], "from": "recommended", "addedAt": None})
    return out


def own_entry(st=None):
    st = st or load_state()
    url = str(core.CONFIG.get("channel_url") or "").strip()
    k = source._url_key(url)
    own = st.get("own") or {}
    return {"key": OWN, "name": own.get("name") or "우리 채널", "url": url,
            "channelId": k[1] if k and k[0] == "id" else own.get("channelId"),
            "handle": k[1] if k and k[0] == "handle" else own.get("handle"), "group": own.get("group") or OWN_GROUP}


def _url_info(url):
    """화면에서 넣은 채널 주소·@핸들 → (열쇠, channelId, handle, 정리한 주소). YouTube 채널 주소가 아니면 StrategyError."""
    s = str(url or "").strip()
    bad = StrategyError("채널 주소나 @핸들을 넣어 주세요")
    if not s or len(s) > 300:
        raise bad
    if re.match(r"https?://", s, re.I) or re.match(r"(?:(?:www|m)\.)?youtube\.com/", s, re.I):
        from urllib.parse import urlsplit
        host = urlsplit(s if re.match(r"https?://", s, re.I) else "https://" + s).netloc.lower().split(":")[0]
        if host not in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            raise bad
        if re.search(r"/(watch|shorts/[\w-]{11}|live/|embed/|playlist)", s):
            raise bad
    elif not re.fullmatch(r"@?[\w.\-가-힣]{2,100}", s) and not UC_RE.fullmatch(s):
        raise bad
    k = source._url_key(s)
    if not k:
        raise bad
    if k[0] == "id":
        return k[1], k[1], None, f"https://www.youtube.com/channel/{k[1]}"
    if k[0] == "handle":
        return "h:" + k[1], None, k[1], f"https://www.youtube.com/{k[1]}"
    return "p:" + k[1], None, None, f"https://www.youtube.com{k[1]}"


def add_channel(url, group=None):
    """경쟁 채널 넣기 → 넣은 항목. 이미 있으면 StrategyError. 추천 채널이면 이름·분류를 그대로."""
    key, cid, handle, nurl = _url_info(url)
    rec = next((c for c in recommended().get("channels") or []
                if (cid and c.get("channelId") == cid) or (handle and str(c.get("handle") or "").lower() == handle)), None)
    if rec:
        key = _rec_key(rec) or key
        cid = cid or rec.get("channelId")
        handle = handle or str(rec.get("handle") or "").lower() or None
        nurl = rec.get("url") or nurl
    g = group if group in GROUPS else (rec.get("group") if rec and rec.get("group") in GROUPS else GROUPS[0])

    def put(d):
        own = own_entry(d)
        if (cid and cid == own.get("channelId")) or (handle and handle == str(own.get("handle") or "").lower()):
            raise StrategyError("우리 채널이에요. 우리 채널은 언제나 맨 위에 있어요")
        for c in d["competitors"]:
            same_h = handle and str(c.get("handle") or "").lower() == handle
            if c["key"] == key or (cid and c.get("channelId") == cid) or same_h:
                raise StrategyError(f"이미 있는 채널이에요 · {c.get('name') or key}")
        entry = {"key": key, "name": (rec or {}).get("name") or (handle or nurl.rsplit("/", 1)[-1]), "url": nurl, "handle": handle,
                 "channelId": cid, "group": g, "from": "recommended" if rec else "user", "addedAt": time.time()}
        d["competitors"].append(entry)
        d["removed"] = [k for k in d["removed"] if k != key]
        return entry
    return _update_state(put)


def remove_channel(key):
    def rm(d):
        n = len(d["competitors"])
        d["competitors"] = [c for c in d["competitors"] if c["key"] != key]
        if len(d["competitors"]) == n:
            raise StrategyError("목록에 없는 채널이에요")
        if key not in d["removed"]:
            d["removed"].append(key)
    _update_state(rm)


def set_group(key, group):
    if group not in GROUPS:
        raise StrategyError("분류를 골라 주세요")

    def put(d):
        for c in d["competitors"]:
            if c["key"] == key:
                c["group"] = group
                return
        raise StrategyError("목록에 없는 채널이에요")
    _update_state(put)


def known_channels(st=None):
    """알고 있는 모든 채널 {열쇠: 자료} — 새로 고친 자료가 비교 데이터보다 먼저 (보정은 경쟁 채널 목록이 아니라 이것 전부로)."""
    st = st or load_state()
    live = live_channels()
    sd = seed()["channels"]
    out = {}
    own = own_entry(st)
    live_ids = {c.get("channelId") for c in live.values() if c.get("channelId")}
    for k, c in sd.items():
        if k == OWN:
            if own.get("channelId") and c.get("channelId") == own["channelId"]:
                out[k] = c
            continue
        if c.get("channelId") and c["channelId"] in live_ids:
            continue
        out[k] = c
    out.update(live)
    groups = {c["key"]: c.get("group") for c in st["competitors"]}
    names = {c["key"]: c.get("name") for c in st["competitors"] if c.get("from") == "recommended" and c.get("name")}
    for k, c in out.items():
        g, nm = groups.get(k), names.get(k)
        if (g and c.get("group") != g) or (nm and c.get("name") != nm):  # 추천 채널은 조사 때 한국어 이름으로 보여 줌 (YouTube 이름은 영어일 때가 있음)
            out[k] = dict(c, group=g or c.get("group"), name=nm or c.get("name"))
        if k == OWN:
            out[k] = dict(c, group=own["group"])
    return out


# ---------- 글자·숫자 도우미 ----------

def ko_ratio(t):
    letters = re.findall(r"[가-힣A-Za-z]", str(t or ""))
    return sum(1 for x in letters if "가" <= x <= "힣") / len(letters) if letters else 0.0


def is_ko(t):
    return ko_ratio(t) >= 0.3


def fmt_n(n):
    """1.2만 · 3,750 (화면 views() 와 같은 꼴)."""
    if n is None:
        return "—"
    n = float(n)
    if n >= 10000:
        x = n / 10000
        s = f"{x:.0f}" if n >= 100000 else f"{x:.1f}".rstrip("0").rstrip(".")
        return s + "만"
    return f"{int(round(n)):,}"


def _vv(xs):
    return [float(x) for x in xs if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]


def _med(xs):
    xs = list(xs)
    return statistics.median(xs) if xs else None


def _q(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo = math.floor(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _int(x):
    try:
        v = int(x)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def _day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d") if ts else None


def _url_of(vid, k):
    return f"https://www.youtube.com/shorts/{vid}" if k == "S" else f"https://www.youtube.com/watch?v={vid}"


# ---------- RSS · 목록 정리 ----------

def parse_rss(raw):
    """RSS 바이트 → {title, channelId, entries[{id, title, pub, shorts, views, likes}]}. 이상하면 ValueError(한국어)."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("RSS 형식이 아니에요")
    if len(raw) > RSS_MAX:
        raise ValueError("RSS 가 너무 커요")
    up = bytes(raw[:RSS_MAX]).upper()
    if b"<!DOCTYPE" in up or b"<!ENTITY" in up:  # XML 폭탄·외부 개체 대비: 문서 형식 선언이 있는 RSS 는 받지 않음
        raise ValueError("RSS 형식이 이상해요")
    try:
        doc = ET.fromstring(bytes(raw))
    except ET.ParseError:
        raise ValueError("RSS 형식이 깨졌어요") from None
    if doc.tag != "{%s}feed" % NS["a"]:
        raise ValueError("RSS 형식이 아니에요")
    entries, cid = [], None
    for e in doc.findall("a:entry", NS):
        vid = (e.findtext("yt:videoId", namespaces=NS) or "").strip()
        if not VID_RE.fullmatch(vid):
            continue
        c = (e.findtext("yt:channelId", namespaces=NS) or "").strip()
        if UC_RE.fullmatch(c):
            cid = cid or c
        link = e.find("a:link", NS)
        href = link.get("href") if link is not None else ""
        stats = e.find("m:group/m:community/m:statistics", NS)
        star = e.find("m:group/m:community/m:starRating", NS)
        entries.append({"id": vid, "title": re.sub(r"\s+", " ", e.findtext("a:title", namespaces=NS) or "").strip()[:300],
                        "pub": _iso(e.findtext("a:published", namespaces=NS)), "shorts": "/shorts/" in (href or ""),
                        "views": _int(stats.get("views")) if stats is not None else None,
                        "likes": _int(star.get("count")) if star is not None else None})
    if not cid:
        u = doc.findtext("a:author/a:uri", namespaces=NS) or ""
        m = UC_RE.search(u)
        cid = m.group(0) if m else None
    return {"title": (doc.findtext("a:title", namespaces=NS) or "").strip()[:200], "channelId": cid, "entries": entries}


def fetch_rss(cid):
    """채널 RSS 받기 → (정리한 RSS, None) 또는 (None, 이유). 404 는 '채널을 찾지 못했어요', 429·5xx 는 5초 뒤 한 번만 다시."""
    if not isinstance(cid, str) or not UC_RE.fullmatch(cid):
        return None, "채널 id 를 아직 몰라요"
    req = urllib.request.Request(RSS_URL.format(cid), headers={"User-Agent": f"futsal-studio/{core.VERSION}"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=RSS_TIMEOUT) as r:
                raw = r.read(RSS_MAX + 1)
            return parse_rss(raw), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "채널을 찾지 못했어요"
            if (e.code == 429 or e.code >= 500) and attempt == 1:
                _sleep(RSS_RETRY_WAIT)
                continue
            return None, f"RSS 응답 {e.code}"
        except ValueError as e:
            return None, str(e)
        except http.client.HTTPException:  # IncompleteRead 처럼 응답이 끊김 (OSError 가 아님)
            return None, "RSS 를 받지 못했어요"
        except OSError:
            return None, NET_MSG
    return None, "RSS 를 받지 못했어요"


DESC_FLAGS = {"lesson": re.compile(r"레슨|수업|클래스|아카데미|강습|원데이|코칭|lesson|class|academy", re.I),
              "contact": re.compile(r"문의|연락|contact|카톡|카카오|이메일|e-?mail|DM|인스타|instagram|@[\w.-]+\.\w+", re.I),
              "sponsor": re.compile(r"협찬|광고|비즈니스|business|sponsor|제휴|섭외", re.I)}


def desc_flags(desc):
    """채널 설명 → 표시만 (원문·연락처는 저장하지 않음)."""
    d = str(desc or "")
    return {k: bool(rx.search(d)) for k, rx in DESC_FLAGS.items()}


def normalize_flat(info, kind):
    """yt-dlp 목록 원본 → {meta{name, channelId, handle, subs, descFlags}, rows[{id, title, views, duration}]} (최신순 그대로)."""
    info = info if isinstance(info, dict) else {}
    ents = [e for e in info.get("entries") or [] if isinstance(e, dict)]
    tabs = [e for e in ents if e.get("_type") == "playlist"]
    if tabs:  # 채널 첫 화면처럼 탭이 묶여 오면 그 형식의 탭만 (없으면 전부 펼침)
        want = ("shorts", "쇼츠") if kind == "shorts" else ("videos", "동영상")
        pick = [t for t in tabs if any(w in (str(t.get("title") or "") + str(t.get("webpage_url") or t.get("url") or "")).lower() for w in want)]
        ents = [x for t in (pick or tabs) for x in t.get("entries") or [] if isinstance(x, dict)] + [e for e in ents if e.get("_type") != "playlist"]
    rows, seen = [], set()
    for e in ents:
        vid = e.get("id")
        if not isinstance(vid, str) or not VID_RE.fullmatch(vid) or vid in seen:
            continue
        seen.add(vid)
        v, d = e.get("view_count"), e.get("duration")
        rows.append({"id": vid, "title": re.sub(r"\s+", " ", str(e.get("title") or "")).strip()[:300],
                     "views": int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None,
                     "duration": float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0 else None})
    cid = info.get("channel_id")
    up = info.get("uploader_id")
    subs = info.get("channel_follower_count")
    meta = {"name": str(info.get("channel") or info.get("uploader") or "")[:100] or None,
            "channelId": cid if isinstance(cid, str) and UC_RE.fullmatch(cid) else None,
            "handle": up.lower()[:100] if isinstance(up, str) and up.startswith("@") else None,
            "subs": int(subs) if isinstance(subs, (int, float)) and not isinstance(subs, bool) and subs >= 0 else None,
            "descFlags": desc_flags(info.get("description")) if info.get("description") is not None else None}
    return {"meta": meta, "rows": rows}


# ---------- 새로 고침 ----------

def _new_channel(entry):
    return {"v": 1, "key": entry["key"], "name": entry.get("name"), "handle": entry.get("handle"), "url": entry.get("url"),
            "channelId": entry.get("channelId"), "group": entry.get("group"), "subs": None, "subsAt": None, "src": "live",
            "descFlags": None, "tabs": {"long": {"ids": []}, "shorts": {"ids": []}}, "videos": {}, "rss": None,
            "translated": False, "errors": [], "at": None, "listedAt": None}


def _tab_of(k):
    return "long" if k == "L" else "shorts"


def _merge_listing(data, parsed, tab, limit, full, now):
    meta = parsed["meta"]
    for f in ("name", "channelId", "handle"):
        if meta.get(f):
            data[f] = meta[f]
    if meta.get("subs") is not None:
        data["subs"], data["subsAt"] = meta["subs"], now
    if meta.get("descFlags") is not None:
        data["descFlags"] = meta["descFlags"]
    k = "L" if tab == "long" else "S"
    vids = data["videos"]
    listed = []
    for r in parsed["rows"]:
        v = vids.get(r["id"]) or {"k": k, "seen": now}
        v["k"] = k
        v["te"] = r["title"]
        if not v.get("o"):
            v["t"] = r["title"]
        if r["views"] is not None:
            v["v"], v["vAt"] = r["views"], now
        if r["duration"]:
            v["d"] = r["duration"]
        vids[r["id"]] = v
        listed.append(r["id"])
    t = data["tabs"].setdefault(tab, {"ids": []})
    old = t.get("ids") or []
    got = set(listed)
    reached_end = len(listed) < limit
    ids = listed if reached_end else listed + [i for i in old if i not in got]
    complete = reached_end or bool(t.get("complete") and (got & set(old)))
    t.update(ids=ids[:KEEP], complete=complete, n=len(ids) if complete else max(len(ids), int(t.get("n") or 0)), at=now)
    if full:
        t["fullAt"] = now
    if complete:
        t["sum"] = int(sum(vids[i].get("v") or 0 for i in ids if i in vids))
    else:
        t.pop("sum", None)


def _merge_rss(data, rss, now):
    vids = data["videos"]
    ids, fresh = [], {"long": [], "shorts": []}
    for e in rss["entries"]:
        k = "S" if e["shorts"] else "L"
        v = vids.get(e["id"]) or {"k": k, "seen": now}
        v["k"] = k
        if e["title"]:
            v["t"], v["o"] = e["title"], 1
        if e["views"] is not None:
            v["v"], v["vAt"] = e["views"], now
        if e["likes"] is not None:
            v["likes"] = e["likes"]
        if e["pub"]:
            v["pub"] = e["pub"]
        vids[e["id"]] = v
        ids.append(e["id"])
        t = data["tabs"].setdefault(_tab_of(k), {"ids": []})
        if e["id"] not in (t.get("ids") or []):
            fresh[_tab_of(k)].append(e["id"])  # 목록이 막혔을 때 RSS 로만 안 새 영상 → 최신순 그대로 앞에
    for tab, new in fresh.items():
        if new:
            t = data["tabs"][tab]
            t["ids"] = (new + (t.get("ids") or []))[:KEEP]
    if rss.get("channelId") and not data.get("channelId"):
        data["channelId"] = rss["channelId"]
    if rss.get("title") and not data.get("name"):
        data["name"] = rss["title"]
    data["rss"] = {"at": now, "ok": True, "error": None, "ids": ids}
    pairs = [(vids[i].get("t"), vids[i].get("te")) for i in ids if vids[i].get("te")]
    data["translated"] = any(is_ko(a) and not is_ko(b) for a, b in pairs) or bool(data.get("translated") and not pairs)


def _prune(data):
    keep = set()
    for t in data["tabs"].values():
        keep |= set(t.get("ids") or [])
    keep |= set((data.get("rss") or {}).get("ids") or [])
    data["videos"] = {k: v for k, v in data["videos"].items() if k in keep}


def detect_revived(ch):
    """90일 넘게 쉰 뒤 첫 업로드 시각 (가장 최근 것) · 모르면 None."""
    pubs = sorted(v["pub"] for v in (ch or {}).get("videos", {}).values() if isinstance(v.get("pub"), (int, float)))
    rev = None
    for a, b in zip(pubs, pubs[1:]):
        if b - a > REVIVE_GAP * 86400:
            rev = b
    return rev


def _history_rec(data, now, listed):
    """기록 한 줄: 구독자는 이번에 목록에서 새로 받았을 때만 (RSS 만 받은 날 예전 숫자를 오늘 날짜로 남기지 않음)."""
    vids = data["videos"]
    rec = {"k": data["key"], "at": now, "subs": data.get("subs") if listed and data.get("subsAt") == now else None, "listed": bool(listed),
           "rss": {i: vids[i].get("v") for i in (data.get("rss") or {}).get("ids") or [] if i in vids}}
    if listed:
        rec.update(n={}, sum={}, complete={})
        for k in ("L", "S"):
            t = data["tabs"].get(_tab_of(k)) or {}
            rec["n"][k] = t.get("n") or len(t.get("ids") or [])
            rec["sum"][k] = t.get("sum")
            rec["complete"][k] = bool(t.get("complete"))
    return rec


def listed_at(ch):
    """목록(구독자·조회수)을 마지막으로 받은 시각 · RSS 만 받은 날은 바뀌지 않음. 예전 기록은 탭 시각으로."""
    if not ch:
        return None
    if "listedAt" in ch:
        return ch["listedAt"]
    if ch.get("src") == "live":
        ats = [t.get("at") for t in (ch.get("tabs") or {}).values() if isinstance(t, dict) and isinstance(t.get("at"), (int, float))]
        return max(ats) if ats else ch.get("at")
    return None


def _paused(st, now=None):
    p = st.get("pause") or {}
    return isinstance(p.get("until"), (int, float)) and p["until"] > (now or time.time())


def _set_pause(reason, now, log=print):
    secs = FAIL_PAUSE if reason == "fails" else PAUSE_SECS
    try:
        _update_state(lambda d: d.__setitem__("pause", {"until": now + secs, "reason": reason, "at": now}))
    except OSError as e:  # 기록 파일이 잠겨도 새로 고침은 이어 감 (이번 새로 고침 안에서는 목록을 쉼)
        log(f"  쉬는 시간을 기록하지 못했어요 · {e}")


def clear_pause():
    """[지금 다시 시도]: 연달아 실패해서 쉬는 것만 풂 (YouTube 가 막은 6시간은 그대로)."""
    def put(d):
        p = d.get("pause") or {}
        if p.get("reason") in ("blocked", "rate") and isinstance(p.get("until"), (int, float)) and p["until"] > time.time():
            raise StrategyError("YouTube가 막아서 쉬는 중이에요. 정해진 시각이 지난 뒤 다시 눌러 주세요")
        d["pause"] = None
    _update_state(put)


def plan_refresh(keys=None, mode="normal", now=None, st=None):
    """새로 고칠 채널 [(항목, 지금 자료, 전체 훑기?)] · mode: normal(오래된 것만) · all(모두 다시) · own(우리 채널 기록) · check(점검).
    오래됨은 목록을 받은 시각으로 (RSS 만 받은 채널은 다시 받을 차례) · 목록을 쉬는 동안은 RSS 를 받은 시각으로.
    keys 로 고른 채널('이 채널만')은 10분 안에 받은 것만 건너뜀."""
    now = now or time.time()
    st = st or load_state()
    paused = _paused(st, now)
    entries = [own_entry(st)] + st["competitors"]
    if mode in ("own", "check"):
        entries = entries[:1]
    elif keys:
        ks = set(keys)
        entries = [e for e in entries if e["key"] in ks]
    todo = []
    for e in entries:
        cur = load_channel(e["key"], slim=True)
        last = ((cur or {}).get("at") if paused else listed_at(cur)) or 0
        if mode in ("own", "check") or (keys and mode == "normal"):
            gap = OWN_MIN_GAP
        elif mode == "all":
            gap = 0
        else:
            gap = OWN_STALE / 2 if e["key"] == OWN else STALE
        if gap and now - last < gap:
            continue
        lt = ((cur or {}).get("tabs") or {}).get("long") or {}
        full = not lt.get("fullAt") or now - lt["fullAt"] > FULL_EVERY
        todo.append((e, cur, full))
    return todo


def estimate(todo):
    secs = sum(SECS_FULL if full else SECS_NORMAL for _, _, full in todo) + GAP * max(0, len(todo) - 1)
    return {"n": len(todo), "secs": int(secs), "min": max(1, math.ceil(secs / 60)) if todo else 0}


def refresh(keys=None, mode="normal", log=print, cancel=None, label=JOB_REFRESH):
    """채널 숫자 새로 고치기 (작업 안에서). 채널 하나 끝날 때마다 저장 → 멈추거나 막혀도 끝난 채널은 남음.
    채널 하나에서 뜻밖의 오류가 나도 그 채널만 실패로 세고 이어 감. 인터넷이 끊긴 것 같으면 남은 채널은 다음에."""
    t0 = now = time.time()
    st = load_state()
    todo = plan_refresh(keys, mode, now, st)
    yt_ok = not _paused(st, now)
    if not yt_ok:
        log("  YouTube 목록은 잠시 쉬는 중이라 최근 날짜·조회수(RSS)만 새로 고쳐요")
    if not todo:
        log("  새로 고칠 채널이 없어요 (모두 최근에 받았어요)")
    done, failed, blocked, streak, net_n, net = [], [], False, 0, 0, False
    listed_n = rss_only = 0
    reason = None
    for i, (entry, cur, full) in enumerate(todo, 1):
        if cancel is not None and cancel.is_set():
            log("  멈췄어요 · 끝난 채널은 저장했어요")
            break
        if i > 1:
            _sleep(GAP + _jitter() * JITTER)
        name = (cur or {}).get("name") or entry.get("name") or entry["key"]
        if cur:
            cur = load_channel(entry["key"])  # 계획은 줄인 자료로 세웠으니 저장할 원본을 다시 읽음

        def pg(detail, pct_in=0.0, _i=i, _name=name):
            core.set_progress(label=label, item=_name, step=f"{_i}/{len(todo)}", pct=int(100 * (_i - 1 + pct_in) / len(todo)), detail=detail)
        try:
            res = _refresh_one(entry, cur, full and yt_ok, yt_ok, log, cancel, pg)
        except Exception as e:  # noqa: BLE001 — 채널 하나(저장 잠김·뜻밖의 응답)가 나머지를 막지 않게
            log(f"  {name} · 받지 못했어요 ({str(e)[:160]})")
            res = {"blocked": False, "ytFail": False, "saved": False, "net": False, "listed": False}
        if res["blocked"] and yt_ok:
            yt_ok, blocked, reason = False, True, "blocked"
            _set_pause("blocked", time.time(), log)
            log("  YouTube가 잠시 막았어요 · 남은 채널은 최근 날짜·조회수(RSS)만 새로 고칠게요")
        streak = streak + 1 if res["ytFail"] else 0
        if streak >= FAIL_STREAK and yt_ok:
            yt_ok, reason = False, "fails"
            _set_pause("fails", time.time(), log)
            log("  채널 목록을 연달아 못 받았어요 · 1시간 쉬고, 남은 채널은 RSS 만 새로 고칠게요")
        (done if res["saved"] else failed).append(entry["key"])
        if res["saved"]:
            listed_n += bool(res.get("listed"))
            rss_only += not res.get("listed")
        net_n = net_n + 1 if res.get("net") else 0
        if net_n >= NET_STREAK and i < len(todo):
            net = True
            log("  인터넷 연결이 끊긴 것 같아요 · 남은 채널은 다음에 새로 고칠게요")
            break
    core.set_progress(label=label, pct=100, detail="정리하는 중")
    # stopped: 이 작업 중에 멈추기(✕)를 눌렀음 — 휴대폰 '마지막 작업'·알림이 '끝났어요'가 아니라 '멈췄어요'로 (D-028)
    return {"ok": True, "done": len(done), "failed": len(failed), "todo": len(todo), "blocked": blocked, "net": net or (bool(todo) and net_n == len(todo)),
            "listed": listed_n, "rssOnly": rss_only, "paused": not yt_ok, "reason": reason, "secs": round(time.time() - t0, 1),
            "stopped": bool(cancel is not None and cancel.is_set())}


def _seed_for(entry, st=None):
    """함께 배포한 비교 데이터의 같은 채널 (처음 새로 고칠 때 출발점 · 목록을 못 받아도 조사 숫자가 사라지지 않게)."""
    sd = seed()["channels"]
    if entry["key"] == OWN:
        c = sd.get(OWN)
        return c if c and entry.get("channelId") and c.get("channelId") == entry["channelId"] else None
    c = sd.get(entry["key"])
    if c:
        return c
    cid = entry.get("channelId")
    return next((x for k, x in sd.items() if k != OWN and cid and x.get("channelId") == cid), None)


def _start_data(entry, cur):
    if cur:
        return _copy(cur)
    sd = _seed_for(entry)
    if not sd:
        return _new_channel(entry)
    data = _copy(sd)
    for t in (data.get("tabs") or {}).values():  # 조사 때 일부만 남긴 목록 → 처음 목록은 전체 훑기로 다시 받음
        if isinstance(t, dict):
            t.pop("fullAt", None)
            t.pop("at", None)
    data.update(listedAt=None, errors=[], seeded=True)
    return data


def _err_kind(msg):
    if msg == core.BLOCKED_MSG or core._blocked_text(msg) or RATE_ERR.search(msg):
        return "blocked"
    if NET_ERR.search(msg):
        return "net"
    if GONE_ERR.search(msg):
        return "gone"
    return "fail"


def _refresh_one(entry, cur, full, yt_ok, log, cancel, pg):
    now = time.time()
    data = _start_data(entry, cur)
    data.update(key=entry["key"], url=entry.get("url") or data.get("url"), v=1)
    if entry.get("group"):
        data["group"] = entry["group"]
    for f in ("channelId", "handle"):
        if entry.get(f) and not data.get(f):
            data[f] = entry[f]
    res = {"blocked": False, "ytFail": False, "saved": False, "net": False, "listed": False}
    errors, listed, net_list = [], False, False
    limit = FULL_LIMIT if full else LIMIT
    url = data.get("url") or (f"https://www.youtube.com/channel/{data['channelId']}" if data.get("channelId") else data.get("handle"))
    if yt_ok and url:
        for n, (kind, tab, lab) in enumerate((("videos", "long", "롱폼"), ("shorts", "shorts", "쇼츠"))):
            if cancel is not None and cancel.is_set():
                break
            pg(f"{lab} 목록 보는 중" + (" (처음이라 전체를 훑어요)" if full else ""), 0.1 + 0.35 * n)
            try:
                info = core.channel_listing(url, kind, limit, log, sleep_requests=REQ_SLEEP)
            except Exception as e:  # noqa: BLE001 — 채널 하나 실패해도 나머지는 계속
                msg = str(e)
                if NO_TAB.search(msg):
                    data["tabs"][tab] = {"ids": [], "complete": True, "n": 0, "sum": 0, "at": now, **({"fullAt": now} if full else {})}
                    listed = True
                    continue
                kind_ = _err_kind(msg)
                if kind_ == "blocked":
                    res["blocked"] = True
                    errors.append({"at": now, "what": "blocked"})
                    break
                if kind_ == "net":
                    net_list = True
                    errors.append({"at": now, "what": "net", "error": NET_MSG})
                    break
                if kind_ == "gone":
                    errors.append({"at": now, "what": lab, "error": "채널을 찾지 못했어요"})
                    break
                res["ytFail"] = True
                errors.append({"at": now, "what": lab, "error": msg[:200]})
                break
            _merge_listing(data, normalize_flat(info, kind), tab, limit, full, now)
            listed = True
    if cancel is not None and cancel.is_set() and not listed:
        return res
    rss_ok, net_rss = False, False
    if data.get("channelId"):
        pg("최근 15개 날짜·조회수 보는 중", 0.8)
        rss, err = fetch_rss(data["channelId"])
        if rss:
            _merge_rss(data, rss, now)
            rss_ok = True
        else:
            net_rss = err == NET_MSG
            data["rss"] = dict(data.get("rss") or {}, at=now, ok=False, error=err)
            errors.append({"at": now, "what": "rss", "error": err})
    if yt_ok and full and listed and data.get("translated") and not res["blocked"] and not (cancel is not None and cancel.is_set()):
        pg("한국어 원제 보는 중", 0.9)
        if _ko_titles(data, url, log, errors, now) == "blocked":
            res["blocked"] = True
    if listed:
        data["listedAt"] = now
    data["src"] = "live" if data.get("listedAt") or not data.get("seeded") else "seed"
    if not (listed or rss_ok):
        res["net"] = net_list or net_rss
        data["errors"] = (data.get("errors") or [])[-4:] + errors[-1:]
        if cur:  # 예전 자료는 그대로 두고 오류만 기록
            data["at"] = cur.get("at")
            save_channel(data)
        log(f"  {data.get('name') or entry['key']} · 받지 못했어요 ({(errors[-1].get('error') or errors[-1]['what']) if errors else '이유 모름'})")
        return res
    _prune(data)
    data["at"] = now
    data["errors"] = ((data.get("errors") or []) + errors)[-5:]
    save_channel(data)
    res.update(saved=True, listed=listed)
    try:
        append_history(_history_rec(data, now, listed))
        if entry["key"] == OWN:
            _after_own(data)
    except OSError as e:  # 기록 줄·우리 채널 정보를 못 남겨도 채널 자료는 저장됨
        log(f"  {data.get('name') or entry['key']} · 기록을 남기지 못했어요 ({e})")
    nL = data["tabs"].get("long", {}).get("n", 0)
    nS = data["tabs"].get("shorts", {}).get("n", 0)
    log(f"  {data.get('name') or entry['key']} · 롱폼 {nL}개 · 쇼츠 {nS}개" + (" · 최근 날짜 ✓" if rss_ok else "") + (" · 목록은 받지 못함" if not listed else ""))
    return res


def _ko_titles(data, url, log, errors, now):
    """번역 제목이 섞인 채널: 원제가 빠진 최근 영상만 한국어 목록으로 채움 (조회수는 기본 목록 것 그대로).
    YouTube 가 막았으면 'blocked' (새로 고침이 바로 쉬게 · 같은 새로 고침에서 엔진 최신화를 또 하지 않게)."""
    vids = data["videos"]
    for kind, tab in (("videos", "long"), ("shorts", "shorts")):
        miss = {i for i in (data["tabs"].get(tab, {}).get("ids") or [])[:KO_MAX] if i in vids and not vids[i].get("o") and not is_ko(vids[i].get("t"))}
        if not miss:
            continue
        try:
            info = core.channel_listing(url, kind, KO_MAX, log, lang="ko", sleep_requests=REQ_SLEEP)
        except Exception as e:  # noqa: BLE001 — 원제는 곁가지 (실패해도 번역 제목으로 계속)
            msg = str(e)
            errors.append({"at": now, "what": "ko", "error": msg[:200]})
            return "blocked" if _err_kind(msg) == "blocked" else None
        for r in normalize_flat(info, kind)["rows"]:
            if r["id"] in miss and is_ko(r["title"]):
                vids[r["id"]]["t"], vids[r["id"]]["o"] = r["title"], 1
    return None


def _after_own(data):
    """우리 채널: 이름·채널 id 기억, 다시 시작한 날 자동 찾기 (사용자가 고친 날은 그대로)."""
    rev = detect_revived(data)

    def put(d):
        o = d["own"]
        if data.get("name"):
            o["name"] = data["name"]
        if data.get("channelId"):
            o["channelId"] = data["channelId"]
        if data.get("handle"):
            o["handle"] = data["handle"]
        if rev and not o.get("revivedManual"):
            o["revivedAt"] = rev
    _update_state(put)


# ---------- 채널 통계 ----------

def cadence(dated, now):
    """RSS 날짜 [(시각, 'L'|'S')] → 주당 횟수(전체·형식별)·마지막 업로드·간격 고르기(CV)·활동 상태. 3개 미만이면 None."""
    if len(dated) < 3:
        return None
    pubs = sorted((p for p, _ in dated), reverse=True)
    oldest = pubs[-1]
    span = min(90.0, max(7.0, (now - oldest) / 86400))
    in90 = [(p, k) for p, k in dated if now - p <= 90 * 86400]
    per = {"all": round(len(in90) / (span / 7), 2)}
    for k in ("L", "S"):
        per[k] = round(sum(1 for _, kk in in90 if kk == k) / (span / 7), 2)
    last = (now - pubs[0]) / 86400
    gaps = [(a - b) / 86400 for a, b in zip(pubs, pubs[1:])]
    cv = None
    if len(gaps) >= 3 and statistics.mean(gaps) > 0:
        cv = round(statistics.pstdev(gaps) / statistics.mean(gaps), 2)
    act = next((lab for d, lab in ACTIVE if last <= d), "멈춤")
    return {"perWeek": per, "atLeast": len(dated) >= 15 and (now - oldest) <= 90 * 86400, "lastDays": int(last), "cv": cv, "activity": act}


def channel_stats(ch, now=None):
    """채널 하나의 숫자 (화면·가능성 계산 공통)."""
    now = now or time.time()
    vids = ch.get("videos") or {}
    tabs = ch.get("tabs") or {}
    subs = ch.get("subs") if isinstance(ch.get("subs"), (int, float)) else None
    out = {"key": ch.get("key"), "name": ch.get("name"), "group": ch.get("group"), "subs": subs, "src": ch.get("src") or "live",
           "at": ch.get("at"), "listedAt": listed_at(ch), "url": ch.get("url"), "handle": ch.get("handle"), "channelId": ch.get("channelId"),
           "translated": bool(ch.get("translated")), "descFlags": ch.get("descFlags") or {}}
    for k in ("L", "S"):
        t = tabs.get(_tab_of(k)) or {}
        ids = [i for i in t.get("ids") or [] if i in vids]
        rec = [dict(vids[i], id=i) for i in ids[:RECENT]]
        vs = _vv(r.get("v") for r in rec)
        d = {"n": int(t.get("n") or len(ids)) if t.get("complete") else int(t.get("nIds") or len(ids)), "complete": bool(t.get("complete")), "n30": len(vs)}
        if vs:
            d.update(median=round(_med(vs)), mean=round(statistics.mean(vs)), p90=round(_q(vs, 0.9)),
                     median15=round(_med(_vv(r.get("v") for r in rec[:15])) or 0) or None,
                     top=[{"id": r["id"], "t": r.get("t") or r.get("te") or "", "v": r["v"], "url": _url_of(r["id"], k)}
                          for r in sorted((r for r in rec if _vv([r.get("v")])), key=lambda r: -r["v"])[:3]])
        if k == "L":
            ds = [r["d"] for r in rec if isinstance(r.get("d"), (int, float)) and r["d"] > 0]
            d["dur"] = round(_med(ds)) if ds else None
        d["reach"] = round(d["median"] / subs, 2) if subs and d.get("median") else None
        d["sum"] = t.get("sum") if t.get("complete") else None
        out[k] = d
    rss_ids = [i for i in (ch.get("rss") or {}).get("ids") or [] if i in vids]
    rs = [vids[i] for i in rss_ids]
    nL, nS = out["L"]["n"], out["S"]["n"]
    sL = sum(_vv(vids[i].get("v") for i in (tabs.get("long") or {}).get("ids", [])[:RECENT] if i in vids))
    sS = sum(_vv(vids[i].get("v") for i in (tabs.get("shorts") or {}).get("ids", [])[:RECENT] if i in vids))
    out["shortsShare"] = {"rss": round(sum(1 for v in rs if v.get("k") == "S") / len(rs), 2) if len(rs) >= 5 else None,
                          "count": round(nS / (nL + nS), 2) if nL + nS and out["L"]["complete"] and out["S"]["complete"] else None,
                          "views": round(sS / (sL + sS), 2) if sL + sS else None}
    dated = [(v["pub"], v.get("k")) for v in rs if isinstance(v.get("pub"), (int, float))]
    out["cadence"] = cadence(dated, now)
    likes = [v["likes"] / v["v"] for v in rs if isinstance(v.get("likes"), (int, float)) and _vv([v.get("v")]) and v["v"] >= 50]
    out["likeRatio"] = round(_med(likes), 4) if likes else None
    out["life"] = {"L": out["L"]["sum"], "S": out["S"]["sum"], "complete": bool(out["L"]["complete"] and out["S"]["complete"]
                                                                                 and out["L"]["sum"] is not None and out["S"]["sum"] is not None),
                   "nL": nL}
    out["rssOk"] = bool((ch.get("rss") or {}).get("ok"))
    out["errors"] = (ch.get("errors") or [])[-1:]
    return out


# ---------- 제목 패턴 찾기 ----------

TOPIC_EXTRA = ["팬텀", "드래그", "라크로케타", "피보 턴", "마르세유", "국가대표", "1대1", "골키퍼", "풋살화", "축구화",
               "헛다리", "방향전환", "페인팅", "넛메그", "기본기", "포지션", "롭 패스", "바디페인팅", "움직임", "전술", "체크백", "킥 연습",
               "슬립백", "엘라스티코", "오버래핑", "세컨볼", "골레이로"]
_TOPIC_RES = [(t, hooks._term_re(t)) for t in sorted(dict.fromkeys(hooks.TERMS + TOPIC_EXTRA), key=lambda x: -len(x))]
STOP_EXTRA = {"쇼츠", "shorts", "영상", "채널", "이번", "오늘", "모든", "진짜", "그냥", "하는", "있는", "없는", "되는", "이것", "이거",
              "무조건", "바로", "완벽", "정리", "총정리", "방법", "꿀팁", "이유", "모음", "몰아보기", "실전", "기술"}
# 주제가 아닌 말 (이력·대상·플랫폼·흔한 말): '‘출신’ 주제를 다뤄 봐요' 같은 쓸모없는 가져올 점이 나오지 않게
TOPIC_STOP = {"출신", "선출", "비선출", "레벨", "플랩", "프로", "선수", "선수들", "감독", "코치", "레전드", "현역", "은퇴", "세미", "세미프로",
              "아마추어", "동호인", "초보", "입문", "초등", "유소년", "경기", "하이라이트", "브이로그", "리뷰", "인터뷰", "대회", "리그", "결승",
              "축구", "풋살", "유튜브", "구독", "구독자", "좋아요", "댓글", "공유", "편", "탄", "시즌", "마지막", "처음", "최초", "역대", "최고",
              "최악", "레알", "실화", "충격", "반응", "근황", "사람", "친구", "형", "누나", "동생", "우리", "여러분", "선생님", "참가", "도전자",
              "과연", "역시", "드디어", "정말", "제일", "가장", "모두", "다시", "직접", "요즘", "오늘의", "이번엔"}
FORMULAS = [
    ("question", "질문형", re.compile(r"\?|？|까\s*$|까[!.~]|나요|을까|할까|될까")),
    ("number", "숫자·목록형", re.compile(r"\d+\s?가지|\d+\s?개|TOP\s?\d+|\d+\s?분", re.I)),
    ("auth", "권위형", re.compile(r"국가대표|국대|前|現|(?:^|\s)전\s|(?:^|\s)현\s|감독|코치|득점왕|레전드")),  # '비선출'·'프로선수들의 ~'는 이력이 아님
    ("vs", "대결형", re.compile(r"\bvs\b|대결|1\s?:\s?1|1대1|맞대결", re.I)),
    ("howto", "방법·꿀팁형", re.compile(r"하는\s?법|방법|꿀팁|팁|비법")),
    ("why", "이유형", re.compile(r"이유|왜")),
    ("series", "시리즈 표시", re.compile(r"\[[^\]]+\]|EP\.?\s?\d+|\d+\s?탄|시즌\s?\d+", re.I)),
    ("compile", "모음·총정리", re.compile(r"모음|몰아보기|총정리|완전정복|정리")),
    ("target", "대상 지정", re.compile(r"초보|입문|초등|유소년|아마추어|동호인")),  # 플랩·레벨은 경기 브이로그 제목에도 흔해 대상 지정으로 보지 않음
    ("promise", "효익 약속", re.compile(r"무조건|바로|끝|99%|완벽")),
    ("engage", "참여 유도", re.compile(r"맞춰|몇\s?번|댓글|저장|공유|보여줘|퀴즈")),
    ("emph", "강조 기호", re.compile("!|ㄷㄷ|ㅋㅋ|[\U0001F300-\U0001FAFF☀-➿]")),
    ("hashtag", "해시태그", re.compile(r"#[0-9A-Za-z가-힣_]")),
    ("quote", "따옴표 강조", re.compile(r"[\"“”'‘’][^\"“”'‘’]{1,15}[\"“”'‘’]")),
    ("short", "짧은 제목(20자 이하)", None),
    ("long", "긴 제목(40자 이상)", None),
]
FORMULA_LABEL = {f[0]: f[1] for f in FORMULAS}
FORMULA_RX = {f[0]: f[2] for f in FORMULAS}


def _clean_t(t):
    return re.sub(r"\s+", " ", re.sub(r"#\S+", "", str(t or ""))).strip(" |ㅣ-")


@functools.lru_cache(maxsize=2048)
def _clean_len(t):
    return len(_clean_t(t))


def _fhit(fid_, rx, t):
    if fid_ == "short":
        return _clean_len(t) <= 20
    if fid_ == "long":
        return _clean_len(t) >= 40
    return bool(rx.search(t))


AGE_WIN = 5  # 목록에서 앞뒤 5편과 견줌 (오래된 영상일수록 조회가 더 쌓여 있어서 · 목록은 최신순)


def _resid(vals):
    """최신순 조회수 → 앞뒤 AGE_WIN 편(자기 빼고)의 ln 중앙값과의 차이: '비슷한 때 올린 영상보다 몇 배'의 ln."""
    lv = [math.log(v) for v in vals]
    out = []
    for i, x in enumerate(lv):
        nb = lv[max(0, i - AGE_WIN):i] + lv[i + 1:i + 1 + AGE_WIN]
        out.append(x - _med(nb) if nb else 0.0)
    return out


def formula_stats(rows):
    """[(제목, 조회수)] (최신순) → {공식: {share, mult, n, ok}} — mult = 쓴 영상 / 안 쓴 영상, 각각 비슷한 때 올린 영상 대비
    (오래된 영상에 조회가 더 쌓인 것을 빼려고 목록 앞뒤 5편과 견줌) · 각각 3개 이상이면 '근거 있음'."""
    rows = [(t, v) for t, v in rows if t and _vv([v])]
    out = {}
    if len(rows) < 6:
        return out
    rs = _resid([v for _, v in rows])
    for f_id, lab, rx in FORMULAS:
        w = [r for (t, _), r in zip(rows, rs) if _fhit(f_id, rx, t)]
        wo = [r for (t, _), r in zip(rows, rs) if not _fhit(f_id, rx, t)]
        mult = math.exp(_med(w) - _med(wo)) if w and wo else None
        out[f_id] = {"share": round(len(w) / len(rows), 2), "mult": round(mult, 2) if mult else None, "n": len(w), "ok": len(w) >= 3 and len(wo) >= 3}
    return out


# 풀이말 끝 (형태소 분석 없이): 이렇게 끝나는 낱말은 주제어로 보지 않음 ('싶으면'·'쓰는'·'보고'·'쉽게'·'만드는')
_VERB_END = re.compile(r"(?:으면|면|는데|는|던|게|고|서|며|요|다|까|죠|네|자|해|한|할|된|될|하|되|지|니|나|야|려|러|도록|듯|세요|시|ㄴ)$")


def _tokens(t):
    """제목의 한글 낱말 (해시태그·[시리즈 이름] 안은 빼고)."""
    return re.findall(r"[가-힣]{2,10}", re.sub(r"[\[【][^\]】]*[\]】]", " ", _clean_t(t)))


@functools.lru_cache(maxsize=2048)
def _title_parts(t):
    """제목 하나의 풋살 용어·(낱말, 조사 뗀 꼴) — 한 채널을 볼 때 같은 제목을 두 번 나누지 않게 (작게 기억 · 첫 글자가 없으면 정규식을 돌리지 않음)."""
    ct = _clean_t(t)
    terms = frozenset(term for term, rx in _TOPIC_RES if term[0] in ct and rx.search(ct))
    return terms, tuple((raw, hooks._strip_josa(raw)) for raw in _tokens(t))


def _solid(titles):
    """'는·은'으로 끝난 낱말은 풀이말('만드는')일 수 있어, 같은 줄기가 다른 꼴(그대로·다른 조사)로도 나온 것만 이름말로 봄."""
    out = set()
    for t in titles:
        for raw, w in _title_parts(str(t or ""))[1]:
            if w == raw or not raw.endswith(("는", "은")):
                out.add(w)
    return out


def _words(t, solid=None):
    """제목의 주제어 (풋살 용어 + 조사를 뗀 한글 이름말 · 풀이말 끝은 뺌)."""
    terms, toks = _title_parts(str(t or ""))
    found = set(terms)
    for raw, w in toks:
        if w == raw and _VERB_END.search(raw):
            continue
        if w != raw and raw.endswith(("는", "은")) and (solid is None or w not in solid):
            continue
        if 2 <= len(w) <= 6 and w not in hooks.STOP and w not in STOP_EXTRA and not hooks._VERBISH.search(w) and not _VERB_END.search(w):
            found.add(w)
    return found


def topic_stats(rows, min_free=3):
    """[(제목, 조회수)] (최신순) → [{w, n, mult}] (용어는 2번, 그냥 낱말은 3번 이상) · mult = 그 낱말이 든 영상이 비슷한 때 올린 영상보다 몇 배
    · 주제가 아닌 말(TOPIC_STOP)은 뺌."""
    rows = [(t, v) for t, v in rows if t and _vv([v])]
    if len(rows) < 5:
        return []
    rs = _resid([v for _, v in rows])
    allm = _med(rs)
    terms = {t for t, _ in _TOPIC_RES}
    solid = _solid(t for t, _ in rows)
    hits = {}
    for (t, v), r in zip(rows, rs):
        for w in _words(t, solid):
            if w in TOPIC_STOP:
                continue
            hits.setdefault(w, []).append(r)
    out = []
    for w, lv in hits.items():
        if len(lv) < (2 if w in terms else min_free) or len(lv) >= len(rows):
            continue
        out.append({"w": w, "n": len(lv), "mult": round(math.exp(_med(lv) - allm), 2), "term": w in terms})
    out.sort(key=lambda x: (-(x["mult"] * math.log(1 + x["n"])), x["w"]))
    return out[:20]


_SERIES_NUM = re.compile(r"\s*(?:EP\.?\s?\d+|#?\d+\s?탄|시즌\s?\d+|\d+)\s*$", re.I)


def _series_name(raw):
    """'패스 어디까지 해봤니 (' → '패스 어디까지 해봤니' (닫히지 않은 괄호·따옴표 뒤는 버림)."""
    nm = raw.strip(" \"“”'‘’")
    for o, c in (("(", ")"), ("[", "]"), ("（", "）"), ("【", "】")):
        if nm.count(o) > nm.count(c):
            nm = nm[:nm.rfind(o)]
    return nm.strip(" \"“”'‘’-·|:")


def series_stats(rows):
    """[(제목, 조회수, id)] (최신순) → 시리즈·코너 [{name, n, median, mult, top{id,t,v}}] (2편 이상) · mult 는 비슷한 때 올린 영상 대비."""
    rows = [(t, v, i) for t, v, i in rows if t]
    vrows = [(i, v) for t, v, i in rows if _vv([v])]
    rmap = dict(zip((i for i, _ in vrows), _resid([v for _, v in vrows]))) if len(vrows) >= 3 else {}
    groups = {}
    for t, v, i in rows:
        names = set()
        for m in re.finditer(r"[\[【]([^\]】]{1,24})[\]】]", t):
            nm = _SERIES_NUM.sub("", m.group(1)).strip()
            if len(nm) >= 2:
                names.add(f"[{nm}]")
        m = re.search(r"([가-힣A-Za-z][^\[\]|:#]{1,14}?)\s*[\"“']?\s*\d+\s?탄", t)
        if m and len(_series_name(m.group(1))) >= 2:
            names.add(_series_name(m.group(1)) + " N탄")
        m = re.match(r"^\s*([^|:ㅣ\[\]#]{2,20}?)\s*[|:ㅣ]", t)
        if m:
            names.add(m.group(1).strip() + " |")
        for nm in names:
            if len(re.findall(r"[가-힣A-Za-z]", nm)) >= 2:  # 기호뿐인 머리말('??? |')은 시리즈가 아님
                groups.setdefault(nm, []).append((t, v, i))
    out = []
    for nm, items in groups.items():
        if len(items) < 2:
            continue
        iv = _vv(v for _, v, _ in items)
        med = _med(iv) if iv else None
        rr = [rmap[i] for _, _, i in items if i in rmap]
        top = max(items, key=lambda x: x[1] or 0)
        out.append({"name": nm, "n": len(items), "median": round(med) if med else None,
                    "mult": round(math.exp(_med(rr)), 2) if rr else None, "top": {"id": top[2], "t": top[0], "v": top[1]}})
    out.sort(key=lambda x: (-x["n"], -(x["mult"] or 0)))
    return out[:8]


GENERIC_TAGS = {"shorts", "short", "풋살", "축구", "football", "soccer", "futsal", "쇼츠", "유튜브", "youtube"}


def fixed_hashtags(titles):
    """쇼츠 제목의 60% 이상에 붙은 해시태그 (쇼츠 5개 이상일 때) — 채널 이름 같은 고유 태그 먼저."""
    titles = [t for t in titles if t]
    if len(titles) < 5:
        return []
    cnt = {}
    for t in titles:
        for tag in set(re.findall(r"#([0-9A-Za-z가-힣_]{2,30})", t)):
            cnt[tag] = cnt.get(tag, 0) + 1
    out = [{"tag": "#" + k, "n": c, "of": len(titles), "generic": k.lower() in GENERIC_TAGS} for k, c in cnt.items() if c >= 0.6 * len(titles)]
    out.sort(key=lambda x: (x["generic"], -x["n"]))
    return out[:4]


def mine(ch):
    """채널 하나의 패턴 (한국어 제목만: RSS·한국어 원제 · 한글 비율 30% 이상)."""
    vids = ch.get("videos") or {}
    tabs = ch.get("tabs") or {}
    out = {"L": {}, "S": {}, "series": [], "hashtags": [], "nKo": 0}
    allrows = []
    for k in ("L", "S"):
        ids = [i for i in (tabs.get(_tab_of(k)) or {}).get("ids") or [] if i in vids][:120]
        rows = [(vids[i].get("t"), vids[i].get("v"), i) for i in ids if is_ko(vids[i].get("t"))]
        out["nKo"] += len(rows)
        out[k] = {"formulas": formula_stats([(t, v) for t, v, _ in rows]), "topics": topic_stats([(t, v) for t, v, _ in rows]), "n": len(rows)}
        allrows += [(t, v, i, k) for t, v, i in rows]
        if k == "S":
            out["hashtags"] = fixed_hashtags([vids[i].get("t") or vids[i].get("te") for i in ids[:30]])
    for k in ("L", "S"):
        out["series"] += [dict(s, k=k) for s in series_stats([(t, v, i) for t, v, i, kk in allrows if kk == k])]
    out["series"].sort(key=lambda x: (-x["n"], -(x["mult"] or 0)))
    out["series"] = out["series"][:8]
    return out


# ---------- 분석 묶음 (캐시) ----------

def _ana(ch, now):
    key = (ch.get("key"), ch.get("at"), ch.get("src"), ch.get("subs"), ch.get("group"), len(ch.get("videos") or {}), int(now // 3600))
    hit = _ANA.get(ch.get("key"))
    if hit and hit[0] == key:
        return hit[1]
    res = {"stats": channel_stats(ch, now), "mine": mine(ch)}
    _ANA[ch.get("key")] = (key, res)
    return res


def analyze_all(st=None, now=None):
    """알고 있는 채널 전부 → {열쇠: {stats, mine}}."""
    now = now or time.time()
    st = st or load_state()
    return {k: _ana(c, now) for k, c in known_channels(st).items()}


def group_summary(ana):
    """분류마다: 채널 수·보통 구독자·형식별 보통 조회수·구독자 대비 조회·쇼츠 비율·주기 중앙값 + '이 분류에서 잘 되는 공식'."""
    out = {}
    for g in GROUPS:
        xs = [a for k, a in ana.items() if k != OWN and a["stats"].get("group") == g]
        st = [a["stats"] for a in xs]

        def med(vals):
            v = [x for x in vals if isinstance(x, (int, float))]
            return _med(v) if v else None
        good = {}
        for a in xs:
            for k in ("L", "S"):
                for f_id, fs in (a["mine"].get(k, {}).get("formulas") or {}).items():
                    if fs.get("ok") and (fs.get("mult") or 0) >= 1.3:
                        good.setdefault(("f", f_id), set()).add(a["stats"]["key"])
                for tp in a["mine"].get(k, {}).get("topics") or []:
                    if tp["mult"] >= 1.3 and tp["n"] >= 2:
                        good.setdefault(("t", tp["w"]), set()).add(a["stats"]["key"])
        works = sorted(((kind, name, len(ks)) for (kind, name), ks in good.items() if len(ks) >= 2), key=lambda x: (-x[2], x[1]))
        out[g] = {"n": len(xs), "subs": med(s["subs"] for s in st),
                  "L": med(s["L"].get("median") for s in st), "S": med(s["S"].get("median") for s in st),
                  "reachL": med(s["L"].get("reach") for s in st), "reachS": med(s["S"].get("reach") for s in st),
                  "shortsShare": med((s["shortsShare"].get("rss") if s["shortsShare"].get("rss") is not None else s["shortsShare"].get("count")) for s in st),
                  "perWeek": med(((s["cadence"] or {}).get("perWeek") or {}).get("all") for s in st),
                  "likeRatio": med(s.get("likeRatio") for s in st),
                  "likeP75": _q([s["likeRatio"] for s in st if s.get("likeRatio")], 0.75),
                  "reachSP75": _q([s["S"]["reach"] for s in st if s["S"].get("reach")], 0.75),
                  "reachLP75": _q([s["L"]["reach"] for s in st if s["L"].get("reach")], 0.75),
                  "works": [{"kind": kind, "label": FORMULA_LABEL.get(name, name) if kind == "f" else f"‘{name}’ 주제", "id": name, "channels": n}
                            for kind, name, n in works[:6]]}
    return out


def cv_words(cv):
    """올리는 간격의 변동계수 → 쉬운 말 (작을수록 규칙적)."""
    if cv is None:
        return None
    return "간격이 꽤 규칙적" if cv < 0.5 else "간격이 조금 들쭉날쭉" if cv < 1.0 else "간격이 들쭉날쭉"


def strengths(s, m, gs):
    """강점 문장 (같은 분류 가운데 값과 비교 · 숫자를 붙임)."""
    g = gs.get(s.get("group")) or {}
    out = []
    for k, lab in (("S", "쇼츠가"), ("L", "롱폼이")):
        r, p75 = s[k].get("reach"), g.get(f"reach{k}P75")
        if r and p75 and r >= p75:
            out.append(f"{lab} 구독자 수에 비해 잘 보여요 (분류 상위 25% · 구독자의 {r:g}배 · 분류 가운데 값 {g.get('reach' + k) or 0:.2g}배)")
    c = s.get("cadence") or {}
    pw = (c.get("perWeek") or {}).get("all")
    if pw and pw >= 2 and (c.get("cv") is None or c["cv"] < 0.8):
        out.append(f"주 {pw:.1f}회 꾸준히 올려요" + (f" ({cv_words(c['cv'])})" if c.get("cv") is not None else ""))
    for se in m.get("series") or []:
        if (se.get("mult") or 0) >= 1.5 and se["n"] >= 3:
            out.append(f"‘{se['name']}’ {se['n']}편이 비슷한 때 올린 영상의 {se['mult']:g}배")
            break
    lr, lp = s.get("likeRatio"), g.get("likeP75")
    if lr and lp and lr >= lp:
        out.append(f"좋아요 비율 {lr * 100:.1f}% (분류 상위 25%)")
    if (s["S"].get("reach") or 0) >= 1 and not any(x.startswith("쇼츠가") for x in out):
        out.append(f"쇼츠를 구독자 수보다 많이 봐요 ({s['S']['reach']:g}배)")
    if s["L"].get("dur") and s["L"]["dur"] <= 480 and (s["L"].get("reach") or 0) >= (g.get("reachL") or 9):
        out.append(f"롱폼을 {s['L']['dur'] // 60}분 안팎으로 짧게 만들고, 구독자 수에 비해 잘 보여요 ({s['L']['reach']:g}배)")
    return out


def learned_styles():
    """배운 스타일 → {채널 열쇠: [{name, headline, captions, fun, apply}]} (학습용 영상 기록의 채널 열쇠로 연결)."""
    try:
        import style  # 지연 import: style → plan → core 순환을 피함
        lst = style.list_styles()
    except Exception:  # noqa: BLE001 — 스타일을 못 읽어도 전략 화면은 그대로
        return {}
    files = (refs.load().get("files") or {})
    out = {}
    for stl in lst:
        prof, pl = stl.get("profile") or {}, stl.get("plan") or {}
        srcs = prof.get("source")
        srcs = srcs if isinstance(srcs, list) else [srcs]
        keys = {(files.get(n) or {}).get("channelKey") for n in srcs if isinstance(n, str)} - {None}
        info = {"name": stl["name"], "headline": pl.get("headline") or stl.get("plan_desc") or stl.get("desc"),
                "captions": (pl.get("captions") or {}).get("label"), "fun": (pl.get("fun") or {}).get("label"),
                "apply": [a.get("tip") for a in pl.get("apply") or [] if isinstance(a, dict) and a.get("tip")][:3]}
        for k in keys:
            out.setdefault(k, []).append(info)
    return out


def _rec_index():
    idx = {}
    for c in recommended().get("channels") or []:
        for k in (_rec_key(c), c.get("channelId"), "h:" + str(c.get("handle") or "").lower()):
            if k:
                idx[k] = c
    return idx


def _style_for(s, styles):
    for k in (s.get("key"), s.get("channelId"), "h:" + str(s.get("handle") or "").lower()):
        if k and k in styles:
            return styles[k]
    return []


def _ratio_words(a, b):
    """a 가 b 의 몇 분의 일·몇 배인지 쉬운 말."""
    if not a or not b:
        return ""
    x = a / b
    return f"약 {x:.1f}배" if x >= 1.15 else "비슷해요" if x >= 0.87 else f"약 1/{max(2, round(1 / x))}"


def _gap_lines(own, gs):
    """우리와 같은 분류 가운데 값의 차이 (쉬운 문장)."""
    grp = own.get("group") or OWN_GROUP
    g = gs.get(grp) or {}
    out = []
    for k, lab in (("S", "쇼츠가"), ("L", "롱폼이")):
        r, gr = own[k].get("reach"), g.get("reach" + k)
        if r is not None and gr:
            good = r >= gr
            out.append({"text": f"우리 {lab} 구독자 수에 비해 {'잘' if good else '덜'} 보여요 (구독자 대비 {r:g}배 · {grp} 가운데 값 {gr:.2g}배 · "
                                f"{_ratio_words(r, gr)})", "good": good})
    pw = ((own.get("cadence") or {}).get("perWeek") or {}).get("all")
    if g.get("perWeek"):
        out.append({"text": f"우리 업로드 주 {pw or 0:.1f}회 · {grp} 가운데 값 주 {g['perWeek']:.1f}회", "good": (pw or 0) >= g["perWeek"]})
    return out


# ---------- 가져올 점 (규칙 분류기) ----------

RULES = {  # 규칙 → (분류, 품, 방향 태그)
    "R-SERIES": ("시리즈·코너", "보통", {"시리즈"}),
    "R-COMPILE": ("기획·형식", "쉬움", {"총정리", "쇼츠"}),
    "R-VS": ("기획·형식", "큼", {"대결", "챌린지"}),
    "R-DURATION": ("기획·형식", "쉬움", {"레슨"}),
    "R-TOPIC": ("기획·형식", "쉬움", {"레슨", "주제"}),
    "R-AUTH": ("썸네일·제목", "쉬움", {"권위", "제목"}),
    "R-QUESTION": ("썸네일·제목", "쉬움", {"제목", "예능"}),
    "R-NUMBER": ("썸네일·제목", "쉬움", {"제목", "총정리"}),
    "R-HOWTO": ("썸네일·제목", "쉬움", {"제목", "레슨"}),
    "R-TARGET": ("썸네일·제목", "쉬움", {"제목", "입문"}),
    "R-TITLELEN": ("썸네일·제목", "쉬움", {"제목"}),
    "R-STYLE-PLAN": ("편집 스타일", "보통", {"예능", "편집"}),
    "R-HASHTAG": ("쇼츠 운영", "쉬움", {"쇼츠"}),
    "R-SHORTS-RATIO": ("쇼츠 운영", "보통", {"쇼츠"}),
    "R-SHORTS-FROM-LONG": ("쇼츠 운영", "쉬움", {"쇼츠", "레슨"}),
    "R-CADENCE": ("업로드 주기", "보통", {"주기"}),
    "R-PAUSE-RISK": ("업로드 주기", "보통", {"주기"}),
    "R-ENGAGE-Q": ("시청자 참여", "쉬움", {"참여", "쇼츠"}),
    "R-ENGAGE-SAVE": ("시청자 참여", "쉬움", {"참여", "레슨"}),
    "R-LIKE": ("시청자 참여", "쉬움", {"참여"}),
    "R-LESSON-LINK": ("수익화·레슨 연계", "쉬움", {"레슨", "수익"}),
    "R-LESSON-CONTENT": ("수익화·레슨 연계", "보통", {"레슨", "수익"}),
    "R-RESEARCH-TIP": (None, "보통", set()),
}
TITLE_RULES = {"auth": ("R-AUTH", "이력을 앞세운 권위형 제목을 써요 (예: ‘前 국가대표 감독이 알려주는 ~’)"),
               "question": ("R-QUESTION", "짧은 질문형 제목·썸네일 문구를 써요 (예: ‘이 터치 가능?’)"),
               "number": ("R-NUMBER", "숫자를 넣은 제목을 써요 (예: ‘드리블 3가지’, ‘1분 만에’)"),
               "howto": ("R-HOWTO", "‘~하는 법·꿀팁’처럼 무엇을 얻는지 보이는 제목을 써요"),
               "target": ("R-TARGET", "대상을 콕 집은 제목을 써요 (예: ‘풋살 초보라면’, ‘플랩 레벨업’)"),
               "short": ("R-TITLELEN", "제목을 20자 안쪽으로 짧게 써요"),
               "compile": ("R-COMPILE", "예전 영상을 묶은 ‘총정리·몰아보기’ 편을 만들어요"),
               "vs": ("R-VS", "대결 구도(vs·1대1) 편을 만들어요 (예: ‘[국대 vs 국대] 2탄’)")}
TIP_CATS = [(re.compile(r"썸네일|제목|문구|캡션|카피"), "썸네일·제목", {"제목"}),
            (re.compile(r"자막|오버레이|화살표|템포|편집|말풍선|레이아웃|레터박스|누끼|템플릿|로고|브랜드 바|상단|하단|시그니처|촬영"), "편집 스타일", {"편집", "예능"}),
            (re.compile(r"시리즈|EP|코너|시즌|회차|연작|말머리"), "시리즈·코너", {"시리즈"}),
            (re.compile(r"쇼츠"), "쇼츠 운영", {"쇼츠"}),
            (re.compile(r"레슨|문의|클래스|센터|아카데미|수업"), "수익화·레슨 연계", {"레슨", "수익"}),
            (re.compile(r"댓글|퀴즈|참여|맞춰"), "시청자 참여", {"참여"})]
# 조사 메모 가운데 '할 일'이 아닌 관찰 (숫자·상태·평가): 가져올 점으로 만들지 않음 (채널을 펼치면 메모 전체가 보임)
TIP_OBS = re.compile(r"\d[\d,.]*\s*(?:만|천)?\s*회|회대|회 안팎|멈춤|경쟁 채널|확인 못|휴면|추정|반면교사|하락|약함|약해|증거|풀로 쓸|"
                     r"요소는 없음|상위권|최고 조회|\d+\s*배|배 넘|구독자 대비|롱폼이 길어|낮음|높음|좋음|잘 됨|소규모|조회\s*$")


def tip_parts(tip):
    """조사 메모 → 할 만한 조각들: 괄호 밖의 쉼표·문장 끝에서만 나누고, 너무 짧거나 관찰(숫자·상태)인 조각은 버림."""
    tip = str(tip or "")
    parts, cur, depth = [], "", 0
    for i, ch in enumerate(tip):
        if ch in "([{（【":
            depth += 1
        elif ch in ")]}）】":
            depth = max(0, depth - 1)
        nxt = tip[i + 1] if i + 1 < len(tip) else " "
        if depth == 0 and (ch == "," or (ch == "." and nxt.isspace())):
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    parts.append(cur)
    out = []
    for ph in parts:
        ph = ph.strip(" .")
        if len(re.sub(r"\s", "", ph)) < 6 or TIP_OBS.search(ph):
            continue
        out.append(ph)
    return out


def _ev(s, numbers, video=None, mult=None):
    v = None
    if video and video.get("id"):
        k = video.get("k") or "L"
        v = {"id": video["id"], "title": video.get("t") or video.get("title") or "", "url": _url_of(video["id"], k)}
    return {"channel": s.get("name") or s.get("key"), "key": s.get("key"), "group": s.get("group"), "subs": s.get("subs"), "numbers": numbers, "video": v,
            "mult": round(mult, 2) if isinstance(mult, (int, float)) else None}


def _word_rx(w):
    """주제어로 영상 찾기: 앞에 한글이 붙은 말('비선출' 안의 '선출')은 빼고."""
    return re.compile(r"(?<![가-힣])" + re.escape(w))


def _best_video(ch, rx=None, k=None):
    vids = (ch or {}).get("videos") or {}
    best = None
    for i, v in vids.items():
        if k and v.get("k") != k:
            continue
        t = v.get("t") or ""
        if rx is not None and not rx.search(t):
            continue
        if _vv([v.get("v")]) and (best is None or v["v"] > best["v"]):
            best = dict(v, id=i)
    return best


def _cands(st, ana, chans, own_a, gs, styles):
    """경쟁 채널마다 규칙을 돌려 후보 [(rule, pattern, text, evidence, mult, nvid, othersShare)]."""
    out = []
    comp_keys = [c["key"] for c in st["competitors"]]
    own_s, own_m = own_a["stats"], own_a["mine"]
    own_titles = " ".join(v.get("t") or "" for v in (chans.get(OWN) or {}).get("videos", {}).values())
    rec_idx = _rec_index()

    def add(rule, pattern, text, ev, mult=None, nvid=1, share=None, ours=None):
        if mult is not None and ev.get("mult") is None:
            ev["mult"] = round(mult, 2)
        ev["n"] = nvid
        out.append({"rule": rule, "pattern": pattern, "text": text, "ev": ev, "mult": mult, "nvid": nvid, "share": share, "ours": ours})
    for key in comp_keys:
        a = ana.get(key)
        if not a:
            continue
        s, m, ch = a["stats"], a["mine"], chans.get(key) or {}
        for se in m.get("series") or []:
            if se["n"] >= 2 and (se.get("mult") or 0) >= 1.2:
                add("R-SERIES", "series", "번호 붙은 시리즈·코너를 만들어요 (예: ‘[풋살사관학교 기초반 EP01]’)",
                    _ev(s, f"‘{se['name']}’ {se['n']}편 보통 {fmt_n(se['median'])}회 · 비슷한 때 올린 영상의 {se['mult']:g}배", dict(se["top"], k=se.get("k"))),
                    se["mult"], se["n"])
                break
        for k in ("L", "S"):
            fs = m.get(k, {}).get("formulas") or {}
            own_fs = own_m.get(k, {}).get("formulas") or {}
            for f_id, (rule, text) in TITLE_RULES.items():
                x = fs.get(f_id)
                if x and x["ok"] and (x.get("mult") or 0) >= 1.3:
                    rx = FORMULA_RX.get(f_id)
                    vid = _best_video(ch, rx, k) if rx is not None else None
                    lab = "쇼츠" if k == "S" else "롱폼"
                    add(rule, f_id, text, _ev(s, f"{lab} 제목 {x['n']}편({round(x['share'] * 100)}%)이 {FORMULA_LABEL[f_id]} · 비슷한 때 올린 다른 영상보다 {x['mult']:g}배", vid),
                        x["mult"], x["n"], x["share"], (own_fs.get(f_id) or {}).get("share"))
            if k == "S":
                eg = fs.get("engage")
                if eg and eg["n"] >= 2 and (eg.get("mult") or 0) >= 1.2:
                    vid = _best_video(ch, re.compile(r"맞춰|몇\s?번|퀴즈|댓글"), "S") or _best_video(ch, FORMULA_RX["engage"], "S")
                    add("R-ENGAGE-Q", "quiz", "쇼츠에 퀴즈·질문을 넣어 댓글을 받아요 (예: ‘몇 번이 제일 어려워요?’)",
                        _ev(s, f"참여 유도 쇼츠 {eg['n']}개 · 비슷한 때 올린 다른 쇼츠보다 {eg['mult']:g}배", vid), eg["mult"], eg["n"], eg["share"])
        save_v = [dict(v, id=i) for i, v in (ch.get("videos") or {}).items() if "저장" in (v.get("t") or "") and _vv([v.get("v")])]
        if len(save_v) >= 2:
            best = max(save_v, key=lambda v: v["v"])
            sm = _med(_vv(v["v"] for v in save_v))
            allS = s["S"].get("median")
            add("R-ENGAGE-SAVE", "save", "‘저장하고 연습해요’처럼 저장을 부탁해요 (따라 할 레슨일수록)",
                _ev(s, f"‘저장’ 넣은 영상 {len(save_v)}개 보통 {fmt_n(sm)}회" + (f" (쇼츠 보통 {fmt_n(allS)}회)" if allS else ""), best),
                (sm / allS) if allS else None, len(save_v))
        durs = [(v["d"], v["v"]) for v in (ch.get("videos") or {}).values() if v.get("k") == "L" and v.get("d") and _vv([v.get("v")])]
        short_l, long_l = [math.log(v) for d, v in durs if d <= 480], [math.log(v) for d, v in durs if d > 900]
        if len(short_l) >= 3 and len(long_l) >= 3:
            mu = math.exp(_med(short_l) - _med(long_l))
            if mu >= 1.3:
                add("R-DURATION", "short-long", "롱폼은 8분 안팎으로 짧게 만들어요 (15분 넘는 영상보다 반응이 좋아요)",
                    _ev(s, f"8분 이하 롱폼 {len(short_l)}편이 15분 넘는 롱폼보다 보통 {mu:.1f}배"), mu, len(short_l))
        tags = [h for h in m.get("hashtags") or [] if not h["generic"]]
        if tags:
            h = tags[0]
            add("R-HASHTAG", "hashtag", "쇼츠마다 같은 해시태그(예: #풋살사관학교)를 붙여 묶어요",
                _ev(s, f"{h['tag']} · 쇼츠 {h['of']}개 중 {h['n']}개", _best_video(ch, re.compile(re.escape(h["tag"])), "S")), None, h["n"])
        rs, ss = s["S"].get("reach"), s["shortsShare"].get("rss")
        if rs and rs >= 1 and ss is not None and ss >= 0.5 and (own_s["shortsShare"].get("rss") or 0) < ss:
            add("R-SHORTS-RATIO", "shorts-ratio", "쇼츠 비중을 늘려요 (기술 한 개 쇼츠를 주 3개 이상)",
                _ev(s, f"쇼츠가 구독자의 {rs:g}배 보임 · 최근 15개 중 쇼츠 {round(ss * 15)}개", _best_video(ch, None, "S")), rs, 15)
        pairs = _long_short_pairs(ch)
        if len(pairs) >= 2:
            lv, sv = pairs[0]
            add("R-SHORTS-FROM-LONG", "cut-shorts", "롱폼 한 편에서 핵심 장면을 잘라 쇼츠 2~3개로 올려요",
                _ev(s, f"롱폼과 같은 주제의 쇼츠를 2주 안에 {len(pairs)}번 올림 (예: ‘{(sv.get('t') or '')[:24]}’)", lv), None, len(pairs))
        c = s.get("cadence") or {}
        pw = (c.get("perWeek") or {}).get("all") or 0
        own_pw = ((own_s.get("cadence") or {}).get("perWeek") or {}).get("all") or 0
        good_reach = (s["S"].get("reach") or 0) >= ((gs.get(s["group"]) or {}).get("reachS") or 99) or \
            (s["L"].get("reach") or 0) >= ((gs.get(s["group"]) or {}).get("reachL") or 99)
        if pw >= 2 and (c.get("cv") is None or c["cv"] < 0.8) and good_reach and own_pw < pw:
            add("R-CADENCE", "cadence", "올리는 요일을 정해 주 2회 이상 꾸준히 올려요",
                _ev(s, f"주 {pw:.1f}회" + (f" · {cv_words(c['cv'])}" if c.get("cv") is not None else "") + f" · 우리 주 {own_pw:.1f}회"), None, 15)
        if c.get("activity") in ("쉬는 중", "멈춤"):
            ids_l = [i for i in ((ch.get("tabs") or {}).get("long") or {}).get("ids") or [] if i in ch["videos"]]
            a15 = _vv(ch["videos"][i].get("v") for i in ids_l[:15])
            b15 = _vv(ch["videos"][i].get("v") for i in ids_l[15:30])
            if a15 and b15 and _med(a15) < _med(b15):
                add("R-PAUSE-RISK", "pause", "쉬지 않고 이어 가요 (오래 쉬면 반응이 떨어져요)",
                    _ev(s, f"{c['lastDays']}일째 멈춤 · 최근 롱폼 15편 보통 {fmt_n(_med(a15))}회 (그 전 15편 {fmt_n(_med(b15))}회)"), _med(b15) / _med(a15), 15)
        lr, lp = s.get("likeRatio"), (gs.get(s["group"]) or {}).get("likeP75")
        if lr and lp and lr >= lp and lr >= 0.012:
            add("R-LIKE", "like", "영상 끝에 좋아요·댓글을 한마디로 부탁해요",
                _ev(s, f"좋아요 비율 {lr * 100:.1f}% (분류 상위 25%)" + (f" · 우리 {own_s['likeRatio'] * 100:.1f}%" if own_s.get("likeRatio") else "")), None, 15)
        df = s.get("descFlags") or {}
        if df.get("lesson"):
            add("R-LESSON-LINK", "lesson-link", "설명란 첫 줄에 레슨·수업 문의 경로를 넣어요",
                _ev(s, "채널 설명에 수업·레슨 안내" + (" · 문의 경로" if df.get("contact") else "") + "가 있어요"), None, 1)
        lc = [dict(v, id=i) for i, v in (ch.get("videos") or {}).items() if re.search(r"원데이|팀\s?레슨|클래스|레슨\s?후기|수강생|레슨\s?현장", v.get("t") or "")]
        if lc:
            best = max(lc, key=lambda v: v.get("v") or 0)
            add("R-LESSON-CONTENT", "lesson-content", "원데이클래스·팀레슨 현장을 쇼츠로 올려 레슨으로 이어지게 해요",
                _ev(s, f"레슨 현장 영상 {len(lc)}개 (가장 많이 본 것 {fmt_n(best.get('v'))}회)", best), None, len(lc))
        for info in _style_for(s, styles):
            if info.get("headline"):
                add("R-STYLE-PLAN", f"style:{key}", f"{s['name']}의 편집 공식을 따라 해 봐요: {info['headline']}",
                    _ev(s, f"배운 스타일 ‘{info['name']}’ 판단" + (f" · 자막: {info['captions']}" if info.get("captions") else "")), 1.4, 6)
        rc = rec_idx.get(key) or rec_idx.get(s.get("channelId") or "") or {}
        for n, ph in enumerate(tip_parts(rc.get("tip"))):
            add("R-RESEARCH-TIP", f"tip:{key}:{n}", f"참고: {ph}", _ev(s, f"조사 메모 2026-10-07 · 원문 ‘{rc.get('tip')}’"), None, 0)
    topics = {}
    terms = {t for t, _ in _TOPIC_RES}
    for key in comp_keys:
        a = ana.get(key)
        if not a:
            continue
        for k in ("L", "S"):
            for tp in a["mine"].get(k, {}).get("topics") or []:
                if tp["mult"] >= 1.5 and tp["n"] >= 2 and tp["w"] not in TOPIC_STOP and not _word_rx(tp["w"]).search(own_titles):
                    topics.setdefault(tp["w"], {}).setdefault(key, (a["stats"], tp, k))
    # 풋살 용어가 아니면 두 채널 이상에서 잘 된 낱말만 (사람 이름·한 채널만의 말이 '주제'가 되지 않게)
    good = {w: list(v.values()) for w, v in topics.items() if w in terms or len(v) >= 2}
    for w, items in sorted(good.items(), key=lambda kv: (-len(kv[1]), -max(x[1]["mult"] for x in kv[1])))[:6]:
        for s, tp, k in items:
            ch = chans.get(s["key"]) or {}
            vid = _best_video(ch, _word_rx(w), k)
            add("R-TOPIC", f"topic:{w}", f"‘{w}’ 주제를 다뤄 봐요 (다른 채널에서 반응이 좋고 우리는 아직 안 다뤘어요)",
                _ev(s, f"‘{w}’ 영상 {tp['n']}개가 비슷한 때 올린 영상의 {tp['mult']:g}배", vid), tp["mult"], tp["n"])
    return out


def _long_short_pairs(ch):
    vids = (ch or {}).get("videos") or {}
    dated = [dict(v, id=i) for i, v in vids.items() if isinstance(v.get("pub"), (int, float)) and is_ko(v.get("t"))]
    longs = [v for v in dated if v.get("k") == "L"]
    shorts = [v for v in dated if v.get("k") == "S"]
    solid = _solid(v["t"] for v in dated)
    pairs = []
    for lv in longs:
        lw = _words(lv["t"], solid)
        for sv in shorts:
            if abs(sv["pub"] - lv["pub"]) <= 14 * 86400 and lw & _words(sv["t"], solid):
                pairs.append((lv, sv))
                break
    return pairs


def _dir_tags(strat):
    strat = strat or {}
    d = strat.get("direction")
    tags = set(DIR_TAGS.get(d, set()))
    text = " ".join([strat.get("differentiation") or ""] + [f.get("name") or "" for f in strat.get("formats") or []]
                    + [s.get("name") or "" for s in strat.get("series") or []])
    for w, tg in (("대결", "대결"), ("챌린지", "챌린지"), ("시리즈", "시리즈"), ("EP", "시리즈"), ("쇼츠", "쇼츠"), ("레슨", "레슨"),
                  ("총정리", "총정리"), ("예능", "예능"), ("권위", "권위"), ("국가대표", "권위")):
        if w in text:
            tags.add(tg)
    return tags


def _target_words(strat):
    out = []
    for t in (strat or {}).get("target") or []:
        out += list(TARGETS.get(t, ()))
    return out


MINED = {"R-TOPIC", "R-AUTH", "R-QUESTION", "R-NUMBER", "R-HOWTO", "R-TARGET", "R-TITLELEN", "R-COMPILE", "R-VS", "R-ENGAGE-Q", "R-SERIES",
         "R-ENGAGE-SAVE"}
TIP_MAX = 60       # 조사 메모는 '중'까지만 (숫자 근거가 없음)
THIN_MAX = 69      # 제목·주제 규칙은 두 채널 이상 · 영상 5편 이상이어야 '상'
FIT_W = {"G": 0.25, "E": 0.40, "D": 0.15, "F": 0.20}


def _size_w(e_subs, own_subs):
    """근거 채널이 우리보다 얼마나 큰지: 10배 안 1 · 20배 안 0.7 · 그보다 크면 0.5 (구독자를 모르면 0.7)."""
    if not e_subs or not own_subs:
        return 0.7
    r = e_subs / max(own_subs, 1)
    return 1.0 if r <= 10 else 0.7 if r <= 20 else 0.5


def _ev_mult(e, own_subs):
    """근거 하나의 배수를 영상 수로 줄임 (n/(n+6) · 2~3편의 큰 배수는 우연일 때가 많아서) · 우리보다 20배 넘게 큰 채널은 절반만."""
    m = e.get("mult")
    if not m or m <= 0:
        return None
    n = e.get("n") or 1
    x = math.log(m) * n / (n + 6)
    if _size_w(e.get("subs"), own_subs) < 0.7:
        x *= 0.5
    return math.exp(x)


def _plan_met(rule, rates):
    """우리 계획이 이미 그만큼 하고 있으면 (솔루션에서 빼고 '계획에 이미 있어요')."""
    if rule == "R-SHORTS-RATIO":
        return rates["S"] >= 3
    if rule == "R-CADENCE":
        return rates["L"] + rates["S"] >= 2
    return False


def takeaways(st=None, ana=None, chans=None, now=None):
    """가져올 점 → [{id, rule, category, text, effort, fit{score,level,G,E,D,F}, notUsed, evidence[], hidden, todo, planMet}] (맞춤 점수 순).
    맞춤 점수 = 25% 비슷한 분류·규모 + 40% 근거(배수·채널 수·영상 수) + 15% 우리 방향 + 20% 일 크기 (BR-018)."""
    now = now or time.time()
    st = st or load_state()
    chans = chans or known_channels(st)
    ana = ana or {k: _ana(c, now) for k, c in chans.items()}
    gs = group_summary(ana)
    own_a = ana.get(OWN) or {"stats": channel_stats(_new_channel(own_entry(st)), now), "mine": mine({})}
    strat = st.get("strategy") or preset("A")
    rates = plan_rates(strat)
    tags = _dir_tags(strat)
    twords = _target_words(strat)
    own_subs = own_a["stats"].get("subs") or 0
    merged = {}
    for c in _cands(st, ana, chans, own_a, gs, learned_styles()):
        rule = c["rule"]
        cat, effort, rtags = RULES[rule]
        if rule == "R-RESEARCH-TIP":
            cat, rtags = "기획·형식", set()
            for rx, cc, tg in TIP_CATS:
                if rx.search(c["text"]):
                    cat, rtags = cc, tg
                    break
        tid = hashlib.sha1(f"{rule}|{c['pattern']}".encode("utf-8")).hexdigest()[:10]
        m = merged.setdefault(tid, {"id": tid, "rule": rule, "category": cat, "effort": effort, "tags": rtags, "text": c["text"],
                                    "evidence": [], "nvid": 0, "shares": [], "ours": c.get("ours"), "pattern": c["pattern"]})
        if any(e["key"] == c["ev"]["key"] for e in m["evidence"]):
            continue
        m["evidence"].append(c["ev"])
        if c["share"] is not None:
            m["shares"].append(c["share"])
        m["nvid"] += c["nvid"] or 0
    hidden, todos = set(st.get("hidden") or []), {(t.get("from") or {}).get("takeaway"): t for t in st.get("todos") or []}
    out = []
    for m in merged.values():
        ev = sorted(m["evidence"], key=lambda e: -(_ev_mult(e, own_subs) or 0))  # 가장 센 근거(영상 수까지 본 배수) 먼저 ('왜:'·첫 줄)
        G = max(GROUP_W.get(e.get("group"), 0.5) * _size_w(e.get("subs"), own_subs) for e in ev)
        if m["rule"] == "R-RESEARCH-TIP":
            E = 0.3
        else:
            mults = [x for x in (_ev_mult(e, own_subs) for e in ev) if x]
            mult = _med(mults) if mults else None
            base = (0.5 * math.log2(mult) if mult > 1 else 0.0) if mult else 0.4
            E = max(0.0, min(1.0, base + 0.15 * (len(ev) - 1))) * min(1.0, max(m["nvid"], 1) / 6)
            if m["nvid"] < 5:
                E = min(E, 0.5)
        others = _med(m["shares"]) if m["shares"] else None
        not_used = others is not None and others >= 0.5 and (m["ours"] or 0) < 0.2
        if not_used:
            E = min(1.0, E + 0.1)
        D = 1.0 if m["tags"] & tags else 0.5
        text_all = m["text"] + " " + " ".join(e["numbers"] + " " + ((e.get("video") or {}).get("title") or "") for e in ev)
        if twords and any(w in text_all for w in twords):
            D = min(1.0, D + 0.2)
        F = EFFORT_W[m["effort"]]
        score = round(100 * (FIT_W["G"] * G + FIT_W["E"] * E + FIT_W["D"] * D + FIT_W["F"] * F))
        thin = m["rule"] in MINED and (len(ev) < 2 or m["nvid"] < 5)
        if thin:
            score = min(score, THIN_MAX)
        if m["rule"] == "R-RESEARCH-TIP":
            score = min(score, TIP_MAX)
        lvl = "상" if score >= 70 else "중" if score >= 45 else "하"
        td = todos.get(m["id"])
        out.append({"id": m["id"], "rule": m["rule"], "pattern": m["pattern"], "category": m["category"], "text": m["text"], "effort": m["effort"],
                    "fit": {"score": score, "level": lvl, "G": round(G, 2), "E": round(E, 2), "D": round(D, 2), "F": F, "thin": thin},
                    "notUsed": not_used, "evidence": ev[:6], "nChannels": len(ev), "nVideos": m["nvid"], "hidden": m["id"] in hidden,
                    "todo": td["id"] if td else None, "use": USE.get(m["category"], ["plan"]), "planMet": _plan_met(m["rule"], rates),
                    "note": m["rule"] == "R-RESEARCH-TIP"})
    out.sort(key=lambda x: (x["hidden"], x["note"], x["planMet"], -x["fit"]["score"], x["text"]))  # 계획에 이미 있는 것은 뒤로
    return out


# ---------- 할 일 ----------

def todo_add(takeaway=None, text=None, category=None, tk=None):
    """할 일 넣기: 가져올 점에서(같은 것은 하나만 · 멱등) 또는 직접 쓴 글. 넣은(또는 이미 있던) 할 일을 돌려줌."""
    if takeaway:
        tk = tk or next((t for t in takeaways() if t["id"] == takeaway), None)
        if not tk:
            raise StrategyError("가져올 점을 찾지 못했어요. 화면을 새로 고친 뒤 다시 눌러 주세요")
    else:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            raise StrategyError("할 일을 적어 주세요")
        if len(text) > 300:
            raise StrategyError("할 일은 300자까지 적을 수 있어요")

    def put(d):
        if takeaway:
            for t in d["todos"]:
                if (t.get("from") or {}).get("takeaway") == takeaway:
                    return t
        if len(d["todos"]) >= TODOS_MAX:
            raise StrategyError(f"할 일은 {TODOS_MAX}개까지예요. 끝난 할 일을 지운 뒤 넣어 주세요")
        cat = tk["category"] if takeaway else (category if category in CATS else "기획·형식")
        ev = (tk or {}).get("evidence") or [{}]
        t = {"id": hashlib.sha1(f"{takeaway or text}|{time.time()}".encode("utf-8")).hexdigest()[:10],
             "text": tk["text"] if takeaway else text, "category": cat, "use": USE.get(cat, ["plan"]),
             "from": {"takeaway": takeaway, "channel": ev[0].get("channel"), "video": (ev[0].get("video") or {}).get("url")} if takeaway else None,
             "createdAt": time.time(), "done": False, "doneAt": None}
        d["todos"].append(t)
        return t
    return _update_state(put)


def todo_update(tid, done):
    def put(d):
        for t in d["todos"]:
            if t["id"] == tid:
                t["done"] = bool(done)
                t["doneAt"] = time.time() if done else None
                return t
        raise StrategyError("할 일을 찾지 못했어요")
    return _update_state(put)


def todo_remove(tid):
    def put(d):
        n = len(d["todos"])
        d["todos"] = [t for t in d["todos"] if t["id"] != tid]
        if len(d["todos"]) == n:
            raise StrategyError("할 일을 찾지 못했어요")
    _update_state(put)


def todos_for(use):
    """다른 단계(썸네일·올리기·편집)가 읽는 할 일: 아직 안 끝난 것 중 그 쓰임."""
    st = load_state()
    return [t for t in st["todos"] if not t.get("done") and (not use or use in (t.get("use") or []))]


def set_hidden(tid, hide):
    def put(d):
        if hide and tid not in d["hidden"]:
            d["hidden"].append(tid)
        if not hide:
            d["hidden"] = [x for x in d["hidden"] if x != tid]
    _update_state(put)


def set_settings(remind=None, own_auto=None):
    def put(d):
        if remind is not None:
            d["settings"]["remind"] = bool(remind)
        if own_auto is not None:
            d["settings"]["ownAuto"] = bool(own_auto)
        return d["settings"]
    return _update_state(put)


# ---------- 우리 전략 ----------

PRESETS = {
    "A": {"direction": "A", "target": ["풋살 입문자", "플랩 레벨업"], "targetNote": "",
          "formats": [{"id": "f1", "name": "레슨 시리즈 롱폼", "kind": "long", "perWeek": 1},
                      {"id": "f2", "name": "기술 한 개 쇼츠", "kind": "shorts", "perWeek": 3}],
          "days": [], "differentiation": "풋살 전용 포지션·전술(아라·피보·픽소·골레이로, 킥인, 2-2 로테이션) + 前 국가대표 감독 권위 + 유소년 지도 경험",
          "series": [{"name": "[풋살사관학교 기초반 EP01~]", "desc": "기초 기술을 하나씩 번호 붙여 배우는 강좌"},
                     {"name": "[최경진 감독을 뚫어라] N호 도전자", "desc": "도전자가 감독을 1:1로 뚫는 참여형 코너"},
                     {"name": "[국대 vs 국대]", "desc": "‘현 국가대표 대결 1탄’ 쇼츠(8,631회)의 2탄·3탄"}]},
    "B": {"direction": "B", "target": ["동호인 팀", "플랩 레벨업"], "targetNote": "",
          "formats": [{"id": "f1", "name": "대결 롱폼", "kind": "long", "perWeek": 1},
                      {"id": "f2", "name": "대결 직후 레슨 쇼츠", "kind": "shorts", "perWeek": 3}],
          "days": [], "differentiation": "기록이 쌓이는 대결 시리즈 + 대결 직후 감독이 바로 가르쳐 주는 레슨",
          "series": [{"name": "[최경진 감독을 뚫어라] N호 도전자", "desc": "1:1 대결 기록 누적"},
                     {"name": "[수강생팀 강팀 도전기 EP]", "desc": "수강생팀이 강팀에 도전하는 회차형 이야기"}]},
    "C": {"direction": "C", "target": ["풋살 입문자"], "targetNote": "",
          "formats": [{"id": "f1", "name": "기술 한 개 쇼츠 (고정 틀)", "kind": "shorts", "perWeek": 5},
                      {"id": "f2", "name": "N가지 총정리 롱폼", "kind": "long", "perWeek": 0.5}],
          "days": [], "differentiation": "풋살 전용 기술을 30~60초 고정 틀로 자주 + 2주에 한 번 총정리",
          "series": [{"name": "[1분 풋살 기술]", "desc": "같은 틀의 기술 한 개 쇼츠"},
                     {"name": "[N가지 총정리]", "desc": "쇼츠로 낸 기술을 모은 롱폼"}]},
}
DIRECTION_NAMES = {"A": "레슨 + 예능 MSG형", "B": "챌린지·대결형", "C": "쇼츠 집중형", "custom": "직접 정하기"}


def preset(key):
    p = _copy(PRESETS.get(key) or PRESETS["A"])
    p.update(goals={"m6": None, "m12": None}, startedAt=None, updatedAt=None)
    return p


def presets():
    rec = {d.get("key"): d for d in recommended().get("directions") or []}
    return [{"key": k, "title": DIRECTION_NAMES[k], "desc": (rec.get(k) or {}).get("desc") or "",
             "who": [c.get("channel") for c in (rec.get(k) or {}).get("channels") or []], "draft": preset(k)} for k in ("A", "B", "C")]


def _txt(x, n=TEXT_MAX):
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(x or "")).strip()
    if len(s) > n:
        raise StrategyError(f"글이 너무 길어요 ({n}자까지)")
    return s


def validate_strategy(d):
    """화면에서 받은 전략 → 검사한 dict. 잘못이면 StrategyError(한국어)."""
    if not isinstance(d, dict):
        raise StrategyError("전략 형식이 아니에요")
    direction = d.get("direction") if d.get("direction") in ("A", "B", "C", "custom") else "custom"
    target = [t for t in (d.get("target") or []) if isinstance(t, str) and t in TARGETS][:len(TARGETS)]
    fm = d.get("formats") or []
    if not isinstance(fm, list) or len(fm) > MAX_FORMATS:
        raise StrategyError(f"주력 형식은 {MAX_FORMATS}개까지예요")
    formats = []
    for i, f in enumerate(fm):
        if not isinstance(f, dict):
            raise StrategyError("주력 형식 형식이 아니에요")
        try:
            pw = float(f.get("perWeek") or 0)
        except (TypeError, ValueError):
            raise StrategyError("주당 개수는 숫자로 넣어 주세요") from None
        if not (0 <= pw <= MAX_PER_WEEK) or math.isnan(pw):
            raise StrategyError(f"주당 개수는 0~{MAX_PER_WEEK}개로 넣어 주세요")
        formats.append({"id": _txt(f.get("id") or f"f{i + 1}", 20) or f"f{i + 1}", "name": _txt(f.get("name"), 100) or f"형식 {i + 1}",
                        "kind": "shorts" if f.get("kind") == "shorts" else "long", "perWeek": round(pw * 2) / 2})
    se = d.get("series") or []
    if not isinstance(se, list) or len(se) > MAX_SERIES:
        raise StrategyError(f"시리즈 아이디어는 {MAX_SERIES}개까지예요")
    series = [{"name": _txt(s.get("name"), 100), "desc": _txt(s.get("desc"), 300)} for s in se if isinstance(s, dict) and str(s.get("name") or "").strip()]
    days = [x for x in d.get("days") or [] if x in ("월", "화", "수", "목", "금", "토", "일")]
    goals = {}
    for k in ("m6", "m12"):
        g = (d.get("goals") or {}).get(k)
        try:
            g = int(g) if g not in (None, "") else None
        except (TypeError, ValueError):
            raise StrategyError("목표 구독자는 숫자로 넣어 주세요") from None
        if g is not None and not (0 < g <= 10_000_000):
            raise StrategyError("목표 구독자를 다시 확인해 주세요")
        goals[k] = g
    started = d.get("startedAt")
    if started not in (None, ""):
        ts = _iso(started) if isinstance(started, str) else started if isinstance(started, (int, float)) else None
        if ts is None:
            raise StrategyError("시작한 날을 다시 확인해 주세요")
        started = ts
    else:
        started = None
    return {"direction": direction, "target": target, "targetNote": _txt(d.get("targetNote")), "formats": formats, "days": days,
            "differentiation": _txt(d.get("differentiation"), LONG_MAX), "series": series, "goals": goals, "startedAt": started}


def save_strategy(d):
    clean = validate_strategy(d)

    def put(s):
        clean["startedAt"] = clean["startedAt"] or (s.get("strategy") or {}).get("startedAt") or time.time()  # 처음 저장한 날 (다시 시작한 날이 아님)
        clean["updatedAt"] = time.time()
        s["strategy"] = clean
        return clean
    return _update_state(put)


def set_revived(ts):
    ts = _iso(ts) if isinstance(ts, str) else ts
    if not isinstance(ts, (int, float)):
        raise StrategyError("날짜를 다시 확인해 주세요")
    _update_state(lambda d: d["own"].update(revivedAt=ts, revivedManual=True))


def current_strategy(st=None):
    st = st or load_state()
    return st.get("strategy") or dict(preset("A"), draft=True)


def plan_rates(strat):
    out = {"L": 0.0, "S": 0.0}
    for f in (strat or {}).get("formats") or []:
        out["S" if f.get("kind") == "shorts" else "L"] += float(f.get("perWeek") or 0)
    return {k: min(MAX_PER_WEEK, v) for k, v in out.items()}


# ---------- 가능성 (forecast.py 연결 · 캐시) ----------

FC_VER = 2
PACE_DAYS = 28                    # '지금 속도' = 최근 4주에 올린 개수 ÷ 4
_FC_LOCK = threading.Lock()       # 가능성 계산은 한 번에 하나 (같은 입력이면 기다렸다가 캐시를 씀)


def _g0(own_hist, own_ch):
    """올리지 않은 기간의 주당 구독자 변화 중앙값 (기록 2개 이상 · 6일 넘게 떨어진 쌍) · 0 아래는 0."""
    pubs = [v["pub"] for v in ((own_ch or {}).get("videos") or {}).values() if isinstance(v.get("pub"), (int, float))]
    xs = []
    h = [r for r in own_hist or [] if isinstance(r.get("subs"), (int, float))]
    for a, b in zip(h, h[1:]):
        if b["at"] - a["at"] < 6 * 86400:
            continue
        if any(a["at"] < p <= b["at"] for p in pubs):
            continue
        xs.append((b["subs"] - a["subs"]) / ((b["at"] - a["at"]) / (7 * 86400)))
    return max(0.0, _med(xs)) if xs else 0.0


def _data_time(ch, now):
    """채널 자료를 받은 시각 (가능성 입력의 나이·주기는 이 시각 기준 → 시계가 흘러도 같은 자료면 같은 입력 · 캐시가 유지됨)."""
    ts = [x for x in ((ch.get("rss") or {}).get("at"), ch.get("at")) if isinstance(x, (int, float))]
    return min(now, max(ts)) if ts else now


def own_pace(oc, now=None):
    """우리 채널 지금 속도 {L, S} (최근 4주 RSS 날짜로 · 주당 개수, 0.5 단위) · RSS 를 못 받았으면 None."""
    if not oc or not (oc.get("rss") or {}).get("ok"):
        return None
    ref = _data_time(oc, now or time.time())
    rec = [v for v in (oc.get("videos") or {}).values() if isinstance(v.get("pub"), (int, float)) and 0 <= ref - v["pub"] <= PACE_DAYS * 86400]
    return {k: round(2 * sum(1 for v in rec if v.get("k") == k) / (PACE_DAYS / 7)) / 2 for k in ("L", "S")}


def forecast_inputs(st=None, now=None):
    """forecast.run 입력 {peers, own, plan, goals, group, pace, plans} — 모두 자료 받은 시각 기준 (지금 시각에 따라 바뀌지 않음)."""
    now = now or time.time()
    st = st or load_state()
    chans = known_channels(st)
    hist = read_history()
    peers = []
    for k, c in chans.items():
        if k == OWN:
            continue
        ref = _data_time(c, now)
        s = _ana(c, now)["stats"]
        vids = c.get("videos") or {}
        rv = [vids[i] for i in (c.get("rss") or {}).get("ids") or [] if i in vids]
        cad = cadence([(v["pub"], v.get("k")) for v in rv if isinstance(v.get("pub"), (int, float))], ref)
        peers.append({"key": k, "group": s["group"], "subs": s["subs"],
                      "L": [vids[i].get("v") for i in ((c.get("tabs") or {}).get("long") or {}).get("ids", [])[:RECENT] if i in vids],
                      "S": [vids[i].get("v") for i in ((c.get("tabs") or {}).get("shorts") or {}).get("ids", [])[:RECENT] if i in vids],
                      "cad": (cad or {}).get("perWeek"), "life": s["life"],
                      "rssL": [v.get("v") for v in rv if v.get("k") == "L" and isinstance(v.get("pub"), (int, float)) and ref - v["pub"] >= 14 * 86400],
                      "rssS": [v.get("v") for v in rv if v.get("k") == "S" and isinstance(v.get("pub"), (int, float)) and ref - v["pub"] >= 7 * 86400],
                      "history": [{"at": r["at"], "subs": r.get("subs")} for r in hist.get(k, [])]})
    oc = chans.get(OWN) or {}
    os_ = _ana(oc, now)["stats"] if oc else channel_stats(_new_channel(own_entry(st)), now)
    ref = _data_time(oc, now) if oc else now
    vids = oc.get("videos") or {}
    own = {"subs": os_.get("subs") or 0,
           "videos": [{"k": v.get("k"), "v": v.get("v"), "age": round((ref - v["pub"]) / 86400, 1)}
                      for _i, v in sorted(vids.items()) if isinstance(v.get("pub"), (int, float))],
           "life": os_["life"], "g0": _g0(hist.get(OWN), oc)}
    strat = current_strategy(st)
    return {"peers": peers, "own": own, "plan": plan_rates(strat), "goals": strat.get("goals") or {}, "group": own_entry(st)["group"],
            "pace": own_pace(oc, now) if oc.get("src") == "live" else None,
            "plans": {k: plan_rates(preset(k)) for k in ("A", "B", "C")}}


def _fc_hash(inp):
    return forecast.inputs_hash(FC_VER, forecast.B, forecast.M, forecast.W, inp["peers"], inp["own"], inp["plan"], inp["goals"],
                                inp["group"], inp["pace"], inp["plans"])


def forecast_result(st=None, force=False):
    """가능성 (입력이 같으면 forecast.json 캐시) → forecast.run 결과 + at · numpy 가 없으면 forecast.ForecastError.
    계산은 한 번에 하나: 같이 들어온 요청은 앞 계산이 끝나길 기다렸다가 그 캐시를 씀."""
    inp = forecast_inputs(st)
    h = _fc_hash(inp)
    cache = _read(_path("forecast.json"), dict)
    if not force and cache.get("inputsHash") == h and isinstance(cache.get("result"), dict):
        return cache["result"]
    with _FC_LOCK:
        cache = _read(_path("forecast.json"), dict)
        if not force and cache.get("inputsHash") == h and isinstance(cache.get("result"), dict):
            return cache["result"]
        res = forecast.run(inp["peers"], inp["own"], inp["plan"], inp["goals"], inp["group"], pace=inp["pace"], plans=inp["plans"])
        live = live_channels()
        res.update(inputsHash=h, at=time.time(), seedAt=seed()["at"], liveN=sum(1 for p in inp["peers"] if p["key"] in live),
                   draft=(st or load_state()).get("strategy") is None)
        try:
            _write(_path("forecast.json"), {"inputsHash": h, "at": res["at"], "result": res})
        except OSError:
            pass
        return res


def forecast_preview(L, S, st=None):
    """저장하지 않은 계획(롱폼 L · 쇼츠 S 주당 개수)의 다음 목표 가능성 미리 보기 (캐시 없음 · 저장 안 함)."""
    try:
        L, S = float(L), float(S)
    except (TypeError, ValueError):
        raise StrategyError("주당 개수는 숫자로 넣어 주세요") from None
    if not (0 <= L <= MAX_PER_WEEK and 0 <= S <= MAX_PER_WEEK) or math.isnan(L) or math.isnan(S):
        raise StrategyError(f"주당 개수는 0~{MAX_PER_WEEK}개로 넣어 주세요")
    inp = forecast_inputs(st)
    with _FC_LOCK:
        res = forecast.run(inp["peers"], inp["own"], {"L": L, "S": S}, inp["goals"], inp["group"], plans={"preview": {"L": L, "S": S}})
    pv = (res.get("plans") or {}).get("preview") or {}
    return {"rates": {"L": L, "S": S}, "goal": res.get("goal"), "m12": pv.get("m12"), "m6": pv.get("m6"), "subs12": pv.get("subs12"),
            "conv": pv.get("conv")}


# ---------- 성공 솔루션 (30/60/90) ----------

PHASES = (("1~30일", "기본 틀 만들기", 30), ("31~60일", "잘 된 것 늘리기", 60), ("61~90일", "다듬기", 90))
# 가져올 점이 움직이는 쪽 (그 항목 하나의 효과는 따로 알 수 없어서 %는 붙이지 않음 · 계획 바꾸기 줄에만 %)
LEVER = {"썸네일·제목": ("u+", "조회수 올리기"), "기획·형식": ("u+", "조회수 올리기"), "편집 스타일": ("u+", "조회수 올리기"),
         "시리즈·코너": ("u+", "조회수 올리기"), "시청자 참여": ("c+", "구독 전환 올리기"), "쇼츠 운영": ("S+2", "쇼츠 운영"),
         "업로드 주기": ("L+1", "꾸준히 올리기"), "수익화·레슨 연계": (None, "레슨 연계 (구독 수와는 따로)")}
EFFORT_DIV = {"쉬움": 1.0, "보통": 1.5, "큼": 2.5}
SCEN_EFFORT = {"L+1": "큼", "S+2": "보통", "u+": "보통", "c+": "쉬움"}


def _sens_small(sens, sid):
    s = sens.get(sid)
    return s is not None and (s.get("small") or abs(s.get("d12") or 0) < 5)


def solution(st, tks, fc, gs=None, topics=None, own_stats=None):
    """성공 솔루션: 구간 3개(올릴 것·할 일·KPI·왜) + 먼저 할 것(가져올 점 · 맞춤 ÷ 일 크기 · %는 붙이지 않음)
    + 계획을 바꾸면(가능성 계산의 시나리오에만 %) + 한 줄 요약(쇼츠·롱폼)."""
    strat = current_strategy(st)
    rates = plan_rates(strat)
    ideas = [s["name"] for s in strat.get("series") or []] + [f"‘{w}’ 주제" for w in (topics or [])]
    sens = {s["id"]: s for s in (fc or {}).get("sensitivity") or []}
    # 계획이 이미 하고 있는 것 · 계산상 거의 차이 없는 쪽(쇼츠 +2 등)은 솔루션에서 뺌 (조언이 서로 어긋나지 않게)
    act = [t for t in tks if not t["hidden"] and t["fit"]["score"] >= 55 and not t.get("note") and not t.get("planMet")
           and not (LEVER.get(t["category"], (None,))[0] in ("S+2", "L+1") and _sens_small(sens, LEVER[t["category"]][0]))]
    kpi = (fc or {}).get("kpi") or {}
    g = (gs or {}).get(own_entry(st)["group"]) or {}
    own_med = {k: ((own_stats or {}).get(k) or {}).get("median") for k in ("L", "S")}
    phases = []
    k_idea = 0
    for n, (span, title, days) in enumerate(PHASES):
        ups = []
        for f in strat.get("formats") or []:
            cnt = round(float(f.get("perWeek") or 0) * 30 / 7)
            if cnt <= 0:
                continue
            ex = []
            for _ in range(min(2, cnt)):
                if ideas:
                    ex.append(ideas[k_idea % len(ideas)])
                    k_idea += 1
            ups.append({"name": f["name"], "kind": f["kind"], "count": cnt, "examples": ex})
        todo = [t for t in act if (n == 0 and t["effort"] == "쉬움") or (n == 1 and (t["effort"] == "보통" or (t["effort"] == "큼" and t["fit"]["score"] >= 80)))
                or (n == 2 and t["effort"] == "큼" and t["fit"]["score"] < 80)][:6]
        kd = kpi.get(f"day{days}") or {}
        vm = kpi.get("videoMedian") or {}
        kp = [{"text": f"롱폼 {round(rates['L'] * 30 / 7)}개 · 쇼츠 {round(rates['S'] * 30 / 7)}개 올리기"}]
        for f, lab in (("L", "롱폼"), ("S", "쇼츠")):
            if rates[f] > 0 and vm.get(f):
                cur = f" · 지금 {fmt_n(own_med[f])}회" if own_med.get(f) else ""
                kp.append({"text": f"{lab} 영상 보통 조회수: 최소 {vm[f]['p25']:,}회 · 잘 되면 {vm[f]['p50']:,}회{cur}",
                           "min": vm[f]["p25"], "target": vm[f]["p50"]})
        if kd.get("subs"):
            kp.append({"text": f"구독자 약 {kd['subs']['p50']:,}명 ({kd['subs']['p25']:,}~{kd['subs']['p75']:,}명 · 계획대로 올리면)", "target": kd["subs"]["p50"]})
        if g.get("likeRatio"):
            kp.append({"text": f"좋아요 비율 {g['likeRatio'] * 100:.1f}% 이상 (같은 분류 가운데 값)"})
        phases.append({"span": span, "title": title, "uploads": ups, "todos": [{"id": t["id"], "text": t["text"], "effort": t["effort"],
                                                                             "category": t["category"], "todo": t["todo"]} for t in todo],
                       "kpi": kp, "why": [t["id"] for t in todo]})
    scen = []
    for sid in ("L+1", "S+2", "u+", "c+"):
        s = sens.get(sid)
        if s and s["d12"] > 0:
            scen.append({"id": sid, "text": s["text"], "impact": s["d12"], "effort": SCEN_EFFORT[sid], "conv": s.get("conv"),
                         "score": s["d12"] / EFFORT_DIV[SCEN_EFFORT[sid]]})
    scen.sort(key=lambda x: -x["score"])
    lever_max = {sid: sens[sid]["d12"] for sid in ("u+", "c+") if sid in sens and sens[sid]["d12"] > 0}
    pri, per_cat = [], {}
    for t in sorted(act, key=lambda t: -t["fit"]["score"] / EFFORT_DIV[t["effort"]]):
        if per_cat.get(t["category"], 0) >= 2:  # 같은 분류는 두 개까지 (먼저 할 것이 한쪽으로 몰리지 않게)
            continue
        per_cat[t["category"]] = per_cat.get(t["category"], 0) + 1
        sid, lever = LEVER.get(t["category"], (None, "기타"))
        pri.append({"text": t["text"], "effort": t["effort"], "from": t["id"], "category": t["category"], "lever": lever, "leverId": sid,
                    "fit": t["fit"]["score"], "todo": t["todo"]})
        if len(pri) >= 6:
            break
    note = None
    sL, sS = sens.get("L+1"), sens.get("S+2")
    if sL and sS and sL["d12"] >= 5 and _sens_small(sens, "S+2"):
        note = f"쇼츠는 노출용 · 구독은 롱폼이 만들어요 (쇼츠 +2개는 거의 차이 없고, 롱폼 +1개는 +{sL['d12']}%포인트 안팎)"
    return {"phases": phases, "priorities": pri, "scenarios": scen, "leverMax": lever_max, "note": note, "rates": rates,
            "draft": st.get("strategy") is None}


# ---------- 점검 ----------

REPLACE_DAYS = 3                  # 지난 점검이 3일 안이면 새 점검이 그 기록을 바꿈 (여러 번 눌러도 쌓이지 않음 · 기간은 그 전 점검부터)
BAND_MIN_DAYS = 5                 # 예측 띠와 구독자 비교는 5일 넘게 지났을 때만 (며칠 만에는 반올림 단위보다 작게 늘어서)
SHORT_DAYS = 5                    # 기간이 이보다 짧으면 올린 개수는 판단하지 않음
RIPE = {"L": 14, "S": 7}          # 조회수를 볼 만큼 지난 영상: 롱폼 14일 · 쇼츠 7일
RIPE_MAX = 10                     # 조회수 비교는 시작한 뒤 올린 영상 중 최근 10편까지
STUDIO_DAYS = (7, 28)


def sub_step(n):
    """YouTube 가 보여 주는 구독자 단위 (세 자리까지만 · 버림): 1천 아래 1명 · 1만 아래 10명 · 10만 아래 100명 · 그 위 1,000명."""
    n = n or 0
    return 1 if n < 1000 else 10 if n < 10000 else 100 if n < 100000 else 1000 if n < 1000000 else 10000


def _subs_at(hist, ts):
    """ts 이전 가장 가까운 기록의 구독자 (없으면 첫 기록) — 지난 예측 맞춤(Brier)용."""
    h = [r for r in hist or [] if isinstance(r.get("subs"), (int, float))]
    before = [r for r in h if r["at"] <= ts]
    r = before[-1] if before else (h[0] if h else None)
    return (r["subs"], r["at"]) if r else (None, None)


def _subs_near(hist, ts):
    """기간 시작의 구독자: 시작 7일 전 ~ 2일 뒤 사이의 기록만 (멀리 떨어진 기록으로 늘어난 수를 지어내지 않게) → (구독자, 시각) | (None, None)."""
    h = [r for r in hist or [] if isinstance(r.get("subs"), (int, float))]
    before = [r for r in h if ts - 7 * 86400 <= r["at"] <= ts]
    if before:
        return before[-1]["subs"], before[-1]["at"]
    after = [r for r in h if ts < r["at"] <= ts + 2 * 86400]
    return (after[0]["subs"], after[0]["at"]) if after else (None, None)


def band_at(fb, ts):
    """점검 때 저장한 예측 띠를 ts 시점으로 (주 사이는 직선 · 0주 = 그때 구독자 s0) → {week, p10, p50, p90} | None."""
    p10, p50, p90 = fb.get("p10") or [], fb.get("p50") or [], fb.get("p90") or []
    s0 = fb.get("s0")
    w = (ts - fb.get("at", ts)) / (7 * 86400)
    n = min(len(p10), len(p50), len(p90))
    if not n or w <= 0 or w > n or not isinstance(s0, (int, float)):
        return None
    i = int(math.floor(w))

    def at(arr):
        if i >= n:
            return arr[n - 1]
        a = s0 if i == 0 else arr[i - 1]
        return a + (arr[i] - a) * (w - i)
    return {"week": round(w, 1), "p10": round(at(p10)), "p50": round(at(p50)), "p90": round(at(p90))}


def _band_state(s1, b):
    """반올림(버림) 단위를 넣고 띠와 견줌: 실제 값은 s1 ~ s1+단위-1 사이라서."""
    step = sub_step(s1)
    if s1 + step - 1 < b["p10"]:
        return "below"
    if s1 > b["p90"]:
        return "above"
    return "inside"


def _short_title(t, n=24):
    """제목 줄이기: 낱말 경계에서 자르고 '…' · 닫히지 않은 괄호는 뺌."""
    t = re.sub(r"\s+", " ", re.sub(r"#\S+", "", str(t or ""))).strip()
    if len(t) > n:
        cut = t[:n]
        sp = cut.rfind(" ")
        t = (cut[:sp] if sp >= n * 0.5 else cut).rstrip(" ,.·-|") + "…"
    for o, c in (("(", ")"), ("[", "]"), ("（", "）"), ("【", "】"), ("“", "”"), ("‘", "’")):
        if t.count(o) > t.count(c):
            k = t.rfind(o)
            t = t[:k].rstrip(" ,.·-|") + "…" if k > 0 else t.replace(o, "")
    return t.replace("……", "…")


def _approx(x):
    """계획 개수(소수) → 화면 '약 N개' (0.5 이상이면 1개 이상)."""
    return max(1, round(x)) if x >= 0.5 else 0


def checkup(log=print, cancel=None, studio=None):
    """점검(작업 안에서): 우리 채널 새로 고침 → 계획 vs 실제 · 잘 되는 것/안 되는 것/바꿀 것 → checkups.json.
    지난 점검이 3일 안이면 그 기록을 이번 결과로 바꿈 (기록이 쌓이지 않고 '최근 점검'이 거의 빈 점검으로 덮이지 않게)."""
    rf = refresh(mode="check", log=log, cancel=cancel, label=JOB_CHECK) or {}
    core.set_progress(label=JOB_CHECK, pct=None, detail="계획과 실제를 비교하는 중")
    now = time.time()
    rec = evaluate(studio=studio, now=now)
    items = load_checkups()
    if items and now - items[-1]["at"] < REPLACE_DAYS * 86400:
        rec["replaced"] = items[-1]["at"]
        items[-1] = rec
    else:
        items.append(rec)
    _save_checkups(items)
    log(f"  점검했어요 · 잘 되는 것 {len(rec['good'])} · 안 되는 것 {len(rec['bad'])} · 바꿀 것 {len(rec['change'])}"
        + (" · 최근 점검을 이번 결과로 바꿨어요" if rec.get("replaced") else ""))
    # 새로 고침이 멈췄거나·막혔거나·인터넷이 끊겼으면 그대로 알려 줌 (점검은 그때까지 받은 숫자로 · 휴대폰 '마지막 작업' D-028)
    return {"ok": True, "checkup": rec, "stopped": bool(rf.get("stopped")), "blocked": bool(rf.get("blocked")), "net": bool(rf.get("net"))}


def _clean_studio(studio):
    """스튜디오 숫자 {views, subs, days(7·28)} (예전 꼴 views28·subs28 도 받음)."""
    if not isinstance(studio, dict):
        return None
    out = {}
    for k, old in (("views", "views28"), ("subs", "subs28")):
        v = studio.get(k, studio.get(old))
        if v in (None, ""):
            continue
        try:
            v = int(float(v))
        except (TypeError, ValueError):
            raise StrategyError("스튜디오 숫자는 숫자로 넣어 주세요") from None
        if abs(v) > 10 ** 9:
            raise StrategyError("스튜디오 숫자를 다시 확인해 주세요")
        out[k] = v
    if not out:
        return None
    try:
        days = int(studio.get("days") or (28 if ("views28" in studio or "subs28" in studio) else 7))
    except (TypeError, ValueError):
        days = 7
    out["days"] = days if days in STUDIO_DAYS else 7
    return out


def evaluate(studio=None, now=None):
    """계획 vs 실제 (점검 기록 하나 · 저장은 부르는 쪽).
    기간 = 3일 넘게 지난 마지막 점검(없으면 전략을 시작한 날) 뒤 · 올린 개수는 기간이 5일 넘을 때만 판단 ·
    조회수는 기간이 아니라 영상 나이로(시작한 뒤 올린 영상 중 롱폼 14일·쇼츠 7일 지난 것) · 예측 띠는 5일 넘게 지났을 때 그날로 이어서,
    반올림 단위를 넣어 비교 · 가운데 예상(P50)은 '위/아래'로만 말하고 최소 목표(P25) 아래일 때만 '안 되는 것'."""
    now = now or time.time()
    studio = _clean_studio(studio)
    st = load_state()
    strat = current_strategy(st)
    saved = st.get("strategy") is not None
    rates = plan_rates(strat)
    prev = [c for c in load_checkups() if c["at"] <= now - REPLACE_DAYS * 86400]
    base = prev[-1] if prev else None
    s_start = strat.get("startedAt") if saved else None
    start = base["at"] if base else (s_start or now - 28 * 86400)
    start = min(start, now)
    days = max(0.0, (now - start) / 86400)
    weeks = max(days / 7, 1 / 7)
    oc = load_channel(OWN) or (known_channels(st).get(OWN) or {})
    vids = oc.get("videos") or {}
    dated = [dict(v, id=i) for i, v in vids.items() if isinstance(v.get("pub"), (int, float)) and v["pub"] <= now]
    new = [v for v in dated if v["pub"] > start]
    actual = {"L": sum(1 for v in new if v.get("k") == "L"), "S": sum(1 for v in new if v.get("k") == "S")}
    plan = {k: round(rates[k] * weeks, 2) for k in ("L", "S")}
    short = days < SHORT_DAYS
    try:
        fc = forecast_result(st)
    except Exception:  # noqa: BLE001 — 가능성을 못 구해도 점검은 함 (숫자 비교만)
        fc = None
    kpi = ((fc or {}).get("kpi") or {}).get("videoMedian") or {}
    stats = channel_stats(oc, now) if oc else None
    since = s_start or (st.get("own") or {}).get("revivedAt") or now - 90 * 86400
    med, ripe_new = {}, []
    for k in ("L", "S"):
        ripe = sorted((v for v in dated if v.get("k") == k and v["pub"] >= since and now - v["pub"] >= RIPE[k] * 86400 and _vv([v.get("v")])),
                      key=lambda v: -v["pub"])[:RIPE_MAX]
        ripe_new += [v for v in ripe if v["pub"] + RIPE[k] * 86400 > start]  # 이번 기간에 조회수를 볼 만큼 지난 영상
        m = round(_med([v["v"] for v in ripe])) if ripe else None
        kv = kpi.get(k) or {}
        lo, mid = kv.get("p25"), kv.get("p50")
        state = None
        if m and mid and lo:
            state = "up" if m >= mid else "down" if m < lo else "mid"
        med[k] = {"n": len(ripe), "median": m, "min": lo, "kpi": mid, "state": state}
    hist = read_history().get(OWN) or []
    s1 = (stats or {}).get("subs")
    s0, s0at = _subs_near(hist, start)
    delta = (s1 - s0) if isinstance(s1, (int, float)) and isinstance(s0, (int, float)) else None
    delta_src = "rounded" if delta is not None else None
    if studio and "subs" in studio and abs(studio["days"] - days) <= 2:
        delta, delta_src = studio["subs"], "studio"
    band = None
    fb = (base or {}).get("forecastBand")
    if isinstance(fb, dict) and isinstance(s1, (int, float)) and now - fb.get("at", now) >= BAND_MIN_DAYS * 86400:
        b = band_at(fb, now)
        if b:
            band = dict(b, actual=s1, step=sub_step(s1), state=_band_state(s1, b))
            band["inside"] = band["state"] == "inside"
    good, bad, change = [], [], []
    for k, lab, obj in (("L", "롱폼", "롱폼을"), ("S", "쇼츠", "쇼츠를")):
        pn = _approx(plan[k])
        if not short and plan[k] >= 1 and actual[k] < 0.7 * plan[k]:
            bad.append(f"{obj} 계획(약 {pn}개)보다 적게 올렸어요 ({actual[k]}개)")
            change.append("촬영하는 날 쇼츠 4개를 몰아 찍어 두거나, 계획 개수를 현실에 맞게 고쳐요" if k == "S" else
                          "롱폼은 한 번 촬영으로 2편을 나눠 찍거나, 계획 개수를 고쳐요")
        elif not short and plan[k] >= 1 and actual[k] >= plan[k] - 0.5:
            good.append(f"{obj} 계획대로 올렸어요 ({actual[k]}개 / 계획 약 {pn}개)")
        m = med[k]
        if m["state"] == "up":
            good.append(f"{lab} 보통 조회수 {fmt_n(m['median'])}회 · 가운데 예상({fmt_n(m['kpi'])}회)보다 위예요 ({m['n']}편)")
        elif m["state"] == "down":
            bad.append(f"{lab} 보통 조회수 {fmt_n(m['median'])}회 · 최소 목표({fmt_n(m['min'])}회)보다 아래예요 ({m['n']}편)")
    chan_med = {k: ((stats or {}).get(k) or {}).get("median") for k in ("L", "S")}
    for v in sorted(ripe_new, key=lambda v: -(v.get("v") or 0)):
        cm = chan_med.get(v.get("k"))
        if cm and (v.get("v") or 0) > 2 * cm:
            good.append(f"‘{_short_title(v.get('t'), 30)}’ {fmt_n(v['v'])}회 — 채널 보통의 {v['v'] / cm:.1f}배")
            change.append(f"‘{_short_title(v.get('t'), 20)}’ 같은 주제로 2탄을 만들어요")
            break
    longs = [v for v in dated if v.get("k") == "L" and v["pub"] >= since and now - v["pub"] >= RIPE["L"] * 86400
             and (v.get("d") or 0) > 720 and _vv([v.get("v")])]
    if len(longs) >= 2 and chan_med.get("L") and _med([v["v"] for v in longs]) < chan_med["L"]:
        bad.append(f"12분 넘는 롱폼 {len(longs)}편의 반응이 채널 보통보다 약해요")
        change.append("롱폼을 8분 안팎으로 줄여 봐요")
    if band and band["state"] == "below":
        bad.append(f"구독자가 예상 범위보다 적게 늘었어요 (지금 약 {s1:,}명 · 예상 {band['p10']:,}~{band['p90']:,}명)")
        change.append("롱폼 비중을 늘리고, 영상 끝에 구독을 한 번 부탁해요")
    elif band and band["state"] == "above":
        good.append(f"구독자가 예상보다 많이 늘었어요 (지금 약 {s1:,}명 · 예상 {band['p10']:,}~{band['p90']:,}명)")
    elif band:
        good.append(f"구독자가 예상 범위 안이에요 (약 {s1:,}명 · 가운데 예상 {band['p50']:,}명보다 {'위' if s1 >= band['p50'] else '아래'})")
    top = sorted(ripe_new, key=lambda v: -(v.get("v") or 0))[:3]
    hits = [lab for f_id, lab, rx in FORMULAS if f_id not in ("short", "long", "hashtag", "emph") and len(top) >= 2
            and all(_fhit(f_id, rx, v.get("t") or "") for v in top[:2])]
    if hits:
        good.append(f"잘 된 영상들이 ‘{hits[0]}’ 제목이에요 — 계속 써 봐요")
    if not new and not short:
        bad.append("이 기간에 올린 영상이 없어요")
        change.append("이번 주에 쇼츠 하나부터 올려 봐요")
    notes = []
    if short:
        notes.append(f"지난 점검 뒤 {int(days)}일밖에 안 지나서 올린 개수는 다음 점검에서 봐요")
    if delta is None:
        notes.append("기간을 시작할 때 구독자 기록이 없어서, 구독자 변화는 다음 점검부터 비교해요")
    if not saved:
        notes.append("아직 저장한 전략이 없어 방향 A 초안과 비교했어요")
    rec = {"id": hashlib.sha1(f"checkup|{now}".encode()).hexdigest()[:10], "at": now,
           "period": {"from": start, "to": now, "days": int(round(days)), "weeks": round(weeks, 1), "short": short, "base": (base or {}).get("at")},
           "plan": plan, "planN": {k: _approx(plan[k]) for k in ("L", "S")}, "actual": dict(actual, median=med),
           "subs": {"from": s0, "fromAt": s0at, "to": s1, "delta": delta, "src": delta_src, "studio": studio, "step": sub_step(s1)},
           "band": band, "good": good[:6], "bad": bad[:6], "change": list(dict.fromkeys(change))[:6], "studio": studio, "notes": notes,
           "draft": not saved}
    if fc:
        tr = fc["trajectory"]
        rec["forecastBand"] = {"at": now, "s0": s1, "p10": tr["p10"][:26], "p50": tr["p50"][:26], "p90": tr["p90"][:26],
                               "milestones": [{"id": m["id"], "p": m["p"], "target": m["target"], "horizon": m["horizon"]}
                                              for m in fc["milestones"] if m.get("kind") == "subs"]}
    return rec


def forward_check(items=None, hist=None, now=None):
    """지난 예측 맞춤 기록: 점검 때 저장한 띠(그날로 이어서 · 반올림 단위 넣음) 안에 그 뒤 실제 구독자가 들었는지 ·
    기한이 지난 목표의 Brier 점수. 점검 뒤 5일 안의 기록은 세지 않음."""
    now = now or time.time()
    items = items if items is not None else load_checkups()
    hist = hist if hist is not None else (read_history().get(OWN) or [])
    inside = n = 0
    pairs = []
    for c in items:
        fb = c.get("forecastBand") or {}
        if not fb.get("p50"):
            continue
        for r in hist:
            if not isinstance(r.get("subs"), (int, float)) or r["at"] - fb["at"] < BAND_MIN_DAYS * 86400:
                continue
            b = band_at(fb, r["at"])
            if b:
                n += 1
                inside += _band_state(r["subs"], b) == "inside"
        for m in fb.get("milestones") or []:
            due = fb["at"] + (26 if m["horizon"] == 6 else 52) * 7 * 86400
            if due <= now:
                s = _subs_at(hist, due)[0]
                if s is not None:
                    pairs.append((m["p"] / 100, 1.0 if s >= m["target"] else 0.0))
    return {"n": n, "inside": inside, "pct": round(100 * inside / n) if n else None, "brier": forecast.brier(pairs), "resolved": len(pairs)}


def remind(now=None):
    """매주 점검 알림: 켜져 있고 지난 점검(없으면 시작한 날)이 7일 넘게 지났으면 due."""
    now = now or time.time()
    st = load_state()
    if not st["settings"].get("remind"):
        return {"due": False, "days": None, "on": False}
    items = load_checkups()
    last = items[-1]["at"] if items else (st.get("strategy") or {}).get("startedAt")
    if not last:
        return {"due": False, "days": None, "on": True}
    days = int((now - last) // 86400)
    return {"due": days >= REMIND_DAYS, "days": days, "on": True, "never": not items}


# ---------- 클로드 (선택) ----------

AI_FORMAT = ('{"summary": "지금 채널 상황 한두 문장", "diagnosis": ["진단 1", "진단 2"], '
             '"actions": [{"text": "할 일", "why": "근거가 된 숫자", "priority": 1}], "titles": ["제목 예시 1", "제목 예시 2"], '
             '"risks": ["주의할 점"]}')


def data_hash(ov=None):
    ov = ov or {}
    return forecast.inputs_hash((ov.get("own") or {}).get("stats"), [c.get("key") for c in ov.get("competitors") or []],
                                ov.get("strategy"), [t["id"] for t in ov.get("takeaways") or []][:12])[:12]


def claude_prompt(ov=None, max_chars=6000):
    """숫자만 묶은 질문 (약 6000자까지): 우리 통계·분류 중앙값·경쟁 채널 상위 10곳·우리 전략·가져올 점 상위 12개·30/60/90·가능성·지난 점검."""
    ov = ov or overview(with_forecast=True)
    own = (ov.get("own") or {}).get("stats") or {}

    def n(x):
        return "?" if x is None else f"{x:,.0f}" if isinstance(x, (int, float)) and abs(x) >= 10 else f"{x:g}" if isinstance(x, (int, float)) else str(x)
    L = ["당신은 한국 축구·풋살 유튜브 채널 전략가예요. 아래는 '풋살사관학교'(최경진 감독, 다시 시작하는 풋살 레슨 채널)와 비슷한 채널들의 공개 숫자예요.",
         "규칙: 아래 숫자만 근거로 쓰고, 숫자나 확률을 새로 지어내지 마세요. 해요체 한국어로 짧게. 확률은 '가능성' 칸의 어림을 그대로 인용만 하세요.", "",
         f"## 우리 채널: 구독자 {n(own.get('subs'))} · 롱폼 보통 {n(own.get('L', {}).get('median'))}회(구독자 대비 {n(own.get('L', {}).get('reach'))}배) · "
         f"쇼츠 보통 {n(own.get('S', {}).get('median'))}회(구독자 대비 {n(own.get('S', {}).get('reach'))}배) · 주 {n(((own.get('cadence') or {}).get('perWeek') or {}).get('all'))}회 · "
         f"좋아요 비율 {n(round((own.get('likeRatio') or 0) * 100, 2))}%"]
    for g, x in (ov.get("groups") or {}).items():
        if x.get("n"):
            L.append(f"- 분류 '{g}' {x['n']}곳 가운데 값: 구독자 {n(x.get('subs'))} · 롱폼 {n(x.get('L'))} · 쇼츠 {n(x.get('S'))} · "
                     f"구독자 대비 조회 롱 {n(x.get('reachL'))}/쇼 {n(x.get('reachS'))} · 주 {n(x.get('perWeek'))}회")
    L.append("## 경쟁 채널 (구독자 큰 순 10곳)")
    comps = sorted([c for c in ov.get("competitors") or [] if c.get("stats")], key=lambda c: -((c.get("stats") or {}).get("subs") or 0))[:10]
    for c in comps:
        s = c.get("stats") or {}
        L.append(f"- {s.get('name')} ({s.get('group')}): 구독자 {n(s.get('subs'))} · 롱폼 보통 {n(s.get('L', {}).get('median'))} · 쇼츠 보통 {n(s.get('S', {}).get('median'))}"
                 f" · 주 {n(((s.get('cadence') or {}).get('perWeek') or {}).get('all'))}회 · 강점: {' / '.join((c.get('strengths') or [])[:2]) or '-'}")
    stg = ov.get("strategy") or {}
    L.append("## 우리 전략")
    L.append(f"- 방향 {stg.get('direction')} · 대상 {', '.join(stg.get('target') or [])} · 형식 "
             + ", ".join(f"{f['name']}(주 {f['perWeek']:g})" for f in stg.get("formats") or []))
    L.append(f"- 차별화: {stg.get('differentiation') or ''}")
    L.append("- 시리즈: " + ", ".join(s["name"] for s in stg.get("series") or []))
    L.append("## 가져올 점 상위 12개 (규칙 계산)")
    for t in [t for t in ov.get("takeaways") or [] if not t["hidden"]][:12]:
        e = (t.get("evidence") or [{}])[0]
        L.append(f"- [{t['category']}·맞춤 {t['fit']['score']}·{t['effort']}] {t['text']} — 근거: {e.get('channel')} {e.get('numbers')}")
    sol = ov.get("solution") or {}
    for p in sol.get("phases") or []:
        L.append(f"- {p['span']} {p['title']}: " + ", ".join(f"{u['name']} {u['count']}개" for u in p["uploads"]) + " · KPI " + "; ".join(k["text"] for k in p["kpi"][:3]))
    fc = ov.get("forecast") or {}
    if fc:
        r = fc.get("plan") or {}
        L.append(f"## 가능성 (어림 · 비교 채널 숫자로 계산 · 계획대로 롱폼 주 {r.get('L', 0):g}개·쇼츠 주 {r.get('S', 0):g}개 올린다고 본 값)")
        if fc.get("pace"):
            L.append(f"- {fc['pace']['text']}")
        for m in [m for m in fc.get("milestones") or [] if m.get("kind") == "subs" and not m.get("achieved")][:6]:
            L.append(f"- {m['label']} {m['horizon']}개월: {m['text']} (범위 {m['range']} · 믿을 만함 {m.get('confidence')})")
        for s in (fc.get("sensitivity") or [])[:4]:
            L.append(f"- {s['text']}")
        L.append("- 가정: " + " / ".join((fc.get("assumptions") or [])[:4]))
    ck = (ov.get("checkups") or {}).get("latest")
    if ck:
        L.append(f"## 지난 점검: 계획 {ck.get('plan')} · 실제 {({k: v for k, v in (ck.get('actual') or {}).items() if k in ('L', 'S')})} · 구독자 변화 {(ck.get('subs') or {}).get('delta')}")
    L += ["", "요청: 지금 숫자로 본 진단, 가장 먼저 할 일 3~5개(근거 숫자와 함께), 제목 예시 3개, 주의할 점을 주세요.",
          "아래 JSON 형식으로만 답해 주세요 (다른 글 없이):", AI_FORMAT]
    out = "\n".join(L)
    return out if len(out) <= max_chars else out[:max_chars - len(AI_FORMAT) - 40] + "\n…\n" + AI_FORMAT


def parse_ai(text, by="paste", model=None):
    """Claude 대답 → state.ai (글자 길이 자름). 형식이 아니면 StrategyError(한국어)."""
    s = str(text or "").strip()
    if len(s) > 60000:
        raise StrategyError("대답이 너무 길어요. JSON 부분만 붙여 넣어 주세요")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        s = m[1]
    else:
        a, b = s.find("{"), s.rfind("}")
        if a < 0 or b <= a:
            raise StrategyError("대답에서 JSON 형식을 찾지 못했어요. 대답 전체를 그대로 붙여 넣어 주세요")
        s = s[a:b + 1]
    try:
        d = json.loads(s)
    except ValueError:
        raise StrategyError("대답의 JSON 형식이 깨져 있어요. 대답 전체를 그대로 붙여 넣어 주세요") from None
    if not isinstance(d, dict):
        raise StrategyError("대답 형식이 달라요")

    def lst(x, n, cap):
        x = x if isinstance(x, list) else [x] if x else []
        return [re.sub(r"\s+", " ", str(v)).strip()[:cap] for v in x if str(v).strip()][:n]
    acts = []
    for a in d.get("actions") or []:
        if isinstance(a, dict) and str(a.get("text") or "").strip():
            try:
                pr = int(a.get("priority") or len(acts) + 1)
            except (TypeError, ValueError):
                pr = len(acts) + 1
            acts.append({"text": str(a["text"]).strip()[:300], "why": str(a.get("why") or "").strip()[:300], "priority": pr})
        elif isinstance(a, str) and a.strip():
            acts.append({"text": a.strip()[:300], "why": "", "priority": len(acts) + 1})
    out = {"summary": re.sub(r"\s+", " ", str(d.get("summary") or "")).strip()[:600], "diagnosis": lst(d.get("diagnosis"), 6, 300),
           "actions": sorted(acts, key=lambda a: a["priority"])[:8], "titles": lst(d.get("titles"), 6, 100), "risks": lst(d.get("risks"), 5, 300)}
    if not out["summary"] and not out["actions"] and not out["diagnosis"]:
        raise StrategyError("대답에 내용이 거의 없어요. 대답 전체를 그대로 붙여 넣어 주세요")
    out.update(by=by, model=model, at=time.time())
    return out


def save_ai(ai, dhash):
    ai = dict(ai, dataHash=dhash)
    _update_state(lambda d: d.__setitem__("ai", ai))
    return ai


def run_ai(log=print, cancel=None):
    """사용자 PC 의 Claude Code(내 클로드 계정)로 전략을 더 깊게 보기 → state.ai (작업 안에서)."""
    import claude_cli  # 지연 import: 앱 시작을 가볍게 (버튼을 누를 때만 씀)
    core.set_progress(label=JOB_AI, pct=None, detail="숫자를 묶는 중")
    ov = overview(with_forecast=True)
    prompt = claude_prompt(ov)

    def tick(sec):
        core.set_progress(label=JOB_AI, pct=None, detail=f"클로드가 전략을 보는 중… (내 클로드 계정 사용 · {sec}초)")
    try:
        res = claude_cli.run(prompt, cancel=cancel, on_tick=tick)
    except claude_cli.ClaudeError as e:
        log(f"클로드 전략 · {e.kind}")  # 종류만 (프롬프트·대답은 남기지 않음)
        return {"ok": False, "error": str(e), "kind": e.kind}
    try:
        ai = parse_ai(res["text"], by="claude-cli", model=res.get("model"))
    except StrategyError:
        log("클로드 전략 · 형식 다름")
        return {"ok": False, "error": "클로드 대답 형식이 달라요. 다시 눌러 주세요 (내 PC 분석 결과는 그대로 있어요)", "kind": "format"}
    ai = save_ai(ai, data_hash(ov))
    log("클로드 전략 · 저장했어요")
    return {"ok": True, "ai": ai}


# ---------- 화면 묶음 (GET /api/strategy) ----------

def _own_view(st, a, now):
    s = a["stats"]
    own = st.get("own") or {}
    oc = known_channels(st).get(OWN) or {}
    rev = own.get("revivedAt") or detect_revived(oc)
    vids = oc.get("videos") or {}
    since = [v for v in vids.values() if rev and isinstance(v.get("pub"), (int, float)) and v["pub"] >= rev]
    last28 = [v for v in vids.values() if isinstance(v.get("pub"), (int, float)) and now - v["pub"] <= 28 * 86400]
    return {"entry": own_entry(st), "stats": s, "mine": {"series": a["mine"].get("series"), "hashtags": a["mine"].get("hashtags")},
            "revivedAt": rev, "revivedDay": _day(rev), "revivedManual": bool(own.get("revivedManual")),
            "sinceRevive": {"n": len(since), "L": sum(1 for v in since if v.get("k") == "L"), "S": sum(1 for v in since if v.get("k") == "S"),
                            "views": int(sum(_vv(v.get("v") for v in since)))},
            "last28": {"n": len(last28), "L": sum(1 for v in last28 if v.get("k") == "L"), "S": sum(1 for v in last28 if v.get("k") == "S")}}


def _comp_view(entry, a, gs, styles, rec_idx, now):
    s = a["stats"] if a else None
    m = a["mine"] if a else {}
    rc = rec_idx.get(entry["key"]) or rec_idx.get(entry.get("channelId") or "") or {}
    sty = _style_for(s or entry, styles)
    view = {"entry": entry, "stats": s, "strengths": strengths(s, m, gs) if s else [],
            "topics": _topic_view(m),
            "formulas": _formula_view(m), "series": (m.get("series") or [])[:5], "hashtags": m.get("hashtags") or [], "nKo": m.get("nKo", 0),
            "styles": sty, "memo": {"tip": rc.get("tip"), "format": rc.get("format"), "category": rc.get("category"), "at": "2026-10-07"} if rc else None,
            "stale": bool(s and s.get("src") == "live" and (not s.get("listedAt") or now - s["listedAt"] > STALE)), "noData": s is None}
    return view


def _topic_view(m):
    best = {}
    for k in ("L", "S"):
        for tp in (m.get(k) or {}).get("topics") or []:
            if tp["w"] not in best or tp["mult"] > best[tp["w"]]["mult"]:
                best[tp["w"]] = dict(tp, fmt="롱폼" if k == "L" else "쇼츠")
    return sorted(best.values(), key=lambda x: -x["mult"])[:8]


def _formula_view(m):
    out = []
    for k, lab in (("L", "롱폼"), ("S", "쇼츠")):
        for f_id, x in ((m.get(k) or {}).get("formulas") or {}).items():
            if x["n"] and x["share"] >= 0.15:
                out.append({"id": f_id, "label": FORMULA_LABEL[f_id], "fmt": lab, "share": x["share"], "mult": x["mult"], "ok": x["ok"]})
    out.sort(key=lambda x: (not x["ok"], -(x["mult"] or 0)))
    return out[:8]


def week_view(st, oc, now, tks, rem):
    """'이번 주' 카드: 월요일부터 올린 개수 vs 계획 · 먼저 할 일 2개 · 다음 점검 날."""
    lt = datetime.fromtimestamp(now, timezone.utc).astimezone()
    monday = (lt - timedelta(days=lt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    vids = (oc or {}).get("videos") or {}
    wk = [v for v in vids.values() if isinstance(v.get("pub"), (int, float)) and monday <= v["pub"] <= now]
    rates = plan_rates(current_strategy(st))
    fit = {t["id"]: t["fit"]["score"] for t in tks}
    open_ = sorted((t for t in st["todos"] if not t.get("done")),
                   key=lambda t: (-(fit.get((t.get("from") or {}).get("takeaway")) or 50), t.get("createdAt") or 0))
    items = load_checkups()
    last = items[-1]["at"] if items else (st.get("strategy") or {}).get("startedAt")
    return {"from": monday, "L": sum(1 for v in wk if v.get("k") == "L"), "S": sum(1 for v in wk if v.get("k") == "S"),
            "planL": rates["L"], "planS": rates["S"], "saved": st.get("strategy") is not None,
            "todos": [{"id": t["id"], "text": t["text"], "category": t.get("category")} for t in open_[:2]], "openN": len(open_),
            "checkDue": (last + REMIND_DAYS * 86400) if last else None, "checkOn": bool(rem.get("on")), "checkNow": bool(rem.get("due")),
            "rssAt": ((oc or {}).get("rss") or {}).get("at")}


def _preset_refs(ana):
    """방향 A/B/C 의 참고 채널(조사) 숫자: 보통 조회수 가운데 값 (롱폼·쇼츠)."""
    rec = recommended()
    by_name = {c.get("name"): _rec_key(c) for c in rec.get("channels") or []}
    out = {}
    for d in rec.get("directions") or []:
        ks = [by_name.get(c.get("channel")) for c in d.get("channels") or []]
        st_ = [ana[k]["stats"] for k in ks if k and k in ana]
        L = [x["L"].get("median") for x in st_ if x["L"].get("median")]
        S = [x["S"].get("median") for x in st_ if x["S"].get("median")]
        out[d.get("key")] = {"n": len(st_), "L": round(_med(L)) if L else None, "S": round(_med(S)) if S else None}
    return out


def overview(with_forecast=False):
    """8단계 화면 전체 (숫자 계산은 규칙 · 가능성은 with_forecast 일 때만 · 화면은 /api/strategy/forecast 로 따로 받음)."""
    now = time.time()
    st = load_state()
    chans = known_channels(st)
    ana = {k: _ana(c, now) for k, c in chans.items()}
    gs = group_summary(ana)
    for g, x in gs.items():
        x["nComp"] = sum(1 for c in st["competitors"] if c.get("group") == g)
    styles = learned_styles()
    rec_idx = _rec_index()
    own_a = ana.get(OWN) or {"stats": channel_stats(_new_channel(own_entry(st)), now), "mine": mine({})}
    own = _own_view(st, own_a, now)
    own["gaps"] = _gap_lines(own_a["stats"], gs)
    comps = [_comp_view(e, ana.get(e["key"]), gs, styles, rec_idx, now) for e in st["competitors"]]
    tks = takeaways(st, ana, chans, now)
    added = {c["key"] for c in st["competitors"]} | {c.get("channelId") for c in st["competitors"] if c.get("channelId")}
    reco = [dict(c, key=_rec_key(c), added=bool(_rec_key(c) in added or c.get("channelId") in added)) for c in recommended().get("channels") or [] if _rec_key(c)]
    todo = plan_refresh(None, "normal", now, st)
    oc = chans.get(OWN) or {}
    live = live_channels()
    lasts = [c.get("at") for c in live.values() if c.get("at")]
    fc = None
    fc_err = None
    if with_forecast:
        try:
            fc = forecast_result(st)
        except forecast.ForecastError as e:
            fc_err = str(e)
    cks = load_checkups()
    topics = [t["pattern"].split(":", 1)[1] for t in tks if t["rule"] == "R-TOPIC" and not t["hidden"]][:4]
    sol = solution(st, tks, fc, gs, topics, own_a["stats"]) if with_forecast else None
    rem = remind(now)
    refs_ = _preset_refs(ana)
    pre = [dict(x, refs=refs_.get(x["key"]) or {}) for x in presets()]
    sd = seed()
    out = {"ok": True, "now": now, "own": own, "competitors": comps, "groups": gs, "groupNames": list(GROUPS), "recommended": reco,
           "strategy": current_strategy(st), "saved": st.get("strategy") is not None, "presets": pre,
           "directionNames": DIRECTION_NAMES, "targets": list(TARGETS), "cats": list(CATS),
           "takeaways": tks, "todos": st["todos"], "settings": st["settings"],
           "checkups": {"latest": cks[-1] if cks else None, "items": [{"at": c["at"], "good": len(c.get("good") or []), "bad": len(c.get("bad") or []),
                                                                        "subs": (c.get("subs") or {}).get("to"), "id": c.get("id")} for c in cks[-30:]][::-1],
                        "trend": [{"at": r["at"], "subs": r.get("subs")} for r in (read_history().get(OWN) or []) if isinstance(r.get("subs"), (int, float))][-60:],
                        "forward": forward_check(cks)},
           "ai": st.get("ai"), "remind": rem, "week": week_view(st, oc, now, tks, rem),
           "refresh": {"lastAt": max(lasts) if lasts else None, "staleN": len(todo), "estimate": estimate(todo),
                       "pause": st.get("pause") if _paused(st, now) else None, "ownAt": listed_at(oc),
                       "ownStale": not (listed_at(oc) and now - listed_at(oc) < OWN_STALE),
                       "liveN": len(live), "seedAt": sd["at"] if sd["channels"] else None, "seedOnly": not live, "seedN": len(sd["channels"])},
           "jobs": {"refresh": JOB_REFRESH, "own": JOB_OWN, "check": JOB_CHECK, "ai": JOB_AI}}
    if with_forecast:
        out.update(forecast=fc, forecastError=fc_err, solution=sol)
    out["aiStale"] = bool(out["ai"] and out["ai"].get("dataHash") != data_hash(out))
    return out


def solution_view():
    """성공 솔루션만 (가능성 계산을 포함). 다시 만든 시각은 캐시 파일 solution.json 에 (GET 이 사용자 기록 state.json 을 쓰지 않게) ·
    '다시 만들었어요'는 전략이나 채널 자료가 바뀌었을 때만."""
    st = load_state()
    now = time.time()
    chans = known_channels(st)
    ana = {k: _ana(c, now) for k, c in chans.items()}
    gs = group_summary(ana)
    tks = takeaways(st, ana, chans, now)
    try:
        fc = forecast_result(st)
    except forecast.ForecastError:
        fc = None
    topics = [t["pattern"].split(":", 1)[1] for t in tks if t["rule"] == "R-TOPIC" and not t["hidden"]][:4]
    own_a = ana.get(OWN)
    sol = solution(st, tks, fc, gs, topics, (own_a or {}).get("stats"))
    strat = {k: v for k, v in (st.get("strategy") or {}).items() if k != "updatedAt"}
    h = forecast.inputs_hash((fc or {}).get("inputsHash"), strat)
    prev = _read(_path("solution.json"), dict)
    if prev.get("hash") != h:
        changed, at = bool(prev.get("hash")), now
        try:
            _write(_path("solution.json"), {"hash": h, "at": at})
        except OSError:
            pass
    else:
        changed, at = False, prev.get("at")
    return dict(sol, at=at, changed=changed)


def day_iso(ts):
    """화면 날짜 칸용 YYYY-MM-DD (현지 날짜)."""
    return _day(ts)


def parse_day(s):
    """화면 날짜 칸 YYYY-MM-DD → 그날 정오(현지) 시각."""
    try:
        d = datetime.strptime(str(s), "%Y-%m-%d")
    except ValueError:
        raise StrategyError("날짜를 YYYY-MM-DD 로 넣어 주세요") from None
    return (d + timedelta(hours=12)).timestamp()
