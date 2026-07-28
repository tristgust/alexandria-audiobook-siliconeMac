"""Review-eligibility diagnostics for structurally generated Round 1 audio."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multimodel_round1_paths import (
    SafeIdentifier,
    contained_path,
    parse_artifact_paths,
    safe_file_stat,
    safe_read_text,
    safe_sha256_file,
)
from multimodel_round1_runtime import (
    atomic_write_json,
    sha256_text,
    wav_is_decodable,
)


LONG_OUTPUT_SECONDS = 30.0
MOSS_CEILING_DURATION_SECONDS = 122.88
MOSS_MAX_TOKENS = 768
ANOMALY_RELATIVE_PATH = Path("recovery/moss-long-output-anomalies.json")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/b17-t05-multimodel-round1"


def _objective_measurements(
    evidence: Path, internal: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    measurements: dict[str, dict[str, Any]] = {}
    for group in internal["groups"]:
        try:
            group_id = SafeIdentifier(str(group))
            target = contained_path(evidence, f"objective/{group_id}.json")
            payload = json.loads(safe_read_text(target))
        except (OSError, json.JSONDecodeError):
            continue
        measurements.update(payload.get("measurements") or {})
    return measurements


def _diagnostics(
    sample: dict[str, Any], receipt: dict[str, Any], objective: dict[str, Any] | None
) -> dict[str, Any]:
    audio_sha = receipt.get("audio_sha256")
    receipt_sha = receipt.get("_receipt_sha256")
    target_ok = (
        sha256_text(sample["target_text"]) == sample["target_text_sha256"]
        and receipt.get("target_text_sha256") == sample["target_text_sha256"]
    )
    objective_current = bool(
        objective
        and objective.get("audio_sha256") == audio_sha
        and objective.get("target_text_sha256") == sample["target_text_sha256"]
        and objective.get("generation_receipt_sha256") == receipt_sha
    )
    if not objective_current:
        transcript = {"status": "pending", "word_error_rate": None}
    else:
        word_error_rate = objective.get("word_error_rate")
        transcript = {
            "status": "pass" if word_error_rate == 0 else "fail",
            "word_error_rate": word_error_rate,
            "automatic_transcript_sha256": objective.get(
                "automatic_transcript_sha256"
            ),
        }
    return {
        "transcript": transcript,
        "target_text": {"status": "pass" if target_ok else "fail"},
        "artifact": {
            "status": "pass" if receipt.get("_artifact_ok") else "fail"
        },
        "duration": {
            "status": "fail",
            "reason": "duration_over_30_seconds",
            "maximum_review_seconds": LONG_OUTPUT_SECONDS,
        },
    }


def build_moss_long_output_manifest(
    evidence: Path, internal: dict[str, Any]
) -> dict[str, Any]:
    objective_by_sample = _objective_measurements(evidence, internal)
    entries: list[dict[str, Any]] = []
    structural_count = 0
    for sample in internal["sample_specs"]:
        if sample["model_key"] != "moss_tts_local_v15":
            continue
        try:
            artifacts = parse_artifact_paths(
                evidence,
                str(sample["output_file"]),
                str(sample["result_file"]),
            )
            receipt = json.loads(safe_read_text(artifacts.result))
        except (OSError, json.JSONDecodeError):
            continue
        structural_count += 1
        duration = float((receipt.get("audio") or {}).get("duration_seconds") or 0)
        if duration <= LONG_OUTPUT_SECONDS:
            continue
        audio_sha = receipt.get("audio_sha256")
        artifact_ok = bool(
            audio_sha
            and safe_file_stat(artifacts.output).st_size > 44
            and safe_sha256_file(artifacts.output) == audio_sha
            and wav_is_decodable(artifacts.output.literal, root=evidence)
        )
        decorated = {
            **receipt,
            "_artifact_ok": artifact_ok,
            "_receipt_sha256": safe_sha256_file(artifacts.result),
        }
        runtime_tokens = (receipt.get("runtime_controls") or {}).get("max_tokens")
        manifest_tokens = sample["control"].get("max_tokens")
        ceiling_hit = bool(
            runtime_tokens == MOSS_MAX_TOKENS
            and abs(duration - MOSS_CEILING_DURATION_SECONDS) < 0.001
        )
        diagnostics = _diagnostics(
            sample, decorated, objective_by_sample.get(sample["sample_id"])
        )
        review_eligible = all(
            diagnostic["status"] == "pass" for diagnostic in diagnostics.values()
        )
        entries.append(
            {
                "sample_id": sample["sample_id"],
                "blind_id": sample["blind_id"],
                "model_key": sample["model_key"],
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "group": sample["group"],
                "duration_seconds": duration,
                "max_tokens": runtime_tokens,
                "manifest_max_tokens": manifest_tokens,
                "runtime_max_tokens": runtime_tokens,
                "ceiling_hit": ceiling_hit,
                "audio_sha256": audio_sha,
                "target_text_sha256": sample["target_text_sha256"],
                "receipt": sample["result_file"],
                "receipt_sha256": decorated["_receipt_sha256"],
                "diagnostics": diagnostics,
                "structurally_generated": artifact_ok,
                "review_eligible": review_eligible,
            }
        )
    return {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "model_key": "moss_tts_local_v15",
        "duration_threshold_seconds": LONG_OUTPUT_SECONDS,
        "approved_max_tokens": MOSS_MAX_TOKENS,
        "ceiling_duration_seconds": MOSS_CEILING_DURATION_SECONDS,
        "structurally_generated_moss_count": structural_count,
        "over_30_seconds_count": len(entries),
        "ceiling_hit_count": sum(entry["ceiling_hit"] for entry in entries),
        "review_eligible_anomaly_count": sum(
            entry["review_eligible"] for entry in entries
        ),
        "entries": entries,
    }


def write_moss_long_output_manifest(
    evidence: Path, internal: dict[str, Any]
) -> dict[str, Any]:
    manifest = build_moss_long_output_manifest(evidence, internal)
    atomic_write_json(evidence / ANOMALY_RELATIVE_PATH, manifest, root=evidence)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args()
    evidence = Path(args.evidence_root).expanduser().resolve()
    internal = json.loads(
        safe_read_text(contained_path(evidence, "round1_internal_manifest.json"))
    )
    manifest = write_moss_long_output_manifest(evidence, internal)
    print(
        json.dumps(
            {
                "output": str(evidence / ANOMALY_RELATIVE_PATH),
                "over_30_seconds_count": manifest["over_30_seconds_count"],
                "ceiling_hit_count": manifest["ceiling_hit_count"],
                "review_eligible_anomaly_count": manifest[
                    "review_eligible_anomaly_count"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
