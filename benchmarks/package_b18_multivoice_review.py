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
ASSETS = ROOT / "benchmarks" / "b18_multivoice_review_assets"
DEFAULT_CANDIDATES = (
    ROOT / "training_sidecar_runtime" / "b18_multivoice_archetype_screen_20260803"
)
DEFAULT_OUTPUT = (
    ROOT / ".omo" / "evidence" / "b18-multivoice-archetype-screen-20260803"
)
SEED = 20260803


class MultiVoiceReviewError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_round(
    *,
    candidate_root: str | Path = DEFAULT_CANDIDATES,
    output_root: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    candidates = Path(candidate_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    objective = json.loads(
        (candidates / "objective_summary.json").read_text(encoding="utf-8")
    )
    rows = objective["rows"]
    eligible = [row for row in rows if row["eligible"]]
    excluded = [
        {
            "candidate_id": row["candidate_id"],
            "speaker_key": row["speaker_key"],
            "method": row["method"],
            "reason": row.get("exclusion_reason"),
            "error": row.get("error"),
            "objective": row.get("objective"),
        }
        for row in rows
        if not row["eligible"]
    ]
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

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        grouped.setdefault(row["speaker_key"], []).append(row)
    rng = random.Random(SEED)
    groups: list[dict[str, Any]] = []
    public_samples: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    prefixes = {
        "THE DOCTOR": "DOC",
        "BERNICE": "BEN",
        "CHRIS CWEJ": "CHR",
        "ROZ FORRESTER": "ROZ",
        "COMPUTER": "COM",
        "TOBIAS VAUGHN": "TOB",
        "POWERLESS FRIENDLESS": "HIT",
    }
    for speaker_key in [
        "THE DOCTOR",
        "BERNICE",
        "CHRIS CWEJ",
        "ROZ FORRESTER",
        "COMPUTER",
        "TOBIAS VAUGHN",
        "POWERLESS FRIENDLESS",
    ]:
        speaker_rows = list(grouped.get(speaker_key, []))
        if not speaker_rows:
            raise MultiVoiceReviewError(
                f"No eligible candidates remain for {speaker_key}."
            )
        rng.shuffle(speaker_rows)
        first = speaker_rows[0]
        reference = Path(first["objective"]["reference_path"])
        reference_name = f"{prefixes[speaker_key].lower()}_reference{reference.suffix}"
        shutil.copy2(reference, reference_root / reference_name)
        groups.append(
            {
                "speaker_key": speaker_key,
                "display_name": first["display_name"],
                "archetype": first["archetype"],
                "expected_text": first["text"],
                "instruction": first["instruction"],
                "reference_audio": f"reference/{reference_name}",
                "candidate_count": len(speaker_rows),
            }
        )
        for index, row in enumerate(speaker_rows, start=1):
            blind_id = f"{prefixes[speaker_key]}{index:02d}"
            source = Path(row["objective"]["output_path"])
            target = audio_root / f"{blind_id}.wav"
            shutil.copy2(source, target)
            public_samples.append(
                {
                    "sample_id": blind_id,
                    "speaker_key": speaker_key,
                    "expected_speaker": row["display_name"],
                    "expected_text": row["text"],
                    "instruction": row["instruction"],
                    "archetype": row["archetype"],
                    "audio": f"audio/{target.name}",
                }
            )
            answers.append(
                {
                    "sample_id": blind_id,
                    "candidate_id": row["candidate_id"],
                    "speaker_key": speaker_key,
                    "method": row["method"],
                    "source_path": str(source),
                    "source_sha256": sha256_file(source),
                    "objective": row["objective"],
                    "generation": row.get("generation"),
                }
            )
    public = {
        "schema_version": 1,
        "round_id": "b18_multivoice_archetype_screen_20260803",
        "groups": groups,
        "samples": public_samples,
        "score_contract": {
            "identity": "1 wrong character; 5 unmistakably the expected character",
            "delivery": "1 misses the signature delivery; 5 clearly and naturally matches it",
            "naturalness": "1 broken or synthetic in the wrong way; 5 convincing audiobook performance",
            "text_match": "The entire authored line is present without additions",
            "artifact_free": "No looping, truncation, clicks, leakage, spoken tags, or objectionable processing",
        },
        "review_rule": (
            "Judge each speaker independently. Do not use one speaker's winner "
            "as a universal model decision."
        ),
    }
    (review / "data.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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
        "exclusions": excluded,
        "production_promotion_allowed": False,
    }
    (answer_root / "answer-key.json").write_text(
        json.dumps(answer, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": public["round_id"],
        "speaker_count": len(groups),
        "sample_count": len(public_samples),
        "speaker_counts": {
            group["speaker_key"]: group["candidate_count"] for group in groups
        },
        "exclusion_count": len(excluded),
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
