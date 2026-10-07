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
  else if (c.op === "derive") { const d = await P.derivePair(c.code); out.push({ topic: d.topic, pair: await P.openPair(c.msg, d.topic, d.key) }); }
  else if (c.op === "beacon") { const k = await P.importKeys(c.auth, c.beacon); out.push(await P.openBeacon(c.msg, c.device, k.beacon)); }
  else if (c.op === "sign") { const k = await P.importKeys(c.auth, c.beacon); out.push(await P.authHeader(k.auth, c.device, c.method, c.path, c.body, c.ts, c.nonce)); }
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
        topic, key = remote.derive_pair(code)
        msg = "fsp1." + remote.gcm_seal(key, json.dumps({"v": 1, "url": "https://a-b-c.trycloudflare.com", "pc": {"id": "0" * 16, "name": "내 PC"}, "exp": 1}),
                                         "fsp1|" + topic)
        bad = "fsp1." + remote.gcm_seal(key, json.dumps({"v": 1}), "fsp1|other")
        out = self.node([{"op": "derive", "code": code, "msg": msg}, {"op": "derive", "code": code, "msg": bad}])
        self.assertEqual(out[0]["topic"], topic)
        self.assertEqual(out[0]["pair"]["url"], "https://a-b-c.trycloudflare.com")
        self.assertEqual(out[0]["pair"]["pc"]["name"], "내 PC")
        self.assertIsNone(out[1]["pair"])  # 다른 주제에 묶인 글은 못 엶

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
        body = json.dumps({"action": "cancel"})
        reqs = [("GET", "/r/status?since=12", ""), ("POST", "/r/action", body), ("GET", "/r/library", "")]
        out = self.node([{"op": "sign", "device": a["device"]["id"], "method": m, "path": p, "body": b, "ts": ts, **a["keys"]} for m, p, b in reqs])
        for (m, p, b), h in zip(reqs, out):
            self.assertEqual(self.svc.auth.verify(h, m, p, b.encode())["id"], a["device"]["id"])
        h = self.node([{"op": "sign", "device": a["device"]["id"], "method": "POST", "path": "/r/action", "body": body, "ts": ts,
                        "nonce": "A" * 22, **a["keys"]}])[0]
        self.assertEqual(h.split(".")[2], "A" * 22)
        self.assertEqual(h.split(".")[3], remote.sign(remote._unb64u(a["keys"]["auth"]), "POST", "/r/action", str(ts), "A" * 22, body.encode()))

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
