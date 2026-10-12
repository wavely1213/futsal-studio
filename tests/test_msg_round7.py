"""MSG round7 묶음 (B1 · D-260~D-263) — 저장소 폴더에서 python3 -m unittest tests.test_msg_round7

- 쇼츠 = 포인트 하나(BR-092)를 MSG 쇼츠에도 (editor.recommend(points=) · msg.SHORTS_POINTS · _shorts_window) · 쇼츠 수 = 포인트 수 · 챌린지는 빼고(_shorts_points · D-261)
- 재미 순간마다 사건 하나 (plan_events) · 양 맞추기는 그 사건을 지키고(govern protect) 못 지키면 소리만 남김 (accent)
- 쉼으로 쪼개진 문장의 펀치라인 · 정리 카드는 2줄씩 (_recap_cards)
- 뺀 군말 소리가 컷 시작 뒤까지 이어지면 다음 말 앞에서 시작 (snap_edges) · 쇼츠도 구간 안 군말·NG 를 뺌 (D-265)
- 화면이 안 바뀌는 확대 사건은 목록에서 뺌 · 그 순간의 하나뿐인 사건이면 소리만 (govern · D-266)"""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import editor  # noqa: E402
import msg  # noqa: E402
import takes  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402
from test_e2b_rough import FIX, GreetingSectionReviewTest, WorkMixin  # noqa: E402
from test_msg_round4 import build  # noqa: E402


def M(kind, t, text="", a=None, b=None, score=1.0):
    return {"kind": kind, "a": t - 0.5 if a is None else a, "b": t + 0.5 if b is None else b, "t": t, "score": score, "text": text, "why": ""}


