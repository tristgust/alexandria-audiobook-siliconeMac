from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from approved_audio import (
    active_approved_audio_lock,
    approved_audio_binding_fingerprint,
    approved_audio_content_fingerprint,
)
from audio_artifacts import sha256_file
from experimental_prompt_routing import validate_experimental_prompt_routing
from generation_state import atomic_json_write, fingerprint_value
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    routing_fingerprint,
    validate_recurring_voice_routing,
)
from voice_aliases import validate_voice_aliases


PACK_ID = "alexandria_original_sin_overlap_completion_v1"
EVIDENCE_ROUND_ID = "alexandria_original_sin_overlap_character_coverage_v4_closed"
PRODUCTION_SEED = 130363
HISTORY_DIRNAME = "original_sin_overlap_completion_history"
RECEIPT_FILENAME = "original_sin_overlap_completion_pack.json"
ASSET_ROOT = Path("production_prompt_routes/original_sin_overlap_completion_v1")
IDENTITY_ROOT = Path("clone_voices/original_sin_overlap_completion_v1")

SECURITYBOT_CHUNK_IDS = (491, 493, 495, 497, 501, 503, 618, 622, 634)
TOBIAS_ROBOT_CHUNK_IDS = (1341, 3669, 3674, 3676, 3680, 3682, 3684)

DECISION_SOURCES = (
    (
        "original_sin_overlap_character_coverage_round_v3_decision.json",
        "overlap_character_coverage_round_v3",
        False,
    ),
    (
        "original_sin_overlap_character_repairs_round_v4_decision.json",
        "overlap_character_repairs_round_v4",
        False,
    ),
    (
        "original_sin_overlap_final_character_round_v5_decision.json",
        "overlap_final_character_round_v5",
        False,
    ),
    (
        "original_sin_dantalion_mode_completion_round_v1_decision.json",
        "overlap_character_repairs_round_v4",
        True,
    ),
    (
        "original_sin_homeless_identity_transfer_round_v1_decision.json",
        "homeless_identity_transfer_round_v1",
        True,
    ),
)

MODE_SPECS: dict[str, dict[str, Any]] = {
    "doctor_wry_deflection": {
        "voice": "THE DOCTOR",
        "route": "doctor_wry_deflection",
        "keywords": ["wry deflection", "dry nimble wit", "understated authority"],
        "index_strength": 0.65,
    },
    "doctor_hushed_vulnerability": {
        "voice": "THE DOCTOR",
        "route": "doctor_hushed_vulnerability",
        "keywords": ["hushed vulnerability", "restrained intensity", "careful softness"],
        "index_strength": 0.75,
    },
    "bernice_quiet_defiance": {
        "voice": "BERNICE",
        "route": "bernice_quiet_defiance",
        "keywords": ["quiet defiance", "controlled defiance", "contained anger"],
    },
    "bernice_bittersweet_nostalgia": {
        "voice": "BERNICE",
        "route": "bernice_bittersweet_nostalgia",
        "keywords": ["bittersweet nostalgia", "light self-mockery", "warm nostalgia"],
    },
    "roz_survivor_reflection": {
        "voice": "ROZ FORRESTER",
        "route": "roz_survivor_reflection",
        "keywords": ["survivor reflection", "hard-earned relief", "restrained warmth"],
    },
    "roz_defeated_grief": {
        "voice": "ROZ FORRESTER",
        "route": "roz_defeated_grief",
        "keywords": ["defeated grief", "exhausted control", "suppressed pain"],
    },
    "chris_exposed_vulnerability": {
        "voice": "CHRIS CWEJ",
        "route": "chris_exposed_vulnerability",
        "keywords": ["exposed vulnerability", "careful pleading", "pleading urgency"],
    },
    "powerless_wounded_accusation": {
        "voice": "POWERLESS FRIENDLESS",
        "route": "powerless_wounded_accusation",
        "keywords": ["wounded accusation", "wary questioning", "audible tension"],
    },
    "hater_grave_statecraft": {
        "voice": "HATER OF HUMANS",
        "route": "hater_grave_statecraft",
        "keywords": ["grave statecraft", "grave sovereign authority", "wounded pride"],
    },
    "evan_broadcast_authority": {
        "voice": "EVAN CLAPLE",
        "route": "evan_broadcast_authority",
        "keywords": ["broadcast authority", "empire today", "polished news broadcast"],
    },
    "securitybot_identity_repair": {
        "voice": "SECURITYBOT",
        "route": "securitybot_identity_repair",
        "keywords": ["securitybot", "security system", "mechanically precise"],
        "effect_chain": "securitybot_synthetic_v2",
    },
    "tobias_robot_cold_control": {
        "voice": "TOBIAS VAUGHN",
        "route": "tobias_robot_cold_control",
        "keywords": ["robot cold control", "cold lethal command", "minimal emotional variation"],
    },
    "doctor_weary_moral_gravity_repair": {
        "voice": "THE DOCTOR",
        "route": "doctor_weary_moral_gravity",
        "keywords": ["weary moral gravity", "ancient moral gravity", "subdued resignation"],
    },
    "roz_dry_banter_repair": {
        "voice": "ROZ FORRESTER",
        "route": "roz_dry_banter",
        "keywords": ["dry banter", "streetwise teasing", "guarded amusement"],
    },
    "computer_processing_repair": {
        "voice": "COMPUTER",
        "route": "computer_formal_system_response",
        "keywords": ["formal system response", "computer announcement", "flat synthetic"],
        "effect_chain": "computer_terminal_v3",
    },
    "doctor_sudden_realization_final": {
        "voice": "THE DOCTOR",
        "route": "doctor_sudden_realization",
        "keywords": ["sudden realization", "intellectual breakthrough", "delighted urgency"],
        "index_strength": 0.92,
    },
    "shythe_crisis_broadcast": {
        "voice": "SHYTHE SHAHID",
        "route": "shythe_crisis_broadcast",
        "keywords": ["crisis broadcast", "empire today", "broadcast urgency"],
    },
    "dantalion_weary_memory": {
        "voice": "DOC DANTALION",
        "route": "dantalion_weary_memory",
        "keywords": ["weary memory", "weary intelligence", "quiet self-protection"],
    },
    "dantalion_dry_sardonic": {
        "voice": "DOC DANTALION",
        "route": "dantalion_dry_sardonic",
        "keywords": ["dry sardonic", "sardonic intelligence", "measured pacing"],
    },
    "homeless_identity_transfer": {
        "voice": "HOMELESS FORSAKEN",
        "route": "homeless_identity_transfer",
        "keywords": ["homeless identity", "weak breathy urgency", "fading strength"],
        "candidate_as_identity": True,
    },
}

