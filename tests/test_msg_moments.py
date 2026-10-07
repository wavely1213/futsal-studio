"""MSG 신호·재미 순간 (msg.signals · moments, D-023) — 저장소 폴더에서 python3 -m unittest tests.test_msg_moments
정답을 아는 시험 원본(make_msg_fixture: 말 + 말 없는 시범 · 공 소리 · 웃음 · NG 다시 찍기)으로
순간 찾기(정답 80% 넘게) · NG 안에서는 아무것도 안 찾음 · 공 소리만 (말 첫소리·웃음은 아님) · 시범 구간을 살림 · 캐시 · 문장 나누기."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import editor  # noqa: E402
import msg  # noqa: E402
import style  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402


class Moments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        cls.sig = msg.signals(cls.w.name, lambda m: None)
        cls.moms = msg.moments(cls.sig)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def kinds(self, k):
        return [m for m in self.moms if m["kind"] == k]

    def test_recall_on_truth(self):
        """정답 순간(시범·펀치라인·강조·성공·질문·장 나눔·마무리)의 80% 넘게를 ±1초 안에서 찾음."""
        t = self.w.truth
        want = [("play", x["a"], x["b"]) for x in t["plays"]] + [("punchline", x["a"], x["b"] + 1.0) for x in t["punchlines"]] + \
               [("emphasis", x["a"], x["b"]) for x in t["emphasis"]] + [("success", x["a"], x["b"]) for x in t["success"]] + \
               [("question", x["a"], x["b"]) for x in t["question"]] + [("section", x["a"], x["b"]) for x in t["section"]] + \
               [("closing", x["a"], x["b"]) for x in t["closing"]]
        hit = [k for k, a, b in want if any(m["kind"] == k and a - 1.0 <= m["t"] <= b + 1.0 for m in self.moms)]
        self.assertGreaterEqual(len(hit) / len(want), 0.8, f"찾음 {hit} / 정답 {[w[0] for w in want]}")
        self.assertTrue(self.kinds("hook_line"), "앞부분 질문을 훅으로")

    def test_nothing_inside_junk(self):
        """NG 앞 테이크·슬레이트 말(정리할 곳) 안에서는 순간을 찾지 않음 — '인사이드' 강조는 다시 찍은 뒤 테이크에서만."""
        for j in self.w.truth["junk"]:
            inside = [m for m in self.moms if j["a"] <= m["t"] < j["b"]]
            self.assertEqual(inside, [], inside)
        self.assertTrue(any(m["kind"] == "emphasis" and m["t"] >= 17.0 and "인사이드" in m["text"] for m in self.moms))

    def test_onsets_are_kicks_not_speech_or_laughs(self):
        on = self.sig["onsets"]
        kicks = [k for p in self.w.truth["plays"] for k in p["kicks"]]
        self.assertEqual(len(on), len(kicks), on)
        for k in kicks:
            self.assertTrue(any(abs(o - k) <= 0.1 for o in on), (k, on))

    def test_demo_windows_keep_silent_play(self):
        """말로만 정리하면 말 없는 시범이 빠짐 → 움직임·공 소리가 있는 틈은 살림, 컷 목록에도 들어감."""
        p = self.w.truth["plays"][0]
        self.assertTrue(any(a <= p["kicks"][0] and p["kicks"][-1] <= b for a, b in self.sig["demo"]), self.sig["demo"])
        tidy = self.sig["tidy"]
        self.assertFalse(any(c["in"] <= p["kicks"][0] < c["out"] for c in tidy), "말 정리 구간에는 시범이 없음 (기존 가편집)")
        keep = msg.keep_cuts(tidy, self.sig["demo"])
        self.assertTrue(any(c["in"] <= p["kicks"][0] < c["out"] for c in keep))

    def test_scores_normalized_and_deterministic(self):
        for m in self.moms:
            self.assertTrue(0 <= m["score"] <= 1.0, m)
        self.assertEqual(self.moms, msg.moments(self.sig))

    def test_signals_cache_reused(self):
        """영상·받아쓰기가 그대로면 두 번째는 다시 살펴보지 않음 (msg_signals.json)."""
        first = msg.signals(self.w.name, lambda m: None)  # (다른 시험이 받아쓰기를 다시 썼을 수 있음)
        self.assertTrue(msg._sig_file(self.w.name).is_file())
        with mock.patch.object(style, "extract_events", side_effect=AssertionError("다시 살펴봄")):
            again = msg.signals(self.w.name, lambda m: None)
        self.assertEqual(again["onsets"], json.loads(json.dumps(first["onsets"])))

    def test_no_transcript_is_a_friendly_error(self):
        tr = editor.core.adir(self.w.name) / "transcript.json"
        data = tr.read_bytes()
        try:
            tr.unlink()
            with self.assertRaises(msg.MsgError) as cm:
                msg.signals(self.w.name, lambda m: None)
            self.assertIn("편집점 찾기", str(cm.exception))
        finally:
            tr.write_bytes(data)


class Lines(unittest.TestCase):
    def test_merged_segment_is_split_into_sentences(self):
        """받아쓰기 구간이 여러 문장을 한데 묶어도 문장 끝·쉼에서 나눔 (작은 모델·시범 소리 때문에 흔함)."""
        ws = [("왜", 0.0, 0.2), ("놓칠까요?", 0.2, 0.8), ("그래서", 1.0, 1.3), ("중요해요.", 1.3, 1.9), ("나이스", 4.0, 4.4), ("좋아요", 4.5, 4.9)]
        seg = {"start": 0.0, "end": 4.9, "text": " ".join(w for w, _, _ in ws), "words": [{"w": w, "s": a, "e": b} for w, a, b in ws]}
        lines = msg.lines_of([seg])
        self.assertEqual([x["text"] for x in lines], ["왜 놓칠까요?", "그래서 중요해요.", "나이스 좋아요"])
        self.assertEqual(lines[2]["start"], 4.0)

    def test_emphasis_labels_are_not_the_whole_line_or_fragments(self):
        """강조 큰 글자: 말 한 줄을 통째로 옮기지 않고(아래 말 자막과 같은 글 두 번) 핵심 낱말만 · 앞말이 잘린 조각은 안 띄움 · 서술어까지 짧은 구절."""
        self.assertEqual(msg._emph_short("이게 진짜 핵심이에요.", "이게 진짜 핵심이에요."), "진짜 핵심!")
        self.assertEqual(msg._emph_short("인사이드!", "그래서 인사이드로 받을 때는 힘을 빼세요."), "인사이드!")
        self.assertTrue(msg.BOUND.match("번째 슈팅."))
        self.assertFalse(msg.BOUND.match("슈팅 핵심!"))
        self.assertEqual(msg._emph_clause("디딤발이 너무 가까우면 터치가 무조건 길어져요.")[0], "터치가 무조건 길어져요!")
        self.assertIsNone(msg._emph_clause("포인트는 패스하고 멈추지 않는 거예요.")[0])

    def test_lexicons(self):
        self.assertTrue(msg.HOOK_Q.search("왜 다들 공을 놓칠까요?"))
        self.assertFalse(msg.HOOK_Q.search("수비가 붙으니까요"))
        self.assertTrue(msg.COUNT.search("하나, 둘, 셋!"))
        self.assertFalse(msg.COUNT.search("첫 번째 포인트"))
        self.assertTrue(msg.SECTION.search("자, 두 번째 포인트는"))
        self.assertFalse(msg.DEMO_W.search("오늘은 퍼스트 터치를 해 볼게요"))
        self.assertTrue(msg.DEMO_W.search("한 번 보여 드릴게요"))


if __name__ == "__main__":
    unittest.main()
