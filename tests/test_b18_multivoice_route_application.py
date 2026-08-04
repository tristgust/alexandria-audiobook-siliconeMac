from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.apply_b18_multivoice_route_decisions import apply_decisions
from recurring_voice_routing import routing_fingerprint, validate_recurring_voice_routing


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def route(
    identity: str,
    identity_hash: str,
    *,
    backend: str,
    effect: str | None,
) -> dict:
    return {
        "backend": backend,
        "instruction_keywords": ["test route"],
        "identity_audio": identity,
        "identity_audio_sha256": identity_hash,
        "identity_text": "Reference words.",
        "performance_audio": identity if backend == "indextts2_matched_control" else None,
        "performance_audio_sha256": identity_hash if backend == "indextts2_matched_control" else None,
        "performance_text": "Reference words." if backend == "indextts2_matched_control" else None,
        "control": (
            {
                "emotion_strength": 1.0,
                "diffusion_steps": 8,
                "num_beams": 1,
                "greedy": True,
                "max_mel_tokens": 600,
            }
            if backend == "indextts2_matched_control"
            else {}
        ),
        "effect_chain": effect,
        "approval_tier": "restricted_user_accepted",
        "production_promotion_allowed": True,
    }


class B18MultiVoiceRouteApplicationTests(unittest.TestCase):
    def test_applies_two_routes_transactionally_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "clone_voices" / "approved_adaptation"
            computer = assets / "computer" / "identity.wav"
            powerless = assets / "powerless" / "identity.wav"
            computer.parent.mkdir(parents=True)
            powerless.parent.mkdir(parents=True)
            computer.write_bytes(b"computer-reference")
            powerless.write_bytes(b"powerless-reference")
            computer_relative = computer.relative_to(root).as_posix()
            powerless_relative = powerless.relative_to(root).as_posix()
            policies = {}
            for name, relative, fingerprint, backend, effect in (
                ("COMPUTER", computer_relative, file_hash(computer), "fish_s2_pro_cloud", "computer_terminal_v3"),
                ("POWERLESS FRIENDLESS", powerless_relative, file_hash(powerless), "indextts2_matched_control", "powerless_alien_modulation_v1"),
            ):
                current_route = route(relative, fingerprint, backend=backend, effect=effect)
                if backend == "fish_s2_pro_cloud":
                    current_route["control"] = {
                        "api_model_header": "s2.1-pro-free",
                        "prompt_mode": "full_alexandria_tag",
                        "tag": "Test route.",
                        "temperature": 0.7,
                        "top_p": 0.7,
                        "repetition_penalty": 1.2,
                        "reference_mode": "inline_zero_shot",
                    }
                    current_route["performance_audio"] = None
                    current_route["performance_audio_sha256"] = None
                    current_route["performance_text"] = None
                policy = validate_recurring_voice_routing(
                    {
                        "schema_version": 1,
                        "enabled": True,
                        "default_route": "test_route",
                        "fallback_backend": "qwen3_instruction_controlled",
                        "evidence_round_id": "before_review",
                        "production_promotion_allowed": True,
                        "routes": {"test_route": current_route},
                    },
                    project_root=root,
                    verify_audio=True,
                )
                policies[name] = policy
            voice_config = {
                name: {
                    "type": "clone",
                    "clone_backend": "alexandria_responsive_router",
                    "ref_audio": next(iter(policy["routes"].values()))["identity_audio"],
                    "ref_text": "Reference words.",
                    "responsive_backend_routing": policy,
                    "responsive_backend_configuration_fingerprint": routing_fingerprint(policy),
                }
                for name, policy in policies.items()
            }
            voice_config["UNCHANGED"] = {"type": "custom", "voice": "Ryan"}
            (root / "voice_config.json").write_text(json.dumps(voice_config), encoding="utf-8")
            audio = root / "generated" / "computer.wav"
            audio.parent.mkdir()
            audio.write_bytes(b"generated")
            (root / "chunks.json").write_text(
                json.dumps(
                    [
                        {"id": 1, "speaker": "COMPUTER", "status": "done", "audio_path": "generated/computer.wav"},
                        {"id": 2, "speaker": "UNCHANGED", "status": "pending", "audio_path": None},
                    ]
                ),
                encoding="utf-8",
            )
            decisions = {}
            updates = []
            for name, relative, fingerprint in (
                ("COMPUTER", computer_relative, file_hash(computer)),
                ("POWERLESS FRIENDLESS", powerless_relative, file_hash(powerless)),
            ):
                decisions[name] = {
                    "status": "approved" if name == "COMPUTER" else "restricted",
                    "primary_method": "qwen",
                    "primary_candidate_id": f"{name}-qwen",
                    "summary": "Reviewed.",
                    "production_action": "replace_route",
                    "preserve_prior_routes": True,
                    "route_key": "test_route",
                    "approval_tier": "strict" if name == "COMPUTER" else "restricted_user_accepted",
                    "evidence_sample_ids": ["S01"],
                    "unresolved_requirements": [],
                }
                updates.append(
                    {
                        "voice": name,
                        "route_key": "test_route",
                        "expected_configuration_fingerprint": routing_fingerprint(policies[name]),
                        "backend": "qwen3_instruction_controlled",
                        "identity_audio": relative,
                        "identity_audio_sha256": fingerprint,
                        "identity_text": "Reference words.",
                        "control": {},
                        "effect_chain": (
                            None
                            if name == "POWERLESS FRIENDLESS"
                            else next(iter(policies[name]["routes"].values()))[
                                "effect_chain"
                            ]
                        ),
                        "approval_tier": decisions[name]["approval_tier"],
                        "clear_performance_reference": True,
                    }
                )
            decision_path = root / "decision.json"
            decision_path.write_text(
                json.dumps(
                    {
                        "route_evidence_round_id": "round_routing_evidence",
                        "route_updates": updates,
                        "project_decision_document": {
                            "schema_version": 1,
                            "round_id": "round",
                            "completed_at": "2026-08-03T20:17:02.040Z",
                            "review_sha256": "1" * 64,
                            "answer_key_sha256": "2" * 64,
                            "evidence_path": ".omo/evidence/round.json",
                            "decisions": decisions,
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = apply_decisions(project_root=root, decision_path=decision_path)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["changed_voices"], ["COMPUTER", "POWERLESS FRIENDLESS"])
            self.assertEqual(result["invalidated_count"], 1)
            updated = json.loads((root / "voice_config.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["UNCHANGED"], voice_config["UNCHANGED"])
            for name in ("COMPUTER", "POWERLESS FRIENDLESS"):
                self.assertEqual(
                    updated[name]["responsive_backend_routing"]["evidence_round_id"],
                    "round_routing_evidence",
                )
                selected = updated[name]["responsive_backend_routing"]["routes"]["test_route"]
                self.assertEqual(selected["backend"], "qwen3_instruction_controlled")
                self.assertIsNone(selected["performance_audio"])
            self.assertIsNone(
                updated["POWERLESS FRIENDLESS"]["responsive_backend_routing"]
                ["routes"]["test_route"]["effect_chain"]
            )
            self.assertTrue((root / "voice_route_listening_decisions.json").is_file())
            repeated = apply_decisions(project_root=root, decision_path=decision_path)
            self.assertEqual(repeated["status"], "already_applied")


if __name__ == "__main__":
    unittest.main()
