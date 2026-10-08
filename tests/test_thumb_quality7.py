"""썸네일 퀄리티 판정 q5 2회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality7

- 보정(thumb): 채도를 원본의 0.95배 아래로는 빼지 않음 (D-101)
- 장면(p8_ai.js, node): 사람·얼굴이 없는 장면은 사람 장면이 3장 넘으면 안 씀 · 사람끼리 엉킨 장면·저녁/밤 장면 감점 (D-102)
- 그래픽(p8_ai.js, node): 그래픽이 있는 장인지(hasGfx) · 장마다 돌려 넣는 그래픽 종류 (D-103)
- 자르기(p8_ai.js, node): 옆 끝에서 반쯤 잘린 사람 · 위 끝에서 잘린 머리 · 발 가까이 공이 잘림 · 제목 아래 머리 자리 (D-105)
- 문구(thumbcopy): 같은 말투 훅 틀 4개 더 (D-107)
인터넷·진짜 클로드·모델은 쓰지 않음."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thumb  # noqa: E402
import thumbcopy as tc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None

JS = ("srcW", "srcCap", "SHORT_FULL_CAP", "SHORT_ZOOM_CAP", "bandCrop", "arEff", "maxZoom", "coverUp", "fullOk", "imgRect", "f2c", "boxC", "clipTo", "areaOf",
      "interArea", "mainBox", "mainFace", "headBox", "srcHeadCut", "headlessBad", "footClose", "blurQ", "subjQ", "TEXTY", "LOUD", "loud", "loudMult", "frameQ",
      "hamming", "sameFace", "sceneGroups", "usableFrame", "isExpr", "aiFrames", "edgeCutCost", "GFX_NAME", "hasGfx", "DECOR", "UNDER_TITLE", "underTitle", "WIDE_MIN")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => {  // 함수 또는 한 문장 상수 (줄 끝 주석 빼고 ';' 로 끝나는 줄까지)
  let a = src.indexOf('function ' + n + '(');
  if (a < 0) { a = src.indexOf('const ' + n + ' ='); if (a < 0) throw new Error('없음 ' + n);
    for (let i = a; ;) { const e = src.indexOf('\n', i); if (/;\s*$/.test(src.slice(i, e).replace(/\s*\/\/.*$/, ''))) return src.slice(a, e); i = e + 1; } }
  let d = 0, i = src.indexOf(') {', a) + 2; for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]);
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var INFO = { width: 1920, height: 1080 }; var AI = { frames: [], cuts: {}, dbg: null, pick: null };' +
  'var TAC_DEF = { arrow2: {}, pass: {}, ring: {}, spot: {}, xmark: {}, scribble: {}, marker: {}, sparkle: {} };' +
  'var annotLayers = () => null, ballCircle = () => null, passArrow = () => null, spaceArrow = () => null;' +
  'var frameAspect = () => INFO.width / INFO.height; var setInfo = (w, h) => { INFO = { width: w, height: h }; return true; };' +
  'var setAI = o => { Object.assign(AI, o); return true; };' +
  NAMES.map(pick).join('\n') + '\n; globalThis.T = {' + NAMES.join(',') + ', setInfo, setAI};');  // 마지막 상수 줄 끝 주석이 뒤를 먹지 않게 줄을 바꿈
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = cases.map(([fn, args]) => { const r = typeof T[fn] === 'function' ? T[fn](...args) : T[fn]; return fn === 'aiFrames' ? r.map(f => f.t) : fn === 'DECOR' ? r.map(d => [d[0], d[1]]) : r; });
process.stdout.write(JSON.stringify(out));
"""


