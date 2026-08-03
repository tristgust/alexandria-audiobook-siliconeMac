#!/usr/bin/env python3
"""Cross-validate lightweight Fish take-ranking models against blind ratings."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


NUMERIC = [
    "clap_target",
    "clap_margin",
    "clap_probability",
    "acoustic",
    "quality",
    "ser_target",
    "ser_margin",
    "speaker_similarity",
    "prompt_prior",
]
CATEGORICAL = ["style", "prompt_mode"]
PROMPT_PRIORS = {
    "neutral": {
        "simple_tag": 1.0,
        "untagged": 0.75,
        "full_alexandria_tag": 0.55,
        "rich_tag": 0.45,
    },
    "grief": {
        "full_alexandria_tag": 1.0,
        "rich_tag": 0.75,
        "untagged": 0.55,
        "simple_tag": 0.4,
    },
    "sarcastic": {
        "rich_tag": 1.0,
        "full_alexandria_tag": 0.75,
        "untagged": 0.5,
        "simple_tag": 0.4,
    },
    "fear": {
        "full_alexandria_tag": 1.0,
        "rich_tag": 0.8,
        "simple_tag": 0.55,
        "untagged": 0.25,
    },
}


def row_vector(row: dict[str, Any]) -> list[Any]:
    return [row.get(key) for key in NUMERIC + CATEGORICAL]


def eligible(row: dict[str, Any]) -> bool:
    return bool(
        row.get("spoken_text_matches_expected") is True
        and float(row.get("quality") or 0.0) >= 0.65
        and (
            row.get("speaker_similarity_mode") != "mlx_qwen"
            or float(row.get("speaker_similarity") or 0.0) >= 0.78
        )
    )


def target(row: dict[str, Any]) -> int:
    return int(
        row.get("approve_for_comparison") is True
        and row.get("requested_mode_is_clear") is True
        and row.get("spoken_text_matches_expected") is True
        and float(row.get("identity_1_to_5") or 0) >= 4
        and float(row.get("naturalness_1_to_5") or 0) >= 4
    )


def approval_target(row: dict[str, Any]) -> int:
    return int(
        row.get("approve_for_comparison") is True
        and row.get("spoken_text_matches_expected") is True
    )


def pipeline(model: Any) -> Pipeline:
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
    return Pipeline([("features", transform), ("model", model)])


def models() -> dict[str, Pipeline]:
    return {
        "logistic_l2": pipeline(
            LogisticRegression(
                C=0.5,
                class_weight="balanced",
                max_iter=5000,
                random_state=13,
            )
        ),
        "logistic_sparse": pipeline(
            LogisticRegression(
                C=0.2,
                class_weight="balanced",
                penalty="l1",
                solver="liblinear",
                max_iter=5000,
                random_state=13,
            )
        ),
        "random_forest": pipeline(
            RandomForestClassifier(
                n_estimators=500,
                max_depth=4,
                min_samples_leaf=3,
                class_weight="balanced_subsample",
                random_state=13,
            )
        ),
        "extra_trees": pipeline(
            ExtraTreesClassifier(
                n_estimators=500,
                max_depth=4,
                min_samples_leaf=3,
                class_weight="balanced",
                random_state=13,
            )
        ),
    }


def choose(
    candidates: list[dict[str, Any]],
    probabilities: list[float],
    *,
    routed: bool,
) -> tuple[dict[str, Any], float]:
    pairs = [
        (row, probability)
        for row, probability in zip(candidates, probabilities)
        if eligible(row)
    ]
    if not pairs:
        raise RuntimeError("No eligible candidates")
    if routed:
        prompt_order = sorted(
            {row["prompt_mode"] for row, _ in pairs},
            key=lambda prompt: PROMPT_PRIORS[pairs[0][0]["style"]].get(
                prompt,
                0.0,
            ),
            reverse=True,
        )
        for prompt in prompt_order:
            prompt_pairs = [
                item for item in pairs if item[0]["prompt_mode"] == prompt
            ]
            if prompt_pairs:
                return max(prompt_pairs, key=lambda item: item[1])
    return max(pairs, key=lambda item: item[1])


def fold_metrics(
    train: list[dict[str, Any]],
    test: list[dict[str, Any]],
    *,
    target_fn,
    estimator: Pipeline,
    routed: bool,
) -> dict[str, Any]:
    train_x = [row_vector(row) for row in train]
    train_y = [target_fn(row) for row in train]
    estimator.fit(train_x, train_y)
    test_x = [row_vector(row) for row in test]
    probabilities = estimator.predict_proba(test_x)[:, 1].tolist()
    test_y = [target_fn(row) for row in test]
    auc = (
        float(roc_auc_score(test_y, probabilities))
        if len(set(test_y)) > 1
        else None
    )
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(test):
        groups[(row["identity"], row["style"])].append(index)
    selected = []
    for key, indices in sorted(groups.items()):
        candidates = [test[index] for index in indices]
        candidate_probabilities = [probabilities[index] for index in indices]
        row, probability = choose(
            candidates,
            candidate_probabilities,
            routed=routed,
        )
        selected.append(
            {
                "identity": key[0],
                "style": key[1],
                "sample_id": row["sample_id"],
                "prompt_mode": row["prompt_mode"],
                "repeat": row.get("repeat"),
                "probability": float(probability),
                "target": target_fn(row),
                "approve": approval_target(row),
                "mode_clear": row["requested_mode_is_clear"],
                "identity_rating": row["identity_1_to_5"],
                "delivery_rating": row["delivery_1_to_5"],
            }
        )
    return {
        "auc": auc,
        "selection_count": len(selected),
        "strict_success_rate": sum(item["target"] for item in selected)
        / len(selected),
        "approval_rate": sum(item["approve"] for item in selected)
        / len(selected),
        "mode_clear_rate": sum(item["mode_clear"] for item in selected)
        / len(selected),
        "identity_mean": sum(
            float(item["identity_rating"]) for item in selected
        ) / len(selected),
        "delivery_mean": sum(
            float(item["delivery_rating"]) for item in selected
        ) / len(selected),
        "selections": selected,
    }


def cross_validate(
    rows: list[dict[str, Any]],
    *,
    target_name: str,
    target_fn,
    routed: bool,
) -> list[dict[str, Any]]:
    results = []
    identities = sorted({row["identity"] for row in rows})
    for model_name, estimator in models().items():
        folds = []
        for held_out in identities:
            train = [row for row in rows if row["identity"] != held_out]
            test = [row for row in rows if row["identity"] == held_out]
            fold = fold_metrics(
                train,
                test,
                target_fn=target_fn,
                estimator=estimator,
                routed=routed,
            )
            fold["held_out_identity"] = held_out
            folds.append(fold)
        selection_count = sum(fold["selection_count"] for fold in folds)
        results.append(
            {
                "model": model_name,
                "target": target_name,
                "routed": routed,
                "mean_auc": float(
                    np.mean(
                        [fold["auc"] for fold in folds if fold["auc"] is not None]
                    )
                ),
                "strict_success_rate": sum(
                    fold["strict_success_rate"] * fold["selection_count"]
                    for fold in folds
                ) / selection_count,
                "approval_rate": sum(
                    fold["approval_rate"] * fold["selection_count"]
                    for fold in folds
                ) / selection_count,
                "mode_clear_rate": sum(
                    fold["mode_clear_rate"] * fold["selection_count"]
                    for fold in folds
                ) / selection_count,
                "identity_mean": sum(
                    fold["identity_mean"] * fold["selection_count"]
                    for fold in folds
                ) / selection_count,
                "delivery_mean": sum(
                    fold["delivery_mean"] * fold["selection_count"]
                    for fold in folds
                ) / selection_count,
                "folds": folds,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("features", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.features.read_text(encoding="utf-8"))
    rows = list(payload["calibration_rows"])
    results = []
    for target_name, target_fn in (
        ("strict", target),
        ("approval", approval_target),
    ):
        for routed in (False, True):
            results.extend(
                cross_validate(
                    rows,
                    target_name=target_name,
                    target_fn=target_fn,
                    routed=routed,
                )
            )
    results.sort(
        key=lambda item: (
            item["strict_success_rate"],
            item["approval_rate"],
            item["mode_clear_rate"],
            item["delivery_mean"],
        ),
        reverse=True,
    )
    report = {
        "schema_version": 1,
        "validation": "leave_one_identity_out",
        "sample_count": len(rows),
        "results": results,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
