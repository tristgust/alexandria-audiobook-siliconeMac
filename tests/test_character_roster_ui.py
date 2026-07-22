from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"


class CharacterRosterUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    def test_roster_controls_exist_once(self) -> None:
        for element_id in (
            "character-workspace",
            "character-roster-status-badge",
            "btn-refresh-character-roster",
            "btn-discover-character-roster",
            "btn-cancel-character-roster",
            "btn-discard-character-roster-progress",
            "btn-rollback-character-roster",
            "character-roster-summary",
            "character-roster-progress",
            "character-roster-source",
            "character-roster-counts",
            "character-roster-content",
            "character-roster-approval",
            "character-roster-unresolved-ack",
            "btn-approve-character-roster",
            "rosterImportModal",
            "btn-review-roster-import",
            "roster-import-summary",
            "roster-import-search",
            "roster-import-filter",
            "roster-import-list",
            "roster-import-decision-count",
            "btn-apply-roster-import",
        ):
            self.assertEqual(
                self.html.count(f'id="{element_id}"'),
                1,
                element_id,
            )

    def test_roster_and_character_information_share_one_list(self) -> None:
        characters_start = self.html.index('id="character-workspace"')
        characters_end = self.html.index('<!-- Speaker Management Tool -->', characters_start)
        block = self.html[characters_start:characters_end]
        self.assertEqual(block.count('id="voice-projects-list"'), 1)
        self.assertEqual(block.count('id="voice-projects-detail"'), 1)
        self.assertIn("One character list holds the production voice, appearance, identity, references, and advanced preparation", block)
        self.assertIn('id="character-roster-content" hidden', block)
        self.assertNotIn('class="roster-entry"', block)
        for phrase in (
            "No individual approval needed",
            "resolved characters need no individual action",
            "Approve ${entryCount}",
            "characterDraftDetailHtml",
        ):
            self.assertIn(phrase, self.html)

    def test_all_action_and_status_endpoints_are_used(self) -> None:
        for endpoint in (
            "/api/character_roster/status",
            "/api/character_roster/draft",
            "/api/character_roster/draft/action",
            "/api/character_roster/approve",
            "/api/character_roster/rollback",
            "/api/character_roster/discover",
            "/api/character_roster/cancel",
            "/api/character_roster/discard-progress",
            "/api/character_roster/import-reconciliation",
            "/api/character_roster/import-reconciliation/apply",
        ):
            self.assertIn(endpoint, self.html)

    def test_reviewed_replacement_and_exact_undo_are_explicit(self) -> None:
        for phrase in (
            "Replace approved roster",
            "replace_existing: replacing",
            "expected_approved_fingerprint",
            "exact rollback revision",
            "Undo roster replacement",
            "expected_current_fingerprint",
            "revision_history?.latest_available",
        ):
            self.assertIn(phrase, self.html)

    def test_import_reconciliation_is_a_focused_import_modal(self) -> None:
        modal_start = self.html.index('id="rosterImportModal"')
        modal_end = self.html.index('<!-- Confirm modal -->', modal_start)
        block = self.html[modal_start:modal_end]
        for required in (
            "Review imported characters",
            "straightforward identities",
            "All needing review",
            "Conflicting matches",
            "Validation failures",
            "Resolved observations use Alexandria’s safe matches",
            "Review later",
            "Apply character decisions",
            "never approves it",
        ):
            self.assertIn(required, block)
        self.assertNotIn('id="roster-import-reconciliation"', self.html)

    def test_import_reconciliation_renders_only_actionable_observations(self) -> None:
        script_start = self.html.index("function rosterObservationNeedsReview")
        script_end = self.html.index("function renderCharacterRosterLog", script_start)
        block = self.html[script_start:script_end]
        for required in (
            "rosterImportReviewObservations",
            "native_semantic_status === 'invalid'",
            "repaired_evidence_count",
            "currentMatches.length > 1",
            "autoApplySafe",
            "showRosterImportModal",
            "observation.proposed_action",
            "observation.proposed_current_entry_id",
        ):
            self.assertIn(required, block)
        self.assertNotIn(
            "Every imported observation needs a complete reconciliation decision.",
            block,
        )

    def test_initial_hash_navigation_waits_for_api_and_polling_state(self) -> None:
        activation = self.html.rindex(
            "const route = workspaceRouteApi.parseHash(window.location.hash);"
        )
        self.assertGreater(activation, self.html.index("const API ="))
        self.assertGreater(activation, self.html.index("let recoveryPollTimer = null;"))
        self.assertGreater(activation, self.html.index("let prepPoller = null;"))

    def test_upload_refreshes_roster_compatibility(self) -> None:
        upload_start = self.html.index(
            "document.getElementById('file-upload').addEventListener"
        )
        generation_start = self.html.index(
            "document.getElementById('btn-gen-script').addEventListener",
            upload_start,
        )
        upload_block = self.html[upload_start:generation_start]
        self.assertIn(
            "await refreshCharacterRosterStatus();",
            upload_block,
        )

    def test_sensitive_runtime_fields_are_not_rendered(self) -> None:
        roster_block_start = self.html.index(
            "let characterRosterStatusTimer"
        )
        roster_block_end = self.html.index(
            "// --- Script Tab ---",
            roster_block_start,
        )
        block = self.html[roster_block_start:roster_block_end]
        for forbidden in (
            "base_url",
            "api_key",
            "system_prompt",
            "user_prompt",
            "temperature",
            "max_tokens",
            "raw telemetry",
        ):
            self.assertNotIn(forbidden, block)


if __name__ == "__main__":
    unittest.main()
