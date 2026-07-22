from __future__ import annotations

import copy
import unittest

from voice_aliases import (
    VoiceAliasError,
    merge_voice_config_updates,
    resolve_voice_alias,
    validate_voice_aliases,
)


class VoiceAliasContractTests(unittest.TestCase):
    def test_resolves_multi_hop_alias_to_independent_voice(self) -> None:
        config = {
            "THE DOCTOR": {
                "type": "clone",
                "ref_audio": "clone_voices/doctor.wav",
            },
            "DOCTOR": {"alias_of": "THE DOCTOR"},
            "SEVENTH DOCTOR": {"alias_of": "DOCTOR"},
        }

        resolution = resolve_voice_alias("SEVENTH DOCTOR", config)

        self.assertEqual(
            resolution.chain,
            ("SEVENTH DOCTOR", "DOCTOR", "THE DOCTOR"),
        )
        self.assertEqual(resolution.resolved_target, "THE DOCTOR")
        self.assertEqual(resolution.resolved_type, "clone")
        self.assertEqual(resolution.resolved_source, "doctor.wav")

    def test_rejects_missing_target(self) -> None:
        with self.assertRaises(VoiceAliasError) as raised:
            resolve_voice_alias(
                "DOCTOR",
                {"DOCTOR": {"alias_of": "THE DOCTOR"}},
            )

        self.assertEqual(raised.exception.code, "alias_target_missing")
        self.assertEqual(raised.exception.target, "THE DOCTOR")

    def test_rejects_self_alias(self) -> None:
        with self.assertRaises(VoiceAliasError) as raised:
            validate_voice_aliases(
                {"DOCTOR": {"alias_of": "DOCTOR"}},
            )

        self.assertEqual(raised.exception.code, "alias_self_reference")
        self.assertEqual(raised.exception.chain, ("DOCTOR", "DOCTOR"))

    def test_rejects_cycle_with_complete_chain(self) -> None:
        with self.assertRaises(VoiceAliasError) as raised:
            validate_voice_aliases(
                {
                    "DOCTOR": {"alias_of": "SEVENTH DOCTOR"},
                    "SEVENTH DOCTOR": {"alias_of": "DOCTOR"},
                }
            )

        self.assertEqual(raised.exception.code, "alias_cycle")
        self.assertEqual(
            raised.exception.chain,
            ("DOCTOR", "SEVENTH DOCTOR", "DOCTOR"),
        )

    def test_alias_update_preserves_dormant_and_unknown_fields(self) -> None:
        current = {
            "THE DOCTOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {
                "type": "clone",
                "ref_audio": "clone_voices/dormant.wav",
                "ref_text": "Dormant transcript.",
                "unknown": {"keep": True},
            },
        }

        candidate, diagnostics = merge_voice_config_updates(
            current,
            {"DOCTOR": {"alias_of": "THE DOCTOR"}},
        )

        self.assertEqual(candidate["DOCTOR"]["type"], "clone")
        self.assertEqual(
            candidate["DOCTOR"]["ref_audio"],
            "clone_voices/dormant.wav",
        )
        self.assertEqual(candidate["DOCTOR"]["unknown"], {"keep": True})
        self.assertEqual(candidate["DOCTOR"]["alias_of"], "THE DOCTOR")
        self.assertEqual(
            diagnostics["DOCTOR"]["resolved_target"],
            "THE DOCTOR",
        )

    def test_clearing_alias_restores_dormant_configuration(self) -> None:
        current = {
            "THE DOCTOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {
                "type": "clone",
                "ref_audio": "clone_voices/dormant.wav",
                "ref_text": "Dormant transcript.",
                "alias_of": "THE DOCTOR",
            },
        }

        candidate, diagnostics = merge_voice_config_updates(
            current,
            {"DOCTOR": {"alias_of": None}},
        )

        self.assertNotIn("alias_of", candidate["DOCTOR"])
        self.assertEqual(candidate["DOCTOR"]["type"], "clone")
        self.assertEqual(
            candidate["DOCTOR"]["ref_audio"],
            "clone_voices/dormant.wav",
        )
        self.assertFalse(diagnostics["DOCTOR"]["is_alias"])
        self.assertEqual(diagnostics["DOCTOR"]["resolved_target"], "DOCTOR")

    def test_clearing_alias_removes_legacy_alias_field(self) -> None:
        current = {
            "THE DOCTOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {
                "type": "custom",
                "voice": "Aiden",
                "alias": "THE DOCTOR",
            },
        }

        candidate, _ = merge_voice_config_updates(
            current,
            {"DOCTOR": {"alias_of": ""}},
        )

        self.assertNotIn("alias", candidate["DOCTOR"])
        self.assertNotIn("alias_of", candidate["DOCTOR"])
        self.assertEqual(candidate["DOCTOR"]["voice"], "Aiden")

    def test_failed_update_does_not_mutate_input(self) -> None:
        current = {
            "DOCTOR": {"type": "custom", "voice": "Ryan"},
            "MASTER": {"type": "custom", "voice": "Aiden"},
        }
        before = copy.deepcopy(current)

        with self.assertRaises(VoiceAliasError):
            merge_voice_config_updates(
                current,
                {
                    "DOCTOR": {"alias_of": "MASTER"},
                    "MASTER": {"alias_of": "DOCTOR"},
                },
            )

        self.assertEqual(current, before)


if __name__ == "__main__":
    unittest.main()
