"""은평 스타일 띠 그림·로고 (BR-280~BR-284 · D-470~D-477) — 저장소 폴더에서 python3 -m unittest tests.test_ep_pics

- 앱과 함께 싣는 그림(eunpyeong/band.png·logo.png · git 추적 = 배포 목록 · 업데이트 경로 규칙) · 출처 글
- 설정(styles/설정/은평 스타일 그림.json): 기본 둘 다 켬 · 켜기/끄기 · 깨진 파일 · '그림 바꾸기'(그림 확인 · 한글 폴더 · 내용 지문 이름 · 회전 정보 ·
  큰 그림 줄임 · 로고 투명 그대로) · 처음 그림으로 · 바꾼 그림 파일이 없어지면 실은 그림
- 그림 찾기(ep_pic_path · 이상한 이름 거절) · 바탕 그림(ep_bg_image: 띠 질감 · 영상 창 = 띠 색 · 로고 자리 · 깨진 그림 = 띠 색)
- 가편집·MSG 쇼츠: 화면 구성에 그림 이름 · 말 자막·전체 강좌 줄이 로고와 안 겹침 · 설정을 끄면 예전 EP_LAYOUT 그대로
- 그림이 없는 편집본은 내보내기 필터가 예전과 같음 · 실제 내보내기(720×1280): 띠 질감·로고·말 자막·강좌 줄·이름표 · 확대(창 자르기)·전환 · 깨진 그림
- 편집실(editor.html 함수를 node 로): 템플릿 '은평 스타일' = 서버와 같은 그림 이름·자막 한계·강좌 줄 자리 · 다른 템플릿은 지움 · fitRect
- 길: /api/style/card_pic(그림) · /api/style/card_pics · /api/style/card_pic(바꾸기) · /api/style/list · /api/edit/open epx.pics · 원격 길 표 · 프리미어 XML 안내"""
import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
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
from test_shorts_topic import FIX, WorkMixin, run_js, video_band  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
W, H = editor.frame_size("shorts")
FF = shutil.which("ffmpeg")
BLUE = (0x1F, 0x4F, 0xD1)


def png_bytes(size=(320, 64), mode="RGB", color=(200, 40, 40), fmt="PNG", exif_rot=False):
    from PIL import Image
    im = Image.new(mode, size, color)
    if mode == "RGB":   # 줄무늬 (질감)
        px = im.load()
        for x in range(0, size[0], 8):
            for y in range(size[1]):
                px[x, y] = (255, 255, 255)
    b = io.BytesIO()
    kw = {}
    if exif_rot:
        ex = Image.Exif()
        ex[0x0112] = 6   # 90° 돌려 보기
        kw["exif"] = ex
    im.save(b, fmt, **kw)
    return b.getvalue()


