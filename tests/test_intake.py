"""보관함에 들어오는 영상(intake.py) 테스트 — 복사 중인 촬영본 · 아직 못 쓰는 형식(.MTS) · 편집점을 찾은 뒤 바뀐 파일.

크기·수정 시각을 몇 초 지켜보는 판단(시계는 바꿔 끼움) · Windows 쓰기 손잡이 확인(흉내) · /api/state 의 copying·changed·unusable
· 복사 중이면 /api/analyze·/api/bundle 거절 · core.analyze 직전 확인(덜 복사된 채 자라는 파일) · 편집점을 찾을 때의 크기 기록.
실행: 저장소 폴더에서 python3 -m unittest tests.test_intake
"""
import json
import os
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
import core  # noqa: E402
import intake  # noqa: E402
import trouble  # noqa: E402


class Clock:
    """intake 의 시계 흉내 (monotonic·벽시계를 함께 움직임)."""

    def __init__(self):
        self.t, self.w = 1000.0, time.time()

    def mono(self):
        return self.t

    def wall(self):
        return self.w

    def tick(self, s):
        self.t += s
        self.w += s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="들어오는 영상 "))
        work = self.tmp / "풋살 작업 폴더"
        self.dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.videos = self.dirs["VIDEOS"]
        self.clock = Clock()
        self.patches = [mock.patch.object(core, k, v) for k, v in self.dirs.items()]
        self.patches += [mock.patch.object(intake, "_mono", self.clock.mono), mock.patch.object(intake, "_wall", self.clock.wall),
                         mock.patch.object(intake, "_writer_open", return_value=False)]
        for p in self.patches:
            p.start()
        intake._SEEN.clear()
        intake._SIGS.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        intake._SEEN.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def growing(self, name, observe=None):
        """복사 중인 파일: 한 번 본 뒤(observe: 목록을 한 번 부르는 함수) 1초 뒤 자람."""
        p = self.put(name, 100, age=0)
        (observe or (lambda: intake.copying(p)))()
        self.clock.tick(1)
        return self.put(name, 5000, age=0)

    def put(self, name, size, age=3600):
        """보관함에 파일 (age: 수정 시각이 몇 초 전인지)."""
        p = self.videos / name
        with open(p, "ab") as f:
            f.truncate(size)
        t = self.clock.wall() - age
        os.utime(p, (t, t))
        return p


