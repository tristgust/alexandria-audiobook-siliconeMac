"""Verify private candidate and reference audio records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multimodel_round1_chatterbox_cache_policy import (
    legacy_cache_revalidation_status,
)
from multimodel_round1_paths import (
    PathSafetyError,
    SafeRelativePath,
    contained_path,
    parse_artifact_paths,
    safe_sha256_file,
)
from multimodel_round1_review_audio import (
    ReviewAudioError,
    decoded_audio_sha256,
)
from multimodel_round1_verify_contract import (
    PublicVerification,
    VerificationState,
    add_issue,
)


@dataclass(frozen=True, slots=True)
class PrivateAudioVerifier:
    state: VerificationState
    public: PublicVerification
    public_rows: dict[str, dict[str, Any]]

    def candidate(
        self,
        sample: dict[str, Any],
        row: dict[str, Any],
    ) -> None:
        sample_id = str(sample["sample_id"])
        source_sha = self.state.generated.get(sample_id)
        anomalies = {
            entry["sample_id"]: entry
            for entry in self.state.anomaly_manifest["entries"]
        }
        anomaly = anomalies.get(sample_id)
        eligible = bool(source_sha and (not anomaly or anomaly["review_eligible"]))
        status = (
            "ready"
            if eligible
            else "diagnostic_hold"
            if source_sha
            else sample["status"]
        )
        expected = (
            row.get("sample_id") == sample["blind_id"]
            and row.get("model_key") == sample["model_key"]
            and row.get("control") == sample["control"]
            and row.get("audio_sha256") == source_sha
            and row.get("source_audio_sha256") == source_sha
            and row.get("sample_fingerprint")
            == (self.state.fingerprints[sample_id] if source_sha else None)
            and row.get("status") == status
            and row.get("structurally_generated") == bool(source_sha)
            and row.get("review_eligible") == eligible
            and row.get("generation_anomaly") == anomaly
        )
        if not expected:
            add_issue(self.state.issues, "answer_key_stale", sample_id)
        receipt = self.state.receipts.get(sample_id)
        cache_status = legacy_cache_revalidation_status(
            receipt.get("conditionals_cache_hit") if receipt else None
        )
        if row.get("cache_revalidation_status") != cache_status:
            add_issue(self.state.issues, "private_cache_revalidation", sample_id)
        if not eligible:
            publication_fields = (
                "public_audio",
                "public_audio_sha256",
                "source_decoded_sha256",
                "public_decoded_sha256",
            )
            if any(row.get(field) is not None for field in publication_fields):
                add_issue(self.state.issues, "answer_key_stale", sample_id)
            return
        public_row = self.public_rows.get(str(sample["blind_id"])) or {}
        relative = row.get("public_audio")
        artifact = self.public.artifacts.get(str(relative))
        if (
            relative != public_row.get("audio")
            or row.get("public_audio_sha256") != public_row.get("audio_sha256")
            or artifact is None
        ):
            add_issue(self.state.issues, "answer_key_stale", sample_id)
            return
        artifacts = parse_artifact_paths(
            self.state.evidence,
            str(sample["output_file"]),
            str(sample["result_file"]),
        )
        try:
            source_decoded = decoded_audio_sha256(artifacts.output.literal)
        except (OSError, ReviewAudioError):
            add_issue(self.state.issues, "private_source_audio_decode", sample_id)
            return
        if (
            row.get("source_decoded_sha256") != source_decoded
            or row.get("public_decoded_sha256") != artifact.decoded_sha256
            or source_decoded != artifact.decoded_sha256
        ):
            add_issue(
                self.state.issues,
                "private_source_decoded_mismatch",
                sample_id,
            )

    def references(self, private_manifest: dict[str, Any]) -> None:
        expected_sources: dict[str, str] = {}
        for sample in self.state.internal["sample_specs"]:
            reference = sample["reference"]
            for file_key, hash_key in (
                ("source_file", "source_sha256"),
                ("conditioning_file", "conditioning_sha256"),
            ):
                value = reference.get(file_key)
                expected = reference.get(hash_key)
                if value and expected:
                    expected_sources[f"references/{value}"] = str(expected)
        records = private_manifest.get("reference_audio_publications") or []
        by_source = {record.get("source_file"): record for record in records}
        if set(by_source) != set(expected_sources) or len(by_source) != len(records):
            add_issue(
                self.state.issues,
                "private_reference_publications",
                "source_files",
            )
        public_references = {
            identity[field]
            for identity in (self.public.data.get("identities") or {}).values()
            for field in ("original_audio", "conditioning_audio")
            if identity.get(field)
        }
        recorded_public = {
            record.get("public_file") for record in records if record.get("public_file")
        }
        if recorded_public != public_references:
            add_issue(
                self.state.issues,
                "private_reference_publications",
                "public_files",
            )
        for source_file, expected_sha in expected_sources.items():
            record = by_source.get(source_file)
            if record is None:
                continue
            try:
                SafeRelativePath(source_file)
                source = contained_path(self.state.evidence, source_file)
                public_file = SafeRelativePath(str(record.get("public_file")))
                public_path = Path(str(public_file))
                if public_path.parent != Path("reference-audio"):
                    raise PathSafetyError(str(public_file), "invalid public reference")
                source_sha = safe_sha256_file(source)
                source_decoded = decoded_audio_sha256(source.literal)
            except (OSError, ReviewAudioError):
                add_issue(
                    self.state.issues,
                    "private_reference_publications",
                    source_file,
                )
                continue
            artifact = self.public.artifacts.get(str(public_file))
            if (
                source_sha != expected_sha
                or record.get("source_sha256") != expected_sha
                or artifact is None
                or record.get("public_sha256") != artifact.sha256
                or public_path.stem != artifact.sha256
                or record.get("source_decoded_sha256") != source_decoded
                or record.get("public_decoded_sha256") != artifact.decoded_sha256
                or source_decoded != artifact.decoded_sha256
            ):
                add_issue(
                    self.state.issues,
                    "private_reference_decoded_mismatch",
                    source_file,
                )
