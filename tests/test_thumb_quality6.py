"""썸네일 퀄리티 판정 q5 1회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality6

- 장면(thumb): 주인공이 크고 홀로 또렷한 장면 가산 · 사람 많은 장면 가산 줄임 · 이마가 잘린 얼굴은 '머리 잘림' (D-095·D-098)
- 장면(p8_ai.js, node): 형식(롱폼·쇼츠)마다 캔버스 주인공 키·경쟁자 · 주인공 흔들림 0.15 부터 · 꽉 찬 쇼츠 확대 상한 (D-095·D-096)
- 자르기(p8_ai.js, node): 글자가 주인공 몸·다리를 덮는 비율 · 원본에서 이마가 잘린 얼굴 (D-098)
- 보정(thumb·applyGrade, node): 쨍한 스톡도 채도 절대 목표 · 초록 틀어짐(tint) · 밤 장면 목표 밝기·색 얼룩 줄이기(dn) (D-097)
- 문구(thumbcopy): 레퍼런스 제목을 그대로 가져온 '플랩 세미 가는' 틀 없음 · 영상마다 틀 묶음이 다름 (D-100)
- 화면: 추천 0개일 때 까닭 (D-100)
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
      "interArea", "mainBox", "mainFace", "headBox", "srcHeadCut", "blurQ", "subjQ", "extentOf", "canvasPeople", "TEXTY", "emptyReason")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => {  // 함수 또는 한 문장 상수 (줄 끝 주석 빼고 ';' 로 끝나는 줄까지)
  let a = src.indexOf('function ' + n + '(');
  if (a < 0) { a = src.indexOf('const ' + n + ' ='); if (a < 0) throw new Error('없음 ' + n);
    for (let i = a; ;) { const e = src.indexOf('\n', i); if (/;\s*$/.test(src.slice(i, e).replace(/\s*\/\/.*$/, ''))) return src.slice(a, e); i = e + 1; } }
  let d = 0, i = src.indexOf(') {', a) + 2; for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]);
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var INFO = { width: 1920, height: 1080 }; var AI = { frames: [], dbg: null };' +
  'var frameAspect = () => INFO.width / INFO.height; var setInfo = (w, h) => { INFO = { width: w, height: h }; return true; };' +
  'var setAI = o => { Object.assign(AI, o); return true; };' +
  'var bbox = ls => { const xs = ls.flatMap(l => [l.x, l.x + l.w]), ys = ls.flatMap(l => [l.y, l.y + l.h]); const x = Math.min(...xs), y = Math.min(...ys); return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }; };' +
  NAMES.map(pick).join('\n') + '; globalThis.T = {' + NAMES.join(',') + ', setInfo, setAI};');
const cases = JSON.parse(fs.readFileSync(0, 'utf8')); const out = cases.map(([fn, args]) => (typeof T[fn] === 'function' ? T[fn](...args) : T[fn]));
process.stdout.write(JSON.stringify(out));
"""


