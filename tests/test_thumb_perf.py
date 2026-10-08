"""썸네일 편집기 성능 최적화(thumb.html) 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_perf

진짜 브라우저(Playwright + Chromium)로 thumb.html 을 띄움. 서버는 테스트가 직접 띄우는 작은 가짜 서버
(문서·장면·그림·글꼴만 돌려줌, 인터넷 안 씀). 그림은 ffmpeg lavfi 로 만듦.
확인하는 것:
  - 화면 그리기 캐시(아래·위 레이어를 합쳐 둔 그림)를 써도 결과가 처음부터 다 그린 것과 같다 (반올림 오차 이내)
  - 끄는 동안 움직이는 레이어만 다시 그린다 / 되돌리기는 바로 전 효과 그림을 다시 쓴다
  - 글자 배치 기억(textLayout)이 글자·스타일이 바뀌면 다시 재고, 자간은 항상 캔버스에 맞춰 둔다
  - 레이어 패널은 바뀐 줄만 바꿔 끼운다 / 미리보기 그림은 바뀐 것만 다시 그린다
  - 이미지로 저장 결과가 처음부터 다 그린 그림(배율 1)의 JPG·PNG 와 똑같다 (화면 캐시가 끼어들지 않음)
Playwright 나 Chromium 이 없으면 건너뜀."""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
NAME = "20200101_PERFTEST001_썸네일 성능.mp4"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def make_media(d):
    """장면 3장(1280×720) · 큰 사진(2400×1600) · 누끼처럼 투명한 PNG — 모두 ffmpeg lavfi."""
    ff = _ffmpeg()
    if not ff:
        raise unittest.SkipTest("ffmpeg 가 없어요")
    out = {}
    for i, src in enumerate(["testsrc2=size=1280x720:rate=1", "smptehdbars=size=1280x720:rate=1", "mandelbrot=size=1280x720:rate=1"]):
        p = d / f"frame{i}.jpg"
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", src, "-frames:v", "1", "-q:v", "3", str(p)], check=True)
        out[f"f{i}"] = p
    p = d / "big.jpg"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=2400x1600:rate=1", "-vf", "noise=alls=20:allf=t", "-frames:v", "1", "-q:v", "3", str(p)], check=True)
    out["big"] = p
    p = d / "cut.png"  # 가운데 원만 보이는 투명 PNG
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=900x600:rate=1", "-vf",
                    "format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lt(hypot(X-450,Y-300),260),255,0)'",
                    "-frames:v", "1", str(p)], check=True)
    out["cut"] = p
    return out


class _Stub(BaseHTTPRequestHandler):
    """thumb.html 이 쓰는 주소만 흉내 냄."""
    media = {}
    saved = []
    exported = []

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
        q = parse_qs(u.query)
        if u.path == "/thumb":
            return self._send(200, (ROOT / "thumb.html").read_bytes(), "text/html; charset=utf-8")
        if u.path.startswith("/fonts/"):
            p = ROOT / "fonts" / Path(u.path).name
            return self._send(200, p.read_bytes(), "font/otf") if p.exists() else self._send(404, {})
        if u.path == "/frame":
            i = int(float(q["t"][0])) % 3
            return self._send(200, self.media[f"f{i}"].read_bytes(), "image/jpeg")
        if u.path.startswith("/asset/"):
            p = self.media.get(Path(u.path).stem)
            if not p:
                return self._send(404, {})
            return self._send(200, p.read_bytes(), "image/png" if p.suffix == ".png" else "image/jpeg")
        if u.path == "/api/thumb/open":
            return self._send(200, {"docs": {"designs": []}, "hooks": ["풋살 꿀팁 대방출"], "keywords": [], "info": {"duration": 60, "width": 1280, "height": 720}})
        if u.path == "/api/state":
            return self._send(200, {"job": None})
        if u.path == "/api/thumb/brand":  # 브랜드 키트 (편집기가 켤 때 읽음)
            return self._send(200, {"ok": True, "brand": {"logo": "", "logoPos": "tr", "colors": {"hl": "#FFE14D", "hl2": "#FFFFFF", "accent": "#FF3B30", "neon": "#00D1FF", "box": "#111111"},
                                                          "font": "Black Han Sans", "series": "", "seriesOn": False, "handle": "", "apply": True}})
        if u.path == "/api/thumb/style":  # 썸네일 스타일 (편집기가 켤 때 읽음 · D-130) — 고른 스타일 없음
            return self._send(200, {"ok": True, "view": {"styles": [], "pick": None, "active": None, "ours": {"n": 0}}, "sets": []})
        if u.path == "/favicon.ico":
            return self._send(200, b"", "image/x-icon")
        return self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        b = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/thumb/frames":
            return self._send(200, {"ok": True, "frames": [{"t": float(t)} for t in range(6)]})
        if self.path == "/api/thumb/save":
            _Stub.saved.append(b)
            return self._send(200, {"ok": True})
        if self.path == "/api/thumb/export":
            _Stub.exported.append(b)
            return self._send(200, {"ok": True, "file": "export.jpg"})
        return self._send(200, {"ok": True})


