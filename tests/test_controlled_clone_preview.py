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
from controlled_clone_approval import clear_controlled_clone_approvals
from controlled_clone_preview import (
    CONTROLLED_CLONE_BACKEND,
    ControlledClonePreviewUnavailableError,
    ControlledClonePreviewValidationError,
    build_preview_fingerprint,
    generate_controlled_clone_preview,
)


class FakeControlledGenerator:
    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> bool:
        self.calls.append(dict(kwargs))
        if not self.succeed:
            return False
        audio = np.linspace(-0.1, 0.1, 24000, dtype=np.float32)
        sf.write(kwargs["output_path"], audio, 24000)
        return True


class ControlledClonePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clone_dir = self.root / "clone_voices"
        self.clone_dir.mkdir()
        self.reference = self.clone_dir / "owned_reference.wav"
        sf.write(
            self.reference,
            np.zeros(24000, dtype=np.float32),
            24000,
        )
        self.voice_config = self.root / "voice_config.json"
        self.chunks = self.root / "chunks.json"
        self.production_audio = self.root / "voicelines" / "chunk_0.wav"
        self.production_audio.parent.mkdir()
        self.voice_config.write_text(
            json.dumps({"DOCTOR": {"type": "clone"}}),
            encoding="utf-8",
        )
        self.chunks.write_text("[]", encoding="utf-8")
        self.production_audio.write_bytes(b"production-audio")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def protected_bytes(self) -> dict[str, bytes]:
        return {
            "voice_config": self.voice_config.read_bytes(),
            "chunks": self.chunks.read_bytes(),
            "production_audio": self.production_audio.read_bytes(),
            "reference": self.reference.read_bytes(),
        }

    def request(self, generator: FakeControlledGenerator | None = None):
        return generate_controlled_clone_preview(
            root_dir=self.root,
            ref_audio="clone_voices/owned_reference.wav",
            ref_text="This is the exact supplied transcript.",
            text="Tell me the truth.",
            instruct="Controlled anger with restrained intensity.",
            character_style="Preserve the supplied identity and accent.",
            temperature=0.75,
            top_k=50,
            top_p=0.95,
            repetition_penalty=1.5,
            max_tokens=2000,
            seed=4242,
            generator=generator or FakeControlledGenerator(),
        )

    def test_preview_is_confined_and_production_file_pure(self) -> None:
        before = self.protected_bytes()
        generator = FakeControlledGenerator()
        payload = self.request(generator)
        self.assertEqual(payload["status"], "generated")
        self.assertEqual(payload["backend"], CONTROLLED_CLONE_BACKEND)
        self.assertTrue(payload["requires_listen_confirmation"])
        self.assertFalse(payload["production_configuration_changed"])
        self.assertFalse(payload["production_audio_changed"])
        self.assertEqual(payload["audio_duration_seconds"], 1.0)
        self.assertGreaterEqual(payload["real_time_factor"], 0.0)
        self.assertRegex(payload["preview_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            payload["configuration_fingerprint"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            payload["reference_file"],
            "clone_voices/owned_reference.wav",
        )
        output = self.root / payload["audio_url"].removeprefix("/")
        self.assertTrue(output.is_file())
        self.assertTrue(
            output.resolve().is_relative_to(
                (self.clone_dir / "previews").resolve()
            )
        )
        self.assertEqual(before, self.protected_bytes())
        call = generator.calls[0]
        self.assertIn("Controlled anger", call["instruct"])
        self.assertIn("supplied identity", call["instruct"])
        self.assertEqual(call["seed"], 4242)
        self.assertEqual(call["request_label"], "preview")
        self.assertEqual(payload["settings"]["seed"], 4242)

    def test_reference_path_rejects_absolute_traversal_and_unapproved_roots(self) -> None:
        requests = (
            str(self.reference),
            "../owned_reference.wav",
            "uploads/owned_reference.wav",
        )
        uploads = self.root / "uploads"
        uploads.mkdir()
        sf.write(
            uploads / "owned_reference.wav",
            np.zeros(100, dtype=np.float32),
            24000,
        )
        for value in requests:
            with self.subTest(value=value):
                with self.assertRaises(ControlledClonePreviewValidationError):
                    generate_controlled_clone_preview(
                        root_dir=self.root,
                        ref_audio=value,
                        ref_text="Exact transcript.",
                        text="Preview text.",
                        instruct="Neutral delivery.",
                        generator=FakeControlledGenerator(),
                    )

    def test_reference_under_voice_training_project_is_allowed(self) -> None:
        reference = (
            self.root
            / "voice_training_projects"
            / "character_aaaaaaaaaaaaaaaaaaaa"
            / "recordings"
            / "clip.wav"
        )
        reference.parent.mkdir(parents=True)
        sf.write(reference, np.zeros(24000, dtype=np.float32), 24000)
        payload = generate_controlled_clone_preview(
            root_dir=self.root,
            ref_audio=reference.relative_to(self.root).as_posix(),
            ref_text="Exact owned recording transcript.",
            text="Preview text.",
            instruct="Soft reassurance.",
            generator=FakeControlledGenerator(),
        )
        self.assertEqual(payload["status"], "generated")

    def test_invalid_text_and_settings_are_rejected_before_generation(self) -> None:
        generator = FakeControlledGenerator()
        with self.assertRaises(ControlledClonePreviewValidationError):
            generate_controlled_clone_preview(
                root_dir=self.root,
                ref_audio="clone_voices/owned_reference.wav",
                ref_text="Exact transcript.",
                text="",
                instruct="Neutral delivery.",
                generator=generator,
            )
        with self.assertRaisesRegex(
            ControlledClonePreviewValidationError,
            "temperature",
        ):
            generate_controlled_clone_preview(
                root_dir=self.root,
                ref_audio="clone_voices/owned_reference.wav",
                ref_text="Exact transcript.",
                text="Preview text.",
                instruct="Neutral delivery.",
                temperature=0.0,
                generator=generator,
            )
        self.assertEqual(generator.calls, [])

    def test_generator_failure_cleans_temporary_audio(self) -> None:
        with self.assertRaises(ControlledClonePreviewUnavailableError):
            self.request(FakeControlledGenerator(succeed=False))
        preview_dir = self.clone_dir / "previews"
        self.assertEqual(
            list(preview_dir.glob("*.tmp.wav")) if preview_dir.exists() else [],
            [],
        )
        self.assertEqual(
            list(preview_dir.glob("controlled_*.wav"))
            if preview_dir.exists()
            else [],
            [],
        )

    def test_fingerprint_binds_reference_text_instruction_and_settings(self) -> None:
        base = {
            "reference_audio_sha256": "a" * 64,
            "reference_text": "Reference text.",
            "text": "Preview text.",
            "instruct": "Neutral.",
            "character_style": "Same identity.",
            "temperature": 0.75,
            "top_k": 50,
            "top_p": 0.95,
            "repetition_penalty": 1.5,
            "max_tokens": 2000,
            "seed": 4242,
        }
        first = build_preview_fingerprint(**base)
        for key, value in (
            ("reference_text", "Changed reference text."),
            ("instruct", "Angry."),
            ("temperature", 0.9),
            ("seed", 4243),
        ):
            changed = dict(base)
            changed[key] = value
            self.assertNotEqual(
                first,
                build_preview_fingerprint(**changed),
            )


class FakeMLXBackend:
    def __init__(self) -> None:
        self.generator = FakeControlledGenerator()

    def generate_instruction_controlled_clone(self, **kwargs):
        return self.generator(**kwargs)


class FakeEngine:
    def __init__(self, *, use_mlx: bool = True) -> None:
        self._use_mlx = use_mlx
        self.backend = FakeMLXBackend()

    def _init_mlx(self):
        return self.backend


class ControlledClonePreviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_controlled_clone_approvals()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "clone_voices").mkdir()
        sf.write(
            self.root / "clone_voices" / "owned_reference.wav",
            np.zeros(24000, dtype=np.float32),
            24000,
        )
        self.voice_config = self.root / "voice_config.json"
        self.chunks = self.root / "chunks.json"
        self.voice_config.write_text("{}", encoding="utf-8")
        self.chunks.write_text("[]", encoding="utf-8")
        self.engine = FakeEngine()
        self.patches = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "_current_voice_backend_capabilities",
                return_value={"expressive_clone": {"supported": True}},
            ),
            patch.object(
                app_module.project_manager,
                "get_engine",
                return_value=self.engine,
            ),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        clear_controlled_clone_approvals()
        self.temp.cleanup()

    def payload(self) -> dict:
        return {
            "speaker": "DOCTOR",
            "ref_audio": "clone_voices/owned_reference.wav",
            "ref_text": "Exact supplied transcript.",
            "text": "Tell me the truth.",
            "instruct": "Controlled anger.",
            "character_style": "Preserve supplied identity.",
            "temperature": 0.75,
            "top_k": 50,
            "top_p": 0.95,
            "repetition_penalty": 1.5,
            "max_tokens": 2000,
        }

    def test_route_generates_without_mutating_production_state(self) -> None:
        before = {
            "voice_config": self.voice_config.read_bytes(),
            "chunks": self.chunks.read_bytes(),
        }
        response = self.client.post(
            "/api/clone_voices/controlled_preview",
            json=self.payload(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["backend"], CONTROLLED_CLONE_BACKEND)
        self.assertTrue(payload["requires_listen_confirmation"])
        self.assertRegex(
            payload["configuration_fingerprint"],
            r"^[0-9a-f]{64}$",
        )
        confirmation = self.client.post(
            "/api/clone_voices/controlled_preview/confirm",
            json={
                "speaker": "DOCTOR",
                "preview_fingerprint": payload["preview_fingerprint"],
                "configuration_fingerprint": payload[
                    "configuration_fingerprint"
                ],
            },
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.text)
        self.assertTrue(confirmation.json()["approval_token"])
        self.assertEqual(before["voice_config"], self.voice_config.read_bytes())
        self.assertEqual(before["chunks"], self.chunks.read_bytes())
        self.assertEqual(len(self.engine.backend.generator.calls), 1)

    def test_confirmation_rejects_mismatched_speaker(self) -> None:
        generated = self.client.post(
            "/api/clone_voices/controlled_preview",
            json=self.payload(),
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        payload = generated.json()
        response = self.client.post(
            "/api/clone_voices/controlled_preview/confirm",
            json={
                "speaker": "MASTER",
                "preview_fingerprint": payload["preview_fingerprint"],
                "configuration_fingerprint": payload[
                    "configuration_fingerprint"
                ],
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "controlled_clone_preview_mismatch",
        )

    def test_invalid_reference_returns_machine_readable_422(self) -> None:
        payload = self.payload()
        payload["ref_audio"] = "../../etc/passwd"
        response = self.client.post(
            "/api/clone_voices/controlled_preview",
            json=payload,
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "controlled_clone_preview_rejected",
        )
        self.assertEqual(self.engine.backend.generator.calls, [])

    def test_unavailable_capability_returns_409_before_engine(self) -> None:
        with patch.object(
            app_module,
            "_current_voice_backend_capabilities",
            return_value={"expressive_clone": {"supported": False}},
        ):
            response = self.client.post(
                "/api/clone_voices/controlled_preview",
                json=self.payload(),
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "controlled_clone_preview_unavailable",
        )
        self.assertEqual(self.engine.backend.generator.calls, [])

    def test_non_mlx_runtime_returns_409(self) -> None:
        with patch.object(
            app_module.project_manager,
            "get_engine",
            return_value=FakeEngine(use_mlx=False),
        ):
            response = self.client.post(
                "/api/clone_voices/controlled_preview",
                json=self.payload(),
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("MLX", response.json()["detail"]["message"])

    def test_request_schema_and_route_registration(self) -> None:
        invalid = self.client.post(
            "/api/clone_voices/controlled_preview",
            json={"ref_audio": "clone_voices/owned_reference.wav"},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        registrations = [
            (route.path, frozenset(getattr(route, "methods", set())))
            for route in app_module.app.routes
        ]
        self.assertEqual(
            sum(
                path == "/api/clone_voices/controlled_preview"
                and "POST" in methods
                for path, methods in registrations
            ),
            1,
        )
        self.assertEqual(
            sum(
                path == "/api/clone_voices/controlled_preview/confirm"
                and "POST" in methods
                for path, methods in registrations
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
