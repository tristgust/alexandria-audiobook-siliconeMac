from __future__ import annotations

import copy
import re
from collections import Counter
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

from generation_state import fingerprint_text, fingerprint_value
from model_registry import model_spec


INSTRUCTION_DATASET_SCHEMA_VERSION = 1
INSTRUCTION_DATASET_CONTRACT = "alexandria_instruction_dataset_v1"
INSTRUCTION_CHECKPOINT_CONTRACT = "alexandria_instruction_checkpoint_v1"
INSTRUCTION_RECEIPT_CONTRACT = "alexandria_instruction_training_receipt_v1"
RECORD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}")
DATASET_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,63}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SPLITS = ("train", "validation", "test")
SOURCE_KINDS = {"synthetic", "existing_recordings"}
LICENSE_SCOPES = {"owned", "permissive", "synthetic"}
TRAINING_KINDS = {"sft", "lora"}
RECEIPT_STATUSES = {"completed", "failed", "cancelled"}
REVIEW_STATUSES = {"approved", "rejected", "pending"}

DELIVERY_LABEL_ALIASES = {
    "neutral": "neutral",
    "natural": "neutral",
    "conversational": "neutral",
    "urgent": "urgent",
    "urgency": "urgent",
    "restrained anger": "restrained_anger",
    "restrained_anger": "restrained_anger",
    "controlled anger": "restrained_anger",
    "anger": "restrained_anger",
    "angry": "restrained_anger",
    "panic": "panic",
    "panicked": "panic",
    "grief": "grief",
    "grieving": "grief",
    "sad": "grief",
    "whisper": "whisper",
    "whispered": "whisper",
    "sarcasm": "sarcasm",
    "sarcastic": "sarcasm",
    "dry sarcasm": "sarcasm",
}
DELIVERY_LABELS = frozenset(DELIVERY_LABEL_ALIASES.values())


class InstructionDatasetError(ValueError):
    def __init__(self, code: str, message: str, *, path: str | None = None):
        super().__init__(message)
        self.code = code
        self.path = path

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "path": self.path}


