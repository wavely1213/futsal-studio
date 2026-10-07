"""유튜브 바로 올리기 × E1(실패 카드·studio.log) 합침 시험 (D-049) — 가짜 Google(tests/fake_google.py)로만, 인터넷 없이.
- 올리기 작업의 실패 카드: 할당량·연결 끊김(토큰 만료·취소)·썸네일 막힘(403)·인터넷 → trouble.YT_CARDS 의 정해진 문장 + 할 일
- start_job(name, fn, by, ctx={"youtube": True}): 작업 밖으로 나온 예외도 같은 카드 (받기 쪽 '로그인 정보·주소' 안내가 아니게)
- 휴대폰(remote.job_hook): 카드 종류별 정해진 문장 (주소·번호·토큰·Google 원문 없음)
- 기록 한 길: studiolog.write·trace 가 remote.redact(Google 비밀 모양 포함)를 거침 · youtube_api 도 studiolog 로
저장소 폴더에서: python3 -m unittest tests.test_youtube_trouble
"""
import io
import json
import re
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote  # noqa: E402
import studiolog  # noqa: E402
import trouble  # noqa: E402
import youtube_api as yt  # noqa: E402
import youtube_upload as yu  # noqa: E402
import test_youtube as ty  # noqa: E402  (ServiceBase 만 물려받음 — 그 모듈의 시험은 여기서 다시 돌지 않음)
from remote_fixture import Clock, FakeNtfy, dev_env, make_home, pair_device, service  # noqa: E402

SECRETS = ("ya29.SECRETTOKEN", "1//REFRESHSECRET", "GOCSPX-CLIENTSECRET", "upload_id=SESSIONSECRET", "Bearer BEARERSECRET",
           "?code=4/LOGINCODE", "&state=STATESECRET",
           "refresh_token=RTSECRET")
BAD = ("SECRETTOKEN", "REFRESHSECRET", "CLIENTSECRET", "SESSIONSECRET", "BEARERSECRET", "LOGINCODE", "STATESECRET", "RTSECRET")
URLISH = re.compile(r"https?://|www\.|youtu\.?be|\.com\b|upload_id|ya29|GOCSPX|Bearer|1//")


class CardTableTests(unittest.TestCase):
    """정해진 문장 표 (trouble.YT_CARDS) 와 trouble.explain(youtube=True)."""

    def test_cards_are_fixed_plain_sentences_without_urls(self):
        for kind, (msg, actions) in trouble.YT_CARDS.items():
            self.assertTrue(kind.startswith("yt_"), kind)
            self.assertIsNone(URLISH.search(msg), (kind, msg))
            self.assertNotRegex(msg, r"\{|\}|%s")  # 채울 자리 없음 (바뀌는 값을 넣지 않음)
            self.assertTrue(trouble.HANGUL.search(msg))
            self.assertTrue(set(actions) <= {"resume", "relogin", "verify", "thumb", "finish", "log", "logfile"}, kind)
            self.assertEqual(trouble.youtube_kind_of(msg), kind)
        for gkind, card in trouble.YT_KIND.items():
            self.assertIn(card, trouble.YT_CARDS, gkind)
        self.assertIsNone(trouble.youtube_card("file"))  # 설정·고르기 문제는 카드 없이 그 자리에서
        self.assertIsNone(trouble.youtube_kind_of("아무 글"))

    def test_api_errors_map_to_the_four_asked_cards(self):
        cases = {"quota": "yt_quota", "relogin": "yt_relogin", "thumb_verify": "yt_thumb", "network": "yt_net",
                 "network_long": "yt_net", "upload_limit": "yt_limit", "forbidden": "yt_forbidden", "server": "yt_server",
                 "rate": "yt_quota"}
        for gkind, card in cases.items():
            info = trouble.explain(yt.ApiError(gkind), youtube=True)
            self.assertEqual(info["kind"], card, gkind)
            self.assertEqual(info["msg"], trouble.YT_CARDS[card][0])
        self.assertEqual(trouble.explain(yt.ApiError("thumb_verify"), youtube=True)["actions"], ["verify", "thumb"])
        self.assertEqual(trouble.explain(yt.Cancelled(), youtube=True)["kind"], "cancelled")
        other = trouble.explain(yt.ApiError("unknown", reason="weirdReason ya29.SECRETTOKEN"), youtube=True)
        self.assertEqual(other["kind"], "yt_other")
        self.assertNotIn("weird", other["msg"])

    def test_plain_exceptions_get_upload_wording_not_download_wording(self):
        def k(e):
            return trouble.explain(e, youtube=True)
        self.assertEqual(k(OSError("<urlopen error [Errno -3] Temporary failure in name resolution>"))["kind"], "yt_net")
        self.assertEqual(k(RuntimeError("HTTP Error 503: Service Unavailable"))["kind"], "yt_server")
        for raw in ("HTTP Error 403: Forbidden", "Sign in to confirm", "HTTP Error 404: Not Found", "Unsupported URL: x"):
            info = k(RuntimeError(raw))
            self.assertEqual(info["kind"], "yt_other", raw)  # 받기 쪽 '로그인 정보로 다시 받기'·'주소 다시 넣기'가 아님
            self.assertFalse({"cookies", "url", "nocookies"} & set(info["actions"]))
        self.assertEqual(k(OSError(28, "No space left on device"))["kind"], "disk")  # PC 쪽 원인은 그대로
        self.assertEqual(k(MemoryError())["kind"], "memory")
        ours = k(yu.UploadError("먼저 Google 설정을 해 주세요 · [설정 안내 열기] 7단계"))  # 이미 우리 한국어 안내 → 그대로
        self.assertEqual((ours["kind"], ours["msg"]), ("other", "먼저 Google 설정을 해 주세요"))
        leaky = k(RuntimeError("boom " + " ".join(SECRETS) + " https://www.googleapis.com/upload?upload_id=X"))
        self.assertEqual(leaky["kind"], "yt_other")
        self.assertIsNone(URLISH.search(leaky["msg"]))
        # youtube 가 아니면 예전 그대로 (받기 쪽 규칙)
        self.assertEqual(trouble.explain(RuntimeError("HTTP Error 403: Forbidden"))["kind"], "blocked")


