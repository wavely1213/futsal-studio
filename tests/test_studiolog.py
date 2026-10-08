"""studio.log(studiolog.py) 테스트 — 사용자 PC 문제를 이 파일 하나로 찾을 수 있게.

줄마다 연도 붙은 시각 · 크기 제한(studio.old.log 하나만) · 오류 위치(종류·내용·파일:줄·호출 경로·원인) · 콘솔이 없는
pythonw 흉내(sys.stderr=None)에서 POST /api/download {} → studio.log 에 위치 · 요청 처리 중 오류 · 뒤에서 도는 스레드 오류 ·
실행 표시(studio.running.json): 작업 중에 꺼짐 → 다음에 켤 때 기록 + 화면 알림, 작업 없이 꺼짐 → 기록만, 정상 종료 → 없음 ·
시험 서버(작업만 돌림)는 실행 표시를 쓰지 않음 · 편집실 내보내기 오류 위치.
실행: 저장소 폴더에서 python3 -m unittest tests.test_studiolog
"""
import json
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402 — app 을 불러올 때 정한 기록 위치(app.LOGFILE)를 시험마다 되돌리려고 먼저 불러 둠
import core  # noqa: E402
import studiolog  # noqa: E402

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="기록 시험 "))
        self.log = self.tmp / "studio.log"
        self.old = studiolog._PATH[0]
        studiolog.setup(lambda: self.log)
        studiolog._SESSION.clear()

    def tearDown(self):
        studiolog._PATH[0] = self.old
        studiolog._SESSION.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []


def boom():
    return {}["ids"]


class WriteTests(Base):
    def test_year_in_stamp_and_bad_characters_do_not_stop(self):
        studiolog.write("시작")
        studiolog.write("반쪽 이모지 \ud83d 도 그대로")
        ls = self.lines()
        self.assertEqual(len(ls), 2)
        self.assertRegex(ls[0], STAMP)
        self.assertTrue(ls[0].startswith(time.strftime("%Y-")), "연도가 붙음")
        self.assertIn("반쪽 이모지", ls[1])

    def test_rotates_to_one_old_file(self):
        with mock.patch.object(studiolog, "MAX_BYTES", 200):
            for i in range(30):
                studiolog.write(f"줄 {i:02d} " + "가" * 10)
        old = self.tmp / studiolog.OLD_NAME
        self.assertTrue(old.exists())
        self.assertLess(self.log.stat().st_size, 400)
        self.assertLess(old.stat().st_size, 400)
        self.assertIn("줄 29", self.lines()[-1])
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), sorted(["studio.log", studiolog.OLD_NAME]), "하나만 남김")

    def test_no_path_writes_nothing(self):
        studiolog._PATH[0] = None
        studiolog.write("아무 데도 안 씀")
        studiolog.job("작업")
        self.assertFalse(self.log.exists())


class WhereTests(Base):
    def test_location_line_has_file_line_and_call_path(self):
        try:
            boom()
        except KeyError as e:
            w = studiolog.where(e)
        self.assertTrue(w.startswith("KeyError: 'ids' · "), w)
        self.assertRegex(w, r"tests/test_studiolog\.py:\d+ boom$", "앱 폴더 밖 파일은 오류 난 자리만 '폴더/파일'로")

    def test_cause_is_included_and_external_frame_short(self):
        try:
            try:
                json.loads("{깨짐")
            except ValueError as e:
                raise RuntimeError("설정을 읽지 못했어요") from e
        except RuntimeError as e:
            w = studiolog.where(e)
        self.assertIn("RuntimeError: 설정을 읽지 못했어요", w)
        self.assertIn("원인 JSONDecodeError", w)
        self.assertRegex(w, r"json/decoder\.py:\d+ raw_decode", "바깥 파일은 '패키지/파일'로만 (사용자 경로 없음)")

    def test_trace_writes_location_even_without_console(self):
        with mock.patch.object(sys, "stderr", None):
            try:
                boom()
            except KeyError:
                studiolog.trace()
        self.assertIn("오류 위치 · KeyError: 'ids'", self.lines()[0])

    def test_thread_hook_records_background_errors(self):
        hooked = list(studiolog._HOOKED)
        prev = threading.excepthook, sys.excepthook
        try:
            studiolog._HOOKED.clear()
            with mock.patch.object(threading, "excepthook", lambda a: None):
                studiolog.install_hooks()
                t = threading.Thread(target=boom, name="뒤에서 출처 찾기")
                t.start()
                t.join()
        finally:
            threading.excepthook, sys.excepthook = prev
            studiolog._HOOKED[:] = hooked
        line = next(x for x in self.lines() if "뒤에서 하던 일" in x)
        self.assertIn("(뒤에서 출처 찾기)", line)
        self.assertRegex(line, r"KeyError: 'ids' · tests/test_studiolog\.py:\d+ boom")


