from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.package_b18_t04_blind_review import package_round


class B18BlindReviewPackageTests(unittest.TestCase):
    def test_public_data_hides_candidate_identity_and_answer_key_is_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "round"
            manifest = package_round(output_root=output)
            public = json.loads((output / "review" / "data.json").read_text())
            answers = json.loads((output / "answer-keys" / "answer-key.json").read_text())
            rendered = json.dumps(public)
            for answer in answers["answers"]:
                self.assertNotIn(answer["candidate_id"], rendered)
            self.assertEqual(manifest["sample_count"], 10)
            self.assertEqual(manifest["lane_counts"], {"neutral": 4, "dread": 6})
            self.assertTrue((output / "review" / "index.html").is_file())
            self.assertFalse(public.get("production_promotion_allowed", False))


if __name__ == "__main__":
    unittest.main()
