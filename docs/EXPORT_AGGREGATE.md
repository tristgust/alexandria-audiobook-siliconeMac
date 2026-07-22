# Export Aggregate and Guarded Build Transaction

Export is Alexandria's project-scoped delivery stage. It combines current Produce dependencies, audiobook metadata, chapter structure, selected output formats, delivery receipts, player readiness, and guarded multi-output build behavior.

Implemented in:

- `app/export_aggregate.py` — pure metadata/chapter/format planning, receipt/output inspection, validation, temporary multi-format build, rollback, and history;
- `app/project.py` — existing MP3, M4B, and Audacity builders, now able to write to a confined caller-supplied project path while preserving their default canonical behavior;
- `app/app.py` — Export status/plan/build/cancel routes and one unified Export process state;
- `app/project_flow.py` — conservative Export aggregate-to-project-flow adapter.

## Supported formats

| Format | Current backend |
| --- | --- |
| MP3 | Supported through the existing current-audio merge path. |
| M4B | Supported through the existing FFmpeg chapter/metadata path. |
| Audacity ZIP | Supported through the existing per-speaker WAV, LOF, and labels path. |
| Chapter-separated files | Recognized but unavailable. The plan reports this explicitly rather than fabricating support. |

The older single-format routes remain available for compatibility. The unified Export build uses the same builders through confined temporary targets.

## Metadata and chapters

A reviewed plan requires:

- title;
- author;
- at least one supported format;
- complete Produce dependencies;
- a usable chapter mode when M4B is selected.

Optional metadata:

- narrator;
- year;
- description;
- current cover art.

Chapter modes:

| Mode | Behavior |
| --- | --- |
| `smart` | Starts chapters at structural Script text such as Chapter, Part, Prologue, or Epilogue. Falls back to per-chunk chapters when no structural headings exist. |
| `per_chunk` | Creates one chapter for every current chunk. |
| `none` | Omits chapters. Valid for MP3 or Audacity, but blocks M4B. |

Chapter timing is derived from current recorded chunk durations plus the configured same-speaker/different-speaker pauses and explicit per-chunk pause overrides.

## Reviewed plan

A plan is bound to:

- the complete Produce fingerprint set;
- normalized metadata;
- selected formats;
- chapter mode and exact chapter rows;
- cover-art hash;
- ordered selected outputs.

The plan contains:

- `dependency_fingerprint`;
- `plan_fingerprint`;
- exact selected formats and filenames;
- chapter records;
- blockers;
- `safe_to_execute`.

The build route recomputes the plan immediately before dispatch. Changed dependencies or plan data fail closed.

## Guarded multi-output transaction

A unified build does not write directly to canonical outputs.

1. Create a confined pending history directory.
2. Build every selected format into its `built/` subdirectory using the existing backend.
3. Validate every selected output:
   - MP3/M4B: decode, duration, size, format, and SHA-256 through the strict audio validator;
   - Audacity: valid ZIP, required LOF and labels members, size, and SHA-256.
4. Check cancellation before commit.
5. Copy every previous canonical selected output into the pending `previous/` directory.
6. Replace selected canonical outputs.
7. Recheck committed hashes.
8. Write `export_build.json` and the immutable history receipt.
9. Publish the pending history directory atomically under `export_build_history/<build_id>/`.

If a builder, validator, commit, hash check, receipt write, or history publication fails:

- every selected previous output is restored exactly;
- outputs that did not previously exist are removed;
- the previous receipt is restored exactly;
- no failed receipt becomes authoritative.

Cancellation before commit preserves every previous successful output and does not create a receipt.

## Receipt

`export_build.json` records:

- schema and terminal status;
- build ID and timestamp;
- dependency and plan fingerprints;
- normalized metadata;
- selected formats;
- chapter mode and exact chapters;
- cover hash;
- per-output filename, SHA-256, size, duration, and build time;
- exact previous-output backup evidence.

Previous-output backups remain inside the final immutable history directory. Receipt paths never point to the temporary `.pending` name.

## Output state

Each supported output is reported as:

| State | Meaning |
| --- | --- |
| `missing` | No canonical output exists. |
| `legacy_unverified` | A file exists but no current Export receipt validates it. |
| `current` | File hash/size and current dependency fingerprint match the receipt. |
| `stale` | The output receipt is valid, but Script/Cast/Produce/metadata/chapter/format/cover dependencies changed. |
| `invalid` | The current file bytes or size no longer match the receipt, or the file cannot be inspected. |

Routine Export status hashes files but does not decode outputs. Build-time validation and the underlying current-audio prerequisite remain strict.

## Routes

### Read Export

```http
GET /api/export
```

This route is read-only. It does not load a TTS model, connect to an LLM, download a model, decode existing outputs, or change project files.

### Build a reviewed plan

```http
POST /api/export/plan
```

```json
{
  "metadata": {
    "title": "Book",
    "author": "Author",
    "narrator": "Narrator",
    "year": "2026",
    "description": "Description"
  },
  "formats": ["mp3", "m4b"],
  "chapter_mode": "smart"
}
```

### Execute the plan

```http
POST /api/export/build
```

Use the same metadata/formats/chapter fields plus:

```json
{
  "plan_fingerprint": "...",
  "dependency_fingerprint": "..."
}
```

The response accepts the build into the existing FastAPI background-task mechanism. The unified Export process records the operation, formats, fingerprints, start/finish times, result, errors, and capped logs.

### Cancel

```http
POST /api/export/cancel
```

Cancellation is cooperative. A currently executing single-format backend may finish its temporary build, but the transaction rechecks cancellation before canonical commit and preserves the prior delivery.

## Live project observation

The read-only 2026-07-20 probe completed in approximately 5.1 seconds, including the 5,275-chunk Produce inspection. Export reported:

- state `blocked`;
- Produce incomplete;
- title missing;
- author missing;
- MP3, M4B, and Audacity outputs all missing;
- no current receipt.

No output, receipt, history, or project artifact was created. All protected hashes remained unchanged.
