"""풋살사관학교 스킨 쇼츠 (BR-320~BR-327 · D-520~D-537) — 저장소 폴더에서 python3 -m unittest tests.test_academy_skin

- 앱과 함께 싣는 스킨(skins/academy.png · git 추적 = 배포 목록 · 업데이트 경로 규칙 · 출처 글)
- 영상 창 찾기(academy.png → 417~1465 줄 · 영상이 덮을 곳은 찢어진 종이 홈까지 385~1495 · 기억 · 파일이 바뀌면 다시) · '스킨 바꾸기' 확인(9:16 · 투명한 창 30% 이상 · 크기 · 투명 그대로 ·
  내용 지문 이름 · 한글 폴더) · 거절(창 없음 · 비율 · 너무 작음 · 투명 없음 · 그림 아님) · 처음 스킨으로 · 이상한 이름
- 화면 구성 셈(zoom·vpos · 원본 비율마다(16:9 그대로 · 세로 1 · 4:3 · 21:9) 1080·720·4K 내보내기(위아래 4화소 더)와 미리보기 모두에서 덮을 곳을 덮음 · D-530) ·
  은평 것(win·띠 그림·로고·글꼴 고정)은 없음
- 화면 좌우 얼굴 따라가기(skin_follow · skin_reframe · rfk · 얼굴이 뛰면 실제 컷에서 · 한 칸만 뛴 얼굴 무시 · 얼굴 칸 = 그 시각 장면 · 확대 기준점 창 좌표 · crop x 시간 식 · 미리보기 = 서버 · BR-326 · D-537)
- 흰 상자 폭 = 실제 글자 폭(Pretendard Black 표 · PIL 로 잰 폭과 같음) + 양옆 14 · 크기 고르기는 넉넉한 어림 그대로 · 큰 제목 한 줄(skin_headline 80 이상 ·
  손으로 친 긴 글은 48 밑 · \\q2 · 실제 그림에서 흰 상자 안 · D-531) · 주제 줄 96·큰 제목보다 크지 않게 · 배운 강조 큰 글씨는 창 안 (D-534)
- 가편집 쇼츠(큰 제목·흰 상자·주제 줄·말 자막 값) · MSG 쇼츠(고정 글자 말고는 위·아래 띠에 글자 없음 · 말 자막 자리) ·
  말 자막은 소유자 쇼츠 모양(흰 Black 96 · 테두리 10 · 상자 없음 · 나타나기는 배운 그대로 · 실제 글자 폭으로 95% 한 줄 셈 · 긴 한 줄은 글자만 줄여 한 줄 ·
  MSG 글자 자리 정리도 이 자막을 피함 · D-529 · D-532 · D-533)
- 실제 내보내기(1080×1920): 위·아래 띠 = 스킨 화소 · 창 가운데 = 영상 · 찢어진 종이 홈(비치는 화소)에도 영상(검은 바탕 아님) · V2(화면 전체)·빈 곳에도 스킨 · 흰 상자·주황 큰 제목이 스킨 위 ·
  글자·도형만 투명 영상에는 스킨 없음 · 프리미어 '빠진 것' + 옆에 스킨 그림 (D-536) · 4K 필터 · yuva420p 한 번 (D-535)
- 기본·은평 스타일은 스킨 전 코드와 같은 결과 (tests/skin_same.py 지문 = tests/fixtures/academy_same.json)
- 편집실 템플릿 '풋살사관학교 스킨'(editor.html skinCard 를 node 로) = 서버 값 · 다른 템플릿·은평 스타일은 스킨을 지움 · 큰 제목 글·크기를 고치면 흰 상자도 (skinRefit) ·
  '새 쇼츠 편집본으로 추가'는 스킨 바탕이면 KFA 글꼴 찾기를 안 기다림 · 길(/api/style/skin) · 원격 분류"""
import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
import style  # noqa: E402
import updater  # noqa: E402
from test_shorts_topic import FIX, WorkMixin  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
W, H = editor.frame_size("shorts")
FF = shutil.which("ffmpeg")
SKIN = ROOT / "skins" / "academy.png"
VIDEO_RGB = (40, 110, 210)   # 시험 영상 색 (창 가운데)


def skin_png(size=(1080, 1920), top=0.25, bottom=0.75, mode="RGBA", fmt="PNG", color=(30, 80, 50)):
    """시험 스킨: 위·아래 불투명 띠(색) + 가운데 투명 창 [top, bottom) · 창 가장자리는 들쭉날쭉 반투명 (찢어진 종이 흉내)."""
    from PIL import Image
    w, h = size
    im = Image.new("RGBA", size, color + (255,))
    px = im.load()
    t, b = int(round(top * h)), int(round(bottom * h))
    for y in range(t, b):
        for x in range(w):
            px[x, y] = (0, 0, 0, 0)
    for x in range(0, w, 3):   # 가장자리 한두 줄은 반투명 (창 줄로 셈하지 않을 만큼만 · 가로 10% 안쪽 칸의 절반 밑)
        if t > 0:
            px[x, t - 1] = color + (128,)
    if mode != "RGBA":
        im = im.convert(mode)
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def data_url(raw, kind="png"):
    return f"data:image/{kind};base64," + base64.b64encode(raw).decode("ascii")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="스킨 [시험] "))
        self.s = mock.patch.object(style, "STYLES", self.tmp / "작업 폴더" / "styles")
        self.s.start()

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ShippedTest(unittest.TestCase):
    def test_file_ships_with_app(self):
        """기본 스킨은 저장소에 추적됨 → release.sh 배포 목록(tests/·thumb_src/ 빼고 추적 파일 전부)·업데이트 경로 규칙을 지남 · 영문 이름 · 출처 글."""
        from PIL import Image
        self.assertEqual(style.skin_ship_dir() / style.SKIN_SHIP, SKIN)
        with Image.open(SKIN) as im:
            self.assertEqual((im.size, im.mode), ((1080, 1920), "RGBA"))
        for rel in ("skins/academy.png", "skins/NOTICE.md"):
            self.assertEqual(updater._rel(rel), rel)
            self.assertTrue(rel.isascii())
        notice = (ROOT / "skins" / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("소유자", notice)
        self.assertIn("2026-10-19", notice)
        if (ROOT / ".git").exists() and shutil.which("git"):
            tracked = subprocess.run(["git", "ls-files", "skins"], cwd=ROOT, capture_output=True, text=True).stdout.split()
            if tracked:   # 커밋 전(작업 트리)에는 비어 있을 수 있음
                self.assertEqual(sorted(tracked), ["skins/NOTICE.md", "skins/academy.png"])
        rel = (ROOT / "release.sh").read_text(encoding="utf-8")
        self.assertIn('path.startswith(("tests/", "thumb_src/"))', rel)   # 빼는 목록은 이것뿐
        self.assertNotIn("skins", rel)


class WindowTest(Tmp):
    def test_academy_window(self):
        """소유자 스킨: 줄마다 가로 10~90% 칸의 절반 넘게 투명한 줄 → 417~1464 줄 (그다음 줄 1465)."""
        win = style.skin_window(SKIN)
        self.assertAlmostEqual(win["top"] * 1920, 417, delta=1)
        self.assertAlmostEqual(win["bottom"] * 1920, 1465, delta=1)
        self.assertEqual((win["w"], win["h"]), (1080, 1920))
        self.assertEqual(editor.skin_window("academy.png"), win)
        # 영상이 덮을 곳 (검토): 찢어진 종이 홈·그림자로 비치는(알파 < 255) 줄까지 — 385~1494 줄 (그다음 줄 1495) · 그 밖 줄은 다 불투명
        self.assertEqual((round(win["coverTop"] * 1920), round(win["coverBottom"] * 1920)), (385, 1495))
        from PIL import Image
        import numpy as np
        a = np.asarray(Image.open(SKIN).getchannel("A"))
        see = np.where((a[:, 108:972] < 255).any(axis=1))[0]
        self.assertEqual((see.min(), see.max() + 1), (385, 1495))
        self.assertGreater(int((a[401:417] < 128).sum()), 1000)                          # 창 위 홈: 거의 투명한 화소가 많음 (예전엔 검은 바탕이 비침)
        self.assertGreater(int((a[1465:1481] < 128).sum()), 1000)

    def test_window_rules_and_cache(self):
        from PIL import Image
        im = Image.open(io.BytesIO(skin_png((360, 640), 0.3, 0.6)))
        self.assertEqual(style.skin_window_of(im), (192, 384))
        self.assertEqual(style.skin_cover_of(im, (192, 384)), (191, 384))                  # 창 위 반투명 한 줄(가장자리)까지 덮음 · 아래는 바로 불투명
        soft = Image.new("RGBA", (360, 640), (9, 9, 9, 255))
        soft.paste(Image.new("RGBA", (360, 640 - 2 * 192), (0, 0, 0, 0)), (0, 192))
        for x in range(0, 360, 3):                                                         # 위 띠 전체에 반투명 무늬(줄마다 1/3 · 창 줄은 아님)가 있어도
            soft.paste(Image.new("RGBA", (1, 192), (9, 9, 9, 200)), (x, 0))                # 창 위 5%(32줄)까지만 덮음
        self.assertEqual(style.skin_window_of(soft), (192, 448))
        self.assertEqual(style.skin_cover_of(soft, (192, 448)), (192 - int(style.SKIN_COVER_MAX * 640), 448))
        self.assertIsNone(style.skin_window_of(im.convert("RGB")))                       # 투명 없음
        opaque = Image.new("RGBA", (360, 640), (1, 2, 3, 255))
        self.assertIsNone(style.skin_window_of(opaque))
        two = Image.open(io.BytesIO(skin_png((360, 640), 0.1, 0.2)))
        two.paste(Image.new("RGBA", (360, 200), (0, 0, 0, 0)), (0, 300))               # 창이 둘이면 가장 긴 것
        self.assertEqual(style.skin_window_of(two), (300, 500))
        side = Image.new("RGBA", (360, 640), (9, 9, 9, 255))
        side.paste(Image.new("RGBA", (30, 640), (0, 0, 0, 0)), (0, 0))                  # 가장자리(가로 10% 밖)만 투명하면 창이 아님
        self.assertIsNone(style.skin_window_of(side))
        p = self.tmp / "스킨 기억.png"
        p.write_bytes(skin_png((360, 640), 0.3, 0.6))
        calls = []
        real = Image.open
        with mock.patch("PIL.Image.open", lambda *a, **k: calls.append(1) or real(*a, **k)):
            a1 = style.skin_window(p)
            a2 = style.skin_window(p)
        self.assertEqual((a1, len(calls)), (a2, 1))                                       # 같은 파일(경로·고친 시각·크기)은 한 번만 읽음
        import os
        p.write_bytes(skin_png((360, 640), 0.2, 0.7))
        os.utime(p, ns=(1, 1))
        self.assertAlmostEqual(style.skin_window(p)["top"], 0.2, places=2)                # 바뀌면 다시
        self.assertIsNone(style.skin_window(self.tmp / "없음.png"))
        bad = self.tmp / "깨짐.png"
        bad.write_bytes(b"\x89PNG broken")
        self.assertIsNone(style.skin_window(bad))


class SettingTest(Tmp):
    def test_default_pick_reset(self):
        self.assertEqual(style.skin_state(), {"file": "", "ref": "academy.png"})
        self.assertEqual(style.skin_path("academy.png"), SKIN)
        raw = skin_png()
        c = style.save_skin(data_url(raw))
        self.assertRegex(c["file"], r"^skin_[0-9a-f]{12}\.png$")
        self.assertEqual(c["ref"], c["file"])
        p = style.skin_dir() / c["file"]
        self.assertTrue(p.is_file())
        self.assertIn("스킨", str(p))                                                      # 작업 폴더 (한글·띄어쓰기·대괄호 경로)
        from PIL import Image
        with Image.open(p) as im:
            self.assertEqual((im.size, im.mode), ((1080, 1920), "RGBA"))                   # 투명 그대로
        self.assertEqual(style.skin_path(c["file"]), p)
        self.assertEqual(editor.skin_layout()["skin"], c["file"])
        self.assertEqual(style.save_skin(raw)["file"], c["file"])                         # 같은 그림 = 같은 이름 (바이트로 줘도)
        self.assertEqual(style.reset_skin(), {"file": "", "ref": "academy.png"})
        self.assertTrue(p.is_file())                                                       # 바꾼 스킨 파일은 남김 (이미 만든 편집본이 가리킴)
        style.save_skin(raw)
        p.unlink()
        self.assertEqual(style.skin_state()["file"], "")                                  # 파일이 없어지면 실은 스킨
        big = style.save_skin(skin_png((2304, 4096)))
        with Image.open(style.skin_dir() / big["file"]) as im:
            self.assertEqual(im.size, (2304, 4096))
        with mock.patch.object(style, "CARD_PIC_SIDE", 1920):                             # 긴 변은 줄여서 저장
            small = style.save_skin(skin_png((2160, 3840)))
        with Image.open(style.skin_dir() / small["file"]) as im:
            self.assertEqual(im.size, (1080, 1920))
        ok = style.save_skin(skin_png((1080, 1890)))                                       # 9:16 에서 3% 안
        self.assertTrue(ok["file"])
        f = style._skin_file()
        for bad in ("{", "[1]", '{"file": "../app.png"}', '{"file": "skin_zzzzzzzzzzzz.png"}'):
            f.write_text(bad, encoding="utf-8")
            self.assertEqual(style.skin_state(), {"file": "", "ref": "academy.png"}, bad)

    def test_rejects(self):
        """거절은 쉬운 말 · 설정·폴더 그대로."""
        before = style.skin_state()
        cases = {
            "창 없음": skin_png(top=0.5, bottom=0.5),
            "창 작음": skin_png(top=0.4, bottom=0.6),
            "가로 그림": skin_png((1920, 1080)),
            "정사각형": skin_png((1080, 1080)),
            "너무 작음": skin_png((180, 320)),
            "투명 없음": skin_png(mode="RGB"),
            "JPG": skin_png(mode="RGB", fmt="JPEG"),
            "잘린 파일": skin_png()[:200],
            "그림 아님": b"GIF89a" + b"\0" * 40,
        }
        for why, raw in cases.items():
            with self.assertRaises(ValueError, msg=why) as cm:
                style.save_skin(data_url(raw))
            self.assertTrue(any(k in str(cm.exception) for k in ("9:16", "PNG", "작아요", "그림")), (why, str(cm.exception)))
        self.assertIn("가운데가 투명하게 비어 있는 9:16 그림(PNG)을 골라 주세요", str(self._err(skin_png(top=0.5, bottom=0.5))))
        self.assertIn("너무 작아요", str(self._err(skin_png((180, 320)))))
        for data in ("hello", "data:image/png;base64,QUJD", 3, None):
            with self.assertRaises(ValueError):
                style.save_skin(data)
        with mock.patch.object(style, "CARD_PIC_MAX", 100):
            with self.assertRaises(ValueError) as cm:
                style.save_skin(skin_png())
            self.assertIn("너무 커요", str(cm.exception))
        self.assertEqual(style.skin_state(), before)
        self.assertFalse(style.skin_dir().exists() and any(style.skin_dir().iterdir()))

    def _err(self, raw):
        try:
            style.save_skin(raw)
        except ValueError as e:
            return e
        return None

    def test_path_rejects_odd_names(self):
        for bad in (None, "", 3, "../academy.png", "..\\academy.png", "/etc/passwd", "NOTICE.md", "academy", ".png", "skin_000000000000.png",
                    "C:\\x\\academy.png", "band.png", "skin_ZZZZZZZZZZZZ.png", "skins/academy.png"):
            self.assertIsNone(style.skin_path(bad), bad)
            self.assertIsNone(editor.skin_path(bad), bad)


class LayoutMathTest(unittest.TestCase):
    def test_academy_layout(self):
        """덮을 곳 385~1495 (창 417~1465 + 찢어진 종이 홈 · 검토) → 16:9 원본 높이 1110 + 2·4 = 1118 화소면 1080 내보내기에서 위 끝 짝수 내림으로
        아래가 3화소만 더 덮여 한 화소씩 더: 1110 + 2·5 = 1120 · zoom 1120/607.5 · vpos 380/800 (넷째 자리) · 은평 것 없음."""
        lay = editor.skin_layout("academy.png")
        self.assertEqual(lay, {"mode": "box", "bar": "#000000", "zoom": round(1120 / 607.5, 4), "vpos": round(380 / 800, 4), "cropTop": 0.0,
                               "cropBottom": 0.0, "skin": "academy.png"})
        self.assertEqual((lay["zoom"], lay["vpos"]), (1.8436, 0.475))
        ok = editor.skin_fit(385 / 1920, 1495 / 1920)
        self.assertEqual(ok, (1.8436, 0.475))
        z0, v0 = round(1118 / 607.5, 4), round(381 / 802, 4)                              # 덧붙임 없는 값은 반올림 뒤 아래가 3화소뿐 → 받지 않음
        self.assertFalse(editor._skin_covers(z0, v0, 385 / 1920, 1495 / 1920))
        for Ww, Hh in editor.SKIN_SIZES_EXPORT:   # 내보내기 크기마다 위아래 4화소(1920 기준 · 720 은 2 · 4K 는 8) 넘게 더 덮음
            f = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080}, {"format": "shorts", "layout": lay}, Ww, Hh)
            y0, bl = int(f["by"]) & ~1, int(4 * Hh / 1920)
            self.assertLessEqual(y0, 385 * Hh / 1920 - bl, Ww)
            self.assertGreaterEqual(y0 + f["bh"], 1495 * Hh / 1920 + bl, Ww)
        self.assertFalse(editor.ep_font_on({"format": "shorts", "layout": lay}))           # KFA 글꼴 고정 없음 (win 없음)
        self.assertIsNone(editor.ep_win_bottom({"format": "shorts", "layout": lay}, W, H))  # 로고 자막 줄이기 없음
        self.assertIsNone(editor.ep_logo_rect(lay, W, H))
        f = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080}, {"format": "shorts", "layout": lay}, W, H)
        self.assertNotIn("band", f)
        self.assertFalse(f["win"])

    def test_box_covers_window(self):
        """반올림(_even · 넷째 자리 · yuv420 위 끝 짝수 내림) 뒤에도 16:9 원본 칸이 덮을 곳 + 위아래 4화소(크기에 맞춰)를 덮음 — 1080·720·4K 내보내기 ·
        미리보기(fit_rect) · 여러 곳."""
        wins = [(385 / 1920, 1495 / 1920), (417 / 1920, 1465 / 1920), (0.2, 0.8), (0.31, 0.62), (0.05, 0.95), (0.0, 0.5), (0.5, 1.0), (0.123457, 0.654321),
                (0.2172, 0.7631)]
        for top, bottom in wins:
            z, v = editor.skin_fit(top, bottom)
            lay = {"mode": "box", "bar": "#000000", "zoom": z, "vpos": v, "cropTop": 0.0, "cropBottom": 0.0}
            self.assertTrue(editor._skin_covers(z, v, top, bottom), (top, bottom, z, v))
            for Ww, Hh in editor.SKIN_SIZES_EXPORT:
                bl = int(4 * Hh / 1920)
                for sw, sh in ((1920, 1080), (1280, 720), (3840, 2160)):
                    f = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": sw, "h": sh}, {"format": "shorts", "layout": lay}, Ww, Hh)
                    y0 = int(f["by"]) & ~1
                    self.assertLessEqual(y0, max(0, top * Hh - bl) + 1e-6, (top, Ww, sw))
                    self.assertGreaterEqual(y0 + f["bh"], min(Hh, bottom * Hh + bl) - 1e-6, (bottom, Ww, sw))
            ch = (bottom - top) * H + 2 * editor.SKIN_BLEED
            if ch < H:
                self.assertLessEqual(abs(z - ch / 607.5), 0.02, (top, bottom))              # 덮을 곳 + 위아래 4화소 만큼만 (넉넉히 키우지 않음)