class GradeSatTests(unittest.TestCase):
    def test_saturated_scene_not_desaturated(self):
        """판정 q5 2회차(D-101): 짝 판정에서 채도를 덜 뺀 쪽이 프로 59% → 쨍한 원본도 0.95배 아래로는 안 뺌 (예전 0.62배)."""
        import numpy as np
        from PIL import Image
        rng = np.random.default_rng(5)
        a = np.zeros((90, 160, 3), np.float32)
        a[..., 1] = rng.integers(120, 200, (90, 160))  # 쨍한 초록 잔디 (채도 0.5 넘음)
        a[..., 0] = a[..., 1] * 0.35
        a[..., 2] = a[..., 1] * 0.3
        im = Image.fromarray(a.astype("uint8"))
        g = thumb.auto_grade(im)
        x = np.asarray(im.resize((320, 180)), np.float32)
        s0 = float(((x.max(2) - x.min(2)) / np.maximum(x.max(2), 1)).mean())
        self.assertGreaterEqual(thumb._sat_after(x, g) / s0, 0.9)
        self.assertGreaterEqual(g.get("sat", 1), thumb.SAT_ABS_FLOOR)
        self.assertGreaterEqual(thumb.SAT_ABS_FLOOR, 0.95)
        self.assertGreaterEqual(thumb.GRADE_VER, 8, "보정 계산이 바뀌면 장면 후보를 다시 고름")

    def test_more_auto_cuts(self):
        self.assertGreaterEqual(thumb.AUTO_CUTS, 6, "누끼 템플릿(액션 누끼·쇼츠 누끼 크게)이 쓸 장면을 넉넉히 (D-106)")


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ScreenMath7Tests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    CTX = {"W": 1280, "H": 720, "short": False, "ar": 16 / 9, "band": None}
    BG = {"x": 0, "y": 0, "w": 1280, "h": 720, "fit": "cover", "fx": 0.5, "fy": 0.5}

    def test_edge_cut_cost(self):
        m = [0.4, 0.2, 0.12, 0.6, 0.9]
        whole = {"persons": [m, [0.6, 0.25, 0.1, 0.5, 0.9]], "ball": None}
        half = {"persons": [m, [0.95, 0.25, 0.1, 0.5, 0.9]], "ball": None}  # 오른쪽 끝에서 반쯤 잘린 큰 사람
        head = {"persons": [m, [0.7, 0.05, 0.1, 0.5, 0.9]], "ball": None}
        ballcut = {"persons": [[0.4, 0.3, 0.12, 0.65, 0.9]], "ball": [0.47, 0.95, 0.03, 0.05, 0.6]}  # 발 가까이 공이 아래 끝에 걸림
        lay = dict(self.BG, y=-60, h=780)  # 위로 60px 넘침 → 위 끝에 걸린 머리
        out = self.run_js([["edgeCutCost", [self.CTX, self.BG, whole, m]], ["edgeCutCost", [self.CTX, self.BG, half, m]],
                           ["edgeCutCost", [self.CTX, lay, head, m]], ["edgeCutCost", [self.CTX, self.BG, ballcut, ballcut["persons"][0]]]])
        self.assertEqual(out[0], 0, "모두 화면 안")
        self.assertGreater(out[1], 0.2, "옆 끝에서 반쯤 잘린 사람 (쇼츠 묶음 안 pro −0.34)")
        self.assertGreater(out[2], 0.3, "원본엔 있는 다른 사람 머리가 위 끝에서 잘림")
        self.assertGreaterEqual(out[3], 1.2, "주인공 발 가까이 공이 아래 끝에 걸림")

    def test_frame_quality_tangle_and_night(self):
        base = {"t": 1, "score": 10, "persons": [[0.4, 0.2, 0.12, 0.6, 0.9]], "main": 0, "kind": "mid", "blur": 0.05, "flags": [], "grade": {"gamma": 1.1}}
        tangle = dict(base, persons=[[0.4, 0.2, 0.12, 0.6, 0.9], [0.43, 0.22, 0.12, 0.58, 0.9]])
        apart = dict(base, persons=[[0.4, 0.2, 0.12, 0.6, 0.9], [0.7, 0.22, 0.12, 0.58, 0.9]])
        night = dict(base, grade={"gamma": 1.9})
        out = self.run_js([["frameQ", [base, 10]], ["frameQ", [tangle, 10]], ["frameQ", [apart, 10]], ["frameQ", [night, 10]]])
        self.assertLess(out[1], out[2] * 0.9, "두 사람이 몸이 겹친 장면 ('누가 주인공인지 모름' · 엉킴 22%)")
        self.assertAlmostEqual(out[2], out[0], places=6, msg="떨어져 선 동료는 그대로")
        self.assertAlmostEqual(out[3], out[0] * 0.85, places=6, msg="감마 1.6 넘게 밝힌 저녁·밤 장면 ×0.85")

    def test_frames_skip_empty_scenes(self):
        p = lambda t, x: {"t": t, "score": 10 - t, "persons": [[x, 0.2, 0.12, 0.6, 0.9]], "main": 0, "kind": "mid", "blur": 0.05, "flags": [], "hash": "%016x" % (t * 0x1111111111)}  # noqa: E731
        empty = {"t": 0.5, "score": 20, "persons": [], "main": -1, "kind": "scene", "blur": 0.05, "flags": [], "hash": "ffffffffffffffff"}
        three = [p(1, 0.1), p(2, 0.4), p(3, 0.7)]
        out = self.run_js([["setAI", [{"frames": three + [empty], "cuts": {}}]], ["aiFrames", [7, "long"]],
                           ["setAI", [{"frames": three[:2] + [empty], "cuts": {}}]], ["aiFrames", [7, "long"]]])
        self.assertNotIn(0.5, out[1], "사람 장면이 3장 넘으면 빈 경기장 원경(판정 pro 3.0)은 안 씀")
        self.assertIn(0.5, out[3], "사람 장면이 모자라면 예전처럼 1장까지")

    def test_has_graphic_and_rotation(self):
        doc = lambda *ls: {"doc": {"layers": list(ls)}}  # noqa: E731
        circle = {"type": "shape", "shape": "ellipse", "name": "표시 동그라미"}
        arrow = {"type": "shape", "shape": "arrow2", "name": "패스 화살표"}
        logo = {"type": "image", "name": "로고"}
        title = {"type": "text", "name": "제목 큰 줄"}
        out = self.run_js([["hasGfx", [doc(title, logo)]], ["hasGfx", [doc(title, circle)]], ["hasGfx", [doc(arrow)]], ["hasGfx", [doc({"type": "text", "name": "설명 글자"})]], ["DECOR", []]])
        self.assertEqual(out[:4], [False, True, True, True], "로고·제목만 있는 장은 그래픽 없음")
        kinds = [k for k, _ in out[4]]
        self.assertEqual(set(kinds), {"annot", "circle", "pass", "space"}, "짝 판정에서 이긴 그래픽만 (쇼츠 손그림 화살표는 뺌 · 40%)")

    def test_head_gap_under_title(self):
        ctx = {"W": 1080, "H": 1920, "short": True}
        out = self.run_js([["underTitle", [ctx, {"box": {"x": 0, "y": 134, "w": 900, "h": 400}}]], ["WIDE_MIN", []]])
        self.assertAlmostEqual(out[0]["headY"], 534 + 1920 * 0.05, places=3, msg="제목 아래 머리 자리 0.05H (판정 '머리가 제목 바로 아래에 붙어 답답')")
        self.assertAlmostEqual(out[0]["minHeadY"], 534 + 1920 * 0.025, places=3)
        self.assertGreaterEqual(out[1], 80, "6개를 채우려고 넓혀 고를 때는 약한 장을 넣지 않음")


