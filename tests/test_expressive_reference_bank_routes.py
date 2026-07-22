from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import app as app_module
from character_roster import save_character_roster
from expressive_reference_bank import (
    build_reference_bank,
    compute_bank_fingerprint,
    reference_bank_path,
    save_reference_bank,
)
from tests.test_voice_training_projects import VoiceTrainingProjectFixture
from voice_training_projects import (
    build_voice_training_project,
    save_voice_training_project,
    voice_training_project_path,
)


class FakeReferenceBankEngine:
    CLONE_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    def __init__(self, root: Path) -> None:
        self.root = root
        self._use_mlx = True
        self._mode = "local"
        self.design_calls = []
        self.clone_calls = []
        self.counter = 0

    @staticmethod
    def _write(path: Path, frequency: float) -> None:
        sample_rate = 24000
        time = np.linspace(0, 0.05, int(sample_rate * 0.05), endpoint=False)
        audio = (0.05 * np.sin(2 * np.pi * frequency * time)).astype(np.float32)
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, audio, sample_rate)

    def generate_voice_design(
        self,
        description,
        sample_text,
        language=None,
        seed=-1,
    ):
        del language
        self.counter += 1
        path = self.root / "generated" / f"design_{self.counter}.wav"
        self._write(path, 220.0 + self.counter)
        self.design_calls.append(
            {
                "description": description,
                "sample_text": sample_text,
                "seed": seed,
            }
        )
        return str(path), 24000

    def _init_mlx(self):
        return self

    def generate_instruction_controlled_clone(
        self,
        *,
        text,
        ref_audio,
        ref_text,
        instruct,
        output_path,
        temperature,
        top_k,
        top_p,
        repetition_penalty,
        max_tokens,
        **_kwargs,
    ):
        self.counter += 1
        self._write(Path(output_path), 660.0 + self.counter)
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

    def generate_clone_voice(
        self,
        text,
        speaker,
        voice_config,
        output_path,
        instruct_text="",
    ):
        del instruct_text
        self.counter += 1
        self._write(Path(output_path), 440.0 + self.counter)
        self.clone_calls.append(
            {
                "text": text,
                "speaker": speaker,
                "voice_config": voice_config,
            }
        )
        return True


