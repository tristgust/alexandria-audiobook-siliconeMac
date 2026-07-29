from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CONFIG = ROOT / "benchmarks/chris_roz_reference_sources.json"
FISH_CONFIG = ROOT / "benchmarks/fish_s21_preferred_router_retest.json"


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


if __name__ == "__main__":
    unittest.main()
