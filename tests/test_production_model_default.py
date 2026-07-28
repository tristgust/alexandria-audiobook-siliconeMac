from __future__ import annotations

import ast
import unittest
from pathlib import Path

from llm_config import (
    DEFAULT_MODEL_NAME,
    build_runtime_client,
    normalized_llm_section,
)


ROOT = Path(__file__).resolve().parents[1]
NEW_MODEL = "qwen3.5:35b-mlx"
LEGACY_MODELS = {
    (
        "richardyoung/"
        "qwen3-14b-abliterated:Q8_0"
    ),
    (
        "richardyoung/"
        "qwen3-14b-abliterated:q8_0"
    ),
}


class ProductionModelDefaultTests(
    unittest.TestCase
):
    def test_default_model_is_qwen35(self):
        self.assertEqual(
            DEFAULT_MODEL_NAME,
            NEW_MODEL,
        )

    def test_empty_config_uses_qwen35(self):
        section = normalized_llm_section(
            {}
        )

        self.assertEqual(
            section["model_name"],
            NEW_MODEL,
        )

    def test_explicit_legacy_overrides_are_preserved(self):
        for legacy in LEGACY_MODELS:
            with self.subTest(
                legacy=legacy
            ):
                section = (
                    normalized_llm_section(
                        {
                            "model_name": (
                                legacy
                            )
                        }
                    )
                )

                self.assertEqual(
                    section["model_name"],
                    legacy,
                )

    def test_runtime_without_override_uses_qwen35(self):
        runtime = build_runtime_client(
            {
                "llm": {
                    "base_url": (
                        "http://localhost:11434/v1"
                    ),
                    "api_key": "local",
                    "backend": "auto",
                }
            }
        )

        self.assertEqual(
            runtime.model_name,
            NEW_MODEL,
        )

    def test_script_fallback_uses_qwen35(self):
        path = (
            ROOT
            / "app"
            / "generate_script.py"
        )
        tree = ast.parse(
            path.read_text(
                encoding="utf-8"
            )
        )
        literals = {
            node.value
            for node in ast.walk(tree)
            if (
                isinstance(
                    node,
                    ast.Constant,
                )
                and isinstance(
                    node.value,
                    str,
                )
            )
        }

        self.assertIn(
            NEW_MODEL,
            literals,
        )
        self.assertTrue(
            LEGACY_MODELS.isdisjoint(
                literals
            )
        )

    def test_settings_ui_uses_runtime_model_value(self):
        pages = ROOT / "app" / "static" / "pages"
        text = "\n".join(
            (pages / name).read_text(encoding="utf-8")
            for name in (
                "settings.js",
                "settings_model.js",
                "settings_sections.js",
                "settings_view.js",
            )
        )

        self.assertIn(
            "value: draft.provider.model_name",
            text,
        )

        for legacy in LEGACY_MODELS:
            self.assertNotIn(
                legacy,
                text,
            )


if __name__ == "__main__":
    unittest.main()
