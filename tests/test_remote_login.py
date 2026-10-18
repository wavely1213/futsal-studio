"""아이디·비밀번호로 연결 — 서비스·원격 리스너 (D-250~D-258) · python3 -m unittest tests.test_remote_login

개발 모드(FUTSAL_REMOTE_DEV=1) 원격 리스너를 임의 포트에 띄우고 가짜 ntfy(remote_fixture.FakeNtfy)를 쓴다.
클라이언트 쪽 SRP 는 rlogin 의 시험용 함수로 (페이지 proto.js 와 같은 계산은 tests.test_remote_proto 가 node 로 확인).
검증값 만들기는 빨리 돌게 PBKDF2 횟수를 100,000 으로 줄인다 (rlogin.LOGIN_ITERS · 페이지가 받는 가장 작은 값).
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import remote  # noqa: E402
import rlogin  # noqa: E402
import rstudio  # noqa: E402
from remote_fixture import ORIGIN, Clock, FakeBridge, FakeNtfy, dev_env, http, pair_device, signed, topics_of, close_service  # noqa: E402

PW = "노을축구공바다4827"
PW_KEYS = rlogin.canon_pw(PW)
ID = "김코치77"
CID = "rlazhcl77"
UA_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
FAKE_PRESET_PW = "가짜미리정한비번8314"   # 시험에서만 · 진짜 미리 정한 비밀번호는 저장소에 없음


def b64u(b):
    return rlogin._b64u(b)


def unb64u(s):
    return rlogin._unb64u(s)


class Client:
    """페이지(proto.srpStart·srpProof·verifyM2·openLoginSealed)와 같은 계산을 파이썬으로."""

    def __init__(self, svc, cid=CID, pw=PW):
        self.svc, self.cid, self.pw = svc, cid, pw
        self.d = rlogin.derive_login(cid)

    def start_body(self, idc=None):
        self.a, self.A = rlogin.client_start()
        return {"v": 1, "grp": rlogin.GROUP, "idc": idc or self.d["idc"], "A": b64u(rlogin.pad(self.A, rlogin.group()[0]))}

    def proof(self, st, remember=True, name="Windows 노트북 · Chrome", install="", pw=None, host=None, idc=None):
        salt, B = unb64u(st["salt"]), int.from_bytes(unb64u(st["B"]), "big")
        u = rlogin.srp_u(self.A, B)
        x = rlogin.srp_x(rlogin.canon_pw(pw or self.pw), salt, st["iters"])
        self.K = rlogin.session_key(rlogin.client_S(B, self.a, x, u))
        self.T = rlogin.transcript(host if host is not None else self.svc.host(), st["pc"]["id"], idc or self.d["idc"], st["login"],
                                   st["grp"], st["iters"], salt, self.A, B)
        self.M1 = rlogin.m1(self.K, self.T, remember, name, install)
        self.login = st["login"]
        return {"v": 1, "login": st["login"], "M1": b64u(self.M1), "remember": remember, "name": name, "install": install}

    def open(self, fin, pc_id):
        if rlogin.m2(self.K, self.T, self.M1, fin["sealed"]) != unb64u(fin["M2"]):
            return None
        return json.loads(remote.gcm_open(rlogin.seal_key(self.K, self.T), fin["sealed"][5:], rlogin.sealed_aad(self.login, pc_id)))


class LoginBase(unittest.TestCase):
    http_listener = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="아이디 연결 "))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.ntfy = FakeNtfy()
        self.addCleanup(self.ntfy.close)
        patches = [mock.patch.dict(os.environ, dev_env()), mock.patch.object(rlogin, "LOGIN_ITERS", 100_000),
                   mock.patch.object(core, "ENGINE_HOME", self.tmp / ".futsal-studio")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("FUTSAL_LOGIN_TIME_SCALE", None)
        self.clock = Clock(time.time())
        self.fb = FakeBridge()
        self.svc = remote.Service(home=self.tmp / ".futsal-studio", clock=self.clock, ntfy=self.ntfy.url)
        self.svc.init(self.fb.bridge())
        self.svc._ctl_timeout = 3
        self.addCleanup(close_service, self.svc)  # 뒤 스레드(remote-watch·remote-publisher)를 다음 시험으로 남기지 않음 (I-510)
        self.lines = self.fb.lines
        if self.http_listener:
            self.assertTrue(self.svc.turn_on())
            end = time.time() + 10
            while self.svc.state != "on" and time.time() < end:
                time.sleep(0.02)
            self.assertEqual(self.svc.state, "on")
            self.port = self.svc.port()
        self.addCleanup(self.svc.turn_off)
        self.code = pair_device(self.svc, "iPhone · Safari")   # QR·코드로 연결한 휴대폰 (관리 권한)

    def set_login(self, id_=ID, pw=PW, **kw):
        return self.svc.login_set(id_, pw, pw, **dict({"force": True, "ack": True}, **kw))

    def login(self, pw=PW, remember=True, name="Windows 노트북 · Chrome", install="", ip="198.51.100.7", cc="KR", ua=UA_WIN, cid=CID):
        c = Client(self.svc, cid, pw)
        st = self.svc.r_login_start(c.start_body(), ip, ua, cc)
        fin = self.svc.r_login_finish(c.proof(st, remember, name, install), ip, ua, cc)
        inner = c.open(fin, st["pc"]["id"])
        self.assertIsNotNone(inner)
        return fin, inner

    def notes(self, kind=None):
        """가짜 ntfy 로 간 알림 (JSON 알림 · kind 는 글 앞부분으로 거름)."""
        out = [(t, m, h) for t, m, h, _ in self.ntfy.posts if isinstance(h, dict) and h.get("title") == "풋살 스튜디오"]
        return [x for x in out if kind is None or kind in x[1]]

    def wait(self, pred, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.05)
        return False

    def lookups(self, topic=None):
        lg = self.svc.store.login()
        topic = topic or lg["topic"]
        bkey = unb64u(lg["bkey"]) if lg and lg["topic"] == topic else None
        return topic, bkey

    def lookup_records(self, cid=CID):
        d = rlogin.derive_login(cid)
        self.wait(lambda: self.ntfy.topic(d["topic"]), 3)
        return [rlogin.open_lookup(d["bkey"], d["topic"], m, remote.gcm_open) for _, m, _, _ in self.ntfy.topic(d["topic"])]

    # 원격 리스너
    def call(self, path, body=None, origin=ORIGIN, headers=None, raw=None, method="POST"):
        h = {"Origin": origin} if origin else {}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        st, hd, out = http(self.port, method, path, None, h, raw=data)
        try:
            return st, json.loads(out or b"{}")
        except ValueError:
            return st, {"_raw": out}

    def signed_call(self, cred, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        h = {"Origin": ORIGIN, "Authorization": signed(cred, method, path, data, clock=self.clock)}
        st, hd, out = http(self.port, method, path, None, h, raw=data if method == "POST" else None)
        return st, json.loads(out or b"{}")


def as_cred(inner, host):
    return {"device": inner["device"], "keys": inner["keys"], "host": host}


class SetupTests(LoginBase):
    def test_set_stores_no_password(self):
        v = self.set_login()
        self.assertTrue(v["set"] and v["enabled"])
        raw = (self.tmp / ".futsal-studio" / "remote.json").read_text(encoding="utf-8")
        lg = json.loads(raw)["login"]
        for k in ("verifier", "salt", "iters", "topic", "bkey", "idc", "fake", "control", "gen", "canon"):
            self.assertIn(k, lg)
        self.assertEqual((lg["id"], lg["idShow"], lg["gen"], lg["canon"]), (CID, ID, 1, 1))
        for form in (PW, PW_KEYS, PW_KEYS.upper()):
            self.assertNotIn(form, raw)
        self.assertTrue(rlogin.valid_login(lg))

    def test_bad_login_block_dropped_devices_kept_file_not_renamed(self):
        self.set_login()
        p = self.tmp / ".futsal-studio" / "remote.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["login"]["verifier"] = "AAAA"
        p.write_text(json.dumps(d), encoding="utf-8")
        svc2 = remote.Service(home=self.tmp / ".futsal-studio", clock=self.clock, ntfy=self.ntfy.url)
        fb = FakeBridge()
        svc2.init(fb.bridge())
        self.addCleanup(close_service, svc2)
        self.assertTrue(svc2.store.login_bad)
        self.assertIsNone(svc2.store.login())
        self.assertEqual(len(svc2.store.data["devices"]), 1)
        self.assertFalse(p.with_name("remote.json.bad").exists())
        self.assertIn(remote.LOGIN_BAD_BLOCK_MSG, fb.lines)
        self.assertTrue(svc2.login_view()["bad"])
        self.assertNotIn("login", svc2.r_ping().get("features", []))

    def test_collision_refused_with_korean_text(self):
        other = rlogin.derive_login(CID)
        rec = {"v": 1, "pc": "f" * 16, "seq": 1, "ts": int(self.clock()) - 3600, "state": "on", "reason": None, "url": None}
        self.ntfy.seed(other["topic"], rlogin.seal_lookup(other["bkey"], other["topic"], rec, remote.gcm_seal))
        with self.assertRaises(ValueError) as cm:
            self.svc.login_set(ID, PW, PW, ack=True)
        self.assertEqual(str(cm.exception), "이 아이디는 다른 PC가 쓰고 있어요 · 다른 아이디를 골라 주세요")
        self.assertIsNone(self.svc.store.login())
        # 12시간 넘은 기록은 겹침 아님
        rec2 = dict(rec, ts=int(self.clock()) - 13 * 3600)
        self.ntfy.posts.clear()
        self.ntfy.seed(other["topic"], rlogin.seal_lookup(other["bkey"], other["topic"], rec2, remote.gcm_seal))
        self.assertTrue(self.svc.login_set(ID, PW, PW, ack=True)["set"])

    def test_collision_unreachable_needs_force(self):
        self.ntfy.down = True
        with self.assertRaises(remote.LoginAsk) as cm:
            self.svc.login_set(ID, PW, PW, ack=True)
        self.assertEqual((cm.exception.code, str(cm.exception)), ("unchecked", "겹치는지 확인하지 못했어요 (인터넷 확인) · 그래도 쓸까요?"))
        self.assertTrue(self.svc.login_set(ID, PW, PW, ack=True, force=True)["set"])

    def test_need_ack_without_code_phone(self):
        self.svc.revoke(everyone=True)
        with self.assertRaises(remote.LoginAsk) as cm:
            self.svc.login_set(ID, PW, PW, force=True)
        self.assertEqual(cm.exception.code, "need_ack")
        self.assertIn("연결된 휴대폰이 없어서 새 기기 알림을 받을 곳이 없어요", str(cm.exception))
        self.assertTrue(self.svc.login_set(ID, PW, PW, force=True, ack=True)["set"])

    def test_runtime_collision_banner(self):
        self.set_login()
        other = rlogin.derive_login(CID)
        rec = {"v": 1, "pc": "e" * 16, "seq": 9, "ts": int(self.clock()), "state": "off", "reason": "user", "url": None}
        self.ntfy.seed(other["topic"], rlogin.seal_lookup(other["bkey"], other["topic"], rec, remote.gcm_seal))
        self.svc._collision_at = 0
        self.svc.tick()
        self.assertTrue(self.wait(lambda: self.svc.login_view().get("collision")))
        self.assertTrue(self.wait(lambda: any("다른 PC가 같은 아이디를 쓰고 있어요" in x for x in self.lines)))   # 기록 줄은 표시 뒤에 (바쁜 PC)

    def test_check_route_problem_codes(self):
        self.assertEqual(self.svc.login_check(ID, "qwer1234")["problems"], ["common"])
        r = self.svc.login_check(ID, "김코치77abc")
        self.assertIn("id", r["problems"])
        self.assertEqual(self.svc.login_check(ID, PW), {"problems": [], "canonLen": len(PW_KEYS), "idProblem": None})
        self.assertEqual(self.svc.login_check("futsal", PW)["idProblem"], "reserved")
        with self.assertRaises(ValueError) as cm:
            self.svc.login_set(ID, "qwer1234", "qwer1234", force=True, ack=True)
        self.assertEqual(str(cm.exception), "너무 흔한 비밀번호예요")
        with self.assertRaises(ValueError) as cm:
            self.svc.login_set("futsal", PW, PW, force=True, ack=True)
        self.assertEqual(str(cm.exception), rlogin.ID_MSG["reserved"])
        with self.assertRaises(ValueError) as cm:
            self.svc.login_set(ID, PW, PW + "x", force=True, ack=True)
        self.assertEqual(str(cm.exception), "두 비밀번호가 달라요")

    def test_suggest_route(self):
        for _ in range(10):
            pw = self.svc.login_suggest(ID)["password"]
            self.assertEqual(rlogin.pw_problems(pw, CID), [])


class LookupPublishTests(LoginBase):
    def test_on_changed_url(self):
        self.set_login()
        recs = self.lookup_records()
        self.assertEqual(recs[-1]["state"], "on")
        self.assertEqual(recs[-1]["url"], self.svc.url)
        n = len(recs)
        self.svc._tunnel_event(self.svc._gen, None, "on", url="http://127.0.0.1:9")
        self.assertTrue(self.wait(lambda: len(self.lookup_records()) > n))
        self.assertEqual(self.lookup_records()[-1]["url"], "http://127.0.0.1:9")

    def test_turn_off_user_idle_reason(self):
        self.set_login()
        self.svc.turn_off("user")
        self.assertTrue(self.wait(lambda: self.lookup_records()[-1]["reason"] == "user"))
        self.assertEqual(self.lookup_records()[-1]["state"], "off")
        self.svc.turn_on()
        self.wait(lambda: self.svc.state == "on")
        self.svc.store.data["settings"]["autoOffHours"] = 1
        self.clock.tick(3601)
        self.svc.tick()
        self.assertTrue(self.wait(lambda: self.lookup_records()[-1]["reason"] == "idle"))

    def test_error_off(self):
        self.set_login()
        self.svc._tunnel_event(self.svc._gen, None, "error", error="연결이 끊겼어요")
        self.assertTrue(self.wait(lambda: self.lookup_records()[-1]["reason"] == "error"))

    def test_shutdown_post_now_within_1_5s(self):
        self.set_login()
        self.wait(lambda: self.lookup_records())
        t0 = time.time()
        self.svc.shutdown(timeout=1.5)
        self.assertLess(time.time() - t0, 3.0)
        rec = self.lookup_records()[-1]
        self.assertEqual((rec["state"], rec["reason"]), ("off", "app"))

    def test_heartbeat_3h_on_and_off(self):
        self.set_login()
        self.wait(lambda: self.lookup_records())
        n = len(self.lookup_records())
        self.svc._collision_at = self.clock() + 1e9
        self.clock.tick(remote.LOOKUP_HB + 1)
        self.svc.tick()
        self.assertTrue(self.wait(lambda: len(self.lookup_records()) == n + 1))
        self.assertEqual(self.lookup_records()[-1]["state"], "on")
        self.svc.turn_off("user")
        self.wait(lambda: len(self.lookup_records()) == n + 2)
        self.clock.tick(remote.LOOKUP_HB + 1)
        self.svc.tick()
        self.assertTrue(self.wait(lambda: len(self.lookup_records()) == n + 3))
        self.assertEqual(self.lookup_records()[-1]["state"], "off")
        seqs = [r["seq"] for r in self.lookup_records()]
        self.assertEqual(seqs, sorted(seqs))

    def test_login_off_and_id_change_post_login_off_to_old_topic(self):
        self.set_login()
        self.svc.login_id("kimcoach99", force=True)
        # 보내기는 뒤에서 (pub 줄) — 무거운 기계에서 켜짐만 먼저 보일 수 있어 꺼짐 기록이 닿을 때까지 기다림
        self.wait(lambda: self.lookup_records(CID)[-1]["state"] == "off")
        old = self.lookup_records(CID)
        self.assertEqual((old[-1]["state"], old[-1]["reason"]), ("off", "login_off"))
        new = self.lookup_records("kimcoach99")
        self.assertEqual(new[-1]["state"], "on")
        self.svc.login_off()
        self.assertTrue(self.wait(lambda: self.lookup_records("kimcoach99")[-1]["reason"] == "login_off"))

    def test_not_published_when_unset(self):
        self.svc.turn_off("user")
        self.svc.tick()
        time.sleep(0.3)
        self.assertEqual([p for p in self.ntfy.posts if p[0].startswith("fsl")], [])

    def test_record_has_no_name_or_job(self):
        self.set_login()
        rec = self.lookup_records()[-1]
        self.assertEqual(set(rec), {"v", "pc", "seq", "ts", "state", "reason", "url"})
        for _, m, _, _ in self.ntfy.topic(rlogin.derive_login(CID)["topic"]):
            self.assertNotIn("내 PC", m)

    def test_budget_important(self):
        self.set_login()
        self.svc.pub.day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        self.svc.pub.used = remote.BUDGET_SOFT + 1
        n = len(self.lookup_records())
        self.svc._publish_lookup()
        self.assertTrue(self.wait(lambda: len(self.lookup_records()) == n + 1))


class PingTests(LoginBase):
    def test_ping_has_no_pc_name(self):
        self.svc.store.data["pc"]["name"] = "김철수의 PC"
        st, b = self.call("/r/ping", origin=None, method="GET")
        self.assertEqual(st, 200)
        self.assertEqual(b["pc"], {"id": self.svc.store.data["pc"]["id"]})
        self.assertNotIn("김철수", json.dumps(b, ensure_ascii=False))

    def test_features_login_only_when_enabled(self):
        self.assertNotIn("login", self.svc.r_ping().get("features", []))
        self.set_login()
        self.assertIn("login", self.svc.r_ping()["features"])
        self.svc.login_off()
        self.assertNotIn("login", self.svc.r_ping().get("features", []))


class StartFinishHttpTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.set_login()
        self.c = Client(self.svc)

    def start(self, headers=None, body=None):
        return self.call("/r/login/start", body or self.c.start_body(), headers=headers)

    def test_origin_missing_or_foreign_403(self):
        for origin in (None, "http://evil.example", "https://mulgyeol.kr"):
            st, j = self.call("/r/login/start", self.c.start_body(), origin=origin)
            self.assertEqual(st, 403, origin)
        self.assertEqual(len(self.svc.pending), 0)

    def test_preflight_site_only(self):
        st, hd, _ = http(self.port, "OPTIONS", "/r/login/start", None, {"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
        self.assertEqual(st, 204)
        self.assertEqual(hd.get("Access-Control-Allow-Origin"), ORIGIN)
        st, _, _ = http(self.port, "OPTIONS", "/r/login/start", None, {"Origin": "http://evil.example"})
        self.assertEqual(st, 403)

    def test_415_non_json(self):
        st, _ = self.call("/r/login/start", raw=b"a=b", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 415)

    def test_413_over_4096(self):
        st, _ = self.call("/r/login/start", raw=b"{" + b" " * 4100 + b"}")
        self.assertEqual(st, 413)
        st, _ = self.call("/r/login/finish", raw=b"{" + b" " * 4100 + b"}")
        self.assertEqual(st, 413)

    def test_bucket_429(self):
        codes = [self.call("/r/login/start", {"v": 1}, headers={"Cf-Connecting-Ip": "203.0.113.50"})[0] for _ in range(14)]
        self.assertEqual(codes[:12], [400] * 12)
        self.assertEqual(codes[12], 429)
        self.assertEqual(self.call("/r/login/start", {"v": 1}, headers={"Cf-Connecting-Ip": "203.0.113.51"})[0], 429)   # 같은 /24 는 한 바구니
        self.assertEqual(self.call("/r/login/start", {"v": 1}, headers={"Cf-Connecting-Ip": "203.0.114.51"})[0], 400)   # 다른 대역은 그대로

    def test_ip_lockout_429(self):
        ip = "203.0.113.60"
        self.svc.limiter.locked_until[ip] = self.clock() + 900
        self.assertEqual(self.call("/r/login/start", self.c.start_body(), headers={"Cf-Connecting-Ip": ip})[0], 429)

    def test_login_off_403(self):
        self.svc.login_off()
        st, j = self.start()
        self.assertEqual((st, j["code"]), (403, "login_off"))
        self.assertEqual(j["error"], "이 PC는 지금 아이디·비밀번호 연결을 쓰지 않아요 · PC '실행기'에서 켜 주세요")

    def test_locked_423_scope_until(self):
        ip = "203.0.113.70"
        for _ in range(5):
            st, j = self.start({"Cf-Connecting-Ip": ip})
            self.assertEqual(st, 200)
            st, j = self.call("/r/login/finish", self.c.proof(j, pw="틀린비밀번호1234"), headers={"Cf-Connecting-Ip": ip})
            self.assertEqual((st, j["code"]), (403, "bad"))
        self.assertEqual(j["left"], 0)
        self.assertEqual(j["until"], int(self.clock() + 900 + 0.999))
        st, j = self.start({"Cf-Connecting-Ip": ip})
        self.assertEqual((st, j["code"], j["scope"]), (423, "locked", "net"))
        self.assertGreater(j["until"], self.clock())
        st, j = self.start({"Cf-Connecting-Ip": "198.18.0.1"})   # 다른 대역은 그대로
        self.assertEqual(st, 200)

    def test_suspended_423(self):
        self.svc.store.login()["suspended"] = {"why": "kick", "at": 1}
        st, j = self.start()
        self.assertEqual((st, j["code"], j["why"]), (423, "suspended", "kick"))

    def test_busy_429(self):
        for i in range(rlogin.PENDING_MAX):
            self.svc.pending.put(f"10.{i // 250}.{i % 250}.0/24", {})
        st, j = self.start()
        self.assertEqual((st, j["code"]), (429, "busy"))

    def test_again_410_not_counted(self):
        st, j = self.start()
        body = self.c.proof(j)
        self.clock.tick(61)
        st, j = self.call("/r/login/finish", body)
        self.assertEqual((st, j["code"]), (410, "again"))
        st, j = self.call("/r/login/finish", dict(body, login="A" * 22))
        self.assertEqual((st, j["code"]), (410, "again"))
        self.assertEqual(self.svc.store.login()["guard"]["total"], 0)

    def test_bad_403_left(self):
        st, j = self.start()
        st, j = self.call("/r/login/finish", self.c.proof(j, pw="노을축구공바다4828"))
        self.assertEqual((st, j["code"], j["left"], j["until"]), (403, "bad", 4, None))
        self.assertEqual(j["error"], "비밀번호가 맞지 않아요")
        self.assertEqual(self.svc.store.login()["guard"]["total"], 1)
        saved = json.loads((self.tmp / ".futsal-studio" / "remote.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["login"]["guard"]["total"], 1)   # 셀 때마다 저장

    def test_wrong_idc_same_shape_as_real(self):
        st1, real = self.start()
        other = rlogin.derive_login("someoneelse1")
        st2, fake = self.start(body=self.c.start_body(idc=other["idc"]))
        self.assertEqual((st1, st2), (200, 200))
        self.assertEqual(set(real), set(fake))
        self.assertEqual({k: len(str(v)) for k, v in real.items() if k not in ("time",)},
                         {k: len(str(v)) for k, v in fake.items() if k not in ("time",)})
        st3, again = self.start(body=self.c.start_body(idc=other["idc"]))
        self.assertEqual(again["salt"], fake["salt"])   # 같은 틀린 아이디 → 같은 가짜 salt
        st, j = self.call("/r/login/finish", self.c.proof(fake, idc=other["idc"]))
        self.assertEqual((st, j["code"]), (403, "bad"))
        self.assertEqual(self.svc.store.login()["guard"]["total"], 0)   # 틀린 아이디는 그 대역만

    def test_failed_logins_do_not_move_last_auth(self):
        before = self.svc.last_auth
        self.clock.tick(100)
        st, j = self.start()
        self.call("/r/login/finish", self.c.proof(j, pw="노을축구공바다4828"))
        self.assertEqual(self.svc.last_auth, before)

    def test_success_over_http(self):
        st, j = self.start()
        st, fin = self.call("/r/login/finish", self.c.proof(j, remember=False))
        self.assertEqual(st, 200, fin)
        inner = self.c.open(fin, j["pc"]["id"])
        self.assertEqual((inner["v"], inner["via"], inner["guest"]), (2, "login", True))
        self.assertFalse(fin["remembered"])

    def test_m1_binds_remember(self):
        """가운데에서 '이번만' → '기억하기' 로 바꾸면 M1 이 안 맞음 (틀린 시도로 셈)."""
        st, j = self.start()
        body = self.c.proof(j, remember=False)
        st, r = self.call("/r/login/finish", dict(body, remember=True))
        self.assertEqual((st, r["code"]), (403, "bad"))

    def test_pair_and_fsr2_routes_unchanged(self):
        st, j = self.signed_call(self.code, "GET", "/r/status?since=0")
        self.assertEqual(st, 200)
        st, b = self.call("/r/pair", {"v": 2, "nonce": "A" * 22, "ts": int(self.clock()), "proof": "B" * 43})
        self.assertEqual(st, 403)
        st, j = self.call("/r/status", method="GET")
        self.assertEqual(st, 401)


class RememberTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.set_login()

    def test_creates_via_login_device_in_store_and_beacon_items(self):
        fin, inner = self.login(remember=True)
        self.assertTrue(fin["remembered"])
        dev = self.svc.store.device(inner["device"]["id"])
        self.assertEqual((dev["via"], dev["loginGen"]), ("login", 1))
        saved = json.loads((self.tmp / ".futsal-studio" / "remote.json").read_text(encoding="utf-8"))
        self.assertIn(dev["id"], [x["id"] for x in saved["devices"]])
        body = self.svc.beacon_body()
        outer = json.loads(unb64u(body[5:]))
        self.assertIn(dev["id"], [x["d"] for x in outer["items"]])

    def test_inner_notify_null(self):
        _, inner = self.login(remember=True)
        self.assertIsNone(inner["notify"])
        self.assertEqual(inner["beacon"]["topic"], self.svc.store.data["topics"]["beacon"])
        self.assertEqual(inner["login"], {"show": ID})
        self.assertEqual(inner["pc"]["id"], self.svc.store.data["pc"]["id"])
        st = self.svc.r_status(self.svc.store.device(inner["device"]["id"]), 0)
        tp = topics_of(self.svc, as_cred(inner, ""), st)
        self.assertIsNone(tp["notify"])
        self.assertFalse(tp["login"]["manage"])

    def test_same_install_replaces(self):
        _, a = self.login(install="i" * 22)
        _, b = self.login(install="i" * 22)
        self.assertIsNone(self.svc.store.device(a["device"]["id"]))
        self.assertIsNotNone(self.svc.store.device(b["device"]["id"]))

    def test_full_5_falls_back_to_guest_remembered_false(self):
        for i in range(4):
            pair_device(self.svc, f"폰 {i}")
        self.assertEqual(len(self.svc.store.data["devices"]), 5)
        fin, inner = self.login(remember=True)
        self.assertFalse(fin["remembered"])
        self.assertTrue(inner["guest"])
        self.assertEqual(len(self.svc.store.data["devices"]), 5)


class GuestTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.set_login()

    def guest(self, **kw):
        fin, inner = self.login(remember=False, **kw)
        return inner

    def test_not_in_remote_json_or_beacon(self):
        g = self.guest()
        raw = (self.tmp / ".futsal-studio" / "remote.json").read_text(encoding="utf-8")
        self.assertNotIn(g["device"]["id"], [x["id"] for x in json.loads(raw)["devices"]])   # 기록(events)에 번호만 · 열쇠는 없음
        self.assertNotIn(g["keys"]["auth"], raw)
        self.assertNotIn(g["keys"]["beacon"], raw)
        outer = json.loads(unb64u(self.svc.beacon_body()[5:]))
        self.assertNotIn(g["device"]["id"], [x["d"] for x in outer["items"]])
        self.assertNotIn("beacon", g)
        self.assertGreater(g["exp"], self.clock())

    def test_r_status_no_topics(self):
        g = self.guest()
        st, j = self.signed_call(as_cred(g, self.svc.host()), "GET", "/r/status?since=0")
        self.assertEqual(st, 200)
        self.assertNotIn("topics", j)
        self.assertEqual(j["remote"]["guestExp"], g["exp"])

    def test_auth_verify_accepts(self):
        g = self.guest()
        hdr = signed(as_cred(g, self.svc.host()), "GET", "/r/status", b"", clock=self.clock)
        self.assertEqual(self.svc.auth.verify(hdr, "GET", "/r/status", b"", self.svc.host())["id"], g["device"]["id"])

    def test_swept_idle_30m(self):
        g = self.guest()
        self.clock.tick(remote.GUEST_IDLE + 1)
        self.svc.tick()
        self.assertIsNone(self.svc.store.device(g["device"]["id"]))

    def test_swept_12h(self):
        self.svc.set_settings({"autoOffHours": 0})
        gid = self.guest()["device"]["id"]
        end = self.clock() + remote.GUEST_AGE + 10
        while self.clock() < end and gid in self.svc.store.guests:
            self.clock.tick(1500)
            if gid in self.svc.store.guests:
                self.svc.store.guests[gid]["lastSeen"] = self.clock()   # 계속 쓰고 있어도 12시간이면 끝
            self.svc.tick()
        self.assertNotIn(gid, self.svc.store.guests)
        self.assertGreaterEqual(self.clock(), end - remote.GUEST_AGE + remote.GUEST_AGE - 1500 - 10)
        self.assertEqual(self.svc.state, "on")

    def test_dropped_on_url_change_turn_off_shutdown_login_off_password_change(self):
        pw = PW
        for action in ("url", "off", "login_off", "password", "shutdown"):
            if self.svc.state != "on":
                self.svc.turn_on()
                self.wait(lambda: self.svc.state == "on")
            if not self.svc.store.login()["enabled"]:
                self.svc.login_on(force=True)
            g = self.guest(pw=pw)
            gid = g["device"]["id"]
            if action == "url":
                self.svc._tunnel_event(self.svc._gen, None, "on", url="http://127.0.0.1:7")
            elif action == "off":
                self.svc.turn_off("user")
            elif action == "login_off":
                self.svc.login_off()
            elif action == "password":
                pw = "새비밀번호바다3921"
                self.svc.login_set(None, pw, pw, force=True, ack=True)
            else:
                self.svc.shutdown(timeout=0.5)
            self.assertIsNone(self.svc.store.device(gid), action)

    def test_guest_cap_3_evicts_oldest(self):
        ids = []
        for i in range(4):
            self.clock.tick(1)
            ids.append(self.guest()["device"]["id"])
        self.assertIsNone(self.svc.store.device(ids[0]))
        self.assertEqual(len(self.svc.store.guests), 3)
        self.assertIn("이번만 연결 기기가 많아 가장 오래된 기기를 끊었어요", self.lines)

    def test_logout_route(self):
        g = self.guest()
        st, j = self.signed_call(as_cred(g, self.svc.host()), "POST", "/r/login/logout", {})
        self.assertEqual(st, 200)
        self.assertIsNone(self.svc.store.device(g["device"]["id"]))
        st, j = self.signed_call(self.code, "POST", "/r/login/logout", {})
        self.assertEqual((st, j.get("code")), (403, "not_allowed"))


class AlertTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.set_login()

    def test_login_alert_text_vocab_country_kind(self):
        self.login(remember=False, cc="KR", name="<script>내 이름</script>")
        self.assertTrue(self.wait(lambda: self.notes("아이디로 새 기기가")))
        t, m, h = self.notes("아이디로 새 기기가")[-1]
        self.assertRegex(m, r"^아이디로 새 기기가 연결됐어요 \(이번만\) · Windows · Chrome · 한국 · 오[전후] \d{1,2}:\d{2} · 내가 아니면 \[끊기\]를 눌러 주세요$")
        self.assertNotIn("script", m)
        self.assertEqual(t, self.svc.store.data["topics"]["notify"])
        self.login(remember=True, cc="US")
        self.assertTrue(self.wait(lambda: any("(기억함) · Windows · Chrome · 해외(US)" in x[1] for x in self.notes())))
        self.login(remember=True, cc="XX")
        self.assertTrue(self.wait(lambda: any(x[1].startswith("아이디로 새 기기가 연결됐어요 (기억함) · Windows · Chrome · 오") for x in self.notes())))

    def test_actions_http_control_fsk1(self):
        self.login()
        self.assertTrue(self.wait(lambda: self.notes("아이디로 새 기기가")))
        _, _, h = self.notes("아이디로 새 기기가")[-1]
        act = h["actions"][0]
        lg = self.svc.store.login()
        self.assertEqual((act["action"], act["label"], act["method"], act["clear"]), ("http", "끊기", "POST", True))
        self.assertEqual(act["url"], f"{self.ntfy.url}/{lg['control']}")
        self.assertRegex(act["body"], r"^fsk1\.[A-Za-z0-9_-]{24}$")
        self.assertEqual(rlogin.kill_hash(act["body"][5:]), lg["events"][0]["kh"])

    def test_click_kill_hash(self):
        self.login()
        self.assertTrue(self.wait(lambda: self.notes("아이디로 새 기기가")))
        _, _, h = self.notes("아이디로 새 기기가")[-1]
        lg = self.svc.store.login()
        tok = h["actions"][0]["body"][5:]
        self.assertEqual(h["click"], f"{self.svc.site()}#kill={lg['control']}.{tok}.{lg['events'][0]['at']}")

    def test_sent_with_attention_off(self):
        self.svc.set_settings({"notify": {"done": False, "failed": False, "attention": False}})
        self.login()
        self.assertTrue(self.wait(lambda: self.notes("아이디로 새 기기가")))

    def test_no_url_id_topic_password_in_any_post(self):
        self.login(remember=False)
        c = Client(self.svc)
        for _ in range(5):
            st = self.svc.r_login_start(c.start_body(), "203.0.113.9", UA_WIN, "KR")
            try:
                self.svc.r_login_finish(c.proof(st, pw="틀린비밀번호9182"), "203.0.113.9", UA_WIN, "KR")
            except remote.LoginError:
                pass
        self.assertTrue(self.wait(lambda: self.notes("비밀번호가 5번 틀렸어요")))
        lg = self.svc.store.login()
        import re
        for t, m, h in self.notes():
            text = m + json.dumps(h, ensure_ascii=False)
            self.assertIsNone(re.search(rf":{self.port}(?!\d)", text))   # 터널(여기서는 리스너) 주소
            for secret in (CID, ID, lg["topic"], PW, PW_KEYS, "틀린비밀번호9182", lg["idc"], lg["bkey"]):
                self.assertNotIn(secret, text)
        for t, m, _, _ in self.ntfy.posts:
            for secret in (PW, PW_KEYS, CID, ID):
                self.assertNotIn(secret, m)

    def test_studio_notice_suppressed_after_login(self):
        _, inner = self.login(remember=False)
        self.assertIn(inner["device"]["id"], self.svc.studio.sessions.seen)


class KillTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.set_login()

    def kill_token(self):
        self.assertTrue(self.wait(lambda: self.notes("아이디로 새 기기가")))
        return self.notes("아이디로 새 기기가")[-1][2]["actions"][0]["body"]

    def post_control(self, body):
        lg = self.svc.store.login()
        req = urllib.request.Request(f"{self.ntfy.url}/{lg['control']}", data=body.encode(), method="POST")
        urllib.request.urlopen(req, timeout=5).read()

    def test_control_stream_kill_guest(self):
        _, g = self.login(remember=False)
        body = self.kill_token()
        self.assertTrue(self.wait(lambda: self.ntfy.streams > 0))
        self.post_control(body)
        self.assertTrue(self.wait(lambda: self.svc.store.device(g["device"]["id"]) is None))
        self.assertEqual(self.svc.store.login()["suspended"]["why"], "kick")
        self.assertTrue(self.wait(lambda: self.notes("끊었어요 · 그 기기 연결을 끊고")))
        self.assertIn("아이디로 연결한 기기를 끊었어요 (알림에서)", self.lines)

    def test_control_stream_kill_remembered_rotate_notify_false(self):
        notify = self.svc.store.data["topics"]["notify"]
        beacon = self.svc.store.data["topics"]["beacon"]
        _, r = self.login(remember=True)
        body = self.kill_token()
        self.post_control(body)
        self.assertTrue(self.wait(lambda: self.svc.store.device(r["device"]["id"]) is None))
        self.assertEqual(self.svc.store.data["topics"]["notify"], notify)
        self.assertNotEqual(self.svc.store.data["topics"]["beacon"], beacon)
        self.assertIsNotNone(self.svc.store.device(self.code["device"]["id"]))

    def test_expired_unknown_replayed_ignored(self):
        _, g = self.login(remember=False)
        body = self.kill_token()
        self.post_control("fsk1." + "Z" * 24)          # 모르는 표
        self.post_control("hello")
        time.sleep(0.5)
        self.assertIsNotNone(self.svc.store.device(g["device"]["id"]))
        self.assertIsNone(self.svc.store.login()["suspended"])
        self.svc.store.login()["events"][0]["until"] = self.clock() - 1   # 하루 지남
        self.svc._control_message(json.dumps({"event": "message", "message": body}))
        self.assertIsNone(self.svc.store.login()["suspended"])
        self.svc.store.login()["events"][0]["until"] = self.clock() + 100
        self.svc._control_message(json.dumps({"event": "message", "message": body}))
        self.assertEqual(self.svc.store.login()["suspended"]["why"], "kick")
        self.svc.login_unlock()
        self.svc._control_message(json.dumps({"event": "message", "message": body}))   # 이미 쓴 표
        self.assertIsNone(self.svc.store.login()["suspended"])

    def test_second_alert_kill_still_works(self):
        _, g1 = self.login(remember=False)
        b1 = self.kill_token()
        _, g2 = self.login(remember=False, ip="198.51.100.99")
        self.assertTrue(self.wait(lambda: len(self.notes("아이디로 새 기기가")) >= 2))
        b2 = self.notes("아이디로 새 기기가")[-1][2]["actions"][0]["body"]
        control = self.svc.store.login()["control"]
        self.post_control(b1)
        self.assertTrue(self.wait(lambda: self.svc.store.device(g1["device"]["id"]) is None))
        self.assertEqual(self.svc.store.login()["control"], control)   # [끊기] 주제는 그대로
        self.post_control(b2)
        self.assertTrue(self.wait(lambda: self.svc.store.device(g2["device"]["id"]) is None))

    def test_runs_with_tunnel_off(self):
        _, r = self.login(remember=True)
        body = self.kill_token()
        self.svc.turn_off("user")
        self.post_control(body)
        self.assertTrue(self.wait(lambda: self.svc.store.device(r["device"]["id"]) is None))

    def test_signed_kick_code_only_login_device_403(self):
        _, r = self.login(remember=True)
        rc = as_cred(r, self.svc.host())
        for m, p, b in (("GET", "/r/login/events", None), ("POST", "/r/login/kick", {"ref": "x" * 11}), ("POST", "/r/login/unlock", {}),
                        ("POST", "/r/notify-test", {})):
            st, j = self.signed_call(rc, m, p, b)
            self.assertEqual((st, j.get("code")), (403, "not_allowed"), p)
        st, j = self.signed_call(self.code, "GET", "/r/login/events")
        self.assertEqual(st, 200)
        ref = j["events"][0]["ref"]
        self.assertEqual(j["login"]["show"], ID)
        st, j = self.signed_call(self.code, "POST", "/r/login/kick", {"ref": ref})
        self.assertEqual(st, 200)
        self.assertIsNone(self.svc.store.device(r["device"]["id"]))
        self.assertIn("아이디로 연결한 기기를 끊었어요 (휴대폰에서)", self.lines)

    def test_unlock_code_only(self):
        self.svc.store.login()["suspended"] = {"why": "fails", "at": 1}
        st, j = self.signed_call(self.code, "POST", "/r/login/unlock", {})
        self.assertEqual(st, 200)
        self.assertIsNone(self.svc.store.login()["suspended"])

    def test_kick_suspends_even_if_device_gone(self):
        _, g = self.login(remember=False)
        ref = self.svc.store.login()["events"][0]["ref"]
        self.svc._drop_guests({g["device"]["id"]})
        self.svc.login_kill(ref=ref)
        self.assertEqual(self.svc.store.login()["suspended"]["why"], "kick")

    def test_resume_from_pc_and_code_phone(self):
        self.svc.store.login()["suspended"] = {"why": "kick", "at": 1}
        self.svc.login_unlock()
        self.assertIsNone(self.svc.store.login()["suspended"])
        fin, _ = self.login()
        self.assertTrue(fin["remembered"])


class RevocationTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.set_login()

    def test_password_change_default_revokes_login_devices_keeps_code(self):
        _, r = self.login(remember=True)
        self.svc.login_set(None, "새비밀번호바다3921", "새비밀번호바다3921", force=True, ack=True)
        self.assertIsNone(self.svc.store.device(r["device"]["id"]))
        self.assertIsNotNone(self.svc.store.device(self.code["device"]["id"]))
        with self.assertRaises(remote.LoginError):
            self.login()
        self.login(pw="새비밀번호바다3921")

    def test_without_revoke_keeps(self):
        _, r = self.login(remember=True)
        self.svc.login_set(None, "새비밀번호바다3921", "새비밀번호바다3921", revoke=False, force=True, ack=True)
        self.assertIsNotNone(self.svc.store.device(r["device"]["id"]))

    def test_clears_guard_pending_events_suspended_rotates_control_gen_plus_1(self):
        self.login()
        lg = self.svc.store.login()
        lg["guard"]["total"] = 7
        lg["suspended"] = {"why": "kick", "at": 1}
        old = dict(lg)
        self.svc.pending.put("1.1.1.0/24", {})
        self.svc.login_set(None, "새비밀번호바다3921", "새비밀번호바다3921", force=True, ack=True)
        new = self.svc.store.login()
        self.assertEqual(new["gen"], old["gen"] + 1)
        self.assertNotEqual(new["control"], old["control"])
        for k in ("salt", "verifier", "fake"):
            self.assertNotEqual(new[k], old[k], k)
        self.assertEqual((new["events"], new["suspended"], new["guard"]["total"]), ([], None, 0))
        self.assertEqual(len(self.svc.pending), 0)
        self.assertEqual(new["topic"], old["topic"])

    def test_id_change_keeps_devices(self):
        _, r = self.login(remember=True)
        g = self.login(remember=False)[1]
        self.svc.login_id("kimcoach99", force=True)
        self.assertIsNotNone(self.svc.store.device(r["device"]["id"]))
        self.assertIsNotNone(self.svc.store.device(g["device"]["id"]))
        self.login(cid="kimcoach99")
        with self.assertRaises(remote.LoginError) as cm:   # 옛 아이디: 가짜 대답 → 틀림
            self.login(cid=CID)
        self.assertEqual(cm.exception.code, "bad")

    def test_login_off_default_keeps(self):
        _, r = self.login(remember=True)
        self.svc.login_off()
        self.assertIsNotNone(self.svc.store.device(r["device"]["id"]))
        self.svc.login_on(force=True)
        self.svc.login_off(revoke=True)
        self.assertIsNone(self.svc.store.device(r["device"]["id"]))


def beacon_slot(body, dev_id, beacon_b64):
    """비콘 글(fsb1.) → 그 기기 몫을 그 기기 비콘 열쇠로 풀어 낸 속 (휴대폰 proto.openBeacon 과 같음)."""
    outer = json.loads(unb64u(body[5:]))
    it = next((x for x in outer["items"] if x["d"] == dev_id), None)
    if it is None:
        return {}
    return json.loads(remote.gcm_open(unb64u(beacon_b64), it["c"], f"fsb1|{outer['pc']}|{dev_id}|{outer['seq']}"))


class NotifyTopicTests(LoginBase):
    """검토 고침 (D-255·D-256): 아이디로 연결한 기기는 알림 주제(= [끊기] 표가 든 보안 알림)를 어느 길로도 받지 못하고, 그 기기를 끊어도
    소유자의 알림 주제는 그대로."""

    def setUp(self):
        super().setUp()
        self.set_login()

    def test_moved_beacon_has_no_notify_for_login_device(self):
        _, r = self.login(remember=True)
        new = dict(self.svc.store.data["topics"])
        body = self.svc.beacon_body(moved=new)
        lslot = beacon_slot(body, r["device"]["id"], r["keys"]["beacon"])
        self.assertEqual(lslot["moved"], {"beacon": new["beacon"]})
        cslot = beacon_slot(body, self.code["device"]["id"], self.code["keys"]["beacon"])
        self.assertEqual(cslot["moved"], new)   # QR 기기는 그대로 (새 알림 주제로 다시 구독 안내)
        self.assertNotIn(new["notify"], json.dumps(lslot))

    def test_revoke_other_phone_moved_beacon_leaks_nothing(self):
        """다른 휴대폰을 끊을 때 옛 비콘 주제로 가는 '옮겼어요' 비콘 → 아이디 기기 몫에는 새 알림 주제가 없음 (ntfy 를 듣는 쪽 그대로)."""
        _, r = self.login(remember=True)
        other = pair_device(self.svc, "Galaxy · Chrome")
        old_beacon = self.svc.store.data["topics"]["beacon"]
        self.svc.revoke([other["device"]["id"]])
        new = self.svc.store.data["topics"]
        hit = self.ntfy.wait(lambda p: p[0] == old_beacon and p[1].startswith("fsb1.")
                             and "moved" in beacon_slot(p[1], r["device"]["id"], r["keys"]["beacon"]))
        self.assertTrue(hit)
        lslot = beacon_slot(hit[-1][1], r["device"]["id"], r["keys"]["beacon"])
        self.assertEqual(lslot["moved"], {"beacon": new["beacon"]})
        cslot = beacon_slot(hit[-1][1], self.code["device"]["id"], self.code["keys"]["beacon"])
        self.assertEqual(cslot["moved"]["notify"], new["notify"])

    def test_login_device_forget_keeps_notify_topic(self):
        _, r = self.login(remember=True)
        notify, beacon = self.svc.store.data["topics"]["notify"], self.svc.store.data["topics"]["beacon"]
        st, j = self.signed_call(as_cred(r, self.svc.host()), "POST", "/r/forget", {})
        self.assertEqual(st, 200)
        self.assertIsNone(self.svc.store.device(r["device"]["id"]))
        self.assertEqual(self.svc.store.data["topics"]["notify"], notify)
        self.assertNotEqual(self.svc.store.data["topics"]["beacon"], beacon)   # 비콘 주제는 바꿈 (끊은 기기가 못 읽게)
        time.sleep(0.3)
        self.assertEqual([p for p in self.ntfy.posts if p[0] == notify and "알림 주제가 바뀌었어요" in p[1]], [])

    def test_pc_revoke_login_device_keeps_notify_code_device_rotates(self):
        _, r = self.login(remember=True)
        notify = self.svc.store.data["topics"]["notify"]
        self.svc.revoke([r["device"]["id"]])      # PC '연결된 기기'의 [끊기] (app /api/remote/revoke 와 같은 부름)
        self.assertEqual(self.svc.store.data["topics"]["notify"], notify)
        self.svc.revoke([self.code["device"]["id"]])   # QR 기기를 끊으면 예전처럼 알림 주제도 바꿈
        self.assertNotEqual(self.svc.store.data["topics"]["notify"], notify)
        self.assertTrue(self.ntfy.wait(lambda p: p[0] == notify and "알림 주제가 바뀌었어요" in p[1]))

    def test_alerts_still_reach_owner_topic_after_login_device_forget(self):
        _, r = self.login(remember=True)
        notify = self.svc.store.data["topics"]["notify"]
        self.signed_call(as_cred(r, self.svc.host()), "POST", "/r/forget", {})
        self.login(remember=False, ip="198.51.100.20")
        self.assertTrue(self.ntfy.wait(lambda p: p[0] == notify and "아이디로 새 기기가" in p[1]))


class StartFloodTests(LoginBase):
    """검토 고침 (L13): /r/login/start 의 요청 수는 인터넷 대역(/24·/48)으로 세고, PC 하나에 모두 합친 수도 거듭제곱 전에 막음."""

    def setUp(self):
        super().setUp()
        self.set_login()

    def test_ipv6_rotation_in_one_64_shares_bucket(self):
        codes = [self.call("/r/login/start", {"v": 1}, headers={"Cf-Connecting-Ip": f"2001:db8:1:2::{i + 1:x}"})[0] for i in range(14)]
        self.assertEqual(codes[:12], [400] * 12)
        self.assertEqual(codes[12:], [429, 429])
        self.assertEqual(self.call("/r/login/start", {"v": 1}, headers={"Cf-Connecting-Ip": "2001:db8:99::1"})[0], 400)   # 다른 /48

    def test_global_start_budget_before_modexp(self):
        with mock.patch.object(rlogin, "server_start", wraps=rlogin.server_start) as ss:
            codes = []
            for i in range(remote.BUCKETS["login_start"][0] + 2):
                st, j = self.call("/r/login/start", Client(self.svc).start_body(), headers={"Cf-Connecting-Ip": f"10.{i // 200}.{i % 200}.1"})
                codes.append((st, j.get("code")))
        cap = remote.BUCKETS["login_start"][0]
        self.assertEqual([c for c, _ in codes[:cap]], [200] * cap)
        self.assertEqual(codes[cap:], [(429, "busy"), (429, "busy")])
        self.assertEqual(ss.call_count, cap)   # 막힌 요청은 거듭제곱을 하지 않음

    def test_lockout_by_net_and_fails_counted_per_net(self):
        net = remote.net_of("203.0.113.61")
        self.svc.limiter.locked_until[net] = self.clock() + 900
        self.assertEqual(self.call("/r/login/start", {"v": 1}, headers={"Cf-Connecting-Ip": "203.0.113.99"})[0], 429)
        self.svc.limiter.locked_until.pop(net)
        c = Client(self.svc)
        st, j = self.call("/r/login/start", c.start_body(), headers={"Cf-Connecting-Ip": "2001:db8:5::1"})
        self.call("/r/login/finish", c.proof(j, pw="틀린비밀번호1234"), headers={"Cf-Connecting-Ip": "2001:db8:5::1"})
        self.assertEqual(len(self.svc.limiter.fails.get(remote.net_of("2001:db8:5::1")) or []), 1)
        self.assertEqual(len(self.svc.limiter.fails.get("2001:db8:5::1") or []), 1)


class ControlFloodTests(LoginBase):
    """검토 고침 (D-255): [끊기] 주제에 가짜 'fsk1.…'를 쏟아부어도 진짜 [끊기]는 밀려나지 않음."""

    def setUp(self):
        super().setUp()
        self.set_login()

    def token(self):
        self.assertTrue(self.wait(lambda: self.notes("아이디로 새 기기가")))
        return self.notes("아이디로 새 기기가")[-1][2]["actions"][0]["body"]

    def test_junk_then_genuine_direct(self):
        _, g = self.login(remember=False)
        body = self.token()
        for i in range(200):
            self.svc._control_message(json.dumps({"event": "message", "message": "fsk1." + f"{i:024d}"}))
        self.assertIsNone(self.svc.store.login()["suspended"])
        self.svc._control_message(json.dumps({"event": "message", "message": body}))
        self.assertIsNone(self.svc.store.device(g["device"]["id"]))
        self.assertEqual(self.svc.store.login()["suspended"]["why"], "kick")

    def test_cached_junk_before_genuine_on_stream(self):
        """듣는 스레드가 꺼져 있는 사이 쌓인 가짜 30개 + 진짜 → 다시 들을 때 (ntfy 가 옛것부터) 진짜를 처리."""
        _, g = self.login(remember=False)
        body = self.token()
        self.svc._ctl_stop.set()
        self.assertTrue(self.wait(lambda: not (self.svc._ctl and self.svc._ctl.is_alive()), 10))
        control = self.svc.store.login()["control"]
        for i in range(40):
            self.ntfy.seed(control, "fsk1." + "A" * 24)
        self.ntfy.seed(control, body)
        self.svc._ctl_stop.clear()
        self.svc._control_ensure()
        self.assertTrue(self.wait(lambda: self.svc.store.device(g["device"]["id"]) is None, 10))
        self.assertEqual(self.svc.store.login()["suspended"]["why"], "kick")


class ReviewFixTests(LoginBase):
    """검토 고침: 앱 끄기 기록 · 50번째 틀림 · 이번만 기기의 원격 끄기."""

    def setUp(self):
        super().setUp()
        self.set_login()

    def test_shutdown_after_user_or_idle_off_keeps_reason(self):
        for why in ("user", "idle"):
            if self.svc.state != "on":
                self.svc.turn_on()
                self.assertTrue(self.wait(lambda: self.svc.state == "on"))
            self.svc.turn_off(why)
            self.assertTrue(self.wait(lambda: self.lookup_records()[-1]["reason"] == why))
            n = len(self.lookup_records())
            self.svc._ctl_stop.clear()
            self.svc.shutdown(timeout=1.0)
            time.sleep(0.3)
            recs = self.lookup_records()
            self.assertEqual(len(recs), n, why)                 # 이미 꺼 둔 PC: '앱을 껐어요' 기록을 올리지 않음
            self.assertEqual(recs[-1]["reason"], why)
            svc2 = remote.Service(home=self.tmp / ".futsal-studio", clock=self.clock, ntfy=self.ntfy.url)   # 앱을 다시 켬
            svc2.init(FakeBridge().bridge())
            self.addCleanup(close_service, svc2)
            svc2._collision_at = self.clock() + 1e9
            svc2.tick()
            self.assertTrue(self.wait(lambda: len(self.lookup_records()) == n + 1))
            self.assertEqual((self.lookup_records()[-1]["state"], self.lookup_records()[-1]["reason"]), ("off", why))
            self.svc = svc2
            self.svc.turn_on()
            self.assertTrue(self.wait(lambda: self.svc.state == "on"))
            self.port = self.svc.port()

    def test_shutdown_while_on_still_posts_app(self):
        self.wait(lambda: self.lookup_records())
        self.svc.shutdown(timeout=1.5)
        rec = self.lookup_records()[-1]
        self.assertEqual((rec["state"], rec["reason"]), ("off", "app"))

    def test_fiftieth_failure_answers_suspended(self):
        self.svc.store.login()["guard"]["total"] = rlogin.TOTAL_STOP - 1
        c = Client(self.svc)
        ip = "198.51.100.77"
        st, j = self.call("/r/login/start", c.start_body(), headers={"Cf-Connecting-Ip": ip})
        st, j = self.call("/r/login/finish", c.proof(j, pw="틀린비밀번호1234"), headers={"Cf-Connecting-Ip": ip})
        self.assertEqual((st, j["code"], j["why"]), (423, "suspended", "fails"))
        self.assertNotIn("left", j)
        self.assertEqual(self.svc.store.login()["suspended"]["why"], "fails")
        self.assertEqual(len(self.svc.limiter.fails.get(ip) or []), 1)   # 그래도 틀린 시도로 셈
        self.assertTrue(self.wait(lambda: self.notes("비밀번호가 너무 많이 틀려서")))
        st, j = self.call("/r/login/start", c.start_body(), headers={"Cf-Connecting-Ip": ip})
        self.assertEqual((st, j["code"]), (423, "suspended"))

    def test_guest_cannot_turn_remote_off_login_device_can(self):
        _, g = self.login(remember=False)
        st, j = self.signed_call(as_cred(g, self.svc.host()), "POST", "/r/remote-off", {})
        self.assertEqual((st, j.get("code")), (403, "not_allowed"))
        self.assertIn("이 기기에서 나가기", j["error"])
        time.sleep(0.5)
        self.assertEqual(self.svc.state, "on")
        _, r = self.login(remember=True, ip="198.51.100.8")
        st, j = self.signed_call(as_cred(r, self.svc.host()), "POST", "/r/remote-off", {})
        self.assertEqual(st, 200)
        self.assertTrue(self.wait(lambda: self.svc.state == "off"))


class HangulPasswordTests(LoginBase):
    """검토 고침: '한글 5글자 이상이면 숫자 없어도 돼요' — 받침 없는 다섯 글자(10글쇠)도 저장됨."""

    def test_five_syllables_save(self):
        for pw in ("소나기바다", "바다나라소", "나는코치다", "모래사자고", "가자우리팀"):
            self.assertEqual(self.svc.login_check(ID, pw)["problems"], [], pw)
            self.svc.login_set(ID if not self.svc.store.login() else None, pw, pw, force=True, ack=True)
            fin, _ = self.login(pw=rlogin.canon_pw(pw))   # 영문 자판으로 친 같은 글쇠로도
            self.assertTrue(fin["remembered"])
        with self.assertRaises(ValueError) as cm:
            self.svc.login_set(None, "소나기비", "소나기비", force=True, ack=True)   # 네 글자는 여전히 짧음
        self.assertEqual(str(cm.exception), rlogin.PW_MSG["letters"])


NODE = shutil.which("node")
UI_RUNNER = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const pick = re => { const m = src.match(re); if (!m) throw new Error("missing " + re); return m[0]; };
const fn = name => { const i = src.indexOf("function " + name + "("); let d = 0, j = src.indexOf("{", i);
  for (let k = j; k < src.length; k++) { if (src[k] === "{") d++; else if (src[k] === "}") { d--; if (!d) return src.slice(i, k + 1); } } };
eval([pick(/const esc = .*\n/), pick(/const rmClock = .*\n/), pick(/const rlDay = .*\n/), pick(/const rlUntil = [\s\S]*?\n};\n/),
  pick(/const rlKeys = .*\n/), pick(/const rlIsOwner = .*\n/), fn("rlSection"), fn("rlOffNote")].join("\n") + "\nglobalThis.T = { rlSection, rlOffNote, rlUntil, rlKeys };");
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
console.log(JSON.stringify(cases.map(c => c.op === "until" ? T.rlUntil(c.t) : c.op === "off" ? T.rlOffNote(c.d) : T.rlSection(c.d))));
"""


