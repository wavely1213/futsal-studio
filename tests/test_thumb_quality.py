"""썸네일 퀄리티 판정 1회차 반영 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_quality

- 장면 표시(thumb.scene_flags): 벤치(나란히 앉은 사람)·뒷모습·좌우 끝에 걸림·아주 작음·레슨 액션(공이 주인공 발 가까이) → 점수 배율
- 장면 후보: 얼굴 클로즈업은 다른 장면이 있으면 CLOSE_MAX 까지 · 박힌 글자 상자(tboxes)
- 자동 누끼: 마스크 다듬기(clean_mask: 흐린 번짐 끊기·주인공과 안 닿는 조각 버림) · 품질(cut_quality: 새는 곳·채움·조각·얼굴 빈 곳·흐린 테두리)
  · 품질 JSON 기억 · 누끼 딸 장면 고르기(작은·흐린·뒷모습·벤치 제외)
- 분석: 클로드 문구를 장면 분석과 동시에 (로그인돼 있고 브랜드 키트 '클로드 문구 자동'이 켜졌을 때만) · 다 된 분석에 품질 포함
- 화면 계산(p8_ai.js 의 순수 함수: 숫자 글꼴·두 줄 합치기·장면 품질·같은 장면 묶음·머리 상자·보이는 박힌 글자)을 node 로
인터넷·진짜 클로드·배경 제거 모델은 쓰지 않음 (모두 가짜)."""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import thumb  # noqa: E402
import thumbcopy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None


class SceneFlagTests(unittest.TestCase):
    """판정 1회차: 벤치에 앉은 선수·관중·뒷모습·끝에 걸린 사람이 뽑힘 → 장면 표시로 감점, 공을 다루는 순간은 가점."""

    def test_bench_row_is_flagged_but_ball_fight_is_not(self):
        bench = [[0.157, 0.40, 0.146, 0.55, 0.8], [0.001, 0.43, 0.173, 0.51, 0.8], [0.50, 0.18, 0.156, 0.76, 0.8]]  # 붙어 앉은 둘 + 서 있는 사람
        self.assertTrue(thumb.scene_flags(bench, None, None)["bench"])
        ball_at_feet = [0.10, 0.92, 0.03, 0.04, 0.6]
        self.assertFalse(thumb.scene_flags(bench, ball_at_feet, None)["bench"], "공이 그 발 바로 앞이면 공 다툼")
        runners = [[0.0759, 0.47, 0.085, 0.28, 0.8], [0.4571, 0.44, 0.056, 0.22, 0.6], [0.252, 0.48, 0.0485, 0.19, 0.8]]  # 떨어져 뛰는 선수들
        self.assertFalse(thumb.scene_flags(runners, None, None)["bench"])

    def test_back_view_needs_face_model_and_visible_head(self):
        big = [[0.13, 0.16, 0.69, 0.83, 0.9]]
        self.assertTrue(thumb.scene_flags(big, None, [])["back"], "크게 나온 사람인데 얼굴이 없음")
        side_face = [{"box": [0.18, 0.24, 0.12, 0.12]}]
        self.assertTrue(thumb.scene_flags(big, None, side_face)["back"], "옆에 걸친 다른 사람 얼굴은 그 사람 얼굴이 아님")
        own = [{"box": [0.42, 0.2, 0.12, 0.12]}]
        self.assertFalse(thumb.scene_flags(big, None, own)["back"])
        self.assertFalse(thumb.scene_flags(big, None, None)["back"], "얼굴 모델이 없으면 판단하지 않음")
        legs = [[0.3, 0.0, 0.5, 0.9, 0.9]]
        self.assertFalse(thumb.scene_flags(legs, None, [])["back"], "다리만 나온 장면은 headless 가 따로 봄")

    def test_edge_small_lesson(self):
        self.assertTrue(thumb.scene_flags([[0.0, 0.3, 0.06, 0.5, 0.9]], None, None)["edge"])
        self.assertTrue(thumb.scene_flags([[0.4, 0.5, 0.03, 0.12, 0.9]], None, None)["small"])
        p = [[0.4, 0.3, 0.1, 0.45, 0.9]]
        self.assertTrue(thumb.scene_flags(p, [0.46, 0.72, 0.03, 0.04, 0.5], None)["lesson"], "공이 주인공 발 앞")
        self.assertFalse(thumb.scene_flags(p, [0.9, 0.2, 0.03, 0.04, 0.5], None)["lesson"])
        self.assertFalse(any(thumb.scene_flags([], None, None).values()))

    def test_crowd_of_upper_bodies(self):
        crowd = [[0.05, 0.12, 0.3, 0.87, 0.9], [0.35, 0.14, 0.2, 0.84, 0.9], [0.6, 0.19, 0.3, 0.8, 0.9]]  # 난간 뒤 관중 셋 (아래가 잘림)
        self.assertTrue(thumb.scene_flags(crowd, None, None)["crowd"])
        self.assertFalse(thumb.scene_flags(crowd, [0.4, 0.9, 0.03, 0.04, 0.5], None)["crowd"], "공이 있으면 경기 장면일 수 있음")
        self.assertFalse(thumb.scene_flags(crowd[:2], None, None)["crowd"], "둘은 인터뷰일 수 있음")
        self.assertLess(thumb.flags_mult({"crowd": True}), 0.8)

    def test_flags_multiplier_order(self):
        base = thumb.flags_mult({})
        self.assertEqual(base, 1.0)
        self.assertLess(thumb.flags_mult({"bench": True}), 0.6)
        self.assertGreater(thumb.flags_mult({"lesson": True}), 1.2)
        self.assertGreater(thumb.flags_mult({"back": True, "lesson": True}), thumb.flags_mult({"back": True}), "공을 다루는 뒷모습은 덜 깎음")