class SessionTests(Base):
    def test_crash_during_job_is_reported_next_start(self):
        self.assertIsNone(studiolog.session_start("2.3.0"), "처음 켬")
        studiolog.job("편집점 찾기")
        mark = json.loads((self.tmp / studiolog.RUNNING).read_text(encoding="utf-8"))
        self.assertEqual((mark["job"], mark["version"]), ("편집점 찾기", "2.3.0"))
        studiolog._SESSION.clear()  # 여기서 갑자기 꺼짐 (정상 종료 표시 없이)
        note = studiolog.session_start("2.3.0")
        self.assertIn("'편집점 찾기' 중에 갑자기 꺼졌어요", note["log"])
        self.assertIn("v2.3.0", note["log"])
        self.assertIn("studio.log", note["notice"])

    def test_crash_without_job_only_logged(self):
        studiolog.session_start("2.3.0")
        studiolog.job("편집점 찾기")
        studiolog.job(None)
        studiolog._SESSION.clear()
        note = studiolog.session_start("2.3.0")
        self.assertIn("정상적으로 끝나지 않았어요", note["log"])
        self.assertIsNone(note["notice"], "PC 를 끈 것일 수 있어 화면에는 안 알림")

    def test_clean_exit_reports_nothing(self):
        studiolog.session_start("2.3.0")
        studiolog.session_end()
        self.assertFalse((self.tmp / studiolog.RUNNING).exists())
        self.assertIsNone(studiolog.session_start("2.3.0"))

    def test_broken_marker_counts_as_unclean(self):
        (self.tmp / studiolog.RUNNING).write_text("{깨짐", encoding="utf-8")
        note = studiolog.session_start("2.3.0")
        self.assertIn("정상적으로 끝나지 않았어요", note["log"])

    def test_job_without_session_writes_no_marker(self):
        studiolog.job("내보내기")
        studiolog.job(None)
        self.assertFalse((self.tmp / studiolog.RUNNING).exists(), "시험 서버·도구는 실행 표시를 쓰지 않음")


