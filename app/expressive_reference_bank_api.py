from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from voice_identity_context import (
    VoiceIdentityContextInvalidError,
    VoiceIdentityContextSourceMismatchError,
    VoiceIdentityContextUnavailableError,
    load_voice_identity_context,
)
from expressive_reference_bank import (
    COMPARISON_MODES,
    STYLE_DEFINITIONS,
    ExpressiveReferenceBankConflictError,
    ExpressiveReferenceBankError,
    ExpressiveReferenceBankValidationError,
    assign_reference_bank_to_voice_config,
    build_reference_bank_status,
    clear_reference_bank_assignment,
    comparison_directory,
    create_reference_bank_file,
    mutate_reference_bank_file,
    read_reference_bank,
    reference_audio_directory,
    reference_bank_path,
    select_reference_for_instruction,
    sha256_file,
)
from voice_training_projects import (
    VoiceTrainingProjectError,
    read_voice_training_project,
    voice_training_project_path,
)


class ExpressiveReferenceBankApiError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.detail}


def _domain_error(exc: Exception) -> ExpressiveReferenceBankApiError:
    if isinstance(exc, ExpressiveReferenceBankConflictError):
        return ExpressiveReferenceBankApiError(
            status_code=409,
            code="expressive_reference_bank_conflict",
            detail=str(exc),
        )
    if isinstance(exc, ExpressiveReferenceBankValidationError):
        return ExpressiveReferenceBankApiError(
            status_code=422,
            code="expressive_reference_bank_rejected",
            detail=str(exc),
        )
    if isinstance(exc, ExpressiveReferenceBankError):
        return ExpressiveReferenceBankApiError(
            status_code=409,
            code="expressive_reference_bank_error",
            detail=str(exc),
        )
    return ExpressiveReferenceBankApiError(
        status_code=500,
        code="expressive_reference_bank_failed",
        detail=str(exc),
    )


def _load_identity_context(
    *,
    approved_roster_path: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    required: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        return load_voice_identity_context(
            approved_roster_path=approved_roster_path,
            source_text=source_text,
            current_source_fingerprint=current_source_fingerprint,
            required=required,
        )
    except VoiceIdentityContextUnavailableError as exc:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="script_required",
            detail=str(exc),
        ) from exc
    except VoiceIdentityContextSourceMismatchError as exc:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="approved_roster_source_mismatch",
            detail=str(exc),
        ) from exc
    except VoiceIdentityContextInvalidError as exc:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="identity_context_invalid",
            detail=str(exc),
        ) from exc


def _load_approved_roster(
    *,
    approved_roster_path: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    required: bool = True,
) -> dict[str, Any] | None:
    roster, _ = _load_identity_context(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
        required=required,
    )
    return roster


def _find_entry(roster: dict[str, Any], character_id: str) -> dict[str, Any]:
    entry = next(
        (item for item in roster["entries"] if item["id"] == character_id),
        None,
    )
    if entry is None:
        raise ExpressiveReferenceBankApiError(
            status_code=404,
            code="character_not_found",
            detail="The selected speaker is not present in the current script or approved roster.",
        )
    if (
        entry["speaking_status"] not in {"speaker", "narrator"}
        or entry["resolution_status"] != "resolved"
    ):
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="character_ineligible",
            detail=(
                "Only resolved speakers in the current script may use an "
                "expressive reference bank."
            ),
        )
    return entry


def _load_project_and_bank(
    *,
    projects_root: str | Path,
    character_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    project_path = voice_training_project_path(projects_root, character_id)
    bank_path = reference_bank_path(projects_root, character_id)
    if not project_path.exists():
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="voice_training_project_required",
            detail=(
                "Create and approve the expressive voice project before "
                "building a reference bank."
            ),
        )
    if not bank_path.exists():
        raise ExpressiveReferenceBankApiError(
            status_code=404,
            code="expressive_reference_bank_not_found",
            detail="No expressive reference bank exists for this character.",
        )
    try:
        return (
            read_voice_training_project(project_path),
            read_reference_bank(bank_path),
            bank_path,
        )
    except VoiceTrainingProjectError as exc:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="voice_training_project_invalid",
            detail=str(exc),
        ) from exc
    except ExpressiveReferenceBankError as exc:
        raise _domain_error(exc) from exc


def get_reference_bank_status_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    roster, context = _load_identity_context(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
        required=False,
    )
    status = build_reference_bank_status(
        approved_roster=roster,
        projects_root=projects_root,
    )
    status["source_compatible"] = context["source_compatible"]
    status["identity_source"] = context["identity_source"]
    status["roster_enriched"] = context["roster_enriched"]
    status["context_error"] = context["context_warning"]
    return status


