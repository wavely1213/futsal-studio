"""쉬는 동안 모델 내려놓기(idle.py) — python3 -m unittest tests.test_idle"""
import sys
import threading
import time
import types
import unittest
from unittest import mock

import idle


def _fake(name):
    m = types.ModuleType(name)
    m._SESS, m._LOCK = {"a": object(), "b": object()}, threading.Lock()
    return m


class IdleTest(unittest.TestCase):
    def test_release_clears_sessions(self):
        mods = {"face": _fake("face"), "avmodels": _fake("avmodels"), "thumb": _fake("thumb")}
        with mock.patch.dict(sys.modules, mods):
            self.assertEqual(idle.release(), 6)
            self.assertEqual([len(m._SESS) for m in mods.values()], [0, 0, 0])

    def test_locked_module_is_skipped(self):
        f = _fake("face")
        with mock.patch.dict(sys.modules, {"face": f}):
            f._LOCK.acquire()
            try:
                t = time.time()
                idle._clear("face")
                self.assertLess(time.time() - t, 3)
                self.assertEqual(len(f._SESS), 2)  # 불러오는 중이면 건드리지 않음
            finally:
                f._LOCK.release()

    def test_loop_waits_for_idle_and_no_job(self):
        f = _fake("face")
        busy = {"v": True}
        lock = threading.Lock()
        with mock.patch.dict(sys.modules, {"face": f}), mock.patch.object(idle, "CHECK_SEC", 0.05), \
                mock.patch.dict(idle._ST, {"thread": None, "dirty": False}):
            idle.touch("작업", None, None, None, 1.0)
            idle.start(lock, lambda: busy["v"], idle_sec=0.2)
            time.sleep(0.6)
            self.assertEqual(len(f._SESS), 2)  # 작업 중이면 그대로
            busy["v"] = False
            for _ in range(40):
                if not f._SESS:
                    break
                time.sleep(0.05)
            self.assertEqual(len(f._SESS), 0)

    def test_waits_for_leftover_model_thread(self):
        """멈춘 작업이 남긴 글자 읽기 스레드(model-ocr)가 아직 돌면 작업이 없어도 안 내려놓음."""
        f = _fake("face")
        stop = threading.Event()
        th = threading.Thread(target=stop.wait, name="model-ocr", daemon=True)
        th.start()
        try:
            self.assertTrue(idle.models_in_use())
            with mock.patch.dict(sys.modules, {"face": f}), mock.patch.object(idle, "CHECK_SEC", 0.05), \
                    mock.patch.dict(idle._ST, {"thread": None, "dirty": False}):
                idle.touch()
                idle.start(threading.Lock(), lambda: False, idle_sec=0.1)
                time.sleep(0.6)
                self.assertEqual(len(f._SESS), 2)
                stop.set()
                th.join(2)
                self.assertFalse(idle.models_in_use())
                for _ in range(40):
                    if not f._SESS:
                        break
                    time.sleep(0.05)
                self.assertEqual(len(f._SESS), 0)
        finally:
            stop.set()


if __name__ == "__main__":
    unittest.main()
