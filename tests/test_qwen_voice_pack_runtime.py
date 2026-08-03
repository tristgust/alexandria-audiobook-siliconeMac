from __future__ import annotations

import tempfile
import threading
import unittest
import struct
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mlx.core as mx
import numpy as np
import soundfile as sf

from mlx_backend import MLXBackend
from tests.test_qwen_voice_packs import qvoice_bytes
from tts import TTSEngine


class FakeCommunityMLXBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_community_qwen_pack(self, **kwargs):
        self.calls.append(dict(kwargs))
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 24000
        duration = max(0.8, len(kwargs["text"]) * 0.05)
        count = max(1, round(sample_rate * duration))
        timeline = np.arange(count, dtype=np.float32) / sample_rate
        audio = 0.1 * np.sin(2.0 * np.pi * 7.0 * timeline)
        sf.write(
            output,
            audio,
            sample_rate,
            subtype="FLOAT",
        )
        return True


class CommunityQVoiceTTSRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pack = self.root / "community_qwen_packs" / "qvoice_fixture" / "voice.qvoice"
        self.pack.parent.mkdir(parents=True)
        self.pack.write_bytes(b"QVCE-fixture")
        self.output = self.root / "audio" / "line.wav"
        self.backend = FakeCommunityMLXBackend()
        self.engine = TTSEngine.__new__(TTSEngine)
        self.engine._use_mlx = True
        self.engine._mode = "local"
        self.engine._language = "English"
        self.engine._init_mlx = lambda: self.backend
        self.engine._generation_metadata = {}
        self.engine._generation_metadata_lock = threading.RLock()
        self.engine._responsive_generation_state = threading.local()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def voice_config(self) -> dict:
        return {
            "GREENEYE": {
                "type": "community_qvoice",
                "voice": "O. Henry Reader",
                "character_style": "An older English storyteller with dry warmth.",
                "community_pack_id": "qvoice_fixture",
                "community_pack_path": (
                    "community_qwen_packs/qvoice_fixture/voice.qvoice"
                ),
                "community_pack_family": "qvoice_graft",
                "community_pack_runtime": "mlx_qvoice_graft",
                "community_pack_sha256": "a" * 64,
                "community_pack_approval_fingerprint": "b" * 64,
                "seed": 130363,
            }
        }

    def test_route_preserves_identity_line_direction_language_and_seed(self) -> None:
        result = self.engine.generate_voice(
            "You came back before the lamps went out.",
            "Suddenly delighted, breathless, and openly relieved.",
            "GREENEYE",
            self.voice_config(),
            str(self.output),
        )

        self.assertTrue(result)
        self.assertEqual(len(self.backend.calls), 1)
        call = self.backend.calls[0]
        self.assertEqual(call["pack_path"], str(self.pack.resolve()))
        self.assertEqual(call["expected_sha256"], "a" * 64)
        self.assertEqual(call["approval_fingerprint"], "b" * 64)
        self.assertEqual(call["language"], "English")
        self.assertEqual(call["seed"], 130363)
        self.assertIn("older English storyteller", call["instruct"])
        self.assertIn("Suddenly delighted", call["instruct"])

    def test_community_qvoice_supports_deterministic_generation_seeds(self) -> None:
        self.assertTrue(
            self.engine.supports_generation_seed(
                self.voice_config()["GREENEYE"]
            )
        )

    def test_missing_approval_fails_before_mlx_generation(self) -> None:
        config = self.voice_config()
        config["GREENEYE"].pop("community_pack_approval_fingerprint")

        with self.assertRaisesRegex(ValueError, "listening approval"):
            self.engine.generate_voice(
                "This must not synthesize.",
                "Quietly.",
                "GREENEYE",
                config,
                str(self.output),
            )

        self.assertEqual(self.backend.calls, [])

    def test_batch_keeps_each_community_voice_direction_and_seed(self) -> None:
        self.engine._compile_codec_enabled = False
        self.engine._clear_gpu_cache = lambda: None
        result = self.engine.generate_batch(
            [
                {
                    "index": 1,
                    "speaker": "GREENEYE",
                    "text": "First line.",
                    "instruct": "Frightened and breathless.",
                    "generation_seed": 11,
                },
                {
                    "index": 2,
                    "speaker": "GREENEYE",
                    "text": "Second line.",
                    "instruct": "Dryly amused and relaxed.",
                    "generation_seed": 22,
                },
            ],
            self.voice_config(),
            str(self.output.parent),
        )

        self.assertEqual(result, {"completed": [1, 2], "failed": []})
        self.assertEqual([call["seed"] for call in self.backend.calls], [11, 22])
        self.assertIn("Frightened", self.backend.calls[0]["instruct"])
        self.assertIn("Dryly amused", self.backend.calls[1]["instruct"])


class QVoiceMLXRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _pack(self, *, with_overrides: bool = False) -> Path:
        trailing = b""
        if with_overrides:
            hidden = text_hidden = codec_vocabulary = 2048
            tpad = b"TPAD" + struct.pack("<I", hidden)
            tpad += struct.pack(f"<{hidden * 3}f", *([0.25] * (hidden * 3)))
            weight_bytes = (
                (text_hidden * text_hidden * 2)
                + (text_hidden * 4)
                + (hidden * text_hidden * 2)
                + (hidden * 4)
                + (codec_vocabulary * hidden * 2)
            )
            trailing = (
                tpad
                + b"WOVR"
                + struct.pack("<III", hidden, text_hidden, codec_vocabulary)
                + bytes(weight_bytes)
            )
        path = self.root / "reader.qvoice"
        path.write_bytes(
            qvoice_bytes(
                reference_text=b"",
                reference_frames=0,
                flags=0b101,
                trailing=trailing,
            )
        )
        return path

    def test_wovr_and_embedding_overrides_restore_even_after_failure(self) -> None:
        from qvoice_mlx_runtime import apply_qvoice_graft, load_qvoice_graft

        path = self._pack(with_overrides=True)
        tensors = load_qvoice_graft(path)
        original_embedding = object()
        original_fc1_weight = object()
        original_fc1_bias = object()
        original_fc2_weight = object()
        original_fc2_bias = object()
        talker = SimpleNamespace(
            text_projection=SimpleNamespace(
                linear_fc1=SimpleNamespace(
                    weight=original_fc1_weight,
                    bias=original_fc1_bias,
                ),
                linear_fc2=SimpleNamespace(
                    weight=original_fc2_weight,
                    bias=original_fc2_bias,
                ),
            ),
            get_input_embeddings=lambda: original_embedding,
        )
        model = SimpleNamespace(
            config=SimpleNamespace(
                tts_model_type="custom_voice",
                talker_config=SimpleNamespace(spk_id={"ryan": 3000}),
            ),
            talker=talker,
            _prepare_generation_inputs=lambda *args, **kwargs: None,
        )

        with self.assertRaisesRegex(RuntimeError, "forced"):
            with apply_qvoice_graft(model, tensors, speaker="Ryan"):
                self.assertEqual(
                    tuple(talker.text_projection.linear_fc1.weight.shape),
                    (2048, 2048),
                )
                self.assertIsNot(talker.get_input_embeddings(), original_embedding)
                raise RuntimeError("forced")

        self.assertIs(talker.text_projection.linear_fc1.weight, original_fc1_weight)
        self.assertIs(talker.text_projection.linear_fc1.bias, original_fc1_bias)
        self.assertIs(talker.text_projection.linear_fc2.weight, original_fc2_weight)
        self.assertIs(talker.text_projection.linear_fc2.bias, original_fc2_bias)
        self.assertIs(talker.get_input_embeddings(), original_embedding)

    def test_mlx_generation_passes_instruction_seed_and_restores_model(self) -> None:
        path = self._pack()
        expected_sha = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        calls = []

        class FakeModel:
            sample_rate = 24_000
            supported_speakers = ["Ryan"]

            def __init__(self):
                self.config = SimpleNamespace(
                    tts_model_type="custom_voice",
                    talker_config=SimpleNamespace(spk_id={"ryan": 3000}),
                )
                self.original_embedding = object()
                self.talker = SimpleNamespace(
                    get_input_embeddings=lambda: self.original_embedding,
                )
                self._prepare_generation_inputs = lambda *args, **kwargs: None

            def generate(self, text, **kwargs):
                calls.append({"text": text, **kwargs})
                self.assert_grafted = (
                    self.talker.get_input_embeddings() is not self.original_embedding
                )
                timeline = np.arange(24_000, dtype=np.float32) / 24_000
                waveform = 0.1 * np.sin(2.0 * np.pi * 180.0 * timeline)
                yield SimpleNamespace(audio=mx.array(waveform))

        model = FakeModel()
        backend = MLXBackend(language="English")
        backend._model = lambda kind: model
        backend._memory.job = lambda *_args, **_kwargs: nullcontext()
        output = self.root / "result.wav"

        with patch.object(mx.random, "seed") as seed:
            result = backend.generate_community_qvoice(
                text="I knew you would return.",
                pack_path=str(path),
                expected_sha256=expected_sha,
                approval_fingerprint="b" * 64,
                instruct="Quietly heartbroken, then relieved.",
                language="English",
                output_path=str(output),
                seed=104729,
                request_label="GREENEYE",
            )

        self.assertTrue(result)
        self.assertTrue(output.is_file())
        self.assertTrue(model.assert_grafted)
        self.assertIs(model.talker.get_input_embeddings(), model.original_embedding)
        self.assertEqual(calls[0]["voice"], "Ryan")
        self.assertEqual(calls[0]["lang_code"], "English")
        self.assertEqual(calls[0]["max_tokens"], 101)
        self.assertEqual(
            calls[0]["instruct"],
            "Quietly heartbroken, then relieved.",
        )
        seed.assert_called_once_with(104729)


if __name__ == "__main__":
    unittest.main()
