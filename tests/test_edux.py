"""편집실 쓰기 편하게 (D-320~D-328) — 저장소 폴더에서 python3 -m unittest tests.test_edux
- 내보내기 자막·타이틀·도형이 미리보기와 같은 프레임에서 나오고 사라짐 (ASS 시각을 프레임 격자로 · ffmpeg subtitles 필터 셈과 견줌 · 실제로 태워 봄)
- '이 자막만' 자리(capPos) · 손으로 맞춘 자막 시간(tm) · 고친 자막 서명 · 늦게 시작하는 소리 줄기 · 파일 시작 시각
- node 가 있으면 editor.html capsTL(미리보기) = timeline_captions(내보내기) 를 손으로 맞춘 자막·쇼츠 조각·'이 자막만' 자리까지 견줌"""
import copy
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import autorun
import core
import editor

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
FF = core.ffmpeg()


def vf_ms(k, fps):
    """ffmpeg vf_subtitles 가 프레임 k 에 libass 로 넘기는 시각(ms): pts · av_q2d(1/fps) · 1000 → (long long)."""
    return int(k * (1.0 / fps) * 1000)


def ass_ms(s):
    h, m, x = s.split(":")
    sec, cs = x.split(".")
    return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int(cs) * 10


def first_shown(start_ms, fps, upto):
    return next(k for k in range(upto) if vf_ms(k, fps) >= start_ms)


def item(i, start, a, b, track="V1", media="main"):
    return {"id": i, "track": track, "media": media, "start": start, "in": a, "out": b, "speed": 1, "rev": False,
            "reframe": 0.5, "fit": "auto", "fx": {}, "color": {}}


def proj(caps, fmt="long", items=None, **kw):
    q = {"v": 2, "tracks": editor.default_tracks(), "format": fmt, "captionStyle": dict(editor.LONG_STYLE if fmt == "long" else editor.SHORTS_STYLE, effect="none"),
         "captionsOn": True, "titles": [], "shapes": [], "tactics": [], "items": items or [item("v", 0, 0, 30), item("a", 0, 0, 30, "A1")],
         "captions": copy.deepcopy(caps)}
    q.update(kw)
    return q


