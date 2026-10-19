"""풋살사관학교 스킨 쇼츠 (BR-320~BR-325 · D-520~D-524) — 저장소 폴더에서 python3 -m unittest tests.test_academy_skin

- 앱과 함께 싣는 스킨(skins/academy.png · git 추적 = 배포 목록 · 업데이트 경로 규칙 · 출처 글)
- 영상 창 찾기(academy.png → 417~1465 줄 · 기억 · 파일이 바뀌면 다시) · '스킨 바꾸기' 확인(9:16 · 투명한 창 30% 이상 · 크기 · 투명 그대로 ·
  내용 지문 이름 · 한글 폴더) · 거절(창 없음 · 비율 · 너무 작음 · 투명 없음 · 그림 아님) · 처음 스킨으로 · 이상한 이름
- 화면 구성 셈(zoom·vpos · 16:9 원본이 1080·720·4K 내보내기와 미리보기 모두에서 창을 덮음) · 은평 것(win·띠 그림·로고·글꼴 고정)은 없음
- 가편집 쇼츠(큰 제목·흰 상자·주제 줄·말 자막 값) · MSG 쇼츠(고정 글자 말고는 위·아래 띠에 글자 없음 · 말 자막 자리)
- 실제 내보내기(1080×1920): 위·아래 띠 = 스킨 화소 · 창 가운데 = 영상 · V2(화면 전체)·빈 곳에도 스킨 · 흰 상자·주황 큰 제목이 스킨 위 ·
  글자·도형만 투명 영상에는 스킨 없음 · 프리미어 '빠진 것' · 4K 필터
- 기본·은평 스타일은 스킨 전 코드와 같은 결과 (tests/skin_same.py 지문 = tests/fixtures/academy_same.json)
- 편집실 템플릿 '풋살사관학교 스킨'(editor.html skinCard 를 node 로) = 서버 값 · 다른 템플릿·은평 스타일은 스킨을 지움 · 길(/api/style/skin) · 원격 분류"""
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

    def test_window_rules_and_cache(self):
        from PIL import Image
        im = Image.open(io.BytesIO(skin_png((360, 640), 0.3, 0.6)))
        self.assertEqual(style.skin_window_of(im), (192, 384))
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
        """창 417~1465 → 16:9 원본 높이 1048 + 2·4 = 1056 화소 · zoom 1056/607.5 · vpos 413/864 (넷째 자리) · 은평 것 없음."""
        lay = editor.skin_layout("academy.png")
        self.assertEqual(lay, {"mode": "box", "bar": "#000000", "zoom": round(1056 / 607.5, 4), "vpos": round(413 / 864, 4), "cropTop": 0.0,
                               "cropBottom": 0.0, "skin": "academy.png"})
        self.assertEqual((lay["zoom"], lay["vpos"]), (1.7383, 0.478))
        self.assertFalse(editor.ep_font_on({"format": "shorts", "layout": lay}))           # KFA 글꼴 고정 없음 (win 없음)
        self.assertIsNone(editor.ep_win_bottom({"format": "shorts", "layout": lay}, W, H))  # 로고 자막 줄이기 없음
        self.assertIsNone(editor.ep_logo_rect(lay, W, H))
        f = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080}, {"format": "shorts", "layout": lay}, W, H)
        self.assertNotIn("band", f)
        self.assertFalse(f["win"])

    def test_box_covers_window(self):
        """반올림(_even · 넷째 자리 · yuv420 위 끝 짝수 내림) 뒤에도 16:9 원본 칸이 창을 덮음 — 1080·720·4K 내보내기 · 미리보기(fit_rect) · 여러 창."""
        wins = [(417 / 1920, 1465 / 1920), (0.2, 0.8), (0.31, 0.62), (0.05, 0.95), (0.0, 0.5), (0.5, 1.0), (0.123457, 0.654321), (0.2172, 0.7631)]
        for top, bottom in wins:
            z, v = editor.skin_fit(top, bottom)
            lay = {"mode": "box", "bar": "#000000", "zoom": z, "vpos": v, "cropTop": 0.0, "cropBottom": 0.0}
            self.assertTrue(editor._skin_covers(z, v, top, bottom), (top, bottom, z, v))
            for Ww, Hh in editor.SKIN_SIZES_EXPORT:
                for sw, sh in ((1920, 1080), (1280, 720), (3840, 2160)):
                    f = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": sw, "h": sh}, {"format": "shorts", "layout": lay}, Ww, Hh)
                    y0 = int(f["by"]) & ~1
                    self.assertLessEqual(y0, top * Hh + 1e-6, (top, Ww, sw))
                    self.assertGreaterEqual(y0 + f["bh"], bottom * Hh - 1e-6, (bottom, Ww, sw))
            ch = (bottom - top) * H + 2 * editor.SKIN_BLEED
            if ch < H:
                self.assertLessEqual(abs(z - ch / 607.5), 0.02, (top, bottom))              # 창 + 위아래 4화소 만큼만 (넉넉히 키우지 않음)


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
                self.assertEqual((cs["y"], cs["fill"], cs["stroke"]), (0.723, "#FFFFFF", "#000000"))
                self.assertGreaterEqual(cs["strokeW"], 8)
                self.assertEqual(cs["size"], editor.SHORTS_STYLE["size"])                  # 배운 자막 크기 그대로
                top = q["titles"][0]
                self.assertNotIn("\n", top["text"])
                self.assertEqual((top["start"], top["dur"]), (0.0, round(L, 2)))
                self.assertEqual(top["style"], editor.skin_title_style(top["text"]))
                self.assertEqual((top["style"]["fill"], top["style"]["strokeW"], top["style"]["bgOn"], top["style"]["effect"], top["style"]["weight"]),
                                 ("#CE4F2F", 0, False, "none", "Black"))
                self.assertIn(top["style"]["size"], editor.SKIN_SIZES)
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
                self.assertAlmostEqual(box["w"], min(0.9, (tw + 56) / W), places=3)
                self.assertAlmostEqual(box["x"] + box["w"] / 2, 0.5, places=3)
                self.assertEqual(int(17 / 100 * min(int(box["w"] * W), int(box["h"] * H)) / 2), 10)   # 둥근 모서리 약 10화소 (ASS shape_line 셈)
                tb = editor.text_box(top["text"], top["style"], W, H)
                self.assertAlmostEqual((tb[1] + tb[3]) / 2, box["y"] + box["h"] / 2, places=3)     # 상자 세로 가운데
                self.assertAlmostEqual(box["y"] * H, 177, delta=0.5)
                self.assertAlmostEqual((box["y"] + box["h"]) * H, 297, delta=0.5)
                for kt in [t for t in q["titles"] if t.get("plan") == "topic"]:
                    topics += 1
                    self.assertEqual((kt["start"], kt["dur"], kt.get("under"), kt.get("swap")), (0.0, round(L, 2), top["id"], None))
                    self.assertEqual(kt["style"], editor.skin_topic_style(kt["text"], win["top"]))
                    self.assertEqual((kt["style"]["fill"], kt["style"]["weight"], kt["style"]["strokeW"]), ("#FFFFFF", "Black", 0))
                    kb = editor.text_box(kt["text"], kt["style"], W, H)
                    self.assertLessEqual(kb[3], win["top"] - editor.TXT_GAP + 1e-9)        # 영상 창 위 끝 - 틈 위
                    self.assertLess(abs((kb[1] + kb[3]) / 2 - 0.179), 0.006)                # 가운데 0.179 (창에 닿으면 조금 위로)
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
        self.assertEqual(editor.skin_topic_style("강원FC U12", 417 / 1920)["size"], 88)
        self.assertEqual(editor.skin_topic_style("강원FC U12", 417 / 1920)["y"], 0.2051)       # 0.179 + 반 줄이면 창 틈에 닿아 위로
        self.assertEqual(editor.skin_topic_style("강원FC U12", 0.3)["y"], round(0.179 + 1.25 * 88 / 2 / 1920, 4))
        long = editor.skin_topic_style("퍼스트 터치 레슨 · 포인트 3 · 디딤발 방향 정하기", 0.3)
        self.assertLess(long["size"], 88)
        self.assertEqual(editor.skin_cap_y(1465 / 1920), 0.723)
        for y0 in (0.15, 0.55, 0.85, 0.9):
            c = editor.skin_caption(dict(editor.SHORTS_STYLE, y=y0, fill="#1A1A1A", stroke="#FFFFFF", strokeW=3), 1465 / 1920)
            self.assertEqual((c["y"], c["fill"], c["stroke"], c["strokeW"]), (0.723, "#FFFFFF", "#000000", 8))
        self.assertEqual(editor.skin_caption(dict(editor.SHORTS_STYLE, strokeW=12), 0.8)["strokeW"], 12)
        self.assertEqual(editor.skin_title_style("선수 이름 맞추기")["size"], 96)
        self.assertEqual(editor.skin_title_style("퍼스트 터치 첫 번째 포인트")["size"], 80)
        st = editor.skin_title_style("가" * 40)
        self.assertEqual(st["size"], 48)
        g = editor.skin_box({"text": "가" * 40, "style": st}, 10)
        self.assertEqual((g["w"], g["x"]), (0.9, 0.05))                                       # 가장 넓게 90%


