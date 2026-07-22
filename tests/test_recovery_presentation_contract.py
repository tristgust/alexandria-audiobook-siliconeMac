from __future__ import annotations

import unittest
from pathlib import Path


HTML_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "static"
    / "index.html"
)


class RecoveryPresentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = HTML_PATH.read_text(encoding="utf-8")

    def test_recovery_stages_use_flat_rows_instead_of_status_cards(self):
        self.assertIn(
            ".recovery-stage-grid {\n"
            "            display: block;",
            self.source,
        )
        self.assertIn(
            "grid-template-areas:\n"
            "                \"heading summary actions\"",
            self.source,
        )
        self.assertIn("border-radius: 0;", self.source)
        self.assertIn("background: transparent;", self.source)

    def test_state_color_cannot_restore_a_left_edge_stripe(self):
        reset = ".recovery-stage-card[data-state] { border-left: 0; }"
        self.assertIn(reset, self.source)

        last_reset = self.source.rfind(reset)
        last_state_color = self.source.rfind(
            ".recovery-stage-card[data-state=\""
        )
        last_left_width = self.source.rfind("border-left-width:")
        self.assertGreater(last_reset, last_state_color)
        self.assertGreater(last_reset, last_left_width)

    def test_recovery_rows_stack_at_narrow_widths(self):
        self.assertIn("@media (max-width: 900px)", self.source)
        self.assertIn("Continue work", self.source)
        self.assertIn(
            "grid-template-areas:\n"
            "                    \"heading\"\n"
            "                    \"summary\"\n"
            "                    \"reason\"\n"
            "                    \"progress\"\n"
            "                    \"actions\";",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
