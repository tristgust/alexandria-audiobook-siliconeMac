from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from model_registry import instruction_record_payload

_INSTRUCTION_RECORD = instruction_record_payload()
INSTRUCTION_PROPAGATION_SCHEMA_VERSION = _INSTRUCTION_RECORD["schema_version"]
INSTRUCTION_PROPAGATION_CONTRACT = _INSTRUCTION_RECORD["contract"]
INSTRUCTION_FORMATTER = _INSTRUCTION_RECORD["formatter"]
INSTRUCTION_PLACEMENT = _INSTRUCTION_RECORD["placement"]
INSTRUCTION_MODES = frozenset(_INSTRUCTION_RECORD["modes"])


class InstructionPropagationError(ValueError):
    pass


def normalize_instruction(value: Any, *, required: bool = True) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = " ".join(value.split())
    else:
        raise InstructionPropagationError("Instruction must be text.")
    if required and not text:
        raise InstructionPropagationError(
            "Instruction-conditioned operation requires a non-empty instruction."
        )
    if len(text) > 4000:
        raise InstructionPropagationError(
            "Instruction exceeds 4000 normalized characters."
        )
    return text


def normalize_instruction_mode(value: Any) -> str:
    mode = str(value or "identity_only").strip().lower()
    if mode not in INSTRUCTION_MODES:
        raise InstructionPropagationError(
            "Instruction mode must be identity_only or per_record."
        )
    return mode


def format_instruction_prompt(value: Any) -> str:
    instruction = normalize_instruction(value, required=True)
    return f"<|im_start|>user\n{instruction}<|im_end|>\n"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def token_ids_fingerprint(value: Any) -> str:
    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()
    elif hasattr(value, "tolist"):
        value = value.tolist()
    return _fingerprint_json(value)


def instruction_identity(
    value: Any,
    *,
    token_ids: Any | None = None,
) -> dict[str, Any]:
    instruction = normalize_instruction(value, required=True)
    formatted = format_instruction_prompt(instruction)
    identity = {
        "instruction_sha256": sha256_text(instruction),
        "formatted_instruction_sha256": sha256_text(formatted),
    }
    if token_ids is not None:
        if hasattr(token_ids, "shape"):
            token_count = int(token_ids.shape[-1])
        else:
            flattened = token_ids
            while (
                isinstance(flattened, Sequence)
                and flattened
                and isinstance(flattened[0], Sequence)
            ):
                flattened = flattened[0]
            token_count = len(flattened)
        identity.update(
            {
                "instruction_token_count": token_count,
                "instruction_token_ids_sha256": token_ids_fingerprint(
                    token_ids
                ),
            }
        )
    return identity


