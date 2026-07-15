from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open
from unittest.mock import patch

import review_script


def entry(
    speaker,
    text,
    instruct="Neutral delivery.",
):
    return {
        "speaker": speaker,
        "text": text,
        "instruct": instruct,
    }


def response(entries):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        entries,
                        ensure_ascii=False,
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


class SequenceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
            )
        )

    def _create(self, **kwargs):
        self.prompts.append(
            kwargs["messages"][1]["content"]
        )

        if not self.responses:
            raise AssertionError(
                "SequenceClient ran out of responses."
            )

        return response(
            self.responses.pop(0)
        )


def run_review(
    client,
    original,
    *,
    max_retries,
):
    with (
        patch(
            "builtins.open",
            mock_open(),
        ),
        patch(
            "review_script.os.makedirs",
        ),
    ):
        return review_script.review_batch(
            client,
            "qwen3.5:35b-mlx",
            original,
            1,
            1,
            max_retries=max_retries,
            system_prompt=(
                "Review the audiobook script."
            ),
            user_prompt_template=(
                "{context}\n\n"
                "SCRIPT ENTRIES TO REVIEW:\n"
                "{batch}"
            ),
            max_tokens=1000,
            temperature=0.2,
            top_p=0.8,
            top_k=0,
            min_p=0,
            presence_penalty=0.0,
            banned_tokens=[],
        )


class ReviewFidelityIntegrationTests(
    unittest.TestCase
):
    def test_exact_text_is_accepted_once(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            ),
            entry(
                "MARCUS",
                "Wait.",
            ),
        ]

        corrected = [
            entry(
                "NARRATOR",
                "The door opened.",
                "Quiet, tense narration.",
            ),
            entry(
                "ELENA",
                "Wait.",
                "Firm urgency.",
            ),
        ]

        client = SequenceClient(
            [corrected]
        )

        result = run_review(
            client,
            original,
            max_retries=2,
        )

        self.assertEqual(
            result,
            corrected,
        )

        self.assertEqual(
            len(client.prompts),
            1,
        )

    def test_changed_text_retries_with_feedback(self):
        original = [
            entry(
                "NARRATOR",
                "He said in confusion.",
            )
        ]

        rewritten = [
            entry(
                "NARRATOR",
                "He seemed confused.",
            )
        ]

        corrected = [
            entry(
                "NARRATOR",
                "He said in confusion.",
                "Uneasy narration.",
            )
        ]

        client = SequenceClient(
            [
                rewritten,
                corrected,
            ]
        )

        result = run_review(
            client,
            original,
            max_retries=1,
        )

        self.assertEqual(
            result,
            corrected,
        )

        self.assertEqual(
            len(client.prompts),
            2,
        )

        retry_prompt = client.prompts[1]

        self.assertIn(
            (
                "CRITICAL REVIEW TEXT-PRESERVATION "
                "CORRECTION REQUIRED"
            ),
            retry_prompt,
        )

        self.assertIn(
            "He said in confusion.",
            retry_prompt,
        )

        self.assertIn(
            "He seemed confused.",
            retry_prompt,
        )

    def test_final_text_failure_returns_none(self):
        original = [
            entry(
                "NARRATOR",
                "He said in confusion.",
            )
        ]

        rewritten = [
            entry(
                "NARRATOR",
                "He seemed confused.",
            )
        ]

        client = SequenceClient(
            [
                rewritten,
                rewritten,
            ]
        )

        result = run_review(
            client,
            original,
            max_retries=1,
        )

        self.assertIsNone(result)

        self.assertEqual(
            len(client.prompts),
            2,
        )

    def test_preserved_split_is_accepted(self):
        original = [
            entry(
                "NARRATOR",
                (
                    "The door opened. "
                    "Marcus stepped inside."
                ),
            )
        ]

        split = [
            entry(
                "NARRATOR",
                "The door opened.",
            ),
            entry(
                "MARCUS",
                "Marcus stepped inside.",
                "Measured delivery.",
            ),
        ]

        client = SequenceClient(
            [split]
        )

        result = run_review(
            client,
            original,
            max_retries=0,
        )

        self.assertEqual(
            result,
            split,
        )

    def test_retry_suffix_contains_audit_context(self):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            )
        ]

        rewritten = [
            entry(
                "NARRATOR",
                "The door closed.",
            )
        ]

        audit_result = (
            review_script.audit_review_batch(
                original,
                rewritten,
            )
        )

        suffix = (
            review_script
            ._build_review_text_retry_suffix(
                audit_result,
                original,
            )
        )

        self.assertIn(
            "wording_or_order_changed",
            suffix,
        )

        self.assertIn(
            "The door opened.",
            suffix,
        )

        self.assertIn(
            "The door closed.",
            suffix,
        )




class ContextLeakageRecoveryTests(
    unittest.TestCase
):
    def test_interleaved_context_entries_are_removed(
        self,
    ):
        original = [
            entry(
                "DOCTOR",
                "It rarely is.",
            ),
            entry(
                "NARRATOR",
                (
                    "Marcus stood beside the "
                    "observation window."
                ),
            ),
            entry(
                "MARCUS",
                "There is nothing to tell.",
            ),
        ]

        contaminated = [
            entry(
                "NARRATOR",
                "Previous context only.",
            ),
            entry(
                "DOCTOR",
                "It rarely is.",
                "Dry resignation.",
            ),
            entry(
                "NARRATOR",
                "Interleaved context only.",
            ),
            entry(
                "NARRATOR",
                (
                    "Marcus stood beside the "
                    "observation window."
                ),
                "Quiet unease.",
            ),
            entry(
                "MARCUS",
                "There is nothing to tell.",
                "Guarded denial.",
            ),
            entry(
                "NARRATOR",
                "Next context only.",
            ),
        ]

        client = SequenceClient(
            [contaminated]
        )

        result = run_review(
            client,
            original,
            max_retries=0,
        )

        self.assertIsNotNone(result)

        self.assertEqual(
            [
                item["text"]
                for item in result
            ],
            [
                item["text"]
                for item in original
            ],
        )

        self.assertEqual(
            len(result),
            3,
        )

        self.assertEqual(
            len(client.prompts),
            1,
        )

    def test_duplicate_target_is_not_guessed(
        self,
    ):
        original = [
            entry(
                "MARCUS",
                "Wait.",
            )
        ]

        ambiguous = [
            entry(
                "MARCUS",
                "Wait.",
            ),
            entry(
                "MARCUS",
                "Wait.",
            ),
        ]

        client = SequenceClient(
            [ambiguous]
        )

        result = run_review(
            client,
            original,
            max_retries=0,
        )

        self.assertIsNone(result)

    def test_retry_suffix_contains_full_target(
        self,
    ):
        original = [
            entry(
                "NARRATOR",
                "The door opened.",
            ),
            entry(
                "MARCUS",
                "Wait.",
            ),
        ]

        rewritten = [
            entry(
                "NARRATOR",
                "The door closed.",
            )
        ]

        audit_result = (
            review_script.audit_review_batch(
                original,
                rewritten,
            )
        )

        suffix = (
            review_script
            ._build_review_text_retry_suffix(
                audit_result,
                original,
            )
        )

        self.assertIn(
            "EXACT TARGET BATCH JSON:",
            suffix,
        )

        self.assertIn(
            '"The door opened."',
            suffix,
        )

        self.assertIn(
            '"Wait."',
            suffix,
        )

        self.assertIn(
            (
                "Do not copy any PREVIOUS or NEXT "
                "context-only entry"
            ),
            suffix,
        )


if __name__ == "__main__":
    unittest.main()
