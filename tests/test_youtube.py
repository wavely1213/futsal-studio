"""유튜브 바로 올리기 시험 (youtube_api · youtube_upload · /api/youtube/*) — 가짜 Google(tests/fake_google.py)로만, 인터넷 없이.
저장소 폴더에서: python3 -m unittest tests.test_youtube
"""
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import youtube_api as yt  # noqa: E402
import fake_google  # noqa: E402

KB = 1024
UNIT = 256 * KB
CLIENT = {"client_id": fake_google.CLIENT_ID, "client_secret": fake_google.CLIENT_SECRET, "project_id": fake_google.PROJECT_ID}


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


class FakeBase(unittest.TestCase):
    """가짜 Google 하나를 묶음마다 띄우고, 시험마다 처음 상태로."""

    @classmethod
    def setUpClass(cls):
        cls.fake = fake_google.FakeGoogle()
        cls.base = cls.fake.start(0)

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def setUp(self):
        self.fake.reset()
        self.fake.set_mode({"auto": {"account": "acc-brand"}})
        self._p = [mock.patch.dict(os.environ, {yt.ENV: self.base}), mock.patch.object(yt, "CHUNK", UNIT),
                   mock.patch.object(yt, "BACKOFF", (0.01,) * 8), mock.patch.object(yt, "JITTER", 0.0),
                   mock.patch.object(yt, "RATE_WAIT", 0.01)]
        for p in self._p:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._p])

    def mode(self, **kw):
        self.fake.set_mode(kw)

    def login(self, flow=None, timeout=10):
        """가짜 Google 자동 동의로 로그인 → 토큰 기록 (LoginFlow 실제 경로 그대로)."""
        flow = flow or yt.LoginFlow()
        got = {}

        def on_token(tok):
            got["tok"] = tok
            c = yt.Client(CLIENT, tok)
            return c.channel_mine()

        url = flow.start(CLIENT, on_token, timeout=timeout)
        code, _, body = _get(url)
        for _ in range(100):
            if flow.snapshot()["state"] != "waiting":
                break
            time.sleep(0.02)
        return flow, got.get("tok"), code, body

    def client(self, tok=None, **kw):
        if tok is None:
            _, tok, _, _ = self.login()
        return yt.Client(CLIENT, tok, **kw)


class PkceAndClientTests(unittest.TestCase):
    def test_rfc7636_vector(self):
        """RFC 7636 부록 B 의 예: verifier → S256 challenge."""
        self.assertEqual(yt.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_verifier_charset_and_length(self):
        for _ in range(20):
            v, c = yt.pkce_pair()
            self.assertTrue(43 <= len(v) <= 128)
            self.assertRegex(v, r"^[A-Za-z0-9\-._~]+$")
            self.assertEqual(c, yt.challenge(v))
            self.assertNotIn("=", c)

    def test_auth_url_has_pkce_state_offline(self):
        url = yt.auth_url(CLIENT, "http://127.0.0.1:5000/", "CH", "ST")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(q["scope"], [yt.SCOPE])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["state"], ["ST"])
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))

    def test_parse_installed_json(self):
        text = json.dumps({"installed": {"client_id": CLIENT["client_id"], "project_id": "p1", "client_secret": CLIENT["client_secret"],
                                         "auth_uri": "https://accounts.google.com/o/oauth2/auth", "redirect_uris": ["http://localhost"]}})
        c, warn = yt.parse_client(text)
        self.assertEqual((c["client_id"], c["client_secret"], c["project_id"]), (CLIENT["client_id"], CLIENT["client_secret"], "p1"))
        self.assertIsNone(warn)

    def test_web_client_rejected(self):
        with self.assertRaises(yt.ApiError) as cm:
            yt.parse_client(json.dumps({"web": {"client_id": CLIENT["client_id"], "client_secret": "GOCSPX-x123456789"}}))
        self.assertIn("데스크톱 앱", str(cm.exception))

    def test_bad_id_and_broken_json(self):
        with self.assertRaises(yt.ApiError):
            yt.parse_client(None, "abc.apps.googleusercontent.com", "GOCSPX-1234567890")
        with self.assertRaises(yt.ApiError):
            yt.parse_client("{not json")
        with self.assertRaises(yt.ApiError):
            yt.parse_client("x" * (yt.CLIENT_MAX + 1))

    def test_two_fields_and_missing_secret(self):
        c, warn = yt.parse_client(None, "  " + CLIENT["client_id"] + " ", "abcdefghijklmn")
        self.assertEqual(c["client_id"], CLIENT["client_id"])
        self.assertIn("GOCSPX", warn)
        no_secret = json.dumps({"installed": {"client_id": CLIENT["client_id"], "project_id": "p"}})
        with self.assertRaises(yt.ApiError) as cm:
            yt.parse_client(no_secret)
        self.assertIn("보안 비밀번호 추가", str(cm.exception))
        c, _ = yt.parse_client(no_secret, client_secret=CLIENT["client_secret"])  # JSON 에 없으면 칸에 넣은 것
        self.assertEqual(c["client_secret"], CLIENT["client_secret"])

    def test_endpoint_override_only_loopback_http(self):
        with mock.patch.dict(os.environ, {yt.ENV: "http://127.0.0.1:9/"}):
            self.assertEqual(yt.endpoints()["token"], "http://127.0.0.1:9/token")
        for bad in ("https://evil.example", "http://evil.example:80", "http://127.0.0.1", "http://127.0.0.1:9/x", "file:///etc"):
            with mock.patch.dict(os.environ, {yt.ENV: bad}):
                self.assertEqual(yt.endpoints(), yt.ENDPOINTS, bad)


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="유튜브 비밀 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_roundtrip_plain_with_permissions(self):
        p = self.tmp / "youtube" / "token.bin"
        yt.save_secret(p, {"refresh_token": "1//abc", "한글": "값"})
        self.assertEqual(yt.load_secret(p), {"refresh_token": "1//abc", "한글": "값"})
        self.assertTrue(p.read_bytes().startswith(b"FSY1P\n"))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        self.assertFalse(list(p.parent.glob("*.tmp")))

    def test_corrupt_or_foreign_files_are_missing(self):
        p = self.tmp / "x.bin"
        for raw in (b"", b"FSY1P\n{broken", b"FSY1X\n{}", b"{\"refresh_token\": \"1//abc\"}", b"FSY1P\n[1, 2]"):
            p.write_bytes(raw)
            self.assertIsNone(yt.load_secret(p), raw)
        self.assertIsNone(yt.load_secret(self.tmp / "없음.bin"))
        yt.delete_secret(self.tmp / "없음.bin")  # 없어도 괜찮음

    def test_windows_path_uses_dpapi_and_never_plaintext(self):
        calls = []

        def fake_dpapi(data, encrypt):
            calls.append(encrypt)
            data = bytes(data)
            if encrypt:
                return b"DPAPI" + bytes(b ^ 0x5A for b in data)
            if not data.startswith(b"DPAPI"):
                raise OSError("다른 사용자")
            return bytes(b ^ 0x5A for b in data[5:])

        p = self.tmp / "token.bin"
        with mock.patch.object(yt, "_use_dpapi", return_value=True), mock.patch.object(yt, "_dpapi", side_effect=fake_dpapi):
            yt.save_secret(p, {"refresh_token": "1//SECRETVALUE", "access_token": "ya29.SECRET"})
            raw = p.read_bytes()
            self.assertTrue(raw.startswith(b"FSY1D\n"))
            self.assertNotIn(b"SECRETVALUE", raw)
            self.assertNotIn(b"refresh_token", raw)
            self.assertEqual(yt.load_secret(p)["refresh_token"], "1//SECRETVALUE")
            p.write_bytes(b"FSY1D\nnot-dpapi")  # 다른 PC·다른 사용자 → 없음
            self.assertIsNone(yt.load_secret(p))
        self.assertEqual(calls[:2], [True, False])
        kind, data = yt.protect(b"abc")  # 리눅스는 그대로
        self.assertEqual((kind, yt.unprotect(kind, data)), ("plain", b"abc"))

    def test_dpapi_is_not_touched_at_import(self):
        self.assertEqual(yt._CRYPT, {})  # ctypes.windll 은 쓸 때만


