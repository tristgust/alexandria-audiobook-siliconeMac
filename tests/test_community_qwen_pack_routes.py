from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from tests.test_qwen_voice_packs import qvoice_bytes


class CommunityQwenPackRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_routes_are_registered_with_bounded_methods(self) -> None:
        methods = {
            route.path: route.methods
            for route in app_module.app.routes
            if getattr(route, "path", "").startswith("/api/community-qwen-packs")
        }
        self.assertEqual(methods["/api/community-qwen-packs"], {"GET"})
        self.assertEqual(methods["/api/community-qwen-packs/inspect"], {"POST"})
        self.assertEqual(methods["/api/community-qwen-packs/import"], {"POST"})
        self.assertEqual(
            methods["/api/community-qwen-packs/{pack_id}/approve"],
            {"POST"},
        )
        self.assertEqual(
            methods["/api/community-qwen-packs/{pack_id}"],
            {"DELETE"},
        )

    def test_inspect_accepts_a_qvoice_upload_without_installing(self) -> None:
        expected = {
            "family": "qvoice_graft",
            "state": "ready_for_review",
            "license_name": None,
        }
        with patch.object(
            app_module,
            "inspect_qvoice_upload",
            return_value=expected,
        ) as inspector:
            response = self.client.post(
                "/api/community-qwen-packs/inspect",
                files={"file": ("reader.qvoice", qvoice_bytes(), "application/octet-stream")},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        temporary = inspector.call_args.kwargs["source_path"]
        self.assertFalse(temporary.exists())

    def test_inspect_reports_icl_as_not_runnable(self) -> None:
        response = self.client.post(
            "/api/community-qwen-packs/inspect",
            files={"file": ("icl.qvoice", qvoice_bytes(), "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "mlx_conversion_required")
        self.assertEqual(response.json()["prompt_mode"], "icl")

    def test_import_and_approval_use_the_reusable_library_root(self) -> None:
        imported = {"pack_id": "qvoice_abc", "state": "review_required"}
        approved = {"pack_id": "qvoice_abc", "state": "approved"}
        with patch.object(
            app_module,
            "install_qvoice_pack",
            return_value=imported,
        ) as installer:
            response = self.client.post(
                "/api/community-qwen-packs/import",
                files={"file": ("reader.qvoice", qvoice_bytes(), "application/octet-stream")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), imported)
        self.assertEqual(
            installer.call_args.kwargs["reusable_root"],
            app_module.LEGACY_ROOT_DIR,
        )

        with patch.object(
            app_module,
            "approve_qvoice_pack",
            return_value=approved,
        ) as approver:
            response = self.client.post(
                "/api/community-qwen-packs/qvoice_abc/approve",
                json={"expected_preview_fingerprint": "f" * 64},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), approved)
        approver.assert_called_once_with(
            pack_id="qvoice_abc",
            expected_preview_fingerprint="f" * 64,
            reusable_root=app_module.LEGACY_ROOT_DIR,
        )

    def test_preview_generation_records_the_exact_audition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / "voice.qvoice"
            pack.write_bytes(qvoice_bytes())
            calls = []

            class Backend:
                def generate_community_qvoice(self, **kwargs):
                    calls.append(dict(kwargs))
                    Path(kwargs["output_path"]).write_bytes(b"RIFF-preview")
                    return True

            class Engine:
                def _init_mlx(self):
                    return Backend()

            reviewed = {
                "pack_id": "qvoice_abc",
                "state": "review_required",
                "preview_fingerprint": "e" * 64,
            }
            with (
                patch.object(
                    app_module,
                    "resolve_qvoice_pack",
                    return_value=(
                        {"pack_id": "qvoice_abc", "sha256": "a" * 64},
                        pack,
                    ),
                ),
                patch.object(
                    app_module.project_manager,
                    "get_engine",
                    return_value=Engine(),
                ),
                patch.object(
                    app_module,
                    "record_qvoice_preview",
                    return_value=reviewed,
                ) as recorder,
            ):
                response = self.client.post(
                    "/api/community-qwen-packs/qvoice_abc/preview",
                    json={
                        "text": "A bounded audition line.",
                        "persistent_description": "An older English storyteller.",
                        "direction": "Warm, amused, and conversational.",
                        "generation_seed": 104729,
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["audio_url"], (
            "/api/community-qwen-packs/qvoice_abc/preview"
        ))
        self.assertEqual(calls[0]["seed"], 104729)
        self.assertTrue(calls[0]["review_mode"])
        self.assertIn("older English storyteller", calls[0]["instruct"])
        self.assertIn("Warm, amused", calls[0]["instruct"])
        recorder.assert_called_once()


if __name__ == "__main__":
    unittest.main()
