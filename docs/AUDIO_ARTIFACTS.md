# Audio Artifact Integrity

Alexandria treats generated speech as a content-bound production artifact. A file is current only when it belongs to the active chunk text, speaker resolution, line instruction, voice configuration, and synthesis configuration.

## Chunk audio fields

Newly generated chunks retain the existing workflow `status` field and add explicit audio integrity fields:

- `audio_state`: `pending`, `generating`, `current`, `stale`, `failed`, or `missing`;
- `audio_path`: the one project-confined canonical production file, present only while current;
- `stale_audio_path`: a prior non-current file retained temporarily for replacement or an operation-level undo boundary;
- `audio_fingerprint`: SHA-256 of the synthesis contract and active chunk/voice inputs;
- `audio_sha256`: SHA-256 of the installed audio bytes;
- `audio_size_bytes`, `audio_duration_ms`, and `audio_format`: validation metadata for the canonical file.

Legacy `status: done` audio without the integrity fields is not silently trusted. It must be regenerated before MP3, Audacity, or M4B assembly.

## Invalidation

Changing chunk text, line instruction, or speaker immediately removes the old file from `audio_path`, moves the path to `stale_audio_path`, clears its hashes, and returns the chunk to pending. The bytes may remain temporarily so a failed generation does not destroy the only prior take, but the Editor assembler no longer treats them as production audio.

Voice or alias changes invalidate current audio through the binding fingerprint even if an older chunk record still says `done`. Final assembly recomputes the expected binding from the current `voice_config.json` and rejects the mismatch.

Speaker-management and annotated-script import now use the same operation-scoped audio contract. When either operation invalidates a current production file, Alexandria moves the exact bytes out of the canonical `voicelines/` path into a confined content-addressed backup owned by that operation. Chunks remain non-current and point only to the backup; `audio_validity.json` records the original canonical path, backup path, byte hash, size, reason, and operation identity.

A rollback validates both the normal stale-safe JSON fingerprints and every audio backup before restoration. Exact bytes return to the original canonical paths only when no newer file exists there. A newer canonical file is a hard rollback conflict. If a multi-file operation fails partway through, Alexandria restores the pre-operation JSON and audio bytes and removes incomplete backup artifacts.

## Atomic regeneration

Single and batch generation use the same installation path:

1. mark the selected chunk non-current before model initialization;
2. generate a non-canonical source WAV;
3. decode it and require positive size and duration;
4. export MP3 to a unique temporary file in `voicelines/`;
5. validate the exported file and fall back to a validated WAV when MP3 export is unavailable or invalid;
6. atomically replace the canonical destination using `os.replace`;
7. update the chunk with the binding fingerprint, byte hash, duration, size, and format;
8. remove the obsolete alternate extension and any replaced stale path only after the new file is installed.

A failed conversion removes its temporary file and leaves the chunk `failed` or `stale`. It does not relabel the old bytes current.

## Final-output gate

MP3, Audacity ZIP, and M4B output use the same strict readiness check. Every non-empty chunk must:

- have workflow status `done`;
- have `audio_state: current`;
- match the recomputed chunk/voice/synthesis fingerprint;
- resolve inside the project root;
- exist and decode with positive duration;
- match its recorded audio SHA-256.

The entire export fails before writing when any chunk is pending, generating, stale, failed, missing, path-unsafe, fingerprint-mismatched, hash-mismatched, or undecodable. The error identifies the blocking chunk states.

Final products are also written atomically:

- the merged MP3 is exported and validated in the destination directory before replacement;
- the Audacity ZIP is built in a temporary archive, checked with `ZipFile.testzip`, and then replaced;
- the M4B is encoded to a temporary `.m4b`, decoded for positive duration, and then replaced.

A failed final export therefore preserves the last successful canonical MP3, ZIP, or M4B rather than partially overwriting it.

## Current implementation boundary

Implemented:

- pure audio binding, validation, confinement, installation, inspection, operation-backup, restoration, and final-readiness services in `app/audio_artifacts.py`;
- Editor single and batch generation integration;
- immediate invalidation for direct chunk edits;
- annotated-script import invalidation with content-addressed operation backups and exact rollback;
- speaker-management invalidation with content-addressed operation backups and exact undo;
- rollback conflict protection when a newer canonical audio file exists;
- transactional recovery of both JSON and audio bytes after partial failures;
- strict final MP3/Audacity/M4B blocking and atomic replacement;
- alias-aware voice binding and obsolete-format cleanup;
- temporary-root tests for replacement, failure preservation, legacy/stale blocking, hash/fingerprint mismatch, batch behavior, operation backup/restore, import rollback, speaker undo, and final-output safety.

Operation audio backups live inside the existing import or speaker-management history directory as `audio/<sha256>.bin`. Identical bytes are stored once per operation even when several original paths reference them. History records keep an original-path-to-backup mapping so restoration remains deterministic.

Still open:

- define and implement bounded retention/cleanup for completed or superseded operation backups without weakening exact undo;
- migrate or reconcile older live invalidation records that predate the content-addressed backup contract;
- consolidate reference-bank, adapter, alias-target, and voice-save invalidation behind the same service;
- expose current/stale/missing/failed audio states and exact regenerate actions in the Editor UI;
- add crash reconciliation for a process interruption between canonical file replacement and chunk-metadata persistence;
- run listening-led and long-form acceptance after actual project audio is regenerated under this contract.

## Verification

Focused tests:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app:tests ./app/env/bin/python -m unittest -q \
  tests.test_audio_artifacts \
  tests.test_project_audio_safety \
  tests.test_external_workflows \
  tests.test_speaker_management \
  tests.test_voice_aliases
```

Complete offline regression:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app:tests ./app/env/bin/python -m unittest discover -s tests -q
```
