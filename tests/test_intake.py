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
    def test_mtime_change_with_same_size_is_changed(self):
        """탐색기는 복사를 시작할 때 크기를 다 잡아 두고 끝날 때 원래 수정 시각을 붙임 → 크기가 같아도 시각이 2초 넘게 다르면 바뀜."""
        out = self.dirs["ANALYSIS"] / "탐색기 복사"
        out.mkdir()
        p = self.put("탐색기 복사.mp4", 81_000_000, age=0)  # 복사 중: 크기는 처음부터 다 잡힘 · 시각은 지금
        intake.remember(out, intake.sig(p))
        self.assertFalse(intake.changed(out, p.stat()))
        t = p.stat().st_mtime + 1.5  # 2초 안: 같은 것으로 (FAT 2초 단위·폴더 옮기기)
        os.utime(p, (t, t))
        self.assertFalse(intake.changed(out, p.stat()))
        self.put(p.name, 81_000_000, age=86400)  # 복사가 끝나며 원래(하루 전) 수정 시각
        self.assertTrue(intake.changed(out, p.stat()))

    def test_old_size_only_record_still_works(self):
        out = self.dirs["ANALYSIS"] / "예전 기록"
        out.mkdir()
        p = self.put("예전 기록.mp4", 500)
        (out / intake.SIG_FILE).write_text(json.dumps({"size": 500}), encoding="utf-8")
        self.assertFalse(intake.changed(out, p.stat()), "크기만 남긴 예전 기록은 크기만 비교")
        self.put(p.name, 900)
        self.assertTrue(intake.changed(out, p.stat()))

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


class MessageTests(Base):
    def test_copying_message_has_no_mechanical_particle(self):
        msg = intake.copying_msg("복사 중 촬영본 1280x657.mp4")
        self.assertNotIn("은(는)", msg)
        self.assertIn("'복사 중 촬영본 1280x657.mp4' 파일은 아직 복사 중이거나 다른 프로그램이 쓰고 있어요", msg)


def _ts_ffmpeg():
    """MPEG-TS(.MTS)를 읽을 수 있는 ffmpeg — 앱의 ffmpeg 가 먼저, 개발 PC 의 imageio 정적 빌드(7.0.2)는 .ts 를 열면 죽으므로
    그때는 시스템 ffmpeg (둘 다 없으면 None → 건너뜀)."""
    d = Path(tempfile.mkdtemp(prefix="ts 확인 "))
    try:
        for ff in (core.ffmpeg(), shutil.which("ffmpeg")):
            if not ff:
                continue
            t = d / "t.ts"
            core.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "color=s=32x18:d=0.2", "-c:v", "libx264", "-f", "mpegts", str(t)])
            if t.exists() and intake._probe_codecs(ff, t)[0] == "h264":
                return ff
        return None
    finally:
        shutil.rmtree(d, ignore_errors=True)


TS_FF = _ts_ffmpeg()


@unittest.skipUnless(TS_FF, "MPEG-TS 를 읽는 ffmpeg 가 없음")
class ConvertTests(Base):
    """못 쓰는 형식(.MTS) → MP4 (실제 ffmpeg) · 원본은 보관함 안 '바꾸기 전 원본' 폴더로."""

    def make_mts(self, name, vcodec="libx264", acodec="ac3"):
        p = self.videos / name
        r = core.run([TS_FF, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=96x54:d=2", "-f", "lavfi", "-i",
                      "sine=frequency=300:duration=2", "-shortest", "-c:v", vcodec, "-c:a", acodec, "-f", "mpegts", str(p)])
        self.assertEqual(r.returncode, 0, r.stderr)
        return p

    def codecs(self, p):
        return intake._probe_codecs(TS_FF, p)[:2]

    def test_mts_with_ac3_becomes_h264_aac_mp4(self):
        src = self.make_mts("00001.MTS")
        logs, prog = [], []
        out = intake.convert(self.videos, src.name, TS_FF, logs.append, lambda pct, d: prog.append(pct))
        self.assertEqual(out, "00001.mp4")
        self.assertEqual(self.codecs(self.videos / out), ("h264", "aac"))
        self.assertFalse(src.exists())
        self.assertTrue((self.videos / intake.CONVERTED / "00001.MTS").exists(), "원본은 지우지 않고 옮김")
        self.assertEqual(intake.unusable(self.videos), [], "못 쓰는 줄이 사라짐")
        self.assertEqual([v["name"] for v in core.local_videos()], ["00001.mp4"], "편집할 수 있는 목록에 들어옴 (만드는 중 파일은 안 보임)")
        self.assertTrue(any("화질 그대로" in m for m in logs), logs)

    def test_non_h264_is_reencoded_and_name_kept_unique(self):
        (self.videos / "옛날 영상.mp4").write_bytes(b"x")
        src = self.make_mts("옛날 영상.mpg", vcodec="mpeg2video", acodec="mp2")
        out = intake.convert(self.videos, src.name, TS_FF, lambda m: None)
        self.assertEqual(out, "옛날 영상 (MP4).mp4")
        self.assertEqual(self.codecs(self.videos / out), ("h264", "aac"))

    def test_broken_file_fails_and_keeps_original(self):
        (self.videos / "깨진.MTS").write_bytes(os.urandom(3000))
        with self.assertRaises(RuntimeError) as cm:
            intake.convert(self.videos, "깨진.MTS", TS_FF, lambda m: None)
        self.assertIn("'깨진.MTS'", str(cm.exception))
        self.assertTrue((self.videos / "깨진.MTS").exists())
        self.assertEqual([v["name"] for v in core.local_videos()], [])

    def test_cancel_removes_partial_file(self):
        src = self.make_mts("멈출 영상.MTS", vcodec="mpeg2video")
        with self.assertRaises(RuntimeError) as cm:
            intake.convert(self.videos, src.name, TS_FF, lambda m: None, cancel=lambda: True)
        self.assertIn("멈췄어요", str(cm.exception))
        self.assertTrue(src.exists())
        self.assertEqual(list((self.videos / intake.CONVERTED).glob("*.part.mp4")), [])
        self.assertEqual([v["name"] for v in core.local_videos()], [])


