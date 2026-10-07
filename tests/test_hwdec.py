"""그래픽카드로 영상 풀기(hwdec.py) — python3 -m unittest tests.test_hwdec
이 기계엔 d3d11va 가 없어서: 확인 절차(목록·프레임 수 비교)와, 실패하면 일반 방식으로 다시 만드는 길만 시험."""
import unittest
from unittest import mock

import core
import hwdec
from tests.test_export import ExportBase, item, nframes


def fake_run(listed, sw, hw, sw_sec=1.0, hw_sec=0.5, hw_md5=None):
    """hwdec._run 대신: sw/hw = 풀린 프레임 수 (None = 실패) · hw_md5 = 그래픽카드로 푼 프레임 md5 (없으면 일반과 같음)."""
    def run(cmd, timeout=None):
        if "-hwaccels" in cmd:
            return 0, "Hardware acceleration methods:\n" + "\n".join(listed) + "\n", 0.01
        is_hw = "-hwaccel" in cmd
        n = hw if is_hw else sw
        if n is None:
            return 1, "", 0.1
        md = hw_md5 if (is_hw and hw_md5) else "abc"
        return 0, "#tb 0: 1/30\n" + "".join(f"0, {i}, {i}, 1, 100, {md}{i}\n" for i in range(n)), hw_sec if is_hw else sw_sec
    return run


class HwdecTest(unittest.TestCase):
    def setUp(self):
        self.st = mock.patch.dict(hwdec._ST, {"accel": None, "checked": False, "bad": False})
        self.st.start()
        self.addCleanup(self.st.stop)

    def test_only_hdr_and_windows(self):
        with mock.patch.object(hwdec.sys, "platform", "linux"), mock.patch.dict(hwdec.os.environ, {}, clear=False):
            hwdec.os.environ.pop("FUTSAL_HWDEC", None)
            self.assertEqual(hwdec.input_opts("hlg", "a.mov"), [])
        with mock.patch.object(hwdec.sys, "platform", "win32"), mock.patch.object(hwdec, "_run", fake_run(["d3d11va"], 30, 30)):
            self.assertEqual(hwdec.input_opts(None, "a.mov"), [])  # 일반 영상엔 안 씀
            self.assertEqual(hwdec.input_opts("hlg", "a.mov"), ["-hwaccel", "d3d11va"])
            hwdec.mark_bad()
            self.assertEqual(hwdec.input_opts("hlg", "a.mov"), [])

    def test_probe_must_match_frames(self):
        with mock.patch.object(hwdec.sys, "platform", "win32"):
            for listed, sw, hw in ((["vdpau"], 30, 30), (["d3d11va"], 30, 29), (["d3d11va"], 30, None)):
                with mock.patch.dict(hwdec._ST, {"accel": None, "checked": False, "bad": False}), mock.patch.object(hwdec, "_run", fake_run(listed, sw, hw)):
                    self.assertEqual(hwdec.input_opts("pq", "a.mov"), [], (listed, sw, hw))
        with mock.patch.dict(hwdec.os.environ, {"FUTSAL_HWDEC": "0"}), mock.patch.object(hwdec.sys, "platform", "win32"):
            self.assertEqual(hwdec.input_opts("hlg", "a.mov"), [])

    def test_probe_checks_pixels_speed_memory(self):
        """그래픽카드로 푼 화소가 다르거나(몰래 일반 방식·색 정보 틀림 등) 더 느리거나 메모리가 모자라면 안 씀."""
        with mock.patch.object(hwdec.sys, "platform", "win32"), mock.patch.dict(hwdec.os.environ, {"FUTSAL_HWDEC": ""}):
            for kw in ({"hw_md5": "zzz"}, {"sw_sec": 0.5, "hw_sec": 1.0}):
                with mock.patch.dict(hwdec._ST, {"accel": None, "checked": False, "bad": False}), \
                        mock.patch.object(hwdec, "_run", fake_run(["d3d11va"], 30, 30, **kw)):
                    self.assertEqual(hwdec.input_opts("hlg", "a.mov"), [], kw)
            import worker
            with mock.patch.dict(hwdec._ST, {"accel": None, "checked": False, "bad": False}), \
                    mock.patch.object(hwdec, "_run", fake_run(["d3d11va"], 30, 30)), mock.patch.object(worker, "avail_mb", lambda: 1200):
                self.assertEqual(hwdec.input_opts("hlg", "a.mov"), [])

    def test_probe_timeout_and_kill(self):
        """확인 ffmpeg 가 멈추면 시간 초과로 끄고 '안 됨' · 멈추기(✕)의 kill_all 로도 꺼짐."""
        import sys
        import threading
        import time
        slow = [sys.executable, "-c", "import time; time.sleep(30)"]
        t0 = time.time()
        rc, out, _ = hwdec._run(slow, timeout=1)
        self.assertIsNone(rc)
        self.assertLess(time.time() - t0, 10)
        threading.Timer(0.5, hwdec.kill_all).start()
        t0 = time.time()
        rc, out, _ = hwdec._run(slow, timeout=25)
        self.assertNotEqual(rc, 0)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(hwdec.PROCS, set())

    def test_real_probe_software_only(self):
        """진짜 ffmpeg 로 확인 절차 (이 기계 ffmpeg 엔 d3d11va 가 없어서 → 안 씀 · 끝까지 돌고 멈추지 않음)."""
        with mock.patch.dict(hwdec.os.environ, {"FUTSAL_HWDEC": "1"}):
            self.assertIsNone(hwdec._check("no_such_file.mov", 0))

    def test_strip(self):
        self.assertEqual(hwdec.strip(["-hwaccel", "d3d11va", "-ss", "1", "-t", "2", "-i", "x"]), ["-ss", "1", "-t", "2", "-i", "x"])


