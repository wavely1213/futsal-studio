"""studio.log — 사용자 PC 에서 문제가 생기면 관리자에게 보내는 하나뿐인 기록 파일 (PROJECT_CONTEXT 7절).

- write: 줄마다 연도가 붙은 시각 · MAX_BYTES 를 넘으면 studio.old.log 하나만 남기고 새로 시작 (계속 커지지 않게)
- trace: 오류 위치(종류·내용·파일:줄·호출 경로)를 한 줄로. 앱은 pythonw(콘솔 없음)로 돌아 traceback 이 아무 데도 안 남았음
- install_hooks: 작업 밖(뒤에서 도는 스레드·메인)에서 잡히지 않은 오류도 같은 한 줄로 (원래 처리도 그대로 부름)
- session_start·job·session_end: 실행 표시 파일 studio.running.json — 켤 때 남아 있으면 지난번에 정상적으로 꺼지지 않은 것
  (어떤 작업 중이었는지도 남김)
기록 파일 위치는 app 이 setup 으로 알려 준다(작업 폴더의 studio.log). 알려 주지 않으면 아무것도 쓰지 않는다(시험·도구).
표준 라이브러리만 쓴다 (core·editor 가 함수 안에서 불러 씀).
"""
import atexit
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

MAX_BYTES = 2_000_000          # 이보다 커지면 studio.old.log 로 하나만 남김 (하루 200KB 안팎 → 열흘 남짓)
OLD_NAME = "studio.old.log"
RUNNING = "studio.running.json"
APP_DIR = Path(__file__).resolve().parent
_PATH = [None]                 # 기록 파일 경로를 돌려주는 함수 (app.LOGFILE 을 그때그때 읽음 → 시험에서 바꿔 끼울 수 있음)
_LOCK = threading.Lock()
_SESSION = {}                  # 이번 실행 표시 (session_start 를 부른 프로세스만 씀)
_HOOKED = []
_AT_EXIT = []


def setup(path_fn):
    """기록 파일 위치 알려 주기 (부를 때마다 경로를 돌려주는 함수)."""
    _PATH[0] = path_fn


def path():
    f = _PATH[0]
    try:
        return Path(f()) if f else None
    except Exception:  # noqa: BLE001 — 기록은 곁가지
        return None