class CopyHookTests(unittest.TestCase):
    TEXTS = ["1대1 돌파 이렇게 하세요", "수비 앞에서 속도를 줄였다가 확 치고 나가는 거예요", "실수하는 분들이 많은데 공을 뺏기면 안 돼요", "이것만 알면 1대1 무조건 이겨요"]

    def test_new_hooks(self):
        C, tp = tc.rule_candidates("1대1 돌파 이렇게 하세요", self.TEXTS, rotate=False)
        pids = {c["pid"] for c in C}
        self.assertTrue({"trait", "first", "freeze", "wrong"} <= pids, pids)
        for c in C:
            if c["pid"] in ("trait", "first", "freeze", "wrong"):
                self.assertLessEqual(len(c["l1"]), 8, c)  # 쇼츠 한 줄 8자 안
                self.assertLessEqual(len(c["l2"]), 8, c)
        self.assertTrue({"trait", "first", "freeze", "wrong"} <= set(tc.HOOK_PIDS))

    def test_rotation_keeps_subset(self):
        C, _ = tc.rule_candidates("1대1 돌파 이렇게 하세요", self.TEXTS, rotate=True)
        hooks = {c["pid"] for c in C if c["pid"] in tc.HOOK_PIDS}
        self.assertLessEqual(len(hooks), tc.HOOK_KEEP, "영상마다 훅 틀 일부만 (채널 목록에서 덜 되풀이)")


if __name__ == "__main__":
    unittest.main()
