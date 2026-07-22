# LoRA on Apple Silicon

Alexandria now has a technically viable Apple Silicon LoRA architecture and a working end-to-end local path. It deliberately uses two environments:

1. **isolated PyTorch/MPS training** in `app/training_sidecar/env`;
2. **merged 8-bit MLX inference** in the normal Alexandria runtime.

The old shared-runtime LoRA path remains unsupported. Alexandria does not load PEFT adapters or run Qwen3-TTS training inside `app/env`, because the PyTorch Qwen stack and MLX-Audio require incompatible Transformers major versions. Instead, the sidecar trains an adapter, merges it into the pinned official Base checkpoint, exports a standalone MLX model, and installs that validated model for ordinary Alexandria inference.

A real three-epoch MPS run, checkpoint resume, held-out validation, adapter inference, merge, MLX export, installation, direct runtime generation, and live `/api/lora/test` request all pass on the measured M2 Max. Production assignment remains blocked because the quick dataset was unreviewed and only 1.77 minutes long, and the generated samples have not received human listening approval.

## Capability summary

| Boundary | Current result |
| --- | --- |
| LoRA inside shared MLX environment | Unsupported; fail closed |
| Isolated Qwen3-TTS load on MPS | Works from the pinned local snapshot |
| Resumable PEFT LoRA training on MPS | Works across multiple samples and epochs |
| Held-out loss measurement | Works with a deterministic train/validation split |
| Direct PyTorch MPS adapter inference | Works; too slow for production |
| Adapter merge into full Base checkpoint | Works |
| 8-bit MLX conversion | Works |
| Installed merged MLX inference | Works through Alexandria and is faster than real time after load |
| Per-line delivery instruction | Technically validated |
| Production assignment | Blocked pending reviewed data and listening approval |

## Why the environments remain separate

The production Apple Silicon environment uses:

- `mlx-audio==0.4.5`;
- `transformers==5.12.1`;
- MLX inference models.

The isolated training environment uses:

- `qwen-tts==0.1.1`;
- `transformers==4.57.3`;
- `torch==2.7.0` and `torchaudio==2.7.0`;
- `peft==0.18.1`;
- `accelerate==1.12.0`;
- `huggingface-hub==0.36.2`;
- a separate managed SoX 14.4.2 binary.

The sidecar resolves the same immutable Base revision used by merge and export. Alexandria does not downgrade `app/env`, silently enable MPS CPU fallback, or import the PyTorch trainer into the web-server process.

## Current measured M2 Max result

Evidence:

- current result: `benchmarks/results/20260719T213000Z_mps_lora_merged_mlx.json`;
- earlier architecture proof: `benchmarks/results/20260717T040339Z_mps_lora_merged_mlx.json`;
- machine: Apple M2 Max with 96 GB unified memory;
- Base model: `Qwen/Qwen3-TTS-12Hz-1.7B-Base`;
- immutable Base revision: `fd4b254389122332181a7c3db7f27e918eec64e3`.

### Model and target profile

The current pilot used the bounded `attention` target profile:

- `q_proj`, `k_proj`, `v_proj`, and `o_proj`;
- 132 actual Talker modules discovered from the loaded model;
- rank 8;
- alpha 16;
- 3,620,864 trainable parameters;
- 0.189% of Talker parameters.

The broader `attention_mlp` profile remains available for deliberate comparison. Target names are enumerated from Qwen3-TTS itself rather than copied from a text-only Qwen architecture.

### Dataset and split

The quick technical pilot used:

- 32 samples;
- 106.21 seconds, or 1.77 minutes, of audio;
- 26 training samples;
- 6 held-out validation samples;
- deterministic seed `20260719`;
- a 20% validation fraction;
- dataset fingerprint `10e302f6312c922e9b6bfb5977c439d908e194ca01a74f97d6a9067d9c07016f`.

All 32 samples were marked `unreviewed`. This is enough to prove the pipeline but below Alexandria's quality-pilot threshold. It cannot support a production-quality claim.

### Resumable training

The run trained epoch 1, wrote a complete adapter/optimizer/RNG checkpoint, then resumed into a new output directory for epochs 2 and 3. The final run recorded:

