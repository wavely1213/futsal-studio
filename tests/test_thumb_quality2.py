"""썸네일 퀄리티 판정 2회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality2

- 문구(thumbcopy): 구체적인 약속 틀(핵심 1가지·순서·결과·숫자는 대사에 있을 때만) · 막연한 문구 표시 · 'X 어려우면'은 X 가 4자 이하 · 클로드 질문 판(PROMPT_VER)
- 자동 보정(thumb.auto_grade): 채도 높은 원본은 자연 채도를 아낌 · 레벨만으로 채도가 크게 오르면 레벨을 덜 늘림 · 아주 어두운 장면도 밝게 · 보정 판이 장면 후보 지문에 들어감
- 화면 계산(p8_ai.js 순수 함수, node): 줄 길이(쇼츠 8·롱폼 9자)·다시 나누기 · 원본에서 머리 잘린 주인공·발 클로즈업 · 확대 상한·장면 칸 높이 · 쓸 만한 장면 없음
인터넷·진짜 클로드는 쓰지 않음."""
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


class CopyPromiseTests(unittest.TestCase):
    """판정 2회차: 문구 이유 44/84 '흔한·막연한 문구, 구체적 약속이 없음'."""

    def cands(self, title, texts):
        C, tp = thumbcopy.rule_candidates(title, texts, rotate=False)
        return {c["pid"]: thumbcopy.finish(c, tp) for c in C}

    def test_concrete_templates_and_flags(self):
        c = self.cands("1대1 돌파 이렇게 하세요", ["수비를 속이는 드리블은 세 가지 단계예요"])
        self.assertIn("one", c)
        self.assertNotIn("order", c, "판정 3회차: '이 순서대로!'는 화면에 순서가 없는 빈 약속")
        self.assertEqual((c["numlist"]["l2"], c["numlist"]["concrete"]), ("3가지면 끝", True), "대사의 '세 가지' → 숫자 약속")
        self.assertEqual(c["result"]["l2"], "수비가 속아요", "드리블 → 결과 약속")
        self.assertTrue(c["beat"]["concrete"])
        self.assertTrue(c["howto"]["vague"] and not c["howto"]["concrete"])
        self.assertFalse(c["why"]["concrete"], "'1대1' 은 주제 낱말이라 숫자 약속이 아님")
        self.assertTrue(c["one"]["stock"] and c["result"]["stock"], "판정 3회차: '딱 1가지'·'수비가 속아요'는 상투 꼬리표")
        self.assertGreater(c["numlist"]["score"], c["one"]["score"])

    def test_numbers_only_from_transcript(self):
        c = self.cands("슈팅 연습", ["디딤발 위치가 중요해요"])
        self.assertNotIn("numlist", c)
        self.assertNotIn("numsec", c)
        self.assertEqual(c["result"]["l2"], "골이 늘어요")
        c2 = self.cands("슈팅 연습", ["3초 안에 차세요"])
        self.assertEqual(c2["numsec"]["l2"], "3초 법칙")

    def test_long_topic_skips_hard_must(self):
        self.assertNotIn("must", self.cands("퍼스트 터치 기본기", ["퍼스트 터치"]), "'퍼스트 터치 어려우면'(9자)은 쇼츠 한 줄을 넘음")
        self.assertIn("must", self.cands("드리블 기본기", ["드리블"]))

    def test_prompt_demands_promise_and_version_in_sig(self):
        p = thumbcopy.prompt.__code__.co_consts
        # 판정 5회차(D-094, 질문 판 6): '구체적인 약속' 대신 말하듯 끝나는 훅 · 라벨 나열 금지 · 답을 다 말하지 않기
        self.assertTrue(any(isinstance(x, str) and "서술어" in x for x in p))
        self.assertTrue(any(isinstance(x, str) and "라벨 나열" in x for x in p))
        self.assertEqual(thumbcopy._sig("x.mp4")[-1], thumbcopy.PROMPT_VER, "질문이 바뀌면 기억한 클로드 문구를 다시 받음")


