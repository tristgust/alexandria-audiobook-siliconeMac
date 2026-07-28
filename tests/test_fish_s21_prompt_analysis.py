from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.analyze_fish_s21_prompt_controls import (
    PromptControlAnalysisError,
    build_report,
    join_exports,
)


class PromptControlAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.result_paths: list[Path] = []
        for identity_index, identity in enumerate(("ryan_synthetic", "narrator"), start=1):
            round_id = f"round-{identity}"
            key_root = self.root / identity / "private"
            key_root.mkdir(parents=True)
            answer_rows = []
            score_rows = []
            for sample_index, (mode, delivery) in enumerate(
                (("untagged", 2), ("simple_tag", 4), ("rich_tag", 5)),
                start=1,
            ):
                sample_id = f"{identity}-{sample_index}"
                answer_rows.append(
                    {
                        "sample_id": sample_id,
                        "kind": "fish_cloud",
                        "prompt_mode": mode,
                        "style": "grief",
                    }
                )
                score_rows.append(
                    {
                        "sample_id": sample_id,
                        "identity_1_to_5": 5,
                        "delivery_1_to_5": delivery,
                        "naturalness_1_to_5": 5,
                        "artifact_severity_1_to_5": 1,
                        "spoken_text_matches_expected": True,
                        "requested_mode_is_clear": delivery >= 4,
                        "approve_for_comparison": delivery >= 4,
                    }
                )
            baseline_id = f"{identity}-baseline"
            answer_rows.append(
                {
                    "sample_id": baseline_id,
                    "kind": "existing_baseline",
                    "model_key": "voxcpm2",
                    "style": "grief",
                }
            )
            score_rows.append(
                {
                    "sample_id": baseline_id,
                    "identity_1_to_5": 4,
                    "delivery_1_to_5": 4,
                    "naturalness_1_to_5": 4,
                    "artifact_severity_1_to_5": 1,
                    "spoken_text_matches_expected": True,
                    "requested_mode_is_clear": True,
                    "approve_for_comparison": True,
                }
            )
            (key_root / "answer-key.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "round_id": round_id,
                        "rows": answer_rows,
                    }
                ),
                encoding="utf-8",
            )
            result = self.root / f"result-{identity_index}.json"
            result.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "round_id": round_id,
                        "reviewer": "tristan",
                        "rows": score_rows,
                    }
                ),
                encoding="utf-8",
            )
            self.result_paths.append(result)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_exports_join_and_rank_prompt_delivery(self) -> None:
        rows = join_exports(self.root, self.result_paths)
        report = build_report(rows)
        self.assertEqual(report["sample_count"], 8)
        self.assertEqual(report["identity_count"], 2)
        self.assertEqual(
            report["prompt_ranking"],
            ["rich_tag", "simple_tag", "untagged"],
        )
        self.assertEqual(report["overall"]["fish_cloud"]["sample_count"], 6)
        self.assertEqual(report["overall"]["local_baselines"]["sample_count"], 2)

    def test_unknown_or_incomplete_export_is_rejected(self) -> None:
        payload = json.loads(self.result_paths[0].read_text(encoding="utf-8"))
        payload["rows"].pop()
        self.result_paths[0].write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(PromptControlAnalysisError, "Incomplete export"):
            join_exports(self.root, self.result_paths)


if __name__ == "__main__":
    unittest.main()
