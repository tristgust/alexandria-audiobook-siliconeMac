# Engine qualification

The qualification system evaluates an already registered engine or supporting component. It does not register models, alter provider routing, activate production engines, acquire artifacts, accept licenses, or replace the capability records in `app/model_registry.py`. Every manifest recomputes the authoritative B20-T01 record fingerprint and validates only the locked projections for its subject.

Synthetic fixtures exist only to validate the system. They cannot become a registry, a persistent qualification publication, or production evidence. The fixture command performs no provider, network, model-loading, download, cache, project, or audio operation.

## Immutable chain

All published objects use schema version 1, closed object schemas, NFC text, UTF-8, sorted compact JSON keys, and SHA-256 canonical hashes. Duplicate keys, unknown fields, a BOM, invalid UTF-8, floats, NaN, Infinity, Boolean numeric values, trailing data, and unsafe relative paths fail closed.

The chain is non-circular:

1. A manifest binds the authoritative record fingerprint, exact record projections, identities, stage catalog, locked metric profile, applicability, limitations, and prior authority.
2. An expected set declares every item before evaluation. Each subject is locked to its exact ordered fixture IDs, input identities, source spans, expected artifacts, stage IDs, and denominator; its hash binds the manifest.
3. A terminal ledger binds the manifest and expected set. Every expected ID has exactly one row.
4. Stage inputs bind the applicable expected items and terminal rows. Stage results bind their input hashes, and receipt construction recomputes every stage result from the bound ledger.
5. Stage 18 asserts the exact derived disposition, precedence rule, prior authority, record/profile lineage, and hashes of stages 1-17. A receipt then binds the manifest, expected set, ledger, all 18 ordered stage results, optional review and decision hashes, and its parent receipt.

Complete, failed, cancelled, timed-out, excluded, and invalidated rows all remain in the denominator. Exclusion requires a reason and evidence. Missing, duplicate, or unknown outcomes invalidate the ledger; survivor-only aggregation is impossible. Source spans and expected artifacts are copied from the declared expected item, so silence or ground-truth substitution changes the binding and is rejected.

Publication accepts a canonical raw-chain bundle, not an authorization capability. At every publication attempt it rereads and semantically verifies imported evidence, reconstructs the authoritative manifest, exact expected set, terminal ledger, all 18 stage results, trusted decision or absence, receipt, and recovery/provenance lineage. No module-global identity table or caller-constructed dataclass authorizes behavior; a self-rehashed receipt fails reconstruction. Publication is append-only and descriptor-relative beneath a caller-supplied no-follow root. An exclusive owner lock, pending directory, immutable receipt directory, and `HEAD` establish the publication sequence; files and containing directories are fsynced. Lock release rereads both owner content and inode before unlinking. A matching recovery token may recover its own pending work. A foreign lock, parent fork, receipt collision, or symlink fails closed. Cancellation before rename publishes nothing; interruption after rename is recoverable; cancellation after `HEAD` is already terminal and cannot be overwritten by success.

## Ordered stages

The exact stage order is:

1. `artifact_and_expected_set_validity`
2. `installation_and_loader_compatibility`
3. `offline_restart`
4. `relocation_and_cache_migration`
5. `packaged_or_frozen_runtime`
6. `source_span_and_spoken_content_fidelity`
7. `voice_identity_and_drift`
8. `acoustic_duration_seam_and_artifact_quality`
9. `delivery_and_instruction_response`
10. `repeated_seed_consistency`
11. `long_form_behavior`
12. `cancellation_and_interrupted_work_recovery`
13. `reset_and_uninstall_recovery`
14. `telemetry_and_network_review`
15. `unsafe_deserialization_review`
16. `provenance_validity`
17. `blinded_human_listening`
18. `final_truthful_capability_disposition`

An applicable stage with missing evidence is blocked, never automatically not applicable. The exact recognized applicability rules are `U1`, `P1`, `C1`, `V1`, `A1`, `D1`, `S1`, `L1`, `H1`, `H2`, and `N5` through `N11`. A not-applicable result requires its subject-locked `N*` or `H2` rule and has no hidden denominator rows. Failed or invalidated evidence fails a stage; cancelled, timed-out, excluded, or explicitly blocked evidence blocks it.

## Metrics and disposition

Metric profiles are subject-locked and hashed. Every observation states its formula version, unit, canonical decimal, sample count, confidence representation, and limitation codes. A sample below 30 reports a null Wilson interval and `insufficient_sample_size`. The imported Whisper observation is WER 0 for one fixture, with single-fixture, single-speaker, English-only, macOS-only, and evaluation-only limitations. It is observational and cannot promote a component.

