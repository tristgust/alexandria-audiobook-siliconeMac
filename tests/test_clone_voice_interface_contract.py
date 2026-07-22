from __future__ import annotations

import unittest
from pathlib import Path


class CloneVoiceInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "index.html"
        ).read_text(encoding="utf-8")

    def test_clone_editor_has_explicit_identity_fields(self) -> None:
        for phrase in (
            "Reference source",
            "Exact reference transcript",
            "Persistent identity note",
            "Reference audio",
            "Identity authority",
            "The supplied recording and exact transcript define this voice.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_clone_editor_preserves_backend_and_identity_note(self) -> None:
        self.assertIn(
            'data-saved-clone-backend="${escapeHtml(config.clone_backend || \'qwen3_base\')}"',
            self.source,
        )
        self.assertIn(
            "character_style: card.querySelector('.clone-character-style')?.value || ''",
            self.source,
        )
        self.assertIn(
            "clone_backend: card.dataset.savedCloneBackend || 'qwen3_base'",
            self.source,
        )

    def test_reference_changes_invalidate_controlled_clone_approval(self) -> None:
        self.assertIn(
            "function invalidateControlledClonePreview(target)",
            self.source,
        )
        self.assertIn(
            "card.dataset.savedCloneBackend = 'qwen3_base'",
            self.source,
        )
        self.assertIn(
            "delete card.dataset.controlledPreviewFingerprint",
            self.source,
        )
        self.assertIn(
            "delete card.dataset.controlledConfigurationFingerprint",
            self.source,
        )
        self.assertIn(
            "delete card.dataset.controlledCloneApprovalToken",
            self.source,
        )
        self.assertIn(
            "delete card.dataset.controlledPreviewPlayed",
            self.source,
        )
        self.assertIn(
            "The bound reference or preview state changed. Alexandria is using the standard clone",
            self.source,
        )

    def test_controlled_clone_requires_preview_and_listen_confirmation(self) -> None:
        for phrase in (
            "/api/clone_voices/controlled_preview",
            "speaker: card.dataset.voice",
            "result.requires_listen_confirmation !== true",
            "result.configuration_fingerprint",
            "audio.addEventListener('play'",
            "card.dataset.controlledPreviewPlayed = 'true'",
            "audio.addEventListener('ended'",
            "markControlledClonePreviewListened(audio)",
            "card.dataset.controlledPreviewPlayed !== 'true'",
            "/api/clone_voices/controlled_preview/confirm",
            "confirmation.approval_token",
            "card.dataset.controlledCloneApprovalToken",
            "card.dataset.controlledPreviewListened !== 'true'",
            "Generate and finish listening to the matching preview first.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_voice_tab_loads_capabilities_before_rendering_clone_controls(self) -> None:
        for phrase in (
            "async function loadVoices(options = {})",
            "if (!window._voiceBackendCapabilities)",
            "await loadVoiceBackendCapabilities();",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_controlled_clone_settings_are_saved_with_backend(self) -> None:
        for phrase in (
            "instruction_clone_temperature:",
            "instruction_clone_top_k:",
            "instruction_clone_top_p:",
            "instruction_clone_repetition_penalty:",
            "instruction_clone_max_tokens:",
            "controlled_clone_approval_token:",
            "card.dataset.savedCloneBackend = 'qwen3_instruction_controlled'",
            "await saveVoicesNow(card)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_clone_form_does_not_use_placeholder_only_inputs(self) -> None:
        for label in (
            'for="clone-source-${index}"',
            'for="clone-ref-text-${index}"',
            'for="clone-character-style-${index}"',
            'for="clone-ref-audio-${index}"',
        ):
            with self.subTest(label=label):
                self.assertIn(label, self.source)

    def test_moved_voice_cards_keep_change_events_and_use_partial_cast_save(self) -> None:
        self.assertIn("document.addEventListener('change'", self.source)
        self.assertIn("document.addEventListener('input'", self.source)
        self.assertIn("event.target.closest?.('.voice-card')", self.source)
        self.assertNotIn(
            "document.getElementById('voices-list').addEventListener('change'",
            self.source,
        )
        self.assertIn("function collectVoiceConfigForCard(card)", self.source)
        self.assertIn("async function saveCastVoiceCard()", self.source)
        self.assertIn("[name]: collectVoiceConfigForCard(card)", self.source)
        self.assertIn("window.AlexandriaVoiceCardBridge", self.source)
        self.assertIn("markCastVoiceEditorDirty(card)", self.source)

    def test_voice_editor_preserves_saved_seed_and_previews_with_it(self) -> None:
        self.assertIn('data-saved-seed="${escapeHtml(String(config.seed ?? \'-1\'))}"', self.source)
        self.assertIn("const savedSeed = card.dataset.savedSeed || '-1';", self.source)
        self.assertIn("seed: savedSeed", self.source)
        self.assertIn("const seed = Number.parseInt(card.dataset.savedSeed || '-1', 10);", self.source)
        self.assertIn("seed: Number.isFinite(seed) ? seed : -1", self.source)
        self.assertIn("card.dataset.savedSeed = String(result.settings.seed ?? -1);", self.source)
        self.assertIn("instruction_clone_temperature", self.source)
        self.assertIn("instruction_clone_repetition_penalty", self.source)


if __name__ == "__main__":
    unittest.main()
