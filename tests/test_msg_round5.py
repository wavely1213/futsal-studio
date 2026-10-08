"""MSG round5 검토 회귀 시험 — 저장소 폴더에서 python3 -m unittest tests.test_msg_round5
재미 글자: 글자 양은 '재미' 부분(섞기에서 재미를 가져오면 개그 글자도 따라옴) · 시범·딴소리·정리 말 정해 둔 문구 · 클로드 재미 자막(검사·캐시·잘린 말 빼기) ·
말 자막 안의 낱말을 다시 띄우지 않음 · 번호 꼬리표 한 가지 꼴 · 글꼴에 없는 글자 · 스타일 설명은 엔진 값으로 · 컷 리듬·티저 수가 스타일마다 다름 ·
다시 만들기는 양 범위를 넘을 때만(스타일 얼굴은 남김) · 긴 원본(약 9분): 다시 보기·정지·몽타주가 남고 스타일끼리 다름 · 마무리는 끝부분 한 번 ·
멈추기 · 쓸 수 없는 원본 · 소리 크기 한 번에 조금씩 읽기 · 아주 작은 말소리의 효과음 · 다시 보기 자리 · 배경음악 판 바꾸기·내 배경음악 ·
정지 화면 이름('%d'·NFD) · 소리 없는 원본 내보내기 · 효과음 조각 · 디스크 · 최대 크기 다시 만들기 실패 · 큰 작업 결과."""
import json
import math
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
import msgwrite  # noqa: E402
import sfxlib  # noqa: E402
import style  # noqa: E402
import make_msg_fixture as mf  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402


def M(kind, t, text="", a=None, b=None, score=1.0, why=""):
    return {"kind": kind, "t": t, "a": t if a is None else a, "b": t + 1.0 if b is None else b, "score": score, "text": text, "why": why}


def plan(moms, st, intensity="보통", words=(), dur=300.0, caps=None, ai=None):
    sig = {"onsets": [m["t"] for m in moms if m["kind"] == "play"], "duration": dur}
    return msg.plan_events(sig, moms, st, intensity, "long", 7, [{"in": 0.0, "out": dur}], list(words), None, ai=ai, caps=caps)[0]


class TmpWork(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="MSG r5 시험 "))
        self.work = self.tmp / "풋살 작업 폴더"
        for d in ("videos", "analysis", "out", "edit_media", "projects", "styles"):
            (self.work / d).mkdir(parents=True, exist_ok=True)
        self.p = [mock.patch.object(core, "WORK", self.work), mock.patch.object(core, "VIDEOS", self.work / "videos"),
                  mock.patch.object(core, "ANALYSIS", self.work / "analysis"), mock.patch.object(core, "OUT", self.work / "out"),
                  mock.patch.object(editor, "ASSETS", self.work / "edit_media"), mock.patch.object(editor, "PROJECTS", self.work / "projects"),
                  mock.patch.object(style, "STYLES", self.work / "styles")]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        editor.CANCEL.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)


class AspectTest(TmpWork):
    """판정: 강조·상황·속마음·효과 글자 양이 '자막' 부분에 있어 '재미는 예능, 자막은 담백'으로 섞으면 개그 글자가 거의 없음."""

    def test_joke_amounts_live_in_fun(self):
        for k, v in msg.PRESETS.items():
            self.assertIn("perMin", v["fun"], k)
            self.assertNotIn("perMin", v["captions"], k)

    def test_mix_takes_jokes_from_fun_source(self):
        msg.save_mix("하우스", {"fun": {"kind": "preset", "name": "예능 MSG형"}, "captions": {"kind": "preset", "name": "담백 레슨형"}}, "보통")
        st = msg.resolve({"kind": "mix", "name": "하우스"})
        self.assertEqual(st["fun"]["perMin"], msg.PRESETS["예능 MSG형"]["fun"]["perMin"])
        self.assertEqual(st["captions"]["emphBox"], msg.PRESETS["담백 레슨형"]["captions"]["emphBox"])  # 자막 모양은 담백
        self.assertEqual(msg._text_rates(st["fun"], "보통"), msg._text_rates(msg.PRESETS["예능 MSG형"]["fun"], "보통"))
        moms = [M("punchline", 15.0 + 25 * k, "제가 원래 잘 넘어지거든요.", 14.0 + 25 * k, 16.0 + 25 * k) for k in range(3)]
        n = lambda s_: sum(1 for c in plan(moms, s_, dur=60.0) if c["kind"] in ("inner", "fx"))  # noqa: E731
        self.assertEqual(n(st), n(msg.resolve({"kind": "preset", "name": "예능 MSG형"})))     # 재미를 가져온 스타일과 같은 개그 글자 양
        self.assertGreater(n(st), n(msg.resolve({"kind": "preset", "name": "담백 레슨형"})))  # (자막을 가져온 담백보다 많음)

    def test_describe_says_what_the_engine_does(self):
        P = msg.PRESETS
        self.assertIn("명장면 2개", msg.describe_style(P["예능 MSG형"], "보통"))
        self.assertIn("명장면 3개", msg.describe_style(P["예능 MSG형"], "듬뿍"))
        self.assertIn("첫 질문 장면으로 시작", msg.describe_style(P["예능 MSG형"], "담백"))
        self.assertIn("나누지 않음", msg.describe_aspect("rhythm", P["쇼츠 하이텐션형"]["rhythm"], "담백"))
        self.assertIn(f"{2.5 / msg.RHYTHM['보통']:.0f}초마다", msg.describe_aspect("rhythm", P["쇼츠 하이텐션형"]["rhythm"], "보통"))
        self.assertIn("영화 제목", msg.describe_style(P["다큐 감성형"], "보통"))
        self.assertIn("1분에", msg.describe_aspect("fun", P["예능 MSG형"]["fun"], "보통"))
        self.assertNotIn("1분", msg.describe_aspect("captions", P["예능 MSG형"]["captions"]))
        src = msg.sources_listing()
        self.assertEqual(set(src["presets"][0]["descs"]), set(msg.INTENSITY))