class FrameGridTest(unittest.TestCase):
    """D-328: 미리보기는 재생 위치 ≥ 시작인 첫 프레임부터 그림 → 내보내기도 같은 프레임 (예전 반올림은 약 36% 에서 한 프레임 늦었음)."""

    def test_vf_subtitles_model_zero_mismatch(self):
        for fps in (30, 60, 25, 24):
            bad = 0
            for k in range(0, 18001):
                for t in (k / fps, k / fps + 0.37 / fps, round(k / fps, 3)):
                    want = math.ceil(t * fps - 1e-6)
                    ms = ass_ms(editor._ass_ftime(t, fps))
                    if not (vf_ms(want, fps) >= ms and (want == 0 or vf_ms(want - 1, fps) < ms)):
                        bad += 1
            self.assertEqual(bad, 0, fps)

    def test_old_rounding_was_one_frame_late(self):
        """예전 _ass_time(반올림): 2.0667초(프레임 62)는 2.07 → 63번 프레임에서야 보였음 · 새 시각은 62번부터."""
        t = 62 / 30
        self.assertEqual(first_shown(ass_ms(editor._ass_time(t)), 30, 200), 63)
        self.assertEqual(first_shown(ass_ms(editor._ass_ftime(t, 30)), 30, 200), 62)
        late = sum(1 for k in range(3000) if first_shown(ass_ms(editor._ass_time(k / 30)), 30, 4000) != k)
        self.assertGreater(late, 900)  # 3000 중 약 1/3

    def test_whole_seconds_keep_old_text(self):
        for t in (0, 1.0, 2.0, 2.5, 5.8, 10.0, 60.0, 3599.0):
            self.assertEqual(editor._ass_ftime(t, 30), editor._ass_time(t), t)

    def test_build_ass_only_times_differ(self):
        caps = [{"id": "a", "start": 1.0 + 2 / 30, "end": 3.0 + 1 / 30, "text": "첫 자막"}, {"id": "b", "start": 4.37, "end": 6.0, "text": "둘째 자막"}]
        q = proj(caps, titles=[{"id": "t", "text": "제목", "start": 62 / 30, "dur": 1.0, "style": dict(editor.TITLE_STYLE)}],
                 shapes=[{"id": "s", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "color": "#FF0000", "opacity": 1, "radius": 0, "start": 65 / 30, "dur": 0.5}])
        new, old = editor.build_ass(q, 1920, 1080, 30), editor.build_ass(q, 1920, 1080, 30, frame_grid=False)
        nl, ol = new.splitlines(), old.splitlines()
        self.assertEqual(len(nl), len(ol))
        diff = 0
        for a, b in zip(nl, ol):
            if a.startswith("Dialogue: "):
                fa, fb = a.split(",", 3), b.split(",", 3)
                self.assertEqual((fa[0], fa[3]), (fb[0], fb[3]))
                diff += fa[1:3] != fb[1:3]
            else:
                self.assertEqual(a, b)
        self.assertGreaterEqual(diff, 2)

    @unittest.skipUnless(FF, "ffmpeg 가 없어요")
    def test_real_burn_first_frame(self):
        """실제로 태워 봄 (내보내기와 같은 settb=1/30 · subtitles): 프레임 60~71 에 시작하는 타이틀이 그 프레임부터 보이고 12프레임 뒤에 사라짐."""
        got = []
        with tempfile.TemporaryDirectory() as td:
            for k in range(60, 72):
                q = proj([], items=[item("v", 0, 0, 4)], titles=[{"id": "t", "text": "■■", "start": k / 30, "dur": 12 / 30,
                                                                   "style": dict(editor.TITLE_STYLE, size=60, fill="#FFFFFF", effect="none", y=0.7)}])
                (Path(td) / "subs.ass").write_text(editor.build_ass(q, 160, 90, 30), encoding="utf-8")
                r = subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=30:d=3.2", "-vf",
                                    f"settb=1/30,setpts=PTS+0,subtitles=subs.ass:fontsdir={editor.FONTS}", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                   cwd=td, capture_output=True)
                fr = np.frombuffer(r.stdout, np.uint8).reshape(-1, 90, 160)
                on = [i for i in range(len(fr)) if fr[i].max() > 128]
                got.append((k, on[0] if on else None, on[-1] if on else None))
        self.assertEqual([(k, a, b) for k, a, b in got if (a, b) != (k, k + 11)], [])


class CapPosTest(unittest.TestCase):
    """D-321: 미리보기에서 Alt+끌기 = 이 자막만 자리 (편집본마다 capPos) — 내보내기도 그 자리 · 없으면 예전과 같은 결과."""

    caps = [{"id": "a", "start": 1.0, "end": 3.0, "text": "첫 자막"}, {"id": "b", "start": 4.0, "end": 6.0, "text": "둘째 자막"}]

    def test_cap_pos_validation(self):
        self.assertIsNone(editor.cap_pos(None))
        self.assertIsNone(editor.cap_pos({"x": "a", "y": True}))
        self.assertEqual(editor.cap_pos({"x": 1.4, "y": -1}), {"x": 1.0, "y": 0.03})
        self.assertEqual(editor.cap_pos({"y": 0.5}), {"y": 0.5})
        self.assertIsNone(editor.cap_pos({"x": float("nan")}))

    def test_no_override_same_dicts(self):
        self.assertTrue(all("pos" not in c for c in editor.timeline_captions(proj(self.caps))))

    def test_override_one_caption_in_export(self):
        q = proj(self.caps, capPos={"b": {"x": 0.3, "y": 0.4}, "gone": {"y": 0.1}})
        tl = editor.timeline_captions(q)
        self.assertEqual([c.get("pos") for c in tl], [None, {"x": 0.3, "y": 0.4}])
        lines = [ln for ln in editor.build_ass(q, 1920, 1080).splitlines() if ln.startswith("Dialogue: 1,")]
        self.assertIn(r"\pos(960,972)", lines[0])          # 트랙 자리 (y 0.9)
        self.assertIn(r"\pos(576,432)", lines[1])          # 이 자막만 (0.3, 0.4)

    def test_override_beats_c2_row_height(self):
        caps = [dict(self.caps[0], trk="C2")]
        q = proj(caps, capPos={"a": {"y": 0.5}})
        line = next(ln for ln in editor.build_ass(q, 1920, 1080).splitlines() if ln.startswith("Dialogue:") and ",Cap2," in ln)
        self.assertIn(r"\pos(960,540)", line)


