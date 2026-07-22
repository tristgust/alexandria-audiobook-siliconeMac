from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T144501Z_expressive_clone_candidate_probe.json"
)
SMOKE = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T145449Z_expressive_clone_baseline_smoke.json"
)
CHATTERBOX = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T151508Z_chatterbox_expressive_clone_matrix.json"
)
FISH = (
    ROOT
    / "benchmarks"
    / "results"
    / "20260721T154722Z_fish_s2_pro_expressive_clone_matrix.json"
)


class ExpressiveCloneEvidenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = json.loads(PROBE.read_text(encoding="utf-8"))
        cls.smoke = json.loads(SMOKE.read_text(encoding="utf-8"))
        cls.chatterbox = json.loads(CHATTERBOX.read_text(encoding="utf-8"))
        cls.fish = json.loads(FISH.read_text(encoding="utf-8"))

    def test_initial_probe_records_pre_acquisition_state(self) -> None:
        self.assertEqual(self.probe["schema_version"], 1)
        self.assertEqual(
            self.probe["run_kind"],
            "expressive_clone_candidate_probe_summary",
        )
        self.assertEqual(self.probe["primary_candidates_total"], 6)
        self.assertEqual(self.probe["primary_candidates_ready"], 0)
        self.assertEqual(
            self.probe["ready_candidate_keys"],
            ["qwen_icl_patch_baseline", "voxcpm2_baseline"],
        )
        self.assertFalse(
            self.probe["benchmark_contract"]["implicit_downloads_allowed"]
        )
        self.assertFalse(
            self.probe["benchmark_contract"]["production_promotion_allowed"]
        )

    def test_probe_discloses_transcription_blockers(self) -> None:
        transcription = self.probe["evaluators"]["transcription_accuracy"]
        self.assertEqual(transcription["cache_state"], "missing")
        self.assertFalse(transcription["runtime_import_available"])
        self.assertIn("SciPy", transcription["runtime_blocker"])
        self.assertIn("_spropack", transcription["runtime_blocker"])

    def test_smoke_validates_both_comparison_worker_paths(self) -> None:
        self.assertEqual(self.smoke["schema_version"], 1)
        self.assertEqual(
            self.smoke["run_kind"],
            "expressive_clone_baseline_smoke_summary",
        )
        candidates = self.smoke["candidate_results"]
        self.assertEqual(
            set(candidates),
            {"qwen_icl_patch_baseline", "voxcpm2_baseline"},
        )
        for result in candidates.values():
            self.assertEqual(result["measurement_count"], 4)
            self.assertEqual(result["error_count"], 0)
            self.assertLess(result["mean_real_time_factor"], 1.0)
            self.assertGreater(
                result["speaker_cosine_to_primary_reference_range"][0],
                0.95,
            )
            self.assertFalse(result["post_generation_prosody_applied"])
            self.assertTrue(result["comparison_only"])
            self.assertFalse(result["delivery_adherence_accepted"])

    def test_smoke_never_promotes_from_objective_metrics(self) -> None:
        acceptance = self.smoke["acceptance"]
        self.assertTrue(acceptance["harness_worker_paths_validated"])
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["primary_alternative_backend_selected"])
        self.assertTrue(
            self.smoke["listening_review"][
                "manual_blinded_review_required"
            ]
        )
        self.assertEqual(
            self.smoke["listening_review"]["status"],
            "pending",
        )

    def test_chatterbox_matrix_records_real_primary_candidate_outputs(self) -> None:
        self.assertEqual(self.chatterbox["schema_version"], 1)
        self.assertEqual(
            self.chatterbox["run_kind"],
            "chatterbox_expressive_clone_matrix_summary",
        )
        downloads = self.chatterbox["explicit_evaluation_downloads"]
        self.assertEqual(downloads["total_snapshot_size_bytes"], 1523695601)
        self.assertFalse(downloads["production_registry_changed"])
        self.assertFalse(downloads["production_assignment_supported"])

        original = self.chatterbox["candidate_results"][
            "chatterbox_original"
        ]
        self.assertEqual(original["measurement_count"], 6)
        self.assertEqual(original["error_count"], 0)
        self.assertEqual(
            original["directions_generated"],
            ["neutral", "urgent", "sarcasm"],
        )
        self.assertFalse(original["semantic_instruction_support_claimed"])
        self.assertFalse(original["delivery_adherence_accepted"])

        turbo = self.chatterbox["candidate_results"]["chatterbox_turbo"]
        self.assertEqual(turbo["measurement_count"], 4)
        self.assertEqual(turbo["error_count"], 0)
        self.assertEqual(
            turbo["directions_generated"],
            ["neutral", "sarcasm"],
        )
        self.assertEqual(
            turbo["skipped_directions"][0]["direction"],
            "urgent",
        )
        self.assertIn(
            "No native event-tag translation",
            turbo["skipped_directions"][0]["reason"],
        )
        self.assertFalse(turbo["delivery_adherence_accepted"])

    def test_chatterbox_matrix_remains_human_and_transcription_gated(self) -> None:
        acceptance = self.chatterbox["acceptance"]
        self.assertTrue(acceptance["matrix_worker_paths_validated"])
        self.assertEqual(acceptance["candidate_generation_errors"], 0)
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["primary_alternative_backend_selected"])
        self.assertFalse(
            self.chatterbox["transcription_evaluation"]["available"]
        )
        self.assertEqual(
            self.chatterbox["listening_review"]["status"],
            "pending",
        )
        self.assertTrue(
            self.chatterbox["listening_review"][
                "manual_blinded_review_required"
            ]
        )

    def test_fish_matrix_covers_all_requested_directions(self) -> None:
        self.assertEqual(self.fish["schema_version"], 1)
        self.assertEqual(
            self.fish["run_kind"],
            "fish_s2_pro_expressive_clone_matrix_summary",
        )
        download = self.fish["explicit_evaluation_download"]
        self.assertEqual(
            download["repo_id"],
            "mlx-community/fish-audio-s2-pro",
        )
        self.assertEqual(
            download["revision"],
            "eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
        )
        self.assertEqual(download["authoritative_snapshot_size_bytes"], 11007904953)
        self.assertEqual(len(download["verified_lfs_files"]), 3)
        self.assertEqual(download["redundant_incomplete_blob_count"], 4)
        self.assertFalse(download["redundant_incomplete_blobs_deleted"])
        self.assertFalse(download["production_registry_changed"])

        result = self.fish["candidate_result"]
        self.assertEqual(result["measurement_count"], 14)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["skipped_direction_count"], 0)
        self.assertEqual(
            set(result["directions_generated"]),
            {
                "neutral",
                "urgent",
                "restrained_anger",
                "panic",
                "grief",
                "whisper",
                "sarcasm",
            },
        )
        self.assertGreater(result["peak_process_rss_gib"], 10.0)
        self.assertGreater(result["mean_real_time_factor"], 1.0)
        self.assertGreater(
            result["speaker_cosine_to_primary_reference_range"][0],
            0.95,
        )
        self.assertFalse(result["post_generation_prosody_applied"])
        self.assertFalse(result["delivery_adherence_accepted"])

    def test_fish_matrix_remains_human_and_transcription_gated(self) -> None:
        self.assertFalse(self.fish["transcription_evaluation"]["available"])
        review = self.fish["listening_review"]
        self.assertEqual(review["status"], "pending")
        self.assertTrue(review["manual_blinded_review_required"])
        self.assertEqual(review["combined_blinded_review_rows"], 14)
        acceptance = self.fish["acceptance"]
        self.assertTrue(acceptance["all_requested_direction_worker_paths_validated"])
        self.assertEqual(acceptance["candidate_generation_errors"], 0)
        self.assertFalse(acceptance["production_promotion_allowed"])
        self.assertFalse(acceptance["primary_alternative_backend_selected"])
        self.assertFalse(acceptance["fish_s2_pro_delivery_adherence_accepted"])

    def test_saved_evidence_contains_fingerprints_not_fixture_text(self) -> None:
        rendered = (
            PROBE.read_text(encoding="utf-8")
            + SMOKE.read_text(encoding="utf-8")
            + CHATTERBOX.read_text(encoding="utf-8")
            + FISH.read_text(encoding="utf-8")
        )
        self.assertNotIn(
            "This is a synthetic, test-only reference voice",
            rendered,
        )
        self.assertNotIn(
            "The lamp is already lit",
            rendered,
        )
        self.assertRegex(
            self.smoke["fixture"]["primary_reference_audio_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(
            self.smoke["fixture"]["target_text_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(
            self.chatterbox["fixture"]["target_text_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(
            self.fish["fixture"]["target_text_sha256"],
            r"^[0-9a-f]{64}$",
        )


if __name__ == "__main__":
    unittest.main()
