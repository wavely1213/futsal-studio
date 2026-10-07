"""원격 접속 켜기·끄기 수명(remote.Service + tunnel.Tunnel) 시험 — 가짜 cloudflared · 개발 모드 아님
— 저장소 폴더에서 python3 -m unittest tests.test_remote_lifecycle

검토에서 나온 경쟁(race) PoC 를 그대로 옮겼다 (secreview_remote/poc_double_start.py · poc_race_off.py · rv_int_remote/race_onoff.py):
켜기 → 그만두기 → 켜기를 빨리 누르거나, 리스너를 여는 사이에 [끄기]를 눌러도 cloudflared 는 많아야 하나, 끄면 0, enabled 는 끈 대로.
그 밖에: 앱 끄기는 상태와 상관없이 모두 끔 · 남은 pid 정리는 우리가 띄운 것을 안 끔 · 터널 오류 뒤 저절로 다시 · 백신 막힘 안내.
"""
import json
import os
import socket
import stat
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import remote  # noqa: E402
import tunnel  # noqa: E402
from remote_fixture import FakeBridge, FakeNtfy, make_home, pair_device  # noqa: E402

FAKE_SRC = Path(__file__).resolve().parent / "fake_cloudflared.py"


def make_fake(d):
    p = Path(d) / "cloudflared"
    p.write_text(f"#!{sys.executable}\n" + FAKE_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def alive(pid):
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return st.rsplit(")", 1)[1].split()[0] != "Z"


def wait_for(pred, timeout=10.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


@unittest.skipIf(sys.platform == "win32", "가짜 cloudflared 는 shebang 스크립트")
class LifecycleBase(unittest.TestCase):
    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.exe = make_fake(self.home)
        self.log = self.home / "launches.jsonl"
        self.ntfy = FakeNtfy()
        self.addCleanup(self.ntfy.close)
        env = {"FAKE_CF_LOG": str(self.log), "FAKE_CF_MODE": "ok", "FUTSAL_CLOUDFLARED": str(self.exe)}
        for p in (mock.patch.dict(os.environ, env), mock.patch.object(core, "ENGINE_HOME", self.home / ".futsal-studio"),
                  mock.patch.object(tunnel, "_self_check", lambda url, timeout=10: True), mock.patch.object(tunnel, "CHECK_BACKOFF", (0.01,))):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("FUTSAL_REMOTE_DEV", None)
        self.fb = FakeBridge()
        self.svc = remote.Service(home=self.home / ".futsal-studio", ntfy=self.ntfy.url)
        self.svc.init(self.fb.bridge())
        self.addCleanup(self.kill_all)

    def kill_all(self):
        try:
            self.svc.turn_off()
        except Exception:  # noqa: BLE001
            pass
        for pid in self.pids():
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    def launches(self):
        try:
            return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]
        except OSError:
            return []

    def pids(self):
        return [x["pid"] for x in self.launches() if alive(x["pid"])]

    def slow_ensure(self, secs):
        real = tunnel.ensure
        calls = []

        def slow(progress=None, cancel=None, timeout=30):  # 받기가 멈춘 듯 / 백신이 --version 을 붙잡음 — 그만두기를 안 봄
            calls.append(time.time())
            time.sleep(secs)
            return real(progress, cancel, timeout)
        p = mock.patch.object(tunnel, "ensure", slow)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def enabled(self):
        return json.loads((self.home / ".futsal-studio" / "remote.json").read_text(encoding="utf-8"))["enabled"]


class RaceTests(LifecycleBase):
    def test_on_off_on_while_ensure_stalls_leaves_one_tunnel(self):
        """PoC poc_double_start / race_onoff: 켜기 → 0.3초 그만두기 → 0.2초 다시 켜기 (첫 시도는 ensure 안) → cloudflared 는 하나뿐."""
        calls = self.slow_ensure(1.0)
        self.svc.turn_on()
        time.sleep(0.3)
        self.svc.turn_off("user")
        time.sleep(0.2)
        self.svc.turn_on()
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        time.sleep(1.5)  # 첫 시도의 ensure 가 끝난 뒤에도
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.pids()), 1, self.launches())
        self.assertEqual(len(self.launches()), 1)  # 옛 시도는 터널을 아예 안 띄움
        self.assertEqual(self.pids(), [self.svc.tun.proc.pid])
        self.assertTrue(self.enabled())
        self.svc.turn_off("user")
        self.assertTrue(wait_for(lambda: not self.pids(), 5))
        time.sleep(1.0)  # 남은 것이 스스로 다시 켜지지 않는지
        self.assertEqual(self.pids(), [])
        self.assertEqual(self.svc.state, "off")
        self.assertFalse(self.enabled())

    def test_off_during_listener_open_keeps_off(self):
        """PoC poc_race_off: 리스너를 연 바로 그때 [끄기] → 꺼짐 · enabled False(다음 앱 시작에 안 켜짐) · 터널·리스너 없음 · 터널 스레드 TypeError 없음."""
        orig = self.svc._new_listener
        errors = []
        old_hook = threading.excepthook
        threading.excepthook = lambda a: errors.append(a)
        self.addCleanup(lambda: setattr(threading, "excepthook", old_hook))

        made = []

        def racy():
            srv = orig()
            made.append(srv)
            t = threading.Thread(target=self.svc.turn_off, args=("user",))
            t.start()
            t.join()
            return srv
        self.svc._new_listener = racy
        self.svc.turn_on()
        time.sleep(2.0)
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].socket.fileno(), -1)  # 넘겨주지 못한 리스너도 닫힘 (포트가 남지 않음)
        self.assertEqual(self.svc.state, "off")
        self.assertFalse(self.enabled())
        self.assertIsNone(self.svc.tun)
        self.assertIsNone(self.svc.listener)
        self.assertEqual(self.pids(), [])
        self.assertEqual(self.launches(), [])
        self.assertEqual(errors, [])
        self.svc.shutdown(timeout=1)
        self.assertEqual(self.pids(), [])

    def test_many_quick_toggles_never_two_tunnels(self):
        self.slow_ensure(0.3)
        for _ in range(4):
            self.svc.turn_on()
            time.sleep(0.05)
            self.svc.turn_off("user")
        self.svc.turn_on()
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        time.sleep(1.0)
        self.assertEqual(len(self.pids()), 1)
        self.svc.turn_off("user")
        self.assertTrue(wait_for(lambda: not self.pids(), 5))

    def test_events_from_old_tunnel_are_ignored(self):
        self.svc.turn_on()
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        gen, tun, url = self.svc._gen, self.svc.tun, self.svc.url
        self.svc._tunnel_event(gen - 1, tun, "error", error="옛 시도")
        self.svc._tunnel_event(gen, object(), "on", url="https://old-old-old-old.trycloudflare.com")
        self.assertEqual((self.svc.state, self.svc.url), ("on", url))

    def test_shutdown_stops_everything_whatever_the_state(self):
        """state 가 'off' 여도 앱 끄기는 남은 터널·리스너를 모두 끔 (예전에는 바로 돌아감)."""
        self.svc.turn_on()
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        orphan = self.svc.tun
        with self.svc.lock:  # 어떤 까닭으로든 추적만 남은 터널이 있다고 치고
            self.svc.state, self.svc.tun = "off", None
        self.assertTrue(orphan.alive())
        self.svc.shutdown(timeout=1)
        self.assertFalse(orphan.alive())
        self.assertIsNone(self.svc.listener)
        self.assertEqual(self.pids(), [])


