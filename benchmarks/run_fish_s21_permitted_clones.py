#!/usr/bin/env python3
"""Generate separate Fish S2.1 blind reviews for permitted human voice references."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from fish_s21_blind_contract import (
    DEFAULT_ROUND1_ROOT,
    build_prompt,
    sha256_file,
)
from run_fish_s21_blind_test import (
    API_BASE,
    FishBlindRunError,
    FishClient,
    baseline_samples,
    build_review_package,
    delete_remote_models,
    ensure_models,
    find_existing_fish_samples,
    generate_fish_samples,
    write_json,
    audio_metadata,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "benchmarks" / "fish_s21_permitted_clones.json"
DEFAULT_OUTPUT_ROOT = ROOT / ".omo/evidence/fish-s21-permitted-clones"
REQUIRED_IDENTITIES = {"narrator", "benny", "doctor"}
REQUIRED_TIERS = {"conditioning", "full_source"}
REQUIRED_STYLES = {"neutral", "grief", "sarcastic", "fear"}
REQUIRED_MODES = {"alexandria_exact", "fish_optimized"}
REQUIRED_BASELINES = {
    "indextts2",
    "voxcpm2",
    "fish_s2_pro",
    "chatterbox_multilingual_v3",
}


class PermittedCloneContractError(ValueError):
    """Raised when the consented cloud-evaluation contract is incomplete."""


def _unique(rows: list[Mapping[str, Any]], subject: str) -> set[str]:
    values: set[str] = set()
    for row in rows:
        key = str(row.get("key") or "").strip()
        if not key or key in values:
            raise PermittedCloneContractError(f"Duplicate or empty {subject}: {key!r}.")
        values.add(key)
    return values


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise PermittedCloneContractError("Unsupported permitted-clone schema.")
    if payload.get("provider") != "fish_audio":
        raise PermittedCloneContractError("Provider must remain fish_audio.")
    if payload.get("api_model_header") != "s2.1-pro-free":
        raise PermittedCloneContractError("The free S2.1 model header changed.")
    permission = payload.get("permission")
    if not isinstance(permission, Mapping) or permission.get("confirmed_by_user") is not True:
        raise PermittedCloneContractError("Explicit user permission is required.")
    identities = payload.get("identities")
    tiers = payload.get("reference_tiers")
    styles = payload.get("styles")
    modes = payload.get("prompt_modes")
    baselines = payload.get("baseline_models")
    if not all(isinstance(value, list) and value for value in (identities, tiers, styles, modes, baselines)):
        raise PermittedCloneContractError("Identities, tiers, styles, modes, and baselines are required.")
    if _unique(identities, "identity") != REQUIRED_IDENTITIES:
        raise PermittedCloneContractError("The permitted identity set changed.")
    if _unique(tiers, "reference tier") != REQUIRED_TIERS:
        raise PermittedCloneContractError("The permitted reference tiers changed.")
    if _unique(styles, "style") != REQUIRED_STYLES:
        raise PermittedCloneContractError("The delivery style set changed.")
    if _unique(modes, "prompt mode") != REQUIRED_MODES:
        raise PermittedCloneContractError("The prompt-mode set changed.")
    if set(map(str, baselines)) != REQUIRED_BASELINES:
        raise PermittedCloneContractError("The balanced baseline set changed.")
    for identity in identities:
        relative = Path(str(identity.get("reference_manifest") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise PermittedCloneContractError(f"Unsafe reference manifest for {identity.get('key')!r}.")
    for style in styles:
        for field in ("target_text", "alexandria_instruction", "fish_instruction"):
            if not str(style.get(field) or "").strip():
                raise PermittedCloneContractError(f"Style {style['key']!r} is missing {field!r}.")
        build_prompt(style, "alexandria_exact")
        build_prompt(style, "fish_optimized")
    if int(payload.get("generation", {}).get("repeats") or 0) < 2:
        raise PermittedCloneContractError("At least two generations per cell are required.")
    return payload


def _resolved_under(root: Path, relative: str, subject: str) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise PermittedCloneContractError(f"{subject} escaped the evidence root.") from exc
    return target


def prepare_reference_audio(
    source: Path,
    *,
    source_sha256: str,
    prepared_root: Path,
    tier_key: str,
) -> tuple[Path, dict[str, Any]]:
    try:
        metadata = audio_metadata(source)
        return source, metadata
    except FishBlindRunError:
        pass
    prepared_root.mkdir(parents=True, exist_ok=True)
    target = prepared_root / f"{tier_key}.wav"
    receipt_path = prepared_root / f"{tier_key}.json"
    contract = {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "sample_rate": 24000,
        "channels": 1,
        "codec": "pcm_s16le",
    }
    if target.is_file() and receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                receipt.get("contract") == contract
                and receipt.get("prepared_sha256") == sha256_file(target)
            ):
                return target, audio_metadata(target)
        except (OSError, ValueError, json.JSONDecodeError, FishBlindRunError):
            pass
    temporary = target.with_name(f".{target.stem}.preparing.wav")
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise PermittedCloneContractError(
            f"Reference normalization failed for {source.name}: {completed.stderr[-500:]}"
        )
    os.replace(temporary, target)
    metadata = audio_metadata(target)
    write_json(
        receipt_path,
        {
            "contract": contract,
            "prepared_sha256": sha256_file(target),
            "audio": metadata,
        },
    )
    return target, metadata


def identity_tiers(
    round1_root: Path,
    config: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    prepared_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = _resolved_under(
        round1_root,
        str(identity["reference_manifest"]),
        f"{identity['key']} reference manifest",
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("identity_key") != identity["key"] or payload.get("label") != identity["label"]:
        raise PermittedCloneContractError(f"Reference identity mismatch in {manifest_path}.")
    reference_root = manifest_path.parent.parent
    tiers: list[dict[str, Any]] = []
    for tier in config["reference_tiers"]:
        audio_path = _resolved_under(
            reference_root,
            str(payload.get(tier["audio_field"]) or ""),
            f"{identity['key']} {tier['key']} audio",
        )
        if not audio_path.is_file():
            raise PermittedCloneContractError(f"Reference audio is missing: {audio_path}")
        expected_audio = str(payload.get(tier["audio_sha256_field"]) or "")
        if sha256_file(audio_path) != expected_audio:
            raise PermittedCloneContractError(f"Reference audio hash changed: {audio_path}")
        text = str(payload.get(tier["text_field"]) or "").strip()
        expected_text = str(payload.get(tier["text_sha256_field"]) or "")
        if not text or hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_text:
            raise PermittedCloneContractError(
                f"Reference transcript hash changed for {identity['key']} {tier['key']}."
            )
        prepared_audio, metadata = prepare_reference_audio(
            audio_path,
            source_sha256=expected_audio,
            prepared_root=prepared_root,
            tier_key=str(tier["key"]),
        )
        tiers.append(
            {
                "key": tier["key"],
                "label": tier["label"],
                "entries": [
                    {
                        "audio_path": str(prepared_audio),
                        "audio_sha256": sha256_file(prepared_audio),
                        "source_audio_sha256": expected_audio,
                        "text": text,
                        "text_sha256": expected_text,
                    }
                ],
                "duration_seconds": metadata["duration_seconds"],
                "source_manifest": str(manifest_path),
            }
        )
    return tiers, payload


def per_identity_config(config: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, Any]:
    identity_key = str(identity["key"])
    return {
        "schema_version": 1,
        "round_id": f"{config['round_id']}_{identity_key}",
        "provider": config["provider"],
        "marketed_model": config["marketed_model"],
        "api_model_header": config["api_model_header"],
        "identity": {
            "key": identity_key,
            "label": identity["label"],
            "source_kind": "permitted_human_recording",
            "permission_confirmed_by_user": True,
            "permission_confirmed_date": config["permission"]["confirmed_date"],
        },
        "reference_description": (
            f"Alexandria consented Fish S2.1 evaluation reference for {identity['label']}; "
            "private, evaluation-only, and not promoted to production."
        ),
        "reference_tiers": [dict(row) for row in config["reference_tiers"]],
        "prompt_modes": [dict(row) for row in config["prompt_modes"]],
        "styles": [dict(row) for row in config["styles"]],
        "baseline_candidates": [
            {"model_key": model, "identity_key": identity_key}
            for model in config["baseline_models"]
        ],
        "generation": dict(config["generation"]),
    }


def _selected_identities(config: Mapping[str, Any], requested: list[str]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in config["identities"]]
    if not requested:
        return rows
    requested_set = set(requested)
    unknown = requested_set - {str(row["key"]) for row in rows}
    if unknown:
        raise PermittedCloneContractError(f"Unknown identities: {sorted(unknown)}")
    return [row for row in rows if row["key"] in requested_set]


def api_key_from_environment() -> str:
    return os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY") or ""


def write_parent_manifest(output_root: Path, config: Mapping[str, Any], rows: list[dict[str, Any]]) -> None:
    write_json(
        output_root / "manifest.json",
        {
            "schema_version": 1,
            "round_id": config["round_id"],
            "permission": config["permission"],
            "identity_count": len(rows),
            "identities": rows,
            "remote_credentials_persisted": False,
            "production_promotion_allowed": False,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--round1-root", default=str(DEFAULT_ROUND1_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--identity", action="append", default=[])
    parser.add_argument("--package-only", action="store_true")
    parser.add_argument("--delete-remote-voices", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    round1_root = Path(args.round1_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    selected = _selected_identities(config, list(args.identity))
    key = api_key_from_environment()
    client: FishClient | None = None
    if args.delete_remote_voices or not args.package_only:
        client = FishClient(
            api_key=key,
            model_header=str(config["api_model_header"]),
            base_url=args.api_base,
            max_attempts=int(config["generation"]["max_attempts"]),
        )

    summaries: list[dict[str, Any]] = []
    for identity in selected:
        identity_key = str(identity["key"])
        identity_root = output_root / identity_key
        identity_root.mkdir(parents=True, exist_ok=True)
        identity_config = per_identity_config(config, identity)
        tiers, reference_manifest = identity_tiers(
            round1_root,
            config,
            identity,
            prepared_root=identity_root / "private/prepared-references",
        )
        if args.delete_remote_voices:
            deleted = delete_remote_models(client, identity_root, identity_config["round_id"])
            summaries.append({"identity": identity_key, **deleted})
            continue
        if args.package_only:
            fish_samples = find_existing_fish_samples(
                output_root=identity_root,
                config=identity_config,
            )
        else:
            models = ensure_models(
                client,
                output_root=identity_root,
                config=identity_config,
                tiers=tiers,
            )
            fish_samples = generate_fish_samples(
                client,
                output_root=identity_root,
                config=identity_config,
                tiers=tiers,
                models=models,
            )
        manifest = build_review_package(
            output_root=identity_root,
            round1_root=round1_root,
            config=identity_config,
            tiers=tiers,
            fish_samples=fish_samples,
        )
        manifest["permission_confirmed_by_user"] = True
        manifest["permission_confirmed_date"] = config["permission"]["confirmed_date"]
        manifest["reference_source_sha256"] = reference_manifest["source_sha256"]
        manifest["reference_conditioning_sha256"] = reference_manifest["conditioning_sha256"]
        write_json(identity_root / "manifest.json", manifest)
        summaries.append(
            {
                "identity": identity_key,
                "label": identity["label"],
                "sample_count": manifest["sample_count"],
                "fish_sample_count": manifest["fish_sample_count"],
                "baseline_sample_count": manifest["baseline_sample_count"],
                "reference_tiers": {
                    tier["key"]: tier["duration_seconds"] for tier in tiers
                },
                "review_root": manifest["review_root"],
                "answer_key": manifest["answer_key"],
                "partial": manifest["partial"],
            }
        )

    write_parent_manifest(output_root, config, summaries)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": config["round_id"],
                "identity_count": len(summaries),
                "identities": summaries,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
