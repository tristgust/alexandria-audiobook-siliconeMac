# Issue-Focused Roster Reconciliation

Alexandria reconciles imported character-discovery results and reviewed roster drafts through one issue-focused Cast contract. The default operator view contains only decisions that cannot be applied safely without judgment.

Implemented in:

- `app/roster_import_reconciliation.py` — validates imported observations, separates safe changes from issues, and applies the completed partition through the existing transaction;
- `app/roster_reconciliation.py` — read-only project aggregate for imported issues, current draft issues, approval readiness, revision history, and rollback availability;
- `app/character_roster_actions.py` — authoritative draft mutations, initial approval, approved-roster replacement, revision manifests, and exact rollback;
- `app/app.py` — narrow status, issue-apply, and approval routes.

## Core rule

Safe changes do not become operator issues.

Alexandria may prepare an imported observation automatically only when every relevant condition is true:

- native semantic evidence validation passes;
- every evidence offset already matches exactly;
- no imported evidence is invalid or ambiguous;
- the identity is resolved;
- there is no mistaken-merge risk;
- no other imported observation shares the same normalized name or alias;
- a proposed merge matches exactly one current stable character ID; or
- a proposed addition has no current name or alias match.

These changes are listed under `safe_changes` and are included automatically when the issue partition is applied. They are still written only to a reviewable draft. They never approve or replace the current roster by themselves.

## Operator issues

The issue queue is reserved for:

- ambiguous current-roster matches;
- duplicate candidates;
- unresolved or unnamed identities;
- invalid or semantically rejected evidence;
- repaired evidence offsets;
- imported-label collisions;
- incompatible draft or approved rosters;
- invalid stable-ID relationships;
- stale fingerprints and conflicting concurrent edits.

Each imported issue includes the exact allowed actions. The apply route rejects missing issue decisions, duplicate decisions, unsupported actions, and merge targets outside the displayed current matches.

## Approval

Approval remains separate from reconciliation.

A fully resolved draft uses one bulk `approve_resolved` action. There is no second confirmation.

A draft containing unresolved or unnamed identities can use `approve_with_unresolved`. That action is the single explicit bulk acknowledgment that those displayed identities will remain unresolved in the approved roster.

Approval is blocked while:

- imported reconciliation is still pending;
- any blocking issue remains;
- the draft fingerprint changed;
- the current approved-roster fingerprint changed;
- the selected source changed or cannot validate the roster.

Initial approval writes the first approved roster. Replacement approval preserves the current approved roster and the reviewed replacement draft in a revision directory before atomically replacing the authoritative roster.

## Rollback

Approved-roster replacement creates a revision manifest containing:

- the source fingerprint;
- reviewed draft fingerprint;
- previous approved-roster fingerprint;
- replacement approved-roster fingerprint;
- exact previous roster bytes;
- exact replacement roster bytes;
- exact reviewed replacement-draft bytes.

Rollback is available only while the saved replacement remains the current approved roster and the reviewed draft has not changed. It restores exact previous bytes, removes the consumed replacement draft, and marks the revision restored. Later changes fail closed instead of being overwritten.

## Routes

### Read issue-focused state

```http
GET /api/character_roster/reconciliation
```

Optional query:

```text
candidate_id=<structured-result-candidate-id>
```

The response includes:

- current draft and approved fingerprints;
- safe imported changes;
- issue records and exact destinations;
- approval mode and unresolved-acknowledgment requirement;
- applicable rollback revision;
- summary counts.

This route is read-only. It does not load a model, connect to an LLM, download files, mutate a roster, or transfer an imported result.

### Apply imported issue decisions

```http
POST /api/character_roster/reconciliation/apply
```

Body:

```json
{
  "candidate_id": "structured_...",
  "result_fingerprint": "...",
  "current_kind": "approved",
  "current_fingerprint": "...",
  "decisions": [
    {
      "import_id": "imported_...",
      "action": "unresolved",
      "current_entry_id": null
    }
  ]
}
```

`decisions` contains only the displayed issues. Alexandria combines them with the validated safe decisions and invokes the existing atomic imported-roster transaction. The approved roster remains unchanged.

### Approve the reviewed draft

```http
POST /api/character_roster/reconciliation/approve
```

Fully resolved:

```json
{
  "action": "approve_resolved",
  "draft_fingerprint": "...",
  "expected_approved_fingerprint": "..."
}
```

Preserve displayed unresolved identities:

```json
{
  "action": "approve_with_unresolved",
  "draft_fingerprint": "...",
  "expected_approved_fingerprint": "..."
}
```

`expected_approved_fingerprint` is required for replacement approval and omitted for initial approval.

### Exact rollback

The established rollback route remains authoritative:

```http
POST /api/character_roster/rollback
```

It requires the revision ID and the exact current approved-roster fingerprint.

## Compatibility

The older all-observation routes remain available for compatibility:

- `GET /api/character_roster/import-reconciliation`
- `POST /api/character_roster/import-reconciliation/apply`

New Task Bundle routing and the Cast workflow use the issue-focused contract. Compatibility does not restore safe observations as mandatory operator decisions.
