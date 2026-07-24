# Alexandria Audiobook

Alexandria converts books and long-form text into multi-speaker audiobooks with source-fidelity auditing, canonical character identity, voice design, resumable generation, editing, and export.

This fork is optimized for:

- Apple Silicon local TTS through MLX-Audio;
- native Ollama structured JSON with `qwen3.5:35b-mlx`;
- exact source wording and attribution preservation;
- whole-book character roster discovery and review;
- per-character persona and optional visual references;
- transactional advanced identity operations with exact undo;
- reviewed per-character expressive-reference and dataset preparation;
- a browser-verified production interface.

## Stable Apple Silicon capability

| Capability | Status |
| --- | --- |
| MLX CustomVoice | Supported |
| MLX Clone (Qwen Base) | Supported; default |
| Controlled supplied-clip Clone (VoxCPM2) | Supported backend; opt-in with preview/listen gate |
| MLX VoiceDesign | Supported |
| Native-reference accent pipeline | Supported; slower and heavier |
| Native Ollama structured output | Supported |
| Resumable Script generation | Supported |
| Character roster and visual dossiers | Supported |
| Pinned shared model cache | Supported; explicit Maintenance Download/Repair only |
| Dataset preparation | Supported |
| LoRA inside shared MLX runtime | **Unsupported**; fail closed |
| Isolated MPS LoRA training | Experimental; technically validated |
| Direct PyTorch adapter inference | Works; too slow for production |
| Merged 8-bit MLX LoRA inference | Experimental; faster than real time |
| LoRA production assignment | Blocked pending quality review |

The training path uses a separate Qwen/PyTorch environment, then merges the adapter and converts the full checkpoint to MLX. The measured one-step probe trained in about 1.5 seconds, while the exported MLX model generated at 0.47–0.56 RTF with speaker cosine above 0.973. This is a technical architecture result, not proof that a one-step voice sounds good enough for an audiobook. See [LoRA on Apple Silicon](docs/LORA_APPLE_SILICON.md).

Built-in speech, transcription, and training models are pinned in one registry and inspected in **Maintenance → Local model cache**. Normal synthesis or preparation never starts a model or adapter download. Missing models fail with an explicit Download path; incomplete snapshots fail with Repair. The Maintenance inventory shows required/optional state, purpose, revision, location, installed/estimated size, progress, and actionable failures.

## Installation with Pinokio

1. Install or open this repository as a Pinokio app.
2. Select **Install**.
3. Start Ollama and make `qwen3.5:35b-mlx` available.
4. Select **Validate Latest Build** to check runtime files, source syntax, unresolved Git conflicts, and the Alexandria port.
5. Select **Start Latest Tested Build**. The same preflight runs automatically before the server starts.
6. Open the captured local Alexandria URL.

While Alexandria is running, **Start Read-only UI Preview** opens the current canonical interface against the live backend while blocking mutation requests. Use the normal build for actual workflow testing.

The installer selects dependencies by platform:

- Apple Silicon uses `app/requirements-apple-silicon.txt`, MLX-Audio 0.4.5, MLX-Whisper 0.4.3, and Transformers 5.12.1. It does **not** install `qwen-tts`.
- Other platforms use `app/requirements.txt` and the PyTorch Qwen3-TTS stack.

See [Apple Silicon](docs/APPLE_SILICON.md) for the exact dependency and runtime contract.

## Manual development start

From the repository root, after dependencies are installed:

```bash
cd app
./env/bin/python app.py
```

The default server binds to `127.0.0.1:4200` unless `ALEXANDRIA_HOST` or `ALEXANDRIA_PORT` overrides it.

## Production workflow

Alexandria’s approved application structure uses **Projects** as the entry point and four numbered production stages:

1. **Script** — select a source, generate locally, use a ChatGPT Task Bundle, or import an Alexandria Script; then validate exact fidelity and attribution, explicitly accept an immutable version receipt, and preserve provenance/rollback history.
2. **Cast** — resolve the canonical character roster and assign one current valid production Voice to every required speaking identity. Persona remains compatible backend Voice data rather than a separate user workflow.
3. **Produce** — generate missing and stale audio without replacing current valid chunks, inspect exact chunk/Voice/hash problems, retry failures explicitly, cancel or resume bounded queues, complete required listening/review, and make every required audio artifact current.
4. **Export** — complete metadata and chapters, review dependency-bound MP3/M4B/Audacity plans, build every selected format in a confined temporary transaction, validate outputs, preserve the previous delivery on failure or cancellation, play the current result, and reveal delivered files.

