"""Cloudflare 빠른 터널(cloudflared) 관리 — '휴대폰으로 보기'의 바깥 연결 (D-024).

  - 받기: 고정 판(CF_VERSION)의 Windows 실행 파일을 GitHub 릴리스 고정 주소에서 받아 크기·sha256 확인 뒤 ~/.futsal-studio/bin 에 둠.
    확인 안 된 '최신'으로 바꿔 받지 않는다(실패하면 원격 접속만 안 켜짐).
  - 실행: 계정 없는 빠른 터널(https://<네 낱말>.trycloudflare.com) → 원격 리스너(127.0.0.1:<포트>)만 가리킴.
    Host 는 remote.futsal.invalid 로 바꿔 보냄 · 자동 업데이트·진단 끔 · 기록 단계 info (debug 는 요청 헤더까지 찍으므로 쓰지 않음).
  - 지킴이: 'Registered tunnel connection' 이 나와야 켜짐 · 40초 안에 안 되면 http2 로 다시 · 갑자기 꺼지면 5초·15초·1분·5분 뒤 다시
    (한 시간에 6번까지) · Windows 는 Job Object 로 앱이 꺼지면 같이 꺼지게.
  - cloudflared 출력 줄에는 터널 주소가 들어 있으므로 기록(studio.log)에 남기지 않는다.
"""
import collections
import ctypes
import json
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import weakref
from pathlib import Path

import core
import updater

CF_VERSION = "2026.10.0"
CF_URL = "https://github.com/cloudflare/cloudflared/releases/download/{ver}/{name}"
# (파일 이름, 크기, sha256) — 2026-10-07 공식 릴리스 노트의 SHA256 목록과 맞춰 봄 (라이선스 Apache-2.0)
CF_ASSETS = {
    "amd64": ("cloudflared-windows-amd64.exe", 55365048, "86aee4017b26625cee8484c113558f48effa4cd47f7aa05fcf425604e5d2b23c"),
    "386": ("cloudflared-windows-386.exe", 37705864, "0630a8779e9823a1a3b091698b8e71874e0f7b205559219f52fdd301466b5546"),
}
HOST_HEADER = "remote.futsal.invalid"
URL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com")
REGISTERED = "Registered tunnel connection"
FIRST_WAIT, HTTP2_WAIT = 40, 60       # 연결이 잡히길 기다리는 시간(초)
BACKOFF = (5, 15, 60, 300)            # 갑자기 꺼졌을 때 다시 켜기까지
MAX_RESTARTS = 6                      # 한 시간에
CHECK_BACKOFF = (2, 4, 8)             # 자기 확인(/r/ping) 간격 — 너무 일찍 이름을 찾으면 '없음'이 한동안 기억될 수 있음
WIN = sys.platform == "win32"
BLOCKED_MSG = "연결하지 못했어요 · 회사·학교 인터넷은 막혀 있을 수 있어요"
MISSING_MSG = "연결 도구(cloudflared)를 준비하지 못했어요 · 인터넷 연결을 확인하고 다시 켜 주세요"
AV_MSG = "백신 프로그램이 연결 도구(cloudflared)를 막았을 수 있어요 · 백신에서 예외로 허용한 뒤 다시 켜 주세요"
_LIVE = weakref.WeakSet()  # 이 앱이 띄운 Tunnel (남은 pid 정리가 우리 것을 끄지 않게)


class Cancelled(Exception):
    pass


def _arch():
    m = platform.machine().lower()
    return "386" if m in ("x86", "i386", "i686") else "amd64"  # ARM64 Windows 는 amd64 를 에뮬레이션으로 돌림


def remote_dir():
    return core.ENGINE_HOME / "remote"


def bin_path():
    return core.ENGINE_HOME / "bin" / "cloudflared.exe"


def _version(exe):
    """→ "ok" · "other"(돌았지만 고정 판이 아님) · 실행하지 못한 까닭(OSError·시간 초과) — 백신이 막으면 여기서 OSError."""
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=20, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return e
    return "ok" if CF_VERSION in (r.stdout or "") + (r.stderr or "") else "other"


def version_ok(exe):
    return _version(exe) == "ok"


def _av_error(why):
    """확인(크기·sha256)을 마친 파일이 실행되지 않음 → 백신 안내 + Windows 오류 번호 (주소·경로 없음)."""
    code = getattr(why, "winerror", None) or getattr(why, "errno", None)
    return RuntimeError(AV_MSG + (f" (Windows 오류 {code})" if code else ""))


def _verified(p, size, sha):
    try:
        return p.is_file() and p.stat().st_size == size and updater.sha256(p) == sha
    except OSError:
        return False


def find():
    """쓸 수 있는 cloudflared 경로 (FUTSAL_CLOUDFLARED → 사용자 폴더 bin) · 없으면 None."""
    env = os.environ.get("FUTSAL_CLOUDFLARED")
    if env:
        return Path(env) if Path(env).is_file() else None
    p = bin_path()
    return p if p.is_file() and version_ok(p) else None


