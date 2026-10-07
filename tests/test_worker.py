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
        self.assertEqual(cm.exception.code, 9)

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


if __name__ == "__main__":
    unittest.main()