class StaleKillTests(LifecycleBase):
    def test_kill_stale_never_kills_our_live_tunnel(self):
        """검토: 두 번째 Tunnel.start 의 남은 pid 정리가 첫 터널의 살아 있는 프로세스를 끔 → 이제는 이 앱이 띄운 것은 건드리지 않음."""
        ev = []
        t1 = tunnel.Tunnel(self.exe, 9, lambda k, **kw: ev.append(k), check=lambda u: True, check_backoff=(0.01,))
        t1.start()
        self.addCleanup(lambda: t1.stop(timeout=2))
        self.assertTrue(wait_for(lambda: "on" in ev, 15))
        self.assertFalse(tunnel.kill_stale(self.exe))
        self.assertTrue(t1.alive())

    def test_stale_process_cleaned_before_ensure(self):
        order = []
        with mock.patch.object(tunnel, "kill_stale", lambda exe: order.append("kill")), \
                mock.patch.object(tunnel, "ensure", lambda progress=None, cancel=None, timeout=30: order.append("ensure") or self.exe):
            self.svc.turn_on()
            self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        self.assertEqual(order[:2], ["kill", "ensure"])


class RetryTests(LifecycleBase):
    def test_tunnel_error_closes_listener_and_retries_by_itself(self):
        """검토: 터널 오류는 다시 시도하지 않아 밖에서는 계속 못 씀 → 리스너 닫고 2분·5분·15분·30분 뒤 저절로 다시 (켜 둔 경우만)."""
        self.svc.turn_on()
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        clock = [time.time()]
        self.svc.clock = lambda: clock[0]
        self.svc._tunnel_event(self.svc._gen, self.svc.tun, "error", error=tunnel.BLOCKED_MSG)
        self.assertEqual(self.svc.state, "error")
        self.assertIsNone(self.svc.listener)
        self.assertEqual(self.svc._retry["at"], clock[0] + 120)
        self.assertIsNotNone(self.svc.pc_status()["retryAt"])
        self.svc.tick()
        self.assertEqual(self.svc.state, "error")  # 아직
        clock[0] += 121
        self.svc.tick()
        self.assertIn(self.svc.state, ("preparing", "starting", "on"))
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        self.assertEqual(self.svc._retry, {"n": 0, "at": None})  # 다시 켜지면 셈을 처음부터
        self.assertIn("원격 접속을 다시 켜 볼게요", self.fb.lines)
        self.assertEqual(len(self.pids()), 1)

    def test_backoff_grows_and_user_off_stops_retries(self):
        self.svc.turn_on()
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        clock = [time.time()]
        self.svc.clock = lambda: clock[0]
        gaps = []
        for _ in range(5):
            self.svc._tunnel_event(self.svc._gen, self.svc.tun, "error", error="x")
            gaps.append(self.svc._retry["at"] - clock[0])
            with self.svc.lock:
                self.svc.state = "starting"  # 다시 켜는 중이었다고 치고 다음 오류
        self.assertEqual(gaps, [120, 300, 900, 1800, 1800])
        self.svc.turn_off("user")
        clock[0] += 99999
        self.svc.tick()
        self.assertEqual(self.svc.state, "off")

    def test_no_retry_when_not_enabled_or_crypto_missing(self):
        with mock.patch.object(tunnel, "ensure", side_effect=RuntimeError(tunnel.MISSING_MSG)):
            self.svc.turn_on()  # 처음 켜다 실패 (PC 앞에 사람이 있음) → enabled 가 아니라 저절로 다시 안 함
            self.assertTrue(wait_for(lambda: self.svc.state == "error", 10))
        self.svc.clock = lambda: time.time() + 99999
        self.svc.tick()
        self.assertEqual(self.svc.state, "error")
        with mock.patch.object(remote, "crypto_ok", lambda: False):
            self.svc.store.set(enabled=True)
            self.svc.turn_on()
            self.assertEqual(self.svc._err_kind, "crypto")
            self.svc.tick()
            self.assertEqual(self.svc.state, "error")


