from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from generation_state import fingerprint_value


EVIDENCE_SCHEMA_VERSION = 1
PROMPT_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SAMPLE_ID_RE = re.compile(r"sample_[0-9a-f]{16,32}")
_EXPLICIT_SAMPLE_RE = re.compile(
    r"\[\s*sample\s*:\s*(sample_[0-9a-f]{16,32})\s*\]",
    re.IGNORECASE,
)


class ProductionVoiceEvidenceError(RuntimeError):
    pass


class ProductionVoiceEvidenceValidationError(ProductionVoiceEvidenceError):
    pass


class ProductionVoiceEvidenceConflictError(ProductionVoiceEvidenceError):
    pass


def _text(
    value: Any,
    label: str,
    *,
    allow_empty: bool = False,
    max_length: int = 12000,
) -> str:
    if not isinstance(value, str):
        raise ProductionVoiceEvidenceValidationError(f"{label} must be text.")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must not be empty."
        )
    if len(normalized) > max_length:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must be no longer than {max_length} characters."
        )
    return normalized


def _optional_text(
    value: Any,
    label: str,
    *,
    max_length: int = 12000,
) -> str | None:
    if value is None:
        return None
    normalized = _text(
        value,
        label,
        allow_empty=True,
        max_length=max_length,
    )
    return normalized or None


def _sha(value: Any, label: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    normalized = _text(value, label)
    if not _SHA256_RE.fullmatch(normalized):
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must be a lowercase SHA-256 fingerprint."
        )
    return normalized


def _score(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must be a number from 1 to 5 or null."
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must be a number from 1 to 5 or null."
        ) from exc
    if not 1.0 <= result <= 5.0:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must be between 1 and 5."
        )
    return result


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ProductionVoiceEvidenceValidationError(f"{label} must be a list.")
    result: list[str] = []
    for index, item in enumerate(value):
        normalized = _text(item, f"{label}[{index}]", max_length=200)
        if normalized not in result:
            result.append(normalized)
    return result


def _relative_path(value: Any, label: str) -> str:
    normalized = _text(value, label, max_length=1024)
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must be a safe relative path."
        )
    return path.as_posix()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_json(value: Any, label: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain only finite JSON values."
        ) from exc
    return json.loads(encoded)


def _validate_identity_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "source",
        "approved_at_utc",
        "notes",
    }:
        raise ProductionVoiceEvidenceValidationError(
            "identity_binding must contain status, source, approved_at_utc, and notes."
        )
    status = _text(value["status"], "identity_binding.status")
    source = _text(value["source"], "identity_binding.source")
    approved_at = _optional_text(
        value["approved_at_utc"],
        "identity_binding.approved_at_utc",
        max_length=100,
    )
    notes = _text(
        value["notes"],
        "identity_binding.notes",
        allow_empty=True,
        max_length=2000,
    )
    if status not in {"unbound", "approved"}:
        raise ProductionVoiceEvidenceValidationError(
            "identity_binding.status must be unbound or approved."
        )
    if source not in {"none", "cast", "user_review"}:
        raise ProductionVoiceEvidenceValidationError(
            "identity_binding.source must be none, cast, or user_review."
        )
    if status == "approved" and (source == "none" or approved_at is None):
        raise ProductionVoiceEvidenceValidationError(
            "Approved identity binding requires an explicit Cast or user-review source and review time."
        )
    if status == "unbound" and source != "none":
        raise ProductionVoiceEvidenceValidationError(
            "Unbound identity evidence cannot claim an identity source."
        )
    return {
        "status": status,
        "source": source,
        "approved_at_utc": approved_at,
        "notes": notes,
    }


