# Project Migration

Alexandria migration is a project-data compatibility service. It is separate from Git update, dependency installation, and model download.

## Current stable migration

The current migration performs one automatic schema action:

```json
{
  "llm": {
    "profiles": {}
  }
}
```

It adds `profiles` only when:

- `app/config.json` exists;
- its root is a JSON object;
- `llm` is an existing JSON object;
- `profiles` is absent.

It preserves every other field exactly as a JSON value. If configuration is missing, no file is created. If profiles already exist, migration is idempotent.

## Dry-run status

`GET /api/migration/status` is file-pure. It returns:

- plan fingerprint;
- additive actions;
- blockers and warnings;
- project inventory;
- recognized legacy states;
- whether text rewriting or automatic artifact deletion is planned;
- last migration state when present.

The stable plan reports both destructive flags as false.

## Apply

`POST /api/migration/apply` requires:

```json
{
  "plan_fingerprint": "<current plan fingerprint>",
  "confirm": true
}
```

The service rebuilds the plan under a lock. A stale fingerprint returns a conflict. Blockers or missing confirmation reject the operation before writes.

Writes are transactional. The previous configuration and migration state are snapshotted before application. A simulated or real write failure restores exact previous bytes and removes the partial operation backup.

## Backups and operation history

Each successful migration creates a content-addressed operation record under:

```text
migration_backups/migration_<24 hex>/operation.json
```

Records store project-relative file paths, exact before bytes, and after hashes. Paths are validated to remain under the project root. Absolute paths, traversal, malformed IDs, and tampered records are rejected.

`migration_state.json` records the latest successful migration without replacing operation history.

`GET /api/migration/history` provides the file-pure Maintenance inventory. It returns sanitized applied and rolled-back summaries, rollback availability, action and changed-file counts, and separately reported invalid records. It never returns saved file snapshots, previous-state bytes, or `content_base64`. The collection route is declared before `/api/migration/history/{operation_id}` so `history` cannot be interpreted as an operation ID.

## Rollback

`POST /api/migration/rollback` accepts:

```json
{
  "operation_id": "migration_<24 hex>"
}
```

Before restoring, Alexandria verifies every touched file still matches the operation’s after hash. If a later edit changed a file, rollback returns a conflict rather than overwriting it.

A valid rollback restores exact previous bytes and prior migration state, then records a rollback operation.

## Preserved legacy states

The dry run recognizes and preserves:

- script without metadata;
- missing character roster;
- persona references without visual data;
- saved script bundles;
- dataset-builder projects;
- exported datasets;
- adapters and manifests;
- designed/clone reference audio;
- accent registry entries;
- generated voicelines and audiobook outputs;
- voice-training projects;
- unknown configuration fields.

It does not create missing roster, metadata, persona, dataset, or audio files.

## Blockers

Apply is blocked by invalid data it cannot preserve safely, including:

- non-object configuration root;
- non-object `llm` or `profiles` value;
- non-array script root;
- corrupt persona JSON;
- invalid approved roster or voice-training project.

The dry run remains readable so the user can identify the exact blocker.

## Runtime exclusions

`migration_state.json` and `migration_backups/` are ignored by Git. They are project-specific recovery data and should be included in a project backup when rollback history matters.

## Tests

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_migration \
  tests.test_migration_api \
  tests.test_migration_routes
```
