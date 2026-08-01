from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import mlx.core as mx
import mlx.nn as nn

from community_qwen_mlx_runtime import (
    CommunityQwenRuntimeError,
    RuntimeLoraLinear,
    apply_peft_speaker_bundle,
    source_inventory,
    verify_source_inventory,
)


class _Attention(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.q_proj = nn.Linear(3, 2, bias=False)
        self.q_proj.weight = mx.zeros((2, 3))


class _Layer(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.self_attn = _Attention()


class _TalkerModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = [_Layer()]
        self.codec_embedding = nn.Embedding(4, 3)
        self.codec_embedding.weight = mx.zeros((4, 3))


class _Talker(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.model = _TalkerModel()


class _FakeCustomVoice(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.talker = _Talker()
        self.config = SimpleNamespace(
            talker_config=SimpleNamespace(spk_id={"base": 0})
        )
        self.supported_speakers = ["base"]


class CommunityQwenMlxRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _bundle(self, *, use_dora: bool = False) -> Path:
        bundle = self.root / "peft"
        bundle.mkdir()
        (bundle / "adapter_config.json").write_text(
            json.dumps({
                "peft_type": "LORA",
                "r": 1,
                "lora_alpha": 2,
                "use_dora": use_dora,
            }),
            encoding="utf-8",
        )
        mx.save_safetensors(
            str(bundle / "adapter_model.safetensors"),
            {
                "base_model.model.model.layers.0.self_attn.q_proj.lora_A.default.weight": mx.array([[1.0, 0.0, 0.0]]),
                "base_model.model.model.layers.0.self_attn.q_proj.lora_B.default.weight": mx.array([[2.0], [3.0]]),
            },
        )
        mx.save_safetensors(
            str(bundle / "speaker_embedding.safetensors"),
            {"speaker_embedding": mx.array([[0.1, 0.2, 0.3]])},
        )
        (bundle / "tts_config.json").write_text(
            json.dumps({
                "tts_model_type": "custom_voice",
                "talker_config": {"spk_id": {"reader": 2}},
            }),
            encoding="utf-8",
        )
        return bundle

    def test_peft_overlay_applies_lora_and_speaker_embedding_in_memory(self) -> None:
        model = _FakeCustomVoice()

        speaker = apply_peft_speaker_bundle(model, self._bundle())

        self.assertEqual(speaker, "reader")
        layer = model.talker.model.layers[0].self_attn.q_proj
        self.assertIsInstance(layer, RuntimeLoraLinear)
        output = layer(mx.array([[1.0, 2.0, 3.0]]))
        mx.eval(output)
        self.assertEqual(output.tolist(), [[4.0, 6.0]])
        embedding = model.talker.model.codec_embedding.weight[2]
        mx.eval(embedding)
        for actual, expected in zip(embedding.tolist(), [0.1, 0.2, 0.3]):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual(model.config.talker_config.spk_id["reader"], 2)
        self.assertIn("reader", model.supported_speakers)

    def test_source_inventory_supports_huggingface_style_symlink_files(self) -> None:
        source = self.root / "snapshot"
        blobs = self.root / "blobs"
        source.mkdir()
        blobs.mkdir()
        target = blobs / "adapter"
        target.write_bytes(b"weights")
        linked = source / "adapter_model.safetensors"
        linked.symlink_to(target)

        inventory = source_inventory(source, include_hashes=True)

        self.assertTrue(inventory[0]["is_symlink"])
        self.assertEqual(inventory[0]["path"], "adapter_model.safetensors")
        self.assertEqual(verify_source_inventory(source, inventory), source.resolve())

        target.write_bytes(b"changed")
        with self.assertRaises(CommunityQwenRuntimeError):
            verify_source_inventory(source, inventory)

    def test_dora_bundle_fails_before_mutating_the_model(self) -> None:
        model = _FakeCustomVoice()

        with self.assertRaises(CommunityQwenRuntimeError) as caught:
            apply_peft_speaker_bundle(model, self._bundle(use_dora=True))

        self.assertIn("DoRA", str(caught.exception))
        self.assertIsInstance(
            model.talker.model.layers[0].self_attn.q_proj,
            nn.Linear,
        )


if __name__ == "__main__":
    unittest.main()
