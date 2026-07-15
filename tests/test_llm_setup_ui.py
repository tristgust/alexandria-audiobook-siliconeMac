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

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)

        if "id" in attributes:
            self.ids.append(attributes["id"])


class LLMSetupUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = HTML_PATH.read_text(
            encoding="utf-8"
        )

        parser = IDParser()
        parser.feed(cls.source)
        cls.ids = parser.ids

    def test_all_runtime_controls_exist_once(self):
        expected = {
            "llm-url",
            "llm-key",
            "llm-model",
            "llm-backend",
            "llm-context-length",
            "llm-keep-alive",
            "llm-thinking",
            "llm-structured-output",
            "llm-corrective-retry",
            "llm-timeout",
        }

        for control_id in expected:
            with self.subTest(control_id=control_id):
                self.assertEqual(
                    self.ids.count(control_id),
                    1,
                )

    def test_backend_selector_has_supported_values(self):
        match = re.search(
            (
                r'<select[^>]+id="llm-backend"'
                r'[^>]*>(.*?)</select>'
            ),
            self.source,
            flags=re.DOTALL,
        )

        self.assertIsNotNone(match)

        values = set(
            re.findall(
                r'<option\s+value="([^"]+)"',
                match.group(1),
            )
        )

        self.assertEqual(
            values,
            {
                "auto",
                "ollama",
                "openai",
            },
        )

    def test_load_config_binds_every_runtime_field(self):
        expected = [
            "llm.base_url",
            "llm.api_key",
            "llm.model_name",
            "llm.backend",
            "llm.context_length",
            "llm.keep_alive",
            "llm.thinking",
            "llm.structured_output",
            "llm.corrective_retry",
            "llm.timeout",
        ]

        for fragment in expected:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_save_payload_contains_every_runtime_key(self):
        expected = [
            "base_url: llmBaseUrl",
            "api_key: llmApiKey",
            "model_name: llmModelName",
            (
                "backend: document.getElementById"
                "('llm-backend').value"
            ),
            "context_length: llmContextLength",
            "keep_alive: llmKeepAlive",
            (
                "thinking: document.getElementById"
                "('llm-thinking').checked"
            ),
            (
                "structured_output: "
                "document.getElementById"
                "('llm-structured-output').checked"
            ),
            (
                "corrective_retry: "
                "document.getElementById"
                "('llm-corrective-retry').checked"
            ),
            "timeout: llmTimeout",
        ]

        for fragment in expected:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_numeric_and_keep_alive_validation_exist(self):
        self.assertIn(
            "function positiveConfigInteger(",
            self.source,
        )
        self.assertIn(
            "function parseLLMKeepAlive(",
            self.source,
        )
        self.assertIn(
            "durationPattern",
            self.source,
        )

    def test_runtime_controls_are_before_tts_settings(self):
        llm_position = self.source.index(
            "LLM Runtime Settings"
        )
        tts_position = self.source.index(
            "TTS Settings (Voice Generation)"
        )

        self.assertLess(
            llm_position,
            tts_position,
        )

    def test_json_schemas_are_not_editable_controls(self):
        schema_ids = [
            control_id
            for control_id in self.ids
            if "schema" in control_id.lower()
        ]

        self.assertEqual(schema_ids, [])


if __name__ == "__main__":
    unittest.main()
