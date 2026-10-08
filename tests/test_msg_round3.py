"""MSG round3 판정에서 나온 문제의 회귀 시험 — 저장소 폴더에서 python3 -m unittest tests.test_msg_round3
첫 화면: 0초 글자는 효과 없이 · 상자 위 글자는 상자와 함께 · 엔드: 말 자막이 끝난 뒤 · 마무리 말과 겹치지 않는 글 · 마지막 말 뒤 끝 시간 ·
컷: 받아쓰기 구간 첫머리에 몰아 적은 '하나, 둘, 셋에' · 공 소리 앞 준비 동작 · 다시 보기는 반응 말이 다 끝난 뒤 · 확대는 다른 사건·원본 장면 바뀜과
떨어지거나 그 자리에서 · 108% 뒤 쾅 확대는 그 크기에서 · 빼는 사건은 몰린 곳부터 · 글자: '한 번 더!'·명장면·실패 다시 보기 · 말 그대로 옮긴 긴 강조 ·
최종 결과 · 영상 끝에 걸린 글자 · 넓은 글자 · 스타일마다 다른 작은 제목 · 다큐 검은 띠 · 말 자막 바로 바뀜 · 자막 나누는 자리 · 효과음 겹침."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import captions  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402


def M(kind, t, text="", a=None, b=None, score=1.0, why=""):
    return {"kind": kind, "t": t, "a": t if a is None else a, "b": t + 1.0 if b is None else b, "score": score, "text": text, "why": why}


def L(a, b, text):
    return {"start": a, "end": b, "text": text, "words": []}


def build(src_dur=200.0, clips=((10.0, 60.0), (62.0, 106.0))):
    b = msg._Build("x.mp4", {"duration": src_dur, "width": 1920, "height": 1080}, "long")
    for a, c in clips:
        b.clip(a, c, main=True)
    return b


class OpeningTest(unittest.TestCase):
    def test_title_at_frame_zero_has_no_entrance_effect(self):
        """판정: 0.0초 첫 프레임에 글자가 작게 떴다가 커지거나(pop) 흐리게 나타남(fade) → 첫 프레임부터 다 보이게."""
        b = build()
        t0 = b.title("왜 다들 첫 터치에서 공을 놓칠까요?", 0.0, 3.0, dict(editor.TITLE_STYLE, effect="pop"))
        t1 = b.title("다시 보기", 20.0, 2.0, dict(editor.TITLE_STYLE, effect="pop"))
        msg._settle_titles(b)
        self.assertEqual(t0["style"]["effect"], "none")
        self.assertEqual(t1["style"]["effect"], "pop")  # 영상 중간은 그대로

    def test_text_on_a_box_appears_with_the_box(self):
        """판정: 초록 제목 상자가 먼저 뜨고 글자는 0.1~0.25초 뒤에 흐리게 나타나 빈 상자가 보임."""
        b = build()
        sh = b.shape(0.06, 0.6, 0.5, 0.25, msg.GREEN, 5.0, 2.0, 0.92, 24, "제목 카드")
        t1 = b.title("오늘의 주제", 5.1, 1.9, dict(editor.TITLE_STYLE, effect="fade"))
        t2 = b.title("퍼스트 터치", 5.25, 1.75, dict(editor.TITLE_STYLE, effect="stamp"))
        b.event("title", 5.0, "퍼스트 터치", "", refs={"shapes": [sh["id"]], "titles": [t1["id"], t2["id"]]})
        msg._settle_titles(b)
        self.assertEqual((t1["start"], t2["start"]), (5.0, 5.0))
        self.assertAlmostEqual(t1["start"] + t1["dur"], 7.0, places=3)
        self.assertEqual(t1["style"]["effect"], "none")
        self.assertEqual(t2["style"]["effect"], "stamp")  # 크게 찍히는 글자는 첫 프레임부터 보여 그대로

    def test_small_title_look_follows_the_intro_style(self):
        """판정: 담백에서는 스타일끼리 말 자막 글꼴만 달라 고르기 어려움 → 작은 제목 모양이 인트로 스타일마다 다름 (섞은 스타일은 인트로를 가져온 스타일)."""
        looks = {}
        for name in msg.PRESETS:
            parts = msg._small_title(msg.resolve({"kind": "preset", "name": name}), "퍼스트 터치")
            main = [p for p in parts if p[0] == "title"][-1][2]
            looks[name] = (any(p[0] == "shape" for p in parts), main.get("font"), main.get("fill"))
        self.assertEqual(len(set(looks.values())), len(looks), looks)
        st = msg.resolve({"kind": "preset", "name": "담백 레슨형"})
        st["intro"] = msg.resolve({"kind": "preset", "name": "예능 MSG형"})["intro"]  # 인트로만 예능에서 가져온 섞은 스타일
        parts = msg._small_title(st, "퍼스트 터치")
        self.assertFalse(any(p[0] == "shape" for p in parts))
        self.assertEqual(parts[-1][2]["fill"], msg.PRESETS["예능 MSG형"]["intro"]["titleColor"])


class EndCardTest(unittest.TestCase):
    def _end(self, said_last="오늘은 여기까지예요. 감사합니다.", src_dur=200.0, last_out=106.0, face=None, t0=100.0):
        b = build(src_dur, ((10.0, 60.0), (62.0, last_out)))
        words = [(t0, t0 + 0.6, "오늘은", 0), (t0 + 0.6, t0 + 1.4, "여기까지예요.", 0)] + [(t0 + 2.0 + 0.5 * k, t0 + 2.4 + 0.5 * k, w, 1)
                                                                                    for k, w in enumerate(said_last.split()[2:])]
        caps = [{"id": "c1", "start": t0, "end": t0 + 1.4, "text": "오늘은 여기까지예요."},
                {"id": "c2", "start": t0 + 2.0, "end": words[-1][1], "text": " ".join(said_last.split()[2:])}]
        moms = [M("closing", t0, said_last, t0, words[-1][1])]
        ok = msg._end_overlay(b, moms, "보통", msg.SFX_PALETTE["variety"], src_dur, face, words, caps, [])
        return b, ok, caps

    def test_end_text_waits_until_the_last_caption_is_gone(self):
        """판정: 엔드 글자가 마지막 대사 자막 바로 위에 쌓여 얼굴·몸을 가림 → 말 자막이 사라진 뒤 아래쪽에 한꺼번에."""
        b, ok, caps = self._end()
        self.assertTrue(ok)
        tl_caps = editor.timeline_captions({"items": b.items, "captions": caps, "format": "long"})
        cap_end = max(c["end"] for c in tl_caps)
        e = next(e for e in b.events if e["kind"] == "end")
        ts = [t for t in b.titles if t["id"] in e["refs"]["titles"]]
        self.assertEqual(len(ts), 2)
        self.assertTrue(all(t["start"] >= cap_end for t in ts), (cap_end, [(t["text"], t["start"]) for t in ts]))
        self.assertEqual(len({t["start"] for t in ts}), 1)  # 한꺼번에 (화면 사건 하나)
        self.assertTrue(all(float(t["style"]["y"]) >= 0.7 for t in ts))  # 얼굴(위쪽)을 가리지 않게 아래쪽
        self.assertTrue(msg.END_MIN <= e["ins"]["len"] <= msg.END_MAX)
        self.assertAlmostEqual(e["ins"]["start"] + e["ins"]["len"], b.pos, places=3)

    def test_tail_after_the_last_word_keeps_the_last_caption_readable(self):
        """판정: 마지막 '감사합니다.' 자막이 영상 끝에 걸려 0.5초만 보임 → 원본에 있으면 마지막 말 뒤를 END_TAIL 초까지."""
        b, ok, caps = self._end(last_out=103.0)
        self.assertTrue(ok)
        last_tl = editor.i_tl(b.main[-1], 102.4)
        self.assertGreaterEqual(b.pos - last_tl, msg.END_TAIL - 0.05)
        tl_caps = editor.timeline_captions({"items": b.items, "captions": caps, "format": "long"})
        self.assertGreaterEqual(tl_caps[-1]["end"] - tl_caps[-1]["start"], editor.CAP_MIN - 1e-6)

    def test_end_line_does_not_repeat_what_was_said(self):
        b, ok, _ = self._end("오늘은 여기까지예요. 다음 영상에서 만나요.")
        e = next(e for e in b.events if e["kind"] == "end")
        texts = [t["text"] for t in b.titles if t["id"] in e["refs"]["titles"]]
        self.assertNotIn("다음 영상도 같이 봐요!", texts)
        self.assertEqual(msg.end_line("감사합니다 다음 영상에서 만나요 댓글 남겨주세요 구독"), None)
        self.assertEqual(msg.end_line("오늘은 여기까지예요."), "다음 영상도 같이 봐요!")

    def test_no_room_after_the_words_means_a_small_corner_badge(self):
        """마지막 말 뒤 원본이 거의 없으면 큰 글자 대신 얼굴 반대쪽 위 구석의 작은 구독 표시만 (말 자막과 안 겹침)."""
        b, ok, _ = self._end("오늘은 여기까지예요. 연습 많이 해 보시고 꼭 또 봐요", src_dur=106.1, last_out=106.0, face=[0.62, 0.1, 0.82, 0.4],
                             t0=100.4)
        self.assertTrue(ok)
        e = next(e for e in b.events if e["kind"] == "end")
        ts = [t for t in b.titles if t["id"] in e["refs"]["titles"]]
        self.assertEqual(len(ts), 1)
        self.assertLess(float(ts[0]["style"]["y"]), 0.2)
        self.assertEqual(ts[0]["style"]["align"], "left")  # 얼굴이 오른쪽이면 왼쪽 구석


class CutEdgeTest(unittest.TestCase):
    def test_crammed_count_at_segment_start_is_kept(self):
        """판정(쇼츠 하이텐션형): 받아쓰기 구간 첫머리에 '하나, 둘, 셋에'를 0.2초로 몰아 적고, 컷이 앞 소리 덩어리가 끝난 틈에서 시작 →
        '하나, 둘'이 잘리고 '셋에' 가운데서 시작. 바로 앞 소리 덩어리(받아 적지 않은 소리)까지 당김."""
        blobs = [[111.12, 114.23], [114.37, 115.97]]
        words = [(111.9, 112.74, "자,", 0), (114.37, 114.42, "하나,", 1), (114.42, 114.47, "둘,", 1), (114.47, 114.54, "셋에", 1),
                 (114.54, 115.04, "맞춰서", 1)]
        out = msg.snap_edges([{"in": 100.0, "out": 110.5}, {"in": 114.27, "out": 118.0}], blobs, words, [])
        self.assertAlmostEqual(out[1]["in"], 112.79, places=2)
        # 앞 소리 덩어리가 앞 낱말로 다 설명되면(받아 적지 않은 소리 없음) 그대로
        out = msg.snap_edges([{"in": 114.27, "out": 118.0}], [[111.12, 112.9], [114.37, 115.97]], words, [])
        self.assertGreaterEqual(out[0]["in"], 114.27)

    def test_crammed_run_at_segment_start_is_spread_for_captions(self):
        """같은 묶음이 새 받아쓰기 구간의 첫머리면(앞 낱말은 앞 구간) 구간 안만 보던 펼치기가 못 해 자막이 '셋에' 때에야 뜸 → 구간을 넘어 펼침."""
        segs = [{"start": 108.0, "end": 112.74, "text": "공을 받아요. 자,", "words": [{"w": "공을", "s": 108.0, "e": 108.6}, {"w": "받아요.", "s": 108.6, "e": 109.5},
                                                                               {"w": "자,", "s": 111.9, "e": 112.74}]},
                {"start": 114.37, "end": 115.82, "text": "하나, 둘, 셋에 맞춰서 받아볼게요.",
                 "words": [{"w": "하나,", "s": 114.37, "e": 114.42}, {"w": "둘,", "s": 114.42, "e": 114.47}, {"w": "셋에", "s": 114.47, "e": 114.54},
                           {"w": "맞춰서", "s": 114.54, "e": 115.04}, {"w": "받아볼게요.", "s": 115.04, "e": 115.82}]}]
        out = msg.respread(segs, [[108.0, 109.6], [111.12, 114.23], [114.37, 115.97]])
        w = out[1]["words"]
        self.assertAlmostEqual(w[0]["s"], 112.76, places=2)
        self.assertGreater(w[2]["e"] - w[0]["s"], 1.0)
        self.assertEqual(segs[1]["words"][0]["s"], 114.37)  # 받아쓰기 원본은 그대로
        self.assertEqual(msg.respread(segs, []), segs)

    def test_demo_keeps_the_run_up_before_the_ball_sound(self):
        """판정: 시범이 공 차는 순간만 남아(목소리 다듬기에 걸려) 통째로 빠짐 → 화면이 움직이는 공 소리면 DEMO_PRE 초 앞 준비 동작까지."""
        step = 0.48
        speech = [0.0] * 400
        for i in range(int(171.36 / step), int(175.2 / step) + 1):
            speech[i] = 0.95
        motion = [0.0] * 400
        motion[342] = 50.0  # 171.0초 (0.5초 칸)
        sig = {"duration": 190.0, "motion": motion, "motionStep": 0.5, "onsets": [171.04], "junk": [], "tags": {"speech": speech}, "tagStep": step}
        words = [(165.36, 166.04, "해볼게요.", 0), (171.92, 173.08, "그렇죠.", 1), (180.0, 181.0, "끝", 2)]
        dw = msg.demo_windows(sig, words)
        self.assertEqual(len(dw), 1, dw)
        self.assertLessEqual(dw[0][0], 171.04 - msg.DEMO_PRE + 0.01)
        sig["motion"] = [0.0] * 400  # 화면이 안 움직이는 순간 큰 소리(말 첫소리·잡음)는 예전처럼 좁게
        self.assertEqual(msg.demo_windows(sig, words), [])

    def test_replay_waits_for_the_whole_reaction(self):
        """판정: '와,'와 '이거죠.' 사이·'아 아깝다.'와 '터치가 조금 길었네요.' 사이에 다시 보기를 끼워 말이 7~9초 끊김."""
        lines = [L(77.0, 77.96, "와,"), L(78.69, 79.06, "이거죠."), L(79.71, 80.34, "완벽해요."), L(84.0, 86.0, "다음은 두 번째 동작이에요.")]
        self.assertAlmostEqual(msg._after_reaction(lines, 76.9), 80.49, places=2)
        lines = [L(160.62, 162.4, "아, 아깝다."), L(163.42, 164.7, "터치가 조금 길었네요."), L(165.36, 166.04, "다시 해볼게요.")]
        self.assertAlmostEqual(msg._after_reaction(lines, 160.5), 164.85, places=2)
        lines = [L(30.0, 31.0, "좋아요."), L(32.6, 34.0, "그리고 다음 동작은 패스예요.")]
        self.assertAlmostEqual(msg._after_reaction(lines, 29.9), 31.15, places=2)  # 1.5초 넘게 쉬고 새 설명이면 거기까지


class ZoomTest(unittest.TestCase):
    def test_static_fill_avoids_jerky_double_changes(self):
        """판정: 확대가 원본 장면 바뀜 0.7초 뒤에 또 들어가 덜컥거림 → 원본 장면 바뀜과 떨어지거나(1.2초) 그 자리에서 · 다른 사건과도 떨어뜨림."""
        b = build(200.0, ((0.0, 60.0),))
        words = [(0.5 + k * 1.0, 1.2 + k * 1.0, "말", 0) for k in range(58)]
        src_cuts = [14.3, 29.8, 45.2]
        msg.govern(b, "담백", [0.5, 0.4], b.pos, (), words, src_cuts)
        ev = msg.visual_events(b.items, b.titles, b.shapes, b.pos)
        zs = [t for t, k in ev if k == "zoom"]
        self.assertTrue(zs)
        for t in zs:
            d = min(abs(t - c) for c in src_cuts)
            self.assertTrue(d < 0.01 or d >= msg.SRC_CUT_CLEAR - 0.01, (t, d))
        gaps = [y - x for x, y in zip([0.0] + [t for t, _ in ev], [t for t, _ in ev] + [b.pos])]
        self.assertLessEqual(max(gaps), msg.DENSITY["담백"][2] + 1e-6)

    def test_punch_after_soft_zoom_starts_from_that_size(self):
        """108% 조각 바로 뒤 쾅 확대가 100%에서 다시 시작하면 한 번 줄었다 커짐 (판정: 같은 때 확대 두 번)."""
        b = build(200.0, ((10.0, 20.0), (20.0, 30.0)))
        a, c = b.main
        a["fx"] = {"scale": {"v": 108.0, "k": []}, "anchor": {"v": [0.5, 0.4], "k": []}}
        c["fx"] = {"scale": {"v": 100.0, "k": [{"t": 20.0, "v": 100.0, "e": "ease"}, {"t": 20.25, "v": 125.0, "e": "lin"}]}}
        before = len([k for _, k in msg.visual_events(b.items, b.titles, b.shapes, b.pos) if k == "zoom"])
        msg._smooth_zoom_joins(b)
        after = len([k for _, k in msg.visual_events(b.items, b.titles, b.shapes, b.pos) if k == "zoom"])
        self.assertEqual((before, after), (2, 1))
        self.assertEqual(c["fx"]["scale"]["k"][0]["v"], 108.0)
        self.assertEqual(c["fx"]["scale"]["k"][1]["v"], 125.0)

    def test_trim_drops_crowded_events_not_the_last_ones(self):
        """판정(순간 재현율 퇴보): 사건이 많을 때 뒤쪽부터 빼서 영상 끝부분의 강조·성공 순간만 빠짐 → 몰린 곳부터."""
        b = build(400.0, ((0.0, 120.0),))
        ids = {}
        for t in (10.0, 12.0, 14.0, 16.0, 30.0, 50.0, 70.0, 90.0, 110.0):
            tt = b.title(f"강조 {t}", t, 1.5, dict(editor.TITLE_STYLE))
            ids[t] = b.event("emphasis", t, tt["text"], "", src=t, refs={"titles": [tt["id"]]})["id"]
        msg.DENSITY["_시험"] = (0.0, 4.0, 60.0)
        try:
            msg.govern(b, "_시험", [0.5, 0.4], b.pos)
        finally:
            del msg.DENSITY["_시험"]
        left = {e["t"] for e in b.events if e["kind"] == "emphasis"}
        self.assertIn(110.0, left)
        self.assertIn(90.0, left)
        self.assertLess(len(left & {10.0, 12.0, 14.0, 16.0}), 4)


class TextTest(unittest.TestCase):
    def _plan(self, moms, kind="예능 MSG형", intensity="보통", words=(), knobs=None, dur=300.0, onsets=None):
        st = msg.resolve({"kind": "preset", "name": kind})
        sig = {"onsets": onsets if onsets is not None else [m["t"] for m in moms if m["kind"] == "play"], "duration": dur}
        return msg.plan_events(sig, moms, st, intensity, "long", 7, [{"in": 0.0, "out": dur}], list(words), knobs)

    def test_new_step_after_a_demo_is_not_one_more(self):
        """판정: '두 번째 동작은…' 설명 뒤 시범에 '한 번 더!' (엉뚱)."""
        plays = [M("play", 30.0, a=28.0, b=33.0), M("play", 90.0, a=88.0, b=93.0)]
        moms = plays + [M("section", 70.0, "두 번째 동작은 패스하고 옆으로 빠지는 거예요.", 70.0, 74.0)]
        lab = msg._demo_labels(plays, sorted(moms, key=lambda m: m["t"]))
        self.assertEqual([lab[id(m)] for m in plays], ["실전 시범", None])

    def test_failed_try_is_not_replayed_or_shaken(self):
        """판정: 실패 장면에 흔들기와 '다시 보기'를 붙여 실수를 놀리는 것처럼 보임."""
        moms = [M("play", 30.0, a=28.0, b=33.0, score=1.0), M("fail", 34.0, "아 아깝다", 33.5, 35.0),
                M("play", 60.0, a=58.0, b=63.0, score=0.5), M("success", 64.0, "그렇죠!", 63.5, 65.0)]
        picked, _ = self._plan(moms, intensity="듬뿍")
        self.assertFalse([c for c in picked if c["kind"] in ("replay", "shake") and 27.0 <= c["t"] <= 34.0], picked)
        self.assertTrue([c for c in picked if c["kind"] == "replay" and c["a"] == 58.0])

    def test_montage_uses_real_plays_only(self):
        """판정: 설명 장면에 '오늘의 명장면' → 공 소리·칭찬·반응이 붙은 시범만 · 모자라면 몽타주 없음."""
        weak = [M("play", t, a=t - 2, b=t + 2, why="말 없는 3초 · 공 소리 0번") for t in (20.0, 60.0, 100.0, 140.0, 180.0)]
        _, mont = self._plan(weak, intensity="듬뿍", onsets=[])
        self.assertIsNone(mont)
        strong = [M("play", t, a=t - 2, b=t + 2, why="말 없는 3초 · 공 소리 1번") for t in (20.0, 60.0, 100.0, 140.0, 180.0)]
        _, mont = self._plan(strong, intensity="듬뿍")
        self.assertTrue(mont)

    def test_final_score_replaces_the_last_board(self):
        """'5/5 · 3골' 뒤 2.5초에 같은 뜻의 '최종 결과'를 또 띄우지 않고 마지막 점수판이 최종 결과를 겸함 (화면 사건 하나 덜)."""
        moms = [M("total", 2.0, "세", 1.0, 6.0)]
        for n, (t, res) in enumerate(((10.0, "success"), (30.0, "fail"), (50.0, "success")), 1):
            moms += [M("section", t, f"{msg.ORD_KO[n]} 번째 슛.", t, t + 1), M("play", t + 4, a=t + 2, b=t + 6),
                     M(res, t + 7, "들어갔어요!" if res == "success" else "아깝다", t + 7, t + 8)]
        picked, _ = self._plan(sorted(moms, key=lambda m: m["t"]))
        texts = [c["text"] for c in picked if c["kind"] in ("score", "situ")]
        self.assertIn("최종 결과 · 3번 중 2골!", texts)
        self.assertNotIn("3/3 · 2골", texts)
        self.assertEqual(sum(1 for x in texts if x.startswith("최종 결과")), 1)

    def test_verbatim_long_emphasis_is_not_shown_twice(self):
        """판정: 강조 '발 쪽으로 정확하게 패스해주세요!'가 바로 위 대사 자막을 거의 그대로 되풀이 → 글자는 안 띄우고 순간(확대)만."""
        segs = [{"start": 102.46, "end": 108.74, "text": "그리고 마지막으로 받는 사람 발 쪽으로 정확하게 패스해주세요.",
                 "words": [{"w": w, "s": 102.46 + k * 0.7, "e": 102.46 + k * 0.7 + 0.6} for k, w in
                           enumerate("그리고 마지막으로 받는 사람 발 쪽으로 정확하게 패스해주세요.".split())]}]
        sig = {"name": "x", "duration": 200.0, "tidy": [{"in": 0.0, "out": 200.0}], "junk": [], "onsets": [], "motion": [0.0] * 10, "motionStep": 0.5}
        em = [m for m in msg.moments(sig, segs) if m["kind"] == "emphasis"]
        self.assertTrue(em)
        self.assertEqual(em[0]["text"], "")
        picked, _ = self._plan(em)
        self.assertFalse([c for c in picked if c["kind"] == "emphasis"])
        self.assertTrue([c for c in picked if c["kind"] == "punch"])


class LayoutTest(unittest.TestCase):
    def test_text_at_the_very_end_is_dropped_and_wide_text_fits(self):
        b = msg._Build("x.mp4", {"duration": 60.0, "width": 1080, "height": 1920}, "shorts")
        late = b.title("(각오 완료)", 44.7, 1.6, dict(editor.TITLE_STYLE, size=70))
        b.event("inner", 44.7, late["text"], "", refs={"titles": [late["id"]]})
        wide = b.title("왜 다들 첫 터치에서 공을…", 0.0, 2.5, dict(editor.TITLE_STYLE, size=96, bgOn=True, bg="#0F7A3D", bgOpacity=0.92, strokeW=14))
        b.event("hook", 0.0, wide["text"], "", refs={"titles": [wide["id"]]})
        gone = msg.layout_titles(b, [], dict(editor.SHORTS_STYLE), 45.0)
        self.assertIn(late["id"], gone)
        bx = msg.text_box(wide["text"], wide["style"], 1080, 1920)
        self.assertGreaterEqual(bx[0], 0.05 * 1080 - 0.5)
        self.assertLessEqual(bx[2], 0.95 * 1080 + 0.5)

    def test_speech_captions_switch_instantly(self):
        """판정: 자막이 바뀌는 순간 반투명하게 흐려져 밝은 배경에서 안 읽힘."""
        for name in msg.PRESETS:
            st = msg.resolve({"kind": "preset", "name": name})
            cs = msg._caption_style(st, "long", msg.caption_y(st, "long"))
            self.assertIn(cs["effect"], ("none", "karaoke"), name)

    def test_caption_breaks_keep_phrases_together(self):
        """판정: '그리고 마지막으로 받는 / 사람 발 쪽으로…' · '그리고 이건 여담인데 공은 / 항상…' 처럼 말 덩어리 가운데서 끊김."""
        def ws(text, t0, step):
            return [{"w": w, "s": round(t0 + k * step, 2), "e": round(t0 + k * step + step - 0.02, 2)} for k, w in enumerate(text.split())]
        out = captions.chunk(ws("그리고 마지막으로 받는 사람 발 쪽으로 정확하게 패스해주세요.", 102.46, 0.78), "long")
        self.assertFalse([c for c in out if c["text"].replace("\n", " ").endswith("받는")], [c["text"] for c in out])
        out = captions.chunk(ws("그리고 이건 여담인데 공은 항상 두 개쯤 챙겨오세요.", 88.97, 0.55), "long")
        self.assertEqual(out[0]["text"].replace("\n", " "), "그리고 이건 여담인데", [c["text"] for c in out])
        self.assertTrue(captions._adnominal("받는", "사람"))
        self.assertFalse(captions._adnominal("공은", "항상"))


class CompiledTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        cls.res = {k: msg.build_variants(cls.w.name, [{"kind": "preset", "name": n} for n in ("예능 MSG형", "담백 레슨형", "다큐 감성형")], k, ("long",),
                                         lambda m: None) for k in ("담백", "듬뿍")}

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_first_frame_and_boxes(self):
        for r in self.res.values():
            for q in r["sequences"]:
                self.assertTrue(all(t["style"].get("effect") == "none" for t in q["titles"] if t["start"] < 0.05), q["name"])
                for e in q["msg"]["events"]:
                    shs = [s for s in q["shapes"] if s["id"] in (e.get("refs") or {}).get("shapes", [])]
                    ts = [t for t in q["titles"] if t["id"] in (e.get("refs") or {}).get("titles", [])]
                    for s in shs:
                        for t in ts:
                            self.assertFalse(s["start"] < t["start"] <= s["start"] + 0.5, (q["name"], e["kind"], t["text"]))

    def test_documentary_keeps_its_letterbox_all_the_way(self):
        for r in self.res.values():
            q = next(q for q in r["sequences"] if "다큐" in q["name"])
            bars = [s for s in q["shapes"] if s.get("full")]
            self.assertEqual(len(bars), 2, q["name"])
            self.assertFalse([s for s in q["shapes"] if not s.get("full") and s["name"] == "검은 띠"], q["name"])  # 제목 화면에만 있다가 사라지는 띠 없음
            other = next(q for q in r["sequences"] if "예능" in q["name"])
            self.assertFalse([s for s in other["shapes"] if s.get("full")])

    def test_sound_effects_never_overlap(self):
        for r in self.res.values():
            md = {m["id"]: m for m in r["media"]}
            for q in r["sequences"]:
                fx_tr = {t["id"] for t in q["tracks"] if t["k"] == "a" and t.get("name") == "효과음"}
                ivs = sorted((it["start"], it["start"] + float(md[it["media"]].get("dur") or 0.5)) for it in q["items"] if it["track"] in fx_tr)
                for (a0, a1), (b0, _) in zip(ivs, ivs[1:]):
                    self.assertGreaterEqual(b0, a1 - 0.05, (q["name"], (a0, a1), b0))

    def test_end_text_does_not_sit_on_speech_captions(self):
        for r in self.res.values():
            for q in r["sequences"]:
                e = next(e for e in q["msg"]["events"] if e["kind"] == "end")
                if not (e.get("ins") or {}).get("overlay"):
                    continue
                caps = editor.timeline_captions(dict(q, captions=r["captions"]))
                for t in [t for t in q["titles"] if t["id"] in e["refs"]["titles"]]:
                    on = [c for c in caps if min(c["end"], t["start"] + t["dur"]) - max(c["start"], t["start"]) > 0.02]
                    if on:  # 말하는 동안이면 구석의 작은 표시만
                        self.assertLess(float(t["style"]["y"]), 0.2, (q["name"], t["text"]))


if __name__ == "__main__":
    unittest.main()