class LoginTests(FakeBase):
    def test_auto_consent_gets_token_and_channel(self):
        flow, tok, code, body = self.login()
        self.assertEqual(code, 200)
        self.assertIn("연결됐어요", body)
        snap = flow.snapshot()
        self.assertEqual(snap["state"], "done")
        self.assertEqual(snap["channel"]["id"], fake_google.OWN_CHANNEL)
        self.assertTrue(tok["refresh_token"].startswith("1//FAKE"))
        self.assertIn(yt.SCOPE, tok["scope"].split())
        self.assertGreater(tok["refresh_expires_at"], time.time() + 6 * 86400)  # 테스트 상태 앱 → 7일
        la = self.fake.snapshot()["last_auth"]
        self.assertEqual((la["code_challenge_method"], la["access_type"], la["scope"]), ("S256", "offline", yt.SCOPE))
        self.assertRegex(la["redirect_uri"], r"^http://127\.0\.0\.1:\d+/$")
        self.assertEqual(la["code_challenge_len"], 43)

    def test_callback_rejects_bad_state_wrong_host_and_other_paths(self):
        self.mode(auto=None)
        flow = yt.LoginFlow()
        url = flow.start(CLIENT, lambda tok: {}, timeout=10)
        red = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["redirect_uri"][0]
        port = urllib.parse.urlsplit(red).port
        code, hdrs, body = _get(red + "?state=wrong&code=abc")
        self.assertEqual(code, 400)
        self.assertEqual(hdrs.get("Cache-Control"), "no-store")
        self.assertEqual(hdrs.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(_get(f"http://127.0.0.1:{port}/favicon.ico")[0], 404)
        self.assertEqual(_get(red + "?state=x", {"Host": f"localhost:{port}"})[0], 400)  # Host 가 다름
        self.assertEqual(flow.snapshot()["state"], "waiting")  # 잘못된 요청은 기다림을 끝내지 않음
        flow.cancel()
        self.assertEqual(flow.snapshot()["state"], "idle")

    def test_state_is_single_use(self):
        flow, tok, code, _ = self.login()
        self.assertEqual(flow.snapshot()["state"], "done")
        st = urllib.parse.parse_qs(urllib.parse.urlsplit(self.fake.snapshot()["last_auth"]["redirect_uri"]).query)
        self.assertEqual(st, {})  # redirect_uri 에 다른 것이 붙지 않음
        port = urllib.parse.urlsplit(self.fake.snapshot()["last_auth"]["redirect_uri"]).port
        time.sleep(0.3)
        with self.assertRaises(OSError):  # 한 번 받은 뒤에는 닫힘
            urllib.request.urlopen(f"http://127.0.0.1:{port}/?state=x", timeout=2)

    def test_access_denied_message(self):
        self.mode(auto={"deny": True})
        flow, tok, code, body = self.login()
        self.assertIsNone(tok)
        self.assertEqual(flow.snapshot()["state"], "error")
        self.assertEqual(flow.snapshot()["error"], yt.MSG["denied"])
        self.assertIn("테스트 사용자", body)

    def test_unchecked_scope_is_rejected_and_revoked(self):
        self.mode(auto={"account": "acc-brand", "grant": False})
        flow, tok, code, body = self.login()
        self.assertEqual(flow.snapshot()["error"], yt.MSG["consent"])
        self.assertIsNone(tok)  # on_token 까지 가지 않음
        self.assertEqual(self.fake.snapshot()["revoked"], 1)

    def test_timeout_and_second_login_cancels_first(self):
        self.mode(auto=None)
        a = yt.LoginFlow()
        a.start(CLIENT, lambda t: {}, timeout=0.3)
        time.sleep(0.6)
        self.assertEqual(a.snapshot(), {"state": "error", "url": None, "error": yt.MSG["login_timeout"]})
        b = yt.LoginFlow()
        u1 = b.start(CLIENT, lambda t: {}, timeout=10)
        p1 = urllib.parse.urlsplit(urllib.parse.parse_qs(urllib.parse.urlsplit(u1).query)["redirect_uri"][0]).port
        b.start(CLIENT, lambda t: {}, timeout=10)
        time.sleep(0.5)
        with self.assertRaises(OSError):
            urllib.request.urlopen(f"http://127.0.0.1:{p1}/?state=x", timeout=2)
        b.cancel()

    def test_no_channel_account(self):
        self.mode(auto={"account": "acc-me"})
        flow, tok, code, body = self.login()
        self.assertEqual(flow.snapshot()["error"], yt.MSG["no_channel"])

    def test_wrong_secret(self):
        flow = yt.LoginFlow()
        bad = dict(CLIENT, client_secret="GOCSPX-wrong-secret-0")
        url = flow.start(bad, lambda t: {}, timeout=10)
        _get(url)
        time.sleep(0.1)
        self.assertEqual(flow.snapshot()["error"], yt.MSG["client"])


class TokenTests(FakeBase):
    def test_expired_access_token_is_refreshed(self):
        _, tok, _, _ = self.login()
        saved = []
        c = yt.Client(CLIENT, dict(tok, expires_at=time.time() - 5), on_token_saved=saved.append)
        self.assertEqual(c.channel_mine()["id"], fake_google.OWN_CHANNEL)
        self.assertEqual(len(saved), 1)
        self.assertNotEqual(saved[0]["access_token"], tok["access_token"])
        self.assertEqual(saved[0]["refresh_token"], tok["refresh_token"])
        self.assertIn("token:refresh", self.fake.snapshot()["log"])

    def test_401_retries_once_with_new_token(self):
        _, tok, _, _ = self.login()
        with self.fake.lock:
            self.fake.access.clear()  # Google 쪽에서 access_token 만 버림
        c = yt.Client(CLIENT, tok)
        self.assertEqual(c.channel_mine()["id"], fake_google.OWN_CHANNEL)

    def test_revoked_refresh_token_needs_relogin(self):
        _, tok, _, _ = self.login()
        self.assertTrue(yt.revoke(tok))
        c = yt.Client(CLIENT, dict(tok, expires_at=0))
        with self.assertRaises(yt.ApiError) as cm:
            c.channel_mine()
        self.assertEqual(cm.exception.kind, "relogin")

    def test_testing_mode_expires_after_7_days(self):
        _, tok, _, _ = self.login()
        c = yt.Client(CLIENT, dict(tok, expires_at=0))
        c.channel_mine()
        self.fake.offset += 8 * 86400
        c2 = yt.Client(CLIENT, dict(tok, expires_at=0))
        with self.assertRaises(yt.ApiError) as cm:
            c2.channel_mine()
        self.assertEqual(cm.exception.kind, "relogin")
        self.mode(testing=False)  # 게시(프로덕션)한 앱은 끊기지 않음
        _, tok2, _, _ = self.login()
        self.fake.offset += 30 * 86400
        self.assertEqual(yt.Client(CLIENT, dict(tok2, expires_at=0)).channel_mine()["id"], fake_google.OWN_CHANNEL)


def _rand_file(path, size, seed=1):
    import random
    r = random.Random(seed)
    path.write_bytes(bytes(r.getrandbits(8) for _ in range(size)))
    return path


META = {"snippet": {"title": "풋살 시험 영상", "description": "설명 · 한국어", "tags": ["풋살", "풋살 레슨"], "categoryId": "17",
                    "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}


class ResumableTests(FakeBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp(prefix="유튜브 업로드 "))
        cls.big = _rand_file(cls.tmp / "영상 1.mp4", 5 * UNIT + 12345)
        cls.sha = hashlib.sha256(cls.big.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.c = self.client()

    def start(self):
        return self.c.start_upload(META, self.big.stat().st_size, "video/mp4", notify=False)

    def video(self):
        vids = self.fake.snapshot()["videos"]
        self.assertEqual(len(vids), 1)
        return vids[0]

    def test_multi_chunk_upload_and_content_range_sequence(self):
        seen, offs, prog = [], [], []
        real = yt._send

        def spy(method, url, data=None, headers=None, *a, **kw):
            if method == "PUT":
                seen.append(headers.get("Content-Range"))
            return real(method, url, data, headers, *a, **kw)

        uri = self.start()
        self.assertEqual(self.c.upload_status(uri, self.big.stat().st_size), ("incomplete", 0))  # 308 · Range 없음 = 0
        size = self.big.stat().st_size
        with mock.patch.object(yt, "_send", side_effect=spy):
            v = self.c.upload_file(uri, self.big, size, 0, on_offset=offs.append, progress=prog.append)
        self.assertEqual(v["snippet"]["title"], "풋살 시험 영상")
        self.assertEqual(self.video()["sha256"], self.sha)
        self.assertEqual(seen, [f"bytes {i * UNIT}-{(i + 1) * UNIT - 1}/{size}" for i in range(5)] + [f"bytes {5 * UNIT}-{size - 1}/{size}"])
        self.assertEqual(offs, [UNIT * i for i in range(1, 6)])
        self.assertEqual(prog[-1], size)
        st = self.fake.snapshot()
        self.assertEqual(st["insert_calls"], 1)
        self.assertEqual(self.video()["notifySubscribers"], False)
        self.assertIs(self.video()["selfDeclaredMadeForKids"], False)

    def test_server_error_once_is_retried(self):
        self.mode(fail_put_once=True)
        uri = self.start()
        self.c.upload_file(uri, self.big, self.big.stat().st_size, 0)
        self.assertEqual(self.video()["sha256"], self.sha)

    def test_connection_drop_resumes_from_server_offset(self):
        self.mode(drop_after=3 * UNIT + 1000)
        offs = []
        uri = self.start()
        with mock.patch.object(yt, "CHUNK", 8 * UNIT):  # 한 번에 다 보내다 끊김
            self.c.upload_file(uri, self.big, self.big.stat().st_size, 0, on_offset=offs.append)
        self.assertEqual(self.video()["sha256"], self.sha)
        self.assertIn(3 * UNIT, offs)  # 서버가 받은 데(256 KiB 단위)부터 이어서
        self.assertEqual(self.fake.snapshot()["insert_calls"], 1)

    def test_cancel_then_resume_same_session(self):
        self.mode(rate=400 * KB)
        big = _rand_file(self.tmp / "긴 영상.mp4", 24 * UNIT + 777, seed=7)
        sha = hashlib.sha256(big.read_bytes()).hexdigest()
        size = big.stat().st_size
        uri = self.c.start_upload(META, size, "video/mp4")
        cancel = threading.Event()
        threading.Timer(1.5, cancel.set).start()
        t0 = time.monotonic()
        with mock.patch.object(yt, "CHUNK", 32 * UNIT):  # 한 조각으로 보내는 중에 멈춤
            with self.assertRaises(yt.Cancelled):
                self.c.upload_file(uri, big, size, 0, cancel=cancel)
        self.assertLess(time.monotonic() - t0, 3.5)  # 응답을 기다리지 않고 바로 멈춤
        self.mode(rate=None)
        for _ in range(100):  # 서버가 이미 받아 둔(버퍼) 내용까지 다 읽고 끊김을 알아챌 때까지
            if self.fake.snapshot()["sessions"][0]["received"]:
                break
            time.sleep(0.05)
        kind, off = self.c.upload_status(uri, size)
        self.assertEqual(kind, "incomplete")
        self.assertEqual(off % UNIT, 0)
        self.assertGreater(off, 0)
        self.assertLess(off, size)
        self.c.upload_file(uri, big, size, off)
        self.assertEqual(self.video()["sha256"], sha)
        self.assertEqual(self.fake.snapshot()["insert_calls"], 1)

    def test_expired_session_is_404(self):
        uri = self.start()
        self.mode(expire_sessions=True)
        with self.assertRaises(yt.ApiError) as cm:
            self.c.upload_file(uri, self.big, self.big.stat().st_size, 0)
        self.assertEqual(cm.exception.kind, "session_expired")
        with self.assertRaises(yt.ApiError) as cm:
            self.c.upload_status(uri, self.big.stat().st_size)
        self.assertEqual(cm.exception.kind, "session_expired")

    def test_final_200_is_accepted_and_status_after_done(self):
        self.mode(final200=True)
        uri = self.start()
        v = self.c.upload_file(uri, self.big, self.big.stat().st_size, 0)
        self.assertRegex(v["id"], r"^[A-Za-z0-9_-]{11}$")
        self.assertEqual(self.c.upload_status(uri, self.big.stat().st_size)[0], "done")

    def test_network_down_gives_up_after_backoff(self):
        uri = self.start()
        with mock.patch.object(yt, "_send", side_effect=yt._Net("down")), mock.patch.object(yt, "BACKOFF", (0.001,) * 3):
            with self.assertRaises(yt.ApiError) as cm:
                self.c.upload_file(uri, self.big, self.big.stat().st_size, 0)
        self.assertEqual(cm.exception.kind, "network")

    def test_location_must_be_upload_host(self):
        with mock.patch.object(yt, "_send", return_value=(200, {"Location": "https://evil.example/upload/youtube/v3/videos?upload_id=x"}, b"")):
            with self.assertRaises(yt.ApiError) as cm:
                self.c.start_upload(META, 10, "video/mp4")
        self.assertEqual(cm.exception.kind, "server")

    def test_insert_errors_are_korean(self):
        size = self.big.stat().st_size
        cases = [({"snippet": dict(META["snippet"], categoryId="99")}, "invalidCategoryId"),
                 ({"snippet": dict(META["snippet"], title="")}, "invalidTitle"),
                 ({"snippet": dict(META["snippet"], description="가" * 1700)}, "invalidDescription"),
                 ({"status": dict(META["status"], privacyStatus="public", publishAt="2030-01-01T00:00:00Z")}, "invalidPublishAt")]
        for change, reason in cases:
            meta = dict(META, **change)
            with self.assertRaises(yt.ApiError) as cm:
                self.c.start_upload(meta, size, "video/mp4")
            self.assertEqual((cm.exception.kind, cm.exception.reason), ("bad_meta", reason))
            self.assertEqual(str(cm.exception), yt.MSG[reason])
        self.mode(upload_limit=True)
        with self.assertRaises(yt.ApiError) as cm:
            self.c.start_upload(META, size, "video/mp4")
        self.assertEqual(cm.exception.kind, "upload_limit")
        self.mode(upload_limit=False, quota_exhausted_at="videos.insert")
        with self.assertRaises(yt.ApiError) as cm:
            self.c.start_upload(META, size, "video/mp4")
        self.assertEqual(cm.exception.kind, "quota")
        self.assertIn("다시 채워져요", str(cm.exception))
        self.assertNotIn("{reset}", str(cm.exception))
        self.mode(quota_exhausted_at=None, api_disabled=True)
        with self.assertRaises(yt.ApiError) as cm:
            self.c.channel_mine()
        self.assertEqual(cm.exception.kind, "api_off")
        self.assertEqual(self.fake.snapshot()["insert_calls"], 0)


class PostStepTests(FakeBase):
    def setUp(self):
        super().setUp()
        self.c = self.client()
        self.tmp = Path(tempfile.mkdtemp(prefix="유튜브 마무리 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        f = _rand_file(self.tmp / "v.mp4", UNIT + 10)
        uri = self.c.start_upload(META, f.stat().st_size, "video/mp4")
        self.vid = self.c.upload_file(uri, f, f.stat().st_size)["id"]

    def test_thumbnail_ok_forbidden_rate_bad(self):
        jpg = self.tmp / "썸네일 1.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 2000)
        self.c.set_thumbnail(self.vid, jpg)
        self.assertEqual(self.fake.snapshot()["videos"][0]["thumbnail"]["type"], "jpeg")
        for mode, kind in (("thumb_forbidden", "thumb_verify"), ("thumb_rate", "rate")):
            self.mode(**{mode: True})
            with self.assertRaises(yt.ApiError) as cm:
                self.c.set_thumbnail(self.vid, jpg)
            self.assertEqual(cm.exception.kind, kind)
            self.mode(**{mode: False})
        self.assertIn("youtube.com/verify", yt.MSG["thumb_verify"])
        bad = self.tmp / "bad.jpg"
        bad.write_bytes(b"GIF89a....")
        with self.assertRaises(yt.ApiError) as cm:
            self.c.set_thumbnail(self.vid, bad)
        self.assertEqual(str(cm.exception), yt.MSG["thumb_bad"])

    def test_caption_multipart_and_exists(self):
        srt = "1\n00:00:00,000 --> 00:00:02,000\n안녕하세요 풋살사관학교예요\n".encode("utf-8")
        self.c.insert_caption(self.vid, srt)
        cap = self.fake.snapshot()["videos"][0]["captions"][0]
        self.assertEqual((cap["language"], cap["name"], cap["isDraft"]), ("ko", "한국어", False))
        self.assertEqual(cap["sha256"], hashlib.sha256(srt).hexdigest())
        with self.assertRaises(yt.ApiError) as cm:
            self.c.insert_caption(self.vid, srt)
        self.assertEqual(cm.exception.kind, "exists")
        with self.assertRaises(yt.ApiError) as cm:
            self.c.insert_caption(self.vid, b"no timing here", name="다른 이름")
        self.assertEqual(str(cm.exception), yt.MSG["caption_bad"])

    def test_playlists_create_list_add_and_errors(self):
        p = self.c.create_playlist("기본기 시리즈", "public")
        for i in range(3):
            self.c.create_playlist(f"목록 {i}", "private")
        with mock.patch.object(yt.Client, "request", wraps=self.c.request):
            lists = self.c.playlists()
        self.assertEqual(len(lists), 4)
        self.assertEqual(lists[0]["title"], "기본기 시리즈")
        self.c.add_to_playlist(p["id"], self.vid)
        self.assertEqual(self.fake.snapshot()["videos"][0]["playlists"], [p["id"]])
        with self.assertRaises(yt.ApiError) as cm:
            self.c.add_to_playlist("PLnope", self.vid)
        self.assertEqual(str(cm.exception), yt.MSG["playlist_missing"])
        self.mode(playlist_full=True)
        with self.assertRaises(yt.ApiError) as cm:
            self.c.add_to_playlist(p["id"], self.vid)
        self.assertEqual(str(cm.exception), yt.MSG["playlist_full"])

    def test_video_status_and_lock(self):
        self.assertEqual(self.c.video_status(self.vid)["status"]["privacyStatus"], "private")
        self.assertIsNone(self.c.video_status("AAAAAAAAAAA"))

    def test_quota_charge_hook_counts_calls(self):
        ops = []
        self.c.on_call = ops.append
        self.c.channel_mine()
        self.c.video_status(self.vid)
        self.assertEqual(ops, ["channels.list", "videos.list"])

    def test_rate_limit_waits_once_then_message(self):
        calls = []
        real = yt._send

        def flaky(method, url, data=None, headers=None, *a, **kw):
            calls.append(url)
            if len(calls) <= 2:
                return 403, {}, json.dumps(fake_google.gerror(403, "rateLimitExceeded", "slow down")[1]).encode()
            return real(method, url, data, headers, *a, **kw)

        with mock.patch.object(yt, "_send", side_effect=flaky):
            with self.assertRaises(yt.ApiError) as cm:
                self.c.channel_mine()
        self.assertEqual(cm.exception.kind, "rate")
        self.assertEqual(len(calls), 2)
        calls.clear()
        with mock.patch.object(yt, "_send", side_effect=lambda *a, **k: flaky(*a, **k) if len(calls) < 1 else real(*a, **k)):
            self.assertEqual(self.c.channel_mine()["id"], fake_google.OWN_CHANNEL)


class ClassifyTests(unittest.TestCase):
    def test_reasons_map_to_kinds(self):
        table = [("videos.insert", 403, "quotaExceeded", "quota"), ("videos.insert", 403, "dailyLimitExceeded", "quota"),
                 ("videos.insert", 400, "uploadLimitExceeded", "upload_limit"), ("channels.list", 403, "accessNotConfigured", "api_off"),
                 ("videos.insert", 401, "youtubeSignupRequired", "no_channel"), ("videos.insert", 401, "authError", "relogin"),
                 ("thumbnails.set", 403, "forbidden", "thumb_verify"), ("captions.insert", 409, "captionExists", "exists"),
                 ("captions.insert", 403, "forbidden", "forbidden"), ("videos.insert", 403, "insufficientPermissions", "forbidden"),
                 ("videos.insert", 403, "forbiddenPrivacySetting", "bad_meta"), ("videos.list", 404, "videoNotFound", "not_found"),
                 ("videos.insert", 503, "backendError", "server"), ("videos.insert", 429, None, "rate"),
                 ("videos.insert", 400, "somethingNew", "error")]
        for op, st, reason, kind in table:
            self.assertEqual(yt.classify(op, st, reason).kind, kind, (op, st, reason))
        e = yt.classify("videos.insert", 400, "somethingNew")
        self.assertIn("somethingNew", str(e))
        self.assertIn("studio.log", str(e))

    def test_error_body_parsing(self):
        _, body = fake_google.gerror(403, "forbidden", "x", info="SERVICE_DISABLED")
        self.assertEqual(yt.parse_google_error(json.dumps(body).encode())[0], "accessNotConfigured")
        self.assertEqual(yt.parse_google_error(b'{"error": "invalid_grant", "error_description": "x"}')[0], "invalid_grant")
        self.assertEqual(yt.parse_google_error(b"<html>")[0], None)
        self.assertEqual(yt.parse_google_error(b'{"error": {"errors": [{"reason": "a<b>c"}]}}')[0], "abc")



# ======================= 서비스 (youtube_upload) · 경로 (/api/youtube/*) =======================

import core  # noqa: E402
import editor  # noqa: E402
import upload  # noqa: E402
import youtube_upload as yu  # noqa: E402

FF = core.ffmpeg()


def make_video(path, size="320x180", dur=4, rate="5M"):
    """잡음을 넣어 크기가 큰(여러 조각) 짧은 영상."""
    r = core.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={dur}",
                  "-vf", "noise=alls=60:allf=t", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", rate, "-maxrate", rate,
                  "-bufsize", "1M", "-pix_fmt", "yuv420p", str(path)])
    if r.returncode:
        raise RuntimeError(r.stderr)
    return path


def make_jpg(path, size="1280x720"):
    r = core.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={size}", "-frames:v", "1", str(path)])
    if r.returncode:
        raise RuntimeError(r.stderr)
    return path


SEGS = [{"start": 0.2, "end": 1.6, "text": "안녕하세요 풋살사관학교예요."}, {"start": 1.8, "end": 3.6, "text": "오늘은 퍼스트 터치를 알려 드릴게요."}]


def srt_text(segs=SEGS):
    return "".join(f"{i}\n{core._ts(x['start'])} --> {core._ts(x['end'])}\n{x['text']}\n\n" for i, x in enumerate(segs, 1))


class ServiceBase(FakeBase):
    NAME = "풋살 레슨 [꿀팁] 1편.mp4"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = Path(tempfile.mkdtemp(prefix="유튜브 미디어 "))
        make_video(cls.media / "long.mp4")
        make_video(cls.media / "long2.mp4", dur=3)
        make_video(cls.media / "tall.mp4", "180x320", 3, "3M")
        make_jpg(cls.media / "thumb.jpg")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="유튜브 시험 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        (work / "projects").mkdir()
        self.home = self.tmp / "사용자" / ".futsal-studio"
        ps = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [
            mock.patch.object(editor, "PROJECTS", work / "projects"), mock.patch.object(core, "ENGINE_HOME", self.home),
            mock.patch.object(yu, "HOME", self.home / "youtube"), mock.patch.object(yu, "LOGIN", yt.LoginFlow()),
            mock.patch.dict(yu._STATE, {"last": None, "lastAt": None}), mock.patch.object(yu.webbrowser, "open", side_effect=self._browser)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.work, self.out = work, dirs["OUT"]
        self.logs = []
        self.cancel = threading.Event()

    def _browser(self, url):
        """시스템 브라우저 대신: 로그인 주소면 가짜 Google 자동 동의를 따라감 · 그 밖의 주소는 열지 않고 기록만."""
        self.opened = getattr(self, "opened", []) + [url]
        if url.startswith(self.base + "/o/oauth2/"):
            threading.Thread(target=_get, args=(url,), daemon=True).start()
        return True

    def log(self, msg):
        self.logs.append(msg)

    def connect(self):
        yu.save_client(json.dumps({"installed": CLIENT}))
        yu.start_login(self.log)
        for _ in range(200):
            if yu.LOGIN.snapshot()["state"] != "waiting":
                break
            time.sleep(0.02)
        self.assertEqual(yu.LOGIN.snapshot()["state"], "done", yu.LOGIN.snapshot())
        return yu.status()

    def add_video(self, name=None, kind="long", srt=True):
        name = name or self.NAME
        shutil.copy(self.media / f"{kind}.mp4", core.VIDEOS / name)
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(SEGS, ensure_ascii=False), encoding="utf-8")
        if srt:
            (d / "subtitles.srt").write_text(srt_text(), encoding="utf-8")
        return name

    def add_thumb(self, name=None):
        p = self.out / f"{core.adir(name or self.NAME).name}_디자인 1_1.jpg"
        shutil.copy(self.media / "thumb.jpg", p)
        return p

    def kit(self, name=None, seq=None):
        return upload.build_kit(name or self.NAME, seq)

    def add_edit(self, name=None, export=True, fmt="long", kind="long2"):
        name = name or self.NAME
        items = [{"id": "v", "track": "V1", "media": "main", "in": 0.0, "out": 3.0, "start": 0.0, "speed": 1.0, "link": "l"}]
        proj = {"source": name, "info": {"duration": 4.0, "width": 320, "height": 180}, "captions": [], "v": 2, "rev": 1,
                "sequences": [{"id": "s1", "name": "롱폼 가편집", "format": fmt, "v": 2, "items": items, "markers": [], "titles": [],
                               "tracks": editor.default_tracks()}]}
        editor._ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        if export:
            stem = f"{core.adir(name).name}_롱폼 가편집"
            shutil.copy(self.media / f"{kind}.mp4", self.out / f"{stem}.mp4")
            (self.out / f"{stem}.srt").write_text(srt_text(), encoding="utf-8")
        return "s1"

    def ready(self, thumb=True, **kw):
        self.connect()
        name = self.add_video(**kw)
        if thumb:
            self.add_thumb(name)
        k = self.kit(name)
        return name, k

    def run_up(self, name=None, seq=None, opts=None, again=False):
        opts = dict({"privacy": "private", "madeForKids": False}, **(opts or {}))
        return yu.run_upload(name or self.NAME, seq, opts, self.log, self.cancel, again)

    def video(self, i=-1):
        return self.fake.snapshot()["videos"][i]


class UploadFlowTests(ServiceBase):
    def test_full_upload_applies_kit_and_post_steps(self):
        name, k = self.ready()
        pl = yu.create_playlist("기본기 시리즈", "public")["playlist"]
        r = self.run_up(opts={"playlistId": pl["id"], "playlistTitle": pl["title"], "notify": False})
        self.assertTrue(r["ok"], r)
        v = self.video()
        self.assertEqual((v["title"], v["description"], v["tags"]), (k["title"], k["description"], k["tags"]))
        self.assertEqual((v["categoryId"], v["defaultLanguage"], v["defaultAudioLanguage"]), ("17", "ko", "ko"))
        self.assertIs(v["selfDeclaredMadeForKids"], False)
        self.assertEqual((v["privacy"], v["notifySubscribers"]), ("private", False))
        self.assertEqual(v["sha256"], hashlib.sha256((core.VIDEOS / name).read_bytes()).hexdigest())
        self.assertEqual(v["thumbnail"]["type"], "jpeg")
        self.assertEqual([(c["language"], c["name"]) for c in v["captions"]], [("ko", "한국어")])
        self.assertEqual(v["playlists"], [pl["id"]])
        self.assertEqual({k2: s["state"] for k2, s in r["steps"].items()}, {"thumbnail": "ok", "captions": "ok", "playlist": "ok"})
        self.assertEqual(r["url"], f"https://youtu.be/{r['videoId']}")
        self.assertEqual(r["studio"], f"https://studio.youtube.com/video/{r['videoId']}/edit")
        self.assertFalse(r["locked"])
        h = yu.history()["items"][0]
        self.assertEqual((h["videoId"], h["title"], h["name"]), (r["videoId"], k["title"], name))
        self.assertEqual(yu.pending(), [])
        q = yu.quota_view()["used"]  # 연결 1 · 재생목록 만들기 50 · 썸네일 50 · 자막 400 · 재생목록 넣기 50 · 확인 1
        self.assertEqual(q, {"uploads": 1, "units": 552})
        self.assertEqual(self.fake.snapshot()["quota"], q)
        self.assertEqual(yu.get_settings()["playlistId"], pl["id"])  # 고른 값이 다음 기본값
        self.assertTrue(any(m.startswith("유튜브에 올렸어요 · ") for m in self.logs))

    def test_edit_export_with_its_srt_and_shorts_url(self):
        self.connect()
        name = self.add_video()
        seq = self.add_edit(kind="tall")
        k = self.kit(name, seq)
        p = yu.plan(name, seq)
        self.assertEqual(p["files"]["video"], f"{core.adir(name).name}_롱폼 가편집.mp4")
        self.assertEqual(p["files"]["srt"], f"{core.adir(name).name}_롱폼 가편집.srt")
        self.assertTrue(p["files"]["shorts"])
        self.assertTrue(any("쇼츠로 올려요" in w for w in p["warnings"]))
        r = self.run_up(name, seq)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["url"], f"https://www.youtube.com/shorts/{r['videoId']}")
        self.assertEqual(self.video()["sha256"], hashlib.sha256((self.out / p["files"]["video"]).read_bytes()).hexdigest())
        self.assertEqual(self.video()["title"], k["title"])

    def test_pause_then_restart_resumes_without_new_insert(self):
        name, k = self.ready()
        size = (core.VIDEOS / name).stat().st_size
        self.mode(rate=max(200 * KB, size // 6))
        threading.Timer(1.2, self.cancel.set).start()
        with mock.patch.object(yt, "CHUNK", 64 * UNIT):
            r = self.run_up()
        self.assertFalse(r["ok"])
        self.assertTrue(r["paused"], r)
        self.assertEqual(r["error"], yt.MSG["cancelled"])
        key = yu.session_key(name, None)
        raw = yu._session_path(key).read_text(encoding="utf-8")
        for uid in self.fake.secret_values()["upload_ids"]:
            self.assertNotIn(uid, raw)  # 세션 주소는 그대로 쓰지 않음
        self.mode(rate=None)
        time.sleep(0.3)
        # 앱을 껐다 켬: 메모리 상태 없이 파일만으로
        with mock.patch.dict(yu._ACTIVE, {"key": None}), mock.patch.object(yu, "_PROBE", {}), mock.patch.object(yu, "_QUICK", {}):
            pend = yu.pending()
            self.assertEqual([(x["key"], x["state"]) for x in pend], [(key, "paused")])
            self.assertTrue(pend[0]["started"])
            self.cancel.clear()
            r2 = yu.resume(key, self.log, self.cancel)
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(self.fake.snapshot()["insert_calls"], 1)
        self.assertEqual(self.video()["sha256"], hashlib.sha256((core.VIDEOS / name).read_bytes()).hexdigest())
        self.assertTrue(any("이어 올려요" in m for m in self.logs))

    def test_changed_file_starts_new_session(self):
        name, k = self.ready()
        self.mode(rate=150 * KB)
        threading.Timer(0.6, self.cancel.set).start()
        with mock.patch.object(yt, "CHUNK", 64 * UNIT):
            self.assertTrue(self.run_up()["paused"])
        self.mode(rate=None)
        shutil.copy(self.media / "long2.mp4", core.VIDEOS / name)  # 같은 이름으로 다시 넣음
        self.cancel.clear()
        r = yu.resume(yu.session_key(name, None), self.log, self.cancel)
        self.assertTrue(r["ok"], r)
        self.assertIn("영상 파일이 바뀌어서 처음부터 다시 올려요", r["notes"])
        self.assertEqual(self.fake.snapshot()["insert_calls"], 2)
        self.assertEqual(self.video()["sha256"], hashlib.sha256((core.VIDEOS / name).read_bytes()).hexdigest())

    def test_expired_session_restarts_once(self):
        name, k = self.ready()
        self.mode(rate=150 * KB)
        threading.Timer(0.6, self.cancel.set).start()
        with mock.patch.object(yt, "CHUNK", 64 * UNIT):
            self.assertTrue(self.run_up()["paused"])
        self.mode(rate=None, expire_sessions=True)
        self.cancel.clear()
        r = yu.resume(yu.session_key(name, None), self.log, self.cancel)
        self.assertTrue(r["ok"], r)
        self.assertIn(yt.MSG["session_expired"], r["notes"])
        self.assertEqual(self.fake.snapshot()["insert_calls"], 2)

    def test_thumbnail_needs_verification_then_finish(self):
        self.ready()
        self.mode(thumb_forbidden=True)
        r = self.run_up()
        self.assertTrue(r["ok"])
        self.assertEqual(r["steps"]["thumbnail"]["state"], "needs_verify")
        self.assertIn("채널 인증", r["steps"]["thumbnail"]["msg"])
        self.assertEqual(r["steps"]["captions"]["state"], "ok")
        self.assertIsNone(self.video()["thumbnail"])
        self.mode(thumb_forbidden=False)
        r2 = yu.finish(r["videoId"], ["thumbnail"], self.log, self.cancel)
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["steps"]["thumbnail"]["state"], "ok")
        self.assertEqual(self.video()["thumbnail"]["type"], "jpeg")
        self.assertEqual(yu.history()["items"][0]["steps"]["thumbnail"]["state"], "ok")

    def test_quota_at_captions_then_finish_and_caption_exists(self):
        self.ready()
        self.mode(quota_exhausted_at="captions.insert")
        r = self.run_up()
        self.assertTrue(r["ok"])
        self.assertEqual(r["steps"]["captions"]["state"], "quota")
        self.assertIn("다시 채워져요", r["steps"]["captions"]["msg"])
        self.assertTrue(yu.quota_view()["exhausted"]["units"])
        self.assertEqual(yu.quota_view()["remaining"]["units"], 0)
        self.mode(quota_exhausted_at=None)
        r2 = yu.finish(r["videoId"], None, self.log, self.cancel)  # 끝나지 않은 것만 (자막)
        self.assertEqual(r2["steps"]["captions"]["state"], "ok")
        r3 = yu.finish(r["videoId"], ["captions"], self.log, self.cancel)  # 또 → captionExists 는 끝난 것으로
        self.assertEqual(r3["steps"]["captions"], {"state": "ok", "msg": "이미 올라가 있어요"})
        self.assertEqual(len(self.video()["captions"]), 1)

    def test_unverified_project_lock_is_detected(self):
        self.ready()
        self.mode(unverified_lock=True)
        r = self.run_up(opts={"privacy": "public"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["locked"])
        self.assertEqual(r["lockedMsg"], yu.LOCKED_MSG)
        self.assertTrue(yu.history()["items"][0]["locked"])

    def test_scheduled_publish(self):
        self.ready()
        when = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 86400))
        r = self.run_up(opts={"privacy": "scheduled", "publishAt": when})
        self.assertTrue(r["ok"], r)
        self.assertEqual((self.video()["privacy"], self.video()["publishAt"]), ("private", when))
        past = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 60))
        r2 = self.run_up(opts={"privacy": "scheduled", "publishAt": past}, again=True)
        self.assertFalse(r2["ok"])
        self.assertIn("15분 뒤", r2["error"])
        r3 = self.run_up(opts={"privacy": "scheduled", "publishAt": "내일"}, again=True)
        self.assertIn("예약 공개 시각", r3["error"])
        r4 = self.run_up(opts={"madeForKids": "아니요"}, again=True)
        self.assertIn("아동용", r4["error"])

    def test_quota_and_upload_limit_keep_session(self):
        name, k = self.ready()
        self.mode(quota_exhausted_at="videos.insert")
        r = self.run_up()
        self.assertEqual(r["kind"], "quota")
        self.assertIn("다시 채워져요", r["error"])
        self.assertTrue(yu.quota_view()["exhausted"]["uploads"])
        self.assertEqual([x["state"] for x in yu.pending()], ["failed"])
        self.mode(quota_exhausted_at=None, upload_limit=True)
        r = self.run_up()
        self.assertEqual((r["kind"], r["error"]), ("upload_limit", yt.MSG["upload_limit"]))
        self.mode(upload_limit=False)
        r = yu.resume(yu.session_key(name, None), self.log, self.cancel)
        self.assertTrue(r["ok"], r)
        self.assertFalse(yu.quota_view()["exhausted"]["uploads"])  # 다시 잘 되면 '다 씀' 표시를 지움

    def test_revoked_connection_then_reconnect_and_resume(self):
        name, k = self.ready()
        self.mode(rate=150 * KB)
        threading.Timer(0.6, self.cancel.set).start()
        with mock.patch.object(yt, "CHUNK", 64 * UNIT):
            self.assertTrue(self.run_up()["paused"])
        self.mode(rate=None)
        tok = yt.load_secret(yu._token_path())
        yt.revoke(tok)  # 사용자가 Google 계정에서 연결을 끊음 (또는 테스트 상태 7일)
        self.cancel.clear()
        yt_tok = yt.load_secret(yu._token_path())
        yt.save_secret(yu._token_path(), dict(yt_tok, expires_at=0))  # 새 access_token 이 필요하게
        r = yu.resume(yu.session_key(name, None), self.log, self.cancel)
        self.assertFalse(r["ok"])
        self.assertTrue(r["relogin"])
        st = yu.status()
        self.assertTrue(st["needsRelogin"])
        self.assertFalse(st["connected"])
        self.assertIn("다시 연결하기", st["reloginMsg"])
        self.assertIn("7일마다", st["reloginMsg"])  # 테스트 상태 앱 안내
        self.assertIn("다시 연결하기", yu.plan(name)["problems"][0])
        self.connect()
        self.assertFalse(yu.status()["needsRelogin"])
        r = yu.resume(yu.session_key(name, None), self.log, self.cancel)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.fake.snapshot()["insert_calls"], 1)

    def test_connection_lost_after_video_steps_wait_for_reconnect(self):
        """세션 주소로 영상은 끝까지 올라갔는데 연결이 끊김 → 썸네일·자막·재생목록은 '다시 연결한 뒤 마저 하기'(Google 을 더 두드리지 않음)."""
        name, k = self.ready()
        pl = yu.create_playlist("기본기", "public")["playlist"]
        self.mode(rate=150 * KB)
        threading.Timer(0.6, self.cancel.set).start()
        with mock.patch.object(yt, "CHUNK", 64 * UNIT):
            self.assertTrue(self.run_up(opts={"playlistId": pl["id"]})["paused"])
        self.mode(rate=None)
        yt.revoke(yt.load_secret(yu._token_path()))
        self.cancel.clear()
        before = self.fake.snapshot()["log"].count("token:refresh")
        r = yu.resume(yu.session_key(name, None), self.log, self.cancel)
        self.assertTrue(r["ok"], r)
        self.assertEqual({k2: v["state"] for k2, v in r["steps"].items()}, {"thumbnail": "todo", "captions": "todo", "playlist": "todo"})
        self.assertEqual(r["steps"]["captions"]["msg"], yu.RELOGIN_STEP)
        self.assertEqual(self.fake.snapshot()["log"].count("token:refresh"), before)  # 새 토큰을 받으려다 실패한 뒤로는 부르지 않음
        self.assertTrue(yu.status()["needsRelogin"])
        self.connect()
        r2 = yu.finish(r["videoId"], None, self.log, self.cancel)
        self.assertEqual({k2: v["state"] for k2, v in r2["steps"].items()}, {"thumbnail": "ok", "captions": "ok", "playlist": "ok"})
        self.assertEqual(self.video()["playlists"], [pl["id"]])
        self.assertEqual(self.fake.snapshot()["insert_calls"], 1)

    def test_api_disabled_and_no_channel_on_connect(self):
        yu.save_client(json.dumps({"installed": CLIENT}))
        self.mode(api_disabled=True)
        yu.start_login(self.log)
        for _ in range(200):
            if yu.LOGIN.snapshot()["state"] != "waiting":
                break
            time.sleep(0.02)
        self.assertEqual(yu.LOGIN.snapshot()["error"], yt.MSG["api_off"])
        self.assertFalse(yu.status()["connected"])
        self.assertEqual(self.fake.snapshot()["revoked"], 1)

    def test_channel_mismatch_needs_confirmation(self):
        yu.save_client(json.dumps({"installed": CLIENT}))
        self.mode(auto={"account": "acc-other"})
        yu.start_login(self.log)
        for _ in range(200):
            if yu.LOGIN.snapshot()["state"] != "waiting":
                break
            time.sleep(0.02)
        st = yu.status()
        self.assertIs(st["match"], False)
        name = self.add_video()
        self.kit(name)
        self.assertTrue(any("풋살사관학교' 채널이 아니에요" in p for p in yu.plan(name)["problems"]))
        yu.save_settings({"channelOk": fake_google.OTHER_CHANNEL})
        self.assertIs(yu.status()["match"], True)
        self.assertEqual(yu.plan(name)["problems"], [])


