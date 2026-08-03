#!/usr/bin/env python3
"""Measure which Fish preference features survive leave-one-voice-out testing."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


RAW_AUDIO = [
    "duration_seconds",
    "words_per_second",
    "rms_mean",
    "rms_cv",
    "pitch_median_hz",
    "pitch_cv",
    "spectral_centroid_hz",
    "silence_ratio",
    "clipping_ratio",
]

FEATURE_SETS = {
    "prompt_only": [],
    "identity_quality": ["speaker_similarity", "quality"],
    "identity_acoustic_quality": [
        "speaker_similarity",
        "acoustic",
        "quality",
    ],
    "ser_identity_quality": [
        "speaker_similarity",
        "ser_target",
        "ser_margin",
        "quality",
    ],
    "clap_identity_quality": [
        "speaker_similarity",
        "clap_target",
        "clap_margin",
        "clap_probability",
        "quality",
    ],
    "ser_clap_identity_quality": [
        "speaker_similarity",
        "ser_target",
        "ser_margin",
        "clap_target",
        "clap_margin",
        "clap_probability",
        "quality",
    ],
    "local_signals": [
        "speaker_similarity",
        "acoustic",
        "quality",
    ],
    "raw_audio": RAW_AUDIO,
    "raw_audio_identity_quality": [
        "speaker_similarity",
        "quality",
        *RAW_AUDIO,
    ],
    "raw_audio_identity_acoustic_quality": [
        "speaker_similarity",
        "acoustic",
        "quality",
        *RAW_AUDIO,
    ],
    "all_signals": [
        "speaker_similarity",
        "acoustic",
        "quality",
        "ser_target",
        "ser_margin",
        "clap_target",
        "clap_margin",
        "clap_probability",
    ],
}
CATEGORICAL = ["style", "prompt_mode"]
PROMPT_PRIORS = {
    "neutral": ["simple_tag", "untagged", "full_alexandria_tag", "rich_tag"],
    "grief": ["full_alexandria_tag", "rich_tag", "untagged", "simple_tag"],
    "sarcastic": ["rich_tag", "full_alexandria_tag", "untagged", "simple_tag"],
    "fear": ["full_alexandria_tag", "rich_tag", "simple_tag", "untagged"],
}


def strict_target(row: dict[str, Any]) -> int:
    return int(
        row["approve_for_comparison"] is True
        and row["requested_mode_is_clear"] is True
        and row["spoken_text_matches_expected"] is True
        and float(row["identity_1_to_5"] or 0) >= 4
        and float(row["naturalness_1_to_5"] or 0) >= 4
    )


def approval_target(row: dict[str, Any]) -> int:
    return int(
        row["approve_for_comparison"] is True
        and row["spoken_text_matches_expected"] is True
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


def vector(row: dict[str, Any], numeric: list[str]) -> list[Any]:
    return [row.get(key) for key in numeric + CATEGORICAL]


def estimator(numeric: list[str]) -> Pipeline:
    transformers = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                list(range(len(numeric))),
            )
        )
    transformers.append(
        (
            "categorical",
            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
            list(range(len(numeric), len(numeric) + len(CATEGORICAL))),
        )
    )
    return Pipeline(
        [
            ("features", ColumnTransformer(transformers)),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=700,
                    max_depth=4,
                    min_samples_leaf=3,
                    class_weight="balanced_subsample",
                    random_state=13,
                ),
            ),
        ]
    )


def select_group(
    rows: list[dict[str, Any]],
    probabilities: list[float],
) -> dict[str, Any]:
    for prompt in PROMPT_PRIORS[rows[0]["style"]]:
        candidates = [
            (row, probability)
            for row, probability in zip(rows, probabilities)
            if row["prompt_mode"] == prompt and eligible(row)
        ]
        if candidates:
            return max(candidates, key=lambda item: item[1])[0]
    raise RuntimeError("No eligible routed candidate")


def evaluate(
    rows: list[dict[str, Any]],
    *,
    feature_name: str,
    numeric: list[str],
    target_name: str,
    target_fn,
) -> dict[str, Any]:
    selections = []
    identities = sorted({row["identity"] for row in rows})
    for held_out in identities:
        train = [row for row in rows if row["identity"] != held_out]
        test = [row for row in rows if row["identity"] == held_out]
        model = estimator(numeric)
        model.fit(
            [vector(row, numeric) for row in train],
            [target_fn(row) for row in train],
        )
        probabilities = model.predict_proba(
            [vector(row, numeric) for row in test]
        )[:, 1].tolist()
        groups: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(test):
            groups[row["style"]].append(index)
        for style, indices in sorted(groups.items()):
            selected = select_group(
                [test[index] for index in indices],
                [probabilities[index] for index in indices],
            )
            selections.append(
                {
                    "held_out_identity": held_out,
                    "style": style,
                    "sample_id": selected["sample_id"],
                    "prompt_mode": selected["prompt_mode"],
                    "repeat": selected["repeat"],
                    "strict": strict_target(selected),
                    "approval": approval_target(selected),
                    "mode_clear": selected["requested_mode_is_clear"],
                    "identity_rating": selected["identity_1_to_5"],
                    "delivery_rating": selected["delivery_1_to_5"],
                }
            )
    return {
        "feature_set": feature_name,
        "numeric_features": numeric,
        "target": target_name,
        "selection_count": len(selections),
        "strict_success_rate": sum(row["strict"] for row in selections)
        / len(selections),
        "approval_rate": sum(row["approval"] for row in selections)
        / len(selections),
        "mode_clear_rate": sum(row["mode_clear"] for row in selections)
        / len(selections),
        "identity_mean": float(
            np.mean([row["identity_rating"] for row in selections])
        ),
        "delivery_mean": float(
            np.mean([row["delivery_rating"] for row in selections])
        ),
        "selections": selections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("features", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.features.read_text(encoding="utf-8"))[
        "calibration_rows"
    ]
    results = []
    for feature_name, numeric in FEATURE_SETS.items():
        for target_name, target_fn in (
            ("approval", approval_target),
            ("strict", strict_target),
        ):
            results.append(
                evaluate(
                    rows,
                    feature_name=feature_name,
                    numeric=numeric,
                    target_name=target_name,
                    target_fn=target_fn,
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
        "validation": "leave_one_identity_out_routed_repeat_selection",
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