Outside the numbered flow:

- **Library** inventories reusable designed voices, clone references, owned recordings, expressive banks, datasets, and adapters without duplicating files;
- **Settings** contains global defaults and explicit project/stage overrides;
- **Advanced character operations** retains transactional rename, reassignment, merge, split, history, and exact undo;
- **Voice Lab** combines Voice designer, Audio preparer, Dataset builder, and experimental training with project/character context;
- **Maintenance** contains migration, recovery, diagnostics, logs, model repair, and cache management.

The backend and canonical supporting-surface restructuring is implemented through managed project activation, Project Home, Script lifecycle, Cast, Produce, Export, semantic navigation, Library, Voices, Templates, Settings, More, Help, Maintenance, and Voice Lab integration. Current work is concentrated on audio-safety completion, pronunciation guidance, restart-safe segmented synthesis, generated-take retention, expressive-clone validation, and final release acceptance. See [Library Inventory and Guarded Deletion](docs/LIBRARY.md), [Semantic Navigation and Compatibility Routes](docs/NAVIGATION_ROUTES.md), [Export Aggregate and Guarded Build Transaction](docs/EXPORT_AGGREGATE.md), [Produce Aggregate and Generation Planning](docs/PRODUCE_AGGREGATE.md), [Issue-Focused Roster Reconciliation](docs/ROSTER_RECONCILIATION.md), [Cast Aggregate Read Model](docs/CAST_AGGREGATE.md), [Unified Script Lifecycle](docs/SCRIPT_LIFECYCLE.md), [Projects and Project Home](docs/PROJECTS.md), and [Project Flow Contract](docs/PROJECT_FLOW.md).

## Exact source fidelity

Script generation and Review use deterministic audits after JSON validation.

- Script generation may restructure source text into speaker/narrator entries, but it cannot omit, add, reorder, or paraphrase wording.
- Review may correct segmentation, speaker labels, and TTS instruction, but the combined `text` stream must remain exact.
- A failed final audit preserves the prior checkpoint or original batch instead of saving untrusted output.
- Approved roster canonicalization changes only `speaker`, after fidelity auditing succeeds.
- Source normalization removes Unicode replacement markers produced by failed ebook-image decoding without rewriting the uploaded file. Dialogue segmentation supports straight or curly single and double quotation marks while preserving contractions and possessives.

See [Fidelity Audit](docs/FIDELITY_AUDIT.md).

## Resumable generation and provenance

Each accepted source chunk is atomically checkpointed in `generation_state.json`. Resume requires the same source, chunk layout, effective generation identity, audit contract, and approved roster context.

A complete checkpoint can retry finalization without regenerating chunks. Explicit discard removes only the checkpoint.

Finalized provenance is stored beside the script in `annotated_script.meta.json` while `/api/annotated_script` remains a plain array for compatibility.

- [Resumable Generation](docs/RESUMABLE_GENERATION.md)
- [Generation Metadata](docs/GENERATION_METADATA.md)

## Character identity

Alexandria can discover a whole-book source-backed character roster. Every identity fact retains exact quote/offset evidence.

Draft review shows all characters in one master/detail workspace. Safe unique imported matches and clean additions are prepared automatically in the reviewable draft; only ambiguity, duplicates, repaired or invalid evidence, unresolved identities, incompatible artifacts, and invalid stable-ID relationships enter the operator issue queue. A fully resolved draft uses one bulk approval action. Preserving unresolved identities requires one explicit bulk acknowledgment. For an approved speaker, **Voice** is the first and only primary working section: it contains the selected backend, standard/clone/controlled-clone/designed/adapter/alias configuration, exact clone transcript/audio where relevant, preview/listen controls, save state, and meaningful blockers. Appearance and Character details are compact disclosures. Reference-bank, dataset, training, provenance, and specialist handoffs remain inside **More voice tools**. Reviewed reconciliation drafts can replace an approval through a fingerprint-guarded revision with exact rollback.

