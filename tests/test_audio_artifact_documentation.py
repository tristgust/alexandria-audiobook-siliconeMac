from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "AUDIO_ARTIFACTS.md"
SERVICE = ROOT / "app" / "audio_artifacts.py"
INVALIDATION = ROOT / "app" / "audio_invalidation.py"
PROJECT = ROOT / "app" / "project.py"
EXTERNAL = ROOT / "app" / "external_workflows.py"
SPEAKERS = ROOT / "app" / "speaker_management.py"


class AudioArtifactDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = DOC.read_text(encoding="utf-8")
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.invalidation = INVALIDATION.read_text(encoding="utf-8")
        cls.project = PROJECT.read_text(encoding="utf-8")
        cls.external = EXTERNAL.read_text(encoding="utf-8")
        cls.speakers = SPEAKERS.read_text(encoding="utf-8")

    def test_document_exists_and_states_content_bound_audio_contract(self) -> None:
        self.assertTrue(self.doc.startswith("# Audio Artifact Integrity"))
        for phrase in (
            "content-bound production artifact", "audio_state", "audio_fingerprint",
            "audio_sha256", "Legacy `status: done` audio", "not silently trusted",
        ):
            with self.subTest(phrase=phrase): self.assertIn(phrase, self.doc)

    def test_document_requires_atomic_install_and_final_output(self) -> None:
        for phrase in (
            "unique temporary file", "os.replace", "atomically replace", "merged MP3",
            "Audacity ZIP", "M4B", "preserves the last successful canonical",
        ):
            with self.subTest(phrase=phrase): self.assertIn(phrase, self.doc)
        self.assertIn("def install_generated_audio(", self.service)
        self.assertIn("def atomic_export_audio_segment(", self.service)
        self.assertGreaterEqual(self.project.count("os.replace("), 2)

    def test_document_requires_strict_final_readiness(self) -> None:
        for phrase in (
            "status `done`", "`audio_state: current`", "fingerprint", "SHA-256",
            "pending", "generating", "stale", "failed", "missing", "undecodable",
        ):
            with self.subTest(phrase=phrase): self.assertIn(phrase, self.doc)
        self.assertIn("def require_current_project_audio(", self.service)
        self.assertIn("require_current_project_audio(", self.project)

    def test_document_requires_operation_scoped_exact_audio_rollback(self) -> None:
        for phrase in (
            "content-addressed backup", "original canonical path", "backup path",
            "hard rollback conflict", "restores the pre-operation JSON and audio bytes",
            "audio/<sha256>.bin", "stored once per operation",
        ):
            with self.subTest(phrase=phrase): self.assertIn(phrase, self.doc)
        for source in (self.external, self.speakers):
            self.assertIn("apply_audio_invalidation_transaction(", source)
            self.assertIn("undo_audio_invalidation_transaction(", source)
        self.assertIn("backup_operation_audio(", self.invalidation)
        self.assertIn("restore_operation_audio(", self.invalidation)
        self.assertIn("validate_operation_audio_backups(", self.invalidation)
        self.assertIn("def backup_operation_audio(", self.service)
        self.assertIn("def restore_operation_audio(", self.service)

    def test_open_work_is_not_misrepresented_as_complete(self) -> None:
        for phrase in (
            "Still open", "bounded retention/cleanup", "older live invalidation records",
            "pronunciation provenance", "generated-Takes registry",
            "crash reconciliation", "Produce UI",
        ):
            with self.subTest(phrase=phrase): self.assertIn(phrase, self.doc)


if __name__ == "__main__":
    unittest.main()