class AspectTest(unittest.TestCase):
    """검토: 확대가 16:9 기준 하나(1.8436)라 세로(9:16) 원본은 입·턱만 · 4:3 은 머리가 잘리고 · 21:9 는 창 안에 검은 띠 → 원본 비율로 창(+ 찢어진 종이 홈)을
    덮는 가장 작은 확대 (D-530) · 16:9 는 예전과 같은 값."""

    def lay(self, w, h):
        return editor.skin_layout("academy.png", {"width": w, "height": h})

    def test_zoom_per_aspect(self):
        base = editor.skin_layout("academy.png")
        self.assertEqual((base["zoom"], base["vpos"]), (1.8436, 0.475))                      # 16:9 그대로 (모를 때도)
        for w, h in ((1920, 1080), (1280, 720), (3840, 2160), (0, 0), (None, None)):
            self.assertEqual(editor.skin_layout("academy.png", {"width": w, "height": h}), base, (w, h))
        win = editor.skin_window("academy.png")
        want = {(1080, 1920): 1.0, (1440, 1080): round(1120 / 810, 4), (2560, 1080): None, (1080, 1350): 1.0, (1080, 1080): None}
        for (w, h), z in want.items():
            lay = self.lay(w, h)
            if z is not None:
                self.assertAlmostEqual(lay["zoom"], z, delta=0.003, msg=(w, h))
            self.assertTrue(editor._skin_covers(lay["zoom"], lay["vpos"], win["coverTop"], win["coverBottom"], w, h), (w, h))
            for Ww, Hh in editor.SKIN_SIZES_EXPORT:   # 내보내기 크기마다 덮을 곳 + 위아래 4화소 (21:9 도 창 안에 검은 띠 없음)
                f = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": w, "h": h}, {"format": "shorts", "layout": lay}, Ww, Hh)
                bl = int(4 * Hh / 1920)
                self.assertLessEqual(int(f["by"]) & ~1, max(0, win["coverTop"] * Hh - bl) + 1e-6, (w, h, Ww))
                self.assertGreaterEqual((int(f["by"]) & ~1) + f["bh"], min(Hh, win["coverBottom"] * Hh + bl) - 1e-6, (w, h, Ww))
        self.assertEqual(editor.skin_view(self.lay(1080, 1920)), 1.0)                         # 세로 원본: 가로는 다 보임 (입·턱만 보이던 것)
        self.assertGreater(editor.skin_view(self.lay(1440, 1080)), 0.7)                       # 4:3: 72% (예전 54%)
        v169 = editor.skin_view(base)
        self.assertAlmostEqual(v169, 1080 / 1992, places=4)
        # 4:5 처럼 확대 1 로도 덮을 곳보다 높은 원본은 덮을 곳 가운데에
        lay = self.lay(1080, 1350)
        f = editor.fit_rect({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1080, "h": 1350}, {"format": "shorts", "layout": lay}, W, H)
        self.assertAlmostEqual(f["by"] + f["bh"] / 2, (win["coverTop"] + win["coverBottom"]) / 2 * H, delta=2)
        info = editor.skin_info({"width": 1080, "height": 1920})                               # 편집실 템플릿도 그 영상 비율
        self.assertEqual(info["layout"]["zoom"], 1.0)
        self.assertEqual(editor.skin_info()["layout"], base)