- 3 completed epochs;
- 78 sample steps;
- 39 optimizer steps;
- gradient accumulation of 2;
- learning rate `2e-5`;
- MPS float32 with eager attention;
- no implicit `PYTORCH_ENABLE_MPS_FALLBACK`;
- final MPS current allocation 7.23 GiB;
- final MPS driver allocation 8.24 GiB.

| Epoch | Training loss | Held-out loss |
| ---: | ---: | ---: |
| 1 | 4.5158 | 4.4551 |
| 2 | 4.3795 | 4.3270 |
| 3 | 4.2250 | 4.1838 |

Held-out loss improved each epoch. That demonstrates a functioning optimizer, deterministic split, checkpoint resume, and measurable learning. It does not establish perceptual quality.

Each resumable checkpoint stores:

- PEFT adapter weights and configuration;
- completed epoch and global/optimizer steps;
- optimizer state;
- Python, Torch, MPS, or CUDA RNG state as applicable;
- immutable training-contract fingerprint;
- dataset and model revision identity;
- accumulated step and validation metrics.

A checkpoint with different data, model revision, target profile, rank, alpha, learning rate, split, seed, or accumulation settings is rejected as incompatible.

### Direct PyTorch inference

The trained adapter loaded and generated through PyTorch MPS:

- 39.15 seconds generation;
- 4.56 seconds audio;
- RTF 8.59.

That path remains diagnostic only. Production rendering uses the merged MLX artifact.

### Merge and MLX export

The adapter merged into the pinned official Base checkpoint, then exported to an 8-bit MLX package:

- merge total: 10.42 seconds;
- temporary merged checkpoint: approximately 8.41 GB;
- final MLX artifact: 3,116,290,133 bytes;
- quantization: 8-bit, group size 64;
- export and technical validation: 18.76 seconds;
- export fingerprint: `2bf2ccb18112c220e0f02fb846103080e4a934111ea92bafcd545f49a4ea51c4`.

The temporary merged PyTorch checkpoint was deleted after successful export to avoid retaining another 8.4 GB copy. The final package contains the standalone MLX model, speech tokenizer, exact reference audio/transcript, neutral and expressive validation samples, and a hash inventory.

### Export validation

| Validation | RTF | Speaker cosine to reference |
| --- | ---: | ---: |
| Neutral | 0.648 | 0.9911 |
| Expressive | 0.371 | 0.9909 |

The two outputs differ while retaining speaker cosine above the 0.95 technical floor. This validates the instruction channel, speed, and embedding-level identity retention. It does not measure naturalness, accent accuracy, pronunciation, noise, emotional correctness, or long-form drift.

### Installed Alexandria runtime

The export is installed as:

- ID: `narrator_attention_r8_pilot`;
- name: `Narrator Attention R8 Pilot`;
- model path: `lora_models/narrator_attention_r8_pilot/mlx_model`;
- state: experimental and unassigned;
- manual review: pending;
- production assignment: false.

A direct Alexandria runtime measurement produced:

- cold total RTF 0.979, including model load;
- warm RTF 0.354;
- live `/api/lora/test` output: 4.88 seconds, mono, 24 kHz.

`GET /api/lora/models` reports the model with `inference_supported: true`. `GET /api/voice_backend/capabilities` reports standalone exported-MLX LoRA inference separately from unsupported shared-runtime PEFT training and dynamic adapter loading.

## Sidecar workflow

The explicit workflow is:

1. **Setup** the isolated environment and managed SoX prefix.
2. **Environment probe** verifies package versions, MPS, and the exact SoX binary.
3. **Model probe** loads the pinned official 1.7B Base snapshot.
4. **Target probe** enumerates actual Talker modules for `attention` or `attention_mlp`.
5. **Train LoRA** using a deterministic split and checkpoint-compatible contract.
6. **Resume** from a complete epoch checkpoint when needed.
7. **Validate** held-out loss after each epoch.
8. **Diagnostic PyTorch inference** confirms the adapter loads.
9. **Merge adapter** into a complete official Base checkpoint.
10. **Export MLX** to a standalone quantized model.
11. **Validate** fixed neutral and expressive lines for speed and identity.
12. **Install** the hash-verified export as an experimental, unassigned Alexandria model.
13. **Listen and review** before any production assignment.

