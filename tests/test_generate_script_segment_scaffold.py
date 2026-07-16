from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch

import generate_script
from script_audit import audit_script_chunk


class FakeCompletions:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            self.payload
                        )
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
            ),
        )


class FakeClient:
    def __init__(self, payload):
        self.completions = FakeCompletions(
            payload
        )
        self.chat = SimpleNamespace(
            completions=self.completions
        )


class SourceSegmentContractTests(
    unittest.TestCase
):
    def test_contract_lists_exact_ordered_segments(self):
        source = (
            '"Stop," Mara said. '
            '"Why?" asked Jon.'
        )

        contract = (
            generate_script
            ._build_source_segment_contract(
                source
            )
        )

        self.assertIn(
            "The source contains 4 ordered",
            contract,
        )
        self.assertIn(
            '1. DIALOGUE | text="Stop,"',
            contract,
        )
        self.assertIn(
            '2. NARRATION | text="Mara said."',
            contract,
        )
        self.assertIn(
            '3. DIALOGUE | text="Why?"',
            contract,
        )
        self.assertIn(
            '4. NARRATION | text="asked Jon."',
            contract,
        )

    def test_normalization_restores_text_and_narrator(self):
        source = (
            '"Stop," Mara said. '
            '"Why?" asked Jon.'
        )
        candidate = [
            {
                "speaker": "MARA",
                "text": "Stop",
                "instruct": "Sharp.",
            },
            {
                "speaker": "MARA",
                "text": "Mara said",
                "instruct": "Neutral.",
            },
            {
                "speaker": "JON",
                "text": "Why",
                "instruct": "Questioning.",
            },
            {
                "speaker": "JON",
                "text": "asked Jon",
                "instruct": "Neutral.",
            },
        ]

        normalized, changed = (
            generate_script
            ._normalize_candidate_to_source_segments(
                source,
                candidate,
            )
        )

        self.assertTrue(changed)
        self.assertEqual(
            [entry["speaker"] for entry in normalized],
            [
                "MARA",
                "NARRATOR",
                "JON",
                "NARRATOR",
            ],
        )
        self.assertEqual(
            [entry["text"] for entry in normalized],
            [
                "Stop,",
                "Mara said.",
                "Why?",
                "asked Jon.",
            ],
        )
        self.assertTrue(
            audit_script_chunk(
                source,
                normalized,
            ).passed
        )

    def test_normalization_preserves_allowed_attribution_clarification(self):
        source = (
            '"Wait," he said, looking away. '
            '"Listen to me."'
        )
        candidate = [
            {
                "speaker": "MARCUS",
                "text": "Wait,",
                "instruct": "Urgent.",
            },
            {
                "speaker": "NARRATOR",
                "text": "Marcus said, looking away.",
                "instruct": "Neutral.",
            },
            {
                "speaker": "MARCUS",
                "text": "Listen to me.",
                "instruct": "Firm.",
            },
        ]

        normalized, changed = (
            generate_script
            ._normalize_candidate_to_source_segments(
                source,
                candidate,
            )
        )

        self.assertFalse(changed)
        self.assertEqual(
            normalized[1]["text"],
            "Marcus said, looking away.",
        )
        self.assertTrue(
            audit_script_chunk(
                source,
                normalized,
            ).passed
        )

    def test_normalization_does_not_guess_wrong_entry_count(self):
        source = '"Stop," Mara said.'
        candidate = [
            {
                "speaker": "MARA",
                "text": "Stop",
                "instruct": "Sharp.",
            }
        ]

        normalized, changed = (
            generate_script
            ._normalize_candidate_to_source_segments(
                source,
                candidate,
            )
        )

        self.assertFalse(changed)
        self.assertIs(
            normalized,
            candidate,
        )

    def test_read_aloud_narration_is_source_owned(self):
        source = (
            "Jon unfolded the letter and read aloud, "
            '"Dear Mara, come before sunset."'
        )
        candidate = [
            {
                "speaker": "JON",
                "text": "Dear Mara, come before sunset.",
                "instruct": "Reading.",
            },
            {
                "speaker": "JON",
                "text": "Dear Mara, come before sunset.",
                "instruct": "Reading.",
            },
        ]

        normalized, changed = (
            generate_script
            ._normalize_candidate_to_source_segments(
                source,
                candidate,
            )
        )

        self.assertTrue(changed)
        self.assertEqual(
            normalized[0]["speaker"],
            "NARRATOR",
        )
        self.assertEqual(
            normalized[0]["text"],
            "Jon unfolded the letter and read aloud,",
        )
        self.assertEqual(
            normalized[1]["speaker"],
            "JON",
        )
        self.assertTrue(
            audit_script_chunk(
                source,
                normalized,
            ).passed
        )