class PreflightTests(ServiceBase):
    def test_upload_files_for(self):
        """upload.files_for: 원본은 보관함 영상 + analysis 자막 · 편집본은 가장 최근에 내보낸 영상 + 옆의 .srt (키트 파일은 그대로)."""
        name = self.add_video()
        f = upload.files_for(name)
        self.assertEqual((f["video"], f["srt"], f["srtWhere"], f["format"]), (core.VIDEOS / name, core.adir(name) / "subtitles.srt", "analysis", None))
        (core.adir(name) / "subtitles.srt").unlink()
        self.assertIsNone(upload.files_for(name)["srt"])
        seq = self.add_edit(export=False)
        self.assertEqual((upload.files_for(name, seq)["video"], upload.files_for(name, seq)["format"]), (None, "long"))
        self.add_edit()
        stem = f"{core.adir(name).name}_롱폼 가편집"
        time.sleep(0.02)
        shutil.copy(self.media / "long.mp4", self.out / f"{stem} (2).mp4")  # 다시 내보냄 (자막 없이)
        f = upload.files_for(name, seq)
        self.assertEqual((f["video"].name, f["srt"], f["export"]), (f"{stem} (2).mp4", None, f"{stem} (2).mp4"))
        with self.assertRaises(LookupError):
            upload.files_for(name, "없는편집본")
        with self.assertRaises(FileNotFoundError):
            upload.files_for("없음.mp4")

    def test_no_kit_not_exported_and_not_connected(self):
        name = self.add_video()
        p = yu.plan(name)
        self.assertEqual(p["codes"][:2], ["kit", "setup"])
        seq = self.add_edit(export=False)
        self.kit(name, seq)
        self.connect()
        p = yu.plan(name, seq)
        self.assertEqual(p["codes"], ["export"])

    def test_title_description_bytes_tags(self):
        name, k = self.ready()
        upload.save_edits(name, None, title="가" * 101, desc="풋살 " * 10 + "가" * 1700, tags=", ".join(f"태그{i:03d} 풋살" for i in range(60)))
        p = yu.plan(name)
        joined = " / ".join(p["problems"])
        self.assertIn("101자", joined)
        self.assertIn("바이트", joined)
        self.assertIn("1,600자", joined)
        self.assertIn("태그가 합계", joined)
        self.assertEqual(set(p["codes"]), {"meta"})
        self.assertGreater(p["kit"]["descBytes"], 5000)
        self.assertLess(p["kit"]["descChars"], 5000)  # 글자 수로는 5000 안쪽 → 바이트로 막아야 함
        upload.save_edits(name, None, title="<좋은> 제목", desc="짧은 설명", tags="풋살")
        self.assertEqual(yu.plan(name)["problems"], ["제목에 < > 는 쓸 수 없어요"])
        r = self.run_up()
        self.assertFalse(r["ok"])
        self.assertEqual(self.fake.snapshot()["insert_calls"], 0)

    def test_warnings_shorts_length_srt_duplicate_audit(self):
        self.connect()
        name = self.add_video(kind="tall", srt=False)
        k = self.kit(name)
        self.assertEqual(k["format"], "shorts")
        self.add_thumb(name)
        p = yu.plan(name, None, "public")
        w = " / ".join(p["warnings"])
        self.assertIn("쇼츠 맞춤 썸네일", w)
        self.assertFalse(p["defaults"]["thumbnail"])  # 쇼츠는 썸네일을 기본으로 안 올림
        self.assertIn("자막 파일(.srt)이 없어서", w)
        self.assertIn("감사", w)
        self.assertEqual(p["costParts"], {"thumbnail": 50, "captions": 0, "playlist": 50, "check": 1})
        with mock.patch.object(yu, "_probe", return_value={"width": 1280, "height": 720, "duration": 1000.0}):
            w = " / ".join(yu.plan(name)["warnings"])
        self.assertIn("15분이 넘는", w)
        self.assertIn("긴 영상으로 올려요", w)
        r = self.run_up(name)
        self.assertTrue(r["ok"])
        self.assertEqual(r["steps"]["thumbnail"]["state"], "skip")
        p = yu.plan(name)
        self.assertEqual(p["duplicate"]["videoId"], r["videoId"])
        self.assertTrue(any("이미 올렸어요" in x for x in p["warnings"]))
        self.assertEqual(p["last"]["videoId"], r["videoId"])

    def test_kit_alerts_and_resume_note_after_kit_edit(self):
        name, k = self.ready()
        self.mode(rate=150 * KB)
        threading.Timer(0.6, self.cancel.set).start()
        with mock.patch.object(yt, "CHUNK", 64 * UNIT):
            self.assertTrue(self.run_up()["paused"])
        self.mode(rate=None)
        upload.save_edits(name, None, title="고친 제목")
        self.cancel.clear()
        r = yu.resume(yu.session_key(name, None), self.log, self.cancel)
        self.assertTrue(r["ok"])
        self.assertTrue(any("스튜디오에서 고쳐" in n for n in r["notes"]))
        self.assertEqual(self.video()["title"], k["title"])  # 이미 보낸 제목


