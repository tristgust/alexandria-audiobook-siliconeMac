from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_provenance_followups.py"
EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t69-three-voice-provenance-followups"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceProvenanceFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "prepare_three_voice_provenance_followups")

    def test_followup_scope_is_exactly_two_cards(self) -> None:
        self.assertEqual(self.module.BOUNDARY_CLIP_ID, "benny_hesitation_fatalistic_dread")
        self.assertEqual(self.module.CLEANUP_CLIP_ID, "doctor_acf_dismissive_contempt")
        self.assertEqual(set(self.module.SEPARATION_MODELS), {"bs317", "fv4", "mdx"})

    def test_generated_manifest_when_present(self) -> None:
        path = EVIDENCE / "followup-manifest.json"
        if not path.is_file():
            self.skipTest("Generated provenance follow-up evidence is not present.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = self.module.validate_manifest(payload)
        self.assertEqual(result["card_count"], 2)
        self.assertEqual(
            result["card_type_counts"],
            {"boundary_final": 1, "source_separation": 1},
        )
        rows = {row["clip_id"]: row for row in payload["rows"]}
        boundary = rows["benny_hesitation_fatalistic_dread"]
        self.assertTrue(boundary["final"]["technical_pass"])
        self.assertEqual(boundary["final"]["verification_similarity"], 1.0)
        self.assertEqual(boundary["source_recovery_method"], "hash_verified_candidate_relative_trim")
        doctor = rows["doctor_acf_dismissive_contempt"]
        self.assertEqual(len(doctor["candidates"]), 3)
        self.assertEqual(doctor["source_recovery_method"], "hash_verified_reviewed_clip")
        self.assertTrue(all(row["technical_pass"] for row in doctor["candidates"]))
        self.assertEqual(
            payload["abandoned_without_generation"],
            [{"clip_id": "benny_hesitation_protective_reassurance", "reason": "role_contaminated_performance"}],
        )
        self.assertFalse(payload["automatic_production_assignment"])
        self.assertFalse(payload["production_promotion_allowed"])

    def test_review_package_when_present(self) -> None:
        review = EVIDENCE / "review"
        if not (review / "data.js").is_file():
            self.skipTest("Generated provenance follow-up review is not present.")
        result = self.module.validate_package(
            type("Args", (), {"output_root": str(EVIDENCE)})()
        )
        self.assertEqual(result["card_count"], 2)
        manifest = json.loads((review / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["abandoned_role_contaminated_clip_count"], 1)
        self.assertTrue(manifest["model_names_blinded"])
        self.assertFalse(manifest["production_promotion_allowed"])
        app = (review / "app.js").read_text(encoding="utf-8")
        self.assertIn("alexandria_three_voice_provenance_followups_review.json", app)
        self.assertNotIn("alexandria_three_voice_final_salvage_review.json", app)


if __name__ == "__main__":
    unittest.main()
