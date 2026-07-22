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

    def test_source_backed_first_person_narrator_replaces_pronoun_label(self):
        source = (
            "From the diary of Prof Bernice Summerfield\n\n"
            "'I can see it.'"
        )
        narrator = generate_script._first_person_narrator_from_source(source)
        self.assertEqual(narrator, "BERNICE")
        candidate = [
            {
                "speaker": "NARRATOR",
                "text": "From the diary of Prof Bernice Summerfield",
                "instruct": "Neutral.",
            },
            {
                "speaker": "I",
                "text": "I can see it.",
                "instruct": "Certain.",
            },
        ]

        normalized, changed = (
            generate_script._normalize_candidate_to_source_segments(
                source,
                candidate,
                first_person_narrator=narrator,
            )
        )

        self.assertTrue(changed)
        self.assertEqual(normalized[1]["speaker"], "BERNICE")
        self.assertTrue(audit_script_chunk(source, normalized).passed)
        contract = generate_script._build_source_segment_contract(
            source,
            first_person_narrator=narrator,
        )
        self.assertIn("SOURCE-BACKED FIRST-PERSON NARRATOR: BERNICE", contract)
        self.assertIn("Never use I as a speaker label", contract)

    def test_self_identification_canonicalizes_chunk_aliases(self):
        entries = [
            {
                "speaker": "BENNY",
                "text": "I'm Bernice Summerfield.",
                "instruct": "Friendly.",
            },
            {
                "speaker": "BENNY",
                "text": "Sorry?",
                "instruct": "Confused.",
            },
            {
                "speaker": "YOUNG WOMAN",
                "text": "Constance Harding. I was going to my first dance.",
                "instruct": "Matter-of-fact.",
            },
            {
                "speaker": "YOUNG WOMAN",
                "text": "Your accent gives you away.",
                "instruct": "Dryly amused.",
            },
        ]

        normalized, changed = (
            generate_script._canonicalize_self_identified_speakers(entries)
        )

        self.assertTrue(changed)
        self.assertEqual(
            [entry["speaker"] for entry in normalized],
            ["BERNICE", "BERNICE", "CONSTANCE", "CONSTANCE"],
        )

    def test_short_name_maps_only_to_established_longer_source_name(self):
        source = (
            "Timothy stood by the radiator. "
            "'I said,' Tim said, 'leave it alone.'"
        )
        entries = [
            {
                "speaker": "TIM",
                "text": "I said,",
                "instruct": "Firm.",
            },
            {
                "speaker": "NARRATOR",
                "text": "Tim said,",
                "instruct": "Neutral.",
            },
            {
                "speaker": "TIM",
                "text": "leave it alone.",
                "instruct": "Firm.",
            },
        ]

        normalized, changed = (
            generate_script._canonicalize_to_established_speakers(
                source,
                entries,
                established_speakers=["TIMOTHY"],
            )
        )
        self.assertTrue(changed)
        self.assertEqual(
            [entry["speaker"] for entry in normalized],
            ["TIMOTHY", "NARRATOR", "TIMOTHY"],
        )

        unchanged, changed = (
            generate_script._canonicalize_to_established_speakers(
                "Ann met Annette at the station.",
                [{"speaker": "ANN", "text": "Hello.", "instruct": "Warm."}],
                established_speakers=["ANNETTE"],
            )
        )
        self.assertFalse(changed)
        self.assertEqual(unchanged[0]["speaker"], "ANN")

    def test_first_person_identity_does_not_leak_into_third_person_chunks(self):
        source = "'Who is there?' he asked."
        candidate = [
            {
                "speaker": "I",
                "text": "Who is there?",
                "instruct": "Cautious.",
            },
            {
                "speaker": "NARRATOR",
                "text": "he asked.",
                "instruct": "Neutral.",
            },
        ]

        normalized, _changed = (
            generate_script._normalize_candidate_to_source_segments(
                source,
                candidate,
                first_person_narrator="BERNICE",
            )
        )

        self.assertFalse(
            generate_script._source_uses_first_person_narration(source)
        )
        self.assertEqual(normalized[0]["speaker"], "I")
        self.assertFalse(audit_script_chunk(source, normalized).passed)

    def test_pronoun_attribution_uses_adjacent_named_addressee(self):
        source = (
            "'Aren't there any monsters?' "
            "I asked the Doctor. "
            "'Alien monsters...' he mused."
        )
        candidate = [
            {
                "speaker": "BERNICE",
                "text": "Aren't there any monsters?",
                "instruct": "Questioning.",
            },
            {
                "speaker": "NARRATOR",
                "text": "I asked the Doctor.",
                "instruct": "Neutral.",
            },
            {
                "speaker": "BERNICE",
                "text": "Alien monsters...",
                "instruct": "Thoughtful.",
            },
            {
                "speaker": "NARRATOR",
                "text": "he mused.",
                "instruct": "Neutral.",
            },
        ]

        normalized, changed = (
            generate_script._normalize_candidate_to_source_segments(
                source,
                candidate,
                first_person_narrator="BERNICE",
            )
        )

        self.assertTrue(changed)
        self.assertEqual(
            [entry["speaker"] for entry in normalized],
            ["BERNICE", "NARRATOR", "DOCTOR", "NARRATOR"],
        )
        self.assertTrue(audit_script_chunk(source, normalized).passed)

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

    def test_inverted_attribution_restores_meaningful_title(self):
        source = (
            '"You came alone," said Captain Vale, '
            "without lowering his weapon."
        )
        candidate = [
            {
                "speaker": "VALE",
                "text": "You came alone,",
                "instruct": "Controlled.",
            },
            {
                "speaker": "NARRATOR",
                "text": (
                    "said Captain Vale, "
                    "without lowering his weapon."
                ),
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
            normalized[0]["speaker"],
            "CAPTAIN VALE",
        )
        self.assertTrue(
            audit_script_chunk(
                source,
                normalized,
            ).passed
        )

    def test_normal_attribution_corrects_nonhuman_misspelling(self):
        source = (
            "The envoy's shell brightened. "
            '"Your oxygen debt is noted," '
            "the Khepri said. "
            '"Payment will be biological."'
        )
        candidate = [
            {
                "speaker": "NARRATOR",
                "text": (
                    "The envoy's shell brightened."
                ),
                "instruct": "Neutral.",
            },
            {
                "speaker": "THE KHOPRI",
                "text": (
                    "Your oxygen debt is noted,"
                ),
                "instruct": "Cold.",
            },
            {
                "speaker": "NARRATOR",
                "text": "the Khepri said.",
                "instruct": "Neutral.",
            },
            {
                "speaker": "THE KHOPRI",
                "text": (
                    "Payment will be biological."
                ),
                "instruct": "Cold.",
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
            normalized[1]["speaker"],
            "KHEPRI",
        )
        self.assertEqual(
            normalized[3]["speaker"],
            "THE KHOPRI",
        )
        self.assertTrue(
            audit_script_chunk(
                source,
                normalized,
            ).passed
        )

    def test_definite_article_title_variant_is_preserved(self):
        source = (
            '"That is impossible," '
            "the Doctor said."
        )
        candidate = [
            {
                "speaker": "THE DOCTOR",
                "text": "That is impossible,",
                "instruct": "Firm.",
            },
            {
                "speaker": "NARRATOR",
                "text": "the Doctor said.",
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

        self.assertFalse(changed)
        self.assertEqual(
            normalized[0]["speaker"],
            "THE DOCTOR",
        )

    def test_multiword_reader_name_is_captured_without_prose(self):
        source = (
            "Doctor Sen unfolded the letter and read aloud, "
            '"Come before sunset."'
        )
        candidate = [
            {
                "speaker": "NARRATOR",
                "text": (
                    "Doctor Sen unfolded the letter "
                    "and read aloud,"
                ),
                "instruct": "Neutral.",
            },
            {
                "speaker": "SEN",
                "text": "Come before sunset.",
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
            normalized[1]["speaker"],
            "DOCTOR SEN",
        )

    def test_generic_lowercase_title_does_not_override_known_name(self):
        source = (
            '"Call me Ilyan," '
            "the professor said."
        )
        candidate = [
            {
                "speaker": "ILYAN",
                "text": "Call me Ilyan,",
                "instruct": "Calm.",
            },
            {
                "speaker": "NARRATOR",
                "text": "the professor said.",
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

        self.assertFalse(changed)
        self.assertEqual(
            normalized[0]["speaker"],
            "ILYAN",
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