# 30개 가까운 레이어: 큰 사진·투명 PNG·효과 글자(테두리·그림자·광선·뒤틀기·돌출)·도형·혼합 모드·숨김·반투명
MKDOC = r"""
(() => {
  const A = n => '/asset/' + n, HD = dx => ({ on: true, color: '#000000', blur: 0, dx, dy: dx, opacity: 1 });
  const T = (text, o, x, y) => { const l = txt(text, o); l.x = x; l.y = y; return l; };
  const ls = [
    L('image', { name: '배경', src: A('big'), x: 0, y: 0, w: 1280, h: 720, bright: 92, contrast: 112, sat: 115 }),
    L('image', { name: '장면 2', src: frameSrc(1), x: 600, y: 0, w: 680, h: 720, fade: { on: true, angle: 180, start: 0.4, end: 0.95 } }),
    L('image', { name: '누끼', src: A('cut'), x: 100, y: 40, w: 900, h: 600, fit: 'contain', outline: { on: true, color: '#FFFFFF', width: 12 }, shadow: { on: true, color: '#000000', blur: 24, dx: 0, dy: 10, opacity: 0.85 } }),
    L('shape', { name: '아래 어둡게', x: 0, y: 300, w: 1280, h: 420, fill: 'rgba(0,0,0,0)', fill2: '#000000', gradAngle: 90, opacity: 0.85 }),
    L('shape', { name: '집중선', shape: 'burst', x: 0, y: 0, w: 1280, h: 720, fill: '#FFFFFF', opacity: 0.35, lines: 60, inner: 0.45 }),
    L('shape', { name: '원', shape: 'ellipse', x: 1040, y: 470, w: 180, h: 180, fill: '#FFD400', stroke: { color: '#FFFFFF', width: 6 }, glow: { on: true, color: '#FFE14D', size: 30, opacity: 1 } }),
    L('shape', { name: '숨긴 별', shape: 'star', x: 60, y: 420, w: 150, h: 150, fill: '#FFE14D', hidden: true }),
    T('큰 글씨 첫 줄', { size: 140, fill: '#FFE14D', align: 'center', strokes: [{ color: '#111111', width: 26 }, { color: '#FFFFFF', width: 0 }], shadow: HD(9) }, 220, 380),
    T('네온', { size: 110, fill: '#FFFFFF', strokes: [{ color: '#B26BFF', width: 10 }, { color: '#FFFFFF', width: 0 }], shadow: { on: false }, glow: { on: true, color: '#9B5CFF', size: 40, opacity: 1 } }, 760, 60),
    T('아치', { size: 90, fill: '#FFFFFF', strokes: [{ color: '#000000', width: 14 }, { color: '#FFFFFF', width: 0 }], shadow: { on: true, color: '#000000', blur: 12, dx: 0, dy: 6, opacity: 1 }, warp: { style: 'arc', bend: 0.3 } }, 380, 30),
    T('3D 입체', { size: 100, fill: '#FFE14D', fill2: '#FFB800', gradAngle: 90, strokes: [{ color: '#111111', width: 14 }, { color: '#FFFFFF', width: 0 }], extrude: { on: true, color: '#8A3B00', depth: 16, angle: 50 }, shadow: { on: true, color: '#000000', blur: 18, dx: 0, dy: 10, opacity: 0.7 } }, 60, 120),
    T('곱하기', { size: 120, fill: '#FF3EA5', blend: 'multiply', strokes: [{ color: '#000000', width: 0 }, { color: '#FFFFFF', width: 0 }], shadow: { on: false } }, 500, 140),
    T('반투명', { size: 90, fill: '#FFFFFF', opacity: 0.8, strokes: [{ color: '#000000', width: 16 }, { color: '#FFFFFF', width: 0 }], shadow: { on: false } }, 300, 600),
    T('자간 넓게', { size: 70, fill: '#FFFFFF', ls: 12, strokes: [{ color: '#000000', width: 8 }, { color: '#FFFFFF', width: 0 }], shadow: { on: false } }, 820, 330),
    T('맨 위 효과', { font: 'Pretendard Black', size: 110, fill: '#FFFFFF', fill2: '#FF8A00', gradAngle: 90, strokes: [{ color: '#111111', width: 22 }, { color: '#FFFFFF', width: 8 }], shadow: { on: true, color: '#000000', blur: 30, dx: 0, dy: 10, opacity: 0.9 }, glow: { on: true, color: '#FF3EA5', size: 20, opacity: 1 }, outline: { on: true, color: '#FFFFFF', width: 4 }, warp: { style: 'flag', bend: 0.2 } }, 330, 250),
  ];
  DOCS.designs.push({ id: nid(), name: '성능 테스트', doc: { w: 1280, h: 720, bg: '#000000', layers: ls } });
  openDesign(DOCS.designs.length - 1);
  return Promise.all(ls.filter(l => l.type === 'image').map(l => imgReady(l.src)));
})()
"""

