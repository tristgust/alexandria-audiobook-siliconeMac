# Unified Script Lifecycle

Alexandria treats Local generation, ChatGPT Task Bundle results, and imported Alexandria Scripts as three methods for entering one authoritative Script lifecycle.

The lifecycle is implemented in `app/script_lifecycle.py`. It layers explicit validation, acceptance, immutable version receipts, rollback, and the Script-to-Cast handoff over the existing generation/checkpoint and transactional import systems. It does not replace those native systems or weaken their safeguards.

## Product contract

The operator goal is to produce and explicitly accept one exact, correctly attributed audiobook Script.

Supported generation methods:

- `local`
- `chatgpt_task_bundle`
- `import_existing_script`

All three converge on:

- `annotated_script.json` as the current authoritative Script bytes;
- `annotated_script.meta.json` as current generation/import provenance;
- one Script fingerprint;
- current source fingerprint;
- structural validation;
- exact-source and speaker-attribution audit;
- an explicit accepted-version receipt;
- immutable accepted Script/metadata snapshots;
- Script version history;
- exact rollback with audio invalidation;
- character-discovery handoff state bound to the accepted version.

A generated or applied Script is not complete merely because its files exist or its generation process returned success. It remains `review_required` until explicit acceptance succeeds.

## Files

Project-scoped lifecycle files:

```text
script_lifecycle.json
script_versions/
  script_version_<id>/
    annotated_script.json
    annotated_script.meta.json
    receipt.json
script_lifecycle_history/
  script_rollback_<id>/
    operation.json
    audio_backups/
```

`script_lifecycle.json` contains:

- schema version;
- current review state;
- accepted version ID;
- sanitized version summaries;
- Cast-discovery handoff state;
- lifecycle history;
- state fingerprint.

A status read does not create any lifecycle file or directory.

## Status endpoint

```text
GET /api/script_lifecycle/status
```

The response reports:

- `state`;
- one state-derived `primary_action`;
- detected generation method;
- provenance summary;
- current Script/source/metadata/generation/receipt fingerprints;
- current acceptance state and accepted version;
- blockers;
- generation/checkpoint process state;
- Cast-discovery handoff state;
- version summaries;
- lifecycle state fingerprint.

Lifecycle states:

- `not_started`
- `running`
- `resumable`
- `review_required`
- `blocked`
- `accepted`
- `stale`
- `failed`

Primary action resolution:

- no Script → `Generate Script`;
- compatible checkpoint → `Resume generation`;
- current unaccepted Script → `Review Script`;
- stored import candidate without authoritative Script → `Review imported Script`;
- accepted current Script → `Open Cast`.

The route is read-only and makes no model, network, generation, migration, audio, or project-file mutation.

## Explicit acceptance

```text
POST /api/script_lifecycle/accept
```

Request:

```json
{
  "expected_script_fingerprint": "...",
  "expected_metadata_fingerprint": "...",
  "expected_source_fingerprint": "...",
  "expected_state_fingerprint": "..."
}
```

Acceptance requires:

1. No running or resumable generation checkpoint.
2. Current source fingerprint matches the reviewed source.
3. Current Script fingerprint matches the reviewed Script.
4. Current metadata fingerprint matches the reviewed provenance.
5. Script metadata does not identify a different source.
6. Script is a non-empty array of entries with string `speaker`, `text`, and `instruct` fields.
7. Every entry has a non-empty speaker and spoken text.
8. The existing Script audit passes exact-source fidelity and blocking speaker-attribution checks.
9. A matching prior receipt, when reused idempotently, still has valid immutable snapshot hashes.

Successful acceptance:

- detects `local`, `chatgpt_task_bundle`, or `import_existing_script` from native metadata;
- records `verified_at_acceptance` without erasing original provenance distinctions;
- writes exact immutable Script and metadata snapshots into a destination-side pending version directory;
- validates staged hashes and receipt fingerprint;
- atomically publishes the version directory;
- atomically records the accepted version in `script_lifecycle.json`;
- sets Cast discovery handoff to `pending`;
- then invokes the post-acceptance handoff helper.

Cast discovery cannot launch before the accepted receipt is committed. A structural regression test parses `app.py` and proves the discovery launcher is called only by the post-acceptance handoff helper.

Acceptance is idempotent for the same source, Script, and metadata fingerprints. Reaccepting the same valid version does not create a duplicate version or reset a completed handoff.

## Rejection

```text
POST /api/script_lifecycle/reject
```

Rejection requires the reviewed Script fingerprint, lifecycle fingerprint, and a concise reason. It:

- removes current accepted authority;
- marks the review rejected;
- makes Cast discovery ineligible;
- preserves current Script bytes;
- preserves current metadata;
- preserves all immutable accepted versions.

