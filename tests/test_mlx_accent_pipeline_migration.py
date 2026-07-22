from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = (
    ROOT
    / "app"
    / "mlx_backend.py"
)
PIPELINE_PATH = (
    ROOT
    / "app"
    / "accent_pipeline.py"
)


class MLXAccentPipelineMigrationTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        cls.backend_text = (
            BACKEND_PATH.read_text(
                encoding="utf-8"
            )
        )
        cls.pipeline_text = (
            PIPELINE_PATH.read_text(
                encoding="utf-8"
            )
        )
        cls.backend_tree = ast.parse(
            cls.backend_text
        )
        cls.backend_class = next(
            node
            for node in cls.backend_tree.body
            if (
                isinstance(
                    node,
                    ast.ClassDef,
                )
                and node.name
                == "MLXBackend"
            )
        )

    def method_source(self, name):
        node = next(
            node
            for node
            in self.backend_class.body
            if (
                isinstance(
                    node,
                    ast.FunctionDef,
                )
                and node.name == name
            )
        )

        return ast.get_source_segment(
            self.backend_text,
            node,
        )

    def test_accent_data_moved_out_of_backend(self):
        self.assertNotIn(
            "Henri regarda les vieilles pierres",
            self.backend_text,
        )
        self.assertNotIn(
            "Генрих медленно пересёк",
            self.backend_text,
        )
        self.assertNotIn(
            "pipelines = [",
            self.backend_text,
        )

        self.assertIn(
            "Henri regarda les vieilles pierres",
            self.pipeline_text,
        )
        self.assertIn(
            "Генрих медленно пересёк",
            self.pipeline_text,
        )

    def test_compatibility_symbols_remain(self):
        expected = {
            "_sha256_file",
            "_accent_registry_dir",
            "_register_accent_preview",
            "_resolve_accent_clone_reference",
            "_split_clone_segments",
            "_accent_pipeline_for",
        }
        actual = {
            node.name
            for node
            in self.backend_class.body
            if isinstance(
                node,
                ast.FunctionDef,
            )
        }

        self.assertTrue(
            expected.issubset(actual)
        )

    def test_compatibility_methods_delegate(self):
        expected_calls = {
            "_sha256_file": (
                "shared_sha256_file"
            ),
            "_accent_registry_dir": (
                "shared_accent_registry_dir"
            ),
            "_register_accent_preview": (
                "shared_register_accent_preview"
            ),
            "_resolve_accent_clone_reference": (
                "shared_resolve_accent_clone_reference"
            ),
            "_split_clone_segments": (
                "shared_split_clone_segments"
            ),
            "_accent_pipeline_for": (
                "detect_accent_pipeline"
            ),
        }

        for method, call in expected_calls.items():
            with self.subTest(method=method):
                source = self.method_source(
                    method
                )
                self.assertIn(
                    call,
                    source,
                )

    def test_design_then_clone_order_is_preserved(self):
        source = self.method_source(
            "_generate_design_preview_locked"
        )

        design_position = source.index(
            'self._model("design")'
        )
        clone_position = source.index(
            'self._model("clone")'
        )
        register_position = source.index(
            "self._register_accent_preview"
        )

        self.assertLess(
            design_position,
            clone_position,
        )
        self.assertLess(
            clone_position,
            register_position,
        )

    def test_native_and_output_languages_remain_separate(self):
        source = self.method_source(
            "_generate_design_preview_locked"
        )

        self.assertIn(
            "build_native_seed_instruction",
            source,
        )
        self.assertIn(
            "lang_code=native_language",
            source,
        )
        self.assertIn(
            "normalize_output_language",
            source,
        )
        self.assertIn(
            "lang_code=output_language",
            source,
        )

    def test_ordinary_design_path_still_exists(self):
        source = self.method_source(
            "_generate_design_preview_locked"
        )

        self.assertIn(
            "if pipeline is None:",
            source,
        )
        self.assertIn(
            "lang_code=self.language",
            source,
        )


if __name__ == "__main__":
    unittest.main()
