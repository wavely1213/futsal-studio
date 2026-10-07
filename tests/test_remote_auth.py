"""원격 접속 보안 핵심(remote.py: 저장 파일·서명·표·요청 수 제한·글 다듬기·암호 부품) 시험
— 저장소 폴더에서 python3 -m unittest tests.test_remote_auth"""
import base64
import builtins
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import remote  # noqa: E402
from remote_fixture import Clock, make_home, pair_device, service, signed  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.clock = Clock()

    def test_new_store_has_random_ids_and_topics(self):
        a = remote.Store(self.home / "a.json", self.clock).data
        b = remote.Store(self.home / "b.json", self.clock).data
        self.assertRegex(a["topics"]["beacon"], r"^fsb[0-9a-f]{24}$")
        self.assertRegex(a["topics"]["notify"], r"^fsn[0-9a-f]{24}$")
        self.assertNotEqual(a["topics"], b["topics"])
        self.assertEqual(a["pc"]["name"], "내 PC")  # Windows 컴퓨터 이름(사람 이름일 수 있음)은 쓰지 않음
        self.assertFalse(a["enabled"])

    def test_atomic_save_and_reload(self):
        s = remote.Store(self.home / "remote.json", self.clock)
        dev = s.add_device("내 휴대폰")
        again = remote.Store(self.home / "remote.json", self.clock)
        self.assertEqual(again.device(dev["id"])["name"], "내 휴대폰")
        self.assertFalse(list(self.home.glob("*.tmp")))

    @unittest.skipIf(sys.platform == "win32", "권한 600 은 Windows 에서 의미 없음")
    def test_file_mode_600(self):
        s = remote.Store(self.home / "remote.json", self.clock)
        s.save()
        self.assertEqual(stat.S_IMODE((self.home / "remote.json").stat().st_mode), 0o600)

    def test_corrupt_file_kept_as_bad_and_fresh_store(self):
        f = self.home / "remote.json"
        f.write_text("{깨진", encoding="utf-8")
        s = remote.Store(f, self.clock)
        self.assertEqual(s.data["devices"], [])
        self.assertTrue((self.home / "remote.json.bad").exists())

    def test_wrong_shape_is_corrupt(self):
        f = self.home / "remote.json"
        good = remote._fresh()
        good["devices"] = [{"id": "zz", "auth": "x", "beacon": "y"}]
        f.write_text(json.dumps(good), encoding="utf-8")
        self.assertEqual(remote.Store(f, self.clock).data["devices"], [])

    def test_device_expiry_after_90_days(self):
        s = remote.Store(self.home / "remote.json", self.clock)
        d = s.add_device("오래된 폰")
        self.clock.tick(89 * 86400)
        self.assertEqual(s.expired(), [])
        self.clock.tick(2 * 86400)
        self.assertEqual(s.expired(), [d["id"]])

    def test_seq_only_goes_up_and_is_persisted(self):
        s = remote.Store(self.home / "remote.json", self.clock)
        a, b = s.next_seq(), s.next_seq()
        self.assertEqual(b, a + 1)
        self.assertEqual(remote.Store(self.home / "remote.json", self.clock).next_seq(), b + 1)

    def test_store_lives_in_user_profile_not_work_or_app(self):
        """remote.json 은 ~/.futsal-studio (core.ENGINE_HOME) 아래 — 작업 폴더·앱 폴더(업데이트·공개 저장소)에는 절대 안 생김."""
        with mock.patch.object(core, "ENGINE_HOME", self.home / ".futsal-studio"):
            svc = remote.Service(clock=self.clock, ntfy="http://127.0.0.1:1")
            svc.init(None)
            svc.store.add_device("폰")
            self.assertTrue((self.home / ".futsal-studio" / "remote.json").is_file())
        self.assertFalse(list(core.APP_DIR.glob("remote.json")))
        self.assertFalse(list(core.WORK.rglob("remote.json")))


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.clock = Clock()
        self.svc, _ = service(self.home, self.clock)
        self.cred = pair_device(self.svc)

    def verify(self, header, method="GET", path="/r/status?since=0", body=b""):
        return self.svc.auth.verify(header, method, path, body)

    def test_good_signature_accepted(self):
        dev = self.verify(signed(self.cred, "GET", "/r/status?since=0", clock=self.clock))
        self.assertEqual(dev["id"], self.cred["device"]["id"])

    def test_signature_covers_method_path_query_and_body(self):
        h = signed(self.cred, "POST", "/r/action", b'{"action":"cancel"}', clock=self.clock)
        self.assertEqual(self.verify(h, "POST", "/r/action", b'{"action":"cancel"}')["id"], self.cred["device"]["id"])
        for m, p, b in (("GET", "/r/action", b'{"action":"cancel"}'), ("POST", "/r/forget", b'{"action":"cancel"}'),
                        ("POST", "/r/action", b'{"action":"qa"}'), ("POST", "/r/action?x=1", b'{"action":"cancel"}')):
            h = signed(self.cred, "POST", "/r/action", b'{"action":"cancel"}', clock=self.clock)
            with self.assertRaises(remote.AuthError) as cm:
                self.verify(h, m, p, b)
            self.assertEqual(cm.exception.code, "bad_sig")

    def test_replay_rejected(self):
        h = signed(self.cred, "GET", "/r/status?since=0", clock=self.clock)
        self.verify(h)
        with self.assertRaises(remote.AuthError) as cm:
            self.verify(h)
        self.assertEqual(cm.exception.code, "replay")

    def test_clock_skew_window(self):
        ok = signed(self.cred, "GET", "/r/status?since=0", ts=self.clock() - 299, clock=self.clock)
        self.verify(ok)
        for off in (-301, 301):
            h = signed(self.cred, "GET", "/r/status?since=0", ts=self.clock() + off, clock=self.clock)
            with self.assertRaises(remote.AuthError) as cm:
                self.verify(h)
            self.assertEqual(cm.exception.code, "skew")

    def test_unknown_and_revoked_device(self):
        other = dict(self.cred, device={"id": "0123456789abcdef"})
        with self.assertRaises(remote.AuthError) as cm:
            self.verify(signed(other, "GET", "/r/status?since=0", clock=self.clock))
        self.assertEqual(cm.exception.code, "unknown_device")
        self.svc.revoke([self.cred["device"]["id"]])
        with self.assertRaises(remote.AuthError) as cm:
            self.verify(signed(self.cred, "GET", "/r/status?since=0", clock=self.clock))
        self.assertEqual(cm.exception.code, "unknown_device")

    def test_wrong_key_rejected(self):
        bad = json.loads(json.dumps(self.cred))
        bad["keys"]["auth"] = remote._b64u(os.urandom(32))
        with self.assertRaises(remote.AuthError) as cm:
            self.verify(signed(bad, "GET", "/r/status?since=0", clock=self.clock))
        self.assertEqual(cm.exception.code, "bad_sig")

    def test_malformed_authorization_fuzz(self):
        good = signed(self.cred, "GET", "/r/status?since=0", clock=self.clock)
        did, rest = good[5:].split(".", 1)
        cases = [None, "", "Bearer x", "FSR1", "FSR1 " + did, good.replace("FSR1", "FSR2"), good + ".x", good[:-1],
                 "FSR1 " + did.upper() + "." + rest, good.replace(".", ":"), "FSR1 " + "a" * 5000, good + "\r\nX: y",
                 "FSR1 ../../etc." + rest, "FSR1 " + did + ".-1." + rest.split(".", 1)[1], base64.b64encode(good.encode()).decode()]
        for h in cases:
            with self.assertRaises(remote.AuthError, msg=repr(h)[:60]):
                self.verify(h)

    def test_nonce_memory_is_bounded(self):
        for _ in range(remote.NONCE_MAX + 50):
            self.verify(signed(self.cred, "GET", "/r/status?since=0", clock=self.clock))
        self.assertLessEqual(len(self.svc.auth.nonces[self.cred["device"]["id"]]), remote.NONCE_MAX)


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.t = remote.Tickets(self.clock)

    def test_ticket_expires_after_two_hours(self):
        tid = self.t.issue("dev1", "file", "/x/a.mp4")
        self.assertEqual(self.t.get(tid)[2], "/x/a.mp4")
        self.clock.tick(remote.TICKET_TTL + 1)
        self.assertIsNone(self.t.get(tid))

    def test_ticket_reused_while_fresh_then_renewed(self):
        a = self.t.issue("dev1", "file", "/x/a.mp4")
        self.assertEqual(self.t.issue("dev1", "file", "/x/a.mp4"), a)
        self.assertNotEqual(self.t.issue("dev2", "file", "/x/a.mp4"), a)  # 기기마다 따로
        self.clock.tick(remote.TICKET_TTL - 1000)
        self.assertNotEqual(self.t.issue("dev1", "file", "/x/a.mp4"), a)

    def test_revoke_drops_only_that_device(self):
        a, b = self.t.issue("dev1", "file", "/x/a"), self.t.issue("dev2", "file", "/x/a")
        self.t.drop({"dev1"})
        self.assertIsNone(self.t.get(a))
        self.assertIsNotNone(self.t.get(b))

    def test_ticket_ids_are_128_bit_random(self):
        a = self.t.issue("d", "file", "/1")
        self.assertGreaterEqual(len(a), 22)
        self.assertRegex(a, r"^[A-Za-z0-9_-]+$")

    def test_lru_cap(self):
        for i in range(remote.TICKET_MAX + 10):
            self.t.issue("d", "file", f"/{i}")
        self.assertLessEqual(len(self.t.items), remote.TICKET_MAX)


class LimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.l = remote.Limiter(self.clock)

    def test_open_bucket_10_then_refill_every_6s(self):
        self.assertTrue(all(self.l.take("open", "1.2.3.4") for _ in range(10)))
        self.assertFalse(self.l.take("open", "1.2.3.4"))
        self.assertTrue(self.l.take("open", "5.6.7.8"))  # 다른 주소는 따로
        self.clock.tick(6)
        self.assertTrue(self.l.take("open", "1.2.3.4"))
        self.assertFalse(self.l.take("open", "1.2.3.4"))

    def test_auth_bucket_60_plus_5_per_sec(self):
        self.assertEqual(sum(self.l.take("auth", "ip") for _ in range(70)), 60)
        self.clock.tick(1)
        self.assertEqual(sum(self.l.take("auth", "ip") for _ in range(10)), 5)

    def test_lockout_after_10_failures_for_15_minutes(self):
        for _ in range(9):
            self.assertFalse(self.l.failed("ip"))
        self.assertTrue(self.l.failed("ip"))
        self.assertTrue(self.l.locked("ip"))
        self.assertFalse(self.l.locked("other"))
        self.clock.tick(remote.LOCKOUT + 1)
        self.assertFalse(self.l.locked("ip"))

    def test_failures_outside_window_do_not_count(self):
        for _ in range(9):
            self.l.failed("ip")
        self.clock.tick(remote.AUTH_FAIL_WINDOW + 1)
        self.assertFalse(self.l.failed("ip"))
        self.assertFalse(self.l.locked("ip"))

    def test_action_gap_per_device(self):
        self.assertTrue(self.l.action_ok("d1"))
        self.assertFalse(self.l.action_ok("d1"))
        self.assertTrue(self.l.action_ok("d2"))
        self.clock.tick(2.1)
        self.assertTrue(self.l.action_ok("d1"))

    def test_media_concurrency_per_device_and_total(self):
        self.assertEqual(sum(self.l.media_enter("d1") for _ in range(8)), remote.MEDIA_PER_DEVICE)
        self.assertEqual(sum(self.l.media_enter("d2") for _ in range(8)), remote.MEDIA_TOTAL - remote.MEDIA_PER_DEVICE)
        self.l.media_leave("d1")
        self.assertTrue(self.l.media_enter("d2"))

    def test_bucket_memory_bounded(self):
        for i in range(remote.LRU_MAX + 100):
            self.l.take("auth", f"ip{i}")
        self.assertLessEqual(len(self.l.buckets), remote.LRU_MAX)


