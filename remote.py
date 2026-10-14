"""원격 접속 '실행기'(PC 왼쪽 아래 ON/OFF · 예전 이름 '휴대폰으로 보기' · D-310) — 원격 경계 (D-027).

PC 에서 하는 작업을 휴대폰(SITE · 지금 https://futsal.mulgyeol.kr)에서 보고, 정해진 몇 가지 일을 시키는 곳. 바깥(터널)에서 온 요청은 이 모듈만 받는다.
  - 원격 리스너: 켜 둔 동안만 127.0.0.1:<임의 포트> 에 따로 뜨는 작은 서버(앱 화면 포트 창 app_ports 밖). 화면용 로컬 서버(8765)는
    터널 뒤에 두지 않는다. /r/* 는 여기서, 그 밖의 길(밖에서 작업 · PC 스튜디오 화면 · D-230)은 rstudio.gate 가 세션 쿠키로 막은 뒤
    앱의 처리 코드(app.StudioHandler)를 같은 프로세스 안에서 부른다 — 넘겨주기 표는 r_handoff.
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
import unicodedata
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import autorun
import core
import editor
import qa
import refs
import rlogin
import rstudio
import source
import strategy
import style
import thumb
import thumbcopy
import thumbstyle
import trouble
import tunnel
import updater
import youtube_api as yt_api
import youtube_upload

API = 1
SENTINEL = "remote.futsal.invalid"  # cloudflared 가 원격 리스너로 보낼 때 쓰는 Host (.invalid 는 어떤 브라우저도 만들 수 없음)
# 휴대폰·밖에서 작업 페이지 주소 — 이 한 줄이 하나뿐인 원본 (D-240 · D-233 · 와벨리 저장소 public/futsal 을 Vercel 프로젝트 wavely 가
# 뿌리 '/' 에서 내줌): 연결 QR·링크 · 알림을 누르면 열 곳 · 허용 출처(ORIGINS) · PC 화면 안내 · 알림 글 · 밖에서 작업 화면의 '← 원격 홈'이
# 모두 여기서 나온다. 다른 주소로 옮길 때는 이 줄 + Vercel 도메인만 바꿈 (페이지는 어느 앞머리에서도 같은 파일로 돎).
SITE = "https://futsal.mulgyeol.kr"


def site_origin(site):
    """주소 → 출처 (scheme://host[:port], 작은 글자)."""
    u = urlparse(str(site or ""))
    return f"{u.scheme.lower()}://{u.netloc.lower()}" if u.scheme and u.netloc else ""


ORIGINS = (site_origin(SITE),)  # 그 페이지 출처 하나만 — mulgyeol.kr·www.mulgyeol.kr 은 와벨리 커뮤니티라 믿지 않음 (D-240)
SITE_TEXT = SITE.split("://", 1)[-1]  # 알림·안내 글에 쓰는 모양 (futsal.mulgyeol.kr)
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
CONCURRENCY = 48               # 처리 동시 (밖에서 작업 화면은 한 번에 여러 요청 · Cloudflare 빠른 터널은 약 200)
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
# (최대, 초당 채움) · studio: 밖에서 작업 화면 (D-230) · login: 아이디·비밀번호로 연결의 열린 두 길 (start·finish 같이 · D-250)
# login 은 인터넷 대역(net_of: IPv4 /24 · IPv6 /48)마다 — IPv6 /64 안에서 주소를 바꿔 가며 보내도 한 바구니 · login_start 는 PC 하나에
# 모두 합쳐 (start 하나가 3072비트 거듭제곱 둘 · 약 15~30ms → 1초에 2개면 CPU 몇 %만 · 대역을 많이 가진 쪽의 CPU 쏟아붓기 막음)
BUCKETS = {"open": (10, 1 / 6), "auth": (60, 5.0), "studio": (300, 40.0), "login": (12, 1 / 5), "login_start": (60, 2.0)}
# 아이디·비밀번호로 연결 (D-250~D-258): 열린 길 둘 (세션·서명 없이 · Origin 은 필요) — 그 밖(/r/login/events·kick·unlock·logout)은 서명 길
LOGIN_OPEN = (("POST", "/r/login/start"), ("POST", "/r/login/finish"))
LOGIN_BODY_MAX = 4096
GUEST_IDLE, GUEST_AGE, GUEST_MAX = 1800, 43200, 3   # '이번만' 기기: 30분 안 쓰면 · 12시간이면 끝 · 3대까지 (메모리에만)
LOOKUP_HB = 10800            # 'PC 찾기' 기록을 3시간마다 다시 (ntfy 는 12시간 담아 둠)
COLLISION_AGE = 43200        # 다른 PC 의 같은 아이디 기록이 12시간 안이면 겹침
LOGIN_CONTROL_TIMEOUT = 90   # [끊기] 주제 듣기: ntfy 는 45초마다 keepalive
PRESET_MARK = "remote-preset.json"   # 미리 정한 로그인(D-258)을 이 PC 에서 다룬 표 (remote.json 옆 · 그 파일과 따로)
LRU_MAX = 10000
MISSING_MSG = "실행기에 필요한 구성요소가 없어요 · 왼쪽 아래 '업데이트 확인' → 다운로드 엔진 최신으로 바꾼 뒤 다시 켜 주세요"
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
              youtube_upload.JOB_NAME, youtube_upload.JOB_FINISH,
              # AI 추천 썸네일 (PC 썸네일 편집기에서만 시작 · 휴대폰에는 진행·알림만 · 썸네일 작업은 휴대폰 허용 목록(ACTIONS)에 없음)
              thumb.JOB_ANALYZE, thumbcopy.JOB_AI, thumbcopy.JOB_JUDGE,
              # 썸네일 버릇 배우기 (PC 스타일 카드에서만 시작 · D-130)
              thumbstyle.JOB,
              # 끝까지 자동으로 (PC 보관함·휴대폰 'auto' · D-203)
              autorun.JOB,
              # 쉴 때 새 버전을 받는 동안 잡는 작업 자리 (app.AUTO_JOB · D-311)
              "자동 업데이트"}
# OFF_REASONS 값은 비콘·조회 기록으로 페이지에 가는 약속 글자 (페이지가 글자 그대로 맞춰 봄) → 화면 이름이 '실행기'로 바뀌어도 그대로 (D-310)
OFF_REASONS = {"app": "앱을 껐어요", "user": "원격 접속을 껐어요", "idle": "오래 쓰지 않아서 껐어요", "error": "연결이 끊겼어요"}
# 밖에 있을 때 할 수 있는 것: 원격 홈의 [더보기] → [실행기 끄기] (모든 연결·밖에서 작업이 바로 끝남) · PC 앞이면 [끊기] (D-239)
NOT_ME = "내가 한 게 아니면 원격 홈의 [더보기] → [실행기 끄기]를 누르거나, PC의 '실행기'에서 [끊기]를 눌러 주세요"
NOTE_TEXT = {
    "paired": "새 기기가 연결됐어요 ({kind}) · " + NOT_ME,
    "test": "알림 시험이에요 · 잘 받았다면 준비 끝!",
    "tunnel": "확인이 필요해요 · 원격 연결이 자꾸 끊겨요",
    "blocked": "확인이 필요해요 · YouTube가 막았어요",
    "missed": "확인이 필요해요 · 받지 못한 영상이 있어요",
    "partial": "확인이 필요해요 · 편집점을 찾지 못한 영상이 있어요",
    "net": "확인이 필요해요 · PC 인터넷이 끊겨 다 끝내지 못했어요",
    "studio": "밖에서 PC 스튜디오를 열었어요 ({kind}) · " + NOT_ME,
    "auto": "확인이 필요해요 · 끝까지 자동으로 · 다 했지만 PC 보관함의 결과 카드에서 볼 것이 있어요",
    # 아이디·비밀번호로 연결 (D-255): 늘 보냄(알림 켜기·끄기와 상관없음) · 정해진 낱말만 (login_note)
    "login": "아이디로 새 기기가 연결됐어요 ({how}) · {dev} · {where}{at} · 내가 아니면 [끊기]를 눌러 주세요",
    "login_fail": "아이디·비밀번호 연결에서 비밀번호가 {n}번 틀렸어요 · 내가 아니면 PC에서 비밀번호를 바꿔 주세요",
    "login_stop": "비밀번호가 너무 많이 틀려서 아이디·비밀번호 연결을 잠갔어요 · 연결된 휴대폰은 그대로 써요 · 휴대폰 '더보기'나 PC에서 다시 켤 수 있어요",
    "login_kill": "끊었어요 · 그 기기 연결을 끊고 아이디·비밀번호 연결을 잠갔어요 · PC에서 새 비밀번호를 정해 주세요",
    "moved": f"알림 주제가 바뀌었어요 · 이 주제로는 더 이상 알림이 오지 않아요 · {SITE_TEXT} 에서 새 주제로 다시 구독해 주세요 "
             "(스튜디오 알림은 무엇을 설치하라고 하지 않아요)",
}
# 알림에 넣는 기기 종류: 정해진 낱말만 (기기가 보낸 이름 칸은 짝지은 쪽이 아무 글이나 넣을 수 있어 알림에 그대로 넣지 않음 · D-239)
DEV_KINDS = {"iPhone": "아이폰", "iPad": "아이패드", "Galaxy": "갤럭시", "Android": "안드로이드", "Mac": "Mac",
             "Windows 노트북": "Windows 노트북", "Windows": "Windows 노트북", "PC": "Windows 노트북", "컴퓨터": "컴퓨터"}
DEV_BROWSERS = {"Safari": "Safari", "Chrome": "Chrome", "Edge": "Edge", "Firefox": "Firefox", "삼성 인터넷": "삼성 인터넷"}


def dev_name(name):
    """기기 이름 저장 꼴: 예전 휴대폰 페이지는 Windows 노트북을 'PC · Chrome' 으로 지었음 → PC 화면의 '밖에서 작업 중 · PC · Chrome' 이
    이 PC 처럼 읽힘 → 'Windows 노트북 · Chrome' (지금 페이지 deviceName 과 같음 · D-239)."""
    n = clean(name, 40) or "휴대폰"
    return "Windows 노트북" + n[2:] if re.match(r"PC(\s*·|$)", n) else n


def device_kind(name):
    """기기 이름('iPhone · Safari · 홈 화면' 꼴 · 휴대폰 페이지 deviceName) → 알림용 정해진 낱말 ('아이폰 · Safari' · 모르면 '기기')."""
    parts = [x.strip() for x in str(name or "").split("·")]
    k = DEV_KINDS.get(parts[0], "기기")
    b = next((DEV_BROWSERS[x] for x in parts[1:] if x in DEV_BROWSERS), "")
    return f"{k} · {b}" if b else k


def note_text(kind, name=None):
    """정해진 알림 문장 (기기 종류 칸이 있으면 device_kind 로만 채움)."""
    return NOTE_TEXT[kind].replace("{kind}", device_kind(name))


def ko_time(ts):
    """알림·기록용 '오후 2:05' (이 PC 시각)."""
    lt = time.localtime(ts)
    return f"{'오전' if lt.tm_hour < 12 else '오후'} {lt.tm_hour % 12 or 12}:{lt.tm_min:02d}"


def login_note(guest, ua, cc, at):
    """새 기기 알림 (D-255): 정해진 낱말만 — 기기는 User-Agent 에서 고른 낱말 · 나라는 Cf-Ipcountry · 기기가 보낸 이름은 넣지 않음."""
    where = rlogin.country(cc)
    return (NOTE_TEXT["login"].replace("{how}", "이번만" if guest else "기억함").replace("{dev}", rlogin.ua_vocab(ua))
            .replace("{where}", f"{where} · " if where else "").replace("{at}", ko_time(at)))


