# Resumable Script Generation

Alexandria checkpoints completed source chunks so an interrupted run can continue without regenerating accepted work. Resume is strict: a checkpoint is reused only when the source, chunk layout, effective generation settings, approved roster, and audit contract remain compatible.

## Runtime files

- `generation_state.json` — active resumable checkpoint;
- `annotated_script.json` — finalized script array;
- `annotated_script.meta.json` — finalized provenance;
- `state.json` — currently selected input source.

These are runtime/user artifacts and are not committed.

## Checkpoint contents

Schema version 1 records:

- normalized source fingerprint;
- generation fingerprint;
- optional effective generation identity;
- chunk fingerprints and total chunk count;
- auditor contract version;
- ordered completed chunk records;
- exact script entries accepted for each completed chunk;
- source summary.

Writes are atomic. A chunk is added only after its JSON contract and source-fidelity audit pass.

## Generation modes

The status/action layer resolves three safe modes:

- **new** — no checkpoint exists;
- **resume** — a compatible checkpoint contains a proper prefix of completed chunks;
- **finalize** — every chunk is complete but final script or metadata writing needs retry.

A complete checkpoint is never regenerated merely because finalization failed.

## Compatibility checks

Resume compares:

- source fingerprint;
- total chunks and every chunk fingerprint;
- generation fingerprint;
- effective runtime/model identity;
- prompts and sampling configuration;
- approved-roster context and fingerprint when present;
- auditor contract version.

Changing a stage model profile, source text, chunk size, prompt, relevant sampling option, or approved roster intentionally makes the checkpoint incompatible.

The UI explains the mismatch and disables resume. It does not overwrite or reinterpret old progress.

## Failure behavior

If a chunk returns malformed JSON, fails correction, or fails the final fidelity audit:

- the current chunk is not checkpointed;
- earlier completed chunks remain intact;
- `generation_state.json` remains available for later resume;
- no partial finalized script is trusted.

If final output writing fails after all chunks complete, the checkpoint remains in finalization state.

## Explicit discard

Discarding progress removes only `generation_state.json`. It is blocked while generation is running and requires explicit confirmation in the UI.

Discard does not delete:

- an existing finalized script;
- metadata;
- voice configuration;
- roster or persona references;
- chunks or generated audio.

Speaker-management operations archive an active checkpoint inside their operation history before changing speaker identity. They do not silently delete it.

## Status states

The Script-specific model-free status distinguishes:

- absent;
- partial compatible;
- complete compatible;
- incompatible source;
- incompatible generation identity;
- corrupt JSON;
- invalid schema/content;
- active process.

The aggregate `/api/recovery/status` route maps Script plus roster, visual dossiers, Persona, Dataset builder, audio, and experimental training into one strict public state set: `new`, `running`, `resumable`, `finalization_only`, `restart_required`, `complete`, `blocked`, `invalid`, or `unavailable`.

Each aggregate stage returns only its currently valid action. Action requests are revalidated against live state before dispatch, so a stale Resume, Finalize, Restart, or Discard button cannot overwrite newer progress. Script, roster, and visual checkpoint discards remain independent.

Read-only status creates no checkpoint and loads no LLM or TTS model.

## Setup recovery presentation

Setup restores the saved source identity from `state.json` without pretending a browser file input can be repopulated. `Project status` remains a compact collapsed disclosure by default. When expanded, it shows seven flat stage rows, exact next-unit/action wording, and capped stage logs.

Roster discovery owns its own process state and persisted log after Script completes. The completed Script becomes available immediately; the automatic roster pass no longer keeps Script falsely running. Polling follows the log tail only while the operator remains near it, preserves manual scroll position, and stops when Setup is no longer active.

## External Script application and checkpoints

An Alexandria Task Bundle Script result or direct annotated-script import is inspected before any project write. The stored candidate snapshots the current Script fingerprint, checkpoint state, and completed-audio count. Apply rechecks those values and fails closed if Script or checkpoint state changed. Version 1 handoff results remain readable for compatibility, but the current interface never asks the user to enter their internal identifier.

When a checkpoint exists, the operator must choose `keep`, `discard`, or `cancel`:

- `keep` preserves `generation_state.json` and warns that it may be incompatible with the imported Script;
- `discard` removes only the Script checkpoint as part of the same atomic import transaction;
- `cancel` performs no mutation.

Import application backs up every touched file, writes explicit provenance, rebuilds all chunks as pending, and preserves existing audio files while marking them stale. Exact rollback is available only while the post-import files still match the operation record; later edits block rollback rather than being overwritten.

## Finalization

When all chunks are complete, Alexandria builds generation metadata from the same source and generation identity, writes the script atomically, writes metadata, rereads both for exact verification, and only then clears the checkpoint.

If verification differs, finalization raises and preserves the checkpoint.

## Tests

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_generation_state \
  tests.test_generate_script_resume \
  tests.test_generation_actions \
  tests.test_generation_status \
  tests.test_recovery_status \
  tests.test_recovery_status_routes \
  tests.test_recovery_process_separation \
  tests.test_stage_logs \
  tests.test_phase17e_api_behavior \
  tests.test_phase17e_ui_behavior
```

See [Generation Metadata](GENERATION_METADATA.md) for the finalized artifact contract.
