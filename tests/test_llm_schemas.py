from __future__ import annotations

import unittest

from llm_schemas import (
    ContractValidationError,
    get_schema,
    validate_advanced_discovery,
    validate_alias_map,
    validate_contract,
    validate_persona,
    validate_script,
)


class PersonaContractTests(unittest.TestCase):
    def test_valid_persona(self) -> None:
        result = validate_persona(
            {
                "description": " A mature British baritone. ",
                "ref_text": " The matter is not settled. ",
            }
        )

        self.assertEqual(
            result,
            {
                "description": "A mature British baritone.",
                "ref_text": "The matter is not settled.",
            },
        )

    def test_persona_rejects_extra_keys(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_persona(
                {
                    "description": "A mature British baritone.",
                    "ref_text": "The matter is not settled.",
                    "persona_name": "Arthur",
                }
            )

    def test_persona_rejects_missing_ref_text(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_persona(
                {
                    "description": "A mature British baritone.",
                }
            )

    def test_persona_rejects_empty_description(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_persona(
                {
                    "description": "   ",
                    "ref_text": "The matter is not settled.",
                }
            )


class ScriptContractTests(unittest.TestCase):
    def test_valid_bare_script_array(self) -> None:
        result = validate_script(
            [
                {
                    "speaker": "NARRATOR",
                    "text": " The door opened. ",
                    "instruct": " Neutral, even narration. ",
                }
            ]
        )

        self.assertEqual(
            result,
            [
                {
                    "speaker": "NARRATOR",
                    "text": "The door opened.",
                    "instruct": "Neutral, even narration.",
                }
            ],
        )

    def test_valid_wrapped_script_array(self) -> None:
        result = validate_script(
            {
                "entries": [
                    {
                        "speaker": "THE DOCTOR",
                        "text": "No. It rarely is.",
                        "instruct": "Dry, quiet resignation.",
                    }
                ]
            }
        )

        self.assertEqual(result[0]["speaker"], "THE DOCTOR")

    def test_script_rejects_missing_instruct(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_script(
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "The door opened.",
                    }
                ]
            )

    def test_script_rejects_non_string_text(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_script(
                [
                    {
                        "speaker": "NARRATOR",
                        "text": 42,
                        "instruct": "Neutral narration.",
                    }
                ]
            )

    def test_script_rejects_empty_array(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_script([])

    def test_script_rejects_extra_entry_fields(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_script(
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "The door opened.",
                        "instruct": "Neutral narration.",
                        "emotion": "neutral",
                    }
                ]
            )


class AliasContractTests(unittest.TestCase):
    def test_valid_alias_map(self) -> None:
        result = validate_alias_map(
            {
                "DOCTOR": "THE DOCTOR",
                "BENNY": "BERNICE SUMMERFIELD",
            }
        )

        self.assertEqual(result["DOCTOR"], "THE DOCTOR")

    def test_alias_map_rejects_non_string_target(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_alias_map({"DOCTOR": ["THE DOCTOR"]})


class AdvancedDiscoveryTests(unittest.TestCase):
    def test_valid_dynamic_speaker_map(self) -> None:
        result = validate_advanced_discovery(
            {
                "BERNICE": {
                    "aliases": ["BENNY"],
                    "features": ["Archaeologist"],
                    "personality": ["Dry wit"],
                    "voice_clues": ["Adult British woman"],
                    "relationships": ["Travels with the Doctor"],
                    "evidence": [
                        {
                            "entry_index": 4,
                            "quote": "That is not remotely reassuring.",
                        }
                    ],
                    "sample_lines": [
                        "That is not remotely reassuring."
                    ],
                }
            }
        )

        self.assertIn("BERNICE", result)
        self.assertEqual(
            result["BERNICE"]["aliases"],
            ["BENNY"],
        )

    def test_missing_optional_lists_become_empty(self) -> None:
        result = validate_advanced_discovery(
            {
                "THE DOCTOR": {
                    "sample_lines": ["No. It rarely is."],
                }
            }
        )

        self.assertEqual(
            result["THE DOCTOR"]["features"],
            [],
        )

    def test_unexpected_discovery_field_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_advanced_discovery(
                {
                    "THE DOCTOR": {
                        "sample_lines": ["No."],
                        "invented_field": ["invalid"],
                    }
                }
            )


class ContractDispatchTests(unittest.TestCase):
    def test_known_schema(self) -> None:
        schema = get_schema("persona")
        self.assertEqual(schema["type"], "object")

    def test_unknown_schema_name(self) -> None:
        with self.assertRaises(ValueError):
            get_schema("does_not_exist")

    def test_validate_contract_dispatch(self) -> None:
        result = validate_contract(
            "persona",
            {
                "description": "A mature British baritone.",
                "ref_text": "The matter is not settled.",
            },
        )

        self.assertEqual(
            result["description"],
            "A mature British baritone.",
        )


if __name__ == "__main__":
    unittest.main()
