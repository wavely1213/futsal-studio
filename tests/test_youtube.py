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



if __name__ == "__main__":
    unittest.main()
