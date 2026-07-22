from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from character_roster import save_character_roster
from generation_state import fingerprint_text, fingerprint_value
from voice_identity_context import build_script_speaker_roster


class ExternalWorkflowAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = 'The room was quiet. "Run," said the Doctor.'
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.source, encoding="utf-8")
        self.entries = [
            {
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "DOCTOR",
                "text": "Run,",
                "instruct": "Urgent command.",
            },
            {
                "speaker": "NARRATOR",
                "text": "said the Doctor.",
                "instruct": "Neutral narration.",
            },
        ]
        self.old_entries = [
            {
                "speaker": "NARRATOR",
                "text": "Old script.",
                "instruct": "Neutral.",
            }
        ]
        self.write_json(
            "state.json",
            {"input_file_path": str(self.source_path)},
        )
        self.write_json("annotated_script.json", self.old_entries)
        self.write_json(
            "chunks.json",
            [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "Old script.",
                    "instruct": "Neutral.",
                    "status": "done",
                    "audio_path": "voicelines/chunk_000000.wav",
                }
            ],
        )
        self.write_json(
            "voice_config.json",
            {"NARRATOR": {"type": "custom", "voice": "Ryan"}},
        )
        config_path = self.root / "app" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "llm": {},
                    "tts": {"mode": "local", "url": "", "device": "auto"},
                    "prompts": {
                        "system_prompt": "Generate exact script JSON.",
                        "user_prompt": "Use {chunk}.",
                        "review_system_prompt": "Review exact script JSON.",
                        "review_user_prompt": "Review {entries}.",
                    },
                    "generation": {"chunk_size": 3000},
                }
            ),
            encoding="utf-8",
        )
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CONFIG_PATH", str(config_path)),
            patch.object(
                app_module,
                "SCRIPT_PATH",
                str(self.root / "annotated_script.json"),
            ),
            patch.object(
                app_module,
                "SCRIPT_METADATA_PATH",
                str(self.root / "annotated_script.meta.json"),
            ),
            patch.object(
                app_module,
                "CHUNKS_PATH",
                str(self.root / "chunks.json"),
            ),
            patch.object(
                app_module,
                "GENERATION_STATE_PATH",
                str(self.root / "generation_state.json"),
            ),
            patch.object(
                app_module,
                "VOICE_CONFIG_PATH",
                str(self.root / "voice_config.json"),
            ),
            patch.object(
                app_module,
                "EXTERNAL_WORKFLOW_UPLOAD_DIR",
                str(self.root / "external_workflows" / "uploads"),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.root / "character_roster.json"),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_DRAFT_PATH",
                str(self.root / "character_roster.draft.json"),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_STATE_PATH",
                str(self.root / "character_roster_state.json"),
            ),
            patch.object(
                app_module,
                "VOICE_TRAINING_PROJECTS_DIR",
                str(self.root / "voice_training_projects"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.saved_process_state = {
            name: dict(value)
            for name, value in app_module.process_state.items()
        }
        for name in ("script", "roster", "persona", "visual", "audio", "review"):
            app_module.process_state[name]["running"] = False
            app_module.process_state[name]["logs"] = []
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        for name, value in self.saved_process_state.items():
            app_module.process_state[name] = value
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def write_json(self, name: str, value) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def test_generation_handoff_download_result_review_and_apply(self) -> None:
        exported = self.client.post(
            "/api/external/handoff/export",
            json={"task_type": "script_generation"},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        handoff = exported.json()
        self.assertEqual(handoff["task_type"], "script_generation")
        self.assertTrue(handoff["download_url"].endswith("/download"))

        downloaded = self.client.get(handoff["download_url"])
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.headers["content-type"], "application/zip")
        self.assertTrue(downloaded.content.startswith(b"PK"))

        prompt = self.client.get(
            f"/api/external/handoff/{handoff['handoff_id']}/prompt"
        )
        self.assertEqual(prompt.status_code, 200, prompt.text)
        self.assertIn("Return only valid JSON", prompt.json()["prompt"])

        with patch.object(
            app_module,
            "open_handoff_folder",
            return_value={
                "handoff_id": handoff["handoff_id"],
                "task_type": "script_generation",
                "opened": True,
            },
        ):
            opened = self.client.post(
                f"/api/external/handoff/{handoff['handoff_id']}/open-folder"
            )
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertTrue(opened.json()["opened"])

        inspected = self.client.post(
            "/api/external/handoff/result",
            data={"handoff_id": handoff["handoff_id"]},
            files={
                "file": (
                    "result.json",
                    json.dumps(self.entries).encode("utf-8"),
                    "application/json",
                )
            },
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        candidate = inspected.json()
        self.assertEqual(candidate["provenance"]["status"], "verified")
        self.assertEqual(candidate["summary"]["entry_count"], 3)
        self.assertEqual(candidate["comparison"]["current"]["entry_count"], 1)
        self.assertEqual(candidate["comparison"]["deltas"]["entry_count"], 2)

        applied = self.client.post(
            "/api/external/annotated-script/apply",
            json={"candidate_id": candidate["candidate_id"]},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["status"], "applied")
        self.assertEqual(
            json.loads((self.root / "annotated_script.json").read_text()),
            self.entries,
        )
        self.assertTrue((self.root / "annotated_script.meta.json").exists())
        self.assertTrue(
            all(
                chunk["status"] == "pending"
                for chunk in json.loads((self.root / "chunks.json").read_text())
            )
        )

    def test_stage_aware_exports_validate_required_project_context(self) -> None:
        roster = self.client.post(
            "/api/external/handoff/export",
            json={"task_type": "roster_discovery"},
        )
        self.assertEqual(roster.status_code, 200, roster.text)
        self.assertEqual(roster.json()["manifest"]["contract"], "roster_discovery")

        source_bound_script = [
            {
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Neutral narration.",
            }
        ]
        self.write_json("annotated_script.json", source_bound_script)
        approved_roster = build_script_speaker_roster(
            root_dir=self.root,
            source_text=self.source,
            current_source_fingerprint=fingerprint_text(self.source),
        )
        save_character_roster(
            approved_roster,
            self.root / "character_roster.json",
            source_text=self.source,
            expected_status="approved",
        )
        self.write_json("annotated_script.json", self.old_entries)

        persona = self.client.post(
            "/api/external/handoff/export",
            json={
                "task_type": "persona_generation",
                "target": "NARRATOR",
            },
        )
        self.assertEqual(persona.status_code, 200, persona.text)
        persona_manifest = persona.json()["manifest"]
        self.assertEqual(persona_manifest["contract"], "persona")
        self.assertEqual(
            persona_manifest["artifact_fingerprints"]["annotated_script"],
            fingerprint_value(self.old_entries),
        )
        persona_result = self.client.post(
            "/api/external/handoff/result",
            data={"handoff_id": persona.json()["handoff_id"]},
            files={
                "file": (
                    "result.json",
                    json.dumps(
                        {
                            "description": "A measured, neutral narrator voice.",
                            "ref_text": "Old script.",
                        }
                    ).encode("utf-8"),
                    "application/json",
                )
            },
        )
        self.assertEqual(persona_result.status_code, 200, persona_result.text)
        self.assertEqual(persona_result.json()["kind"], "structured_result")
        self.assertEqual(persona_result.json()["task_type"], "persona_generation")
        self.assertEqual(persona_result.json()["status"], "inspected")
        self.assertTrue(persona_result.json()["candidate_id"].startswith("structured_"))
        self.assertEqual(persona_result.json()["review"]["root_type"], "object")
        self.assertTrue(persona_result.json()["native_transfer"]["supported"])
        self.assertEqual(
            persona_result.json()["native_transfer"]["destination"],
            "expressive_voices",
        )

        transferred = self.client.post(
            "/api/external/structured-result/"
            f"{persona_result.json()['candidate_id']}/transfer",
            json={
                "result_fingerprint": persona_result.json()[
                    "result_fingerprint"
                ]
            },
        )
        self.assertEqual(transferred.status_code, 200, transferred.text)
        self.assertEqual(transferred.json()["status"], "transferred")
        self.assertEqual(
            transferred.json()["application"]["destination"],
            "expressive_voices",
        )
        project_path = (
            self.root
            / "voice_training_projects"
            / transferred.json()["application"]["character_id"]
            / "project.json"
        )
        project = json.loads(project_path.read_text(encoding="utf-8"))
        self.assertEqual(
            project["desired_base_persona"]["approval_status"],
            "draft",
        )
        self.assertEqual(
            project["desired_base_persona"]["description"],
            "A measured, neutral narrator voice.",
        )
        fetched_structured = self.client.get(
            "/api/external/structured-result/"
            f"{persona_result.json()['candidate_id']}"
        )
        self.assertEqual(
            fetched_structured.json()["status"],
            "transferred",
        )

        missing_target = self.client.post(
            "/api/external/handoff/export",
            json={"task_type": "persona_generation"},
        )
        self.assertEqual(missing_target.status_code, 400)
        self.assertEqual(
            missing_target.json()["detail"]["code"],
            "external_target_required",
        )

        no_observations = self.client.post(
            "/api/external/handoff/export",
            json={"task_type": "roster_reconciliation"},
        )
        self.assertEqual(no_observations.status_code, 409)
        self.assertEqual(
            no_observations.json()["detail"]["code"],
            "external_roster_observations_required",
        )

        no_visual_roster = self.client.post(
            "/api/external/handoff/export",
            json={
                "task_type": "visual_discovery",
                "target": "DOCTOR",
            },
        )
        self.assertEqual(no_visual_roster.status_code, 409)
        self.assertEqual(
            no_visual_roster.json()["detail"]["code"],
            "external_visual_roster_entry_required",
        )

    def test_review_handoff_requires_and_fingerprints_current_script(self) -> None:
        response = self.client.post(
            "/api/external/handoff/export",
            json={"task_type": "script_review"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        manifest = response.json()["manifest"]
        self.assertEqual(manifest["contract"], "review")
        self.assertEqual(
            manifest["artifact_fingerprints"]["annotated_script"],
            fingerprint_value(self.old_entries),
        )

        (self.root / "annotated_script.json").unlink()
        blocked = self.client.post(
            "/api/external/handoff/export",
            json={"task_type": "script_review"},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["code"], "external_script_required")

    def test_direct_import_can_be_unverified_but_requires_explicit_apply(self) -> None:
        incoming = [
            {
                "speaker": "NARRATOR",
                "text": "Independent imported wording.",
                "instruct": "Neutral.",
            }
        ]
        inspected = self.client.post(
            "/api/external/annotated-script/inspect",
            data={"verify_source": "false"},
            files={
                "file": (
                    "incoming.json",
                    json.dumps(incoming).encode("utf-8"),
                    "application/json",
                )
            },
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        candidate = inspected.json()
        self.assertEqual(candidate["status"], "inspected")
        self.assertEqual(candidate["provenance"]["status"], "unverified")
        self.assertEqual(
            json.loads((self.root / "annotated_script.json").read_text()),
            self.old_entries,
        )
        fetched = self.client.get(
            f"/api/external/annotated-script/candidate/{candidate['candidate_id']}"
        )
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json(), candidate)

    def test_source_verification_and_stale_handoff_fail_closed(self) -> None:
        mismatched = [
            {
                "speaker": "NARRATOR",
                "text": "Different wording.",
                "instruct": "Neutral.",
            }
        ]
        fidelity = self.client.post(
            "/api/external/annotated-script/inspect",
            data={"verify_source": "true"},
            files={
                "file": (
                    "mismatch.json",
                    json.dumps(mismatched).encode("utf-8"),
                    "application/json",
                )
            },
        )
        self.assertEqual(fidelity.status_code, 400, fidelity.text)
        self.assertEqual(fidelity.json()["detail"]["code"], "source_fidelity_failed")

        exported = self.client.post(
            "/api/external/handoff/export",
            json={"task_type": "script_generation"},
        ).json()
        self.source_path.write_text("Changed source.", encoding="utf-8")
        stale = self.client.post(
            "/api/external/handoff/result",
            data={"handoff_id": exported["handoff_id"]},
            files={
                "file": (
                    "result.json",
                    json.dumps(self.entries).encode("utf-8"),
                    "application/json",
                )
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "stale_source")

    def test_active_pipeline_blocks_apply_and_rollback(self) -> None:
        inspected = self.client.post(
            "/api/external/annotated-script/inspect",
            data={"verify_source": "false"},
            files={
                "file": (
                    "incoming.json",
                    json.dumps(self.entries).encode("utf-8"),
                    "application/json",
                )
            },
        ).json()
        app_module.process_state["audio"]["running"] = True
        blocked = self.client.post(
            "/api/external/annotated-script/apply",
            json={"candidate_id": inspected["candidate_id"]},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["stage"], "audio")
        app_module.process_state["audio"]["running"] = False

        applied = self.client.post(
            "/api/external/annotated-script/apply",
            json={"candidate_id": inspected["candidate_id"]},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        operation_id = applied.json()["operation"]["operation_id"]
        app_module.process_state["roster"]["running"] = True
        rollback = self.client.post(
            "/api/external/annotated-script/rollback",
            json={"operation_id": operation_id},
        )
        self.assertEqual(rollback.status_code, 409)
        self.assertEqual(rollback.json()["detail"]["stage"], "roster")

    def test_upload_names_and_empty_files_are_rejected(self) -> None:
        traversal = self.client.post(
            "/api/external/annotated-script/inspect",
            data={"verify_source": "false"},
            files={"file": ("../escape.json", b"[]", "application/json")},
        )
        self.assertEqual(traversal.status_code, 400)
        self.assertEqual(
            traversal.json()["detail"]["code"],
            "unsupported_external_upload",
        )
        empty = self.client.post(
            "/api/external/annotated-script/inspect",
            data={"verify_source": "false"},
            files={"file": ("empty.json", b"", "application/json")},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["detail"]["code"], "external_upload_empty")


if __name__ == "__main__":
    unittest.main()