def data_url(raw, kind="png"):
    return f"data:image/{kind};base64," + base64.b64encode(raw).decode("ascii")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="은평 그림 [시험] "))
        self.s = mock.patch.object(style, "STYLES", self.tmp / "작업 폴더" / "styles")
        self.s.start()

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ShippedTest(unittest.TestCase):
    def test_files_ship_with_app(self):
        """기본 그림은 저장소에 추적됨 → release.sh 배포 목록(tests/ 빼고 추적 파일 전부)·업데이트 경로 규칙(updater._rel)을 지남 · 영문 이름 · 출처 글."""
        from PIL import Image
        for f, size, mode in (("band.png", (1600, 320), "RGB"), ("logo.png", (180, 254), "RGBA")):
            p = editor.EP_PICS / f
            self.assertTrue(p.is_file(), f)
            with Image.open(p) as im:
                self.assertEqual((im.size, im.mode), (size, mode), f)
            rel = f"eunpyeong/{f}"
            self.assertEqual(updater._rel(rel), rel)
            self.assertTrue(rel.isascii())
            self.assertEqual(style.CARD_PIC_SHIP[f[:-4]], f)
        notice = (editor.EP_PICS / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("소유자", notice)
        if (ROOT / ".git").exists() and shutil.which("git"):
            tracked = subprocess.run(["git", "ls-files", "eunpyeong"], cwd=ROOT, capture_output=True, text=True).stdout.split()
            if tracked:   # 커밋 전(작업 트리)에는 비어 있을 수 있음
                self.assertEqual(sorted(tracked), ["eunpyeong/NOTICE.md", "eunpyeong/band.png", "eunpyeong/logo.png"])
        rel = (ROOT / "release.sh").read_text(encoding="utf-8")
        self.assertIn('ls-files", "-s", "-z"', rel)        # 배포 목록 = 추적 파일 (경로 목록을 따로 두지 않음 · tests/ 만 뺌)
        self.assertNotIn("eunpyeong", rel)                  # 빼는 목록(tests/ · thumb_src/)에 없음


class SettingTest(Tmp):
    def test_defaults_on_and_toggle(self):
        self.assertEqual(style.card_pics(), {"band": True, "bandFile": "", "logo": True, "logoFile": ""})
        self.assertEqual(style.card_pic_refs(), {"band": "band.png", "logo": "logo.png"})
        self.assertEqual(editor.ep_layout(), dict(editor.EP_LAYOUT, band="band.png", logo="logo.png"))
        self.assertEqual(style.set_card_pics(band=False)["band"], False)
        self.assertTrue(style.card_pics()["logo"])          # 준 값만 바뀜
        self.assertEqual(style.card_pic_refs(), {"logo": "logo.png"})
        style.set_card_pics(logo=False)
        self.assertEqual(editor.ep_layout(), editor.EP_LAYOUT)   # 둘 다 끄면 예전 화면 구성 그대로 (띠 색)
        f = style._card_pic_file()
        for bad in ("{", "[1]", '{"band": "no", "logo": 0}'):
            f.write_text(bad, encoding="utf-8")
            self.assertEqual(style.card_pics(), {"band": True, "bandFile": "", "logo": True, "logoFile": ""}, bad)   # 못 읽으면 기본 (켬)
        f.write_text('{"band": true, "logo": true, "bandFile": "../../app.png", "logoFile": "logo_zzzzzzzzzzzz.png"}', encoding="utf-8")
        self.assertEqual(style.card_pic_refs(), {"band": "band.png", "logo": "logo.png"})   # 이상한 이름은 무시

    def test_pick_copy_validate(self):
        raw = png_bytes((800, 160))
        c = style.save_card_pic("band", data_url(raw))
        self.assertRegex(c["bandFile"], r"^band_[0-9a-f]{12}\.png$")
        p = style.card_pic_dir() / c["bandFile"]
        self.assertTrue(p.is_file())
        self.assertIn("은평 그림", str(p))                    # 작업 폴더 (한글·띄어쓰기·대괄호 경로)
        self.assertEqual(style.card_pic_refs()["band"], c["bandFile"])
        self.assertEqual(editor.ep_pic_path(c["bandFile"]), p)
        self.assertEqual(style.save_card_pic("band", raw)["bandFile"], c["bandFile"])   # 같은 그림 = 같은 이름 (바이트로 줘도)
        style.set_card_pics(band=False)
        self.assertTrue(style.save_card_pic("band", raw)["band"])                        # 그림을 고르면 켬
        # 로고: 투명 그대로 · JPG·회전 정보 · 큰 그림 줄임
        from PIL import Image
        lg = style.save_card_pic("logo", data_url(png_bytes((60, 90), "RGBA", (0, 0, 0, 0))))
        with Image.open(style.card_pic_dir() / lg["logoFile"]) as im:
            self.assertEqual(im.mode, "RGBA")
        jp = style.save_card_pic("band", data_url(png_bytes((100, 40), fmt="JPEG", exif_rot=True), "jpeg"))
        with Image.open(style.card_pic_dir() / jp["bandFile"]) as im:
            self.assertEqual((im.size, im.mode), ((40, 100), "RGB"))                 # 회전 정보를 실제로 적용 · PNG
        with mock.patch.object(style, "CARD_PIC_SIDE", 200):
            big = style.save_card_pic("band", png_bytes((1000, 100)))
        with Image.open(style.card_pic_dir() / big["bandFile"]) as im:
            self.assertEqual(im.size, (200, 20))
        # 처음 그림으로 · 바꾼 그림 파일이 없어지면 실은 그림 · 예전 그림 파일은 지우지 않음 (이미 만든 편집본이 가리킴)
        self.assertEqual(style.set_card_pics(reset="band")["bandFile"], "")
        self.assertTrue(p.is_file())
        style.save_card_pic("band", raw)
        p.unlink()
        self.assertEqual(style.card_pics()["bandFile"], "")
        self.assertIsNone(editor.ep_pic_path(c["bandFile"]))

    def test_bad_pictures(self):
        before = style.card_pics()
        for data in ("data:image/png;base64,QUJD", "hello", data_url(png_bytes()[:60]), data_url(b"GIF89a" + b"\0" * 40),
                     data_url(png_bytes((8, 8)))):
            with self.assertRaises(ValueError, msg=str(data)[:40]) as cm:
                style.save_card_pic("band", data)
            self.assertTrue(str(cm.exception).endswith("요"), str(cm.exception))   # 화면 문구 (쉬운 한국어)
        with self.assertRaises(ValueError):
            style.save_card_pic("other", data_url(png_bytes()))
        with mock.patch.object(style, "CARD_PIC_MAX", 100):
            with self.assertRaisesRegex(ValueError, "너무 커요"):
                style.save_card_pic("band", png_bytes())
        self.assertEqual(style.card_pics(), before)
        self.assertFalse(style.card_pic_dir().exists() and any(style.card_pic_dir().iterdir()))

    def test_pic_path_rejects_odd_names(self):
        self.assertEqual(editor.ep_pic_path("band.png"), editor.EP_PICS / "band.png")
        self.assertEqual(editor.ep_pic_path("logo.png"), editor.EP_PICS / "logo.png")
        for bad in (None, "", 3, "../band.png", "..\\band.png", "/etc/passwd", "NOTICE.md", "band", ".png", "band_000000000000.png",
                    "C:\\x\\band.png"):
            self.assertIsNone(editor.ep_pic_path(bad), bad)


class BgImageTest(unittest.TestCase):
    """바탕 그림 (editor.ep_bg_image · 내보내기가 쓰는 한 장)."""

    def fit(self, Ww=W, Hh=H):
        return editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080},
                           {"format": "shorts", "layout": dict(editor.EP_LAYOUT, band="band.png", logo="logo.png")}, Ww, Hh)

    def test_band_texture_window_logo(self):
        import numpy as np
        f = self.fit()
        self.assertEqual((f["band"], f["logo"]), ("band.png", "logo.png"))
        im = editor.ep_bg_image(W, H, f["by"], f["bh"], f["bar"], editor.ep_pic_path("band.png"), editor.ep_pic_path("logo.png"))
        a = np.asarray(im).astype(int)
        by, b2 = int(f["by"]), int(f["by"] + f["bh"])
        top, bot = a[:by], a[b2 + 1:int(0.8 * H)]
        for band in (top, bot):
            self.assertGreater(band.std(axis=(0, 1)).max(), 6, "질감 (한 색이 아님)")
            self.assertLess(abs(band[..., 2].mean() - band[..., 0].mean() - 70), 60, "파란 계열")
        self.assertTrue((a[by + 2:b2 - 2] == BLUE).all(), "영상 창 = 띠 색")
        x, y, w, h = editor.ep_logo_box(180, 254, W, H)
        self.assertAlmostEqual(h / H, 0.151, places=3)
        self.assertAlmostEqual((y + h / 2) / H, 0.892, places=3)
        self.assertAlmostEqual(x + w / 2, W / 2, places=3)
        plain = np.asarray(editor.ep_bg_image(W, H, f["by"], f["bh"], f["bar"], editor.ep_pic_path("band.png"), None)).astype(int)
        ys, xs = np.nonzero(np.abs(a - plain).max(axis=2) > 30)
        self.assertLessEqual(abs(ys.min() - y), 6)
        self.assertLessEqual(abs(ys.max() - (y + h)), 6)
        self.assertLessEqual(abs(xs.min() - x), 4)
        self.assertLessEqual(abs(xs.max() - (x + w)), 4)
        self.assertGreater(ys.min() / H, editor.EP_CAP_Y)          # 말 자막 띠(0.8) 아래
        self.assertLess(ys.max() / H, 0.97)                          # 맨 바닥에 붙지 않음

    def test_matches_agreed_mock(self):
        """합의한 모양(1080×1920): 위·아래 띠는 띠 그림을 띠마다 꽉 차게 가운데 기준 · 로고 높이 0.151 · 가운데 0.892."""
        f = self.fit()
        r = editor.ep_cover(1600, 320, W, f["by"])
        self.assertAlmostEqual(r[1], f["by"], places=6)              # 위 띠는 높이에 맞춤 (가로가 넘쳐 양옆을 자름)
        self.assertAlmostEqual(r[2], (W - r[0]) / 2, places=6)
        self.assertEqual(editor.ep_band_rects(f["by"], f["bh"], H), [(0.0, f["by"]), (f["by"] + f["bh"], float(H))])
        self.assertEqual(editor.ep_band_rects(0, H, H), [])          # 세로 영상이 화면을 다 채우면 띠 없음
        thin = editor.ep_cover(1600, 320, W, 100)                     # 띠가 그림보다 납작하면 가로에 맞추고 위아래를 자름
        self.assertAlmostEqual(thin[0], W, places=6)
        self.assertLess(thin[3], 0)

    def test_broken_or_missing(self):
        import numpy as np
        f = self.fit(720, 1280)
        bad = Path(tempfile.mkdtemp()) / "band_000000000000.png"
        bad.write_bytes(b"\x89PNG broken")
        logs = []
        im = editor.ep_bg_image(720, 1280, f["by"], f["bh"], "#1F4FD1", bad, editor.ep_pic_path("logo.png"), log=logs.append)
        a = np.asarray(im).astype(int)
        self.assertTrue((a[10:200, 10] == BLUE).all(), "깨진 띠 그림 = 띠 색")
        self.assertTrue(any("띠 그림" in m for m in logs))
        self.assertGreater(np.abs(a[1050:1200, 330:390] - BLUE).max(), 60, "로고는 그대로")
        self.assertIsNone(editor.ep_bg_image(720, 1280, f["by"], f["bh"], "#1F4FD1", bad, bad, log=logs.append))
        self.assertIsNone(editor.ep_bg_image(720, 1280, f["by"], f["bh"], "#1F4FD1", None, None))
        shutil.rmtree(bad.parent, ignore_errors=True)
        with tempfile.TemporaryDirectory() as t:   # 그림 이름이 있지만 파일이 없으면 바탕 그림 없음 (예전 색 바탕)
            self.assertIsNone(editor._ep_bg_file(dict(f, band="band_111111111111.png", logo=None), 720, 1280, t))
            p = editor._ep_bg_file(f, 720, 1280, t)
            self.assertTrue(p.is_file() and p.name.startswith("epbg_") and p.name.isascii())
            self.assertEqual(editor._ep_bg_file(f, 720, 1280, t), p)   # 같은 배치는 한 번만


