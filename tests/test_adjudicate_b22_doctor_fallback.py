from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.adjudicate_b22_doctor_fallback_decision import adjudicate


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DoctorFallbackAdjudicationTests(unittest.TestCase):
    def fixture(self, root: Path, selection: str) -> tuple[Path, Path, Path]:
        identity = root / "doctor-neutral.wav"
        identity.write_bytes(b"doctor-neutral")
        route = {
            "backend": "qwen3_instruction_controlled",
            "identity_audio": identity.name,
            "identity_audio_sha256": digest(identity.read_bytes()),
            "identity_text": "Doctor reference.",
            "approval_tier": "strict",
            "production_promotion_allowed": True,
        }
        (root / "voice_config.json").write_text(
            json.dumps(
                {
                    "THE DOCTOR": {
                        "responsive_backend_configuration_fingerprint": "f" * 64,
                        "responsive_backend_routing": {
                            "routes": {
                                "neutral": route,
                                "ordinary_identity": dict(route),
                            }
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        current = {
            "schema_version": 1,
            "round_id": "prior",
            "completed_at": "2026-08-03T00:00:00Z",
            "review_sha256": "1" * 64,
            "answer_key_sha256": "2" * 64,
            "evidence_path": ".omo/evidence/prior.json",
            "decisions": {
                "THE DOCTOR": {
                    "status": "return_to_preparation",
                    "primary_method": None,
                    "primary_candidate_id": None,
                    "summary": "Prior.",
                    "production_action": "preserve_prior_routes",
                    "preserve_prior_routes": True,
                    "route_key": "ordinary_identity",
                    "approval_tier": None,
                    "evidence_sample_ids": [],
                    "unresolved_requirements": ["Prepare."],
                }
            },
        }
        (root / "voice_route_listening_decisions.json").write_text(
            json.dumps(current), encoding="utf-8"
        )
        review = root / "review.json"
        review.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "round_id": "b22_doctor_fallback_repair_20260804",
                    "completed_at": "2026-08-04T14:17:37.683Z",
                    "selection": selection,
                    "notes": "",
                }
            ),
            encoding="utf-8",
        )
        answer = root / "answer.json"
        answer.write_text(
            json.dumps(
                {
                    "round_id": "b22_doctor_fallback_repair_20260804",
                    "answers": [
                        {
                            "sample_id": "DFR02",
                            "method": "b18_neutral_identity_anchor",
                            "route_key": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return root, review, answer

    def test_dfr02_maps_to_strict_neutral_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, review, answer = self.fixture(Path(temporary), "DFR02")
            result = adjudicate(
                project_root=project,
                review_path=review,
                answer_key_path=answer,
                uploaded_review_sha256="a" * 64,
            )
            self.assertEqual(result["selected_reference_route"], "neutral")
            self.assertEqual(result["route_updates"][0]["route_key"], "ordinary_identity")
            self.assertEqual(result["route_updates"][0]["identity_audio"], "doctor-neutral.wav")
            decision = result["project_decision_document"]["decisions"]["THE DOCTOR"]
            self.assertEqual(decision["status"], "approved")
            self.assertEqual(decision["primary_candidate_id"], "DFR02")
            self.assertEqual(decision["unresolved_requirements"], [])

    def test_none_preserves_existing_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, review, answer = self.fixture(Path(temporary), "none")
            result = adjudicate(
                project_root=project,
                review_path=review,
                answer_key_path=answer,
            )
            self.assertFalse(result["production_route_change"])
            self.assertEqual(result["route_updates"], [])
            self.assertEqual(
                result["project_decision_document"]["decisions"]["THE DOCTOR"]["status"],
                "return_to_preparation",
            )


if __name__ == "__main__":
    unittest.main()