def get_reference_bank_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    _find_entry(roster, character_id)
    _, bank, _ = _load_project_and_bank(
        projects_root=projects_root,
        character_id=character_id,
    )
    if bank["character"]["roster_fingerprint"] != roster["roster_fingerprint"]:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="incompatible_roster",
            detail=(
                "The expressive reference bank belongs to another speaker "
                "identity catalog."
            ),
        )
    return bank


def create_reference_bank_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    identity_seed: int | None = None,
    source_clip_id: str | None = None,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    _find_entry(roster, character_id)
    try:
        bank = create_reference_bank_file(
            projects_root=projects_root,
            character_id=character_id,
            identity_seed=identity_seed,
            source_clip_id=source_clip_id,
            created_at_utc=created_at_utc,
        )
    except (ExpressiveReferenceBankError, VoiceTrainingProjectError) as exc:
        raise _domain_error(exc) from exc
    if bank["character"]["roster_fingerprint"] != roster["roster_fingerprint"]:
        try:
            reference_bank_path(projects_root, character_id).unlink()
        except FileNotFoundError:
            pass
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="incompatible_roster",
            detail=(
                "The expressive voice project belongs to another speaker "
                "identity catalog."
            ),
        )
    return bank


def apply_reference_bank_action_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    expected_fingerprint: str,
    action: str,
    payload: dict[str, Any] | None = None,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    _find_entry(roster, character_id)
    try:
        updated = mutate_reference_bank_file(
            projects_root=projects_root,
            character_id=character_id,
            expected_fingerprint=expected_fingerprint,
            action=action,
            payload=dict(payload or {}),
        )
    except (ExpressiveReferenceBankError, VoiceTrainingProjectError) as exc:
        raise _domain_error(exc) from exc
    if updated["character"]["roster_fingerprint"] != roster["roster_fingerprint"]:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="incompatible_roster",
            detail=(
                "The expressive reference bank belongs to another speaker "
                "identity catalog."
            ),
        )
    return updated


def _copy_generated_audio(
    *,
    source_path: str | Path,
    target_directory: Path,
    stem: str,
) -> tuple[Path, str]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise ExpressiveReferenceBankValidationError(
            "The generated reference audio is missing or empty."
        )
    target_directory.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    suffix = source.suffix.lower() if source.suffix else ".wav"
    target = target_directory / f"{stem}_{source_hash[:16]}{suffix}"
    if not target.exists():
        shutil.copy2(source, target)
    if sha256_file(target) != source_hash:
        raise ExpressiveReferenceBankValidationError(
            "The copied reference audio did not verify."
        )
    return target, source_hash


