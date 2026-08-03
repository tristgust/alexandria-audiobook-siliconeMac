from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path

from controlled_clone_preview import generate_controlled_clone_preview
from generation_state import fingerprint_value
from production_voice_evidence import compute_evidence_set_fingerprint
from tts import TTSEngine


def write_wav(path: Path, *, frames: int = 2400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * frames)


def evidence_fixture(root: Path) -> tuple[Path, Path]:
    directory = root / "production_voice_evidence"
    audio = directory / "neutral.wav"
    write_wav(audio)
    transcript = "This is the approved reference."
    operations = [{"operation": "none"}]
    sample = {
        "sample_id": "sample_1000000000000001",
        "order": 0,
        "audio_path": "neutral.wav",
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "transcript": transcript,
        "transcript_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "language": "English",
        "provenance": {
            "source_kind": "owned_recording",
            "source_id": "source-neutral",
            "permission_basis": "Owned recording.",
            "model_id": None,
            "model_revision": None,
            "recorded_at_utc": "2026-08-03T16:00:00Z",
        },
        "quality": {
            "approved": True,
            "reviewed_at_utc": "2026-08-03T16:00:00Z",
            "identity_score": 5,
            "naturalness_score": 5,
            "artifact_severity": 1,
            "text_match": True,
        },
        "delivery": {
            "approved": True,
            "labels": ["neutral"],
            "instruction": "Steady approved delivery.",
            "score": 5,
        },
        "compatibility": {
            "backends": ["qwen3_instruction_controlled"],
            "languages": ["English"],
            "speaker_classes": ["primary_character"],
        },
        "preprocessing": {
            "pipeline_id": "exact_v1",
            "operations": operations,
            "fingerprint": fingerprint_value(
                {"pipeline_id": "exact_v1", "operations": operations}
            ),
        },
        "pronunciation": {
            "registry_fingerprint": "a" * 64,
            "entry_ids": ["pronunciation_fixture"],
        },
        "advisory": {
            "speaker_label": "speaker-one",
            "diarization_cluster": "cluster-one",
            "speaker_embedding_fingerprint": "b" * 64,
            "asr_tags": ["clean"],
            "learned_emotion_labels": ["neutral"],
        },
    }
    value = {
        "schema_version": 1,
        "voice_id": "voice_doctor",
        "canonical_name": "The Doctor",
        "character_id": "character_0123456789abcdef0123",
        "status": "approved",
        "language": "English",
        "identity_binding": {
            "status": "approved",
            "source": "cast",
            "approved_at_utc": "2026-08-03T16:00:00Z",
            "notes": "Approved in Cast.",
        },
        "samples": [sample],
        "default_sample_id": sample["sample_id"],
        "speaker_evidence_review": {
            "status": "not_required",
            "decision": "none",
            "reviewed_at_utc": None,
            "notes": "",
        },
        "evidence_set_fingerprint": "0" * 64,
    }
    value["evidence_set_fingerprint"] = compute_evidence_set_fingerprint(value)
    path = directory / "evidence.json"
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path, audio


class FakePromptModel:
    def __init__(self) -> None:
        self.calls = 0

    def create_voice_clone_prompt(self, *, ref_audio, ref_text):
        self.calls += 1
        return {"call": self.calls, "text": ref_text, "frames": len(ref_audio[0])}


class ProductionVoicePromptRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.evidence_path, self.audio = evidence_fixture(self.root)
        self.voice = {
            "type": "clone",
            "clone_backend": "qwen3_instruction_controlled",
            "production_voice_evidence_path": (
                "production_voice_evidence/evidence.json"
            ),
            "production_voice_language": "English",
            "character_style": "Playful but recognizably the same character.",
            "seed": 42,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_preview_and_production_use_identical_prompt_resolution(self) -> None:
        captured = {}

        def generate(**kwargs):
            captured.update(kwargs)
            write_wav(Path(kwargs["output_path"]))
            return True

        preview = generate_controlled_clone_preview(
            root_dir=self.root,
            ref_audio="",
            ref_text="",
            text="A preview sentence.",
            instruct="Playfully.",
            character_style=self.voice["character_style"],
            seed=42,
            production_voice_evidence_path=(
                self.voice["production_voice_evidence_path"]
            ),
            language="English",
            generator=generate,
        )
        engine = TTSEngine({"tts": {"mode": "external", "language": "English"}})
        config, selection = engine._resolve_reference_bank_voice_config(
            "DOCTOR",
            {"DOCTOR": self.voice},
            "Playfully.",
            project_root=self.root,
        )
        production = selection["production_voice"]
        self.assertEqual(
            preview["production_voice_evidence"]["sample_id"],
            production["sample_id"],
        )
        self.assertEqual(
            preview["production_voice_evidence"]["prompt_fingerprint"],
            production["prompt_fingerprint"],
        )
        self.assertEqual(captured["instruct"], production["instruction"])
        self.assertEqual(config["DOCTOR"]["ref_text"], production["ref_text"])
        self.assertFalse(
            preview["production_voice_evidence"]["advisory_identity_used"]
        )

    def test_clone_prompt_cache_invalidates_for_any_prompt_dependency(self) -> None:
        engine = TTSEngine({"tts": {"mode": "external"}})
        model = FakePromptModel()
        base = {
            "DOCTOR": {
                "type": "clone",
                "ref_audio": str(self.audio),
                "ref_audio_sha256": hashlib.sha256(
                    self.audio.read_bytes()
                ).hexdigest(),
                "ref_text": "This is the approved reference.",
                "production_voice_evidence_fingerprint": "c" * 64,
                "production_voice_prompt_fingerprint": "d" * 64,
                "production_voice_preprocessing_fingerprint": "e" * 64,
                "production_voice_pronunciation_fingerprint": "f" * 64,
            }
        }
        first = engine._get_clone_prompt("DOCTOR", base, model=model)
        second = engine._get_clone_prompt("DOCTOR", base, model=model)
        self.assertIs(first, second)
        self.assertEqual(model.calls, 1)
        changed = json.loads(json.dumps(base))
        changed["DOCTOR"]["production_voice_pronunciation_fingerprint"] = "0" * 64
        third = engine._get_clone_prompt("DOCTOR", changed, model=model)
        self.assertEqual(model.calls, 2)
        self.assertNotEqual(first, third)

    def test_generation_receipt_never_promotes_advisory_identity(self) -> None:
        engine = TTSEngine({"tts": {"mode": "external", "language": "English"}})
        engine._external_generate_clone = lambda *args, **kwargs: True
        output = self.root / "output.wav"
        success = engine.generate_clone_voice(
            "A production sentence.",
            "DOCTOR",
            {"DOCTOR": self.voice},
            str(output),
            instruct_text="Playfully.",
        )
        self.assertTrue(success)
        receipt = engine.consume_responsive_generation_receipt()
        self.assertTrue(receipt["production_voice_evidence_used"])
        self.assertFalse(receipt["production_voice_advisory_identity_used"])


if __name__ == "__main__":
    unittest.main()
