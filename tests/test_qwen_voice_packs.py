from __future__ import annotations

import importlib
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path


def qvoice_bytes(
    *,
    encoder_dimension: int = 2048,
    reference_text: bytes = b"A clean English reference.",
    reference_frames: int = 2,
    embedding_value: float = 0.125,
    flags: int = 0b110,
    trailing: bytes = b"",
) -> bytes:
    header = b"QVCE" + struct.pack("<II", 3, encoder_dimension)
    embedding = struct.pack(
        f"<{encoder_dimension}f",
        *([embedding_value] * encoder_dimension),
    )
    codes = struct.pack(
        f"<{reference_frames * 16}i",
        *[index % 2048 for index in range(reference_frames * 16)],
    )
    meta = (
        b"META"
        + struct.pack("<I", 2)
        + b"english\x00".ljust(16, b"\x00")
        + struct.pack("<II f", 2048, encoder_dimension, 0.16)
        + b"Fixture Voice\x00".ljust(64, b"\x00")
        + struct.pack("<I", flags)
    )
    return (
        header
        + embedding
        + struct.pack("<I", len(reference_text))
        + reference_text
        + struct.pack("<I", reference_frames)
        + codes
        + meta
        + trailing
    )


def load_pack_module():
    try:
        return importlib.import_module("qwen_voice_packs")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "Alexandria has no community Qwen voice-pack parser yet."
        ) from exc


class QVoiceParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_pack(self, payload: bytes, name: str = "voice.qvoice") -> Path:
        path = self.root / name
        path.write_bytes(payload)
        return path

    def test_portable_v3_icl_prompt_parses_to_stable_manifest(self) -> None:
        module = load_pack_module()
        path = self.write_pack(qvoice_bytes())

        pack = module.parse_qvoice(path, expected_encoder_dimension=2048)

        self.assertEqual(pack.version, 3)
        self.assertEqual(pack.encoder_dimension, 2048)
        self.assertEqual(pack.reference_text, "A clean English reference.")
        self.assertEqual(pack.reference_frames, 2)
        self.assertEqual(pack.language, "english")
        self.assertEqual(pack.voice_name, "Fixture Voice")
        self.assertTrue(pack.has_icl)
        self.assertTrue(pack.source_is_base_model)
        self.assertEqual(pack.sections, ("META",))
        self.assertEqual(len(pack.sha256), 64)
        inspection = module.inspect_community_pack(path)
        self.assertEqual(inspection.state.value, "mlx_conversion_required")
        self.assertIn("cannot preserve", inspection.message)

    def test_xvector_graft_without_reference_codes_is_supported(self) -> None:
        module = load_pack_module()
        path = self.write_pack(
            qvoice_bytes(reference_text=b"", reference_frames=0, flags=0b101)
        )

        pack = module.parse_qvoice(path, expected_encoder_dimension=2048)

        self.assertTrue(pack.xvector_only)
        self.assertFalse(pack.has_icl)
        self.assertIsNone(pack.reference_text)
        self.assertEqual(pack.reference_codes, ())

        inspection = module.inspect_community_pack(path)

        self.assertEqual(inspection.family.value, "qvoice_graft")
        self.assertNotIn("ICL", inspection.message)

    def test_truncated_payload_fails_at_the_named_boundary(self) -> None:
        module = load_pack_module()
        complete = qvoice_bytes()
        path = self.write_pack(complete[: 12 + (2048 * 4) + 3])

        with self.assertRaises(module.QwenVoicePackError) as caught:
            module.parse_qvoice(path, expected_encoder_dimension=2048)

        self.assertEqual(caught.exception.code, "qvoice_truncated")
        self.assertIn("reference text length", str(caught.exception))

    def test_encoder_dimension_mismatch_fails_before_tensor_use(self) -> None:
        module = load_pack_module()
        path = self.write_pack(qvoice_bytes(encoder_dimension=1024))

        with self.assertRaises(module.QwenVoicePackError) as caught:
            module.parse_qvoice(path, expected_encoder_dimension=2048)

        self.assertEqual(caught.exception.code, "qvoice_model_mismatch")

    def test_weight_delta_is_rejected_instead_of_losing_emotion_control(self) -> None:
        module = load_pack_module()
        path = self.write_pack(qvoice_bytes(trailing=b"WDLT" + struct.pack("<I", 2048)))

        with self.assertRaises(module.QwenVoicePackError) as caught:
            module.parse_qvoice(path, expected_encoder_dimension=2048)

        self.assertEqual(caught.exception.code, "qvoice_frozen_weights_unsupported")

    def test_nonfinite_embedding_is_rejected(self) -> None:
        module = load_pack_module()
        path = self.write_pack(qvoice_bytes(embedding_value=math.inf))

        with self.assertRaises(module.QwenVoicePackError) as caught:
            module.parse_qvoice(path, expected_encoder_dimension=2048)

        self.assertEqual(caught.exception.code, "qvoice_invalid_embedding")

    def test_nonfinite_weight_override_is_rejected_before_runtime(self) -> None:
        module = load_pack_module()
        payload_size = (2048 * 2048 * 2) + (2048 * 4) + (2048 * 2048 * 2) \
            + (2048 * 4) + (2048 * 2048 * 2)
        payload = bytearray(payload_size)
        payload[0:2] = struct.pack("<H", 0x7FC1)
        trailing = b"WOVR" + struct.pack("<III", 2048, 2048, 2048) + payload
        path = self.write_pack(qvoice_bytes(
            reference_text=b"", reference_frames=0, flags=0b101, trailing=trailing,
        ))

        with self.assertRaises(module.QwenVoicePackError) as caught:
            module.inspect_community_pack(path)

        self.assertEqual(caught.exception.code, "qvoice_invalid_weight_override")

    def test_unknown_trailing_section_is_rejected(self) -> None:
        module = load_pack_module()
        path = self.write_pack(qvoice_bytes(trailing=b"NOPE"))

        with self.assertRaises(module.QwenVoicePackError) as caught:
            module.parse_qvoice(path, expected_encoder_dimension=2048)

        self.assertEqual(caught.exception.code, "qvoice_unknown_section")


class CommunityPackFamilyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_peft_embedding_bundle_is_identified_without_license_block(self) -> None:
        module = load_pack_module()
        bundle = self.root / "peft"
        bundle.mkdir()
        (bundle / "adapter_config.json").write_text(
            json.dumps({"peft_type": "LORA", "r": 16, "lora_alpha": 32}),
            encoding="utf-8",
        )
        (bundle / "adapter_model.safetensors").write_bytes(b"safe-adapter")
        (bundle / "speaker_embedding.safetensors").write_bytes(b"safe-embedding")
        (bundle / "tts_config.json").write_text(
            json.dumps(
                {
                    "tts_model_type": "custom_voice",
                    "tts_model_size": "1b7",
                    "talker_config": {"spk_id": {"reader": 3000}},
                }
            ),
            encoding="utf-8",
        )
        (bundle / "voice_pack.json").write_text("{not-json", encoding="utf-8")

        result = module.inspect_community_pack(bundle)

        self.assertEqual(result.family.value, "peft_speaker_bundle")
        self.assertIsNone(result.license_name)
        self.assertEqual(result.state.value, "mlx_conversion_required")
        self.assertFalse(result.production_supported)

    def test_peft_bundle_without_adapter_weights_is_not_detected(self) -> None:
        module = load_pack_module()
        bundle = self.root / "incomplete-peft"
        bundle.mkdir()
        for name, payload in (
            ("adapter_config.json", {}),
            ("tts_config.json", {"tts_model_type": "custom_voice"}),
        ):
            (bundle / name).write_text(json.dumps(payload), encoding="utf-8")
        (bundle / "speaker_embedding.safetensors").write_bytes(b"embedding")

        with self.assertRaises(module.QwenVoicePackError) as caught:
            module.inspect_community_pack(bundle)

        self.assertEqual(caught.exception.code, "community_pack_unrecognized")

    def test_full_english_custom_voice_checkpoint_is_identified(self) -> None:
        module = load_pack_module()
        bundle = self.root / "checkpoint"
        bundle.mkdir()
        (bundle / "config.json").write_text(
            json.dumps(
                {
                    "tts_model_type": "custom_voice",
                    "tts_model_size": "1b7",
                    "tokenizer_type": "qwen3_tts_tokenizer_12hz",
                    "talker_config": {"spk_id": {"ljspeech_voice": 3000}},
                }
            ),
            encoding="utf-8",
        )
        (bundle / "model.safetensors").write_bytes(b"safe-model")

        result = module.inspect_community_pack(bundle)

        self.assertEqual(result.family.value, "full_custom_voice_checkpoint")
        self.assertEqual(result.state.value, "mlx_conversion_required")
        self.assertEqual(result.speakers, ("ljspeech_voice",))
        self.assertIsNone(result.license_name)


if __name__ == "__main__":
    unittest.main()