- [Character Roster](docs/CHARACTER_ROSTER.md)
- [Issue-Focused Roster Reconciliation](docs/ROSTER_RECONCILIATION.md)
- [Persona and Visual References](docs/PERSONA_AND_VISUAL_REFS.md)
- [Advanced Identity Operations](docs/SPEAKER_MANAGEMENT.md)

## Voice paths

### CustomVoice

Uses a built-in Qwen speaker plus per-line instructions. Apple Silicon uses the MLX CustomVoice model.

### Clone

Uses reference audio plus its exact transcript. The standard Qwen Base clone remains the default. A measured opt-in VoxCPM2 controlled-clone backend keeps the supplied recording as the identity anchor while applying each script line’s `instruct` to delivery. It is not LoRA and does not recreate the speaker with VoiceDesign. Alexandria normalizes clone references to the backend’s exact input rate before model generation and prevents the tokenizer-only MLX path from loading unused scikit-learn/SciPy generation helpers. Saving the opt-in backend requires a generated preview, a completed manual listen, and a short-lived one-time server receipt bound to the speaker, reference-audio bytes, transcript, identity note, and generation settings. Editing that configuration invalidates the receipt.

### VoiceDesign

Generates a voice directly from a description. Supported accent phrases may activate a two-stage native-reference pipeline.

### Alias

Routes one script label to another speaker’s production voice configuration. It is not a separate synthesis backend.

See [Voice Types](docs/VOICE_TYPES.md) and [Accent Pipeline](docs/ACCENT_PIPELINE.md).

## Reference and training identity, expressive references, and datasets

A selected character may hold preparation-only identity metadata for expressive references, dataset generation, or experimental training. It is not a second production-voice configuration and stays hidden inside **More voice tools** until advanced work is opened. Preparation can use:

- synthetic VoiceDesign samples; or
- owned/permissively licensed same-speaker recordings.

Preparation-identity approval, sample/clip review, dataset approval, export, and reference selection are separate explicit actions inside **More voice tools** or a contextual specialist tool. Audio preparer can transcribe an owned recording locally with MLX-Whisper, segment it into 24 kHz clips, filter by transcript confidence and estimated SNR, and write an atomic ZIP containing WAV clips, `metadata.jsonl`, `ref.wav`, `ref_text.txt`, and a review manifest. Its output remains unreviewed until the user confirms transcripts, speaker identity, and clip quality. For an owned-recording bank, the selected supplied clip is the immutable identity source and canonical neutral reference. The selected character can create and review the bank, play hash-verified reference and comparison audio, replace style references with owned clips or controlled experimental variants, record identity/drift/emotion/pronunciation/pace gates, run a fixed three-mode comparison including long-form drift, approve the bank, and then separately assign it to the character’s production voice. VoiceDesign is retained only as an external comparison mode. The isolated sidecar can train and export experimental LoRA voices, but assignment remains blocked until listening and multi-sample/multi-epoch validation pass.

- [Dataset Builder](docs/DATASET_BUILDER.md)
- [Voice Training and Expressive Preparation](docs/VOICE_TRAINING.md)

## Native Ollama

Default LLM settings:

```json
{
  "base_url": "http://localhost:11434/v1",
  "model_name": "qwen3.5:35b-mlx",
  "backend": "auto",
  "context_length": 40960,
  "keep_alive": -1,
  "thinking": false,
  "structured_output": true,
  "corrective_retry": true
}
```

Native mode supplies JSON schemas directly, controls thinking explicitly, supports preload/unload, and records bounded telemetry. Eight guarded stage profiles inherit the global model unless a measured evidence record permits a different model.

See [Native Ollama](docs/NATIVE_OLLAMA.md).

## Measured M2 Max performance

Phase 22 was measured on an Apple M2 Max with 96 GB unified memory.

