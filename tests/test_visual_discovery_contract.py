from __future__ import annotations

import unittest

from character_visuals import PROFILE_BUCKETS
from llm_schemas import (
    ContractValidationError,
    get_schema,
    validate_contract,
)


class VisualDiscoveryContractTests(unittest.TestCase):
    def observation(self):
        return {
            "character_id": "character_doctor",
            "category": "accessories_weapons_equipment",
            "detail": "a battered hat",
            "scope": "stable",
            "certainty": 0.95,
            "basis": "explicit",
            "quote": "battered hat",
            "start_char": 10,
            "end_char": 22,
        }

    def test_visual_discovery_contract(self):
        schema = get_schema("visual_discovery")
        self.assertEqual(schema["type"], "object")
        result = validate_contract(
            "visual_discovery",
            {
                "observations": [self.observation()],
                "warnings": [],
            },
        )
        self.assertEqual(
            result["observations"][0]["scope"],
            "stable",
        )

    def test_visual_discovery_rejects_unknown_bucket(self):
        observation = self.observation()
        observation["category"] = "vibes"
        with self.assertRaisesRegex(
            ContractValidationError,
            "must be one of",
        ):
            validate_contract(
                "visual_discovery",
                {
                    "observations": [observation],
                    "warnings": [],
                },
            )

    def reconciliation(self):
        profile = {
            bucket: []
            for bucket in PROFILE_BUCKETS
        }
        profile["accessories_weapons_equipment"] = [
            {
                "detail": "a battered hat",
                "certainty": 0.95,
                "observation_ids": ["visual_hat"],
            }
        ]
        return {
            "characters": [
                {
                    "character_id": "character_doctor",
                    "profile": profile,
                    "variants": [],
                    "conflicts": [],
                    "unknowns": [
                        {
                            "category": "hair",
                            "question": "No hair evidence found.",
                        }
                    ],
                }
            ],
            "warnings": [],
        }

    def test_visual_reconciliation_contract(self):
        schema = get_schema("visual_reconciliation")
        self.assertEqual(schema["type"], "object")
        result = validate_contract(
            "visual_reconciliation",
            self.reconciliation(),
        )
        self.assertEqual(
            result["characters"][0]["profile"]
            ["accessories_weapons_equipment"][0]["detail"],
            "a battered hat",
        )

    def test_reconciliation_requires_all_profile_buckets(self):
        value = self.reconciliation()
        value["characters"][0]["profile"].pop("hair")
        with self.assertRaisesRegex(
            ContractValidationError,
            "missing keys.*hair",
        ):
            validate_contract(
                "visual_reconciliation",
                value,
            )

    def test_variant_cannot_use_stable_scope(self):
        value = self.reconciliation()
        value["characters"][0]["variants"] = [
            {
                "label": "Wrong layer",
                "scope": "stable",
                "details": ["a battered hat"],
                "observation_ids": ["visual_hat"],
            }
        ]
        with self.assertRaisesRegex(
            ContractValidationError,
            "must be one of",
        ):
            validate_contract(
                "visual_reconciliation",
                value,
            )


if __name__ == "__main__":
    unittest.main()
