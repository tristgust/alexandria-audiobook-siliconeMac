from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from chatgpt_handoff import HandoffValidationError
from llm_schemas import ContractValidationError, validate_contract
from task_bundles import (
    create_completed_task_bundle,
    create_task_bundle,
    inspect_completed_task_bundle,
)


def evidence(quote: str, start: int, category: str) -> dict:
    return {
        "quote": quote,
        "start_char": start,
        "end_char": start + len(quote),
        "category": category,
        "confidence": 0.98,
        "basis": "explicit",
    }


def roster_entity() -> dict:
    quote = "Clara spoke softly to Edmund."
    return {
        "identity_seed": "clara-leighton",
        "canonical_name": "Clara Leighton",
        "display_name": "Clara Leighton",
        "entity_kind": "character",
        "speaking_status": "speaker",
        "titles": [],
        "aliases": ["CLARA"],
        "nicknames": [],
        "pronouns": [],
        "species": [],
        "relationships": ["Sister of Edmund Fairfax"],
        "voice_clues": ["Speaks softly"],
        "sample_lines": ["I knew the letter would arrive."],
        "confidence": 0.98,
        "resolution_status": "resolved",
        "unresolved_questions": [],
        "evidence": [evidence(quote, 0, "relationship")],
    }


def trait(value: str, basis: str = "casting_recommendation") -> dict:
    return {
        "value": value,
        "basis": basis,
        "evidence_quotes": (
            ["Clara spoke softly to Edmund."]
            if basis in {"explicit", "inferred"}
            else []
        ),
    }


def complete_result() -> dict:
    return {
        "selected_sections": {
            "roster_and_relationships": True,
            "voice_personas_and_designs": True,
            "visual_dossiers": True,
        },
        "roster": {
            "entities": [roster_entity()],
            "warnings": [],
        },
        "voice_dossiers": {
            "voices": [
                {
                    "speaker": "CLARA",
                    "persona_summary": "Restrained, observant, and privately resolute.",
                    "designed_voice_description": (
                        "A clear adult alto with light breath texture, compact resonance, "
                        "measured cadence, and restrained warmth."
                    ),
                    "ref_text": "I knew the letter would arrive.",
                    "vocal_age_impression": trait("Adult", "casting_recommendation"),
                    "pitch": trait("Mid-low alto", "casting_recommendation"),
                    "weight_and_resonance": trait("Compact, lightly chest-led resonance"),
                    "texture_and_timbre": trait("Clear with a faint breath texture"),
                    "accent_and_language": trait("Neutral English-language casting"),
                    "cadence_and_rhythm": trait("Measured and deliberate"),
                    "energy_range": trait("Quiet to firmly projected"),
                    "emotional_range": trait("Restrained warmth through controlled alarm"),
                    "casting_guidance": trait("Cast for intelligence rather than fragility"),
                    "uncertainties": ["No source-supported regional accent."],
                }
            ],
            "warnings": [],
        },
        "visual_observations": {
            "observations": [
                {
                    "observation_id": "visual-clara-1",
                    "character_id": "clara-leighton",
                    "category": "body_features",
                    "detail": "No stable body description is supplied.",
                    "scope": "stable",
                    "certainty": 0.95,
                    "basis": "inferred",
                    "quote": "Clara spoke softly to Edmund.",
                    "start_char": 0,
                    "end_char": len("Clara spoke softly to Edmund."),
                }
            ],
            "warnings": [],
        },
        "visual_dossiers": {
            "characters": [
                {
                    "character_id": "clara-leighton",
                    "profile": {
                        "height_and_build": [],
                        "apparent_age": [],
                        "face_and_features": [],
                        "eyes": [],
                        "hair": [],
                        "skin_and_complexion": [],
                        "distinguishing_marks": [],
                        "clothing": [],
                        "accessories_weapons_equipment": [],
                        "posture_and_movement": [],
                        "body_features": [
                            {
                                "detail": "No stable body description is supplied.",
                                "certainty": 0.95,
                                "observation_ids": ["visual-clara-1"],
                            }
                        ],
                        "species_or_ancestry": [],
                        "nonhuman_anatomy": [],
                        "cybernetics_or_modifications": [],
                    },
                    "variants": [],
                    "conflicts": [],
                    "unknowns": [
                        {"category": "face_and_features", "question": "Facial traits are not described."}
                    ],
                }
            ],
            "warnings": [],
        },
        "warnings": [],
    }


class CompleteCastDossierContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_task(self):
        return create_task_bundle(
            output_dir=self.root,
            task_type="complete_cast_dossier",
            input_payload={
                "requested_sections": {
                    "roster_and_relationships": True,
                    "voice_personas_and_designs": True,
                    "visual_dossiers": True,
                },
                "source_text": "Clara spoke softly to Edmund.",
                "source_context": {"fingerprint": "a" * 64},
                "script_speakers": [
                    {
                        "speaker": "CLARA",
                        "sample_lines": ["I knew the letter would arrive."],
                    }
                ],
            },
            application_version="test",
            source_fingerprint="a" * 64,
            artifact_fingerprints={"annotated_script": "b" * 64},
            created_at_utc="2026-07-27T12:00:00Z",
        )

    def test_complete_cast_bundle_includes_readable_voice_guidance(self) -> None:
        task = self.create_task()
        with zipfile.ZipFile(task["path"]) as archive:
            names = set(archive.namelist())
            instructions = archive.read("instructions.md").decode("utf-8")
        self.assertTrue(
            {
                "guidance/persona.md",
                "guidance/voice-identity.md",
                "guidance/line-direction.md",
                "guidance/cast-dossier.md",
                "guidance/nonhuman-speakers.md",
            }.issubset(names)
        )
        self.assertIn(
            "Use `guidance/persona.md` to write `persona_summary`",
            instructions,
        )
        self.assertIn(
            "Use `guidance/voice-identity.md` to write `designed_voice_description`",
            instructions,
        )
        self.assertIn(
            "momentary emotion and one-line delivery belong in Script directions",
            instructions,
        )

    def test_valid_complete_cast_dossier(self) -> None:
        normalized = validate_contract("complete_cast_dossier", complete_result())
        self.assertEqual(normalized["voice_dossiers"]["voices"][0]["speaker"], "CLARA")
        self.assertEqual(normalized["roster"]["entities"][0]["relationships"], ["Sister of Edmund Fairfax"])

    def test_unsupported_inferred_voice_trait_is_demoted_with_warning(self) -> None:
        result = complete_result()
        result["voice_dossiers"]["voices"][0]["pitch"] = {
            "value": "Low",
            "basis": "inferred",
            "evidence_quotes": [],
        }
        normalized = validate_contract("complete_cast_dossier", result)
        pitch = normalized["voice_dossiers"]["voices"][0]["pitch"]
        self.assertEqual(pitch["basis"], "casting_recommendation")
        self.assertEqual(pitch["evidence_quotes"], [])
        self.assertTrue(
            any(
                "retained the text as a casting recommendation" in warning
                for warning in normalized["voice_dossiers"]["warnings"]
            )
        )

    def test_voice_identity_matches_roster_with_leading_article(self) -> None:
        result = complete_result()
        result["selected_sections"]["visual_dossiers"] = False
        result["visual_observations"] = None
        result["visual_dossiers"] = None
        entity = result["roster"]["entities"][0]
        entity["identity_seed"] = "the_clara"
        entity["canonical_name"] = "The Clara"
        entity["display_name"] = "The Clara"
        entity["aliases"] = []
        normalized = validate_contract("complete_cast_dossier", result)
        self.assertEqual(
            normalized["voice_dossiers"]["voices"][0]["speaker"],
            "CLARA",
        )

    def test_visual_dossier_cannot_reference_unknown_observation(self) -> None:
        result = complete_result()
        result["visual_dossiers"]["characters"][0]["profile"]["body_features"][0][
            "observation_ids"
        ] = ["missing-observation"]
        with self.assertRaises(ContractValidationError):
            validate_contract("complete_cast_dossier", result)

    def test_completed_bundle_requires_every_exported_speaker(self) -> None:
        task = self.create_task()
        result = complete_result()
        result["voice_dossiers"]["voices"] = []
        with self.assertRaises(HandoffValidationError) as error:
            create_completed_task_bundle(
                task_bundle_path=task["path"],
                result=result,
                output_path=self.root / "incomplete.zip",
            )
        self.assertEqual(error.exception.code, "cast_dossier_voice_catalog_incomplete")

    def test_complete_bundle_round_trip(self) -> None:
        task = self.create_task()
        completed = self.root / "complete.alexandria-completed-task.zip"
        create_completed_task_bundle(
            task_bundle_path=task["path"],
            result=complete_result(),
            output_path=completed,
            completed_at_utc="2026-07-27T13:00:00Z",
        )
        inspected = inspect_completed_task_bundle(
            path=completed,
            current_source_fingerprint="a" * 64,
            current_artifact_fingerprints={"annotated_script": "b" * 64},
        )
        self.assertEqual(inspected["task_type"], "complete_cast_dossier")
        self.assertEqual(len(inspected["result"]["visual_dossiers"]["characters"]), 1)


if __name__ == "__main__":
    unittest.main()
