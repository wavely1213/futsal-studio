"""MSG round7 묶음 (B1 · D-260~D-263) — 저장소 폴더에서 python3 -m unittest tests.test_msg_round7

- 쇼츠 = 포인트 하나(BR-092)를 MSG 쇼츠에도 (editor.recommend(points=) · msg.SHORTS_POINTS · _shorts_window) · 쇼츠 수 = 포인트 수 · 챌린지는 빼고(_shorts_points · D-261)
- 재미 순간마다 사건 하나 (plan_events) · 양 맞추기는 그 사건을 지키고(govern protect) 못 지키면 소리만 남김 (accent)
- 쉼으로 쪼개진 문장의 펀치라인 · 정리 카드는 2줄씩 (_recap_cards)
- 뺀 군말 소리가 컷 시작 뒤까지 이어지면 다음 말 앞에서 시작 (snap_edges) · 쇼츠도 구간 안 군말·NG 를 뺌 (D-265)
- 화면이 안 바뀌는 확대 사건은 목록에서 뺌 · 그 순간의 하나뿐인 사건이면 소리만 (govern · D-266)
- 검토 고침: 쇼츠마다 다른 포인트 하나·이음말 뒤 순서 말·있는 그대로의 쇼츠 수 안내·15초 안 되면 끊지 않음(D-267) · 공 소리를 군말로
  자르지 않음·앞 조각 끝·쇼츠 조각(D-268) · 재미 순간 지키기는 듬뿍만·소리만은 말 틈에만·확대 간격·정리 카드 한 덩어리·펀치라인·'한 번 더!'(D-269)"""
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
        self.assertEqual(r, ("done", "포인트가 3개라 쇼츠도 3개만 만들어요"))

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
        try:   # (소리만 남기기는 VITAL_LEVELS(듬뿍)에서만 · D-269 — 시험 양도 그 묶음에 넣음)
            with mock.patch.object(msg, "VITAL_LEVELS", ("_시험",)):
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
        """사건이 붙은 재미 순간 수: 순간마다 사건 하나 + 지키기(D-262 · 듬뿍만 · D-269) ≥ 예전(끄고 만든 것) · 적어도 한 스타일은 더 많음 ·
        양 범위는 그대로 지킴 (보통은 켜고 끈 것이 같음)."""
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
        """듬뿍 양을 아주 좁히면 그 순간의 하나뿐인 사건은 소리만 (D-262 · 듬뿍만 · D-269)."""
        dens = dict(msg.DENSITY, 듬뿍=(3.0, 6.0, 9.85))
        with mock.patch.object(msg, "DENSITY", dens), mock.patch.object(msg, "AUDIO_DENSITY", dict(msg.AUDIO_DENSITY, 듬뿍=(0.0, 60.0))):
            res = msg.build_variants(self.w.name, [{"kind": "preset", "name": "예능 MSG형"}], "듬뿍", ("long",), lambda m: None)
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
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, moms, st, "듬뿍", "long", 1, [{"in": 0.0, "out": 80.0}], [])
        vit = [c for c in picked if c.get("vital")]
        self.assertTrue(all(c["kind"] not in ("inner", "fx") for c in vit), vit)



# ---------------------------------------------------------------- 검토 고침 (D-267 ~ D-269)

def _lesson_two_points():
    """검토 coverage: 포인트 1(강조 말이 많음 · 73초) + 포인트 2(평범한 말 · 39초)."""
    segs = [{"start": 0.5, "end": 4.0, "text": "안녕하세요. 오늘은 패스 두 가지를 알려 드릴게요."},
            {"start": 5.0, "end": 8.5, "text": "자, 첫 번째 포인트는 디딤발이에요."}]
    t = 9.0
    for x in ["디딤발이 진짜 핵심이에요.", "이게 제일 중요한 팁이에요.", "디딤발 위치가 비결이에요.", "공 옆에 딱 두세요 이게 핵심.",
              "무릎을 살짝 굽히는 게 중요해요.", "이 방법이 진짜 꿀팁이에요.", "프로들도 이렇게 해요 핵심이에요.", "발목을 고정하는 게 중요해요.",
              "디딤발이 멀면 실수가 나요.", "이 차이가 진짜 중요해요.", "어떻게 하는지 다시 볼게요 핵심!", "이게 국가대표 비결이에요.",
              "디딤발 하나로 달라져요 중요해요.", "이게 오늘의 핵심 팁이에요."]:
        segs.append({"start": t, "end": t + 4.0, "text": x})
        t += 5.0
    segs.append({"start": t, "end": t + 3.5, "text": "두 번째 포인트는 시선이에요."})
    p2 = t
    t += 5.0
    for x in ["공을 받기 전에 앞을 봐요.", "주변을 먼저 살펴요.", "그 다음 패스를 줘요.", "고개를 들고 받아요.", "시선은 앞에 둬요.",
              "공만 보면 안 돼요.", "앞을 보고 패스해요."]:
        segs.append({"start": t, "end": t + 4.0, "text": x})
        t += 5.0
    segs.append({"start": t, "end": t + 3.0, "text": "오늘은 여기까지예요. 감사합니다."})
    return segs, p2


