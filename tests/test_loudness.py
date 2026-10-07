"""가편집 소리 크기 목표·검수 (E6) — 저장소 폴더에서 python3 -m unittest tests.test_loudness

배운 스타일의 소리 크기(레퍼런스 원본을 잰 값, 예: -27 LUFS)를 그대로 목표로 쓰면 완성본이 유튜브에서 8~10dB 작게 나오는데
검수는 '목표대로 맞췄어요' 100점을 주던 문제: 가편집 목표는 -16~-13 안(-14~-13, 한 번 맞추기가 1dB 남짓 모자라도 -16 밑으로 안 가게)으로 묶고,
검수는 목표와 상관없이 -16 보다 작으면 경고.
검수는 ffmpeg 출력 글을 흉내 내서 보고(영상 없음), 내보내기 실측은 진짜 ffmpeg 로 짧은 시험 영상을 만들어 잼."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402
import qa  # noqa: E402
import style  # noqa: E402

LEARNED = {"lufs": -27.1, "pauseP75": 0.9, "zoomCutsPerMin": 0.42, "avgZoom": 1.21, "captionRatio": 0.07, "captionPos": "bottom",
           "captionColor": "#FFFFFF", "medianShot": 4.5, "curve3": [1.5, 5.75, 7.25], "charsPerSec": 3.28}  # MSGRAW01·03 으로 배운 값


def ff_log(lufs, peak=-2.0, w=1920, h=1080, dur="00:01:00.00"):
    """qa.check_video 가 읽는 ffmpeg 출력 글 (ebur128 요약 + 영상·소리 줄)."""
    return (f"  Duration: {dur}, start: 0.000000, bitrate: 4000 kb/s\n"
            f"  Stream #0:0[0x1](und): Video: h264 (High), yuv420p, {w}x{h}, 30 fps, 30 tbr\n"
            "  Stream #0:1[0x2](und): Audio: aac (LC), 48000 Hz, stereo, fltp, 192 kb/s\n"
            "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n"
            f"    I:         {lufs:.1f} LUFS\n    Threshold: -30.0 LUFS\n\n  True peak:\n    Peak:       {peak:.1f} dBFS\n")


def check(lufs, master, peak=-2.0):
    log = ff_log(lufs, peak)
    with mock.patch.object(core, "set_progress"):
        return qa.check_video("x.mp4", "long", master, run=lambda cmd: subprocess.CompletedProcess(cmd, 0, "", log))


def sound(r):
    return [(i["lv"], i["title"], i["msg"]) for i in r["items"] if "소리" in i["title"]]


class TargetTest(unittest.TestCase):
    def test_auto_lufs_band(self):
        for v, want in ((-27.1, -14.0), (-21.9, -14.0), (-14.45, -14.0), (-13.45, -13.4), (-14.0, -14.0), (-9.0, -13.0), (None, -14.0),
                        ("x", -14.0), (float("nan"), -14.0), (float("-inf"), -14.0)):
            with self.subTest(v=v):
                self.assertEqual(editor.auto_lufs(v), want)

    def test_learned_quiet_style_targets_youtube_band(self):
        # 재현: MSGRAW01·03 으로 배운 스타일(lufs=-27.1) → 예전에는 edit_params 가 그대로 넘기고 가편집 master.lufs=-24.0
        p = style.edit_params(LEARNED)
        self.assertEqual(p["lufs"], -14.0)
        self.assertEqual(style.edit_params(dict(LEARNED, lufs=-13.45))["lufs"], -13.45)  # 범위 안이면 배운 값 그대로
        self.assertEqual(style.edit_params(dict(LEARNED, lufs=None))["lufs"], -14.0)


class AutoSeqTest(unittest.TestCase):
    name = "드릴 레슨.mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="소리 크기 테스트 "))
        work = self.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        for p in self.patches:
            p.start()
        d = core.adir(self.name)
        d.mkdir(parents=True, exist_ok=True)
        segs = [{"start": 1.0 + 4 * k, "end": 4.0 + 4 * k, "text": f"{k + 1}번째 설명 문장이에요 패스하고 뛰어요"} for k in range(12)]
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": []}), encoding="utf-8")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_style_rough_cut_master(self):
        info = {"duration": 50.0, "width": 1920, "height": 1080, "fps": 30.0}
        for prof_lufs, want in ((-27.1, -14.0), (-21.9, -14.0), (-14.45, -14.0), (-13.45, -13.4), (-7.0, -13.0)):
            p = style.edit_params(dict(LEARNED, lufs=prof_lufs))
            for q in editor.auto_sequences(self.name, info, p):
                self.assertEqual(q["master"]["lufs"], want, (prof_lufs, q["name"]))
        # 스타일 값을 직접 넘겨도(예전 저장본·MSG 아닌 다른 길) 가편집은 범위 안
        for q in editor.auto_sequences(self.name, info, {"lufs": -24.0}):
            self.assertEqual(q["master"]["lufs"], -14.0)
        for q in editor.auto_sequences(self.name, info):
            self.assertEqual(q["master"], {"volume": 1.0, "normalize": True, "lufs": -14.0})


class QaLoudnessTest(unittest.TestCase):
    def test_quiet_export_warns_even_when_target_met(self):
        # 재현: 목표 -24 로 -23.9 LUFS 가 나왔는데 '100점 · 목표(-24)대로 맞췄어요. 유튜브는 -14 근처로 맞춰 틀어요'
        r = check(-23.9, {"normalize": True, "lufs": -24})
        self.assertLess(r["score"], 100)
        (lv, title, msg), = sound(r)
        self.assertEqual((lv, title), ("warn", "소리가 작아요"))
        self.assertIn("키워 주지 않아서", msg)
        self.assertIn("-14로 바꿔", msg)
        self.assertNotIn("목표(-24)대로", msg)

    def test_quiet_without_normalize_or_missed_target(self):
        r = check(-21.9, None)
        self.assertEqual(sound(r)[0][:2], ("warn", "소리가 작아요"))
        self.assertIn("켜고", sound(r)[0][2])
        r = check(-16.5, {"normalize": True, "lufs": -14})  # 목표 -14 인데 한 번 맞추기로 모자람 → 경고 하나만
        self.assertEqual(len(sound(r)), 1)
        self.assertEqual(sound(r)[0][:2], ("warn", "소리가 작아요"))
        self.assertIn("2.5dB 작게", sound(r)[0][2])
        self.assertIn("목표를 -12로 올려", sound(r)[0][2])  # 모자란 만큼 올리라고 (같은 목표로 다시 내보내면 같은 결과) · -12 까지만 (E6 검토)
        r = check(-16.1, {"normalize": True, "lufs": -15})  # 실측: MSGRAW01 전체 목표 -15 → -16.1
        self.assertIn("목표를 -14로 올려", sound(r)[0][2])

    def test_youtube_band_ok(self):
        for lufs, master in ((-14.1, {"normalize": True, "lufs": -14}), (-15.8, {"normalize": True, "lufs": -16}), (-13.2, None)):
            with self.subTest(lufs=lufs):
                r = check(lufs, master)
                self.assertEqual(r["score"], 100, sound(r))
                self.assertEqual(sound(r)[0][0], "ok")

    def test_loud_and_peak(self):
        r = check(-9.0, {"normalize": True, "lufs": -9})
        self.assertEqual(sound(r)[0][:2], ("warn", "소리가 커요"))
        r = check(-14.0, {"normalize": True, "lufs": -14}, peak=-0.8)  # 유튜브 권장 -1 dBTP 를 넘음
        self.assertIn(("warn", "소리 깨짐 가능"), [x[:2] for x in sound(r)])
        r = check(-14.0, {"normalize": True, "lufs": -14}, peak=-1.5)
        self.assertEqual([x[0] for x in sound(r)], ["ok"])

    def test_short_of_target_by_more_than_1db_is_not_on_target(self):
        # E6 검토 재현: feat_e6 가편집 실측 -15.3 · -15.6 LUFS (목표 -14) 가 100점 '목표(-14)대로 맞췄어요' — 유튜브는 키워 주지 않으므로 1dB 넘게 모자라면 경고
        for lufs in (-15.3, -15.6, -15.9):
            with self.subTest(lufs=lufs):
                r = check(lufs, {"normalize": True, "lufs": -14})
                (lv, title, msg), = sound(r)
                self.assertEqual((lv, title), ("warn", "소리 크기"))
                self.assertNotIn("대로 맞췄어요", msg)
                self.assertIn(f"{-14 - lufs:.1f}dB 작게", msg)
                self.assertLess(r["score"], 100)
        r = check(-14.9, {"normalize": True, "lufs": -14})  # 1dB 안은 목표대로
        self.assertEqual(sound(r)[0][:2], ("ok", "소리 크기"))
        r = check(-12.8, {"normalize": True, "lufs": -14})  # 1dB 넘게 크면 그것도 알려 줌
        self.assertEqual(sound(r)[0][:2], ("warn", "소리 크기"))

    def test_raise_advice_never_above_minus_12(self):
        # 모자란 정도는 영상마다 달라서 따라 하면 다른 영상이 -11 근처로 커질 수 있음 → 안내는 -12 까지만
        for lufs, tgt, want in ((-16.5, -14, -12), (-19.0, -14, -12), (-15.4, -14, -12), (-16.1, -15, -14)):
            with self.subTest(lufs=lufs, tgt=tgt):
                msg = sound(check(lufs, {"normalize": True, "lufs": tgt}))[0][2]
                self.assertIn(f"목표를 {want}로 올려", msg)

    def test_target_mismatch_inside_band(self):
        r = check(-12.5, {"normalize": True, "lufs": -15})
        self.assertEqual(sound(r)[0][:2], ("warn", "소리 크기"))


class ExportLoudnessTest(unittest.TestCase):
    """진짜 ffmpeg: 작게 녹음된(-30 LUFS 안팎) 시험 영상을 배운 스타일(-27) 가편집 목표로 내보내면 -14 LUFS 근처 · 최대 피크 -1 dBTP 이하."""

    def test_quiet_source_export_lands_in_band(self):
        tmp = Path(tempfile.mkdtemp(prefix="소리 내보내기 "))
        try:
            work = tmp / "작업"
            dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
            for d in dirs.values():
                d.mkdir(parents=True, exist_ok=True)
            with mock.patch.multiple(core, **dirs):
                name = "작은 소리.mp4"
                r = core.run([core.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=8",
                              "-f", "lavfi", "-i", "sine=f=440:d=8,volume=0.03", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                              "-c:a", "aac", "-b:a", "128k", str(core.VIDEOS / name)])
                self.assertEqual(r.returncode, 0, r.stderr)
                info = editor.media_info(name)
                seq = editor.auto_sequences(name, info, style.edit_params(LEARNED), ("long",))[0]
                self.assertEqual(seq["master"]["lufs"], -14.0)
                proj = dict(seq, captions=[], info=info, source=name,
                            media=[{"id": "main", "kind": "video", "src": "videos", "file": name, "dur": info["duration"], "w": info["width"],
                                    "h": info["height"], "fps": 30.0, "audio": True}])
                out = editor.export(name, proj, {"preset": "youtube", "fps": 30, "srt": False, "xml": False, "hw": False}, lambda *a: None)
                f = core.OUT / out[0]
                err = core.run([core.ffmpeg(), "-hide_banner", "-nostats", "-i", str(f), "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
                lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])
                peak = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)[-1])
                self.assertAlmostEqual(lufs, -14.0, delta=1.0)
                self.assertGreater(lufs, qa.QUIET, "검수 기준(-16)보다 큼")
                self.assertLessEqual(peak, -1.0)
                with mock.patch.object(core, "set_progress"):
                    res = qa.check_video(f, "long", editor.EXPORT_META[out[0]]["master"])
                self.assertEqual([i["lv"] for i in res["items"] if "소리" in i["title"]], ["ok"], res["items"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