class AnalyzeManyTests(Base):
    """여러 영상 편집점 찾기: 깨진 파일 하나는 건너뛰고 나머지를 계속 · 메모리처럼 다음 영상도 같을 문제는 멈추고 남은 것만 다시."""

    def run_many(self, names, fail):
        for n in names:
            self.put(n, 10)

        def fake(name, log, model, step):
            if name in fail:
                raise fail[name]
            d = core.adir(name)
            d.mkdir(parents=True, exist_ok=True)
            return d
        with mock.patch.object(core, "_analyze", side_effect=fake):
            out = core.analyze_many(names, lambda m: None)
        return out, out.failed

    def test_broken_file_skipped_rest_done(self):
        bad = core.FileProblem("'b.mp4'에서 소리를 꺼내지 못했어요 (파일이 깨졌거나 소리가 없는 영상일 수 있어요) · moov atom not found")
        out, failed = self.run_many(["a.mp4", "b.mp4", "c.mp4"], {"b.mp4": bad})
        self.assertEqual([Path(o).name for o in out], ["a", "c"])
        self.assertEqual(list(failed), ["b.mp4"])
        self.assertEqual(failed["b.mp4"]["kind"], "broken")
        self.assertIn("'b.mp4'", failed["b.mp4"]["msg"], "어느 영상인지 이름")

    def test_all_broken_is_job_failure(self):
        bad = lambda n: core.FileProblem(f"'{n}'에서 소리를 꺼내지 못했어요 (파일이 깨졌거나 소리가 없는 영상일 수 있어요) · Invalid data")  # noqa: E731
        with self.assertRaises(trouble.Trouble) as cm:
            self.run_many(["a.mp4", "b.mp4"], {"a.mp4": bad("a.mp4"), "b.mp4": bad("b.mp4")})
        self.assertIn("2개 모두 편집점을 찾지 못했어요", str(cm.exception))
        self.assertNotIn("retry", cm.exception.info["actions"], "다시 해도 같음")

    def test_systemic_error_stops_with_remaining_names(self):
        with self.assertRaises(MemoryError) as cm:
            self.run_many(["a.mp4", "b.mp4", "c.mp4"], {"b.mp4": MemoryError()})
        self.assertEqual(cm.exception.retry, {"names": ["b.mp4", "c.mp4"]})
        self.assertIn("3개 중 1개는 끝났어요", cm.exception.note)

    def test_single_file_raises_as_before(self):
        bad = core.FileProblem("'a.mp4'에서 소리를 꺼내지 못했어요 (파일이 깨졌거나 소리가 없는 영상일 수 있어요) · x")
        with self.assertRaises(core.FileProblem):
            self.run_many(["a.mp4"], {"a.mp4": bad})


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
        self.assertEqual(json.loads((out / intake.SIG_FILE).read_text(encoding="utf-8")), {"size": 4321, "mtime_ns": p.stat().st_mtime_ns})

    def test_reanalysis_of_changed_file_clears_partial_media_cache(self):
        """반쪽 파일로 만든 파형·썸네일·포스터·미리보기 파일은 바뀐 파일로 편집점을 다시 찾을 때 지움 (편집실이 새로 만듦)."""
        p = self.put("반쪽으로 찾은 영상.mp4", 1000)
        out = core.adir(p.name)
        out.mkdir(parents=True)
        intake.remember(out, intake.sig(p))
        names = ["waveform_50.json", "thumbs2.json", "thumbs2.jpg", "poster.jpg", "proxy.mp4", "transcript.json", "project_keep.txt"]
        for n in names:
            (out / n).write_text("x", encoding="utf-8")
        (out / "rev").mkdir()
        (out / "rev" / "rev_0.000_1.000_1.0000.wav").write_text("x", encoding="utf-8")
        logs = []

        def fake(name, log, model, step):
            return out
        with mock.patch.object(core, "_analyze", side_effect=fake):
            core.analyze(p.name, logs.append)  # 그대로인 파일 → 캐시는 그대로
        self.assertTrue((out / "waveform_50.json").exists())
        self.put(p.name, 4000)  # 복사가 끝나 자람
        with mock.patch.object(core, "_analyze", side_effect=fake):
            core.analyze(p.name, logs.append)
        left = sorted(x.name for x in out.iterdir())
        self.assertEqual(left, sorted(["transcript.json", "project_keep.txt", intake.SIG_FILE]), left)
        self.assertTrue(any("예전 파형·썸네일" in m for m in logs), logs)
        self.assertFalse(intake.changed(out, p.stat()), "새 기록 → 칩 사라짐")

    def test_waveform_follows_grown_file_after_reanalysis(self):
        """실제 ffmpeg: 10초 → 40초로 자란 파일을 다시 찾으면 편집실 파형도 40초 (예전엔 10초짜리 캐시가 남았음)."""
        import editor
        p = self.videos / "자라는 촬영본.mp4"
        mk = lambda sec: core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={sec}",  # noqa: E731
                                   "-f", "lavfi", "-i", f"color=c=blue:s=64x36:d={sec}", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(p)])
        mk(10)
        out = core.adir(p.name)
        out.mkdir(parents=True, exist_ok=True)
        intake.remember(out, intake.sig(p))
        self.assertAlmostEqual(len(editor.waveform(p.name)["peaks"]) / 50, 10, delta=0.5)
        mk(40)
        with mock.patch.object(core, "_analyze", side_effect=lambda *a: out):
            core.analyze(p.name, lambda m: None)
        self.assertAlmostEqual(len(editor.waveform(p.name)["peaks"]) / 50, 40, delta=0.5)


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

    def test_analyze_partial_failure_result_and_retry_only_failed(self):
        for n in ("좋은.mp4", "깨진.mp4"):
            self.put(n, 100)
        info = {"kind": "broken", "msg": "'깨진.mp4'에서 소리를 꺼내지 못했어요", "actions": ["folder", "retry"]}

        def many(names, log, model):
            out = core.Analyzed([str(core.adir("좋은.mp4"))])
            out.failed = {"깨진.mp4": info}
            return out
        with mock.patch.object(core, "analyze_many", side_effect=many), \
                mock.patch("editor.reanalyze_project", return_value=({"sequences": [{"name": "롱폼 가편집"}]}, 0, False)) as rp, \
                mock.patch("editor.thumbs"), mock.patch("editor.waveform"):
            self.assertEqual(self.call("/api/analyze", {"names": ["좋은.mp4", "깨진.mp4"]})[0], 200)
            for _ in range(200):
                if not self.app.JOB["name"]:
                    break
                time.sleep(0.05)
        s = self.call("/api/state?since=0")[1]
        self.assertIsNone(s["error"])
        self.assertEqual(s["result"]["failed"], ["깨진.mp4"])
        self.assertEqual(s["result"]["why"]["깨진.mp4"]["kind"], "broken")
        self.assertEqual([c.args[0] for c in rp.call_args_list], ["좋은.mp4"], "깨진 영상은 가편집을 만들지 않음")

    def test_convert_route(self):
        self.put("00001.MTS", 100)
        code, j = self.call("/api/convert", {"name": "../00001.MTS"})
        self.assertEqual(code, 400)
        code, j = self.call("/api/convert", {"name": "없는.MTS"})
        self.assertEqual(code, 404)
        with mock.patch.object(intake, "convert", return_value="00001.mp4") as cv:
            code, j = self.call("/api/convert", {"name": "00001.MTS"})
            self.assertEqual((code, j["ok"]), (200, True))
            for _ in range(200):
                if not self.app.JOB["name"]:
                    break
                time.sleep(0.05)
        self.assertEqual(cv.call_args[0][:2], (core.VIDEOS, "00001.MTS"))
        self.assertEqual(self.call("/api/state?since=0")[1]["result"], {"ok": True, "name": "00001.mp4"})

    def test_open_log_and_work_folder(self):
        with mock.patch.object(self.app, "reveal") as rv, mock.patch.object(self.app, "open_folder") as of:
            self.assertEqual(self.call("/api/open", {"which": "log"})[1], {"ok": True})
            self.assertEqual(self.call("/api/open", {"which": "work"})[1], {"ok": True})
        rv.assert_called_once_with(self.app.LOGFILE)
        of.assert_called_once_with(core.WORK)
        with mock.patch.object(self.app, "open_folder") as of2:  # 기록 파일이 아직 없으면 작업 폴더만
            self.app.reveal(self.tmp / "없는 studio.log")
        of2.assert_called_once_with(self.tmp)

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
