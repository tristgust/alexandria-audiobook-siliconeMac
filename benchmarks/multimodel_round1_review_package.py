"""Build Round 1 public/private review rows from validated sources."""

from __future__ import annotations

from typing import Any

from multimodel_round1_chatterbox_cache_policy import (
    legacy_cache_revalidation_status,
)
from multimodel_round1_handoff import Round1HandoffPaths
from multimodel_round1_paths import (
    contained_path,
    parse_artifact_paths,
)
from multimodel_round1_review_audio import (
    AudioPublisher,
    PublishedAudio,
    PublicAudioDirectory,
)
from multimodel_round1_review_eligibility import write_moss_long_output_manifest
from multimodel_round1_review_inputs import (
    native_aliases,
    objective_measurements,
    reference_record,
    validate_receipt,
)
from multimodel_round1_review_output import ReviewPackageBuild
from multimodel_round1_runtime import (
    sha256_text,
    validate_sample_references,
)


class ReviewPackageError(RuntimeError):
    pass


def build_review_package(
    handoff: Round1HandoffPaths,
    internal: dict[str, Any],
) -> ReviewPackageBuild:
    evidence = handoff.evidence_root
    models = {
        str(item["key"]): item for item in internal["model_contract"]["models"]
    }
    anomaly_manifest = write_moss_long_output_manifest(evidence, internal)
    anomalies = {
        entry["sample_id"]: entry for entry in anomaly_manifest["entries"]
    }
    aliases = native_aliases(internal)
    objectives = objective_measurements(evidence, internal)
    publisher = AudioPublisher(evidence, handoff.public_root.name)
    reference_cache: dict[str, PublishedAudio] = {}
    reference_records: dict[str, dict[str, str]] = {}
    public_identities: dict[str, Any] = {}
    public_samples: list[dict[str, Any]] = []
    answer_keys = {str(key): [] for key in internal["groups"]}
    group_counts = {str(key): 0 for key in internal["groups"]}
    structural_counts = {str(key): 0 for key in internal["groups"]}
    model_counts = {key: 0 for key in models}
    review_model_counts = {key: 0 for key in models}
    for sample in internal["sample_specs"]:
        sample_id = str(sample["sample_id"])
        if sha256_text(sample["target_text"]) != sample["target_text_sha256"]:
            raise ReviewPackageError(f"invalid_manifest_target_text_hash: {sample_id}")
        validate_sample_references(evidence, sample)
        receipt, source_sha = validate_receipt(
            evidence,
            sample,
            models[str(sample["model_key"])],
        )
        anomaly = anomalies.get(sample_id)
        eligible = bool(source_sha and (not anomaly or anomaly["review_eligible"]))
        status = (
            "ready"
            if eligible
            else "diagnostic_hold"
            if source_sha
            else sample["status"]
        )
        publication: PublishedAudio | None = None
        if source_sha:
            structural_counts[str(sample["group"])] += 1
            model_counts[str(sample["model_key"])] += 1
        if eligible and source_sha:
            artifacts = parse_artifact_paths(
                evidence,
                str(sample["output_file"]),
                str(sample["result_file"]),
            )
            publication = publisher.publish(
                artifacts.output,
                PublicAudioDirectory.CANDIDATE,
                source_sha,
            )
            group_counts[str(sample["group"])] += 1
            review_model_counts[str(sample["model_key"])] += 1
        internal_identity = str(sample["identity_key"])
        native = internal_identity in aliases
        public_identity, public_label = aliases.get(
            internal_identity,
            (internal_identity, str(sample["identity_review_name"])),
        )
        reference_key = (
            f"{public_identity}:{sample['style']}"
            if internal_identity == "ryan_acted"
            else public_identity
        )
        reference = sample["reference"]
        public_reference: dict[str, Any] = {
            "identity_key": public_identity,
            "review_name": public_label,
            "kind": "native_voice_reference" if native else sample["identity_kind"],
            "conditioning_transcript": reference.get("conditioning_transcript"),
            "conditioning_transcript_sha256": reference.get(
                "conditioning_transcript_sha256"
            ),
        }
        for public_key, file_key, hash_key in (
            ("original_audio", "source_file", "source_sha256"),
            ("conditioning_audio", "conditioning_file", "conditioning_sha256"),
        ):
            value = reference.get(file_key)
            expected = reference.get(hash_key)
            if value and expected:
                source_file = f"references/{value}"
                source = contained_path(evidence, source_file)
                cache_key = f"{expected}:{source.literal.suffix.casefold()}"
                if cache_key not in reference_cache:
                    reference_cache[cache_key] = publisher.publish(
                        source,
                        PublicAudioDirectory.REFERENCE,
                        str(expected),
                    )
                published = reference_cache[cache_key]
                public_reference[public_key] = published.relative_path
                reference_records[source_file] = reference_record(
                    source_file,
                    published,
                )
        public_identities[reference_key] = public_reference
        objective = objectives.get(sample_id)
        public_samples.append(
            {
                "sample_id": sample["blind_id"],
                "group": sample["group"],
                "style": sample["style"],
                "style_label": sample["style_label"],
                "identity_key": public_identity,
                "identity_reference_key": reference_key,
                "expected_identity": public_label,
                "review_section_key": "model_native_voices" if native else public_identity,
                "review_section_label": "Model-native voices" if native else public_label,
                "target_text": sample["target_text"],
                "requested_instruction": sample["control"]["requested_instruction"],
                "status": status,
                "structurally_generated": bool(source_sha),
                "review_eligible": eligible,
                "diagnostic_hold_reason": "long_output_diagnostics_required" if anomaly else None,
                "audio": publication.relative_path if publication else None,
                "audio_sha256": publication.public_sha256 if publication else None,
                "automatic_transcript": objective.get("automatic_transcript") if objective else None,
                "word_error_rate": objective.get("word_error_rate") if objective else None,
                "speaker_cosine": objective.get("speaker_cosine_to_expected_identity") if objective else None,
                "audio_diagnostics": objective.get("audio_diagnostics") if objective else None,
            }
        )
        cache_status = legacy_cache_revalidation_status(
            receipt.get("conditionals_cache_hit") if receipt else None
        )
        answer_keys[str(sample["group"])].append(
            {
                "sample_id": sample["blind_id"],
                "source_sample_id": sample_id,
                "model_key": sample["model_key"],
                "model_label": sample["model_label"],
                "identity_key": internal_identity,
                "expected_identity": sample["identity_review_name"],
                "style": sample["style"],
                "group": sample["group"],
                "control": sample["control"],
                "reference": reference,
                "seed": sample["seed"],
                "sample_fingerprint": receipt.get("sample_fingerprint") if receipt else None,
                "audio_sha256": source_sha,
                "source_audio_sha256": source_sha,
                "public_audio": publication.relative_path if publication else None,
                "public_audio_sha256": publication.public_sha256 if publication else None,
                "source_decoded_sha256": publication.source_decoded_sha256 if publication else None,
                "public_decoded_sha256": publication.public_decoded_sha256 if publication else None,
                "cache_revalidation_status": cache_status,
                "status": status,
                "structurally_generated": bool(source_sha),
                "review_eligible": eligible,
                "generation_anomaly": anomaly,
            }
        )
    return ReviewPackageBuild(
        internal,
        public_identities,
        public_samples,
        answer_keys,
        aliases,
        group_counts,
        structural_counts,
        model_counts,
        review_model_counts,
        anomaly_manifest,
        [reference_records[key] for key in sorted(reference_records)],
    )
