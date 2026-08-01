# Immutable Audio Takes and Renditions

Alexandria treats every successful production-audio result as an immutable
rendition. Regeneration creates a new raw **Take**. It never overwrites or
deletes the previous valid Take. An approved processing or mastering operation
creates a child rendition linked to its source Take; it never rewrites the raw
source.

## Registry and storage

`app/audio_takes.py` owns the project-local versioned registry:

```text
audio_takes.json
voicelines/takes/<chunk-key>/<take-id>.mp3|wav
audio_take_history/<operation-id>/
```

`audio_path` remains the compatibility pointer used by playback and Export, but
the registry is the authority for ordering, current selection, Keep, lineage,
review state, deletion eligibility, cleanup, and rollback. Produce lists Takes
newest first.

Older projects are inspected without mutation. Existing current or stale audio
appears as a deterministic legacy Take in the read-only registry view. The
first generation or explicit Take mutation materializes the registry. Merely
opening Produce does not rewrite project data.

## Self-describing records

Every raw Take records:

- stable Take and chunk identity;
- authored text, speaker, direction, and text fingerprint;
- resolved production Voice and Voice configuration evidence;
- engine, provider, model, runtime, route, prompt, settings, and seed evidence;
- exact request and chunk dependency identity when generation was request-owned;
- pronunciation and spoken-continuity evidence;
- the complete segment/source-span map and seam receipt;
- original sample count, sample rate, channels, duration, format, size, and byte hash;
- current, Keep, review, listening, and approval state;
- a fingerprint covering the complete Take record.

A child rendition additionally records `source_take_id`, `root_take_id`, the
approved processing operation, reversible settings, and its own complete
artifact manifest. Source lineage remains intact even when the child becomes
current.

## Generation and approved performances

Single, parallel, fast-batch, and Produce-plan generation all install to unique
Take paths. The alternate extension for that same Take may be removed after
validation, but prior Take files are retained. The exact-once request ledger
continues to own generation and terminal state; the Take registry owns the
accepted immutable result.

Reviewed adaptation performances use the same registry. Promotion snapshots
`chunks.json`, `audio_takes.json`, and every new Take artifact so rollback
restores the prior selection, lineage, metadata, and exact bytes.

## Dependency invalidation

Changing Script text, direction, speaker, pronunciation, Voice, alias, route,
or synthesis settings immediately removes the old Take from current production
eligibility. For a persisted Take, invalidation clears `current_take_id` and
keeps the immutable file in place. Legacy pre-registry audio continues through
the content-addressed rollback-backup contract.

The invalidation transaction snapshots both chunk metadata and
`audio_takes.json`. Undo restores the former current selection exactly without
copying or re-encoding the retained Take.

## Produce workflow

The selected-chunk inspector shows current and prior Takes newest first. Each
row exposes:

- **Play**, using the persistent player;
- **Use this take**, only when the recorded text, Voice, pronunciation, route,
  and synthesis dependency fingerprint still matches the current chunk;
- **Keep** or **Unkeep**, protecting the Take and its source ancestors;
- **Delete**, only for one eligible non-current, non-kept, unreferenced
  rendition with no protected lineage dependency.

An incompatible prior Take remains playable and retained, but cannot silently
become current. Selection, deletion, and cleanup are blocked while any
persistent audio-generation request remains active or resumable.

## Cleanup and exact undo

**Clean up old takes** is manual and impact-reviewed. The impact report records
candidate IDs, byte count, policy, protection reasons, registry fingerprint,
and one impact fingerprint. The apply request must present that exact impact
fingerprint.

Cleanup and individual deletion exclude:

- the current Take;
- kept Takes;
- source ancestors of current or kept renditions;
- raw Takes that still have child renditions;
- active generation jobs and request receipts;
- invalidation, promotion, export, rollback, and other project evidence;
- Voice references, source material, and every artifact referenced by project
  JSON or history.

Eligible files move to operation-owned rollback storage rather than being
immediately destroyed. The receipt snapshots the prior registry and chunk
state. Undo validates the post-operation registry fingerprint and backup hashes,
then restores the exact bytes and records. A changed destination is a hard
conflict.

`audio_takes.json` is a current Library reference and `audio_take_history/` is
history evidence. Generic Library cleanup therefore cannot misclassify them as
unowned files.

## Boundary retained for B16-T06

B16-T05 provides immutable lineage and transactional user operations. It does
not claim to reconcile every possible process death during the small cross-file
interval between installing a Take, updating `chunks.json`, updating
`audio_takes.json`, and completing the exact-once request record. B16-T06 owns
startup reconciliation of those orphaned or half-committed artifacts.
