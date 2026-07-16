from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from accent_pipeline import (
    ACCENT_PIPELINES,
    build_native_seed_instruction,
    detect_accent_pipeline,
    normalize_output_language,
    register_accent_preview,
    resolve_accent_clone_reference,
    sha256_file,
    split_clone_segments,
)


class AccentDetectionTests(
    unittest.TestCase
):
    def test_supported_accent_descriptions(self):
        cases = {
            (
                "A restrained southern French "
                "accent with a low voice."
            ): "French",
            (
                "[accent: occitan] An older man."
            ): "French",
            (
                "A precise Castilian accent."
            ): "Spanish",
            (
                "A native German-speaking woman."
            ): "German",
            (
                "A warm Italian accent."
            ): "Italian",
            (
                "[accent: brazilian] Bright tenor."
            ): "Portuguese",
            (
                "A subtle Russian accent."
            ): "Russian",
        }

        for description, label in cases.items():
            with self.subTest(
                description=description
            ):
                pipeline = (
                    detect_accent_pipeline(
                        description
                    )
                )

                self.assertIsNotNone(
                    pipeline
                )
                self.assertEqual(
                    pipeline["label"],
                    label,
                )

    def test_explicit_disable_marker_wins(self):
        pipeline = detect_accent_pipeline(
            (
                "[accent: off] "
                "A French accent is mentioned "
                "only as something to avoid."
            )
        )

        self.assertIsNone(pipeline)

    def test_unknown_description_is_ordinary_design(self):
        self.assertIsNone(
            detect_accent_pipeline(
                "A warm, mature British narrator."
            )
        )

    def test_detected_pipeline_is_a_copy(self):
        first = detect_accent_pipeline(
            "A German accent."
        )
        second = detect_accent_pipeline(
            "A German accent."
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNot(
            first,
            second,
        )
        self.assertIsNot(
            first["patterns"],
            second["patterns"],
        )

        first["patterns"].append(
            "mutated"
        )

        self.assertNotIn(
            "mutated",
            second["patterns"],
        )

    def test_all_profiles_have_complete_seed_data(self):
        self.assertEqual(
            len(ACCENT_PIPELINES),
            6,
        )

        for profile in ACCENT_PIPELINES:
            with self.subTest(
                label=profile["label"]
            ):
                self.assertTrue(
                    profile["language"]
                )
                self.assertTrue(
                    profile["patterns"]
                )
                self.assertTrue(
                    profile["seed_text"]
                )
                self.assertTrue(
                    profile[
                        "native_instruction"
                    ]
                )


class AccentInstructionTests(
    unittest.TestCase
):
    def test_hidden_seed_instruction_preserves_character(self):
        pipeline = detect_accent_pipeline(
            "A restrained French accent."
        )
        instruction = (
            build_native_seed_instruction(
                (
                    "An older woman with a low, "
                    "weathered contralto."
                ),
                pipeline,
            )
        )

        self.assertIn(
            "older woman",
            instruction,
        )
        self.assertIn(
            pipeline[
                "native_instruction"
            ],
            instruction,
        )
        self.assertIn(
            "native language, not in English",
            instruction,
        )
        self.assertIn(
            "Preserve every requested character trait",
            instruction,
        )

    def test_output_language_defaults(self):
        self.assertEqual(
            normalize_output_language(
                None
            ),
            "English",
        )
        self.assertEqual(
            normalize_output_language(
                ""
            ),
            "English",
        )
        self.assertEqual(
            normalize_output_language(
                "Auto"
            ),
            "English",
        )
        self.assertEqual(
            normalize_output_language(
                "French"
            ),
            "French",
        )


class AccentRegistryTests(
    unittest.TestCase
):
    def test_registry_round_trip_recovers_native_seed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preview = (
                root
                / "designed_voices"
                / "previews"
                / "preview.wav"
            )
            seed = (
                root
                / "designed_voices"
                / "accent_seeds"
                / "french.wav"
            )
            preview.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            seed.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            preview.write_bytes(
                b"preview-audio"
            )
            seed.write_bytes(
                b"native-seed"
            )

            registry_path = (
                register_accent_preview(
                    root=root,
                    preview_audio_path=preview,
                    native_seed_audio=seed,
                    native_seed_text=(
                        "Texte français."
                    ),
                    native_language="French",
                    preview_text=(
                        "English preview."
                    ),
                )
            )

            self.assertEqual(
                registry_path.name,
                f"{sha256_file(preview)}.json",
            )

            record = json.loads(
                registry_path.read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                record["marker"],
                "accent preview registry",
            )
            self.assertFalse(
                Path(
                    record[
                        "native_seed_audio"
                    ]
                ).is_absolute()
            )

            (
                resolved_audio,
                resolved_text,
                meta,
            ) = resolve_accent_clone_reference(
                root=root,
                ref_audio=preview,
                ref_text="English preview.",
            )

            self.assertEqual(
                resolved_audio,
                str(seed),
            )
            self.assertEqual(
                resolved_text,
                "Texte français.",
            )
            self.assertEqual(
                meta["native_language"],
                "French",
            )

    def test_missing_native_seed_falls_back(self):
        warnings = []

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preview = root / "preview.wav"
            seed = root / "missing.wav"
            preview.write_bytes(
                b"preview"
            )

            register_accent_preview(
                root=root,
                preview_audio_path=preview,
                native_seed_audio=seed,
                native_seed_text="Native.",
                native_language="French",
                preview_text="English.",
            )

            result = (
                resolve_accent_clone_reference(
                    root=root,
                    ref_audio=preview,
                    ref_text="English.",
                    warning=warnings.append,
                )
            )

            self.assertEqual(
                result,
                (
                    str(preview),
                    "English.",
                    None,
                ),
            )
            self.assertEqual(
                len(warnings),
                1,
            )
            self.assertIn(
                "native seed missing",
                warnings[0],
            )


class AccentCloneSegmentationTests(
    unittest.TestCase
):
    def test_empty_text_has_no_segments(self):
        self.assertEqual(
            split_clone_segments(""),
            [],
        )

    def test_natural_boundaries_are_preferred(self):
        segments = split_clone_segments(
            (
                "First sentence. Second clause, "
                "then the final words."
            ),
            max_words=5,
        )

        self.assertEqual(
            segments,
            [
                "First sentence.",
                "Second clause,",
                "then the final words.",
            ],
        )

    def test_long_text_is_word_bounded(self):
        segments = split_clone_segments(
            "one two three four five six seven",
            max_words=3,
        )

        self.assertEqual(
            segments,
            [
                "one two three",
                "four five six seven",
            ],
        )


if __name__ == "__main__":
    unittest.main()