| Path | Cold RTF | Warm/aggregate RTF | Peak RSS |
| --- | ---: | ---: | ---: |
| VoiceDesign | 2.32 | 0.30 | 3.34 GiB |
| VoiceDesign-generated Qwen Clone | 1.63 | 0.33 | 3.37 GiB |
| Controlled supplied-clip Clone | 4.38 s model load | 0.78–0.85 | 5.65 GiB* |
| CustomVoice | 2.07 | 0.29 | 3.35 GiB |
| Accent pipeline | 3.32 | — | 6.26 GiB |
| Mixed-length CustomVoice | — | 0.29 | 3.34 GiB |
| MPS LoRA training probe | — | 1.50 s/step | 7.31 GiB MPS allocated |
| Direct PyTorch LoRA inference | — | 7.18 RTF | Diagnostic only |
| Merged 8-bit MLX LoRA | — | 0.47–0.56 RTF | 3.12 GB artifact |

The controlled-clone memory figure includes VoxCPM2 synthesis and the separate Qwen speaker-evaluation model resident in the benchmark process. Measured speaker cosine to the supplied clip was 0.976 for neutral delivery and 0.960 for expressive delivery. The merged LoRA validation retained 0.973–0.976 speaker cosine. The mixed-length and Apple Silicon LoRA paths are sequential orchestration, not tensor batching. Results are machine-, dataset-, and prompt-specific; manual listening is still required.

See [Benchmarking](docs/BENCHMARKING.md).

## Backward-compatible migration

Migration is dry-run-first and file-pure until explicitly applied.

Current automatic behavior is limited to adding an empty `llm.profiles` object when a valid legacy LLM configuration lacks one. Existing prompts, custom fields, scripts, metadata, voices, persona refs, roster-less projects, datasets, adapters, registries, and audio are preserved.

Routes:

- `GET /api/migration/status`
- `GET /api/migration/history/{operation_id}`
- `POST /api/migration/apply`
- `POST /api/migration/rollback`

Apply requires the current plan fingerprint plus `confirm: true`. Rollback restores exact bytes and refuses to overwrite later edits.

See [Project Migration](docs/MIGRATION.md) and [Updating the Fork](docs/UPDATING_FORK.md).

## API examples

The default examples below use `http://127.0.0.1:4200`.

### cURL

Check LLM runtime:

```bash
curl -s http://127.0.0.1:4200/api/llm/status
```

List current and managed projects:

```bash
curl -s http://127.0.0.1:4200/api/projects
```

This read is side-effect free. The current checkout appears as a virtual legacy project. Managed project creation uses multipart `POST /api/projects`; selection, duplication, archive, delete-impact, and recoverable deletion use the project-scoped routes documented in [Projects and Project Home](docs/PROJECTS.md).

Check the versioned project-flow summary:

```bash
curl -s http://127.0.0.1:4200/api/project_flow/status
```

Check the aggregate Cast collection and selected-character contract:

```bash
curl -s http://127.0.0.1:4200/api/cast
```

Use `selected_character_id`, `filter`, and `search` query parameters to preserve context while narrowing the one canonical Cast list. Native roster, clone, adapter, alias, and Voice validation remain authoritative and can only make the aggregate stricter. See [Cast Aggregate Read Model](docs/CAST_AGGREGATE.md).

Check the unified Script lifecycle and its one current action:

```bash
curl -s http://127.0.0.1:4200/api/script_lifecycle/status
```

Generated or imported Script bytes remain review-required until `POST /api/script_lifecycle/accept` validates the reviewed source, Script, metadata, and lifecycle fingerprints. Character discovery starts only after that accepted receipt is committed. See [Unified Script Lifecycle](docs/SCRIPT_LIFECYCLE.md).

Check detailed native Script generation/checkpoint state:

```bash
curl -s http://127.0.0.1:4200/api/script_generation/status
```

Check Apple Silicon voice capability:

```bash
curl -s http://127.0.0.1:4200/api/voice_backend/capabilities
```

Dry-run migration:

```bash
curl -s http://127.0.0.1:4200/api/migration/status
```

### Python

