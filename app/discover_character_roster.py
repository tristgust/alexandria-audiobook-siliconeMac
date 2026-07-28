from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from character_roster import (
    build_source_snapshot,
    read_character_roster,
    save_character_roster,
)
from generate_script import fix_mojibake
from llm_adapter import (
    build_roster_client,
    print_llm_metrics,
)
from roster_discovery import (
    DEFAULT_PASSAGE_OVERLAP,
    DEFAULT_PASSAGE_SIZE,
    RosterDiscoveryEvidenceError,
    build_discovery_identity,
    build_discovery_passages,
    build_draft_from_discovery_state,
    checkpoint_roster_passage,
    checkpoint_roster_reconciliation,
    completed_observations,
    normalize_passage_result,
    prepare_roster_discovery_state,
    clear_roster_discovery_state,
)
from stage_metrics import (
    StageMetricsError,
    mark_stage_metrics_failed,
    prepare_stage_metrics,
    record_stage_event,
    record_stage_unit,
    summarize_stage_metrics,
)


DISCOVERY_SYSTEM_PROMPT = """You are performing evidence-bound whole-book character roster discovery for an audiobook pipeline. Return only JSON matching the requested roster_discovery schema.

Rules:
- The top level must be exactly {"entities": [...], "warnings": []}. Never return a roster_discovery wrapper.
- Every entity must include every schema field: identity_seed, canonical_name, display_name, entity_kind, speaking_status, titles, aliases, nicknames, pronouns, species, relationships, voice_clues, sample_lines, confidence, resolution_status, unresolved_questions, and evidence. Never use entity_id in place of identity_seed.
- Return compact one-line JSON without indentation or repeated whitespace.
- Use empty arrays for unsupported optional fields.
- Every optional claim field is always a JSON array of strings; never a bare string, object, or null.
- Entity confidence and every evidence confidence must each be an unquoted finite JSON number from 0.0 through 1.0; never use strings, labels, NaN, or Infinity.
- Include at most one sample line per entity.
- sample_lines must be exactly [] or [one exact source string].
- Include no redundant evidence records; retain one exact evidence record for every category required by each populated claim.
- Do not omit a materially distinct supported entity only to shorten the response.
- Use only the supplied source passage.
- Record characters, groups, creatures, narrator roles, and named non-speakers when relevant.
- Do not merge uncertain or same-name identities. Keep them separate and mark ambiguity.
- Every entity requires exact source evidence.
- Evidence quote must be copied byte-for-byte from the passage.
- Evidence start_char and end_char are zero-based offsets relative to the supplied passage, with end_char exclusive.
- Evidence start_char and end_char must be JSON integers with 0 <= start_char < end_char <= the supplied passage's Unicode code-point length.
- For every nonempty exact evidence quote, end_char must equal start_char plus the exact quote's Unicode code-point length.
- Evidence categories must support every populated claim: name/alias/title/nickname for canonical identity; title for titles; alias for aliases; nickname for nicknames; pronoun for pronouns; species for species; relationship for relationships; voice for voice clues; and speaking for speaker, non-speaker, or narrator status.
- The same exact quote may appear in multiple evidence records when it supports multiple categories.
- Sample lines must be exact source substrings from the passage.
- Pronouns, species, relationships, voice clues, titles, aliases, and nicknames must be explicit or clearly marked through evidence basis; do not invent missing details.
- Prior candidates are continuity hints, not authority.
"""


RECONCILIATION_SYSTEM_PROMPT = """You reconcile evidence-bound character observations from across an entire book. Return only JSON matching the requested roster_reconciliation schema.

Rules:
- Every observation_id must appear exactly once, either assigned to one entry or in excluded_observation_ids.
- Do not silently merge uncertain identities.
- Use one entry for observations that clearly refer to the same identity.
- Keep distinct or uncertain same-name identities separate and use possible_duplicate_seeds plus duplicate_candidates.
- Preserve unnamed speakers and unresolved questions explicitly.
- Named non-speakers may remain roster entries; exclude only observations that are not useful roster entities.
- observation_ids are the evidence authority. Do not invent observations, names, aliases, species, relationships, or voice traits.
"""


