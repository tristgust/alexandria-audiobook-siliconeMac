#!/usr/bin/env python3
"""Unblind the four Chris/Roz follow-up review exports.

This is research-only evidence processing. It never mutates Alexandria voices,
production audio, or user-owned source recordings.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS = Path("/Users/tristan/Downloads")
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-roz-followup-results-v1"

EXPORTS = {
    "source_repair": "chris_canonical_reference_repair_v1-tristan.json",
    "repair_pairwise": "alexandria_chris_reference_repair_pairwise_v1-tristan.json",
    "model_pairwise": "alexandria_chris_roz_pairwise_v1-tristan.json",
    "urgency": "alexandria_chris_urgency_control_review_v1-tristan.json",
}
ANSWER_KEYS = {
    "source_repair": ROOT / ".omo/evidence/chris-canonical-reference-repair-v1/private/answer-key.json",
    "repair_pairwise": ROOT / ".omo/evidence/chris-reference-repair-pairwise-v1/private/answer-key.json",
    "model_pairwise": ROOT / ".omo/evidence/chris-roz-pairwise-v1/private/answer-key.json",
    "urgency": ROOT / ".omo/evidence/chris-urgency-control-review-v1/private/answer-key.json",
}


class FollowupAnalysisError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise FollowupAnalysisError(f"Required JSON file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def require_round(payload: Mapping[str, Any], expected: str, path: Path) -> None:
    if payload.get("round_id") != expected:
        raise FollowupAnalysisError(
            f"Unexpected round ID in {path}: {payload.get('round_id')!r}"
        )


def source_repair_report(export: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    require_round(export, "chris_canonical_reference_repair_v1", Path(EXPORTS["source_repair"]))
    rows = []
    for blind_id, score in export["scores"].items():
        source = answer["candidates"].get(blind_id)
        if source is None:
            raise FollowupAnalysisError(f"Unknown source-repair candidate: {blind_id}")
        rows.append(
            {
                "blind_id": blind_id,
                "variant": source["key"],
                "method": source["method"],
                "settings": source.get("settings") or {},
                "human": dict(score),
                "objective": dict(source["metrics"]),
            }
        )
    retained = [row for row in rows if row["human"].get("retain") is True]
    retained.sort(
        key=lambda row: (
            -int(row["human"].get("dryness") or 99),
            float(row["objective"]["srmr"]),
            float(row["objective"]["speaker_cosine"]),
        ),
        reverse=True,
    )
    preferred = next(
        (row for row in retained if row["variant"] == "mossformer2_demucs"),
        retained[0] if retained else None,
    )
    return {
        "candidate_count": len(rows),
        "retained_count": len(retained),
        "dryness_scale_interpretation": "1 means least residual echo/background; 5 means most",
        "selected_variant": preferred["variant"] if preferred else None,
        "selected_reason": (
            "Human-retained, explicitly described as very good, lowest residual-echo score, "
            "exact transcript, and strongest dereverberation score among the retained variants."
            if preferred else None
        ),
        "retained_variants": [row["variant"] for row in retained],
        "rows": rows,
    }


def repair_pairwise_report(export: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    require_round(export, "alexandria_chris_reference_repair_pairwise_v1", Path(EXPORTS["repair_pairwise"]))
    rows = []
    tally = Counter()
    by_model: dict[str, Counter[str]] = defaultdict(Counter)
    for pair_id, score in export["results"].items():
        source = answer["pairs"].get(pair_id)
        if source is None:
            raise FollowupAnalysisError(f"Unknown repair pair: {pair_id}")
        choice = str(score.get("choice") or "")
        if choice == "tie":
            winner = "tie"
        elif choice in {"a", "b"}:
            winner = source[f"{choice}_reference"]
        else:
            raise FollowupAnalysisError(f"Invalid pairwise choice for {pair_id}: {choice!r}")
        tally[winner] += 1
        by_model[source["model_key"]][winner] += 1
        rows.append(
            {
                "pair_id": pair_id,
                "model_key": source["model_key"],
                "style": source["style"],
                "a_reference": source["a_reference"],
                "b_reference": source["b_reference"],
                "choice": choice,
                "winner": winner,
                "notes": score.get("notes") or "",
            }
        )
    return {
        "pair_count": len(rows),
        "tally": dict(tally),
        "by_model": {key: dict(value) for key, value in sorted(by_model.items())},
        "tested_repair_variant": "mossformer2_blend_70",
        "disposition": (
            "Superseded for source-repair validation because the human source review selected "
            "mossformer2_demucs instead of mossformer2_blend_70. Preserve as negative/mixed evidence."
        ),
        "rows": rows,
    }


def model_pairwise_report(export: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    require_round(export, "alexandria_chris_roz_pairwise_v1", Path(EXPORTS["model_pairwise"]))
    rows = []
    winners: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for score in export["rows"]:
        pair_id = str(score["pair_id"])
        source = answer["pairs"].get(pair_id)
        if source is None:
            raise FollowupAnalysisError(f"Unknown model pair: {pair_id}")
        choice = str(score.get("choice") or "")
        winner = "tie" if choice == "tie" else source[choice]["model_key"]
        cell = (source["identity_key"], source["style"])
        winners[cell][winner] += 1
        rows.append(
            {
                "pair_id": pair_id,
                "identity_key": source["identity_key"],
                "style": source["style"],
                "repeat": source["repeat"],
                "a_model": source["a"]["model_key"],
                "b_model": source["b"]["model_key"],
                "choice": choice,
                "winner": winner,
                "notes": score.get("notes") or "",
            }
        )
    decisions = []
    for (identity, style), tally in sorted(winners.items()):
        winner, count = tally.most_common(1)[0]
        decisions.append(
            {
                "identity_key": identity,
                "style": style,
                "winner": winner,
                "wins": count,
                "pair_count": sum(tally.values()),
                "tally": dict(tally),
            }
        )
    return {"pair_count": len(rows), "decisions": decisions, "rows": rows}


def urgency_report(export: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    require_round(export, "alexandria_chris_urgency_control_review_v1", Path(EXPORTS["urgency"]))
    rows = []
    for blind_id, score in export["scores"].items():
        source = answer["samples"].get(blind_id)
        if source is None:
            raise FollowupAnalysisError(f"Unknown urgency candidate: {blind_id}")
        rows.append(
            {
                "blind_id": blind_id,
                "model_key": source["model_key"],
                "variant": source["variant"],
                "control": source.get("control") or {},
                "emotion_reference": source.get("emotion_reference") or {},
                "human": dict(score),
            }
        )
    retained = [row for row in rows if row["human"].get("retain") is True]
    retained.sort(
        key=lambda row: (
            row["human"].get("mode_clear") is True,
            int(row["human"].get("delivery") or 0),
            int(row["human"].get("identity") or 0),
            int(row["human"].get("naturalness") or 0),
            -int(row["human"].get("artifacts") or 5),
        ),
        reverse=True,
    )
    primary = next(
        (
            row for row in retained
            if row["model_key"] == "indextts2_matched_control"
            and row["variant"] == "protective_a100"
        ),
        retained[0] if retained else None,
    )
    alternate = next(
        (
            row for row in retained
            if row["model_key"] == "indextts2_matched_control"
            and row["variant"] == "protective_a085"
        ),
        None,
    )
    return {
        "sample_count": len(rows),
        "retained_count": len(retained),
        "primary": (
            {"model_key": primary["model_key"], "variant": primary["variant"]}
            if primary else None
        ),
        "alternate": (
            {"model_key": alternate["model_key"], "variant": alternate["variant"]}
            if alternate else None
        ),
        "voxcpm_first_word_cut_count": sum(
            row["model_key"] == "voxcpm2_controllable_clone"
            and 'cut out the "Get"' in str(row["human"].get("notes") or "")
            for row in rows
        ),
        "rows": rows,
    }


def routing_decisions(model: Mapping[str, Any], urgency: Mapping[str, Any]) -> dict[str, Any]:
    pairwise = {
        (row["identity_key"], row["style"]): row["winner"]
        for row in model["decisions"]
    }
    return {
        "identity_conditioning": {
            "chris": "clean_actor",
            "roz": "clean_actor",
            "canonical_character_audio": "emotion_or_delivery_reference_only",
        },
        "chris": {
            "neutral": "indextts2_matched_control",
            "dry_humour": pairwise[("chris", "dry_humour")],
            "urgent_authority": urgency["primary"],
            "urgent_authority_alternate": urgency["alternate"],
            "vulnerability": "fish_s2_pro_cloud",
        },
        "roz": {
            "neutral": pairwise[("roz", "neutral")],
            "dry_humour": "voxcpm2_controllable_clone",
            "urgent_authority": "indextts2_matched_control",
            "vulnerability": "fish_s2_pro_cloud",
        },
        "tnia_miller_included": False,
        "production_promotion_allowed": False,
    }


def markdown_report(report: Mapping[str, Any]) -> str:
    source = report["source_repair"]
    repair = report["repair_pairwise"]
    model = report["model_pairwise"]
    urgency = report["urgency"]
    lines = [
        "# Chris and Roz follow-up review results",
        "",
        f"Generated: {report['analyzed_at']}",
        "",
        "## Chris source repair",
        "",
        f"Selected source variant: `{source['selected_variant']}`.",
        f"Retained variants: {', '.join(f'`{value}`' for value in source['retained_variants'])}.",
        "",
        "The earlier old-versus-repaired clone comparison is retained as mixed evidence but is superseded because it tested `mossformer2_blend_70`, not the selected `mossformer2_demucs` source.",
        "",
        "## Model tie-breakers",
        "",
    ]
    for row in model["decisions"]:
        lines.append(
            f"- {row['identity_key']} / {row['style']}: `{row['winner']}` won {row['wins']} of {row['pair_count']}."
        )
    lines.extend(
        [
            "",
            "## Chris urgency",
            "",
            f"Primary: `{urgency['primary']['model_key']}` / `{urgency['primary']['variant']}`.",
            f"Alternate: `{urgency['alternate']['model_key']}` / `{urgency['alternate']['variant']}`.",
            f"VoxCPM2 cut the first word in {urgency['voxcpm_first_word_cut_count']} reviewed samples.",
            "",
            "## Product state",
            "",
            "No production voice assignment or audiobook audio was changed.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    downloads = args.downloads.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    exports = {key: read_json(downloads / filename) for key, filename in EXPORTS.items()}
    answers = {key: read_json(path) for key, path in ANSWER_KEYS.items()}

    source = source_repair_report(exports["source_repair"], answers["source_repair"])
    repair = repair_pairwise_report(exports["repair_pairwise"], answers["repair_pairwise"])
    model = model_pairwise_report(exports["model_pairwise"], answers["model_pairwise"])
    urgency = urgency_report(exports["urgency"], answers["urgency"])
    report = {
        "schema_version": 1,
        "analysis_id": "alexandria_chris_roz_followup_results_v1",
        "analyzed_at": utc_now(),
        "source_repair": source,
        "repair_pairwise": repair,
        "model_pairwise": model,
        "urgency": urgency,
        "routing": routing_decisions(model, urgency),
        "tnia_miller_included": False,
        "production_promotion_allowed": False,
    }
    write_json(output / "report.json", report)
    (output / "report.md").write_text(markdown_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(output / "report.json"),
                "selected_source_repair": source["selected_variant"],
                "model_decisions": model["decisions"],
                "urgency_primary": urgency["primary"],
                "superseded_repair_pairwise": repair["tested_repair_variant"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