def build_instruction_propagation_contract(
    *,
    mode: Any,
    samples: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    selected_mode = normalize_instruction_mode(mode)
    records: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        source_index = sample.get("source_index", index)
        if isinstance(source_index, bool) or not isinstance(source_index, int):
            raise InstructionPropagationError(
                "Instruction sample source_index must be an integer."
            )
        instruction = normalize_instruction(
            sample.get("instruction"),
            required=selected_mode == "per_record",
        )
        if selected_mode == "identity_only":
            continue
        identity = instruction_identity(
            instruction,
            token_ids=sample.get("instruction_ids"),
        )
        records.append(
            {
                "source_index": source_index,
                **identity,
            }
        )
    records.sort(key=lambda item: item["source_index"])
    value = {
        "schema_version": INSTRUCTION_PROPAGATION_SCHEMA_VERSION,
        "contract": INSTRUCTION_PROPAGATION_CONTRACT,
        "mode": selected_mode,
        "instruction_field": "instruction",
        "formatter": INSTRUCTION_FORMATTER,
        "placement": INSTRUCTION_PLACEMENT,
        "record_count": len(records),
        "instruction_required_at_inference": selected_mode == "per_record",
        "records": records,
    }
    value["records_fingerprint"] = _fingerprint_json(records)
    value["propagation_fingerprint"] = _fingerprint_json(value)
    return value


def validate_instruction_propagation_contract(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InstructionPropagationError(
            "Instruction propagation contract must be an object."
        )
    allowed_top_level = {
        "schema_version",
        "contract",
        "mode",
        "instruction_field",
        "formatter",
        "placement",
        "record_count",
        "instruction_required_at_inference",
        "records",
        "records_fingerprint",
        "propagation_fingerprint",
    }
    if set(value) != allowed_top_level:
        raise InstructionPropagationError(
            "Instruction propagation contract has unexpected fields."
        )
    mode = normalize_instruction_mode(value.get("mode"))
    if value.get("schema_version") != INSTRUCTION_PROPAGATION_SCHEMA_VERSION:
        raise InstructionPropagationError(
            "Instruction propagation schema is unsupported."
        )
    if value.get("contract") != INSTRUCTION_PROPAGATION_CONTRACT:
        raise InstructionPropagationError(
            "Instruction propagation contract is unsupported."
        )
    if value.get("instruction_field") != "instruction":
        raise InstructionPropagationError(
            "Instruction propagation field is unsupported."
        )
    if value.get("formatter") != INSTRUCTION_FORMATTER:
        raise InstructionPropagationError(
            "Instruction propagation formatter is unsupported."
        )
    if value.get("placement") != INSTRUCTION_PLACEMENT:
        raise InstructionPropagationError(
            "Instruction propagation placement is unsupported."
        )
    records = value.get("records")
    if not isinstance(records, list):
        raise InstructionPropagationError(
            "Instruction propagation records must be an array."
        )
    normalized_records: list[dict[str, Any]] = []
    seen: set[int] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise InstructionPropagationError(
                "Instruction propagation record must be an object."
            )
        allowed_record_fields = {
            "source_index",
            "instruction_sha256",
            "formatted_instruction_sha256",
            "instruction_token_count",
            "instruction_token_ids_sha256",
        }
        required_record_fields = {
            "source_index",
            "instruction_sha256",
            "formatted_instruction_sha256",
        }
        if (
            not required_record_fields.issubset(record)
            or not set(record).issubset(allowed_record_fields)
        ):
            raise InstructionPropagationError(
                "Instruction propagation record has unexpected fields."
            )
        source_index = record.get("source_index")
        if (
            isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index < 0
            or source_index in seen
        ):
            raise InstructionPropagationError(
                "Instruction propagation source indices must be unique non-negative integers."
            )
        seen.add(source_index)
        normalized = {"source_index": source_index}
        for key in (
            "instruction_sha256",
            "formatted_instruction_sha256",
        ):
            digest = record.get(key)
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise InstructionPropagationError(
                    f"Instruction propagation {key} is invalid."
                )
            normalized[key] = digest
        token_count = record.get("instruction_token_count")
        token_digest = record.get("instruction_token_ids_sha256")
        if token_count is not None or token_digest is not None:
            if (
                isinstance(token_count, bool)
                or not isinstance(token_count, int)
                or token_count <= 0
            ):
                raise InstructionPropagationError(
                    "Instruction token count must be positive."
                )
            if (
                not isinstance(token_digest, str)
                or len(token_digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in token_digest
                )
            ):
                raise InstructionPropagationError(
                    "Instruction token fingerprint is invalid."
                )
            normalized.update(
                {
                    "instruction_token_count": token_count,
                    "instruction_token_ids_sha256": token_digest,
                }
            )
        normalized_records.append(normalized)
    normalized_records.sort(key=lambda item: item["source_index"])
    if mode == "identity_only" and normalized_records:
        raise InstructionPropagationError(
            "Identity-only propagation cannot list instruction records."
        )
    if mode == "per_record" and not normalized_records:
        raise InstructionPropagationError(
            "Per-record propagation requires instruction records."
        )
    expected = {
        "schema_version": INSTRUCTION_PROPAGATION_SCHEMA_VERSION,
        "contract": INSTRUCTION_PROPAGATION_CONTRACT,
        "mode": mode,
        "instruction_field": "instruction",
        "formatter": INSTRUCTION_FORMATTER,
        "placement": INSTRUCTION_PLACEMENT,
        "record_count": len(normalized_records),
        "instruction_required_at_inference": mode == "per_record",
        "records": normalized_records,
    }
    expected["records_fingerprint"] = _fingerprint_json(normalized_records)
    expected["propagation_fingerprint"] = _fingerprint_json(expected)
    if value.get("record_count") != expected["record_count"]:
        raise InstructionPropagationError(
            "Instruction propagation record count does not match."
        )
    if (
        value.get("instruction_required_at_inference")
        != expected["instruction_required_at_inference"]
    ):
        raise InstructionPropagationError(
            "Instruction inference requirement does not match the mode."
        )
    if value.get("records_fingerprint") != expected["records_fingerprint"]:
        raise InstructionPropagationError(
            "Instruction record fingerprint does not match."
        )
    if (
        value.get("propagation_fingerprint")
        != expected["propagation_fingerprint"]
    ):
        raise InstructionPropagationError(
            "Instruction propagation fingerprint does not match."
        )
    return expected
