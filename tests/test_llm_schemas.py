from __future__ import annotations

import unittest

from llm_schemas import (
    ContractValidationError,
    get_schema,
    validate_advanced_discovery,
    validate_alias_map,
    validate_contract,
    validate_persona,
    validate_roster_discovery,
    validate_roster_reconciliation,
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


class RosterDiscoveryContractTests(unittest.TestCase):
    @staticmethod
    def valid_entity() -> dict:
        return {
            "identity_seed": "passage-1:doctor-at-10",
            "canonical_name": "THE DOCTOR",
            "display_name": "The Doctor",
            "entity_kind": "character",
            "speaking_status": "speaker",
            "titles": ["Doctor"],
            "aliases": [],
            "nicknames": [],
            "pronouns": ["he/him"],
            "species": ["Time Lord"],
            "relationships": [],
            "voice_clues": ["Scottish burr"],
            "sample_lines": [" No. It rarely is."],
            "confidence": 0.95,
            "resolution_status": "resolved",
            "unresolved_questions": [],
            "evidence": [
                {
                    "quote": " The Doctor",
                    "start_char": 9,
                    "end_char": 20,
                    "category": "name",
                    "confidence": 1.0,
                    "basis": "explicit",
                }
            ],
        }

    def test_valid_discovery_preserves_exact_text(self) -> None:
        result = validate_roster_discovery(
            {
                "entities": [self.valid_entity()],
                "warnings": [],
            }
        )
        self.assertEqual(
            result["entities"][0]["evidence"][0]["quote"],
            " The Doctor",
        )
        self.assertEqual(
            result["entities"][0]["sample_lines"][0],
            " No. It rarely is.",
        )

    def test_discovery_rejects_unknown_fields(self) -> None:
        entity = self.valid_entity()
        entity["invented"] = True
        with self.assertRaises(ContractValidationError):
            validate_roster_discovery(
                {"entities": [entity], "warnings": []}
            )

    def test_discovery_rejects_invalid_enum(self) -> None:
        entity = self.valid_entity()
        entity["entity_kind"] = "spaceship"
        with self.assertRaises(ContractValidationError):
            validate_roster_discovery(
                {"entities": [entity], "warnings": []}
            )


class RosterReconciliationContractTests(unittest.TestCase):
    @staticmethod
    def valid_value() -> dict:
        return {
            "entries": [
                {
                    "identity_seed": "doctor",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "observation_ids": ["observation_a"],
                    "confidence": 0.98,
                    "resolution_status": "resolved",
                    "possible_duplicate_seeds": [],
                    "mistaken_merge_risk": False,
                    "unresolved_questions": [],
                }
            ],
            "duplicate_candidates": [],
            "excluded_observation_ids": [],
            "warnings": [],
        }

    def test_valid_reconciliation(self) -> None:
        result = validate_roster_reconciliation(
            self.valid_value()
        )
        self.assertEqual(
            result["entries"][0]["observation_ids"],
            ["observation_a"],
        )

    def test_reconciliation_rejects_duplicate_observation_ids(self) -> None:
        value = self.valid_value()
        value["entries"][0]["observation_ids"] = [
            "observation_a",
            "observation_a",
        ]
        with self.assertRaisesRegex(
            ContractValidationError,
            "must not contain duplicates",
        ):
            validate_roster_reconciliation(value)

    def test_duplicate_candidate_requires_evidence_observations(self) -> None:
        value = self.valid_value()
        value["duplicate_candidates"] = [
            {
                "identity_seeds": ["doctor", "doctor_two"],
                "reason": "Names may refer to one person.",
                "confidence": 0.6,
                "observation_ids": [],
            }
        ]
        with self.assertRaisesRegex(
            ContractValidationError,
            "must not be empty",
        ):
            validate_roster_reconciliation(value)


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
