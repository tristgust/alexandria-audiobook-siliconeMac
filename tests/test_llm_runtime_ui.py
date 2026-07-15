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


class LLMRuntimeUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = HTML_PATH.read_text(
            encoding="utf-8"
        )

        parser = IDParser()
        parser.feed(cls.source)
        cls.ids = parser.ids

    def test_runtime_panel_and_controls_exist_once(self):
        expected = {
            "llm-runtime-panel",
            "llm-runtime-badge",
            "llm-runtime-error",
            "btn-llm-refresh",
            "btn-llm-preload",
            "btn-llm-unload",
        }

        for element_id in expected:
            with self.subTest(element_id=element_id):
                self.assertEqual(
                    self.ids.count(element_id),
                    1,
                )

    def test_runtime_status_fields_exist_once(self):
        expected = {
            "llm-status-model",
            "llm-status-backend",
            "llm-status-mode",
            "llm-status-placement",
            "llm-status-loaded",
            "llm-status-context",
            "llm-status-keep-alive",
            "llm-status-timeout",
            "llm-status-thinking",
            "llm-status-structured",
            "llm-status-corrective",
            "llm-status-running-count",
            "llm-status-last-action",
            "llm-status-action-time",
            "llm-status-load-time",
            "llm-request-badge",
            "llm-request-error",
            "llm-request-contract",
            "llm-request-model",
            "llm-request-backend",
            "llm-request-validation",
            "llm-request-prompt-tokens",
            "llm-request-prompt-speed",
            "llm-request-output-tokens",
            "llm-request-output-speed",
            "llm-request-elapsed",
            "llm-request-provider-time",
            "llm-request-recorded",
            "llm-request-retry-reason",
            "llm-request-pipeline-stage",
            "llm-request-unit",
            "llm-request-outer-attempt",
            "llm-request-outer-retry",
            "llm-request-unit-time",
            "llm-request-audit-result",
            "llm-request-audit-details",
        }

        for element_id in expected:
            with self.subTest(element_id=element_id):
                self.assertEqual(
                    self.ids.count(element_id),
                    1,
                )

    def test_request_telemetry_renderer_exists(self):
        self.assertIn(
            "function renderLLMRequestTelemetry(",
            self.source,
        )
        self.assertIn(
            "status.telemetry",
            self.source,
        )
        self.assertIn(
            "snapshot.latest_request",
            self.source,
        )
        self.assertIn(
            "request.metrics",
            self.source,
        )

    def test_request_telemetry_covers_required_metrics(self):
        expected = [
            "request.contract",
            "request.model_name",
            "request.backend",
            "request.validation_mode",
            "request.retry_reason",
            "request.request_elapsed_seconds",
            "request.recorded_at",
            "metrics.prompt_tokens",
            "metrics.prompt_tokens_per_second",
            "metrics.output_tokens",
            "metrics.output_tokens_per_second",
            "metrics.total_duration_seconds",
        ]

        for fragment in expected:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_request_telemetry_has_empty_state(self):
        self.assertIn(
            "No request recorded",
            self.source,
        )
        self.assertIn(
            "if (!request)",
            self.source,
        )

    def test_request_validation_modes_are_readable(self):
        self.assertIn(
            "value === 'direct'",
            self.source,
        )
        self.assertIn(
            "value === 'corrective_retry'",
            self.source,
        )
        self.assertIn(
            "Corrective retry",
            self.source,
        )

    def test_request_token_speeds_include_units(self):
        self.assertIn(
            "tok/s",
            self.source,
        )

    def test_pipeline_telemetry_renderer_exists(self):
        expected = [
            "request.pipeline",
            "pipeline.stage",
            "pipeline.unit_kind",
            "pipeline.unit_index",
            "pipeline.unit_total",
            "pipeline.outer_attempt",
            "pipeline.outer_retry_used",
            "pipeline.unit_elapsed_seconds",
            "pipeline.audit_kind",
            "pipeline.audit_passed",
            "pipeline.retry_reason",
            "pipeline.audit",
        ]

        for fragment in expected:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_script_fidelity_metrics_are_rendered(self):
        expected = [
            "matched_segment_count",
            "source_segment_count",
            "exact_match_count",
            "tts_conversion_count",
            "attribution_clarification_count",
        ]

        for fragment in expected:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_review_text_metrics_are_rendered(self):
        expected = [
            "original_entry_count",
            "corrected_entry_count",
            "exact_text_match",
        ]

        for fragment in expected:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_audit_result_has_pass_blocked_states(self):
        self.assertIn(
            "return 'PASS'",
            self.source,
        )
        self.assertIn(
            "return 'BLOCKED'",
            self.source,
        )

    def test_pipeline_retry_reason_takes_precedence(self):
        self.assertIn(
            "pipeline.retry_reason",
            self.source,
        )
        self.assertIn(
            "|| request.retry_reason",
            self.source,
        )

    def test_status_endpoint_is_used(self):
        self.assertIn(
            "API.get('/api/llm/status')",
            self.source,
        )

    def test_lifecycle_endpoints_are_used(self):
        self.assertIn(
            "`/api/llm/${action}`",
            self.source,
        )
        self.assertIn(
            "runLLMLifecycleAction('preload')",
            self.source,
        )
        self.assertIn(
            "runLLMLifecycleAction('unload')",
            self.source,
        )

    def test_status_renderer_covers_required_runtime_fields(self):
        expected = [
            "status.model_name",
            "status.backend",
            "status.native_ollama",
            "status.processor_placement",
            "status.loaded",
            "status.warm",
            "status.context_length",
            "status.keep_alive",
            "status.timeout",
            "status.thinking",
            "status.structured_output",
            "status.corrective_retry",
            "status.running_models",
            "metrics.load_duration_seconds",
        ]

        for fragment in expected:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment,
                    self.source,
                )

    def test_status_loads_with_config(self):
        pattern = re.compile(
            r"const config = await API\.get"
            r"\('/api/config'\);\s*"
            r"loadLLMStatus\(\);"
        )

        self.assertRegex(
            self.source,
            pattern,
        )

    def test_status_refreshes_after_config_save(self):
        pattern = re.compile(
            r"await API\.post"
            r"\('/api/config', config\);\s*"
            r"await loadLLMStatus\(\);"
        )

        self.assertRegex(
            self.source,
            pattern,
        )

    def test_lifecycle_is_disabled_for_unsupported_backend(self):
        self.assertIn(
            "status.supports_lifecycle === true",
            self.source,
        )
        self.assertIn(
            "setLLMLifecycleButtons(false)",
            self.source,
        )

    def test_runtime_panel_precedes_tts_settings(self):
        runtime_position = self.source.index(
            'id="llm-runtime-panel"'
        )
        tts_position = self.source.index(
            "TTS Settings (Voice Generation)"
        )

        self.assertLess(
            runtime_position,
            tts_position,
        )


if __name__ == "__main__":
    unittest.main()
