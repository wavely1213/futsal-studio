"""풋살사관학교 스튜디오 — 데스크톱 앱 (화면은 전용 창, 내부 통신은 127.0.0.1 전용)."""
import base64
import collections
import errno
import faulthandler
import json
import mimetypes
import os
import re
import time
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import autorun
import bundle
import captions
import claude_cli
import core
import cutout_worker
import editor
import hooks
import idle
import intake
import msg
import plan
import qa
import qr
import refs
import remote
import rename
import rstudio
import source
import strategy
import studiolog
import trouble
import style
import tactic
import thumb
import thumbcopy
import thumbstyle
import updater
import upload
import winlink
import youtube_upload


def _utf8_console():
    """Windows 콘솔·기록 출력은 기본이 cp949 라 쪼개진 한글 자모(예: 'ᄃ')·이모지가 든 파일 이름을 쓰면 오류가 남
    → UTF-8 로 바꾸고, 그래도 못 쓰는 글자는 대신 표시 (출력 때문에 작업이 멈추지 않게)."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pythonw(콘솔 없음)는 None · 바꿀 수 없는 출력은 그대로
            pass


_utf8_console()

PORT = int(os.environ.get("FUTSAL_PORT", "8765"))
PORT_FALLBACK = range(PORT + 1, PORT + 35)  # 8765 를 다른 프로그램이 쓰거나 Windows(Hyper-V·WSL·Docker)가 예약해 두었으면
APP_ID = "futsal-studio"  # /api/ping: 이 포트에서 듣는 게 이 앱인지 (다른 프로그램이면 창을 띄우라고 보내지 않음)
LOG, JOB = [], {"name": None, "result": None, "error": None, "by": None, "t0": None, "id": 0, "big": False}
DONE = collections.OrderedDict()  # 끝난 작업 번호 → {이름·시킨 곳·결과·오류} (최근 20개) — PC 화면이 자기가 시킨 작업의 결과만 받게 (휴대폰 작업과 안 섞임)
FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".woff2": "font/woff2"}  # /fonts/ 응답 종류 (확장자로)
BIG_RESULT = 64 << 10   # 이보다 큰 작업 결과(MSG 후보 묶음)는 /api/state?result=1 로 물을 때만 (1초마다 몇 MB 를 다시 보내지 않게)
LOCK = threading.Lock()
BUSY_MSG = "다른 작업이 끝난 뒤에 다시 눌러 주세요"
RESTARTING = threading.Event()  # 업데이트 다시 시작이 정해짐 → 새 작업(PC·휴대폰)을 받지 않음 (곧 이 프로세스가 끝나 그 작업이 끊기므로)
RESTART_WAIT_MSG = "지금 하는 작업이 끝나면 다시 시작해요"
JOB_HOOKS = []  # 작업이 끝나면 부름 (이름, 오류, 결과, 시킨 곳, 걸린 초) — 휴대폰 알림·결과 (remote.Service.job_hook)
_REMOTE = threading.local()  # 밖에서 작업(rstudio.gate → StudioHandler.studio_call) 요청을 처리하는 동안만: who = 그 기기 이름


LOGFILE = core.WORK / "studio.log"
STYLE_JOBS = ("스타일 배우기", "클로드로 더 깊게 보기", "학습용 영상 받기", "학습용 스타일 배우기")   # 스타일 파일(plan)을 끝에 다시 쓰는 작업
studiolog.setup(lambda: LOGFILE)  # studio.log 쓰기: 연도 붙은 시각 · 크기 제한 · 오류 위치 (시험은 LOGFILE 을 바꿔 끼움)


def log(msg):
    msg = core.clean_text(str(msg))  # 반쪽 이모지가 섞이면 화면 응답(/api/state)·파일 기록이 오류로 멈춤 → '�'로
    msg = remote.redact(msg)  # 비밀(터널 주소·주제·Google 토큰·업로드 세션 주소 등)이 섞여도 studio.log·화면·휴대폰 기록에 남지 않게 (D-048)
    with LOCK:
        LOG.append(msg)
    try:
        print(msg, flush=True)
    except Exception:  # 화면 출력이 안 돼도 작업·파일 기록은 계속
        pass
    studiolog.write(msg)  # 콘솔 없이 실행되므로 파일에도 남김 (연도 붙은 시각 · 커지면 studio.old.log 로 · 비밀은 '…' · 실패해도 작업은 계속)


# 프로그램 오류가 아닌 실패(사용자가 멈춤·복사 중·YouTube 막힘·비공개 영상·로그인·탭 없음): studio.log 에 원문 줄만 — 오류 위치·traceback 은 남기지 않음
EXPECTED_KINDS = {"cancelled", "copying", "blocked", "unavailable", "login", "notab",
                  # 유튜브 올리기: 할당량·연결 끊김·권한·썸네일 막힘·인터넷·서버 (Google 이 알려 준 사정 · D-049)
                  "yt_quota", "yt_limit", "yt_relogin", "yt_forbidden", "yt_thumb", "yt_net", "yt_server", "yt_relogin_left", "yt_quota_left"}
YT_JOBS = (youtube_upload.JOB_NAME, youtube_upload.JOB_FINISH)
YT_CTX = {"youtube": True}  # 유튜브 올리기 작업의 start_job ctx → trouble.explain(youtube=True): 7단계 실패 카드 (받기 쪽 안내 대신)


def start_job(name, fn, by=None, ctx=None):
    """긴 작업 하나 시작 → 작업 번호(1부터 · 참) · 이미 돌고 있으면 False. by: 휴대폰에서 시켰으면 '휴대폰 · <기기 이름>'.
    실패하면 JOB error(쉬운 한 줄)·fail(종류·할 일 · trouble.explain) — 끝난 작업(DONE[번호])에도 같이 남겨 그 작업을 시킨 화면이 받음.
    ctx: 오류 안내에 쓸 것 — browser(고른 로그인 정보 브라우저)·blocked(이 화면용 YouTube 막힘 안내).
    밖에서 작업(D-230) 요청이 시킨 작업이면 by='밖에서 · <기기>' 이고 다운로드 엔진·Deno 를 스스로 설치·업데이트하지 않는다 (휴대폰과 같게)."""
    who = getattr(_REMOTE, "who", None)
    if who and by is None:
        by, inner = f"밖에서 · {who}", fn

        def fn():
            with core.no_self_update():
                return inner()
    with LOCK:
        if JOB["name"] or RESTARTING.is_set():
            return False
        jid = JOB["id"] + 1
        JOB.update(name=name, result=None, error=None, fail=None, big=False, by=by, t0=time.time(), id=jid)

    def runner():
        studiolog.job(name)  # 실행 표시에 작업 이름 (작업 중에 갑자기 꺼지면 다음에 켤 때 알림)
        core.set_progress()
        editor.CANCEL.clear()  # 예전 작업에서 누른 멈추기(✕)가 다음 작업에 남지 않게
        claude_cli.job_begin()  # 이 작업에서 클로드를 쓴 양 (끝나면 작업 기록에 한 줄 · D-394)
        try:
            with core.keep_awake():  # 켜 두고 자리를 비워도 Windows 가 절전으로 들어가 작업이 멈추지 않게 (모든 작업)
                res = fn()
            try:
                JOB["big"] = len(json.dumps(res, ensure_ascii=False)) > BIG_RESULT
            except (TypeError, ValueError):
                JOB["big"] = False
            JOB["result"] = res
        except Exception as e:
            info = dict(trouble.explain(e, **(ctx or {})), job=name)  # 영어 원문 → 쉬운 한 줄 + 할 일 (원문·위치는 studio.log 에만)
            _fail_extra(info, e)
            JOB["error"], JOB["fail"] = info["msg"], info
            log(f"문제가 생겼어요 · {info['msg']}")
            yt_job = bool((ctx or {}).get("youtube"))  # 유튜브 올리기: 토큰 새로 받기·저장 중 오류 글엔 redact 가 모르는 비밀이 섞일 수 있음
            studiolog.write(f"  원문 · {type(e).__name__}" if yt_job else  # → 원문 글 없이 종류만 (로그인 콜백과 같은 기준 · D-049)
                            f"  원문 · {' '.join(str(e).split())[:400]}")
            if info["kind"] not in EXPECTED_KINDS:  # 사용자 쪽 사정(멈춤·복사 중·막힘…)은 원문 줄만
                studiolog.trace(e, detail=not yt_job)
        finally:
            try:
                line = claude_cli.job_end()
                if line:
                    log(line)  # 예: '클로드 3번 · 약 12천 토큰 · 아껴 쓰기 (저장해 둔 대답 2번 다시 씀)' — 숫자만 (프롬프트·대답 없음)
            except Exception as ce:  # noqa: BLE001 — 기록이 작업을 깨뜨리지 않게
                studiolog.trace(ce, "클로드 사용 기록 오류 위치")
            core.set_progress()
            err, res, secs = JOB["error"], JOB["result"], time.time() - (JOB["t0"] or time.time())  # 다음 작업이 바로 시작돼도 이 작업 값으로
            fail = JOB.get("fail")
            with LOCK:
                DONE[jid] = {"name": name, "by": by, "result": res, "error": err, "fail": fail}
                while len(DONE) > 20:
                    DONE.popitem(last=False)
                JOB["name"] = None
                _SEEN["job"] = time.time()  # 자리를 놓는 순간 → 자동 업데이트가 화면이 이어서 시킬 작업 사이에 끼어들지 않게 (AUTO_AFTER_JOB)
            studiolog.job(None)
            for h in list(JOB_HOOKS):
                try:  # 알림 같은 곁가지가 작업을 깨뜨리지 않게
                    h(name, err, res, by, secs)
                except Exception as he:  # noqa: BLE001
                    studiolog.trace(he, "작업 끝 알림 오류 위치")

    threading.Thread(target=runner, daemon=True).start()
    return jid


_SECS = {}  # 보관함 영상 길이 (이름, 크기, 수정 시각) → 초 · 받아쓰기 예상 시간용 (ffmpeg -i 를 고를 때마다 다시 안 부름)
_SECS_RUN = set()  # 지금 길이를 읽는 중인 영상 (같은 영상을 두 요청이 동시에 읽지 않게)
_SECS_LOCK = threading.Lock()
_SECS_FILL = {"q": [], "on": False}  # 뒤에서 하나씩 읽을 영상 (마지막으로 고른 것만)
PROBE_MAX = 6  # 한 번 물을 때 그 자리에서 길이를 읽는 영상 수 · 나머지는 크기로 어림하고 뒤에서 하나씩 (100개를 골라도 요청이 안 막히게)


def _secs_key(name):
    try:
        st = (core.VIDEOS / name).stat()
    except OSError:
        return None, 0
    return (name, st.st_size, st.st_mtime_ns), st.st_size


def _probe_secs(key):
    """영상 하나의 길이(초)를 읽어 기억 → 초 (못 읽으면 0) · 다른 요청이 읽는 중이면 None (기다리지 않음)."""
    with _SECS_LOCK:
        if key in _SECS:
            return _SECS[key]
        if key in _SECS_RUN:
            return None
        _SECS_RUN.add(key)
    try:
        try:
            v = float(editor.probe(core.VIDEOS / key[0])["duration"] or 0)
        except Exception:  # noqa: BLE001 — 안내용 · 못 읽으면 0
            v = 0.0
        with _SECS_LOCK:
            if len(_SECS) > 2000:
                _SECS.clear()
            _SECS[key] = v
        return v
    finally:
        with _SECS_LOCK:
            _SECS_RUN.discard(key)


def _fill_secs_later(keys):
    """어림한 영상들의 길이는 뒤에서 한 번에 하나씩 읽어 둠 (다음에 물으면 정확히) · 새로 고르면 그 목록으로 바꿈."""
    with _SECS_LOCK:
        _SECS_FILL["q"] = list(keys)
        if _SECS_FILL["on"]:
            return
        _SECS_FILL["on"] = True

    def run():
        while True:
            with _SECS_LOCK:
                if not _SECS_FILL["q"]:
                    _SECS_FILL["on"] = False
                    return
                k = _SECS_FILL["q"].pop(0)
            _probe_secs(k)
    threading.Thread(target=run, daemon=True, name="영상 길이 읽기").start()


def _selected_secs(names):
    """고른 영상들의 길이 합 → (초, 크기로 어림한 영상 수). 기억한 길이 + 이번에 PROBE_MAX 개까지 읽고, 나머지는
    읽은 영상들의 초당 바이트(모르면 초당 1MB)로 어림 · 뒤에서 하나씩 읽어 둠."""
    total, rest, probed, known_b, known_s = 0.0, [], 0, 0, 0.0
    for n in names:
        key, size = _secs_key(n)
        if key is None:
            continue
        with _SECS_LOCK:
            v = _SECS.get(key)
        if v is None and probed < PROBE_MAX:
            v = _probe_secs(key)  # 뒤에서 읽는 중이면 None (기다리지 않고 어림 · 읽은 수에도 안 셈)
            probed += v is not None
        if v is None:
            rest.append((key, size))
            continue
        total += v
        if v > 0:
            known_b, known_s = known_b + size, known_s + v
    if rest:
        bps = known_b / known_s if known_s else 1e6
        total += sum(size / bps for _, size in rest)
        _fill_secs_later([k for k, _ in rest])
    return total, len(rest)


def _started(ok):
    """작업 시작 응답: jobId 로 화면이 '내가 시킨 작업'의 끝을 기다림 (/api/state?job=<번호> 의 done)."""
    return {"ok": bool(ok), "jobId": ok or None, "error": None if ok else BUSY_MSG}


def _job_snapshot():
    """휴대폰 화면용 지금 작업 모습 (remote.Bridge.job)."""
    with LOCK:
        return {"name": JOB["name"], "id": JOB["id"] if JOB["name"] else None, "by": JOB["by"], "t0": JOB["t0"], "progress": dict(core.PROGRESS),
                "result": JOB["result"], "error": JOB["error"]}


def _log_lines(since):
    with LOCK:
        return LOG[since:], len(LOG)


def _remote_bridge():
    return remote.Bridge(log=log, start_job=start_job, job=_job_snapshot, logs=_log_lines, analyze=Handler._analyze,
                         refs_job=_refs_job, version=core.VERSION, studio=StudioHandler)


def _fail_extra(info, e):
    """작업이 붙여 준 다시 하기 범위(retry: 화면이 보낼 값 · 예: 남은 영상 이름)와 한 줄 덧붙임(note)을 실패 안내에."""
    if isinstance(getattr(e, "retry", None), dict):
        info["retry"] = e.retry
    if getattr(e, "note", None):
        info["msg"] = f"{info['msg']} · {e.note}"
    return info


def _thumb_habits(res):
    """스타일 배우기 끝에 이어서 썸네일 버릇도 배움 (i.ytimg.com · 실패해도 스타일은 그대로 · D-130). res: style.learn 결과 또는 학습용 작업 결과."""
    if not isinstance(res, dict):
        return res
    if isinstance(res.get("name"), str) and "profile" in res:
        res["thumb"] = thumbstyle.learn_quiet([res["name"]], log).get(res["name"])
    for x in res.get("learned") or []:
        if isinstance(x, dict) and isinstance(x.get("style"), str) and not x.get("error"):
            x["thumb"] = thumbstyle.learn_quiet([x["style"]], log).get(x["style"])
    return res


def _style_thumbs(name):
    """스타일 카드 [썸네일 버릇 배우기] 작업 → 결과 (사용자 안내는 ok False + error)."""
    try:
        return dict(thumbstyle.learn(str(name or ""), log), ok=True)
    except (thumbstyle.ThumbStyleError, style.StyleError) as e:
        log(f"  {e}")
        return {"ok": False, "error": str(e)}


def _refs_job(fn, ck=None):
    """학습용 영상 작업: 사용자에게 보여 줄 안내(채널 주소 없음·YouTube 막힘·멈춤)는 결과로 돌려줌 (그 밖의 오류는 작업 기록에).
    ck: 고른 로그인 정보 브라우저 — 막혔을 때 '그 브라우저 로그인 정보로도 막힘' 안내 (고르지 않았으면 이 화면 설정 칸을 가리킴)."""
    try:
        return dict(fn(), ok=True)
    except (refs.RefsError, style.StyleCancelled) as e:
        log(f"  {e}")
        return {"ok": False, "error": str(e)}
    except RuntimeError as e:
        if str(e) != core.BLOCKED_MSG:
            raise
        # 휴대폰에서 시킨 받기는 엔진을 바꾸지 않았고 이 화면도 없음 → PC 에서 할 일 (core.REMOTE_BLOCKED_MSG)
        info = trouble.explain(e, browser=ck, blocked=refs.BLOCKED_MSG if core.self_update_allowed() else core.REMOTE_BLOCKED_MSG)
        log(f"  {info['msg']}")
        return {"ok": False, "error": info["msg"], "blocked": True,  # 고르지 않았으면 이 화면의 '로그인 정보로 받기'를 가리킴
                "fail": info}


def reveal(path):
    """파일이 든 폴더를 열고 그 파일을 골라 보여 줌 (Windows 탐색기 · Mac Finder) · 파일이 없으면 폴더만."""
    path = Path(path)
    if not path.exists():
        return open_folder(path.parent)
    if sys.platform == "win32":
        subprocess.Popen(f'explorer /select,"{path}"')  # 목록으로 넘기면 따옴표가 '/select,' 까지 감싸 탐색기가 못 알아들음
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        open_folder(path.parent)


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def restart(auto=False):
    """새 프로세스로 앱을 다시 띄우고 지금 프로세스는 종료 (업데이트 후). 순서 (D-034):
    1. 실행기(원격)를 끔 (터널·리스너·마지막 비콘이 새 프로세스의 켜기와 겹치지 않게 · 켜 둠 표시는 그대로 → 새 앱이 이어서 켬)
    2. 실행기(updater --launch)를 띄움 — 이 프로세스 번호(FUTSAL_OLD_PID)를 넘겨, 뒤로 미룬 구성요소 설치(.req_pending)는
       이 프로세스가 끝나 .pyd 를 놓은 뒤에 하게 (.launch_lock 안에서) → 3. 끝내기 (남은 자식은 Job Object 로 같이 꺼짐).
    auto: 자동 업데이트(D-311)의 다시 시작 → 새 창은 내려 둔 채로 (FUTSAL_AUTO · 다른 프로그램을 쓰는 사람 앞에 창이 뜨지 않게)."""
    log("다시 시작하는 중…")
    remote.SVC.shutdown()
    kw = {"cwd": str(core.APP_DIR)}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    kw["env"] = dict(os.environ, FUTSAL_RESTART="1", FUTSAL_OLD_PID=str(os.getpid()))  # 새 프로세스는 '이미 실행 중' 확인을 건너뜀
    if auto:
        kw["env"]["FUTSAL_AUTO"] = "1"
    args = ["--browser"] if "--browser" in sys.argv else []
    try:
        subprocess.Popen([_gui_python(), str(core.APP_DIR / "updater.py"), "--launch", *args], **kw)  # 새 버전이 열리는지 확인 후 실행
    except OSError as e:  # 업데이트 실행기(updater)를 못 띄우면 끄지 않음 (작업은 다시 받음 · 원격 실행기는 다음에 켤 때 이어서)
        log(f"다시 시작하지 못했어요 · {e} · 프로그램을 닫고 다시 켜 주세요")
        RESTARTING.clear()
        return
    _quit()


def _quit():
    """앱 끝내기: 휴대폰에 '앱을 껐어요'를 알리고 터널을 끔 → 실행 표시(studio.running.json)를 지움(정상 종료) →
    저장 중인 프로젝트는 끝까지 쓰고, 남은 ffmpeg 는 끔 (Windows 는 자식 프로세스가 같이 안 꺼짐)."""
    remote.SVC.shutdown()
    studiolog.session_end()  # 창 닫기(작업 중이면 확인을 받은 뒤)·업데이트 재시작 → 다음에 켤 때 '갑자기 꺼짐'으로 보지 않음
    editor.wait_saves(3)
    editor.cancel_export()
    os._exit(0)


def _gui_python():
    """Windows 에서는 콘솔 창이 안 뜨는 pythonw.exe 사용."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        return str(exe.parent / "pythonw.exe")
    return str(exe)


