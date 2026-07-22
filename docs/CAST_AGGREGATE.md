# Cast Aggregate Read Model

Alexandria uses **Cast** as the normal production stage for resolving character identity and assigning one valid production Voice to every required speaking role.

The backend read model is implemented in `app/cast_aggregate.py`. It does not create another roster, Voice list, Persona workflow, visual-character list, or preparation-project roster. It adapts the existing authoritative artifacts and specialist services into one operator-facing character collection.

## Current implementation status

Implemented:

- one versioned aggregate Cast contract;
- one visible character collection keyed by stable character ID;
- one selected-character aggregate;
- deterministic Script-label mapping with ambiguity reporting;
- Identity, Script connection, Voice, Character, Appearance, Advanced Voice Setup, and Readiness records;
- Persona description/reference-text compatibility folded into Voice;
- built-in/custom, clone, controlled clone, designed Voice, adapter, and alias read-model support;
- readable clone-reference and exact-transcript checks;
- native controlled-clone, adapter, alias, roster, and Voice validation preserved conservatively;
- optional appearance and advanced preparation that do not block ordinary Cast completion unless native Voice validation says they are required;
- meaningful row states only;
- required filters and search;
- selected-character preservation when filtering hides the selected row;
- stable-character deep-link endpoint;
- bounded, read-only optional specialist-state indexing;
- path confinement to `technical_details`;
- project-flow Cast evidence integration.

Pending later boundaries:

- semantic Cast page scaffold and final visual design;
- reconciliation issue queue and approval mutations;
- Cast-level bulk actions;
- unified one-character Voice save command and stale-write concurrency adapter;
- managed-project runtime activation;
- contextual Voice Lab shell;
- specialist operation UI consolidation.

Existing specialist endpoints and files remain authoritative for mutation, controlled-clone receipts, Persona/preparation projects, visual collection, dataset generation, training, aliases, and Voice assignment. The aggregate never silently writes, approves, assigns, trains, generates, migrates, or deletes those artifacts.

## Endpoints

### Cast collection and selected character

```text
GET /api/cast
```

Query parameters:

- `filter`: `all`, `needs_attention`, `unassigned`, `speaking_roles`, `non_speaking`, or `ready`;
- `search`: optional name, alias, or Script-label search;
- `selected_character_id`: optional stable character ID.

The response includes:

- schema version;
- stage summary and completion state;
- filter counts;
- filtered visible character rows;
- full selected-character aggregate even when hidden by the active filter/search;
- `selection_visible`;
- exact aggregate blockers;
- dependency fingerprints;
- native validation summary;
- compatibility advisories;
- technical project path only under `technical_details`.

### Stable-character deep link

```text
GET /api/cast/characters/{character_id}
```

This returns the exact selected-character aggregate for the stable character ID. Unknown IDs return a machine-readable `404 cast_character_not_found` response.

## Aggregate data sources

Authoritative ordinary inputs:

- `character_roster.json`, or `character_roster.draft.json` when no approved roster exists;
- `annotated_script.json`;
- `voice_config.json`;
- existing native roster/Voice validation through `inspect_cast_evidence`.

Optional compatibility inputs are indexed from bounded project-local JSON under known specialist locations such as:

- `persona_projects/`;
- `voice_training_projects/`;
- `persona_refs/`;
- `designed_voices/`;
- visual-dossier files/directories;
- dataset/preparer/training directories;
- clone/Voice preview directories.

Optional indexing:

- follows no symbolic links;
- reads JSON only;
- limits individual file size, total size, and file count;
- never triggers model loading, model download, generation, transcription, training, migration, or network access;
- reports invalid or skipped optional files as non-blocking compatibility advisories;
- treats malformed authoritative roster, Script, or Voice JSON as an explicit Cast error rather than an empty state.

## Character row contract

Each visible row contains only:

- `display_name`;
- speaking or non-speaking role;
- concise `voice_summary`;
- one `readiness_state`;
- blocker count;
- next useful action.

Allowed readiness states:

- `needs_identity_review`;
- `needs_voice`;
- `preview_recommended`;
- `ready`.

The row does not repeat roster, mapping, Persona, visual, preparation, dataset, and training state as separate badges.

## Selected-character order

The aggregate is structured for the approved inspector order:

1. `voice`
2. `character`
3. `appearance`
4. `advanced_voice_setup`

Identity and Script mapping remain available both in the compact Character record and as explicit backend records for blockers and deep links.

## Identity record

Per stable character ID:

- canonical name;
- display name;
- aliases;
- titles;
- nicknames;
- pronouns;
- species/type;
- relationships;
- role;
- speaking state;
- source confidence;
- unresolved questions;
- conflict state;
- source evidence summary;
- representative Script lines;
- stable-ID presence;
- roster-entry fingerprint.

A missing stable ID receives a deterministic compatibility ID for inspection only and a blocking `cast_stable_character_id_missing` issue. It is not treated as a repaired authoritative ID.

## Script connection and deterministic label mapping

Per character:

- resolved Script Voice label;
- mapping method;
- confidence;
- ambiguity state;
- candidate labels;
- Script line count;
- representative lines;
- Script fingerprint.

Resolution uses, in order:

- explicit stored Script label;
- normalized canonical/display names;
- aliases, titles, and nicknames;
- article normalization;
- parenthetical names;
- unique surname or given-name evidence;
- exact representative-line evidence.