Sidecar routes:

- `GET /api/training_sidecar/status`
- `GET /api/training_sidecar/jobs/{job_id}`
- `POST /api/training_sidecar/jobs`
- `POST /api/training_sidecar/jobs/{job_id}/execute`
- `POST /api/training_sidecar/import`

Job actions include `train_lora`, `merge_lora`, and `export_mlx`. The validated installer currently lives in the sidecar service and writes the ordinary `lora_models` runtime contract atomically.

## Runtime inference

A validated exported model is referenced in voice configuration with:

- `type: "lora"`;
- `adapter_path`;
- `mlx_model_path`;
- optional MLX sampling settings;
- optional persistent character style.

Alexandria then:

- loads the standalone checkpoint through MLX;
- uses the exact supplied reference audio and transcript as the identity prompt;
- combines the line delivery instruction with persistent character style;
- processes Apple Silicon LoRA lines sequentially so instructions are not lost through mixed batching;
- never imports the PyTorch/Qwen sidecar into `app/env`.

## What remains blocked

The current artifact is usable for preview and technical testing, but not for automatic production casting. The remaining quality gate requires:

- reviewed, clean, same-speaker source audio and exact transcripts;
- at least the planned pilot/quality dataset duration rather than 1.77 minutes;
- attention-only versus attention-plus-MLP comparison;
- rank/alpha and learning-rate comparison where useful;
- fixed unseen-line comparisons against ordinary Qwen Clone and controlled VoxCPM2 Clone;
- identity consistency across multiple lines;
- pronunciation and accent stability;
- emotional/prosodic instruction adherence;
- speech-rate drift and long-form stability;
- checks for noise, metallic artifacts, repetition, truncation, and gibberish;
- explicit human listening approval.

Every training, export, installation, and capability record continues to state `production_assignment_supported: false` until those checks pass.

## Product training and route behavior

The shared runtime still does not train or dynamically load PEFT adapters. `POST /api/lora/train` now coordinates the isolated path instead of invoking the retired `train_lora.py` subprocess in `app/env`.

From **More → Voice Lab → Experimental adapter training**:

1. select a dataset and name the experiment;
2. choose the attention-only or attention+MLP target profile;
3. set epochs, rank, alpha, learning rate, gradient accumulation, and held-out validation fraction;
4. choose **Train, validate, and install**.

The background pipeline then:

- creates a project-confined sidecar training job on MPS;
- records the active stage and completed epoch/held-out loss summaries;
- merges the adapter into the immutable official Base checkpoint;
- exports and validates an 8-bit standalone MLX package;
- atomically installs the hash-verified model under `lora_models/`;
- removes the duplicate temporary export after installation;
- leaves the installed result experimental and unassigned.

The route returns `409 lora_sidecar_unavailable` when the validated isolated trainer is absent. Batch size is fixed at 1 on MPS; gradient accumulation provides the effective larger batch. Any train, merge, export, validation, or installation failure records the failed stage and never registers a partial model.

Validated standalone MLX artifacts then use the normal product routes:

- `GET /api/lora/models` lists installed experimental models;
- `POST /api/lora/test` works for a validated installed MLX artifact;
- `POST /api/lora/preview/{adapter_id}` can use its installed preview/runtime model;
- training and test routes do not assign the model to a production speaker automatically.

## Verification

Focused technical contracts:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app:tests ./app/env/bin/python -m unittest -q \
  tests.test_hf_access \
  tests.test_training_sidecar_resume \
  tests.test_training_sidecar_service \
  tests.test_training_sidecar_audio \
  tests.test_mlx_lora_runtime \
  tests.test_voice_backend_capabilities \
  tests.test_voice_backend_capability_routes \
  tests.test_lora_sidecar_benchmark_contract
```

The current evidence is fully local and uses the immutable cached Base snapshot. Reproduce a quality run only with a reviewed dataset and a new empty output directory. Keep large merged intermediates only until the MLX export is validated.

See [Apple Silicon](APPLE_SILICON.md), [Voice Training](VOICE_TRAINING.md), [Voice Types](VOICE_TYPES.md), and [Benchmarking](BENCHMARKING.md).