class CrashTargetTests(Base):
    """D-072: 작업 중에 갑자기 꺼지면 다음에 켤 때 '어느 영상'·받아쓰기 방식까지 (예전: 작업 이름만 → 어디서 무엇을 다시 할지 모름)."""

    def crash(self, name="편집점 찾기", **what):
        studiolog.session_start("2.8.0")
        studiolog.job(name)
        if what:
            studiolog.target(**what)
        studiolog._SESSION.clear()  # oom-kill · kill -9 흉내 (정상 종료 표시 없이)
        return studiolog.session_start("2.8.0")

    def test_target_saved_and_reported(self):
        note = self.crash(path="/api/analyze", names=["IMG_4830.mp4", "레슨 [꿀팁].mp4"], model="large-v3-turbo")
        self.assertIn("'IMG_4830.mp4' 외 1개 영상의 '편집점 찾기' 중에", note["notice"])
        self.assertIn("대상 IMG_4830.mp4, 레슨 [꿀팁].mp4", note["log"])
        self.assertIn("받아쓰기 large-v3-turbo", note["log"])
        c = note["crash"]
        self.assertEqual((c["job"], c["path"], c["names"], c["model"]), ("편집점 찾기", "/api/analyze", ["IMG_4830.mp4", "레슨 [꿀팁].mp4"], "large-v3-turbo"))
        self.assertTrue(c["id"])

    def test_without_target_like_before(self):
        note = self.crash("보관함에 담기")
        self.assertIn("지난번에 '보관함에 담기' 중에 프로그램이 갑자기 꺼졌어요", note["notice"])
        self.assertEqual(note["crash"]["job"], "보관함에 담기")
        self.assertNotIn("names", note["crash"])

    def test_finished_job_forgets_target(self):
        studiolog.session_start("2.8.0")
        studiolog.job("편집점 찾기")
        studiolog.target(path="/api/analyze", names=["a.mp4"])
        studiolog.job(None)
        studiolog.job("내보내기")
        studiolog._SESSION.clear()
        note = studiolog.session_start("2.8.0")
        self.assertNotIn("names", note["crash"], "다음 작업은 앞 작업의 대상을 물려받지 않음")
        self.assertNotIn("a.mp4", note["notice"])

    def test_target_needs_running_job_and_known_fields(self):
        studiolog.target(path="/api/analyze", names=["a.mp4"])  # 실행 표시 없음 (시험 서버) → 아무것도 안 씀
        self.assertFalse((self.tmp / studiolog.RUNNING).exists())
        note = self.crash(path="/api/analyze", names=["a.mp4", 3, ""], model=["x"], cookies="firefox", title="t")
        c = note["crash"]
        self.assertEqual(c["names"], ["a.mp4"])
        self.assertNotIn("model", c, "꼴이 다른 값은 버림")
        self.assertNotIn("cookies", c, "모르는 칸은 남기지 않음")

    def test_lone_surrogate_name_does_not_break_job(self):
        """검토 재현: 요청 본문의 이름에 짝 없는 반쪽 글자(\\ud800)가 있으면 실행 표시를 쓰다 UnicodeEncodeError(ValueError)가
        빠져나와 작업이 시작도 못 하고 날것의 오류를 냄 → 이제 \\u 꼴로 쓰고 작업은 그대로 · 다음에 켤 때 그대로 읽힘."""
        bad = "IMG_\ud800.mp4"
        note = self.crash(path="/api/analyze", names=[bad], model="small")
        self.assertEqual(note["crash"]["names"], [bad])

    def test_hand_edited_marker(self):
        (self.tmp / studiolog.RUNNING).write_text(json.dumps({"start": "x", "version": "1", "job": "편집점 찾기",
                                                              "what": {"names": "a.mp4", "path": 7}}), encoding="utf-8")
        note = studiolog.session_start("2.8.0")
        self.assertEqual({k: v for k, v in note["crash"].items() if k not in ("job", "at", "id")}, {})


