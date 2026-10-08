"""썸네일 퀄리티 판정 4회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality4

- 문구(thumbcopy): 부정 문장('~지 마세요')·조사 붙은 낱말('3초 안에 공을')·다른 마디의 방향 낱말 → 구체 낱말로 쓰지 않음 · '속도를 줄이고' → '속도 줄이기' ·
  질문은 대사의 질문 또는 문제 말 + 같은 기술 종류일 때만 · 상투 꼬리표는 대사에 있어도 상투 · 같은 낱말이 두 줄에 · 클로드 근거 인용(문장 부호 무시, 3글자 조각) ·
  근거 없음 오류 종류·실패 기억 · 확인 늦음(unknown) → 'maybe' · NFD 한글
- 실제 받아쓰기(tests/fixtures/thumb_asr: Whisper 로 받아쓴 레슨 말, 추출 규칙을 만든 사람이 쓰지 않은 글)로 구체 낱말 정확도
- 장면(thumb): 이름 띠는 이름 띠 모양만 (광고판·바닥 글자·숫자·발이 보이는 사람이 있으면 아님) · 클로드 기다림 상한
- 화면 계산(p8_ai.js 순수 함수, node): 둘째 줄 열쇠 · 보이는 점수(100 동점 없음) · 요란한 색 부드러운 감점 · O/X 기호 · 흐린 빈 곳 비율
인터넷·진짜 클로드는 쓰지 않음."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import thumb  # noqa: E402
import thumbcopy as tc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASR = ROOT / "tests" / "fixtures" / "thumb_asr"
HAS_NODE = shutil.which("node") is not None


def asr(name):
    return [s["text"] for s in json.loads((ASR / f"{name}.json").read_text(encoding="utf-8"))]


class DetailTests(unittest.TestCase):
    def d(self, *texts, tp=()):
        return [p for p, _ in tc.details(list(texts), list(tp))]

    def test_negated_lines_are_not_answers(self):
        for t in ("구석을 노리지 마세요", "무릎을 굽히지 마세요", "고개를 숙이지 마세요", "5초 안에 슛하지 마", "실수하는 분들이 많은데 무게중심이 뒤에 있으면 안 돼요",
                  "공 보고 차지 말고 고개 드세요"):
            self.assertEqual(self.d(t), [], t)

    def test_particle_word_after_n_seconds_is_rejected(self):
        self.assertEqual(self.d("3초 안에 공을 받으세요"), [])
        self.assertEqual(self.d("5번 만에 골을 넣었어요"), [])
        self.assertEqual(self.d("공을 뺏기면 3초 안에 압박하세요"), ["3초 안에 압박"])

    def test_change_verbs_keep_meaning(self):
        self.assertEqual(self.d("속도를 줄이고 먼저 멈추세요"), ["속도 줄이기"], "'핵심은 속도'는 대사와 반대 뜻")
        self.assertEqual(self.d("무게중심을 낮추고 반대쪽으로 치세요"), ["무게중심 낮추기"], "방향은 다른 마디('-고' 뒤) 것")
        self.assertEqual(self.d("첫 터치를 수비 반대쪽으로"), ["첫 터치는 반대쪽"], "같은 마디의 방향은 붙임")

    def test_single_word_needs_key_framing(self):
        self.assertEqual(self.d("자, 두 번째 포인트는 디딤발이에요."), ["디딤발"])
        self.assertEqual(self.d("디딤발이 공이랑 너무 가까우면 터치가 무조건 길어져요."), [], "뜻이 뒤에 있는 낱말 하나는 쓰지 않음")
        self.assertEqual(self.d("자, 디딤발 위치가, 정말 핵심이에요."), ["디딤발 위치"], "받아쓰기 쉼표")

    def test_question_sentence_is_not_an_answer_and_synonym_of_topic(self):
        self.assertEqual(self.d("왜 다들 첫 터치에서 공을 놓칠까요?"), [])
        self.assertEqual(self.d("첫 터치가 핵심이에요", tp=["퍼스트 터치"]), [], "주제와 같은 뜻 (첫 터치 = 퍼스트 터치)")

    def test_transcript_question(self):
        self.assertEqual(tc.transcript_question(["어... 왜 다들 첫 터치에서 공을 놓칠까요?"], ["퍼스트 터치"]), "왜 공 놓칠까?")
        self.assertEqual(tc.transcript_question(["오늘은 드리블을 해요"]), "")

    def test_problem_question_needs_evidence_and_same_kind(self):
        self.assertEqual(tc.problem_q("dribble", "상체 페인트", "수비한테 자꾸 막혀요"), "왜 막힐까?")
        self.assertEqual(tc.problem_q("dribble", "상체 페인트", "상체 페인트로 속이세요"), "", "대사에 문제 말이 없으면 질문 없음")
        self.assertEqual(tc.problem_q("dribble", "고개 들기", "자꾸 막혀요"), "", "콘 드리블의 '고개 들기'는 막힘의 답이 아님")
        self.assertEqual(tc.problem_q("tactic", "3초 안에 압박", "패스를 못 받아요"), "", "압박은 '못 받을까'의 답이 아님")
        self.assertEqual(tc.problem_q("shoot", "디딤발 위치", "슛이 자꾸 떠요"), "슛이 뜨는 이유?")

    def test_rule_candidates_on_real_asr(self):
        """실제 받아쓰기(만든 사람이 규칙에 맞춰 쓰지 않은 말) — 부정·질문·주제와 같은 낱말이 큰 줄로 가지 않고, 상투 꼬리표가 위에 오지 않음."""
        for name, title in (("MSGRAW01_퍼스트 터치 레슨", "퍼스트 터치 레슨"), ("MSGRAW02_패스 앤 무브 드릴", "패스 앤 무브 드릴"),
                            ("CAPTEST001_퍼스트 터치 강의", "퍼스트 터치 강의"), ("MSGRAW03_슈팅 챌린지", "슈팅 챌린지")):
            texts = asr(name)
            ds = tc.details(texts, tc.topics(title, texts))
            for ph, src in ds:
                self.assertFalse(tc.NEG.search(src[src.find(ph.split()[0]):]) and ph.split()[0] in src, (name, ph, src))
                self.assertNotIn("?", src)
            C, tp = tc.rule_candidates(title, texts)
            body = " ".join(texts) + " " + title
            top = sorted((tc.finish(c, tp, body) for c in C), key=lambda c: -c["score"])[:5]
            self.assertFalse(any(c["stock"] for c in top), (name, [(c["l1"], c["l2"]) for c in top]))
            for c in top:
                self.assertNotIn(".", c["l1"] + c["l2"], "받아쓰기 마침표가 썸네일에")
        self.assertEqual([p for p, _ in tc.details(asr("MSGRAW01_퍼스트 터치 레슨"), ["퍼스트 터치"])], ["고개 들기", "디딤발"])
        self.assertEqual(tc.transcript_question(asr("MSGRAW01_퍼스트 터치 레슨"), ["퍼스트 터치"]), "왜 공 놓칠까?")

    def test_topic_word_not_on_both_lines(self):
        C, _ = tc.rule_candidates("슈팅 디딤발", ["디딤발 위치가 핵심이에요"])
        for c in C:
            if c.get("detail"):
                self.assertFalse(c["l1"] == "슈팅 디딤발" and "디딤발" in c["l2"], (c["l1"], c["l2"]))
        self.assertIn(("슈팅", "디딤발 위치"), [(c["l1"], c["l2"]) for c in C])


class StockGroundTests(unittest.TestCase):
    def test_quoted_stock_is_still_stock(self):
        c = tc._cand("패스 앤 무브 하나로", "플랩 레벨업", 1, "레벨업", "", "levelup", 0.75, "")
        self.assertTrue(tc.stock(c, "패스 앤 무브 이것만 알면 플랩 레벨업 바로 됩니다"))
        self.assertTrue(tc.stock(tc._cand("드리블", "꿀팁 1가지", 1, "", "", "x", 1, "")))

    def test_grounded_ignores_punctuation_and_needs_real_overlap(self):
        body = "자, 디딤발 위치가, 정말 핵심이에요."
        self.assertTrue(tc.grounded("디딤발 위치", "정말 핵심", "디딤발 위치가 정말 핵심이에요", body))
        self.assertFalse(tc.grounded("오늘은", "이것만 보세요", "오늘은 퍼스트 터치 꿀팁", "오늘은 퍼스트 터치 꿀팁을 알려드릴게요"), "두 글자('오늘')만 겹침")
        self.assertFalse(tc.grounded("플랩 레벨업", "바로 됩니다", "바로 해 볼게요", "바로 해 볼게요"))

    def test_ungrounded_is_its_own_error(self):
        txt = json.dumps([{"l1": "플랩 레벨업", "l2": "바로 됩니다", "emph": "", "sub": "", "q": "없는 말"}], ensure_ascii=False)
        with self.assertRaises(tc.Ungrounded):
            tc.parse_ai(txt, "디딤발 위치가 핵심이에요", [])
        with self.assertRaises(ValueError):
            tc.parse_ai("[]", "디딤발", [])

    def test_prompt_has_no_banned_examples(self):
        p = "\n".join(tc.REF_EXAMPLES)
        for bad in ("실력이 늘어요", "플랩 레벨업", "골이 늘어요", "1분만"):
            self.assertNotIn(bad, p)
        self.assertGreaterEqual(tc.PROMPT_VER, 5)

    def test_nfd_title(self):
        nfd = unicodedata.normalize("NFD", "드리블 돌파 비법")
        self.assertEqual(tc.topics(nfd, [])[:1], ["드리블 돌파"])
        self.assertEqual(tc.nice_title("20261007_ABCDEFGHIJK_" + nfd + ".mp4"), "드리블 돌파 비법")
        self.assertEqual(tc.topics("ㅋㅋㅋ", ["ㅋㅋㅋ 대박"]), [])


class AiFailTests(unittest.TestCase):
    NAME = "20261007_ABCDEFGHIJK_발바닥 드래그 기본기.mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="문구 실패 "))
        w = self.tmp / "작업"
        self.p = [mock.patch.object(core, "WORK", w), mock.patch.object(core, "VIDEOS", w / "videos"), mock.patch.object(core, "ANALYSIS", w / "analysis")]
        for p in self.p:
            p.start()
        (w / "videos").mkdir(parents=True)
        (w / "videos" / self.NAME).write_bytes(b"0")
        core.adir(self.NAME).mkdir(parents=True)
        (core.adir(self.NAME) / "transcript.json").write_text(json.dumps([{"start": 0, "end": 2, "text": "발바닥 드래그로 수비를 속이세요"}], ensure_ascii=False), encoding="utf-8")
        tc._PROVEN["ok"] = False

    def tearDown(self):
        for p in self.p:
            p.stop()
        tc._PROVEN["ok"] = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failure_is_remembered_with_cooldown(self):
        import claude_cli
        bad = json.dumps([{"l1": "플랩 레벨업", "l2": "바로 됩니다", "emph": "", "sub": "", "q": "없는 말이에요"}], ensure_ascii=False)
        with mock.patch.object(claude_cli, "run", return_value={"text": bad}):
            r = tc.run_ai(self.NAME, log=lambda *_: None, progress=False)
        self.assertEqual(r["kind"], "ungrounded")
        self.assertTrue(tc.failed_recently(self.NAME))
        self.assertIsNone(tc.load_ai(self.NAME))
        self.assertTrue(tc._PROVEN["ok"], "클로드가 대답은 했음")
        d = json.loads((core.adir(self.NAME) / tc.CACHE).read_text(encoding="utf-8"))
        d["failAt"] -= tc.AI_RETRY + 5
        (core.adir(self.NAME) / tc.CACHE).write_text(json.dumps(d), encoding="utf-8")
        self.assertFalse(tc.failed_recently(self.NAME), "1시간 뒤에는 다시")
        with mock.patch.object(claude_cli, "run", side_effect=claude_cli.ClaudeError("limit", "한도")):
            tc.run_ai(self.NAME, log=lambda *_: None, progress=False)
        self.assertTrue(tc.failed_recently(self.NAME))

    def test_start_skips_after_failure(self):
        (core.adir(self.NAME) / tc.CACHE).write_text(json.dumps({"failSig": tc._sig(self.NAME), "failAt": int(time.time())}), encoding="utf-8")
        with mock.patch.object(thumb, "_ai_on", return_value="ready"):
            self.assertIsNone(thumb._start_ai_copy(self.NAME, lambda *_: None))

    def test_unknown_state_is_maybe_until_proven(self):
        import claude_cli
        with mock.patch.object(claude_cli, "find_exe", return_value="claude"), mock.patch.object(claude_cli, "status", return_value={"state": "unknown"}):
            self.assertEqual(tc.ai_state(), "maybe")
            self.assertFalse(tc.ai_ready())
            with mock.patch.object(thumb, "load_brand", return_value={"aiCopy": True}):
                self.assertEqual(thumb._ai_on(self.NAME), "maybe")
                self.assertEqual(thumb._AI_WAIT[self.NAME], thumb.AI_MAYBE_WAIT)
            tc._PROVEN["ok"] = True
            self.assertEqual(tc.ai_state(), "ready")
        with mock.patch.object(claude_cli, "find_exe", return_value="claude"), mock.patch.object(claude_cli, "status", return_value={"state": "login"}):
            self.assertEqual(tc.ai_state(), "")


class NameBarTests(unittest.TestCase):
    def test_only_name_bar_shape(self):
        nm = {"box": [0.1, 0.86, 0.45, 0.9], "h": 0.04, "text": "Edwin José Pinzón"}
        self.assertEqual(thumb.name_bar([nm]), 0.845)
        self.assertIsNone(thumb.name_bar([dict(nm, box=[0.1, 0.8, 0.6, 0.9])]), "높은 글자(광고판·전광판)")
        self.assertIsNone(thumb.name_bar([dict(nm, box=[0.55, 0.86, 0.95, 0.9])]), "오른쪽에서 시작 (바닥 글자·광고)")
        self.assertIsNone(thumb.name_bar([dict(nm, text="12 : 34")]), "숫자 (점수판)")
        self.assertIsNone(thumb.name_bar([nm, dict(nm, box=[0.1, 0.8, 0.4, 0.83]), dict(nm, box=[0.1, 0.92, 0.4, 0.95])]), "줄이 많음")
        self.assertIsNone(thumb.name_bar([nm], persons=[[0.4, 0.3, 0.1, 0.62, 0.9]]), "띠 아래에 발이 보이는 선수 → 경기장 안 글자")
        self.assertEqual(thumb.name_bar([nm], persons=[[0.3, 0.1, 0.5, 0.9, 0.9]]), 0.845, "인터뷰 상반신(화면 아래 끝에 잘림)은 괜찮음")

    def test_wait_cap(self):
        thumb._AI_WAIT["x"] = 0.2
        import threading
        th = threading.Thread(target=time.sleep, args=(3,), daemon=True)
        th.start()
        t0 = time.time()
        with mock.patch.object(core, "set_progress"):
            thumb._wait([th], "x", "")
        self.assertLess(time.time() - t0, 2)


JS = ("clamp", "l2Key", "shownScore", "LOUD", "loud", "loudMult", "OX_RX", "oxRuns", "blurFill")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => { let a = src.indexOf('function ' + n + '('); if (a < 0) { a = src.indexOf('const ' + n + ' ='); const e = src.indexOf(';\n', a); return src.slice(a, e + 1); }
  let d = 0, i = src.indexOf('{', a); for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]).filter(n => n !== 'clamp');
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var DIGIT_FONT = "Pretendard Black";' + NAMES.map(pick).join('\n') + '; globalThis.T = {' + NAMES.join(',') + '};');
const cases = JSON.parse(fs.readFileSync(0, 'utf8')); const out = cases.map(([fn, args]) => (typeof T[fn] === 'function' ? T[fn](...args) : T[fn]));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ScreenMath4Tests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_l2_key(self):
        out = self.run_js([["l2Key", [{"l1": "1대1 돌파", "l2": "핵심은 속도"}]], ["l2Key", [{"l1": "왜 막힐까?", "l2": "속도"}]], ["l2Key", [{"l1": "떨지 마세요!", "l2": ""}]]])
        self.assertEqual(out[0], out[1], "'속도'와 '핵심은 속도'는 같은 둘째 줄")
        self.assertEqual(out[2], "떨지마세요")

    def test_shown_score_never_ties_at_100(self):
        out = self.run_js([["shownScore", [80]], ["shownScore", [100]], ["shownScore", [130]], ["shownScore", [160]]])
        self.assertEqual(out[0], 80)
        self.assertTrue(out[0] < out[1] < out[2] < out[3] < 100, out)

    def test_loud_soft(self):
        out = self.run_js([["loudMult", [{"kind": "mid", "color": 70}]], ["loudMult", [{"kind": "mid", "color": 100}]], ["loudMult", [{"kind": "mid", "color": 200}]],
                           ["loudMult", [{"kind": "close", "color": 120}]]])
        self.assertEqual(out[0], 1, "레퍼런스 상위 10%(83) 아래는 깎지 않음")
        self.assertAlmostEqual(out[1], 0.75, places=3)
        self.assertEqual(out[2], 0.7)
        self.assertEqual(out[3], 1)

    def test_ox_runs(self):
        out = self.run_js([["oxRuns", ["공 보기 X"]], ["oxRuns", ["고개 들기 O"]], ["oxRuns", ["OX퀴즈"]]])
        self.assertEqual(out[0][0]["s"], 5)
        self.assertEqual(out[1][0]["fill"], "#2BD96B")
        self.assertEqual(out[2], [], "낱말 안의 O·X 는 그대로")

    def test_blur_fill(self):
        W, H = 1080, 1920
        letter = {"w": W, "h": H, "layers": [{"type": "image", "name": "흐린 배경", "x": 0, "y": 0, "w": W, "h": H, "blur": 38},
                                             {"type": "image", "name": "배경", "x": 0, "y": 160, "w": W, "h": 972, "blur": 0}]}
        two = {"w": W, "h": H, "layers": [{"type": "image", "name": "배경", "x": 0, "y": 0, "w": W, "h": 960, "blur": 0},
                                          {"type": "image", "name": "장면 2", "x": 0, "y": 960, "w": W, "h": 960, "blur": 0}]}
        band = {"w": W, "h": H, "layers": [{"type": "image", "name": "흐린 배경", "x": 0, "y": 0, "w": W, "h": H, "blur": 38},
                                           {"type": "shape", "name": "검은 띠", "x": 0, "y": 0, "w": W, "h": 500},
                                           {"type": "image", "name": "배경", "x": 0, "y": 500, "w": W, "h": 1100, "blur": 0}]}
        out = self.run_js([["blurFill", [letter]], ["blurFill", [two]], ["blurFill", [band]]])
        self.assertGreater(out[0], 0.3, "720p 가로 장면 + 흐린 판")
        self.assertEqual(out[1], 0)
        self.assertLess(out[2], 0.05, "띠(단색)는 흐린 빈 곳이 아님")


if __name__ == "__main__":
    unittest.main()