@unittest.skipUnless(NODE, "node 가 없어 건너뜀")
class PcUiTextTests(unittest.TestCase):
    """PC '휴대폰으로 보기'의 아이디·비밀번호 칸 문장 (ui.html 의 함수를 node 로 그대로)."""

    def run_ui(self, cases):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "ui.js"
            r.write_text(UI_RUNNER, encoding="utf-8")
            out = subprocess.run([NODE, str(r), str(Path(__file__).resolve().parents[1] / "ui.html")], input=json.dumps(cases),
                                 capture_output=True, text=True, timeout=60, env=dict(os.environ, TZ="Asia/Seoul"))
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        return json.loads(out.stdout)

    def view(self, **kw):
        L = {"set": True, "enabled": True, "idShow": "김코치77", "idKeys": "rlazhcl77", "setAt": int(time.time()), "codeDevices": 1,
             "autoOffHours": 0, "events": [], "failsToday": 0, "lock": None, "suspended": None}
        L.update(kw)
        return {"site": "https://futsal.mulgyeol.kr", "devices": [{}], "maxDevices": 5, "login": L}

    def test_id_only_texts(self):
        """아이디만 (D-290): 켜짐 줄 · 밖에서 넣을 것 · 버튼 ('비밀번호 정하기' · 아이디 바꾸기 없음) · 소유자 비밀번호 PC 에만 '비밀번호 없이 쓰기'."""
        own = dict(idShow="wavely1213", idKeys="wavely1213", idHint="wavely1213")
        out = self.run_ui([{"d": self.view(idOnly=True, **own)}, {"d": self.view(**own)}, {"d": self.view(idHint="wavely1213")},
                           {"op": "off", "d": self.view(idOnly=True, **own)}])
        self.assertIn("비밀번호 없이 아이디만으로 들어와요", out[0])
        self.assertIn("아이디를 아는 사람은 누구나 들어올 수 있어요", out[0])
        self.assertIn('data-rl="pw">비밀번호 정하기', out[0])
        self.assertNotIn('data-rl="id"', out[0])
        self.assertNotIn("미리 정해 둔 비밀번호", out[0])
        self.assertIn('data-rl="idonly"', out[1])
        self.assertNotIn('data-rl="idonly"', out[2])   # 김코치77 → 비밀번호 없이는 못 고름
        self.assertIn("아이디만으로 연결이 준비돼 있어요", out[3])

    def test_keys_parenthetical_only_for_hangul_ids(self):
        out = self.run_ui([{"d": self.view()}, {"d": self.view(idShow="wavely1213", idKeys="wavely1213")},
                           {"d": self.view(idShow="WAVELY1213", idKeys="wavely1213")}, {"d": self.view(idShow="Kim.Coach77", idKeys="kimcoach77")}])
        self.assertIn("(영문 자판으로는 rlazhcl77)", out[0])
        self.assertNotIn("영문 자판으로는", out[1])
        self.assertNotIn("영문 자판으로는", out[2])
        self.assertIn("(영문 자판으로는 kimcoach77)", out[3])

    def test_no_code_phone_warns_instead_of_promising_alerts(self):
        promise, warn = "새 기기가 연결되면 휴대폰 알림이 와요", remote.LOGIN_ACK_MSG
        out = self.run_ui([{"d": self.view(codeDevices=0, preset=True)}, {"d": self.view(codeDevices=2)},
                           {"op": "off", "d": self.view(codeDevices=0, preset=True)}, {"op": "off", "d": self.view(codeDevices=1)}])
        self.assertNotIn(promise, out[0])
        self.assertIn(warn, out[0])
        self.assertIn('data-rm="pair"', out[0])   # [휴대폰 연결하기]
        self.assertIn(promise, out[1])
        self.assertNotIn(warn, out[1])
        self.assertIn(warn, out[2])
        self.assertNotIn(warn, out[3])

    def test_lock_until_names_the_day(self):
        # 화면(node)은 TZ=Asia/Seoul 로 돌므로 기대값도 같은 시간대로 계산 (시험 PC 가 UTC 면 날짜가 하루 어긋남)
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        kst = ZoneInfo("Asia/Seoul")
        now_k = datetime.now(kst)
        now = now_k.timestamp()
        tomorrow = (now_k + timedelta(days=1)).replace(hour=15, minute=40, second=0, microsecond=0).timestamp()
        later_k = (now_k + timedelta(days=3)).replace(hour=9, minute=5, second=0, microsecond=0)
        later = later_k.timestamp()
        out = self.run_ui([{"op": "until", "t": int(now) + 60}, {"op": "until", "t": int(tomorrow)}, {"op": "until", "t": int(later)},
                           {"d": self.view(lock={"scope": "net", "until": int(tomorrow)})}])
        self.assertFalse(out[0].startswith("내일") or "월" in out[0])
        self.assertTrue(out[1].startswith("내일 "), out[1])
        self.assertTrue(out[2].startswith(f"{later_k.month}월 {later_k.day}일 "), out[2])
        self.assertIn("내일 ", out[3])