class PointsFlagTest(WorkMixin, unittest.TestCase):
    """editor.recommend(segs=…, points=True) = 기본 가편집과 같은 BR-092 규칙 · points 없이는 예전(v2.16.0) 그대로."""
    LINES = GreetingSectionReviewTest.LINES

    def segs(self):
        return [{"start": 0.5, "end": 4.0, "text": "안녕하세요 인사이드 패스 꿀팁 핵심이에요"}] + \
            [{"start": 4.5 + 5 * k, "end": 8.0 + 5 * k, "text": t} for k, t in enumerate(self.LINES[1:5] + ["두 번째 포인트는 발목이에요", "이게 진짜 중요해요"])] + \
            [{"start": 40.0, "end": 42.0, "text": "감사합니다"}]

    def both(self, name, **kw):
        off = (mock.patch.object(editor, "GREETING", re.compile("$^")), mock.patch.object(editor, "SECTION", re.compile("$^")),
               mock.patch.object(takes, "CLOSING", re.compile("$^")))
        a = editor.recommend(name, **kw)
        for p in off:
            p.start()
        try:
            b = editor.recommend(name, **kw)
        finally:
            for p in off:
                p.stop()
        return [(s["start"], s["end"]) for s in a["shorts"]], [(s["start"], s["end"]) for s in b["shorts"]]

    def test_points_turns_br092_on_for_segs(self):
        segs = self.segs()
        name = self.put("v.mp4", segs)
        a, b = self.both(name, segs=json.loads(json.dumps(segs)))
        self.assertEqual(a, b)  # points 없이: 예전 그대로 (test_e2b_rough 와 같음)
        a, b = self.both(name, segs=json.loads(json.dumps(segs)), points=True)
        self.assertNotEqual(a, b)  # points: 인사·끝인사·장 나눔 규칙이 MSG 쪽에도 걸림
        r = editor.recommend(name, segs=json.loads(json.dumps(segs)), points=True)
        for s in r["shorts"]:
            self.assertGreater(s["start"], 0.5)  # 인사로 시작하지 않음
            self.assertLess(s["end"], 40.0)      # 끝인사로 끝나지 않음

    def test_short_count_is_point_count(self):
        """쇼츠 수 = 포인트를 연 말 수 (n 까지) — MSGRAW01 은 포인트 3개라 n=5 여도 쇼츠 3개 · 포인트 말이 없는 영상은 n 그대로."""
        name = self.put("v.mp4", FIX["MSGRAW01"]["transcript"], FIX["MSGRAW01"]["analysis"])
        with mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
            r5 = editor.recommend(name, n=5)
            r2 = editor.recommend(name, n=2)
        self.assertEqual(len(r5["shorts"]), 3)
        self.assertEqual(len(r2["shorts"]), 2)
        segs = [{"start": 1.0 + 5 * k, "end": 4.5 + 5 * k, "text": t} for k, t in enumerate(self.LINES * 3)]
        name = self.put("w.mp4", segs)
        r = editor.recommend(name, n=3, min_len=10.0, max_len=25.0)
        with mock.patch.object(editor, "POINT_OPEN", re.compile("$^")):
            r0 = editor.recommend(name, n=3, min_len=10.0, max_len=25.0)
        self.assertEqual(r["shorts"], r0["shorts"])  # '첫/두 번째'가 없는 영상은 예전 그대로(n 까지)

    def test_autorun_makes_one_short_per_point(self):
        """끝까지 자동으로: 고른 쇼츠 수(5)보다 포인트가 적으면(MSGRAW01 = 포인트 3개) 쇼츠도 3개 · 진행 표시에 그 이유 (D-260)."""
        import autorun
        name = self.put("v.mp4", FIX["MSGRAW01"]["transcript"], FIX["MSGRAW01"]["analysis"])
        st = autorun.new_state(name, autorun.options({"shorts": 5}))
        c = autorun._Ctx(st, lambda m: None, None)
        with mock.patch.object(editor, "load_project", return_value={"sequences": []}), mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
            r = autorun._step_rough(c)
        self.assertEqual(len(st["points"]), 3)
        self.assertEqual(r, ("done", "쇼츠로 쓸 포인트가 3개라 쇼츠도 3개만 만들어요"))

    def test_single_marker_keeps_n(self):
        """'마지막으로' 하나뿐·'N번째' 하나뿐인 영상은 포인트로 세어 나가는 영상이 아님 → 예전처럼 n."""
        lines = self.LINES * 2 + ["마지막으로 하나만 더 말씀드리면 발목이에요"]
        segs = [{"start": 1.0 + 5 * k, "end": 4.5 + 5 * k, "text": t} for k, t in enumerate(lines)]
        name = self.put("x.mp4", segs)
        r = editor.recommend(name, n=3, min_len=10.0, max_len=25.0)
        with mock.patch.object(editor, "POINT_OPEN", re.compile("$^")):
            r0 = editor.recommend(name, n=3, min_len=10.0, max_len=25.0)
        self.assertEqual(r["shorts"], r0["shorts"])

    def test_point_open_is_section_without_recap(self):
        for t in ("자, 첫 번째 포인트.", "두 번째 포인트는 디딤발이에요", "마지막으로 하나만", "그리고 마지막으로 패스"):
            self.assertTrue(editor.POINT_OPEN.match(t), t)
        for t in ("오늘 배운 거 정리해 볼게요", "정리할게요", "그 다음 발목", "이번엔 왼발"):
            self.assertFalse(editor.POINT_OPEN.match(t), t)

    def test_msg_recommend_passes_flag(self):
        got = {}

        def fake(name, **kw):
            got.update(kw)
            return {"shorts": [], "tidy": [], "junk_list": []}
        with mock.patch.object(editor, "recommend", fake):
            msg._recommend("v.mp4", [{"start": 0.0, "end": 1.0, "text": "안녕"}])
            self.assertEqual(got["points"], msg.SHORTS_POINTS)
            msg._recommend("v.mp4", [{"start": 0.0, "end": 1.0, "text": "안녕"}], points=True)
            self.assertTrue(got["points"])

    def test_gate_decision_recorded(self):
        self.assertIs(msg.SHORTS_POINTS, True)  # D-261: round7 판정에서 레슨 쇼츠 맥락 점수가 올라 켬 (문서와 같아야 함)