class TextTests(unittest.TestCase):
    def test_scrub_replaces_home_and_controls(self):
        home = str(Path.home())
        s = remote.scrub(f"못 열었어요 · {home}/풋살사관학교_작업/out/a.mp4\x1b[31m‮")
        self.assertNotIn(home, s)
        self.assertIn("~/풋살사관학교_작업/out/a.mp4", s)
        self.assertNotIn("\x1b", s)
        self.assertNotIn("‮", s)

    def test_clean_strips_newlines_and_limits(self):
        self.assertEqual(remote.clean("가\r\n나‮다" + "x" * 100, 10), "가  나다xxxxx")

    def test_code_normalize(self):
        self.assertEqual(remote.norm_code("7k3q-m9xd-2p"), "7K3QM9XD2P")
        self.assertEqual(remote.norm_code(" 7K3Q M9XD 2P "), "7K3QM9XD2P")
        self.assertEqual(remote.norm_code("OOOO-IIII-LL"), "0000111111")
        for bad in ("", None, "7K3Q-M9XD", "7K3Q-M9XD-2PX", "UUUU-UUUU-UU", "7K3Q-M9XD-2!", "７K3Q-M9XD-2P"):
            self.assertIsNone(remote.norm_code(bad), bad)

    def test_new_code_alphabet_and_format(self):
        for _ in range(50):
            c = remote.new_code()
            self.assertEqual(len(c), 10)
            self.assertTrue(set(c) <= set(remote.CROCK))
        self.assertEqual(remote.fmt_code("7K3QM9XD2P"), "7K3Q-M9XD-2P")


class CryptoMissingTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)

    def _no_crypto(self):
        real = builtins.__import__

        def fake(name, *a, **kw):
            if name.startswith("Cryptodome"):
                raise ImportError("없음")
            return real(name, *a, **kw)
        return mock.patch.object(builtins, "__import__", fake)

    def test_turn_on_fails_closed_with_korean_message(self):
        svc, _ = service(self.home)
        with self._no_crypto():
            self.assertFalse(remote.crypto_ok())
            self.assertFalse(svc.turn_on())
        self.assertEqual(svc.state, "error")
        self.assertEqual(svc.error, remote.MISSING_MSG)
        self.assertIsNone(svc.listener)

    def test_pairing_refused_without_crypto(self):
        svc, _ = service(self.home)
        svc.state = "on"
        with self._no_crypto(), self.assertRaises(remote.PairError):
            svc.pair_start()

    def test_seal_raises_crypto_missing(self):
        with self._no_crypto(), self.assertRaises(remote.CryptoMissing):
            remote.gcm_seal(b"k" * 32, b"x", "aad")

    def test_app_imports_without_crypto(self):
        """업데이트 import 확인(IMPORT_CHECK)이 암호 부품 때문에 되돌리지 않게: Cryptodome 없이도 app 이 import 됨."""
        import subprocess
        code = ("import builtins,sys\nreal=builtins.__import__\n"
                "def f(n,*a,**k):\n    if n.startswith('Cryptodome'): raise ImportError(n)\n    return real(n,*a,**k)\n"
                "builtins.__import__=f\nimport app, core, editor, thumb, style, qa, remote, tunnel, qr\nprint('ok', remote.crypto_ok())")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(core.APP_DIR), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip(), "ok False", r.stderr[-800:])


class GcmTests(unittest.TestCase):
    def test_seal_open_roundtrip_and_aad_binding(self):
        k = os.urandom(32)
        blob = remote.gcm_seal(k, "안녕", "aad-1")
        self.assertEqual(remote.gcm_open(k, blob, "aad-1").decode(), "안녕")
        with self.assertRaises(ValueError):
            remote.gcm_open(k, blob, "aad-2")
        with self.assertRaises(ValueError):
            remote.gcm_open(os.urandom(32), blob, "aad-1")

    def test_nonce_is_fresh_each_time(self):
        k = os.urandom(32)
        self.assertNotEqual(remote.gcm_seal(k, "x", "a")[:16], remote.gcm_seal(k, "x", "a")[:16])


if __name__ == "__main__":
    unittest.main()
