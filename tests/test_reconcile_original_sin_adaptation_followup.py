from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "benchmarks/reconcile_original_sin_adaptation_followup.py"


def load_module():
    spec = importlib.util.spec_from_file_location("original_sin_reconcile", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load reconciliation module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OriginalSinAdaptationFollowupTests(unittest.TestCase):
    def test_user_evidence_rounds_are_unique(self) -> None:
        module = load_module()
        round_ids = [row[2] for row in module.USER_REVIEW_LINEAGE]
        canonical_paths = [row[1] for row in module.USER_REVIEW_LINEAGE]
        self.assertEqual(len(round_ids), 10)
        self.assertEqual(len(round_ids), len(set(round_ids)))
        self.assertEqual(len(canonical_paths), len(set(canonical_paths)))

    def test_beltempest_contract_keeps_neutral_and_modes_separate(self) -> None:
        module = load_module()
        self.assertEqual(len(module.BELTEMPEST_MODES), 4)
        self.assertNotIn("neutral", module.BELTEMPEST_MODES)
        self.assertEqual(len(module.BELTEMPEST_ANCHOR_SHA256), 64)


if __name__ == "__main__":
    unittest.main()
