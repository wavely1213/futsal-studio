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
            self.post("/api/thumb/save", {})  # name 없음 → 처리 중 KeyError
        text = self.log.read_text(encoding="utf-8")
        self.assertRegex(text, r"요청 오류 · POST /api/thumb/save · KeyError: 'name' · app\.py:\d+ do_POST")

    def test_client_disconnect_not_logged_as_error(self):
        """화면이 먼저 끊은 연결은 studio.log 를 어지럽히지 않음."""
        with mock.patch.object(self.app.Handler, "do_GET", side_effect=ConnectionResetError(104, "Connection reset by peer")), \
                mock.patch.object(sys, "stderr", None):
            try:
                urllib.request.urlopen(self.base + "/api/state", timeout=10)
            except Exception:  # noqa: BLE001 — 끊긴 응답
                pass
        self.assertNotIn("요청 오류", "\n".join(self.lines()))

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
