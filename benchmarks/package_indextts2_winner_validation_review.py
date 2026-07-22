#!/usr/bin/env python3
"""Evaluate and package the bounded IndexTTS2 winner-validation review."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = (
    ROOT / ".omo" / "evidence" / "b17-t05-reference-transfer-salvage" / "winner-validation"
)
STYLE_ORDER = ["fear", "panic", "contempt", "relief", "urgent", "calm", "pleading", "shout"]
SPEAKER_ORDER = ["narrator", "benny", "doctor"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def storage_key(review_rows: list[dict[str, Any]]) -> str:
    review_key = hashlib.sha256(
        "\0".join(str(row["sample_id"]) for row in review_rows).encode("utf-8")
    ).hexdigest()[:16]
    return f"alexandria-emotional-clone-review-{review_key}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--manifest")
    parser.add_argument("--outputs-root")
    parser.add_argument("--output-root")
    parser.add_argument("--worker-timeout", type=int, default=1800)
    args = parser.parse_args()

    validation_root = Path(args.validation_root).expanduser().resolve()
    manifest_path = (
        Path(args.manifest).expanduser().resolve()
        if args.manifest
        else validation_root / "manifest.json"
    )
    outputs_root = (
        Path(args.outputs_root).expanduser().resolve()
        if args.outputs_root
        else validation_root / "outputs"
    )
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else validation_root / "review"
    )
    manifest = read_json(manifest_path)
    samples = list(manifest.get("samples") or [])
    if manifest.get("sample_count") != 24 or len(samples) != 24:
        raise ValueError("Winner-validation review requires exactly 24 samples")
    if manifest.get("generic_ryan_regenerated") is not False:
        raise ValueError("Generic Ryan must not be part of winner validation")
    if set(manifest.get("styles") or []) != set(STYLE_ORDER):
        raise ValueError("Winner-validation style set changed")
    if set(manifest.get("speakers") or []) != set(SPEAKER_ORDER):
        raise ValueError("Winner-validation speaker set changed")

    manifests_dir = output_root / "manifests"
    pages_dir = output_root / "pages"
    answer_keys_dir = output_root / "answer-keys"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    answer_keys_dir.mkdir(parents=True, exist_ok=True)

    page_info: dict[str, Any] = {}
    aggregate_samples: list[dict[str, Any]] = []
    for style in STYLE_ORDER:
        style_samples = [item for item in samples if item["style"] == style]
        style_samples.sort(key=lambda row: SPEAKER_ORDER.index(row["speaker"]))
        if len(style_samples) != 3:
            raise ValueError(f"Expected three validation samples for {style}")
        expected_texts = {item["text"] for item in style_samples}
        if len(expected_texts) != 1:
            raise ValueError(f"Expected one matched line for {style}")
        manifest_samples = []
        for item in style_samples:
            audio_path = outputs_root / item["sample_id"] / "audio.wav"
            result_path = outputs_root / item["sample_id"] / "result.json"
            if not audio_path.is_file() or not result_path.is_file():
                raise FileNotFoundError(f"Missing generated output for {item['sample_id']}")
            result = read_json(result_path)
            if result.get("audio_sha256") != sha256_file(audio_path):
                raise ValueError(f"Generated audio hash mismatch for {item['sample_id']}")
            manifest_samples.append(
                {
                    "sample_id": item["sample_id"],
                    "candidate": "indextts2_winner_validation",
                    "direction": style,
                    "seed": item["seed"],
                    "path": str(audio_path.resolve()),
                    "reference_audio": item["reference_audio"],
                    "identity_label": item["identity_label"],
                    "expected_text": item["text"],
                }
            )

        evaluation_manifest = {
            "schema_version": 1,
            "purpose": "bounded_indextts2_cross_speaker_winner_validation",
            "expected_text": next(iter(expected_texts)),
            "reference_audio": style_samples[0]["reference_audio"],
            "identity_label": "Expected speaker identity",
            "samples": manifest_samples,
            "production_promotion_allowed": False,
        }
        evaluation_manifest_path = manifests_dir / f"{style}.json"
        evaluation_manifest_path.write_text(
            json.dumps(evaluation_manifest, indent=2) + "\n", encoding="utf-8"
        )
        page_dir = pages_dir / style
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "benchmarks" / "evaluate_emotional_clone_outputs.py"),
                "--manifest",
                str(evaluation_manifest_path),
                "--output-dir",
                str(page_dir),
                "--worker-timeout",
                str(args.worker_timeout),
            ],
            cwd=str(ROOT),
            check=True,
        )
        audio_dir = page_dir / "audio"
        for audio_path in audio_dir.iterdir():
            if audio_path.is_symlink():
                source = audio_path.resolve()
                audio_path.unlink()
                shutil.copy2(source, audio_path)

        answer_key = read_json(page_dir / "answer_key.json")
        by_source = {item["sample_id"]: item for item in style_samples}
        enriched_answer_key = []
        for row in answer_key:
            source_id = row["source_sample_id"]
            source = by_source[source_id]
            result = read_json(outputs_root / source_id / "result.json")
            enriched_answer_key.append(
                {
                    **row,
                    "speaker": source["speaker"],
                    "identity_label": source["identity_label"],
                    "style": source["style"],
                    "selection_kind": source["selection_kind"],
                    "source_selection_sample_id": source["source_selection_sample_id"],
                    "source_instruction_sha256": source["source_instruction_sha256"],
                    "source_seed": source["source_seed"],
                    "emotion_strength": source["emotion_strength"],
                    "emotion_strength_origin": source["emotion_strength_origin"],
                    "speaker_reference_sha256": source["reference_audio_sha256"],
                    "emotion_reference_sha256": source["emotion_audio_sha256"],
                    "generated_audio_sha256": result["audio_sha256"],
                    "generated_result": str(
                        (outputs_root / source_id / "result.json").relative_to(ROOT)
                    ),
                }
            )
        style_answer_key_path = answer_keys_dir / f"{style}.json"
        style_answer_key_path.write_text(
            json.dumps(enriched_answer_key, indent=2) + "\n", encoding="utf-8"
        )
        (page_dir / "answer_key.json").unlink()

        review_rows = read_json(page_dir / "listening_review.json")
        evaluation = read_json(page_dir / "evaluation.json")
        page_storage_key = storage_key(review_rows)
        page_info[style] = {
            "review": str((page_dir / "review.html").relative_to(output_root)),
            "answer_key": str(style_answer_key_path.relative_to(output_root)),
            "answer_key_sha256": sha256_file(style_answer_key_path),
            "storage_key": page_storage_key,
            "sample_count": 3,
            "expected_identities": [item["identity_label"] for item in style_samples],
            "all_audio_copied": all(
                audio_path.is_file() and not audio_path.is_symlink()
                for audio_path in audio_dir.iterdir()
            ),
            "transcription_complete": evaluation["transcription_evaluation"]["complete"],
            "speaker_evaluation_available": evaluation["speaker_evaluation"]["available"],
        }
        for item in style_samples:
            result = read_json(outputs_root / item["sample_id"] / "result.json")
            transcript = evaluation["transcription_evaluation"]["measurements"][item["sample_id"]]
            speaker = evaluation["speaker_evaluation"]["measurements"][item["sample_id"]]
            aggregate_samples.append(
                {
                    "sample_id": item["sample_id"],
                    "speaker": item["speaker"],
                    "style": style,
                    "selection_kind": item["selection_kind"],
                    "source_selection_sample_id": item["source_selection_sample_id"],
                    "emotion_strength": item["emotion_strength"],
                    "speaker_reference_sha256": item["reference_audio_sha256"],
                    "emotion_reference_sha256": item["emotion_audio_sha256"],
                    "generated_audio_sha256": result["audio_sha256"],
                    "duration_seconds": result["audio"]["duration_seconds"],
                    "real_time_factor": result["real_time_factor"],
                    "automatic_transcript": transcript["transcript"],
                    "automatic_transcript_sha256": transcript["transcript_sha256"],
                    "word_error_rate": transcript["word_error_rate"],
                    "speaker_cosine_to_expected_reference": speaker[
                        "speaker_cosine_to_primary_reference"
                    ],
                }
            )

    storage_keys = {info["storage_key"] for info in page_info.values()}
    if len(storage_keys) != len(STYLE_ORDER):
        raise ValueError("Review pages must use unique autosave keys")
    if not all(info["all_audio_copied"] for info in page_info.values()):
        raise ValueError("Every review WAV must be copied into the review tree")

    cards = "\n".join(
        f'<a class="card" href="{html.escape(info["review"])}">'
        f'<strong>{html.escape(style.title())}</strong>'
        f'<span>3 voices · selected {html.escape(style)} reference/configuration</span></a>'
        for style, info in page_info.items()
    )
    hub = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IndexTTS2 winner-validation review</title>
<style>
body{{font:16px/1.45 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:32px;background:#f5f3ee;color:#25231f}}
.notice{{padding:14px 16px;border:1px solid #b9b3a7;background:#fffdf8;border-radius:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:24px}}
.card{{display:grid;gap:4px;padding:18px;background:white;border:1px solid #d9d3c7;border-radius:10px;color:inherit;text-decoration:none}}
.card:hover{{border-color:#777}} .card span{{color:#666}}
</style></head><body>
<h1>IndexTTS2 winner-validation review</h1>
<p class="notice"><strong>Bounded cross-speaker validation.</strong> Each page contains Narrator, Benny, and Doctor using the same human-selected acting reference or transfer strength. Expected identity is visible; configuration and speaker order remain in separate answer keys. Complete every required field before export.</p>
<div class="grid">{cards}</div>
</body></html>"""
    (output_root / "index.html").write_text(hub, encoding="utf-8")

    aggregate_samples.sort(key=lambda row: (STYLE_ORDER.index(row["style"]), SPEAKER_ORDER.index(row["speaker"])))
    objective = {
        "schema_version": 1,
        "purpose": "bounded_cross_speaker_validation_of_selected_acting_references_and_transfer_strengths",
        "manifest": str(manifest_path.relative_to(ROOT)),
        "manifest_sha256": sha256_file(manifest_path),
        "style_count": len(STYLE_ORDER),
        "speaker_count": len(SPEAKER_ORDER),
        "sample_count": len(aggregate_samples),
        "perfect_transcript_count": sum(
            item["word_error_rate"] == 0.0 for item in aggregate_samples
        ),
        "max_word_error_rate": max(item["word_error_rate"] for item in aggregate_samples),
        "speaker_cosine_range": [
            min(item["speaker_cosine_to_expected_reference"] for item in aggregate_samples),
            max(item["speaker_cosine_to_expected_reference"] for item in aggregate_samples),
        ],
        "mean_real_time_factor": sum(item["real_time_factor"] for item in aggregate_samples)
        / len(aggregate_samples),
        "samples": aggregate_samples,
        "review": {
            "hub": str((output_root / "index.html").relative_to(validation_root)),
            "page_count": len(page_info),
            "sample_count": len(aggregate_samples),
            "page_info": page_info,
            "answer_keys_separate": True,
            "unique_autosave_keys": True,
            "autosave_on_input": True,
            "completion_counter": True,
            "next_incomplete_control": True,
            "incomplete_export_blocked": True,
            "all_audio_copied_into_review_tree": True,
            "temporary_paths_required": False,
            "manual_blinded_review_required": True,
        },
        "compatibility_status": {
            speaker: {
                style: "pending_human_review" for style in STYLE_ORDER
            }
            for speaker in SPEAKER_ORDER
        },
        "license_review_complete": False,
        "production_promotion_allowed": False,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    objective_path = validation_root / "objective_summary.json"
    objective_path.write_text(json.dumps(objective, indent=2) + "\n", encoding="utf-8")
    review_manifest = {
        "schema_version": 1,
        "purpose": "durable_blinded_winner_validation_review",
        "hub": "index.html",
        "page_count": len(page_info),
        "sample_count": len(aggregate_samples),
        "page_info": page_info,
        "answer_keys_separate": True,
        "unique_autosave_keys": True,
        "all_audio_copied_into_review_tree": True,
        "temporary_paths_required": False,
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(review_manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "hub": str(output_root / "index.html"),
                "objective_summary": str(objective_path),
                "page_count": len(page_info),
                "sample_count": len(aggregate_samples),
                "perfect_transcript_count": objective["perfect_transcript_count"],
                "temporary_paths_required": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
