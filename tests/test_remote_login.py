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
from remote_fixture import ORIGIN, Clock, FakeBridge, FakeNtfy, dev_env, http, pair_device, signed, topics_of  # noqa: E402

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
        self.addCleanup(self.svc._ctl_stop.set)
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
        self.assertTrue(any("다른 PC가 같은 아이디를 쓰고 있어요" in x for x in self.lines))

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
        self.assertEqual(self.call("/r/login/start", {"v": 1}, headers={"Cf-Connecting-Ip": "203.0.113.51"})[0], 400)

    def test_ip_lockout_429(self):
        ip = "203.0.113.60"
        self.svc.limiter.locked_until[ip] = self.clock() + 900
        self.assertEqual(self.call("/r/login/start", self.c.start_body(), headers={"Cf-Connecting-Ip": ip})[0], 429)

    def test_login_off_403(self):
        self.svc.login_off()
        st, j = self.start()
        self.assertEqual((st, j["code"]), (403, "login_off"))
        self.assertEqual(j["error"], "이 PC는 지금 아이디·비밀번호 연결을 쓰지 않아요 · PC '휴대폰으로 보기'에서 켜 주세요")

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


class PresetTests(LoginBase):
    """미리 정해 둔 로그인 (D-258) — 가짜 비밀번호로만 (저장소의 PRESET 은 None)."""
    http_listener = False

    def make_preset(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import make_preset
        return make_preset.make(FAKE_PRESET_PW, iters=100_000)

    def fresh(self, preset, channel="https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g", home=None):
        home = home or (self.tmp / "다른 PC")
        with mock.patch.object(rlogin, "PRESET", preset), mock.patch.dict(core.CONFIG, {"channel_url": channel}):
            svc = remote.Service(home=home, clock=self.clock, ntfy=self.ntfy.url)
            fb = FakeBridge()
            svc.init(fb.bridge())
            self.addCleanup(svc.turn_off)
            self.addCleanup(svc._ctl_stop.set)
        return svc, fb

    def test_preset_none_does_nothing(self):
        svc, _ = self.fresh(None)
        self.assertIsNone(svc.store.login())
        self.assertNotIn("presetApplied", svc.store.data)
        self.assertEqual(svc.login_view()["idHint"], "wavely1213")

    def test_preset_applied_on_matching_channel_once(self):
        p = self.make_preset()
        svc, fb = self.fresh(p)
        lg = svc.store.login()
        self.assertEqual((lg["id"], lg["idShow"], lg["enabled"], lg["preset"]), ("wavely1213", "wavely1213", True, True))
        self.assertTrue(svc.store.data["presetApplied"])
        v = svc.login_view()
        self.assertTrue(v["preset"] and v["set"] and v["enabled"])
        self.assertEqual(svc.state, "off")   # 원격 접속은 켜지 않음
        self.assertFalse(svc.store.data.get("enabled"))
        raw = (self.tmp / "다른 PC" / "remote.json").read_text(encoding="utf-8")
        for form in (FAKE_PRESET_PW, rlogin.canon_pw(FAKE_PRESET_PW)):
            self.assertNotIn(form, raw)
        self.assertTrue(any("미리 정해 둔 아이디·비밀번호" in x for x in fb.lines))

    def test_preset_login_works_with_fake_password(self):
        p = self.make_preset()
        svc, _ = self.fresh(p)
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

    def test_preset_wrong_channel_not_applied(self):
        p = self.make_preset()
        for ch in ("https://www.youtube.com/@other", "", "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx"):
            svc, _ = self.fresh(p, ch, home=self.tmp / f"채널 {len(ch)}")
            self.assertIsNone(svc.store.login(), ch)
            self.assertNotIn("presetApplied", svc.store.data)

    def test_preset_not_applied_when_configured(self):
        self.set_login()   # 이 PC 는 이미 김코치77
        p = self.make_preset()
        svc, _ = self.fresh(p, home=self.tmp / ".futsal-studio")
        self.assertEqual(svc.store.login()["id"], CID)
        self.assertNotIn("presetApplied", svc.store.data)

    def test_preset_not_applied_over_bad_block(self):
        home = self.tmp / "깨진 PC"
        home.mkdir()
        svc0, _ = self.fresh(None, home=home)
        svc0.store.data["login"] = {"v": 1, "broken": True}
        svc0.store.save()
        svc, _ = self.fresh(self.make_preset(), home=home)
        self.assertTrue(svc.store.login_bad)
        self.assertIsNone(svc.store.login())

    def test_preset_applied_once_never_reapplied_after_change_or_off(self):
        p = self.make_preset()
        home = self.tmp / "다른 PC"
        svc, _ = self.fresh(p, home=home)
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