def generate_reference_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    expected_fingerprint: str,
    style_key: str,
    reference_text: str,
    controlled_clone_generator: Callable[..., bool],
    generation_backend: str,
    model: str,
    instruction: str | None = None,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    _find_entry(roster, character_id)
    _project, bank, bank_path = _load_project_and_bank(
        projects_root=projects_root,
        character_id=character_id,
    )
    if expected_fingerprint != bank["bank_fingerprint"]:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="stale_expressive_reference_bank",
            detail="The expressive reference bank changed after it was loaded.",
        )
    style = style_key.strip().casefold().replace("-", "_").replace(" ", "_")
    definition = STYLE_DEFINITIONS.get(style)
    if definition is None:
        raise ExpressiveReferenceBankApiError(
            status_code=422,
            code="unsupported_reference_style",
            detail=f"Unsupported expressive reference style: {style_key!r}.",
        )
    if bank["identity_source"]["kind"] != "owned_recording":
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="owned_identity_required",
            detail=(
                "Generated style references require an approved owned "
                "recording as the sole identity source."
            ),
        )
    style_instruction = (instruction or definition["instruction"]).strip()
    if not style_instruction:
        raise ExpressiveReferenceBankApiError(
            status_code=422,
            code="reference_instruction_required",
            detail="A style reference requires a delivery instruction.",
        )
    exact_reference_text = str(reference_text or "").strip()
    if not exact_reference_text:
        raise ExpressiveReferenceBankApiError(
            status_code=422,
            code="reference_text_required",
            detail="A style reference requires spoken reference text.",
        )
    identity = bank["identity_source"]
    identity_audio = (bank_path.parent / identity["audio_path"]).resolve()
    try:
        identity_audio.relative_to(bank_path.parent.resolve())
    except ValueError as exc:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="owned_identity_invalid",
            detail="The owned identity recording escaped its character project.",
        ) from exc
    if (
        not identity_audio.is_file()
        or sha256_file(identity_audio) != identity["audio_sha256"]
    ):
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="owned_identity_invalid",
            detail="The owned identity recording is missing or changed.",
        )
    target_directory = reference_audio_directory(
        projects_root,
        character_id,
    )
    target_directory.mkdir(parents=True, exist_ok=True)
    temporary = target_directory / (
        f".style_{style}.{uuid.uuid4().hex}.tmp.wav"
    )
    controlled_instruction = (
        style_instruction.rstrip(". ")
        + ". Preserve the exact supplied speaker identity, accent, age, "
        "and timbre from the owned reference recording."
    )
    try:
        generated = controlled_clone_generator(
            text=exact_reference_text,
            ref_audio=str(identity_audio),
            ref_text=identity["exact_transcript"],
            instruct=controlled_instruction,
            output_path=str(temporary),
            temperature=0.75,
            top_k=50,
            top_p=0.95,
            repetition_penalty=1.5,
            max_tokens=2000,
        )
        if generated is not True:
            raise ExpressiveReferenceBankValidationError(
                "Controlled clone generation returned no audio."
            )
        target, audio_hash = _copy_generated_audio(
            source_path=temporary,
            target_directory=target_directory,
            stem=f"style_{style}",
        )
        relative = target.relative_to(bank_path.parent).as_posix()
        updated = mutate_reference_bank_file(
            projects_root=projects_root,
            character_id=character_id,
            expected_fingerprint=expected_fingerprint,
            action="add_reference",
            payload={
                "style_key": style,
                "instruction": style_instruction,
                "reference_text": exact_reference_text,
                "seed": None,
                "audio_path": relative,
                "audio_sha256": audio_hash,
                "generation_backend": generation_backend,
                "model": model,
                "source_kind": "qwen_icl_instruction_experimental",
                "source_clip_id": identity["source_clip_id"],
                "generated_at_utc": generated_at_utc,
            },
        )
    except (ExpressiveReferenceBankError, VoiceTrainingProjectError) as exc:
        raise _domain_error(exc) from exc
    except Exception as exc:
        raise ExpressiveReferenceBankApiError(
            status_code=500,
            code="controlled_reference_generation_failed",
            detail=str(exc),
        ) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    generated_reference = next(
        item for item in updated["references"]
        if item["style_key"] == style
    )
    return {"bank": updated, "reference": generated_reference}