class GradeTests(unittest.TestCase):
    """판정 2회차 B6: 채도 +46%(레벨만으로) · 낙서 벽에 vib 55 ('과한 형광 필터') · 밝기 0.378."""

    def test_saturated_source_gets_little_vibrance(self):
        import numpy as np
        from PIL import Image
        rng = np.random.default_rng(1)
        a = np.zeros((90, 160, 3), np.uint8)
        a[..., 0] = rng.integers(150, 250, (90, 160))
        a[..., 1] = rng.integers(20, 90, (90, 160))
        a[..., 2] = rng.integers(120, 220, (90, 160))  # 분홍·보라 낙서 벽 (채도 높음)
        g = thumb.auto_grade(Image.fromarray(a))
        self.assertLessEqual(g["vib"], thumb.VIB_HI)

    def test_level_stretch_reduced_when_it_alone_oversaturates(self):
        import numpy as np
        from PIL import Image
        x = np.linspace(40, 215, 160, dtype=np.float32)
        a = np.stack([x, x * 0.92, x * 0.85], -1)[None].repeat(90, 0).astype(np.uint8)  # 회색에 가까운 흐린 장면
        g = thumb.auto_grade(Image.fromarray(a))
        s0 = float(((a.max(2) - a.min(2)) / np.maximum(a.max(2), 1)).mean())
        self.assertLessEqual(thumb._sat_after(a.astype(np.float32), g) / s0, thumb.SAT_MAX + 0.02)

    def test_very_dark_scene_reaches_target(self):
        import numpy as np
        from PIL import Image
        a = (np.random.default_rng(2).random((90, 160, 3)) * 70 + 5).astype(np.uint8)
        g = thumb.auto_grade(Image.fromarray(a))
        self.assertGreater(g["gamma"], 1.8, "감마 상한 2.2 에 걸려 0.378 이던 세로 영상 (판정 q5 1회차 D-097: 아주 어두운 원본은 목표 0.41 이라 감마가 조금 낮음)")
        self.assertLessEqual(g["gamma"], thumb.GAMMA_RANGE[1])

    def test_grade_version_in_candidate_signature(self):
        self.assertGreaterEqual(thumb.GRADE_VER, 2)
        import inspect
        self.assertIn("GRADE_VER", inspect.getsource(thumb._cand_sig))


JS = ("clamp", "srcW", "srcCap", "imgRect", "upOf", "bandCrop", "arEff", "SHORT_ZOOM_CAP", "maxZoom", "panelH", "mainBox", "mainFace", "srcHeadCut", "footClose", "copyFits", "rebalance", "LOUD", "loud", "loudMult", "blurQ", "tangleOf", "biggerRival", "frameQ", "weakScenes", "headlessBad")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => { let a = src.indexOf('function ' + n + '('); if (a < 0) { a = src.indexOf('const ' + n + ' ='); const e = src.indexOf(';\n', a); return src.slice(a, e + 1); }
  let d = 0, i = src.indexOf('{', a); for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]).filter(n => n !== 'clamp');
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var TEXTY = 0.05; var LINE_MAX_FMT = { short: 8, long: 9 }; var INFO = { width: 1280, height: 720 }; var AI = { frames: [], cuts: {}, pick: null };'
  + 'var setAI = o => { Object.assign(AI, o); return null; }; var setInfo = o => { Object.assign(INFO, o); return null; };' + NAMES.map(pick).join('\n') + '; globalThis.T = {setAI, setInfo,' + NAMES.join(',') + '};');
