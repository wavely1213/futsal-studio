"""MSG 편집에 쓰는 렌더 재료 (editor.py ↔ editor.html 짝) — 저장소 폴더에서 python3 -m unittest tests.test_msg_render
타이틀 효과 '쾅 찍기'(stamp)·'흔들기'(shake) · 기울기(rot) · 글꼴(검은고딕·도현) · 클립 표시 noCaps(말 자막을 다시 띄우지 않음).
ASS 명령을 확인하고, 실제 ffmpeg 로 내보내 프레임에서 크기·기울기가 미리보기 계산(editor.html effect())과 맞는지 본다."""
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

FF = core.ffmpeg()


def title(tid, text, start, dur, **st):
    return {"id": tid, "text": text, "start": start, "dur": dur, "style": dict(editor.TITLE_STYLE, **st)}


class AssTags(unittest.TestCase):
    def proj(self, titles):
        return {"captionStyle": dict(editor.LONG_STYLE), "titles": titles, "shapes": [], "items": [], "captions": [], "captionsOn": False}

    def test_stamp_and_shake_tags(self):
        self.assertEqual(editor._effect_tags("stamp", 0, 0, 1), r"{\fscx165\fscy165\t(0,120,\fscx100\fscy100)}")
        sh = editor._effect_tags("shake", 0, 0, 1, -4.0)
        self.assertIn(r"\t(0,60,\frz2)", sh)
        self.assertIn(r"\t(60,120,\frz-10)", sh)
        self.assertTrue(sh.endswith(r"\t(180,240,\frz-4)}"))

    def test_rot_is_clockwise_on_screen(self):
        """style.rot +8 (화면 시계 방향) → ASS \\frz-8 (반시계 +) · 숫자가 아니면 0 · ±45 로 묶음."""
        ass = editor.build_ass(self.proj([title("a", "가", 0, 2, rot=8), title("b", "나", 0, 2, rot="x"), title("c", "다", 0, 2, rot=90)]), 1920, 1080)
        lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
        self.assertIn(r"\frz-8", lines[0])
        self.assertNotIn(r"\frz", lines[1])
        self.assertIn(r"\frz-45", lines[2])

    def test_fonts_use_own_family_and_size_ratio(self):
        ass = editor.build_ass(self.proj([title("a", "가", 0, 2, font="Black Han Sans", size=100), title("b", "나", 0, 2, font="Do Hyeon", size=100),
                                          title("c", "다", 0, 2, size=100), title("d", "라", 0, 2, font="Comic Sans", size=100)]), 1920, 1080)
        st = {m[1]: m[2] for m in re.finditer(r"^Style: T(\w+),([^,]+),", ass, re.M)}
        sizes = {m[1]: int(m[2]) for m in re.finditer(r"^Style: T(\w+),[^,]+,(\d+),", ass, re.M)}
        self.assertEqual(st, {"a": "Black Han Sans", "b": "Do Hyeon", "c": "Pretendard Black", "d": "Pretendard Black"})
        self.assertEqual(sizes, {"a": 102, "b": 100, "c": 119, "d": 119})
        bold = {m[1]: m[2] for m in re.finditer(r"^Style: T(\w+),(?:[^,]+,){6}(-?\d+),", ass, re.M)}
        self.assertEqual(bold["a"], "0")


class NoCaps(unittest.TestCase):
    def test_replay_clip_does_not_repeat_speech_captions(self):
        it = lambda i, s, a, b, **k: dict({"id": i, "track": "V1", "media": "main", "start": s, "in": a, "out": b, "speed": 1.0}, **k)  # noqa: E731
        seq = {"items": [it("a", 0, 0, 4), it("r", 4, 1, 3, noCaps=True, speed=0.5), it("b", 8, 4, 6)], "titles": [], "shapes": [], "format": "long",
               "captions": [{"id": "c1", "start": 1.0, "end": 2.0, "text": "슛!"}, {"id": "c2", "start": 4.5, "end": 5.5, "text": "다음"}]}
        caps = editor.timeline_captions(seq)
        self.assertEqual([(round(c["start"], 2), c["text"]) for c in caps], [(1.0, "슛!"), (8.5, "다음")])
        self.assertAlmostEqual(editor._src_to_tl(seq["items"], 1.5), 1.5)
        self.assertIsNone(editor._src_to_tl([seq["items"][1]], 1.5))


