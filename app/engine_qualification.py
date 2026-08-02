from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Final

from model_registry import (
    ModelRegistryError,
    component_record_payload,
    engine_record_fingerprint,
    engine_record_payload,
)


SCHEMA_VERSION: Final = 1
STAGE_IDS: Final = (
    "artifact_and_expected_set_validity",
    "installation_and_loader_compatibility",
    "offline_restart",
    "relocation_and_cache_migration",
    "packaged_or_frozen_runtime",
    "source_span_and_spoken_content_fidelity",
    "voice_identity_and_drift",
    "acoustic_duration_seam_and_artifact_quality",
    "delivery_and_instruction_response",
    "repeated_seed_consistency",
    "long_form_behavior",
    "cancellation_and_interrupted_work_recovery",
    "reset_and_uninstall_recovery",
    "telemetry_and_network_review",
    "unsafe_deserialization_review",
    "provenance_validity",
    "blinded_human_listening",
    "final_truthful_capability_disposition",
)
STAGE_STATES: Final = ("passed", "failed", "blocked", "not_applicable")
TERMINAL_STATES: Final = (
    "complete",
    "failed",
    "cancelled",
    "timed_out",
    "excluded",
    "invalidated",
)
ASSERTION_OUTCOMES: Final = ("pass", "fail", "block")
QUALIFICATION_STATES: Final = ("draft", "evidence_bound", "blocked", "failed", "complete")
FINAL_DISPOSITIONS: Final = (
    "production_accepted",
    "restricted",
    "evaluation_only",
    "supporting_component_accepted",
    "blocked_by_license",
    "blocked_by_acquisition",
    "blocked_by_platform",
    "failed_qualification",
    "deferred",
    "rejected",
)
SUBJECT_KINDS: Final = (
    "production_tts",
    "evaluation_tts",
    "supporting_transcription_alignment",
    "supporting_enhancement_codec",
    "supporting_speaker_evidence",
    "optional_evaluator",
)
EVIDENCE_ORIGINS: Final = ("authoritative_existing", "synthetic_validation")
_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_DECIMAL_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$|^-[1-9][0-9]*(?:\.[0-9]*[1-9])?$")

_PROFILE_PREIMAGES: Final = {
    "qwen3_inherited_structural_v1": '{"human_rule":"H1","id":"qwen3_inherited_structural_v1","metrics":[{"confidence":"none","direction":"minimum","formula_version":"exact_span_v1","id":"source_span_coverage","limitations":["imported_structural_only","no_new_production_authority"],"minimum_sample_count":1,"threshold_decimal":"1","unit":"ratio"}],"production_effect":"never_promotes"}',
    "controlled_delivery_pending_v1": '{"human_rule":"H1","id":"controlled_delivery_pending_v1","metrics":[{"confidence":"none","direction":"minimum","formula_version":"trace_presence_v1","id":"delivery_trace_completeness","limitations":["experimental_unaccepted","listening_missing"],"minimum_sample_count":1,"threshold_decimal":"1","unit":"ratio"}],"production_effect":"never_promotes"}',
    "known_transcript_eval_v1": '{"human_rule":"H2","id":"known_transcript_eval_v1","metrics":[{"confidence":"insufficient_sample_size","direction":"maximum","formula_version":"wer_standard_v1","id":"word_error_rate","limitations":["single_fixture","single_speaker","english_only","macos_only","evaluation_only"],"minimum_sample_count":1,"threshold_decimal":"0","unit":"ratio"}],"production_effect":"never_promotes"}',
}
PROFILE_HASHES: Final = {
    name: hashlib.sha256(preimage.encode("utf-8")).hexdigest()
    for name, preimage in _PROFILE_PREIMAGES.items()
}
_EXPECTED_PROFILE_HASHES: Final = {
    "qwen3_inherited_structural_v1": "1c01426d2c160fb74651acaf9bf938a4fe5f3318d004ce5fc80cd60de2527a79",
    "controlled_delivery_pending_v1": "a76d3a705febf9cf1c860334951d5eee9c4068c1a08b83c77819f42c827108ae",
    "known_transcript_eval_v1": "c36632f0ccf738ba019b1c0614f6fba9906e5ab4b88e3831f3a2808cd57aa6d6",
}
if PROFILE_HASHES != _EXPECTED_PROFILE_HASHES:
    raise RuntimeError("Qualification metric profile preimages do not match the approved hashes.")

_QUALIFICATION_BINDINGS: Final = {
    "qwen3_base": {
        "record_kind": "engine",
        "subject_kind": "production_tts",
        "profile_id": "qwen3_inherited_structural_v1",
        "projection_paths": ("/engine_id", "/voice_methods", "/readiness"),
        "prior_authority": "current_production_baseline",
        "qualification_state": "evidence_bound",
        "expected_count": 18,
        "conditional_rules": ("P1", "C1", "V1", "A1", "N9", "S1", "L1", "H1"),
    },
    "qwen3_instruction_controlled": {
        "record_kind": "engine",
        "subject_kind": "evaluation_tts",
        "profile_id": "controlled_delivery_pending_v1",
        "projection_paths": ("/engine_id", "/voice_methods", "/instruction/supported"),
        "prior_authority": "experimental_unaccepted",
        "qualification_state": "blocked",
        "expected_count": 18,
        "conditional_rules": ("N5", "C1", "V1", "A1", "D1", "S1", "L1", "H1"),
    },
    "mlx_whisper_base": {
        "record_kind": "component",
        "subject_kind": "supporting_transcription_alignment",
        "profile_id": "known_transcript_eval_v1",
        "projection_paths": ("/component_id", "/consumers", "/installation_class"),
        "prior_authority": "evaluation_only_supporting",
        "qualification_state": "blocked",
        "expected_count": 13,
        "conditional_rules": ("N5", "C1", "N7", "A1", "N9", "N10", "N11", "H2"),
    },
}

_IMPORTED_EVIDENCE: Final = {
    "instruction-trace.json": "d1d82857a032c75753e0ff89fe97f72082cec2bcb6fe35dc8598097f1585fec0",
    "known-transcript-result.json": "587e62cd4bd30e094ae2a6a58ce031a28556f326395e16ad00c563ea09ebe356",
    "b20-t01-final-f3-manual-qa.md": "4f24e1ff97746866e43be82dd3b3b35ea9f1d7339d0a8e4cffa2ae93aec197b6",
    "b20-t01-final-f4-scope-fidelity.md": "f0f371112dd38408cadd2b986456b0c777c2bb22d6bfce46bef9954b46f44f7b",
}
_IMPORTED_SOURCE_PATHS: Final = {
    "instruction-trace.json": "/Users/tristan/pinokio/api/alexandria-audiobook.git/.omo/evidence/b17-t06-instruction-trace/result.json",
    "known-transcript-result.json": "/Users/tristan/pinokio/api/alexandria-audiobook.git/.omo/evidence/b17-t04-transcription-evaluator/known-transcript-result.json",
    "b20-t01-final-f3-manual-qa.md": "/Users/tristan/pinokio/api/alexandria-audiobook.git/.omo/evidence/b20-t01/final-f3-manual-qa.md",
    "b20-t01-final-f4-scope-fidelity.md": "/Users/tristan/pinokio/api/alexandria-audiobook.git/.omo/evidence/b20-t01/final-f4-scope-fidelity.md",
}


class QualificationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise QualificationError(code, message)


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            _fail("non_canonical_text", "All qualification text must be NFC normalized.")
        return value
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        _fail("float_forbidden", "Floating-point JSON values are forbidden.")
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            _fail("invalid_key", "JSON object keys must be text.")
        return {_normalize(key): _normalize(item) for key, item in value.items()}
    _fail("unsupported_value", "Value cannot be represented in canonical qualification JSON.")


def canonical_bytes(value: Any) -> bytes:
    normalized = _normalize(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _reject_duplicate_pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            _fail("duplicate_key", f"Duplicate JSON key: {key!r}.")
        result[key] = value
    return result


def strict_json_loads(raw: bytes | str) -> Any:
    if isinstance(raw, bytes):
        if raw.startswith(b"\xef\xbb\xbf"):
            _fail("bom_forbidden", "Qualification JSON must not contain a BOM.")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise QualificationError("invalid_utf8", "Qualification JSON must be UTF-8.") from exc
    else:
        text = raw

    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=lambda token: _fail("float_forbidden", f"Float token forbidden: {token}."),
            parse_constant=lambda token: _fail("non_finite", f"Non-finite token forbidden: {token}."),
        )
    except json.JSONDecodeError as exc:
        raise QualificationError("invalid_json", "Qualification JSON is invalid or has trailing data.") from exc
    _normalize(value)
    return value


def _closed(value: Any, fields: set[str], code: str = "unknown_field") -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code, "Object fields do not match the closed qualification schema.")
    return value


def _identifier(value: Any, name: str = "identifier") -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _fail("invalid_identifier", f"Invalid {name}.")
    return value


def _digest(value: Any, name: str = "hash") -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail("invalid_hash", f"Invalid {name}.")
    return value


