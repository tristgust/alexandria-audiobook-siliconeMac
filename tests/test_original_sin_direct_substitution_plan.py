from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.original_sin_overlap_word_alignment import (
    accepted_transcript_check,
    transcript_check_eligible,
)


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks" / "original_sin_direct_substitution_plan.json"
BUILDER = ROOT / "benchmarks" / "build_original_sin_direct_substitution_round.py"


class OriginalSinDirectSubstitutionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = json.loads(PLAN.read_text(encoding="utf-8"))

    def test_pilot_is_bounded_to_six_groups_and_ten_candidates(self) -> None:
        self.assertEqual(len(self.plan["groups"]), 6)
        self.assertEqual(sum(len(group["treatments"]) for group in self.plan["groups"]), 10)
        self.assertEqual(self.plan["candidate_count"], 10)

    def test_every_group_binds_one_chunk_and_one_book_speaker(self) -> None:
        chunk_ids = [group["chunk_id"] for group in self.plan["groups"]]
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))
        self.assertTrue(all(group["book_speaker"] and group["expected_transcript"] for group in self.plan["groups"]))

    def test_routes_follow_previously_safe_character_treatments(self) -> None:
        routes = {
            group["character"]: group["treatments"][1]
            for group in self.plan["groups"]
            if len(group["treatments"]) > 1
        }
        self.assertEqual(routes["Hater of Humans"], "mel_roformer_vocal")
        zebulon = next(group for group in self.plan["groups"] if group["character"] == "Zebulon Pryce")
        self.assertEqual(zebulon["treatments"], ["source_mix"])
        for character in {
            "Roz Forrester",
            "Rashid",
            "Powerless Friendless",
        }:
            self.assertEqual(routes[character], "mossformer2_source_mix")
        securitybot = next(
            group for group in self.plan["groups"] if group["character"] == "Securitybot"
        )
        self.assertEqual(securitybot["treatments"], ["source_mix"])

    def test_review_uses_production_format_mp3_proxy(self) -> None:
        proxy = self.plan["production_proxy"]
        self.assertEqual(proxy["format"], "mp3")
        self.assertEqual(proxy["sample_rate"], 44100)
        self.assertEqual(proxy["channels"], 2)

    def test_securitybot_has_a_bounded_extended_trailing_allowance(self) -> None:
        securitybot = next(
            group for group in self.plan["groups"] if group["character"] == "Securitybot"
        )
        self.assertLessEqual(securitybot["maximum_boundary_margin_seconds"], 0.45)
        self.assertLessEqual(securitybot["trailing_margin_seconds"], 0.4)

    def test_explicit_semantic_variant_can_pass_without_global_fuzziness(self) -> None:
        check = accepted_transcript_check(
            [
                "But you were going to let the Doctor and Bernice die.",
                "But you were gonna let the Doctor and Bernice die.",
            ],
            "But you were gonna let the Doctor and Bernice die.",
        )
        self.assertTrue(transcript_check_eligible(check))
        self.assertEqual(
            check["accepted_transcript"],
            "But you were gonna let the Doctor and Bernice die.",
        )

    def test_builder_hash_guards_project_and_does_not_install_chunks(self) -> None:
        source = BUILDER.read_text(encoding="utf-8")
        self.assertIn("project_hashes(project)", source)
        self.assertIn('"production_changes": False', source)
        self.assertNotIn("chunks.write_text", source)
        self.assertNotIn("voice_config.write_text", source)


if __name__ == "__main__":
    unittest.main()