class ExpressiveReferenceBankRouteTests(
    unittest.TestCase,
    VoiceTrainingProjectFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
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
        self.projects = self.root / "voice_training_projects"
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
        save_voice_training_project(
            self.project,
            self.project_path,
        )
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.voice_config = self.root / "voice_config.json"
        self.voice_config.write_text(
            json.dumps({"THE DOCTOR": {"type": "custom", "unknown": "keep"}}),
            encoding="utf-8",
        )
        self.engine = FakeReferenceBankEngine(self.root)
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CHARACTER_ROSTER_PATH", str(self.roster_path)),
            patch.object(app_module, "VOICE_TRAINING_PROJECTS_DIR", str(self.projects)),
            patch.object(app_module, "VOICE_CONFIG_PATH", str(self.voice_config)),
            patch.object(
                app_module,
                "_current_voice_backend_capabilities",
                return_value={
                    "expressive_clone": {"supported": True}
                },
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def create_bank(self, seed: int = 314159) -> dict:
        response = self.client.post(
            f"/api/expressive_reference_banks/{self.character_id}/create",
            json={
                "identity_seed": seed,
                "source_clip_id": self.project[
                    "selected_reference_sample"
                ]["clip_id"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def generate_style(self, bank: dict, style_key: str) -> dict:
        with patch.object(
            app_module.project_manager,
            "get_engine",
            return_value=self.engine,
        ):
            response = self.client.post(
                f"/api/expressive_reference_banks/{self.character_id}/generate",
                json={
                    "bank_fingerprint": bank["bank_fingerprint"],
                    "style_key": style_key,
                    "reference_text": f"The {style_key} reference line.",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        reference = result["reference"]
        review = self.client.post(
            f"/api/expressive_reference_banks/{self.character_id}/action",
            json={
                "bank_fingerprint": result["bank"]["bank_fingerprint"],
                "action": "review_reference",
                "payload": {
                    "reference_id": reference["reference_id"],
                    "source_identity_retention_passed": True,
                    "identity_drift_passed": True,
                    "emotion_match_passed": True,
                    "pronunciation_passed": True,
                    "pace_passed": True,
                    "notes": "Approved fixture.",
                    "reviewed_at_utc": self.TIME,
                },
            },
        )
        self.assertEqual(review.status_code, 200, review.text)
        return review.json()

    def test_status_is_model_free_and_static_route_precedes_dynamic(self) -> None:
        with patch.object(app_module.project_manager, "get_engine") as engine:
            response = self.client.get("/api/expressive_reference_banks/status")
        self.assertEqual(response.status_code, 200, response.text)
        engine.assert_not_called()
        payload = response.json()
        doctor = next(
            item for item in payload["entries"]
            if item["character_id"] == self.character_id
        )
        self.assertEqual(doctor["status"], "absent")
        paths = [route.path for route in app_module.app.routes]
        self.assertLess(
            paths.index("/api/expressive_reference_banks/status"),
            paths.index("/api/expressive_reference_banks/{character_id}"),
        )

    def test_create_get_and_review_action_are_model_free(self) -> None:
        with patch.object(app_module.project_manager, "get_engine") as engine:
            bank = self.create_bank()
            fetched = self.client.get(
                f"/api/expressive_reference_banks/{self.character_id}"
            )
        engine.assert_not_called()
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json(), bank)

    def test_reference_audio_route_serves_only_current_verified_assets(self) -> None:
        bank = self.create_bank()
        neutral = next(
            item
            for item in bank["references"]
            if item["style_key"] == bank["neutral_style_key"]
        )
        bank_path = reference_bank_path(self.projects, self.character_id)
        audio_path = (bank_path.parent / neutral["audio_path"]).resolve()

        response = self.client.get(
            f"/api/expressive_reference_banks/{self.character_id}/audio/"
            f"reference/{neutral['reference_id']}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, audio_path.read_bytes())

        audio_path.write_bytes(b"tampered expressive reference")
        invalid = self.client.get(
            f"/api/expressive_reference_banks/{self.character_id}/audio/"
            f"reference/{neutral['reference_id']}"
        )
        self.assertEqual(invalid.status_code, 409, invalid.text)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "reference_bank_audio_invalid",
        )

        missing = self.client.get(
            f"/api/expressive_reference_banks/{self.character_id}/audio/"
            "reference/reference_missing"
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "reference_bank_audio_not_found",
        )

    def test_generate_route_uses_owned_identity_and_controlled_clone(self) -> None:
        bank = self.create_bank(seed=24680)
        updated = self.generate_style(bank, "urgency")
        call = next(
            item for item in self.engine.clone_calls
            if item.get("controlled") is True
        )
        self.assertEqual(
            Path(call["ref_audio"]).resolve(),
            (
                self.project_path.parent
                / bank["identity_source"]["audio_path"]
            ).resolve(),
        )
        self.assertEqual(
            call["ref_text"],
            bank["identity_source"]["exact_transcript"],
        )
        self.assertIn("urgent", call["instruct"].casefold())
        self.assertIn("exact supplied speaker identity", call["instruct"])
        self.assertEqual(self.engine.design_calls, [])
        reference = next(
            item
            for item in updated["references"]
            if item["style_key"] == "urgency"
        )
        self.assertTrue(reference["review"]["approved"])
        self.assertEqual(
            reference["source_kind"],
            "qwen_icl_instruction_experimental",
        )
        self.assertEqual(
            reference["source_clip_id"],
            bank["identity_source"]["source_clip_id"],
        )

    def test_compare_route_generates_all_three_modes(self) -> None:
        bank = self.create_bank()
        bank = self.generate_style(bank, "neutral")
        bank = self.generate_style(bank, "urgency")
        with patch.object(
            app_module.project_manager,
            "get_engine",
            return_value=self.engine,
        ):
            response = self.client.post(
                f"/api/expressive_reference_banks/{self.character_id}/compare",
                json={
                    "bank_fingerprint": bank["bank_fingerprint"],
                    "lines": [
                        {
                            "text": "We have to leave now.",
                            "instruct": "Urgent warning.",
                        }
                    ],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        outputs = response.json()["outputs"]
        self.assertEqual(len(outputs), 3)
        self.assertEqual(
            {item["mode"] for item in outputs},
            {
                "reference_bank_clone",
                "single_reference_clone",
                "direct_voice_design",
            },
        )
        controlled_calls = [
            item for item in self.engine.clone_calls
            if item.get("controlled") is True
        ]
        comparison_calls = [
            item for item in self.engine.clone_calls
            if item.get("controlled") is not True
        ]
        self.assertEqual(len(controlled_calls), 2)
        self.assertEqual(len(comparison_calls), 2)
        self.assertEqual(len(self.engine.design_calls), 1)

        for output in outputs:
            audio = self.client.get(
                f"/api/expressive_reference_banks/{self.character_id}/audio/"
                f"comparison/{output['line_index']}/{output['mode']}"
            )
            self.assertEqual(audio.status_code, 200, audio.text)
            self.assertGreater(len(audio.content), 0)

        invalid_mode = self.client.get(
            f"/api/expressive_reference_banks/{self.character_id}/audio/"
            "comparison/0/not_a_mode"
        )
        self.assertEqual(invalid_mode.status_code, 404, invalid_mode.text)
        self.assertEqual(
            invalid_mode.json()["detail"]["code"],
            "reference_bank_audio_not_found",
        )
        invalid_line = self.client.get(
            f"/api/expressive_reference_banks/{self.character_id}/audio/"
            "comparison/-1/reference_bank_clone"
        )
        self.assertEqual(invalid_line.status_code, 404, invalid_line.text)

    def test_assignment_route_is_model_free_and_explicit(self) -> None:
        # Use the pure owner to create a reduced complete fixture, then exercise
        # only the real assignment route.
        bank = self.create_bank(seed=42)
        bank["required_style_keys"] = ["neutral"]
        bank["bank_fingerprint"] = compute_bank_fingerprint(bank)
        bank_path = reference_bank_path(self.projects, self.character_id)
        save_reference_bank(bank, bank_path)
        bank = self.generate_style(bank, "neutral")
        with patch.object(
            app_module.project_manager,
            "get_engine",
            return_value=self.engine,
        ):
            comparison = self.client.post(
                f"/api/expressive_reference_banks/{self.character_id}/compare",
                json={
                    "bank_fingerprint": bank["bank_fingerprint"],
                    "lines": [{"text": "Hello.", "instruct": "Neutral."}],
                },
            )
        self.assertEqual(comparison.status_code, 200, comparison.text)
        reviewed = self.client.post(
            f"/api/expressive_reference_banks/{self.character_id}/action",
            json={
                "bank_fingerprint": comparison.json()["bank"]["bank_fingerprint"],
                "action": "review_comparison",
                "payload": {
                    "source_identity_retention_passed": True,
                    "identity_consistency_passed": True,
                    "emotion_match_passed": True,
                    "pronunciation_passed": True,
                    "pace_passed": True,
                    "long_form_drift_passed": True,
                    "notes": "Approved.",
                    "reviewed_at_utc": self.TIME,
                },
            },
        )
        approved = self.client.post(
            f"/api/expressive_reference_banks/{self.character_id}/action",
            json={
                "bank_fingerprint": reviewed.json()["bank_fingerprint"],
                "action": "approve_bank",
                "payload": {},
            },
        )
        with patch.object(app_module.project_manager, "get_engine") as engine:
            assigned = self.client.post(
                f"/api/expressive_reference_banks/{self.character_id}/assign",
                json={
                    "bank_fingerprint": approved.json()["bank_fingerprint"],
                    "assign": True,
                },
            )
        engine.assert_not_called()
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertIn(
            "reference_bank_path",
            assigned.json()["voice_config"]["THE DOCTOR"],
        )

    def test_stale_and_invalid_requests_are_machine_readable(self) -> None:
        bank = self.create_bank()
        stale = self.client.post(
            f"/api/expressive_reference_banks/{self.character_id}/action",
            json={
                "bank_fingerprint": "0" * 64,
                "action": "return_to_draft",
                "payload": {},
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "expressive_reference_bank_conflict",
        )
        invalid = self.client.post(
            f"/api/expressive_reference_banks/{self.character_id}/action",
            json={
                "bank_fingerprint": bank["bank_fingerprint"],
                "action": "invent_voice",
                "payload": {},
            },
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_routes_are_registered_once(self) -> None:
        registrations = [
            (route.path, frozenset(getattr(route, "methods", set())))
            for route in app_module.app.routes
        ]
        expected = (
            ("/api/expressive_reference_banks/status", "GET"),
            ("/api/expressive_reference_banks/{character_id}", "GET"),
            (
                "/api/expressive_reference_banks/{character_id}/audio/"
                "reference/{reference_id}",
                "GET",
            ),
            (
                "/api/expressive_reference_banks/{character_id}/audio/"
                "comparison/{line_index}/{mode}",
                "GET",
            ),
            ("/api/expressive_reference_banks/{character_id}/create", "POST"),
            ("/api/expressive_reference_banks/{character_id}/generate", "POST"),
            ("/api/expressive_reference_banks/{character_id}/action", "POST"),
            ("/api/expressive_reference_banks/{character_id}/compare", "POST"),
            ("/api/expressive_reference_banks/{character_id}/assign", "POST"),
        )
        for path, method in expected:
            self.assertEqual(
                sum(
                    route_path == path and method in methods
                    for route_path, methods in registrations
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