def _git_commit(value: Any) -> str:
    if not isinstance(value, str) or _GIT_COMMIT_RE.fullmatch(value) is None:
        _fail("invalid_commit", "Invalid Git commit SHA.")
    return value


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _fail("unsafe_path", "Path must be non-empty text.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        _fail("unsafe_path", "Path traversal is forbidden.")
    return value


def _record(subject_id: str) -> tuple[str, dict[str, Any]]:
    subject = _QUALIFICATION_BINDINGS.get(subject_id)
    if subject is None:
        _fail("unregistered_subject", "Qualification subject is not registered by B20-T01.")
    try:
        if subject["record_kind"] == "engine":
            return "engine", engine_record_payload(subject_id)
        return "component", component_record_payload(subject_id)
    except ModelRegistryError as exc:
        raise QualificationError("unregistered_subject", "Qualification subject is not registered by B20-T01.") from exc


def _pointer(record: dict[str, Any], pointer: str) -> Any:
    if not pointer.startswith("/"):
        _fail("invalid_projection", "Record projection must be a JSON pointer.")
    current: Any = record
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            _fail("record_projection_overflow", "Record projection leaves authoritative scope.")
        current = current[token]
    return current


def identity(kind: str, value: str) -> dict[str, Any]:
    _identifier(kind, "identity kind")
    if not isinstance(value, str) or not value:
        _fail("invalid_identity", "Identity value must be non-empty text.")
    return {"kind": kind, "value": value, "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}


@dataclass(frozen=True, slots=True)
class ImportedEvidenceMaterial:
    fixture_root: str
    accepted_parent: str
    source_hashes: tuple[tuple[str, str], ...]
    bundle_hash: str
    supported_stage_ids: tuple[str, ...]


def _verified_import_hash(value: ImportedEvidenceMaterial | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, ImportedEvidenceMaterial):
        _fail("unverified_imported_evidence", "Imported evidence must include its locked canonical source material.")
    observed = verify_imported_evidence(value.fixture_root)
    if observed != value:
        _fail("imported_evidence_drift", "Imported evidence material differs from its locked canonical sources.")
    if value.accepted_parent != "8f2e98bde6376caa7b3690c0f50f78ee592a1197":
        _fail("imported_evidence_drift", "Imported evidence is bound to the wrong accepted parent.")
    rebuilt = canonical_hash({"accepted_parent": value.accepted_parent, "source_hashes": [list(item) for item in value.source_hashes], "supported_stage_ids": list(value.supported_stage_ids)})
    if value.bundle_hash != rebuilt:
        _fail("imported_evidence_drift", "Imported evidence bundle identity is invalid.")
    return value.bundle_hash


def _exact_expected_items(subject_id: str) -> list[dict[str, Any]]:
    subject = _QUALIFICATION_BINDINGS[subject_id]
    conditional_stages = STAGE_IDS[4:11] + (STAGE_IDS[16],)
    rules = dict(zip(conditional_stages, subject["conditional_rules"], strict=True))
    selected = [stage for stage in STAGE_IDS if rules.get(stage, "U1") not in {"N5", "N6", "N7", "N8", "N9", "N10", "N11", "H2"}]
    items: list[dict[str, Any]] = []
    for stage in selected:
        repetitions = 2 if stage == STAGE_IDS[0] else 1
        for index in range(repetitions):
            suffix = "artifact_identity" if index == 0 else "expected_set_identity"
            item_id = f"{stage}:{suffix}" if repetitions == 2 else stage
            items.append({
                "item_id": item_id,
                "stage_id": stage,
                "fixture_id": f"fixture:{subject_id}:{item_id}",
                "input_identity": identity("fixture_input", f"{subject_id}:{item_id}:input"),
                "source_span": {"source_id": "fixture-source", "start": 0, "end": 1, "unit": "item"},
                "expected_artifact": identity("fixture_artifact", f"{subject_id}:{item_id}:expected"),
            })
    if len(items) != subject["expected_count"]:
        raise RuntimeError("Authoritative expected-set definition has the wrong denominator.")
    return items


_MANIFEST_FIELDS: Final = {
    "schema_version", "evidence_origin", "qualification_id", "subject_id", "subject_kind",
    "record_fingerprint", "source_identity", "build_identity", "revision_identity",
    "license_disposition", "acquisition_disposition", "artifact_manifest_identity",
    "runtime_identity", "loader_identity", "platform_target", "packaging_target",
    "expected_set_identity", "record_projections", "applicable_stages", "metric_profile_id",
    "metric_profile_hash", "metrics", "human_review_requirements", "exclusions",
    "recovery_requirements", "prior_authority", "qualification_state", "final_disposition",
    "imported_evidence_hash",
}


def build_manifest(subject_id: str, *, evidence_origin: str = "authoritative_existing", verified_import: ImportedEvidenceMaterial | None = None) -> dict[str, Any]:
    record_kind, record = _record(subject_id)
    subject = _QUALIFICATION_BINDINGS[subject_id]
    projections = [
        {
            "record_kind": record_kind,
            "record_id": subject_id,
            "json_pointer": pointer,
            "expected_value_sha256": canonical_hash(_pointer(record, pointer)),
        }
        for pointer in subject["projection_paths"]
    ]
    profile_id = subject["profile_id"]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "evidence_origin": evidence_origin,
        "qualification_id": f"b20-t07:{subject_id}",
        "subject_id": subject_id,
        "subject_kind": subject["subject_kind"],
        "record_fingerprint": engine_record_fingerprint(record),
        "source_identity": identity("record", subject_id),
        "build_identity": identity("record_fingerprint", engine_record_fingerprint(record)),
        "revision_identity": identity("revision", str(record.get("engine_revision", record.get("revision", "registered")))),
        "license_disposition": "record_declared",
        "acquisition_disposition": "existing_only",
        "artifact_manifest_identity": identity("artifact_manifest", f"{subject_id}:" + (_verified_import_hash(verified_import) or "unverified")),
        "runtime_identity": identity("runtime", str(record.get("runtime", record.get("provider", "registered")))),
        "loader_identity": identity("loader", str(record.get("loader", "registered"))),
        "platform_target": identity("platform", "darwin-arm64"),
        "packaging_target": identity("packaging", "nonpackaged-evaluation" if subject_id != "qwen3_base" else "application-runtime"),
        "expected_set_identity": identity("expected_set", f"b20-t07:{subject_id}:v1:{canonical_hash(_exact_expected_items(subject_id))}"),
        "record_projections": projections,
        "applicable_stages": list(STAGE_IDS),
        "metric_profile_id": profile_id,
        "metric_profile_hash": PROFILE_HASHES[profile_id],
        "metrics": json.loads(_PROFILE_PREIMAGES[profile_id])["metrics"],
        "human_review_requirements": [{"id": subject["conditional_rules"][-1], "evidence_hash": canonical_hash({"subject_id": subject_id, "review": "missing"})}],
        "exclusions": [],
        "recovery_requirements": [{"id": "restart_retry", "reason_hash": canonical_hash({"reason": "retain_interrupted_row"}), "recovery_code": "distinct_retry_item"}],
        "prior_authority": subject["prior_authority"],
        "qualification_state": subject["qualification_state"],
        "final_disposition": None,
        "imported_evidence_hash": _verified_import_hash(verified_import),
    }
    validate_manifest(manifest)
    return manifest


def _validate_identity(value: Any) -> None:
    item = _closed(value, {"kind", "value", "sha256"})
    _identifier(item["kind"], "identity kind")
    if not isinstance(item["value"], str) or not item["value"]:
        _fail("invalid_identity", "Identity value must be non-empty text.")
    if hashlib.sha256(item["value"].encode("utf-8")).hexdigest() != item["sha256"]:
        _fail("identity_hash_mismatch", "Identity hash does not match its value.")


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _closed(value, _MANIFEST_FIELDS)
    if manifest["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported_schema", "Qualification manifest schema is unsupported.")
    if manifest["evidence_origin"] not in EVIDENCE_ORIGINS:
        _fail("invalid_evidence_origin", "Evidence origin is not recognized.")
    _identifier(manifest["qualification_id"], "qualification ID")
    _identifier(manifest["subject_id"], "subject ID")
    if manifest["subject_kind"] not in SUBJECT_KINDS:
        _fail("invalid_subject_kind", "Qualification subject kind is not recognized.")
    record_kind, record = _record(manifest["subject_id"])
    subject = _QUALIFICATION_BINDINGS[manifest["subject_id"]]
    if manifest["qualification_id"] != f"b20-t07:{manifest['subject_id']}":
        _fail("qualification_id_mismatch", "Qualification ID differs from its registered subject binding.")
    if manifest["subject_kind"] != subject["subject_kind"]:
        _fail("subject_kind_mismatch", "Subject kind differs from authoritative binding.")
    record_fingerprint = engine_record_fingerprint(record)
    if manifest["record_fingerprint"] != record_fingerprint:
        _fail("stale_record", "Qualification record fingerprint is stale.")
    for field in ("source_identity", "build_identity", "revision_identity", "artifact_manifest_identity", "runtime_identity", "loader_identity", "platform_target", "packaging_target", "expected_set_identity"):
        _validate_identity(manifest[field])
    expected_identities = {
        "source_identity": identity("record", manifest["subject_id"]),
        "build_identity": identity("record_fingerprint", record_fingerprint),
        "revision_identity": identity("revision", str(record.get("engine_revision", record.get("revision", "registered")))),
        "runtime_identity": identity("runtime", str(record.get("runtime", record.get("provider", "registered")))),
        "loader_identity": identity("loader", str(record.get("loader", "registered"))),
        "packaging_target": identity("packaging", "nonpackaged-evaluation" if manifest["subject_id"] != "qwen3_base" else "application-runtime"),
        "expected_set_identity": identity("expected_set", f"b20-t07:{manifest['subject_id']}:v1:{canonical_hash(_exact_expected_items(manifest['subject_id']))}"),
        "artifact_manifest_identity": identity("artifact_manifest", f"{manifest['subject_id']}:" + (manifest["imported_evidence_hash"] or "unverified")),
    }
    if any(manifest[field] != expected for field, expected in expected_identities.items()):
        _fail("identity_substitution", "Manifest identity differs from its authoritative subject binding.")
    expected_kinds = {"platform_target": "platform"}
    if any(manifest[field]["kind"] != kind for field, kind in expected_kinds.items()):
        _fail("identity_substitution", "Manifest identity kind differs from its required role.")
    if manifest["license_disposition"] not in {"record_declared", "blocked"} or manifest["acquisition_disposition"] not in {"existing_only", "blocked"}:
        _fail("invalid_disposition", "License or acquisition disposition is not recognized.")
    if manifest["imported_evidence_hash"] is not None:
        _digest(manifest["imported_evidence_hash"], "imported evidence hash")
    if manifest["prior_authority"] != subject["prior_authority"]:
        _fail("prior_authority_mismatch", "Prior authority differs from the authoritative subject binding.")
    projections = manifest["record_projections"]
    if not isinstance(projections, list) or not projections:
        _fail("missing_projection", "At least one record projection is required.")
    expected_projection_paths = tuple(subject["projection_paths"])
    if len(projections) != len(expected_projection_paths):
        _fail("record_projection_overflow", "Projection set differs from the approved scope.")
    for projection, pointer in zip(projections, expected_projection_paths, strict=True):
        item = _closed(projection, {"record_kind", "record_id", "json_pointer", "expected_value_sha256"})
        if item["record_kind"] != record_kind or item["record_id"] != manifest["subject_id"] or item["json_pointer"] != pointer:
            _fail("record_projection_overflow", "Projection differs from approved authoritative scope.")
        if item["expected_value_sha256"] != canonical_hash(_pointer(record, pointer)):
            _fail("record_projection_mismatch", "Projection does not match the authoritative record.")
    if manifest["applicable_stages"] != list(STAGE_IDS):
        _fail("stage_catalog_mismatch", "Manifest must declare the exact ordered stage catalog.")
    if manifest["metric_profile_id"] != subject["profile_id"] or manifest["metric_profile_hash"] != PROFILE_HASHES[subject["profile_id"]]:
        _fail("metric_profile_subject_mismatch", "Metric profile differs from the authoritative subject binding.")
    if manifest["metrics"] != json.loads(_PROFILE_PREIMAGES[subject["profile_id"]])["metrics"]:
        _fail("metric_profile_subject_mismatch", "Metric definitions differ from the locked capability profile.")
    if manifest["qualification_state"] not in QUALIFICATION_STATES:
        _fail("invalid_qualification_state", "Qualification state is not recognized.")
    disposition = manifest["final_disposition"]
    if disposition is not None and disposition not in FINAL_DISPOSITIONS:
        _fail("invalid_disposition", "Final disposition is not recognized.")
    if disposition in {"production_accepted", "restricted", "evaluation_only", "supporting_component_accepted"} and manifest["qualification_state"] != "complete":
        _fail("impossible_disposition", "An accepted disposition requires a complete qualification.")
    for collection in ("metrics", "human_review_requirements", "exclusions", "recovery_requirements"):
        if not isinstance(manifest[collection], list):
            _fail("invalid_collection", f"Manifest {collection} must be an array.")
    for metric in manifest["metrics"]:
        profile_metric = _closed(metric, {"id", "formula_version", "unit", "threshold_decimal", "direction", "minimum_sample_count", "confidence", "limitations"})
        for field in ("id", "formula_version", "unit", "direction", "confidence"):
            _identifier(profile_metric[field], field)
        if not isinstance(profile_metric["minimum_sample_count"], int) or isinstance(profile_metric["minimum_sample_count"], bool) or profile_metric["minimum_sample_count"] < 0:
            _fail("invalid_metric_count", "Profile sample count must be a non-negative integer.")
        if not isinstance(profile_metric["threshold_decimal"], str) or _DECIMAL_RE.fullmatch(profile_metric["threshold_decimal"]) is None:
            _fail("invalid_decimal", "Profile threshold must be a canonical decimal string.")
        if not isinstance(profile_metric["limitations"], list) or len(profile_metric["limitations"]) != len(set(profile_metric["limitations"])):
            _fail("invalid_limitations", "Profile limitations must be unique ordered IDs.")
    for requirement in manifest["human_review_requirements"]:
        item = _closed(requirement, {"id", "evidence_hash"})
        if item["id"] not in {"H1", "H2"}:
            _fail("unsupported_applicability_combination", "Human-review rule is not recognized.")
        _digest(item["evidence_hash"], "human-review evidence hash")
    expected_human_requirements = [{"id": subject["conditional_rules"][-1], "evidence_hash": canonical_hash({"subject_id": manifest["subject_id"], "review": "missing"})}]
    if manifest["human_review_requirements"] != expected_human_requirements:
        _fail("unsupported_applicability_combination", "Human-review applicability differs from the subject binding.")
    for exclusion in manifest["exclusions"]:
        item = _closed(exclusion, {"id", "reason_hash", "evidence_hash", "applicability_code"})
        _identifier(item["id"], "exclusion ID")
        _identifier(item["applicability_code"], "exclusion applicability code")
        _digest(item["reason_hash"], "exclusion reason hash")
        _digest(item["evidence_hash"], "exclusion evidence hash")
    for requirement in manifest["recovery_requirements"]:
        item = _closed(requirement, {"id", "reason_hash", "recovery_code"})
        _identifier(item["id"], "recovery requirement ID")
        _identifier(item["recovery_code"], "recovery code")
        _digest(item["reason_hash"], "recovery reason hash")
    canonical_bytes(manifest)
    return manifest


_EXPECTED_ITEM_FIELDS: Final = {"item_id", "stage_id", "fixture_id", "input_identity", "source_span", "expected_artifact"}


def validate_expected_set(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    expected = _closed(value, {"schema_version", "qualification_id", "manifest_hash", "items"})
    if expected["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported_schema", "Expected-set schema is unsupported.")
    if expected["qualification_id"] != manifest["qualification_id"] or expected["manifest_hash"] != canonical_hash(manifest):
        _fail("expected_set_manifest_mismatch", "Expected set is not bound to this manifest.")
    items = expected["items"]
    if not isinstance(items, list) or not items:
        _fail("incomplete_expected_set", "Expected set must contain declared work.")
    identifiers: set[str] = set()
    for raw in items:
        item = _closed(raw, _EXPECTED_ITEM_FIELDS)
        item_id = _identifier(item["item_id"], "expected item ID")
        if item_id in identifiers:
            _fail("duplicate_expected_item", "Expected item IDs must be unique.")
        identifiers.add(item_id)
        if item["stage_id"] not in STAGE_IDS:
            _fail("unknown_stage", "Expected item names an unknown stage.")
        _identifier(item["fixture_id"], "fixture ID")
        _validate_identity(item["input_identity"])
        _validate_identity(item["expected_artifact"])
        span = _closed(item["source_span"], {"source_id", "start", "end", "unit"})
        _identifier(span["source_id"], "source ID")
        if any(isinstance(span[key], bool) or not isinstance(span[key], int) or span[key] < 0 for key in ("start", "end")) or span["end"] < span["start"]:
            _fail("invalid_source_span", "Source span is invalid.")
        _identifier(span["unit"], "source-span unit")
    if items != _exact_expected_items(manifest["subject_id"]):
        _fail("expected_set_definition_mismatch", "Expected set differs from the exact authoritative subject definition.")
    return expected


def make_expected_set(manifest: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    items = _exact_expected_items(manifest["subject_id"])
    expected = {"schema_version": SCHEMA_VERSION, "qualification_id": manifest["qualification_id"], "manifest_hash": canonical_hash(manifest), "items": items}
    validate_expected_set(expected, manifest)
    return expected


_ROW_FIELDS: Final = {
    "item_id", "subject_id", "record_fingerprint", "fixture_id", "input_identity", "source_span",
    "expected_artifact", "terminal_state", "assertion_outcome", "output_hash", "transcript_result",
    "error", "exclusion", "timing", "recovery_result", "metrics", "limitations", "human_review_link",
}


def terminal_row(manifest: dict[str, Any], item: dict[str, Any], *, terminal_state: str = "complete", assertion_outcome: str = "pass", limitations: list[str] | None = None, evidence_hash: str | None = None) -> dict[str, Any]:
    return {
        "item_id": item["item_id"], "subject_id": manifest["subject_id"], "record_fingerprint": manifest["record_fingerprint"],
        "fixture_id": item["fixture_id"], "input_identity": deepcopy(item["input_identity"]), "source_span": deepcopy(item["source_span"]),
        "expected_artifact": deepcopy(item["expected_artifact"]), "terminal_state": terminal_state, "assertion_outcome": assertion_outcome,
        "output_hash": canonical_hash({"manifest_hash": canonical_hash(manifest), "expected_item": item, "outcome": assertion_outcome, "evidence_hash": evidence_hash}) if terminal_state == "complete" else None,
        "transcript_result": None, "error": {"id": "evaluation_failure", "state": "terminal", "hash": canonical_hash({"item": item["item_id"], "error": terminal_state})} if terminal_state in {"failed", "invalidated"} else None,
        "exclusion": {"id": "allowlisted_fixture_exclusion", "state": "excluded", "hash": canonical_hash({"item": item["item_id"], "exclusion": True})} if terminal_state == "excluded" else None,
        "timing": {"started_ns": 0, "finished_ns": 1, "duration_ns": 1}, "recovery_result": None, "metrics": [],
        "limitations": [] if limitations is None else limitations, "human_review_link": None,
    }


def validate_ledger(value: Any, manifest: dict[str, Any], expected_set: dict[str, Any]) -> dict[str, Any]:
    ledger = _closed(value, {"schema_version", "qualification_id", "manifest_hash", "expected_set_hash", "parent_ledger_hash", "rows"})
    if ledger["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported_schema", "Terminal-ledger schema is unsupported.")
    if ledger["qualification_id"] != manifest["qualification_id"] or ledger["manifest_hash"] != canonical_hash(manifest) or ledger["expected_set_hash"] != canonical_hash(expected_set):
        _fail("ledger_lineage_mismatch", "Terminal ledger lineage does not match its inputs.")
    if ledger["parent_ledger_hash"] is not None:
        _digest(ledger["parent_ledger_hash"], "parent ledger hash")
    expected_by_id = {item["item_id"]: item for item in expected_set["items"]}
    rows = ledger["rows"]
    if not isinstance(rows, list):
        _fail("invalid_rows", "Terminal rows must be an array.")
    row_ids = [row.get("item_id") if isinstance(row, dict) else None for row in rows]
    if len(row_ids) != len(set(row_ids)):
        _fail("duplicate_terminal_row", "Every expected item receives exactly one terminal row.")
    if set(row_ids) != set(expected_by_id):
        _fail("missing_terminal_row" if set(expected_by_id) - set(row_ids) else "unknown_terminal_row", "Terminal ledger must exactly cover the declared expected set.")
    for raw in rows:
        row = _closed(raw, _ROW_FIELDS)
        item = expected_by_id[row["item_id"]]
        if row["subject_id"] != manifest["subject_id"] or row["record_fingerprint"] != manifest["record_fingerprint"]:
            _fail("cross_subject_reuse", "Terminal row subject identity differs from its manifest.")
        for field in ("fixture_id", "input_identity", "source_span", "expected_artifact"):
            if row[field] != item[field]:
                _fail("expected_item_mismatch", "Terminal row differs from the declared expected item.")
        if row["terminal_state"] not in TERMINAL_STATES or row["assertion_outcome"] not in ASSERTION_OUTCOMES:
            _fail("unknown_terminal_state", "Terminal row state is not recognized.")
        if row["terminal_state"] == "complete" and row["output_hash"] is None:
            _fail("missing_output_hash", "Complete output requires an immutable hash.")
        if row["output_hash"] is not None:
            _digest(row["output_hash"], "terminal output hash")
        if row["terminal_state"] == "excluded" and row["exclusion"] is None:
            _fail("exclusion_without_reason", "Excluded rows require reason and evidence.")
        if row["terminal_state"] in {"failed", "invalidated"} and row["error"] is None:
            _fail("failure_without_error", "Failed and invalidated rows require immutable error evidence.")
        for field in ("transcript_result", "error", "exclusion", "recovery_result", "human_review_link"):
            if row[field] is not None:
                evidence = _closed(row[field], {"id", "state", "hash"})
                _identifier(evidence["id"], f"{field} ID")
                _identifier(evidence["state"], f"{field} state")
                _digest(evidence["hash"], f"{field} hash")
        timing = _closed(row["timing"], {"started_ns", "finished_ns", "duration_ns"})
        if any(isinstance(timing[key], bool) or not isinstance(timing[key], int) or timing[key] < 0 for key in timing) or timing["duration_ns"] != timing["finished_ns"] - timing["started_ns"]:
            _fail("invalid_timing", "Terminal timing is inconsistent.")
        if not isinstance(row["limitations"], list) or len(row["limitations"]) != len(set(row["limitations"])):
            _fail("invalid_limitations", "Limitations must be unique ordered IDs.")
        for limitation in row["limitations"]:
            _identifier(limitation, "limitation ID")
        if not isinstance(row["metrics"], list):
            _fail("invalid_metric", "Terminal metrics must be an array.")
        profile_metrics = {metric["id"]: metric for metric in manifest["metrics"]}
        for raw_metric in row["metrics"]:
            metric = validate_metric(raw_metric)
            definition = profile_metrics.get(metric["id"])
            if definition is None or metric["formula_version"] != definition["formula_version"] or metric["unit"] != definition["unit"]:
                _fail("metric_profile_subject_mismatch", "Observed metric differs from the locked capability profile.")
            if any(limitation not in metric["limitation_codes"] for limitation in definition["limitations"]):
                _fail("invalid_limitations", "Observed metric omits a locked capability limitation.")
            if metric["sample_count"] < 30 and (metric["confidence_interval_decimal_pair"] is not None or "insufficient_sample_size" not in metric["limitation_codes"]):
                _fail("insufficient_sample_disclosure", "Undersized observations require a null interval and explicit limitation.")
    return ledger


def make_ledger(manifest: dict[str, Any], expected_set: dict[str, Any], rows: list[dict[str, Any]] | None = None, parent_ledger_hash: str | None = None) -> dict[str, Any]:
    validate_expected_set(expected_set, manifest)
    terminal_rows = rows if rows is not None else [terminal_row(manifest, item) for item in expected_set["items"]]
    ledger = {"schema_version": SCHEMA_VERSION, "qualification_id": manifest["qualification_id"], "manifest_hash": canonical_hash(manifest), "expected_set_hash": canonical_hash(expected_set), "parent_ledger_hash": parent_ledger_hash, "rows": terminal_rows}
    validate_ledger(ledger, manifest, expected_set)
    return ledger


def ledger_counts(ledger: dict[str, Any]) -> dict[str, int]:
    rows = ledger["rows"]
    counts = {"expected_count": len(rows), "terminal_count": len(rows), "complete_count": 0, "failed_count": 0, "excluded_count": 0, "cancelled_count": 0, "timed_out_count": 0, "invalidated_count": 0, "recovery_count": 0}
    for row in rows:
        counts[f"{row['terminal_state']}_count"] += 1
        if row["recovery_result"] is not None:
            counts["recovery_count"] += 1
    return counts


def validate_metric(value: Any) -> dict[str, Any]:
    metric = _closed(value, {"id", "formula_version", "unit", "value_decimal", "sample_count", "confidence_level_decimal", "confidence_interval_decimal_pair", "limitation_codes"})
    for key in ("id", "formula_version", "unit"):
        _identifier(metric[key], key)
    if isinstance(metric["sample_count"], bool) or not isinstance(metric["sample_count"], int) or metric["sample_count"] < 0:
        _fail("invalid_metric_count", "Metric sample count must be a non-negative integer.")
    for key in ("value_decimal", "confidence_level_decimal"):
        decimal = metric[key]
        if decimal is not None and (not isinstance(decimal, str) or _DECIMAL_RE.fullmatch(decimal) is None):
            _fail("invalid_decimal", "Metric decimal is not canonical.")
    interval = metric["confidence_interval_decimal_pair"]
    if interval is not None and (not isinstance(interval, list) or len(interval) != 2 or any(not isinstance(item, str) or _DECIMAL_RE.fullmatch(item) is None for item in interval)):
        _fail("invalid_confidence_interval", "Metric confidence interval is invalid.")
    if not isinstance(metric["limitation_codes"], list) or len(metric["limitation_codes"]) != len(set(metric["limitation_codes"])):
        _fail("invalid_limitations", "Metric limitations must be unique.")
    return metric


def wilson_interval(successes: int, sample_count: int) -> list[str] | None:
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (successes, sample_count)) or successes < 0 or sample_count < successes:
        _fail("invalid_metric_count", "Wilson inputs must be valid counts.")
    if sample_count < 30:
        return None
    with localcontext() as context:
        context.prec = 28
        z = Decimal("1.959963984540054")
        n = Decimal(sample_count)
        p = Decimal(successes) / n
        denominator = Decimal(1) + z * z / n
        center = (p + z * z / (Decimal(2) * n)) / denominator
        margin = z * ((p * (Decimal(1) - p) + z * z / (Decimal(4) * n)) / n).sqrt() / denominator
        quantum = Decimal("0.000000000001")
        return [_decimal_text((center - margin).quantize(quantum, rounding=ROUND_HALF_EVEN)), _decimal_text((center + margin).quantize(quantum, rounding=ROUND_HALF_EVEN))]


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _stage_rows(stage_id: str, expected_set: dict[str, Any], ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items = [item for item in expected_set["items"] if item["stage_id"] == stage_id]
    item_ids = {item["item_id"] for item in items}
    rows = [row for row in ledger["rows"] if row["item_id"] in item_ids]
    return items, rows


def aggregate_stage(manifest: dict[str, Any], expected_set: dict[str, Any], ledger: dict[str, Any], stage_id: str) -> dict[str, Any]:
    validate_ledger(ledger, manifest, expected_set)
    if stage_id not in STAGE_IDS:
        _fail("unknown_stage", "Stage is not registered.")
    subject = _QUALIFICATION_BINDINGS[manifest["subject_id"]]
    items, rows = _stage_rows(stage_id, expected_set, ledger)
    conditional_stages = STAGE_IDS[4:11] + (STAGE_IDS[16],)
    applicability_rule = dict(zip(conditional_stages, subject["conditional_rules"], strict=True)).get(stage_id, "U1")
    if applicability_rule in {"N5", "N6", "N7", "N8", "N9", "N10", "N11", "H2"}:
        if items or rows:
            _fail("invalid_not_applicable", "N/A stage must have no expected rows.")
        state = "not_applicable"
    else:
        if not items:
            _fail("missing_stage_evidence", "Applicable stage must declare expected evidence.")
        states = {row["terminal_state"] for row in rows}
        outcomes = {row["assertion_outcome"] for row in rows}
        if states & {"failed", "invalidated"} or "fail" in outcomes:
            state = "failed"
        elif states & {"cancelled", "timed_out"} or "block" in outcomes or "excluded" in states:
            state = "blocked"
        else:
            state = "passed"
    stage_input = {"schema_version": SCHEMA_VERSION, "qualification_id": manifest["qualification_id"], "stage_id": stage_id, "manifest_hash": canonical_hash(manifest), "expected_items": items, "terminal_rows": rows}
    counts = ledger_counts({"rows": rows})
    metric_results = [metric for row in rows for metric in row["metrics"]]
    return {"schema_version": SCHEMA_VERSION, "qualification_id": manifest["qualification_id"], "stage_id": stage_id, "applicability_rule": applicability_rule, "expected_set_hash": canonical_hash(expected_set), "stage_input_hash": canonical_hash(stage_input), "state": state, "counts": counts, "metric_results": metric_results, "evidence_hashes": [canonical_hash(stage_input)]}


_STAGE_RESULT_FIELDS: Final = {"schema_version", "qualification_id", "stage_id", "applicability_rule", "expected_set_hash", "stage_input_hash", "state", "counts", "metric_results", "evidence_hashes"}


@dataclass(frozen=True, slots=True)
class TrustedDecisionMaterial:
    decision_bytes: bytes
    signature_path: str
    allowed_signers_path: str
    signer_identity: str
    nonce_ledger_root: str
    evidence_origin: str


def _validate_stage_result(result: Any, manifest: dict[str, Any], expected_set_hash: str) -> dict[str, Any]:
    item = _closed(result, _STAGE_RESULT_FIELDS)
    if item["schema_version"] != SCHEMA_VERSION or item["qualification_id"] != manifest["qualification_id"] or item["expected_set_hash"] != expected_set_hash:
        _fail("stage_result_lineage_mismatch", "Stage result is not bound to the qualification inputs.")
    if item["stage_id"] not in STAGE_IDS or item["state"] not in STAGE_STATES:
        _fail("stage_result_lineage_mismatch", "Stage result state or stage is invalid.")
    allowed_rules = {"U1", "P1", "C1", "V1", "A1", "D1", "S1", "L1", "H1", "H2", "N5", "N6", "N7", "N8", "N9", "N10", "N11"}
    if item["applicability_rule"] not in allowed_rules:
        _fail("unsupported_applicability_combination", "Stage applicability rule is not recognized.")
    _digest(item["stage_input_hash"], "stage-input hash")
    counts = _closed(item["counts"], {"expected_count", "terminal_count", "complete_count", "failed_count", "excluded_count", "cancelled_count", "timed_out_count", "invalidated_count", "recovery_count"})
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()) or counts["expected_count"] != counts["terminal_count"]:
        _fail("invalid_stage_counts", "Stage counts must be complete non-negative integer denominators.")
    if not isinstance(item["metric_results"], list):
        _fail("invalid_metric", "Stage metric results must be an array.")
    for metric in item["metric_results"]:
        validate_metric(metric)
    if not isinstance(item["evidence_hashes"], list) or not item["evidence_hashes"]:
        _fail("missing_stage_evidence", "Stage result must bind ordered evidence hashes.")
    for digest in item["evidence_hashes"]:
        _digest(digest, "stage evidence hash")
    return item


def derive_disposition(manifest: dict[str, Any], stage_results: list[dict[str, Any]], *, trusted_decision: Any = None, decision_attestation_hash: str | None = None) -> str:
    validate_manifest(manifest)
    if not isinstance(stage_results, list) or len(stage_results) not in {17, 18} or [item.get("stage_id") if isinstance(item, dict) else None for item in stage_results] != list(STAGE_IDS[:len(stage_results)]):
        _fail("stage_result_catalog_mismatch", "Disposition requires the ordered stage 1-17 results, optionally followed by stage 18.")
    expected_set_hash = stage_results[0].get("expected_set_hash")
    _digest(expected_set_hash, "expected-set hash")
    for result in stage_results:
        _validate_stage_result(result, manifest, expected_set_hash)
    decision = None
    if trusted_decision is not None:
        if not isinstance(trusted_decision, TrustedDecisionMaterial) or decision_attestation_hash is not None:
            _fail("untrusted_decision", "Approval and rejection require raw detached-signature material.")
        decision = _reverify_trusted_decision(trusted_decision, manifest)
    elif decision_attestation_hash is not None:
        _fail("untrusted_decision", "A detached attestation hash alone is not trusted authority.")
    if decision is not None and decision["decision_kind"] == "reject":
        return "rejected"
    if manifest["license_disposition"] == "blocked":
        return "blocked_by_license"
    if manifest["acquisition_disposition"] == "blocked":
        return "blocked_by_acquisition"
    if manifest["platform_target"]["value"] == "blocked":
        return "blocked_by_platform"
    states = {result["state"] for result in stage_results if result["stage_id"] != STAGE_IDS[-1]}
    if "failed" in states:
        return "failed_qualification"
    if "blocked" in states:
        return "deferred"
    if decision is None or decision["decision_kind"] != "approve":
        return "deferred"
    human_rule = manifest["human_review_requirements"][0]["id"]
    if human_rule == "H1" and (decision["package_hash"] is None or decision["result_hash"] is None):
        _fail("missing_human_review", "Approval requires a package-bound complete human-review result.")
    if manifest["exclusions"]:
        return "restricted"
    kind = manifest["subject_kind"]
    if kind == "production_tts":
        return "production_accepted"
    if kind in {"evaluation_tts", "optional_evaluator"}:
        return "evaluation_only"
    return "supporting_component_accepted"


def _final_disposition_assertion(manifest: dict[str, Any], disposition: str, stage_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "disposition": disposition,
        "precedence_rule_id": {
            "rejected": "rule_1_trusted_rejection", "blocked_by_license": "rule_2_license",
            "blocked_by_acquisition": "rule_3_acquisition", "blocked_by_platform": "rule_4_platform",
            "failed_qualification": "rule_5_required_stage_failure", "deferred": "rule_6_deferred",
            "restricted": "rule_7_restricted", "evaluation_only": "rule_8_evaluation_only",
            "supporting_component_accepted": "rule_9_supporting_component",
            "production_accepted": "rule_10_production",
        }[disposition],
        "prior_authority": manifest["prior_authority"],
        "record_fingerprint": manifest["record_fingerprint"],
        "record_projection_hash": canonical_hash(manifest["record_projections"]),
        "metric_profile_hash": manifest["metric_profile_hash"],
        "stage_result_hashes_1_17": [canonical_hash(result) for result in stage_results[:17]],
    }


def build_receipt(manifest: dict[str, Any], expected_set: dict[str, Any], ledger: dict[str, Any], stage_results: list[dict[str, Any]], disposition: str, *, parent_receipt_hash: str | None = None, review_result_hash: str | None = None, trusted_decision: TrustedDecisionMaterial | None = None, decision_attestation_hash: str | None = None) -> dict[str, Any]:
    validate_manifest(manifest)
    if manifest["evidence_origin"] != "authoritative_existing":
        _fail("synthetic_publication_forbidden", "Synthetic qualification output cannot become a persistent receipt.")
    validate_expected_set(expected_set, manifest)
    validate_ledger(ledger, manifest, expected_set)
    if disposition not in FINAL_DISPOSITIONS:
        _fail("invalid_disposition", "Receipt disposition is not recognized.")
    if decision_attestation_hash is not None:
        _fail("untrusted_decision", "A caller-supplied decision hash is not trusted authority.")
    if not isinstance(stage_results, list) or [result.get("stage_id") if isinstance(result, dict) else None for result in stage_results] != list(STAGE_IDS):
        _fail("stage_result_catalog_mismatch", "Receipt requires all ordered stage results.")
    for stage_id, result in zip(STAGE_IDS, stage_results, strict=True):
        _validate_stage_result(result, manifest, canonical_hash(expected_set))
        if result != aggregate_stage(manifest, expected_set, ledger, stage_id):
            _fail("stage_result_lineage_mismatch", "Stage result does not match its bound ledger input.")
    decision = _reverify_trusted_decision(trusted_decision, manifest) if trusted_decision is not None else None
    derived_disposition = derive_disposition(manifest, stage_results, trusted_decision=trusted_decision)
    if disposition != derived_disposition:
        _fail("untrusted_disposition", "Receipt disposition does not match the verified qualification outcome.")
    accepted = {"production_accepted", "restricted", "evaluation_only", "supporting_component_accepted"}
    if disposition in accepted:
        if decision is None or review_result_hash != decision["result_hash"]:
            _fail("missing_human_review", "Accepted receipt must match its trusted review-result linkage.")
        human_item_ids = {item["item_id"] for item in expected_set["items"] if item["stage_id"] == "blinded_human_listening"}
        human_rows = [row for row in ledger["rows"] if row["item_id"] in human_item_ids]
        if manifest["human_review_requirements"][0]["id"] == "H1" and (len(human_rows) != 1 or human_rows[0]["human_review_link"] is None or human_rows[0]["human_review_link"]["hash"] != decision["result_hash"]):
            _fail("missing_human_review", "Accepted receipt requires exact trusted stage-17 review linkage.")
    final_item_ids = {item["item_id"] for item in expected_set["items"] if item["stage_id"] == STAGE_IDS[-1]}
    final_rows = [row for row in ledger["rows"] if row["item_id"] in final_item_ids]
    if len(final_rows) != 1 or final_rows[0]["assertion_outcome"] != "pass" or final_rows[0]["output_hash"] != canonical_hash(_final_disposition_assertion(manifest, disposition, stage_results)):
        _fail("final_assertion_mismatch", "Stage 18 does not assert the exact derived disposition lineage.")
    decision_attestation_hash = canonical_hash(decision) if decision is not None else None
    for digest in (parent_receipt_hash, review_result_hash, decision_attestation_hash):
        if digest is not None:
            _digest(digest)
    receipt = {"schema_version": SCHEMA_VERSION, "qualification_id": manifest["qualification_id"], "subject_id": manifest["subject_id"], "record_fingerprint": manifest["record_fingerprint"], "manifest_hash": canonical_hash(manifest), "expected_set_hash": canonical_hash(expected_set), "ledger_hash": canonical_hash(ledger), "stage_result_hashes": [canonical_hash(result) for result in stage_results], "parent_receipt_hash": parent_receipt_hash, "review_result_hash": review_result_hash, "decision_attestation_hash": decision_attestation_hash, "final_disposition": disposition}
    receipt["receipt_hash"] = canonical_hash(receipt)
    validate_receipt(receipt)
    return receipt


def validate_receipt(receipt: Any) -> dict[str, Any]:
    fields = {"schema_version", "qualification_id", "subject_id", "record_fingerprint", "manifest_hash", "expected_set_hash", "ledger_hash", "stage_result_hashes", "parent_receipt_hash", "review_result_hash", "decision_attestation_hash", "final_disposition", "receipt_hash"}
    value = _closed(receipt, fields)
    if value["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported_schema", "Receipt schema is unsupported.")
    _identifier(value["qualification_id"], "qualification ID")
    _identifier(value["subject_id"], "subject ID")
    for field in ("record_fingerprint", "manifest_hash", "expected_set_hash", "ledger_hash", "receipt_hash"):
        _digest(value[field], field)
    if not isinstance(value["stage_result_hashes"], list) or len(value["stage_result_hashes"]) != len(STAGE_IDS):
        _fail("stage_result_catalog_mismatch", "Receipt must bind all eighteen stage results.")
    for stage_hash in value["stage_result_hashes"]:
        _digest(stage_hash, "stage result hash")
    for field in ("parent_receipt_hash", "review_result_hash", "decision_attestation_hash"):
        if value[field] is not None:
            _digest(value[field], field)
    if value["final_disposition"] not in FINAL_DISPOSITIONS:
        _fail("invalid_disposition", "Receipt disposition is not recognized.")
    claimed = value["receipt_hash"]
    unsigned = {key: item for key, item in value.items() if key != "receipt_hash"}
    if claimed != canonical_hash(unsigned):
        _fail("tampered_receipt", "Receipt hash is invalid.")
    return value


@dataclass(frozen=True, slots=True)
class PublicationBundle:
    manifest_bytes: bytes
    expected_set_bytes: bytes
    ledger_bytes: bytes
    stage_results_bytes: bytes
    receipt_bytes: bytes
    imported_evidence: ImportedEvidenceMaterial | None
    trusted_decision: TrustedDecisionMaterial | None


def _publication_material(data: Any) -> Any:
    if not isinstance(data, bytes):
        _fail("unverified_publication", "Publication material must be canonical bytes.")
    value = strict_json_loads(data)
    if canonical_bytes(value) != data:
        _fail("unverified_publication", "Publication material must use canonical encoding.")
    return value


def _reconstruct_publication(publication: Any) -> dict[str, Any]:
    if not isinstance(publication, PublicationBundle):
        _fail("unverified_publication", "Receipt publication requires the complete raw qualification chain.")
    manifest = _publication_material(publication.manifest_bytes)
    expected_set = _publication_material(publication.expected_set_bytes)
    ledger = _publication_material(publication.ledger_bytes)
    stage_results = _publication_material(publication.stage_results_bytes)
    receipt = _publication_material(publication.receipt_bytes)
    validate_manifest(manifest)
    expected_import_hash = _verified_import_hash(publication.imported_evidence)
    if manifest["imported_evidence_hash"] != expected_import_hash:
        _fail("unverified_imported_evidence", "Publication requires the locked imported-evidence material bound by the manifest.")
    supported = set(publication.imported_evidence.supported_stage_ids if publication.imported_evidence is not None else ())
    if not isinstance(stage_results, list):
        _fail("stage_result_catalog_mismatch", "Publication stage results must be an ordered array.")
    for result in stage_results[:-1]:
        if isinstance(result, dict) and result.get("state") == "passed" and result.get("stage_id") not in supported:
            _fail("unsupported_stage_pass", "A passing stage lacks semantically verified evidence.")
    rebuilt = build_receipt(
        manifest,
        expected_set,
        ledger,
        stage_results,
        receipt.get("final_disposition") if isinstance(receipt, dict) else None,
        parent_receipt_hash=receipt.get("parent_receipt_hash") if isinstance(receipt, dict) else None,
        review_result_hash=receipt.get("review_result_hash") if isinstance(receipt, dict) else None,
        trusted_decision=publication.trusted_decision,
    )
    if receipt != rebuilt:
        _fail("unverified_publication", "Receipt does not match the fully reconstructed qualification chain.")
    return rebuilt


def prepare_publication(
    manifest: dict[str, Any],
    expected_set: dict[str, Any],
    ledger: dict[str, Any],
    stage_results: list[dict[str, Any]],
    receipt: dict[str, Any],
    *,
    verified_import: ImportedEvidenceMaterial | None = None,
    trusted_decision: TrustedDecisionMaterial | None = None,
) -> PublicationBundle:
    publication = PublicationBundle(
        canonical_bytes(manifest),
        canonical_bytes(expected_set),
        canonical_bytes(ledger),
        canonical_bytes(stage_results),
        canonical_bytes(receipt),
        verified_import,
        trusted_decision,
    )
    _reconstruct_publication(publication)
    return publication


@dataclass(frozen=True, slots=True)
class PublicationResult:
    receipt_hash: str
    status: str
    publication_count: int


def _write_exclusive_at(directory: int, name: str, data: bytes) -> os.stat_result:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def _read_regular_at(directory: int, name: str) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("unsafe_path", "Publication entry must be a regular file.")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks), metadata
    finally:
        os.close(descriptor)


def _open_directory_at(directory: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)


def _entry_exists_at(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _receipt_count(directory: int) -> int:
    count = 0
    for name in os.listdir(directory):
        if _HASH_RE.fullmatch(name):
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                count += 1
    return count


def _remove_owned_pending(root: int, pending_name: str, owner: dict[str, Any]) -> None:
    pending = _open_directory_at(root, pending_name)
    try:
        owner_bytes, _ = _read_regular_at(pending, "owner.json")
        if strict_json_loads(owner_bytes) != owner or set(os.listdir(pending)) != {"owner.json", "receipt.json"}:
            _fail("foreign_lock", "Pending publication belongs to another operation.")
        os.unlink("receipt.json", dir_fd=pending)
        os.unlink("owner.json", dir_fd=pending)
        os.fsync(pending)
    finally:
        os.close(pending)
    os.rmdir(pending_name, dir_fd=root)
    os.fsync(root)


def publish_receipt(output_root: str | Path, publication: PublicationBundle, *, recovery_token: str, interrupt_at: str | None = None) -> PublicationResult:
    receipt = _reconstruct_publication(publication)
    root = Path(output_root)
    if root.is_symlink():
        _fail("unsafe_path", "Publication root must not be a symlink.")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise QualificationError("unsafe_path", "Publication root must be an existing no-follow directory.") from exc
    receipt_hash = receipt["receipt_hash"]
    owner = {"qualification_id": receipt["qualification_id"], "parent_hash": receipt["parent_receipt_hash"], "receipt_hash": receipt_hash, "recovery_token_hash": hashlib.sha256(recovery_token.encode("utf-8")).hexdigest()}
    owner_bytes = canonical_bytes(owner)
    lock_metadata: os.stat_result | None = None
    try:
        try:
            lock_metadata = _write_exclusive_at(root_descriptor, ".publish.lock", owner_bytes)
            os.fsync(root_descriptor)
        except FileExistsError as exc:
            try:
                existing_bytes, lock_metadata = _read_regular_at(root_descriptor, ".publish.lock")
                existing = strict_json_loads(existing_bytes)
            except (OSError, QualificationError) as read_error:
                raise QualificationError("foreign_lock", "Publication lock cannot be safely recovered.") from read_error
            if existing != owner:
                raise QualificationError("foreign_lock", "Publication lock belongs to another operation.") from exc
        pending_name = f".pending-{receipt['qualification_id'].replace(':', '_')}-{receipt_hash}"
        pending_owned = False
        if _entry_exists_at(root_descriptor, "HEAD"):
            try:
                head_bytes, _ = _read_regular_at(root_descriptor, "HEAD")
                current_head = head_bytes.decode("ascii")
            except (OSError, UnicodeDecodeError) as exc:
                raise QualificationError("unsafe_path", "Publication HEAD is not a safe regular ASCII file.") from exc
            _digest(current_head, "HEAD")
        else:
            current_head = None
        if current_head not in {receipt["parent_receipt_hash"], receipt_hash}:
            _fail("receipt_parent_fork", "Publication HEAD differs from receipt parent.")
        if _entry_exists_at(root_descriptor, receipt_hash):
            try:
                destination = _open_directory_at(root_descriptor, receipt_hash)
                try:
                    existing_bytes, _ = _read_regular_at(destination, "receipt.json")
                finally:
                    os.close(destination)
                existing = strict_json_loads(existing_bytes)
            except (OSError, QualificationError) as exc:
                raise QualificationError("receipt_collision", "Existing receipt is not the expected immutable object.") from exc
            if existing != receipt:
                _fail("receipt_collision", "Existing receipt bytes differ.")
            if current_head != receipt_hash:
                temp_head_name = f".HEAD-{receipt_hash}"
                if _entry_exists_at(root_descriptor, temp_head_name):
                    temp_bytes, _ = _read_regular_at(root_descriptor, temp_head_name)
                    if temp_bytes != receipt_hash.encode("ascii"):
                        _fail("receipt_collision", "Temporary HEAD differs from the receipt identity.")
                else:
                    _write_exclusive_at(root_descriptor, temp_head_name, receipt_hash.encode("ascii"))
                os.replace(temp_head_name, "HEAD", src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
                os.fsync(root_descriptor)
            return PublicationResult(receipt_hash, "idempotent", _receipt_count(root_descriptor))
        if _entry_exists_at(root_descriptor, pending_name):
            _remove_owned_pending(root_descriptor, pending_name, owner)
        os.mkdir(pending_name, 0o700, dir_fd=root_descriptor)
        pending_owned = True
        pending_descriptor = _open_directory_at(root_descriptor, pending_name)
        try:
            _write_exclusive_at(pending_descriptor, "receipt.json", canonical_bytes(receipt))
            _write_exclusive_at(pending_descriptor, "owner.json", owner_bytes)
            os.fsync(pending_descriptor)
        finally:
            os.close(pending_descriptor)
        if interrupt_at == "before_rename":
            _fail("cancelled_before_rename", "Publication interrupted before receipt rename.")
        os.rename(pending_name, receipt_hash, src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
        pending_owned = False
        os.fsync(root_descriptor)
        if interrupt_at == "after_rename":
            _fail("interrupted_after_rename", "Publication interrupted after receipt rename.")
        temp_head_name = f".HEAD-{receipt_hash}"
        _write_exclusive_at(root_descriptor, temp_head_name, receipt_hash.encode("ascii"))
        os.replace(temp_head_name, "HEAD", src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
        os.fsync(root_descriptor)
        if interrupt_at == "after_head":
            return PublicationResult(receipt_hash, "already_terminal", _receipt_count(root_descriptor))
        return PublicationResult(receipt_hash, "published", _receipt_count(root_descriptor))
    finally:
        try:
            if "pending_owned" in locals() and pending_owned and _entry_exists_at(root_descriptor, pending_name):
                _remove_owned_pending(root_descriptor, pending_name, owner)
            if lock_metadata is not None and _entry_exists_at(root_descriptor, ".publish.lock"):
                current_bytes, current_metadata = _read_regular_at(root_descriptor, ".publish.lock")
                if (current_metadata.st_dev, current_metadata.st_ino, current_bytes) != (lock_metadata.st_dev, lock_metadata.st_ino, owner_bytes):
                    _fail("foreign_lock", "Publication lock changed while held.")
                os.unlink(".publish.lock", dir_fd=root_descriptor)
                os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)


def initial_qualification(subject_id: str, *, verified_import: ImportedEvidenceMaterial | None = None) -> dict[str, Any]:
    manifest = build_manifest(subject_id, verified_import=verified_import)
    expected = make_expected_set(manifest)
    limitations = []
    if subject_id == "qwen3_base":
        limitations = ["imported_structural_only", "no_new_production_authority", "listening_missing"]
    elif subject_id == "qwen3_instruction_controlled":
        limitations = ["experimental_unaccepted", "listening_missing"]
    else:
        limitations = ["single_fixture", "single_speaker", "english_only", "macos_only", "evaluation_only", "artifact_identity_unverified", "expected_set_lineage_unverified"]
    rows = [terminal_row(manifest, item, assertion_outcome="block", limitations=limitations, evidence_hash=manifest["imported_evidence_hash"]) for item in expected["items"]]
    if subject_id == "mlx_whisper_base":
        fidelity = next(row for row in rows if row["item_id"] == "source_span_and_spoken_content_fidelity")
        fidelity["metrics"] = [{
            "id": "word_error_rate", "formula_version": "wer_standard_v1", "unit": "ratio",
            "value_decimal": "0", "sample_count": 1, "confidence_level_decimal": None,
            "confidence_interval_decimal_pair": None,
            "limitation_codes": ["insufficient_sample_size", "single_fixture", "single_speaker", "english_only", "macos_only", "evaluation_only"],
        }]
    ledger = make_ledger(manifest, expected, rows)
    stage_results = [aggregate_stage(manifest, expected, ledger, stage) for stage in STAGE_IDS[:-1]]
    disposition = derive_disposition(manifest, stage_results)
    final_item = next(item for item in expected["items"] if item["stage_id"] == STAGE_IDS[-1])
    final_row = next(row for row in ledger["rows"] if row["item_id"] == final_item["item_id"])
    final_row["assertion_outcome"] = "pass"
    final_row["output_hash"] = canonical_hash(_final_disposition_assertion(manifest, disposition, stage_results))
    stage_results.append(aggregate_stage(manifest, expected, ledger, STAGE_IDS[-1]))
    receipt = build_receipt(manifest, expected, ledger, stage_results, disposition)
    return {"manifest": manifest, "expected_set": expected, "ledger": ledger, "stage_results": stage_results, "receipt": receipt, "limitations": limitations}


def verify_imported_evidence(fixture_root: str | Path) -> ImportedEvidenceMaterial:
    root = Path(os.path.abspath(os.fspath(fixture_root)))
    try:
        root_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            imported_descriptor = _open_directory_at(root_descriptor, "imported_evidence")
        finally:
            os.close(root_descriptor)
    except OSError as exc:
        raise QualificationError("fixture_root_invalid", "Imported evidence root is missing or unsafe.") from exc
    try:
        try:
            sidecar_bytes, _ = _read_regular_at(imported_descriptor, "sources.json")
        except OSError as exc:
            raise QualificationError("imported_evidence_missing", "Locked import source sidecar is missing or unsafe.") from exc
        sidecar = _closed(strict_json_loads(sidecar_bytes), {"schema_version", "accepted_parent", "sources"}, "import_sidecar_schema_mismatch")
        expected_sources = [
            {"snapshot": name, "source_path": _IMPORTED_SOURCE_PATHS[name], "sha256": digest}
            for name, digest in _IMPORTED_EVIDENCE.items()
        ]
        if sidecar != {"schema_version": 1, "accepted_parent": "8f2e98bde6376caa7b3690c0f50f78ee592a1197", "sources": expected_sources}:
            _fail("imported_evidence_drift", "Locked import source sidecar differs from the approved sources.")
        observed: dict[str, str] = {}
        snapshots: dict[str, bytes] = {}
        for name, expected_hash in _IMPORTED_EVIDENCE.items():
            try:
                snapshot_bytes, _ = _read_regular_at(imported_descriptor, _safe_relative(name))
            except OSError as exc:
                raise QualificationError("imported_evidence_missing", f"Locked imported evidence is missing: {name}.") from exc
            observed_hash = hashlib.sha256(snapshot_bytes).hexdigest()
            if observed_hash != expected_hash:
                _fail("imported_evidence_drift", f"Locked imported evidence drifted: {name}.")
            observed[name] = observed_hash
            snapshots[name] = snapshot_bytes
        try:
            instruction = json.loads(
                snapshots["instruction-trace.json"],
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=lambda token: _fail("non_finite", f"Non-finite token forbidden: {token}."),
            )
            known = json.loads(
                snapshots["known-transcript-result.json"],
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=lambda token: _fail("non_finite", f"Non-finite token forbidden: {token}."),
            )
        except json.JSONDecodeError as exc:
            raise QualificationError("imported_evidence_semantic_mismatch", "Imported JSON evidence is invalid.") from exc
        try:
            instruction_ok = (
                instruction["schema_version"] == 1
                and instruction["capability_contract"]["status"] == "experimental_unaccepted"
                and instruction["capability_contract"]["production_default"] is False
                and instruction["evidence_comparison"]["qwen_icl_patch"]["revision"] == "e7dd0585652209fa0d7783659aad4e8a324de11c"
                and instruction["evidence_comparison"]["qwen_icl_patch"]["manual_listening_status"] == "pending"
                and instruction["evidence_comparison"]["qwen_icl_patch"]["delivery_adherence_accepted"] is False
            )
            known_ok = (
                known["result"]["model_key"] == "mlx_whisper_base"
                and known["result"]["revision"] == "1e3e249fb8d01c655324bd6841b1deadffd6d04c"
                and known["result"]["local_files_only"] is True
                and known["result"]["expected_count"] == known["result"]["success_count"] == 1
                and known["result"]["failure_count"] == 0
                and known["result"]["measurements"]["known-say-fixture"]["word_error_rate"] == 0
            )
        except (KeyError, TypeError):
            instruction_ok = known_ok = False
        reports_ok = all(
            b"8f2e98bde6376caa7b3690c0f50f78ee592a1197" in snapshots[name] and b"PASS" in snapshots[name]
            for name in ("b20-t01-final-f3-manual-qa.md", "b20-t01-final-f4-scope-fidelity.md")
        )
        if not instruction_ok or not known_ok or not reports_ok:
            _fail("imported_evidence_semantic_mismatch", "Imported evidence content does not match its approved revision, disposition, or result semantics.")
        source_hashes = tuple(observed.items())
        supported_stage_ids: tuple[str, ...] = ()
        bundle_hash = canonical_hash({"accepted_parent": sidecar["accepted_parent"], "source_hashes": [list(item) for item in source_hashes], "supported_stage_ids": list(supported_stage_ids)})
        return ImportedEvidenceMaterial(str(root), sidecar["accepted_parent"], source_hashes, bundle_hash, supported_stage_ids)
    finally:
        os.close(imported_descriptor)


_TRUSTED_DECISION_FIELDS: Final = {"schema_version", "decision_kind", "subject_id", "record_fingerprint", "record_projection_hash", "profile_hash", "package_hash", "result_hash", "reviewer_id", "nonce", "issued_ns"}
_SIGNATURE_NAMESPACE: Final = "alexandria-engine-qualification-v1"
_SSH_KEYGEN: Final = "/usr/bin/ssh-keygen"


def _verify_trusted_decision_input(
    value: Any,
    *,
    manifest: dict[str, Any],
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    signer_identity: str,
    nonce_ledger_root: str | Path,
    package_hash: str | None,
    result_hash: str | None,
    evidence_origin: str,
    consume_nonce: bool,
) -> dict[str, Any]:
    decision = _closed(value, _TRUSTED_DECISION_FIELDS, "trusted_decision_schema_mismatch")
    validate_manifest(manifest)
    if evidence_origin != "authoritative_existing" or manifest["evidence_origin"] != "authoritative_existing":
        _fail("synthetic_attestation_forbidden", "Synthetic decisions never satisfy trusted human review.")
    if decision["schema_version"] != SCHEMA_VERSION or decision["decision_kind"] not in {"approve", "reject"}:
        _fail("invalid_user_decision", "Trusted decision schema or kind is invalid.")
    if decision["subject_id"] != manifest["subject_id"] or decision["record_fingerprint"] != manifest["record_fingerprint"]:
        _fail("trusted_decision_scope_mismatch", "Trusted decision is bound to another subject record.")
    projection_hash = canonical_hash(manifest["record_projections"])
    if decision["record_projection_hash"] != projection_hash or decision["profile_hash"] != manifest["metric_profile_hash"]:
        _fail("trusted_decision_scope_mismatch", "Trusted decision is bound to another projection or profile.")
    if decision["package_hash"] != package_hash or decision["result_hash"] != result_hash:
        _fail("wrong_package", "Trusted decision is bound to another review package or result.")
    _identifier(decision["reviewer_id"], "reviewer ID")
    if not isinstance(decision["nonce"], str) or not decision["nonce"] or not isinstance(decision["issued_ns"], int) or isinstance(decision["issued_ns"], bool) or decision["issued_ns"] < 0:
        _fail("invalid_user_decision", "Trusted decision nonce and issue time are required.")
    for field in ("record_fingerprint", "record_projection_hash", "profile_hash"):
        _digest(decision[field], field)
    for field in ("package_hash", "result_hash"):
        if decision[field] is not None:
            _digest(decision[field], field)
    signature = Path(signature_path)
    allowed = Path(allowed_signers_path)
    nonce_root = Path(nonce_ledger_root)
    if not isinstance(signer_identity, str) or not signer_identity or "\x00" in signer_identity:
        _fail("invalid_user_decision", "Signer identity must be non-empty text.")
    try:
        signature_descriptor = os.open(signature, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            allowed_descriptor = os.open(allowed, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            os.close(signature_descriptor)
            raise
    except OSError as exc:
        raise QualificationError("unsafe_trust_path", "Trusted signature and signer policy must be existing no-follow regular files.") from exc
    try:
        if not stat.S_ISREG(os.fstat(signature_descriptor).st_mode) or not stat.S_ISREG(os.fstat(allowed_descriptor).st_mode):
            _fail("unsafe_trust_path", "Trusted signature and signer policy must be regular files.")
        try:
            completed = subprocess.run(
                [_SSH_KEYGEN, "-Y", "verify", "-f", f"/dev/fd/{allowed_descriptor}", "-I", signer_identity, "-n", _SIGNATURE_NAMESPACE, "-s", f"/dev/fd/{signature_descriptor}"],
                input=canonical_bytes(decision), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                pass_fds=(allowed_descriptor, signature_descriptor),
            )
        except OSError as exc:
            raise QualificationError("signature_verification_failed", "OpenSSH signature verification could not run.") from exc
    finally:
        os.close(allowed_descriptor)
        os.close(signature_descriptor)
    if completed.returncode != 0:
        _fail("signature_verification_failed", "Detached user signature is not trusted.")
    decision_hash = canonical_hash(decision)
    nonce_name = hashlib.sha256(decision["nonce"].encode("utf-8")).hexdigest() + ".used"
    payload = canonical_bytes({"qualification_id": manifest["qualification_id"], "trusted_decision_hash": decision_hash})
    try:
        directory = os.open(nonce_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            if consume_nonce:
                _write_exclusive_at(directory, nonce_name, payload)
                os.fsync(directory)
            else:
                observed, _ = _read_regular_at(directory, nonce_name)
                if observed != payload:
                    _fail("untrusted_decision", "Trusted-decision nonce lineage does not match the signed decision.")
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise QualificationError("replayed_attestation", "Trusted-decision nonce was already consumed.") from exc
    except OSError as exc:
        code = "nonce_ledger_write_failed" if consume_nonce else "untrusted_decision"
        raise QualificationError(code, "Trusted-decision nonce lineage could not be verified safely.") from exc
    return decision


def verify_trusted_decision(
    value: Any,
    *,
    manifest: dict[str, Any],
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    signer_identity: str,
    nonce_ledger_root: str | Path,
    package_hash: str | None,
    result_hash: str | None,
    evidence_origin: str = "authoritative_existing",
) -> TrustedDecisionMaterial:
    signature = os.path.abspath(os.fspath(signature_path))
    allowed_signers = os.path.abspath(os.fspath(allowed_signers_path))
    nonce_root = os.path.abspath(os.fspath(nonce_ledger_root))
    decision = _verify_trusted_decision_input(
        value,
        manifest=manifest,
        signature_path=signature,
        allowed_signers_path=allowed_signers,
        signer_identity=signer_identity,
        nonce_ledger_root=nonce_root,
        package_hash=package_hash,
        result_hash=result_hash,
        evidence_origin=evidence_origin,
        consume_nonce=True,
    )
    return TrustedDecisionMaterial(canonical_bytes(decision), signature, allowed_signers, signer_identity, nonce_root, evidence_origin)


def _reverify_trusted_decision(material: TrustedDecisionMaterial, manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(material, TrustedDecisionMaterial):
        _fail("untrusted_decision", "Trusted decisions require their raw signed material and nonce lineage.")
    decision = strict_json_loads(material.decision_bytes)
    if not isinstance(decision, dict) or canonical_bytes(decision) != material.decision_bytes:
        _fail("untrusted_decision", "Trusted decision material is not canonical.")
    return _verify_trusted_decision_input(
        decision,
        manifest=manifest,
        signature_path=material.signature_path,
        allowed_signers_path=material.allowed_signers_path,
        signer_identity=material.signer_identity,
        nonce_ledger_root=material.nonce_ledger_root,
        package_hash=decision.get("package_hash"),
        result_hash=decision.get("result_hash"),
        evidence_origin=material.evidence_origin,
        consume_nonce=False,
    )


def validate_user_attestation(value: Any, *, package_hash: str, output_root_hash: str, trusted_user_proof: str, used_hashes: set[str]) -> dict[str, Any]:
    attestation = _closed(value, {"schema_version", "actor_type", "package_hash", "output_root_hash", "decision", "nonce", "proof_hash", "attestation_hash"}, "attestation_schema_mismatch")
    unsigned = {key: item for key, item in attestation.items() if key != "attestation_hash"}
    if attestation["attestation_hash"] != canonical_hash(unsigned):
        _fail("forged_attestation", "Trusted decision attestation hash is invalid.")
    if attestation["actor_type"] != "user" or attestation["proof_hash"] != hashlib.sha256(trusted_user_proof.encode("utf-8")).hexdigest():
        _fail("forged_attestation", "Only externally supplied user proof crosses the trust boundary.")
    if attestation["package_hash"] != package_hash:
        _fail("wrong_package", "Trusted decision is bound to another review package.")
    if attestation["output_root_hash"] != output_root_hash:
        _fail("replayed_attestation", "Trusted decision is bound to another output root.")
    if attestation["attestation_hash"] in used_hashes:
        _fail("replayed_attestation", "Trusted decision has already been consumed.")
    if attestation["decision"] not in {"approve", "reject"}:
        _fail("invalid_user_decision", "Trusted decision is not recognized.")
    used_hashes.add(attestation["attestation_hash"])
    return attestation


def _fixture_attestation(package_hash: str, output_root_hash: str, proof: str, nonce: str) -> dict[str, Any]:
    unsigned = {"schema_version": SCHEMA_VERSION, "actor_type": "user", "package_hash": package_hash, "output_root_hash": output_root_hash, "decision": "reject", "nonce": nonce, "proof_hash": hashlib.sha256(proof.encode("utf-8")).hexdigest()}
    return {**unsigned, "attestation_hash": canonical_hash(unsigned)}


class OfflineCallGuard:
    def __init__(self) -> None:
        self._events: list[dict[str, str]] = []
        self._socket = socket.socket
        self._create_connection = socket.create_connection

    def _block(self, category: str, operation: str) -> None:
        self._events.append({"category": category, "operation": operation})
        _fail("offline_call_blocked", f"Offline qualification blocked a {category} call.")

    def __enter__(self) -> OfflineCallGuard:
        setattr(socket, "socket", lambda *args, **kwargs: self._block("network", "socket"))
        setattr(socket, "create_connection", lambda *args, **kwargs: self._block("network", "create_connection"))
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        setattr(socket, "socket", self._socket)
        setattr(socket, "create_connection", self._create_connection)

    def provider_call(self) -> None:
        self._block("provider", "provider_call")

    def model_call(self) -> None:
        self._block("model", "model_call")

    def model_load(self) -> None:
        self._block("model_load", "model_load")

    def download(self) -> None:
        self._block("download", "download")

    def counts(self) -> dict[str, int]:
        categories = ("network", "provider", "model", "model_load", "download")
        observed = {category: sum(event["category"] == category for event in self._events) for category in categories}
        return {**observed, "unstubbed_calls": len(self._events)}


def _exercise_fixture_cases(result: dict[str, Any], output: Path, seed: str) -> tuple[dict[str, str], dict[str, str]]:
    from engine_qualification_review import build_review_package, publish_review_package, validate_review_result

    package = build_review_package([
        {
            "expected_item_id": f"review_{index}", "subject_id": result["manifest"]["subject_id"],
            "record_fingerprint": result["manifest"]["record_fingerprint"], "profile_hash": result["manifest"]["metric_profile_hash"],
            "audio_identity": f"fixture-audio-{index}", "required_playback": True, "restriction_options": ["validation_only"],
        }
        for index in range(3)
    ], seed)
    publish_review_package(output / "human-package", output.parent / f"{output.name}-controlled-answer-keys", package)
    rejected: dict[str, str] = {}

    def require_rejection(name: str, code: str, callback: Callable[[], object]) -> None:
        try:
            callback()
        except RuntimeError as exc:
            observed_code = getattr(exc, "code", None)
            if observed_code != code:
                _fail("fixture_case_mismatch", f"{name} returned {observed_code}, expected {code}.")
            rejected[name] = observed_code
            return
        _fail("fixture_case_unexpected_success", f"{name} did not fail closed.")

    tampered = deepcopy(result["receipt"])
    tampered["final_disposition"] = "production_accepted"
    require_rejection("tampered_receipt", "tampered_receipt", lambda: validate_receipt(tampered))
    review_result = deepcopy(package["result_template"])
    review_result["incomplete_labels"] = []
    require_rejection("tampered_review", "tampered_review", lambda: validate_review_result(package["public"], package["answer_key"], review_result))
    missing = deepcopy(result["ledger"])
    missing["rows"].pop()
    require_rejection("missing_expected_item", "missing_terminal_row", lambda: validate_ledger(missing, result["manifest"], result["expected_set"]))
    require_rejection("unregistered_subject", "unregistered_subject", lambda: build_manifest("unregistered_subject"))
    package_hash = canonical_hash(package["public"])
    output_root_hash = canonical_hash(str(output.resolve()))
    proof = "fixture-user-boundary-validation-only"
    used: set[str] = set()
    forged = _fixture_attestation(package_hash, output_root_hash, "wrong-proof", "forged")
    require_rejection("forged_attestation", "forged_attestation", lambda: validate_user_attestation(forged, package_hash=package_hash, output_root_hash=output_root_hash, trusted_user_proof=proof, used_hashes=used))
    replay = _fixture_attestation(package_hash, output_root_hash, proof, "replay")
    validate_user_attestation(replay, package_hash=package_hash, output_root_hash=output_root_hash, trusted_user_proof=proof, used_hashes=used)
    require_rejection("replayed_attestation", "replayed_attestation", lambda: validate_user_attestation(replay, package_hash=package_hash, output_root_hash=output_root_hash, trusted_user_proof=proof, used_hashes=used))
    wrong = _fixture_attestation("0" * 64, output_root_hash, proof, "wrong-package")
    require_rejection("wrong_package", "wrong_package", lambda: validate_user_attestation(wrong, package_hash=package_hash, output_root_hash=output_root_hash, trusted_user_proof=proof, used_hashes=used))
    successes: dict[str, str] = {}
    publication = prepare_publication(result["manifest"], result["expected_set"], result["ledger"], result["stage_results"], result["receipt"], verified_import=result.get("verified_import"))
    with tempfile.TemporaryDirectory() as temporary:
        require_rejection("cancel_before_rename", "cancelled_before_rename", lambda: publish_receipt(Path(temporary) / "cancel", publication, recovery_token="cancel", interrupt_at="before_rename"))
        after_head = publish_receipt(Path(temporary) / "after-head", publication, recovery_token="after-head", interrupt_at="after_head")
        successes["cancel_after_head"] = after_head.status
        retry_root = Path(temporary) / "retry"
        try:
            publish_receipt(retry_root, publication, recovery_token="retry", interrupt_at="after_rename")
        except QualificationError as exc:
            if exc.code != "interrupted_after_rename":
                raise
        else:
            _fail("fixture_case_unexpected_success", "Restart-retry setup did not interrupt after rename.")
        retry = publish_receipt(retry_root, publication, recovery_token="retry")
        successes["restart_retry"] = "published_once" if retry.publication_count == 1 else "invalid_count"
    return rejected, successes


def _fixture_qualify(arguments: argparse.Namespace) -> int:
    output = Path(arguments.output_root)
    if not arguments.offline_guard:
        _fail("offline_guard_required", "Fixture qualification requires the offline guard.")
    output.mkdir(parents=True, exist_ok=True)
    guard = OfflineCallGuard()
    with guard:
        verified_import = verify_imported_evidence(arguments.fixture_root)
        subjects = arguments.subjects.split(",")
        if subjects != ["qwen3_base", "qwen3_instruction_controlled", "mlx_whisper_base"]:
            _fail("invalid_subject_set", "Fixture qualification requires the three approved subjects.")
        results = {subject: initial_qualification(subject, verified_import=verified_import) for subject in subjects}
        for result in results.values():
            result["verified_import"] = verified_import
        for subject, result in results.items():
            (output / f"{subject}.json").write_bytes(canonical_bytes(result["receipt"]))
        requested = arguments.exercise_errors.split(",") if arguments.exercise_errors else []
        required = ["tampered_receipt", "tampered_review", "missing_expected_item", "cancel_before_rename", "cancel_after_head", "restart_retry", "forged_attestation", "replayed_attestation", "wrong_package", "unregistered_subject"]
        if requested != required:
            _fail("fixture_case_set_mismatch", "Fixture error/recovery cases differ from the locked matrix.")
        rejected, successes = _exercise_fixture_cases(results["qwen3_base"], output, arguments.seed)
    call_counts = guard.counts()
    fixture_hashes = dict(verified_import.source_hashes)
    summary = {
        "schema_version": 1, "subjects": 3, "stage_results": sum(len(result["stage_results"]) for result in results.values()),
        "per_subject_expected_counts": {subject: len(result["expected_set"]["items"]) for subject, result in results.items()},
        "expected_items": sum(len(result["expected_set"]["items"]) for result in results.values()),
        "terminal_items": sum(len(result["ledger"]["rows"]) for result in results.values()), "trusted_user_results": 0,
        **call_counts, "dispositions": {subject: result["receipt"]["final_disposition"] for subject, result in results.items()},
        "record_fingerprints": {subject: result["manifest"]["record_fingerprint"] for subject, result in results.items()},
        "profile_hashes": {subject: result["manifest"]["metric_profile_hash"] for subject, result in results.items()},
        "record_projections": {subject: result["manifest"]["record_projections"] for subject, result in results.items()},
        "prior_authority": {subject: result["manifest"]["prior_authority"] for subject, result in results.items()},
        "fixture_hashes": fixture_hashes,
        "imported_evidence_hash": verified_import.bundle_hash,
    }
    error_summary = {"rejected_cases": rejected, "successful_recovery_cases": successes, "unexpected_publications": 0, "restart_retry_publications": 1}
    (output / "e2e.json").write_bytes(canonical_bytes(summary))
    (output / "e2e-error.json").write_bytes(canonical_bytes(error_summary))
    print(json.dumps(summary, sort_keys=True))
    return 0


def _read_regular_path(path: Path, label: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise QualificationError("closure_input_missing", f"Closure {label} is missing or unsafe: {path}.") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail("closure_input_missing", f"Closure {label} must be a regular file: {path}.")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _validate_test_log(raw: bytes | str, expected_count: int) -> None:
    text = raw.decode("utf-8", "strict") if isinstance(raw, bytes) else raw
    counts = re.findall(r"(?m)^Ran ([0-9]+) tests? in [0-9.]+s$", text)
    if counts != [str(expected_count)] or re.search(r"(?m)^OK$", text) is None or "skipped=" in text or "TimeoutExpired" in text:
        _fail("invalid_test_receipt", f"Test receipt must report exactly {expected_count} passing tests with no skip or timeout.")


def _contains_pass(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in {"verdict", "status", "result"} and isinstance(item, str) and item.casefold() == "pass" for key, item in value.items()) or any(_contains_pass(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_pass(item) for item in value)
    return False


def _evidence_inventory(root: Path, excluded: Path) -> dict[str, str]:
    try:
        root_metadata = os.lstat(root)
    except OSError as exc:
        raise QualificationError("closure_input_missing", "Closure evidence root is missing.") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        _fail("unsafe_path", "Closure evidence root must be a no-follow directory.")
    inventory: dict[str, str] = {}
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in names:
            metadata = os.lstat(directory_path / name)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                _fail("unsafe_path", "Closure evidence contains an unsafe directory entry.")
        for name in files:
            path = directory_path / name
            if path.resolve() == excluded.resolve():
                continue
            raw = _read_regular_path(path, "evidence artifact")
            inventory[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    return dict(sorted(inventory.items()))


def _verify_closure(arguments: argparse.Namespace) -> int:
    commit = arguments.commit
    _git_commit(commit)
    named_paths = {
        "plan": Path(arguments.plan), "master": Path(arguments.master), "hello": Path(arguments.hello),
        "ledger": Path(arguments.ledger), "boulder": Path(arguments.boulder),
        "start_work_ledger": Path(arguments.start_work_ledger),
    }
    inputs = {name: _read_regular_path(path, name) for name, path in named_paths.items()}
    plan_text = inputs["plan"].decode("utf-8", "strict")
    if re.search(r"(?m)^- \[ \] (?:[1-7]\.|F[1-5]\.)", plan_text):
        _fail("closure_incomplete", "Focused-plan task or final-gate checkbox remains incomplete.")
    master_text = inputs["master"].decode("utf-8", "strict")
    if "| `B20-T07` | COMPLETE" not in master_text or "| `B20-T02` | BLOCKED" not in master_text:
        _fail("closure_scope_mismatch", "Master state must close B20-T07 while leaving B20-T02 blocked.")
    for name in ("hello", "ledger", "boulder", "start_work_ledger"):
        if commit not in inputs[name].decode("utf-8", "strict"):
            _fail("closure_commit_mismatch", f"Closure {name} does not bind the exact task commit.")
    strict_json_loads(inputs["boulder"])
    for line in inputs["start_work_ledger"].splitlines():
        if line.strip():
            strict_json_loads(line)

    evidence_root = Path(arguments.evidence_root)
    test_logs = {
        "task-7/focused-qualification.txt": 89,
        "task-7/focused-authority.txt": 46,
        "task-7/focused-runtime-recovery.txt": 81,
        "task-7/focused-production.txt": 143,
        "task-7/full-suite.txt": 2270,
    }
    for relative, expected_count in test_logs.items():
        _validate_test_log(_read_regular_path(evidence_root / relative, relative), expected_count)
    zero_calls = strict_json_loads(_read_regular_path(evidence_root / "task-7/zero-call-ledger.json", "zero-call ledger"))
    if zero_calls.get("unstubbed_calls") != 0:
        _fail("closure_scope_mismatch", "Closure requires zero unstubbed provider, network, and model calls.")
    e2e = strict_json_loads(_read_regular_path(evidence_root / "task-7/e2e/e2e.json", "fixture summary"))
    expected_summary = {"subjects": 3, "stage_results": 54, "expected_items": 49, "terminal_items": 49, "trusted_user_results": 0, "unstubbed_calls": 0}
    if any(e2e.get(key) != expected for key, expected in expected_summary.items()) or e2e.get("per_subject_expected_counts") != {"mlx_whisper_base": 13, "qwen3_base": 18, "qwen3_instruction_controlled": 18} or set(e2e.get("dispositions", {}).values()) != {"deferred"}:
        _fail("closure_scope_mismatch", "Fixture summary differs from the locked truthful counts or dispositions.")
    errors = strict_json_loads(_read_regular_path(evidence_root / "task-7/e2e/e2e-error.json", "fixture error summary"))
    if set(errors.get("rejected_cases", {})) != {"tampered_receipt", "tampered_review", "missing_expected_item", "cancel_before_rename", "forged_attestation", "replayed_attestation", "wrong_package", "unregistered_subject"} or errors.get("successful_recovery_cases") != {"cancel_after_head": "already_terminal", "restart_retry": "published_once"} or errors.get("restart_retry_publications") != 1 or errors.get("unexpected_publications") != 0:
        _fail("closure_scope_mismatch", "Fixture failure and recovery summary differs from the locked matrix.")
    for name in ("f1-plan-compliance.json", "f2-code-security.json", "f3-manual-qa.json", "f4-scope-truth.json", "f5-review-debug.json"):
        raw = _read_regular_path(evidence_root / "final" / name, f"final gate {name}")
        receipt = strict_json_loads(raw)
        if commit not in raw.decode("utf-8", "strict") or not _contains_pass(receipt):
            _fail("closure_gate_failed", f"Final gate does not PASS at the exact task commit: {name}.")

    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence_hashes = _evidence_inventory(evidence_root, output)
    report = {
        "schema_version": 1, "commit": commit,
        "inputs": {str(named_paths[name]): hashlib.sha256(raw).hexdigest() for name, raw in inputs.items()},
        "evidence_root": str(evidence_root), "evidence_hashes": evidence_hashes,
        "counts": {"qualification": 89, "authority": 46, "runtime_recovery": 81, "production": 143, "full_suite": 2270, "subjects": 3, "stage_results": 54, "expected_items": 49, "terminal_items": 49, "unstubbed_calls": 0},
        "status": "PASS",
    }
    try:
        parent = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            _write_exclusive_at(parent, output.name, canonical_bytes(report))
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError as exc:
        raise QualificationError("closure_output_failed", "Closure report must be created once beneath a safe parent.") from exc
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine_qualification")
    commands = parser.add_subparsers(dest="command", required=True)
    fixture = commands.add_parser("fixture-qualify")
    fixture.add_argument("--fixture-root", required=True)
    fixture.add_argument("--output-root", required=True)
    fixture.add_argument("--subjects", required=True)
    fixture.add_argument("--seed", required=True)
    fixture.add_argument("--offline-guard", action="store_true")
    fixture.add_argument("--exercise-errors", default="")
    fixture.set_defaults(handler=_fixture_qualify)
    closure = commands.add_parser("verify-closure")
    for name in ("commit", "plan", "master", "hello", "ledger", "boulder", "start-work-ledger", "evidence-root", "output"):
        closure.add_argument(f"--{name}", required=True)
    closure.set_defaults(handler=_verify_closure)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return arguments.handler(arguments)
    except QualificationError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
