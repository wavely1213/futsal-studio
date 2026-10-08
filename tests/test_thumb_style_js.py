"""썸네일 스타일 버릇이 AI 추천·자동 후보에 들어가는 계산(p8_ai.js, node) — 저장소 폴더에서 python3 -m unittest tests.test_thumb_style_js

- styleBrand·brandOf: 스타일 강조색(hl)·바탕 글자색(hl2) · 브랜드 키트에서 직접 바꾼 색이 먼저
- headline 색 역할 (D-130 검토): 강조 낱말은 늘 강조색(hl) — 바탕·강조를 뒤바꾸지 않음 · big('line' 큰 줄 역할) · stack(같은 크기 두 줄의 위 줄 역할)
  · '우리 채널'(기본 모양으로 이긴 장)은 기본과 같은 그림 — 강조 낱말 글자색(runs)까지 확인 (l.fill 만이 아니라)
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
      "imgRect", "f2c", "boxC", "mainBox", "mainFace", "ST_W", "GLYPH_H", "styleBonus", "AB_W", "abBonus")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => {
  let a = src.indexOf('function ' + n + '(');
  if (a < 0) { a = src.indexOf('const ' + n + ' ='); if (a < 0) throw new Error('없음 ' + n);
    for (let i = a; ;) { const e = src.indexOf('\n', i); if (/;\s*$/.test(src.slice(i, e).replace(/\s*\/\/.*$/, ''))) return src.slice(a, e); i = e + 1; } }
  let d = 0, i = src.indexOf(') {', a) + 2; for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]);
eval((process.argv[3] || '') + 'var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var toHex = c => String(c || "#000000"); var INFO = { width: 1920, height: 1080 };' +
  'var bbox = ls => { const xs = ls.flatMap(l => [l.x, l.x + l.w]), ys = ls.flatMap(l => [l.y, l.y + l.h]); const x = Math.min(...xs), y = Math.min(...ys); return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }; };' +
  NAMES.map(pick).join('\n') + '\n; globalThis.T = {' + NAMES.join(',') + '};' +
  'T.setView = v => { TS.view = v; return true; }; T.setBrand = (b, c) => { AI.brand = b; AI.brandCustom = c; return true; };');
if (process.argv[4]) eval(process.argv[4]);  // 시험용 덧붙임 (HeadlineRoleTests)
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
    def run_js(self, cases, names=JS, prelude=""):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(names), prelude], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_style_brand_colors(self):
        out = self.run_js([["styleBrand", [BRAND, None, []]], ["styleBrand", [BRAND, TALK, []]], ["styleBrand", [BRAND, TALK, ["hl"]]], ["styleBrand", [BRAND, {"sw": 0.07}, []]]])
        self.assertEqual(out[0], BRAND, "스타일이 없으면 브랜드 그대로")
        self.assertEqual((out[1]["colors"]["hl"], out[1]["colors"]["hl2"]), ("#FFFFFF", "#FFE14D"))
        self.assertNotIn("swapWord", out[1]["_st"], "바탕·강조 뒤바꾸기는 없앰 (D-130 검토)")
        self.assertEqual(out[1]["colors"]["accent"], "#FF3B30", "다른 색은 브랜드 그대로")
        self.assertEqual((out[2]["colors"]["hl"], out[2]["colors"]["hl2"]), ("#FFE14D", "#FFE14D"), "직접 바꾼 강조색(hl)은 그대로 · 둘째 색만 스타일")
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

    def test_style_bonus_text_size(self):
        """글자 높이 가산점(D-132): 크게 쓰는 채널(21%H)은 큰 글자 후보를, 작게 쓰는 채널(14%H)은 작은 글자 후보를 앞으로 · 쇼츠는 없음."""
        ctx = lambda st, short=False: {"brand": {"_st": st}, "short": short, "frame": {"persons": [], "main": -1, "faces": []}, "ar": 16 / 9}  # noqa: E731
        big = {"w": 1280, "h": 720, "layers": [dict(BG), title(480, size=178)]}
        small = {"w": 1280, "h": 720, "layers": [dict(BG), dict(title(520, size=120), runs=[{"s": 0, "e": 2, "size": 130}])]}
        out = self.run_js([["styleBonus", [big, ctx({"textH": 0.21})]], ["styleBonus", [small, ctx({"textH": 0.21})]],
                           ["styleBonus", [big, ctx({"textH": 0.14})]], ["styleBonus", [small, ctx({"textH": 0.14})]], ["styleBonus", [big, ctx({"textH": 0.21}, True)]]])
        self.assertGreater(out[0], 2.5, "버릇과 같은 크기(178px × 0.85 ÷ 720 = 21%) → +")
        self.assertGreater(out[0] - out[1], 4, "크게 쓰는 채널: 큰 글자 후보가 작은 것보다 4점 넘게 앞")
        self.assertGreater(out[3] - out[2], 4, "작게 쓰는 채널: 작은 글자 후보가 앞 (강조 낱말 크기까지 봄)")
        self.assertEqual(out[4], 0, "쇼츠는 글자 높이 가산점 없음")

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


# headline 을 node 에서 돌리기: 그림 그리기(L·fitText·bbox·extentOf)만 가짜 — 글자 폭 = 글자 수 × 크기 × 0.9
HEAD_JS = ("AI", "THIN_FONTS", "THIN_SW", "thinFont", "swOf", "softShadow", "swFor", "tStyle", "DIGIT_FONT", "digitRuns", "SMALL_FONT_PX", "readableFont", "OX_RX", "oxRuns",
           "lineLayer", "emphOf", "PRED_END", "LINK_END", "lineRatio", "headline", "grayish", "mix", "BIG_MIN", "SAFE", "subBox", "styleBrand")
HEAD_PRE = ("var L = (type, o) => Object.assign({ type, x: 0, y: 0, runs: [] }, o); var fitText = l => { l.w = String(l.text).length * l.size * 0.9; l.h = l.size * 1.1; };"
            "var extentOf = l => ({ x: l.x, y: l.y, w: l.w, h: l.h });")
W, H = 1280, 720


def head_case(params, copy, style):
    """스타일 값·문구·제목 모양 → headline 결과에서 줄마다 [이름, 바탕 글자색, 강조 낱말 글자색들] (읽는 순서)."""
    return ["head", [params, copy, style]]


HEAD_FN = ("T.head = (sp, copy, style) => { const brand = styleBrand(" + json.dumps(BRAND) + ", sp, []);"
           "const ctx = { W: " + str(W) + ", H: " + str(H) + ", short: false, copy, brand };"
           "const r = headline(ctx, { x: 60, y: 300, w: 1160, h: 380 }, { style, anchor: 'bottom' });"
           "return r.layers.map(l => [l.name, l.fill, (l.runs || []).filter(u => u.s != null && u.size).map(u => u.fill)]); };")


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class HeadlineRoleTests(unittest.TestCase):
    """색 역할: 강조 낱말(runs)의 색까지 — 예전 시험은 줄 바탕색(l.fill)만 봐서 쌈바형의 '강조 낱말이 흰색으로 뒤바뀜'을 놓침."""
    ONE = {"l1": "수비가 다 속아요", "l2": "", "emph": [0, 4, 9]}               # 한 줄 · '다 속아요' 강조
    TWO_B = {"l1": "다들 여기서", "l2": "수비가 얼어요", "emph": [1, 4, 7]}      # 작은 위 줄 + 큰 아래 줄 ('얼어요' 강조)
    TWO_T = {"l1": "수비가 얼어요", "l2": "다들 여기서", "emph": [0, 4, 7]}      # 큰 위 줄 + 작은 아래 줄
    Y, WH = "#FFE14D", "#FFFFFF"

    def run_head(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(HEAD_JS), HEAD_PRE, HEAD_FN],
                           input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_hook_word_keeps_accent(self):
        samba = {"hl": self.Y, "hl2": self.WH, "textScale": 1.25, "sw": 0.07}   # 쌈바형 (강조 낱말 버릇 · thumbstyle.params 에 big 없음)
        out = self.run_head([head_case(None, self.ONE, "word"), head_case(samba, self.ONE, "word"), head_case(samba, self.TWO_B, "word")])
        self.assertEqual(out[0], [["제목 큰 줄", self.WH, [self.Y]]], "기본: 흰 글자 + 노란 강조 낱말")
        self.assertEqual(out[1], out[0], "쌈바형도 강조 낱말은 노랑 (바탕 흰색) — '다 속아요'가 흰색으로 뒤바뀌지 않음")
        big = next(x for x in out[2] if x[0] == "제목 큰 줄")
        self.assertEqual(big[2], [self.Y], "'얼어요' 강조 낱말은 노랑")

    def test_line_mode_big_line_accent(self):
        line = {"hl": self.Y, "hl2": self.WH, "big": "hl"}  # 큰 줄 전체가 강조색인 채널 ('ALA 움직임')
        out = self.run_head([head_case(line, self.ONE, "word"), head_case(line, self.TWO_B, "word"), head_case(line, self.TWO_B, "line")])
        self.assertEqual(out[0], [["제목 큰 줄", self.Y, [self.Y]]], "큰 줄 전체 노랑 · 강조 낱말도 노랑(크기로 강조) — 흰색으로 바뀌지 않음")
        self.assertEqual([x[1] for x in out[1]], [self.WH, self.Y], "작은 줄은 바탕 흰색")
        self.assertEqual([x[1] for x in out[2]], [self.WH, self.Y])

    def test_plain_and_stack(self):
        plain = {"hl": self.Y, "hl2": self.WH, "big": "hl2"}     # 흰 큰 글자 + 강조색 작은 줄
        stack = {"hl": self.Y, "hl2": self.WH, "stack": "hl2"}   # lcs: 같은 크기 두 줄 — 위 흰 · 아래 노랑
        out = self.run_head([head_case(plain, self.TWO_B, "line"), head_case(stack, self.TWO_T, "line"), head_case(None, self.TWO_T, "line"),
                             head_case(stack, self.TWO_B, "line"), head_case(stack, self.TWO_T, "word")])
        self.assertEqual([x[1] for x in out[0]], [self.Y, self.WH], "plain: 큰 줄 흰색 · 작은 줄 강조색")
        self.assertEqual([x[1] for x in out[1]], [self.WH, self.Y], "stack: 큰 줄이 위여도 위 흰 · 아래 노랑 (lcs 그대로)")
        self.assertEqual([x[1] for x in out[2]], [self.Y, self.WH], "기본은 큰 줄 노랑")
        self.assertEqual([x[1] for x in out[3]], [self.WH, self.Y], "stack: 작은 위 줄 흰 · 큰 아래 줄 노랑 — 노란 작은 위 줄이 되지 않음")
        self.assertEqual([x[1] for x in out[4]], [self.WH, self.Y], "'word' 제목도 위 흰 · 아래 노랑")
        self.assertEqual(out[4][0][2], [self.Y], "강조 낱말은 노랑")

    def test_ours_default_winner_same_as_default(self):
        """기본 색으로 이긴 A/B 장 → '우리 채널'(thumbstyle.ours params) 그림이 '기본'과 같아야 함 (강조 낱말 색까지)."""
        ours = {"hl": self.Y, "hl2": self.WH}
        for copy in (self.ONE, self.TWO_B, self.TWO_T):
            for st in ("word", "line"):
                out = self.run_head([head_case(None, copy, st), head_case(ours, copy, st)])
                self.assertEqual(out[1], out[0], f"{copy['l1']} · {st}")


if __name__ == "__main__":
    unittest.main()