class LogPathTests(unittest.TestCase):
    """기록은 한 길: studiolog.write·trace → remote.redact (Google 비밀 모양 포함)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="기록 시험 "))
        self.log = self.tmp / "studio.log"
        old = studiolog._PATH[0]
        studiolog.setup(lambda: self.log)
        self.addCleanup(lambda: studiolog.setup(old))

    def text(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_write_and_trace_redact_google_secrets(self):
        studiolog.write("유튜브 · " + " ".join(SECRETS) + ' {"refresh_token": "1//X", "uri": "https://u?upload_id=Y"}')
        err = io.StringIO()
        try:
            raise RuntimeError("실패 " + " ".join(SECRETS))
        except RuntimeError as e:
            with mock.patch.object(sys, "stderr", err):
                studiolog.trace(e, "유튜브 시험 오류 위치")
        blob = self.text() + err.getvalue()
        for bad in BAD:
            self.assertNotIn(bad, blob, bad)
        self.assertIn("유튜브 시험 오류 위치 · RuntimeError", self.text())
        self.assertIn("ya29.…", blob)

    def test_trace_without_detail_keeps_only_type_and_place(self):
        err = io.StringIO()
        try:
            v = "WEIRD" + "SHAPE-not-a-known-secret"  # 실행 중에 생긴 값 (traceback 의 코드 줄에는 안 보임)
            raise ValueError(v)
        except ValueError as e:
            with mock.patch.object(sys, "stderr", err):
                studiolog.trace(e, "유튜브 연결 오류 위치", detail=False)
        self.assertNotIn("WEIRDSHAPE", self.text() + err.getvalue())
        self.assertIn("ValueError: " + studiolog.HIDDEN, self.text())
        self.assertIn("test_youtube_trouble.py", self.text())
        self.assertIn("Traceback", err.getvalue())

    def test_redact_dict_repr_colon_form_and_login_code(self):
        """예외 글에 요청 값·응답을 통째로 찍은 꼴(파이썬 사전 repr · '키: 값') · 주소 밖 로그인 코드(4/0…)도 지움."""
        cases = ["{'access_token': 'zzzSECRET', 'refresh_token': 'RTSECRET'}", "access_token: zzzSECRET",
                 "client_secret : CLIENTSECRET", "{'code': '4/0AbcLOGINCODE_x-y', 'client_secret': 'CLIENTSECRET'}",
                 "code=4/0AbcLOGINCODE_x-y", '{"code_verifier": "VERIFSECRET"}']
        for c in cases:
            out = remote.redact(c)
            for bad in ("zzzSECRET", "RTSECRET", "CLIENTSECRET", "LOGINCODE", "VERIFSECRET"):
                self.assertNotIn(bad, out, c)
        self.assertEqual(remote.redact("{'access_token': 'zzzSECRET'}"), "{'access_token': '…'}")
        self.assertEqual(remote.redact("code=4/0AbcLOGINCODE_x"), "code=4/0…")
        self.assertEqual(remote.redact("4/0 경기 · 1/2 쪽"), "4/0 경기 · 1/2 쪽")  # 짧은 숫자 꼴은 그대로

    def test_login_flow_unexpected_error_goes_through_studiolog_without_text(self):
        flow = yt.LoginFlow()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err), mock.patch.object(yt, "exchange_code", side_effect=KeyError("ya29.WEIRD")):
            got = []
            url = flow.start({"client_id": "id.apps.googleusercontent.com", "client_secret": "GOCSPX-x"}, lambda tok: got.append(tok),
                             timeout=10)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            redirect, state = q["redirect_uri"][0], q["state"][0]
            try:
                urllib.request.urlopen(f"{redirect}?state={state}&code=4/abc&scope={yt.SCOPE}", timeout=10).read()
            except urllib.error.HTTPError:
                pass
            for _ in range(200):
                if flow.snapshot()["state"] != "waiting":
                    break
                time.sleep(0.02)
        self.assertEqual(flow.snapshot()["state"], "error")
        self.assertIn("유튜브 연결 오류 위치 · KeyError: " + studiolog.HIDDEN, self.text())
        self.assertNotIn("WEIRD", self.text() + err.getvalue())

    def test_no_second_trace_path_in_upload_code(self):
        for name in ("app.py", "youtube_upload.py", "youtube_api.py", "remote.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("format_exc(", src, name)
            self.assertNotIn("print_exc(", src, name)
            self.assertNotIn("format_tb(", src, name)
        self.assertIn("import studiolog", (ROOT / "youtube_api.py").read_text(encoding="utf-8"))


class UploadCardTests(ty.ServiceBase):
    """실제 올리기 흐름(가짜 Google)에서 결과의 fail 카드."""

    def assertCard(self, r, kind):
        self.assertIsInstance(r.get("fail"), dict, r)
        self.assertEqual(r["fail"]["kind"], kind)
        self.assertEqual(r["fail"]["msg"], trouble.YT_CARDS[kind][0])
        self.assertIsNone(URLISH.search(json.dumps(r["fail"], ensure_ascii=False)))

    def test_quota_on_insert_gives_quota_card_and_keeps_session(self):
        name, _ = self.ready()
        self.mode(quota_exhausted_at="videos.insert")
        r = self.run_up()
        self.assertFalse(r["ok"])
        self.assertCard(r, "yt_quota")
        self.assertEqual(r["fail"]["actions"], ["resume"])
        self.assertEqual(r["key"], yu.session_key(name, None))  # 카드의 [이어 올리기]가 쓸 열쇠
        self.assertEqual(yu.status()["last"]["fail"]["kind"], "yt_quota")  # 7단계가 끝난 뒤 읽는 곳
        self.mode(quota_exhausted_at=None, upload_limit=True)
        self.assertCard(self.run_up(), "yt_limit")

    def test_expired_token_gives_relogin_card(self):
        name, _ = self.ready()
        tok = yt.load_secret(yu._token_path())
        yt.revoke(tok)  # 테스트 상태 7일·사용자가 끊음 → refresh 가 invalid_grant
        yt.save_secret(yu._token_path(), dict(yt.load_secret(yu._token_path()), expires_at=0))
        r = self.run_up()
        self.assertFalse(r["ok"])
        self.assertTrue(r["relogin"])
        self.assertCard(r, "yt_relogin")
        self.assertEqual(r["fail"]["actions"], ["relogin", "resume"])
        r2 = yu.resume(r["key"], self.log, self.cancel)  # 다시 연결하기 전 [이어 올리기] → 막히고 같은 카드
        self.assertCard(r2, "yt_relogin")

    def test_forbidden_thumbnail_gives_thumb_card_then_clears(self):
        self.ready()
        self.mode(thumb_forbidden=True)
        r = self.run_up()
        self.assertTrue(r["ok"])
        self.assertCard(r, "yt_thumb")
        self.assertEqual(r["fail"]["actions"], ["verify", "thumb"])
        self.assertTrue(yu.VIDEO_ID.match(r["videoId"]))  # 카드의 [썸네일 다시 올리기]가 쓸 영상
        self.mode(thumb_forbidden=False)
        r2 = yu.finish(r["videoId"], ["thumbnail"], self.log, self.cancel)
        self.assertTrue(r2["ok"])
        self.assertIsNone(r2["fail"])

    def test_quota_on_captions_gives_finish_card(self):
        self.ready()
        self.mode(quota_exhausted_at="captions.insert")
        r = self.run_up()
        self.assertTrue(r["ok"])
        self.assertCard(r, "yt_quota_left")
        self.assertEqual(r["fail"]["actions"], ["finish"])

    def test_network_gone_gives_network_card(self):
        self.ready()
        real = yt._send

        def down(method, url, *a, **kw):
            if method == "PUT":
                raise yt._Net("down")
            return real(method, url, *a, **kw)

        with mock.patch.object(yt, "_send", side_effect=down), mock.patch.object(yt, "BACKOFF", (0.001,) * 3):
            r = self.run_up()
        self.assertFalse(r["ok"])
        self.assertIn(r["kind"], ("network", "network_long"))
        self.assertCard(r, "yt_net")
        self.assertEqual(r["fail"]["actions"], ["resume"])
        self.assertEqual([x["state"] for x in yu.pending()], ["failed"])

    def test_paused_has_no_card(self):
        self.ready()
        self.mode(rate=150 * ty.KB)
        threading.Timer(0.5, self.cancel.set).start()
        with mock.patch.object(yt, "CHUNK", 64 * ty.UNIT):
            r = self.run_up()
        self.assertTrue(r["paused"])
        self.assertIsNone(r["fail"])


class StartJobCardTests(ty.ServiceBase):
    """app.start_job(…, ctx={"youtube": True}) — /api/youtube/* 로 시킨 작업에서 예외가 나와도 7단계 카드 · studio.log 는 비밀 없이."""

    def setUp(self):
        super().setUp()
        import app
        from http.server import ThreadingHTTPServer
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        for p in (mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log"),
                  mock.patch.object(app, "LOG", [])):
            p.start()
            self.addCleanup(p.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base_app = f"http://127.0.0.1:{port}"
        self.hooked = []
        hook = lambda *a: self.hooked.append(a)  # noqa: E731
        app.JOB_HOOKS.append(hook)
        self.addCleanup(app.JOB_HOOKS.remove, hook)

    def call(self, path, body=None):
        req = urllib.request.Request(self.base_app + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def wait_job(self, timeout=60):
        for _ in range(int(timeout / 0.05)):
            if not self.app.JOB["name"]:
                return
            time.sleep(0.05)
        self.fail("작업이 끝나지 않았어요")

    def run_crash(self, exc, path="/api/youtube/upload", body=None, target="run_upload"):
        name, _ = self.ready()
        with mock.patch.object(yu, target, side_effect=exc):
            code, j = self.call(path, body or {"name": name, "opts": {"privacy": "private", "madeForKids": False}})
            self.assertEqual(code, 200, j)
            self.wait_job()
        code, st = self.call(f"/api/youtube/status?job={j['jobId']}")
        self.assertEqual(code, 200)
        return st["done"], (self.work / "studio.log").read_text(encoding="utf-8")

    def test_start_job_signature_passes_youtube_ctx(self):
        seen = []
        real = self.app.start_job

        def spy(name, fn, by=None, ctx=None):
            seen.append((name, by, ctx))
            return real(name, fn, by, ctx)

        name, _ = self.ready()
        with mock.patch.object(self.app, "start_job", side_effect=spy), mock.patch.object(yu, "run_upload", return_value={"ok": True}):
            self.call("/api/youtube/upload", {"name": name, "opts": {"privacy": "private", "madeForKids": False}})
            self.wait_job()
        self.assertEqual(seen, [(yu.JOB_NAME, None, {"youtube": True})])

    def test_unexpected_error_gets_other_card_and_redacted_trace(self):
        done, log = self.run_crash(RuntimeError("boom " + " ".join(SECRETS)))
        self.assertEqual(done["fail"]["kind"], "yt_other")
        self.assertEqual(done["fail"]["job"], yu.JOB_NAME)
        self.assertEqual(done["error"], trouble.YT_CARDS["yt_other"][0])
        self.assertIn("오류 위치 · RuntimeError", log)  # 프로그램 오류 → 위치 한 줄
        for bad in BAD:
            self.assertNotIn(bad, log + json.dumps(done, ensure_ascii=False), bad)
        self.assertEqual(self.hooked[-1][1], trouble.YT_CARDS["yt_other"][0])  # 휴대폰 훅도 정해진 문장

    def test_api_error_escaping_gets_its_card_without_trace(self):
        done, log = self.run_crash(yt.ApiError("quota"))
        self.assertEqual(done["fail"]["kind"], "yt_quota")
        self.assertNotIn("오류 위치", log)  # Google 이 알려 준 사정 (EXPECTED_KINDS) → 원문 줄만
        self.assertIn("원문 · ", log)

    def test_youtube_crash_logs_no_error_text_even_for_unknown_secret_shapes(self):
        """토큰 새로 받기·저장 중 오류 글엔 redact 가 모르는 비밀이 섞일 수 있음 → 유튜브 작업은 원문 글 없이 종류·위치만."""
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            done, log = self.run_crash(RuntimeError("token save failed WEIRDTOKENshape-123"))
        self.assertEqual(done["fail"]["kind"], "yt_other")
        self.assertNotIn("WEIRDTOKEN", log + err.getvalue())
        self.assertIn("원문 · RuntimeError", log)
        self.assertIn("RuntimeError: " + studiolog.HIDDEN, log)
        self.assertIn("Traceback", err.getvalue())
        # 다른 작업(받기·내보내기…)은 원문 그대로 (E1 그대로)
        self.app.start_job("내보내기", lambda: (_ for _ in ()).throw(RuntimeError("EXPORTDETAIL kept")))
        self.wait_job()
        self.assertIn("EXPORTDETAIL kept", (self.work / "studio.log").read_text(encoding="utf-8"))

    def test_cancel_escaping_job_is_paused_not_failed(self):
        done, _log = self.run_crash(yt.Cancelled())
        self.assertEqual(done["fail"]["kind"], "cancelled")
        self.assertTrue(trouble.CANCELLED.search(self.hooked[-1][1]))

    def test_finish_crash_from_network(self):
        name, _ = self.ready()
        with mock.patch.object(yu, "finish", side_effect=OSError("<urlopen error timed out>")):
            code, j = self.call("/api/youtube/finish", {"videoId": "AbCdEfGhIjK"})
            self.wait_job()
        code, st = self.call(f"/api/youtube/status?job={j['jobId']}")
        self.assertEqual(st["done"]["fail"]["kind"], "yt_net")

    def test_status_done_only_for_youtube_jobs(self):
        self.ready()
        ok = self.app.start_job("내보내기", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.wait_job()
        code, st = self.call(f"/api/youtube/status?job={ok}")
        self.assertIsNone(st["done"])  # 다른 작업의 결과는 7단계로 가지 않음
        self.assertIsNone(self.call("/api/youtube/status?job=abc")[1]["done"])


class PhoneTests(unittest.TestCase):
    """휴대폰 '마지막 작업' 칸·알림: 카드 종류별 정해진 문장."""

    def setUp(self):
        self.home, cleanup = make_home()
        self.addCleanup(cleanup)
        self.ntfy = FakeNtfy()
        self.addCleanup(self.ntfy.close)
        p = mock.patch.dict("os.environ", dev_env())
        p.start()
        self.addCleanup(p.stop)
        self.svc, _ = service(self.home, Clock(time.time()), ntfy=self.ntfy.url)

    def hook(self, name, error, result, secs=90):
        self.svc.job_hook(name, error, result, None, secs)
        return self.svc.last

    def test_card_kinds_map_to_fixed_phone_sentences(self):
        url, tok = "https://youtu.be/AbCdEfGhIjK", "ya29.SECRETTOKEN"
        card = trouble.youtube_card
        cases = [
            ({"ok": False, "kind": "quota", "error": f"할당량 {url}", "key": "k", "fail": card("quota")}, "quota", False),
            ({"ok": False, "kind": "upload_limit", "error": "x", "fail": card("upload_limit")}, "quota", False),
            ({"ok": False, "relogin": True, "error": f"다시 연결 {tok}", "fail": card("relogin")}, "relogin", False),
            ({"ok": False, "kind": "network", "error": "끊김", "fail": card("network")}, "net", False),
            ({"ok": False, "kind": "server", "error": "5xx", "fail": card("server")}, "net", False),
            ({"ok": True, "videoId": "AbCdEfGhIjK", "url": url, "warnings": [], "fail": card("yt_thumb")}, "thumb", True),
            ({"ok": True, "videoId": "AbCdEfGhIjK", "url": url, "warnings": [], "fail": card("yt_quota_left")}, "quota_left", True),
            ({"ok": True, "videoId": "AbCdEfGhIjK", "url": url, "warnings": [], "fail": card("yt_relogin_left")}, "relogin", True),
            ({"ok": False, "paused": True, "error": "멈춤", "fail": None}, "paused", False),
        ]
        for res, key, warn in cases:
            last = self.hook(yu.JOB_NAME, None, res)
            self.assertEqual((last["error"], last["warn"]), (remote.YOUTUBE_MSG[key], warn), key)
            self.assertIsNone(URLISH.search(json.dumps(last, ensure_ascii=False)), key)
        # 작업 밖으로 나온 예외: start_job 이 만든 카드 문장(error) → 그 종류의 휴대폰 문장
        self.assertEqual(self.hook(yu.JOB_NAME, trouble.YT_CARDS["yt_quota"][0], None)["error"], remote.YOUTUBE_MSG["quota"])
        self.assertEqual(self.hook(yu.JOB_FINISH, trouble.YT_CARDS["yt_net"][0], None)["error"], remote.YOUTUBE_MSG["net"])
        self.assertEqual(self.hook(yu.JOB_NAME, f"boom {tok}", None)["error"], remote.YOUTUBE_MSG["failed"])
        for k in ("quota", "net", "thumb", "quota_left"):
            self.assertIsNone(URLISH.search(remote.YOUTUBE_MSG[k]), k)

    def test_cancel_escaping_job_is_quiet_paused(self):
        """멈춤(yt.Cancelled)이 작업 밖으로 예외로 나와도(start_job 의 cancelled 안내) '멈췄어요' · 실패 알림 안 울림."""
        msg = trouble.explain(yt.Cancelled(), youtube=True)["msg"]
        with mock.patch.object(self.svc, "pub") as pub, mock.patch.object(self.svc, "state", "on"), \
                mock.patch.dict(self.svc.store.data, {"devices": {"d": {}}}):
            last = self.hook(yu.JOB_NAME, msg, None)
            self.assertEqual(last["error"], remote.YOUTUBE_MSG["paused"])
            pub.notify.assert_not_called()
            self.hook(yu.JOB_NAME, "boom", None)  # 카드도 멈춤도 아닌 글 → 실패 알림 (대조)
            self.assertEqual(pub.notify.call_args[0][:2], ("failed", remote.YOUTUBE_MSG["failed"]))
        # 인터넷 끊김 카드(문장에 '멈췄어요'가 있어도)는 멈춤이 아니라 인터넷 문장
        self.assertEqual(self.hook(yu.JOB_NAME, trouble.YT_CARDS["yt_net"][0], None)["error"], remote.YOUTUBE_MSG["net"])

    def test_thumb_card_on_success_is_attention_not_done(self):
        """예전엔 썸네일만 막히면(경고 없음) '올렸어요'로 알렸음 → 이제 '확인이 필요해요' 알림."""
        pair_device(self.svc, "폰 A")
        self.assertTrue(self.svc.turn_on())
        self.addCleanup(lambda: self.svc.state != "off" and self.svc.turn_off())
        end = time.time() + 10
        while self.svc.state != "on" and time.time() < end:
            time.sleep(0.02)
        t = self.svc.store.data["topics"]["notify"]
        time.sleep(0.5)  # 짝짓기 알림이 먼저 가게
        before = len(self.ntfy.topic(t))
        self.hook(yu.JOB_NAME, None, {"ok": True, "videoId": "AbCdEfGhIjK", "warnings": [], "fail": trouble.youtube_card("yt_thumb")})
        got = []
        end = time.time() + 8
        while time.time() < end and not got:
            got = self.ntfy.topic(t)[before:]
            time.sleep(0.03)
        self.assertEqual([p[1] for p in got], [remote.YOUTUBE_MSG["thumb"]])


if __name__ == "__main__":
    unittest.main()
