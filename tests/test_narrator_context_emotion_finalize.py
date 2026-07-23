from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "prepare_narrator_context_emotion_pass.py"
)
SPEC = importlib.util.spec_from_file_location(
    "prepare_narrator_context_emotion_pass",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NarratorContextFinalizeTests(unittest.TestCase):
    def _write_json(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_context_recut_supersedes_prior_and_note_recovery_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prior_root = root / "prior"
            output_root = root / "context"
            results_path = root / "context-results.json"
            prior_results_path = root / "prior-results.json"
            output_zip = root / "reviewed.zip"

            prior_audio = prior_root / "review" / "audio"
            prior_audio.mkdir(parents=True)
            for sample_id in ("02a843d600853f2d", "prior_keep"):
                (prior_audio / f"{sample_id}.wav").write_bytes(
                    b"RIFFfixture-prior-" + sample_id.encode("ascii")
                )
            self._write_json(
                prior_root / "triage-manifest.json",
                {
                    "rows": [
                        {
                            "sample_id": "02a843d600853f2d",
                            "source_start_seconds": 1.0,
                        },
                        {
                            "sample_id": "prior_keep",
                            "source_start_seconds": 2.0,
                        },
                    ]
                },
            )
            self._write_json(
                prior_results_path,
                {
                    "rows": [
                        {
                            "sample_id": "02a843d600853f2d",
                            "status": "accepted",
                            "transcript": "I am sane. I am in control of my mind.",
                            "instruction": "Neutral delivery.",
                            "category": "neutral",
                        },
                        {
                            "sample_id": "prior_keep",
                            "status": "accepted",
                            "transcript": "A clean ordinary line.",
                            "instruction": "Natural clear narration.",
                            "category": "neutral",
                        },
                    ]
                },
            )

            supplement_audio = output_root / "audio"
            supplement_audio.mkdir(parents=True)
            replacement_id = "ee0272a5f9dd6490"
            recovery_id = "2478f8f45eaa7324"
            replacement_path = supplement_audio / f"{replacement_id}.wav"
            recovery_path = supplement_audio / f"{recovery_id}.wav"
            replacement_path.write_bytes(b"RIFFfixture-replacement")
            recovery_path.write_bytes(b"RIFFfixture-recovery")
            self._write_json(
                output_root / "manifest.json",
                {
                    "corrections": [],
                    "supplement": [
                        {
                            "sample_id": replacement_id,
                            "audio_path": str(replacement_path),
                            "transcript": (
                                "I am sane. I am in control of my mind. "
                                "I know what is real, and what isn't."
                            ),
                            "source_start_seconds": 10.0,
                        },
                        {
                            "sample_id": recovery_id,
                            "audio_path": str(recovery_path),
                            "transcript": (
                                "Oh, no. No, it's to the right, my mistake. "
                                "No, no, no, not the right."
                            ),
                            "source_start_seconds": 20.0,
                        },
                    ],
                },
            )
            self._write_json(
                results_path,
                {
                    "round_id": MODULE.ROUND_ID,
                    "corrections": [
                        {
                            "sample_id": "02a843d600853f2d",
                            "action": "keep_original",
                        }
                    ],
                    "supplement": [
                        {
                            "sample_id": replacement_id,
                            "status": "accepted",
                            "transcript_confirmed": True,
                            "transcript": (
                                "I am sane. I am in control of my mind. "
                                "I know what is real, and what isn't."
                            ),
                            "instruction": "Firm defensive certainty.",
                            "category": "defensive_reassurance",
                        },
                        {
                            "sample_id": recovery_id,
                            "status": "rejected",
                            "transcript_confirmed": True,
                            "transcript": (
                                "Oh, no. No, it's to the right, my mistake. "
                                "No, no, no, not the right."
                            ),
                            "instruction": "Panic.",
                            "category": "panic_alarm",
                            "notes": "I want to use this but not like that.",
                        },
                    ],
                },
            )

            result = MODULE.finalize(
                argparse.Namespace(
                    prior_root=str(prior_root),
                    prior_results=str(prior_results_path),
                    output_root=str(output_root),
                    results=str(results_path),
                    output_zip=str(output_zip),
                    dataset_id="fixture_context_dataset",
                    minimum_accepted=1,
                    force=True,
                )
            )

            self.assertEqual(result["accepted_count"], 3)
            self.assertEqual(result["superseded_prior_count"], 1)
            self.assertEqual(result["recovered_supplement_count"], 1)
            with zipfile.ZipFile(output_zip) as archive:
                metadata = [
                    json.loads(line)
                    for line in archive.read("metadata.jsonl")
                    .decode("utf-8")
                    .splitlines()
                ]
                manifest = json.loads(
                    archive.read("preparation_manifest.json")
                )

            ids = {row["triage_sample_id"] for row in metadata}
            self.assertNotIn("02a843d600853f2d", ids)
            self.assertIn(replacement_id, ids)
            self.assertIn(recovery_id, ids)
            recovered = next(
                row
                for row in metadata
                if row["triage_sample_id"] == recovery_id
            )
            self.assertEqual(recovered["delivery_category"], "dry_flustered")
            self.assertEqual(recovered["provenance"], "review_note_recovery")
            self.assertEqual(len(manifest["superseded_prior_samples"]), 1)
            self.assertEqual(len(manifest["review_note_recoveries"]), 1)


if __name__ == "__main__":
    unittest.main()
