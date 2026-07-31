from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cast_dossier_package import (
    CastDossierPackageError,
    _identity_index,
    _import_visual_package,
    _resolve_identity,
    inspect_visual_identity_review,
)
from character_roster import save_character_roster
from generation_state import fingerprint_text
from visual_discovery import load_visual_discovery_state
from voice_identity_context import build_script_speaker_roster


PROFILE_BUCKETS = (
    "height_and_build",
    "apparent_age",
    "face_and_features",
    "eyes",
    "hair",
    "skin_and_complexion",
    "distinguishing_marks",
    "clothing",
    "accessories_weapons_equipment",
    "posture_and_movement",
    "body_features",
    "species_or_ancestry",
    "nonhuman_anatomy",
    "cybernetics_or_modifications",
)


class CastDossierVisualPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_text = (
            'Clara wore a red coat. "Hello," Clara said. '
            'Edmund waited. "Indeed," Edmund said.'
        )
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.source_text, encoding="utf-8")
        self.script_path = self.root / "annotated_script.json"
        self.script_path.write_text(
            json.dumps(
                [
                    {
                        "speaker": "CLARA",
                        "text": "Hello,",
                        "instruct": "Quietly.",
                    },
                    {
                        "speaker": "EDMUND",
                        "text": "Indeed,",
                        "instruct": "Calmly.",
                    },
                ]
            ),
            encoding="utf-8",
        )
        roster = build_script_speaker_roster(
            root_dir=self.root,
            source_text=self.source_text,
            current_source_fingerprint=fingerprint_text(self.source_text),
            script_path=self.script_path,
        )
        self.roster = save_character_roster(
            roster,
            self.root / "character_roster.json",
            source_text=None,
            expected_status="approved",
        )
        self.source = {
            "path": str(self.source_path),
            "basename": self.source_path.name,
            "fingerprint": fingerprint_text(self.source_text),
            "character_count": len(self.source_text),
        }
        start = self.source_text.index("red coat")
        profile = {key: [] for key in PROFILE_BUCKETS}
        profile["clothing"] = []
        unknown_profile = {key: [] for key in PROFILE_BUCKETS}
        self.package = {
            "parent_candidate_id": "structured_fixture_parent",
            "parent_result_fingerprint": "a" * 64,
            "visual_observations": {
                "observations": [
                    {
                        "observation_id": "chatgpt-visual-1",
                        "character_id": "CLARA",
                        "category": "clothing",
                        "detail": "Wears a red coat in this scene.",
                        "scope": "scene_specific",
                        "certainty": 1.0,
                        "basis": "explicit",
                        "quote": "red coat",
                        "start_char": start,
                        "end_char": start + len("red coat"),
                    }
                ],
                "warnings": [],
            },
            "visual_dossiers": {
                "characters": [
                    {
                        "character_id": "CLARA",
                        "profile": profile,
                        "variants": [
                            {
                                "label": "Red coat scene",
                                "scope": "scene_specific",
                                "details": ["Wears a red coat."],
                                "observation_ids": ["chatgpt-visual-1"],
                            }
                        ],
                        "conflicts": [],
                        "unknowns": [
                            {
                                "category": "face_and_features",
                                "question": "Facial details are not supplied.",
                            }
                        ],
                    },
                    {
                        "character_id": "EDMUND",
                        "profile": unknown_profile,
                        "variants": [],
                        "conflicts": [],
                        "unknowns": [
                            {
                                "category": "clothing",
                                "question": "No source-supported clothing details were found.",
                            },
                            {
                                "category": "face_and_features",
                                "question": "No source-supported facial details were found.",
                            },
                        ],
                    },
                ],
                "warnings": [],
            },
        }
        self.state_path = self.root / "persona_visual_state.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_identity_resolution_prefers_one_resolved_match_over_unresolved_duplicate(self) -> None:
        roster = {
            "entries": [
                {
                    "id": "character_resolved_narrator",
                    "canonical_name": "NARRATOR",
                    "display_name": "Narrator",
                    "resolution_status": "resolved",
                },
                {
                    "id": "character_unresolved_narrator",
                    "canonical_name": "Narrator",
                    "display_name": "Narrator",
                    "resolution_status": "unresolved",
                },
                {
                    "id": "character_bernice",
                    "canonical_name": "BERNICE",
                    "display_name": "Bernice",
                    "aliases": ["Bernice Summerfield"],
                    "resolution_status": "resolved",
                },
                {
                    "id": "character_script_aubertides",
                    "canonical_name": "AUBERTIDES",
                    "display_name": "AUBERTIDES",
                    "resolution_status": "resolved",
                },
                {
                    "id": "character_group_aubertides",
                    "canonical_name": "The Aubertides",
                    "display_name": "The Aubertides",
                    "resolution_status": "resolved",
                },
                {
                    "id": "character_doctor",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "THE DOCTOR",
                    "aliases": ["THE TENTH DOCTOR"],
                    "resolution_status": "resolved",
                },
                {
                    "id": "character_tenth_doctor",
                    "canonical_name": "THE TENTH DOCTOR",
                    "display_name": "THE TENTH DOCTOR",
                    "resolution_status": "resolved",
                },
            ]
        }
        index, ambiguous = _identity_index(roster)
        self.assertNotIn("narrator", ambiguous)
        self.assertEqual(
            _resolve_identity("NARRATOR", index=index, ambiguous=ambiguous),
            "character_resolved_narrator",
        )
        self.assertEqual(
            _resolve_identity(
                "bernice_summerfield",
                index=index,
                ambiguous=ambiguous,
            ),
            "character_bernice",
        )
        self.assertEqual(
            _resolve_identity("AUBERTIDES", index=index, ambiguous=ambiguous),
            "character_script_aubertides",
        )
        self.assertEqual(
            _resolve_identity("the_aubertides", index=index, ambiguous=ambiguous),
            "character_group_aubertides",
        )
        self.assertEqual(
            _resolve_identity(
                "THE TENTH DOCTOR",
                index=index,
                ambiguous=ambiguous,
            ),
            "character_tenth_doctor",
        )

    def test_visual_package_writes_native_dossiers(self) -> None:
        result = _import_visual_package(
            root=self.root,
            package=self.package,
            source_snapshot=self.source,
            source_text=self.source_text,
            roster=self.roster,
            visual_state_path=self.state_path,
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["observation_count"], 1)
        self.assertEqual(result["written_count"], 2)
        self.assertIsNone(load_visual_discovery_state(self.state_path))
        ids = {
            entry["canonical_name"]: entry["id"]
            for entry in self.roster["entries"]
        }
        references = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((self.root / "persona_refs").glob("*.json"))
        ]
        self.assertEqual(len(references), 2)
        by_id = {item["roster_entry_id"]: item for item in references}
        clara = by_id[ids["CLARA"]]["visual"]
        self.assertEqual(clara["variants"][0]["scope"], "scene_specific")
        self.assertIn("red coat", clara["image_prompt_summary"].lower())
        edmund = by_id[ids["EDMUND"]]["visual"]
        self.assertEqual(edmund["profile"], {key: [] for key in PROFILE_BUCKETS})
        self.assertTrue(edmund["unknowns"])

    def test_visual_package_applies_explicit_crosswalk_and_preserves_exclusions(self) -> None:
        ids = {
            entry["canonical_name"]: entry["id"]
            for entry in self.roster["entries"]
        }
        self.package["visual_observations"]["observations"][0][
            "character_id"
        ] = "clara_in_memory"
        characters = self.package["visual_dossiers"]["characters"]
        characters[0]["character_id"] = "clara_in_memory"
        characters[1]["character_id"] = "unnamed_child"

        result = _import_visual_package(
            root=self.root,
            package=self.package,
            source_snapshot=self.source,
            source_text=self.source_text,
            roster=self.roster,
            visual_state_path=self.state_path,
            identity_crosswalk={"clara_in_memory": ids["CLARA"]},
            excluded_identity_keys={"unnamed_child"},
        )

        self.assertEqual(result["identity_crosswalk"], {
            "clara_in_memory": ids["CLARA"],
        })
        self.assertEqual(result["excluded_identity_keys"], ["unnamed_child"])
        self.assertEqual(result["written_count"], 1)
        self.assertIsNone(load_visual_discovery_state(self.state_path))
        references = list((self.root / "persona_refs").glob("*.json"))
        self.assertEqual(len(references), 1)
        reference = json.loads(references[0].read_text(encoding="utf-8"))
        self.assertEqual(reference["roster_entry_id"], ids["CLARA"])

    def test_visual_identity_review_suggests_match_but_preserves_exclusion_default(self) -> None:
        ids = {
            entry["canonical_name"]: entry["id"]
            for entry in self.roster["entries"]
        }
        self.package["visual_observations"]["observations"][0][
            "character_id"
        ] = "clara_in_memory"
        self.package["visual_dossiers"]["characters"][0][
            "character_id"
        ] = "clara_in_memory"
        self.roster["excluded_entities"] = [
            {"name": "Memory Clara", "reason": "Reviewed exclusion", "evidence": []}
        ]

        review = inspect_visual_identity_review(
            package=self.package,
            roster=self.roster,
            roster_entities=[
                {
                    "identity_seed": "clara_in_memory",
                    "canonical_name": "Unidentified Clara in memory",
                    "display_name": "Memory Clara",
                    "aliases": ["CLARA"],
                }
            ],
        )

        self.assertTrue(review["required"])
        self.assertEqual(len(review["issues"]), 1)
        issue = review["issues"][0]
        self.assertEqual(issue["identity_key"], "clara_in_memory")
        self.assertEqual(issue["suggested_entry_id"], ids["CLARA"])
        self.assertTrue(issue["excluded_during_roster_review"])
        self.assertEqual(
            {entry["id"] for entry in review["approved_entries"]},
            set(ids.values()),
        )

    def test_visual_package_never_overwrites_existing_review(self) -> None:
        _import_visual_package(
            root=self.root,
            package=self.package,
            source_snapshot=self.source,
            source_text=self.source_text,
            roster=self.roster,
            visual_state_path=self.state_path,
        )
        with self.assertRaises(CastDossierPackageError) as error:
            _import_visual_package(
                root=self.root,
                package=self.package,
                source_snapshot=self.source,
                source_text=self.source_text,
                roster=self.roster,
                visual_state_path=self.state_path,
            )
        self.assertEqual(error.exception.code, "visual_work_in_progress")


if __name__ == "__main__":
    unittest.main()
