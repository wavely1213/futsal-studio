"""썸네일 퀄리티(쪼살급) 기능 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_pro

- 함께 배포하는 글꼴·스티커: 라이선스 파일, 스티커 목록, /fonts·/stickers 주소 (폴더 밖 거절)
- 사람·공 찾기(detect.py): 상자 풀기·겹친 상자 정리(NMS)·레터박스 좌표 되돌리기 · 모델 없으면 None
- 자동 보정 숫자(thumb.auto_grade) · 흔들림·장면 지문 · 장면 종류 · 주인공 고르기
- 브랜드 키트 읽기·쓰기 검사 · /api/thumb/brand·analyze·ocr·cut 이름 검사(I-021)
인터넷은 쓰지 않음(내려받기는 모두 막음). 모델이 필요한 확인은 모델 파일이 있을 때만.
풋살장 사진(fixtures/futsal_court_pd.jpg · futsal_coach_pd.jpg): Wikimedia Commons 'Código Futsal - Cuartos de final de la Liga FUTVE Futsal 2.webm'
(Venezolana de Televisión, 퍼블릭 도메인)의 한 장면을 480px 로 줄인 것."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import detect  # noqa: E402
import thumb  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"
HAS_NODE = shutil.which("node") is not None


def _det_model_ok():
    return all(importlib.util.find_spec(m) for m in ("numpy", "onnxruntime")) and detect.ready()


class AssetTests(unittest.TestCase):
    """저장소에 넣은 글꼴·스티커는 라이선스 원문과 함께 있어야 함 (DEPENDENCY_POLICY 4번)."""

    def test_every_font_has_license_file(self):
        fonts = sorted(p.name for p in (ROOT / "fonts").iterdir() if p.suffix.lower() in (".ttf", ".otf"))
        self.assertIn("Jua-Regular.ttf", fonts)
        self.assertIn("Dokdo-Regular.ttf", fonts)
        lic = {p.name for p in (ROOT / "fonts").iterdir() if p.suffix == ".txt"}
        need = {"BlackHanSans-Regular.ttf": "OFL-BlackHanSans.txt", "DoHyeon-Regular.ttf": "OFL-DoHyeon.txt", "Jua-Regular.ttf": "OFL-Jua.txt",
                "Dokdo-Regular.ttf": "OFL-Dokdo.txt", "Pretendard-Black.otf": "LICENSE.txt", "Pretendard-Bold.otf": "LICENSE.txt"}
        for f in fonts:
            self.assertIn(f, need, f"라이선스를 모르는 글꼴: {f}")
            self.assertIn(need[f], lic)
            self.assertIn("SIL OPEN FONT LICENSE Version 1.1", (ROOT / "fonts" / need[f]).read_text(encoding="utf-8"))

    def test_font_list_in_editor_matches_files(self):
        """편집기 글꼴 목록(FONT_FILES)의 파일이 모두 fonts/ 에 있음."""
        import re
        src = (ROOT / "thumb_src" / "parts" / "p1_core.js").read_text(encoding="utf-8")
        files = re.findall(r'"([A-Za-z0-9-]+\.(?:ttf|otf))"', src[src.index("FONT_FILES"):src.index("const FONTS")])
        self.assertGreaterEqual(len(files), 6)
        for f in files:
            self.assertTrue((ROOT / "fonts" / f).is_file(), f)

    def test_stickers_index_and_license(self):
        d = json.loads((ROOT / "stickers" / "index.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(d), 30)
        for x in d:
            self.assertTrue((ROOT / "stickers" / x["file"]).is_file(), x["file"])
            self.assertTrue(x["label"] and isinstance(x["tags"], list))
        self.assertIn("MIT License", (ROOT / "stickers" / "LICENSE").read_text(encoding="utf-8"))
        self.assertIn("fluentui-emoji", (ROOT / "stickers" / "NOTICE.md").read_text(encoding="utf-8"))
        total = sum(p.stat().st_size for p in (ROOT / "stickers").glob("*.png"))
        self.assertLess(total, 1.5e6, "스티커 묶음이 너무 커요 (배포 크기)")


class DetectTests(unittest.TestCase):
    """사람·공 찾기: 모델 출력 풀기·겹친 상자 정리·레터박스 (모델 없이) + 모델이 있으면 실제 사진."""

    def test_decode_cell_to_box(self):
        import numpy as np
        out = np.zeros((3549, 85), np.float32)
        # stride 8 칸 (x=10, y=20) 에 중심 +0.5칸, 크기 exp(0)*8 = 8px 짜리 사람
        i = 20 * 52 + 10
        out[i, :4] = [0.5, 0.5, 0.0, 0.0]
        out[i, 4] = 0.9
        out[i, 5 + detect.PERSON] = 1.0
        boxes, sc = detect.decode(out)
        self.assertEqual(boxes.shape, (3549, 4))
        np.testing.assert_allclose(boxes[i], [84 - 4, 164 - 4, 84 + 4, 164 + 4], atol=1e-4)
        self.assertAlmostEqual(float(sc[i, detect.PERSON]), 0.9, places=5)
        # stride 32 칸의 첫 칸 (3549 - 169 번째)
        j = 52 * 52 + 26 * 26
        out[j, :4] = [0, 0, np.log(2), np.log(3)]
        b2, _ = detect.decode(out)
        np.testing.assert_allclose(b2[j], [0 - 32, 0 - 48, 32, 48], atol=1e-3)

    def test_nms_keeps_best_and_far(self):
        import numpy as np
        b = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], np.float32)
        s = np.array([0.5, 0.9, 0.7], np.float32)
        self.assertEqual(detect.nms(b, s), [1, 2])

    def test_letterbox_top_left_and_ratio(self):
        from PIL import Image
        x, r = detect.letterbox(Image.new("RGB", (832, 416), (255, 0, 0)))
        self.assertEqual(x.shape, (1, 3, 416, 416))
        self.assertAlmostEqual(r, 0.5)
        self.assertEqual(float(x[0, 2, 0, 0]), 255.0, "BGR 순서: 빨강은 마지막 채널")
        self.assertEqual(float(x[0, 0, 300, 0]), 114.0, "아래 남는 곳은 114")

    def test_none_without_model(self):
        from PIL import Image
        with mock.patch.dict(detect._SESS, clear=True):
            self.assertIsNone(detect.people(Image.new("RGB", (64, 36))))

    def test_offline_gives_up_quietly(self):
        tmp = Path(tempfile.mkdtemp(prefix="모델 없음 "))
        try:
            with mock.patch.object(thumb, "MODELS", tmp), mock.patch.dict(detect._SESS, clear=True), mock.patch.dict(detect._FAIL, {"t": 0.0}), \
                    mock.patch("updater.download", side_effect=OSError("인터넷 없음")) as dl:
                self.assertFalse(detect.ensure())
                self.assertTrue(dl.called)
                self.assertTrue((tmp / detect._MARK).is_file(), "10분 동안 다시 기다리지 않게 표시")
                n = dl.call_count
                detect._FAIL["t"] = 0.0
                self.assertFalse(detect.ensure())
                self.assertEqual(dl.call_count, n, "앱을 다시 켜도 바로 포기")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(_det_model_ok(), "선수 찾기 모델이 없어요 (~/.futsal-studio/models/yolox_nano.onnx)")
    def test_finds_person_in_photo(self):
        self.assertTrue(detect.ensure())
        r = detect.people(FIX / "smile_closeup.jpg")
        self.assertGreaterEqual(len(r["persons"]), 1, r)
        x, y, w, h, p = r["persons"][0]
        self.assertTrue(0 <= x <= 1 and 0 <= y <= 1 and w > 0.2 and h > 0.5 and p >= detect.P_THR, r)


def _img(color, size=(320, 180), noise=12, seed=1):
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(seed)
    a = np.clip(np.array(color, np.float32) + rng.normal(0, noise, (size[1], size[0], 3)), 0, 255).astype("uint8")
    return Image.fromarray(a)


GRADE_JS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const pick = n => { const a = src.indexOf('function ' + n + '('); let d = 0, i = src.indexOf('{', a); for (; i < src.length; i++) { if (src[i] === '{') d++; if (src[i] === '}' && --d === 0) break; } return src.slice(a, i + 1); };
eval('var clamp = (v, a, b) => Math.min(b, Math.max(a, v));' + pick('boxBlur') + pick('applyGrade'));
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const px = new Uint8ClampedArray(inp.px); applyGrade(px, inp.w, inp.h, inp.g);
process.stdout.write(JSON.stringify(Array.from(px)));
"""


