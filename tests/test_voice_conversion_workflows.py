from __future__ import annotations

from contextlib import AbstractContextManager
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import prepare_doctor_seedvc_anchor_bank as doctor_anchors
import prepare_doctor_character_identity_bank as doctor_character_bank
import prepare_doctor_calm_donor_rescue as doctor_calm_donor
import prepare_doctor_same_speaker_salvage as doctor_same_speaker
import prepare_expanded_same_speaker_round as expanded_same_speaker
import prepare_new_doctor_upload_matches as new_doctor_uploads
import prepare_same_speaker_performance_validation as same_speaker
import prepare_three_voice_openvoice_conversion as openvoice_workflow
import prepare_three_voice_seedvc_conversion as seedvc_workflow
import package_narrator_benny_seedvc_review as final_package
import package_same_speaker_final_review as same_speaker_final
import package_three_voice_seedvc_final_review as three_voice_final


class DummyContext(AbstractContextManager):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class VoiceConversionWorkflowTests(unittest.TestCase):
    def test_mps_autocast_uses_noop_context(self) -> None:
        calls = []

        class FakeTorch:
            @staticmethod
            def autocast(device_type, *args, **kwargs):
                calls.append((device_type, args, kwargs))
                return DummyContext()

        original, factory = seedvc_workflow.safe_autocast(FakeTorch)
        self.assertIs(original, FakeTorch.autocast)
        with factory("mps"):
            pass
        self.assertEqual(calls, [])
        with factory("cpu", dtype="float32"):
            pass
        self.assertEqual(calls[0][0], "cpu")

    def test_cfm_compatibility_converts_scalar_to_speaker_guidance(self) -> None:
        captured = {}

        class FakeCFM:
            def inference(self, mu, x_lens, prompt, style, **kwargs):
                captured.update(kwargs)
                return "generated"

        class FakeWrapper:
            cfm = FakeCFM()

        wrapper = FakeWrapper()
        seedvc_workflow.install_cfm_compatibility(wrapper)
        result = wrapper.cfm.inference(
            "mu",
            "lengths",
            "prompt",
            "style",
            n_timesteps=20,
            temperature=1.0,
            inference_cfg_rate=0.7,
            sway_sampling=True,
            amo_sampling=True,
        )
        self.assertEqual(result, "generated")
        self.assertEqual(captured["n_timesteps"], 20)
        self.assertEqual(captured["inference_cfg_rate"], [0.0, 0.7])
        self.assertNotIn("sway_sampling", captured)
        self.assertNotIn("amo_sampling", captured)

    def test_doctor_anchor_boundaries_are_ordered_and_homogeneous(self) -> None:
        labels = [item["label"] for item in doctor_anchors.ANCHORS]
        self.assertEqual(
            labels,
            ["canonical_calm", "dry_sarcastic", "irritated", "threatening"],
        )
        for item in doctor_anchors.ANCHORS:
            self.assertGreater(item["end_seconds"], item["start_seconds"])
            self.assertGreater(len(item["transcript"].split()), 3)
        for left, right in zip(doctor_anchors.ANCHORS, doctor_anchors.ANCHORS[1:]):
            self.assertLessEqual(left["end_seconds"], right["start_seconds"])

    def test_pitch_shape_similarity_rewards_preserved_contour(self) -> None:
        donor = {"pitch_thirds_hz": [100.0, 125.0, 90.0]}
        preserved = {"pitch_thirds_hz": [200.0, 250.0, 180.0]}
        flattened = {"pitch_thirds_hz": [200.0, 200.0, 200.0]}
        self.assertGreater(
            openvoice_workflow.pitch_shape_similarity(preserved, donor),
            openvoice_workflow.pitch_shape_similarity(flattened, donor),
        )

    def test_doctor_character_bank_uses_only_in_character_clips(self) -> None:
        names = {
            name
            for clips in doctor_character_bank.BANKS.values()
            for name in clips
        }
        self.assertTrue(names)
        indices = [int(name.removeprefix("sample_").removesuffix(".wav")) for name in names]
        self.assertGreaterEqual(min(indices), 197)
        self.assertLessEqual(max(indices), 213)
        self.assertNotIn("sample_0000.wav", names)
        self.assertIn("sample_0208.wav", names)

    def test_short_calm_donor_uses_complete_sentence(self) -> None:
        self.assertEqual(
            doctor_calm_donor.TEXTS["calm"],
            "Breathe slowly. You are safe here.",
        )
        self.assertNotIn("not going anywhere", doctor_calm_donor.TEXTS["calm"])

    def test_final_route_contract_excludes_only_doctor_calm(self) -> None:
        self.assertEqual(len(three_voice_final.EXPECTED_ROUTES), 8)
        self.assertNotIn(("doctor", "calm"), three_voice_final.EXPECTED_ROUTES)
        self.assertIn(("doctor", "pleading"), three_voice_final.EXPECTED_ROUTES)
        self.assertIn(("doctor", "angry"), three_voice_final.EXPECTED_ROUTES)
        for target in ("narrator", "benny"):
            for mode in ("calm", "pleading", "angry"):
                self.assertIn((target, mode), three_voice_final.EXPECTED_ROUTES)

    def test_same_speaker_sources_never_cross_characters(self) -> None:
        expected_source_kinds = {
            "narrator": {"narrator_context"},
            "benny": {"benny_download"},
            "doctor": {"doctor_clip"},
        }
        for spec in same_speaker.SPECS:
            self.assertIn(spec["source_kind"], expected_source_kinds[spec["target_key"]])
            if "speaker_source_kind" in spec:
                self.assertEqual(spec["target_key"], "doctor")
                self.assertEqual(spec["speaker_source_kind"], "doctor_bank")

    def test_same_speaker_final_route_contract(self) -> None:
        self.assertEqual(
            same_speaker_final.EXPECTED_ROUTES,
            (
                ("narrator", "panic"),
                ("narrator", "smug_menace"),
                ("benny", "emergency_distress"),
                ("benny", "excited_discovery"),
                ("doctor", "protective_authority"),
                ("doctor", "dark_warning"),
            ),
        )

    def test_doctor_same_speaker_salvage_uses_in_character_clips(self) -> None:
        by_mode = {spec["mode"]: spec for spec in doctor_same_speaker.SPECS}
        self.assertEqual(
            by_mode["protective_authority"]["source_clips"],
            ("sample_0208.wav",),
        )
        self.assertEqual(
            by_mode["dark_warning"]["source_clips"],
            ("sample_0204.wav", "sample_0205.wav", "sample_0206.wav", "sample_0207.wav"),
        )

    def test_same_speaker_vocoder_scaling_precedes_file_save(self) -> None:
        source = (BENCHMARKS / "prepare_same_speaker_performance_validation.py").read_text(encoding="utf-8")
        scale_position = source.index("return original_bigvgan(*positional, **keywords) * 0.70")
        inference_position = source.index("returned = model.infer(")
        self.assertLess(scale_position, inference_position)

    def test_expanded_round_is_balanced_across_three_characters(self) -> None:
        counts = {
            target: sum(spec["target_key"] == target for spec in expanded_same_speaker.SPECS)
            for target in expanded_same_speaker.TARGET_ORDER
        }
        self.assertEqual(counts, {"narrator": 3, "benny": 3, "doctor": 3})
        sample_count = sum(
            len(spec["speaker_strategies"]) * len(spec["alphas"])
            for spec in expanded_same_speaker.SPECS
        )
        self.assertEqual(sample_count, 18)

    def test_new_doctor_uploads_separate_character_from_interview_speech(self) -> None:
        kinds = {row["upload_name"]: row["provisional_kind"] for row in new_doctor_uploads.MATCHES}
        self.assertEqual(kinds["dw7voice2.mp3"], "in_character")
        self.assertEqual(kinds["dw7voice3.mp3"], "actor_interview")
        self.assertEqual(kinds["dw7voice4.mp3"], "actor_interview")
        starts = {row["upload_name"]: row["source_start_seconds"] for row in new_doctor_uploads.MATCHES}
        self.assertAlmostEqual(starts["dw7voice2.mp3"], 1336.0)
        self.assertAlmostEqual(starts["dw7voice3.mp3"], 1034.2)
        self.assertAlmostEqual(starts["dw7voice4.mp3"], 0.0)

    def test_interview_audio_is_identity_only_not_performance_reference(self) -> None:
        doctor_specs = [spec for spec in expanded_same_speaker.SPECS if spec["target_key"] == "doctor"]
        self.assertEqual(len(doctor_specs), 3)
        self.assertEqual(
            {spec["source_name"] for spec in doctor_specs},
            {"dw7voice2.wav", "doctor_dry_irritated.wav", "sample_0208.wav"},
        )
        self.assertNotIn("dw7voice3.wav", {spec["source_name"] for spec in doctor_specs})
        self.assertNotIn("dw7voice4.wav", {spec["source_name"] for spec in doctor_specs})

    def test_expanded_doctor_gate_does_not_hard_reject_interview_divergence(self) -> None:
        row = {
            "target_key": "doctor",
            "canonical_identity_cosine": 0.70,
            "style_reference_cosine": 0.72,
            "doctor_actor_identity_cosine": 0.10,
            "acoustic_match": 0.70,
            "acoustic_metrics": {
                "pitch_trajectory_anomaly": False,
                "clipping_fraction": 0.0,
            },
        }
        self.assertTrue(expanded_same_speaker.technical_pass(row, 1.0, True))

    def test_expanded_review_uses_cleanliness_not_inverse_artifact_scale(self) -> None:
        html = (BENCHMARKS / "expanded_same_speaker_assets" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Audio cleanliness · 5 best", html)
        self.assertIn("Every rating uses <strong>5 as best</strong>", html)
        self.assertNotIn("Artifact severity", html)

    def test_expanded_vocoder_scaling_precedes_generation(self) -> None:
        source = (BENCHMARKS / "prepare_expanded_same_speaker_round.py").read_text(encoding="utf-8")
        scale_position = source.index("return original_bigvgan(*positional, **keywords) * 0.70")
        inference_position = source.index("returned = model.infer(")
        self.assertLess(scale_position, inference_position)

    def test_doctor_diagnosis_never_promotes_failed_identity(self) -> None:
        samples = []
        for mode in ("calm", "pleading", "angry"):
            for anchor, whole, minimum in (
                ("canonical_calm", 0.76, 0.64),
                ("threatening", 0.61, 0.45),
            ):
                samples.append(
                    {
                        "mode": mode,
                        "target_anchor": anchor,
                        "whole_identity_cosine": whole,
                        "minimum_third_identity_cosine": minimum,
                        "third_identity_cosines": [minimum, whole, whole - 0.02],
                        "pitch_shape_similarity_to_donor": 0.95,
                        "text_similarity": 1.0,
                        "technical_pass": False,
                    }
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "analysis.json").write_text(
                json.dumps({"samples": samples}),
                encoding="utf-8",
            )
            diagnosis = final_package.doctor_diagnosis(root)
        self.assertEqual(diagnosis["status"], "identity_anchor_insufficient")
        self.assertFalse(diagnosis["production_route_approved"])
        self.assertEqual(diagnosis["technical_pass_count"], 0)
        self.assertEqual(diagnosis["tested_register_anchor_count"], 2)


if __name__ == "__main__":
    unittest.main()