const cases = JSON.parse(fs.readFileSync(0, 'utf8')); const out = cases.map(([fn, args]) => T[fn](...args));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ScreenMath2Tests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_line_length_and_rebalance(self):
        c = {"l1": "퍼스트 터치 어려우면", "l2": "무조건 봐", "emph": [1, 0, 5]}
        out = self.run_js([["copyFits", [c, "short"]], ["copyFits", [{"l1": "수비가 못 막는", "l2": "드리블"}, "short"]], ["rebalance", [c, "short"]], ["rebalance", [{"l1": "고수만 아는 오프더볼의", "l2": "비밀", "emph": [0, 7, 11]}, "long"]],
                           ["rebalance", [{"l1": "발바닥 드래그 못하는", "l2": "진짜 이유", "emph": [1, 0, 5]}, "long"]], ["rebalance", [{"l1": "아주아주긴낱말하나로만", "l2": "끝"}, "short"]]])
        self.assertEqual(out[:2], [False, True])
        self.assertIsNone(out[2], "쇼츠 8자 안으로 못 나누면 쓰지 않음")
        self.assertEqual((out[3]["l1"], out[3]["l2"]), ("고수만 아는", "오프더볼의 비밀"))
        e = out[3]["emph"]
        self.assertEqual([out[3]["l1"], out[3]["l2"]][e[0]][e[1]:e[2]], "오프더볼", "강조 낱말은 다시 찾음")
        self.assertTrue(all(len(t) <= 9 for t in (out[4]["l1"], out[4]["l2"])))
        self.assertIsNone(out[5])

    def test_source_head_cut_and_foot_closeup(self):
        legs = {"main": 0, "persons": [[0.3, 0.0, 0.4, 0.95, 0.9]], "faces": [], "kind": "mid"}
        foot = dict(legs, ball=[0.45, 0.8, 0.08, 0.1])
        far = {"main": 0, "persons": [[0.34, 0.0, 0.4, 0.61, 0.9]], "faces": [], "kind": "mid", "ball": [0.73, 0.49, 0.067, 0.1]}
        self.assertEqual(self.run_js([["footClose", [far]]]), [False], "공이 사람 옆 멀리 · 다리만 나온 장면은 발 클로즈업이 아님")
        whole = {"main": 0, "persons": [[0.3, 0.1, 0.2, 0.8, 0.9]], "faces": [], "kind": "mid"}
        face = dict(legs, faces=[{"box": [0.42, 0.02, 0.1, 0.12]}])
        out = self.run_js([["srcHeadCut", [legs]], ["footClose", [legs]], ["footClose", [foot]], ["headlessBad", [foot]], ["headlessBad", [legs]], ["srcHeadCut", [whole]], ["srcHeadCut", [face]]])
        # 판정 5회차(D-091): 발·공 클로즈업도 장면 자체는 '머리 없음' — 발 이야기 문구일 때만 쓰는 것은 footOk(문구까지 봄)가 정함
        self.assertEqual(out, [True, False, True, True, True, False, False])

    def test_upscale_cap_and_panel_height(self):
        ctx = {"W": 1080, "H": 1920, "ar": 16 / 9}
        out = self.run_js([["srcCap", []], ["panelH", [ctx, 1300]], ["maxZoom", [{"W": 1280, "H": 720, "ar": 16 / 9}]],
                           ["setInfo", [{"width": 1920, "height": 1080}]], ["srcCap", []], ["panelH", [ctx, 1300]], ["panelH", [dict(ctx, ar=9 / 16), 1500]]])
        # 판정 5회차(D-091): 1.3배 넘게 키운 장이 판정에서 깎이지 않음 → 720p 도 1.5배 (판정 q5 1회차 D-096: 롱폼 2.0배는 차이가 흔들림 안이라 그대로)
        self.assertEqual(out[0], 1.5)
        self.assertEqual(out[1], 1080, "720p 가로 장면: 칸 높이 1080 (1.5배)")
        self.assertAlmostEqual(out[2], 1.5)
        self.assertEqual(out[4], 1.5)
        self.assertEqual(out[5], 1300, "1080p: 칸이 남은 높이를 다 써도 1.5배 안")
        self.assertEqual(out[6], 1500, "세로 장면은 폭으로 맞춰져 높이 제한 없음")
        band = self.run_js([["setInfo", [{"width": 1920, "height": 1080}]], ["panelH", [dict(ctx, band="bottom", frame={"bandY": 0.8}), 1900]]])[1]
        self.assertEqual(band, round(1.5 * 1920 * 0.79 / (16 / 9)), "방송 띠를 잘라내면 남은 장면이 더 커지므로 칸을 그만큼 줄임 (판정: 1.66배)")
        lay = {"x": 0, "y": 0, "w": 1080, "h": 972, "fit": "cover", "fx": 0.5, "fy": 0.5}
        up = self.run_js([["setInfo", [{"width": 1280, "height": 720}]], ["upOf", [lay, 16 / 9]]])[1]
        self.assertAlmostEqual(up, 1.35, places=2)

    def test_weak_scenes(self):
        p = [[0.3, 0.2, 0.3, 0.7, 0.9]]
        fr = lambda t, ai: {"t": t, "hash": "0", "ai": ai, "score": 10, "text": 0.3, "blur": 0.1, "kind": "mid", "persons": p, "main": 0, "flags": []}  # noqa: E731
        bad = [fr(i, 1 + i % 4) for i in range(12)]
        good = bad[:11] + [fr(99, 7)]
        out = self.run_js([["setAI", [{"frames": bad}]], ["weakScenes", []], ["setAI", [{"frames": good}]], ["weakScenes", []], ["setAI", [{"frames": bad, "pick": 3}]], ["weakScenes", []]])
        self.assertEqual(out[1::2], [True, False, False], "클로드가 모두 1~4점 → 억지로 6개 안 채움 · 좋은 장면이 하나라도 · 직접 고른 장면은 그대로")


if __name__ == "__main__":
    unittest.main()
