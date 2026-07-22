# Project Flow Contract

Alexandria’s approved product architecture uses **Projects** as the application entry point and four numbered production stages:

1. Script
2. Cast
3. Produce
4. Export

Library, Settings, Voice Lab, Advanced character operations, and Maintenance remain outside the numbered flow. The current high-fidelity visual redesign is separate from this backend contract.

## Implementation status

Implemented across the backend and canonical interface boundaries:

- pure versioned project-flow domain service in `app/project_flow.py`;
- read-only `GET /api/project_flow/status` route;
- stable stage keys and operator-facing state vocabulary;
- conservative mapping from current Script, roster/Voice, chunk/audio, export, operation, and migration evidence;
- stable blocker codes, native destinations, target IDs, safe actions, and dependency fingerprints;
- project identity that keeps raw paths inside `technical_details`;
- strict tests preventing false completion;
- status reads that do not generate audio, load a model, download a model, migrate data, or rewrite project files.

The flow contract is integrated with managed-project activation, Project Home, Script lifecycle, Cast reconciliation, Produce planning, Export transactions, semantic navigation, Library, Voices, Templates, Settings, More, Voice Lab, Advanced identity operations, Help, and Maintenance.

Remaining flow work belongs to later safety and release boundaries rather than missing navigation architecture:

- complete unified audio invalidation and generated-Takes retention;
- complete pronunciation and restart-safe segmented synthesis;
- finish expressive-clone and instruction-conditioned training decisions;
- run final browser, accessibility, runtime-purity, and release acceptance.

## Endpoint

```text
GET /api/project_flow/status
```

The route is project-scoped to the currently selected Alexandria project in the existing single-project runtime. It is read-only.

## Top-level response

```json
{
  "schema_version": 1,
  "generated_at_utc": "2026-07-20T17:00:00Z",
  "summary_state": "current",
  "project": {},
  "source": {},
  "recommended_stage": "script",
  "safe_next_action": {},
  "stages": [],
  "stage_map": {},
  "blocker_count": 0,
  "completion_state": "requires_work",
  "resumable_operation": null,
  "running_operation": null,
  "compatibility": {}
}
```

`summary_state` is `current`, `stale`, or `unavailable`. `completion_state` is `complete` only when all four authoritative stage contracts pass.

## Stage contract

Required stage keys:

- `script`
- `cast`
- `produce`
- `export`

Allowed operator-facing states:

- `not_started`
- `ready`
- `running`
- `resumable`
- `review_required`
- `blocked`
- `complete`
- `stale`
- `failed`

Each stage contains:

```json
{
  "key": "cast",
  "state": "blocked",
  "summary": "Cast has identity or Voice blockers.",
  "blocker_count": 1,
  "blockers": [],
  "safe_next_action": {},
  "fingerprints": {},
  "operation": {},
  "metrics": {}
}
```

The service derives `state`; callers cannot force completion by supplying a label.

## Blocker contract

Each blocker contains:

- stable `id`;
- machine-readable `code`;
- affected `stage`;
- concise `title` and `explanation`;
- `severity`;
- `blocking` versus advisory classification;
- `native_destination`;
- stable `target_id`;
- optional `safe_action_id`;
- relevant `dependency_fingerprint`;
- `technical_detail_available`.

Raw project or migration paths are not exposed as ordinary blocker/action identity. Maintenance can retrieve technical details separately.

## Completion gates

### Script

Script cannot be complete when any of these fail:

- source availability;
- structural validation;
- speaker attribution validation;
- exact-source fidelity;
- current source/generation dependency match;
- provenance;
- finalization;
- acceptance.

Every generated or imported Script now requires the explicit accepted-version receipt documented in [Unified Script Lifecycle](SCRIPT_LIFECYCLE.md). Finalized native generation is no longer treated as accepted merely for compatibility. A newly created `import_existing_script` project stores the validated candidate under `imports/` and reports Script `review_required`; it does not silently write the candidate as authoritative `annotated_script.json`.

### Cast

Cast cannot be complete when any required speaking identity has:

- unresolved identity;
- ambiguous or missing Script-label mapping;
- missing or invalid production Voice;
- missing clone reference audio;
- empty exact clone transcript;
- stale controlled-clone approval;
- invalid alias;
- missing, incompatible, experimental-only, or unreviewed adapter;
- stale authoritative Voice configuration;
- unapproved or source-incompatible roster.

Visual dossiers, expressive banks, datasets, training projects, and adapters are optional unless the selected production Voice path depends on them.