Equal best candidates are reported as ambiguous rather than guessed.

Regression coverage preserves:

- Bernice Summerfield → `BERNICE`;
- Narrator (Benny) → `NARRATOR (BENNY)`;
- Clive Alton → `ALTON`;
- The Aubertides → `AUBERTIDES`.

The live project was probed read-only and preserves all four mappings.

## Voice record

Operator-facing Voice combines production assignment and compatible Persona data:

- selected production method;
- selected backend;
- selected built-in/custom/designed Voice;
- clone reference state;
- exact reference transcript;
- reference-audio fingerprint;
- controlled-clone capability and approval state;
- persistent Voice description;
- representative/reference text;
- preview state and listening state;
- designed-Voice state;
- adapter summary;
- alias target;
- saved configuration fingerprint;
- meaningful Voice blockers.

Persona remains valid backend data. Compatible Persona `description` and `ref_text` fields are exposed as `persistent_voice_description` and `representative_text` inside Voice. No separate ordinary Persona project or Persona row is introduced.

The aggregate does not claim that persistent Voice description replaces per-line delivery instructions.

## Voice method validation

### Built-in/custom/saved Voice

Requires a selected Voice identifier.

### Supplied-recording clone

Requires:

- readable project-local or absolute reference audio;
- exact non-empty reference transcript.

The response exposes state, not raw reference paths.

### Controlled clone

The aggregate recognizes controlled-clone configuration but does not invent approval. It requires the saved approval/configuration fingerprint and then applies the stricter native controlled-clone validation. Native missing, invalid, stale, or unbound approval remains blocking even when aggregate fields appear populated.

### Designed Voice

Requires a saved designed Voice or stable Voice description compatible with the existing native configuration. Production assignment remains an explicit specialist mutation.

### Adapter/trained Voice

The compatibility summary reads only bounded manifest fields. It requires native production assignment support and manual listening approval. Raw adapter paths and unrestricted manifest content are not exposed.

### Alias

Requires an existing compatible Voice target. Missing or invalid targets remain blockers.

## Appearance

Appearance is compact and optional by default:

- status;
- concise summary;
- stable traits;
- variants;
- conflicts;
- unknowns;
- evidence availability;
- operation state.

An absent, stale, failed, or incompatible visual dossier does not block Cast completion unless a later explicit feature declares appearance required. Non-speaking identities remain in the same character collection.

## Advanced Voice Setup

Collapsed contextual data includes:

- expressive-reference state;
- owned-recording preparation state;
- dataset state;
- adapter/training state;
- compatibility state;
- provenance availability;
- specialist blockers.

These optional states do not receive equal readiness weight with ordinary Voice assignment. They do not silently assign generated, prepared, or trained artifacts.

## Readiness and native validation

Aggregate parsing can only make native state more understandable; it cannot weaken native validation.

After the full unfiltered roster is built, Alexandria applies authoritative native evidence for:

- roster existence;
- reconciliation review;
- approved-roster state;
- source/roster compatibility;
- required speaking-role count;
- valid production-Voice count;
- unresolved identities;
- ambiguous Script-label mappings;
- missing or invalid Voices;
- invalid clone references;
- controlled-clone approval;
- adapters and aliases;
- stale Voice configuration;
- running, resumable, and failed operations.

Native blockers are unioned with aggregate blockers. They are never replaced by looser aggregate interpretation.

Filtering and search run only after this full native validation. A hidden selected character keeps its authoritative blockers, and filtered rows cannot change total completion counts.

Cast is complete only when:

- a character collection exists;
- native roster review is complete;
- the approved roster is current;
- every required speaking identity is resolved;
- every required speaking identity maps to one Script label;
- every required speaking identity passes native production-Voice validation;
- clone, controlled-clone, adapter, alias, and stale-configuration checks pass;
- no blocking aggregate/native issue remains.

Visual dossiers, expressive banks, datasets, adapters, training, and preparation projects remain optional unless the selected production Voice path explicitly depends on one.

## Project-flow integration

`GET /api/project_flow/status` consumes the same Cast aggregate evidence and merges it conservatively with the existing detailed native Cast evidence.

The project-flow adapter:

- preserves stricter native blocker IDs;
- uses the larger required-role count;
- uses the smaller valid-Voice count;
- requires both native and aggregate roster approval/current state;
- unions identity, mapping, Voice, clone, controlled-clone, adapter/alias, and stale-Voice blockers;
- preserves native process, resume, failure, and fingerprints.

The live project remains blocked because the existing approved roster evidence is incompatible with the selected source. The aggregate does not relabel it complete.

## Verification

Focused service and route suite:

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_cast_aggregate \
  tests.test_cast_aggregate_routes
```

Coverage includes:

- stable character aggregate;
- required label-mapping regressions;
- representative-line resolution;
- ambiguity;
- missing Voice;
- Persona-as-Voice compatibility;
- clone audio/transcript validation;
- controlled-clone approval;
- designed Voice;
- alias;
- adapter approval;
- non-speaking identity;
- optional appearance;
- optional advanced preparation;
- filtering/search;
- hidden selected-character persistence;
- native validation precedence;
- model-free and file-pure status reads;
- malformed authoritative versus optional state;
- stable-character route;
- project-flow authority merge.
