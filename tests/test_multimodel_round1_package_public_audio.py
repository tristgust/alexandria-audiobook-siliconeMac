from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmarks.multimodel_round1_handoff import (
    ANSWER_KEY_ROOT_NAME,
    CANONICAL_PUBLIC_ROOT_NAME,
)
from benchmarks.multimodel_round1_public_audio import verify_public_audio
from tests.multimodel_round1_packaging_fixture import (
    JsonValue,
    ROUND_ID,
    canonical_hash,
    make_fixture,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / "benchmarks/package_multimodel_round1_review.py"
VERIFIER = ROOT / "benchmarks/verify_multimodel_round1.py"
DATA_PREFIX = "window.ALEXANDRIA_ROUND1_DATA = "


def _run_cli(script: Path, evidence: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), "--evidence-root", str(evidence)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _read_public(review: Path) -> dict[str, JsonValue]:
    raw = (review / "data.js").read_text(encoding="utf-8")
    return json.loads(raw[len(DATA_PREFIX) : -2])


def _write_public(review: Path, value: dict[str, JsonValue]) -> None:
    payload = DATA_PREFIX + json.dumps(value, ensure_ascii=False) + ";\n"
    (review / "data.js").write_text(payload, encoding="utf-8")


def _tag_wav(path: Path) -> None:
    tagged = path.with_name(f".{path.stem}.tagged.wav")
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
            "-map", "0:a:0", "-c:a", "copy",
            "-metadata", "artist=derricksjones",
            "-metadata", "title=7thDoctorSpeeches",
            "-metadata", "comment=/private/model/vendor/source.wav",
            str(tagged),
        ],
        check=True,
    )
    os.replace(tagged, path)


def _fingerprint(
    sample: dict[str, JsonValue], model: dict[str, JsonValue]
) -> str:
    reference = sample["reference"]
    if not isinstance(reference, dict):
        raise AssertionError("fixture reference must be an object")
    relevant: dict[str, JsonValue] = {
        "round": ROUND_ID,
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
    return canonical_hash(relevant)


class PublicAudioPackageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.evidence = Path(self.temporary.name).resolve() / "evidence"
        self.review = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        self.answers = self.evidence / ANSWER_KEY_ROOT_NAME
        self.samples, self.models = make_fixture(self.evidence)
        self._make_sources_adversarial()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_sources_adversarial(self) -> None:
        internal_path = self.evidence / "round1_internal_manifest.json"
        internal = json.loads(internal_path.read_text(encoding="utf-8"))
        first = internal["sample_specs"][0]
        output = self.evidence / first["output_file"]
        reference = self.evidence / "references" / first["reference"]["source_file"]
        _tag_wav(output)
        _tag_wav(reference)
        reference_sha = sha256_file(reference)
        first["reference"]["source_sha256"] = reference_sha
        first["reference"]["conditioning_sha256"] = reference_sha
        internal_path.write_text(json.dumps(internal), encoding="utf-8")
        self.samples[0] = first
        for index, sample in enumerate(self.samples):
            receipt_path = self.evidence / str(sample["result_file"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["sample_fingerprint"] = _fingerprint(sample, self.models[index])
            receipt["audio_sha256"] = sha256_file(
                self.evidence / str(sample["output_file"])
            )
            receipt["conditionals_cache_hit"] = index == 0
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    def test_package_sanitizes_content_hash_audio_and_records_private_equivalence(
        self,
    ) -> None:
        # Given tagged candidate/reference audio, when packaged, all public audio is blind.
        completed = _run_cli(PACKAGER, self.evidence)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        public = _read_public(self.review)
        public_text = (self.review / "data.js").read_text(encoding="utf-8")
        for row in public["samples"]:
            relative = Path(row["audio"])
            self.assertEqual(relative.stem, row["audio_sha256"])
            artifact = verify_public_audio(self.review / relative)
            self.assertEqual(artifact.sha256, row["audio_sha256"])
        for identity in public["identities"].values():
            for field in ("original_audio", "conditioning_audio"):
                relative = Path(identity[field])
                self.assertEqual(relative.stem, sha256_file(self.review / relative))
                verify_public_audio(self.review / relative)
        self.assertNotIn("cache_revalidation_status", public_text)
        self.assertNotIn("derricksjones", public_text.casefold())

        rows = json.loads((self.answers / "baseline.json").read_text())
        self.assertEqual(
            [row["cache_revalidation_status"] for row in rows],
            ["requires_revalidation", "not_flagged"],
        )
        for row in rows:
            self.assertEqual(
                row["source_decoded_sha256"], row["public_decoded_sha256"]
            )
            self.assertEqual(row["public_audio_sha256"], Path(row["public_audio"]).stem)
        private_manifest = json.loads((self.answers / "manifest.json").read_text())
        self.assertEqual(private_manifest["requires_revalidation_count"], 1)
        for record in private_manifest["reference_audio_publications"]:
            self.assertEqual(
                record["source_decoded_sha256"],
                record["public_decoded_sha256"],
            )

    def test_verifier_rejects_public_audio_metadata(self) -> None:
        self.assertEqual(_run_cli(PACKAGER, self.evidence).returncode, 0)
        public = _read_public(self.review)
        target = self.review / public["samples"][0]["audio"]
        _tag_wav(target)

        completed = _run_cli(VERIFIER, self.evidence)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("public_audio_metadata_forbidden", completed.stdout)

    def test_verifier_rejects_private_source_decoded_mismatch(self) -> None:
        self.assertEqual(_run_cli(PACKAGER, self.evidence).returncode, 0)
        answer_path = self.answers / "baseline.json"
        rows = json.loads(answer_path.read_text())
        rows[0]["source_decoded_sha256"] = "0" * 64
        answer_path.write_text(json.dumps(rows), encoding="utf-8")

        completed = _run_cli(VERIFIER, self.evidence)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("private_source_decoded_mismatch", completed.stdout)

    def test_verifier_rejects_private_cache_status_and_public_leak(self) -> None:
        self.assertEqual(_run_cli(PACKAGER, self.evidence).returncode, 0)
        answer_path = self.answers / "baseline.json"
        rows = json.loads(answer_path.read_text())
        rows[1]["cache_revalidation_status"] = "requires_revalidation"
        answer_path.write_text(json.dumps(rows), encoding="utf-8")
        public = _read_public(self.review)
        public["samples"][0]["cache_revalidation_status"] = "not_flagged"
        _write_public(self.review, public)

        completed = _run_cli(VERIFIER, self.evidence)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("private_cache_revalidation", completed.stdout)
        self.assertIn("private_cache_status_public", completed.stdout)

    def test_packager_rejects_symlinked_public_audio_directory(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.review.mkdir()
        (self.review / "audio").symlink_to(outside, target_is_directory=True)

        completed = _run_cli(PACKAGER, self.evidence)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_packager_rejects_reference_manifest_traversal(self) -> None:
        outside = self.evidence / "outside.wav"
        outside.write_bytes(b"outside")
        manifest_path = self.evidence / "round1_internal_manifest.json"
        internal = json.loads(manifest_path.read_text())
        reference = internal["sample_specs"][0]["reference"]
        reference["source_file"] = "../outside.wav"
        reference["source_sha256"] = sha256_file(outside)
        manifest_path.write_text(json.dumps(internal), encoding="utf-8")

        completed = _run_cli(PACKAGER, self.evidence)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(outside.read_bytes(), b"outside")


if __name__ == "__main__":
    unittest.main()
