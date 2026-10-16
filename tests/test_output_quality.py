"""완성본 소리·영상 품질 (C1 · D-400~D-404) — 저장소 폴더에서 python3 -m unittest tests.test_output_quality

- 느리게 한 클립: 같은 장면이 두 번씩 나오지 않게 앞뒤 장면 섞기(기본)·'아주 부드럽게'(움직임 보간) · 60fps 촬영본은 원래 장면 (editor.slow_mode)
- 컷 자리 소리: 원본 소리 한가운데를 자른 끝마다 짧은 페이드 (editor._cut_edges · EDGE_FADE) · 가편집 컷 끝이 소리 나는 곳이면 조용한 곳으로 (_quiet_edges)
- 한쪽만 녹음된 대사: 큰 쪽 소리를 양쪽에 (editor.channel_levels·one_sided) · 검수 '좌우 소리 차이' (qa.check_video)
- 검토 고침 (D-405): '아주 부드럽게' 구간이 짧거나 원본 끝이어도 프레임 수 그대로 · 빠르기만 다른 이어진 조각은 페이드 없음 ·
  채널 재기는 멈추기(✕)로 끔·못 재도 한 번만 · 편집실 '느린 장면' 칸은 쓸모 있을 때만 (editor.html smoothRow 를 node 로)
진짜 ffmpeg 로 몇 초짜리 시험 영상을 만들어 내보낸 파일을 잼."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import editor  # noqa: E402
import qa  # noqa: E402

FF = core.ffmpeg()
ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def ff(*a):
    r = subprocess.run([FF, "-v", "error", "-y", *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]


def gray(path, w=64, h=36):
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def pcm(path):
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def rms_db(x):
    return 20 * np.log10(max(1e-12, float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))))


def item(iid, media, track, start, a, b, **kw):
    d = {"id": iid, "track": track, "media": media, "start": start, "in": a, "out": b, "speed": 1, "rev": False, "link": None, "fit": "auto",
         "fx": {}, "color": {}, "reframe": 0.5}
    if track.startswith("A"):
        d.update(gain=0, fadeIn=0, fadeOut=0, mute=False)
    d.update(kw)
    return d


def pair(k, media, start, a, b, **kw):
    return [item("v" + k, media, "V1", start, a, b, link=k, **kw), item("a" + k, media, "A1", start, a, b, link=k, **kw)]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="완성본 품질 테스트 "))
        work = cls.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in list(dirs.values()) + [work / "edit_media"]:
            d.mkdir(parents=True, exist_ok=True)
        cls.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(editor, "ASSETS", work / "edit_media")]
        for p in cls.patches:
            p.start()
        cls.A = work / "edit_media"
        cls.media = []
        cls.make()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def add(cls, k, f, fps=30.0, dur=4.0):
        cls.media.append({"id": k, "kind": "video", "src": "assets", "file": f, "dur": dur, "w": 320, "h": 180, "fps": fps, "audio": True})

    def proj(self, name, items, dur, trans=(), tracks=None):
        return {"id": "s", "name": name, "format": "long", "v": 2, "captionsOn": False, "captionStyle": dict(editor.LONG_STYLE), "titles": [],
                "shapes": [], "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": False, "lufs": -14},
                "duck": {"on": False, "amount": -14}, "tracks": tracks or editor.default_tracks(), "items": items, "trans": list(trans),
                "markers": [], "captions": [], "info": {"duration": dur, "width": 320, "height": 180, "fps": 30.0}, "source": "테스트.mp4",
                "media": self.media}

    def export(self, p):
        out = editor.export("테스트.mp4", p, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda *_: None)
        return core.OUT / out[0]


class SlowMotionTest(Base):
    @classmethod
    def make(cls):
        for fps in (30, 60):
            ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r={fps}:d=4", "-f", "lavfi", "-i", "sine=f=440:d=4", "-c:v", "libx264", "-preset",
               "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cls.A / f"m{fps}.mp4"))
            cls.add(f"m{fps}", f"m{fps}.mp4", float(fps))

    def test_slow_mode_rules(self):
        """30fps 0.5배 → 섞기 · 60fps 0.5배는 원래 장면(30fps 로 내보낼 때) · 1배·빠르게·사진은 그대로 · 아주 부드럽게는 6초 이하만."""
        m30, m60 = {"fps": 30.0, "kind": "video"}, {"fps": 60.0, "kind": "video"}
        it = item("v", "m30", "V1", 0, 1.0, 2.0, speed=0.5)
        self.assertEqual(editor.slow_mode(it, m30, 30), "blend")
        self.assertIsNone(editor.slow_mode(it, m60, 30))
        self.assertEqual(editor.slow_mode(it, m60, 60), "blend")   # 60fps 로 내보내면 60fps 촬영본도 모자람
        self.assertIsNone(editor.slow_mode(dict(it, speed=1), m30, 30))
        self.assertIsNone(editor.slow_mode(dict(it, speed=2), m30, 30))
        self.assertIsNone(editor.slow_mode(it, {"fps": 30.0, "kind": "image"}, 30))
        self.assertEqual(editor.slow_mode(dict(it, smooth="motion"), m30, 30), "motion")
        self.assertEqual(editor.slow_mode(dict(it, smooth="motion", out=1.0 + 3.5), m30, 30), "blend")  # 7초짜리 → 섞기로
        self.assertEqual(editor._rate_filter(None, 30), "fps=30")

    def check_no_repeats(self, f, n):
        fr = gray(f)
        self.assertEqual(len(fr), n)
        d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
        self.assertEqual(int((d < 0.05).sum()), 0, d[:10])
        return fr

    def test_half_speed_30fps_blends_between_frames(self):
        """예전: 0.5배 120장 중 60장이 앞 장면과 같음 → 섞은 장면 (짝수 장면은 원본 그대로 · 홀수는 앞뒤 원본의 중간)."""
        f = self.export(self.proj("느리게 30", pair("x", "m30", 0, 1.0, 3.0, speed=0.5), 4.0))
        fr = self.check_no_repeats(f, 120)
        src = gray(self.A / "m30.mp4")
        self.assertLess(max(np.abs(fr[2 * j] - src[30 + j]).mean() for j in range(20)), 1.5)            # 시각이 밀리지 않음
        self.assertLess(max(np.abs(fr[2 * j + 1] - (src[30 + j] + src[31 + j]) / 2).mean() for j in range(20)), 1.5)

    def test_half_speed_60fps_uses_native_frames(self):
        f = self.export(self.proj("느리게 60", pair("x", "m60", 0, 1.0, 3.0, speed=0.5), 4.0))
        fr = self.check_no_repeats(f, 120)
        src = gray(self.A / "m60.mp4")
        self.assertLess(max(np.abs(fr[j] - src[60 + j]).mean() for j in range(40)), 1.5)

    def test_motion_interpolation_short_clip(self):
        f = self.export(self.proj("아주 부드럽게", pair("x", "m30", 0, 1.0, 2.0, speed=0.5, smooth="motion"), 2.0))
        self.check_no_repeats(f, 60)

    def test_motion_keeps_frame_count_short_segment_at_source_end(self):
        """검토 재현: 원본 끝(3~4초)을 0.5배 '아주 부드럽게' + 1.95초에 끝나는 B롤(→ 원본 1장도 안 되는 1.95~2초 구간) + 뒤 보통 클립 —
        예전: 움직임 보간이 그 구간을 0장으로 내보내 117장(소리 4초보다 3장 모자람 · 뒤 장면이 일찍) → 120장 · 뒤 클립 첫 장면이 제자리."""
        its = pair("x", "m30", 0, 3.0, 4.0, speed=0.5, smooth="motion") + [item("b", "m60", "V2", 0.5, 0.0, 1.45)] + pair("y", "m30", 2.0, 0.0, 2.0)
        fr = gray(self.export(self.proj("부드럽게 짧은 구간", its, 4.0)))
        self.assertEqual(len(fr), 120)
        src = gray(self.A / "m30.mp4")
        self.assertLess(np.abs(fr[60] - src[0]).mean(), 1.5)
        self.assertLess(np.abs(fr[119] - src[59]).mean(), 1.5)

    def test_motion_reversed_keeps_frame_count(self):
        """검토 재현: 거꾸로 0.5배 '아주 부드럽게' 4.033초 — 거꾸로 재생 2초 나눔 끝에 원본 1장짜리 구간이 생겨 예전엔 2장 모자람 → 섞기와 같은 장수."""
        its = pair("x", "m30", 0, 0.9, 2.9165, speed=0.5, rev=True, smooth="motion")
        n = len(gray(self.export(self.proj("거꾸로 부드럽게", its, 4.033))))
        b = len(gray(self.export(self.proj("거꾸로 섞기", pair("x", "m30", 0, 0.9, 2.9165, speed=0.5, rev=True), 4.033))))
        self.assertEqual(n, b)
        self.assertGreaterEqual(n, 121)

    def test_slow_clip_next_to_transitions_exports(self):
        """회귀(끝까지 자동으로 롱폼이 '영상을 만들지 못했어요'): 느린 클립 앞뒤 흰 화면 번쩍임·디졸브 — 섞기·아주 부드럽게 모두 내보내짐 (D-417)."""
        for smooth in (None, "motion"):
            kw = {"smooth": smooth} if smooth else {}
            its = pair("x", "m30", 0, 0.0, 1.0) + pair("y", "m30", 1.0, 1.0, 2.0, speed=0.5, **kw) + pair("z", "m30", 3.0, 2.0, 3.0)
            tr = [{"id": "t1", "track": "V1", "a": "vx", "b": "vy", "type": "white", "dur": 0.2, "align": "center"},
                  {"id": "t2", "track": "V1", "a": "vy", "b": "vz", "type": "dissolve", "dur": 0.4, "align": "center"}]
            fr = gray(self.export(self.proj(f"느린 클립 전환 {smooth}", its, 4.0, trans=tr)))
            self.assertEqual(len(fr), 120, smooth)
        self.assertIn("settb=1/30", editor._rate_filter("blend", 30))
        self.assertIn("settb=1/30", editor._rate_filter("motion", 30))

    def test_motion_filter_pads_before_interpolation(self):
        """움직임 보간 앞에 마지막 장면을 몇 장 더 붙임 (섞기·보통은 그대로)."""
        p = self.proj("필터", pair("x", "m30", 0, 3.0, 4.0, speed=0.5, smooth="motion"), 2.0)
        fc = []
        with tempfile.TemporaryDirectory() as td:
            args = editor._build_segment(p, {m["id"]: m for m in self.media}, 320, 180, 30, 0, 60, [], Path(td), 0)
            for f in Path(td).iterdir():
                fc.append(f.read_text(encoding="utf-8"))
        g = "".join(fc) or json.dumps(args, ensure_ascii=False)
        self.assertIn(f"tpad=stop_mode=clone:stop={editor.MOTION_TAIL},setpts=PTS/0.50000,minterpolate", g)


class CutFadeTest(Base):
    @classmethod
    def make(cls):
        ff("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=5", "-f", "lavfi", "-i",
           "aevalsrc=0.5*sin(2*PI*440*t)|0.5*sin(2*PI*440*t):s=48000:d=5",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest", str(cls.A / "tone.mov"))
        cls.add("tone", "tone.mov", dur=5.0)

    def test_cut_edges_rules(self):
        md = {"dur": 5.0}
        a, b = item("a", "tone", "A1", 0, 1.0, 2.0), item("b", "tone", "A1", 1.0, 2.5, 3.5)
        self.assertEqual(editor._cut_edges(a, md, [a, b]), (True, True))
        head = item("h", "tone", "A1", 0, 0.0, 1.0)  # 원본 처음부터 → 앞은 자른 곳이 아님
        self.assertEqual(editor._cut_edges(head, md, [head]), (False, True))
        c = item("c", "tone", "A1", 1.0, 2.0, 3.0)  # a 를 나누기만 한 것 (원본이 끊김 없이 이어짐)
        self.assertEqual(editor._cut_edges(a, md, [a, c]), (True, False))
        self.assertEqual(editor._cut_edges(c, md, [a, c]), (False, True))
        other = dict(c, media="x")
        self.assertEqual(editor._cut_edges(a, md, [a, other]), (True, True))
        # 빠르기만 다른 이어진 조각(말 빠르기 맞추기 1.12배 → 1배 · 나눈 뒤 한쪽만 빠르기 바꿈)도 원본은 이어짐 → 페이드 없음 (D-405)
        f = item("f", "tone", "A1", 0, 1.0, 2.12, speed=1.12)
        g = item("g", "tone", "A1", 1.0, 2.12, 3.0)
        self.assertEqual(editor._cut_edges(f, md, [f, g]), (True, False))
        self.assertEqual(editor._cut_edges(g, md, [f, g]), (False, True))
        r1, r2 = item("r1", "tone", "A1", 0, 2.0, 3.0, rev=True), item("r2", "tone", "A1", 1.0, 1.5, 2.0, rev=True, speed=0.5)
        self.assertEqual(editor._cut_edges(r1, md, [r1, r2])[1], False)    # 거꾸로도 같은 방향이면 이어짐
        self.assertEqual(editor._cut_edges(r1, md, [r1, dict(r2, rev=False)])[1], True)

    def test_jump_cut_has_no_click(self):
        """위상이 어긋난 곳에서 이어 붙인 사인 — 예전: 컷 자리 샘플 차이 0.29 (다른 곳 최대 0.03의 10배) → 다른 곳만큼."""
        f = self.export(self.proj("컷 소리", pair("1", "tone", 0, 1.0, 2.0) + pair("2", "tone", 1.0, 2.5037, 3.5), 2.0))
        x = pcm(f)[:, 0]
        d = np.abs(np.diff(x))
        self.assertLess(float(d[48000 - 240:48000 + 240].max()), 1.2 * float(d[4800:48000 - 2400].max()))
        # 페이드는 컷 앞뒤 12ms 만 — 그 밖은 그대로
        self.assertGreater(float(np.abs(x[48000 - 1200:48000 - 720]).max()), 0.45)
        self.assertGreater(float(np.abs(x[48000 + 720:48000 + 1200]).max()), 0.45)

    def test_speed_split_keeps_level(self):
        """검토 재현: 1.12배 → 1배로 원본이 이어지는 곳 — 예전(D-401 처음): 12ms 페이드 두 번으로 24ms 움푹 꺼짐 → 그대로."""
        its = pair("1", "tone", 0, 0.5, 2.74, speed=1.12) + pair("2", "tone", 2.0, 2.74, 3.74)
        x = pcm(self.export(self.proj("빠르기 나눈 곳", its, 3.0)))[:, 0]
        self.assertGreater(float(np.abs(x[96000 - 200:96000 + 200]).max()), 0.45)

    def test_split_without_cut_keeps_level(self):
        """나누기만 한 곳(원본이 이어짐)은 페이드하지 않음 — 소리가 움푹 꺼지지 않음."""
        f = self.export(self.proj("나눈 곳", pair("1", "tone", 0, 1.0, 2.0) + pair("2", "tone", 1.0, 2.0, 3.0), 2.0))
        x = pcm(f)[:, 0]
        self.assertGreater(float(np.abs(x[48000 - 200:48000 + 200]).max()), 0.45)


class QuietEdgeTest(unittest.TestCase):
    """가편집 컷 끝·시작 옮기기 (_quiet_edges · 1초에 50칸 파형)."""

    @staticmethod
    def wave(loud):
        pk = [0.01] * 500
        for a, b in loud:
            for k in range(int(a * 50), int(b * 50)):
                pk[k] = 0.5
        return {"per_sec": 50, "peaks": pk}

    def test_tail_still_sounding_moves_to_quiet(self):
        wf = self.wave([(1.0, 3.08)])  # 말이 3.08초까지 소리 남 (받아쓰기 끝 + 여유 = 3.0)
        got = editor._quiet_edges([{"in": 0.9, "out": 3.0}], wf, [(1.0, 2.75, 3)])
        self.assertEqual(got, [{"in": 0.9, "out": 3.09}])

    def test_head_onset_before_cut_moves_earlier(self):
        wf = self.wave([(0.92, 2.0)])
        self.assertEqual(editor._quiet_edges([{"in": 1.0, "out": 2.5}], wf, [(1.05, 2.0, 3)]), [{"in": 0.91, "out": 2.5}])

    def test_limits(self):
        wf = self.wave([(1.0, 3.3)])  # 0.15초 안에 조용한 곳이 없음 → 그대로
        self.assertEqual(editor._quiet_edges([{"in": 1.2, "out": 3.0}], wf, []), [{"in": 1.2, "out": 3.0}])
        wf = self.wave([(1.0, 3.08)])
        # 다음 낱말(잘라 낸 추임새)이 바로 뒤에 있으면 넘어가지 않음 · 다음 컷 시작도 넘지 않음
        self.assertEqual(editor._quiet_edges([{"in": 0.9, "out": 3.0}], wf, [(3.01, 3.4, 1)]), [{"in": 0.9, "out": 3.0}])
        self.assertEqual(editor._quiet_edges([{"in": 0.9, "out": 3.0}, {"in": 3.05, "out": 4.0}], wf, [])[0]["out"], 3.0)
        # 파형이 없으면 그대로 · 이미 조용하면 그대로
        self.assertEqual(editor._quiet_edges([{"in": 0.9, "out": 3.0}], None, []), [{"in": 0.9, "out": 3.0}])
        self.assertEqual(editor._quiet_edges([{"in": 0.5, "out": 3.5}], wf, []), [{"in": 0.5, "out": 3.5}])
        # 앞 낱말 끝(잘라 낸 말)보다 앞으로는 안 감
        wf = self.wave([(0.92, 2.0)])
        self.assertEqual(editor._quiet_edges([{"in": 1.0, "out": 2.5}], wf, [(0.6, 0.99, 2)]), [{"in": 1.0, "out": 2.5}])


class ChannelTest(Base):
    @classmethod
    def make(cls):
        ff("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=4", "-f", "lavfi", "-i",
           "aevalsrc=0.2*sin(2*PI*300*t)*(0.6+0.4*sin(2*PI*2*t))|0:s=48000:d=4",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(cls.A / "left.mp4"))
        ff("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=4", "-f", "lavfi", "-i",
           "aevalsrc=0.2*sin(2*PI*300*t)|0.15*sin(2*PI*500*t):s=48000:d=4",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(cls.A / "both.mp4"))
        cls.add("left", "left.mp4")
        cls.add("both", "both.mp4")

    def test_levels_and_decision(self):
        lr = editor.channel_levels("left.mp4", "assets")
        self.assertGreater(lr[0], -30)
        self.assertLess(lr[1], -60)
        self.assertEqual(editor.one_sided(lr), 0)
        self.assertEqual(editor.one_sided([-80.0, -20.0]), 1)
        self.assertIsNone(editor.one_sided(editor.channel_levels("both.mp4", "assets")))
        self.assertIsNone(editor.one_sided([-75.0, -120.0]))   # 둘 다 거의 무음 → 그대로
        self.assertIsNone(editor.one_sided(None))

    def test_one_sided_dialog_fills_both_ears(self):
        """예전: 왼쪽 -20.6dB · 오른쪽 무음 그대로 → 양쪽 같은 소리."""
        y = pcm(self.export(self.proj("한쪽 소리", pair("x", "left", 0, 0.0, 4.0), 4.0)))
        self.assertAlmostEqual(rms_db(y[:, 0]), rms_db(y[:, 1]), delta=0.5)
        self.assertGreater(rms_db(y[:, 1]), -30)
        y = pcm(self.export(self.proj("양쪽 소리", pair("x", "both", 0, 0.0, 4.0), 4.0)))  # 고른 것은 그대로
        self.assertGreater(rms_db(y[:, 0]) - rms_db(y[:, 1]), 1.5)

    def test_music_track_left_alone(self):
        tr = editor.default_tracks()
        its = [item("v", "both", "V1", 0, 0.0, 4.0), item("m", "left", "A2", 0, 0.0, 4.0)]  # A2 = 음악 트랙
        y = pcm(self.export(self.proj("음악 한쪽", its, 4.0, tracks=tr)))
        self.assertLess(rms_db(y[:, 1]), -60)

    def test_channel_scan_stops_on_cancel(self):
        """멈추기(✕): 채널 재기 ffmpeg 도 끌 수 있게 _PROCS 에 · 멈추면 Cancelled 이고 캐시에 남기지 않음 (D-405)."""
        seen = []

        class Rec(set):
            def add(self, p):
                seen.append(p)
                super().add(p)
        cache = editor._cache_dir("both.mp4", "assets") / "chan_v1.json"
        cache.unlink(missing_ok=True)
        with mock.patch.object(editor, "_PROCS", Rec()):
            self.assertIsNotNone(editor.channel_levels("both.mp4", "assets"))
        self.assertEqual(len(seen), 1)
        cache.unlink()
        ab = __import__("threading").Event()
        ab.set()
        with self.assertRaises(editor.Cancelled):
            editor.channel_levels("both.mp4", "assets", ab)
        self.assertFalse(cache.exists())
        editor.CANCEL.set()
        try:
            with self.assertRaises(editor.Cancelled):
                editor._chan_fill(self.media[1])
        finally:
            editor.CANCEL.clear()
        self.assertFalse(cache.exists())

    def test_channel_failure_measured_once(self):
        """못 재면(예: 캐시를 못 씀) 같은 원본 클립마다 다시 읽지 않음 · 캐시만 못 쓰면 잰 값은 씀."""
        editor._CHAN_MEMO.clear()
        with mock.patch.object(editor, "channel_levels", side_effect=OSError("디스크 가득")) as cl:
            self.assertEqual(editor._chan_fill(self.media[0]), [])
            self.assertEqual(editor._chan_fill(self.media[0]), [])
        self.assertEqual(cl.call_count, 1)
        editor._CHAN_MEMO.clear()
        (editor._cache_dir("left.mp4", "assets") / "chan_v1.json").unlink(missing_ok=True)
        with mock.patch.object(editor, "_write_atomic", side_effect=OSError("잠긴 파일")):
            self.assertEqual(editor._chan_fill(self.media[0])[-1], "pan=stereo|c0=c0|c1=c0")
        editor._CHAN_MEMO.clear()

    def test_qa_warns_on_one_sided_file(self):
        with mock.patch.object(core, "set_progress"):
            r = qa.check_video(self.A / "left.mp4", "long", None)
            ok = qa.check_video(self.A / "both.mp4", "long", None)
        w = [i for i in r["items"] if i["title"] == "좌우 소리 차이"]
        self.assertEqual(len(w), 1, r["items"])
        self.assertEqual(w[0]["lv"], "warn")
        self.assertIn("오른쪽 소리가 거의 없어요", w[0]["msg"])
        self.assertFalse([i for i in ok["items"] if i["title"] == "좌우 소리 차이"])

    def test_qa_lr_parse(self):
        log = "[Parsed_astats_1 @ 0x1] Channel: 1\n[Parsed_astats_1 @ 0x1] RMS level dB: -17.0\n" \
              "[Parsed_astats_1 @ 0x1] Channel: 2\n[Parsed_astats_1 @ 0x1] RMS level dB: -inf\n"
        self.assertEqual(qa._lr_levels(log), [-17.0, float("-inf")])
        self.assertIsNone(qa._lr_levels(log.split("[Parsed_astats_1 @ 0x1] Channel: 2")[0]))   # 한 채널(모노)


@unittest.skipUnless(NODE, "node 가 없어요")
class SmoothRowTest(unittest.TestCase):
    """편집실 효과 컨트롤 '느린 장면' 칸 (editor.html smoothRow 를 그대로 node 로) — 쓸모 없을 때는 숨기거나 끔 · fps 숫자 없이 (D-405)."""

    def rows(self, cases):
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        i = src.index("\nconst MOTION_MAX =") + 1
        j = src.index("\n}", src.index("\nfunction smoothRow(", i)) + 2
        prog = ("const FPS = 30; let P = { info: {} }; let MD = {};\nconst mdOf = v => MD; const sp = it => it.speed || 1;"
                "const iLen = it => (it.out - it.in) / sp(it);\n" + src[i:j]
                + "\nconst out = [];\nfor (const [mainFps, fps, v] of " + json.dumps(cases) + ") { P.info.fps = mainFps; MD = { kind: 'video', fps };"
                + " out.push(smoothRow(v)); }\nprocess.stdout.write(JSON.stringify(out));")
        r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_rows(self):
        half = {"in": 0, "out": 2, "speed": 0.5}
        r30, r60_on30, r60_can60, r120, longc, motion, normal = self.rows([
            [30, 30, half], [30, 60, half], [60, 60, half], [60, 120, half],
            [30, 30, {"in": 0, "out": 4, "speed": 0.5}], [30, 30, dict(half, smooth="motion")], [30, 30, {"in": 0, "out": 2, "speed": 1}]])
        self.assertIn('id="pSmooth"', r30)
        self.assertNotIn("disabled", r30)
        # 60fps 촬영본 0.5배 · 60 으로 못 내보내는 편집본 → 칸 없이 알림만 (예전: 칸이 있어 켜도 아무것도 안 바뀜)
        self.assertNotIn("pSmooth\"", r60_on30)
        self.assertIn("넉넉해서", r60_on30)
        # 60 으로 내보낼 수 있으면 칸은 두고 60 일 때만 쓰인다고 (예전: '원래 장면 그대로' — 60 으로 내보내면 틀린 말)
        self.assertIn('id="pSmooth"', r60_can60)
        self.assertIn("초당 장면 60", r60_can60)
        self.assertNotIn("pSmooth\"", r120)                     # 120fps 0.5배 = 60 으로도 넉넉
        self.assertIn("disabled", longc)                       # 6초 넘으면 끔
        self.assertIn("checked", motion)
        self.assertEqual(normal, "")
        for h in (r30, r60_on30, r60_can60, r120, longc, motion):
            self.assertNotIn("fps", h)


if __name__ == "__main__":
    unittest.main()
