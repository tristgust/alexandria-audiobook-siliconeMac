from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from chatgpt_handoff import HandoffValidationError
from external_workflows import (
    ExternalWorkflowConflictError,
    create_stored_task_bundle,
    get_structured_result_candidate,
    get_task_bundle_path,
    inspect_completed_task_upload,
    list_task_library,
    mark_structured_result_transferred,
)
from generation_state import fingerprint_text
from task_bundles import (
    TASK_REGISTRY,
    create_result_envelope,
    inspect_task_bundle,
)


ROOT = Path(__file__).resolve().parents[1]


class Boundary14AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_text = "Good evening."
        self.source_fingerprint = fingerprint_text(self.source_text)
        (self.root / "source.txt").write_text(
            self.source_text,
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_task(self, *, created_at: str = "2026-08-02T22:00:00Z") -> tuple[dict, Path]:
        task = create_stored_task_bundle(
            root_dir=self.root,
            task_type="persona_generation",
            input_payload={
                "speaker": "THE DOCTOR",
                "sample_lines": [self.source_text],
            },
            application_version="test",
            source_fingerprint=self.source_fingerprint,
            target={"kind": "speaker", "value": "THE DOCTOR"},
            created_at_utc=created_at,
        )
        path, _record = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        return task, path

    def envelope(self, bundle: Path, *, description: str) -> Path:
        payload = create_result_envelope(
            task_bundle_path=bundle,
            result={
                "description": description,
                "ref_text": self.source_text,
            },
        )
        target = self.root / (hashlib.sha256(description.encode()).hexdigest() + ".json")
        target.write_text(json.dumps(payload), encoding="utf-8")
        return target

    def inspect_result(self, completed: Path) -> dict:
        return inspect_completed_task_upload(
            root_dir=self.root,
            completed_path=completed,
            original_task_path=None,
            current_source_fingerprint=self.source_fingerprint,
            current_artifact_fingerprints={},
            source_text=self.source_text,
            source_context={
                "path": str(self.root / "source.txt"),
                "basename": "source.txt",
                "fingerprint": self.source_fingerprint,
                "character_count": len(self.source_text),
            },
            current_script_fingerprint=None,
            checkpoint_status="none",
            generated_audio_count=0,
            created_at_utc="2026-08-02T23:00:00Z",
        )

    def rewrite_zip(
        self,
        source: Path,
        destination: Path,
        *,
        extra_name: str | None = None,
        duplicate_name: str | None = None,
        symlink_name: str | None = None,
    ) -> None:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as output:
            for info in original.infolist():
                payload = original.read(info)
                if info.filename == symlink_name:
                    link = zipfile.ZipInfo(info.filename)
                    link.create_system = 3
                    link.external_attr = (0o120777 << 16)
                    output.writestr(link, b"target")
                else:
                    output.writestr(info, payload)
                if info.filename == duplicate_name:
                    output.writestr(info.filename, payload)
            if extra_name is not None:
                output.writestr(extra_name, b"{}")

    def test_archive_paths_symlinks_and_duplicates_fail_before_project_mutation(self) -> None:
        _task, bundle = self.create_task()
        sentinel = self.root / "project-state.json"
        sentinel.write_text('{"unchanged":true}\n', encoding="utf-8")
        before = sentinel.read_bytes()
        cases = (
            ("traversal", {"extra_name": "../escape.json"}, "unsafe_archive_member"),
            ("absolute", {"extra_name": "/escape.json"}, "unsafe_archive_member"),
            ("drive", {"extra_name": "C:/escape.json"}, "unsafe_archive_member"),
            ("control", {"extra_name": "bad\nname.json"}, "unsafe_archive_member"),
            ("duplicate", {"duplicate_name": "input.json"}, "duplicate_archive_member"),
            ("symlink", {"symlink_name": "input.json"}, "archive_symlink"),
        )
        for name, options, code in cases:
            with self.subTest(name=name):
                target = self.root / f"{name}.zip"
                self.rewrite_zip(bundle, target, **options)
                with self.assertRaises(HandoffValidationError) as error:
                    inspect_task_bundle(target)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(sentinel.read_bytes(), before)
                self.assertFalse((self.root / "pronunciation_registry.json").exists())

    def test_duplicate_out_of_order_and_restart_are_candidate_safe(self) -> None:
        first_task, first_bundle = self.create_task(
            created_at="2026-08-02T22:00:00Z"
        )
        second_task, second_bundle = self.create_task(
            created_at="2026-08-02T22:01:00Z"
        )
        second = self.inspect_result(
            self.envelope(second_bundle, description="Second result.")
        )
        first = self.inspect_result(
            self.envelope(first_bundle, description="First result.")
        )
        duplicate = self.inspect_result(
            self.envelope(second_bundle, description="Second result.")
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["candidate_id"], second["candidate_id"])
        self.assertNotEqual(first["candidate_id"], second["candidate_id"])
        records = list_task_library(
            root_dir=self.root,
            current_source_fingerprint=self.source_fingerprint,
            current_artifact_fingerprints={},
        )
        self.assertEqual(len(records), 2)
        self.assertEqual({record["status"] for record in records}, {"imported"})
        self.assertEqual(len({record["download_url"] for record in records}), 2)
        self.assertTrue(
            all("handoff_id" not in record and "task_id" not in record for record in records)
        )
        mark_structured_result_transferred(
            root_dir=self.root,
            candidate_id=second["candidate_id"],
            expected_result_fingerprint=second["result_fingerprint"],
            application={"destination": "expressive_voices"},
        )
        with self.assertRaises(ExternalWorkflowConflictError) as replay:
            mark_structured_result_transferred(
                root_dir=self.root,
                candidate_id=second["candidate_id"],
                expected_result_fingerprint=second["result_fingerprint"],
                application={"destination": "expressive_voices"},
            )
        self.assertEqual(replay.exception.code, "structured_result_already_transferred")

        code = (
            "import json,sys; "
            "from external_workflows import get_structured_result_candidate; "
            "print(json.dumps(get_structured_result_candidate(root_dir=sys.argv[1], candidate_id=sys.argv[2]), sort_keys=True))"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "app")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        restarted = subprocess.run(
            [sys.executable, "-c", code, str(self.root), second["candidate_id"]],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        restored = json.loads(restarted.stdout)
        self.assertEqual(restored["status"], "transferred")
        self.assertEqual(restored["candidate_id"], second["candidate_id"])

    def test_current_task_bundle_operations_are_offline_and_registry_driven(self) -> None:
        _task, bundle = self.create_task()
        result = self.envelope(bundle, description="Offline result.")
        with mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("network access is forbidden"),
        ):
            inspected = inspect_task_bundle(bundle)
            candidate = self.inspect_result(result)
        self.assertEqual(inspected["manifest"]["schema_version"], 2)
        self.assertEqual(candidate["status"], "inspected")
        self.assertEqual(
            set(TASK_REGISTRY),
            {definition.task_type for definition in TASK_REGISTRY.values()},
        )

    def test_documentation_states_v1_v2_security_states_and_native_review(self) -> None:
        document = (ROOT / "docs/TASK_BUNDLES.md").read_text(encoding="utf-8")
        for text in (
            "Version 2",
            "Version 1",
            "awaiting_import",
            "imported",
            "stale",
            "failed",
            "transferred",
            "path traversal",
            "checksums",
            "native review",
            "pronunciation_guidance",
            "offline",
        ):
            with self.subTest(text=text):
                self.assertIn(text, document)


if __name__ == "__main__":
    unittest.main()
