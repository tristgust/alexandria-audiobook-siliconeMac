from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


TOPIC = """---
schema_version: 1
slug: {slug}
title: {title}
summary: Bundled route guidance.
version: \"1.0\"
context_ids: {context_ids}
destinations: [\"projects\"]
related: {related}
---
# {title}

Use `safe-inline-code` as literal documentation text. {body}
"""


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HelpCenterRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for slug, title, related, contexts, body in (
            (
                "alpha",
                "Alpha",
                ["beta"],
                ["projects", "new-project"],
                "Deep route-search wording.",
            ),
            ("beta", "Beta", [], ["settings"], "Secondary guidance."),
        ):
            (self.root / f"{slug}.md").write_text(
                TOPIC.format(
                    slug=slug,
                    title=title,
                    related=json.dumps(related),
                    context_ids=json.dumps(contexts),
                    body=body,
                ),
                encoding="utf-8",
            )
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "bundle_version": "1.0",
                    "topics": [
                        {
                            "slug": slug,
                            "filename": f"{slug}.md",
                            "content_sha256": content_hash(
                                self.root / f"{slug}.md"
                            ),
                        }
                        for slug in ("alpha", "beta")
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.patcher = patch.object(
            app_module,
            "HELP_CENTER_DIR",
            str(self.root),
        )
        self.patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        self.patcher.stop()
        self.temporary.cleanup()

    def test_list_get_and_context_lookup_are_read_only_and_machine_readable(self) -> None:
        before = {
            path.name: path.read_bytes()
            for path in self.root.iterdir()
            if path.is_file()
        }
        inventory = self.client.get("/api/help")
        self.assertEqual(inventory.status_code, 200, inventory.text)
        payload = inventory.json()
        self.assertEqual(payload["summary"]["topic_count"], 2)
        self.assertEqual(payload["bundle_version"], "1.0")
        self.assertEqual(payload["context_index"]["new-project"], "alpha")
        topic = self.client.get("/api/help/alpha")
        self.assertEqual(topic.status_code, 200, topic.text)
        self.assertEqual(topic.json()["related_topics"][0]["slug"], "beta")
        self.assertIn("safe-inline-code", topic.json()["markdown"])
        contextual = self.client.get("/api/help/context/new-project")
        self.assertEqual(contextual.status_code, 200, contextual.text)
        self.assertEqual(contextual.json()["slug"], "alpha")
        after = {
            path.name: path.read_bytes()
            for path in self.root.iterdir()
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_full_content_search_missing_and_invalid_context_are_explicit(self) -> None:
        searched = self.client.get(
            "/api/help",
            params={"search": "deep wording"},
        )
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(
            [item["slug"] for item in searched.json()["topics"]],
            ["alpha"],
        )
        missing = self.client.get("/api/help/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "help_topic_not_found",
        )
        invalid = self.client.get("/api/help/INVALID")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "help_topic_slug_invalid",
        )
        missing_context = self.client.get(
            "/api/help/context/missing-context"
        )
        self.assertEqual(missing_context.status_code, 404)
        self.assertEqual(
            missing_context.json()["detail"]["code"],
            "help_context_not_found",
        )

    def test_routes_are_registered_once_with_static_context_before_dynamic_topic(self) -> None:
        routes = [
            (method, route.path)
            for route in app_module.app.routes
            for method in sorted(getattr(route, "methods", set()))
            if route.path.startswith("/api/help")
        ]
        self.assertEqual(
            routes,
            [
                ("GET", "/api/help"),
                ("GET", "/api/help/context/{context_id}"),
                ("GET", "/api/help/{slug}"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
