"""휴대폰으로 보기(remote·tunnel) × Windows 대비(core·updater·app) — 두 기능을 합치며 맞물린 곳 시험 (D-034).

- 자식 프로세스: cloudflared(실행·--version)는 core.popen·core.run 으로만 → 앱이 꺼지면 같이 꺼지는 Job Object 하나에 한 번만
  (터널만의 Job Object 는 없앰 · 겹쳐 넣지 않음)
- 절전 막기: 작업(app.start_job)·편집점 찾기·원격('켜 둔 동안 항상')이 겹쳐도 한쪽이 놓을 때 다른 쪽 것이 풀리지 않음
  (Windows 는 SetThreadExecutionState 를 스레드마다 셈 → 스레드마다 따로 기억하는 가짜 kernel32 로 확인)
- 포트: 원격 리스너는 앱 화면 포트 창(8765~8804 · FUTSAL_PORT 면 그 포트만)을 쓰지 않음 · /api/ping 으로 리스너를 이 앱으로
  착각하지 않음 · 8765 를 못 써 다른 포트(.port)로 켠 앱에서도 /api/ping·'휴대폰으로 보기' 창·실행기 찾기가 됨
- 업데이트 다시 시작: 원격 끄기 → 실행기 띄우기 → 끝내기 순서 · 뒤로 미룬 구성요소 설치(.req_pending)는 이전 앱 프로세스가 끝난 뒤에
- 저장: remote.json 은 write_atomic(0600 · 실패하면 임시 파일을 지움) · 터널 pid·설정 파일 · 휴대폰 미리보기는 백신 잠금이면 기다림
- 기록·글자: 원격 리스너 오류가 studio-error.log(pythonw)로 가도 표·주제·터널 주소·서명·연결 코드가 남지 않음 ·
  반쪽 이모지가 든 휴대폰 요청·응답
- 합친 뒤 검토 반영: 업데이트 다시 시작은 도는 작업(휴대폰이 시킨 것 포함)이 끝난 뒤에 · 그 뒤로 새 작업을 받지 않음 ·
  실행기가 업데이트 마무리 동안 절전 막기 · 리스너도 Windows 에서 SO_REUSEADDR 끔 · 짝짓기 주제(fsp)도 지움 ·
  ntfy·터널 자기 확인·채널 RSS 도 updater.urlopen
인터넷은 쓰지 않는다. 실행: 저장소 폴더에서 python3 -m unittest tests.test_remote_windows
"""
import errno
import inspect
import io
import json
import os
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import textwrap
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import app  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
import remote  # noqa: E402
import strategy  # noqa: E402
import tunnel  # noqa: E402
import updater  # noqa: E402
from remote_fixture import ORIGIN, dev_env, http, make_home, pair_body, service  # noqa: E402