def _validate_speaker_evidence_review(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "decision",
        "reviewed_at_utc",
        "notes",
    }:
        raise ProductionVoiceEvidenceValidationError(
            "speaker_evidence_review must contain status, decision, reviewed_at_utc, and notes."
        )
    status = _text(value["status"], "speaker_evidence_review.status")
    decision = _text(value["decision"], "speaker_evidence_review.decision")
    reviewed_at = _optional_text(
        value["reviewed_at_utc"],
        "speaker_evidence_review.reviewed_at_utc",
        max_length=100,
    )
    notes = _text(
        value["notes"],
        "speaker_evidence_review.notes",
        allow_empty=True,
        max_length=2000,
    )
    if status not in {"not_required", "pending", "reviewed"}:
        raise ProductionVoiceEvidenceValidationError(
            "speaker_evidence_review.status is invalid."
        )
    if decision not in {"none", "accept", "reject"}:
        raise ProductionVoiceEvidenceValidationError(
            "speaker_evidence_review.decision is invalid."
        )
    if status == "reviewed" and (decision == "none" or reviewed_at is None):
        raise ProductionVoiceEvidenceValidationError(
            "Reviewed speaker evidence requires a decision and review time."
        )
    if status != "reviewed" and decision != "none":
        raise ProductionVoiceEvidenceValidationError(
            "Unreviewed speaker evidence cannot contain a decision."
        )
    return {
        "status": status,
        "decision": decision,
        "reviewed_at_utc": reviewed_at,
        "notes": notes,
    }


def _validate_provenance(value: Any, label: str) -> dict[str, Any]:
    expected = {
        "source_kind",
        "source_id",
        "permission_basis",
        "model_id",
        "model_revision",
        "recorded_at_utc",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain the exact provenance fields."
        )
    source_kind = _text(value["source_kind"], f"{label}.source_kind")
    if source_kind not in {
        "owned_recording",
        "approved_performance",
        "generated_reviewed",
        "adaptation_source",
    }:
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.source_kind is unsupported."
        )
    return {
        "source_kind": source_kind,
        "source_id": _text(value["source_id"], f"{label}.source_id"),
        "permission_basis": _text(
            value["permission_basis"],
            f"{label}.permission_basis",
            max_length=2000,
        ),
        "model_id": _optional_text(value["model_id"], f"{label}.model_id"),
        "model_revision": _optional_text(
            value["model_revision"],
            f"{label}.model_revision",
        ),
        "recorded_at_utc": _optional_text(
            value["recorded_at_utc"],
            f"{label}.recorded_at_utc",
            max_length=100,
        ),
    }


def _validate_quality(value: Any, label: str) -> dict[str, Any]:
    expected = {
        "approved",
        "reviewed_at_utc",
        "identity_score",
        "naturalness_score",
        "artifact_severity",
        "text_match",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain the exact quality fields."
        )
    approved = value["approved"]
    text_match = value["text_match"]
    if not isinstance(approved, bool) or not isinstance(text_match, bool):
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.approved and {label}.text_match must be boolean."
        )
    reviewed_at = _optional_text(
        value["reviewed_at_utc"],
        f"{label}.reviewed_at_utc",
        max_length=100,
    )
    if approved and (reviewed_at is None or not text_match):
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.approved requires a review time and exact text match."
        )
    return {
        "approved": approved,
        "reviewed_at_utc": reviewed_at,
        "identity_score": _score(value["identity_score"], f"{label}.identity_score"),
        "naturalness_score": _score(
            value["naturalness_score"],
            f"{label}.naturalness_score",
        ),
        "artifact_severity": _score(
            value["artifact_severity"],
            f"{label}.artifact_severity",
        ),
        "text_match": text_match,
    }


def _validate_delivery(value: Any, label: str) -> dict[str, Any]:
    expected = {"approved", "labels", "instruction", "score"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain approved, labels, instruction, and score."
        )
    approved = value["approved"]
    if not isinstance(approved, bool):
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.approved must be boolean."
        )
    labels = _string_list(value["labels"], f"{label}.labels")
    instruction = _text(
        value["instruction"],
        f"{label}.instruction",
        allow_empty=True,
        max_length=1200,
    )
    if approved and not labels and not instruction:
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.approved requires a delivery label or instruction."
        )
    return {
        "approved": approved,
        "labels": labels,
        "instruction": instruction,
        "score": _score(value["score"], f"{label}.score"),
    }