def ensure(progress=None, cancel=None, timeout=30):
    """cloudflared 준비 (처음 한 번 약 55MB). 고정 주소 · 크기·sha256 확인 뒤에만 제자리로. 실패하면 RuntimeError(한국어)."""
    p = find()
    if p:
        return p
    if os.environ.get("FUTSAL_CLOUDFLARED"):
        raise RuntimeError(MISSING_MSG)
    if not WIN:
        raise RuntimeError("원격 접속은 Windows PC에서만 켤 수 있어요")
    name, size, sha = CF_ASSETS[_arch()]
    dest = bin_path()
    if _verified(dest, size, sha):  # 고정 판 그대로인데 실행이 안 됨 → 다시 받지 않고(55MB) 백신 안내
        raise _av_error(_version(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")

    def hook(got, total):
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(got, total or size)
    try:
        updater.download(CF_URL.format(ver=CF_VERSION, name=name), tmp, hook, timeout=timeout)
        if tmp.stat().st_size != size:
            raise OSError("받은 파일 크기가 달라요")
        if updater.sha256(tmp) != sha:
            raise OSError("받은 파일 확인(sha256)에 실패했어요")
        updater._replace(tmp, dest)
    except Cancelled:
        _unlink(tmp)
        raise RuntimeError("연결 도구 받기를 멈췄어요") from None
    except updater.NET_ERRORS as e:
        _unlink(tmp)
        raise RuntimeError(f"{MISSING_MSG} ({updater._why(e)})") from None
    v = _version(dest)
    if v == "ok":
        return dest
    if v == "other":  # 돌기는 하는데 고정 판이 아님 (있을 수 없지만) → 지우고 다음에 다시
        _unlink(dest)
        raise RuntimeError(MISSING_MSG)
    raise _av_error(v)  # 확인한 파일을 백신이 격리했거나 실행을 막음 → 파일은 지우지 않음


def _unlink(p):
    try:
        Path(p).unlink()
    except OSError:
        pass


def config_file():
    """비어 있지 않은 설정 파일 (빈 파일은 오류 줄을 남김) · 자동 업데이트 끔."""
    d = remote_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / "cloudflared.yml"
    want = "no-autoupdate: true\n"
    try:
        if f.read_text(encoding="utf-8") == want:
            return f
    except OSError:
        pass
    f.write_text(want, encoding="utf-8")
    return f


def command(exe, port, http2=False):
    """cloudflared 실행 인자 (비밀·debug 없음)."""
    cmd = [str(exe), "tunnel", "--no-autoupdate", "--config", str(config_file()), "--url", f"http://127.0.0.1:{int(port)}",
           "--http-host-header", HOST_HEADER, "--metrics", "127.0.0.1:0", "--management-diagnostics=false", "--loglevel", "info"]
    if http2:
        cmd += ["--protocol", "http2"]
    return cmd


# ---------- Windows: 앱이 꺼지면 cloudflared 도 꺼지게 (Job Object) ----------

_JOB = None


def _job():
    """JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 인 Job Object 하나 (앱이 갑자기 꺼져도 핸들이 닫히며 자식이 같이 꺼짐)."""
    global _JOB
    if _JOB is not None or not WIN:
        return _JOB
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
            return None
        _JOB = (k32, h)
    except (OSError, AttributeError):
        _JOB = None
    return _JOB


def _assign(proc):
    j = _job()
    if j:
        try:
            j[0].AssignProcessToJobObject(j[1], int(proc._handle))
        except (OSError, AttributeError):
            pass


# ---------- 남은 프로세스 정리 (pid 파일 · 우리 실행 파일일 때만) ----------

def _pidfile():
    return remote_dir() / "tunnel.pid"


def _image_of(pid):
    if WIN:
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else None
        finally:
            k32.CloseHandle(h)
    try:  # 개발 PC: 가짜 cloudflared 는 파이썬 스크립트라 명령줄로 봄
        args = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
        return next((a.decode("utf-8", "replace") for a in args if a.endswith(b"cloudflared") or b"cloudflared" in a), None)
    except OSError:
        return None


def _same(a, b):
    try:
        return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))
    except (OSError, ValueError):
        return False


def _live_pids():
    out = set()
    for t in list(_LIVE):
        p = t.proc
        if p is not None and p.poll() is None:
            out.add(p.pid)
    return out


