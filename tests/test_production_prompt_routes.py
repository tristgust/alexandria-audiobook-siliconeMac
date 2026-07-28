from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from experimental_prompt_routing import (
    resolve_experimental_prompt_override,
    sha256_file,
)
from production_prompt_routes import (
    EXPRESSIVE_REFERENCE_REPAIRS,
    EXPRESSIVE_ROUTE_SUBSTITUTIONS,
    PRIMARY_VOICE_ALIASES,
    ProductionPromptRouteError,
    inspect_primary_responsive_voice_pack,
    install_primary_responsive_voices,
    materialize_primary_responsive_voice_pack,
    promote_validated_expressive_routes,
)
from project import ProjectManager
from project_catalog import create_managed_project


def write_wav(path: Path, *, frames: int = 2400, value: bytes = b"\x00\x00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(value * frames)


class RecordingGenerationEngine:
    def __init__(self) -> None:
        self.single_calls: list[dict] = []
        self.batch_calls: list[dict] = []

    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        self.single_calls.append(
            {"text": text, "instruct": instruct, "speaker": speaker}
        )
        write_wav(Path(output_path), frames=24000)
        return True

    def generate_batch(self, chunks, voice_config, output_dir, seed):
        self.batch_calls.extend(dict(chunk) for chunk in chunks)
        completed = []
        for chunk in chunks:
            write_wav(
                Path(output_dir) / f"temp_batch_{chunk['index']}.wav",
                frames=24000,
            )
            completed.append(chunk["index"])
        return {"completed": completed, "failed": []}


class ProductionPromptRouteInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps(
                {
                    "tts": {
                        "language": "English",
                        "deterministic_seed_enabled": True,
                    }
                }
            ),
            encoding="utf-8",
        )
        self.narrator = self.root / "clone_voices" / "narrator.wav"
        self.benny_base = self.root / "clone_voices" / "benny.wav"
        self.doctor_base = self.root / "clone_voices" / "doctor.wav"
        write_wav(self.narrator, value=b"\x01\x00")
        write_wav(self.benny_base, value=b"\x02\x00")
        write_wav(self.doctor_base, value=b"\x03\x00")
        self.benny_prompt = self.root / "sources" / "benny-fear.wav"
        self.doctor_prompt = self.root / "sources" / "doctor-playful.wav"
        write_wav(self.benny_prompt, value=b"\x04\x00")
        write_wav(self.doctor_prompt, value=b"\x05\x00")
        self.voice_config = {
            "NARRATOR": {
                "type": "clone",
                "clone_backend": "qwen3_base",
                "ref_audio": "clone_voices/narrator.wav",
                "ref_text": "Narrator reference transcript.",
                "seed": "-1",
            },
            "BERNICE": {
                "type": "clone",
                "clone_backend": "qwen3_base",
                "ref_audio": "clone_voices/benny.wav",
                "ref_text": "Benny reference transcript.",
                "seed": "-1",
            },
            "THE DOCTOR": {
                "type": "clone",
                "clone_backend": "voxcpm2_controlled",
                "ref_audio": "clone_voices/doctor.wav",
                "ref_text": "Doctor reference transcript.",
                "character_style": "Measured, dry, Scottish.",
                "seed": "-1",
            },
        }
        (self.root / "voice_config.json").write_text(
            json.dumps(self.voice_config),
            encoding="utf-8",
        )
        old_audio = self.root / "voicelines" / "doctor.wav"
        write_wav(old_audio, frames=4800)
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "THE DOCTOR",
                        "text": "Oh, wonderful.",
                        "instruct": "Dryly amused; conversational pace.",
                        "status": "done",
                        "audio_state": "current",
                        "audio_path": "voicelines/doctor.wav",
                        "audio_fingerprint": "a" * 64,
                        "audio_sha256": sha256_file(old_audio),
                    }
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def install(self):
        with (
            patch(
                "production_prompt_routes.BENNY_SOURCE_SHA256",
                sha256_file(self.benny_prompt),
            ),
            patch(
                "production_prompt_routes.DOCTOR_SOURCE_SHA256",
                sha256_file(self.doctor_prompt),
            ),
        ):
            return install_primary_responsive_voices(
                project_root=self.root,
                benny_prompt_source=self.benny_prompt,
                doctor_prompt_source=self.doctor_prompt,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-26T06:00:00Z",
            )

    def write_reviewed_bank(self) -> tuple[Path, list[dict]]:
        bank_root = self.root / "reviewed-bank"
        references = []
        fixtures = (
            (
                "narrator",
                "narrator_demo_warm_nostalgia",
                "Warm nostalgia",
                "That was lovely. They were such wonderful moments.",
            ),
            (
                "narrator",
                "narrator_skip_existential_dread",
                "Existential dread",
                "I wish you to feel afraid, as I do.",
            ),
            (
                "narrator",
                "narrator_ud_bittersweet_nostalgia",
                "Bittersweet nostalgia",
                "We were so innocent. We'll never be like that again.",
            ),
            (
                "narrator",
                "narrator_ud_creative_insecurity",
                "Creative insecurity",
                "Where did I mess up the joke?",
            ),
            (
                "narrator",
                "narrator_ud_explosive_indignation",
                "Explosive indignation",
                "I'm infuriated and I'm offended.",
            ),
            (
                "benny",
                "benny_criminal_incredulous_concern",
                "Wary concern",
                "Why would he have done that?",
            ),
            (
                "benny",
                "benny_criminal_sardonic_concern",
                "Dry sarcasm",
                "You'd think that would be his forte.",
            ),
            (
                "benny",
                "benny_hesitation_grave_reflection",
                "Grave reflection",
                "A race was dying. The whole planet wiped out.",
            ),
            (
                "doctor",
                "doctor_acf_fond_reminiscence",
                "Fond nostalgia",
                "My granddaughter always could talk me round.",
            ),
            (
                "doctor",
                "doctor_comic_disorientation",
                "Comic disorientation",
                "Where am I? Who am I? And who are you?",
            ),
            (
                "doctor",
                "doctor_indomitable_determination",
                "Indomitable determination",
                "I won't stop. I can't stop. Not while there's work to do.",
            ),
        )
        for index, (target, clip_id, emotion, transcript) in enumerate(fixtures):
            audio = bank_root / "audio" / target / f"{clip_id}.wav"
            write_wav(audio, value=bytes((index + 10, 0)))
            references.append(
                {
                    "target": target,
                    "clip_id": clip_id,
                    "primary_emotion": emotion,
                    "secondary_emotion": None,
                    "dramatic_function": emotion,
                    "selected_transcript": transcript,
                    "reference_status": "approved_source_reference_final",
                    "audio_path": str(audio),
                    "audio_sha256": sha256_file(audio),
                }
            )
        manifest = bank_root / "three-voice-validated-reference-bank.json"
        manifest.write_text(
            json.dumps({"schema_version": 1, "references": references}),
            encoding="utf-8",
        )
        return manifest, references

    def test_confirmation_is_required_before_mutation(self) -> None:
        before = (self.root / "voice_config.json").read_bytes()
        with self.assertRaisesRegex(
            ProductionPromptRouteError,
            "explicit confirmation",
        ):
            install_primary_responsive_voices(
                project_root=self.root,
                benny_prompt_source=self.benny_prompt,
                doctor_prompt_source=self.doctor_prompt,
                confirm_production_opt_in=False,
            )
        self.assertEqual((self.root / "voice_config.json").read_bytes(), before)
        self.assertFalse((self.root / "production_prompt_routes").exists())

    def test_installer_upgrades_all_primary_voices_and_invalidates_old_audio(self) -> None:
        result = self.install()
        self.assertTrue(result["final_export_eligible"])
        self.assertEqual(
            result["voices"],
            ["NARRATOR", "BERNICE", "THE DOCTOR"],
        )
        config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        for name in ("NARRATOR", "BERNICE", "THE DOCTOR"):
            self.assertEqual(
                config[name]["clone_backend"],
                "qwen3_instruction_controlled",
            )
            self.assertEqual(config[name]["seed"], "130363")
            self.assertEqual(
                len(config[name]["controlled_clone_configuration_fingerprint"]),
                64,
            )
        self.assertNotIn("experimental_prompt_routing", config["NARRATOR"])
        self.assertEqual(
            config["BERNICE"]["experimental_prompt_routing"]["scope"],
            "production_opt_in",
        )
        self.assertEqual(
            config["THE DOCTOR"]["experimental_prompt_routing"]["general_routing"],
            "instruction_keywords",
        )
        chunks = json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(chunks[0]["status"], "pending")
        self.assertEqual(chunks[0]["audio_state"], "stale")
        self.assertIsNone(chunks[0]["audio_path"])
        self.assertTrue(
            (self.root / "production_prompt_routes" / "benny_credible_fear.wav").is_file()
        )
        self.assertTrue(
            (self.root / "production_prompt_routes" / "doctor_playful_identity.wav").is_file()
        )

    def test_materialized_pack_is_self_contained_and_alias_safe(self) -> None:
        self.install()
        source_status = inspect_primary_responsive_voice_pack(self.root)
        self.assertTrue(source_status["ready"], source_status)
        destination = self.root / "managed-project"
        receipt = materialize_primary_responsive_voice_pack(
            source_project_root=self.root,
            destination_project_root=destination,
        )
        self.assertEqual(receipt["pack_id"], source_status["pack_id"])
        self.assertEqual(
            receipt["pack_fingerprint"],
            source_status["pack_fingerprint"],
        )
        copied = json.loads(
            (destination / "voice_config.json").read_text(encoding="utf-8")
        )
        for name in ("NARRATOR", "BERNICE", "THE DOCTOR"):
            self.assertEqual(
                copied[name]["clone_backend"],
                "qwen3_instruction_controlled",
            )
            self.assertEqual(copied[name]["seed"], "130363")
        for alias, target in PRIMARY_VOICE_ALIASES.items():
            self.assertEqual(copied[alias], {"alias_of": target})
        for relative in (
            "clone_voices/narrator.wav",
            "clone_voices/benny.wav",
            "clone_voices/doctor.wav",
            "production_prompt_routes/benny_credible_fear.wav",
            "production_prompt_routes/doctor_playful_identity.wav",
        ):
            self.assertTrue((destination / relative).is_file(), relative)
        destination_status = inspect_primary_responsive_voice_pack(destination)
        self.assertTrue(destination_status["ready"], destination_status)
        self.assertEqual(
            destination_status["pack_fingerprint"],
            source_status["pack_fingerprint"],
        )

    def test_new_managed_project_inherits_responsive_voice_pack(self) -> None:
        self.install()
        script = self.root / "next-book-script.json"
        script.write_text(
            json.dumps(
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "The doors opened.",
                        "instruct": "Measured suspense.",
                    },
                    {
                        "speaker": "BENNY",
                        "text": "Who is there?",
                        "instruct": "Fearful and tense.",
                    },
                    {
                        "speaker": "DOCTOR",
                        "text": "Only me.",
                        "instruct": "Dryly amused.",
                    },
                ]
            ),
            encoding="utf-8",
        )
        created = create_managed_project(
            data_root=self.root / "application-data",
            project_name="Next Book",
            source_path=script,
            source_language="English",
            output_language="English",
            generation_method="import_existing_script",
            starter_voice_pack_root=self.root,
            at_utc="2026-07-26T07:00:00Z",
        )
        project_root = Path(
            created["project"]["technical_details"]["project_path"]
        )
        self.assertEqual(
            created["starter_voice_pack"]["pack_id"],
            inspect_primary_responsive_voice_pack(self.root)["pack_id"],
        )
        self.assertTrue(
            inspect_primary_responsive_voice_pack(project_root)["ready"]
        )
        state = json.loads(
            (project_root / "state.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (project_root / "alexandria-project.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            state["starter_voice_pack"]["pack_id"],
            created["starter_voice_pack"]["pack_id"],
        )
        self.assertTrue(
            manifest["starter_voice_pack"]["final_export_eligible"]
        )

    def test_installed_routes_match_real_delivery_directions_automatically(self) -> None:
        self.install()
        config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        doctor = resolve_experimental_prompt_override(
            voice_data=config["THE DOCTOR"],
            instruction="Dryly amused; conversational pace, underplay the punch line.",
            project_root=self.root,
        )
        self.assertEqual(doctor["route_key"], "ordinary_identity")
        self.assertTrue(doctor["production_promotion_allowed"])
        benny = resolve_experimental_prompt_override(
            voice_data=config["BERNICE"],
            instruction="Fearful and tense; let the dread arrive slowly.",
            project_root=self.root,
        )
        self.assertEqual(benny["route_key"], "credible_fear")
        self.assertTrue(benny["production_promotion_allowed"])
        neutral = resolve_experimental_prompt_override(
            voice_data=config["BERNICE"],
            instruction="Dryly inquisitive; conversational pace.",
            project_root=self.root,
        )
        self.assertIsNone(neutral)

    def test_reviewed_bank_promotes_emotion_routes_for_all_primary_voices(self) -> None:
        # Given an installed responsive pack and one reviewed performance reference
        # for each primary character.
        self.install()
        manifest, _ = self.write_reviewed_bank()

        # When the operator promotes that reviewed bank into production routing.
        result = promote_validated_expressive_routes(
            project_root=self.root,
            validated_bank_path=manifest,
            confirm_production_opt_in=True,
            approved_at_utc="2026-07-28T03:00:00Z",
        )

        # Then every primary voice selects its reviewed prompt from its own line
        # direction, and the promoted assets participate in portable pack copying.
        self.assertEqual(result["promoted_voice_count"], 3)
        config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        cases = (
            (
                "NARRATOR",
                "Quietly grief-weighted; let the sorrow remain controlled.",
                "narrator_ud_bittersweet_nostalgia",
            ),
            (
                "BERNICE",
                "Somber grief and grave reflection.",
                "benny_hesitation_grave_reflection",
            ),
            (
                "THE DOCTOR",
                "Urgent, commanding determination.",
                "doctor_indomitable_determination",
            ),
        )
        for voice, instruction, expected_route in cases:
            selected = resolve_experimental_prompt_override(
                voice_data=config[voice],
                instruction=instruction,
                project_root=self.root,
            )
            self.assertIsNotNone(selected)
            self.assertEqual(selected["route_key"], expected_route)
            self.assertTrue(selected["production_promotion_allowed"])
        status = inspect_primary_responsive_voice_pack(self.root)
        self.assertTrue(status["ready"], status)
        promoted_assets = {
            asset["route"]
            for asset in status["assets"]
            if asset["kind"] == "performance_prompt"
        }
        self.assertTrue({case[2] for case in cases}.issubset(promoted_assets))

    def test_curated_reference_repair_replaces_an_already_promoted_bad_route(self) -> None:
        self.install()
        manifest, references = self.write_reviewed_bank()
        promote_validated_expressive_routes(
            project_root=self.root,
            validated_bank_path=manifest,
            confirm_production_opt_in=True,
            approved_at_utc="2026-07-28T03:00:00Z",
        )
        bad = next(
            item
            for item in references
            if item["clip_id"] == "narrator_demo_warm_nostalgia"
        )
        repaired_audio = self.root / "reviewed-bank" / "repaired-warm.wav"
        write_wav(
            repaired_audio,
            frames=24000 * 2,
            value=b"\x00\x20",
        )
        repaired_text = "That was lovely. No concerns. Just a blank slate."
        repair = {
            "manifest_audio_sha256": bad["audio_sha256"],
            "manifest_ref_text": bad["selected_transcript"],
            "source": repaired_audio,
            "audio_sha256": sha256_file(repaired_audio),
            "ref_text": repaired_text,
        }

        with patch.dict(
            EXPRESSIVE_REFERENCE_REPAIRS,
            {("narrator", bad["clip_id"]): repair},
        ):
            promote_validated_expressive_routes(
                project_root=self.root,
                validated_bank_path=manifest,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-28T04:00:00Z",
            )

        config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        route = config["NARRATOR"]["experimental_prompt_routing"]["routes"][
            bad["clip_id"]
        ]
        self.assertEqual(route["ref_text"], repaired_text)
        self.assertEqual(route["ref_audio_sha256"], sha256_file(repaired_audio))
        self.assertEqual(
            sha256_file(self.root / route["ref_audio"]),
            sha256_file(repaired_audio),
        )

    def test_reviewed_bank_promotion_is_voice_scoped(self) -> None:
        # Given an unrelated supplied-recording voice alongside the three opted-in
        # primary voices.
        self.install()
        config_path = self.root / "voice_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["JOAN REDFERN"] = {
            "type": "clone",
            "clone_backend": "qwen3_base",
            "ref_audio": "clone_voices/narrator.wav",
            "ref_text": "Joan's unchanged reference.",
            "seed": "-1",
            "future_extension": "preserve",
        }
        config_path.write_text(json.dumps(config), encoding="utf-8")
        before_unrelated = dict(config["JOAN REDFERN"])
        manifest, _ = self.write_reviewed_bank()

        # When the reviewed three-voice bank is promoted.
        promote_validated_expressive_routes(
            project_root=self.root,
            validated_bank_path=manifest,
            confirm_production_opt_in=True,
            approved_at_utc="2026-07-28T03:00:00Z",
        )

        # Then no unspecified current or future voice is opted into routing.
        promoted = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(promoted["JOAN REDFERN"], before_unrelated)
        self.assertNotIn(
            "experimental_prompt_routing",
            promoted["JOAN REDFERN"],
        )

    def test_human_nature_delivery_vocabulary_routes_without_collisions(self) -> None:
        # Given reviewed prompts spanning the dominant expressive families found
        # in the Human Nature directions.
        self.install()
        manifest, _ = self.write_reviewed_bank()
        promote_validated_expressive_routes(
            project_root=self.root,
            validated_bank_path=manifest,
            confirm_production_opt_in=True,
            approved_at_utc="2026-07-28T03:00:00Z",
        )
        config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        cases = (
            (
                "NARRATOR",
                "Quietly grief-weighted; let resentment and social discomfort remain controlled.",
                "narrator_ud_bittersweet_nostalgia",
            ),
            (
                "NARRATOR",
                "Warm third-person narration; tender and affectionate.",
                "narrator_demo_warm_nostalgia",
            ),
            (
                "NARRATOR",
                "Dry comic narration; lightly amused but self-conscious.",
                "narrator_ud_creative_insecurity",
            ),
            (
                "NARRATOR",
                "Tense suspense with existential dread and foreboding.",
                "narrator_skip_existential_dread",
            ),
            (
                "BERNICE",
                "Dry, self-aware recollection with restrained irony.",
                "benny_criminal_sardonic_concern",
            ),
            (
                "BERNICE",
                "Curious and skeptical; searching but composed.",
                "benny_criminal_incredulous_concern",
            ),
            (
                "BERNICE",
                "Intimate and grief-weighted; reflective and candid.",
                "benny_hesitation_grave_reflection",
            ),
            (
                "THE DOCTOR",
                "Playfully probing; restlessly thoughtful.",
                "ordinary_identity",
            ),
            (
                "THE DOCTOR",
                "Controlled and purposeful; decisive, urgent, and commanding.",
                "doctor_indomitable_determination",
            ),
            (
                "THE DOCTOR",
                "Affectionate and remorseful, with fond warmth.",
                "doctor_acf_fond_reminiscence",
            ),
            (
                "THE DOCTOR",
                "Puzzled and confused; brisk comic disorientation.",
                "doctor_comic_disorientation",
            ),
        )

        # When each real-script delivery family is resolved.
        for voice, instruction, expected_route in cases:
            with self.subTest(voice=voice, instruction=instruction):
                selected = resolve_experimental_prompt_override(
                    voice_data=config[voice],
                    instruction=instruction,
                    project_root=self.root,
                )
                # Then exactly one character-correct reviewed route wins.
                self.assertIsNotNone(selected)
                self.assertEqual(selected["route_key"], expected_route)

        # Basic neutral/documentary direction remains on the canonical identity
        # plus Qwen line instruction instead of forcing an emotional prompt.
        neutral = resolve_experimental_prompt_override(
            voice_data=config["NARRATOR"],
            instruction="Neutral documentary tone; concise and unobtrusive.",
            project_root=self.root,
        )
        self.assertIsNone(neutral)

    def test_invalid_reviewed_bank_does_not_mutate_production_assets(self) -> None:
        # Given an installed pack and a manifest whose reviewed audio no longer
        # matches its recorded fingerprint.
        self.install()
        manifest, references = self.write_reviewed_bank()
        Path(references[0]["audio_path"]).write_bytes(b"tampered")
        config_path = self.root / "voice_config.json"
        before_config = config_path.read_bytes()
        before_assets = sorted(
            path.relative_to(self.root).as_posix()
            for path in (self.root / "production_prompt_routes").rglob("*")
            if path.is_file()
        )

        # When promotion validates the entire bank.
        with self.assertRaisesRegex(
            ProductionPromptRouteError,
            "fingerprint|changed|SHA-256",
        ):
            promote_validated_expressive_routes(
                project_root=self.root,
                validated_bank_path=manifest,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-28T03:00:00Z",
            )

        # Then no config or production prompt asset has been changed.
        self.assertEqual(config_path.read_bytes(), before_config)
        after_assets = sorted(
            path.relative_to(self.root).as_posix()
            for path in (self.root / "production_prompt_routes").rglob("*")
            if path.is_file()
        )
        self.assertEqual(after_assets, before_assets)

    def test_current_reviewed_bank_schema_rejects_an_inaudibly_quiet_reference(self) -> None:
        self.install()
        manifest, _ = self.write_reviewed_bank()
        bank = json.loads(manifest.read_text(encoding="utf-8"))
        bank["schema_version"] = 3
        manifest.write_text(json.dumps(bank), encoding="utf-8")
        before = (self.root / "voice_config.json").read_bytes()

        with self.assertRaisesRegex(
            ProductionPromptRouteError,
            "too quiet",
        ):
            promote_validated_expressive_routes(
                project_root=self.root,
                validated_bank_path=manifest,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-28T03:00:00Z",
            )

        self.assertEqual((self.root / "voice_config.json").read_bytes(), before)

    def test_reviewed_bank_requires_supported_schema_before_mutation(self) -> None:
        # Given an installed pack and otherwise valid manifests whose schema is
        # missing or unsupported.
        self.install()
        manifest, _ = self.write_reviewed_bank()
        original = json.loads(manifest.read_text(encoding="utf-8"))
        config_path = self.root / "voice_config.json"
        before_config = config_path.read_bytes()

        for schema in (None, True, 1.0, 3.0, 999):
            with self.subTest(schema=schema):
                candidate = dict(original)
                if schema is None:
                    candidate.pop("schema_version", None)
                else:
                    candidate["schema_version"] = schema
                manifest.write_text(json.dumps(candidate), encoding="utf-8")

                # When promotion validates the manifest boundary.
                with self.assertRaisesRegex(
                    ProductionPromptRouteError,
                    "schema_version|schema version",
                ):
                    promote_validated_expressive_routes(
                        project_root=self.root,
                        validated_bank_path=manifest,
                        confirm_production_opt_in=True,
                        approved_at_utc="2026-07-28T03:00:00Z",
                    )

                # Then configuration is unchanged before any production copy.
                self.assertEqual(config_path.read_bytes(), before_config)

    def test_reviewed_bank_rejects_revoked_approval_state_before_mutation(self) -> None:
        # Given a status that begins with "approved" but explicitly revokes it.
        self.install()
        manifest, references = self.write_reviewed_bank()
        references[0]["reference_status"] = "approved_then_revoked"
        manifest.write_text(
            json.dumps({"schema_version": 1, "references": references}),
            encoding="utf-8",
        )
        config_path = self.root / "voice_config.json"
        before_config = config_path.read_bytes()

        # When promotion validates exact approval states.
        with self.assertRaisesRegex(ProductionPromptRouteError, "not approved"):
            promote_validated_expressive_routes(
                project_root=self.root,
                validated_bank_path=manifest,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-28T03:00:00Z",
            )

        # Then the rejected bank cannot change production configuration.
        self.assertEqual(config_path.read_bytes(), before_config)

    def test_same_clip_id_with_different_evidence_is_rejected_before_mutation(
        self,
    ) -> None:
        # Given the established Benny fear route and a valid bank asset that
        # reuses its clip ID with different audio evidence.
        self.install()
        manifest, references = self.write_reviewed_bank()
        references[5]["clip_id"] = "benny_hesitation_fatalistic_dread"
        manifest.write_text(
            json.dumps({"schema_version": 1, "references": references}),
            encoding="utf-8",
        )
        config_path = self.root / "voice_config.json"
        before_config = config_path.read_bytes()

        # When promotion encounters the same semantic ID with different proof.
        with self.assertRaisesRegex(
            ProductionPromptRouteError,
            "different evidence|substitution",
        ):
            promote_validated_expressive_routes(
                project_root=self.root,
                validated_bank_path=manifest,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-28T03:00:00Z",
            )

        # Then the collision is rejected before config or assets mutate.
        self.assertEqual(config_path.read_bytes(), before_config)

    def test_direct_route_keyword_tampering_is_rejected_before_mutation(
        self,
    ) -> None:
        # Given a directly promoted route whose matching keywords are altered
        # after approval while all audio evidence remains valid.
        self.install()
        manifest, _ = self.write_reviewed_bank()
        promote_validated_expressive_routes(
            project_root=self.root,
            validated_bank_path=manifest,
            confirm_production_opt_in=True,
            approved_at_utc="2026-07-28T03:00:00Z",
        )
        config_path = self.root / "voice_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        route = config["NARRATOR"]["experimental_prompt_routing"]["routes"][
            "narrator_ud_bittersweet_nostalgia"
        ]
        route["instruction_keywords"] = ["tampered keyword"]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        before_config = config_path.read_bytes()
        before_assets = sorted(
            path.relative_to(self.root).as_posix()
            for path in (self.root / "production_prompt_routes").rglob("*")
            if path.is_file()
        )

        # When an idempotent promotion validates stable route evidence.
        with self.assertRaisesRegex(
            ProductionPromptRouteError,
            "different evidence|substitution",
        ):
            promote_validated_expressive_routes(
                project_root=self.root,
                validated_bank_path=manifest,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-28T03:01:00Z",
            )

        # Then neither the tampered config nor production assets are rewritten.
        self.assertEqual(config_path.read_bytes(), before_config)
        self.assertEqual(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in (self.root / "production_prompt_routes").rglob("*")
                if path.is_file()
            ),
            before_assets,
        )

    def test_substitution_keyword_tampering_is_rejected_before_mutation(
        self,
    ) -> None:
        # Given an explicit synthetic substitution whose approved existing route
        # is later changed only in its instruction keywords.
        self.install()
        manifest, references = self.write_reviewed_bank()
        reference = references[5]
        reference["clip_id"] = "benny_hesitation_fatalistic_dread"
        manifest.write_text(
            json.dumps({"schema_version": 1, "references": references}),
            encoding="utf-8",
        )
        config_path = self.root / "voice_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        route = config["BERNICE"]["experimental_prompt_routing"]["routes"][
            "credible_fear"
        ]
        stable_route = {
            field: value
            for field, value in route.items()
            if field != "operator_approved_at_utc"
        }
        substitution = {
            "manifest_audio_sha256": reference["audio_sha256"],
            "manifest_ref_text": reference["selected_transcript"],
            "route_key": "credible_fear",
            "route_evidence": stable_route,
        }
        route["instruction_keywords"] = ["tampered keyword"]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        before_config = config_path.read_bytes()

        # When the same clip is checked against its explicit substitution.
        with patch.dict(
            EXPRESSIVE_ROUTE_SUBSTITUTIONS,
            {("BERNICE", reference["clip_id"]): substitution},
        ):
            with self.assertRaisesRegex(
                ProductionPromptRouteError,
                "different evidence.*validated substitution",
            ):
                promote_validated_expressive_routes(
                    project_root=self.root,
                    validated_bank_path=manifest,
                    confirm_production_opt_in=True,
                    approved_at_utc="2026-07-28T03:00:00Z",
                )

        # Then the route remains untouched and no promotion mutation occurs.
        self.assertEqual(config_path.read_bytes(), before_config)

    def test_individual_and_mass_generation_use_identical_scoped_routes(self) -> None:
        # Given promoted routes, canonical aliases, and one representative line
        # for each researched voice.
        self.install()
        manifest, _ = self.write_reviewed_bank()
        promote_validated_expressive_routes(
            project_root=self.root,
            validated_bank_path=manifest,
            confirm_production_opt_in=True,
            approved_at_utc="2026-07-28T03:00:00Z",
        )
        config_path = self.root / "voice_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["NARRATOR (BENNY)"] = {"alias_of": "BERNICE"}
        config["DOCTOR"] = {"alias_of": "THE DOCTOR"}
        config_path.write_text(json.dumps(config), encoding="utf-8")
        chunks_path = self.root / "chunks.json"
        chunks_path.write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "NARRATOR",
                        "text": "We had lost him.",
                        "instruct": "Quietly grief-weighted and sorrowful.",
                        "status": "pending",
                        "audio_path": None,
                    },
                    {
                        "id": 1,
                        "speaker": "NARRATOR (BENNY)",
                        "text": "I still remember.",
                        "instruct": "Intimate grief; reflective and candid.",
                        "status": "pending",
                        "audio_path": None,
                    },
                    {
                        "id": 2,
                        "speaker": "DOCTOR",
                        "text": "We keep moving.",
                        "instruct": "Controlled, purposeful, and commanding.",
                        "status": "pending",
                        "audio_path": None,
                    },
                ]
            ),
            encoding="utf-8",
        )
        expected_routes = [
            "narrator_ud_bittersweet_nostalgia",
            "benny_hesitation_grave_reflection",
            "doctor_indomitable_determination",
        ]
        expected_speakers = ["NARRATOR", "BERNICE", "THE DOCTOR"]
        manager = ProjectManager(str(self.root))
        engine = RecordingGenerationEngine()
        manager.engine = engine

        # When each line is generated individually.
        for index in range(3):
            success, _ = manager.generate_chunk_audio(index)
            self.assertTrue(success)
        individual_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

        # Then canonical speakers and selected route metadata are correct.
        self.assertEqual(
            [call["speaker"] for call in engine.single_calls],
            expected_speakers,
        )
        self.assertEqual(
            [chunk["experimental_prompt_route"] for chunk in individual_chunks],
            expected_routes,
        )

        # When those same lines are regenerated through mass generation.
        batch_result = manager.generate_chunks_batch([0, 1, 2], batch_size=3)
        self.assertEqual(batch_result["completed"], [0, 1, 2])
        mass_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

        # Then mass generation uses the same canonical speakers, directions, and
        # production-approved route decisions as individual generation.
        self.assertEqual(
            [call["speaker"] for call in engine.batch_calls],
            expected_speakers,
        )
        self.assertEqual(
            [call["instruct"] for call in engine.batch_calls],
            [chunk["instruct"] for chunk in mass_chunks],
        )
        self.assertEqual(
            [chunk["experimental_prompt_route"] for chunk in mass_chunks],
            expected_routes,
        )
        self.assertTrue(
            all(chunk["audio_production_prompt_approved"] for chunk in mass_chunks)
        )


if __name__ == "__main__":
    unittest.main()
