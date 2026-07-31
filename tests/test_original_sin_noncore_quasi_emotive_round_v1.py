from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/build_original_sin_noncore_quasi_emotive_round_v1.py"


class NoncoreQuasiEmotiveRoundContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.text)
        namespace: dict[str, object] = {}
        for node in cls.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if isinstance(target, ast.Name) and target.id in {
                    "ROUND_ID",
                    "MODE_SPECS",
                    "MAX_ACCEPTABLE_WER",
                    "TRANSCRIPTION_ALIAS_POLICY",
                }:
                    namespace[target.id] = ast.literal_eval(node.value)
        cls.round_id = namespace["ROUND_ID"]
        cls.modes = list(namespace["MODE_SPECS"])
        cls.max_wer = namespace["MAX_ACCEPTABLE_WER"]
        cls.alias_policy = namespace["TRANSCRIPTION_ALIAS_POLICY"]

    def test_round_is_bounded_and_unique(self) -> None:
        self.assertEqual(
            self.round_id,
            "alexandria_original_sin_noncore_quasi_emotive_round_v1",
        )
        self.assertEqual(len(self.modes), 16)
        self.assertEqual(
            len({item["mode_id"] for item in self.modes}),
            len(self.modes),
        )
        self.assertEqual(
            len({item["target_chunk_id"] for item in self.modes}),
            len(self.modes),
        )

    def test_only_evidence_rich_characters_receive_comparisons(self) -> None:
        compared = {
            item["speaker"]
            for item in self.modes
            if item["compare_identity_baseline"]
        }
        self.assertEqual(
            compared,
            {"BELTEMPEST", "TOBIAS VAUGHN", "ZEBULON PRYCE"},
        )

    def test_synthetic_voices_remain_constrained(self) -> None:
        synthetic = {
            item["speaker"]: item["review_instruction"]
            for item in self.modes
            if item["speaker"] in {"BOT", "COMPUTER"}
        }
        self.assertIn("low-emotion", synthetic["BOT"])
        self.assertIn("not broad emotion", synthetic["COMPUTER"])

    def test_objective_text_gate_and_retry_are_mandatory(self) -> None:
        self.assertEqual(self.max_wer, 0.25)
        self.assertIn("RETRY_SEED", self.text)
        self.assertIn("evaluate_transcriptions", self.text)
        self.assertIn("transcription_gate_failed", self.text)

    def test_recognizer_aliases_are_securitybot_only(self) -> None:
        self.assertEqual(set(self.alias_policy), {"bot_synthetic_neutral"})
        bot = self.alias_policy["bot_synthetic_neutral"]
        self.assertEqual(bot["token_aliases"]["5"], "five")
        self.assertEqual(
            bot["phrase_aliases"][("a", "judicator")],
            ("adjudicator",),
        )

    def test_round_is_non_installing_and_reviewable(self) -> None:
        for marker in (
            '"production_routing_changed": False',
            '"project_audio_changed": False',
            '"voice_config_changed": False',
            "Export review",
            "Identity",
            "Delivery fit",
            "Naturalness",
            "Intelligibility",
        ):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
