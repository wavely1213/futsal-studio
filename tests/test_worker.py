"""따로 프로세스 작업 틀(worker.py)과 누끼 프로세스(cutout_worker.py) 테스트 — python3 -m unittest tests.test_worker

모델 없이 확인: 결과·진행 표시 전달 · 오류 문장 · 프로세스가 죽었을 때 · 멈추기(✕) · 한글 인자 · 남은 메모리 읽기 ·
누끼: 메모리가 모자라면 고품질을 건너뛰고 빠른 누끼 + 안내 / 고품질 프로세스가 죽으면 빠른 누끼로 한 번 더."""
import os
import threading
import time
import unittest
from unittest import mock

import core
import cutout_worker
import worker


# ---- 자식 프로세스가 부르는 함수들 (tests.test_worker:이름) ----

def _ok(a, b=0, name=""):
    core.set_progress(label="테스트", pct=50, detail=name)
    return {"sum": a + b, "name": name}


def _fail():
    raise ValueError("일부러 낸 오류 · 한글")


def _die():
    os._exit(9)


def _noisy():
    import sys
    sys.stdout.write("라이브러리가 줄바꿈 없이 쓴 글")  # 결과 줄이 이 뒤에 붙어도 잃지 않아야 함
    sys.stdout.flush()
    return "결과"


def _sleep(s):
    time.sleep(s)
    return "다 잤어요"


class WorkerTest(unittest.TestCase):
    def test_result_and_progress(self):
        got = []
        with mock.patch.object(core, "set_progress", lambda **kw: got.append(kw)):
            r = worker.call("tests.test_worker:_ok", 2, b=3, name="풋살 사관학교/누끼 ✂.png")
        self.assertEqual(r, {"sum": 5, "name": "풋살 사관학교/누끼 ✂.png"})
        self.assertIn({"label": "테스트", "pct": 50, "detail": "풋살 사관학교/누끼 ✂.png"}, got)

    def test_error_message(self):
        with self.assertRaises(worker.WorkerError) as cm:
            worker.call("tests.test_worker:_fail")
        self.assertIn("일부러 낸 오류 · 한글", str(cm.exception))
        self.assertEqual(cm.exception.kind, "ValueError")

    def test_died(self):
        with self.assertRaises(worker.WorkerError) as cm:
            worker.call("tests.test_worker:_die")
        self.assertEqual(cm.exception.kind, "died")
        # 우리 한국어 안내가 ' · ' 앞에 → 실패 카드가 그 줄을 씀 (원문이 붙어도)
        self.assertTrue(str(cm.exception).startswith("작업 프로세스가 끝까지 못 했어요 (종료 코드 9)"))
        self.assertEqual(cm.exception.code, 9)

    def test_goes_through_core_popen_with_utf8_env(self):
        """자식 파이썬도 core.popen (Windows: 검은 창 없이 · 앱이 꺼지면 같이 꺼지는 Job Object) · 출력은 UTF-8 (updater.py_env)."""
        seen = []
        real = core.popen

        def spy(cmd, **kw):
            seen.append(kw)
            return real(cmd, **kw)

        with mock.patch.object(core, "popen", spy):
            self.assertEqual(worker.call("tests.test_worker:_sleep", 0), "다 잤어요")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["env"].get("PYTHONUTF8"), "1")
        self.assertEqual(seen[0]["env"].get("PYTHONIOENCODING"), "utf-8")

    def test_child_stderr_goes_to_studio_log(self):
        lines = []
        with mock.patch.object(worker.studiolog, "write", lines.append):
            with self.assertRaises(worker.WorkerError):
                worker.call("tests.test_worker:_fail")
        self.assertTrue(any("작업 프로세스(tests.test_worker:_fail) 오류 출력" in x and "ValueError" in x for x in lines), lines)

    def test_result_after_unterminated_print(self):
        self.assertEqual(worker.call("tests.test_worker:_noisy"), "결과")

    def test_python_for_child(self):
        exe, env = worker._python()
        self.assertTrue(os.path.exists(exe))
        if os.name != "nt":
            self.assertEqual(env, {})

    def test_out_of_memory_detection(self):
        oom = [worker.WorkerError("x", -9, "died"), worker.WorkerError("x", 0xC0000017, "died"),
               worker.WorkerError("std::bad_alloc", 1, "RuntimeException"), worker.WorkerError("", 1, "MemoryError"),
               worker.WorkerError("[ONNXRuntimeError] : 6 : Failed to allocate memory for requested buffer", 1, "Fail")]
        other = [worker.WorkerError("DLL load failed while importing onnxruntime_pybind11_state", 1, "ImportError"),
                 worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 1)", 1, "died"),
                 worker.WorkerError("x", 0xC0000135, "died")]
        self.assertTrue(all(worker.out_of_memory(e) for e in oom))
        self.assertFalse(any(worker.out_of_memory(e) for e in other))

    def test_cancel_kills_child(self):
        ev, procs = threading.Event(), set()
        threading.Timer(0.8, ev.set).start()
        t = time.time()
        with self.assertRaises(worker.Cancelled):
            worker.call("tests.test_worker:_sleep", 30, cancel=ev, procs=procs)
        self.assertLess(time.time() - t, 10)
        self.assertEqual(procs, set())

    def test_avail_mb(self):
        v = worker.avail_mb()
        self.assertTrue(v is None or (isinstance(v, int) and v > 0))


