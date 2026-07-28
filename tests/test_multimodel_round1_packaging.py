from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from benchmarks.multimodel_round1_handoff import (
    ANSWER_KEY_ROOT_NAME,
    CANONICAL_PUBLIC_ROOT_NAME,
)
from tests.multimodel_round1_packaging_fixture import (
    JsonValue,
    ROUND_ID,
    canonical_hash,
    make_fixture,
    sha256_file,
    write_wav,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / "benchmarks" / "package_multimodel_round1_review.py"
VERIFIER = ROOT / "benchmarks" / "verify_multimodel_round1.py"


class MultimodelRound1PackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"
        self.review = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        self.answer_keys = self.evidence / ANSWER_KEY_ROOT_NAME
        self.samples, self.models = self.make_fixture(self.evidence)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_fixture(
        self, evidence: Path
    ) -> tuple[list[dict[str, JsonValue]], list[dict[str, JsonValue]]]:
        return make_fixture(evidence)

    def run_cli(self, script: Path, evidence: Path | None = None) -> subprocess.CompletedProcess[str]:
        active = evidence or self.evidence
        return subprocess.run(
            [sys.executable, str(script), "--evidence-root", str(active)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_packager_rejects_stale_receipt_fingerprint(self) -> None:
        # Given a receipt whose audio hash is current but fingerprint is stale.
        receipt_path = self.evidence / str(self.samples[0]["result_file"])
        receipt = json.loads(receipt_path.read_text())
        receipt["sample_fingerprint"] = "stale"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        # When the review package is built.
        completed = self.run_cli(PACKAGER)

        # Then packaging fails at the stale receipt boundary.
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("sample_fingerprint", completed.stderr + completed.stdout)

    def test_packager_rejects_hash_matching_empty_wav(self) -> None:
        # Given an empty WAV whose receipt hash was updated to match it.
        output = self.evidence / str(self.samples[0]["output_file"])
        output.write_bytes(b"")
        receipt_path = self.evidence / str(self.samples[0]["result_file"])
        receipt = json.loads(receipt_path.read_text())
        receipt["audio_sha256"] = sha256_file(output)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        # When packaging runs, then audio decoding prevents publication.
        completed = self.run_cli(PACKAGER)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("audio_decode", completed.stderr + completed.stdout)

    def test_packager_ignores_and_preserves_legacy_public_answer_keys(self) -> None:
        # Given a stale public key, default packaging selects only the canonical root.
        legacy = self.evidence / "review" / "answer-keys" / "legacy.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("legacy", encoding="utf-8")
        completed = self.run_cli(PACKAGER)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(legacy.is_file())

    def test_packager_rejects_explicit_stale_public_root(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PACKAGER),
                "--evidence-root",
                str(self.evidence),
                "--output-root",
                str(self.evidence / "review"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("stale_public_root", completed.stderr + completed.stdout)

    def test_packager_keeps_public_review_blind_and_reports_model_counts(self) -> None:
        # Given valid generated samples, when the review package is built.
        completed = self.run_cli(PACKAGER)

        # Then keys are sibling-private, native facts are anonymous, and counts exist.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse((self.review / "answer-keys").exists())
        keys = self.answer_keys / "baseline.json"
        self.assertTrue(keys.is_file())
        public_text = (self.review / "data.js").read_text() + (
            self.review / "manifest.json"
        ).read_text()
        for secret in ("answer_key_files", "model_alpha", "model_beta", "Secret Alpha", "Secret Beta", "native_beta_voice", "Beta Star", "model_beta_native_voice"):
            self.assertNotIn(secret, public_text)
        answer_text = keys.read_text()
        self.assertIn("model_beta", answer_text)
        summary = json.loads(completed.stdout)
        self.assertEqual(
            summary["generated_counts_by_model"],
            {"model_alpha": 1, "model_beta": 1},
        )

    def test_packager_records_projected_disk_headroom_before_copy(self) -> None:
        completed = self.run_cli(PACKAGER)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        records = [
            json.loads(line)
            for line in (self.evidence / "recovery" / "disk-headroom.jsonl")
            .read_text()
            .splitlines()
        ]
        record = next(row for row in records if row["stage"] == "package:before-copy")
        self.assertTrue(record["ok"])
        self.assertGreater(record["projected_bytes"], 0)
        self.assertGreater(
            record["remaining_after_reservations_bytes"],
            record["strict_floor_bytes"],
        )

    def test_verifier_accepts_complete_package(self) -> None:
        # Given a complete package, when it is verified, then it is clean.
        packaged = self.run_cli(PACKAGER)
        self.assertEqual(packaged.returncode, 0, packaged.stderr)
        completed = self.run_cli(VERIFIER)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn('"ok": true', completed.stdout)

    def test_verifier_rejects_every_integrity_failure_class(self) -> None:
        cases = (
            ("receipt_sample_fingerprint", "fingerprint"),
            ("missing_packaged_audio", "missing"),
            ("extra_packaged_audio", "extra"),
            ("packaged_audio_hash", "copied"),
            ("receipt_audio_hash", "hash"),
            ("receipt_control", "control"),
            ("receipt_sample_id", "id"),
            ("public_blind_leak", "blind"),
            ("answer_key_stale", "stale"),
        )
        for expected, mutation in cases:
            with self.subTest(mutation=mutation):
                evidence = self.root / mutation
                samples, _ = self.make_fixture(evidence)
                packaged = self.run_cli(PACKAGER, evidence)
                self.assertEqual(packaged.returncode, 0, packaged.stderr)
                self.apply_mutation(evidence, samples, mutation)

                completed = self.run_cli(VERIFIER, evidence)

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(expected, completed.stdout + completed.stderr)

    @staticmethod
    def apply_mutation(evidence: Path, samples: list[dict[str, JsonValue]], mutation: str) -> None:
        review = evidence / CANONICAL_PUBLIC_ROOT_NAME
        raw = (review / "data.js").read_text()
        public = json.loads(raw[len("window.ALEXANDRIA_ROUND1_DATA = ") : -2])
        audio_by_id = {row["sample_id"]: row["audio"] for row in public["samples"]}
        receipt_path = evidence / str(samples[0]["result_file"])
        receipt = json.loads(receipt_path.read_text())
        if mutation == "fingerprint":
            receipt["sample_fingerprint"] = "stale"
            receipt_path.write_text(json.dumps(receipt))
        elif mutation == "missing":
            (review / audio_by_id[samples[0]["blind_id"]]).unlink()
        elif mutation == "extra":
            write_wav(review / "audio" / "extra.wav", 77)
        elif mutation == "copied":
            shutil.copyfile(
                review / audio_by_id[samples[0]["blind_id"]],
                review / audio_by_id[samples[1]["blind_id"]],
            )
        elif mutation == "hash":
            output = evidence / str(samples[0]["output_file"])
            write_wav(output, 99)
        elif mutation == "control":
            receipt["control"] = {"changed": True}
            receipt_path.write_text(json.dumps(receipt))
        elif mutation == "id":
            receipt["sample_id"] = "wrong"
            receipt_path.write_text(json.dumps(receipt))
        elif mutation == "blind":
            data = review / "data.js"
            data.write_text(data.read_text().replace('"schema_version": 1', '"schema_version": 1, "debug_model": "model_alpha"'))
        elif mutation == "stale":
            keys = evidence / ANSWER_KEY_ROOT_NAME / "baseline.json"
            keys.write_text(keys.read_text().replace('"status": "ready"', '"status": "pending_generation"', 1))


if __name__ == "__main__":
    unittest.main()