class ShutdownTests(LifecycleBase):
    def test_shutdown_with_hung_ntfy_has_hard_deadline_and_stops_tunnel_first(self):
        """검토: 앱 끄기·업데이트 다시 시작이 ntfy(느린 DNS) 때문에 오래 걸리면 새 프로세스가 포기함 → 터널 먼저 끄고 마지막 비콘은 정한 시간 안에서만."""
        hole = socket.socket()
        hole.bind(("127.0.0.1", 0))
        hole.listen(50)  # 받기만 하고 대답 없는 서버
        self.addCleanup(hole.close)
        pair_device(self.svc, "폰")
        self.svc.turn_on()
        self.assertTrue(wait_for(lambda: self.svc.state == "on", 15))
        tun = self.svc.tun
        self.svc._ntfy = f"http://127.0.0.1:{hole.getsockname()[1]}"
        t0 = time.time()
        self.svc.shutdown(timeout=1.0)
        self.assertLess(time.time() - t0, 4.0)
        self.assertFalse(tun.alive())
        self.assertEqual(self.pids(), [])
        self.assertTrue(self.enabled())  # 다음에 앱을 켜면 이어서 켬

    def test_restart_shuts_remote_down_before_spawning(self):
        import app
        order = []
        with mock.patch.object(app.remote.SVC, "shutdown", lambda *a, **k: order.append("shutdown")), \
                mock.patch.object(app.subprocess, "Popen", lambda *a, **k: order.append("spawn")), \
                mock.patch.object(app, "_quit", lambda: order.append("quit")), mock.patch.object(app, "log", lambda m: None):
            app.restart()
        self.assertEqual(order, ["shutdown", "spawn", "quit"])


