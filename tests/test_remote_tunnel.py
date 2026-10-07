"""Cloudflare 터널 관리(tunnel.py) 시험 — 가짜 cloudflared(tests/fake_cloudflared.py)로
— 저장소 폴더에서 python3 -m unittest tests.test_remote_tunnel

진짜 cloudflared·인터넷은 쓰지 않는다. 받기 확인은 테스트 안의 작은 HTTP 서버(8851~ 대신 임의 포트)로.
"""
import json
import os
import stat
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import remote  # noqa: E402
import tunnel  # noqa: E402
from remote_fixture import FakeBridge, make_home  # noqa: E402

FAKE_SRC = Path(__file__).resolve().parent / "fake_cloudflared.py"


def make_fake(d):
    """실행할 수 있는 가짜 cloudflared (이 파이썬으로)."""
    p = Path(d) / "cloudflared"
    p.write_text(f"#!{sys.executable}\n" + FAKE_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


class Events:
    def __init__(self):
        self.items = []
        self.cv = threading.Condition()

    def __call__(self, kind, url=None, error=None):
        with self.cv:
            self.items.append((kind, url, error))
            self.cv.notify_all()

    def wait(self, kind, n=1, timeout=15):
        end = time.time() + timeout
        with self.cv:
            while sum(k == kind for k, _, _ in self.items) < n:
                left = end - time.time()
                if left <= 0:
                    return False
                self.cv.wait(left)
        return True

    def kinds(self):
        return [k for k, _, _ in self.items]


@unittest.skipIf(sys.platform == "win32", "가짜 cloudflared 는 shebang 스크립트")
class TunnelTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.exe = make_fake(self.home)
        self.log = self.home / "launches.jsonl"
        p = mock.patch.dict(os.environ, {"FAKE_CF_LOG": str(self.log), "FAKE_CF_MODE": "ok"})
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(core, "ENGINE_HOME", self.home / ".futsal-studio")
        p2.start()
        self.addCleanup(p2.stop)
        self.tuns = []

    def tearDown(self):
        for t in self.tuns:
            t.stop(timeout=3)

    def tun(self, **kw):
        ev = Events()
        kw.setdefault("check", lambda url: True)
        kw.setdefault("check_backoff", (0.01,))
        t = tunnel.Tunnel(self.exe, 8972, ev, **kw)
        self.tuns.append(t)
        return t, ev

    def launches(self):
        try:
            return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]
        except OSError:
            return []

    def test_url_parsed_and_on_only_after_registered(self):
        t, ev = self.tun()
        t.start()
        self.assertTrue(ev.wait("on"))
        kind, url, _ = ev.items[-1]
        self.assertRegex(url, r"^https://[a-z]+(-[a-z]+){3}\.trycloudflare\.com$")
        self.assertEqual(ev.kinds(), ["on"])
        self.assertEqual(len(self.launches()), 1)
        self.assertTrue(t.alive())

    def test_args_exact_and_no_secrets_or_debug(self):
        t, ev = self.tun()
        t.start()
        self.assertTrue(ev.wait("on"))
        argv = self.launches()[0]["argv"]
        self.assertEqual(argv[:2], ["tunnel", "--no-autoupdate"])
        cfg = Path(argv[argv.index("--config") + 1])
        self.assertEqual(cfg.read_text(encoding="utf-8"), "no-autoupdate: true\n")
        self.assertEqual(argv[argv.index("--url") + 1], "http://127.0.0.1:8972")
        self.assertEqual(argv[argv.index("--http-host-header") + 1], "remote.futsal.invalid")
        self.assertEqual(argv[argv.index("--metrics") + 1], "127.0.0.1:0")
        self.assertIn("--management-diagnostics=false", argv)
        self.assertEqual(argv[argv.index("--loglevel") + 1], "info")
        self.assertNotIn("debug", " ".join(argv).lower())
        self.assertNotIn("token", " ".join(argv).lower())
        self.assertNotIn("--protocol", argv)

    def test_tunnel_env_vars_not_passed(self):
        with mock.patch.dict(os.environ, {"TUNNEL_LOGLEVEL": "debug", "TUNNEL_TOKEN": "secret"}):
            t, ev = self.tun()
            t.start()
            self.assertTrue(ev.wait("on"))
            env = Path(f"/proc/{t.proc.pid}/environ").read_bytes().split(b"\0")
        self.assertFalse([e for e in env if e.startswith(b"TUNNEL_")])

    def test_http2_fallback_when_quic_blocked(self):
        os.environ["FAKE_CF_MODE"] = "http2"
        t, ev = self.tun(first_wait=1.0, http2_wait=5)
        t.start()
        self.assertTrue(ev.wait("on"))
        ls = self.launches()
        self.assertEqual(len(ls), 2)
        self.assertNotIn("--protocol", ls[0]["argv"])
        self.assertEqual(ls[1]["argv"][-2:], ["--protocol", "http2"])

    def test_error_when_both_fail(self):
        os.environ["FAKE_CF_MODE"] = "silent"
        t, ev = self.tun(first_wait=0.6, http2_wait=0.6)
        t.start()
        self.assertTrue(ev.wait("error"))
        self.assertEqual(ev.items[-1][2], tunnel.BLOCKED_MSG)
        self.assertFalse(t.alive())
        self.assertEqual(len(self.launches()), 2)

    def test_crash_restarts_with_backoff_then_gives_up(self):
        os.environ["FAKE_CF_MODE"] = "crash"
        t, ev = self.tun(backoff=(0.05, 0.05, 0.05, 0.05))
        t.start()
        self.assertTrue(ev.wait("error", timeout=40))
        kinds = ev.kinds()
        self.assertEqual(kinds.count("on"), tunnel.MAX_RESTARTS + 1)
        self.assertEqual(kinds.count("restarting"), tunnel.MAX_RESTARTS)
        self.assertEqual(kinds[-2:], ["attention", "error"])
        urls = [u for k, u, _ in ev.items if k == "on"]
        self.assertGreater(len(set(urls)), 1)  # 다시 켤 때마다 주소가 바뀜 (가짜도 그렇게)

    def test_restart_backoff_order(self):
        self.assertEqual(tunnel.BACKOFF, (5, 15, 60, 300))
        os.environ["FAKE_CF_MODE"] = "crash"
        waits = []
        t, ev = self.tun(backoff=(0.01, 0.02, 0.03, 0.04), check_backoff=(0.005,))
        real = t.stopping.wait

        def spy(d=None):
            waits.append(d)
            return real(d)
        t.stopping.wait = spy
        t.start()
        self.assertTrue(ev.wait("restarting", n=5, timeout=30))
        t.stop()
        bw = [w for w in waits if w in (0.01, 0.02, 0.03, 0.04)]
        self.assertEqual(bw[:5], [0.01, 0.02, 0.03, 0.04, 0.04])

    def test_stop_kills_process_and_single_process(self):
        t, ev = self.tun()
        t.start()
        self.assertTrue(ev.wait("on"))
        pid = t.proc.pid
        self.assertTrue((core.ENGINE_HOME / "remote" / "tunnel.pid").exists())
        t.stop()
        self.assertIsNotNone(t.proc.poll())
        self.assertFalse(Path(f"/proc/{pid}").exists() and Path(f"/proc/{pid}/status").read_text().find("zombie") < 0)
        self.assertFalse((core.ENGINE_HOME / "remote" / "tunnel.pid").exists())
        self.assertEqual(len(self.launches()), 1)

    def test_stale_pid_killed_only_if_ours(self):
        p = subprocess.Popen([str(self.exe), "tunnel"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(5)))
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (other.poll() is None and other.kill(), other.wait(5)))
        time.sleep(0.3)
        tunnel.remote_dir().mkdir(parents=True, exist_ok=True)
        tunnel._pidfile().write_text(json.dumps({"pid": other.pid, "exe": str(self.exe)}), encoding="utf-8")
        self.assertFalse(tunnel.kill_stale(self.exe))  # 우리 실행 파일이 아닌 프로세스는 건드리지 않음
        self.assertIsNone(other.poll())
        tunnel._pidfile().write_text(json.dumps({"pid": p.pid, "exe": str(self.exe)}), encoding="utf-8")
        self.assertTrue(tunnel.kill_stale(self.exe))
        p.wait(5)
        self.assertIsNotNone(p.poll())

    def test_output_lines_never_reach_log(self):
        """서비스 전체(가짜 cloudflared · dev 아님): 기록에 터널 주소·출력 줄이 없음."""
        fb = FakeBridge()
        svc = remote.Service(home=self.home, ntfy="http://127.0.0.1:1")
        svc.init(fb.bridge())
        with mock.patch.dict(os.environ, {"FUTSAL_CLOUDFLARED": str(self.exe)}), mock.patch.object(tunnel, "_self_check", lambda url: True), \
                mock.patch.object(tunnel, "CHECK_BACKOFF", (0.01,)):
            self.assertTrue(svc.turn_on())
            end = time.time() + 15
            while svc.state != "on" and time.time() < end:
                time.sleep(0.05)
            self.assertEqual(svc.state, "on")
            self.assertRegex(svc.url, r"\.trycloudflare\.com$")
            svc.turn_off()
        text = "\n".join(fb.lines)
        self.assertIn("원격 접속을 켰어요", text)
        self.assertNotIn("trycloudflare", text)
        self.assertNotIn("Registered", text)
        self.assertNotIn("INF", text)
        self.assertIsNone(svc.tun)
        self.assertIsNone(svc.listener)


