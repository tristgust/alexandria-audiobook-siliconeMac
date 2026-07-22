# Character Roster

The approved character roster is Alexandria’s canonical identity authority. It is built from the whole normalized source, reviewed explicitly, and then supplied to downstream LLM stages. It does not rewrite audiobook wording.

## Artifacts

- `character_roster_state.json` — resumable discovery progress;
- `character_roster.draft.json` — editable discovery result;
- `character_roster.json` — current approved roster;
- `character_roster_history/<revision>/` — exact previous approval, replacement approval, replacement draft, and revision receipt for one reviewed replacement.

All are runtime/user artifacts and are ignored by Git.

## Discovery

Whole-book discovery splits the normalized source into overlapping passages. Each observation must contain:

- exact source quote;
- absolute character offsets;
- category and basis;
- confidence;
- passage/batch ownership.

Observations are checkpointed passage by passage. Reconciliation then assigns every observation to a stable identity or an explicit exclusion. It may propose duplicate candidates but cannot silently merge them.

Stable entry IDs are opaque and derived from immutable evidence identity, not the editable canonical name.

## Entry types and states

Entries distinguish:

- speaking characters;
- named non-speakers;
- creatures/nonhuman speakers;
- unresolved identities;
- unnamed identities;
- duplicate candidates.

Each entry can retain canonical/display names, titles, aliases, nicknames, pronouns, species, relationships, voice clues, sample lines, evidence, uncertainty, and duplicate-risk data.

## Imported Task Bundle reconciliation

A validated `roster_discovery` Task Bundle remains an inspected structured-result candidate until the user reconciles it in Cast roster review. Import does not write discovery state, replace a draft, modify an approved roster, approve identities, or discard the returned observations.

Cast roster review recovers pending candidates by selected-source fingerprint after navigation or application restart. The comparison shows:

- approved/current identities beside every imported observation;
- proposed merges and additions;
- aliases and nicknames;
- explicit exclusions;
- groups;
- unresolved or duplicate-risk identities;
- source-bound evidence and any offset repair diagnostics;
- native semantic-evidence failures that prevent an observation from being treated as resolved.

Every imported observation must receive exactly one `merge`, `add`, `unresolved`, or `exclude` decision. Observations that fail native semantic validation may only remain unresolved or be excluded; their raw returned data remains preserved in the Task Bundle candidate. Applying the complete partition creates a reviewable draft. The imported candidate is marked transferred only after the draft saves successfully, and any existing approved roster remains unchanged.

## Review actions

Draft review supports:

- confirm identity;
- rename canonical/display name;
- add alias;
- reject/remove alias proposal;
- keep two identities separate;
- merge confirmed duplicates;
- mark unresolved;
- exclude a non-character entity.

Every mutation requires the current draft fingerprint and appends immutable review history. A stale browser edit receives a conflict instead of overwriting newer work.

## Approval

Approval is blocked until every duplicate candidate is explicitly merged or kept separate. Unresolved/unnamed entries require explicit acknowledgment.

The approved roster records:

- approved draft fingerprint;
- approval time and summary;
- deterministic roster fingerprint;
- exact source ownership;
- review history.

Ordinary draft review never mutates the approved artifact. When Task Bundle reconciliation creates a reviewed draft against an existing approval, Alexandria permits that newer draft to be edited and then replaced through one explicit bulk action. Replacement requires both the current draft fingerprint and current approved-roster fingerprint. Before the approved path changes, Alexandria writes a versioned revision containing the exact previous approved bytes, the replacement approved bytes, the reviewed replacement draft, and a receipt.

The latest replacement can be undone only while its approved fingerprint is still current and its active replacement draft has not changed. Rollback restores the exact previous approved bytes, removes only that unchanged active replacement draft, marks the revision restored, and leaves Script, voices, Personas, visual dossiers, training projects, chunks, and audio untouched. A stale approved roster or newer draft edit blocks rollback rather than discarding work.

Later production identity operations use the separate **More → Advanced identity operations** transaction system.

## Downstream enforcement

The same deterministic roster context is supplied to:

- Script generation;
- Review;
- Persona generation and advanced discovery;
- visual discovery/reconciliation;
- expressive-voice preparation.

Resolved aliases map only when one normalized label identifies one resolved speaking entry. Ambiguous aliases remain unchanged. Unresolved identities remain separate. Named non-speakers are never promoted to dialogue speakers.

The roster may change only the `speaker` label after Script/Review fidelity audits pass. It never authorizes changes to `text`, punctuation, order, or quantity.

The roster fingerprint enters Script generation identity. Changing the approved roster invalidates an incompatible resume checkpoint instead of silently continuing with different identity rules.

## API

Read/discovery/review routes include:

- `GET /api/character_roster/status`
- `GET /api/character_roster/draft`
- `GET /api/character_roster`
- `POST /api/character_roster/discover`
- `POST /api/character_roster/cancel`
- `POST /api/character_roster/discard-progress`
- `POST /api/character_roster/draft/action`
- `POST /api/character_roster/approve` — first approval or guarded replacement;
- `POST /api/character_roster/rollback` — exact rollback of the current saved replacement;
- `GET /api/character_roster/import-reconciliation`
- `POST /api/character_roster/import-reconciliation/apply`

Status and reconciliation reads are model-free and file-pure.

## Testing

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_character_roster \
  tests.test_character_roster_status \
  tests.test_roster_discovery \
  tests.test_character_roster_actions \
  tests.test_character_roster_actions_api \
  tests.test_roster_import_reconciliation \
  tests.test_roster_context \
  tests.test_roster_pipeline_integration
```

See [Speaker Management](SPEAKER_MANAGEMENT.md) for approved-identity corrections that must propagate across project artifacts.