class PointCoverageTest(WorkMixin, unittest.TestCase):
    """쇼츠 수를 포인트 수로 줄이면 쇼츠마다 다른 포인트 하나 (D-267 · 예전: 점수로만 골라 강조 말이 많은 포인트에서 둘 · 포인트 2 는 빠짐)."""

    def test_each_point_gets_a_short(self):
        segs, p2 = _lesson_two_points()
        name = self.put("v.mp4", segs)
        r = editor.recommend(name, n=3)
        sh = sorted((s["start"], s["end"]) for s in r["shorts"])
        self.assertEqual(len(sh), 2, sh)
        self.assertLessEqual(sh[0][1], p2)          # 첫 쇼츠는 포인트 1 안에서 끝남 (포인트 2 로 넘어가지 않음)
        self.assertGreaterEqual(sh[1][0], p2 - 0.5)  # 둘째 쇼츠는 포인트 2
        self.assertEqual(r["points"], 2)

    def test_one_short_takes_the_best_point(self):
        segs, p2 = _lesson_two_points()
        r = editor.recommend(self.put("v.mp4", segs), n=1)
        self.assertEqual(len(r["shorts"]), 1)
        self.assertLessEqual(r["shorts"][0]["end"], p2)

    def lead_in(self, word):
        segs = json.loads(json.dumps(FIX["MSGRAW01"]["transcript"]))
        s = next(s for s in segs if abs(s["start"] - 87.54) < 0.01)
        s["text"] = s["text"].replace("자,", word, 1)
        s["words"][0]["w"] = word
        return self.put("v.mp4", segs, FIX["MSGRAW01"]["analysis"])

    def test_lead_in_words_before_ordinal(self):
        """'그럼 두 번째 포인트는' · '이제' · '네,' · '좋아요,' 도 포인트를 여는 말 — 예전에는 '자'만 받아 포인트를 2개로 세어 쇼츠도 2개 ·
        디딤발 포인트(87~126초)가 빠지고 '포인트가 2개라'고 안내 (검토)."""
        for word in ("그럼", "이제", "네,", "좋아요,"):
            name = self.lead_in(word)
            with mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
                r = editor.recommend(name, n=5)
            sh = sorted((s["start"], s["end"]) for s in r["shorts"])
            self.assertEqual(len(sh), 3, (word, sh))
            self.assertTrue(any(87.0 <= a < 126.0 for a, b in sh), (word, sh))
            self.assertEqual(r["points"], 3, word)

    def test_point_opens(self):
        L = lambda *ts: [{"start": 5.0 * k, "end": 5.0 * k + 4, "text": t} for k, t in enumerate(ts)]  # noqa: E731
        segs = L("안녕하세요", "자, 첫 번째 포인트는 디딤발", "설명", "그럼 두 번째 포인트는 시선", "두 번째 포인트 다시 말하면 시선",
                 "설명", "네, 세 번째는 발목", "마지막으로 오늘 배운 거 정리해 볼게요")
        opens, top, gap = editor._point_opens(segs, set())
        self.assertEqual([k for _, k in opens], [1, 2, 3])   # 같은 번호 되풀이·정리 말은 안 셈
        self.assertEqual((top, gap), (3, False))
        segs = L("자, 첫 번째 포인트는 디딤발", "설명", "빼고 하시는 게 자 두 번째 포인트는 시선", "설명", "세 번째 포인트는 발목")
        opens, top, gap = editor._point_opens(segs, set())
        self.assertEqual(([k for _, k in opens], top, gap), ([1, 3], 3, True))  # 줄 가운데 '두 번째 포인트' → 셈이 빔 (점수로 고름)
        self.assertEqual(editor._point_opens(L("마지막으로 하나만", "설명"), set()), ([], None, False))
        self.assertTrue(editor.POINT_OPEN.match("좋아요 그럼 이제 두 번째"))
        self.assertTrue(editor.POINT_OPEN.match("네 번째 포인트"))
        self.assertTrue(msg._point_mark(M("section", 10.0, "그럼 두 번째 포인트는 시선이에요.")))

    def test_gap_falls_back_to_scores(self):
        """셈이 비면(첫·세 번째만 줄 첫머리) 포인트로 나누지 않고 예전처럼 점수로 · 쇼츠 수는 들은 가장 큰 번호까지 · 안내용 포인트 수는 없음."""
        segs, p2 = _lesson_two_points()
        segs[1]["text"] = "빼고 하시는 게 자 첫 번째 포인트는 디딤발이에요."
        r = editor.recommend(self.put("v.mp4", segs), n=3)
        self.assertIsNone(r.get("points"))


