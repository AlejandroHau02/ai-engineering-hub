"""WCAG 2.x contrast of the SYNCODE Desktop color tokens (light and dark themes).

Reads the real tokens from desktop/ui/styles.css. Text pairs need 4.5:1, icons
and focus/borders that carry meaning need 3:1 (WCAG 1.4.3 and 1.4.11). This
checks the palette, not every rendered pixel; rendered evidence is in CI.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

CSS = Path(__file__).resolve().parents[1] / "desktop" / "ui" / "styles.css"


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def themes() -> dict[str, dict[str, str]]:
    css = CSS.read_text(encoding="utf-8")
    light = _tokens(css[css.index(":root {"): css.index("}", css.index(":root {"))])
    dark_start = css.index("@media (prefers-color-scheme: dark)")
    dark_root = css.index(":root {", dark_start)
    dark = {**light, **_tokens(css[dark_root: css.index("}", dark_root)])}
    return {"light": light, "dark": dark}


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TEXT_PAIRS = [
    ("text", "bg"), ("text", "surface"), ("text", "surface-2"), ("muted", "bg"),
    ("muted", "surface"), ("muted", "surface-2"), ("accent-text", "accent"),
    ("accent", "surface"), ("text", "block-bg"), ("text", "info-bg"), ("text", "warn-bg"),
    ("text", "fail-bg"), ("muted", "fail-bg"), ("block", "surface"), ("block", "block-bg"),
]
NON_TEXT_PAIRS = [
    ("ok", "surface"), ("warn", "surface"), ("warn", "warn-bg"), ("info", "info-bg"),
    ("block", "block-bg"), ("neutral", "surface"), ("neutral", "fail-bg"), ("focus", "bg"),
    ("focus", "surface"), ("accent", "bg"),
]


class ContrastTests(unittest.TestCase):
    def test_text_pairs_meet_aa(self):
        for theme, tokens in themes().items():
            for fg, bg in TEXT_PAIRS:
                with self.subTest(theme=theme, pair=(fg, bg)):
                    self.assertGreaterEqual(ratio(tokens[fg], tokens[bg]), 4.5)

    def test_non_text_pairs_meet_3_to_1(self):
        for theme, tokens in themes().items():
            for fg, bg in NON_TEXT_PAIRS:
                with self.subTest(theme=theme, pair=(fg, bg)):
                    self.assertGreaterEqual(ratio(tokens[fg], tokens[bg]), 3.0)

    def test_parser_reads_both_themes(self):
        parsed = themes()
        self.assertNotEqual(parsed["light"]["bg"], parsed["dark"]["bg"])
        self.assertIn("accent-text", parsed["dark"])


if __name__ == "__main__":
    unittest.main()
