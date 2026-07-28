"""Verify the blind Round 1 package and every published audio file."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from multimodel_round1_paths import (
    PathSafetyError,
    SafeRelativePath,
    contained_path_guard,
)
from multimodel_round1_public_audio import (
    PublicAudioError,
    SanitizedAudio,
    verify_public_audio,
)
from multimodel_round1_review_output import REVIEW_ASSET_FILES
from multimodel_round1_verify_contract import (
    PublicVerification,
    VerificationInputError,
    VerificationState,
    add_issue,
    load_public,
    read_json,
    read_text,
    relative_file_tree,
)


@dataclass(frozen=True, slots=True)
class _PublicVerifier:
    review: Path
    state: VerificationState
    artifacts: dict[str, SanitizedAudio]

    def audio(self, relative: str) -> SanitizedAudio | None:
        if relative in self.artifacts:
            return self.artifacts[relative]
        try:
            artifact = verify_public_audio(
                self.review / relative,
                path_guard=contained_path_guard(self.review),
            )
        except FileNotFoundError:
            return None
        except PublicAudioError as exc:
            add_issue(
                self.state.issues,
                f"public_audio_{exc.code}",
                relative,
            )
            return None
        except OSError:
            add_issue(self.state.issues, "public_audio_path", relative)
            return None
        self.artifacts[relative] = artifact
        return artifact


def _allowed_relative(
    value: Any,
    directory: str,
    suffixes: frozenset[str],
) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = SafeRelativePath(value)
    except PathSafetyError:
        return None
    path = Path(str(parsed))
    if path.parent != Path(directory) or path.suffix.casefold() not in suffixes:
        return None
    return str(parsed)


def _verify_candidate_rows(
    verifier: _PublicVerifier,
    public: dict[str, Any],
) -> set[str]:
    state = verifier.state
    anomalies = {
        entry["sample_id"]: entry for entry in state.anomaly_manifest["entries"]
    }
    rows = public.get("samples") or []
    rows_by_id = {row.get("sample_id"): row for row in rows}
    expected_ids = {
        sample["blind_id"] for sample in state.internal["sample_specs"]
    }
    if set(rows_by_id) != expected_ids or len(rows_by_id) != len(rows):
        add_issue(state.issues, "public_sample_ids", "samples")
    expected_audio: set[str] = set()
    for sample in state.internal["sample_specs"]:
        blind_id = str(sample["blind_id"])
        row = rows_by_id.get(blind_id)
        if row is None:
            continue
        source_sha = state.generated.get(str(sample["sample_id"]))
        anomaly = anomalies.get(sample["sample_id"])
        eligible = bool(source_sha and (not anomaly or anomaly["review_eligible"]))
        status = (
            "ready"
            if eligible
            else "diagnostic_hold"
            if source_sha
            else sample["status"]
        )
        if row.get("status") != status:
            add_issue(state.issues, "public_status_stale", blind_id)
        if (
            row.get("structurally_generated") != bool(source_sha)
            or row.get("review_eligible") != eligible
        ):
            add_issue(state.issues, "public_eligibility_stale", blind_id)
        if not eligible:
            if row.get("audio") is not None or row.get("audio_sha256") is not None:
                add_issue(state.issues, "public_pending_audio", blind_id)
            continue
        relative = _allowed_relative(
            row.get("audio"),
            "audio",
            frozenset((".wav",)),
        )
        if relative is None:
            add_issue(state.issues, "public_audio_stale", blind_id)
            continue
        expected_audio.add(relative)
        artifact = verifier.audio(relative)
        if artifact is None:
            add_issue(state.issues, "missing_packaged_audio", blind_id)
            continue
        if (
            row.get("audio_sha256") != artifact.sha256
            or Path(relative).stem != artifact.sha256
        ):
            add_issue(state.issues, "packaged_audio_hash", blind_id)
    return expected_audio


def _verify_reference_rows(
    verifier: _PublicVerifier,
    public: dict[str, Any],
) -> set[str]:
    expected: set[str] = set()
    for identity in (public.get("identities") or {}).values():
        for field in ("original_audio", "conditioning_audio"):
            value = identity.get(field)
            if not value:
                continue
            relative = _allowed_relative(
                value,
                "reference-audio",
                frozenset((".wav", ".mp3")),
            )
            if relative is None:
                add_issue(verifier.state.issues, "public_reference_path", str(value))
                continue
            expected.add(relative)
            artifact = verifier.audio(relative)
            if artifact is None:
                add_issue(
                    verifier.state.issues,
                    "missing_packaged_reference",
                    relative,
                )
            elif Path(relative).stem != artifact.sha256:
                add_issue(
                    verifier.state.issues,
                    "packaged_reference_hash",
                    relative,
                )
    return expected


def _verify_counts(
    public: dict[str, Any],
    manifest: dict[str, Any],
    state: VerificationState,
) -> None:
    anomalies = {
        entry["sample_id"]: entry for entry in state.anomaly_manifest["entries"]
    }
    group_counts = {group: 0 for group in state.internal["groups"]}
    structural_counts = {group: 0 for group in state.internal["groups"]}
    for sample in state.internal["sample_specs"]:
        if sample["sample_id"] in state.generated:
            structural_counts[sample["group"]] += 1
            anomaly = anomalies.get(sample["sample_id"])
            if not anomaly or anomaly["review_eligible"]:
                group_counts[sample["group"]] += 1
    counts_match = (
        public.get("generated_counts") == group_counts
        and public.get("structurally_generated_counts") == structural_counts
        and manifest.get("generated_counts") == group_counts
        and manifest.get("structurally_generated_counts") == structural_counts
        and manifest.get("generated_sample_count") == sum(group_counts.values())
        and manifest.get("review_eligible_sample_count") == sum(group_counts.values())
        and manifest.get("structurally_generated_sample_count")
        == len(state.generated)
        and manifest.get("long_output_anomaly_count")
        == state.anomaly_manifest["over_30_seconds_count"]
        and manifest.get("ceiling_hit_count")
        == state.anomaly_manifest["ceiling_hit_count"]
    )
    if not counts_match:
        add_issue(state.issues, "public_counts_stale", "generated_counts")


def verify_public(
    review: Path,
    state: VerificationState,
) -> PublicVerification:
    try:
        public, raw = load_public(review, "data.js")
        manifest = read_json(review, "manifest.json")
        manifest_raw = read_text(review, "manifest.json")
        assets_raw = "".join(read_text(review, name) for name in REVIEW_ASSET_FILES)
    except (OSError, json.JSONDecodeError, VerificationInputError):
        add_issue(state.issues, "invalid_public_package", str(review))
        return PublicVerification({}, {})
    secrets = {"answer_key_files", "answer-keys"}
    for model in state.internal["model_contract"]["models"]:
        secrets.update((model["key"], model["label"]))
    for sample in state.internal["sample_specs"]:
        if str(sample["identity_key"]).startswith("native_"):
            secrets.update(
                (
                    sample["identity_key"],
                    sample["identity_review_name"],
                    sample["identity_kind"],
                )
            )
    combined = raw + manifest_raw + assets_raw
    for secret in sorted(secrets):
        if secret and secret in combined:
            add_issue(state.issues, "public_blind_leak", str(secret))
    if (
        "cache_revalidation_status" in combined
        or "requires_revalidation" in combined
    ):
        add_issue(state.issues, "private_cache_status_public", "data.js")
    artifacts: dict[str, SanitizedAudio] = {}
    verifier = _PublicVerifier(review, state, artifacts)
    expected_audio = _verify_candidate_rows(verifier, public)
    expected_references = _verify_reference_rows(verifier, public)
    try:
        actual_audio = {f"audio/{name}" for name in relative_file_tree(review, "audio")}
        actual_references = {
            f"reference-audio/{name}"
            for name in relative_file_tree(review, "reference-audio")
        }
    except OSError:
        add_issue(state.issues, "unsafe_public_directory", str(review))
        actual_audio, actual_references = set(), set()
    for extra in sorted(actual_audio - expected_audio):
        add_issue(state.issues, "extra_packaged_audio", extra)
    for extra in sorted(actual_references - expected_references):
        add_issue(state.issues, "extra_packaged_reference", extra)
    _verify_counts(public, manifest, state)
    return PublicVerification(public, artifacts)
