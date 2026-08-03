from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from benchmarks.adjudicate_b18_multivoice_review import (
    CANDIDATE_DISPOSITIONS,
    ROUND_ID,
    adjudicate,
)


class B18MultiVoiceAdjudicationTests(unittest.TestCase):
    def _fixtures(self, root: Path) -> tuple[Path, Path]:
        ratings = {}
        answers = []
        for sample_id in sorted(CANDIDATE_DISPOSITIONS):
            ratings[sample_id] = {
                "identity": 4,
                "delivery": 4,
                "naturalness": 4,
                "text_match": True,
                "artifact_free": True,
                "notes": "",
            }
            speaker = {
                "DOC": "THE DOCTOR",
                "BEN": "BERNICE",
                "CHR": "CHRIS CWEJ",
                "ROZ": "ROZ FORRESTER",
                "COM": "COMPUTER",
                "TOB": "TOBIAS VAUGHN",
                "HIT": "POWERLESS FRIENDLESS",
            }[sample_id[:3]]
            duplicate = (
                "d" * 64
                if sample_id in {"CHR01", "CHR04"}
                else hashlib.sha256(sample_id.encode("utf-8")).hexdigest()
            )
            answers.append(
                {
                    "sample_id": sample_id,
                    "speaker_key": speaker,
                    "candidate_id": f"candidate_{sample_id}",
                    "method": "method",
                    "source_sha256": duplicate,
                }
            )
        review = root / "review.json"
        answer = root / "answer.json"
        review.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "round_id": ROUND_ID,
                    "completed_at": "2026-08-03T20:17:02.040Z",
                    "ratings": ratings,
                }
            ),
            encoding="utf-8",
        )
        answer.write_text(
            json.dumps({"schema_version": 1, "round_id": ROUND_ID, "answers": answers}),
            encoding="utf-8",
        )
        return review, answer

    def test_adjudication_is_per_speaker_and_detects_duplicate_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            review, answer = self._fixtures(Path(temporary))
            result = adjudicate(review_path=review, answer_key_path=answer)
            self.assertEqual(result["candidate_count"], 32)
            self.assertEqual(result["speaker_count"], 7)
            self.assertFalse(result["universal_backend_selected"])
            self.assertEqual(len(result["route_updates"]), 2)
            self.assertIn(
                {"source_sha256": "d" * 64, "sample_ids": ["CHR01", "CHR04"]},
                result["duplicate_audio_groups"],
            )

    def test_incomplete_review_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            review, answer = self._fixtures(Path(temporary))
            value = json.loads(review.read_text(encoding="utf-8"))
            value["ratings"].pop("DOC01")
            review.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "incomplete or mismatched"):
                adjudicate(review_path=review, answer_key_path=answer)


if __name__ == "__main__":
    unittest.main()
