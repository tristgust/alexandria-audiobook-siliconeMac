from __future__ import annotations

import unittest
from pathlib import Path


class VoiceTrainingInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        static = Path(__file__).resolve().parents[1] / "app" / "static"
        cls.html = (static / "index.html").read_text(encoding="utf-8")
        cls.reference_bank_ui = (
            static / "reference_bank_ui.js"
        ).read_text(encoding="utf-8")

    def test_characters_stage_uses_one_roster_led_master_detail_workspace(self) -> None:
        markup_start = self.html.index("<!-- Characters Stage -->")
        markup_end = self.html.index("<!-- Speaker Management Tool -->", markup_start)
        script_start = self.html.index("function characterIdentitySectionHtml")
        script_end = self.html.index("// --- Voice Designer ---", script_start)
        block = self.html[markup_start:markup_end] + self.html[script_start:script_end]
        for phrase in (
            "One character list holds the production voice, appearance, identity, references, and advanced preparation",
            "Imported characters need review",
            "Resolved speaking character",
            "No individual approval needed",
            "Character identity",
            "All canonical roster information for this character",
            "Appearance",
            "Character details",
            "<h4>Voice</h4>",
            "Reference and training identity",
            "More voice tools",
            "character-production-voice-slot",
            "script_voice_name",
            "data-character-visual-inline-action",
        ):
            self.assertIn(phrase, block)
        for rejected in (
            "Script required",
            "Script speakers",
            "No script speakers available",
            "Script speaker · No preparation project",
            "<h2>Voice profiles & preparation</h2>",
            "<h2>Voice casting</h2>",
            "<h4>Voice persona</h4>",
            "<h4>Production voice</h4>",
            "Create voice persona",
        ):
            self.assertNotIn(rejected, block)

    def test_character_list_filters_and_selection_persistence_are_native(self) -> None:
        for phrase in (
            'id="voice-projects-filter"',
            '<option value="needs-review">Needs review</option>',
            '<option value="missing-voice">Missing production voice</option>',
            '<option value="visual-missing">Missing visual dossier</option>',
            '<option value="preparation">Expressive preparation active</option>',
            "function characterMatchesWorkspaceFilter",
            "function characterNeedsWorkspaceReview",
            "function rememberedCharacterSelection",
            "function rememberCharacterSelection",
            "window.localStorage?.setItem",
            "rememberCharacterSelection(characterId);",
        ):
            self.assertIn(phrase, self.html)

    def test_list_readiness_prioritizes_production_voice(self) -> None:
        presentation_start = self.html.index(
            "function characterProductionVoiceState"
        )
        presentation_end = self.html.index(
            "function activeCharacterRoster",
            presentation_start,
        )
        block = self.html[presentation_start:presentation_end]
        for phrase in (
            "Voice configured",
            "Voice missing",
            "Voice link needed",
            "Assign production voice",
            "persona_pending !== true",
        ):
            self.assertIn(phrase, block)
        self.assertNotIn("label: 'Not started'", block)

    def test_routine_identity_management_lives_in_selected_character(self) -> None:
        start = self.html.index(
            "function characterRoutineManagementSectionHtml"
        )
        end = self.html.index(
            "function characterDraftDuplicateCandidates",
            start,
        )
        block = self.html[start:end]
        for phrase in (
            "character-management-body",
            "Identity and production label",
            "character-manage-name",
            "character-manage-display",
            "data-character-management-action=\"rename\"",
            "data-character-management-action=\"add-alias\"",
            "data-character-remove-alias",
            "Exact Script lines",
            "Open advanced identity operations",
        ):
            self.assertIn(phrase, block)
        for destructive in (
            "data-speaker-action=\"merge\"",
            "data-speaker-action=\"split\"",
            "data-speaker-action=\"reassign\"",
        ):
            self.assertNotIn(destructive, block)
        self.assertIn("function runCharacterSpeakerOperation", self.html)
        self.assertIn("characterSpeakerManagementStatus", self.html)
        self.assertIn("'/api/speaker_management/status'", self.html)

    def test_specialist_tools_preserve_character_context_and_return(self) -> None:
        self.assertEqual(
            self.html.count('data-character-tool-context="'),
            5,
        )
        for phrase in (
            "function openCharacterTool",
            "function returnToCharacterContext",
            "function renderCharacterToolContext",
            "alexandria.characterToolContext",
            "window.sessionStorage?.setItem",
            "More voice tools",
            "function characterSpecialistActionsHtml",
            "openCharacterTool('designer')",
            "openCharacterTool('preparer')",
            "openCharacterTool('dataset-builder')",
            "openCharacterTool('training')",
            "Return to character",
            "Script label:",
            "function characterToolContextFromEntry",
            "async function hydrateCharacterToolContext",
            "speakerManagementEntryById(characterId)",
            "route.context.tool === 'advanced-character-operations'",
            "characterToolContextFromEntry(\n                            'speaker-management'",
            "CHARACTER_TOOL_MODES",
            "source: route.context.source",
            "design-voice-name",
            "prep-output",
        ):
            self.assertIn(phrase, self.html)
        self.assertNotIn(
            "save it for use in the Voices stage",
            self.html,
        )

    def test_supplied_recording_is_presented_as_identity_authority(self) -> None:
        for phrase in (
            "Start from the user-owned reference recording and its exact transcript.",
            "Bind the owned reference recording, exact transcript, and audio fingerprint.",
            "Review same-speaker references",
            "Supplied audio and its exact transcript remain authoritative for clones.",
            "Owned audio is never replaced by a generated imitation.",
        ):
            self.assertIn(phrase, self.html)

    def test_rejected_voicedesign_identity_copy_does_not_return(self) -> None:
        self.assertNotIn(
            "Approve one detailed VoiceDesign persona and a stable seed.",
            self.html,
        )
        self.assertNotIn(
            "Prepare one approved voice identity and a reviewed range of emotional references.",
            self.html,
        )

    def test_characters_is_the_primary_handoff_and_training_stays_secondary(self) -> None:
        self.assertIn(
            ">Open Characters</button>",
            self.html,
        )
        self.assertIn(
            'id="lora-training-controls" disabled',
            self.html,
        )
        self.assertIn('data-tab="characters" data-route="cast"', self.html)
        self.assertNotIn('data-route="voice-casting"', self.html)
        self.assertNotIn('data-tab="voice-projects">Voice profiles', self.html)

    def test_adapter_experiment_remains_progressively_disclosed(self) -> None:
        self.assertIn(
            'id="lora-experimental-panel"',
            self.html,
        )
        self.assertIn(
            "Experimental adapter training",
            self.html,
        )

    def test_measurement_states_do_not_render_as_table_rows(self) -> None:
        self.assertIn(
            'id="voice-capability-measurements-state"',
            self.html,
        )
        self.assertIn(
            'id="voice-capability-measurements-table" class="table-responsive" hidden',
            self.html,
        )
        self.assertNotIn(
            '<tr><td colspan="4" class="text-muted">Loading measured results',
            self.html,
        )
        self.assertNotIn(
            '<tr><td colspan="4" class="text-muted">No measured inference results',
            self.html,
        )

    def test_controlled_clone_measurements_are_rendered_as_real_capability(self) -> None:
        self.assertIn("controlled_clone_neutral: 'Controlled clone — neutral'", self.html)
        self.assertIn("controlled_clone_expressive: 'Controlled clone — expressive'", self.html)
        self.assertIn("Controlled supplied-clip clone", self.html)
        self.assertIn("speaker_cosine_to_reference", self.html)
        self.assertIn("<th>Identity</th>", self.html)

    def test_reference_bank_ui_is_native_to_the_selected_character(self) -> None:
        self.assertIn('id="voice-reference-bank-section"', self.html)
        self.assertIn(
            '<script src="/static/reference_bank_ui.js"></script>',
            self.html,
        )
        for phrase in (
            "Create reference bank",
            "Generate controlled variant",
            "Save listening review",
            "Fixed listening comparison",
            "Direct design comparator",
            "Approve reference bank",
            "Open Cast assignment",
        ):
            self.assertIn(phrase, self.reference_bank_ui)

    def test_reference_bank_ui_keeps_identity_and_assignment_explicit(self) -> None:
        for phrase in (
            "The selected owned clip remains authoritative.",
            "Generated variants remain experimental",
            "The comparator is not an identity candidate.",
            "Production assignment is still separate.",
        ):
            self.assertIn(phrase, self.reference_bank_ui)
        self.assertIn(
            "/api/expressive_reference_banks/status",
            self.reference_bank_ui,
        )
        self.assertIn(
            "window.openCastAssignmentForCharacter?.()",
            self.reference_bank_ui,
        )
        self.assertNotIn(
            "/api/expressive_reference_banks/${encodeURIComponent(window.voiceTrainingSelectedId)}/assign",
            self.reference_bank_ui,
        )
        self.assertNotIn('data-reference-bank-action="assign"', self.reference_bank_ui)
        self.assertNotIn('data-reference-bank-action="unassign"', self.reference_bank_ui)

    def test_voice_lab_modes_state_non_assignment_authority(self) -> None:
        for phrase in (
            "Voice Lab · Design",
            "Voice Lab · Prepare",
            "Voice Lab · Dataset",
            "Voice Lab · Train",
            "Voice Lab prepares reusable Voice material only.",
            "Prepared audio and datasets remain candidates until reviewed.",
            "Dataset projects prepare reviewed training material only.",
            "Training, validation, and installation do not change the production Voice.",
            "window.openCastAssignmentForCharacter = openCastAssignmentForCharacter",
            "const exactReturnRoute = currentWorkspaceRoute?.context?.return",
            "const routeMatchesTool = route.destination !== 'more'",
        ):
            self.assertIn(phrase, self.html)

    def test_dataset_list_has_deliberate_states_and_named_actions(self) -> None:
        self.assertIn("No reviewed datasets", self.html)
        self.assertIn("Datasets could not be loaded", self.html)
        self.assertIn('class="training-dataset-row"', self.html)
        self.assertIn('aria-label="Delete ${escapeHtml(d.dataset_id)}"', self.html)
        self.assertNotIn(
            '<span class="text-muted">No datasets uploaded',
            self.html,
        )


if __name__ == "__main__":
    unittest.main()
