from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from approved_audio import approved_audio_lock_fields
from audio_invalidation import undo_project_audio_invalidation
from pronunciation_registry import (
    PronunciationRegistryError,
    apply_pronunciation_registry_change,
    empty_pronunciation_registry,
    pronunciation_chunk_fields,
    resolve_pronunciation_request,
    upsert_pronunciation_entry,
)


def write_wav(path: Path, *, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * frames)


def entry(
    *,
    pronunciation_id: str,
    chunk_index: int,
    start_char: int,
    end_char: int,
    original: str,
    spoken_form: str | None = None,
    phonetic_hint: str | None = None,
    fallback: dict | None = None,
    languages: list[str] | None = None,
    voice_ids: list[str] | None = None,
    character_labels: list[str] | None = None,
    engine_ids: list[str] | None = None,
) -> dict:
    return {
        "pronunciation_id": pronunciation_id,
        "chunk_index": chunk_index,
        "start_char": start_char,
        "end_char": end_char,
        "original": original,
        "spoken_form": spoken_form,
        "phonetic_hint": phonetic_hint,
        "languages": languages or [],
        "voice_ids": voice_ids or [],
        "character_labels": character_labels or [],
        "engine_ids": engine_ids or [],
        "engine_source": {
            "kind": "manual",
            "engine": "reviewed-listening",
        },
        "fallback": fallback or {"strategy": "bypass"},
        "review": {
            "state": "approved",
            "reviewer": "fixture-reviewer",
            "reviewed_at_utc": "2026-08-01T10:00:00Z",
        },
        "provenance": {
            "source": "operator_review",
            "created_at_utc": "2026-08-01T09:00:00Z",
            "evidence": {"note": "fixture"},
        },
    }


