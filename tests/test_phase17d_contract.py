from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


def frontend_source() -> str:
    return (
        ROOT
        / "app"
        / "static"
        / "index.html"
    ).read_text(
        encoding="utf-8"
    )


def provenance_javascript() -> str:
    source = frontend_source()
    start_marker = (
        "function "
        "scriptGenerationProvenancePresentation("
    )
    end_marker = (
        "function "
        "renderScriptGenerationStatus(status)"
    )

    start = source.index(start_marker)
    end = source.index(
        end_marker,
        start,
    )

    return source[start:end]


class Phase17DContractTests(
    unittest.TestCase
):
    def test_provenance_elements_exist_once(self):
        source = frontend_source()

        element_ids = (
            "script-generation-provenance",
            "script-generation-metadata-status",
            "script-generation-provenance-note",
            "script-generation-source-name",
            "script-generation-generated-at",
            "script-generation-model-name",
            "script-generation-backend",
            "script-generation-chunk-count",
            "script-generation-entry-count",
            "script-generation-speakers",
            "script-generation-resume-status",
            "script-generation-script-fingerprint",
        )

        for element_id in element_ids:
            with self.subTest(
                element_id=element_id
            ):
                self.assertEqual(
                    source.count(
                        f'id="{element_id}"'
                    ),
                    1,
                )

    def test_all_result_states_have_presentations(
        self,
    ):
        source = provenance_javascript()

        for status in (
            "complete",
            "legacy",
            "missing",
            "finalization_pending",
            "metadata_corrupt",
            "metadata_invalid",
            "orphan_metadata",
            "script_corrupt",
            "script_invalid",
            "unavailable",
        ):
            with self.subTest(status=status):
                self.assertIn(
                    f"{status}: {{",
                    source,
                )

    def test_only_selected_stable_metadata_fields_render(
        self,
    ):
        source = provenance_javascript()

        required_fields = (
            "metadata.generated_at_utc",
            "source.basename",
            "identity.model_name",
            "identity.backend",
            "source.chunk_count",
            "metadataResult.entry_count",
            "metadataResult.speaker_labels",
            "resume.resumed",
            "resume.previously_completed_chunks",
            "metadataResult.script_fingerprint",
        )

        for field in required_fields:
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    source,
                )

        forbidden_fields = (
            "system_prompt",
            "user_prompt_template",
            "base_url",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "max_tokens",
            "banned_tokens",
            "structured_output",
            "corrective_retry",
            "thinking",
            "JSON.stringify",
            "telemetry",
        )

        for field in forbidden_fields:
            with self.subTest(field=field):
                self.assertNotIn(
                    field,
                    source,
                )

    def test_invalid_metadata_is_not_used_as_provenance(
        self,
    ):
        source = provenance_javascript()

        self.assertIn(
            "result.metadata_status === 'valid'",
            source,
        )
        self.assertIn(
            "? result.metadata",
            source,
        )

    def test_metadata_errors_are_presented_safely(
        self,
    ):
        source = provenance_javascript()

        self.assertIn(
            "Array.isArray(result.errors)",
            source,
        )
        self.assertIn(
            "note.textContent",
            source,
        )
        self.assertNotIn(
            "note.innerHTML",
            source,
        )

    def test_provenance_renderer_receives_full_status(
        self,
    ):
        source = frontend_source()

        self.assertIn(
            "renderScriptGenerationProvenance(\n"
            "                status\n"
            "            );",
            source,
        )
        self.assertIn(
            "status.checkpoint",
            provenance_javascript(),
        )

    def test_full_script_fingerprint_is_rendered(
        self,
    ):
        source = provenance_javascript()

        self.assertIn(
            "currentFingerprint",
            source,
        )
        self.assertIn(
            "'script-generation-script-fingerprint'",
            source,
        )
        self.assertIn(
            "element.title = String(value)",
            source,
        )

    def test_saved_script_load_refreshes_provenance(
        self,
    ):
        source = frontend_source()
        start = source.index(
            "async function loadScript(name)"
        )
        end = source.index(
            "async function deleteScript(name)",
            start,
        )
        segment = source[start:end]

        self.assertIn(
            "await refreshScriptGenerationStatus();",
            segment,
        )

    def test_existing_refresh_paths_remain(self):
        source = frontend_source()

        self.assertGreaterEqual(
            source.count(
                "await refreshScriptGenerationStatus();"
            ),
            4,
        )
        self.assertIn(
            "startScriptGenerationStatusPolling();",
            source,
        )
        self.assertIn(
            "refreshScriptGenerationStatus();",
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
            "metadata",
            segment.lower(),
        )

    def test_phase17d_adds_no_new_api_route(
        self,
    ):
        path = ROOT / "app" / "app.py"
        source = path.read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        status_routes = []
        metadata_routes = []

        for node in tree.body:
            if not isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            ):
                continue

            for decorator in node.decorator_list:
                if not isinstance(
                    decorator,
                    ast.Call,
                ):
                    continue

                function = decorator.func

                if not isinstance(
                    function,
                    ast.Attribute,
                ):
                    continue

                if not decorator.args:
                    continue

                argument = decorator.args[0]

                if (
                    not isinstance(
                        argument,
                        ast.Constant,
                    )
                    or not isinstance(
                        argument.value,
                        str,
                    )
                ):
                    continue

                route = argument.value

                if (
                    route
                    == "/api/script_generation/status"
                ):
                    status_routes.append(
                        (
                            function.attr.lower(),
                            node.name,
                        )
                    )

                if "generation_metadata" in route:
                    metadata_routes.append(route)

        self.assertEqual(
            status_routes,
            [
                (
                    "get",
                    "get_script_generation_status",
                )
            ],
        )
        self.assertEqual(
            metadata_routes,
            [],
        )


if __name__ == "__main__":
    unittest.main()