```python
from __future__ import annotations

import requests

BASE = "http://127.0.0.1:4200"

projects = requests.get(f"{BASE}/api/projects", timeout=30)
projects.raise_for_status()
print(projects.json()["projects"])

flow = requests.get(f"{BASE}/api/project_flow/status", timeout=30)
flow.raise_for_status()
summary = flow.json()
print(summary["recommended_stage"], summary["safe_next_action"])

script = requests.get(f"{BASE}/api/script_lifecycle/status", timeout=30)
script.raise_for_status()
print(script.json()["state"], script.json()["primary_action"])

cast = requests.get(
    f"{BASE}/api/cast",
    params={"filter": "needs_attention"},
    timeout=30,
)
cast.raise_for_status()
print(cast.json()["summary"], cast.json()["characters"])

runtime = requests.get(f"{BASE}/api/llm/status", timeout=30)
runtime.raise_for_status()
print(runtime.json())

migration = requests.get(f"{BASE}/api/migration/status", timeout=30)
migration.raise_for_status()
plan = migration.json()

if plan["migration_required"] and not plan["migration_blocked"]:
    applied = requests.post(
        f"{BASE}/api/migration/apply",
        json={
            "plan_fingerprint": plan["plan_fingerprint"],
            "confirm": True,
        },
        timeout=30,
    )
    applied.raise_for_status()
    print(applied.json()["operation"]["operation_id"])
```

### JavaScript

```javascript
const base = "http://127.0.0.1:4200";

const capabilities = await fetch(`${base}/api/voice_backend/capabilities`)
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  });

console.log(capabilities.stable_lora_outcome);

const rosterStatus = await fetch(`${base}/api/character_roster/status`)
  .then((response) => response.json());

console.log(rosterStatus);
```

FastAPI also exposes its generated OpenAPI schema at `/openapi.json`.

## Verification

Complete offline regression:

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest discover -s tests
```

Interface acceptance:

```bash
./app/env/bin/python tests/interface_browser_audit.py \
  --repo-root . \
  --output-dir /tmp/alexandria-interface-audit
```

Launcher contract:

```bash
node --check install.js
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_apple_silicon_install_contract
```

## Documentation

- [Boundary 13 Supporting-Surface Acceptance](docs/BOUNDARY13_ACCEPTANCE.md)
- [Library Inventory and Guarded Deletion](docs/LIBRARY.md)
- [Maintenance and Guarded Technical Actions](docs/MAINTENANCE.md)
- [Offline Help Center](docs/HELP_CENTER.md)
- [Semantic Navigation and Compatibility Routes](docs/NAVIGATION_ROUTES.md)
- [Export Aggregate and Guarded Build Transaction](docs/EXPORT_AGGREGATE.md)
- [Produce Aggregate and Generation Planning](docs/PRODUCE_AGGREGATE.md)
- [Issue-Focused Roster Reconciliation](docs/ROSTER_RECONCILIATION.md)
- [Cast Aggregate Read Model](docs/CAST_AGGREGATE.md)
- [Unified Script Lifecycle](docs/SCRIPT_LIFECYCLE.md)
- [Projects and Project Home](docs/PROJECTS.md)
- [Project Flow Contract](docs/PROJECT_FLOW.md)
- [Apple Silicon](docs/APPLE_SILICON.md)
- [Native Ollama](docs/NATIVE_OLLAMA.md)
- [Accent Pipeline](docs/ACCENT_PIPELINE.md)
- [Fidelity Audit](docs/FIDELITY_AUDIT.md)
- [Benchmarking](docs/BENCHMARKING.md)
- [Resumable Generation](docs/RESUMABLE_GENERATION.md)
- [Generation Metadata](docs/GENERATION_METADATA.md)
- [Character Roster](docs/CHARACTER_ROSTER.md)
- [Persona and Visual References](docs/PERSONA_AND_VISUAL_REFS.md)
- [Voice Types](docs/VOICE_TYPES.md)
- [Dataset Builder](docs/DATASET_BUILDER.md)
- [Voice Training](docs/VOICE_TRAINING.md)
- [Instruction Propagation Across Training and Inference](docs/INSTRUCTION_PROPAGATION.md)
- [LoRA on Apple Silicon](docs/LORA_APPLE_SILICON.md)
- [Speaker Management](docs/SPEAKER_MANAGEMENT.md)
- [Project Migration](docs/MIGRATION.md)
- [Updating the Fork](docs/UPDATING_FORK.md)
- [Interface Design](docs/INTERFACE_DESIGN.md)
- [Interface Acceptance](docs/INTERFACE_ACCEPTANCE.md)

## License and upstream

This repository is a fork of Alexandria Audiobook Generator. Preserve upstream licensing and attribution when redistributing. Review the repository license and third-party model licenses before publishing generated voices, datasets, or audiobook material.
