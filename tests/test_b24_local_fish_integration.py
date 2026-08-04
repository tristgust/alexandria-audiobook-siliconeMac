from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.prepare_b24_local_fish_integration import (
    LOCAL_MODEL_KEY,
    ROUND_ID,
    SELECTED_CANDIDATE,
    prepare_decision,
)
from model_registry import model_spec
from recurring_voice_routing import routing_fingerprint


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class B24LocalFishIntegrationTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path]:
        project = root / "project"
        identity = project / "clone_voices" / "roz" / "identity.wav"
        identity.parent.mkdir(parents=True)
        identity.write_bytes(b"roz-reference")
        route = {
            "backend": "fish_s2_pro_cloud",
            "instruction_keywords": ["dry banter"],
            "identity_audio": identity.relative_to(project).as_posix(),
            "identity_audio_sha256": digest(identity.read_bytes()),
            "identity_text": "Keep up with the news.",
            "performance_audio": None,
            "performance_audio_sha256": None,
            "performance_text": None,
            "control": {
                "api_model_header": "s2.1-pro-free",
                "prompt_mode": "full_alexandria_tag",
                "tag": "Dry streetwise teasing.",
                "temperature": 0.7,
                "top_p": 0.7,
                "repetition_penalty": 1.2,
                "reference_mode": "inline_zero_shot",
            },
            "effect_chain": None,
            "approval_tier": "restricted_user_accepted",
            "production_promotion_allowed": True,
        }
        policy = {
            "schema_version": 1,
            "enabled": True,
            "default_route": "roz_dry_banter",
            "fallback_backend": "qwen3_instruction_controlled",
            "evidence_round_id": "before",
            "production_promotion_allowed": True,
            "routes": {"roz_dry_banter": route},
        }
        (project / "voice_config.json").write_text(
            json.dumps(
                {
                    "ROZ FORRESTER": {
                        "responsive_backend_routing": policy,
                        "responsive_backend_configuration_fingerprint": routing_fingerprint(policy),
                    }
                }
            ),
            encoding="utf-8",
        )
        (project / "voice_route_listening_decisions.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "round_id": "prior",
                    "completed_at": "2026-08-04T00:00:00Z",
                    "review_sha256": "1" * 64,
                    "answer_key_sha256": "2" * 64,
                    "evidence_path": ".omo/evidence/prior.json",
                    "decisions": {
                        "ROZ FORRESTER": {
                            "status": "approved",
                            "primary_method": "fish_s2_pro_local",
                            "primary_candidate_id": SELECTED_CANDIDATE,
                            "summary": "Prior.",
                            "production_action": "keep_current",
                            "preserve_prior_routes": True,
                            "route_key": "roz_dry_banter",
                            "approval_tier": "restricted_user_accepted",
                            "evidence_sample_ids": ["ROZ02", "ROZ04"],
                            "unresolved_requirements": ["Integrate local Fish."],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        b18 = root / "b18.json"
        b18.write_text(
            json.dumps(
                {
                    "review_sha256": "3" * 64,
                    "answer_key_sha256": "4" * 64,
                    "speaker_decisions": {
                        "ROZ FORRESTER": {
                            "status": "approved",
                            "primary_method": "fish_s2_pro_local",
                            "primary_candidate_id": SELECTED_CANDIDATE,
                        }
                    },
                    "candidate_decisions": [
                        {
                            "sample_id": "ROZ02",
                            "candidate_id": SELECTED_CANDIDATE,
                            "ratings": {
                                "artifact_free": True,
                                "identity": 5,
                                "delivery": 4,
                                "naturalness": 5,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return project, b18

    def test_prepares_local_first_route_with_hosted_and_qwen_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, b18 = self.fixture(Path(temporary))
            spec = model_spec(LOCAL_MODEL_KEY)
            result = prepare_decision(
                project_root=project,
                b18_decision_path=b18,
                model_status={
                    "cached": True,
                    "revision": spec.revision,
                    "snapshot_path": "/cached/local-fish",
                },
            )
            self.assertEqual(result["round_id"], ROUND_ID)
            update = result["route_updates"][0]
            self.assertEqual(update["voice"], "ROZ FORRESTER")
            self.assertEqual(update["backend"], "fish_s2_pro_local")
            self.assertEqual(
                update["control"]["license_scope"],
                "noncommercial_research",
            )
            self.assertEqual(
                update["control"]["hosted_fallback"]["api_model_header"],
                "s2.1-pro-free",
            )
            document = result["project_decision_document"]
            self.assertEqual(document["decisions"]["ROZ FORRESTER"]["production_action"], "replace_route")
            self.assertEqual(document["decisions"]["ROZ FORRESTER"]["unresolved_requirements"], [])

    def test_rejects_changed_or_unapproved_review_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, b18 = self.fixture(Path(temporary))
            value = json.loads(b18.read_text(encoding="utf-8"))
            value["candidate_decisions"][0]["ratings"]["identity"] = 4
            b18.write_text(json.dumps(value), encoding="utf-8")
            spec = model_spec(LOCAL_MODEL_KEY)
            with self.assertRaisesRegex(RuntimeError, "scores changed"):
                prepare_decision(
                    project_root=project,
                    b18_decision_path=b18,
                    model_status={"cached": True, "revision": spec.revision},
                )


if __name__ == "__main__":
    unittest.main()
