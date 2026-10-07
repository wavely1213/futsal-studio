"""studio.log — 사용자 PC 에서 문제가 생기면 관리자에게 보내는 기록 파일 (PROJECT_CONTEXT 7절 · 오류 기록은 이 모듈 하나로 · D-037 · D-044).

오류가 남는 곳 (한 오류는 파일마다 한 번):
  studio.log        화면 기록 줄 + 오류마다 '오류 위치' 한 줄(종류·내용·파일:줄) — 관리자에게 보내는 파일 (write·trace)
  studio-error.log  같은 오류의 traceback 전체 + 프로그램이 통째로 죽을 때의 위치(faulthandler) — pythonw(콘솔 없음)일 때만
                    (app._error_log 가 sys.stderr 를 이 파일로 돌림 · 콘솔로 켜면 콘솔에)
  둘 다 비밀(터널 주소·알림 주제·영상 표·서명·연결 코드)은 remote.redact 로 '…' (redact).
- write: 줄마다 연도가 붙은 시각 · MAX_BYTES 를 넘으면 studio.old.log 하나만 남기고 새로 시작 (계속 커지지 않게)
- trace: 오류 위치(종류·내용·파일:줄·호출 경로)를 한 줄로 + 오류 출력(stderr)에 traceback 전체.
  앱은 pythonw(콘솔 없음)로 돌아 traceback 이 아무 데도 안 남았음 · 원격(remote._trace)·서버 요청 오류도 이것 하나로
- install_hooks: 작업 밖(뒤에서 도는 스레드·메인)에서 잡히지 않은 오류도 같은 한 줄 + traceback (기본 처리 대신 · 비밀은 지움)
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


def redact(s):
    """기록에 남으면 안 되는 비밀을 '…'로 — remote.redact 를 그대로 씀 (규칙은 한 곳에만). remote 를 불러올 수 없으면 그대로."""
    try:
        import remote
    except Exception:  # noqa: BLE001 — 기록은 곁가지 (remote 는 표준 라이브러리 + 앱 모듈뿐이라 보통은 불러와짐)
        return str(s)
    return remote.redact(s)


def stamp(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S ", time.localtime(t))


def write(msg):
    """studio.log 에 한 줄 (화면 작업 기록에는 안 보임). 못 쓰는 글자는 대신 표시 · 실패해도 작업은 계속."""
    p = path()
    if p is None:
        return
    line = stamp() + redact(msg) + "\n"
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


def _stderr(e, head=""):
    """오류 출력(콘솔 · pythonw 면 studio-error.log)에 traceback 전체 — 비밀은 지우고. 출력이 없으면(pythonw 초기) 그냥 넘어감."""
    if sys.stderr is None or e is None:
        return
    try:
        sys.stderr.write(redact(head + "".join(traceback.format_exception(type(e), e, e.__traceback__))))
    except Exception:  # noqa: BLE001
        pass


def trace(e=None, what="오류 위치"):
    """지금 처리 중인(또는 준) 오류의 위치를 studio.log 에 한 줄로 + 오류 출력에 traceback 전체(비밀은 지움).
    같은 오류를 두 번 부르면(요청 처리 → 서버 오류 처리) 두 번째는 건너뜀."""
    e = e if e is not None else sys.exc_info()[1]
    if e is None or getattr(e, "_studio_traced", False):
        return
    try:
        e._studio_traced = True
    except (AttributeError, TypeError):  # 속성을 못 붙이는 오류도 기록은 함
        pass
    try:
        write(f"  {what} · {where(e)}")
    except Exception:  # noqa: BLE001
        pass
    _stderr(e)


def install_hooks():
    """뒤에서 도는 스레드·메인 스레드의 잡히지 않은 오류도 studio.log 에 위치 한 줄 + 오류 출력에 traceback (비밀은 지움).
    파이썬 기본 처리(traceback 을 그대로 오류 출력에)는 부르지 않음 — 같은 traceback 이 두 번, 비밀이 섞인 채로 남지 않게.
    다른 곳에서 바꿔 끼운 처리가 있었으면 그것은 그대로 부름."""
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
        if prev_thread is threading.__excepthook__:
            if args.exc_type is not SystemExit:
                _stderr(args.exc_value, f"Exception in thread {getattr(args.thread, 'name', '') or '?'}:\n")
        else:
            prev_thread(args)

    def sys_hook(tp, value, tb):
        if not issubclass(tp, KeyboardInterrupt):
            try:
                write(f"프로그램 오류 · {where(value)}")
            except Exception:  # noqa: BLE001
                pass
        if prev_sys is sys.__excepthook__:
            _stderr(value)
        else:
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
        return {"log": f"지난번 실행({started})이 정상적으로 끝나지 않았어요 (PC를 껐거나 프로그램이 갑자기 꺼졌을 수 있어요 · "
                       "남은 위치가 있으면 studio-error.log)",
                "notice": None}
    return {"log": f"지난번 실행({started})이 '{job}' 중에 갑자기 꺼졌어요 ({old.get('jobAt') or '?'}에 시작한 작업 · "
                   "남은 위치가 있으면 studio-error.log)",
            "notice": f"지난번에 '{job}' 중에 프로그램이 갑자기 꺼졌어요. 그 작업을 다시 해 주세요. "
                      "계속 꺼지면 작업 폴더의 studio.log 파일을 관리자에게 보내 주세요 (같은 폴더에 studio-error.log 가 있으면 그것도요)."}


def job(name):
    """작업 시작(name)·끝(None)을 실행 표시에 (session_start 를 부른 프로세스만 · 시험에서 띄운 서버는 쓰지 않음)."""
    if not _SESSION:
        return
    _SESSION.update(job=name, jobAt=stamp().strip() if name else None)
    _save_running()


def session_end():
    """정상 종료: 실행 표시를 지움 (창 닫기·업데이트 재시작).
    이 프로세스가 쓴 표시만 — 다시 시작할 때 새 프로세스가 먼저 자기 표시를 썼으면 그대로 둠 (새 실행의 '갑자기 꺼짐'을 잃지 않게)."""
    if not _SESSION:
        return
    _SESSION.clear()
    r = _running()
    if r is None:
        return
    try:
        cur = json.loads(r.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError):  # 깨진 표시 → 누구 것인지 모름 · 지움
        cur = None
    if isinstance(cur, dict) and cur.get("pid") not in (None, os.getpid()):
        return
    try:
        r.unlink()
    except OSError:
        pass
