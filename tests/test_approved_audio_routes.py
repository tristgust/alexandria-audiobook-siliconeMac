from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
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
