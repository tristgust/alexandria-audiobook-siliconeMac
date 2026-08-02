from __future__ import annotations

import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import audio_crash_reconciliation as crash_reconciliation
import audio_takes as audio_takes_module
import project as project_module

from audio_artifacts import sha256_file
from audio_crash_reconciliation import InjectedAudioCrash, reconcile_audio_transitions
from project import ProjectManager


def write_wav(path: Path, *, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * frames)


class FakeEngine:
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
        write_wav(Path(output_path))
        return True


class AudioTakeProjectManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {"NARRATOR": {"type": "custom", "voice": "Ryan"}}
            ),
            encoding="utf-8",
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "NARRATOR",
                        "text": "Hello.",
                        "instruct": "Calm.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.manager.engine = FakeEngine()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def chunks(self) -> list[dict]:
        return json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )

    def generate(self) -> dict:
        success, _path = self.manager.generate_chunk_audio(0)
        self.assertTrue(success)
        return self.manager.audio_take_status(0)

    def _assert_rendition_transaction_boundary(self, member: str, point: str) -> None:
        source_status = self.generate()
        source = source_status["takes"][0]
        before_registry = (self.root / "audio_takes.json").read_bytes()
        before_chunks = (self.root / "chunks.json").read_bytes()
        processed = self.root / "processed.wav"
        write_wav(processed, frames=23000)
        processed_bytes = processed.read_bytes()
        crashed = False

        def boundary(call, *args, **kwargs):
            nonlocal crashed
            if not crashed and point == "before":
                crashed = True
                raise InjectedAudioCrash(f"before-rendition-{member}")
            result = call(*args, **kwargs)
            if not crashed and point == "after":
                crashed = True
                raise InjectedAudioCrash(f"after-rendition-{member}")
            return result

        original_restore = crash_reconciliation._restore_snapshot
        original_take_json = audio_takes_module.atomic_json_write
        original_transition_json = crash_reconciliation.atomic_json_write

        def restore_boundary(path, snapshot):
            if member == "artifact" and Path(path).name.startswith("rendition_"):
                return boundary(original_restore, path, snapshot)
            return original_restore(path, snapshot)

        def json_boundary(original, value, path):
            name = Path(path).name
            selected_registry = (
                name == "audio_takes.json"
                and isinstance(value, dict)
                and len(value.get("takes", {})) == 2
            )
            matches = (
                (member == "registry" and selected_registry)
                or (member == "chunks" and name == "chunks.json")
                or (member == "receipt" and name == "receipt.json")
            )
            if matches:
                return boundary(original, value, path)
            return original(value, path)

        with patch.object(
            crash_reconciliation,
            "_restore_snapshot",
            side_effect=restore_boundary,
        ), patch.object(
            audio_takes_module,
            "atomic_json_write",
            side_effect=lambda value, path: json_boundary(original_take_json, value, path),
        ), patch.object(
            crash_reconciliation,
            "atomic_json_write",
            side_effect=lambda value, path: json_boundary(original_transition_json, value, path),
        ):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.register_audio_rendition(
                    0,
                    source_take_id=source["take_id"],
                    source_audio_path=processed,
                    expected_source_sha256=sha256_file(processed),
                    expected_registry_fingerprint=source_status[
                        "registry_fingerprint"
                    ],
                    expected_source_record_fingerprint=source[
                        "record_fingerprint"
                    ],
                    processing={
                        "operation": "approved_gain_adjustment",
                        "settings": {"gain_db": -1.0},
                    },
                )
        self.assertTrue(crashed)
        first = reconcile_audio_transitions(self.root)
        second = reconcile_audio_transitions(self.root)
        self.assertEqual(second["actions"], [])

        if member == "artifact" and point == "before":
            self.assertGreaterEqual(first["rolled_back_count"], 1)
            self.assertEqual((self.root / "audio_takes.json").read_bytes(), before_registry)
            self.assertEqual((self.root / "chunks.json").read_bytes(), before_chunks)
            self.assertFalse(any((self.root / "voicelines" / "takes").rglob("take_rendition*")))
            self.assertFalse(list((self.root / "audio_take_history").glob("*/receipt.json")))
            return

        self.assertGreaterEqual(first["repaired_count"], 1)
        status = self.manager.audio_take_status(0)
        self.assertEqual(status["take_count"], 2)
        child = next(take for take in status["takes"] if take["kind"] == "rendition")
        self.assertTrue(child["current"])
        self.assertEqual(child["source_take_id"], source["take_id"])
        self.assertEqual(child["root_take_id"], source["root_take_id"])
        child_path = self.root / child["audio"]["relative_path"]
        self.assertEqual(child_path.read_bytes(), processed_bytes)
        receipts = list((self.root / "audio_take_history").glob("*/receipt.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt["status"], "applied")
        undone = self.manager.undo_audio_take_operation(
            operation_id=receipt["operation_id"],
            expected_registry_fingerprint=status["registry_fingerprint"],
        )
        self.assertEqual(undone["status"], "undone")
        restored = self.manager.audio_take_status(0)
        self.assertEqual(restored["take_count"], 1)
        self.assertTrue(restored["takes"][0]["current"])
        self.assertEqual(restored["takes"][0]["take_id"], source["take_id"])
        self.assertFalse(child_path.exists())
        self.assertEqual(reconcile_audio_transitions(self.root)["actions"], [])

    def test_rendition_crash_before_artifact_member(self) -> None:
        self._assert_rendition_transaction_boundary("artifact", "before")

    def test_rendition_crash_after_artifact_member(self) -> None:
        self._assert_rendition_transaction_boundary("artifact", "after")

    def test_rendition_crash_before_registry_member(self) -> None:
        self._assert_rendition_transaction_boundary("registry", "before")

    def test_rendition_crash_after_registry_member(self) -> None:
        self._assert_rendition_transaction_boundary("registry", "after")

    def test_rendition_crash_before_chunks_member(self) -> None:
        self._assert_rendition_transaction_boundary("chunks", "before")

    def test_rendition_crash_after_chunks_member(self) -> None:
        self._assert_rendition_transaction_boundary("chunks", "after")

    def test_rendition_crash_before_receipt_member(self) -> None:
        self._assert_rendition_transaction_boundary("receipt", "before")

    def test_rendition_crash_after_receipt_member(self) -> None:
        self._assert_rendition_transaction_boundary("receipt", "after")

    def test_generation_history_promote_keep_and_child_rendition_round_trip(self) -> None:
        first_status = self.generate()
        first = first_status["takes"][0]
        first_path = self.root / first["audio"]["relative_path"]
        first_bytes = first_path.read_bytes()

        second_status = self.generate()
        self.assertEqual(second_status["take_count"], 2)
        newest = second_status["takes"][0]
        prior = next(
            item
            for item in second_status["takes"]
            if item["take_id"] == first["take_id"]
        )
        self.assertTrue(newest["current"])
        self.assertFalse(prior["current"])
        self.assertEqual(first_path.read_bytes(), first_bytes)

        promoted = self.manager.promote_audio_take(
            0,
            take_id=prior["take_id"],
            expected_registry_fingerprint=second_status[
                "registry_fingerprint"
            ],
            expected_record_fingerprint=prior["record_fingerprint"],
        )
        self.assertEqual(
            promoted["chunk"]["current_take_id"],
            prior["take_id"],
        )
        self.assertEqual(
            promoted["chunk"]["audio_path"],
            prior["audio"]["relative_path"],
        )

        promoted_status = self.manager.audio_take_status(0)
        selected = next(
            item
            for item in promoted_status["takes"]
            if item["take_id"] == prior["take_id"]
        )
        kept = self.manager.set_audio_take_kept(
            0,
            take_id=selected["take_id"],
            kept=True,
            expected_registry_fingerprint=promoted_status[
                "registry_fingerprint"
            ],
            expected_record_fingerprint=selected[
                "record_fingerprint"
            ],
        )
        self.assertTrue(kept["take"]["kept"])

        kept_status = self.manager.audio_take_status(0)
        source = next(
            item
            for item in kept_status["takes"]
            if item["take_id"] == selected["take_id"]
        )
        processed = self.root / "processed.wav"
        write_wav(processed, frames=23000)
        rendition = self.manager.register_audio_rendition(
            0,
            source_take_id=source["take_id"],
            source_audio_path=processed,
            expected_source_sha256=sha256_file(processed),
            expected_registry_fingerprint=kept_status[
                "registry_fingerprint"
            ],
            expected_source_record_fingerprint=source[
                "record_fingerprint"
            ],
            processing={
                "operation": "approved_gain_adjustment",
                "settings": {"gain_db": -1.0},
            },
        )
        child = rendition["take"]
        child_path = self.root / child["audio"]["relative_path"]
        self.assertEqual(child["kind"], "rendition")
        self.assertEqual(child["source_take_id"], source["take_id"])
        self.assertEqual(child["root_take_id"], source["root_take_id"])
        self.assertTrue(child["current"])
        self.assertTrue(child_path.is_file())
        self.assertTrue(first_path.is_file())

        undone = self.manager.undo_audio_take_operation(
            operation_id=rendition["operation_id"],
            expected_registry_fingerprint=rendition[
                "registry_fingerprint"
            ],
        )
        self.assertEqual(undone["status"], "undone")
        self.assertFalse(child_path.exists())
        restored = self.manager.audio_take_status(0)
        restored_source = next(
            item
            for item in restored["takes"]
            if item["take_id"] == source["take_id"]
        )
        self.assertTrue(restored_source["current"])
        self.assertTrue(restored_source["kept"])

    def test_project_manager_promote_rejects_stale_authored_text(self) -> None:
        status = self.generate()
        take = status["takes"][0]
        chunks = self.chunks()
        chunks[0]["text"] = "Changed text."
        chunks[0]["status"] = "pending"
        chunks[0]["audio_path"] = None
        (self.root / "chunks.json").write_text(
            json.dumps(chunks),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Exception, "older text"):
            self.manager.promote_audio_take(
                0,
                take_id=take["take_id"],
                expected_registry_fingerprint=status[
                    "registry_fingerprint"
                ],
                expected_record_fingerprint=take[
                    "record_fingerprint"
                ],
            )

    def test_direct_chunk_edit_deselects_take_without_deleting_audio(self) -> None:
        status = self.generate()
        take = status["takes"][0]
        path = self.root / take["audio"]["relative_path"]
        before = path.read_bytes()

        updated = self.manager.update_chunk(0, {"text": "Changed text."})

        self.assertEqual(updated["audio_state"], "stale")
        self.assertEqual(updated["stale_audio_path"], take["audio"]["relative_path"])
        self.assertIsNone(updated["current_take_id"])
        self.assertEqual(path.read_bytes(), before)
        refreshed = self.manager.audio_take_status(0)
        retained = next(
            item for item in refreshed["takes"] if item["take_id"] == take["take_id"]
        )
        self.assertFalse(retained["current"])
        self.assertFalse(retained["promotable"] if "promotable" in retained else False)

    def test_explicit_invalidation_deselects_take_without_deleting_audio(self) -> None:
        status = self.generate()
        take = status["takes"][0]
        path = self.root / take["audio"]["relative_path"]
        before = path.read_bytes()

        changed = self.manager.invalidate_chunk_audio(
            [0],
            operation_id="take_invalidate_fixture",
            reason="Reviewed regeneration requested.",
        )

        self.assertEqual(changed, [0])
        updated = self.chunks()[0]
        self.assertIsNone(updated["current_take_id"])
        self.assertEqual(updated["stale_audio_path"], take["audio"]["relative_path"])
        self.assertEqual(path.read_bytes(), before)
        retained = self.manager.audio_take_status(0)["takes"][0]
        self.assertFalse(retained["current"])

    def test_invalidation_real_surface_exposes_durable_crash_boundary(self) -> None:
        self.generate()
        with patch.dict(os.environ, {
            "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
            "ALEXANDRIA_AUDIO_CRASH_POINT": "invalidation:after",
        }, clear=False):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.invalidate_chunk_audio(
                    [0], operation_id="crash_invalidate_fixture", reason="test"
                )
        self.assertEqual(reconcile_audio_transitions(self.root)["repaired_count"], 1)
        self.assertIsNone(self.chunks()[0]["current_take_id"])
        self.assertEqual(reconcile_audio_transitions(self.root)["actions"], [])

    def test_authored_edit_journals_registry_and_chunks_after_each_member_write(self) -> None:
        self.generate()
        original_write = crash_reconciliation.atomic_json_write
        for sequence, member in enumerate(("audio_takes.json", "chunks.json"), start=1):
            with self.subTest(member=member):
                if sequence > 1:
                    self.generate()

                def crash_after_member(value, path, *, selected=member):
                    original_write(value, path)
                    if Path(path).name == selected:
                        raise InjectedAudioCrash(f"after:{selected}")

                with patch.object(
                    crash_reconciliation,
                    "atomic_json_write",
                    side_effect=crash_after_member,
                ):
                    with self.assertRaises(InjectedAudioCrash):
                        self.manager.update_chunk(
                            0,
                            {"text": f"Authored replacement {sequence}."},
                        )

                applying = [
                    json.loads(path.read_text(encoding="utf-8"))
                    for path in (self.root / "audio_transition_journal").glob("*/transition.json")
                    if json.loads(path.read_text(encoding="utf-8")).get("status") == "applying"
                ]
                self.assertEqual(len(applying), 1)
                self.assertEqual(applying[0]["transition"], "invalidation")
                self.assertEqual(set(applying[0]["writes"]), {"audio_takes.json", "chunks.json"})
                self.assertEqual(reconcile_audio_transitions(self.root)["repaired_count"], 1)
                self.assertEqual(self.chunks()[0]["text"], f"Authored replacement {sequence}.")
                self.assertIsNone(self.chunks()[0]["current_take_id"])
                self.assertEqual(reconcile_audio_transitions(self.root)["actions"], [])

    def test_incomplete_install_guard_preserves_external_replacement(self) -> None:
        original_install = project_module.install_generated_audio

        def crash_after_install(**kwargs):
            original_install(**kwargs)
            raise InjectedAudioCrash("install-body-exited-before-after-snapshot")

        with patch.object(project_module, "install_generated_audio", side_effect=crash_after_install):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.generate_chunk_audio(0)
        installed = list((self.root / "voicelines" / "takes").rglob("take_*.*"))
        self.assertEqual(len(installed), 1)
        installed[0].write_bytes(b"external-install-replacement")

        report = reconcile_audio_transitions(self.root)

        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(installed[0].read_bytes(), b"external-install-replacement")
        self.assertEqual(reconcile_audio_transitions(self.root)["unresolved_count"], 1)

    def test_incomplete_join_guard_preserves_external_replacement(self) -> None:
        self.generate()
        original_export = project_module.atomic_export_audio_segment

        def crash_after_join(**kwargs):
            original_export(**kwargs)
            raise InjectedAudioCrash("join-body-exited-before-after-snapshot")

        with patch.object(project_module, "atomic_export_audio_segment", side_effect=crash_after_join):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.merge_audio()
        target = self.root / "cloned_audiobook.mp3"
        target.write_bytes(b"external-join-replacement")

        report = reconcile_audio_transitions(self.root)

        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(target.read_bytes(), b"external-join-replacement")
        self.assertEqual(reconcile_audio_transitions(self.root)["unresolved_count"], 1)

    def test_install_and_join_real_surfaces_reconcile_post_write_crashes(self) -> None:
        with patch.dict(os.environ, {
            "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
            "ALEXANDRIA_AUDIO_CRASH_POINT": "immutable_take_installation:after",
        }, clear=False):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.generate_chunk_audio(0)
        install_report = reconcile_audio_transitions(self.root)
        self.assertEqual(install_report["repaired_count"], 1)
        artifact_paths = list((self.root / "voicelines" / "takes").rglob("take_*.*"))
        self.assertEqual(len(artifact_paths), 1)

        self.manager.generate_chunk_audio(0)
        with patch.dict(os.environ, {
            "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
            "ALEXANDRIA_AUDIO_CRASH_POINT": "join:after",
        }, clear=False):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.merge_audio()
        self.assertEqual(reconcile_audio_transitions(self.root)["repaired_count"], 1)
        self.assertTrue((self.root / "cloned_audiobook.mp3").is_file())


if __name__ == "__main__":
    unittest.main()
