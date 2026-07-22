# Projects and Project Home Backend Contract

Alexandria uses **Projects** as the normal application entry point. Projects is not a numbered production stage. The numbered flow remains **Script → Cast → Produce → Export**.

This document describes the Project Home catalog and managed-runtime contract implemented by `app/project_catalog.py` and `app/app.py`. Project selection now activates the selected runtime root inside the running Alexandria process; it no longer requires a launcher restart.

## Current implementation status

Implemented:

- read-only project inventory;
- virtual compatibility entry for the current legacy checkout;
- stable project IDs and visible names;
- selected versus currently active project state;
- transactional managed-project creation;
- Local, ChatGPT Task Bundle, and imported-Script creation methods;
- source-language, output-language, and preset metadata;
- EPUB validation and spine-order text preparation;
- imported Alexandria Script validation without premature authoritative application;
- safe managed-project duplication;
- compatibility duplication of the current legacy project without copying application code, environments, caches, or model files;
- archive/unarchive state;
- guarded delete-impact reporting;
- recoverable deletion by atomic move to Alexandria application Trash;
- catalog and project-manifest fingerprint conflict checks;
- last-selected-project persistence;
- unavailable versus invalid project distinction;
- read-only listing that creates no catalog or data directory;
- raw path confinement to `technical_details`.

Managed runtime activation:

- creating a project publishes the managed project transaction, then activates its runtime root immediately;
- opening a project switches every project-scoped path, manager, static asset mount, and in-memory process state inside one guarded runtime transaction;
- project switching is rejected while a project-scoped operation is running;
- a failed runtime commit restores the previous globals, static mounts, manager, process state, and catalog selection;
- startup reactivates the valid last-selected managed project, or remains on the legacy checkout if recovery cannot complete;
- Project Home routes to the selected project stage only after the runtime reports `activation_state: current`.

No API response claims a managed project is active before the runtime actually changes.

## Storage model

The application-data root is resolved in this order:

1. `ALEXANDRIA_DATA_ROOT`, when explicitly set;
2. `~/Library/Application Support/Alexandria` on macOS;
3. `~/.alexandria` on other platforms.

The default structure is:

```text
Alexandria/
  projects.json
  .projects.lock
  Projects/
    project-name--short-id/
      alexandria-project.json
      state.json
      sources/
      imports/
      ...project artifacts...
  Trash/
    project-id--timestamp/
      ...recoverable project directory...
```

A catalog read does not create any of these paths. They are created only by an explicit mutation such as create, select, duplicate, archive, or delete.

### Legacy checkout adapter

The existing checkout remains intact. `GET /api/projects` synthesizes one virtual `legacy_checkout` project from the current project-flow summary. It is not moved, rewritten, or silently registered in `projects.json`.

The legacy project can be duplicated into managed storage. That operation copies only known project artifacts and the selected source. It excludes application code, `.git`, Python environments, caches, Hugging Face snapshots, launcher files, and active operation-state files.

## Managed project manifest

Each managed project contains `alexandria-project.json` with:

- `schema_version`;
- stable `project_id`;
- visible `name`;
- creation/update timestamps;
- `archive_state`;
- source metadata and source fingerprint;
- source/output languages;
- generation method and preset;
- runtime storage/activation contract;
- a versioned project-flow snapshot;
- manifest fingerprint.

The catalog stores the same stable ID, name, manifest fingerprint, storage root, source summary, generation summary, archive state, and timestamps. The raw root path is internal catalog data and is surfaced only in API `technical_details`.

## Project inventory

```text
GET /api/projects
```

The response includes:

- `schema_version`;
- `catalog_fingerprint`;
- `current_project_id`;
- `last_selected_project_id`;
- recent current/managed/archived projects;
- `trash_count`;
- storage activation contract.

Each project summary distinguishes:

- `availability_state`: `available`, `unavailable`, or `invalid`;
- `activation_state`: `current`, `available`, `unavailable`, or `invalid`; the route response becomes `current` only after the runtime transition succeeds;
- current versus selected state;
- recommended stage and stage summary;
- blocker count;
- resumable operation;
- compatibility state;
- completion state;
- archive state.

The catalog reader verifies a managed project directory, manifest schema, stable ID, and manifest fingerprint. A missing directory is `unavailable`; a malformed or mismatched manifest is `invalid`.

## New project transaction

```text
POST /api/projects
Content-Type: multipart/form-data
```

Required fields:

- `project_name`;
- `source_file`;
- `source_language`;
- `output_language`;
- `generation_method`;
- `preset`;
- `expected_catalog_fingerprint`.

Generation methods:

- `local`;
- `chatgpt_task_bundle`;
- `import_existing_script`.

Presets:

- `standard`;
- `maximum_fidelity`;
- `faster_draft`;
- `custom`.

Current source support:

