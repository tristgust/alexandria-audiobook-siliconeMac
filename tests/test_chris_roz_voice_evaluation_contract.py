from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CONFIG = ROOT / "benchmarks/chris_roz_reference_sources.json"
FISH_CONFIG = ROOT / "benchmarks/fish_s21_preferred_router_retest.json"
POSTREVIEW_SELECTION = ROOT / "benchmarks/chris_roz_postreview_selection.json"
CLEANUP_CONFIG = ROOT / "benchmarks/chris_roz_cleanup_candidates.json"


class ChrisRozVoiceEvaluationContractTests(unittest.TestCase):
    def test_reference_source_contract_keeps_tnia_out_of_identity_pool(self) -> None:
        payload = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["permission"]["confirmed_by_user"])
        self.assertEqual(len(payload["sources"]), 6)
        self.assertEqual({row["key"] for row in payload["sources"]}, {
            "original_sin",
            "damaged_goods",
            "trial_time_machine",
            "vanguard",
            "jabari_countdown",
            "dread_of_night",
        })
        self.assertEqual(set(payload["anchors"]), {"chris", "roz"})
        identity_candidates = payload["curated_candidates"]
        self.assertEqual({row["identity"] for row in identity_candidates}, {"chris", "roz"})
        self.assertTrue(payload["tnia_style_sources"])
        self.assertTrue(payload["curated_style_candidates"])
        self.assertTrue(all(row["key"].startswith("tnia_") for row in payload["curated_style_candidates"]))

    def test_reference_candidates_have_exact_trim_and_transcript_fields(self) -> None:
        payload = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))
        rows = [*payload["curated_candidates"], *payload["curated_style_candidates"]]
        keys = [row["key"] for row in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for row in rows:
            self.assertGreater(float(row["end_seconds"]), float(row["start_seconds"]))
            self.assertTrue(str(row["transcript"]).strip())
            self.assertTrue(str(row["delivery"]).strip())

    def test_fish_retest_uses_identity_specific_preferred_routing(self) -> None:
        payload = json.loads(FISH_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["api_model_header"], "s2.1-pro-free")
        self.assertTrue(payload["permission"]["confirmed_by_user"])
        self.assertEqual(int(payload["generation"]["repeats"]), 2)
        tests = payload["tests"]
        self.assertEqual(len(tests), 12)
        self.assertEqual(len({row["key"] for row in tests}), 12)
        by_identity = {
            identity: [row for row in tests if row["identity"] == identity]
            for identity in ("narrator", "benny", "doctor")
        }
        self.assertEqual({row["prompt_mode"] for row in by_identity["narrator"]}, {"full_alexandria_tag"})
        self.assertEqual({row["prompt_mode"] for row in by_identity["benny"]}, {"rich_tag"})
        self.assertEqual({row["prompt_mode"] for row in by_identity["doctor"]}, {"untagged", "full_alexandria_tag"})
        self.assertEqual(len(by_identity["narrator"]), 4)
        self.assertEqual(len(by_identity["benny"]), 4)
        self.assertEqual(len(by_identity["doctor"]), 4)

    def test_fish_retest_every_tagged_row_has_all_control_forms(self) -> None:
        payload = json.loads(FISH_CONFIG.read_text(encoding="utf-8"))
        for row in payload["tests"]:
            self.assertTrue(str(row["target_text"]).strip())
            self.assertTrue(str(row["simple_tag"]).strip())
            self.assertTrue(str(row["fish_instruction"]).strip())
            self.assertTrue(str(row["alexandria_instruction"]).strip())

    def test_postreview_selection_removes_tnia_from_every_downstream_lane(self) -> None:
        payload = json.loads(POSTREVIEW_SELECTION.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["tnia_miller"]["disposition"], "removed")
        self.assertFalse(payload["tnia_miller"]["downstream_allowed"])
        self.assertFalse(payload["next_round"]["tnia_style_lane"])
        encoded = json.dumps(
            {
                "identity_references": payload["identity_references"],
                "performance_bank": payload["performance_bank"],
                "models": payload["next_round"]["models"],
            }
        ).casefold()
        self.assertNotIn("tnia", encoded)
        self.assertEqual(
            payload["next_round"]["models"],
            [
                "fish_s2_pro_cloud",
                "voxcpm2_controllable_clone",
                "indextts2_matched_control",
            ],
        )
        self.assertEqual(payload["next_round"]["reference_tiers_per_character"], 2)
        self.assertEqual(payload["next_round"]["repeats_per_cell"], 2)

    def test_cleanup_contract_has_unique_non_tnia_candidates_and_safe_methods(self) -> None:
        payload = json.loads(CLEANUP_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertFalse(payload["policy"]["preserve_raw"] is False)
        self.assertEqual(payload["policy"]["demucs_then_clearvoice"], "rejected_by_probe")
        rows = payload["candidates"]
        ids = [row["id"] for row in rows]
        self.assertEqual(len(rows), 22)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(any(candidate_id.startswith("tnia_") for candidate_id in ids))
        allowed_methods = {
            "boundary_repaired_raw",
            "clearvoice_enhanced",
            "demucs_vocals",
            "speech_separation_target",
        }
        self.assertEqual(
            {row["cleanup_method"] for row in rows},
            allowed_methods,
        )
        for row in rows:
            self.assertGreater(float(row["end_seconds"]), float(row["start_seconds"]))
            self.assertTrue(str(row["transcript"]).strip())
            self.assertIn(row["identity"], {"chris", "roz"})
            self.assertTrue(row["roles"])

    def test_cleanup_probe_policy_rejects_the_identity_damaging_chain(self) -> None:
        payload = json.loads(CLEANUP_CONFIG.read_text(encoding="utf-8"))
        probe = payload["probe_evidence"]
        self.assertTrue(probe["music_dialogue"]["demucs_exact_text"])
        self.assertGreater(
            probe["music_dialogue"]["clearvoice_speaker_cosine_delta"],
            -0.08,
        )
        self.assertGreater(
            probe["music_dialogue"]["demucs_then_clearvoice_word_error_rate"],
            0.0,
        )
        self.assertTrue(probe["stray_laugh"]["target_stream_exact_text"])
        self.assertEqual(probe["stray_laugh"]["non_target_stream_transcript"], "Hi!")


if __name__ == "__main__":
    unittest.main()