class TextTest(unittest.TestCase):
    def test_word_inside_caption_is_a_repeat(self):
        caps = [{"start": 10.0, "end": 13.0, "text": "어? 이것도 들어갔어요. 대박!"}, {"start": 20.0, "end": 21.0, "text": "완벽해요."},
                {"start": 30.0, "end": 33.0, "text": "두 번째 포인트는 디딤발이에요."}]
        self.assertTrue(msg._cap_dup("대박!", 11.0, 12.0, caps))
        self.assertTrue(msg._cap_dup("완벽!", 20.0, 21.0, caps))
        self.assertTrue(msg._cap_dup("두 번째 포인트", 30.0, 32.0, caps))
        self.assertFalse(msg._cap_dup("나이스!!", 20.0, 21.0, caps))
        self.assertEqual(msg._dedupe_text("fx", "완벽!", 20.0, 1.0, caps, ["완벽!", "깔끔!"]), ("깔끔!", 20.0))
        self.assertEqual(msg._dedupe_text("situ", "두 번째 포인트", 30.0, 2.0, caps), ("포인트 ②", 30.0))
        self.assertEqual(msg._dedupe_text("emphasis", "디딤발!", 30.0, 1.5, caps, term="디딤발"), None)  # 기술 이름도 말 자막 안이면 없음

    def test_numbered_labels_use_one_format(self):
        cands = [{"kind": "situ", "text": "첫 번째 도전", "t": 10.0, "must": True}, {"kind": "situ", "text": "두 번째 슛", "t": 20.0},
                 {"kind": "situ", "text": "세 번째 도전", "t": 30.0, "must": True}, {"kind": "situ", "text": "첫 번째 포인트", "t": 40.0},
                 {"kind": "situ", "text": "마지막! 세 번째 포인트", "t": 60.0}, {"kind": "situ", "text": "실전 시범", "t": 70.0}]
        msg._series_badges(cands)
        self.assertEqual([c["text"] for c in cands], ["슛 ①", "슛 ②", "슛 ③", "포인트 ①", "마지막! 포인트 ③", "실전 시범"])

    def test_glyphs_missing_from_title_fonts(self):
        self.assertEqual(msg.glyph_safe("아깝다…", "Black Han Sans"), "아깝다...")
        self.assertEqual(msg.glyph_safe("2/5 · 1골", "Black Han Sans"), "2/5, 1골")
        self.assertEqual(msg.glyph_safe("포인트 ③", "Black Han Sans"), "포인트 3")
        self.assertEqual(msg.glyph_safe("포인트 ③", "Do Hyeon"), "포인트 3")   # 도현에는 있지만 작게 뭉개짐 (판정 round5 최종)
        self.assertEqual(msg.glyph_safe("포인트 ③", None), "포인트 3")
        self.assertEqual(msg.glyph_safe("① 고개 들기\n② 디딤발 거리", None), "1. 고개 들기\n2. 디딤발 거리")
        self.assertEqual(msg.glyph_safe("(생각 중…)", "Do Hyeon"), "(생각 중...)")
        b = msg._Build("x.mp4", {"duration": 60}, "long")
        t = b.title("(아까비…)", 1.0, 1.0, {"font": "Do Hyeon", "y": 0.5})
        self.assertEqual(t["text"], "(아까비...)")

    def test_recap_line_becomes_a_numbered_card(self):
        lines = [{"start": 179.3, "end": 181.0, "text": "오늘 배운거 정리해볼게요."}, {"start": 181.4, "end": 186.0, "text": "고개 들기, 디딤발거리, 그리고 다음 방향으로 받기."}]
        txt, b, a = msg._recap_items(lines, 0)
        self.assertEqual(txt, "① 고개 들기\n② 디딤발거리\n③ 다음 방향으로 받기")
        self.assertEqual((a, b), (181.4, 186.0))
        self.assertIsNone(msg._recap_items([{"start": 0, "end": 2, "text": "정리하면 이게 진짜 중요한 거예요."}], 0))

    def test_template_jokes_for_demos_and_asides(self):
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        moms = [M("play", 30.0, a=26.0, b=33.0, why="공 소리 1번"), M("aside", 60.0, "(물 타임)", 59.0, 61.0)]
        texts = [(c["kind"], c.get("text")) for c in plan(moms, st, "듬뿍")]
        self.assertTrue(any(k == "inner" and t in msg.INNER_TEXTS["demo"] for k, t in texts), texts)
        self.assertTrue(any(k == "fx" and t in msg.FX_TEXTS["touch"] for k, t in texts), texts)   # 슛 이야기가 없는 시범은 가벼운 '툭!'류
        self.assertIn(("inner", "(물 타임)"), texts)
        segs = [{"start": 10.0, "end": 13.0, "text": "아 근데 오늘 진짜 덥네요. 물 좀 마시고 할게요."}]
        sig = {"name": "x", "duration": 60.0, "tidy": [{"in": 0, "out": 60}], "junk": [], "onsets": [], "motion": [0.0] * 120, "motionStep": 0.5}
        asides = [m for m in msg.moments(sig, segs) if m["kind"] == "aside"]
        self.assertEqual([m["text"] for m in asides], ["(물 타임)"])


class WriterTest(TmpWork):
    LINES = [{"start": 0.0, "end": 3.0, "text": "안녕하세요. 오늘은 패스 연습이에요."}, {"start": 4.0, "end": 6.0, "text": "패스하고 왜 멈추면 안 될까요?"},
             {"start": 20.0, "end": 24.0, "text": "패스를 하는 순간 수비는 공을 따라가요."}, {"start": 40.0, "end": 43.0, "text": "두 번째 동작은 옆으로, 아니다."},
             {"start": 44.0, "end": 45.0, "text": "다시 할게요."}, {"start": 60.0, "end": 62.0, "text": "아이고, 공이 뒤로 갔네요."},
             {"start": 90.0, "end": 92.0, "text": "오늘 영상은 여기까지입니다. 감사합니다."}]
    MOMS = [M("question", 4.0, "패스하고 왜 멈추면 안 될까요?", 4.0, 6.0), M("fail", 60.5, "아이고,", 60.0, 62.0)]

    def fake(self, out):
        calls = []

        def run(prompt, timeout=None, cancel=None):
            calls.append(prompt)
            return {"text": json.dumps({"lines": out}, ensure_ascii=False), "model": "시험"}
        return run, calls

    def test_lines_are_checked_and_cached(self):
        cands = msgwrite.candidates(self.MOMS, self.LINES, junk=[(40.0, 45.0, "NG")])
        ids = {c["kind"]: c["id"] for c in cands}
        self.assertNotIn("아니다", " ".join(c["ctx"] for c in cands))          # 잘리는 NG 말은 앞뒤 말로도 안 보냄
        self.assertFalse(any("여기까지" in c["said"] for c in cands if c["kind"] == "talk"))  # 마무리 인사에는 안 물어봄
        out = [{"id": ids["question"], "kind": "inner", "t": "정답은 잠시 후에"}, {"id": ids["fail"], "kind": "inner", "t": "(외모는 못 속여)"},
               {"id": ids["fail"], "kind": "fx", "t": "괜찮아 다시 하면 돼 정말로 진짜!"}, {"id": ids["talk"], "kind": "fx", "t": "와!"},
               {"id": ids["talk"], "kind": "inner", "t": "(수비는 공을 따라가요)"}]
        run, calls = self.fake(out)
        cf = self.tmp / "ai.json"
        lines, talk = msgwrite.write(str(cf), self.MOMS, self.LINES, "패스", run=run, junk=[(40.0, 45.0, "NG")], log=lambda m: None)
        self.assertEqual(lines, {"question:4.00": {"kind": "inner", "text": "(정답은 잠시 후에)"}})  # 괄호로 · 무례·너무 김·종류 틀림·말 되풀이는 뺌
        self.assertEqual([m["kind"] for m in talk], ["talk"])
        lines2, _ = msgwrite.write(str(cf), self.MOMS, self.LINES, "패스", run=run, junk=[(40.0, 45.0, "NG")], log=lambda m: None)
        self.assertEqual((lines2, len(calls)), (lines, 1))  # 같은 입력은 다시 안 보냄
        with self.assertRaises(msgwrite.WriteError):
            msgwrite.parse("그냥 글", cands)

    def test_plan_uses_lines_and_build_falls_back_without_login(self):
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        moms = [M("punchline", 30.5, "제가 원래 잘 넘어지거든요.", 28.8, 30.6), M("talk", 80.0, "패스를 하는 순간 수비는 공을 따라가요.", 79.0, 82.0)]
        ai = {"punchline:30.50": {"kind": "inner", "text": "(이건 비밀)"}, "talk:80.00": {"kind": "inner", "text": "(여기 밑줄)"}}
        texts = [c.get("text") for c in plan(moms, st, "듬뿍", ai=ai)]
        self.assertIn("(이건 비밀)", texts)          # 정해 둔 '(머쓱)' 대신 클로드 글
        self.assertIn("(여기 밑줄)", texts)          # 재미 순간 없는 긴 설명 위 글
        w = MsgWork()
        try:
            logs = []
            with mock.patch("claude_cli.status", return_value={"state": "missing"}):
                r = msg.build_variants(w.name, [{"kind": "preset", "name": "예능 MSG형"}], "듬뿍", ("long",), logs.append, writer=True)
            self.assertTrue(r["sequences"])
            self.assertTrue(any("정해 둔 문구" in x for x in logs), logs)
        finally:
            w.close()


