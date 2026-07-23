from __future__ import annotations

import json
import unittest
from pathlib import Path

from help_center import inspect_help_center


ROOT = Path(__file__).resolve().parents[1]
HELP_DIR = ROOT / "docs" / "help"
HELP_UI = ROOT / "app/static/specialists/help_center.js"


class HelpContentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.help_source = HELP_UI.read_text(encoding="utf-8")
        cls.inventory = inspect_help_center(help_dir=HELP_DIR)
        cls.topics = {
            item["slug"]: item
            for item in cls.inventory["topics"]
        }
        cls.bodies = {}
        for slug in cls.topics:
            source = (HELP_DIR / f"{slug}.md").read_text(encoding="utf-8")
            marker = source.find("\n---\n", 4)
            cls.bodies[slug] = source[marker + 5 :].strip()

    def test_manifest_inventory_and_context_index_are_complete(self) -> None:
        self.assertEqual(self.inventory["bundle_version"], "1.1")
        self.assertEqual(
            list(self.topics),
            [
                "project-home",
                "script",
                "cast",
                "produce",
                "export",
                "voices-library",
                "settings",
                "maintenance",
                "model-cache",
            ],
        )
        expected_contexts = {
            "projects": "project-home",
            "new-project": "project-home",
            "script": "script",
            "script-review": "script",
            "cast": "cast",
            "voice-assignment": "cast",
            "produce": "produce",
            "audio-review": "produce",
            "export": "export",
            "publication-build": "export",
            "library": "voices-library",
            "voices": "voices-library",
            "templates": "voices-library",
            "settings": "settings",
            "accessibility": "settings",
            "maintenance": "maintenance",
            "migration": "maintenance",
            "model-cache": "model-cache",
            "cache-repair": "model-cache",
        }
        for context_id, slug in expected_contexts.items():
            self.assertEqual(
                self.inventory["context_index"].get(context_id),
                slug,
            )

    def test_visible_labels_match_current_product_contract(self) -> None:
        required = {
            "project-home": (
                "Script, Cast, Produce, and Export",
                "More > Maintenance",
                "recoverable Alexandria Trash",
            ),
            "script": (
                "Uncertain speaker",
                "Delivery direction",
                "Source mismatch",
                "Approve Script",
            ),
            "cast": (
                "Continue to Produce",
                "Production Voice assignment happens only in Cast",
                "never silently assign",
            ),
            "produce": (
                "Generate missing and stale audio",
                "Needs listening",
                "Current",
                "Regenerate all audio",
            ),
            "export": (
                "Build Audiobook",
                "M4B audiobook",
                "MP3 audio file",
                "Separate chapter files",
            ),
            "voices-library": (
                "Voices is a focused read-only view",
                "It never changes an assignment",
                "exact-name confirmation",
            ),
            "settings": (
                "Command-S or Control-S",
                "Not saved",
                "More > Maintenance",
            ),
            "maintenance": (
                "Maintenance is read-only first",
                "Review impact",
                "APPLY MIGRATION",
                "recoverable Alexandria Trash",
            ),
            "model-cache": (
                "Download or Repair",
                "does not silently download",
                "one cache operation at a time",
            ),
        }
        for slug, phrases in required.items():
            text = self.bodies[slug]
            for phrase in phrases:
                with self.subTest(slug=slug, phrase=phrase):
                    self.assertIn(phrase, text)

    def test_obsolete_or_unsupported_labels_do_not_return(self) -> None:
        all_text = "\n".join(self.bodies.values())
        for forbidden in (
            "Voice casting",
            "Generate Audio",
            "Regenerate All",
            "Assign to production voice",
            "Remove production assignment",
            "Setup → Local model cache",
            "restart Alexandria to switch projects",
            "Complete chunk state",
            "automatic model download",
        ):
            self.assertNotIn(forbidden, all_text)

    def test_bundle_contains_no_raw_html_or_external_help_urls(self) -> None:
        for slug, body in self.bodies.items():
            with self.subTest(slug=slug):
                self.assertNotRegex(body, r"<\s*/?\s*[A-Za-z][^>]*>")
                self.assertNotIn("http://", body)
                self.assertNotIn("https://", body)

    def test_manifest_hashes_are_lowercase_sha256(self) -> None:
        manifest = json.loads(
            (HELP_DIR / "manifest.json").read_text(encoding="utf-8")
        )
        for item in manifest["topics"]:
            self.assertRegex(item["content_sha256"], r"^[0-9a-f]{64}$")

    def test_help_center_is_direct_contextual_and_renders_text_safely(self) -> None:
        for phrase in (
            "export async function mount",
            'api.get("/api/help"',
            'api.get(`/api/help/',
            "document.createTextNode",
            "textContent",
            "aria-activedescendant",
            "data-support-return",
        ):
            self.assertIn(phrase, self.help_source)
        for forbidden in (
            "innerHTML",
            "insertAdjacentHTML",
            "marked.parse",
            "legacy-tab-store",
        ):
            self.assertNotIn(forbidden, self.help_source)


if __name__ == "__main__":
    unittest.main()