class ShortCountNoteTest(WorkMixin, unittest.TestCase):
    """끝까지 자동으로: 쇼츠를 고른 수보다 적게 만들 때 그 까닭을 있는 그대로 (D-267)."""

    def test_note(self):
        import autorun
        self.assertEqual(autorun._short_count_note(3, 3), "포인트가 3개라 쇼츠도 3개만 만들어요")
        self.assertEqual(autorun._short_count_note(2, 3), "포인트 3개 중 2개만 쇼츠로 만들 만큼 길어요 (나머지 포인트는 너무 짧아요)")
        self.assertEqual(autorun._short_count_note(2, None), "쇼츠로 쓸 만한 구간이 2개라 쇼츠도 2개만 만들어요")

    def test_short_last_point(self):
        """MSGRAW02: 포인트 3개 · 마지막 포인트는 끝인사 전 5초뿐 → 쇼츠 2개 · '포인트가 2개라'가 아님 (검토)."""
        import autorun
        name = self.put("v.mp4", FIX["MSGRAW02"]["transcript"], FIX["MSGRAW02"]["analysis"])
        st = autorun.new_state(name, autorun.options({"shorts": 3}))
        c = autorun._Ctx(st, lambda m: None, None)
        with mock.patch.object(editor, "load_project", return_value={"sequences": []}), mock.patch.object(editor, "_sound_onsets", lambda n, w: []):
            r = autorun._step_rough(c)
        self.assertEqual(len(st["points"]), 2)
        self.assertEqual(r, ("done", "포인트 3개 중 2개만 쇼츠로 만들 만큼 길어요 (나머지 포인트는 너무 짧아요)"))


class ShortsTrimLengthTest(unittest.TestCase):
    """다음 포인트 앞에서 끊는 것은 남는 컷이 MIN_SHORTS 초 넘을 때만 (원본 시간이 아니라) · 끊어서 15초가 안 되면 끊지 않고 다시 (D-267)."""
    rec = {"shorts": [{"start": 100.0, "end": 160.0, "cuts": [{"in": 100.0, "out": 105.0}, {"in": 110.0, "out": 113.0}, {"in": 118.0, "out": 160.0}]}],
           "tidy": []}

    def moms(self, at):
        return [M("play", 104.0, score=2.0), M("section", at, "자, 네 번째 포인트는 몸 방향이에요.", at, at + 2.0)]

    def test_kept_length_decides(self):
        cuts = msg._shorts_window(self.rec, self.moms(120.0), 200.0, [], points=True)  # 원본 19.8초 · 남는 컷 9.8초 → 안 끊음 (예전: 끊어 9.8초)
        self.assertEqual(cuts[-1]["out"], 160.0)
        cuts = msg._shorts_window(self.rec, self.moms(130.0), 200.0, [], points=True)  # 남는 컷 19.8초 → 그 앞에서 끊음
        self.assertAlmostEqual(cuts[-1]["out"], 129.8)
        cuts = msg._shorts_window(self.rec, self.moms(130.0), 200.0, [], points=True, stop=False)
        self.assertEqual(cuts[-1]["out"], 160.0)

    def test_compile_seq_retries_without_stop(self):
        calls = []

        def fake(*a):
            knobs = a[12]
            calls.append(knobs.get("pointStop", True))
            dur = 14.0 if knobs.get("pointStop", True) else 40.0
            q = {"items": [{"track": "V1", "start": 0.0, "in": 0.0, "out": dur, "speed": 1.0}], "titles": [], "shapes": [],
                 "msg": {"events": [], "summary": {"kinds": {}}}}
            return q, [], 0, 0
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        with mock.patch.object(msg, "_compile_once", fake):
            q, _ = msg.compile_seq("v.mp4", {"duration": 300.0}, {}, [], [M("play", 10.0)], st, "보통", "shorts", 1, "x", lambda m: None)
        self.assertEqual(calls, [True, False])
        self.assertEqual(editor.seq_total(q), 40.0)
        calls.clear()
        with mock.patch.object(msg, "_compile_once", fake), mock.patch.object(msg, "_shorts_points", lambda moms: False):
            msg.compile_seq("v.mp4", {"duration": 300.0}, {}, [], [M("play", 10.0)], st, "보통", "shorts", 1, "x", lambda m: None)
        self.assertEqual(calls, [True])   # 포인트 규칙을 안 쓰면(챌린지) 다시 만들지 않음