Imported roster observations use the issue-focused contract documented in [Issue-Focused Roster Reconciliation](ROSTER_RECONCILIATION.md). Safe unique merges and clean additions stay outside the operator issue queue and enter only a reviewable draft. Ambiguity, duplicates, repaired or invalid evidence, unresolved identities, incompatible roster artifacts, and invalid stable-ID relationships remain explicit blockers. A fully resolved draft has one bulk approval action; preserving unresolved identities requires one explicit bulk acknowledgment. Approved-roster replacement preserves exact previous, replacement, and reviewed-draft bytes for guarded rollback.

The shared deterministic resolver remains authoritative for long roster names and shorter Script labels, including:

- Bernice Summerfield → `BERNICE`
- Narrator (Benny) → `NARRATOR (BENNY)`
- Clive Alton → `ALTON`
- The Aubertides → `AUBERTIDES`

### Produce

The authoritative row/read/plan contract is documented in [Produce Aggregate and Generation Planning](PRODUCE_AGGREGATE.md). Project flow consumes that aggregate conservatively together with the existing native audio collector; native binding, file, hash, review, process, and compatibility evidence can only make the stage stricter.

Produce cannot be complete while a required non-empty chunk is:

- missing or ready to generate;
- stale by text, delivery direction, speaker, Voice, synthesis fingerprint, or Script replacement;
- failed;
- hash-invalid or artifact-metadata-invalid;
- blocked by a missing or invalid production Voice;
- awaiting issue review;
- awaiting required listening approval.

The primary plan selects only ready and stale chunks with valid Voices. Current validated audio is preserved. Failed audio uses an explicit retry plan. `Regenerate all` is secondary, destructive, fingerprint-guarded, and explicitly confirmed. Queue status and cancellation remain part of Produce rather than a separate worker workflow.

Status inspection hashes current audio bytes but does not generate, decode, delete, or replace audio. Final Export retains strict decode validation.

### Export

The authoritative metadata/chapter/format/output/receipt contract is documented in [Export Aggregate and Guarded Build Transaction](EXPORT_AGGREGATE.md). Project flow consumes that aggregate for current delivery state while retaining stricter native build-process or output-validation failures.

Export cannot be complete while:

- Produce is incomplete;
- required metadata is missing;
- M4B has no usable chapter structure;
- a selected format is unsupported;
- no selected output exists;
- the output dependency fingerprint is stale;
- output bytes or receipt metadata fail validation;
- a prior build failed.

The build dependency fingerprint includes Produce, metadata, exact chapter rows, selected formats, and cover art; Produce already binds Script, Cast, Voice, synthesis, and audio. Every selected output is built and validated in a confined temporary history directory before canonical replacement. Builder, validation, commit, receipt, or history-publication failure restores all previous selected outputs and the previous receipt. Cancellation before commit preserves the prior delivery. Status inspection hashes existing outputs but does not decode them; build-time validation remains strict.

## Compatibility mapping

Migration blockers are mapped to the nearest native destination when possible. For example, an approved roster whose evidence no longer matches the selected source links to `cast:review` and blocks Cast plus downstream stages without falsely marking Script invalid.

Compatibility data may include sanitized Maintenance actions and a plan fingerprint. Absolute paths remain technical details only.

## Relationship to native endpoints

The flow endpoint does not replace specialist routes. Existing Script-generation, unified Script-lifecycle, aggregate Cast, roster, controlled-clone, audio-artifact, migration, training, and recovery endpoints remain the detailed source of truth for their domains. The flow service maps their authoritative evidence into one product-level decision contract.

## Cast aggregate authority

The Cast stage now consumes the same versioned aggregate evidence exposed by `GET /api/cast`, then merges it conservatively with native roster and Voice validation. The aggregate supplies stable-character identity, deterministic Script-label mapping, Persona-compatible Voice fields, optional appearance/preparation context, selected-character deep links, and concise readiness states. Native reconciliation, clone receipts, adapter approval, aliases, stale Voice configuration, process state, and compatibility remain stricter authorities: their blockers are unioned rather than replaced. Filtering is applied only after the full Cast has been validated, so hidden rows cannot change stage completion.

Do not implement a frontend completion rule by reassembling unrelated endpoint state or inspecting DOM classes. Consume this versioned contract and follow each blocker’s native destination.

## Verification

Focused contract and route suite:

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_project_flow \
  tests.test_project_flow_routes
```

The integration suite additionally covers current generation, roster transactions, audio artifacts, recovery, and model-cache routing.