def _mask(w=200, h=120):
    import numpy as np
    return np.zeros((h, w), np.uint8)


class CutQualityTests(unittest.TestCase):
    """판정 1회차: 작은·흐린 선수 누끼가 하얗게 번지고 외곽선이 벽으로 샘 → 다듬기 + 품질 검사."""

    def test_components_count(self):
        import numpy as np
        b = np.zeros((10, 10), bool)
        b[1:3, 1:3] = True
        b[6:9, 5:9] = True
        lab, sizes = thumb._components(b)
        self.assertEqual(sorted(sizes[1:]), [4, 12])
        self.assertEqual(int(lab[1, 1]) != int(lab[7, 6]), True)

    def test_clean_drops_far_blobs_and_haze(self):
        import numpy as np
        from PIL import Image
        a = _mask()
        a[30:110, 80:120] = 255      # 주인공 (상자 안)
        a[5:25, 5:40] = 255          # 벽 조각 (상자 밖)
        a[110:120, 60:140] = 60      # 공 밑 흐린 잔상 (알파 0.24)
        box = [80 / 200, 30 / 120, 40 / 200, 80 / 120]
        out = np.asarray(thumb.clean_mask(Image.fromarray(a), box))
        self.assertGreater(out[60:100, 90:110].mean(), 240, "주인공은 그대로")
        self.assertLess(out[5:25, 5:40].max(), 10, "상자와 안 닿는 조각은 버림")
        self.assertLess(out[112:120, 60:75].max(), 30, "흐린 잔상은 끊음")

    def test_clean_erases_burned_text_but_keeps_face(self):
        """누끼 모델이 남긴 박힌 글자 조각('(진정해ㅎ')은 지우고, 글자 상자와 겹친 얼굴은 남김."""
        import numpy as np
        from PIL import Image
        a = _mask()
        a[20:110, 80:120] = 255
        box = [80 / 200, 20 / 120, 40 / 200, 90 / 120]
        text = [[70 / 200, 80 / 120, 60 / 200, 20 / 120]]     # 몸통 아래쪽에 박힌 글자
        face = [{"box": [90 / 200, 22 / 120, 20 / 200, 20 / 120]}]
        out = np.asarray(thumb.clean_mask(Image.fromarray(a), box, text + [[88 / 200, 20 / 120, 24 / 200, 25 / 120]], [f["box"] for f in face]))
        self.assertLess(out[85:95, 85:115].max(), 10, "글자 상자 자리는 지움")
        self.assertGreater(out[25:40, 95:105].mean(), 240, "얼굴은 글자 상자와 겹쳐도 남김")
        self.assertGreater(out[50:70, 90:110].mean(), 240)

    def test_quality_good_and_bad(self):
        from PIL import Image
        a = _mask()
        a[30:110, 85:115] = 255
        box = [80 / 200, 28 / 120, 40 / 200, 84 / 120]
        good = thumb.cut_quality(Image.fromarray(a), box)
        self.assertTrue(good["ok"], good)
        self.assertGreater(good["q"], 0.8)
        leak = a.copy()
        leak[0:120, 150:200] = 255  # 벽으로 샘
        q = thumb.cut_quality(Image.fromarray(leak), box)
        self.assertFalse(q["ok"])
        self.assertGreater(q["leak"], thumb.CUT_Q["leak"])
        holey = a.copy()
        holey[32:46, 92:108] = 0  # 얼굴 자리가 비었음 (하얗게 날아감)
        q = thumb.cut_quality(Image.fromarray(holey), box, faces=[{"box": [92 / 200, 32 / 120, 16 / 200, 14 / 120]}])
        self.assertFalse(q["ok"])
        self.assertLess(q["face"], thumb.CUT_Q["face"])
        soft = (a // 2).copy()  # 전부 반투명 (뭉개진 누끼)
        q = thumb.cut_quality(Image.fromarray(soft), box)
        self.assertFalse(q["ok"])
        empty = thumb.cut_quality(Image.fromarray(_mask()), box)
        self.assertFalse(empty["ok"], "아무것도 못 땀")

    def test_cut_targets_skip_small_blurry_back(self):
        it = lambda t, h, **k: dict({"t": t, "persons": [[0.4, 0.2, 0.1, h, 0.9]], "main": 0, "blur": 0.1, "flags": []}, **k)  # noqa: E731
        items = [it(1, 0.2), it(2, 0.5, blur=0.5), it(3, 0.5, flags=["back"]), it(4, 0.5), it(5, 0.15, faces=[{"box": [0.4, 0.2, 0.05, 0.1]}]), it(6, 0.6), it(7, 0.7)]
        ts = [x["t"] for x, _ in thumb._cut_targets(items)]
        self.assertEqual(ts, [4, 5, 6, 7][:thumb.AUTO_CUTS], "작은 선수·흔들림·뒷모습은 빼고 · 얼굴 장면은 넣음")
        same = [it(t, 0.5, hash="ffff0000ffff0000") for t in (1, 2, 3)] + [it(4, 0.5, hash="0000ffff0000ffff"), it(5, 0.5, hash="00ff00ff00ff00ff")]
        ts = [x["t"] for x, _ in thumb._cut_targets(same)]
        self.assertEqual(ts[:3], [1, 4, 5], "같은 화면(지문)보다 다른 장면 먼저")


class AnalyzeTests(unittest.TestCase):
    """분석: 누끼 품질을 함께 돌려주고 기억 · 클로드 문구는 조건이 맞을 때만 동시에."""
    NAME = "20261007_ABCDEFGHIJK_발바닥 드래그 기본기.mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="풋살 분석 "))
        w = self.tmp / "작업 폴더"
        self.p = [mock.patch.object(core, "WORK", w), mock.patch.object(core, "VIDEOS", w / "videos"), mock.patch.object(core, "ANALYSIS", w / "analysis"),
                  mock.patch.object(thumb, "THUMBS", w / "thumbnails"), mock.patch.object(thumb, "ASSETS", w / "thumbnails" / "assets")]
        for p in self.p:
            p.start()
        (w / "videos").mkdir(parents=True)
        (w / "thumbnails" / "assets").mkdir(parents=True)
        (w / "videos" / self.NAME).write_bytes(b"0")
        a = core.adir(self.NAME)
        a.mkdir(parents=True)
        (a / "transcript.json").write_text(json.dumps([{"start": 0, "end": 2, "text": "발바닥 드래그로 수비를 속이세요"}], ensure_ascii=False), encoding="utf-8")
        self.items = [{"t": 3.0, "url": "/frame?x", "persons": [[0.4, 0.2, 0.2, 0.6, 0.9]], "main": 0, "blur": 0.1, "flags": [], "score": 5}]

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_cut(self, name, t, box=None, kind="fast", faces=None, tboxes=None):
        p = thumb._cut_path(name, t, box, kind)
        p.write_bytes(b"\x89PNG")
        q = {"ok": False, "q": 0.4, "leak": 0.3}
        p.with_suffix(".json").write_text(json.dumps(q), encoding="utf-8")
        return p, q

    def test_analyze_returns_quality_and_cache_needs_it(self):
        with mock.patch.object(thumb, "frame_candidates", return_value=self.items), mock.patch.object(thumb, "cut_auto", side_effect=self.fake_cut), \
                mock.patch.object(thumb, "_start_ai_copy", return_value=None), mock.patch.object(thumb, "_ai_on", return_value=False), \
                mock.patch.object(thumb, "cached_candidates", return_value=self.items):
            r = thumb.analyze(self.NAME, log=lambda m: None)
            self.assertEqual(r["cuts"]["3.0"]["q"]["ok"], False)
            c = thumb.cached_analysis(self.NAME)
            self.assertEqual(c["cuts"]["3.0"]["q"]["q"], 0.4)
            with mock.patch.object(thumb, "_ai_on", return_value=True):
                self.assertIsNone(thumb.cached_analysis(self.NAME), "클로드 자동이 켜졌는데 장면 점수가 없으면 분석 작업으로")
            thumb._cut_path(self.NAME, 3.0, self.items[0]["persons"][0][:4]).with_suffix(".json").unlink()
            self.assertIsNone(thumb.cached_analysis(self.NAME), "품질 기록이 없으면 다시 분석 (예전 누끼)")

    def test_ai_copy_runs_only_when_ready_and_enabled(self):
        ran = threading.Event()
        with mock.patch.object(thumbcopy, "ai_state", return_value="ready"), mock.patch.object(thumbcopy, "run_ai", side_effect=lambda *a, **k: ran.set()) as ra:
            th = thumb._start_ai_copy(self.NAME, lambda m: None)
            self.assertIsNotNone(th)
            th.join(5)
            self.assertTrue(ran.is_set())
            self.assertEqual(ra.call_args.kwargs.get("progress"), False, "분석 진행 표시를 덮지 않음")
        with mock.patch.object(thumbcopy, "ai_state", return_value=""):
            self.assertIsNone(thumb._start_ai_copy(self.NAME, lambda m: None), "로그인 안 됨 → 규칙 문구만")
        thumb.save_brand({"aiCopy": False})
        with mock.patch.object(thumbcopy, "ai_state", return_value="ready"):
            self.assertIsNone(thumb._start_ai_copy(self.NAME, lambda m: None), "브랜드 키트에서 끔")
        thumb.save_brand({"aiCopy": True})
        (core.adir(self.NAME) / thumbcopy.CACHE).write_text(json.dumps({"sig": thumbcopy._sig(self.NAME), "items": [{"l1": "수비를 속이는", "l2": "드래그", "emph": "드래그", "sub": ""}]}, ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(thumbcopy, "ai_state", return_value="ready"):
            self.assertIsNone(thumb._start_ai_copy(self.NAME, lambda m: None), "기억한 클로드 문구가 있으면 다시 안 부름")

    def test_ai_frames_rated_saved_and_attached(self):
        """클로드 장면 고르기: 후보 시트를 그림으로 넘기고, 대답(번호별 점수)을 장면 시각으로 기억 → 분석 결과에 ai·aiWhy · 후보가 바뀌면 안 씀."""
        import claude_cli
        from PIL import Image
        items = [dict(self.items[0], t=1.0), dict(self.items[0], t=2.0)]
        seen = {}

        def fake_grab(name, t, w=1920):
            p = self.tmp / f"f{t}.jpg"
            Image.new("RGB", (320, 180), (40, 120, 60)).save(p)
            return p

        def fake_run(prompt, images=None, **k):
            seen["prompt"], seen["img"] = prompt, images[0][1]
            seen["size"] = Image.open(images[0][0]).size
            return {"text": '앞말 {"frames": [{"n": 1, "score": 8.5, "why": "공 다루는 순간"}, {"n": 2, "score": 2, "why": "관중"}, {"n": 9, "score": 9}]}'}
        with mock.patch.object(thumb, "grab", side_effect=fake_grab), mock.patch.object(claude_cli, "run", side_effect=fake_run):
            out = thumb.run_ai_frames(self.NAME, items, log=lambda m: None)
        self.assertEqual(out, {"1.0": {"score": 8.5, "why": "공 다루는 순간"}, "2.0": {"score": 2.0, "why": "관중"}}, "범위 밖 번호는 버림")
        self.assertEqual(seen["img"], "frames.jpg")
        self.assertIn("Read 도구", seen["prompt"])
        self.assertIn("발바닥 드래그", seen["prompt"], "영상 제목·주제를 알려 줌")
        withai = thumb._with_ai_frames(self.NAME, items)
        self.assertEqual((withai[0]["ai"], withai[1]["ai"]), (8.5, 2.0))
        self.assertNotIn("ai", items[0], "원본 목록은 그대로")
        self.assertIsNone(thumb.load_ai_frames(self.NAME, items[:1]), "후보 장면이 바뀌면 다시")
        with self.assertRaises(ValueError):
            thumb.parse_ai_frames("모르겠어요", 2)
        with mock.patch.object(thumb, "_ai_on", return_value=True):
            self.assertFalse(thumb.need_ai_frames(self.NAME, items), "받은 점수가 있으면 다시 안 부름")
            self.assertTrue(thumb.need_ai_frames(self.NAME, items[:1]), "후보가 바뀌면 다시")
        with mock.patch.object(thumb, "grab", side_effect=fake_grab), mock.patch.object(claude_cli, "run", side_effect=claude_cli.ClaudeError("limit", "한도")):
            self.assertIsNone(thumb.run_ai_frames(self.NAME, items[:1], log=lambda m: None), "클로드가 안 되면 규칙 점수만")
        with mock.patch.object(thumb, "_ai_on", return_value=True):
            self.assertFalse(thumb.need_ai_frames(self.NAME, items[:1]), "막 실패했으면 1시간은 다시 안 부름 (열 때마다 기다리지 않게)")

    def test_ai_ready_uses_status(self):
        import claude_cli
        with mock.patch.object(claude_cli, "find_exe", return_value=None):
            self.assertFalse(thumbcopy.ai_ready())
        with mock.patch.object(claude_cli, "find_exe", return_value="/x/claude"), mock.patch.object(claude_cli, "status", return_value={"state": "ready"}):
            self.assertTrue(thumbcopy.ai_ready())
        with mock.patch.object(claude_cli, "find_exe", return_value="/x/claude"), mock.patch.object(claude_cli, "status", side_effect=OSError("x")):
            self.assertFalse(thumbcopy.ai_ready())

    def test_text_boxes_skip_band_and_small(self):
        import avmodels
        from PIL import Image
        lines = [{"box": [0.1, 0.1, 0.5, 0.2], "h": 0.1, "text": "더 힘들까", "conf": 0.9},
                 {"box": [0.1, 0.85, 0.9, 0.95], "h": 0.1, "text": "자막", "conf": 0.9},
                 {"box": [0.5, 0.5, 0.55, 0.53], "h": 0.03, "text": "10", "conf": 0.9}]
        with mock.patch.object(avmodels, "available", return_value=True), mock.patch.object(avmodels, "ocr", return_value=lines):
            bs = thumb.text_boxes(Image.new("RGB", (64, 36)), "bottom", 0.8)
        self.assertEqual(bs, [[0.1, 0.1, 0.4, 0.1]])


JS_PURE = ("clipTo", "areaOf", "interArea", "imgRect", "f2c", "boxC", "mainBox", "mainFace", "headBox", "visibleText", "digitRuns", "joinCopy", "LOUD", "loud", "loudMult", "frameQ", "hamming", "sameFace", "sceneGroups", "usableFrame", "srcHeadCut", "footClose", "headlessBad", "isExpr", "aiFrames")
NODE_RUN = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => { let a = src.indexOf('function ' + n + '('); if (a < 0) { a = src.indexOf('const ' + n + ' ='); const e = src.indexOf(';\n', a); return src.slice(a, e + 1); }
  let d = 0, i = src.indexOf('{', a); for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
const NAMES = JSON.parse(process.argv[2]);
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v)); var TEXTY = 0.05; var DIGIT_FONT = "Pretendard Black"; var AI = { frames: [], cuts: {}, pick: null }; var setAI = o => { Object.assign(AI, o); return null; };' + NAMES.map(pick).join('\n') + '; globalThis.T = {setAI,' + NAMES.join(',') + '};');
const cases = JSON.parse(fs.readFileSync(0, 'utf8')); const out = cases.map(([fn, args]) => T[fn](...args));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(HAS_NODE, "node 가 없어 화면 계산 확인을 건너뜀")
class ScreenMathTests(unittest.TestCase):
    """p8_ai.js 의 순수 계산을 node 로 그대로 돌려 봄 (화면 픽셀은 e2e 가 봄)."""

    def run_js(self, cases):
        r = subprocess.run(["node", "-e", NODE_RUN, str(ROOT / "thumb_src/parts/p8_ai.js"), json.dumps(JS_PURE)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_digits_get_readable_font(self):
        out = self.run_js([["digitRuns", ["1대1 돌파", "Black Han Sans"]], ["digitRuns", ["돌파", "Black Han Sans"]], ["digitRuns", ["3초면 10번", "Pretendard Black"]]])
        self.assertEqual(out[0], [{"s": 0, "e": 1, "font": "Pretendard Black"}, {"s": 2, "e": 3, "font": "Pretendard Black"}], "판정 OCR: 검은고딕 '1대1' → '그대그'")
        self.assertEqual(out[1], [])
        self.assertEqual(out[2], [], "이미 숫자가 잘 읽히는 글꼴이면 그대로")

    def test_join_copy_moves_emphasis(self):
        out = self.run_js([["joinCopy", [{"l1": "플랩", "l2": "레벨업", "emph": [1, 0, 3]}]], ["joinCopy", [{"l1": "드래그", "l2": "", "emph": [0, 0, 3]}]]])
        self.assertEqual((out[0]["l1"], out[0]["l2"], out[0]["emph"]), ("플랩 레벨업", "", [0, 3, 6]))
        self.assertEqual(out[1]["l1"], "드래그")

    def test_frame_quality_penalties(self):
        f = lambda **k: dict({"score": 10, "flags": [], "blur": 0.1, "text": 0}, **k)  # noqa: E731
        out = self.run_js([["frameQ", [f(), 10]], ["frameQ", [f(flags=["bench"]), 10]], ["frameQ", [f(flags=["lesson"], score=8), 10]], ["frameQ", [f(text=0.3), 10]], ["frameQ", [f(score=1), 10]]])
        self.assertAlmostEqual(out[0], 1.0)
        self.assertLess(out[1], 0.6)
        self.assertGreater(out[2], out[4])
        self.assertLess(out[3], 0.85)
        self.assertGreater(out[4], 0.4, "점수 차이를 눌러 폄 (얼굴 배율 큰 장면만 몰리지 않게)")

    def test_same_person_closeups_group(self):
        frames = [{"t": 1, "hash": "ffff0000ffff0000", "kind": "close"}, {"t": 2, "hash": "ffff0000ffff00ff", "kind": "close"}, {"t": 3, "hash": "0000ffff0000ffff", "kind": "mid"},
                  {"t": 4, "hash": "ffff0000ff00ff0f", "kind": "close"}]
        g = self.run_js([["sceneGroups", [frames]]])[0]
        self.assertEqual(g["1"], g["2"])
        self.assertEqual(g["1"], g["4"], "같은 사람 인터뷰 (지문 20 안쪽)")
        self.assertNotEqual(g["1"], g["3"])

    def test_frames_keep_usable_cut_scenes(self):
        # 다른 썸네일이 통째로 든 영상: 클로드가 모든 장면을 1~3점으로 줌 · 글자 박힌 장면은 누끼가 있어야 템플릿이 씀
        # (판정 9회차: 누끼 있는 장면이 7장 밖으로 밀려 6개가 모두 같은 장면)
        p = [[0.3, 0.2, 0.3, 0.7, 0.9]]
        fr = lambda t, h, ai, **k: dict({"t": t, "hash": h, "ai": ai, "score": 10, "text": 0.4, "blur": 0.1, "kind": "mid", "persons": p, "main": 0, "flags": []}, **k)  # noqa: E731
        frames = [fr(1, "0000000000000000", 3), fr(2, "ffffffffffffffff", 3), fr(3, "00000000ffffffff", 1), fr(4, "ffffffff00000000", 2, text=0.01)]
        cuts = {"3": {"src": "c.png", "q": {"ok": True, "q": 0.9}}, "1": {"src": "d.png", "q": {"ok": False, "q": 0.3}}}
        out = self.run_js([["setAI", [{"frames": frames, "cuts": cuts}]], ["usableFrame", [frames[0]]], ["usableFrame", [frames[2]]], ["usableFrame", [frames[3]]],
                           ["aiFrames", [2]]])
        self.assertEqual(out[1:4], [False, True, True], "품질을 못 넘은 누끼는 없는 것과 같음 · 글자 적은 장면은 누끼 없어도 씀")
        self.assertEqual(sorted(f["t"] for f in out[4]), [3, 4], "클로드 점수가 낮아도 쓸 수 있는 장면이 먼저")

    def test_frames_drop_claude_irrelevant(self):
        # 인터뷰 영상: 클로드가 앵커·잡지 장면에 1~2점 → 좋은 장면이 3장 넘으면 빼서 다양성 채우기로 끌려오지 않게 (판정 9회차)
        p = [[0.3, 0.2, 0.3, 0.7, 0.9]]
        fr = lambda t, h, ai: {"t": t, "hash": h, "ai": ai, "score": 10, "text": 0, "blur": 0.1, "kind": "mid", "persons": p, "main": 0, "flags": []}  # noqa: E731
        frames = [fr(1, "0000000000000000", 7), fr(2, "ffffffffffffffff", 6), fr(3, "00000000ffffffff", 6), fr(4, "ffffffff00000000", 1), fr(5, "0f0f0f0f0f0f0f0f", 2)]
        out = self.run_js([["setAI", [{"frames": frames, "cuts": {}}]], ["aiFrames", [5]], ["setAI", [{"frames": frames[2:]}]], ["aiFrames", [5]]])
        self.assertEqual(sorted(f["t"] for f in out[1]), [1, 2, 3])
        self.assertEqual(len(out[3]), 3, "좋은 장면이 3장보다 적으면 빼지 않음")

    def test_head_box_and_visible_text(self):
        f = {"main": 0, "persons": [[0.4, 0.2, 0.2, 0.6, 0.9]], "kind": "mid"}
        hb = self.run_js([["headBox", [f]], ["headBox", [dict(f, persons=[[0.4, 0.0, 0.2, 0.9, 0.9]])]]])
        self.assertAlmostEqual(hb[0][1], 0.2)
        self.assertLess(hb[0][3], 0.13)
        self.assertIsNone(hb[1], "원본에서 이미 머리가 잘렸으면 지킬 머리 없음")
        # 얼굴이 여럿이면 주인공 상자 안 얼굴 (가장 큰 얼굴이 옆 사람이어도) — 판정: 다른 사람에 초점이 가서 누끼 주인공이 화면 밖
        two = dict(f, faces=[{"box": [0.05, 0.1, 0.12, 0.16]}, {"box": [0.45, 0.22, 0.08, 0.1]}])
        mf = self.run_js([["mainFace", [two]], ["mainFace", [dict(two, main=-1)]]])
        self.assertEqual(mf[0], [0.45, 0.22, 0.08, 0.1])
        self.assertEqual(mf[1], [0.05, 0.1, 0.12, 0.16], "주인공이 없으면 가장 큰 얼굴")
        layer = {"x": 0, "y": 0, "w": 1280, "h": 720, "fit": "cover", "fx": 0.5, "fy": 0.5}
        frame = {"tboxes": [[0.0, 0.0, 0.5, 0.2]]}
        vt = self.run_js([["visibleText", [layer, frame, 16 / 9, 1280, 720]], ["visibleText", [dict(layer, x=-1280, w=2560, h=1440, y=-720), frame, 16 / 9, 1280, 720]]])
        self.assertAlmostEqual(vt[0], 0.1, places=3)
        self.assertLess(vt[1], 0.001, "확대해서 글자를 화면 밖으로 밀면 0")


if __name__ == "__main__":
    unittest.main()