class MsgSkinTest(unittest.TestCase):
    """MSG 쇼츠(재미있게 자동 편집 · 끝까지 자동으로): 스킨 화면 구성 · 큰 제목·흰 상자 · 고정 글자 말고는 위 띠(0~창 위)·아래 띠(창 아래~끝)에 글자 없음."""

    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        style.set_shorts_layout("academy")
        style.set_card_extras(series=True, long_line=True)
        specs = [{"kind": "preset", "name": n} for n in msg.PRESETS]
        cls.qs = []
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
            self.assertEqual((q["captionStyle"]["y"], q["captionStyle"]["fill"], q["captionStyle"]["stroke"]), (0.723, "#FFFFFF", "#000000"))
            self.assertGreaterEqual(q["captionStyle"]["strokeW"], 8)
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
        looks = msg.text_looks({"captions": {}}, "shorts", 0.723, skin=win)
        for k in ("situ", "score"):
            b = msg.text_box("가나다", looks[k], W, H)
            self.assertAlmostEqual(b[1], (win["top"] + editor.TXT_GAP) * H, delta=2.5, msg=k)
        plain = msg.text_looks({"captions": {}}, "shorts", 0.723)
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
        shapes = editor.skin_card(top, kt, 2.5, win)
        cs = editor.skin_caption(dict(editor.SHORTS_STYLE), win["bottom"])
        q = editor._new_seq("쇼츠 스킨", "shorts", A + B + [v2], captionStyle=cs, layout=lay, titles=[top, kt], shapes=shapes)
        proj = dict(q, captions=[{"id": "c1", "start": 0.0, "end": 2.5, "text": "애들 목소리가 다 똑같아"}], info=info, source=name, media=[],
                    master={"volume": 1, "normalize": False, "lufs": -14})
        outs = editor.export(name, proj, {"preset": "youtube", "hw": False, "xml": True, "srt": False, "overlay": True}, lambda m: None)
        mp4 = core.OUT / next(o for o in outs if o.endswith(".mp4"))
        skin = np.asarray(Image.open(SKIN).convert("RGBA")).astype(int)
        sk_rgb, alpha = skin[..., :3], skin[..., 3]
        t1 = round(win["bottom"] * H)
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
            # 흰 상자(177~297)는 스킨 위 (상자 왼쪽 안 · 둥근 모서리·글자 없는 곳 · 그 자리 스킨은 칠판) · 큰 제목 주황 글씨
            bx0 = int(shapes[0]["x"] * W)
            box = (slice(190, 285), slice(bx0 + 8, bx0 + 22))
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
            self.assertTrue(len(cap) > 10 and cap.max() <= 0.723 * H + 10 and cap.min() > 0.6 * H, (what, cap.min() if len(cap) else None))
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
        # 지금 화면 그림으로 저장(프레임 PNG)도 같은 그리기
        fr = editor.export_frame(name, proj, 0.5)
        png = np.asarray(Image.open(core.OUT / fr["file"]).convert("RGB")).astype(int)
        d = np.abs(png[0:170] - sk_rgb[0:170])[alpha[0:170] == 255].mean()
        self.assertLess(d, 6)

    def test_filter_graph(self):
        """구간 필터: 스킨은 한 장 그림 입력(-loop 1 · image2) → 화면 크기(4K 도)로 · rgba 로 겹친 뒤 yuv420p · 그다음 자막(subs.ass) ·
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
            self.assertIn(f"scale={Ww}:{Hh}:flags=lanczos,format=rgba", fc)
            i_skin, i_sub = fc.index("format=rgba"), fc.index("subtitles=subs.ass")
            self.assertLess(i_skin, i_sub)                                                     # 스킨 → 자막 차례
            ov = [ln for ln in fc.split(";\n") if "overlay=x=0:y=0:format=auto,format=yuv420p" in ln]
            self.assertEqual(len(ov), 1)
        for other in (dict(lay, skin="skin_000000000000.png"), editor.BOX_LAYOUT, editor.EP_LAYOUT):
            args, fc = seg(other, 1080, 1920)
            self.assertEqual(args.count("-i"), 1, other)
            self.assertNotIn("format=rgba", fc)
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
    EXTRA = ("newPair", "fullSeq", "winClip", "fitRect", "SKIN", "SKX", "skinTitleStyle", "skinBoxFit", "skinTopicStyle", "skinCapY", "skinCard", "skinOn")

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
  S = rough(swap); S.captionStyle.y = 0.85; S.captionStyle.fill = "#1A1A1A"; S.captionStyle.strokeW = 3;
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
            cs, want_cs = r["captionStyle"], editor.skin_caption({"y": 0.85, "fill": "#1A1A1A", "strokeW": 3}, info["bottom"])
            for k2 in ("y", "fill", "stroke", "strokeW"):
                self.assertEqual(cs[k2], want_cs[k2], k2)
            self.assertEqual(cs["size"], 72)
            big = next(t for t in r["titles"] if t["id"] == "big")
            kt = next(t for t in r["titles"] if t["id"] == "k")
            self.assertNotIn("\n", big["text"])
            self.assertEqual((big["start"], big["dur"]), (0, 30))
            self.assertEqual(big["style"], editor.skin_title_style(big["text"]))
            self.assertEqual((kt["start"], kt["dur"], kt.get("swap"), kt["under"]), (0, 30, None, "big"))
            self.assertEqual(kt["style"], editor.skin_topic_style(kt["text"], info["top"]))
            [box] = r["shapes"]
            want = editor.skin_box(big, 30)
            for key in ("x", "y", "w", "h", "color", "opacity", "radius", "start", "dur", "full", "plan"):
                self.assertEqual(box[key], want[key], key)
            self.assertEqual(got["runs"][k + 1], {"n": 1})
            off = got["runs"][k + 2]
            self.assertNotIn("skin", off["layout"])                                            # 다른 템플릿: 스킨·흰 상자 지움
            self.assertEqual(off["shapes"], [])
            self.assertEqual(off["layout"]["zoom"], 1)
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
        self.assertIn("SKX = (EPX && EPX.skin) || null;", src)
        # 미리보기 차례: 스킨 그림(#skinL)은 영상 묶음(#layers · z 1) 다음 · 글자(#ov · z 60) 앞 · z 30
        self.assertIn('<div id="layers"></div><img id="skinL" alt="" draggable="false"><div class="ov" id="ov">', src)
        css = src[src.index("#skinL {"):].split("\n")[0]
        self.assertIn("z-index: 30", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn("skinShow();  // 풋살사관학교 스킨", src)


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
        self.assertIn("skin=editor.skin_info()", src)   # 편집실을 열 때 템플릿이 넣을 스킨 (epx.skin)
        ui = (ROOT / "ui.html").read_text(encoding="utf-8")
        self.assertIn('<option value="academy">풋살사관학교 스킨 (초록 칠판 · 내 채널 스킨)</option>', ui)
        self.assertEqual(style.SHORTS_LAYOUTS, ("", "eunpyeong", "academy"))


if __name__ == "__main__":
    unittest.main()
