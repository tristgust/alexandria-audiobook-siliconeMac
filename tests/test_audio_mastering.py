from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from audio_artifacts import sha256_file
from audio_mastering import (
    AudioMasteringCancelled,
    AudioMasteringError,
    build_mastering_plan,
    create_mastered_candidate,
    normalize_mastering_settings,
    normalize_provenance_evidence,
)


def write_fixture(path: Path, *, rate: int = 24000, gain: float = 0.3) -> None:
    timeline = np.arange(rate * 2, dtype=np.float32) / rate
    waveform = gain * (
        0.72 * np.sin(2 * np.pi * 180 * timeline)
        + 0.28 * np.sin(2 * np.pi * 1200 * timeline)
    )
    sf.write(path, waveform, rate, subtype="PCM_16")


def settings() -> dict:
    return {
        "schema_version": 1,
        "gain_db": 1.0,
        "high_pass_hz": 70,
        "low_pass_hz": 10000,
        "compression": {
            "enabled": True,
            "threshold_dbfs": -22,
            "ratio": 2,
            "attack_ms": 8,
            "release_ms": 120,
        },
        "normalization": {
            "enabled": True,
            "target_loudness_dbfs": -20,
            "maximum_gain_db": 8,
        },
        "limiter_ceiling_dbfs": -1,
    }


