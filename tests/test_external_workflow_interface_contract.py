from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"
APP_PATH = ROOT / "app" / "app.py"
SERVICE_PATH = ROOT / "app" / "external_workflows.py"
TASK_SERVICE_PATH = ROOT / "app" / "task_bundles.py"


class ExternalWorkflowInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.app = APP_PATH.read_text(encoding="utf-8")
        cls.service = SERVICE_PATH.read_text(encoding="utf-8")
        cls.task_service = TASK_SERVICE_PATH.read_text(encoding="utf-8")

    def test_task_bundle_workflow_is_secondary_to_script_controls(self):
        review_index = self.html.index(
            'class="utility-disclosure script-review-disclosure"'
        )
        workflow_index = self.html.index('id="script-external-workflow"')
        roster_index = self.html.index('class="script-character-handoff"')
        import_index = self.html.index('id="script-import-workflow"')
        self.assertLess(roster_index, review_index)
        self.assertLess(review_index, workflow_index)
        self.assertLess(workflow_index, import_index)
        opening_tag = re.search(
            r'<details[^>]+id="script-external-workflow"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(opening_tag)
        self.assertNotIn(" open", opening_tag.group(0))

    def test_task_bundle_controls_exist_once_and_obsolete_controls_are_gone(self):
        controls = (
            "script-external-workflow",
            "external-workflow-status",
            "task-bundle-task",
            "task-bundle-target-field",
            "task-bundle-target-label",
            "task-bundle-target",
            "task-bundle-selection-summary",
            "btn-export-task-bundle",
            "completed-task-file",
            "original-task-file-wrap",
            "original-task-file",
            "btn-import-completed-task",
            "task-bundle-import-note",
            "external-structured-result",
            "external-structured-result-status",
            "external-structured-result-destination",
            "external-structured-result-target",
            "external-structured-result-note",
            "external-structured-result-json",
            "btn-open-structured-destination",
            "btn-copy-structured-result",
            "btn-dismiss-structured-result",
            "persona-catalog-conflicts",
            "persona-catalog-conflict-list",
            "btn-apply-persona-catalog",
            "character-roster-voice-profiles",
            "character-roster-voice-profile-status",
            "btn-export-roster-voice-profiles",
        )
        for control_id in controls:
            with self.subTest(control_id=control_id):
                self.assertEqual(self.html.count(f'id="{control_id}"'), 1)
        obsolete = (
            "external-handoff-id",
            "btn-copy-external-handoff-id",
            "btn-copy-chatgpt-prompt",
            "btn-open-chatgpt-folder",
            "btn-export-chatgpt-generation",
            "btn-export-chatgpt-review",
            "external-stage-task",
            "btn-export-chatgpt-stage",
            "external-chatgpt-result-file",
            "btn-transfer-structured-result",
        )
        for control_id in obsolete:
            with self.subTest(obsolete=control_id):
                self.assertNotIn(f'id="{control_id}"', self.html)
        self.assertNotIn("Other structured tasks", self.html)
        self.assertNotIn("Handoff reference", self.html)

    def test_registry_drives_export_and_target_scope(self):
        registry_function = self.html[
            self.html.index("async function loadTaskBundleRegistry"):
            self.html.index("async function exportTaskBundle")
        ]
        export_function = self.html[
            self.html.index("async function exportTaskBundle"):
            self.html.index("async function routeCompletedTaskResult")
        ]
        target_function = self.html[
            self.html.index("function updateTaskBundleTargetState"):
            self.html.index("async function loadTaskBundleRegistry")
        ]
        self.assertIn("/api/tasks/registry", registry_function)
        self.assertIn("taskBundleRegistry", registry_function)
        self.assertIn("target_kind", target_function)
        self.assertIn("definition.native_destination", target_function)
        self.assertIn("/api/tasks/export", export_function)
        self.assertIn("task_type: definition.task_type", export_function)
        self.assertIn("target: target || null", export_function)
        self.assertNotIn("handoff_id", export_function)

    def test_completed_task_import_accepts_zip_json_and_optional_original_zip(self):
        completed = re.search(
            r'<input[^>]+id="completed-task-file"[^>]*>',
            self.html,
        )
        original = re.search(
            r'<input[^>]+id="original-task-file"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(completed)
        self.assertIsNotNone(original)
        assert completed is not None and original is not None
        self.assertIn('class="file-picker-input"', completed.group(0))
        self.assertIn(".zip,.json", completed.group(0))
        self.assertIn('class="file-picker-input"', original.group(0))
        self.assertIn(".zip", original.group(0))
        import_function = self.html[
            self.html.index("async function importCompletedTask"):
            self.html.index("async function postExternalWorkflowForm")
        ]
        self.assertIn("/api/tasks/import", import_function)
        self.assertIn("formData.append('file', completed)", import_function)
        self.assertIn("formData.append('original_task', original)", import_function)
        self.assertIn("original_task_required", import_function)
        self.assertIn("legacy_task_bundle_required", import_function)
        self.assertNotIn("handoff_id", import_function)

    def test_import_routes_to_native_review_without_approval(self):
        route_function = self.html[
            self.html.index("async function routeCompletedTaskResult"):
            self.html.index("async function importCompletedTask")
        ]
        import_function = self.html[
            self.html.index("async function importCompletedTask"):
            self.html.index("async function postExternalWorkflowForm")
        ]
        self.assertIn("refreshCharactersWorkspace", route_function)
        self.assertIn("refreshCharacterRosterImportReconciliation", route_function)
        self.assertIn("autoApplySafe: true", route_function)
        self.assertIn("open: true", route_function)
        self.assertIn("activateWorkspaceTab('characters'", route_function)
        self.assertIn("renderExternalScriptCandidate", import_function)
        self.assertIn("renderExternalStructuredResult", import_function)
        self.assertIn("Nothing has been approved automatically", self.html)
        self.assertNotIn("/approve", import_function)
        self.assertNotIn("/assign", import_function)

    def test_structured_result_card_has_decision_relevant_state_and_action(self):
        render_function = self.html[
            self.html.index("function renderExternalStructuredResult"):
            self.html.index("function dismissExternalStructuredResult")
        ]
        for state in (
            "review_ready",
            "awaiting_reconciliation",
            "blocked",
            "unsupported",
        ):
            self.assertIn(state, render_function)
        self.assertIn("routing.message", render_function)
        self.assertIn("routing.tab", render_function)
        self.assertIn("Open ${destinationLabel}", render_function)
        self.assertNotIn("Ready to transfer", render_function)
        self.assertNotIn("Manual transfer required", render_function)

    def test_routes_cover_v2_core_and_v1_compatibility(self):
        v2_routes = (
            '@app.get("/api/tasks/registry")',
            '@app.post("/api/tasks/export")',
            '@app.get("/api/tasks/{task_id}/download")',
            '@app.post("/api/tasks/import")',
        )
        for route in v2_routes:
            with self.subTest(route=route):
                self.assertIn(route, self.app)
        compatibility_routes = (
            '@app.post("/api/external/handoff/export")',
            '@app.post("/api/external/handoff/result")',
            '@app.post("/api/external/annotated-script/inspect")',
            '@app.post("/api/external/annotated-script/apply")',
            '@app.post("/api/external/annotated-script/rollback")',
        )
        for route in compatibility_routes:
            with self.subTest(route=route):
                self.assertIn(route, self.app)

    def test_registry_covers_voice_roster_visual_and_line_tasks(self):
        for task_type in (
            "script_generation",
            "script_review",
            "roster_discovery",
            "roster_reconciliation",
            "persona_catalog_generation",
            "persona_generation",
            "persona_refinement",
            "persona_reconciliation",
            "persona_audit",
            "visual_discovery",
            "visual_reconciliation",
            "persistent_voice_description_generation",
            "persistent_voice_description_refinement",
            "persistent_voice_description_audit",
            "line_direction_generation",
            "line_direction_audit",
        ):
            with self.subTest(task_type=task_type):
                self.assertIn(f'"{task_type}"', self.task_service)
        self.assertIn('"expressive_voices"', self.task_service)
        self.assertIn('"visual_dossiers"', self.task_service)
        self.assertIn('"editor"', self.task_service)
        self.assertIn('"persona_catalog"', self.task_service)

    def test_identity_persona_casting_and_preparation_share_characters(self):
        self.assertRegex(
            self.html,
            r'data-route="script"[^>]*>.*?<span>Script</span>',
        )
        self.assertRegex(
            self.html,
            r'data-route="cast"[^>]*>.*?<span>Cast</span>',
        )
        self.assertIn('id="character-workspace"', self.html)
        self.assertIn('Character identity', self.html)
        self.assertIn('<h4>Voice</h4>', self.html)
        self.assertIn('Reference and training identity', self.html)
        self.assertIn('More voice tools', self.html)
        self.assertIn('character-production-voice-slot', self.html)
        self.assertIn('No individual approval needed', self.html)
        self.assertIn('resolved characters need no individual action', self.html)
        self.assertIn('Draft missing advanced identities', self.html)
        self.assertIn("task_type: 'persona_catalog_generation'", self.html)
        self.assertIn('persona_catalog_comparison_required', self.html)
        self.assertIn('Replace this identity draft', self.html)
        self.assertNotIn('2</span><span>Voice casting', self.html)
        self.assertNotIn('<h2>Voice profiles & preparation</h2>', self.html)
        self.assertNotIn('<h4>Voice persona</h4>', self.html)
        self.assertNotIn('<h4>Production voice</h4>', self.html)
        self.assertNotIn('Create voice persona', self.html)

    def test_voice_reference_guidance_is_versioned_and_task_specific(self):
        self.assertIn("GUIDANCE_MANIFEST_PATH", self.task_service)
        self.assertIn("guidance_profile=\"persona\"", self.task_service)
        self.assertIn("guidance_profile=\"voice_identity\"", self.task_service)
        self.assertIn("guidance_profile=\"line_direction\"", self.task_service)
        self.assertIn("NONHUMAN_GUIDANCE_PATH", self.task_service)
        self.assertIn("guidance_fingerprint_mismatch", self.task_service)

    def test_script_import_review_and_apply_remain_separate(self):
        inspect_function = self.html[
            self.html.index("async function inspectExternalAnnotatedScript"):
            self.html.index("async function applyExternalScriptCandidate")
        ]
        apply_function = self.html[
            self.html.index("async function applyExternalScriptCandidate"):
            self.html.index("async function rollbackExternalScriptImport")
        ]
        self.assertIn("renderExternalScriptCandidate(candidate)", inspect_function)
        self.assertNotIn("/api/external/annotated-script/apply", inspect_function)
        self.assertIn("showConfirm(", apply_function)
        self.assertIn("candidate_id: externalScriptCandidate.candidate_id", apply_function)

    def test_layout_is_two_clear_task_actions_not_dashboard_cards(self):
        self.assertIn(".task-bundle-workspace {", self.html)
        self.assertIn(".task-bundle-panel {", self.html)
        self.assertIn("Export task", self.html)
        self.assertIn("Import completed task", self.html)
        self.assertIn("Work with ChatGPT", self.html)
        self.assertIn("Import a script", self.html)
        self.assertNotIn("External structured workflow", self.html)
        self.assertNotIn("external-workflow-card", self.html)


if __name__ == "__main__":
    unittest.main()