class SecretsTests(LoginBase):
    def test_redact_new_patterns(self):
        cases = [('{"password": "노을축구공바다4827"}', "노을축구공바다4827"), ("{'password2': 'qq77'}", "qq77"), ('{"verifier": "xyz9"}', "xyz9"),
                 ('{"fake": "ff12"}', "ff12"), ('{"bkey": "kk34"}', "kk34"), ('{"idc": "ii56"}', "ii56"),
                 ("fsk1.ABCDEFGHIJKLMNOPQRSTUVWX", "ABCDEFGHIJ"), ("fsl1.abc.def", "abc.def"), ("fsl2.zzz", "zzz"),
                 ("https://futsal.mulgyeol.kr#kill=fsc1.tok.1", "fsc1.tok"), ("x?seen=abc123", "abc123"),
                 ("fsl" + "a" * 24, "a" * 24), ("fsc" + "b" * 24, "b" * 24)]
        for text, secret in cases:
            self.assertNotIn(secret, remote.redact(text), text)

    def test_pc_status_no_secrets(self):
        self.set_login()
        lg = self.svc.store.login()
        text = json.dumps(self.svc.pc_status(), ensure_ascii=False)
        for k in ("verifier", "salt", "topic", "bkey", "idc", "fake", "control"):
            self.assertNotIn(str(lg[k]), text, k)
        self.assertIn(ID, text)

    def test_logs_never_contain_password_raw_canonical_hangul(self):
        self.set_login()
        self.login(remember=False)
        try:
            self.login(pw="틀린비밀번호9182")
        except remote.LoginError:
            pass
        self.svc.login_set(None, "새비밀번호바다3921", "새비밀번호바다3921", force=True, ack=True)
        text = "\n".join(self.lines)
        for form in (PW, PW_KEYS, "새비밀번호바다3921", rlogin.canon_pw("새비밀번호바다3921"), "틀린비밀번호9182", CID, ID):
            self.assertNotIn(form, text)