class CutoutWorkerTest(unittest.TestCase):
    def test_low_memory_uses_fast(self):
        calls = []

        def fake(target, src, kind, **kw):
            calls.append(kind)
            return "/tmp/cut.png"

        with mock.patch.object(worker, "avail_mb", return_value=3000), mock.patch.object(worker, "call", fake):
            out, used, note = cutout_worker.remove_bg("/tmp/a.png", "hq")
        self.assertEqual(calls, ["fast"])
        self.assertEqual((used, note), ("fast", cutout_worker.LOW_MEM_NOTE))

    def test_enough_memory_keeps_hq(self):
        with mock.patch.object(worker, "avail_mb", return_value=12000), mock.patch.object(worker, "call", return_value="/tmp/c.png") as c:
            out, used, note = cutout_worker.remove_bg("/tmp/a.png", "hq")
        self.assertEqual((used, note), ("hq", None))
        self.assertEqual(c.call_args[0][2], "hq")

    def test_hq_died_retries_fast(self):
        calls = []

        def fake(target, src, kind, **kw):
            calls.append(kind)
            if kind == "hq":
                raise worker.WorkerError("죽음", -9, "died")
            return "/tmp/cut.png"

        with mock.patch.object(worker, "avail_mb", return_value=None), mock.patch.object(worker, "call", fake):
            out, used, note = cutout_worker.remove_bg("/tmp/a.png", "hq")
        self.assertEqual(calls, ["hq", "fast"])
        self.assertEqual((used, note), ("fast", cutout_worker.LOW_MEM_NOTE))

    def test_hq_other_failure_is_not_memory(self):
        def fake(target, src, kind, **kw):
            if kind == "hq":
                raise worker.WorkerError("DLL load failed", 1, "ImportError")
            return "/tmp/cut.png"

        with mock.patch.object(worker, "avail_mb", return_value=None), mock.patch.object(worker, "call", fake):
            out, used, note = cutout_worker.remove_bg("/tmp/a.png", "hq")
        self.assertEqual((used, note), ("fast", cutout_worker.FAIL_NOTE))

    def test_fast_failure_raises(self):
        with mock.patch.object(worker, "call", side_effect=worker.WorkerError("x", 1, "died")):
            with self.assertRaises(worker.WorkerError):
                cutout_worker.remove_bg("/tmp/a.png", "fast")

    def test_failure_is_plain_korean_card(self):
        """누끼 프로세스가 끝내 실패 → 실패 카드(trouble.explain)는 쉬운 한국어 한 줄 (영어 원문·'작업 프로세스'는 안 보임)."""
        import trouble
        cases = [
            (worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 -9)", -9, "died"), "memory", "메모리가 부족해요"),
            (worker.WorkerError("onnxruntime: Failed to load model", 1, "RuntimeError"), "other", None),
            (worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 1) · ImportError: DLL load failed while importing x", 1, "died"),
             "other", "Visual C++"),
        ]
        for err, kind, extra in cases:
            with mock.patch.object(worker, "avail_mb", return_value=None), mock.patch.object(worker, "call", side_effect=err), \
                    mock.patch.object(cutout_worker.studiolog, "trace"):
                with self.assertRaises(worker.WorkerError) as cm:
                    cutout_worker.remove_bg("/tmp/a.png", "hq")
            info = trouble.explain(cm.exception)
            self.assertEqual(info["kind"], kind, info)
            self.assertTrue(info["msg"].startswith(cutout_worker.FAIL_MSG), info)
            self.assertNotIn("작업 프로세스", info["msg"])
            self.assertNotIn("onnxruntime", info["msg"])
            if extra:
                self.assertIn(extra, info["msg"])
        self.assertEqual(trouble.explain(worker.Cancelled("멈췄어요"))["kind"], "cancelled")

    def test_hq_failure_traced(self):
        def fake(target, src, kind, **kw):
            if kind == "hq":
                raise worker.WorkerError("죽음", -9, "died")
            return "/tmp/cut.png"

        with mock.patch.object(worker, "avail_mb", return_value=None), mock.patch.object(worker, "call", fake), \
                mock.patch.object(cutout_worker.studiolog, "trace") as tr:
            cutout_worker.remove_bg("/tmp/a.png", "hq")
        self.assertEqual(tr.call_count, 1)
        self.assertEqual(tr.call_args[0][1], "고품질 누끼 오류 위치")