class QuotaClockTests(unittest.TestCase):
    @staticmethod
    def ts(s):
        from datetime import datetime, timezone
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()

    def test_pacific_offsets_across_dst(self):
        self.assertEqual(yu._pt_offset(self.ts("2026-03-08 09:59")), -8)  # 3월 둘째 일요일 02:00 PST
        self.assertEqual(yu._pt_offset(self.ts("2026-03-08 10:00")), -7)
        self.assertEqual(yu._pt_offset(self.ts("2026-11-01 08:59")), -7)  # 11월 첫째 일요일 02:00 PDT
        self.assertEqual(yu._pt_offset(self.ts("2026-11-01 09:00")), -8)
        self.assertEqual(yu._pt_offset(self.ts("2027-03-14 10:00")), -7)
        self.assertEqual(yu.pt_day(self.ts("2026-10-07 06:59")), "2026-10-06")
        self.assertEqual(yu.pt_day(self.ts("2026-10-07 07:00")), "2026-10-07")
        self.assertEqual(yu.pt_day(self.ts("2026-12-01 07:59")), "2026-11-30")
        self.assertEqual(yu.next_reset(self.ts("2026-10-07 03:00")), self.ts("2026-10-07 07:00"))
        self.assertEqual(yu.next_reset(self.ts("2026-12-01 09:00")), self.ts("2026-12-02 08:00"))
        self.assertEqual(yu.next_reset(self.ts("2026-03-08 06:00")), self.ts("2026-03-08 08:00"))  # 아직 3월 7일 PST
        self.assertEqual(yu.next_reset(self.ts("2026-03-08 12:00")), self.ts("2026-03-09 07:00"))  # 그날 자정은 PST, 다음 자정은 PDT
        self.assertEqual(yu.next_reset(self.ts("2026-10-31 12:00")), self.ts("2026-11-01 07:00"))
        self.assertEqual(yu.next_reset(self.ts("2026-11-01 12:00")), self.ts("2026-11-02 08:00"))

    @unittest.skipUnless(hasattr(time, "tzset"), "시간대 바꾸기(tzset)가 없는 OS")
    def test_reset_text_in_korea(self):
        old = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"
        time.tzset()
        try:
            self.assertEqual(yu.reset_text(self.ts("2026-10-07 03:00")), "오늘 오후 4시")  # 여름: 16시
            self.assertEqual(yu.reset_text(self.ts("2026-10-07 08:00")), "내일 오후 4시")
            self.assertEqual(yu.reset_text(self.ts("2026-12-01 03:00")), "오늘 오후 5시")  # 겨울: 17시
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_counters_reset_each_pacific_day(self):
        tmp = Path(tempfile.mkdtemp(prefix="할당량 "))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(core, "WORK", tmp):
            (tmp / "youtube").mkdir()
            (tmp / "youtube" / "quota.json").write_text(json.dumps({"v": 1, "day": "2000-01-01", "uploads": 99, "units": 9999,
                                                                    "exhausted": {"units": True}}), encoding="utf-8")
            self.assertEqual(yu.quota_view()["used"], {"uploads": 0, "units": 0})  # 예전 날은 0부터
            for op in ("videos.insert", "captions.insert", "thumbnails.set", "videos.list", "playlists.list"):
                yu.charge(op)
            v = yu.quota_view()
            self.assertEqual(v["used"], {"uploads": 1, "units": 452})
            self.assertEqual(v["remaining"], {"uploads": 99, "units": 9548})
            yu._exhausted("captions.insert")
            self.assertEqual(yu.quota_view()["remaining"]["units"], 0)
            (tmp / "youtube" / "quota.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(yu.quota_view()["used"]["units"], 0)
            self.assertTrue((tmp / "youtube" / "quota.json").exists())  # 읽기는 아무것도 옮기지 않음
            yu.charge("videos.list")
            self.assertTrue((tmp / "youtube" / "quota.json.bad").exists())


class SmallRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="유튜브 규칙 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(core, "WORK", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def test_links_and_open_only_fixed_urls(self):
        self.assertEqual(yu.links("abcdefghijk"), {"url": "https://youtu.be/abcdefghijk", "studio": "https://studio.youtube.com/video/abcdefghijk/edit"})
        self.assertEqual(yu.links("abcdefghijk", True)["url"], "https://www.youtube.com/shorts/abcdefghijk")
        with mock.patch.object(yu.webbrowser, "open") as op:
            self.assertEqual(yu.open_link("studio", "abcdefghijk"), "https://studio.youtube.com/video/abcdefghijk/edit")
            self.assertEqual(yu.open_link("guide", key="audit"), "https://support.google.com/youtube/contact/yt_api_form")
            for what, vid, key in (("watch", "../../x", None), ("watch", "javascript:a", None), ("guide", None, "https://evil"),
                                   ("url", "abcdefghijk", None)):
                with self.assertRaises(yu.UploadError):
                    yu.open_link(what, vid, key)
            self.assertEqual(op.call_count, 2)
        for k, url in yu.GUIDE_URLS.items():
            self.assertTrue(url.startswith("https://"), k)

    def test_settings_whitelist(self):
        st = yu.save_settings({"privacy": "public", "evil": "<script>", "madeForKids": False, "consentMode": "production"})
        self.assertNotIn("evil", st)
        self.assertEqual((st["privacy"], st["consentMode"]), ("public", "production"))
        for bad in ({"privacy": "everyone"}, {"madeForKids": "no"}, {"playlistId": "../x"}, {"channelOk": "UC123"}):
            with self.assertRaises(yu.UploadError):
                yu.save_settings(bad)
        with self.assertRaises(yu.UploadError):
            yu.save_settings(None)
        (self.tmp / "youtube" / "settings.json").write_text('{"privacy": 3, "notify": false}', encoding="utf-8")
        self.assertEqual((yu.get_settings()["privacy"], yu.get_settings()["notify"]), ("private", False))

    def test_history_is_capped(self):
        d = {"v": 1, "items": [{"videoId": f"v{i:010d}", "at": i} for i in range(yu.HISTORY_MAX + 5)]}
        (self.tmp / "youtube").mkdir()
        (self.tmp / "youtube" / "history.json").write_text(json.dumps(d), encoding="utf-8")
        yu._history_put({"videoId": "NEWVIDEO_01", "at": 1})
        h = yu._history()
        self.assertEqual(len(h), yu.HISTORY_MAX)
        self.assertEqual(h[0]["videoId"], "NEWVIDEO_01")

    def test_remote_label_hook_is_optional(self):
        yu._remote_label()  # remote 모듈이 없어도 그냥 지나감
        fake = type(sys)("remote")
        fake.JOB_LABELS, fake.STOPPABLE = {"내보내기"}, {"내보내기"}
        with mock.patch.dict(sys.modules, {"remote": fake}):
            yu._remote_label()
        self.assertTrue({yu.JOB_NAME, yu.JOB_FINISH} <= fake.JOB_LABELS)
        self.assertTrue({yu.JOB_NAME, yu.JOB_FINISH} <= fake.STOPPABLE)


class RouteTests(ServiceBase):
    """app.py 의 /api/youtube/* (실제 HTTP 서버로)."""

    def setUp(self):
        super().setUp()
        import app
        from http.server import ThreadingHTTPServer
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.port = port
        for p in (mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log"),
                  mock.patch.object(app, "LOG", [])):
            p.start()
            self.addCleanup(p.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base_app = f"http://127.0.0.1:{port}"
        self.responses = []

    def call(self, path, body=None, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base_app + path, data=None if body is None else json.dumps(body).encode(), headers=h,
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode("utf-8")
                code = r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read().decode("utf-8"), e.code
        self.responses.append(raw)
        return code, json.loads(raw)

    def wait_job(self, timeout=60):
        for _ in range(int(timeout / 0.05)):
            if not self.app.JOB["name"]:
                return self.app.JOB["result"]
            time.sleep(0.05)
        self.fail("작업이 끝나지 않았어요")

    def test_host_origin_and_names(self):
        self.assertEqual(self.call("/api/youtube/status", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("/api/youtube/login", {}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("/api/youtube/plan?name=..%2Fx.mp4")[0], 404)
        self.assertEqual(self.call("/api/youtube/plan?name=" + urllib.parse.quote("없음.mp4"))[0], 404)
        self.assertEqual(self.call("/api/youtube/upload", {"name": "..\\x.mp4"})[0], 400)
        self.assertEqual(self.call("/api/youtube/upload", {"name": "없음.mp4"})[0], 404)
        self.assertEqual(self.call("/api/youtube/resume", {"key": "../x"})[0], 400)
        self.assertEqual(self.call("/api/youtube/finish", {"videoId": "x"})[0], 400)
        self.assertEqual(self.call("/api/youtube/discard", {"key": "zz"})[0], 400)
        self.assertEqual(self.call("/api/youtube/open", {"what": "guide", "key": "https://evil"})[0], 400)
        code, j = self.call("/api/youtube/open", {"what": "guide", "key": "clients"})
        self.assertEqual((code, self.opened[-1]), (200, yu.GUIDE_URLS["clients"]))
        self.assertEqual(self.call("/api/youtube/nothing", {})[0], 404)

    def test_get_routes_write_nothing(self):
        name, k = self.ready()
        self.call("/api/youtube/status")  # 처음 한 번 (studio.log 등)

        def snap():
            return sorted((str(p.relative_to(self.tmp)), p.stat().st_mtime_ns, p.stat().st_size) for p in self.tmp.rglob("*"))
        before = snap()
        for path in ("/api/youtube/status", "/api/youtube/plan?name=" + urllib.parse.quote(name), "/api/youtube/history",
                     "/api/youtube/plan?name=" + urllib.parse.quote(name) + "&privacy=public"):
            self.assertEqual(self.call(path)[0], 200, path)
        self.assertEqual(snap(), before)

    def test_client_status_never_echo_secrets(self):
        code, j = self.call("/api/youtube/client", {"json": json.dumps({"installed": CLIENT})})
        self.assertEqual((code, j["clientHint"]), (200, CLIENT["client_id"][:12] + "…"))
        code, j = self.call("/api/youtube/client", {"json": json.dumps({"web": CLIENT})})
        self.assertEqual(code, 400)
        self.assertIn("데스크톱 앱", j["error"])
        code, j = self.call("/api/youtube/client", {"client_id": CLIENT["client_id"], "client_secret": CLIENT["client_secret"]})
        self.assertEqual(code, 200)
        code, j = self.call("/api/youtube/login", {})
        self.assertEqual(code, 200)
        self.assertTrue(j["url"].startswith(self.base + "/o/oauth2/v2/auth?"))
        for _ in range(200):
            if self.call("/api/youtube/status")[1]["connected"]:
                break
            time.sleep(0.05)
        code, st = self.call("/api/youtube/status")
        self.assertTrue(st["connected"])
        self.assertEqual(st["channel"]["id"], fake_google.OWN_CHANNEL)
        self.assertIs(st["match"], True)
        sec = self.fake.secret_values()
        blob = "\n".join(self.responses)
        for v in [sec["client_secret"]] + sec["access"] + sec["refresh"] + sec["codes"]:
            self.assertNotIn(v, blob)
        code, j = self.call("/api/youtube/logout", {})
        self.assertFalse(self.call("/api/youtube/status")[1]["connected"])
        self.assertEqual(self.fake.snapshot()["revoked"], 1)
        code, j = self.call("/api/youtube/client", {"clear": True})
        self.assertFalse(self.call("/api/youtube/status")[1]["configured"])
        self.assertFalse(yu._client_path().exists())

    def test_upload_job_pause_busy_and_hygiene(self):
        name, k = self.ready()
        q = urllib.parse.quote(name)
        code, p = self.call(f"/api/youtube/plan?name={q}")
        self.assertEqual((code, p["problems"]), (200, []))
        code, j = self.call("/api/youtube/pause", {})
        self.assertEqual(code, 409)  # 우리 작업이 아니면 멈추지 않음
        hold = threading.Event()
        self.assertTrue(self.app.start_job("내보내기", hold.wait))
        try:
            code, j = self.call("/api/youtube/upload", {"name": name, "seq": "", "opts": {"privacy": "private", "madeForKids": False}})
            self.assertEqual(code, 409)
            self.assertEqual(self.call("/api/youtube/pause", {})[0], 409)
            self.assertFalse(editor.CANCEL.is_set())  # 내보내기를 멈추지 않음
        finally:
            hold.set()
        self.wait_job()
        self.mode(rate=150 * KB)
        with mock.patch.object(yt, "CHUNK", 64 * UNIT):
            code, j = self.call("/api/youtube/upload", {"name": name, "seq": "", "opts": {"privacy": "private", "madeForKids": False}})
            self.assertEqual((code, j), (200, {"ok": True}))
            time.sleep(0.6)
            self.assertEqual(self.call("/api/youtube/status")[1]["job"], yu.JOB_NAME)
            self.assertEqual(self.call("/api/youtube/pause", {})[0], 200)
            r = self.wait_job()
        self.assertTrue(r["paused"], r)
        self.mode(rate=None)
        st = self.call("/api/youtube/status")[1]
        self.assertEqual(st["pending"][0]["state"], "paused")
        self.assertEqual(st["last"]["paused"], True)
        code, j = self.call("/api/youtube/resume", {"key": st["pending"][0]["key"]})
        self.assertEqual(code, 200)
        r = self.wait_job()
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.call("/api/youtube/history")[1]["items"][0]["videoId"], r["videoId"])
        code, j = self.call("/api/youtube/finish", {"videoId": r["videoId"], "steps": ["captions"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.wait_job()["steps"]["captions"]["state"], "ok")
        # 시크릿 위생: 기록·작업 폴더·응답 어디에도 토큰·코드·보안 비밀번호·세션 주소가 없음 (사용자 폴더의 .bin 만 예외)
        sec = self.fake.secret_values()
        values = [sec["client_secret"]] + sec["access"] + sec["refresh"] + sec["codes"] + sec["upload_ids"]
        self.assertTrue(sec["upload_ids"] and sec["access"])
        texts = ["\n".join(self.app.LOG), "\n".join(self.responses), "\n".join(self.logs)]
        for f in self.tmp.rglob("*"):
            if f.is_file() and f.suffix != ".bin" and f.suffix not in (".mp4", ".jpg"):
                texts.append(f.read_text(encoding="utf-8", errors="replace"))
        self.assertTrue((self.work / "studio.log").exists())
        blob = "\n".join(texts)
        for v in values:
            self.assertNotIn(v, blob)
        self.assertNotIn(k["title"], "\n".join(self.app.LOG))  # 제목도 기록에 남기지 않음

    def test_upload_problems_are_400(self):
        self.connect()
        name = self.add_video()
        code, j = self.call("/api/youtube/upload", {"name": name, "opts": {}})
        self.assertEqual(code, 400)
        self.assertEqual(j["codes"], ["kit"])
        code, j = self.call("/api/youtube/settings", {"settings": {"privacy": "nope"}})
        self.assertEqual(code, 400)
        code, j = self.call("/api/youtube/settings", {"settings": {"audited": True}})
        self.assertEqual((code, j["settings"]["audited"]), (200, True))
        code, j = self.call("/api/youtube/playlists", {"create": {"title": "기본기"}})
        self.assertEqual(code, 200)
        code, j = self.call("/api/youtube/playlists", {})
        self.assertEqual([x["title"] for x in j["items"]], ["기본기"])
        self.mode(api_disabled=True)
        code, j = self.call("/api/youtube/playlists", {})
        self.assertEqual((code, j["kind"]), (502, "api_off"))


if __name__ == "__main__":
    unittest.main()