class FollowTest(unittest.TestCase):
    """검토: 스킨 창은 16:9 원본 가로 가운데 54% 만 보여 가운데에서 벗어난 감독님이 4~8초씩 창 밖이었음 (기본 배치는 다 보임) →
    원본 V1 클립 묶음마다 얼굴을 보고 화면 좌우(reframe)를 맞추거나 얼굴을 따라 움직임(rfk) · 확대 기준점은 창 좌표로 (BR-326 · D-530)."""
    V = 1080 / 1992

    def inside(self, cx, rf, v=None):
        v = v or self.V
        x0 = rf * (1 - v)
        return x0 <= cx <= x0 + v

    def test_static_when_it_fits(self):
        smp = [(10 + k / 2, [[0.80, 0.1, 0.1, 0.3]]) for k in range(12)]
        rf, keys = editor.skin_follow(smp, self.V)
        self.assertIsNone(keys)
        self.assertTrue(self.inside(0.85, rf))
        x0 = rf * (1 - self.V)
        self.assertTrue(x0 + editor.SKIN_FOLLOW_PAD <= 0.80 and 0.90 + editor.SKIN_FOLLOW_PAD <= x0 + self.V + 1e-9)
        self.assertEqual(editor.skin_follow(smp, 1.0), (None, None))                          # 다 보이는 화면(세로 원본)은 그대로
        self.assertEqual(editor.skin_follow([(1.0, None), (1.5, [])], self.V), (None, None))  # 얼굴을 못 봄
        self.assertEqual(editor.skin_follow([(1.0, [[0.4, 0.4, 0.02, 0.03]])], self.V), (None, None))   # 멀리 작은 얼굴은 안 따라감

    def test_follows_slow_pan(self):
        """검토 rough_MSGRAW01_s1: 한 컷 안에서 얼굴이 원본 0.13 → 0.67 로 17초 동안 움직임 → 한 자리로는 안 되고 따라감 · 모든 칸에서 얼굴이 창 안."""
        smp = []
        for k in range(36):
            t = 28 + k / 2
            cx = 0.13 + (0.67 - 0.13) * k / 35 + (0.01 if k % 3 == 0 else -0.01)   # 얼굴 찾기 흔들림
            smp.append((t, [[cx - 0.045, 0.1, 0.09, 0.28]]))
        rf, keys = editor.skin_follow(smp, self.V)
        self.assertTrue(keys and len(keys) >= 2)
        self.assertLess(len(keys), 14)                                                         # 키프레임은 줄여서 (흔들림을 따라가지 않음)
        for t, fs in smp:
            r = editor._rf_keys_at(keys, t)
            self.assertTrue(self.inside(fs[0][0] + 0.045, r), (t, r))
        steps = [abs(b[1] - a[1]) / max(1e-6, b[0] - a[0]) for a, b in zip(keys, keys[1:])]
        self.assertLess(max(steps), 0.25)                                                      # 천천히 (1초에 reframe 0.25 밑)

    def test_scene_change_jumps(self):
        """얼굴이 뛴 두 칸 사이에 컷이 있으면 그 컷에서 바로 (컷 앞 장면은 옛 자리 그대로 · round 1 실제 렌더: 두 칸 가운데서 옮겨 컷 0.35초 앞부터 감독님이 창 밖이었음) ·
        컷이 없으면(같은 장면에서 빨리 움직임) 두 칸 사이 0.5초 동안 미끄러지듯 (D-537)."""
        smp = [(k / 2, [[0.15, 0.1, 0.1, 0.3]]) for k in range(10)] + [(5 + k / 2, [[0.80, 0.1, 0.1, 0.3]]) for k in range(10)]
        asked = []
        rf, keys = editor.skin_follow(smp, self.V, cut=lambda a, b: asked.append((a, b)) or 4.9)
        self.assertEqual(asked, [(4.5, 5.0)])
        for t, fs in smp:
            self.assertTrue(self.inside(fs[0][0] + 0.05, editor._rf_keys_at(keys, t)), t)
        jumps = [(a, b) for a, b in zip(keys, keys[1:]) if abs(b[1] - a[1]) > 0.3]
        self.assertEqual(len(jumps), 1)
        a, b = jumps[0]
        self.assertLessEqual(b[0] - a[0], 0.011)                                               # 컷에서 바로 (미끄러지지 않음)
        self.assertTrue(4.88 < a[0] < b[0] < 4.9, jumps)                                        # 컷 앞 장면(4.867)은 옛 자리 · 컷 장면(4.9)은 새 자리
        self.assertAlmostEqual(editor._rf_keys_at(keys, 4.8667), keys[0][1], places=6)
        self.assertAlmostEqual(editor._rf_keys_at(keys, 4.9), keys[-1][1], places=6)
        rf, glide = editor.skin_follow(smp, self.V, cut=lambda a, b: None)                     # 컷 없음: 4.5 → 5.0 미끄러지듯
        moves = [(a, b) for a, b in zip(glide, glide[1:]) if abs(b[1] - a[1]) > 0.3]
        self.assertEqual([(a[0], b[0]) for a, b in moves], [(4.5, 5.0)])
        for t, fs in smp:
            self.assertTrue(self.inside(fs[0][0] + 0.05, editor._rf_keys_at(glide, t)), t)
        self.assertEqual(editor.skin_follow(smp, self.V, cut=lambda a, b: 7.0)[1], glide)       # 두 칸 밖 컷 값은 안 씀

    def test_one_sample_flash_ignored(self):
        """round 1 실제 렌더 msg_MSGRAW01: 한 칸(0.5초)에만 왼쪽에 더 큰 얼굴(지나가는 사람)이 잡혀 창이 0.5초 동안 왼쪽으로 갔다 옴 → 앞뒤 칸이 같은 자리면 그 칸은
        그 자리 가까운 얼굴(없으면 앞 칸 자리) · 두 칸 넘게 이어지면 그대로 따라감 (D-537)."""
        coach = [0.555, 0.19, 0.058, 0.133]
        smp = [(150 + k / 2, [coach]) for k in range(12)]
        for extra in ([[0.227, 0.195, 0.109, 0.189], coach], [[0.227, 0.195, 0.109, 0.189]]):
            s = list(smp)
            s[5] = (s[5][0], extra)
            rf, keys = editor.skin_follow(s, self.V, cut=lambda a, b: self.fail("뛴 곳이 없어야 함"))
            self.assertIsNone(keys, extra)
            self.assertTrue(self.inside(0.584, rf))
        for i in (0, -1):                                                                        # 맨 앞·끝 한 칸도 (사이에 컷이 없으면)
            s = list(smp)
            s[i] = (s[i][0], [[0.227, 0.195, 0.109, 0.189]])
            self.assertIsNone(editor.skin_follow(s, self.V)[1])
            self.assertIsNone(editor.skin_follow(s, self.V, cut=lambda a, b: None)[1])
            rf, keys = editor.skin_follow(s, self.V, cut=lambda a, b: (a + b) / 2)              # 컷 너머 다음 장면이면 진짜 (round 1 rough_MSGRAW03_s2 끝 장면)
            self.assertTrue(keys, i)
            self.assertTrue(self.inside(0.28, editor._rf_keys_at(keys, s[i][0])), i)
        s = list(smp)
        for i in (5, 6, 7):
            s[i] = (s[i][0], [[0.227, 0.195, 0.109, 0.189]])                                    # 1초 넘게 이어지면 진짜로 다른 자리
        rf, keys = editor.skin_follow(s, self.V, cut=lambda a, b: None)
        self.assertTrue(keys)
        self.assertTrue(self.inside(0.28, editor._rf_keys_at(keys, 153.0)))
        alt = [(150 + k / 2, [[0.10, 0.1, 0.1, 0.3]] if k % 2 else [coach]) for k in range(12)]   # 번갈아 잡혀도 한쪽으로 (뒤집히지 않음)
        rf, keys = editor.skin_follow(alt, self.V, cut=lambda a, b: None)
        self.assertIsNone(keys)

    def test_reframe_items(self):
        """얼굴이 원본 0.15·0.85 인 컷 → 창 안 · 확대 컷 기준점은 창 좌표 · 정지 화면은 앞 컷 끝 자리 · 얼굴을 못 보면 가운데 그대로."""
        lay = editor.skin_layout("academy.png")
        a = editor._pair(10.0, 14.0, start=0.0)
        b = editor._pair(30.0, 34.0, start=4.0)
        b[0]["fx"] = {"scale": {"v": 115.0, "k": []}, "anchor": {"v": [0.86, 0.3], "k": []}}
        still = {"id": "st", "track": "V1", "media": "m_still", "start": 8.0, "in": 0.0, "out": 1.0, "speed": 1.0, "rev": False, "link": None, "reframe": 0.5,
                 "fit": "auto", "fx": {}, "color": {}}
        items = a + b + [still]
        fake = {}
        for t in [x / 2 for x in range(18, 30)]:
            fake[t] = [[0.10, 0.1, 0.1, 0.3]]
        for t in [x / 2 for x in range(58, 70)]:
            fake[t] = [[0.80, 0.1, 0.1, 0.3]]
        with mock.patch.object(editor, "skin_faces", lambda name, spans: {t: fake.get(t) for sp in spans for t in editor._skin_grid(*sp)}):
            faces = editor.skin_reframe("x.mp4", items, lay)
        self.assertTrue(faces)
        v = editor.skin_view(lay)
        self.assertTrue(self.inside(0.15, a[0]["reframe"], v))
        self.assertTrue(self.inside(0.85, b[0]["reframe"], v))
        self.assertNotIn("rfk", a[0])
        x0 = b[0]["reframe"] * (1 - v)
        self.assertAlmostEqual(b[0]["fx"]["anchor"]["v"][0], round((0.85 - x0) / v, 3), places=3)   # 그 컷 얼굴 (창 좌표)
        self.assertEqual(b[0]["fx"]["anchor"]["v"][1], 0.3)
        self.assertEqual(still["reframe"], round(editor.rf_at(b[0], 34.0), 4))
        for it in (a[1], b[1]):
            self.assertNotIn("reframe", it)                                                   # 소리 클립은 그대로
        plain = editor._pair(10.0, 14.0, start=0.0)
        with mock.patch.object(editor, "skin_faces", lambda name, spans: {}):
            editor.skin_reframe("x.mp4", plain, lay)
        self.assertEqual(plain[0]["reframe"], 0.5)
        vert = editor._pair(10.0, 14.0, start=0.0)                                            # 세로 원본(가로가 다 보임)은 볼 것도 없음
        with mock.patch.object(editor, "skin_faces", side_effect=AssertionError("부르면 안 됨")):
            editor.skin_reframe("x.mp4", vert, editor.skin_layout("academy.png", {"width": 1080, "height": 1920}))
        self.assertEqual(vert[0]["reframe"], 0.5)

    def test_keys_in_clip_and_export_expr(self):
        """따라가는 클립: rfk(원본 시각) · reframe = 가운데 값 · 내보내기 crop x 는 시간 식 · 미리보기(fit_rect m)·전술 그림(media_rect m)도 그 자리 ·
        구간 안에서 안 움직이면 숫자 그대로 · 프리미어는 가운데 값 하나 (움직임 목록에 적음)."""
        lay = editor.skin_layout("academy.png")
        v, au = editor._pair(20.0, 30.0, start=0.0)
        keys = [(20.0, 0.0), (25.0, 1.0)]
        fake = {t: [[0.13 + (0.6 * (t - 20) / 5 if t <= 25 else 0.6), 0.1, 0.08, 0.3]] for t in editor._skin_grid(20.0, 30.0)}
        with mock.patch.object(editor, "skin_faces", lambda name, spans: fake):
            editor.skin_reframe("x.mp4", [v, au], lay)
        self.assertTrue(v.get("rfk"))
        self.assertEqual(v["rfk"][0]["t"], 20.0)
        self.assertEqual(v["rfk"][-1]["t"], 30.0)
        self.assertEqual(v["reframe"], round(editor.rf_at(v, 25.0), 4))
        seq = {"format": "shorts", "layout": lay}
        md = {"kind": "video", "w": 1920, "h": 1080}
        for m in (20.0, 22.5, 27.0):
            f = editor.fit_rect(v, md, seq, W, H, m)
            self.assertAlmostEqual(-f["mx"] / (f["mw"] - W), editor.rf_at(v, m), places=6)
            import tactic
            self.assertAlmostEqual(tactic.media_rect(v, md, seq, W, H, m)["mx"], f["mx"], places=6)
        fit = editor._fit(v, md, seq, W, H, "if(lt(t,1),0,1)")
        self.assertIn("crop=w=1080:h=1120:x='912*(if(lt(t,1),0,1))':y=0.0", fit["chain"])   # 이름 붙인 값만 (x= 뒤 순서 값은 ffmpeg 가 잘못 읽음)
        self.assertIn("crop=1080:1120:", editor._fit(v, md, seq, W, H)["chain"])
        self.assertTrue(editor.premiere_motion(v, md, seq, W, H)["animated"])
        q = editor._new_seq("쇼츠", "shorts", [v, au], layout=lay, captionStyle=dict(editor.SHORTS_STYLE))
        mdd = {"main": {"id": "main", "kind": "video", "src": "videos", "file": "x.mp4", "dur": 60, "w": 1920, "h": 1080, "fps": 30.0}}
        with tempfile.TemporaryDirectory() as t, mock.patch.object(editor, "media_path", lambda f, s: Path(t) / "x.mp4"):
            (Path(t) / "x.mp4").write_bytes(b"0")
            editor._build_segment(q, mdd, W, H, 30, 0, 150, [], Path(t), 0)
            fc = (Path(t) / "fc0.txt").read_text(encoding="utf-8")
            self.assertIn("crop=w=1080:h=1120:x='912*(if(lt(t,", fc)                          # 움직이는 구간: 시간 식
            editor._build_segment(q, mdd, W, H, 30, 240, 270, [], Path(t), 1)
            fc1 = (Path(t) / "fc1.txt").read_text(encoding="utf-8")
            self.assertNotIn("x='", fc1.split("subtitles")[0].split("crop=1080:1120:")[1][:20])   # 안 움직이는 구간: 숫자
        self.assertEqual(keys[0][1], 0.0)

    @unittest.skipUnless(shutil.which("node"), "node 가 없어요")
    def test_preview_same(self):
        """editor.html rfAt·fitRect(t) = editor.py rf_at·fit_rect(m) · 화면 좌우 칸을 손으로 옮기면 따라가기를 끔."""
        from test_eunpyeong import run_js2
        lay = editor.skin_layout("academy.png")
        it = {"id": "a", "track": "V1", "media": "main", "start": 2.0, "in": 20.0, "out": 30.0, "speed": 1.0, "reframe": 0.4,
              "rfk": [{"t": 20.0, "v": 0.0, "e": "lin"}, {"t": 24.0, "v": 0.9, "e": "lin"}, {"t": 30.0, "v": 0.6, "e": "lin"}]}
        got = run_js2(f"""
S = {{ format: "shorts", layout: {json.dumps(lay)} }};
const it = {json.dumps(it)};
out = [2, 4.5, 6, 9, 11.9].map(t => [rfAt(it, t), fitRect(it, {{ kind: "video", w: 1920, h: 1080 }}, t).mx]);""", extra=("newPair", "fullSeq", "winClip", "fitRect", "rfAt", "kfAt", "lerp"))
        seq = {"format": "shorts", "layout": lay}
        for (r, mx), t in zip(got, (2, 4.5, 6, 9, 11.9)):
            m = editor.i_mt(it, t)
            self.assertAlmostEqual(r, editor.rf_at(it, m), places=9)
            self.assertAlmostEqual(mx, editor.fit_rect(it, {"kind": "video", "w": 1920, "h": 1080}, seq, W, H, m)["mx"], places=6)
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        self.assertIn('bindRange("pReframe", x => { v.reframe = x; if (v.rfk) { delete v.rfk;', src)
        self.assertIn("const f = fitRect(it, md, t), mo = motionAt(it, t)", src)               # 미리보기는 그 시각 자리