The Script returns to `review_required` until corrected and accepted again.

## Generation completion and Cast discovery

Local generation process success no longer starts character discovery. It records that the Script is ready for review.

After acceptance, the handoff service resolves the native roster state:

- compatible approved roster → `not_required`;
- discovery already running → `running`;
- resumable discovery checkpoint → `resumable`;
- otherwise start discovery → `running` or `pending`;
- launch failure → `failed` with an actionable error.

The accepted Script remains accepted if discovery launch fails. The handoff can be retried without rerunning or reaccepting the Script:

```text
POST /api/script_lifecycle/discovery-handoff
```

Every handoff update is bound to the exact accepted version ID and lifecycle fingerprint. A stale client cannot attach discovery state to a newer accepted Script.

## Imported Script candidate acceptance

Existing transactional candidate import remains authoritative for replacement mechanics:

```text
POST /api/script_lifecycle/candidates/{candidate_id}/accept
```

The request carries the existing expected Script, metadata, Voice configuration, and chunks fingerprints plus the reviewed source and lifecycle fingerprints.

The operation:

1. Applies the candidate through the existing `apply_annotated_script_candidate` transaction.
2. Preserves exact prior bytes and stale-audio invalidation through the existing operation record.
3. Validates and accepts the newly current Script through the unified lifecycle.
4. Starts Cast discovery only after acceptance commits.

If lifecycle acceptance fails after candidate application, Alexandria invokes the existing exact `rollback_annotated_script_import` operation with the newly applied fingerprints. If that rollback also fails, the route returns a distinct machine-readable critical error containing both operation failures and the import operation ID.

Existing compatibility candidate/apply routes remain available, but they do not grant accepted lifecycle authority. New operator-facing Script workflows should use the lifecycle candidate-accept route.

## Versions

```text
GET /api/script_lifecycle/versions
```

Each public version includes:

- version ID;
- acceptance timestamp;
- generation method;
- source, Script, metadata, and generation fingerprints;
- original provenance summary;
- acceptance audit summary;
- receipt fingerprint.

Every status read verifies:

- receipt schema and version ID;
- receipt fingerprint;
- exact Script snapshot hash;
- exact metadata snapshot hash;
- agreement between the lifecycle summary and immutable receipt.

A missing or altered snapshot prevents the Script from being reported accepted. Reacceptance also refuses to trust a damaged matching version.

## Rollback

```text
POST /api/script_lifecycle/versions/{version_id}/rollback
```

Rollback requires:

- target accepted version ID;
- current source fingerprint;
- current Script fingerprint;
- lifecycle state fingerprint.

The target version must belong to the current source. Rollback then:

1. Validates immutable target receipt and snapshot hashes.
2. Captures exact pre-operation bytes for Script, metadata, chunks, audio validity, and lifecycle.
3. Moves current production audio into the existing content-addressed operation backup.
4. Restores exact target Script and metadata bytes.
5. Rebuilds chunks as pending with missing audio.
6. Records stale audio validity and exact invalidation records.
7. Sets the target version accepted.
8. Re-queues Cast discovery for that accepted version.
9. Writes an operation record under `script_lifecycle_history/`.

If any write fails, touched files are restored and audio backups are returned to their canonical paths. Existing current audio is never silently considered valid for a rolled-back Script.

## Project-flow integration

`GET /api/project_flow/status` now consumes the lifecycle contract. A verified generated Script without an accepted lifecycle receipt remains `review_required`; the previous compatibility behavior that treated finalized generation as accepted has been removed.

Script completion requires:

- current source;
- valid structure;
- passing fidelity/attribution audit represented by the accepted receipt;
- recorded provenance;
- finalization;
- current immutable accepted-version receipt.

A changed source, Script, metadata, or damaged receipt makes acceptance stale or invalid. Cast and downstream stages remain blocked by the Script stage until the current Script is accepted.

## Managed-project import candidate

Project creation with `import_existing_script` validates the JSON candidate and stores it under the managed project’s `imports/` directory without writing authoritative `annotated_script.json`. The project-flow snapshot starts at Script `review_required`.

Managed-project runtime activation is still pending. Once that compatibility boundary activates the project root, the Script scaffold must register or surface this stored candidate through the unified candidate-review contract; it must never auto-apply it merely because the project was opened.

## Verification

Focused lifecycle service and route suite:

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_script_lifecycle \
  tests.test_script_lifecycle_routes
```

The broader Script architecture suite also covers native checkpoints, generation metadata/status, direct imports, ChatGPT Task Bundles, candidate transactions, project flow, Project Home, audio artifacts, roster transactions, recovery, and model-cache routing.
