# Exact-Once Audio Generation Lifecycle

Alexandria persists every accepted production-audio request independently of
the web request, Python thread, or in-process task object that happens to run
it. The existing audio queue remains the only executor. The lifecycle service
does not introduce a second scheduler.

## Request identity

`app/audio_generation_lifecycle.py` owns the versioned request record. Before a
single, parallel, fast-batch, or Produce-plan generation starts,
`ProjectManager.build_audio_generation_manifest()` records:

- the selected stable chunk IDs and current indices;
- mode, Produce operation mode, reviewed plan and chunks fingerprints;
- the requested generation seed and bounded execution settings;
- each chunk's resolved speaker, Voice and TTS dependencies;
- pronunciation and spoken-continuity request identity;
- the backend synthesis-window declaration and exact segment plan;
- one dependency fingerprint for every chunk and internal segment.

Canonical JSON produces one `request_fingerprint` and deterministic
`audio_request_<prefix>` ID. Submitting an identical non-terminal request is a
duplicate, not another dispatch. A terminal request remains an immutable
historical receipt; a genuinely different seed, dependency, plan, chunk set,
or execution mode creates a different request identity.

Request records and verified segment WAVs live under the project-local ignored
runtime directory:

```text
audio_generation_requests/<request_id>/
  request.json
  segments/<chunk-key>/<segment-id>.wav
```

The record can be inspected without an in-process worker through:

- `GET /api/audio-generation/requests`
- `GET /api/audio-generation/requests/{request_id}`

## Ownership and terminal states

Only one worker may claim a request. Claiming assigns a random owner token and
records the process ID and attempt count. Every chunk start, segment update,
canonical publication, failure, and finalization checks the same owner token.
A worker from an older attempt receives `audio_request_owner_stale` and cannot
write more progress or publish audio.

The request states are:

- `prepared`: accepted but not yet claimed;
- `running`: owned by one current worker;
- `resumable`: startup found an interrupted prepared or running request;
- `cancelling`: cancellation has terminal precedence over remaining work;
- `queued_replacement`: one bounded replacement waiting for its predecessor;
- `succeeded`, `failed`, `cancelled`, or `replaced`: terminal states.

Every terminal request records a terminal reason, exact completed/failed/
cancelled/pending counts, final chunk artifacts, finish time, and a
`terminal_receipt_fingerprint`. A batch cannot become `succeeded` unless every
planned chunk is complete. Partial completion is terminal failure even when
some valid chunks were published.

## Segment progress and restart

The B16-T03 segment plan is copied into request progress before model work.
Each internal segment moves through pending, running, completed, or failed and
records its attempt count, exact dependency fingerprint, byte hash, sample
rate, sample count, artifact path, and backend metadata.

After a process restart, startup reconciliation does not silently run audio.
It removes the dead owner token and changes abandoned `prepared` or `running`
requests to `resumable`. A request that was already cancelling becomes
terminal cancelled or replaced. The operator may submit the identical request
again; only a matching request fingerprint may claim the resumable record.

A completed segment is reused only when:

- its request and segment IDs match;
- its dependency fingerprint still matches the current plan;
- the confined WAV exists;
- its bytes, sample rate, and sample count match the persisted artifact record.

The resumed worker skips that verified segment and generates only missing or
failed segments. Provider scratch WAVs are deleted before new dispatch and are
never treated as restart evidence. This prevents stale buffered output from a
prior attempt entering replacement generation.

## Cancellation, replacement, and disconnects

`POST /api/cancel_audio`, `POST /api/cancel_generation`, and Produce Cancel
write cancellation into the persistent request. The in-memory flag is only a
compatibility projection. Once cancellation is recorded, remaining segment
dispatch stops and a late model result cannot become request-owned evidence.

Canonical publication and the final lifecycle update share one lifecycle lock.
If cancellation or replacement acquires that lock first, the late worker may
not install canonical audio. If publication finishes first, that completed
chunk remains valid while cancellation applies to the remaining expected set.

An explicit `replace_active: true` request admits at most one replacement. An
accepted predecessor that was never claimed is marked `replaced` immediately,
and the replacement may dispatch without waiting for a nonexistent owner. An
owned/running predecessor becomes `cancelling`; its one `queued_replacement`
does not run concurrently and is claimed only after the predecessor has a
terminal receipt. A second pending replacement fails with bounded-backpressure
status.

The generation endpoints also check the client connection before acceptance.
A client disconnected before acceptance produces
`client_disconnected_before_acceptance`, cancels the prepared request, and
schedules no worker. Once a request is accepted, it is intentionally durable
and does not depend on the browser connection remaining open.

## Canonical publication

Generation still uses the existing ProjectManager and TTS engine paths. The
lifecycle adds ownership around them:

1. claim the exact request and create per-chunk contexts;
2. mark one chunk running;
3. generate or reuse every exact internal segment;
4. validate and join the complete segment expected set;
5. recompute the current request and chunk dependency fingerprints;
6. while cancellation is excluded, run the existing canonical audio installer
   and chunk JSON update;
7. persist the canonical artifact into request progress;
8. finalize one request-owned terminal state.

The current request fingerprint must still equal the reviewed request at step
5. A Voice, Script, pronunciation, setting, route, segment-plan, or execution
change rejects publication with `audio_request_dependency_changed`.

## API contract

The existing routes remain valid and now return the persistent request object,
whether a worker was dispatched, and whether the request was deduplicated:

- `POST /api/chunks/{index}/generate`
- `POST /api/generate_batch`
- `POST /api/generate_batch_fast`
- `POST /api/generate_fast_batch`
- `POST /api/produce/generate`
- `POST /api/produce/retry-failed`

`replace_active` is explicit and false by default. Silent queue replacement is
not allowed.

## Boundary retained for B16-T06

B16-T04 prevents a normal late worker from publishing after cancellation and
makes request/segment progress restart-safe. It does not claim to repair every
possible process death during the small cross-file interval after canonical
audio bytes or chunk JSON have changed but before all related lifecycle state
is durable. B16-T06 owns startup reconciliation of those orphaned or
half-committed canonical artifacts.

