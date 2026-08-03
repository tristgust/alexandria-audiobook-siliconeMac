from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from voice_route_listening_decisions import (
    VoiceRouteListeningDecisionError,
    decision_for_voice,
    decision_fingerprint,
    normalize_voice_route_listening_decisions,
)


def decision_document() -> dict:
    return {
        "schema_version": 1,
        "round_id": "round_1",
        "completed_at": "2026-08-03T20:17:02.040Z",
        "review_sha256": "1" * 64,
        "answer_key_sha256": "2" * 64,
        "evidence_path": ".omo/evidence/round_1.json",
        "decisions": {
            "COMPUTER": {
                "status": "approved",
                "primary_method": "qwen_controlled_identity__computer_terminal_v3",
                "primary_candidate_id": "computer__qwen_terminal",
                "summary": "Qwen with terminal processing was approved.",
                "production_action": "replace_route",
                "preserve_prior_routes": True,
                "route_key": "computer_formal_system_response",
                "approval_tier": "strict",
                "evidence_sample_ids": ["COM05"],
                "unresolved_requirements": [],
            }
        },
    }


class VoiceRouteListeningDecisionTests(unittest.TestCase):
    def test_normalizes_and_loads_voice_decision(self) -> None:
        value = decision_document()
        normalized = normalize_voice_route_listening_decisions(value)
        self.assertEqual(normalized["decisions"]["COMPUTER"]["status"], "approved")
        self.assertEqual(len(decision_fingerprint(value)), 64)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "voice_route_listening_decisions.json").write_text(
                json.dumps(value), encoding="utf-8"
            )
            selected = decision_for_voice(root, "computer")
            self.assertEqual(selected["primary_candidate_id"], "computer__qwen_terminal")
            self.assertEqual(selected["round_id"], "round_1")

    def test_rejects_unknown_fields_and_incomplete_replacement(self) -> None:
        value = decision_document()
        value["unexpected"] = True
        with self.assertRaises(VoiceRouteListeningDecisionError):
            normalize_voice_route_listening_decisions(value)
        value = decision_document()
        value["decisions"]["COMPUTER"]["route_key"] = None
        with self.assertRaises(VoiceRouteListeningDecisionError):
            normalize_voice_route_listening_decisions(value)


if __name__ == "__main__":
    unittest.main()