class HandTimedTest(unittest.TestCase):
    """D-322: 손으로 시간을 맞춘 자막(tm)은 맞춘 그대로 (최소 0.7초·읽을 시간으로 늘리지 않음) · 고친 자막 서명에 들어감."""

    def test_tm_caption_not_stretched(self):
        caps = [{"id": "a", "start": 1.0, "end": 1.3, "text": "짧게"}, {"id": "b", "start": 1.3, "end": 3.0, "text": "다음 자막"}]
        auto = editor.timeline_captions(proj(caps))
        self.assertAlmostEqual(auto[0]["end"], 1.7, places=6)   # CAP_MIN 0.7초 · 다음 자막은 늦춤
        self.assertAlmostEqual(auto[1]["start"], 1.7, places=6)
        caps[0]["tm"] = 1
        hand = editor.timeline_captions(proj(caps))
        self.assertAlmostEqual(hand[0]["end"], 1.3, places=6)
        self.assertAlmostEqual(hand[1]["start"], 1.3, places=6)

    def test_tm_next_not_pushed(self):
        caps = [{"id": "a", "start": 1.0, "end": 1.3, "text": "짧게"}, {"id": "b", "start": 1.3, "end": 3.0, "text": "다음 자막", "tm": 1}]
        out = editor.timeline_captions(proj(caps))
        self.assertAlmostEqual(out[1]["start"], 1.3, places=6)  # 손으로 맞춘 다음 자막은 그대로
        self.assertAlmostEqual(out[0]["end"], 1.3, places=6)    # 앞 자막은 그 앞까지만
        self.assertTrue(all("_tm" not in c for c in out))

    def test_signature_includes_tm(self):
        a = [{"id": "a", "text": "글"}]
        b = [{"id": "a", "text": "글", "tm": 1}]
        self.assertNotEqual(editor._cap_text(a), editor._cap_text(b))
        self.assertEqual(editor._cap_text([{"text": "글", "trk": "C2"}]), "글\tC2")

    def test_autorun_keeps_hand_timed(self):
        """다시 들은 받아쓰기(끝까지 자동으로): 손으로 시간만 맞춘 자막도 '고친 자막' → 몰래 바꾸지 않음 (autorun.add_to_project · 편집실 msgCaps 와 같은 서명)."""
        heard_from = [{"id": "a", "start": 2.08, "end": 4.6, "text": "퍼스트 터치"}]
        relistened = [{"id": "z", "start": 2.1, "end": 4.6, "text": "퍼스트 터치를"}]
        for cur, replaced in (([dict(heard_from[0])], True), ([dict(heard_from[0], start=2.38, tm=1)], False)):
            store = {"captions": cur, "sequences": [], "media": []}
            saved = []
            with mock.patch.object(editor, "load_project", lambda n: copy.deepcopy(store)), \
                    mock.patch.object(editor, "save_project", lambda n, pj, *a, **k: saved.append(pj)):
                autorun.add_to_project("x.mp4", [], [], relistened, heard_from)
            self.assertEqual(saved[-1]["captions"] == relistened, replaced, cur)