def kill_stale(exe):
    """지난번 앱이 갑자기 꺼져 남은 cloudflared 가 있으면 끔 — 그 pid 의 실행 파일이 우리 것일 때만, 지금 이 앱이 띄운 것은 빼고."""
    f = _pidfile()
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        pid = int(d["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if pid in _live_pids():
        return False
    _unlink(f)
    img = _image_of(pid)
    if not img or not (_same(img, exe) or _same(img, d.get("exe") or "")) or not _same(d.get("exe") or "", exe):
        return False
    try:
        os.kill(pid, signal.SIGTERM)  # Windows 는 TerminateProcess
        return True
    except OSError:
        return False


# ---------- 지킴이 ----------

def _self_check(url, timeout=10):
    try:
        req = urllib.request.Request(url + "/r/ping", headers=updater.UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(4096) or b"{}").get("api") == 1
    except Exception:  # noqa: BLE001
        return False


class Tunnel:
    """on_event(kind, url=None, error=None): 'on'(주소) · 'restarting' · 'error'(한국어 안내) · 'attention'(자꾸 끊김)."""

    def __init__(self, exe, port, on_event, check=None, first_wait=None, http2_wait=None, backoff=None, check_backoff=None, clock=time.monotonic):
        self.exe, self.port, self.on_event = exe, port, on_event
        self.check = check or (lambda url: _self_check(url))
        self.first_wait = FIRST_WAIT if first_wait is None else first_wait
        self.http2_wait = HTTP2_WAIT if http2_wait is None else http2_wait
        self.backoff = BACKOFF if backoff is None else backoff
        self.check_backoff = CHECK_BACKOFF if check_backoff is None else check_backoff
        self.clock = clock
        self.stopping = threading.Event()
        self.proc = None
        self.lock = threading.Lock()
        self.thread = None
        self.restarts = collections.deque()
        self.url = None
        self.state = "off"
        self.launches = 0

    def start(self):
        kill_stale(self.exe)
        _LIVE.add(self)
        self.thread = threading.Thread(target=self._run, daemon=True, name="tunnel")
        self.thread.start()

    def _emit(self, kind, **kw):
        self.state = kind
        try:
            self.on_event(kind, **kw)
        except Exception:  # noqa: BLE001
            pass

    def _launch(self, http2):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("TUNNEL_")}  # 환경 변수로 debug·토큰이 끼어들지 않게
        p = subprocess.Popen(command(self.exe, self.port, http2), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=env, **core.NO_WINDOW)
        _assign(p)
        self.launches += 1
        try:
            _pidfile().write_text(json.dumps({"pid": p.pid, "exe": str(self.exe)}), encoding="utf-8")
        except OSError:
            pass
        st = {"url": None, "registered": threading.Event(), "url_ev": threading.Event()}

        def reader():
            # 출력은 끝까지 읽어 버림 (파이프가 차서 멈추지 않게) · 줄은 기록하지 않음 (터널 주소가 들어 있음)
            for raw in iter(p.stdout.readline, b""):
                line = raw.decode("utf-8", "replace")
                if st["url"] is None:
                    m = URL_RE.search(line)
                    if m:
                        st["url"] = m.group(0)
                        st["url_ev"].set()
                if REGISTERED in line:
                    st["registered"].set()
            try:
                p.stdout.close()
            except OSError:
                pass
        threading.Thread(target=reader, daemon=True, name="tunnel-out").start()
        with self.lock:
            self.proc = p
        return p, st

    def _wait_registered(self, p, st, limit):
        end = self.clock() + limit
        while self.clock() < end and not self.stopping.is_set():
            if st["registered"].wait(0.2) and st["url"]:
                return True
            if p.poll() is not None:
                return False
        return False

    def _kill(self, p, wait=5):
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(wait)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        _unlink(_pidfile())

    def _run(self):
        http2 = False
        while not self.stopping.is_set():
            try:
                p, st = self._launch(http2)
            except OSError:
                return self._emit("error", error=MISSING_MSG)
            if not self._wait_registered(p, st, self.http2_wait if http2 else self.first_wait):
                self._kill(p)
                if self.stopping.is_set():
                    return
                if not http2:
                    http2 = True  # UDP(QUIC)가 막힌 곳: TCP(http2)로 한 번 더
                    continue
                return self._emit("error", error=BLOCKED_MSG)
            url = st["url"]
            for d in self.check_backoff:  # 바깥에서 실제로 닿는지 (안 닿아도 Cloudflare 가 '등록됨'이라 했으면 켬)
                if self.stopping.wait(d) or self.check(url):
                    break
            if self.stopping.is_set():
                self._kill(p)
                return
            self.url = url
            self._emit("on", url=url)
            while p.poll() is None and not self.stopping.wait(0.5):
                pass
            if self.stopping.is_set():
                self._kill(p)
                return
            _unlink(_pidfile())
            now = self.clock()
            self.restarts.append(now)
            while self.restarts and now - self.restarts[0] > 3600:
                self.restarts.popleft()
            if len(self.restarts) > MAX_RESTARTS:
                self._emit("attention")
                return self._emit("error", error="원격 연결이 자꾸 끊겨요 · PC 인터넷을 확인한 뒤 [다시 시도]를 눌러 주세요")
            self.url = None
            self._emit("restarting")
            if self.stopping.wait(self.backoff[min(len(self.restarts), len(self.backoff)) - 1]):
                return

    def stop(self, timeout=5):
        self.stopping.set()
        with self.lock:
            p = self.proc
        if p is not None:
            self._kill(p, wait=timeout)
        t = self.thread
        if t and t is not threading.current_thread():
            t.join(timeout)
        with self.lock:  # 멈추는 사이 막 띄운 프로세스가 있었으면 그것도
            p2 = self.proc
        if p2 is not None and p2 is not p:
            self._kill(p2, wait=timeout)
        self.state = "off"

    def alive(self):
        with self.lock:
            return self.proc is not None and self.proc.poll() is None