class SnapKickTest(unittest.TestCase):
    """D-265 의 '군말 소리를 마저 뺌'이 군말에 붙은 공 소리·시범을 함께 잘라 내지 않게 · 앞 조각 끝은 진짜 군말 소리 앞 틈까지 (D-268)."""

    def test_long_blob_tail_kept(self):
        """'어,'(9.0~9.6) 바로 뒤 공 차는 소리가 같은 소리 덩어리(9.0~10.6) — 덩어리가 컷 시작 뒤로 1초 → 그대로 (예전: 10.78 로 옮겨 공 소리가 빠짐)."""
        cuts = msg.snap_edges([{"in": 9.6, "out": 15.0}], [[9.0, 10.6], [10.9, 11.7]], [(9.0, 9.6, "어,", 0), (10.95, 11.5, "이렇게요.", 0)],
                              [(9.0, 9.6, "추임새")])
        self.assertEqual(cuts[0]["in"], 9.6)

    def test_hit_or_demo_inside_kept(self):
        blobs = [[9.25, 9.88], [10.15, 10.9]]
        words = [(8.9, 9.65, "어,", 0), (10.2, 10.9, "이렇게요.", 0)]
        junk = [(8.9, 9.65, "추임새")]
        self.assertAlmostEqual(msg.snap_edges([{"in": 9.65, "out": 15.0}], blobs, words, junk)[0]["in"], 10.15 - msg.SNAP_PRE, places=2)
        self.assertEqual(msg.snap_edges([{"in": 9.65, "out": 15.0}], blobs, words, junk, hits=[9.8])[0]["in"], 9.65)       # 공 소리
        self.assertEqual(msg.snap_edges([{"in": 9.65, "out": 15.0}], blobs, words, junk, demos=[[9.7, 10.0]])[0]["in"], 9.65)  # 시범

    def test_previous_piece_ends_before_real_filler(self):
        """판정 검토 S2: 웃음(68.1~69.33)이 받아쓰기의 '어,' 시작(69.05)에서 잘림 · 진짜 '어'는 69.5 → 앞 조각 끝은 69.47."""
        words = [(66.88, 67.54, "못했어요.", 0), (69.05, 69.62, "어,", 1), (70.38, 70.74, "다시", 1), (70.74, 71.62, "해볼게요.", 1)]
        blobs = [[66.85, 67.69], [68.10, 69.33], [69.50, 70.02], [70.49, 70.84], [71.21, 71.79]]
        cuts = msg.snap_edges([{"in": 66.73, "out": 67.74}, {"in": 68.12, "out": 69.05}, {"in": 69.62, "out": 80.0}], blobs, words,
                              [(69.05, 69.62, "추임새")])
        self.assertAlmostEqual(cuts[1]["out"], 69.47, places=2)
        self.assertAlmostEqual(cuts[2]["in"], 70.49 - msg.SNAP_PRE, places=2)
        cuts = msg.snap_edges([{"in": 68.12, "out": 69.05}, {"in": 69.62, "out": 80.0}], blobs, words, [(69.05, 69.62, "추임새")], hits=[69.2])
        self.assertEqual(cuts[0]["out"], 69.05)   # 사이에 공 소리가 있으면 앞 조각은 그대로
        merged = [[66.85, 67.69], [68.11, 70.02], [70.49, 70.84], [71.21, 71.79]]   # 실제 S2: 웃음과 '어'가 한 소리 덩어리
        cuts = msg.snap_edges([{"in": 68.12, "out": 69.05}, {"in": 69.62, "out": 80.0}], merged, words, [(69.05, 69.62, "추임새")])
        self.assertAlmostEqual(cuts[0]["out"], 69.42, places=2)   # 군말을 덩어리 끝까지 민 자리(69.45) 바로 앞 — 웃음 끝 조용한 틈(69.40~69.46)
        self.assertAlmostEqual(cuts[1]["in"], 70.49 - msg.SNAP_PRE, places=2)

    def test_blob_with_kick_is_not_filler(self):
        """S1 172.3초: 받아쓰기가 '그렇죠.'를 뒤로 옮겨 낱말 없는 공 소리 덩어리가 '추임새'로 잡힘 → 탁 하고 끝나는 소리(hits)가 있으면 아님."""
        blobs = [[170.0, 171.9], [172.32, 172.72], [173.5, 173.9]]
        words = [(170.0, 171.9, "좋아요", 0), (173.53, 173.83, "그렇죠.", 0)]
        self.assertTrue(msg.blob_fillers(blobs, words))
        self.assertFalse(msg.blob_fillers(blobs, words, [172.33]))
        got = {}

        def spy(blobs, words, onsets=()):
            got["on"] = list(onsets)
            return []
        with mock.patch.object(msg, "blob_fillers", spy), mock.patch.object(editor, "recommend", lambda *a, **k: {"shorts": [], "tidy": [], "junk_list": []}):
            msg._recommend("v.mp4", [{"start": 0.0, "end": 1.0, "text": "안녕"}], sig={"blobs": blobs, "onsets": [1.0], "hits": [172.33]})
        self.assertEqual(got["on"], [1.0, 172.33])

    def test_shorts_drop_slivers(self):
        """쇼츠도 앞뒤가 다 잘린 짧은 조각을 뺌 (점프 컷 두 번 사이 0.6초 '그렇죠.' · 롱폼과 같음)."""
        cuts = [{"in": 165.29, "out": 172.29}, {"in": 173.41, "out": 174.03}, {"in": 174.89, "out": 177.0}]
        self.assertEqual(len(msg.drop_slivers(cuts, [])), 2)
        import inspect
        src = inspect.getsource(msg._compile_once)
        self.assertIn("base = drop_slivers(base, moms, _cheers(sig)) or base", src.split('if fmt == "shorts":')[1].split("else:")[0])
        # 펀치라인 말 끝 1초 안에 시작하는 웃음 조각·웃음 소리 구간과 겹치는 조각은 남김 (S2 68.12~69.05 웃음 · 빼면 농담이 웃음 없이 끝남)
        cuts = [{"in": 66.73, "out": 67.74}, {"in": 68.12, "out": 69.05}, {"in": 70.37, "out": 79.54}]
        self.assertEqual(len(msg.drop_slivers(cuts, [M("punchline", 67.4, "저도 처음엔 잘 못했어요.", 65.5, 67.5)])), 3)
        self.assertEqual(len(msg.drop_slivers(cuts, [], [(68.0, 69.4)])), 3)
        self.assertEqual(len(msg.drop_slivers(cuts, [])), 2)   # (순간도 웃음도 없으면 가운데 조각은 뺌)