class TitleFitTest(unittest.TestCase):
    """검토: 긴 큰 제목이 48 로 줄어(소유자 96 의 절반 · 그 아래 주제 줄 88 보다 작음) 넘치면 libass 가 두 줄로 바꿔 한 줄 높이 흰 상자 위아래로 넘침 →
    가편집·MSG 가 만드는 큰 제목은 80 이상으로 들어가는 글(앞 구절 · 포인트 말 · 영상 주제 · skin_headline) · 손으로 고친 긴 글은 더 작게 한 줄(\\q2) ·
    주제 줄은 큰 제목보다 크지 않게 · 같은 글이면 뺌 (D-531)."""
    LONG = "패스하고 그 자리에 서 있으면 왜 안될까요 끝까지 보면 알게 돼요"

    def test_headline(self):
        H_ = editor.skin_headline
        self.assertEqual(H_("선수 이름 맞추기"), "선수 이름 맞추기")
        self.assertEqual(H_("퍼스트 터치\n첫 번째 포인트"), "퍼스트 터치 첫 번째 포인트")                 # 두 줄 훅은 한 줄로 (80)
        self.assertEqual(H_("패스하고 그 자리에 서 있으면 왜 안될까요?", [None, "패스 앤 무브 드릴"]), "패스 앤 무브 드릴")
        self.assertEqual(H_("퍼스트 터치 레슨, 이것만 알면 공이 발에 착 붙어요", []), "퍼스트 터치 레슨")        # 앞 구절
        cut = H_(self.LONG, [])
        self.assertGreaterEqual(editor.skin_title_style(cut)["size"], editor.SKIN_AUTO_MIN)
        self.assertTrue(self.LONG.startswith(cut))
        for t in ("트래핑 꿀팁", "슈팅 챌린지", "디딤발 꿀팁", "패스 두 번째 동작"):
            self.assertEqual(H_(t, ["x"]), t)

    def test_manual_long_title_one_line(self):
        st = editor.skin_title_style(self.LONG)
        self.assertLess(st["size"], 48)
        self.assertGreaterEqual(st["size"], editor.SKIN_TITLE_FLOOR)
        with editor.ep_font_scope(False):
            self.assertLessEqual(editor._text_w(self.LONG, st), 0.9 * W - 2 * editor.SKIN_PAD + 1e-6)
        self.assertEqual(editor.cap_wrap_lines(self.LONG, st, W), 1)
        top = {"id": "t", "text": self.LONG, "start": 0.0, "dur": 5.0, "style": st}
        q = {"format": "shorts", "captionStyle": dict(editor.SHORTS_STYLE), "titles": [top], "shapes": [], "items": [], "captionsOn": False, "captions": []}
        [d] = [ln for ln in editor.build_ass(q, W, H).splitlines() if ln.startswith("Dialogue:") and ",Tt," in ln]
        self.assertIn(r"\q2", d)                                                               # libass 가 줄을 바꾸지 않음
        plain = dict(top, style=dict(editor.TITLE_STYLE))
        [d2] = [ln for ln in editor.build_ass(dict(q, titles=[plain]), W, H).splitlines() if ln.startswith("Dialogue:") and ",Tt," in ln]
        self.assertNotIn(r"\q2", d2)                                                           # 스킨이 아닌 글자는 그대로

    def test_card(self):
        win = editor.skin_window("academy.png")
        top = {"id": "t", "text": "패스하고 그 자리에 서 있으면 왜 안될까요?"}
        kt = {"id": "k", "text": "패스 앤 무브 드릴", "plan": "topic"}
        shapes, kt2 = editor.skin_card(top, kt, 20, win, alts=[None, "패스 앤 무브 드릴"])
        self.assertEqual((top["text"], top["style"]["size"], kt2), ("패스 앤 무브 드릴", 96, None))   # 같은 글 주제 줄은 뺌
        top = {"id": "t", "text": "퍼스트 터치 첫 번째 포인트"}
        kt = {"id": "k", "text": "강원FC U12", "plan": "topic"}
        shapes, kt2 = editor.skin_card(top, kt, 20, win)
        self.assertEqual((top["style"]["size"], kt2["style"]["size"]), (80, 80))                   # 주제 줄은 큰 제목(80)보다 크지 않게

    def test_rough_long_hook(self):
        """끝까지 자동으로가 MSG 를 못 넣은 쇼츠(auto_sequences(rec=…))의 아주 긴 큰 제목 · 주제 없음 → 80 이상 한 줄."""
        import test_shorts_topic as ST
        w = ST.WorkMixin()
        w.setUp()
        try:
            name = w.fix("MSGRAW03")
            style.set_shorts_layout("academy")
            rec = editor.recommend(name)
            r = dict(rec["shorts"][0], hook=self.LONG)
            r.pop("topic", None)
            [q] = editor.auto_sequences(name, {"duration": ST.FIX["MSGRAW03"]["duration"], "width": 1920, "height": 1080}, None, ("shorts",),
                                        rec={"shorts": [r], "tidy": []})
            top = q["titles"][0]
            self.assertGreaterEqual(top["style"]["size"], editor.SKIN_AUTO_MIN, top["text"])
            with editor.ep_font_scope(False):
                self.assertLessEqual(editor._text_w(top["text"], top["style"]), 0.9 * W - 2 * editor.SKIN_PAD + 1e-6)
        finally:
            w.tearDown()


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class CaptionFitJsTest(unittest.TestCase):
    """editor.html capFit·capLines·hugOne = editor.py cap_fit·cap_lines·hug_one (스킨 말 자막 실제 글자 폭 · 95% · D-533) · 스킨이 아니면 예전 셈."""

    def test_same(self):
        from test_eunpyeong import run_js2
        win = editor.skin_window("academy.png")
        sk = editor.skin_caption(dict(editor.SHORTS_STYLE), win["bottom"])
        plain = dict(editor.SHORTS_STYLE)
        texts = ["애들 목소리가 다 똑같아...", "수비가 바로 붙어버리니까요.", "가" * 13, "가나다 라마바 사아자 차카타 파하거 너더러", "ABC 1234 xyz!", "가나다라마바사아자차카타파하거"]
        got = run_js2(f"""
const T_ = {json.dumps(texts, ensure_ascii=False)}, ST = [{json.dumps(sk)}, {json.dumps(plain)}];
out = ST.map(st => T_.map(t => [capFit(t, st, 1080), capLines(t, st, 1080), hugOne(t, st, 1080)]));""",
                      extra=("newPair", "fullSeq", "winClip", "fitRect", "SKIN", "SKIN_HUG", "fitNeed", "capFit", "hugOne", "capLines", "capWrapLines"))
        for st, rows in zip((sk, plain), got):
            for t, (k, n, one) in zip(texts, rows):
                self.assertEqual(k, editor.cap_fit(t, st, W), t)
                self.assertEqual(n, editor.cap_lines(t, st, W), t)
                self.assertEqual(one, editor.hug_one(t, st, W), t)
        self.assertEqual(got[0][0], [1, 1, True])