class GatedPointsTest(unittest.TestCase):
    """_shorts_points: 스위치가 켜졌고 챌린지(점수판이 서는 영상)가 아닐 때만 MSG 쇼츠에 BR-092 (D-261)."""
    LESSON = [M("section", 20.0, "첫 번째 포인트는 고개 들기예요."), M("play", 30.0), M("success", 34.0, "나이스!"),
              M("section", 60.0, "두 번째 포인트는 디딤발이에요."), M("fail", 70.0, "아 아깝다.")]
    CHALLENGE = [M("section", 10.0, "첫 번째, 슛."), M("fail", 15.0, "아 아깝다!"), M("section", 20.0, "두 번째, 슛."),
                 M("success", 25.0, "들어갔어요!"), M("section", 30.0, "세 번째."), M("success", 34.0, "이것도 들어갔어요."),
                 M("section", 50.0, "네 번째, 슛."), M("fail", 55.0, "빗나갔어요.")]

    def test_lesson_uses_points(self):
        with mock.patch.object(msg, "SHORTS_POINTS", True):
            self.assertTrue(msg._shorts_points(self.LESSON))

    def test_challenge_keeps_v2160(self):  # 'N번째'가 시도라 시도마다 끊으면 마지막 시도·결과가 빠짐 (판정 round7 S3)
        self.assertTrue(msg._scoreboard(self.CHALLENGE))
        with mock.patch.object(msg, "SHORTS_POINTS", True):
            self.assertFalse(msg._shorts_points(self.CHALLENGE))

    def test_switch_off(self):
        with mock.patch.object(msg, "SHORTS_POINTS", False):
            self.assertFalse(msg._shorts_points(self.LESSON))


class ShortsWindowTest(unittest.TestCase):
    """_shorts_window(points=True): 포인트를 여는 말로 시작한 구간에 앞 포인트의 시범을 붙이지 않고, 정리 말·끝인사·다음 포인트 앞에서 끝냄."""
    rec = {"shorts": [{"start": 40.0, "end": 90.0, "cuts": [{"in": 40.0, "out": 62.0}, {"in": 63.0, "out": 90.0}]}], "tidy": []}
    moms = [M("section", 40.0, "두 번째 포인트는 디딤발이에요.", 40.0, 42.0), M("play", 50.0, score=3.0), M("recap", 70.0, "① 고개\n② 발", 68.0, 75.0),
            M("closing", 85.0, "오늘은 여기까지예요.", 85.0, 87.0)]
    demo = [[33.0, 39.0], [48.0, 53.0]]

    def test_off_keeps_v2160(self):
        cuts = msg._shorts_window(self.rec, self.moms, 120.0, self.demo, points=False)
        self.assertLess(cuts[0]["in"], 40.0)     # 앞 시범을 붙임 (예전)
        self.assertGreater(cuts[-1]["out"], 80)  # 정리 말·끝인사까지 (예전 · 끝 8~15초 규칙에 안 걸림)

    def test_on_one_point(self):
        cuts = msg._shorts_window(self.rec, self.moms, 120.0, self.demo, points=True)
        self.assertGreaterEqual(cuts[0]["in"], 40.0)  # 포인트 첫 말에서 시작 (앞 포인트 시범 없음)
        self.assertLessEqual(cuts[-1]["out"], 68.0)   # 정리 말 앞에서 끝
        self.assertTrue(any(c["in"] <= 50.0 <= c["out"] for c in cuts))  # 그 포인트 시범은 남음

    def test_soft_step_words_do_not_cut(self):
        moms = self.moms[:2] + [M("section", 60.0, "그 다음 발목을 고정하세요", 60.0, 61.0)]
        cuts = msg._shorts_window(self.rec, moms, 120.0, self.demo, points=True)
        self.assertGreater(cuts[-1]["out"], 85.0)  # '그 다음'은 장 나눔이 아님 (D-168)


