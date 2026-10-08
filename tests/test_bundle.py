"""촬영본 묶음(bundle.py) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_bundle"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import bundle  # noqa: E402

FF = core.ffmpeg()


def ff(*args):
    r = core.run([FF, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)])
    if r.returncode:
        raise RuntimeError(r.stderr)


def make_clip(path, size="1920x1080", rate=30, dur=5, audio=True, created=None, rotate=None):
    """lavfi testsrc 로 시험용 영상 (rotate: 휴대폰 세로 영상처럼 회전 표시만 붙임)."""
    path = Path(path)
    tmp = path.with_name("_src_" + path.name) if rotate else path
    args = ["-f", "lavfi", "-i", f"testsrc=s={size}:r={rate}:d={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f=440:r=44100:d={dur}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-shortest"] if audio else []
    if created and not rotate:
        args += ["-metadata", f"creation_time={created}"]
    ff(*args, tmp)
    if rotate:  # 예전 'rotate=90' 표시와 같은 회전 정보 (ffmpeg 7 방식)
        ff("-display_rotation", -rotate, "-i", tmp, "-c", "copy", *(["-metadata", f"creation_time={created}"] if created else []), path)
        tmp.unlink()
    return path


def make_beep_clip(path, dur=2, adur=None, ar=48000, audio=True, created=None, drop_frame=False, size="320x240"):
    """클립 첫머리에 0.1초 삑 소리 (이어 붙인 뒤 소리가 제자리에 있는지 확인용). drop_frame: 휴대폰 가변 fps 처럼 10장면마다 하나씩 빠짐."""
    args = ["-f", "lavfi", "-i", f"testsrc=s={size}:r=30:d={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"aevalsrc='0.8*sin(2*PI*1000*t)*lt(t,0.1)':s={ar}:d={adur or dur}"]
    if drop_frame:
        args += ["-vf", "select='not(eq(mod(n\\,10)\\,5))'", "-fps_mode", "passthrough"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-video_track_timescale", "600"]
    args += ["-c:a", "aac"] if audio else []
    if created:
        args += ["-metadata", f"creation_time={created}"]
    ff(*args, path)
    return Path(path)


def analysis_wav(video, tmp):
    """편집점 찾기(core.analyze)와 똑같이 소리를 뽑음 → (16kHz 모노 샘플, 16000)."""
    import array
    import wave
    wav = Path(tmp) / "a.wav"
    ff("-i", video, "-vn", "-ac", "1", "-ar", "16000", wav)
    with wave.open(str(wav)) as w:
        return array.array("h", w.readframes(w.getnframes())), 16000


def beep_at(data, sr, lo, hi):
    """lo~hi 초 사이 처음 삑 소리가 나는 시각 (없으면 None)."""
    return next((j / sr for j in range(int(max(0, lo) * sr), min(int(hi * sr), len(data))) if abs(data[j]) > 8000), None)


def key_times(video):
    """화면 키프레임 시각들 (다시 풀지 않고 읽음)."""
    out = core.run([FF, "-hide_banner", "-loglevel", "error", "-i", str(video), "-map", "0:v:0", "-c", "copy", "-f", "framecrc", "-"]).stdout
    tb = re.search(r"#tb 0: (\d+)/(\d+)", out)
    k = int(tb[1]) / int(tb[2])
    return [int(ln.split(",")[2]) * k for ln in out.splitlines() if ln and ln[0] != "#" and "F=" not in ln]


def info(path):
    err = core.run([FF, "-hide_banner", "-i", str(path)]).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    vids = re.findall(r"Stream #\d+:\d+\S*: Video: (.*)", err)
    return {"duration": int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0, "videos": vids,
            "audio": bool(re.search(r"Stream #\d+:\d+\S*: Audio:", err)), "rotation": re.search(r"rotation of (-?[\d.]+)", err), "err": err}


def gray_frame(path, t, w, h):
    """t 초 장면을 흑백 바이트로 (화면 배치 확인용)."""
    r = __import__("subprocess").run([FF, "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                                      "-pix_fmt", "gray", "-"], capture_output=True)
    assert len(r.stdout) == w * h, len(r.stdout)
    return r.stdout


def col_mean(buf, w, h, x):
    return sum(buf[y * w + x] for y in range(0, h, 4)) / len(range(0, h, 4))


class BundleBase(unittest.TestCase):
    def setUp(self):
        # 한글·띄어쓰기가 들어간 작업 폴더 (Windows 사용자 폴더처럼)
        self.tmp = Path(tempfile.mkdtemp(prefix="묶음 테스트 "))
        work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        for p in self.patches:
            p.start()
        self.videos = dirs["VIDEOS"]
        self.logs = []
        core.set_progress()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, m):
        self.logs.append(m)

    def bundle_json(self, name):
        return json.loads((core.adir(name) / "bundle.json").read_text(encoding="utf-8"))

    def assert_no_temp(self):
        left = [d.name for d in core.OUT.iterdir() if d.name.startswith(bundle.TMP_PREFIX)]
        self.assertEqual(left, [], "임시 폴더가 남았어요")


class TestBundleEncode(BundleBase):
    def test_mixed_specs_reencode(self):
        """1080p30+소리 / 720p25 무음 / 세로(회전) → 15초, 1920x1080 영상 1개, 소리 있음, 찍은 순서."""
        a = make_clip(self.videos / "zz 첫 장면.mp4", "1920x1080", 30, audio=True, created="2024-05-01T10:00:00Z")
        b = make_clip(self.videos / "aa 둘째 장면 무음.mp4", "1280x720", 25, audio=False, created="2024-05-01T10:05:00Z")
        c = make_clip(self.videos / "mm 셋째 장면 세로.mp4", "1920x1080", 30, audio=True, created="2024-05-01T10:10:00Z", rotate=90)
        self.assertIsNotNone(info(c)["rotation"], "시험 영상에 회전 정보가 있어야 함")
        names = [b.name, c.name, a.name]  # 고른 순서와 상관없이 찍은 순서대로
        res = bundle.make_bundle(names, "우리 팀 경기", self.log)
        self.assertEqual(res["mode"], "encode")
        self.assertEqual(res["clips"], 3)
        self.assertTrue(res["name"].startswith("묶음_20240501_우리 팀 경기"), res["name"])
        out = self.videos / res["name"]
        self.assertTrue(out.is_file())
        i = info(out)
        self.assertAlmostEqual(i["duration"], 15.0, delta=0.1)
        self.assertEqual(len(i["videos"]), 1, i["videos"])
        self.assertIn("1920x1080", i["videos"][0])
        self.assertIn("h264", i["videos"][0])
        self.assertIn("30 fps", i["videos"][0])
        self.assertTrue(i["audio"])
        self.assertIsNone(i["rotation"], "회전은 화면에 적용되고 표시는 없어야 함")
        meta = self.bundle_json(res["name"])
        self.assertEqual([m["file"] for m in meta], [a.name, b.name, c.name])
        self.assertEqual([m["start"] for m in meta], [0, 5, 10])
        self.assertEqual([m["dur"] for m in meta], [5, 5, 5])
        # 원본은 그대로
        for p in (a, b, c):
            self.assertTrue(p.exists())
        # 세로 영상(10~15초)은 가운데에 세워져 양옆이 검은 띠, 720p(5~10초)는 화면을 꽉 채움
        W, H = 1920, 1080
        g = gray_frame(out, 12.5, W, H)
        self.assertLess(col_mean(g, W, H, 100), 20)
        self.assertLess(col_mean(g, W, H, W - 100), 20)
        self.assertGreater(col_mean(g, W, H, W // 2), 40)
        g = gray_frame(out, 7.5, W, H)
        self.assertGreater(max(col_mean(g, W, H, 100), col_mean(g, W, H, W - 100)), 40)
        # 소리: 앞 5초는 사인파, 무음 파일 구간은 조용함
        lv = core.run([FF, "-hide_banner", "-ss", "6", "-t", "3", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"]).stderr
        self.assertRegex(lv, r"max_volume: -(9\d|\d{3})|max_volume: -inf")
        lv = core.run([FF, "-hide_banner", "-ss", "1", "-t", "3", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"]).stderr
        mx = float(re.search(r"max_volume: (-?[\d.]+)", lv)[1])
        self.assertGreater(mx, -30)
        self.assert_no_temp()
        self.assertTrue(any("완성" in m for m in self.logs), self.logs)


class TestBundleCopy(BundleBase):
    def test_identical_specs_copy_path(self):
        """규격이 같은 세 파일은 다시 만들지 않고 concat -c copy 로 이어 붙임 (이름에 ' 와 한글·띄어쓰기)."""
        base = time.time() - 3600
        files = []
        for k, nm in enumerate(["it's 클립 3.mp4", "클립 10.mp4", "클립 2.mp4"]):
            p = make_clip(self.videos / nm, "1280x720", 30, dur=2, audio=True)
            files.append(p)
        # 촬영 시각이 없으면 파일 수정 시각 순서: 클립 2 → 클립 10 → it's 클립 3
        for p, off in zip(files, (300, 200, 100)):
            os.utime(p, (base + off, base + off))
        with mock.patch.object(core, "run", wraps=core.run) as spy:
            res = bundle.make_bundle([p.name for p in files], "복사 묶음", self.log)
        cmds = [c.args[0] for c in spy.call_args_list]
        concat = [c for c in cmds if "concat" in c]
        self.assertEqual(len(concat), 1, cmds)
        cc = concat[0]
        self.assertIn("-c:v", cc)
        self.assertEqual(cc[cc.index("-c:v") + 1], "copy")
        self.assertEqual(cc.count("-i"), 4, "목록 1개 + 파일마다 소리 1개")  # 소리는 파일 길이에 맞춰 다시 만듦
        self.assertFalse(any("libx264" in c or "-vf" in c for c in cmds), "화면은 다시 만들면 안 됨")
        self.assertEqual(res["mode"], "copy")
        out = self.videos / res["name"]
        i = info(out)
        self.assertAlmostEqual(i["duration"], 6.0, delta=0.15)
        self.assertEqual(len(i["videos"]), 1)
        self.assertIn("1280x720", i["videos"][0])
        self.assertTrue(i["audio"])
        meta = self.bundle_json(res["name"])
        self.assertEqual([m["file"] for m in meta], ["클립 2.mp4", "클립 10.mp4", "it's 클립 3.mp4"])
        self.assertAlmostEqual(meta[1]["start"], 2.0, delta=0.05)
        self.assertAlmostEqual(meta[2]["start"], 4.0, delta=0.05)
        self.assertGreater(res["size_mb"], 0)
        self.assert_no_temp()

    def test_copy_keeps_rotation_and_name_unique(self):
        """세로(회전) 파일끼리는 회전 표시를 그대로 유지 · 같은 제목이면 (2) 를 붙임."""
        ps = [make_clip(self.videos / f"세로 {k}.mp4", "640x360", 30, dur=1, created=f"2024-06-0{k}T09:00:00Z", rotate=90)
              for k in (1, 2)]
        r1 = bundle.make_bundle([p.name for p in ps], "세로", self.log)
        r2 = bundle.make_bundle([p.name for p in ps], "세로", self.log)
        self.assertEqual(r1["mode"], "copy")
        self.assertEqual(r1["name"], "묶음_20240601_세로.mp4")
        self.assertEqual(r2["name"], "묶음_20240601_세로 (2).mp4")
        i = info(self.videos / r1["name"])
        self.assertIsNotNone(i["rotation"], "회전 표시가 사라지면 세로 영상이 눕혀 보임")
        self.assertAlmostEqual(i["duration"], 2.0, delta=0.15)

    def test_all_silent_gets_silent_track(self):
        """모두 소리 없는 파일이어도 편집점 찾기(받아쓰기)가 되도록 무음 소리 트랙을 넣음."""
        ps = [make_clip(self.videos / f"드론 {k}.mp4", "640x360", 25, dur=1, audio=False, created=f"2024-06-01T09:0{k}:00Z")
              for k in (1, 2)]
        res = bundle.make_bundle([p.name for p in ps], "", self.log)
        self.assertEqual(res["mode"], "copy")
        self.assertIn("_촬영본", res["name"])
        i = info(self.videos / res["name"])
        self.assertTrue(i["audio"])
        self.assertAlmostEqual(i["duration"], 2.0, delta=0.15)


class TestBundleSync(BundleBase):
    def test_copy_audio_video_stay_in_sync(self):
        """그대로 이어 붙여도 이음새마다 소리가 밀리지 않음: 받아쓰기용 wav 의 삑 소리·화면 첫 장면이 bundle.json start 에 딱."""
        specs = [dict(), dict(adur=2.06), dict(audio=False), dict(ar=44100, adur=1.95), dict(), dict(), dict(adur=2.03)]
        names = []
        for k, sp in enumerate(specs):
            names.append(make_beep_clip(self.videos / f"폰 촬영 {k}.mp4", created=f"2024-08-01T10:0{k}:00Z", **sp).name)
        res = bundle.make_bundle(names, "싱크", self.log)
        self.assertEqual(res["mode"], "copy", self.logs)
        out = self.videos / res["name"]
        meta = self.bundle_json(res["name"])
        self.assertEqual([m["file"] for m in meta], names)
        data, sr = analysis_wav(out, self.tmp)
        for m, sp in zip(meta, specs):
            t = beep_at(data, sr, m["start"] - 0.2, m["start"] + 0.5)
            if sp.get("audio") is False:
                self.assertIsNone(t, f"소리 없는 파일 자리는 조용해야 함 · {m}")
            else:
                self.assertIsNotNone(t, m)
                self.assertAlmostEqual(t, m["start"], delta=0.015, msg=f"소리가 밀림 · {m['file']}")
        keys = key_times(out)
        for m in meta:
            self.assertAlmostEqual(min(keys, key=lambda x: abs(x - m["start"])), m["start"], delta=0.002, msg=f"화면이 밀림 · {m['file']}")
        self.assertAlmostEqual(info(out)["duration"], res["duration"], delta=0.05)

    def test_variable_fps_phone_clips_still_copy(self):
        """휴대폰 가변 fps: 어두운 실내처럼 한 파일만 평균 27fps(30 tbr)여도 같은 30fps 로 보고 그대로 이어 붙임."""
        ps = [make_beep_clip(self.videos / f"가변 {k}.mp4", dur=4, drop_frame=(k == 1), created=f"2024-08-02T10:0{k}:00Z")
              for k in range(3)]
        self.assertLess(bundle.probe(ps[1])["fps"], 28, "시험 영상의 평균 fps 가 낮아야 함")
        self.assertEqual([bundle.probe(p)["fps_r"] for p in ps], [(30, 1)] * 3)
        res = bundle.make_bundle([p.name for p in ps], "가변", self.log)
        self.assertEqual(res["mode"], "copy", self.logs)
        self.assertFalse(any("다시 만들어요" in m for m in self.logs), self.logs)
        self.assertAlmostEqual(info(self.videos / res["name"])["duration"], 12.0, delta=0.1)


class TestBundleAudioTrack(BundleBase):
    def test_uses_chosen_audio_track(self):
        """고른 소리 트랙(idx)을 씀 — 첫 트랙을 풀 수 없는 파일 대신, 0번 무음 · 1번 삑 소리인 파일로 확인 (두 경로 모두)."""
        def two_tracks(path, size):
            ff("-f", "lavfi", "-i", f"testsrc=s={size}:r=30:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2",
               "-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*1000*t)*lt(t,0.1)':s=48000:d=2", "-map", "0:v", "-map", "1:a", "-map", "2:a",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", path)
            return Path(path).name
        real = bundle.probe

        def probe(path):
            c = real(path)
            c["audio"]["idx"] = 1
            return c
        for mode, sizes in (("copy", ("320x240", "320x240")), ("encode", ("320x240", "640x480"))):
            names = [two_tracks(self.videos / f"{mode} 두 트랙 {k}.mp4", sz) for k, sz in enumerate(sizes)]
            for k, n in enumerate(names):
                os.utime(self.videos / n, (1e9 + k, 1e9 + k))
            with mock.patch.object(bundle, "probe", side_effect=probe):
                res = bundle.make_bundle(names, mode, self.log)
            self.assertEqual(res["mode"], mode)
            data, sr = analysis_wav(self.videos / res["name"], self.tmp)
            for m in self.bundle_json(res["name"]):
                t = beep_at(data, sr, m["start"] - 0.2, m["start"] + 0.5)
                self.assertIsNotNone(t, f"{mode}: 1번 트랙 소리가 없음 · {m}")
                self.assertAlmostEqual(t, m["start"], delta=0.015)


class TestBundleErrors(BundleBase):
    def test_stale_temp_swept_before_space_check(self):
        """앱을 닫아 멈춘 묶기의 남은 조각이 공간을 차지해도, 먼저 지운 뒤 공간을 확인 (다시 하면 됨)."""
        ps = [make_clip(self.videos / f"s{k}.mp4", "320x240", 25, dur=1) for k in (1, 2)]
        stale = core.OUT / f"{bundle.TMP_PREFIX}old12345"
        stale.mkdir()
        (stale / "c0000.mov").write_bytes(b"x" * 1000)
        real = shutil.disk_usage

        def usage(p):  # 남은 조각이 있으면 공간이 거의 없음
            u = real(p)
            return shutil._ntuple_diskusage(u.total, u.used, 1000 if stale.exists() else 10 ** 12)
        with mock.patch.object(bundle.shutil, "disk_usage", side_effect=usage):
            res = bundle.make_bundle([p.name for p in ps], "다시", self.log)
        self.assertFalse(stale.exists())
        self.assertTrue((self.videos / res["name"]).is_file())

    def test_kept_even_when_folder_cannot_be_renamed(self):
        """검토 재현(D-076): 백신이 bundle.mp4 를 잡고 있으면 Windows 는 그 파일이 든 폴더 이름도 못 바꿈 → 예전에는 _keep_tmp 가
        None 을 돌려 finally 가 임시 폴더째(묶음·옮길 이름 기록까지) 지움. 이제 표시를 넣고 그 자리에 두고(정리에서도 빼고)
        다음에 켤 때 보관함으로."""
        import editor
        ps = [make_clip(self.videos / f"k{k}.mp4", "320x240", 25, dur=1) for k in (1, 2)]
        real = os.replace

        def locked(src, dst):  # bundle.mp4 를 옮기기 · 그 파일이 든 폴더 이름 바꾸기 모두 막힘
            if Path(src).name == "bundle.mp4" or Path(src).name.startswith(bundle.TMP_PREFIX):
                raise PermissionError(13, "다른 프로세스가 파일을 사용 중")
            return real(src, dst)
        with mock.patch.object(bundle.updater, "SETTLE_SECS", 0.2), mock.patch.object(os, "replace", locked):
            with self.assertRaisesRegex(RuntimeError, "다음에 앱을 켜면 보관함에 넣어 드려요"):
                bundle.make_bundle([p.name for p in ps], "잠김", self.log)
        left = [d for d in core.OUT.iterdir() if d.name.startswith(bundle.TMP_PREFIX)]
        self.assertEqual(len(left), 1, "임시 폴더째 지우지 않음")
        self.assertEqual(sorted(x.name for x in left[0].iterdir()), sorted(["bundle.mp4", bundle.KEEP_INFO, bundle.KEEP_MARK]))
        editor.sweep_temp(0)
        bundle._sweep_old()
        self.assertTrue((left[0] / "bundle.mp4").exists(), "정리(편집실 sweep_temp · 묶기 _sweep_old)에서도 지우지 않음")
        self.assertEqual(editor.place_kept(lambda *a: None), [], "편집실 쪽은 묶음을 완성본으로 옮기지 않음")
        moved = bundle.place_kept(lambda *a: None)
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0].endswith("_잠김.mp4") and (self.videos / moved[0]).stat().st_size > 1000, moved)
        self.assertFalse(left[0].exists())

    def test_needs_two_files(self):
        p = make_clip(self.videos / "하나.mp4", "320x240", 25, dur=1)
        with self.assertRaisesRegex(RuntimeError, "2개 이상"):
            bundle.make_bundle([p.name, p.name], "x", self.log)
        with self.assertRaisesRegex(RuntimeError, "찾지 못한"):
            bundle.make_bundle([p.name, "없는 파일.mp4"], "x", self.log)

    def test_broken_file_named(self):
        p = make_clip(self.videos / "정상.mp4", "320x240", 25, dur=1)
        (self.videos / "깨진 파일.mp4").write_bytes(b"not a video" * 100)
        with self.assertRaisesRegex(RuntimeError, "깨진 파일.mp4"):
            bundle.make_bundle([p.name, "깨진 파일.mp4"], "x", self.log)
        self.assert_no_temp()
        self.assertEqual(sorted(x.name for x in self.videos.iterdir()), ["깨진 파일.mp4", "정상.mp4"])

    def test_not_enough_space(self):
        ps = [make_clip(self.videos / f"c{k}.mp4", "320x240", 25, dur=1) for k in (1, 2)]
        fake = shutil._ntuple_diskusage(10 ** 9, 10 ** 9, 1000)
        with mock.patch.object(bundle.shutil, "disk_usage", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "저장 공간이 부족"):
                bundle.make_bundle([p.name for p in ps], "x", self.log)
        self.assertEqual(len(list(self.videos.iterdir())), 2)


