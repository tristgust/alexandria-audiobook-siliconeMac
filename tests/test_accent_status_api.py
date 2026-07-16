from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import app as app_module


class AccentStatusAPITests(
    unittest.TestCase
):
    @staticmethod
    def status(
        description,
        output_language=None,
    ):
        request = (
            app_module
            .AccentPipelineStatusRequest(
                description=description,
                output_language=output_language,
            )
        )

        return asyncio.run(
            app_module
            .voice_design_accent_status(
                request
            )
        )

    def test_french_accent_status(self):
        result = self.status(
            (
                "A mature woman with a "
                "restrained French accent."
            ),
            "English",
        )

        self.assertEqual(
            result["status"],
            "accent_pipeline",
        )
        self.assertTrue(
            result["accent_detected"]
        )
        self.assertEqual(
            result["accent_label"],
            "French",
        )
        self.assertEqual(
            result["native_language"],
            "French",
        )
        self.assertEqual(
            result["output_language"],
            "English",
        )
        self.assertEqual(
            result["sequence"],
            (
                "native_seed_design "
                "-> output_clone"
            ),
        )
        self.assertEqual(
            [
                stage["id"]
                for stage in result["stages"]
            ],
            [
                "native_seed_design",
                "output_clone",
            ],
        )

    def test_ordinary_design_status(self):
        result = self.status(
            "A warm British narrator.",
            "German",
        )

        self.assertEqual(
            result["status"],
            "ordinary_design",
        )
        self.assertFalse(
            result["accent_detected"]
        )
        self.assertIsNone(
            result["accent_label"]
        )
        self.assertIsNone(
            result["native_language"]
        )
        self.assertEqual(
            result["output_language"],
            "German",
        )
        self.assertEqual(
            result["sequence"],
            "ordinary_design",
        )

    def test_disabled_accent_is_ordinary_design(self):
        result = self.status(
            (
                "[accent: off] "
                "A French accent is mentioned "
                "only as something to avoid."
            ),
            "English",
        )

        self.assertFalse(
            result["accent_detected"]
        )
        self.assertEqual(
            result["status"],
            "ordinary_design",
        )

    def test_auto_output_language_resolves_to_english(self):
        result = self.status(
            "A strong German accent.",
            "Auto",
        )

        self.assertEqual(
            result["output_language"],
            "English",
        )
        self.assertEqual(
            result["stages"][1]["language"],
            "English",
        )

    def test_status_does_not_initialize_tts(self):
        with patch.object(
            app_module.project_manager,
            "get_engine",
            side_effect=AssertionError(
                "TTS engine must not initialize"
            ),
        ) as get_engine:
            result = self.status(
                "A subtle Russian accent.",
                "English",
            )

        self.assertTrue(
            result["accent_detected"]
        )
        get_engine.assert_not_called()

    def test_route_is_registered_once(self):
        paths = [
            route.path
            for route
            in app_module.app.routes
            if getattr(
                route,
                "path",
                None,
            ) == (
                "/api/voice_design/"
                "accent_status"
            )
        ]

        self.assertEqual(
            paths,
            [
                (
                    "/api/voice_design/"
                    "accent_status"
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
