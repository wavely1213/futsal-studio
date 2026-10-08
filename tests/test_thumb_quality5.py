"""썸네일 퀄리티 판정 5회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality5

- 글자(p8_ai.js, node): 두 줄 크기 비율은 문장이면 같은 크기·꼬리표면 확실히 작게 · 위계 점수는 1.0~1.25·2.2 넘게가 만점 · 프리텐다드 블랙은 얇은 외곽선 ·
  작은 검은고딕은 도현이 아니라 프리텐다드 블랙으로 (D-090)
- 구도(p8_ai.js, node): 쇼츠 꽉 찬 장면은 원본 1080p 가로·세로 영상만 (720p 는 예전 칸) · 다른 사람 머리도 지킴 · 발·공 클로즈업은 발 이야기 문구에만 (D-090·D-091)
- 예전 템플릿 칸을 얼굴·머리에 맞춰 자르기(anchorSlot·anchorTpl · P1, D-092)
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
      "slotFocus", "anchorSlot", "frameT", "frameMeta", "anchorTpl")
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
  'var setInfo = (w, h) => { INFO = { width: w, height: h }; return true; }; var setFrames = fs => { AI.frames = fs; return true; };' +
  NAMES.map(pick).join('\n') + '; globalThis.T = {' + NAMES.join(',') + ', setInfo, setFrames};');
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
        self.assertFalse(out[4], "720p 가로 영상은 2.67배 → 예전 칸")
        self.assertTrue(out[6], "세로 영상")
        self.assertFalse(out[7], "롱폼은 해당 없음")

    def test_heads_of_others_and_foot_rule(self):
        f = {"persons": [[0.4, 0.2, 0.1, 0.6, 0.9], [0.7, 0.3, 0.08, 0.3, 0.9], [0.1, 0.5, 0.03, 0.1, 0.9], [0.85, 0.0, 0.1, 0.5, 0.9]], "main": 0, "faces": []}
        foot = {"persons": [[0.3, 0.0, 0.4, 0.9, 0.9]], "main": 0, "ball": [0.45, 0.7, 0.08, 0.1], "faces": [], "kind": "mid", "color": 40}
        out = self.run_js([["headsOf", [f]], ["footOk", [foot, {"l1": "발바닥", "l2": "드래그 했네.."}]], ["footOk", [foot, {"l1": "왜 막힐까?", "l2": "상체 페인트"}]],
                           ["footOk", [dict(foot, color=95), {"l1": "발바닥", "l2": "드래그"}]], ["srcHeadCut", [foot]]])
        heads = out[0]
        self.assertEqual(len(heads), 2, "주인공 + 키 0.15H 넘는 사람 (작은 사람·머리가 화면 위로 잘린 사람은 뺌)")
        self.assertTrue(heads[0]["main"])
        self.assertFalse(heads[1]["main"])
        self.assertTrue(out[1], "발 이야기 문구 + 차분한 배경")
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
        C, tp = tc.rule_candidates(title, texts)
        body = " ".join(texts) + " " + title
        return {c["pid"]: c for c in (tc.finish(c, tp, body) for c in C)}

    def test_hook_templates(self):
        it = self.items()
        self.assertEqual((it["regret"]["l1"], it["regret"]["l2"]), ("이것도 모르고", "드래그 했네.."), "줄이 길면 주제의 마지막 낱말")
        self.assertIn("semi", it)
        self.assertIn("hide", it)
        self.assertTrue(all(len(x) <= 10 for c in it.values() if c["pid"] in ("regret", "foryou", "semi", "hide") for x in (c["l1"], c["l2"])))

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
