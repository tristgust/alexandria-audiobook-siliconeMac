from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import unittest
import uuid
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import run_multimodel_round1_mlx as mlx_runner  # noqa: E402


class MultimodelRound1RuntimeSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        base = (
            ROOT
            / ".omo"
            / "evidence"
            / "b17-t05-multimodel-round1"
            / "recovery"
            / "runtime-tests"
        )
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / uuid.uuid4().hex
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_disk_floor_is_strict_and_includes_projection_and_margin(self) -> None:
        status = getattr(mlx_runner, "disk_headroom_status", None)
        self.assertTrue(callable(status), "disk headroom guard is missing")
        floor = 30 * 1024**3
        projected = 256 * 1024**2
        margin = 2 * 1024**3

        exact = status(
            self.root,
            projected_bytes=projected,
            safety_margin_bytes=margin,
            free_bytes=floor + projected + margin,
        )
        above = status(
            self.root,
            projected_bytes=projected,
            safety_margin_bytes=margin,
            free_bytes=floor + projected + margin + 1,
        )

        self.assertFalse(exact["ok"])
        self.assertTrue(above["ok"])
        self.assertEqual(exact["remaining_after_reservations_bytes"], floor)

    def test_disk_guard_records_failed_check_before_raising(self) -> None:
        guard = getattr(mlx_runner, "require_disk_headroom", None)
        error_type = getattr(mlx_runner, "DiskHeadroomError", None)
        self.assertTrue(callable(guard), "disk headroom guard is missing")
        self.assertIsNotNone(error_type)
        receipt = self.root / "disk-checks.jsonl"

        with self.assertRaises(error_type):
            guard(
                self.root,
                projected_bytes=1,
                safety_margin_bytes=0,
                free_bytes=30 * 1024**3 + 1,
                receipt_path=receipt,
                stage="test-boundary",
            )

        recorded = json.loads(receipt.read_text().strip())
        self.assertFalse(recorded["ok"])
        self.assertEqual(recorded["stage"], "test-boundary")

    def test_global_metal_lock_contends_and_is_released_after_crash(self) -> None:
        lock_context = getattr(mlx_runner, "metal_generation_lock", None)
        busy_error = getattr(mlx_runner, "MetalLockBusyError", None)
        self.assertTrue(callable(lock_context), "global Metal lock is missing")
        self.assertIsNotNone(busy_error)
        lock_path = self.root / "metal.lock"
        child_code = (
            "import os,sys,time;"
            "sys.path.insert(0,sys.argv[1]);"
            "from multimodel_round1_runtime import metal_generation_lock;"
            "ctx=metal_generation_lock(sys.argv[2],purpose='child');"
            "ctx.__enter__();print('LOCKED',flush=True);time.sleep(60)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(BENCHMARKS), str(lock_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertIsNotNone(child.stdout)
            self.assertEqual(child.stdout.readline().strip(), "LOCKED")
            with self.assertRaises(busy_error):
                with lock_context(lock_path, purpose="parent-contention"):
                    pass
        finally:
            child.kill()
            child.wait(timeout=10)
            if child.stdout is not None:
                child.stdout.close()
            if child.stderr is not None:
                child.stderr.close()

        with lock_context(lock_path, purpose="parent-after-kill"):
            pass
        crash_code = (
            "import os,sys;"
            "sys.path.insert(0,sys.argv[1]);"
            "from multimodel_round1_runtime import acquire_metal_lock;"
            "lease=acquire_metal_lock(sys.argv[2],purpose='crash');os._exit(23)"
        )
        crashed = subprocess.run(
            [sys.executable, "-c", crash_code, str(BENCHMARKS), str(lock_path)],
            check=False,
        )
        self.assertEqual(crashed.returncode, 23)
        with lock_context(lock_path, purpose="parent-after-crash"):
            pass

    def test_wav_validation_rejects_corrupt_audio(self) -> None:
        validator = getattr(mlx_runner, "wav_is_decodable", None)
        self.assertTrue(callable(validator), "WAV integrity validator is missing")
        corrupt = self.root / "corrupt.wav"
        corrupt.write_bytes(b"RIFF truncated")
        self.assertFalse(validator(corrupt))

    def test_reference_validation_rejects_declared_hash_mismatch(self) -> None:
        validator = getattr(mlx_runner, "validate_sample_references", None)
        error_type = getattr(mlx_runner, "ReferenceIntegrityError", None)
        self.assertTrue(callable(validator), "reference integrity guard is missing")
        self.assertIsNotNone(error_type)
        references = self.root / "references"
        references.mkdir()
        reference = references / "voice.wav"
        reference.write_bytes(b"reference")
        sample = {
            "sample_id": "sample-a",
            "reference": {
                "conditioning_file": "voice.wav",
                "conditioning_sha256": hashlib.sha256(b"different").hexdigest(),
                "conditioning_transcript": "Words.",
                "conditioning_transcript_sha256": hashlib.sha256(
                    b"Words."
                ).hexdigest(),
            },
        }

        with self.assertRaises(error_type):
            validator(self.root, sample)

    def test_resume_filters_valid_pairs_before_applying_bound(self) -> None:
        partition = getattr(mlx_runner, "partition_generation_samples", None)
        fingerprint = getattr(mlx_runner, "sample_fingerprint", None)
        self.assertTrue(callable(partition), "safe resume partition is missing")
        model = {"key": "model-a", "revision": "pinned"}
        samples = []
        for index in range(3):
            sample = {
                "sample_id": f"sample-{index}",
                "blind_id": f"blind-{index}",
                "model_key": "model-a",
                "identity_key": "narrator",
                "style": "neutral",
                "target_text_sha256": hashlib.sha256(
                    f"line-{index}".encode()
                ).hexdigest(),
                "reference": {},
                "control": {"temperature": 0.8},
                "seed": index,
                "output_file": f"outputs/sample-{index}.wav",
                "result_file": f"outputs/sample-{index}.json",
            }
            samples.append(sample)
        output = self.root / samples[0]["output_file"]
        output.parent.mkdir(parents=True)
        with wave.open(str(output), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8_000)
            handle.writeframes(b"\x00\x00" * 80)
        receipt = {
            "sample_id": samples[0]["sample_id"],
            "blind_id": samples[0]["blind_id"],
            "model_key": samples[0]["model_key"],
            "target_text_sha256": samples[0]["target_text_sha256"],
            "control": samples[0]["control"],
            "audio_file": samples[0]["output_file"],
            "audio_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "sample_fingerprint": fingerprint(samples[0], model),
        }
        (self.root / samples[0]["result_file"]).write_text(json.dumps(receipt))

        pending, reused = partition(
            self.root,
            samples,
            model,
            reuse_existing=True,
            max_samples=1,
        )

        self.assertEqual([item["sample_id"] for item in reused], ["sample-0"])
        self.assertEqual([item["sample_id"] for item in pending], ["sample-1"])


if __name__ == "__main__":
    unittest.main()
