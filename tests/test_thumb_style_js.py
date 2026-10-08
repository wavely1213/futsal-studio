"""썸네일 스타일 버릇이 AI 추천·자동 후보에 들어가는 계산(p8_ai.js, node) — 저장소 폴더에서 python3 -m unittest tests.test_thumb_style_js

- styleBrand·brandOf: 스타일 강조색 2개 · 브랜드 키트에서 직접 바꾼 색이 먼저 · 'word' 제목 바탕 글자를 주 색으로(swapWord)는 두 색 다 스타일일 때만
- swFor: 템플릿이 획을 따로 정하지 않았으면 스타일의 테두리 버릇 · styleLayers: 배경 밝기·채도 배율 · 예전 템플릿 노랑·흰 글자 → 스타일 색
- styleBonus: 제목 위치(롱폼만)·누끼·큰 줄 수·얼굴 크기 가산점 · abBonus: 우리 채널 A/B 이긴 틀·문구 틀 (상한)
인터넷·모델·브라우저는 쓰지 않음 (D-130 · D-131)."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None
JS = ("BRAND_DEF", "AI", "TS", "activeStyle", "styleBrand", "brandOf", "THIN_FONTS", "THIN_SW", "thinFont", "swOf", "swFor", "grayish", "styleLayers",
      "imgRect", "f2c", "boxC", "mainBox", "mainFace", "ST_W", "styleBonus", "AB_W", "abBonus")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => {
  let a = src.indexOf('function ' + n + '(');
  if (a < 0) { a = src.indexOf('const ' + n + ' ='); if (a < 0) throw new Error('없음 ' + n);
    for (let i = a; ;) { const e = src.indexOf('\n', i); if (/;\s*$/.test(src.slice(i, e).replace(/\s*\/\/.*$/, ''))) return src.slice(a, e); i = e + 1; } }
  let d = 0, i = src.indexOf(') {', a) + 2; for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]);
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var toHex = c => String(c || "#000000"); var INFO = { width: 1920, height: 1080 };' +
  'var bbox = ls => { const xs = ls.flatMap(l => [l.x, l.x + l.w]), ys = ls.flatMap(l => [l.y, l.y + l.h]); const x = Math.min(...xs), y = Math.min(...ys); return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }; };' +
  NAMES.map(pick).join('\n') + '\n; globalThis.T = {' + NAMES.join(',') + '};' +
  'T.setView = v => { TS.view = v; return true; }; T.setBrand = (b, c) => { AI.brand = b; AI.brandCustom = c; return true; };');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(([fn, args]) => { const r = typeof T[fn] === 'function' ? T[fn](...args) : T[fn]; return r === undefined ? null : r; })));
"""

BRAND = {"colors": {"hl": "#FFE14D", "hl2": "#FFFFFF", "accent": "#FF3B30", "neon": "#00D1FF", "box": "#111111"}, "font": "Pretendard Black", "apply": True}
SAMBA = {"hl": "#FFE14D", "hl2": "#FFFFFF", "textScale": 1.22, "posW": {"top": 0, "middle": 0, "bottom": 1}, "sw": 0.13, "bgBright": 0.83, "bgSat": 1.0, "cut": 0.67, "lines": 1}
TALK = {"hl": "#FFFFFF", "hl2": "#FFE14D", "textScale": 0.85, "posW": {"top": 0, "middle": 0, "bottom": 1}, "sw": 0.13, "bgBright": 1.2, "bgSat": 0.91, "cut": 0, "face": 0.36, "lines": 2}


def view(params, ours=None):
    return {"styles": [], "pick": "x", "active": {"name": "x", "params": params} if params else None, "ours": ours or {"n": 0, "tpl": {}, "pid": {}}}


def title(y, h=120, size=120, name="제목 큰 줄"):
    return {"type": "text", "name": name, "x": 60, "y": y, "w": 900, "h": h, "size": size, "hidden": False}


