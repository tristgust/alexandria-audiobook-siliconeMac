from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open
from unittest.mock import patch

import generate_script


def make_response(entries):
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

        return make_response(
            self.responses.pop(0)
        )


def run_process_chunk(
    client,
    source,
    *,
    max_retries,
):
    with (
        patch(
            "builtins.open",
            mock_open(),
        ),
        patch(
            "generate_script.os.makedirs",
        ),
    ):
        return generate_script.process_chunk(
            client,
            "qwen3.5:35b-mlx",
            source,
            1,
            1,
            previous_entries=None,
            max_retries=max_retries,
            system_prompt=(
                "Return an audiobook script."
            ),
            user_prompt_template=(
                "{context}\n\n"
                "SOURCE TEXT:\n"
                "{chunk}"
            ),
            max_tokens=1000,
            temperature=0.2,
            top_p=0.8,
            top_k=0,
            min_p=0,
            presence_penalty=0.0,
            banned_tokens=[],
        )


class FidelityRetryIntegrationTests(
    unittest.TestCase
):
    def test_passing_output_is_accepted_once(self):
        source = (
            '"No," he said quietly. '
            '"It rarely is."'
        )

        correct = [
            {
                "speaker": "THE DOCTOR",
                "text": "No,",
                "instruct": "Quiet agreement.",
            },
            {
                "speaker": "NARRATOR",
                "text": "he said quietly.",
                "instruct": (
                    "Neutral, even narration."
                ),
            },
            {
                "speaker": "THE DOCTOR",
                "text": "It rarely is.",
                "instruct": "Dry resignation.",
            },
        ]

        client = SequenceClient(
            [correct]
        )

        result = run_process_chunk(
            client,
            source,
            max_retries=2,
        )

        self.assertEqual(
            result,
            correct,
        )

        self.assertEqual(
            len(client.prompts),
            1,
        )

    def test_failed_audit_retries_with_feedback(self):
        source = (
            '"No," he said quietly. '
            '"It rarely is."'
        )

        missing_attribution = [
            {
                "speaker": "THE DOCTOR",
                "text": "No,",
                "instruct": "Quiet agreement.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "It rarely is.",
                "instruct": "Dry resignation.",
            },
        ]

        corrected = [
            {
                "speaker": "THE DOCTOR",
                "text": "No,",
                "instruct": "Quiet agreement.",
            },
            {
                "speaker": "NARRATOR",
                "text": "he said quietly.",
                "instruct": (
                    "Neutral, even narration."
                ),
            },
            {
                "speaker": "THE DOCTOR",
                "text": "It rarely is.",
                "instruct": "Dry resignation.",
            },
        ]

        client = SequenceClient(
            [
                missing_attribution,
                corrected,
            ]
        )

        result = run_process_chunk(
            client,
            source,
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
                "CRITICAL SOURCE-FIDELITY "
                "CORRECTION REQUIRED"
            ),
            retry_prompt,
        )

        self.assertIn(
            "he said quietly.",
            retry_prompt,
        )

        self.assertIn(
            "Regenerate the ENTIRE source chunk",
            retry_prompt,
        )

    def test_final_failed_audit_returns_empty(self):
        source = (
            '"No," he said quietly. '
            '"It rarely is."'
        )

        bad = [
            {
                "speaker": "THE DOCTOR",
                "text": "No, It rarely is.",
                "instruct": "Dry resignation.",
            }
        ]

        client = SequenceClient(
            [
                bad,
                bad,
            ]
        )

        result = run_process_chunk(
            client,
            source,
            max_retries=1,
        )

        self.assertEqual(
            result,
            [],
        )

        self.assertEqual(
            len(client.prompts),
            2,
        )

    def test_optional_attribution_clarification_passes(
        self,
    ):
        source = (
            '"Wait," he said, looking away. '
            '"Listen to me."'
        )

        clarified = [
            {
                "speaker": "MARCUS",
                "text": "Wait,",
                "instruct": "Urgent restraint.",
            },
            {
                "speaker": "NARRATOR",
                "text": (
                    "Marcus said, looking away."
                ),
                "instruct": (
                    "Neutral, even narration."
                ),
            },
            {
                "speaker": "MARCUS",
                "text": "Listen to me.",
                "instruct": "Firm urgency.",
            },
        ]

        client = SequenceClient(
            [clarified]
        )

        result = run_process_chunk(
            client,
            source,
            max_retries=0,
        )

        self.assertEqual(
            result,
            clarified,
        )

        self.assertEqual(
            len(client.prompts),
            1,
        )


class FidelityFeedbackTests(unittest.TestCase):
    def test_retry_suffix_contains_source_and_output(
        self,
    ):
        source = (
            '"There is nothing to tell," '
            "said Marcus, unable to meet her gaze."
        )

        rewritten = [
            {
                "speaker": "MARCUS",
                "text": (
                    "There is nothing to tell,"
                ),
                "instruct": "Guarded.",
            },
            {
                "speaker": "NARRATOR",
                "text": (
                    "Marcus was unable to meet "
                    "her gaze."
                ),
                "instruct": "Neutral narration.",
            },
        ]

        audit_result = (
            generate_script.audit_script_chunk(
                source,
                rewritten,
            )
        )

        suffix = (
            generate_script
            ._build_fidelity_retry_suffix(
                audit_result
            )
        )

        self.assertIn(
            "attribution_changed",
            suffix,
        )

        self.assertIn(
            (
                "said Marcus, unable to meet "
                "her gaze."
            ),
            suffix,
        )

        self.assertIn(
            (
                "Marcus was unable to meet "
                "her gaze."
            ),
            suffix,
        )


if __name__ == "__main__":
    unittest.main()
