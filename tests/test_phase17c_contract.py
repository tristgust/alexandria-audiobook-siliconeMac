from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


class Phase17CContractTests(
    unittest.TestCase
):
    def test_action_routes_exist(self):
        source = (
            ROOT / "app" / "app.py"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '@app.post("/api/generate_script")',
            source,
        )
        self.assertIn(
            '@app.post("/api/script_generation/discard")',
            source,
        )
        self.assertIn(
            "choose_generation_action(",
            source,
        )
        self.assertIn(
            "discard_generation_checkpoint(",
            source,
        )
        self.assertIn(
            '"--finalize-only"',
            source,
        )

    def test_generate_route_blocks_bad_state_before_start(
        self,
    ):
        path = ROOT / "app" / "app.py"
        source = path.read_text(
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
            and node.name == "generate_script"
        ]

        self.assertEqual(len(matches), 1)

        node = matches[0]
        segment = "\n".join(
            source.splitlines()[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        self.assertLess(
            segment.index(
                "choose_generation_action("
            ),
            segment.index(
                "background_tasks.add_task("
            ),
        )
        self.assertIn(
            "GenerationActionBlockedError",
            segment,
        )
        self.assertIn(
            "status_code=409",
            segment,
        )

    def test_discard_route_has_running_guard(
        self,
    ):
        path = ROOT / "app" / "app.py"
        source = path.read_text(
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
            == "discard_script_generation_state"
        ]

        self.assertEqual(
            len(matches),
            1,
        )

        endpoint = matches[0]
        has_script_process_guard = False
        has_conflict_response = False
        has_discard_call = False

        for node in ast.walk(endpoint):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(
                    node.value,
                    ast.Name,
                )
                and node.value.id
                == "process_state"
                and isinstance(
                    node.slice,
                    ast.Constant,
                )
                and node.slice.value
                == "script"
            ):
                has_script_process_guard = True

            if not isinstance(node, ast.Call):
                continue

            if (
                isinstance(
                    node.func,
                    ast.Name,
                )
                and node.func.id
                == "discard_generation_checkpoint"
            ):
                has_discard_call = True

            if (
                isinstance(
                    node.func,
                    ast.Name,
                )
                and node.func.id
                == "HTTPException"
            ):
                for keyword in node.keywords:
                    if (
                        keyword.arg
                        == "status_code"
                        and isinstance(
                            keyword.value,
                            ast.Constant,
                        )
                        and keyword.value.value
                        == 409
                    ):
                        has_conflict_response = True

        self.assertTrue(
            has_script_process_guard,
            "Discard must inspect "
            "process_state['script'].",
        )
        self.assertTrue(
            has_conflict_response,
            "Discard must return HTTP 409 "
            "while generation is running.",
        )
        self.assertTrue(
            has_discard_call,
            "Discard must call the checkpoint-only "
            "discard helper.",
        )

    def test_finalization_cli_path_exists(self):
        source = (
            ROOT
            / "app"
            / "generate_script.py"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "def finalize_completed_generation_checkpoint(",
            source,
        )
        self.assertIn(
            '"--finalize-only"',
            source,
        )
        self.assertIn(
            "finalize_completed_generation_checkpoint(",
            source,
        )



    def test_annotated_script_api_remains_unchanged(
        self,
    ):
        path = ROOT / "app" / "app.py"
        source = path.read_text(
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