def _validate_compatibility(value: Any, label: str) -> dict[str, Any]:
    expected = {"backends", "languages", "speaker_classes"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain backends, languages, and speaker_classes."
        )
    backends = _string_list(value["backends"], f"{label}.backends")
    languages = _string_list(value["languages"], f"{label}.languages")
    if not backends or not languages:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must name at least one backend and language."
        )
    return {
        "backends": backends,
        "languages": languages,
        "speaker_classes": _string_list(
            value["speaker_classes"],
            f"{label}.speaker_classes",
        ),
    }


def _validate_preprocessing(value: Any, label: str) -> dict[str, Any]:
    expected = {"pipeline_id", "operations", "fingerprint"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain pipeline_id, operations, and fingerprint."
        )
    pipeline_id = _text(value["pipeline_id"], f"{label}.pipeline_id")
    operations = _safe_json(value["operations"], f"{label}.operations")
    if not isinstance(operations, list):
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.operations must be a JSON list."
        )
    fingerprint = _sha(value["fingerprint"], f"{label}.fingerprint")
    expected_fingerprint = fingerprint_value(
        {"pipeline_id": pipeline_id, "operations": operations}
    )
    if fingerprint != expected_fingerprint:
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.fingerprint does not match its pipeline and operations."
        )
    return {
        "pipeline_id": pipeline_id,
        "operations": operations,
        "fingerprint": fingerprint,
    }


def _validate_pronunciation(value: Any, label: str) -> dict[str, Any]:
    expected = {"registry_fingerprint", "entry_ids"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain registry_fingerprint and entry_ids."
        )
    return {
        "registry_fingerprint": _sha(
            value["registry_fingerprint"],
            f"{label}.registry_fingerprint",
            allow_none=True,
        ),
        "entry_ids": _string_list(value["entry_ids"], f"{label}.entry_ids"),
    }


def _validate_advisory(value: Any, label: str) -> dict[str, Any]:
    expected = {
        "speaker_label",
        "diarization_cluster",
        "speaker_embedding_fingerprint",
        "asr_tags",
        "learned_emotion_labels",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} must contain the exact advisory evidence fields."
        )
    return {
        "speaker_label": _optional_text(
            value["speaker_label"],
            f"{label}.speaker_label",
            max_length=200,
        ),
        "diarization_cluster": _optional_text(
            value["diarization_cluster"],
            f"{label}.diarization_cluster",
            max_length=200,
        ),
        "speaker_embedding_fingerprint": _sha(
            value["speaker_embedding_fingerprint"],
            f"{label}.speaker_embedding_fingerprint",
            allow_none=True,
        ),
        "asr_tags": _string_list(value["asr_tags"], f"{label}.asr_tags"),
        "learned_emotion_labels": _string_list(
            value["learned_emotion_labels"],
            f"{label}.learned_emotion_labels",
        ),
    }


