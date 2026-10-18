"""은평 스타일 KFA 글꼴 고정 (BR-310~BR-313 · D-500~D-505) — 저장소 폴더에서 python3 -m unittest tests.test_ep_font

KFA 글꼴은 저장소에 없으므로 대신할 글꼴로 시험한다: 앱 글꼴 fonts/DoHyeon-Regular.ttf(Pretendard 보다 좁은 한글 글꼴) ·
그 이름 표를 'KFA고딕 테스트'(한국어 이름만)로 바꾼 사본(깔린 글꼴 찾기) · 그대로 'KFA고딕 테스트.ttf' 로 이름만 바꾼 사본(고른 파일 · 이름은 안 따짐).
- 글꼴 파일 읽기(epfont.faces · 앱 글꼴의 줄 높이 비율 = editor.FONT_K·TITLE_FONTS · TTC · 글꼴이 아닌 것 거절) · 글자 폭 표(epfont.metrics · 한글 없는 글꼴 거절)
- 찾기(epfont.find): 고른 파일이 먼저 · 깔린 글꼴은 이름 표의 'KFA' (파일 이름이 달라도) · 여럿이면 가장 굵은 것 · 고른 파일이 깨지면 깔린 것 ·
  기억해 두고 폴더가 바뀌면 다시 · 다른 스레드가 찾는 중이면 화면은 기다리지 않음 · 스타일 화면 줄 상태
- '글꼴 파일 고르기'(style.save_card_font: data URL·한글 파일 이름·내용 지문 이름·크기 한도·글꼴 아님·한글 없음) · '처음으로'
- 내보내기 ASS(build_ass): 은평 스타일 화면의 모든 글자 = 그 글꼴 이름·줄 높이 비율 · 배운 스타일·MSG 강조 글꼴(style.font)보다 먼저 ·
  찾지 못했거나 다른 화면은 예전과 같은 바이트 · 큰 제목·이름표·말 자막 맞춤이 그 글꼴 폭(진짜 글꼴로 잰 폭 안) · 내보내기 글꼴 폴더에 글꼴 파일
- MSG 은평 스타일 쇼츠(시험 원본): 큰 제목이 그 글꼴로 맞춘 크기 · 글자 자리 셈이 그 글꼴 · ASS 는 모두 그 글꼴
- 실제 1080×1920 내보내기 장면(editor.export_frame): libass 가 모든 글자에 그 글꼴 파일을 고름(fontselect 기록) · 글자 자리마다 예전 글꼴과 다르게 그려짐
- 편집실 미리보기(editor.html 함수를 node 로): 같은 폭 표로 큰 제목 크기·이름표 폭·자막 맞춤·글 상자 = 서버 · 다른 화면은 그대로 · 글꼴 줄 문구
- 길: /api/style/card_font(줄 · 고르기 · 처음으로) · /api/style/ep_font(파일) · /api/style/list cardFont · /api/edit/open epx.font · 원격 길 표"""
import base64
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import epfont  # noqa: E402
import msg  # noqa: E402
import style  # noqa: E402
from test_shorts_topic import JS_DEFS, WorkMixin, js_defs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
W, H = editor.frame_size("shorts")
FF = shutil.which("ffmpeg")
NODE = shutil.which("node")
DOHYEON = editor.FONTS / "DoHyeon-Regular.ttf"
FAMILY = "KFA고딕 테스트"


def renamed(src, family=FAMILY, weight=None, lang=0x412, ps="KFATest-Regular", full=None):
    """글꼴 바이트의 이름 표를 바꿈 (Windows 플랫폼 · 한국어 이름만 · 새 이름 표를 끝에 붙이고 표 목록이 가리키게) · weight: OS/2 굵기 ·
    ps: PostScript 이름 · full: 전체 이름 (없으면 글꼴 이름)."""
    b = bytearray(Path(src).read_bytes() if not isinstance(src, (bytes, bytearray)) else src)
    n = struct.unpack_from(">H", b, 4)[0]
    rows, strs = [], b""
    for txt, nid in ((family, 1), ("Regular", 2), (full or family, 4), (ps, 6)):
        s = txt.encode("utf-16-be")
        rows.append(struct.pack(">6H", 3, 1, 0x409 if nid == 6 else lang, nid, len(s), len(strs)))
        strs += s
    tab = struct.pack(">3H", 0, len(rows), 6 + 12 * len(rows)) + b"".join(rows) + strs
    while len(b) % 4:
        b.append(0)
    off = len(b)
    b += tab
    for i in range(n):
        o = 12 + 16 * i
        tag = bytes(b[o:o + 4])
        if tag == b"name":
            struct.pack_into(">II", b, o + 8, off, len(tab))
        if tag == b"OS/2" and weight:
            struct.pack_into(">H", b, struct.unpack_from(">I", b, o + 8)[0] + 4, weight)
    return bytes(b)


def ttc(fonts):
    """글꼴 바이트 여럿 → TTC 묶음 (글꼴마다 표 위치를 파일 처음 기준으로 옮김)."""
    head = 12 + 4 * len(fonts)
    head += (-head) % 4
    offs, body = [], b""
    for f in fonts:
        f = bytearray(f)
        o = head + len(body)
        for i in range(struct.unpack_from(">H", f, 4)[0]):
            r = 12 + 16 * i
            struct.pack_into(">I", f, r + 8, struct.unpack_from(">I", f, r + 8)[0] + o)
        offs.append(o)
        body += bytes(f) + b"\0" * ((-len(f)) % 4)
    hdr = b"ttcf" + struct.pack(">HHI", 1, 0, len(fonts)) + b"".join(struct.pack(">I", o) for o in offs)
    return hdr + b"\0" * (head - len(hdr)) + body


def data_url(raw):
    return "data:font/ttf;base64," + base64.b64encode(raw).decode("ascii")


def no_hangul_font():
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if Path(p).is_file():
            return Path(p)
    return None


class FontEnv:
    """작업 폴더(styles) + 가짜 Windows 글꼴 폴더 두 곳 (한글·띄어쓰기 이름) · 찾기 기억은 시험마다 비움."""

    def setUp(self):
        super().setUp()
        self.ftmp = Path(tempfile.mkdtemp(prefix="은평 글꼴 [시험] "))
        self.fdirs = [self.ftmp / "Windows" / "Fonts", self.ftmp / "사용자 글꼴"]
        for d in self.fdirs:
            d.mkdir(parents=True)
        self.patches = [mock.patch.object(epfont, "FONT_DIRS", self.fdirs)]
        if not isinstance(self, WorkMixin):
            self.patches.append(mock.patch.object(style, "STYLES", self.ftmp / "작업 폴더" / "styles"))
        for p in self.patches:
            p.start()
        epfont.forget()

    def tearDown(self):
        epfont.forget()
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.ftmp, ignore_errors=True)
        super().tearDown()

    def install(self, name="KFA고딕.ttf", d=0, **kw):
        p = self.fdirs[d] / name
        p.write_bytes(renamed(DOHYEON, **kw))
        epfont.forget()
        return p

    def pick(self, name="KFA고딕 테스트.ttf"):
        """대신할 글꼴을 '글꼴 파일 고르기'로 고름 (도현 그대로 · 파일 이름만 KFA)."""
        return style.save_card_font(data_url(DOHYEON.read_bytes()), name)


# ---------- 글꼴 파일 읽기 ----------

