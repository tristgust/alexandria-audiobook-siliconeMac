from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))


class MultimodelRound1ChatterboxResumeTests(unittest.TestCase):
    @staticmethod
    def load_runner() -> ModuleType:
        path = BENCHMARKS / "run_multimodel_round1_chatterbox_v3.py"
        if not path.is_file():
            raise AssertionError("guarded Chatterbox V3 runner is missing")
        spec = importlib.util.spec_from_file_location("round1_chatterbox", path)
        if spec is None or spec.loader is None:
            raise AssertionError("could not load guarded Chatterbox V3 runner")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_recovered_fingerprint_matches_all_completed_receipts(self) -> None:
        runner = self.load_runner()
        evidence = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
        manifest = json.loads(
            (evidence / "round1_internal_manifest.json").read_text()
        )
        samples = {
            sample["sample_id"]: sample
            for sample in manifest["sample_specs"]
            if sample["model_key"] == "chatterbox_multilingual_v3"
        }
        receipts = sorted(
            path
            for path in (evidence / "outputs" / "chatterbox_multilingual_v3").rglob(
                "*.json"
            )
            if not path.name.endswith(".lock")
        )

        self.assertEqual(
            len(receipts), len(samples) - len(runner.QUARANTINED_SAMPLES)
        )
        for path in receipts:
            receipt = json.loads(path.read_text())
            self.assertEqual(
                runner.chatterbox_sample_fingerprint(samples[receipt["sample_id"]]),
                receipt["sample_fingerprint"],
                receipt["sample_id"],
            )

    def test_chatterbox_resume_reuses_before_bounding_pending_work(self) -> None:
        runner = self.load_runner()
        evidence = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
        manifest = json.loads(
            (evidence / "round1_internal_manifest.json").read_text()
        )
        samples = [
            sample
            for sample in manifest["sample_specs"]
            if sample["model_key"] == "chatterbox_multilingual_v3"
        ]

        pending, reused = runner.partition_chatterbox_samples(
            evidence,
            samples,
            reuse_existing=True,
            max_samples=1,
        )

        self.assertEqual(
            len(reused), len(samples) - len(runner.QUARANTINED_SAMPLES)
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            {sample["sample_id"] for sample in pending}, runner.QUARANTINED_SAMPLES
        )
        self.assertEqual(
            runner.RUNTIME_CONTROLS,
            {
                "device": "mps",
                "cpu_staged_checkpoint_load": True,
                "watermark_applied": False,
                "watermark_reason": "perth_backend_unavailable_on_macos",
                "temperature": 0.8,
                "repetition_penalty": 1.2,
                "min_p": 0.05,
                "top_p": 1.0,
            },
        )

    def test_selection_excludes_only_explicitly_quarantined_samples(self) -> None:
        runner = self.load_runner()
        manifest = {
            "sample_specs": [
                {
                    "sample_id": sample_id,
                    "model_key": "chatterbox_multilingual_v3",
                    "status": "pending_generation",
                    "group": "baseline_positive",
                    "style": "proud",
                    "identity_key": "narrator",
                }
                for sample_id in ("unsafe", "safe")
            ]
        }
        args = SimpleNamespace(
            group=None,
            style=None,
            identity=None,
            skip_sample=["unsafe"],
        )

        selected = runner.selected_samples(manifest, args)

        self.assertEqual([item["sample_id"] for item in selected], ["safe"])

    def test_configures_nonzero_conservative_mps_watermarks(self) -> None:
        runner = self.load_runner()
        environment: dict[str, str] = {}

        runner.configure_mps_safety_environment(environment)

        high = float(environment["PYTORCH_MPS_HIGH_WATERMARK_RATIO"])
        low = float(environment["PYTORCH_MPS_LOW_WATERMARK_RATIO"])
        self.assertGreater(low, 0.0)
        self.assertLess(low, high)
        self.assertLessEqual(high, 0.5)

    def test_releases_unused_mps_cache_after_each_sample(self) -> None:
        runner = self.load_runner()
        calls: list[str] = []
        fake_torch = SimpleNamespace(
            mps=SimpleNamespace(empty_cache=lambda: calls.append("empty"))
        )

        runner.release_sample_mps_cache(fake_torch)

        self.assertEqual(calls, ["empty"])

    def test_abort_cleanup_removes_only_pid_partials(self) -> None:
        runner = self.load_runner()
        root = (
            ROOT
            / ".omo"
            / "evidence"
            / "b17-t05-multimodel-round1"
            / "recovery"
            / "runtime-tests"
            / uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, root)
        root.mkdir(parents=True)
        final_wav = root / "sample.wav"
        final_json = root / "sample.json"
        partial_wav = root / "sample.123.partial.wav"
        partial_json = root / "sample.123.partial.json"
        final_wav.write_bytes(b"published-audio")
        final_json.write_text("published-receipt")
        partial_wav.write_bytes(b"partial-audio")
        partial_json.write_text("partial-receipt")

        runner.cleanup_sample_partials(partial_wav, partial_json)

        self.assertEqual(final_wav.read_bytes(), b"published-audio")
        self.assertEqual(final_json.read_text(), "published-receipt")
        self.assertFalse(partial_wav.exists())
        self.assertFalse(partial_json.exists())


if __name__ == "__main__":
    unittest.main()
