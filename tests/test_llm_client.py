from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from llm_client import (
    CompletionResult,
    LLMClient,
    parse_json_content,
)
from llm_schemas import ContractValidationError
from ollama_runtime import (
    OllamaChatResult,
    is_local_ollama_base_url,
    native_root_from_openai_base_url,
)


def make_ollama_result(
    content: str,
    *,
    thinking: str | None = None,
    done_reason: str = "stop",
) -> OllamaChatResult:
    return OllamaChatResult(
        content=content,
        thinking=thinking,
        done_reason=done_reason,
        model="qwen3.5:35b-mlx",
        total_duration=2_000_000_000,
        load_duration=100_000_000,
        prompt_eval_duration=500_000_000,
        prompt_eval_count=100,
        eval_duration=1_000_000_000,
        eval_count=72,
        raw={
            "message": {
                "content": content,
                "thinking": thinking,
            },
            "done_reason": done_reason,
        },
    )


def make_client(
    *,
    base_url: str = "http://localhost:11434/v1",
    corrective_retry: bool = True,
) -> LLMClient:
    return LLMClient(
        base_url=base_url,
        api_key="local",
        model_name="qwen3.5:35b-mlx",
        backend="auto",
        context_length=40960,
        keep_alive=-1,
        thinking=False,
        structured_output=True,
        corrective_retry=corrective_retry,
    )


class JSONParsingTests(unittest.TestCase):
    def test_plain_json(self) -> None:
        parsed = parse_json_content(
            '{"description":"Voice","ref_text":"Hello."}'
        )

        self.assertEqual(parsed["description"], "Voice")

    def test_fenced_json(self) -> None:
        parsed = parse_json_content(
            '```json\n'
            '{"description":"Voice","ref_text":"Hello."}'
            '\n```'
        )

        self.assertEqual(parsed["ref_text"], "Hello.")

    def test_thinking_tags_are_removed(self) -> None:
        parsed = parse_json_content(
            "<think>Private reasoning.</think>\n"
            '{"description":"Voice","ref_text":"Hello."}'
        )

        self.assertEqual(parsed["description"], "Voice")

    def test_surrounding_text_is_tolerated(self) -> None:
        parsed = parse_json_content(
            'Here is the result: '
            '{"description":"Voice","ref_text":"Hello."}'
            " Finished."
        )

        self.assertEqual(parsed["ref_text"], "Hello.")

    def test_empty_content_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            parse_json_content("   ")

    def test_malformed_json_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            parse_json_content('{"description":')
            

class OllamaURLTests(unittest.TestCase):
    def test_localhost_v1_detection(self) -> None:
        self.assertEqual(
            native_root_from_openai_base_url(
                "http://localhost:11434/v1"
            ),
            "http://localhost:11434",
        )

    def test_loopback_detection(self) -> None:
        self.assertEqual(
            native_root_from_openai_base_url(
                "http://127.0.0.1:11434/v1/"
            ),
            "http://127.0.0.1:11434",
        )

    def test_remote_server_is_not_treated_as_local_ollama(self) -> None:
        self.assertIsNone(
            native_root_from_openai_base_url(
                "https://api.example.com/v1"
            )
        )

    def test_wrong_local_port_is_not_treated_as_ollama(self) -> None:
        self.assertFalse(
            is_local_ollama_base_url(
                "http://localhost:1234/v1"
            )
        )


