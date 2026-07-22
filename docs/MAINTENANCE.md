# Alexandria Maintenance

Maintenance is the global, read-only-first surface for recovery, local capability health, dependency inspection, migration history, and guarded technical actions. It is outside the numbered Script → Cast → Produce → Export workflow and does not create a parallel project, artifact, model, or recovery store.

## Authoritative inputs

The canonical Maintenance page composes existing APIs rather than introducing a new aggregate database:

- `GET /api/recovery/status` for the selected source and stage checkpoints;
- `GET /api/model_registry/status` for pinned local-model availability and any current explicit cache operation;
- `GET /api/library` for active-project artifacts, dependencies, validity, and native destinations;
- `GET /api/projects` for project availability, archive state, compatibility, and recoverable Trash count;
- `GET /api/migration/status` for the current file-pure migration dry run;
- `GET /api/migration/history` for sanitized applied and rolled-back operation summaries.

The page uses `Promise.allSettled` so one unavailable diagnostic does not erase healthy evidence from the other services. Opening or refreshing Maintenance performs reads only. It does not load a model, contact the model Hub, migrate a project, delete an artifact, move a project, or start generation.

## Normal presentation

The first view shows:

- overall attention state;
- recovery blocker count;
- required-model cache readiness;
- dependency-bearing Library artifact count;
- recoverable project Trash count;
- saved source and stage recovery rows;
- one row per pinned model with required or optional status;
- dependency-bearing or guarded Library artifacts;
- current and managed projects;
- recovery checkpoints and migration-operation history;
- the current migration dry-run result.

Normal rows do not show absolute paths, raw fingerprints, cache roots, snapshot paths, configuration paths, backup snapshots, or base64 file content. Model purpose, runtime, installed or estimated size, required/optional state, missing required files, and actionable state remain visible without exposing implementation paths.

## Native destinations

Maintenance links evidence to the authoritative product destination instead of duplicating work:

- source and Script evidence → Script;
- roster, visual, and Persona evidence → Cast;
- audio evidence → Produce;
- dataset and training evidence → the relevant Voice Lab mode;
- Library artifacts → each artifact’s existing native route;
- projects → Project Home.

Project, character, source, mode, and the exact Maintenance return route are carried in semantic route state. Back restores the same Maintenance route and rendered evidence.

## Guarded impact review

Potentially mutating controls are secondary review actions. No destructive button is visible on the normal page. Selecting **Review impact** opens a native dialog and loads the authoritative current impact from the existing route.

Library deletion delegates to:

- `POST /api/library/artifacts/{artifact_id}/delete-impact`;
- `DELETE /api/library/artifacts/{artifact_id}`.

The delete request must include the current inventory fingerprint, current artifact fingerprint, and the exact artifact name. Unsupported artifacts, dependency blockers, stale fingerprints, unsafe paths, and running audio or Voice operations fail closed.

Managed-project deletion delegates to:

- `GET /api/projects/{project_id}/delete-impact`;
- `POST /api/projects/{project_id}/delete`.

The active project cannot be deleted. A managed project must be archived first, must still match its catalog and manifest fingerprints, and requires explicit acknowledgement of the reported dependency categories. Successful deletion moves the project to recoverable Alexandria Trash. The catalog transaction restores the project directory if catalog publication fails.

## Migration and rollback

Migration remains a dry run until the user explicitly reviews and types `APPLY MIGRATION`. Apply rechecks the exact plan fingerprint and rejects stale or blocked plans. Existing migration rules prohibit automatic source or Script text rewriting and automatic artifact deletion.

`GET /api/migration/history` is file-pure. It returns only:

- operation ID and type;
- applied or rolled-back time;
- action and changed-file counts;
- whether text was rewritten or artifacts were deleted;
- rollback availability and rolled-back relationship;
- invalid history-record summaries.

It never returns saved file snapshots, previous-state bytes, or `content_base64`. Invalid history directories are reported separately without hiding valid operations.

Rollback requires the exact recorded operation and a typed `ROLL BACK` confirmation. The existing rollback service verifies every current file against the recorded post-migration SHA-256 before restoring prior bytes. Any later edit causes a conflict and no newer work is overwritten.

## Model cache actions

Model-cache reads are local-only. Missing and incomplete snapshots are shown as Download or Repair candidates, but neither action begins automatically. Review shows the pinned model purpose, runtime, estimated size, current state, and missing required files. The user must type the exact action word before dispatching `POST /api/model_registry/action`.

Download and Repair remain the authoritative model-registry actions. They enforce pinned revisions, required-file validation, free-space headroom, one active cache operation, and post-download validation. Boundary 15 remains responsible for the broader cache-completeness, memory-admission, and blocked-network release proof.

## Keyboard and dialog behavior

The page follows the canonical shell’s DOM order. Review buttons are ordinary focusable controls. Opening an impact dialog records and focuses its trigger before `showModal()`, moves focus to the confirmation field, and restores focus to the same stable `data-*` trigger after close. Wrong confirmation text keeps the action disabled. Escape or Cancel closes the dialog without dispatching the action.

The page has no filled workflow primary action and no visible destructive action before impact review. At 1536 × 1024 the summary uses five columns. At 1024 × 768 it uses two columns and the major sections stack without horizontal overflow.

## Verification

Focused and adjacent tests cover:

- recovery status and advertised actions;
- file-pure migration planning and history inventory;
- applied, rolled-back, invalid, stale, and conflict records;
- model-registry status and explicit operations;
- Library dependency and delete-impact contracts;
- project dependency reports, archive requirements, recoverable deletion, and transaction rollback;
- canonical Settings/Maintenance separation;
- semantic routes and exact return state;
- native dialog confirmation and focus restoration;
- absence of raw paths and fingerprints.

The Boundary 13 real-Chrome audit covers wide and compact Maintenance, partial diagnostic composition, dependency rows, model rows, typed confirmation gates without executing deletion, native artifact routing, exact Back restoration, and zero console, network, or runtime errors.

Browser evidence: `.omo/evidence/b13-t08-maintenance-browser-final/`.
