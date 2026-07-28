from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
MODULE_PATH = STATIC / "pages/community_qwen_packs.js"
STYLE_PATH = STATIC / "styles/pages/community_qwen_packs.css"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


class CommunityQwenPackInterfaceContractTests(unittest.TestCase):
    def test_styles_use_existing_tokens_and_are_loaded(self) -> None:
        styles = STYLE_PATH.read_text(encoding="utf-8")
        self.assertIn("community_qwen_packs.css", INDEX)
        self.assertIn("var(--space-", styles)
        self.assertIn("var(--color-", styles)
        self.assertNotRegex(styles, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotIn("linear-gradient", styles)
        self.assertIn("repeat(2", styles)
        self.assertIn("var(--color-error)", styles)
        self.assertNotIn("--listbox-max", styles)
        self.assertNotIn("--color-danger", styles)
        self.assertIn('data-layout="narrow"', styles)

    def test_modules_are_valid_javascript(self) -> None:
        for path in (
            MODULE_PATH,
            STATIC / "pages/community_qwen_pack_components.js",
            STATIC / "pages/voices.js",
            STATIC / "pages/cast_voice_assignment_form.js",
        ):
            subprocess.run(
                ["node", "--check", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
