# Dataset Builder

Dataset Builder creates and reviews voice samples before exporting a dataset. It remains useful even when no adapter-training backend is supported.

## Working projects

Projects live under `dataset_builder/<name>/` and contain `state.json` plus generated sample WAV files. The project state stores:

- root voice description;
- optional global seed;
- ordered sample rows;
- per-row emotion/delivery text;
- sample text;
- optional per-row seed;
- generation status and preview URL.

Working projects are user runtime data and are ignored by Git.

## Workflow

1. Create or select a project.
2. Write one root identity description.
3. Add varied sample rows covering useful delivery states.
4. Generate samples individually or as a bounded batch.
5. Listen to every sample.
6. Regenerate, revise, or remove drifted samples.
7. Choose one completed sample as the reference.
8. Export to the training-dataset directory.

Changing row text resets that row to pending. Existing generation state is preserved only when the normalized text remains the same.

## Sample design

A useful expressive dataset should cover natural variation without changing identity. Examples include:

- neutral exposition;
- quiet concern;
- controlled anger;
- urgency;
- warmth;
- dry humor;
- low-volume speech;
- louder projection;
- short and long phrasing.

Do not use 20 near-identical sentences. Do not accept samples that change age, accent, timbre, or speaker identity merely to maximize emotion variety.

## Seeds

- `-1` means random generation.
- A global non-negative seed can make a synthetic set more repeatable.
- Per-row seeds override the global seed.

A fixed seed improves reproducibility but does not guarantee identical output across model, dependency, or hardware changes. Seed and generation provenance should be retained with reviewed samples.

## Export format

A saved dataset contains:

- completed WAV clips;
- one `metadata.jsonl`;
- `ref.wav` copied from the explicitly selected sample;
- `ref_text.txt` containing that sample’s exact transcript.

Each metadata line includes the audio filename, exact text, and `ref_audio: "ref.wav"`.

The export does not train an adapter and does not assign a voice.

## Existing-audio preparation

For owned or permissively licensed recordings, use Audio preparer or the Expressive voices recording path. Audio preparer accepts WAV, MP3, FLAC, OGG, or M4A; transcribes locally with MLX-Whisper on Apple Silicon; uses word timestamps to form reviewable phrases; converts accepted clips to mono 24 kHz PCM WAV; and filters by transcript confidence, estimated signal-to-noise ratio, and duration.

Its atomic ZIP contains:

- accepted `sample_####.wav` clips;
- `metadata.jsonl` with exact transcript, clip boundaries, confidence, SNR, source segment, and `review_status: "unreviewed"`;
- one selected `ref.wav` and `ref_text.txt`;
- `preparation_manifest.json` with source hash, transcription model, thresholds, rejections, and explicit review requirements.

The archive is preparation evidence, not approval. The user must verify the transcript, same-speaker identity, pronunciation, contamination, and clip quality before importing or training.

The reviewed contract can retain:

- recording/file provenance;
- same-speaker declaration;
- clip boundaries;
- transcript and corrections;
- confidence and quality scores;
- duplicate/contamination review;
- accepted/rejected state.

Only accepted clips can enter an approved dataset.

## UI states

The Dataset builder workspace uses:

- a project selector and explicit New project action;
- a dense desktop table for real comparison work;
- labeled stacked sample rows on narrow screens;
- loading, empty, and error states that replace the table rather than masquerading as rows;
- one clear export action after samples are ready;
- generation count and percent outside the progress fill so low completion values remain readable.

## API

- `GET /api/dataset_builder/list`
- `POST /api/dataset_builder/create`
- `POST /api/dataset_builder/update_meta`
- `POST /api/dataset_builder/update_rows`
- `POST /api/dataset_builder/generate_sample`
- `POST /api/dataset_builder/generate_batch`
- `POST /api/dataset_builder/cancel`
- `GET /api/dataset_builder/status/{name}`
- `POST /api/dataset_builder/save`
- `DELETE /api/dataset_builder/{name}`
- `POST /api/preparer/upload`
- `POST /api/preparer/start`
- `POST /api/preparer/batch/start`
- `POST /api/preparer/cancel`
- `POST /api/preparer/batch/cancel`
- `GET /api/preparer/list`
- `GET /api/preparer/download/{filename}`

Legacy LoRA dataset upload/generation/list/delete routes remain available as dataset-management surfaces. On Apple Silicon, their existence does not imply LoRA training support.

## Safety

- Dataset names are sanitized before filesystem use.
- Existing dataset directories are not overwritten.
- Partial export directories are removed on export failure.
- Deletion is explicit.
- Migration does not rewrite or delete working projects or exported datasets.

See [Voice Training](VOICE_TRAINING.md) and [LoRA on Apple Silicon](LORA_APPLE_SILICON.md).