FAKE_SRC = REPO / "tests" / "fake_cloudflared.py"
NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def make_fake(d):
    """실행할 수 있는 가짜 cloudflared (이 파이썬으로)."""
    p = Path(d) / "cloudflared"
    p.write_text(f"#!{sys.executable}\n" + FAKE_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def wait_job_idle(timeout=30):
    return wait_for(lambda: not app.JOB["name"], timeout)


def locked(pred, secs, real=os.replace):
    """pred(src) 인 파일을 처음 건드린 뒤 secs 초 동안 os.replace 를 PermissionError(WinError 32 흉내)로 막는 가짜."""
    first = {}

    def fake(src, dst):
        if pred(Path(src)):
            t0 = first.setdefault(str(src), time.monotonic())
            if time.monotonic() - t0 < secs:
                e = PermissionError(errno.EACCES, "다른 프로세스가 파일을 사용 중이기 때문에 프로세스가 액세스 할 수 없습니다")
                e.winerror = 32
                raise e
        return real(src, dst)
    return fake


class Home(unittest.TestCase):
    """원격 서비스 하나 (임시 사용자 폴더 · 가짜 Bridge) + 앱 기록 파일은 임시 폴더로."""

    def setUp(self):
        self.home, cleanup = make_home()
        self.addCleanup(cleanup)
        p = mock.patch.object(app, "LOGFILE", self.home / "studio.log")
        p.start()
        self.addCleanup(p.stop)

    def svc(self, **kw):
        svc, fb = service(self.home, **kw)
        self.addCleanup(svc._close_listener)
        return svc, fb


# ---------- 1. 자식 프로세스: cloudflared 도 core 의 Job Object 하나로 ----------

@unittest.skipIf(sys.platform == "win32", "가짜 cloudflared 는 shebang 스크립트")
class TunnelChild(Home):
    def setUp(self):
        super().setUp()
        self.exe = make_fake(self.home)
        for p in (mock.patch.dict(os.environ, {"FAKE_CF_MODE": "ok"}),
                  mock.patch.object(core, "ENGINE_HOME", self.home / ".futsal-studio")):
            p.start()
            self.addCleanup(p.stop)

    def test_cloudflared_tracked_once_by_core(self):
        """--version 확인과 터널 실행 모두 core.run/popen → core.track 에 한 번씩 · 터널 모듈에 따로 Job Object 를 만들지 않음."""
        tracked, saved = [], []
        real_track, real_write = core.track, updater.write_atomic
        on = threading.Event()
        with mock.patch.object(core, "track", side_effect=lambda p: tracked.append(p) or real_track(p)), \
                mock.patch.object(updater, "write_atomic", side_effect=lambda p, d, **k: saved.append(Path(p).name) or real_write(p, d, **k)):
            self.assertTrue(tunnel.version_ok(self.exe))
            t = tunnel.Tunnel(self.exe, 1, lambda kind, **kw: kind == "on" and on.set(), check=lambda url: True, check_backoff=(0.01,))
            self.addCleanup(t.stop, 3)
            t.start()
            self.assertTrue(on.wait(15), "가짜 터널이 켜지지 않음")
        self.assertEqual(len(tracked), 2, "cloudflared --version · cloudflared tunnel")
        self.assertIs(tracked[1], t.proc)
        self.assertEqual(list(t.proc.args[1:2]), ["tunnel"])
        for name in ("_job", "_assign", "_JOB"):
            self.assertFalse(hasattr(tunnel, name), f"tunnel.{name}: 터널만의 Job Object 는 두지 않음 (core.track 과 겹침)")
        self.assertIn("cloudflared.yml", saved)
        self.assertIn("tunnel.pid", saved)
        self.assertEqual(json.loads(tunnel._pidfile().read_text(encoding="utf-8"))["pid"], t.proc.pid)
        t.stop(3)
        self.assertIsNotNone(t.proc.poll())

    def test_version_timeout_still_reported(self):
        """core.run 으로 바꿔도 시간 초과·실행 실패는 예전처럼 까닭(예외)으로 돌려줌 (백신 안내가 그대로 나오게)."""
        with mock.patch.object(core, "run", side_effect=subprocess.TimeoutExpired("cloudflared", 20)):
            self.assertIsInstance(tunnel._version(self.exe), subprocess.TimeoutExpired)
        with mock.patch.object(core, "run", side_effect=PermissionError(errno.EACCES, "액세스가 거부되었습니다")):
            self.assertIsInstance(tunnel._version(self.exe), PermissionError)
        self.assertEqual(tunnel._version(self.exe), "ok")


# ---------- 2. 절전 막기: 작업 · 편집점 찾기 · 원격이 겹칠 때 ----------

class FakeES:
    """Windows SetThreadExecutionState 흉내: 스레드마다 상태를 따로 기억하고 '앞 상태'를 돌려줌 ·
    어느 스레드든 ES_SYSTEM_REQUIRED 를 쥐고 있으면 PC 가 깨어 있음 (Windows 가 부른 스레드 수를 세는 것과 같음)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {}

    def __call__(self, v):
        with self.lock:
            tid = threading.get_ident()
            prev = self.state.get(tid, core.ES_CONTINUOUS)
            if v & core.ES_CONTINUOUS:
                self.state[tid] = v
            return prev

    def holders(self):
        with self.lock:
            return {t for t, v in self.state.items() if v & core.ES_SYSTEM_REQUIRED}

    def awake(self):
        return bool(self.holders())


class KeepAwake(Home):
    def setUp(self):
        super().setUp()
        self.assertTrue(wait_job_idle(), "앞 시험의 작업이 아직 돌고 있음")
        self.es = FakeES()
        p = mock.patch("ctypes.windll", types.SimpleNamespace(kernel32=types.SimpleNamespace(SetThreadExecutionState=self.es)), create=True)
        p.start()
        self.addCleanup(p.stop)
        self.s, self.fb = self.svc()
        self.stop = threading.Event()
        self.addCleanup(self.stop.set)

    def remote_on(self, keep="always"):
        self.s.store.data["settings"]["keepAwake"] = keep
        self.s.state = "on"
        th = threading.Thread(target=self.s._awake_loop, args=(self.stop, 0.02), daemon=True)
        th.start()
        self.addCleanup(th.join, 2)
        return th

    def test_remote_off_does_not_release_running_job(self):
        """원격이 쥔 뒤 작업 시작 → 원격을 꺼도(그 스레드가 놓음) 도는 작업은 계속 깨어 있음 → 작업이 끝나야 풂."""
        self.remote_on()
        self.assertTrue(wait_for(self.es.awake))
        gate = threading.Event()
        self.assertTrue(app.start_job("내보내기", lambda: gate.wait(20)))
        self.assertTrue(wait_for(lambda: len(self.es.holders()) == 2), "작업 스레드 + 원격 스레드")
        self.s.state = "off"
        self.assertTrue(wait_for(lambda: len(self.es.holders()) == 1), "원격 스레드만 놓음")
        time.sleep(0.1)
        self.assertTrue(self.es.awake(), "원격을 꺼도 내보내기는 계속 (일찍 풀리지 않음)")
        gate.set()
        self.assertTrue(wait_job_idle())
        self.assertFalse(self.es.awake(), "둘 다 놓으면 잠들 수 있음")

    def test_job_end_does_not_release_remote_always(self):
        """원격 '켜 둔 동안 항상' 이 쥔 동안 작업이 시작했다 끝나도 PC 는 계속 깨어 있음 → 원격을 끄면 풂."""
        self.remote_on()
        self.assertTrue(wait_for(self.es.awake))
        self.assertTrue(app.start_job("영상 검수", lambda: time.sleep(0.1)))
        self.assertTrue(wait_job_idle())
        time.sleep(0.1)
        self.assertTrue(self.es.awake(), "작업이 끝나도 원격이 쥔 것은 그대로 (휴대폰에서 다음 작업을 시킬 수 있게)")
        self.s.state = "off"
        self.assertTrue(wait_for(lambda: not self.es.awake()))

    def test_job_mode_and_nested_analysis(self):
        """'작업할 때만' + 작업 안의 편집점 찾기(_analysis_session): 편집점 찾기가 끝나도 작업이 도는 동안은 깨어 있음."""
        self.remote_on(keep="job")
        time.sleep(0.1)
        self.assertFalse(self.es.awake(), "작업할 때만: 작업이 없으면 쥐지 않음")
        seen = {}

        def work():
            with core._analysis_session():
                seen["in"] = self.es.awake()
            seen["after"] = self.es.awake()
            seen["mine"] = threading.get_ident() in self.es.holders()
        self.fb.job_state["name"] = "편집점 찾기"  # 휴대폰 쪽이 보는 작업 모습 (가짜 Bridge)
        self.assertTrue(app.start_job("편집점 찾기", work))
        self.assertTrue(wait_job_idle())
        self.assertEqual(seen, {"in": True, "after": True, "mine": True})
        self.fb.job_state["name"] = None
        self.assertTrue(wait_for(lambda: not self.es.awake()), "작업이 끝나고 원격도 놓으면 잠들 수 있음")

    def test_remote_never_calls_kernel32_itself(self):
        """원격은 절전 막기를 core.keep_awake 로만 (SetThreadExecutionState 를 부르는 곳은 updater.awake_state 하나 ·
        core._keep_awake 가 그것 · 실행기의 업데이트 마무리도 같은 것)."""
        src = (REPO / "remote.py").read_text(encoding="utf-8")
        self.assertIn("core.keep_awake()", src)
        for name in ("remote.py", "tunnel.py", "core.py", "app.py"):
            self.assertNotIn("kernel32.SetThreadExecutionState", (REPO / name).read_text(encoding="utf-8"), name)
        self.assertEqual((REPO / "updater.py").read_text(encoding="utf-8").count("kernel32.SetThreadExecutionState"), 1)
        self.assertIs(core._keep_awake, updater.awake_state)


# ---------- 3. 포트: 원격 리스너 · 다른 포트로 켠 앱 · 실행기 ----------

class Ports(Home):
    def test_window_matches_app_and_launcher(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("FUTSAL_PORT", None)
            w = remote.app_ports()
            for p in range(8765, 8800):  # app: 8765 + PORT_FALLBACK(8766~8799)
                self.assertIn(p, w)
            for p in range(8766, 8805):  # updater._app_ports 가 받아 주는 .port
                self.assertIn(p, w)
            os.environ["FUTSAL_PORT"] = "9123"
            self.assertEqual(set(remote.app_ports()), {9123}, "포트를 정해 켜면 그 포트 하나만 (앱도 다른 포트로 가지 않음)")

    def test_listener_never_takes_app_port(self):
        s, _ = self.svc()
        with mock.patch.dict(os.environ, {"FUTSAL_REMOTE_PORT": "8770"}):
            os.environ.pop("FUTSAL_PORT", None)
            with self.assertRaises(RuntimeError):
                s._new_listener()
        real, made = remote.RemoteServer, []

        def factory(addr, svc):
            srv = real(addr, svc)
            made.append(srv)
            return srv
        with mock.patch.dict(os.environ, {}), mock.patch.object(remote, "RemoteServer", side_effect=factory), \
                mock.patch.object(remote, "app_ports", side_effect=lambda: {made[0].server_address[1]}):
            os.environ.pop("FUTSAL_REMOTE_PORT", None)
            srv = s._new_listener()
        self.addCleanup(remote._close_server, srv)
        self.assertEqual(len(made), 2, "임의 포트가 앱 포트 창에 걸리면 닫고 다시")
        self.assertIs(srv, made[1])
        self.assertEqual(made[0].socket.fileno(), -1, "걸린 쪽은 닫음")

    def test_listener_is_not_mistaken_for_app(self):
        """두 번째 실행(app._ours)·실행기(updater._app_running)가 원격 리스너 포트를 물어도 '이 앱'이 아님 (개발 모드 포함)."""
        s, _ = self.svc()
        s._open_listener()
        port = s.port()
        self.assertFalse(app._ours(port, 2))
        with mock.patch.object(updater, "_app_ports", return_value=[port]):
            self.assertFalse(updater._app_running(self.home))
        with mock.patch.dict(os.environ, dev_env()):
            self.assertFalse(app._ours(port, 2))

    def test_fallback_port_serves_ping_and_remote_panel(self):
        """8765(여기서는 임의 포트)를 다른 프로그램이 씀 → 다음 포트로 켜고 .port 에 남김 → 그 포트에서 /api/ping 은 이 앱 ·
        '휴대폰으로 보기' 창(/api/remote · 켜기)이 열리고 · 실행기도 그 포트의 앱을 찾음."""
        work = self.home / "작업"
        work.mkdir()
        base = free_port()
        for p in (mock.patch.object(app, "PORT", base), mock.patch.object(app, "PORT_FALLBACK", range(base + 1, base + 6)),
                  mock.patch.object(core, "WORK", work)):
            p.start()
            self.addCleanup(p.stop)

        class Other(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"other program")
            do_POST = do_GET
        other = ThreadingHTTPServer(("127.0.0.1", base), Other)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("FUTSAL_PORT", None)
            srv = app._bind()
        self.assertIsNotNone(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = app.PORT
        self.assertNotEqual(port, base)
        self.assertEqual((work / ".port").read_text(encoding="utf-8"), str(port))
        s, _ = self.svc()
        p = mock.patch.object(remote, "SVC", s)
        p.start()
        self.addCleanup(p.stop)

        def call(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST" if data is not None else "GET",
                                         headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
            with NOPROXY.open(req, timeout=10) as r:
                return json.loads(r.read())
        self.assertEqual(call("/api/ping")["app"], app.APP_ID)
        st = call("/api/remote")
        self.assertEqual(st["state"], "off")
        self.assertIn("site", st)
        with mock.patch.object(updater, "_app_ports", return_value=[base, port]):  # 실행기: 8765 + .port (창 안의 번호만 · 위 시험)
            self.assertTrue(updater._app_running(self.home), "8765 의 다른 프로그램은 건너뛰고 .port 의 이 앱을 찾음")
        if not remote.crypto_ok():
            return
        with mock.patch.dict(os.environ, dev_env(FUTSAL_REMOTE_PORT="")):
            self.assertTrue(call("/api/remote/on", {})["ok"])
            self.assertTrue(wait_for(lambda: s.state == "on"))
            self.assertNotIn(s.port(), remote.app_ports())
            pair = call("/api/remote/pair", {})["pair"]
            self.assertIn("#pair=", pair["link"])
            self.assertTrue(pair["qr"])
            call("/api/remote/off", {})


# ---------- 4. 업데이트 다시 시작: 원격 끄기 → 실행기 → 구성요소 설치는 이전 앱이 끝난 뒤 ----------

class Restart(unittest.TestCase):
    def test_remote_off_before_launcher(self):
        order = []
        with mock.patch.object(remote.SVC, "shutdown", side_effect=lambda *a, **k: order.append("원격 끄기")), \
                mock.patch.object(app.subprocess, "Popen", side_effect=lambda cmd, **kw: order.append(("실행기", cmd, kw["env"]))), \
                mock.patch.object(app, "_quit", side_effect=lambda: order.append("끝내기")), mock.patch.object(app, "log"):
            app.restart()
        self.assertEqual([x if isinstance(x, str) else x[0] for x in order], ["원격 끄기", "실행기", "끝내기"])
        cmd, env = order[1][1], order[1][2]
        self.assertEqual(cmd[1:3], [str(core.APP_DIR / "updater.py"), "--launch"])
        self.assertEqual(env["FUTSAL_RESTART"], "1")
        self.assertEqual(env["FUTSAL_OLD_PID"], str(os.getpid()), "새 실행기가 이 프로세스가 끝나길 기다릴 수 있게")

    def app_dir(self):
        tmp = Path(tempfile.mkdtemp(prefix="다시 시작 시험 "))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        a = tmp / "앱"
        rb = a / updater.ROLLBACK / "v1.0"
        (rb / "files").mkdir(parents=True)
        (a / "config.json").write_text(json.dumps({"workspace": str(tmp / "작업")}), encoding="utf-8")
        (a / "version.txt").write_text("1.1\n", encoding="utf-8")
        (a / "requirements.txt").write_text("numpy\n", encoding="utf-8")
        (rb / "files" / "version.txt").write_text("1.0\n", encoding="utf-8")
        updater._write_json(rb / "rollback.json", {"from": "1.0", "to": "1.1", "backup": ["version.txt"], "added": []})
        updater._write_json(a / updater.PENDING, {"from": "1.0", "to": "1.1", "state": "installed", "rollback": "v1.0"})
        updater._write_json(a / updater.REQ_PENDING, {"to": "1.1"})
        return a

    def old_app(self, secs):
        """끝나 가는 '이전 앱' 프로세스 (부모인 이 시험이 바로 거둬 감 → 좀비로 남지 않게)."""
        p = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({secs})"])
        threading.Thread(target=p.wait, daemon=True).start()
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def test_deferred_pip_waits_for_old_app_to_exit(self):
        """켜져 있던 앱이 불러 둔 .pyd 때문에 미룬 설치: 이전 앱 프로세스가 아직 끝나지 않았으면 끝날 때까지 기다렸다가 pip."""
        a = self.app_dir()
        old = self.old_app(1.2)
        seen = []

        def fake_run(cmd, **kw):
            seen.append((time.monotonic(), updater._pid_alive(old.pid)))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        t0 = time.monotonic()
        with mock.patch.dict(os.environ, {"FUTSAL_RESTART": "1", "FUTSAL_OLD_PID": str(old.pid)}), \
                mock.patch.object(updater.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(updater, "_import_check", return_value=(True, "")):
            self.assertEqual(updater.check(a, log=lambda m: None), "ok")
        self.assertEqual(len(seen), 1)
        self.assertGreater(seen[0][0] - t0, 0.9, "이전 앱이 끝나기 전에 pip 를 시작하지 않음")
        self.assertFalse(seen[0][1], "pip 를 시작할 때 이전 앱은 이미 끝남")
        self.assertFalse((a / updater.REQ_PENDING).exists())

    def test_wait_for_old_app_is_bounded(self):
        """이전 앱이 끝내 안 끝나도(멈춤) 정해진 시간만 기다리고 설치를 이어 감 (예전과 같은 결과 · 실패하면 되돌림)."""
        a = self.app_dir()
        old = self.old_app(30)
        t0 = time.monotonic()
        with mock.patch.dict(os.environ, {"FUTSAL_RESTART": "1", "FUTSAL_OLD_PID": str(old.pid)}), \
                mock.patch.object(updater, "OLD_APP_WAIT", 0.5), \
                mock.patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                mock.patch.object(updater, "_import_check", return_value=(True, "")):
            self.assertEqual(updater.check(a, log=lambda m: None), "ok")
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(run.call_count, 1)

    def test_no_old_pid_no_wait(self):
        self.assertFalse(updater._pid_alive(None))
        self.assertFalse(updater._pid_alive("x"))
        self.assertTrue(updater._pid_alive(os.getpid()))
        t0 = time.monotonic()
        updater._wait_gone(None, 5)
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_launcher_keeps_pc_awake_while_finishing_update(self):
        """이전 앱이 끝나면 그 앱(작업·원격 '켜 둔 동안 항상')이 쥐던 절전 막기가 풀림 → 실행기가 이전 앱 기다리기·미룬 구성요소 설치
        (몇 분)·새 버전 확인 동안 이어 쥠 · 끝나면 놓음 (새 앱의 작업·원격이 다시 쥠)."""
        es = FakeES()
        a = self.app_dir()
        seen = {}

        def fake_run(cmd, **kw):
            seen["pip"] = (es.awake(), threading.get_ident() in es.holders())
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def fake_check(app_dir, python=None):
            seen["import"] = es.awake()
            return True, ""
        with mock.patch("ctypes.windll", types.SimpleNamespace(kernel32=types.SimpleNamespace(SetThreadExecutionState=es)), create=True), \
                mock.patch.dict(os.environ, {"FUTSAL_RESTART": "1", "FUTSAL_OLD_PID": "0"}), \
                mock.patch.object(updater, "_wait_gone", side_effect=lambda pid, secs: seen.setdefault("wait", es.awake())), \
                mock.patch.object(updater.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(updater, "_import_check", side_effect=fake_check):
            self.assertEqual(updater.check(a, log=lambda m: None), "ok")
        self.assertEqual(seen, {"wait": True, "pip": (True, True), "import": True})
        self.assertFalse(es.awake(), "확인이 끝나면 놓음")


# ---------- 5. 저장: remote.json · 휴대폰 미리보기 ----------

class Saves(Home):
    def test_remote_json_atomic_private_and_cleans_tmp(self):
        s, _ = self.svc()
        s.store.save()
        path = s.store.path
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, "기기 열쇠가 든 파일은 나만 읽게")
        before = path.read_bytes()
        s.store.data["seq"] = 99
        with mock.patch.object(updater, "replace_retry", side_effect=PermissionError(errno.EACCES, "다른 프로세스가 사용 중")):
            with self.assertRaises(PermissionError):
                s.store.save()
        self.assertEqual(path.read_bytes(), before, "바꿔 끼우기에 실패해도 예전 파일은 그대로")
        self.assertEqual([p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")], [], "열쇠가 든 임시 파일을 남기지 않음")
        with mock.patch.object(updater.os, "replace", locked(lambda p: p.name.startswith("remote.json."), 0.3)):
            s.store.save()  # 백신이 잠깐 잡아도 기다렸다가 저장
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["seq"], 99)

    def test_phone_preview_waits_for_antivirus(self):
        """휴대폰 '작은 미리보기'(editor.out_preview)도 편집실 미리보기 파일처럼 막 만든 파일이 잠겨 있으면 기다림."""
        w = self.home / "작업"
        for d in ("out", "analysis"):
            (w / d).mkdir(parents=True)
        (w / "out" / "완성본.mp4").write_bytes(b"\0" * 64)
        for p in (mock.patch.object(core, "OUT", w / "out"), mock.patch.object(core, "ANALYSIS", w / "analysis")):
            p.start()
            self.addCleanup(p.stop)
        out = editor.out_preview_path("완성본.mp4")

        class P:
            returncode = 0
            stdout = io.BytesIO(b"out_time_us=1000000\n")

            def wait(self):
                out.with_suffix(".part.mp4").write_bytes(b"PREVIEW")
        with mock.patch.object(core, "popen", return_value=P()), mock.patch.object(editor, "probe", return_value={"duration": 2, "hdr": None}), \
                mock.patch.object(updater.os, "replace", locked(lambda p: p.name == "preview.part.mp4", 0.5)):
            editor.out_preview("완성본.mp4", lambda *a: None)
        self.assertEqual(out.read_bytes(), b"PREVIEW")

    def test_phone_preview_lock_never_cleared(self):
        w = self.home / "작업"
        for d in ("out", "analysis"):
            (w / d).mkdir(parents=True)
        (w / "out" / "a.mp4").write_bytes(b"\0" * 64)
        for p in (mock.patch.object(core, "OUT", w / "out"), mock.patch.object(core, "ANALYSIS", w / "analysis")):
            p.start()
            self.addCleanup(p.stop)
        out = editor.out_preview_path("a.mp4")

        class P:
            returncode = 0
            stdout = io.BytesIO(b"")

            def wait(self):
                out.with_suffix(".part.mp4").write_bytes(b"X")
        with mock.patch.object(core, "popen", return_value=P()), mock.patch.object(editor, "probe", return_value={"duration": 2, "hdr": None}), \
                mock.patch.object(updater, "replace_retry", side_effect=PermissionError(errno.EACCES, "잠김")):
            with self.assertRaisesRegex(RuntimeError, "백신"):
                editor.out_preview("a.mp4", lambda *a: None)
        self.assertFalse(out.with_suffix(".part.mp4").exists())


# ---------- 6. 기록·글자: studio-error.log 에 비밀이 남지 않음 · 반쪽 이모지 ----------

TICKET = "AbCdEf0123456789_-zyXW"
TOPIC = "fsb" + "0a1b2c3d4e5f" * 2
URL = "https://quiet-river-blue-mango.trycloudflare.com"
AUTH = "FSR2 0123456789abcdef.1800000000.bm9uY2Vub25jZW5vbmNl.c2lnbmF0dXJlc2lnbmF0dXJl"
SECRETS = (TICKET, TOPIC, "quiet-river-blue-mango", "0123456789abcdef.1800000000", "PAIR-CODE7")


class Secrets(Home):
    def assertClean(self, text):
        for s in SECRETS:
            self.assertNotIn(s, text)

    def test_redact_and_scrub(self):
        raw = f"표 /r/m/{TICKET} · 주제 {TOPIC} · {URL}/r/status · {AUTH} · https://mulgyeol.kr/futsal#pair=PAIR-CODE7&u=quiet-river-blue-mango"
        self.assertClean(remote.redact(raw))
        self.assertClean(remote.scrub(raw))
        self.assertIn("/r/m/", remote.redact(raw))
        self.assertEqual(remote.redact("내보내기 · 완성본.mp4"), "내보내기 · 완성본.mp4")

    def test_listener_error_goes_to_error_log_without_secrets(self):
        """pythonw 에서는 오류 출력이 studio-error.log(app._StampedErr)로 감 — 원격 리스너가 요청을 처리하다 난 오류에도 비밀이 없음."""
        with mock.patch.dict(os.environ, dev_env()):
            s, _ = self.svc()
            s._open_listener()
            port = s.port()
            buf = io.StringIO()
            boom = RuntimeError(f"표 /r/m/{TICKET} 주제 {TOPIC} 주소 {URL} {AUTH}")
            with mock.patch.object(sys, "stderr", app._StampedErr(buf)), mock.patch.object(s, "r_ping", side_effect=boom):
                st, _, body = http(port, "GET", "/r/ping")
                try:
                    raise ValueError(f"/r/m/{TICKET} {URL}")
                except ValueError:
                    s.listener.handle_error(None, ("127.0.0.1", 50123))  # 처리 밖으로 나온 오류 (socketserver 기본은 주소·추적을 그대로 찍음)
        self.assertEqual(st, 500)
        self.assertNotIn(b"RuntimeError", body, "오류 글은 응답에 넣지 않음")
        out = buf.getvalue()
        self.assertIn("RuntimeError", out, "무슨 오류인지는 남김")
        self.assertIn("ValueError", out)
        self.assertClean(out)
        self.assertNotIn("50123", out)

    def test_phone_hang_up_is_not_an_error(self):
        """휴대폰이 영상을 앞뒤로 옮기며 연결을 끊음 — Windows 는 ConnectionAbortedError(10053): 오류 기록(studio-error.log)에 쌓지 않음
        (로컬 서버 _file·_send 와 같게)."""
        with mock.patch.dict(os.environ, dev_env()):
            s, _ = self.svc()
            s._open_listener()
            buf = io.StringIO()
            e = ConnectionAbortedError(10053, "현재 연결은 사용자의 호스트 시스템의 소프트웨어의 의해 중단되었습니다")
            with mock.patch.object(sys, "stderr", app._StampedErr(buf)), mock.patch.object(s, "r_ping", side_effect=e):
                try:
                    http(s.port(), "GET", "/r/ping")
                except (OSError, urllib.error.URLError):
                    pass  # 응답 없이 닫힘
        self.assertEqual(buf.getvalue(), "")

    def test_half_emoji_from_phone_pairs_and_saves(self):
        """휴대폰이 보낸 이름에 반쪽 이모지(\\ud83d) → 짝짓기·remote.json 저장이 UnicodeEncodeError 로 멈추지 않음 (서버의 모든 POST 와 같게)."""
        if not remote.crypto_ok():
            self.skipTest("pycryptodomex 없음")
        with mock.patch.dict(os.environ, dev_env()):
            s, _ = self.svc()
            s._open_listener()
            p = s.pairing.create()
            body = json.dumps(pair_body(p, name="iPhone 🔥\ud83d")).encode("ascii")
            st, _, out = http(s.port(), "POST", "/r/pair", raw=body, headers={"Origin": ORIGIN})
        self.assertEqual(st, 200, out[:200])
        saved = json.loads(s.store.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"][0]["name"], "iPhone 🔥�")

    def test_half_emoji_in_reply_still_answers(self):
        with mock.patch.dict(os.environ, dev_env()):
            s, _ = self.svc()
            s._open_listener()
            with mock.patch.object(s, "r_ping", return_value={"api": 1, "name": "보관함 영상 🔥\ud83d"}):
                st, _, out = http(s.port(), "GET", "/r/ping")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(out)["name"], "보관함 영상 🔥\ud83d")


# ---------- 7. 합친 뒤 검토 반영 ----------

class ReviewRestart(Home):
    """업데이트 다시 시작: 휴대폰이 시킨 작업이 돌고 있으면 끝난 뒤에 · 다시 시작을 정한 뒤로는 새 작업(PC·휴대폰)을 받지 않음."""

    def setUp(self):
        super().setUp()
        self.assertTrue(wait_job_idle(), "앞 시험의 작업이 아직 돌고 있음")
        self.addCleanup(app.RESTARTING.clear)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.port = self.srv.server_address[1]
        p = mock.patch.object(app, "PORT", self.port)
        p.start()
        self.addCleanup(p.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def post(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        try:
            with NOPROXY.open(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_restart_waits_for_phone_job_then_refuses_new_jobs(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.assertTrue(app.start_job("작은 미리보기 만들기", lambda: gate.wait(20), by="휴대폰 · iPhone · Safari"))
        with mock.patch.object(app, "restart") as rs:
            st, body = self.post("/api/restart")
            self.assertEqual((st, body.get("busy"), body.get("error")), (409, True, app.RESTART_WAIT_MSG))
            self.assertFalse(app.RESTARTING.is_set(), "도는 작업이 있으면 다시 시작을 정하지 않음 (화면이 잠시 뒤 다시 부름)")
            time.sleep(0.8)
            rs.assert_not_called()
            self.assertEqual(app.JOB["name"], "작은 미리보기 만들기", "휴대폰 작업을 끊지 않음")
            gate.set()
            self.assertTrue(wait_job_idle())
            st, body = self.post("/api/restart")
            self.assertEqual((st, body.get("ok")), (200, True))
            self.assertTrue(app.RESTARTING.is_set())
            self.assertFalse(app.start_job("내보내기", lambda: None), "다시 시작을 정한 뒤 PC 작업은 받지 않음")
            self.assertFalse(app.start_job("편집점 찾기", lambda: None, by="휴대폰 · iPhone · Safari"),
                             "휴대폰 작업도 받지 않음 (remote._go 가 BUSY_MSG 409)")
            self.assertTrue(wait_for(lambda: rs.called, 10))
        self.assertIsNone(app.JOB["name"])

    def test_launcher_failure_keeps_app_taking_jobs(self):
        app.RESTARTING.set()
        with mock.patch.object(remote.SVC, "shutdown"), mock.patch.object(app.subprocess, "Popen", side_effect=OSError("실행 파일 없음")), \
                mock.patch.object(app, "_quit") as quit_, mock.patch.object(app, "log") as log:
            app.restart()
        quit_.assert_not_called()
        self.assertFalse(app.RESTARTING.is_set(), "실행기를 못 띄우면 계속 작업을 받음")
        self.assertIn("다시 시작하지 못했어요", log.call_args[0][0])


class ReviewListenerAndSecrets(Home):
    def test_listener_reuse_rule_matches_app(self):
        """app._Server 와 같은 규칙 (D-030): Windows 에서는 SO_REUSEADDR 를 끔 → 다른 프로세스가 듣는 FUTSAL_REMOTE_PORT 를 같이 잡지 않음."""
        self.assertEqual(remote.RemoteServer.allow_reuse_address, app._Server.allow_reuse_address)
        src = textwrap.dedent(inspect.getsource(remote.RemoteServer))
        for plat, want in (("win32", False), ("linux", True)):
            ns = dict(vars(remote))
            with mock.patch.object(sys, "platform", plat):
                exec(compile(src, "remote.py", "exec"), ns)  # noqa: S102 — 클래스 몸통을 그 운영체제로 다시 계산
            self.assertIs(bool(ns["RemoteServer"].allow_reuse_address), want, plat)

    def test_busy_fixed_port_is_not_shared(self):
        """정해 둔 원격 포트를 다른 프로그램이 듣고 있으면 같이 잡지 않고 '열지 못했어요' (Windows 는 위 규칙이 있어야 이렇게 됨)."""
        with socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen(1)
            busy = other.getsockname()[1]
            s, _ = self.svc()
            with mock.patch.dict(os.environ, {"FUTSAL_REMOTE_PORT": str(busy)}), mock.patch.object(remote.time, "sleep"), \
                    self.assertRaisesRegex(RuntimeError, "열지 못했어요"):
                s._new_listener()

    def test_pair_topic_redacted(self):
        topic = remote.derive_pair("ABCD2345EFGH")[0]
        self.assertRegex(topic, r"^fsp[0-9a-f]{24}$")
        for f in (remote.redact, remote.scrub):
            out = f(f"만남 주제 {topic} · 비콘 {TOPIC}")
            self.assertNotIn(topic, out)
            self.assertNotIn(TOPIC, out)
            self.assertIn("fs…", out)

    def test_phone_https_calls_use_updater_urlopen(self):
        """ntfy 보내기·마지막 비콘·터널 자기 확인·채널 RSS 도 updater.urlopen (Python 3.13+ 에서 백신 'HTTPS 검사' 인증서도 받음 · D-029)."""
        seen = []
        rss = (REPO / "tests" / "fixtures" / "strategy" / "rss_own.xml").read_bytes()

        class Resp:
            def __init__(self, raw):
                self.raw = raw

            def read(self, n=-1):
                return self.raw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout):
            seen.append(req.full_url)
            return Resp(rss if "feeds" in req.full_url else b'{"api": 1}')
        s, _ = self.svc()
        with mock.patch.object(updater, "urlopen", side_effect=fake), \
                mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("urllib.request.urlopen 을 바로 부름")):
            self.assertTrue(s.pub._post("https://ntfy.example/post", b"a", {}))
            self.assertTrue(s.pub.post_now("https://ntfy.example/last", b"b", {}))
            self.assertTrue(tunnel._self_check("https://quiet-river.trycloudflare.com"))
            r, err = strategy.fetch_rss("UC" + "a" * 22)
        self.assertIsNone(err)
        self.assertTrue(r["entries"])
        for want in ("https://ntfy.example/post", "https://ntfy.example/last", "https://quiet-river.trycloudflare.com/r/ping", "feeds"):
            self.assertTrue(any(want in u for u in seen), want)

    def test_cert_failure_says_antivirus_without_secrets(self):
        s, fb = self.svc()
        err = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))
        with mock.patch.object(updater, "urlopen", side_effect=err), mock.patch.object(remote, "RETRY_DELAYS", ()):
            self.assertFalse(s.pub._post(f"https://ntfy.example/{TOPIC}", b"a", {}))
        lines = [x for x in fb.lines if "휴대폰 알림을 보내지 못했어요" in x]
        self.assertEqual(len(lines), 1)
        self.assertIn("HTTPS 검사", lines[0])
        self.assertNotIn(TOPIC, "\n".join(fb.lines))


if __name__ == "__main__":
    unittest.main()