class CutRouteTest(unittest.TestCase):
    """/api/thumb/cut (실제 HTTP 서버 · 실제 start_job): 누끼는 cutout_worker 로 (편집실 멈추기 신호·프로세스 목록을 넘김) ·
    결과에 쓴 모델·안내 · 끝내 실패하면 실패 카드가 쉬운 한국어 한 줄."""

    def setUp(self):
        import json
        import shutil
        import tempfile
        import urllib.request
        from http.server import ThreadingHTTPServer
        from pathlib import Path

        import app
        import editor
        import thumb
        self.app, self.editor, self.json, self.urllib = app, editor, json, urllib
        tmp = Path(tempfile.mkdtemp(prefix="누끼 경로 시험 "))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assets = tmp / "assets"
        self.assets.mkdir()
        (self.assets / "올린 사진.png").write_bytes(b"png")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        for pt in (mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", tmp / "studio.log"),
                   mock.patch.object(thumb, "ASSETS", self.assets)):
            pt.start()
            self.addCleanup(pt.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{port}"

    def cut(self):
        req = self.urllib.request.Request(self.base + "/api/thumb/cut", method="POST", headers={"Content-Type": "application/json"},
                                          data=self.json.dumps({"src": "/asset/올린 사진.png", "kind": "hq"}).encode())
        with self.urllib.request.urlopen(req, timeout=20) as r:
            j = self.json.loads(r.read())
        self.assertTrue(j["ok"], j)
        end = time.time() + 20
        while self.app.JOB["name"] and time.time() < end:
            time.sleep(0.05)
        return self.app.DONE[j["jobId"]]

    def test_cut_goes_through_worker(self):
        seen = []

        def fake(sp, kind, cancel, procs, log):
            seen.append((sp.name, kind, cancel, procs))
            return self.assets / "cut_1.png", "fast", cutout_worker.LOW_MEM_NOTE

        with mock.patch.object(cutout_worker, "remove_bg", fake):
            done = self.cut()
        self.assertEqual(seen, [("올린 사진.png", "hq", self.editor.CANCEL, self.editor._PROCS)])
        self.assertIsNone(done["error"])
        self.assertEqual(done["result"], {"cut": "/asset/cut_1.png", "src": "/asset/올린 사진.png", "kind": "fast",
                                          "note": cutout_worker.LOW_MEM_NOTE})

    def test_cut_failure_card(self):
        err = worker.WorkerError(cutout_worker.fail_msg(worker.WorkerError("작업 프로세스가 끝까지 못 했어요 (종료 코드 -9)", -9, "died")),
                                 -9, "died")
        with mock.patch.object(cutout_worker, "remove_bg", side_effect=err):
            done = self.cut()
        self.assertEqual(done["fail"]["kind"], "memory")
        self.assertTrue(done["error"].startswith(cutout_worker.FAIL_MSG), done)
        self.assertNotIn("작업 프로세스", done["error"])
        self.assertNotIn("종료 코드", done["error"])


if __name__ == "__main__":
    unittest.main()
