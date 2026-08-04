from __future__ import annotations

import unittest

from audio_artifacts import audio_binding_fingerprint
from dialogue_continuity import (
    continuity_synthesis_text,
    effective_delivery_instruction,
    effective_pause_after_ms,
    is_attached_dialogue_tag,
    resolve_spoken_continuity,
)


class DialogueContinuityTests(unittest.TestCase):
    def test_comma_split_dialogue_attribution_and_resume_get_distinct_roles(self) -> None:
        chunks = [
            {
                "speaker": "JOAN REDFERN",
                "text": "Well, Mr Shuttleworth,",
                "instruct": "Patient and politely corrective.",
            },
            {
                "speaker": "NARRATOR",
                "text": "Joan began, replacing her own plate delicately on the table,",
                "instruct": "Controlled observational narration.",
            },
            {
                "speaker": "JOAN REDFERN",
                "text": "may we move on to the subject of our talk for today?",
                "instruct": "Courteous but firm.",
            },
        ]

        first = resolve_spoken_continuity(chunks, 0)
        tag = resolve_spoken_continuity(chunks, 1)
        resumed = resolve_spoken_continuity(chunks, 2)

        self.assertEqual(first["role"], "dialogue_open_before_attribution")
        self.assertEqual(first["boundary_after"], "open")
        self.assertEqual(first["suggested_pause_after_ms"], 130)
        self.assertEqual(
            tag["role"],
            "parenthetical_attribution_between_dialogue",
        )
        self.assertEqual(tag["boundary_before"], "attached")
        self.assertEqual(tag["boundary_after"], "open")
        self.assertEqual(tag["suggested_pause_after_ms"], 130)
        self.assertEqual(resumed["role"], "dialogue_resume_after_attribution")
        self.assertEqual(resumed["boundary_before"], "resume")

    def test_exclamation_stays_terminal_but_following_tag_is_attached(self) -> None:
        chunks = [
            {
                "speaker": "THE DOCTOR",
                "text": "Hanky panky!",
                "instruct": "Sudden delighted shout.",
            },
            {
                "speaker": "NARRATOR",
                "text": "shouted the Doctor.",
                "instruct": "Plain narration.",
            },
        ]

        dialogue = resolve_spoken_continuity(chunks, 0)
        tag = resolve_spoken_continuity(chunks, 1)

        self.assertEqual(
            dialogue["role"],
            "dialogue_terminal_before_attribution",
        )
        self.assertEqual(dialogue["boundary_after"], "terminal")
        self.assertIn("full exclamation cadence", dialogue["instruction"])
        self.assertEqual(
            tag["role"],
            "attached_attribution_after_terminal_dialogue",
        )
        self.assertEqual(tag["boundary_before"], "attached_after_terminal")
        self.assertIn("low-reset", tag["instruction"])

    def test_independent_narration_after_question_is_not_misclassified(self) -> None:
        chunks = [
            {
                "speaker": "JOAN REDFERN",
                "text": "May we move on?",
                "instruct": "Firmly.",
            },
            {
                "speaker": "NARRATOR",
                "text": "One of the ladies leaned over to her friend and whispered in her ear.",
                "instruct": "Confidential observation.",
            },
        ]

        self.assertFalse(
            is_attached_dialogue_tag(chunks[1]["text"], "JOAN REDFERN")
        )
        self.assertIsNone(resolve_spoken_continuity(chunks, 0))
        self.assertIsNone(resolve_spoken_continuity(chunks, 1))

    def test_authored_pause_overrides_derived_continuity_pause(self) -> None:
        chunk = {
            "pause_after": 420,
            "spoken_continuity": {"suggested_pause_after_ms": 130},
        }
        self.assertEqual(effective_pause_after_ms(chunk), 420)

    def test_derived_pause_migrates_only_after_continuity_generation(self) -> None:
        legacy = {
            "spoken_continuity": {"suggested_pause_after_ms": 130},
        }
        generating = {
            **legacy,
            "spoken_continuity_binding_enabled": True,
        }
        installed = {
            **legacy,
            "spoken_continuity_applied": {
                "role": "dialogue_open_before_attribution",
            },
        }

        self.assertIsNone(effective_pause_after_ms(legacy))
        self.assertEqual(effective_pause_after_ms(generating), 130)
        self.assertEqual(effective_pause_after_ms(installed), 130)

    def test_effective_instruction_preserves_authored_direction(self) -> None:
        continuity = {
            "instruction": "Spoken continuity: begin as an attached dialogue tag."
        }
        result = effective_delivery_instruction("Quietly and precisely.", continuity)
        self.assertEqual(
            result,
            "Quietly and precisely. Spoken continuity: begin as an attached dialogue tag.",
        )

    def test_effective_instruction_does_not_duplicate_existing_continuity(self) -> None:
        supplemental = "Spoken continuity: begin as an attached dialogue tag."
        authored = f"Quietly and precisely. {supplemental}"
        self.assertEqual(
            effective_delivery_instruction(authored, {"instruction": supplemental}),
            authored,
        )

    def test_attached_attribution_gets_synthesis_only_continuation_cue(self) -> None:
        self.assertEqual(
            continuity_synthesis_text(
                "Bernice said, but she knew it was too late.",
                {"role": "attached_attribution_after_open_dialogue"},
            ),
            ", bernice said, but she knew it was too late.",
        )

    def test_independent_narration_keeps_authored_synthesis_text(self) -> None:
        text = "Bernice crossed the room."
        self.assertEqual(continuity_synthesis_text(text, None), text)

    def test_binding_migrates_on_touch_without_staling_legacy_audio(self) -> None:
        base = {
            "speaker": "NARRATOR",
            "text": "shouted the Doctor.",
            "instruct": "Plain narration.",
        }
        continuity = {
            "contract_version": 2,
            "role": "attached_attribution_after_terminal_dialogue",
            "boundary_before": "attached_after_terminal",
            "boundary_after": "normal",
            "preceding_or_terminal_punctuation": "exclamation",
            "suggested_pause_after_ms": None,
            "instruction": "Attached tag.",
        }
        voice_config = {"NARRATOR": {"type": "custom", "voice": "Ryan"}}
        baseline = audio_binding_fingerprint(
            chunk=base,
            resolved_speaker="NARRATOR",
            voice_config=voice_config,
        )
        legacy_with_derived_context = audio_binding_fingerprint(
            chunk={**base, "spoken_continuity": continuity},
            resolved_speaker="NARRATOR",
            voice_config=voice_config,
        )
        migrated = audio_binding_fingerprint(
            chunk={
                **base,
                "spoken_continuity": continuity,
                "spoken_continuity_binding_enabled": True,
            },
            resolved_speaker="NARRATOR",
            voice_config=voice_config,
        )

        self.assertEqual(legacy_with_derived_context, baseline)
        self.assertNotEqual(migrated, baseline)


if __name__ == "__main__":
    unittest.main()