DEFAULT_ROUTE_BY_VOICE = {
    "SECURITYBOT": "securitybot_identity_repair",
    "COMPUTER": "computer_formal_system_response",
    "DOC DANTALION": "dantalion_weary_memory",
}


class OriginalSinOverlapCompletionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OriginalSinOverlapCompletionError(f"{label} could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise OriginalSinOverlapCompletionError(f"{label} must contain an object.")
    return value


def _safe_name(value: str) -> str:
    return "_".join(
        part
        for part in "".join(
            character.casefold() if character.isalnum() else " "
            for character in value
        ).split()
        if part
    )


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_handle, temporary.open("wb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(path: Path, value: bytes | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _source(path_value: Any, expected_sha256: str, label: str) -> Path:
    source = Path(str(path_value or "")).expanduser().resolve()
    if not source.is_file():
        raise OriginalSinOverlapCompletionError(f"{label} is missing: {source}")
    actual = sha256_file(source)
    if actual != expected_sha256:
        raise OriginalSinOverlapCompletionError(
            f"{label} changed; expected {expected_sha256}, got {actual}."
        )
    return source


def _mode_map(answer: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in answer.get("modes") or []:
        if isinstance(raw, dict) and str(raw.get("mode_id") or "").strip():
            result[str(raw["mode_id"])] = raw
    return result


def _selected_rows(repository_root: Path, evidence_root: Path) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for decision_name, round_name, single_row in DECISION_SOURCES:
        decision = _read_json(repository_root / "benchmarks" / decision_name, decision_name)
        answer_path = evidence_root / round_name / "private" / "answer-key.json"
        answer = _read_json(answer_path, f"{round_name} answer key")
        candidates = answer.get("candidates")
        if not isinstance(candidates, dict):
            raise OriginalSinOverlapCompletionError(f"{round_name} has no candidate map.")
        modes = _mode_map(answer)
        raw_selected = decision.get("selected")
        if not isinstance(raw_selected, dict):
            raise OriginalSinOverlapCompletionError(f"{decision_name} has no selection.")
        if single_row:
            mode_id = str(raw_selected.get("mode_id") or "").strip()
            if decision_name.startswith("original_sin_homeless"):
                mode_id = "homeless_identity_transfer"
            decision_rows = [(mode_id, raw_selected)]
        else:
            decision_rows = list(raw_selected.items())
        for mode_id, decision_row in decision_rows:
            if mode_id not in MODE_SPECS or not isinstance(decision_row, dict):
                raise OriginalSinOverlapCompletionError(
                    f"Unsupported selected overlap mode: {mode_id!r}."
                )
            candidate_id = str(decision_row.get("candidate_id") or "").strip()
            candidate = candidates.get(candidate_id)
            mode = modes.get(mode_id)
            if not isinstance(candidate, dict) or not isinstance(mode, dict):
                raise OriginalSinOverlapCompletionError(
                    f"Selected evidence is incomplete for {mode_id}."
                )
            selected.append(
                {
                    "mode_id": mode_id,
                    "decision": copy.deepcopy(decision_row),
                    "candidate": copy.deepcopy(candidate),
                    "mode": copy.deepcopy(mode),
                    "answer_path": answer_path,
                }
            )
    observed = {row["mode_id"] for row in selected}
    if observed != set(MODE_SPECS):
        raise OriginalSinOverlapCompletionError(
            "Selected overlap evidence does not match the authoritative mode set."
        )
    return selected


def _public_references(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    round_root = Path(row["answer_path"]).parents[1]
    result: list[dict[str, Any]] = []
    for raw in row["mode"].get("public_references") or []:
        if not isinstance(raw, Mapping):
            continue
        expected = str(raw.get("audio_sha256") or "")
        source = _source(
            round_root / str(raw.get("audio") or "").removeprefix("../"),
            expected,
            f"{row['mode_id']} reference",
        )
        transcript = str(raw.get("transcript") or "").strip()
        if not transcript:
            raise OriginalSinOverlapCompletionError(
                f"{row['mode_id']} reference has no transcript."
            )
        result.append(
            {
                "kind": str(raw.get("kind") or "reference"),
                "source": source,
                "sha256": expected,
                "transcript": transcript,
            }
        )
    return result


def _reference_by_hash(
    references: Iterable[Mapping[str, Any]], expected: str, label: str
) -> dict[str, Any]:
    for raw in references:
        if raw.get("sha256") == expected:
            return dict(raw)
    raise OriginalSinOverlapCompletionError(
        f"{label} fingerprint {expected} is absent from the reviewed references."
    )


def _normalize_backend(value: str) -> str:
    if value in {
        "fish_s2_pro_free_zero_shot",
        "postprocess_fish_s2_pro_free_zero_shot",
    }:
        return "fish_s2_pro_cloud"
    return value


def _control(spec: Mapping[str, Any], backend: str, instruct: str) -> dict[str, Any]:
    if backend == "fish_s2_pro_cloud":
        return {
            "reference_mode": "inline_zero_shot",
            "api_model_header": "s2.1-pro-free",
            "prompt_mode": "full_alexandria_tag",
            "tag": instruct,
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
        }
    if backend == "indextts2_matched_control":
        return {
            "emotion_strength": float(spec["index_strength"]),
            "diffusion_steps": 8,
            "num_beams": 1,
            "greedy": True,
            "max_mel_tokens": 600,
        }
    if backend == "voxcpm2_controllable_clone":
        return {
            "instruction": instruct,
            "cfg_value": 2.0,
            "inference_timesteps": 10,
            "warmup_patches": 0,
            "max_tokens": 1800,
        }
    if backend == "qwen3_instruction_controlled":
        return {}
    raise OriginalSinOverlapCompletionError(f"Unsupported backend: {backend}.")


def _copy_asset(
    *,
    root: Path,
    source: Path,
    expected_sha256: str,
    relative: Path,
    assets: dict[Path, bytes | None],
) -> str:
    destination = root / relative
    if destination not in assets:
        assets[destination] = destination.read_bytes() if destination.is_file() else None
    if destination.is_file():
        actual = sha256_file(destination)
        if actual != expected_sha256:
            raise OriginalSinOverlapCompletionError(
                f"Route asset destination already contains different audio: {destination}."
            )
        return relative.as_posix()
    _atomic_copy(source, destination)
    if sha256_file(destination) != expected_sha256:
        raise OriginalSinOverlapCompletionError(
            f"Installed route asset failed verification: {destination}."
        )
    return relative.as_posix()


def _base_voice(identity_relative: str, identity_text: str) -> dict[str, Any]:
    return {
        "type": "clone",
        "voice": "Ryan",
        "ref_audio": identity_relative,
        "ref_text": identity_text,
        "clone_backend": "qwen3_instruction_controlled",
        "character_style": "",
        "default_style": "",
        "seed": str(PRODUCTION_SEED),
        "instruction_clone_temperature": 0.75,
        "instruction_clone_top_k": 50,
        "instruction_clone_top_p": 0.95,
        "instruction_clone_repetition_penalty": 1.5,
        "instruction_clone_max_tokens": 2000,
    }


def _route(
    *,
    backend: str,
    keywords: list[str],
    identity_relative: str,
    identity_sha256: str,
    identity_text: str,
    performance_relative: str | None,
    performance_sha256: str | None,
    performance_text: str | None,
    control: dict[str, Any],
    effect_chain: str | None,
    approval_tier: str,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "instruction_keywords": keywords,
        "identity_audio": identity_relative,
        "identity_audio_sha256": identity_sha256,
        "identity_text": identity_text,
        "performance_audio": performance_relative,
        "performance_audio_sha256": performance_sha256,
        "performance_text": performance_text,
        "control": control,
        "effect_chain": effect_chain,
        "approval_tier": approval_tier,
        "production_promotion_allowed": True,
    }


def _current_reference(
    *,
    root: Path,
    voice_name: str,
    voice: Mapping[str, Any],
    assets: dict[Path, bytes | None],
) -> dict[str, Any]:
    source_value = str(voice.get("ref_audio") or "").strip()
    transcript = str(voice.get("ref_text") or "").strip()
    if not source_value or not transcript:
        raise OriginalSinOverlapCompletionError(
            f"Voice {voice_name!r} has no usable neutral identity."
        )
    source_path = Path(source_value)
    source = source_path.resolve() if source_path.is_absolute() else (root / source_path).resolve()
    if not source.is_file():
        raise OriginalSinOverlapCompletionError(
            f"Voice {voice_name!r} identity is missing: {source}."
        )
    fingerprint = sha256_file(source)
    relative = (
        IDENTITY_ROOT
        / _safe_name(voice_name)
        / f"neutral{source.suffix.casefold() or '.wav'}"
    )
    installed = _copy_asset(
        root=root,
        source=source,
        expected_sha256=fingerprint,
        relative=relative,
        assets=assets,
    )
    return {
        "relative": installed,
        "sha256": fingerprint,
        "transcript": transcript,
    }


def _special_identity_from_row(
    *,
    root: Path,
    voice_name: str,
    row: Mapping[str, Any],
    assets: dict[Path, bytes | None],
) -> dict[str, Any]:
    references = _public_references(row)
    expected = str(row["candidate"].get("reference_audio_sha256") or "")
    reference = _reference_by_hash(
        references,
        expected,
        f"{row['mode_id']} identity",
    )
    relative = (
        IDENTITY_ROOT
        / _safe_name(voice_name)
        / f"neutral{Path(reference['source']).suffix.casefold() or '.wav'}"
    )
    installed = _copy_asset(
        root=root,
        source=Path(reference["source"]),
        expected_sha256=str(reference["sha256"]),
        relative=relative,
        assets=assets,
    )
    return {
        "relative": installed,
        "sha256": str(reference["sha256"]),
        "transcript": str(reference["transcript"]),
    }


def _existing_routes(
    *,
    root: Path,
    voice: Mapping[str, Any],
    neutral: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    raw_responsive = voice.get("responsive_backend_routing")
    if isinstance(raw_responsive, dict):
        policy = validate_recurring_voice_routing(
            raw_responsive,
            project_root=root,
            verify_audio=True,
        )
        routes = copy.deepcopy(policy["routes"])
        default_route = str(policy["default_route"])
    else:
        routes = {}
        default_route = "neutral"

    raw_experimental = voice.get("experimental_prompt_routing")
    if isinstance(raw_experimental, dict):
        experimental = validate_experimental_prompt_routing(
            raw_experimental,
            project_root=root,
            verify_audio=True,
        )
        for route_key, route in experimental["routes"].items():
            converted_key = str(route_key)
            if converted_key in routes:
                converted_key = f"legacy_{converted_key}"
            routes[converted_key] = _route(
                backend="qwen3_instruction_controlled",
                keywords=list(route.get("instruction_keywords") or []),
                identity_relative=str(route["ref_audio"]),
                identity_sha256=str(route["ref_audio_sha256"]),
                identity_text=str(route["ref_text"]),
                performance_relative=None,
                performance_sha256=None,
                performance_text=None,
                control={},
                effect_chain=None,
                approval_tier="strict",
            )

    if "neutral" not in routes:
        routes["neutral"] = _route(
            backend="qwen3_instruction_controlled",
            keywords=[],
            identity_relative=str(neutral["relative"]),
            identity_sha256=str(neutral["sha256"]),
            identity_text=str(neutral["transcript"]),
            performance_relative=None,
            performance_sha256=None,
            performance_text=None,
            control={},
            effect_chain=None,
            approval_tier="strict",
        )
    return routes, default_route


def _reference_for_hash(
    *,
    root: Path,
    voice: Mapping[str, Any],
    neutral: Mapping[str, Any],
    references: Iterable[Mapping[str, Any]],
    expected: str,
    label: str,
) -> dict[str, Any]:
    for raw in references:
        if raw.get("sha256") == expected:
            return dict(raw)
    if neutral.get("sha256") == expected:
        return {
            "source": root / str(neutral["relative"]),
            "sha256": expected,
            "transcript": str(neutral["transcript"]),
        }
    source_value = str(voice.get("ref_audio") or "").strip()
    if source_value:
        source_path = Path(source_value)
        source = source_path.resolve() if source_path.is_absolute() else (root / source_path).resolve()
        if source.is_file() and sha256_file(source) == expected:
            return {
                "source": source,
                "sha256": expected,
                "transcript": str(voice.get("ref_text") or ""),
            }
    raise OriginalSinOverlapCompletionError(
        f"{label} fingerprint {expected} is unavailable."
    )


def _install_route_reference(
    *,
    root: Path,
    voice_name: str,
    route_key: str,
    kind: str,
    reference: Mapping[str, Any],
    assets: dict[Path, bytes | None],
) -> str:
    source = Path(reference["source"])
    relative = (
        ASSET_ROOT
        / _safe_name(voice_name)
        / f"{route_key}_{kind}{source.suffix.casefold() or '.wav'}"
    )
    return _copy_asset(
        root=root,
        source=source,
        expected_sha256=str(reference["sha256"]),
        relative=relative,
        assets=assets,
    )


def _prepare_voice_config(
    *,
    root: Path,
    config: Mapping[str, Any],
    selected_rows: list[dict[str, Any]],
    assets: dict[Path, bytes | None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows_by_voice: dict[str, list[dict[str, Any]]] = {}
    for row in selected_rows:
        voice_name = str(MODE_SPECS[row["mode_id"]]["voice"])
        rows_by_voice.setdefault(voice_name, []).append(row)

    updated = copy.deepcopy(dict(config))
    route_records: list[dict[str, Any]] = []
    for voice_name, rows in sorted(rows_by_voice.items()):
        existing = updated.get(voice_name)
        if isinstance(existing, Mapping) and not existing.get("alias_of"):
            source_voice = copy.deepcopy(dict(existing))
            neutral = _current_reference(
                root=root,
                voice_name=voice_name,
                voice=source_voice,
                assets=assets,
            )
        else:
            if existing is not None:
                raise OriginalSinOverlapCompletionError(
                    f"Voice {voice_name!r} conflicts with an alias or invalid entry."
                )
            neutral = _special_identity_from_row(
                root=root,
                voice_name=voice_name,
                row=rows[0],
                assets=assets,
            )
            source_voice = _base_voice(
                str(neutral["relative"]),
                str(neutral["transcript"]),
            )

        routes, default_route = _existing_routes(
            root=root,
            voice=source_voice,
            neutral=neutral,
        )
        for row in rows:
            mode_id = str(row["mode_id"])
            spec = MODE_SPECS[mode_id]
            decision = row["decision"]
            candidate = row["candidate"]
            mode = row["mode"]
            route_key = str(spec["route"])
            backend = _normalize_backend(
                str(decision.get("backend") or candidate.get("backend") or "")
            )
            instruct = str(candidate.get("instruct") or mode.get("target_instruct") or "").strip()
            if not instruct:
                raise OriginalSinOverlapCompletionError(f"{mode_id} has no route instruction.")
            references = _public_references(row)

            if spec.get("candidate_as_identity"):
                candidate_sha = str(
                    decision.get("audio_sha256")
                    or (candidate.get("audio") or {}).get("sha256")
                    or ""
                )
                identity_reference = {
                    "source": _source(
                        decision.get("audio_path") or candidate.get("audio_path"),
                        candidate_sha,
                        f"{mode_id} approved generated identity",
                    ),
                    "sha256": candidate_sha,
                    "transcript": str(mode.get("target_text") or candidate.get("text") or "").strip(),
                }
            else:
                candidate_reference_sha = str(candidate.get("reference_audio_sha256") or "")
                if backend in {"indextts2_matched_control", "voxcpm2_controllable_clone"}:
                    identity_sha = str(
                        decision.get("identity_audio_sha256")
                        or candidate_reference_sha
                    )
                else:
                    identity_sha = candidate_reference_sha or str(
                        decision.get("identity_audio_sha256") or ""
                    )
                identity_reference = _reference_for_hash(
                    root=root,
                    voice=source_voice,
                    neutral=neutral,
                    references=references,
                    expected=identity_sha,
                    label=f"{mode_id} identity",
                )

            identity_relative = _install_route_reference(
                root=root,
                voice_name=voice_name,
                route_key=route_key,
                kind="identity",
                reference=identity_reference,
                assets=assets,
            )

            performance_relative = None
            performance_sha = None
            performance_text = None
            performance_expected = str(decision.get("performance_audio_sha256") or "")
            if backend == "indextts2_matched_control" or (
                backend == "voxcpm2_controllable_clone" and performance_expected
            ):
                performance_reference = _reference_for_hash(
                    root=root,
                    voice=source_voice,
                    neutral=neutral,
                    references=references,
                    expected=performance_expected,
                    label=f"{mode_id} performance",
                )
                performance_relative = _install_route_reference(
                    root=root,
                    voice_name=voice_name,
                    route_key=route_key,
                    kind="performance",
                    reference=performance_reference,
                    assets=assets,
                )
                performance_sha = str(performance_reference["sha256"])
                performance_text = str(performance_reference["transcript"])

            approval_tier = str(decision.get("approval_tier") or "strict")
            route_value = _route(
                backend=backend,
                keywords=list(spec["keywords"]),
                identity_relative=identity_relative,
                identity_sha256=str(identity_reference["sha256"]),
                identity_text=str(identity_reference["transcript"]),
                performance_relative=performance_relative,
                performance_sha256=performance_sha,
                performance_text=performance_text,
                control=_control(spec, backend, instruct),
                effect_chain=(
                    str(spec["effect_chain"])
                    if spec.get("effect_chain")
                    else None
                ),
                approval_tier=approval_tier,
            )
            current = routes.get(route_key)
            if current is not None and current != route_value:
                raise OriginalSinOverlapCompletionError(
                    f"Voice route {voice_name}/{route_key} already has different evidence."
                )
            routes[route_key] = route_value
            route_records.append(
                {
                    "voice": voice_name,
                    "mode_id": mode_id,
                    "route_key": route_key,
                    "candidate_id": str(decision.get("candidate_id") or ""),
                    "backend": backend,
                    "approval_tier": approval_tier,
                    "effect_chain": route_value["effect_chain"],
                }
            )

        if voice_name in DEFAULT_ROUTE_BY_VOICE:
            default_route = DEFAULT_ROUTE_BY_VOICE[voice_name]
        policy = validate_recurring_voice_routing(
            {
                "schema_version": 1,
                "enabled": True,
                "default_route": default_route,
                "fallback_backend": "qwen3_instruction_controlled",
                "evidence_round_id": EVIDENCE_ROUND_ID,
                "production_promotion_allowed": True,
                "routes": routes,
            },
            project_root=root,
            verify_audio=True,
        )
        source_voice["clone_backend"] = ROUTED_CLONE_BACKEND
        source_voice["seed"] = str(PRODUCTION_SEED)
        source_voice["responsive_backend_routing"] = policy
        source_voice["responsive_backend_configuration_fingerprint"] = (
            routing_fingerprint(policy)
        )
        source_voice.pop("experimental_prompt_routing", None)
        source_voice.pop("controlled_clone_configuration_fingerprint", None)
        updated[voice_name] = source_voice

    validate_voice_aliases(updated)
    return updated, route_records


def _rebind_approved_lock(
    chunk: dict[str, Any],
    lock: Mapping[str, Any] | None,
) -> None:
    if lock is None:
        return
    rebound = copy.deepcopy(dict(lock))
    rebound["content_fingerprint"] = approved_audio_content_fingerprint(chunk)
    rebound["binding_fingerprint"] = approved_audio_binding_fingerprint(chunk, rebound)
    chunk["approved_audio_lock"] = rebound
    chunk["audio_fingerprint"] = rebound["binding_fingerprint"]


def _remap_bot_chunks(chunks: list[Any], affected_voices: set[str]) -> list[dict[str, Any]]:
    bot_rows = {
        int(chunk.get("id")): chunk
        for chunk in chunks
        if isinstance(chunk, dict)
        and str(chunk.get("speaker") or "").strip() == "BOT"
        and isinstance(chunk.get("id"), int)
    }
    expected_ids = set(SECURITYBOT_CHUNK_IDS) | set(TOBIAS_ROBOT_CHUNK_IDS)
    if set(bot_rows) != expected_ids:
        raise OriginalSinOverlapCompletionError(
            "BOT speaker split is stale; the live BOT chunk set no longer matches review evidence."
        )

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        speaker = str(chunk.get("speaker") or "").strip()
        if speaker not in affected_voices and speaker != "BOT":
            continue
        if (chunk.get("audio_path") or chunk.get("status") == "done") and active_approved_audio_lock(chunk) is None:
            raise OriginalSinOverlapCompletionError(
                f"Chunk {chunk.get('id')} has unprotected production audio that must be invalidated first."
            )

    remapped: list[dict[str, Any]] = []
    for chunk_id in SECURITYBOT_CHUNK_IDS:
        chunk = bot_rows[chunk_id]
        lock = active_approved_audio_lock(chunk)
        chunk["speaker"] = "SECURITYBOT"
        _rebind_approved_lock(chunk, lock)
        remapped.append({"chunk_id": chunk_id, "speaker": "SECURITYBOT"})
    for chunk_id in TOBIAS_ROBOT_CHUNK_IDS:
        chunk = bot_rows[chunk_id]
        lock = active_approved_audio_lock(chunk)
        chunk["speaker"] = "TOBIAS VAUGHN"
        _rebind_approved_lock(chunk, lock)
        remapped.append({"chunk_id": chunk_id, "speaker": "TOBIAS VAUGHN"})
    return remapped


def _read_chunks(path: Path) -> list[Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OriginalSinOverlapCompletionError(f"Project chunks could not be read: {exc}") from exc
    if not isinstance(value, list):
        raise OriginalSinOverlapCompletionError("Project chunks must contain an array.")
    return value


def _require_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise OriginalSinOverlapCompletionError(f"{label} is missing: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise OriginalSinOverlapCompletionError(
            f"{label} changed; expected {expected}, got {actual}."
        )


def install_original_sin_overlap_completion(
    *,
    project_root: str | Path,
    repository_root: str | Path,
    evidence_root: str | Path,
    expected_voice_config_sha256: str,
    expected_chunks_sha256: str,
    expected_audio_validity_sha256: str,
    confirm_production_opt_in: bool,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_production_opt_in is not True:
        raise OriginalSinOverlapCompletionError(
            "Original Sin overlap completion requires explicit production confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    repository = Path(repository_root).expanduser().resolve()
    evidence = Path(evidence_root).expanduser().resolve()
    voice_config_path = root / "voice_config.json"
    chunks_path = root / "chunks.json"
    audio_validity_path = root / "audio_validity.json"
    receipt_path = root / RECEIPT_FILENAME
    if receipt_path.is_file():
        existing = _read_json(receipt_path, "Overlap completion receipt")
        if existing.get("status") == "installed":
            raise OriginalSinOverlapCompletionError(
                "Original Sin overlap completion is already installed."
            )

    _require_hash(voice_config_path, expected_voice_config_sha256, "Voice configuration")
    _require_hash(chunks_path, expected_chunks_sha256, "Project chunks")
    _require_hash(audio_validity_path, expected_audio_validity_sha256, "Audio validity")
    before_voice = voice_config_path.read_bytes()
    before_chunks = chunks_path.read_bytes()
    before_validity = audio_validity_path.read_bytes()
    before_root_receipt = receipt_path.read_bytes() if receipt_path.is_file() else None
    approved_at = approved_at_utc or utc_now()
    operation_id = "overlap_completion_" + fingerprint_value(
        {
            "pack_id": PACK_ID,
            "approved_at_utc": approved_at,
            "voice_config_sha256": expected_voice_config_sha256,
            "chunks_sha256": expected_chunks_sha256,
        }
    )[:24]
    operation_dir = root / HISTORY_DIRNAME / operation_id
    if operation_dir.exists():
        raise OriginalSinOverlapCompletionError(
            f"Overlap completion operation already exists: {operation_id}."
        )
    before_dir = operation_dir / "before"
    before_dir.mkdir(parents=True, exist_ok=False)
    (before_dir / "voice_config.json").write_bytes(before_voice)
    (before_dir / "chunks.json").write_bytes(before_chunks)
    (before_dir / "audio_validity.json").write_bytes(before_validity)

    assets: dict[Path, bytes | None] = {}
    try:
        config = _read_json(voice_config_path, "Voice configuration")
        chunks = _read_chunks(chunks_path)
        selected_rows = _selected_rows(repository, evidence)
        updated_config, routes = _prepare_voice_config(
            root=root,
            config=config,
            selected_rows=selected_rows,
            assets=assets,
        )
        affected_voices = {str(record["voice"]) for record in routes}
        affected_speakers = set(affected_voices)
        for alias, value in updated_config.items():
            if isinstance(value, Mapping) and value.get("alias_of") in affected_voices:
                affected_speakers.add(str(alias))
        remapped = _remap_bot_chunks(chunks, affected_speakers)
        atomic_json_write(updated_config, voice_config_path)
        atomic_json_write(chunks, chunks_path)
        if audio_validity_path.read_bytes() != before_validity:
            raise OriginalSinOverlapCompletionError(
                "Audio validity changed unexpectedly during overlap completion."
            )

        after_hashes = {
            "voice_config.json": sha256_file(voice_config_path),
            "chunks.json": sha256_file(chunks_path),
            "audio_validity.json": sha256_file(audio_validity_path),
        }
        asset_records = [
            {
                "relative_path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "preexisting": before is not None,
            }
            for path, before in sorted(assets.items(), key=lambda item: str(item[0]))
        ]
        receipt = {
            "schema_version": 1,
            "status": "installed",
            "pack_id": PACK_ID,
            "evidence_round_id": EVIDENCE_ROUND_ID,
            "operation_id": operation_id,
            "approved_at_utc": approved_at,
            "before_hashes": {
                "voice_config.json": expected_voice_config_sha256,
                "chunks.json": expected_chunks_sha256,
                "audio_validity.json": expected_audio_validity_sha256,
            },
            "after_hashes": after_hashes,
            "route_count": len(routes),
            "routes": routes,
            "remapped_chunks": remapped,
            "securitybot_chunk_ids": list(SECURITYBOT_CHUNK_IDS),
            "tobias_robot_chunk_ids": list(TOBIAS_ROBOT_CHUNK_IDS),
            "assets": asset_records,
            "approved_locked_audio_preserved": True,
            "audio_validity_unchanged": True,
            "rollback_available": True,
        }
        receipt["receipt_fingerprint"] = fingerprint_value(receipt)
        atomic_json_write(receipt, operation_dir / "receipt.json")
        atomic_json_write(receipt, receipt_path)
        return receipt
    except Exception:
        _atomic_bytes(voice_config_path, before_voice)
        _atomic_bytes(chunks_path, before_chunks)
        _atomic_bytes(audio_validity_path, before_validity)
        _atomic_bytes(receipt_path, before_root_receipt)
        for destination, before in assets.items():
            _atomic_bytes(destination, before)
        shutil.rmtree(operation_dir, ignore_errors=True)
        raise


def inspect_original_sin_overlap_completion(
    project_root: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    try:
        receipt = _read_json(root / RECEIPT_FILENAME, "Overlap completion receipt")
        if receipt.get("status") != "installed" or receipt.get("pack_id") != PACK_ID:
            raise OriginalSinOverlapCompletionError(
                "Overlap completion receipt is not installed."
            )
        for relative, expected in receipt.get("after_hashes", {}).items():
            _require_hash(root / relative, str(expected), relative)
        for asset in receipt.get("assets") or []:
            _require_hash(
                root / str(asset["relative_path"]),
                str(asset["sha256"]),
                "Overlap completion asset",
            )
        config = _read_json(root / "voice_config.json", "Voice configuration")
        voices = sorted({str(record["voice"]) for record in receipt.get("routes") or []})
        route_counts: dict[str, int] = {}
        for voice_name in voices:
            voice = config.get(voice_name)
            if not isinstance(voice, dict) or voice.get("clone_backend") != ROUTED_CLONE_BACKEND:
                raise OriginalSinOverlapCompletionError(
                    f"Promoted Voice is not responsive: {voice_name}."
                )
            policy = validate_recurring_voice_routing(
                voice.get("responsive_backend_routing"),
                project_root=root,
                verify_audio=True,
            )
            expected_fingerprint = routing_fingerprint(policy)
            if voice.get("responsive_backend_configuration_fingerprint") != expected_fingerprint:
                raise OriginalSinOverlapCompletionError(
                    f"Promoted Voice routing fingerprint is stale: {voice_name}."
                )
            route_counts[voice_name] = len(policy["routes"])
        chunks = _read_chunks(root / "chunks.json")
        by_id = {
            int(chunk["id"]): chunk
            for chunk in chunks
            if isinstance(chunk, dict) and isinstance(chunk.get("id"), int)
        }
        if any(by_id[chunk_id].get("speaker") != "SECURITYBOT" for chunk_id in SECURITYBOT_CHUNK_IDS):
            raise OriginalSinOverlapCompletionError("Securitybot chunk remap is stale.")
        if any(by_id[chunk_id].get("speaker") != "TOBIAS VAUGHN" for chunk_id in TOBIAS_ROBOT_CHUNK_IDS):
            raise OriginalSinOverlapCompletionError("Tobias robot chunk remap is stale.")
        if active_approved_audio_lock(by_id[618]) is None:
            raise OriginalSinOverlapCompletionError(
                "The approved Securitybot direct performance lock is no longer active."
            )
        return {
            "ready": True,
            "pack_id": PACK_ID,
            "operation_id": receipt["operation_id"],
            "route_count": int(receipt["route_count"]),
            "voices": voices,
            "route_counts": route_counts,
            "remapped_chunk_count": len(receipt.get("remapped_chunks") or []),
            "error": None,
        }
    except Exception as exc:
        return {
            "ready": False,
            "pack_id": PACK_ID,
            "operation_id": None,
            "route_count": 0,
            "voices": [],
            "route_counts": {},
            "remapped_chunk_count": 0,
            "error": str(exc),
        }


def rollback_original_sin_overlap_completion(
    *,
    project_root: str | Path,
    confirm_rollback: bool,
    rolled_back_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_rollback is not True:
        raise OriginalSinOverlapCompletionError(
            "Original Sin overlap completion rollback requires confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    receipt_path = root / RECEIPT_FILENAME
    receipt = _read_json(receipt_path, "Overlap completion receipt")
    if receipt.get("status") != "installed" or receipt.get("pack_id") != PACK_ID:
        raise OriginalSinOverlapCompletionError(
            "Overlap completion is not available for rollback."
        )
    for relative, expected in receipt.get("after_hashes", {}).items():
        _require_hash(root / relative, str(expected), relative)
    for asset in receipt.get("assets") or []:
        _require_hash(
            root / str(asset["relative_path"]),
            str(asset["sha256"]),
            "Overlap completion asset",
        )
    operation_dir = root / HISTORY_DIRNAME / str(receipt["operation_id"])
    before_dir = operation_dir / "before"
    _atomic_bytes(root / "voice_config.json", (before_dir / "voice_config.json").read_bytes())
    _atomic_bytes(root / "chunks.json", (before_dir / "chunks.json").read_bytes())
    _atomic_bytes(root / "audio_validity.json", (before_dir / "audio_validity.json").read_bytes())
    removed_assets: list[str] = []
    for asset in receipt.get("assets") or []:
        if asset.get("preexisting"):
            continue
        path = root / str(asset["relative_path"])
        path.unlink(missing_ok=True)
        removed_assets.append(str(asset["relative_path"]))
    updated = {
        **receipt,
        "status": "rolled_back",
        "rolled_back_at_utc": rolled_back_at_utc or utc_now(),
        "rollback_available": False,
        "removed_assets": removed_assets,
    }
    updated["receipt_fingerprint"] = fingerprint_value(
        {key: value for key, value in updated.items() if key != "receipt_fingerprint"}
    )
    atomic_json_write(updated, operation_dir / "receipt.json")
    atomic_json_write(updated, receipt_path)
    return updated
