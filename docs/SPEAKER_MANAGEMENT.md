# Advanced Identity Operations

Advanced identity operations propagate approved character corrections after the canonical roster exists. Routine canonical/display rename, alias maintenance, and compact exact-line inspection are available directly in the selected Characters inspector. This advanced tool remains for operations that affect multiple characters, line groups, or rollback history.

## When to use it

Use **Tools → Advanced identity operations** when you need to:

- inspect the full line-selection workspace for a character;
- merge two approved identities;
- split selected lines and evidence into a new identity;
- reassign selected script lines or an inclusive line range;
- inspect the exact project changes from a prior operation;
- undo a completed operation when no later edit conflicts.

Use the selected character in **3 Characters** for ordinary rename and alias work. Do not use a production voice alias as a substitute for a canonical identity correction: a voice alias only shares synthesis settings, while identity operations update the project’s identity-bearing artifacts.

## Required project state

Advanced identity operations require:

- an approved `character_roster.json`;
- a valid `annotated_script.json`;
- a current script fingerprint.

The browser submits the fingerprint loaded with the inspector. If the script changed after the page was loaded, the mutation fails with `409 stale_speaker_management` instead of overwriting newer work.

## Operations

### Rename

Rename changes the selected roster entry’s canonical and display name and updates matching script speaker labels. The stable character ID remains unchanged.

The previous canonical name can be preserved as an alias. If both old and new names already have voice configurations, the operation requires an explicit voice-resolution choice.

### Add or remove alias

Adding an alias records it on the approved identity and creates a production voice alias when appropriate.

Removing an alias can optionally remove the corresponding voice alias. An alias that belongs to another character’s canonical identity is rejected.

### Merge

Merge keeps one primary stable identity and folds the secondary identity into it. It combines evidence and aliases without silently discarding conflicts.

When both identities have voice configurations or expressive-voice projects, the operation requires an explicit resolution. Dataset, adapter, reference, and validation state are never copied automatically between identities.

### Split

Split moves explicitly selected script indexes and explicitly selected evidence indexes into a new stable identity. It does not infer a split from visual proximity or copy all evidence by default.

A split must leave supporting evidence with the original identity. Trained-adapter or expressive-project state is not cloned to the new identity.

### Reassign

Reassign changes selected script entries to another approved identity. The request can provide exact indexes or an inclusive start/end range. An optional expected-speaker guard prevents moving lines that changed after selection.

## Propagated state

A transaction updates or explicitly invalidates affected state across:

- `annotated_script.json`;
- `annotated_script.meta.json` when present;
- `voice_config.json`;
- `chunks.json`;
- `character_roster.json`;
- `persona_refs/`;
- visual roster ownership;
- generation checkpoint state;
- `audio_validity.json`;
- expressive voice-training projects.

Unknown fields in supported artifacts are preserved where the contract allows them.

## Generated-audio invalidation

When a speaker change alters a generated chunk:

- the existing audio file is preserved;
- the new chunk becomes `pending`;
- the former path is retained as `stale_audio_path`;
- the operation ID is recorded on the chunk;
- `audio_validity.json` records the invalidation.

This prevents stale audio from being included silently while avoiding destructive deletion. Regenerate invalidated chunks in Editor before building the final audiobook.

## Operation history and undo

Every operation writes a content-addressed record under `speaker_management_history/<operation_id>/`. The record contains:

- operation type and timestamp;
- request payload;
- source and result script fingerprints;
- affected speakers and files;
- changed script indexes;
- audio invalidations;
- exact before-state snapshots and after hashes.

Undo restores the exact previous bytes only when every touched file still matches the operation’s recorded after-state. If a later edit changed one of those files, undo fails with a conflict rather than erasing later work.

Undo itself is recorded as another operation, so the history remains auditable.

## Interface behavior

When opened from Characters, a context banner preserves the stable character ID, display name, actual Script voice label, and a direct **Return to character** action. The advanced workspace remains master/detail:

- the left list shows character identity, line count, and alias count;
- the inspector shows exact Script lines and explicit selection;
- merge, split, and reassignment remain collapsed until needed;
- operation history remains compact;
- fingerprints and raw snapshots stay inside operation detail.

At narrow widths, the character list precedes the inspector and all actions stack without horizontal overflow.

## API

### Read status and lines

```text
GET /api/speaker_management/status
GET /api/speaker_management/status?speaker=THE%20DOCTOR
```

The response includes the current script fingerprint, approved roster entries, line counts, filtered lines, and recent history.

### Apply an operation

```text
POST /api/speaker_management/action
```

Request:

```json
{
  "operation": "rename",
  "expected_script_fingerprint": "<current fingerprint>",
  "payload": {
    "entry_id": "character_...",
    "new_name": "THE TRAVELER",
    "preserve_old_as_alias": true
  }
}
```

Supported operation names are `rename`, `add_alias`, `remove_alias`, `merge`, `split`, and `reassign`.

### Read operation detail

```text
GET /api/speaker_management/history/{operation_id}
```

### Undo

```text
POST /api/speaker_management/undo
```

Request:

```json
{
  "operation_id": "speaker_..."
}
```

## Verification

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_speaker_management \
  tests.test_speaker_management_api \
  tests.test_speaker_management_routes
```

The browser acceptance fixture also performs a real rename, verifies exact line inspection, records a generated-audio invalidation, and restores the original identity through undo.

See [Character Roster](CHARACTER_ROSTER.md) for canonical discovery and approval, and [Voice Types](VOICE_TYPES.md) for production voice aliases.
