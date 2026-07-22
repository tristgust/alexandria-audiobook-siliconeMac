from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

import generate_personas
import generate_script
import review_script
from roster_context import (
    RosterContextSourceUnavailableError,
    load_project_roster_context,
)
from tests.test_roster_context import RosterContextFixture


class JsonSequenceClient:
    def __init__(self, payloads: list[object]):
        self.payloads = list(payloads)
        self.prompts: list[str] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][1]["content"])
        if not self.payloads:
            raise AssertionError("Fake client ran out of payloads.")
        payload = self.payloads.pop(0)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
            ),
        )


class RosterPipelineIntegrationTests(
    unittest.TestCase,
    RosterContextFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.roster = self.approved_roster(self.source_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_script_prompt_and_output_use_approved_roster(self) -> None:
        source = '"Hello," said the Doctor.'
        response = [
            {
                "speaker": "Doctor",
                "text": "Hello,",
                "instruct": "Warm greeting.",
            },
            {
                "speaker": "NARRATOR",
                "text": "said the Doctor.",
                "instruct": "Neutral narration.",
            },
        ]
        client = JsonSequenceClient([response])
        with (
            patch("builtins.open", mock_open()),
            patch("generate_script.os.makedirs"),
        ):
            entries = generate_script.process_chunk(
                client,
                "qwen3.5:35b-mlx",
                source,
                1,
                1,
                previous_entries=None,
                max_retries=0,
                system_prompt="Return JSON.",
                user_prompt_template="{context}\nSOURCE:\n{chunk}",
                max_tokens=1000,
                temperature=0.2,
                top_p=0.8,
                top_k=0,
                min_p=0,
                presence_penalty=0.0,
                banned_tokens=[],
                approved_roster=self.roster,
            )
        self.assertIn("APPROVED CHARACTER ROSTER", client.prompts[0])
        self.assertIn(self.roster["roster_fingerprint"], client.prompts[0])
        self.assertEqual(entries[0]["speaker"], "THE DOCTOR")
        self.assertEqual(entries[0]["text"], "Hello,")
        self.assertEqual(entries[0]["instruct"], "Warm greeting.")

    def test_script_legacy_context_remains_without_roster(self) -> None:
        source = '"Hello."'
        response = [
            {
                "speaker": "ROZ",
                "text": "Hello.",
                "instruct": "Brief greeting.",
            }
        ]
        client = JsonSequenceClient([response])
        with (
            patch("builtins.open", mock_open()),
            patch("generate_script.os.makedirs"),
        ):
            generate_script.process_chunk(
                client,
                "qwen3.5:35b-mlx",
                source,
                2,
                3,
                previous_entries=[
                    {
                        "speaker": "THE DOCTOR",
                        "text": "Wait.",
                        "instruct": "Sharp warning.",
                    }
                ],
                max_retries=0,
                system_prompt="Return JSON.",
                user_prompt_template="{context}\nSOURCE:\n{chunk}",
                max_tokens=1000,
                temperature=0.2,
                top_p=0.8,
                top_k=0,
                min_p=0,
                presence_penalty=0.0,
                banned_tokens=[],
                approved_roster=None,
            )
        self.assertIn("Characters in this book: THE DOCTOR", client.prompts[0])
        self.assertNotIn("APPROVED CHARACTER ROSTER", client.prompts[0])

    def test_review_prompt_canonicalizes_speaker_after_text_audit(self) -> None:
        batch = [
            {
                "speaker": "Doctor",
                "text": "Tell me the truth.",
                "instruct": "Quiet urgency.",
            }
        ]
        client = JsonSequenceClient([batch])
        with (
            patch("builtins.open", mock_open()),
            patch("review_script.os.makedirs"),
        ):
            reviewed = review_script.review_batch(
                client,
                "qwen3.5:35b-mlx",
                batch,
                1,
                1,
                max_retries=0,
                system_prompt="Review JSON.",
                user_prompt_template="{context}\n{batch}",
                approved_roster=self.roster,
            )
        self.assertIn("APPROVED CHARACTER ROSTER", client.prompts[0])
        self.assertEqual(reviewed[0]["speaker"], "THE DOCTOR")
        self.assertEqual(reviewed[0]["text"], batch[0]["text"])
        self.assertEqual(reviewed[0]["instruct"], batch[0]["instruct"])

    def test_persona_discovery_and_compile_prompts_share_roster(self) -> None:
        batch_prompt = generate_personas._build_batch_discovery_prompt(
            0,
            [
                {
                    "speaker": "THE DOCTOR",
                    "text": "Tell me what happened.",
                }
            ],
            ["THE DOCTOR"],
            approved_roster=self.roster,
        )
        compile_prompt = generate_personas._compile_character_prompt(
            {
                "name": "THE DOCTOR",
                "aliases": [],
                "features": [],
                "personality": [],
                "voice_clues": [],
                "relationships": [],
                "sample_lines": ["Tell me what happened."],
                "observations": [],
            },
            approved_roster=self.roster,
        )
        for prompt in (batch_prompt, compile_prompt):
            self.assertIn("APPROVED CHARACTER ROSTER", prompt)
            self.assertIn(self.roster["roster_fingerprint"], prompt)
            self.assertIn("THE DOCTOR", prompt)

    def test_persona_roster_alias_is_exact_not_fuzzy(self) -> None:
        self.assertEqual(
            generate_personas.canonical_speaker_name(
                "Doctor",
                self.roster,
            ),
            "THE DOCTOR",
        )
        self.assertEqual(
            generate_personas.canonical_speaker_name(
                "Doctor-ish",
                self.roster,
            ),
            "Doctor-ish",
        )

    def test_generation_identity_changes_only_when_roster_is_present(self) -> None:
        runtime = SimpleNamespace(
            backend="ollama-native",
            thinking=False,
            structured_output=True,
            corrective_retry=True,
        )
        kwargs = {
            "runtime_client": runtime,
            "base_url": "http://localhost:11434/v1",
            "model_name": "qwen3.5:35b-mlx",
            "system_prompt": "system",
            "user_prompt_template": "{context}{chunk}",
            "chunk_size": 3000,
            "max_tokens": 4096,
            "temperature": 0.6,
            "top_p": 0.8,
            "top_k": 0,
            "min_p": 0,
            "presence_penalty": 0.0,
            "banned_tokens": [],
        }
        legacy = generate_script._script_generation_identity(**kwargs)
        with_roster = generate_script._script_generation_identity(
            **kwargs,
            roster_identity={
                "context_version": 1,
                "source_fingerprint": self.roster["source"]["fingerprint"],
                "roster_fingerprint": self.roster["roster_fingerprint"],
            },
        )
        self.assertNotIn("approved_roster", legacy)
        self.assertEqual(
            with_roster["approved_roster"]["roster_fingerprint"],
            self.roster["roster_fingerprint"],
        )

    def test_project_context_uses_state_source_and_blocks_missing_source(self) -> None:
        roster_path = self.root / "character_roster.json"
        from character_roster import save_character_roster

        save_character_roster(
            self.roster,
            roster_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        roster, source_text, source_path = load_project_roster_context(
            root_dir=self.root,
        )
        self.assertEqual(roster, self.roster)
        self.assertEqual(source_text, self.SOURCE_TEXT)
        self.assertEqual(source_path, self.source_path)

        (self.root / "state.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(RosterContextSourceUnavailableError):
            load_project_roster_context(root_dir=self.root)


if __name__ == "__main__":
    unittest.main()
