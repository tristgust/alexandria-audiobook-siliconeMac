from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review_script


class ReviewScriptRuntimePathTests(unittest.TestCase):
    def test_main_uses_explicit_managed_project_and_config_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script_path = root / "annotated_script.json"
            chunks_path = root / "chunks.json"
            config_path = root / "review-config.json"
            entries = [
                {"speaker": "NARRATOR", "text": "One.", "instruct": "Plain."},
                {"speaker": "DOCTOR", "text": "Two.", "instruct": "Direct."},
            ]
            script_path.write_text(json.dumps(entries), encoding="utf-8")
            chunks_path.write_text("[]", encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "generation": {
                            "review_batch_size": 1,
                            "merge_narrators": False,
                        }
                    }
                ),
                encoding="utf-8",
            )

            reviewed_batches: list[list[dict]] = []

            def keep_batch(*args, **kwargs):
                batch = args[2]
                reviewed_batches.append(batch)
                return batch

            argv = [
                "review_script.py",
                "--project-root",
                str(root),
                "--config-path",
                str(config_path),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    review_script,
                    "_create_review_client",
                    return_value=(object(), None),
                ),
                patch.object(review_script, "review_batch", side_effect=keep_batch),
            ):
                review_script.main()

            self.assertEqual(len(reviewed_batches), 2)
            self.assertEqual(
                json.loads(script_path.read_text(encoding="utf-8")),
                entries,
            )
            self.assertFalse(chunks_path.exists())


if __name__ == "__main__":
    unittest.main()
