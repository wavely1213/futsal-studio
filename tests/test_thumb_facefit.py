"""예전 템플릿 칸이 옆에 선 감독님 얼굴을 담는지 (백로그 343 확인 · D-092·D-270·D-273 · D-493) — 저장소 폴더에서 python3 -m unittest tests.test_thumb_facefit

백로그 재현 그대로: 얼굴이 왼쪽 [0.09, 0.11, 0.15, 0.29] 인 장면 → '자동 제작' 예전 템플릿 롱폼 14개·쇼츠 10개 후보의 장면 칸마다
얼굴이 보이는 비율. 템플릿 그대로(가운데 자르기 · v2.8.0)는 얼굴 0% 칸이 있고, 지금(anchorTpl)은 0% 도 70% 아래도 없음.
진짜 브라우저(Playwright + Chromium)와 작은 가짜 서버(tests.test_thumb_exported 의 것)로 thumb.html 을 띄움 · 없으면 건너뜀."""
import shutil
import subprocess
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_thumb_exported import CHROME, NAME, _ffmpeg, _Stub, sync_playwright  # noqa: E402

LEFT = {"t": 1.0, "persons": [[0.02, 0.06, 0.3, 0.92, 0.9]], "main": 0, "faces": [{"box": [0.09, 0.11, 0.15, 0.29], "score": 0.9}], "face": 0.29}
FRAMES = [LEFT,
          {"t": 3.0, "persons": [[0.3, 0.1, 0.3, 0.85, 0.9]], "main": 0, "faces": [{"box": [0.4, 0.15, 0.1, 0.25], "score": 0.9}], "face": 0.25},
          {"t": 5.0, "persons": [[0.55, 0.1, 0.25, 0.85, 0.9]], "main": 0, "faces": [{"box": [0.62, 0.14, 0.09, 0.22], "score": 0.9}], "face": 0.22},
          {"t": 7.0, "persons": [[0.05, 0.08, 0.28, 0.9, 0.9]], "main": 0, "faces": [{"box": [0.12, 0.1, 0.12, 0.28], "score": 0.9}], "face": 0.28}]


class _FaceStub(_Stub):
    def do_POST(self):
        if self.path == "/api/thumb/frames":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return self._send(200, {"ok": True, "frames": FRAMES})
        return super().do_POST()


MEASURE = r"""(fmt) => {
  AUTO_FMT = fmt; renderAuto();
  const f = FMT[fmt], fr = FRAMES.map(x => x.t), sets = [fr, [...fr.slice(2), ...fr.slice(0, 2)]], ar = frameAspect(), cv = { x: 0, y: 0, w: f.w, h: f.h };
  const out = {};
  for (const [tag, anchored] of [["before", false], ["now", true]]) {
    let cells = 0, zero = 0, low = 0, cands = 0; const where = [];
    for (const [name, fn] of Object.entries(TPL[fmt])) for (let k = 0; k < 2; k++) {
      cands++;
      const ls = fn(sets[k], "감독님", "꿀팁", null), layers = anchored ? anchorTpl(ls, sets[k], { w: f.w, h: f.h }) : ls;
      for (const l of layers) {
        if (l.type !== "image" || l.hidden || frameT(l.src) == null) continue;
        const m = frameMeta(frameT(l.src)), fc = m.faces[0].box, bx = boxC(l, fc, ar);
        const p = areaOf(clipTo(clipTo(bx, { x: l.x, y: l.y, w: l.w, h: l.h }), cv)) / areaOf(bx);
        cells++; if (p <= 0.001) { zero++; where.push(name + ":" + l.name); } else if (p < 0.7) low++;
      }
    }
    out[tag] = { cands, cells, zero, low, where };
  }
  return out;
}"""


@unittest.skipUnless(sync_playwright and _ffmpeg(), "Playwright·ffmpeg 가 없어 화면 시험을 건너뜀")
class FaceFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(__import__("tempfile").mkdtemp())
        p = tmp / "f.jpg"
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=1", "-frames:v", "1", "-q:v", "3", str(p)], check=True)
        _Stub.frame = p.read_bytes()
        shutil.rmtree(tmp, ignore_errors=True)
        _Stub.docs, _Stub.outs = {"designs": []}, []
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FaceStub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROME) if Path(CHROME).exists() else cls.pw.chromium.launch()
        cls.page = cls.browser.new_page(viewport={"width": 1500, "height": 900})
        cls.errs = []
        cls.page.on("pageerror", lambda e: cls.errs.append(str(e)))
        cls.page.goto(f"http://127.0.0.1:{cls.srv.server_address[1]}/thumb?name=" + quote(NAME))
        cls.page.wait_for_function("document.getElementById('saved').textContent === '저장됨' && FRAMES.length === 4", timeout=60000)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()

    def check(self, fmt, n_cands):
        r = self.page.evaluate(MEASURE, fmt)
        b, n = r["before"], r["now"]
        self.assertEqual(n["cands"], n_cands)
        self.assertGreater(b["zero"], 0, f"재현 확인: 가운데 자르기는 얼굴 0% 칸이 있음 {b}")
        self.assertEqual((n["zero"], n["low"]), (0, 0), f"지금은 모든 칸이 얼굴을 70% 넘게 담음 {n}")
        self.assertEqual(n["cells"], b["cells"], "칸 수는 그대로 (자르는 자리·장면만 바뀜)")
        return r

    def test_long_templates(self):
        self.check("long", 14)

    def test_short_templates(self):
        r = self.check("short", 10)
        self.assertTrue(any(w.startswith("쇼츠 · 인물 강조") for w in r["before"]["where"]), "백로그: '인물 강조' 후보에도 사람이 없었음")
        self.assertEqual(self.errs, [])

    def test_made_candidates_use_anchor(self):
        """화면의 후보(makeCands)가 그 자르기를 씀 — 첫 후보 배경은 얼굴 쪽(왼쪽)으로."""
        self.page.evaluate("AUTO_FMT = 'short'; renderAuto();")
        fx = self.page.evaluate("CANDS[0].doc.layers.find(l => l.name === '배경').fx")
        self.assertLess(fx, 0.2, "쇼츠 칸은 왼쪽 얼굴 쪽을 자름 (가운데 0.5 아님)")


if __name__ == "__main__":
    unittest.main()
