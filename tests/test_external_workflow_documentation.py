from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DOC = ROOT / "docs" / "EXTERNAL_STRUCTURED_WORKFLOWS.md"
TASK_BUNDLE_DOC = ROOT / "docs" / "TASK_BUNDLES.md"
INTERFACE_ACCEPTANCE = ROOT / "docs" / "INTERFACE_ACCEPTANCE.md"
INTERFACE_DESIGN = ROOT / "docs" / "INTERFACE_DESIGN.md"
RESUMABLE = ROOT / "docs" / "RESUMABLE_GENERATION.md"
DESIGN_MEMORY = ROOT / ".design-system" / "codex-ui-system.md"


class ExternalWorkflowDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_DOC.read_text(encoding="utf-8")
        cls.task_bundle = TASK_BUNDLE_DOC.read_text(encoding="utf-8")
        cls.acceptance = INTERFACE_ACCEPTANCE.read_text(encoding="utf-8")
        cls.design = INTERFACE_DESIGN.read_text(encoding="utf-8")
        cls.resumable = RESUMABLE.read_text(encoding="utf-8")
        cls.memory = DESIGN_MEMORY.read_text(encoding="utf-8")

    def test_operator_document_covers_portable_task_bundle(self):
        required = (
            "ordinary ChatGPT",
            "self-contained `*.alexandria-task.zip`",
            "completed task",
            "native JSON contract",
            "does not automate the ChatGPT website",
            "no user-visible handoff ID",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.workflow)

    def test_operator_document_covers_stage_boundaries(self):
        required = (
            "Script generation",
            "Script review",
            "Character-roster discovery",
            "Character-roster reconciliation",
            "bulk preparation-identity generation",
            "visual discovery",
            "advanced acoustic-identity",
            "per-line delivery-direction",
            "never approved merely because validation succeeds",
            "Reconciliation required",
            "Current",
            "Imported",
            "primary **Voice** section",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.workflow)

    def test_operator_document_covers_safe_script_application(self):
        required = (
            "current-to-imported count deltas",
            "Source verified",
            "Source not verified",
            "opaque candidate ID",
            "rebuilds all chunks as `pending`",
            "records them as stale",
            "exact pre-import bytes",
            "Rollback is rejected if any affected file changed",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.workflow)

    def test_interface_and_recovery_docs_reference_the_boundary(self):
        self.assertIn("Work with ChatGPT", self.design)
        self.assertIn("Phase 24C external structured workflow result", self.acceptance)
        self.assertIn("native review", self.acceptance)
        self.assertIn("External Script application and checkpoints", self.resumable)
        self.assertIn("keep`, `discard`, or `cancel", self.resumable)
        self.assertIn("Ordinary-ChatGPT work", self.memory)
        self.assertIn("Phase 24C external-workflow browser evidence", self.memory)
        self.assertIn("Alexandria Task Bundles", self.task_bundle)
        self.assertIn("Voice Reference guidance", self.task_bundle)
        self.assertIn("never copies or types", self.task_bundle)


if __name__ == "__main__":
    unittest.main()
