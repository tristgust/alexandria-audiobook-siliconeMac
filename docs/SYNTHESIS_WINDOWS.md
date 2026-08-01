# Synthesis Windows and Seam Integrity

Alexandria treats long-form internal segmentation as part of the production
audio contract. A backend may receive several bounded synthesis requests for one
canonical chunk, but those requests remain internal. The Script and chunk text
are not split, rewritten, or reordered.

## Authoritative backend declarations

`app/synthesis_windows.py` owns one versioned declaration for each production
backend family. A declaration records:

- the backend and family identifiers;
- maximum characters and, where required, maximum words per internal request;
- the minimum viable retry span;
- split-boundary priority;
- one explicit seam mode and seam duration;
- a declaration fingerprint.

Current declarations cover Qwen custom Voice, supplied-recording clone,
instruction-controlled clone, LoRA, Voice Design, community Qwen Voices,
VoxCPM2 controlled clone, Fish cloud, the responsive router, and generic
external generation. The declarations are published in the Voice-backend
capability response under `synthesis_windows.catalog`.

A declaration change is an audio dependency. Current-audio validation compares
the recorded declaration fingerprint with the declaration in the running code.
An older file therefore becomes stale rather than being silently certified
under a changed window or seam policy.

## Exact source-span planning

The planner assigns every internal segment:

- a stable segment ID and index;
- exact zero-based `source_start` and `source_end` character offsets;
- the exact source slice, including punctuation and whitespace;
- a trimmed generation string;
- source and generation text hashes;
- the same request dependency fingerprint as every sibling segment.

Segments are adjacent and reconstruct the complete synthesis request exactly.
The planner prefers paragraph and sentence boundaries, then the backend’s
approved clause or word fallback, and finally a bounded character split. It
never drops punctuation, paragraph breaks, double spaces, or trailing text.

Empty requests produce no segment plan and cannot be admitted as audio. Short
and exactly aligned requests remain one segment. Adaptive retry splitting keeps
the parent source span: too-short output uses the earliest viable sentence
boundary, falling back to a balanced word split; too-long output uses a balanced
word split. A retry may not produce a one-word fragment on either side.

## Exact expected-set validation

Every planned segment must return exactly once. Assembly fails closed for:

- a missing segment;
- a duplicate segment;
- an unexpected segment;
- invalid, non-finite, empty, or effectively silent audio;
- a segment that fails text-to-duration bounds;
- mismatched sample rates;
- an unsupported seam policy.

Alexandria never joins only the surviving outputs. A partial provider result is
a failed chunk, not a shorter audiobook line.

## Seam policies

Each backend uses one declared policy:

- `silence_gap`: retain all validated segment samples and insert an exact
  zero-valued gap;
- `crossfade`: overlap the declared number of samples with deterministic linear
  fades and subtract that overlap from the joined length;
- `discard_overlap`: discard the declared leading samples from each later
  segment and record the exact count;
- `none`: concatenate complete validated segments without hidden trimming.

The assembler computes the exact expected sample count from validated segment
lengths and the declared seam arithmetic. Any mismatch is deterministically
trimmed or padded at the tail and recorded. The admitted WAV must then contain
the exact declared frame count, sample rate, and one channel before
`os.replace` publishes it.

Fish inline cues are phrase-bound to the complete original request. When
internal segmentation is required, Alexandria preserves the global Fish
direction but bypasses the stale phrase-bound cue plan and records
`internal_segmentation_changed_plan_text`.

## Atomic admission and receipts

Internal segment WAVs use hidden temporary paths. Alexandria validates and
assembles them into one hidden joined WAV, validates that file’s exact frame,
rate, and channel contract, and atomically replaces the request output. Failed
segments, incompatible outputs, or join errors leave no joined output and clean
all internal temporary files.

ProjectManager then uses the existing canonical audio installer. The chunk
receipt records:

- backend declaration and plan fingerprints;
- every segment span, hash, sample rate, and raw/prepared sample count;
- every seam’s left/right IDs, mode, requested and applied samples, and output
  sample range;
- exact pre-admission and final sample counts;
- the joined waveform hash and receipt fingerprint;
- common provider and generation provenance retained across all segments.

The receipt participates in the normal audio binding fingerprint. Text, Voice,
pronunciation, synthesis settings, backend declaration, segment plan, seam
policy, or receipt changes therefore make prior audio non-current.

## Batch behavior

Batch generation uses the same contract. Requests that fit one backend window
remain eligible for the backend’s native batch path, but the completed output is
still validated, rewritten to the exact admitted waveform, and assigned a
one-segment receipt. Over-window batch rows use the same segmented single-request
path and retain their own receipt. One row may fail without causing Alexandria
to mislabel a missing or partial row as complete.

## Invalidation and rollback

Any operation that makes audio non-current clears its synthesis-window and seam
receipt fields. Exact unchanged chunks retained by speaker-management operations
preserve the receipt. Audio invalidation, imported Script replacement, Script
rollback, direct edits, batch failures, and regeneration start all use the same
reset contract.

The normal audio-invalidating transactions continue to preserve exact audio
bytes and JSON state for guarded undo. This task changes internal request and
join integrity; it does not authorize live project regeneration or cleanup.
