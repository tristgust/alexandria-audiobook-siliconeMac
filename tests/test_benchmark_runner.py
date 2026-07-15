from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = (
    ROOT
    / "benchmarks"
    / "run_benchmarks.py"
)

spec = importlib.util.spec_from_file_location(
    "alexandria_benchmark_runner",
    RUNNER_PATH,
)

if spec is None or spec.loader is None:
    raise RuntimeError(
        "Unable to import benchmark runner"
    )

runner = importlib.util.module_from_spec(
    spec
)
spec.loader.exec_module(runner)


class BenchmarkRunnerManifestTests(
    unittest.TestCase
):
    def test_manifest_validation(self):
        manifest = runner.load_json(
            runner.DEFAULT_MANIFEST
        )
        cases = runner.validate_manifest(
            manifest
        )

        self.assertEqual(
            len(cases),
            17,
        )

    def test_validate_references(self):
        manifest = runner.load_json(
            runner.DEFAULT_MANIFEST
        )
        cases = runner.validate_manifest(
            manifest
        )

        self.assertEqual(
            runner.validate_references(
                cases
            ),
            [],
        )

    def test_cli_validate_only_has_no_model_calls(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(
            runner.APP_DIR
        )

        result = subprocess.run(
            [
                sys.executable,
                str(RUNNER_PATH),
                "--validate-only",
                "--case",
                "interrupted_dialogue",
                "--case",
                "review_contextual",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            result.returncode,
            0,
            result.stderr,
        )
        self.assertIn(
            "Benchmark corpus validation: PASS",
            result.stdout,
        )
        self.assertIn(
            "Selected cases: 2",
            result.stdout,
        )


class BenchmarkQualityMetricTests(
    unittest.TestCase
):
    def load_expected(self, case_id):
        return runner.load_json(
            runner.EXPECTED
            / f"{case_id}.json"
        )

    def test_perfect_script_metrics(self):
        expected = self.load_expected(
            "interrupted_dialogue"
        )
        entries = expected[
            "reference_entries"
        ]

        metrics = (
            runner.aligned_script_metrics(
                entries,
                entries,
                expected,
            )
        )

        self.assertEqual(
            metrics["missing_word_count"],
            0,
        )
        self.assertEqual(
            metrics["extra_word_count"],
            0,
        )
        self.assertEqual(
            metrics["speaker_accuracy"],
            1.0,
        )
        self.assertEqual(
            metrics[
                "narrator_dialogue_accuracy"
            ],
            1.0,
        )
        self.assertEqual(
            metrics["punctuation_accuracy"],
            1.0,
        )

    def test_script_metrics_detect_missing_text_and_speaker(self):
        expected = self.load_expected(
            "interrupted_dialogue"
        )
        actual = [
            dict(
                expected[
                    "reference_entries"
                ][0]
            ),
            dict(
                expected[
                    "reference_entries"
                ][1]
            ),
        ]
        actual[0]["speaker"] = "NARRATOR"

        metrics = (
            runner.aligned_script_metrics(
                expected[
                    "reference_entries"
                ],
                actual,
                expected,
            )
        )

        self.assertGreater(
            metrics["missing_word_count"],
            0,
        )
        self.assertLess(
            metrics["speaker_accuracy"],
            1.0,
        )
        self.assertLess(
            metrics[
                "narrator_dialogue_accuracy"
            ],
            1.0,
        )

    def test_alias_consistency_accepts_declared_alias(self):
        expected = self.load_expected(
            "aliases_titles"
        )
        actual = [
            dict(entry)
            for entry in expected[
                "reference_entries"
            ]
        ]

        actual[1]["speaker"] = (
            "PROFESSOR ILYAN"
        )

        metrics = (
            runner.aligned_script_metrics(
                expected[
                    "reference_entries"
                ],
                actual,
                expected,
            )
        )

        self.assertEqual(
            metrics["speaker_accuracy"],
            1.0,
        )
        self.assertEqual(
            metrics["alias_consistency"],
            1.0,
        )

    def test_review_metrics_detect_punctuation_change(self):
        target = [
            {
                "speaker": "MARA",
                "text": "Wait!",
                "instruct": "Urgent.",
            }
        ]
        actual = [
            {
                "speaker": "MARA",
                "text": "Wait.",
                "instruct": "Urgent.",
            }
        ]

        metrics = (
            runner.review_quality_metrics(
                target,
                actual,
            )
        )

        self.assertFalse(
            metrics["exact_text_match"]
        )
        self.assertLess(
            metrics["punctuation_accuracy"],
            1.0,
        )


class BenchmarkRequestMetricTests(
    unittest.TestCase
):
    def test_weighted_request_rates(self):
        summary = runner.summarize_requests(
            [
                {
                    "status": "success",
                    "validation_mode": "direct",
                    "metrics": {
                        "prompt_tokens": 100,
                        "prompt_tokens_per_second": 200,
                        "output_tokens": 50,
                        "output_tokens_per_second": 50,
                    },
                },
                {
                    "status": "success",
                    "validation_mode": (
                        "corrective_retry"
                    ),
                    "metrics": {
                        "prompt_tokens": 100,
                        "prompt_tokens_per_second": 100,
                        "output_tokens": 50,
                        "output_tokens_per_second": 25,
                    },
                },
            ]
        )

        self.assertEqual(
            summary["request_count"],
            2,
        )
        self.assertEqual(
            summary[
                "corrective_retry_requests"
            ],
            1,
        )
        self.assertEqual(
            summary[
                "internal_corrective_retry_rate"
            ],
            0.5,
        )
        self.assertAlmostEqual(
            summary[
                "prompt_tokens_per_second"
            ],
            200 / 1.5,
        )
        self.assertAlmostEqual(
            summary[
                "output_tokens_per_second"
            ],
            100 / 3,
        )

    def test_model_aggregate(self):
        case_runs = [
            {
                "kind": "script",
                "status": "success",
                "schema_success": True,
                "audit_passed": True,
                "elapsed_seconds": 2.0,
                "outer_retry_units": 1,
                "units": [{}, {}],
                "quality": {
                    "missing_word_count": 0,
                    "punctuation_accuracy": 1.0,
                    "speaker_accuracy": 1.0,
                    "narrator_dialogue_accuracy": 1.0,
                    "alias_consistency": None,
                },
                "requests": {
                    "request_count": 2,
                    "successful_request_count": 2,
                    "corrective_retry_requests": 1,
                    "prompt_tokens": 200,
                    "prompt_seconds_estimate": 1.0,
                    "output_tokens": 100,
                    "output_seconds_estimate": 2.0,
                },
            },
            {
                "kind": "review",
                "status": "success",
                "schema_success": True,
                "audit_passed": True,
                "elapsed_seconds": 1.0,
                "outer_retry_units": 0,
                "units": [{}],
                "quality": {
                    "missing_word_count": 0,
                    "punctuation_accuracy": 1.0,
                },
                "requests": {
                    "request_count": 1,
                    "successful_request_count": 1,
                    "corrective_retry_requests": 0,
                    "prompt_tokens": 100,
                    "prompt_seconds_estimate": 1.0,
                    "output_tokens": 50,
                    "output_seconds_estimate": 1.0,
                },
            },
        ]

        summary = (
            runner.aggregate_model_results(
                case_runs
            )
        )

        self.assertEqual(
            summary["schema_success_rate"],
            1.0,
        )
        self.assertEqual(
            summary[
                "script_audit_pass_rate"
            ],
            1.0,
        )
        self.assertEqual(
            summary[
                "review_audit_pass_rate"
            ],
            1.0,
        )
        self.assertEqual(
            summary["outer_retry_rate"],
            1 / 3,
        )
        self.assertEqual(
            summary[
                "prompt_tokens_per_second"
            ],
            150.0,
        )
        self.assertEqual(
            summary[
                "output_tokens_per_second"
            ],
            50.0,
        )


class BenchmarkOutputTests(unittest.TestCase):
    def test_atomic_json_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "result.json"
            )

            runner.atomic_json_write(
                path,
                {
                    "ok": True,
                },
            )

            self.assertEqual(
                json.loads(
                    path.read_text(
                        encoding="utf-8"
                    )
                ),
                {
                    "ok": True,
                },
            )


if __name__ == "__main__":
    unittest.main()