class RecapCardTest(unittest.TestCase):
    def test_three_items_two_cards(self):
        m = M("recap", 183.3, "① 고개 들기\n② 디딤발 거리\n③ 다음 방향으로 받기", 180.3, 187.6)
        words = [(183.0, 183.5, "고개", 0), (183.6, 184.0, "들기,", 0), (184.3, 184.9, "디딤발", 0), (185.0, 185.4, "거리,", 0),
                 (185.6, 186.0, "그리고", 0), (186.1, 186.5, "다음", 0), (186.6, 187.0, "방향으로", 0), (187.1, 187.6, "받기.", 0)]
        cards = msg._recap_cards(m, words)
        self.assertEqual([c[0] for c in cards], ["① 고개 들기\n② 디딤발 거리", "③ 다음 방향으로 받기"])
        self.assertTrue(all(c[0].count("\n") + 1 <= msg.RECAP_LINES for c in cards))
        self.assertAlmostEqual(cards[1][1], 186.0, places=1)  # 셋째 항목을 말할 때 둘째 장
        self.assertLessEqual(cards[0][1] + cards[0][2], cards[1][1])  # 겹치지 않음
        self.assertGreaterEqual(cards[1][1] + cards[1][2], 187.6 + 1.0)  # 다 말한 뒤까지

    def test_two_items_one_card(self):
        m = M("recap", 10.0, "① 고개 들기\n② 디딤발 거리", 9.0, 12.0)
        self.assertEqual(len(msg._recap_cards(m, [])), 1)

    def test_four_items_without_words(self):
        m = M("recap", 10.0, "① 가\n② 나\n③ 다\n④ 라", 9.0, 14.0)
        cards = msg._recap_cards(m, [])
        self.assertEqual([c[0].count("\n") + 1 for c in cards], [2, 2])
        self.assertLess(cards[0][1], cards[1][1])

    def test_continuation_card_follows_first(self):
        """plan_events: 정리 카드 둘째 장은 첫 장 바로 뒤에도 (글자 간격 규칙에 안 걸림) · 첫 장을 못 고르면 둘째 장도 안 띄움."""
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        m = M("recap", 12.0, "① 고개 들기\n② 디딤발 거리\n③ 다음 방향으로 받기", 11.0, 16.0)
        words = [(14.5, 14.9, "다음", 0)]
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, [m], st, "보통", "long", 1, [{"in": 0.0, "out": 40.0}], words)
        rc = [c for c in picked if c.get("look") == "recap"]
        self.assertEqual(len(rc), 2, picked)
        self.assertEqual(rc[1]["text"], "③ 다음 방향으로 받기")


class PunchlineSplitTest(unittest.TestCase):
    def test_joke_across_pause(self):
        """쉼으로 쪼개진 문장('저도 처음엔' / '잘 못했어요.')도 농담 말 (round6 S2 펀치라인을 못 찾음)."""
        segs = [{"start": 61.4, "end": 64.0, "text": "아이고, 공이 조금 뒤로 갔네요.", "words": []},
                {"start": 64.6, "end": 65.1, "text": "괜찮아요.", "words": []},
                {"start": 65.5, "end": 66.1, "text": "저도 처음엔", "words": []},
                {"start": 66.9, "end": 67.5, "text": "잘 못했어요.", "words": []}]
        sig = {"name": "x.mp4", "duration": 80.0, "tidy": [{"in": 0.0, "out": 80.0}], "junk": [], "motion": [], "tags": {}, "faces": {}, "cuts": []}
        with mock.patch.object(msg, "lines_of", lambda s: s), mock.patch.object(msg, "_words", lambda s: []):
            moms = msg.moments(sig, segs)
        pl = [m for m in moms if m["kind"] == "punchline"]
        self.assertEqual(len(pl), 1, moms)
        self.assertAlmostEqual(pl[0]["b"], 67.5)
        self.assertIn("잘 못했어요", pl[0]["text"])