# ---------- 안전한 업데이트 · 다운로드 엔진 자동 관리 ----------

def _after_start():
    """앱이 잘 켜진 뒤(포트 확보): 업데이트 표시 정리·결과 알림, 설정 파일 안내, 다운로드 엔진은 뒤에서 확인 (3일마다 최신으로)."""
    try:
        for line in updater.finish(core.APP_DIR):
            log(line)
    except Exception as e:
        log(f"업데이트 마무리 중 문제가 생겼어요 · {e}")
    for note in core.CONFIG_NOTES:  # config.json 을 못 읽었거나 작업 폴더를 못 써서 기본값으로 켰음 → 기록 + 화면 알림 한 번
        log(note)
        updater._NOTICES.append({"text": note, "warn": True})
    if sys.platform in ("win32", "darwin"):
        threading.Thread(target=core.engine_autoupdate, args=(log,), daemon=True).start()
    _session_start()
    threading.Thread(target=_place_kept, daemon=True).start()
    core.app_autoupdate(log, _auto_claim, _auto_done)  # 쉴 때 새 버전을 받아 다시 시작 (D-311 · config "auto_update": false 면 안 함)


def _place_kept():
    """지난번에 백신·OneDrive 잠금으로 제자리에 못 옮긴 완성본·묶음 → 완성본 폴더·보관함으로 (켤 때 한 번 · 실패해도 앱은 그대로)."""
    for fn in (editor.place_kept, bundle.place_kept):
        try:
            fn(log)
        except Exception as e:  # noqa: BLE001 — 곁가지
            studiolog.trace(e, "옮기지 못한 결과 옮기기 오류 위치")


# 자동 업데이트 (D-311 · 확인·설치는 core.auto_update_once): 스튜디오를 쓰지 않을 때만 — 하는 작업·다시 시작·편집실이 만드는 파일·
# 밖에서 주고받는 파일이 없고, 작업이 끝난 뒤 AUTO_AFTER_JOB, PC 화면에서 AUTO_LOCAL_QUIET, 밖에서 작업·원격 홈이 AUTO_REMOTE_QUIET,
# (Windows) PC 키보드·마우스가 AUTO_INPUT_QUIET 동안 조용할 때. 아니면 core 가 5분 뒤 다시 물음.
AUTO_JOB = "자동 업데이트"
AUTO_LOCAL_QUIET = 1800   # PC 화면에서 사람이 마지막으로 무언가 한 뒤 (화면이 저절로 묻는 QUIET_REQS·AUTO_HEADER 는 안 셈)
AUTO_REMOTE_QUIET = 600   # 밖에서 작업 세션·원격 홈의 마지막 요청 뒤 (그 화면이 열려 있으면 묻기만 해도 셈 · 원격 홈은 화면이 보일 때만 물음)
AUTO_INPUT_QUIET = 600    # Windows: PC 키보드·마우스를 (다른 프로그램에서라도) 마지막으로 쓴 뒤 — PC 를 쓰는 사람 앞에서 창을 다시 띄우지 않게
AUTO_AFTER_JOB = 120      # 작업이 끝난 뒤 — 화면이 이어서 시키는 작업(내보내기 → 자동 검수)·끝난 뒤 새로 고침 사이에 끼어들지 않게
AUTO_UPLOAD_GAP = 120     # 밖에서 올리는 파일: 마지막 조각이 이 시간 안에 왔으면 '올리는 중' (조각과 조각 사이)
AUTO_SETTLE_STEP = 10     # 설치를 마친 뒤 다시 시작하기 전: 이 간격으로 다시 봄
AUTO_SETTLE_QUIET = 120   # 설치하는 사이 누가 쓰기 시작했으면 이만큼 조용해질 때까지 기다림
AUTO_SETTLE_MAX = 600     # 그래도 계속 쓰면 이만큼만 기다리고 다시 시작 (그동안 새 작업은 409) · 주고받는 파일·편집실 ffmpeg 는 끝날 때까지
# 화면이 저절로 묻는 길 (사람이 누른 것이 아님): 상태·핑·원격 상태·썸네일 그릴 것·업데이트 확인·유튜브 상태 · 출처 찾기
QUIET_REQS = {("GET", "/api/state"), ("GET", "/api/ping"), ("GET", "/api/remote"), ("GET", "/api/auto/pending"),
              ("GET", "/api/update/check"), ("GET", "/api/youtube/status"), ("POST", "/api/source/check")}
AUTO_HEADER = "X-FS-Auto"  # 화면이 저절로 보낸 요청 표시 ('1') — 길이 같아도 사람이 누른 것과 가름 (7단계 올린 영상 처리 확인과 그 뒤 새로 고침)
_SEEN = {"local": time.time(), "job": 0.0}  # PC 화면에서 사람이 마지막으로 한 일 (켠 때부터 셈 — 켜자마자 바꾸지 않게) · 작업이 마지막으로 끝난 때


def _auto_busy():
    """다시 시작하면 끊기는 일 (끝날 때까지 기다림): 작업 밖 편집실 ffmpeg · 밖에서 주고받는 파일 (원격 리스너가 처리 중인 요청 —
    휴대폰이 받는 완성본·영상 보기·올리기 조각 · 조각 사이의 올리기). None = 없음."""
    if editor._PROCS:  # 작업 밖에서 도는 내보내기·미리보기 파일 ffmpeg
        return "편집실이 파일을 만드는 중"
    if remote.SVC.in_flight():
        return "밖에서 파일을 주고받는 중"
    st = remote.SVC.studio
    if st is not None and st.uploads.active(AUTO_UPLOAD_GAP):
        return "밖에서 파일을 올리는 중"
    return None


def _auto_used(local_quiet, remote_quiet):
    """사람이 쓰는 중인 까닭 (None = 조용함): PC 화면 · 밖에서 작업 세션 · 원격 홈(휴대폰 · 화면이 보이는 동안 10초마다 물음)."""
    if time.time() - _SEEN["local"] < local_quiet:
        return "PC 화면을 쓰는 중"
    svc = remote.SVC
    if svc.studio is not None:
        now = svc.studio.sessions.clock()
        if any(now - r["last"] < remote_quiet for r in svc.studio.sessions.all()):
            return "밖에서 작업 중"
    if svc.clock() - svc.last_auth < remote_quiet:
        return "원격 홈을 보는 중"
    return None


def _auto_why():
    """자동 업데이트를 지금 하면 안 되는 까닭 (None = 쉬는 중)."""
    if JOB["name"]:
        return f"'{JOB['name']}' 작업 중"
    if RESTARTING.is_set():
        return "다시 시작하는 중"
    why = _auto_busy()
    if why:
        return why
    if time.time() - _SEEN["job"] < AUTO_AFTER_JOB:
        return "작업이 막 끝남"
    why = _auto_used(AUTO_LOCAL_QUIET, AUTO_REMOTE_QUIET)
    if why:
        return why
    idle = core.input_idle_secs()
    if idle is not None and idle < AUTO_INPUT_QUIET:
        return "PC를 쓰는 중"
    return None


def _auto_claim():
    """쉬는 중이면 작업 자리를 잡고 None (설치하는 동안 PC·휴대폰·밖에서 새 작업을 시작하지 못하게 · 화면엔 '자동 업데이트 중') · 아니면 까닭.
    자리는 새 작업 번호로 (앞 작업의 결과·오류·번호를 이 자리가 다시 내보이지 않게)."""
    why = _auto_why()
    if why:
        return why
    with LOCK:
        if JOB["name"] or RESTARTING.is_set():  # 확인한 사이에 시작됨
            return BUSY_MSG
        JOB.update(name=AUTO_JOB, result=None, error=None, fail=None, big=False, by=None, t0=time.time(), id=JOB["id"] + 1)
    return None


def _auto_settle(sleep=time.sleep, clock=time.time):
    """설치를 마친 뒤 다시 시작하기 전 (작업 자리는 그대로 '자동 업데이트' → 새 작업은 409): 설치하는 사이 시작된 일이 있으면 기다림 —
    편집실 ffmpeg·밖에서 주고받는 파일은 끝날 때까지, 사람이 쓰는 것(PC 화면·밖에서 작업·원격 홈)은 AUTO_SETTLE_QUIET 동안 조용해질 때까지
    (길어야 AUTO_SETTLE_MAX · 그 뒤로는 다시 시작). 기다리면 기록 한 줄 + 진행 줄."""
    start, said = clock(), False
    while True:
        why = _auto_busy() or (clock() - start < AUTO_SETTLE_MAX and _auto_used(AUTO_SETTLE_QUIET, AUTO_SETTLE_QUIET))
        if not why:
            return
        if not said:
            said = True
            log("  새 버전을 받았어요 · 하던 일이 끝나면 다시 시작해요")
            core.set_progress(label="새 버전을 받았어요", detail="하던 일이 끝나면 다시 시작해요")
        sleep(AUTO_SETTLE_STEP)


def _auto_done(restart_now, fail=None):
    """자리를 놓음. 다시 시작이면 먼저 설치하는 사이 시작된 일을 기다린 뒤(_auto_settle), 자리를 놓으며 RESTARTING 을 한 잠금 안에서 →
    설치와 다시 시작 사이에 끼어드는 작업이 없음 (/api/restart 와 같은 표시 · 실행기를 못 띄우면 restart 가 표시를 지우고 그대로 켜 둠).
    fail: 실패한 쉬운 한 줄 → 휴대폰 '마지막 작업' 칸 (알림은 안 보냄)."""
    if restart_now:
        _auto_settle()
        core.set_progress()
    # 휴대폰 '마지막 작업' 칸을 이 자리로 (자리를 놓기 전에 → 원격 홈이 작업이 끝난 것을 보고 그 전 작업의 끝을 또 알리지 않게)
    remote.SVC.last = {"name": AUTO_JOB, "ok": not fail, "warn": bool(fail), "error": remote.scrub(fail) if fail else None,
                       "blocked": False, "endedAt": int(remote.SVC.clock()), "by": None}
    with LOCK:
        JOB["name"] = None
        if restart_now:
            RESTARTING.set()
    if restart_now:
        restart(auto=True)


def _redirect_to_updater():
    """app.py 를 바로 켰는데(예전에 고정한 작업 표시줄 아이콘 등) 업데이트가 중간에 끊겨 있으면
    실행기(updater.py --launch)를 거쳐 다시 켬 → 섞인 파일을 이전 버전으로 되돌린 뒤 열림. 그렇게 했으면 True."""
    if os.environ.pop("FUTSAL_VIA_UPDATER", None):
        return False
    try:
        p = updater._read_json(core.APP_DIR / updater.PENDING)
        if not isinstance(p, dict) or p.get("state") == "installed":
            return False
        updater.spawn(core.APP_DIR, sys.argv[1:])
        return True
    except Exception:
        return False


GONE_MSG = "이 영상의 이름이 바뀌었거나 보관함에서 빠졌어요 · 스튜디오에서 다시 열어 주세요"  # 옛 이름으로 열린 편집실·썸네일의 저장 (404 gone)
# 편집실 저장이 안 된 까닭 (trouble.explain 의 종류 → 머리줄에 보일 쉬운 한 줄 · D-412) — 같은 까닭은 SAVE_FAIL_EVERY 초에 한 번만 기록
SAVE_WHY = {"disk": "저장 공간이 모자라요 · 필요 없는 파일을 지워 공간을 비워 주세요",
            "locked": "다른 프로그램(OneDrive·백신 등)이 편집본 파일을 잡고 있어요 · 잠시 뒤 저절로 다시 저장해요",
            "other": "작업 폴더의 편집본 파일에 쓰지 못했어요"}
SAVE_FAIL_EVERY = 60  # 편집실은 3초마다 다시 저장함 → studio.log 가 같은 줄로 차지 않게
_SAVE_FAIL = {}  # 영상 이름 → (마지막으로 남긴 까닭, 시각)


def _save_fail(name, e):
    """편집본 저장 실패 → 쉬운 까닭 (화면에 보냄) · studio.log 에 한 번 (영어 원문은 studio.log 에만)."""
    why = SAVE_WHY.get(trouble.explain(e)["kind"], SAVE_WHY["other"])
    last = _SAVE_FAIL.get(name)
    if not last or last[0] != why or time.time() - last[1] > SAVE_FAIL_EVERY:
        _SAVE_FAIL[name] = (why, time.time())
        log(f"편집본을 저장하지 못했어요 · {name} · {why}")
        studiolog.write(f"  원문 · {type(e).__name__}: {e}")
    return why


CRASH = {}  # 지난번에 편집점 찾기·묶기 중에 갑자기 꺼짐 → 보관함 카드 '○○ 영상' + [다시 하기]·[빠르게로 다시 하기] (/api/state · D-072)
CRASH_RETRY = ("/api/analyze", "/api/bundle", autorun.PATH)  # 보관함에서 같은 영상으로 다시 할 수 있는 작업 (끝까지 자동으로는 [이어서 하기] · D-202)


def _session_start():
    """포트를 잡은 뒤 한 번: 잡히지 않은 오류도 studio.log 에 위치를 남기게 하고, 지난번에 정상적으로 꺼지지 않았으면 기록
    (작업 중에 꺼졌으면 화면에도 한 번 알림 · 보관함에서 다시 할 수 있는 작업이면 그 영상과 [다시 하기]를 보관함 카드로)."""
    studiolog.install_hooks()
    note = studiolog.session_start(core.VERSION)  # 보통 종료(브라우저로 쓰다 Ctrl+C 등)도 표시를 지우게 atexit 에 걸어 둠
    if note:
        log(note["log"])
        crash = note.get("crash") or {}
        if crash.get("path") in CRASH_RETRY and crash.get("names"):
            CRASH.clear()
            CRASH.update(crash)
        elif note["notice"]:
            updater._NOTICES.append({"text": note["notice"], "warn": True})


APP_NAME = "풋살사관학교 스튜디오"