def _space_case():
    b = msg._Build("x.mp4", {"duration": 200.0, "width": 1920, "height": 1080}, "long")
    b.clip(0.0, 20.0, main=True)
    b.clip(30.0, 30.9, main=True)                     # 20.0 에 점프 컷
    fx = {"scale": {"v": 100.0, "k": [{"t": 30.9, "v": 106.0, "e": "lin"}, {"t": 31.3, "v": 106.0, "e": "lin"}]},
          "pos": {"v": [0.5, 0.5], "k": msg._shake_keys(30.9, 1.0, 7)}}
    y, _ = b.clip(30.9, 31.3, fx=fx, main=True)        # 컷 0.9초 뒤 흔들기
    b.clip(31.3, 60.0, main=True)
    b.event("shake", y["start"], "", "화면 흔들기", src=30.9, refs={"items": [y["id"]], "fx": "shake"})
    return b, [M("surprise", 30.9, "와!", 30.6, 31.6)]   # (그 순간의 하나뿐인 사건)


class GovernVisibleTest(unittest.TestCase):
    """재미 순간 지키기는 듬뿍만 · 확대 간격 규칙은 지킬 때도 먼저 · 소리만은 말이 쉬는 틈에만 · 장 넘김 '딸깍'으로 대신하지 않음 (D-269)."""

    def gov(self, b, moms, lv, words=(), protect=True):
        msg.DENSITY["_t"] = (0.0, 30.0, 60.0)
        try:
            with mock.patch.object(msg, "VITAL_LEVELS", ("_t",) if lv else ()):
                msg.govern(b, "_t", [0.5, 0.4], b.pos, moms, words, protect=protect)
        finally:
            del msg.DENSITY["_t"]

    def changes(self, b):
        return [round(t, 2) for t, k in msg.visual_events(b.items, b.titles, b.shapes, b.pos) if k in ("zoom", "cut")][:3]

    def test_zoom_gap_beats_protect(self):
        for lv in (True, False):
            b, moms = _space_case()
            self.gov(b, moms, lv)
            self.assertEqual(self.changes(b)[:1], [20.0], lv)
            self.assertFalse([t for t in self.changes(b) if 20.5 < t < 21.6], lv)   # 20.9 의 흔들기 화면은 빠짐 (컷 0.9초 뒤 · 예전 판 그대로)
            self.assertFalse([e for e in b.events if e["kind"] == "shake"])
            acc = [e for e in b.events if e.get("accent")]
            self.assertEqual(len(acc), 1 if lv else 0)   # 듬뿍은 그 순간 소리만 · 보통은 예전처럼 뺌

    def test_levels(self):
        self.assertEqual((msg._knobs("보통")["vital"], msg._knobs("보통")["protect"]), (False, False))
        self.assertEqual((msg._knobs("듬뿍")["vital"], msg._knobs("듬뿍")["protect"]), (True, True))
        self.assertEqual(msg.VITAL_LEVELS, ("듬뿍",))
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        moms = [M("emphasis", 5.0 + 3 * k, "핵심!") for k in range(10)]
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, moms, st, "보통", "long", 1, [{"in": 0.0, "out": 40.0}], [])
        self.assertFalse(any(c.get("vital") for c in picked))   # 보통은 순간마다 더하지 않음

    def phantom(self, moms, words=()):
        b = build(200.0, ((0.0, 40.0),))
        n1 = msg._split_main(b, b.main[0], 10.0, [0.5, 0.4])
        b.event("punch", 10.0, "", "오래 멈춰", src=10.0, refs={"items": [n1["id"]]})
        n2 = msg._split_main(b, n1, 10.7, [0.5, 0.4])
        b.event("punch", 10.7, "", "재미 순간 살짝 확대", src=10.7, refs={"items": [n2["id"]]})
        self.gov(b, moms, True, words)
        return b

    def test_accent_only_in_speech_gap(self):
        mom = M("success", 11.8, "나이스!", 11.6, 12.2)
        talk = [(9.5, 12.0, "좋아요", 0), (12.05, 13.5, "아주잘했어요", 0)]   # 순간 내내 말함 → 소리 자리 없음
        b = self.phantom([mom], talk)
        self.assertFalse([e for e in b.events if e.get("accent")])
        gap = [(9.5, 11.5, "좋아요", 0), (12.0, 13.5, "아주잘했어요", 0)]     # 11.5~12.0 쉼
        b = self.phantom([mom], gap)
        acc = [e for e in b.events if e.get("accent")]
        self.assertEqual(len(acc), 1)
        t = b.sfx[acc[0]["refs"]["sfx"][0]][0]
        self.assertAlmostEqual(t, b.src_to_tl(11.55), places=2)   # 말 위(10.7)가 아니라 쉬는 틈

    def test_no_click_fallback(self):
        """실수 뒤 위로하는 펀치라인처럼 맞는 소리가 없으면 소리만 남기지 않음 (예전: 장 넘김 '딸깍')."""
        b = self.phantom([M("fail", 5.0, "아이고"), M("punchline", 10.7, "괜찮아요 저도 처음엔", 10.2, 11.2)])
        self.assertFalse([e for e in b.events if e.get("accent")])
        self.assertNotIn("딸깍", [x[1] for x in b.sfx if x])