class NativeClientTests(unittest.TestCase):
    def test_native_backend_selection(self) -> None:
        client = make_client()

        self.assertEqual(client.backend, "ollama-native")
        self.assertEqual(
            client.native_root,
            "http://localhost:11434",
        )

    def test_non_ollama_backend_selection(self) -> None:
        client = make_client(
            base_url="http://localhost:1234/v1"
        )

        self.assertEqual(
            client.backend,
            "openai-compatible",
        )
        self.assertIsNone(client.native_root)

    @patch("llm_client.preload_model")
    def test_preload_success(self, preload_mock) -> None:
        preload_mock.return_value = {
            "done": True,
            "done_reason": "load",
        }

        client = make_client()
        success, message = client.preload()

        self.assertTrue(success)
        self.assertIn("Preloaded", message)
        preload_mock.assert_called_once()

    @patch("llm_client.preload_model")
    def test_preload_failure_does_not_raise(
        self,
        preload_mock,
    ) -> None:
        preload_mock.side_effect = RuntimeError("offline")

        client = make_client()
        success, message = client.preload()

        self.assertFalse(success)
        self.assertIn("failed", message.lower())

    @patch("llm_client.chat_json")
    def test_direct_persona_contract(
        self,
        chat_mock,
    ) -> None:
        chat_mock.return_value = make_ollama_result(
            json.dumps(
                {
                    "description": "A mature British baritone.",
                    "ref_text": "The matter is not settled.",
                }
            )
        )

        client = make_client()

        result = client.complete_json(
            messages=[
                {
                    "role": "user",
                    "content": "Create a persona.",
                }
            ],
            contract="persona",
            temperature=0.3,
            max_tokens=400,
        )

        self.assertIsInstance(result, CompletionResult)
        self.assertEqual(result.validation_mode, "direct")
        self.assertEqual(result.backend, "ollama-native")
        self.assertEqual(
            result.data["description"],
            "A mature British baritone.",
        )
        self.assertEqual(
            result.metrics["output_tokens_per_second"],
            72.0,
        )
        self.assertEqual(chat_mock.call_count, 1)

    @patch("llm_client.chat_json")
    def test_wrapped_script_is_normalized(
        self,
        chat_mock,
    ) -> None:
        chat_mock.return_value = make_ollama_result(
            json.dumps(
                {
                    "entries": [
                        {
                            "speaker": "THE DOCTOR",
                            "text": "No. It rarely is.",
                            "instruct": "Quiet resignation.",
                        }
                    ]
                }
            )
        )

        client = make_client()

        result = client.complete_json(
            messages=[
                {
                    "role": "user",
                    "content": "Convert this passage.",
                }
            ],
            contract="script",
            temperature=0.6,
            max_tokens=1000,
        )

        self.assertIsInstance(result.data, list)
        self.assertEqual(
            result.data[0]["speaker"],
            "THE DOCTOR",
        )

    @patch("llm_client.chat_json")
    def test_corrective_retry(
        self,
        chat_mock,
    ) -> None:
        invalid = make_ollama_result(
            json.dumps(
                {
                    "description": "Voice",
                    "ref_text": "Hello.",
                    "persona_name": "Invalid extra field",
                }
            )
        )

        corrected = make_ollama_result(
            json.dumps(
                {
                    "description": "Voice",
                    "ref_text": "Hello.",
                }
            )
        )

        chat_mock.side_effect = [invalid, corrected]

        client = make_client(corrective_retry=True)

        result = client.complete_json(
            messages=[
                {
                    "role": "user",
                    "content": "Create a persona.",
                }
            ],
            contract="persona",
            temperature=0.3,
            max_tokens=400,
        )

        self.assertEqual(
            result.validation_mode,
            "corrective_retry",
        )
        self.assertEqual(chat_mock.call_count, 2)
        self.assertIn(
            "initial_validation_error",
            result.metrics,
        )

        second_messages = chat_mock.call_args_list[1].kwargs[
            "messages"
        ]

        self.assertIn(
            "violated the required",
            second_messages[-1]["content"],
        )

    @patch("llm_client.chat_json")
    def test_retry_can_be_disabled(
        self,
        chat_mock,
    ) -> None:
        chat_mock.return_value = make_ollama_result(
            '{"description":"Voice","unexpected":"bad"}'
        )

        client = make_client(corrective_retry=False)

        with self.assertRaises(ContractValidationError):
            client.complete_json(
                messages=[
                    {
                        "role": "user",
                        "content": "Create a persona.",
                    }
                ],
                contract="persona",
                temperature=0.3,
                max_tokens=400,
            )

        self.assertEqual(chat_mock.call_count, 1)

    @patch("llm_client.chat_json")
    def test_corrective_retry_failure_is_reported(
        self,
        chat_mock,
    ) -> None:
        invalid_first = make_ollama_result(
            '{"description":"Voice","unexpected":"bad"}'
        )

        invalid_second = make_ollama_result(
            '{"still":"wrong"}'
        )

        chat_mock.side_effect = [
            invalid_first,
            invalid_second,
        ]

        client = make_client(corrective_retry=True)

        with self.assertRaises(ContractValidationError) as context:
            client.complete_json(
                messages=[
                    {
                        "role": "user",
                        "content": "Create a persona.",
                    }
                ],
                contract="persona",
                temperature=0.3,
                max_tokens=400,
            )

        self.assertIn(
            "corrective retry failed",
            str(context.exception),
        )

    def test_unknown_contract_is_rejected_before_request(
        self,
    ) -> None:
        client = make_client()

        with self.assertRaises(ValueError):
            client.complete_json(
                messages=[
                    {
                        "role": "user",
                        "content": "Test.",
                    }
                ],
                contract="unknown",
                temperature=0.3,
                max_tokens=100,
            )


if __name__ == "__main__":
    unittest.main()