class RetryTest(unittest.TestCase):
    def test_reduce_keeps_style_signature_and_skips_noops(self):
        k = msg._knobs("듬뿍")
        steps = []
        have = {"montage": 1, "freeze": 2, "count": 0, "replay": 3, "teaser": 1}
        while msg._reduce(k, have, ("replay", "freeze", "montage")):
            steps.append(dict(k))
        self.assertTrue(steps)
        self.assertEqual(k["montage"], msg.MONTAGE_N["듬뿍"])   # 몽타주 남김
        self.assertEqual(k["freeze"], 1)                          # 정지 화면 하나는 남김
        self.assertEqual(k["replay"], 1)                          # 다시 보기 하나는 남김
        self.assertTrue(k["count"])                               # 숫자 세기가 없으면 그 단계는 건너뜀 (다시 만들어도 같음)

    def test_no_rebuild_inside_band_even_if_moments_were_dropped(self):
        calls = []
        seq = {"items": [], "msg": {"summary": {"kinds": {}}, "events": []}}

        def once(*a, **k):
            calls.append(1)
            return seq, [], 5, 4      # 사건 5개 (범위 안) · 빠진 순간 4
        with mock.patch.object(msg, "_compile_once", once), mock.patch.object(editor, "seq_total", return_value=60.0):
            msg.compile_seq("x", {}, {}, [], [], msg.resolve({"kind": "preset", "name": "예능 MSG형"}), "보통", "long", 1, "x")
        self.assertEqual(len(calls), 1)

    def test_signature_by_style(self):
        R = lambda n: msg.resolve({"kind": "preset", "name": n})  # noqa: E731
        self.assertEqual(msg._signature(R("다큐 감성형"), "듬뿍"), ("replay", "freeze"))
        self.assertEqual(msg._signature(R("예능 MSG형"), "듬뿍"), ("replay", "freeze", "montage"))
        self.assertEqual(msg._signature(R("예능 MSG형"), "담백"), ())


class ColdOpenTest(unittest.TestCase):
    """전체 시험에서 찾음: 짧은 원본에서 양 범위 때문에 티저 명장면을 다 뺀(0개) 티저 스타일은 본편에서 첫 질문을 빼 놓고 앞에 넣지 않아
    질문이 통째로 사라지고 그만큼 짧아져 엔드 화면까지 빠짐 → 첫 질문 장면으로 시작(콜드 오픈)."""

    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_teaser_style_without_highlights_opens_on_the_question(self):
        real = msg._knobs
        with mock.patch.object(msg, "_knobs", lambda k: dict(real(k), teaser=0)):
            r = msg.build_variants(self.w.name, [{"kind": "preset", "name": "예능 MSG형"}], "보통", ("long",), lambda m: None)
        q = r["sequences"][0]
        ev = q["msg"]["events"]
        tz = [e for e in ev if e["kind"] == "teaser"]
        self.assertEqual(len(tz), 1, [e["kind"] for e in ev])
        self.assertIn("첫 질문", tz[0]["why"])
        v1 = [it for it in q["items"] if it["track"] == "V1" and it["media"] == "main"]
        first = min(v1, key=lambda it: it["start"])
        self.assertLess(first["start"], 0.05)
        self.assertTrue(first["in"] <= 4.3 and 6.5 <= first["out"], first)   # 첫 장면이 첫 질문 (4.2~6.6초 '왜 다들 첫 터치에서 공을 놓칠까요?')
        self.assertTrue(any(e["kind"] == "end" for e in ev), [e["kind"] for e in ev])    # 엔드 화면도 남음


class FinalJudgeTest(unittest.TestCase):
    """round5 최종 판정 중간 결과에서 고침: 배경음악이 말보다 30dB 남짓 작아 '거의 안 들림' · 작은 제목과 같은 말 자막이 한꺼번에."""

    def test_music_bed_stays_audible_under_speech(self):
        for n, p in msg.PRESETS.items():
            self.assertTrue(-9.0 <= p["sound"]["duck"] <= -6.0, n)             # 말할 때 줄이는 양 (예전 -13~-14)
            self.assertTrue(-8.0 <= p["sound"]["bgmDb"] <= -4.0, n)            # 예전 -7~-10 (보정 전 말 크기 기준)
            self.assertGreaterEqual(-(p["sound"]["bgmDb"] + p["sound"]["duck"]), 11.0, n)   # 말할 때 보정 전 말보다 11dB 넘게 작게 → 보정한 말보다 약 21~23dB
        self.assertEqual(msg.BANDS["duck"], (-14.0, -6.0))
        self.assertEqual(msg.BANDS["bgmDb"], (-10.0, -4.0))

    def test_small_title_waits_for_the_line_that_says_it(self):
        B = msg._Build.__new__(msg._Build)
        B.main = [{"start": 3.0, "in": 10.0, "out": 40.0, "speed": 1.0, "track": "V1"}]
        caps = [{"start": 10.2, "end": 12.4, "text": "오늘은 슈팅 챌린지예요."}, {"start": 12.6, "end": 15.0, "text": "다섯 번 차 볼게요."}]
        s0, ln = msg._title_after_said(B, 3.0, 2.6, "슈팅 챌린지", caps, 30.0)
        self.assertAlmostEqual(s0, 3.0 + 2.38 + 0.1, places=2)        # 그 말 자막이 끝난 뒤
        self.assertEqual(ln, 2.6)
        self.assertEqual(msg._title_after_said(B, 3.0, 2.6, "퍼스트 터치", caps, 30.0), (3.0, 2.6))   # 다른 말이면 그대로
        long_caps = [{"start": 10.2, "end": 18.0, "text": "오늘은 슈팅 챌린지를 아주 길게 설명해요."}]
        self.assertEqual(msg._title_after_said(B, 3.0, 2.6, "슈팅 챌린지", long_caps, 30.0), (3.0, 2.6))  # 너무 늦어지면 그대로
        # 첫 질문 장면(0~2초, 본편 아님) 위 제목: 본편 첫 말이 제목과 같으면 그 말 전에 제목을 끝냄
        B.main = [{"start": 2.0, "in": 10.0, "out": 40.0, "speed": 1.0, "track": "V1"}]
        s0, ln = msg._title_after_said(B, 0.0, 2.6, "슈팅 챌린지", [{"start": 10.0, "end": 12.4, "text": "안녕하세요. 오늘은 슈팅 챌린지예요."}], 30.0)
        self.assertEqual(s0, 0.0)
        self.assertAlmostEqual(ln, 1.95, places=2)


class FinalJudgeLinesTest(unittest.TestCase):
    """round5 최종 판정 중간 결과: 쉬었다 잇는 문장 머리('사실 이건' … '발 기술 문제라기보다 …')를 끊긴 말로 보고 뺌 ·
    쉼으로 쪼개진 '어, … 다시 해볼게요.'(시범 예고)를 슬레이트 말로 보고 빼서 '해볼게요'가 사라짐."""

    def test_sentence_head_before_a_pause_is_not_a_broken_phrase(self):
        self.assertFalse(msg._fragment("사실 이건"))
        self.assertTrue(msg._fragment("오늘 진짜"))      # 진짜 끊긴 말은 그대로 (round2)
        segs = [{"start": 10.0, "end": 11.2, "text": "사실 이건", "words": [{"s": 10.0, "e": 10.4, "w": "사실"}, {"s": 10.4, "e": 11.2, "w": "이건"}]},
                {"start": 12.0, "end": 15.0, "text": "발 기술 문제라기보다 준비 자세 문제예요.",
                 "words": [{"s": 12.0 + 0.5 * k, "e": 12.4 + 0.5 * k, "w": w} for k, w in enumerate("발 기술 문제라기보다 준비 자세 문제예요.".split())]}]
        kept, junk = msg.clean_lines(segs)
        self.assertEqual(junk, [])
        self.assertEqual(len(kept), 2)

    def test_split_demo_call_before_a_ball_sound_stays(self):
        words = [(69.05, 69.62, "어,", 0), (70.38, 70.74, "다시", 1), (70.74, 71.62, "해볼게요.", 1), (77.14, 78.28, "와,", 2)]
        extra = [(69.05, 69.62, "슬레이트 말"), (70.49, 71.62, "슬레이트 말")]
        self.assertEqual(msg._demo_calls(extra, words, [60.5, 77.14], [75.37]), [(70.49, 71.62, "슬레이트 말")])   # '어,' 는 그대로 뺌
        self.assertEqual(msg._demo_calls(extra, words, [60.5, 72.5], [75.37]), [])     # 공 소리보다 말이 먼저 → 다시 찍기 신호
        self.assertEqual(msg._demo_calls(extra, words, [60.5, 77.14], []), [])