class ParseTest(unittest.TestCase):
    def test_bundled_fonts(self):
        """앱 글꼴: 이름·굵기 · 줄 높이 비율 = libass 로 재어 둔 값 (editor.FONT_K · TITLE_FONTS)."""
        want = {"Pretendard-Bold.otf": ("Pretendard", 700, editor.FONT_K), "Pretendard-Black.otf": ("Pretendard Black", 900, editor.FONT_K),
                "BlackHanSans-Regular.ttf": ("Black Han Sans", 400, editor.TITLE_FONTS["Black Han Sans"][1]),
                "DoHyeon-Regular.ttf": ("Do Hyeon", 400, editor.TITLE_FONTS["Do Hyeon"][1])}
        for f, (fam, wt, k) in want.items():
            [x] = epfont.faces(editor.FONTS / f)
            self.assertEqual((x["family"], x["weight"], x["index"]), (fam, wt, 0), f)
            self.assertAlmostEqual(x["k"], k, delta=0.002, msg=f)
            self.assertFalse(epfont.has_kfa(x))
        self.assertEqual(epfont.faces(DOHYEON), epfont.faces(DOHYEON.read_bytes()))   # 경로·바이트 같음

    def test_korean_name_and_weight(self):
        [x] = epfont.faces(renamed(DOHYEON, weight=800))
        self.assertEqual((x["family"], x["full"], x["weight"]), (FAMILY, FAMILY, 800))
        self.assertTrue(epfont.has_kfa(x))
        [y] = epfont.faces(renamed(DOHYEON, "Kfa Sans", lang=0x409))
        self.assertTrue(epfont.has_kfa(y))   # 대소문자 상관없이

    def test_ttc(self):
        raw = ttc([DOHYEON.read_bytes(), renamed(DOHYEON, weight=700)])
        self.assertEqual(epfont.ext_of(raw), ".ttc")
        fs = epfont.faces(raw)
        self.assertEqual([(x["index"], x["family"]) for x in fs], [(0, "Do Hyeon"), (1, FAMILY)])
        self.assertEqual(epfont._best(fs, want_kfa=True)[0]["index"], 1)
        self.assertEqual([x["index"] for x in epfont._best(fs)], [1, 0])   # KFA 이름이 먼저
        self.assertEqual(epfont.metrics(raw, 1), epfont.metrics(DOHYEON))   # PIL 이 묶음 속 글꼴을 읽음

    def test_single_face(self):
        """TTC 묶음에서 글꼴 하나만 떼어 냄 (편집실 미리보기 · 브라우저는 묶음의 첫 글꼴만 그림) — 그 글꼴 하나 · 이름·굵기·폭 표가 묶음 속 그 글꼴과 같음."""
        raw = ttc([DOHYEON.read_bytes(), renamed(DOHYEON, weight=700)])
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "묶음.ttc"
            p.write_bytes(raw)
            one, ext = epfont.single_face(p, 1)
            self.assertEqual(ext, ".ttf")
            self.assertEqual(one[:4], b"\x00\x01\x00\x00")
            [x] = epfont.faces(one)
            self.assertEqual((x["index"], x["family"], x["weight"]), (0, FAMILY, 700))
            self.assertEqual(epfont.metrics(one), epfont.metrics(raw, 1))
            [y] = epfont.faces(epfont.single_face(p, 0)[0])
            self.assertEqual((y["family"], y["weight"]), ("Do Hyeon", 400))
            from PIL import ImageFont
            import io
            self.assertEqual(ImageFont.truetype(io.BytesIO(one), 40).getname(), ImageFont.truetype(io.BytesIO(raw), 40, index=1).getname())   # FreeType 도 같은 글꼴
            for bad in ((p, 2), (DOHYEON, 0)):
                with self.assertRaises(ValueError):
                    epfont.single_face(*bad)

    def test_ass_name(self):
        """ASS 'Style:' 줄은 쉼표로 칸을 나눔 — 쉼표·제어 글자가 든 글꼴 이름은 안 씀 (전체 이름 → PostScript 이름 → FreeType 이름 · 다 안 되면 그 글꼴은 안 씀)."""
        for ok in ("KFA고딕", "Do Hyeon", "KFATest-Regular"):
            self.assertTrue(epfont.ass_name_ok(ok), ok)
        for bad in ("", "  ", "Foo Gothic, KR", "a\tb", "a\nb", "a\x7fb", None):
            self.assertFalse(epfont.ass_name_ok(bad), bad)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "쉼표.ttf"
            p.write_bytes(renamed(DOHYEON, "KFA고딕, 굵게", full="KFA고딕 굵게"))
            [x] = epfont.faces(p)
            self.assertEqual((x["family"], x["full"], x["ass"]), ("KFA고딕, 굵게", "KFA고딕 굵게", ["KFA고딕, 굵게", "KFA고딕 굵게"]))   # TrueType = PostScript 이름은 안 씀
            self.assertEqual(epfont._info(p, x, "picked", "쉼표.ttf")["family"], "KFA고딕 굵게")       # 전체 이름 (libass 가 맞춰 봄)
            p.write_bytes(renamed(DOHYEON, "KFA고딕, 굵게"))
            [x] = epfont.faces(p)
            with self.assertRaisesRegex(ValueError, "쓸 수 없는 글자"):
                epfont._info(p, x, "picked", "쉼표.ttf")
        [o] = epfont.faces(editor.FONTS / "Pretendard-Bold.otf")   # CFF 글꼴은 PostScript 이름까지 (libass 가 맞춰 봄)
        self.assertEqual(o["ass"][0], "Pretendard")
        self.assertIn("Pretendard-Bold", o["ass"])
        # 이름이 다 쉼표면 그 글꼴은 안 씀 → 깔린 글꼴 · 지금 글꼴 (엉뚱한 글꼴·크기로 그리지 않게)
        epfont.forget()
        with mock.patch.object(style, "STYLES", Path(tempfile.mkdtemp()) / "styles"), mock.patch.object(epfont, "FONT_DIRS", []), \
                mock.patch.object(epfont, "_log"):
            style.save_card_font(renamed(DOHYEON, "KFA고딕, 굵게"), "쉼표.ttf")
            self.assertIsNone(epfont.find())
            shutil.rmtree(style.STYLES.parent, ignore_errors=True)
        epfont.forget()

    def test_not_fonts(self):
        good = DOHYEON.read_bytes()
        bad = [b"", b"hello world, not a font", (ROOT / "eunpyeong" / "logo.png").read_bytes(), good[:200], b"ttcf" + b"\0" * 40,
               good[:4] + b"\0\0" + good[6:]]
        for raw in bad:
            with self.assertRaises(ValueError, msg=raw[:12]):
                epfont.faces(raw)
        self.assertEqual(epfont.ext_of(good), ".ttf")
        self.assertEqual(epfont.ext_of((editor.FONTS / "Pretendard-Bold.otf").read_bytes()), ".otf")


class MetricsTest(unittest.TestCase):
    def test_table(self):
        m, p = epfont.metrics(DOHYEON), epfont.metrics(editor.FONTS / "Pretendard-Bold.otf")
        self.assertLess(m["hangul"], 0.8)          # 대신할 글꼴은 좁음 (Pretendard 어림 0.9 · 실제 0.864)
        self.assertAlmostEqual(p["hangul"], 0.8643, places=3)
        for ch in " A0a?!▶→":
            self.assertIn(ch, m["em"])
            self.assertTrue(0 < m["em"][ch] < 1.5, ch)
        self.assertNotIn("가", m["em"])            # 한글은 평균 하나
        self.assertNotIn("·", m["em"])            # 글꼴에 없는 글자는 빼고 (화면은 다른 글꼴로 그림 · 예전 어림)
        from PIL import ImageFont
        fo = ImageFont.truetype(str(DOHYEON), 1000)
        self.assertAlmostEqual(m["em"]["W"], fo.getlength("W") / 1000, places=4)

    def test_font_without_hangul(self):
        f = no_hangul_font()
        if not f:
            self.skipTest("한글 없는 글꼴이 없어요")
        with self.assertRaisesRegex(ValueError, "한글이 없는 글꼴"):
            epfont.metrics(f)


# ---------- 찾기 ----------

