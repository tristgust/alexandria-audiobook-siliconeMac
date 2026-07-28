#!/usr/bin/env python3
"""Build the four-identity Fish S2.1 prompt-control blind evaluation.

The round holds one reference model constant per identity and compares four
prompt conditions: no tag, a simple bracket tag, a rich natural-language tag,
and the full Alexandria delivery instruction in brackets. Existing rich/full
samples are reused only after their prompt and audio receipts are verified.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from fish_s21_blind_contract import (
    DEFAULT_ROUND1_ROOT,
    build_prompt,
    load_config as load_ryan_config,
    reference_tier_payloads,
    sample_fingerprint,
    sha256_bytes,
    sha256_file,
    sha256_value,
)
from run_fish_s21_blind_test import (
    API_BASE,
    FishBlindRunError,
    FishClient,
    GeneratedSample,
    audio_metadata,
    build_review_package,
    find_existing_fish_samples,
    generate_fish_samples,
    write_json,
)
from run_fish_s21_permitted_clones import (
    identity_tiers,
    load_config as load_permitted_config,
)

CONFIG_PATH = ROOT / "benchmarks/fish_s21_prompt_controls.json"
DEFAULT_OUTPUT_ROOT = ROOT / ".omo/evidence/fish-s21-prompt-controls"
DEFAULT_RYAN_SOURCE_ROOT = Path(
    os.environ.get(
        "ALEXANDRIA_FISH_RYAN_SOURCE_ROOT",
        str(
            Path.home()
            / ".devspace/worktrees/alexandria-research-fish-s21-blind-test"
            / ".omo/evidence/fish-s21-pro-blind-test"
        ),
    )
).expanduser()
DEFAULT_PERMITTED_SOURCE_ROOT = Path(
    os.environ.get(
        "ALEXANDRIA_FISH_PERMITTED_SOURCE_ROOT",
        str(
            Path.home()
            / ".devspace/worktrees/alexandria-research-fish-s21-permitted-clones"
            / ".omo/evidence/fish-s21-permitted-clones"
        ),
    )
).expanduser()
REQUIRED_IDENTITIES = {"ryan_synthetic", "narrator", "benny", "doctor"}
REQUIRED_MODES = {
    "untagged",
    "simple_tag",
    "rich_tag",
    "full_alexandria_tag",
}
REQUIRED_STYLES = {"neutral", "grief", "sarcastic", "fear"}
REQUIRED_BASELINES = {"indextts2", "voxcpm2", "chatterbox_multilingual_v3"}
REUSED_MODE_MAP = {
    "rich_tag": "fish_optimized",
    "full_alexandria_tag": "alexandria_exact",
}
GENERATED_MODES = {"untagged", "simple_tag"}


class PromptControlContractError(ValueError):
    """Raised when the prompt-control contract or source evidence changed."""


def _unique(rows: list[Mapping[str, Any]], subject: str) -> set[str]:
    result: set[str] = set()
    for row in rows:
        key = str(row.get("key") or "").strip()
        if not key or key in result:
            raise PromptControlContractError(
                f"Duplicate or empty {subject} key: {key!r}."
            )
        result.add(key)
    return result


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise PromptControlContractError("Unsupported prompt-control schema.")
    if payload.get("provider") != "fish_audio":
        raise PromptControlContractError("Provider must remain fish_audio.")
    if payload.get("api_model_header") != "s2.1-pro-free":
        raise PromptControlContractError("The free S2.1 model header changed.")
    permission = payload.get("permission")
    if not isinstance(permission, Mapping) or permission.get("confirmed_by_user") is not True:
        raise PromptControlContractError("Explicit permission must remain recorded.")
    identities = payload.get("identities")
    modes = payload.get("prompt_modes")
    styles = payload.get("styles")
    baselines = payload.get("baseline_models")
    if not all(
        isinstance(value, list) and value
        for value in (identities, modes, styles, baselines)
    ):
        raise PromptControlContractError(
            "Identities, prompt modes, styles, and baselines are required."
        )
    if _unique(identities, "identity") != REQUIRED_IDENTITIES:
        raise PromptControlContractError("The four-identity set changed.")
    if _unique(modes, "prompt mode") != REQUIRED_MODES:
        raise PromptControlContractError("The four prompt controls changed.")
    if _unique(styles, "style") != REQUIRED_STYLES:
        raise PromptControlContractError("The delivery set changed.")
    if set(map(str, baselines)) != REQUIRED_BASELINES:
        raise PromptControlContractError("The reliable baseline set changed.")
    for identity in identities:
        if identity.get("source_round") not in {
            "ryan_calibration",
            "permitted_clones",
        }:
            raise PromptControlContractError(
                f"Unknown source round for {identity.get('key')!r}."
            )
        if not str(identity.get("selected_reference_tier") or ""):
            raise PromptControlContractError(
                f"Selected reference tier is missing for {identity.get('key')!r}."
            )
    for style in styles:
        for field in (
            "label",
            "target_text",
            "simple_tag",
            "fish_instruction",
            "alexandria_instruction",
        ):
            if not str(style.get(field) or "").strip():
                raise PromptControlContractError(
                    f"Style {style.get('key')!r} is missing {field!r}."
                )
        for mode in REQUIRED_MODES:
            build_prompt(style, mode)
    generation = payload.get("generation")
    if not isinstance(generation, Mapping):
        raise PromptControlContractError("Generation settings are missing.")
    if int(generation.get("repeats") or 0) != 2:
        raise PromptControlContractError("Exactly two repeats are required.")
    return payload


def _state_model(
    source_root: Path,
    *,
    expected_round_id: str,
    tier_key: str,
    tier: Mapping[str, Any],
) -> dict[str, Any]:
    path = source_root / "private/fish-voice-models.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("round_id") != expected_round_id:
        raise PromptControlContractError(
            f"Source model state round changed: {path}."
        )
    model = payload.get("models", {}).get(tier_key)
    if not isinstance(model, Mapping) or not str(model.get("model_id") or ""):
        raise PromptControlContractError(
            f"Private Fish model is missing for {tier_key!r}: {path}."
        )
    expected_fingerprint = sha256_value(
        {
            "tier": tier_key,
            "entries": [
                {
                    "audio_sha256": row["audio_sha256"],
                    "text_sha256": row["text_sha256"],
                }
                for row in tier["entries"]
            ],
        }
    )
    if model.get("reference_fingerprint") != expected_fingerprint:
        raise PromptControlContractError(
            f"Reference fingerprint changed for {tier_key!r}."
        )
    if model.get("visibility") != "private":
        raise PromptControlContractError(
            f"Fish model is not private for {tier_key!r}."
        )
    return dict(model)


def _source_identity(
    identity: Mapping[str, Any],
    *,
    round1_root: Path,
    ryan_source_root: Path,
    permitted_source_root: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any], bool]:
    tier_key = str(identity["selected_reference_tier"])
    if identity["source_round"] == "ryan_calibration":
        source_root = ryan_source_root.resolve()
        tiers = reference_tier_payloads(round1_root, load_ryan_config())
        expected_round_id = "alexandria_fish_s21_pro_calibration_v1"
        human = False
    else:
        source_root = (permitted_source_root / str(identity["key"])).resolve()
        permitted_config = load_permitted_config()
        source_identity = next(
            row
            for row in permitted_config["identities"]
            if row["key"] == identity["source_identity_key"]
        )
        tiers, _ = identity_tiers(
            round1_root,
            permitted_config,
            source_identity,
            prepared_root=source_root / "private/prepared-references",
        )
        expected_round_id = (
            f"alexandria_fish_s21_permitted_clones_v1_{identity['key']}"
        )
        human = True
    tier = next((row for row in tiers if row["key"] == tier_key), None)
    if tier is None:
        raise PromptControlContractError(
            f"Reference tier {tier_key!r} is unavailable for {identity['key']!r}."
        )
    model = _state_model(
        source_root,
        expected_round_id=expected_round_id,
        tier_key=tier_key,
        tier=tier,
    )
    return source_root, dict(tier), model, human


def _identity_config(
    config: Mapping[str, Any], identity: Mapping[str, Any]
) -> dict[str, Any]:
    key = str(identity["key"])
    return {
        "schema_version": 1,
        "round_id": f"{config['round_id']}_{key}",
        "provider": config["provider"],
        "marketed_model": config["marketed_model"],
        "api_model_header": config["api_model_header"],
        "identity": {
            "key": key,
            "label": identity["label"],
            "source_kind": (
                "synthetic_qwen_custom_voice"
                if identity["source_round"] == "ryan_calibration"
                else "permitted_human_recording"
            ),
            "permission_confirmed_by_user": True,
            "permission_confirmed_date": config["permission"]["confirmed_date"],
        },
        "reference_tiers": [
            {
                "key": identity["selected_reference_tier"],
                "label": "Fixed identity reference",
            }
        ],
        "prompt_modes": [dict(row) for row in config["prompt_modes"]],
        "styles": [dict(row) for row in config["styles"]],
        "baseline_candidates": [
            {
                "model_key": model,
                "identity_key": identity["source_identity_key"],
            }
            for model in config["baseline_models"]
        ],
        "generation": dict(config["generation"]),
    }


def _request_contract(
    identity_config: Mapping[str, Any],
    *,
    tier: Mapping[str, Any],
    model: Mapping[str, Any],
    style: Mapping[str, Any],
    mode_key: str,
    repeat: int,
) -> dict[str, Any]:
    return {
        "round_id": identity_config["round_id"],
        "provider": identity_config["provider"],
        "marketed_model": identity_config["marketed_model"],
        "api_model_header": identity_config["api_model_header"],
        "reference_tier": tier["key"],
        "reference_fingerprint": model["reference_fingerprint"],
        "style": style["key"],
        "prompt_mode": mode_key,
        "prompt": build_prompt(style, mode_key),
        "repeat": repeat,
        "settings": identity_config["generation"],
    }


def _reused_tag_samples(
    *,
    source_root: Path,
    identity_config: Mapping[str, Any],
    tier: Mapping[str, Any],
    model: Mapping[str, Any],
) -> list[GeneratedSample]:
    result: list[GeneratedSample] = []
    for style in identity_config["styles"]:
        for new_mode, old_mode in REUSED_MODE_MAP.items():
            prompt = build_prompt(style, new_mode)
            prompt_sha = sha256_bytes(prompt.encode("utf-8"))
            for repeat in range(1, int(identity_config["generation"]["repeats"]) + 1):
                directory = (
                    source_root
                    / "outputs/fish_s21_pro"
                    / str(tier["key"])
                    / str(style["key"])
                    / old_mode
                )
                audio_path = directory / f"repeat-{repeat}.wav"
                receipt_path = directory / f"repeat-{repeat}.json"
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("audio_sha256") != sha256_file(audio_path):
                    raise PromptControlContractError(
                        f"Reused Fish audio hash changed: {audio_path}."
                    )
                if receipt.get("prompt_sha256") != prompt_sha:
                    raise PromptControlContractError(
                        f"Reused Fish prompt changed: {receipt_path}."
                    )
                if receipt.get("reference_fingerprint") != model["reference_fingerprint"]:
                    raise PromptControlContractError(
                        f"Reused Fish reference changed: {receipt_path}."
                    )
                if receipt.get("settings") != identity_config["generation"]:
                    raise PromptControlContractError(
                        f"Reused Fish generation settings changed: {receipt_path}."
                    )
                metadata = audio_metadata(audio_path)
                contract = _request_contract(
                    identity_config,
                    tier=tier,
                    model=model,
                    style=style,
                    mode_key=new_mode,
                    repeat=repeat,
                )
                result.append(
                    GeneratedSample(
                        fingerprint=sample_fingerprint(contract),
                        audio_path=audio_path,
                        audio_sha256=receipt["audio_sha256"],
                        duration_seconds=metadata["duration_seconds"],
                        answer={
                            "kind": "fish_cloud",
                            "provider": identity_config["provider"],
                            "marketed_model": identity_config["marketed_model"],
                            "api_model_header": identity_config["api_model_header"],
                            "remote_reference_id": model["model_id"],
                            "reference_tier": tier["key"],
                            "reference_duration_seconds": tier["duration_seconds"],
                            "prompt_mode": new_mode,
                            "style": style["key"],
                            "repeat": repeat,
                            "prompt_sha256": prompt_sha,
                            "reused_verified_source": str(receipt_path),
                        },
                    )
                )
    return result


def _new_prompt_samples(
    client: FishClient | None,
    *,
    output_root: Path,
    identity_config: Mapping[str, Any],
    tier: Mapping[str, Any],
    model: Mapping[str, Any],
    package_only: bool,
) -> list[GeneratedSample]:
    generation_config = copy.deepcopy(identity_config)
    generation_config["prompt_modes"] = [
        row
        for row in generation_config["prompt_modes"]
        if row["key"] in GENERATED_MODES
    ]
    if package_only:
        samples = find_existing_fish_samples(
            output_root=output_root,
            config=generation_config,
        )
    else:
        if client is None:
            raise FishBlindRunError("fish_api_key_missing", "Set FISH_API_KEY.")
        samples = generate_fish_samples(
            client,
            output_root=output_root,
            config=generation_config,
            tiers=[tier],
            models={str(tier["key"]): model},
        )
    expected = (
        len(identity_config["styles"])
        * len(GENERATED_MODES)
        * int(identity_config["generation"]["repeats"])
    )
    if len(samples) != expected:
        raise PromptControlContractError(
            f"Expected {expected} new prompt samples, found {len(samples)}."
        )
    return samples


def _write_hub(output_root: Path, summaries: list[dict[str, Any]]) -> None:
    cards = "\n".join(
        (
            '<li><a href="{key}/review/?reviewer=tristan">'
            "<strong>{label}</strong><span>{count} candidates</span></a></li>"
        ).format(
            key=row["identity"],
            label=row["label"],
            count=row["sample_count"],
        )
        for row in summaries
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alexandria Fish S2.1 Prompt Controls</title>
<style>
:root{{color-scheme:light;font-family:system-ui,-apple-system,sans-serif;background:#f3efe7;color:#28241f}}
body{{max-width:780px;margin:0 auto;padding:48px 24px}}h1{{font-family:Georgia,serif;font-weight:600}}
p{{color:#70685e;line-height:1.55}}ul{{list-style:none;padding:0;display:grid;gap:12px}}
a{{display:flex;justify-content:space-between;gap:20px;padding:18px;border:1px solid #d9d0c3;border-radius:10px;background:#fffdf8;color:inherit;text-decoration:none}}a:hover{{border-color:#315c55}}span{{color:#70685e}}
</style></head><body><p>Alexandria evaluation</p>
<h1>Fish S2.1 prompt-control reviews</h1>
<p>Complete each identity separately. Candidate providers and prompt conditions remain hidden until results are decoded against the private answer keys.</p>
<ul>{cards}</ul></body></html>"""
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "index.html").write_text(html, encoding="utf-8")
    write_json(
        output_root / "manifest.json",
        {
            "schema_version": 1,
            "round_id": "alexandria_fish_s21_prompt_controls_v1",
            "identity_count": len(summaries),
            "identities": summaries,
            "answer_keys_separate": True,
            "production_promotion_allowed": False,
        },
    )