class LayoutSiblingsTest(unittest.TestCase):
    def run_layout(self, two):
        b = msg._Build("x.mp4", {"duration": 200.0, "width": 1920, "height": 1080}, "long")
        b.clip(0.0, 10.0, main=True)
        t1 = b.title("나이스!", 9.5, 1.0, dict(editor.TITLE_STYLE))
        b.sfx.append((9.5, "레벨업", None, "fx"))
        b.event("fx", 9.5, "나이스!", "성공 말", src=9.5, refs={"titles": [t1["id"]], "sfx": [0]})
        if two:
            t2 = b.title("(뿌듯)", 9.6, 1.4, dict(editor.TITLE_STYLE))
            b.sfx.append((9.6, "띠용", None, "inner"))
            b.event("inner", 9.6, "(뿌듯)", "성공 말", src=9.6, refs={"titles": [t2["id"]], "sfx": [1]})
        msg.layout_titles(b, [], dict(editor.TITLE_STYLE), 10.0, None, [M("success", 9.4, "나이스!", 9.2, 9.8)])
        return b

    def test_two_dropped_siblings_keep_one_sound(self):
        """한 순간의 글자 사건 둘이 함께 자리를 잃으면 둘 다 소리까지 빠졌음 → 효과음 차례가 높은 하나는 소리만 (D-269)."""
        for two in (False, True):
            b = self.run_layout(two)
            acc = [e for e in b.events if e.get("accent")]
            self.assertEqual(len(acc), 1, two)
            self.assertEqual(acc[0]["text"], "레벨업")
            self.assertEqual(len([x for x in b.sfx if x]), 1)

    def test_unit_titles_go_together(self):
        b = msg._Build("x.mp4", {"duration": 200.0, "width": 1920, "height": 1080}, "long")
        b.clip(0.0, 20.0, main=True)
        t1 = b.title("① 가\n② 나", 5.0, 2.0, dict(editor.TITLE_STYLE))
        t2 = b.title("③ 다", 9.7, 1.0, dict(editor.TITLE_STYLE))   # 끝 MIN_TEXT 안 → 자리 없음
        b.event("situ", 5.0, "① 가\n② 나\n③ 다", "정리", src=5.0, refs={"titles": [t1["id"], t2["id"]]})["unit"] = True
        msg.layout_titles(b, [], dict(editor.TITLE_STYLE), 10.0)
        self.assertEqual(b.titles, [])
        self.assertEqual(b.events, [])


