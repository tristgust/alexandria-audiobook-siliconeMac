from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(
    encoding="utf-8"
)


class InterfaceHierarchyContractTests(unittest.TestCase):
    def test_page_headers_use_one_title_and_trailing_action_grid(self) -> None:
        for phrase in (
            "grid-template-columns: minmax(0, 1fr) auto;",
            ".workflow-surface-header > :first-child",
            ".workspace-header > :first-child",
            ".page-header-actions {",
            "grid-column: 2;",
            "justify-self: end;",
        ):
            self.assertIn(phrase, HTML)
        characters_start = HTML.index('id="characters-tab"')
        characters_end = HTML.index(
            '<!-- Speaker Management Tool -->',
            characters_start,
        )
        characters = HTML[characters_start:characters_end]
        self.assertIn('class="page-header-actions"', characters)
        speaker_start = HTML.index('id="speaker-management-tab"')
        speaker_end = HTML.index(
            '<!-- Legacy voice-profile routes',
            speaker_start,
        )
        speaker = HTML[speaker_start:speaker_end]
        self.assertIn('class="page-header-actions"', speaker)

    def test_secondary_surfaces_have_distinct_but_readable_heading_level(self) -> None:
        self.assertIn("font-size: 1.3rem;", HTML)
        self.assertIn("font-size: clamp(1.55rem, 2vw, 1.9rem);", HTML)
        self.assertIn("font-size: 0.82rem;\n            line-height: 1.5;", HTML)

    def test_disclosure_status_and_chevron_have_explicit_trailing_columns(self) -> None:
        for phrase in (
            "grid-template-columns: minmax(0, 1fr) auto auto;",
            ".utility-disclosure > summary > .stage-page-state",
            "grid-column: 2;",
            ".utility-disclosure > summary::after",
            "grid-column: 3;",
        ):
            self.assertIn(phrase, HTML)

    def test_narrow_headers_stack_without_centering_statuses(self) -> None:
        narrow_start = HTML.index("@media (max-width: 620px) {")
        narrow_end = HTML.index(
            "@media (hover: hover)",
            narrow_start,
        )
        narrow = HTML[narrow_start:narrow_end]
        self.assertIn("grid-template-columns: minmax(0, 1fr);", narrow)
        self.assertIn("grid-column: 1;", narrow)
        self.assertIn("justify-self: start;", narrow)
        self.assertIn("justify-content: flex-start;", narrow)


if __name__ == "__main__":
    unittest.main()