class DownloadTests(unittest.TestCase):
    """ensure(): 고정 주소 · 크기·sha256 확인 · .part → 제자리 · 틀리면 아무것도 안 남김 · '최신'으로 바꿔 받지 않음."""

    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.payload = b"MZ fake cloudflared " * 1000
        payload = self.payload
        self.hits = []
        hits = self.hits

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        url = f"http://127.0.0.1:{self.srv.server_address[1]}" + "/{ver}/{name}"
        for p in (mock.patch.object(core, "ENGINE_HOME", self.home / ".futsal-studio"), mock.patch.object(tunnel, "CF_URL", url),
                  mock.patch.object(tunnel, "WIN", True), mock.patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("FUTSAL_CLOUDFLARED", None)

    def assets(self, size=None, sha=None):
        import hashlib
        return {"amd64": ("cloudflared-windows-amd64.exe", size or len(self.payload), sha or hashlib.sha256(self.payload).hexdigest()),
                "386": ("cloudflared-windows-386.exe", 1, "0" * 64)}

    def test_good_download_placed_after_verify(self):
        prog = []
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets()), mock.patch.object(tunnel, "_version", lambda p: "ok"), \
                mock.patch.object(tunnel, "_arch", lambda: "amd64"):
            p = tunnel.ensure(progress=lambda g, t: prog.append((g, t)))
        self.assertEqual(p, tunnel.bin_path())
        self.assertEqual(p.read_bytes(), self.payload)
        self.assertFalse(p.with_name(p.name + ".part").exists())
        self.assertEqual(self.hits, ["/2026.10.0/cloudflared-windows-amd64.exe"])  # 고정 판·고정 이름
        self.assertEqual(prog[-1], (len(self.payload), len(self.payload)))

    def test_size_mismatch_leaves_nothing(self):
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets(size=123)), mock.patch.object(tunnel, "_arch", lambda: "amd64"):
            with self.assertRaises(RuntimeError) as cm:
                tunnel.ensure()
        self.assertIn("크기", str(cm.exception))
        self.assertFalse(tunnel.bin_path().exists())
        self.assertFalse(list((self.home / ".futsal-studio" / "bin").glob("*.part")))

    def test_sha_mismatch_leaves_nothing(self):
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets(sha="f" * 64)), mock.patch.object(tunnel, "_arch", lambda: "amd64"):
            with self.assertRaises(RuntimeError) as cm:
                tunnel.ensure()
        self.assertIn("sha256", str(cm.exception))
        self.assertFalse(tunnel.bin_path().exists())
        self.assertFalse(list((self.home / ".futsal-studio" / "bin").glob("*")))

    def test_bad_version_after_download_removed(self):
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets()), mock.patch.object(tunnel, "_arch", lambda: "amd64"), \
                mock.patch.object(tunnel, "_version", lambda p: "other"):
            with self.assertRaises(RuntimeError) as cm:
                tunnel.ensure()
        self.assertEqual(str(cm.exception), tunnel.MISSING_MSG)
        self.assertFalse(tunnel.bin_path().exists())

    def test_cancel_removes_part(self):
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets()), mock.patch.object(tunnel, "_arch", lambda: "amd64"):
            with self.assertRaises(RuntimeError) as cm:
                tunnel.ensure(cancel=lambda: True)
        self.assertIn("멈췄어요", str(cm.exception))
        self.assertFalse(list((self.home / ".futsal-studio" / "bin").glob("*")))

    def test_network_error_korean_and_no_unpinned_fallback(self):
        self.srv.shutdown()
        self.srv.server_close()
        with mock.patch.object(tunnel, "CF_ASSETS", self.assets()), mock.patch.object(tunnel, "_arch", lambda: "amd64"):
            with self.assertRaises(RuntimeError) as cm:
                tunnel.ensure(timeout=3)
        self.assertIn("연결 도구", str(cm.exception))
        self.assertNotIn("latest", tunnel.CF_URL)

    def test_pinned_assets_match_release_notes(self):
        self.assertEqual(tunnel.CF_VERSION, "2026.10.0")
        self.assertEqual(tunnel.CF_ASSETS["amd64"][1:], (55365048, "86aee4017b26625cee8484c113558f48effa4cd47f7aa05fcf425604e5d2b23c"))
        self.assertEqual(tunnel.CF_ASSETS["386"][1:], (37705864, "0630a8779e9823a1a3b091698b8e71874e0f7b205559219f52fdd301466b5546"))

    def test_arch_choice(self):
        for m, want in (("AMD64", "amd64"), ("x86", "386"), ("i686", "386"), ("ARM64", "amd64")):
            with mock.patch.object(tunnel.platform, "machine", lambda m=m: m):
                self.assertEqual(tunnel._arch(), want)

    def test_non_windows_needs_env(self):
        with mock.patch.object(tunnel, "WIN", False), self.assertRaises(RuntimeError):
            tunnel.ensure()


if __name__ == "__main__":
    unittest.main()
