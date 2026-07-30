from __future__ import annotations

import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from experimental_prompt_routing import sha256_file
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    RecurringVoiceRoutingError,
    resolve_recurring_voice_route,
    routing_fingerprint,
    validate_recurring_voice_routing,
)
from responsive_voice_backend import (
    FishAudioBackend,
    ResponsiveVoiceBackendError,
)
from tts import TTSEngine


def write_wav(
    path: Path,
    *,
    value: bytes = b"\x01\x00",
    frames: int = 4800,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(value * frames)


class FakeResponsiveBackend:
    def __init__(self, *, available: bool) -> None:
        self.available = available
        self.calls: list[dict] = []

    def backend_available(self, backend: str) -> bool:
        return self.available

    def generate(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))
        write_wav(Path(kwargs["output_path"]), value=b"\x10\x00")

    def close(self) -> None:
        return None


class FakeMLXBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_clone(self, **kwargs) -> bool:
        self.calls.append(dict(kwargs))
        write_wav(Path(kwargs["output_path"]), value=b"\x20\x00")
        return True

    def generate_instruction_controlled_clone(self, **kwargs) -> bool:
        self.calls.append(dict(kwargs))
        write_wav(Path(kwargs["output_path"]), value=b"\x20\x00")
        return True


class RecurringVoiceRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = self.root / "clone_voices" / "primary" / "chris.wav"
        self.dry = (
            self.root
            / "production_prompt_routes"
            / "expressive"
            / "chris"
            / "dry.wav"
        )
        write_wav(self.identity, value=b"\x01\x00")
        write_wav(self.dry, value=b"\x02\x00")
        self.policy = {
            "schema_version": 1,
            "enabled": True,
            "default_route": "neutral",
            "fallback_backend": "qwen3_instruction_controlled",
            "evidence_round_id": "reviewed_round",
            "production_promotion_allowed": True,
            "routes": {
                "neutral": {
                    "backend": "indextts2_matched_control",
                    "instruction_keywords": ["neutral", "analytical"],
                    "identity_audio": self.identity.relative_to(self.root).as_posix(),
                    "identity_audio_sha256": sha256_file(self.identity),
                    "identity_text": "Chris identity reference.",
                    "performance_audio": self.identity.relative_to(self.root).as_posix(),
                    "performance_audio_sha256": sha256_file(self.identity),
                    "performance_text": "Chris identity reference.",
                    "control": {
                        "emotion_strength": 0.0,
                        "diffusion_steps": 8,
                        "num_beams": 1,
                        "greedy": True,
                        "max_mel_tokens": 600,
                    },
                    "production_promotion_allowed": True,
                },
                "dry_humour": {
                    "backend": "fish_s2_pro_cloud",
                    "instruction_keywords": ["dry humour", "wry"],
                    "identity_audio": self.identity.relative_to(self.root).as_posix(),
                    "identity_audio_sha256": sha256_file(self.identity),
                    "identity_text": "Chris identity reference.",
                    "performance_audio": self.dry.relative_to(self.root).as_posix(),
                    "performance_audio_sha256": sha256_file(self.dry),
                    "performance_text": "Chris dry reference.",
                    "control": {
                        "reference_id": "fish-reference-id",
                        "api_model_header": "s2.1-pro-free",
                        "prompt_mode": "rich_tag",
                        "tag": "dry understated humour",
                        "temperature": 0.7,
                        "top_p": 0.7,
                        "repetition_penalty": 1.2,
                    },
                    "production_promotion_allowed": True,
                },
            },
        }
        self.voice = {
            "type": "clone",
            "clone_backend": ROUTED_CLONE_BACKEND,
            "ref_audio": self.identity.relative_to(self.root).as_posix(),
            "ref_text": "Chris identity reference.",
            "seed": "130363",
            "responsive_backend_routing": self.policy,
            "responsive_backend_configuration_fingerprint": routing_fingerprint(
                validate_recurring_voice_routing(
                    self.policy,
                    project_root=self.root,
                    verify_audio=True,
                )
            ),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_policy_round_trips_without_absolute_paths(self) -> None:
        normalized = validate_recurring_voice_routing(
            self.policy,
            project_root=self.root,
            verify_audio=True,
        )
        self.assertNotIn("identity_audio_path", normalized["routes"]["neutral"])
        second = validate_recurring_voice_routing(
            normalized,
            project_root=self.root,
            verify_audio=True,
        )
        self.assertEqual(second, normalized)

    def test_keyword_explicit_and_default_routes(self) -> None:
        dry = resolve_recurring_voice_route(
            voice_data=self.voice,
            instruction="Dry humour with exact ironic timing.",
            project_root=self.root,
        )
        self.assertEqual(dry["route_key"], "dry_humour")
        self.assertEqual(dry["mapping_reason"], "instruction_keyword_match")
        explicit = resolve_recurring_voice_route(
            voice_data=self.voice,
            instruction="[prompt-route:dry_humour] Speak plainly.",
            project_root=self.root,
        )
        self.assertEqual(explicit["route_key"], "dry_humour")
        self.assertEqual(explicit["mapping_reason"], "explicit_tag")
        default = resolve_recurring_voice_route(
            voice_data=self.voice,
            instruction="Conversational pace.",
            project_root=self.root,
        )
        self.assertEqual(default["route_key"], "neutral")
        self.assertTrue(Path(default["identity_audio_path"]).is_file())

    def test_changed_audio_is_rejected(self) -> None:
        self.identity.write_bytes(b"changed")
        with self.assertRaisesRegex(RecurringVoiceRoutingError, "changed"):
            resolve_recurring_voice_route(
                voice_data=self.voice,
                instruction="Neutral.",
                project_root=self.root,
            )

    def test_tts_uses_specialist_route_when_available(self) -> None:
        fake = FakeResponsiveBackend(available=True)
        engine = TTSEngine({"tts": {"mode": "local", "language": "English"}})
        engine._init_responsive_voice_backend = lambda: fake
        output = self.root / "specialist.wav"
        result = engine.generate_clone_voice(
            "A line of dialogue.",
            "CHRIS",
            {"CHRIS": self.voice},
            str(output),
            instruct_text="Dry humour.",
        )
        self.assertTrue(result)
        self.assertTrue(output.is_file())
        self.assertEqual(fake.calls[0]["route"]["route_key"], "dry_humour")
        receipt = engine.consume_responsive_generation_receipt()
        self.assertEqual(receipt["responsive_voice_used_backend"], "fish_s2_pro_cloud")
        self.assertFalse(receipt["responsive_voice_fallback_used"])
        self.assertIsNone(receipt["responsive_voice_backend_error"])

    def test_unavailable_specialist_falls_back_to_qwen_reference(self) -> None:
        fake = FakeResponsiveBackend(available=False)
        mlx = FakeMLXBackend()
        engine = TTSEngine({"tts": {"mode": "local", "language": "English"}})
        engine._init_responsive_voice_backend = lambda: fake
        engine._init_mlx = lambda: mlx
        engine._use_mlx = True
        output = self.root / "fallback.wav"
        result = engine.generate_clone_voice(
            "A line of dialogue.",
            "CHRIS",
            {"CHRIS": self.voice},
            str(output),
            instruct_text="Dry humour.",
        )
        self.assertTrue(result)
        self.assertTrue(output.is_file())
        self.assertEqual(mlx.calls[0]["ref_audio"], str(self.identity.resolve()))
        self.assertEqual(mlx.calls[0]["ref_text"], "Chris identity reference.")
        receipt = engine.consume_responsive_generation_receipt()
        self.assertEqual(
            receipt["responsive_voice_used_backend"],
            "qwen3_instruction_controlled",
        )
        self.assertTrue(receipt["responsive_voice_fallback_used"])
        self.assertIn("unavailable", receipt["responsive_voice_backend_error"])

    def test_responsive_receipts_are_thread_local(self) -> None:
        engine = TTSEngine({"tts": {"mode": "local", "language": "English"}})
        engine._responsive_generation_state.receipt = {"thread": "main"}
        observed = []

        def worker() -> None:
            engine._responsive_generation_state.receipt = {"thread": "worker"}
            observed.append(engine.consume_responsive_generation_receipt())

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(observed, [{"thread": "worker"}])
        self.assertEqual(
            engine.consume_responsive_generation_receipt(),
            {"thread": "main"},
        )

    def test_fish_retries_with_lower_variance_before_fallback(self) -> None:
        payload_path = self.root / "fish-payload.wav"
        write_wav(payload_path, frames=48000, value=b"\x00\x20")
        payload = payload_path.read_bytes()

        class Response:
            status_code = 200
            content = payload

            @staticmethod
            def json():
                return {}

        backend = FishAudioBackend()
        backend._api_key = "test-key"
        output = self.root / "fish-result.wav"
        control = {
            "reference_id": "fish-reference-id",
            "api_model_header": "s2.1-pro-free",
            "prompt_mode": "full_alexandria_tag",
            "tag": "Speak with restrained vulnerability.",
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
        }
        with (
            patch.object(backend._session, "post", return_value=Response()) as request,
            patch(
                "responsive_voice_backend._verify_specialist_text",
                side_effect=[
                    ResponsiveVoiceBackendError("first word missing"),
                    {
                        "automatic_transcript": "Just she mentioned family and",
                        "word_error_rate": 0.0,
                        "first_word_present": True,
                    },
                ],
            ),
        ):
            receipt = backend.generate(
                text="Just... she mentioned family and...",
                control=control,
                output_path=output,
            )
        self.assertTrue(output.is_file())
        self.assertEqual(receipt["attempt_count"], 2)
        self.assertEqual(receipt["repair_strategy"], "lower_variance_retry")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].kwargs["json"]["temperature"], 0.7)
        self.assertEqual(request.call_args_list[1].kwargs["json"]["temperature"], 0.35)
        self.assertFalse(
            request.call_args_list[1].kwargs["json"]["condition_on_previous_chunks"]
        )

    def test_fish_raises_only_after_all_same_model_attempts_fail(self) -> None:
        payload_path = self.root / "fish-failure-payload.wav"
        write_wav(payload_path, frames=48000, value=b"\x00\x20")
        payload = payload_path.read_bytes()

        class Response:
            status_code = 200
            content = payload

            @staticmethod
            def json():
                return {}

        backend = FishAudioBackend()
        backend._api_key = "test-key"
        output = self.root / "fish-failed.wav"
        control = {
            "reference_id": "fish-reference-id",
            "api_model_header": "s2.1-pro-free",
            "prompt_mode": "full_alexandria_tag",
            "tag": "Speak with restrained vulnerability.",
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
        }
        with (
            patch.object(backend._session, "post", return_value=Response()) as request,
            patch(
                "responsive_voice_backend._verify_specialist_text",
                side_effect=ResponsiveVoiceBackendError("first word missing"),
            ),
        ):
            with self.assertRaisesRegex(
                ResponsiveVoiceBackendError,
                "failed verified same-model recovery",
            ):
                backend.generate(
                    text="Just... she mentioned family and...",
                    control=control,
                    output_path=output,
                )
        self.assertEqual(request.call_count, 3)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
