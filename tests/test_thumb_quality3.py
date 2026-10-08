"""썸네일 퀄리티 판정 3회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality3

- 문구(thumbcopy): 상투 꼬리표('이 순서대로!'·'플랩 레벨업'·'~늘어요'·'딱 1가지') 표시·감점 · 대사의 구체 낱말 틀('디딤발 위치'·'3초 안에 압박') ·
  클로드 문구는 대사 근거 인용(q)이 있어야 · 질문 판 3
- 장면(thumb): 색이 요란한 장면(colorfulness) · 아래 이름 띠(로워서드) 찾기 · 후보 지문에 CAND_VER
- 화면 계산(p8_ai.js 순수 함수, node): 화살표 끝과 선수 거리 · 같은 인터뷰 얼굴 묶기 · 요란한 장면 감점
인터넷·진짜 클로드는 쓰지 않음."""
import inspect
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thumb  # noqa: E402
import thumbcopy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None


class StockCopyTests(unittest.TestCase):
    """판정 3회차: '이 순서대로!' 15/76 · '플랩 레벨업' 7 · 상투 꼬리표가 있는 장과 클로드 총점 상관 −0.33."""

    def cands(self, title, texts):
        C, tp = thumbcopy.rule_candidates(title, texts, rotate=False)
        body = " ".join(texts) + " " + title
        return {c["pid"]: thumbcopy.finish(c, tp, body) for c in C}, tp

    def test_no_order_and_stock_marked(self):
        c, _ = self.cands("슈팅 연습", ["디딤발 위치가 핵심이에요"])
        self.assertNotIn("order", c, "순서를 화면에 못 보여 주면 '이 순서대로!'는 빈 약속")
        for pid in ("one", "result", "levelup", "grow"):
            self.assertTrue(c[pid]["stock"] and c[pid]["vague"], pid)
        self.assertFalse(c["detail0"]["stock"] or c["detail0"]["vague"])
        self.assertTrue(c["detail0"]["concrete"])

    def test_quoted_stock_is_still_stock(self):
        c, _ = self.cands("패스 앤 무브", ["패스 앤 무브 이것만 알면 플랩 레벨업 바로 됩니다"])
        self.assertTrue(c["levelup"]["stock"], "판정 4회차: 대사에 그 말이 있어도 상투 (인용 예외 없앰)")

    def test_detail_phrases_from_transcript(self):
        d = dict(thumbcopy.details(["디딤발 위치가 핵심이에요", "공을 뺏기면 3초 안에 압박하세요", "이거 진짜 중요해요 첫 터치를 수비 반대쪽으로",
                                    "고개 들고 드리블하는 습관이 제일 중요해요", "오프더볼의 비밀은 시야예요", "골키퍼 반대쪽 구석을 보세요", "구석구석 다 봐요"]))
        self.assertEqual(list(d), ["디딤발 위치", "3초 안에 압박", "첫 터치는 반대쪽", "고개 들기", "시야", "반대쪽 구석"])
        self.assertEqual(thumbcopy.details(["슈팅 연습해요"], ["슈팅"]), [])

    def test_detail_beats_stock_and_generic(self):
        c, tp = self.cands("슈팅 연습 세로 영상", ["슈팅 이렇게 차면 무조건 들어가요", "슛이 자꾸 떠요", "디딤발 위치가 핵심이에요", "골키퍼 반대쪽 구석을 보세요"])
        best = max(c.values(), key=lambda x: x["score"])
        # 판정 5회차(D-094): 맨 위는 구체 낱말 라벨이 아니라 말하듯 끝나는 훅 (라벨 '슈팅 / 디딤발 위치'는 판정 3/6/3) — 구체 낱말은 훅의 보조 문구로
        self.assertTrue(thumbcopy.spoken(best), (best["l1"], best["l2"]))
        self.assertIn("디딤발 위치", [x["l2"] for x in c.values() if x.get("detail")])
        self.assertIn("디딤발 위치", [x["sub"] for x in c.values() if x["pid"] in thumbcopy.HOOK_SUB], "구체 낱말은 훅의 작은 줄로")
        q = [x for x in c.values() if x["pid"].startswith("detailq")]
        self.assertTrue(q and q[0]["l1"].endswith("?"), "문제 질문 + 대사의 해답")
        for k in ("one", "result"):
            self.assertLess(c[k]["score"], best["score"] - 1)

    def test_question_follows_topic_kind(self):
        c, _ = self.cands("오프더볼 움직임", ["골키퍼가 완전히 속았어요", "오프더볼의 비밀은 시야예요", "패스를 못 받아요"])
        q = [x for x in c.values() if x["pid"].startswith("detailq")]
        self.assertTrue(q)
        self.assertNotIn("들어갈", q[0]["l1"], "주제(오프더볼)가 전술이면 대사의 '골키퍼'로 슈팅 질문을 만들지 않음")
        self.assertTrue(all(len(x["l1"]) <= 8 for x in q), "질문 줄은 쇼츠 한 줄(8자) 안")

    def test_josa(self):
        self.assertEqual([thumbcopy.josa(w, "은", "는") for w in ("디딤발", "첫 터치", "슈팅")], ["은", "는", "은"])

    def test_ai_needs_grounding(self):
        body = "공을 뺏기면 3초 안에 압박하세요"
        ok = thumbcopy._check_ai({"l1": "수비 전환", "l2": "3초 안에 압박", "emph": "3초", "sub": "", "q": body}, body, ["수비 전환"])
        self.assertEqual(ok["q"], body)
        self.assertIsNone(thumbcopy._check_ai({"l1": "수비 전환", "l2": "이 순서대로!", "emph": "", "sub": "", "q": "수비 전환이에요"}, body, ["수비 전환"]),
                          "근거가 대사에 없음")
        self.assertIsNone(thumbcopy._check_ai({"l1": "수비 전환", "l2": "이 순서대로!", "emph": "", "sub": ""}, body, ["수비 전환"]), "근거 없음")
        self.assertIsNotNone(thumbcopy._check_ai({"l1": "수비 전환", "l2": "이 순서대로!", "emph": "", "sub": ""}), "대사가 없는 영상은 검사하지 않음")

    def test_prompt_v3(self):
        self.assertGreaterEqual(thumbcopy.PROMPT_VER, 3)
        src = inspect.getsource(thumbcopy.prompt)
        self.assertIn('"q"', src)  # 판정 5회차(D-094): 상투 꼬리표 금지 목록은 질문에서 뺌 (쪼살 자신의 인기 문구 · 묶음에 1개는 화면 recommend 가 지킴)
        self.assertIn("대사 근거", src)