Disposition precedence is trusted user rejection, license block, acquisition block, platform block, failed qualification, blocked/deferred qualification, then a user-approved capability-specific disposition. The ten values are `production_accepted`, `restricted`, `evaluation_only`, `supporting_component_accepted`, `blocked_by_license`, `blocked_by_acquisition`, `blocked_by_platform`, `failed_qualification`, `deferred`, and `rejected`. Metrics and package generation alone never accept production.

Prior authority is separate from the new qualification result. `qwen3_base` retains its inherited production baseline while B20-T07 is deferred. `qwen3_instruction_controlled` remains experimental and deferred without changing the neutral route. `mlx_whisper_base` retains evaluation-only supporting authority while B20-T07 remains deferred because artifact identity and expected-set/review lineage are incomplete.

## Blinded human review

The agent may build a package but may not fabricate listening approval. Labels are `sample-` plus the first 16 hexadecimal characters of HMAC-SHA-256 over the seed and canonical expected-item bytes. Ordering uses HMAC-SHA-256 over `order:` plus those bytes. The public package contains only opaque labels and independently opaque `audio-` handles, playback requirements, rating scale, restriction options, and incomplete-only navigation. Raw paths and subject, engine, component, or fixture identifiers are forbidden from public handles. A separate answer key maps labels and handles to expected-item, subject-record, and profile hashes and binds the public package, item set, and seed commitment. It must be written to a separately supplied controlled destination that is neither the public root nor its neighbor or ancestor.

The result template carries narrator/voice identity, text fidelity, delivery, naturalness, and artifact ratings; inclusion/exclusion, full-playback evidence, restrictions, and inert notes; plus immutable package/key/result hashes. Incomplete-only navigation is derived from exactly the unrated labels. Duplicate, unknown, reordered, Boolean, out-of-range, incomplete-playback, key-mismatched, or tampered votes fail closed.

Subjective approval remains user-owned. A trusted decision is a closed external record bound to the exact subject fingerprint, record-projection hash, profile hash, package/result hashes, reviewer, nonce, and issue time. It must carry a detached OpenSSH signature verified with `ssh-keygen -Y verify`, namespace `alexandria-engine-qualification-v1`, against a caller-supplied `allowed_signers` file outside the repository. Signature and signer-policy files are opened as regular no-follow descriptors before verification. Before use, its nonce is consumed exactly once in a caller-supplied external nonce ledger using exclusive no-follow creation and directory fsync. Every disposition or publication use reopens the raw signature and signer policy, reverifies the signature, and confirms the exact immutable nonce-ledger payload. A raw decision hash or preconstructed object cannot authorize approval or rejection. Synthetic/agent decisions, unknown signers, wrong namespace/package/result, and replay are rejected. The fixture proof records exercise rejection behavior only and can never satisfy this boundary. Missing or incomplete review means blocked/deferred, never accepted.

## Locked evidence and fixture workflow

`tests/fixtures/engine_qualification/imported_evidence/sources.json` records the four explicit canonical source paths, accepted parent `8f2e98bde6376caa7b3690c0f50f78ee592a1197`, and locked hashes. The snapshots are byte-identical, and their revision, capability status, observational result, and pending review state are semantically reverified from the locked files whenever the material is consumed and bound through the manifest and receipt chain. The imported sources do not establish the exact qualification expected set, exclusions, or completed review lineage, so the verified material supports no passing stage; those gaps remain terminal blocks. Import selects no file by glob, basename, modification time, or recency; a missing, changed, duplicate-key, or semantically inconsistent snapshot fails. Imported observations do not turn an unevaluated stage into a pass.

The offline guard actively replaces socket creation and records blocked network, provider, model, model-load, and download attempts. Reported zero-call counts are derived from that observed event ledger, not fixture assertions.

Run the offline exercise with the repository interpreter:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app:tests /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python -m engine_qualification fixture-qualify --fixture-root tests/fixtures/engine_qualification --output-root OUTPUT --subjects qwen3_base,qwen3_instruction_controlled,mlx_whisper_base --seed b20-t07-fixture-v1 --offline-guard --exercise-errors tampered_receipt,tampered_review,missing_expected_item,cancel_before_rename,cancel_after_head,restart_retry,forged_attestation,replayed_attestation,wrong_package,unregistered_subject
```

The result must report three subjects, 54 stage results, expected counts 18/18/13, 49 expected and terminal items, zero trusted user results, zero unstubbed calls, eight fail-closed cases, and two successful recovery cases. All three B20-T07 dispositions remain `deferred`.
