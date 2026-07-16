from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from character_visuals import (
    PROFILE_BUCKETS,
    CharacterVisualError,
    CharacterVisualValidationError,
    base_persona_reference,
    build_image_prompt_summary,
    build_visual_dossier,
    build_visual_status,
    inspect_visual_dossier,
    persona_reference_path,
    sanitize_character_filename,
    validate_visual_dossier,
    write_visual_dossier,
)


class CharacterVisualFixture:
    SOURCE_TEXT = (
        "The Khepri unfolded four delicate arms. Its eyes were "
        "chrome-green. Later it wore a torn red cloak."
    )

    @classmethod
    def observation(
        cls,
        *,
        observation_id: str,
        category: str,
        quote: str,
        detail: str,
        scope: str = "stable",
        certainty: float = 0.9,
        basis: str = "explicit",
    ) -> dict:
        start = cls.SOURCE_TEXT.index(quote)
        return {
            "observation_id": observation_id,
            "category": category,
            "detail": detail,
            "scope": scope,
            "certainty": certainty,
            "basis": basis,
            "source_location": (
                f"characters {start}-{start + len(quote)}"
            ),
            "start_char": start,
            "end_char": start + len(quote),
            "passage_index": 1,
            "quote": quote,
        }

    @classmethod
    def empty_profile(cls) -> dict[str, list[dict]]:
        return {bucket: [] for bucket in PROFILE_BUCKETS}

    @classmethod
    def dossier(cls) -> dict:
        anatomy = cls.observation(
            observation_id="visual_anatomy",
            category="nonhuman_anatomy",
            quote="four delicate arms",
            detail="four delicate arms",
        )
        eyes = cls.observation(
            observation_id="visual_eyes",
            category="eyes",
            quote="chrome-green",
            detail="chrome-green eyes",
        )
        cloak = cls.observation(
            observation_id="visual_cloak",
            category="clothing",
            quote="torn red cloak",
            detail="a torn red cloak",
            scope="scene_specific",
        )
        profile = cls.empty_profile()
        profile["nonhuman_anatomy"] = [
            {
                "detail": "four delicate arms",
                "certainty": 0.9,
                "observation_ids": ["visual_anatomy"],
            }
        ]
        profile["eyes"] = [
            {
                "detail": "chrome-green eyes",
                "certainty": 0.9,
                "observation_ids": ["visual_eyes"],
            }
        ]
        return build_visual_dossier(
            observations=[anatomy, eyes, cloak],
            profile=profile,
            variants=[
                {
                    "label": "Red-cloak scene",
                    "scope": "scene_specific",
                    "details": ["a torn red cloak"],
                    "observation_ids": ["visual_cloak"],
                }
            ],
            unknowns=[
                {
                    "category": "hair",
                    "question": "No source-backed hair description.",
                }
            ],
            source_text=cls.SOURCE_TEXT,
        )


class CharacterVisualContractTests(
    unittest.TestCase,
    CharacterVisualFixture,
):
    def test_safe_character_filename_matches_persona_convention(self):
        self.assertEqual(
            sanitize_character_filename("The Khepri"),
            "the_khepri",
        )
        self.assertEqual(
            sanitize_character_filename("Lady Geneviève"),
            "lady_geneviève",
        )

    def test_disabled_base_reference_has_no_visual_field(self):
        reference = base_persona_reference(
            name="THE KHEPRI",
            aliases=["KHEPRI"],
        )
        self.assertNotIn("visual", reference)

    def test_valid_dossier_has_every_required_bucket(self):
        dossier = self.dossier()
        self.assertEqual(
            set(dossier["profile"]),
            set(PROFILE_BUCKETS),
        )
        self.assertEqual(
            dossier["schema_version"],
            1,
        )
        self.assertIn(
            "chrome-green eyes",
            dossier["image_prompt_summary"],
        )
        self.assertIn(
            "four delicate arms",
            dossier["image_prompt_summary"],
        )
        self.assertIn(
            "Red-cloak scene",
            dossier["image_prompt_summary"],
        )

    def test_missing_profile_bucket_is_rejected(self):
        profile = self.empty_profile()
        profile.pop("hair")
        with self.assertRaisesRegex(
            CharacterVisualValidationError,
            "missing hair",
        ):
            build_visual_dossier(
                observations=[],
                profile=profile,
            )

    def test_observation_quote_must_match_source_offsets(self):
        dossier = self.dossier()
        dossier["observations"][0]["quote"] = (
            "five delicate arms"
        )
        with self.assertRaisesRegex(
            CharacterVisualValidationError,
            "does not match",
        ):
            validate_visual_dossier(
                dossier,
                source_text=self.SOURCE_TEXT,
            )

    def test_profile_fact_must_reference_same_category(self):
        dossier = self.dossier()
        dossier["profile"]["hair"] = [
            {
                "detail": "chrome-green hair",
                "certainty": 0.8,
                "observation_ids": ["visual_eyes"],
            }
        ]
        dossier["image_prompt_summary"] = (
            build_image_prompt_summary(
                profile=dossier["profile"],
                variants=dossier["variants"],
                conflicts=dossier["conflicts"],
            )
        )
        with self.assertRaisesRegex(
            CharacterVisualValidationError,
            "wrong category",
        ):
            validate_visual_dossier(dossier)

    def test_summary_tampering_is_rejected(self):
        dossier = self.dossier()
        dossier["image_prompt_summary"] = (
            "A generic alien with cinematic lighting."
        )
        with self.assertRaisesRegex(
            CharacterVisualValidationError,
            "deterministic summary",
        ):
            validate_visual_dossier(dossier)

    def test_conflicted_category_is_not_flattened_into_summary(self):
        observations = [
            self.observation(
                observation_id="eyes_green",
                category="eyes",
                quote="chrome-green",
                detail="chrome-green eyes",
            ),
            self.observation(
                observation_id="eyes_green_2",
                category="eyes",
                quote="Its eyes",
                detail="indigo eyes",
                certainty=0.4,
                basis="inferred",
            ),
        ]
        profile = self.empty_profile()
        profile["eyes"] = [
            {
                "detail": "chrome-green eyes",
                "certainty": 0.9,
                "observation_ids": ["eyes_green"],
            },
            {
                "detail": "indigo eyes",
                "certainty": 0.4,
                "observation_ids": ["eyes_green_2"],
            },
        ]
        dossier = build_visual_dossier(
            observations=observations,
            profile=profile,
            conflicts=[
                {
                    "category": "eyes",
                    "details": [
                        "chrome-green eyes",
                        "indigo eyes",
                    ],
                    "observation_ids": [
                        "eyes_green",
                        "eyes_green_2",
                    ],
                }
            ],
            source_text=self.SOURCE_TEXT,
        )
        summary = dossier["image_prompt_summary"]
        self.assertNotIn("chrome-green eyes", summary)
        self.assertNotIn("indigo eyes", summary)
        self.assertIn(
            "Conflicting source descriptions exist for eyes",
            summary,
        )


