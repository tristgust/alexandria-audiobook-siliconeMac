from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from character_roster import (
    build_source_snapshot,
    read_character_roster,
)
from character_visuals import persona_reference_targets
from generate_script import fix_mojibake
from llm_adapter import build_roster_client
from visual_discovery import (
    DEFAULT_PASSAGE_OVERLAP,
    DEFAULT_PASSAGE_SIZE,
    run_visual_discovery,
)


def _load_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Configuration could not be read: {exc}"
        ) from exc
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


def _selected_targets(
    approved_roster: dict[str, Any],
    entry_ids: list[str],
    persona_refs_dir: str | Path,
) -> dict[str, Path]:
    ownership = [
        {
            "entry_id": entry["id"],
            "character_name": (
                entry["canonical_name"]
                or entry["display_name"]
            ),
        }
        for entry in approved_roster["entries"]
    ]
    return persona_reference_targets(
        persona_refs_dir=persona_refs_dir,
        selected_entries=[
            item
            for item in ownership
            if item["entry_id"] in entry_ids
        ],
        all_entries=ownership,
    )


def run_persona_visual_discovery(
    *,
    enabled: bool,
    source_path: str | Path,
    approved_roster_path: str | Path,
    entry_ids: list[str],
    config_path: str | Path,
    state_path: str | Path,
    persona_refs_dir: str | Path,
    runtime_client: Any | None = None,
    compiled_at_utc: str | None = None,
    passage_size_override: int | None = None,
    overlap_override: int | None = None,
    replace_existing: bool = False,
) -> dict[str, Any]:
    del compiled_at_utc  # Kept for compatibility with interrupted callers.

    if not enabled:
        return {"status": "disabled", "written": []}

    source, source_text = build_source_snapshot(
        Path(source_path).expanduser().resolve(),
        normalizer=fix_mojibake,
    )
    if not source_text:
        raise RuntimeError("The selected source book is empty.")

    approved = read_character_roster(
        approved_roster_path,
        source_text=source_text,
        expected_status="approved",
    )
    approved_ids = {
        entry["id"]
        for entry in approved["entries"]
    }
    if not entry_ids or len(entry_ids) != len(set(entry_ids)):
        raise RuntimeError(
            "Select at least one unique approved roster entry."
        )
    unknown_ids = sorted(set(entry_ids) - approved_ids)
    if unknown_ids:
        raise RuntimeError(
            "Selected approved roster entries were not found: "
            + ", ".join(unknown_ids)
        )

    config = _load_config(config_path)
    visual_config = (
        config.get("visual")
        if isinstance(config.get("visual"), dict)
        else {}
    )
    passage_size = _int_setting(
        (
            passage_size_override
            if passage_size_override is not None
            else visual_config.get("passage_size")
        ),
        DEFAULT_PASSAGE_SIZE,
        minimum=100,
    )
    overlap = _int_setting(
        (
            overlap_override
            if overlap_override is not None
            else visual_config.get("passage_overlap")
        ),
        DEFAULT_PASSAGE_OVERLAP,
        minimum=0,
    )
    if overlap >= passage_size:
        raise ValueError(
            "Visual passage overlap must be smaller than passage size."
        )
    temperature = _float_setting(
        visual_config.get("temperature"),
        0.1,
        minimum=0.0,
        maximum=2.0,
    )
    max_tokens = _int_setting(
        visual_config.get("max_tokens"),
        5000,
        minimum=256,
    )
    seed_value = visual_config.get("seed", 42)
    seed = (
        None
        if seed_value is None
        else _int_setting(seed_value, 42, minimum=0)
    )

    targets = _selected_targets(
        approved,
        entry_ids,
        persona_refs_dir,
    )
    runtime = runtime_client or build_roster_client(config)
    result = run_visual_discovery(
        runtime_client=runtime,
        source=source,
        source_text=source_text,
        approved_roster=approved,
        character_ids=entry_ids,
        state_path=state_path,
        persona_refs_dir=persona_refs_dir,
        passage_size=passage_size,
        overlap_chars=overlap,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        replace_existing=replace_existing,
    )
    return {
        **result,
        "entry_count": len(entry_ids),
        "written": [str(targets[entry_id]) for entry_id in entry_ids],
    }


def main(argv: list[str] | None = None) -> int:
    app_dir = Path(__file__).resolve().parent
    root = app_dir.parent
    parser = argparse.ArgumentParser(
        description=(
            "Collect optional evidence-backed character visual dossiers."
        )
    )
    parser.add_argument("source_path")
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument(
        "--entry-id",
        action="append",
        dest="entry_ids",
        default=[],
    )
    parser.add_argument("--passage-size", type=int)
    parser.add_argument("--overlap-chars", type=int)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument(
        "--config-path",
        default=str(app_dir / "config.json"),
    )
    parser.add_argument(
        "--state-path",
        default=str(root / "persona_visual_state.json"),
    )
    parser.add_argument(
        "--approved-roster-path",
        default=str(root / "character_roster.json"),
    )
    parser.add_argument(
        "--persona-refs-dir",
        default=str(root / "persona_refs"),
    )
    args = parser.parse_args(argv)

    result = run_persona_visual_discovery(
        enabled=args.enabled,
        source_path=args.source_path,
        approved_roster_path=args.approved_roster_path,
        entry_ids=args.entry_ids,
        config_path=args.config_path,
        state_path=args.state_path,
        persona_refs_dir=args.persona_refs_dir,
        passage_size_override=args.passage_size,
        overlap_override=args.overlap_chars,
        replace_existing=args.replace_existing,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
