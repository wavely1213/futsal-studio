"""원격 접속 프로토콜 맞물림 시험: PC(remote.py) ⇄ 휴대폰 페이지(proto.js, 와벨리 저장소 public/futsal/)
— 저장소 폴더에서 python3 -m unittest tests.test_remote_proto

node ≥ 20 과 페이지 폴더(FUTSAL_SITE_DIR, 기본 /home/user/wavely/public/futsal)가 있을 때만 돈다 (없으면 건너뜀).
파이썬이 잠근 것을 node 가 열고, node 가 서명한 것을 파이썬이 확인한다. 코드 → 만남 주제·열쇠도 같아야 한다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote  # noqa: E402
from remote_fixture import Clock, make_home, pair_device, service  # noqa: E402

SITE = Path(os.environ.get("FUTSAL_SITE_DIR") or "/home/user/wavely/public/futsal")
NODE = shutil.which("node")


def _node_ok():
    if not NODE or not (SITE / "proto.js").is_file():
        return False
    try:
        v = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=20).stdout.strip().lstrip("v")
        return int(v.split(".")[0]) >= 20
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


RUNNER = r"""
import * as P from %s;
const cmd = JSON.parse(await new Promise(r => { let s = ""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => r(s)); }));
const out = [];
for (const c of cmd) {
  if (c.op === "norm") out.push(P.normCode(c.s));
  else if (c.op === "derive") { const d = await P.derivePair(c.code); out.push({ topic: d.topic, pair: await P.openPair(c.msg, d.topic, d.key),
    proof: await P.pairProof(d.proof, c.nonce || "AAAAAAAAAAAAAAAAAAAAAA", c.ts || 1), ex: [d.key.extractable, d.proof.extractable, d.proof.usages] }); }
  else if (c.op === "pairresp") { const d = await P.derivePair(c.code); out.push(await P.openPairResponse(c.resp, d.topic, d.key)); }
  else if (c.op === "beacon") { const k = await P.importKeys(c.auth, c.beacon); out.push(await P.openBeacon(c.msg, c.device, k.beacon)); }
  else if (c.op === "topics") { const k = await P.importKeys(c.auth, c.beacon); out.push(await P.openTopics(c.blob, k.beacon, c.pc, c.device)); }
  else if (c.op === "sign") { const k = await P.importKeys(c.auth, c.beacon); out.push(await P.authHeader(k.auth, c.device, c.host, c.method, c.path, c.body, c.ts, c.nonce)); }
  else if (c.op === "hostof") out.push(P.hostOf(c.url));
  else if (c.op === "hangul") out.push([P.hasHangul(c.s), P.fromHangul(c.s), P.normCode(P.fromHangul(c.s))]);
  else if (c.op === "inapp") out.push(P.inAppBrowser(c.ua));
  else if (c.op === "host") out.push(P.hostOk(c.url, c.dev));
  else if (c.op === "hint") out.push(P.hintUrl(c.u, c.dev));
  else if (c.op === "hash") out.push(P.parseHash(c.h));
  else if (c.op === "extractable") { const k = await P.importKeys(c.auth, c.beacon); out.push([k.auth.extractable, k.beacon.extractable, k.auth.usages, k.beacon.usages]); }
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(_node_ok(), "node 20 이상이나 페이지 폴더(FUTSAL_SITE_DIR)가 없어 건너뜀")
class ProtoInteropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.runner = cls.tmp / "run.mjs"
        cls.runner.write_text(RUNNER % json.dumps((SITE / "proto.js").resolve().as_uri()), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def node(self, cmds):
        r = subprocess.run([NODE, str(self.runner)], input=json.dumps(cmds), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return json.loads(r.stdout)

    def setUp(self):
        self.home, self.cleanup = make_home()
        self.addCleanup(self.cleanup)
        self.clock = Clock()
        self.svc, self.fb = service(self.home, self.clock)

    def test_code_normalize_same_both_sides(self):
        cases = ["7k3q-m9xd-2p", " 7K3Q M9XD 2P", "OOOO-IIII-LL", "UUUU-UUUU-UU", "", "7K3Q-M9XD", "abcdefghjk", "7K3Q-M9XD-2P-"]
        self.assertEqual(self.node([{"op": "norm", "s": c} for c in cases]), [remote.norm_code(c) for c in cases])

    def test_pairing_rendezvous_opens_in_page(self):
        code = remote.new_code()
        topic, key, _ = remote.derive_pair(code)
        msg = "fsp1." + remote.gcm_seal(key, json.dumps({"v": 1, "url": "https://a-b-c.trycloudflare.com", "pc": {"id": "0" * 16, "name": "내 PC"}, "exp": 1}),
                                         "fsp1|" + topic)
        bad = "fsp1." + remote.gcm_seal(key, json.dumps({"v": 1}), "fsp1|other")
        out = self.node([{"op": "derive", "code": code, "msg": msg}, {"op": "derive", "code": code, "msg": bad}])
        self.assertEqual(out[0]["topic"], topic)
        self.assertEqual(out[0]["pair"]["url"], "https://a-b-c.trycloudflare.com")
        self.assertEqual(out[0]["pair"]["pc"]["name"], "내 PC")
        self.assertIsNone(out[1]["pair"])  # 다른 주제에 묶인 글은 못 엶
        self.assertEqual(out[0]["ex"], [False, False, ["sign"]])  # 코드 열쇠도 가져오기 전용

    def test_pair_proof_and_sealed_response_match_pc(self):
        """짝짓기 v2: 페이지의 증명을 PC 가 받고, PC 의 잠긴 대답을 페이지가 엶 (코드가 다르면 못 엶 · 대답을 바꾸면 못 엶)."""
        self.svc.state, self.svc.url = "on", "https://quiet-river-sample-x.trycloudflare.com"
        p = self.svc.pairing.create()
        code, nonce, ts = p["code"], remote._b64u(os.urandom(16)), int(self.clock())
        out = self.node([{"op": "derive", "code": code, "msg": "", "nonce": nonce, "ts": ts}])[0]
        self.assertEqual(out["proof"], remote.pair_proof(p["proof"], nonce, ts))
        resp = self.svc.r_pair({"v": 2, "nonce": nonce, "ts": ts, "proof": out["proof"], "name": "node"})
        other = remote.new_code()
        tampered = dict(resp, device={"id": "f" * 16})
        got = self.node([{"op": "pairresp", "code": code, "resp": resp}, {"op": "pairresp", "code": other, "resp": resp},
                         {"op": "pairresp", "code": code, "resp": tampered}, {"op": "pairresp", "code": code, "resp": {"keys": {"auth": "x"}}}])
        self.assertEqual(got[0]["url"], "https://quiet-river-sample-x.trycloudflare.com")
        self.assertEqual(got[0]["keys"]["auth"], self.svc.store.device(resp["device"]["id"])["auth"])
        self.assertEqual(got[0]["notify"]["topic"], self.svc.store.data["topics"]["notify"])
        self.assertEqual(got[1:], [None, None, None])

    def test_sealed_topics_open_in_page(self):
        a = pair_device(self.svc, "a")
        st = self.svc.r_status(self.svc.store.device(a["device"]["id"]), 0)
        self.assertNotIn(self.svc.store.data["topics"]["notify"], json.dumps(st))  # 주제는 잠긴 채로만
        pc = self.svc.store.data["pc"]["id"]
        out = self.node([{"op": "topics", "blob": st["topics"], "pc": pc, "device": a["device"]["id"], **a["keys"]},
                         {"op": "topics", "blob": st["topics"], "pc": pc, "device": "0" * 16, **a["keys"]}])
        self.assertEqual(out[0]["notify"]["topic"], self.svc.store.data["topics"]["notify"])
        self.assertEqual(out[0]["beacon"]["topic"], self.svc.store.data["topics"]["beacon"])
        self.assertIsNone(out[1])

    def test_korean_keyboard_code_converted(self):
        """검토: 한국 휴대폰 기본 자판(한글)으로 코드를 치면 '10글자를 다시 확인' → 누른 영문 글쇠로 바꿈."""
        code = "7K3QM9XD2P"
        typed = "7ㅏ3ㅂ-ㅡ9ㅌㅇ-2ㅔ"  # 한글 자판에서 7K3Q-M9XD-2P 를 누른 모습
        composed = "7ㅏ3ㅂㅡ9ㅌ아2ㅔ"  # 자음 뒤 모음이 붙어 글자가 된 경우 (d + k → 아)
        out = self.node([{"op": "hangul", "s": typed}, {"op": "hangul", "s": composed}, {"op": "hangul", "s": "ABCD"}, {"op": "hangul", "s": "닭꽥"}])
        self.assertEqual(out[0], [True, "7k3q-m9xd-2p", code])
        self.assertTrue(out[1][0])
        self.assertEqual(out[1][1], "7k3qm9xdk2p")  # 'ㅇ'+'ㅏ'(아) 는 d k 로 풀림 → 11글자라 코드 아님 (안내로)
        self.assertEqual(out[2], [False, "ABCD", None])
        self.assertEqual(out[3][1], "ekfrRhor")  # 닭 = e k fr · 꽥 = R ho r

    def test_page_strips_same_control_chars_as_pc(self):
        """휴대폰 페이지 app.js 의 CTRL 과 PC remote._CTRL 이 같은 글자를 뺌 (줄 구분·폭 없는 글자 포함)."""
        line = next(ln for ln in (SITE / "app.js").read_text(encoding="utf-8").splitlines() if ln.startswith("const CTRL ="))
        samples = ["a\u2028b", "c\u0085d", "e\u2029f", "g\u200bh\u200di", "\u202ej\u2066k\u2069", "l\ufeffm", "n\x00\x1b[31mo", "p\tq\nr", "보통 글자"]
        r = subprocess.run([NODE, "-e", line + "\nconsole.log(JSON.stringify(" + json.dumps(samples) + ".map(s => s.replace(CTRL, ''))))"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(r.stdout), [remote._CTRL.sub("", x) for x in samples])

    def test_in_app_browser_detected(self):
        uas = {"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 KAKAOTALK 10.4.5": "카카오톡",
               "Mozilla/5.0 (Linux; Android 14; SM-S918N Build/UP1A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120 Mobile Safari/537.36 NAVER(inapp; search; 2000; 12.1.0)": "네이버",
               "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 300.0": "인스타그램",
               "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1": None,
               "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36": None,
               "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/24.0 Chrome/117.0 Mobile Safari/537.36": None}
        self.assertEqual(self.node([{"op": "inapp", "ua": u} for u in uas]), list(uas.values()))

    def test_beacon_only_opens_with_own_key(self):
        a = pair_device(self.svc, "a")
        b = pair_device(self.svc, "b")
        self.svc.state, self.svc.url = "on", "https://x-y.trycloudflare.com"
        body = self.svc.beacon_body()
        seq = self.svc.store.data["seq"]
        out = self.node([{"op": "beacon", "msg": body, "device": a["device"]["id"], **a["keys"]},
                         {"op": "beacon", "msg": body, "device": b["device"]["id"], **b["keys"]},
                         {"op": "beacon", "msg": body, "device": a["device"]["id"], **b["keys"]},  # 남의 열쇠
                         {"op": "beacon", "msg": "fsb1.xx", "device": a["device"]["id"], **a["keys"]}])
        self.assertEqual(out[0]["url"], "https://x-y.trycloudflare.com")
        self.assertEqual(out[0]["seq"], seq)
        self.assertEqual(out[0]["state"], "on")
        self.assertEqual(out[1]["pc"], self.svc.store.data["pc"]["id"])
        self.assertIsNone(out[2])
        self.assertIsNone(out[3])

    def test_tampered_beacon_seq_rejected(self):
        a = pair_device(self.svc, "a")
        self.svc.state, self.svc.url = "on", "https://x-y.trycloudflare.com"
        body = self.svc.beacon_body()
        outer = json.loads(remote._unb64u(body[5:]))
        outer["seq"] += 5  # 옛 글의 seq 만 키워 다시 올려도 (aad 에 묶여) 못 엶
        forged = "fsb1." + remote._b64u(json.dumps(outer).encode())
        out = self.node([{"op": "beacon", "msg": forged, "device": a["device"]["id"], **a["keys"]}])
        self.assertIsNone(out[0])

    def test_page_signature_verified_by_pc(self):
        a = pair_device(self.svc, "a")
        ts = int(self.clock())
        host = "quiet-river-sample-x.trycloudflare.com"
        body = json.dumps({"action": "cancel"})
        reqs = [("GET", "/r/status?since=12", ""), ("POST", "/r/action", body), ("GET", "/r/library", "")]
        out = self.node([{"op": "sign", "device": a["device"]["id"], "host": host, "method": m, "path": p, "body": b, "ts": ts, **a["keys"]} for m, p, b in reqs])
        for (m, p, b), h in zip(reqs, out):
            self.assertTrue(h.startswith("FSR2 "))
            self.assertEqual(self.svc.auth.verify(h, m, p, b.encode(), host)["id"], a["device"]["id"])
        h2 = self.node([{"op": "sign", "device": a["device"]["id"], "host": "other-host-x.trycloudflare.com", "method": "GET", "path": "/r/library",
                         "body": "", "ts": ts, **a["keys"]}])[0]
        with self.assertRaises(remote.AuthError):  # 다른 주소로 보낸 서명은 이 PC 에서 안 맞음
            self.svc.auth.verify(h2, "GET", "/r/library", b"", host)
        h = self.node([{"op": "sign", "device": a["device"]["id"], "host": host, "method": "POST", "path": "/r/action", "body": body, "ts": ts,
                        "nonce": "A" * 22, **a["keys"]}])[0]
        self.assertEqual(h.split(".")[2], "A" * 22)
        self.assertEqual(h.split(".")[3], remote.sign(remote._unb64u(a["keys"]["auth"]), host, "POST", "/r/action", str(ts), "A" * 22, body.encode()))
        self.assertEqual(self.node([{"op": "hostof", "url": "https://Quiet-River.trycloudflare.com"}, {"op": "hostof", "url": "http://127.0.0.1:8972"}]),
                         ["quiet-river.trycloudflare.com", "127.0.0.1:8972"])

    def test_keys_are_non_extractable_in_page(self):
        a = pair_device(self.svc, "a")
        out = self.node([{"op": "extractable", **a["keys"]}])[0]
        self.assertEqual(out[:2], [False, False])
        self.assertEqual(out[2], ["sign"])
        self.assertEqual(out[3], ["decrypt"])

    def test_page_only_trusts_tunnel_hosts(self):
        cases = [("https://abc-def.trycloudflare.com", False, "https://abc-def.trycloudflare.com"),
                 ("https://abc-def.trycloudflare.com/", False, "https://abc-def.trycloudflare.com"),
                 ("http://abc-def.trycloudflare.com", False, None), ("https://evil.com", False, None),
                 ("https://a.trycloudflare.com.evil.com", False, None), ("https://x.y.trycloudflare.com", False, None),
                 ("https://u:p@abc.trycloudflare.com", False, None), ("https://abc.trycloudflare.com:8443", False, None),
                 ("https://abc.trycloudflare.com/r/x", False, None), ("javascript:alert(1)", False, None),
                 ("http://127.0.0.1:8972", False, None), ("http://127.0.0.1:8972", True, "http://127.0.0.1:8972")]
        out = self.node([{"op": "host", "url": u, "dev": d} for u, d, _ in cases])
        self.assertEqual(out, [w for _, _, w in cases])
        hints = self.node([{"op": "hint", "u": "abc-def", "dev": False}, {"op": "hint", "u": "evil.com", "dev": False},
                           {"op": "hint", "u": "127.0.0.1:8972", "dev": False}, {"op": "hint", "u": "127.0.0.1:8972", "dev": True}])
        self.assertEqual(hints, ["https://abc-def.trycloudflare.com", None, None, "http://127.0.0.1:8972"])

    def test_pair_link_parsed_by_page(self):
        self.svc.state, self.svc.url = "on", "https://quiet-river.trycloudflare.com"
        link = self.svc.pair_start()["link"]
        out = self.node([{"op": "hash", "h": "#" + link.split("#", 1)[1]}])[0]
        self.assertEqual(out["u"], "quiet-river")
        self.assertEqual(out["pair"], self.svc.pairing.live()["code"])


if __name__ == "__main__":
    unittest.main()