class CharacterVisualStorageTests(
    unittest.TestCase,
    CharacterVisualFixture,
):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.refs = self.root / "persona_refs"
        self.refs.mkdir()
        self.path = persona_reference_path(
            self.refs,
            "THE KHEPRI",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_write_preserves_existing_persona_fields(self):
        existing = {
            "name": "THE KHEPRI",
            "aliases": ["KHEPRI"],
            "features": ["Existing feature"],
            "personality": ["Existing personality"],
            "voice_clues": ["Existing voice"],
            "relationships": [],
            "sample_lines": [],
            "observations": [{"batch": 1}],
            "custom_field": {"preserve": True},
            "updated_at": 1,
        }
        self.path.write_text(
            json.dumps(existing),
            encoding="utf-8",
        )
        updated = write_visual_dossier(
            persona_ref_path=self.path,
            visual=self.dossier(),
            character_name="THE KHEPRI",
            aliases=["KHEPRI"],
            source_text=self.SOURCE_TEXT,
        )
        self.assertEqual(
            updated["features"],
            ["Existing feature"],
        )
        self.assertEqual(
            updated["custom_field"],
            {"preserve": True},
        )
        self.assertEqual(
            updated["visual"],
            self.dossier(),
        )

    def test_existing_visual_requires_explicit_replacement(self):
        write_visual_dossier(
            persona_ref_path=self.path,
            visual=self.dossier(),
            character_name="THE KHEPRI",
            source_text=self.SOURCE_TEXT,
        )
        before = self.path.read_bytes()
        with self.assertRaisesRegex(
            CharacterVisualError,
            "already exists",
        ):
            write_visual_dossier(
                persona_ref_path=self.path,
                visual=self.dossier(),
                character_name="THE KHEPRI",
                source_text=self.SOURCE_TEXT,
            )
        self.assertEqual(self.path.read_bytes(), before)

        replaced = write_visual_dossier(
            persona_ref_path=self.path,
            visual=self.dossier(),
            character_name="THE KHEPRI",
            source_text=self.SOURCE_TEXT,
            replace_existing=True,
        )
        self.assertIn("visual", replaced)

    def test_absent_status_does_not_create_persona_reference(self):
        status = inspect_visual_dossier(
            persona_ref_path=self.path,
            source_text=self.SOURCE_TEXT,
        )
        self.assertEqual(status["status"], "absent")
        self.assertFalse(self.path.exists())

    def test_visual_status_reports_roster_entries(self):
        write_visual_dossier(
            persona_ref_path=self.path,
            visual=self.dossier(),
            character_name="THE KHEPRI",
            source_text=self.SOURCE_TEXT,
        )
        roster = {
            "entries": [
                {
                    "id": "character_khepri",
                    "canonical_name": "THE KHEPRI",
                    "display_name": "The Khepri",
                    "entity_kind": "creature",
                },
                {
                    "id": "character_roz",
                    "canonical_name": "ROZ FORRESTER",
                    "display_name": "Roz Forrester",
                    "entity_kind": "character",
                },
            ]
        }
        status = build_visual_status(
            approved_roster=roster,
            persona_refs_dir=self.refs,
            source_text=self.SOURCE_TEXT,
        )
        self.assertTrue(status["available"])
        self.assertEqual(status["complete_count"], 1)
        self.assertEqual(status["absent_count"], 1)
        self.assertEqual(status["invalid_count"], 0)
        by_id = {
            item["character_id"]: item
            for item in status["entries"]
        }
        self.assertEqual(
            by_id["character_khepri"]["status"],
            "complete",
        )
        self.assertEqual(
            by_id["character_roz"]["status"],
            "absent",
        )

    def test_no_roster_has_disabled_unavailable_status(self):
        status = build_visual_status(
            approved_roster=None,
            persona_refs_dir=self.refs,
        )
        self.assertFalse(status["available"])
        self.assertEqual(status["entries"], [])
        self.assertFalse(any(self.refs.iterdir()))


if __name__ == "__main__":
    unittest.main()
