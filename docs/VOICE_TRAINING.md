# Voice Training and Expressive Voice Preparation

Alexandria separates **expressive voice preparation** from **adapter training**. Preparing a voice identity, reviewing emotional references, or exporting a dataset does not prove that a training backend is available and does not change the production voice automatically.

## Stable workflow

The production-oriented path is:

1. approve the canonical character roster;
2. open **More voice tools** for a resolved speaking character;
3. define and approve one preparation-only reference/training identity;
4. choose either synthetic VoiceDesign samples or owned/permissive recordings;
5. review identity, delivery, transcript, and audio quality;
6. approve the dataset and select a reference;
7. compare the prepared voice against fixed validation lines;
8. assign production voice state only after explicit user approval.

The current Apple Silicon runtime supports VoiceDesign, Qwen Base Clone, CustomVoice, the accent pipeline, and an opt-in VoxCPM2 controlled supplied-clip Clone through MLX. Ordinary cloning from a supplied user-owned clip remains unchanged. Controlled cloning keeps that supplied clip as the identity anchor while applying each line’s `instruct` to delivery. In-process MLX LoRA training and adapter inference remain unavailable while the isolated identity-preserving training work is measured.

## Reference/training identity and preparation in Characters

Open **Cast** and select the character. **Voice** is the only primary working section. **Appearance**, **Character details**, and **More voice tools** follow as compact disclosures. The preparation identity fields described below stay inside **More voice tools**, so an unapproved project does not duplicate the production Voice description, clone transcript, or persistent identity note. Contextual Voice Lab modes—Voice designer, Audio preparer, Dataset builder, and experimental training—return to the same character. Projects are stored under:

```text
voice_training_projects/<stable character ID>.json
```

The stable character ID comes from the approved roster. Renaming the character does not create a second project. A deterministic resolver links the full roster identity to the actual Script/voice-config label using canonical/display names, alternate names, then unique representative-line evidence; exact string equality is not assumed. While roster discovery, reconciliation, or approval is pending, production assignment and preparation remain blocked in Characters. Existing project files remain preserved but are not presented as current identities until an approved roster owns them.

A project can contain:

- character/source/roster ownership;
- candidacy priority;
- preparation-only reference/training identity and approval state;
- one synthetic or existing-recordings preparation path;
- reviewed source clips;
- approved dataset identity;
- explicit reference selection;
- readiness blockers and warnings;
- optional adapter provenance, validation, and assignment;
- deterministic project fingerprint.

Project mutations submit the current fingerprint. A stale edit is rejected rather than overwriting newer state.

## Reference and training identity

This preparation-only record defines the identity that later generated references or dataset samples must retain. It does not replace or override the selected production Voice configuration. It contains:

- detailed voice description;
- representative reference text;
- draft or approved state;
- approval time and fingerprint.

Approve this identity before creating a synthetic project. Once the synthetic path exists, the preparation identity cannot be edited silently; create a new candidate when that advanced identity itself must change.

## Synthetic VoiceDesign path

The synthetic path uses one root persona and a stable seed where supported. It targets approximately 20–25 reviewed samples with varied delivery while preserving the same identity.

Each sample should retain:

- clip ID;
- style/emotion label;
- exact text;
- generation instruction;
- seed and model provenance;
- audio path and hash;
- review state and notes;
- identity-drift flags.

Reject samples that materially change age, accent, timbre, species, or speaker identity merely to produce a stronger emotion.

## Existing-recordings path

Use only audio you own or have permission to use. The workflow requires an explicit same-speaker declaration.

The project can retain:

- source-file identity and provenance;
- transcription and segmentation;
- exact clip transcript;
- transcript confidence and correction state;
- audio-quality score;
- duplicate and contamination review;
- inclusion decision;
- style label;
- audio path and hash.

Synthetic and existing-recordings paths are mutually exclusive within one project.

## Dataset approval and export

Only reviewed clips can enter an approved dataset:

- synthetic clips require `accepted` review state;
- recording clips require `included` inclusion state;
- one dataset cannot mix synthetic and existing-recording clips.

Dataset approval records a deterministic fingerprint. Export records the dataset directory, `metadata.jsonl`, ZIP path, and export time.

Instruction-conditioned experiments additionally use the versioned [Instruction-Aware Voice Dataset Contract](INSTRUCTION_DATASET.md). That contract binds every reviewed clip to its exact transcript, exact delivery instruction, normalized label, source and license provenance, split, pinned base-model revision, checkpoint fingerprint, and technical run receipt. [Instruction Propagation Across Training and Inference](INSTRUCTION_PROPAGATION.md) defines the separate mechanical proof that the same instruction reaches teacher forcing, resume identity, exported artifacts, and both PyTorch and MLX inference. Neither contract can claim completed listening or production assignment.

