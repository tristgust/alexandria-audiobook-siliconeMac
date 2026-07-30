from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from audio_invalidation import apply_project_audio_invalidation
from chris_dry_reference_repair import (
    OUTPUT_SHA256 as CHRIS_DRY_REPAIR_SHA256,
    install_reviewed_chris_dry_reference,
)
from generation_state import atomic_json_write, fingerprint_value
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    routing_fingerprint,
    validate_recurring_voice_routing,
)
from experimental_prompt_routing import sha256_file
from voice_aliases import validate_voice_aliases


PACK_ID = "alexandria_chris_roz_recurring_voices_v1"
EVIDENCE_ROUND_ID = "alexandria_five_recurring_voice_repair_v1_closed"
PRODUCTION_SEED = 130363
VOICE_NAMES = ("CHRIS", "ROZ")
ALIASES = {
    "CHRIS CWEJ": "CHRIS",
    "CHRISTOPHER CWEJ": "CHRIS",
    "ROZ FORRESTER": "ROZ",
    "ROSLYN FORRESTER": "ROZ",
}
RECEIPT_FILENAME = "chris_roz_recurring_voice_pack.json"


class ChrisRozRecurringVoiceError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChrisRozRecurringVoiceError(f"{label} could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ChrisRozRecurringVoiceError(f"{label} must contain an object.")
    return value


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_handle, temporary.open("wb") as output_handle:
            for block in iter(lambda: input_handle.read(1024 * 1024), b""):
                output_handle.write(block)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _restore(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _source(record: Mapping[str, Any], *, path_field: str, hash_field: str, label: str) -> Path:
    path = Path(str(record.get(path_field) or "")).expanduser().resolve()
    expected = str(record.get(hash_field) or "")
    if not path.is_file():
        raise ChrisRozRecurringVoiceError(f"{label} is missing: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise ChrisRozRecurringVoiceError(
            f"{label} changed; expected {expected}, got {actual}."
        )
    return path


def _bank_records(bank: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if bank.get("schema_version") != 1 or bank.get("tnia_miller_included") is not False:
        raise ChrisRozRecurringVoiceError(
            "The Chris/Roz reference bank schema or T'Nia exclusion contract is invalid."
        )
    identities = bank.get("identity_references")
    performances = bank.get("performance_bank")
    if not isinstance(identities, dict) or not isinstance(performances, dict):
        raise ChrisRozRecurringVoiceError("The Chris/Roz reference bank is incomplete.")
    records: dict[str, dict[str, Any]] = {}
    for identity in ("chris", "roz"):
        row = identities.get(identity)
        if not isinstance(row, dict) or not isinstance(row.get("clean_actor"), dict):
            raise ChrisRozRecurringVoiceError(f"Missing clean actor identity for {identity}.")
        records[f"{identity}:identity"] = dict(row["clean_actor"])
        rows = performances.get(identity)
        if not isinstance(rows, list):
            raise ChrisRozRecurringVoiceError(f"Missing performance bank for {identity}.")
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            candidate_id = str(raw.get("candidate_id") or "").strip()
            if candidate_id:
                records[candidate_id] = dict(raw)
    required = {
        "chris:identity",
        "roz:identity",
        "chris_canonical_dry",
        "chris_dread_protective",
        "chris_canonical_vulnerable",
        "roz_canonical_dry_humour",
        "roz_canonical_tactical_01",
        "roz_vanguard_concern",
    }
    missing = sorted(required - set(records))
    if missing:
        raise ChrisRozRecurringVoiceError(
            "The reviewed reference bank is missing: " + ", ".join(missing)
        )
    return records


def _asset_spec(records: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping = {
        "chris_identity": (
            records["chris:identity"],
            "audio_path",
            "audio_sha256",
            "clone_voices/primary/chris_clean_actor.wav",
        ),
        "roz_identity": (
            records["roz:identity"],
            "audio_path",
            "audio_sha256",
            "clone_voices/primary/roz_clean_actor.wav",
        ),
        "chris_dry": (
            records["chris_canonical_dry"],
            "audio_path",
            "audio_sha256",
            (
                "production_prompt_routes/expressive/chris/"
                "chris_canonical_dry_mossformer2_blend70.wav"
            ),
        ),
        "chris_protective": (
            records["chris_dread_protective"],
            "audio_path",
            "audio_sha256",
            "production_prompt_routes/expressive/chris/chris_dread_protective.wav",
        ),
        "chris_vulnerable": (
            records["chris_canonical_vulnerable"],
            "audio_path",
            "audio_sha256",
            "production_prompt_routes/expressive/chris/chris_canonical_vulnerable.wav",
        ),
        "roz_dry": (
            records["roz_canonical_dry_humour"],
            "audio_path",
            "audio_sha256",
            "production_prompt_routes/expressive/roz/roz_canonical_dry_humour.wav",
        ),
        "roz_tactical": (
            records["roz_canonical_tactical_01"],
            "audio_path",
            "audio_sha256",
            "production_prompt_routes/expressive/roz/roz_canonical_tactical_01.wav",
        ),
        "roz_concern": (
            records["roz_vanguard_concern"],
            "audio_path",
            "audio_sha256",
            "production_prompt_routes/expressive/roz/roz_vanguard_concern.wav",
        ),
    }
    result: dict[str, dict[str, Any]] = {}
    for key, (record, path_field, hash_field, relative) in mapping.items():
        source = _source(record, path_field=path_field, hash_field=hash_field, label=key)
        result[key] = {
            "source": source,
            "relative_path": relative,
            "sha256": str(record[hash_field]),
            "transcript": str(record.get("transcript") or "").strip(),
        }
        if not result[key]["transcript"]:
            raise ChrisRozRecurringVoiceError(f"{key} has no exact transcript.")
    chris_dry = result["chris_dry"]
    chris_dry["source_sha256"] = chris_dry["sha256"]
    chris_dry["sha256"] = CHRIS_DRY_REPAIR_SHA256
    chris_dry["derivation"] = "chris_dry_mossformer2_blend70_v1"
    return result


def _route(
    *,
    backend: str,
    keywords: list[str],
    identity: Mapping[str, Any],
    performance: Mapping[str, Any] | None,
    control: dict[str, Any],
) -> dict[str, Any]:
    return {
        "backend": backend,
        "instruction_keywords": keywords,
        "identity_audio": identity["relative_path"],
        "identity_audio_sha256": identity["sha256"],
        "identity_text": identity["transcript"],
        "performance_audio": (
            performance["relative_path"] if performance is not None else None
        ),
        "performance_audio_sha256": (
            performance["sha256"] if performance is not None else None
        ),
        "performance_text": (
            performance["transcript"] if performance is not None else None
        ),
        "control": control,
        "production_promotion_allowed": True,
    }


def build_routing_policies(
    *,
    project_root: str | Path,
    assets: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    chris_identity = assets["chris_identity"]
    roz_identity = assets["roz_identity"]
    chris = {
        "schema_version": 1,
        "enabled": True,
        "default_route": "neutral",
        "fallback_backend": "qwen3_instruction_controlled",
        "evidence_round_id": EVIDENCE_ROUND_ID,
        "production_promotion_allowed": True,
        "routes": {
            "neutral": _route(
                backend="indextts2_matched_control",
                keywords=[
                    "neutral",
                    "analytical",
                    "professional",
                    "clear diction",
                    "restrained confidence",
                    "matter-of-fact",
                ],
                identity=chris_identity,
                performance=chris_identity,
                control={
                    "emotion_strength": 0.0,
                    "diffusion_steps": 8,
                    "num_beams": 1,
                    "greedy": True,
                    "max_mel_tokens": 600,
                },
            ),
            "dry_humour": _route(
                backend="indextts2_matched_control",
                keywords=[
                    "dry humour",
                    "dry humor",
                    "dryly amused",
                    "understated humour",
                    "understated humor",
                    "sarcastic",
                    "sarcasm",
                    "wry",
                    "ironic",
                    "amused disbelief",
                ],
                identity=chris_identity,
                performance=assets["chris_dry"],
                control={
                    "emotion_strength": 0.75,
                    "diffusion_steps": 8,
                    "num_beams": 1,
                    "greedy": True,
                    "max_mel_tokens": 600,
                },
            ),
            "urgent_authority": _route(
                backend="indextts2_matched_control",
                keywords=[
                    "urgent authority",
                    "urgent",
                    "protective command",
                    "protective",
                    "commanding",
                    "immediate command",
                    "controlled danger",
                    "defiant",
                    "warning",
                    "resolve",
                ],
                identity=chris_identity,
                performance=assets["chris_protective"],
                control={
                    "emotion_strength": 1.0,
                    "diffusion_steps": 8,
                    "num_beams": 1,
                    "greedy": True,
                    "max_mel_tokens": 600,
                },
            ),
            "vulnerability": _route(
                backend="fish_s2_pro_cloud",
                keywords=[
                    "vulnerable",
                    "vulnerability",
                    "emotionally exposed",
                    "hesitant",
                    "sincere",
                    "restrained grief",
                    "worried",
                    "quiet concern",
                    "trying not to lose control",
                ],
                identity=chris_identity,
                performance=assets["chris_vulnerable"],
                control={
                    "reference_id": "631bff1fd20b48e1a4a08db8e936b038",
                    "api_model_header": "s2.1-pro-free",
                    "prompt_mode": "full_alexandria_tag",
                    "tag": (
                        "Speak with hesitant, restrained vulnerability: sincere, "
                        "emotionally exposed, and trying not to lose control."
                    ),
                    "temperature": 0.7,
                    "top_p": 0.7,
                    "repetition_penalty": 1.2,
                },
            ),
        },
    }
    roz = {
        "schema_version": 1,
        "enabled": True,
        "default_route": "neutral",
        "fallback_backend": "qwen3_instruction_controlled",
        "evidence_round_id": EVIDENCE_ROUND_ID,
        "production_promotion_allowed": True,
        "routes": {
            "neutral": _route(
                backend="fish_s2_pro_cloud",
                keywords=[
                    "neutral",
                    "professional authority",
                    "grounded authority",
                    "calm and direct",
                    "unsentimental",
                    "matter-of-fact",
                ],
                identity=roz_identity,
                performance=None,
                control={
                    "reference_id": "0a23ec9242bf4a42b88ab69f92aa9816",
                    "api_model_header": "s2.1-pro-free",
                    "prompt_mode": "simple_tag",
                    "tag": "neutral",
                    "temperature": 0.7,
                    "top_p": 0.7,
                    "repetition_penalty": 1.2,
                },
            ),
            "dry_humour": _route(
                backend="voxcpm2_controllable_clone",
                keywords=[
                    "dry humour",
                    "dry humor",
                    "dry professional sarcasm",
                    "sarcastic",
                    "sarcasm",
                    "restrained impatience",
                    "ironic emphasis",
                    "wry",
                ],
                identity=roz_identity,
                performance=assets["roz_dry"],
                control={
                    "instruction": (
                        "Deliver with dry professional sarcasm, restrained "
                        "impatience, and exact ironic emphasis."
                    ),
                    "cfg_value": 2.0,
                    "inference_timesteps": 10,
                    "warmup_patches": 0,
                    "max_tokens": 1800,
                },
            ),
            "urgent_authority": _route(
                backend="indextts2_matched_control",
                keywords=[
                    "urgent command",
                    "urgent authority",
                    "tactical command",
                    "clipped precision",
                    "commanding",
                    "sustained control",
                    "decisive",
                    "move now",
                ],
                identity=roz_identity,
                performance=assets["roz_tactical"],
                control={
                    "emotion_strength": 0.85,
                    "diffusion_steps": 8,
                    "num_beams": 1,
                    "greedy": True,
                    "max_mel_tokens": 600,
                },
            ),
            "vulnerability": _route(
                backend="fish_s2_pro_cloud",
                keywords=[
                    "restrained concern",
                    "personal concern",
                    "fear of loss",
                    "contained fear",
                    "worried",
                    "vulnerable",
                    "emotionally contained",
                ],
                identity=roz_identity,
                performance=assets["roz_concern"],
                control={
                    "reference_id": "0a23ec9242bf4a42b88ab69f92aa9816",
                    "api_model_header": "s2.1-pro-free",
                    "prompt_mode": "full_alexandria_tag",
                    "tag": (
                        "Speak with restrained personal concern beneath professional "
                        "control, allowing the fear of loss to remain audible but contained."
                    ),
                    "temperature": 0.7,
                    "top_p": 0.7,
                    "repetition_penalty": 1.2,
                },
            ),
        },
    }
    return {
        "CHRIS": validate_recurring_voice_routing(
            chris,
            project_root=project_root,
            verify_audio=True,
        ),
        "ROZ": validate_recurring_voice_routing(
            roz,
            project_root=project_root,
            verify_audio=True,
        ),
    }


def _voice_config(
    *,
    identity: Mapping[str, Any],
    routing: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "type": "clone",
        "voice": "Ryan",
        "ref_audio": identity["relative_path"],
        "ref_text": identity["transcript"],
        "clone_backend": ROUTED_CLONE_BACKEND,
        "character_style": "",
        "default_style": "",
        "seed": str(PRODUCTION_SEED),
        "responsive_backend_routing": copy.deepcopy(routing),
        "responsive_backend_configuration_fingerprint": routing_fingerprint(routing),
    }


def install_chris_roz_recurring_voices(
    *,
    project_root: str | Path,
    reference_bank_path: str | Path,
    reviewed_chris_dry_reference_path: str | Path,
    confirm_production_opt_in: bool,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_production_opt_in is not True:
        raise ChrisRozRecurringVoiceError(
            "Chris/Roz recurring Voice installation requires explicit production confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    bank_path = Path(reference_bank_path).expanduser().resolve()
    bank = _read_json(bank_path, "Chris/Roz reference bank")
    records = _bank_records(bank)
    assets = _asset_spec(records)
    voice_config_path = root / "voice_config.json"
    config = _read_json(voice_config_path, "Voice configuration")
    before_config = voice_config_path.read_bytes()
    destinations = {
        root / str(asset["relative_path"]): asset
        for asset in assets.values()
    }
    before_assets = {
        destination: destination.read_bytes() if destination.is_file() else None
        for destination in destinations
    }
    approved_at = approved_at_utc or utc_timestamp()
    repair_receipts: dict[str, dict[str, Any]] = {}
    try:
        for destination, asset in destinations.items():
            if asset.get("derivation") == "chris_dry_mossformer2_blend70_v1":
                repair_receipt = install_reviewed_chris_dry_reference(
                    source=reviewed_chris_dry_reference_path,
                    destination=destination,
                )
                asset["sha256"] = str(repair_receipt["output_sha256"])
                repair_receipts["chris_dry"] = repair_receipt
            else:
                _atomic_copy(Path(asset["source"]), destination)
            if sha256_file(destination) != asset["sha256"]:
                raise ChrisRozRecurringVoiceError(
                    f"Installed recurring Voice asset failed verification: {destination}."
                )
        policies = build_routing_policies(project_root=root, assets=assets)
        updated = copy.deepcopy(config)
        updated["CHRIS"] = _voice_config(
            identity=assets["chris_identity"],
            routing=policies["CHRIS"],
        )
        updated["ROZ"] = _voice_config(
            identity=assets["roz_identity"],
            routing=policies["ROZ"],
        )
        for alias, target in ALIASES.items():
            updated[alias] = {"alias_of": target}
        validate_voice_aliases(updated)
        atomic_json_write(updated, voice_config_path)
        operation_id = "audio_dependency_" + fingerprint_value(
            {
                "operation": "chris_roz_recurring_voice_install",
                "pack_id": PACK_ID,
                "approved_at_utc": approved_at,
                "voices": list(VOICE_NAMES),
                "routing": {
                    voice: routing_fingerprint(policy)
                    for voice, policy in policies.items()
                },
            }
        )[:24]
        invalidation = apply_project_audio_invalidation(
            project_root=root,
            operation_id=operation_id,
            operation="chris_roz_recurring_voice_install",
            at_utc=approved_at,
            speakers=set(VOICE_NAMES) | set(ALIASES),
            reason=(
                "Chris and Roz changed to reviewed recurring model-specific Voice routing"
            ),
            dependency_before={
                voice_config_path: before_config,
                **before_assets,
            },
        )
    except Exception:
        _restore(voice_config_path, before_config)
        for destination, content in before_assets.items():
            _restore(destination, content)
        raise

    receipt = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "evidence_round_id": EVIDENCE_ROUND_ID,
        "approved_at_utc": approved_at,
        "voices": list(VOICE_NAMES),
        "aliases": copy.deepcopy(ALIASES),
        "assets": [
            {
                "relative_path": asset["relative_path"],
                "sha256": asset["sha256"],
            }
            for asset in assets.values()
        ],
        "routing_fingerprints": {
            voice: routing_fingerprint(policy)
            for voice, policy in policies.items()
        },
        "reviewed_reference_repairs": copy.deepcopy(repair_receipts),
        "production_seed": PRODUCTION_SEED,
        "automatic_instruction_matching": True,
        "final_export_eligible": True,
        "audio_invalidation": invalidation,
    }
    atomic_json_write(receipt, root / RECEIPT_FILENAME)
    return receipt


def inspect_chris_roz_recurring_voices(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    try:
        config = _read_json(root / "voice_config.json", "Voice configuration")
        assets: dict[str, dict[str, Any]] = {}
        fingerprints: dict[str, str] = {}
        for voice_name in VOICE_NAMES:
            voice = config.get(voice_name)
            if not isinstance(voice, dict):
                raise ChrisRozRecurringVoiceError(f"Recurring Voice {voice_name} is missing.")
            if voice.get("type") != "clone" or voice.get("clone_backend") != ROUTED_CLONE_BACKEND:
                raise ChrisRozRecurringVoiceError(
                    f"Recurring Voice {voice_name} is not using the responsive router."
                )
            policy = validate_recurring_voice_routing(
                voice.get("responsive_backend_routing"),
                project_root=root,
                verify_audio=True,
            )
            fingerprint = routing_fingerprint(policy)
            if voice.get("responsive_backend_configuration_fingerprint") != fingerprint:
                raise ChrisRozRecurringVoiceError(
                    f"Recurring Voice {voice_name} routing approval is stale."
                )
            fingerprints[voice_name] = fingerprint
            for route in policy["routes"].values():
                for path_key, hash_key in (
                    ("identity_audio", "identity_audio_sha256"),
                    ("performance_audio", "performance_audio_sha256"),
                ):
                    relative = route.get(path_key)
                    if not relative:
                        continue
                    assets[str(relative)] = {
                        "relative_path": str(relative),
                        "sha256": str(route[hash_key]),
                    }
        for alias, target in ALIASES.items():
            if config.get(alias) != {"alias_of": target}:
                raise ChrisRozRecurringVoiceError(f"Recurring Voice alias {alias} is invalid.")
        return {
            "ready": True,
            "pack_id": PACK_ID,
            "voices": list(VOICE_NAMES),
            "aliases": copy.deepcopy(ALIASES),
            "assets": [assets[key] for key in sorted(assets)],
            "routing_fingerprints": fingerprints,
            "error": None,
        }
    except Exception as exc:
        return {
            "ready": False,
            "pack_id": PACK_ID,
            "voices": list(VOICE_NAMES),
            "aliases": copy.deepcopy(ALIASES),
            "assets": [],
            "routing_fingerprints": {},
            "error": str(exc),
        }
