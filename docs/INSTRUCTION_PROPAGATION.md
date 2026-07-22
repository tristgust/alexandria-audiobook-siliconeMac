# Instruction Propagation Across Training and Inference

Alexandria uses one explicit instruction-propagation contract for Qwen3-TTS SFT and LoRA experiments. The contract proves where the reviewed delivery instruction enters teacher forcing, checkpoints, exported artifacts, PyTorch inference, and MLX inference. It does not prove that a trained model follows the instruction acoustically, does not replace blinded listening, and does not authorize production Voice assignment.

Implementation:

- `app/instruction_propagation.py`;
- `app/training_sidecar/qwen_training.py`;
- `app/training_sidecar/mlx_export.py`;
- `app/training_sidecar_service.py`;
- `app/mlx_backend.py`;
- `app/tts.py`.

## Modes

Every training run selects one mode:

- `identity_only` — instructions present in the reviewed corpus are ignored for optimization and are not required at inference;
- `per_record` — every optimized record requires one reviewed instruction, and the installed artifact requires an instruction at inference.

Identity-only remains the compatibility default and enables the later controlled experiment to use the same reviewed corpus without conditioning on its instructions. Per-record is opt-in through the LoRA training UI, API request, sidecar service, and `--instruction-mode per_record` runner argument.

A partial or unknown mode fails before training. A per-record dataset with any missing or empty instruction fails before reference decoding, Qwen imports, model loading, or optimizer creation.

## Reviewed dataset bridge

The training sidecar consumes the canonical fields created by the Instruction-Aware Voice Dataset Contract:

- `transcript` → training text;
- `audio_path` → owned audio path;
- `instruction` → delivery instruction;
- nested `review.status` → training review status;
- `split` → explicit train, validation, or test assignment.

The legacy `text`, `audio_filepath`, `audio`, `instruct`, and flat `review_status` names remain compatibility aliases. When two aliases are present, their normalized values must agree. Conflicting transcript, audio, or instruction aliases fail closed rather than selecting one silently.

Reviewed explicit splits override fractional re-splitting. Test records are fingerprinted and reported but never enter optimization or validation loss. If no record declares a split, the existing deterministic seed/fraction split remains available for legacy datasets.

## Normalization and formatter

Instructions are normalized by collapsing surrounding and internal whitespace while preserving wording and punctuation. The normalized text is limited to 4,000 characters.

Both training and inference use exactly:

```text
<|im_start|>user
{normalized instruction}<|im_end|
```

The formatter identity is `qwen_chat_user_v1`. The placement identity is:

```text
instruction_embedding_then_original_icl_prefill
```

No training-specific paraphrase, delivery-label expansion, hidden system prompt, or clone-regression repair is inserted.

## Teacher forcing

For `per_record`, the sidecar tokenizes the formatted instruction through the same Qwen processor used for text conditioning. It projects those token embeddings through the talker text projection and prepends them before the unchanged original ICL prefill.

The original ICL sequence remains byte-equivalent after removing the instruction prefix. Teacher-forcing labels prepend the same number of `-100` ignore positions, so instruction tokens condition the prediction but are not treated as target codec tokens.

Each step record includes:

- normalized instruction SHA-256;
- formatted instruction SHA-256;
- token count;
- token-ID SHA-256;
- prefill instruction-token count;
- placement identity.

## Dataset and resume identity

The dataset fingerprint always includes the selected instruction mode and reviewed split. In `per_record`, it additionally includes each record’s instruction and instruction-token fingerprints. Changing instruction wording, mode, tokenization, or split changes the dataset fingerprint.

The training contract embeds the complete propagation contract. Its fingerprint therefore changes when instruction propagation changes. Resume validation compares the exact training-contract fingerprint and refuses a checkpoint from another mode, corpus, instruction set, tokenizer result, model revision, target profile, or hyperparameter set.

## Artifact chain

The same validated propagation object is copied through:

1. data-preparation metrics;
2. training contract and checkpoint metadata;
3. training metrics and sidecar artifact manifest;
4. PEFT LoRA merge metrics;
5. merged MLX export manifest;
6. installation `training_meta.json`;
7. installed LoRA registry entry;
8. optional saved Voice configuration;
9. backend capability status.

Every object carries a deterministic `propagation_fingerprint`. Installation rejects a training/export mismatch. Capability discovery rejects a registry/export mismatch. Voice saving rejects a malformed or tampered propagation object.

Legacy artifacts without this field normalize to an explicit `identity_only` contract. They are never silently upgraded to per-record instruction conditioning.

## PyTorch inference

The official PyTorch Qwen model already supports native `instruct_ids`. Sidecar adapter inference and the product LoRA runtime use the shared formatter and tokenizer, then pass the resulting IDs through that underlying generation channel.

Request-local instruction IDs are cleared in `finally` after sidecar inference. A blank instruction for a per-record artifact fails before generation. Identity-only artifacts continue to operate without an instruction.

## MLX inference and export

The MLX Qwen ICL patch uses the same shared formatter and the same placement: instruction embedding before the original ICL prefill. Product runtime validation reads the installed propagation contract before calling merged-MLX generation.

MLX export validates the propagation contract copied from merge metrics. Its neutral and expressive technical samples record instruction hashes and the placement identity. Export does not publish the output directory when propagation validation fails.

Technical validation still requires valid audio, identity similarity, distinct instruction-channel output, and bounded performance. Those checks do not establish instruction adherence; manual audio review remains pending.

## Product training control

The experimental LoRA training form exposes:

- Identity only;
- Per-record delivery instruction.

The selected value is validated by the API and forwarded to the isolated sidecar runner. Per-record copy states that every sample must have a reviewed instruction and that inference will require one. No automatic mode inference is performed from the presence of dataset fields.

## Non-claims and safety gates

This contract proves propagation mechanics only. It does not prove:

- lower training loss means better delivery;
- neutral and expressive WAVs are perceptually correct;
- per-record conditioning outperforms identity-only training;
- a technically validated adapter is production-ready;
- a clone-regression trace is training evidence;
- a trained artifact may be assigned automatically.

All SFT and LoRA outputs remain experimental and `production_assignment_supported: false`. Human listening and the bounded identity-only versus conditioned experiments are separate later tasks.

## Verification

The propagation suite is model-free. It uses deterministic fake embeddings and native call signatures to prove:

- normalization and formatter identity;
- missing-instruction preflight before Qwen imports or audio work;
- B18-T01 field and split bridging;
- instruction prefix placement with unchanged original ICL prefill;
- ignored teacher-forcing labels for the instruction prefix;
- PyTorch native `instruct_ids` injection;
- MLX formatter and placement parity;
- dataset and resume fingerprint changes;
- sidecar command propagation;
- training/export/install/registry fingerprint agreement;
- MLX export publication rollback on tampering;
- blank-instruction rejection in PyTorch and MLX runtimes;
- normalized Voice-save and capability read models.

No training run, model download, audio generation, production assignment, migration, deletion, or live project mutation is required for this proof.
