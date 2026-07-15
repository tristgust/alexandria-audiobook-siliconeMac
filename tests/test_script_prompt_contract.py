from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def load_versioned_prompt():
    raw_defaults = (
        REPO
        / "default_prompts.txt"
    ).read_text(encoding="utf-8")

    system_prompt, separator, user_prompt = (
        raw_defaults.partition(
            "---SEPARATOR---"
        )
    )

    if not separator:
        raise AssertionError(
            "Default prompt separator is missing."
        )

    return (
        system_prompt.strip(),
        user_prompt,
    )


class ScriptPromptContractTests(unittest.TestCase):
    def test_boundary_splitting_is_required(self):
        prompt, _ = load_versioned_prompt()

        self.assertIn(
            "QUOTATION-BOUNDARY SPLITTING",
            prompt,
        )

        self.assertIn(
            "Never merge character dialogue across "
            "intervening narrator text.",
            prompt,
        )

    def test_attribution_is_retained(self):
        prompt, _ = load_versioned_prompt()

        self.assertIn(
            "ATTRIBUTIONS ARE NARRATION AND "
            "MUST BE RETAINED",
            prompt,
        )

        self.assertIn(
            '→ NARRATOR: "said Marcus, unable to meet '
            'her gaze."',
            prompt,
        )

    def test_optional_pronoun_clarification_exists(self):
        prompt, _ = load_versioned_prompt()

        self.assertIn(
            "OPTIONAL ATTRIBUTION-SUBJECT CLARIFICATION",
            prompt,
        )

        self.assertIn(
            "Make this substitution only as needed "
            "for clarity, not automatically.",
            prompt,
        )

        self.assertIn(
            '"he said, pulling on his coat." '
            '→ "Marcus said, pulling on his coat."',
            prompt,
        )

    def test_default_is_to_preserve_pronoun(self):
        prompt, _ = load_versioned_prompt()

        self.assertIn(
            "Default to preserving the attribution's "
            "original subject exactly.",
            prompt,
        )

        self.assertIn(
            "If the speaker identity is uncertain, "
            "retain the original pronoun.",
            prompt,
        )

    def test_only_subject_pronoun_may_change(self):
        prompt, _ = load_versioned_prompt()

        self.assertIn(
            "Change only the subject pronoun.",
            prompt,
        )

        self.assertIn(
            "Preserve every other word, verb form, "
            "adverb, clause, punctuation mark, "
            "and their order.",
            prompt,
        )

    def test_inverted_attribution_stays_inverted(self):
        prompt, _ = load_versioned_prompt()

        self.assertIn(
            '"said Marcus, unable to meet her gaze." '
            '→ "said Marcus, unable to meet her gaze."',
            prompt,
        )

    def test_rewritten_attribution_is_forbidden(self):
        prompt, _ = load_versioned_prompt()

        self.assertIn(
            '"said Marcus, unable to meet her gaze." '
            '→ "Marcus was unable to meet her gaze."',
            prompt,
        )

        self.assertIn(
            "The wrong example deletes \"said\"",
            prompt,
        )

    def test_blanket_name_replacement_is_absent(self):
        prompt, _ = load_versioned_prompt()

        self.assertNotIn(
            "using the character's name "
            "(not a pronoun)",
            prompt,
        )

        self.assertNotIn(
            "Always replace attribution pronouns",
            prompt,
        )

    def test_user_prompt_template_is_preserved(self):
        _, user_prompt = load_versioned_prompt()

        self.assertEqual(
            user_prompt.count("{context}"),
            1,
        )

        self.assertEqual(
            user_prompt.count("{chunk}"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
