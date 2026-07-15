from __future__ import annotations

import json
import unittest
from pathlib import Path

from review_audit import (
    audit_review_batch,
    normalize_review_text,
)
from script_audit import audit_script_chunk


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
MANIFEST_PATH = BENCHMARKS / "manifest.json"


REQUIRED_CASES = {
    "plain_narration",
    "simple_dialogue",
    "interrupted_dialogue",
    "inverted_attribution",
    "pronoun_attribution",
    "ambiguous_pronouns",
    "three_speakers",
    "aliases_titles",
    "internal_quotation_marks",
    "letters_read_aloud",
    "chapter_headings",
    "long_narration",
    "emotional_changes",
    "nonhuman_characters",
    "multi_chunk_continuity",
    "review_ordinary",
    "review_contextual",
}


def load_json(path: Path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


class BenchmarkManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_json(
            MANIFEST_PATH
        )
        cls.cases = cls.manifest["cases"]

    def test_manifest_schema_and_run_count(self):
        self.assertEqual(
            self.manifest["schema_version"],
            1,
        )
        self.assertEqual(
            self.manifest[
                "required_runs_per_model"
            ],
            3,
        )

    def test_manifest_has_every_required_case(self):
        actual = {
            case["id"]
            for case in self.cases
        }

        self.assertEqual(
            actual,
            REQUIRED_CASES,
        )

    def test_case_ids_are_unique(self):
        case_ids = [
            case["id"]
            for case in self.cases
        ]

        self.assertEqual(
            len(case_ids),
            len(set(case_ids)),
        )

    def test_case_files_exist(self):
        for case in self.cases:
            with self.subTest(
                case_id=case["id"]
            ):
                input_path = (
                    BENCHMARKS / case["input"]
                )
                expected_path = (
                    BENCHMARKS
                    / case["expected"]
                )

                self.assertTrue(
                    input_path.is_file()
                )
                self.assertTrue(
                    expected_path.is_file()
                )

    def test_results_directory_is_tracked(self):
        self.assertTrue(
            (
                BENCHMARKS
                / "results"
                / ".gitkeep"
            ).is_file()
        )


class ScriptBenchmarkReferenceTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        manifest = load_json(
            MANIFEST_PATH
        )

        cls.cases = [
            case
            for case in manifest["cases"]
            if case["kind"] == "script"
        ]

    def test_reference_entries_have_exact_shape(self):
        for case in self.cases:
            expected = load_json(
                BENCHMARKS / case["expected"]
            )

            for index, entry in enumerate(
                expected["reference_entries"]
            ):
                with self.subTest(
                    case_id=case["id"],
                    entry_index=index,
                ):
                    self.assertEqual(
                        set(entry),
                        {
                            "speaker",
                            "text",
                            "instruct",
                        },
                    )

                    for key in (
                        "speaker",
                        "text",
                        "instruct",
                    ):
                        self.assertIsInstance(
                            entry[key],
                            str,
                        )
                        self.assertTrue(
                            entry[key].strip()
                        )

    def test_reference_speaker_sequences_match(self):
        for case in self.cases:
            expected = load_json(
                BENCHMARKS / case["expected"]
            )

            actual = [
                entry["speaker"]
                for entry in expected[
                    "reference_entries"
                ]
            ]

            self.assertEqual(
                actual,
                expected["speaker_sequence"],
                case["id"],
            )

    def test_auditable_references_pass_production_audit(
        self,
    ):
        for case in self.cases:
            expected = load_json(
                BENCHMARKS / case["expected"]
            )

            if not expected.get(
                "audit_reference",
                True,
            ):
                continue

            source = (
                BENCHMARKS
                / case["input"]
            ).read_text(encoding="utf-8")

            result = audit_script_chunk(
                source,
                expected["reference_entries"],
            )

            with self.subTest(
                case_id=case["id"]
            ):
                self.assertTrue(
                    result.passed,
                    result.to_dict(),
                )

    def test_chapter_heading_boundaries_are_explicit(
        self,
    ):
        case = next(
            case
            for case in self.cases
            if case["id"]
            == "chapter_headings"
        )

        expected = load_json(
            BENCHMARKS / case["expected"]
        )

        self.assertEqual(
            [
                entry["text"]
                for entry in expected[
                    "reference_entries"
                ]
            ],
            expected[
                "required_entry_boundaries"
            ],
        )

    def test_multi_chunk_case_has_forced_boundary(
        self,
    ):
        case = next(
            case
            for case in self.cases
            if case["id"]
            == "multi_chunk_continuity"
        )

        source = (
            BENCHMARKS / case["input"]
        ).read_text(encoding="utf-8")

        self.assertGreater(
            len(source),
            case["chunk_size"],
        )


class ReviewBenchmarkReferenceTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        manifest = load_json(
            MANIFEST_PATH
        )

        cls.cases = [
            case
            for case in manifest["cases"]
            if case["kind"] == "review"
        ]

    def test_review_modes_are_complete(self):
        self.assertEqual(
            {
                case["mode"]
                for case in self.cases
            },
            {
                "ordinary",
                "contextual",
            },
        )

    def test_review_reference_text_is_exact(self):
        for case in self.cases:
            input_payload = load_json(
                BENCHMARKS / case["input"]
            )
            expected = load_json(
                BENCHMARKS / case["expected"]
            )
            target = input_payload["target"]

            result = audit_review_batch(
                target,
                target,
            )

            with self.subTest(
                case_id=case["id"]
            ):
                self.assertTrue(
                    result.passed,
                    result.to_dict(),
                )
                self.assertEqual(
                    "\n".join(
                        entry["text"]
                        for entry in target
                    ),
                    expected[
                        "expected_text_stream"
                    ],
                )

    def test_contextual_neighbors_are_not_target_text(
        self,
    ):
        case = next(
            case
            for case in self.cases
            if case["mode"] == "contextual"
        )

        payload = load_json(
            BENCHMARKS / case["input"]
        )

        target_stream = normalize_review_text(
            "\n".join(
                entry["text"]
                for entry in payload["target"]
            )
        )

        neighbor_stream = normalize_review_text(
            "\n".join(
                entry["text"]
                for key in ("before", "after")
                for entry in payload[key]
            )
        )

        self.assertNotEqual(
            target_stream,
            neighbor_stream,
        )
        self.assertNotIn(
            neighbor_stream,
            target_stream,
        )


if __name__ == "__main__":
    unittest.main()
