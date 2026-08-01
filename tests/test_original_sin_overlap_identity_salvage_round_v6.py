from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "benchmarks" / "build_original_sin_overlap_identity_salvage_round_v6.py"


class IdentitySalvageRoundV6ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = BUILDER.read_text(encoding="utf-8")
        tree = ast.parse(cls.text)
        namespace: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                try:
                    exec(
                        compile(ast.Module(body=[node], type_ignores=[]), str(BUILDER), "exec"),
                        namespace,
                    )
                except Exception:
                    continue
        cls.specs = namespace["CHARACTER_SPECS"]

    def test_round_covers_only_three_unresolved_identities(self) -> None:
        self.assertEqual(
            {spec["character"] for spec in self.specs},
            {"Doc Dantalion", "Homeless Forsaken", "Shythe Shahid"},
        )
        self.assertEqual(sum(len(spec["variants"]) for spec in self.specs), 9)

    def test_new_operations_do_not_repeat_source_separation(self) -> None:
        self.assertIn("robust_waveform_consensus", self.text)
        self.assertIn("low_quantile_spectral_consensus", self.text)
        self.assertIn("deecho_spectral_consensus", self.text)
        self.assertIn("spectral_gate_transient_control", self.text)
        self.assertIn("music_suppressed_consensus", self.text)
        self.assertIn('"repeated_source_separation_inference": False', self.text)
        self.assertNotIn("load_model(", self.text)

    def test_review_separates_context_from_promotable_candidates(self) -> None:
        self.assertIn("Source-context extraction — identity reference only, not eligible", self.text)
        self.assertIn("source_context_not_eligible", self.text)
        self.assertIn("candidate_methods_hidden", self.text)
        self.assertIn("Contamination removal", self.text)
        self.assertIn("Entire line present", self.text)

    def test_round_is_non_installing_and_objectively_screened(self) -> None:
        self.assertIn("evaluate_transcriptions", self.text)
        self.assertIn("MAX_ACCEPTABLE_WER = 0.25", self.text)
        self.assertIn('"production_routing_changed": False', self.text)
        self.assertIn('"project_audio_changed": False', self.text)
        self.assertIn('"voice_config_changed": False', self.text)


if __name__ == "__main__":
    unittest.main()