class FrameScoreTests(unittest.TestCase):
    def test_protagonist_mult(self):
        lone = [[0.4, 0.2, 0.1, 0.65, 0.9]]
        crowd = [[0.4, 0.3, 0.06, 0.32, 0.9], [0.2, 0.3, 0.06, 0.3, 0.9], [0.6, 0.3, 0.06, 0.29, 0.9], [0.8, 0.3, 0.06, 0.28, 0.9]]
        self.assertGreater(thumb.protagonist_mult(lone, 0), 1.1, "크고 홀로 보이는 주인공")
        self.assertLess(thumb.protagonist_mult(crowd, 0), 0.8, "비슷한 키의 아이들이 흩어진 원경 ('주인공 불분명' 107번)")
        self.assertEqual(thumb.protagonist_mult([], -1), 1.0)
        self.assertLess(thumb.action_score([[0.1 * i, 0.3, 0.05, 0.3, 0.9] for i in range(6)], None), thumb.action_score([[0.3, 0.3, 0.05, 0.3, 0.9], [0.5, 0.3, 0.05, 0.3, 0.9]], None),
                        "사람이 많을수록 더 주지 않음")

    def test_forehead_cut_face_is_headless(self):
        legs = [[0.36, 0.006, 0.18, 0.84, 0.9]]
        self.assertTrue(thumb.headless(legs, None, [{"box": [0.43, 0.0, 0.05, 0.08]}]), "얼굴 상자가 위 끝(0)에 걸림 = 이마가 잘림")
        self.assertFalse(thumb.headless(legs, None, [{"box": [0.43, 0.05, 0.05, 0.08]}]))

    def test_grade_targets(self):
        import numpy as np
        from PIL import Image
        rng = np.random.default_rng(5)
        a = np.zeros((90, 160, 3), np.float32)
        a[..., 1] = rng.integers(120, 200, (90, 160))  # 쨍한 초록 잔디 (채도 0.5 넘음)
        a[..., 0] = a[..., 1] * 0.35
        a[..., 2] = a[..., 1] * 0.3
        g = thumb.auto_grade(Image.fromarray(a.astype("uint8")))
        x = np.asarray(Image.fromarray(a.astype("uint8")).resize((320, 180)), np.float32)
        s0 = float(((x.max(2) - x.min(2)) / np.maximum(x.max(2), 1)).mean())
        self.assertGreater(s0, 0.5)
        self.assertLessEqual(thumb._sat_after(x, g), max(thumb.SAT_ABS_MAX, s0 * thumb.SAT_ABS_FLOOR) + 0.03, "판정 q5 1회차: 0.92배에서 멈춰 0.51 로 남던 것")
        grey = np.full((90, 160, 3), 128, np.float32)
        grey[..., 1] += 14  # 회색이 초록으로 치우침
        self.assertGreater(thumb.auto_grade(Image.fromarray(grey.astype("uint8")))["tint"], 0)
        dark = (np.random.default_rng(6).random((90, 160, 3)) * 50 + 5).astype("uint8")
        gd = thumb.auto_grade(Image.fromarray(dark))
        self.assertGreater(gd["dn"], 0.5, "많이 밝히는 밤 장면은 색 얼룩 줄이기")
        self.assertEqual(thumb.auto_grade(Image.fromarray(np.full((90, 160, 3), 120, np.uint8)))["dn"], 0)


