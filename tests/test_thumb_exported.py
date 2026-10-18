"""썸네일 '유튜브용 그림' 상태 테스트 (D-491 · BR-301) — 저장소 폴더에서 python3 -m unittest tests.test_thumb_exported

- thumb.exported_files: 디자인이 기억한 그림(designs[].exp.file) 중 완성본 폴더에 있고 이 영상 이름으로 시작하는 것만 · /api/thumb/open 의 outs
- 화면(thumb.html, 진짜 브라우저 + 작은 가짜 서버): 제목 옆 '유튜브용 그림 아직 없음 / 고친 뒤 다시 안 만듦 / 저장됨' ·
  열고 그냥 나가면 묻지 않음 · 고친 디자인은 나갈 때 [계속 고치기]·[그냥 나가기]·[저장하고 나가기](JPG) ·
  내보내면 디자인 파일에 exp(지문·파일 이름)를 적고 다시 열어도 '저장됨' · 그림이 지워졌으면 '아직 없음' ·
  AI 추천 카드 '이걸로 저장'은 디자인으로 불러와 JPG 로 저장(완성본 폴더는 안 엶) · '이미지로 저장' 단추는 예전처럼 고른 형식 + 폴더 엶
- 검토 고침(D-494~D-497): 연 디자인이 아니어도 이 창에서 고친 디자인을 물어봄 · 2MB 넘는 PNG 는 '저장됨'(초록)이 아님 ·
  [저장하고 나가기] 뒤 디자인 저장이 실패하면 물어봄 · 1280 너비에서 위 막대가 한 줄('저장됨' 줄바꿈 없음 · 영상 이름 안 잘림)
Playwright·Chromium 이 없으면 화면 시험은 건너뜀. 인터넷은 쓰지 않음."""
import json
import shutil
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import editor  # noqa: E402
import thumb  # noqa: E402
from test_windows_compat import Server, Work  # noqa: E402

NAME = "20200101_EXPTEST001_유튜브용 그림.mp4"
STEM = "20200101_EXPTEST001_유튜브용 그림"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


def docs_with(*files):
    return {"designs": [{"id": f"d{i}", "name": f"디자인 {i}", "doc": {"w": 1280, "h": 720, "layers": []}, **({"exp": {"sig": "x", "file": f}} if f is not None else {})}
                        for i, f in enumerate(files)]}


class ExportedFilesTests(Work):
    def test_only_existing_own_files(self):
        out = thumb.core.OUT
        (out / f"{STEM}_디자인 1_1.jpg").write_bytes(b"jpg")
        (out / f"{STEM}_디자인 2_1.jpg").write_bytes(b"jpg")
        (out / "다른 영상_디자인 1_1.jpg").write_bytes(b"jpg")
        d = docs_with(f"{STEM}_디자인 1_1.jpg", f"{STEM}_지운 그림_1.jpg", "다른 영상_디자인 1_1.jpg", f"../{STEM}_디자인 2_1.jpg",
                      f"{STEM}_디자인 1_1.jpg", None, f"{STEM}_디자인 2_1.jpg")
        d["designs"].append({"id": "x", "exp": "깨진 값"})
        d["designs"].append("깨진 디자인")
        self.assertEqual(thumb.exported_files(NAME, d), [f"{STEM}_디자인 1_1.jpg", f"{STEM}_디자인 2_1.jpg"],
                         "있는 것만 · 다른 영상 이름·폴더 밖·겹침·깨진 값은 빼고")
        self.assertEqual(thumb.exported_files(NAME, {}), [])
        self.assertEqual(thumb.exported_files(NAME, None), [])


class OpenRouteTests(Server):
    def test_open_gives_outs(self):
        self.video(NAME)
        (thumb.core.OUT / f"{STEM}_디자인 1_1.jpg").write_bytes(b"jpg")
        thumb.save_docs(NAME, docs_with(f"{STEM}_디자인 1_1.jpg", f"{STEM}_지운 그림_1.jpg"))
        with mock.patch.object(editor, "recommend", return_value={"shorts": []}), mock.patch.object(editor, "media_info", return_value={"duration": 10}):
            code, j = self.call("/api/thumb/open?name=" + quote(NAME))
        self.assertEqual(code, 200)
        self.assertEqual(j["outs"], [f"{STEM}_디자인 1_1.jpg"])
        self.assertEqual(j["docs"]["designs"][0]["exp"]["file"], f"{STEM}_디자인 1_1.jpg", "디자인 파일은 exp 를 그대로 돌려줌")


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