class TestBundleRoute(BundleBase):
    def test_api_bundle_job(self):
        """POST /api/bundle: 이름 검사 → 작업으로 묶기 → /api/state 결과에 새 파일 이름·크기."""
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        import app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)  # 나중에 등록한 것이 먼저: 멈춘 뒤 닫기

        def call(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET",
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        ps = [make_clip(self.videos / f"폰 촬영 {k}.mp4", "320x240", 25, dur=1, created=f"2024-07-0{k}T09:00:00Z") for k in (1, 2)]
        analyzed = []

        def fake_analyze(b):  # 받아쓰기(whisper) 대신
            analyzed.append(b)
            if b["names"][0].endswith("(2).mp4"):
                raise RuntimeError("받아쓰기 엔진이 없어요")

        def wait():
            t0 = time.time()
            while time.time() - t0 < 60:
                st = call("/api/state")[1]
                if not st["job"]:
                    return st
                time.sleep(0.2)
            self.fail("작업이 끝나지 않음")
        with mock.patch.object(app, "PORT", port), mock.patch.object(app, "log", self.log), \
                mock.patch.object(app.Handler, "_analyze", staticmethod(fake_analyze)):
            self.assertEqual(call("/api/bundle", {"names": [ps[0].name, "..\\밖.mp4"]})[0], 400)
            self.assertEqual(call("/api/bundle", {"names": [ps[0].name, "없는.mp4"]})[0], 400)
            self.assertEqual(call("/api/bundle", {"names": [ps[0].name, ps[0].name]})[0], 400)
            code, j = call("/api/bundle", {"names": [p.name for p in ps], "title": "경기 / 하이라이트", "model": "small"})
            self.assertEqual((code, j["ok"]), (200, True))
            st = wait()
            self.assertIsNone(st["error"])
            res = st["result"]
            self.assertEqual(res["name"], "묶음_20240701_경기 하이라이트.mp4")
            self.assertIn(res["name"], [v["name"] for v in st["local"]])
            self.assertTrue(res["added"].endswith("MB"))
            self.assertTrue(any("완성" in m for m in self.logs), self.logs)
            # 같은 작업 안에서 새 파일의 편집점 찾기까지 (화면을 옮겨 다녀와도 끊기지 않게)
            self.assertEqual(analyzed, [{"names": [res["name"]], "model": "small"}])
            self.assertTrue(res["analyzed"])
            # 편집점 찾기가 실패해도 묶은 결과(이름·늘어난 크기)는 알려 줌
            call("/api/bundle", {"names": [p.name for p in ps], "title": "경기 / 하이라이트"})
            st = wait()
            self.assertIsNone(st["error"])
            self.assertEqual(st["result"]["name"], "묶음_20240701_경기 하이라이트 (2).mp4")
            self.assertEqual(analyzed[-1]["model"], core.default_model(), "고르지 않았으면 이 PC 사양에 맞는 기본값 (D-070)")
            self.assertIn("받아쓰기 엔진", st["result"]["analyze_error"])
            self.assertTrue(any("편집점 찾기는 하지 못했어요" in m for m in self.logs), self.logs)


def fake(name, created=None, mtime=0.0, dur=10.0, dw=1920, dh=1080, fps=(30, 1)):
    return {"name": name, "created": created, "mtime": mtime, "duration": dur, "dw": dw, "dh": dh, "fps_r": fps}


class TestBundleUnits(unittest.TestCase):
    def test_sort_order(self):
        cl = [fake("b.mp4", created=200), fake("a.mp4", created=100), fake("VID 10.mp4", mtime=50), fake("VID 9.mp4", mtime=50)]
        self.assertEqual([c["name"] for c in bundle.sort_clips(cl)], ["VID 9.mp4", "VID 10.mp4", "a.mp4", "b.mp4"])

    def test_parse_time(self):
        self.assertEqual(bundle._parse_time("2024-05-01T10:00:00.000000Z"), 1714557600)
        self.assertEqual(bundle._parse_time("2024-05-01T19:00:00+0900"), 1714557600)
        self.assertEqual(bundle._parse_time("2024-05-01T19:00:00+09:00"), 1714557600)
        self.assertEqual(bundle._parse_time("2024-05-01 10:00:00"), 1714557600)
        self.assertIsNone(bundle._parse_time("1904-01-01T00:00:00Z"))
        self.assertIsNone(bundle._parse_time("1970-01-01T00:00:00.000000Z"))
        self.assertIsNone(bundle._parse_time("abc"))

    def test_snap_fps(self):
        self.assertEqual(bundle._snap_fps(29.98), (30000, 1001))
        self.assertEqual(bundle._snap_fps(30), (30, 1))
        self.assertEqual(bundle._snap_fps(59.94), (60000, 1001))
        self.assertEqual(bundle._snap_fps(25), (25, 1))
        self.assertIsNone(bundle._snap_fps(0))

    def test_nominal_fps(self):
        self.assertEqual(bundle._nominal_fps(29.90, 30), 30)  # 가변 fps 휴대폰
        self.assertEqual(bundle._nominal_fps(21.3, 30), 30)  # 어두운 실내
        self.assertEqual(bundle._nominal_fps(25, 50, interlaced=True), 25)  # 캠코더 인터레이스
        self.assertEqual(bundle._nominal_fps(29.97, 90000), 29.97)  # 엉터리 tbr
        self.assertEqual(bundle._nominal_fps(0, 30), 30)
        self.assertEqual(bundle._nominal_fps(0, 90000), 0)
        cl = [dict(vcodec="h264", fps_r=(30, 1), vdesc="h264", pix="yuv420p", size_desc="1280x720", tbn="600", rot=0),
              dict(vcodec="h264", fps_r=(30000, 1001), vdesc="h264", pix="yuv420p", size_desc="1280x720", tbn="600", rot=0)]
        self.assertTrue(bundle.can_copy(cl))  # 29.97 ↔ 30 은 같은 것으로
        cl[1]["fps_r"] = (25, 1)
        self.assertFalse(bundle.can_copy(cl))

    def test_probe_iphone_like(self):
        """아이폰식 정보: 풀 수 없는 공간 음향(none) 트랙은 건너뛰고 AAC · 가변 fps 는 30 · 회전 · HLG · 현지 촬영 시각."""
        err = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'IMG_0001.MOV':
  Metadata:
    creation_time   : 2025-03-01T01:02:09.000000Z
    com.apple.quicktime.creationdate: 2025-03-01T10:02:03+0900
  Duration: 00:00:10.03, start: 0.000000, bitrate: 20000 kb/s
  Stream #0:0[0x1](und): Video: hevc (Main 10) (hvc1 / 0x31637668), yuv420p10le(tv, bt2020nc/bt2020/arib-std-b67), 1920x1080, 18000 kb/s, 29.98 fps, 30 tbr, 600 tbn (default)
      Metadata:
        creation_time   : 2025-03-01T01:02:09.000000Z
      Side data:
        displaymatrix: rotation of -90.00 degrees
  Stream #0:1[0x2](und): Audio: none (apac / 0x63617061), 48000 Hz, 4 channels, 300 kb/s (default)
  Stream #0:2[0x3](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 160 kb/s
  Stream #0:3[0x4](und): Data: none (mebx / 0x7862656D), 0 kb/s (default)
At least one output file must be specified
"""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "IMG_0001.MOV"
            f.write_bytes(b"x")
            with mock.patch.object(bundle.core, "run", return_value=mock.Mock(stderr=err)):
                c = bundle.probe(f)
            self.assertEqual((c["audio"]["codec"], c["audio"]["idx"], c["audio"]["rate"]), ("aac", 1, 44100))
            self.assertEqual((c["fps_r"], c["rot"], c["hdr"], c["duration"], c["start"]), ((30, 1), 270, "hlg", 10.03, 0.0))
            self.assertEqual((c["dw"], c["dh"]), (1080, 1920))
            self.assertEqual(c["created"], bundle._parse_time("2025-03-01T01:02:03Z"))
            only_none = err.replace("  Stream #0:2[0x3](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 160 kb/s\n", "")
            with mock.patch.object(bundle.core, "run", return_value=mock.Mock(stderr=only_none)):
                self.assertIsNone(bundle.probe(f)["audio"], "풀 수 없는 소리뿐이면 무음으로")

    def test_target_format(self):
        # 가로 2개(짧음) + 세로 1개(아주 김) → 길이 기준으로 세로
        cl = [fake("a", dur=5), fake("b", dur=5), fake("c", dur=60, dw=1080, dh=1920, fps=(60, 1))]
        self.assertEqual(bundle.target_format(cl), (1080, 1920, (60, 1)))
        # 크기가 섞이면 가장 많은 크기, 같으면 큰 쪽 · 4K 초과는 4K 로 · 120fps 는 60 으로
        cl = [fake("a", dw=1920, dh=1080, fps=(30, 1)), fake("b", dw=1280, dh=720, fps=(25, 1))]
        self.assertEqual(bundle.target_format(cl), (1920, 1080, (30, 1)))
        cl = [fake("a", dw=7680, dh=4320, fps=(120, 1))]
        self.assertEqual(bundle.target_format(cl), (3840, 2160, (60, 1)))

    def test_clean_title(self):
        self.assertEqual(bundle.clean_title('a/b:c*?"<>| 경기. '), "a b c 경기")
        self.assertEqual(bundle.clean_title("  ...  "), "촬영본")
        self.assertEqual(bundle.clean_title(None), "촬영본")
        self.assertLessEqual(len(bundle.clean_title("가" * 200)), 60)

    def test_split_top(self):
        self.assertEqual(bundle._split_top("h264 (High) (avc1 / 0x1), yuv420p(tv, bt709, progressive), 1920x1080 [SAR 1:1 DAR 16:9], 30 fps"),
                         ["h264 (High) (avc1 / 0x1)", "yuv420p(tv, bt709, progressive)", "1920x1080 [SAR 1:1 DAR 16:9]", "30 fps"])


if __name__ == "__main__":
    unittest.main()
