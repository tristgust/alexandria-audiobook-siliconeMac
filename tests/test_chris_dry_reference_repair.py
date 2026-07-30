from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chris_dry_reference_repair as repair


class ChrisDryReferenceRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_wrong_reviewed_asset_is_rejected(self) -> None:
        candidate = self.root / "wrong.wav"
        candidate.write_bytes(b"not the reviewed repair")
        with self.assertRaisesRegex(
            repair.ChrisDryReferenceRepairError,
            "changed",
        ):
            repair.validate_reviewed_chris_dry_reference(candidate)

    def test_reviewed_asset_is_copied_and_reverified(self) -> None:
        source = self.root / "reviewed.wav"
        source.write_bytes(b"reviewed repair fixture")
        expected = repair.sha256_file(source)
        destination = self.root / "project" / "chris-dry.wav"
        with patch.object(repair, "OUTPUT_SHA256", expected):
            receipt = repair.install_reviewed_chris_dry_reference(
                source=source,
                destination=destination,
            )
        self.assertEqual(destination.read_bytes(), source.read_bytes())
        self.assertEqual(receipt["output_sha256"], expected)
        self.assertEqual(receipt["installed_sha256"], expected)
        self.assertFalse(receipt["regeneration_allowed"])


if __name__ == "__main__":
    unittest.main()
