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

    def test_danger_icon_mark_readable(self):
        """실패·꺼짐 카드의 동그란 '!' (굵은 13px 글자 · var(--danger) 바탕): 글자색을 카드 바탕색(--surface)으로 → 두 화면 모두 4.5:1 이상.
        검토 재현: 흰 글자는 어두운 화면 --danger(#f85149) 위 3.35:1."""
        css = (ROOT / "ui.html").read_text(encoding="utf-8")
        rule = re.search(r"\.fail \.fic \{([^}]*)\}", css).group(1)
        self.assertIn("background: var(--danger)", rule)
        self.assertIn("color: var(--surface)", rule)
        light, dark = _blocks()
        self.assertLess(contrast("#ffffff", dark["danger"]), 4.5, "재현: 예전 흰 글자")
        for toks in (light, dark):
            self.assertGreaterEqual(contrast(toks["surface"], toks["danger"]), 4.5)


def _editor_tokens():
    css = (ROOT / "editor.html").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", re.search(r":root\s*\{(.*?)\}", css, re.S).group(1), flags=re.S)
    return css, {k: v.strip() for k, v in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body)}


class EditorTextTests(unittest.TestCase):
    """편집실(늘 어두운 화면)에 E10 이 더한 글자: 마커 탭 안내·규칙 어긋남 · 내보낸 뒤 '다음 할 일' 설명 — 4.5:1 이상 (검토 재현: 2.84·3.09·4.21)."""

    def color_of(self, css, selector):
        m = re.search(re.escape(selector) + r"\s*\{[^}]*?color:\s*var\(--([\w-]+)\)", css)
        self.assertIsNotNone(m, selector)
        return m.group(1)

    def test_new_editor_text_contrast(self):
        css, t = _editor_tokens()
        for sel, bgs in ((".mkrow .prob", ("panel", "panel-2")), ("#lt-mk .hint", ("panel", "panel-2")), (".qanext .hint", ("panel-2",))):
            tok = self.color_of(css, sel)
            for bg in bgs:
                self.assertGreaterEqual(contrast(t[tok], t[bg]), 4.5, f"{sel} (--{tok}) 위 --{bg}")
        self.assertLess(contrast(t["ink-3"], t["panel-2"]), 4.5, "재현: 예전 --ink-3 안내")
        self.assertLess(contrast(t["danger"], t["panel"]), 4.5, "재현: 예전 --danger 글자")

    def test_danger_ink_is_the_studio_dark_value(self):
        """새 색을 만들지 않음: 편집실 --danger-ink = 스튜디오 어두운 화면 --danger."""
        self.assertEqual(_editor_tokens()[1]["danger-ink"], _blocks()[1]["danger"])


if __name__ == "__main__":
    unittest.main()
