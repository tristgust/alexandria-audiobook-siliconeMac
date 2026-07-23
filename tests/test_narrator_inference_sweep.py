from __future__ import annotations

import unittest

from benchmarks.prepare_narrator_inference_sweep import choose_review_rows


class NarratorInferenceSweepTests(unittest.TestCase):
    def test_selection_covers_temperatures_and_repeats_most_reliable(self) -> None:
        rows = []
        fixtures = {
            0.5: [(101, True, 9.0), (102, True, 8.0), (103, True, 7.0)],
            0.7: [(201, True, 8.5), (202, False, 7.5), (203, False, 7.0)],
            0.9: [(301, False, 8.0), (302, False, 7.0), (303, False, 6.0)],
        }
        for temperature, candidates in fixtures.items():
            for seed, hard_pass, score in candidates:
                rows.append(
                    {
                        "style": "neutral",
                        "temperature": temperature,
                        "seed": seed,
                        "hard_pass": hard_pass,
                        "screening_score": score,
                    }
                )

        selected = choose_review_rows(rows)

        self.assertEqual(len(selected), 4)
        selected_pairs = {
            (row["temperature"], row["seed"]) for row in selected
        }
        self.assertIn((0.5, 101), selected_pairs)
        self.assertIn((0.7, 201), selected_pairs)
        self.assertIn((0.9, 301), selected_pairs)
        self.assertIn((0.5, 102), selected_pairs)


if __name__ == "__main__":
    unittest.main()
