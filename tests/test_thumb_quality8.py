"""썸네일 판정 q5 2회차 검토 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality8

- 공 동그라미(p8_ai.js, node): 믿을 만한 공만(손목 띠 0.27 은 아님) · 누끼를 따로 옮긴 틀에는 안 넣음 · 쇼츠 버튼·제목 자리·로고를 피함 · '여기' 문구는 공이 없으면 발 쪽 (D-119)
- 그래픽 돌려 넣기(decorate): 장마다 한 종류씩 차례로 · 종류마다 묶음 상한 · 이미 그래픽이 있는 장은 그대로 (D-114 · D-119)
- 6개 채우기(wideWorth·wideOk): 남은 문구·장면이 있어야 · 장면 묶음 때문에 모자라면 장면이 더 없을 때는 다시 안 함 · 모든 장 80점 넘을 때만 받음 (D-118 · D-119)
- 쇼츠 누끼 크게(ghostOf·cutCrowded·actionPose): 배경의 같은 주인공이 누끼 밖으로 보임 · 남의 머리가 누끼에 들어감 · 서 있기만 한 장면 (D-119)
- 엉킴(tangleOf)·더 큰 다른 사람(biggerRival) · 칸 자르기(bakeCrop) · 쓸 만한 장면 없음(weakScenes) · 누끼 대상 모자랄 때(thumb._cut_targets) (D-119)
인터넷·진짜 클로드·모델은 쓰지 않음."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thumb  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None

JS = ("SAFE", "TEXTY", "clipTo", "areaOf", "interArea", "imgRect", "f2c", "boxC", "mainBox", "mainFace", "headBox", "headsOf", "overlap", "inSafe", "extentOf",
      "BALL_CONF", "ballSure", "sameBox", "markAvoid", "circleAt", "ballCircle", "HERE_COPY", "hereMark", "feetCircle", "GFX_NAME", "hasGfx", "DECOR", "decorate",
      "WIDE_MIN", "wideWorth", "wideOk", "GHOST_MAX", "ghostOf", "cutCrowded", "actionPose", "tangleOf", "biggerRival", "bakeCrop", "weakScenes")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => {  // 함수 또는 한 문장 상수 (줄 끝 주석 빼고 ';' 로 끝나는 줄까지)
  let a = src.indexOf('function ' + n + '(');
  if (a < 0) { a = src.indexOf('const ' + n + ' ='); if (a < 0) throw new Error('없음 ' + n);
    for (let i = a; ;) { const e = src.indexOf('\n', i); if (/;\s*$/.test(src.slice(i, e).replace(/\s*\/\/.*$/, ''))) return src.slice(a, e); i = e + 1; } }
  let d = 0, i = src.indexOf(') {', a) + 2; for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]);
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var INFO = { width: 1920, height: 1080 }; var AI = { frames: [], cuts: {}, dbg: null, pick: null, copySel: null };' +
  'var TAC_DEF = { arrow2: {}, pass: {}, ring: {}, spot: {}, xmark: {}, scribble: {}, marker: {}, sparkle: {} };' +
  'var L = (type, o) => Object.assign({ type, rot: 0, hidden: false, shadow: { on: false }, glow: { on: false }, outline: { on: false } }, o); var normLayer = l => l;' +
  'var bbox = ls => { const xs = ls.flatMap(l => [l.x, l.x + l.w]), ys = ls.flatMap(l => [l.y, l.y + l.h]); const x = Math.min(...xs), y = Math.min(...ys); return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }; };' +
  'var STUB = {}; var annotLayers = x => STUB.annot ? [{ type: "text", name: "설명 글자", x: 0, y: 0, w: 1, h: 1 }] : null;' +
  'var passArrow = x => STUB.pass ? [{ type: "shape", shape: "arrow2", name: "패스 화살표", x: 0, y: 0, w: 1, h: 1 }] : null;' +
  'var spaceArrow = x => STUB.space ? [{ type: "shape", shape: "marker", name: "공간 칩", x: 0, y: 0, w: 1, h: 1 }] : null;' +
  'var frameQ = () => 0.9; var tryProp = (x, ls) => { x.doc.layers.push(...ls); return true; }; var emojiLayer = () => null; var propAvoid = () => []; var AUTO_BADGE = false;' +
  'var frameAspect = () => INFO.width / INFO.height; var setInfo = (w, h) => { INFO = { width: w, height: h }; return true; };' +
  'var setAI = o => { Object.assign(AI, o); return true; }; var setStub = o => { STUB = o; return true; };' +
  NAMES.map(pick).join('\n') + '\n; globalThis.T = {' + NAMES.join(',') + ', setInfo, setAI, setStub};');
// 묶음 꾸미기: 장마다 붙은 그래픽 이름을 돌려줌 (ballCircle 은 진짜 함수 — 공이 없는 장면이면 STUB 이 아닌 진짜 결과)
const run = (fn, args) => {
  if (fn === 'decorateNames') { const out = args[0]; T.decorate(out, args[1]); return out.map(x => x.doc.layers.map(l => l.name).filter(n => n !== '배경' && !/^제목/.test(n))); }
  const r = typeof T[fn] === 'function' ? T[fn](...args) : T[fn];
  if (fn === 'ballCircle' || fn === 'hereMark' || fn === 'feetCircle') return r ? r.map(l => ({ name: l.name, x: l.x, y: l.y, w: l.w, h: l.h })) : null;
  return fn === 'DECOR' ? r.map(d => [d[0], d[1]]) : r;
};
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(([fn, args]) => run(fn, args))));
"""