def _validate_sample(value: Any, index: int) -> dict[str, Any]:
    label = f"samples[{index}]"
    expected = {
        "sample_id",
        "order",
        "audio_path",
        "audio_sha256",
        "transcript",
        "transcript_sha256",
        "language",
        "provenance",
        "quality",
        "delivery",
        "compatibility",
        "preprocessing",
        "pronunciation",
        "advisory",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            f"{label} has unexpected or missing fields."
        )
    sample_id = _text(value["sample_id"], f"{label}.sample_id")
    if not _SAMPLE_ID_RE.fullmatch(sample_id):
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.sample_id is invalid."
        )
    order = value["order"]
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.order must be a non-negative integer."
        )
    transcript = _text(
        value["transcript"],
        f"{label}.transcript",
        max_length=12000,
    )
    transcript_sha = _sha(
        value["transcript_sha256"],
        f"{label}.transcript_sha256",
    )
    expected_transcript_sha = hashlib.sha256(
        transcript.encode("utf-8")
    ).hexdigest()
    if transcript_sha != expected_transcript_sha:
        raise ProductionVoiceEvidenceValidationError(
            f"{label}.transcript_sha256 does not match its transcript."
        )
    return {
        "sample_id": sample_id,
        "order": order,
        "audio_path": _relative_path(value["audio_path"], f"{label}.audio_path"),
        "audio_sha256": _sha(value["audio_sha256"], f"{label}.audio_sha256"),
        "transcript": transcript,
        "transcript_sha256": transcript_sha,
        "language": _text(value["language"], f"{label}.language", max_length=100),
        "provenance": _validate_provenance(value["provenance"], f"{label}.provenance"),
        "quality": _validate_quality(value["quality"], f"{label}.quality"),
        "delivery": _validate_delivery(value["delivery"], f"{label}.delivery"),
        "compatibility": _validate_compatibility(
            value["compatibility"],
            f"{label}.compatibility",
        ),
        "preprocessing": _validate_preprocessing(
            value["preprocessing"],
            f"{label}.preprocessing",
        ),
        "pronunciation": _validate_pronunciation(
            value["pronunciation"],
            f"{label}.pronunciation",
        ),
        "advisory": _validate_advisory(value["advisory"], f"{label}.advisory"),
    }


def compute_evidence_set_fingerprint(value: Mapping[str, Any]) -> str:
    payload = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != "evidence_set_fingerprint"
    }
    # JSON distinguishes ``5`` from ``5.0`` even though the review contract
    # treats them as the same score. Normalize the score fields exactly as the
    # validator does before hashing so equivalent human-review data has one
    # stable fingerprint.
    samples = payload.get("samples")
    if isinstance(samples, list):
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            quality = sample.get("quality")
            if isinstance(quality, dict):
                for key in (
                    "identity_score",
                    "naturalness_score",
                    "artifact_severity",
                ):
                    if quality.get(key) is not None:
                        quality[key] = float(quality[key])
            delivery = sample.get("delivery")
            if isinstance(delivery, dict) and delivery.get("score") is not None:
                delivery["score"] = float(delivery["score"])
    return fingerprint_value(payload)


def _speaker_evidence_conflicts(samples: list[dict[str, Any]]) -> dict[str, Any]:
    labels = sorted(
        {
            item["advisory"]["speaker_label"]
            for item in samples
            if item["quality"]["approved"]
            and item["delivery"]["approved"]
            and item["advisory"]["speaker_label"]
        }
    )
    clusters = sorted(
        {
            item["advisory"]["diarization_cluster"]
            for item in samples
            if item["quality"]["approved"]
            and item["delivery"]["approved"]
            and item["advisory"]["diarization_cluster"]
        }
    )
    return {
        "speaker_labels": labels,
        "diarization_clusters": clusters,
        "conflict": len(labels) > 1 or len(clusters) > 1,
    }


