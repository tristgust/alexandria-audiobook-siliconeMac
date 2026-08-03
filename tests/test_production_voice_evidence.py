from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path

from generation_state import fingerprint_value
from production_voice_evidence import (
    ProductionVoiceEvidenceConflictError,
    ProductionVoiceEvidenceValidationError,
    compute_evidence_set_fingerprint,
    resolve_production_voice_prompt,
    resolve_evidence_set_path,
    validate_production_voice_evidence_set,
)


def write_wav(path: Path, *, frames: int = 2400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * frames)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preprocessing(
    pipeline_id: str = "source_exact_v1",
    operations: list[dict] | None = None,
) -> dict:
    values = operations or [{"operation": "none"}]
    return {
        "pipeline_id": pipeline_id,
        "operations": values,
        "fingerprint": fingerprint_value(
            {"pipeline_id": pipeline_id, "operations": values}
        ),
    }


def sample(
    *,
    sample_id: str,
    order: int,
    audio_path: str,
    audio_sha256: str,
    transcript: str,
    labels: list[str],
    instruction: str,
    speaker_label: str = "reviewed-speaker",
    cluster: str = "cluster-a",
    preprocessing_value: dict | None = None,
    registry_fingerprint: str | None = None,
) -> dict:
    return {
        "sample_id": sample_id,
        "order": order,
        "audio_path": audio_path,
        "audio_sha256": audio_sha256,
        "transcript": transcript,
        "transcript_sha256": hashlib.sha256(
            transcript.encode("utf-8")
        ).hexdigest(),
        "language": "English",
        "provenance": {
            "source_kind": "owned_recording",
            "source_id": f"source-{sample_id}",
            "permission_basis": "Owned and approved for this project.",
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
            "labels": labels,
            "instruction": instruction,
            "score": 5,
        },
        "compatibility": {
            "backends": ["qwen3_instruction_controlled"],
            "languages": ["English"],
            "speaker_classes": ["primary_character"],
        },
        "preprocessing": preprocessing_value or preprocessing(),
        "pronunciation": {
            "registry_fingerprint": registry_fingerprint,
            "entry_ids": [],
        },
        "advisory": {
            "speaker_label": speaker_label,
            "diarization_cluster": cluster,
            "speaker_embedding_fingerprint": "e" * 64,
            "asr_tags": ["clean"],
            "learned_emotion_labels": labels,
        },
    }


def evidence(samples: list[dict], *, review: dict | None = None) -> dict:
    value = {
        "schema_version": 1,
        "voice_id": "voice_narrator",
        "canonical_name": "Narrator",
        "character_id": "character_0123456789abcdef0123",
        "status": "approved",
        "language": "English",
        "identity_binding": {
            "status": "approved",
            "source": "user_review",
            "approved_at_utc": "2026-08-03T16:00:00Z",
            "notes": "Identity was explicitly approved.",
        },
        "samples": samples,
        "default_sample_id": samples[0]["sample_id"],
        "speaker_evidence_review": review
        or {
            "status": "not_required",
            "decision": "none",
            "reviewed_at_utc": None,
            "notes": "",
        },
        "evidence_set_fingerprint": "0" * 64,
    }
    value["evidence_set_fingerprint"] = compute_evidence_set_fingerprint(value)
    return value


class ProductionVoiceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.evidence_dir = self.root / "production_voice_evidence"
        self.neutral_audio = self.evidence_dir / "neutral.wav"
        self.fear_audio = self.evidence_dir / "fear.wav"
        write_wav(self.neutral_audio)
        write_wav(self.fear_audio, frames=3600)
        self.neutral = sample(
            sample_id="sample_0000000000000001",
            order=0,
            audio_path="neutral.wav",
            audio_sha256=sha256_file(self.neutral_audio),
            transcript="The room was quiet.",
            labels=["neutral"],
            instruction="Natural neutral delivery.",
        )
        self.fear = sample(
            sample_id="sample_0000000000000002",
            order=1,
            audio_path="fear.wav",
            audio_sha256=sha256_file(self.fear_audio),
            transcript="Something was moving behind us.",
            labels=["fear", "dread"],
            instruction="Audible restrained fear.",
        )
        self.path = self.evidence_dir / "evidence.json"
        self.write_evidence(evidence([self.neutral, self.fear]))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_evidence(self, value: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def resolve(self, instruction: str = "Neutral narration.") -> dict:
        return resolve_production_voice_prompt(
            evidence_set_path="production_voice_evidence/evidence.json",
            project_root=self.root,
            instruction=instruction,
            backend="qwen3_instruction_controlled",
            language="English",
            persistent_style="Stable narrator identity.",
        )

    def test_delivery_match_and_explicit_sample_selection_are_deterministic(self) -> None:
        fear = self.resolve("With fear and dread.")
        self.assertEqual(fear["sample_id"], self.fear["sample_id"])
        self.assertEqual(fear["selection_reason"], "delivery_match")
        self.assertIn("Audible restrained fear.", fear["instruction"])
        explicit = self.resolve(
            f"[sample:{self.neutral['sample_id']}] With fear and dread."
        )
        self.assertEqual(explicit["sample_id"], self.neutral["sample_id"])
        self.assertEqual(explicit["selection_reason"], "explicit_sample")
        self.assertFalse(explicit["advisory_evidence"]["authoritative_identity"])
        self.assertFalse(explicit["advisory_evidence"]["approval_source"])

    def test_every_prompt_dependency_changes_the_dependency_fingerprint(self) -> None:
        baseline = self.resolve("Neutral narration.")
        mutations = []

        added = evidence(
            [
                self.neutral,
                self.fear,
                sample(
                    sample_id="sample_0000000000000003",
                    order=2,
                    audio_path="fear.wav",
                    audio_sha256=sha256_file(self.fear_audio),
                    transcript="A third exact sample.",
                    labels=["urgent"],
                    instruction="Urgent but clear.",
                ),
            ]
        )
        mutations.append(added)

        removed = evidence([self.neutral])
        mutations.append(removed)

        reordered_neutral = copy.deepcopy(self.neutral)
        reordered_fear = copy.deepcopy(self.fear)
        reordered_neutral["order"] = 1
        reordered_fear["order"] = 0
        reordered = evidence([reordered_fear, reordered_neutral])
        reordered["default_sample_id"] = reordered_neutral["sample_id"]
        reordered["evidence_set_fingerprint"] = compute_evidence_set_fingerprint(
            reordered
        )
        mutations.append(reordered)

        transcript_changed = copy.deepcopy(self.neutral)
        transcript_changed["transcript"] = "The room remained quiet."
        transcript_changed["transcript_sha256"] = hashlib.sha256(
            transcript_changed["transcript"].encode("utf-8")
        ).hexdigest()
        mutations.append(evidence([transcript_changed, self.fear]))

        preprocessing_changed = copy.deepcopy(self.neutral)
        preprocessing_changed["preprocessing"] = preprocessing(
            "trim_and_normalize_v2",
            [
                {"operation": "trim", "leading_ms": 20},
                {"operation": "normalize", "peak_dbfs": -2.0},
            ],
        )
        mutations.append(evidence([preprocessing_changed, self.fear]))

        pronunciation_changed = copy.deepcopy(self.neutral)
        pronunciation_changed["pronunciation"] = {
            "registry_fingerprint": "a" * 64,
            "entry_ids": ["pronunciation_doctor"],
        }
        mutations.append(evidence([pronunciation_changed, self.fear]))

        fingerprints = set()
        for value in mutations:
            self.write_evidence(value)
            resolved = self.resolve("Neutral narration.")
            fingerprints.add(resolved["dependency_fingerprint"])
            self.assertNotEqual(
                resolved["dependency_fingerprint"],
                baseline["dependency_fingerprint"],
            )
        self.assertEqual(len(fingerprints), len(mutations))

    def test_conflicting_speaker_evidence_requires_explicit_review(self) -> None:
        conflicting_fear = copy.deepcopy(self.fear)
        conflicting_fear["advisory"]["speaker_label"] = "other-speaker"
        value = evidence([self.neutral, conflicting_fear])
        with self.assertRaises(ProductionVoiceEvidenceValidationError):
            validate_production_voice_evidence_set(value)
        reviewed = evidence(
            [self.neutral, conflicting_fear],
            review={
                "status": "reviewed",
                "decision": "accept",
                "reviewed_at_utc": "2026-08-03T16:15:00Z",
                "notes": "The advisory disagreement was manually reviewed.",
            },
        )
        normalized = validate_production_voice_evidence_set(reviewed)
        self.assertEqual(
            normalized["speaker_evidence_review"]["decision"],
            "accept",
        )

    def test_advisory_metadata_cannot_approve_cast_identity(self) -> None:
        value = evidence([self.neutral, self.fear])
        value["identity_binding"] = {
            "status": "unbound",
            "source": "none",
            "approved_at_utc": None,
            "notes": "Only embeddings and diarization were available.",
        }
        value["evidence_set_fingerprint"] = compute_evidence_set_fingerprint(value)
        with self.assertRaises(ProductionVoiceEvidenceValidationError):
            validate_production_voice_evidence_set(value)

    def test_audio_hash_and_backend_compatibility_fail_closed(self) -> None:
        self.neutral_audio.write_bytes(b"changed")
        with self.assertRaises(ProductionVoiceEvidenceValidationError):
            self.resolve()
        self.neutral_audio.unlink()
        write_wav(self.neutral_audio)
        incompatible = copy.deepcopy(self.neutral)
        incompatible["audio_sha256"] = sha256_file(self.neutral_audio)
        incompatible["compatibility"]["backends"] = ["other_backend"]
        self.write_evidence(evidence([incompatible]))
        with self.assertRaises(ProductionVoiceEvidenceConflictError):
            self.resolve()

    def test_unselected_sample_asset_is_verified_during_approval(self) -> None:
        self.fear_audio.write_bytes(b"tampered-unselected-sample")
        with self.assertRaisesRegex(
            ProductionVoiceEvidenceValidationError,
            self.fear["sample_id"],
        ):
            self.resolve("Neutral narration.")

    def test_absolute_evidence_path_outside_project_is_rejected(self) -> None:
        outside = self.root.parent / "outside-production-voice.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            with self.assertRaises(ProductionVoiceEvidenceValidationError):
                resolve_evidence_set_path(
                    outside,
                    project_root=self.root,
                )
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