class FinalJudgeReplayTest(unittest.TestCase):
    """round5 최종 판정 중간 결과: 다시 보기가 시범 뒤 원본 장면 바뀜(스튜디오)까지 느리게 나와 '다시 보기 ▶'가 말하는 장면 위에 남음 ·
    딴소리 조각('자, 공 좀 가져올게요')을 짧은 조각으로 보고 빼서 '빠진 말'."""

    def test_replay_stays_inside_the_source_shot(self):
        self.assertEqual(msg._replay_span(31.65, 34.15, 32.85, [29.25, 33.25, 39.75]), (31.65, 33.2))   # 33.25 장면 바뀜 앞에서 끝
        self.assertEqual(msg._replay_span(31.65, 34.15, 32.85, [32.7]), (31.65, 34.15))                 # 공 차는 순간 0.3초 안 → 그대로
        self.assertEqual(msg._replay_span(30.0, 32.5, 31.8, [30.4]), (30.45, 32.5))                     # 앞쪽 장면 바뀜 뒤에서 시작
        self.assertEqual(msg._replay_span(31.65, 33.3, 32.85, [32.4]), (31.65, 33.3))                   # 남는 길이가 1초보다 짧아지면 그대로

    def test_aside_piece_is_kept(self):
        cuts = [{"in": 30.0, "out": 42.17}, {"in": 44.8, "out": 46.11}, {"in": 50.11, "out": 80.0}]
        self.assertEqual(len(msg.drop_slivers(cuts, [{"kind": "aside", "t": 45.66}])), 3)
        self.assertEqual(len(msg.drop_slivers(cuts, [])), 2)   # 재미 순간이 없는 짧은 조각은 그대로 뺌


class FinalJudgeAlignTest(unittest.TestCase):
    """round5 최종 판정 중간 결과: '아, 아깝다.'를 받아쓰기가 '아,'(긴 소리) + '아깝다.'(다음 소리 앞 0.05초)로 적어 실패 반응이 추임새와 함께 잘림 ·
    '다시 한번 해볼게요.' 뒤에 공 소리 없이 말 없는 시범이 바로 오면 시범 예고."""

    def test_collapsed_word_after_interjection_takes_the_long_blob(self):
        segs = [{"start": 15.8, "end": 19.32, "text": "아, 아깝다. 골대 맞았어요.",
                 "words": [{"s": 15.8, "e": 16.96, "w": "아,"}, {"s": 18.52, "e": 18.57, "w": "아깝다."}, {"s": 18.57, "e": 18.78, "w": "골대"},
                           {"s": 18.78, "e": 19.32, "w": "맞았어요."}]}]
        blobs = [[14.29, 14.78], [16.8, 17.84], [18.52, 19.41]]
        out, n = msg.align_to_sound(segs, blobs)
        ws = out[0]["words"]
        self.assertGreaterEqual(n, 1)
        self.assertTrue(16.9 <= ws[1]["s"] <= 17.3 and ws[1]["e"] >= 17.8, ws[1])     # '아깝다' 는 '아' 소리 덩어리 뒷부분
        self.assertLessEqual(ws[0]["e"], ws[1]["s"])                                  # '아,' 는 그 앞까지 (추임새로 잘려도 '아깝다'는 남음)
        self.assertAlmostEqual(ws[2]["s"], 18.57, places=2)                           # 나머지는 그대로
        self.assertGreaterEqual(msg.ALIGN_VER, 4)
        # 추임새 덩어리가 짧으면(진짜 '아' 하나) 그대로
        short = [[16.8, 17.2], [18.52, 19.41]]
        out2, _ = msg.align_to_sound(segs, short)
        self.assertGreaterEqual(out2[0]["words"][1]["s"], 18.4)

    def test_word_in_silence_takes_the_blob_right_after(self):
        """S1 '발을 살짝 뒤로': 받아쓰기는 '살짝'을 소리 덩어리 0.03초 앞에서 시작 — 예전에는 그 덩어리를 건너뛰고 다음 덩어리로
        옮겨 '발을 살짝' 소리를 낱말 없는 추임새로 잘랐음 (판정 round5 최종: '살짝' 빠짐)."""
        segs = [{"start": 144.18, "end": 147.28, "text": "공이 튀어나가요.",
                 "words": [{"s": 146.24, "e": 146.64, "w": "공이"}, {"s": 146.64, "e": 147.28, "w": "튀어나가요."}]},
                {"start": 148.84, "end": 152.1, "text": "살짝 뒤로 빼주면서 방향만 바꿔주세요.",
                 "words": [{"s": 148.84, "e": 149.2, "w": "살짝"}, {"s": 149.2, "e": 149.68, "w": "뒤로"}, {"s": 149.68, "e": 150.44, "w": "빼주면서"},
                           {"s": 150.44, "e": 151.48, "w": "방향만"}, {"s": 151.48, "e": 152.1, "w": "바꿔주세요."}]}]
        blobs = [[145.98, 147.37], [148.87, 149.31], [149.47, 150.59], [151.01, 152.2]]
        out, _ = msg.align_to_sound(segs, blobs)
        ws = out[1]["words"]
        self.assertTrue(148.85 <= ws[0]["s"] <= 148.9, ws[0])          # 바로 뒤 덩어리 시작으로 (예전 149.47)
        self.assertLess(ws[0]["s"], 149.31)
        self.assertGreaterEqual(msg.ALIGN_VER, 5)
        fl = msg.blob_fillers(blobs, msg._words(out))
        self.assertFalse(any(a < 149.2 and b > 148.9 for a, b, *_ in fl), fl)   # 그 덩어리는 추임새로 자르지 않음

    def test_fresh_transcript_keeps_reaction_inside_interjection_blob(self):
        """처음 받아쓴 영상(맞춘 적 없음): '아,' 15.8~16.96 · '아깝다.' 17.16~17.74 · 소리 16.8~17.84 — 예전에는 '아깝다'를 다음 덩어리
        (18.52)로 옮겨 '아 아깝다' 소리를 추임새로 잘랐고, 두 번째 맞추기(ALIGN_VER 4)에서만 되돌아왔음 (한 번에 맞아야 함)."""
        segs = [{"start": 15.8, "end": 19.32, "text": "아, 아깝다. 골대 맞았어요.",
                 "words": [{"s": 15.8, "e": 16.96, "w": "아,"}, {"s": 17.16, "e": 17.74, "w": "아깝다."}, {"s": 18.46, "e": 18.78, "w": "골대"},
                           {"s": 18.78, "e": 19.32, "w": "맞았어요."}]}]
        blobs = [[14.29, 14.78], [16.8, 17.84], [18.52, 19.41], [19.92, 20.38]]
        out, _ = msg.align_to_sound(segs, blobs)
        ws = out[0]["words"]
        self.assertTrue(17.0 <= ws[1]["s"] <= 17.2 and ws[1]["e"] >= 17.7, ws[1])
        self.assertGreater(ws[0]["e"], ws[1]["s"] - 0.05)                 # '아,'는 반응에 붙음 → 홀로 떨어진 추임새 컷이 '아깝다' 첫소리를 자르지 않음
        import takes
        self.assertFalse([f for f in takes.find_fillers(out) if f[1] > 16.8], takes.find_fillers(out))
        again, _ = msg.align_to_sound(out, blobs)                         # 두 번 맞춰도 그대로
        self.assertAlmostEqual(again[0]["words"][1]["s"], ws[1]["s"], places=2)
        # 진짜 추임새 소리에 붙여 적은 낱말(덩어리 밖으로 이어짐)은 여전히 다음 덩어리로
        segs2 = [{"start": 9.2, "end": 10.8, "text": "음 패스는", "words": [{"s": 9.2, "e": 9.5, "w": "음"}, {"s": 9.55, "e": 10.8, "w": "패스는"}]}]
        out2, _ = msg.align_to_sound(segs2, [[9.2, 9.7], [10.0, 10.9]])
        self.assertAlmostEqual(out2[0]["words"][1]["s"], 10.0, places=2)

    def test_last_word_moved_into_sound_keeps_its_tail(self):
        segs = [{"start": 199.16, "end": 199.88, "text": "감사합니다.", "words": [{"s": 199.16, "e": 199.88, "w": "감사합니다."}]}]
        out, _ = msg.align_to_sound(segs, [[197.1, 198.9], [199.61, 200.26]])
        w = out[0]["words"][0]
        self.assertAlmostEqual(w["s"], 199.61, places=2)
        self.assertAlmostEqual(w["e"], 200.26, places=2)                  # 예전 199.91 (끝 0.35초가 컷에서 잘림)

    def test_demo_call_before_a_silent_demo(self):
        self.assertTrue(msg._demo_call_ok(166.84, [173.53], [], [[166.94, 171.54]]))     # 공 소리를 못 잡아도 시범이 바로 뒤
        self.assertFalse(msg._demo_call_ok(166.84, [167.5], [], [[168.0, 171.54]]))      # 다음 말이 먼저 → 다시 찍기 신호
        self.assertFalse(msg._demo_call_ok(166.84, [173.53], [], [[170.0, 171.54]]))     # 시범이 2초 넘게 뒤


