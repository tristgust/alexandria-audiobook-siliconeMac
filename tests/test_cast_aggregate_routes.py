from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class CastAggregateRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        reference = self.root / "references" / "benny.wav"
        reference.parent.mkdir()
        reference.write_bytes(b"reference-audio")
        (self.root / "character_roster.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "id": "character_bernice",
                            "canonical_name": "Bernice Summerfield",
                            "display_name": "Bernice Summerfield",
                            "resolution_status": "resolved",
                            "speaking_status": "speaking",
                        },
                        {
                            "id": "character_benny_narrator",
                            "canonical_name": "Narrator (Benny)",
                            "display_name": "Narrator (Benny)",
                            "resolution_status": "resolved",
                            "speaking_status": "speaking",
                        },
                        {
                            "id": "character_village",
                            "canonical_name": "The Village",
                            "display_name": "The Village",
                            "resolution_status": "resolved",
                            "speaking_status": "non-speaking",
                        },
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.root / "annotated_script.json").write_text(
            json.dumps(
                [
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
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "BERNICE": {
                        "type": "custom",
                        "voice": "benny-main",
                    },
                    "NARRATOR (BENNY)": {
                        "type": "clone",
                        "clone_backend": "voxcpm2",
                        "ref_audio": "references/benny.wav",
                        "ref_text": "I wrote the date in the margin.",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        persona = self.root / "persona_projects" / "bernice"
        persona.mkdir(parents=True)
        (persona / "persona.json").write_text(
            json.dumps(
                {
                    "character_id": "character_bernice",
                    "description": "Adult woman, dry and incisive, restrained warmth.",
                    "ref_text": "I know what I saw.",
                    "status": "approved",
                }
            ),
            encoding="utf-8",
        )
        persona_refs = self.root / "persona_refs"
        persona_refs.mkdir()
        (persona_refs / "bernice_summerfield.json").write_text(
            json.dumps(
                {
                    "roster_entry_id": "character_bernice",
                    "name": "Bernice Summerfield",
                    "visual": {
                        "image_prompt_summary": "Bernice wears a red coat.",
                        "profile": {
                            "clothing": [
                                {
                                    "detail": "Bernice wears a red coat.",
                                    "certainty": 1.0,
                                    "observation_ids": ["visual_bernice_coat"],
                                }
                            ]
                        },
                        "variants": [],
                        "conflicts": [],
                        "unknowns": [],
                    },
                }
            ),
            encoding="utf-8",
        )
        visual = self.root / "persona_visual"
        visual.mkdir()
        (visual / "village.json").write_text(
            json.dumps(
                {
                    "character_id": "character_village",
                    "status": "incompatible",
                    "summary": "Old dossier needs refresh.",
                }
            ),
            encoding="utf-8",
        )
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_routes_are_registered_once(self) -> None:
        expected = {
            ("GET", "/api/cast"),
            ("GET", "/api/cast/characters/{character_id}"),
        }
        actual = []
        for route in app_module.app.routes:
            route_path = getattr(route, "path", None)
            methods = getattr(route, "methods", set())
            for method in methods:
                pair = (method, route_path)
                if pair in expected:
                    actual.append(pair)
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(expected))

    def test_cast_route_is_model_free_read_only_and_preserves_selection(self) -> None:
        protected = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        with (
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=AssertionError("Cast status must not download models"),
            ),
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError("Cast status must not load TTS"),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError("Cast status must not connect to an LLM"),
            ),
        ):
            response = self.client.get(
                "/api/cast",
                params={
                    "filter": "speaking_roles",
                    "search": "Bernice",
                    "selected_character_id": "character_village",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(
            [item["character_id"] for item in payload["characters"]],
            ["character_bernice"],
        )
        self.assertEqual(payload["selected_character_id"], "character_village")
        self.assertFalse(payload["selection_visible"])
        self.assertEqual(
            payload["selected_character"]["appearance"]["status"],
            "incompatible",
        )
        bernice = payload["characters"][0]
        self.assertEqual(
            bernice["voice"]["persistent_voice_description"],
            "Adult woman, dry and incisive, restrained warmth.",
        )
        self.assertEqual(bernice["appearance"]["status"], "complete")
        self.assertEqual(
            bernice["appearance"]["summary"],
            "Bernice wears a red coat.",
        )
        self.assertEqual(bernice["appearance"]["entry_id"], "character_bernice")
        self.assertEqual(len(bernice["appearance"]["stable_traits"]), 1)
        self.assertTrue(payload["summary"]["complete"])
        self.assertEqual(
            payload["technical_details"]["project_path"],
            str(self.root.resolve()),
        )
        ordinary = {
            key: value
            for key, value in payload.items()
            if key != "technical_details"
        }
        self.assertNotIn(str(self.root.resolve()), json.dumps(ordinary))
        self.assertEqual(
            {
                path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*")
                if path.is_file()
            },
            protected,
        )

    def test_cast_route_exposes_roster_progress_when_characters_are_not_ready(self) -> None:
        (self.root / "character_roster.json").unlink()
        roster_status = {
            "process": {"running": True, "logs": ["Discovering roster passage 3/42"]},
            "progress": {
                "status": "resumable",
                "completed_passages": 2,
                "total_passages": 42,
                "next_passage": 3,
            },
        }
        with (
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.root / "character_roster.json"),
            ),
            patch.object(
                app_module,
                "_current_character_roster_status",
                return_value=roster_status,
            ),
        ):
            response = self.client.get("/api/cast")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"]["state"], "running")
        self.assertTrue(payload["process"]["running"])
        self.assertEqual(payload["progress"]["completed_passages"], 2)
        self.assertEqual(payload["progress"]["total_passages"], 42)
        self.assertEqual(payload["progress"]["next_passage"], 3)

    def test_character_route_returns_exact_stable_character(self) -> None:
        with patch.object(app_module, "ROOT_DIR", str(self.root)):
            response = self.client.get(
                "/api/cast/characters/character_benny_narrator"
            )
        self.assertEqual(response.status_code, 200, response.text)
        character = response.json()
        self.assertEqual(character["character_id"], "character_benny_narrator")
        self.assertEqual(
            character["script_connection"]["resolved_script_voice_label"],
            "NARRATOR (BENNY)",
        )
        self.assertEqual(character["readiness_state"], "ready")

    def test_missing_character_has_machine_readable_404(self) -> None:
        with patch.object(app_module, "ROOT_DIR", str(self.root)):
            response = self.client.get(
                "/api/cast/characters/character_missing"
            )
        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "cast_character_not_found")
        self.assertEqual(
            detail["context"]["character_id"],
            "character_missing",
        )

    def test_malformed_authoritative_roster_is_invalid_not_empty(self) -> None:
        (self.root / "character_roster.json").write_text(
            "{not-json",
            encoding="utf-8",
        )
        with patch.object(app_module, "ROOT_DIR", str(self.root)):
            response = self.client.get("/api/cast")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "cast_artifact_invalid_json",
        )

    def test_invalid_optional_specialist_json_is_advisory(self) -> None:
        invalid = self.root / "persona_visual" / "invalid.json"
        invalid.write_text("{not-json", encoding="utf-8")
        with patch.object(app_module, "ROOT_DIR", str(self.root)):
            response = self.client.get("/api/cast")
        self.assertEqual(response.status_code, 200, response.text)
        compatibility = response.json()["compatibility"]
        self.assertEqual(compatibility["state"], "advisory")
        self.assertIn(
            "cast_auxiliary_invalid_json",
            {item["code"] for item in compatibility["warnings"]},
        )


if __name__ == "__main__":
    unittest.main()
