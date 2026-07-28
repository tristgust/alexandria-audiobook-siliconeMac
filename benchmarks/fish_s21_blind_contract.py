#!/usr/bin/env python3
"""Contract and deterministic helpers for the Fish S2.1 Pro blind test."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "benchmarks" / "fish_s21_blind_test.json"
DEFAULT_ROUND1_ROOT = Path(
    os.environ.get(
        "ALEXANDRIA_MULTIMODEL_ROUND1_ROOT",
        str(
            Path.home()
            / ".devspace/worktrees/alexandria-research-multimodel-voice-benchmark"
            / ".omo/evidence/b17-t05-multimodel-round1"
        ),
    )
).expanduser()
DEFAULT_OUTPUT_ROOT = ROOT / ".omo/evidence/fish-s21-pro-blind-test"


class FishBlindContractError(ValueError):
    """Raised when a blind-test contract or source is unsafe or inconsistent."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_config(payload)
    return payload


def _unique_keys(rows: Iterable[Mapping[str, Any]], subject: str) -> set[str]:
    result: set[str] = set()
    for row in rows:
        key = str(row.get("key") or "").strip()
        if not key or key in result:
            raise FishBlindContractError(f"Duplicate or empty {subject} key: {key!r}.")
        result.add(key)
    return result


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise FishBlindContractError("Unsupported Fish blind-test schema version.")
    if config.get("provider") != "fish_audio":
        raise FishBlindContractError("The provider must remain fish_audio.")
    if config.get("api_model_header") != "s2.1-pro-free":
        raise FishBlindContractError(
            "The Fish S2.1 Pro free evaluation header must be s2.1-pro-free."
        )
    identity = config.get("identity")
    if not isinstance(identity, Mapping):
        raise FishBlindContractError("Identity configuration is missing.")
    if identity.get("source_kind") != "synthetic_qwen_custom_voice":
        raise FishBlindContractError(
            "Cloud evaluation is restricted to the synthetic Ryan reference."
        )
    tiers = config.get("reference_tiers")
    modes = config.get("prompt_modes")
    styles = config.get("styles")
    baselines = config.get("baseline_candidates")
    if not all(isinstance(value, list) and value for value in (tiers, modes, styles, baselines)):
        raise FishBlindContractError("Reference tiers, prompt modes, styles, and baselines are required.")
    tier_keys = _unique_keys(tiers, "reference tier")
    mode_keys = _unique_keys(modes, "prompt mode")
    style_keys = _unique_keys(styles, "style")
    if tier_keys != {"short_5s", "standard_10s", "long_30s"}:
        raise FishBlindContractError("The three reference-length tiers changed.")
    if mode_keys != {"alexandria_exact", "fish_optimized"}:
        raise FishBlindContractError("The two prompt modes changed.")
    if style_keys != {"neutral", "grief", "sarcastic", "fear"}:
        raise FishBlindContractError("The four binding delivery styles changed.")
    for tier in tiers:
        entries = tier.get("entries")
        if not isinstance(entries, list) or not entries:
            raise FishBlindContractError(f"Reference tier {tier['key']!r} has no entries.")
    for style in styles:
        for field in ("label", "target_text", "alexandria_instruction", "fish_instruction"):
            if not str(style.get(field) or "").strip():
                raise FishBlindContractError(
                    f"Style {style['key']!r} is missing {field!r}."
                )
    baseline_pairs: set[tuple[str, str]] = set()
    for row in baselines:
        pair = (str(row.get("model_key") or ""), str(row.get("identity_key") or ""))
        if not all(pair) or pair in baseline_pairs:
            raise FishBlindContractError(f"Invalid duplicate baseline candidate: {pair!r}.")
        baseline_pairs.add(pair)
    generation = config.get("generation")
    if not isinstance(generation, Mapping):
        raise FishBlindContractError("Generation configuration is missing.")
    if int(generation.get("repeats") or 0) < 2:
        raise FishBlindContractError("At least two Fish generations per cell are required.")
    for field in ("temperature", "top_p"):
        value = float(generation.get(field))
        if not 0.0 <= value <= 1.0:
            raise FishBlindContractError(f"{field} must remain between zero and one.")