class RecapUnitTest(unittest.TestCase):
    """정리 카드: 항목 순서대로 말한 때 · 한 글자 첫 낱말 · 한 덩어리 (D-269)."""
    W = [(183.4, 183.8, "디딤발", 0), (183.8, 184.3, "위치,", 0), (185.4, 185.9, "시선,", 0), (186.5, 187.0, "그리고", 0),
         (187.1, 187.5, "디딤발", 0), (187.5, 188.0, "방향.", 0)]

    def test_shared_head_waits_for_its_item(self):
        m = M("recap", 180.0, "① 디딤발 위치\n② 시선\n③ 디딤발 방향", 180.0, 188.0)
        cards = msg._recap_cards(m, self.W)
        self.assertAlmostEqual(cards[1][1], 187.0, places=1)              # '디딤발 방향'을 말할 때 (예전 185.1 · 2초 일찍)
        self.assertGreaterEqual(cards[0][1] + cards[0][2], 185.9)         # 첫 장은 '시선'까지 보임

    def test_one_syllable_first_word(self):
        m = M("recap", 183.3, "① 고개 들기\n② 디딤발 거리\n③ 발 안쪽으로 받기", 180.3, 187.6)
        words = [(183.0, 183.5, "고개", 0), (183.6, 184.0, "들기,", 0), (184.3, 184.9, "디딤발", 0), (185.0, 185.4, "거리,", 0),
                 (186.1, 186.3, "발", 0), (186.4, 187.0, "안쪽으로", 0), (187.1, 187.6, "받기.", 0)]
        self.assertAlmostEqual(msg._recap_cards(m, words)[1][1], 186.0, places=1)   # (예전 187.47 · 다 말할 때쯤)

    def test_partial_recap_is_dropped(self):
        """둘째 장('③ 다음 방향으로 받기')이 같은 때 말 자막과 같아 빠지면 첫 장('①②')도 띄우지 않음 (예전: 둘뿐인 정리 카드)."""
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        m = M("recap", 12.0, "① 고개 들기\n② 디딤발 거리\n③ 다음 방향으로 받기", 11.0, 16.0)
        words = [(14.5, 14.9, "다음", 0)]
        caps = [{"start": 14.3, "end": 16.0, "text": "다음 방향으로 받기."}]
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, [m], st, "보통", "long", 1, [{"in": 0.0, "out": 40.0}], words, caps=caps)
        self.assertFalse([c for c in picked if c.get("look") == "recap"])
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, [m], st, "보통", "long", 1, [{"in": 0.0, "out": 40.0}], words)
        self.assertEqual(len([c for c in picked if c.get("look") == "recap"]), 2)

    def test_join_into_one_event(self):
        b = build(200.0, ((0.0, 40.0),))
        ids = []
        for k, (txt, t) in enumerate((("① 가\n② 나", 12.3), ("③ 다", 14.4))):
            tt = b.title(txt, t, 2.0, dict(editor.TITLE_STYLE))
            b.sfx.append((t, "딸깍", None, "situ"))
            e = b.event("situ", t, txt, "정리", src=t, refs={"titles": [tt["id"]], "sfx": [len(b.sfx) - 1]})
            e["_recap"] = 7
            ids.append(tt["id"])
        picked = [{"look": "recap", "mid": 7}, {"look": "recap", "mid": 7}]
        msg._join_recap(b, picked)
        self.assertEqual(len(b.events), 1)
        self.assertEqual(b.events[0]["refs"]["titles"], ids)    # [빼기] 한 번에 정리 전체
        self.assertTrue(b.events[0]["unit"])
        self.assertEqual(len([x for x in b.sfx if x]), 1)
        b2 = build(200.0, ((0.0, 40.0),))
        tt = b2.title("① 가\n② 나", 12.3, 2.0, dict(editor.TITLE_STYLE))
        b2.event("situ", 12.3, "① 가\n② 나", "정리", src=12.3, refs={"titles": [tt["id"]]})["_recap"] = 7
        msg._join_recap(b2, picked)   # 둘째 장을 못 만듦 → 첫 장도 뺌
        self.assertEqual((b2.events, b2.titles), ([], []))