def _load_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Configuration could not be read: {exc}") from exc
    return value if isinstance(value, dict) else {}


def _int_setting(
    value: Any,
    default: int,
    *,
    minimum: int,
) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return normalized if normalized >= minimum else default


def _float_setting(
    value: Any,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if minimum <= normalized <= maximum:
        return normalized
    return default


def _runtime_identity(runtime: Any) -> dict[str, Any]:
    return {
        "base_url": getattr(runtime, "base_url", None),
        "context_length": getattr(runtime, "context_length", None),
        "keep_alive": getattr(runtime, "keep_alive", None),
        "thinking": getattr(runtime, "thinking", None),
        "structured_output": getattr(
            runtime,
            "structured_output",
            None,
        ),
        "corrective_retry": getattr(
            runtime,
            "corrective_retry",
            None,
        ),
        "timeout": getattr(runtime, "timeout", None),
    }


def _metric_seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    result = float(value)
    return max(result, 0.0) if math.isfinite(result) else 0.0


def _metric_count(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        return 0
    return value


def _roster_result_timing(
    result: Any,
    *,
    measured_request_seconds: float,
) -> dict[str, Any]:
    metrics = getattr(result, "metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
    corrective_retries = _metric_count(
        metrics.get("corrective_retry_count")
    )
    return {
        "attempts": 1 + corrective_retries,
        "corrective_retries": corrective_retries,
        "prompt_tokens": _metric_count(
            metrics.get("prompt_tokens")
        ),
        "output_tokens": _metric_count(
            metrics.get("output_tokens")
        ),
        "validation_mode": getattr(
            result,
            "validation_mode",
            None,
        ),
        "phases_seconds": {
            "request_wall": max(
                _metric_seconds(
                    metrics.get("request_wall_seconds")
                ),
                _metric_seconds(measured_request_seconds),
            ),
            "model_total": _metric_seconds(
                metrics.get("total_seconds")
            ),
            "model_load": _metric_seconds(
                metrics.get("load_seconds")
            ),
            "model_prompt": _metric_seconds(
                metrics.get("prompt_seconds")
            ),
            "model_generation": _metric_seconds(
                metrics.get("generation_seconds")
            ),
            "schema_validation": _metric_seconds(
                metrics.get("schema_validation_seconds")
            ),
        },
    }


def _prepare_roster_stage_metrics(
    path: str | Path | None,
    *,
    run_id: str,
    total_units: int,
    baseline_completed_units: int,
) -> Path | None:
    if path is None:
        return None
    target = Path(path)
    try:
        prepare_stage_metrics(
            target,
            stage="roster",
            run_id=run_id,
            total_units=total_units,
            baseline_completed_units=(
                baseline_completed_units
            ),
        )
    except (StageMetricsError, OSError, TypeError, ValueError) as exc:
        print(
            "Warning: Character-roster timing is unavailable: "
            f"{exc}"
        )
        return None
    return target


def _record_roster_unit_metrics(
    path: Path | None,
    *,
    index: int,
    input_characters: int,
    output_items: int,
    timing: dict[str, Any],
) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        document = record_stage_unit(
            path,
            stage="roster",
            index=index,
            input_characters=input_characters,
            output_items=output_items,
            attempts=timing["attempts"],
            corrective_retries=timing[
                "corrective_retries"
            ],
            prompt_tokens=timing["prompt_tokens"],
            output_tokens=timing["output_tokens"],
            validation_mode=timing["validation_mode"],
            phases_seconds=timing["phases_seconds"],
        )
    except (StageMetricsError, OSError, TypeError, ValueError) as exc:
        print(
            "Warning: Character-roster timing could not be recorded: "
            f"{exc}"
        )
        return None
    summary = summarize_stage_metrics(document)
    throughput = summary.get("rolling_units_per_minute")
    if throughput is not None:
        print(
            "  Rolling roster throughput: "
            f"{throughput:.2f} passages/min"
        )
    if summary.get("eta_reliable"):
        print(
            "  Conservative roster ETA: "
            f"{summary['eta_seconds'] / 60.0:.1f} min"
        )
    return document


def _record_roster_event_metrics(
    path: Path | None,
    *,
    event: str,
    timing: dict[str, Any],
    mark_complete: bool = False,
) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        return record_stage_event(
            path,
            stage="roster",
            event=event,
            phases_seconds=timing["phases_seconds"],
            attempts=timing.get("attempts", 0),
            corrective_retries=timing.get(
                "corrective_retries",
                0,
            ),
            prompt_tokens=timing.get("prompt_tokens"),
            output_tokens=timing.get("output_tokens"),
            validation_mode=timing.get("validation_mode"),
            mark_complete=mark_complete,
        )
    except (StageMetricsError, OSError, TypeError, ValueError) as exc:
        print(
            "Warning: Character-roster stage timing was not recorded: "
            f"{exc}"
        )
        return None


def _mark_roster_metrics_failed(
    path: Path | None,
    error: Exception | str,
) -> None:
    if path is None or not path.exists():
        return
    try:
        mark_stage_metrics_failed(
            path,
            stage="roster",
            error=str(error),
        )
    except (StageMetricsError, OSError, TypeError, ValueError) as exc:
        print(
            "Warning: Character-roster timing failure was not recorded: "
            f"{exc}"
        )


def _continuity_hints(
    observations: list[dict[str, Any]],
    *,
    limit: int = 160,
) -> list[dict[str, Any]]:
    hints = []
    for observation in observations[-limit:]:
        hints.append(
            {
                "observation_id": observation["observation_id"],
                "identity_seed": observation["identity_seed"],
                "canonical_name": observation["canonical_name"],
                "display_name": observation["display_name"],
                "entity_kind": observation["entity_kind"],
                "speaking_status": observation["speaking_status"],
                "aliases": observation["aliases"],
                "titles": observation["titles"],
            }
        )
    return hints


def discovery_messages(
    *,
    passage: dict[str, Any],
    total_passages: int,
    prior_observations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "passage_index": passage["index"],
        "total_passages": total_passages,
        "absolute_start_char": passage["start_char"],
        "absolute_end_char": passage["end_char"],
        "prior_candidate_hints": _continuity_hints(
            prior_observations
        ),
        "source_passage": passage["text"],
    }
    return [
        {"role": "system", "content": DISCOVERY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def reconciliation_messages(
    observations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    compact = [
        {
            "observation_id": item["observation_id"],
            "identity_seed": item["identity_seed"],
            "canonical_name": item["canonical_name"],
            "display_name": item["display_name"],
            "entity_kind": item["entity_kind"],
            "speaking_status": item["speaking_status"],
            "titles": item["titles"],
            "aliases": item["aliases"],
            "nicknames": item["nicknames"],
            "pronouns": item["pronouns"],
            "species": item["species"],
            "relationships": item["relationships"],
            "confidence": item["confidence"],
            "resolution_status": item["resolution_status"],
            "unresolved_questions": item["unresolved_questions"],
            "voice_clues": item["voice_clues"][:6],
            "sample_lines": item["sample_lines"][:6],
            "evidence": [
                {
                    "source_quote": evidence["source_quote"],
                    "source_location": evidence["source_location"],
                    "category": evidence["category"],
                    "confidence": evidence["confidence"],
                    "basis": evidence["basis"],
                }
                for evidence in item["evidence"][:6]
            ],
        }
        for item in observations
    ]
    return [
        {"role": "system", "content": RECONCILIATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"observations": compact},
                ensure_ascii=False,
            ),
        },
    ]


def run_roster_discovery(
    source_path: str | Path,
    *,
    config_path: str | Path,
    state_path: str | Path,
    draft_path: str | Path,
    approved_path: str | Path,
    replace_draft: bool = False,
    passage_size_override: int | None = None,
    overlap_override: int | None = None,
    runtime_client: Any | None = None,
    generated_at_utc: str | None = None,
    metrics_path: str | Path | None = None,
) -> dict[str, Any]:
    source_target = Path(source_path).expanduser().resolve()
    state_target = Path(state_path)
    draft_target = Path(draft_path)
    approved_target = Path(approved_path)

    if approved_target.exists():
        raise RuntimeError(
            "An approved character roster already exists. It must not be "
            "overwritten by discovery."
        )

    if draft_target.exists() and not replace_draft and not state_target.exists():
        raise RuntimeError(
            "A character roster draft already exists. Use explicit "
            "replacement intent to generate a new draft."
        )

    source, source_text = build_source_snapshot(
        source_target,
        normalizer=fix_mojibake,
    )

    if not source_text:
        raise RuntimeError("The selected source book is empty.")

    if (
        passage_size_override is not None
        and passage_size_override < 100
    ):
        raise ValueError(
            "passage_size_override must be at least 100 characters."
        )

    if overlap_override is not None and overlap_override < 0:
        raise ValueError("overlap_override must be non-negative.")

    if (
        passage_size_override is not None
        and overlap_override is not None
        and overlap_override >= passage_size_override
    ):
        raise ValueError(
            "overlap_override must be smaller than passage_size_override."
        )

    config = _load_config(config_path)
    roster_config = (
        config.get("roster")
        if isinstance(config.get("roster"), dict)
        else {}
    )
    passage_size = _int_setting(
        (
            passage_size_override
            if passage_size_override is not None
            else roster_config.get("passage_size")
        ),
        DEFAULT_PASSAGE_SIZE,
        minimum=100,
    )
    overlap = _int_setting(
        (
            overlap_override
            if overlap_override is not None
            else roster_config.get("passage_overlap")
        ),
        DEFAULT_PASSAGE_OVERLAP,
        minimum=0,
    )

    if overlap >= passage_size:
        overlap = min(DEFAULT_PASSAGE_OVERLAP, passage_size - 1)

    temperature = _float_setting(
        roster_config.get("temperature"),
        0.2,
        minimum=0.0,
        maximum=2.0,
    )
    max_tokens = _int_setting(
        roster_config.get("max_tokens"),
        6144,
        minimum=256,
    )
    seed_value = roster_config.get("seed", 42)
    seed = (
        None
        if seed_value is None
        else _int_setting(seed_value, 42, minimum=0)
    )

    runtime = runtime_client or build_roster_client(config)
    passages = build_discovery_passages(
        source_text,
        passage_size=passage_size,
        overlap=overlap,
    )
    identity = build_discovery_identity(
        model_name=runtime.model_name,
        backend=runtime.backend,
        passage_size=passage_size,
        overlap=overlap,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        runtime_identity=_runtime_identity(runtime),
    )
    state = prepare_roster_discovery_state(
        path=state_target,
        source=source,
        generation_identity=identity,
        passages=passages,
    )
    completed_count = len(state["completed_passages"])
    active_metrics_path = _prepare_roster_stage_metrics(
        metrics_path,
        run_id=state["generation_fingerprint"],
        total_units=state["total_passages"],
        baseline_completed_units=completed_count,
    )

    print(
        f"Roster discovery: {len(state['completed_passages'])}/"
        f"{state['total_passages']} passages already complete."
    )

    for passage in passages[len(state["completed_passages"]):]:
        unit_started_at = time.perf_counter()
        try:
            prompt_started_at = time.perf_counter()
            prior = completed_observations(state)
            messages = discovery_messages(
                passage=passage,
                total_passages=len(passages),
                prior_observations=prior,
            )
            prompt_seconds = (
                time.perf_counter()
                - prompt_started_at
            )
            print(
                f"Discovering roster passage {passage['index']}/"
                f"{len(passages)} ({passage['start_char']}-"
                f"{passage['end_char']})..."
            )
            timing = {
                "attempts": 0,
                "corrective_retries": 0,
                "prompt_tokens": 0,
                "output_tokens": 0,
                "validation_mode": None,
                "phases_seconds": {
                    "prompt_assembly": prompt_seconds,
                },
            }
            evidence_validation_seconds = 0.0
            evidence_retry_used = False

            for evidence_attempt in range(2):
                request_started_at = time.perf_counter()
                result = runtime.complete_json(
                    messages=messages,
                    contract="roster_discovery",
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=0.8,
                    seed=seed,
                )
                request_seconds = (
                    time.perf_counter()
                    - request_started_at
                )
                print_llm_metrics(
                    (
                        "Roster discovery evidence retry"
                        if evidence_attempt
                        else "Roster discovery"
                    ),
                    result,
                )
                result_timing = _roster_result_timing(
                    result,
                    measured_request_seconds=request_seconds,
                )
                timing["attempts"] += result_timing["attempts"]
                timing["corrective_retries"] += (
                    result_timing["corrective_retries"]
                )
                timing["prompt_tokens"] += result_timing["prompt_tokens"]
                timing["output_tokens"] += result_timing["output_tokens"]
                for phase, seconds in result_timing[
                    "phases_seconds"
                ].items():
                    timing["phases_seconds"][phase] = (
                        timing["phases_seconds"].get(phase, 0.0)
                        + seconds
                    )

                evidence_started_at = time.perf_counter()
                try:
                    observations, warnings = normalize_passage_result(
                        result.data,
                        passage=passage,
                        source_fingerprint=source["fingerprint"],
                    )
                except RosterDiscoveryEvidenceError as exc:
                    evidence_validation_seconds += (
                        time.perf_counter()
                        - evidence_started_at
                    )
                    if evidence_attempt >= 1:
                        raise

                    evidence_retry_used = True
                    timing["corrective_retries"] += 1
                    correction_started_at = time.perf_counter()
                    messages.extend(
                        [
                            {
                                "role": "assistant",
                                "content": getattr(
                                    result,
                                    "content",
                                    json.dumps(
                                        result.data,
                                        ensure_ascii=False,
                                    ),
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    "Your JSON passed the structural schema "
                                    "but failed exact source-evidence "
                                    "validation.\n\n"
                                    f"Validation error: {exc}\n\n"
                                    "Return the complete corrected "
                                    "roster_discovery object. Use only exact "
                                    "quotes from the supplied source passage. "
                                    "For every populated claim, include a "
                                    "separate matching evidence category. "
                                    "Offsets must identify the exact quote; "
                                    "use a longer unique quote if the same "
                                    "text occurs more than once. If no exact "
                                    "quote supports a speaker, non-speaker, "
                                    "or narrator status, change "
                                    "speaking_status to uncertain. Do not "
                                    "add unsupported facts."
                                ),
                            },
                        ]
                    )
                    timing["phases_seconds"]["prompt_assembly"] += (
                        time.perf_counter()
                        - correction_started_at
                    )
                    continue

                evidence_validation_seconds += (
                    time.perf_counter()
                    - evidence_started_at
                )
                break

            timing["validation_mode"] = (
                "evidence_retry"
                if evidence_retry_used
                else result_timing["validation_mode"]
            )
            timing["phases_seconds"]["evidence_validation"] = (
                evidence_validation_seconds
            )
            checkpoint_started_at = time.perf_counter()
            state = checkpoint_roster_passage(
                state=state,
                path=state_target,
                passage=passage,
                observations=observations,
                warnings=warnings,
            )
            timing["phases_seconds"]["checkpoint_write"] = (
                time.perf_counter()
                - checkpoint_started_at
            )
            measured_unit_wall = (
                time.perf_counter()
                - unit_started_at
            )
            phase_lower_bound = sum(
                timing["phases_seconds"].get(name, 0.0)
                for name in (
                    "prompt_assembly",
                    "request_wall",
                    "evidence_validation",
                    "checkpoint_write",
                )
            )
            timing["phases_seconds"]["unit_wall"] = max(
                measured_unit_wall,
                phase_lower_bound,
            )
            _record_roster_unit_metrics(
                active_metrics_path,
                index=passage["index"],
                input_characters=len(passage["text"]),
                output_items=len(observations),
                timing=timing,
            )
            print(
                f"Checkpointed passage {passage['index']} with "
                f"{len(observations)} observation(s)."
            )
        except Exception as exc:
            _mark_roster_metrics_failed(
                active_metrics_path,
                exc,
            )
            raise

    observations = completed_observations(state)

    if state["reconciliation"] is None:
        reconciliation_started_at = time.perf_counter()
        try:
            print(
                f"Reconciling {len(observations)} whole-book observation(s)..."
            )
            prompt_started_at = time.perf_counter()
            messages = reconciliation_messages(observations)
            prompt_seconds = (
                time.perf_counter()
                - prompt_started_at
            )
            request_started_at = time.perf_counter()
            result = runtime.complete_json(
                messages=messages,
                contract="roster_reconciliation",
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=0.8,
                seed=seed,
            )
            request_seconds = (
                time.perf_counter()
                - request_started_at
            )
            print_llm_metrics("Roster reconciliation", result)
            reconciliation_timing = _roster_result_timing(
                result,
                measured_request_seconds=request_seconds,
            )
            reconciliation_timing["phases_seconds"][
                "prompt_assembly"
            ] = prompt_seconds
            validation_started_at = time.perf_counter()
            state = checkpoint_roster_reconciliation(
                state=state,
                path=state_target,
                reconciliation=result.data,
            )
            reconciliation_timing["phases_seconds"][
                "reconciliation_validation"
            ] = time.perf_counter() - validation_started_at
            reconciliation_timing["phases_seconds"]["unit_wall"] = (
                time.perf_counter()
                - reconciliation_started_at
            )
            _record_roster_event_metrics(
                active_metrics_path,
                event="reconciliation",
                timing=reconciliation_timing,
            )
        except Exception as exc:
            _mark_roster_metrics_failed(
                active_metrics_path,
                exc,
            )
            raise

    finalization_started_at = time.perf_counter()
    try:
        draft_started_at = time.perf_counter()
        draft = build_draft_from_discovery_state(
            state,
            source_text=source_text,
            generated_at_utc=generated_at_utc,
        )
        draft_seconds = time.perf_counter() - draft_started_at
        artifact_started_at = time.perf_counter()
        saved = save_character_roster(
            draft,
            draft_target,
            source_text=source_text,
            expected_status="draft",
        )
        verified = read_character_roster(
            draft_target,
            source_text=source_text,
            expected_status="draft",
        )

        if verified != saved:
            raise RuntimeError(
                "Character roster draft verification did not match the saved "
                "artifact."
            )

        clear_roster_discovery_state(state_target)
        artifact_seconds = (
            time.perf_counter()
            - artifact_started_at
        )
        _record_roster_event_metrics(
            active_metrics_path,
            event="finalization",
            timing={
                "attempts": 0,
                "corrective_retries": 0,
                "prompt_tokens": None,
                "output_tokens": None,
                "validation_mode": None,
                "phases_seconds": {
                    "draft_build": draft_seconds,
                    "artifact_write": artifact_seconds,
                    "finalization": (
                        time.perf_counter()
                        - finalization_started_at
                    ),
                },
            },
            mark_complete=True,
        )
    except Exception as exc:
        _mark_roster_metrics_failed(
            active_metrics_path,
            exc,
        )
        raise
    print(
        f"Character roster draft finalized with "
        f"{len(saved['entries'])} entries."
    )
    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover an evidence-backed whole-book character roster."
    )
    parser.add_argument("source_path")
    parser.add_argument("--replace-draft", action="store_true")
    parser.add_argument("--passage-size", type=int)
    parser.add_argument("--overlap-chars", type=int)
    parser.add_argument("--config-path")
    parser.add_argument("--state-path")
    parser.add_argument("--draft-path")
    parser.add_argument("--approved-path")
    parser.add_argument("--metrics-path")
    args = parser.parse_args(argv)

    app_dir = Path(__file__).resolve().parent
    root = app_dir.parent

    try:
        run_roster_discovery(
            args.source_path,
            config_path=Path(args.config_path) if args.config_path else app_dir / "config.json",
            state_path=Path(args.state_path) if args.state_path else root / "character_roster_state.json",
            draft_path=Path(args.draft_path) if args.draft_path else root / "character_roster.draft.json",
            approved_path=Path(args.approved_path) if args.approved_path else root / "character_roster.json",
            replace_draft=args.replace_draft,
            passage_size_override=args.passage_size,
            overlap_override=args.overlap_chars,
            metrics_path=(
                Path(args.metrics_path)
                if args.metrics_path
                else root / "logs" / "stages" / "roster_metrics.json"
            ),
        )
    except Exception as exc:
        print(f"Roster discovery failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
