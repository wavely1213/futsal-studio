"""밖에서 작업 (원격 스튜디오 · D-230~D-234) — 주소 상수 · 세션 · 넘겨주기 · 확인 · 경계 · 페이지 끼워 넣기.

HTTP 시험은 개발 모드(FUTSAL_REMOTE_DEV=1) 원격 리스너를 임의 포트에 띄워 app 의 StudioHandler 와 같이 돌린다.
"""
import ast
import unittest
from pathlib import Path

import remote

ROOT = Path(__file__).resolve().parent.parent


class SiteConstantTest(unittest.TestCase):
    """remote.SITE 하나가 주소의 원본 (D-233) — 허용 출처·알림 글·PC 화면 안내가 여기서 나옴."""

    def test_origins_derived_from_site(self):
        self.assertEqual(remote.ORIGINS, remote.derived_origins(remote.SITE))
        self.assertEqual(remote.derived_origins("https://mulgyeol.kr/futsal"), ("https://mulgyeol.kr", "https://www.mulgyeol.kr"))
        # 따로 떼어 낸 주소로 옮기면 그 출처 하나만 (와벨리 출처는 더 이상 허용하지 않음)
        self.assertEqual(remote.derived_origins("https://futsal.mulgyeol.kr"), ("https://futsal.mulgyeol.kr",))
        self.assertEqual(remote.derived_origins("https://www.mulgyeol.kr/futsal"), ("https://www.mulgyeol.kr",))
        self.assertEqual(remote.derived_origins("http://127.0.0.1:8973/futsal"), ("http://127.0.0.1:8973",))
        self.assertEqual(remote.derived_origins(""), ())
        self.assertNotIn("https://futsal.mulgyeol.kr", remote.ORIGINS)  # 예전에 미리 허용해 둔 자리는 뺌 (옮길 때 저절로 생김)

    def test_moved_note_uses_site(self):
        self.assertIn(remote.SITE_TEXT, remote.NOTE_TEXT["moved"])
        self.assertEqual(remote.SITE_TEXT, remote.SITE.split("://", 1)[1])

    def test_no_other_literal(self):
        """주소 글자는 remote.SITE 한 곳에만 — 화면(ui.html)·다른 모듈에 따로 적지 않음 (옮길 때 한 줄)."""
        for f in ("ui.html", "editor.html", "thumb.html", "app.py"):
            self.assertNotIn("mulgyeol.kr", (ROOT / f).read_text(encoding="utf-8"), f)
        tree = ast.parse((ROOT / "remote.py").read_text(encoding="utf-8"))
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        lits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "mulgyeol.kr" in n.value and id(n) not in docs]
        self.assertEqual(lits, ["https://mulgyeol.kr/futsal"])  # SITE = … 한 줄 (설명 글·주석 말고는 없음)


if __name__ == "__main__":
    unittest.main()
