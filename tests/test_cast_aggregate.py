from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from cast_aggregate import (
    CastAggregateError,
    apply_native_cast_validation,
    build_cast_aggregate,
    filter_cast_aggregate,
    resolve_script_label,
)


class CastAggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.reference = self.root / "references" / "benny.wav"
        self.reference.parent.mkdir()
        self.reference.write_bytes(b"reference-audio")
        self.roster = {
            "entries": [
                {
                    "id": "character_bernice",
                    "canonical_name": "Bernice Summerfield",
                    "display_name": "Bernice Summerfield",
                    "aliases": ["Benny"],
                    "resolution_status": "resolved",
                    "speaking_status": "speaking",
                    "role": "lead",
                    "species": "human",
                },
                {
                    "id": "character_benny_narrator",
                    "canonical_name": "Narrator (Benny)",
                    "display_name": "Narrator (Benny)",
                    "resolution_status": "resolved",
                    "speaking_status": "speaking",
                    "role": "secondary narrator",
                },
                {
                    "id": "character_alton",
                    "canonical_name": "Clive Alton",
                    "display_name": "Clive Alton",
                    "resolution_status": "resolved",
                    "speaking_status": "speaking",
                },
                {
                    "id": "character_aubertides",
                    "canonical_name": "The Aubertides",
                    "display_name": "The Aubertides",
                    "resolution_status": "resolved",
                    "speaking_status": "speaking",
                    "species": "alien collective",
                },
                {
                    "id": "character_town",
                    "canonical_name": "The Town",
                    "display_name": "The Town",
                    "resolution_status": "resolved",
                    "speaking_status": "non-speaking",
                    "role": "setting identity",
                },
            ]
        }
        self.script = [
            {
                "speaker": "BERNICE",
                "text": "I know what I saw.",
                "instruct": "Controlled insistence.",
            },
            {
                "speaker": "NARRATOR (BENNY)",
                "text": "I wrote the date in the margin.",
                "instruct": "Private diary narration.",
            },
            {
                "speaker": "ALTON",
                "text": "That is not possible.",
                "instruct": "Flat disbelief.",
            },
            {
                "speaker": "AUBERTIDES",
                "text": "We remember.",
                "instruct": "Layered collective calm.",
            },
        ]
        self.voice_config = {
            "BERNICE": {
                "type": "custom",
                "voice": "benny-main",
                "description": "Adult woman, brisk and intelligent, dry warmth.",
                "ref_text": "I know what I saw.",
            },
            "NARRATOR (BENNY)": {
                "type": "clone",
                "clone_backend": "voxcpm2",
                "ref_audio": "references/benny.wav",
                "ref_text": "I wrote the date in the margin.",
            },
            "ALTON": {
                "type": "alias",
                "alias_of": "BERNICE",
            },
            "AUBERTIDES": {
                "type": "design",
                "description": "Non-human collective with tightly aligned layered voices.",
                "representative_text": "We remember.",
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build(self, **overrides):
        values = {
            "roster": self.roster,
            "script": self.script,
            "voice_config": self.voice_config,
            "root_dir": self.root,
        }
        values.update(overrides)
        return build_cast_aggregate(**values)

    def test_required_long_name_to_short_label_regressions(self) -> None:
        aggregate = self._build()
        mapped = {
            item["canonical_name"]: item["script_connection"][
                "resolved_script_voice_label"
            ]
            for item in aggregate["characters"]
        }
        self.assertEqual(mapped["Bernice Summerfield"], "BERNICE")
        self.assertEqual(mapped["Narrator (Benny)"], "NARRATOR (BENNY)")
        self.assertEqual(mapped["Clive Alton"], "ALTON")
        self.assertEqual(mapped["The Aubertides"], "AUBERTIDES")

    def test_resolved_cast_has_one_character_list_and_optional_visual_does_not_block(self) -> None:
        aggregate = self._build(
            visual_state={
                "characters": [
                    {
                        "character_id": "character_town",
                        "status": "incompatible",
                        "summary": "Legacy visual dossier needs refresh.",
                        "conflicts": ["Old source fingerprint"],
                    }
                ]
            }
        )
        self.assertEqual(aggregate["summary"]["state"], "complete")
        self.assertTrue(aggregate["summary"]["complete"])
        self.assertEqual(len(aggregate["characters"]), 5)
        self.assertEqual(aggregate["summary"]["required_speaking_count"], 4)
        town = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_town"
        )
        self.assertEqual(town["readiness_state"], "ready")
        self.assertEqual(town["appearance"]["status"], "incompatible")
        self.assertTrue(town["appearance"]["optional"])
        self.assertEqual(town["voice"]["valid"], False)
        self.assertEqual(town["blocker_count"], 0)

    def test_persona_fields_are_exposed_inside_voice_not_as_workflow(self) -> None:
        voice_config = {
            **self.voice_config,
            "BERNICE": {"type": "custom", "voice": "benny-main"},
        }
        aggregate = self._build(
            voice_config=voice_config,
            persona_state={
                "characters": [
                    {
                        "character_id": "character_bernice",
                        "description": "Adult woman, dry and incisive, restrained warmth.",
                        "ref_text": "I know what I saw.",
                        "status": "approved",
                    }
                ]
            },
        )
        bernice = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_bernice"
        )
        self.assertEqual(
            bernice["voice"]["persistent_voice_description"],
            "Adult woman, dry and incisive, restrained warmth.",
        )
        self.assertEqual(
            bernice["voice"]["representative_text"],
            "I know what I saw.",
        )
        self.assertNotIn("persona", bernice)
        self.assertEqual(bernice["readiness_state"], "ready")

    def test_voice_listening_decision_is_exposed_without_blocking_prior_routes(self) -> None:
        (self.root / "voice_route_listening_decisions.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "round_id": "round_1",
                    "completed_at": "2026-08-03T20:17:02.040Z",
                    "review_sha256": "1" * 64,
                    "answer_key_sha256": "2" * 64,
                    "evidence_path": ".omo/evidence/round_1.json",
                    "decisions": {
                        "BERNICE": {
                            "status": "return_to_preparation",
                            "primary_method": None,
                            "primary_candidate_id": None,
                            "summary": "The generalized lane needs stronger identity.",
                            "production_action": "preserve_prior_routes",
                            "preserve_prior_routes": True,
                            "route_key": "sardonic_concern",
                            "approval_tier": None,
                            "evidence_sample_ids": ["BEN01"],
                            "unresolved_requirements": [
                                "Prepare a stronger identity reference."
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        aggregate = self._build()
        bernice = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_bernice"
        )
        decision = bernice["voice"]["listening_decision"]
        self.assertEqual(decision["status"], "return_to_preparation")
        self.assertTrue(decision["preserve_prior_routes"])
        self.assertTrue(bernice["voice"]["valid"])
        alton = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_alton"
        )
        self.assertEqual(
            alton["voice"]["listening_decision"]["status"],
            "return_to_preparation",
        )

    def test_cast_voice_ui_surfaces_listening_decisions(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "pages"
            / "cast_profile_voice_section.js"
        ).read_text(encoding="utf-8")
        self.assertIn("listening_decision", source)
        self.assertIn("Voice lane returned to preparation", source)

    def test_complete_cast_voice_dossier_is_exposed_without_assigning_it(self) -> None:
        aggregate = self._build(
            persona_state={
                "voices": [
                    {
                        "character_id": "character_bernice",
                        "speaker": "BERNICE",
                        "persona_summary": "Dry, incisive, and privately warm.",
                        "designed_voice_description": (
                            "A clear adult alto with compact resonance and restrained warmth."
                        ),
                        "pitch": {
                            "value": "Mid-low alto",
                            "basis": "casting_recommendation",
                            "evidence_quotes": [],
                        },
                        "casting_guidance": {
                            "value": "Prioritize intelligence over fragility",
                            "basis": "casting_recommendation",
                            "evidence_quotes": [],
                        },
                        "uncertainties": ["No source-supported regional accent."],
                    }
                ]
            },
        )
        bernice = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_bernice"
        )
        dossier = bernice["voice"]["imported_dossier"]
        self.assertEqual(dossier["persona_summary"], "Dry, incisive, and privately warm.")
        self.assertEqual(dossier["pitch"]["value"], "Mid-low alto")
        self.assertEqual(
            dossier["casting_guidance"]["basis"],
            "casting_recommendation",
        )
        self.assertEqual(
            bernice["voice"]["persistent_voice_description"],
            "Adult woman, brisk and intelligent, dry warmth.",
        )
        self.assertEqual(
            dossier["designed_voice_description"],
            "A clear adult alto with compact resonance and restrained warmth.",
        )
        self.assertEqual(
            bernice["voice"]["selected_production_method"],
            "custom",
        )
        self.assertEqual(bernice["voice"]["selected_voice"], "benny-main")

    def test_missing_voice_is_one_meaningful_character_blocker(self) -> None:
        config = dict(self.voice_config)
        config.pop("ALTON")
        aggregate = self._build(voice_config=config)
        alton = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_alton"
        )
        self.assertEqual(alton["readiness_state"], "needs_voice")
        self.assertEqual(alton["voice_summary"], "No production Voice")
        self.assertEqual(alton["next_useful_action"]["id"], "assign_character_voice")
        self.assertEqual(alton["blocker_count"], 1)
        self.assertEqual(
            alton["blockers"][0]["code"],
            "cast_voice_configuration_missing",
        )
        self.assertFalse(aggregate["summary"]["complete"])
        self.assertEqual(aggregate["summary"]["blocker_count"], 1)
        self.assertEqual(aggregate["filters"]["counts"]["unassigned"], 1)

    def test_uncertain_source_identity_without_script_lines_does_not_block_cast(self) -> None:
        roster = json.loads(json.dumps(self.roster))
        roster["entries"].append(
            {
                "id": "character_uncertain_source_identity",
                "canonical_name": "Ace",
                "display_name": "Ace",
                "resolution_status": "resolved",
                "speaking_status": "uncertain",
            }
        )
        aggregate = self._build(roster=roster)
        ace = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_uncertain_source_identity"
        )
        self.assertFalse(ace["required_for_completion"])
        self.assertEqual(ace["speaking_role"], "non_speaking")
        self.assertEqual(ace["readiness_state"], "ready")
        self.assertEqual(ace["blockers"], [])

    def test_identity_conflict_precedes_voice_decision(self) -> None:
        roster = json.loads(json.dumps(self.roster))
        roster["entries"][0]["conflict_state"] = "duplicate_candidate"
        roster["entries"][0]["unresolved_questions"] = ["Benny or Bernice?"]
        aggregate = self._build(roster=roster)
        bernice = aggregate["characters"][0]
        self.assertEqual(
            bernice["readiness_state"],
            "needs_identity_review",
        )
        self.assertEqual(
            bernice["next_useful_action"]["id"],
            "review_character_identity",
        )
        codes = {item["code"] for item in bernice["blockers"]}
        self.assertIn("cast_identity_unresolved", codes)

    def test_ambiguous_mapping_returns_candidates_instead_of_guessing(self) -> None:
        character = {
            "canonical_name": "John Smith",
            "aliases": ["John", "Smith"],
        }
        script_index = {
            "labels": ["JOHN", "SMITH"],
            "by_key": {"john": {"JOHN"}, "smith": {"SMITH"}},
            "by_text": {},
        }
        mapping = resolve_script_label(
            character=character,
            script_index=script_index,
        )
        self.assertTrue(mapping["ambiguous"])
        self.assertIsNone(mapping["resolved_label"])
        self.assertEqual(set(mapping["candidate_labels"]), {"JOHN", "SMITH"})

    def test_canonical_exact_label_precedes_an_alias_that_is_also_a_label(self) -> None:
        mapping = resolve_script_label(
            character={
                "canonical_name": "THE DOCTOR",
                "display_name": "THE DOCTOR",
                "aliases": ["Doctor", "THE TENTH DOCTOR"],
            },
            script_index={
                "labels": ["THE DOCTOR", "THE TENTH DOCTOR"],
                "by_key": {
                    "the doctor": {"THE DOCTOR"},
                    "doctor": {"THE DOCTOR"},
                    "the tenth doctor": {"THE TENTH DOCTOR"},
                    "tenth doctor": {"THE TENTH DOCTOR"},
                },
                "by_text": {},
            },
        )
        self.assertFalse(mapping["ambiguous"])
        self.assertEqual(mapping["resolved_label"], "THE DOCTOR")
        self.assertEqual(mapping["method"], "exact_name")

    def test_representative_line_evidence_can_resolve_a_label(self) -> None:
        character = {
            "canonical_name": "The Professor",
            "sample_lines": ["That is not possible."],
        }
        aggregate = self._build(
            roster={
                "entries": [
                    {
                        "id": "character_professor",
                        **character,
                        "resolution_status": "resolved",
                        "speaking_status": "speaking",
                    }
                ]
            },
            voice_config={
                "ALTON": {"type": "custom", "voice": "professor"}
            },
        )
        professor = aggregate["characters"][0]
        self.assertEqual(
            professor["script_connection"]["resolved_script_voice_label"],
            "ALTON",
        )
        self.assertEqual(
            professor["script_connection"]["mapping_method"],
            "representative_line",
        )

    def test_clone_requires_readable_audio_and_exact_transcript(self) -> None:
        config = dict(self.voice_config)
        config["NARRATOR (BENNY)"] = {
            "type": "clone",
            "clone_backend": "voxcpm2",
            "ref_audio": "references/missing.wav",
            "ref_text": "",
        }
        aggregate = self._build(voice_config=config)
        narrator = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_benny_narrator"
        )
        self.assertEqual(narrator["readiness_state"], "needs_voice")
        codes = {item["code"] for item in narrator["blockers"]}
        self.assertIn("cast_clone_reference_audio_invalid", codes)
        self.assertIn("cast_clone_reference_transcript_missing", codes)

    def test_community_qvoice_requires_integrity_and_listening_approval(self) -> None:
        pack = self.root / "community_qwen_packs" / "ohenry" / "ohenry.qvoice"
        pack.parent.mkdir(parents=True)
        pack.write_bytes(b"approved-community-pack")
        config = dict(self.voice_config)
        config["BERNICE"] = {
            "type": "community_qvoice",
            "voice": "O. Henry reader",
            "description": "An older English storyteller.",
            "community_pack_path": "community_qwen_packs/ohenry/ohenry.qvoice",
            "community_pack_sha256": hashlib.sha256(pack.read_bytes()).hexdigest(),
            "community_pack_approval_fingerprint": "a" * 64,
        }
        aggregate = self._build(voice_config=config)
        bernice = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_bernice"
        )
        self.assertEqual(bernice["readiness_state"], "ready")
        self.assertEqual(bernice["voice"]["selected_production_method"], "community_qvoice")
        self.assertEqual(bernice["voice"]["preview"]["approved"], True)

        config["BERNICE"].pop("community_pack_approval_fingerprint")
        blocked = self._build(voice_config=config)
        bernice = next(
            item
            for item in blocked["characters"]
            if item["character_id"] == "character_bernice"
        )
        self.assertEqual(bernice["readiness_state"], "needs_voice")
        self.assertIn(
            "cast_community_qvoice_approval_missing",
            {item["code"] for item in bernice["blockers"]},
        )

    def test_clone_exposes_only_safely_mounted_reference_audio(self) -> None:
        mounted = self.root / "clone_voices" / "benny.wav"
        mounted.parent.mkdir()
        mounted.write_bytes(b"mounted-reference-audio")
        config = dict(self.voice_config)
        config["NARRATOR (BENNY)"] = {
            "type": "clone",
            "clone_backend": "qwen3_base",
            "ref_audio": "clone_voices/benny.wav",
            "ref_text": "I wrote the date in the margin.",
        }
        aggregate = self._build(voice_config=config)
        narrator = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_benny_narrator"
        )
        self.assertEqual(
            narrator["voice"]["clone"]["reference_audio_url"],
            "/clone_voices/benny.wav",
        )

        config["NARRATOR (BENNY)"]["ref_audio"] = "references/benny.wav"
        aggregate = self._build(voice_config=config)
        narrator = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_benny_narrator"
        )
        self.assertIsNone(narrator["voice"]["clone"]["reference_audio_url"])

    def test_controlled_clone_requires_current_approval_fingerprint(self) -> None:
        config = dict(self.voice_config)
        controlled = {
            "type": "controlled_clone",
            "clone_backend": "qwen3_instruction_controlled",
            "ref_audio": "references/benny.wav",
            "ref_text": "I wrote the date in the margin.",
        }
        config["NARRATOR (BENNY)"] = controlled
        missing = self._build(voice_config=config)
        narrator = next(
            item
            for item in missing["characters"]
            if item["character_id"] == "character_benny_narrator"
        )
        self.assertIn(
            "cast_controlled_clone_approval_missing",
            {item["code"] for item in narrator["blockers"]},
        )

        config["NARRATOR (BENNY)"] = {
            **controlled,
            "controlled_clone_configuration_fingerprint": "approval-fingerprint",
        }
        approved = self._build(voice_config=config)
        narrator = next(
            item
            for item in approved["characters"]
            if item["character_id"] == "character_benny_narrator"
        )
        self.assertEqual(narrator["readiness_state"], "ready")
        self.assertEqual(
            narrator["voice"]["clone"]["controlled_approval_state"],
            "approved",
        )

    def test_legacy_voxcpm2_control_is_blocked(self) -> None:
        config = dict(self.voice_config)
        config["NARRATOR (BENNY)"] = {
            "type": "clone",
            "clone_backend": "voxcpm2_controlled",
            "ref_audio": "references/benny.wav",
            "ref_text": "I wrote the date in the margin.",
            "controlled_clone_configuration_fingerprint": "stale-legacy",
        }
        aggregate = self._build(voice_config=config)
        narrator = next(
            item
            for item in aggregate["characters"]
            if item["character_id"] == "character_benny_narrator"
        )
        self.assertIn(
            "cast_legacy_controlled_clone_unsupported",
            {item["code"] for item in narrator["blockers"]},
        )
        self.assertEqual(narrator["readiness_state"], "needs_voice")

    def test_alias_must_resolve_existing_voice_target(self) -> None:
        valid = self._build()
        alton = next(
            item
            for item in valid["characters"]
            if item["character_id"] == "character_alton"
        )
        self.assertEqual(alton["voice"]["alias"]["state"], "ready")

        stale_ui_config = dict(self.voice_config)
        stale_ui_config["ALTON"] = {
            "type": "existing",
            "alias_of": "BERNICE",
            "voice": None,
        }
        stale_ui = self._build(voice_config=stale_ui_config)
        alton = next(
            item
            for item in stale_ui["characters"]
            if item["character_id"] == "character_alton"
        )
        self.assertEqual(alton["voice"]["selected_production_method"], "alias")
        self.assertEqual(alton["voice"]["alias"]["state"], "ready")
        self.assertNotIn(
            "cast_voice_method_unsupported",
            {item["code"] for item in alton["blockers"]},
        )

        config = dict(self.voice_config)
        config["ALTON"] = {"type": "alias", "alias_of": "MISSING"}
        invalid = self._build(voice_config=config)
        alton = next(
            item
            for item in invalid["characters"]
            if item["character_id"] == "character_alton"
        )
        self.assertEqual(alton["readiness_state"], "needs_voice")
        self.assertIn(
            "cast_alias_target_invalid",
            {item["code"] for item in alton["blockers"]},
        )

    def test_adapter_requires_production_support_and_manual_listening_approval(self) -> None:
        adapter = self.root / "adapters" / "alton"
        adapter.mkdir(parents=True)
        config = dict(self.voice_config)
        config["ALTON"] = {"type": "adapter", "adapter_path": str(adapter)}
        invalid = self._build(voice_config=config)
        alton = next(
            item
            for item in invalid["characters"]
            if item["character_id"] == "character_alton"
        )
        self.assertEqual(alton["readiness_state"], "needs_voice")

        (adapter / "manifest.json").write_text(
            json.dumps(
                {
                    "production_assignment_supported": True,
                    "validation": {
                        "manual_audio_review_status": "approved"
                    },
                }
            ),
            encoding="utf-8",
        )
        valid = self._build(voice_config=config)
        alton = next(
            item
            for item in valid["characters"]
            if item["character_id"] == "character_alton"
        )
        self.assertEqual(alton["readiness_state"], "ready")
        self.assertEqual(alton["voice"]["adapter"]["state"], "ready")

    def test_selected_character_survives_filtering_and_search(self) -> None:
        aggregate = self._build(
            selected_character_id="character_town",
            filter_key="speaking_roles",
            search="Bernice",
        )
        self.assertEqual(
            [item["character_id"] for item in aggregate["characters"]],
            ["character_bernice"],
        )
        self.assertEqual(
            aggregate["selected_character_id"],
            "character_town",
        )
        self.assertEqual(
            aggregate["selected_character"]["display_name"],
            "The Town",
        )
        self.assertFalse(aggregate["selection_visible"])

    def test_missing_selected_character_and_invalid_filter_are_explicit(self) -> None:
        with self.assertRaises(CastAggregateError) as missing:
            self._build(selected_character_id="character_missing")
        self.assertEqual(missing.exception.code, "cast_character_not_found")

        with self.assertRaises(CastAggregateError) as invalid_filter:
            self._build(filter_key="persona")
        self.assertEqual(invalid_filter.exception.code, "cast_filter_invalid")

    def test_native_validation_can_only_make_cast_stricter(self) -> None:
        aggregate = self._build()
        self.assertTrue(aggregate["summary"]["complete"])
        validated = apply_native_cast_validation(
            aggregate,
            {
                "process": {"running": False},
                "resumable": False,
                "failed": False,
                "roster_exists": True,
                "review_required": False,
                "roster_approved": True,
                "roster_current": True,
                "required_speaking_characters": 4,
                "valid_production_voices": 4,
                "unresolved_identity_ids": [],
                "ambiguous_mapping_ids": [],
                "missing_voice_ids": [],
                "invalid_voice_ids": [],
                "invalid_clone_ids": [],
                "controlled_clone_approval_missing_ids": [
                    "character_benny_narrator"
                ],
                "invalid_adapter_ids": [],
                "stale_voice_ids": [],
                "fingerprints": {"native": "native-fingerprint"},
            },
        )
        self.assertFalse(validated["summary"]["complete"])
        self.assertEqual(validated["summary"]["state"], "blocked")
        narrator = next(
            item
            for item in validated["characters"]
            if item["character_id"] == "character_benny_narrator"
        )
        self.assertEqual(narrator["readiness_state"], "needs_voice")
        self.assertIn(
            "cast_native_controlled_clone_approval_missing",
            {item["code"] for item in narrator["blockers"]},
        )
        self.assertTrue(
            validated["authoritative_native_validation"]["applied"]
        )

    def test_filtering_occurs_after_full_native_validation(self) -> None:
        aggregate = self._build(
            selected_character_id="character_benny_narrator"
        )
        validated = apply_native_cast_validation(
            aggregate,
            {
                "process": {"running": False},
                "resumable": False,
                "failed": False,
                "roster_exists": True,
                "review_required": False,
                "roster_approved": True,
                "roster_current": True,
                "required_speaking_characters": 4,
                "valid_production_voices": 4,
                "unresolved_identity_ids": [],
                "ambiguous_mapping_ids": [],
                "missing_voice_ids": [],
                "invalid_voice_ids": [],
                "invalid_clone_ids": [],
                "controlled_clone_approval_missing_ids": [
                    "character_benny_narrator"
                ],
                "invalid_adapter_ids": [],
                "stale_voice_ids": [],
                "fingerprints": {},
            },
        )
        filtered = filter_cast_aggregate(
            validated,
            filter_key="speaking_roles",
            search="Bernice",
        )
        self.assertEqual(
            [item["character_id"] for item in filtered["characters"]],
            ["character_bernice"],
        )
        self.assertFalse(filtered["selection_visible"])
        self.assertEqual(
            filtered["selected_character"]["readiness_state"],
            "needs_voice",
        )
        self.assertFalse(filtered["summary"]["complete"])
        self.assertEqual(filtered["summary"]["required_speaking_count"], 4)

    def test_advanced_preparation_is_contextual_and_optional(self) -> None:
        aggregate = self._build(
            preparation_state={
                "characters": [
                    {
                        "character_id": "character_bernice",
                        "reference_bank_state": "ready",
                        "dataset_state": "failed",
                        "training_state": "experimental",
                        "blockers": [
                            {
                                "code": "dataset_failed",
                                "title": "Dataset failed",
                            }
                        ],
                    }
                ]
            }
        )
        bernice = aggregate["characters"][0]
        advanced = bernice["advanced_voice_setup"]
        self.assertEqual(advanced["expressive_reference_state"], "ready")
        self.assertEqual(advanced["dataset_state"], "failed")
        self.assertTrue(advanced["optional"])
        self.assertEqual(bernice["readiness_state"], "ready")
        self.assertTrue(aggregate["summary"]["complete"])


if __name__ == "__main__":
    unittest.main()
