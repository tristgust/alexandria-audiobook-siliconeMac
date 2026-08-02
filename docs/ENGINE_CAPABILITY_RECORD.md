# Engine Capability Record

`app/model_registry.py` owns Alexandria's versioned engine and component truth.
Its `EngineComponentRecord` catalog is the only place that pins component source
IDs, immutable revisions, required artifact paths, runtimes, loaders, artifact
roles, deterministic build identities, and serialization formats. Qwen records
identify model, text-tokenizer, speech-tokenizer/codec, and auxiliary assets;
VoxCPM2 records identify its model and tokenizer assets. `ModelSpec` and the existing model-registry
payload remain compatibility projections of those component records.

Each engine record declares its stable ID and revision, component IDs, provider,
support and readiness state, platform and device constraints, languages, sample
rates, modes, Voice methods, preprocessing, instruction contract, synthesis
window and seam policy, determinism and streaming behavior, concurrency and
lifecycle policy, expected memory, offline/cache/acquisition/repair policy, and
consumer IDs. Record fingerprints use canonical sorted JSON and therefore bind
all of those decisions.

## Migration state

Qwen supplied-recording clone (`qwen3_base`) and VoxCPM2 controlled clone
(`voxcpm2_controlled`) are `migrated`. Existing engines not yet expanded by this
work are explicit `legacy_passthrough` records. Passthrough preserves their
current public route and fallback behavior while preventing a second capability
table from becoming authoritative.

Strict migration accepts only the shipped pre-record Qwen/Vox synthesis-window
and model-spec payloads or an already canonical record. Validation rejects
duplicate stable IDs, unknown fields, moving or mismatched revisions,
unsupported-ready contradictions, and synthesis, instruction, or offline-policy
drift. Migration is deterministic, idempotent, and preserves canonical
fingerprints.

## Consumer rules

Consumers query record projections instead of copying engine membership,
revision, synthesis-window, instruction, or offline decisions. Existing APIs in
`model_registry`, `synthesis_windows`, `instruction_propagation`, and
`voice_backend_capabilities` retain their public shapes. Unknown synthesis
backends still use the established external-generic fallback; strict handling of
record input does not change runtime route fallback behavior.

`capability_truth.audit_engine_record_truth()` requires independently supplied
consumer declarations and public engine projections, then compares them with the
canonical record. A drift finding is diagnostic and
never loads a model, inspects a live cache or project, or repairs state.

## Fixture artifact admission

`app/engine_artifact_admission.py` admits synthetic or prepared artifact trees
through a strict manifest containing the engine revision, record fingerprint,
and exact component source/revision/build/path/role/size/hash/runtime/loader/serialization set. It
rejects unknown fields, duplicates, unsafe paths and serialization, incompatible
components, stale declarations, altered or partial trees, and destination
collisions.

Admission copies into one unique same-parent staging directory, verifies staged
hashes, and publishes with an atomic no-overwrite rename. Failure removes only
that transaction's staging directory. Existing destinations and sources remain
unchanged. The returned receipt binds the canonical manifest hash and every
published file's path, size, and SHA-256 digest.
