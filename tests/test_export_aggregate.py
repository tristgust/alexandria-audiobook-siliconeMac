from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from export_aggregate import (
    ExportAggregateError,
    build_export_chapters,
    build_export_plan,
    execute_export_build,
    inspect_export_project,
)


class FakeProjectManager:
    def __init__(
        self,
        *,
        fail_format: str | None = None,
        invalid_format: str | None = None,
    ) -> None:
        self.fail_format = fail_format
        self.invalid_format = invalid_format
        self.calls: list[tuple[str, Path]] = []

    def _audio(self, format_name: str, output_path: str | Path):
        target = Path(output_path)
        self.calls.append((format_name, target))
        if self.fail_format == format_name:
            return False, f"synthetic {format_name} build failure"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (
            b""
            if self.invalid_format == format_name
            else f"new-{format_name}-audio".encode("utf-8")
        )
        target.write_bytes(content)
        return True, str(target)

    def merge_audio(self, output_path=None):
        return self._audio("mp3", output_path)

    def merge_m4b(self, per_chunk_chapters=False, metadata=None, output_path=None):
        return self._audio("m4b", output_path)

    def export_audacity(self, output_path=None):
        target = Path(output_path)
        self.calls.append(("audacity", target))
        if self.fail_format == "audacity":
            return False, "synthetic audacity build failure"
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.invalid_format == "audacity":
            target.write_bytes(b"not-a-zip")
        else:
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("project.lof", "file \"NARRATOR.wav\"\n")
                archive.writestr("labels.txt", "0\t1\t[NARRATOR] Line\n")
                archive.writestr("NARRATOR.wav", b"wav-bytes")
        return True, str(target)


class ExportAggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {
            "tts": {
                "pause_between_speakers_ms": 500,
                "pause_same_speaker_ms": 250,
            }
        }
        self.produce = {
            "summary": {
                "required_chunk_count": 3,
                "current_count": 3,
                "complete": True,
            },
            "chunks": [
                self._chunk(0, "NARRATOR", "Prologue", 1000),
                self._chunk(1, "NARRATOR", "Opening line.", 1200),
                self._chunk(2, "DOCTOR", "Chapter One", 900),
            ],
            "fingerprints": {
                "aggregate": "produce-aggregate",
                "chunks": "produce-chunks",
                "voice_config": "voice-config",
                "synthesis": "synthesis",
            },
        }
        self.metadata = {
            "title": "Book",
            "author": "Author",
            "narrator": "Narrator",
            "year": "2026",
            "description": "Description",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _chunk(
        index: int,
        speaker: str,
        text: str,
        duration_ms: int,
    ) -> dict:
        return {
            "chunk_id": f"chunk:{index}",
            "speaker": speaker,
            "text": text,
            "duration_ms": duration_ms,
            "pause_after_ms": None,
            "state": "current",
        }

    @staticmethod
    def _validator(path: str | Path, *, format_hint=None) -> dict:
        target = Path(path)
        content = target.read_bytes()
        if not content:
            raise OSError("empty output")
        return {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "duration_ms": 3100,
        }

    def _plan(
        self,
        *,
        formats=("mp3",),
        chapter_mode="smart",
        produce=None,
        metadata=None,
    ) -> dict:
        return build_export_plan(
            produce=produce or self.produce,
            metadata=metadata or self.metadata,
            formats=formats,
            chapter_mode=chapter_mode,
            config=self.config,
        )

    def test_plan_blocks_incomplete_produce_missing_metadata_and_unavailable_format(self) -> None:
        incomplete = json.loads(json.dumps(self.produce))
        incomplete["summary"]["complete"] = False
        plan = build_export_plan(
            produce=incomplete,
            metadata={"title": "", "author": ""},
            formats=["chapter_separated", "unknown"],
            chapter_mode="none",
            config=self.config,
        )
        codes = {item["code"] for item in plan["blockers"]}
        self.assertEqual(
            codes,
            {
                "export_produce_incomplete",
                "export_metadata_missing",
                "export_format_unknown",
                "export_format_unavailable",
            },
        )
        self.assertFalse(plan["safe_to_execute"])

    def test_smart_and_per_chunk_chapters_use_current_chunk_timing(self) -> None:
        smart = build_export_chapters(
            self.produce["chunks"],
            config=self.config,
            mode="smart",
        )
        self.assertEqual([item["name"] for item in smart], ["Prologue", "Chapter One"])
        self.assertEqual(smart[0]["start_ms"], 0)
        self.assertEqual(smart[0]["end_ms"], 2450)
        self.assertEqual(smart[1]["start_ms"], 2950)
        self.assertEqual(smart[1]["end_ms"], 3850)

        per_chunk = build_export_chapters(
            self.produce["chunks"],
            config=self.config,
            mode="per_chunk",
        )
        self.assertEqual(len(per_chunk), 3)
        self.assertEqual(per_chunk[2]["start_chunk_id"], "chunk:2")

    def test_m4b_requires_chapters_but_mp3_does_not(self) -> None:
        m4b = self._plan(formats=["m4b"], chapter_mode="none")
        self.assertIn(
            "export_chapters_required",
            {item["code"] for item in m4b["blockers"]},
        )
        mp3 = self._plan(formats=["mp3"], chapter_mode="none")
        self.assertTrue(mp3["safe_to_execute"])

    def test_status_is_file_pure_and_marks_current_stale_invalid_and_legacy_outputs(self) -> None:
        plan = self._plan(formats=["mp3", "m4b"], chapter_mode="smart")
        mp3 = self.root / "cloned_audiobook.mp3"
        m4b = self.root / "audiobook.m4b"
        audacity = self.root / "audacity_export.zip"
        mp3.write_bytes(b"mp3-current")
        m4b.write_bytes(b"m4b-stale")
        with zipfile.ZipFile(audacity, "w") as archive:
            archive.writestr("project.lof", "")
            archive.writestr("labels.txt", "")
        receipt = {
            "schema_version": 1,
            "status": "complete",
            "build_id": "export_existing",
            "built_at_utc": "2026-07-20T12:00:00Z",
            "dependency_fingerprint": plan["dependency_fingerprint"],
            "plan_fingerprint": plan["plan_fingerprint"],
            "metadata": plan["metadata"],
            "formats": ["mp3", "m4b"],
            "chapter_mode": "smart",
            "chapters": plan["chapters"],
            "cover_sha256": None,
            "outputs": {
                "mp3": {
                    "sha256": hashlib.sha256(mp3.read_bytes()).hexdigest(),
                    "size_bytes": mp3.stat().st_size,
                    "duration_ms": 3600,
                    "built_at_utc": "2026-07-20T12:00:00Z",
                },
                "m4b": {
                    "sha256": "0" * 64,
                    "size_bytes": m4b.stat().st_size,
                    "duration_ms": 3600,
                    "built_at_utc": "2026-07-20T12:00:00Z",
                },
            },
        }
        (self.root / "export_build.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )
        before = {
            path: path.read_bytes()
            for path in (mp3, m4b, audacity, self.root / "export_build.json")
        }
        status = inspect_export_project(
            root_dir=self.root,
            produce=self.produce,
            config=self.config,
        )
        self.assertEqual(status["outputs"]["mp3"]["state"], "current")
        self.assertEqual(status["outputs"]["m4b"]["state"], "invalid")
        self.assertEqual(status["outputs"]["audacity"]["state"], "legacy_unverified")
        self.assertEqual(
            {path: path.read_bytes() for path in before},
            before,
        )

        changed = json.loads(json.dumps(self.produce))
        changed["fingerprints"]["chunks"] = "changed"
        stale = inspect_export_project(
            root_dir=self.root,
            produce=changed,
            config=self.config,
        )
        self.assertEqual(stale["outputs"]["mp3"]["state"], "stale")

    def test_successful_multi_output_build_commits_receipt_and_history(self) -> None:
        plan = self._plan(formats=["mp3", "m4b", "audacity"])
        manager = FakeProjectManager()
        result = execute_export_build(
            root_dir=self.root,
            project_manager=manager,
            plan=plan,
            audio_validator=self._validator,
            at_utc="2026-07-20T12:00:00Z",
        )
        self.assertTrue(result["committed"])
        self.assertEqual(
            [item[0] for item in manager.calls],
            ["mp3", "m4b", "audacity"],
        )
        receipt = json.loads(
            (self.root / "export_build.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["dependency_fingerprint"], plan["dependency_fingerprint"])
        self.assertEqual(set(receipt["outputs"]), {"mp3", "m4b", "audacity"})
        history = self.root / "export_build_history" / result["build_id"]
        self.assertTrue((history / "receipt.json").is_file())
        for format_name, filename in {
            "mp3": "cloned_audiobook.mp3",
            "m4b": "audiobook.m4b",
            "audacity": "audacity_export.zip",
        }.items():
            output = self.root / filename
            self.assertTrue(output.is_file())
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                receipt["outputs"][format_name]["sha256"],
            )

    def test_builder_or_validation_failure_preserves_previous_outputs_and_receipt(self) -> None:
        old_mp3 = self.root / "cloned_audiobook.mp3"
        old_m4b = self.root / "audiobook.m4b"
        old_mp3.write_bytes(b"old-mp3")
        old_m4b.write_bytes(b"old-m4b")
        old_receipt = b'{"status":"old"}'
        (self.root / "export_build.json").write_bytes(old_receipt)
        plan = self._plan(formats=["mp3", "m4b"])

        with self.assertRaises(ExportAggregateError):
            execute_export_build(
                root_dir=self.root,
                project_manager=FakeProjectManager(fail_format="m4b"),
                plan=plan,
                audio_validator=self._validator,
            )
        self.assertEqual(old_mp3.read_bytes(), b"old-mp3")
        self.assertEqual(old_m4b.read_bytes(), b"old-m4b")
        self.assertEqual((self.root / "export_build.json").read_bytes(), old_receipt)

        with self.assertRaises(ExportAggregateError):
            execute_export_build(
                root_dir=self.root,
                project_manager=FakeProjectManager(invalid_format="mp3"),
                plan=plan,
                audio_validator=self._validator,
            )
        self.assertEqual(old_mp3.read_bytes(), b"old-mp3")
        self.assertEqual(old_m4b.read_bytes(), b"old-m4b")
        self.assertEqual((self.root / "export_build.json").read_bytes(), old_receipt)

    def test_m4b_builder_receives_cancel_callback_and_returns_cancelled(self) -> None:
        class CancellableManager(FakeProjectManager):
            def __init__(self) -> None:
                super().__init__()
                self.received_cancel_check = False

            def merge_m4b(
                self,
                per_chunk_chapters=False,
                metadata=None,
                output_path=None,
                cancel_check=None,
            ):
                self.received_cancel_check = callable(cancel_check)
                if cancel_check is not None:
                    cancel_check()
                return False, "M4B export cancelled"

        manager = CancellableManager()
        checks = {"count": 0}

        def cancel_check() -> bool:
            checks["count"] += 1
            return checks["count"] >= 2

        result = execute_export_build(
            root_dir=self.root,
            project_manager=manager,
            plan=self._plan(formats=["m4b"]),
            cancel_check=cancel_check,
            audio_validator=self._validator,
        )

        self.assertTrue(manager.received_cancel_check)
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["committed"])
        self.assertFalse((self.root / "audiobook.m4b").exists())

    def test_m4b_builder_receives_progress_callback_and_finishes_at_one_hundred(self) -> None:
        class ProgressManager(FakeProjectManager):
            def merge_m4b(
                self,
                per_chunk_chapters=False,
                metadata=None,
                output_path=None,
                progress_callback=None,
            ):
                self.assert_progress = callable(progress_callback)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "phase": "loading_audio",
                            "phase_label": "Loading production audio",
                            "completed_count": 2,
                            "total_count": 4,
                            "overall_percent": 27.5,
                            "progress_message": "Loaded 2 of 4 chunks.",
                        }
                    )
                return self._audio("m4b", output_path)

        manager = ProgressManager()
        events = []
        result = execute_export_build(
            root_dir=self.root,
            project_manager=manager,
            plan=self._plan(formats=["m4b"]),
            progress_callback=lambda event: events.append(dict(event)),
            audio_validator=self._validator,
        )

        self.assertTrue(manager.assert_progress)
        self.assertTrue(result["committed"])
        self.assertIn("loading_audio", {event.get("phase") for event in events})
        self.assertEqual(events[-1]["phase"], "complete")
        self.assertEqual(events[-1]["overall_percent"], 100)

    def test_cancellation_before_commit_preserves_previous_delivery(self) -> None:
        old = self.root / "cloned_audiobook.mp3"
        old.write_bytes(b"old")
        calls = {"count": 0}

        def cancel_check() -> bool:
            calls["count"] += 1
            return calls["count"] >= 2

        result = execute_export_build(
            root_dir=self.root,
            project_manager=FakeProjectManager(),
            plan=self._plan(formats=["mp3", "m4b"]),
            cancel_check=cancel_check,
            audio_validator=self._validator,
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["committed"])
        self.assertEqual(old.read_bytes(), b"old")
        self.assertFalse((self.root / "export_build.json").exists())

    def test_cancellation_during_multi_output_commit_rolls_back_everything(self) -> None:
        old_mp3 = self.root / "cloned_audiobook.mp3"
        old_m4b = self.root / "audiobook.m4b"
        old_mp3.write_bytes(b"old-mp3")
        old_m4b.write_bytes(b"old-m4b")
        old_receipt = b'{"status":"old"}'
        (self.root / "export_build.json").write_bytes(old_receipt)
        checks = {"count": 0}

        def cancel_check() -> bool:
            checks["count"] += 1
            return checks["count"] >= 7

        result = execute_export_build(
            root_dir=self.root,
            project_manager=FakeProjectManager(),
            plan=self._plan(formats=["mp3", "m4b"]),
            cancel_check=cancel_check,
            audio_validator=self._validator,
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["committed"])
        self.assertEqual(old_mp3.read_bytes(), b"old-mp3")
        self.assertEqual(old_m4b.read_bytes(), b"old-m4b")
        self.assertEqual((self.root / "export_build.json").read_bytes(), old_receipt)

    def test_dependency_change_during_commit_rolls_back_everything(self) -> None:
        old_mp3 = self.root / "cloned_audiobook.mp3"
        old_m4b = self.root / "audiobook.m4b"
        old_mp3.write_bytes(b"old-mp3")
        old_m4b.write_bytes(b"old-m4b")
        old_receipt = b'{"status":"old"}'
        (self.root / "export_build.json").write_bytes(old_receipt)
        checks = {"count": 0}

        def publication_check() -> None:
            checks["count"] += 1
            if checks["count"] >= 3:
                raise ExportAggregateError(
                    status_code=409,
                    code="export_dependencies_changed",
                    detail="Synthetic dependency change during commit.",
                )

        with self.assertRaises(ExportAggregateError) as changed:
            execute_export_build(
                root_dir=self.root,
                project_manager=FakeProjectManager(),
                plan=self._plan(formats=["mp3", "m4b"]),
                publication_check=publication_check,
                audio_validator=self._validator,
            )
        self.assertEqual(changed.exception.code, "export_dependencies_changed")
        self.assertEqual(old_mp3.read_bytes(), b"old-mp3")
        self.assertEqual(old_m4b.read_bytes(), b"old-m4b")
        self.assertEqual((self.root / "export_build.json").read_bytes(), old_receipt)

    def test_joined_publication_does_not_reenter_cancel_callback(self) -> None:
        inside_gate = {"value": False}

        def cancel_check() -> bool:
            if inside_gate["value"]:
                raise AssertionError(
                    "joined publication must not reenter scheduler cancellation"
                )
            return False

        def publication_gate(publisher, _result):
            inside_gate["value"] = True
            try:
                publisher()
            finally:
                inside_gate["value"] = False
            return "succeeded"

        result = execute_export_build(
            root_dir=self.root,
            project_manager=FakeProjectManager(),
            plan=self._plan(formats=["mp3"]),
            cancel_check=cancel_check,
            publication_gate=publication_gate,
            audio_validator=self._validator,
        )

        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["committed"])

    def test_commit_failure_restores_every_previous_output_and_receipt(self) -> None:
        old_mp3 = self.root / "cloned_audiobook.mp3"
        old_m4b = self.root / "audiobook.m4b"
        old_mp3.write_bytes(b"old-mp3")
        old_m4b.write_bytes(b"old-m4b")
        old_receipt = b'{"status":"old"}'
        (self.root / "export_build.json").write_bytes(old_receipt)
        calls = {"count": 0}

        def fail_second(source, target):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("synthetic commit failure")
            os.replace(source, target)

        with self.assertRaises(OSError):
            execute_export_build(
                root_dir=self.root,
                project_manager=FakeProjectManager(),
                plan=self._plan(formats=["mp3", "m4b"]),
                audio_validator=self._validator,
                commit_replace=fail_second,
            )
        self.assertEqual(old_mp3.read_bytes(), b"old-mp3")
        self.assertEqual(old_m4b.read_bytes(), b"old-m4b")
        self.assertEqual((self.root / "export_build.json").read_bytes(), old_receipt)

    def test_previous_output_backup_paths_point_to_final_history(self) -> None:
        old = self.root / "cloned_audiobook.mp3"
        old.write_bytes(b"old")
        result = execute_export_build(
            root_dir=self.root,
            project_manager=FakeProjectManager(),
            plan=self._plan(formats=["mp3"]),
            audio_validator=self._validator,
        )
        record = result["receipt"]["previous_outputs"]["mp3"]
        backup = self.root / record["backup_relative_path"]
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), b"old")
        self.assertNotIn(".pending", record["backup_relative_path"])


if __name__ == "__main__":
    unittest.main()
