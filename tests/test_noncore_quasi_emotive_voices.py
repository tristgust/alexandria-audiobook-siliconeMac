from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from generation_state import atomic_json_write
from noncore_quasi_emotive_voices import (
    NoncoreVoicePackError,
    inspect_noncore_quasi_emotive_voices,
    install_noncore_quasi_emotive_voices,
    rollback_noncore_quasi_emotive_voices,
)


class NoncoreQuasiEmotiveVoiceTests(unittest.TestCase):
    def test_confirmation_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(NoncoreVoicePackError):
                install_noncore_quasi_emotive_voices(
                    project_root=directory,
                    answer_key_path="missing.json",
                    decision_path="missing.json",
                    confirm_production_opt_in=False,
                )

    def test_inspection_fails_closed_without_pack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json_write({}, root / "voice_config.json")
            status = inspect_noncore_quasi_emotive_voices(root)
            self.assertFalse(status["ready"])
            self.assertIn("not routed", status["error"])

    def test_rollback_requires_an_installed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json_write(
                {"pack_id": "wrong", "status": "installed"},
                root / "noncore_quasi_emotive_voice_pack.json",
            )
            with self.assertRaises(NoncoreVoicePackError):
                rollback_noncore_quasi_emotive_voices(
                    project_root=root,
                    confirm_rollback=True,
                )


if __name__ == "__main__":
    unittest.main()