class ProcessChunkScaffoldTests(
    unittest.TestCase
):
    def run_chunk(self, source, payload):
        client = FakeClient(payload)

        with (
            patch(
                "builtins.open",
                mock_open(),
            ),
            patch.object(
                generate_script.os,
                "makedirs",
            ),
            patch.object(
                generate_script,
                "record_llm_pipeline_result",
            ),
        ):
            entries = generate_script.process_chunk(
                client,
                "test-model",
                source,
                1,
                1,
                max_retries=0,
                temperature=0.2,
            )

        return client, entries

    def test_process_chunk_repairs_three_speaker_punctuation(self):
        source = (
            '"Stop," Mara said. '
            '"Why?" asked Jon. '
            '"Because it is listening," Lena replied.'
        )
        payload = [
            {
                "speaker": "MARA",
                "text": "Stop",
                "instruct": "Sharp.",
            },
            {
                "speaker": "MARA",
                "text": "Mara said",
                "instruct": "Neutral.",
            },
            {
                "speaker": "JON",
                "text": "Why",
                "instruct": "Questioning.",
            },
            {
                "speaker": "JON",
                "text": "asked Jon",
                "instruct": "Neutral.",
            },
            {
                "speaker": "LENA",
                "text": "Because it is listening",
                "instruct": "Grave.",
            },
            {
                "speaker": "LENA",
                "text": "Lena replied",
                "instruct": "Neutral.",
            },
        ]

        client, entries = self.run_chunk(
            source,
            payload,
        )

        self.assertTrue(
            audit_script_chunk(
                source,
                entries,
            ).passed
        )
        self.assertEqual(
            [
                entry["text"]
                for entry in entries
            ],
            [
                "Stop,",
                "Mara said.",
                "Why?",
                "asked Jon.",
                "Because it is listening,",
                "Lena replied.",
            ],
        )

        messages = (
            client.completions
            .calls[0]["messages"]
        )
        user_message = messages[1][
            "content"
        ]

        self.assertIn(
            "ORDERED SOURCE-SEGMENT CONTRACT",
            user_message,
        )
        self.assertIn(
            "The source contains 6 ordered",
            user_message,
        )

    def test_process_chunk_repairs_emotional_sequence(self):
        source = (
            'The lights failed. "Stay calm," '
            "Mara said evenly. A crash sounded below. "
            '"Run!" she shouted.'
        )
        payload = [
            {
                "speaker": "NARRATOR",
                "text": "The lights failed",
                "instruct": "Tense.",
            },
            {
                "speaker": "MARA",
                "text": "Stay calm",
                "instruct": "Controlled.",
            },
            {
                "speaker": "MARA",
                "text": (
                    "Mara said evenly. "
                    "A crash sounded below"
                ),
                "instruct": "Tense.",
            },
            {
                "speaker": "MARA",
                "text": "Run",
                "instruct": "Urgent.",
            },
            {
                "speaker": "MARA",
                "text": "she shouted",
                "instruct": "Urgent.",
            },
        ]

        _, entries = self.run_chunk(
            source,
            payload,
        )

        self.assertTrue(
            audit_script_chunk(
                source,
                entries,
            ).passed
        )
        self.assertEqual(
            [
                entry["speaker"]
                for entry in entries
            ],
            [
                "NARRATOR",
                "MARA",
                "NARRATOR",
                "MARA",
                "NARRATOR",
            ],
        )


class RetryInstructionTests(
    unittest.TestCase
):
    def test_retry_rules_cover_observed_failures(self):
        issue = SimpleNamespace(
            code="punctuation_changed",
            message="Punctuation changed.",
            source_text="Stay calm,",
            output_text="Stay calm",
        )
        result = SimpleNamespace(
            blocking_issues=[issue]
        )

        suffix = (
            generate_script
            ._build_fidelity_retry_suffix(
                result
            )
        )

        self.assertIn(
            "ordered source-segment contract",
            suffix,
        )
        self.assertIn(
            "Punctuation immediately before",
            suffix,
        )
        self.assertIn(
            "read-aloud material",
            suffix,
        )
        self.assertIn(
            "stable, correctly spelled speaker labels",
            suffix,
        )


if __name__ == "__main__":
    unittest.main()
