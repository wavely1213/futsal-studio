"""받아쓰기 기본값을 PC 사양에 맞추기 (D-070): 메모리 8GB 이하·4코어 이하는 '빠르게'(small) · 아는 모델만 · 예상 시간·메모리.

예전에는 메모리를 읽는 코드가 없고 보관함의 받아쓰기 고르기가 늘 '정확하게'(large-v3-turbo)로 시작해, 8GB 노트북에서
60분 영상이 약 48분·2GB(스왑)로 돌았다 ('빠르게'는 약 16분·0.8GB). 휴대폰에서 시킨 편집점 찾기도 늘 '정확하게'였다.
화면이 고른 값을 기억하는 것(localStorage)은 Playwright 로 따로 본다.
실행: python3 -m unittest tests.test_pc_model
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402
import core  # noqa: E402
import editor  # noqa: E402
from test_windows_compat import Server  # noqa: E402

LAPTOP_8GB = {"memGB": 7.8, "cores": 8}  # Windows 8GB 노트북은 7.7~7.9GB 로 보여 줌
DESKTOP = {"memGB": 31.9, "cores": 12}


class SpecTests(unittest.TestCase):
    def test_default_follows_memory_and_cores(self):
        cases = [(LAPTOP_8GB, "small"), ({"memGB": 15.7, "cores": 4}, "small"), ({"memGB": 15.7, "cores": 8}, "large-v3-turbo"),
                 (DESKTOP, "large-v3-turbo"), ({"memGB": None, "cores": 8}, "large-v3-turbo"), ({"memGB": None, "cores": 2}, "small"),
                 ({"memGB": 4.0, "cores": 16}, "small"), ({"memGB": 11.9, "cores": 6}, "large-v3-turbo")]
        for spec, want in cases:
            self.assertEqual(core.default_model(spec), want, spec)

    def test_reads_this_pc(self):
        spec = core.pc_spec()
        self.assertGreater(spec["cores"], 0)
        if sys.platform != "win32":  # Windows 는 GlobalMemoryStatusEx (실기 미검증)
            self.assertGreater(spec["memGB"], 0.5)
        self.assertIn(core.default_model(), core.WHISPER_COST)

    def test_only_known_models(self):
        """화면·휴대폰이 보낸 값은 아는 모델만 (없거나 모르는 값·경로 → 이 PC 기본값)."""
        with mock.patch.object(core, "pc_spec", return_value=DESKTOP):
            self.assertEqual(core.model_of("small"), "small")
            for v in (None, "", "medium", "../../evil", "C:\\models\\x", 3):
                self.assertEqual(core.model_of(v), "large-v3-turbo", v)
        with mock.patch.object(core, "pc_spec", return_value=LAPTOP_8GB):
            self.assertEqual(core.model_of(None), "small")
            self.assertEqual(core.model_of("large-v3-turbo"), "large-v3-turbo", "고른 값은 그대로")

    def test_estimate_from_measured_lesson(self):
        """60분 영상: '빠르게' 약 16분·0.8GB, '정확하게' 약 48분·2GB (4코어 실측) · 코어가 적으면 그만큼 느리게 · 많아도 더 빠르다고 안 함."""
        e = core.whisper_estimate(3600, {"memGB": 15.7, "cores": 4})
        self.assertEqual((e["models"]["small"]["secs"], e["models"]["large-v3-turbo"]["secs"]), (972, 2880))
        self.assertEqual((e["models"]["small"]["memMB"], e["models"]["large-v3-turbo"]["memMB"]), (765, 2048))
        self.assertEqual(e["models"]["small"]["perHour"], 972)
        self.assertEqual(e["default"], "small")
        self.assertEqual(core.whisper_estimate(3600, {"memGB": 15.7, "cores": 2})["models"]["small"]["secs"], 1944)
        self.assertEqual(core.whisper_estimate(3600, {"memGB": 15.7, "cores": 16})["models"]["small"]["secs"], 972)
        self.assertEqual(core.whisper_estimate(0, DESKTOP)["models"]["small"]["secs"], 0)

    def test_tight_memory_flag(self):
        """8GB 노트북에서 '정확하게'(2GB)는 빠듯함 표시 · '빠르게'는 아님 · 넉넉한 PC 는 둘 다 아님."""
        m = core.whisper_estimate(600, LAPTOP_8GB)["models"]
        self.assertEqual((m["large-v3-turbo"]["tight"], m["small"]["tight"]), (True, False))
        m = core.whisper_estimate(600, DESKTOP)["models"]
        self.assertEqual((m["large-v3-turbo"]["tight"], m["small"]["tight"]), (False, False))
        m = core.whisper_estimate(600, {"memGB": None, "cores": 4})["models"]
        self.assertFalse(m["large-v3-turbo"]["tight"], "메모리를 모르면 겁주지 않음")


class RouteTests(Server):
    def test_estimate_route_sums_selected_videos(self):
        self.video("레슨 1.mp4")
        self.video("레슨 [꿀팁] 2.mp4", b"\1" * 200)
        durs = {"레슨 1.mp4": 1800.0, "레슨 [꿀팁] 2.mp4": 1800.0}
        calls = []

        def probe(p):
            calls.append(Path(p).name)
            return {"duration": durs[Path(p).name]}
        with mock.patch.object(editor, "probe", side_effect=probe), mock.patch.object(core, "pc_spec", return_value=LAPTOP_8GB):
            st, j = self.call("/api/whisper/estimate", {"names": list(durs)})
            self.assertEqual(st, 200)
            self.assertEqual((j["ok"], j["default"], j["dur"], j["memGB"], j["cores"]), (True, "small", 3600.0, 7.8, 8))
            self.assertEqual(j["models"]["small"]["secs"], 972)
            self.assertTrue(j["models"]["large-v3-turbo"]["tight"])
            self.call("/api/whisper/estimate", {"names": list(durs)})
            self.assertEqual(sorted(calls), sorted(durs), "길이는 파일마다 한 번만 읽음 (고를 때마다 ffmpeg 를 안 부름)")
            st, j = self.call("/api/whisper/estimate", {"names": []})
            self.assertEqual((st, j["dur"], j["models"]["small"]["perHour"]), (200, 0.0, 972))
            st, j = self.call("/api/whisper/estimate", {"names": ["없는 영상.mp4"]})
            self.assertEqual((st, j["dur"]), (200, 0.0))
        for bad in (["..\\밖.mp4"], ["C:\\x.mp4"], "x", [1], ["a"] * 501):
            self.assertEqual(self.call("/api/whisper/estimate", {"names": bad})[0], 400, bad)

    def test_analyze_uses_pc_default_when_not_chosen(self):
        """편집점 찾기: 고른 값이 없거나 모르는 값이면 이 PC 기본값 (예전: 늘 large-v3-turbo · 아무 문자열이나 모델 이름으로)."""
        got = []

        def fake(names, log, model):
            got.append(model)
            out = core.Analyzed()
            out.failed = {n: {"kind": "broken", "msg": "x"} for n in names}
            return out
        with mock.patch.object(core, "analyze_many", side_effect=fake), mock.patch.object(core, "pc_spec", return_value=LAPTOP_8GB):
            for body in ({"names": ["a.mp4"]}, {"names": ["a.mp4"], "model": "small"}, {"names": ["a.mp4"], "model": "large-v3-turbo"},
                         {"names": ["a.mp4"], "model": "../../x"}):
                app.Handler._analyze(body)
        self.assertEqual(got, ["small", "small", "large-v3-turbo", "small"])


if __name__ == "__main__":
    unittest.main()