class LayoutPlaceTest(unittest.TestCase):
    def test_logo_and_text_rows(self):
        """로고 자리(0.8165~0.9675) · 로고가 있으면 강좌 줄은 로고 바로 위 · 말 자막은 그 위 (모두 틈 TXT_GAP 이상 · 안 겹침)."""
        self.assertAlmostEqual(editor.EP_LOGO_TOP, 0.8165, places=6)
        self.assertEqual(editor.EP_LINK_LOGO_Y, __import__("math").floor((editor.EP_LOGO_TOP - editor.TXT_GAP) * 1000 + 1e-6) / 1000)
        logo_only = editor.ep_cap_lim(None, True)
        self.assertEqual(logo_only, 0.804)
        ln = editor.ep_longline(10, logo=True)
        self.assertEqual(ln["style"]["y"], editor.EP_LINK_LOGO_Y)
        self.assertEqual(editor.ep_longline(10)["style"], editor.EP_LINK)       # 로고가 없으면 예전 자리 0.865
        lb = editor.text_box(ln["text"], ln["style"], W, H)
        self.assertLessEqual(lb[3] + editor.TXT_GAP, editor.EP_LOGO_TOP + 1e-9)
        lim = editor.ep_cap_lim(ln, True)
        self.assertEqual(lim, 0.768)
        for y0, line, logo, want in ((0.8, None, True, 0.8), (0.85, None, True, 0.804), (0.9, ln, True, 0.768), (0.8, ln, True, 0.768),
                                     (0.85, editor.ep_longline(3), False, 0.829), (0.85, None, False, 0.85)):
            cs = editor.ep_caption(dict(editor.SHORTS_STYLE, y=y0))
            got = editor.ep_cap_clear(cs, [line] if line else [], logo)["y"]
            self.assertEqual(got, want, (y0, bool(line), logo))
            cb = editor._cap_band(cs, H, 2)
            if logo:
                self.assertLessEqual(cb[1] + editor.TXT_GAP, editor.EP_LOGO_TOP + 1e-9)
            if line:
                self.assertLessEqual(cb[1] + editor.TXT_GAP, editor.text_box(line["text"], line["style"], W, H)[1] + 1e-9)
        cs = editor.ep_cap_clear(editor.ep_caption(dict(editor.SHORTS_STYLE)), [ln], True)
        self.assertGreaterEqual(editor._cap_band(cs, H)[0], video_band(editor.EP_LAYOUT)[1])   # 한 줄 자막은 아직 영상 창 아래 (아래 띠)