class FindTest(FontEnv, unittest.TestCase):
    def test_nothing_found(self):
        (self.fdirs[0] / "DoHyeon.ttf").write_bytes(DOHYEON.read_bytes())   # 한글 글꼴이어도 KFA 이름이 아니면 안 씀
        (self.fdirs[0] / "깨진 KFA.ttf").write_bytes(b"broken")
        self.assertIsNone(epfont.find())
        self.assertEqual(style.card_font_state(), {"found": False, "picked": False, "checking": False})
        self.assertIsNone(epfont.public(None))

    def test_installed_by_name_table(self):
        """파일 이름에 KFA 가 없어도 글꼴 이름 표로 찾음 (모든 글꼴 파일을 읽음) · 사용자 글꼴 폴더도 · 여럿이면 가장 굵은 것."""
        self.install("font0123.ttf", d=1)
        info = epfont.find()
        self.assertEqual((info["family"], info["src"], info["file"], info["weight"], info["k"]), (FAMILY, "installed", "font0123.ttf", 400, 1.0))
        self.assertEqual(info["em"], epfont.metrics(DOHYEON)["em"])
        self.install("KFA고딕 Bold.ttf", weight=700)
        info = epfont.find()
        self.assertEqual((info["file"], info["weight"]), ("KFA고딕 Bold.ttf", 700))
        st = style.card_font_state()
        self.assertEqual(st, {"found": True, "picked": False, "checking": False, "family": FAMILY, "src": "installed", "file": "KFA고딕 Bold.ttf"})
        pub = epfont.public(info)
        self.assertEqual(set(pub), {"family", "k", "em", "hangul", "src", "file", "url"})
        self.assertRegex(pub["url"], r"^/api/style/ep_font\?v=[0-9a-f]{10}$")
        self.assertNotIn(str(self.ftmp), json.dumps(pub, ensure_ascii=False))   # 경로는 화면에 안 줌

    def test_installed_by_file_name(self):
        """파일 이름에 KFA 가 들었으면 속 이름에 KFA 가 없어도 씀 (진짜 KFA 글꼴의 속 이름을 아직 모름 · 한국어 이름일 수도) ·
        이름 표에만 KFA 가 든 다른 파일보다 먼저 · 한글이 없는 'KFA' 파일은 건너뜀."""
        (self.fdirs[0] / "KFA고딕.ttf").write_bytes(DOHYEON.read_bytes())          # 속 이름 'Do Hyeon'
        self.install("font0123.ttf", d=1, weight=900)                              # 이름 표에만 KFA (더 굵어도 파일 이름 쪽이 먼저)
        nh = no_hangul_font()
        if nh:
            (self.fdirs[1] / "KFA Latin.ttf").write_bytes(nh.read_bytes())
        info = epfont.find()
        self.assertEqual((info["family"], info["src"], info["file"]), ("Do Hyeon", "installed", "KFA고딕.ttf"))
        self.assertEqual(info["em"], epfont.metrics(DOHYEON)["em"])
        (self.fdirs[0] / "KFA고딕.ttf").unlink()
        epfont.forget()
        self.assertEqual(epfont.find()["file"], "font0123.ttf")                     # 없으면 이름 표로
        # 파일 이름 쪽 여럿: 속 이름에 KFA 가 든 것 먼저 · 그다음 가장 굵은 것
        (self.fdirs[0] / "KFA 굵게.ttf").write_bytes(renamed(DOHYEON, "Other Bold", weight=800, ps="Other-Bold"))
        (self.fdirs[0] / "KFA 보통.ttf").write_bytes(renamed(DOHYEON, weight=400))
        epfont.forget()
        self.assertEqual(epfont.find()["file"], "KFA 보통.ttf")

    def test_rescan_reads_only_new_files(self):
        """글꼴 폴더가 바뀌어 다시 찾을 때는 새로 깐(바뀐) 파일만 읽음 (파일마다 읽은 이름 표를 기억 · 지운 파일은 잊음)."""
        import os
        for i in range(5):
            (self.fdirs[0] / f"다른 글꼴 {i}.ttf").write_bytes(renamed(DOHYEON, f"Other {i}", ps=f"Other-{i}"))
        with mock.patch.object(epfont, "faces", wraps=epfont.faces) as fc, mock.patch.object(epfont, "RECHECK", 0):
            self.assertIsNone(epfont.find())
            self.assertEqual(fc.call_count, 5)
            new = self.fdirs[0] / "새 글꼴.ttf"
            new.write_bytes(renamed(DOHYEON, ps="New-1"))
            os.utime(self.fdirs[0], ns=(1, 10 ** 18))
            fc.reset_mock()
            self.assertEqual(epfont.find()["file"], "새 글꼴.ttf")
            self.assertEqual([c.args[0].name for c in fc.call_args_list], ["새 글꼴.ttf"])
            (self.fdirs[0] / "다른 글꼴 0.ttf").unlink()
            os.utime(self.fdirs[0], ns=(1, 2 * 10 ** 18))
            fc.reset_mock()
            self.assertEqual(epfont.find()["file"], "새 글꼴.ttf")
            self.assertEqual(fc.call_count, 0)
            self.assertNotIn(str(self.fdirs[0] / "다른 글꼴 0.ttf"), epfont._FACES)
        epfont.forget(files=False)   # 고르기·처음으로 = 파일마다 읽은 것은 남김
        self.assertTrue(epfont._FACES)
        epfont.forget()
        self.assertFalse(epfont._FACES)

    def test_picked_first_then_reset(self):
        self.install()
        c = self.pick()
        self.assertRegex(c["file"], r"^font_[0-9a-f]{12}\.ttf$")
        self.assertEqual(c["name"], "KFA고딕 테스트.ttf")
        self.assertTrue((style.card_pic_dir() / c["file"]).is_file())
        info = epfont.find()
        self.assertEqual((info["family"], info["src"], info["file"]), ("Do Hyeon", "picked", "KFA고딕 테스트.ttf"))   # 고른 파일은 이름을 안 따짐
        self.assertTrue(style.card_font_state()["picked"])
        style.reset_card_font()
        self.assertEqual(epfont.find()["src"], "installed")
        self.assertEqual(style.card_font(), {"file": "", "name": ""})
        self.pick()
        (style.card_pic_dir() / style.card_font()["file"]).unlink()   # 고른 파일이 없어지면 = 고르지 않음
        epfont.forget()
        self.assertEqual(epfont.find()["src"], "installed")

    def test_broken_picked_falls_back(self):
        self.install()
        c = self.pick()
        (style.card_pic_dir() / c["file"]).write_bytes(b"broken now")
        epfont.forget()
        with mock.patch.object(epfont, "_log") as lg:
            self.assertEqual(epfont.find()["src"], "installed")
        self.assertIn("고른 은평 스타일 글꼴을 읽지 못해", lg.call_args[0][0])
        style._card_font_file().write_text("{", encoding="utf-8")   # 깨진 설정 = 고르지 않음
        self.assertEqual(style.card_font(), {"file": "", "name": ""})

    def test_remembered_and_rechecked(self):
        with mock.patch.object(epfont, "_lookup", wraps=epfont._lookup) as lk:
            self.assertIsNone(epfont.find())
            self.assertIsNone(epfont.find())
            self.assertEqual(lk.call_count, 1)                      # 기억해 둔 값
            with mock.patch.object(epfont, "RECHECK", 0):
                self.assertIsNone(epfont.find())
                self.assertEqual(lk.call_count, 1)                  # 폴더가 그대로면 다시 안 읽음
                p = self.fdirs[0] / "KFA.ttf"
                p.write_bytes(renamed(DOHYEON))
                import os
                os.utime(self.fdirs[0], ns=(1, 10 ** 18))           # 글꼴을 깔면 폴더가 바뀜
                self.assertEqual(epfont.find()["family"], FAMILY)
                self.assertEqual(lk.call_count, 2)

    def test_screen_does_not_wait(self):
        """다른 스레드가 글꼴 폴더를 읽는 동안 화면 요청은 UI_WAIT 만 기다리고 '찾는 중' (내보내기는 끝까지 기다림)."""
        self.install()
        go, held = threading.Event(), threading.Event()
        real = epfont._lookup

        def slow():
            held.set()
            go.wait(10)
            return real()
        with mock.patch.object(epfont, "_lookup", slow), mock.patch.object(epfont, "UI_WAIT", 0.2):
            th = threading.Thread(target=epfont.find)
            th.start()
            held.wait(5)
            self.assertIsNone(epfont.find(wait=False))
            self.assertTrue(epfont.checking())
            self.assertEqual(style.card_font_state(), {"found": False, "picked": False, "checking": True})
            go.set()
            th.join(10)
        self.assertEqual(epfont.find(wait=False)["family"], FAMILY)
        self.assertFalse(epfont.checking())

    def test_screen_never_searches_itself(self):
        """화면 요청(wait=False)은 폴더가 바뀌었어도 스스로 찾지 않음 — 뒤 스레드가 찾고 화면은 UI_WAIT 뒤 기억해 둔 값 + '찾는 중' ·
        끝나면 새 값 (글꼴을 하나 깔고 바로 스타일 화면·편집실을 열어도 요청이 글꼴 폴더를 다 읽기를 기다리지 않게)."""
        import os
        self.install()
        first, k0 = epfont.find(), epfont._CACHE["key"]
        self.assertEqual(first["file"], "KFA고딕.ttf")
        go, held, who = threading.Event(), threading.Event(), []
        real = epfont._lookup

        def slow():
            who.append(threading.current_thread())
            held.set()
            go.wait(10)
            return real()
        self.install("KFA 새 글꼴.ttf", weight=800)
        epfont._CACHE.update(key=k0, info=first, at=0.0)   # 깔기 전 결과를 기억한 채 (install 이 비운 것을 되돌림)
        os.utime(self.fdirs[0], ns=(1, 10 ** 18))
        with mock.patch.object(epfont, "_lookup", slow), mock.patch.object(epfont, "UI_WAIT", 0.2):
            t0 = time.monotonic()
            self.assertEqual(epfont.find(wait=False)["file"], "KFA고딕.ttf")          # 예전 값을 바로
            self.assertLess(time.monotonic() - t0, 2)
            held.wait(5)
            self.assertIsNot(who[0], threading.current_thread())                       # 찾기는 뒤 스레드
            self.assertTrue(epfont.checking())
            st = style.card_font_state()
            self.assertEqual((st["found"], st["file"], st["checking"]), (True, "KFA고딕.ttf", True))   # 찾은 것 + 다시 찾는 중
            go.set()
            epfont._BG["th"].join(10)
        self.assertFalse(epfont.checking())
        self.assertEqual(epfont.find(wait=False)["file"], "KFA 새 글꼴.ttf")

    def test_failure_is_quiet(self):
        with mock.patch.object(epfont, "_lookup", side_effect=RuntimeError("boom")), mock.patch.object(epfont, "_log") as lg:
            self.assertIsNone(epfont.find())
        self.assertIn("boom", lg.call_args[0][0])
        with mock.patch.object(epfont, "find", side_effect=RuntimeError("x")):
            self.assertIsNone(editor.ep_font())