def validate_production_voice_evidence_set(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "voice_id",
        "canonical_name",
        "character_id",
        "status",
        "language",
        "identity_binding",
        "samples",
        "default_sample_id",
        "speaker_evidence_review",
        "evidence_set_fingerprint",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionVoiceEvidenceValidationError(
            "Production Voice evidence set has unexpected or missing fields."
        )
    if value["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise ProductionVoiceEvidenceValidationError(
            "Unsupported Production Voice evidence schema version."
        )
    samples_value = value["samples"]
    if not isinstance(samples_value, list) or not samples_value:
        raise ProductionVoiceEvidenceValidationError(
            "Production Voice evidence requires at least one sample."
        )
    samples = [_validate_sample(item, index) for index, item in enumerate(samples_value)]
    sample_ids = [item["sample_id"] for item in samples]
    orders = [item["order"] for item in samples]
    if len(set(sample_ids)) != len(sample_ids):
        raise ProductionVoiceEvidenceValidationError("Sample IDs must be unique.")
    if len(set(orders)) != len(orders):
        raise ProductionVoiceEvidenceValidationError("Sample order values must be unique.")
    if samples != sorted(samples, key=lambda item: (item["order"], item["sample_id"])):
        raise ProductionVoiceEvidenceValidationError(
            "Samples must be stored in deterministic order."
        )
    status = _text(value["status"], "status")
    if status not in {"draft", "approved"}:
        raise ProductionVoiceEvidenceValidationError(
            "Production Voice evidence status must be draft or approved."
        )
    default_sample_id = _text(value["default_sample_id"], "default_sample_id")
    if default_sample_id not in sample_ids:
        raise ProductionVoiceEvidenceValidationError(
            "default_sample_id does not identify a sample."
        )
    identity_binding = _validate_identity_binding(value["identity_binding"])
    speaker_review = _validate_speaker_evidence_review(
        value["speaker_evidence_review"]
    )
    approved_samples = [
        item
        for item in samples
        if item["quality"]["approved"]
        and item["delivery"]["approved"]
        and item["quality"]["text_match"]
    ]
    conflicts = _speaker_evidence_conflicts(samples)
    if status == "approved":
        evidence_language = _text(
            value["language"],
            "language",
            max_length=100,
        )
        if any(item["language"] != evidence_language for item in samples):
            raise ProductionVoiceEvidenceValidationError(
                "All samples in an approved Production Voice set must use the set language."
            )
        if (
            identity_binding["source"] == "cast"
            and _optional_text(value["character_id"], "character_id") is None
        ):
            raise ProductionVoiceEvidenceValidationError(
                "Cast-approved Production Voice identity requires a character_id."
            )
        if identity_binding["status"] != "approved":
            raise ProductionVoiceEvidenceValidationError(
                "Approved Production Voice evidence requires explicit identity approval."
            )
        if not approved_samples:
            raise ProductionVoiceEvidenceValidationError(
                "Approved Production Voice evidence requires an approved sample."
            )
        if default_sample_id not in {item["sample_id"] for item in approved_samples}:
            raise ProductionVoiceEvidenceValidationError(
                "The default Production Voice sample must be approved."
            )
        if conflicts["conflict"] and not (
            speaker_review["status"] == "reviewed"
            and speaker_review["decision"] == "accept"
        ):
            raise ProductionVoiceEvidenceValidationError(
                "Conflicting speaker or diarization evidence requires explicit acceptance."
            )
    normalized = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "voice_id": _text(value["voice_id"], "voice_id"),
        "canonical_name": _text(value["canonical_name"], "canonical_name"),
        "character_id": _optional_text(value["character_id"], "character_id"),
        "status": status,
        "language": _text(value["language"], "language", max_length=100),
        "identity_binding": identity_binding,
        "samples": samples,
        "default_sample_id": default_sample_id,
        "speaker_evidence_review": speaker_review,
        "evidence_set_fingerprint": _sha(
            value["evidence_set_fingerprint"],
            "evidence_set_fingerprint",
        ),
    }
    expected_fingerprint = compute_evidence_set_fingerprint(normalized)
    if normalized["evidence_set_fingerprint"] != expected_fingerprint:
        raise ProductionVoiceEvidenceValidationError(
            "Production Voice evidence fingerprint does not match its contents."
        )
    return normalized