BRAND = {"colors": {"hl": "#FFE14D", "hl2": "#FFFFFF", "accent": "#FF3B30", "neon": "#00D1FF", "box": "#111111"}}


def bg(w, h):
    return {"type": "image", "name": "배경", "x": 0, "y": 0, "w": w, "h": h, "fit": "cover", "fx": 0.5, "fy": 0.5}


def item(frame, short=False, copy=None, extra=()):
    W, H = (1080, 1920) if short else (1280, 720)
    ar = 16 / 9
    layers = [bg(W, H), {"type": "text", "name": "제목 큰 줄", "x": W * 0.07, "y": H * 0.07, "w": W * 0.8, "h": H * 0.12, "shadow": {"on": False}, "glow": {"on": False}, "outline": {"on": False}}]
    return {"ctx": {"W": W, "H": H, "short": short, "ar": ar, "frame": frame, "brand": BRAND}, "doc": {"w": W, "h": H, "layers": layers + list(extra)},
            "copy": copy or {"l1": "드리블", "l2": "이렇게"}}


M = [0.4, 0.25, 0.12, 0.6, 0.9]  # 주인공 (발은 0.85)


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class Review8Tests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_ball_sure(self):
        fr = lambda ball: {"persons": [M], "main": 0, "ball": ball, "kind": "mid"}  # noqa: E731
        out = self.run_js([["ballSure", [fr([0.45, 0.79, 0.03, 0.05, 0.8])]],
                           ["ballSure", [fr([0.45, 0.49, 0.02, 0.03, 0.271])]],   # 손목 띠 (키의 46% 높이 · 확률 0.27 — 001_short_3)
                           ["ballSure", [fr([0.45, 0.82, 0.03, 0.05, 0.213])]],   # 가까이 찍은 진짜 공 (발 옆 · 확률 0.21 — 003_short_2)
                           ["ballSure", [fr([0.9, 0.82, 0.03, 0.05, 0.3])]],      # 발 높이지만 주인공에서 먼 낮은 확률
                           ["ballSure", [fr(None)]]])
        self.assertEqual(out, [True, False, True, False, False])

    def test_ball_circle_conditions(self):
        good = {"persons": [M], "main": 0, "ball": [0.45, 0.78, 0.03, 0.05, 0.8], "kind": "mid"}
        wrist = dict(good, ball=[0.45, 0.49, 0.02, 0.03, 0.271])
        moved_cut = {"type": "image", "name": "누끼", "x": 300, "y": 20, "w": 900, "h": 506}
        aligned_cut = dict(bg(1280, 720), name="누끼")
        logo = {"type": "image", "name": "로고", "x": 560, "y": 520, "w": 140, "h": 140, "shadow": {"on": False}, "glow": {"on": False}, "outline": {"on": False}}
        low_short = dict(good, persons=[[0.4, 0.3, 0.12, 0.62, 0.9]], ball=[0.47, 0.86, 0.03, 0.05, 0.8])  # 쇼츠 공이 0.88H — 버튼·제목 자리
        out = self.run_js([["ballCircle", [item(good)]], ["ballCircle", [item(wrist)]], ["ballCircle", [item(good, extra=[moved_cut])]],
                           ["ballCircle", [item(good, extra=[aligned_cut])]], ["ballCircle", [item(good, extra=[logo])]], ["ballCircle", [item(low_short, short=True)]]])
        self.assertTrue(out[0], "또렷한 공")
        self.assertIsNone(out[1], "손목 띠(확률 0.27) — '빨간 원이 손목을 가리켜'")
        self.assertIsNone(out[2], "누끼를 따로 옮긴 틀: 배경 공은 흐린 판에 있음 — '빨간 원이 빈 땅을 가리켜'")
        self.assertTrue(out[3], "배경과 같은 자리 누끼는 그대로 맞음")
        self.assertIsNone(out[4], "로고와 겹치면 안 넣음")
        self.assertIsNone(out[5], "쇼츠 버튼·제목 자리(0.76H 아래)")
        c = out[0][0]
        self.assertAlmostEqual(c["x"] + c["w"] / 2, (0.45 + 0.015) * 1280, delta=2)

    def test_here_mark_fallback(self):
        noball = {"persons": [M], "main": 0, "ball": None, "kind": "mid"}
        here = {"l1": "'여기' 봐야", "l2": "뚫립니다"}
        out = self.run_js([["hereMark", [item(noball, copy=here)]], ["hereMark", [item(noball)]]])
        self.assertTrue(out[0], "'여기' 문구에 공이 없으면 주인공 발 쪽 동그라미 ('여기가 어딘지 안 보임')")
        self.assertGreater(out[0][0]["y"] + out[0][0]["h"] / 2, 720 * 0.75, "발 높이")
        self.assertIsNone(out[1], "'여기' 문구가 아니면 없음")

    def test_decorate_rotation_and_caps(self):
        noball = {"persons": [M], "main": 0, "ball": None, "kind": "mid"}
        ball = dict(noball, ball=[0.45, 0.78, 0.03, 0.05, 0.8])
        arrow = {"type": "shape", "shape": "arrow2", "name": "곡선 화살표", "x": 0, "y": 0, "w": 1, "h": 1}
        items = [item(ball) for _ in range(8)] + [item(ball, extra=[arrow])]
        out = self.run_js([["setStub", [{"annot": True, "pass": True, "space": True}]], ["decorateNames", [items, "short"]]])[1]
        kinds = [n[0] if n else None for n in out]
        self.assertEqual(kinds[:8], ["설명 글자", "표시 동그라미", "패스 화살표", "공간 칩", "설명 글자", "표시 동그라미", "패스 화살표", "표시 동그라미"],
                         "장마다 한 칸씩 돌려서 · 공간 칩 1 · 설명 글자 2 · 패스 2 상한 뒤엔 다음 종류")
        self.assertEqual(out[8], ["곡선 화살표"], "이미 전술 그래픽이 있는 장은 그대로")
        out = self.run_js([["setStub", [{}]], ["decorateNames", [[item(noball), item(noball)], "short"]]])[1]
        self.assertEqual(out, [[], []], "넣을 수 있는 그래픽이 없으면 빈 장 그대로 (억지로 안 넣음)")

    def test_wide_retry_rules(self):
        s = lambda *xs: [{"score": x} for x in xs]  # noqa: E731
        out = self.run_js([["wideOk", [s(90, 85, 81, 95, 88, 92), s(90, 85, 81, 95, 88)]], ["wideOk", [s(90, 85, 79, 95, 88, 92), s(90, 85, 81, 95, 88)]],
                           ["wideOk", [s(90, 85, 81), s(90, 85, 81)]],
                           ["wideWorth", [{"why": {"scene": 3, "tplScene": 2}}, True, False]], ["wideWorth", [{"why": {"scene": 3}}, True, True]],
                           ["wideWorth", [{"why": {"l2": 4, "scene": 1}}, True, False]], ["wideWorth", [{"why": {"l2": 4}}, False, False]]])
        self.assertEqual(out[:3], [True, False, False], "더 채우고 모든 장 80점 넘을 때만 (79점 한 장이면 버림)")
        self.assertEqual(out[3:], [False, True, True, False], "장면 묶음 때문에 모자라고 장면이 더 없으면 문구만 늘려 봐야 소용없음 · 남은 문구·장면이 없으면 안 함")

    def test_cut_out_template_guards(self):
        ctx = {"W": 1080, "H": 1920, "short": True, "ar": 16 / 9, "frame": {"persons": [[0.4, 0.2, 0.15, 0.5, 0.9]], "main": 0, "kind": "mid"}}
        cut = {"type": "image", "name": "누끼", "x": -600, "y": 400, "w": 2400, "h": 1350, "fit": "cover", "fx": 0.5, "fy": 0.5}  # 주인공 키 675px
        big_bg = dict(bg(3413, 1920), x=-1200)   # 배경 주인공 960px > 누끼 675px
        hid_bg = {"type": "image", "name": "배경", "x": -410, "y": 501.25, "w": 2000, "h": 1125, "fit": "cover", "fx": 0.5, "fy": 0.5}  # 누끼와 같은 가운데 · 562px
        neighbor = {"persons": [[0.4, 0.2, 0.15, 0.5, 0.9], [0.47, 0.18, 0.1, 0.45, 0.8]], "main": 0, "kind": "mid", "faces": []}
        alone = {"persons": [[0.4, 0.2, 0.15, 0.5, 0.9], [0.8, 0.2, 0.1, 0.45, 0.8]], "main": 0, "kind": "mid", "faces": []}
        stand = {"persons": [[0.4, 0.12, 0.13, 0.74, 0.9]], "main": 0, "kind": "mid", "flags": [], "ball": None}
        lesson = dict(stand, flags=["lesson"])
        out = self.run_js([["ghostOf", [ctx, big_bg, cut]], ["ghostOf", [ctx, hid_bg, cut]], ["ghostOf", [ctx, cut, cut]],
                           ["cutCrowded", [neighbor]], ["cutCrowded", [alone]], ["actionPose", [stand]], ["actionPose", [lesson]]])
        self.assertEqual(out[0], 1, "배경 주인공이 누끼보다 큼 — '뒤에 겹친 흐린 같은 사람' (005·006 쇼츠)")
        self.assertLess(out[1], 0.25, "가운데를 맞춘 작은 배경 주인공은 누끼 뒤에 숨음")
        self.assertEqual(out[2], 0, "같은 자리 누끼")
        self.assertEqual(out[3:5], [True, False], "누끼를 따는 상자 안에 남의 머리 (005_short_3)")
        self.assertEqual(out[5:], [False, True], "서 있기만 한 사람 (006 '가만히 서 있는 누끼') · 공 다루는 순간")

    def test_tangle_both_sides_and_bigger_rival(self):
        t377 = {"persons": [[0.417, 0.278, 0.095, 0.268], [0.27, 0.269, 0.053, 0.217], [0.413, 0.35, 0.04, 0.186], [0.634, 0.309, 0.042, 0.182]], "main": 0, "kind": "wide"}
        t344 = {"persons": [[0.5277, 0.2996, 0.1587, 0.3894], [0.2668, 0.306, 0.1329, 0.3529], [0.4969, 0.2774, 0.0509, 0.1951]], "main": 1, "kind": "wide"}
        t536 = {"persons": [[0.365, 0.229, 0.087, 0.452], [0.194, 0.287, 0.093, 0.398], [0.025, 0.39, 0.055, 0.265], [0.302, 0.437, 0.078, 0.232]], "main": 3, "kind": "mid"}
        out = self.run_js([["tangleOf", [t377]], ["tangleOf", [t344]], ["biggerRival", [t536]], ["biggerRival", [t344]]])
        self.assertGreater(out[0], 0.8, "뒤에 가려진 사람(작은 상자)의 90% 가 주인공 상자 안 — 주인공 쪽에서만 재면 26% (스톡 002 37.7초 '두 선수가 겹쳐')")
        self.assertEqual(out[1], 0, "34.4초: 주인공(빨간 조끼)은 아무와도 안 겹침 (겹친 것은 다른 두 사람)")
        self.assertGreater(out[2], 1.9, "공을 가진 주인공보다 두 배 큰 다른 사람 (스톡 003 53.6초 '주인공이 없음')")
        self.assertLess(out[3], 1.15)

    def test_bake_crop_keeps_picture(self):
        lay = {"type": "image", "x": -100, "y": -300, "w": 1300, "h": 1500, "fit": "cover", "fx": 0.5, "fy": 0.4}
        ar = 16 / 9
        out = self.run_js([["f2c", [lay, 0.5, 0.5, ar]], ["bakeCrop", [dict(lay), ar, {"x": 0, "y": 0, "w": 1080, "h": 900}]]])
        baked = out[1]
        self.assertGreaterEqual(baked["y"], -0.5, "칸 위로 안 나감 (레터박스 검은 띠 위로 장면이 올라오던 것 — 005_short_5 262px)")
        self.assertLessEqual(baked["y"] + baked["h"], 900.5)
        self.assertGreaterEqual(baked["x"], -0.5)
        p = self.run_js([["f2c", [baked, 0.5, 0.5, ar]]])[0]
        self.assertAlmostEqual(p[0], out[0][0], delta=0.5, msg="같은 장면 점이 같은 자리 (배율·위치 그대로)")
        self.assertAlmostEqual(p[1], out[0][1], delta=0.5)

    def test_weak_when_every_scene_has_big_text(self):
        fr = lambda t, text, ai=None: {"t": t, "hash": "0", "score": 10, "text": text, "blur": 0.1, "kind": "mid", "persons": [M], "main": 0, "flags": [], **({"ai": ai} if ai else {})}  # noqa: E731
        out = self.run_js([["setAI", [{"frames": [fr(i, 0.3) for i in range(6)], "pick": None}]], ["weakScenes", []],
                           ["setAI", [{"frames": [fr(i, 0.3) for i in range(5)] + [fr(9, 0.01)]}]], ["weakScenes", []],
                           ["setAI", [{"frames": [fr(i, 0.3) for i in range(5)] + [fr(9, 0.3, 7)]}]], ["weakScenes", []]])
        self.assertEqual(out[1::2], [True, False, False], "THUMBTEST01: 모든 장면에 큰 글자 → 약한 영상(2개까지) · 글자 없는 장면 하나라도 · 클로드가 좋다고 한 장면")


