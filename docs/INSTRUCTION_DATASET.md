# Instruction-Aware Voice Dataset Contract

Alexandria uses one versioned data contract for instruction-conditioned SFT and LoRA experiments. The contract preserves the exact transcript and delivery instruction that were reviewed with each clip. It does not train a model, approve generated audio, or authorize a production Voice assignment.

Implementation: `app/instruction_dataset.py`

The isolated training sidecar consumes the reviewed `transcript`, `audio_path`, `instruction`, nested review status, and explicit split directly. Exact propagation through teacher forcing, checkpoints, export, and both runtimes is documented in [Instruction Propagation Across Training and Inference](INSTRUCTION_PROPAGATION.md).

## Record contract

Every record contains:

- `record_id`: stable within the dataset;
- `audio_path`: confined relative audio path;
- `transcript`: exact reviewed spoken text;
- `instruction`: exact reviewed delivery direction;
- `delivery_labels`: normalized controlled vocabulary;
- `split`: `train`, `validation`, or `test`;
- duration, sample rate, and channel count;
- source and review provenance;
- deterministic `record_fingerprint`.

The current normalized delivery labels are:

```text
neutral
urgent
restrained_anger
panic
grief
whisper
sarcasm
```

Common source wording such as `Natural`, `controlled anger`, `whispered`, or `dry sarcasm` normalizes to that vocabulary. Unsupported subjective buckets are rejected rather than silently converted.

## Provenance requirements

A record binds:

- source kind: `synthetic` or `existing_recordings`;
- stable project, character, and clip IDs;
- exact audio SHA-256;
- transcript SHA-256;
- instruction SHA-256;
- source-manifest SHA-256;
- reviewed-source fingerprint;
- license scope: `owned`, `permissive`, or `synthetic`;
- explicit same-speaker assertion.

Transcript and instruction hashes are recomputed during validation. A later wording change invalidates the record. Synthetic records must use the synthetic license scope; owned recordings cannot claim it.

## Review requirements

A training manifest accepts only records with an approved review that explicitly confirms:

- exact transcript;
- retained speaker identity;
- verified delivery label;
- approved audio quality;
- reviewer identity and UTC review time.

A pending or rejected clip cannot enter a training manifest. A technical pipeline cannot infer or manufacture these decisions.

## Split and leakage rules

One manifest belongs to one stable project, one character, and one source kind. It cannot mix synthetic samples with owned recordings.

Training and validation records are both required. Test records are optional. Split policy must explicitly group by audio SHA-256. Duplicate audio is rejected, and the same audio bytes cannot appear in more than one split.

## Manifest

`build_instruction_dataset_manifest()` produces:

- contract and schema version;
- approved dataset ID and identity;
- ordered normalized records;
- record and manifest fingerprints;
- split membership and split fingerprints;
- normalized delivery-label counts;
- exact field mapping for audio, transcript, instruction, and labels;
- pinned base model key, repository, and immutable revision;
- `review_required: true`;
- `production_assignment_supported: false`.

Record ordering does not change the manifest fingerprint. Content changes do.

## Checkpoint contract

`build_instruction_checkpoint_contract()` binds every checkpoint to:

- dataset manifest fingerprint;
- record-set fingerprint;
- pinned base model and revision;
- instruction field name;
- training kind: `sft` or `lora`;
- explicit hyperparameters;
- checkpoint ID, step, time, and optional parent checkpoint;
- deterministic checkpoint fingerprint;
- no production-assignment claim.

A checkpoint cannot be reused with another dataset merely because the directory name or model family looks compatible.

## Training receipt

`build_instruction_training_receipt()` binds a technical run to the exact dataset and checkpoint fingerprints. It records run status, timing, metrics, and an optional output-artifact fingerprint.

Every receipt remains:

```json
{
  "manual_audio_review_required": true,
  "manual_audio_review_status": "pending",
  "production_assignment_supported": false
}
```

Loss reduction, completed steps, or an exported artifact cannot rewrite those fields. Human listening and explicit assignment remain separate later gates.

## Failure behavior

Validation fails closed for:

- unsafe or absolute audio paths;
- unsupported audio extensions;
- unknown delivery labels;
- transcript or instruction hash mismatch;
- invalid source/license combinations;
- missing same-speaker assertion;
- incomplete review assertions;
- mixed character, project, or source identities;
- duplicate record IDs or audio;
- cross-split audio leakage;
- missing train or validation split;
- tampered manifest, checkpoint, or receipt fingerprints;
- technical claims of completed listening or production support.

## Verification

```bash
PYTHONPATH=app ./app/env/bin/python -m unittest -v \
  tests.test_instruction_dataset
```

The tests include deterministic round trips and adversarial path, provenance, review, split-leakage, identity-mixing, model-binding, checkpoint, listening-claim, and production-claim cases.
