from __future__ import annotations

import unittest

from legacy_script_repair import repair_legacy_curly_apostrophe_script


class LegacyScriptRepairTests(unittest.TestCase):
    def test_repairs_curly_apostrophe_split_and_removes_watermark(self) -> None:
        raw_source = "OceanofPDF.com\n\n‘It’s dangerous,’ he said."
        legacy_entries = [
            {
                "speaker": "NARRATOR",
                "text": "OceanofPDF.com",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "DOCTOR",
                "text": "It",
                "instruct": "Quiet warning.",
            },
            {
                "speaker": "NARRATOR",
                "text": "s dangerous,’ he said.",
                "instruct": "Neutral narration.",
            },
        ]

        repaired, summary = repair_legacy_curly_apostrophe_script(
            raw_source=raw_source,
            entries=legacy_entries,
        )

        self.assertEqual(
            repaired,
            [
                {
                    "speaker": "DOCTOR",
                    "text": "It’s dangerous,",
                    "instruct": "Quiet warning.",
                },
                {
                    "speaker": "NARRATOR",
                    "text": "he said.",
                    "instruct": "Neutral narration.",
                },
            ],
        )
        self.assertEqual(summary["watermark_count"], 1)
        self.assertEqual(summary["original_entry_count"], 3)
        self.assertEqual(summary["repaired_entry_count"], 2)

    def test_source_start_marker_trims_front_matter_but_keeps_story_opening(self) -> None:
        raw_source = (
            "Cover quotation.\n\nPublication data.\n\n"
            "A cold wind blew.\n‘It’s dangerous,’ he said."
        )
        current_entries = [
            {
                "speaker": "NARRATOR",
                "text": "Cover quotation.\n\nPublication data.\n\nA cold wind blew.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "DOCTOR",
                "text": "It’s dangerous,",
                "instruct": "Quiet warning.",
            },
            {
                "speaker": "NARRATOR",
                "text": "he said.",
                "instruct": "Neutral narration.",
            },
        ]

        repaired, summary = repair_legacy_curly_apostrophe_script(
            raw_source=raw_source,
            entries=current_entries,
            start_marker="A cold wind blew.",
        )

        self.assertEqual(repaired[0]["text"], "A cold wind blew.")
        self.assertEqual(repaired[1]["text"], "It’s dangerous,")
        self.assertNotIn("Cover quotation", " ".join(item["text"] for item in repaired))
        self.assertGreater(summary["trimmed_character_count"], 0)

    def test_missing_close_epigraph_does_not_consume_story_dialogue(self) -> None:
        raw_source = (
            "‘We will sing . . .\n"
            "Emilio Marinetti, The Manifesto of Futurism Prologue\n"
            "A cold wind blew.\n"
            "‘I’m dying,’ he hissed."
        )
        legacy_entries = [
            {
                "speaker": "UNRESOLVED SPEAKER",
                "text": "We will sing . . .\nEmilio Marinetti, The Manifesto of Futurism Prologue\nA cold wind blew.\n‘I",
                "instruct": "Quoted delivery.",
            },
            {
                "speaker": "NARRATOR",
                "text": "m dying,’ he hissed.",
                "instruct": "Neutral narration.",
            },
        ]

        repaired, _ = repair_legacy_curly_apostrophe_script(
            raw_source=raw_source,
            entries=legacy_entries,
        )

        self.assertEqual(
            [entry["speaker"] for entry in repaired],
            ["NARRATOR", "UNRESOLVED SPEAKER", "NARRATOR"],
        )
        self.assertEqual(repaired[1]["text"], "I’m dying,")
        self.assertEqual(repaired[2]["text"], "he hissed.")


if __name__ == "__main__":
    unittest.main()
