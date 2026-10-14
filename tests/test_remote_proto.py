"""원격 접속 프로토콜 맞물림 시험: PC(remote.py) ⇄ 휴대폰 페이지(proto.js, 와벨리 저장소 public/futsal/)
— 저장소 폴더에서 python3 -m unittest tests.test_remote_proto

node ≥ 20 과 페이지 폴더(FUTSAL_SITE_DIR, 기본 /home/user/wavely/public/futsal)가 있을 때만 돈다 (없으면 건너뜀).
밖에서 작업(D-230 · D-233)의 페이지 쪽(nextOk·#resume·앞머리 부트스트랩)은 그 판의 페이지에서만 돈다 (예전 판이면 그 시험만 건너뜀 ·
와벨리 저장소에 합치기 전에는 FUTSAL_SITE_DIR=$SCRATCH/wavely_remote2/public/futsal).
파이썬이 잠근 것을 node 가 열고, node 가 서명한 것을 파이썬이 확인한다. 코드 → 만남 주제·열쇠도 같아야 한다.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote  # noqa: E402
import rstudio  # noqa: E402
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
  else if (c.op === "next") out.push(P.nextOk ? P.nextOk(c.p) : "missing");
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

    def _studio_page(self):
        if "nextOk" not in (SITE / "proto.js").read_text(encoding="utf-8"):
            self.skipTest("예전 판 페이지 (밖에서 작업 전) · FUTSAL_SITE_DIR 로 새 판을 가리켜 주세요")

    NEXT_CASES = ["/", "/editor", "/thumb", "/editor?name=20260101_a%20b.mp4", "/editor?name=a(1)!.mp4&seq=x", "/thumb?name=" + "%EC%8B%9C" * 30,
                  "", "//evil.com", "https://evil.com", "/api/state", "/editor#x", "/editor?name=<x>", "/s/hello", "/editor?" + "a" * 601,
                  "/editor\\..", "/editor?name=a b", "/thumbx", "/editor/", "javascript:alert(1)"]

    def test_next_path_rule_same_everywhere(self):
        """넘겨준 뒤 갈 곳: PC(rstudio.NEXT_RE) · 인사 쪽(hello.js) · 화면 심(remote_ui.js) · 휴대폰 페이지(proto.nextOk)가 같은 규칙 (열린 이동 없음)."""
        self._studio_page()
        out = self.node([{"op": "next", "p": p} for p in self.NEXT_CASES])
        self.assertEqual(out, [bool(rstudio.NEXT_RE.fullmatch(p)) for p in self.NEXT_CASES])
        src = re.search(r"NEXT_RE = (/.*?/);", (SITE / "proto.js").read_text(encoding="utf-8")).group(1)
        for f in ("hello.js", "remote_ui.js"):
            self.assertIn(src, (Path(__file__).resolve().parents[1] / f).read_text(encoding="utf-8"), f)

    def test_resume_hash_from_lock_page(self):
        """세션이 끝난 스튜디오 화면은 SITE#resume=<encodeURIComponent(갈 곳)> 로 돌려보냄 → 페이지가 같은 갈 곳을 읽음 (아니면 null)."""
        self._studio_page()
        from urllib.parse import quote
        good = "/editor?name=20260101_a%20b%5B1%5D.mp4"
        out = self.node([{"op": "hash", "h": "#resume=" + quote(good, safe="")}, {"op": "hash", "h": "#resume=" + quote("https://evil.com", safe="")},
                         {"op": "hash", "h": "#resume=%2F"}, {"op": "hash", "h": "#pair=7K3Q-M9XD-2P"}])
        self.assertEqual([o["resume"] for o in out], [good, None, "/", None])

    def test_bootstrap_hash_pinned_in_csp(self):
        """index.html 의 앞머리 부트스트랩(인라인 스크립트 하나)은 CSP 에 sha256 으로 박혀 있음 (그 밖의 인라인 스크립트는 안 돎)."""
        html = (SITE / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        if not scripts:
            self.skipTest("예전 판 페이지 (부트스트랩 전)")
        self.assertEqual(len(scripts), 1)
        digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode()
        csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', html).group(1)
        self.assertIn(f"'sha256-{digest}'", csp)
        self.assertNotIn("unsafe-inline", csp.split("script-src", 1)[1].split(";")[0])
        self.assertNotIn('"/futsal', html)  # 앞머리 글자는 페이지 파일에 없음 (주소에서 읽음 · 지금은 뿌리 '/' · D-240)
        self.assertIn('rel:"manifest",href:r+"/manifest.webmanifest",crossorigin:"use-credentials"', scripts[0])  # 로그인한 미리보기에서도 manifest
        for f in ("app.js", "sw.js", "proto.js"):
            code = "\n".join(ln for ln in (SITE / f).read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("//"))
            self.assertNotIn('"/futsal', code, f)
        man = json.loads((SITE / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertTrue(all(not i["src"].startswith("/") for i in man["icons"]))
        self.assertEqual((man["start_url"], man["scope"]), ("./", "./"))  # manifest 자리 기준 (뿌리면 '/')

    def test_pair_link_parsed_by_page(self):
        self.svc.state, self.svc.url = "on", "https://quiet-river.trycloudflare.com"
        link = self.svc.pair_start()["link"]
        out = self.node([{"op": "hash", "h": "#" + link.split("#", 1)[1]}])[0]
        self.assertEqual(out["u"], "quiet-river")
        self.assertEqual(out["pair"], self.svc.pairing.live()["code"])


LOGIN_RUNNER = r"""
import * as P from %s;
const cmd = JSON.parse(await new Promise(r => { let s = ""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => r(s)); }));
const hexb = h => new Uint8Array(h.match(/../g).map(x => parseInt(x, 16)));
const out = [];
for (const c of cmd) {
  try {
    if (c.op === "canon") out.push(c.list.map(s => [P.keysOf(s), P.canonId(s), P.canonPw(s), P.idProblem(s), P.pwProblem(s)]));
    else if (c.op === "derive") { const d = await P.deriveLogin(c.cid); out.push({ topic: d.topic, idc: d.idc, ex: d.bkey.extractable, usages: d.bkey.usages,
      rec: c.msg ? await P.openLookup(d.bkey, c.topic || d.topic, c.msg, true) : null }); }
    else if (c.op === "modpow") out.push(P.modPow(BigInt("0x" + c.b), BigInt("0x" + c.e), BigInt("0x" + c.m)).toString(16));
    else if (c.op === "k") out.push((await P.srpK()).toString(16));
    else if (c.op === "x") out.push((await P.srpX(c.pw, P.unb64u(c.salt), c.iters)).toString(16));
    else if (c.op === "start") { const s = P.srpStart(hexb(c.a)); out.push(s.Ab64); }
    else if (c.op === "proof" || c.op === "finish") {
      const s = P.srpStart(hexb(c.a));
      const pr = await P.srpProof(s, c.st, c.o);
      if (c.op === "proof") out.push({ M1: P.b64u(pr.M1) });
      else {
        const ok = await P.verifyM2(pr.K, pr.T, pr.M1, c.fin.sealed, c.fin.M2);
        const opened = ok ? await P.openLoginSealed(pr.K, pr.T, c.fin.sealed, c.st.login, c.o.pcId) : null;
        out.push({ ok, inner: opened && opened.inner, ex: opened && [opened.keys.auth.extractable, opened.keys.beacon.extractable],
          tamperedM2: await P.verifyM2(pr.K, pr.T, pr.M1, c.fin.sealed.slice(0, -2) + "AA", c.fin.M2),
          tamperedSeal: await P.openLoginSealed(pr.K, pr.T, c.fin.sealed.slice(0, -4) + "AAAA", c.st.login, c.o.pcId),
          otherPc: await P.openLoginSealed(pr.K, pr.T, c.fin.sealed, c.st.login, "ffffffffffffffff") });
      }
    }
    else if (c.op === "hash") out.push(P.parseHash(c.h).kill);
    else if (c.op === "idonly") out.push({ owner: P.OWNER_ID, list: c.list.map(s => [P.idOnlyOk(s), P.idSecret(s), P.canonPw(P.idSecret(s))]) });
  } catch (e) { out.push({ error: String(e && e.message || e) }); }
}
console.log(JSON.stringify(out));
"""


def _login_page():
    try:
        return _node_ok() and "deriveLogin" in (SITE / "proto.js").read_text(encoding="utf-8")
    except OSError:
        return False


@unittest.skipUnless(_login_page(), "node 20 이상이나 아이디·비밀번호로 연결 판 페이지(FUTSAL_SITE_DIR)가 없어 건너뜀")
class LoginProtoInteropTests(unittest.TestCase):
    """아이디·비밀번호로 연결 (D-250~D-253): canon v1 표 · PC 찾기 열쇠 · SRP-6a (node 클라이언트 ⇄ 파이썬 PC) · #kill=."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.runner = cls.tmp / "login.mjs"
        cls.runner.write_text(LOGIN_RUNNER % json.dumps((SITE / "proto.js").resolve().as_uri()), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def node(self, cmds):
        r = subprocess.run([NODE, str(self.runner)], input=json.dumps(cmds), capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_canon_table_equal(self):
        import rlogin
        table = ["김코치77", "RLAZHCL77", "까치", "가치", "ㅒㅖ", "닭", "달ㄱ", "닭", "ㄷㅏㄹㄱ", "Ｋｉｍ　Ｃｏａｃｈ", "₩￦\\",
                 " kim.coach-7_7 ", "  ab cd 12  \t\n", "노을축구공바다4827", "SHDMFCNRRNRHDQKEK4827", "ㆍㆍㆍ아이디만", "축구⚽코치12",
                 "風林火山코치", "ᅀᅀabcdef1234", "ÀÉÎ", "ﬁ ligature", "①②③④⑤⑥", "가나다", "철수", "01012345678", "a" * 70, "", "Ǆ"]
        got = self.node([{"op": "canon", "list": table}])[0]
        js_pw = {"empty": "empty", "chars": "chars", "short": "short", "long": "long"}
        for s, (keys, cid, cpw, idp, pwp) in zip(table, got):
            self.assertEqual(keys, rlogin.keys_of(s), s)
            self.assertEqual(cid, rlogin.canon_id(s), s)
            self.assertEqual(cpw, rlogin.canon_pw(s), s)
            want_id = rlogin.id_problem(s)
            self.assertEqual(idp, None if want_id == "reserved" else want_id, s)   # 흔한 아이디 목록은 PC 에서만
            want = [x for x in rlogin.pw_problems(s) if x in js_pw]
            self.assertEqual(pwp, want[0] if want else None, s)

    def test_derive_login_equal(self):
        import rlogin
        d = rlogin.derive_login("rlazhcl77")
        got = self.node([{"op": "derive", "cid": "rlazhcl77"}])[0]
        self.assertEqual((got["topic"], got["idc"]), (d["topic"], d["idc"]))
        self.assertEqual((got["topic"], got["idc"]), ("fsl1d315a8a97aaa3d816a1584b", "nBjO1vuzIzg432UZX9coMQ"))   # tests.test_rlogin 과 같은 수
        self.assertEqual((got["ex"], got["usages"]), (False, ["decrypt"]))

    def test_open_lookup_interop(self):
        import rlogin
        d = rlogin.derive_login("rlazhcl77")
        on = {"v": 1, "pc": "0123456789abcdef", "seq": 4, "ts": 1_800_000_000, "state": "on", "reason": None, "url": "http://127.0.0.1:9541"}
        off = dict(on, state="off", reason="idle", url=None)
        bad_url = dict(on, url="https://evil.example")
        msgs = [rlogin.seal_lookup(d["bkey"], d["topic"], x, remote.gcm_seal) for x in (on, off, bad_url)]
        got = self.node([{"op": "derive", "cid": "rlazhcl77", "msg": m} for m in msgs]
                        + [{"op": "derive", "cid": "rlazhcl77", "msg": msgs[0], "topic": "fsl" + "0" * 24},
                           {"op": "derive", "cid": "kimcoach99", "msg": msgs[0]}])
        self.assertEqual(got[0]["rec"], on)
        self.assertEqual(got[1]["rec"], off)
        self.assertIsNone(got[2]["rec"])   # 받으면 안 되는 주소
        self.assertIsNone(got[3]["rec"])   # 다른 주제로 옮긴 글
        self.assertIsNone(got[4]["rec"])   # 다른 아이디

    def test_rfc_vectors_node(self):
        """node 의 modPow·srpK·srpX 가 RFC 5054 부록 B(1024)·파이썬과 같은지."""
        import rlogin
        N, g, _ = rlogin.group("5054-1024-sha1")
        a = "60975527035CF2AD1989806F0407210BC81EDC04E2762A56AFD529DDDA2D4393"
        want_A = format(rlogin.client_start("5054-1024-sha1", int(a, 16))[1], "x")
        salt = rlogin._b64u(bytes(range(16)))
        got = self.node([{"op": "modpow", "b": format(g, "x"), "e": a, "m": format(N, "x")}, {"op": "k"},
                         {"op": "x", "pw": "shdmfcnrrnrhdqkek4827", "salt": salt, "iters": 100000}])
        self.assertEqual(got[0], want_A)
        self.assertEqual(int(got[1], 16), rlogin.srp_k())
        self.assertEqual(int(got[2], 16), rlogin.srp_x("shdmfcnrrnrhdqkek4827", bytes(range(16)), 100000))

    def _service(self):
        import rlogin
        home, cleanup = make_home()
        self.addCleanup(cleanup)
        clock = Clock()
        svc, _ = service(home, clock)
        svc.url = "http://127.0.0.1:9541"
        p = __import__("unittest.mock", fromlist=["patch"]).patch.object(rlogin, "LOGIN_ITERS", 100_000)
        p.start()
        self.addCleanup(p.stop)
        svc._collision = lambda cid, timeout=5: "clear"
        svc.login_set("김코치77", "노을축구공바다4827", "노을축구공바다4827", ack=True)
        return svc, clock

    def test_full_handshake_node_client_python_server(self):
        import rlogin
        svc, clock = self._service()
        d = rlogin.derive_login("rlazhcl77")
        a = os.urandom(32).hex()
        A = self.node([{"op": "start", "a": a}])[0]
        self.assertEqual(A, rlogin._b64u(rlogin.pad(rlogin.client_start(a=int(a, 16))[1], rlogin.group()[0])))
        for remember, name in ((False, "Windows 노트북 · Chrome"), (True, "iPhone · Safari · 홈 화면")):
            st = svc.r_login_start({"v": 1, "grp": rlogin.GROUP, "idc": d["idc"], "A": A}, "198.51.100.1", "", "KR")
            o = {"canonPw": "SHDMFCNRRNRHDQKEK4827".lower(), "host": svc.host(), "pcId": st["pc"]["id"], "idc": d["idc"], "remember": remember,
                 "name": name, "install": "i" * 22 if remember else ""}
            m1 = self.node([{"op": "proof", "a": a, "st": st, "o": o}])[0]
            self.assertNotIn("error", m1)
            fin = svc.r_login_finish({"v": 1, "login": st["login"], "M1": m1["M1"], "remember": remember, "name": name, "install": o["install"]},
                                     "198.51.100.1", "", "KR")
            res = self.node([{"op": "finish", "a": a, "st": st, "o": o, "fin": fin}])[0]
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["inner"]["via"], "login")
            self.assertEqual(bool(res["inner"].get("guest")), not remember)
            self.assertIsNone(res["inner"]["keys"])
            self.assertEqual(res["ex"], [False, False])   # test_sealed_keys_non_extractable
            self.assertFalse(res["tamperedM2"])
            self.assertIsNone(res["tamperedSeal"])
            self.assertIsNone(res["otherPc"])

    def test_id_only_rule_and_secret_same_both_sides(self):
        """아이디만 (D-290): 소유자 아이디 · 비밀(맞춘 아이디)이 PC 와 같음 — 한글 자판·대문자·점으로 친 꼴도."""
        import rlogin
        table = ["wavely1213", "WAVELY1213", "ㅈㅁㅍ디ㅛ1213", " wavely.12-13 ", "wavely12134", "kimcoach99", "김코치77", ""]
        got = self.node([{"op": "idonly", "list": table}])[0]
        self.assertEqual(got["owner"], rlogin.PRESET_ID)
        for s, (ok, sec, cpw) in zip(table, got["list"]):
            self.assertEqual(ok, rlogin.id_only_ok(s), s)
            self.assertEqual(sec, rlogin.id_secret(s), s)
            self.assertEqual(cpw, rlogin.canon_pw(rlogin.id_secret(s)), s)
        self.assertEqual([x[0] for x in got["list"]], [True, True, True, True, False, False, False, False])

    def test_id_only_lookup_flag_and_handshake(self):
        """아이디만 PC: 'PC 찾기' 기록에 idOnly 표시 → 페이지가 맞춘 아이디를 비밀로 써서 SRP 가 끝까지 맞음 (M2 · 봉투)."""
        import rlogin
        home, cleanup = make_home()
        self.addCleanup(cleanup)
        svc, _ = service(home, Clock())
        svc.url = "http://127.0.0.1:9541"
        svc._collision = lambda cid, timeout=5: "clear"
        svc.login_set("WAVELY1213", id_only=True, ack=True)
        lg = svc.store.login()
        topic, body = svc._publish_lookup(direct=True)
        d = rlogin.derive_login("wavely1213")
        rec = self.node([{"op": "derive", "cid": "wavely1213", "msg": body}])[0]["rec"]
        self.assertIs(rec["idOnly"], True)
        self.assertEqual(topic, lg["topic"])
        cpw = self.node([{"op": "idonly", "list": ["ㅈㅁㅍ디ㅛ1213"]}])[0]["list"][0][2]
        a = os.urandom(32).hex()
        A = self.node([{"op": "start", "a": a}])[0]
        st = svc.r_login_start({"v": 1, "grp": rlogin.GROUP, "idc": d["idc"], "A": A}, "198.51.100.1", "", "KR")
        o = {"canonPw": cpw, "host": svc.host(), "pcId": st["pc"]["id"], "idc": d["idc"], "remember": True,
             "name": "Windows 노트북 · Chrome", "install": "i" * 22}
        m1 = self.node([{"op": "proof", "a": a, "st": st, "o": o}])[0]
        fin = svc.r_login_finish({"v": 1, "login": st["login"], "M1": m1["M1"], "remember": True, "name": o["name"], "install": o["install"]},
                                 "198.51.100.1", "", "KR")
        res = self.node([{"op": "finish", "a": a, "st": st, "o": o, "fin": fin}])[0]
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["inner"]["via"], "login")

    def test_tampered_B_M2_sealed_rejected(self):
        import rlogin
        N = rlogin.group()[0]
        st = {"v": 1, "login": "L" * 22, "grp": rlogin.GROUP, "salt": rlogin._b64u(bytes(16)), "iters": 100000,
              "B": rlogin._b64u(rlogin.pad(N - 1, N)), "pc": {"id": "0123456789abcdef"}}
        o = {"canonPw": "shdmfcnrrnrhdqkek4827", "host": "a.trycloudflare.com", "pcId": "0123456789abcdef", "idc": "I" * 22, "remember": False,
             "name": "x", "install": ""}
        bad = [dict(st, B=rlogin._b64u(bytes(384))), dict(st, B=rlogin._b64u(N.to_bytes(384, "big"))),
               dict(st, B=rlogin._b64u(bytes(383))), dict(st, salt=rlogin._b64u(bytes(15))), dict(st, login="short"),
               dict(st, pc={"id": "ffffffffffffffff"})]
        got = self.node([{"op": "proof", "a": "11" * 32, "st": b, "o": o} for b in bad])
        for g in got:
            self.assertIn("error", g)

    def test_server_group_refused(self):
        import rlogin
        st = {"login": "L" * 22, "grp": "5054-1024-sha1", "salt": rlogin._b64u(bytes(16)), "iters": 100000, "B": rlogin._b64u(bytes([1]) * 128),
              "pc": {"id": "0123456789abcdef"}}
        got = self.node([{"op": "proof", "a": "11" * 32, "st": st, "o": {"pcId": "0123456789abcdef", "canonPw": "x" * 8, "host": "h", "idc": "i"}}])
        self.assertEqual(got[0], {"error": "group"})

    def test_iters_out_of_bounds_refused(self):
        import rlogin
        N = rlogin.group()[0]
        base = {"login": "L" * 22, "grp": rlogin.GROUP, "salt": rlogin._b64u(bytes(16)), "B": rlogin._b64u(rlogin.pad(N - 1, N)),
                "pc": {"id": "0123456789abcdef"}}
        o = {"pcId": "0123456789abcdef", "canonPw": "x" * 8, "host": "h", "idc": "i", "remember": False, "name": "n", "install": ""}
        got = self.node([{"op": "proof", "a": "11" * 32, "st": dict(base, iters=n), "o": o} for n in (99_999, 2_000_001, "600000", 1.5)])
        self.assertEqual(got, [{"error": "iters"}] * 4)

    def test_B_zero_u_zero_refused(self):
        import rlogin
        N = rlogin.group()[0]
        base = {"login": "L" * 22, "grp": rlogin.GROUP, "salt": rlogin._b64u(bytes(16)), "iters": 100000, "pc": {"id": "0123456789abcdef"}}
        o = {"pcId": "0123456789abcdef", "canonPw": "x" * 8, "host": "h", "idc": "i", "remember": False, "name": "n", "install": ""}
        got = self.node([{"op": "proof", "a": "11" * 32, "st": dict(base, B=rlogin._b64u(bytes(384))), "o": o},
                         {"op": "proof", "a": "11" * 32, "st": dict(base, B=rlogin._b64u(N.to_bytes(384, "big"))), "o": o}])
        self.assertEqual(got, [{"error": "B"}, {"error": "B"}])

    def test_parse_hash_kill(self):
        c, t = "fsc" + "a" * 24, "Ab_-" * 6
        got = self.node([{"op": "hash", "h": f"#kill={c}.{t}.1800000000"}, {"op": "hash", "h": f"#kill={c}.{t}"},
                         {"op": "hash", "h": f"#kill=fsx{'a' * 24}.{t}.1"}, {"op": "hash", "h": f"#kill={c}.{t[:-1]}.1"},
                         {"op": "hash", "h": f"#kill={c}.{t}.12a"}, {"op": "hash", "h": "#pair=ABCDEFGHJK"}])
        self.assertEqual(got[0], {"control": c, "token": t, "at": 1800000000})
        self.assertEqual(got[1:], [None] * 5)

    def test_index_html_and_vercel_json_unchanged(self):
        """아이디·비밀번호로 연결은 index.html(CSP)·vercel.json 을 바꾸지 않음 (이미 ntfy.sh·*.trycloudflare.com 허용 · D-250).
        D-310: 머리글 이름 글자만 '휴대폰으로 보기' → '실행기' (CSP 줄은 그대로)."""
        self.assertEqual(hashlib.sha256((SITE / "index.html").read_bytes()).hexdigest(),
                         "93087dd38e3706b3d18ddd8c3ee2e4bee20ff44c1fa0c06f24165ed9569a9c5c")
        vj = SITE.parent.parent / "vercel.json"
        if vj.is_file():
            self.assertEqual(hashlib.sha256(vj.read_bytes()).hexdigest(), "f637dc2a0b0cc993ea86d93cfed12f14d9ef27e79cb51dd4b7ca79f1b5c3392c")


if __name__ == "__main__":
    unittest.main()
