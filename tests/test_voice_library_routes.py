from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from tests.voice_design_save_route_cases import VoiceDesignSaveRouteCases
from tests.voice_library_assignment_route_cases import VoiceLibraryAssignmentRouteCases
from tests.voice_library_range_preview_route_cases import VoiceLibraryRangePreviewRouteCases
from voice_library import VoiceLibraryError


class VoiceLibraryMethodCounts(TypedDict):
    built_in: int
    designed: int
    supplied_recording: int
    instruction_controlled: int
    adapter: int
    alias: int


class VoiceLibrarySummary(TypedDict):
    voice_count: int
    assigned_voice_count: int
    assignment_count: int
    invalid_voice_count: int
    method_counts: VoiceLibraryMethodCounts
    cast_character_count: int
    cast_blocker_count: int


class VoiceLibraryFilters(TypedDict):
    methods: list[str]
    states: list[str]


class VoiceLibraryPayload(TypedDict):
    schema_version: int
    project_id: str
    summary: VoiceLibrarySummary
    methods: list[dict[str, str]]
    filters: VoiceLibraryFilters
    voices: list[dict[str, str]]
    assignment_mutation_supported: bool
    cast_is_authoritative: bool
    fingerprint: str


class VoiceLibraryRouteTests(
    VoiceLibraryAssignmentRouteCases,
    VoiceLibraryRangePreviewRouteCases,
    VoiceDesignSaveRouteCases,
    unittest.TestCase,
):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def payload(self) -> VoiceLibraryPayload:
        return {
            "schema_version": 1,
            "project_id": "project_1",
            "summary": {
                "voice_count": 2,
                "assigned_voice_count": 1,
                "assignment_count": 1,
                "invalid_voice_count": 0,
                "method_counts": {
                    "built_in": 1,
                    "designed": 0,
                    "supplied_recording": 1,
                    "instruction_controlled": 0,
                    "adapter": 0,
                    "alias": 0,
                },
                "cast_character_count": 1,
                "cast_blocker_count": 0,
            },
            "methods": [],
            "filters": {
                "methods": ["built_in", "supplied_recording"],
                "states": ["available"],
            },
            "voices": [],
            "assignment_mutation_supported": True,
            "cast_is_authoritative": True,
            "fingerprint": "a" * 64,
        }

    def test_route_is_registered_once_and_only_for_get(self) -> None:
        matching = [
            route
            for route in app_module.app.routes
            if getattr(route, "path", None) == "/api/voice-library"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].methods, {"GET"})
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)("/api/voice-library")
            self.assertEqual(response.status_code, 405, response.text)

    def test_route_preserves_project_and_return_context(self) -> None:
        expected = self.payload()
        with patch.object(
            app_module,
            "build_voice_library",
            return_value=expected,
        ) as builder:
            response = self.client.get(
                "/api/voice-library",
                params={
                    "project_id": "project_1",
                    "return_route": "#/voices?method=supplied_recording",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        builder.assert_called_once_with(
            root_dir=app_module.ROOT_DIR,
            project_id="project_1",
            return_route="#/voices?method=supplied_recording",
            reusable_root_dir=app_module.LEGACY_ROOT_DIR,
        )

    def test_route_uses_active_project_identity_when_not_supplied(self) -> None:
        expected = self.payload()
        with (
            patch.object(app_module, "ACTIVE_PROJECT_ID", "active_project"),
            patch.object(
                app_module,
                "build_voice_library",
                return_value=expected,
            ) as builder,
        ):
            response = self.client.get("/api/voice-library")
        self.assertEqual(response.status_code, 200, response.text)
        builder.assert_called_once_with(
            root_dir=app_module.ROOT_DIR,
            project_id="active_project",
            return_route="#/voices",
            reusable_root_dir=app_module.LEGACY_ROOT_DIR,
        )

    def test_voice_library_error_remains_machine_readable(self) -> None:
        with patch.object(
            app_module,
            "build_voice_library",
            side_effect=VoiceLibraryError(
                "voice_library_config_invalid",
                "Voice configuration is invalid.",
            ),
        ):
            response = self.client.get("/api/voice-library")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "voice_library_config_invalid",
                "message": "Voice configuration is invalid.",
            },
        )

    def test_designed_voice_preview_is_staged_in_active_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "engine-output" / "generated.wav"
            generated.parent.mkdir()
            generated.write_bytes(b"generated-audition")
            project_designed = root / "project" / "designed_voices"

            class Engine:
                _use_mlx = True

                def generate_voice_design(self, **_kwargs):
                    return str(generated), 24_000

            with (
                patch.object(app_module.project_manager, "get_engine", return_value=Engine()),
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(project_designed)),
            ):
                response = self.client.post(
                    "/api/voice_design/preview",
                    json={
                        "description": "Warm, precise, with a restrained French accent.",
                        "sample_text": "A project audition.",
                        "language": "English",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            filename = Path(response.json()["audio_url"]).name
            self.assertEqual(filename, "generated.wav")
            self.assertEqual(
                response.json()["accent_pipeline"],
                {
                    "applied": True,
                    "label": "French",
                    "native_language": "French",
                    "output_language": "English",
                    "sequence": "native_seed_design -> output_clone",
                },
            )
            self.assertEqual(
                (project_designed / "previews" / filename).read_bytes(),
                b"generated-audition",
            )

    def test_designed_voice_preview_reports_accent_not_applied_without_mlx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "engine-output" / "generated.wav"
            generated.parent.mkdir()
            generated.write_bytes(b"non-mlx-audition")

            class Engine:
                _use_mlx = False

                def generate_voice_design(self, **_kwargs):
                    return str(generated), 24_000

            with (
                patch.object(app_module.project_manager, "get_engine", return_value=Engine()),
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(root / "designed_voices")),
            ):
                response = self.client.post(
                    "/api/voice_design/preview",
                    json={
                        "description": "Warm, precise, with a restrained French accent.",
                        "sample_text": "A non-MLX audition.",
                        "language": "English",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                response.json()["accent_pipeline"],
                {
                    "applied": False,
                    "label": "French",
                    "native_language": None,
                    "output_language": "English",
                    "sequence": "direct_voice_design",
                },
            )

    def test_designed_voice_range_preview_uses_fish_from_one_identity_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previews = root / "designed_voices" / "previews"
            previews.mkdir(parents=True)
            audition = previews / "range.wav"
            identity = previews / "identity.wav"
            audition.write_bytes(b"fish-range")
            identity.write_bytes(b"voice-design-identity")
            calls = []

            class Engine:
                _use_mlx = True

                def generate_voice_design_range_preview(self, **kwargs):
                    calls.append(kwargs)
                    return {
                        "audio_path": str(audition),
                        "identity_seed_path": str(identity),
                        "identity_seed_text": "A stable identity sentence.",
                        "sample_rate": 24_000,
                        "delivery_backend": "fish_s21_cloud",
                        "sequence": [
                            {"id": value}
                            for value in ("baseline", "happy", "sad", "angry")
                        ],
                    }

            with (
                patch.object(app_module.project_manager, "get_engine", return_value=Engine()),
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(root / "designed_voices")),
            ):
                response = self.client.post(
                    "/api/voice_design/range-preview",
                    json={
                        "description": "A compact, precise alto.",
                        "persona_context": "Dry, guarded, and intellectually agile.",
                        "sample_text": "A stable identity sentence.",
                        "language": "English",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(
                payload["audio_url"],
                "/designed_voices/previews/range.wav?revision=0",
            )
            self.assertEqual(
                payload["clone_source_url"],
                "/designed_voices/previews/identity.wav",
            )
            self.assertEqual(payload["delivery_backend"], "fish_s21_cloud")
            self.assertEqual(payload["identity_backend"], "mlx_qwen3_voice_design")
            self.assertTrue(payload["persona_context_applied"])
            self.assertEqual(
                [item["id"] for item in payload["sequence"]],
                ["baseline", "happy", "sad", "angry"],
            )
            self.assertEqual(calls[0]["persona_context"], "Dry, guarded, and intellectually agile.")

    def test_designed_voice_range_preview_regenerates_one_lane_and_returns_full_montage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previews = root / "designed_voices" / "previews"
            previews.mkdir(parents=True)
            audition = previews / "range.wav"
            identity = previews / "identity.wav"
            audition.write_bytes(b"updated-full-range")
            identity.write_bytes(b"unchanged-identity")
            calls = []

            class Engine:
                def regenerate_voice_design_range_lane(self, **kwargs):
                    calls.append(kwargs)
                    return {
                        "audio_path": str(audition),
                        "identity_seed_path": str(identity),
                        "identity_seed_text": "A stable identity sentence.",
                        "delivery_backend": "fish_s21_cloud",
                        "preview_fingerprint": "a" * 64,
                        "revision": 2,
                        "regenerated_lane": "angry",
                        "warnings": [],
                        "all_lanes_distinct": True,
                        "sequence": [
                            {"id": value}
                            for value in ("baseline", "happy", "sad", "angry")
                        ],
                    }

            with (
                patch.object(app_module.project_manager, "get_engine", return_value=Engine()),
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(root / "designed_voices")),
            ):
                response = self.client.post(
                    "/api/voice_design/range-preview/regenerate",
                    json={
                        "preview_fingerprint": "a" * 64,
                        "lane": "angry",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["regenerated_lane"], "angry")
            self.assertEqual(payload["revision"], 2)
            self.assertEqual(
                payload["audio_url"],
                "/designed_voices/previews/range.wav?revision=2",
            )
            self.assertEqual(
                [item["id"] for item in payload["sequence"]],
                ["baseline", "happy", "sad", "angry"],
            )
            self.assertEqual(calls, [{
                "preview_fingerprint": "a" * 64,
                "lane": "angry",
                "output_dir": previews.resolve(),
            }])

if __name__ == "__main__":
    unittest.main()
