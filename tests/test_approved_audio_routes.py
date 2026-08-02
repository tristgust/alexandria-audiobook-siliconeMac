from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from approved_audio_acceptance import ApprovedAudioAcceptanceError
from approved_audio_promotion import ApprovedAudioPromotionError


class ApprovedAudioRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_promotion_route_forwards_explicit_controls(self) -> None:
        with patch.object(
            app_module,
            "promote_approved_adaptation_audio",
            return_value={"status": "promoted", "installed_chunk_count": 84},
        ) as promote:
            response = self.client.post(
                "/api/approved-audio/promote",
                json={
                    "manifest_path": "/tmp/complete-manifest.json",
                    "confirm_installation": True,
                    "include_restricted": True,
                    "promote_voice_evidence": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["installed_chunk_count"], 84)
        promote.assert_called_once_with(
            project_root=app_module.ROOT_DIR,
            manifest_path="/tmp/complete-manifest.json",
            confirm_installation=True,
            include_restricted=True,
            promote_voice_evidence=True,
        )

    def test_promotion_error_is_structured(self) -> None:
        with patch.object(
            app_module,
            "promote_approved_adaptation_audio",
            side_effect=ApprovedAudioPromotionError(
                "approved_audio_project_changed",
                "Project changed after review.",
            ),
        ):
            response = self.client.post(
                "/api/approved-audio/promote",
                json={
                    "manifest_path": "/tmp/complete-manifest.json",
                    "confirm_installation": True,
                },
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "approved_audio_project_changed",
        )

    def test_acceptance_preview_route_requires_index_and_stable_key(self) -> None:
        expected = {"chunk_index": 0, "chunk_key": "chunk:3431"}
        with patch.object(
            app_module,
            "preview_approved_audio_acceptance",
            return_value=expected,
        ) as preview:
            response = self.client.post(
                "/api/approved-audio/acceptance/preview",
                json={"chunk_index": 0, "chunk_key": "chunk:3431"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        preview.assert_called_once_with(
            project_root=app_module.ROOT_DIR,
            chunks_lock=app_module.project_manager._chunks_lock,
            chunk_key_value="chunk:3431",
        )

    def test_acceptance_confirm_route_forwards_typed_preview_identity(self) -> None:
        fingerprint = "a" * 64
        with patch.object(
            app_module,
            "confirm_approved_audio_acceptance",
            return_value={"status": "accepted", "receipt_fingerprint": fingerprint},
        ) as confirm:
            response = self.client.post(
                "/api/approved-audio/acceptance/confirm",
                json={
                    "chunk_index": 0,
                    "chunk_key": "chunk:3431",
                    "action_fingerprint": fingerprint,
                    "chunks_fingerprint": "b" * 64,
                    "registry_fingerprint": "c" * 64,
                    "voice_configuration_fingerprint": "d" * 64,
                    "idempotency_key": "accept-3431",
                    "confirm_acceptance": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        confirm.assert_called_once_with(
            project_root=app_module.ROOT_DIR,
            chunks_lock=app_module.project_manager._chunks_lock,
            chunk_index_value=0,
            chunk_key_value="chunk:3431",
            action_fingerprint=fingerprint,
            chunks_fingerprint="b" * 64,
            registry_fingerprint="c" * 64,
            voice_configuration_fingerprint="d" * 64,
            idempotency_key="accept-3431",
            confirm_acceptance=True,
        )

    def test_acceptance_conflict_is_structured(self) -> None:
        with patch.object(
            app_module,
            "preview_approved_audio_acceptance",
            side_effect=ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_lock_changed",
                "The approved lock changed.",
                context={"chunk_key": "chunk:3431"},
            ),
        ):
            response = self.client.post(
                "/api/approved-audio/acceptance/preview",
                json={"chunk_index": 0, "chunk_key": "chunk:3431"},
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "approved_audio_acceptance_lock_changed",
                "message": "The approved lock changed.",
                "context": {"chunk_key": "chunk:3431"},
            },
        )

    def test_acceptance_confirm_conflict_is_structured(self) -> None:
        with patch.object(
            app_module,
            "confirm_approved_audio_acceptance",
            side_effect=ApprovedAudioAcceptanceError(
                "approved_audio_acceptance_voice_configuration_changed",
                "The Voice configuration changed after preview.",
                context={"chunk_key": "chunk:3431"},
            ),
        ):
            response = self.client.post(
                "/api/approved-audio/acceptance/confirm",
                json={
                    "chunk_index": 0,
                    "chunk_key": "chunk:3431",
                    "action_fingerprint": "a" * 64,
                    "chunks_fingerprint": "b" * 64,
                    "registry_fingerprint": "c" * 64,
                    "voice_configuration_fingerprint": "d" * 64,
                    "idempotency_key": "accept-3431",
                    "confirm_acceptance": True,
                },
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "approved_audio_acceptance_voice_configuration_changed",
                "message": "The Voice configuration changed after preview.",
                "context": {"chunk_key": "chunk:3431"},
            },
        )

    def test_rollback_route_forwards_receipt(self) -> None:
        with patch.object(
            app_module,
            "rollback_approved_adaptation_audio",
            return_value={"status": "rolled_back"},
        ) as rollback:
            response = self.client.post(
                "/api/approved-audio/rollback",
                json={
                    "receipt_path": "/tmp/receipt.json",
                    "confirm_rollback": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        rollback.assert_called_once_with(
            project_root=app_module.ROOT_DIR,
            receipt_path="/tmp/receipt.json",
            confirm_rollback=True,
        )


if __name__ == "__main__":
    unittest.main()
