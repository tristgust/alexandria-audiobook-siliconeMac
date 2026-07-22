# Apple Silicon

Alexandria uses **MLX-Audio** for local TTS on Apple Silicon. It does not run the normal PyTorch Qwen3-TTS inference path and it does not treat basic PyTorch MPS availability as proof that every PyTorch feature is supported.

## Supported hardware

The stable Apple Silicon target is an arm64 Mac. Phase 22 was measured on:

- MacBook Pro `Mac14,6`
- Apple M2 Max
- 96 GB unified memory
- macOS 26.4
- Python 3.10.20
- MLX 0.32.0
- MLX-Audio 0.4.5

Lower-memory Apple Silicon systems may work, but the recorded memory and speed figures below are not guarantees for other machines.

## Installation

The Pinokio installer selects dependencies by platform.

On `darwin/arm64` it:

1. creates `app/env`;
2. removes `qwen-tts` if an older installation left it behind;
3. installs `app/requirements-apple-silicon.txt`;
4. installs the normal macOS PyTorch wheel through `torch.js` for non-MLX utilities and capability checks.

The Apple Silicon requirements intentionally use:

- `mlx-audio==0.4.5`
- `mlx-whisper==0.4.3`
- `psutil==7.2.2`
- `transformers==5.12.1`

They intentionally omit `qwen-tts`. The PyTorch package `qwen-tts==0.1.1` requires Transformers 4.57.3, which conflicts with the Transformers version required by MLX-Audio.

Other platforms continue to use `app/requirements.txt` and `qwen-tts==0.1.1`.

## Runtime selection

`TTSEngine` selects MLX when all of the following are true:

- TTS mode is `local`;
- the operating system is macOS;
- the machine architecture is arm64.

The console prints:

```text
Apple Silicon detected: Alexandria will use MLX-Audio.
```

The MLX backend lazily loads only the required model family:

