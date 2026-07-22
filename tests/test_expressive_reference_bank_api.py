from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import soundfile as sf
import numpy as np

from character_roster import save_character_roster
from expressive_reference_bank import (
    add_reference,
    build_reference_bank,
    read_reference_bank,
    reference_bank_path,
    review_reference,
    save_reference_bank,
    sha256_file,
)
from expressive_reference_bank_api import (
    ExpressiveReferenceBankApiError,
    apply_reference_bank_action_payload,
    assign_reference_bank_payload,
    create_reference_bank_payload,
    generate_comparison_payload,
    generate_reference_payload,
    get_reference_bank_payload,
    get_reference_bank_status_payload,
)
from tests.test_voice_training_projects import VoiceTrainingProjectFixture
from voice_training_projects import (
    build_voice_training_project,
    save_voice_training_project,
    voice_training_project_path,
)


class FakeAudioBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.design_calls: list[dict] = []
        self.clone_calls: list[dict] = []
        self.counter = 0

    @staticmethod
    def _write(path: Path, frequency: float) -> None:
        sample_rate = 24000
        time = np.linspace(0.0, 0.05, int(sample_rate * 0.05), endpoint=False)
        audio = (0.05 * np.sin(2 * np.pi * frequency * time)).astype(np.float32)
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, audio, sample_rate)

    def design(self, *, description: str, sample_text: str, seed: int):
        self.counter += 1
        path = self.root / "generated" / f"design_{self.counter}.wav"
        self._write(path, 220.0 + self.counter)
        self.design_calls.append(
            {
                "description": description,
                "sample_text": sample_text,
                "seed": seed,
                "path": str(path),
            }
        )
        return str(path), 24000

    def clone(
        self,
        *,
        text: str,
        ref_audio: str,
        ref_text: str,
        output_path: str,
    ):
        self.counter += 1
        path = Path(output_path)
        self._write(path, 440.0 + self.counter)
        self.clone_calls.append(
            {
                "text": text,
                "ref_audio": ref_audio,
                "ref_text": ref_text,
                "output_path": output_path,
            }
        )
        return True

    def controlled_clone(
        self,
        *,
        text: str,
        ref_audio: str,
        ref_text: str,
        instruct: str,
        output_path: str,
        temperature: float,
        top_k: int,
        top_p: float,
        repetition_penalty: float,
        max_tokens: int,
        **_kwargs,
    ):
        self.counter += 1
        path = Path(output_path)
        self._write(path, 660.0 + self.counter)
        self.clone_calls.append(
            {
                "text": text,
                "ref_audio": ref_audio,
                "ref_text": ref_text,
                "instruct": instruct,
                "output_path": output_path,
                "temperature": temperature,
                "top_k": top_k,
                "top_p": top_p,
                "repetition_penalty": repetition_penalty,
                "max_tokens": max_tokens,
                "controlled": True,
            }
        )
        return True