class _Stub(BaseHTTPRequestHandler):
    """thumb.html 이 쓰는 주소만 흉내 냄 (디자인 저장은 기억했다가 다음 열기에 그대로)."""
    frame = b""
    docs = {"designs": []}
    outs = []
    saved, exported, opened = [], [], []
    fail_after_export = False  # 그림을 내보낸 뒤의 디자인 저장만 실패 (Windows 백신·OneDrive 가 잠깐 잡음 흉내)

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, "<!doctype html><title>스튜디오</title>스튜디오".encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/thumb":
            return self._send(200, (ROOT / "thumb.html").read_bytes(), "text/html; charset=utf-8")
        if u.path.startswith("/fonts/"):
            p = ROOT / "fonts" / Path(u.path).name
            return self._send(200, p.read_bytes(), "font/otf") if p.exists() else self._send(404, {})
        if u.path == "/frame":
            return self._send(200, _Stub.frame, "image/jpeg")
        if u.path == "/api/thumb/open":
            return self._send(200, {"docs": _Stub.docs, "outs": list(_Stub.outs), "hooks": ["풋살 꿀팁 대방출"], "keywords": [],
                                    "info": {"duration": 60, "width": 1280, "height": 720}})
        if u.path == "/api/state":
            return self._send(200, {"job": None})
        if u.path == "/api/auto/pending":
            return self._send(200, {"ok": True, "want": None})
        if u.path == "/api/thumb/brand":
            return self._send(200, {"ok": True, "brand": {"logo": "", "logoPos": "tr", "colors": {"hl": "#FFE14D", "hl2": "#FFFFFF", "accent": "#FF3B30", "neon": "#00D1FF", "box": "#111111"},
                                                          "font": "Black Han Sans", "series": "", "seriesOn": False, "handle": "", "apply": True}})
        if u.path == "/api/thumb/style":
            return self._send(200, {"ok": True, "view": {"styles": [], "pick": None, "active": None, "ours": {"n": 0}}, "sets": []})
        return self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        b = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/thumb/frames":
            return self._send(200, {"ok": True, "frames": [{"t": float(t)} for t in (1, 3, 5, 7)]})
        if self.path == "/api/thumb/save":
            if _Stub.fail_after_export and _Stub.exported:
                return self._send(200, {"ok": False, "error": "잠김"})
            _Stub.saved.append(b)
            _Stub.docs = b["docs"]
            return self._send(200, {"ok": True, "rev": len(_Stub.saved)})
        if self.path == "/api/thumb/export":
            f = f"{STEM}_{b['label']}_{len(_Stub.exported) + 1}.{b['fmt']}"
            _Stub.exported.append(dict(b, file=f))
            _Stub.outs.append(f)
            return self._send(200, {"ok": True, "file": f})
        if self.path == "/api/open":
            _Stub.opened.append(b)
            return self._send(200, {"ok": True})
        if self.path == "/api/thumb/copy":
            return self._send(200, {"ok": False})
        return self._send(200, {"ok": True})


