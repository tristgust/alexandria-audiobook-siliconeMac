from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from character_visuals import PROFILE_BUCKETS
from persona_visual import (
    PROFILE_CATEGORIES,
    PersonaVisualValidationError,
    inspect_persona_visual,
    load_persona_ref,
    persona_ref_filename,
    persona_ref_targets,
)


class PersonaVisualCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_compatibility_module_uses_canonical_profile_buckets(self) -> None:
        self.assertEqual(PROFILE_CATEGORIES, PROFILE_BUCKETS)

    def test_filename_and_duplicate_target_rules_match_canonical_storage(self) -> None:
        self.assertEqual(
            persona_ref_filename("THE DOCTOR"),
            "the_doctor.json",
        )
        selected = [
            {
                "entry_id": "character_11111111aaaaaaaa",
                "canonical_name": "THE DOCTOR",
            },
            {
                "entry_id": "character_22222222bbbbbbbb",
                "canonical_name": "THE DOCTOR",
            },
        ]
        targets = persona_ref_targets(
            persona_refs_dir=self.root,
            selected_entries=selected,
            all_entries=selected,
        )
        self.assertNotEqual(
            targets[selected[0]["entry_id"]],
            targets[selected[1]["entry_id"]],
        )
        self.assertEqual(
            targets[selected[0]["entry_id"]].name,
            "the_doctor__11111111.json",
        )
        self.assertEqual(
            targets[selected[1]["entry_id"]].name,
            "the_doctor__22222222.json",
        )

    def test_load_blocks_reference_owned_by_another_roster_entry(self) -> None:
        target = self.root / "the_doctor.json"
        target.write_text(
            json.dumps(
                {
                    "name": "THE DOCTOR",
                    "roster_entry_id": "character_original",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            PersonaVisualValidationError,
            "different roster entry",
        ):
            load_persona_ref(
                target,
                expected_entry_id="character_other",
            )

    def test_inspection_is_model_free_and_reports_absence(self) -> None:
        target = self.root / "roz_forrester.json"
        status = inspect_persona_visual(
            target,
            expected_entry_id="character_roz",
        )
        self.assertEqual(status["status"], "absent")
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
