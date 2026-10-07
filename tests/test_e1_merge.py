"""E1 묶음 × 휴대폰으로 보기·Windows 대비 합침 시험 (D-044) — 저장소 폴더에서 python3 -m unittest tests.test_e1_merge

- 오류 기록 하나: studio.log 에 위치 한 줄 · 오류 출력(pythonw 면 studio-error.log)에 traceback 한 벌 · 둘 다 비밀 없음
  (작업 실패 · 서버 요청 오류 → handle_error 는 다시 안 찍음 · 스레드 오류 · 원격 _trace · 작업 끝 알림 오류)
- 작업 번호(jobId) 결과에 실패 안내(fail) · 휴대폰 '마지막 작업'은 쉬운 한 줄 · PC 받기 {failed, why} · 편집점 찾기 일부 실패
- 휴대폰에서 시킨 받기가 막히면 PC 에서 할 일 문구 · 창 닫기 확인 '예' → 기록 · 다시 시작 실패는 실행 표시를 지우지 않음
- intake.convert 는 core.run/core.popen(Job Object) + replace_retry · file_sig.json 은 write_atomic
- 로그인 정보 브라우저(/api/browsers·cookies)는 휴대폰에서 닿지 않음
"""
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402
import core  # noqa: E402
import intake  # noqa: E402
import remote  # noqa: E402
import studiolog  # noqa: E402
import trouble  # noqa: E402
import updater  # noqa: E402
from test_remote_api import Base as RemoteBase  # noqa: E402

TICKET = "AbCdEf0123456789_-zyXW"
TOPIC = "fsn" + "0a1b2c3d4e5f" * 2
URL = "https://quiet-river-blue-mango.trycloudflare.com"
SECRETS = (TICKET, TOPIC, "quiet-river-blue-mango")
RAW = f"HTTP Error 404 · 표 /r/m/{TICKET} · 주제 {TOPIC} · {URL}/r/status"


class LogBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="합침 시험 "))
        self.log = self.tmp / "studio.log"
        old = studiolog._PATH[0]
        studiolog.setup(lambda: self.log)
        self.addCleanup(lambda: studiolog._PATH.__setitem__(0, old))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        studiolog._SESSION.clear()
        self.addCleanup(studiolog._SESSION.clear)
        self.err = io.StringIO()
        p = mock.patch.object(sys, "stderr", app._StampedErr(self.err))  # pythonw 흉내: 오류 출력 = studio-error.log
        p.start()
        self.addCleanup(p.stop)

    def text(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def assertClean(self, s):
        for x in SECRETS:
            self.assertNotIn(x, s)


def _wait_idle(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if not app.JOB["name"]:
            return True
        time.sleep(0.02)
    return False


class ErrorLogTests(LogBase):
    def test_job_failure_one_location_one_traceback_no_secrets(self):
        hooks = []
        with mock.patch.object(app, "JOB_HOOKS", [lambda *a: hooks.append(a)]), mock.patch.object(app, "log", lambda m: None):
            def fail():
                raise RuntimeError(RAW)
            jid = app.start_job("채널 불러오기", fail, by="휴대폰 · 시험")
            self.assertTrue(jid)
            self.assertTrue(_wait_idle())
            for _ in range(100):
                if hooks:
                    break
                time.sleep(0.02)
        done = app.DONE[jid]
        self.assertEqual(done["fail"]["kind"], "url", "작업 번호로 받는 결과에 실패 안내")
        self.assertEqual(done["error"], done["fail"]["msg"])
        self.assertNotIn("HTTP Error", done["error"], "화면·휴대폰에는 쉬운 한 줄만")
        self.assertEqual(hooks[0][1], done["error"], "휴대폰 job_hook 도 같은 쉬운 한 줄")
        log = self.text()
        self.assertIn("원문 · HTTP Error 404", log)
        self.assertEqual(log.count("오류 위치 · RuntimeError"), 1)
        self.assertClean(log)
        err = self.err.getvalue()
        self.assertEqual(err.count("Traceback (most recent call last)"), 1, "traceback 은 한 벌")
        self.assertClean(err)

    def test_request_error_traced_once_then_handle_error_skips(self):
        try:
            raise KeyError(f"/r/m/{TICKET}")
        except KeyError as e:
            studiolog.trace(e, "요청 오류 · POST /api/x")
            srv = object.__new__(app._Server)
            srv.handle_error(None, ("127.0.0.1", 50123))  # socketserver 가 이어서 부름
        self.assertEqual(self.text().count("요청 오류"), 1)
        err = self.err.getvalue()
        self.assertEqual(err.count("Traceback"), 1, "handle_error 가 같은 오류를 다시 찍지 않음")
        self.assertNotIn("50123", err)
        self.assertClean(err + self.text())

    def test_handle_error_alone_traces_and_ignores_hangups(self):
        srv = object.__new__(app._Server)
        try:
            raise ValueError(URL)
        except ValueError:
            srv.handle_error(None, ("127.0.0.1", 1))
        try:
            raise ConnectionAbortedError(10053, "끊김")
        except ConnectionAbortedError:
            srv.handle_error(None, ("127.0.0.1", 1))
        self.assertEqual(self.text().count("요청 오류"), 1, "화면이 끊은 연결은 오류 아님")
        self.assertIn("ValueError", self.err.getvalue())
        self.assertClean(self.err.getvalue() + self.text())

    def test_thread_error_default_hook_replaced_with_redacted_traceback(self):
        hooked = list(studiolog._HOOKED)
        prev = threading.excepthook, sys.excepthook
        try:
            studiolog._HOOKED.clear()
            threading.excepthook = threading.__excepthook__
            studiolog.install_hooks()

            def bad():
                raise RuntimeError(RAW)
            t = threading.Thread(target=bad, name="뒤에서 비콘")
            t.start()
            t.join()
        finally:
            threading.excepthook, sys.excepthook = prev
            studiolog._HOOKED[:] = hooked
        self.assertIn("뒤에서 하던 일(뒤에서 비콘)", self.text())
        err = self.err.getvalue()
        self.assertEqual(err.count("Traceback"), 1)
        self.assertIn("Exception in thread 뒤에서 비콘", err)
        self.assertClean(err + self.text())

    def test_remote_trace_goes_through_studiolog(self):
        try:
            raise OSError(f"터널 {URL} 주제 {TOPIC}")
        except OSError:
            remote._trace()
        self.assertIn("휴대폰 연결 오류 위치 · OSError", self.text())
        self.assertIn("OSError", self.err.getvalue())
        self.assertClean(self.err.getvalue() + self.text())

    def test_every_studio_log_line_is_redacted(self):
        studiolog.write(f"원문 · {RAW}")
        updater_line = self.text()
        self.assertIn("…", updater_line)
        self.assertClean(updater_line)

    def test_stamped_err_redacts_any_write(self):
        sys.stderr.write(f"직접 쓴 글 {URL}/r/m/{TICKET}\n")
        self.assertClean(self.err.getvalue())
        self.assertRegex(self.err.getvalue(), r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2} 직접 쓴 글")

    def test_job_hook_error_is_traced_not_printed_raw(self):
        def bad_hook(*a):
            raise RuntimeError(f"알림 실패 {TOPIC}")
        with mock.patch.object(app, "JOB_HOOKS", [bad_hook]), mock.patch.object(app, "log", lambda m: None):
            app.start_job("시험", lambda: None)
            self.assertTrue(_wait_idle())
            time.sleep(0.2)
        self.assertIn("작업 끝 알림 오류 위치 · RuntimeError", self.text())
        self.assertClean(self.err.getvalue() + self.text())

    def test_updater_studio_log_has_year(self):
        with mock.patch.object(updater, "workspace", return_value=self.tmp):
            updater.studio_log(self.tmp, "실행기 줄")
        self.assertRegex(self.text(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} 실행기 줄")


class PhoneTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from remote_fixture import make_home, service
        self.home, clean = make_home()
        self.addCleanup(clean)
        self.svc, _ = service(self.home)

    def test_plain_line_for_phone_job_failure(self):
        info = trouble.explain(RuntimeError("ERROR: [youtube] x: Video unavailable. This video is private"))
        self.svc.job_hook("보관함에 담기", info["msg"], None, "휴대폰 · x", 10)
        self.assertEqual(self.svc.last["error"], info["msg"])
        self.assertNotIn("ERROR", self.svc.last["error"])

    def test_pc_download_result_with_why_is_attention(self):
        self.svc.job_hook("보관함에 담기", None, {"failed": ["AbCdEfGhIjK"], "why": {"AbCdEfGhIjK": {"msg": "막힘"}}}, None, 30)
        self.assertEqual((self.svc.last["ok"], self.svc.last["warn"]), (False, True))
        self.assertEqual(self.svc.last["error"], remote.MISSED_MSG.format(n=1))
        self.assertNotIn("크롬 로그인 정보로 받기", remote.MISSED_MSG + remote.BLOCKED_PHONE_MSG + core.REMOTE_BLOCKED_MSG)
        self.svc.job_hook("보관함에 담기", None, {"failed": [], "why": {}}, None, 30)
        self.assertEqual((self.svc.last["ok"], self.svc.last["warn"]), (True, False))

    def test_partial_analyze_is_attention_with_plain_reason(self):
        why = {"깨진.mp4": trouble.explain(core.FileProblem("'깨진.mp4'에서 소리를 꺼내지 못했어요 · moov atom not found"))}
        self.svc.job_hook("편집점 찾기", None, {"done": ["a"], "failed": ["깨진.mp4"], "why": why}, "휴대폰 · x", 30)
        last = self.svc.last
        self.assertEqual((last["ok"], last["warn"]), (False, True))
        self.assertTrue(last["error"].startswith("편집점을 찾지 못한 영상이 1개 있어요 · "), last["error"])
        self.assertNotIn("moov", last["error"])


class CoreTests(LogBase):
    def test_phone_download_blocked_points_to_pc(self):
        logs, why = [], {}
        with core.no_self_update():
            core._failed_one("AbCdEfGhIjK", RuntimeError("ERROR: Sign in to confirm you're not a bot"), logs.append, None, None, why)
        self.assertEqual(why["AbCdEfGhIjK"]["msg"], " ".join(core.REMOTE_BLOCKED_MSG.split()))
        self.assertIn("원문 · AbCdEfGhIjK · ERROR: Sign in", self.text())
        why2 = {}
        core._failed_one("AbCdEfGhIjK", RuntimeError("ERROR: Sign in to confirm you're not a bot"), logs.append, "firefox", None, why2)
        self.assertIn("파이어폭스 로그인 정보로도", why2["AbCdEfGhIjK"]["msg"], "PC 에서 브라우저를 고른 받기는 그 안내")


class AppFlowTests(LogBase):
    def test_close_confirm_yes_logs_and_quit_clears_marker(self):
        studiolog.session_start("9.9.9")
        self.addCleanup(studiolog.session_end)
        lines = []

        class Win:
            def evaluate_js(self, js, callback=None):
                callback(True)
        with mock.patch.dict(app.JOB, name="내보내기"), mock.patch.object(app, "log", lines.append):
            self.assertTrue(app._confirm_close(Win()))
        self.assertTrue(any("작업 중에 창을 닫았어요 · '내보내기'" in x for x in lines))
        running = self.tmp / studiolog.RUNNING
        self.assertTrue(running.exists())
        with mock.patch.object(app.remote.SVC, "shutdown"), mock.patch.object(app.editor, "wait_saves"), \
                mock.patch.object(app.editor, "cancel_export"), mock.patch.object(app.os, "_exit"):
            app._quit()
        self.assertFalse(running.exists(), "알고 닫음 → 다음에 '갑자기 꺼짐' 아님")

    def test_close_confirm_no_keeps_running(self):
        class Win:
            def evaluate_js(self, js, callback=None):
                callback(False)
        with mock.patch.dict(app.JOB, name="내보내기"), mock.patch.object(app, "log", lambda m: None):
            self.assertFalse(app._confirm_close(Win()))

    def test_restart_failure_keeps_running_marker(self):
        studiolog.session_start("9.9.9")
        self.addCleanup(studiolog.session_end)
        with mock.patch.object(app.remote.SVC, "shutdown"), mock.patch.object(app.subprocess, "Popen", side_effect=OSError("없음")), \
                mock.patch.object(app, "log", lambda m: None), mock.patch.object(app, "_quit") as q:
            app.restart()
        q.assert_not_called()
        self.assertTrue((self.tmp / studiolog.RUNNING).exists(), "계속 켜져 있으니 실행 표시도 그대로")


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="바꾸기 시험 "))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_convert_uses_job_object_runner_and_replace_retry(self):
        ff = shutil.which("ffmpeg") or core.ffmpeg()
        src = self.tmp / "00001.avi"
        r = core.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=160x120:r=10:d=1",
                      "-c:v", "mjpeg", str(src)])
        if r.returncode:
            self.skipTest("시험 영상을 못 만듦")
        calls = {"run": 0, "popen": 0, "replace": []}
        real_run, real_popen, real_rep = core.run, core.popen, updater.replace_retry

        def run(*a, **k):
            calls["run"] += 1
            return real_run(*a, **k)

        def popen(*a, **k):
            calls["popen"] += 1
            return real_popen(*a, **k)

        def rep(s, d, secs=None):
            calls["replace"].append((Path(s).name, Path(d).name, secs))
            return real_rep(s, d, secs)
        with mock.patch.object(core, "run", run), mock.patch.object(core, "popen", popen), mock.patch.object(updater, "replace_retry", rep):
            out = intake.convert(self.tmp, src.name, ff, lambda m: None)
        self.assertEqual(out, "00001.mp4")
        self.assertTrue((self.tmp / out).stat().st_size > 0)
        self.assertGreaterEqual(calls["run"], 1, "코덱 보기는 core.run")
        self.assertEqual(calls["popen"], calls["run"] + 1, "바꾸기는 core.popen (Job Object · core.run 도 안에서 popen)")
        self.assertEqual(calls["replace"][0][1:], ("00001.mp4", updater.SETTLE_SECS))
        self.assertEqual(calls["replace"][1][1], "00001.avi", "원본 옮기기도 replace_retry")
        self.assertTrue((self.tmp / intake.CONVERTED / "00001.avi").exists())

    def test_remember_is_atomic(self):
        with mock.patch.object(updater, "write_atomic", wraps=updater.write_atomic) as wa:
            intake.remember(self.tmp, {"size": 1, "mtime_ns": 2})
        wa.assert_called_once()
        self.assertEqual(json.loads((self.tmp / intake.SIG_FILE).read_text(encoding="utf-8")), {"size": 1, "mtime_ns": 2})


class PhoneCannotPickBrowserTests(RemoteBase):
    def test_browsers_and_cookies_not_reachable_from_phone(self):
        for path in ("/api/browsers", "/r/browsers", "/r/../api/browsers"):
            st, _, _ = self.call("GET", path)
            self.assertIn(st, (400, 404), path)
        got = []
        with mock.patch.object(core, "download", lambda ids, log, ck: got.append(ck) or []):
            st, _, b = self.act("download", {"url": "https://youtu.be/AbCdEfGhIjK", "cookies": "firefox"})
            self.assertEqual(st, 200, b)
            self.assertTrue(self.fb.wait_idle())
        self.assertEqual(got, [None], "휴대폰 받기는 로그인 정보(쿠키) 없이")

    def test_pc_server_refuses_tunnel_host_for_browsers(self):
        srv = app._Server(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with mock.patch.object(app, "PORT", port):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/browsers", headers={"Host": "quiet-river.trycloudflare.com"})
            with self.assertRaises(urllib.error.HTTPError) as c:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(c.exception.code, 403)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/browsers", timeout=10) as r:
                self.assertIn("firefox", json.loads(r.read())["order"])


if __name__ == "__main__":
    unittest.main()