class CutTargetTests(unittest.TestCase):
    def test_back_scenes_when_one_scene_group(self):
        """THUMBTEST01: 좋은 장면이 한 묶음뿐이고 그 누끼가 품질 검사에 떨어지면 추천 0개 → 뒷모습 장면도 누끼 대상으로."""
        it = lambda t, h, flags=(): {"t": t, "hash": h, "persons": [[0.3, 0.1, 0.3, 0.8, 0.9]], "main": 0, "blur": 0.1, "flags": list(flags)}  # noqa: E731
        one = [it(1, "0000000000000000"), it(2, "0000000000000001"), it(3, "ffffffffffffffff", ["back"])]
        two = [it(1, "0000000000000000"), it(2, "00000000ffffffff"), it(3, "ffffffffffffffff", ["back"])]
        self.assertIn(3, [x["t"] for x, _ in thumb._cut_targets(one)])
        self.assertNotIn(3, [x["t"] for x, _ in thumb._cut_targets(two)], "서로 다른 좋은 장면이 둘 넘으면 예전 그대로")
        self.assertNotIn(4, [x["t"] for x, _ in thumb._cut_targets(one + [it(4, "0f0f0f0f0f0f0f0f", ["bench"])])], "벤치는 언제나 뺌")


if __name__ == "__main__":
    unittest.main()