def _mapping(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InstructionDatasetError(
            "instruction_dataset_object_required",
            f"{path} must be an object.",
            path=path,
        )
    return value


def _list(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise InstructionDatasetError(
            "instruction_dataset_list_required",
            f"{path} must be an array.",
            path=path,
        )
    return value


def _text(
    value: Any,
    *,
    path: str,
    maximum: int = 4096,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise InstructionDatasetError(
            "instruction_dataset_text_required",
            f"{path} must be text.",
            path=path,
        )
    normalized = " ".join(value.split())
    if not normalized and not allow_empty:
        raise InstructionDatasetError(
            "instruction_dataset_text_empty",
            f"{path} must not be empty.",
            path=path,
        )
    if len(normalized) > maximum:
        raise InstructionDatasetError(
            "instruction_dataset_text_too_long",
            f"{path} exceeds {maximum} characters.",
            path=path,
        )
    return normalized


def _sha256(value: Any, *, path: str) -> str:
    text = _text(value, path=path, maximum=64)
    if not SHA256_RE.fullmatch(text):
        raise InstructionDatasetError(
            "instruction_dataset_sha256_invalid",
            f"{path} must be a lowercase SHA-256 value.",
            path=path,
        )
    return text


def _utc(value: Any, *, path: str) -> str:
    text = _text(value, path=path, maximum=40)
    if not text.endswith("Z"):
        raise InstructionDatasetError(
            "instruction_dataset_timestamp_invalid",
            f"{path} must be an explicit UTC timestamp ending in Z.",
            path=path,
        )
    try:
        datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise InstructionDatasetError(
            "instruction_dataset_timestamp_invalid",
            f"{path} is not a valid UTC timestamp.",
            path=path,
        ) from exc
    return text


def _positive_int(value: Any, *, path: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InstructionDatasetError(
            "instruction_dataset_integer_invalid",
            f"{path} must be an integer greater than or equal to {minimum}.",
            path=path,
        )
    return value


def _relative_audio_path(value: Any, *, path: str) -> str:
    text = _text(value, path=path, maximum=512).replace("\\", "/")
    candidate = PurePosixPath(text)
    if (
        candidate.is_absolute()
        or text.startswith("~")
        or ":" in candidate.parts[0]
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise InstructionDatasetError(
            "instruction_dataset_audio_path_unsafe",
            f"{path} must be a confined relative path.",
            path=path,
        )
    if candidate.suffix.casefold() not in {".wav", ".flac", ".mp3", ".m4a", ".ogg"}:
        raise InstructionDatasetError(
            "instruction_dataset_audio_format_invalid",
            f"{path} has an unsupported audio extension.",
            path=path,
        )
    return candidate.as_posix()


def normalize_delivery_label(value: Any) -> str:
    label = _text(value, path="delivery_label", maximum=80).casefold()
    normalized = DELIVERY_LABEL_ALIASES.get(label.replace("-", " "))
    if normalized is None:
        normalized = DELIVERY_LABEL_ALIASES.get(label)
    if normalized is None:
        raise InstructionDatasetError(
            "instruction_dataset_delivery_label_invalid",
            f"Unsupported delivery label: {value!r}.",
            path="delivery_labels",
        )
    return normalized


def _normalize_delivery_labels(value: Any, *, path: str) -> list[str]:
    labels = [normalize_delivery_label(item) for item in _list(value, path=path)]
    labels = list(dict.fromkeys(labels))
    if not labels:
        raise InstructionDatasetError(
            "instruction_dataset_delivery_labels_empty",
            f"{path} must contain at least one delivery label.",
            path=path,
        )
    return sorted(labels)


def _validate_provenance(
    value: Any,
    *,
    path: str,
    transcript: str,
    instruction: str,
) -> dict[str, Any]:
    source = _mapping(value, path=path)
    source_kind = _text(source.get("source_kind"), path=f"{path}.source_kind", maximum=40)
    if source_kind not in SOURCE_KINDS:
        raise InstructionDatasetError(
            "instruction_dataset_source_kind_invalid",
            f"{path}.source_kind must be synthetic or existing_recordings.",
            path=f"{path}.source_kind",
        )
    license_scope = _text(
        source.get("license_scope"),
        path=f"{path}.license_scope",
        maximum=40,
    )
    if license_scope not in LICENSE_SCOPES:
        raise InstructionDatasetError(
            "instruction_dataset_license_scope_invalid",
            f"{path}.license_scope is unsupported.",
            path=f"{path}.license_scope",
        )
    if source_kind == "synthetic" and license_scope != "synthetic":
        raise InstructionDatasetError(
            "instruction_dataset_license_scope_mismatch",
            "Synthetic records must use the synthetic license scope.",
            path=f"{path}.license_scope",
        )
    if source_kind == "existing_recordings" and license_scope == "synthetic":
        raise InstructionDatasetError(
            "instruction_dataset_license_scope_mismatch",
            "Owned recordings cannot use the synthetic license scope.",
            path=f"{path}.license_scope",
        )
    if source.get("same_speaker_asserted") is not True:
        raise InstructionDatasetError(
            "instruction_dataset_same_speaker_missing",
            f"{path}.same_speaker_asserted must be true.",
            path=f"{path}.same_speaker_asserted",
        )
    transcript_sha256 = _sha256(
        source.get("transcript_sha256"),
        path=f"{path}.transcript_sha256",
    )
    instruction_sha256 = _sha256(
        source.get("instruction_sha256"),
        path=f"{path}.instruction_sha256",
    )
    if transcript_sha256 != fingerprint_text(transcript):
        raise InstructionDatasetError(
            "instruction_dataset_transcript_fingerprint_mismatch",
            "Transcript provenance does not match the record transcript.",
            path=f"{path}.transcript_sha256",
        )
    if instruction_sha256 != fingerprint_text(instruction):
        raise InstructionDatasetError(
            "instruction_dataset_instruction_fingerprint_mismatch",
            "Instruction provenance does not match the record instruction.",
            path=f"{path}.instruction_sha256",
        )
    return {
        "source_kind": source_kind,
        "project_id": _text(source.get("project_id"), path=f"{path}.project_id", maximum=160),
        "character_id": _text(source.get("character_id"), path=f"{path}.character_id", maximum=160),
        "clip_id": _text(source.get("clip_id"), path=f"{path}.clip_id", maximum=160),
        "audio_sha256": _sha256(source.get("audio_sha256"), path=f"{path}.audio_sha256"),
        "transcript_sha256": transcript_sha256,
        "instruction_sha256": instruction_sha256,
        "source_manifest_sha256": _sha256(
            source.get("source_manifest_sha256"),
            path=f"{path}.source_manifest_sha256",
        ),
        "reviewed_source_fingerprint": _sha256(
            source.get("reviewed_source_fingerprint"),
            path=f"{path}.reviewed_source_fingerprint",
        ),
        "license_scope": license_scope,
        "same_speaker_asserted": True,
    }


def _validate_review(value: Any, *, path: str, require_approved: bool) -> dict[str, Any]:
    review = _mapping(value, path=path)
    status = _text(review.get("status"), path=f"{path}.status", maximum=30)
    if status not in REVIEW_STATUSES:
        raise InstructionDatasetError(
            "instruction_dataset_review_status_invalid",
            f"{path}.status is unsupported.",
            path=f"{path}.status",
        )
    if require_approved and status != "approved":
        raise InstructionDatasetError(
            "instruction_dataset_review_not_approved",
            "Only approved reviewed records can enter a training manifest.",
            path=f"{path}.status",
        )
    truth_fields = (
        "transcript_exact",
        "identity_retained",
        "delivery_labels_verified",
        "audio_quality_approved",
    )
    for key in truth_fields:
        if require_approved and review.get(key) is not True:
            raise InstructionDatasetError(
                "instruction_dataset_review_assertion_missing",
                f"{path}.{key} must be true for an approved record.",
                path=f"{path}.{key}",
            )
    return {
        "status": status,
        "reviewer_id": _text(review.get("reviewer_id"), path=f"{path}.reviewer_id", maximum=160),
        "reviewed_at_utc": _utc(review.get("reviewed_at_utc"), path=f"{path}.reviewed_at_utc"),
        "transcript_exact": review.get("transcript_exact") is True,
        "identity_retained": review.get("identity_retained") is True,
        "delivery_labels_verified": review.get("delivery_labels_verified") is True,
        "audio_quality_approved": review.get("audio_quality_approved") is True,
        "notes": _text(
            review.get("notes", ""),
            path=f"{path}.notes",
            maximum=2000,
            allow_empty=True,
        ),
    }


def validate_instruction_record(
    value: Any,
    *,
    require_approved: bool = True,
) -> dict[str, Any]:
    record = _mapping(value, path="record")
    schema_version = record.get("schema_version")
    if schema_version != INSTRUCTION_DATASET_SCHEMA_VERSION:
        raise InstructionDatasetError(
            "instruction_dataset_schema_unsupported",
            "The instruction dataset record schema is unsupported.",
            path="record.schema_version",
        )
    record_id = _text(record.get("record_id"), path="record.record_id", maximum=128)
    if not RECORD_ID_RE.fullmatch(record_id):
        raise InstructionDatasetError(
            "instruction_dataset_record_id_invalid",
            "record.record_id contains unsupported characters.",
            path="record.record_id",
        )
    split = _text(record.get("split"), path="record.split", maximum=20)
    if split not in SPLITS:
        raise InstructionDatasetError(
            "instruction_dataset_split_invalid",
            "record.split must be train, validation, or test.",
            path="record.split",
        )
    transcript = _text(record.get("transcript"), path="record.transcript", maximum=8000)
    instruction = _text(record.get("instruction"), path="record.instruction", maximum=4000)
    normalized = {
        "schema_version": INSTRUCTION_DATASET_SCHEMA_VERSION,
        "record_id": record_id,
        "audio_path": _relative_audio_path(record.get("audio_path"), path="record.audio_path"),
        "transcript": transcript,
        "instruction": instruction,
        "delivery_labels": _normalize_delivery_labels(
            record.get("delivery_labels"),
            path="record.delivery_labels",
        ),
        "split": split,
        "duration_ms": _positive_int(record.get("duration_ms"), path="record.duration_ms"),
        "sample_rate": _positive_int(
            record.get("sample_rate"),
            path="record.sample_rate",
            minimum=16000,
        ),
        "channels": _positive_int(record.get("channels"), path="record.channels"),
        "provenance": _validate_provenance(
            record.get("provenance"),
            path="record.provenance",
            transcript=transcript,
            instruction=instruction,
        ),
        "review": _validate_review(
            record.get("review"),
            path="record.review",
            require_approved=require_approved,
        ),
    }
    if normalized["channels"] not in {1, 2}:
        raise InstructionDatasetError(
            "instruction_dataset_channels_invalid",
            "record.channels must be one or two.",
            path="record.channels",
        )
    normalized["record_fingerprint"] = fingerprint_value(normalized)
    supplied = record.get("record_fingerprint")
    if supplied is not None and supplied != normalized["record_fingerprint"]:
        raise InstructionDatasetError(
            "instruction_dataset_record_fingerprint_mismatch",
            "The record fingerprint does not match the normalized record.",
            path="record.record_fingerprint",
        )
    return normalized


def _split_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for split in SPLITS:
        record_ids = sorted(
            item["record_id"] for item in records if item["split"] == split
        )
        summary[split] = {
            "record_ids": record_ids,
            "count": len(record_ids),
            "fingerprint": fingerprint_value(record_ids),
        }
    return summary


def build_instruction_dataset_manifest(
    *,
    dataset_id: str,
    records: list[Mapping[str, Any]],
    base_model_key: str,
    created_at_utc: str,
    split_policy: Mapping[str, Any],
) -> dict[str, Any]:
    if not DATASET_ID_RE.fullmatch(dataset_id):
        raise InstructionDatasetError(
            "instruction_dataset_id_invalid",
            "dataset_id contains unsupported characters.",
            path="dataset_id",
        )
    if not records:
        raise InstructionDatasetError(
            "instruction_dataset_records_empty",
            "An instruction-aware dataset requires at least one record.",
            path="records",
        )
    normalized = [validate_instruction_record(item) for item in records]
    record_ids = [item["record_id"] for item in normalized]
    if len(record_ids) != len(set(record_ids)):
        raise InstructionDatasetError(
            "instruction_dataset_record_id_duplicate",
            "Instruction dataset record IDs must be unique.",
            path="records",
        )
    audio_hashes = [item["provenance"]["audio_sha256"] for item in normalized]
    if len(audio_hashes) != len(set(audio_hashes)):
        duplicates = {
            value for value, count in Counter(audio_hashes).items() if count > 1
        }
        duplicate_splits = {
            item["split"]
            for item in normalized
            if item["provenance"]["audio_sha256"] in duplicates
        }
        if len(duplicate_splits) > 1:
            raise InstructionDatasetError(
                "instruction_dataset_split_leakage",
                "The same audio bytes cannot appear in more than one split.",
                path="records",
            )
        raise InstructionDatasetError(
            "instruction_dataset_audio_duplicate",
            "The same audio bytes cannot appear twice in a dataset.",
            path="records",
        )
    source_kinds = {item["provenance"]["source_kind"] for item in normalized}
    project_ids = {item["provenance"]["project_id"] for item in normalized}
    character_ids = {item["provenance"]["character_id"] for item in normalized}
    if len(source_kinds) != 1:
        raise InstructionDatasetError(
            "instruction_dataset_source_kind_mixed",
            "One dataset cannot mix synthetic and existing-recording sources.",
            path="records",
        )
    if len(project_ids) != 1 or len(character_ids) != 1:
        raise InstructionDatasetError(
            "instruction_dataset_identity_mixed",
            "Every record must belong to one stable project and character identity.",
            path="records",
        )
    splits = _split_summary(normalized)
    if splits["train"]["count"] == 0 or splits["validation"]["count"] == 0:
        raise InstructionDatasetError(
            "instruction_dataset_required_split_missing",
            "Training and validation splits must both contain records.",
            path="records",
        )
    split_policy_value = copy.deepcopy(dict(_mapping(split_policy, path="split_policy")))
    policy_name = _text(
        split_policy_value.get("name"),
        path="split_policy.name",
        maximum=80,
    )
    policy_seed = _positive_int(
        split_policy_value.get("seed"),
        path="split_policy.seed",
        minimum=0,
    )
    if split_policy_value.get("group_by_audio_sha256") is not True:
        raise InstructionDatasetError(
            "instruction_dataset_split_grouping_missing",
            "split_policy.group_by_audio_sha256 must be true.",
            path="split_policy.group_by_audio_sha256",
        )
    spec = model_spec(base_model_key)
    ordered = sorted(normalized, key=lambda item: item["record_id"])
    label_counts = Counter(
        label for item in ordered for label in item["delivery_labels"]
    )
    manifest = {
        "schema_version": INSTRUCTION_DATASET_SCHEMA_VERSION,
        "contract": INSTRUCTION_DATASET_CONTRACT,
        "dataset_id": dataset_id,
        "status": "approved",
        "created_at_utc": _utc(created_at_utc, path="created_at_utc"),
        "project_id": next(iter(project_ids)),
        "character_id": next(iter(character_ids)),
        "source_kind": next(iter(source_kinds)),
        "record_count": len(ordered),
        "records": ordered,
        "records_fingerprint": fingerprint_value(
            [item["record_fingerprint"] for item in ordered]
        ),
        "splits": splits,
        "split_policy": {
            "name": policy_name,
            "seed": policy_seed,
            "group_by_audio_sha256": True,
        },
        "delivery_label_counts": dict(sorted(label_counts.items())),
        "fields": {
            "audio": "audio_path",
            "transcript": "transcript",
            "instruction": "instruction",
            "delivery_labels": "delivery_labels",
        },
        "base_model": {
            "key": spec.key,
            "repo_id": spec.repo_id,
            "revision": spec.revision,
        },
        "review_required": True,
        "production_assignment_supported": False,
    }
    manifest["manifest_fingerprint"] = fingerprint_value(manifest)
    return manifest


def validate_instruction_dataset_manifest(value: Any) -> dict[str, Any]:
    manifest = _mapping(value, path="manifest")
    if manifest.get("contract") != INSTRUCTION_DATASET_CONTRACT:
        raise InstructionDatasetError(
            "instruction_dataset_manifest_contract_invalid",
            "The instruction dataset manifest contract is unsupported.",
            path="manifest.contract",
        )
    expected = build_instruction_dataset_manifest(
        dataset_id=_text(
            manifest.get("dataset_id"),
            path="manifest.dataset_id",
            maximum=64,
        ),
        records=_list(manifest.get("records"), path="manifest.records"),
        base_model_key=_text(
            _mapping(manifest.get("base_model"), path="manifest.base_model").get("key"),
            path="manifest.base_model.key",
            maximum=120,
        ),
        created_at_utc=manifest.get("created_at_utc"),
        split_policy=_mapping(manifest.get("split_policy"), path="manifest.split_policy"),
    )
    if manifest.get("manifest_fingerprint") != expected["manifest_fingerprint"]:
        raise InstructionDatasetError(
            "instruction_dataset_manifest_fingerprint_mismatch",
            "The dataset manifest fingerprint does not match its contents.",
            path="manifest.manifest_fingerprint",
        )
    if manifest.get("production_assignment_supported") is not False:
        raise InstructionDatasetError(
            "instruction_dataset_production_claim_invalid",
            "A dataset manifest cannot claim production assignment support.",
            path="manifest.production_assignment_supported",
        )
    return expected


def build_instruction_checkpoint_contract(
    *,
    manifest: Mapping[str, Any],
    checkpoint_id: str,
    training_kind: str,
    created_at_utc: str,
    step: int,
    hyperparameters: Mapping[str, Any],
    parent_checkpoint_fingerprint: str | None = None,
) -> dict[str, Any]:
    dataset = validate_instruction_dataset_manifest(manifest)
    checkpoint_id = _text(checkpoint_id, path="checkpoint_id", maximum=128)
    if not RECORD_ID_RE.fullmatch(checkpoint_id):
        raise InstructionDatasetError(
            "instruction_checkpoint_id_invalid",
            "checkpoint_id contains unsupported characters.",
            path="checkpoint_id",
        )
    training_kind = _text(training_kind, path="training_kind", maximum=20)
    if training_kind not in TRAINING_KINDS:
        raise InstructionDatasetError(
            "instruction_checkpoint_training_kind_invalid",
            "training_kind must be sft or lora.",
            path="training_kind",
        )
    parameters = copy.deepcopy(dict(_mapping(hyperparameters, path="hyperparameters")))
    if not parameters:
        raise InstructionDatasetError(
            "instruction_checkpoint_hyperparameters_empty",
            "A checkpoint contract requires explicit hyperparameters.",
            path="hyperparameters",
        )
    if parent_checkpoint_fingerprint is not None:
        parent_checkpoint_fingerprint = _sha256(
            parent_checkpoint_fingerprint,
            path="parent_checkpoint_fingerprint",
        )
    value = {
        "schema_version": INSTRUCTION_DATASET_SCHEMA_VERSION,
        "contract": INSTRUCTION_CHECKPOINT_CONTRACT,
        "checkpoint_id": checkpoint_id,
        "training_kind": training_kind,
        "created_at_utc": _utc(created_at_utc, path="created_at_utc"),
        "step": _positive_int(step, path="step", minimum=0),
        "dataset_id": dataset["dataset_id"],
        "dataset_manifest_fingerprint": dataset["manifest_fingerprint"],
        "records_fingerprint": dataset["records_fingerprint"],
        "base_model": copy.deepcopy(dataset["base_model"]),
        "instruction_field": dataset["fields"]["instruction"],
        "hyperparameters": parameters,
        "parent_checkpoint_fingerprint": parent_checkpoint_fingerprint,
        "production_assignment_supported": False,
    }
    value["checkpoint_fingerprint"] = fingerprint_value(value)
    return value


def validate_instruction_checkpoint_contract(
    value: Any,
    *,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    checkpoint = _mapping(value, path="checkpoint")
    if checkpoint.get("contract") != INSTRUCTION_CHECKPOINT_CONTRACT:
        raise InstructionDatasetError(
            "instruction_checkpoint_contract_invalid",
            "The instruction checkpoint contract is unsupported.",
            path="checkpoint.contract",
        )
    expected = build_instruction_checkpoint_contract(
        manifest=manifest,
        checkpoint_id=checkpoint.get("checkpoint_id"),
        training_kind=checkpoint.get("training_kind"),
        created_at_utc=checkpoint.get("created_at_utc"),
        step=checkpoint.get("step"),
        hyperparameters=_mapping(
            checkpoint.get("hyperparameters"),
            path="checkpoint.hyperparameters",
        ),
        parent_checkpoint_fingerprint=checkpoint.get(
            "parent_checkpoint_fingerprint"
        ),
    )
    if checkpoint.get("checkpoint_fingerprint") != expected["checkpoint_fingerprint"]:
        raise InstructionDatasetError(
            "instruction_checkpoint_fingerprint_mismatch",
            "The checkpoint fingerprint does not match its contents.",
            path="checkpoint.checkpoint_fingerprint",
        )
    return expected


def build_instruction_training_receipt(
    *,
    manifest: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    run_id: str,
    status: str,
    started_at_utc: str,
    finished_at_utc: str,
    metrics: Mapping[str, Any],
    output_artifact_fingerprint: str | None = None,
) -> dict[str, Any]:
    dataset = validate_instruction_dataset_manifest(manifest)
    checkpoint_value = validate_instruction_checkpoint_contract(
        checkpoint,
        manifest=dataset,
    )
    run_id = _text(run_id, path="run_id", maximum=128)
    if not RECORD_ID_RE.fullmatch(run_id):
        raise InstructionDatasetError(
            "instruction_receipt_run_id_invalid",
            "run_id contains unsupported characters.",
            path="run_id",
        )
    status = _text(status, path="status", maximum=20)
    if status not in RECEIPT_STATUSES:
        raise InstructionDatasetError(
            "instruction_receipt_status_invalid",
            "Training receipt status is unsupported.",
            path="status",
        )
    metrics_value = copy.deepcopy(dict(_mapping(metrics, path="metrics")))
    if output_artifact_fingerprint is not None:
        output_artifact_fingerprint = _sha256(
            output_artifact_fingerprint,
            path="output_artifact_fingerprint",
        )
    value = {
        "schema_version": INSTRUCTION_DATASET_SCHEMA_VERSION,
        "contract": INSTRUCTION_RECEIPT_CONTRACT,
        "run_id": run_id,
        "status": status,
        "started_at_utc": _utc(started_at_utc, path="started_at_utc"),
        "finished_at_utc": _utc(finished_at_utc, path="finished_at_utc"),
        "dataset_id": dataset["dataset_id"],
        "dataset_manifest_fingerprint": dataset["manifest_fingerprint"],
        "checkpoint_id": checkpoint_value["checkpoint_id"],
        "checkpoint_fingerprint": checkpoint_value["checkpoint_fingerprint"],
        "training_kind": checkpoint_value["training_kind"],
        "metrics": metrics_value,
        "output_artifact_fingerprint": output_artifact_fingerprint,
        "manual_audio_review_required": True,
        "manual_audio_review_status": "pending",
        "production_assignment_supported": False,
    }
    value["receipt_fingerprint"] = fingerprint_value(value)
    return value


def validate_instruction_training_receipt(
    value: Any,
    *,
    manifest: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = _mapping(value, path="receipt")
    if receipt.get("contract") != INSTRUCTION_RECEIPT_CONTRACT:
        raise InstructionDatasetError(
            "instruction_receipt_contract_invalid",
            "The instruction training receipt contract is unsupported.",
            path="receipt.contract",
        )
    expected = build_instruction_training_receipt(
        manifest=manifest,
        checkpoint=checkpoint,
        run_id=receipt.get("run_id"),
        status=receipt.get("status"),
        started_at_utc=receipt.get("started_at_utc"),
        finished_at_utc=receipt.get("finished_at_utc"),
        metrics=_mapping(receipt.get("metrics"), path="receipt.metrics"),
        output_artifact_fingerprint=receipt.get("output_artifact_fingerprint"),
    )
    if receipt.get("receipt_fingerprint") != expected["receipt_fingerprint"]:
        raise InstructionDatasetError(
            "instruction_receipt_fingerprint_mismatch",
            "The training receipt fingerprint does not match its contents.",
            path="receipt.receipt_fingerprint",
        )
    if receipt.get("manual_audio_review_status") != "pending":
        raise InstructionDatasetError(
            "instruction_receipt_listening_claim_invalid",
            "A technical training receipt cannot claim completed human listening.",
            path="receipt.manual_audio_review_status",
        )
    if receipt.get("production_assignment_supported") is not False:
        raise InstructionDatasetError(
            "instruction_receipt_production_claim_invalid",
            "A technical training receipt cannot claim production assignment support.",
            path="receipt.production_assignment_supported",
        )
    return expected