class RoughTest(WorkMixin, unittest.TestCase):
    INFO = {"duration": FIX["MSGRAW01"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}

    def test_rough_shorts(self):
        name = self.fix("MSGRAW01")
        style.set_shorts_layout("academy")
        style.set_card_extras(series=True, long_line=True)      # 은평 덧붙임은 스킨에 안 들어감
        win = editor.skin_window("academy.png")
        topics = 0
        for st in ({"captionPos": "top", "captionColor": "#1A1A1A"}, {"captionPos": "bottom"}, None):
            qs = editor.auto_sequences(name, self.INFO, st, ("shorts",))
            self.assertTrue(qs)
            for q in qs:
                self.assertEqual(q["layout"], editor.skin_layout(), q["name"])
                L = editor.i_end(max(q["items"], key=editor.i_end))
                self.assertFalse(any(t.get("plan") in ("series", "longline") for t in q["titles"]))
                self.assertFalse(any(g.get("plan") == "series" for g in q["shapes"]))
                cs = q["captionStyle"]
                self.assertEqual((cs["y"], cs["fill"], cs["stroke"]), (0.717, "#FFFFFF", "#000000"))
                # 소유자 쇼츠 말 자막 모양 (흰 Black 96 · 검은 테두리 10 · 상자 없음 · 실제 글자 폭으로 한 줄 · D-529 · D-532 · D-533) · 나타나기는 가편집 쇼츠 그대로(pop)
                self.assertEqual((cs["weight"], cs["size"], cs["strokeW"], cs["bgOn"], cs["effect"], cs["hug"]), ("Black", 96, 10, False, editor.SHORTS_STYLE["effect"], "skin"))
                top = q["titles"][0]
                self.assertNotIn("\n", top["text"])
                self.assertEqual((top["start"], top["dur"]), (0.0, round(L, 2)))
                self.assertEqual(top["style"], editor.skin_title_style(top["text"]))
                self.assertEqual((top["style"]["fill"], top["style"]["strokeW"], top["style"]["bgOn"], top["style"]["effect"], top["style"]["weight"]),
                                 ("#CE4F2F", 0, False, "none", "Black"))
                self.assertIn(top["style"]["size"], editor.SKIN_SIZES)
                self.assertGreaterEqual(top["style"]["size"], editor.SKIN_AUTO_MIN, top["text"])          # 가편집이 만드는 큰 제목은 80 이상 (D-531)
                with editor.ep_font_scope(False):
                    tw = editor._text_w(top["text"], top["style"])
                if top["style"]["size"] > editor.SKIN_SIZES[-1]:
                    self.assertLessEqual(tw, 0.9 * W - 2 * editor.SKIN_PAD + 1e-6)
                bigger = [z for z in editor.SKIN_SIZES if z > top["style"]["size"]]
                if bigger:
                    with editor.ep_font_scope(False):
                        self.assertGreater(editor._text_w(top["text"], dict(top["style"], size=min(bigger))), 0.9 * W - 2 * editor.SKIN_PAD)
                [box] = [g for g in q["shapes"] if g.get("plan") == "card"]
                self.assertEqual((box["color"], box["opacity"], box["radius"], box["full"], box["start"], box["y"], box["h"]),
                                 ("#FFFFFF", 1.0, 17, True, 0.0, 0.0922, 0.0625))
                hw = editor.skin_hug_w(top["text"], top["style"])                              # 상자 폭은 실제 글자 폭 (크기 고르기의 넉넉한 어림보다 좁음 · 검토)
                self.assertLess(hw, tw)
                self.assertAlmostEqual(box["w"], min(0.9, (hw + 2 * editor.SKIN_PAD) / W), places=3)
                self.assertAlmostEqual(box["x"] + box["w"] / 2, 0.5, places=3)
                self.assertEqual(int(17 / 100 * min(int(box["w"] * W), int(box["h"] * H)) / 2), 10)   # 둥근 모서리 약 10화소 (ASS shape_line 셈)
                tb = editor.text_box(top["text"], top["style"], W, H)
                self.assertAlmostEqual((tb[1] + tb[3]) / 2, box["y"] + box["h"] / 2, places=3)     # 상자 세로 가운데
                self.assertAlmostEqual(box["y"] * H, 177, delta=0.5)
                self.assertAlmostEqual((box["y"] + box["h"]) * H, 297, delta=0.5)
                for kt in [t for t in q["titles"] if t.get("plan") == "topic"]:
                    topics += 1
                    self.assertEqual((kt["start"], kt["dur"], kt.get("under"), kt.get("swap")), (0.0, round(L, 2), top["id"], None))
                    self.assertEqual(kt["style"], editor.skin_topic_style(kt["text"], win["top"], top["style"]["size"]))
                    self.assertLessEqual(kt["style"]["size"], top["style"]["size"])                       # 주제 줄은 큰 제목보다 크지 않음 (D-531)
                    self.assertNotEqual(editor._norm(kt["text"]), editor._norm(top["text"]))
                    self.assertEqual((kt["style"]["fill"], kt["style"]["weight"], kt["style"]["strokeW"]), ("#FFFFFF", "Black", 0))
                    kb = editor.text_box(kt["text"], kt["style"], W, H)
                    self.assertLessEqual(kb[3], win["top"] - editor.SKIN_TOPIC_GAP + 1e-9)  # 영상 창 위 끝 - 틈 위
                    self.assertLess(abs((kb[1] + kb[3]) / 2 - 0.179), 0.006)                # 가운데 0.179 (창에 닿으면 조금 위로)
                for t in q["titles"]:   # 큰 제목 한 줄 · 그 밖 글자(배운 강조 큰 글씨)는 영상 창 안 (위 띠·아래 띠에 안 걸침)
                    if t is top or t.get("plan") == "topic":
                        continue
                    b = editor.text_box(t["text"], t["style"], W, H)
                    self.assertTrue(win["top"] <= b[1] and b[3] <= win["bottom"], (t["text"], b))
                    self.assertLessEqual(kb[2] - kb[0], 0.92)
                self.assertFalse(editor.ep_font_on(q))
        self.assertGreater(topics, 0)
        # 스킨을 못 읽으면 지금처럼 (멈추지 않음)
        with mock.patch.object(editor, "skin_window", lambda ref: None):
            for q in editor.auto_sequences(name, self.INFO, None, ("shorts",)):
                self.assertEqual(q["layout"], editor.BOX_LAYOUT)
        style.set_shorts_layout("")
        for q in editor.auto_sequences(name, self.INFO, None, ("shorts",)):
            self.assertNotIn("skin", q["layout"])

    def test_text_rules(self):
        """주제 줄: 한 줄로 90% 안 가장 큰 글자 · 창 위 끝이 높은 스킨이면 위로 · 말 자막: 창 아래 끝 - 0.04 (배운 자리와 상관없이)."""
        self.assertEqual(editor.skin_topic_style("강원FC U12", 417 / 1920)["size"], 96)                # 소유자 쇼츠 약 97 (D-532 · 예전 88)
        self.assertEqual(editor.skin_topic_style("강원FC U12", 417 / 1920)["y"], 0.2091)       # 0.179 + 반 줄이면 창 틈에 닿아 위로 (글자 301~386화소 = 소유자)
        self.assertEqual(editor.skin_topic_style("강원FC U12", 0.3)["y"], round(0.179 + 1.25 * 96 / 2 / 1920, 4))
        self.assertEqual(editor.skin_topic_style("강원FC U12", 417 / 1920, 72)["size"], 72)            # 큰 제목(72)보다 크지 않게 (D-531)
        long = editor.skin_topic_style("퍼스트 터치 레슨 · 포인트 3 · 디딤발 방향 정하기", 0.3)
        self.assertLess(long["size"], 88)
        self.assertEqual(editor.skin_cap_y(1465 / 1920), 0.717)                                       # 글자 아래 끝 1361화소 (소유자 1362 · D-532)
        for y0 in (0.15, 0.55, 0.85, 0.9):
            c = editor.skin_caption(dict(editor.SHORTS_STYLE, y=y0, fill="#1A1A1A", stroke="#FFFFFF", strokeW=3), 1465 / 1920)
            self.assertEqual((c["y"], c["fill"], c["stroke"], c["strokeW"]), (0.717, "#FFFFFF", "#000000", 10))
        # 말 자막 모양은 소유자 쇼츠 값 그대로 (배운 굵기·크기·테두리·상자와 상관없이 · D-529) · 나타나기·노래방 강조 색은 배운 스타일 그대로
        for base in (dict(editor.SHORTS_STYLE, strokeW=12), dict(editor.SHORTS_STYLE, weight="Bold", size=60, strokeW=4, bgOn=True, effect="fade"),
                     dict(editor.SHORTS_STYLE, size=80, effect="karaoke", highlight="#00FF00"), dict(editor.LONG_STYLE, effect="none")):
            c = editor.skin_caption(base, 0.8)
            self.assertEqual({k: c[k] for k in editor.SKIN_CAP}, {"weight": "Black", "size": 96, "fill": "#FFFFFF", "stroke": "#000000", "strokeW": 10, "bgOn": False,
                                                                  "hug": "skin"})
            self.assertEqual((c["effect"], c.get("highlight"), c["y"]), (base["effect"], base.get("highlight"), 0.754))
        self.assertEqual(editor.skin_title_style("선수 이름 맞추기")["size"], 96)
        self.assertEqual(editor.skin_title_style("퍼스트 터치 첫 번째 포인트")["size"], 80)
        st = editor.skin_title_style("가" * 40)
        self.assertEqual(st["size"], editor.SKIN_TITLE_FLOOR)                                 # 48 에도 안 들어가면 더 작게 (FLOOR 까지 · D-531)
        g = editor.skin_box({"text": "가" * 40, "style": st}, 10)
        self.assertLessEqual(g["w"], 0.9)                                                     # 줄인 글에 맞춘 폭 (가장 넓게 90%)
        self.assertAlmostEqual(g["x"] + g["w"] / 2, 0.5, places=3)
        g = editor.skin_box({"text": "가" * 40, "style": dict(st, size=48)}, 10)
        self.assertEqual((g["w"], g["x"]), (0.9, 0.05))                                       # 손으로 키운 크기면 가장 넓게 90%

    def test_caption_one_line(self):
        """소유자 쇼츠 말 자막(96 · 한 줄): 한 줄 셈은 Pretendard Black 실제 글자 폭 표로 화면 폭 95%까지 (D-533 · 예전 넉넉한 어림 90% 라 소유자 줄도 0.9 배) —
        소유자 줄 '애들 목소리가 다 똑같아...'는 그대로 96 · 한 줄 · ASS \\q2 (libass 가 줄을 바꾸지 않음) · 더 긴 한 줄은 글자만 줄여 한 줄 (FIT_MIN 까지) ·
        그보다 훨씬 긴 글은 예전처럼 띄어쓰기에서 줄을 바꿈 · 스킨이 아닌 자막 셈은 그대로."""
        import captions
        st = editor.skin_caption(dict(editor.SHORTS_STYLE), 1465 / 1920)
        for text in ("애들 목소리가 다 똑같아...", "가" * captions.LIMITS["shorts"][0], "패스하고 그 자리에 서 있으면", "선수 이름 맞추기"):
            k = editor.cap_fit(text, st, W)
            self.assertTrue(editor.FIT_MIN <= k <= 1.0, (text, k))
            self.assertEqual(editor.cap_lines(text, st, W), 1, text)
        self.assertEqual(editor.cap_fit("애들 목소리가 다 똑같아...", st, W), 1.0)          # 실제 글자 폭(약 999화소)은 화면 95%(1026) 안 → 그대로 96 (검토: 예전 0.9 배)
        self.assertTrue(editor.hug_one("애들 목소리가 다 똑같아...", st, W))
        self.assertGreaterEqual(editor.cap_fit("수비가 바로 붙어버리니까요.", st, W), 0.94)   # 검토: 예전 0.84 배(77.3)로 줄던 줄 → 0.94 배(90)
        plain = dict(st)
        plain.pop("hug")
        self.assertLess(editor.cap_fit("애들 목소리가 다 똑같아...", plain, W), 1.0)       # 스킨이 아닌 자막은 예전 셈 그대로 (넉넉한 어림 · 90%)
        self.assertFalse(editor.hug_one("애들 목소리가 다 똑같아...", plain, W))
        self.assertEqual(editor.cap_lines("가나다 라마바 사아자 차카타 파하거 너더러", st, W), 2)   # 너무 길면 두 줄 (창 안에서 위로)
        self.assertFalse(editor.hug_one("가나다 라마바 사아자 차카타 파하거 너더러", st, W))
        v = {"id": "v", "track": "V1", "media": "main", "start": 0.0, "in": 0.0, "out": 10.0, "speed": 1.0}
        proj = {"format": "shorts", "captionStyle": st, "titles": [], "shapes": [], "items": [v], "captionsOn": True,
                "captions": [{"id": "a", "start": 0.0, "end": 2.0, "text": "애들 목소리가 다 똑같아..."}]}
        [d] = [ln.split(",", 9) for ln in editor.build_ass(proj, W, H).splitlines() if ln.startswith("Dialogue: 1,") and ",Cap," in ln]
        k = editor.cap_fit("애들 목소리가 다 똑같아...", st, W)
        self.assertEqual(k, 1.0)
        self.assertIn(rf"\pos(540,{int(H * 0.717)})\q2", d[9])                                   # 줄을 바꾸지 않음 (D-533)
        self.assertNotRegex(d[9], r"\\fs\d")                                                  # 글자를 줄이지 않음
        self.assertNotIn(r"\N", d[9])
        sty = next(ln for ln in editor.build_ass(proj, W, H).splitlines() if ln.startswith("Style: Cap,"))
        self.assertIn(",&H00FFFFFF,", sty)                                                     # 흰 글씨
        self.assertIn(",10,", sty)                                                             # 테두리 10

    def test_box_hugs_real_width(self):
        """검토: 흰 상자 폭이 넉넉한 어림(_text_w · 한글 0.95em)으로 셈해 글보다 양옆 60화소쯤 넓었음 → Pretendard Black 실제 글자 폭 표(SKIN_HUG)로 셈 —
        PIL 로 실은 글꼴(fonts/Pretendard-Black.otf)을 재 본 폭과 0.5% 안 · 소유자 쇼츠 상자(206~862 · 657화소)와 거의 같은 폭 · 크기 고르기는 그대로."""
        from PIL import ImageFont
        font = ImageFont.truetype(str(ROOT / "fonts" / "Pretendard-Black.otf"), 1000)
        for text in ("선수 이름 맞추기", "강원FC U12", "트래핑 꿀팁", "퍼스트 터치 레슨 · 포인트 3", "2대1 패스 ABC xyz!", "‘왜’ 안될까요? (1/3)", "ㅋㅋ 진짜…"):
            st = dict(editor.skin_title_style(text), size=100)
            real = font.getlength(text) / 10
            self.assertLess(abs(editor.skin_hug_w(text, st) - real), 0.005 * real + 0.5, text)
            with editor.ep_font_scope(False):
                self.assertGreater(editor._text_w(text, st), editor.skin_hug_w(text, st), text)   # 크기 고르기 어림은 여전히 넉넉함
        top = {"text": "선수 이름 맞추기", "style": editor.skin_title_style("선수 이름 맞추기")}
        self.assertEqual(top["style"]["size"], 96)
        g = editor.skin_box(top, 10)
        self.assertEqual((g["x"], g["w"]), (0.1989, 0.6023))                                  # 215~865화소 (양옆 14 · 소유자 206~862 · D-532 · 예전 201~879)
        self.assertLess(abs(g["w"] * W - 657), 12)
        with mock.patch.object(editor, "SKIN_HUG", {"hangul": 0.864, "em": {}}):              # 표에 없는 글자는 Pretendard 어림 (멈추지 않음)
            self.assertGreater(editor.skin_hug_w("⚽ A", dict(st, size=100)), 0)


class RoughEmphasisTest(WorkMixin, unittest.TestCase):
    """검토: 배운 스타일의 강조 큰 글씨(emphasisTitles)가 스킨 가편집 쇼츠에서만 빠졌음 (기본 배치는 넣음) → 영상 창 안 (창 맨 위 줄·머리 위·말 자막 바로 위 ·
    위 띠·흰 상자·주제 줄·말 자막·아래 띠를 피함 · D-534)."""

    def test_emphasis_in_window(self):
        name = self.fix("MSGRAW01")
        info = {"duration": FIX["MSGRAW01"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}
        st = {"emphasisTitles": {"perMin": 2.0, "color": "#FFD400"}, "zoomEvery": 6, "zoomScale": 1.15}
        style.set_shorts_layout("")
        plain = [t for q in editor.auto_sequences(name, info, st, ("shorts",)) for t in q["titles"] if t.get("plan") == "emphasis"]
        style.set_shorts_layout("academy")
        win = editor.skin_window("academy.png")
        got = 0
        for q in editor.auto_sequences(name, info, st, ("shorts",)):
            cb = editor._cap_band(q["captionStyle"], H)
            for t in [t for t in q["titles"] if t.get("plan") == "emphasis"]:
                got += 1
                b = editor.text_box(t["text"], t["style"], W, H)
                self.assertTrue(win["top"] + editor.TXT_GAP <= b[1] + 1e-9 and b[3] <= cb[0] + 1e-9, (t["text"], b, cb))
                self.assertIn(t["style"]["size"], editor.SKIN_EMPH_SIZES)
                self.assertTrue(editor._one_line(t["text"], t["style"], W))
        self.assertTrue(plain)
        self.assertEqual(got, len(plain))                                                     # 기본 배치와 같은 수


class MsgSkinTest(unittest.TestCase):
    """MSG 쇼츠(재미있게 자동 편집 · 끝까지 자동으로): 스킨 화면 구성 · 큰 제목·흰 상자 · 고정 글자 말고는 위 띠(0~창 위)·아래 띠(창 아래~끝)에 글자 없음."""

    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        style.set_shorts_layout("academy")
        style.set_card_extras(series=True, long_line=True)
        specs = [{"kind": "preset", "name": n} for n in msg.PRESETS]
        cls.qs, cls.lay_caps = [], []
        orig = msg.layout_titles

        def rec(B, caps_tl, cap_style, *a, **k):   # 글자 자리 정리가 피하는 말 자막 모양 (실제 말 자막과 같아야 함 · D-529)
            cls.lay_caps.append(dict(cap_style))
            return orig(B, caps_tl, cap_style, *a, **k)
        with mock.patch.object(msg, "layout_titles", rec):
            for inten in ("듬뿍", "보통"):
                cls.qs += msg.build_variants(cls.w.name, specs[:3], inten, ("shorts",), lambda m: None)["sequences"]

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_msg_shorts(self):
        self.assertTrue(self.qs)
        lay = editor.skin_layout()
        win = editor.skin_window(lay["skin"])
        top, bot = win["top"] * H, win["bottom"] * H
        others = 0
        for q in self.qs:
            self.assertEqual(q["layout"], lay, q["name"])
            cs = q["captionStyle"]
            self.assertEqual((cs["y"], cs["fill"], cs["stroke"]), (0.717, "#FFFFFF", "#000000"))
            self.assertEqual((cs["weight"], cs["size"], cs["strokeW"], cs["bgOn"], cs["hug"]), ("Black", 96, 10, False, "skin"))   # 소유자 쇼츠 말 자막 (D-529 · D-532)
            self.assertIn(cs["effect"], ("none", "karaoke"))                                 # 나타나기는 MSG 말 자막 그대로 (_caption_style)
            self.assertFalse(any(t.get("plan") in ("series", "longline") for t in q["titles"]))
            end = round(editor.seq_total(q), 3)
            [box] = [g for g in q["shapes"] if g.get("plan") == "card"]
            self.assertEqual((box["radius"], box["dur"], box["y"], box["h"]), (17, end, 0.0922, 0.0625))
            self.assertFalse(any(g.get("name") == "검은 띠" for g in q["shapes"]))            # 다시 보기 위아래 검은 띠 없음
            fixed = [t for t in q["titles"] if t.get("plan") == "topic" or t["style"] == editor.skin_title_style(t["text"])]
            self.assertTrue(fixed, q["name"])
            for t in fixed:
                self.assertEqual((t["start"], t["dur"]), (0.0, end))
            for t in q["titles"]:
                if t in fixed:
                    continue
                others += 1
                b = msg.text_box(t["text"], t["style"], W, H)
                self.assertGreaterEqual(b[1], top - 1e-6, (q["name"], t["text"], t["style"].get("y"), b))   # 위 띠에 안 걸침
                self.assertLessEqual(b[3], bot + 1e-6, (q["name"], t["text"], t["style"].get("y"), b))       # 아래 띠에 안 걸침
        self.assertGreater(others, 3)

    def test_layout_avoids_real_caption(self):
        """글자 자리 정리(layout_titles)는 스킨의 실제 말 자막(흰 Black 96 · 테두리 10 · 창 아래 끝 - 0.046)을 피함 — 예전에는 MSG 기본 말 자막(72)으로 셈해
        강조 글자가 커진 말 자막 윗부분에 닿을 수 있었음 (D-529)."""
        self.assertTrue(self.lay_caps)
        for c in self.lay_caps:
            self.assertEqual((c["weight"], c["size"], c["strokeW"], c["bgOn"], c["y"]), ("Black", 96, 10, False, 0.717))

    def test_layout_titles_avoid_bands(self):
        """글자 자리 정리: 띠 안에 놓인 강조·상황·속마음 글자는 창 안으로 옮김 · 스킨이 아니면 예전 그대로."""
        lay = editor.skin_layout()
        win = editor.skin_window(lay["skin"])
        for skin in (True, False):
            b = msg._Build("x.mp4", {"duration": 60.0, "width": 1080, "height": 1920}, "shorts")
            if skin:
                b.lay, b.skin = lay, win
            ts = []
            for k, (kind, y) in enumerate((("emphasis", 0.12), ("situ", 0.18), ("inner", 0.9), ("fx", 0.97))):
                t = b.title(f"강조 글자 {k + 1}", 5.0 + k * 10, 2.0, dict(editor.TITLE_STYLE, size=80, y=y))
                b.event(kind, t["start"], t["text"], "", refs={"titles": [t["id"]]})
                ts.append(t)
            gone = msg.layout_titles(b, [], editor.skin_caption(dict(editor.SHORTS_STYLE), win["bottom"]), 60.0)
            kept = [t for t in ts if t["id"] not in gone]
            if skin:
                self.assertEqual(len(kept), 4)
                for t in kept:
                    bx = msg.text_box(t["text"], t["style"], W, H)
                    self.assertTrue(win["top"] * H <= bx[1] and bx[3] <= win["bottom"] * H, (t["text"], t["style"]["y"], bx))
            else:
                self.assertEqual([t["style"]["y"] for t in ts if t["id"] not in gone][:2], [0.12, 0.18])

    def test_rows(self):
        """상황 자막·점수판·'다시 보기 ▶'는 영상 창 맨 위 줄 (상자 위 끝 = 창 위 끝 + 틈)."""
        win = editor.skin_window("academy.png")
        looks = msg.text_looks({"captions": {}}, "shorts", 0.717, skin=win)
        for k in ("situ", "score"):
            b = msg.text_box("가나다", looks[k], W, H)
            self.assertAlmostEqual(b[1], (win["top"] + editor.TXT_GAP) * H, delta=2.5, msg=k)
        plain = msg.text_looks({"captions": {}}, "shorts", 0.717)
        self.assertEqual((plain["situ"]["y"], plain["score"]["y"]), (0.18, 0.27))            # 스킨이 아니면 예전 자리


@unittest.skipUnless(FF, "ffmpeg 가 없어요")
class ExportSkinTest(WorkMixin, unittest.TestCase):
    """실제 내보내기 (1080×1920 · 2.5초): V1 0~1초 · 빈 곳 1~1.5초 · V1 1.5~2.5초 + V2(화면 전체) 1.8~2.5초 · 큰 제목·흰 상자·주제 줄·말 자막."""

    def frame(self, out, t, w=1080, h=1920, pix="rgb24", ch=3):
        import numpy as np
        raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", pix, "-"],
                             capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(h, w, ch).astype(int)

    def test_render(self):
        import numpy as np
        from PIL import Image
        name = self.fix("MSGRAW01")
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x{'%02X%02X%02X' % VIDEO_RGB}:s=640x360:r=30:d=4", "-f", "lavfi", "-i",
                            "anullsrc=r=48000:cl=stereo", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)],
                           capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        info = {"duration": 4.0, "width": 640, "height": 360, "fps": 30.0}
        A, B = editor._pair(0.0, 1.0, start=0.0), editor._pair(1.5, 2.5, start=1.5)
        v2 = dict(editor._pair(2.0, 2.7, start=1.8)[0], track="V2", fit="cover")
        lay = editor.skin_layout()
        win = editor.skin_window(lay["skin"])
        top = {"id": "t", "text": "선수 이름 맞추기", "style": dict(editor.TITLE_STYLE)}
        kt = {"id": "k", "text": "강원FC U12", "plan": "topic"}
        shapes, kt = editor.skin_card(top, kt, 2.5, win)
        cs = editor.skin_caption(dict(editor.SHORTS_STYLE), win["bottom"])
        q = editor._new_seq("쇼츠 스킨", "shorts", A + B + [v2], captionStyle=cs, layout=lay, titles=[top, kt], shapes=shapes)
        proj = dict(q, captions=[{"id": "c1", "start": 0.0, "end": 2.5, "text": "애들 목소리가 다 똑같아"}], info=info, source=name, media=[],
                    master={"volume": 1, "normalize": False, "lufs": -14})
        outs = editor.export(name, proj, {"preset": "youtube", "hw": False, "xml": True, "srt": False, "overlay": True}, lambda m: None)
        mp4 = core.OUT / next(o for o in outs if o.endswith(".mp4"))
        skin = np.asarray(Image.open(SKIN).convert("RGBA")).astype(int)
        sk_rgb, alpha = skin[..., :3], skin[..., 3]
        t1 = round(win["bottom"] * H)
        c0, c1 = round(win["coverTop"] * H), round(win["coverBottom"] * H)
        self.assertEqual((c0, c1), (385, 1495))
        see = np.zeros(alpha.shape, bool)   # 찢어진 종이 홈 — 창(417~1464 · 글자·말 자막 자리) 밖으로 비치는 줄 (385~416 · 1465~1494)
        for r0, r1 in ((c0, round(win["top"] * H)), (t1, c1)):
            see[r0:r1] = alpha[r0:r1] < 255
        for t, what in ((0.5, "V1"), (1.25, "빈 곳"), (2.2, "V2 화면 전체")):
            a = self.frame(mp4, t)
            for rows in ((0, 170), (t1 + 4, H)):   # 위 띠(흰 상자 위) · 아래 띠 전체 = 스킨 화소 (영상·V2 가 덮지 않음)
                region = (slice(*rows), slice(None))
                d = np.abs(a[region] - sk_rgb[region])[alpha[region] == 255].mean()
                self.assertLess(d, 6, (what, rows, d))
            mid = a[int(0.45 * H):int(0.5 * H), 400:680].reshape(-1, 3).mean(axis=0)
            if what == "빈 곳":
                self.assertLess(mid.max(), 30, (what, mid))                                     # 창 가운데 = 바탕(검정)
            else:
                self.assertLess(np.abs(mid - VIDEO_RGB).max(), 12, (what, mid))                # 창 가운데 = 영상
            # 창 가장자리 안쪽(스킨이 완전히 투명해지는 첫 줄 둘레) = 영상 (16:9 원본이 창을 덮음 · 위아래로 빈 줄 없음)
            clear = np.where((alpha[:, 108:972] == 0).all(axis=1))[0]
            if what != "빈 곳":
                for y in (clear.min(), clear.max()):
                    row = a[y, 300:780].mean(axis=0)
                    self.assertLess(np.abs(row - VIDEO_RGB).max(), 25, (what, y, row))
                # 찢어진 종이 홈·그림자(창 밖으로 비치는 385~1494 줄 · 검토): 비치는 화소에는 박스 바탕(검정)이 아니라 영상이 섞여 보임
                dark = (a.max(axis=2) < 60) & (alpha < 128) & see
                self.assertEqual(int(dark.sum()), 0, (what, np.where(dark.any(axis=1))[0][:5]))
                k = alpha[..., None] / 255.0
                want = k * sk_rgb + (1 - k) * np.array(VIDEO_RGB)
                d = np.abs(a - want).max(axis=2)[see & (alpha < 128)]
                self.assertLess(float(np.median(d)), 20, (what, float(np.median(d))))
            # 흰 상자(177~297)는 스킨 위 (상자 왼쪽 안 · 둥근 모서리·글자 없는 곳 · 그 자리 스킨은 칠판) · 큰 제목 주황 글씨
            bx0 = int(shapes[0]["x"] * W)
            box = (slice(190, 285), slice(bx0 + 3, bx0 + 10))   # (양옆 여백 14 안 · 글자 밖)
            self.assertGreater(a[box].min(), 230, what)
            self.assertLess(sk_rgb[box].max(), 200)
            inner = a[190:285, int(0.3 * W):int(0.7 * W)].reshape(-1, 3)
            orange = (np.abs(inner - (0xCE, 0x4F, 0x2F)).max(axis=1) < 40).mean()
            self.assertGreater(orange, 0.05, what)
            self.assertLess(a[240, bx0 - 12:bx0 - 4].max(), 200, what)                         # 상자 밖(위 띠) = 칠판 (흰 상자 아님)
            # 말 자막 흰 글씨는 창 안 · 아래 끝 0.723 위 (빈 곳에는 말 자막도 없음)
            if what == "빈 곳":
                continue
            r0, r1 = clear.min() + 10, clear.max() - 10
            win_rows = (a[r0:r1].min(axis=2) > 225).sum(axis=1)
            cap = np.where(win_rows > 3)[0] + r0
            self.assertTrue(len(cap) > 10 and cap.max() <= 0.717 * H + 10 and cap.min() > 0.6 * H, (what, cap.min() if len(cap) else None))
        # 글자·도형만 투명 영상(D-348)에는 스킨이 없음 — 위 띠는 투명 · 흰 상자는 있음
        ov = [o for o in outs if o.endswith((".mov", ".zip"))]
        self.assertTrue(ov, outs)
        if ov[0].endswith(".mov"):
            o = self.frame(core.OUT / ov[0], 0.5, pix="rgba", ch=4)
            self.assertEqual(o[0:170, :, 3].max(), 0)
            self.assertEqual(o[t1 + 4:, :, 3].max(), 0)
            self.assertGreater(o[184:290, bx0 + 14:bx0 + 30, 3].min(), 240)
        notes = "\n".join(p.read_text(encoding="utf-8") for p in core.OUT.rglob("*.txt"))
        self.assertIn("풋살사관학교 스킨이 빠져요", notes)
        # 검토: 프리미어 '빠진 것'이 스킨 그림을 얹으라면서 어디 있는지 안 알려 줌 → XML 옆에 '<이름>_스킨.png' 로 복사하고 그 이름을 적음 (D-536)
        png = [o for o in outs if o.endswith(editor.SKIN_TAG)]
        self.assertEqual(len(png), 1, outs)
        self.assertEqual((core.OUT / png[0]).read_bytes(), SKIN.read_bytes())
        self.assertIn(f"'{png[0]}'(스킨 그림)", notes)
        # 지금 화면 그림으로 저장(프레임 PNG)도 같은 그리기
        fr = editor.export_frame(name, proj, 0.5)
        png = np.asarray(Image.open(core.OUT / fr["file"]).convert("RGB")).astype(int)
        d = np.abs(png[0:170] - sk_rgb[0:170])[alpha[0:170] == 255].mean()
        self.assertLess(d, 6)
        self.assertEqual(int(((png.max(axis=2) < 60) & (alpha < 128) & see).sum()), 0)        # 찢어진 종이 홈에도 영상 (검토)

    def test_follow_render(self):
        """얼굴 따라가기 키프레임(rfk)이 있는 클립을 실제로 내보냄 (검토 고침 뒤 실제 렌더에서 ffmpeg 가 crop 옵션을 잘못 읽어 실패했음 — 그래프 글만 보던 시험이
        못 잡음) — 원본 왼쪽 절반 빨강 · 오른쪽 절반 파랑 · 화면 좌우 0 → 1 (2초) → 처음엔 창 가운데가 빨강, 끝엔 파랑 · 지금 화면 그림도 같은 자리."""
        import numpy as np
        from PIL import Image
        name = self.fix("MSGRAW01")
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x360:r=30:d=3", "-f", "lavfi", "-i", "color=c=blue:s=320x360:r=30:d=3",
                            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", "[0:v][1:v]hstack[v]", "-map", "[v]", "-map", "2:a", "-t", "3",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        info = {"duration": 3.0, "width": 640, "height": 360, "fps": 30.0}
        lay = editor.skin_layout(info=info)
        win = editor.skin_window(lay["skin"])
        v, au = editor._pair(0.0, 2.5, start=0.0)
        v["rfk"] = [{"t": 0.3, "v": 0.0, "e": "lin"}, {"t": 2.0, "v": 1.0, "e": "lin"}]
        v["reframe"] = 0.5
        q = editor._new_seq("쇼츠 따라가기", "shorts", [v, au], captionStyle=editor.skin_caption(dict(editor.SHORTS_STYLE), win["bottom"]), layout=lay,
                            titles=[], shapes=[])
        proj = dict(q, captions=[], info=info, source=name, media=[], master={"volume": 1, "normalize": False, "lufs": -14})
        outs = editor.export(name, proj, {"preset": "youtube", "hw": False, "xml": False, "srt": False}, lambda m: None)
        mp4 = core.OUT / next(o for o in outs if o.endswith(".mp4"))
        mid = (slice(int(0.45 * H), int(0.5 * H)), slice(500, 580))
        for t, want in ((0.15, (255, 0, 0)), (2.3, (0, 0, 255))):
            a = self.frame(mp4, t)[mid].reshape(-1, 3).mean(axis=0)
            self.assertLess(np.abs(a - want).max(), 40, (t, a))
        a = self.frame(mp4, 1.15)                                                                    # 가운데쯤: 창 안에 빨강·파랑 경계가 보임
        row = a[int(0.48 * H), 60:1020]
        self.assertTrue((row[:, 0] > 200).any() and (row[:, 2] > 200).any(), row[::120].tolist())
        fr = editor.export_frame(name, proj, 0.15)
        png = np.asarray(Image.open(core.OUT / fr["file"]).convert("RGB")).astype(int)
        self.assertLess(np.abs(png[mid].reshape(-1, 3).mean(axis=0) - (255, 0, 0)).max(), 40)

    def test_follow_sample_times_and_cut(self):
        """얼굴 칸 t 의 그림 = t 의 장면 (ffmpeg fps 기본(near)은 t+0.23초까지의 장면을 줘 바뀐 장면을 먼저 봤음 → 창이 컷보다 0.35초 먼저 옮겨감 · round 1) ·
        두 칸 사이 컷 시각 = 실제로 그림이 바뀐 장면 · 컷이 없으면 None (D-537). 원본: 2.1초에 빨강 → 파랑."""
        from PIL import Image
        import face
        name = self.fix("MSGRAW01")
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=4", "-vf", "drawbox=c=blue:t=fill:enable='gte(t,2.1)'",
                            "-c:v", "libx264", "-g", "60", "-pix_fmt", "yuv420p", str(core.VIDEOS / name)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])

        def fake_faces(f):
            px = Image.open(f).convert("RGB").getpixel((5, 5))
            return [{"box": [0.1 if px[0] > px[2] else 0.8, 0.1, 0.1, 0.3]}]
        with mock.patch.object(editor, "_face_ready", lambda n: True), mock.patch.object(face, "faces", fake_faces):
            got = editor.skin_faces(name, [(1.0, 3.0)])
        self.assertEqual({t: got[t][0][0] for t in (1.0, 1.5, 2.0, 2.5, 3.0)}, {1.0: 0.1, 1.5: 0.1, 2.0: 0.1, 2.5: 0.8, 3.0: 0.8})
        self.assertAlmostEqual(editor.skin_cut(name, 2.0, 2.5), 2.1, delta=0.034)
        self.assertIsNone(editor.skin_cut(name, 0.5, 1.0))
        self.assertIsNone(editor.skin_cut("없는 영상.mp4", 0.5, 1.0))

    def test_long_title_stays_in_box(self):
        """검토: 26글자 큰 제목이 두 줄로 바뀌어 흰 상자(177~296) 위로 넘침 → 손으로 고친 긴 글도 한 줄 · 주황 글자 줄은 흰 상자 안 (실제 그림)."""
        import numpy as np
        from PIL import Image
        name = self.fix("MSGRAW01")
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x284050:s=640x360:r=30:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        info = {"duration": 2.0, "width": 640, "height": 360, "fps": 30.0}
        lay = editor.skin_layout(info=info)
        win = editor.skin_window(lay["skin"])
        for text in (TitleFitTest.LONG, "선수 이름 맞추기"):
            top = {"id": "t", "text": text, "style": dict(editor.TITLE_STYLE)}
            top["style"] = editor.skin_title_style(text)
            top.update(start=0.0, dur=2.0)
            shapes = [editor.skin_box(top, 2.0)]
            q = editor._new_seq("쇼츠 긴 제목", "shorts", editor._pair(0.0, 2.0, start=0.0), captionStyle=editor.skin_caption(dict(editor.SHORTS_STYLE), win["bottom"]),
                                layout=lay, titles=[top], shapes=shapes)
            proj = dict(q, captions=[], info=info, source=name, media=[], master={"volume": 1, "normalize": False, "lufs": -14})
            fr = editor.export_frame(name, proj, 1.0)
            a = np.asarray(Image.open(core.OUT / fr["file"]).convert("RGB")).astype(int)
            orange = (np.abs(a - (0xCE, 0x4F, 0x2F)).max(axis=2) < 60)
            rows = np.where(orange[100:420].sum(axis=1) > 2)[0] + 100
            self.assertTrue(len(rows) > 10, text)
            self.assertGreaterEqual(rows.min(), round(editor.SKIN_BOX["y"] * H), (text, rows.min()))
            self.assertLessEqual(rows.max(), round((editor.SKIN_BOX["y"] + editor.SKIN_BOX["h"]) * H), (text, rows.max()))
            cols = np.where(orange[100:420].sum(axis=0) > 0)[0]
            self.assertGreaterEqual(cols.min(), shapes[0]["x"] * W - 2)
            self.assertLessEqual(cols.max(), (shapes[0]["x"] + shapes[0]["w"]) * W + 2)

    def test_filter_graph(self):
        """구간 필터: 스킨은 한 장 그림 입력(-loop 1 · image2) → 화면 크기(4K 도)로 · 한 번만 yuva420p 로 바꿔 yuv420 그대로 겹침(D-535 · 예전 rgba +
        format=auto 는 프레임마다 바꿔 1.9배 느렸음) · 그다음 자막(subs.ass) ·
        스킨이 아니거나 그림 파일이 없으면 예전 그대로 (입력 하나)."""
        md = {"main": {"id": "main", "kind": "video", "src": "videos", "file": "x.mp4", "dur": 10, "w": 1920, "h": 1080, "fps": 30.0}}
        v, a = editor._pair(0, 2, start=0)

        def seg(layout, Ww, Hh, fmt="shorts"):
            q = editor._new_seq("쇼츠", fmt, [v, a], layout=layout, captionStyle=dict(editor.SHORTS_STYLE))
            with tempfile.TemporaryDirectory() as t, mock.patch.object(editor, "media_path", lambda f, s: Path(t) / "x.mp4"):
                (Path(t) / "x.mp4").write_bytes(b"0")
                args, final, n = editor._build_segment(q, md, Ww, Hh, 30, 0, 30, [], Path(t), 0)
                return args, (Path(t) / "fc0.txt").read_text(encoding="utf-8")
        lay = editor.skin_layout()
        for Ww, Hh in ((1080, 1920), (2160, 3840), (720, 1280)):
            args, fc = seg(lay, Ww, Hh)
            self.assertEqual(args.count("-i"), 2)
            self.assertIn(str(SKIN), args)
            i = args.index(str(SKIN))
            self.assertEqual(args[i - 11:i - 9] + args[i - 5:i - 3], ["-loop", "1", "-f", "image2"])
            self.assertIn(f"scale={Ww}:{Hh}:flags=lanczos,format=yuva420p,fps=30", fc)               # 바꾸기는 프레임을 늘리기 전에 한 번
            i_skin, i_sub = fc.index("format=yuva420p"), fc.index("subtitles=subs.ass")
            self.assertLess(i_skin, i_sub)                                                     # 스킨 → 자막 차례
            ov = [ln for ln in fc.split(";\n") if "overlay=x=0:y=0:format=yuv420,format=yuv420p" in ln]
            self.assertEqual(len(ov), 1)
        for other in (dict(lay, skin="skin_000000000000.png"), editor.BOX_LAYOUT, editor.EP_LAYOUT):
            args, fc = seg(other, 1080, 1920)
            self.assertEqual(args.count("-i"), 1, other)
            self.assertNotIn("format=yuva420p", fc)
        args, fc = seg(lay, 1920, 1080, fmt="long")                                            # 롱폼에는 스킨 없음
        self.assertEqual(args.count("-i"), 1)


