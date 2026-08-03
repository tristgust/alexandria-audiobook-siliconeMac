from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.prepare_b18_t03_reviewed_narrator_dataset import (
    ReviewedDatasetError,
    prepare_dataset,
)


class ReviewedNarratorDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "clone_voices").mkdir()
        reference = self.root / "clone_voices" / "narrator.wav"
        reference.write_bytes(b"reference")
        routes = {}
        for index in range(6):
            audio = self.root / "routes" / f"route_{index}.wav"
            audio.parent.mkdir(exist_ok=True)
            audio.write_bytes(f"audio-{index}".encode())
            routes[f"route_{index}"] = {
                "status": "production_opt_in",
                "ref_audio": audio.relative_to(self.root).as_posix(),
                "ref_audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                "ref_text": f"Transcript {index}.",
                "production_promotion_allowed": True,
                "instruction_keywords": [f"delivery {index}", "secondary"],
                "approval_basis": "operator_approved_after_listening",
                "operator_approved_at_utc": "2026-07-28T12:00:00Z",
            }
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "NARRATOR": {
                        "ref_audio": "clone_voices/narrator.wav",
                        "ref_text": "Exact reference transcript.",
                        "experimental_prompt_routing": {
                            "production_promotion_allowed": True,
                            "evidence_round_id": "round_1",
                            "routes": routes,
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_reviewed_explicit_split_dataset(self) -> None:
        output = self.root / "out"
        manifest = prepare_dataset(source_root=self.root, output_dir=output)
        self.assertEqual(manifest["sample_count"], 6)
        self.assertEqual(
            manifest["split_counts"],
            {"train": 4, "validation": 1, "test": 1},
        )
        rows = [json.loads(line) for line in (output / "metadata.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["review_status"] == "approved" for row in rows))
        self.assertEqual(rows[0]["instruction"], "delivery 0")
        self.assertTrue((output / rows[0]["audio_filepath"]).is_file())
        second = prepare_dataset(source_root=self.root, output_dir=output)
        self.assertEqual(
            manifest["dataset_fingerprint"],
            second["dataset_fingerprint"],
        )

    def test_changed_approved_audio_fails_closed(self) -> None:
        (self.root / "routes" / "route_0.wav").write_bytes(b"changed")
        with self.assertRaisesRegex(ReviewedDatasetError, "approved fingerprint"):
            prepare_dataset(source_root=self.root, output_dir=self.root / "out")


if __name__ == "__main__":
    unittest.main()