The standard export format is documented in [Dataset Builder](DATASET_BUILDER.md).

## Reference selection

A selected reference must belong to the approved dataset. Alexandria records its clip ID, source kind, audio path, audio hash, and selection time.

One supplied reference is sufficient for ordinary Qwen cloning. The opt-in controlled-clone backend can apply per-line delivery instructions directly while preserving the supplied reference audio/text as the identity anchor; it does not require a reference bank or LoRA. Before changing the saved backend, Alexandria requires a generated preview, completed playback, and a short-lived one-time server receipt bound to the speaker plus the exact reference-audio bytes, transcript, identity note, and generation settings. The receipt is consumed on save and any configuration change requires another preview and listen.

The active Phase 22 multi-reference extension adds a reviewed **expressive reference bank** with deterministic per-line matching and neutral fallback. For the owned-recording path, the supplied reference audio, exact transcript, and audio fingerprint are the identity authority. The selected Characters inspector creates the bank from the approved owned reference, shows every required style as a full-width listening row, supports same-speaker owned replacements or controlled experimental variants, and records identity retention, identity drift, emotion, pronunciation, pace, and notes per reference. A fixed comparison then plays the same lines through the reference bank, the single neutral reference, and a direct VoiceDesign comparator, with identity consistency and long-form drift recorded separately. Every reference and the comparison must pass before explicit bank approval; production assignment remains a separate action in the same character’s Production voice section. Audio playback resolves only through the current validated bank and rejects missing or hash-changed files.

## Readiness states

`not_ready` means one or more required preparation steps are incomplete.

Current blockers include:

- base persona not approved;
- reviewed dataset not approved/exported;
- reference sample not selected.

`ready_for_feasibility_review` means the preparation contract is complete. It does **not** mean LoRA is supported or that an adapter can be assigned.

## Adapter training

The Voice training page shows the current backend capability from:

```text
GET /api/voice_backend/capabilities
```

When training or adapter inference is unsupported:

- ordinary and controlled supplied-clip cloning remain available independently of LoRA;
- expressive voice preparation remains available for reviewed datasets and multi-reference work;
- measured MLX inference remains visible;
- dataset upload/list/delete remains available;
- training controls remain collapsed and disabled;
- adapter test, preview, and download actions are not offered;
- existing adapter artifacts remain visible and can be explicitly deleted.

The active experimental plan uses a separate PyTorch environment for official Qwen3-TTS SFT and a PEFT LoRA prototype. It must not downgrade or mutate the production MLX environment. See [LoRA on Apple Silicon](LORA_APPLE_SILICON.md).

## Production assignment

No project action automatically changes `voice_config.json`.

Adapter assignment, when supported, requires:

- approved dataset fingerprint;
- recorded training provenance;
- completed validation;
- validated status;
- explicit user approval;
- assignment path matching the recorded provenance.

A rejected or unvalidated adapter cannot be assigned.

## API

### Project status

```text
GET /api/voice_training/status
GET /api/voice_training/{character_id}
```

### Create a candidate

```text
POST /api/voice_training/{character_id}/create
```

The request includes priority and optional starting persona text.

### Mutate a project

```text
POST /api/voice_training/{character_id}/action
```

The request contains:

```json
{
  "expected_fingerprint": "<current project fingerprint>",
  "action": "approve_persona",
  "payload": {
    "description": "...",
    "ref_text": "..."
  }
}
```

Implemented actions include persona update/approval, synthetic-project creation, synthetic sample review, recording-project creation, recording file/clip review, dataset approval/export, reference selection, readiness refresh, and guarded adapter provenance/validation/assignment.

## Legacy dataset and adapter routes

Dataset-management routes remain available independently of training support:

```text
POST /api/lora/upload_dataset
GET /api/lora/datasets
DELETE /api/lora/datasets/{dataset_id}
```

Adapter artifact listing and explicit deletion remain available:

```text
GET /api/lora/models
DELETE /api/lora/models/{adapter_id}
```

Train/download/test/preview routes fail closed when the capability contract reports unsupported.

## Verification

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_voice_training_projects \
  tests.test_voice_training_actions \
  tests.test_voice_training_api \
  tests.test_voice_training_routes \
  tests.test_voice_backend_capabilities \
  tests.test_voice_backend_capability_routes
```

The browser audit verifies persona approval, project-fingerprint refresh, explicit synthetic-path creation, capability-driven action visibility, and responsive Voice training composition.

See [Voice Types](VOICE_TYPES.md), [Dataset Builder](DATASET_BUILDER.md), and [Apple Silicon](APPLE_SILICON.md).