def _long_fixture(work, reps=16):
    """make_msg_fixture 원본을 reps 번 이어 붙인 약 9분 원본 (받아쓰기 시각도 옮김)."""
    name, truth = mf.make_msg_fixture(work)
    src = work / "videos" / name
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{src.as_posix()}'\n" for _ in range(reps)), encoding="utf-8")
    long_name = "20261008_MSGLONG_긴 레슨 [시험].mp4"
    r = core.run([core.ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(work / "videos" / long_name)])
    assert r.returncode == 0, r.stderr
    a0 = work / "analysis" / Path(name).stem
    a1 = work / "analysis" / Path(long_name).stem
    a1.mkdir(parents=True, exist_ok=True)
    segs = json.loads((a0 / "transcript.json").read_text(encoding="utf-8"))
    out = []
    for k in range(reps):
        off = k * mf.DUR
        for s in segs:
            out.append(dict(s, start=round(s["start"] + off, 3), end=round(s["end"] + off, 3),
                            words=[dict(w, s=round(w["s"] + off, 3), e=round(w["e"] + off, 3)) for w in s["words"]]))
    (a1 / "transcript.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(a0 / "asr.json", a1 / "asr.json")
    an = json.loads((a0 / "analysis.json").read_text(encoding="utf-8"))
    an = {"silences": [dict(x, start=x["start"] + k * mf.DUR, end=x["end"] + k * mf.DUR) for k in range(reps) for x in an["silences"]],
          "loud_peaks": [dict(x, time=x["time"] + k * mf.DUR) for k in range(reps) for x in an["loud_peaks"]]}
    (a1 / "analysis.json").write_text(json.dumps(an), encoding="utf-8")
    return long_name, reps * mf.DUR


class LongSourceTest(unittest.TestCase):
    """검토: 긴 원본에서 '빠진 재미 순간'까지 다시 만들기로 줄여 다시 보기·정지·몽타주가 모두 빠지고 스타일끼리 같아짐 ·
    60분 레슨 중간의 '감사합니다'에 몽타주·'마무리' 챕터 여러 개."""

    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        cls.name, cls.dur = _long_fixture(cls.w.work)
        reps = int(cls.dur / mf.DUR)

        def tags(path, wave_, name):
            k = int(cls.dur / 0.48) + 1
            lg = [0.0] * k
            for r in range(reps):
                for x in cls.w.truth["laughs"]:
                    for i in range(int((x["a"] + r * mf.DUR) / 0.48), min(k, int((x["b"] + r * mf.DUR) / 0.48) + 1)):
                        lg[i] = 0.8
            return {"laugh": lg, "cheer": [0.0] * k, "music": [0.0] * k, "speech": [0.0] * k}
        cls.tp = mock.patch.object(msg, "_tags", tags)
        cls.tp.start()
        specs = [{"kind": "preset", "name": n} for n in ("예능 MSG형", "쇼츠 하이텐션형", "다큐 감성형")]
        cls.res = {k: msg.build_variants(cls.name, specs, k, ("long",), lambda m: None) for k in ("보통", "듬뿍")}

    @classmethod
    def tearDownClass(cls):
        cls.tp.stop()
        cls.w.close()

    def kinds(self, q):
        return q["msg"]["summary"]["kinds"]

    def test_style_signature_survives_on_long_source(self):
        for k, r in self.res.items():
            for q in r["sequences"]:
                self.assertGreaterEqual(self.kinds(q).get("replay", 0), 1, q["name"])
            dq = next(q for q in r["sequences"] if "다큐" in q["name"])
            self.assertGreaterEqual(self.kinds(dq).get("freeze", 0), 1, dq["name"])
        yq = next(q for q in self.res["듬뿍"]["sequences"] if "예능" in q["name"])
        self.assertGreaterEqual(self.kinds(yq).get("montage", 0) + self.kinds(yq).get("freeze", 0), 2, self.kinds(yq))

    def test_styles_differ_in_structure(self):
        for k, r in self.res.items():
            sigs = []
            for q in r["sequences"]:
                cuts = tuple(sorted(round(float(x["in"]), 1) for x in q["items"] if x["track"] == "V1" and x["media"] == "main"))
                kinds = tuple(sorted((e["kind"], round(e["t"])) for e in q["msg"]["events"] if e["kind"] not in ("bgm", "chapters")))
                sigs.append((cuts, kinds))
            for i in range(len(sigs)):
                for j in range(i + 1, len(sigs)):
                    a, b = set(sigs[i][0]), set(sigs[j][0])
                    self.assertLess(len(a & b) / max(1, len(a | b)), 0.95, (k, i, j))   # 컷이 같지 않음 (컷 리듬·티저)

    def test_jokes_per_minute_on_long_source(self):
        q = next(q for q in self.res["듬뿍"]["sequences"] if "예능" in q["name"])
        jokes = [e for e in q["msg"]["events"] if e["kind"] in ("inner", "fx", "situ", "emphasis")]
        zooms = [e for e in q["msg"]["events"] if e["kind"] == "punch" and e.get("why") == msg.STILL_WHY]
        mins = editor.seq_total(q) / 60.0
        self.assertGreaterEqual(len(jokes) / mins, 3.0, (len(jokes), mins))
        self.assertLess(len(zooms), len(jokes), (len(zooms), len(jokes)))

    def test_text_does_not_run_into_an_inserted_scene(self):
        """판정 round5 최종: 앞 장면의 '나이스!!'가 다시 보기 장면 위까지 남음."""
        n = 0
        for r in self.res.values():
            for q in r["sequences"]:
                ins = [float(e["ins"]["start"]) for e in q["msg"]["events"] if e.get("ins") and e["kind"] in ("replay", "freeze", "montage")]
                ids = {i for e in q["msg"]["events"] if e["kind"] in ("inner", "fx", "situ", "emphasis") for i in (e.get("refs") or {}).get("titles", [])}
                for t in q["titles"]:
                    if t["id"] in ids:
                        n += 1
                        for s0 in ins:
                            self.assertFalse(t["start"] < s0 - 0.03 and t["start"] + t["dur"] > s0 + 0.03, (q["name"], t["text"], t["start"], s0))
        self.assertGreater(n, 10)

    def test_teaser_hook_text_only_over_highlights(self):
        """판정 round5 최종: 훅 글자가 티저 끝 질문 장면(감독님이 다른 말부터 함)까지 남아 말과 자막이 다름 → 질문 장면은 말 자막."""
        seen = 0
        for r in self.res.values():
            for q in r["sequences"]:
                for e in q["msg"]["events"]:
                    if e["kind"] == "teaser" and "미리 보기 + 첫 질문" in (e.get("why") or "") and e.get("ins"):
                        seen += 1
                        hk = next((t for t in q["titles"] if t["id"] in e["refs"]["titles"]), None)
                        self.assertIsNotNone(hk, (q["name"], e, [(t["text"], t["start"]) for t in q["titles"] if t["start"] < 12]))
                        qv = [i for i in q["items"] if i["id"] in e["refs"]["items"] and i["track"] == "V1"][-1]
                        self.assertLessEqual(hk["start"] + hk["dur"], qv["start"] + 0.05, q["name"])
                        self.assertFalse(qv.get("noCaps"), q["name"])
        self.assertGreater(seen, 0)

    def test_single_final_closing(self):
        q = next(q for q in self.res["듬뿍"]["sequences"] if "예능" in q["name"])
        mont = [e for e in q["msg"]["events"] if e["kind"] == "montage"]
        self.assertTrue(all(e["t"] > editor.seq_total(q) * 0.8 for e in mont), [e["t"] for e in mont])
        self.assertLessEqual(sum(1 for m in q.get("markers") or [] if m["name"] == "마무리"), 1)
        self.assertEqual(msg._final_closings([M("closing", 1800.0), M("closing", 3590.0)], 3600.0), [3590.0])


class CancelAndInputTest(TmpWork):
    def test_cancel_during_last_candidate_returns_nothing(self):
        w = MsgWork()
        try:
            real = msg._compile_once
            n = []

            def once(*a, **k):
                n.append(1)
                if len(n) == 2:
                    editor.CANCEL.set()   # 두 번째(마지막) 후보를 만드는 중에 ✕
                return real(*a, **k)
            with mock.patch.object(msg, "_compile_once", once):
                with self.assertRaises(msg.MsgError):
                    msg.build_variants(w.name, [{"kind": "preset", "name": "담백 레슨형"}, {"kind": "preset", "name": "다큐 감성형"}], "보통",
                                       ("long",), lambda m: None)
        finally:
            editor.CANCEL.clear()
            w.close()

    def test_unusable_inputs_get_a_plain_message(self):
        segs = [{"start": 1.0, "end": 3.0, "text": "시청해 주셔서 감사합니다.", "words": []}]
        with self.assertRaises(msg.MsgError) as e:
            msg._check_input({"duration": 8.0}, segs, {})
        self.assertIn("짧아요", str(e.exception))
        with self.assertRaises(msg.MsgError) as e:
            msg._check_input({"duration": 100.0}, segs, {})
        self.assertIn("말소리", str(e.exception))
        many = [{"start": float(i), "end": i + 0.9, "text": "음악 위에 들린 말 같은 소리", "words": []} for i in range(4)]
        with self.assertRaises(msg.MsgError):
            msg._check_input({"duration": 100.0}, many, {"tags": {"speech": [0.0] * 200}, "tagStep": 0.48})
        msg._check_input({"duration": 100.0}, many * 2, {"tags": {"speech": [0.9] * 200}, "tagStep": 0.48})   # 말이 들리면 괜찮음

    def test_tiny_shorts_is_not_offered(self):
        w = MsgWork()
        try:
            with mock.patch.object(msg, "_shorts_window", lambda rec, moms, dur, demo=(): [{"in": 10.0, "out": 14.0}]):
                logs = []
                r = msg.build_variants(w.name, [{"kind": "preset", "name": "예능 MSG형"}], "보통", ("long", "shorts"), logs.append)
            self.assertEqual([q["format"] for q in r["sequences"]], ["long"])
            self.assertTrue(any("쇼츠 후보는 만들지 않았어요" in x for x in logs), logs)
        finally:
            w.close()


def _stereo(path, x, sr=48000):
    import numpy as np
    y = (np.clip(np.stack([x, x * 0.9], axis=1), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(y.tobytes())


class LevelsTest(TmpWork):
    def test_one_streaming_pass_measures_like_before(self):
        import numpy as np
        sr = 48000
        t = np.arange(sr * 12) / sr
        x = np.zeros_like(t)
        talk = [(1.0, 4.0), (6.0, 9.0)]
        for a, b in talk:
            m = (t >= a) & (t < b)
            x[m] = 0.2 * np.sin(2 * np.pi * 200 * t[m])
        x[int(10.5 * sr):int(10.52 * sr)] = 0.9   # 공 소리
        f = self.tmp / "말 소리.wav"
        _stereo(f, x)
        segs = [{"start": a, "end": b} for a, b in talk]
        words = [(a, b, "말", 0) for a, b in talk]
        r = msg._levels_pass(f, segs, words)
        self.assertAlmostEqual(r["speechPk"], 20 * math.log10(0.2), delta=0.3)
        self.assertAlmostEqual(r["dialogDb"], 20 * math.log10(0.2 / math.sqrt(2)) + 10 * math.log10((1 + 0.81) / 2), delta=0.5)
        self.assertGreater(r["peakDb"], r["speechPk"])
        self.assertIsNotNone(r["voicePk"])
        with mock.patch("numpy.frombuffer", side_effect=MemoryError):   # 메모리가 모자라면 None (흑백 소리로 잰 값으로)
            self.assertIsNone(msg._levels_pass(f, segs, words))
        editor.CANCEL.set()
        with self.assertRaises(msg.MsgError):
            msg._levels_pass(f, segs, words)

    def test_onsets_vectorized_baseline(self):
        import numpy as np
        rng = np.random.default_rng(3)
        w = (rng.standard_normal(16000 * 6) * 300).astype(np.int16)
        w[16000 * 3:16000 * 3 + 300] = 20000
        self.assertEqual(msg._onsets(w, []), [3.0])

    def test_quiet_speech_keeps_effects_under_it(self):
        lv = msg._sfx_level({"voicePk": -45.0}, "휙", "replay", -3.0)
        self.assertLess(lv - 3.0, -45.0)    # 효과음 최대(파일 -3 dBFS + 레벨)가 말소리보다 작음 (예전 -30 바닥에서는 말보다 큼)


class ReplayPlaceTest(unittest.TestCase):
    def test_short_line_inside_demo_is_part_of_it(self):
        lines = [{"start": 24.75, "end": 24.95, "text": "툭."}, {"start": 26.0, "end": 27.5, "text": "좋아요. 이렇게요."}]
        self.assertGreaterEqual(msg._after_reaction(lines, 25.0), 25.05)

    def test_next_tip_after_demo(self):
        lines = [{"start": 25.4, "end": 27.0, "text": "슈팅할 때는 디딤발이 핵심이에요."}]
        at = msg._after_reaction(lines, 25.0)
        self.assertTrue(25.05 <= at <= 25.35, at)


class BgmTest(TmpWork):
    def test_variants_change_tune_not_key(self):
        a0 = sfxlib.bgm_render("경쾌", 5, bars=2)
        self.assertTrue((a0 == sfxlib.bgm_render("경쾌", 5, bars=2, variant=0)).all())   # 0 은 예전 그대로
        a1 = sfxlib.bgm_render("경쾌", 5, bars=2, variant=1)
        self.assertEqual(a0.shape, a1.shape)
        self.assertFalse((a0 == a1).all())
        self.assertEqual(sfxlib.bgm_file_name("경쾌", 5, 1), "배경음악_경쾌_5_1.wav")

    def _section_items(self, ln=200.0):
        B = msg._Build("x.mp4", {"duration": 300.0}, "long")
        B.clip(0.0, ln, main=True)
        sig = {"sig": [1, 2], "dialogDb": -24.0, "voicePk": -10.0}
        snd = dict(msg.PRESETS["예능 MSG형"]["sound"])
        msg._audio_items(B, sig, [(0.0, ln, "경쾌")], 7, snd, "보통")
        return [it for it in B.items if it["track"] in ("A3", "A4")], B

    def test_long_section_cycles_arrangements(self):
        items, B = self._section_items()
        files = [B.media[it["media"]]["file"] for it in items]
        self.assertGreater(len(items), 3)
        self.assertGreaterEqual(len(set(files)), 2, files)
        self.assertTrue(all(b != a for a, b in zip(files, files[1:])), files)  # 같은 묶음이 바로 이어 되풀이되지 않음

    def test_my_music_folder_is_used(self):
        import numpy as np
        d = msg.user_bgm_dir() / "경쾌"
        d.mkdir(parents=True)
        t = np.arange(48000 * 20) / 48000
        _stereo(d / "내 곡.wav", 0.1 * np.sin(2 * np.pi * 330 * t))
        items, B = self._section_items(60.0)
        files = {B.media[it["media"]]["file"] for it in items}
        self.assertTrue(files and all(f.startswith("내배경음악_") for f in files), files)
        lv = items[0]["fx"]["level"]["v"]
        self.assertAlmostEqual(lv, -24.0 + msg.PRESETS["예능 MSG형"]["sound"]["bgmDb"] - 20 * math.log10(0.1 / math.sqrt(2)), delta=1.0)


class FileNameTest(TmpWork):
    def _video(self, name, audio=True, dur=3.0):
        a = ["-f", "lavfi", "-i", f"sine=f=300:d={dur}"] if audio else []
        r = core.run([core.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s=320x180:r=30:d={dur}", *a, "-c:v", "libx264", "-preset", "ultrafast",
                      "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-shortest"] if audio else []), str(self.work / "videos" / name)])
        self.assertEqual(r.returncode, 0, r.stderr)
        return name

    def test_freeze_png_name_is_safe(self):
        nfd = unicodedata.normalize("NFD", "20261008_X_%[풋살 꿀팁] 슈팅 100%d 성공 패스 앤 무브 드릴.mp4")
        self._video(nfd)
        e = editor.freeze_frame("videos", nfd, 1.0)
        self.assertNotIn("%", e["file"])
        self.assertEqual(e["file"], unicodedata.normalize("NFC", e["file"]))
        self.assertEqual(msg._title_of(nfd), "%[풋살 꿀팁] 슈팅 100%d 성공 패스 앤 무브 드릴")   # 풀어 쓴 한글도 제목 낱말을 찾게 (NFC)

    def _export(self, name, items, media, trackset=None, **kw):
        info = editor.media_info(name)
        proj = {"name": "시험", "format": "long", "v": 2, "captionStyle": dict(editor.LONG_STYLE), "items": items, "titles": [], "shapes": [], "trans": [], "markers": [], "captions": [],
                "captionsOn": False, "tracks": trackset or editor.default_tracks(), "info": info, "source": name, "media": media,
                "master": {"volume": 1.0, "normalize": True, "lufs": -14.0}}
        proj.update(kw)
        logs = []
        outs = editor.export(name, proj, {"preset": "youtube", "hw": False, "xml": False, "srt": False}, logs.append)
        return outs, logs

    def test_still_with_percent_and_raw_without_audio_export(self):
        name = self._video("20261008_화면녹화 %d 시험.mp4", audio=False)
        png = editor.freeze_frame("videos", name, 1.0)
        bad = editor.ASSETS / "정지_100%d_시험.png"   # 사용자가 가져온 사진 이름에도 '%d'
        shutil.copy2(editor.ASSETS / png["file"], bad)
        v, a = editor._pair(0.0, 2.0, start=0.0)
        img = {"id": editor._nid(), "track": "V1", "media": "img", "start": 2.0, "in": 0.0, "out": 1.0, "speed": 1.0, "rev": False, "link": None,
               "reframe": 0.5, "fit": "auto", "color": {}, "fx": {}}
        media = [{**editor.media_entry(bad.name, "assets", "img")}]
        outs, logs = self._export(name, [v, a, img], media)
        self.assertTrue((core.OUT / outs[0]).exists(), logs)

    def test_effects_are_added_without_a_full_length_bus(self):
        import numpy as np
        name = self._video("20261008_효과음 시험.mp4", dur=4.0)
        f = editor.ASSETS / "효과음_시험.wav"
        t = np.arange(4800) / 48000
        _stereo(f, 0.5 * np.sin(2 * np.pi * 880 * t))
        tracks = editor.default_tracks()
        for tr in tracks:
            if tr["id"] == "A2":
                tr.update(voiceFx=False)
        v, a = editor._pair(0.0, 4.0, start=0.0)
        fx = {"id": editor._nid(), "track": "A2", "media": "fx", "start": 1.0, "in": 0.0, "out": 0.1, "speed": 1.0, "rev": False, "link": None, "fx": {},
              "gain": 0.0, "fadeIn": 0.0, "fadeOut": 0.0, "mute": False}
        seq = {"items": [v, a, fx], "tracks": tracks, "voice": {"hp": True, "comp": True}, "duck": {"on": False}}
        media = {"main": {"id": "main", "kind": "video", "src": "videos", "file": name, "dur": 4.0, "audio": True}, "fx": editor.media_entry(f.name, "assets", "fx")}
        tmp = self.tmp / "섞기"
        tmp.mkdir()
        out, _ = editor._mix_audio_impl(seq, media, 0.0, 4.0, tmp, [], lambda x: None, None)
        self.assertFalse((tmp / "fx.f32").exists())
        y = np.fromfile(out, np.float32).reshape(-1, 2)
        self.assertGreater(np.abs(y[48000:48000 + 4800]).max(), np.abs(y[2 * 48000:2 * 48000 + 4800]).max() * 1.2)

    def test_not_enough_disk_space_says_so_before_rendering(self):
        name = self._video("20261008_디스크 시험.mp4")
        v, a = editor._pair(0.0, 3.0, start=0.0)
        Usage = type("U", (), {"free": 1000, "total": 10 ** 9, "used": 0})
        with mock.patch("shutil.disk_usage", return_value=Usage()):
            with self.assertRaises(RuntimeError) as e:
                self._export(name, [v, a], [])
        self.assertIn("디스크 공간이 모자라요", str(e.exception))
        self.assertGreater(editor._disk_need(3000.0, 1920, 1080, {"tracks": editor.default_tracks(), "voice": {"hp": True}}), 3e9)

    def test_failed_peak_retry_keeps_first_normalised_audio(self):
        name = self._video("20261008_최대 크기 시험.mp4", dur=4.0)
        v, a = editor._pair(0.0, 4.0, start=0.0)
        real = editor._run_ff
        seen = []

        def ff(args, *aa, **kw):
            if "audio.m4a" in args and any("loudnorm" in str(x) for x in args):
                seen.append(1)
                if len(seen) == 2:
                    raise RuntimeError("다시 만들기 실패 흉내")
            return real(args, *aa, **kw)
        with mock.patch.object(editor, "_run_ff", ff), mock.patch.object(editor, "_true_peak", return_value=0.0):
            outs, logs = self._export(name, [v, a], [])
        self.assertEqual(len(seen), 2)
        self.assertTrue(any("처음 맞춘 소리" in x for x in logs), logs)
        r = core.run([core.ffmpeg(), "-v", "info", "-nostats", "-i", str(core.OUT / outs[0]), "-af", "ebur128", "-f", "null", "-"])
        lufs = float(r.stderr.rsplit("I:", 1)[1].split("LUFS")[0])
        self.assertAlmostEqual(lufs, -14.0, delta=1.5)   # 소리 크기를 맞춘 첫 소리 (안 맞춘 소리로 덮어쓰지 않음)


class StateRouteTest(TmpWork):
    def test_big_result_is_sent_only_when_asked(self):
        import app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = srv.server_address[1]
        ps = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log")]
        for x in ps:
            x.start()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            big = {"sequences": [{"x": "가" * 100000}]}
            self.assertTrue(app.start_job("큰 결과 시험", lambda: big))
            for _ in range(50):
                if not app.JOB["name"]:
                    break
                time.sleep(0.05)
            get = lambda q: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state?since=999999{q}", timeout=20).read())  # noqa: E731
            s = get("")
            self.assertIsNone(s["result"])
            self.assertTrue(s["resultBig"])
            self.assertEqual(get("&result=1")["result"], big)
            self.assertTrue(app.start_job("작은 결과 시험", lambda: ["a.mp4"]))
            for _ in range(50):
                if not app.JOB["name"]:
                    break
                time.sleep(0.05)
            self.assertEqual(get("")["result"], ["a.mp4"])   # 작은 결과(내보내기 파일 이름)는 예전처럼
        finally:
            srv.shutdown()
            srv.server_close()
            for x in ps:
                x.stop()


if __name__ == "__main__":
    unittest.main()


class FinalJudge2Test(unittest.TestCase):
    """round5 최종 판정(b169a15) 지적: '다시 보기 ▶'가 감독님이 말하는 장면(물 마시기) 위 · 앞 장면 글자가 다시 보기 위에 남음 ·
    속마음이 너무 작고 흐림 · '포인트 ③'이 뭉개짐."""

    def _sig(self, cuts):
        motion = [0.1] * 120
        for i in range(64, 70):   # 32~35초: 움직임만 (공 소리 없음)
            motion[i] = 3.0
        return {"name": "x", "duration": 60.0, "tidy": [{"in": 0.0, "out": 60.0}], "junk": [], "motion": motion, "motionStep": 0.5,
                "onsets": [], "cuts": cuts}

    def test_motion_only_gap_in_the_talking_shot_is_not_a_play(self):
        segs = [{"start": 26.0, "end": 31.0, "text": "아 근데 오늘 진짜 덥네요. 물 좀 마시고 할게요.",
                 "words": [{"w": "덥네요.", "s": 26.0, "e": 31.0}]},
                {"start": 37.0, "end": 40.0, "text": "세 번째 포인트로 갈게요.", "words": [{"w": "포인트로", "s": 37.0, "e": 40.0}]}]
        plays = [m for m in msg.moments(self._sig([20.0, 45.0]), segs) if m["kind"] == "play"]
        self.assertEqual(plays, [])                                  # 같은 장면(바뀜 없음) 안의 몸짓 = 시범 아님
        plays = [m for m in msg.moments(self._sig([31.5, 36.5]), segs) if m["kind"] == "play"]
        self.assertEqual(len(plays), 1)                              # 장면이 바뀐 곳(다른 영상)이면 시범

    def test_inner_text_stays_long_enough(self):
        self.assertGreaterEqual(msg.INNER_MIN, 2.0)
        lk = msg.text_looks(msg.PRESETS["예능 MSG형"], "long", 0.86)["inner"]
        self.assertEqual(lk["effect"], "pop")
        self.assertGreaterEqual(lk["size"], 70)

    def test_praise_emphasis_is_not_swapped_for_a_term(self):
        caps = [{"start": 60.5, "end": 62.0, "text": "완벽해요."}]
        self.assertIsNone(msg._dedupe_text("emphasis", "완벽!", 61.1, 1.2, caps, (), "패스"))      # 예전 '패스 체크!'
        caps2 = [{"start": 60.5, "end": 62.0, "text": "디딤발이 중요해요."}]
        self.assertIsNone(msg._dedupe_text("emphasis", "중요!", 61.1, 1.2, caps2, (), "패스"))      # 예전 '패스 체크!' (지금 말과 안 맞음)

    def test_writer_context_stops_at_the_moment(self):
        import msgwrite
        lines = [{"start": 80.0, "end": 82.0, "text": "하나, 둘, 셋 리듬으로 패스해요."},
                 {"start": 89.0, "end": 91.0, "text": "받는 사람 발 쪽으로 정확하게 패스해 주세요."}]
        moms = [{"kind": "aside", "a": 80.0, "b": 82.0, "t": 81.0, "score": 1.0, "text": "", "why": ""}]
        c = msgwrite.candidates(moms, lines)
        self.assertNotIn("발 쪽으로", c[0]["ctx"])                  # 7초 뒤 말에 맞춘 글이 먼저 뜨지 않게
        self.assertGreaterEqual(msgwrite.VER, 4)
        self.assertIsNone(msgwrite.clean("inner", "(진심 어린 조언)"))

    def test_filler_cut_leaves_lead_before_next_word(self):
        blobs = [[130.0, 134.5], [136.56, 137.09], [137.67, 138.7]]
        words = [(130.0, 134.5, "받아요.", 0), (136.56, 137.09, "어…", 1), (137.67, 137.98, "세번째", 1)]
        f = msg.blob_fillers(blobs, words)
        self.assertEqual(len(f), 1)
        self.assertAlmostEqual(f[0][1], 137.67 - msg.SNAP_PRE, places=2)   # 예전 0.06초 앞 → 'ㅅ' 첫소리가 잘림

    def test_shorts_starting_with_a_reaction_keeps_the_demo_before(self):
        rec = {"shorts": [{"start": 30.0, "end": 70.0, "cuts": [{"in": 30.0, "out": 70.0}]}]}
        w = msg._shorts_window(rec, [], 120.0, [[23.5, 28.6], [40.0, 45.0]])
        self.assertAlmostEqual(w[0]["in"], 23.5)                       # '아이고, 공이 조금 뒤로 갔네요' 앞의 시범부터
        w2 = msg._shorts_window(rec, [], 120.0, [[10.0, 15.0]])
        self.assertAlmostEqual(w2[0]["in"], 30.0)                      # 멀리 떨어진 시범은 안 붙임

    def test_recap_list_split_by_a_pause_keeps_every_item(self):
        lines = [{"start": 180.3, "end": 182.4, "text": "오늘 배운 거 정리해볼게요."},
                 {"start": 183.3, "end": 185.7, "text": "고개 들기, 디딤발 거리, 그리고"},
                 {"start": 186.2, "end": 187.6, "text": "다음 방향으로 받기."},
                 {"start": 188.0, "end": 191.0, "text": "이 세 가지만 바꿔도 첫 터치가 완전히 달라져요."}]
        r = msg._recap_items(lines, 0)
        self.assertEqual(r[0], "① 고개 들기\n② 디딤발 거리\n③ 다음 방향으로 받기")       # 예전 ①② 둘만
        one = [{"start": 0.0, "end": 9.0, "text": "오늘 배운 거 정리해볼게요. 고개 들기, 디딤발 거리, 그리고 다음 방향으로 받기. 이 세 가지만 바꿔도 달라져요."}]
        self.assertEqual(msg._recap_items(one, 0)[0].count("\n"), 2)

    def test_shorts_does_not_end_inside_a_new_section(self):
        rec = {"shorts": [{"start": 10.0, "end": 52.0, "cuts": [{"in": 10.0, "out": 30.0}, {"in": 31.0, "out": 52.0}]}]}
        moms = [{"kind": "section", "a": 45.0, "b": 47.0, "t": 45.0, "score": 1.0, "text": "세 번째 포인트", "why": ""},
                {"kind": "play", "a": 20.0, "b": 24.0, "t": 22.0, "score": 2.0, "text": "", "why": ""}]
        w = msg._shorts_window(rec, moms, 120.0, [])
        self.assertLessEqual(w[-1]["out"], 44.9)                         # '세 번째 포인트' 앞에서 끝
        self.assertAlmostEqual(w[0]["in"], 10.0)

    def test_low_motion_cutaway_scene_is_a_demo(self):
        """판정 round6: 선수가 작게 보이는 시범 장면(움직임 중앙값 아래)이 말 없는 틈째로 잘려 '하나, 둘, 셋' → '나이스!'만 남음."""
        motion = [0.1] * 120
        motion[34] = motion[44] = 5.0                   # 17초·22초 장면 바뀜 순간만 큼
        sig = {"name": "x", "duration": 60.0, "tidy": [{"in": 0.0, "out": 60.0}], "junk": [], "motion": motion, "motionStep": 0.5,
               "onsets": [], "cuts": [17.25, 22.25, 40.0]}
        words = [(10.0, 18.2, "셋.", 0), (23.9, 24.8, "나이스!", 1)]
        dw = msg.demo_windows(sig, words)
        self.assertTrue(any(d[0] <= 18.4 and d[1] >= 22.0 for d in dw), dw)
        segs = [{"start": 10.0, "end": 18.2, "text": "하나, 둘, 셋.", "words": [{"w": "셋.", "s": 10.0, "e": 18.2}]},
                {"start": 23.9, "end": 24.8, "text": "나이스!", "words": [{"w": "나이스!", "s": 23.9, "e": 24.8}]}]
        sig["demo"] = dw
        plays = [m for m in msg.moments(sig, segs) if m["kind"] == "play"]
        self.assertEqual(len(plays), 1)
        self.assertTrue(18.2 < plays[0]["t"] < 22.25, plays[0])          # 장면 바뀜 순간이 아니라 시범 장면 가운데

    def test_words_do_not_overlap_across_segments(self):
        segs = [{"start": 56.3, "end": 58.0, "text": "아이고, 빗나갔어요.", "words": [{"w": "아이고,", "s": 56.34, "e": 56.64}, {"w": "빗나갔어요.", "s": 57.4, "e": 57.6}]},
                {"start": 58.12, "end": 59.74, "text": "마지막 다섯 번째,", "words": [{"w": "마지막", "s": 58.12, "e": 58.92}, {"w": "번째,", "s": 58.92, "e": 59.74}]}]
        out, _ = msg.align_to_sound(segs, [[56.3, 56.7], [57.51, 58.4], [58.5, 59.8]])
        self.assertLessEqual(out[0]["words"][-1]["e"], out[1]["words"][0]["s"] - 0.02 + 1e-6)   # 말 자막 두 줄이 한꺼번에 뜨지 않게
