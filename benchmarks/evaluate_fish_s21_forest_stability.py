#!/usr/bin/env python3
"""Stress-test compact routed Fish preference forests across seeds and sizes."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


NUMERIC = ["speaker_similarity", "ser_target", "ser_margin", "quality"]
CATEGORICAL = ["style", "prompt_mode"]
PROMPT_ORDER = {
    "neutral": ["simple_tag", "untagged", "full_alexandria_tag", "rich_tag"],
    "grief": ["full_alexandria_tag", "rich_tag", "untagged", "simple_tag"],
    "sarcastic": ["rich_tag", "full_alexandria_tag", "untagged", "simple_tag"],
    "fear": ["full_alexandria_tag", "rich_tag", "simple_tag", "untagged"],
}


def vector(row: dict[str, Any]) -> list[Any]:
    return [row.get(key) for key in NUMERIC + CATEGORICAL]


def target(row: dict[str, Any]) -> int:
    return int(
        row["approve_for_comparison"] is True
        and row["spoken_text_matches_expected"] is True
    )


def strict(row: dict[str, Any]) -> int:
    return int(
        target(row)
        and row["requested_mode_is_clear"] is True
        and float(row["identity_1_to_5"] or 0) >= 4
        and float(row["naturalness_1_to_5"] or 0) >= 4
    )


def eligible(row: dict[str, Any]) -> bool:
    return bool(
        row["spoken_text_matches_expected"] is True
        and float(row["quality"] or 0) >= 0.65
        and (
            row["speaker_similarity_mode"] != "mlx_qwen"
            or float(row["speaker_similarity"] or 0) >= 0.78
        )
    )


def estimator(*, kind: str, trees: int, depth: int, seed: int) -> Pipeline:
    transform = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                list(range(len(NUMERIC))),
            ),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                list(range(len(NUMERIC), len(NUMERIC) + len(CATEGORICAL))),
            ),
        ]
    )
    return Pipeline(
        [
            ("features", transform),
            (
                "model",
                (
                    RandomForestClassifier(
                        n_estimators=trees,
                        max_depth=depth,
                        min_samples_leaf=3,
                        class_weight="balanced_subsample",
                        random_state=seed,
                    )
                    if kind == "random_forest"
                    else ExtraTreesClassifier(
                        n_estimators=trees,
                        max_depth=depth,
                        min_samples_leaf=3,
                        class_weight="balanced",
                        random_state=seed,
                    )
                ),
            ),
        ]
    )


def choose(
    candidates: list[dict[str, Any]],
    probabilities: list[float],
) -> dict[str, Any]:
    for prompt in PROMPT_ORDER[candidates[0]["style"]]:
        eligible_rows = [
            (row, probability)
            for row, probability in zip(candidates, probabilities)
            if row["prompt_mode"] == prompt and eligible(row)
        ]
        if eligible_rows:
            return max(eligible_rows, key=lambda item: item[1])[0]
    raise RuntimeError("No routed candidate")


def evaluate(
    rows: list[dict[str, Any]],
    *,
    kind: str,
    trees: int,
    depth: int,
    seed: int,
) -> dict[str, Any]:
    selections = []
    for held_out in sorted({row["identity"] for row in rows}):
        train = [row for row in rows if row["identity"] != held_out]
        test = [row for row in rows if row["identity"] == held_out]
        model = estimator(kind=kind, trees=trees, depth=depth, seed=seed)
        model.fit([vector(row) for row in train], [target(row) for row in train])
        probabilities = model.predict_proba([vector(row) for row in test])[:, 1]
        groups: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(test):
            groups[row["style"]].append(index)
        for style, indices in sorted(groups.items()):
            selected = choose(
                [test[index] for index in indices],
                [float(probabilities[index]) for index in indices],
            )
            selections.append(selected)
    return {
        "kind": kind,
        "trees": trees,
        "depth": depth,
        "seed": seed,
        "approval_rate": sum(target(row) for row in selections) / len(selections),
        "strict_rate": sum(strict(row) for row in selections) / len(selections),
        "mode_clear_rate": sum(
            row["requested_mode_is_clear"] for row in selections
        ) / len(selections),
        "identity_mean": float(
            np.mean([row["identity_1_to_5"] for row in selections])
        ),
        "delivery_mean": float(
            np.mean([row["delivery_1_to_5"] for row in selections])
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("features", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.features.read_text(encoding="utf-8"))[
        "calibration_rows"
    ]
    results = [
        evaluate(rows, kind=kind, trees=trees, depth=depth, seed=seed)
        for kind in ("random_forest", "extra_trees")
        for trees in (16, 32, 64, 128)
        for depth in (2, 3, 4)
        for seed in range(1, 21)
    ]
    grouped = []
    for kind in ("random_forest", "extra_trees"):
        for trees in (16, 32, 64, 128):
            for depth in (2, 3, 4):
                matches = [
                    row
                    for row in results
                    if row["kind"] == kind
                    and row["trees"] == trees
                    and row["depth"] == depth
                ]
                grouped.append(
                    {
                        "kind": kind,
                        "trees": trees,
                        "depth": depth,
                        "approval_mean": float(
                            np.mean([row["approval_rate"] for row in matches])
                        ),
                        "approval_min": min(row["approval_rate"] for row in matches),
                        "approval_max": max(row["approval_rate"] for row in matches),
                        "strict_mean": float(
                            np.mean([row["strict_rate"] for row in matches])
                        ),
                        "mode_clear_mean": float(
                            np.mean([row["mode_clear_rate"] for row in matches])
                        ),
                        "identity_mean": float(
                            np.mean([row["identity_mean"] for row in matches])
                        ),
                        "delivery_mean": float(
                            np.mean([row["delivery_mean"] for row in matches])
                        ),
                    }
                )
    grouped.sort(
        key=lambda row: (
            row["approval_mean"],
            row["approval_min"],
            row["strict_mean"],
            row["mode_clear_mean"],
        ),
        reverse=True,
    )
    report = {
        "schema_version": 1,
        "validation": "leave_one_identity_out_20_random_seeds",
        "feature_set": NUMERIC + CATEGORICAL,
        "grouped": grouped,
        "runs": results,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