class PronunciationRegistryTests(unittest.TestCase):
    def test_exact_occurrence_is_synthesis_only_and_unrelated_entries_do_not_change_binding(self) -> None:
        chunks = [
            {"speaker": "NARRATOR", "text": "Dalek and Dalek.", "instruct": "Calm."},
            {"speaker": "NARRATOR", "text": "Skaro fell.", "instruct": "Calm."},
        ]
        registry = upsert_pronunciation_entry(
            empty_pronunciation_registry(),
            entry(
                pronunciation_id="dalek-first",
                chunk_index=0,
                start_char=0,
                end_char=5,
                original="Dalek",
                spoken_form="DAH-lek",
            ),
            chunks=chunks,
        )
        first = resolve_pronunciation_request(
            registry=registry,
            chunk_index=0,
            text=chunks[0]["text"],
            speaker="NARRATOR",
            resolved_speaker="NARRATOR",
            voice_data={"type": "custom", "voice": "Ryan"},
            language="English",
            engine_id="custom",
        )
        self.assertEqual(first["source_text"], "Dalek and Dalek.")
        self.assertEqual(first["synthesis_text"], "DAH-lek and Dalek.")
        self.assertEqual(first["receipt"]["applied_count"], 1)
        self.assertEqual(first["receipt"]["decisions"][0]["reason"], "spoken_form")
        saved_entry = registry["entries"][0]
        self.assertEqual(saved_entry["source"]["kind"], "accepted_script_chunk")
        self.assertEqual(saved_entry["source"]["quote"], "Dalek")

        expanded = upsert_pronunciation_entry(
            registry,
            entry(
                pronunciation_id="skaro",
                chunk_index=1,
                start_char=0,
                end_char=5,
                original="Skaro",
                spoken_form="SKA-roh",
            ),
            chunks=chunks,
        )
        second = resolve_pronunciation_request(
            registry=expanded,
            chunk_index=0,
            text=chunks[0]["text"],
            speaker="NARRATOR",
            resolved_speaker="NARRATOR",
            voice_data={"type": "custom", "voice": "Ryan"},
            language="English",
            engine_id="custom",
        )
        self.assertNotEqual(
            first["receipt"]["registry_fingerprint"],
            second["receipt"]["registry_fingerprint"],
        )
        self.assertEqual(
            first["receipt"]["request_fingerprint"],
            second["receipt"]["request_fingerprint"],
        )

    def test_phonetic_hint_uses_explicit_fallback_and_limits_bypass_visibly(self) -> None:
        chunks = [{"speaker": "ROZ", "text": "Skaro fell.", "instruct": "Calm."}]
        registry = upsert_pronunciation_entry(
            empty_pronunciation_registry(),
            entry(
                pronunciation_id="skaro",
                chunk_index=0,
                start_char=0,
                end_char=5,
                original="Skaro",
                phonetic_hint="ˈskɑːroʊ",
                fallback={
                    "strategy": "spoken_form",
                    "spoken_form": "SKA-roh",
                    "reason": "Backend has no phoneme channel.",
                },
                languages=["English"],
            ),
            chunks=chunks,
        )
        applied = resolve_pronunciation_request(
            registry=registry,
            chunk_index=0,
            text=chunks[0]["text"],
            speaker="ROZ",
            resolved_speaker="ROZ",
            voice_data={"type": "custom", "voice": "Ryan"},
            language="English",
            engine_id="custom",
            supports_phonetic_hint=False,
        )
        self.assertEqual(applied["synthesis_text"], "SKA-roh fell.")
        self.assertEqual(
            applied["receipt"]["decisions"][0]["reason"],
            "phonetic_hint_fallback",
        )

        bypassed = resolve_pronunciation_request(
            registry=registry,
            chunk_index=0,
            text=chunks[0]["text"],
            speaker="ROZ",
            resolved_speaker="ROZ",
            voice_data={"type": "custom", "voice": "Ryan"},
            language="Swedish",
            engine_id="custom",
            supports_phonetic_hint=False,
        )
        self.assertEqual(bypassed["synthesis_text"], chunks[0]["text"])
        self.assertEqual(bypassed["receipt"]["bypassed_count"], 1)
        self.assertEqual(
            bypassed["receipt"]["decisions"][0]["reason"],
            "language_limit",
        )

    def test_approved_overlapping_entries_are_rejected(self) -> None:
        chunks = [{"speaker": "NARRATOR", "text": "Skaro fell.", "instruct": "Calm."}]
        registry = upsert_pronunciation_entry(
            empty_pronunciation_registry(),
            entry(
                pronunciation_id="one",
                chunk_index=0,
                start_char=0,
                end_char=5,
                original="Skaro",
                spoken_form="SKA-roh",
            ),
            chunks=chunks,
        )
        with self.assertRaisesRegex(
            PronunciationRegistryError,
            "cannot overlap",
        ):
            upsert_pronunciation_entry(
                registry,
                entry(
                    pronunciation_id="two",
                    chunk_index=0,
                    start_char=0,
                    end_char=3,
                    original="Ska",
                    spoken_form="SCA",
                ),
                chunks=chunks,
            )

    def test_character_voice_and_engine_limits_each_fail_closed(self) -> None:
        chunks = [{"speaker": "ROZ", "text": "Skaro fell.", "instruct": "Calm."}]
        registry = upsert_pronunciation_entry(
            empty_pronunciation_registry(),
            entry(
                pronunciation_id="limited",
                chunk_index=0,
                start_char=0,
                end_char=5,
                original="Skaro",
                spoken_form="SKA-roh",
                character_labels=["ROZ"],
                voice_ids=["Ryan"],
                engine_ids=["custom"],
            ),
            chunks=chunks,
        )
        cases = (
            ("DOCTOR", "DOCTOR", {"type": "custom", "voice": "Ryan"}, "custom", "character_limit"),
            ("ROZ", "ROZ", {"type": "custom", "voice": "Aiden"}, "custom", "voice_limit"),
            ("ROZ", "ROZ", {"type": "custom", "voice": "Ryan"}, "fish_s21_cloud", "engine_limit"),
        )
        for speaker, resolved, voice_data, engine_id, reason in cases:
            with self.subTest(reason=reason):
                result = resolve_pronunciation_request(
                    registry=registry,
                    chunk_index=0,
                    text=chunks[0]["text"],
                    speaker=speaker,
                    resolved_speaker=resolved,
                    voice_data=voice_data,
                    language="English",
                    engine_id=engine_id,
                )
                self.assertEqual(result["synthesis_text"], chunks[0]["text"])
                self.assertEqual(result["receipt"]["decisions"][0]["reason"], reason)

    def test_registry_change_invalidates_only_anchored_audio_and_undoes_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_audio = root / "voicelines" / "first.wav"
            second_audio = root / "voicelines" / "second.wav"
            write_wav(first_audio)
            write_wav(second_audio)
            chunks = [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "Skaro fell.",
                    "instruct": "Calm.",
                    "status": "done",
                    "audio_state": "current",
                    "audio_path": "voicelines/first.wav",
                    "audio_fingerprint": "a" * 64,
                },
                {
                    "id": 1,
                    "speaker": "NARRATOR",
                    "text": "Dalek advanced.",
                    "instruct": "Calm.",
                    "status": "done",
                    "audio_state": "current",
                    "audio_path": "voicelines/second.wav",
                    "audio_fingerprint": "b" * 64,
                },
            ]
            chunks_path = root / "chunks.json"
            chunks_path.write_text(json.dumps(chunks), encoding="utf-8")
            script_path = root / "annotated_script.json"
            script_bytes = json.dumps(chunks).encode("utf-8")
            script_path.write_bytes(script_bytes)
            before = empty_pronunciation_registry()
            after = upsert_pronunciation_entry(
                before,
                entry(
                    pronunciation_id="skaro",
                    chunk_index=0,
                    start_char=0,
                    end_char=5,
                    original="Skaro",
                    spoken_form="SKA-roh",
                ),
                chunks=chunks,
            )
            record = apply_pronunciation_registry_change(
                project_root=root,
                before=before,
                after=after,
                operation_id="pronunciation_fixture",
                operation="pronunciation_upsert",
                at_utc="2026-08-01T10:00:00Z",
                reason="Reviewed pronunciation changed.",
            )
            updated = json.loads(chunks_path.read_text(encoding="utf-8"))
            self.assertEqual(updated[0]["audio_state"], "stale")
            self.assertIsNone(updated[0]["audio_path"])
            self.assertEqual(updated[1], chunks[1])
            self.assertFalse(first_audio.exists())
            self.assertTrue(second_audio.is_file())
            self.assertEqual(script_path.read_bytes(), script_bytes)
            self.assertEqual(
                record["affected_chunk_indices"],
                [0],
            )

            undone = undo_project_audio_invalidation(
                project_root=root,
                operation_id="pronunciation_fixture",
                undone_at_utc="2026-08-01T11:00:00Z",
            )
            self.assertEqual(undone["status"], "undone")
            self.assertFalse((root / "pronunciation_registry.json").exists())
            self.assertEqual(
                json.loads(chunks_path.read_text(encoding="utf-8")),
                chunks,
            )
            self.assertTrue(first_audio.is_file())
            self.assertTrue(second_audio.is_file())
            self.assertEqual(script_path.read_bytes(), script_bytes)

    def test_approved_performance_is_not_invalidated_by_pronunciation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "voicelines" / "approved.wav"
            write_wav(audio)
            chunk = {
                "id": 0,
                "speaker": "BERNICE",
                "text": "Skaro fell.",
                "instruct": "Calm.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/approved.wav",
                "audio_fingerprint": "a" * 64,
            }
            chunk.update(
                approved_audio_lock_fields(
                    chunk=chunk,
                    promotion_id="promotion_fixture",
                    candidate_id="candidate_fixture",
                    source_round_id="round_fixture",
                    direct_placement_tier="strict_clean",
                    source_audio_path="reviewed.wav",
                    source_audio_sha256="c" * 64,
                    manifest_path="manifest.json",
                    installed_at_utc="2026-08-01T09:00:00Z",
                    reference_bank_eligible=True,
                )
            )
            chunks = [chunk]
            (root / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
            before = empty_pronunciation_registry()
            after = upsert_pronunciation_entry(
                before,
                entry(
                    pronunciation_id="skaro",
                    chunk_index=0,
                    start_char=0,
                    end_char=5,
                    original="Skaro",
                    spoken_form="SKA-roh",
                ),
                chunks=chunks,
            )
            record = apply_pronunciation_registry_change(
                project_root=root,
                before=before,
                after=after,
                operation_id="pronunciation_locked_fixture",
                operation="pronunciation_upsert",
                at_utc="2026-08-01T10:00:00Z",
                reason="Reviewed pronunciation changed.",
            )
            self.assertEqual(record["audio_invalidation"]["invalidated_chunks"], [])
            self.assertEqual(
                json.loads((root / "chunks.json").read_text(encoding="utf-8")),
                chunks,
            )
            self.assertTrue(audio.is_file())


if __name__ == "__main__":
    unittest.main()