class CopyingTests(Base):
    def test_growing_file_is_copying_until_stable_for_seconds(self):
        """앞 20MB 만 들어온 81MB 촬영본: 자라는 동안은 '복사 중', 크기가 STABLE 초 그대로면 다 들어온 것."""
        p = self.put("촬영 원본 [1편].MP4", 20_000_000, age=0)
        self.assertFalse(intake.copying(p), "처음 본 파일은 기다리지 않음 (막 받은·묶은 영상) — 자라면 다음 관찰에서")
        for size in (40_000_000, 60_000_000, 81_000_000):
            self.clock.tick(1)
            self.put(p.name, size, age=0)
            self.assertTrue(intake.copying(p))
        self.clock.tick(intake.STABLE - 0.5)
        self.assertTrue(intake.copying(p), "아직 몇 초가 안 지남")
        self.clock.tick(1)
        self.assertFalse(intake.copying(p), "몇 초 동안 그대로 → 다 들어옴")

    def test_old_file_seen_first_time_is_ready_at_once(self):
        """앱을 켰을 때 이미 있던 영상은 기다리지 않음 (모두 '복사 중'이 되면 안 됨)."""
        p = self.put("예전 영상.mp4", 5000, age=3600)
        self.assertFalse(intake.copying(p))

    def test_writer_handle_means_copying_even_if_size_is_fixed(self):
        """Windows 탐색기는 처음에 크기를 다 잡아 둠 → 크기가 그대로여도 쓰는 프로그램이 있으면 복사 중."""
        p = self.put("탐색기 복사.mp4", 81_000_000, age=3600)
        with mock.patch.object(intake, "_writer_open", return_value=True) as w:
            self.assertTrue(intake.copying(p))
            self.clock.tick(10)
            self.assertTrue(intake.copying(p))
        self.assertGreaterEqual(w.call_count, 2)
        with mock.patch.object(intake, "_writer_open", return_value=False) as w2:
            self.assertFalse(intake.copying(p))
            self.assertFalse(intake.copying(p))
        self.assertEqual(w2.call_count, 1, "다 들어온 것을 확인한 뒤에는 다시 열어 보지 않음")

    def test_change_after_ready_goes_back_to_copying(self):
        p = self.put("덮어쓰기.mp4", 1000, age=3600)
        self.assertFalse(intake.copying(p))
        self.put(p.name, 9000, age=0)
        self.assertTrue(intake.copying(p), "같은 이름으로 다시 복사하면 다시 지켜봄")

    def test_future_mtime_from_camera_clock_is_not_copying_forever(self):
        p = self.put("시계 틀린 카메라.mp4", 1000, age=-86400)
        self.assertFalse(intake.copying(p))

    def test_new_file_not_copying_at_first_sight(self):
        """막 받은 영상·막 묶은 영상(방금 생김)은 '복사 중'으로 깜빡이지 않음 — Windows 는 쓰기 손잡이로 확인."""
        p = self.put("20240101_abcdefghijk_막 받은 영상.mp4", 5000, age=0)
        self.assertFalse(intake.copying(p))
        self.clock.tick(1)
        self.assertFalse(intake.copying(p), "그대로면 계속 다 들어온 것")

    def test_forget_drops_removed_files(self):
        p = self.put("지울 영상.mp4", 10, age=0)
        intake.copying(p)
        intake.forget([])
        self.assertEqual(intake._SEEN, {})

    def test_busy_probe_before_analysis(self):
        """편집점 찾기 직전: 방금 바뀐 파일은 잠깐 지켜보고, 그사이 자라면 복사 중."""
        p = self.put("자라는 중.mp4", 1000, age=0)
        grow = lambda s: self.put(p.name, 2000, age=0)  # noqa: E731 — 지켜보는 사이 자람
        with mock.patch.object(intake, "PROBE", 0.01), mock.patch.object(intake, "_sleep", side_effect=grow):
            self.assertTrue(intake.busy(p))
        with mock.patch.object(intake, "PROBE", 0.01), mock.patch.object(intake, "_sleep") as sl:
            self.assertFalse(intake.busy(self.put("오래된.mp4", 10, age=3600)))
        sl.assert_not_called()
        with mock.patch.object(intake, "_writer_open", return_value=True):
            self.assertTrue(intake.busy(self.put("쓰는 중.mp4", 10, age=3600)))
        self.assertFalse(intake.busy(self.videos / "없음.mp4"))


class UnusableTests(Base):
    def test_mts_and_avi_listed_as_unusable_not_hidden(self):
        self.put("00001.MTS", 2_500_000)
        self.put("옛날 영상.avi", 1000)
        self.put("보통.mp4", 1000)
        (self.videos / "archive.txt").write_text("youtube x\n", encoding="utf-8")
        (self.videos / "폴더.mts").mkdir()
        got = intake.unusable(self.videos)
        self.assertEqual([(u["name"], u["ext"]) for u in got], [("00001.MTS", "MTS"), ("옛날 영상.avi", "AVI")])
        self.assertEqual(got[0]["size_mb"], 2.5)
        self.assertEqual([v["name"] for v in core.local_videos()], ["보통.mp4"], "쓸 수 있는 목록은 그대로")
        self.assertTrue(intake.UNUSABLE_EXTS.isdisjoint(core.VIDEO_EXTS))


class ChangedTests(Base):
    def test_size_recorded_at_analysis_then_changed_flag(self):
        out = self.dirs["ANALYSIS"] / "촬영"
        out.mkdir()
        p = self.put("촬영.mp4", 20_000_000)
        intake.remember(out, intake.sig(p))
        self.assertFalse(intake.changed(out, 20_000_000))
        self.assertTrue(intake.changed(out, 81_000_000), "덜 복사된 채 편집점을 찾은 뒤 다 들어옴")
        self.assertFalse(intake.changed(self.dirs["ANALYSIS"] / "예전 분석", 5), "기록이 없는 예전 분석은 모름")
        (out / intake.SIG_FILE).write_text("깨짐", encoding="utf-8")
        os.utime(out / intake.SIG_FILE, (time.time() + 5, time.time() + 5))
        self.assertFalse(intake.changed(out, 81_000_000))

    def test_annotate_marks_copying_and_changed(self):
        self.growing("복사 중.mp4")
        done = self.put("다 됨.mp4", 300)
        (core.adir(done.name)).mkdir(parents=True)
        (core.adir(done.name) / "transcript_timeline.md").write_text("# 타임라인\n", encoding="utf-8")
        intake.remember(core.adir(done.name), {"size": 100})
        rows = intake.annotate(core.local_videos(), core.VIDEOS, core.adir)
        by = {r["name"]: r for r in rows}
        self.assertTrue(by["복사 중.mp4"].get("copying"))
        self.assertTrue(by["다 됨.mp4"]["analyzed"])
        self.assertTrue(by["다 됨.mp4"].get("changed"))
        self.assertNotIn("copying", by["다 됨.mp4"])