class GradeTests(unittest.TestCase):
    """자동 보정 숫자(thumb.auto_grade)와 화면 보정(applyGrade, node 로 실행)이 짝으로 맞는지 — 밝기 0.38~0.62 · 채도 +5~35% · 잘림 ≤2%."""

    def stats(self, a):
        import numpy as np
        a = np.asarray(a, np.float32).reshape(-1, 4)[:, :3] if a.ndim == 1 else np.asarray(a, np.float32).reshape(-1, 3)
        lum = a @ np.array([0.299, 0.587, 0.114], np.float32) / 255
        mx, mn = a.max(1), a.min(1)
        return float(lum.mean()), float(((mx - mn) / np.maximum(mx, 1)).mean()), float(((a <= 1) | (a >= 254)).all(1).mean())

    def run_js(self, im, g):
        import numpy as np
        rgba = np.asarray(im.convert("RGBA"), np.uint8)
        h, w = rgba.shape[:2]
        r = subprocess.run(["node", "-e", GRADE_JS, str(ROOT / "thumb_src" / "parts" / "p1_core.js")], input=json.dumps({"px": rgba.flatten().tolist(), "w": w, "h": h, "g": g}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return np.array(json.loads(r.stdout), np.float32)

    def test_numbers_are_bounded(self):
        for col in ((40, 45, 60), (200, 205, 210), (90, 140, 70)):
            g = thumb.auto_grade(_img(col))
            self.assertTrue(all(0 <= v <= thumb.MAX_SHIFT for v in g["lo"]) and all(255 - thumb.MAX_SHIFT <= v <= 255 for v in g["hi"]), g)
            self.assertTrue(thumb.GAMMA_RANGE[0] <= g["gamma"] <= thumb.GAMMA_RANGE[1] and -40 <= g["vib"] <= 80 and -20 <= g["temp"] <= 30, g)
        self.assertGreater(thumb.auto_grade(_img((40, 45, 60)))["gamma"], 1.0, "어두우면 밝게")
        self.assertLess(thumb.auto_grade(_img((215, 215, 215), noise=20))["gamma"], 1.0, "밝으면 어둡게")
        self.assertEqual(thumb.auto_grade(_img((120, 120, 120)), close=True)["clarity"], 20)

    def test_blue_fluorescent_gets_warmer(self):
        g = thumb.auto_grade(_img((110, 125, 150), noise=8))
        self.assertGreater(g["temp"], 0, g)

    @unittest.skipUnless(HAS_NODE, "node 가 없어요")
    def test_js_grade_meets_targets(self):
        """흐린 실내·어두운 저녁·밝은 낮 장면: 보정 뒤 평균 밝기 0.38~0.62 · 채도 +5~35% · 하이라이트·섀도 잘림 ≤ 2%."""
        import numpy as np
        from PIL import Image
        scenes = [Image.open(FIX / f).convert("RGB").resize((320, 180)) for f in ("futsal_court_pd.jpg", "futsal_coach_pd.jpg", "smile_closeup.jpg")]
        scenes += [Image.fromarray((np.asarray(x, np.float32) * k).astype("uint8")) for x, k in ((scenes[0], 0.5), (scenes[2], 0.45))]  # 어두운 실내·저녁
        scenes.append(_img((70, 80, 95), noise=25))
        for k, sc in enumerate(scenes):
            g = thumb.auto_grade(sc)
            before = self.stats(np.asarray(sc, np.float32))
            after = self.stats(self.run_js(sc, g))
            self.assertTrue(0.38 <= after[0] <= 0.62, (k, g, before, after))
            self.assertTrue(1.05 <= after[1] / max(1e-6, before[1]) <= 1.35, (k, g, before, after))
            self.assertLessEqual(after[2] - before[2], 0.02, (k, g, before, after))  # 보정이 새로 잘라 먹은 픽셀 (원본이 이미 하얗게 날아간 곳은 빼고)

    @unittest.skipUnless(HAS_NODE, "node 가 없어요")
    def test_identity_grade_keeps_pixels(self):
        import numpy as np
        im = _img((100, 140, 90))
        out = self.run_js(im, {"on": True, "amt": 1, "lo": [0, 0, 0], "hi": [255, 255, 255], "gamma": 1, "vib": 0, "clarity": 0, "temp": 0, "sharpen": 0})
        np.testing.assert_allclose(out.reshape(-1, 4)[:, :3], np.asarray(im, np.float32).reshape(-1, 3), atol=1)


class SceneTests(unittest.TestCase):
    """장면 정보: 지문·흔들림·액션 배율·주인공·종류."""

    def test_headless_frame(self):
        """다리·몸통만 보이는 장면(주인공 머리가 위로 잘림·얼굴 없음)은 감점 대상, 얼굴을 찾았거나 전신이면 아님."""
        legs = [[0.49, 0.0, 0.5, 0.74, 0.85], [0.38, 0.43, 0.13, 0.24, 0.76]]
        self.assertTrue(thumb.headless(legs, None, []))
        self.assertFalse(thumb.headless(legs, None, [{"box": [0.5, 0.1, 0.1, 0.2]}]))
        self.assertFalse(thumb.headless([[0.4, 0.2, 0.1, 0.5, 0.9]], None, []))
        self.assertFalse(thumb.headless([], None, None))
        self.assertFalse(thumb.headless([[0.2, 0.0, 0.6, 1.0, 0.9]], None, []), "화면을 꽉 채운 인터뷰는 아님")

    def test_burned_text_penalty(self):
        """영상에 이미 박힌 큰 글자(다른 썸네일·타이틀 화면)는 감점 — 자막 띠 안·작은 글자는 셈하지 않음, 모델이 없으면 그대로."""
        import avmodels
        lines = [{"box": [0.1, 0.1, 0.5, 0.2], "h": 0.1, "text": "더 힘들까", "conf": 0.9},     # 큰 제목 글자: 0.04
                 {"box": [0.1, 0.85, 0.9, 0.95], "h": 0.1, "text": "자막", "conf": 0.9},       # 아래 자막 띠 안: 뺌
                 {"box": [0.5, 0.5, 0.55, 0.53], "h": 0.03, "text": "10", "conf": 0.9}]       # 작은 등번호: 뺌
        with mock.patch.object(avmodels, "available", return_value=True), mock.patch.object(avmodels, "ocr", return_value=lines):
            a = thumb.text_area(_img((90, 140, 70)), "bottom", 0.8)
        self.assertAlmostEqual(a, 0.04, places=3)
        self.assertAlmostEqual(thumb.text_penalty(a), 1 - thumb.TEXT_K * 0.04, places=3)
        self.assertEqual(thumb.text_penalty(0.5), thumb.TEXT_FLOOR, "아무리 많아도 바닥 값까지만")
        self.assertEqual(thumb.text_penalty(None), 1.0)
        self.assertEqual(thumb.text_penalty(0), 1.0)
        with mock.patch.object(avmodels, "available", return_value=False), mock.patch.object(avmodels, "ready", return_value=False):
            self.assertIsNone(thumb.text_area(_img((90, 140, 70))), "글자 읽기 모델이 없으면 내려받지 않고 None")

    def test_dhash_same_and_different(self):
        a, b = _img((90, 140, 70), seed=1), _img((90, 140, 70), seed=1)
        self.assertEqual(thumb.dhash(a), thumb.dhash(b))
        self.assertEqual(len(thumb.dhash(a)), 16)
        from PIL import ImageDraw
        c = a.copy()
        ImageDraw.Draw(c).rectangle([0, 0, 160, 180], fill=(250, 250, 250))
        self.assertGreater(thumb.hamming(thumb.dhash(a), thumb.dhash(c)), thumb.SAME_HASH)
        self.assertEqual(thumb.hamming("zz", "00"), 64)

    def test_motion_blur_scores_higher(self):
        import numpy as np
        from PIL import Image, ImageFilter
        im = Image.open(FIX / "smile_closeup.jpg").convert("RGB")
        g = np.asarray(im.convert("L"), np.float32)
        k = 15  # 가로로 15px 끌린 모션 블러
        blurred = np.mean([np.roll(g, s, axis=1) for s in range(-k // 2, k // 2 + 1)], axis=0)
        sharp_b, blur_b = thumb._blur_of(g), thumb._blur_of(blurred)
        self.assertLess(sharp_b, 0.35)
        self.assertGreater(blur_b, sharp_b + 0.2)
        soft = np.asarray(im.convert("L").filter(ImageFilter.GaussianBlur(5)), np.float32)
        self.assertGreater(thumb._blur_of(soft), 0.6)
        self.assertEqual(thumb.blur_penalty(0.7), 0.5)
        self.assertEqual(thumb.blur_penalty(0.2), 1.0)

    def test_action_and_main(self):
        two = [[0.1, 0.3, 0.1, 0.35, 0.9], [0.6, 0.35, 0.1, 0.3, 0.9]]
        ball = [0.62, 0.62, 0.02, 0.03, 0.5]
        self.assertEqual(thumb.action_score(None, None), 1.0, "모델이 없으면 그대로")
        self.assertEqual(thumb.action_score([], None), 0.85)
        self.assertGreater(thumb.action_score(two, ball), thumb.action_score(two, None) * 1.25)
        self.assertEqual(thumb.main_person(two, ball), 1, "공과 가까운 선수")
        self.assertEqual(thumb.main_person(two, None), 0, "공이 없으면 가장 큰 선수")
        self.assertEqual(thumb.main_person([], ball), -1)
        self.assertEqual(thumb.main_person([[0.3, 0.05, 0.6, 0.9, 0.9], [0.6, 0.5, 0.05, 0.2, 0.8]], ball), 0, "인터뷰처럼 크게 나온 사람이 주인공")
        tangle = [[0.4, 0.3, 0.12, 0.4, 0.9], [0.45, 0.32, 0.12, 0.38, 0.9]]
        self.assertGreater(thumb.action_score(tangle, None), thumb.action_score([tangle[0], [0.8, 0.3, 0.1, 0.4, 0.9]], None))

    def test_burned_in_band(self):
        """방송 띠(아래쪽 회색 띠 + 작은 글자)를 찾고 띠가 시작하는 높이를 알려 줌 · 띠 없는 장면은 None."""
        from PIL import Image, ImageDraw
        tmp = Path(tempfile.mkdtemp(prefix="띠 "))
        try:
            im = Image.open(FIX / "futsal_court_pd.jpg").convert("RGB").resize((640, 360))
            clean = tmp / "깨끗.jpg"
            im.save(clean)
            self.assertEqual(thumb._band(clean), (None, None))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 322, 640, 360], fill=(235, 235, 235))
            for x in range(10, 630, 14):
                d.text((x, 332), "가", fill=(20, 20, 20))
            banded = tmp / "띠.jpg"
            im.save(banded, quality=95)
            pos, y = thumb._band(banded)
            self.assertEqual(pos, "bottom")
            self.assertTrue(0.85 <= y <= 0.92, y)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_kind(self):
        self.assertEqual(thumb.scene_kind(0.3, []), "close")
        self.assertEqual(thumb.scene_kind(0, [[0.3, 0.1, 0.3, 0.8, 0.9]]), "mid")
        self.assertEqual(thumb.scene_kind(0, [[0.1, 0.4, 0.05, 0.2, 0.9], [0.5, 0.4, 0.05, 0.22, 0.9]]), "wide")
        self.assertEqual(thumb.scene_kind(0, []), "scene")


class BrandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="브랜드 키트 "))
        self.p = [mock.patch.object(thumb, "THUMBS", self.tmp), mock.patch.object(thumb, "ASSETS", self.tmp / "assets")]
        for p in self.p:
            p.start()
        (self.tmp / "assets").mkdir()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_and_roundtrip(self):
        b = thumb.load_brand()
        self.assertEqual(b, thumb.BRAND_DEFAULT)
        (self.tmp / "assets" / "img_1.png").write_bytes(b"\x89PNG")
        b2 = thumb.save_brand({"colors": {"hl": "#00ff00"}, "logo": "/asset/img_1.png", "logoPos": "tl", "series": "쌈풋 강좌", "seriesOn": True})
        self.assertEqual(b2["colors"]["hl"], "#00FF00")
        self.assertEqual(b2["colors"]["accent"], thumb.BRAND_DEFAULT["colors"]["accent"], "안 보낸 색은 기본값")
        self.assertEqual(thumb.load_brand(), b2)
        thumb.save_brand({"series": "두 번째"})
        self.assertTrue((self.tmp / "brand.json.bak").is_file())
        (self.tmp / "brand.json").write_text("{깨짐", encoding="utf-8")
        self.assertEqual(thumb.load_brand()["series"], "쌈풋 강좌", "깨지면 바로 전 저장본")

    def test_rejects_bad_values(self):
        for bad in ({"colors": {"hl": "red"}}, {"colors": {"zz": "#FFFFFF"}}, {"logo": "/asset/../app.py"}, {"logo": "C:\\x.png"},
                    {"logo": "/asset/없음.png"}, {"logoPos": "middle"}, {"font": "Comic Sans"}, {"series": "가" * 21}, {"handle": "<script>"}, "문자열"):
            with self.assertRaises(ValueError, msg=bad):
                thumb.check_brand(bad)


class ServerBase(unittest.TestCase):
    """임시 작업 폴더(한글·띄어쓰기) + 실제 HTTP 서버."""

    def setUp(self):
        import app
        self.app = app
        self.tmp = Path(tempfile.mkdtemp(prefix="풋살 썸네일 "))
        self.work = self.tmp / "작업 폴더"
        for d in ("videos", "analysis", "out", "thumbnails/assets"):
            (self.work / d).mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, "WORK", self.work), mock.patch.object(core, "VIDEOS", self.work / "videos"),
                        mock.patch.object(core, "ANALYSIS", self.work / "analysis"), mock.patch.object(core, "OUT", self.work / "out"),
                        mock.patch.object(thumb, "THUMBS", self.work / "thumbnails"), mock.patch.object(thumb, "ASSETS", self.work / "thumbnails" / "assets"),
                        mock.patch.object(thumb, "MODELS", self.tmp / "models"), mock.patch("updater.download", side_effect=OSError("인터넷 막음"))]
        for p in self.patches:
            p.start()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.work / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2 + self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def raw(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()

    def call(self, path, body=None):
        code, _, data = self.raw(path, body)
        return code, json.loads(data or b"{}")


class StaticRouteTests(ServerBase):
    def test_fonts_content_type_by_extension(self):
        code, ctype, data = self.raw("/fonts/Jua-Regular.ttf")
        self.assertEqual((code, ctype), (200, "font/ttf"))
        self.assertGreater(len(data), 100000)
        self.assertEqual(self.raw("/fonts/Pretendard-Black.otf")[:2], (200, "font/otf"))

    def test_stickers_served_only_from_folder(self):
        code, ctype, data = self.raw("/stickers/fire.png")
        self.assertEqual((code, ctype), (200, "image/png"))
        self.assertEqual(data[:4], b"\x89PNG")
        self.assertEqual(self.raw("/stickers/index.json")[0], 200)
        for bad in ("/stickers/..%2Fapp.py", "/stickers/../app.py", "/stickers/NOTICE.md", "/stickers/%EC%97%86%EC%9D%8C.png", "/fonts/..%2Fapp.py"):
            self.assertEqual(self.raw(bad)[0], 404, bad)


class ThumbRouteTests(ServerBase):
    """/api/thumb/brand·analyze·copy·ocr·cut (이름 검사 I-021)."""

    def add_video(self, name="20261007_ABCDEFGHIJK_발바닥 드래그.mp4"):
        (self.work / "videos" / name).write_bytes(b"0")
        a = core.adir(name)
        a.mkdir(parents=True, exist_ok=True)
        (a / "transcript.json").write_text(json.dumps([{"start": 1, "end": 3, "text": "발바닥 드래그 이렇게 하세요"}], ensure_ascii=False), encoding="utf-8")
        return name

    def test_brand_get_post(self):
        code, j = self.call("/api/thumb/brand")
        self.assertEqual((code, j["brand"]["colors"]["hl"]), (200, "#FFE14D"))
        self.assertIn("Jua", j["fonts"])
        code, j = self.call("/api/thumb/brand", {"brand": {"colors": {"neon": "#00ffaa"}, "series": "풋사관 강좌"}})
        self.assertEqual((code, j["brand"]["colors"]["neon"]), (200, "#00FFAA"))
        code, j = self.call("/api/thumb/brand", {"brand": {"colors": {"neon": "파랑"}}})
        self.assertEqual(code, 400)
        self.assertIn("#RRGGBB", j["error"])

    def test_analyze_and_copy(self):
        self.assertEqual(self.call("/api/thumb/analyze", {"name": "..\\x.mp4"})[0], 400)
        self.assertEqual(self.call("/api/thumb/analyze", {"name": "없음.mp4"})[0], 404)
        name = self.add_video()
        with mock.patch.object(thumb, "analyze", return_value={"frames": [], "cuts": {}, "copy": {}}) as an, mock.patch.object(self.app, "start_job", side_effect=lambda n, fn: (fn(), True)[1]) as sj:
            code, j = self.call("/api/thumb/analyze", {"name": name})
        self.assertEqual((code, j["ok"], j["job"]), (200, True, True))
        an.assert_called_once()
        self.assertEqual(sj.call_args[0][0], "썸네일 분석")
        # 다 돼 있으면 작업 없이 바로
        with mock.patch.object(thumb, "cached_analysis", return_value={"frames": [{"t": 1}], "cuts": {}, "copy": {"items": []}}):
            code, j = self.call("/api/thumb/analyze", {"name": name})
        self.assertEqual((code, j["frames"]), (200, [{"t": 1}]))
        code, j = self.call("/api/thumb/copy", {"name": name})
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(j["items"]), 5)
        self.assertIn("발바닥 드래그", j["topics"])
        with mock.patch.object(self.app, "start_job", return_value=False):
            self.assertEqual(self.call("/api/thumb/copy", {"name": name, "ai": True})[0], 409)

    def test_cut_checks_names(self):
        """I-021: 장면 주소의 영상 이름도 파일 이름만 · 올린 그림은 그림 폴더 안만."""
        from urllib.parse import quote
        with mock.patch.object(self.app, "start_job", return_value=True) as sj:
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/frame?name=" + quote("..\\..\\x.mp4") + "&t=1"})[0], 400)
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/frame?name=" + quote("없는 영상.mp4") + "&t=1"})[0], 404)
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/frame?name=x.mp4&t=abc"})[0], 400)
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/asset/없음.png"})[0], 404)
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/asset/x.png", "kind": "../../evil"})[0], 404)
            sj.assert_not_called()
            name = self.add_video()
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/frame?name=" + quote(name) + "&t=1", "kind": "fast"})[0], 200)
            (thumb.ASSETS / "img_1.png").write_bytes(b"x")
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/asset/img_1.png", "kind": "zzz"})[0], 400)
            self.assertEqual(self.call("/api/thumb/cut", {"src": "/asset/img_1.png"})[0], 200)
            self.assertEqual(sj.call_count, 2)

    def test_ocr_limits_and_missing_model(self):
        self.assertEqual(self.call("/api/thumb/ocr", {"data": "data:image/png;base64," + "A" * (thumb.OCR_MAX + 1)})[0], 400)
        self.assertEqual(self.call("/api/thumb/ocr", {"data": "http://x"})[0], 400)
        with mock.patch.object(thumb, "read_text", return_value=None):
            code, j = self.call("/api/thumb/ocr", {"data": "data:image/png;base64,AAAA"})
        self.assertEqual((code, j["ok"]), (200, False))
        self.assertIn("모델", j["error"])
        with mock.patch.object(thumb, "read_text", return_value=[{"text": "무조건 봐", "conf": 0.9, "box": [0, 0, 1, 1]}]):
            code, j = self.call("/api/thumb/ocr", {"data": "data:image/png;base64,AAAA"})
        self.assertEqual(j["lines"][0]["text"], "무조건 봐")

    def test_ab_export_names_and_no_overwrite(self):
        import base64
        name = self.add_video()
        jpg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0 jpg").decode()
        code, j = self.call("/api/thumb/ab", {"name": name, "items": [jpg, jpg, jpg], "mobile": jpg})
        self.assertEqual(code, 200, j)
        stem = core.adir(name).name
        self.assertEqual(j["files"], [f"{stem}_썸네일_A.jpg", f"{stem}_썸네일_B.jpg", f"{stem}_썸네일_C.jpg", f"{stem}_모바일 비교.jpg"])
        code, j = self.call("/api/thumb/ab", {"name": name, "items": [jpg, jpg]})
        self.assertEqual(j["files"], [f"{stem}_썸네일_A (2).jpg", f"{stem}_썸네일_B (2).jpg"], "예전 묶음을 덮어쓰지 않음")
        for bad in ({"items": [jpg]}, {"items": [jpg, "http://x"]}, {"items": "x"}, {"items": [jpg] * 7}):
            self.assertEqual(self.call("/api/thumb/ab", dict(bad, name=name))[0], 400, bad)
        self.assertEqual(self.call("/api/thumb/ab", {"name": "..\\x.mp4", "items": [jpg, jpg]})[0], 400)

    def test_judge_route_and_parse(self):
        import thumbcopy
        with mock.patch.object(self.app, "start_job", return_value=True) as sj:
            self.assertEqual(self.call("/api/thumb/judge", {"small": "x", "full": "y"})[0], 400)
            code, j = self.call("/api/thumb/judge", {"small": "data:image/jpeg;base64,AA", "full": "data:image/jpeg;base64,AA"})
        self.assertEqual((code, j["job"]), (200, True))
        self.assertEqual(sj.call_args[0][0], "클로드에게 평가받기")
        r = thumbcopy.parse_judge('평가: {"readability": 8.6, "contrast": 12, "hierarchy": 7, "appeal": "6", "pro_level": 0, "fixes": ["a", "b", "c", "d"], "summary": "좋아요"}')
        self.assertEqual((r["readability"], r["contrast"], r["appeal"], r["pro_level"]), (9, 10, 6, 1))
        self.assertEqual(len(r["fixes"]), 3)
        with self.assertRaises(ValueError):
            thumbcopy.parse_judge('{"readability": 5}')


if __name__ == "__main__":
    unittest.main()