@unittest.skipUnless(sync_playwright and _ffmpeg(), "Playwright·ffmpeg 가 없어 화면 시험을 건너뜀")
class ExportStatePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(__import__("tempfile").mkdtemp())
        p = tmp / "f.jpg"
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=1", "-frames:v", "1", "-q:v", "3", str(p)], check=True)
        _Stub.frame = p.read_bytes()
        shutil.rmtree(tmp, ignore_errors=True)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROME) if Path(CHROME).exists() else cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _Stub.docs, _Stub.outs = {"designs": []}, []
        _Stub.saved, _Stub.exported, _Stub.opened = [], [], []
        _Stub.fail_after_export = False
        self.page = self.browser.new_page(viewport={"width": 1500, "height": 900})
        self.errs = []
        self.page.on("pageerror", lambda e: self.errs.append(str(e)))
        self.addCleanup(self.page.close)

    def open(self):
        pg = self.page
        pg.goto(f"{self.base}/thumb?name=" + quote(NAME))
        pg.wait_for_function("document.getElementById('saved').textContent === '저장됨' && FRAMES.length > 0 && D && D.doc.layers.length > 0", timeout=60000)
        pg.wait_for_timeout(500)
        return pg

    def state(self):
        return self.page.evaluate("[expState(D), document.getElementById('expState').textContent]")

    def leave(self):
        self.page.click("#back")
        self.page.wait_for_timeout(600)

    def at_home(self):
        return urlparse(self.page.url).path == "/"

    def test_fresh_open_shows_none_and_leaves_without_asking(self):
        self.open()
        self.assertEqual(self.state(), ["none", "· 유튜브용 그림 아직 없음"], "열 때 깐 배경만 있어도 상태는 보임")
        self.leave()
        self.assertTrue(self.at_home(), "고친 것이 없으면 묻지 않고 나감")
        self.assertFalse(_Stub.exported)
        self.assertEqual(self.errs, [])

    def test_edited_design_asks_on_leave(self):
        pg = self.open()
        pg.locator(".cand").first.click()  # 예전 템플릿 후보 = '큰 글씨 2줄' (백로그 재현)
        pg.wait_for_timeout(800)
        self.leave()
        self.assertTrue(pg.is_visible("#leaveModal"), "고친 디자인은 유튜브용 그림이 없으면 물어봄")
        self.assertIn("아직 만들지 않았어요", pg.inner_text("#leaveMsg"))
        pg.click("#leaveStay")
        pg.wait_for_timeout(300)
        self.assertFalse(pg.is_visible("#leaveModal"))
        self.assertFalse(self.at_home(), "[계속 고치기] 는 남음")
        self.leave()
        pg.click("#leaveGo")
        pg.wait_for_url(f"{self.base}/", timeout=10000)
        self.assertEqual(_Stub.exported, [], "[그냥 나가기] 는 그림을 만들지 않음")
        self.assertEqual(self.errs, [])

    def test_save_and_leave_exports_jpg_and_remembers(self):
        pg = self.open()
        pg.select_option("#exFmt", "png")  # 고른 형식과 상관없이 유튜브용 JPG
        pg.locator(".cand").first.click()
        pg.wait_for_timeout(800)
        self.leave()
        pg.click("#leaveSave")
        pg.wait_for_url(f"{self.base}/", timeout=20000)
        self.assertEqual(len(_Stub.exported), 1)
        ex = _Stub.exported[0]
        self.assertEqual(ex["fmt"], "jpg")
        self.assertTrue(ex["data"].startswith("data:image/jpeg"))
        self.assertEqual(_Stub.opened, [], "나가면서 저장할 때는 완성본 폴더를 열지 않음")
        d = _Stub.docs["designs"][-1]
        self.assertEqual(d["exp"]["file"], ex["file"], "디자인 파일에 내보낸 그림을 기억 (저장한 뒤 나감)")
        self.assertTrue(d["exp"]["sig"])
        # 다시 열면 '저장됨' · 고치면 '고친 뒤 다시 안 만듦' · 나갈 때 그 말로 물어봄
        pg = self.open()
        pg.evaluate(f"openDesign({len(_Stub.docs['designs']) - 1})")
        pg.wait_for_timeout(200)
        self.assertEqual(self.state(), ["ok", "· 유튜브용 그림 저장됨"])
        self.assertIn(ex["file"], pg.get_attribute("#expState", "title"))
        self.leave()
        self.assertTrue(self.at_home(), "그림이 최신이면 묻지 않음")
        pg = self.open()
        pg.evaluate(f"openDesign({len(_Stub.docs['designs']) - 1}); commit('시험'); D.doc.layers.find(l => l.type === 'text').x += 30; changed();")
        pg.wait_for_timeout(500)
        self.assertEqual(self.state(), ["stale", "· 고친 뒤 유튜브용 그림 다시 안 만듦"])
        pg.evaluate("commit('안내선'); guidesOf().v.push(100); scheduleSave(); renderExp();")
        self.assertEqual(self.state()[0], "stale")
        self.leave()
        self.assertTrue(pg.is_visible("#leaveModal"))
        self.assertIn("고치기 전 그림", pg.inner_text("#leaveMsg"))
        pg.click("#leaveStay")
        pg.evaluate("undo(); undo();")  # 되돌리면 다시 같은 그림 → 저장됨 (안내선은 그림이 아님)
        pg.wait_for_timeout(500)
        self.assertEqual(self.state()[0], "ok")
        self.assertEqual(self.errs, [])

    def test_missing_file_is_none(self):
        """기억한 그림이 완성본 폴더에 없으면(지움·이름 바꾸기) '아직 없음'."""
        pg = self.open()
        pg.locator(".cand").first.click()
        pg.wait_for_timeout(500)
        pg.click("#exportBtn")
        pg.wait_for_timeout(1500)
        self.assertEqual(self.state()[0], "ok")
        self.assertEqual(len(_Stub.opened), 1, "'이미지로 저장' 단추는 예전처럼 완성본 폴더를 엶")
        self.assertEqual(_Stub.exported[0]["fmt"], "jpg")
        pg.evaluate("saveNow()")
        pg.wait_for_timeout(500)
        _Stub.outs = []
        pg = self.open()
        pg.evaluate(f"openDesign({len(_Stub.docs['designs']) - 1})")
        self.assertEqual(self.state()[0], "none")
        self.assertEqual(self.errs, [])

    def test_ai_card_save_this(self):
        pg = self.open()
        n0 = pg.evaluate("DOCS.designs.length")
        pg.evaluate("""(() => { const doc = clone(CANDS[0].doc);
          AI.results = [{ tpl: '시험 틀', score: 81, why: ['얼굴 큼'], doc, copy: { l1: '제목', l2: '' }, t: 1 }]; renderAICards(); })()""")
        self.assertEqual(pg.inner_text("[data-asave='0']"), "이걸로 저장")
        pg.click("[data-asave='0']")
        pg.wait_for_function("DOCS.designs.length > %d && D.exp && D.exp.file" % n0, timeout=20000)
        pg.wait_for_timeout(1200)
        self.assertEqual(len(_Stub.exported), 1)
        self.assertEqual(_Stub.exported[0]["fmt"], "jpg")
        self.assertEqual(_Stub.opened, [], "카드에서 저장할 때는 완성본 폴더를 열지 않음")
        self.assertEqual(self.state()[0], "ok")
        self.assertIn("올리기(7단계)에 이 그림을 써요", pg.inner_text("#toast"))
        self.assertEqual(_Stub.docs["designs"][-1]["exp"]["file"], _Stub.exported[0]["file"])
        self.leave()
        self.assertTrue(self.at_home(), "이미 유튜브용 그림이 최신이라 묻지 않음")
        self.assertEqual(self.errs, [])

    def test_other_edited_design_asks(self):
        """고친 디자인 2 를 두고 디자인 1 을 연 채 나가도 디자인 2 를 물어보고, [저장하고 나가기]는 그 디자인을 저장 (D-494)."""
        pg = self.open()
        pg.locator(".cand").first.click()  # 새 디자인(고침 · 그림 없음)
        pg.wait_for_timeout(800)
        name = pg.evaluate("D.name")
        pg.evaluate("openDesign(0)")  # 처음 디자인(열 때 깐 배경만 · 고치지 않음)으로 돌아가 비교
        pg.wait_for_timeout(300)
        self.leave()
        self.assertTrue(pg.is_visible("#leaveModal"), "연 디자인이 아니어도 이 창에서 고친 디자인을 살핌")
        self.assertIn(f"'{name}'", pg.inner_text("#leaveMsg"))
        self.assertIn(f"'{name}'", pg.inner_text("#leaveHint"), "[저장하고 나가기]가 무엇을 저장하는지 알려 줌")
        pg.click("#leaveSave")
        pg.wait_for_url(f"{self.base}/", timeout=20000)
        self.assertEqual([e["label"] for e in _Stub.exported], [name], "그 디자인만 JPG 로")
        self.assertEqual(_Stub.exported[0]["fmt"], "jpg")
        d = next(x for x in _Stub.docs["designs"] if x["name"] == name)
        self.assertEqual(d["exp"]["file"], _Stub.exported[0]["file"])
        self.assertNotIn("exp", _Stub.docs["designs"][0], "열어 둔 디자인 1 은 그대로")
        self.assertEqual(_Stub.opened, [])
        self.assertEqual(self.errs, [])

    def test_open_design_only_one_message(self):
        """연 디자인 하나만 걸리면 예전 글 그대로 ('지금 디자인')."""
        pg = self.open()
        pg.locator(".cand").first.click()
        pg.wait_for_timeout(800)
        self.leave()
        self.assertIn("지금 디자인을 JPG(유튜브용)로 저장하고 나가요", pg.inner_text("#leaveHint"))
        self.assertNotIn("'", pg.inner_text("#leaveMsg"), "연 디자인이면 이름을 붙이지 않음")
        pg.click("#leaveStay")
        self.assertEqual(self.errs, [])

    def test_save_fail_after_export_asks(self):
        """[저장하고 나가기]: 그림은 만들었는데 디자인 저장(exp 기억)이 실패하면 '← 스튜디오'와 같이 물어봄 (D-496)."""
        pg = self.open()
        pg.locator(".cand").first.click()
        pg.wait_for_timeout(800)
        self.leave()
        self.assertTrue(pg.is_visible("#leaveModal"))
        _Stub.fail_after_export = True
        asked = []
        pg.once("dialog", lambda dl: (asked.append(dl.message), dl.dismiss()))
        pg.click("#leaveSave")
        pg.wait_for_timeout(2500)
        self.assertEqual(len(_Stub.exported), 1)
        self.assertEqual(len(asked), 1)
        self.assertIn("디자인을 저장하지 못했어요", asked[0])
        self.assertFalse(self.at_home(), "[취소]면 남음 (예전: 그대로 나가 다음에 또 '아직 없음')")
        self.assertFalse(pg.is_visible("#leaveModal"))
        _Stub.fail_after_export = False  # 다시 저장되면 그대로 나감
        self.leave()
        self.at_home() or pg.wait_for_url(f"{self.base}/", timeout=10000)
        self.assertTrue(self.at_home())
        self.assertEqual(_Stub.docs["designs"][-1]["exp"]["file"], _Stub.exported[0]["file"])
        self.assertEqual(self.errs, [])

    def test_big_png_is_not_ok(self):
        """2MB 넘는 PNG 를 그대로 두면 7단계 확인에 걸리므로 '저장됨'(초록)이 아니고 나갈 때 물어봄 (D-495)."""
        import numpy as np
        from PIL import Image
        import io
        old = _Stub.frame
        buf = io.BytesIO()
        Image.fromarray(np.random.default_rng(1).integers(0, 256, (720, 1280, 3), dtype=np.uint8)).save(buf, "JPEG", quality=95)
        _Stub.frame = buf.getvalue()
        self.addCleanup(setattr, _Stub, "frame", old)
        pg = self.open()
        pg.locator(".cand").first.click()
        pg.wait_for_timeout(800)
        pg.select_option("#exFmt", "png")
        asked = []
        pg.once("dialog", lambda dl: (asked.append(dl.message), dl.dismiss()))  # [취소] = PNG 그대로
        pg.click("#exportBtn")
        pg.wait_for_function("D.exp && D.exp.file", timeout=20000)
        pg.wait_for_timeout(500)
        self.assertEqual(len(asked), 1)
        self.assertIn("2MB", asked[0])
        self.assertEqual(_Stub.exported[0]["fmt"], "png")
        self.assertEqual(self.state(), ["big", "· 유튜브용 그림이 2MB 넘음 · 올리기에 걸림"])
        self.assertIn("JPG", pg.get_attribute("#expState", "title"))
        self.assertEqual(pg.evaluate("getComputedStyle(document.getElementById('expState')).color"), "rgb(245, 165, 36)", "주황")
        self.leave()
        self.assertTrue(pg.is_visible("#leaveModal"), "올리기에 걸리는 그림이면 물어봄")
        self.assertIn("2MB", pg.inner_text("#leaveMsg"))
        pg.click("#leaveSave")
        pg.wait_for_url(f"{self.base}/", timeout=20000)
        self.assertEqual([e["fmt"] for e in _Stub.exported], ["png", "jpg"])
        self.assertNotIn("big", _Stub.docs["designs"][-1]["exp"], "JPG 로 다시 저장하면 '저장됨'")
        self.assertEqual(self.errs, [])

    def test_top_bar_one_line_at_1280(self):
        """앱 창(1320, 줄인 화면은 더 좁음)에서 긴 상태 글이 '저장됨'·영상 이름을 밀지 않음 — 상태 글이 먼저 말줄임 (D-497)."""
        self.page.set_viewport_size({"width": 1280, "height": 860})
        pg = self.open()
        pg.locator(".cand").first.click()
        pg.wait_for_timeout(800)
        pg.evaluate("D.exp = { sig: 'old', file: 'x.jpg' }; OUTS.add('x.jpg'); renderExp();")
        self.assertEqual(self.state()[0], "stale")
        m = pg.evaluate("""(() => { const r = id => document.getElementById(id).getBoundingClientRect(), s = document.getElementById('saved'), e = document.getElementById('expState'), n = document.getElementById('pname');
          return { saved: r('saved').height, exps: r('expState').height, lh: parseFloat(getComputedStyle(s).fontSize), nameCut: n.scrollWidth > n.clientWidth + 1, top: r('saved').top - r('pname').top,
                   title: e.title, savedText: s.textContent }; })()""")
        self.assertLess(m["saved"], m["lh"] * 1.8, "'저장됨'은 한 줄")
        self.assertLess(m["exps"], m["lh"] * 1.8, "상태 글도 한 줄 (모자라면 말줄임)")
        self.assertFalse(m["nameCut"], "영상 이름은 예전처럼 다 보임")
        self.assertIn("고친 뒤 유튜브용 그림 다시 안 만듦", m["title"], "잘린 글은 마우스를 올리면 다 보임")
        self.assertEqual(m["savedText"], "저장됨")
        self.assertEqual(self.errs, [])


if __name__ == "__main__":
    unittest.main()
