#!/usr/bin/env python3
"""Build the second surgical boundary repair round for strict overlaps."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from model_registry import resolve_model_path
import benchmarks.build_original_sin_direct_overlap_expansion_batch_002 as review_base
from benchmarks.build_original_sin_direct_overlap_boundary_repair_v3 import (
    source_wav_for,
    write_start_trim,
    write_tail_splice,
    write_trailing_trim,
)
from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_overlap_reference_round import (
    WHISPER_MODEL_KEY,
    metrics,
    sha256_file,
    transcribe,
    utc_now,
    write_json,
)
from benchmarks.build_original_sin_overlap_reference_repair_round import project_hashes
from benchmarks.original_sin_overlap_word_alignment import accepted_transcript_check, transcript_check_eligible


ROUND_ID = "alexandria_original_sin_direct_overlap_boundary_repair_v4"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_overlap_boundary_repair_plan_v4.json")


class BoundaryRepairV4Error(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    plan = read_json(args.plan.expanduser().resolve())
    if plan.get("round_id") != ROUND_ID:
        raise BoundaryRepairV4Error("Boundary-repair v4 plan mismatch")
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    answer_keys = {
        "direct_overlap_boundary_repair_v3": read_json(workflow / "direct_overlap_boundary_repair_v3/private/answer-key.json")["candidates"],
        "direct_overlap_expansion_batch_003": read_json(workflow / "direct_overlap_expansion_batch_003/private/answer-key.json")["candidates"],
    }
    output = args.output_root.expanduser().resolve() if args.output_root else workflow / "direct_overlap_boundary_repair_v4"
    if output.exists():
        if not args.replace:
            raise BoundaryRepairV4Error(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    private = output / "private/audio"
    groups: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    for spec in plan["groups"]:
        rows = answer_keys[spec["source_round"]]
        bases = [(candidate_id, rows[candidate_id]) for candidate_id in spec["candidate_ids"]]
        candidates: list[dict[str, Any]] = []
        for base_id, base in bases:
            variants: list[tuple[str, dict[str, Any]]] = []
            if spec["action"] == "start_trim":
                variants = [("start_trim", {})]
            elif spec["action"] == "tighter_trailing_trim":
                variants = [("trailing_trim", {"postroll": float(value)}) for value in plan["trailing_postroll_seconds"]]
            elif spec["action"] == "bounded_tail_recovery":
                variants = [("tail_recovery", {"takeover_ms": int(value)}) for value in plan["tail_source_takeover_milliseconds"]]
            else:
                raise BoundaryRepairV4Error(spec["action"])
            for action, values in variants:
                suffix = action + "_" + "_".join(f"{key}{value}" for key, value in values.items()) if values else action
                treatment = f"{base['treatment']}__{suffix}"
                wav = private / f"chunk_{spec['chunk_id']}__{treatment}.wav"
                try:
                    source = base["source"]
                    if action == "start_trim":
                        trim = max(0.0, float(source["transcript_segment_start_seconds"]) - float(source["source_start_seconds"]) - float(plan["start_preroll_seconds"]))
                        receipt = write_start_trim(Path(base["wav_path"]), wav, trim, int(plan["fade_milliseconds"]), int(plan["appended_silence_milliseconds"]))
                    elif action == "trailing_trim":
                        keep = float(source["transcript_segment_end_seconds"]) - float(source["source_start_seconds"]) + float(values["postroll"])
                        receipt = write_trailing_trim(Path(base["wav_path"]), wav, keep, int(plan["fade_milliseconds"]), int(plan["appended_silence_milliseconds"]))
                    else:
                        receipt = write_tail_splice(
                            Path(base["wav_path"]), source_wav_for(base), wav,
                            takeover_ms=int(values["takeover_ms"]),
                            start_trim_ms=0,
                            fade_ms=int(plan["fade_milliseconds"]),
                            silence_ms=int(plan["appended_silence_milliseconds"]),
                        )
                    wav_check = accepted_transcript_check([base["transcript"]], transcribe(wav, whisper))
                    proxy = private / f"chunk_{spec['chunk_id']}__{treatment}.mp3"
                    encode_proxy(wav, proxy, bitrate=plan["production_proxy"]["bitrate"])
                    proxy_check = accepted_transcript_check([base["transcript"]], transcribe(proxy, whisper))
                    probe = probe_audio(proxy)
                    if not (transcript_check_eligible(wav_check) and transcript_check_eligible(proxy_check) and probe["codec_name"] == "mp3" and probe["sample_rate"] == 44100 and probe["channels"] == 2):
                        raise BoundaryRepairV4Error("Objective transcript/proxy gate failed")
                    candidates.append({
                        "treatment": treatment,
                        "base_candidate_id": base_id,
                        "base_treatment": base["treatment"],
                        "repair_action": spec["action"],
                        "repair_receipt": receipt,
                        "wav_path": wav,
                        "wav_metrics": metrics(wav),
                        "wav_objective": wav_check,
                        "proxy_path": proxy,
                        "proxy_sha256": sha256_file(proxy),
                        "proxy_probe": probe,
                        "proxy_objective": proxy_check,
                        "objective_eligible": True,
                    })
                except Exception as exc:
                    omissions.append({"chunk_id": spec["chunk_id"], "treatment": treatment, "error_type": type(exc).__name__, "error": str(exc)[:1200]})
        if candidates:
            first = bases[0][1]
            groups.append({
                "character": first["character"],
                "book_speaker": first["book_speaker"],
                "chunk_id": int(first["chunk_id"]),
                "transcript": first["transcript"],
                "source": first["source"],
                "review_context": f"Surgical {spec['action'].replace('_', ' ')} repair; reject clipped phonemes, pre-roll sound, adjacent breath, or source-tail contamination.",
                "candidates": candidates,
            })
    review_base.ROUND_ID = ROUND_ID
    review_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise BoundaryRepairV4Error("Protected project hashes changed")
    write_json(output / "generation-summary.json", {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "planned_chunk_count": len(plan["groups"]),
        "review_chunk_count": len(groups),
        "candidate_count": sum(len(group["candidates"]) for group in groups),
        "omissions": omissions,
        "protected_project_hashes_before": before,
        "protected_project_hashes_after": after,
        "production_changes": False,
        "output_root": str(output),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
