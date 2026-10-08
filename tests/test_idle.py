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


def _end_loop(ended):
    """시험이 남긴 루프를 '늘 작업 중' 으로 + 그 루프가 하던 내려놓기가 끝날 때까지 기다림 (release 는 _USE_LOCK 을 쥐고 돎).
    안 기다리면 바쁜 PC 에서 앞 시험의 release(끝의 gc.collect 가 느림)가 다음 시험의 touch() 뒤에 dirty=False 를 써서
    그 시험의 루프가 영영 안 내려놓았음 (test_using_blocks_release → test_waits_for_leftover_model_thread 에서 실제로 잡힘)."""
    ended["v"] = True
    with idle._USE_LOCK:
        pass


class IdleTest(unittest.TestCase):
    def _start(self, busy, idle_sec, lock=None):
        """idle.start 를 부르되, 시험이 끝나면 그 루프는 늘 '작업 중' 으로 봄 — 끝나지 않는 루프 스레드가 남아 같은 프로세스의
        다른 시험(진짜 face·thumb 모델 · 편집실 시험의 using() 이 부르는 touch)을 내려놓지 않게."""
        ended = {"v": False}
        self.addCleanup(_end_loop, ended)
        idle.start(lock or threading.Lock(), lambda: ended["v"] or busy(), idle_sec=idle_sec)

    def _wait_released(self, sess, what="", secs=10):
        """루프가 내려놓을 때까지 기다림 (바쁜 PC — load 40+·남은 메모리 2GB 에서 2초로는 모자란 적이 있음).
        끝내 안 비면 그때의 상태(쓰는 중 수 · 모델 스레드 · 마지막 작업 뒤 시간 · 살아 있는 스레드)를 실패 글에 남김."""
        end = time.time() + secs
        while sess and time.time() < end:
            time.sleep(0.02)
        if sess:
            ths = sorted(t.name for t in threading.enumerate() if t.is_alive())
            self.fail(f"{what} {secs}초 안에 안 내려놓음 · 남은 {sorted(sess)} · models_in_use={idle.models_in_use()} · "
                      f"_USE={dict(idle._USE)} · dirty={idle._ST['dirty']} · last={time.time() - idle._ST['last']:.2f}초 전 · 스레드 {ths}")

    def test_release_clears_sessions(self):
        mods = {"face": _fake("face"), "detect": _fake("detect"), "avmodels": _fake("avmodels"), "thumb": _fake("thumb")}  # detect: 선수·공 찾기 (D-069)
        with mock.patch.dict(sys.modules, mods):
            self.assertEqual(idle.release(), 8)
            self.assertEqual([len(m._SESS) for m in mods.values()], [0, 0, 0, 0])

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
            self._start(lambda: busy["v"], 0.2, lock)
            time.sleep(0.6)
            self.assertEqual(len(f._SESS), 2)  # 작업 중이면 그대로
            busy["v"] = False
            self._wait_released(f._SESS, "작업이 끝났는데")

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
                self._start(lambda: False, 0.1)
                time.sleep(0.6)
                self.assertEqual(len(f._SESS), 2)
                stop.set()
                th.join(2)
                self.assertFalse(idle.models_in_use())
                self._wait_released(f._SESS, "모델 스레드가 끝났는데")
        finally:
            stop.set()

    def test_using_blocks_release(self):
        """작업 밖에서 모델을 쓰는 동안(using) 은 작업이 없어도 안 내려놓고, 끝나면 그때부터 다시 셈."""
        f = _fake("face")
        with mock.patch.dict(sys.modules, {"face": f}), mock.patch.object(idle, "CHECK_SEC", 0.02), \
                mock.patch.dict(idle._ST, {"thread": None, "dirty": False}), mock.patch.dict(idle._USE, {"n": 0}):
            with idle.using():
                idle.touch()
                self._start(lambda: False, 0.05)
                with idle.using():  # 겹쳐도 됨
                    time.sleep(0.3)
                time.sleep(0.2)
                self.assertTrue(idle.models_in_use())
                self.assertEqual(len(f._SESS), 2)
            self.assertEqual(idle._USE["n"], 0)
            self._wait_released(f._SESS, "using 이 끝났는데 (끝나면 다시 내려놓음 = 루프가 살아 있었음)")

    def test_out_of_job_rough_cut_face_pass_keeps_model(self):
        """스타일 가편집(/api/edit/autoseq)은 작업이 아니라 바로 답하는 요청 — 강조 글씨 자리 찾기가 얼굴 모델을 쓰는 동안
        쉬는 동안 내려놓기가 돌아도 얼굴 모델(face._SESS)이 지워지지 않음 (지워지면 조용히 흔한 얼굴 자리로 돌아감)."""
        from pathlib import Path

        import editor
        import face
        seen = []

        def fake_faces(p, *a, **kw):
            time.sleep(0.03)
            seen.append(bool(face._SESS))
            return [{"box": [0.43, 0.12, 0.13, 0.27]}] if face._SESS else None

        def fake_ensure(**kw):  # 진짜 ensure 처럼 없으면 다시 불러 둠 · 그 뒤 장면 뽑기(스레드 시작)까지 잠깐 빔 — 그 틈에 지워지면 안 됨
            if not face._SESS:
                face._SESS.update(detect=object(), emotion=object())
            time.sleep(0.15)
            return True

        def fake_grab(name, t, folder):
            out = Path(folder) / f"f_{t:09.3f}.jpg"
            out.write_bytes(b"x")
            return out
        items = editor._items_from_cuts([{"in": 0.0, "out": 300.0}])
        spans = [(6.0 * k, 6.0 * k + editor.EMPH_DUR) for k in range(1, 21)]
        with mock.patch.dict(face._SESS, {"detect": object(), "emotion": object()}, clear=True), \
                mock.patch.object(face, "ready", return_value=True), \
                mock.patch.object(face, "ensure", side_effect=fake_ensure), \
                mock.patch.object(face, "faces", side_effect=fake_faces), \
                mock.patch.object(editor, "_grab_small", side_effect=fake_grab), \
                mock.patch.object(idle, "CHECK_SEC", 0.01), mock.patch.dict(idle._USE, {"n": 0}), \
                mock.patch.dict(idle._ST, {"thread": None, "dirty": False}):
            idle.touch()
            self._start(lambda: False, 0.001)  # 작업 없음 · 바로 내려놓을 때
            place = editor.emphasis_placer("얼굴 시험.mp4", items, "long", dict(editor.LONG_STYLE), True)
            place.prefetch(spans)
            for a, b in spans[:5]:
                place("인사이드!", dict(editor.TITLE_STYLE, size=88, y=0.3), a, b)
            self.assertEqual(len(seen), 40)
            self.assertTrue(all(seen), "얼굴 찾는 동안 모델이 지워짐")
            self._wait_released(face._SESS, "가편집이 끝났는데")  # 끝나면 내려놓음 = 루프가 내내 살아 있었음 (시험이 헛돌지 않았음)


if __name__ == "__main__":
    unittest.main()
