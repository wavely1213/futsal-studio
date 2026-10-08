"""MSG round2 판정에서 나온 문제의 회귀 시험 — 저장소 폴더에서 python3 -m unittest tests.test_msg_round2
엔드 화면: 멈춘 사진·빈 상자 없음 (마무리 인사 위에 얹거나 움직이는 장면을 깔고) · 담백: 티저·다시 보기 없이 첫 장면 위 질문 + 작은 제목
받아쓰기 낱말 시각: 뭉개진 낱말·몰아 적은 묶음·잡음 덩어리까지 늘여 적은 낱말 · 소리가 이어지는 가짜 쉼은 안 자름 · 끊긴 말 조각 · 짧은 조각
글자: 앞 낱말에 기대는 말로 시작하는 강조 · 예고하는 '하나 둘 셋에' · 시범 예고는 시범이 이어질 때만 · 같은 개그 글자 되풀이 없음 ·
성공 뒤 웃음은 뿌듯 · 정지 화면 글자 · 얼굴을 피하는 글자 자리 · 스타일마다 다른 말 자막 · 챌린지 음악."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import editor  # noqa: E402
import msg  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402


def W(w, s, e, p=0.9):
    return {"w": w, "s": s, "e": e, "p": p}


def M(kind, t, text="", a=None, b=None, score=1.0, why=""):
    return {"kind": kind, "t": t, "a": t if a is None else a, "b": t + 1.0 if b is None else b, "score": score, "text": text, "why": why}


class TranscriptRepairTest(unittest.TestCase):
    def test_collapsed_word_takes_its_sound(self):
        """'그리고'를 0.02초로 적으면 그 소리를 낱말 없는 추임새로 보고 잘랐음 (판정: '그리고 다음'이 빠짐)."""
        blobs = [[183.59, 184.3], [184.74, 185.13], [185.63, 186.64]]
        segs = [{"start": 183.59, "end": 186.44, "text": "디딤발 거리, 그리고 다음 방향으로",
                 "words": [W("디딤발", 183.59, 183.97), W("거리,", 183.97, 184.4), W("그리고", 184.92, 184.94), W("다음", 185.63, 185.82),
                           W("방향으로", 185.82, 186.44)]}]
        out, n = msg.align_to_sound(segs, blobs)
        g = out[0]["words"][2]
        self.assertEqual((g["s"], g["e"]), (184.74, 185.13))
        self.assertGreaterEqual(n, 1)
        f = msg.blob_fillers(blobs, msg._words(out))
        self.assertFalse([x for x in f if x[0] < 185.0 < x[1]])  # 더는 추임새로 안 봄

    def test_crammed_run_is_spread_over_the_unwritten_sound(self):
        """'자리에 서 있으면 왜'를 0.2초에 몰아 적고 앞 소리(그 말)는 비워 둠 → 앞 낱말 뒤 소리로 펼침 (가짜 쉼·끊긴 말 오판 막기)."""
        blobs = [[6.87, 8.51], [8.91, 9.87]]
        segs = [{"start": 6.87, "end": 9.72, "text": "패스하고 그 자리에 서 있으면 왜 안 될까요?",
                 "words": [W("패스하고", 6.87, 7.38), W("그", 7.38, 7.64), W("자리에", 8.91, 8.96), W("서", 8.96, 9.01), W("있으면", 9.01, 9.06),
                           W("왜", 9.06, 9.11), W("안", 9.11, 9.24), W("될까요?", 9.24, 9.72)]}]
        out, _ = msg.align_to_sound(segs, blobs)
        ws = out[0]["words"]
        self.assertAlmostEqual(ws[2]["s"], 7.66, places=2)
        self.assertTrue(all(a["e"] <= b["s"] + 1e-6 for a, b in zip(ws, ws[1:])))
        self.assertTrue(all(w["e"] - w["s"] >= 0.1 for w in ws[2:6]))
        self.assertEqual(len(msg.lines_of(out)), 1)  # 한 문장 (가짜 틈으로 안 쪼개짐)

    def test_run_after_interjection(self):
        """'자… 첫 번째'의 '첫'은 추임새 꼬리로 펼치지 않음 · '자, (1.5초) 하나, 둘, 셋에'는 펼침."""
        segs = [{"start": 9.12, "end": 11.0, "text": "자, 첫 번째", "words": [W("자,", 9.12, 9.28), W("첫", 10.54, 10.59), W("번째", 10.59, 11.0)]}]
        out, _ = msg.align_to_sound(segs, [[9.17, 9.75], [10.54, 11.06]])
        self.assertEqual(out[0]["words"][1]["s"], 10.54)
        segs = [{"start": 111.9, "end": 115.82, "text": "자, 하나, 둘, 셋에 맞춰서 받아볼게요.",
                 "words": [W("자,", 111.9, 112.74), W("하나,", 114.37, 114.42), W("둘,", 114.42, 114.47), W("셋에", 114.47, 114.54),
                           W("맞춰서", 114.54, 115.04), W("받아볼게요.", 115.04, 115.82)]}]
        out, _ = msg.align_to_sound(segs, [[111.12, 114.23], [114.37, 115.97]])
        self.assertAlmostEqual(out[0]["words"][1]["s"], 112.76, places=2)
        self.assertGreater(out[0]["words"][3]["e"] - out[0]["words"][1]["s"], 1.0)

    def test_word_stretched_over_noise_and_filler_moves_to_its_sound(self):
        """잡음과 이어진 덩어리 안의 '어…'를 다음 낱말 '다시'에 붙여 적음 → '다시'는 제 소리에서 시작 (판정: '어…'가 남음)."""
        blobs = [[66.92, 67.53], [67.99, 70.15], [70.41, 71.5]]
        segs = [{"start": 66.92, "end": 71.22, "text": "못했어요. 다시 해볼게요.",
                 "words": [W("못했어요.", 66.92, 67.48), W("다시", 69.62, 70.58), W("해볼게요.", 70.58, 71.22)]}]
        out, _ = msg.align_to_sound(segs, blobs)
        self.assertEqual(out[0]["words"][1]["s"], 70.41)

    def test_long_word_in_continuous_speech_is_not_moved(self):
        blobs = [[10.0, 12.0], [12.2, 13.0]]
        segs = [{"start": 10.0, "end": 12.9, "text": "공을 멈추려고 하면",
                 "words": [W("공을", 10.0, 10.5), W("멈추려고", 10.55, 11.4), W("하면", 11.4, 12.9)]}]
        out, _ = msg.align_to_sound(segs, blobs)
        self.assertEqual([w["s"] for w in out[0]["words"]], [10.0, 10.55, 11.4])


class CutTest(unittest.TestCase):
    def test_gap_full_of_sound_is_not_cut(self):
        cuts = [{"in": 100.0, "out": 112.8}, {"in": 114.2, "out": 118.6}, {"in": 120.0, "out": 125.0}]
        out = msg.pace_pauses(cuts, [], 10.0, blobs=[[111.1, 114.23], [119.0, 119.4]])
        self.assertEqual([(c["in"], c["out"]) for c in out], [(100.0, 118.6), (120.0, 125.0)])  # 소리가 이어진 틈은 이어 붙임
        junk = [(113.0, 113.6, "추임새")]
        out = msg.pace_pauses(cuts, junk, 10.0, blobs=[[111.1, 114.23]])
        self.assertEqual(len(out), 3)  # 정리할 말이 든 틈은 그래도 자름

    def test_cut_starting_on_crammed_word_reaches_back_into_its_sound(self):
        blobs = [[111.12, 114.23], [114.37, 115.97]]
        words = [(111.9, 112.74, "자,", 0), (114.37, 114.42, "하나,", 0), (114.42, 114.47, "둘,", 0), (114.47, 114.54, "셋에", 0),
                 (114.54, 115.04, "맞춰서", 0)]
        out = msg.snap_edges([{"in": 100.0, "out": 110.5}, {"in": 114.22, "out": 118.0}], blobs, words, [])
        self.assertAlmostEqual(out[1]["in"], 112.79, places=2)

    def test_dangling_fragment_is_dropped(self):
        segs = [{"start": 128.78, "end": 131.78, "text": "오늘 진짜 물 좀 마시고 할게요.",
                 "words": [W("오늘", 128.78, 129.62), W("진짜", 129.62, 130.02), W("물", 130.91, 131.0), W("좀", 131.0, 131.14),
                           W("마시고", 131.14, 131.46), W("할게요.", 131.46, 131.78)]}]
        kept, junk = msg.clean_lines(segs, [[128.7, 130.1], [130.91, 132.04]])
        self.assertEqual([j[2] for j in junk], ["끊긴 말"])
        self.assertEqual([k["text"] for k in kept], ["물 좀 마시고 할게요."])
        # 소리가 그 뒤로 이어지면(낱말 시각이 틀림) 끊긴 말로 보지 않음
        kept, junk = msg.clean_lines(segs, [[128.7, 130.9]])
        self.assertFalse(junk)
        for t in ("고개 들기,", "공을", "오늘은", "자,", "하나 둘", "두 번째"):
            self.assertFalse(msg._fragment(t), t)
        self.assertTrue(msg._fragment("오늘 진짜"))

    def test_demo_window_does_not_swallow_unwritten_voice_at_its_edge(self):
        """말 없는 틈의 끝에 받아쓰지 못한 '어…'(목소리)가 있으면 그 앞에서 시범 구간을 끊음."""
        step = 0.48
        speech = [0.0] * 200
        for i in range(int(69.6 / step), int(71.0 / step) + 1):
            speech[i] = 0.98
        sig = {"duration": 90.0, "motion": [0.0] * 180, "motionStep": 0.5, "onsets": [69.15], "junk": [], "tags": {"speech": speech}, "tagStep": step}
        words = [(60.0, 67.48, "못했어요.", 0), (70.41, 71.22, "다시", 1), (80.0, 81.0, "끝", 2)]
        self.assertEqual(msg.demo_windows(sig, words), [])
        sig["tags"] = {}
        self.assertEqual(len(msg.demo_windows(sig, words)), 1)  # 목소리 정보가 없으면 예전처럼

    def test_short_isolated_piece_is_dropped_unless_it_matters(self):
        cuts = [{"in": 100.0, "out": 110.5}, {"in": 130.85, "out": 132.03}, {"in": 137.7, "out": 150.0}]
        self.assertEqual(len(msg.drop_slivers(cuts, [])), 2)
        self.assertEqual(len(msg.drop_slivers(cuts, [M("success", 131.2)])), 3)
        self.assertEqual(len(msg.drop_slivers([{"in": 0, "out": 1.0}, {"in": 1.0, "out": 9.0}], [])), 2)  # 앞이 이어지면 그대로


class TextTest(unittest.TestCase):
    def test_emphasis_clause_does_not_start_on_a_bound_word(self):
        lab, _ = msg._emph_clause("그리고 마지막으로, 받는 사람 발 쪽으로 정확하게 패스해 주세요.")
        self.assertIsNotNone(lab)
        self.assertFalse(lab.startswith("쪽"), lab)
        self.assertIn("발 쪽으로", lab)

    def test_count_only_on_the_real_count(self):
        self.assertFalse(msg.COUNT.search("자, 하나, 둘, 셋에 맞춰서 받아볼게요."))
        self.assertTrue(msg.COUNT.search("하나, 둘, 셋."))
        self.assertTrue(msg.COUNT.search("하나 둘 셋!"))

    def _plan(self, moms, kind="예능 MSG형", intensity="보통", words=(), avoid=(), knobs=None, dur=300.0):
        st = msg.resolve({"kind": "preset", "name": kind})
        sig = {"onsets": [o for m in moms if m["kind"] == "play" for o in (m["t"],)], "duration": dur}
        picked, _ = msg.plan_events(sig, moms, st, intensity, "long", 7, [{"in": 0.0, "out": dur}], list(words), knobs, avoid=avoid)
        return picked

    def test_demo_call_situ_only_when_a_demo_follows(self):
        moms = [M("demo_call", 50.0, "괜찮아요. 다시 해볼게요.", 49.0, 51.0), M("demo_call", 120.0, "하나, 둘, 셋 리듬으로 해볼게요.", 119.0, 121.0),
                M("play", 54.0, a=52.0, b=57.0, score=1.0)]
        situ = [c for c in self._plan(moms) if c["kind"] == "situ"]
        self.assertEqual([c["text"] for c in situ if c["src"] == "demo_call"], ["다시 도전"])
        self.assertFalse(any(c["text"] == "시범 들어갑니다" for c in situ))

    def test_demo_labels_do_not_repeat_or_announce(self):
        plays = [M("play", t, a=t - 2, b=t + 3) for t in (30.0, 90.0, 150.0, 210.0)]
        lab = msg._demo_labels(plays, plays)
        self.assertEqual([lab[id(m)] for m in plays], ["실전 시범", "한 번 더!", None, None])

    def test_gag_texts_never_repeat(self):
        moms = []
        for k, t in enumerate((30.0, 60.0, 90.0, 120.0, 150.0)):
            moms += [M("play", t - 3, a=t - 5, b=t - 0.5, score=1.0), M("success", t, "들어갔어요!", t, t + 1.5, score=1.0)]
        words = [(t - 10, t - 9, "슈팅", 0) for t in (30.0, 60.0, 90.0, 120.0, 150.0)]
        fx = [c["text"] for c in self._plan(moms, intensity="듬뿍", words=words) if c["kind"] == "fx"]
        self.assertEqual(len(fx), len(set(fx)), fx)

    def test_laugh_after_goal_is_proud_not_awkward(self):
        moms = [M("play", 33.0, a=30.0, b=34.5), M("success", 35.9, "이것도 들어갔어요.", 35.0, 37.6), M("punchline", 42.0, "매일 이러면 좋겠어요.", 40.9, 42.1)]
        inner = [c["text"] for c in self._plan(moms, intensity="듬뿍") if c["kind"] == "inner" and c["src"] == "punchline"]  # (시범·성공의 속마음은 따로)
        self.assertTrue(inner)
        self.assertTrue(all(t in msg.INNER_TEXTS["proud"] for t in inner), inner)

    def test_freeze_says_what_to_watch(self):
        moms = [M("fail", 40.0, "아깝다", 39.5, 41.0), M("play", 52.0, a=50.0, b=55.0), M("success", 56.0, "나이스!", 55.5, 57.0),
                M("play", 102.0, a=100.0, b=105.0), M("success", 106.0, "그렇죠!", 105.5, 107.0)]
        words = [(95.0, 95.5, "디딤발이", 0)]
        fr = [c for c in self._plan(moms, kind="다큐 감성형", intensity="듬뿍", words=words, knobs={"replay": 0, "freeze": 2}, dur=600.0) if c["kind"] == "freeze"]
        self.assertEqual([c["at"] for c in fr], [50.0, 100.0])
        self.assertTrue(fr[0]["retry"])
        self.assertFalse(fr[1]["retry"])
        self.assertEqual(fr[1]["term"], "디딤발")

    def test_replay_prefers_a_shot_not_used_in_the_teaser(self):
        moms = [M("play", 23.0, a=21.0, b=26.0, score=1.0), M("play", 70.0, a=68.0, b=73.0, score=0.8)]
        rp = [c for c in self._plan(moms, avoid=[(22.4, 24.1)], knobs={"replay": 1}) if c["kind"] == "replay"]
        self.assertEqual([c["a"] for c in rp], [68.0])
        rp = [c for c in self._plan(moms, knobs={"replay": 1}) if c["kind"] == "replay"]
        self.assertEqual([c["a"] for c in rp], [21.0])

    def test_unsaid_try_gets_its_own_label(self):
        """'세 번째'를 받아쓰기가 놓쳐도 점수판은 3번째로 셈 → 그 슛 장면 앞에 '세 번째 도전' (판정: 점수판이 갑자기 바뀜)."""
        moms = [M("total", 2.0, "다섯", 1.0, 6.0), M("section", 10.5, "첫 번째 갑니다.", 10.5, 12.2), M("play", 14.6, a=12.7, b=16.7),
                M("fail", 17.3, "아깝다", 17.0, 19.5), M("section", 20.2, "두 번째 슛.", 20.2, 21.2), M("play", 23.0, a=21.8, b=25.8),
                M("success", 26.2, "들어갔어요!", 26.1, 28.0), M("play", 32.3, a=30.8, b=34.8), M("success", 35.9, "이것도 들어갔어요.", 35.0, 37.6),
                M("section", 50.3, "네 번째 슛.", 50.3, 51.5), M("play", 54.8, a=52.1, b=56.1), M("fail", 57.4, "빗나갔어요", 56.4, 58.2)]
        board = msg._scoreboard(moms)
        self.assertEqual([c["text"] for c in board], ["1/5 · 0골", "2/5 · 1골", "3/5 · 2골", "4/5 · 2골"])
        lab = msg._unsaid_tries(board, [m for m in moms if m["kind"] == "play"])
        self.assertEqual([(p["a"], t) for p, t in lab], [(30.8, "세 번째 도전")])
        # 시도 묶음은 한 가지 꼴(말한 이름 '슛' + 번호 · round5 · 판정: '첫 번째 도전' → '슛 ②' → '세 번째 도전'처럼 꼴이 바뀌면 실수로 보임)
        situ = [c for c in self._plan(moms) if c["kind"] == "situ" and "③" in c["text"]]
        self.assertTrue(any(c["text"] == "슛 ③" and 30.8 <= c["t"] < 32.3 for c in situ), situ)

    def test_challenge_music_is_not_sentimental(self):
        moms = [M("section", t, f"{n} 번째 슛.", t, t + 1) for t, n in ((10, "첫"), (20, "두"), (30, "세"), (40, "네"))]
        moms += [M("success" if k % 2 else "fail", t + 5, "들어갔어요!" if k % 2 else "아깝다", t + 5, t + 6) for k, t in enumerate((10, 20, 30, 40))]
        out = msg._moods_for(msg.PRESETS["다큐 감성형"]["sound"]["moods"], sorted(moms, key=lambda m: m["t"]))
        self.assertEqual((out["demo"], out["lesson"], out["intro"]), ("경쾌", "경쾌", "감성"))
        self.assertEqual(msg._moods_for(msg.PRESETS["다큐 감성형"]["sound"]["moods"], []), msg.PRESETS["다큐 감성형"]["sound"]["moods"])

    def test_styles_have_different_speech_captions(self):
        cs = {k: msg._caption_style(msg.resolve({"kind": "preset", "name": k}), "long", 0.9) for k in ("예능 MSG형", "다큐 감성형", "담백 레슨형")}
        sig = {k: (v["weight"], v["size"], v["fill"], v.get("bgOn")) for k, v in cs.items()}
        self.assertEqual(len(set(sig.values())), 3, sig)

    def test_texts_avoid_the_face(self):
        b = msg._Build("x.mp4", {"duration": 60.0, "width": 1920, "height": 1080}, "long")
        t = b.title("자리에 서 있으면 왜 안 될까요?", 0.0, 3.0, dict(editor.TITLE_STYLE, size=84, y=0.2, x=0.5, align="center"))
        b.event("teaser", 0.0, t["text"], "", refs={"titles": [t["id"]]})
        face = [0.42, 0.08, 0.62, 0.36]
        msg.layout_titles(b, [], dict(editor.LONG_STYLE, y=0.9), 60.0, face)
        bx = msg.text_box(t["text"], t["style"], 1920, 1080)
        self.assertLess(msg._overlap(bx, [face[0] * 1920, face[1] * 1080, face[2] * 1920, face[3] * 1080]), 0.12)


class EndScreenTest(unittest.TestCase):
    def _build(self, dur_src=120.0, closing_at=100.0, last_out=106.0):
        b = msg._Build("x.mp4", {"duration": dur_src, "width": 1920, "height": 1080}, "long")
        b.clip(10.0, 60.0, main=True)
        b.clip(62.0, last_out, main=True)
        moms = [M("closing", closing_at, "오늘은 여기까지예요. 감사합니다.", closing_at, closing_at + 4)]
        return b, moms

    def test_end_screen_sits_on_the_closing_words(self):
        b, moms = self._build()
        n_v1 = len([x for x in b.items if x["track"] == "V1"])
        self.assertTrue(msg._end_overlay(b, moms, "보통", msg.SFX_PALETTE["variety"], 120.0))
        e = next(e for e in b.events if e["kind"] == "end")
        self.assertTrue(msg.END_MIN <= e["ins"]["len"] <= msg.END_MAX)
        self.assertAlmostEqual(e["ins"]["start"] + e["ins"]["len"], b.pos, places=3)
        v1 = sorted([x for x in b.items if x["track"] == "V1"], key=lambda x: x["start"])
        self.assertEqual(len(v1), n_v1 + 1)  # 나눈 것뿐 (새 장면·정지 사진 없음)
        a, c = v1[-2], v1[-1]
        self.assertAlmostEqual(editor.i_end(a), c["start"], places=3)
        self.assertAlmostEqual(float(a["out"]), float(c["in"]), places=3)  # 이어진 화면 (컷 아님)
        self.assertEqual(c["color"].get("exp"), msg.END_DIM["exp"])
        ev = msg.visual_events(b.items, b.titles, b.shapes, b.pos)
        self.assertFalse([k for t, k in ev if k == "cut" and t > 50.0])
        self.assertFalse(b.shapes)  # 빈 상자 없음
        self.assertTrue(e["ins"].get("overlay"))  # 편집실에서 빼도 본편을 지우거나 빈자리를 당기지 않음
        self.assertFalse(e["refs"].get("items"))
        self.assertEqual(e["refs"]["dim"], msg.END_DIM)

    def test_short_closing_borrows_the_rest_of_the_source(self):
        b, moms = self._build(dur_src=120.0, closing_at=104.0, last_out=106.0)
        self.assertTrue(msg._end_overlay(b, moms, "보통", msg.SFX_PALETTE["variety"], 120.0))
        e = next(e for e in b.events if e["kind"] == "end")
        self.assertGreaterEqual(e["ins"]["len"], msg.END_MIN - 1e-6)
        b, moms = self._build(dur_src=106.5, closing_at=104.0, last_out=106.0)
        self.assertFalse(msg._end_overlay(b, moms, "보통", msg.SFX_PALETTE["variety"], 106.5))  # 원본이 모자라면 따로

    def test_no_closing_means_separate_moving_end_screen(self):
        b, moms = self._build(closing_at=20.0)
        self.assertFalse(msg._end_overlay(b, moms, "보통", msg.SFX_PALETTE["variety"], 120.0))


class CompiledTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        cls.res = {k: msg.build_variants(cls.w.name, [{"kind": "preset", "name": n} for n in ("예능 MSG형", "담백 레슨형", "다큐 감성형")], k, ("long",),
                                         lambda m: None) for k in ("담백", "보통")}

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_end_screen_is_not_a_still_with_empty_boxes(self):
        for k, r in self.res.items():
            md = {m["id"]: m for m in r["media"]}
            for q in r["sequences"]:
                e = [e for e in q["msg"]["events"] if e["kind"] == "end"]
                self.assertEqual(len(e), 1, q["name"])
                tot = editor.seq_total(q)
                self.assertAlmostEqual(e[0]["ins"]["start"] + e[0]["ins"]["len"], tot, delta=0.05)
                self.assertFalse([s for s in q["shapes"] if s["name"] == "다음 영상 자리"], q["name"])
                under = [x for x in q["items"] if x["track"] == "V1" and x["start"] < tot - 0.1 and editor.i_end(x) > tot - 0.1]
                self.assertTrue(under)
                it = under[-1]
                moving = it["media"] == "main" or len(((it.get("fx") or {}).get("scale") or {}).get("k") or []) >= 2
                self.assertTrue(moving, (q["name"], it, md.get(it["media"])))

    def test_mild_has_no_teaser_clip_or_replay_and_starts_with_text(self):
        # round5: 담백도 앞 30초 안의 첫 질문이 있으면 그 질문 장면 하나로 시작 (콜드 오픈 · 판정: 인사로 시작해 첫 3초 훅이 약함) · 그 뒤는 원본 순서
        for q in self.res["담백"]["sequences"]:
            kinds = {e["kind"] for e in q["msg"]["events"]}
            self.assertNotIn("replay", kinds, q["name"])
            v1 = sorted([x for x in q["items"] if x["track"] == "V1"], key=lambda x: x["start"])
            opened = {i for e in q["msg"]["events"] if e["kind"] == "teaser" for i in (e["refs"].get("items") or [])}
            body = [x for x in v1 if x["media"] == "main" and x["id"] not in opened]
            cold = [x for x in v1 if x["id"] in opened]
            self.assertLessEqual(len(cold), 1, q["name"])  # 질문 장면 하나뿐 (티저 명장면 없음)
            self.assertTrue(all(float(a["in"]) <= float(b["in"]) for a, b in zip(body, body[1:]) if editor.i_end(b) < editor.seq_total(q) - 9),
                            q["name"])  # 원본 순서 그대로 (앞에 끼운 장면 없음)
            self.assertTrue(any(t["start"] <= 0.05 for t in q["titles"]), q["name"])
            on0 = [t for t in q["titles"] if t["start"] <= 0.05]
            self.assertLessEqual(len(on0), 2, q["name"])

    def test_title_first_style_shows_title_at_once(self):
        q = next(q for q in self.res["보통"]["sequences"] if "다큐" in q["name"])
        self.assertTrue(any(t["start"] <= 1.0 for t in q["titles"]), [(t["text"], t["start"]) for t in q["titles"]])


if __name__ == "__main__":
    unittest.main()