class AnalyzeGuardTests(Base):
    def test_core_analyze_refuses_file_still_copying(self):
        self.put("반쪽.mp4", 1000, age=0)
        with mock.patch.object(intake, "busy", return_value=True), mock.patch.object(core, "_analyze") as an:
            with self.assertRaises(trouble.Trouble) as cm:
                core.analyze_many(["반쪽.mp4"], lambda m: None)
        an.assert_not_called()
        self.assertEqual(cm.exception.info["kind"], "copying")
        self.assertIn("복사 중", str(cm.exception))

    def test_core_analyze_checks_all_names_before_starting(self):
        self.put("a.mp4", 10)
        self.put("b.mp4", 10, age=0)
        with mock.patch.object(intake, "busy", side_effect=lambda p: Path(p).name == "b.mp4"), \
                mock.patch.object(core, "_analyze") as an:
            with self.assertRaises(trouble.Trouble):
                core.analyze_many(["a.mp4", "b.mp4"], lambda m: None)
        an.assert_not_called()

    def test_core_analyze_remembers_size(self):
        p = self.put("다 들어온 영상.mp4", 4321)
        out = core.adir(p.name)

        def fake(name, log, model, step):
            out.mkdir(parents=True, exist_ok=True)
            return out
        with mock.patch.object(core, "_analyze", side_effect=fake):
            core.analyze(p.name, lambda m: None)
        self.assertEqual(json.loads((out / intake.SIG_FILE).read_text(encoding="utf-8")), {"size": 4321})


class RouteTests(Base):
    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        for _ in range(200):
            if not self.app.JOB["name"]:
                break
            time.sleep(0.05)
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2:
            p.stop()
        super().tearDown()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_state_shows_copying_and_unusable(self):
        self.put("다 들어옴.mp4", 100)
        self.put("00001.MTS", 100)
        self.growing("들어오는 중.mp4", lambda: self.call("/api/state?since=0"))
        code, s = self.call("/api/state?since=0")
        self.assertEqual(code, 200)
        by = {v["name"]: v for v in s["local"]}
        self.assertTrue(by["들어오는 중.mp4"]["copying"])
        self.assertNotIn("copying", by["다 들어옴.mp4"])
        self.assertEqual([u["name"] for u in s["unusable"]], ["00001.MTS"])
        self.assertNotIn("00001.MTS", by, "못 쓰는 형식은 고를 수 있는 목록에 없음")

    def test_analyze_and_bundle_refused_while_copying(self):
        self.put("다른 영상.mp4", 100)
        self.growing("들어오는 중.mp4", lambda: self.call("/api/state?since=0"))
        code, j = self.call("/api/analyze", {"names": ["들어오는 중.mp4"]})
        self.assertEqual((code, j["ok"]), (409, False))
        self.assertIn("복사 중", j["error"])
        self.assertEqual(j["fail"]["kind"], "copying")
        self.assertEqual(j["copying"], ["들어오는 중.mp4"])
        self.assertIsNone(self.app.JOB["name"], "작업을 시작하지 않음")
        code, j = self.call("/api/bundle", {"names": ["들어오는 중.mp4", "다른 영상.mp4"]})
        self.assertEqual((code, j["ok"], j["fail"]["kind"]), (409, False, "copying"))
        self.clock.tick(intake.STABLE + 1)
        with mock.patch.object(core, "analyze_many", return_value=[]) as an, \
                mock.patch("editor.reanalyze_project", return_value=({"sequences": []}, 0, False)), \
                mock.patch("editor.thumbs"), mock.patch("editor.waveform"):
            code, j = self.call("/api/analyze", {"names": ["들어오는 중.mp4"]})
            self.assertEqual((code, j["ok"]), (200, True), "다 들어오면 됨")
            for _ in range(200):
                if not self.app.JOB["name"]:
                    break
                time.sleep(0.05)
        an.assert_called_once()


if __name__ == "__main__":
    unittest.main()
