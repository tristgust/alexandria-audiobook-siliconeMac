from __future__ import annotations

import unittest

from audio_artifacts import audio_binding_fingerprint
from fish_cloud_tts import build_prompt_route
from fish_inline_cues import (
    FishInlineCueError,
    compile_inline_text,
    strip_inline_tags,
    text_sha256,
)


class FishInlineCueTests(unittest.TestCase):
    def plan(self, text: str, cues: list[dict]) -> dict:
        return {
            "schema_version": 1,
            "text_sha256": text_sha256(text),
            "cues": cues,
        }

    def test_places_midline_tag_immediately_before_exact_phrase(self) -> None:
        text = "I didn't want to go inside."
        rendered, normalized = compile_inline_text(
            text,
            self.plan(
                text,
                [
                    {
                        "anchor": "before_phrase",
                        "phrase": "inside",
                        "occurrence": 1,
                        "tag": "whispering",
                        "kind": "delivery",
                    }
                ],
            ),
        )
        self.assertEqual(rendered, "I didn't want to go [whispering] inside.")
        self.assertEqual(strip_inline_tags(rendered).split(), text.split())
        self.assertEqual(normalized.cues[0].tag, "whispering")

    def test_supports_multiple_local_shifts_and_explicit_reset(self) -> None:
        text = "I was calm, but then I understood."
        rendered, _ = compile_inline_text(
            text,
            self.plan(
                text,
                [
                    {"anchor": "start", "tag": "soft voice", "kind": "delivery"},
                    {
                        "anchor": "before_phrase",
                        "phrase": "but then",
                        "occurrence": 1,
                        "tag": "sudden alarm",
                        "kind": "delivery",
                    },
                    {
                        "anchor": "before_phrase",
                        "phrase": "I understood",
                        "occurrence": 1,
                        "tag": "steady voice",
                        "kind": "reset",
                    },
                ],
            ),
        )
        self.assertEqual(
            rendered,
            "[soft voice] I was calm, [sudden alarm] but then [steady voice] I understood.",
        )

    def test_allows_only_well_tested_reaction_tags_at_line_end(self) -> None:
        text = "That was the third time this week."
        rendered, _ = compile_inline_text(
            text,
            self.plan(
                text,
                [{"anchor": "end", "tag": "sigh", "kind": "reaction"}],
            ),
        )
        self.assertEqual(rendered, "That was the third time this week. [sigh]")
        with self.assertRaisesRegex(FishInlineCueError, "well-tested reaction"):
            compile_inline_text(
                text,
                self.plan(
                    text,
                    [
                        {
                            "anchor": "end",
                            "tag": "be emotionally nuanced",
                            "kind": "reaction",
                        }
                    ],
                ),
            )

    def test_rejects_plan_when_canonical_text_changed(self) -> None:
        original = "Please go inside."
        plan = self.plan(
            original,
            [
                {
                    "anchor": "before_phrase",
                    "phrase": "inside",
                    "occurrence": 1,
                    "tag": "whispering",
                    "kind": "delivery",
                }
            ],
        )
        with self.assertRaisesRegex(FishInlineCueError, "no longer matches"):
            compile_inline_text("Please go outside.", plan)

    def test_fish_prompt_route_prefers_structured_inline_plan(self) -> None:
        text = "I thought I was ready. I wasn't."
        plan = self.plan(
            text,
            [
                {
                    "anchor": "before_phrase",
                    "phrase": "I wasn't",
                    "occurrence": 1,
                    "tag": "voice breaking",
                    "kind": "delivery",
                }
            ],
        )
        route = build_prompt_route(
            text,
            "Restrained at first, then emotionally breaking.",
            render_plan=plan,
        )
        self.assertEqual(route.variants[0].key, "structured_inline")
        self.assertEqual(
            route.variants[0].text,
            "I thought I was ready. [voice breaking] I wasn't.",
        )

    def test_render_plan_is_part_of_audio_binding(self) -> None:
        text = "You need to stop."
        chunk = {"speaker": "DOCTOR", "text": text, "instruct": "Firmly."}
        voice_config = {"DOCTOR": {"type": "clone", "ref_audio": "a.wav", "ref_text": "a"}}
        baseline = audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
        )
        explicit_none = audio_binding_fingerprint(
            chunk={**chunk, "fish_render_plan": None},
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
        )
        self.assertEqual(baseline, explicit_none)
        planned = audio_binding_fingerprint(
            chunk={
                **chunk,
                "fish_render_plan": self.plan(
                    text,
                    [
                        {
                            "anchor": "before_phrase",
                            "phrase": "stop",
                            "occurrence": 1,
                            "tag": "emphasis",
                            "kind": "delivery",
                        }
                    ],
                ),
            },
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
        )
        self.assertNotEqual(baseline, planned)


if __name__ == "__main__":
    unittest.main()