class SaveTest(FontEnv, unittest.TestCase):
    def test_validate(self):
        a = self.pick()
        self.assertEqual(style.save_card_font(DOHYEON.read_bytes(), "다른 이름.ttf")["file"], a["file"])   # 같은 내용 = 같은 이름 (바이트로 줘도)
        self.assertEqual(style.card_font()["name"], "다른 이름.ttf")
        self.assertEqual(style.save_card_font(DOHYEON.read_bytes(), r"C:\Users\감독\Downloads\  KFA   고딕.ttf")["name"], "KFA 고딕.ttf")
        o = style.save_card_font((editor.FONTS / "Pretendard-Bold.otf").read_bytes(), "")
        self.assertTrue(o["file"].endswith(".otf"))
        self.assertEqual(o["name"], o["file"])
        t = style.save_card_font(ttc([DOHYEON.read_bytes()]), "묶음.ttc")
        self.assertTrue(t["file"].endswith(".ttc"))
        for bad, why in ((b"not a font", "글꼴 파일이 아니에요"), ("data:x", "읽지 못했어요"), ("hello", "읽지 못했어요"), (None, "읽지 못했어요"), (12, "읽지 못했어요"),
                         ((ROOT / "eunpyeong" / "band.png").read_bytes(), "글꼴 파일이 아니에요")):
            with self.assertRaisesRegex(ValueError, why):
                style.save_card_font(bad, "x.ttf")
        with mock.patch.object(epfont, "FONT_MAX", 1000), self.assertRaisesRegex(ValueError, "너무 커요"):
            style.save_card_font(DOHYEON.read_bytes(), "큰.ttf")
        f = no_hangul_font()
        if f:
            n = len(list(style.card_pic_dir().iterdir()))
            with self.assertRaisesRegex(ValueError, "한글이 없는 글꼴"):
                style.save_card_font(f.read_bytes(), "영문.ttf")
            self.assertEqual(len(list(style.card_pic_dir().iterdir())), n)   # 거절한 글꼴은 복사하지 않음
        self.assertEqual(style.card_font()["name"], "묶음.ttc")             # 거절해도 고른 글꼴은 그대로


# ---------- 내보내기 ASS · 글자 맞춤 ----------

def ep_seq(title="디딤발 방향이 슈팅을 정해요", cap_text="공을 멈추는 게 아니라 다음 동작을 준비하는 거예요", logo=True, emph_font="Black Han Sans"):
    """은평 스타일 편집본 하나 (큰 제목·사실 줄·이름표·강좌 줄·말 자막 · MSG 강조 글자(배운 글꼴)) — 지금 글꼴로 맞춤."""
    d = 6.0
    A = editor._pair(0.0, d, start=0.0)
    top = {"id": "t", "text": title, "start": 0.0, "dur": d, "style": dict(editor.TITLE_STYLE)}
    kt = {"id": "k", "text": "퍼스트 터치 레슨 · 포인트 3", "plan": "topic"}
    shapes = editor.ep_card(top, kt, d)
    t, g = editor.ep_series("1분 레슨", 12, d)
    line = editor.ep_longline(d, logo)
    emph = {"id": "e", "text": "포인트!", "start": 1.0, "dur": 4.0, "style": dict(editor.TITLE_STYLE, font=emph_font, y=0.5, size=90)}
    cs = editor.ep_cap_clear(editor.ep_caption(dict(editor.SHORTS_STYLE)), [line], logo)
    lay = dict(editor.EP_LAYOUT, **({"logo": "logo.png"} if logo else {}))
    q = editor._new_seq("쇼츠 1 · 은평", "shorts", A, captionStyle=cs, layout=lay, titles=[top, kt, t, line, emph], shapes=shapes + [g])
    q.update(captions=[{"id": "c1", "start": 0.0, "end": d, "text": cap_text}], info={"duration": d, "width": 1920, "height": 1080}, media=[])
    return q


def styles_of(ass):
    return {ln.split(":", 1)[1].split(",")[0].strip(): ln.split(",") for ln in ass.splitlines() if ln.startswith("Style:")}


