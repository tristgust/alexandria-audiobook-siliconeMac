from __future__ import annotations

import argparse
import json
import sys
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


DISCOVERY_SYSTEM_PROMPT = """You are performing evidence-bound whole-book character roster discovery for an audiobook pipeline. Return only JSON matching the requested roster_discovery schema.

Rules:
- Use only the supplied source passage.
- Record characters, groups, creatures, narrator roles, and named non-speakers when relevant.
- Do not merge uncertain or same-name identities. Keep them separate and mark ambiguity.
- Every entity requires exact source evidence.
- Evidence quote must be copied byte-for-byte from the passage.
- Evidence start_char and end_char are zero-based offsets relative to the supplied passage, with end_char exclusive.
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

    print(
        f"Roster discovery: {len(state['completed_passages'])}/"
        f"{state['total_passages']} passages already complete."
    )

    for passage in passages[len(state["completed_passages"]):]:
        prior = completed_observations(state)
        print(
            f"Discovering roster passage {passage['index']}/"
            f"{len(passages)} ({passage['start_char']}-"
            f"{passage['end_char']})..."
        )
        result = runtime.complete_json(
            messages=discovery_messages(
                passage=passage,
                total_passages=len(passages),
                prior_observations=prior,
            ),
            contract="roster_discovery",
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=0.8,
            seed=seed,
        )
        print_llm_metrics("Roster discovery", result)
        observations, warnings = normalize_passage_result(
            result.data,
            passage=passage,
            source_fingerprint=source["fingerprint"],
        )
        state = checkpoint_roster_passage(
            state=state,
            path=state_target,
            passage=passage,
            observations=observations,
            warnings=warnings,
        )
        print(
            f"Checkpointed passage {passage['index']} with "
            f"{len(observations)} observation(s)."
        )

    observations = completed_observations(state)

    if state["reconciliation"] is None:
        print(
            f"Reconciling {len(observations)} whole-book observation(s)..."
        )
        result = runtime.complete_json(
            messages=reconciliation_messages(observations),
            contract="roster_reconciliation",
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=0.8,
            seed=seed,
        )
        print_llm_metrics("Roster reconciliation", result)
        state = checkpoint_roster_reconciliation(
            state=state,
            path=state_target,
            reconciliation=result.data,
        )

    draft = build_draft_from_discovery_state(
        state,
        source_text=source_text,
        generated_at_utc=generated_at_utc,
    )
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
    args = parser.parse_args(argv)

    app_dir = Path(__file__).resolve().parent
    root = app_dir.parent

    try:
        run_roster_discovery(
            args.source_path,
            config_path=app_dir / "config.json",
            state_path=root / "character_roster_state.json",
            draft_path=root / "character_roster.draft.json",
            approved_path=root / "character_roster.json",
            replace_draft=args.replace_draft,
            passage_size_override=args.passage_size,
            overlap_override=args.overlap_chars,
        )
    except Exception as exc:
        print(f"Roster discovery failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