class IdOnlyLoginTests(LoginBase):
    """아이디만 (D-290 · 소유자 결정 '접근도 wavely1213 하나 입력하면 통과되게'): 소유자 아이디에서만 · 비밀 = 맞춘 아이디 ·
    'PC 찾기' 기록에 표시 · 비밀번호 ⇄ 아이디만 바꾸기 · 다른 아이디로는 못 바꿈."""
    OWNER = "wavely1213"

    def set_id_only(self, id_="WAVELY1213", **kw):
        return self.svc.login_set(id_, None, None, **dict({"force": True, "ack": True, "id_only": True}, **kw))

    def test_set_and_login_with_id_only(self):
        v = self.set_id_only()
        self.assertTrue(v["idOnly"] and v["set"] and v["enabled"])
        lg = self.svc.store.login()
        self.assertEqual((lg["id"], lg["mode"], lg["iters"]), (self.OWNER, "idOnly", rlogin.ID_ITERS))
        for typed in ("wavely1213", "ㅈㅁㅍ디ㅛ1213", "WAVELY1213"):   # 페이지: 친 아이디 → 맞춘 꼴이 비밀
            fin, inner = self.login(pw=rlogin.id_secret(typed), cid=self.OWNER, remember=False)
            self.assertTrue(inner["guest"])
        c = Client(self.svc, self.OWNER, "다른비밀번호1234")
        st = self.svc.r_login_start(c.start_body(), "198.51.100.7", UA_WIN, "KR")
        with self.assertRaises(remote.LoginError):
            self.svc.r_login_finish(c.proof(st), "198.51.100.7", UA_WIN, "KR")
        self.assertTrue(any("아이디만으로 연결을 켰어요" in x for x in self.lines))

    def test_lookup_record_carries_flag_only_in_id_only(self):
        self.set_id_only()
        rec = self.lookup_records(self.OWNER)[-1]
        self.assertIs(rec.get("idOnly"), True)
        self.svc.login_set(None, PW, PW, force=True, ack=True)   # 비밀번호로 되돌림
        self.assertTrue(self.wait(lambda: "idOnly" not in self.lookup_records(self.OWNER)[-1]))
        self.assertFalse(self.svc.login_view()["idOnly"])
        self.assertEqual(self.svc.store.login()["mode"], "password")

    def test_other_ids_cannot_be_id_only(self):
        for id_ in (ID, "kimcoach99", "wavely12134"):
            with self.assertRaises(ValueError):
                self.set_id_only(id_)
        self.assertIsNone(self.svc.store.login())
        self.set_login()   # 김코치77 · 비밀번호
        with self.assertRaises(ValueError):
            self.svc.login_set(None, None, None, force=True, ack=True, id_only=True)
        self.assertEqual(self.svc.store.login()["mode"], "password")

    def test_switch_password_to_id_only_and_back(self):
        self.svc.login_set(self.OWNER, PW, PW, force=True, ack=True)
        fin, inner = self.login(pw=PW, cid=self.OWNER)
        dev = inner["device"]["id"] if isinstance(inner.get("device"), dict) else None
        self.svc.login_set(None, None, None, force=True, ack=True, id_only=True, revoke=False)   # PC '비밀번호 없이 쓰기'
        self.assertEqual(self.svc.store.login()["mode"], "idOnly")
        self.login(pw=self.OWNER, cid=self.OWNER, remember=False)
        c = Client(self.svc, self.OWNER, PW)   # 옛 비밀번호는 이제 틀림
        st = self.svc.r_login_start(c.start_body(), "198.51.100.8", UA_WIN, "KR")
        with self.assertRaises(remote.LoginError):
            self.svc.r_login_finish(c.proof(st), "198.51.100.8", UA_WIN, "KR")
        if dev:
            self.assertIsNotNone(self.svc.store.device(dev))   # revoke=False → 기억한 기기 그대로
        self.svc.login_set(None, PW, PW, force=True, ack=True)   # 비밀번호 정하기 (기본 revoke → 아이디로 연결한 기기 끊음)
        self.assertEqual(self.svc.store.login()["mode"], "password")
        self.login(pw=PW, cid=self.OWNER, remember=False)

    def test_id_change_stays_on_owner_id(self):
        self.set_id_only()
        with self.assertRaises(ValueError):
            self.svc.login_id("kimcoach99", force=True)
        self.assertEqual(self.svc.store.login()["id"], self.OWNER)
        self.svc.login_id("Wavely.1213", force=True)   # 같은 맞춘 꼴 → 그대로 들어감
        self.login(pw=self.OWNER, cid=self.OWNER, remember=False)

    def test_view_and_status_have_no_secret(self):
        self.set_id_only()
        v = self.svc.login_view()
        raw = json.dumps(v, ensure_ascii=False)
        for k in ("verifier", "salt", "bkey", "fake"):
            self.assertNotIn(k, raw)