GRADE_JS = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => { const a = src.indexOf('function ' + n + '('); let d = 0, i = src.indexOf('{', a); for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v));' + pick('boxBlur') + pick('applyGrade'));
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const px = new Uint8ClampedArray(inp.px); applyGrade(px, inp.w, inp.h, inp.g);
process.stdout.write(JSON.stringify(Array.from(px)));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어요")
class GradeJsTests(unittest.TestCase):
    def run_js(self, px, w, h, g):
        r = subprocess.run(["node", "-e", GRADE_JS, str(ROOT / "thumb_src/parts/p1_core.js")], input=json.dumps({"px": px, "w": w, "h": h, "g": g}), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_denoise_reduces_dark_colour_noise(self):
        """applyGrade 혼자(판정 도구가 떼어 씀)로도 dn 이 돌고, 어두운 곳의 색 얼룩(채도 흔들림)이 줄어듦."""
        import numpy as np
        rng = np.random.default_rng(7)
        w, h = 96, 64
        a = np.full((h, w, 3), 20, np.float32) + rng.normal(0, 9, (h, w, 3))
        rgba = np.concatenate([np.clip(a, 0, 255), np.full((h, w, 1), 255)], 2).astype(np.uint8)
        base = {"on": True, "amt": 1, "lo": [0, 0, 0], "hi": [255, 255, 255], "gamma": 2.6, "vib": 0, "clarity": 0, "temp": 0, "tint": 0, "sharpen": 0}
        out0 = np.array(json.loads(self.run_js(rgba.flatten().tolist(), w, h, dict(base, dn=0))), np.float32).reshape(h, w, 4)[..., :3]
        out1 = np.array(json.loads(self.run_js(rgba.flatten().tolist(), w, h, dict(base, dn=1))), np.float32).reshape(h, w, 4)[..., :3]
        chroma = lambda o: float((o.max(2) - o.min(2)).mean())  # noqa: E731
        self.assertLess(chroma(out1), chroma(out0) * 0.6)
        self.assertAlmostEqual(float(out1.mean()), float(out0.mean()), delta=12, msg="밝기는 그대로")

    def test_tint_moves_green(self):
        import numpy as np
        px = np.array([[100, 130, 100, 255]] * 16, np.uint8).flatten().tolist()
        g = {"on": True, "amt": 1, "lo": [0, 0, 0], "hi": [255, 255, 255], "gamma": 1, "vib": 0, "clarity": 0, "temp": 0, "tint": 20, "dn": 0, "sharpen": 0}
        out = json.loads(self.run_js(px, 4, 4, g))
        self.assertLess(out[1], 130)
        self.assertEqual(out[0], 100)


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ScreenMath6Tests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_subject_quality_per_format(self):
        big = {"t": 1, "persons": [[0.45, 0.15, 0.1, 0.7, 0.9]], "main": 0, "faces": [], "kind": "mid", "blur": 0.05}
        small = {"t": 2, "persons": [[0.45, 0.5, 0.04, 0.22, 0.9]], "main": 0, "faces": [], "kind": "wide", "blur": 0.05}
        crowd = {"t": 3, "persons": [[0.45, 0.4, 0.08, 0.4, 0.9], [0.5, 0.4, 0.08, 0.38, 0.9], [0.4, 0.42, 0.08, 0.36, 0.9]], "main": 0, "faces": [], "kind": "wide", "blur": 0.05}
        far = dict(crowd, persons=[[0.45, 0.4, 0.08, 0.4, 0.9], [0.05, 0.42, 0.08, 0.38, 0.9]])
        out = self.run_js([["subjQ", [big, "short"]], ["subjQ", [small, "short"]], ["subjQ", [crowd, "short"]], ["subjQ", [far, "short"]], ["subjQ", [big, "long"]], ["subjQ", [small, "long"]]])
        self.assertGreater(out[0], out[1] + 0.15, "쇼츠: 원래 큰 주인공 먼저 (작은 주인공은 키워도 덜)")
        self.assertLess(out[2], out[3], "쇼츠: 9:16 창 안의 비슷한 키 경쟁자만 깎음 (멀리 옆 사람은 창 밖)")
        self.assertGreater(out[4], out[5])
        self.assertLess(out[0] - out[1], 0.9)

    def test_blur_quality_from_015(self):
        out = self.run_js([["blurQ", [{"blur": 0.1}]], ["blurQ", [{"blur": 0.25}]], ["blurQ", [{"blur": 0.5}]]])
        self.assertEqual(out[0], 1)
        self.assertLess(out[1], 0.9, "0.15~0.35 도 '흐림' 지적 절반 (판정 q5 1회차)")
        self.assertGreaterEqual(out[2], 0.55)

    def test_short_zoom_cap(self):
        ctx = {"W": 1080, "H": 1920, "short": True, "ar": 16 / 9, "band": None, "frame": {}}
        out = self.run_js([["setInfo", [1920, 1080]], ["maxZoom", [ctx]], ["setInfo", [1280, 720]], ["maxZoom", [ctx]], ["fullOk", [ctx]],
                           ["maxZoom", [{"W": 1280, "H": 720, "short": False, "ar": 16 / 9, "frame": {}}]]])
        self.assertAlmostEqual(out[1], 3.6 / (1920 * 16 / 9 / 1920), places=3, msg="1080p 가로 → 꽉 찬 쇼츠에서 덮기 1.78배 위로 2배까지 더 (주인공을 크게)")
        self.assertAlmostEqual(out[3], 3.6 / (1920 * 16 / 9 / 1280), places=3)
        self.assertTrue(out[4], "720p 가로도 꽉 찬 쇼츠")
        self.assertAlmostEqual(out[5], 1.5, msg="롱폼 720p 는 1.5배 그대로")

    def test_forehead_cut_face_counts_as_head_cut(self):
        f = {"persons": [[0.36, 0.006, 0.18, 0.84, 0.9]], "main": 0, "faces": [{"box": [0.427, 0, 0.046, 0.085]}]}
        ok = dict(f, faces=[{"box": [0.427, 0.03, 0.046, 0.085]}])
        self.assertEqual(self.run_js([["srcHeadCut", [f]], ["srcHeadCut", [ok]]]), [True, False])

    def test_canvas_people_body_cover_and_rivals(self):
        f = {"persons": [[0.5, 0.1, 0.12, 0.8, 0.9], [0.2, 0.15, 0.1, 0.7, 0.9], [0.8, 0.6, 0.03, 0.1, 0.9]], "main": 0, "faces": [{"box": [0.54, 0.12, 0.04, 0.08]}]}
        geo = {"x": 0, "y": 0, "w": 1280, "h": 720, "fit": "cover", "fx": 0.5, "fy": 0.5}
        legs = {"type": "text", "name": "제목 큰 줄", "x": 500, "y": 450, "w": 700, "h": 200, "shadow": {"on": False}, "glow": {"on": False}, "outline": {"on": False}}
        top = dict(legs, y=10, h=60, x=20, w=300)
        doc = lambda t: {"w": 1280, "h": 720, "layers": [geo, t]}  # noqa: E731
        out = self.run_js([["canvasPeople", [doc(legs), f, geo, 16 / 9]], ["canvasPeople", [doc(top), f, geo, 16 / 9]]])
        self.assertGreater(out[0]["legCov"], 0.5, "아래 제목이 다리를 덮음 (쌈바형 13장)")
        self.assertGreater(out[0]["bodyCov"], 0.3)
        self.assertEqual(out[1]["bodyCov"], 0)
        self.assertEqual(out[0]["rivals"], 1, "주인공 키의 60% 넘는 다른 사람 1명 (작은 사람은 뺌)")

    def test_empty_reason(self):
        texty = [{"t": 1, "text": 0.2, "persons": [[0.4, 0.2, 0.1, 0.5]]}, {"t": 2, "text": 0.1, "persons": []}]
        out = self.run_js([["setAI", [{"frames": texty}]], ["emptyReason", []], ["setAI", [{"frames": [{"t": 1, "text": 0, "persons": []}]}]], ["emptyReason", []],
                           ["setAI", [{"frames": [{"t": 1, "text": 0, "persons": [[0.4, 0.2, 0.1, 0.5]]}], "dbg": {"byTpl": {"a": [0, 3, ["주인공 머리 잘림"]]}}}]], ["emptyReason", []]])
        self.assertIn("큰 글자", out[1])
        self.assertIn("사람", out[3])
        self.assertIn("머리가 잘린", out[5])


class CopyTests(unittest.TestCase):
    TEXTS = ["오늘은 콘 드리블 훈련을 해볼게요", "발 안쪽 바깥쪽을 번갈아 쓰세요", "고개 들고 드리블하는 습관이 제일 중요해요"]

    def test_no_copied_reference_title(self):
        for title in ("유소년 드리블 훈련", "발바닥 드래그 기본기", "1대1 돌파 이렇게 하세요", "오프더볼"):
            C, _ = tc.rule_candidates(title, self.TEXTS, rotate=False)
            self.assertFalse(any("플랩 세미" in c["l1"] + c["l2"] for c in C), title)

    def test_spin_is_stable_and_bounded(self):
        a = tc.spin("발바닥 드래그 기본기", "regret")
        self.assertEqual(a, tc.spin("발바닥 드래그 기본기", "regret"))
        self.assertLessEqual(abs(a), tc.HOOK_SPIN / 2 + 1e-9)
        vals = {round(tc.spin(t, "regret"), 3) for t in ("가", "나", "다", "라", "마")}
        self.assertGreater(len(vals), 2)


if __name__ == "__main__":
    unittest.main()
