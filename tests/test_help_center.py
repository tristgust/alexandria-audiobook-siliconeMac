from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from help_center import (
    HelpCenterError,
    get_help_topic,
    get_help_topic_by_context,
    inspect_help_center,
)


TOPIC = """---
schema_version: 1
slug: {slug}
title: {title}
summary: A safe bundled topic.
version: \"1.0\"
context_ids: {context_ids}
destinations: [\"projects\"]
related: {related}
---
# {title}

{body}
"""


def content_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HelpCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.write_topic(
            "alpha",
            "Alpha",
            related=["beta"],
            context_ids=["projects", "new-project"],
            body="Body text with résumé guidance and deep search wording.",
        )
        self.write_topic(
            "beta",
            "Beta",
            related=[],
            context_ids=["settings"],
            body="Secondary body text.",
        )
        self.write_manifest(["alpha", "beta"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_topic(
        self,
        slug: str,
        title: str,
        *,
        related: list[str],
        context_ids: list[str],
        body: str,
        path: Path | None = None,
    ) -> Path:
        target = path or (self.root / f"{slug}.md")
        target.write_text(
            TOPIC.format(
                slug=slug,
                title=title,
                related=json.dumps(related),
                context_ids=json.dumps(context_ids),
                body=body,
            ),
            encoding="utf-8",
        )
        return target

    def write_manifest(self, slugs: list[str]) -> None:
        manifest = {
            "schema_version": 1,
            "bundle_version": "1.0",
            "topics": [
                {
                    "slug": slug,
                    "filename": f"{slug}.md",
                    "content_sha256": content_sha256(
                        self.root / f"{slug}.md"
                    ),
                }
                for slug in slugs
            ],
        }
        (self.root / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

    def test_inventory_and_topic_are_manifest_versioned_and_related(self) -> None:
        inventory = inspect_help_center(help_dir=self.root)
        self.assertEqual(
            inventory["summary"],
            {"topic_count": 2, "visible_count": 2},
        )
        self.assertEqual(inventory["bundle_version"], "1.0")
        self.assertEqual(len(inventory["manifest_sha256"]), 64)
        self.assertEqual(
            [item["slug"] for item in inventory["topics"]],
            ["alpha", "beta"],
        )
        self.assertEqual(inventory["context_index"]["new-project"], "alpha")
        topic = get_help_topic(help_dir=self.root, slug="alpha")
        self.assertEqual(topic["schema_version"], 1)
        self.assertEqual(topic["bundle_version"], "1.0")
        self.assertEqual(topic["related_topics"][0]["slug"], "beta")
        self.assertEqual(len(topic["content_sha256"]), 64)

    def test_context_lookup_uses_stable_context_id(self) -> None:
        topic = get_help_topic_by_context(
            help_dir=self.root,
            context_id="new-project",
        )
        self.assertEqual(topic["slug"], "alpha")
        with self.assertRaises(HelpCenterError) as caught:
            get_help_topic_by_context(
                help_dir=self.root,
                context_id="missing-context",
            )
        self.assertEqual(caught.exception.code, "help_context_not_found")

    def test_search_is_local_unicode_and_full_content_based(self) -> None:
        for query in ("alpha", "résumé", "deep wording"):
            with self.subTest(query=query):
                inventory = inspect_help_center(
                    help_dir=self.root,
                    search=query,
                )
                self.assertEqual(
                    [item["slug"] for item in inventory["topics"]],
                    ["alpha"],
                )
        self.assertEqual(
            inspect_help_center(
                help_dir=self.root,
                search="not present",
            )["topics"],
            [],
        )

    def test_traversal_and_invalid_slugs_fail_closed(self) -> None:
        with self.assertRaises(HelpCenterError) as caught:
            get_help_topic(help_dir=self.root, slug="../alpha")
        self.assertEqual(caught.exception.code, "help_topic_slug_invalid")

    def test_manifest_must_match_exact_bundled_inventory(self) -> None:
        self.write_topic(
            "extra",
            "Extra",
            related=[],
            context_ids=["extra"],
            body="Unlisted topic.",
        )
        with self.assertRaises(HelpCenterError) as caught:
            inspect_help_center(help_dir=self.root)
        self.assertEqual(
            caught.exception.code,
            "help_manifest_inventory_mismatch",
        )
        self.assertEqual(caught.exception.context["unlisted"], ["extra.md"])

    def test_manifest_hash_detects_unreviewed_topic_change(self) -> None:
        alpha = self.root / "alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8") + "\nChanged after review.\n",
            encoding="utf-8",
        )
        with self.assertRaises(HelpCenterError) as caught:
            inspect_help_center(help_dir=self.root)
        self.assertEqual(caught.exception.code, "help_manifest_content_mismatch")

    def test_missing_related_topic_is_rejected(self) -> None:
        self.write_topic(
            "alpha",
            "Alpha",
            related=["missing"],
            context_ids=["projects", "new-project"],
            body="Body text.",
        )
        self.write_manifest(["alpha", "beta"])
        with self.assertRaises(HelpCenterError) as caught:
            inspect_help_center(help_dir=self.root)
        self.assertEqual(caught.exception.code, "help_topic_related_missing")

    def test_duplicate_context_id_is_rejected(self) -> None:
        self.write_topic(
            "beta",
            "Beta",
            related=[],
            context_ids=["projects"],
            body="Secondary body text.",
        )
        self.write_manifest(["alpha", "beta"])
        with self.assertRaises(HelpCenterError) as caught:
            inspect_help_center(help_dir=self.root)
        self.assertEqual(caught.exception.code, "help_topic_context_duplicate")

    def test_raw_html_and_unsafe_controls_are_rejected(self) -> None:
        for body, expected in (
            ("<script>alert(1)</script>", "help_topic_html_forbidden"),
            ("Unsafe\u0000text", "help_topic_body_invalid"),
        ):
            with self.subTest(expected=expected):
                self.write_topic(
                    "alpha",
                    "Alpha",
                    related=["beta"],
                    context_ids=["projects", "new-project"],
                    body=body,
                )
                self.write_manifest(["alpha", "beta"])
                with self.assertRaises(HelpCenterError) as caught:
                    inspect_help_center(help_dir=self.root)
                self.assertEqual(caught.exception.code, expected)

    def test_symlink_topic_is_rejected(self) -> None:
        outside = Path(self.temporary.name).parent / "linked-help-topic.md"
        try:
            self.write_topic(
                "linked",
                "Linked",
                related=[],
                context_ids=["linked"],
                body="Linked body.",
                path=outside,
            )
            (self.root / "linked.md").symlink_to(outside)
            manifest = json.loads(
                (self.root / "manifest.json").read_text(encoding="utf-8")
            )
            manifest["topics"].append(
                {
                    "slug": "linked",
                    "filename": "linked.md",
                    "content_sha256": content_sha256(outside),
                }
            )
            (self.root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            with self.assertRaises(HelpCenterError) as caught:
                inspect_help_center(help_dir=self.root)
            self.assertEqual(caught.exception.code, "help_topic_file_unsafe")
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
