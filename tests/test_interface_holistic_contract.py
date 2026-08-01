from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "app" / "static" / "styles" / "tokens.css"
COMPONENTS = ROOT / "app" / "static" / "styles" / "components.css"
AUDIT = ROOT / "tests" / "interface_holistic_audit.js"


class InterfaceHolisticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tokens = TOKENS.read_text(encoding="utf-8")
        cls.components = COMPONENTS.read_text(encoding="utf-8")
        cls.audit = AUDIT.read_text(encoding="utf-8")

    def test_narrow_text_controls_prevent_mobile_focus_zoom(self) -> None:
        self.assertIn("--type-control-touch-size: 16px;", self.tokens)
        self.assertIn(
            '.app-shell[data-layout="narrow"] .field__control,\n'
            '.app-shell[data-layout="narrow"] .search-field__control {\n'
            '  font-size: var(--type-control-touch-size);\n'
            '}',
            self.components,
        )

    def test_rendered_audit_covers_primary_routes_and_layouts(self) -> None:
        for route in (
            "projects", "script", "cast", "produce", "export",
            "library", "voices", "templates", "settings", "more",
        ):
            self.assertIn(f"name: '{route}'", self.audit)
        for viewport in ("wide", "compact", "narrow"):
            self.assertIn(f"name: '{viewport}'", self.audit)
        for contract in (
            "horizontalOverflow",
            "visibleH1",
            "unlabeledControls",
            "unlabeledFields",
            "smallTargets",
            "mobileSmallInputs",
            "rawProjectIds",
            "legacyIcons",
            "genericCopy",
            "browserErrors",
        ):
            self.assertIn(contract, self.audit)

    def test_audit_source_has_valid_javascript_syntax(self) -> None:
        result = subprocess.run(
            ["node", "--check", str(AUDIT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