class RoughPicsTest(WorkMixin, unittest.TestCase):
    INFO = {"duration": FIX["MSGRAW01"]["duration"], "width": 1920, "height": 1080, "fps": 30.0}

    def test_rough_shorts_get_pics(self):
        name = self.fix("MSGRAW01")
        style.set_shorts_layout("eunpyeong")
        style.set_card_extras(series=True, long_line=True)
        qs = editor.auto_sequences(name, self.INFO, {"captionPos": "bottom"}, ("shorts",))
        self.assertTrue(qs)
        for q in qs:
            self.assertEqual((q["layout"]["band"], q["layout"]["logo"]), ("band.png", "logo.png"))
            [line] = [t for t in q["titles"] if t.get("plan") == "longline"]
            self.assertEqual(line["style"]["y"], editor.EP_LINK_LOGO_Y)
            self.assertEqual(q["captionStyle"]["y"], 0.768)
            cb = editor._cap_band(q["captionStyle"], H)
            lb = editor.text_box(line["text"], line["style"], W, H)
            self.assertLessEqual(cb[1] + editor.TXT_GAP, lb[1] + 1e-9)
            self.assertLessEqual(lb[3] + editor.TXT_GAP, editor.EP_LOGO_TOP + 1e-9)
        style.set_card_pics(band=False, logo=False)    # 끄면 띠 색 · 강좌 줄·자막은 예전 자리 (D-428)
        for q in editor.auto_sequences(name, self.INFO, {"captionPos": "bottom"}, ("shorts",)):
            self.assertEqual(q["layout"], editor.EP_LAYOUT)
            self.assertEqual([t for t in q["titles"] if t.get("plan") == "longline"][0]["style"], editor.EP_LINK)
            self.assertEqual(q["captionStyle"]["y"], 0.829)
        style.set_card_pics(band=True)
        for q in editor.auto_sequences(name, self.INFO, None, ("shorts",)):
            self.assertEqual(q["layout"], dict(editor.EP_LAYOUT, band="band.png"))
        style.set_shorts_layout("")                    # 은평 스타일이 아니면 그림 없음
        for q in editor.auto_sequences(name, self.INFO, None, ("shorts",)):
            self.assertNotIn("band", q["layout"])
            self.assertNotIn("logo", q["layout"])


class MsgPicsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        style.set_shorts_layout("eunpyeong")
        style.set_card_extras(series=True, long_line=True)
        specs = [{"kind": "preset", "name": n} for n in msg.PRESETS]
        cls.qs = msg.build_variants(cls.w.name, specs[:3], "듬뿍", ("shorts",), lambda m: None)["sequences"]

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_msg_shorts_same_look(self):
        self.assertTrue(self.qs)
        for q in self.qs:
            self.assertEqual(q["layout"], editor.ep_layout(), q["name"])
            self.assertEqual((q["layout"]["band"], q["layout"]["logo"]), ("band.png", "logo.png"))
            [line] = [t for t in q["titles"] if t.get("plan") == "longline"]
            self.assertEqual(line["style"]["y"], editor.EP_LINK_LOGO_Y)
            self.assertLessEqual(q["captionStyle"]["y"], editor.ep_cap_lim(line, True))
            cb = editor._cap_band(q["captionStyle"], H, 2)
            self.assertLessEqual(cb[1] + editor.TXT_GAP, editor.text_box(line["text"], line["style"], W, H)[1] + 1e-9)
            for t in q["titles"]:   # 어떤 글자도 로고 자리(가로 가운데 · 0.8165~0.9675)에 안 들어감
                b = msg.text_box(t["text"], t["style"], W, H)
                x, y, w, h = editor.ep_logo_box(180, 254, W, H)
                self.assertFalse(b[0] < x + w and x < b[2] and b[1] < y + h and y < b[3], (q["name"], t["text"], b))


class SameWhenUnusedTest(unittest.TestCase):
    """그림 이름이 없는 화면 구성(예전 편집본·다른 템플릿·설정을 끈 은평 스타일)은 _fit 값·내보내기 필터가 예전과 같음 (색 바탕 · 입력 하나)."""

    def segment(self, layout):
        md = {"main": {"id": "main", "kind": "video", "src": "videos", "file": "x.mp4", "dur": 10, "w": 1920, "h": 1080, "fps": 30.0}}
        v, a = editor._pair(0, 2, start=0)
        v["fx"] = {"scale": {"v": 140, "k": []}}
        q = editor._new_seq("쇼츠", "shorts", [v, a], layout=layout, captionStyle=dict(editor.SHORTS_STYLE))
        with tempfile.TemporaryDirectory() as t, mock.patch.object(editor, "media_path", lambda f, s: Path(t) / "x.mp4"):
            (Path(t) / "x.mp4").write_bytes(b"0")
            args, _final, _n = editor._build_segment(q, md, 720, 1280, 30, 0, 30, [], Path(t), 0)
            return args, (Path(t) / "fc0.txt").read_text(encoding="utf-8")

    def test_no_pics_keeps_filter(self):
        old_keys = {"chain", "bw", "bh", "bx", "by", "bg", "bar", "win"}
        for lay in (editor.EP_LAYOUT, editor.BOX_LAYOUT, msg.seq_layout("shorts"), {"mode": "fill"}):
            f = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080}, {"format": "shorts", "layout": lay}, 720, 1280)
            self.assertLessEqual(set(f), old_keys, lay)
            args, fc = self.segment(lay)
            self.assertEqual(args.count("-i"), 1, lay)
            self.assertNotIn("epbg_", fc)
        args, fc = self.segment(dict(editor.EP_LAYOUT, band="band.png", logo="logo.png"))
        self.assertEqual(args.count("-i"), 2)
        self.assertIn("epbg_", " ".join(args))
        self.assertNotIn("color=c=0x1F4FD1:s=720x1280", fc)   # 바탕은 그림 (창 자르기 판은 그대로 띠 색)
        self.assertIn("color=c=0x1F4FD1:s=720x", fc)


