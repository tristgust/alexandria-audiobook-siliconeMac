from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlsplit

from more_tools import MORE_TOOL_DEFINITIONS, MoreToolsError, inspect_more_tools


class MoreToolsTests(unittest.TestCase):
    def test_inventory_is_deterministic_read_only_and_complete(self) -> None:
        first = inspect_more_tools()
        second = inspect_more_tools()
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["summary"]["tool_count"], 9)
        self.assertEqual(len(first["tools"]), len(MORE_TOOL_DEFINITIONS))
        self.assertFalse(first["landing_mutation_supported"])
        self.assertEqual(
            [category["id"] for category in first["categories"]],
            ["character", "system", "help"],
        )
        self.assertEqual(
            {item["tool"] for item in first["tools"]},
            {
                "advanced-character-operations",
                "voice-designer",
                "audio-preparer",
                "dataset-builder",
                "voice-training",
                "maintenance",
                "model-cache",
                "background-work",
                "help-center",
            },
        )
        self.assertTrue(all(item["availability"]["state"] == "available" for item in first["tools"]))
        self.assertTrue(all(item["fingerprint"] for item in first["tools"]))

    def test_exact_project_character_source_and_return_context_are_preserved(self) -> None:
        payload = inspect_more_tools(
            project_id="project_1",
            character_id="character_2",
            source="cast:character:character_2",
            return_route="#/cast?project=project_1&character=character_2",
        )
        self.assertEqual(payload["context"]["label"], "Selected character")
        for tool in payload["tools"]:
            route = tool["route"]
            self.assertEqual(route["destination"], "more")
            self.assertEqual(route["context"]["tool"], tool["tool"])
            self.assertEqual(route["context"]["project"], "project_1")
            self.assertEqual(route["context"]["character"], "character_2")
            self.assertEqual(route["context"]["source"], "cast:character:character_2")
            self.assertEqual(
                route["context"]["return"],
                "#/cast?project=project_1&character=character_2",
            )
            parsed = parse_qs(urlsplit(route["hash"].replace("#", "", 1)).query)
            self.assertEqual(parsed["tool"], [tool["tool"]])
            self.assertEqual(parsed["project"], ["project_1"])
            self.assertEqual(parsed["character"], ["character_2"])
            self.assertEqual(parsed["source"], ["cast:character:character_2"])
            self.assertEqual(
                parsed["return"],
                ["#/cast?project=project_1&character=character_2"],
            )
        character_tools = [
            item
            for item in payload["tools"]
            if item["context_scope"] == "character_optional"
        ]
        self.assertTrue(
            all(
                item["availability"]["message"]
                == "Opens for the selected character."
                for item in character_tools
            )
        )

    def test_project_context_does_not_invent_character_blockers(self) -> None:
        payload = inspect_more_tools(
            project_id="project_1",
            return_route="#/more?project=project_1",
        )
        self.assertEqual(payload["context"]["label"], "Current project")
        self.assertIsNone(payload["context"]["character_id"])
        for item in payload["tools"]:
            self.assertEqual(item["availability"]["state"], "available")
            self.assertNotIn("blocked", item["availability"]["message"].casefold())
            self.assertNotIn("character", item["route"]["context"])

    def test_global_context_remains_available(self) -> None:
        payload = inspect_more_tools(return_route="#/projects")
        self.assertEqual(payload["context"]["label"], "Global")
        for item in payload["tools"]:
            self.assertEqual(item["availability"]["state"], "available")
            self.assertEqual(item["route"]["context"]["return"], "#/projects")
            self.assertNotIn("project", item["route"]["context"])
            self.assertNotIn("character", item["route"]["context"])

    def test_character_without_project_fails_closed(self) -> None:
        with self.assertRaises(MoreToolsError) as caught:
            inspect_more_tools(character_id="character_2")
        self.assertEqual(caught.exception.code, "more_character_project_required")
        self.assertEqual(caught.exception.status_code, 422)

    def test_unsafe_context_values_are_rejected(self) -> None:
        for field, kwargs in (
            ("project_id", {"project_id": "project\nunsafe"}),
            ("character_id", {"project_id": "p", "character_id": "c\x00unsafe"}),
            ("source", {"source": "s" * 513}),
            ("return_route", {"return_route": "#/more\x7f"}),
        ):
            with self.subTest(field=field):
                with self.assertRaises(MoreToolsError) as caught:
                    inspect_more_tools(**kwargs)
                self.assertEqual(caught.exception.code, "more_context_invalid")
                self.assertEqual(caught.exception.context["field"], field)

    def test_public_contract_contains_no_internal_paths_or_mutation_endpoints(self) -> None:
        rendered = json.dumps(inspect_more_tools()).casefold()
        for forbidden in (
            "/users/",
            "voice_config.json",
            "annotated_script.json",
            "delete /api",
            "post /api",
            "put /api",
            "patch /api",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
