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

    def test_context_length_controls_accept_standard_1024_multiples(self):
        for control_id in (
            "llm-context-length",
            "llm-profile-context",
        ):
            with self.subTest(control_id=control_id):
                match = re.search(
                    rf'<input[^>]+id="{control_id}"[^>]*>',
                    self.source,
                )
                self.assertIsNotNone(match)
                markup = match.group(0)
                self.assertIn('min="1024"', markup)
                self.assertIn('step="1024"', markup)
                self.assertNotIn('min="1"', markup)

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

    def test_required_runtime_text_inputs_have_actual_defaults(self):
        expected_defaults = {
            "llm-model": "qwen3.5:35b-mlx",
            "llm-url": "http://localhost:11434/v1",
        }

        for control_id, expected_value in expected_defaults.items():
            with self.subTest(control_id=control_id):
                match = re.search(
                    rf'<input[^>]+id="{re.escape(control_id)}"[^>]*>',
                    self.source,
                )
                self.assertIsNotNone(match)
                tag = match.group(0)
                self.assertIn(' required', tag)
                self.assertIn(
                    f'value="{expected_value}"',
                    tag,
                )
                self.assertNotIn('placeholder=', tag)

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
            'id="llm-backend"'
        )
        tts_position = self.source.index(
            'id="tts-mode"'
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

    def test_system_readouts_fail_quietly_without_na_copy(self):
        self.assertIn(
            '<span id="sys-gpu-val" class="app-system-value">—</span>',
            self.source,
        )
        self.assertIn(
            '<span id="sys-disk-val" class="app-system-value">—</span>',
            self.source,
        )
        self.assertNotIn("gpuEl.textContent = 'N/A'", self.source)

    def test_obvious_setup_groups_do_not_repeat_their_headings(self):
        redundant_copy = (
            "Choose the language model and where Alexandria should reach it.",
            "Control context, availability, validation, and retry behavior.",
            "Choose where voices are synthesized and which language the engine should speak.",
            "Control parallel generation, repeatability, batching, and pauses in the finished audiobook.",
        )
        for text in redundant_copy:
            with self.subTest(text=text):
                self.assertNotIn(text, self.source)


if __name__ == "__main__":
    unittest.main()
