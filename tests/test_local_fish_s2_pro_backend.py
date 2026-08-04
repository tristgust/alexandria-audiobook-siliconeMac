from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from responsive_voice_backend import (
    FISH_ROUTE_BACKEND_ID,
    LOCAL_FISH_ROUTE_BACKEND_ID,
    LocalFishS2ProBackend,
    ResponsiveVoiceBackend,
    ResponsiveVoiceBackendError,
)


class FakeMemory:
    @contextmanager
    def job(self, component_ids, *, label):
        self.component_ids = tuple(component_ids)
        self.label = label
        yield

    def status(self):
        return {"residents": []}


class FakeFishModel:
    sample_rate = 24000

    def __init__(self) -> None:
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return [object()]


class FakeLocalUnavailable:
    def available(self) -> bool:
        return False


class FakeHostedFish:
    def __init__(self) -> None:
        self.calls = []

    def available(self) -> bool:
        return True

    def generate_zero_shot(self, **kwargs):
        self.calls.append(dict(kwargs))
        Path(kwargs["output_path"]).write_bytes(b"hosted-fish")
        return {
            "attempt_count": 1,
            "repair_strategy": "primary",
            "text_verification": {"word_error_rate": 0.0},
        }


def control() -> dict:
    return {
        "prompt_mode": "full_alexandria_tag",
        "tag": "Dry banter and professional sarcasm.",
        "temperature": 0.7,
        "top_p": 0.7,
        "top_k": 30,
        "max_tokens": 500,
        "chunk_length": 300,
        "speed": 1.0,
        "license_scope": "noncommercial_research",
        "hosted_fallback": {
            "api_model_header": "s2.1-pro-free",
            "prompt_mode": "full_alexandria_tag",
            "tag": "Dry streetwise teasing.",
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
            "reference_mode": "inline_zero_shot",
        },
    }


class LocalFishS2ProBackendTests(unittest.TestCase):
    def test_local_generation_preserves_reviewed_inline_tag_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(b"reference")
            output = root / "output.wav"
            memory = FakeMemory()
            backend = LocalFishS2ProBackend(model_residency=memory)
            model = FakeFishModel()
            backend._model = model
            with (
                patch(
                    "responsive_voice_backend._collect_mlx_audio",
                    return_value=(np.zeros(2400, dtype=np.float32), 24000),
                ),
                patch(
                    "mlx_audio.utils.load_audio",
                    return_value="loaded-reference",
                ),
                patch("responsive_voice_backend._finalize_specialist_audio"),
                patch(
                    "responsive_voice_backend._verify_specialist_text",
                    return_value={"word_error_rate": 0.0},
                ),
                patch(
                    "responsive_voice_backend._verify_production_encoded_text",
                    return_value={"word_error_rate": 0.0},
                ),
            ):
                receipt = backend.generate(
                    text="Dream on, kid.",
                    identity_audio=str(reference),
                    identity_text="Keep up with the news.",
                    control=control(),
                    output_path=output,
                    seed=130363,
                    maximum_word_error_rate=0.15,
                    require_first_word=True,
                )
            self.assertTrue(output.is_file())
            self.assertEqual(memory.component_ids, ("mlx_fish_s2_pro",))
            self.assertEqual(
                model.calls[0]["text"],
                "[Dry banter and professional sarcasm.] Dream on, kid.",
            )
            self.assertEqual(model.calls[0]["ref_audio"], "loaded-reference")
            self.assertEqual(receipt["used_backend"], LOCAL_FISH_ROUTE_BACKEND_ID)
            self.assertFalse(receipt["fallback_used"])
            self.assertEqual(receipt["license_scope"], "noncommercial_research")

    def test_responsive_backend_uses_hosted_fish_when_local_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(b"reference")
            output = root / "output.wav"
            backend = ResponsiveVoiceBackend(model_residency=FakeMemory())
            backend.local_fish = FakeLocalUnavailable()
            hosted = FakeHostedFish()
            backend.fish = hosted
            route = {
                "backend": LOCAL_FISH_ROUTE_BACKEND_ID,
                "identity_audio_path": str(reference),
                "identity_text": "Keep up with the news.",
                "control": control(),
                "verification": {
                    "maximum_word_error_rate": 0.15,
                    "require_first_word": True,
                },
            }
            receipt = backend.generate(
                route=route,
                text="Dream on, kid.",
                output_path=output,
                seed=130363,
            )
            self.assertTrue(output.is_file())
            self.assertEqual(receipt["used_backend"], FISH_ROUTE_BACKEND_ID)
            self.assertTrue(receipt["fallback_used"])
            self.assertIn("unavailable", receipt["primary_backend_error"])
            self.assertEqual(
                receipt["repair_strategy"],
                "hosted_s21_pro_free_fallback",
            )
            self.assertEqual(len(hosted.calls), 1)

    def test_both_fish_paths_fail_closed_for_qwen_fallback(self) -> None:
        backend = ResponsiveVoiceBackend(model_residency=FakeMemory())
        backend.local_fish = FakeLocalUnavailable()
        backend.fish = type(
            "UnavailableHosted",
            (),
            {"available": lambda self: False},
        )()
        route = {
            "backend": LOCAL_FISH_ROUTE_BACKEND_ID,
            "identity_audio_path": "/missing.wav",
            "identity_text": "Reference.",
            "control": control(),
        }
        with self.assertRaises(ResponsiveVoiceBackendError):
            backend.generate(
                route=route,
                text="Line.",
                output_path="/tmp/unused.wav",
                seed=130363,
            )


if __name__ == "__main__":
    unittest.main()
