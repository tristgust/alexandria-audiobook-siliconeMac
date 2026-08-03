from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from character_roster import save_character_roster
from backend_render_plan import chunks_fingerprint
from fish_inline_cues import text_sha256
from generation_state import fingerprint_text, fingerprint_value
from task_bundles import create_result_envelope, inspect_task_bundle
from voice_identity_context import build_script_speaker_roster


class TaskBundleRouteTests(unittest.TestCase):
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
                "instruct": "Even narration.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "Run,",
                "instruct": "Urgent command.",
            },
            {
                "speaker": "NARRATOR",
                "text": "said the Doctor.",
                "instruct": "Even narration.",
            },
        ]
        self.write_json(
            "state.json",
            {"input_file_path": str(self.source_path)},
        )
        self.write_json("annotated_script.json", self.entries)
        self.write_json("chunks.json", [])
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
                        "system_prompt": "Generate exact Script JSON.",
                        "user_prompt": "Use {chunk}.",
                        "review_system_prompt": "Review exact Script JSON.",
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
                "CHARACTER_ROSTER_STATE_PATH",
                str(self.root / "character_roster_state.json"),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_DRAFT_PATH",
                str(self.root / "character_roster.draft.json"),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.root / "character_roster.json"),
            ),
            patch.object(
                app_module,
                "PERSONA_VISUAL_STATE_PATH",
                str(self.root / "persona_visual_state.json"),
            ),
            patch.object(
                app_module,
                "VOICE_TRAINING_PROJECTS_DIR",
                str(self.root / "voice_training_projects"),
            ),
            patch.object(
                app_module,
                "EXTERNAL_WORKFLOW_UPLOAD_DIR",
                str(self.root / "external_workflows" / "uploads"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.saved_process_state = {
            name: dict(value)
            for name, value in app_module.process_state.items()
        }
        for name in (
            "script",
            "roster",
            "persona",
            "visual",
            "audio",
            "review",
        ):
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

    def prepare_approved_roster(self) -> dict:
        roster = build_script_speaker_roster(
            root_dir=self.root,
            source_text=self.source,
            current_source_fingerprint=fingerprint_text(self.source),
            script_path=self.root / "annotated_script.json",
        )
        return save_character_roster(
            roster,
            self.root / "character_roster.json",
            source_text=None,
            expected_status="approved",
        )

    def export_and_download(
        self,
        task_type: str,
        target: str | None = None,
        options: dict[str, bool] | None = None,
    ):
        response = self.client.post(
            "/api/tasks/export",
            json={
                "task_type": task_type,
                "target": target,
                "options": options or {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()
        downloaded = self.client.get(record["download_url"])
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        path = self.root / f"{task_type}.alexandria-task.zip"
        path.write_bytes(downloaded.content)
        return record, path

    def test_backend_render_plan_task_exports_and_applies_without_invalidating_audio(self) -> None:
        chunks = [
            {
                "id": index,
                **entry,
                "status": "done",
                "audio_state": "current",
                "audio_path": f"voicelines/{index}.mp3",
            }
            for index, entry in enumerate(self.entries)
        ]
        self.write_json("chunks.json", chunks)
        script_fingerprint = fingerprint_value(self.entries)
        with patch.object(
            app_module,
            "_current_script_lifecycle_status",
            return_value={
                "accepted": True,
                "fingerprints": {"script": script_fingerprint},
            },
        ):
            _, task_path = self.export_and_download(
                "backend_render_plan_generation"
            )
        inspected = inspect_task_bundle(task_path)
        self.assertEqual(inspected["manifest"]["contract"], "backend_render_plan")
        task_input = inspected["input"]
        self.assertEqual(len(task_input["chunks"]), 3)
        self.assertEqual(
            task_input["chunks"][1]["spoken_continuity"]["role"],
            "dialogue_open_before_attribution",
        )
        self.assertIn("community_caveats", task_input["backend_guidance"]["fish"])
        result = {
            "schema_version": 1,
            "script_fingerprint": task_input["script_fingerprint"],
            "chunks_fingerprint": task_input["chunks_fingerprint"],
            "entries": [
                {
                    "index": item["index"],
                    "chunk_id": item["chunk_id"],
                    "speaker": item["speaker"],
                    "text_sha256": item["text_sha256"],
                    "qwen_instruction": (
                        "Urgent and continuous, preserving the authored boundary."
                        if item["index"] == 1
                        else "Measured and naturally connected to the neighboring line."
                    ),
                    "fish_direction": (
                        "urgent command, open cadence"
                        if item["index"] == 1
                        else "measured attached narration"
                    ),
                    "fish_cues": (
                        [
                            {
                                "anchor": "start",
                                "tag": "urgent",
                                "kind": "delivery",
                            }
                        ]
                        if item["index"] == 1
                        else []
                    ),
                    "warnings": [],
                }
                for item in task_input["chunks"]
            ],
            "warnings": [],
        }
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result=result,
        )
        result_path = self.root / "completed-backend-render-plan.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        candidate = imported.json()
        self.assertEqual(candidate["task_type"], "backend_render_plan_generation")
        self.assertEqual(candidate["status"], "transferred")
        applied = candidate["application"]
        self.assertEqual(applied["destination"], "script_review")
        self.assertEqual(applied["chunk_count"], 3)
        saved_chunks = json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved_chunks[1]["audio_state"], "current")
        self.assertNotIn("backend_render_plan_applied", saved_chunks[1])
        self.assertEqual(
            saved_chunks[1]["qwen_render_instruction"],
            result["entries"][1]["qwen_instruction"],
        )
        self.assertEqual(
            saved_chunks[1]["fish_render_plan"]["text_sha256"],
            text_sha256(saved_chunks[1]["text"]),
        )
        plan = json.loads(
            (self.root / "backend_render_plan.json").read_text(encoding="utf-8")
        )
        self.assertEqual(plan["chunk_count"], 3)
        self.assertEqual(
            task_input["chunks_fingerprint"],
            chunks_fingerprint(chunks),
        )

    def test_pronunciation_task_exports_imports_and_previews_as_draft_only(self) -> None:
        chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "Skaro was silent.",
                "instruct": "Quiet narration.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/0.mp3",
            }
        ]
        self.write_json("chunks.json", chunks)
        script = [
            {
                "speaker": "NARRATOR",
                "text": "Skaro was silent.",
                "instruct": "Quiet narration.",
            }
        ]
        self.write_json("annotated_script.json", script)
        script_fingerprint = fingerprint_value(script)
        before_chunks = (self.root / "chunks.json").read_bytes()
        with patch.object(
            app_module,
            "_current_script_lifecycle_status",
            return_value={
                "accepted": True,
                "fingerprints": {"script": script_fingerprint},
            },
        ):
            _, task_path = self.export_and_download("pronunciation_guidance")
        inspected = inspect_task_bundle(task_path)
        self.assertEqual(
            inspected["manifest"]["contract"],
            "pronunciation_guidance",
        )
        task_input = inspected["input"]
        self.assertEqual(task_input["schema_version"], 1)
        self.assertEqual(task_input["existing_entries"], [])
        self.assertEqual(task_input["chunks"][0]["text"], "Skaro was silent.")
        self.assertEqual(
            task_input["chunks"][0]["chunk_text_sha256"],
            hashlib.sha256(b"Skaro was silent.").hexdigest(),
        )
        result = {
            "schema_version": 1,
            "entries": [
                {
                    "chunk_index": 0,
                    "start_char": 0,
                    "end_char": 5,
                    "original": "Skaro",
                    "chunk_text_sha256": hashlib.sha256(
                        b"Skaro was silent."
                    ).hexdigest(),
                    "spoken_form": "SKA-roh",
                    "phonetic_hint": None,
                    "languages": [],
                    "character_labels": ["NARRATOR"],
                    "voice_ids": [],
                    "engine_ids": [],
                    "engine_source": {
                        "kind": "task_bundle",
                        "engine": None,
                        "revision": None,
                        "phoneme_alphabet": None,
                    },
                    "fallback": {
                        "strategy": "bypass",
                        "spoken_form": None,
                        "reason": "Use only the reviewed spoken form.",
                    },
                    "rationale": "Proper-name pronunciation guidance.",
                }
            ],
            "warnings": [],
        }
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result=result,
        )
        result_path = self.root / "completed-pronunciation.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        candidate = imported.json()
        self.assertEqual(candidate["task_type"], "pronunciation_guidance")
        self.assertEqual(candidate["status"], "transferred")
        application = candidate["application"]
        self.assertEqual(application["status"], "review_ready")
        self.assertEqual(application["candidate_count"], 1)
        self.assertTrue(application["explicit_acceptance_required"])
        self.assertFalse(application["production_state_changed"])
        native_entry = application["entries"][0]
        self.assertEqual(native_entry["review"]["state"], "draft")
        self.assertFalse((self.root / "pronunciation_registry.json").exists())
        self.assertEqual((self.root / "chunks.json").read_bytes(), before_chunks)

        with patch.object(
            app_module.project_manager,
            "load_chunks",
            return_value=chunks,
        ):
            preview = self.client.post(
                "/api/pronunciation-registry/preview",
                json={
                    "chunk_index": 0,
                    "candidate_entry": native_entry,
                    "generate_audio": False,
                },
            )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["synthesis_text"], "SKA-roh was silent.")
        self.assertFalse((self.root / "pronunciation_registry.json").exists())

    def test_registry_lists_every_safe_task_without_handoff_ui_fields(self) -> None:
        response = self.client.get("/api/tasks/registry")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        tasks = {item["task_type"]: item for item in payload["tasks"]}
        self.assertEqual(payload["schema_version"], 2)
        self.assertIn("complete_cast_dossier", tasks)
        self.assertIn("persona_catalog_generation", tasks)
        self.assertIn("persona_audit", tasks)
        self.assertIn("visual_reconciliation", tasks)
        self.assertIn("persistent_voice_description_generation", tasks)
        self.assertIn("line_direction_audit", tasks)
        self.assertEqual(
            tasks["persistent_voice_description_generation"][
                "native_destination"
            ],
            "expressive_voices",
        )
        serialized = json.dumps(payload)
        self.assertNotIn("handoff_id", serialized)
        self.assertNotIn("short_code", serialized)

    def test_task_library_route_exposes_states_without_internal_identifiers(self) -> None:
        empty = self.client.get("/api/tasks/library")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["tasks"], [])

        self.export_and_download("script_generation")
        response = self.client.get("/api/tasks/library?q=script&status=awaiting_import")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["tasks"]), 1)
        task = payload["tasks"][0]
        self.assertEqual(task["status"], "awaiting_import")
        self.assertEqual(task["review_destination"], "script_review")
        self.assertTrue(task["download_url"].endswith("/download"))
        serialized = json.dumps(payload)
        self.assertNotIn("handoff_id", serialized)
        self.assertNotIn("manifest_fingerprint", serialized)
        invalid = self.client.get("/api/tasks/library?status=unknown")
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "invalid_task_library_status",
        )

    def test_script_task_export_download_and_import_opens_script_review(self) -> None:
        exported, task_path = self.export_and_download("script_generation")
        self.assertTrue(exported["filename"].endswith(".alexandria-task.zip"))
        inspected = inspect_task_bundle(task_path)
        self.assertEqual(inspected["manifest"]["native_destination"], "script_review")
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result=self.entries,
        )
        result_path = self.root / "completed-script.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        payload = imported.json()
        self.assertEqual(payload["kind"], "annotated_script")
        self.assertEqual(payload["routing"]["status"], "review_ready")
        self.assertEqual(payload["routing"]["tab"], "script")
        self.assertEqual(payload["status"], "inspected")

    def test_complete_cast_bundle_routes_one_result_into_roster_and_voice_reviews(self) -> None:
        self.prepare_approved_roster()
        _, task_path = self.export_and_download(
            "complete_cast_dossier",
            options={
                "roster_and_relationships": True,
                "voice_personas_and_designs": True,
                "visual_dossiers": False,
            },
        )
        inspected = inspect_task_bundle(task_path)
        self.assertEqual(
            inspected["input"]["requested_sections"],
            {
                "roster_and_relationships": True,
                "voice_personas_and_designs": True,
                "visual_dossiers": False,
            },
        )
        self.assertEqual(
            [item["speaker"] for item in inspected["input"]["script_speakers"]],
            ["NARRATOR", "THE DOCTOR"],
        )

        def voice_trait(value: str) -> dict:
            return {
                "value": value,
                "basis": "casting_recommendation",
                "evidence_quotes": [],
            }

        narrator_quote = "The room was quiet."
        doctor_quote = "Doctor"
        narrator_start = self.source.index(narrator_quote)
        doctor_start = self.source.index(doctor_quote)
        result = {
            "selected_sections": {
                "roster_and_relationships": True,
                "voice_personas_and_designs": True,
                "visual_dossiers": False,
            },
            "roster": {
                "entities": [
                    {
                        "identity_seed": "narrator",
                        "canonical_name": "NARRATOR",
                        "display_name": "Narrator",
                        "entity_kind": "narrator_role",
                        "speaking_status": "narrator",
                        "titles": [],
                        "aliases": ["NARRATOR"],
                        "nicknames": [],
                        "pronouns": [],
                        "species": [],
                        "relationships": [],
                        "voice_clues": [],
                        "sample_lines": ["The room was quiet."],
                        "confidence": 0.9,
                        "resolution_status": "resolved",
                        "unresolved_questions": [],
                        "evidence": [
                            {
                                "quote": narrator_quote,
                                "start_char": narrator_start,
                                "end_char": narrator_start + len(narrator_quote),
                                "category": "speaking",
                                "confidence": 0.9,
                                "basis": "inferred",
                            }
                        ],
                    },
                    {
                        "identity_seed": "the-doctor",
                        "canonical_name": "THE DOCTOR",
                        "display_name": "The Doctor",
                        "entity_kind": "character",
                        "speaking_status": "speaker",
                        "titles": ["Doctor"],
                        "aliases": ["THE DOCTOR"],
                        "nicknames": [],
                        "pronouns": [],
                        "species": [],
                        "relationships": ["Present in the room described by the narrator"],
                        "voice_clues": [],
                        "sample_lines": ["Run,"],
                        "confidence": 0.98,
                        "resolution_status": "resolved",
                        "unresolved_questions": [],
                        "evidence": [
                            {
                                "quote": doctor_quote,
                                "start_char": doctor_start,
                                "end_char": doctor_start + len(doctor_quote),
                                "category": "name",
                                "confidence": 1.0,
                                "basis": "explicit",
                            }
                        ],
                    },
                ],
                "warnings": [],
            },
            "voice_dossiers": {
                "voices": [
                    {
                        "speaker": "NARRATOR",
                        "persona_summary": "Steady literary observer.",
                        "designed_voice_description": "A clear neutral literary voice with measured pacing and restrained warmth.",
                        "ref_text": "The room was quiet.",
                        "vocal_age_impression": voice_trait("Adult neutral casting"),
                        "pitch": voice_trait("Mid register"),
                        "weight_and_resonance": voice_trait("Balanced resonance"),
                        "texture_and_timbre": voice_trait("Clear and unobtrusive"),
                        "accent_and_language": voice_trait("Neutral English-language casting"),
                        "cadence_and_rhythm": voice_trait("Measured literary cadence"),
                        "energy_range": voice_trait("Quiet to moderately projected"),
                        "emotional_range": voice_trait("Restrained and observant"),
                        "casting_guidance": voice_trait("Prioritize clarity and continuity"),
                        "uncertainties": [],
                    },
                    {
                        "speaker": "THE DOCTOR",
                        "persona_summary": "Urgent, decisive traveller.",
                        "designed_voice_description": "A clear agile tenor with compact resonance, quick articulation, and controlled urgency.",
                        "ref_text": "Run,",
                        "vocal_age_impression": voice_trait("Adult casting"),
                        "pitch": voice_trait("Mid tenor"),
                        "weight_and_resonance": voice_trait("Compact resonance"),
                        "texture_and_timbre": voice_trait("Clear and lightly bright"),
                        "accent_and_language": voice_trait("Neutral English-language casting"),
                        "cadence_and_rhythm": voice_trait("Quick, precise cadence"),
                        "energy_range": voice_trait("Conversational to urgent command"),
                        "emotional_range": voice_trait("Dry control through alarm"),
                        "casting_guidance": voice_trait("Prioritize intelligence and agility"),
                        "uncertainties": [],
                    },
                ],
                "warnings": [],
            },
            "visual_observations": None,
            "visual_dossiers": None,
            "warnings": [],
        }
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result=result,
        )
        result_path = self.root / "completed-cast-dossier.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        payload = imported.json()
        self.assertEqual(payload["task_type"], "roster_discovery")
        package = payload["cast_dossier_package"]
        self.assertTrue(package["selected_sections"]["voice_personas_and_designs"])
        self.assertEqual(package["summary"]["voice_dossier_count"], 2)
        parent_id = package["parent_candidate_id"]

        status = self.client.get(
            f"/api/character_roster/reconciliation?candidate_id={payload['candidate_id']}"
        )
        self.assertEqual(status.status_code, 200, status.text)
        focused = status.json()["pending_import"]
        issue_decisions = []
        for issue in focused.get("issues") or []:
            allowed = issue.get("allowed_actions") or []
            action = "exclude" if "exclude" in allowed else allowed[0]
            issue_decisions.append(
                {
                    "import_id": issue["import_id"],
                    "action": action,
                    "current_entry_id": None,
                }
            )
        applied = self.client.post(
            "/api/character_roster/reconciliation/apply",
            json={
                "candidate_id": focused["candidate_id"],
                "result_fingerprint": focused["result_fingerprint"],
                "current_kind": focused["current_kind"],
                "current_fingerprint": focused["current_fingerprint"],
                "decisions": issue_decisions,
                "create_designed_voice_profiles": True,
                "discover_visual_details": True,
            },
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertIsNone(applied.json()["enrichment"])
        approval = applied.json()["reconciliation"]["approval"]
        approved = self.client.post(
            "/api/character_roster/reconciliation/approve",
            json={
                "action": (
                    "approve_with_unresolved"
                    if approval["requires_unresolved_acknowledgement"]
                    else "approve_resolved"
                ),
                "draft_fingerprint": approval["draft_fingerprint"],
                "expected_approved_fingerprint": approval[
                    "expected_approved_fingerprint"
                ],
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        roster_fingerprint = approved.json()["approved"]["roster_fingerprint"]
        resumed = self.client.get("/api/character_roster/reconciliation")
        self.assertEqual(resumed.status_code, 200, resumed.text)
        resumed_package = resumed.json()["cast_dossier_package"]
        self.assertEqual(resumed_package["parent_candidate_id"], parent_id)
        self.assertTrue(resumed_package["activation"]["ready"])
        activated = self.client.post(
            f"/api/cast-dossier/{parent_id}/activate",
            json={
                "expected_roster_fingerprint": roster_fingerprint,
                "import_voice_dossiers": True,
                "import_visual_dossiers": False,
            },
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        applications = activated.json()["package"]["applications"]
        self.assertIn("voice_dossiers", applications)
        self.assertNotIn("visual_dossiers", applications)
        completed_status = self.client.get(
            "/api/character_roster/reconciliation"
        )
        self.assertEqual(completed_status.status_code, 200)
        completed_activation = completed_status.json()[
            "cast_dossier_package"
        ]["activation"]
        self.assertTrue(completed_activation["completed"])
        self.assertFalse(completed_activation["ready"])
        dossier = json.loads(
            (self.root / "cast_voice_dossiers.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(dossier["voices"]), 2)
        self.assertEqual(
            {item["speaker"] for item in dossier["voices"]},
            {"NARRATOR", "THE DOCTOR"},
        )
        projects = list(
            (self.root / "voice_training_projects").rglob("project.json")
        )
        self.assertEqual(len(projects), 2)

    def test_voice_only_complete_cast_bundle_activates_without_roster_replacement(self) -> None:
        approved = self.prepare_approved_roster()
        _, task_path = self.export_and_download(
            "complete_cast_dossier",
            options={
                "roster_and_relationships": False,
                "voice_personas_and_designs": True,
                "visual_dossiers": False,
            },
        )

        def trait(value: str) -> dict:
            return {
                "value": value,
                "basis": "casting_recommendation",
                "evidence_quotes": [],
            }

        result = {
            "selected_sections": {
                "roster_and_relationships": False,
                "voice_personas_and_designs": True,
                "visual_dossiers": False,
            },
            "roster": None,
            "voice_dossiers": {
                "voices": [
                    {
                        "speaker": "NARRATOR",
                        "persona_summary": "Steady literary observer.",
                        "designed_voice_description": "A clear neutral literary voice with measured pacing.",
                        "ref_text": "The room was quiet.",
                        "vocal_age_impression": trait("Adult neutral casting"),
                        "pitch": trait("Mid register"),
                        "weight_and_resonance": trait("Balanced resonance"),
                        "texture_and_timbre": trait("Clear and unobtrusive"),
                        "accent_and_language": trait("Neutral English-language casting"),
                        "cadence_and_rhythm": trait("Measured literary cadence"),
                        "energy_range": trait("Quiet to moderately projected"),
                        "emotional_range": trait("Restrained and observant"),
                        "casting_guidance": trait("Prioritize clarity and continuity"),
                        "uncertainties": [],
                    },
                    {
                        "speaker": "THE DOCTOR",
                        "persona_summary": "Urgent, decisive traveller.",
                        "designed_voice_description": "A clear agile tenor with quick articulation and controlled urgency.",
                        "ref_text": "Run,",
                        "vocal_age_impression": trait("Adult casting"),
                        "pitch": trait("Mid tenor"),
                        "weight_and_resonance": trait("Compact resonance"),
                        "texture_and_timbre": trait("Clear and lightly bright"),
                        "accent_and_language": trait("Neutral English-language casting"),
                        "cadence_and_rhythm": trait("Quick, precise cadence"),
                        "energy_range": trait("Conversational to urgent command"),
                        "emotional_range": trait("Dry control through alarm"),
                        "casting_guidance": trait("Prioritize intelligence and agility"),
                        "uncertainties": [],
                    },
                ],
                "warnings": [],
            },
            "visual_observations": None,
            "visual_dossiers": None,
            "warnings": [],
        }
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result=result,
        )
        result_path = self.root / "completed-voice-only-cast.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        payload = imported.json()
        self.assertEqual(payload["task_type"], "complete_cast_dossier")
        package = payload["cast_dossier_package"]
        self.assertTrue(package["activation"]["ready"])
        self.assertEqual(
            package["activation"]["approved_roster_fingerprint"],
            approved["roster_fingerprint"],
        )
        activated = self.client.post(
            f"/api/cast-dossier/{package['parent_candidate_id']}/activate",
            json={
                "expected_roster_fingerprint": approved["roster_fingerprint"],
                "import_voice_dossiers": True,
                "import_visual_dossiers": False,
            },
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertIn(
            "voice_dossiers",
            activated.json()["package"]["applications"],
        )
        self.assertFalse((self.root / "character_roster.draft.json").exists())

    def test_roster_task_import_persists_actionable_reconciliation_without_native_write(self) -> None:
        _, task_path = self.export_and_download("roster_discovery")
        quote = "The room was quiet."
        start = self.source.index(quote)
        result = {
            "entities": [
                {
                    "identity_seed": "speaker:narrator",
                    "canonical_name": "Narrator",
                    "display_name": "Narrator",
                    "entity_kind": "narrator_role",
                    "speaking_status": "narrator",
                    "titles": [],
                    "aliases": ["NARRATOR"],
                    "nicknames": [],
                    "pronouns": [],
                    "species": [],
                    "relationships": [],
                    "voice_clues": [],
                    "sample_lines": [],
                    "confidence": 0.95,
                    "resolution_status": "resolved",
                    "unresolved_questions": [],
                    "evidence": [
                        {
                            "quote": quote,
                            "start_char": start,
                            "end_char": start + len(quote),
                            "category": "other",
                            "confidence": 1.0,
                            "basis": "explicit",
                        }
                    ],
                }
            ],
            "warnings": [],
        }
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result=result,
        )
        result_path = self.root / "completed-roster.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")

        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )

        self.assertEqual(imported.status_code, 200, imported.text)
        payload = imported.json()
        self.assertEqual(payload["status"], "inspected")
        self.assertEqual(payload["routing"]["status"], "awaiting_reconciliation")
        self.assertEqual(payload["routing"]["tab"], "characters")
        self.assertEqual(
            payload["routing"]["code"],
            "roster_import_reconciliation_required",
        )
        self.assertEqual(
            payload["reconciliation"]["summary"]["imported_observations"],
            1,
        )
        self.assertFalse((self.root / "character_roster_state.json").exists())
        self.assertFalse((self.root / "character_roster.draft.json").exists())
        self.assertFalse((self.root / "character_roster.json").exists())

        reopened = self.client.get(
            "/api/character_roster/import-reconciliation"
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        comparison = reopened.json()
        self.assertEqual(comparison["status"], "pending")
        self.assertEqual(comparison["candidate_id"], payload["candidate_id"])
        self.assertEqual(len(comparison["observations"]), 1)
        self.assertEqual(
            comparison["observations"][0]["display_name"],
            "Narrator",
        )
        self.assertEqual(
            comparison["observations"][0]["native_semantic_status"],
            "invalid",
        )
        self.assertEqual(
            comparison["observations"][0]["proposed_action"],
            "unresolved",
        )

        legacy_transfer = self.client.post(
            f"/api/external/structured-result/{payload['candidate_id']}/transfer",
            json={"result_fingerprint": payload["result_fingerprint"]},
        )
        self.assertEqual(legacy_transfer.status_code, 200, legacy_transfer.text)
        self.assertEqual(
            legacy_transfer.json()["routing"]["status"],
            "awaiting_reconciliation",
        )
        self.assertFalse((self.root / "character_roster_state.json").exists())
        self.assertFalse((self.root / "character_roster.draft.json").exists())

    def test_bulk_persona_task_exports_all_speakers_and_routes_all_drafts(self) -> None:
        self.prepare_approved_roster()
        _, task_path = self.export_and_download(
            "persona_catalog_generation"
        )
        inspected = inspect_task_bundle(task_path)
        manifest = inspected["manifest"]
        self.assertIsNone(manifest["target"])
        self.assertEqual(manifest["contract"], "persona_catalog")
        speakers = inspected["input"]["speakers"]
        self.assertEqual(
            [item["speaker"] for item in speakers],
            ["NARRATOR", "THE DOCTOR"],
        )
        self.assertEqual(
            len([item for item in speakers if item["speaker"] == "NARRATOR"]),
            1,
        )
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result={
                "personas": [
                    {
                        "speaker": "NARRATOR",
                        "description": "A steady neutral literary voice.",
                        "ref_text": "The room was quiet.",
                    },
                    {
                        "speaker": "THE DOCTOR",
                        "description": "A clear, lightly nasal tenor.",
                        "ref_text": "Run,",
                    },
                ],
                "warnings": [],
            },
        )
        result_path = self.root / "completed-persona-catalog.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        payload = imported.json()
        self.assertEqual(payload["task_type"], "persona_catalog_generation")
        self.assertEqual(payload["routing"]["status"], "review_ready")
        self.assertEqual(payload["application"]["persona_count"], 2)
        project_files = list(
            (self.root / "voice_training_projects").rglob("project.json")
        )
        self.assertEqual(len(project_files), 2)
        for project_path in project_files:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            self.assertEqual(
                project["desired_base_persona"]["approval_status"],
                "draft",
            )

    def test_bulk_persona_conflicts_compare_and_replace_selected_speakers(self) -> None:
        self.prepare_approved_roster()
        _, single_path = self.export_and_download(
            "persona_generation",
            "THE DOCTOR",
        )
        single_envelope = create_result_envelope(
            task_bundle_path=single_path,
            result={
                "description": "Current Doctor profile.",
                "ref_text": "Run,",
            },
        )
        single_result = self.root / "single-persona.json"
        single_result.write_text(json.dumps(single_envelope), encoding="utf-8")
        first_import = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    single_result.name,
                    single_result.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(first_import.status_code, 200, first_import.text)

        _, catalog_path = self.export_and_download(
            "persona_catalog_generation"
        )
        catalog_envelope = create_result_envelope(
            task_bundle_path=catalog_path,
            result={
                "personas": [
                    {
                        "speaker": "NARRATOR",
                        "description": "Imported narrator profile.",
                        "ref_text": "The room was quiet.",
                    },
                    {
                        "speaker": "THE DOCTOR",
                        "description": "Imported Doctor profile.",
                        "ref_text": "Run,",
                    },
                ],
                "warnings": [],
            },
        )
        catalog_result = self.root / "catalog-personas.json"
        catalog_result.write_text(json.dumps(catalog_envelope), encoding="utf-8")
        compared = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    catalog_result.name,
                    catalog_result.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(compared.status_code, 200, compared.text)
        comparison = compared.json()
        self.assertEqual(
            comparison["routing"]["code"],
            "persona_catalog_comparison_required",
        )
        conflicts = comparison["routing"]["details"]["conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["speaker"], "THE DOCTOR")
        self.assertEqual(
            conflicts[0]["current"]["description"],
            "Current Doctor profile.",
        )
        self.assertEqual(
            conflicts[0]["imported"]["description"],
            "Imported Doctor profile.",
        )
        applied = self.client.post(
            f"/api/external/structured-result/{comparison['candidate_id']}/transfer",
            json={
                "result_fingerprint": comparison["result_fingerprint"],
                "persona_catalog_decision": True,
                "replace_persona_speakers": ["THE DOCTOR"],
            },
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        application = applied.json()["application"]
        self.assertEqual(application["created_count"], 1)
        self.assertEqual(application["replaced_count"], 1)
        self.assertEqual(application["kept_count"], 0)
        doctor_project = next(
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.root / "voice_training_projects").rglob("project.json")
            if json.loads(path.read_text(encoding="utf-8"))["character"]["canonical_name"]
            == "THE DOCTOR"
        )
        self.assertEqual(
            doctor_project["desired_base_persona"]["description"],
            "Imported Doctor profile.",
        )
        self.assertEqual(
            doctor_project["desired_base_persona"]["approval_status"],
            "draft",
        )

    def test_persona_task_requires_approved_roster_before_export(self) -> None:
        response = self.client.post(
            "/api/tasks/export",
            json={
                "task_type": "persona_generation",
                "target": "THE DOCTOR",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "external_approved_roster_required",
        )
        self.assertFalse(
            (self.root / "voice_training_projects").exists()
        )

    def test_persona_task_contains_guidance_and_routes_to_draft_review(self) -> None:
        self.prepare_approved_roster()
        _, task_path = self.export_and_download(
            "persona_generation",
            "THE DOCTOR",
        )
        inspected = inspect_task_bundle(task_path)
        manifest = inspected["manifest"]
        self.assertEqual(manifest["target"]["value"], "THE DOCTOR")
        self.assertEqual(manifest["guidance"]["profile"], "persona")
        with zipfile.ZipFile(task_path) as archive:
            guidance = archive.read("guidance/task-guidance.md").decode("utf-8")
        self.assertIn("stable and acoustic", guidance)
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": "Run,",
            },
        )
        result_path = self.root / "completed-persona.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        payload = imported.json()
        self.assertEqual(payload["task_type"], "persona_generation")
        self.assertEqual(payload["status"], "transferred")
        self.assertEqual(payload["routing"]["status"], "review_ready")
        self.assertEqual(payload["routing"]["tab"], "voice-projects")
        self.assertEqual(
            payload["application"]["destination"],
            "expressive_voices",
        )
        project_files = list(
            (self.root / "voice_training_projects").rglob("project.json")
        )
        self.assertEqual(len(project_files), 1)
        project = json.loads(project_files[0].read_text(encoding="utf-8"))
        self.assertEqual(
            project["desired_base_persona"]["approval_status"],
            "draft",
        )

    def test_json_from_unknown_library_requests_original_zip_not_code(self) -> None:
        self.prepare_approved_roster()
        _, task_path = self.export_and_download(
            "persona_generation",
            "THE DOCTOR",
        )
        envelope = create_result_envelope(
            task_bundle_path=task_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": "Run,",
            },
        )
        result_path = self.root / "completed-persona.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        task_library = self.root / "external_workflows" / "tasks"
        if task_library.exists():
            import shutil

            shutil.rmtree(task_library)
        missing = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                )
            },
        )
        self.assertEqual(missing.status_code, 409, missing.text)
        self.assertEqual(missing.json()["detail"]["code"], "original_task_required")
        self.assertNotIn("code or reference", missing.json()["detail"]["message"].lower())
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    result_path.name,
                    result_path.read_bytes(),
                    "application/json",
                ),
                "original_task": (
                    task_path.name,
                    task_path.read_bytes(),
                    "application/zip",
                ),
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["task_type"], "persona_generation")

    def test_completed_zip_is_self_contained(self) -> None:
        self.prepare_approved_roster()
        _, task_path = self.export_and_download(
            "persona_generation",
            "THE DOCTOR",
        )
        from task_bundles import create_completed_task_bundle

        completed_path = self.root / "completed.alexandria-completed-task.zip"
        create_completed_task_bundle(
            task_bundle_path=task_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": "Run,",
            },
            output_path=completed_path,
        )
        task_library = self.root / "external_workflows" / "tasks"
        if task_library.exists():
            import shutil

            shutil.rmtree(task_library)
        imported = self.client.post(
            "/api/tasks/import",
            files={
                "file": (
                    completed_path.name,
                    completed_path.read_bytes(),
                    "application/zip",
                )
            },
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["task_type"], "persona_generation")


if __name__ == "__main__":
    unittest.main()
