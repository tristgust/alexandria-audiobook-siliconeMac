from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


class Phase17BackendContractTests(
    unittest.TestCase
):
    def test_backend_status_route_exists(self):
        source = (
            ROOT / "app" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '@app.get("/api/script_generation/status")',
            source,
        )
        self.assertIn(
            "build_generation_status(",
            source,
        )

    def test_phase17b_has_no_discard_route(self):
        source = (
            ROOT / "app" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "/api/script_generation/discard",
            source,
        )

    def test_phase17b_does_not_modify_frontend(self):
        source = (
            ROOT
            / "app"
            / "static"
            / "index.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "script-generation-status-panel",
            source,
        )
        self.assertNotIn(
            "btn-discard-generation-state",
            source,
        )

    def test_status_endpoint_contains_no_write_operations(
        self,
    ):
        path = ROOT / "app" / "app.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        matches = [
            node
            for node in tree.body
            if isinstance(
                node,
                ast.AsyncFunctionDef,
            )
            and node.name
            == "get_script_generation_status"
        ]

        self.assertEqual(len(matches), 1)

        node = matches[0]
        segment = "\n".join(
            source.splitlines()[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        for forbidden in (
            "atomic_json_write",
            "clear_generation_state",
            ".unlink(",
            "os.remove(",
            "os.replace(",
            "preload(",
        ):
            self.assertNotIn(
                forbidden,
                segment,
            )

    def test_snapshot_is_model_load_free(self):
        path = (
            ROOT
            / "app"
            / "generate_script.py"
        )
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        matches = [
            node
            for node in tree.body
            if isinstance(
                node,
                ast.FunctionDef,
            )
            and node.name
            == "build_script_generation_snapshot"
        ]

        self.assertEqual(len(matches), 1)

        node = matches[0]
        segment = "\n".join(
            source.splitlines()[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        self.assertNotIn(".preload(", segment)
        self.assertNotIn(".status(", segment)

    def test_checkpoint_provenance_is_optional(
        self,
    ):
        source = (
            ROOT
            / "app"
            / "generation_state.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '"generation_identity"',
            source,
        )
        self.assertIn(
            '"auditor_contract_version"',
            source,
        )
        self.assertIn('"source"', source)

    def test_existing_annotated_script_api_is_unchanged(
        self,
    ):
        path = ROOT / "app" / "app.py"
        source = path.read_text(encoding="utf-8")
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

        self.assertEqual(len(matches), 1)

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
            "metadata",
            segment.lower(),
        )


if __name__ == "__main__":
    unittest.main()