@unittest.skipUnless(FF, "ffmpeg 가 없어요")
class ExportPicsTest(WorkMixin, unittest.TestCase):
    """실제 내보내기 (720×1280): 띠 질감 · 영상 창 · 로고 자리 · 말 자막·강좌 줄은 로고 위 · 이름표 · 확대(창 자르기)·디졸브 전환 · 깨진 띠 그림 = 띠 색."""
    D = 4.0

    def frame(self, out, t):
        import numpy as np
        raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(core.OUT / out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1280, 720, 3).astype(int)

    def make(self, name, layout, label):
        d = self.D
        A, B = editor._pair(0.0, 2.0, start=0.0), editor._pair(2.0, 4.0, start=2.0)
        B[0]["fx"] = {"scale": {"v": 160, "k": []}}
        top = {"id": "t", "text": "트래핑 꿀팁", "start": 0.0, "dur": d, "style": dict(editor.TITLE_STYLE)}
        kt = {"id": "k", "text": "퍼스트 터치 레슨 · 포인트 3", "plan": "topic"}
        shapes = editor.ep_card(top, kt, d)
        logo = bool(layout.get("logo"))
        xt, xg = editor.ep_extras(d, logo=logo)
        cs = editor.ep_cap_clear(editor.ep_caption(dict(editor.SHORTS_STYLE)), xt, logo)
        q = editor._new_seq(label, "shorts", A + B, captionStyle=cs, layout=layout, titles=[top, kt] + xt, shapes=shapes + xg,
                            trans=[{"id": "x", "track": "V1", "a": A[0]["id"], "b": B[0]["id"], "type": "dissolve", "dur": 0.6, "align": "center"}])
        caps = [{"id": "c1", "start": 0.0, "end": d, "text": "공을 멈추는 게 아니라"}]
        proj = dict(q, captions=caps, info=self.info, source=name, media=[], master={"volume": 1, "normalize": False, "lufs": -14})
        return editor.export(name, proj, {"preset": "small", "hw": False, "xml": True, "srt": False}, lambda m: None), q

    def test_render(self):
        import numpy as np
        name = self.fix("MSGRAW01")
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s=640x360:r=30:d={self.D}", "-f", "lavfi", "-i",
                            "anullsrc=r=48000:cl=stereo", "-t", str(self.D), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)],
                           capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.info = {"duration": self.D, "width": 640, "height": 360, "fps": 30.0}
        style.set_card_extras(series=True, long_line=True)
        Hh, Ww = 1280, 720
        on, q = self.make(name, editor.ep_layout(), "쇼츠 그림")
        style.set_card_pics(band=False, logo=False)
        off, _ = self.make(name, editor.ep_layout(), "쇼츠 색")
        fit = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 640, "h": 360}, {"format": "shorts", "layout": editor.EP_LAYOUT}, Ww, Hh)
        wy0, wy1 = int(fit["by"]) & ~1, (int(fit["by"]) & ~1) + fit["bh"]
        ref = np.asarray(editor.ep_bg_image(Ww, Hh, fit["by"], fit["bh"], fit["bar"], editor.ep_pic_path("band.png"), editor.ep_pic_path("logo.png"))).astype(int)
        lx, ly, lw, lh = editor.ep_logo_box(180, 254, Ww, Hh)
        for t in (1.0, 2.0, 3.3):   # 그냥 · 디졸브 가운데 · 160% 확대
            a, b = self.frame(on[0], t), self.frame(off[0], t)
            # 띠: 질감(가장자리 세로줄 · 글자 없는 곳) · 바탕 그림과 같음 / 끄면 한 색 파랑
            for rows in ((0.20, 0.27), (0.71, 0.77)):
                y0, y1 = int(rows[0] * Hh), int(rows[1] * Hh)
                strip_on, strip_off = a[y0:y1, 4:60], b[y0:y1, 4:60]
                self.assertGreater(strip_on.std(axis=(0, 1)).max(), 4, (t, rows))
                self.assertLess(strip_off.std(axis=(0, 1)).max(), 2.5, (t, rows))
                self.assertLess(np.abs(strip_on - ref[y0:y1, 4:60]).mean(), 6, (t, rows))
                self.assertTrue(all(abs(strip_off[..., c].mean() - BLUE[c]) < 8 for c in range(3)))
            # 영상 창은 회색 그대로 (확대해도 창 안 · 띠로 안 넘침)
            for x in (0.02, 0.98):   # (글자 없는 세로줄)
                col = a[:, int(x * Ww)]
                gray = np.where((abs(col - 128) < 20).all(axis=1))[0]
                self.assertGreater(len(gray), 300, (t, x))
                self.assertGreaterEqual(gray.min(), wy0 - 1, (t, x))
                self.assertLessEqual(gray.max(), wy1, (t, x))
            # 로고 자리 = 바탕 그림의 로고 (말 자막·글자 아래 · 영상이 덮지 않음)
            box = (slice(int(ly) + 2, int(ly + lh) - 2), slice(int(lx) + 2, int(lx + lw) - 2))
            self.assertLess(np.abs(a[box] - ref[box]).mean(), 8, t)
            self.assertGreater(np.abs(a[box] - b[box]).mean(), 30, t)
            # 말 자막·강좌 줄 흰 글씨는 로고 위 (로고 바깥 세로줄에서 셈)
            side = np.concatenate([a[int(0.70 * Hh):, :int(lx) - 4], a[int(0.70 * Hh):, int(lx + lw) + 4:]], axis=1)
            wrows = np.where((side.min(axis=2) > 225).sum(axis=1) > 3)[0] + int(0.70 * Hh)
            self.assertGreater(len(wrows), 10, t)
            self.assertLess(wrows.max(), ly, t)
            self.assertGreater(wrows.min(), wy1, t)
            groups = np.split(wrows, np.where(np.diff(wrows) > 3)[0] + 1)
            self.assertGreaterEqual(len(groups), 2, (t, [(g.min(), g.max()) for g in groups]))   # 말 자막 · 강좌 줄 (안 겹침)
            # 이름표 검은 상자는 그대로 (띠 그림 위)
            tag = a[int(0.056 * Hh):int(0.084 * Hh), int(0.45 * Ww):int(0.55 * Ww)]
            self.assertTrue((tag.max(axis=2) < 40).mean() > 0.3 or (tag.min(axis=2) > 215).mean() > 0.1, t)
        xml = sorted(core.OUT.rglob("*.txt"))
        notes = "\n".join(p.read_text(encoding="utf-8") for p in xml)
        self.assertIn("띠 그림·로고가 빠져요", notes)
        # 깨진 띠 그림 (작업 폴더의 바꾼 그림이 망가짐) → 띠 색 · 로고는 그대로 · 내보내기는 끝남
        style.set_card_pics(band=True, logo=True)
        c = style.save_card_pic("band", png_bytes((400, 80)))
        (style.card_pic_dir() / c["bandFile"]).write_bytes(b"\x89PNG broken")
        broken, _ = self.make(name, editor.ep_layout(), "쇼츠 깨짐")
        a = self.frame(broken[0], 1.0)
        strip = a[int(0.20 * Hh):int(0.27 * Hh), 4:60]
        self.assertLess(strip.std(axis=(0, 1)).max(), 2.5)
        self.assertTrue(all(abs(strip[..., k].mean() - BLUE[k]) < 8 for k in range(3)))
        box = (slice(int(ly) + 2, int(ly + lh) - 2), slice(int(lx) + 2, int(lx + lw) - 2))
        self.assertGreater(np.abs(a[box] - np.array(BLUE)).mean(), 30)