def _selected_identities(
    config: Mapping[str, Any], requested: list[str]
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in config["identities"]]
    if not requested:
        return rows
    requested_set = set(requested)
    unknown = requested_set - {str(row["key"]) for row in rows}
    if unknown:
        raise PromptControlContractError(
            f"Unknown identities requested: {sorted(unknown)}."
        )
    return [row for row in rows if row["key"] in requested_set]


def api_key_from_environment() -> str:
    return os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY") or ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--round1-root", default=str(DEFAULT_ROUND1_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--ryan-source-root", default=str(DEFAULT_RYAN_SOURCE_ROOT))
    parser.add_argument(
        "--permitted-source-root", default=str(DEFAULT_PERMITTED_SOURCE_ROOT)
    )
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--identity", action="append", default=[])
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    round1_root = Path(args.round1_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    ryan_source_root = Path(args.ryan_source_root).expanduser().resolve()
    permitted_source_root = Path(args.permitted_source_root).expanduser().resolve()
    selected = _selected_identities(config, list(args.identity))
    client: FishClient | None = None
    if not args.package_only:
        client = FishClient(
            api_key=api_key_from_environment(),
            model_header=str(config["api_model_header"]),
            base_url=args.api_base,
            max_attempts=int(config["generation"]["max_attempts"]),
        )

    summaries: list[dict[str, Any]] = []
    for identity in selected:
        key = str(identity["key"])
        identity_root = output_root / key
        identity_root.mkdir(parents=True, exist_ok=True)
        identity_config = _identity_config(config, identity)
        source_root, tier, model, human = _source_identity(
            identity,
            round1_root=round1_root,
            ryan_source_root=ryan_source_root,
            permitted_source_root=permitted_source_root,
        )
        reused = _reused_tag_samples(
            source_root=source_root,
            identity_config=identity_config,
            tier=tier,
            model=model,
        )
        new_samples = _new_prompt_samples(
            client,
            output_root=identity_root,
            identity_config=identity_config,
            tier=tier,
            model=model,
            package_only=args.package_only,
        )
        manifest = build_review_package(
            output_root=identity_root,
            round1_root=round1_root,
            config=identity_config,
            tiers=[tier],
            fish_samples=[*reused, *new_samples],
        )
        manifest.update(
            {
                "permission_confirmed_by_user": True,
                "permission_confirmed_date": config["permission"]["confirmed_date"],
                "human_or_licensed_voice_uploaded": human,
                "synthetic_reference_only": not human,
                "selected_reference_tier": tier["key"],
                "selected_reference_duration_seconds": tier["duration_seconds"],
                "reused_verified_fish_sample_count": len(reused),
                "new_fish_sample_count": len(new_samples),
                "excluded_baseline_models": ["fish_s2_pro", "moss_tts_local_v15"],
            }
        )
        write_json(identity_root / "manifest.json", manifest)
        summaries.append(
            {
                "identity": key,
                "label": identity["label"],
                "sample_count": manifest["sample_count"],
                "fish_sample_count": manifest["fish_sample_count"],
                "baseline_sample_count": manifest["baseline_sample_count"],
                "reference_duration_seconds": tier["duration_seconds"],
                "review": f"{key}/review/",
                "answer_key": f"{key}/private/answer-key.json",
            }
        )

    existing_manifest = output_root / "manifest.json"
    if args.identity and existing_manifest.is_file():
        previous = json.loads(existing_manifest.read_text(encoding="utf-8"))
        by_key = {
            str(row["identity"]): row
            for row in previous.get("identities", [])
            if isinstance(row, Mapping)
        }
        by_key.update({row["identity"]: row for row in summaries})
        summaries = [
            by_key[row["key"]]
            for row in config["identities"]
            if row["key"] in by_key
        ]
    _write_hub(output_root, summaries)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": config["round_id"],
                "identity_count": len(summaries),
                "identities": summaries,
                "hub": str(output_root / "index.html"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