| Voice path | Model |
| --- | --- |
| CustomVoice | `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` |
| Qwen Base Clone | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit` |
| Controlled supplied-clip Clone | `mlx-community/VoxCPM2-4bit` |
| VoiceDesign | `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit` |
| Exported LoRA/SFT voice | project-local merged 8-bit MLX Qwen checkpoint |

Model snapshots are resolved through the versioned registry in `app/model_registry.py`. Every built-in MLX, Whisper, and PyTorch training model has one repository ID, one immutable 40-character revision, and a required-file contract. Loaders ask the registry for a local snapshot path and then initialize the model with `local_files_only=True`; a model library is not allowed to perform a second implicit download after Alexandria has resolved the snapshot.

Alexandria checks the standard shared cache at `~/.cache/huggingface/hub`, then Pinokio's active `HF_HOME` cache. For a pinned revision it reads the cache layout directly and verifies required files and symlink targets without a Hub request. Ordinary synthesis, transcription, compatibility loading, and training preparation are strictly local-only: a missing snapshot fails before model initialization and directs the operator to **Setup → Local model cache → Download**; an incomplete snapshot directs them to **Repair**. Only those explicit Setup actions may contact Hugging Face, and the result is written to the shared cache. Set `ALEXANDRIA_HF_CACHE` to an absolute cache root to override the shared location deliberately. Existing snapshots in other cache roots are still readable; Alexandria does not delete or migrate them automatically.

Public downloads preserve explicit valid-token support. When an inherited local token is rejected for a public repository, Alexandria retries anonymously rather than disabling authentication globally. The built-in LoRA manifest is also pinned: a valid project-local `builtin_lora/manifest.json` is authoritative during ordinary startup, while a remote refresh happens only through an explicit refresh request. This prevents an hourly background `HEAD`/authentication request merely to rediscover an unchanged manifest.

Audio preparer uses the pinned `mlx-community/whisper-large-v3-turbo` registry entry by default through MLX-Whisper and follows the same shared-cache-first resolution. Project-owned audio normalization uses SoundFile, SoXR, and FFmpeg rather than importing Librosa/SciPy, producing canonical mono audio at the exact backend rate. It creates review-required 24 kHz clips and does not approve or train a dataset automatically.

MLX TTS model loading disables Transformers’ unused optional scikit-learn candidate-generation branch before tokenizer initialization. This matters for VoxCPM2: it needs `AutoTokenizer`, but not scikit-learn or SciPy. Controlled-clone and ordinary clone references are normalized to the exact model input rate before MLX-Audio sees them, so reference conversion cannot fall through to MLX-Audio’s SciPy resampler. The 2026-07-20 live verification loaded the pinned VoxCPM2 snapshot in 1.81 seconds and generated 2.24 seconds of controlled-clone audio in 2.78 seconds (RTF 1.24) from the existing Doctor MP3 while neither scikit-learn nor SciPy PROPACK loaded.

### Registered model identity

`app/model_registry.py` is the runtime authority for built-in model identity. Do not duplicate mutable `main` model strings or revisions in another loader. The registry currently covers:

- MLX Qwen Base Clone, CustomVoice, and VoiceDesign;
- MLX VoxCPM2 controlled cloning;
- MLX Whisper Large V3 Turbo and the lightweight Whisper Base compatibility model;
- official PyTorch Qwen Base, CustomVoice, and VoiceDesign checkpoints used by isolated training or non-MLX compatibility paths.

Training-sidecar manifests, merge metrics, and MLX export manifests record the resolved base-model revision so an adapter cannot be reviewed as though it came from an unspecified moving checkpoint.

The model-cache backend and Setup inventory expose three separate operations:

- `model_registry_status()` returns `cached`, `missing`, or `incomplete` for every pinned model, including revision, cache root, required-file failures, broken symlinks, file count, and installed bytes;
- `download_or_repair_model()` performs the explicit download or forced repair after checking available disk space and preserving a safety margin;
- `resolve_model_path()` provides only a validated complete local snapshot to normal runtime code; missing or incomplete state raises an actionable cache error before any Hub or model-library request.

`GET /api/model_registry/status` adds the shared cache root and current background-operation state. `POST /api/model_registry/action` starts one explicit Download, Repair, or required-model batch. Setup renders model name, purpose, required/optional classification, pinned revision, expected/current location, installed and estimated size, validation failures, progress, and failure recovery. The inventory is read-only until an operator chooses an action; opening Setup never starts a download.

## Measured performance

The general reproducible runner is `benchmarks/run_phase22_benchmarks.py`, with result `benchmarks/results/20260717T014952Z_phase22_apple_silicon.json`. The controlled supplied-clip benchmark is `benchmarks/run_controlled_clone_benchmark.py`, with result `benchmarks/results/20260717T031401Z_voxcpm2_controlled_clone.json`.

| Path | Cold RTF | Warm RTF | Peak process RSS |
| --- | ---: | ---: | ---: |
| VoiceDesign | 2.32 | 0.30 | 3.34 GiB |
| VoiceDesign-generated Qwen Clone | 1.63 | 0.33 | 3.37 GiB |
| Controlled supplied-clip Clone | 4.38 s model load | 0.78–0.85 | 5.65 GiB* |
| CustomVoice | 2.07 | 0.29 | 3.35 GiB |
| French accent pipeline | 3.32 | not separately warmed | 6.26 GiB |
| Mixed-length CustomVoice orchestration | — | 0.29 aggregate | 3.34 GiB |
| MPS LoRA training probe | — | 1.50 s/step | 7.31 GiB MPS allocation |
| Direct PyTorch LoRA inference | — | 7.18 RTF | Diagnostic only |
| Merged 8-bit MLX LoRA | — | 0.47–0.56 RTF | 3.12 GB artifact |

An RTF below 1.0 means generation completed faster than the duration of the output audio. Cold figures include first model load in an isolated process. The controlled-clone memory figure includes VoxCPM2 plus the separate Qwen speaker-evaluation model in the benchmark process. Its neutral and expressive speaker cosine values to the supplied clip were 0.976 and 0.960. The merged LoRA validation retained 0.973–0.976 speaker cosine. Results depend on dataset, training duration, prompt, output length, model cache, thermal state, and machine.

The current MLX `generate_custom_batch` path is a **sequential loop** over multiple items. It is stable and faster than real time in the measured aggregate, but it is not tensor batching and must not be described as such.

## Adapter architecture

LoRA remains unsupported **inside Alexandria’s shared MLX environment**. Shared-runtime PEFT training and dynamic adapter loading therefore stay fail closed.

`POST /api/lora/train` is now an orchestrator for the separate validated path rather than the retired in-process trainer. When the isolated MPS sidecar is available, the visible Voice training control runs training, held-out validation, merge, 8-bit MLX export, and experimental installation as one background pipeline. Test and preview routes work only for hash-validated installed standalone MLX artifacts.

A separate technically validated architecture now exists:

1. load and train the official 1.7B Base model in `app/training_sidecar/env` on MPS;
2. merge the PEFT adapter into a full Base checkpoint;
3. convert and quantize the merged checkpoint to an 8-bit MLX package;
4. run fast production-style inference in `app/env` with the supplied reference identity and per-line instruction prefix.

The isolated sidecar uses its own Transformers/Qwen versions and managed SoX prefix. Direct PyTorch adapter inference works but is too slow; the merged MLX path is the intended inference architecture. Newly trained models are installed as experimental and unassigned. Production support remains withheld pending reviewed data, manual listening, and the bounded quality comparison.

Instruction-controlled supplied-clip cloning also works immediately through the opt-in VoxCPM2 backend without training. The owned-recording multi-reference bank preserves the supplied clip as canonical identity and treats generated styles as reviewed candidates.

See [LoRA on Apple Silicon](LORA_APPLE_SILICON.md) and [Voice Training](VOICE_TRAINING.md).

## Verification

From the repository root:

```bash
node --check install.js
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_apple_silicon_install_contract \
  tests.test_phase22_benchmark_contract \
  tests.test_controlled_clone_benchmark_contract \
  tests.test_lora_sidecar_benchmark_contract \
  tests.test_model_registry \
  tests.test_hf_access \
  tests.test_hf_utils \
  tests.test_model_cache_routing \
  tests.test_voice_backend_capabilities \
  tests.test_voice_backend_capability_routes \
  tests.test_training_sidecar_routes \
  tests.test_mlx_lora_runtime \
  tests.test_alexandria_preparer \
  tests.test_user_test_repairs
```

To rerun the hardware benchmark:

```bash
./app/env/bin/python benchmarks/run_phase22_benchmarks.py

./app/env/bin/python benchmarks/run_lora_sidecar_benchmark.py \
  --data-dir /path/to/reviewed-dataset \
  --work-dir /path/to/empty-workdir \
  --output benchmarks/results/<timestamp>_mps_lora_merged_mlx.json
```

This performs real model loads and audio generation. It is not part of the normal offline test suite.