class EnsureAvTests(unittest.TestCase):
    """검토: 확인을 마친 cloudflared.exe 를 백신이 막으면 '인터넷을 확인하라' 안내 + 매번 55MB 를 다시 받음 → 백신 안내 · 파일은 둠 · 다시 안 받음."""

    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        for p in (mock.patch.object(core, "ENGINE_HOME", self.home / ".futsal-studio"), mock.patch.object(tunnel, "WIN", True),
                  mock.patch.object(tunnel, "_arch", lambda: "amd64")):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("FUTSAL_CLOUDFLARED", None)
        self.payload = b"MZ verified " * 100
        import hashlib
        self.assets = {"amd64": ("cloudflared-windows-amd64.exe", len(self.payload), hashlib.sha256(self.payload).hexdigest())}
        self.downloads = []

        def fake_download(url, dest, hook, timeout=30):
            self.downloads.append(url)
            Path(dest).write_bytes(self.payload)
        p = mock.patch.object(tunnel.updater, "download", fake_download)
        p.start()
        self.addCleanup(p.stop)

    def test_blocked_run_after_verified_download_gives_av_message_and_keeps_file(self):
        blocked = OSError(13, "Access is denied")
        blocked.winerror = 225
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets), mock.patch.object(tunnel, "_version", lambda p: blocked):
            with self.assertRaises(RuntimeError) as cm:
                tunnel.ensure()
            self.assertIn("백신", str(cm.exception))
            self.assertIn("225", str(cm.exception))
            self.assertTrue(tunnel.bin_path().exists())  # 확인한 파일은 지우지 않음
            with self.assertRaises(RuntimeError) as cm2:
                tunnel.ensure()  # 다시 켜도 55MB 를 또 받지 않고 같은 안내
            self.assertIn("백신", str(cm2.exception))
        self.assertEqual(len(self.downloads), 1)

    def test_verified_file_runs_after_av_exception_added(self):
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets), mock.patch.object(tunnel, "_version", lambda p: OSError(5, "x")):
            with self.assertRaises(RuntimeError):
                tunnel.ensure()
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets), mock.patch.object(tunnel, "_version", lambda p: "ok"):
            self.assertEqual(tunnel.ensure(), tunnel.bin_path())
        self.assertEqual(len(self.downloads), 1)

    def test_quarantined_file_downloads_again(self):
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets), mock.patch.object(tunnel, "_version", lambda p: OSError(2, "gone")):
            with self.assertRaises(RuntimeError):
                tunnel.ensure()
        tunnel.bin_path().unlink()  # 백신이 격리해 파일이 사라짐 → 다음에는 다시 받음
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets), mock.patch.object(tunnel, "_version", lambda p: "ok"):
            tunnel.ensure()
        self.assertEqual(len(self.downloads), 2)


if __name__ == "__main__":
    unittest.main()
