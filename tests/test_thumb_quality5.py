"""썸네일 퀄리티 판정 5회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality5

- 글자(p8_ai.js, node): 두 줄 크기 비율은 문장이면 같은 크기·꼬리표면 확실히 작게 · 위계 점수는 1.0~1.25·2.2 넘게가 만점 · 프리텐다드 블랙은 얇은 외곽선 ·
  작은 검은고딕은 도현이 아니라 프리텐다드 블랙으로 (D-090)
- 구도(p8_ai.js, node): 쇼츠 꽉 찬 장면은 원본 1080p 가로·세로 영상만 (720p 는 예전 칸) · 다른 사람 머리도 지킴 · 발·공 클로즈업은 발 이야기 문구에만 (D-090·D-091)
- 예전 템플릿 칸을 얼굴·머리에 맞춰 자르기(anchorSlot·anchorTpl · P1, D-092) · 배경과 짝이 아닌 누끼도 그 누끼 장면의 머리로(anchorCut · D-270)
- 장면(thumb·face): 레슨 가산은 머리가 보일 때만 · 주인공 흔들림 배율 · 머리 쪽 작은 얼굴 찾기 · 채도 절대 목표 (D-092·D-093)
- 문구(thumbcopy): 유튜버 말투 훅 · 말하듯 끝나는 줄 가산·라벨 감산 · 근거 인용은 대사에 있는지·숫자·이력만 · 새 클로드 질문 (D-094)
- 브랜드 키트: 예전 기본값(검은고딕) 저장본은 새 기본 글꼴로, 고른 글꼴(fontV 2)은 그대로
인터넷·진짜 클로드·얼굴 모델은 쓰지 않음."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import face  # noqa: E402
import thumb  # noqa: E402
import thumbcopy as tc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None

JS = ("PRED_END", "LINK_END", "lineRatio", "hierOf", "THIN_FONTS", "THIN_SW", "thinFont", "swOf", "SMALL_FONT_PX", "readableFont",
      "bandCrop", "arEff", "srcW", "SHORT_FULL_CAP", "coverUp", "fullOk", "imgRect", "f2c", "boxC", "clipTo", "areaOf", "interArea",
      "mainBox", "mainFace", "headBox", "srcHeadCut", "footClose", "LOUD", "loud", "FOOT_COPY", "footCopy", "footOk", "headsOf",
      "slotFocus", "anchorPt", "anchorSlot", "frameT", "frameMeta", "anchorTpl", "anchorCut")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => {  // 함수 (기본값 '= {}' 매개변수 건너뜀) 또는 한 문장 상수 (줄 끝 주석 빼고 ';' 로 끝나는 줄까지)
  let a = src.indexOf('function ' + n + '(');
  if (a < 0) { a = src.indexOf('const ' + n + ' ='); if (a < 0) throw new Error('없음 ' + n);
    for (let i = a; ;) { const e = src.indexOf('\n', i); if (/;\s*$/.test(src.slice(i, e).replace(/\s*\/\/.*$/, ''))) return src.slice(a, e); i = e + 1; } }
  let d = 0, i = src.indexOf(') {', a) + 2; for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]);
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var INFO = { width: 1920, height: 1080 }; var AI = { frames: [] }; var FRAMES = [];' +
  'var frameAspect = () => INFO.width / INFO.height; var frameSrc = t => "/frame?name=x&t=" + t;' +
  'var setInfo = (w, h) => { INFO = { width: w, height: h }; return true; }; var setFrames = fs => { AI.frames = fs; return true; }; var setPick = t => { AI.pick = t; return true; };' +
  NAMES.map(pick).join('\n') + '; globalThis.T = {' + NAMES.join(',') + ', setInfo, setFrames, setPick};');
const cases = JSON.parse(fs.readFileSync(0, 'utf8')); const out = cases.map(([fn, args]) => (typeof T[fn] === 'function' ? T[fn](...args) : T[fn]));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ScreenMath5Tests(unittest.TestCase):
    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_line_ratio_by_copy_type(self):
        c = lambda l1, l2, e=1: {"l1": l1, "l2": l2, "emph": [e, 0, 1]}  # noqa: E731
        out = self.run_js([["lineRatio", [c("이것도 모르고", "드래그 했네..")]], ["lineRatio", [c("수비가 왜 먼저", "속는 걸까?")]],
                           ["lineRatio", [c("수비를 속이는", "발바닥 드래그"), True]], ["lineRatio", [c("수비를 속이는", "발바닥 드래그")]],
                           ["lineRatio", [c("핵심은", "디딤발 위치")]], ["lineRatio", [{"l1": "한 줄", "l2": ""}]]])
        self.assertEqual(out[0], 0.88, "한 문장은 두 줄이 거의 같은 크기")
        self.assertEqual(out[1], 0.88)
        self.assertEqual(out[2], 0.42, "쌈바형 꾸밈말 + 이름씨 (kicker 템플릿)")
        self.assertEqual(out[3], 0.88, "kicker 가 아니면 같은 크기")
        self.assertEqual(out[4], 0.45, "짧은 꼬리표")
        self.assertEqual(out[5], 1)
        self.assertFalse(any(0.5 < x < 0.75 for x in out), "어중간한 비율(0.5~0.75)은 안 씀 — 레퍼런스 4%")

    def test_hierarchy_rewards_equal_or_clear_kicker(self):
        out = self.run_js([["hierOf", [1.0]], ["hierOf", [1.136]], ["hierOf", [1.8]], ["hierOf", [2.3]], ["hierOf", [1.33]]])
        self.assertEqual(out[0], 1)
        self.assertEqual(out[1], 1)
        self.assertAlmostEqual(out[2], 0.4)
        self.assertEqual(out[3], 1)
        self.assertTrue(0.4 < out[4] < 1)

    def test_font_and_outline(self):
        out = self.run_js([["swOf", ["Pretendard Black", {}]], ["swOf", ["Pretendard Black", {"sw": 0.14}]], ["swOf", ["Black Han Sans", {}]],
                           ["readableFont", [{"short": False, "W": 1280}, "Black Han Sans", 100]], ["readableFont", [{"short": False, "W": 1280}, "Black Han Sans", 200]]])
        self.assertEqual(out[0], 0.09, "프리텐다드 블랙은 얇은 외곽선 (+ 부드러운 그림자)")
        self.assertEqual(out[1], 0.09)
        self.assertEqual(out[2], 0.2, "검은고딕은 예전 굵은 외곽선")
        self.assertEqual(out[3], "Pretendard Black", "작은 검은고딕은 도현(OCR 0.51)이 아니라 프리텐다드 블랙(0.88)")
        self.assertEqual(out[4], "Black Han Sans")

    def test_full_bleed_shorts_only_when_source_allows(self):
        ctx = {"W": 1080, "H": 1920, "short": True, "ar": 16 / 9, "band": None, "frame": {}}
        vctx = dict(ctx, ar=1080 / 1920)
        out = self.run_js([["setInfo", [1920, 1080]], ["fullOk", [ctx]], ["coverUp", [ctx]], ["setInfo", [1280, 720]], ["fullOk", [ctx]],
                           ["setInfo", [1080, 1920]], ["fullOk", [vctx]], ["fullOk", [dict(ctx, short=False)]]])
        self.assertTrue(out[1], "1080p 가로 영상 → 9:16 전체 (1.78배)")
        self.assertAlmostEqual(out[2], 1920 * 16 / 9 / 1920, places=3)
        self.assertTrue(out[4], "판정 q5 1회차(D-096): 720p 가로 영상도 2.67배로 꽉 찬 장면 (목록 크기에서는 원본보다 작게 보임)")
        self.assertTrue(out[6], "세로 영상")
        self.assertFalse(out[7], "롱폼은 해당 없음")

    def test_heads_of_others_and_foot_rule(self):
        f = {"persons": [[0.4, 0.2, 0.1, 0.6, 0.9], [0.7, 0.3, 0.08, 0.3, 0.9], [0.1, 0.5, 0.03, 0.1, 0.9], [0.85, 0.0, 0.1, 0.5, 0.9]], "main": 0, "faces": []}
        foot = {"t": 7, "persons": [[0.3, 0.0, 0.4, 0.9, 0.9]], "main": 0, "ball": [0.45, 0.7, 0.08, 0.1], "faces": [], "kind": "mid", "color": 40}
        out = self.run_js([["headsOf", [f]], ["footOk", [foot, {"l1": "발바닥", "l2": "드래그 했네.."}]], ["setPick", [7]], ["footOk", [foot, {"l1": "발바닥", "l2": "드래그 했네.."}]],
                           ["footOk", [foot, {"l1": "왜 막힐까?", "l2": "상체 페인트"}]], ["footOk", [dict(foot, color=95), {"l1": "발바닥", "l2": "드래그"}]], ["srcHeadCut", [foot]]])
        self.assertFalse(out[1], "판정 q5 1회차(D-098): 자동 추천은 다리만 나온 장면을 안 씀")
        out = out[:1] + out[3:]
        heads = out[0]
        self.assertEqual(len(heads), 2, "주인공 + 키 0.15H 넘는 사람 (작은 사람·머리가 화면 위로 잘린 사람은 뺌)")
        self.assertTrue(heads[0]["main"])
        self.assertFalse(heads[1]["main"])
        self.assertTrue(out[1], "사용자가 그 장면을 골랐고 발 이야기 문구 + 차분한 배경")
        self.assertFalse(out[2], "발 이야기가 아니면 다리만 나온 장면은 안 씀")
        self.assertFalse(out[3], "요란한 낙서 벽이면 안 씀")
        self.assertTrue(out[4])

    def test_anchor_slot_keeps_face(self):
        f = {"t": 1, "persons": [[0.75, 0.2, 0.1, 0.6, 0.9]], "main": 0, "faces": [{"box": [0.78, 0.22, 0.04, 0.07]}]}
        slot = {"x": 0, "y": 0, "w": 500, "h": 720, "fit": "cover", "fx": 0.5, "fy": 0.5, "cropL": 0, "cropR": 0, "cropT": 0, "cropB": 0}
        out = self.run_js([["anchorSlot", [slot, f, 16 / 9]], ["setInfo", [1920, 1080]]])
        self.assertTrue(out[0], "가운데 자르기(fx 0.5)면 얼굴(0.8)이 칸 밖 → 얼굴에 맞추면 보임")
        # 칸 안 값 확인 (같은 계산을 한 번 더 · 바뀐 fx 를 돌려받음)
        js = [["setFrames", [[f]]], ["anchorTpl", [[dict(slot, type="image", src="/frame?name=x&t=1")], [1]]]]
        out = self.run_js(js)
        self.assertGreater(out[1][0]["fx"], 0.9, "얼굴 쪽(오른쪽)으로 잘라냄")

    def test_anchor_tpl_swaps_frame_when_head_cannot_fit(self):
        wide = {"t": 1, "persons": [[0.1, 0.1, 0.6, 0.85, 0.9]], "main": 0, "faces": [{"box": [0.2, 0.12, 0.3, 0.3]}]}  # 칸(100px)보다 넓은 얼굴
        ok = {"t": 2, "persons": [[0.45, 0.3, 0.1, 0.5, 0.9]], "main": 0, "faces": [{"box": [0.48, 0.32, 0.03, 0.06]}]}
        slot = {"type": "image", "src": "/frame?name=x&t=1", "x": 0, "y": 0, "w": 100, "h": 720, "fit": "cover", "fx": 0.5, "fy": 0.5, "cropL": 0, "cropR": 0, "cropT": 0, "cropB": 0}
        out = self.run_js([["setFrames", [[wide, ok]]], ["anchorTpl", [[slot], [1, 2]]]])
        self.assertEqual(out[1][0]["src"], "/frame?name=x&t=2", "머리를 칸에 못 담으면 다음 장면으로")


    # ----- D-270: 배경과 짝(twin)이 아닌 누끼 — '인물 누끼 넣어서 만들기'(CUT_AUTO) · 둘째 후보 묶음(k=1) -----
    LEFT = {"t": 1, "persons": [[0.09, 0.11, 0.15, 0.8, 0.9]], "main": 0, "faces": [{"box": [0.13, 0.13, 0.06, 0.1]}]}  # 왼쪽에 선 감독님 (얼굴 0.13~0.19)
    MID = {"t": 3, "persons": [[0.45, 0.2, 0.1, 0.6, 0.9]], "main": 0, "faces": [{"box": [0.48, 0.22, 0.04, 0.07]}]}

    def cut(self, **o):
        return dict({"type": "image", "name": "누끼", "src": "/assets/cut_1.png", "orig": "/frame?name=x&t=1", "fit": "cover", "fx": 0.5, "fy": 0.5,
                     "cropL": 0, "cropR": 0, "cropT": 0, "cropB": 0}, **o)

    def bg(self, t, w, h):
        return {"type": "image", "name": "배경", "src": f"/frame?name=x&t={t}", "x": 0, "y": 0, "w": w, "h": h, "fit": "cover", "fx": 0.5, "fy": 0.5,
                "cropL": 0, "cropR": 0, "cropT": 0, "cropB": 0}

    def head_in_canvas(self, l, W, H, f=None, ar=16 / 9):
        """누끼 레이어에서 머리(이마까지 얼굴 상자)가 화면·레이어 안에 보이는 비율 (anchorSlot 과 같은 계산을 파이썬으로)."""
        f = f or self.LEFT
        a = ar  # 자르기 없음
        if l["w"] / l["h"] > a:
            dw, dh = l["w"], l["w"] / a
        else:
            dh, dw = l["h"], l["h"] * a
        dx, dy = (l["w"] - dw) * l["fx"], (l["h"] - dh) * l["fy"]
        fc = f["faces"][0]["box"]
        hb = [fc[0] - fc[2] * 0.12, max(0, fc[1] - fc[3] * 0.6), fc[2] * 1.24, fc[3] * 1.65]
        x0, y0 = l["x"] + dx + hb[0] * dw, l["y"] + dy + hb[1] * dh
        x1, y1 = x0 + hb[2] * dw, y0 + hb[3] * dh
        vx0, vy0, vx1, vy1 = max(0, l["x"]), max(0, l["y"]), min(W, l["x"] + l["w"]), min(H, l["y"] + l["h"])
        iw, ih = max(0, min(x1, vx1) - max(x0, vx0)), max(0, min(y1, vy1) - max(y0, vy0))
        return iw * ih / ((x1 - x0) * (y1 - y0)), (x0 + x1) / 2

    def edges_kept(self, before, after, W, H):
        """D-273: 템플릿 누끼가 화면 끝까지 덮던 쪽은 옮긴 뒤에도 덮음 (레이어 끝 = 원본 테두리·자른 선이 화면 안으로 들어오지 않음)."""
        if before["x"] <= 0:
            self.assertLessEqual(after["x"], 1e-6, "왼쪽 끝이 화면 안으로")
        if before["x"] + before["w"] >= W:
            self.assertGreaterEqual(after["x"] + after["w"], W - 1e-6, "오른쪽 끝이 화면 안으로")
        if before["y"] <= 0:
            self.assertLessEqual(after["y"], 1e-6, "위 끝이 화면 안으로")
        if before["y"] + before["h"] >= H:
            self.assertGreaterEqual(after["y"] + after["h"], H - 1e-6, "아래 끝이 화면 안으로")

    def off_edge_band(self, l, W, H, f, ar=16 / 9):
        """머리가 화면 옆 끝 8% 밖인지 (anchorCut 의 '옆 끝' 기준)."""
        vis, cx = self.head_in_canvas(l, W, H, f, ar)
        fc = f["faces"][0]["box"]
        a = ar
        dw = l["w"] if l["w"] / l["h"] > a else l["h"] * a
        hw = fc[2] * 1.24 * dw
        return cx - hw / 2 >= 0.08 * W - 1e-6 and cx + hw / 2 <= W * 0.92 + 1e-6

    def test_cutout_person_emphasis_keeps_off_center_coach(self):
        """'쇼츠 · 인물 강조' 누끼(x −540 · 폭 2160 · cover)는 가운데 자르기면 원본 0.32~0.68 만 보여 왼쪽 감독님 머리가 화면 밖 → 화면 가운데로."""
        W, H = 1080, 1920
        cut = self.cut(x=-540, y=260, w=2160, h=H - 260)
        before = self.head_in_canvas(cut, W, H)
        self.assertEqual(before[0], 0, "고치기 전: 머리가 화면에 하나도 안 보임")
        out = self.run_js([["setFrames", [[self.LEFT, self.MID]]], ["anchorTpl", [[self.bg(1, W, H), cut], [1, 3], {"w": W, "h": H}]]])
        bg, c = out[1]
        vis, cx = self.head_in_canvas(c, W, H)
        self.assertGreaterEqual(vis, 0.95, c)
        self.assertTrue(self.off_edge_band(c, W, H, self.LEFT), c)
        self.assertLess(abs(cx - W / 2), abs(before[1] - W / 2), "템플릿이 사람을 두려던 화면 가운데 쪽으로")
        self.assertEqual(c["fx"], 0, "fx 로 갈 수 있는 데까지 → 남은 만큼 레이어를 옮김")
        self.assertEqual(c["x"], 0, "왼쪽 끝은 화면 끝까지만 (D-273)")
        self.edges_kept(cut, c, W, H)
        self.assertEqual(c["y"], 260, "머리가 화면 안이면 위아래는 그대로")
        self.assertLess(bg["fx"], 0.2, "배경 칸도 얼굴 쪽으로 (D-092 그대로)")

    def test_cutout_on_other_scene_uses_its_own_face(self):
        """둘째 후보 묶음(k=1): 배경은 다른 장면(t=3)이라 짝이 아님 → 화면 전체 쇼츠 누끼(위아래 큰 글씨·네온)도 누끼 장면(t=1) 얼굴에 맞춤."""
        W, H = 1080, 1920
        cut = self.cut(x=0, y=0, w=W, h=H)
        self.assertEqual(self.head_in_canvas(cut, W, H)[0], 0, "고치기 전: 가운데 자르기면 머리가 화면 밖")
        out = self.run_js([["setFrames", [[self.LEFT, self.MID]]], ["anchorTpl", [[self.bg(3, W, H), cut], [3, 1], {"w": W, "h": H}]]])
        bg, c = out[1]
        self.assertAlmostEqual(bg["fx"], 0.5, delta=0.05, msg="배경은 자기 장면(가운데 감독님)")
        self.assertLess(c["fx"], 0.1, "누끼는 왼쪽 얼굴 쪽")
        self.assertEqual((c["x"], c["y"]), (0, 0), "fx 만으로 들어오면 옮기지 않음")
        self.assertGreaterEqual(self.head_in_canvas(c, W, H)[0], 0.95)

    def test_twin_and_contain_cutouts_unchanged(self):
        """배경과 같은 자리·같은 장면 누끼(twin)는 배경 fx·fy 그대로 · contain 누끼('3분할 사선')와 장면 정보가 없는 누끼는 건드리지 않음."""
        W, H = 1080, 1920
        twin = self.cut(x=0, y=0, w=W, h=H)
        box = self.cut(x=240, y=30, w=800, h=450, fit="contain")
        lost = self.cut(x=-540, y=260, w=2160, h=H - 260, orig="/assets/up.png")
        out = self.run_js([["setFrames", [[self.LEFT, self.MID]]], ["anchorTpl", [[self.bg(1, W, H), twin, box, lost], [1, 3], {"w": W, "h": H}]]])
        bg, t, b, lo = out[1]
        self.assertEqual((t["fx"], t["fy"], t["x"]), (bg["fx"], bg["fy"], 0))
        self.assertEqual((b["fx"], b["x"], b["y"]), (0.5, 240, 30))
        self.assertEqual((lo["fx"], lo["x"]), (0.5, -540))

    def test_cutout_shifted_left_template(self):
        """'두 장면 겹치기' 누끼는 x −200 (16:9 그대로라 fx 로는 못 옮김) → 왼쪽 끝 감독님 머리가 화면 밖이면 오른쪽으로 옮기되,
        레이어 왼쪽 끝(= 원본 왼쪽 테두리)은 화면 왼쪽 끝까지만 (D-273 · 테두리에 닿은 사람이 곧은 선으로 잘려 보이지 않게)."""
        W, H = 1280, 720
        edge = {"t": 1, "persons": [[0.02, 0.15, 0.12, 0.8, 0.9]], "main": 0, "faces": [{"box": [0.05, 0.17, 0.05, 0.1]}]}
        cut = self.cut(x=-200, y=0, w=W, h=H)
        self.assertLess(self.head_in_canvas(cut, W, H, edge)[0], 0.5)
        out = self.run_js([["setFrames", [[edge]]], ["anchorTpl", [[self.bg(1, W, H), cut], [1], {"w": W, "h": H}]]])
        bg, c = out[1]
        vis, cx = self.head_in_canvas(c, W, H, edge)
        self.assertGreaterEqual(vis, 0.95)
        self.assertEqual(c["x"], 0, "왼쪽 테두리는 화면 왼쪽 끝까지만 → 같은 장면 배경과 꼭 겹침")
        self.assertEqual((c["fx"], c["fy"]), (bg["fx"], bg["fy"]))
        self.edges_kept(cut, c, W, H)
        out = self.run_js([["setFrames", [[self.MID]]], ["anchorTpl", [[self.bg(3, W, H), self.cut(x=-200, y=0, w=W, h=H, orig="/frame?name=x&t=3")], [3], {"w": W, "h": H}]]])
        self.assertEqual(out[1][1]["x"], -200, "머리가 화면 안이면 템플릿 자리 그대로")

    def test_cutout_person_emphasis_edge_coach_centered(self):
        """시험 영상 005(2.7초): 주인공이 오른쪽 끝(0.84~0.95) → fx 1 이면 머리는 들어와도 화면 옆 끝(8% 안)에 걸침 → '인물 강조'답게 가운데로 옮김."""
        W, H = 1080, 1920
        right = {"t": 1, "persons": [[0.82, 0.15, 0.15, 0.8, 0.9]], "main": 0, "faces": [{"box": [0.86, 0.18, 0.06, 0.1]}]}
        out = self.run_js([["setFrames", [[right, self.MID]]], ["anchorTpl", [[self.bg(1, W, H), self.cut(x=-540, y=260, w=2160, h=H - 260)], [1, 3], {"w": W, "h": H}]]])
        c = out[1][1]
        vis, cx = self.head_in_canvas(c, W, H, right)
        self.assertGreaterEqual(vis, 0.95)
        self.assertTrue(self.off_edge_band(c, W, H, right), c)
        self.assertEqual(c["fx"], 1)
        self.assertEqual(c["x"] + c["w"], W, "오른쪽 끝은 화면 오른쪽 끝까지만 (D-273)")

    def test_headless_cutout_left_as_template(self):
        """시험 영상 003(40초): 주인공은 머리가 원본 위로 잘린 다리 · 얼굴로 찾은 것은 공 → 지킬 머리가 없으니 누끼는 템플릿 그대로."""
        W, H = 1080, 1920
        leg = {"t": 1, "persons": [[0.5961, 0.0033, 0.4039, 0.9965, 0.87]], "main": 0, "faces": [{"box": [0.1465, 0.1214, 0.1442, 0.2728]}]}
        out = self.run_js([["setFrames", [[leg, self.MID]]], ["anchorTpl", [[self.bg(3, W, H), self.cut(x=-540, y=260, w=2160, h=H - 260)], [3, 1], {"w": W, "h": H}]]])
        c = out[1][1]
        self.assertEqual((c["x"], c["y"], c["fx"], c["fy"]), (-540, 260, 0.5, 0.5))

    def test_cutout_focus_on_main_head_not_other_face(self):
        """시험 영상 004(18.6초): 주인공(오른쪽) 얼굴을 못 찾고 다른 사람(왼쪽) 얼굴만 있음 → 초점은 남의 얼굴인데 지킬 머리는 주인공 →
        누끼는 주인공 머리 쪽으로 다시 맞춤 (레이어를 옮겨 화면 밖 칸을 만들지 않음)."""
        W, H = 1080, 1920
        f = {"t": 1, "persons": [[0.2656, 0.2841, 0.0663, 0.3612, 0.8], [0.6404, 0.3165, 0.0674, 0.3254, 0.78]], "main": 1, "faces": [{"box": [0.3111, 0.298, 0.0154, 0.0401]}]}
        out = self.run_js([["setFrames", [[f, self.MID]]], ["anchorTpl", [[self.bg(3, W, H), self.cut(x=0, y=0, w=W, h=H)], [3, 1], {"w": W, "h": H}]]])
        c = out[1][1]
        self.assertEqual((c["x"], c["y"]), (0, 0), "화면 전체 누끼는 옮기지 않고 자르는 자리로")
        dw = H * 16 / 9
        hx = (W - dw) * c["fx"] + (0.6404 + 0.0674 / 2) * dw
        self.assertAlmostEqual(hx, W / 2, delta=W * 0.05, msg="주인공 머리가 화면 가운데")

    def test_cutout_big_face_kept_whole(self):
        """세로 영상(006) 큰 얼굴을 롱폼 칸에: 얼굴 가운데를 칸 위 1/3 에 두면 머리(이마·머리카락)가 칸 위로 넘침 → 머리가 다 들게 조금 내림."""
        W, H = 1280, 720
        f = {"t": 1, "persons": [[0.0034, 0.0648, 0.7311, 0.901, 0.9]], "main": 0, "faces": [{"box": [0.2013, 0.172, 0.1682, 0.1299]}]}
        out = self.run_js([["setInfo", [1080, 1920]], ["setFrames", [[f, self.MID]]],
                           ["anchorTpl", [[self.bg(3, W, H), self.cut(x=0, y=0, w=W, h=H)], [3, 1], {"w": W, "h": H}]]])
        c = out[2][1]
        dh = W / (1080 / 1920)
        fc = f["faces"][0]["box"]
        top = (H - dh) * c["fy"] + max(0, fc[1] - fc[3] * 0.6) * dh
        bottom = (H - dh) * c["fy"] + (max(0, fc[1] - fc[3] * 0.6) + fc[3] * 1.65) * dh
        seen = (min(bottom, H) - max(top, 0)) / (bottom - top)
        self.assertGreaterEqual(seen, 0.95, f"머리가 칸에 95% 넘게 ({top:.0f}~{bottom:.0f})")
        self.assertEqual(c["y"], 0)


    # ----- D-273 (검토): 레이어를 옮겨도 템플릿이 덮던 화면 끝은 계속 덮음 — 원본 테두리에 닿은 사람이 흰 테두리 친 곧은 선으로 잘려 보이지 않게 -----
    F006 = {"t": 1, "persons": [[0.0034, 0.0648, 0.7311, 0.901, 0.908]], "main": 0, "faces": [{"box": [0.2013, 0.172, 0.1682, 0.1299]}]}  # 시험 영상 006 19.9초 (세로 · 사람이 왼쪽 테두리에 닿음)
    F004 = {"t": 1, "persons": [[0.0155, 0.0, 0.9842, 0.9837, 0.862]], "main": 0, "faces": [{"box": [0.7754, 0.0925, 0.1431, 0.287]}]}  # 시험 영상 004 47.1초 (머리가 오른쪽)

    def test_shifted_cutout_never_exposes_source_border(self):
        """검토(006 · 롱폼 '두 장면 겹치기' k0·k1): 세로 원본은 이미지 폭 = 레이어 폭이라 왼쪽 레이어 끝이 곧 원본 왼쪽 테두리 →
        예전엔 x −200 → 75 로 옮겨 테두리에 닿은 팔·주먹이 x 75 에서 곧은 흰 선으로 잘림. 이제 이미지 왼쪽 끝은 화면 왼쪽 끝(≤ 0)까지만."""
        W, H = 1280, 720
        for bg_t in (1, 3):  # k0: 배경도 같은 장면 · k1: 배경은 다른 장면
            cut = self.cut(x=-200, y=0, w=W, h=H)
            out = self.run_js([["setInfo", [1080, 1920]], ["setFrames", [[self.F006, self.MID]]],
                               ["anchorTpl", [[self.bg(bg_t, W, H), cut], [bg_t, 1 if bg_t == 3 else 3], {"w": W, "h": H}]]])
            bg, c = out[2]
            dw = H * (1080 / 1920) if W / H <= 1080 / 1920 else W  # cover: 세로 원본은 레이어 폭 그대로
            self.assertEqual(dw, W)
            self.assertLessEqual(c["x"] + (W - dw) * c["fx"], 1e-6, "이미지(원본 테두리) 왼쪽 끝이 화면 안으로 들어오지 않음")
            self.edges_kept(cut, c, W, H)
            self.assertGreater(c["x"], -200, "머리 쪽으로는 옮김")
            self.assertGreaterEqual(self.head_in_canvas(c, W, H, self.F006, 1080 / 1920)[0], 0.95)
            if bg_t == 1:  # 누끼와 같은 장면 배경과 꼭 겹침 (옆·위아래로 비껴 머리가 두 번 보이지 않음) — 배경도 누끼처럼 머리가 다 들게
                self.assertEqual((c["x"], c["y"], c["fx"], c["fy"]), (bg["x"], bg["y"], bg["fx"], bg["fy"]))
            else:
                self.assertEqual(bg["src"], "/frame?name=x&t=3", "다른 장면 배경은 그대로 자기 장면")

    def test_full_canvas_cutout_side_band_does_not_move(self):
        """검토(004 47.1초 · 롱폼 '큰 글씨 2줄'·'네온 강조' k1): 머리는 다 보이고 오른쪽 8% 띠에만 걸림 → 예전엔 x 0 → −444 · y 0 → +22 로 옮겨
        든 팔 위가 흰 막대로, 다리가 x 836 에서 잘림. 화면 전체 누끼는 이제 옮기지 않음 (머리 아래로 내리는 여백도)."""
        W, H = 1280, 720
        cut = self.cut(x=0, y=0, w=W, h=H)
        out = self.run_js([["setFrames", [[self.F004, self.MID]]], ["anchorTpl", [[self.bg(3, W, H), cut], [3, 1], {"w": W, "h": H}]]])
        c = out[1][1]
        self.assertEqual((c["x"], c["y"]), (0, 0))
        self.assertGreaterEqual(self.head_in_canvas(c, W, H, self.F004)[0], 0.95)
        SW, SH = 1080, 1920  # 쇼츠 화면 전체 누끼도 같음
        out = self.run_js([["setFrames", [[self.F004, self.MID]]], ["anchorTpl", [[self.bg(3, SW, SH), self.cut(x=0, y=0, w=SW, h=SH)], [3, 1], {"w": SW, "h": SH}]]])
        self.assertEqual((out[1][1]["x"], out[1][1]["y"]), (0, 0))

    def test_person_emphasis_right_head_stops_at_canvas_edge(self):
        """검토(004 47.1초 · 쇼츠 '인물 강조'): 예전엔 x −540 → −1168 로 옮겨 머리는 들어와도 오른쪽 레이어 끝(x 992)에서 다리가 잘림 →
        오른쪽 끝은 화면 오른쪽 끝까지만(x −1080) · 머리는 그래도 화면 안·옆 끝 밖."""
        W, H = 1080, 1920
        cut = self.cut(x=-540, y=260, w=2160, h=H - 260)
        self.assertEqual(self.head_in_canvas(cut, W, H, self.F004)[0], 0, "고치기 전: 머리가 화면 밖")
        out = self.run_js([["setFrames", [[self.F004, self.MID]]], ["anchorTpl", [[self.bg(1, W, H), cut], [1, 3], {"w": W, "h": H}]]])
        c = out[1][1]
        self.assertEqual(c["x"], W - 2160)
        self.assertEqual(c["y"], 260)
        self.edges_kept(cut, c, W, H)
        self.assertGreaterEqual(self.head_in_canvas(c, W, H, self.F004)[0], 0.95)
        self.assertTrue(self.off_edge_band(c, W, H, self.F004), c)


class FrameChoice5Tests(unittest.TestCase):
    def test_lesson_needs_visible_head(self):
        ball = [0.45, 0.8, 0.04, 0.06]
        legs = [[0.35, 0.0, 0.2, 0.75, 0.9]]
        full = [[0.35, 0.2, 0.2, 0.62, 0.9]]
        self.assertFalse(thumb.scene_flags(legs, ball, None)["lesson"], "다리만 (머리가 화면 위로 잘림)")
        self.assertTrue(thumb.scene_flags(full, ball, None)["lesson"])

    def test_back_view_checked_for_mid_players(self):
        p = [[0.4, 0.2, 0.15, 0.4, 0.9]]
        self.assertTrue(thumb.scene_flags(p, None, [])["back"], "키 0.4H 주인공도 얼굴이 없으면 뒷모습 (예전엔 0.55H 넘을 때만)")
        self.assertFalse(thumb.scene_flags(p, None, [{"box": [0.45, 0.22, 0.03, 0.05]}])["back"])

    def test_subject_blur_mult(self):
        self.assertEqual(thumb.subject_blur_mult(0.1), 1)
        self.assertAlmostEqual(thumb.subject_blur_mult(0.35), 0.68)
        self.assertEqual(thumb.subject_blur_mult(0.9), 0.3)

    def test_head_faces_without_model(self):
        from PIL import Image
        with mock.patch.dict(face._SESS, {}, clear=True):
            self.assertEqual(face.head_faces(Image.new("RGB", (64, 36)), [[0.4, 0.2, 0.1, 0.6]]), [])

    def test_head_faces_maps_crop_to_frame_and_filters(self):
        from PIL import Image
        img = Image.new("RGB", (1920, 1080), (90, 120, 90))
        p = [0.4, 0.2, 0.1, 0.6, 0.9]  # 머리 쪽 = x 0.385~0.515, y 0.176~0.428
        hits = [(0.4, 0.1, 0.55, 0.3, 0.95),   # 머리 쪽 위 가운데 → 얼굴
                (0.0, 0.85, 0.12, 0.99, 0.97)]  # 위 40% 밖 (어깨 아래) → 뺌
        with mock.patch.dict(face._SESS, {"detect": object()}), mock.patch.object(face, "_detect", return_value=hits), \
                mock.patch.object(face, "_expr", return_value=({"happiness": 0.1}, 0.8)):
            fs = face.head_faces(img, [p])
            again = face.head_faces(img, [p], known=fs)
        self.assertEqual(len(fs), 1)
        x, y, w, h = fs[0]["box"]
        self.assertTrue(p[0] <= x + w / 2 <= p[0] + p[2] and p[1] - 0.02 <= y + h / 2 <= p[1] + p[3] * 0.4)
        self.assertTrue(fs[0]["head"])
        self.assertEqual(again, [], "이미 찾은 얼굴과 겹치면 다시 넣지 않음")

    def test_grade_absolute_saturation(self):
        import numpy as np
        from PIL import Image
        rng = np.random.default_rng(3)
        a = np.zeros((90, 160, 3), np.uint8)
        a[..., 0] = rng.integers(170, 250, (90, 160))
        a[..., 1] = rng.integers(30, 90, (90, 160))
        a[..., 2] = rng.integers(140, 220, (90, 160))  # 분홍 낙서 벽 (채도 높음)
        im = Image.fromarray(a)
        g = thumb.auto_grade(im)
        arr = np.asarray(im.resize((320, 180)), np.float32)
        mx, mn = arr.max(2), arr.min(2)
        s0 = float(((mx - mn) / np.maximum(mx, 1)).mean())
        after = thumb._sat_after(arr, g)
        self.assertLessEqual(after, max(thumb.SAT_ABS_MAX, s0) + 0.02, "원본보다 쨍하게 올리지 않음")


class Copy5Tests(unittest.TestCase):
    TEXTS = ["풋살 기본기 중에 제일 중요한 발바닥 드래그", "발바닥으로 공을 끌어서 수비를 속이는 기술이에요", "슛하는 척하다가 드래그로 방향을 바꾸세요",
             "실수하는 분들이 많은데 무게중심이 뒤에 있으면 안 돼요", "이거 하나만 제대로 해도 수비가 못 따라와요"]

    def items(self, title="발바닥 드래그 기본기", texts=None):
        texts = texts or self.TEXTS
        C, tp = tc.rule_candidates(title, texts, rotate=False)
        body = " ".join(texts) + " " + title
        return {c["pid"]: c for c in (tc.finish(c, tp, body) for c in C)}

    def test_hook_templates(self):
        it = self.items()
        self.assertEqual((it["regret"]["l1"], it["regret"]["l2"]), ("이것도 모르고", "드래그 했네.."), "줄이 길면 주제의 마지막 낱말")
        self.assertNotIn("semi", it, "판정 q5 1회차(D-111): 풋살해주호 제목('플랩 세미 가는 기술')을 그대로 가져온 틀은 뺌")
        self.assertFalse(any("플랩 세미" in c["l1"] + c["l2"] for c in it.values()))
        self.assertIn("hide", it)
        for pid in ("stuck", "diff", "please", "beatit"):  # 우리 말로 쓴 새 훅 틀 (대사에 실수·'못 따라와요'가 있음)
            self.assertIn(pid, it)
        self.assertTrue(all(len(x) <= 10 for c in it.values() if c["pid"] in tc.HOOK_SUB for x in (c["l1"], c["l2"])))

    def test_hooks_rotate_per_video(self):
        """판정 q5 1회차(D-111): 'X가 / 이렇게 쉬웠어?' 18/18 묶음 · '이것만 알면 / X 끝!' 9/9 영상 → 영상(제목)마다 훅 틀 4개·흔한 틀 4개만, 늘 같게."""
        C, _ = tc.rule_candidates("발바닥 드래그 기본기", self.TEXTS)
        C2, _ = tc.rule_candidates("발바닥 드래그 기본기", self.TEXTS)
        hooks = [c["pid"] for c in C if c["pid"] in tc.HOOK_PIDS]
        self.assertLessEqual(len(hooks), tc.HOOK_KEEP)
        self.assertLessEqual(len([c for c in C if c["pid"] in tc.GENERIC_PIDS]), tc.GENERIC_KEEP)
        self.assertEqual([c["pid"] for c in C], [c["pid"] for c in C2], "다시 열어도 같은 틀")
        sets = {tuple(sorted(c["pid"] for c in tc.rule_candidates(t, self.TEXTS)[0] if c["pid"] in tc.HOOK_PIDS + tc.GENERIC_PIDS))
                for t in ("발바닥 드래그 기본기", "발바닥 드래그 실전", "발바닥 드래그 강좌", "발바닥 드래그 레슨")}
        self.assertGreater(len(sets), 1, "제목이 다르면 다른 틀 묶음")

    def test_hooks_beat_labels(self):
        it = self.items()
        labels = [c for c in it.values() if c["pid"].startswith("detail") and not c["pid"].startswith("detailq")]
        for c in labels:
            self.assertTrue(tc.label_pair(c))
            self.assertLess(c["score"], it["regret"]["score"], f"라벨 {c['l1']}/{c['l2']} 이 훅보다 아래")

    def test_spoken_and_label(self):
        self.assertTrue(tc.spoken({"l1": "이것도 모르고", "l2": "드래그 했네.."}))
        self.assertTrue(tc.spoken({"l1": "'여기' 봐야", "l2": "뚫립니다"}))
        self.assertFalse(tc.spoken({"l1": "슈팅", "l2": "디딤발 위치"}))
        self.assertTrue(tc.label_pair({"l1": "슈팅", "l2": "디딤발 위치"}))
        self.assertFalse(tc.label_pair({"l1": "수비를 속이는", "l2": "발바닥 드래그"}), "꾸밈말 + 이름씨는 라벨이 아님")

    def test_grounded_no_overlap_needed_but_numbers_and_credentials(self):
        body = " ".join(self.TEXTS)
        q = "발바닥으로 공을 끌어서 수비를 속이는 기술이에요"
        self.assertTrue(tc.grounded("이것도 모르고", "드래그 했네..", q, body), "훅은 대사와 낱말이 안 겹쳐도 근거 인용이 맞으면 통과")
        self.assertFalse(tc.grounded("3초면", "수비가 속아요", q, body), "대사에 없는 숫자")
        self.assertFalse(tc.grounded("국가대표가 쓰는", "드래그", q, body), "대사에 없는 이력")
        self.assertFalse(tc.grounded("이것도 모르고", "드래그 했네..", "대사에 없는 말이에요", body))

    def test_prompt_v6(self):
        self.assertGreaterEqual(tc.PROMPT_VER, 6)
        with mock.patch.object(tc, "_texts", return_value=self.TEXTS):
            p = tc.prompt("20261007_THQSTOCK004_발바닥 드래그 기본기.mp4")
        self.assertIn("서술어", p)
        self.assertIn("답(팁 내용)을 다 말하지 마세요", p)
        self.assertIn("'여기' 봐야 / 뚫립니다", p)
        self.assertNotIn("8자 이내", p)


class Brand5Tests(unittest.TestCase):
    def test_old_default_font_migrates(self):
        self.assertEqual(thumb.BRAND_DEFAULT["font"], "Pretendard Black")
        self.assertEqual(thumb.check_brand({"font": "Black Han Sans"})["font"], "Pretendard Black", "예전 기본값 그대로 저장된 키트")
        self.assertEqual(thumb.check_brand({"font": "Black Han Sans", "fontV": 2})["font"], "Black Han Sans", "새 판에서 고른 글꼴은 그대로")
        self.assertEqual(thumb.check_brand({"font": "Jua"})["font"], "Jua")
        self.assertTrue(thumb.check_brand({})["autoLogo"])
        self.assertFalse(thumb.check_brand({"autoLogo": False})["autoLogo"])


if __name__ == "__main__":
    unittest.main()