class AssTest(FontEnv, unittest.TestCase):
    def test_all_texts_use_font(self):
        self.install(weight=700)
        q = ep_seq()
        self.assertTrue(editor.ep_font_on(q))
        ass = editor.build_ass(q, W, H)
        st = styles_of(ass)
        texts = {k: v for k, v in st.items() if k not in ("Shape", "Board")}
        self.assertEqual(set(texts), {"Cap", "Tt", "Tk", "Te", *("T" + x["id"] for x in q["titles"])})
        for name, f in texts.items():
            self.assertEqual(f[1], FAMILY, name)            # 배운 스타일 강조 글꼴(검은고딕)도 KFA (Te)
            self.assertEqual(f[7], "700", name)             # 글꼴 파일 굵기 그대로 (libass 가 그 글꼴을 고름 · 굵게 흉내 없음)
        sizes = {t["id"]: t["style"]["size"] for t in q["titles"]}
        for tid, size in sizes.items():
            self.assertEqual(int(st["T" + tid][2]), round(size * 1.0), tid)   # 줄 높이 비율 1.0 (도현)
        self.assertEqual(st["Shape"][1], "Arial")
        # 글꼴을 못 찾으면 예전과 같은 바이트
        epfont.forget()
        with mock.patch.object(epfont, "FONT_DIRS", []):
            old = editor.build_ass(q, W, H)
        self.assertNotIn(FAMILY, old)
        self.assertIn("Style: Te,Black Han Sans,", old)
        with mock.patch.object(editor, "ep_font", lambda wait=True: None):
            self.assertEqual(editor.build_ass(q, W, H), old)

    def test_other_layouts_unchanged(self):
        """은평 스타일이 아닌 화면(영상 창 없음 · 롱폼 · 흐린 배경)은 글꼴을 찾았어도 예전과 같은 바이트."""
        self.install()
        base = ep_seq()
        for lay, fmt in ((dict(editor.BOX_LAYOUT), "shorts"), ({"mode": "blur", "zoom": 1.35, "vpos": 0.5, "win": True}, "shorts"),
                         (dict(editor.EP_LAYOUT), "long")):
            q = dict(base, layout=lay, format=fmt)
            self.assertFalse(editor.ep_font_on(q))
            got = editor.build_ass(q, W, H)
            with mock.patch.object(editor, "ep_font", lambda wait=True: None):
                self.assertEqual(got, editor.build_ass(q, W, H), (lay, fmt))
            self.assertNotIn(FAMILY, got)

    def test_title_refit_with_real_metrics(self):
        """큰 제목은 그 글꼴 폭으로 흰 상자에 맞춤 — 좁은 글꼴이면 더 큰 글자 · 진짜 글꼴로 잰 폭이 상자 안 (양옆 여백)."""
        from PIL import ImageFont
        room = editor.EP_BOX["w"] * W - 2 * editor.EP_PAD
        texts = ["패스하고 그 자리에 서 있으면 안 돼요", "공을 멈추지 말고 다음 동작으로 이어 가요", "트래핑 꿀팁", "2대1 패스 ABC 연습"]
        old = [editor.ep_title_style(t)["size"] for t in texts]
        self.install()
        new = [editor.ep_title_style(t)["size"] for t in texts]
        self.assertGreater(new[0], old[0])
        self.assertGreater(new[1], old[1])
        for t, z in zip(texts, new):
            real = ImageFont.truetype(str(DOHYEON), z).getlength(t)
            self.assertLessEqual(real, room + 1, (t, z))
            if z < editor.EP_SIZES[0]:   # 한 단계 더 크면 넘침 (가장 큰 글자)
                bigger = editor.EP_SIZES[editor.EP_SIZES.index(z) - 1]
                self.assertGreater(ImageFont.truetype(str(DOHYEON), bigger).getlength(t), room - 2, (t, z))
        # 이름표 상자 폭도 그 글꼴 폭
        tg, g = editor.ep_series("1분 레슨", 123, 5.0)
        real = ImageFont.truetype(str(DOHYEON), tg["style"]["size"]).getlength(tg["text"])
        self.assertAlmostEqual(g["w"] * W, real + 2 * editor.EP_TAG_PAD, delta=3)

    def test_caption_fit_uses_font(self):
        cs = dict(editor.ep_caption(dict(editor.SHORTS_STYLE)), size=80)
        text, long = "가" * 13, "가나다라마바사 아자차카타파하"
        old = (editor.cap_fit(text, cs, W), editor.cap_wrap_lines(long, cs, W), editor.text_box(text, cs, W, H))
        info = self.install() and epfont.find()
        with editor.ep_font_scope(info):
            new = (editor.cap_fit(text, cs, W), editor.cap_wrap_lines(long, cs, W), editor.text_box(text, cs, W, H))
            self.assertAlmostEqual(editor._em("가", fm=info), info["hangul"])
            self.assertEqual(editor._em("😀", fm=info), editor._em("😀"))   # 글꼴에 없는 글자는 예전 어림
        self.assertEqual(editor.ep_font_now(), None)                       # 범위 밖은 그대로
        self.assertLess(old[0], 1.0)
        self.assertEqual(new[0], 1.0)                                       # 좁은 글꼴 = 줄이지 않아도 들어감
        self.assertLess(new[1], old[1])
        self.assertLess(new[2][2] - new[2][0], old[2][2] - old[2][0])
        # 내보내기: 줄인 자막의 \fs 도 그 글꼴 비율 (도현 1.0)
        q = ep_seq(cap_text="가" * 17)
        q["captionStyle"]["size"] = 80
        ass = editor.build_ass(q, W, H)
        k0 = editor.cap_fit("가" * 17, q["captionStyle"], W)
        with editor.ep_font_scope(epfont.find()):
            k = editor.cap_fit("가" * 17, q["captionStyle"], W)
        self.assertLess(k0, k)
        self.assertLess(k, 1.0)
        self.assertIn(rf"\fs{round(80 * k * 1.0)}", ass)

    def test_write_subs_copies_font(self):
        info = (self.install(), epfont.find())[1]
        q = ep_seq()
        with tempfile.TemporaryDirectory() as t:
            editor._write_subs(Path(t), q, W, H, 30)
            self.assertEqual((Path(t) / "fonts" / "ep_font.ttf").read_bytes(), Path(info["path"]).read_bytes())
            self.assertTrue((Path(t) / "fonts" / "Pretendard-Bold.otf").is_file())
            self.assertIn(f"Style: Cap,{FAMILY},", (Path(t) / "subs.ass").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as t, mock.patch.object(editor, "ep_font", lambda wait=True: dict(info, path=str(self.ftmp / "없음.ttf"))), \
                mock.patch.object(editor.studiolog, "write") as lg:
            editor._write_subs(Path(t), q, W, H, 30)   # 글꼴 파일을 못 넣으면 ASS 도 지금 글꼴 (엉뚱한 글꼴로 안 그림)
            self.assertNotIn(FAMILY, (Path(t) / "subs.ass").read_text(encoding="utf-8"))
            self.assertFalse((Path(t) / "fonts" / "ep_font.ttf").exists())
        self.assertIn("지금 글꼴로 만들어요", lg.call_args[0][0])
        with tempfile.TemporaryDirectory() as t:   # 다른 화면은 글꼴 파일을 안 넣음
            editor._write_subs(Path(t), dict(q, layout=dict(editor.BOX_LAYOUT)), W, H, 30)
            self.assertFalse(any(p.name.startswith("ep_font") for p in (Path(t) / "fonts").iterdir()))

    @unittest.skipUnless(FF, "ffmpeg 가 없어요")
    def test_comma_name_real_render(self):
        """글꼴 이름(이름 표 1번)에 쉼표가 든 고른 글꼴: ASS 'Style:' 줄 칸이 밀리지 않게 전체 이름을 씀 · libass 가 그 이름으로 그 글꼴 파일을 고름."""
        style.save_card_font(renamed(DOHYEON, "KFA고딕, 굵게", full="KFA고딕 굵게", ps="KFAComma-Bold"), "쉼표 글꼴.ttf")
        info = epfont.find()
        self.assertEqual((info["family"], info["src"]), ("KFA고딕 굵게", "picked"))
        q = ep_seq()
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            editor._write_subs(t, q, W, H, 30)
            ass = (t / "subs.ass").read_text(encoding="utf-8")
            fmt = next(ln for ln in ass.splitlines() if ln.startswith("Format: Name, Fontname"))
            for ln in ass.splitlines():
                if ln.startswith("Style:"):
                    self.assertEqual(len(ln.split(",")), len(fmt.split(",")), ln)   # 칸 수 그대로 (크기·색·굵기가 밀리지 않음)
            self.assertNotIn("KFA고딕, 굵게", ass)
            r = subprocess.run([FF, "-hide_banner", "-loglevel", "debug", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=4:r=30", "-vf",
                                "subtitles=subs.ass:fontsdir=fonts", "-f", "null", "-"], cwd=t, capture_output=True, text=True, encoding="utf-8", errors="replace")
        sel = [x for x in re.findall(r"fontselect: \((.+?), (\d+), \d+\) -> (\S+?),", r.stderr) if x[0] != "Arial"]
        self.assertTrue(sel, r.stderr[-1500:])
        self.assertEqual({f for f, _w, _p in sel}, {"KFA고딕 굵게"}, sel)
        self.assertEqual(sel[0][2], "KFAComma-Bold", sel)                      # 그 글꼴 파일 (내보내기 fonts 폴더의 ep_font.ttf)

    def test_msg_measure(self):
        info = (self.install(), epfont.find())[1]
        st = dict(editor.TITLE_STYLE, size=80, y=0.5, font="Black Han Sans")
        old = msg.text_box("디딤발 방향이 중요해요", st, W, H)
        with editor.ep_font_scope(info):
            fo, k = msg._font(st)
            new = msg.text_box("디딤발 방향이 중요해요", st, W, H)
        self.assertEqual((fo.getname()[1], k), ("Regular", 1.0))
        from PIL import ImageFont
        self.assertAlmostEqual(new[2] - new[0], ImageFont.truetype(str(DOHYEON), 80).getlength("디딤발 방향이 중요해요") + 2 * st["strokeW"], delta=1)
        self.assertNotEqual(old, new)


class MsgFontTest(unittest.TestCase):
    """시험 원본(make_msg_fixture)으로 은평 스타일 MSG 쇼츠 — 대신할 글꼴을 고른 채로: 큰 제목은 그 글꼴로 맞춘 크기 · 글자 자리 셈도 그 글꼴 ·
    ASS 의 모든 글자 = 그 글꼴 (배운 스타일 강조 글꼴도)."""

    @classmethod
    def setUpClass(cls):
        from make_msg_fixture import MsgWork
        cls.w = MsgWork()
        cls.fd = mock.patch.object(epfont, "FONT_DIRS", [])
        cls.fd.start()
        epfont.forget()
        style.set_shorts_layout("eunpyeong")
        style.set_card_extras(series=True, long_line=True)
        style.save_card_font(DOHYEON.read_bytes(), "KFA고딕 테스트.ttf")
        cls.info = epfont.find()
        specs = [{"kind": "preset", "name": n} for n in msg.PRESETS]
        cls.qs = msg.build_variants(cls.w.name, specs[:3], "듬뿍", ("shorts",), lambda m: None)["sequences"]

    @classmethod
    def tearDownClass(cls):
        epfont.forget()
        cls.fd.stop()
        cls.w.close()

    def test_msg_shorts(self):
        self.assertEqual(self.info["family"], "Do Hyeon")
        self.assertTrue(self.qs)
        fonts = set()
        for q in self.qs:
            self.assertTrue(editor.ep_font_on(q), q["name"])
            ev = next(e for e in q["msg"]["events"] if e["kind"] == "hook")
            hk = next(t for t in q["titles"] if t["id"] == ev["refs"]["titles"][0])
            self.assertEqual(hk["style"], editor.ep_title_style(hk["text"]))   # 그 글꼴로 맞춘 크기
            with mock.patch.object(editor, "ep_font", lambda wait=True: None):
                old = editor.ep_title_style(hk["text"])["size"]
            self.assertGreaterEqual(hk["style"]["size"], old)
            ass = editor.build_ass(q, W, H)
            for name, f in styles_of(ass).items():
                if name not in ("Shape", "Board"):
                    self.assertEqual(f[1], "Do Hyeon", (q["name"], name))
            fonts |= {t["style"].get("font") for t in q["titles"]}
            # 글자 자리 셈(그 글꼴)으로 화면 글자끼리·흰 상자와 안 겹침
            with editor.ep_font_scope(self.info):
                [box] = [g for g in q["shapes"] if g.get("plan") == "card"]
                fixed = [box["x"] * W, box["y"] * H, (box["x"] + box["w"]) * W, (box["y"] + box["h"]) * H]
                for t in q["titles"]:
                    if t["id"] in ev["refs"]["titles"] or t.get("plan") in ("series", "longline"):
                        continue
                    b = msg.text_box(t["text"], t["style"], W, H)
                    self.assertFalse(b[0] < fixed[2] and fixed[0] < b[2] and b[1] < fixed[3] and fixed[1] < b[3], (q["name"], t["text"], b))
        self.assertTrue(len(fonts) >= 1)


@unittest.skipUnless(FF, "ffmpeg 가 없어요")
class ExportFrameTest(FontEnv, WorkMixin, unittest.TestCase):
    """실제 1080×1920 내보내기 장면(editor.export_frame = 내보내기와 같은 그리기): libass 가 모든 글자에 그 글꼴 파일을 고름 ·
    글자 자리마다 예전 글꼴(Pretendard·검은고딕)과 다르게 그려짐 · 장면은 증거로 SCRATCH 에 남길 수 있음 (EP_FONT_EV)."""
    D = 6.0

    def frame(self, q, name, tag):
        seen = {}
        real = editor._write_subs

        def keep(tmp, proj, Wd, Hd, fps):
            real(tmp, proj, Wd, Hd, fps)
            seen["ass"] = (tmp / "subs.ass").read_text(encoding="utf-8")
            seen["fonts"] = self.ftmp / f"fonts_{tag}"
            shutil.copytree(tmp / "fonts", seen["fonts"], dirs_exist_ok=True)
        with mock.patch.object(editor, "_write_subs", keep):
            r = editor.export_frame(name, dict(q, source=name, master={"volume": 1, "normalize": False, "lufs": -14}), 3.0, lambda m: None)
        self.assertEqual((r["w"], r["h"]), (1080, 1920))
        import os
        ev = os.environ.get("EP_FONT_EV")
        if ev:
            Path(ev).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(core.OUT / r["file"], Path(ev) / f"export_frame_{tag}.png")
        from PIL import Image
        import numpy as np
        with Image.open(core.OUT / r["file"]) as im:
            return np.asarray(im.convert("RGB")).astype(int), seen

    def fontselect(self, seen):
        """subs.ass 를 같은 fonts 폴더로 그려 libass 가 고른 글꼴 기록 (debug)."""
        d = Path(tempfile.mkdtemp(dir=self.ftmp))
        (d / "subs.ass").write_text(seen["ass"], encoding="utf-8")
        shutil.copytree(seen["fonts"], d / "fonts")
        r = subprocess.run([FF, "-hide_banner", "-loglevel", "debug", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=4:r=30", "-vf",
                            "subtitles=subs.ass:fontsdir=fonts", "-f", "null", "-"], cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return re.findall(r"fontselect: \((.+?), (\d+), \d+\) -> (\S+?),", r.stderr)

    def test_frame(self):
        import numpy as np
        name = self.fix("MSGRAW01")
        r = subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s=640x360:r=30:d={self.D}", "-f", "lavfi", "-i",
                            "anullsrc=r=48000:cl=stereo", "-t", str(self.D), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(core.VIDEOS / name)],
                           capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.install()
        q = ep_seq()
        q["info"] = {"duration": self.D, "width": 640, "height": 360, "fps": 30.0}
        on, s_on = self.frame(q, name, "kfa")
        sel = [x for x in self.fontselect(s_on) if x[0] != "Arial"]            # Arial = 도형(흰 상자·이름표 상자) 그리기 스타일 (글자 아님)
        self.assertTrue(sel)
        self.assertEqual({fam for fam, _w, _p in sel}, {FAMILY}, sel)          # 모든 글자: KFA 이름으로 찾고
        self.assertEqual(sel[0][2], "KFATest-Regular", sel)                    # 그 글꼴 파일을 고름 (깔리지 않은 글꼴 = 내보내기 fonts 폴더)
        self.assertTrue(all(p == "KFATest-Regular" or p.startswith("/") for _f, _w, p in sel), sel)   # 그 글꼴에 없는 글자('·')만 다른 글꼴
        with mock.patch.object(epfont, "FONT_DIRS", []):
            epfont.forget()
            off, s_off = self.frame(q, name, "pretendard")
        self.assertEqual({fam for fam, _w, _p in self.fontselect(s_off)} - {"Arial"}, {"Pretendard", "Pretendard Black", "Black Han Sans"})
        # 글자마다 (큰 제목 · 사실 줄 · 이름표 · 강좌 줄 · 강조 글자 · 말 자막) 그 자리 화소가 예전 글꼴과 다름
        boxes = [editor.text_box(t["text"], t["style"], W, H) for t in q["titles"]]
        boxes.append(editor.text_box(q["captions"][0]["text"], q["captionStyle"], W, H))
        for b in boxes:
            x0, y0, x1, y1 = int(b[0] * W), int(b[1] * H), int(b[2] * W), int(b[3] * H)
            diff = np.abs(on[y0:y1, x0:x1] - off[y0:y1, x0:x1]).sum(axis=2) > 60
            self.assertGreater(diff.mean(), 0.03, b)


# ---------- 편집실 미리보기 (node) ----------

def run_js(body, epf):
    prog = ("let N = 0; const nid = () => 'n' + (++N);\nlet S = null, P = { info: { width: 1920, height: 1080 } }, out = null;\n"
            "const total = () => 6;\nconst tacFollowCut = g => g, tacRefollow = g => g.follow;\nconst BW = () => 1080, BH = () => 1920;\n"
            + js_defs(list(JS_DEFS) + ["capFit", "capWrapLines", "fitRect", "epWinB", "epRoomFit"]) + f"\nEPF = {json.dumps(epf, ensure_ascii=False)};\n"
            + body + "\nprocess.stdout.write(JSON.stringify(out));")
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout)


