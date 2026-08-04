from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "script_review_model_harness.js"


class ScriptReviewModelTests(unittest.TestCase):
    def test_stale_acceptance_is_not_an_entry_level_source_issue(self) -> None:
        completed = subprocess.run(
            ["node", str(HARNESS)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["staleApprovalEnabled"])


if __name__ == "__main__":
    unittest.main()
