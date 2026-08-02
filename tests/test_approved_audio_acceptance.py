from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from approved_audio import (
    approved_audio_binding_fingerprint,
    approved_audio_lock_fields,
    require_regeneration_unlocked,
)
from approved_audio_acceptance import (
    ApprovedAudioAcceptanceError,
    confirm_approved_audio_acceptance,
    preview_approved_audio_acceptance,
)
from audio_artifacts import sha256_file
from audio_crash_reconciliation import (
    InjectedAudioCrash,
    reconcile_audio_transitions,
)
from audio_takes import AudioTakeError, load_registry, normalize_registry
from generation_state import fingerprint_value
from project import ProjectManager
from pronunciation_registry import (
    empty_pronunciation_registry,
    upsert_pronunciation_entry,
)


def write_wav(path: Path, *, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = bytearray()
    for index in range(frames):
        value = 1400 if (index // 120) % 2 == 0 else -1400
        samples.extend(value.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(bytes(samples))


class ApprovedAudioAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunks_lock = threading.RLock()
        self.audio = self.root / "voicelines" / "approved-3431.wav"
        write_wav(self.audio)
        self.audio_bytes = self.audio.read_bytes()
        self.audio_sha = hashlib.sha256(self.audio_bytes).hexdigest()
        chunk = {
            "id": 3431,
            "speaker": "BERNICE",
            "text": "The approved line remains exact.",
            "instruct": "Warm, certain, and restrained.",
            "pause_after": 180,
            "status": "done",
            "audio_state": "current",
            "audio_path": "voicelines/approved-3431.wav",
            "stale_audio_path": None,
            "audio_sha256": self.audio_sha,
            "audio_size_bytes": len(self.audio_bytes),
            "audio_format": "wav",
            "provider": "approved_import",
            "cloud_provider": "fixture-cloud",
            "voice_fingerprint": "v" * 64,
            "generation_provenance": {"source": "reviewed-candidate"},
            "synthesis_seam_receipt": {"receipt_fingerprint": "s" * 64},
        }
        lock_fields = approved_audio_lock_fields(
            chunk=chunk,
            promotion_id="promotion_fixture",
            candidate_id="candidate_fixture",
            source_round_id="round_fixture",
            direct_placement_tier="strict_clean",
            source_audio_path="review/evidence/candidate.wav",
            source_audio_sha256=self.audio_sha,
            manifest_path="approved_audio_promotion_history/fixture/manifest.json",
            installed_at_utc="2026-08-01T12:00:00Z",
            reference_bank_eligible=False,
        )
        chunk.update(lock_fields)
        chunk["audio_fingerprint"] = lock_fields["approved_audio_lock"][
            "binding_fingerprint"
        ]
        self.chunks = [
            chunk,
            {
                "id": 3432,
                "speaker": "DOCTOR",
                "text": "An unrelated authored line.",
                "instruct": "Dry.",
                "status": "pending",
                "provider": "local",
            },
        ]
        self.voice_config = {
            "BERNICE": {"type": "custom", "voice": "Ryan", "provider": "local"},
            "DOCTOR": {"type": "clone", "voice": "Doctor"},
        }
        self._write_chunks(self.chunks)
        (self.root / "voice_config.json").write_text(
            json.dumps(self.voice_config, indent=2), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_chunks(self, chunks: list[dict]) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(chunks, indent=2), encoding="utf-8"
        )

    def _preview(self) -> dict:
        return preview_approved_audio_acceptance(
            project_root=self.root,
            chunks_lock=self.chunks_lock,
            chunk_key_value="chunk:3431",
        )

    def _confirm(self, preview: dict, *, key: str = "accept-3431", crash=None) -> dict:
        return confirm_approved_audio_acceptance(
            project_root=self.root,
            chunks_lock=self.chunks_lock,
            chunk_index_value=0,
            chunk_key_value="chunk:3431",
            action_fingerprint=preview["action_fingerprint"],
            chunks_fingerprint=preview["chunks_fingerprint"],
            registry_fingerprint=preview["registry_fingerprint"],
            voice_configuration_fingerprint=preview[
                "voice_configuration_fingerprint"
            ],
            idempotency_key=key,
            confirm_acceptance=True,
            crash_point=crash,
        )

    def _assert_no_acceptance_write(self) -> None:
        self.assertFalse((self.root / "audio_takes.json").exists())
        self.assertFalse((self.root / "approved_audio_acceptance_history").exists())
        takes = self.root / "voicelines" / "takes"
        self.assertFalse(takes.exists() and any(takes.rglob("*.wav")))

    def _regenerate_after_acceptance(self, accepted_take: dict) -> tuple:
        class RegenerationEngine:
            mode = "local"
            _use_mlx = False

            def generate_voice(
                self,
                _text,
                _instruct,
                _speaker,
                _voice_config,
                output_path,
            ) -> bool:
                write_wav(Path(output_path), frames=48000)
                return True

        manager = ProjectManager(str(self.root))
        manager.engine = RegenerationEngine()
        success, result = manager.generate_chunk_audio(0)
        self.assertTrue(success, result)
        status = manager.audio_take_status(0)
        archived = next(
            take
            for take in status["takes"]
            if take["take_id"] == accepted_take["take_id"]
        )
        stored = load_registry(self.root)["takes"][archived["take_id"]]
        return manager, status, archived, stored

    def _assert_promotion_dependency_mismatch(
        self,
        manager: ProjectManager,
        status: dict,
        archived: dict,
    ) -> None:
        with self.assertRaises(AudioTakeError) as caught:
            manager.promote_audio_take(
                0,
                take_id=archived["take_id"],
                expected_registry_fingerprint=status["registry_fingerprint"],
                expected_record_fingerprint=archived["record_fingerprint"],
            )
        self.assertEqual(caught.exception.code, "audio_take_dependency_mismatch")

    def test_acceptance_copies_exact_bytes_and_preserves_lineage_while_detaching(self) -> None:
        before_chunks = copy.deepcopy(self.chunks)
        before_voice = (self.root / "voice_config.json").read_bytes()
        preview = self._preview()

        receipt = self._confirm(preview)

        artifact = self.root / receipt["take"]["artifact"]["relative_path"]
        self.assertEqual(artifact.read_bytes(), self.audio_bytes)
        self.assertEqual(receipt["take"]["artifact"]["sha256"], self.audio_sha)
        registry = load_registry(self.root)
        take = registry["takes"][receipt["take"]["take_id"]]
        self.assertFalse(take["legacy"])
        self.assertFalse(take["current"])
        self.assertIsNone(registry["chunks"]["chunk:3431"]["current_take_id"])
        self.assertEqual(
            take["voice"]["approved_audio_lock"],
            before_chunks[0]["approved_audio_lock"],
        )
        self.assertEqual(
            take["voice"]["approved_audio_origin"],
            before_chunks[0]["approved_audio_origin"],
        )
        self.assertEqual(take["voice"]["resolved_speaker"], "BERNICE")
        self.assertEqual(
            take["voice"]["configuration"],
            self.voice_config["BERNICE"],
        )
        self.assertEqual(
            take["voice"]["configuration_fingerprint"],
            fingerprint_value(self.voice_config["BERNICE"]),
        )
        self.assertEqual(
            take["voice"]["binding_fingerprint"],
            before_chunks[0]["audio_fingerprint"],
        )
        self.assertEqual(
            receipt["voice_configuration_fingerprint"],
            preview["voice_configuration_fingerprint"],
        )
        after = json.loads((self.root / "chunks.json").read_text())[0]
        self.assertEqual(after["status"], "pending")
        self.assertEqual(after["audio_state"], "stale")
        self.assertIsNone(after["audio_path"])
        self.assertEqual(after["stale_audio_path"], artifact.relative_to(self.root).as_posix())
        self.assertIsNone(after["current_take_id"])
        self.assertNotIn("approved_audio_lock", after)
        self.assertNotIn("approved_audio_origin", after)
        for field in ("text", "speaker", "instruct", "pause_after", "provider", "cloud_provider", "voice_fingerprint"):
            self.assertEqual(after[field], before_chunks[0][field])
        self.assertEqual(
            json.loads((self.root / "chunks.json").read_text())[1], before_chunks[1]
        )
        self.assertEqual((self.root / "voice_config.json").read_bytes(), before_voice)
        self.assertEqual(receipt["before"]["chunks_fingerprint"], preview["chunks_fingerprint"])
        self.assertEqual(receipt["after"]["chunks_fingerprint"], fingerprint_value(json.loads((self.root / "chunks.json").read_text())))
        self.assertEqual(receipt["required_artifacts"], [{"relative_path": artifact.relative_to(self.root).as_posix(), "sha256": self.audio_sha}])
        require_regeneration_unlocked(after)

    def test_receipt_backed_accepted_take_promotes_after_unchanged_regeneration(self) -> None:
        accepted = self._confirm(self._preview())
        accepted_take = accepted["take"]
        self.assertNotIn("chunk_audio_fields", accepted_take["generation"])
        accepted_path = self.root / accepted_take["artifact"]["relative_path"]
        accepted_bytes = accepted_path.read_bytes()
        manager, status, archived, stored = self._regenerate_after_acceptance(
            accepted_take
        )
        compatible_fingerprint = manager._take_compatible_audio_fingerprint(
            chunks=manager.load_chunks(),
            index=0,
            take=stored,
        )
        self.assertEqual(
            stored["generation"]["audio_fingerprint"],
            compatible_fingerprint,
            "authentic accepted Take must retain its approved binding after regeneration",
        )

        promoted = manager.promote_audio_take(
            0,
            take_id=archived["take_id"],
            expected_registry_fingerprint=status["registry_fingerprint"],
            expected_record_fingerprint=archived["record_fingerprint"],
        )

        self.assertEqual(promoted["chunk"]["current_take_id"], archived["take_id"])
        self.assertEqual(
            promoted["chunk"]["approved_audio_lock"],
            stored["voice"]["approved_audio_lock"],
        )
        self.assertEqual(
            promoted["chunk"]["approved_audio_origin"],
            stored["voice"]["approved_audio_origin"],
        )
        self.assertEqual(
            promoted["chunk"]["audio_fingerprint"],
            manager._audio_binding(
                promoted["chunk"],
                self.voice_config,
                resolved_speaker="BERNICE",
            ),
        )
        promoted_status = manager.audio_take_status(0)
        self.assertEqual(
            sum(1 for take in promoted_status["takes"] if take["current"]),
            1,
        )
        self.assertEqual(accepted_path.read_bytes(), accepted_bytes)

        current = next(
            take for take in promoted_status["takes"] if take["current"]
        )
        before_retry = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        retry = manager.promote_audio_take(
            0,
            take_id=current["take_id"],
            expected_registry_fingerprint=promoted_status[
                "registry_fingerprint"
            ],
            expected_record_fingerprint=current["record_fingerprint"],
        )
        self.assertEqual(retry["status"], "current")
        self.assertIsNone(retry["operation_id"])
        self.assertEqual(
            {
                path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*")
                if path.is_file()
            },
            before_retry,
        )

        export_chunks = manager.load_chunks()
        export_chunks[1]["text"] = ""
        self._write_chunks(export_chunks)
        loaded = manager._load_chunks_with_audio()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0][0]["current_take_id"], archived["take_id"])
        self.assertEqual(loaded[0][0]["audio_sha256"], self.audio_sha)

    def test_receipt_backed_accepted_take_rejects_pronunciation_drift(self) -> None:
        accepted_take = self._confirm(self._preview())["take"]
        manager, status, archived, _stored = self._regenerate_after_acceptance(
            accepted_take
        )
        chunks = manager.load_chunks()
        registry = upsert_pronunciation_entry(
            empty_pronunciation_registry(),
            {
                "pronunciation_id": "approved-word",
                "chunk_index": 0,
                "start_char": 4,
                "end_char": 12,
                "original": "approved",
                "spoken_form": "uh-PROOVD",
                "engine_source": {
                    "kind": "manual",
                    "engine": "reviewed-listening",
                },
                "fallback": {"strategy": "bypass"},
                "review": {"state": "approved"},
                "provenance": {"source": "operator_review"},
            },
            chunks=chunks,
        )
        (self.root / "pronunciation_registry.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )

        self._assert_promotion_dependency_mismatch(manager, status, archived)

    def test_receipt_backed_accepted_take_rejects_effective_direction_drift(self) -> None:
        accepted_take = self._confirm(self._preview())["take"]
        manager, status, archived, _stored = self._regenerate_after_acceptance(
            accepted_take
        )
        chunks = manager.load_chunks()
        chunks[0]["qwen_render_instruction"] = "Urgent and sharply detached."
        self._write_chunks(chunks)

        self._assert_promotion_dependency_mismatch(manager, status, archived)

    def test_receipt_replay_fails_closed_when_acceptance_had_unrecorded_direction_dependencies(
        self,
    ) -> None:
        chunks = copy.deepcopy(self.chunks)
        chunks[0].update(
            {
                "text": "The approved line remains exact?",
                "qwen_render_instruction": "Warm and certain, with a questioning close.",
                "fish_render_instruction": "Warm, restrained, and gently questioning.",
            }
        )
        chunks[1].update(
            {
                "speaker": "NARRATOR",
                "text": "Bernice asked.",
            }
        )
        lock_fields = approved_audio_lock_fields(
            chunk=chunks[0],
            promotion_id="promotion_fixture",
            candidate_id="candidate_fixture",
            source_round_id="round_fixture",
            direct_placement_tier="strict_clean",
            source_audio_path="review/evidence/candidate.wav",
            source_audio_sha256=self.audio_sha,
            manifest_path="approved_audio_promotion_history/fixture/manifest.json",
            installed_at_utc="2026-08-01T12:00:00Z",
            reference_bank_eligible=False,
        )
        chunks[0].update(lock_fields)
        chunks[0]["audio_fingerprint"] = lock_fields["approved_audio_lock"][
            "binding_fingerprint"
        ]
        self._write_chunks(chunks)

        accepted_take = self._confirm(self._preview())["take"]
        manager, status, archived, _stored = self._regenerate_after_acceptance(
            accepted_take
        )
        current, continuity = manager._chunk_with_spoken_continuity(
            manager.load_chunks(),
            0,
            bind=True,
        )
        self.assertIsNotNone(continuity)
        self.assertNotEqual(current["effective_instruct"], current["instruct"])
        self.assertNotEqual(
            current["effective_fish_instruct"],
            current["effective_instruct"],
        )

        self._assert_promotion_dependency_mismatch(manager, status, archived)

    def test_receipt_replay_requires_chunk_audio_fields_key_to_be_absent(self) -> None:
        accepted_take = self._confirm(self._preview())["take"]
        manager, _status, _archived, stored = self._regenerate_after_acceptance(
            accepted_take
        )
        recorded = stored["generation"]["audio_fingerprint"]

        for malformed in (None, {}, [], "", "invalid", 0, False):
            with self.subTest(chunk_audio_fields=malformed):
                candidate = copy.deepcopy(stored)
                candidate["generation"]["chunk_audio_fields"] = malformed
                compatible = manager._take_compatible_audio_fingerprint(
                    chunks=manager.load_chunks(),
                    index=0,
                    take=candidate,
                )
                self.assertNotEqual(recorded, compatible)

    def test_receipt_replay_rejects_nonaccepted_and_malformed_records(self) -> None:
        accepted_take = self._confirm(self._preview())["take"]
        manager, _status, _archived, stored = self._regenerate_after_acceptance(
            accepted_take
        )
        recorded = stored["generation"]["audio_fingerprint"]
        mutations = {
            "generic historical provenance": lambda take: take["generation"][
                "provenance"
            ].update(operation="generate_chunk_audio"),
            "malformed provenance": lambda take: take["generation"].update(
                provenance=[]
            ),
            "malformed approved lock": lambda take: take["generation"][
                "provenance"
            ].update(approved_audio_lock="invalid"),
            "malformed approved origin": lambda take: take["generation"][
                "provenance"
            ].update(approved_audio_origin=[]),
            "malformed voice": lambda take: take.update(voice=[]),
            "malformed artifact": lambda take: take.update(artifact="invalid"),
        }

        for label, mutate in mutations.items():
            with self.subTest(record_shape=label):
                candidate = copy.deepcopy(stored)
                mutate(candidate)
                compatible = manager._take_compatible_audio_fingerprint(
                    chunks=manager.load_chunks(),
                    index=0,
                    take=candidate,
                )
                self.assertNotEqual(recorded, compatible)

    def test_receipt_replay_rejects_wrong_type_origin_fields(self) -> None:
        accepted_take = self._confirm(self._preview())["take"]
        manager, _status, _archived, stored = self._regenerate_after_acceptance(
            accepted_take
        )
        wrong_type_mutations = (
            ("promotion_id", 2),
            ("manifest_path", 2),
            ("candidate_id", 2),
            ("direct_placement_tier", 2),
            ("source_audio_path", 2),
            ("source_audio_sha256", 2),
            ("installed_at_utc", 2),
            ("manifest_path", True),
            ("source_audio_path", ["wrong-type"]),
            ("installed_at_utc", {"wrong": "type"}),
            ("source_round_id", 2),
            ("source_round_id", True),
            ("source_round_id", ["wrong-type"]),
        )
        lock_identity_fields = {
            "promotion_id",
            "candidate_id",
            "source_round_id",
            "direct_placement_tier",
            "source_audio_sha256",
            "installed_at_utc",
        }
        binding_fields = {
            "promotion_id",
            "candidate_id",
            "direct_placement_tier",
            "source_audio_sha256",
        }

        for field, value in wrong_type_mutations:
            with self.subTest(origin_wrong_type=field, value_type=type(value).__name__):
                candidate = copy.deepcopy(stored)
                provenance = candidate["generation"]["provenance"]
                origin_mirrors = (
                    provenance["approved_audio_origin"],
                    candidate["voice"]["approved_audio_origin"],
                )
                for origin in origin_mirrors:
                    origin[field] = value
                lock_mirrors = (
                    provenance["approved_audio_lock"],
                    candidate["voice"]["approved_audio_lock"],
                )
                if field in lock_identity_fields:
                    for lock in lock_mirrors:
                        lock[field] = value
                if field in binding_fields:
                    binding = approved_audio_binding_fingerprint(
                        manager.load_chunks()[0], lock_mirrors[0]
                    )
                    self.assertIsNotNone(binding)
                    for lock in lock_mirrors:
                        lock["binding_fingerprint"] = binding
                    candidate["generation"]["audio_fingerprint"] = binding
                    candidate["voice"]["binding_fingerprint"] = binding
                if field == "source_audio_sha256":
                    candidate["generation"]["source_audio_sha256"] = str(value)
                    candidate["artifact"]["sha256"] = str(value)
                expected = candidate["generation"]["audio_fingerprint"]
                compatible = manager._take_compatible_audio_fingerprint(
                    chunks=manager.load_chunks(),
                    index=0,
                    take=candidate,
                )
                self.assertNotEqual(expected, compatible)

    def test_receipt_replay_rejects_semantically_invalid_lock_and_origin(self) -> None:
        accepted_take = self._confirm(self._preview())["take"]
        manager, _status, _archived, stored = self._regenerate_after_acceptance(
            accepted_take
        )
        recorded = stored["generation"]["audio_fingerprint"]
        mutations = (
            ("lock schema missing", "approved_audio_lock", "schema_version", None),
            ("lock schema changed", "approved_audio_lock", "schema_version", 2),
            ("lock schema boolean", "approved_audio_lock", "schema_version", True),
            ("lock status changed", "approved_audio_lock", "status", "unlocked"),
            (
                "lock content fingerprint changed",
                "approved_audio_lock",
                "content_fingerprint",
                "wrong-content",
            ),
            ("origin schema missing", "approved_audio_origin", "schema_version", None),
            ("origin schema changed", "approved_audio_origin", "schema_version", 2),
            (
                "origin schema boolean",
                "approved_audio_origin",
                "schema_version",
                True,
            ),
            ("origin promotion missing", "approved_audio_origin", "promotion_id", None),
            ("origin manifest missing", "approved_audio_origin", "manifest_path", None),
            ("origin candidate missing", "approved_audio_origin", "candidate_id", None),
            ("origin round missing", "approved_audio_origin", "source_round_id", None),
            (
                "origin tier missing",
                "approved_audio_origin",
                "direct_placement_tier",
                None,
            ),
            (
                "origin source path missing",
                "approved_audio_origin",
                "source_audio_path",
                None,
            ),
            (
                "origin reference eligibility missing",
                "approved_audio_origin",
                "reference_bank_eligible",
                None,
            ),
            (
                "origin installation time missing",
                "approved_audio_origin",
                "installed_at_utc",
                None,
            ),
            (
                "origin identity changed",
                "approved_audio_origin",
                "promotion_id",
                "different-promotion",
            ),
        )

        for label, receipt_field, field, value in mutations:
            with self.subTest(receipt_mutation=label):
                candidate = copy.deepcopy(stored)
                mirrors = (
                    candidate["generation"]["provenance"][receipt_field],
                    candidate["voice"][receipt_field],
                )
                for mirror in mirrors:
                    if value is None:
                        mirror.pop(field, None)
                    else:
                        mirror[field] = value
                compatible = manager._take_compatible_audio_fingerprint(
                    chunks=manager.load_chunks(),
                    index=0,
                    take=candidate,
                )
                self.assertNotEqual(recorded, compatible)

    def test_same_idempotency_and_action_returns_exact_receipt_without_second_take(self) -> None:
        preview = self._preview()
        first = self._confirm(preview)
        first_bytes = (self.root / "audio_takes.json").read_bytes()
        second = self._confirm(preview)
        self.assertEqual(second, first)
        self.assertEqual((self.root / "audio_takes.json").read_bytes(), first_bytes)
        self.assertEqual(len(load_registry(self.root)["takes"]), 1)

    def test_receipt_replay_rejects_changed_after_state(self) -> None:
        preview = self._preview()
        self._confirm(preview)
        chunks = json.loads((self.root / "chunks.json").read_text())
        chunks[1]["text"] = "Changed after acceptance."
        self._write_chunks(chunks)

        with self.assertRaises(ApprovedAudioAcceptanceError) as caught:
            self._confirm(preview)

        self.assertEqual(
            caught.exception.code, "approved_audio_acceptance_after_state_changed"
        )

    def test_stale_second_action_conflicts_without_mutation(self) -> None:
        preview = self._preview()
        self._confirm(preview)
        state = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with self.assertRaises(ApprovedAudioAcceptanceError) as caught:
            self._confirm(preview, key="another-acceptance")
        self.assertEqual(caught.exception.code, "approved_audio_acceptance_chunks_changed")
        self.assertEqual(state, {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_later_distinct_approved_audio_can_be_accepted_for_same_chunk(self) -> None:
        first = self._confirm(self._preview())
        chunks = json.loads((self.root / "chunks.json").read_text())
        replacement = self.root / "voicelines" / "approved-3431-replacement.wav"
        write_wav(replacement)
        replacement_sha = sha256_file(replacement)
        chunks[0].update(
            {
                "status": "done",
                "audio_state": "current",
                "audio_path": replacement.relative_to(self.root).as_posix(),
                "stale_audio_path": first["take"]["artifact"]["relative_path"],
                "audio_sha256": replacement_sha,
                "audio_size_bytes": replacement.stat().st_size,
                "audio_format": "wav",
            }
        )
        lock_fields = approved_audio_lock_fields(
            chunk=chunks[0],
            promotion_id="promotion_replacement",
            candidate_id="candidate_replacement",
            source_round_id="round_replacement",
            direct_placement_tier="strict_clean",
            source_audio_path="review/evidence/replacement.wav",
            source_audio_sha256=replacement_sha,
            manifest_path="approved_audio_promotion_history/replacement/manifest.json",
            installed_at_utc="2026-08-01T13:00:00Z",
            reference_bank_eligible=False,
        )
        chunks[0].update(lock_fields)
        chunks[0]["audio_fingerprint"] = lock_fields["approved_audio_lock"][
            "binding_fingerprint"
        ]
        self._write_chunks(chunks)

        second = self._confirm(
            self._preview(), key="accept-3431-replacement"
        )

        registry = load_registry(self.root)
        self.assertEqual(len(registry["takes"]), 2)
        self.assertNotEqual(first["take"]["take_id"], second["take"]["take_id"])
        self.assertEqual(second["take"]["artifact"]["sha256"], replacement_sha)

    def test_voice_configuration_drift_after_preview_conflicts_without_writes(self) -> None:
        preview = self._preview()
        changed = copy.deepcopy(self.voice_config)
        changed["BERNICE"]["provider"] = "cloud"
        (self.root / "voice_config.json").write_text(
            json.dumps(changed, indent=2), encoding="utf-8"
        )

        with self.assertRaises(ApprovedAudioAcceptanceError) as caught:
            self._confirm(preview)

        self.assertEqual(
            caught.exception.code,
            "approved_audio_acceptance_voice_configuration_changed",
        )
        self._assert_no_acceptance_write()

    def test_preview_then_authored_or_other_chunk_drift_conflicts_without_writes(self) -> None:
        for mutation in ("text", "instruct", "speaker", "other_chunk"):
            with self.subTest(mutation=mutation):
                preview = self._preview()
                changed = copy.deepcopy(self.chunks)
                if mutation == "other_chunk":
                    changed[1]["text"] += " changed"
                else:
                    changed[0][mutation] += " changed"
                self._write_chunks(changed)
                with self.assertRaises(ApprovedAudioAcceptanceError) as caught:
                    self._confirm(preview)
                self.assertEqual(caught.exception.code, "approved_audio_acceptance_chunks_changed")
                self._assert_no_acceptance_write()
                self._write_chunks(self.chunks)

    def test_preview_then_path_hash_or_lock_drift_conflicts_without_writes(self) -> None:
        for mutation in ("path", "hash", "lock"):
            with self.subTest(mutation=mutation):
                preview = self._preview()
                changed = copy.deepcopy(self.chunks)
                if mutation == "path":
                    changed[0]["audio_path"] = "voicelines/other.wav"
                elif mutation == "hash":
                    changed[0]["audio_sha256"] = "0" * 64
                else:
                    changed[0]["approved_audio_lock"]["candidate_id"] = "changed"
                self._write_chunks(changed)
                with self.assertRaises(ApprovedAudioAcceptanceError):
                    self._confirm(preview)
                self._assert_no_acceptance_write()
                self._write_chunks(self.chunks)

    def test_preview_then_registry_drift_conflicts_without_acceptance_write(self) -> None:
        preview = self._preview()
        registry = load_registry(self.root)
        registry["chunks"]["chunk:999"] = {
            "chunk_key": "chunk:999",
            "current_take_id": None,
            "take_ids": [],
        }
        registry["registry_fingerprint"] = None
        registry = normalize_registry(registry)
        (self.root / "audio_takes.json").write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(ApprovedAudioAcceptanceError) as caught:
            self._confirm(preview)
        self.assertEqual(caught.exception.code, "approved_audio_acceptance_registry_changed")
        self.assertFalse((self.root / "approved_audio_acceptance_history").exists())

    def test_invalid_sources_reject_without_registry_chunk_or_receipt_write(self) -> None:
        cases = ("missing", "escaping", "symlink", "unsupported", "hash_mismatch")
        for case in cases:
            with self.subTest(case=case):
                original_chunks = (self.root / "chunks.json").read_bytes()
                changed = copy.deepcopy(self.chunks)
                if case == "missing":
                    changed[0]["audio_path"] = "voicelines/missing.wav"
                elif case == "escaping":
                    changed[0]["audio_path"] = "../outside.wav"
                elif case == "unsupported":
                    unsupported = self.root / "voicelines" / "approved.flac"
                    unsupported.write_bytes(self.audio_bytes)
                    changed[0]["audio_path"] = "voicelines/approved.flac"
                elif case == "hash_mismatch":
                    self.audio.write_bytes(self.audio_bytes + b"changed")
                else:
                    outside = self.root.parent / f"{self.root.name}-outside.wav"
                    outside.write_bytes(self.audio_bytes)
                    self.audio.unlink()
                    self.audio.symlink_to(outside)
                self._write_chunks(changed)
                with self.assertRaises(ApprovedAudioAcceptanceError):
                    self._preview()
                self.assertEqual((self.root / "chunks.json").read_bytes(), self._json_bytes(changed))
                self._assert_no_acceptance_write()
                if self.audio.is_symlink():
                    self.audio.unlink()
                    outside.unlink(missing_ok=True)
                    write_wav(self.audio)
                elif case == "hash_mismatch":
                    self.audio.write_bytes(self.audio_bytes)
                self._write_chunks(self.chunks)
                self.assertNotEqual(original_chunks, b"")

    def _json_bytes(self, value: list[dict]) -> bytes:
        return json.dumps(value, indent=2).encode("utf-8")

    def test_generating_state_and_false_confirmation_are_rejected(self) -> None:
        changed = copy.deepcopy(self.chunks)
        changed[0]["status"] = "generating"
        self._write_chunks(changed)
        with self.assertRaises(ApprovedAudioAcceptanceError) as caught:
            self._preview()
        self.assertEqual(caught.exception.code, "approved_audio_acceptance_generation_active")
        self._write_chunks(self.chunks)
        preview = self._preview()
        with self.assertRaises(ApprovedAudioAcceptanceError) as caught:
            confirm_approved_audio_acceptance(
                project_root=self.root,
                chunks_lock=self.chunks_lock,
                chunk_index_value=0,
                chunk_key_value="chunk:3431",
                action_fingerprint=preview["action_fingerprint"],
                chunks_fingerprint=preview["chunks_fingerprint"],
                registry_fingerprint=preview["registry_fingerprint"],
                voice_configuration_fingerprint=preview["voice_configuration_fingerprint"],
                idempotency_key="accept-3431",
                confirm_acceptance=False,
            )
        self.assertEqual(caught.exception.code, "approved_audio_acceptance_confirmation_required")
        self._assert_no_acceptance_write()

    def test_crash_before_or_after_single_durable_transition_recovers_exact_side(self) -> None:
        for point in ("before", "after"):
            with self.subTest(point=point):
                if point == "after":
                    self.tearDown()
                    self.setUp()
                before_chunks = (self.root / "chunks.json").read_bytes()
                preview = self._preview()
                with self.assertRaises(InjectedAudioCrash):
                    self._confirm(preview, crash=point)
                report = reconcile_audio_transitions(self.root)
                self.assertEqual(report["unresolved_count"], 0)
                if point == "before":
                    self.assertEqual((self.root / "chunks.json").read_bytes(), before_chunks)
                    self.assertFalse((self.root / "audio_takes.json").exists())
                    self.assertEqual(report["rolled_back_count"], 1)
                else:
                    self.assertEqual(report["repaired_count"], 1)
                    chunks = json.loads((self.root / "chunks.json").read_text())
                    self.assertIsNone(chunks[0]["audio_path"])
                    self.assertNotIn("approved_audio_lock", chunks[0])
                    self.assertEqual(len(load_registry(self.root)["takes"]), 1)


if __name__ == "__main__":
    unittest.main()