class AudioStartTest(unittest.TestCase):
    """D-324: 소리 줄기가 늦게 시작하는 파일 — 파형·받아쓰기·내보내기 소리를 파일 시작 기준 제자리로 · 보통 파일은 같은 소리 그대로."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        d = Path(cls.td.name)
        cls.late, cls.plain, cls.st1 = d / "late.mp4", d / "plain.mp4", d / "st1.mp4"
        beep = "aevalsrc='if(between(t,1,1.2),0.8*sin(2*PI*1000*t),0)':s=48000:d=3"
        for out, off in ((cls.late, ["-itsoffset", "0.5"]), (cls.plain, [])):
            subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x36:r=30:d=4", *off, "-f", "lavfi", "-i", beep,
                            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac", "-t", "4", str(out)], check=True)
        subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x36:r=30:d=3", "-c:v", "libx264", "-output_ts_offset", "1", str(cls.st1)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def onset(self, path, af):
        r = subprocess.run([FF, "-v", "error", "-i", str(path), "-vn", *(["-af", af] if af else []), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True)
        a = np.abs(np.frombuffer(r.stdout, np.int16).astype(float))
        k = np.where(a > 3000)[0]
        return k[0] / 8000 if len(k) else None, r.stdout

    def test_late_audio_in_place(self):
        before, _ = self.onset(self.late, None)
        after, _ = self.onset(self.late, editor.AUDIO_T0)
        self.assertAlmostEqual(before, 1.0, delta=0.03)  # 예전: 0.5초 이름
        self.assertAlmostEqual(after, 1.5, delta=0.03)

    def test_plain_file_same_samples(self):
        _, a = self.onset(self.plain, None)
        _, b = self.onset(self.plain, editor.AUDIO_T0)
        self.assertEqual(a, b)

    def test_probe_start(self):
        self.assertAlmostEqual(editor.probe(self.st1)["start"], 1.0, delta=0.01)
        self.assertAlmostEqual(editor.probe(self.plain)["start"], 0.0, delta=0.01)


@unittest.skipUnless(NODE, "node 가 없어요")
class PreviewParityTest(unittest.TestCase):
    """editor.html capsTL(미리보기) = timeline_captions(내보내기): 손으로 맞춘 자막(tm) · 쇼츠 조각 · 컷으로 잘린 자막."""

    def run_js(self, cases):
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        out = []
        for name in ("const FPS =", "const EPS =", "const sp =", "const iEnd =", "const iLen =", "const mt =", "const tlOf =", "const aTracks ="):
            i = src.index(name)
            out.append(src[i:src.index("\n", i)])
        for a in ("function mergeIv(", "function minusIv("):
            i = src.index(a)
            out.append(src[i:src.index("\n}\n", i) + 2])
        i = src.index("function capWords(")
        blk = src[i:src.index("// 한 줄 자막이 화면 밖으로", i)]
        self.assertNotIn("document", blk)
        out.append(blk)
        prog = "let S, P, ver = 0;\nconst total = () => Math.max(0, ...S.items.map(iEnd));\n" + "\n".join(out) + """
const inp = JSON.parse(require('fs').readFileSync(0, 'utf8')), res = [];
for (const c of inp) { S = { items: c.items, tracks: c.tracks, format: c.format, capPos: c.capPos }; P = { captions: c.captions }; ver++;
  res.push(capsTL().map(x => [x.s, x.e, x.text, x.trk, x.mid === 'main' ? capPosOf(x.cap.id) : null])); }
process.stdout.write(JSON.stringify(res));"""
        r = subprocess.run([NODE, "-e", prog], input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_parity(self):
        caps = [{"id": "a", "start": 1.0, "end": 1.3, "text": "짧게", "tm": 1}, {"id": "b", "start": 1.3, "end": 3.0, "text": "다음 자막"},
                {"id": "c", "start": 3.1, "end": 3.4, "text": "또 짧게"}, {"id": "d", "start": 3.4, "end": 6.9, "text": "퍼스트 터치 꿀팁을 알려드릴게요 오늘은",
                                                                          "sh": [2, 4], "shn": 5, "tm": 1},
                {"id": "e", "start": 7.0, "end": 7.2, "text": "끝", "trk": "C2"}]
        items = [item("v", 0, 0.5, 4.0), item("a", 0, 0.5, 4.0, "A1"), item("v2", 3.5, 4.0, 9.0), item("a2", 3.5, 4.0, 9.0, "A1")]
        cases = [proj(caps, items=items), proj(caps, items=items, fmt="shorts"), proj(caps, items=items, fmt="shorts", capPos={"d": {"x": 0.3, "y": 0.45}, "zz": {"y": 2}})]
        js = self.run_js(cases)
        for c, j in zip(cases, js):
            py = editor.timeline_captions(c)
            self.assertEqual([[round(x["start"], 4), round(x["end"], 4), x["text"], x.get("trk", "C1"), x.get("pos")] for x in py],
                             [[round(a, 4), round(b, 4), t, k, p] for a, b, t, k, p in j])
        self.assertTrue(any(p for *_, p in js[2]))


if __name__ == "__main__":
    unittest.main()
