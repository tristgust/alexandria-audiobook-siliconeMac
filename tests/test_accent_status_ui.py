from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


HTML_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "static"
    / "index.html"
)


class IDParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(
        self,
        tag,
        attrs,
    ):
        attributes = dict(attrs)

        if "id" in attributes:
            self.ids.append(
                attributes["id"]
            )


class AccentStatusUITests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        cls.source = HTML_PATH.read_text(
            encoding="utf-8"
        )

        parser = IDParser()
        parser.feed(cls.source)
        cls.ids = parser.ids

    def test_status_elements_exist_once(self):
        expected = {
            "design-accent-status",
            "design-accent-badge",
            "design-accent-summary",
            (
                "design-accent-"
                "native-language"
            ),
            (
                "design-accent-"
                "output-language"
            ),
            "design-accent-sequence",
        }

        for element_id in expected:
            with self.subTest(
                element_id=element_id
            ):
                self.assertEqual(
                    self.ids.count(
                        element_id
                    ),
                    1,
                )

    def test_status_row_follows_description(self):
        description_position = (
            self.source.index(
                'id="design-description"'
            )
        )
        status_position = (
            self.source.index(
                'id="design-accent-status"'
            )
        )
        sample_position = (
            self.source.index(
                'id="design-sample-text"'
            )
        )

        self.assertLess(
            description_position,
            status_position,
        )
        self.assertLess(
            status_position,
            sample_position,
        )

    def test_status_endpoint_is_used(self):
        self.assertIn(
            (
                "API.post('/api/voice_design/"
                "accent_status'"
            ),
            self.source,
        )
        self.assertIn(
            "description: description",
            self.source,
        )
        self.assertIn(
            "output_language: outputLanguage",
            self.source,
        )

    def test_status_refresh_is_debounced(self):
        self.assertIn(
            "function scheduleDesignAccentStatus(",
            self.source,
        )
        self.assertIn(
            "clearTimeout(designAccentStatusTimer)",
            self.source,
        )
        self.assertRegex(
            self.source,
            re.compile(
                r"setTimeout\(\s*"
                r"loadDesignAccentStatus,\s*"
                r"250\s*\)"
            ),
        )

    def test_description_and_language_trigger_status(self):
        self.assertIn(
            (
                "designDescriptionInput."
                "addEventListener"
            ),
            self.source,
        )
        self.assertIn(
            "'input',",
            self.source,
        )
        self.assertIn(
            (
                "designOutputLanguageInput."
                "addEventListener"
            ),
            self.source,
        )
        self.assertIn(
            "'change',",
            self.source,
        )

    def test_renderer_covers_all_status_fields(self):
        expected = [
            "status?.accent_detected",
            "status.accent_label",
            "status.native_language",
            "status?.output_language",
            "const resolvedOutput",
            (
                "Native seed design "
                "→ output clone"
            ),
            "Ordinary VoiceDesign",
            "Not used",
        ]

        for fragment in expected:
            with self.subTest(
                fragment=fragment
            ):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_stale_status_responses_are_ignored(self):
        self.assertIn(
            "designAccentStatusRequest",
            self.source,
        )
        self.assertIn(
            (
                "requestId !== "
                "designAccentStatusRequest"
            ),
            self.source,
        )

    def test_editor_refreshes_status(self):
        pattern = re.compile(
            r"window\.openVoiceDesignEditor"
            r".*?"
            r"scheduleDesignAccentStatus\(\);",
            flags=re.DOTALL,
        )

        self.assertRegex(
            self.source,
            pattern,
        )

    def test_preview_and_save_paths_are_unchanged(self):
        self.assertIn(
            (
                "API.post('/api/voice_design/"
                "preview'"
            ),
            self.source,
        )
        self.assertIn(
            (
                "API.post('/api/voice_design/"
                "save'"
            ),
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