class SceneTests(unittest.TestCase):
    def test_colorfulness(self):
        from PIL import Image
        import numpy as np
        grass = Image.new("RGB", (320, 180), (60, 120, 50))
        rng = np.random.default_rng(1)
        wall = Image.fromarray((rng.integers(0, 2, (18, 32, 1)) * np.array([[[255, 20, 200]]]) + (1 - rng.integers(0, 2, (18, 32, 1))) * np.array([[[20, 255, 60]]])).astype(np.uint8)).resize((320, 180))
        self.assertLess(thumb.colorfulness(grass), 45)
        self.assertGreater(thumb.colorfulness(wall), 70)

    def test_name_bar(self):
        lines = [{"box": [0.1, 0.86, 0.45, 0.9], "h": 0.04, "text": "Edwin José Pinzón"}, {"box": [0.1, 0.1, 0.5, 0.2], "h": 0.1, "text": "x"}]
        self.assertEqual(thumb.name_bar(lines), 0.845)
        self.assertIsNone(thumb.name_bar([{"box": [0.1, 0.86, 0.15, 0.9], "h": 0.04, "text": "10"}]), "등번호처럼 좁은 글자는 아님")
        self.assertIsNone(thumb.name_bar([]))

    def test_version_in_signature(self):
        self.assertGreaterEqual(thumb.CAND_VER, 2)
        self.assertIn("CAND_VER", inspect.getsource(thumb._cand_sig))
        self.assertIn('"color"', inspect.getsource(thumb.frame_candidates))


JS = ("clamp", "clipTo", "areaOf", "interArea", "mainBox", "mainFace", "hamming", "tipClear", "sameFace", "sceneGroups", "loud", "loudMult", "blurQ", "frameQ", "srcHeadCut", "footClose", "headlessBad")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => { let a = src.indexOf('function ' + n + '('); if (a < 0) { a = src.indexOf('const ' + n + ' ='); const e = src.indexOf(';\n', a); return src.slice(a, e + 1); }
  let d = 0, i = src.indexOf('{', a); for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]).filter(n => n !== 'clamp');
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var TEXTY = 0.05; var LOUD = 85;' + NAMES.map(pick).join('\n') + '; globalThis.T = {' + NAMES.join(',') + '};');
const cases = JSON.parse(fs.readFileSync(0, 'utf8')); const out = cases.map(([fn, args]) => T[fn](...args));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ScreenMath3Tests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_arrow_tip_clear_of_players(self):
        p = {"box": {"x": 500, "y": 300, "w": 80, "h": 200}}
        out = self.run_js([["tipClear", [[600, 400], [p], 1280]], ["tipClear", [[720, 400], [p], 1280]], ["tipClear", [[540, 380], [p], 1280]], ["tipClear", [[0, 0], [], 1280]]])
        self.assertEqual(out, [False, True, False, True], "판정 3회차: 화살표 끝이 선수에서 0.065W → 0.1W(128px) 넘게")

    def test_same_interview_face_grouped(self):
        f = lambda t, h, fx: {"t": t, "hash": h, "kind": "close", "main": 0, "persons": [[0.3, 0.1, 0.5, 0.9, 0.9]], "faces": [{"box": [fx, 0.15, 0.2, 0.3]}]}  # noqa: E731
        a, b, c = f(1, "0000000000000000", 0.4), f(2, "ffffffffffffffff", 0.42), f(3, "00000000ffffffff", 0.1)
        g = self.run_js([["sceneGroups", [[a, b, c]]]])[0]
        self.assertEqual(g["1"], g["2"], "지문이 달라도 얼굴 자리가 같으면 같은 인터뷰 (005 쇼츠 6장 같은 코치)")
        self.assertNotEqual(g["1"], g["3"])

    def test_loud_colour_scene_penalized(self):
        base = {"score": 10, "kind": "mid", "persons": [[0.3, 0.2, 0.3, 0.7, 0.9]], "main": 0, "flags": [], "blur": 0, "text": 0}
        out = self.run_js([["frameQ", [dict(base, color=30), 10]], ["frameQ", [dict(base, color=95), 10]], ["frameQ", [dict(base, color=95, kind="close"), 10]]])
        self.assertAlmostEqual(out[1], out[0] * 5 / 6, places=4)  # 판정 4회차: 85 넘는 만큼만 부드럽게 (95 → ×0.833)
        self.assertNotAlmostEqual(out[2], out[1], places=4)


if __name__ == "__main__":
    unittest.main()
