from __future__ import annotations

import re
import unittest
from pathlib import Path


HTML_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "static"
    / "index.html"
)
MARKER = "/* Focus must not erase the native selected mark, switch thumb, or radio dot. */"


class CheckedControlVisibilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = HTML_PATH.read_text(encoding="utf-8")
        marker_position = cls.source.index(MARKER)
        style_end = cls.source.index("</style>", marker_position)
        cls.override = cls.source[marker_position:style_end]
        cls.marker_position = marker_position

    def test_selected_state_override_is_after_generic_focus_rules(self):
        generic_focus_position = self.source.rfind(
            ".form-check-input:focus",
            0,
            self.marker_position,
        )
        self.assertGreater(self.marker_position, generic_focus_position)

    def test_checked_indeterminate_focus_and_disabled_states_keep_fill(self):
        required_selectors = (
            ".form-check-input:checked",
            ".form-check-input:checked:focus",
            ".form-check-input:checked:focus-visible",
            ".form-check-input:checked:disabled",
            ".form-check-input:indeterminate",
            ".form-check-input:indeterminate:focus",
            ".form-check-input:indeterminate:focus-visible",
            ".form-check-input:indeterminate:disabled",
        )
        for selector in required_selectors:
            with self.subTest(selector=selector):
                self.assertIn(selector, self.override)

        selected_rule = re.search(
            r"\.form-check-input:checked,[\s\S]*?\{(?P<body>[^}]+)\}",
            self.override,
        )
        self.assertIsNotNone(selected_rule)
        body = selected_rule.group("body")
        self.assertIn("border-color: var(--alexandria-accent);", body)
        self.assertIn("background-color: var(--alexandria-accent);", body)

    def test_keyboard_focus_is_separate_from_selected_fill(self):
        self.assertIn(
            ".form-check-input:checked:focus-visible,\n"
            "        .form-check-input:indeterminate:focus-visible {\n"
            "            outline: 2px solid var(--alexandria-focus);",
            self.override,
        )
        self.assertIn("outline-offset: 2px;", self.override)
        self.assertIn("box-shadow:", self.override)

    def test_native_check_dot_and_switch_thumb_are_not_replaced(self):
        self.assertNotIn("background-image:", self.override)
        self.assertNotIn("background-position:", self.override)
        self.assertIn('class="form-check form-switch"', self.source)
        self.assertIn('type="radio"', self.source)
        self.assertIn('type="checkbox"', self.source)

    def test_disabled_selected_state_remains_legible(self):
        match = re.search(
            r"\.form-check-input:checked:disabled,\s*"
            r"\.form-check-input:indeterminate:disabled\s*"
            r"\{(?P<body>[^}]+)\}",
            self.override,
        )
        self.assertIsNotNone(match)
        opacity_match = re.search(r"opacity:\s*([0-9.]+)", match.group("body"))
        self.assertIsNotNone(opacity_match)
        self.assertGreaterEqual(float(opacity_match.group(1)), 0.7)

    def test_high_contrast_and_forced_colors_are_explicit(self):
        self.assertIn("@media (prefers-contrast: more)", self.override)
        self.assertIn("border-width: 2px;", self.override)
        self.assertIn("outline-width: 3px;", self.override)
        self.assertIn("@media (forced-colors: active)", self.override)
        self.assertIn("forced-color-adjust: auto;", self.override)
        self.assertIn("outline: 2px solid Highlight;", self.override)


if __name__ == "__main__":
    unittest.main()