BG = {"type": "image", "name": "배경", "x": 0, "y": 0, "w": 1280, "h": 720, "fit": "cover", "fx": 0.5, "fy": 0.5, "bright": 100, "sat": 100, "hidden": False}


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ThumbStyleJsTests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_style_brand_colors(self):
        out = self.run_js([["styleBrand", [BRAND, None, []]], ["styleBrand", [BRAND, TALK, []]], ["styleBrand", [BRAND, TALK, ["hl"]]], ["styleBrand", [BRAND, {"sw": 0.07}, []]]])
        self.assertEqual(out[0], BRAND, "스타일이 없으면 브랜드 그대로")
        self.assertEqual((out[1]["colors"]["hl"], out[1]["colors"]["hl2"], out[1]["_st"]["swapWord"]), ("#FFFFFF", "#FFE14D", True))
        self.assertEqual(out[1]["colors"]["accent"], "#FF3B30", "다른 색은 브랜드 그대로")
        self.assertEqual((out[2]["colors"]["hl"], out[2]["colors"]["hl2"], out[2]["_st"]["swapWord"]), ("#FFE14D", "#FFE14D", False),
                         "직접 바꾼 강조색(hl)은 그대로 · 둘째 색만 스타일 · 바탕 글자 바꾸기 안 함")
        self.assertEqual(out[3]["colors"], BRAND["colors"], "색이 없는 스타일(글자가 없는 썸네일)은 색은 그대로")

    def test_brand_of(self):
        user = dict(BRAND, colors=dict(BRAND["colors"], hl="#FF5FA2"))
        off = dict(user, apply=False)
        out = self.run_js([["setView", [view(None)]], ["brandOf", []], ["setView", [view(SAMBA)]], ["setBrand", [user, ["hl"]]], ["brandOf", []],
                           ["setBrand", [off, ["hl"]]], ["brandOf", []]])
        self.assertEqual(out[1]["colors"]["hl"], "#FFE14D", "브랜드 키트를 안 읽었으면 기본")
        self.assertEqual(out[4]["colors"]["hl"], "#FF5FA2", "브랜드 키트에서 직접 고른 색이 먼저")
        self.assertEqual(out[4]["_st"]["bgBright"], 0.83, "색 말고 다른 버릇은 그대로")
        self.assertEqual(out[6]["colors"]["hl"], "#FFE14D", "'새 추천에 적용'을 끄면 기본 색 + 스타일")

    def test_sw_for(self):
        ctx = {"brand": {"_st": {"sw": 0.13}}}
        out = self.run_js([["swFor", [ctx, "Pretendard Black", {}]], ["swFor", [ctx, "Pretendard Black", {"sw": 0.2}]], ["swFor", [{"brand": {}}, "Pretendard Black", {}]],
                           ["swFor", [{"brand": {}}, "Black Han Sans", {}]]])
        self.assertEqual(out, [0.13, 0.09, 0.09, 0.2], "스타일 테두리 · 템플릿이 정한 획은 그대로 · 스타일이 없으면 글꼴 기본")

    def test_style_layers(self):
        text_y = {"type": "text", "name": "제목", "fill": "#FFE14D", "box": {"on": False}}
        text_w = {"type": "text", "name": "둘째", "fill": "#FFFFFF", "box": {"on": False}}
        cut = dict(BG, name="누끼")
        b = {"colors": {"hl": "#FFFFFF", "hl2": "#FFE14D"}, "_st": TALK}
        out = self.run_js([["styleLayers", [[dict(BG), dict(cut), dict(text_y), dict(text_w)], b, True]], ["styleLayers", [[dict(BG), dict(text_y)], b, False]],
                           ["styleLayers", [[dict(BG, bright=110), dict(text_y)], {"colors": BRAND["colors"]}, True]],
                           ["styleLayers", [[dict(BG, bright=120)], {"colors": BRAND["colors"], "_st": {"bgBright": 1.22}}, False]]])
        self.assertEqual((out[0][0]["bright"], out[0][0]["sat"]), (120, 91), "배경 밝기·채도 배율")
        self.assertEqual(out[0][1]["bright"], 100, "누끼는 그대로")
        self.assertEqual((out[0][2]["fill"], out[0][3]["fill"]), ("#FFFFFF", "#FFE14D"), "예전 템플릿 노랑 → 주 색 · 흰색 → 둘째 색")
        self.assertEqual(out[1][1]["fill"], "#FFE14D", "새 템플릿 글자색은 템플릿이 브랜드 색으로 이미 정함")
        self.assertEqual((out[2][0]["bright"], out[2][1]["fill"]), (110, "#FFE14D"), "스타일이 없으면 그대로")
        self.assertEqual(out[3][0]["bright"], 130, "밝기 상한 130")

    def test_grayish(self):
        self.assertEqual(self.run_js([["grayish", ["#FFFFFF"]], ["grayish", ["#FFE14D"]], ["grayish", ["#E0E0D8"]]]), [True, False, True])

    def test_style_bonus_position_and_lines(self):
        ctx = lambda st, short=False: {"brand": {"_st": st}, "short": short, "frame": {"persons": [], "main": -1, "faces": []}, "ar": 16 / 9}  # noqa: E731
        bottom = {"w": 1280, "h": 720, "layers": [dict(BG), title(520)]}
        top = {"w": 1280, "h": 720, "layers": [dict(BG), title(40)]}
        two = {"w": 1280, "h": 720, "layers": [dict(BG), title(380, name="제목 작은 줄", size=110), title(520)]}
        out = self.run_js([["styleBonus", [bottom, ctx(SAMBA)]], ["styleBonus", [top, ctx(SAMBA)]], ["styleBonus", [two, ctx(SAMBA)]], ["styleBonus", [two, ctx(TALK)]],
                           ["styleBonus", [top, ctx(SAMBA, True)]], ["styleBonus", [bottom, {"brand": {}, "short": False, "frame": {}}]]])
        self.assertAlmostEqual(out[0], 15 * (1 - 1 / 3) + 3, places=3, msg="아래 제목 100% 버릇: 아래 제목 +10 · 큰 줄 1개 +3")
        self.assertAlmostEqual(out[1], -5 + 3, places=3, msg="위 제목은 깎음")
        self.assertLess(out[2], out[0], "큰 줄 2개는 한 줄 버릇에서 덜")
        self.assertGreater(out[3], out[2], "두 줄 버릇에서는 두 줄이 더")
        self.assertAlmostEqual(out[4], 3, places=3, msg="쇼츠는 위치 가산점 없음 (레퍼런스가 16:9 썸네일)")
        self.assertEqual(out[5], 0, "스타일이 없으면 0")

    def test_style_bonus_cut_and_face(self):
        cut = dict(BG, name="누끼")
        face_frame = {"persons": [[0.3, 0.1, 0.4, 0.9, 0.9]], "main": 0, "faces": [{"box": [0.42, 0.12, 0.16, 0.36]}]}
        ctx = lambda st, fr=None: {"brand": {"_st": st}, "short": True, "frame": fr or {"persons": [], "main": -1, "faces": []}, "ar": 16 / 9}  # noqa: E731
        doc = lambda *ls: {"w": 1280, "h": 720, "layers": [dict(BG), *ls]}  # noqa: E731
        out = self.run_js([["styleBonus", [doc(cut), ctx({"cut": 0.67})]], ["styleBonus", [doc(cut), ctx({"cut": 0})]], ["styleBonus", [doc(), ctx({"cut": 0.67})]],
                           ["styleBonus", [doc(), ctx({"face": 0.36}, face_frame)]], ["styleBonus", [doc(), ctx({"face": 0.09}, face_frame)]]])
        self.assertAlmostEqual(out[0], 8 * 0.37, places=3)
        self.assertAlmostEqual(out[1], -8 * 0.3, places=3, msg="누끼를 안 쓰는 채널은 누끼 틀을 조금 깎음")
        self.assertEqual(out[2], 0)
        self.assertGreater(out[3], 2.5, "얼굴 크기가 버릇과 같으면 +")
        self.assertLess(out[4], 0, "버릇보다 4배 크면 −")

    def test_ab_bonus(self):
        ours = {"n": 3, "tpl": {"위 제목 (쪼살형)": 1, "아래 제목 (쌈바형)": 3}, "pid": {"q": 1, "num": 5}}
        out = self.run_js([["setView", [view(None)]], ["abBonus", ["위 제목 (쪼살형)", {"pid": "q"}]], ["setView", [view(None, ours)]],
                           ["abBonus", ["위 제목 (쪼살형)", {"pid": "q"}]], ["abBonus", ["아래 제목 (쌈바형)", {"pid": "num"}]], ["abBonus", ["다른 틀", {"pid": "x"}]],
                           ["abBonus", ["위 제목 (쪼살형)", {}]]])
        self.assertEqual(out[1], 0, "A/B 이긴 기록이 없으면 0")
        self.assertEqual(out[3], 4 + 3)
        self.assertEqual(out[4], 8 + 6, "이긴 횟수만큼 · 상한 틀 8 · 문구 틀 6")
        self.assertEqual(out[5], 0)
        self.assertEqual(out[6], 4)


if __name__ == "__main__":
    unittest.main()