- Local and ChatGPT Task Bundle: UTF-8 `.txt` and valid `.epub`;
- Import existing Alexandria Script: non-empty `.json` array whose entries contain string `speaker`, `text`, and `instruct` fields.

PDF is not advertised because the current backend does not provide a validated PDF text-ingestion contract.

Creation order:

1. Reject creation while a project-scoped operation is running.
2. Validate the uploaded source before catalog mutation.
3. Lock the catalog mutation boundary.
4. Reject stale catalog fingerprints and conflicting names.
5. Create a sibling hidden pending directory under `Projects/`.
6. Copy the original source without modifying it.
7. Prepare EPUB text in OPF spine order, or validate/copy the imported Script candidate.
8. Write and re-read `state.json` and `alexandria-project.json`.
9. Atomically rename the pending directory to its final project directory.
10. Atomically update `projects.json`.
11. Activate the new managed runtime root.
12. If activation fails, keep the safely created project available, restore the previous selection, and return a machine-readable activation failure.

A Local or ChatGPT project starts with Script `not_started`. An imported Script candidate starts with Script `review_required`; it is stored under `imports/` and is not silently written as authoritative `annotated_script.json`.

## Select/open contract

```text
POST /api/projects/{project_id}/open
```

The request accepts `expected_catalog_fingerprint`.

For the current project, the response is `activation_state: current`. For another valid project, the service validates and persists the selection, activates the selected project root, and returns `activation_state: current` plus the native destination only after the transition succeeds.

Activation rebinds project-scoped paths, `ProjectManager`, static Voice/audio mounts, stage-log destinations, and transient process state. The previous runtime binding remains intact if preparation or commit fails. If the catalog selection was written before a later activation failure, Alexandria restores the prior selection with the new catalog fingerprint before returning the original activation error.

Archived, unavailable, and invalid projects cannot be opened. Active generation, discovery, preparation, audio, or training work blocks switching with `409 project_activation_operation_running`.

## Duplicate contract

```text
POST /api/projects/{project_id}/duplicate
```

Required JSON:

```json
{
  "name": "Copy name",
  "expected_catalog_fingerprint": "..."
}
```

Duplication:

- creates a new stable project ID;
- uses destination-side pending creation and atomic publication;
- copies authoritative source, Script, Cast, Voice, audio, output, history, and specialist project artifacts from the explicit allowlist;
- copies the selected source into the managed project and rewrites only the duplicate’s `state.json` source path;
- excludes active generation/discovery/visual operation state;
- refuses symbolic links;
- does not copy application code, environments, caches, model snapshots, Git metadata, or launcher files;
- preserves the source project unchanged;
- remains inactive after duplication; opening it uses the managed activation contract above.

## Archive contract

```text
POST /api/projects/{project_id}/archive
```

Required JSON:

```json
{
  "archived": true,
  "expected_catalog_fingerprint": "...",
  "expected_project_fingerprint": "..."
}
```

Archive changes metadata only. It does not move or delete project files. The catalog and manifest are updated with rollback protection. The active project cannot be archived. Archiving the selected inactive project returns selection to the active legacy project.

## Delete-impact and recoverable delete

```text
GET /api/projects/{project_id}/delete-impact
POST /api/projects/{project_id}/delete
```

Impact reports:

- file count and total bytes;
- source files;
- Script/Cast artifacts;
- audio artifacts;
- Voice assets;
- dataset/training assets;
- export outputs;
- history records;
- current catalog and project fingerprints;
- whether archive is still required.

Delete requires:

- the project to be managed, available, and archived;
- exact `confirm_project_id` match;
- current catalog fingerprint;
- current manifest fingerprint;
- explicit dependency acknowledgment;
- project not currently active.

Deletion atomically moves the entire self-contained project under Alexandria `Trash/`, removes it from the active catalog, and records a recoverable Trash entry. It does not permanently purge bytes. If catalog update fails, the directory is moved back to its original location.

## Concurrency and failure behavior

Mutations use a catalog lock plus catalog/manifest fingerprints. Stale views receive machine-readable `409` conflicts rather than overwriting newer catalog or project state.

Manifest changes and catalog changes preserve prior valid state on failure. New-project and duplicate operations remove only their own newly created pending/published directory when publication fails.

Runtime switching uses a separate re-entrant lock and a two-step prepare/commit boundary. Candidate directories and the new `ProjectManager` are prepared before globals change. Commit snapshots the old globals, static mounts, process-state dictionaries, and manager; any exception restores that snapshot. The previous manager engine and controlled-clone approval receipts are released only after a successful commit.

## Verification

Focused service, route, restart, rollback, and real-browser tests:

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_project_catalog \
  tests.test_project_catalog_routes
```

The integrated project architecture slice additionally runs project-flow, generation, audio-artifact, roster transaction, recovery, and model-cache tests.