@unittest.skipUnless(NODE, "node 가 없어요")
class PreviewTest(FontEnv, unittest.TestCase):
    TEXTS = ["디딤발 방향이 슈팅을 정해요", "트래핑 꿀팁", "2대1 패스 ABC xyz!", "공을 멈추는 게 아니라 다음 동작을 준비하는 거예요 😀",
             "가" * 26, "Shooting 꿀팁 · 포인트 ▶"]

    def test_same_as_server(self):
        info = (self.install(), epfont.find())[1]
        pub = epfont.public(info)
        cs = [dict(editor.ep_caption(dict(editor.SHORTS_STYLE)), size=z) for z in (64, 80, 96)]
        lay = dict(editor.EP_LAYOUT, logo="logo.png")
        body = f"""const T = {json.dumps(self.TEXTS, ensure_ascii=False)}, CS = {json.dumps(cs)};
S = {{ format: "shorts", layout: {json.dumps(lay)}, titles: [], shapes: [], captionStyle: CS[0], items: [] }};
const wb = epWinB();
out = {{ on: epFontOn(S), title: T.map(t => epTitleStyle(t)), fit: CS.map(st => T.map(t => [capFit(t, st, 1080), capWrapLines(t, st, 1080), txtBox(t, st, 1080, 1920), epRoomFit(t, st, 1080, 1920, wb), textW(t, st)])) }};
const g = {{}}; const tg = {{ text: "1분 레슨 (123)", style: {{ ...EP.TAG }} }}; epSeriesFit(tg, g); out.tag = g;
S = {{ ...S, layout: {json.dumps(dict(editor.BOX_LAYOUT))} }};
out.off = {{ on: epFontOn(S), fit: CS.map(st => T.map(t => [capFit(t, st, 1080), textW(t, st)])) }};"""
        js = run_js(body, pub)
        self.assertTrue(js["on"])
        self.assertFalse(js["off"]["on"])
        self.assertEqual(js["title"], [editor.ep_title_style(t) for t in self.TEXTS])   # 큰 제목 크기 = 서버 (그 글꼴 폭)
        tg, g = editor.ep_series("1분 레슨", 123, 5.0)
        self.assertEqual((js["tag"]["x"], js["tag"]["w"]), (g["x"], g["w"]))
        wb = editor.ep_win_bottom({"format": "shorts", "layout": lay}, W, H)
        with editor.ep_font_scope(info):
            want = [[[editor.cap_fit(t, st, W), editor.cap_wrap_lines(t, st, W), list(editor.text_box(t, st, W, H)), editor.ep_room_fit(t, st, W, H, wb),
                      editor._text_w(t, st)] for t in self.TEXTS] for st in cs]
        for a, b in zip(js["fit"], want):
            for x, y in zip(a, b):
                self.assertEqual(x[:2], y[:2])
                self.assertEqual([round(v, 6) for v in x[2]], [round(v, 6) for v in y[2]])
                self.assertEqual(x[3], y[3])
                self.assertAlmostEqual(x[4], y[4], places=6)
        # 다른 화면은 예전 어림 그대로 (서버도 범위 밖)
        self.assertEqual(js["off"]["fit"], [[[editor.cap_fit(t, st, W), editor._text_w(t, st)] for t in self.TEXTS] for st in cs])
        # 글꼴을 못 찾으면(EPF null) 은평 스타일 화면도 예전 그대로
        js0 = run_js(f"""S = {{ format: "shorts", layout: {json.dumps(lay)} }}; out = {json.dumps(self.TEXTS, ensure_ascii=False)}.map(t => epTitleStyle(t));""", None)
        with mock.patch.object(editor, "ep_font", lambda wait=True: None):
            self.assertEqual(js0, [editor.ep_title_style(t) for t in self.TEXTS])

    def test_late_font(self):
        """편집실을 열 때 서버가 아직 찾는 중이었으면(epx.fontChecking) 끝날 때까지 다시 물어 글꼴을 받음 — 받은 뒤 큰 제목 크기는 그 글꼴 폭 (I-503) ·
        찾는 중이 아니면 묻지 않음 · 끝내 못 찾으면 null."""
        info = (self.install(), epfont.find())[1]
        pub = epfont.public(info)
        text = "패스하고 그 자리에 서 있으면 안 돼요"   # 좁은 글꼴이면 더 큰 글자 (AssTest.test_title_refit_with_real_metrics)
        prog = ("let frameDirty = false, asked = 0; const toast = () => {};\n" + js_defs(["EP", "TOPIC", "emOf", "textW", "EPF", "EPX", "epFontLoad", "epFontWaiting",
                                                                                      "epFontReady", "epTitleStyle"]) + f"""
const answers = [{{ checking: true }}, {{ checking: true }}, {{ found: true, checking: false, font: {json.dumps(pub, ensure_ascii=False)} }}];
globalThis.fetch = async u => {{ asked++; if (u !== "/api/style/card_font?full=1") throw new Error(u); const a = answers.shift(); return {{ json: async () => a }}; }};
(async () => {{
  const out = {{}};
  EPX = {{}}; out.idle = (await epFontReady(), asked);                        // 찾는 중이 아니면 묻지 않음
  EPX = {{ fontChecking: true }}; out.before = epTitleStyle({json.dumps(text, ensure_ascii=False)});
  const a = epFontReady(), b = epFontReady(); out.same = a === b;            // 두 번 불러도 한 번만 물음
  await a; out.asked = asked; out.family = EPF && EPF.family; out.after = epTitleStyle({json.dumps(text, ensure_ascii=False)}); out.dirty = frameDirty;
  out.checking = EPX.fontChecking;
  EPF = null; EPX = {{ fontChecking: true }}; answers.push({{ found: false, checking: false, font: null }}); await epFontReady(); out.none = EPF;
  process.stdout.write(JSON.stringify(out));
}})();""")
        r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        js = json.loads(r.stdout)
        self.assertEqual((js["idle"], js["same"], js["asked"], js["family"], js["dirty"], js["checking"], js["none"]), (0, True, 3, FAMILY, True, False, None))
        with mock.patch.object(editor, "ep_font", lambda wait=True: None):
            self.assertEqual(js["before"], editor.ep_title_style(text))           # 받기 전 = 예전 어림
        self.assertEqual(js["after"], editor.ep_title_style(text))               # 받은 뒤 = 서버 (그 글꼴 폭)
        self.assertNotEqual(js["before"]["size"], js["after"]["size"])
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        self.assertIn('if (b.dataset.tpl === "은평 스타일" && !EPF && EPX && EPX.fontChecking) { toast("KFA 글꼴을 찾는 중이에요 · 찾으면 바로 적용해요"); await epFontReady(); }', src)
        self.assertIn("if (epOn(recBase().base)) await epFontReady();", src)
        self.assertIn("epFontLoad(); epFontReady();", src)

    def test_editor_source(self):
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        self.assertIn('el.style.fontFamily = ep ? \'"EpFontE", PretendardE\'', src)                  # 은평 화면 글자 = KFA 글꼴 (타이틀 글꼴보다 먼저)
        self.assertIn("EPF = (EPX && EPX.font) || null; epFontLoad();", src)
        self.assertIn('new FontFace("EpFontE", `url("${EPF.url}")`', src)


