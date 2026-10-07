"""원격 접속 짝짓기(remote.py: 연결 코드·만남 주제·기기 만들기·끊기) 시험
— 저장소 폴더에서 python3 -m unittest tests.test_remote_pair"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote  # noqa: E402
from remote_fixture import Clock, make_home, pair_device, service  # noqa: E402


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.p = remote.Pairing(self.clock)

    def test_code_single_use(self):
        c = self.p.create()["code"]
        self.assertTrue(self.p.check(remote.fmt_code(c).lower()))
        with self.assertRaises(remote.PairError):
            self.p.check(c)

    def test_code_expires_after_10_minutes(self):
        c = self.p.create()["code"]
        self.clock.tick(remote.CODE_TTL + 1)
        with self.assertRaises(remote.PairError):
            self.p.check(c)
        self.assertIsNone(self.p.live())

    def test_five_wrong_attempts_kill_code(self):
        c = self.p.create()["code"]
        wrong = ("0" if c[0] != "0" else "1") + c[1:]
        for _ in range(remote.CODE_FAILS):
            with self.assertRaises(remote.PairError):
                self.p.check(wrong)
        with self.assertRaises(remote.PairError):
            self.p.check(c)  # 맞는 코드도 이제는 안 됨

    def test_new_code_replaces_old(self):
        a = self.p.create()["code"]
        b = self.p.create()["code"]
        with self.assertRaises(remote.PairError):
            self.p.check(a)
        self.assertTrue(self.p.check(b))

    def test_global_failures_block_until_new_code(self):
        for _ in range(remote.PAIR_FAILS_HOUR):
            self.p.create()
            with self.assertRaises(remote.PairError):
                self.p.check("ZZZZZZZZZZ")
            self.p.blocked = self.p.blocked  # 새 코드를 만들면 풀리므로 막힌 뒤에만 확인
        c = self.p.cur["code"] if self.p.cur else None
        self.assertTrue(self.p.blocked)
        self.assertIsNone(c)  # 막히면 지금 코드도 버림
        c2 = self.p.create()["code"]  # PC 에서 새 코드 → 다시 됨
        self.assertTrue(self.p.check(c2))

    def test_blocked_stays_until_new_code_even_after_hour(self):
        self.p.blocked = True
        self.clock.tick(7200)
        with self.assertRaises(remote.PairError):
            self.p.check("0000000000")

    def test_only_hash_compared_and_garbage_rejected(self):
        self.p.create()
        for bad in (None, "", "x" * 1000, {"a": 1}, ["x"], "../../etc", "7K3Q-M9XD-2P\x00"):
            with self.assertRaises(remote.PairError):
                self.p.check(bad)


class DeriveTests(unittest.TestCase):
    def test_topic_and_key_from_pbkdf2(self):
        topic, key = remote.derive_pair("7K3QM9XD2P")
        dk = hashlib.pbkdf2_hmac("sha256", b"7K3QM9XD2P", b"futsal-remote/pair/v1", 200_000, 48)
        self.assertEqual(topic, "fsp" + dk[:12].hex())
        self.assertEqual(key, dk[16:48])
        self.assertRegex(topic, r"^[-_A-Za-z0-9]{1,64}$")  # ntfy 주제 규칙

    def test_different_codes_different_topics(self):
        self.assertNotEqual(remote.derive_pair("0000000000")[0], remote.derive_pair("0000000001")[0])


class ServicePairTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.clock = Clock()
        self.svc, self.fb = service(self.home, self.clock)

    def test_pair_start_needs_remote_on(self):
        with self.assertRaises(remote.PairError):
            self.svc.pair_start()

    def test_pair_returns_keys_and_topics_once(self):
        cred = pair_device(self.svc, "iPhone · Safari")
        self.assertEqual(cred["api"], 1)
        self.assertEqual(len(remote._unb64u(cred["keys"]["auth"])), 32)
        self.assertEqual(len(remote._unb64u(cred["keys"]["beacon"])), 32)
        self.assertNotEqual(cred["keys"]["auth"], cred["keys"]["beacon"])
        self.assertEqual(cred["beacon"]["topic"], self.svc.store.data["topics"]["beacon"])
        self.assertEqual(cred["device"]["name"], "iPhone · Safari")
        self.assertIn("휴대폰이 연결됐어요 · iPhone · Safari", self.fb.lines)
        self.assertFalse(any(cred["keys"]["auth"] in x or cred["beacon"]["topic"] in x for x in self.fb.lines))

    def test_device_name_cleaned(self):
        cred = pair_device(self.svc, "가짜\r\n원격 · 끔‮" + "x" * 80)
        self.assertNotIn("\n", cred["device"]["name"])
        self.assertLessEqual(len(cred["device"]["name"]), 40)

    def test_max_five_devices(self):
        for i in range(remote.MAX_DEVICES):
            pair_device(self.svc, f"폰{i}")
        self.svc.state = "on"
        with self.assertRaises(remote.PairError):
            self.svc.pair_start()
        with self.assertRaises(remote.PairError):
            pair_device(self.svc, "여섯째")

    def test_pc_status_shows_code_and_link_only_when_on(self):
        self.svc.state = "on"
        self.svc.url = "https://quiet-river-tunnel-sample.trycloudflare.com"
        pair = self.svc.pair_start()
        self.assertRegex(pair["code"], r"^[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{2}$")
        code = pair["code"].replace("-", "")
        self.assertEqual(pair["link"], f"https://mulgyeol.kr/futsal#pair={code}&u=quiet-river-tunnel-sample")
        self.assertGreater(pair["expiresAt"], self.clock())
        self.svc.state = "starting"
        self.assertIsNone(self.svc.pc_status()["pair"])

    def test_revoke_all_clears_devices_pairing_and_rotates(self):
        pair_device(self.svc, "a")
        pair_device(self.svc, "b")
        old = dict(self.svc.store.data["topics"])
        self.svc.state = "on"
        self.svc.pairing.create()
        self.assertEqual(self.svc.revoke(everyone=True), 2)
        self.assertEqual(self.svc.store.data["devices"], [])
        self.assertIsNone(self.svc.pairing.live())
        self.assertNotEqual(self.svc.store.data["topics"]["beacon"], old["beacon"])
        self.assertNotEqual(self.svc.store.data["topics"]["notify"], old["notify"])
        saved = json.loads((self.home / "remote.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"], [])

    def test_settings_validation(self):
        self.assertEqual(self.svc.set_settings({"autoOffHours": 3})["autoOffHours"], 3)
        for bad in ({"autoOffHours": 5}, {"autoOffHours": True}, {"autoOffHours": "12"}, {"keepAwake": "never"}):
            with self.assertRaises(ValueError):
                self.svc.set_settings(bad)
        s = self.svc.set_settings({"keepAwake": "always", "notify": {"done": False}})
        self.assertEqual(s["keepAwake"], "always")
        self.assertEqual(s["notify"], {"done": False, "failed": True, "attention": True})


if __name__ == "__main__":
    unittest.main()
