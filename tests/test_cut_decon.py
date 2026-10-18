"""누끼 가장자리 색 정화 테스트 (D-490) — 저장소 폴더에서 python3 -m unittest tests.test_cut_decon

- thumb.decontaminate: 반투명 가장자리 RGB 를 가까운 안쪽(불투명) 색으로 · 초록 조끼는 초록 그대로 · 알파 0·불투명·먼 가장자리는 그대로
- remove_bg(손 누끼)·cut_auto(자동 누끼) 모두 색 정화한 RGB 에 같은 알파 · 자동 누끼 판(CUT_VER) 4 → 예전 판 파일을 그대로 쓰지 않음
누끼 모델은 쓰지 않음 (_bg_mask 를 합성 마스크로 바꿈)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thumb  # noqa: E402

GRASS, RED, VEST = (40, 160, 50), (200, 30, 30), (60, 200, 70)


def scene(w=320, h=240, inner=RED, blur=4):
    """잔디 위 빨간 사람(타원) — 마스크 가장자리는 반투명이고 그 RGB 는 사람 색과 잔디가 알파만큼 섞임 (실제 카메라처럼)."""
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).ellipse((90, 30, 230, 220), fill=255)
    m = m.filter(ImageFilter.GaussianBlur(blur))
    a = np.asarray(m, np.float32)[..., None] / 255
    rgb = np.asarray(inner, np.float32) * a + np.asarray(GRASS, np.float32) * (1 - a)
    return Image.fromarray(np.rint(rgb).astype(np.uint8), "RGB"), m


def green_bias(arr):
    arr = arr.astype(np.int32)
    return arr[..., 1] - np.maximum(arr[..., 0], arr[..., 2])


class DecontaminateTests(unittest.TestCase):
    def test_edge_takes_inner_colour(self):
        img, m = scene()
        a = np.asarray(m, np.float32) / 255
        edge = (a > 0) & (a < thumb.DECON_SOLID)
        before = green_bias(np.asarray(img))
        self.assertGreater((before[edge & (a < 0.4)] > 10).mean(), 0.9, "예전: 알파 0.4 아래 가장자리는 잔디 초록 쪽")
        out = np.asarray(thumb.decontaminate(img, m)).astype(np.int32)
        self.assertEqual((green_bias(out)[edge] > 10).mean(), 0.0, "지금: 가장자리에 초록 치우침 없음")
        self.assertLessEqual(np.abs(out[edge] - np.array(RED)).max(), 6, "가장자리는 안쪽(빨강) 색")

    def test_keeps_solid_transparent_and_alpha(self):
        img, m = scene()
        a = np.asarray(m)
        out = thumb.decontaminate(img, m)
        o, i = np.asarray(out), np.asarray(img)
        self.assertTrue((o[a == 255] == i[a == 255]).all(), "불투명한 곳은 그대로")
        self.assertTrue((o[a == 0] == i[a == 0]).all(), "알파 0 인 곳은 그대로 (되살리기 브러시는 원본에서 그림)")
        self.assertEqual(out.size, img.size)
        self.assertEqual(out.mode, "RGB")
        self.assertTrue((np.asarray(img) == i).all(), "입력 그림을 고치지 않음")

    def test_green_vest_stays_green(self):
        """초록 빼기(despill)가 아니라 안쪽 색 넓히기 — 초록 조끼 가장자리는 조끼 초록."""
        img, m = scene(inner=VEST)
        a = np.asarray(m, np.float32) / 255
        edge = (a > 0) & (a < thumb.DECON_SOLID)
        out = np.asarray(thumb.decontaminate(img, m)).astype(np.int32)
        self.assertLessEqual(np.abs(out[edge] - np.array(VEST)).max(), 6)

    def test_nothing_to_do(self):
        img, _ = scene()
        hard = Image.new("L", img.size, 0)
        ImageDraw.Draw(hard).rectangle((50, 50, 150, 150), fill=255)
        self.assertIs(thumb.decontaminate(img, hard), img, "반투명 가장자리가 없으면 그대로")
        haze = Image.new("L", img.size, 100)
        self.assertIs(thumb.decontaminate(img, haze), img, "불투명한 곳이 하나도 없으면 그대로 (가져올 색이 없음)")

    def test_far_haze_untouched(self):
        """안쪽 화소에서 창(63) 밖으로 먼 옅은 번짐은 그대로 (먼 곳의 색을 끌고 오지 않음)."""
        w, h = 400, 120
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rectangle((0, 0, 40, h), fill=255)
        ImageDraw.Draw(m).rectangle((300, 0, 330, h), fill=60)  # 사람에서 260화소 떨어진 옅은 번짐
        img = Image.new("RGB", (w, h), GRASS)
        ImageDraw.Draw(img).rectangle((0, 0, 40, h), fill=RED)
        out = np.asarray(thumb.decontaminate(img, m))
        self.assertTrue((out[:, 300:331] == np.array(GRASS)).all())

    def test_nearest_side_wins(self):
        """두 색 사이 가장자리는 가까운 쪽 색 (가장 작은 창부터)."""
        w, h = 200, 60
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rectangle((0, 0, 99, h), fill=255)
        ImageDraw.Draw(m).rectangle((100, 0, 101, h), fill=128)
        ImageDraw.Draw(m).rectangle((140, 0, 199, h), fill=255)
        img = Image.new("RGB", (w, h), GRASS)
        ImageDraw.Draw(img).rectangle((0, 0, 99, h), fill=RED)
        ImageDraw.Draw(img).rectangle((140, 0, 199, h), fill=(20, 20, 200))
        out = np.asarray(thumb.decontaminate(img, m))
        self.assertTrue((out[5:-5, 100:102] == np.array(RED)).all())

    def test_large_cut_colour_exact(self):
        """큰 누끼(1920×1080 거의 전부)도 합이 넘치거나 어긋나지 않음 (정수 적분 영상)."""
        w, h = 1920, 1080
        m = Image.new("L", (w, h), 255)
        ImageDraw.Draw(m).rectangle((1900, 0, w, h), fill=0)
        m = m.filter(ImageFilter.GaussianBlur(3))
        img = Image.new("RGB", (w, h), (251, 252, 253))
        ImageDraw.Draw(img).rectangle((1895, 0, w, h), fill=GRASS)
        a = np.asarray(m, np.float32) / 255
        edge = (a > 0.02) & (a < thumb.DECON_SOLID)
        out = np.asarray(thumb.decontaminate(img, m)).astype(np.int32)
        self.assertLessEqual(np.abs(out[edge] - np.array([251, 252, 253])).max(), 1)


class CutFilesTests(unittest.TestCase):
    """손 누끼(remove_bg)·자동 누끼(cut_auto) 파일이 색 정화한 RGB + 모델 알파."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.img, self.m = scene()
        for p in (mock.patch.object(thumb, "ASSETS", self.tmp), mock.patch.object(thumb, "_bg_mask", side_effect=lambda im, kind: self.m.resize(im.size)),
                  mock.patch.object(thumb.core, "set_progress")):
            p.start()
            self.addCleanup(p.stop)

    def check_png(self, p):
        o = np.asarray(Image.open(p).convert("RGBA"))
        a = o[..., 3].astype(np.float32) / 255
        edge = (a > 0.05) & (a < 0.95)
        self.assertTrue(edge.any())
        self.assertEqual((green_bias(o[..., :3])[edge] > 10).mean(), 0.0, "가장자리에 잔디 초록 없음")
        return o

    def test_remove_bg(self):
        src = self.tmp / "장면.png"
        self.img.save(src)
        p = thumb.remove_bg(src, "fast")
        o = self.check_png(p)
        self.assertTrue((o[..., 3] == np.asarray(self.m)).all(), "알파는 모델 마스크 그대로")

    def test_cut_auto(self):
        src = self.tmp / "장면.jpg"
        self.img.save(src, quality=98)
        with mock.patch.object(thumb, "grab", return_value=src), mock.patch.object(thumb, "_cut_path", return_value=self.tmp / "cut_auto_x.png"):
            p, q = thumb.cut_auto("영상.mp4", 3.0, None, "fast")
        self.check_png(p)
        self.assertEqual(json.loads((self.tmp / "cut_auto_x.json").read_text(encoding="utf-8")), q, "품질 기억은 예전처럼")

    def test_cut_version_bumped(self):
        """예전 판(3)으로 딴 자동 누끼 파일은 다른 이름 → 다음 분석이 색 정화로 다시 땀 (예전 파일은 지우지 않아 예전 디자인은 그대로)."""
        self.assertGreaterEqual(thumb.CUT_VER, 4)
        with mock.patch.object(thumb, "CUT_VER", 3):
            old = thumb.cut_file("영상", 3.0, [0.1, 0.2, 0.3, 0.4])
        self.assertNotEqual(old, thumb.cut_file("영상", 3.0, [0.1, 0.2, 0.3, 0.4]))


if __name__ == "__main__":
    unittest.main()
