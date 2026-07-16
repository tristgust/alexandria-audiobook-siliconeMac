from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = (
    ROOT
    / "benchmarks"
    / "run_benchmarks.py"
)

spec = importlib.util.spec_from_file_location(
    "benchmark_runner_speaker_metrics",
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


class BenchmarkSpeakerAliasTests(
    unittest.TestCase
):
    def load_expected(self, case_id):
        return runner.load_json(
            runner.BENCHMARK_DIR
            / "expected"
            / f"{case_id}.json"
        )

    def test_sen_is_a_defensible_doctor_sen_alias(self):
        expected = self.load_expected(
            "aliases_titles"
        )
        actual = [
            dict(entry)
            for entry in expected[
                "reference_entries"
            ]
        ]

        actual[-1]["speaker"] = "SEN"

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

    def test_the_khepri_is_alias_but_misspelling_is_not(self):
        expected = self.load_expected(
            "nonhuman_characters"
        )
        correct = [
            dict(entry)
            for entry in expected[
                "reference_entries"
            ]
        ]
        misspelled = [
            dict(entry)
            for entry in expected[
                "reference_entries"
            ]
        ]

        for entry in correct:
            if entry["speaker"] != "NARRATOR":
                entry["speaker"] = (
                    "THE KHEPRI"
                )

        for entry in misspelled:
            if entry["speaker"] != "NARRATOR":
                entry["speaker"] = (
                    "THE KHOPRI"
                )

        correct_metrics = (
            runner.aligned_script_metrics(
                expected[
                    "reference_entries"
                ],
                correct,
                expected,
            )
        )
        misspelled_metrics = (
            runner.aligned_script_metrics(
                expected[
                    "reference_entries"
                ],
                misspelled,
                expected,
            )
        )

        self.assertEqual(
            correct_metrics[
                "speaker_accuracy"
            ],
            1.0,
        )
        self.assertLess(
            misspelled_metrics[
                "speaker_accuracy"
            ],
            1.0,
        )

    def test_doctor_title_variant_is_canonical(self):
        expected = self.load_expected(
            "multi_chunk_continuity"
        )
        actual = [
            dict(entry)
            for entry in expected[
                "reference_entries"
            ]
        ]

        for entry in actual:
            if "DOCTOR" in entry["speaker"]:
                entry["speaker"] = "DOCTOR"

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

    def test_chapter_roman_numeral_does_not_reduce_role_score(self):
        expected = self.load_expected(
            "chapter_headings"
        )
        actual = [
            {
                "speaker": "NARRATOR",
                "text": (
                    "CHAPTER I\n"
                    "THE SIGNAL\n\n"
                    "The station had been silent "
                    "for thirty years."
                ),
                "instruct": (
                    "Neutral, even narration."
                ),
            }
        ]

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
            metrics[
                "narrator_dialogue_accuracy"
            ],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
