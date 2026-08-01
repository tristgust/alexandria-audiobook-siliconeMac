from __future__ import annotations

import unittest

import numpy as np

from synthesis_windows import (
    SynthesisWindowError,
    assemble_synthesis_segments,
    plan_synthesis_segments,
    resolve_synthesis_backend_id,
    synthesis_window,
    synthesis_window_catalog,
)


def tone(samples: int, *, frequency: float = 4.0, sample_rate: int = 1000) -> np.ndarray:
    timeline = np.arange(samples, dtype=np.float32) / sample_rate
    return 0.1 * np.sin(2.0 * np.pi * frequency * timeline)


class SynthesisWindowTests(unittest.TestCase):
    def test_catalog_declares_qwen_voxcpm_and_generic_families(self) -> None:
        catalog = synthesis_window_catalog()
        self.assertEqual(catalog["qwen3_custom"]["family"], "qwen3")
        self.assertEqual(catalog["voxcpm2_controlled"]["family"], "voxcpm2")
        self.assertEqual(catalog["external_generic"]["family"], "external")
        self.assertEqual(catalog["qwen3_custom"]["seam_mode"], "silence_gap")
        self.assertEqual(
            catalog["voxcpm2_controlled"]["seam_mode"],
            "discard_overlap",
        )
        self.assertEqual(
            synthesis_window("unknown-provider")["fallback_declaration"],
            True,
        )

    def test_backend_resolution_is_stable_for_production_voice_types(self) -> None:
        cases = (
            ({"type": "custom"}, "local", True, "qwen3_custom"),
            ({"type": "clone", "clone_backend": "qwen3_base"}, "local", True, "qwen3_base"),
            (
                {"type": "clone", "clone_backend": "qwen3_instruction_controlled"},
                "local",
                True,
                "qwen3_instruction_controlled",
            ),
            (
                {"type": "clone", "clone_backend": "voxcpm2_controlled"},
                "local",
                True,
                "voxcpm2_controlled",
            ),
            ({"type": "lora"}, "local", True, "qwen3_lora"),
            ({"type": "design"}, "local", True, "qwen3_voice_design"),
            ({"type": "community_qvoice"}, "local", True, "community_qwen"),
            ({"type": "custom"}, "external", False, "external_generic"),
        )
        for voice, mode, mlx, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    resolve_synthesis_backend_id(voice, mode=mode, use_mlx=mlx),
                    expected,
                )

    def test_zero_short_and_aligned_requests_have_exact_spans(self) -> None:
        empty = plan_synthesis_segments("", backend_id="qwen3_custom")
        self.assertEqual(empty["segments"], [])

        short = plan_synthesis_segments("Short line.", backend_id="qwen3_custom")
        self.assertEqual(len(short["segments"]), 1)
        self.assertEqual(short["segments"][0]["source_start"], 0)
        self.assertEqual(short["segments"][0]["source_end"], 11)

        aligned_text = "x" * 96
        aligned = plan_synthesis_segments(aligned_text, backend_id="qwen3_custom")
        self.assertEqual(len(aligned["segments"]), 1)
        self.assertEqual(aligned["segments"][0]["source_text"], aligned_text)

    def test_long_text_preserves_every_character_punctuation_and_paragraph(self) -> None:
        text = (
            "First sentence, with a clause; and an em dash — still here.\n\n"
            "Second paragraph keeps  double spaces, punctuation, and its exact ending! "
            + "x" * 103
        )
        plan = plan_synthesis_segments(text, backend_id="qwen3_custom")
        self.assertGreater(len(plan["segments"]), 2)
        self.assertEqual(
            "".join(segment["source_text"] for segment in plan["segments"]),
            text,
        )
        self.assertEqual(plan["segments"][0]["source_start"], 0)
        self.assertEqual(plan["segments"][-1]["source_end"], len(text))
        for previous, current in zip(plan["segments"], plan["segments"][1:]):
            self.assertEqual(previous["source_end"], current["source_start"])
        self.assertTrue(
            all(
                segment["dependency_fingerprint"]
                == plan["dependency_fingerprint"]
                for segment in plan["segments"]
            )
        )

    def test_qwen_clone_respects_word_window_without_source_loss(self) -> None:
        text = " ".join(f"word{index}" for index in range(31))
        plan = plan_synthesis_segments(text, backend_id="qwen3_base")
        self.assertGreater(len(plan["segments"]), 1)
        self.assertEqual(
            "".join(segment["source_text"] for segment in plan["segments"]),
            text,
        )
        self.assertTrue(
            all(len(segment["generation_text"].split()) <= 14 for segment in plan["segments"])
        )

    def test_crossfade_receipt_has_exact_length_and_no_unreported_loss(self) -> None:
        plan = plan_synthesis_segments(
            "Alpha beta gamma. Delta epsilon zeta.",
            backend_id="qwen3_instruction_controlled",
            max_chars=20,
        )
        self.assertEqual(len(plan["segments"]), 2)
        results = [
            {
                "segment_id": segment["segment_id"],
                "audio": tone(1000 + index * 200),
                "sample_rate": 1000,
            }
            for index, segment in enumerate(plan["segments"])
        ]
        joined, rate, receipt = assemble_synthesis_segments(plan, results)
        self.assertEqual(rate, 1000)
        self.assertEqual(receipt["seams"][0]["mode"], "crossfade")
        self.assertEqual(receipt["seams"][0]["applied_samples"], 12)
        self.assertEqual(receipt["pre_edge_expected_sample_count"], 2188)
        self.assertEqual(receipt["pre_edge_actual_sample_count"], 2188)
        self.assertEqual(receipt["exact_length_restoration"], "none")
        self.assertEqual(receipt["final_sample_count"], len(joined))

    def test_voxcpm_discard_policy_is_explicit_and_exact(self) -> None:
        plan = plan_synthesis_segments(
            "Alpha beta gamma. Delta epsilon zeta.",
            backend_id="voxcpm2_controlled",
            max_chars=20,
        )
        results = [
            {
                "segment_id": segment["segment_id"],
                "audio": tone(1000),
                "sample_rate": 1000,
            }
            for segment in plan["segments"]
        ]
        joined, _rate, receipt = assemble_synthesis_segments(plan, results)
        self.assertEqual(receipt["seams"][0]["mode"], "discard_overlap")
        self.assertEqual(receipt["seams"][0]["applied_samples"], 20)
        self.assertEqual(receipt["pre_edge_expected_sample_count"], 1980)
        self.assertLessEqual(len(joined), 1980)

    def test_silence_gap_policy_preserves_all_segment_samples_plus_declared_gap(self) -> None:
        plan = plan_synthesis_segments(
            "Alpha beta gamma. Delta epsilon zeta.",
            backend_id="qwen3_custom",
            max_chars=20,
        )
        results = [
            {
                "segment_id": segment["segment_id"],
                "audio": tone(1000),
                "sample_rate": 1000,
            }
            for segment in plan["segments"]
        ]
        _joined, _rate, receipt = assemble_synthesis_segments(plan, results)
        self.assertEqual(receipt["seams"][0]["mode"], "silence_gap")
        self.assertEqual(receipt["seams"][0]["applied_samples"], 100)
        self.assertEqual(receipt["pre_edge_expected_sample_count"], 2100)

    def test_missing_duplicate_unexpected_and_incompatible_results_fail_closed(self) -> None:
        plan = plan_synthesis_segments(
            "Alpha beta gamma. Delta epsilon zeta.",
            backend_id="qwen3_custom",
            max_chars=20,
        )
        first, second = plan["segments"]
        valid = {
            "segment_id": first["segment_id"],
            "audio": tone(1000),
            "sample_rate": 1000,
        }
        with self.assertRaisesRegex(SynthesisWindowError, "exact planned segment set"):
            assemble_synthesis_segments(plan, [valid])
        with self.assertRaisesRegex(SynthesisWindowError, "duplicate"):
            assemble_synthesis_segments(plan, [valid, valid])
        with self.assertRaisesRegex(SynthesisWindowError, "exact planned segment set"):
            assemble_synthesis_segments(
                plan,
                [
                    valid,
                    {
                        "segment_id": "unexpected",
                        "audio": tone(1000),
                        "sample_rate": 1000,
                    },
                ],
            )
        with self.assertRaisesRegex(SynthesisWindowError, "different sample rates"):
            assemble_synthesis_segments(
                plan,
                [
                    valid,
                    {
                        "segment_id": second["segment_id"],
                        "audio": tone(1000, sample_rate=1200),
                        "sample_rate": 1200,
                    },
                ],
            )

    def test_silent_or_survivor_only_segment_cannot_produce_joined_audio(self) -> None:
        plan = plan_synthesis_segments(
            "Alpha beta gamma. Delta epsilon zeta.",
            backend_id="external_generic",
            max_chars=20,
        )
        first, second = plan["segments"]
        with self.assertRaisesRegex(SynthesisWindowError, "effectively silent"):
            assemble_synthesis_segments(
                plan,
                [
                    {
                        "segment_id": first["segment_id"],
                        "audio": tone(1000),
                        "sample_rate": 1000,
                    },
                    {
                        "segment_id": second["segment_id"],
                        "audio": np.zeros(1000, dtype=np.float32),
                        "sample_rate": 1000,
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
