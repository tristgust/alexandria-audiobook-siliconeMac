from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
ROUTES = (STATIC / "navigation_routes.js").read_text(encoding="utf-8")
APP_SHELL = (STATIC / "app_shell.js").read_text(encoding="utf-8")
STYLE_PATH = STATIC / "styles/pages/settings_more.css"
MODULES = (
    "pages/settings.js",
    "pages/maintenance.js",
    "pages/more.js",
    "specialists/advanced_character_operations.js",
    "specialists/voice_designer.js",
    "specialists/audio_preparer.js",
    "specialists/dataset_builder.js",
    "specialists/voice_training.js",
    "specialists/model_cache.js",
    "specialists/help_center.js",
)


class SupportingDestinationsInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = {
            relative: (STATIC / relative).read_text(encoding="utf-8")
            for relative in MODULES
        }
        cls.styles = STYLE_PATH.read_text(encoding="utf-8")

    def test_every_supporting_destination_has_one_direct_module(self) -> None:
        for relative, source in self.sources.items():
            with self.subTest(module=relative):
                self.assertIn("export async function mount", source)
                self.assertIn("dataRouteOwner", source)
                self.assertIn("data-state-region", source)
                self.assertIn(relative, APP_SHELL)

    def test_specialist_routes_are_more_routes_with_stable_context(self) -> None:
        for tool in (
            "advanced-character-operations",
            "voice-designer",
            "audio-preparer",
            "dataset-builder",
            "voice-training",
            "maintenance",
            "model-cache",
            "help-center",
        ):
            self.assertIn(f"'more/{tool}'", ROUTES)
        specialist_text = "\n".join(
            source for name, source in self.sources.items()
            if name.startswith("specialists/")
        )
        for phrase in ("route.context", "data-support-return", "shell.navigate"):
            self.assertIn(phrase, specialist_text)

    def test_no_module_uses_legacy_dom_or_unsafe_markup(self) -> None:
        combined = "\n".join(self.sources.values())
        for forbidden in (
            "canonical_interface",
            "legacy-tab-store",
            "data-tab-panel",
            "activateWorkspaceTab",
            "VoiceCardBridge",
            "innerHTML",
            "insertAdjacentHTML",
        ):
            self.assertNotIn(forbidden, combined)

    def test_styles_use_existing_tokens_and_no_new_raw_visual_values(self) -> None:
        self.assertIn("var(--color-", self.styles)
        self.assertIn("var(--space-", self.styles)
        self.assertNotRegex(self.styles, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotIn("linear-gradient", self.styles)
        self.assertNotIn("radial-gradient", self.styles)

    def test_all_modules_are_valid_javascript(self) -> None:
        for relative in MODULES:
            with self.subTest(module=relative):
                subprocess.run(
                    ["node", "--check", str(STATIC / relative)],
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
