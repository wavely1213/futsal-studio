"""스튜디오 화면(ui.html) 색 토큰: 어두운 화면에서도 경고·오류 글자(--danger)가 읽히게.

예전에는 --danger(#b42318)를 밝은 화면 :root 에만 두고 어두운 화면 블록에서 다시 정하지 않아, Windows 어두운 모드의
카드(#161b22) 위 빨간 글자가 2.63:1 이었다 ('감사 전엔 공개로 못 바꿔요' 경고·실패 안내 · WCAG 글자 기준 4.5:1).
실행: python3 -m unittest tests.test_ui_theme
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _blocks():
    """(밝은 화면 :root 토큰, 어두운 화면 :root 토큰) — {이름: 값}."""
    css = (ROOT / "ui.html").read_text(encoding="utf-8")
    light = re.search(r"<style>\s*:root\s*\{(.*?)\}", css, re.S).group(1)
    dark = re.search(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{(.*?)\}", css, re.S).group(1)

    def toks(body):
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        return {k: v.strip() for k, v in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body)}
    return toks(light), toks(dark)


def _lum(hexc):
    h = hexc.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class ThemeTokenTests(unittest.TestCase):
    def test_dark_block_redefines_every_color_token(self):
        """밝은 화면의 색 토큰은 어두운 화면에서도 모두 다시 정함 (빠지면 밝은 화면 색이 어두운 바탕에 그대로 남음)."""
        light, dark = _blocks()
        colors = [k for k, v in light.items() if v.startswith("#")]
        self.assertIn("danger", colors)
        self.assertEqual([k for k in colors if k not in dark], [])

    def test_danger_text_readable_in_both_themes(self):
        """경고·오류 글자(.err·.yt-red·.rerr·.sx-err·.up-cnt.over)가 카드·바탕·옅은 카드 위에서 4.5:1 이상."""
        for theme, toks in zip(("밝은 화면", "어두운 화면"), _blocks()):
            for bg in ("surface", "bg", "surface-2"):
                ratio = contrast(toks["danger"], toks[bg])
                self.assertGreaterEqual(ratio, 4.5, f"{theme} --danger 위 --{bg}: {ratio:.2f}:1")

    def test_dark_danger_was_too_dark_before(self):
        """재현: 밝은 화면 값을 어두운 카드에 그대로 쓰면 2.63:1 (이 시험이 막는 상황)."""
        light, dark = _blocks()
        self.assertLess(contrast(light["danger"], dark["surface"]), 3.0)

    def test_danger_icon_keeps_white_mark_visible(self):
        """실패 카드의 동그란 '!' 아이콘(흰 글자 · var(--danger) 바탕)은 그림 기준 3:1 이상."""
        for toks in _blocks():
            self.assertGreaterEqual(contrast("#ffffff", toks["danger"]), 3.0)


if __name__ == "__main__":
    unittest.main()