class AppTests(Base):
    """app 과 함께: 작업 실패·요청 오류·켤 때 알림."""

    def setUp(self):
        super().setUp()
        self.app = app
        work = self.tmp / "작업"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        self.patches += [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.log)]
        for p in self.patches:
            p.start()
        studiolog.setup(lambda: app.LOGFILE)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        for _ in range(200):
            if not self.app.JOB["name"]:
                break
            time.sleep(0.05)
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.patches:
            p.stop()
        super().tearDown()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except (urllib.error.URLError, ConnectionError):
            return None

    def test_pythonw_download_without_ids_leaves_location_in_studio_log(self):
        """재현(backlog): 콘솔 없이(stdout·stderr=None) POST /api/download {} → 예전에는 "문제가 생겼어요 'ids'" 한 줄뿐."""
        with mock.patch.object(sys, "stderr", None), mock.patch.object(sys, "stdout", None):
            self.assertEqual(self.post("/api/download", {}), 200)
            for _ in range(200):
                if not self.app.JOB["name"]:
                    break
                time.sleep(0.05)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("문제가 생겼어요 · 예상하지 못한 문제가 생겼어요", text)
        self.assertRegex(text, r"오류 위치 · KeyError: 'ids' · app\.py:\d+ _download ← app\.py:\d+ <lambda> ← app\.py:\d+ runner")
        self.assertTrue(all(STAMP.match(x) for x in text.splitlines()), "모든 줄에 연도 붙은 시각")

    def test_request_handler_error_location(self):
        with mock.patch.object(sys, "stderr", None):
            self.post("/api/thumb/frames", {})  # name 없음 → 처리 중 KeyError (/api/thumb/save 는 이제 이름부터 확인 · 404 gone)
        text = self.log.read_text(encoding="utf-8")
        self.assertRegex(text, r"요청 오류 · POST /api/thumb/frames · KeyError: 'name' · app\.py:\d+ do_POST")

    def test_client_disconnect_not_logged_as_error(self):
        """화면이 먼저 끊은 연결은 studio.log 를 어지럽히지 않음."""
        with mock.patch.object(self.app.Handler, "do_GET", side_effect=ConnectionResetError(104, "Connection reset by peer")), \
                mock.patch.object(sys, "stderr", None):
            try:
                urllib.request.urlopen(self.base + "/api/state", timeout=10)
            except Exception:  # noqa: BLE001 — 끊긴 응답
                pass
        self.assertNotIn("요청 오류", "\n".join(self.lines()))

    def test_crash_during_analyze_becomes_library_card(self):
        """편집점 찾기 중 꺼짐 → 다음에 켜면 보관함 카드용(/api/state crash: 영상·받아쓰기 방식·다시 할 주소) · 1단계 알림 카드는 안 띄움 ·
        ✕ 로 닫거나 같은 작업을 다시 시작하면 내림."""
        import updater
        updater.take_notice()
        (core.VIDEOS / "IMG_4830.mp4").write_bytes(b"\0" * 10)
        studiolog.session_start("2.8.0")
        started = threading.Event()
        gate = threading.Event()

        def fake(names, log, model):  # 받아쓰는 중 (여기서 꺼짐)
            started.set()
            gate.wait(10)
            out = core.Analyzed()
            out.failed = {n: {"kind": "broken", "msg": "x"} for n in names}
            return out
        with mock.patch.object(core, "analyze_many", side_effect=fake), mock.patch.object(core, "pc_spec", return_value={"memGB": 7.8, "cores": 8}):
            self.app.start_job("편집점 찾기", lambda: self.app.Handler._analyze({"names": ["IMG_4830.mp4"], "model": "large-v3-turbo"}))
            self.assertTrue(started.wait(10))
            mark = json.loads((self.tmp / studiolog.RUNNING).read_text(encoding="utf-8"))
            self.assertEqual(mark["what"], {"path": "/api/analyze", "names": ["IMG_4830.mp4"], "model": "large-v3-turbo"})
            snap = dict(studiolog._SESSION)
            gate.set()
        for _ in range(200):
            if not self.app.JOB["name"]:
                break
            time.sleep(0.05)
        (self.tmp / studiolog.RUNNING).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")  # 받아쓰는 중에 꺼졌던 표시
        studiolog._SESSION.clear()
        hooked = list(studiolog._HOOKED)
        prev = threading.excepthook, sys.excepthook
        try:
            with mock.patch.object(studiolog.atexit, "register"), mock.patch.dict(self.app.CRASH, {}, clear=True):
                self.app._session_start()
                self.assertIsNone(updater.take_notice(), "1단계에 일반 알림 카드를 띄우지 않음 (보관함 카드로)")
                with urllib.request.urlopen(self.base + "/api/state", timeout=10) as r:
                    crash = json.loads(r.read())["crash"]
                self.assertEqual((crash["path"], crash["names"], crash["model"], crash["job"]),
                                 ("/api/analyze", ["IMG_4830.mp4"], "large-v3-turbo", "편집점 찾기"))
                self.assertEqual(self.post("/api/crash/dismiss", {}), 200)
                self.assertEqual(self.app.CRASH, {})
                self.app.CRASH.update(crash)
                with mock.patch.object(core, "analyze_many", side_effect=fake):
                    gate.set()
                    self.assertEqual(self.post("/api/analyze", {"names": ["IMG_4830.mp4"], "model": "small"}), 200)
                    self.assertEqual(self.app.CRASH, {}, "다시 하는 중이면 카드를 내림")
                    for _ in range(200):
                        if not self.app.JOB["name"]:
                            break
                        time.sleep(0.05)
        finally:
            threading.excepthook, sys.excepthook = prev
            studiolog._HOOKED[:] = hooked
        self.assertIn("대상 IMG_4830.mp4", self.log.read_text(encoding="utf-8"))

    def test_session_start_logs_and_notifies_crash_during_job(self):
        import updater
        updater.take_notice()
        (self.tmp / studiolog.RUNNING).write_text(json.dumps({"start": "2026-10-07 09:00:00", "version": "2.2.0",
                                                              "job": "보관함에 담기", "jobAt": "2026-10-07 09:05:00"}), encoding="utf-8")
        hooked = list(studiolog._HOOKED)
        prev = threading.excepthook, sys.excepthook
        try:
            with mock.patch.object(studiolog.atexit, "register"):
                self.app._session_start()
        finally:
            threading.excepthook, sys.excepthook = prev
            studiolog._HOOKED[:] = hooked
        self.assertIn("'보관함에 담기' 중에 갑자기 꺼졌어요", self.log.read_text(encoding="utf-8"))
        n = updater.take_notice()
        self.assertTrue(n["warn"])
        self.assertIn("보관함에 담기", n["text"])
        mark = json.loads((self.tmp / studiolog.RUNNING).read_text(encoding="utf-8"))
        self.assertIsNone(mark["job"], "이번 실행 표시로 바뀜")
        self.app.start_job("시험 작업", lambda: time.sleep(0.2))
        time.sleep(0.05)
        self.assertEqual(json.loads((self.tmp / studiolog.RUNNING).read_text(encoding="utf-8"))["job"], "시험 작업")
        for _ in range(100):
            if not self.app.JOB["name"]:
                break
            time.sleep(0.05)
        time.sleep(0.05)
        self.assertIsNone(json.loads((self.tmp / studiolog.RUNNING).read_text(encoding="utf-8"))["job"])
        with mock.patch.object(self.app.editor, "wait_saves"), mock.patch.object(self.app.editor, "cancel_export"), \
                mock.patch.object(self.app.os, "_exit") as ex:
            self.app._quit()
        ex.assert_called_once_with(0)
        self.assertFalse((self.tmp / studiolog.RUNNING).exists(), "창을 닫으면 정상 종료")

    def test_export_error_location_from_editor(self):
        """편집실 내보내기 안의 예상 못 한 오류(RuntimeError 가 아님)도 파일:줄을 남김 (editor.py 의 traceback.print_exc 자리)."""
        import editor
        proj = {"format": "long", "info": {"duration": 5, "width": 320, "height": 240}, "source": "a.mp4",
                "clips": [{"in": 0, "out": 2}], "captions": []}
        with mock.patch.object(editor, "_missing_media", return_value=[]), \
                mock.patch.object(editor, "build_ass", side_effect=ZeroDivisionError("division by zero")), \
                mock.patch.object(sys, "stderr", None):
            with self.assertRaises(RuntimeError):
                editor.export("a.mp4", proj, {"srt": False, "xml": False}, lambda m: None)
        line = next(x for x in self.lines() if "오류 위치" in x)
        self.assertRegex(line, r"ZeroDivisionError: division by zero · .*editor\.py:\d+ export")


if __name__ == "__main__":
    unittest.main()
