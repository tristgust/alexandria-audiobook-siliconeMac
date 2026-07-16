from __future__ import annotations

import unittest

from llm_schemas import (
    ContractValidationError,
    get_schema,
    validate_contract,
)


class RosterDiscoveryContractTests(unittest.TestCase):
    def evidence(self):
        return {
            "quote": "The Doctor",
            "start_char": 0,
            "end_char": 10,
            "category": "name",
            "confidence": 1.0,
            "basis": "explicit",
        }

    def entity(self):
        return {
            "identity_seed": "THE DOCTOR",
            "canonical_name": "THE DOCTOR",
            "display_name": "The Doctor",
            "entity_kind": "character",
            "speaking_status": "speaker",
            "titles": ["Doctor"],
            "aliases": [],
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "voice_clues": [],
            "sample_lines": [],
            "confidence": 0.95,
            "resolution_status": "resolved",
            "unresolved_questions": [],
            "evidence": [self.evidence()],
        }

    def test_roster_discovery_schema_and_validator(self):
        schema = get_schema("roster_discovery")
        self.assertEqual(schema["type"], "object")
        value = validate_contract(
            "roster_discovery",
            {
                "entities": [self.entity()],
                "warnings": [],
            },
        )
        self.assertEqual(
            value["entities"][0]["canonical_name"],
            "THE DOCTOR",
        )
        self.assertEqual(
            value["entities"][0]["evidence"][0][
                "start_char"
            ],
            0,
        )

    def test_roster_discovery_rejects_bad_evidence(self):
        entity = self.entity()
        entity["evidence"][0]["end_char"] = 0
        with self.assertRaisesRegex(
            ContractValidationError,
            "greater than start_char",
        ):
            validate_contract(
                "roster_discovery",
                {"entities": [entity], "warnings": []},
            )

    def test_roster_discovery_preserves_unnamed_identity(self):
        entity = self.entity()
        entity["identity_seed"] = "unnamed speaker 1"
        entity["canonical_name"] = ""
        entity["display_name"] = "Unnamed speaker near Roz"
        entity["resolution_status"] = "unnamed"
        entity["unresolved_questions"] = [
            "Is this Roz or a separate speaker?"
        ]
        result = validate_contract(
            "roster_discovery",
            {"entities": [entity], "warnings": []},
        )
        self.assertEqual(
            result["entities"][0]["resolution_status"],
            "unnamed",
        )

    def reconciliation(self):
        return {
            "entries": [
                {
                    "identity_seed": "THE DOCTOR",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "observation_ids": ["observation_1"],
                    "confidence": 0.95,
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

    def test_roster_reconciliation_contract(self):
        schema = get_schema("roster_reconciliation")
        self.assertEqual(schema["type"], "object")
        result = validate_contract(
            "roster_reconciliation",
            self.reconciliation(),
        )
        self.assertEqual(
            result["entries"][0]["observation_ids"],
            ["observation_1"],
        )

    def test_reconciliation_requires_distinct_duplicate_seeds(self):
        value = self.reconciliation()
        value["duplicate_candidates"] = [
            {
                "identity_seeds": [
                    "THE DOCTOR",
                    "THE DOCTOR",
                ],
                "reason": "same name",
                "confidence": 0.5,
                "observation_ids": ["observation_1"],
            }
        ]
        with self.assertRaisesRegex(
            ContractValidationError,
            "two distinct values",
        ):
            validate_contract(
                "roster_reconciliation",
                value,
            )


if __name__ == "__main__":
    unittest.main()