def stamp(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S ", time.localtime(t))


def write(msg):
    """studio.log 에 한 줄 (화면 작업 기록에는 안 보임). 못 쓰는 글자는 대신 표시 · 실패해도 작업은 계속."""
    p = path()
    if p is None:
        return
    line = stamp() + str(msg) + "\n"
    with _LOCK:
        try:
            if p.stat().st_size > MAX_BYTES:
                os.replace(p, p.with_name(OLD_NAME))
        except OSError:  # 없음·잠김(메모장 등) → 이번에는 그대로 이어 씀
            pass
        try:
            with open(p, "a", encoding="utf-8", errors="replace") as f:
                f.write(line)
        except (OSError, ValueError):
            pass


# ---------- 오류 위치 ----------

def _one_line(s, n=300):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _ours(fname):
    try:
        return Path(fname).resolve().parent == APP_DIR
    except (OSError, ValueError):
        return False


def _short(fname):
    """우리 파일은 이름만 · 바깥 파일은 '패키지/파일' (사용자 이름이 든 경로를 남기지 않게)."""
    parts = Path(fname).parts
    return parts[-1] if _ours(fname) else "/".join(parts[-2:])


def _frames(e):
    frames = traceback.extract_tb(e.__traceback__) if getattr(e, "__traceback__", None) else []
    pick = [f for f in frames if _ours(f.filename)][::-1][:6]  # 안쪽(오류 난 곳)부터
    if frames and not _ours(frames[-1].filename):  # 바깥 라이브러리 안에서 났으면 그 자리도
        pick.insert(0, frames[-1])
    return " ← ".join(f"{_short(f.filename)}:{f.lineno} {f.name}" for f in pick)


def where(e):
    """오류 → "KeyError: 'ids' · app.py:469 <lambda> ← app.py:77 runner" (+ 원인 오류가 있으면 한 단계 더)."""
    out = f"{type(e).__name__}: {_one_line(e)}"
    loc = _frames(e)
    if loc:
        out += f" · {loc}"
    cause = e.__cause__ or (None if e.__suppress_context__ else e.__context__)
    if cause is not None and cause is not e:
        out += f" · 원인 {type(cause).__name__}: {_one_line(cause, 200)}"
        loc = _frames(cause)
        if loc:
            out += f" · {loc}"
    return out


def trace(e=None, what="오류 위치"):
    """지금 처리 중인(또는 준) 오류의 위치를 studio.log 에 한 줄로 + 콘솔이 있으면 traceback 전체(예전처럼)."""
    e = e if e is not None else sys.exc_info()[1]
    if e is None:
        return
    try:
        write(f"  {what} · {where(e)}")
    except Exception:  # noqa: BLE001
        pass
    if sys.stderr is not None:
        try:
            traceback.print_exception(type(e), e, e.__traceback__)
        except Exception:  # noqa: BLE001
            pass


def install_hooks():
    """뒤에서 도는 스레드·메인 스레드의 잡히지 않은 오류도 studio.log 에 위치를 남김 (원래 처리도 그대로)."""
    if _HOOKED:
        return
    prev_thread, prev_sys = threading.excepthook, sys.excepthook

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            name = getattr(args.thread, "name", "") or "?"
            try:
                write(f"뒤에서 하던 일({name})에 오류가 났어요 · {where(args.exc_value)}")
            except Exception:  # noqa: BLE001
                pass
        prev_thread(args)

    def sys_hook(tp, value, tb):
        if not issubclass(tp, KeyboardInterrupt):
            try:
                write(f"프로그램 오류 · {where(value)}")
            except Exception:  # noqa: BLE001
                pass
        prev_sys(tp, value, tb)

    threading.excepthook, sys.excepthook = thread_hook, sys_hook
    _HOOKED.append((prev_thread, prev_sys))


# ---------- 실행 표시 (갑자기 꺼졌는지) ----------

def _running():
    p = path()
    return p.with_name(RUNNING) if p else None


def _save_running():
    r = _running()
    if r is None:
        return
    tmp = r.with_name(RUNNING + ".tmp")
    try:
        tmp.write_text(json.dumps(_SESSION, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, r)
    except OSError:  # 잠김(백신 등) → 다음 기회에
        pass


def session_start(version):
    """앱이 켜질 때 (포트를 잡은 뒤 한 번): 지난번 실행 표시가 남아 있으면 {"log": 기록 줄, "notice": 화면 알림 또는 None}.
    이번 실행 표시를 새로 씀. 작업 중에 꺼졌을 때만 화면에도 알린다 (PC 를 끈 것일 수도 있어서 작업이 없으면 기록만)."""
    r = _running()
    if r is None:
        return None
    try:
        old = json.loads(r.read_text(encoding="utf-8"))
        old = old if isinstance(old, dict) else {}
    except FileNotFoundError:
        old = None
    except (OSError, ValueError):  # 깨진 표시도 '정상 종료가 아님'
        old = {}
    _SESSION.clear()
    _SESSION.update(pid=os.getpid(), start=stamp().strip(), version=str(version), job=None, jobAt=None)
    _save_running()
    if not _AT_EXIT:  # 보통 종료(브라우저로 쓰다 Ctrl+C 등)도 표시를 지움 · 창 닫기·재시작은 app._quit 이 먼저
        atexit.register(session_end)
        _AT_EXIT.append(True)
    if old is None:
        return None
    started = f"{old.get('start') or '?'} 시작 · v{old.get('version') or '?'}"
    job = old.get("job") if isinstance(old.get("job"), str) else None
    if not job:
        return {"log": f"지난번 실행({started})이 정상적으로 끝나지 않았어요 (PC를 껐거나 프로그램이 갑자기 꺼졌을 수 있어요)",
                "notice": None}
    return {"log": f"지난번 실행({started})이 '{job}' 중에 갑자기 꺼졌어요 ({old.get('jobAt') or '?'}에 시작한 작업)",
            "notice": f"지난번에 '{job}' 중에 프로그램이 갑자기 꺼졌어요. 그 작업을 다시 해 주세요. "
                      "계속 꺼지면 작업 폴더의 studio.log 파일을 관리자에게 보내 주세요."}


def job(name):
    """작업 시작(name)·끝(None)을 실행 표시에 (session_start 를 부른 프로세스만 · 시험에서 띄운 서버는 쓰지 않음)."""
    if not _SESSION:
        return
    _SESSION.update(job=name, jobAt=stamp().strip() if name else None)
    _save_running()


def session_end():
    """정상 종료: 실행 표시를 지움 (창 닫기·업데이트 재시작)."""
    if not _SESSION:
        return
    _SESSION.clear()
    r = _running()
    try:
        if r is not None:
            r.unlink()
    except OSError:
        pass