class HwdecFallbackExport(ExportBase):
    def test_failed_hw_decode_falls_back(self):
        """그래픽카드 풀기가 실패하는 PC: 그 구간을 일반 방식으로 다시 만들어 끝까지 (프레임 수 그대로) · 다음부턴 안 씀."""
        p = self.proj("하드웨어 풀기 실패", [item("a", "pat", "V1", 0, 0, 2), item("b", "red", "V1", 2, 1, 3)])
        calls = []

        def opts(hdr, path, t=0.0):
            calls.append(t)
            return [] if hwdec._ST["bad"] else ["-hwaccel", "no_such_accel"]

        with mock.patch.dict(hwdec._ST, {"bad": False}), mock.patch.object(hwdec, "input_opts", opts):
            out, logs = self.export(p)
            self.assertTrue(hwdec._ST["bad"])
        self.assertEqual(nframes(core.OUT / out[0]), 120)
        self.assertTrue(any("일반 방식으로 다시" in x for x in logs), logs)


class HwEncNotDecode(ExportBase):
    def test_encoder_error_keeps_hw_decode(self):
        """그래픽카드 '인코더' 오류(HwEncError)는 풀기 탓으로 보지 않음 → hwdec 를 끄지 않고 원래 처리(일반 인코딩)로."""
        import editor
        p = self.proj("인코더 실패", [item("a", "pat", "V1", 0, 0, 2)])
        real = editor._run_ff
        seen = []

        def ff(args, *a, **kw):
            if kw.get("enc") and "-hwaccel" in args:
                seen.append(1)
                raise editor.HwEncError("fake encoder fail")
            return real(hwdec.strip(args), *a, **kw)

        with mock.patch.dict(hwdec._ST, {"bad": False}), mock.patch.object(hwdec, "input_opts", lambda *a, **k: ["-hwaccel", "x"]), \
                mock.patch.object(editor, "hw_encoder", lambda: "h264_nvenc"), mock.patch.object(editor, "_hw_works", lambda *a: False), \
                mock.patch.object(editor, "_run_ff", ff):
            out, logs = self.export(p, hw=True)
            self.assertFalse(hwdec._ST["bad"])
        self.assertTrue(seen)
        self.assertFalse(any("영상 풀기가 안 돼서" in x for x in logs), logs)
        self.assertEqual(nframes(core.OUT / out[0]), 60)


class MemWorkers(unittest.TestCase):
    def test_workers_follow_free_memory(self):
        import editor
        import worker
        hd = {"a": {"kind": "video", "w": 1920, "h": 1080, "hdr": None}}
        hdr = {"a": {"kind": "video", "w": 3840, "h": 2160, "hdr": "hlg"}}
        logs = []
        with mock.patch.object(worker, "avail_mb", lambda: 8000):
            self.assertEqual(editor._mem_workers(3, hd, 1920, 1080), 3)
        with mock.patch.object(worker, "avail_mb", lambda: 2600):
            self.assertEqual(editor._mem_workers(2, hd, 1920, 1080), 2)       # 1080p: 0.5GB 씩
            self.assertEqual(editor._mem_workers(2, hdr, 1920, 1080, logs.append), 1)  # 4K HDR: 1.1GB 씩
        self.assertTrue(logs and "한 번에 하나씩" in logs[0], logs)
        with mock.patch.object(worker, "avail_mb", lambda: 500):
            self.assertEqual(editor._mem_workers(3, hd, 1920, 1080), 1)
        with mock.patch.object(worker, "avail_mb", lambda: None):
            self.assertEqual(editor._mem_workers(2, hdr, 1920, 1080), 2)


if __name__ == "__main__":
    unittest.main()