class SnapAfterJunkTest(unittest.TestCase):
    """판정 round7 S2: 받아쓰기가 '어,'를 69.05~69.62 로 앞당겨 적어 실제 소리(69.5~70.1)가 컷 시작(69.62) 뒤에 남음 (U7) → 다음 낱말 소리 앞에서 (D-265)."""
    words = [(66.88, 67.54, "못했어요.", 0), (69.05, 69.62, "어,", 1), (70.38, 70.74, "다시", 1), (70.74, 71.62, "해볼게요.", 1)]
    junk = [(69.05, 69.62, "추임새")]

    def test_filler_sound_past_cut_start(self):
        blobs = [[66.85, 67.69], [68.11, 70.02], [70.49, 70.84], [71.21, 71.79]]
        cuts = msg.snap_edges([{"in": 60.0, "out": 69.05}, {"in": 69.62, "out": 80.0}], blobs, self.words, self.junk)
        self.assertAlmostEqual(cuts[1]["in"], 70.49 - msg.SNAP_PRE, places=2)  # '어' 소리(70.02 까지)는 빠지고 '다시' 첫소리 앞 여유
        self.assertEqual(cuts[0]["in"], 60.0)

    def test_other_sound_between_is_kept(self):
        """사이에 다른 소리 덩어리(공 소리·시범)가 있으면 그대로 — 시범 소리를 자르지 않음."""
        blobs = [[66.85, 67.69], [68.11, 70.02], [70.1, 70.3], [70.49, 70.84], [71.21, 71.79]]
        cuts = msg.snap_edges([{"in": 69.62, "out": 80.0}], blobs, self.words, self.junk)
        self.assertEqual(cuts[0]["in"], 69.62)

    def test_not_after_junk_unchanged(self):
        blobs = [[66.85, 67.69], [68.11, 70.02], [70.49, 70.84], [71.21, 71.79]]
        cuts = msg.snap_edges([{"in": 69.62, "out": 80.0}], blobs, self.words, [])
        self.assertEqual(cuts[0]["in"], 69.62)  # 군말을 뺀 자리가 아니면 예전 그대로

    def test_sound_ends_at_cut_unchanged(self):
        """받아쓰기 시각이 맞아 군말 소리가 컷 시작 전에 끝나면 그대로."""
        blobs = [[66.85, 67.69], [69.0, 69.6], [70.49, 70.84], [71.21, 71.79]]
        cuts = msg.snap_edges([{"in": 69.62, "out": 80.0}], blobs, self.words, self.junk)
        self.assertEqual(cuts[0]["in"], 69.62)


class PhantomZoomTest(unittest.TestCase):
    """판정 round7 S1: 'punch 효과가 0.7초 간격으로 두 번' — space 가 나눠서 바꾼 크기를 앞 조각 크기로 되돌려 화면은 한 번만 바뀌는데
    MSG 목록에는 확대 사건이 둘 → 화면이 안 바뀌는 확대 사건은 빼고, 그 순간의 하나뿐인 사건이면 소리만 남김 (D-266)."""

    def run_govern(self, moms=()):
        b = build(200.0, ((0.0, 40.0),))
        n1 = msg._split_main(b, b.main[0], 10.0, [0.5, 0.4])
        b.event("punch", 10.0, "", "오래 멈춰", src=10.0, refs={"items": [n1["id"]]})
        n2 = msg._split_main(b, n1, 10.7, [0.5, 0.4])
        b.event("punch", 10.7, "", "재미 순간 살짝 확대", src=10.7, refs={"items": [n2["id"]]})
        msg.DENSITY["_시험"] = (0.0, 30.0, 60.0)
        try:
            msg.govern(b, "_시험", [0.5, 0.4], b.pos, moms)
        finally:
            del msg.DENSITY["_시험"]
        return b

    def visible(self, b):
        zt = [t for t, k in msg.visual_events(b.items, b.titles, b.shapes, b.pos) if k in ("zoom", "cut")]
        by = {it["id"]: it for it in b.items}
        return [e for e in b.events if e["kind"] == "punch"
                and any(abs(by[i]["start"] - t) < 0.05 for i in e["refs"].get("items") or [] if i in by for t in zt)]

    def test_invisible_zoom_event_removed(self):
        b = self.run_govern()
        punches = [e for e in b.events if e["kind"] == "punch"]
        self.assertEqual(len(punches), len(self.visible(b)), [(e["t"], e["why"]) for e in punches])  # 남은 확대 사건은 모두 화면에서 보임
        self.assertLessEqual(len(punches), 1)

    def test_lone_moment_keeps_sound(self):
        b = self.run_govern([M("success", 11.8, "나이스!", 11.6, 12.2)])   # (10.7 의 확대만 이 순간을 보여 줌 · 10.0 은 1초 넘게 앞)
        acc = [e for e in b.events if e.get("accent")]
        punches = [e for e in b.events if e["kind"] == "punch"]
        self.assertEqual(len(punches), len(self.visible(b)))
        self.assertEqual([e["t"] for e in punches], [10.0])   # 화면이 바뀌는 확대는 10.0 하나
        self.assertEqual([(e["kind"], e["src"]) for e in acc], [("sfx", 10.7)])   # 10.7 은 그 순간 효과음만
        self.assertTrue(b.sfx[acc[0]["refs"]["sfx"][0]])