class ScreenTest(FontEnv, unittest.TestCase):
    def test_style_screen_wording(self):
        ui = (ROOT / "ui.html").read_text(encoding="utf-8")
        self.assertIn("KFA 글꼴을 이 PC에서 찾지 못했어요", ui)
        self.assertIn('<button class="btn" id="cardFontPick">글꼴 파일 고르기</button><button class="btn" id="cardFontReset" hidden>처음으로</button>', ui)
        self.assertIn("은평 스타일 쇼츠의 모든 글자", ui)
        self.assertNotIn("KFA고딕", ui)   # 글꼴 이름은 찾은 파일에서 읽음

    def test_no_font_shipped(self):
        """KFA 글꼴 파일은 저장소·배포 목록에 없음 (사용 허락이 공개돼 있지 않음 · D-500)."""
        for p in ROOT.rglob("*"):
            if ".git" in p.parts or "tests" in p.parts:
                continue
            if p.suffix.lower() in epfont.FONT_EXTS and p.is_file():
                self.assertFalse(any(epfont.has_kfa(x) for x in epfont.faces(p)), p)
                self.assertNotIn("kfa", p.name.lower(), p)


class RouteTest(unittest.TestCase):
    """길: 스타일 화면 글꼴 줄·고르기·처음으로 · 글꼴 파일 · 편집실 열기 epx.font · 원격 길 표 (진짜 app.Handler · 시험 작업 폴더)."""

    def test_routes(self):
        import urllib.error
        import urllib.request
        from test_windows_compat import Server

        class T(Server):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        fd = mock.patch.object(epfont, "FONT_DIRS", [])
        fd.start()
        epfont.forget()
        try:
            def get(path):
                try:
                    with t.opener.open(urllib.request.Request(f"http://127.0.0.1:{t.port}{path}"), timeout=20) as r:
                        return r.status, r.headers.get("Content-Type"), r.read()
                except urllib.error.HTTPError as e:
                    return e.code, None, b""
            self.assertEqual(t.call("/api/style/card_font"), (200, {"found": False, "picked": False, "checking": False}))
            self.assertEqual(t.call("/api/style/list")[1]["cardFont"], {"found": False, "picked": False, "checking": False})
            self.assertEqual(get("/api/style/ep_font")[0], 404)
            st, j = t.call("/api/style/card_font", {"data": data_url(b"nope"), "name": "x.ttf"})
            self.assertEqual(st, 400)
            self.assertIn("글꼴 파일이 아니에요", j["error"])
            st, j = t.call("/api/style/card_font", {"data": data_url(DOHYEON.read_bytes()), "name": "KFA고딕 테스트.ttf"})
            self.assertEqual((st, j), (200, {"ok": True, "found": True, "picked": True, "checking": False, "family": "Do Hyeon", "src": "picked",
                                             "file": "KFA고딕 테스트.ttf"}))
            self.assertEqual(get("/api/style/ep_font?v=1"), (200, "font/ttf", DOHYEON.read_bytes()))
            self.assertEqual(t.call("/api/style/list")[1]["cardFont"]["family"], "Do Hyeon")
            st, j = t.call("/api/style/card_font?full=1")   # 편집실이 늦게 받을 글꼴 정보 (찾는 중이었을 때 · I-503)
            self.assertEqual((st, j["font"]["family"], set(j["font"])), (200, "Do Hyeon", {"family", "k", "em", "hangul", "src", "file", "url"}))
            self.assertNotIn("font", t.call("/api/style/card_font")[1])
            # TTC 묶음: 쓰는 글꼴(KFA 이름 · 굵은 것 = 묶음의 2번째)만 떼어 줌 — 브라우저가 묶음의 첫 글꼴을 그리지 않게 (내보내기와 같은 글꼴)
            raw = ttc([DOHYEON.read_bytes(), renamed(DOHYEON, weight=700)])
            st, j = t.call("/api/style/card_font", {"data": data_url(raw), "name": "KFA 묶음.ttc"})
            self.assertEqual((st, j["family"], j["src"]), (200, FAMILY, "picked"))
            info = epfont.find()
            self.assertEqual((info["index"], info["ext"]), (1, ".ttc"))
            st, ct, body = get("/api/style/ep_font?v=2")
            self.assertEqual((st, ct), (200, "font/ttf"))
            [x] = epfont.faces(body)
            self.assertEqual((x["family"], x["weight"]), (FAMILY, 700))
            self.assertEqual(epfont.metrics(body), {k: info[k] for k in ("em", "hangul")})   # 미리보기 폭 표 = 떼어 준 글꼴
            st, j = t.call("/api/style/card_font", {"reset": True})
            self.assertEqual((st, j["found"], j["picked"]), (200, False, False))
            self.assertEqual(get("/api/style/ep_font")[0], 404)
            self.assertEqual(t.call("/api/style/card_font", {"reset": "yes"})[0], 400)   # reset 은 true 만 · 아니면 고르기(글꼴 없음 → 거절)
        finally:
            epfont.forget()
            fd.stop()
            t.doCleanups()
        import app
        import rstudio
        self.assertEqual(rstudio.classify("GET", "/api/style/card_font"), rstudio.READ)
        self.assertEqual(rstudio.classify("GET", "/api/style/ep_font"), rstudio.READ)
        self.assertEqual(rstudio.classify("POST", "/api/style/card_font"), rstudio.WRITE)
        self.assertEqual(app.FONT_TYPES[".ttc"], "font/collection")
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("font=epfont.public(editor.ep_font(wait=False))", src)   # 편집실을 열 때 은평 글꼴 (epx.font)
        self.assertIn("fontChecking=epfont.checking())", src)                   # 아직 찾는 중이면 편집실이 다시 받음
        self.assertIn("epfont.warm()", src)


