"""고정 틀 세로 쇼츠 배우기 (BR-255~BR-257 · D-440~D-443) — 저장소 폴더에서 python3 -m unittest tests.test_style_frame

정답 영상(tests/make_fixture.make_frame_fixture)은 ffmpeg lavfi 로 만든 360×640 세로 쇼츠:
위 파랑 띠(흰 상자 + 검은 제목 + 흰 사실 줄 · 내내 그대로) · 가운데 움직이는 영상 창(30~72%) · 아래 주황 띠(노란 말 자막이 바뀜 + 작은 흰 글씨 그대로).
- style.frame_layout: 안 바뀌는 띠와 움직이는 영상 창을 찾고, 띠에 늘 떠 있는 글자 자리를 남김 (세로 비율 그대로인 작은 화면으로)
- 늘 떠 있는 글자는 자막 통계(captionPos·captionColor)와 기획 분석 자막(plan · 강조 색)에서 빠짐
- 배운 화면 배치는 프로필 layout · edit_params 의 layout(쇼츠만 섞어 배우면 byFormat.shorts.layout) · msg.learned_aspects 의 layout 에 알려 주기만 함
- 가로 영상·틀 없는 세로 쇼츠는 예전과 똑같음 (기록·프로필·값)
인터넷은 쓰지 않음 (글자 읽기 모델이 있으면 실제로 읽어 보는 시험도 함)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import avmodels  # noqa: E402
import core  # noqa: E402
import msg  # noqa: E402
import plan  # noqa: E402
import style  # noqa: E402
import make_fixture as fx  # noqa: E402

FRAME, PLAIN, LAND, RAW = "고정 틀 쇼츠.mp4", "틀 없는 쇼츠.mp4", "가로로 편 영상.mp4", "고정 틀 쇼츠 (틀 안 봄).mp4"
LEGACY_EV = {"v", "sig", "source", "duration", "analyzed", "cuts", "zooms", "fps", "text", "capColors", "silences", "rmsStep", "rms",
             "motion", "lufs", "speech", "look"}
M = {}


def quiet(*a, **k):
    pass


def setUpModule():
    tmp = Path(tempfile.mkdtemp(prefix="고정 틀 시험 "))
    work = tmp / "풋살 작업 폴더"
    dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    M["tmp"] = tmp
    M["patches"] = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(style, "STYLES", work / "styles")]
    for p in M["patches"]:
        p.start()
    v = dirs["VIDEOS"]
    M["truth"] = fx.make_frame_fixture(v / FRAME)
    fx.make_frame_fixture(v / PLAIN, bands=False)
    shutil.copy2(v / FRAME, v / RAW)
    fx.reencode(v / FRAME, v / LAND, size="640x360", crf=20)  # 같은 틀을 가로로 편 영상 — 가로 영상은 틀을 찾지 않음
    M["ev"] = style.extract_events(FRAME, quiet)
    M["prof"] = style.summarize(M["ev"])


def tearDownModule():
    for p in M.get("patches", []):
        p.stop()
    shutil.rmtree(M.get("tmp", ""), ignore_errors=True)


def near_hex(a, b, tol=14):
    x, y = style._hex_rgb(a), style._hex_rgb(b)
    return x is not None and y is not None and all(abs(p - q) <= tol for p, q in zip(x, y))


class FrameDetect(unittest.TestCase):
    def test_frame_found(self):
        fr, t = M["ev"]["frame"], M["truth"]
        self.assertTrue(fr["found"], fr)
        self.assertAlmostEqual(fr["win"][0], t["window"][0], delta=0.02)
        self.assertAlmostEqual(fr["win"][1], t["window"][1], delta=0.02)
        self.assertTrue(near_hex(fr["top"], t["top"]) and near_hex(fr["bottom"], t["bottom"]), fr)
        self.assertEqual(fr["bar"], fr["top"], "위 띠가 더 커서 상자 바깥 색은 위 띠 색")

    def test_fixed_text_boxes(self):
        boxes = M["ev"]["frame"]["text"]
        x0, y0, x1, y1 = M["truth"]["titleBox"]
        self.assertTrue(plan.in_fixed([x0 + 0.05, y0 + 0.01, x1 - 0.05, y1 - 0.01], boxes), boxes)   # 흰 상자 제목
        self.assertTrue(plan.in_fixed([0.2, 0.2, 0.8, 0.23], boxes), boxes)                         # 흰 사실 줄
        self.assertTrue(plan.in_fixed([0.4, 0.965, 0.6, 0.985], boxes), boxes)                      # 맨 아래 작은 글씨
        self.assertFalse(plan.in_fixed([0.18, 0.82, 0.82, 0.858], boxes), "바뀌는 말 자막 자리는 늘 떠 있는 글자가 아님")
        self.assertTrue(all(b[3] <= fr_win_top() or b[1] >= M["ev"]["frame"]["win"][1] for b in boxes), "띠 안에서만")

    def test_captions_not_polluted(self):
        """틀을 보지 않으면 늘 떠 있는 제목·사실 줄 때문에 자막이 '위·흰색'으로 잡힘 → 틀을 보면 '아래·노랑' (말 자막 그대로)."""
        p = M["prof"]
        self.assertEqual((p["captionPos"], p["captionColor"]), ("bottom", M["truth"]["captionColor"]))
        self.assertEqual(p["captionBands"]["top"], 0.0)
        with mock.patch.object(style, "_frame_of", return_value=(None, None)):
            raw = style.summarize(style.extract_events(RAW, quiet))
        self.assertEqual(raw["captionPos"], "top", raw["captionBands"])
        self.assertEqual(raw["captionColor"], "#FFFFFF")
        self.assertEqual(raw["captionBands"]["bottom"], p["captionBands"]["bottom"], "아래 말 자막은 그대로 셈")

    def test_layout_stored(self):
        lay = M["prof"]["layout"]
        self.assertEqual((lay["count"], lay["of"]), (1, 1))
        self.assertAlmostEqual(lay["zoom"], (0.72 - 0.30) / style.BOX_SRC_H, delta=0.06)  # 16:9 화면을 약 1.33배
        self.assertAlmostEqual(lay["vpos"], 0.52, delta=0.03)
        prm = style.edit_params(M["prof"])["layout"]
        self.assertEqual({k: prm[k] for k in ("mode", "bar", "zoom", "vpos", "cropTop", "cropBottom")},
                         {"mode": "box", "bar": lay["bar"], "zoom": lay["zoom"], "vpos": lay["vpos"], "cropTop": 0.0, "cropBottom": 0.0})
        self.assertEqual(prm["window"], lay["window"])
        d = style.describe(M["prof"])
        self.assertIn("고정 틀", d)
        self.assertIn("저절로 쓰지 않아요", d)
        # 저장했다 다시 읽어도 (스타일 파일 = JSON) 그대로
        self.assertEqual(style.edit_params(json.loads(json.dumps(M["prof"])))["layout"], prm)

    def test_box_layout_matches_editor_fit(self):
        """배운 zoom·vpos 를 편집실 상자 배치(editor._fit)에 넣으면 16:9 영상 창이 배운 자리에 놓임."""
        import editor
        lay = style.edit_params(M["prof"])["layout"]
        seq = {"format": "shorts", "layout": {k: lay[k] for k in ("mode", "bar", "zoom", "vpos", "cropTop", "cropBottom")}}
        f = editor._fit({"track": "V1"}, {"w": 1920, "h": 1080, "kind": "video"}, seq, 1080, 1920)
        y0, y1 = f["by"] / 1920, (f["by"] + f["bh"]) / 1920
        self.assertAlmostEqual(y0, lay["window"][0], delta=0.02)
        self.assertAlmostEqual(y1, lay["window"][1], delta=0.02)
        self.assertEqual(f["bar"], lay["bar"])


def fr_win_top():
    return M["ev"]["frame"]["win"][0]


class OldBehaviour(unittest.TestCase):
    def test_plain_vertical_same_as_before(self):
        """틀이 없는 세로 쇼츠: 틀 기록은 found=False 만 더해지고, 글자 밀도·자막 색·프로필은 틀을 안 볼 때와 똑같음."""
        ev = style.extract_events(PLAIN, quiet)
        self.assertEqual(ev["frame"]["found"], False)
        (core.adir(PLAIN) / "style_events.json").unlink()
        with mock.patch.object(style, "_frame_of", return_value=(None, None)):
            old = style.extract_events(PLAIN, quiet)
        for k in ("text", "capColors", "cuts", "zooms", "motion", "look"):
            self.assertEqual(ev[k], old[k], k)
        a, b = style.summarize(ev), style.summarize(old)
        a.pop("analyzed")
        b.pop("analyzed")
        self.assertEqual(a, b)
        self.assertNotIn("layout", a)

    def test_landscape_never_looks_for_frame(self):
        boom = mock.Mock(side_effect=AssertionError("가로 영상은 틀을 찾지 않음"))
        with mock.patch.object(style, "_frame_samples", boom), mock.patch.object(style, "frame_layout", boom):
            ev = style.extract_events(LAND, quiet)
        self.assertEqual(set(ev), LEGACY_EV)
        p = style.summarize(ev)
        self.assertNotIn("layout", p)
        self.assertNotIn("layout", style.edit_params(p))
        self.assertNotIn("고정 틀", style.describe(p))

    def test_text_bands_without_mask_unchanged(self):
        import numpy as np
        rgb = np.random.default_rng(3).integers(0, 255, (style.FH, style.FW, 3), dtype=np.uint8)
        a, b = style._text_bands(rgb), style._text_bands(rgb, None)
        self.assertEqual(a[1], b[1])
        self.assertTrue((a[2] == b[2]).all())

    def test_old_events_without_frame(self):
        """예전 기록(frame 없음)·틀 없는 프로필은 화면 배치 값이 아예 없음 (스타일 파일·가편집 값이 예전과 같음)."""
        ev = {k: v for k, v in M["ev"].items() if k != "frame"}
        self.assertNotIn("layout", style.summarize(ev))
        self.assertIsNone(plan.fixed_text(ev))
        pe = {"ocr": [{"t": 0, "d": 1, "lines": [{"text": "a", "box": [0.1, 0.1, 0.9, 0.16]}]}]}
        self.assertIs(plan.ocr_lines(pe, ev), pe["ocr"])


class FrameLayoutUnit(unittest.TestCase):
    def frames(self, n=10, top=48, bottom=112, seed=1):
        import numpy as np
        rng = np.random.default_rng(seed)
        out = []
        for _ in range(n):
            f = np.zeros((style.FRAME_H, style.FRAME_W, 3), np.uint8)
            f[:top] = (30, 90, 168)
            f[bottom:] = (232, 134, 42)
            f[top:bottom] = rng.integers(0, 255, (bottom - top, style.FRAME_W, 3))
            out.append(f)
        return out

    def test_found_and_not_found(self):
        fr, still = style.frame_layout(self.frames())
        self.assertTrue(fr["found"])
        self.assertEqual(fr["win"], [0.3, 0.7])
        self.assertEqual((fr["top"], fr["bottom"]), ("#1E5AA8", "#E8862A"))
        self.assertEqual(still.shape, (style.FRAME_H, style.FRAME_W))
        self.assertFalse(still[60].any(), "영상 창은 늘 바뀌는 칸")
        self.assertFalse(style.frame_layout(self.frames(n=style.FRAME_MIN_N - 1))[0]["found"], "장면이 너무 적음")
        self.assertFalse(style.frame_layout(self.frames(top=2))[0]["found"], "위 띠가 없음 (6% 안 됨)")
        self.assertFalse(style.frame_layout(self.frames(top=0, bottom=style.FRAME_H))[0]["found"], "화면 전체가 움직임")
        self.assertEqual(style.frame_layout(self.frames(top=0, bottom=style.FRAME_H))[1], None)

    def test_majority_of_shorts(self):
        f = {"v": 1, "found": True, "win": [0.28, 0.7], "top": "#111111", "bottom": "#222222", "bar": "#111111", "text": []}
        g = {"v": 1, "found": True, "win": [0.3, 0.72], "top": "#111111", "bottom": "#333333", "bar": "#111111", "text": []}
        no = {"v": 1, "found": False, "n": 30}
        lay = style._layout_of([f, g, no])
        self.assertEqual((lay["count"], lay["of"], lay["bar"]), (2, 3, "#111111"))
        self.assertEqual(lay["window"], [0.29, 0.71])
        self.assertIsNone(style._layout_of([f, no, no]), "고정 틀이 절반이 안 되면 배치를 배우지 않음")
        self.assertIsNone(style._layout_of([None, None]))
        # 기록 없이 프로필만 합칠 때도 같은 규칙
        p1, p2, p3 = {"layout": style._layout_of([f])}, {"layout": style._layout_of([g])}, {"format": "shorts"}
        self.assertEqual((style._merge_layouts([p1, p2, p3])["count"], style._merge_layouts([p1, p2, p3])["of"]), (2, 3))
        self.assertIsNone(style._merge_layouts([p1, p3, {"format": "shorts"}]))
        self.assertIsNone(style._merge_layouts([{"format": "long"}]))

    def test_merge_events_carries_frames(self):
        land = style.extract_events(LAND, quiet)
        m = style.merge([M["prof"], style.summarize(land)], [M["ev"], land])
        self.assertEqual((m["layout"]["count"], m["layout"]["of"]), (1, 1), "가로 영상은 틀 투표에 들지 않음")
        m2 = style.merge([style.summarize(land), style.summarize(land)], [land, land])
        self.assertNotIn("layout", m2)


class Formats(unittest.TestCase):
    def test_byformat_shorts_layout_only(self):
        """쇼츠·긴 영상을 섞어 배운 스타일: 화면 배치는 byFormat.shorts.layout 에만 (롱폼 값에는 없음)."""
        land = style.extract_events(LAND, quiet)
        ps, pl = dict(M["prof"], format="shorts"), dict(style.summarize(land), format="long")
        prof = style.merge([ps, pl], [M["ev"], land])
        prof["refs"] = [ps, pl]
        prof["formats"] = style.split_formats([ps, pl], [M["ev"], land])
        prm = style.edit_params(prof)
        self.assertEqual(prm["byFormat"]["shorts"]["layout"]["mode"], "box")
        self.assertNotIn("layout", prm["byFormat"]["long"])
        self.assertNotIn("layout", style.edit_params(prof, "long"))
        self.assertIn("고정 틀", style.describe(prof))


class MsgInfo(unittest.TestCase):
    def test_learned_aspects_layout_is_info_only(self):
        prm = style.edit_params(M["prof"])
        st = {"params": prm, "plan": {}}
        a = msg.learned_aspects(st)
        self.assertEqual(a["layout"], prm["layout"])
        b = msg.learned_aspects({"params": {k: v for k, v in prm.items() if k != "layout"}, "plan": {}})
        self.assertNotIn("layout", b)
        self.assertEqual({k: a[k] for k in msg.ASPECTS}, {k: b[k] for k in msg.ASPECTS}, "다섯 부분 값은 그대로 (화면 배치를 저절로 쓰지 않음)")
        mixed = {"params": {"byFormat": {"shorts": {"layout": {"mode": "box", "zoom": 1.3}}, "long": {}}}, "plan": {}}
        self.assertEqual(msg.learned_aspects(mixed)["layout"]["zoom"], 1.3)


class PlanCaptions(unittest.TestCase):
    def ocr(self):
        """글자 읽기 흉내: 내내 떠 있는 주황 제목(띠 안) + 바뀌는 노란 말 자막(아래 띠)."""
        title = {"text": "발바닥으로 공 멈추기", "box": [0.12, 0.10, 0.88, 0.16], "h": 0.055, "conf": 0.95, "color": "#FF9F1C", "cname": "orange", "boxed": True}
        out = []
        for k, (a, b, t) in enumerate(fx.FRAME_CAPS):
            out.append({"t": int(a), "d": 2, "lines": [dict(title), {"text": t, "box": [0.2, 0.82, 0.8, 0.858], "h": 0.038, "conf": 0.95,
                                                                     "color": "#FFE14D", "cname": "yellow", "boxed": False}]})
        return {"duration": fx.VDUR, "ocr": out}

    def test_fixed_title_left_out(self):
        pe, sev = self.ocr(), M["ev"]
        caps = plan.group_captions(plan.ocr_lines(pe, sev), [], pe["duration"])
        self.assertTrue(caps and all(c["pos"] == "bottom" and c["cname"] == "yellow" for c in caps), caps)
        j = plan.judge_captions(pe, {"caps": caps}, [], sev)
        self.assertEqual(j["colors"], ["#FFE14D"])
        # 틀을 모르면(예전) 주황 제목이 강조 색 후보로 섞임
        raw = plan.group_captions(pe["ocr"], [], pe["duration"])
        jr = plan.judge_captions(pe, {"caps": raw}, [], {})
        self.assertIn("#FF9F1C", jr["colors"])

    @unittest.skipUnless(avmodels.ready("ocr"), "글자 읽기 모델이 없어요 (~/.futsal-studio/models)")
    def test_real_ocr(self):
        """실제 글자 읽기: 틀 제목은 자막 사건에서 빠지고 말 자막만 남음."""
        with mock.patch.object(plan, "OCR_SLOW", float("inf")):
            pe = plan.extract_plan(FRAME, M["ev"], quiet)
        self.assertTrue(any("멈추기" in ln["text"] for o in pe["ocr"] for ln in o["lines"]), "제목을 읽긴 읽음")
        caps = plan.detect(pe, M["ev"], [])["caps"]
        self.assertTrue(caps, pe["ocr"])
        self.assertFalse([c for c in caps if "멈추기" in c["text"] and c["pos"] == "top"], caps)
        self.assertTrue(all(c["pos"] == "bottom" for c in caps), caps)
        raw = plan.detect(pe, {k: v for k, v in M["ev"].items() if k != "frame"}, [])["caps"]
        self.assertTrue([c for c in raw if c["pos"] == "top"], "틀을 모르면 제목이 자막으로 섞였음")


if __name__ == "__main__":
    unittest.main()
