"""무거운 작업을 따로 프로세스에서 돌리는 틀 (누끼 · 나중에 편집점 찾기도).

왜: 고품질 누끼(BiRefNet)는 한 번에 6.5GB 넘게 씀 → 앱 서버 안에서 돌리면 8GB 노트북에서 PC 가 멈추거나 앱이 꺼지고,
끝나도 메모리를 안 돌려줌. 따로 프로세스에서 돌리면 끝날 때 메모리를 전부 OS 에 돌려주고, 그 프로세스가 죽어도 앱은 살아 있음.

call("모듈:함수", 인자...) → 자식 파이썬이 그 함수를 부르고 결과(JSON)를 돌려줌.
- 자식 안의 core.set_progress(...) 는 부모 화면 진행 표시로 그대로 전달됨
- cancel(threading.Event) 이 켜지면 자식을 끔 → Cancelled
- procs(set) 에 자식을 넣어 둠 → 멈추기(✕)·앱 끄기(editor.cancel_export) 때 같이 꺼짐
- 인자·결과는 표준 입력·출력의 JSON 한 줄 (한글 경로도 명령줄 인코딩 문제 없이)

avail_mb(): 지금 남은 실제 메모리(MB) · Windows GlobalMemoryStatusEx · Linux /proc/meminfo · 모르면 None.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import core

HERE = Path(__file__).resolve().parent
_TAG = "@@worker@@ "  # 자식이 표준 출력에 쓰는 줄 머리 (라이브러리가 print 한 것과 섞이지 않게)


class WorkerError(RuntimeError):
    """자식 프로세스가 실패했거나(오류·메모리 부족으로 죽음) 결과를 못 돌려줌."""

    def __init__(self, msg, code=None, kind=None):
        super().__init__(msg)
        self.code, self.kind = code, kind


class Cancelled(Exception):
    pass


# ---------- 남은 메모리 ----------

def avail_mb():
    """지금 쓸 수 있는 실제 메모리(MB). 알 수 없으면 None."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullAvailPhys // (1024 * 1024)
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", encoding="ascii") as f:
                for ln in f:
                    if ln.startswith("MemAvailable:"):
                        return int(ln.split()[1]) // 1024
        try:
            import psutil  # 있으면 (macOS 등)
            return psutil.virtual_memory().available // (1024 * 1024)
        except Exception:  # noqa: BLE001
            pass
        if sys.platform == "darwin":  # psutil 없이: 빈 페이지 + 비울 수 있는 페이지 (대략)
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = 4096
            vals = {}
            for ln in out.splitlines():
                if "page size of" in ln:
                    page = int(ln.split("page size of")[1].split()[0])
                elif ":" in ln:
                    k, v = ln.split(":", 1)
                    try:
                        vals[k.strip()] = int(v.strip().rstrip("."))
                    except ValueError:
                        pass
            n = sum(vals.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
            return n * page // (1024 * 1024) if n else None
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------- 부모 쪽 ----------

def _popen(cmd, **kw):
    """core.popen 이 있으면 그걸로 (Windows: 앱이 꺼지면 같이 꺼지는 Job Object) · 없으면 검은 창 없이."""
    pop = getattr(core, "popen", None)
    if pop is not None:
        return pop(cmd, **kw)
    if "creationflags" not in kw:
        kw.update(core.NO_WINDOW)
    return subprocess.Popen(cmd, **kw)


def _python():
    """자식 파이썬: Windows 의 pythonw.exe 는 표준 입출력이 없을 수 있어 python.exe 를 씀 (검은 창은 CREATE_NO_WINDOW 로 막음)."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def call(target, *args, cancel=None, procs=None, timeout=None, env=None, **kwargs):
    """target = '모듈:함수' 를 따로 프로세스에서 부르고 결과(JSON 으로 바꿀 수 있는 값)를 돌려줌.
    실패하면 WorkerError(자식 오류 문장 · 종료 코드) · cancel 이 켜지면 자식을 끄고 Cancelled."""
    e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    e.update(env or {})
    cmd = [_python(), "-X", "utf8", str(HERE / "worker.py")]
    p = _popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(HERE), env=e)
    if procs is not None:
        procs.add(p)
    err_tail = []

    def read_err():
        for ln in p.stderr:
            err_tail.append(ln.decode("utf-8", "replace"))
            del err_tail[:-40]

    te = threading.Thread(target=read_err, daemon=True)
    te.start()
    done = threading.Event()

    def watch():  # 멈추기(✕) · 시간 초과 → 자식 끄기
        t0 = time.time()
        while not done.wait(0.2):
            if (cancel is not None and cancel.is_set()) or (timeout and time.time() - t0 > timeout):
                try:
                    p.kill()
                except OSError:
                    pass
                return

    threading.Thread(target=watch, daemon=True).start()
    res = None
    try:
        try:
            p.stdin.write((json.dumps({"target": target, "args": args, "kwargs": kwargs}, ensure_ascii=True) + "\n").encode("ascii"))
            p.stdin.close()
        except OSError:
            pass  # 자식이 바로 죽음 → 아래에서 종료 코드로 알림
        for raw in p.stdout:
            ln = raw.decode("utf-8", "replace")
            if not ln.startswith(_TAG):
                continue
            try:
                msg = json.loads(ln[len(_TAG):])
            except ValueError:
                continue
            if "progress" in msg:
                try:
                    core.set_progress(**msg["progress"])
                except Exception:  # noqa: BLE001
                    pass
            elif "result" in msg or "error" in msg:
                res = msg
        p.wait()
        te.join(2)
    finally:
        done.set()
        if procs is not None:
            procs.discard(p)
        for s in (p.stdout, p.stderr):
            try:
                s.close()
            except OSError:
                pass
    if cancel is not None and cancel.is_set():
        raise Cancelled("멈췄어요")
    if res and "result" in res and p.returncode == 0:
        return res["result"]
    if res and "error" in res:
        raise WorkerError(res["error"], p.returncode, res.get("type"))
    tail = "".join(err_tail).strip().splitlines()[-1:] or [""]
    raise WorkerError(f"작업 프로세스가 끝까지 못 했어요 (종료 코드 {p.returncode}) {tail[0][:300]}".strip(), p.returncode, "died")


# ---------- 자식 쪽 ----------

def _emit(obj):
    sys.stdout.write(_TAG + json.dumps(obj, ensure_ascii=True, default=str) + "\n")
    sys.stdout.flush()


def _child():
    req = json.loads(sys.stdin.readline())

    def set_progress(**kw):  # 진행 표시는 부모 화면으로
        _emit({"progress": kw})

    core.set_progress = set_progress
    mod, fn = req["target"].split(":", 1)
    try:
        import importlib
        f = getattr(importlib.import_module(mod), fn)
        out = f(*req.get("args") or [], **req.get("kwargs") or {})
    except BaseException as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        _emit({"error": str(ex) or type(ex).__name__, "type": type(ex).__name__})
        sys.stdout.flush()
        os._exit(1)
    _emit({"result": out})
    sys.stdout.flush()
    os._exit(0)  # 남은 스레드(onnxruntime 등)를 기다리지 않고 바로 끝 → 메모리 반환


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    _child()