@unittest.skipUnless(shutil.which("node"), "node 가 없어요")
class EditorPicsTest(unittest.TestCase):
    """editor.html 템플릿(epCard·epExtras·epCapClear·epOff)·fitRect 를 node 로 그대로 → 서버와 같은 값."""

    def test_template_same_as_server(self):
        from test_eunpyeong import EditorTemplateTest
        got = run_js(EditorTemplateTest.SHORT + """
const cp = x => JSON.parse(JSON.stringify(x));
out = [];
EPX = { series: true, seriesName: "1분 레슨", longLine: true, next: 3, pics: { band: "band.png", logo: "logo.png" } };
S = rough(false); S.captionStyle.y = 0.85; TEMPLATES["은평 스타일"](); TEMPLATES["은평 스타일"](); out.push(cp(S));
TEMPLATES["위아래 검정 박스"](); out.push(cp(S));
EPX = { series: false, seriesName: "1분 레슨", longLine: false, next: 3, pics: { logo: "logo_0123456789ab.png" } };
S = rough(false); S.captionStyle.y = 0.9; TEMPLATES["은평 스타일"](); out.push(cp(S));
EPX = { series: false, seriesName: "1분 레슨", longLine: true, next: 3, pics: {} };
TEMPLATES["은평 스타일"](); out.push(cp(S));   // 로고를 끄고 다시: 그림 이름을 지우고 강좌 줄은 예전 자리
EPX = null; S = rough(false); TEMPLATES["은평 스타일"](); out.push(cp(S));""")
        on, other, logo_only, again, none = got
        self.assertEqual((on["layout"]["band"], on["layout"]["logo"]), ("band.png", "logo.png"))
        [line] = [t for t in on["titles"] if t.get("plan") == "longline"]
        self.assertEqual(line["style"], editor.ep_longline(3, logo=True)["style"])
        self.assertEqual(on["captionStyle"]["y"], editor.ep_cap_clear({"y": 0.85}, [editor.ep_longline(3, True)], True)["y"])
        self.assertEqual(on["captionStyle"]["y"], 0.768)
        self.assertNotIn("band", other["layout"])
        self.assertNotIn("logo", other["layout"])
        self.assertEqual((logo_only["layout"].get("band"), logo_only["layout"]["logo"]), (None, "logo_0123456789ab.png"))
        self.assertEqual(logo_only["captionStyle"]["y"], editor.ep_cap_lim(None, True))
        self.assertNotIn("logo", again["layout"])
        self.assertEqual([t for t in again["titles"] if t.get("plan") == "longline"][0]["style"]["y"], editor.EP_LINK["y"])
        self.assertNotIn("band", none["layout"])
        for k, v in editor.EP_LAYOUT.items():
            self.assertEqual(none["layout"][k], v, k)

    def test_fit_rect_carries_pics(self):
        from test_eunpyeong import run_js2
        lay = dict(editor.EP_LAYOUT, band="band.png", logo="logo.png")
        got = run_js2(f"""
const md = {{ kind: "video", w: 1920, h: 1080 }}, it = {{ track: "V1", fit: "auto" }};
S = {{ format: "shorts", layout: {json.dumps(lay)} }}; out = {{ on: fitRect(it, md) }};
S = {{ format: "shorts", layout: {json.dumps(editor.EP_LAYOUT)} }}; out.off = fitRect(it, md);
S = {{ format: "shorts", layout: {{ ...{json.dumps(lay)}, mode: "blur" }} }}; out.blur = fitRect(it, md);""")
        py = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": 1920, "h": 1080}, {"format": "shorts", "layout": lay}, 1080, 1920)
        self.assertEqual((got["on"]["band"], got["on"]["logo"]), (py["band"], py["logo"]))
        self.assertAlmostEqual(got["on"]["by"], py["by"], delta=1)
        self.assertNotIn("band", got["off"])
        self.assertNotIn("band", got["blur"])
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        body = src[src.index("function epPics("):]
        body = body[:body.index("\n}\n")]
        for k in ("EP.LOGO.h * H", "EP.LOGO.cy * H - h / 2", "f.by + f.bh", "backgroundImage"):
            self.assertIn(k, body)
        css = src[src.index(".lay .lbp i { top"):].split("\n")[0]
        self.assertIn("background-size: cover", css)
        self.assertIn("background-position: center", css)
        self.assertIn("epPics(e.bgd, f, k, W, H)", src)


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
            st, ct, body = get("/api/style/card_pic?f=band.png")
            self.assertEqual((st, ct, body[:4]), (200, "image/png", b"\x89PNG"))
            self.assertEqual(body, (editor.EP_PICS / "band.png").read_bytes())
            for bad in ("../app.py", "..%2Fapp.py", "NOTICE.md", "", "band_000000000000.png"):
                self.assertEqual(get("/api/style/card_pic?f=" + bad)[0], 404, bad)
            self.assertEqual(t.call("/api/style/list")[1]["cardPics"], {"band": True, "bandFile": "", "logo": True, "logoFile": ""})
            self.assertEqual(t.call("/api/style/card_pics", {"logo": False, "band": "x"}),
                             (200, {"ok": True, "band": True, "bandFile": "", "logo": False, "logoFile": ""}))
            st, j = t.call("/api/style/card_pic", {"kind": "logo", "data": data_url(png_bytes((90, 120), "RGBA", (10, 20, 30, 255)))})
            self.assertEqual((st, j["ok"], j["logo"]), (200, True, True))
            st2, ct2, body2 = get("/api/style/card_pic?f=" + j["logoFile"])
            self.assertEqual((st2, ct2), (200, "image/png"))
            st, j2 = t.call("/api/style/card_pic", {"kind": "logo", "data": "data:image/png;base64,AAAA"})
            self.assertEqual(st, 400)
            self.assertIn("그림", j2["error"])
            self.assertEqual(t.call("/api/style/card_pics", {"reset": "logo"})[1]["logoFile"], "")
            self.assertEqual(t.call("/api/style/card_pics", {"reset": "../x"})[0], 200)   # 모르는 값은 안 바꿈
        finally:
            t.doCleanups()
        import rstudio
        self.assertEqual(rstudio.classify("GET", "/api/style/card_pic"), rstudio.READ)
        self.assertEqual(rstudio.classify("POST", "/api/style/card_pic"), rstudio.WRITE)
        self.assertEqual(rstudio.classify("POST", "/api/style/card_pics"), rstudio.WRITE)
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("pics=style.card_pic_refs()", src)   # 편집실을 열 때 템플릿이 넣을 그림 (epx.pics)


if __name__ == "__main__":
    unittest.main()