class PresetTests(LoginBase):
    """미리 정해 둔 로그인 (D-258) — 가짜 비밀번호로만 (저장소의 PRESET 은 None).
    채널 조건은 배포한 기본 config 그대로 (모든 설치가 그 채널 → 그것만으로는 소유자 PC 를 못 가림 · 검토 고침) —
    소유자 PC 표시(이 판 전에 QR 로 연결해 둔 옛 기기 · 그 채널로 연결한 유튜브) · 같은 아이디를 쓰는 PC · 파일 밖 표까지."""
    http_listener = False

    def make_preset(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import make_preset
        return make_preset.make(FAKE_PRESET_PW, iters=100_000)

    def seed_owner(self, home, owner):
        """소유자 PC 처럼: 'paired' = 이 판 전에 QR 로 연결해 둔 기기(via 칸 없음) · 'youtube' = 그 채널로 연결한 유튜브 ·
        'new_phone' = 이 판에서 연결한 기기(via=code · 소유자 표시 아님) · 'other_yt' = 다른 채널 유튜브."""
        home.mkdir(parents=True, exist_ok=True)
        if owner in ("paired", "new_phone"):  # 파일만 (서비스를 띄우면 뒤늦은 비콘 저장이 시험의 remote.json 을 덮을 수 있음)
            st = remote.Store(home / "remote.json", self.clock)
            dev, _ = st.add_device("Galaxy · 삼성 인터넷")
            if owner == "paired":
                dev.pop("via", None)
                st.save()
        if owner in ("youtube", "other_yt"):
            ch = rlogin.PRESET_CHANNEL if owner == "youtube" else "UC" + "x" * 22
            remote.yt_api.save_secret(home / "youtube" / "token.bin", {"refresh_token": "가짜", "channel": {"id": ch, "title": "채널"}})

    def fresh(self, preset, channel=None, home=None, owner=None):
        home = home or (self.tmp / "다른 PC")
        if owner:
            self.seed_owner(home, owner)
        cfg = {"channel_url": channel} if channel is not None else {}
        with mock.patch.object(rlogin, "PRESET", preset), mock.patch.dict(core.CONFIG, cfg):
            svc = remote.Service(home=home, clock=self.clock, ntfy=self.ntfy.url)
            fb = FakeBridge()
            svc.init(fb.bridge())
            self.addCleanup(svc.turn_off)
            self.addCleanup(close_service, svc)
            if svc._preset_thread:
                svc._preset_thread.join(15)
        return svc, fb

    def marker(self, home):
        return home / remote.PRESET_MARK

    def test_shipped_default_config_is_the_owner_channel(self):
        """그래서 채널만으로는 가리지 못함 (모든 설치의 기본값)."""
        self.assertIn(rlogin.PRESET_CHANNEL, core.CONFIG_DEFAULT["channel_url"])
        shipped = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
        self.assertIn(rlogin.PRESET_CHANNEL, shipped["channel_url"])
        self.assertIn(rlogin.PRESET_CHANNEL, core.CONFIG["channel_url"])

    def test_preset_none_does_nothing(self):
        svc, _ = self.fresh(None, owner="paired")
        self.assertIsNone(svc.store.login())
        self.assertNotIn("presetApplied", svc.store.data)
        self.assertEqual(svc.login_view()["idHint"], "wavely1213")

    def test_stranger_install_with_default_config_not_applied(self):
        """공개 앱을 새로 깐 PC (기본 config · 옛 기기 없음 · 유튜브 없음 · 이 판에서 휴대폰을 연결한 PC 도) → 넣지 않음."""
        p = self.make_preset()
        for owner in (None, "new_phone", "other_yt"):
            home = self.tmp / f"남의 PC {owner}"
            svc, _ = self.fresh(p, home=home, owner=owner)
            self.assertIsNone(svc._preset_thread, owner)
            self.assertIsNone(svc.store.login(), owner)
            self.assertNotIn("presetApplied", svc.store.data)
            self.assertFalse(self.marker(home).exists())

    def test_preset_applied_on_owner_pc_once(self):
        p = self.make_preset()
        for owner in ("paired", "youtube"):
            home = self.tmp / f"소유자 PC {owner}"
            svc, fb = self.fresh(p, home=home, owner=owner)
            lg = svc.store.login()
            self.assertEqual((lg["id"], lg["idShow"], lg["enabled"], lg["preset"]), ("wavely1213", "wavely1213", True, True), owner)
            self.assertTrue(svc.store.data["presetApplied"])
            self.assertEqual(json.loads(self.marker(home).read_text(encoding="utf-8"))["result"], "applied")
            v = svc.login_view()
            self.assertTrue(v["preset"] and v["set"] and v["enabled"])
            self.assertEqual(svc.state, "off")   # 원격 접속은 켜지 않음
            self.assertFalse(svc.store.data.get("enabled"))
            raw = (home / "remote.json").read_text(encoding="utf-8") + self.marker(home).read_text(encoding="utf-8")
            for form in (FAKE_PRESET_PW, rlogin.canon_pw(FAKE_PRESET_PW)):
                self.assertNotIn(form, raw)
            self.assertTrue(any("미리 정해 둔 아이디·비밀번호" in x for x in fb.lines))

    def test_preset_login_works_with_fake_password(self):
        p = self.make_preset()
        svc, _ = self.fresh(p, owner="paired")
        svc.turn_on()
        self.assertTrue(self.wait(lambda: svc.state == "on"))
        c = Client(svc, "wavely1213", FAKE_PRESET_PW)
        st = svc.r_login_start(c.start_body(), "198.51.100.1", UA_WIN, "KR")
        fin = svc.r_login_finish(c.proof(st, remember=False), "198.51.100.1", UA_WIN, "KR")
        self.assertTrue(c.open(fin, st["pc"]["id"])["guest"])
        c2 = Client(svc, "WAVELY1213", "가짜미리정한비번8315")
        st = svc.r_login_start(c2.start_body(), "198.51.100.1", UA_WIN, "KR")
        with self.assertRaises(remote.LoginError):
            svc.r_login_finish(c2.proof(st), "198.51.100.1", UA_WIN, "KR")

    def test_id_only_preset_applied_and_logs_in_with_id(self):
        """배포하는 PRESET 모양 (D-291): 아이디만 · 소유자 PC 에 넣으면 아이디 하나로 연결."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import make_preset
        p = make_preset.make()
        svc, fb = self.fresh(p, owner="paired")
        lg = svc.store.login()
        self.assertEqual((lg["id"], lg["mode"], lg["preset"]), ("wavely1213", "idOnly", True))
        self.assertTrue(svc.login_view()["idOnly"])
        self.assertTrue(any("wavely1213)만으로 연결을 준비했어요" in x for x in fb.lines))
        svc.turn_on()
        self.assertTrue(self.wait(lambda: svc.state == "on"))
        c = Client(svc, "wavely1213", rlogin.id_secret("ㅈㅁㅍ디ㅛ1213"))
        st = svc.r_login_start(c.start_body(), "198.51.100.1", UA_WIN, "KR")
        fin = svc.r_login_finish(c.proof(st, remember=False), "198.51.100.1", UA_WIN, "KR")
        self.assertTrue(c.open(fin, st["pc"]["id"])["guest"])

    def test_id_only_preset_with_foreign_verifier_not_applied(self):
        """아이디만이라면서 검증값이 아이디에서 나온 것이 아니면 넣지 않음 (손댄 PRESET · D-291)."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import make_preset
        p = dict(make_preset.make(), verifier=self.make_preset()["verifier"])
        svc, _ = self.fresh(p, owner="paired")
        self.assertIsNone(svc.store.login())

    def test_preset_wrong_channel_not_applied(self):
        p = self.make_preset()
        for ch in ("https://www.youtube.com/@other", "", "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx"):
            svc, _ = self.fresh(p, ch, home=self.tmp / f"채널 {len(ch)}", owner="paired")
            self.assertIsNone(svc.store.login(), ch)
            self.assertNotIn("presetApplied", svc.store.data)

    def test_preset_not_applied_when_configured(self):
        self.set_login()   # 이 PC 는 이미 김코치77
        p = self.make_preset()
        svc, _ = self.fresh(p, home=self.tmp / ".futsal-studio", owner="youtube")
        self.assertEqual(svc.store.login()["id"], CID)
        self.assertNotIn("presetApplied", svc.store.data)

    def test_preset_not_applied_over_bad_block(self):
        home = self.tmp / "깨진 PC"
        svc0, _ = self.fresh(None, home=home, owner="paired")
        svc0.store.data["login"] = {"v": 1, "broken": True}
        svc0.store.save()
        svc, _ = self.fresh(self.make_preset(), home=home)
        self.assertTrue(svc.store.login_bad)
        self.assertIsNone(svc.store.login())

    def test_preset_applied_once_never_reapplied_after_change_or_off(self):
        p = self.make_preset()
        home = self.tmp / "다른 PC"
        svc, _ = self.fresh(p, home=home, owner="youtube")
        svc.login_set(None, "소유자가바꾼비번6153", "소유자가바꾼비번6153", force=True, ack=True)
        svc2, _ = self.fresh(p, home=home)
        lg = svc2.store.login()
        self.assertNotIn("preset", lg)
        self.assertEqual(lg["gen"], 2)
        svc2.login_off()
        svc3, _ = self.fresh(p, home=home)
        self.assertFalse(svc3.store.login()["enabled"])   # 끈 그대로 · 다시 넣지 않음
        svc3.store.data["login"] = None                    # (지워도) presetApplied 가 있으면 다시 넣지 않음
        svc3.store.save()
        svc4, _ = self.fresh(p, home=home)
        self.assertIsNone(svc4.store.login())

    def test_store_reset_or_deleted_does_not_reapply(self):
        """검토 고침: remote.json 이 깨져(.bad) 새로 만들어지거나 지워져도 공개된 미리 정한 비밀번호로 되돌아가지 않음 (파일 밖 표)."""
        p = self.make_preset()
        home = self.tmp / "소유자 PC"
        svc, _ = self.fresh(p, home=home, owner="youtube")   # 유튜브 표시는 remote.json 이 새로 만들어져도 남음
        svc.login_set(None, "소유자가바꾼비번6153", "소유자가바꾼비번6153", force=True, ack=True)
        path = home / "remote.json"
        raw = path.read_bytes()
        path.write_bytes(raw[: len(raw) // 2])             # 반쯤 잘린 파일 (전원이 나감 · OneDrive)
        svc2, _ = self.fresh(p, home=home)
        self.assertTrue((home / "remote.json.bad").exists())
        self.assertIsNone(svc2.store.login())
        self.assertIsNone(svc2._preset_thread)
        path.unlink(missing_ok=True)   # 지워진 remote.json
        svc3, _ = self.fresh(p, home=home)
        self.assertIsNone(svc3.store.login())
        # (예전 판처럼) remote.json 에만 presetApplied 가 있으면 파일 밖 표도 남김
        home2 = self.tmp / "예전 표"
        svc4, _ = self.fresh(None, home=home2, owner="youtube")
        svc4.store.data["presetApplied"] = 1
        svc4.store.save()
        svc5, _ = self.fresh(p, home=home2)
        self.assertTrue(self.marker(home2).exists())
        self.assertIsNone(svc5.store.login())

    def test_taken_by_other_pc_not_applied_and_marked(self):
        """같은 아이디(wavely1213)를 12시간 안에 쓴 다른 PC 가 있으면 넣지 않고 다시도 넣지 않음 (밖에서 아무 PC 에나 붙지 않게)."""
        d = rlogin.derive_login("wavely1213")
        rec = {"v": 1, "pc": "ab" * 8, "seq": 1, "ts": int(self.clock()) - 60, "state": "on", "reason": None, "url": "https://a-b.trycloudflare.com"}
        self.ntfy.seed(d["topic"], rlogin.seal_lookup(d["bkey"], d["topic"], rec, remote.gcm_seal))
        home = self.tmp / "둘째 PC"
        svc, fb = self.fresh(self.make_preset(), home=home, owner="paired")
        self.assertIsNone(svc.store.login())
        self.assertTrue(svc.store.data["presetApplied"])
        self.assertEqual(json.loads(self.marker(home).read_text(encoding="utf-8"))["result"], "taken")
        self.assertTrue(any("다른 PC가 쓰고 있어서" in x for x in fb.lines))

    def test_ntfy_unreachable_retried_next_start(self):
        p = self.make_preset()
        home = self.tmp / "인터넷 없는 PC"
        self.ntfy.down = True
        svc, _ = self.fresh(p, home=home, owner="paired")
        self.assertIsNone(svc.store.login())
        self.assertFalse(self.marker(home).exists())
        self.ntfy.down = False
        svc2, _ = self.fresh(p, home=home)
        self.assertTrue(svc2.store.login()["preset"])


class PcApiTests(LoginBase):
    """로컬 화면 서버(app.Handler)의 /api/remote/login* — 이 PC 화면에서만 (Host·Origin) · 본문은 기록하지 않음."""

    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.lport = self.srv.server_address[1]
        self.logfile = self.tmp / "studio.log"
        for p in (mock.patch.object(app, "PORT", self.lport), mock.patch.object(app, "LOGFILE", self.logfile),
                  mock.patch.object(remote, "SVC", self.svc)):
            p.start()
            self.addCleanup(p.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def local(self, method, path, body=None, host=None, origin=None):
        h = {"Host": host or f"127.0.0.1:{self.lport}"}
        if origin:
            h["Origin"] = origin
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.lport}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_local_only_host_origin(self):
        body = {"id": ID, "password": PW, "password2": PW, "force": True, "ack": True}
        for host in ("abc-def.trycloudflare.com", "remote.futsal.invalid"):
            self.assertEqual(self.local("POST", "/api/remote/login", body, host=host)[0], 403)
        self.assertEqual(self.local("POST", "/api/remote/login", body, origin=ORIGIN)[0], 403)
        self.assertIsNone(self.svc.store.login())
        st, j = self.local("POST", "/api/remote/login", body)
        self.assertEqual(st, 200, j)
        self.assertEqual(j["login"]["idShow"], ID)
        st, j = self.local("GET", "/api/remote")
        self.assertTrue(j["login"]["set"])

    def test_routes_and_errors(self):
        st, j = self.local("POST", "/api/remote/login/check", {"id": ID, "password": "qwer1234"})
        self.assertEqual((st, j["problems"]), (200, ["common"]))
        st, j = self.local("POST", "/api/remote/login/suggest", {"id": ID})
        self.assertEqual(rlogin.pw_problems(j["password"], CID), [])
        self.svc.revoke(everyone=True)
        st, j = self.local("POST", "/api/remote/login", {"id": ID, "password": PW, "password2": PW, "force": True})
        self.assertEqual((st, j["code"]), (409, "need_ack"))
        self.ntfy.down = True
        st, j = self.local("POST", "/api/remote/login", {"id": ID, "password": PW, "password2": PW, "ack": True})
        self.assertEqual((st, j["code"]), (409, "unchecked"))
        st, j = self.local("POST", "/api/remote/login", {"id": ID, "password": "qwer1234", "password2": "qwer1234", "ack": True, "force": True})
        self.assertEqual((st, j["error"]), (400, "너무 흔한 비밀번호예요"))
        st, j = self.local("POST", "/api/remote/login", {"id": ID, "password": PW, "password2": PW, "ack": True, "force": True})
        self.assertEqual(st, 200)
        for path in ("/api/remote/login/off", "/api/remote/login/on", "/api/remote/login/unlock"):
            st, j = self.local("POST", path, {"force": True})
            self.assertEqual(st, 200, path)
        st, j = self.local("POST", "/api/remote/login/kick", {"ref": "nope"})
        self.assertEqual(st, 400)
        st, j = self.local("POST", "/api/remote/login/id", {"id": "kimcoach99", "force": True})
        self.assertEqual((st, j["login"]["idKeys"]), (200, "kimcoach99"))

    def test_app_route_bodies_not_logged(self):
        self.local("POST", "/api/remote/login", {"id": ID, "password": PW, "password2": PW, "force": True, "ack": True})
        self.local("POST", "/api/remote/login/check", {"id": ID, "password": PW})
        self.local("POST", "/api/remote/login", {"password": "qwer1234", "password2": "qwer1234"})
        text = self.logfile.read_text(encoding="utf-8") if self.logfile.exists() else ""
        text += "\n".join(self.lines)
        for form in (PW, PW_KEYS, CID, ID):
            self.assertNotIn(form, text)

    def test_studio_session_gets_403_pc_only_for_all_api_remote_login(self):
        for p in ("/api/remote/login", "/api/remote/login/id", "/api/remote/login/check", "/api/remote/login/suggest",
                  "/api/remote/login/on", "/api/remote/login/off", "/api/remote/login/unlock", "/api/remote/login/kick"):
            self.assertEqual(rstudio.classify("POST", p), rstudio.PC_ONLY, p)


if __name__ == "__main__":
    unittest.main()
