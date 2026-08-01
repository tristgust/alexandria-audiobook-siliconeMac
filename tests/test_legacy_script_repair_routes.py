from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module


class LegacyScriptRepairRouteTests(unittest.TestCase):
    def test_confirmed_repair_uses_verified_candidate_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.txt"
            source_path.write_text(
                "OceanofPDF.com\n\n‘It’s dangerous,’ he said.",
                encoding="utf-8",
            )
            legacy_entries = [
                {
                    "speaker": "NARRATOR",
                    "text": "OceanofPDF.com",
                    "instruct": "Neutral narration.",
                },
                {
                    "speaker": "DOCTOR",
                    "text": "It",
                    "instruct": "Quiet warning.",
                },
                {
                    "speaker": "NARRATOR",
                    "text": "s dangerous,’ he said.",
                    "instruct": "Neutral narration.",
                },
            ]
            inspected = {
                "candidate_id": "candidate_repaired",
                "consequences": {"checkpoint_decision_required": False},
            }
            applied = {"operation_id": "script_import_repaired"}
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "EXTERNAL_WORKFLOW_UPLOAD_DIR", str(root / "uploads")),
                patch.object(app_module, "_external_import_busy_stage", return_value=None),
                patch.object(app_module, "_selected_script_input_path", return_value=str(source_path)),
                patch.object(app_module, "_external_script_entries", return_value=legacy_entries),
                patch.object(
                    app_module,
                    "_external_source_context",
                    return_value=(
                        {"fingerprint": "f" * 64},
                        "‘It’s dangerous,’ he said.",
                        None,
                    ),
                ),
                patch.object(
                    app_module,
                    "_external_script_state",
                    return_value={
                        "script_fingerprint": "s" * 64,
                        "checkpoint_status": "none",
                        "generated_audio_count": 0,
                    },
                ),
                patch.object(
                    app_module,
                    "inspect_annotated_script_upload",
                    return_value=inspected,
                ) as inspect_mock,
                patch.object(
                    app_module,
                    "apply_annotated_script_candidate",
                    return_value=applied,
                ) as apply_mock,
            ):
                result = asyncio.run(
                    app_module.repair_legacy_imported_script(
                        app_module.LegacyScriptRepairRequest(confirm=True)
                    )
                )

            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["repair"]["watermark_count"], 1)
            self.assertEqual(result["repair"]["repaired_entry_count"], 2)
            self.assertEqual(result["application"], applied)
            inspected_path = Path(inspect_mock.call_args.kwargs["import_path"])
            self.assertFalse(inspected_path.exists())
            apply_mock.assert_called_once_with(
                root_dir=str(root),
                candidate_id="candidate_repaired",
                current_script_fingerprint="s" * 64,
                checkpoint_status="none",
                checkpoint_decision=None,
            )

    def test_confirmed_source_start_trim_updates_prepared_source_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.txt"
            source_path.write_text(
                "Cover copy.\n\nA cold wind blew.\n‘It’s dangerous,’ he said.",
                encoding="utf-8",
            )
            entries = [
                {
                    "speaker": "NARRATOR",
                    "text": "Cover copy.\n\nA cold wind blew.",
                    "instruct": "Neutral narration.",
                },
                {
                    "speaker": "DOCTOR",
                    "text": "It’s dangerous,",
                    "instruct": "Quiet warning.",
                },
                {
                    "speaker": "NARRATOR",
                    "text": "he said.",
                    "instruct": "Neutral narration.",
                },
            ]
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "EXTERNAL_WORKFLOW_UPLOAD_DIR", str(root / "uploads")),
                patch.object(app_module, "_external_import_busy_stage", return_value=None),
                patch.object(app_module, "_selected_script_input_path", return_value=str(source_path)),
                patch.object(app_module, "_external_script_entries", return_value=entries),
                patch.object(
                    app_module,
                    "_external_script_state",
                    return_value={
                        "script_fingerprint": "s" * 64,
                        "checkpoint_status": "none",
                        "generated_audio_count": 0,
                    },
                ),
                patch.object(
                    app_module,
                    "inspect_annotated_script_upload",
                    return_value={
                        "candidate_id": "candidate_trimmed",
                        "consequences": {"checkpoint_decision_required": False},
                    },
                ),
                patch.object(
                    app_module,
                    "apply_annotated_script_candidate",
                    return_value={"operation": {"operation_id": "operation_trimmed"}},
                ),
            ):
                result = asyncio.run(
                    app_module.repair_legacy_imported_script(
                        app_module.LegacyScriptRepairRequest(
                            confirm=True,
                            start_marker="A cold wind blew.",
                        )
                    )
                )

            self.assertEqual(result["status"], "applied")
            self.assertTrue(source_path.read_text(encoding="utf-8").startswith("A cold wind blew."))
            backup_path = Path(result["source_repair_backup"])
            self.assertTrue(backup_path.is_file())
            self.assertTrue(backup_path.read_text(encoding="utf-8").startswith("Cover copy."))
            self.assertTrue((backup_path.parent / "receipt.json").is_file())


if __name__ == "__main__":
    unittest.main()
