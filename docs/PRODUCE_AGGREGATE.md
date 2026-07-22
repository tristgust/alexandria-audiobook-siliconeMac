# Produce Aggregate and Generation Planning

Produce is Alexandria's project-scoped audio generation and quality-control stage. It does not create a second synthesis system. The aggregate inspects authoritative chunk, Voice, synthesis, audio-file, review, and queue state, then dispatches validated plans through the existing `ProjectManager` batch generation path.

Implemented in:

- `app/produce_aggregate.py` — pure row classification, selected-chunk inspector, filtering/search, binding/hash verification, and generation planning;
- `app/project.py` — authoritative individual, parallel, and batch synthesis plus atomic audio installation;
- `app/audio_artifacts.py` — confined audio paths, binding fingerprints, hashes, strict final decode validation, atomic replacement, and operation backups;
- `app/app.py` — Produce status/plan/execute/retry/cancel routes and bounded queue telemetry;
- `app/project_flow.py` — conservative Produce aggregate-to-project-flow adapter.

## Produce row contract

Every non-empty current chunk receives one stable current-project identifier:

```text
chunk:<chunk.id>
```

The row includes:

- current index and source chunk ID;
- Script speaker;
- mapped stable character ID and display name;
- text excerpt and full text;
- delivery direction;
- pause;
- recorded duration;
- Voice method and resolved configuration key;
- audio/playback metadata;
- exact state and reason;
- blockers and native destinations;
- individual Generate or Regenerate action.

Raw filesystem paths remain in technical details or confined relative audio metadata. Canonical playback URLs are exposed only for current, listening-required, or review-required audio. Stale audio is never presented as current production playback.

## States

Produce derives one of these states:

| State | Meaning |
| --- | --- |
| `ready` | The chunk has a valid production Voice and no current audio. |
| `generating` | The authoritative chunk record is actively generating. |
| `needs_listening` | Binding and file verification pass, but explicit listening approval remains incomplete. |
| `needs_review` | Binding and file verification pass, but another operator review flag remains. |
| `current` | Voice binding, synthesis settings, path confinement, file existence, size, duration, format, and SHA-256 all match. |
| `stale` | Audio was invalidated or its binding no longer matches current text, direction, speaker, Voice, or synthesis settings. |
| `failed` | Generation failed or the recorded audio file/path/hash/metadata cannot be trusted. |
| `missing_voice` | Cast does not provide one current valid production Voice for the Script speaker. |

Routine status reads verify binding and file hashes without decoding every audio file. Final Export remains stricter: it decodes and validates every required current audio artifact before assembly.

`audio_validity.json` is part of the read model. A Script replacement can therefore mark rebuilt pending chunks stale even when the new `chunks.json` does not retain the prior audio path.

## Voice and dependency rules

Produce resolves the Script speaker through the current Cast aggregate and the existing alias resolver. Generation is excluded for chunks whose Voice is missing, ambiguous, invalid, unapproved, or otherwise blocked.

Text, delivery direction, speaker, Voice configuration, and synthesis settings are all part of the audio binding fingerprint. Changing any one of them makes previously generated audio stale.

Current audio is preserved until a selected replacement passes validation and atomically replaces it. A failed generation leaves prior bytes available only as stale/recovery data; those bytes are not eligible for final output.

## Default generation plan

The primary plan is:

```text
missing_stale
```

It selects only:

- `ready` chunks;
- `stale` chunks;
- chunks with valid current production Voices.

It preserves:

- current validated audio;
- chunks awaiting listening/review;
- failed chunks until an explicit retry;
- missing-Voice chunks until Cast is repaired.

Other explicit modes:

| Mode | Behavior |
| --- | --- |
| `retry_failed` | Selects only failed chunks whose Voice is valid. |
| `selected` | Regenerates only explicitly selected current chunk IDs. |
| `regenerate_all` | Selects every eligible chunk, including current audio. It is destructive and requires explicit confirmation. |

Each plan is bound to:

- the exact current chunks fingerprint;
- the aggregate dependency fingerprint;
- ordered chunk IDs and indices;
- selected mode.

Execution recomputes the plan and fails closed when either fingerprint changed.

## Queue telemetry

Both existing batch routes now publish the same bounded public queue contract:

- operation ID;
- mode;
- plan and chunks fingerprints;
- total, completed, failed, and canceled counts;
- worker limit;
- first 200 queued stable chunk IDs plus a truncation flag;
- start and finish timestamps;
- latest error;
- capped logs.

Concurrency remains bounded by the configured worker or batch size. Produce does not add a model loader, network downloader, or second TTS worker implementation.

Cancellation requests the existing batch cancellation flag. If Alexandria finds abandoned `generating` chunks while no queue is running, it resets them to:

- `pending` + `stale` when a stale prior path exists;
- `pending` + `pending` otherwise.

The same coherent reset occurs during application startup.

## Routes

### Read Produce

```http
GET /api/produce
```

Optional query parameters:

```text
selected_chunk_id=chunk:42
filter=all|needs_generation|needs_review|current|failed|missing_voice
search=<text>
```

### Read one selected chunk

```http
GET /api/produce/chunks/{chunk_id}
```

Both `42` and `chunk:42` resolve to `chunk:42`.

### Build a plan

```http
POST /api/produce/plan
```

```json
{
  "mode": "missing_stale",
  "selected_chunk_ids": []
}
```

This route is read-only and does not load a TTS model, connect to an LLM, download a model, or mutate project files.

### Execute a reviewed plan

```http
POST /api/produce/generate
```

```json
{
  "mode": "missing_stale",
  "selected_chunk_ids": [],
  "plan_fingerprint": "...",
  "chunks_fingerprint": "...",
  "confirm_regenerate_all": false
}
```

`confirm_regenerate_all` must be `true` for `regenerate_all`.

### Retry failed audio

```http
POST /api/produce/retry-failed
```

The body uses the same execute contract and must carry `mode: "retry_failed"`.

### Cancel

```http
POST /api/produce/cancel
```

## Live project observation

The read-only 2026-07-20 probe inspected 5,275 current chunks in approximately 5.5 seconds without decoding audio or changing project files. It reported:

- 2,735 ready chunks;
- 5 stale chunks with valid Voices;
- 2,535 missing-Voice chunks;
- 0 current, failed, generating, or review-pending chunks.

The default plan selected the 2,740 ready/stale chunks and excluded every missing-Voice chunk. All protected project hashes remained unchanged. No generation was started.
