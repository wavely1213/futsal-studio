"""썸네일 제목 문구 엔진(thumbcopy.py) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_pro_copy

규칙 문구(주제·두 줄 나누기·점수·금지어·서로 다른 틀) · 클로드 문구(가짜 claude: 검사·기억·받아쓰기가 바뀌면 다시) — 인터넷·진짜 클로드는 쓰지 않음."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_cli  # noqa: E402
import core  # noqa: E402
import thumbcopy as tc  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_topics_prefer_title_terms(self):
        self.assertEqual(tc.topics("발바닥 드래그 기본기", ["슛하는 척하다가 드래그로 방향을 바꾸세요", "수비가 못 따라와요"])[0], "발바닥 드래그")
        tp = tc.topics("풋살 경기 하이라이트", ["오프더볼 움직임이 중요해요", "오프더볼의 비밀은 시야예요", "공간을 보세요"])
        self.assertEqual(tp[0], "오프더볼")
        self.assertNotIn("풋살", tp)
        self.assertEqual(tc.title_phrase("1대1 돌파 이렇게 하세요"), "1대1 돌파")
        self.assertEqual(tc.title_phrase("[꿀팁] 슈팅 연습"), "슈팅", "말머리는 빼고 · 연습·훈련 같은 흔한 낱말은 뺌")

    def test_split_keeps_words_whole(self):
        self.assertEqual(tc.split2("영상만 봐도 실력이 늘어요"), ("영상만 봐도", "실력이 늘어요"))
        self.assertEqual(tc.split2("드래그"), ("드래그", ""))
        self.assertIsNone(tc.split2("가나다라마바사아자차카타파하가나다라마바사아자"), "한 낱말이 너무 길면 못 나눔")

    def test_score_rules(self):
        good = tc.finish(tc._cand("드리블", "무조건 봐", 1, "무조건 봐", "", "must", 1.0, ""), ["드리블"])
        long = tc.finish(tc._cand("드리블을 정말 잘하는 방법을 지금", "알려드릴게요", 1, "", "", "x", 1.0, ""), ["드리블"])
        banned = tc.finish(tc._cand("충격", "드리블", 1, "", "", "x", 1.0, ""), ["드리블"])
        self.assertGreater(good["score"], 3)  # 판정 5회차(D-094): 막연 문구 감점은 작게 · 주제 가산 1.5 → 0.6
        self.assertLess(long["score"], 0, "한 줄 12자 넘으면 버림")
        self.assertLess(banned["score"], 0, "낚시 금지어")
        self.assertEqual(good["emph"], [1, 0, 5])
        self.assertEqual(good["tag"], "보기")

    def test_suggest_is_diverse_and_short(self):
        texts = ["오늘은 발바닥 드래그를 알려드릴게요", "실수하는 분들이 많은데 무게중심이 뒤에 있으면 안 돼요", "슛하는 척하다가 드래그로 방향을 바꾸세요", "대박 수비가 완전히 속았어요"]
        C, tp = tc.rule_candidates("발바닥 드래그 기본기", texts, rotate=False)
        items = tc._pick([tc.finish(c, tp) for c in C])
        self.assertGreaterEqual(len(items), 6)
        self.assertEqual(len({c["pid"] for c in items}), len(items), "같은 틀은 하나씩")
        for c in items:
            self.assertLessEqual(len(tc.nospace(c["l1"])), tc.LINE_MAX)
            self.assertLessEqual(len(tc.nospace(c["l2"])), tc.LINE_MAX)
            if c["emph"]:
                ln, a, b = c["emph"]
                self.assertTrue(0 <= a < b <= len((c["l1"], c["l2"])[ln]))
        pids = {c["pid"] for c in C}
        self.assertIn("dont", pids, "대사에 실수 이야기 → 경고형")
        self.assertIn("wow", pids, "대사에 감탄 → 놀람 질문형")
        ox = next(c for c in C if c["pid"] == "ox")
        self.assertEqual(ox["ox"], ["슛", "드래그"], "반전 O/X: 슛? X / 드래그 O")
        # 판정 피드백: '진짜 쉽게'·'이렇게 하세요' 처럼 흔한 말보다 주제(기술 이름)가 큰 줄이어야 무슨 영상인지 바로 보임
        for pid in ("easy", "howto"):
            c = next(c for c in C if c["pid"] == pid)
            self.assertEqual(c["emph"][0], 0, pid)
            self.assertEqual(c["l1"][c["emph"][1]:c["emph"][2]], tp[0], pid)
        # 판정 1회차: '무조건 봐'는 작은 흰 꼬리표가 아니라 가장 큰 노란 줄 (쪼살 'V자 어려우면 / 무조건 봐') · 주제는 첫 줄에 그대로
        # 판정 2회차: 주제가 4자 넘으면('발바닥 드래그 어려우면') 쇼츠 한 줄을 넘어 만들지 않음 → 짧은 주제로 확인
        self.assertNotIn("must", pids)
        C2, tp2 = tc.rule_candidates("드리블 기본기", ["드리블 실수하는 분들이 많은데"], rotate=False)
        must = next(c for c in C2 if c["pid"] == "must")
        self.assertEqual((must["l2"], must["emph"]), ("무조건 봐", [1, 0, 5]))
        self.assertIn(tp2[0], must["l1"])
        self.assertIn("deceive", pids, "드래그 + 대사에 '속이' → '수비를 속이는 X'")
        self.assertEqual(next(c for c in C if c["pid"] == "deceive")["l2"], tp[0])

    def test_no_misleading_patterns(self):
        """대사·주제와 맞지 않는 틀은 만들지 않음: 슈팅은 '수비를 속이는'이 아님 · 동호인 얘기가 없으면 '국대와 동호인' 없음 · 대사에 N초가 있을 때만 숫자 훅."""
        C, _ = tc.rule_candidates("슈팅 연습", ["슈팅 이렇게 차면 무조건 들어가요", "골키퍼가 속았어요"], rotate=False)
        pids = {c["pid"] for c in C}
        self.assertNotIn("deceive", pids)
        self.assertNotIn("gap", pids)
        self.assertNotIn("secs", pids)
        C, _ = tc.rule_candidates("수비 전환", ["공을 뺏기면 3초 안에 압박하세요", "국가대표와 동호인의 차이예요"], rotate=False)
        secs = next(c for c in C if c["pid"] == "secs")
        self.assertEqual((secs["l1"], secs["l2"]), ("3초면 끝나는", "수비 전환"))
        self.assertIn("gap", {c["pid"] for c in C})

    def test_topic_in_small_line_is_penalized(self):
        """핵심 낱말(주제)이 작은 줄로 가고 큰 줄은 밋밋하면 감점 (판정: '디딤발 위치가 / 핵심이에요')."""
        tp = ["디딤발", "슈팅"]
        flat = tc.finish(tc._cand("디딤발 위치", "중요해요", 1, "중요해요", "", "x", 0.8, ""), tp)
        good = tc.finish(tc._cand("디딤발 위치", "중요해요", 0, "디딤발", "", "x", 0.8, ""), tp)
        hook = tc.finish(tc._cand("디딤발 모르면", "무조건 봐", 1, "무조건 봐", "", "x", 0.8, ""), tp)
        self.assertGreater(good["score"], flat["score"] + 1)
        self.assertGreaterEqual(hook["score"], good["score"] - 0.3, "큰 줄이 훅이면 감점 없음")

    def test_context_boost_by_topic(self):
        self.assertGreater(tc.context_boost("오프더볼 공간")["secret"], 0)
        self.assertGreater(tc.context_boost("유소년 기본기")["easy"], 0)
        self.assertEqual(tc.context_boost("안녕하세요"), {})


class AiTests(unittest.TestCase):
    NAME = "20261007_ABCDEFGHIJK_발바닥 드래그 기본기.mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="문구 테스트 "))
        w = self.tmp / "작업 폴더"
        self.p = [mock.patch.object(core, "WORK", w), mock.patch.object(core, "VIDEOS", w / "videos"), mock.patch.object(core, "ANALYSIS", w / "analysis")]
        for p in self.p:
            p.start()
        (w / "videos").mkdir(parents=True)
        (w / "videos" / self.NAME).write_bytes(b"0")
        a = core.adir(self.NAME)
        a.mkdir(parents=True)
        self.tr = a / "transcript.json"
        self.tr.write_text(json.dumps([{"start": 0, "end": 2, "text": "발바닥 드래그로 수비를 속이세요"}], ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_checks_each_item(self):
        txt = "여기요\n```json\n" + json.dumps([
            {"l1": "수비를 속이는", "l2": "발바닥 드래그", "emph": "드래그", "sub": "1분 강좌", "q": "발바닥 드래그로 수비를 속이세요"},
            {"l1": "너무너무너무너무 긴 첫째 줄이에요", "l2": "x", "emph": "x", "sub": ""},
            {"l1": 3, "l2": "숫자", "emph": "", "sub": ""},
            {"l1": "<b>나쁜</b>", "l2": "글자", "emph": "", "sub": ""},
            {"l1": "드래그", "l2": "무조건 봐", "emph": "없는 낱말", "sub": ""}], ensure_ascii=False) + "\n```"
        out = tc.parse_ai(txt)
        self.assertEqual([c["l1"] for c in out], ["수비를 속이는", "드래그"])
        body = "발바닥 드래그로 수비를 속이세요"
        grounded = tc.parse_ai(txt, body, ["발바닥 드래그", "수비"])
        self.assertEqual([c["l1"] for c in grounded], ["수비를 속이는"], "대사가 있으면 근거(q) 인용이 맞는 문구만")
        self.assertEqual(grounded[0]["q"], body)
        # 판정 5회차(D-094): 근거는 인용이 대사에 있는지만 (낱말 겹침 조건은 훅 문구 65% 를 버렸음) · 상투 꼬리표는 따로 표시해 묶음에 1개만
        self.assertTrue(tc.grounded("수비 전환", "이 순서대로!", "수비 전환이 중요해요", "수비 전환이 중요해요", ["수비 전환"]))
        self.assertTrue(tc.stock({"l1": "수비 전환", "l2": "이 순서대로!"}))
        self.assertEqual(out[0]["emph"], [1, 4, 7])
        self.assertIsNone(out[1]["emph"], "줄에 없는 강조 낱말은 강조 없음")
        self.assertEqual(out[0]["src"], "ai")
        for bad in ("대답 없음", "[깨짐", json.dumps({"l1": "a"}), json.dumps([{"l1": "x" * 20, "l2": ""}])):
            with self.assertRaises(ValueError):
                tc.parse_ai(bad)

    def test_prompt_has_rules_and_examples(self):
        p = tc.prompt(self.NAME)
        self.assertIn("발바닥 드래그 기본기", p)
        self.assertIn("발바닥 드래그로 수비를 속이세요", p)
        self.assertIn("JSON 배열", p)
        self.assertIn("'여기' 봐야 / 뚫립니다", p)
        self.assertIn("영상만 봐도 실력이 늘어요", p, "판정 5회차(D-094): 쪼살 자신의 인기 문구는 말투 예시로 (묶음에 1개는 화면이 지킴)")
        self.assertIn("서술어", p)

    def test_run_ai_caches_and_invalidates(self):
        reply = json.dumps([{"l1": "수비를 속이는", "l2": "발바닥 드래그", "emph": "드래그", "sub": "1분 강좌", "q": "발바닥 드래그로 수비를 속이세요"},
                            {"l1": "이거 하나면", "l2": "수비 끝!", "emph": "끝!", "sub": ""}], ensure_ascii=False)
        with mock.patch.object(claude_cli, "run", return_value={"text": reply, "model": "claude-test"}) as run:
            r = tc.run_ai(self.NAME, log=lambda m: None)
        self.assertTrue(r["ok"], r)
        run.assert_called_once()
        self.assertTrue((core.adir(self.NAME) / tc.CACHE).is_file())
        sug = tc.suggest(self.NAME)
        self.assertTrue(sug["ai"])
        self.assertIn("수비를 속이는", [c["l1"] for c in sug["items"] if c["src"] == "ai"])
        self.assertNotIn("이거 하나면", [c["l1"] for c in sug["items"] if c["src"] == "ai"], "판정 3회차: 대사 근거(q)가 없는 클로드 문구는 버림")
        # 받아쓰기가 바뀌면 예전 클로드 문구는 안 씀
        import os
        st = self.tr.stat()
        os.utime(self.tr, (st.st_atime, st.st_mtime + 100))
        self.assertIsNone(tc.load_ai(self.NAME))
        self.assertFalse(tc.suggest(self.NAME)["ai"])

    def test_run_ai_failures_keep_rules(self):
        with mock.patch.object(claude_cli, "run", side_effect=claude_cli.ClaudeError("login", "클로드에 로그인해 주세요")):
            r = tc.run_ai(self.NAME, log=lambda m: None)
        self.assertEqual((r["ok"], r["kind"]), (False, "login"))
        with mock.patch.object(claude_cli, "run", return_value={"text": "모르겠어요", "model": "x"}):
            r = tc.run_ai(self.NAME, log=lambda m: None)
        self.assertEqual((r["ok"], r["kind"]), (False, "format"))
        self.assertIn("규칙 문구는 그대로", r["error"])
        self.assertGreaterEqual(len(tc.suggest(self.NAME)["items"]), 5)


if __name__ == "__main__":
    unittest.main()