# 아이디·비밀번호로 연결 — 휴대폰 페이지에 돌려주는 정해진 문장 (페이지는 code 로 자기 문장을 고름)
LOGIN_MSG = {
    "not_ready": "PC가 아직 준비 중이에요 · 잠시 뒤 다시 해 주세요",
    "login_off": "이 PC는 지금 아이디·비밀번호 연결을 쓰지 않아요 · PC '실행기'에서 켜 주세요",
    "suspended": "아이디·비밀번호 연결이 잠겨 있어요",
    "locked": "비밀번호를 여러 번 틀려서 잠시 막았어요",
    "busy": "지금 연결하려는 기기가 많아요 · 잠시 뒤 다시 해 주세요",
    "again": "다시 해 주세요",
    "bad": "비밀번호가 맞지 않아요",
    "bad_request": "잘못된 요청이에요",
    "not_allowed": "QR·코드로 연결한 휴대폰이나 PC에서 할 수 있어요",
    "guest_off": "이번만 연결한 기기에서는 실행기를 끌 수 없어요 · 다 쓰면 [이 기기에서 나가기]를 눌러 주세요",
}
LOGIN_ACK_MSG = ("연결된 휴대폰이 없어서 새 기기 알림을 받을 곳이 없어요 · 먼저 휴대폰을 QR로 연결하고 알림을 켜 두는 걸 권해요")
LOGIN_TAKEN_MSG = "이 아이디는 다른 PC가 쓰고 있어요 · 다른 아이디를 골라 주세요"
LOGIN_UNCHECKED_MSG = "겹치는지 확인하지 못했어요 (인터넷 확인) · 그래도 쓸까요?"
LOGIN_BAD_BLOCK_MSG = "아이디·비밀번호 설정을 읽지 못했어요 · 다시 정해 주세요"


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
# 끝까지 자동으로: 멈추거나 단계 몇 개가 안 돼도 작업은 끝까지 가서 결과(public · ok 없음)를 돌려줌 → 휴대폰에 '끝 ✓'로 보이면 안 됨 (D-207)
AUTO_MSG = {
    "stopped": "멈췄어요 · 만든 것은 그대로예요 · PC 보관함의 [끝까지 자동으로] → [이어서 하기]로 나머지를 해요",
    "check": "다 했지만 확인할 것이 {n}개 있어요 · PC 보관함의 결과 카드에서 봐 주세요 (유튜브에는 아직 올리지 않았어요)",
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
             thumbcopy.JOB_AI, thumbcopy.JOB_JUDGE,  # 클로드 문구·평가: claude_cli.run 이 editor.CANCEL 을 봄 (썸네일 분석은 장면·선수 살피기(frame_candidates)가 멈추기 신호를 안 봄 → 빠짐 · 자동 누끼 자식은 ✕ 로 꺼짐 · D-069)
             autorun.JOB,  # 끝까지 자동으로: 단계마다·내보내기·MSG·썸네일 기다리기가 editor.CANCEL 을 봄 → 만든 것은 두고 [이어서 하기]
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
    def __init__(self, msg, status=400, code=None):
        super().__init__(msg)
        self.status, self.code = status, code


class LoginError(Exception):
    """/r/login/* 의 대답 (정해진 문장 + code + 덧붙일 칸)."""

    def __init__(self, status, code, extra=None, counted=False):
        super().__init__(LOGIN_MSG.get(code, LOGIN_MSG["bad_request"]))
        self.status, self.code, self.extra = status, code, dict(extra or {})
        self.counted = counted   # 틀린 비밀번호로 센 시도 (그 시도로 잠가서 'suspended' 로 대답해도)


class LoginAsk(ValueError):
    """PC 화면의 설정이 한 번 더 물어야 함 (code: need_ack · unchecked) — app 이 409 로."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


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
    (re.compile(r"\bfs[bnplc][0-9a-f]{24}\b"), "fs…"),  # 비콘(fsb)·알림(fsn)·짝짓기 만남(fsp)·PC 찾기(fsl)·[끊기](fsc) 주제
    # 아이디·비밀번호로 연결 (D-250): [끊기] 표·PC 찾기 기록·알림의 #kill=·비밀번호와 검증값이 든 JSON·repr 칸
    (re.compile(r"\bfsk1\.[A-Za-z0-9_-]+"), "fsk1.…"),
    (re.compile(r"\bfsl[12]\.[A-Za-z0-9_.-]+"), "fsl….…"),
    (re.compile(r"([#&?](?:kill|seen)=)[^&\s'\"]+"), r"\1…"),
    (re.compile(r'("(?:password|password2|verifier|fake|bkey|idc)"\s*:\s*")[^"]*'), r"\1…"),
    (re.compile(r"('(?:password|password2|verifier|fake|bkey|idc)'\s*:\s*')[^']*"), r"\1…"),
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
    # 밖에서 작업 (D-230): 넘겨주기 표(#t=) · 세션 쿠키 · CSRF·확인 머리글 · JSON 의 token·csrf 칸
    (re.compile(r"([#&?]t=)[A-Za-z0-9_-]{20,}"), r"\1…"),
    (re.compile(r"\b((?:__Host-)?fs_s=)[^;\s'\"]+"), r"\1…"),
    (re.compile(r"\b(X-FS-(?:CSRF|Confirm)['\"]?\s*[:=,]\s*['\"]?)[^\s'\",}]+", re.I), r"\1…"),
    (re.compile(r'("(?:token|csrf)"\s*:\s*")[^"]*'), r"\1…"),
    (re.compile(r"('(?:token|csrf)'\s*:\s*')[^']*"), r"\1…"),
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


LISTING_BAD = "YouTube 채널·영상·재생목록 주소만 넣을 수 있어요"


def listing_url(s):
    """밖에서 작업(D-235)의 '채널 불러오기'·'학습용 영상 받기' 주소 → 새로 만든 YouTube 표준 주소 ('' 는 우리 채널).
    YouTube 영상 하나(youtube_id) · 재생목록(youtube.com/playlist?list=) · 채널(channel_url · PC 화면처럼 @ 없는 핸들도)만 —
    그 밖(다른 사이트·집 안 공유기·내부망 주소 등)은 ValueError. 원래 글은 yt-dlp 에 넘기지 않는다 (PC 가 엉뚱한 곳에 요청하지 않게)."""
    s = str(s or "").strip()
    if not s:
        return ""
    if len(s) > 300:
        raise ValueError(LISTING_BAD)
    try:
        return "https://www.youtube.com/watch?v=" + youtube_id(s)
    except ValueError:
        pass
    scheme = re.match(r"^https?://", s, re.I)
    if not scheme and not re.match(r"(?:(?:www|m)\.)?(?:youtube\.com|youtu\.be)/", s, re.I):
        s = s if s.startswith("@") else "@" + s  # 핸들 (core._listing_target 과 같은 해석)
    else:
        u = urlparse(s if scheme else "https://" + s)
        if u.path.rstrip("/") == "/playlist":
            lid = (parse_qs(u.query).get("list") or [""])[0]
            if ((u.hostname or "").lower() not in _YT_HOSTS or u.username or u.password or u.port not in (None, 80, 443)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{2,80}", lid)):
                raise ValueError(LISTING_BAD)
            return "https://www.youtube.com/playlist?list=" + lid
    try:
        return channel_url(s)
    except ValueError:
        raise ValueError(LISTING_BAD) from None


# ---------- 저장 (~/.futsal-studio/remote.json) ----------

def _fresh():
    return {"v": 1, "pc": {"id": secrets.token_hex(8), "name": "내 PC"}, "enabled": False,
            # PC 잠들지 않게: 기본은 '켜 둔 동안 항상' (잠들면 밖에서 새 작업을 못 시킴 · 원격 접속 자체가 소유자가 켜는 것)
            # 늘 켜기 (D-295 · 소유자 결정): 스튜디오가 켜져 있으면 원격 접속도 켬 (앱을 켤 때 저절로) · 자동으로 끄기는 기본 '안 함'
            "settings": {"alwaysOn": True, "autoOffHours": 0, "keepAwake": "always", "notify": {"done": True, "failed": True, "attention": True},
                         # 밖에서 작업 허용 (D-234): 기본 켜짐 — 기기를 연결해야 쓸 수 있으므로 사실상 '연결하면 켜짐' · 끄면 휴대폰으로 보기만
                         "studio": True},
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
    """기기 열쇠·알림 주제가 든 비밀 파일. 앱 폴더·config.json 밖(사용자 폴더), 바꿔 끼우기로 저장, 깨지면 .bad 로 남기고 새로.
    'login' 블록(아이디·비밀번호로 연결 · D-250)은 따로 확인: 깨졌으면 메모리에서만 끄고(login_bad) 파일·기기 목록은 그대로.
    guests: '이번만' 기기 (메모리에만 · 파일·비콘에 없음 · D-256) — device() 는 저장된 기기 다음에 여기서도 찾음."""

    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.lock = threading.RLock()
        self._saved_seen = 0.0
        self.login_bad = False
        self.guests = {}
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
        if "alwaysOn" not in d["settings"]:  # 늘 켜기가 생기기 전 기록 (D-295): 한 번만 늘 켜기 + 자동으로 끄기 안 함 (소유자 PC 가 업데이트만으로)
            base["settings"]["alwaysOn"], base["settings"]["autoOffHours"] = True, 0
        d["settings"] = base["settings"]
        for x in d["devices"]:  # 예전 페이지가 'PC · …' 로 지은 노트북 이름 (D-239)
            x["name"] = dev_name(x.get("name"))
        if d.get("login") is not None and not rlogin.valid_login(d["login"]):  # 그 블록만 끔 (.bad 로 바꾸지 않음 · 기기는 그대로)
            d["login"], self.login_bad = None, True
        return d

    def login(self):
        """아이디·비밀번호로 연결 블록 (없으면 None)."""
        return self.data.get("login")

    def save(self):
        """공통 저장 도구(updater.write_atomic): 임시 파일을 나만 읽게(0600 · Windows 는 무시) 해 둔 뒤 바꿔 끼움 ·
        백신·OneDrive 가 잠깐 잡으면 기다렸다 다시 · 실패하면 열쇠가 든 임시 파일을 지우고 오류를 올려 보냄."""
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            updater.write_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=1), mode=0o600)

    def device(self, did):
        return next((x for x in self.data["devices"] if x["id"] == did), None) or self.guests.get(did)

    def add_device(self, name, install=None, via="code", gen=None):
        """새 기기 → (기기, 바꿔 끼운 옛 기기들). 같은 브라우저(install)가 다시 연결하면 옛 항목을 지우고 새것으로.
        via: 'code'(QR·코드) · 'login'(아이디·비밀번호 · gen 은 그때 비밀번호 판) — 없으면 code 로 봄."""
        with self.lock:
            now = int(self.clock())
            dev = {"id": secrets.token_hex(8), "name": dev_name(name), "created": now, "lastSeen": now,
                   "auth": _b64u(secrets.token_bytes(32)), "beacon": _b64u(secrets.token_bytes(32)), "via": via}
            if via == "login":
                dev["loginGen"] = int(gen or 0)
            old = []
            if install and INSTALL_RE.fullmatch(install):
                dev["install"] = install
                old = [x for x in self.data["devices"] if x.get("install") == install]
            self.data["devices"] = [x for x in self.data["devices"] if x not in old] + [dev]
            self.save()
            return dev, old

    def add_guest(self, name, scale=1.0):
        """'이번만' 기기 → (기기, 밀려난 옛 이번만 기기들) · 3대가 넘으면 가장 오래된 것부터."""
        with self.lock:
            now = int(self.clock())
            dev = {"id": secrets.token_hex(8), "name": dev_name(name), "created": now, "lastSeen": now,
                   "auth": _b64u(secrets.token_bytes(32)), "beacon": _b64u(secrets.token_bytes(32)),
                   "exp": now + int(GUEST_AGE * scale), "guest": True, "via": "login"}
            self.guests[dev["id"]] = dev
            out = []
            while len(self.guests) > GUEST_MAX:
                old = min(self.guests.values(), key=lambda x: x["created"])
                out.append(self.guests.pop(old["id"]))
            return dev, out

    def take_guests(self, ids=None):
        """이번만 기기 떼어 내기 (ids 가 None 이면 모두) → 뗀 기기들."""
        with self.lock:
            gone = [g for k, g in self.guests.items() if ids is None or k in ids]
            for g in gone:
                self.guests.pop(g["id"], None)
            return gone

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
        if dev.get("guest"):  # 이번만 기기는 파일에 없음
            return
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
    analyze(b) 편집점 찾기 + 가편집 · refs_job(fn) 학습용 작업 안내 처리 · version ·
    studio: 밖에서 작업 화면 처리기 클래스 (app.StudioHandler · 없으면 /r/* 밖의 요청은 모두 401)."""

    def __init__(self, log, start_job, job, logs, analyze, refs_job, version, studio=None):
        self.log, self.start_job, self.job, self.logs = log, start_job, job, logs
        self.analyze, self.refs_job, self.version = analyze, refs_job, version
        self.studio = studio


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
# 하루 몫(180개)을 넘겨도 보내는 알림 — 보안 알림(아이디로 새 기기·틀린 비밀번호·잠금·끊음)은 늘 (D-255)
IMPORTANT_NOTES = ("attention", "failed", "paired", "moved", "studio", "login", "login_fail", "login_stop", "login_kill")

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

    def notify(self, kind, text, prio=3, tags="bell", topic=None, click=None, actions=None):
        self.q.put(("notify", kind, text, prio, tags, topic, click, actions))

    def rendezvous(self, topic, body):
        self.q.put(("raw", topic, body))

    def lookup(self, topic, body):
        """'PC 찾기' 기록 (아이디·비밀번호로 연결 · D-252) — 하루 몫에서 중요한 것으로."""
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
                    _, kind, text, prio, tags, topic, click, actions = item
                    if self._budget(kind in IMPORTANT_NOTES):
                        self._post_notify(text, prio, tags, topic, click, actions)
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

    def _post_notify(self, text, prio, tags, topic=None, click=None, actions=None):
        msg = {"topic": topic or self.svc.store.data["topics"]["notify"], "title": "풋살 스튜디오", "message": text,
               "tags": [tags], "priority": prio, "click": click or self.svc.site()}
        if actions:  # 알림의 [끊기] 단추 (ntfy http 동작 · D-255)
            msg["actions"] = actions
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
        self._busy = 0          # 원격 리스너가 지금 처리 중인 요청 수 (받기·영상 보기·올리기 조각 포함) → in_flight · 자동 업데이트가 끊지 않게 (D-311)
        self._busy_lock = threading.Lock()
        self.last_auth = clock()  # 휴대폰 원격 홈·밖에서 작업이 마지막으로 연결 정보를 보인 때 (자동 끄기 · 자동 업데이트 D-311)
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
        self.studio = None      # 밖에서 작업 (rstudio.Studio · 세션·넘겨주기·확인·올리기)
        self._hcls = None
        self._up_swept = 0.0
        # 아이디·비밀번호로 연결 (D-250~D-258): 로그인 블록을 바꾸는 일(정하기·끄기·세기·끊기)은 모두 이 잠금 하나 안에서
        self.login_lock = threading.RLock()
        self.pending = None
        self._guard_obj = None
        self._lookup_at = 0.0       # 'PC 찾기' 기록을 마지막으로 올린 때 (3시간 하트비트)
        self._collision_at = 0.0    # 같은 아이디를 쓰는 다른 PC 를 마지막으로 살핀 때
        self._ctl = None            # [끊기] 주제를 듣는 스레드 (remote-login-control)
        self._ctl_seen = collections.deque()
        self._ctl_timeout = LOGIN_CONTROL_TIMEOUT
        self._ctl_stop = threading.Event()   # 앱을 끌 때 (데몬 스레드라 남아도 앱 끄기를 막지 않음)
        self._preset_thread = None           # 미리 정한 로그인 넣기 (D-258 · 같은 아이디를 쓰는 PC 살피기)

    # ----- 준비 -----
    def init(self, bridge):
        self.bridge = bridge
        home = self.home or core.ENGINE_HOME
        self.store = Store(home / "remote.json", self.clock)
        self.auth = Auth(self.store, self.clock)
        self.pairing = Pairing(self.clock)
        self.tickets = Tickets(self.clock)
        self.limiter = Limiter(self.clock)
        self.studio = rstudio.Studio(self)
        self.pending = rlogin.Pending(self.clock)
        self._guard_obj = None
        if self.store.data.get("offReason") in ("user", "idle"):  # 꺼 둔 채로 앱을 껐다 켬 → 'PC 찾기' 기록의 까닭을 그대로
            self._off_reason = self.store.data["offReason"]
        if self.store.login_bad:
            self.log(LOGIN_BAD_BLOCK_MSG)
        self._apply_preset()
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

    @staticmethod
    def dev():
        return _dev()

    net_of = staticmethod(net_of)
    youtube_id = staticmethod(youtube_id)    # 밖에서 작업 경계(rstudio.yt_guard)가 휴대폰과 같은 규칙으로 (rstudio 는 remote 를 import 하지 않음)
    listing_url = staticmethod(listing_url)

    def handler_class(self):
        """원격 리스너의 처리기: 앱이 화면 처리기(StudioHandler)를 넘겨줬으면 RemoteHandler 와 묶음 (RemoteHandler 가 앞 →
        do_GET·_route·timeout 은 원격 쪽 · 스튜디오 요청은 rstudio.gate 가 studio_call 로 앱 코드를 부름) · 없으면 RemoteHandler 만."""
        st = getattr(self.bridge, "studio", None) if self.bridge else None
        if st is None:
            return RemoteHandler
        if self._hcls is None or self._hcls.__bases__[1] is not st:
            self._hcls = type("RemoteStudioHandler", (RemoteHandler, st), {})
        return self._hcls

    # ----- 밖에서 작업 (D-230~D-234) -----
    def studio_allowed(self):
        """PC 의 '밖에서 작업 허용' (기본 켜짐) · 앱이 화면 처리기를 넘겨준 때만."""
        return bool(self.store and self.studio and getattr(self.bridge, "studio", None)
                    and self.store.data["settings"].get("studio", True))

    def auth_failed(self, ip):
        if self.limiter.failed(ip) and self.clock() - self._lockout_logged > 600:
            self._lockout_logged = self.clock()
            self.log("실행기 · 잘못된 연결 시도가 많아 잠시 막았어요")

    def studio_started(self, rec, replaced=False):
        if not replaced:
            self.log(f"밖에서 작업 시작 · {clean(rec['name'], 40)}")
        if rec.get("quietBefore"):  # 그 기기가 30분 넘게 세션이 없었음 → 내가 한 게 아니면 알 수 있게 (정해진 문장)
            self._note("studio", note_text("studio", rec.get("name")))

    def studio_ended(self, recs):
        for r in recs or []:
            self.log(f"밖에서 작업 끝냄 · {clean(r['name'], 40)} ({r.get('endedWhy') or '끝'})")

    def studio_revoke(self, ids=None, everyone=False):
        """PC 의 [끊기]·[모두 끊기]: 세션만 끝냄 (기기 열쇠는 그대로 — 기기째 끊기는 revoke) → 끝낸 수."""
        if not self.studio:
            return 0
        want = set(ids or [])
        gone = self.studio.sessions.end(lambda r: everyone or r["id"] in want, "revoked", "PC에서 끊음")
        self.studio_ended(gone)
        return len(gone)

    def _studio_view(self):
        if not self.studio:
            return {"available": False, "allowed": False, "sessions": []}
        now = self.clock()
        return {"available": bool(getattr(self.bridge, "studio", None)), "allowed": self.studio_allowed(),
                "sessions": [{"id": r["id"], "device": r["name"], "deviceId": r["device"], "startedAt": int(r["created"]),
                              "lastSeen": int(r["last"]), "active": now - r["last"] <= rstudio.ACTIVE_SECS,
                              "uploads": self.studio.uploads.count(r["device"]), "guest": bool(r.get("guest"))}
                             for r in sorted(self.studio.sessions.all(), key=lambda r: r["created"])]}

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
        """PC 화면 상태(/api/state)용 짧은 모습 · studio: 살아 있는 밖에서 작업 세션 모두 (끊기 전까지 · 30분 안 쓰면 끝) —
        active 는 2분 안에 요청이 있었던 것 ('지금 쓰는 중' · 아니면 lastSeen 으로 'N분 전') · 보관함·편집실·썸네일 PC 화면이 같이 씀 (D-238)."""
        now = self.clock()
        ss = sorted(self.studio.sessions.all(), key=lambda r: r["created"]) if self.studio else []
        return {"state": self.state, "devices": len(self.store.data["devices"]) if self.store else 0,
                "studio": [{"id": r["id"], "device": r["name"], "active": now - r["last"] <= rstudio.ACTIVE_SECS, "lastSeen": int(r["last"])}
                           for r in ss]}

    def pc_status(self):
        d = self.store.data
        p = self.pairing.live()
        pair = None
        if p and self.state == "on":
            pair = {"code": fmt_code(p["code"]), "link": self.pair_link(p["code"]), "expiresAt": int(p["exp"])}
        now = self.clock()
        return {"state": self.state, "error": self.error, "prep": self.prep, "dev": _dev(),
                "devices": [{"id": x["id"], "name": x["name"], "created": x["created"], "lastSeen": x["lastSeen"], "via": x.get("via", "code"),
                             "stale": now - (x.get("lastSeen") or x.get("created") or 0) > STALE_DAYS * 86400} for x in d["devices"]],
                "maxDevices": MAX_DEVICES, "pair": pair, "pairNote": PAIR_NOTES.get(self.pairing.note) if not pair else None,
                "settings": d["settings"], "pc": d["pc"],
                "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]}, "site": self.site(),
                "autoOffAt": self._auto_off_at(), "crypto": crypto_ok(), "studio": self._studio_view(),
                "retryAt": int(self._retry["at"]) if self.state == "error" and self._retry["at"] and d.get("enabled") else None,
                "login": self.login_view()}

    def pair_link(self, code):
        hint = ""
        if self.url:
            m = re.fullmatch(r"https://([a-z0-9-]{1,63})\.trycloudflare\.com", self.url)
            hint = m.group(1) if m else (urlparse(self.url).netloc if _dev() else "")
        return f"{self.site()}#pair={code}" + (f"&u={hint}" if hint else "")

    def turn_on(self, auto=False):
        """실행기 켜기 (PC 화면에서만 · auto 는 오류 뒤 저절로 다시)."""
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
            self.log("실행기를 켰어요 (ON)")
        except Exception as e:  # noqa: BLE001
            with self.lock:
                if not self._current(gen, cancel):  # 받는 중에 [그만두기]·[끄기]
                    return
                tuns, lst2 = self._take_resources()
                self.state, self.error, self.prep, self._err_kind = "error", scrub(e) or "실행기를 켜지 못했어요", None, "start"
                self._schedule_retry()
            self._stop_resources(tuns, lst2)
            if not isinstance(e, (RuntimeError, OSError)):
                _trace()
            self.log(f"실행기를 켜지 못했어요 · {scrub(e)}")
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
            if self.studio:  # 새 주소에는 옛 쿠키가 없음 → 세션도 모두 끝 (휴대폰 페이지가 새 주소로 다시 넘겨줌)
                self.studio_ended(self.studio.drop_all("none", "연결 주소가 바뀜"))
            self._drop_guests(None, "none", "연결 주소가 바뀜")  # 이번만 기기는 새 주소에서 다시 로그인 (D-256)
            self.pending.clear()
            self.pub.beacon(important=True)
            self._publish_pair()
            self._publish_lookup()
        if kind == "error":
            self._stop_resources([dead], lst, timeout=2)  # 멈춘 터널이 (혹시라도) 남긴 프로세스까지 · 이 스레드가 터널 스레드여도 됨
            self.log(f"실행기 · {error}")
            self._drop_guests(None, "none", "연결이 끊김")
            self.pending.clear()
            self.pub.beacon(important=True)
            self._publish_lookup("off", "error")

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
                try:  # 꺼진 까닭(user·idle)도 남김 → 앱을 다시 켠 뒤의 'PC 찾기' 기록도 같은 까닭 (D-252)
                    self.store.set(enabled=False, offReason=why if why in ("user", "idle") else "user")  # 같은 잠금 안 → 바로 뒤의 새 켜기가 쓴 True 를 덮지 않음
                except OSError as e:
                    err = e
        self._stop_resources(tuns, lst)
        self.tickets.drop()
        self.pairing.cancel()
        if self.studio:
            self.studio_ended(self.studio.drop_all("none", "실행기를 끔"))
        self._drop_guests(None, "none", "실행기를 끔")
        if self.pending is not None:
            self.pending.clear()
        if was != "off":
            self.pub.beacon(important=True)
            self.log("실행기를 껐어요 (OFF)" + (" · 오래 쓰지 않아서" if why == "idle" else ""))
            self._publish_lookup("off", why if why in ("user", "idle") else "user")
        if err:
            raise err
        return True

    def pair_start(self):
        if self.state != "on":
            raise PairError("먼저 실행기를 켜 주세요")
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
        """끊기: 열쇠·표·nonce 를 바로 버리고 비콘 주제를 바꿈 (rotate_notify 면 알림 주제도 → 옛 알림 주제에 한 번 안내를 남김).
        알림 주제는 끊는 기기 중 QR·코드 기기가 있을 때만 바꿈 — 아이디로 연결한 기기는 알림 주제를 받은 적이 없으므로, 그 기기만 끊으면
        (PC [끊기] · 그 기기의 '이 기기 연결 끊기') 소유자의 ntfy 구독을 끊지 않는다 (D-256).
        이번만 기기(메모리)도 같이 (주제는 원래 모름 · 바꾸지 않음)."""
        d = self.store.data
        guests = self._drop_guests(None if everyone else set(ids or []), "revoked", "기기 연결 끊음") if (everyone or ids) else 0
        gone = [x for x in d["devices"] if everyone or x["id"] in (ids or [])]
        if not gone:
            return guests
        rotate_notify = rotate_notify and any(x.get("via", "code") == "code" for x in gone)
        old = self.store.rotate_topics(notify=rotate_notify)
        self.store.remove({x["id"] for x in gone})
        self.tickets.drop({x["id"] for x in gone})
        self.auth.forget([x["id"] for x in gone])
        if self.studio:  # 그 기기의 밖에서 작업 세션·넘겨주기 표·올리던 조각도
            ids = {x["id"] for x in gone}
            self.studio.handoffs.drop(ids)
            self.studio_ended(self.studio.sessions.end(lambda r: r["device"] in ids, "revoked", "기기 연결 끊음"))
            try:
                self.studio.uploads.drop_devices(ids)
            except OSError:
                pass
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
        return len(gone) + guests

    def switch(self, on):
        """실행기 ON/OFF (D-310): 스위치 하나 = 지금 켜고 끄기 + 스튜디오를 다시 켤 때도 그대로 (늘 켜기 설정에 같이 적음).
        OFF 는 PC 화면·밖의 [실행기 끄기] 모두 같은 뜻 → 다시 ON 을 누를 때까지 꺼진 채. 자동으로 끄기(idle)는 늘 켜기를 건드리지 않음."""
        self.store.set(settings=dict(self.store.data["settings"], alwaysOn=bool(on)))
        if on:
            return self.turn_on()
        self.turn_off("user")
        return True

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
        if "studio" in b:
            if not isinstance(b["studio"], bool):
                raise ValueError("밖에서 작업 설정을 다시 골라 주세요")
            s["studio"] = b["studio"]
        if "alwaysOn" in b:
            if not isinstance(b["alwaysOn"], bool):
                raise ValueError("늘 켜기 설정을 다시 골라 주세요")
            s["alwaysOn"] = b["alwaysOn"]
        self.store.set(settings=s)
        if b.get("alwaysOn") is True and self.state in ("off", "error"):  # 늘 켜기를 켜면 지금도 켬 (D-295)
            self.turn_on()
        if "studio" in b and self.studio:
            if not b["studio"]:  # 끄면 열려 있던 밖에서 작업도 바로 끝 (실행기·작업 보기는 그대로)
                self.studio.handoffs.drop()
                self.studio_ended(self.studio.sessions.end(lambda r: True, "none", "밖에서 작업 허용을 끔"))
            self.log("밖에서 작업 허용을 " + ("켰어요" if b["studio"] else "껐어요"))
        return s

    def test_notify(self):
        self.pub.notify("test", NOTE_TEXT["test"], 3, "bell")

    def shutdown(self, timeout=1.5):
        """앱을 끌 때: 터널·리스너를 먼저 끄고(무엇이 켜져 있든) → 휴대폰에 '앱을 껐어요' 비콘을 한 번, timeout 안에서만
        (느린 DNS 에도 앱 끄기·업데이트 다시 시작이 기다리지 않게 따로 스레드). 다음에 켜면 다시 켜지도록 enabled 는 그대로."""
        if not self.store:
            return
        self._ctl_stop.set()
        try:
            with self.lock:
                was = self.state
                self._off_reason = "app"
                self._cancel.set()
                self._gen += 1
                tuns, lst = self._take_resources()
                self.state, self.url, self.prep = "off", None, None
            self._stop_resources(tuns, lst, timeout=1.0)
            if self.studio:
                self.studio.drop_all("none", "앱을 끔")
            self.store.take_guests()
            if self.pending is not None:
                self.pending.clear()
            if self.pub:
                posts = []
                body = self.beacon_body(state="off", reason=OFF_REASONS["app"]) if was != "off" else None
                if body:
                    posts.append((f"{self.ntfy()}/{self.store.data['topics']['beacon']}", body.encode("ascii")))
                # 아이디로 찾는 기록도 '앱을 껐어요' (같은 시간 안에서 · D-252) — 원격 접속이 켜져 있었을 때만 (이미 꺼 둔(user·idle) PC 는
                # 그 까닭이 마지막 기록으로 남게: 'off/app' 이면 페이지가 '스튜디오를 켜면 저절로 켜져요'라고 잘못 안내함)
                look = self._publish_lookup("off", "app", direct=True) if was != "off" else None
                if look:
                    posts.append((f"{self.ntfy()}/{look[0]}", look[1].encode("ascii")))
                ts = [threading.Thread(target=self.pub.post_now, daemon=True, name="remote-last-beacon",
                                       args=(url, data, {"Content-Type": "text/plain"}, timeout)) for url, data in posts]
                for t in ts:
                    t.start()
                end = time.monotonic() + timeout
                for t in ts:
                    t.join(max(0.0, end - time.monotonic()))
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

        def build():
            items = []
            for x in devs:
                one = inner
                if moved:  # 옮긴 주제: 아이디로 연결한 기기에는 비콘 주제만 (알림 주제 = [끊기] 표가 든 보안 알림 · D-255 · 기기마다 따로 잠금)
                    one = dict(inner, moved=moved if x.get("via", "code") == "code" else {"beacon": moved.get("beacon")})
                pt = json.dumps(one, ensure_ascii=False)
                items.append({"d": x["id"], "c": gcm_seal(_unb64u(x["beacon"]), pt, f"fsb1|{d['pc']['id']}|{x['id']}|{seq}")})
            return "fsb1." + _b64u(json.dumps({"v": 1, "pc": d["pc"]["id"], "seq": seq, "items": items}).encode("utf-8"))
        body = build()
        if len(body) > 3900 and job:  # ntfy 는 4KB 가 넘으면 첨부 파일로 바꿈 → 작업 칸을 빼고 다시
            inner["job"] = None
            body = build()
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
        elif name == autorun.JOB and isinstance(result, dict):  # 멈춤 → '멈췄어요' · 못 한 단계·확인할 것 → '확인이 필요해요' (D-207)
            n_chk = sum(1 for c in result.get("check") or [] if isinstance(c, dict) and c.get("lv") in ("bad", "warn"))
            if result.get("status") == "stopped":
                ok, err = False, AUTO_MSG["stopped"]
            elif result.get("status") != "done" or result.get("redo") or n_chk:
                ok, warn, note = False, True, "auto"
                err = AUTO_MSG["check"].format(n=max(n_chk, 1))
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
        return int(self.last_auth + h * 3600 * rlogin.time_scale()) if h and self.state == "on" else None

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
            if h and not cur and self.clock() - self.last_auth >= h * 3600 * rlogin.time_scale():  # 시간 줄이기는 개발 때만 (e2e)
                self.turn_off("idle")
                self._note("attention", f"실행기를 껐어요 ({h}시간 동안 안 써서)")
        elif (self.state == "error" and self._err_kind in ("tunnel", "start") and self.store.data.get("enabled")
              and self._retry["at"] is not None and self.clock() >= self._retry["at"]):
            self._retry["at"] = None
            self.log("실행기를 다시 켜 볼게요")
            self.turn_on(auto=True)
        if self.studio:  # 밖에서 작업: 오래 안 쓴(30분)·12시간 지난 세션 끝 · 7일 넘은 올리던 조각은 한 시간에 한 번 정리
            self.studio_ended(self.studio.sessions.sweep())
            if self.clock() - self._up_swept >= 3600:
                self._up_swept = self.clock()
                try:
                    self.studio.uploads.sweep()
                except OSError:
                    pass
        old = self.store.expired()
        if old:  # 오래 안 쓴 기기: 비콘 주제만 바꿈 (알림 주제까지 바꾸면 쓰고 있는 휴대폰의 ntfy 알림이 조용히 끊김)
            self.revoke(old, why="90일 동안 안 씀", rotate_notify=False)
        self._login_tick()

    def _awake_wanted(self):
        """PC 잠들지 않게: 원격이 켜져 있을 때(on·restarting) — 그리고 켜 두기로 한(enabled) 원격이 터널 오류 뒤 저절로 다시 켜 보는 사이
        (error 에 다음 시도가 잡혀 있음 · 그 시도 중 preparing·starting)에도. 집 인터넷이 30분 넘게 끊긴 사이 PC 가 잠들면 다음 시도를 못 해
        밖에서 다시 못 들어옴 (D-238). 그중 작업 중 · '켜 둔 동안 항상' · 밖에서 작업 세션이 살아 있을 때만."""
        st = self.state
        if st not in ("on", "restarting"):
            retrying = st in ("preparing", "starting") or (st == "error" and self._err_kind in ("tunnel", "start")
                                                            and self._retry.get("at") is not None)
            if not (retrying and self.store.data.get("enabled")):
                return False
        return (bool((self.bridge.job() or {}).get("name")) or self.store.data["settings"].get("keepAwake") == "always"
                or bool(self.studio and self.studio.sessions.all()))  # 밖에서 작업 세션 (2분 넘게 조용해도 30분 동안은 살아 있음)

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
            raise RuntimeError("실행기용 포트가 앱 화면 포트와 겹쳐요 · FUTSAL_REMOTE_PORT 를 바꿔 주세요")
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
            raise RuntimeError("실행기용 포트를 열지 못했어요 · 잠시 뒤 다시 켜 주세요")
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
    def in_flight(self):
        """원격 리스너가 지금 처리 중인 요청 수 — 휴대폰이 받는 완성본(/r/m · /s/dl)·영상 보기·올리기 조각은 끝날 때까지 셈
        (멈춘 연결은 RemoteHandler.timeout 뒤 끝남). 앱 다시 시작(자동 업데이트)이 이것을 끊지 않게 (app._auto_why · D-311)."""
        return self._busy

    def touch(self, dev):
        self.last_auth = self.clock()
        self.store.touch(dev)

    def r_ping(self):
        """열린 길: PC 이름은 넣지 않음 (아이디만 아는 사람에게 PC 이름이 보이지 않게 · 예전 페이지는 pc.id 만 읽음 · D-252)."""
        out = {"api": API, "pc": {"id": self.store.data["pc"]["id"]}, "time": int(self.clock())}
        feats = []
        if getattr(self.bridge, "studio", None):  # 이 PC 는 밖에서 작업(PC 스튜디오 열기)을 함 · API 판은 그대로 1 (예전 페이지·예전 PC 와 서로 됨)
            feats.append("studio")
        lg = self.store.login()
        if lg and lg.get("enabled"):  # 아이디·비밀번호로 연결을 받음
            feats.append("login")
        if feats:
            out["features"] = feats
        return out

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
        self._note("paired", note_text("paired", dev.get("name")))
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
        out = {"api": API, "ver": self.bridge.version, "time": int(self.clock()), "pc": d["pc"], "job": job, "last": self.last,
               "log": [scrub(x) for x in new], "logTotal": total,
               "remote": {"autoOffAt": self._auto_off_at(), "devices": len(d["devices"]), "device": {"id": dev["id"], "name": dev["name"]},
                          "studio": {"allowed": self.studio_allowed()}},
               "features": ["studio"] if getattr(self.bridge, "studio", None) else []}  # 휴대폰 페이지가 [PC 스튜디오 열기]를 보일지
        if dev.get("guest"):  # 이번만 기기: 주제를 주지 않음 (비콘·알림 없음 · D-256)
            out["remote"]["guestExp"] = int(dev["exp"])
            return out
        code = dev.get("via", "code") == "code"
        # 주제 이름은 곧 비밀(알림 주제 = 보내기 열쇠) → 기기 비콘 열쇠로 잠가서 (Cloudflare 가 못 봄) · 아이디로 연결한 기기는 알림 주제를 받지 않음
        # (알림에 [끊기] 표가 들어 있음 · D-255) · 아이디·비밀번호 연결 모습도 같은 봉투 안에
        topics = json.dumps({"beacon": {"server": self.ntfy(), "topic": d["topics"]["beacon"]},
                             "notify": {"server": self.ntfy(), "topic": d["topics"]["notify"]} if code else None,
                             "login": self._login_brief(code)}, ensure_ascii=False)
        out["topics"] = gcm_seal(_unb64u(dev["beacon"]), topics, topics_aad(d["pc"]["id"], dev["id"]))
        return out

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
        if dev.get("via", "code") != "code":  # 알림은 QR·코드로 연결한 휴대폰에서만 (아이디로 연결한 기기는 알림 주제를 모름 · D-255)
            raise ActionError(LOGIN_MSG["not_allowed"], 403, "not_allowed")
        now = self.clock()
        if now - self._last_test < NOTIFY_TEST_GAP:
            raise ActionError("알림 시험은 1분에 한 번만 보낼 수 있어요 · 잠시 뒤 다시 눌러 주세요", 429)
        self._last_test = now
        self.test_notify()
        self.log(f"원격 · {clean(dev['name'], 40)} · 알림 시험")
        return {"ok": True}

    def r_handoff(self, dev, b, ip=""):
        """PC 스튜디오 열기 (D-230): 서명된 요청 → 한 번 쓰는 표(60초 · 이 기기·이 IP 대역·갈 곳에 묶음 · 기기마다 하나) + 지금 주소.
        휴대폰 페이지는 <주소>/s/hello#t=<표> 로 넘어감 (표는 주소의 # 뒤라 Cloudflare 기록·Referer 에 안 남음)."""
        if not self.studio_allowed():
            raise ActionError(rstudio.MSG["studio_off"], 403, "studio_off")
        path = b.get("path", "/")
        if not isinstance(path, str) or not rstudio.NEXT_RE.fullmatch(path):
            raise ActionError("잘못된 요청이에요")
        if not self.url:
            raise ActionError("PC 연결 주소를 준비하는 중이에요 · 잠시 뒤 다시 해 주세요", 503)
        try:
            tok = self.studio.handoffs.issue(dev["id"], net_of(ip), path)
        except rstudio.TooMany as e:
            raise ActionError(str(e), 429) from None
        return {"ok": True, "url": self.url, "token": tok}

    # ----- 아이디·비밀번호로 연결 (D-250~D-258) -----
    def _guard(self):
        """지금 로그인 블록의 Guard (블록이 바뀌면 새로 · 잠금은 login_lock 하나)."""
        lg = self.store.login()
        if lg is None:
            return None
        if self._guard_obj is None or self._guard_obj.login is not lg:
            self._guard_obj = rlogin.Guard(lg, self.store.save, self.clock, lock=self.login_lock)
        return self._guard_obj

    def _security(self, kind, text, click=None, actions=None):
        """보안 알림: '알림' 켜기·끄기와 상관없이 늘 (D-255)."""
        if self.pub:
            self.pub.notify(kind, text, 4, "warning", click=click, actions=actions)

    def _code_devices(self):
        return [x for x in self.store.data["devices"] if x.get("via", "code") == "code"]

    def _login_devices(self):
        return [x for x in self.store.data["devices"] if x.get("via") == "login"]

    def _drop_guests(self, ids=None, code="revoked", why="끊음"):
        """이번만 기기 버리기 (ids 가 None 이면 모두): 열쇠·표·nonce·세션·넘겨주기·올리던 조각까지 → 버린 수."""
        if not self.store:
            return 0
        return self._end_guests(self.store.take_guests(ids), code, why)

    def _end_guests(self, gone, code, why):
        if not gone:
            return 0
        gids = {g["id"] for g in gone}
        self.tickets.drop(gids)
        self.auth.forget(gids)
        if self.studio:
            self.studio.handoffs.drop(gids)
            self.studio_ended(self.studio.sessions.end(lambda r: r["device"] in gids, code, why))
            try:
                self.studio.uploads.drop_devices(gids)
            except OSError:
                pass
        return len(gone)

    def _lookup_state(self):
        """'PC 찾기' 기록에 넣을 지금 상태 → (state, reason) · 켜는 중·다시 잡는 중이면 None (다음에)."""
        if self.state == "on" and self.url:
            return "on", None
        if self.state in ("preparing", "starting", "restarting"):
            return None
        if self.state == "error":
            return "off", "error"
        return "off", self._off_reason if self._off_reason in ("app", "user", "idle", "error") else "user"

    def _publish_lookup(self, state=None, reason=None, lg=None, direct=False):
        """아이디로 PC 를 찾는 기록 (D-252): {v, pc, seq, ts, state, reason, url} 을 아이디 열쇠로 잠가 아이디 주제에 — PC 이름·작업은 없음.
        로그인을 켜 둔 동안만 (꺼짐 알림 login_off 는 예외) · direct: 앱을 끌 때 (보낼 (주제, 글)만 돌려줌)."""
        lg = lg or (self.store.login() if self.store else None)
        if not lg or not crypto_ok() or not self.pub:
            return None
        if not lg.get("enabled") and reason != "login_off":
            return None
        with self.store.lock:   # 상태·순번·시각을 한 번에 (켜짐 기록이 꺼짐 기록보다 늦은 순번을 받지 않게 · 페이지는 (ts, seq) 순)
            if state is None:
                st = self._lookup_state()
                if st is None:
                    return None
                state, reason = st
            url = self.url if state == "on" else None
            lg["seq"] = int(lg.get("seq") or 0) + 1
            seq, ts = lg["seq"], int(self.clock())
            try:
                self.store.save()
            except OSError:
                pass
        inner = {"v": 1, "pc": self.store.data["pc"]["id"], "seq": seq, "ts": ts, "state": state, "reason": reason, "url": url}
        if rlogin.is_id_only(lg):   # 아이디만 (D-290): 페이지가 비밀번호를 묻지 않게 · 페이지는 소유자 아이디에서만 이 표시를 믿음
            inner["idOnly"] = True
        body = rlogin.seal_lookup(_unb64u(lg["bkey"]), lg["topic"], inner, gcm_seal)
        self._lookup_at = self.clock()
        if direct:
            return lg["topic"], body
        self.pub.lookup(lg["topic"], body)
        return lg["topic"], body

    def _collision(self, cid, timeout=5):
        """같은 아이디를 쓰는 다른 PC 가 12시간 안에 기록을 올렸는지 → 'taken' · 'clear' · 'unknown'(ntfy 에 닿지 않음)."""
        d = rlogin.derive_login(cid)
        try:
            req = urllib.request.Request(f"{self.ntfy()}/{d['topic']}/json?poll=1&since=all", headers=updater.UA)
            with updater.urlopen(req, timeout) as r:
                raw = r.read(1 << 20)
        except Exception:  # noqa: BLE001 — 인터넷·ntfy 문제 (주소는 기록하지 않음)
            return "unknown"
        mine, now = self.store.data["pc"]["id"], self.clock()
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict) or m.get("event") != "message":
                continue
            rec = rlogin.open_lookup(d["bkey"], d["topic"], m.get("message"), gcm_open)
            if rec and rec["pc"] != mine and rec["ts"] >= now - COLLISION_AGE:
                return "taken"
        return "clear"

    def _check_free(self, cid, force):
        """정하기·켜기 전에: 다른 PC 가 쓰는 아이디면 ValueError · 확인을 못 했으면(인터넷) force 가 있어야 함."""
        got = self._collision(cid)
        if got == "taken":
            raise ValueError(LOGIN_TAKEN_MSG)
        if got == "unknown" and not force:
            raise LoginAsk("unchecked", LOGIN_UNCHECKED_MSG)

    def _recheck_collision(self):
        """앱을 켤 때·3시간마다: 겹치면 PC 화면에 알림 띠 (login.collision) — 따로 스레드 (ntfy 를 기다리지 않게)."""
        lg = self.store.login()
        if not lg or not lg.get("enabled"):
            return
        cid = lg["id"]

        def run():
            got = self._collision(cid)
            with self.login_lock:
                cur = self.store.login()
                if got == "unknown" or cur is not lg or cur["id"] != cid:
                    return
                want = int(self.clock()) if got == "taken" else None
                if bool(cur.get("collision")) != bool(want):
                    cur["collision"] = want
                    try:
                        self.store.save()
                    except OSError:
                        pass
                    if want:
                        self.log("다른 PC가 같은 아이디를 쓰고 있어요 · PC '실행기'에서 아이디를 바꿔 주세요")
        threading.Thread(target=run, daemon=True, name="remote-login-collision").start()

    def _new_block(self, cid, id_show, ver, old=None, mode="password"):
        d = rlogin.derive_login(cid)
        return {"v": 1, "canon": rlogin.CANON, "enabled": True, "mode": mode, "id": cid, "idShow": id_show, "topic": d["topic"],
                "bkey": _b64u(d["bkey"]), "idc": d["idc"], "grp": ver["grp"], "salt": ver["salt"], "iters": ver["iters"],
                "verifier": ver["verifier"], "fake": _b64u(secrets.token_bytes(32)), "control": "fsc" + secrets.token_hex(12),
                "gen": int(old["gen"]) + 1 if old else 1, "setAt": int(self.clock()), "seq": int(old.get("seq") or 0) if old else 0,
                "guard": rlogin.fresh_guard(), "suspended": None, "events": [], "collision": None}

    @staticmethod
    def _id_show(s):
        return clean(unicodedata.normalize("NFKC", str(s or "")).strip(), 32)

    def _preset_marker(self):
        """미리 정한 로그인을 이 PC 에서 한 번 다룬 표 (remote.json 밖 · 그 파일이 깨져 새로 만들어져도 다시 넣지 않게 · D-258)."""
        return self.store.path.with_name(PRESET_MARK)

    def _mark_preset(self, result):
        self._preset_marker().parent.mkdir(parents=True, exist_ok=True)
        updater.write_atomic(self._preset_marker(), json.dumps({"v": 1, "at": int(self.clock()), "id": rlogin.PRESET_ID, "result": result}),
                             mode=0o600)

    def _preset_owner(self):
        """소유자 PC 표시 (D-258 보강) → 'paired' · 'youtube' · None. config 의 채널 주소는 모든 설치의 기본값(core.CONFIG_DEFAULT ·
        config.json)이라 그것만으로는 소유자 PC 를 못 가림 → 이 PC 에만 있는 것 하나가 더 있어야 함:
          (1) 이 판 전에 QR·코드로 연결해 둔 기기 (via 칸이 없는 옛 기기 · 이 판부터 새 기기는 늘 via 를 씀 → 새로 깐 PC 는 절대 안 맞음)
          (2) 유튜브 올리기가 바로 그 채널로 연결돼 있음 (Google 이 확인한 채널 · youtube/token.bin)."""
        if any("via" not in x for x in self.store.data["devices"]):
            return "paired"
        tok = yt_api.load_secret(self.store.path.parent / "youtube" / "token.bin") or {}
        ch = tok.get("channel") if isinstance(tok.get("channel"), dict) else {}
        return "youtube" if ch.get("id") == rlogin.PRESET_CHANNEL else None

    def _apply_preset(self):
        """미리 정해 둔 로그인 (D-258): rlogin.PRESET(검증값만)이 있고 · 이 PC 가 로그인을 정한 적이 없고 · 한 번도 다룬 적이 없고 ·
        설정의 채널이 최경진 풋살사관학교이고 · 소유자 PC 표시(_preset_owner)가 있을 때만 → 따로 스레드에서 같은 아이디를 쓰는 다른 PC 를
        살핀 뒤(ntfy · 5초) 한 번 넣음. 넣었거나 다른 PC 가 쓰고 있으면 표(_preset_marker)와 presetApplied 를 남겨 다시 넣지 않음 ·
        ntfy 에 닿지 않으면 다음에 켤 때 다시. 원격 접속은 켜지 않는다. → 살피기 시작했으면 그 스레드 (시험이 기다림)."""
        p = rlogin.PRESET
        d = self.store.data
        marker = self._preset_marker()
        if d.get("presetApplied") and not marker.exists():  # (예전 판이 남긴 기록) 파일 밖 표로도 남김
            try:
                self._mark_preset("applied")
            except OSError:
                pass
        if not p or not rlogin.preset_ok(p) or d.get("login") is not None or self.store.login_bad or d.get("presetApplied"):
            return None
        if marker.exists() or rlogin.PRESET_CHANNEL not in str((core.CONFIG or {}).get("channel_url") or ""):
            return None
        owner = self._preset_owner()
        if not owner:
            return None
        t = threading.Thread(target=self._preset_run, args=(p, owner), daemon=True, name="remote-login-preset")
        self._preset_thread = t
        t.start()
        return t

    def _preset_run(self, p, owner):
        cid = rlogin.canon_id(p["id"])
        mode = "idOnly" if rlogin.is_id_only(p) else "password"
        if mode == "idOnly" and not rlogin.preset_matches_id(p):   # 아이디만 PRESET 은 검증값이 정말 아이디에서 나온 것만 (D-291)
            return False
        got = self._collision(cid)
        if got == "unknown":  # ntfy 에 닿지 않음 → 다음에 켤 때 다시
            return False
        block = self._new_block(cid, self._id_show(p["idShow"]),
                                {"grp": p["grp"], "salt": p["salt"], "iters": p["iters"], "verifier": p["verifier"]}, mode=mode)
        block["preset"] = True
        if not rlogin.valid_login(block):
            return False
        d = self.store.data
        with self.login_lock:
            if d.get("login") is not None or self.store.login_bad or d.get("presetApplied") or self._preset_marker().exists():
                return False   # 그사이 소유자가 직접 정함
            try:
                self._mark_preset("applied" if got == "clear" else "taken")   # 파일 밖 표부터 (깨져 새로 만든 remote.json 에도 다시 넣지 않게)
            except OSError:
                return False
            d["presetApplied"] = int(self.clock())
            if got == "clear":
                d["login"] = block
            try:
                self.store.save()
            except OSError:
                d["login"] = None
                return False
        if got == "taken":
            self.log(f"미리 정해 둔 아이디({rlogin.PRESET_ID})는 다른 PC가 쓰고 있어서 이 PC에는 넣지 않았어요")
            return False
        if mode == "idOnly":
            self.log(f"미리 정해 둔 아이디({rlogin.PRESET_ID})만으로 연결을 준비했어요 · 밖에서 이 아이디만 넣으면 열려요")
        else:
            self.log("미리 정해 둔 아이디·비밀번호로 연결을 준비했어요 · PC '실행기'에서 비밀번호를 바꾸는 걸 권해요")
        return True

    def _login_brief(self, manage):
        """연결된 기기에 주는 모습 (기기 비콘 열쇠로 잠긴 봉투 안 · D-255): 켜짐 · 아이디 · 영문 자판 꼴 · 막힘 · 잠김 · 관리 권한."""
        lg = self.store.login()
        if not lg:
            return {"on": False, "set": False, "manage": manage}
        g = self._guard().view()
        return {"on": bool(lg.get("enabled")), "set": True, "show": lg["idShow"], "keys": lg["id"], "lock": g["lock"],
                "suspended": g["suspended"], "manage": manage}

    def login_view(self):
        """PC 화면(/api/remote)용 — 검증값·salt·주제·열쇠·가짜 비밀·[끊기] 주제는 넣지 않는다."""
        if not self.store:
            return {"set": False}
        lg = self.store.login()
        now = self.clock()
        base = {"set": bool(lg), "bad": self.store.login_bad, "idHint": rlogin.PRESET_ID,
                "autoOffHours": self.store.data["settings"].get("autoOffHours") or 0, "codeDevices": len(self._code_devices()),
                "loginDevices": len(self._login_devices()) + len(self.store.guests)}
        if not lg:
            return base
        g = self._guard().view()
        evs = []
        for e in lg.get("events") or []:
            dev = self.store.device(e.get("dev"))
            evs.append({"ref": e["ref"], "at": e["at"], "kind": e["kind"], "vocab": e.get("vocab") or "기타 · 기타", "cc": e.get("cc"),
                        "alive": bool(dev), "active": bool(dev and now - (dev.get("lastSeen") or 0) <= rstudio.ACTIVE_SECS),
                        "kicked": bool(e.get("kicked"))})
        base.update({"enabled": bool(lg.get("enabled")), "idShow": lg["idShow"], "idKeys": lg["id"], "setAt": lg["setAt"], "gen": lg["gen"],
                     "lock": g["lock"], "netLocks": g["netLocks"], "suspended": g["suspended"], "failsToday": g["failsToday"],
                     "total": g["total"], "collision": lg.get("collision"), "events": evs, "preset": bool(lg.get("preset")),
                     "idOnly": rlogin.is_id_only(lg)})
        return base

    def login_check(self, id_=None, password=""):
        """[저장] 전 살아 있는 점검표 (0.3초마다) → {problems: [코드], canonLen} · 비밀번호는 저장·기록하지 않음."""
        lg = self.store.login()
        cid = rlogin.canon_id(id_) if id_ else (lg["id"] if lg else None)
        return {"problems": rlogin.pw_problems(password or "", cid), "canonLen": len(rlogin.canon_pw(password or "")),
                "idProblem": rlogin.id_problem(id_) if id_ else None}

    def login_suggest(self, id_=None):
        lg = self.store.login()
        return {"password": rlogin.suggest(rlogin.canon_id(id_) if id_ else (lg["id"] if lg else None))}

    def login_set(self, id_=None, password=None, password2=None, revoke=True, force=False, ack=False, id_only=False):
        """아이디·비밀번호 정하기 / 비밀번호 바꾸기 (PC 화면에서만 · 옛 비밀번호는 묻지 않음 · D-256):
        새 salt·검증값·가짜 비밀·[끊기] 주제 · 판(gen)+1 · 막힘·잠김·기록·주고받는 중 지움 · 이번만 기기 모두 끊음 ·
        revoke(기본) 면 아이디로 연결한 기기도 끊음 (QR·코드 기기는 그대로).
        id_only: 비밀번호 없이 아이디만으로 (D-290 · 소유자 아이디에서만) — 검증값은 아이디에서 나온 비밀로 · 비밀번호 칸은 보지 않음."""
        lg = self.store.login()
        if id_ is None and not lg:
            raise ValueError("아이디를 넣어 주세요")
        if id_ is not None:
            p = rlogin.id_problem(id_)
            if p:
                raise ValueError(rlogin.ID_MSG[p])
            cid = rlogin.canon_id(id_)
        else:
            cid = lg["id"]
        if id_only:
            if not rlogin.id_only_ok(cid):
                raise ValueError(f"비밀번호 없이 연결은 소유자 아이디({rlogin.PRESET_ID})에서만 쓸 수 있어요")
            password = password2 = None
        else:
            probs = rlogin.pw_problems(password or "", cid, password2)
            if probs:
                raise ValueError(rlogin.PW_MSG[probs[0]])
        if not crypto_ok():
            raise ValueError(MISSING_MSG)
        if not self._code_devices() and not ack:
            raise LoginAsk("need_ack", LOGIN_ACK_MSG)
        if not lg or cid != lg["id"]:
            self._check_free(cid, force)
        ver = rlogin.make_id_verifier(cid) if id_only else rlogin.make_verifier(password)
        password = password2 = None  # noqa: F841 — 여기서 버림 (검증값만 남김)
        with self.login_lock:
            old = self.store.login()
            show = self._id_show(id_) if id_ is not None else old["idShow"]
            self.store.data["login"] = self._new_block(cid, show, ver, old, mode="idOnly" if id_only else "password")
            self.store.login_bad = False
            self.store.save()
            self.pending.clear()
        self._drop_guests(None, "revoked", "비밀번호를 바꿈")
        if old and revoke:
            ids = [x["id"] for x in self._login_devices()]
            if ids:
                self.revoke(ids, why="비밀번호를 바꿈", rotate_notify=False)
        if old and old["id"] != cid:
            self._publish_lookup("off", "login_off", lg=old)
        self._publish_lookup()
        self._control_ensure()
        if id_only:
            self.log("비밀번호 없이 아이디만으로 연결하게 바꿨어요" if old else "아이디만으로 연결을 켰어요")
        else:
            self.log("아이디·비밀번호로 연결의 비밀번호를 바꿨어요" if old else "아이디·비밀번호로 연결을 켰어요")
        return self.login_view()

    def login_id(self, id_, force=False):
        """아이디만 바꾸기: 새 주제·열쇠 · 옛 주제에 '안 씀' 기록 · 기억한 기기·이번만 기기는 그대로."""
        lg = self.store.login()
        if not lg:
            raise ValueError("먼저 아이디·비밀번호를 정해 주세요")
        p = rlogin.id_problem(id_)
        if p:
            raise ValueError(rlogin.ID_MSG[p])
        cid = rlogin.canon_id(id_)
        if rlogin.is_id_only(lg) and not rlogin.id_only_ok(cid):   # 아이디만은 소유자 아이디에서만 (D-290)
            raise ValueError(f"비밀번호 없이 연결은 {rlogin.PRESET_ID} 에서만 돼요 · 다른 아이디를 쓰려면 먼저 비밀번호를 정해 주세요")
        if cid != lg["id"]:
            self._check_free(cid, force)
        with self.login_lock:
            old = dict(self.store.login())
            d = rlogin.derive_login(cid)
            cur = self.store.login()
            cur.update({"id": cid, "idShow": self._id_show(id_), "topic": d["topic"], "bkey": _b64u(d["bkey"]), "idc": d["idc"],
                        "collision": None})
            self.store.save()
            self.pending.clear()
        if old["id"] != cid:
            self._publish_lookup("off", "login_off", lg=old)
        self._publish_lookup()
        self.log("아이디·비밀번호로 연결의 아이디를 바꿨어요")
        return self.login_view()

    def login_on(self, force=False):
        lg = self.store.login()
        if not lg:
            raise ValueError("먼저 아이디·비밀번호를 정해 주세요")
        self._check_free(lg["id"], force)
        with self.login_lock:
            lg["enabled"] = True
            self.store.save()
        self._publish_lookup()
        self.log("아이디·비밀번호로 연결을 다시 켰어요")
        return self.login_view()

    def login_off(self, revoke=False):
        """끄기: 같은 아이디·비밀번호로 [다시 켜기] 할 수 있음 · 이번만 기기는 끊음 · revoke 면 아이디로 연결한 기기도 (기본 아님)."""
        lg = self.store.login()
        if not lg:
            raise ValueError("아이디·비밀번호로 연결이 꺼져 있어요")
        with self.login_lock:
            lg["enabled"] = False
            self.store.save()
            self.pending.clear()
        self._publish_lookup("off", "login_off", lg=lg)
        self._drop_guests(None, "revoked", "아이디 연결을 끔")
        if revoke:
            ids = [x["id"] for x in self._login_devices()]
            if ids:
                self.revoke(ids, why="아이디 연결을 끔", rotate_notify=False)
        self.log("아이디·비밀번호로 연결을 껐어요")
        return self.login_view()

    def login_unlock(self, where="PC에서"):
        """막힘·모두 틀린 수·잠김을 지움 (PC [지금 풀기]·[그대로 다시 켜기] · QR·코드 휴대폰 [다시 켜기])."""
        g = self._guard()
        if not g:
            raise ValueError("아이디·비밀번호로 연결이 꺼져 있어요")
        with self.login_lock:
            g.unlock()
        self.log(f"아이디·비밀번호 연결을 다시 열었어요 ({where})")
        return self.login_view()

    def login_kill(self, ref=None, kh=None, where="PC에서"):
        """[끊기] (알림·#kill 쪽·QR 휴대폰·PC · D-255): 그 기기를 끊고 아이디·비밀번호 연결을 잠금 (기기가 이미 없어도 잠금 —
        '내가 아님' = 비밀번호가 샜다는 뜻) · [끊기] 주제는 바꾸지 않음 (두 번째 알림의 [끊기]도 되게)."""
        with self.login_lock:
            lg = self.store.login()
            if not lg:
                raise ValueError("아이디·비밀번호로 연결이 꺼져 있어요")
            now = self.clock()
            if kh is not None:
                ev = next((e for e in lg.get("events") or [] if hmac.compare_digest(str(e.get("kh")), kh)
                           and not e.get("kicked") and e.get("until", 0) > now), None)
                if not ev:
                    return False
            else:
                ev = next((e for e in lg.get("events") or [] if e.get("ref") == ref), None)
                if not ev:
                    raise ValueError("그 연결 기록을 찾지 못했어요 · 목록을 새로 고쳐 주세요")
            ev["kicked"] = True
            lg["suspended"] = {"why": "kick", "at": int(now)}
            self.store.save()
            self.pending.clear()
            dev_id = ev.get("dev")
        if dev_id in self.store.guests:
            self._drop_guests({dev_id}, "revoked", "아이디 연결 [끊기]")
        elif self.store.device(dev_id):
            self.revoke([dev_id], why="아이디 연결 [끊기]", rotate_notify=False)
        self.log(f"아이디로 연결한 기기를 끊었어요 ({where})")
        self._security("login_kill", NOTE_TEXT["login_kill"])
        return True

    # 터널 쪽 (열린 길 둘)
    def r_login_start(self, b, ip, ua="", cc=""):
        """POST /r/login/start {v, grp, idc, A} → {v, login, grp, salt, iters, B, pc:{id}, time} — 아무것도 세지 않음."""
        if not crypto_ok() or not self.url:
            raise LoginError(503, "not_ready")
        lg = self.store.login()
        if not lg or not lg.get("enabled"):
            raise LoginError(403, "login_off")
        net = net_of(ip)
        with self.login_lock:
            v = self._guard().check(net)
        if v:
            raise LoginError(423, v["code"], {k: v[k] for k in ("why", "scope", "until") if k in v})
        idc, A = b.get("idc"), b.get("A")
        if (b.get("v") != 1 or b.get("grp") != rlogin.GROUP or not isinstance(idc, str) or not re.fullmatch(r"[A-Za-z0-9_-]{22}", idc)
                or not isinstance(A, str) or not re.fullmatch(r"[A-Za-z0-9_-]{512}", A)):
            raise LoginError(400, "bad_request")
        try:
            raw = _unb64u(A)
        except ValueError:
            raise LoginError(400, "bad_request") from None
        N = rlogin.group()[0]
        a_int = int.from_bytes(raw, "big")
        if len(raw) != rlogin.nlen(N) or not rlogin.a_ok(a_int):
            raise LoginError(400, "bad_request")
        real = hmac.compare_digest(idc, lg["idc"])
        if real:
            salt, ver = _unb64u(lg["salt"]), int.from_bytes(_unb64u(lg["verifier"]), "big")
        else:  # 틀린 아이디: 같은 모양의 가짜 대답 (아이디가 맞는지 알 수 없게 · 틀리면 그 대역만 셈)
            salt, ver = rlogin.fake_params(_unb64u(lg["fake"]), idc)
        iters = int(lg["iters"])
        bb, B, u = rlogin.server_start(ver, a_int)
        lid = rlogin._b64u(secrets.token_bytes(16))
        T = rlogin.transcript(self.host(), self.store.data["pc"]["id"], idc, lid, rlogin.GROUP, iters, salt, a_int, B)
        try:
            self.pending.put(net, {"A": a_int, "b": bb, "u": u, "v": ver, "T": T, "real": real, "gen": lg["gen"], "ip": ip,
                                   "ua": str(ua or "")[:512], "cc": str(cc or "")[:8]}, lid=lid)
        except rlogin.Busy:
            raise LoginError(429, "busy") from None
        return {"v": 1, "login": lid, "grp": rlogin.GROUP, "salt": _b64u(salt), "iters": iters, "B": _b64u(rlogin.pad(B, N)),
                "pc": {"id": self.store.data["pc"]["id"]}, "time": int(self.clock())}

    def r_login_finish(self, b, ip, ua="", cc=""):
        """POST /r/login/finish {v, login, M1, remember, name, install} → {v, M2, sealed, remembered, time}.
        계산(S·K·M1)은 잠금 밖 · 막힘 다시 보기·M1 비교·세기·저장은 login_lock 안 (동시에 와도 넘치지 않게 · D-254)."""
        e = self.pending.take(b.get("login"))
        if not e:
            raise LoginError(410, "again")
        m1b, remember, name, install = b.get("M1"), b.get("remember"), b.get("name"), b.get("install", "")
        if (b.get("v") != 1 or not isinstance(m1b, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", m1b) or not isinstance(remember, bool)
                or not isinstance(name, str) or len(name) > rlogin.NAME_MAX or not isinstance(install, str)
                or (install and not INSTALL_RE.fullmatch(install))):
            raise LoginError(400, "bad_request")
        net = net_of(ip)
        lg = self.store.login()
        if not lg or not lg.get("enabled") or lg["gen"] != e["gen"] or e["net"] != net:
            raise LoginError(410, "again")
        K = rlogin.session_key(rlogin.server_S(e["A"], e["b"], e["v"], e["u"]))
        want = rlogin.m1(K, e["T"], remember, name, install)
        got = _unb64u(m1b)
        with self.login_lock:
            g = self._guard()
            if g is None or self.store.login() is not lg:
                raise LoginError(410, "again")
            v = g.check(net)
            if v:  # 그사이 막힘 → 맞았는지 알려 주지 않고 세지도 않음
                raise LoginError(423, v["code"], {k: v[k] for k in ("why", "scope", "until") if k in v})
            ok = hmac.compare_digest(want, got) and e["real"]
            if ok:
                g.success(net)
            else:
                out = g.fail(net, real=e["real"])
        if not ok:
            stop = False
            for kind, n in out["notes"]:
                if kind == "login_stop":
                    stop = True
                    self.log("비밀번호가 너무 많이 틀려서 아이디·비밀번호 연결을 잠갔어요")
                    self._security("login_stop", NOTE_TEXT["login_stop"])
                else:
                    self._security("login_fail", NOTE_TEXT["login_fail"].replace("{n}", str(n)))
            if stop:  # 이 시도(50번째)로 잠김 → '남은 기회 0번 · 잠시 막혀요'가 아니라 잠긴 까닭을 바로 (PC·QR 휴대폰이 다시 켤 때까지)
                raise LoginError(423, "suspended", {"why": "fails"}, counted=True)
            raise LoginError(403, "bad", {"left": out["left"], "until": out["until"]})
        return self._login_success(lg, e, K, got, b.get("login"), remember, name, install)

    def _login_success(self, lg, e, K, M1, lid, remember, name, install):
        saved = self.store.data["devices"]
        same = bool(install) and any(x.get("install") == install for x in saved)
        guest = not remember or (len(saved) >= MAX_DEVICES and not same)
        if guest:
            dev, evicted = self.store.add_guest(name, rlogin.time_scale())
            if evicted:
                self._end_guests(evicted, "revoked", "이번만 기기가 많음")
                self.log("이번만 연결 기기가 많아 가장 오래된 기기를 끊었어요")
        else:
            dev, old = self.store.add_device(name, install or None, via="login", gen=lg["gen"])
            if old:  # 같은 브라우저가 다시 연결 → 옛 열쇠·표는 바로 버림 (짝짓기와 같음)
                self.tickets.drop({x["id"] for x in old})
                self.auth.forget([x["id"] for x in old])
        now = int(self.clock())
        tok = rlogin.kill_token()
        cc = e.get("cc") if rlogin.country(e.get("cc")) else None
        ev = {"ref": _b64u(secrets.token_bytes(8)), "at": now, "dev": dev["id"], "kind": "guest" if guest else "remember",
              "vocab": rlogin.ua_vocab(e.get("ua")), "cc": cc, "kh": rlogin.kill_hash(tok), "until": now + rlogin.KILL_TTL, "kicked": False}
        with self.login_lock:
            lg["events"] = ([ev] + list(lg.get("events") or []))[:rlogin.EVENTS_MAX]
            try:
                self.store.save()
            except OSError:
                pass
        act = [{"action": "http", "label": "끊기", "url": f"{self.ntfy()}/{lg['control']}", "method": "POST", "body": "fsk1." + tok, "clear": True}]
        self._security("login", login_note(guest, e.get("ua"), cc, now), click=f"{self.site()}#kill={lg['control']}.{tok}.{now}", actions=act)
        if self.studio:  # 방금 보안 알림을 보냈으니 밖에서 작업 '새 세션' 알림은 또 울리지 않게
            self.studio.sessions.seen[dev["id"]] = now
        self.touch(dev)
        if not guest:
            self.pub.beacon(important=True)
        self.log(f"밖에서 아이디로 연결했어요 · {ev['vocab']} ({'이번만' if guest else '기억함'})")
        self._control_ensure()
        d = self.store.data
        inner = {"v": 2, "via": "login", "url": self.url, "pc": {"id": d["pc"]["id"], "name": d["pc"]["name"]},
                 "device": {"id": dev["id"], "name": dev["name"]}, "keys": {"auth": dev["auth"], "beacon": dev["beacon"]},
                 "login": {"show": lg["idShow"]}}
        if guest:
            inner.update(guest=True, exp=int(dev["exp"]))
        else:
            inner.update(beacon={"server": self.ntfy(), "topic": d["topics"]["beacon"]}, notify=None)
        sealed = "fsl2." + gcm_seal(rlogin.seal_key(K, e["T"]), json.dumps(inner, ensure_ascii=False), rlogin.sealed_aad(lid, d["pc"]["id"]))
        return {"v": 1, "M2": _b64u(rlogin.m2(K, e["T"], M1, sealed)), "sealed": sealed, "remembered": not guest, "time": now}

    # 서명 길 (연결된 기기)
    def _manage(self, dev):
        if dev.get("via", "code") != "code":  # 아이디로 연결한 기기·이번만 기기는 보기·끊기·풀기를 못 함 (D-255)
            raise ActionError(LOGIN_MSG["not_allowed"], 403, "not_allowed")

    def r_login_events(self, dev):
        self._manage(dev)
        v = self.login_view()
        return {"ok": True, "login": self._login_brief(True), "events": v.get("events") or [], "failsToday": v.get("failsToday") or 0}

    def r_login_kick(self, dev, b):
        self._manage(dev)
        ref = b.get("ref")
        if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,24}", ref):
            raise ActionError("잘못된 요청이에요")
        try:
            self.login_kill(ref=ref, where="휴대폰에서")
        except ValueError as e:
            raise ActionError(str(e), 404) from None
        return {"ok": True}

    def r_login_unlock(self, dev):
        self._manage(dev)
        try:
            self.login_unlock("휴대폰에서")
        except ValueError as e:
            raise ActionError(str(e), 409) from None
        return {"ok": True}

    def r_login_logout(self, dev):
        if not dev.get("guest"):
            raise ActionError("이번만 연결한 기기에서만 할 수 있어요", 403, "not_allowed")
        self.guest_logout(dev["id"])
        return {"ok": True}

    def guest_logout(self, dev_id):
        """[이 기기에서 나가기] (휴대폰 페이지 /r/login/logout · 스튜디오 /s/logout): 그 이번만 기기를 버림."""
        dev = self.store.guests.get(dev_id)
        if dev and self._drop_guests({dev_id}, "logout", "이 기기에서 나감"):
            self.log(f"이번만 연결한 기기가 나갔어요 · {clean(dev['name'], 40)}")

    # [끊기] 주제 듣기 (remote-login-control · 터널이 꺼져 있어도 · D-255)
    def _live_events(self):
        lg = self.store.login() if self.store else None
        now = self.clock()
        return [e for e in (lg.get("events") or []) if not e.get("kicked") and e.get("until", 0) > now] if lg else []

    def _control_ensure(self):
        with self.login_lock:
            if (self._ctl and self._ctl.is_alive()) or not self._live_events():
                return
            self._ctl = threading.Thread(target=self._control_loop, daemon=True, name="remote-login-control")
            self._ctl.start()

    def _control_loop(self):
        backoff = 5
        while not self._ctl_stop.is_set():
            lg = self.store.login()
            live = self._live_events()
            if not lg or not live:
                return
            control, since = lg["control"], min(int(e["at"]) for e in live)
            try:
                req = urllib.request.Request(f"{self.ntfy()}/{control}/json?since={since}", headers=updater.UA)
                with updater.urlopen(req, self._ctl_timeout) as r:
                    backoff = 5
                    for line in r:
                        cur = self.store.login()
                        if self._ctl_stop.is_set() or not cur or cur["control"] != control or not self._live_events():
                            break
                        self._control_message(line)
                    else:
                        self._ctl_stop.wait(1)
            except Exception:  # noqa: BLE001 — ntfy·인터넷 문제 → 잠시 뒤 다시 (주소는 기록하지 않음)
                self._ctl_stop.wait(backoff)
                backoff = min(60, backoff * 2)

    def _control_message(self, line):
        try:
            m = json.loads(line)
        except ValueError:
            return
        if not isinstance(m, dict) or m.get("event") != "message":
            return
        msg = str(m.get("message") or "").strip()
        tok = msg[5:] if msg.startswith("fsk1.") else ""
        if not rlogin.KILL_RE.fullmatch(tok):
            return
        # 먼저 살아 있는 [끊기] 표와 맞춰 봄 (sha256 하나 · 싸다) — 맞지 않는 글은 세지도 않고 버림: 주제를 아는 사람이 가짜 'fsk1.…'를
        # 쏟아부어도 진짜 [끊기]가 밀려나지 않게. 1분에 30개 제한은 진짜 표에만 (표는 많아야 20개 · 한 번 쓰면 끝)
        kh = rlogin.kill_hash(tok)
        if not any(hmac.compare_digest(str(e.get("kh")), kh) for e in self._live_events()):
            return
        now = self.clock()
        while self._ctl_seen and now - self._ctl_seen[0] > 60:
            self._ctl_seen.popleft()
        if len(self._ctl_seen) >= 30:
            return
        self._ctl_seen.append(now)
        try:
            self.login_kill(kh=kh, where="알림에서")
        except ValueError:
            pass

    def _login_tick(self):
        """5초마다: 이번만 기기 정리(30분 안 씀·12시간) · 주고받는 중 정리 · 3시간마다 'PC 찾기' 기록과 겹침 살피기 · [끊기] 듣기."""
        if self.pending is None:
            return
        now, sc = self.clock(), rlogin.time_scale()
        self.pending.sweep()
        old = [g["id"] for g in list(self.store.guests.values()) if now - (g.get("lastSeen") or 0) > GUEST_IDLE * sc or now >= g["exp"]]
        if old:
            self._drop_guests(set(old), "expired", "이번만 기기 시간 끝")
        lg = self.store.login()
        if not lg or not lg.get("enabled"):
            return
        if now - self._lookup_at >= LOOKUP_HB * sc:
            self._publish_lookup()
        if now - self._collision_at >= LOOKUP_HB * sc:
            self._collision_at = now
            self._recheck_collision()
        self._control_ensure()


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
    return svc._go(dev, "편집점 찾기", lambda: svc.bridge.analyze({"names": names, "model": core.default_model()}),
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


def _a_auto(svc, dev, a, tok):
    """끝까지 자동으로 (D-203): 휴대폰에서도 같은 작업 · 받아쓰기 방식·클로드 오타 고치기·재미 자막은 PC 화면에서 마지막으로 고른 것
    (autorun.load_prefs · E7 검토) · 유튜브에는 올리지 않음 (키트까지).
    썸네일은 PC 의 스튜디오 화면이 그림 (화면이 없으면 그 단계만 '확인할 것')."""
    n = _one_video(a.get("name"))
    resume = a.get("resume") is True
    opts = autorun.options({k: a.get(k) for k in ("intensity", "shorts", "style")})
    return svc._go(dev, autorun.JOB, lambda: autorun.run(n, opts, svc.bridge.log, svc.bridge.analyze, resume), n)


ACTIONS = {"download": _a_download, "analyze": _a_analyze, "add_refs": _a_add_refs, "learn_refs": _a_learn_refs, "autoseq": _a_autoseq,
           "export": _a_export, "qa": _a_qa, "preview": _a_preview, "auto": _a_auto, "cancel": None}


# ---------- 원격 리스너 (HTTP) ----------

class RemoteServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    # app._Server 와 같은 규칙 (D-030): Windows 의 SO_REUSEADDR 는 다른 프로세스가 듣고 있는 포트도 같이 잡음 →
    # FUTSAL_REMOTE_PORT 가 쓰이는 중이면 조용히 같이 듣지 않고 _new_listener 가 기다렸다 '열지 못했어요'로
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, addr, svc):
        self.svc = svc
        super().__init__(addr, svc.handler_class())

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
        with svc._busy_lock:
            svc._busy += 1
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
            with svc._busy_lock:
                svc._busy -= 1
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
        login_open = (method, path) in LOGIN_OPEN
        net = net_of(ip) if login_open else None  # 아이디 연결의 열린 길: 막기·요청 수는 인터넷 대역으로도 (IPv6 주소 바꾸기로 못 피함)
        if svc.limiter.locked(ip) or (net and svc.limiter.locked(net)):
            return self._json(429, {"error": "잠시 막았어요 · 15분 뒤 다시 해 주세요"}, {"Retry-After": str(LOCKOUT)})
        studio = not path.startswith("/r/")  # 밖에서 작업 화면 (D-230) — /r/* 는 아래 예전 순서 그대로
        if studio and not hasattr(self, "studio_call"):  # 앱이 화면 처리기를 넘겨주지 않음 → 그 밖의 길은 없음
            return self._json(401, {"error": "연결 정보가 없어요", "code": "none"})
        bucket = rstudio.bucket_of(path) if studio else "open" if path in ("/r/ping", "/r/pair") else "login" if login_open else "auth"
        if not svc.limiter.take(bucket, net or ip):
            return self._json(429, {"error": "너무 자주 눌렀어요 · 잠시 뒤 다시 해 주세요"}, {"Retry-After": "5"})
        if login_open and path == "/r/login/start" and not svc.limiter.take("login_start", "*"):  # PC 하나에 모두 합친 start 수 (거듭제곱 전에)
            return self._json(429, {"ok": False, "code": "busy", "error": LOGIN_MSG["busy"]}, {"Retry-After": "5"})
        if studio:
            return rstudio.gate(self, method, svc, ip)
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
            if n < 0 or n > (LOGIN_BODY_MAX if login_open else BODY_MAX):  # 아이디 연결의 열린 길은 4KB 까지 (읽기 전에)
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
        if login_open:  # 아이디·비밀번호로 연결 (D-251): 열린 길 (Origin·JSON·4KB 는 위에서) · 틀린 비밀번호는 잠금 횟수에도 셈
            b = _json_body(body)
            if b is None:
                return self._json(400, {"ok": False, "code": "bad_request", "error": LOGIN_MSG["bad_request"]})
            ua, cc = self.headers.get("User-Agent") or "", self.headers.get("Cf-Ipcountry") or ""
            try:
                fn = svc.r_login_start if path.endswith("/start") else svc.r_login_finish
                return self._json(200, fn(b, ip, ua, cc))
            except LoginError as e:
                if e.code == "bad" or e.counted:  # 틀린 비밀번호 (그 시도로 잠겼어도) → 주소와 인터넷 대역 둘 다 셈
                    self._fail(svc, ip)
                    self._fail(svc, net)
                return self._json(e.status, dict({"ok": False, "code": e.code, "error": str(e), "time": int(svc.clock())}, **e.extra))
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
                  ("POST", "/r/notify-test"): lambda: svc.r_notify_test(dev), ("POST", "/r/handoff"): lambda: svc.r_handoff(dev, b, ip),
                  # 아이디·비밀번호로 연결 (D-255): 보기·끊기·풀기는 QR·코드 기기만 · 나가기는 이번만 기기만
                  ("GET", "/r/login/events"): lambda: svc.r_login_events(dev), ("POST", "/r/login/kick"): lambda: svc.r_login_kick(dev, b),
                  ("POST", "/r/login/unlock"): lambda: svc.r_login_unlock(dev), ("POST", "/r/login/logout"): lambda: svc.r_login_logout(dev)}
        if (method, path) == ("POST", "/r/remote-off"):  # 더 안전하게만 (휴대폰에서 켜는 길은 없음)
            if dev.get("guest"):  # 이번만 기기(빌린 기기)는 모두의 실행기를 끄지 못함 — 끝낼 때는 [이 기기에서 나가기] (D-256)
                return self._json(403, {"ok": False, "code": "not_allowed", "error": LOGIN_MSG["guest_off"]})
            svc.log(f"원격 · {clean(dev['name'], 40)} · 실행기 끄기")
            self._json(200, {"ok": True})
            threading.Timer(0.3, svc.switch, args=(False,)).start()  # 사람이 끈 OFF 는 PC 의 OFF 와 같음: 스튜디오를 다시 켜도 꺼진 채 (D-310)
            return
        fn = routes.get((method, path))
        if not fn:
            return self._json(404, {"error": "없는 주소예요"})
        try:
            return self._json(200, fn())
        except ActionError as e:
            return self._json(e.status, dict({"ok": False, "error": str(e)}, **({"code": e.code} if e.code else {})))

    def _fail(self, svc, ip):
        if svc.limiter.failed(ip) and svc.clock() - svc._lockout_logged > 600:
            svc._lockout_logged = svc.clock()
            svc.log("원격 접속 · 잘못된 연결 시도가 많아 잠시 막았어요")

    def _media(self, svc, tid, ip):
        it = svc.tickets.get(tid, ip) if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", tid or "") else None
        if not it:  # 없거나·지났거나·다른 곳(IP 대역)에서 온 표 → 휴대폰 화면이 목록을 다시 받아 새 표로 (403: 허락 안 됨 · 같은 문장)
            return self._json(403, {"error": "다시 불러와 주세요"})
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
    if SVC.store.data.get("enabled") or SVC.store.data["settings"].get("alwaysOn"):
        SVC.turn_on()  # 켜 둔 채로 앱을 껐거나 업데이트로 다시 켜졌으면 · 늘 켜기(D-295 · 기본)면 스튜디오를 켤 때마다 이어서 켬
    return SVC