class ExpressiveReferenceBankApiTests(
    unittest.TestCase,
    VoiceTrainingProjectFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / "voice_training_projects"
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.roster = self.approved_roster(self.source_path)
        self.roster_path = self.root / "character_roster.json"
        save_character_roster(
            self.roster,
            self.roster_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.character_id = self.roster["entries"][0]["id"]
        project = build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.character_id,
            priority="primary",
            created_at_utc=self.TIME,
        )
        self.project = self.approve_persona(project)
        self.project_path = voice_training_project_path(
            self.projects,
            self.character_id,
        )
        self.project = self.configure_owned_recording_project(
            self.project,
            self.project_path.parent,
        )
        save_voice_training_project(self.project, self.project_path)
        self.backend = FakeAudioBackend(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_bank(self, *, seed: int = 314159) -> dict:
        return create_reference_bank_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            identity_seed=seed,
            source_clip_id=self.project["selected_reference_sample"][
                "clip_id"
            ],
            source_text=self.SOURCE_TEXT,
            current_source_fingerprint=self.roster["source"]["fingerprint"],
            created_at_utc=self.TIME,
        )

    def generate_and_review(
        self,
        bank: dict,
        style_key: str,
    ) -> dict:
        generated = generate_reference_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=bank["bank_fingerprint"],
            style_key=style_key,
            reference_text=f"The {style_key} comparison reference.",
            controlled_clone_generator=self.backend.controlled_clone,
            generation_backend="mlx-audio-qwen3-icl-instruction-experimental",
            model="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            source_text=self.SOURCE_TEXT,
            current_source_fingerprint=self.roster["source"]["fingerprint"],
            generated_at_utc=self.TIME,
        )
        reference = generated["reference"]
        return apply_reference_bank_action_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=generated["bank"]["bank_fingerprint"],
            action="review_reference",
            payload={
                "reference_id": reference["reference_id"],
                "source_identity_retention_passed": True,
                "identity_drift_passed": True,
                "emotion_match_passed": True,
                "pronunciation_passed": True,
                "pace_passed": True,
                "notes": "Approved in API fixture.",
                "reviewed_at_utc": self.TIME,
            },
            source_text=self.SOURCE_TEXT,
            current_source_fingerprint=self.roster["source"]["fingerprint"],
        )

    def test_status_is_file_pure_and_missing_roster_is_available_false(self) -> None:
        missing = self.root / "missing_roster.json"
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        status = get_reference_bank_status_payload(
            approved_roster_path=missing,
            projects_root=self.projects,
        )
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse(status["available"])
        self.assertEqual(status["entries"], [])

    def test_create_and_read_are_bound_to_current_roster(self) -> None:
        bank = self.create_bank()
        loaded = get_reference_bank_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            source_text=self.SOURCE_TEXT,
            current_source_fingerprint=self.roster["source"]["fingerprint"],
        )
        self.assertEqual(loaded, bank)
        with self.assertRaises(ExpressiveReferenceBankApiError) as duplicate:
            self.create_bank()
        self.assertEqual(duplicate.exception.status_code, 409)

    def test_generate_reference_uses_owned_identity_and_controlled_delivery(self) -> None:
        bank = self.create_bank(seed=98765)
        result = generate_reference_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=bank["bank_fingerprint"],
            style_key="urgency",
            reference_text="We have to move before dawn.",
            controlled_clone_generator=self.backend.controlled_clone,
            generation_backend="mlx-audio-qwen3-icl-instruction-experimental",
            model="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            source_text=self.SOURCE_TEXT,
            current_source_fingerprint=self.roster["source"]["fingerprint"],
            generated_at_utc=self.TIME,
        )
        call = self.backend.clone_calls[0]
        identity = bank["identity_source"]
        self.assertEqual(
            Path(call["ref_audio"]).resolve(),
            (self.project_path.parent / identity["audio_path"]).resolve(),
        )
        self.assertEqual(call["ref_text"], identity["exact_transcript"])
        self.assertIn("urgent", call["instruct"].casefold())
        self.assertIn("exact supplied speaker identity", call["instruct"])
        self.assertEqual(call["temperature"], 0.75)
        self.assertEqual(call["top_k"], 50)
        self.assertEqual(call["top_p"], 0.95)
        self.assertEqual(call["repetition_penalty"], 1.5)
        self.assertEqual(call["max_tokens"], 2000)
        self.assertEqual(self.backend.design_calls, [])
        reference = result["reference"]
        self.assertEqual(reference["style_key"], "urgency")
        self.assertEqual(
            reference["source_kind"],
            "qwen_icl_instruction_experimental",
        )
        self.assertEqual(
            reference["source_clip_id"],
            identity["source_clip_id"],
        )
        self.assertIsNone(reference["seed"])
        copied = self.project_path.parent / reference["audio_path"]
        self.assertTrue(copied.is_file())
        self.assertEqual(sha256_file(copied), reference["audio_sha256"])

    def test_generate_rejects_unsupported_style_and_stale_fingerprint(self) -> None:
        bank = self.create_bank()
        with self.assertRaises(ExpressiveReferenceBankApiError) as style:
            generate_reference_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects,
                character_id=self.character_id,
                expected_fingerprint=bank["bank_fingerprint"],
                style_key="sarcastic_octopus",
                reference_text="No.",
                controlled_clone_generator=self.backend.controlled_clone,
                generation_backend="mlx-audio-qwen3-icl-instruction-experimental",
                model="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            )
        self.assertEqual(style.exception.status_code, 422)
        self.assertEqual(style.exception.code, "unsupported_reference_style")

        with self.assertRaises(ExpressiveReferenceBankApiError) as stale:
            generate_reference_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects,
                character_id=self.character_id,
                expected_fingerprint="0" * 64,
                style_key="neutral",
                reference_text="No.",
                controlled_clone_generator=self.backend.controlled_clone,
                generation_backend="mlx-audio-qwen3-icl-instruction-experimental",
                model="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            )
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(stale.exception.code, "stale_expressive_reference_bank")

    def test_comparison_generates_three_modes_per_line(self) -> None:
        bank = self.create_bank()
        bank = self.generate_and_review(bank, "neutral")
        bank = self.generate_and_review(bank, "urgency")
        comparison = generate_comparison_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=bank["bank_fingerprint"],
            test_lines=[
                {
                    "text": "We have to leave now.",
                    "instruct": "Urgent, desperate warning.",
                },
                {
                    "text": "Everything is quiet.",
                    "instruct": "Neutral delivery.",
                },
            ],
            design_generator=self.backend.design,
            clone_generator=self.backend.clone,
            source_text=self.SOURCE_TEXT,
            current_source_fingerprint=self.roster["source"]["fingerprint"],
        )
        self.assertEqual(len(comparison["outputs"]), 6)
        controlled_calls = [
            item for item in self.backend.clone_calls
            if item.get("controlled") is True
        ]
        comparison_clone_calls = [
            item for item in self.backend.clone_calls
            if item.get("controlled") is not True
        ]
        self.assertEqual(len(controlled_calls), 2)
        self.assertEqual(len(comparison_clone_calls), 4)
        self.assertEqual(len(self.backend.design_calls), 2)
        modes = {item["mode"] for item in comparison["outputs"]}
        self.assertEqual(
            modes,
            {
                "reference_bank_clone",
                "single_reference_clone",
                "direct_voice_design",
            },
        )
        selected = next(
            item
            for item in comparison["outputs"]
            if item["mode"] == "reference_bank_clone"
            and item["line_index"] == 0
        )
        self.assertEqual(selected["style_key"], "urgency")
        self.assertEqual(comparison["bank"]["comparison"]["status"], "generated")

    def test_assignment_and_clear_use_explicit_final_action(self) -> None:
        bank = self.create_bank()
        bank_path = reference_bank_path(self.projects, self.character_id)
        # Use a reduced required style fixture while retaining the real action path.
        bank["required_style_keys"] = ["neutral"]
        bank["bank_fingerprint"] = __import__(
            "expressive_reference_bank"
        ).compute_bank_fingerprint(bank)
        save_reference_bank(bank, bank_path)
        bank = self.generate_and_review(bank, "neutral")
        # Build a minimal approved comparison using valid generated audio.
        comparison = generate_comparison_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=bank["bank_fingerprint"],
            test_lines=[{"text": "Hello.", "instruct": "Neutral delivery."}],
            design_generator=self.backend.design,
            clone_generator=self.backend.clone,
        )
        bank = apply_reference_bank_action_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=comparison["bank"]["bank_fingerprint"],
            action="review_comparison",
            payload={
                "source_identity_retention_passed": True,
                "identity_consistency_passed": True,
                "emotion_match_passed": True,
                "pronunciation_passed": True,
                "pace_passed": True,
                "long_form_drift_passed": True,
                "notes": "Approved comparison.",
                "reviewed_at_utc": self.TIME,
            },
        )
        bank = apply_reference_bank_action_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=bank["bank_fingerprint"],
            action="approve_bank",
            payload={},
        )
        voice_config_path = self.root / "voice_config.json"
        voice_config_path.write_text(
            json.dumps({"THE DOCTOR": {"type": "custom", "unknown": 7}}),
            encoding="utf-8",
        )
        assigned = assign_reference_bank_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=bank["bank_fingerprint"],
            voice_config_path=voice_config_path,
            project_root=self.root,
            assign=True,
        )
        self.assertEqual(assigned["bank"]["production_assignment"]["status"], "assigned")
        self.assertEqual(assigned["voice_config"]["THE DOCTOR"]["unknown"], 7)
        self.assertIn(
            "reference_bank_path",
            assigned["voice_config"]["THE DOCTOR"],
        )

        cleared = assign_reference_bank_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            character_id=self.character_id,
            expected_fingerprint=assigned["bank"]["bank_fingerprint"],
            voice_config_path=voice_config_path,
            project_root=self.root,
            assign=False,
        )
        self.assertEqual(cleared["bank"]["production_assignment"]["status"], "unassigned")
        self.assertNotIn(
            "reference_bank_path",
            cleared["voice_config"]["THE DOCTOR"],
        )

    def test_source_mismatch_is_visible_in_status_and_blocks_mutation(self) -> None:
        bank = self.create_bank()
        status = get_reference_bank_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects,
            source_text=None,
            current_source_fingerprint="f" * 64,
        )
        self.assertFalse(status["source_compatible"])
        with self.assertRaises(ExpressiveReferenceBankApiError) as mismatch:
            apply_reference_bank_action_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects,
                character_id=self.character_id,
                expected_fingerprint=bank["bank_fingerprint"],
                action="return_to_draft",
                payload={},
                current_source_fingerprint="f" * 64,
            )
        self.assertEqual(mismatch.exception.code, "approved_roster_source_mismatch")


if __name__ == "__main__":
    unittest.main()