class PunchlineJoinTest(unittest.TestCase):
    def test_matching_line_is_not_joined(self):
        """'제가 원래 잘 넘어지거든요'(마침표 없음)는 그 줄만으로 농담 → 다음 포인트 말('자, 두 번째 포인트는…')과 잇지 않음 (D-269)."""
        segs = [{"start": 28.8, "end": 30.6, "text": "제가 원래 잘 넘어지거든요", "words": []},
                {"start": 31.4, "end": 33.6, "text": "자, 두 번째 포인트는 디딤발이에요.", "words": []}]
        sig = {"name": "x.mp4", "duration": 80.0, "tidy": [{"in": 0.0, "out": 80.0}], "junk": [], "motion": [], "tags": {}, "faces": {}, "cuts": []}
        with mock.patch.object(msg, "lines_of", lambda s: s), mock.patch.object(msg, "_words", lambda s: []):
            moms = msg.moments(sig, segs)
        pl = [m for m in moms if m["kind"] == "punchline"]
        self.assertEqual(len(pl), 1, pl)
        self.assertAlmostEqual(pl[0]["b"], 30.6)
        self.assertNotIn("두 번째", pl[0]["text"])


class DemoAgainTest(unittest.TestCase):
    def test_new_instruction_is_not_again(self):
        """앞 시범 뒤 새 설명을 6초 넘게 했으면 다음 시범에 '한 번 더!'를 붙이지 않음 (판정 round7 S2 · D-269)."""
        plays = [M("play", 70.0, a=69.5, b=72.0), M("play", 101.0, a=100.6, b=103.0)]
        long_talk = [(78.0 + k, 78.8 + k, "설명", 0) for k in range(20)]
        self.assertEqual(sorted(msg._demo_labels(plays, plays, long_talk).values(), key=str), sorted(["실전 시범", None], key=str))
        short_talk = [(80.0, 81.0, "한번더", 0), (81.0, 82.0, "갈게요", 0)]
        self.assertEqual(sorted(msg._demo_labels(plays, plays, short_talk).values()), ["실전 시범", "한 번 더!"])

    def test_vital_skips_demo_labels(self):
        """순간마다 사건 하나(듬뿍)는 시범 상황 자막을 예산 밖으로 더하지 않음 (엉뚱한 '한 번 더!'가 퍼짐 · 검토)."""
        st = json.loads(json.dumps(msg.resolve({"kind": "preset", "name": "담백 레슨형"})))
        for part in ("fun", "captions"):
            if isinstance(st[part].get("perMin"), dict):
                st[part]["perMin"]["situ"] = 0
        plays = [M("play", 10.0, a=9.0, b=12.0, score=3.0), M("play", 20.0, a=19.0, b=22.0, score=3.0)]
        picked, _ = msg.plan_events({"onsets": [], "cuts": []}, plays, st, "듬뿍", "long", 1, [{"in": 0.0, "out": 40.0}], [])
        self.assertFalse([c for c in picked if c["kind"] == "situ" and c.get("text") in msg.DEMO_TXT], picked)


class SkipNoteTest(unittest.TestCase):
    def test_build_variants_returns_skip_reason(self):
        """쇼츠가 15초 안 되면 만들지 않은 까닭을 결과 notes 로 (편집실 알림 · 초는 내림 — '15초뿐이에요 (15초 넘어야 해요)'가 되지 않게 · D-267)."""
        def fake(name, info, sig, segs, moms, st, lv, fmt, seed, label, log, caps, ai, win):
            dur = 60.0 if fmt == "long" else 14.6
            return {"id": fmt, "items": [{"track": "V1", "start": 0.0, "in": 0.0, "out": dur, "speed": 1.0}], "msg": {"summary": {"text": ""}}}, []
        with mock.patch.multiple(msg, compile_seq=fake, ensure_transcript=lambda *a: None, _check_input=lambda *a: None,
                                 signals=lambda *a, **k: {"blobs": [], "sig": "s"}, moments=lambda *a, **k: [], _check=lambda: None), \
                mock.patch.object(editor, "media_info", lambda n: {"duration": 120.0}), mock.patch.object(editor, "_segments_of", lambda n: []), \
                mock.patch.object(editor, "_captions_of", lambda *a: []):
            res = msg.build_variants("v.mp4", [{"kind": "preset", "name": "예능 MSG형"}], "보통", ("long", "shorts"), lambda m: None)
        self.assertEqual([q["id"] for q in res["sequences"]], ["long"])
        self.assertEqual(len(res["notes"]), 1)
        self.assertIn("14초뿐이에요 (15초 넘어야 해요)", res["notes"][0]["why"])


if __name__ == "__main__":
    unittest.main()