def read_production_voice_evidence_set(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionVoiceEvidenceValidationError(
            f"Production Voice evidence could not be read: {exc}"
        ) from exc
    return validate_production_voice_evidence_set(value)


def resolve_evidence_set_path(
    path: str | Path,
    *,
    project_root: str | Path,
) -> Path:
    requested = Path(path).expanduser()
    root = Path(project_root).expanduser().resolve()
    candidates = [root, *root.parents]
    anchors = [
        candidate
        for candidate in candidates[:8]
        if (
            (candidate / "voice_config.json").is_file()
            or (candidate / "chunks.json").is_file()
            or (candidate / "production_voice_evidence").is_dir()
        )
    ]
    if root not in anchors:
        anchors.insert(0, root)
    if requested.is_absolute():
        target = requested.resolve()
        if not any(target.is_relative_to(anchor) for anchor in anchors):
            raise ProductionVoiceEvidenceValidationError(
                "Production Voice evidence must remain inside the project."
            )
        if not target.is_file():
            raise ProductionVoiceEvidenceValidationError(
                "Production Voice evidence file is missing."
            )
        return target
    if ".." in requested.parts:
        raise ProductionVoiceEvidenceValidationError(
            "Production Voice evidence path must not escape the project."
        )
    for anchor in anchors:
        candidate = (anchor / requested).resolve()
        try:
            candidate.relative_to(anchor)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    raise ProductionVoiceEvidenceValidationError(
        "Production Voice evidence file is missing."
    )


def _eligible_samples(
    evidence: dict[str, Any],
    *,
    backend: str,
    language: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in evidence["samples"]
        if item["quality"]["approved"]
        and item["delivery"]["approved"]
        and item["quality"]["text_match"]
        and backend in item["compatibility"]["backends"]
        and language in item["compatibility"]["languages"]
    ]


def _sample_audio_path(
    evidence_path: Path,
    sample: Mapping[str, Any],
    *,
    verify_audio: bool,
) -> Path:
    reference = (evidence_path.parent / sample["audio_path"]).resolve()
    try:
        reference.relative_to(evidence_path.parent)
    except ValueError as exc:
        raise ProductionVoiceEvidenceValidationError(
            "Production Voice sample audio escaped its evidence directory."
        ) from exc
    if verify_audio:
        if not reference.is_file():
            raise ProductionVoiceEvidenceValidationError(
                f"Production Voice sample audio is missing: {sample['sample_id']}."
            )
        if sha256_file(reference) != sample["audio_sha256"]:
            raise ProductionVoiceEvidenceValidationError(
                f"Production Voice sample audio fingerprint changed: {sample['sample_id']}."
            )
    return reference


def _selection_score(sample: Mapping[str, Any], instruction: str) -> int:
    haystack = instruction.casefold()
    tokens = set(re.findall(r"[a-z0-9]+", haystack))
    score = 0
    for label in sample["delivery"]["labels"]:
        normalized = label.casefold()
        if normalized in haystack:
            score += 3
        score += sum(1 for token in re.findall(r"[a-z0-9]+", normalized) if token in tokens)
    return score


def _distinct_join(parts: list[str]) -> str:
    result: list[str] = []
    for part in parts:
        normalized = str(part or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return " ".join(result) or "Natural, clear delivery."


def resolve_production_voice_prompt(
    *,
    evidence_set_path: str | Path,
    project_root: str | Path,
    instruction: str,
    backend: str,
    language: str,
    persistent_style: str = "",
    pronunciation_resolution: Mapping[str, Any] | None = None,
    expected_evidence_fingerprint: str | None = None,
    verify_audio: bool = True,
) -> dict[str, Any]:
    target = resolve_evidence_set_path(
        evidence_set_path,
        project_root=project_root,
    )
    evidence = read_production_voice_evidence_set(target)
    if (
        expected_evidence_fingerprint is not None
        and evidence["evidence_set_fingerprint"]
        != _sha(
            expected_evidence_fingerprint,
            "expected_evidence_fingerprint",
        )
    ):
        raise ProductionVoiceEvidenceConflictError(
            "Production Voice evidence changed after its listening approval."
        )
    if evidence["status"] != "approved":
        raise ProductionVoiceEvidenceConflictError(
            "Production Voice evidence is not approved."
        )
    requested_language = _text(language, "language", max_length=100)
    selected_backend = _text(backend, "backend", max_length=200)
    eligible = _eligible_samples(
        evidence,
        backend=selected_backend,
        language=requested_language,
    )
    if not eligible:
        raise ProductionVoiceEvidenceConflictError(
            "No approved Production Voice sample is compatible with this backend and language."
        )
    audio_paths = {
        item["sample_id"]: _sample_audio_path(
            target,
            item,
            verify_audio=verify_audio,
        )
        for item in evidence["samples"]
    }
    raw_instruction = str(instruction or "")
    explicit = _EXPLICIT_SAMPLE_RE.search(raw_instruction)
    clean_instruction = _EXPLICIT_SAMPLE_RE.sub("", raw_instruction).strip()
    reason = "default"
    if explicit is not None:
        requested_id = explicit.group(1).casefold()
        selected = next(
            (item for item in eligible if item["sample_id"] == requested_id),
            None,
        )
        if selected is None:
            raise ProductionVoiceEvidenceConflictError(
                "The explicitly requested Production Voice sample is unavailable or incompatible."
            )
        reason = "explicit_sample"
    else:
        ranked = sorted(
            eligible,
            key=lambda item: (
                -_selection_score(item, clean_instruction),
                item["order"],
                item["sample_id"],
            ),
        )
        selected = ranked[0]
        if _selection_score(selected, clean_instruction) > 0:
            reason = "delivery_match"
        elif evidence["default_sample_id"] in {
            item["sample_id"] for item in eligible
        }:
            selected = next(
                item
                for item in eligible
                if item["sample_id"] == evidence["default_sample_id"]
            )
            reason = "default"
        else:
            reason = "ordered_fallback"
    reference = audio_paths[selected["sample_id"]]
    prompt_instruction = _distinct_join(
        [
            clean_instruction,
            str(persistent_style or ""),
            selected["delivery"]["instruction"],
        ]
    )
    pronunciation = (
        _safe_json(pronunciation_resolution, "pronunciation_resolution")
        if pronunciation_resolution is not None
        else copy.deepcopy(selected["pronunciation"])
    )
    pronunciation_fingerprint = fingerprint_value(pronunciation)
    dependency_payload = {
        "schema_version": PROMPT_SCHEMA_VERSION,
        "evidence_set_fingerprint": evidence["evidence_set_fingerprint"],
        "ordered_sample_ids": [item["sample_id"] for item in evidence["samples"]],
        "selected_sample_id": selected["sample_id"],
        "backend": selected_backend,
        "language": requested_language,
        "instruction": prompt_instruction,
        "audio_sha256": selected["audio_sha256"],
        "transcript_sha256": selected["transcript_sha256"],
        "provenance": selected["provenance"],
        "quality": selected["quality"],
        "delivery": selected["delivery"],
        "compatibility": selected["compatibility"],
        "preprocessing": selected["preprocessing"],
        "pronunciation_fingerprint": pronunciation_fingerprint,
    }
    dependency_fingerprint = fingerprint_value(dependency_payload)
    prompt_fingerprint = fingerprint_value(
        {
            "schema_version": PROMPT_SCHEMA_VERSION,
            "dependency_fingerprint": dependency_fingerprint,
            "reference_text": selected["transcript"],
            "instruction": prompt_instruction,
        }
    )
    conflicts = _speaker_evidence_conflicts(evidence["samples"])
    return {
        "schema_version": PROMPT_SCHEMA_VERSION,
        "voice_id": evidence["voice_id"],
        "canonical_name": evidence["canonical_name"],
        "character_id": evidence["character_id"],
        "evidence_set_path": str(target),
        "evidence_set_fingerprint": evidence["evidence_set_fingerprint"],
        "sample_id": selected["sample_id"],
        "selection_reason": reason,
        "ref_audio": str(reference),
        "ref_audio_sha256": selected["audio_sha256"],
        "ref_text": selected["transcript"],
        "reference_language": selected["language"],
        "instruction": prompt_instruction,
        "backend": selected_backend,
        "language": requested_language,
        "preprocessing_fingerprint": selected["preprocessing"]["fingerprint"],
        "pronunciation_fingerprint": pronunciation_fingerprint,
        "dependency_fingerprint": dependency_fingerprint,
        "prompt_fingerprint": prompt_fingerprint,
        "speaker_evidence_conflicts": conflicts,
        "advisory_evidence": {
            **copy.deepcopy(selected["advisory"]),
            "authoritative_identity": False,
            "approval_source": False,
        },
    }
