from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APPLY_SCRIPT = ROOT / "benchmarks" / "apply_three_voice_final_salvage_review.py"
REFINE_SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_selected_refinements.py"
ASSET_ROOT = ROOT / "benchmarks" / "three_voice_selected_refinement_assets"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceSelectedRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.apply = load_module(APPLY_SCRIPT, "apply_three_voice_final_salvage_review")
        cls.refine = load_module(REFINE_SCRIPT, "prepare_three_voice_selected_refinements")

    def test_review_notes_route_only_two_selected_sources_to_refinement(self) -> None:
        self.assertTrue(self.apply.requires_refinement({"notes": "Candidate B is best, if refined."}))
        self.assertTrue(self.apply.requires_refinement({"notes": "Still hear page turn effects in the background."}))
        self.assertFalse(self.apply.requires_refinement({"notes": ""}))
        self.assertFalse(self.apply.requires_refinement({}))

    def test_candidate_decision_resolves_blind_label(self) -> None:
        answer = {
            "card_id": "separation:test",
            "candidates": [
                {"candidate_label": "A", "model_key": "first"},
                {"candidate_label": "B", "model_key": "second"},
            ],
        }
        self.assertEqual(self.apply.candidate_for_decision(answer, "candidate_B")["model_key"], "second")
        with self.assertRaises(self.apply.FinalSalvageApplyError):
            self.apply.candidate_for_decision(answer, "candidate_C")

    def test_prior_references_are_normalized_across_clean_and_repaired_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "clip.wav"
            sf.write(audio, np.zeros(2400, dtype=np.float32), 24000, subtype="PCM_16")
            digest = self.apply.sha256_file(audio)
            clean = self.apply.normalize_prior_reference({
                "clip_id": "clean",
                "audio_path": str(audio),
                "audio_sha256": digest,
            })
            repaired = self.apply.normalize_prior_reference({
                "clip_id": "repaired",
                "repaired_audio_path": str(audio),
                "repaired_audio_sha256": digest,
            })
            self.assertEqual(clean["audio_path"], str(audio))
            self.assertEqual(repaired["audio_path"], str(audio))
            self.assertEqual(repaired["audio_sha256"], digest)
            self.assertFalse(repaired["production_promotion_allowed"])

    def test_segment_mask_suppresses_gaps_and_fades_edges(self) -> None:
        sample_rate = 1000
        mask = self.refine.raised_segment_mask(
            2000,
            sample_rate,
            [(0.4, 0.8), (1.2, 1.6)],
            0.05,
        )
        self.assertEqual(float(mask[100]), 0.0)
        self.assertEqual(float(mask[1000]), 0.0)
        self.assertEqual(float(mask[1900]), 0.0)
        self.assertGreater(float(mask[425]), 0.0)
        self.assertEqual(float(mask[500]), 1.0)
        self.assertEqual(float(mask[1300]), 1.0)
        self.assertLess(float(mask[790]), 1.0)

    def test_doctor_refinement_rebuilds_complete_source_entrance(self) -> None:
        self.assertEqual(self.refine.DOCTOR_MODEL_FILENAME, "mel_band_roformer_vocals_fv4_gabox.ckpt")
        self.assertAlmostEqual(self.refine.DOCTOR_SOURCE_START, 58.98, places=2)
        self.assertAlmostEqual(self.refine.DOCTOR_SOURCE_END, 75.22, places=2)
        source = REFINE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("source_recut_then_fv4_vocal_separation", source)
        self.assertIn("opening_word_source_start_seconds", source)
        self.assertIn("word_timed_interphrase_effect_suppression", source)
        self.assertIn('"production_promotion_allowed": False', source)

    def test_micro_review_assets_keep_only_two_audio_elements(self) -> None:
        html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        app = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertEqual(html.lower().count("<audio"), 2)
        self.assertIn("Use refined", html)
        self.assertIn("Keep selected", html)
        self.assertIn("Reject source", html)
        self.assertIn("alexandria_three_voice_selected_refinement_review.json", app)

    def test_generated_ledgers_match_review_when_present(self) -> None:
        applied_path = ROOT / ".omo" / "evidence" / "b17-t58-three-voice-final-salvage-applied" / "applied-salvage-review-ledger.json"
        refinement_path = ROOT / ".omo" / "evidence" / "b17-t59-three-voice-selected-refinements" / "refinement-manifest.json"
        if not applied_path.is_file() or not refinement_path.is_file():
            self.skipTest("Generated local final-refinement evidence is not present.")
        applied = json.loads(applied_path.read_text(encoding="utf-8"))
        refinements = json.loads(refinement_path.read_text(encoding="utf-8"))
        self.assertEqual(applied["validated_reference_count"], 18)
        self.assertEqual(len(applied["refinement_queue"]), 2)
        self.assertEqual(len(applied["rejected_sources"]), 1)
        self.assertEqual(
            applied["disposition_counts"],
            {
                "approved_final_boundary": 5,
                "approved_source_separation": 1,
                "rejected_no_usable_separation": 1,
                "selected_for_refinement": 2,
            },
        )
        self.assertEqual(refinements["candidate_count"], 2)
        self.assertEqual(refinements["technical_pass_count"], 2)
        self.assertFalse(refinements["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