def bbox(gray, thr=200):
    import numpy as np
    m = gray > thr
    ys, xs = np.where(m)
    return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None


class _Render(unittest.TestCase):
    """실제로 내보내 프레임에서 확인 (720p, 흰 글씨 · 검은 바탕) — 시험 재료 준비만."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="렌더 재료 시험 "))
        work = cls.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in list(dirs.values()) + [work / "edit_media"]:
            d.mkdir(parents=True, exist_ok=True)
        cls.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(editor, "ASSETS", work / "edit_media")]
        for p in cls.patches:
            p.start()
        r = core.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                      "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(work / "edit_media" / "black.mp4")])
        assert r.returncode == 0, r.stderr[-300:]
        cls.media = [{"id": "bk", "kind": "video", "src": "assets", "file": "black.mp4", "dur": 2.0, "w": 320, "h": 180, "fps": 30.0, "audio": True}]

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, titles):
        items = [{"id": "v", "track": "V1", "media": "bk", "start": 0, "in": 0, "out": 2, "speed": 1, "rev": False, "link": None, "fit": "auto", "fx": {}, "color": {}}]
        p = {"id": "s", "name": f"재료 {len(list(core.OUT.glob('*.mp4')))}", "format": "long", "v": 2, "captionsOn": False, "captionStyle": dict(editor.LONG_STYLE),
             "titles": titles, "shapes": [], "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": False, "lufs": -14}, "duck": {"on": False},
             "tracks": editor.default_tracks(), "items": items, "trans": [], "markers": [], "captions": [], "info": {"duration": 2.0, "width": 320, "height": 180, "fps": 30.0},
             "source": "시험.mp4", "media": self.media}
        out = editor.export("시험.mp4", p, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda m: None)
        return core.OUT / out[0]

    def frame(self, f, k):
        import numpy as np
        raw = subprocess.run([FF, "-v", "error", "-i", str(f), "-vf", f"select=eq(n\\,{k})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(720, 1280)



class RenderedFrames(_Render):
    def test_stamp_scale_matches_preview_curve(self):
        """쾅 찍기: 0초 165% → 0.12초 100% (미리보기 sc = 1.65 - 0.65·e/0.12 와 같은 직선)."""
        f = self.render([title("a", "가나다", 0, 2, effect="stamp", fill="#FFFFFF", strokeW=0, y=0.6, size=120)])
        w0 = bbox(self.frame(f, 0))
        w2 = bbox(self.frame(f, 2))  # 0.067초 → 1.65 - 0.65 × 0.556 = 1.289
        w9 = bbox(self.frame(f, 9))
        base = w9[2] - w9[0]
        self.assertAlmostEqual((w0[2] - w0[0]) / base, 1.65, delta=0.08)
        self.assertAlmostEqual((w2[2] - w2[0]) / base, 1.289, delta=0.08)

    def test_rot_and_font_render(self):
        """기울기 +12°(시계 방향): 글자 오른쪽 끝이 왼쪽 끝보다 아래 · 검은고딕으로 그려짐(글꼴이 없으면 폭이 다름)."""
        import numpy as np
        f = self.render([title("a", "가나다라마", 0, 2, effect="none", fill="#FFFFFF", strokeW=0, y=0.6, size=110, rot=12, font="Black Han Sans")])
        g = self.frame(f, 5) > 200
        ys, xs = np.where(g)
        left, right = ys[xs < np.percentile(xs, 20)].mean(), ys[xs > np.percentile(xs, 80)].mean()
        self.assertGreater(right - left, 25)
        f2 = self.render([title("a", "가나다라마", 0, 2, effect="none", fill="#FFFFFF", strokeW=0, y=0.6, size=110, font="Black Han Sans")])
        f3 = self.render([title("a", "가나다라마", 0, 2, effect="none", fill="#FFFFFF", strokeW=0, y=0.6, size=110, font="Do Hyeon")])
        b2, b3 = bbox(self.frame(f2, 5)), bbox(self.frame(f3, 5))
        # 미리보기 글자 폭 = PIL(em = size × 720/1080)로 잰 폭 — 내보낸 폭이 5% 안에서 같아야 함
        from PIL import Image, ImageDraw, ImageFont
        for b, fn in ((b2, "BlackHanSans-Regular.ttf"), (b3, "DoHyeon-Regular.ttf")):
            fo = ImageFont.truetype(str(editor.FONTS / fn), round(110 * 720 / 1080))
            im = Image.new("L", (1280, 400))
            ImageDraw.Draw(im).text((20, 100), "가나다라마", font=fo, fill=255)
            pb = bbox(np.array(im))
            self.assertAlmostEqual((b[2] - b[0]) / (pb[2] - pb[0]), 1.0, delta=0.05, msg=fn)

    def test_shake_wobbles_then_settles(self):
        import numpy as np
        f = self.render([title("a", "가나다라마", 0, 2, effect="shake", fill="#FFFFFF", strokeW=0, y=0.6, size=110)])

        def tilt(k):
            g = self.frame(f, k) > 200
            ys, xs = np.where(g)
            return ys[xs > np.percentile(xs, 80)].mean() - ys[xs < np.percentile(xs, 20)].mean()
        # 0.067초(2프레임): 미리보기 각도 = -6 + 12×(0.067-0.06)/0.06 ≈ -4.7° (반시계 → 오른쪽 끝이 위) · 0.3초 뒤엔 0°
        self.assertLess(tilt(2), -8)
        self.assertLess(abs(tilt(12)), 4)


class Loudness(_Render):
    """공 소리처럼 순간만 큰 소리가 섞이면 loudnorm 한 번으로는 목표까지 못 올리던 것 (-17 LUFS 로 남음) → 미리 키우고 순간 소리만 눌러 맞춤."""

    def test_peaky_mix_reaches_target_loudness(self):
        work = core.WORK
        r = core.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i",
                      "aevalsrc='0.05*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*3*t))+0.95*lt(mod(t,2),0.01)*sin(2*PI*90*t)':s=48000:d=12",
                      "-ac", "2", str(work / "edit_media" / "peaky.wav")])
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        md = {"id": "pk", "kind": "audio", "src": "assets", "file": "peaky.wav", "dur": 12.0, "w": 0, "h": 0, "fps": 30.0, "audio": True}
        items = [{"id": "v", "track": "V1", "media": "bk", "start": 0, "in": 0, "out": 2, "speed": 1, "rev": False, "link": None, "fit": "auto", "fx": {}, "color": {}},
                 {"id": "v2", "track": "V1", "media": "bk", "start": 2, "in": 0, "out": 2, "speed": 1, "rev": False, "link": None, "fit": "auto", "fx": {}, "color": {}},
                 {"id": "a", "track": "A1", "media": "pk", "start": 0, "in": 0, "out": 4, "speed": 1, "rev": False, "link": None, "fx": {}, "gain": 0, "fadeIn": 0,
                  "fadeOut": 0, "mute": False}]
        p = {"id": "s", "name": "소리 크기", "format": "long", "v": 2, "captionsOn": False, "captionStyle": dict(editor.LONG_STYLE), "titles": [], "shapes": [],
             "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": True, "lufs": -14}, "duck": {"on": False}, "tracks": editor.default_tracks(),
             "items": items, "trans": [], "markers": [], "captions": [], "info": {"duration": 2.0, "width": 320, "height": 180, "fps": 30.0},
             "source": "시험.mp4", "media": self.media + [md]}
        out = editor.export("시험.mp4", p, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda m: None)
        err = core.run([FF, "-hide_banner", "-nostats", "-i", str(core.OUT / out[0]), "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
        i_out = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])
        tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)[-1])
        self.assertLessEqual(abs(i_out + 14.0), 1.0, i_out)
        self.assertLessEqual(tp, -0.9, tp)


if __name__ == "__main__":
    unittest.main()