def _comparison_output_target(
    *,
    projects_root: str | Path,
    character_id: str,
    mode: str,
    line_index: int,
) -> Path:
    directory = comparison_directory(projects_root, character_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{line_index:02d}_{mode}.wav"


def generate_comparison_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    expected_fingerprint: str,
    test_lines: list[dict[str, str]],
    design_generator: Callable[..., tuple[str, int]],
    clone_generator: Callable[..., bool],
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    _find_entry(roster, character_id)
    project, bank, bank_path = _load_project_and_bank(
        projects_root=projects_root,
        character_id=character_id,
    )
    if expected_fingerprint != bank["bank_fingerprint"]:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="stale_expressive_reference_bank",
            detail="The expressive reference bank changed after it was loaded.",
        )
    if not isinstance(test_lines, list) or not test_lines:
        raise ExpressiveReferenceBankApiError(
            status_code=422,
            code="comparison_lines_required",
            detail="Provide at least one fixed comparison line.",
        )
    approved_styles = {
        item["style_key"]: item
        for item in bank["references"]
        if item["review"]["approved"]
    }
    neutral = approved_styles.get(bank["neutral_style_key"])
    if neutral is None:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="neutral_reference_required",
            detail="Approve the neutral reference before generating comparisons.",
        )
    neutral_audio = (bank_path.parent / neutral["audio_path"]).resolve()
    if not neutral_audio.is_file() or sha256_file(neutral_audio) != neutral["audio_sha256"]:
        raise ExpressiveReferenceBankApiError(
            status_code=409,
            code="neutral_reference_invalid",
            detail="The neutral reference audio is missing or changed.",
        )
    normalized_lines: list[str] = []
    outputs: list[dict[str, Any]] = []
    persona = project["desired_base_persona"]
    for index, line in enumerate(test_lines):
        if not isinstance(line, dict):
            raise ExpressiveReferenceBankApiError(
                status_code=422,
                code="invalid_comparison_line",
                detail=f"Comparison line {index} must be an object.",
            )
        text = str(line.get("text", "")).strip()
        instruction = str(line.get("instruct", "")).strip()
        if not text:
            raise ExpressiveReferenceBankApiError(
                status_code=422,
                code="invalid_comparison_line",
                detail=f"Comparison line {index} requires text.",
            )
        normalized_lines.append(text)
        try:
            selected = select_reference_for_instruction(
                bank_path=bank_path,
                instruction=instruction,
                project_root=bank_path.parent,
                require_bank_approved=False,
            )
        except ExpressiveReferenceBankError as exc:
            raise _domain_error(exc) from exc
        bank_target = _comparison_output_target(
            projects_root=projects_root,
            character_id=character_id,
            mode="reference_bank_clone",
            line_index=index,
        )
        if not clone_generator(
            text=text,
            ref_audio=selected["ref_audio"],
            ref_text=selected["ref_text"],
            output_path=str(bank_target),
        ):
            raise ExpressiveReferenceBankApiError(
                status_code=500,
                code="comparison_generation_failed",
                detail="Reference-bank clone comparison generation failed.",
            )
        outputs.append(
            {
                "mode": "reference_bank_clone",
                "style_key": selected["style_key"],
                "line_index": index,
                "audio_path": bank_target.relative_to(bank_path.parent).as_posix(),
                "audio_sha256": sha256_file(bank_target),
                "identity_role": "owned_identity_candidate",
            }
        )

        single_target = _comparison_output_target(
            projects_root=projects_root,
            character_id=character_id,
            mode="single_reference_clone",
            line_index=index,
        )
        if not clone_generator(
            text=text,
            ref_audio=str(neutral_audio),
            ref_text=neutral["reference_text"],
            output_path=str(single_target),
        ):
            raise ExpressiveReferenceBankApiError(
                status_code=500,
                code="comparison_generation_failed",
                detail="Single-reference clone comparison generation failed.",
            )
        outputs.append(
            {
                "mode": "single_reference_clone",
                "style_key": None,
                "line_index": index,
                "audio_path": single_target.relative_to(bank_path.parent).as_posix(),
                "audio_sha256": sha256_file(single_target),
                "identity_role": "owned_identity_candidate",
            }
        )

        direct_temp, _sample_rate = design_generator(
            description=(
                persona["description"].rstrip(". ")
                + ". "
                + (instruction or "Natural neutral delivery.")
            ),
            sample_text=text,
            seed=bank["identity_seed"],
        )
        direct_target, direct_hash = _copy_generated_audio(
            source_path=direct_temp,
            target_directory=comparison_directory(
                projects_root,
                character_id,
            ),
            stem=f"{index:02d}_direct_voice_design",
        )
        outputs.append(
            {
                "mode": "direct_voice_design",
                "style_key": None,
                "line_index": index,
                "audio_path": direct_target.relative_to(bank_path.parent).as_posix(),
                "audio_sha256": direct_hash,
                "identity_role": "external_experimental_comparator",
            }
        )
    try:
        updated = mutate_reference_bank_file(
            projects_root=projects_root,
            character_id=character_id,
            expected_fingerprint=expected_fingerprint,
            action="record_comparison_outputs",
            payload={
                "test_lines": normalized_lines,
                "outputs": outputs,
            },
        )
    except (ExpressiveReferenceBankError, VoiceTrainingProjectError) as exc:
        raise _domain_error(exc) from exc
    return {"bank": updated, "outputs": outputs}


def assign_reference_bank_payload(
    *,
    approved_roster_path: str | Path,
    projects_root: str | Path,
    character_id: str,
    expected_fingerprint: str,
    voice_config_path: str | Path,
    project_root: str | Path,
    assign: bool,
    voice_name: str | None = None,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    roster = _load_approved_roster(
        approved_roster_path=approved_roster_path,
        source_text=source_text,
        current_source_fingerprint=current_source_fingerprint,
    )
    assert roster is not None
    entry = _find_entry(roster, character_id)
    bank_path = reference_bank_path(projects_root, character_id)
    try:
        if assign:
            return assign_reference_bank_to_voice_config(
                bank_path=bank_path,
                voice_config_path=voice_config_path,
                project_root=project_root,
                expected_fingerprint=expected_fingerprint,
                voice_name=voice_name or entry["canonical_name"],
            )
        return clear_reference_bank_assignment(
            bank_path=bank_path,
            voice_config_path=voice_config_path,
            project_root=project_root,
            expected_fingerprint=expected_fingerprint,
        )
    except ExpressiveReferenceBankError as exc:
        raise _domain_error(exc) from exc
