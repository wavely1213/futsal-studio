"""썸네일 스타일 배우기(D-130)·A/B 이긴 것(D-131) — 저장소 폴더에서 python3 -m unittest tests.test_thumb_style

- 재기(thumbstyle.measure): 시험 그림 5장(tests/fixtures/thumb_style · make_thumb_style_fixture.py) — 쌈바형(어두운 배경·아래 노란 큰 글자·흰 테두리 누끼
  · 한 줄 안 노란 강조 낱말 + 흰 글자 + 그림자)과 토크형(밝은 배경·큰 얼굴·흰 글자 + 노란 둘째 줄·검은 테두리)이 서로 다른 버릇으로 나오는지.
  색은 역할(강조색·바탕 글자색·큰 줄을 어떻게 칠했나)로 · 그림자는 테두리가 아님 · 어두운 배경 위 테두리는 '잴 수 없음'. 글자·얼굴 상자는 boxes.json 으로 줌 (모델 안 씀)
- 버릇 → 편집기 값(params)·쉬운 한 줄(describe) · 썸네일 받기(fetch: 큰 크기부터 · 기억 · 인터넷 안 되면 멈춤) ·
  스타일에 배우기(learn: 스타일 파일 thumb · 다시 배울 때 잰 값 재사용 · 채널 인기 썸네일 더 보기) · 썸네일에 쓸 스타일(pick) ·
  A/B 묶음 기록·이긴 장(record_ab·set_winner·ours) · 브랜드 키트에서 직접 바꾼 색(thumb.brand_custom)
인터넷·모델은 쓰지 않음 (i.ytimg.com 은 _get 을 바꿔 끼움)."""
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import style  # noqa: E402
import thumb  # noqa: E402
import thumbstyle as ts  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "thumb_style"
BOXES = json.loads((FIX / "boxes.json").read_text(encoding="utf-8"))
YELLOW, WHITE = ts.PALETTE["yellow"], ts.PALETTE["white"]
# 영상 id(11자) → 시험 그림
IDS = {"darkAAAAAA1": "dark_1.jpg", "darkAAAAAA2": "dark_2.jpg", "darkAAAAAA3": "dark_3.jpg", "brightBBBB1": "bright_1.jpg", "brightBBBB2": "bright_2.jpg"}


def meas(name):
    return ts.measure(FIX / name, BOXES[name]["lines"], BOXES[name]["faces"])


class MeasureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = {n: meas(n) for n in BOXES}

    def test_dark_yellow_bottom(self):
        for n in ("dark_1.jpg", "dark_2.jpg"):
            m = self.m[n]
            self.assertEqual(m["colors"][0], YELLOW, n)
            self.assertEqual(m["pos"], "bottom", n)
            self.assertEqual(m["lines"], 1, n)
            self.assertGreater(m["textH"], 0.15, n)
            self.assertLess(m["bright"], 0.35, n)
            self.assertGreater(m["yellow"], 0.05, n)
            self.assertEqual(m["roles"], {"mode": "line", "base": None, "accent": YELLOW}, f"{n}: 큰 줄 전체가 노랑")

    def test_bright_white_two_lines(self):
        for n in ("bright_1.jpg", "bright_2.jpg"):
            m = self.m[n]
            self.assertEqual(m["roles"], {"mode": "stack", "base": WHITE, "accent": YELLOW, "top": "hl2"}, f"{n}: 같은 크기 두 줄 — 위 흰(바탕) · 아래 노랑(강조)")
            self.assertEqual(m["lines"], 2, n)
            self.assertLess(m["textH"], 0.15, n)
            self.assertGreater(m["bright"], 0.7, n)
            self.assertEqual(ts.outline_tier(m["outline"]), 2, f"{n}: 검은 테두리 9px → 두껍게 ({m['outline']})")
            self.assertAlmostEqual(m["face"], 0.417, places=2)

    def test_outline_unknown_on_dark(self):
        for n in ("dark_1.jpg", "dark_2.jpg"):
            self.assertIsNone(self.m[n]["outline"], f"{n}: 어두운 배경 위 글자는 테두리를 잴 수 없음 (배경 어둠을 테두리로도, '없음'으로도 세지 않음)")
        self.assertIsNone(ts.outline_tier(None))

    def test_shadow_is_not_outline(self):
        m = self.m["dark_3.jpg"]
        self.assertEqual(ts.outline_tier(m["outline"]), 0, f"아래로 떨어진 그림자는 테두리가 아님 ({m['outline']})")

    def test_word_emphasis_roles(self):
        m = self.m["dark_3.jpg"]
        self.assertEqual(m["roles"], {"mode": "word", "base": WHITE, "accent": YELLOW}, "한 줄 안 노란 강조 낱말 + 흰 글자 → 바탕 흰색 · 강조 노랑 (뒤바꾸지 않음)")
        self.assertEqual(m["lines"], 1)

    def test_cutout_thin_white_line(self):
        self.assertGreater(min(self.m["dark_1.jpg"]["cutout"], self.m["dark_2.jpg"]["cutout"]), 0.003)
        self.assertLess(max(self.m["bright_1.jpg"]["cutout"], self.m["bright_2.jpg"]["cutout"]), 0.001)

    def test_no_models_no_text_values(self):
        with mock.patch.object(ts, "ocr_lines", return_value=None), mock.patch.object(ts, "face_boxes", return_value=None):
            m = ts.measure(FIX / "dark_1.jpg")
        self.assertFalse(m["ocr"])
        self.assertIsNone(m["lines"])
        self.assertIsNone(m["textH"])
        self.assertIsNone(m["face"])
        self.assertLess(m["bright"], 0.35, "밝기·채도·노랑 비율은 모델 없이도")

    def test_letterbox_crop(self):
        from PIL import Image
        im = Image.new("RGB", (480, 360), (0, 0, 0))
        im.paste(Image.new("RGB", (480, 270), (250, 250, 250)), (0, 45))
        m = ts.measure(im, [], [])
        self.assertGreater(m["bright"], 0.95, "hqdefault(4:3) 위아래 검은 띠는 잘라 냄")

    def test_classify(self):
        import numpy as np
        a = np.array([[[255, 225, 77], [255, 255, 255], [17, 17, 17], [215, 205, 190], [0, 209, 255], [255, 59, 48]]], np.uint8)
        names = [ts.CLS[i] for i in ts.classify(a)[0]]
        self.assertEqual(names, ["yellow", "white", "black", "none", "cyan", "red"], "베이지 배경(215,205,190)은 흰 글자가 아님")


class HabitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dark = ts.summarize([meas("dark_1.jpg"), meas("dark_2.jpg"), meas("dark_3.jpg")])
        cls.bright = ts.summarize([meas("bright_1.jpg"), meas("bright_2.jpg")])

    def test_summaries_differ(self):
        d, b = self.dark, self.bright
        self.assertEqual((d["accent"], d["base"], d["mode"]), (YELLOW, WHITE, "line"), "쌈바형: 노랑 강조 · 큰 줄 전체 노랑(2장) > 강조 낱말(1장)")
        self.assertEqual((b["accent"], b["base"], b["mode"], b["stackTop"]), (YELLOW, WHITE, "stack", "hl2"), "토크형: 위 흰 · 아래 노랑")
        self.assertEqual(d["colors"], [YELLOW, WHITE], "화면용 색 목록은 [강조색, 바탕 글자색]")
        self.assertEqual((d["lines"], b["lines"]), (1, 2))
        self.assertEqual(d["pos"], "bottom")
        self.assertEqual(d["posShare"]["bottom"], 1.0)
        self.assertEqual((d["cutout"], b["cutout"]), (0.67, 0.0), "흰 테두리 누끼")
        self.assertEqual((d["outlineN"], b["outlineN"]), (1, 2), "어두운 배경 위 글자는 테두리를 잰 장 수에서 빠짐")
        self.assertEqual((d["faceShare"], b["faceShare"]), (0.0, 1.0))
        self.assertLess(d["bright"], 0.4, "어두운 배경")
        self.assertGreater(b["bright"], 0.7)

    def test_params_dark(self):
        p = ts.params(self.dark)
        self.assertEqual((p["hl"], p["hl2"], p["big"]), (YELLOW, WHITE, "hl"), "강조 노랑 · 바탕 흰색 · 큰 줄은 강조색으로")
        self.assertNotIn("stack", p)
        self.assertLess(p["bgBright"], 1)
        self.assertGreater(p["textScale"], 1)
        self.assertEqual(p["textH"], self.dark["textH"], "글자 높이 가산점(styleBonus)용 버릇 그대로")
        self.assertEqual(p["posW"]["bottom"], 1.0)
        self.assertEqual(p["sw"], ts.SW_OF_TIER[0], "그림자만 있는 한 장 → 테두리 없음 (어두운 배경 두 장은 셈에서 빠짐)")
        self.assertNotIn("face", p, "얼굴이 나온 썸네일이 드물면 얼굴 크기를 쓰지 않음")
        self.assertEqual(p["lines"], 1)

    def test_params_bright(self):
        p = ts.params(self.bright)
        self.assertEqual((p["hl"], p["hl2"], p["stack"]), (YELLOW, WHITE, "hl2"), "강조 노랑 · 바탕 흰색 · 같은 크기 두 줄은 위 줄이 바탕색")
        self.assertNotIn("big", p)
        self.assertGreater(p["bgBright"], 1.1)
        self.assertLess(p["textScale"], 1)
        self.assertEqual(p["sw"], ts.SW_OF_TIER[2])
        self.assertAlmostEqual(p["face"], 0.417, places=2)
        self.assertLessEqual(p["lines"], 2)
        json.dumps(p)  # numpy 값이 섞이지 않음 (스타일 파일·화면으로 보냄)

    def test_thick_outline_needs_two(self):
        h = dict(self.bright, outlineN=1)
        self.assertEqual(ts.params(h)["sw"], ts.SW_OF_TIER[1], "두꺼운 테두리를 한 장에서만 쟀으면 얇게 (프리텐다드 블랙 얇은 획 상한 유지)")
        self.assertIn("얇은 테두리", ts.describe(h))

    def test_broken_habits(self):
        """메모장으로 고쳐 숫자가 깨진 버릇: 계산이 멈추지 않음 (스타일 목록이 500 이 되지 않게)."""
        bad = dict(self.bright, textH="x", bright="밝음", sat=None, outline=[1], face="큼", faceShare="x", lines="둘", cutout={}, posShare={"top": "x"}, n="많이")
        p = ts.params(bad)
        self.assertNotIn("textScale", p)
        self.assertNotIn("bgBright", p)
        self.assertEqual(p["posW"]["top"], 0.0)
        ts.describe(bad)
        self.assertIsNotNone(ts.card({"thumb": {"habits": bad}}))
        ts.card({"thumb": {"habits": {"colors": 5, "mode": [], "accent": 3}}})  # 깨져도 멈추지 않음

    def test_params_clamped(self):
        p = ts.params({"textH": 0.6, "bright": 1.0, "sat": 0.0, "colors": ["#111111"], "outline": 0.5})
        self.assertEqual(p["textScale"], 1.25)
        self.assertEqual(p["bgBright"], 1.22)
        self.assertEqual(p["bgSat"], 0.85)
        self.assertNotIn("hl", p, "검정은 글자 강조색으로 쓰지 않음")
        self.assertIsNone(ts.params(None))
        self.assertIsNone(ts.summarize([]))

    def test_describe(self):
        d = ts.describe(self.dark)
        for w in ("강조색 노랑", "아래쪽", "어두운 배경", "테두리 없음"):
            self.assertIn(w, d)
        b = ts.describe(self.bright)
        for w in ("강조색 노랑 · 바탕 글자 흰색(두 줄 색을 나눔)", "두꺼운 테두리", "얼굴 크게", "밝은 배경", "누끼 안 씀"):
            self.assertIn(w, b)


