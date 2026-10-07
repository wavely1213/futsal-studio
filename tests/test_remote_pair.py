"""원격 접속 짝짓기(remote.py: 연결 코드·만남 주제·기기 만들기·끊기) 시험
— 저장소 폴더에서 python3 -m unittest tests.test_remote_pair"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote  # noqa: E402
from remote_fixture import Clock, make_home, open_pair, pair_body, pair_device, service  # noqa: E402


class PairingTests(unittest.TestCase):
    """휴대폰은 코드 대신 증명(HMAC(코드에서 만든 열쇠, nonce|ts))만 보낸다 — 코드 글은 터널로 안 감."""

    def setUp(self):
        self.clock = Clock()
        self.p = remote.Pairing(self.clock)

    def args(self, p, ts=None, nonce=None, key=None):
        ts = int(self.clock()) if ts is None else ts
        nonce = nonce or remote._b64u(os.urandom(16))
        return nonce, ts, remote.pair_proof(key or p["proof"], nonce, ts)

    def test_code_single_use(self):
        c = self.p.create()
        a = self.args(c)
        got = self.p.check(*a)
        self.assertEqual((got["topic"], got["key"]), (c["topic"], c["key"]))
        with self.assertRaises(remote.PairError):
            self.p.check(*a)  # 같은 증명을 다시 (Cloudflare 가 봤어도) → 이미 쓴 코드
        with self.assertRaises(remote.PairError):
            self.p.check(*self.args(c))

    def test_code_expires_after_10_minutes(self):
        c = self.p.create()
        self.clock.tick(remote.CODE_TTL + 1)
        with self.assertRaises(remote.PairError):
            self.p.check(*self.args(c))
        self.assertIsNone(self.p.live())

    def test_five_wrong_attempts_kill_code_and_tell_pc(self):
        c = self.p.create()
        for _ in range(remote.CODE_FAILS):
            with self.assertRaises(remote.PairError):
                self.p.check(*self.args(c, key=os.urandom(16)))
        with self.assertRaises(remote.PairError):
            self.p.check(*self.args(c))  # 맞는 증명도 이제는 안 됨
        self.assertEqual(self.p.note, "killed")  # PC 화면이 까닭을 보여 줌
        self.assertIn("틀린 연결 코드가 5번", remote.PAIR_NOTES["killed"])
        self.p.create()
        self.assertIsNone(self.p.note)

    def test_new_code_replaces_old(self):
        a = self.p.create()
        b = self.p.create()
        with self.assertRaises(remote.PairError):
            self.p.check(*self.args(a))
        self.assertTrue(self.p.check(*self.args(b)))

    def test_global_failures_block_until_new_code(self):
        for _ in range(remote.PAIR_FAILS_HOUR):
            c = self.p.create()
            with self.assertRaises(remote.PairError):
                self.p.check(*self.args(c, key=os.urandom(16)))
        self.assertTrue(self.p.blocked)
        self.assertIsNone(self.p.cur)  # 막히면 지금 코드도 버림
        self.assertEqual(self.p.note, "blocked")
        c2 = self.p.create()  # PC 에서 새 코드 → 다시 됨
        self.assertTrue(self.p.check(*self.args(c2)))

    def test_blocked_stays_until_new_code_even_after_hour(self):
        c = self.p.create()
        self.p.blocked = True
        self.clock.tick(7200)
        with self.assertRaises(remote.PairError):
            self.p.check(*self.args(c))

    def test_garbage_rejected(self):
        c = self.p.create()
        n, ts, pr = self.args(c)
        for bad in [(None, None, None), ("", 0, ""), ("x" * 1000, ts, pr), (n, str(ts), pr), (n, True, pr), (n, float(ts), pr),
                    (n + "=", ts, pr), (n, ts, pr[:-1]), (n, ts, pr + "A"), ({"a": 1}, ts, pr), (n, ts, ["x"]), ("../../etc/passwd!!!!!!", ts, pr)]:
            with self.assertRaises(remote.PairError, msg=repr(bad)[:60]):
                self.p.check(*bad)

    def test_clock_skew_rejected_with_code(self):
        c = self.p.create()
        with self.assertRaises(remote.PairError) as cm:
            self.p.check(*self.args(c, ts=int(self.clock()) - remote.SKEW - 5))
        self.assertEqual(cm.exception.code, "skew")

    def test_proof_bound_to_nonce_and_time(self):
        c = self.p.create()
        n, ts, pr = self.args(c)
        with self.assertRaises(remote.PairError):
            self.p.check(remote._b64u(os.urandom(16)), ts, pr)  # 다른 nonce 에 옛 증명
        with self.assertRaises(remote.PairError):
            self.p.check(n, ts + 1, pr)


class DeriveTests(unittest.TestCase):
    def test_topic_and_keys_from_pbkdf2(self):
        topic, key, proof = remote.derive_pair("7K3QM9XD2P")
        dk = hashlib.pbkdf2_hmac("sha256", b"7K3QM9XD2P", b"futsal-remote/pair/v1", 200_000, 64)
        self.assertEqual(topic, "fsp" + dk[:12].hex())
        self.assertEqual(key, dk[16:48])
        self.assertEqual(proof, dk[48:64])
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

    def test_pair_response_is_sealed_with_code_key(self):
        """검토(중간): 대답에 기기 열쇠·주제·주소를 그대로 넣으면 TLS 를 끝내는 Cloudflare 가 봄 → 코드에서 만든 열쇠로 잠금."""
        self.svc.state, self.svc.url = "on", "https://quiet-river-sample-x.trycloudflare.com"
        p = self.svc.pairing.create()
        resp = self.svc.r_pair(pair_body(p, "iPhone · Safari", ts=self.clock()))
        self.assertEqual(set(resp), {"api", "v", "pc", "device", "sealed", "time"})
        self.assertEqual(set(resp["pc"]), {"id"})
        flat = json.dumps(resp)
        dev = self.svc.store.data["devices"][0]
        for secret in (dev["auth"], dev["beacon"], self.svc.store.data["topics"]["beacon"], self.svc.store.data["topics"]["notify"],
                       "quiet-river-sample-x", p["code"]):
            self.assertNotIn(secret, flat)
        inner = open_pair(p, resp)
        self.assertEqual(inner["keys"], {"auth": dev["auth"], "beacon": dev["beacon"]})
        self.assertEqual(inner["url"], "https://quiet-river-sample-x.trycloudflare.com")  # 진짜 PC 주소는 잠긴 속에만
        self.assertEqual(inner["notify"]["topic"], self.svc.store.data["topics"]["notify"])
        forged = dict(resp, device={"id": "0" * 16})  # 다른 기기 번호로 바꾸면 (aad) 못 엶
        with self.assertRaises(ValueError):
            open_pair(p, forged)
        wrong = dict(p, key=os.urandom(32))  # 코드를 모르는 곳(가짜 PC·Cloudflare)은 못 엶
        with self.assertRaises(ValueError):
            open_pair(wrong, resp)

    def test_raw_code_is_never_accepted(self):
        self.svc.state = "on"
        p = self.svc.pairing.create()
        with self.assertRaises(remote.PairError):
            self.svc.r_pair({"code": p["code"], "name": "x"})  # 예전 방식(코드 글 그대로)은 안 받음
        with self.assertRaises(remote.PairError):
            self.svc.r_pair({"code": remote.fmt_code(p["code"]), "name": "x"})

    def test_same_browser_relink_replaces_old_entry(self):
        """검토: 같은 휴대폰을 다시 연결하면 PC 목록에 같은 이름이 쌓이고 5칸을 채운 뒤 90일 뒤 주제를 바꿈 → 설치 번호로 바꿔 끼움."""
        inst = remote._b64u(os.urandom(16))
        a = pair_device(self.svc, "Galaxy · Chrome", install=inst)
        other = pair_device(self.svc, "iPhone · Safari")
        topics = dict(self.svc.store.data["topics"])
        t = self.svc.tickets.issue(a["device"]["id"], "file", "/x.mp4", "203.0.113.1")
        b = pair_device(self.svc, "Galaxy · Chrome", install=inst)
        ids = [x["id"] for x in self.svc.store.data["devices"]]
        self.assertEqual(sorted(ids), sorted([other["device"]["id"], b["device"]["id"]]))
        self.assertNotIn(a["device"]["id"], ids)
        self.assertIsNone(self.svc.tickets.get(t, "203.0.113.1"))  # 옛 표는 바로 버림
        self.assertEqual(self.svc.store.data["topics"], topics)  # 같은 휴대폰이라 주제는 그대로 (알림 구독이 안 끊김)
        self.assertIn("휴대폰이 다시 연결됐어요 · Galaxy · Chrome", self.fb.lines)
        for i in range(remote.MAX_DEVICES - 2):
            pair_device(self.svc, f"폰{i}")
        self.assertEqual(len(self.svc.store.data["devices"]), remote.MAX_DEVICES)
        pair_device(self.svc, "Galaxy · Chrome", install=inst)  # 꽉 차도 같은 휴대폰의 다시 연결은 됨
        self.assertEqual(len(self.svc.store.data["devices"]), remote.MAX_DEVICES)
        with self.assertRaises(remote.PairError):
            pair_device(self.svc, "새 폰", install=remote._b64u(os.urandom(16)))

    def test_bad_install_id_is_ignored(self):
        for bad in ("../../x", "a" * 100, "짧음"):
            c = pair_device(self.svc, "폰", install=bad)
            self.assertNotIn("install", self.svc.store.device(c["device"]["id"]))
        self.assertEqual(len(self.svc.store.data["devices"]), 3)  # 바꿔 끼우지 않고 따로

    def test_pc_shows_why_code_was_dropped(self):
        self.svc.state, self.svc.url = "on", "https://a-b.trycloudflare.com"
        p = self.svc.pair_start()
        self.assertIsNone(self.svc.pc_status()["pairNote"])
        cur = self.svc.pairing.live()
        for _ in range(remote.CODE_FAILS):
            with self.assertRaises(remote.PairError):
                self.svc.r_pair(pair_body({"proof": os.urandom(16)}, ts=self.clock()))
        st = self.svc.pc_status()
        self.assertIsNone(st["pair"])
        self.assertIn("다른 사람이 코드를 맞히려 했을 수 있어요", st["pairNote"])
        self.assertTrue(p["code"] and cur)
        self.svc.pair_start()
        self.assertIsNone(self.svc.pc_status()["pairNote"])

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
