"""그래픽카드로 영상 풀기(hwdec.py) — python3 -m unittest tests.test_hwdec
이 기계엔 d3d11va 가 없어서: 확인 절차(목록·프레임 수 비교)와, 실패하면 일반 방식으로 다시 만드는 길만 시험."""
import subprocess
import unittest
from unittest import mock

import core
import hwdec
from tests.test_export import ExportBase, item, nframes


def fake_run(listed, sw, hw):
    def run(cmd):
        if "-hwaccels" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "Hardware acceleration methods:\n" + "\n".join(listed) + "\n", "")
        n = hw if "-hwaccel" in cmd else sw
        return subprocess.CompletedProcess(cmd, 0 if n is not None else 1, "", f"frame=   {n} fps=0.0" if n is not None else "error")
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
        with mock.patch.object(hwdec.sys, "platform", "win32"), mock.patch.object(core, "run", fake_run(["d3d11va"], 30, 30)):
            self.assertEqual(hwdec.input_opts(None, "a.mov"), [])  # 일반 영상엔 안 씀
            self.assertEqual(hwdec.input_opts("hlg", "a.mov"), ["-hwaccel", "d3d11va"])
            hwdec.mark_bad()
            self.assertEqual(hwdec.input_opts("hlg", "a.mov"), [])

    def test_probe_must_match_frames(self):
        with mock.patch.object(hwdec.sys, "platform", "win32"):
            for listed, sw, hw in ((["vdpau"], 30, 30), (["d3d11va"], 30, 29), (["d3d11va"], 30, None)):
                with mock.patch.dict(hwdec._ST, {"accel": None, "checked": False, "bad": False}), mock.patch.object(core, "run", fake_run(listed, sw, hw)):
                    self.assertEqual(hwdec.input_opts("pq", "a.mov"), [], (listed, sw, hw))
        with mock.patch.dict(hwdec.os.environ, {"FUTSAL_HWDEC": "0"}), mock.patch.object(hwdec.sys, "platform", "win32"):
            self.assertEqual(hwdec.input_opts("hlg", "a.mov"), [])

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


if __name__ == "__main__":
    unittest.main()
