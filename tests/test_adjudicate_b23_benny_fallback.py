from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks import adjudicate_b23_benny_fallback_decision as adjudicator


def route(audio: str, fingerprint: str, text: str) -> dict:
    return {
        "backend": "qwen3_instruction_controlled",
        "instruction_keywords": ["fixture"],
        "identity_audio": audio,
        "identity_audio_sha256": fingerprint,
        "identity_text": text,
        "performance_audio": None,
        "performance_audio_sha256": None,
        "performance_text": None,
        "control": {},
        "effect_chain": None,
        "approval_tier": "strict",
        "production_promotion_allowed": True,
    }


class BennyFallbackAdjudicationTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        project = root / "project"
        project.mkdir()
        routes = {
            "neutral": route("neutral.wav", "neutral-sha", "Neutral Benny."),
            "benny_criminal_sardonic_concern": route(
                "sardonic.wav", "sardonic-sha", "Old sardonic Benny."
            ),
            "benny_criminal_incredulous_concern": route(
                "incredulous.wav", "incredulous-sha", "Incredulous Benny."
            ),
        }
        policy = {
            "schema_version": 1,
            "enabled": True,
            "default_route": "neutral",
            "fallback_backend": "qwen3_instruction_controlled",
            "evidence_round_id": "fixture",
            "production_promotion_allowed": True,
            "routes": routes,
        }
        config = {
            "BERNICE": {
                "responsive_backend_routing": policy,
                "responsive_backend_configuration_fingerprint": "f" * 64,
            }
        }
        (project / "voice_config.json").write_text(json.dumps(config), encoding="utf-8")
        (project / adjudicator.PROJECT_DECISION_FILENAME).write_text(
            json.dumps({"schema_version": 1, "decisions": {"BERNICE": {}}}),
            encoding="utf-8",
        )
        review = root / "review.json"
        review.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "round_id": adjudicator.ROUND_ID,
                    "completed_at": "2026-08-04T14:41:58.795Z",
                    "selection": "DFR03",
                    "notes": "",
                }
            ),
            encoding="utf-8",
        )
        answer = root / "answer.json"
        answer.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "round_id": adjudicator.ROUND_ID,
                    "answers": [
                        {
                            "sample_id": "BFR03",
                            "method": "qwen_reference__benny_criminal_incredulous_concern",
                            "route_key": "benny_criminal_incredulous_concern",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return project, review, answer

    def test_legacy_dfr03_maps_to_incredulous_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, review, answer = self.fixture(Path(temporary))
            result = adjudicator.adjudicate(
                project_root=project,
                review_path=review,
                answer_key_path=answer,
                uploaded_review_sha256="uploaded-sha",
            )
            self.assertEqual(result["exported_selection"], "DFR03")
            self.assertEqual(result["selection"], "BFR03")
            self.assertEqual(
                result["selected_reference_route"],
                "benny_criminal_incredulous_concern",
            )
            update = result["route_updates"][0]
            self.assertEqual(update["voice"], "BERNICE")
            self.assertEqual(
                update["route_key"], "benny_criminal_sardonic_concern"
            )
            self.assertEqual(update["identity_audio"], "incredulous.wav")
            decision = result["project_decision_document"]["decisions"]["BERNICE"]
            self.assertEqual(decision["status"], "approved")
            self.assertEqual(decision["primary_candidate_id"], "BFR03")

    def test_none_preserves_existing_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, review, answer = self.fixture(Path(temporary))
            value = json.loads(review.read_text(encoding="utf-8"))
            value["selection"] = "none"
            review.write_text(json.dumps(value), encoding="utf-8")
            result = adjudicator.adjudicate(
                project_root=project,
                review_path=review,
                answer_key_path=answer,
            )
            self.assertFalse(result["production_route_change"])
            self.assertEqual(result["route_updates"], [])
            decision = result["project_decision_document"]["decisions"]["BERNICE"]
            self.assertEqual(decision["status"], "return_to_preparation")


if __name__ == "__main__":
    unittest.main()