# 메인 캔버스(캐시 경로) vs 처음부터 다 그린 그림
CMP = r"""
(() => {
  paint();
  const c = newCanvas(cv.width, cv.height); renderDoc(c.getContext('2d'), D.doc, RS);
  const a = ctx.getImageData(0, 0, cv.width, cv.height).data, b = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let s = 0, mx = 0; const w = cv.width, h = cv.height;
  for (let y = 0; y < h - 1; y++) for (let x = 0; x < w - 1; x++) for (let k = 0; k < 4; k++) {  // 마지막 줄·칸(반 픽셀)은 빼고
    const i = (y * w + x) * 4 + k, d = Math.abs(a[i] - b[i]); s += d; if (d > mx) mx = d;
  }
  return { mean: s / ((w - 1) * (h - 1) * 4), max: mx, last: STK.last };
})()
"""


@unittest.skipUnless(sync_playwright, "playwright 가 없어요")
class TestThumbPerf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="thumbperf_"))
        _Stub.media = make_media(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(executable_path=CHROME) if Path(CHROME).exists() else cls.pw.chromium.launch()
        except Exception as e:  # pragma: no cover
            cls.pw.stop()
            cls.srv.shutdown()
            raise unittest.SkipTest(f"Chromium 을 띄울 수 없어요: {e}")
        cls.page = cls.browser.new_page(viewport={"width": 1600, "height": 940})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.on("console", lambda m: cls.errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        cls.page.on("response", lambda r: cls.errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        cls.page.on("dialog", lambda d: d.accept())
        cls.page.goto(f"http://127.0.0.1:{cls.srv.server_address[1]}/thumb?name=" + quote(NAME))
        cls.page.wait_for_function("document.getElementById('saved').textContent === '저장됨' && FRAMES.length > 0", timeout=60000)
        cls.page.evaluate(MKDOC)
        cls.page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.srv.shutdown()
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def js(self, s, arg=None):
        return self.page.evaluate(s, arg) if arg is not None else self.page.evaluate(s)

    def assertSameAsFull(self, what):
        r = self.js(CMP)
        # 위쪽 덩어리를 따로 합치면 8비트 반올림이 조금 다를 수 있음 (평균 1/255 이하가 기준)
        self.assertLessEqual(r["mean"], 1.0, f"{what}: 평균 차이 {r['mean']:.4f}")
        self.assertLessEqual(r["max"], 8, f"{what}: 최대 차이 {r['max']}")
        return r

    def test_1_stack_cache_matches_full_render(self):
        js = self.js
        lid = lambda i: js(f"D.doc.layers[{i}].id")  # noqa: E731
        steps = [
            ("선택 없음", "select([])"),
            ("맨 위 레이어 선택", f"select(['{lid(14)}'])"),
            ("맨 위 레이어 이동", f"byId('{lid(14)}').x += 13.4; byId('{lid(14)}').y -= 5.2"),
            ("한 번 더 이동", f"byId('{lid(14)}').x += 7.7"),
            ("누끼 선택", f"select(['{lid(2)}'])"),
            ("누끼 이동", f"byId('{lid(2)}').x -= 21.3"),
            ("누끼 그림자 흐림", f"byId('{lid(2)}').shadow.blur = 40"),
            ("배경 밝기", f"byId('{lid(0)}').bright = 70"),
            ("곱하기 레이어 이동", f"byId('{lid(11)}').x += 30"),
            ("숨긴 별 보이기", f"byId('{lid(6)}').hidden = false"),
            ("레이어 지우기", "D.doc.layers.splice(4, 1)"),
            ("순서 바꾸기", "const a = D.doc.layers.splice(3, 1)[0]; D.doc.layers.splice(9, 0, a)"),
            ("맨 위에 복제 추가", f"const c = clone(byId('{lid(7)}')); c.id = 'dup1'; c.x += 50; normLayer(c); D.doc.layers.push(c); select(['dup1'])"),
            ("혼합 모드 바꾸기", f"byId('{lid(9)}').blend = 'screen'"),
            ("반투명 바꾸기", "D.doc.layers[2].opacity = 0.5"),
        ]
        for what, code in steps:
            js(f"(() => {{ {code}; }})()")
            self.assertSameAsFull(what)
        self.assertEqual(self.errors, [])

    def test_2_drag_redraws_only_the_moving_layer(self):
        js = self.js
        js("(() => { const l = D.doc.layers[D.doc.layers.length - 1]; select([l.id]); paint(); paint(); })()")
        n = js("""(() => {
          const l = D.doc.layers[D.doc.layers.length - 1], f = window.drawLayer; let n = 0;
          window.drawLayer = function () { n++; return f.apply(this, arguments); };
          try { for (let i = 0; i < 10; i++) { l.x += 3; paint(); } } finally { window.drawLayer = f; }
          return n;
        })()""")
        self.assertLessEqual(n, 10, f"끄는 10프레임 동안 레이어 {n}번 그림 (움직이는 것만 그려야 함)")
        last = js("STK.last")
        self.assertEqual(last["built"], 0)
        self.assertLessEqual(last["win"], 1)
        self.assertSameAsFull("끈 뒤")

    def test_3_undo_reuses_previous_effect_bitmap(self):
        js = self.js
        r = js("""(() => {
          const l = D.doc.layers.find(x => lname(x) === '네온'); select([l.id]); paint();
          const s = String(Math.min(2, 2 ** Math.ceil(Math.log2(Math.max(RS, 0.125))))), c0 = l._bm[s].c;  // 화면 배율 계단 (layerBitmap 과 같은 식)
          commit('광선'); l.glow.size = 70; paint();
          const c1 = byId(l.id)._bm[s].c;
          undo(); paint();
          const back = byId(l.id)._bm[s].c;
          redo(); paint();
          const fwd = byId(l.id)._bm[s].c;
          return { changed: c1 !== c0, undoReuse: back === c0, redoReuse: fwd === c1 };
        })()""")
        self.assertTrue(r["changed"], "효과를 바꾸면 그림을 새로 만들어야 함")
        self.assertTrue(r["undoReuse"], "되돌리기는 바로 전 효과 그림을 다시 써야 함")
        self.assertTrue(r["redoReuse"], "다시 실행도 그림을 다시 써야 함")
        self.assertSameAsFull("되돌리기 뒤")

    def test_4_text_layout_memo(self):
        r = self.js("""(() => {
          const l = D.doc.layers.find(x => lname(x) === '자간 넓게'), c = newCanvas(10, 10).getContext('2d');
          const m1 = textLayout(ctx, l), m2 = textLayout(c, l);
          const ls = c.letterSpacing;
          l.text = '자간 넓게!'; const m3 = textLayout(c, l);
          l.ls = 20; const m4 = textLayout(c, l), ls2 = c.letterSpacing;
          l.size = 90; const m5 = textLayout(c, l);
          // 기억한 결과가 새로 잰 것과 같은지
          const fresh = (() => { const k = l._tl; l._tl = null; const m = textLayout(c, l); l._tl = k; return m; })();
          return { same: m1 === m2, ls, wider: m3.natW > m2.natW, ls2, lsWider: m4.natW > m3.natW, bigger: m5.natH > m4.natH,
                   freshEq: JSON.stringify(fresh) === JSON.stringify(m5) };
        })()""")
        self.assertTrue(r["same"], "글자가 그대로면 지난번 배치를 써야 함")
        self.assertEqual(r["ls"], "12px", "기억한 배치를 써도 캔버스 자간은 맞춰 둬야 함")
        self.assertTrue(r["wider"])
        self.assertEqual(r["ls2"], "20px")
        self.assertTrue(r["lsWider"])
        self.assertTrue(r["bigger"])
        self.assertTrue(r["freshEq"])
        self.js("(() => { const l = D.doc.layers.find(x => lname(x).startsWith('자간')); l.text = '자간 넓게'; l.ls = 12; l.size = 70; fitText(l); })()")

    def test_5_layer_panel_replaces_only_changed_row(self):
        r = self.js("""(async () => {
          const l = D.doc.layers.find(x => lname(x) === '큰 글씨 첫 줄'); select([l.id]); renderLayers();
          const rows = () => [...document.querySelectorAll('#layers [data-l]')];
          const before = rows(), canv = before.map(r => r.querySelector('canvas'));
          startEdit(l, false); const ta = editing.ta; ta.value += '가'; ta.dispatchEvent(new Event('input', { bubbles: true }));
          const after = rows(), kept = after.filter((r, i) => r === before[i]).length, canvKept = after.filter((r, i) => r.querySelector('canvas') === canv[i]).length;
          const nm = after.find(r => r.dataset.l === l.id).querySelector('.nm').textContent;
          endEdit(); await new Promise(r => setTimeout(r, 200));
          // 순서를 바꾸면 전부 다시 만듦
          const n0 = rows().length; moveLayers(1); const n1 = rows().length, order = rows().map(r => r.dataset.l).join() === [...D.doc.layers].reverse().map(x => x.id).join();
          return { total: before.length, kept, canvKept, nm, n0, n1, order };
        })()""")
        self.assertEqual(r["kept"], r["total"] - 1, "글자를 친 줄만 바뀌어야 함")
        self.assertEqual(r["canvKept"], r["total"], "미리보기 그림은 옮겨 담아야 함")
        self.assertIn("가", r["nm"])
        self.assertEqual(r["n0"], r["n1"])
        self.assertTrue(r["order"])

    def test_6_layer_thumbs_and_design_thumbs_only_redraw_changes(self):
        r = self.js("""(async () => {
          const wait = ms => new Promise(r => setTimeout(r, ms)), f = window.drawLayer, g = window.renderDoc; let nl = 0, nd = 0;
          clearTimeout(saveTimer); await saveNow(); drawLayerThumbs(); renderDesigns(); await wait(500);  // 밀린 저장·그리기를 먼저 끝냄
          window.renderDoc = function (c) { if (c.canvas.closest('#designs')) nd++; return g.apply(this, arguments); };
          renderDesigns(); await wait(500); const dNone = nd;
          D.doc.layers[0].bright = 80; renderDesigns(); await wait(500); const dOne = nd - dNone;
          window.renderDoc = g;
          window.drawLayer = function () { nl++; return f.apply(this, arguments); };
          drawLayerThumbs(); const none = nl - (D.doc.layers[0].type === 'image' ? 1 : 0);  // 밝기 바꾼 배경 하나는 다시
          const n0 = nl; D.doc.layers[D.doc.layers.length - 1].x += 40; drawLayerThumbs(); const one = nl - n0;
          window.drawLayer = f;
          return { none, one, dNone, dOne, designs: DOCS.designs.length };
        })()""")
        self.assertEqual(r["none"], 0, "안 바뀐 레이어 미리보기는 다시 안 그림")
        self.assertEqual(r["one"], 1)
        self.assertEqual(r["dNone"], 0, "안 바뀐 디자인 미리보기는 다시 안 그림")
        self.assertEqual(r["dOne"], 1, "바뀐 디자인만 다시 그림")

    def test_7_export_bytes_same_as_before(self):
        r = self.js("""(async () => {
          const p = window.post; let sent = null;
          window.post = async (u, b) => { if (u === '/api/thumb/export') sent = b; return u === '/api/thumb/export' ? { ok: false } : { ok: true }; };
          try {
            $('exFmt').value = 'jpg'; await exportImg(); const jpg = sent;
            $('exFmt').value = 'png'; await exportImg(); const png = sent;
            $('exFmt').value = 'jpg';
            const c = newCanvas(W, H); renderDoc(c.getContext('2d'), D.doc, 1);
            return { jpg: jpg && jpg.data === c.toDataURL('image/jpeg', 0.93), png: png && png.data === c.toDataURL('image/png'), jpgHead: jpg && jpg.data.slice(0, 23) };
          } finally { window.post = p; }
        })()""")
        self.assertTrue(r["jpg"], "JPG 파일이 처음부터 다 그린 그림과 같아야 함")
        self.assertTrue(r["png"], "PNG 파일이 처음부터 다 그린 그림과 같아야 함")
        self.assertEqual(r["jpgHead"], "data:image/jpeg;base64,")

    def test_8_geometry_sync_leaves_other_fields(self):
        r = self.js("""(() => {
          const l = D.doc.layers.find(x => lname(x) === '네온'); select([l.id]); showTab('props');
          const x = document.querySelector('#tab-props input.num[data-k=x]'), bl = document.querySelector('#tab-props input.num[data-k="glow.size"]');
          bl.value = '1'; l.x += 5; syncFields(null, XFK_SET);
          const r1 = { x: +x.value, glow: bl.value };
          syncFields();
          return { ...r1, glowAfter: +bl.value, lx: Math.round(l.x * 10) / 10, size: l.glow.size };
        })()""")
        self.assertAlmostEqual(r["x"], r["lx"], places=1)
        self.assertEqual(r["glow"], "1", "위치만 맞출 때 다른 칸은 그대로")
        self.assertEqual(r["glowAfter"], r["size"])

    # 이름 바꾸기 칸이 열린 채로 다른 레이어를 고르면 줄을 바꿔 끼우다 blur 가 끼어들어 오류가 났음 (리뷰 지적)
    SELSTATE = """(() => { const l = byId(selIds[0]), sb = document.querySelector('#ui .selbox'), h = document.querySelector('#lspanel.show .lsbody h4');
      return { sel: selIds.slice(), box: sb ? Math.round(parseFloat(sb.style.left)) : null, want: l ? Math.round(l.x * Z) : null,
               tab: document.querySelector('.tab.on').dataset.tab, ls: h ? h.textContent : null, renaming: !!document.querySelector('#layers input.s') }; })()"""

    def _rename_open(self, name, typed):
        lid = self.js(f"D.doc.layers.find(x => lname(x) === {json.dumps(name)}).id")
        for attempt in range(3):  # 바쁜 PC(load 40+): 두 번 누르기 사이가 화면의 450ms 를 넘으면 한 번씩 누른 것 → 다시 두 번
            self.page.wait_for_timeout(500)  # 두 번 누르기 판정이 앞 동작과 섞이지 않게
            nm = self.page.locator(f'#layers [data-l="{lid}"] .nm')
            nm.click()
            self.page.wait_for_timeout(60)
            nm.click()
            try:
                self.page.wait_for_selector("#layers input.s", timeout=5000)
                break
            except Exception:  # playwright TimeoutError
                if self.page.query_selector("#layers input.s"):  # 늦게라도 열렸으면 그대로
                    break
                if attempt == 2:
                    raise
        self.page.keyboard.type(typed)  # 열릴 때 이름 전체가 골라져 있음
        return lid

    def test_8b_rename_then_select_elsewhere(self):
        js, e0 = self.js, len(self.errors)
        other = js("D.doc.layers.find(x => lname(x) === '맨 위 효과').id")
        # ① 코드로 다른 레이어 고르기
        lid = self._rename_open("원", "원 새이름")
        js(f"select(['{other}'])")
        r1 = js(self.SELSTATE)
        # ② 레이어 스타일 창을 연 채로 다른 줄을 마우스로 누르기
        js("toggleLS(true)")
        lid2 = self._rename_open("아래 어둡게", "어둡게 새이름")
        self.page.locator(f'#layers [data-l="{other}"] .ty').click()
        self.page.wait_for_timeout(300)
        r2 = js(self.SELSTATE)
        js("toggleLS(false)")
        # ③ 캔버스를 눌러도 오류 없이 끝남
        self._rename_open("원 새이름", "원")
        bx = self.page.locator("#cv").bounding_box()
        self.page.mouse.click(bx["x"] + 5, bx["y"] + 5)
        self.page.wait_for_timeout(300)
        r3 = js(self.SELSTATE)
        names = js(f"[lname(byId('{lid}')), lname(byId('{lid2}'))]")
        js(f"byId('{lid2}').name = '아래 어둡게'; renderLayers()")
        self.assertEqual(self.errors[e0:], [], "이름 바꾸던 중 다른 곳을 눌러도 오류가 없어야 함")
        for r in (r1, r2):
            self.assertEqual(r["sel"], [other])
            self.assertEqual(r["box"], r["want"], "선택 상자가 새로 고른 레이어에 있어야 함")
            self.assertEqual(r["tab"], "props")
            self.assertFalse(r["renaming"])
        self.assertTrue(r2["ls"] and r2["ls"].endswith("맨 위 효과"), f"레이어 스타일 창은 새로 고른 레이어: {r2['ls']}")
        self.assertFalse(r3["renaming"])
        self.assertEqual(names, ["원", "어둡게 새이름"], "바꾼 이름은 그대로 저장")

    # 혼합 모드 레이어가 번갈아 많으면 위쪽 덩어리가 잘게 쪼개져 화면 크기 캔버스가 수십 장 생기고 더 느려졌음 (리뷰 지적)
    def test_9a_many_blend_layers_bounded_segments(self):
        js = self.js
        js("""(() => {
          const ls = [L('shape', { name: '바탕', x: 0, y: 0, w: 1280, h: 720, fill: '#3366CC' })];
          for (let i = 0; i < 20; i++) ls.push(L('shape', { name: '작은 ' + i, x: 40 + i * 55, y: 200 + (i % 4) * 60, w: 80, h: 80,
            fill: i % 2 ? '#FF3EA5' : '#FFE14D', blend: i % 2 ? 'multiply' : 'source-over' }));
          DOCS.designs.push({ id: nid(), name: '혼합 많이', doc: { w: 1280, h: 720, bg: '#000000', layers: ls } });
          openDesign(DOCS.designs.length - 1); select([ls[0].id]); paint(); paint();
        })()""")
        for k in range(3):
            js("(() => { D.doc.layers[0].x += 7; paint(); })()")
            r = self.assertSameAsFull(f"혼합 많은 문서 바탕 이동 {k}")
        seg = js("""(() => { const a = STK.above; return a ? { cached: a.segs.filter(s => !s.direct).length, all: a.segs.length,
                    covered: a.segs.reduce((n, s) => n + s.b - s.a, 0), want: D.doc.layers.length - 1 } : null; })()""")
        self.assertIsNotNone(seg)
        self.assertLessEqual(seg["cached"], 2, f"합쳐 둔 화면 크기 캔버스가 {seg['cached']}장 (최대 2장)")
        self.assertEqual(seg["covered"], seg["want"], "위쪽 레이어가 빠짐없이 그려져야 함")
        self.assertEqual(r["last"]["built"], 0, "끄는 동안은 다시 합치지 않음")
        self.assertEqual(self.errors, [])

    def test_9_no_js_errors(self):
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