def ensure_shortcut():
    """Mac: 응용 프로그램에 앱을 만든다 (Windows 바로가기·작업 표시줄은 winlink.prepare)."""
    try:
        if sys.platform == "darwin":
            app = Path.home() / "Applications" / f"{APP_NAME}.app"
            macos = app / "Contents" / "MacOS"
            res = app / "Contents" / "Resources"
            macos.mkdir(parents=True, exist_ok=True)
            res.mkdir(parents=True, exist_ok=True)
            launcher = macos / "launcher"
            launcher.write_text(f'#!/bin/bash\ncd "{core.APP_DIR}"\nexec "{sys.executable}" "{Path(__file__).resolve()}"\n', encoding="utf-8")
            launcher.chmod(0o755)
            (app / "Contents" / "Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>{APP_NAME}</string>
<key>CFBundleDisplayName</key><string>{APP_NAME}</string>
<key>CFBundleIdentifier</key><string>kr.futsalacademy.studio</string>
<key>CFBundleExecutable</key><string>launcher</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>{core.VERSION}</string>
</dict></plist>""", encoding="utf-8")
            if not (res / "icon.icns").exists():
                iconset = res / "icon.iconset"
                iconset.mkdir(exist_ok=True)
                for sz in (16, 32, 128, 256, 512):
                    for scale, suffix in ((1, ""), (2, "@2x")):
                        subprocess.run(["sips", "-z", str(sz * scale), str(sz * scale), str(core.APP_DIR / "icon.png"),
                                        "--out", str(iconset / f"icon_{sz}x{sz}{suffix}.png")], capture_output=True)
                subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(res / "icon.icns")], capture_output=True)
                subprocess.run(["rm", "-rf", str(iconset)])
    except Exception as e:
        log(f"바로가기를 만들지 못했어요 · {e}")


class Handler(BaseHTTPRequestHandler):
    timeout = 120  # 멈춘(일시 정지한) 영상 미리보기가 연결을 끝없이 붙잡아 그 파일이 잠긴 채로 남지 않게 (Windows: 열린 파일은 못 지움)

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, bytes):
            data = body
        else:
            try:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            except UnicodeEncodeError:  # 짝 없는 대리 문자가 든 파일 이름 등 → \uXXXX 로 (응답이 끊겨 화면이 멈추지 않게)
                data = json.dumps(body, ensure_ascii=True).encode("ascii")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if ctype.startswith(("application/json", "text/html")):  # 뒤로·앞으로 가기에 캐시해 둔 예전 화면·내용을 쓰지 않게 (D-411)
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except ConnectionError:  # 화면이 먼저 끊음 (Windows: ConnectionAbortedError 10053 도)
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return core.clean_json(json.loads(self.rfile.read(n) or b"{}"))  # 반쪽 이모지('\ud83d')는 여기서 '�'로 (모든 POST)

    def _file(self, path, ctype):
        """영상은 구간 요청(Range)을 지원해야 미리보기에서 앞뒤로 이동 가능.
        받을 수 없는 구간(파일 끝 뒤 · bytes=-0 · 끝이 시작보다 앞)은 416 · 여러 구간·알 수 없는 꼴은 무시하고 전체 (remote._send_range 와 같은 규칙 ·
        예전엔 206 에 길이 0·음수를 보내거나 처리 중 오류로 답이 없었음 · I-363)."""
        size = path.stat().st_size
        rng = (self.headers.get("Range") or "").strip()
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng) if rng and "," not in rng else None
        if m and (m.group(1) or m.group(2)):
            a, b = m.group(1), m.group(2)
            if a:
                start, end = int(a), (min(int(b), size - 1) if b else size - 1)
            else:
                start = size - int(b) if int(b) else size
                start = max(0, start)
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, start + (8 << 20) - 1)  # 8MB씩: 편집실이 영상 여러 개를 열어도 연결이 막히지 않게
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionError, TimeoutError):  # 앞뒤로 옮기면 화면이 연결을 끊음 (Windows 는 ConnectionAbortedError)
                pass

    def _host_ok(self):
        """이 컴퓨터의 앱 창(127.0.0.1)에서 온 요청만 (다른 사이트가 주소를 바꿔 몰래 읽는 것 막기)."""
        return (self.headers.get("Host") or "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _origin_ok(self, origin):
        """POST 의 Origin: 없거나(같은 PC 의 프로그램 · _focus_running) 이 앱 창과 같은 출처만 (다른 사이트의 몰래 요청 막기)."""
        return not origin or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    def _seen(self, method, path):
        """PC 화면에서 사람이 무언가 함 → 자동 업데이트는 그 뒤 AUTO_LOCAL_QUIET 를 기다림 (화면이 저절로 묻는 길 QUIET_REQS ·
        저절로 보낸 표시 AUTO_HEADER 가 붙은 요청은 안 셈 · D-311)."""
        if (method, path) not in QUIET_REQS and self.headers.get(AUTO_HEADER) != "1":
            _SEEN["local"] = time.time()

    def handle_one_request(self):
        """요청 처리 중 잡히지 않은 오류도 studio.log 에 위치(어느 요청인지)를 남김 (pythonw 는 콘솔이 없어 사라졌음).
        traceback 은 studiolog.trace 가 한 번만 오류 출력에 → 서버의 handle_error(_Server)는 같은 오류를 다시 찍지 않음."""
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):  # 화면이 먼저 끊은 연결(미리보기 앞뒤로 옮기기·새로 고침)은 오류가 아님
            raise
        except Exception as e:
            studiolog.trace(e, f"요청 오류 · {getattr(self, 'command', '?')} {urlparse(getattr(self, 'path', '') or '').path}")
            raise

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        self._seen("GET", u.path)
        # 영상 이름을 받는 주소: 파일 이름만 허용 (C:\..., \\서버\... 같은 경로는 거절)
        if u.path in ("/frame", "/api/thumb/open", "/api/edit/open", "/api/edit/autoseq", "/media", "/thumbs.jpg", "/api/timeline", "/api/edit/backups"):
            try:
                n = q["name"][0]
                editor.safe_name(n)
                if u.path != "/api/edit/backups" and u.path != "/thumbs.jpg":
                    editor.video_path(n)
            except (KeyError, ValueError, FileNotFoundError):
                return self._send(404, {"error": "not found"})
        if u.path == "/api/ping":  # 두 번째로 켠 앱이 이 포트의 주인이 이 앱인지 확인 (app._ours · updater._app_running)
            return self._send(200, {"app": APP_ID, "version": core.VERSION})
        if u.path == "/":
            return self._send(200, (core.APP_DIR / "ui.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/thumb":
            return self._send(200, (core.APP_DIR / "thumb.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/frame":
            p = thumb.grab(q["name"][0], float(q["t"][0]))
            return self._file(p, "image/jpeg") if p.exists() else self._send(404, {"error": "not found"})
        if u.path.startswith("/asset/"):
            p = (thumb.ASSETS / Path(u.path).name).resolve()
            ctype = "image/png" if p.suffix == ".png" else "image/jpeg"
            return self._file(p, ctype) if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/api/style/list":
            act = thumbstyle.editor_view().get("active")
            return self._send(200, {"styles": style.list_styles(), "thumbActive": act["name"] if act else "", "rough": style.rough_default() or "",
                                    "shortsTopic": style.shorts_topic_on(), "shortsLayout": style.shorts_layout(),
                                    "cardExtras": style.card_extras()})
        if u.path == "/api/style/shorts_topic":  # '쇼츠 첫머리에 주제 한 줄 넣기' 지금 값 (편집실 '새 쇼츠 편집본으로 추가'가 누를 때 다시 봄 · BR-120)
            return self._send(200, {"on": style.shorts_topic_on()})
        if u.path == "/api/style/shorts_layout":  # '쇼츠 화면 배치' 지금 값 ('' 지금처럼 · eunpyeong 은평 스타일 · BR-240)
            return self._send(200, {"layout": style.shorts_layout()})
        if u.path == "/api/style/card_extras":  # 은평 스타일 덧붙임 (시리즈 이름표·전체 강좌 줄 · BR-245 · BR-246) 지금 값
            return self._send(200, style.card_extras())
        if u.path == "/api/style/thumb_img":  # 썸네일 버릇을 배운 썸네일 (스타일 폴더의 _thumbs/<영상 id>.jpg 만)
            p = thumbstyle.thumb_path((q.get("id") or [""])[0])
            return self._file(p, "image/jpeg") if p and p.is_file() else self._send(404, {"error": "not found"})
        if u.path == "/api/thumb/style":  # 썸네일 편집기: 고른 썸네일 스타일·버릇 + 이 영상의 A/B 묶음
            n = (q.get("name") or [""])[0]
            try:
                n = editor.safe_name(n) if n else ""
            except ValueError:
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            return self._send(200, {"ok": True, "view": thumbstyle.editor_view(), "sets": thumbstyle.ab_sets(n)[:3] if n else []})
        # ---- MSG 자동 편집 스타일 (기본 스타일 · 배운 스타일 · 섞은 스타일 · D-140) ----
        if u.path == "/api/style/presets":
            return self._send(200, msg.sources_listing())
        if u.path == "/api/style/mix":
            try:
                return self._send(200, msg.mix_view(q.get("name", [msg.DRAFT_NAME])[0]))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        # ---- 학습용 영상 (스타일 배우기 전용 · 편집용 보관함과 따로) ----
        if u.path == "/api/refs":
            try:
                return self._send(200, dict(refs.listing(), ok=True))
            except OSError as e:
                return self._send(500, {"ok": False, "error": f"학습용 영상 목록을 읽지 못했어요 · {e}"})
        if u.path == "/api/refs/recommended":
            return self._send(200, refs.recommended())
        if u.path == "/api/browsers":  # 로그인 정보를 읽을 브라우저: 이 PC 에 있는 것 (잘 되는 순서)
            return self._send(200, {"browsers": trouble.installed(), "order": list(trouble.ORDER)})
        # ---- 영상 기획 분석: 클로드 계정 상태 (읽기만 · 60초 기억) · Claude 에게 물어볼 내용 ----
        if u.path == "/api/claude/status":
            return self._send(200, dict(claude_cli.status(refresh=(q.get("refresh") or ["0"])[0] == "1"), **claude_cli.saver()))  # + 아껴 쓰기·오늘 쓴 양
        if u.path == "/api/style/plan_prompt":
            try:
                d = json.loads(style.style_file((q.get("name") or [""])[0]).read_text(encoding="utf-8"))
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except (OSError, ValueError):
                return self._send(500, {"ok": False, "error": "스타일 파일을 읽지 못했어요"})
            if not isinstance(d.get("plan"), dict):
                return self._send(400, {"ok": False, "error": "이 스타일은 아직 기획 분석이 없어요. 먼저 '다시 배우기'를 눌러 주세요"})
            return self._send(200, {"ok": True, "prompt": plan.claude_prompt(d)})
        if u.path == "/api/thumb/brand":  # 브랜드 키트 (로고·색·글꼴·시리즈 이름)
            return self._send(200, {"ok": True, "brand": thumb.load_brand(), "fonts": list(thumb.BRAND_FONTS), "custom": thumb.brand_custom()})
        if u.path == "/api/thumb/open":
            n = q["name"][0]
            rec = editor.recommend(n)
            return self._send(200, {"docs": thumb.load_docs(n), "hooks": [editor._hook(r) for r in rec["shorts"]],
                                    "keywords": sorted({k for r in rec["shorts"] for k in r["keywords"]}),
                                    "title": n, "info": editor.media_info(n),
                                    "cached": thumb.cached_candidates(n) is not None})
        if u.path == "/editor":
            return self._send(200, (core.APP_DIR / "editor.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/media":
            return self._file(editor.video_path(q["name"][0]), "video/mp4")
        if u.path.startswith("/fonts/"):
            p = (editor.FONTS / Path(u.path).name).resolve()
            ok = p.is_file() and p.parent == editor.FONTS.resolve()
            return self._file(p, FONT_TYPES.get(p.suffix.lower(), "application/octet-stream")) if ok else self._send(404, {"error": "not found"})
        if u.path.startswith("/stickers/"):  # 썸네일 스티커 (저장소 stickers/ · 이름만 받고 폴더 밖은 거절)
            p = (thumb.STICKERS / Path(u.path).name).resolve()
            ok = p.is_file() and p.parent == thumb.STICKERS.resolve() and p.suffix.lower() in (".png", ".json")
            return self._file(p, "image/png" if p.suffix.lower() == ".png" else "application/json") if ok else self._send(404, {"error": "not found"})
        if u.path == "/thumbs.jpg":
            p = core.adir(q["name"][0]) / "thumbs2.jpg"
            return self._file(p, "image/jpeg") if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/api/edit/file":  # 편집실 미디어 (보관함 영상·가져온 음악/이미지, proxy=1 이면 미리보기 파일)
            try:
                p = editor.media_path(q["f"][0], q.get("src", ["videos"])[0]).resolve()
            except (KeyError, ValueError):
                return self._send(404, {"error": "not found"})
            if not p.exists() or p.parent not in (core.VIDEOS.resolve(), editor.ASSETS.resolve()):
                return self._send(404, {"error": "not found"})
            lite = (q.get("lite") or [""])[0]
            if lite in editor.LITE_KINDS:  # 밖에서 작업: 가벼운 소리·그림 (원격 화면만 달라고 함 · PC 화면은 원본 · D-363)
                try:
                    lp = editor.lite_file(p.name, q.get("src", ["videos"])[0], lite)
                except (OSError, ValueError):
                    lp = None
                if lp:
                    return self._file(lp, editor.LITE_TYPES[lite])
            if q.get("proxy") and editor.proxy_path(p.name, q.get("src", ["videos"])[0]).exists():
                return self._file(editor.proxy_path(p.name, q.get("src", ["videos"])[0]), "video/mp4")
            return self._file(p, mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        if u.path == "/api/edit/media":
            try:
                return self._send(200, editor.media_bundle(q["f"][0], q.get("src", ["videos"])[0]))
            except (FileNotFoundError, ValueError) as e:
                return self._send(404, {"error": f"파일을 찾을 수 없어요 · {e}"})
            except Exception as e:
                return self._send(500, {"error": f"미디어를 열지 못했어요 · {e}"})
        if u.path in ("/api/edit/thumbs.jpg", "/api/edit/poster.jpg", "/api/edit/revaudio"):
            try:
                f, src = q["f"][0], q.get("src", ["videos"])[0]
                if not editor.media_path(f, src).exists():
                    return self._send(404, {"error": "not found"})
                if u.path == "/api/edit/revaudio":  # 거꾸로 재생 클립 소리 (미리보기)
                    return self._file(editor.rev_audio(src, f, q["a"][0], q["b"][0], q.get("sp", ["1"])[0]), "audio/wav")
                if u.path == "/api/edit/poster.jpg":  # 미디어 탭 목록용 한 장면 (빠름)
                    p = editor.poster(f, src)
                else:
                    p = editor.thumbs_file(f, src)
                    if not p.exists() and Path(f).suffix.lower() not in editor.AUDIO_EXTS:
                        editor.thumbs(f, src)  # 처음 보는 미디어는 그 자리에서 만듦 (키프레임만 읽어 빠름)
            except (KeyError, ValueError, OSError, RuntimeError):
                return self._send(404, {"error": "not found"})
            return self._file(p, "image/jpeg") if p.exists() else self._send(404, {"error": "not found"})
        if u.path == "/api/edit/library":
            return self._send(200, editor.library())
        if u.path == "/api/edit/backups":
            return self._send(200, {"backups": editor.backups(q["name"][0])})
        if u.path == "/api/edit/autoseq":
            n = q["name"][0]
            return self._send(200, {"sequences": editor.auto_sequences(n, editor.media_info(n), editor.default_style_params())})
        if u.path == "/api/edit/open":
            n = q["name"][0]
            try:
                proj = editor.series_view(n, editor.load_project(n))   # 다른 곳에서 정한 시리즈 번호를 이름표에 (BR-245)
                rec = proj.pop("_recovered", None)
                lite = (q.get("lite") or [""])[0]
                if lite in ("opus", "aac"):  # 원격 편집실: 소리·그림 미리보기를 뒤에서 미리 만들어 둠 (D-363)
                    editor.lite_warm([(m.get("src", "assets"), m.get("file"), m.get("kind")) for m in proj.get("media") or []
                                      if m.get("file") and m.get("kind") in ("audio", "image")], {"audio": lite, "image": "webp"})
                return self._send(200, {"project": proj, "waveform": editor.waveform(n), "recovered": rec,
                                        "capLong": editor.long_captions(proj.get("captions"), proj.get("info")),
                                        "capMerged": editor.merged_captions(proj.get("captions")),
                                        "thumbs": editor.thumbs(n), "events": core.timeline_events(n),
                                        "recommend": editor.recommend(n),
                                        "epx": dict(style.card_extras(), next=style.series_peek())})   # 은평 스타일 덧붙임 설정 · 다음 시리즈 번호 (BR-245)
            except Exception as e:
                studiolog.trace(e)
                return self._send(500, {"error": f"편집실을 열지 못했어요 · {e}"})
        if u.path == "/api/state":
            since = int(q.get("since", ["0"])[0])
            try:
                want = int(q.get("job", ["0"])[0])
            except ValueError:
                want = 0
            with LOCK:
                lines = LOG[since:]
                total = len(LOG)
                running = bool(JOB["name"])
                jinfo = {"job_id": JOB["id"] if running else None, "job_by": JOB["by"] if running else None,
                         "done": dict(DONE[want], id=want) if want in DONE else None}  # ?job=<번호>: 그 작업이 끝났으면 결과
            local = source.annotate(core.local_videos())  # 영상마다 출처(풋살사관학교·다른 채널·내 촬영본) + 고르기 칩 개수
            intake.annotate(local, core.VIDEOS, core.adir)  # 복사 중(copying) · 편집점을 찾은 뒤 파일이 바뀜(changed)
            rename.annotate(local)  # 탐색기에서 이름을 바꿔 끊긴 옛 이름 작업 (renamedFrom · [이어 붙이기])
            intake.media(local, core.VIDEOS)  # 해상도·비트레이트(media · 뒤에서 읽어 둠) · 카카오톡 압축 사본 같음(lowq) (D-462)
            return self._send(200, {"version": core.VERSION, "workspace": str(core.WORK), "job": JOB["name"], **jinfo,
                                    "result": JOB["result"] if not JOB["name"] and (not JOB["big"] or q.get("result") == ["1"]) else None,
                                    "resultBig": bool(JOB["big"]) and not JOB["name"],
                                    "error": JOB["error"] if not JOB["name"] else None,
                                    "fail": JOB.get("fail") if not JOB["name"] else None,  # 쉬운 한 줄 + 할 일 (화면의 실패 카드 · 내가 시킨 작업은 done.fail)
                                    "unusable": intake.unusable(core.VIDEOS),  # 아직 못 쓰는 형식 (.MTS 등)
                                    "nested": intake.nested(core.VIDEOS),  # 폴더째 넣은 촬영본: 하위 폴더별 영상 수 → [밖으로 꺼내기] (D-460)
                                    "log": lines, "log_total": total, "progress": dict(core.PROGRESS), "local": local, "sources": source.summary(local),
                                    "remote": remote.SVC.brief(), "crash": dict(CRASH) or None,
                                    "auto": dict(autorun.LIVE) or None,  # 끝까지 자동으로: 지금 단계 (i/n · 이름)
                                    "autoThumb": autorun.pending()})  # 끝까지 자동으로: 화면이 그려 줄 썸네일 요청 (D-201)
        if u.path == "/api/progress":  # 4단계 카드: 영상마다 가편집·내보냄·썸네일·올리기 (D-075 · 그 화면을 열 때만)
            try:
                names = [v["name"] for v in core.local_videos() if v["analyzed"]]
                res = upload.progress(names)
                ups = {x.get("name") for x in youtube_upload._history()}
            except Exception as e:  # noqa: BLE001 — 안내용 · 못 읽으면 카드는 예전처럼
                studiolog.trace(e, "진행 단계 읽기 오류 위치")
                return self._send(200, {"ok": False, "videos": {}})
            for n, r in res.items():
                r["uploaded"] = n in ups
            return self._send(200, {"ok": True, "videos": res})
        if u.path == "/api/remote":  # '실행기' 창 (이 PC 화면에서만 · 터널로는 닿지 않음)
            if not remote.SVC.store:
                return self._send(503, {"error": "실행기를 준비하는 중이에요"})
            st = remote.SVC.pc_status()
            if st["pair"]:
                st["pair"]["qr"] = qr.encode(st["pair"]["link"])
            return self._send(200, st)
        if u.path == "/api/timeline":
            n = q["name"][0]
            return self._send(200, {"text": core.timeline(n), "events": core.timeline_events(n)})
        if u.path == "/api/update/check":
            try:
                return self._send(200, core.check_update())
            except Exception as e:
                return self._send(200, {"current": core.VERSION, "available": False, "note": f"확인 실패: {e}"})
        # ---- 올리기 키트 (제목 후보·설명·챕터·태그) ----
        if u.path == "/api/auto/pending":  # 편집실이 3초마다: 그려 줄 썸네일 요청 (가벼운 물음 · /api/state 대신)
            return self._send(200, {"ok": True, "want": autorun.pending()})
        if u.path == "/api/auto":  # 끝까지 자동으로: 이 영상의 마지막 자동 작업 기록 (이어서 하기 · 결과 · 확인할 것) + 기본 설정
            try:
                n = editor.safe_name(q.get("name", [""])[0])
            except ValueError:
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            return self._send(200, {"ok": True, "run": autorun.view(n), "style": autorun.default_style(), "shortsMax": autorun.SHORTS_MAX,
                                    "shortsDefault": autorun.SHORTS_DEFAULT})
        if u.path == "/api/upload/kit":  # 고를 수 있는 편집본 + 저장해 둔 키트
            try:
                n = q["name"][0]
                editor.video_path(n)
                src = upload.sources(n)
            except (KeyError, ValueError, FileNotFoundError):
                return self._send(404, {"error": "영상을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요"})
            seq = parse_qs(u.query, keep_blank_values=True).get("seq", [src["default"]])[0]
            try:
                kit = upload.load_kit(n, seq, live=True)
            except LookupError:  # 그 사이 편집실에서 지운 편집본
                seq, kit = "", upload.load_kit(n, "", live=True)
            return self._send(200, dict(src, seq=seq, kit=kit))
        if u.path == "/api/upload/thumb":  # 완성본 폴더의 썸네일 미리보기
            try:
                p = core.OUT / editor.safe_name(q["file"][0])
            except (KeyError, ValueError):
                return self._send(404, {"error": "not found"})
            if p.suffix.lower() not in upload.IMG_EXTS or not p.is_file():
                return self._send(404, {"error": "not found"})
            return self._file(p, "image/png" if p.suffix.lower() == ".png" else "image/jpeg")
        # ---- 용어 사전: 받아쓰기에 알려 줄 풋살 용어·이름 + 자주 틀리게 받아쓰는 말 (작업 폴더 dict.json) ----
        if u.path.startswith("/api/strategy"):  # 채널 전략 (8단계 · 읽기만)
            return self._strategy_get(u.path, q)
        if u.path.startswith("/api/youtube/"):  # 유튜브에 바로 올리기 (7단계 · 읽기만 · 인터넷 안 씀)
            return self._youtube_get(u.path, q)
        if u.path == "/api/dict":
            return self._send(200, dict(captions.load_dict(core.dict_path()), defaults=captions.default_dict()))
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # 다른 사이트가 이 로컬 서버를 조작하지 못하게: 같은 출처 요청만 허용
        if not self._host_ok() or not self._origin_ok(self.headers.get("Origin")):
            return self._send(403, {"error": "forbidden"})
        self._seen("POST", urlparse(self.path).path)
        if urlparse(self.path).path == "/api/edit/upload":  # 큰 파일은 JSON 대신 그대로 받음
            try:
                n = parse_qs(urlparse(self.path).query)["name"][0]
                return self._send(200, editor.save_upload(n, self.rfile, int(self.headers.get("Content-Length") or 0)))
            except Exception as e:
                return self._send(400, {"error": str(e)})
        b, path = self._body(), urlparse(self.path).path
        try:  # 영상 이름은 파일 이름만
            for n in ([b["name"]] if isinstance(b.get("name"), str) and path.startswith(("/api/edit/", "/api/thumb/", "/api/render", "/api/auto")) else []) + \
                     (list(b.get("names") or []) if path in ("/api/analyze", "/api/style/learn") else []):
                editor.safe_name(n)
        except ValueError:
            return self._send(400, {"error": "잘못된 파일 이름이에요"})
        try:  # 로그인 정보(쿠키)를 읽을 브라우저: 사용자가 고른 목록 안의 값만 (D-009)
            ck = trouble.browser(b.get("cookies"))
        except ValueError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        if path in ("/api/analyze", "/api/bundle", autorun.PATH):  # 아직 복사 중인 영상은 편집점을 찾지 않음 (앞부분만 받아쓰고 '준비됨'이 붙지 않게)
            busy = self._copying(b.get("names") if path != autorun.PATH else [b.get("name")])  # 이름 검사는 그 전(analyze)·뒤(bundle 경로) 그대로 — 잘못된 이름은 복사 중으로 세지 않음
            if busy:
                why = intake.copying_msg(busy[0]) + (f" (복사 중인 영상 {len(busy)}개)" if len(busy) > 1 else "")  # (msg 는 MSG 모듈 이름이라 쓰지 않음)
                return self._send(409, {"ok": False, "error": why, "copying": busy, "fail": {"kind": "copying", "msg": why, "actions": ["retry"]}})
        jobs = {
            "/api/list": ("채널 불러오기", lambda: source.annotate_listing(hooks.remember_listing(  # 우리 채널이면 제목 패턴용으로 저장 (올리기 키트)
                core.list_videos(b.get("kind", "videos"), ck, b.get("url"), log), b.get("kind", "videos"), b.get("url")), b.get("url"))),
            "/api/style/learn": ("스타일 배우기", lambda: _thumb_habits(style.learn(b.get("name") or "내 스타일", b["names"], log))),
            "/api/style/thumbs": (thumbstyle.JOB, lambda: _style_thumbs(b.get("name"))),
            "/api/download": ("보관함에 담기", lambda: self._download(b, ck)),
            "/api/analyze": ("편집점 찾기", lambda: self._analyze(b)),
            "/api/render": ("러프컷 만들기", lambda: str(core.render(b["name"], b["spec"], log))),
            "/api/update": ("업데이트", lambda: self._update(b)),
            # 학습용 영상: 채널 인기 영상 받기 · 추천 방향 한 번에 · 채널 스타일 다시 배우기 · 지우기 · 배운 파일만 지우기 · 보관함에서 옮기기
            "/api/refs/add": ("학습용 영상 받기", lambda: _refs_job(lambda: _thumb_habits(refs.add_channel(b.get("url"), b.get("count") or 5, b.get("kind") or "videos", log, ck,
                                                                  b.get("learn", True) is not False, bool(b.get("prune")), b.get("style") or None)), ck)),
            "/api/refs/direction": ("학습용 영상 받기", lambda: _refs_job(lambda: _thumb_habits(refs.add_direction(str(b.get("dir") or ""), log, ck, b.get("learn", True) is not False,
                                                                        bool(b.get("prune")))), ck)),
            "/api/refs/learn": ("학습용 스타일 배우기", lambda: _refs_job(lambda: _thumb_habits(refs.learn_channel(str(b.get("channel") or ""), log, bool(b.get("prune")))))),
            "/api/refs/delete": ("학습용 영상 지우기", lambda: _refs_job(lambda: refs.delete(b.get("names"), b.get("channel"), log, bool(b.get("confirmOriginal"))))),
            "/api/refs/prune": ("배운 영상 파일 지우기", lambda: _refs_job(lambda: refs.prune(b["names"] if b.get("names") is not None else refs.channel_names(b["channel"]), log))),
            "/api/refs/restore": ("보관함으로 되돌리기", lambda: _refs_job(lambda: refs.restore(b["names"], log))),
            "/api/refs/move": ("학습용으로 옮기기", lambda: _refs_job(lambda: refs.move_from_library(b["names"], log, bool(b.get("confirm"))))),
            "/api/convert": ("MP4로 바꾸기", lambda: self._convert(b)),
        }
        if path == "/api/convert":  # MP4로 바꾸기: 보관함에 있는 못 쓰는 형식(.MTS 등) 파일 이름만
            try:
                n = editor.safe_name(b.get("name"))
            except (ValueError, TypeError):
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            if n not in {u["name"] for u in intake.unusable(core.VIDEOS)}:
                return self._send(404, {"ok": False, "error": "바꿀 영상을 보관함에서 찾지 못했어요. 목록을 새로 고친 뒤 다시 눌러 주세요"})
        if path in ("/api/refs/delete", "/api/refs/prune", "/api/refs/move", "/api/refs/restore"):  # 이름은 파일 이름만 · 보관함에서 옮길 때 확인이 필요한 영상
            try:
                names = b.get("names")
                if names is not None and not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
                    raise ValueError
                for n in names or []:
                    editor.safe_name(n)
                if path in ("/api/refs/move", "/api/refs/restore") and not names:
                    raise ValueError
                if path == "/api/refs/move":
                    for n in names:
                        editor.video_path(n)
            except (ValueError, TypeError):
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            except FileNotFoundError:
                return self._send(404, {"ok": False, "error": "보관함에서 영상을 찾지 못했어요. 목록을 새로 고친 뒤 다시 골라 주세요"})
            if path == "/api/refs/move" and not b.get("confirm"):
                data = source.load()
                need = [n for n in names if source.describe(n, data, False)["kind"] != "other"]
                if need:
                    return self._send(200, {"ok": False, "confirm": need, "error": None})
            if path == "/api/refs/move":  # 편집실 프로젝트에서 쓰는 영상은 옮기지 않음 → 먼저 알려 줌
                busy = refs.projects_using(names)
                if busy and not b.get("skipInUse"):
                    return self._send(200, {"ok": False, "inUse": busy, "error": None})
            if path == "/api/refs/prune" and not names and not isinstance(b.get("channel"), str):
                return self._send(400, {"ok": False, "error": "파일을 지울 영상이나 채널을 골라 주세요"})
            if path == "/api/refs/delete" and not b.get("confirmOriginal"):  # 원본(다시 받을 수 없음)은 한 번 더 확인
                ch = b.get("channel") if isinstance(b.get("channel"), str) else None
                orig = refs.originals(names if ch is None else None, ch)
                if orig:
                    return self._send(200, {"ok": False, "original": orig, "error": None})
            if path == "/api/refs/delete" and names is None and not isinstance(b.get("channel"), str):
                return self._send(400, {"ok": False, "error": "지울 영상이나 채널을 골라 주세요"})
        if path == "/api/refs/add" and not str(b.get("url") or "").strip():
            return self._send(400, {"ok": False, "error": "채널 주소나 @핸들을 넣어 주세요"})
        if path == "/api/refs/banner":
            try:
                refs.dismiss_banner(b.get("dismiss", True) is not False)
            except OSError:
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True})
        if path == "/api/style/delete":
            f = (style.STYLES / f"{b['name']}.json").resolve()
            try:
                if style.STYLES.resolve() in f.parents and f.exists():
                    f.unlink()
            except OSError as e:  # Windows: 백신·탐색기가 잡고 있음
                log(f"스타일을 지우지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": "스타일을 지우지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True})
        if path == "/api/style/rough":  # 스타일 카드 '첫 가편집에 쓰기' 켜기/끄기 (E2 · BR-094)
            try:
                n = style.set_rough_default(str(b.get("name") or "") or None)
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except OSError:
                return self._send(500, {"ok": False, "error": "설정을 저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True, "rough": n or ""})
        if path == "/api/style/shorts_topic":  # 스타일 화면 '쇼츠 첫머리에 주제 한 줄 넣기' 켜기/끄기 (BR-120)
            try:
                on = style.set_shorts_topic(bool(b.get("on")))
            except OSError:
                return self._send(500, {"ok": False, "error": "설정을 저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True, "on": on})
        if path == "/api/style/shorts_layout":  # 스타일 화면 '쇼츠 화면 배치' 고르기 (BR-240 · 다음에 만드는 쇼츠부터)
            try:
                v = style.set_shorts_layout(b.get("layout"))
            except ValueError:
                return self._send(400, {"ok": False, "error": "모르는 화면 배치예요"})
            except OSError:
                return self._send(500, {"ok": False, "error": "설정을 저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True, "layout": v})
        if path == "/api/style/card_extras":  # 스타일 화면 시리즈 이름표·전체 강좌 줄 (준 값만 바꿈 · 다음에 만드는 은평 스타일 쇼츠부터)
            try:
                v = style.set_card_extras(b.get("series") if isinstance(b.get("series"), bool) else None,
                                          b.get("seriesName") if isinstance(b.get("seriesName"), str) else None,
                                          b.get("longLine") if isinstance(b.get("longLine"), bool) else None)
            except OSError:
                return self._send(500, {"ok": False, "error": "설정을 저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, dict(v, ok=True))
        # ---- 컷 리듬 맞추기 (#7): 스타일 카드의 '말 빠르기 맞추기' 켜기/끄기 (스타일 파일에 저장) ----
        if path == "/api/style/tempo":
            try:
                style.set_tempo(str(b.get("name") or ""), bool(b.get("on")))
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except (OSError, ValueError):
                return self._send(500, {"ok": False, "error": "설정을 저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True})
        # ---- 스타일 일치 점수: 배운 스타일로 만든 자동 가편집이 그 스타일과 얼마나 닮았는지 (내보내지 않고 바로) ----
        # 원본 화면을 처음 보는 영상이면 작업으로 한 번 살펴본 뒤 매김 (결과는 작업 결과로) · 안내는 한국어만 화면에
        if path == "/api/style/score":
            sname, vname = str(b.get("style") or ""), str(b.get("name") or "")
            fail = "점수를 매기지 못했어요. 왼쪽 아래 '작업 기록 보기'에서 내용을 확인해 주세요"

            def do_score():
                try:
                    return dict(style.score_video(sname, vname, log, analyze=True), ok=True)
                except style.StyleError as e:
                    return {"ok": False, "error": str(e)}
                except Exception as e:  # noqa: BLE001
                    studiolog.trace(e)
                    log(f"점수를 매기지 못했어요 · {e}")
                    return {"ok": False, "error": fail}
            try:
                return self._send(200, dict(style.score_video(sname, vname), ok=True))
            except style.NeedsAnalysis:
                ok = start_job("스타일 일치 점수", do_score)
                return self._send(200 if ok else 409, dict(_started(ok), job=bool(ok)))
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except style.StyleError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                studiolog.trace(e)
                log(f"점수를 매기지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": fail})
        if path == "/api/thumb/frames":
            c = thumb.cached_candidates(b["name"])  # 이미 골라 둔 장면이 있으면 다른 작업 중이어도 바로 돌려줌
            if c is not None:
                return self._send(200, {"ok": True, "frames": c})
            ok = start_job("장면 고르기", lambda: thumb.frame_candidates(b["name"]))
            return self._send(200 if ok else 409, _started(ok))
        if path == "/api/thumb/cut":
            try:  # 장면 주소의 영상 이름도 파일 이름만 · 올린 그림은 썸네일 그림 폴더 안만 (I-021)
                src = str(b.get("src") or "")
                if src.startswith("/frame"):
                    qq = parse_qs(urlparse(src).query)
                    cut_t = float(qq["t"][0])  # 잘못된 시각은 400 (영상 찾기보다 먼저)
                    cut_name = editor.video_path(qq["name"][0]).name  # GET /frame 과 같게: 보관함 안의 파일 이름만 (I-021)
                else:
                    sp = (thumb.ASSETS / Path(urlparse(src).path).name).resolve()
                    if sp.parent != thumb.ASSETS.resolve() or not sp.is_file():
                        raise FileNotFoundError(src)
                if b.get("kind", "hq") not in thumb.BG_MODELS:
                    raise ValueError("kind")
            except (KeyError, IndexError, ValueError, TypeError):
                return self._send(400, {"ok": False, "error": "잘못된 그림 주소예요"})
            except FileNotFoundError:
                return self._send(404, {"ok": False, "error": "그림을 찾지 못했어요"})

            def do_cut():
                sp2 = thumb.grab(cut_name, cut_t, thumb.WORK_W) if src.startswith("/frame") else sp  # 누끼는 1920 으로 (메모리)
                # 따로 프로세스에서 (끝나면 메모리 반환 · 죽어도 앱은 그대로 · 메모리가 모자라면 빠른 누끼)
                out, used, note = cutout_worker.remove_bg(sp2, b.get("kind", "hq"), editor.CANCEL, editor._PROCS, log)
                log(f"  누끼 완료 · {'고품질' if used == 'hq' else '빠른'} 모델{' · ' + note if note else ''}")
                return {"cut": thumb.asset_url(out), "src": src, "kind": used, "note": note}
            ok = start_job("누끼 따기", do_cut)
            return self._send(200 if ok else 409, _started(ok))
        # ---- AI 추천 썸네일: 장면·선수 후보 + 자동 누끼 + 문구 (다 돼 있으면 바로) ----
        if path in ("/api/thumb/analyze", "/api/thumb/copy"):
            try:
                n = editor.safe_name(b.get("name"))
                editor.video_path(n)
            except (ValueError, TypeError, FileNotFoundError):
                return self._send(404, {"ok": False, "error": "영상을 찾지 못했어요"})
            if path == "/api/thumb/analyze":
                c = thumb.cached_analysis(n)
                if c is None:  # 끝까지 자동으로가 그 영상 썸네일을 그리는 중: 막 한 분석 (그 작업이 작업 자리를 잡고 있어 새 분석을 못 띄움 · D-207)
                    c = autorun.analysis_for(n)
                if c is not None:
                    return self._send(200, dict(c, ok=True))
                ok = start_job(thumb.JOB_ANALYZE, lambda: thumb.analyze(n, log))
                return self._send(200 if ok else 409, dict(_started(ok), job=bool(ok)))
            if not b.get("ai"):
                return self._send(200, dict(thumbcopy.suggest(n), ok=True))
            ok = start_job(thumbcopy.JOB_AI, lambda: thumbcopy.run_ai(n, log, editor.CANCEL))
            return self._send(200 if ok else 409, dict(_started(ok), job=bool(ok)))
        if path == "/api/thumb/brand":
            try:
                return self._send(200, {"ok": True, "brand": thumb.save_brand(b.get("brand"))})
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError:
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
        if path == "/api/thumb/ocr":  # 검수: 작게 줄인 썸네일에서 읽히는 글자 (글자 읽기 모델이 있을 때만)
            data = b.get("data")
            if not isinstance(data, str) or not data.startswith("data:image/") or len(data) > thumb.OCR_MAX:
                return self._send(400, {"ok": False, "error": "그림이 너무 크거나 형식이 달라요"})
            try:
                lines = thumb.read_text(data)
            except Exception as e:  # noqa: BLE001 — 검수 보조 기능이라 실패해도 화면은 계속 (원문·위치는 studio.log 에만)
                studiolog.trace(e)
                return self._send(200, {"ok": False, "error": f"글자를 읽지 못했어요 · {trouble.explain(e)['msg']}"})
            if lines is None:
                return self._send(200, {"ok": False, "error": "글자 읽기 모델이 아직 없어요 (스타일 배우기를 한 번 하면 생겨요)"})
            return self._send(200, {"ok": True, "lines": lines})
        if path in ("/api/thumb/ab", "/api/thumb/export", "/api/thumb/save"):
            try:  # 이름을 바꿨거나 보관함에서 빠진 영상이면 옛 이름으로 열린 썸네일 창이 저장·내보내기를 못 함 (편집실 저장과 같게 · D-073 · D-078) —
                editor.video_path(b.get("name"))  # 옛 이름 디자인을 새로 만들거나 새 이름 '썸네일 ✓'에 안 잡히는 옛 이름 그림을 완성본 폴더에 쓰지 않게
            except (ValueError, TypeError, FileNotFoundError):
                return self._send(404, {"ok": False, "gone": True, "error": GONE_MSG})
        if path == "/api/thumb/ab":  # A/B 묶음 (썸네일 2~6장 + 모바일 비교 한 장)
            try:
                files = thumb.export_ab(b["name"], b.get("items"), b.get("mobile"))
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError as e:  # Windows 잠금·디스크 가득: 연결이 끊기지 않고 안내 (썸네일 저장과 같게)
                log(f"A/B 썸네일을 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"저장하지 못했어요. 잠시 뒤 다시 눌러 주세요 · {trouble.explain(e)['msg']}"})
            log(f"A/B 썸네일 저장 · {', '.join(files)}")
            try:  # 장마다 틀·색·문구 틀 (나중에 이긴 장을 누르면 가산점 · D-131) — 못 적어도 그림 저장은 그대로
                thumbstyle.record_ab(b["name"], files[:len(b.get("items") or [])], b.get("metas"))  # 모바일 비교 한 장은 빼고
            except OSError as e:
                log(f"A/B 기록을 적지 못했어요 · {e}")
            return self._send(200, {"ok": True, "files": files})
        if path == "/api/thumb/style":  # 썸네일에 쓸 스타일 고르기 ('' = 기본 · '__ours__' = 우리 채널 A/B 이긴 것)
            try:
                thumbstyle.set_pick(b.get("style"))
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except OSError:
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(200, {"ok": True, "view": thumbstyle.editor_view()})
        if path == "/api/thumb/ab_win":  # YouTube '테스트 및 비교'에서 이긴 장 적기 (한 번 누름 · 같은 것을 다시 누르면 지움)
            try:
                thumbstyle.set_winner(b.get("id"), b.get("tag"))
            except thumbstyle.ThumbStyleError as e:
                return self._send(404, {"ok": False, "error": str(e)})
            except OSError:
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            n = b.get("name") if isinstance(b.get("name"), str) else None
            return self._send(200, {"ok": True, "view": thumbstyle.editor_view(), "sets": thumbstyle.ab_sets(n)[:3] if n else []})
        if path == "/api/thumb/judge":  # 검수 창: 내 클로드 계정으로 평가 (선택)
            sm, fu = b.get("small"), b.get("full")
            if not all(isinstance(x, str) and x.startswith("data:image/") and len(x) < thumb.AB_MAX for x in (sm, fu)):
                return self._send(400, {"ok": False, "error": "그림 형식이 달라요"})
            ok = start_job(thumbcopy.JOB_JUDGE, lambda: thumbcopy.judge(sm, fu, editor.CANCEL))
            return self._send(200 if ok else 409, dict(_started(ok), job=bool(ok)))
        if path in ("/api/thumb/upload", "/api/thumb/export"):  # Windows 잠금·디스크 가득이면 연결이 끊기지 않고 안내
            try:
                if path == "/api/thumb/upload":
                    ext = "png" if b["data"].startswith("data:image/png") else "jpg"
                    return self._send(200, {"ok": True, "url": thumb.asset_url(thumb.save_upload(b["data"], ext))})
                out = thumb.export_image(b["name"], b["data"], b.get("fmt", "jpg"), b.get("label", "썸네일"))
            except (OSError, ValueError) as e:
                log(f"썸네일 그림을 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"저장하지 못했어요. 잠시 뒤 다시 눌러 주세요 · {e}"})
            log(f"썸네일 저장 · {out.name}")
            return self._send(200, {"ok": True, "file": out.name})
        if path == "/api/thumb/save":
            try:  # rev: 썸네일 창이 받은 판 번호 → 그 사이 다른 창이 저장했으면 덮어쓰지 않고 알려 줌 (편집실과 같게 · D-411)
                rev = thumb.save_docs(b["name"], b["docs"], b.get("client"), bool(b.get("force")))
            except thumb.Conflict as e:
                return self._send(409, {"ok": False, "conflict": True, "rev": e.rev, "error": str(e)})
            except (OSError, ValueError, TypeError) as e:  # 응답 없이 끊기면 화면이 '저장 중…'에 멈추고 바뀐 디자인이 사라짐
                log(f"썸네일 디자인을 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": "저장하지 못했어요 · 잠시 뒤 다시 저장할게요"})
            return self._send(200, {"ok": True, "rev": rev or None})
        if path == "/api/edit/save":
            try:  # 이름을 바꿨거나 보관함에서 빠진 영상이면 옛 이름 편집본을 새로 만들지 않음 (이름 바꾸기 D-073)
                editor.video_path(b.get("name"))
            except (ValueError, TypeError, FileNotFoundError):
                return self._send(404, {"ok": False, "gone": True, "error": GONE_MSG})
            merged = {}  # 그 사이 끝까지 자동으로가 더한 편집본·미디어(·자막) — 이 창 내용에 합쳐 저장하고 창에도 알려 줌 (D-207)
            client = b.get("client") if b.get("client") != editor.AUTO_CLIENT else None  # '자동' 저장 표시는 작업만 씀
            try:  # rev: 편집실이 받은 판 번호 → 그 사이 다른 창이 저장했으면 덮어쓰지 않고 알려 줌
                rev = editor.save_project(b["name"], b["project"], b.get("rev"), bool(b.get("force")), client, b.get("seq"), merged=merged)
            except editor.Conflict as e:
                return self._send(409, {"ok": False, "conflict": True, "rev": e.rev, "error": str(e)})
            except OSError as e:  # 디스크 가득·OneDrive/백신 잠금 등: 쉬운 까닭을 화면(머리줄 · 나가기 창)에 · studio.log 에 기록 (D-412)
                why = _save_fail(b["name"], e)
                return self._send(500, {"ok": False, "error": f"저장하지 못했어요 · {why}", "why": why})
            except ValueError as e:
                return self._send(500, {"ok": False, "error": f"저장하지 못했어요 · {e}"})
            if _SAVE_FAIL.pop(b["name"], None):
                log(f"편집본을 다시 저장했어요 · {b['name']}")
            return self._send(200, dict({"ok": True, "rev": rev}, **({"autoAdded": merged} if merged else {})))
        if path == "/api/edit/rescue":  # 저장이 계속 안 될 때 '편집본 파일로 내려받기': 창 내용을 자동 백업·다운로드 폴더에 따로 (D-412)
            try:
                files = editor.rescue_copy(b.get("name"), b.get("project"))
            except (ValueError, TypeError) as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError as e:
                why = SAVE_WHY.get(trouble.explain(e)["kind"], SAVE_WHY["other"])
                studiolog.write(f"저장하지 못한 편집본을 따로 남기지도 못했어요 · {type(e).__name__}: {e}")
                return self._send(500, {"ok": False, "error": f"따로 남기지도 못했어요 · {why}"})
            where = ["편집실 '기록' 탭 자동 백업" if f.parent.name == "backup" else "다운로드 폴더" if f.parent.name == "Downloads" else "완성본 폴더" for f in files]
            log(f"저장하지 못한 편집본을 따로 남겼어요 · {b.get('name')} · " + ", ".join(f"{w}의 {f.name}" for w, f in zip(where, files)))
            shown = next((f for f in files if f.parent.name != "backup"), None)
            if shown and not getattr(self, "_studio_req", False):  # 밖에서 작업은 그 기기가 내려받음 (PC 탐색기를 열지 않음)
                try:
                    reveal(shown)
                except OSError:
                    pass  # 탐색기를 못 열어도 파일은 남음 (곁가지)
            return self._send(200, {"ok": True, "where": where, "file": shown.name if shown else files[0].name})
        if path == "/api/edit/oneline":  # 편집실 '자막 한 줄씩 나누기' (지금 글 그대로 나눔 · 저장은 편집실이 함)
            caps = b.get("captions")
            if not isinstance(caps, list):
                return self._send(400, {"error": "자막 목록이 아니에요"})
            info = b.get("info") if isinstance(b.get("info"), dict) else editor.media_info(b["name"])
            src_of = {}  # 나뉜 조각(새 id) → 원래 자막 id ('이 자막만' 자리를 조각마다 · D-321)
            try:
                out, n = editor.oneline_captions(caps, info, editor.silences_of(b["name"]), src_of=src_of)
            except (TypeError, ValueError, KeyError) as e:
                return self._send(400, {"error": f"자막을 나누지 못했어요 · {e}"})
            return self._send(200, {"captions": out, "split": n, "from": src_of})
        if path == "/api/edit/resplit":  # 편집실 '자막 다시 나누기' (이 편집본에 나오는 자막을 새 규칙으로 · 고친 글 그대로 · 저장은 편집실이 함 · D-380)
            caps, ids = b.get("captions"), b.get("ids")
            if not isinstance(caps, list) or (ids is not None and not isinstance(ids, list)):
                return self._send(400, {"error": "자막 목록이 아니에요"})
            info = b.get("info") if isinstance(b.get("info"), dict) else editor.media_info(b["name"])
            src_of = {}
            try:
                try:
                    ends = editor._seg_end_times(editor._segments_of(b["name"]))
                except Exception:  # noqa: BLE001 — 받아쓰기를 못 읽어도 자막 단어 시각만으로 나눔
                    ends = set()
                out, n = editor.resplit_captions(caps, info, ids, src_of=src_of, seg_ends=ends)
            except (TypeError, ValueError, KeyError) as e:
                return self._send(400, {"error": f"자막을 다시 나누지 못했어요 · {e}"})
            return self._send(200, {"captions": out, "changed": n, "from": src_of})
        if path == "/api/edit/export":
            ser, pj = None, b.get("project")
            snap = json.loads(json.dumps(pj)) if isinstance(pj, dict) else None   # 작업이 고치기 전 그대로 (번호 셈에만)
            ok = start_job("내보내기", lambda: editor.export(b["name"], b["project"], b.get("opts", {}), log))
            if ok and snap is not None:
                try:  # 시리즈 이름표 번호를 편집실에 돌려줌 (화면 이름표도 같은 번호 · BR-245) — 작업을 시작했을 때만 정함 (못 시작하면 번호를 안 씀 ·
                    # 작업 안 editor.export 도 같은 편집본·같은 글로 정하므로 같은 번호 · 먼저 정한 쪽을 다른 쪽이 그대로 받음)
                    editor.safe_name(b["name"])
                    ser = editor.series_apply(b["name"], snap)
                except (KeyError, ValueError, TypeError, OSError):
                    ser = None
            return self._send(200 if ok else 409, dict(_started(ok), **({"series": ser} if ser else {})))
        if path == "/api/edit/srt":  # 자막 파일(SRT) 가져오기: 글자만 읽어 돌려줌 (어디에·어느 트랙에 놓을지는 편집실이 정하고 저장도 편집실이 함 · D-345)
            try:
                raw = base64.b64decode(str(b.get("data") or ""), validate=True)
            except (ValueError, TypeError):
                return self._send(400, {"ok": False, "error": "자막 파일을 읽지 못했어요"})
            try:
                return self._send(200, dict(editor.srt_import(raw, b.get("fps") or 30), ok=True))
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
        if path == "/api/edit/frame":  # 지금 화면 그림으로 저장 (Ctrl+Shift+E · 한 프레임이라 작업 줄 없이 바로 · D-349)
            try:
                return self._send(200, dict(editor.export_frame(b["name"], b["project"], float(b.get("t") or 0), log), ok=True))
            except (KeyError, TypeError, ValueError) as e:
                return self._send(400, {"ok": False, "error": f"그림으로 저장하지 못했어요 · {e}"})
            except RuntimeError as e:
                return self._send(409 if "저장하는 중" in str(e) else 500, {"ok": False, "error": str(e)[-300:]})
            except Exception as e:  # noqa: BLE001 — 예상 못 한 오류도 화면에 쉬운 말로 (위치는 studio.log)
                studiolog.trace(e, "지금 화면 저장 오류 위치")
                return self._send(500, {"ok": False, "error": "그림으로 저장하지 못했어요 · 잠시 뒤 다시 눌러 주세요"})
        if path in ("/api/edit/restore", "/api/edit/freeze"):
            try:
                if path == "/api/edit/restore":
                    return self._send(200, {"project": editor.restore_backup(b["name"], b["file"])})
                return self._send(200, editor.freeze_frame(b.get("src", "videos"), b["file"], float(b["t"])))
            except Exception as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/edit/track":  # 전술 그림 '선수 따라가기' (몇 초 · 한 번에 하나 · 결과는 원본 기준 선수 상자들)
            try:
                f = editor.media_path(b.get("file"), b.get("src", "videos"))
                if not f.is_file():
                    raise FileNotFoundError("영상을 찾지 못했어요")
                return self._send(200, tactic.track(f, b.get("t0"), b.get("t1"), b.get("x"), b.get("y"), log=log, kind=b.get("kind")))
            except tactic.Busy as e:
                return self._send(409, {"ok": False, "error": str(e)})
            except (ValueError, TypeError, LookupError, FileNotFoundError) as e:
                return self._send(400, {"ok": False, "error": str(e) or "따라가지 못했어요"})
            except RuntimeError as e:
                return self._send(500, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001 — 예상 못 한 오류도 화면에 쉬운 말로 (위치는 studio.log)
                studiolog.trace(e, "선수 따라가기 오류 위치")
                return self._send(500, {"ok": False, "error": "선수를 따라가지 못했어요 · 잠시 뒤 다시 눌러 주세요"})
        if path == "/api/edit/qa":  # 내보낸 영상 자동 검수
            f = (core.OUT / Path(b["file"]).name).resolve()
            if core.OUT.resolve() not in f.parents or not f.exists():
                return self._send(404, {"ok": False, "error": "검수할 파일을 찾지 못했어요"})
            meta = editor.EXPORT_META.get(f.name) or {}  # 내보낼 때 기록한 설정이 먼저 (편집실을 새로 열면 화면 쪽 기록이 없음)
            ok = start_job("영상 검수", lambda: qa.check_video(f, meta.get("format") or b.get("format"), meta.get("master") or b.get("master"),
                                                             editor.run_killable))
            return self._send(200 if ok else 409, _started(ok))
        if path == "/api/edit/proxy":
            ok = start_job("미리보기 파일 만들기", lambda: editor.make_proxy(b["file"], b.get("src", "videos"), log))
            return self._send(200 if ok else 409, _started(ok))
        # ---- 영상 기획 분석: 클로드 계정으로 쓰기 (사용자 PC 의 Claude Code CLI) · Claude 대답 붙여 넣기 ----
        if path == "/api/claude/save":  # 클로드 아껴 쓰기 켬/끔 (D-390 · 사용자 폴더 claude_prefs.json)
            if not isinstance(b.get("on"), bool):
                return self._send(400, {"ok": False, "error": "켬/끔 값이 달라요"})
            try:
                claude_cli.set_save(b["on"])
            except OSError:
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            log("클로드 아껴 쓰기를 " + ("켰어요" if b["on"] else "껐어요 (예전처럼 모두 Opus)"))
            return self._send(200, dict(claude_cli.saver(), ok=True))
        if path in ("/api/claude/install", "/api/claude/login", "/api/claude/token"):
            try:
                if path == "/api/claude/install":
                    claude_cli.install()
                elif path == "/api/claude/login":
                    claude_cli.login()
                elif b.get("clear"):
                    claude_cli.clear_token()
                else:
                    claude_cli.save_token(b.get("token"))
            except claude_cli.ClaudeError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError:
                return self._send(500, {"ok": False, "error": "창을 띄우지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            log({"/api/claude/install": "클로드 설치 창을 띄웠어요", "/api/claude/login": "클로드 로그인 창을 띄웠어요",
                 "/api/claude/token": "클로드 로그인 코드를 " + ("지웠어요" if b.get("clear") else "저장했어요")}[path])
            return self._send(200, {"ok": True})
        if path in ("/api/style/plan_ai", "/api/style/plan_paste"):
            sname = str(b.get("name") or "")
            try:
                style.style_file(sname)
            except style.StyleMissing as e:
                return self._send(404, {"ok": False, "error": str(e)})
            if path == "/api/style/plan_paste":
                try:
                    ai = plan.parse_ai(b.get("text"), by="paste")
                    # 스타일 파일을 쓰는 작업(배우기·클로드 판단)이 도는 중이면 그 작업이 끝에 덮어써서 붙여 넣은 대답이 사라짐 → 거절.
                    # 확인과 저장을 작업 잠금 안에서 해서, 저장하는 동안 새 작업이 끼어들지 않게 (저장은 금방 끝남)
                    with LOCK:
                        if JOB["name"] in STYLE_JOBS:
                            return self._send(409, {"ok": False, "busy": True,
                                                    "error": f"지금 '{JOB['name']}' 중이에요. 끝난 뒤에 [저장]을 다시 눌러 주세요 (붙여 넣은 글은 그대로 있어요)"})
                        plan.save_ai(sname, ai)
                except (ValueError, style.StyleError) as e:
                    return self._send(400, {"ok": False, "error": str(e)})
                except OSError:
                    return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
                log(f"Claude 대답을 붙여 넣었어요 · {sname}")
                return self._send(200, {"ok": True, "ai": ai})

            def do_ai():
                try:
                    return plan.run_ai(sname, log, editor.CANCEL)
                except (style.StyleError, OSError, ValueError) as e:
                    return {"ok": False, "error": str(e) or "클로드 판단을 저장하지 못했어요"}
            ok = start_job("클로드로 더 깊게 보기", do_ai)
            return self._send(200 if ok else 409, _started(ok))
        if path == "/api/edit/sfx":  # 미디어 탭 '효과음 꺼내기' (#12 · E7): 실어 둔·만드는 효과음을 모두 편집실 미디어 폴더에 → '가져온 파일'에 보임
            import sfxlib
            try:
                files = sfxlib.ensure_assets(list(sfxlib.CATALOG), editor.ASSETS)
            except (OSError, RuntimeError) as e:
                return self._send(500, {"ok": False, "error": f"효과음을 꺼내지 못했어요 · {e}"})
            return self._send(200, {"ok": True, "files": sorted(files.values())})
        if path == "/api/edit/cancel":
            editor.cancel_export()
            return self._send(200, {"ok": True})
        if path == "/api/edit/msg":  # MSG 후보 만들기 (작업 · 결과는 새 편집본 후보 + 효과음·배경음악·정지 화면 미디어)
            try:
                editor.video_path(b["name"])
                specs = [x for x in (b.get("styles") or []) if isinstance(x, dict)][:3]
                kinds = tuple(k for k in (b.get("kinds") or ["long"]) if k in ("long", "shorts")) or ("long",)
                inten = b.get("intensity") if b.get("intensity") in msg.INTENSITY or b.get("intensity") == "모두" else "보통"
                if not specs:
                    raise ValueError("스타일을 하나 이상 골라 주세요")
                first = max(0, int(b.get("first") or 0))
            except (KeyError, ValueError, TypeError, FileNotFoundError) as e:
                return self._send(400, {"ok": False, "error": str(e) or "잘못된 요청이에요"})
            proof = b.get("proofread") is True  # '클로드로 자막 오타 고치기'를 켰을 때만 (사용자 클로드 계정으로 대사 글만 보냄)
            writer = b.get("writer") is True   # '클로드로 재미 자막 쓰기'를 켰을 때만 (같은 계정 · 대사 글만)
            ok = start_job("MSG 후보 만들기", lambda: msg.build_variants(b["name"], specs, inten, kinds, log, proofread=proof, writer=writer, first=first))
            return self._send(200 if ok else 409, {"ok": ok, "error": None if ok else "다른 작업이 끝난 뒤에 다시 눌러 주세요"})
        if path in ("/api/style/mix", "/api/style/mix_pick"):  # 스타일 섞기 저장 · '이 후보의 ○○가 좋아요'
            try:
                nm = str(b.get("name") or msg.DRAFT_NAME)
                if path == "/api/style/mix":
                    msg.save_mix(nm, b.get("aspects") if isinstance(b.get("aspects"), dict) else None, b.get("intensity"))
                else:
                    msg.save_mix(nm, pick=(b.get("aspect"), b.get("source")))
                return self._send(200, dict(msg.mix_view(nm), ok=True))
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError:
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
        if path == "/api/edit/autoseq":  # 배운 스타일로 자동 가편집 (새 편집본으로 추가)
            n, sname = b["name"], b.get("style")
            st = next((x for x in style.list_styles() if x["name"] == sname), None)
            if sname and not st:
                return self._send(404, {"error": "스타일을 찾지 못했어요"})
            kinds = tuple(b.get("kinds") or ("long", "shorts"))
            seqs = editor.auto_sequences(n, editor.media_info(n), st["params"] if st else None, kinds)
            for q in seqs:
                q["name"] = f"{sname} 스타일 가편집" + ("" if q["format"] == "long" else " · " + q["name"]) if sname else q["name"]
            return self._send(200, {"sequences": seqs, "params": st["params"] if st else None})
        if path == "/api/focus":  # 이미 켜진 앱을 다시 실행하면 그 창을 앞으로
            if _BROWSER:  # 전용 창 없이 브라우저로 쓰는 중 → 화면을 한 번 더 열어 줌 (새 프로세스는 그대로 끝남)
                webbrowser.open(f"http://127.0.0.1:{PORT}/")
                return self._send(200, {"ok": True})
            try:
                import webview
                for w in webview.windows:
                    w.restore()
                    w.show()
                    w.on_top = True
                    w.on_top = False
            except Exception:
                return self._send(200, {"ok": False})
            return self._send(200, {"ok": bool(_WINDOW)})
        if path == "/api/open":
            try:
                if b["which"] == "refs":
                    refs.root().mkdir(parents=True, exist_ok=True)
                if b["which"] == "log":  # studio.log 위치 (관리자에게 보낼 파일 · 탐색기에서 골라 보여 줌)
                    reveal(LOGFILE)
                    return self._send(200, {"ok": True})
                if b["which"] == "bgm":  # MSG '내 배경음악' 폴더 (분위기 이름 폴더도 만들어 둠)
                    for m in ("신남", "경쾌", "잔잔", "감성"):
                        (msg.user_bgm_dir() / m).mkdir(parents=True, exist_ok=True)
                open_folder({"videos": core.VIDEOS, "analysis": core.ANALYSIS, "out": core.OUT, "refs": refs.root(), "work": core.WORK, "bgm": msg.user_bgm_dir()}[b["which"]])
            except Exception as e:
                log(f"폴더를 열지 못했어요 · {e}")
                return self._send(200, {"ok": False})
            return self._send(200, {"ok": True})
        # ---- 영상 출처 (풋살사관학교 · 다른 채널 · 내 촬영본): 보관함에서 직접 고르기 · 모르는 영상 뒤에서 찾기 ----
        if path == "/api/source":
            try:
                n = editor.safe_name(b.get("name"))
                editor.video_path(n)
                return self._send(200, {"ok": True, "source": source.set_manual(n, b.get("kind"), b.get("channel"), b.get("channelKey"))})
            except FileNotFoundError:
                return self._send(404, {"ok": False, "error": "보관함에서 영상을 찾지 못했어요. 목록을 새로 고친 뒤 다시 골라 주세요"})
            except (ValueError, TypeError) as e:
                return self._send(400, {"ok": False, "error": str(e) or "잘못된 파일 이름이에요"})
            except OSError as e:
                log(f"영상 출처를 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
        if path == "/api/source/check":  # 작업(start_job)이 아님: 다른 작업을 막지 않고 뒤에서 천천히
            if b.get("stop"):
                source.stop_backfill()
            else:
                source.start_backfill(log)
            return self._send(200, {"ok": True, "running": source.backfill_running()})
        if path == "/api/unnest":  # 폴더째 넣은 촬영본을 보관함 바로 안으로 꺼내기 (D-460) — 이름 바꾸기처럼 작업 자리를 잡고 바로 답함
            sub = b.get("folder")
            if not isinstance(sub, str) or sub not in {r["folder"] for r in intake.nested(core.VIDEOS, fresh=True)}:
                return self._send(404, {"ok": False, "error": "그 폴더를 보관함에서 찾지 못했어요. 목록을 새로 고친 뒤 다시 눌러 주세요"})
            with LOCK:
                busy = bool(JOB["name"]) or RESTARTING.is_set()
                if not busy:
                    JOB["name"] = "폴더 안 영상 꺼내기"
            if busy:  # 편집점 찾기·묶기가 보관함 파일을 쓰는 중일 수 있음
                return self._send(409, {"ok": False, "error": BUSY_MSG})
            try:
                log(f"폴더 안 영상 꺼내기 · {sub}")
                r = intake.unnest(core.VIDEOS, sub, log)
                code, res = 200, dict(r, ok=True)
            except FileNotFoundError as e:
                code, res = 404, {"ok": False, "error": str(e)}
            except OSError as e:
                log(f"폴더 안 영상을 꺼내지 못했어요 · {e}")
                code, res = 500, {"ok": False, "error": "폴더 안 영상을 꺼내지 못했어요. 잠시 뒤 다시 눌러 주세요"}
            finally:
                with LOCK:
                    JOB["name"] = None
            return self._send(code, res)
        if path in ("/api/rename", "/api/rename/attach"):  # 보관함 영상 이름 바꾸기 · 탐색기에서 바꾼 영상에 옛 작업 이어 붙이기 (D-073)
            with LOCK:  # 작업 자리를 잡아 둠 → 바꾸는 동안 휴대폰·다른 창이 옛 이름으로 작업을 시작하지 못하게 (확인만 하고 놓으면 그 틈에 시작됨)
                busy = bool(JOB["name"]) or RESTARTING.is_set()
                if not busy:
                    JOB["name"] = "이름 바꾸기"
            if busy:  # 작업이 그 영상 파일·폴더를 쓰는 중일 수 있음
                return self._send(409, {"ok": False, "error": BUSY_MSG})
            try:
                n = editor.safe_name(b.get("name"))
                r = rename.rename(n, b.get("to"), log) if path == "/api/rename" else rename.attach(n, str(b.get("old") or ""), log)
                code, res = 200, dict(r, ok=True)
            except FileNotFoundError as e:
                code, res = 404, {"ok": False, "error": str(e)}
            except (ValueError, TypeError) as e:  # RenameError 포함 · 잘못된 이름
                code, res = 400, {"ok": False, "error": str(e) or "잘못된 파일 이름이에요"}
            except OSError as e:
                log(f"이름을 바꾸지 못했어요 · {e}")
                code, res = 500, {"ok": False, "error": "이름을 바꾸지 못했어요. 잠시 뒤 다시 눌러 주세요"}
            finally:
                with LOCK:  # 대답 전에 자리를 놓음 (화면이 바로 다음 작업을 시킬 수 있게)
                    JOB["name"] = None
            return self._send(code, res)
        if path == autorun.PATH:  # 끝까지 자동으로 (E7 · D-200): 영상 하나 → 가편집·MSG·내보내기·쇼츠·썸네일·키트·확인할 것 (유튜브엔 안 올림)
            try:
                n = editor.safe_name(b.get("name"))
                editor.video_path(n)
            except (ValueError, TypeError, FileNotFoundError):
                return self._send(404, {"ok": False, "error": "보관함에서 영상을 찾지 못했어요. 목록을 새로 고친 뒤 다시 골라 주세요"})
            opts, resume = autorun.options(b, remember=True), b.get("resume") is True
            ok = start_job(autorun.JOB, lambda: autorun.run(n, opts, log, self._analyze, resume))
            if ok:
                CRASH.clear()
            return self._send(200 if ok else 409, _started(ok))
        if path == "/api/auto/prefs":  # 편집실 MSG 체크(클로드 오타·재미 자막)를 바꿈 → 휴대폰에서 시작한 끝까지 자동으로도 같은 설정
            return self._send(200, {"ok": True, "prefs": autorun.save_prefs({k: b.get(k) for k in ("proofread", "writer")})})
        if path == "/api/auto/claim":  # 썸네일 그리기 요청을 이 화면이 맡음 (먼저 맡은 화면 하나만)
            got = autorun.claim(str(b.get("id") or ""))
            return self._send(200 if got else 409, dict(got or {}, ok=bool(got)))
        if path == "/api/auto/release":  # 맡은 화면이 닫히거나 다른 화면으로 넘어감 (pagehide 신호) → 다른 화면이 다시 맡게 (D-207)
            return self._send(200, {"ok": autorun.release(str(b.get("id") or ""))})
        if path == "/api/auto/thumb":  # 보이지 않는 썸네일 편집기가 그린 그림 (이름은 서버가 정함)
            try:
                f = autorun.deliver(str(b.get("id") or ""), b)
            except LookupError as e:
                return self._send(410, {"ok": False, "error": str(e)})
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError as e:
                return self._send(500, {"ok": False, "error": f"썸네일을 저장하지 못했어요 · {e}"})
            return self._send(200, {"ok": True, "file": f})
        if path == "/api/crash/dismiss":  # 지난번 꺼짐 카드 닫기 (✕)
            CRASH.clear()
            return self._send(200, {"ok": True})
        # ---- 받아쓰기 모델: 이 PC 에 맞는 기본값 · 고른 영상의 예상 시간·메모리 (누르기 전에 · D-070) ----
        if path == "/api/whisper/estimate":
            names = b.get("names") or []
            if not isinstance(names, list) or len(names) > 500 or not all(isinstance(n, str) for n in names):
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            try:
                for n in names:
                    editor.safe_name(n)
            except ValueError:
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            secs, guess = _selected_secs(names)
            return self._send(200, dict(core.whisper_estimate(secs), ok=True, guess=guess))
        # ---- 촬영본 묶음: 여러 파일을 찍은 순서대로 한 영상으로 (원본은 그대로) ----
        if path == "/api/bundle":
            try:
                names = list(dict.fromkeys(str(n) for n in (b.get("names") or [])))
                for n in names:
                    editor.video_path(n)  # 보관함 안의 파일 이름만
            except (ValueError, FileNotFoundError, TypeError):
                return self._send(400, {"ok": False, "error": "보관함에서 찾지 못한 영상이 있어요. 목록을 새로 고친 뒤 다시 골라 주세요"})
            if len(names) < 2:
                return self._send(400, {"ok": False, "error": "묶으려면 영상을 2개 이상 골라 주세요"})

            def run_bundle():
                studiolog.target(path="/api/bundle", names=names, model=core.model_of(b.get("model")), title=str(b.get("title") or ""))
                r = bundle.make_bundle(names, b.get("title"), log)
                # 이어서 편집점 찾기까지 같은 작업 안에서 (그사이 편집실·썸네일에 다녀와도 끊기지 않게)
                try:
                    self._analyze({"names": [r["name"]], "model": core.model_of(b.get("model"))})
                    r["analyzed"] = True
                except Exception as e:
                    studiolog.trace(e)
                    why = trouble.explain(e)["msg"]
                    log(f"묶은 영상은 만들었지만 편집점 찾기는 하지 못했어요 · {why} · 보관함에서 골라 '편집점 찾기'를 다시 눌러 주세요")
                    r["analyze_error"] = why
                return r
            ok = start_job("한 영상으로 묶기", run_bundle)
            if ok:
                CRASH.clear()
            return self._send(200 if ok else 409, _started(ok))
        # ---- 올리기 키트 (제목 후보·설명·챕터·태그) ----
        if path == "/api/upload/kit":  # 만들기 · edits 가 있으면 화면에서 고친 내용 저장
            try:
                n = editor.safe_name(b.get("name"))
                editor.video_path(n)
                e = b.get("edits")
                if isinstance(b.get("guests"), list):  # 출연자·게스트 칸 → 제목 후보·태그·해시태그·설명 출연 줄 (D-085)
                    kit = upload.set_guests(n, b.get("seq") or None, b["guests"])
                elif isinstance(b.get("newTitles"), bool):  # '쇼츠 새 말투·이모지 후보' 칸 → 설정 + 지금 키트 제목 후보 (D-438)
                    kit = upload.set_new_titles(n, b.get("seq") or None, b["newTitles"])
                elif isinstance(b.get("carryFrom"), str):  # 올릴 대상을 바꿀 때 고친 제목·설명 가져오기 (물어본 뒤 · D-452)
                    kit, swapped = upload.carry_edits(n, b["carryFrom"] or None, b.get("seq") or None)
                    return self._send(200, {"ok": True, "kit": kit, "chaptersSwapped": swapped})
                elif isinstance(e, dict):
                    kit = upload.save_edits(n, b.get("seq") or None, e.get("title"), e.get("description"), e.get("tags"))
                else:
                    kit = upload.build_kit(n, b.get("seq") or None)
                    log(f"올리기 키트 · {kit['files']['txt']}")
                return self._send(200, {"ok": True, "kit": kit})
            except (ValueError, FileNotFoundError, LookupError) as e:
                return self._send(400, {"ok": False, "error": str(e) or "영상을 찾지 못했어요"})
            except Exception as e:
                studiolog.trace(e)
                log(f"올리기 키트를 만들지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"올리기 키트를 만들지 못했어요 · {e}"})
        if path == "/api/upload/open":  # 유튜브 스튜디오 · 키트 파일 위치 · 설명 틀
            try:
                if b.get("what") == "studio":  # 우리 채널 업로드 창 (채널을 모르면 스튜디오 첫 화면)
                    return self._send(200, {"ok": True, "channel": upload.open_studio() != upload.STUDIO_URL})
                elif b.get("what") == "template":
                    upload.open_template()
                elif b.get("what") == "file":
                    upload.reveal(core.OUT / editor.safe_name(b.get("file")))
                else:
                    return self._send(400, {"ok": False, "error": "무엇을 열지 모르겠어요"})
            except ValueError:
                return self._send(400, {"ok": False, "error": "잘못된 파일 이름이에요"})
            except Exception as e:
                log(f"열지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"열지 못했어요 · {e}"})
            return self._send(200, {"ok": True})
        # ---- 용어 사전 저장 (다음 '편집점 찾기'부터 받아쓰기에 씀) ----
        if path == "/api/dict":
            try:
                d = captions.save_dict(core.dict_path(), {"terms": b.get("terms", []), "fix": b.get("fix", {})})
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError as e:
                log(f"용어 사전을 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": f"용어 사전을 저장하지 못했어요 · {e}"})
            return self._send(200, dict(d, ok=True))
        if path.startswith("/api/strategy/"):  # 채널 전략 (8단계)
            return self._strategy_post(path, b)
        if path.startswith("/api/remote/"):  # 실행기: ON·OFF·연결·끊기·설정 (PC 화면에서만 · 켜는 길은 여기뿐)
            svc = remote.SVC
            if not svc.store:
                return self._send(503, {"ok": False, "error": "실행기를 준비하는 중이에요"})
            try:
                if path == "/api/remote/on":  # 실행기 ON·OFF = 스위치 (D-310): 지금 + 다음에 스튜디오를 켤 때도 그대로
                    ok = svc.switch(True)
                    return self._send(200 if ok else 400, {"ok": ok, "error": None if ok else svc.error, "state": svc.state})
                if path == "/api/remote/off":
                    svc.switch(False)
                    return self._send(200, {"ok": True})
                if path == "/api/remote/pair":
                    pair = svc.pair_start()
                    pair["qr"] = qr.encode(pair["link"])
                    return self._send(200, {"ok": True, "pair": pair})
                if path == "/api/remote/pair/cancel":
                    svc.pairing.cancel()
                    return self._send(200, {"ok": True})
                if path == "/api/remote/revoke":
                    if b.get("all") is True:
                        n = svc.revoke(everyone=True)
                    elif isinstance(b.get("id"), str):
                        n = svc.revoke([b["id"]])
                    else:
                        return self._send(400, {"ok": False, "error": "끊을 휴대폰을 골라 주세요"})
                    return self._send(200, {"ok": True, "removed": n})
                if path == "/api/remote/settings":
                    return self._send(200, {"ok": True, "settings": svc.set_settings(b)})
                if path == "/api/remote/session/revoke":  # 밖에서 작업 중인 세션 끊기 (기기 열쇠는 그대로 · 기기째 끊기는 /api/remote/revoke)
                    if b.get("all") is True:
                        n = svc.studio_revoke(everyone=True)
                    elif isinstance(b.get("id"), str):
                        n = svc.studio_revoke([b["id"]])
                    else:
                        return self._send(400, {"ok": False, "error": "끊을 연결을 골라 주세요"})
                    return self._send(200, {"ok": True, "ended": n})
                if path == "/api/remote/test":
                    if not svc.store.data["devices"]:
                        return self._send(400, {"ok": False, "error": "먼저 휴대폰을 연결해 주세요"})
                    svc.test_notify()
                    return self._send(200, {"ok": True})
                # 아이디·비밀번호로 연결 (D-250 · 이 PC 화면에서만 · 비밀번호는 검증값으로만 남기고 요청 본문은 기록하지 않음)
                pw = lambda k: b.get(k) if isinstance(b.get(k), str) else None  # noqa: E731
                if path == "/api/remote/login":  # 정하기 · 비밀번호 바꾸기 (아이디를 빼면 그대로)
                    return self._send(200, {"ok": True, "login": svc.login_set(pw("id"), pw("password") or "", pw("password2"),
                                                                                 b.get("revoke") is not False, b.get("force") is True,
                                                                                 b.get("ack") is True, b.get("idOnly") is True)})
                if path == "/api/remote/login/id":
                    return self._send(200, {"ok": True, "login": svc.login_id(pw("id") or "", b.get("force") is True)})
                if path == "/api/remote/login/check":
                    return self._send(200, dict(svc.login_check(pw("id"), pw("password") or ""), ok=True))
                if path == "/api/remote/login/suggest":
                    return self._send(200, dict(svc.login_suggest(pw("id")), ok=True))
                if path == "/api/remote/login/on":
                    return self._send(200, {"ok": True, "login": svc.login_on(b.get("force") is True)})
                if path == "/api/remote/login/off":
                    return self._send(200, {"ok": True, "login": svc.login_off(b.get("revoke") is True)})
                if path == "/api/remote/login/unlock":
                    return self._send(200, {"ok": True, "login": svc.login_unlock()})
                if path == "/api/remote/login/kick":
                    if not isinstance(b.get("ref"), str):
                        return self._send(400, {"ok": False, "error": "끊을 연결을 골라 주세요"})
                    svc.login_kill(ref=b["ref"], where="PC에서")
                    return self._send(200, {"ok": True, "login": svc.login_view()})
            except remote.LoginAsk as e:  # 한 번 더 물어야 함 (휴대폰 없음 · 겹침 확인 못 함)
                return self._send(409, {"ok": False, "code": e.code, "error": str(e)})
            except (remote.PairError, ValueError) as e:
                return self._send(400, {"ok": False, "error": str(e)})
            except OSError as e:
                log(f"실행기 설정을 저장하지 못했어요 · {e}")
                return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
            return self._send(404, {"error": "not found"})
        if path.startswith("/api/youtube/"):  # 유튜브에 바로 올리기 (7단계)
            return self._youtube_post(path, b)
        if path == "/api/restart":
            with LOCK:  # 작업 확인과 '다시 시작' 표시를 한 번에 → 그 뒤로는 휴대폰도 새 작업을 시작하지 못함 (start_job)
                busy = JOB["name"]
                if not busy:
                    RESTARTING.set()
            if busy:  # 업데이트가 끝난 뒤 휴대폰이 시킨 작업 등: 끝까지 하고 다시 시작 (화면이 잠시 뒤 다시 부름 · 끊지 않음)
                return self._send(409, {"ok": False, "busy": True, "error": RESTART_WAIT_MSG})
            self._send(200, {"ok": True})
            threading.Timer(0.5, restart).start()
            return
        if path in jobs:
            if path in ("/api/list", "/api/download", "/api/refs/add", "/api/refs/direction"):
                source.stop_backfill()  # 뒤에서 하던 출처 찾기는 멈춤 (YouTube 에 한꺼번에 묻지 않게 · 다음에 보관함을 열면 이어서)
            name, fn = jobs[path]
            ok = start_job(name, fn, ctx={"browser": ck, "blocked": refs.BLOCKED_MSG if path.startswith("/api/refs/") else None})
            if ok and path in CRASH_RETRY:
                CRASH.clear()  # 다시 하는 중 → 지난번 꺼짐 카드는 내림
            return self._send(200 if ok else 409, _started(ok))
        self._send(404, {"error": "not found"})

    def _strategy_get(self, path, q):
        """채널 전략 읽기 (GET 은 사용자 기록을 바꾸지 않음 · 쓰는 것은 캐시 forecast.json·solution.json 뿐 ·
        가능성은 입력이 같으면 캐시, 처음이면 그 자리에서 1~2초 계산 · 계산은 한 번에 하나)."""
        try:
            if path == "/api/strategy":
                return self._send(200, strategy.overview())
            if path == "/api/strategy/forecast":
                return self._send(200, {"ok": True, "forecast": strategy.forecast_result(), "solution": strategy.solution_view()})
            if path == "/api/strategy/prompt":
                return self._send(200, {"ok": True, "prompt": strategy.claude_prompt()})
            if path == "/api/strategy/todos":
                use = (q.get("use") or [""])[0]
                return self._send(200, {"ok": True, "todos": strategy.todos_for(use if use in ("thumb", "title", "edit", "upload", "plan", "shorts") else "")})
            if path == "/api/strategy/remind":
                return self._send(200, dict(strategy.remind(), ok=True))
            if path == "/api/strategy/preview":  # 저장하지 않은 계획의 가능성 미리 보기 (캐시·저장 없음)
                return self._send(200, dict(strategy.forecast_preview((q.get("L") or ["0"])[0], (q.get("S") or ["0"])[0]), ok=True))
        except strategy.StrategyError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        except strategy.forecast.ForecastError as e:
            return self._send(500, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            studiolog.trace(e)
            log(f"채널 전략을 읽지 못했어요 · {e}")
            return self._send(500, {"ok": False, "error": "채널 전략을 읽지 못했어요. 잠시 뒤 다시 열어 주세요"})
        return self._send(404, {"error": "not found"})

    def _strategy_post(self, path, b):
        """채널 전략 바꾸기·작업 시작 (YouTube 를 쓰는 작업은 뒤에서 하던 출처 찾기를 멈추고 시작).
        작업 시작 응답은 다른 작업과 같은 _started (jobId → 화면이 자기가 시킨 작업의 끝만 받음). 휴대폰에서는 시작할 수 없다 (D-028)."""
        try:
            if path == "/api/strategy/draft":  # 저장 안 한 '우리 전략' 초안 (고칠 때마다 · 떠날 때 sendBeacon · D-087)
                if b.get("clear"):
                    strategy.clear_draft()
                    return self._send(200, {"ok": True})
                return self._send(200, {"ok": True, "draft": strategy.save_draft(b.get("strategy"))})
            if path == "/api/strategy/save":
                if b.get("revivedAt") is not None:
                    strategy.set_revived(strategy.parse_day(b["revivedAt"]))
                    return self._send(200, {"ok": True})
                saved = strategy.save_strategy(b.get("strategy"))
                log("채널 전략을 저장했어요")
                return self._send(200, {"ok": True, "strategy": saved})
            if path == "/api/strategy/channels":
                if isinstance(b.get("add"), dict):
                    a = b["add"]
                    entry = strategy.add_channel(a.get("url"), a.get("group"))
                    log(f"채널 전략 · 경쟁 채널 추가 · {entry['name']}")
                    job = False
                    if a.get("refresh"):
                        source.stop_backfill()
                        job = start_job(strategy.JOB_REFRESH, lambda: strategy.refresh([entry["key"]], "normal", log, editor.CANCEL))
                    return self._send(200, {"ok": True, "entry": entry, "job": job, "jobId": job or None})
                if isinstance(b.get("remove"), dict):
                    strategy.remove_channel(str(b["remove"].get("key") or ""))
                    return self._send(200, {"ok": True})
                if isinstance(b.get("group"), dict):
                    strategy.set_group(str(b["group"].get("key") or ""), b["group"].get("group"))
                    return self._send(200, {"ok": True})
                return self._send(400, {"ok": False, "error": "무엇을 할지 모르겠어요"})
            if path == "/api/strategy/refresh":
                if b.get("own"):
                    name, fn = strategy.JOB_OWN, lambda: strategy.refresh(None, "own", log, editor.CANCEL, strategy.JOB_OWN)
                else:
                    keys = b.get("keys") if isinstance(b.get("keys"), list) else None
                    if keys is not None and not all(isinstance(k, str) for k in keys):
                        return self._send(400, {"ok": False, "error": "잘못된 채널 목록이에요"})
                    mode = "all" if b.get("all") else "normal"
                    name, fn = strategy.JOB_REFRESH, lambda: strategy.refresh(keys, mode, log, editor.CANCEL)
                source.stop_backfill()  # 뒤에서 하던 출처 찾기는 멈춤 (YouTube 에 한꺼번에 묻지 않게)
                ok = start_job(name, fn)
                return self._send(200 if ok else 409, _started(ok))
            if path == "/api/strategy/pause":  # [지금 다시 시도]: 연달아 실패해서 쉬는 것만 풂
                strategy.clear_pause()
                return self._send(200, {"ok": True})
            if path == "/api/strategy/checkup":
                studio = strategy._clean_studio(b.get("studio"))
                source.stop_backfill()
                ok = start_job(strategy.JOB_CHECK, lambda: strategy.checkup(log, editor.CANCEL, studio))
                return self._send(200 if ok else 409, _started(ok))
            if path == "/api/strategy/todo":
                if isinstance(b.get("add"), dict):
                    a = b["add"]
                    t = strategy.todo_add(takeaway=a.get("takeaway") or None, text=a.get("text"), category=a.get("category"))
                    return self._send(200, {"ok": True, "todo": t})
                if isinstance(b.get("update"), dict):
                    return self._send(200, {"ok": True, "todo": strategy.todo_update(str(b["update"].get("id") or ""), bool(b["update"].get("done")))})
                if isinstance(b.get("remove"), dict):
                    strategy.todo_remove(str(b["remove"].get("id") or ""))
                    return self._send(200, {"ok": True})
                return self._send(400, {"ok": False, "error": "무엇을 할지 모르겠어요"})
            if path == "/api/strategy/takeaway":
                tid = str(b.get("id") or "")
                if not tid:
                    return self._send(400, {"ok": False, "error": "가져올 점을 골라 주세요"})
                strategy.set_hidden(tid, b.get("hide", True) is not False)
                return self._send(200, {"ok": True})
            if path == "/api/strategy/settings":
                return self._send(200, {"ok": True, "settings": strategy.set_settings(
                    b.get("remind") if isinstance(b.get("remind"), bool) else None, b.get("ownAuto") if isinstance(b.get("ownAuto"), bool) else None)})
            if path == "/api/strategy/ai":
                ok = start_job(strategy.JOB_AI, lambda: strategy.run_ai(log, editor.CANCEL))
                return self._send(200 if ok else 409, _started(ok))
            if path == "/api/strategy/ai_paste":
                ai = strategy.parse_ai(b.get("text"), by="paste")
                dh = strategy.data_hash(strategy.overview())
                with LOCK:  # 클로드 작업이 끝에 덮어쓰지 않게: 도는 중이면 거절 (확인과 저장을 작업 잠금 안에서)
                    if JOB["name"] == strategy.JOB_AI:
                        return self._send(409, {"ok": False, "busy": True,
                                                "error": f"지금 '{JOB['name']}' 중이에요. 끝난 뒤에 [저장]을 다시 눌러 주세요 (붙여 넣은 글은 그대로 있어요)"})
                    ai = strategy.save_ai(ai, dh)
                log("Claude 전략 대답을 붙여 넣었어요")
                return self._send(200, {"ok": True, "ai": ai})
        except strategy.StrategyError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        except OSError as e:
            log(f"채널 전략을 저장하지 못했어요 · {e}")
            return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
        return self._send(404, {"error": "not found"})

    # ---- 유튜브에 바로 올리기 (7단계 · youtube_upload · D-046) ----
    def _youtube_get(self, path, q):
        """읽기만 (인터넷 안 씀 · 파일을 바꾸지 않음 · 토큰·보안 비밀번호·세션 주소는 응답에 없음)."""
        yu = youtube_upload
        try:
            if path == "/api/youtube/status":
                want = (q.get("job") or [""])[0]
                with LOCK:  # ?job=<번호>: 7단계가 시킨 작업이 끝났으면 그 실패 안내 (작업 밖으로 나온 예외 · trouble.explain 카드 · D-049)
                    d = DONE.get(int(want)) if want.isdigit() else None
                    done = {"id": int(want), "error": d["error"], "fail": d["fail"]} if d and d["name"] in YT_JOBS else None
                return self._send(200, dict(yu.status(), ok=True, job=JOB["name"], done=done))
            if path == "/api/youtube/plan":
                try:
                    n = editor.safe_name((q.get("name") or [""])[0])
                    editor.video_path(n)
                except (ValueError, FileNotFoundError):
                    return self._send(404, {"ok": False, "error": "영상을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요"})
                priv = (q.get("privacy") or [""])[0]
                return self._send(200, yu.plan(n, (q.get("seq") or [""])[0] or None, priv if priv in yu.PRIVACY_KO else None))
            if path == "/api/youtube/history":
                lim = (q.get("limit") or ["10"])[0]
                return self._send(200, yu.history(int(lim) if lim.isdigit() else 10))
        except LookupError as e:  # 그사이 편집실에서 지운 편집본
            return self._send(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            studiolog.trace(e, "유튜브 올리기 화면 오류 위치")  # 기록은 한 길로 (studio.log 한 줄 + 오류 출력 · 비밀은 remote.redact · D-049)
            log(f"유튜브 올리기 화면을 읽지 못했어요 · {type(e).__name__}")
            return self._send(500, {"ok": False, "error": "유튜브 올리기 정보를 읽지 못했어요. 잠시 뒤 다시 열어 주세요"})
        return self._send(404, {"error": "not found"})

    def _youtube_post(self, path, b):
        """설정·연결·작업 시작. 올리기·이어 올리기·마무리는 start_job (한 번에 하나 · 겹치면 409 · 응답은 다른 작업과 같은 _started:
        jobId → 화면이 자기가 시킨 올리기의 끝만 받음). /api/youtube/* 는 모두 이 PC 화면 전용 — 휴대폰 원격(remote.ACTIONS) 허용 목록에
        넣지 않음 (로그인·연결 끊기·설정·토큰 흐름 · 올리기 시작도 PC 에서만). 휴대폰은 진행·끝 알림·[멈추기]만 (D-048)."""
        yu = youtube_upload
        try:
            if path == "/api/youtube/client":
                if b.get("clear"):
                    return self._send(200, yu.clear_all(log))
                text = b.get("json")
                if text is not None and not isinstance(text, str):
                    return self._send(400, {"ok": False, "error": "JSON 파일 내용을 그대로 넣어 주세요"})
                return self._send(200, yu.save_client(text, b.get("client_id"), b.get("client_secret"), log))
            if path == "/api/youtube/login":
                return self._send(200, yu.start_login(log))
            if path == "/api/youtube/login_cancel":
                return self._send(200, yu.cancel_login())
            if path == "/api/youtube/logout":
                return self._send(200, yu.logout(log))
            if path == "/api/youtube/settings":
                return self._send(200, {"ok": True, "settings": yu.save_settings(b.get("settings"))})
            if path == "/api/youtube/playlists":
                if isinstance(b.get("create"), dict):
                    c = b["create"]
                    return self._send(200, yu.create_playlist(c.get("title"), c.get("privacy") or "public"))
                return self._send(200, yu.playlists())
            if path == "/api/youtube/upload":
                n = editor.safe_name(b.get("name"))
                editor.video_path(n)
                seq = b.get("seq") or None
                opts = b.get("opts") if isinstance(b.get("opts"), dict) else {}
                if seq is not None and not isinstance(seq, str):
                    return self._send(400, {"ok": False, "error": "편집본을 다시 골라 주세요"})
                p = yu.plan(n, seq, opts.get("privacy") if opts.get("privacy") in yu.PRIVACY_KO else None)
                if p["problems"]:
                    return self._send(400, {"ok": False, "error": p["problems"][0], "problems": p["problems"], "codes": p["codes"]})
                again = bool(b.get("again"))
                ok = start_job(yu.JOB_NAME, lambda: yu.run_upload(n, seq, opts, log, editor.CANCEL, again), ctx=YT_CTX)
                return self._send(200 if ok else 409, _started(ok))
            if path == "/api/youtube/resume":
                key = str(b.get("key") or "")
                if not yu.KEY_RE.match(key):
                    return self._send(400, {"ok": False, "error": "잘못된 요청이에요"})
                ok = start_job(yu.JOB_NAME, lambda: yu.resume(key, log, editor.CANCEL), ctx=YT_CTX)
                return self._send(200 if ok else 409, _started(ok))
            if path == "/api/youtube/finish":
                vid = str(b.get("videoId") or "")
                steps = b.get("steps") if isinstance(b.get("steps"), list) and all(isinstance(x, str) for x in b["steps"]) else None
                if not yu.VIDEO_ID.match(vid):
                    return self._send(400, {"ok": False, "error": "잘못된 영상이에요"})
                ok = start_job(yu.JOB_FINISH, lambda: yu.finish(vid, steps, log, editor.CANCEL), ctx=YT_CTX)
                return self._send(200 if ok else 409, _started(ok))
            if path == "/api/youtube/pause":  # 우리 작업일 때만 멈춤 (내보내기의 ffmpeg 를 끄지 않게)
                with LOCK:
                    mine = JOB["name"] in (yu.JOB_NAME, yu.JOB_FINISH)
                    if mine:
                        editor.CANCEL.set()
                return self._send(200 if mine else 409, {"ok": True} if mine else {"ok": False, "error": "지금 유튜브에 올리는 중이 아니에요"})
            if path == "/api/youtube/check":  # 처리 상태 다시 확인 (videos.list 1단위 · 작업 아님)
                return self._send(200, yu.check(str(b.get("videoId") or "")))
            if path == "/api/youtube/discard":
                return self._send(200, yu.discard(b.get("key")))
            if path == "/api/youtube/open":
                yu.open_link(str(b.get("what") or ""), b.get("videoId"), b.get("key"))
                return self._send(200, {"ok": True})
        except yu.UploadError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        except yu.yt.ApiError as e:  # 연결·재생목록·상태 확인처럼 바로 Google 에 묻는 것 (끊긴 연결 표시는 youtube_upload 가 그 연결에만)
            log(f"유튜브 · {e.kind}" + (f" ({e.reason})" if e.reason else ""))
            return self._send(502, {"ok": False, "error": yu.explain(e), "kind": e.kind, "relogin": e.kind == "relogin"})
        except FileNotFoundError:
            return self._send(404, {"ok": False, "error": "영상을 찾지 못했어요 · 목록을 새로 고친 뒤 다시 골라 주세요"})
        except (ValueError, LookupError) as e:
            return self._send(400, {"ok": False, "error": str(e) or "잘못된 요청이에요"})
        except (BrokenPipeError, ConnectionResetError):  # 화면이 응답을 기다리지 않고 닫힘 (작업은 그대로)
            return None
        except OSError as e:
            log(f"유튜브 올리기 설정을 저장하지 못했어요 · {type(e).__name__}")
            return self._send(500, {"ok": False, "error": "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요"})
        return self._send(404, {"error": "not found"})

    @staticmethod
    def _convert(b):
        """보관함의 못 쓰는 형식(.MTS 등) 하나를 MP4 로 (원본은 보관함 안 '바꾸기 전 원본' 폴더로)."""
        n = editor.safe_name(b.get("name"))
        prog = lambda pct, detail: core.set_progress(label="MP4로 바꾸는 중", item=n, pct=pct, detail=detail)  # noqa: E731
        prog(None, "준비 중")
        out = intake.convert(core.VIDEOS, n, core.ffmpeg(), log, prog, editor.CANCEL.is_set)
        try:
            source.mark_footage(out, "local")  # 내 촬영본 (캠코더·카메라)
        except Exception:  # noqa: BLE001 — 출처 표시는 곁가지
            pass
        return {"ok": True, "name": out}

    @staticmethod
    def _analyze(b):
        model = core.model_of(b.get("model"))  # 안 보냈거나 모르는 값이면 이 PC 사양에 맞는 기본값
        studiolog.target(path="/api/analyze", names=list(b["names"]), model=model)  # 갑자기 꺼지면 다음에 켤 때 이 영상으로 [다시 하기]
        # 다시 찾는 영상: 받아쓰기를 새로 하기 전 글을 읽어 둠 → 사용자가 고치거나 지운 자막은 그대로 (D-413)
        before = {n: editor.caption_baseline(n) for n in b["names"] if editor._ppath(n).exists()}
        out = core.analyze_many(b["names"], log, model)
        failed = getattr(out, "failed", None) or {}  # 여러 개 중 그 파일만의 문제(깨짐 등)로 건너뛴 영상 → 쉬운 안내
        again = {}  # 다시 찾은 영상: {이름: {added: 더한 새 가편집 수, kept: 남긴 고친 자막 수}} → 화면 알림 (D-414)
        # 편집점 찾기 직후 1차 가편집(롱폼 정리본 + 쇼츠 편집본)까지 만들어 둠
        for n in b["names"]:
            if n in failed:
                continue
            core.set_progress(label="가편집 만드는 중", item=n, pct=99, detail="컷 정리·쇼츠 구간 고르는 중")
            had = editor._ppath(n).exists()
            # 이미 편집하던 영상이면 만든 편집본은 그대로 두고, 받아쓴 그대로인 자막만 새로 + 새 가편집은 옆에 추가 (이전 상태는 백업)
            proj, added, caps_changed = editor.reanalyze_project(n, before.get(n))
            editor.thumbs(n)
            editor.waveform(n)
            if had:
                kept = int((proj.get("fresh") or {}).get("kept") or 0)
                again[n] = {"added": added, "kept": kept}
                log(f"  편집본은 그대로 두고 새 가편집 {added}개를 추가했어요 · "
                    + (f"손으로 고친 자막 {kept}줄은 그대로 두고 나머지 자막을 새로 받아쓴 내용으로 바꿨어요" if kept else "자막은 새로 받아쓴 내용으로 바꿨어요")
                    + (" (예전 자막은 편집실 '기록' 탭의 '편집점 다시 찾기 전' 백업에 있어요)" if caps_changed else ""))
            else:
                names = ", ".join(q["name"] for q in proj["sequences"])
                log(f"  가편집 완료 · {names}")
        if failed:  # 일부만 실패: 화면이 '몇 개 중 몇 개' 카드 + 다시 하기는 실패한 영상만
            return dict({"done": out, "failed": list(failed), "why": failed}, **({"again": again} if again else {}))
        return {"done": out, "again": again} if again else out

    @staticmethod
    def _download(b, ck):
        """보관함에 담기 → {failed: [영상 id], why: {영상 id: 쉬운 안내}} (화면이 실패 카드·다시 하기에 씀)."""
        source.remember_hints(b.get("sources"))
        why = {}
        failed = core.download(b["ids"], log, ck, why=why)
        return {"failed": failed, "why": why}

    @staticmethod
    def _copying(names):
        """고른 영상 중 아직 들어오는 중(복사 중)인 것 (보관함 목록이 1초마다 지켜본 것 · 바로 답함)."""
        out = []
        for n in names if isinstance(names, list) else []:
            p = core.VIDEOS / str(n)
            if isinstance(n, str) and Path(n).name == n and p.is_file() and intake.copying(p):
                out.append(n)
        return out

    @staticmethod
    def _update(b):
        if b.get("engine", True):
            core.update_engine(log)
        changed = core.update_app(log) if b.get("app", True) else False
        return {"restart": changed}


class StudioHandler(Handler):
    """밖에서 작업(D-230): 원격 리스너가 RemoteHandler 와 묶어 쓰는 화면 처리기 — rstudio.gate 가 Host·세션·Origin·CSRF·길 분류를 본 뒤에만
    studio_call 로 이 앱의 처리 코드를 그대로 부른다. 그 동안(_studio_req)만 응답 머리글을 굳힘 (앱이 붙인 Cache-Control·CORS 는 빼고
    private·no-store · nosniff · 틀 안에 못 넣음 · CSP) · JSON·HTML 은 gzip · /api/state 의 사용자 폴더 경로는 '~'.
    로컬 서버(8765)는 이것을 쓰지 않는다 (_Server 는 Handler)."""
    _studio_req = False

    def _host_ok(self):
        return True if self._studio_req else super()._host_ok()  # 경계(rstudio.gate)가 Host 를 이미 봄

    def _origin_ok(self, origin):
        return True if self._studio_req else super()._origin_ok(origin)  # 경계가 터널 출처와 정확히 같은지 이미 봄

    def _seen(self, method, path):
        """밖에서 작업은 PC 화면이 아님 — 자동 업데이트는 그 세션의 마지막 요청(rstudio)으로 따로 셈 (app._auto_why)."""

    def studio_call(self, method, who):
        """경계를 지난 요청을 이 앱의 처리 코드로 · 그 동안 이 스레드는 '밖에서 · <기기>' (start_job) · 엔진 스스로 고치기 없음."""
        _REMOTE.who = who
        try:
            with core.no_self_update():
                return Handler.do_GET(self) if method == "GET" else Handler.do_POST(self)
        finally:
            _REMOTE.who = None

    def send_response(self, code, message=None):
        if self._studio_req:
            self._studio_sent = set()
        super().send_response(code, message)

    def send_header(self, keyword, value):
        if self._studio_req:
            k = keyword.lower()
            if k == "cache-control" or k.startswith("access-control-"):  # 캐시는 늘 private (Cloudflare 가 .jpg·.mp4·.js 를 가장자리에 두지 않게)
                return
            self._studio_sent.add(k)
        super().send_header(keyword, value)

    def end_headers(self):
        if self._studio_req:
            for k, v in rstudio.hardening(self):
                if k.lower() not in self._studio_sent:
                    super().send_header(k, v)
        super().end_headers()

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not self._studio_req:
            return super()._send(code, body, ctype)
        if isinstance(body, dict) and urlparse(self.path).path == "/api/state":
            body = rstudio.scrub_state(body)  # 작업 폴더·기록 줄의 사용자 폴더 경로(Windows 사용자 이름) → '~'
        gz = bool(getattr(self, "_studio_gz", False)) and ctype.startswith(("application/json", "text/html"))
        return rstudio.send(self, code, body, ctype, gz=gz)


class _Server(ThreadingHTTPServer):
    # Windows 의 SO_REUSEADDR 는 '다른 프로세스가 이미 듣고 있는 포트도 같이 잡기' → 앱이 두 개 떠서 작업·저장이 섞임
    # (asyncio 도 Windows 에서는 끔). 업데이트 재시작은 이전 프로세스가 포트를 놓을 때까지 _bind 가 기다림.
    allow_reuse_address = sys.platform != "win32"

    def handle_error(self, request, client_address):
        """처리 밖으로 나온 오류: socketserver 기본은 보낸 곳 주소와 traceback 을 그대로 오류 출력에 → studiolog.trace 하나로
        (요청 처리 중 이미 남긴 오류는 건너뜀 · 화면이 먼저 끊은 연결은 오류가 아님 · D-044)."""
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionError, TimeoutError)):
            return
        studiolog.trace(e, "요청 오류")


def _port_file():
    return core.WORK / ".port"  # 8765 를 못 써서 다른 포트로 켰으면 그 번호 (두 번째 실행·실행기가 찾아옴)


def _saved_port():
    try:
        p = int(_port_file().read_text(encoding="utf-8").strip())
        return p if p in PORT_FALLBACK else None
    except (OSError, ValueError):
        return None


def _save_port(p):
    try:
        if p == int(os.environ.get("FUTSAL_PORT") or 8765):
            _port_file().unlink(missing_ok=True)
        else:
            updater.write_atomic(_port_file(), str(p))
    except OSError:
        pass


def _reserved(e):
    """Windows 가 막아 둔 포트 (Hyper-V·WSL·Docker 의 excludedportrange → WinError 10013) — 기다려도 안 풀림."""
    return getattr(e, "winerror", None) == 10013 or e.errno == errno.EACCES


def _bind(restart=False):
    """서버 포트 잡기. 기본은 8765 (FUTSAL_PORT 로 바꾸면 그 포트만).
    - 이 앱이 이미 듣고 있으면 None (부르는 쪽이 그 창을 앞으로) · 업데이트 재시작이면 이전 프로세스가 놓을 때까지 기다림
    - 다른 프로그램이 쓰거나 Windows 가 예약한 포트면 8766~8799 중 빈 곳 (번호는 작업 폴더 .port 에 남김)"""
    global PORT
    fixed = bool(os.environ.get("FUTSAL_PORT"))
    ports = [PORT] if fixed else list(dict.fromkeys([_saved_port() or PORT, PORT, *PORT_FALLBACK]))
    end = time.monotonic() + (10 if restart or fixed else 0)
    for p in ports:
        while True:
            try:
                srv = _Server(("127.0.0.1", p), Handler)
            except OSError as e:
                if _reserved(e):
                    break
                if not restart and _ours(p):
                    return None
                if time.monotonic() < end:
                    time.sleep(0.25)
                    continue
                break
            PORT = p
            _save_port(p)
            return srv
    return None


_WINDOW = []
_BROWSER = []  # 전용 창 없이 브라우저로 쓰는 중 (--browser 또는 창을 못 열었을 때)
_NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # PC 에 프록시를 설정해 둬도 127.0.0.1 은 바로


def _ours(port, timeout=2):
    """그 포트에서 듣는 게 이 앱인지 (GET /api/ping). 예전 버전(ping 없음)은 404 {"error": "not found"} 로 답함."""
    try:
        with _NOPROXY.open(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            return json.loads(r.read() or b"{}").get("app") == APP_ID
    except urllib.error.HTTPError as e:
        try:
            return e.code == 404 and json.loads(e.read() or b"{}") == {"error": "not found"}
        except ValueError:
            return False
    except (OSError, ValueError):
        return False


def _candidate_ports():
    return list(dict.fromkeys([PORT] + ([] if os.environ.get("FUTSAL_PORT") else [_saved_port() or PORT])))


def _focus_running():
    """이미 켜진 이 앱이 있으면 그 창을 앞으로 (새 창·브라우저를 또 열지 않음 → 같은 편집본을 두 곳에서 고치지 않게)."""
    for p in _candidate_ports():
        if not _ours(p):
            continue
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{p}/api/focus", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            with _NOPROXY.open(req, timeout=2) as r:
                if json.loads(r.read() or b"{}").get("ok") is True:
                    return True
        except (OSError, ValueError):
            pass
    return False


def _running_url():
    """이미 켜진 이 앱의 주소 (못 찾으면 기본 주소)."""
    for p in _candidate_ports():
        if _ours(p, 1):
            return f"http://127.0.0.1:{p}/"
    return f"http://127.0.0.1:{PORT}/"


def _confirm_close(win):
    """작업 중에 창을 닫으려 하면 한 번 묻기 (닫으면 그 작업은 멈춤). 닫아도 되면 True."""
    name = JOB["name"]
    if not name:
        return True
    msg = f"지금 '{name}' 중이에요. 창을 닫으면 이 작업이 멈춰요.\n그래도 닫을까요?"
    done, ans = threading.Event(), {}

    def got(r):
        ans["r"] = r
        done.set()
    try:  # pywebview 는 callback 을 Promise 일 때만 부름 (바로 값이면 부르지 않아 600초를 기다림) → 옆의 flushBeforeClose 처럼 Promise 로
        win.evaluate_js(f"Promise.resolve(confirm({json.dumps(msg, ensure_ascii=False)}))", callback=got)
    except Exception:  # noqa: BLE001 — 물어볼 수 없으면 예전처럼 닫음
        return True
    if not done.wait(600):
        return False  # 대답이 없으면 닫지 않음 (다시 닫기를 누르면 또 물음)
    if ans.get("r") is False:
        return False
    # 알고 닫음 → 정상 종료 (_quit 이 실행 표시 studio.running.json 을 지움 · 다음에 켤 때 '갑자기 꺼졌어요' 알림 없음) · 기록에만 남김
    log(f"작업 중에 창을 닫았어요 · '{name}' 작업은 멈췄어요")
    return True


def _on_closing(win):
    """창을 닫을 때: 작업 중이면 한 번 묻고, 편집실에 저장 안 된 것이 있으면 먼저 저장하고 닫음."""
    state = {"go": False}

    def handler():
        if state["go"]:
            return True
        state["go"] = True

        def flush():
            if not _confirm_close(win):
                state["go"] = False
                return
            done = threading.Event()
            try:
                win.evaluate_js("Promise.resolve(window.flushBeforeClose ? window.flushBeforeClose() : true)", callback=lambda r: done.set())
                done.wait(6)
            except Exception:
                pass
            editor.wait_saves(3)
            try:
                win.destroy()
            except Exception:
                _quit()

        threading.Thread(target=flush, daemon=True).start()
        return False  # 저장이 끝난 뒤 다시 닫음

    return handler


class _StampedErr:
    """pythonw 의 오류 출력: 줄마다 시각을 붙여 studio-error.log 에 (traceback·스레드 오류·서버 요청 오류)."""

    def __init__(self, f):
        self.f, self.bol = f, True

    def write(self, s):
        s = str(s)
        out = []
        for line in remote.redact(s).splitlines(True):  # 비밀(터널 주소·주제·표·서명)은 '…' — 어디서 찍든 이 파일에는 남지 않게
            if self.bol:
                out.append(studiolog.stamp())  # studio.log 와 같은 연도 붙은 시각 (두 파일을 맞춰 보기 쉽게)
            out.append(line)
            self.bol = line.endswith("\n")
        try:
            self.f.write("".join(out))
            self.f.flush()
        except (OSError, ValueError):
            pass
        return len(s)

    def flush(self):
        try:
            self.f.flush()
        except (OSError, ValueError):
            pass

    def fileno(self):
        return self.f.fileno()


ERROR_LOG_MAX = 1_000_000  # 이보다 커지면 .old 로 하나만 남기고 새로


def _error_log():
    """pythonw(콘솔 없음)로 켜지면 traceback·스레드 오류·서버 요청 오류가 모두 사라짐 (sys.stderr 가 None, 실행기가 다시 띄우면 NUL)
    → 작업 폴더의 studio-error.log 로. studio.log 에는 화면 문구만 남아 Windows 에서만 생기는 오류의 위치를 알 수 없었음."""
    if sys.stderr is not None and Path(sys.executable).name.lower() != "pythonw.exe":
        return None
    p = core.WORK / "studio-error.log"
    try:
        if p.exists() and p.stat().st_size > ERROR_LOG_MAX:
            os.replace(p, p.with_name("studio-error.old.log"))
        f = open(p, "a", encoding="utf-8", errors="replace")
    except OSError:
        return None
    sys.stderr = _StampedErr(f)
    try:
        faulthandler.enable(file=f)  # WebView2·onnxruntime 같은 바깥 코드가 죽을 때도 위치를 남김
    except (RuntimeError, ValueError, OSError):
        pass
    return p


def _start_webview(webview):
    """창 띄우기: 설정(localStorage: 보관함 필터·최근 색·타임라인 높이 등)이 다음에도 남게 비공개 모드를 끔 (pywebview 5+ 기본은
    비공개 → 켤 때마다 지워짐). 저장 위치는 앱 전용 폴더. 예전 pywebview 는 그 인자를 몰라 그대로."""
    try:
        webview.start(private_mode=False, storage_path=str(core.ENGINE_HOME / "webview"))
    except TypeError:
        webview.start()


def _create_window(webview, url, minimized=False):
    """앱 창 (자동 업데이트 뒤에는 내려 둔 채로 · 예전 pywebview 가 minimized 를 모르면 그냥)."""
    kw = {"width": 1320, "height": 860, "min_size": (960, 640)}
    if minimized:
        try:
            return webview.create_window(APP_NAME, url, minimized=True, **kw)
        except TypeError:
            pass
    return webview.create_window(APP_NAME, url, **kw)


def main():
    _error_log()
    if _redirect_to_updater():  # 끊긴 업데이트는 실행기가 먼저 되돌림
        return
    restart = bool(os.environ.pop("FUTSAL_RESTART", None))
    auto = bool(os.environ.pop("FUTSAL_AUTO", None))  # 자동 업데이트의 다시 시작 (아무도 안 누름) → 창은 내려 둔 채로 · 브라우저를 새로 열지 않음
    if not restart and _focus_running():
        return
    srv = _bind(restart)
    if srv is None:  # 이미 실행 중 → 그 창을 앞으로 (막 켜지는 중이면 잠깐 기다림 · 안 되면 그 화면만 띄워줌)
        for _ in range(16):
            if _focus_running():
                return
            time.sleep(0.5)
        if any(_ours(p, 1) for p in _candidate_ports()):
            webbrowser.open(_running_url())
        else:
            _no_port()
        return
    url = f"http://127.0.0.1:{PORT}/"
    log(f"{APP_NAME} v{core.VERSION} 시작")
    log(f"작업 폴더 · {core.WORK}")
    if PORT != int(os.environ.get("FUTSAL_PORT") or 8765):
        log(f"  8765 포트를 다른 프로그램이 쓰고 있어서 {PORT} 포트로 켰어요")
    _after_start()
    if "--browser" in sys.argv:
        _BROWSER.append(True)  # 서버가 답하기 전에 정해 둠 (그사이 또 켠 실행이 /api/focus 를 물어도 화면을 다시 열게)
    threading.Thread(target=editor.sweep_temp, daemon=True).start()  # 멈췄거나 갑자기 꺼져 남은 임시 폴더 정리
    JOB_HOOKS.append(idle.touch)  # 작업이 끝나고 5분 동안 다른 작업이 없으면 불러 둔 모델을 내려놓음 (메모리 반환)
    idle.start(LOCK, lambda: bool(JOB["name"]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:  # 실행기(원격): 켜 둔 채로 껐다 켰으면(업데이트 재시작 포함) 이어서 켬 · 실패해도 앱은 그대로
        remote.init(_remote_bridge())
        JOB_HOOKS.append(remote.SVC.job_hook)
    except Exception as e:  # noqa: BLE001
        studiolog.trace(e, "실행기 준비 오류 위치")
        log(f"실행기를 준비하지 못했어요 · {e}")
    if sys.platform == "win32":
        winlink.prepare(core.APP_DIR, _gui_python(), core.ENGINE_HOME, log)  # 아이콘·작업 표시줄 묶음 (창을 만들기 전에)
    elif sys.platform == "darwin":
        threading.Thread(target=ensure_shortcut, daemon=True).start()
    if "--browser" not in sys.argv:
        try:
            import webview  # pywebview: 전용 앱 창
            win = _create_window(webview, url, auto)
            _WINDOW.append(win)
            try:
                win.events.closing += _on_closing(win)
            except Exception:
                pass
            _start_webview(webview)
            _quit()  # 창을 닫으면 종료
        except Exception as e:
            _WINDOW.clear()
            log(f"앱 창을 열지 못해 브라우저로 엽니다 · {e}")
        _BROWSER.append(True)
    if not auto:  # 자동 업데이트: 열어 둔 브라우저 탭이 새 앱에 다시 붙음 (아이콘을 누르면 /api/focus 가 화면을 엶)
        webbrowser.open(url)
    while True:
        time.sleep(3600)


def _no_port():
    """쓸 수 있는 포트가 하나도 없음: 예전에는 아무 말 없이 빈 브라우저 창만 떴음 → 기록 + 알림 창."""
    msg = (f"프로그램을 켜지 못했어요. 이 PC의 다른 프로그램이 {PORT}~{PORT_FALLBACK[-1]} 포트를 모두 쓰고 있어요. "
           "PC를 다시 시작한 뒤 켜 보세요. 계속되면 작업 폴더의 studio.log 를 관리자에게 보내 주세요.")
    log(msg)
    updater._alert(msg)


if __name__ == "__main__":
    main()