class VitalMomentTest(unittest.TestCase):
    """시험 원본으로: 보통·듬뿍은 재미 순간(시범·펀치라인·강조·성공)마다 MSG 사건(원본 시각 앞뒤 1초)이 있고 양 범위(DENSITY)를 지킴 ·
    양을 아주 좁히면(빼야 할 때) 그 순간의 하나뿐인 사건은 소리만 남음(accent) · 담백은 그대로 (순간마다 더하지 않음)."""

    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def key_moments(self):
        sig = msg.signals(self.w.name, lambda m: None)
        segs = msg.respread(editor._segments_of(self.w.name), sig.get("blobs"))
        return [m for m in msg.moments(sig, segs) if m["kind"] in msg.MOMENT_KINDS]

    def covered(self, q, m):
        return any(e.get("src") is not None and e["kind"] not in ("bgm", "chapters") and float(m["a"]) - 1.0 <= float(e["src"]) <= float(m["b"]) + 1.0
                   for e in q["msg"]["events"])

    def make(self, name, lv, vital=True):
        knobs = msg._knobs
        with mock.patch.object(msg, "_knobs", lambda i: dict(knobs(i), **({} if vital else {"vital": False, "protect": False}))):
            return msg.build_variants(self.w.name, [{"kind": "preset", "name": name}], lv, ("long",), lambda m: None)["sequences"][0]

    def test_shorts_points_follow_gate(self):
        """쇼츠를 만들 때 _compile_once 가 _shorts_points(레슨 = 켬 · 챌린지 = 끔) 값을 _recommend 에 넘김."""
        orig, seen = msg._recommend, []

        def spy(*a, **kw):
            seen.append(kw.get("points"))
            return orig(*a, **kw)
        for want in (True, False):  # (_shorts_points 자체는 GatedPointsTest)
            seen.clear()
            with mock.patch.object(msg, "_recommend", spy), mock.patch.object(msg, "_shorts_points", lambda moms, v=want: v):
                q = msg.build_variants(self.w.name, [{"kind": "preset", "name": "담백 레슨형"}], "보통", ("shorts",), lambda m: None)["sequences"][0]
            self.assertEqual(q["format"], "shorts")
            self.assertIn(want, seen)
            self.assertNotIn(not want, seen)

    def test_shorts_drop_junk_inside_window(self):
        """쇼츠 구간(시범 장면으로 살린 곳 포함)에 걸친 군말·NG 도 롱폼처럼 뺌 (판정 round7 S2 쇼츠에 '어,' 가 남음 · U7 · D-265)."""
        sig = msg.signals(self.w.name, lambda m: None)
        segs = msg.respread(editor._segments_of(self.w.name), sig.get("blobs"))
        junk = [(float(j["a"]), float(j["b"])) for j in msg._recommend(self.w.name, segs, None, sig).get("junk_list") or [] if j["b"] - j["a"] >= 0.3]
        self.assertTrue(junk)
        a, b = junk[0]
        win = [{"in": max(0.0, a - 12.0), "out": b + 12.0}]
        with mock.patch.object(msg, "_shorts_window", lambda *x, **k: [dict(c) for c in win]):
            q = msg.build_variants(self.w.name, [{"kind": "preset", "name": "담백 레슨형"}], "보통", ("shorts",), lambda m: None)["sequences"][0]
        main = [(float(i["in"]), float(i["out"])) for i in q["items"] if i["track"] == "V1" and i["media"] == "main" and abs(float(i.get("speed") or 1) - 1) < 1e-3]
        self.assertTrue(main)
        self.assertFalse([(x, y) for x, y in main if min(y, b - 0.1) - max(x, a + 0.1) > 0.05], (a, b, main))

    def test_more_key_moments_get_an_event(self):
        """사건이 붙은 재미 순간 수: 순간마다 사건 하나 + 지키기(D-262) ≥ 예전(끄고 만든 것) · 적어도 한 스타일은 더 많음 · 양 범위는 그대로 지킴."""
        moms = self.key_moments()
        self.assertTrue(moms)
        gain = 0
        for name in msg.PRESETS:
            for lv in ("보통", "듬뿍"):
                q, q0 = self.make(name, lv), self.make(name, lv, vital=False)
                n, n0 = (sum(1 for m in moms if self.covered(x, m)) for x in (q, q0))
                self.assertGreaterEqual(n, n0, (name, lv))
                gain += n - n0
                tot = editor.seq_total(q)
                n_ev = len(msg.visual_events(q["items"], q["titles"], q["shapes"], tot))
                n_ev0 = len(msg.visual_events(q0["items"], q0["titles"], q0["shapes"], editor.seq_total(q0)))
                self.assertLessEqual(n_ev, max(n_ev0, msg.DENSITY[lv][1] * max(0.25, tot / 60.0)) + 0.5, (name, lv))
        self.assertGreater(gain, 0)

    def test_tight_amount_keeps_moments_as_sound(self):
        dens = dict(msg.DENSITY, 보통=(3.0, 6.0, 9.85))
        with mock.patch.object(msg, "DENSITY", dens), mock.patch.object(msg, "AUDIO_DENSITY", dict(msg.AUDIO_DENSITY, 보통=(0.0, 60.0))):
            res = msg.build_variants(self.w.name, [{"kind": "preset", "name": "예능 MSG형"}], "보통", ("long",), lambda m: None)
        q = res["sequences"][0]
        acc = [e for e in q["msg"]["events"] if e.get("accent")]
        self.assertTrue(acc, [e["kind"] for e in q["msg"]["events"]])
        ids = {it["id"] for it in q["items"]}
        for e in acc:
            self.assertEqual(e["kind"], "sfx")
            self.assertFalse(e["refs"].get("titles"))
            self.assertTrue(e["refs"].get("items") and all(i in ids for i in e["refs"]["items"]))  # 그 소리 클립이 편집본에 있음

    def test_mild_does_not_add_per_moment_events(self):
        st = msg.resolve({"kind": "preset", "name": "담백 레슨형"})
        moms = [M("emphasis", 5.0 + 4 * k, "핵심!") for k in range(6)]
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, moms, st, "담백", "long", 1, [{"in": 0.0, "out": 40.0}], [])
        self.assertFalse(any(c.get("vital") for c in picked))

    def test_vital_respects_joke_budget(self):
        """예산을 다 쓴 개그 글자(속마음·효과 글자)는 순간마다 더하지 않고 확대로 (스타일 섞기의 글자 양은 그대로)."""
        st = msg.resolve({"kind": "preset", "name": "담백 레슨형"})
        moms = [M("punchline", 15.0 + 25 * k, "제가 원래 잘 넘어지거든요.", 14.0 + 25 * k, 16.0 + 25 * k) for k in range(3)]
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, moms, st, "보통", "long", 1, [{"in": 0.0, "out": 80.0}], [])
        vit = [c for c in picked if c.get("vital")]
        self.assertTrue(all(c["kind"] not in ("inner", "fx") for c in vit), vit)


if __name__ == "__main__":
    unittest.main()