class SameAsBeforeTest(unittest.TestCase):
    """기본·은평 스타일 쇼츠 (가편집 · MSG · 내보내기 구간 필터 · ASS) = 스킨 기능 전 코드(v2.25.0)와 같은 바이트 (tests/skin_same.py 지문)."""

    def test_same(self):
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "skin_same.py"), str(ROOT)], capture_output=True, text=True,
                           encoding="utf-8", timeout=900)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        want = json.loads((Path(__file__).resolve().parent / "fixtures" / "academy_same.json").read_text(encoding="utf-8"))
        for k in ("rough", "segments", "ass", "counts", "msg", "msgCounts"):
            self.assertEqual(got[k], want[k], k)


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class EditorTemplateTest(unittest.TestCase):
    """editor.html 템플릿 '풋살사관학교 스킨'(skinCard · skinTitleStyle · skinTopicStyle · skinBoxFit · skinCapY)을 node 로 그대로 → 서버와 같은 값."""
    EXTRA = ("newPair", "fullSeq", "winClip", "fitRect", "SKIN", "SKIN_HUG", "SKX", "skinTitleStyle", "skinBoxFit", "skinTopicStyle", "skinCapY", "skinCard", "skinOn",
             "skinTop", "skinRefit")

    def run_js(self, body):
        from test_eunpyeong import EditorTemplateTest as EPT, run_js2
        return run_js2(EPT.SHORT + body, extra=self.EXTRA)

    def test_template_same_as_server(self):
        info = editor.skin_info()
        texts = ["트래핑 꿀팁", "선수 이름 맞추기", "패스하고 그 자리에 서 있으면 왜 안될까요?", "가나다라마바사아자차카타파하거너더러머버서어"]
        topics = ["퍼스트 터치 레슨 · 포인트 3", "강원FC U12", "가나다라마바사아자차카타파하거너더러머버서어 ABC"]
        got = self.run_js(f"""
const cp = x => JSON.parse(JSON.stringify(x));
SKX = {json.dumps(info)};
out = {{ titles: {json.dumps(texts, ensure_ascii=False)}.map(t => skinTitleStyle(t)), topics: {json.dumps(topics, ensure_ascii=False)}.map(t => skinTopicStyle(t, SKX.top)),
         cap: skinCapY(SKX.bottom), runs: [] }};
for (const swap of [false, true]) {{
  S = rough(swap); S.captionStyle.y = 0.85; S.captionStyle.fill = "#1A1A1A"; S.captionStyle.strokeW = 3; S.captionStyle.effect = "pop"; S.captionStyle.bgOn = true;
  TEMPLATES["풋살사관학교 스킨"](); out.runs.push(cp(S));
  TEMPLATES["풋살사관학교 스킨"](); out.runs.push({{ n: S.shapes.length }});   // 두 번 눌러도 상자 하나
  TEMPLATES["위아래 검정 박스"](); out.runs.push(cp(S));
}}
EPX = {{ series: true, seriesName: "1분 레슨", longLine: true, next: 3, pics: {{ band: "band.png", logo: "logo.png" }} }};
S = rough(false); TEMPLATES["은평 스타일"](); TEMPLATES["풋살사관학교 스킨"](); out.fromEp = cp(S);   // 은평 → 스킨: 은평 것을 지움
TEMPLATES["은평 스타일"](); out.toEp = cp(S);                                                         // 스킨 → 은평: 스킨을 지움
SKX = null; S = rough(false); out.noSkin = {{ ok: skinCard(S, total()), S: cp(S) }};""")
        for t, st in zip(texts, got["titles"]):
            self.assertEqual(st, editor.skin_title_style(t), t)
        for t, st in zip(topics, got["topics"]):
            self.assertEqual(st, editor.skin_topic_style(t, info["top"]), t)
        self.assertEqual(got["cap"], editor.skin_cap_y(info["bottom"]))
        for k in (0, 3):
            r = got["runs"][k]
            self.assertEqual(r["layout"], info["layout"])
            cs, want_cs = r["captionStyle"], editor.skin_caption({"y": 0.85, "fill": "#1A1A1A", "strokeW": 3, "effect": "pop"}, info["bottom"])
            for k2 in ("y", "fill", "stroke", "strokeW", "weight", "size", "bgOn", "effect"):
                self.assertEqual(cs[k2], want_cs[k2], k2)
            self.assertEqual((cs["size"], cs["strokeW"], cs["bgOn"], cs["effect"], cs["hug"]), (96, 10, False, "pop", "skin"))   # 소유자 말 자막 (D-529 · D-532) · 나타나기 그대로
            big = next(t for t in r["titles"] if t["id"] == "big")
            kt = next(t for t in r["titles"] if t["id"] == "k")
            self.assertNotIn("\n", big["text"])
            self.assertEqual((big["start"], big["dur"]), (0, 30))
            self.assertEqual(big["style"], editor.skin_title_style(big["text"]))
            self.assertEqual((kt["start"], kt["dur"], kt.get("swap"), kt["under"]), (0, 30, None, "big"))
            self.assertEqual(kt["style"], editor.skin_topic_style(kt["text"], info["top"], big["style"]["size"]))
            [box] = r["shapes"]
            want = editor.skin_box(big, 30)
            for key in ("x", "y", "w", "h", "color", "opacity", "radius", "start", "dur", "full", "plan"):
                self.assertEqual(box[key], want[key], key)
            self.assertEqual(got["runs"][k + 1], {"n": 1})
            off = got["runs"][k + 2]
            self.assertNotIn("skin", off["layout"])                                            # 다른 템플릿: 스킨·흰 상자 지움
            self.assertEqual(off["shapes"], [])
            self.assertEqual(off["layout"]["zoom"], 1)
            self.assertNotIn("hug", off["captionStyle"])                                       # 스킨 한 줄 셈도 지움 (D-533)
            self.assertFalse(any("hug" in t["style"] for t in off["titles"]))
        fe = got["fromEp"]
        self.assertEqual(fe["layout"], info["layout"])
        for k in ("win", "band", "logo"):
            self.assertNotIn(k, fe["layout"])
        self.assertFalse(any(t.get("plan") in ("series", "longline") for t in fe["titles"]))
        self.assertEqual([g["plan"] for g in fe["shapes"]], ["card"])
        self.assertEqual(fe["shapes"][0]["radius"], 17)
        te = got["toEp"]
        self.assertNotIn("skin", te["layout"])
        self.assertTrue(te["layout"]["win"])
        self.assertEqual(te["shapes"][0]["radius"], 0)
        self.assertEqual(got["noSkin"]["ok"], False)                                           # 스킨을 못 읽었으면 그대로
        self.assertNotIn("skin", got["noSkin"]["S"]["layout"])
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        self.assertIn('if (b.dataset.tpl === "풋살사관학교 스킨" && !SKX) return toast(', src)
        # 검토: '새 쇼츠 편집본으로 추가'는 스킨 바탕(흰 상자도 plan "card" 라 epOn 이 참)이면 KFA 글꼴 찾기를 기다리지 않음 (스킨은 KFA 글꼴을 안 씀)
        self.assertIn("if (epOn(recBase().base) && !skinOn(recBase().base)) await epFontReady();", src)
        self.assertEqual(src.count("await epFontReady()"), 2)                                 # 나머지 하나는 템플릿 '은평 스타일' 단추뿐
        self.assertIn('if (b.dataset.tpl === "은평 스타일" && !EPF && EPX && EPX.fontChecking)', src)
        self.assertIn("SKX = (EPX && EPX.skin) || null;", src)
        # 미리보기 차례: 스킨 그림(#skinL)은 영상 묶음(#layers · z 1) 다음 · 글자(#ov · z 60) 앞 · z 30
        self.assertIn('<div id="layers"></div><img id="skinL" alt="" draggable="false"><div class="ov" id="ov">', src)
        css = src[src.index("#skinL {"):].split("\n")[0]
        self.assertIn("z-index: 30", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn("skinShow();  // 풋살사관학교 스킨", src)


    def test_title_edit_refits_box(self):
        """검토: 스킨 큰 제목 글을 편집실에서 고치면 흰 상자가 그대로라 긴 글이 상자 밖(칠판 위)으로 넘쳤음 → 글을 고칠 때(타이틀 글 칸 oninput)·크기를 바꿀 때
        skinRefit 이 상자 폭을 다시 맞추고, 크기·세로 자리를 손대지 않았으면 글자 크기도 새 글에 맞춤 (서버 skin_title_style·skin_box_fit 과 같은 값) ·
        손댄 크기는 그대로 · 스킨이 아니거나 큰 제목이 아니면 아무것도 안 함. 상자 폭 표(SKIN_HUG)도 서버와 같음."""
        info = editor.skin_info()
        texts = ["선수 이름 맞추기", "강원FC U12", "트래핑 꿀팁 세 가지 알려줄게요", "‘왜’ 안될까요? (1/3)", "ㅋㅋ 진짜… ⚽"]
        got = self.run_js(f"""
const cp = x => JSON.parse(JSON.stringify(x));
SKX = {json.dumps(info)};
const T = {json.dumps(texts, ensure_ascii=False)};
out = {{ hug: T.map(t => textW(t, {{ ...SKIN.TITLE, size: 96 }}, SKIN_HUG)), steps: [] }};
S = rough(false); TEMPLATES["풋살사관학교 스킨"]();
const big = S.titles.find(t => t.id === "big"), k = S.titles.find(t => t.id === "k"), box = () => cp(S.shapes.find(g => g.plan === "card"));
out.steps.push({{ st: cp(big.style), box: box() }});
let old = big.text; big.text = "트래핑 꿀팁 세 가지 알려줄게요"; out.ok = skinRefit(S, big, old); out.steps.push({{ st: cp(big.style), box: box() }});   // 글을 길게
old = big.text; big.text = "트래핑"; skinRefit(S, big, old); out.steps.push({{ st: cp(big.style), box: box() }});                                   // 짧게
big.style.size = 70; skinRefit(S, big); out.steps.push({{ st: cp(big.style), box: box() }});                                                         // 크기를 손댐 (크기 칸)
old = big.text; big.text = "트래핑 꿀팁 세 가지"; skinRefit(S, big, old); out.steps.push({{ st: cp(big.style), box: box() }});                      // 손댄 크기는 그대로
out.topic = skinRefit(S, k, k.text);                                                                                                                  // 주제 줄은 아님
TEMPLATES["위아래 검정 박스"](); const t2 = S.titles.find(t => t.id === "big"), st2 = cp(t2.style); old = t2.text; t2.text = "아주 긴 제목으로 바꿔도 그대로예요";
out.plain = {{ ok: skinRefit(S, t2, old), same: JSON.stringify(st2) === JSON.stringify(t2.style), n: S.shapes.length }};""")
        for t, w in zip(texts, got["hug"]):
            self.assertEqual(w, editor.skin_hug_w(t, dict(editor.SKIN_TITLE, size=96)), t)   # 같은 표·같은 셈
        def want(text, size=None):
            st = editor.skin_title_style(text)
            if size:
                st = dict(st, size=size)
            g = {}
            editor.skin_box_fit({"text": text, "style": st}, g)
            return st, g
        s0, s1, s2, s3, s4 = got["steps"]
        self.assertEqual(s0["st"], editor.skin_title_style("트래핑 꿀팁"))
        self.assertTrue(got["ok"])
        for step, text in ((s1, "트래핑 꿀팁 세 가지 알려줄게요"), (s2, "트래핑")):
            st, g = want(text)
            self.assertEqual(step["st"], st, text)
            self.assertEqual((step["box"]["x"], step["box"]["w"]), (g["x"], g["w"]), text)
        self.assertLess(s1["st"]["size"], s0["st"]["size"])                                   # 긴 글은 작은 글자로 (96 → 64)
        self.assertGreater(s1["box"]["w"], s0["box"]["w"])                                    # 상자도 넓어짐
        with editor.ep_font_scope(False):                                                     # 글이 상자 안 (실제 폭 + 양옆 28 = 상자)
            self.assertLessEqual(editor._text_w("트래핑 꿀팁 세 가지 알려줄게요", s1["st"]), 0.9 * W - 56 + 1e-6)
        st, g = want("트래핑", size=70)
        self.assertEqual((s3["st"]["size"], s3["box"]["w"]), (70, g["w"]))
        st, g = want("트래핑 꿀팁 세 가지", size=70)
        self.assertEqual((s4["st"]["size"], s4["st"]["y"], s4["box"]["w"]), (70, s2["st"]["y"], g["w"]))
        self.assertFalse(got["topic"])
        self.assertEqual(got["plain"], {"ok": False, "same": True, "n": 0})
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        self.assertIn("const old = tt.text; tt.text = ta.value;", src)                         # 타이틀 글 칸 → skinRefit
        self.assertIn("skinRefit(S, tt, old);", src)
        self.assertIn('bindStyle("ts_", () => tt.style, el, k => { if (["size", "weight", "strokeW"].includes(k)) skinRefit(S, tt); });', src)


class RouteTest(unittest.TestCase):
    def test_routes(self):
        from test_windows_compat import Server

        class T(Server):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            def get(path):
                req = urllib.request.Request(f"http://127.0.0.1:{t.port}{path}")
                try:
                    with t.opener.open(req, timeout=20) as r:
                        return r.status, r.headers.get("Content-Type"), r.read()
                except urllib.error.HTTPError as e:
                    return e.code, None, b""
            st, ct, body = get("/api/style/skin?f=academy.png")
            self.assertEqual((st, ct), (200, "image/png"))
            self.assertEqual(body, SKIN.read_bytes())
            for bad in ("../app.py", "..%2Fapp.py", "NOTICE.md", "", "skin_000000000000.png", "band.png"):
                self.assertEqual(get("/api/style/skin?f=" + bad)[0], 404, bad)
            self.assertEqual(t.call("/api/style/list")[1]["skin"], {"file": "", "ref": "academy.png"})
            self.assertEqual(t.call("/api/style/shorts_layout", {"layout": "academy"}), (200, {"ok": True, "layout": "academy"}))
            self.assertEqual(t.call("/api/style/shorts_layout")[1], {"layout": "academy"})
            st, j = t.call("/api/style/skin", {"data": data_url(skin_png((540, 960)))})
            self.assertEqual((st, j["ok"]), (200, True))
            self.assertRegex(j["file"], r"^skin_[0-9a-f]{12}\.png$")
            st2, ct2, body2 = get("/api/style/skin?f=" + j["file"])
            self.assertEqual((st2, ct2), (200, "image/png"))
            st, j2 = t.call("/api/style/skin", {"data": data_url(skin_png((1080, 1080)))})
            self.assertEqual(st, 400)
            self.assertIn("가운데가 투명하게 비어 있는 9:16 그림(PNG)을 골라 주세요", j2["error"])
            self.assertEqual(t.call("/api/style/skin", {"reset": True})[1], {"ok": True, "file": "", "ref": "academy.png"})
            self.assertEqual(t.call("/api/style/shorts_layout", {"layout": "x"})[0], 400)
        finally:
            t.doCleanups()
        import rstudio
        self.assertEqual(rstudio.classify("GET", "/api/style/skin"), rstudio.READ)
        self.assertEqual(rstudio.classify("POST", "/api/style/skin"), rstudio.WRITE)
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('skin=editor.skin_info(proj.get("info"))', src)   # 편집실을 열 때 템플릿이 넣을 스킨 (epx.skin · 그 영상 비율 · D-530)
        ui = (ROOT / "ui.html").read_text(encoding="utf-8")
        self.assertIn('<option value="academy">풋살사관학교 스킨 (초록 칠판 · 내 채널 스킨)</option>', ui)
        self.assertEqual(style.SHORTS_LAYOUTS, ("", "eunpyeong", "academy"))


if __name__ == "__main__":
    unittest.main()
