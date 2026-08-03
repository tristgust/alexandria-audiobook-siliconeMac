#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "benchmarks" / "b18_t04_review_assets"
DEFAULT_CANDIDATES = ROOT / "training_sidecar_runtime" / "b18_t04_candidate_round"
DEFAULT_OUTPUT = ROOT / ".omo" / "evidence" / "b18-t04-fixed-corpus-blind-round-20260803"
SEED = 20260803
TEXTS = {
    "neutral": "The corridor was quiet, but the silence felt deliberate.",
    "dread": "The door opened, and I knew I was not alone.",
}


class BlindReviewError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_source(root: Path, sample_id: str) -> Path:
    matches = list(root.rglob(f"{sample_id}.wav"))
    if len(matches) != 1:
        raise BlindReviewError(
            f"Expected exactly one WAV for {sample_id!r}; found {len(matches)}."
        )
    return matches[0].resolve()


def package_round(
    *,
    candidate_root: str | Path = DEFAULT_CANDIDATES,
    output_root: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    candidates = Path(candidate_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    objective_path = candidates / "objective_summary.json"
    objective = json.loads(objective_path.read_text(encoding="utf-8"))
    objective_rows = {row["sample_id"]: row for row in objective["rows"]}

    planned = {
        "neutral": [
            "qwen_controlled_identity_neutral",
            "moss_local_neutral",
            "fish_s2_pro_local_neutral",
            "fish_s21_pro_free_neutral",
        ],
        "dread": [
            "qwen_controlled_identity_dread",
            "qwen_prompt_bank_dread",
            "moss_nano_dread",
            "moss_local_dread",
            "fish_s2_pro_local_dread",
            "fish_s21_pro_free_dread",
        ],
    }
    exclusions = [
        {
            "sample_id": "moss_nano_neutral",
            "reason": "word_error_rate_exceeds_0.08",
            "word_error_rate": objective_rows["moss_nano_neutral"]["word_error_rate"],
        }
    ]
    trained = (
        ROOT
        / "training_sidecar_runtime"
        / "b18_t03_authorized_round"
        / "held_out"
        / "transcription_evaluation.json"
    )
    if trained.is_file():
        measurements = json.loads(trained.read_text(encoding="utf-8"))["measurements"]
        for sample_id, measurement in sorted(measurements.items()):
            exclusions.append(
                {
                    "sample_id": sample_id,
                    "reason": "trained_adapter_authored_text_failure",
                    "word_error_rate": measurement.get("word_error_rate"),
                    "transcript": measurement.get("transcript"),
                }
            )
    exclusions.append(
        {
            "sample_id": "tada_1b",
            "reason": "runtime_blocked_by_upstream_layout_and_gated_meta_tokenizer",
        }
    )

    if output.exists():
        shutil.rmtree(output)
    review = output / "review"
    audio_root = review / "audio"
    reference_root = review / "reference"
    answer_root = output / "answer-keys"
    audio_root.mkdir(parents=True)
    reference_root.mkdir(parents=True)
    answer_root.mkdir(parents=True)
    for asset in ("index.html", "app.js", "styles.css"):
        shutil.copy2(ASSETS / asset, review / asset)

    reference = (
        ROOT
        / "training_sidecar_runtime"
        / "b18_t03_reviewed_narrator_dataset"
        / "audio"
        / "000_narrator_demo_warm_nostalgia.wav"
    )
    shutil.copy2(reference, reference_root / "narrator_reference.wav")

    rng = random.Random(SEED)
    public_samples: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    counter = 0
    for lane in ("neutral", "dread"):
        lane_ids = list(planned[lane])
        rng.shuffle(lane_ids)
        for sample_id in lane_ids:
            counter += 1
            row = objective_rows[sample_id]
            if row["word_error_rate"] > 0.08:
                raise BlindReviewError(f"Ineligible sample entered review: {sample_id}")
            source = _candidate_source(candidates, sample_id)
            blind_id = f"N{counter:02d}" if lane == "neutral" else f"D{counter:02d}"
            target = audio_root / f"{blind_id}.wav"
            shutil.copy2(source, target)
            public_samples.append(
                {
                    "sample_id": blind_id,
                    "lane": lane,
                    "expected_speaker": "Narrator",
                    "expected_text": TEXTS[lane],
                    "audio": f"audio/{target.name}",
                    "reference_audio": "reference/narrator_reference.wav",
                }
            )
            answers.append(
                {
                    "sample_id": blind_id,
                    "candidate_id": sample_id,
                    "source_path": str(source),
                    "source_sha256": sha256_file(source),
                    "objective": row,
                }
            )

    public = {
        "schema_version": 1,
        "round_id": "b18_t04_fixed_corpus_blind_round_20260803",
        "speaker": "Narrator",
        "instructions": {
            "neutral": "Judge identity, exact text, naturalness, and stable restrained narration.",
            "dread": "Judge identity, exact text, naturalness, and whether restrained dread is actually audible.",
        },
        "score_contract": {
            "identity": "1 wrong speaker; 5 unmistakably the expected Narrator",
            "delivery": "1 misses the requested lane; 5 clearly and naturally matches it",
            "naturalness": "1 broken or synthetic; 5 natural audiobook performance",
            "text_match": "The entire authored sentence is present without additions",
            "artifact_free": "No looping, truncation, clicks, leakage, or spoken control tags",
        },
        "samples": public_samples,
    }
    (review / "data.json").write_text(
        json.dumps(public, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (review / "data.js").write_text(
        "window.ALEXANDRIA_REVIEW_DATA = "
        + json.dumps(public, ensure_ascii=False, sort_keys=True)
        + ";\n",
        encoding="utf-8",
    )
    answer = {
        "schema_version": 1,
        "round_id": public["round_id"],
        "seed": SEED,
        "answers": answers,
        "exclusions": exclusions,
        "production_promotion_allowed": False,
    }
    (answer_root / "answer-key.json").write_text(
        json.dumps(answer, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": public["round_id"],
        "sample_count": len(public_samples),
        "lane_counts": {
            lane: sum(item["lane"] == lane for item in public_samples)
            for lane in ("neutral", "dread")
        },
        "exclusion_count": len(exclusions),
        "review_path": str(review / "index.html"),
        "answer_key_path": str(answer_root / "answer-key.json"),
        "data_sha256": sha256_file(review / "data.json"),
        "data_js_sha256": sha256_file(review / "data.js"),
        "file_url_compatible": True,
        "production_promotion_allowed": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    print(
        json.dumps(
            package_round(
                candidate_root=args.candidate_root,
                output_root=args.output_root,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