def build_prompt(style: Mapping[str, Any], mode_key: str) -> str:
    if mode_key == "alexandria_exact":
        instruction = str(style["alexandria_instruction"]).strip()
    elif mode_key == "fish_optimized":
        instruction = str(style["fish_instruction"]).strip()
    else:
        raise FishBlindContractError(f"Unsupported prompt mode: {mode_key!r}.")
    target = str(style["target_text"]).strip()
    return f"[{instruction}] {target}"


def load_ryan_reference_entries(
    round1_root: str | Path,
    config: Mapping[str, Any],
) -> tuple[Path, dict[str, dict[str, Any]]]:
    root = Path(round1_root).expanduser().resolve()
    relative = Path(str(config["identity"]["reference_manifest"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise FishBlindContractError("Ryan reference manifest path is unsafe.")
    manifest_path = (root / relative).resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise FishBlindContractError("Ryan reference manifest escaped the evidence root.") from exc
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("voice") != "Ryan":
        raise FishBlindContractError("The reference manifest is not the synthetic Ryan voice.")
    neutral = payload.get("neutral")
    acted = payload.get("acted")
    if not isinstance(neutral, Mapping) or not isinstance(acted, list):
        raise FishBlindContractError("Ryan reference manifest is incomplete.")
    if not str(neutral.get("kind") or "").startswith("built_in_qwen_custom_voice"):
        raise FishBlindContractError("Ryan neutral reference is not synthetic model output.")
    entries: dict[str, dict[str, Any]] = {"neutral": dict(neutral)}
    for row in acted:
        if not isinstance(row, Mapping):
            continue
        style = str(row.get("style") or "").strip()
        if not style:
            continue
        if not str(row.get("kind") or "").startswith("built_in_qwen_custom_voice"):
            raise FishBlindContractError(f"Ryan acted reference {style!r} is not synthetic.")
        entries[f"acted_{style}"] = dict(row)
    required = {
        str(entry)
        for tier in config["reference_tiers"]
        for entry in tier["entries"]
    }
    missing = sorted(required - set(entries))
    if missing:
        raise FishBlindContractError(f"Ryan reference entries are missing: {missing}.")
    reference_root = manifest_path.parent
    for key in required:
        row = entries[key]
        audio_path = (reference_root / str(row.get("audio_file") or "")).resolve()
        try:
            audio_path.relative_to(reference_root)
        except ValueError as exc:
            raise FishBlindContractError(f"Unsafe Ryan audio path for {key!r}.") from exc
        if not audio_path.is_file():
            raise FishBlindContractError(f"Ryan audio is missing for {key!r}: {audio_path}")
        expected = str(row.get("audio_sha256") or "")
        if expected != sha256_file(audio_path):
            raise FishBlindContractError(f"Ryan audio hash changed for {key!r}.")
        if not str(row.get("text") or "").strip():
            raise FishBlindContractError(f"Ryan transcript is missing for {key!r}.")
        row["resolved_audio_path"] = str(audio_path)
    return manifest_path, entries


def reference_tier_payloads(
    round1_root: str | Path,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    manifest_path, entries = load_ryan_reference_entries(round1_root, config)
    result: list[dict[str, Any]] = []
    for tier in config["reference_tiers"]:
        selected = [entries[str(key)] for key in tier["entries"]]
        duration = sum(float(row.get("audio", {}).get("duration_seconds") or 0.0) for row in selected)
        result.append(
            {
                "key": tier["key"],
                "label": tier["label"],
                "entries": [
                    {
                        "audio_path": row["resolved_audio_path"],
                        "audio_sha256": row["audio_sha256"],
                        "text": row["text"],
                        "text_sha256": row["text_sha256"],
                    }
                    for row in selected
                ],
                "duration_seconds": duration,
                "source_manifest": str(manifest_path),
            }
        )
    return result


def sample_fingerprint(payload: Mapping[str, Any]) -> str:
    return sha256_value(payload)


def blind_id(secret: str, fingerprint: str) -> str:
    if len(secret) < 32:
        raise FishBlindContractError("Blind-test secret must be at least 32 characters.")
    return hashlib.sha256(f"{secret}:{fingerprint}".encode("utf-8")).hexdigest()[:20]


def expected_counts(config: Mapping[str, Any]) -> dict[str, int]:
    styles = len(config["styles"])
    baselines = styles * len(config["baseline_candidates"])
    fish = (
        styles
        * len(config["reference_tiers"])
        * len(config["prompt_modes"])
        * int(config["generation"]["repeats"])
    )
    return {"baseline": baselines, "fish": fish, "total": baselines + fish}
