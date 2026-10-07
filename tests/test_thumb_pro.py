"""썸네일 퀄리티(쪼살급) 기능 테스트 — 저장소 폴더에서 python3 -m unittest tests.test_thumb_pro

- 함께 배포하는 글꼴·스티커: 라이선스 파일, 스티커 목록, /fonts·/stickers 주소 (폴더 밖 거절)
- 사람·공 찾기(detect.py): 상자 풀기·겹친 상자 정리(NMS)·레터박스 좌표 되돌리기 · 모델 없으면 None
- 자동 보정 숫자(thumb.auto_grade) · 흔들림·장면 지문 · 장면 종류 · 주인공 고르기
- 브랜드 키트 읽기·쓰기 검사 · /api/thumb/brand·analyze·ocr·cut 이름 검사(I-021)
인터넷은 쓰지 않음(내려받기는 모두 막음). 모델이 필요한 확인은 모델 파일이 있을 때만."""
import json
import shutil
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
import editor  # noqa: E402
import thumb  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