def jpg(name):
    return (FIX / name).read_bytes()


class Work(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="썸네일 스타일 "))
        work = self.tmp / "작업 폴더"
        self.styles = work / "styles"
        self.thumbs = work / "thumbnails"
        for d in (self.styles, self.thumbs):
            d.mkdir(parents=True)
        self.patches = [mock.patch.object(core, "WORK", work), mock.patch.object(style, "STYLES", self.styles), mock.patch.object(thumb, "THUMBS", self.thumbs),
                        mock.patch.object(ts, "ocr_lines", side_effect=self._lines), mock.patch.object(ts, "face_boxes", side_effect=self._faces)]
        for p in self.patches:
            p.start()
        self.calls = []
        self.current = None

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 글자·얼굴 모델 대신: 지금 재는 그림(_get 이 준 id)의 상자
    def _lines(self, im):
        return BOXES[IDS[self.current]]["lines"] if self.current else []

    def _faces(self, im):
        return BOXES[IDS[self.current]]["faces"] if self.current else []

    def fake_get(self, missing=("maxresdefault.jpg",)):
        def get(url, timeout=None):
            self.calls.append(url)
            vid, size = url.split("/")[-2:]
            if size in missing or vid not in IDS:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
            return jpg(IDS[vid])
        return get

    def make_style(self, name, ids, extra=None):
        base = json.loads((FIX.parent / "style_v17.json").read_text(encoding="utf-8"))  # 예전 판 스타일 파일 (구조 수치만)
        d = dict(base, source=[f"20240101_{i}_영상 {k}.mp4" for k, i in enumerate(ids)], duration=60.0, **(extra or {}))
        (self.styles / f"{name}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def learn(self, name, **kw):
        orig = ts.measure

        def m(p, lines=None, faces=None):
            self.current = Path(p).stem
            return orig(p, lines, faces)
        with mock.patch.object(ts, "measure", side_effect=m):
            return ts.learn(name, lambda *_: None, **kw)


class FetchTests(Work):
    def test_fetch_falls_back_and_caches(self):
        with mock.patch.object(ts, "_get", side_effect=self.fake_get()):
            p = ts.fetch("darkAAAAAA1")
            self.assertTrue(p and p.is_file())
            self.assertEqual([u.split("/")[-1] for u in self.calls], ["maxresdefault.jpg", "sddefault.jpg"], "maxres 가 없으면 sd")
            self.assertEqual(p.parent, self.styles / "_thumbs")
            ts.fetch("darkAAAAAA1")
            self.assertEqual(len(self.calls), 2, "받아 둔 썸네일은 다시 받지 않음")

    def test_fetch_rejects(self):
        self.assertIsNone(ts.fetch("../../etc/x"), "영상 id 꼴만")
        self.assertIsNone(ts.thumb_path("a/b"))
        with mock.patch.object(ts, "_get", return_value=b"x" * 100):
            self.assertIsNone(ts.fetch("tinyCCCCCC1"), "아주 작은 그림(썸네일 없음 회색)은 버림")

    def test_fetch_offline(self):
        with mock.patch.object(ts, "_get", side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(ts.Offline):
                ts.fetch("darkAAAAAA1")


class LearnTests(Work):
    def test_learn_saves_habits_and_card(self):
        self.make_style("쌈바 스타일", ["darkAAAAAA1", "darkAAAAAA2"])
        with mock.patch.object(ts, "_get", side_effect=self.fake_get()):
            r = self.learn("쌈바 스타일")
        self.assertIn("강조색 노랑", r["desc"])
        d = json.loads((self.styles / "쌈바 스타일.json").read_text(encoding="utf-8"))
        self.assertEqual(d["thumb"]["habits"]["n"], 2)
        self.assertEqual(d["duration"], 60.0, "스타일의 다른 값은 그대로")
        st = next(s for s in style.list_styles() if s["name"] == "쌈바 스타일")
        self.assertEqual(st["thumb"]["params"]["hl"], YELLOW)
        self.assertEqual(sorted(st["thumb"]["ids"]), ["darkAAAAAA1", "darkAAAAAA2"])
        # 다시 배우기: 잰 값을 그대로 씀 (받지도 다시 재지도 않음)
        n = len(self.calls)
        with mock.patch.object(ts, "_get", side_effect=AssertionError("받으면 안 됨")):
            r2 = self.learn("쌈바 스타일")
        self.assertEqual(len(self.calls), n)
        self.assertEqual(r2["thumb"]["habits"], r["thumb"]["habits"])

    def test_learn_more_from_channel(self):
        self.make_style("토크 스타일", ["brightBBBB1"])
        rows = [{"id": "brightBBBB2"}, {"id": "brightBBBB1"}, {"id": "nothing0001"}]
        with mock.patch.object(ts, "_get", side_effect=self.fake_get()), mock.patch.object(ts, "_channel_url", return_value="https://www.youtube.com/@talk"), \
                mock.patch.object(core, "list_videos", return_value=rows) as lv:
            r = self.learn("토크 스타일")
        lv.assert_called_once()
        self.assertEqual(r["thumb"]["habits"]["n"], 2, "채널 인기 영상 썸네일도 (같은 영상은 한 번)")
        self.assertEqual(r["thumb"]["missing"], 1, "썸네일이 없는 영상은 셈만")
        self.assertEqual((r["thumb"]["habits"]["base"], r["thumb"]["habits"]["mode"]), (WHITE, "stack"))

    def test_learn_errors(self):
        (self.styles / "보관함 스타일.json").write_text(json.dumps({"source": ["내 촬영본.mp4"]}), encoding="utf-8")
        with self.assertRaises(ts.ThumbStyleError):
            ts.learn("보관함 스타일", lambda *_: None)
        with self.assertRaises(style.StyleMissing):
            ts.learn("없는 스타일", lambda *_: None)
        self.make_style("끊긴 스타일", ["darkAAAAAA1", "darkAAAAAA2", "brightBBBB1"])
        get = mock.Mock(side_effect=urllib.error.URLError("offline"))
        with mock.patch.object(ts, "_get", get):
            with self.assertRaises(ts.ThumbStyleError):
                self.learn("끊긴 스타일")
        self.assertEqual(get.call_count, 1, "인터넷이 안 되면 첫 장에서 멈춤 (장마다 기다리지 않음)")

    def test_broken_style_file_keeps_list(self):
        """스타일 파일의 thumb.habits 를 메모장으로 고쳐 깨뜨려도 스타일 목록(/api/style/list)은 나옴 (D-130 검토)."""
        self.make_style("깨진 스타일", ["darkAAAAAA1"], {"thumb": {"v": ts.VER, "habits": {"textH": "x", "colors": "노랑", "bright": "x", "n": "x"}}})
        self.make_style("멀쩡한 스타일", ["darkAAAAAA1"])
        names = [s["name"] for s in style.list_styles()]
        self.assertIn("깨진 스타일", names)
        self.assertIn("멀쩡한 스타일", names)
        ts.editor_view()

    def test_learn_quiet_never_raises(self):
        logs = []
        out = ts.learn_quiet(["없는 스타일", ""], logs.append)
        self.assertEqual(out, {})
        self.assertTrue(any("썸네일 버릇은 배우지 못했어요" in x for x in logs))


class PickTests(Work):
    def test_pick_and_view(self):
        self.make_style("쌈바 스타일", ["darkAAAAAA1", "darkAAAAAA2"])
        self.make_style("토크 스타일", ["brightBBBB1", "brightBBBB2"])
        self.make_style("배운 적 없음", [])
        with mock.patch.object(ts, "_get", side_effect=self.fake_get()):
            self.learn("쌈바 스타일")
            with mock.patch.object(ts.time, "strftime", return_value="2099-01-01 00:00"):
                self.learn("토크 스타일")
        v = ts.editor_view()
        self.assertIsNone(v["pick"])
        self.assertIsNone(v["active"], "고른 적이 없으면 기본 — 배우기만 해서는 썸네일이 바뀌지 않음 (D-130 검토)")
        self.assertEqual([s["name"] for s in v["styles"]], ["토크 스타일", "쌈바 스타일"], "썸네일 버릇이 있는 스타일만")
        ts.set_pick("쌈바 스타일")
        self.assertEqual(ts.editor_view()["active"]["params"]["hl"], YELLOW)
        ts.set_pick("")
        self.assertIsNone(ts.editor_view()["active"], "'기본' 을 고르면 스타일 안 씀")
        with self.assertRaises(style.StyleMissing):
            ts.set_pick("없는 스타일")
        ts.set_pick("쌈바 스타일")
        (self.styles / "쌈바 스타일.json").unlink()
        self.assertIsNone(ts.editor_view()["active"], "고른 스타일을 지웠으면 기본 (다른 스타일로 넘어가지 않음)")


class ABTests(Work):
    META = [{"tpl": "위 제목 (쪼살형)", "pid": "q", "l1": "왜", "l2": "막힐까?", "colors": {"hl": "#ffffff", "hl2": "#FFE14D"}},
            {"tpl": "아래 제목 (쌈바형)", "pid": "num", "l1": "1분", "l2": "드리블", "colors": {"hl": "#FFE14D", "hl2": "#FFFFFF"}, "evil": "<script>"}]

    def test_record_and_win(self):
        sid = ts.record_ab("a.mp4", ["a_썸네일_A.jpg", "a_썸네일_B.jpg"], self.META)
        sets = ts.ab_sets("a.mp4")
        self.assertEqual(len(sets), 1)
        self.assertEqual([i["tag"] for i in sets[0]["items"]], ["A", "B"])
        self.assertEqual(sets[0]["items"][0]["colors"]["hl"], "#FFFFFF", "색은 대문자 #RRGGBB 만")
        self.assertNotIn("evil", sets[0]["items"][1])
        self.assertEqual(ts.ours()["n"], 0, "이긴 장을 적기 전에는 가산점 없음")
        ts.set_winner(sid, "B")
        o = ts.ours()
        self.assertEqual((o["n"], o["tpl"], o["pid"]), (1, {"아래 제목 (쌈바형)": 1}, {"num": 1}))
        self.assertEqual(o["params"], {"hl": YELLOW, "hl2": WHITE})
        self.assertIn("A/B 1번 이긴 것", o["desc"])
        sid2 = ts.record_ab("b.mp4", ["b_A.jpg", "b_B.jpg", "b_C.jpg"], self.META)
        ts.set_winner(sid2, "B")
        self.assertEqual(ts.ours()["tpl"]["아래 제목 (쌈바형)"], 2)
        self.assertEqual(ts.ours()["n"], 2, "메타가 없는 C 는 틀 없이")
        with self.assertRaises(ts.ThumbStyleError):
            ts.set_winner(sid, "F")
        with self.assertRaises(ts.ThumbStyleError):
            ts.set_winner("없는 묶음", "A")
        ts.set_winner(sid, "")
        self.assertEqual(ts.ours()["n"], 1, "같은 장을 다시 누르면(빈 값) 지움")

    def test_ours_in_view(self):
        sid = ts.record_ab("a.mp4", ["A.jpg", "B.jpg"], self.META)
        ts.set_pick(ts.OURS)
        self.assertIsNone(ts.editor_view()["active"], "이긴 것이 없으면 '우리 채널'은 쓸 수 없음")
        ts.set_winner(sid, "A")
        v = ts.editor_view()
        self.assertEqual(v["active"]["name"], ts.OURS)
        self.assertEqual(v["active"]["params"]["hl"], WHITE)
        self.assertEqual(v["ours"]["n"], 1)

    def test_ours_replays_winning_look(self):
        """기본 모양으로 이긴 장 → '우리 채널'은 기본과 같은 색·같은 역할 (색만 따로 모아 다른 그림이 되지 않게) · 역할을 적은 장은 그 역할 그대로."""
        default = {"tpl": "아래 제목 (쌈바형)", "colors": {"hl": "#FFE14D", "hl2": "#FFFFFF"}, "roles": {"big": "", "stack": ""}}
        sid = ts.record_ab("a.mp4", ["A.jpg", "B.jpg"], [default, dict(default, roles={"big": "hl2", "stack": "x"})])
        ts.set_winner(sid, "A")
        self.assertEqual(ts.ours()["params"], {"hl": YELLOW, "hl2": WHITE}, "기본 역할 (big·stack 없음)")
        ts.set_winner(sid, "B")
        self.assertEqual(ts.ours()["params"], {"hl": YELLOW, "hl2": WHITE, "big": "hl2"}, "이긴 장의 역할을 그대로 · 이상한 값은 버림")
        self.assertEqual(ts.ab_sets()[0]["items"][1]["roles"], {"big": "hl2"})


class BrandCustomTests(Work):
    def test_brand_custom(self):
        self.assertEqual(thumb.brand_custom(), [], "브랜드 키트를 저장한 적이 없으면 스타일 색")
        b = thumb.load_brand()
        thumb.save_brand(b)
        self.assertEqual(thumb.brand_custom(), [], "기본 색 그대로 저장(로고만 바꿈 등)은 직접 고른 색이 아님")
        thumb.save_brand(dict(b, colors=dict(b["colors"], hl="#FF5FA2")))
        self.assertEqual(thumb.brand_custom(), ["hl"])
        thumb.save_brand(dict(b, font="Jua"))
        self.assertEqual(thumb.brand_custom(), ["font"])


class AppTests(Work):
    def test_thumb_habits_after_learn(self):
        import app
        with mock.patch.object(ts, "learn_quiet", return_value={"쌈바": "강조색 노랑"}) as lq:
            r = app._thumb_habits({"name": "쌈바", "profile": {}})
            self.assertEqual(r["thumb"], "강조색 노랑")
            r2 = app._thumb_habits({"learned": [{"style": "쌈바", "names": []}, {"style": "x", "error": "실패"}]})
        self.assertEqual(r2["learned"][0]["thumb"], "강조색 노랑")
        self.assertNotIn("thumb", r2["learned"][1], "배우지 못한 스타일은 건너뜀")
        self.assertEqual(lq.call_count, 2)

    def test_ab_endpoint_records_items_only(self):
        """/api/thumb/ab: 장마다 틀·색을 적되 모바일 비교 한 장은 A/B 장이 아님."""
        import app
        files = ["v_썸네일_A.jpg", "v_썸네일_B.jpg", f"v_{thumb.AB_SHEET}.jpg"]
        with mock.patch.object(thumb, "export_ab", return_value=files), mock.patch.object(app.editor, "video_path", return_value=Path("v.mp4")):
            h = app.Handler.__new__(app.Handler)
            h.headers = {"Host": f"127.0.0.1:{app.PORT}", "Origin": f"http://127.0.0.1:{app.PORT}"}
            h.path = "/api/thumb/ab"
            sent = {}
            h._body = lambda: {"name": "v.mp4", "items": ["d1", "d2"], "metas": ABTests.META, "mobile": "m"}
            h._send = lambda code, obj, *a: sent.update(code=code, obj=obj)
            h.do_POST()
        self.assertEqual(sent["code"], 200)
        self.assertEqual([i["tag"] for i in ts.ab_sets("v.mp4")[0]["items"]], ["A", "B"])

    def test_phone_label(self):
        import remote
        self.assertEqual(remote._label(ts.JOB), ts.JOB, "휴대폰에는 작업 이름이 그대로 (시작은 PC 스타일 카드에서만)")

    def test_style_thumbs_job_message(self):
        import app
        self.make_style("보관함", [])
        r = app._style_thumbs("보관함")
        self.assertFalse(r["ok"])
        self.assertIn("학습용 영상으로 배운 스타일만", r["error"])


if __name__ == "__main__":
    unittest.main()