class RemoteFontTest(unittest.TestCase):
    """밖에서(휴대폰·웹) '글꼴 파일 고르기': 글꼴 한도(40MB)의 base64 본문이 원격 JSON 한도(32MB)에 막히지 않음 · 넘으면 크기 안내 (조각 올리기 안내 아님)."""

    def test_big_font_from_remote(self):
        import rstudio
        from test_remote_studio import StudioBase

        class T(StudioBase):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        fd = mock.patch.object(epfont, "FONT_DIRS", [])
        fd.start()
        epfont.forget()
        try:
            t.login()
            self.assertGreaterEqual(rstudio.FONT_BODY_MAX, epfont.FONT_MAX * 4 // 3 + 1000)
            raw = DOHYEON.read_bytes() + b"\0" * (26 << 20)   # 26MB 글꼴 (끝에 붙은 바이트는 글꼴 표 밖 · base64 본문 약 35MB > JSON_MAX)
            body = {"data": data_url(raw), "name": "KFA 큰 글꼴.ttf"}
            self.assertGreater(len(json.dumps(body)), rstudio.JSON_MAX)
            st, _, out = t.req("POST", "/api/style/card_font", body)
            j = json.loads(out)
            self.assertEqual((st, j.get("ok"), j.get("family"), j.get("src")), (200, True, "Do Hyeon", "picked"), j)
            st, _, out = t.req("POST", "/api/style/card_font", {"reset": True}, headers={"Content-Length": str(rstudio.FONT_BODY_MAX + 1)})
            j = json.loads(out)
            self.assertEqual((st, j["code"]), (413, "too_big"))
            self.assertIn("글꼴 파일이 너무 커요 · 40MB 보다 작은 파일을 골라 주세요", j["error"])
            st, _, out = t.req("POST", "/api/style/card_extras", {}, headers={"Content-Length": str(rstudio.JSON_MAX + 1)})
            self.assertEqual(st, 413)
            self.assertIn("조각 올리기", json.loads(out)["error"])   # 다른 길은 그대로
        finally:
            epfont.forget()
            fd.stop()
            t.doCleanups()


if __name__ == "__main__":
    unittest.main()
