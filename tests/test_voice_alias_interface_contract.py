from __future__ import annotations

import unittest
from pathlib import Path


class VoiceAliasInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "index.html"
        ).read_text(encoding="utf-8")

    def test_inherited_voice_replaces_active_independent_controls(self) -> None:
        for phrase in (
            'class="voice-alias-inheritance" ${hasAlias ? \'\' : \'hidden\'}',
            'class="voice-independent-config" ${hasAlias ? \'hidden inert\' : \'\'}',
            "This speaker uses the fully resolved target voice.",
            "Its own saved settings remain dormant until the alias is cleared.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_inherited_summary_shows_resolution_diagnostics(self) -> None:
        for marker in (
            "data-alias-resolved-target",
            "data-alias-resolved-type",
            "data-alias-resolved-source",
            "data-alias-chain",
            "data-alias-edit-target",
            "openAliasTarget(this)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_alias_cards_send_only_alias_and_independent_cards_clear_alias(self) -> None:
        self.assertIn(
            "if (alias) return { alias_of: alias };",
            self.source,
        )
        self.assertIn(
            "config[card.dataset.voice] = collectVoiceConfigForCard(card);",
            self.source,
        )
        self.assertGreaterEqual(
            self.source.count("alias_of: null,"),
            5,
        )

    def test_alias_changes_use_immediate_validated_save_and_reload(self) -> None:
        for phrase in (
            "async function handleVoiceAliasChange(select)",
            "collectVoiceConfig()",
            "applyAliasDiagnostics(result.aliases)",
            "await loadVoices();",
            "select.value = previous;",
            "Could not save voice alias:",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_target_edits_propagate_without_copying_configuration(self) -> None:
        for phrase in (
            "function applyAliasDiagnostics(aliases)",
            "resolution.resolved_target",
            "resolution.resolved_type",
            "resolution.resolved_source",
            "editButton.dataset.aliasEditTarget = target",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_missing_legacy_target_remains_visible_for_correction(self) -> None:
        self.assertIn(
            "${escapeHtml(aliasTarget)} (missing)",
            self.source,
        )
        self.assertIn("Alias configuration blocked", self.source)
        self.assertIn("voice-alias-error", self.source)


if __name__ == "__main__":
    unittest.main()
