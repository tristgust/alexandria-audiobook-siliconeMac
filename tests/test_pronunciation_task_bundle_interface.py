from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PronunciationTaskBundleInterfaceTests(unittest.TestCase):
    def test_script_workflow_exposes_reviewed_pronunciation_task(self) -> None:
        source = (
            ROOT / "app/static/pages/script_pronunciation_guidance.js"
        ).read_text(encoding="utf-8")
        for required in (
            "Download pronunciation task bundle",
            "Import completed pronunciation task",
            "pronunciation_guidance",
            "/api/pronunciation-registry/preview",
            "/api/pronunciation-registry/entries",
            "Accept guidance",
            "expected_registry_fingerprint",
            "explicit_acceptance_required",
            "Import never changes the Script, registry, or audio.",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn("pronunciation_registry.json", source)
        self.assertNotIn("audio-invalidation", source)

    def test_script_sequence_loads_pronunciation_after_script_acceptance(self) -> None:
        workflows = (
            ROOT / "app/static/pages/script_workflows.js"
        ).read_text(encoding="utf-8")
        state = (
            ROOT / "app/static/pages/script_workflow_state.js"
        ).read_text(encoding="utf-8")
        self.assertIn("createScriptPronunciationGuidance", workflows)
        self.assertIn("pronunciationGuidance.refresh()", workflows)
        self.assertIn("pronunciationGuidance.root", state)
        self.assertLess(
            state.index("deliveryPlan.root"),
            state.index("pronunciationGuidance.root"),
        )

    def test_javascript_modules_parse(self) -> None:
        for relative in (
            "app/static/pages/script_pronunciation_guidance.js",
            "app/static/pages/script_workflows.js",
            "app/static/pages/script_workflow_state.js",
        ):
            with self.subTest(relative=relative):
                result = subprocess.run(
                    ["node", "--check", str(ROOT / relative)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
