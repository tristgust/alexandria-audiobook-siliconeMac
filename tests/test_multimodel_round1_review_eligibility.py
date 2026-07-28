from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmarks.multimodel_round1_handoff import CANONICAL_PUBLIC_ROOT_NAME
from tests import multimodel_round1_packaging_fixture as fixture_support


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / "benchmarks" / "package_multimodel_round1_review.py"
VERIFIER = ROOT / "benchmarks" / "verify_multimodel_round1.py"
DATA_PREFIX = "window.ALEXANDRIA_ROUND1_DATA = "


class ReviewEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.evidence = Path(self.temporary.name) / "evidence"
        self.samples, _ = fixture_support.make_fixture(self.evidence)
        self.make_first_sample_a_moss_ceiling_hit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, script: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--evidence-root", str(self.evidence)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_first_sample_a_moss_ceiling_hit(self) -> None:
        manifest_path = self.evidence / "round1_internal_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        sample = manifest["sample_specs"][0]
        model = manifest["model_contract"]["models"][0]
        model["key"] = "moss_tts_local_v15"
        sample["model_key"] = model["key"]
        sample["control"].update(
            {
                "max_tokens": 768,
                "audio_temperature": 1.7,
                "audio_top_p": 0.8,
                "audio_top_k": 25,
                "n_vq_for_inference": 12,
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result_path = self.evidence / sample["result_file"]
        receipt = json.loads(result_path.read_text())
        reference = sample["reference"]
        relevant = {
            "round": fixture_support.ROUND_ID,
            "sample_id": sample["sample_id"],
            "model": model,
            "identity_key": sample["identity_key"],
            "style": sample["style"],
            "target_text_sha256": sample["target_text_sha256"],
            "reference": {
                key: reference.get(key)
                for key in (
                    "conditioning_sha256",
                    "conditioning_transcript_sha256",
                    "acted_emotion_reference_sha256",
                )
            },
            "control": sample["control"],
            "seed": sample["seed"],
        }
        receipt.update(
            {
                "model_key": model["key"],
                "control": sample["control"],
                "sample_fingerprint": fixture_support.canonical_hash(relevant),
                "runtime_controls": {"max_tokens": 768},
                "audio": {"duration_seconds": 122.88},
            }
        )
        result_path.write_text(json.dumps(receipt), encoding="utf-8")

    def test_ceiling_hit_is_structural_but_not_blind_review_eligible(self) -> None:
        packaged = self.run_cli(PACKAGER)

        self.assertEqual(packaged.returncode, 0, packaged.stderr)
        anomaly_path = (
            self.evidence / "recovery" / "moss-long-output-anomalies.json"
        )
        anomaly = json.loads(anomaly_path.read_text())
        self.assertEqual(anomaly["over_30_seconds_count"], 1)
        self.assertEqual(anomaly["ceiling_hit_count"], 1)
        entry = anomaly["entries"][0]
        self.assertEqual(entry["identity_key"], "narrator")
        self.assertEqual(entry["style"], "neutral")
        self.assertEqual(entry["duration_seconds"], 122.88)
        self.assertEqual(entry["max_tokens"], 768)
        self.assertFalse(entry["review_eligible"])
        self.assertIn("receipt_sha256", entry)
        review = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        self.assertEqual(len(list((review / "audio").glob("*.wav"))), 1)
        review_manifest = json.loads(
            (review / "manifest.json").read_text()
        )
        self.assertEqual(review_manifest["structurally_generated_sample_count"], 2)
        self.assertEqual(review_manifest["review_eligible_sample_count"], 1)
        self.assertEqual(review_manifest["ceiling_hit_count"], 1)
        raw = (review / "data.js").read_text()
        public = json.loads(raw[len(DATA_PREFIX) : -2])
        held = next(row for row in public["samples"] if row["sample_id"] == "blind_one")
        self.assertEqual(held["status"], "diagnostic_hold")
        self.assertIsNone(held["audio"])

        verified = self.run_cli(VERIFIER)
        self.assertEqual(verified.returncode, 0, verified.stderr + verified.stdout)
        result = json.loads(verified.stdout)
        self.assertEqual(result["structurally_generated_sample_count"], 2)
        self.assertEqual(result["review_eligible_sample_count"], 1)
        self.assertEqual(result["ceiling_hit_count"], 1)


if __name__ == "__main__":
    unittest.main()
