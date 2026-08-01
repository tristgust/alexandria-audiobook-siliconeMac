from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RuntimeRootOverrideTests(unittest.TestCase):
    def test_legacy_root_can_be_separated_from_code_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            legacy_root = Path(temporary) / "installed-alexandria"
            legacy_root.mkdir()
            environment = dict(os.environ)
            environment.update(
                {
                    "ALEXANDRIA_LEGACY_ROOT_DIR": str(legacy_root),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "app"),
                }
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import app; print(app.LEGACY_ROOT_DIR)",
                ],
                capture_output=True,
                text=True,
                check=True,
                env=environment,
                timeout=30,
            )
        self.assertEqual(completed.stdout.strip(), str(legacy_root.resolve()))


if __name__ == "__main__":
    unittest.main()
