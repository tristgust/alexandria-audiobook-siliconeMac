from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_provenance_final_boundaries.py"
EVIDENCE_ROOT = ROOT / ".omo" / "evidence" / "b17-t71-three-voice-provenance-final-boundaries"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceProvenanceFinalBoundariesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "prepare_three_voice_provenance_final_boundaries")

    def test_partial_review_contract_is_strict(self) -> None:
        previous = {
            "round_id": self.module.PREVIOUS_MANIFEST_ROUND_ID,
            "rows": [
                {
                    "card_id": self.module.BOUNDARY_CARD_ID,
                    "card_type": "boundary_final",
                    "clip_id": "benny_hesitation_fatalistic_dread",
                    "target": "benny",
                    "target_label": "Benny",
                    "selected_transcript": "It wasn't bad luck that they'd found the Doctor. It was inevitable.",
                    "primary_emotion": "Dread",
                },
                {
                    "card_id": self.module.SEPARATION_CARD_ID,
                    "card_type": "source_separation",
                    "clip_id": "doctor_acf_dismissive_contempt",
                    "target": "doctor",
                    "target_label": "Doctor",
                    "selected_transcript": "Oh, just another potty little bully. Never mind. Forget it.",
                    "primary_emotion": "Dismissive contempt",
                },
            ],
        }
        review = {
            "source_upload": {"sha256": "a" * 64},
            "review": {
                "round_id": self.module.SOURCE_REVIEW_ROUND_ID,
                "summary": {
                    "card_count": 2,
                    "complete_count": 1,
                    "separation_selected_count": 0,
                    "separation_none_count": 0,
                    "boundary_approved_count": 0,
                    "boundary_wrong_count": 1,
                },
                "rows": [
                    {
                        **previous["rows"][0],
                        "decision": "still_wrong",
                        "notes": "Cuts off inevitable still",
                    },
                    {
                        **previous["rows"][1],
                        "notes": "Someone else starts talking at the end",
                    },
                ],
            },
        }
        result = self.module.validate_partial_review(review, previous)
        self.assertIsNone(result["review_rows"][self.module.SEPARATION_CARD_ID].get("decision"))
        changed = json.loads(json.dumps(review))
        changed["review"]["rows"][1]["decision"] = "candidate_B"
        with self.assertRaises(self.module.FinalBoundaryError):
            self.module.validate_partial_review(changed, previous)

    def test_source_encodes_surgical_endpoint_rules(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('BENNY_END_POLICY = "preserve_full_hash_verified_source_tail_without_fade"', source)
        self.assertIn("DOCTOR_END_SECONDS = 4.34", source)
        self.assertIn("DOCTOR_FADE_SECONDS = 0.008", source)
        self.assertNotIn("automatic_production_assignment\": True", source)
        self.assertNotIn("production_promotion_allowed\": True", source)

    def test_generated_manifest_contract_when_present(self) -> None:
        path = EVIDENCE_ROOT / "final-boundary-manifest.json"
        if not path.is_file():
            self.skipTest("Generated final-boundary evidence is not present in this checkout.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = self.module.validate_manifest(payload)
        self.assertEqual(result["card_count"], 2)
        rows = {row["card_id"]: row for row in payload["rows"]}
        benny = rows[self.module.BOUNDARY_CARD_ID]
        doctor = rows[self.module.SEPARATION_CARD_ID]
        self.assertEqual(benny["end_policy"], self.module.BENNY_END_POLICY)
        self.assertEqual(benny["final"]["verification_similarity"], 1.0)
        self.assertTrue(benny["final"]["technical_pass"])
        self.assertGreater(benny["final"]["metrics"]["duration_seconds"], 4.7)
        self.assertEqual(len(doctor["candidates"]), 3)
        self.assertEqual(doctor["tail_policy"]["trim_end_seconds"], 4.34)
        self.assertGreaterEqual(
            sum(candidate["verification_similarity"] == 1.0 for candidate in doctor["candidates"]),
            2,
        )
        for row in payload["rows"]:
            self.assertFalse(row["automatic_production_assignment"])
            self.assertFalse(row["production_promotion_allowed"])

    def test_review_package_is_blinded_when_present(self) -> None:
        review = EVIDENCE_ROOT / "review"
        data_path = review / "data.js"
        manifest_path = review / "manifest.json"
        if not data_path.is_file() or not manifest_path.is_file():
            self.skipTest("Generated final-boundary review is not present in this checkout.")
        visible = "\n".join(
            (review / name).read_text(encoding="utf-8")
            for name in ("index.html", "data.js")
        )
        self.assertNotRegex(visible, r"BS-RoFormer|MelBand|MDX-Net|model_bs|gabox|Voc_FT")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["card_count"], 2)
        self.assertTrue(manifest["model_names_blinded"])
        self.assertFalse(manifest["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