class AudioMasteringTests(unittest.TestCase):
    def test_settings_are_bounded_and_reject_novelty_or_unapproved_room_profile(self) -> None:
        normalized = normalize_mastering_settings(settings())
        self.assertEqual(normalized["high_pass_hz"], 70)
        self.assertRegex(normalized["settings_fingerprint"], r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(AudioMasteringError, "object"):
            normalize_mastering_settings(["reverb"])
        with self.assertRaisesRegex(AudioMasteringError, "explicit approval"):
            normalize_mastering_settings(
                {**settings(), "room_correction": {"profile_id": "studio-a"}}
            )
        with self.assertRaisesRegex(AudioMasteringError, "between 3000 and 22000"):
            normalize_mastering_settings(
                {**settings(), "high_pass_hz": 500, "low_pass_hz": 700}
            )
        with self.assertRaisesRegex(AudioMasteringError, "unsupported or novelty"):
            normalize_mastering_settings({**settings(), "dramatic_reverb": 0.5})
        with self.assertRaisesRegex(AudioMasteringError, "must be boolean"):
            normalize_mastering_settings(
                {
                    **settings(),
                    "compression": {
                        **settings()["compression"],
                        "enabled": "false",
                    },
                }
            )

    def test_structural_provenance_never_implies_trust_or_approval(self) -> None:
        value = normalize_provenance_evidence(
            {
                "c2pa": {
                    "present": True,
                    "structural_status": "valid",
                    "signer_trust": "unverified",
                },
                "watermark": {
                    "present": True,
                    "structural_status": "detected",
                    "ownership_trust": "unverified",
                },
            }
        )
        self.assertEqual(value["c2pa"]["structural_status"], "valid")
        self.assertEqual(value["c2pa"]["signer_trust"], "unverified")
        self.assertEqual(value["voice_authorization"], "not_evaluated")
        self.assertEqual(value["human_approval"], "pending_final_listen")
        self.assertEqual(normalize_provenance_evidence(value), value)
        with self.assertRaisesRegex(AudioMasteringError, "does not establish"):
            normalize_provenance_evidence(
                {
                    "c2pa": {
                        "present": True,
                        "structural_status": "valid",
                        "signer_trust": "trusted",
                    }
                }
            )
        with self.assertRaisesRegex(AudioMasteringError, "Voice authorization"):
            normalize_provenance_evidence(
                {**value, "voice_authorization": "authorized"}
            )
        with self.assertRaisesRegex(AudioMasteringError, "must be an object"):
            normalize_provenance_evidence(["c2pa"])

    def test_plan_binds_exact_take_registry_order_settings_and_provenance(self) -> None:
        take = {
            "take_id": "take_fixture",
            "record_fingerprint": "a" * 64,
            "artifact": {"sha256": "b" * 64, "sample_rate": 24000},
        }
        first = build_mastering_plan(
            take=take,
            registry_fingerprint="c" * 64,
            source_order_fingerprint="d" * 64,
            settings=settings(),
        )
        second = build_mastering_plan(
            take=take,
            registry_fingerprint="c" * 64,
            source_order_fingerprint="d" * 64,
            settings=settings(),
        )
        self.assertEqual(first, second)
        self.assertTrue(first["safe_to_execute"])
        self.assertIn("pitch_shift", first["rejected_effects"])
        changed = build_mastering_plan(
            take=take,
            registry_fingerprint="e" * 64,
            source_order_fingerprint="d" * 64,
            settings=settings(),
        )
        self.assertNotEqual(
            first["dependency_fingerprint"],
            changed["dependency_fingerprint"],
        )

    def test_processing_is_deterministic_preserves_duration_and_enforces_peak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            first = root / "first.wav"
            second = root / "second.wav"
            write_fixture(source)
            source_bytes = source.read_bytes()
            receipt_a = create_mastered_candidate(
                source_audio_path=source,
                output_path=first,
                settings=settings(),
            )
            receipt_b = create_mastered_candidate(
                source_audio_path=source,
                output_path=second,
                settings=settings(),
            )
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(sha256_file(first), sha256_file(second))
            self.assertEqual(
                receipt_a["processing_fingerprint"],
                receipt_b["processing_fingerprint"],
            )
            self.assertEqual(receipt_a["operation"], "publication_mastering")
            self.assertTrue(receipt_a["safeguards"]["duration_preserved"])
            self.assertTrue(receipt_a["safeguards"]["no_clipped_samples"])
            self.assertTrue(receipt_a["safeguards"]["peak_ceiling_passed"])
            self.assertLessEqual(
                receipt_a["metrics_after"]["estimated_true_peak_dbfs"],
                -0.85,
            )

    def test_processing_supports_approved_room_correction_and_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            output = root / "output.wav"
            write_fixture(source)
            configured = {
                **settings(),
                "room_correction": {
                    "approved": True,
                    "profile_id": "room-a-2026",
                    "gain_db": -1,
                    "high_pass_hz": 80,
                    "low_pass_hz": 9000,
                },
            }
            receipt = create_mastered_candidate(
                source_audio_path=source,
                output_path=output,
                settings=configured,
            )
            self.assertEqual(
                receipt["settings"]["room_correction"]["profile_id"],
                "room-a-2026",
            )
            cancelled_output = root / "cancelled.wav"
            with self.assertRaises(AudioMasteringCancelled):
                create_mastered_candidate(
                    source_audio_path=source,
                    output_path=cancelled_output,
                    settings=settings(),
                    cancel_check=lambda: True,
                )
            self.assertFalse(cancelled_output.exists())

    def test_late_cancellation_removes_encoded_candidate_and_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            output = root / "late-cancel.wav"
            write_fixture(source)
            calls = {"count": 0}

            def cancel_after_write() -> bool:
                calls["count"] += 1
                return calls["count"] >= 7

            with self.assertRaises(AudioMasteringCancelled):
                create_mastered_candidate(
                    source_audio_path=source,
                    output_path=output,
                    settings=settings(),
                    cancel_check=cancel_after_write,
                )
            self.assertFalse(output.exists())
            self.assertEqual(
                [item.name for item in root.iterdir() if item.name != "source.wav"],
                [],
            )

    def test_plan_rejects_filter_above_source_nyquist(self) -> None:
        take = {
            "take_id": "take_fixture",
            "record_fingerprint": "a" * 64,
            "artifact": {
                "sha256": "b" * 64,
                "sample_rate": 24000,
            },
        }
        with self.assertRaisesRegex(AudioMasteringError, "Nyquist"):
            build_mastering_plan(
                take=take,
                registry_fingerprint="c" * 64,
                source_order_fingerprint="d" * 64,
                settings={**settings(), "low_pass_hz": 16000},
            )

    def test_processing_refuses_to_overwrite_source_take(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.wav"
            write_fixture(source)
            before = source.read_bytes()
            with self.assertRaisesRegex(AudioMasteringError, "immutable source"):
                create_mastered_candidate(
                    source_audio_path=source,
                    output_path=source,
                    settings=settings(),
                )
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
