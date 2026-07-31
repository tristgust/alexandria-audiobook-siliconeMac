#!/usr/bin/env python3
"""Build a no-mutation Original Sin promotion manifest from blind-approved evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROMOTION_ID = "alexandria_original_sin_overlap_promotion_v1"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_PLAN = Path(__file__).with_name("original_sin_overlap_promotion_plan_v1.json")


class PromotionManifestError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionManifestError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_hashes(project: Path) -> dict[str, str]:
    return {
        name: sha256_file(project / name)
        for name in ("voice_config.json", "chunks.json")
    }


def candidate_index(workflow: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for answer_key in workflow.rglob("answer-key.json"):
        payload = read_json(answer_key)
        candidates = payload.get("candidates")
        if not isinstance(candidates, dict):
            continue
        for candidate_id, row in candidates.items():
            if candidate_id in index:
                prior = index[candidate_id]
                prior_row = prior["candidate"]
                prior_identity = (
                    prior_row.get("path") or prior_row.get("wav_path"),
                    (prior_row.get("metrics") or prior_row.get("wav_metrics") or {}).get("sha256"),
                    prior_row.get("proxy_path"),
                    prior_row.get("proxy_sha256"),
                )
                new_identity = (
                    row.get("path") or row.get("wav_path"),
                    (row.get("metrics") or row.get("wav_metrics") or {}).get("sha256"),
                    row.get("proxy_path"),
                    row.get("proxy_sha256"),
                )
                if prior_identity != new_identity:
                    raise PromotionManifestError(
                        f"Conflicting duplicate candidate id: {candidate_id}"
                    )
                prior["answer_key_paths"].append(answer_key)
                prior["round_ids"].append(payload.get("round_id"))
                continue
            index[candidate_id] = {
                "answer_key": answer_key,
                "answer_key_paths": [answer_key],
                "round_id": payload.get("round_id"),
                "round_ids": [payload.get("round_id")],
                "candidate": row,
            }
    return index


def resolve_candidate(index: dict[str, dict[str, Any]], candidate_id: str) -> dict[str, Any]:
    try:
        record = index[candidate_id]
    except KeyError as exc:
        raise PromotionManifestError(f"Missing candidate: {candidate_id}") from exc
    row = record["candidate"]
    audio_value = row.get("path") or row.get("wav_path")
    audio_path = Path(str(audio_value or "")).expanduser().resolve()
    expected_audio_hash = str(
        (row.get("metrics") or row.get("wav_metrics") or {}).get("sha256") or ""
    )
    if not audio_path.is_file() or not expected_audio_hash:
        raise PromotionManifestError(f"Candidate audio is incomplete: {candidate_id}")
    if sha256_file(audio_path) != expected_audio_hash:
        raise PromotionManifestError(f"Candidate audio changed: {candidate_id}")

    proxy_path = None
    proxy_hash = None
    if row.get("proxy_path"):
        proxy_path = Path(str(row["proxy_path"])).expanduser().resolve()
        proxy_hash = str(row.get("proxy_sha256") or "")
        if not proxy_path.is_file() or not proxy_hash or sha256_file(proxy_path) != proxy_hash:
            raise PromotionManifestError(f"Candidate proxy changed: {candidate_id}")

    return {
        "candidate_id": candidate_id,
        "source_round_id": record.get("round_id"),
        "source_round_ids": record.get("round_ids"),
        "answer_key_path": str(record["answer_key"]),
        "answer_key_paths": [str(path) for path in record.get("answer_key_paths", [])],
        "character": row.get("character"),
        "book_speaker": row.get("book_speaker"),
        "chunk_id": row.get("chunk_id"),
        "transcript": row.get("transcript") or row.get("text"),
        "treatment": row.get("treatment"),
        "route_key": row.get("route_key"),
        "actual_backend": row.get("actual_backend"),
        "fallback_used": bool(row.get("fallback_used")),
        "audio_path": str(audio_path),
        "audio_sha256": expected_audio_hash,
        "proxy_path": str(proxy_path) if proxy_path else None,
        "proxy_sha256": proxy_hash,
    }


def render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Original Sin overlap production-promotion manifest",
        "",
        "This manifest is preparation only. It does not authorize or perform production mutation.",
        "",
        "## Approved identity anchors",
        "",
        "| Character | Candidate | Transcript |",
        "|---|---|---|",
    ]
    for row in manifest["identity_anchors"]:
        lines.append(
            f"| {row['character']} | `{row['candidate_id']}` | {row['transcript']} |"
        )
    lines.extend([
        "",
        "## Expressive modes",
        "",
        "| Character | Mode | Primary | Backend | Alternates |",
        "|---|---|---|---|---|",
    ])
    for row in manifest["expressive_modes"]:
        alternates = ", ".join(f"`{item['candidate_id']}`" for item in row["alternates"]) or "—"
        lines.append(
            f"| {row['character']} | {row['mode']} | `{row['primary']['candidate_id']}` | "
            f"`{row['primary']['actual_backend']}` | {alternates} |"
        )
    lines.extend([
        "",
        "## Exact adaptation substitutions",
        "",
        "| Chunk | Character | Candidate | Transcript |",
        "|---:|---|---|---|",
    ])
    for row in manifest["direct_substitutions"]:
        lines.append(
            f"| {row['chunk_id']} | {row['character']} | `{row['candidate_id']}` | {row['transcript']} |"
        )
    lines.extend([
        "",
        "## Unresolved adaptation identities",
        "",
    ])
    for row in manifest["unresolved_characters"]:
        lines.append(f"- **{row['character']}** — {row['reason']}")
    lines.append("")
    return "\n".join(lines)


def build_manifest(project: Path, plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("promotion_id") != PROMOTION_ID:
        raise PromotionManifestError("Promotion plan id mismatch")
    if plan.get("installation_authorized") is not False or plan.get("production_changes") is not False:
        raise PromotionManifestError("Promotion plan must be no-mutation")
    workflow = project / "external_workflows/big_finish_overlap_reference_v1"
    index = candidate_index(workflow)
    before = project_hashes(project)

    identities = [
        {
            **resolve_candidate(index, candidate_id),
            "proposed_action": "add_reference_bank_identity",
        }
        for candidate_id in plan["identity_anchor_candidate_ids"]
    ]
    performance = []
    for spec in plan["adaptation_performance_references"]:
        performance.append(
            {
                **resolve_candidate(index, spec["candidate_id"]),
                "mode": spec["mode"],
                "proposed_action": "add_reference_bank_adaptation_performance",
            }
        )

    expressive = []
    for spec in plan["expressive_modes"]:
        primary = resolve_candidate(index, spec["primary_candidate_id"])
        if primary["fallback_used"]:
            raise PromotionManifestError(
                f"Primary expressive route used fallback: {primary['candidate_id']}"
            )
        alternates = [
            resolve_candidate(index, candidate_id)
            for candidate_id in spec.get("alternate_candidate_ids", [])
        ]
        if any(row["fallback_used"] for row in alternates):
            raise PromotionManifestError("Promotable alternate used fallback")
        restricted = [
            resolve_candidate(index, candidate_id)
            for candidate_id in spec.get("restricted_evidence_candidate_ids", [])
        ]
        if any(not row["fallback_used"] for row in restricted):
            raise PromotionManifestError("Restricted fallback evidence did not use fallback")
        expressive.append(
            {
                "character": spec["character"],
                "mode": spec["mode"],
                "primary": primary,
                "alternates": alternates,
                "restricted_fallback_evidence": restricted,
                "proposed_action": "add_generated_performance_reference",
            }
        )

    direct = []
    for candidate_id in plan["direct_substitution_candidate_ids"]:
        row = resolve_candidate(index, candidate_id)
        if row["chunk_id"] is None or not row["proxy_path"]:
            raise PromotionManifestError(f"Direct candidate is incomplete: {candidate_id}")
        direct.append(
            {
                **row,
                "proposed_action": "install_exact_adaptation_chunk_audio",
            }
        )

    after = project_hashes(project)
    if before != after:
        raise PromotionManifestError("Protected project state changed while building manifest")
    return {
        "schema_version": 1,
        "promotion_id": PROMOTION_ID,
        "project_root": str(project),
        "protected_project_hashes_before": before,
        "protected_project_hashes_after": after,
        "identity_anchor_count": len(identities),
        "adaptation_performance_reference_count": len(performance),
        "expressive_mode_count": len(expressive),
        "direct_substitution_count": len(direct),
        "unresolved_character_count": len(plan["unresolved_characters"]),
        "identity_anchors": identities,
        "adaptation_performance_references": performance,
        "expressive_modes": expressive,
        "direct_substitutions": sorted(direct, key=lambda row: int(row["chunk_id"])),
        "unresolved_characters": plan["unresolved_characters"],
        "installation_authorized": False,
        "requires_separate_promotion_receipt": True,
        "required_promotion_controls": [
            "snapshot voice_config.json and chunks.json before mutation",
            "copy selected assets into project-owned immutable paths",
            "verify copied hashes before references or chunks are rebound",
            "write explicit prior/new chunk bindings for every direct substitution",
            "write reference-bank provenance and primary/alternate mode ordering",
            "provide one-command rollback restoring files and bindings",
            "blind-listen to a production-context assembly before final export eligibility"
        ],
        "production_changes": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(
        args.project_root.expanduser().resolve(),
        read_json(args.plan.expanduser().resolve()),
    )
    write_json(args.output_json, manifest)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(manifest), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
