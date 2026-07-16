from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from script_library import (
    current_metadata_path,
    delete_script_bundle,
    list_saved_script_records,
    load_script_bundle,
    save_script_bundle,
)


class ScriptLibraryTests(
    unittest.TestCase
):
    def write_json(
        self,
        path: Path,
        value,
    ) -> None:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        path.write_text(
            json.dumps(value),
            encoding="utf-8",
        )

    def test_save_list_load_and_delete_metadata(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts_dir = root / "scripts"
            script_path = (
                root
                / "annotated_script.json"
            )
            voice_path = (
                root
                / "voice_config.json"
            )
            metadata_path = Path(
                current_metadata_path(
                    script_path
                )
            )
            chunks_path = (
                root
                / "chunks.json"
            )

            script_value = [
                {
                    "speaker": "NARRATOR",
                    "text": "Text.",
                    "instruct": "Neutral.",
                }
            ]
            voice_value = {
                "NARRATOR": {
                    "type": "design"
                }
            }
            metadata_value = {
                "schema_version": 1,
                "result": {
                    "entry_count": 1
                },
            }

            self.write_json(
                script_path,
                script_value,
            )
            self.write_json(
                voice_path,
                voice_value,
            )
            self.write_json(
                metadata_path,
                metadata_value,
            )

            save_script_bundle(
                scripts_dir=scripts_dir,
                name="example",
                script_path=script_path,
                voice_config_path=voice_path,
                metadata_path=metadata_path,
            )

            self.write_json(
                scripts_dir
                / "orphan.meta.json",
                {"orphan": True},
            )
            self.write_json(
                scripts_dir
                / "orphan.voice_config.json",
                {"orphan": True},
            )

            listing = (
                list_saved_script_records(
                    scripts_dir
                )
            )

            self.assertEqual(
                len(listing),
                1,
            )
            self.assertEqual(
                listing[0]["name"],
                "example",
            )
            self.assertTrue(
                listing[0][
                    "has_voice_config"
                ]
            )
            self.assertTrue(
                listing[0][
                    "has_metadata"
                ]
            )

            script_path.unlink()
            voice_path.unlink()
            metadata_path.unlink()

            self.write_json(
                chunks_path,
                {"stale": True},
            )

            load_script_bundle(
                scripts_dir=scripts_dir,
                name="example",
                script_path=script_path,
                voice_config_path=voice_path,
                metadata_path=metadata_path,
                chunks_path=chunks_path,
            )

            self.assertEqual(
                json.loads(
                    script_path.read_text(
                        encoding="utf-8"
                    )
                ),
                script_value,
            )
            self.assertEqual(
                json.loads(
                    metadata_path.read_text(
                        encoding="utf-8"
                    )
                ),
                metadata_value,
            )
            self.assertFalse(
                chunks_path.exists()
            )

            delete_script_bundle(
                scripts_dir=scripts_dir,
                name="example",
            )

            self.assertFalse(
                (
                    scripts_dir
                    / "example.json"
                ).exists()
            )
            self.assertFalse(
                (
                    scripts_dir
                    / "example.voice_config.json"
                ).exists()
            )
            self.assertFalse(
                (
                    scripts_dir
                    / "example.meta.json"
                ).exists()
            )

    def test_legacy_load_clears_stale_metadata(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            script_path = (
                root
                / "annotated_script.json"
            )
            voice_path = (
                root
                / "voice_config.json"
            )
            metadata_path = Path(
                current_metadata_path(
                    script_path
                )
            )
            chunks_path = (
                root
                / "chunks.json"
            )

            legacy_script = [
                {
                    "speaker": "NARRATOR",
                    "text": "Legacy.",
                    "instruct": "Neutral.",
                }
            ]

            self.write_json(
                scripts_dir / "legacy.json",
                legacy_script,
            )
            self.write_json(
                metadata_path,
                {"stale": True},
            )

            load_script_bundle(
                scripts_dir=scripts_dir,
                name="legacy",
                script_path=script_path,
                voice_config_path=voice_path,
                metadata_path=metadata_path,
                chunks_path=chunks_path,
            )

            self.assertEqual(
                json.loads(
                    script_path.read_text(
                        encoding="utf-8"
                    )
                ),
                legacy_script,
            )
            self.assertFalse(
                metadata_path.exists()
            )

    def test_save_without_current_metadata_removes_stale_companion(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()
            script_path = (
                root
                / "annotated_script.json"
            )
            voice_path = (
                root
                / "voice_config.json"
            )
            metadata_path = Path(
                current_metadata_path(
                    script_path
                )
            )

            self.write_json(
                script_path,
                [],
            )
            self.write_json(
                scripts_dir
                / "example.meta.json",
                {"stale": True},
            )

            save_script_bundle(
                scripts_dir=scripts_dir,
                name="example",
                script_path=script_path,
                voice_config_path=voice_path,
                metadata_path=metadata_path,
            )

            self.assertFalse(
                (
                    scripts_dir
                    / "example.meta.json"
                ).exists()
            )

    def test_annotated_script_api_remains_script_only(
        self,
    ):
        app_path = (
            Path(__file__)
            .resolve()
            .parents[1]
            / "app"
            / "app.py"
        )
        source = app_path.read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        matches = [
            node
            for node in tree.body
            if isinstance(
                node,
                ast.AsyncFunctionDef,
            )
            and node.name
            == "get_annotated_script"
        ]

        self.assertEqual(
            len(matches),
            1,
        )

        node = matches[0]
        segment = "\n".join(
            source.splitlines()[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        self.assertIn(
            "return json.load(f)",
            segment,
        )
        self.assertNotIn(
            "meta.json",
            segment,
        )


if __name__ == "__main__":
    unittest.main()
