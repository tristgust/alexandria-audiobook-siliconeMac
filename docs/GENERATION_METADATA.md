# Generation Metadata

A finalized script is accompanied by `annotated_script.meta.json`. The script endpoint remains a plain JSON array for backward compatibility; provenance lives in the companion file.

## Schema

Metadata schema version 1 contains four sections.

### Source

- source basename;
- normalized source SHA-256 fingerprint;
- normalized character count;
- source chunk count.

### Generation

- generation fingerprint;
- complete effective generation identity.

The effective identity includes the runtime/model configuration, prompts, chunk size, token/sampling settings, audit version, and approved-roster context when active. It is intended for trust and resume compatibility, not for display in the default workflow.

### Result

- script fingerprint computed from the complete entry array;
- entry count;
- sorted speaker labels.

### Resume

- whether generation resumed;
- number of chunks already completed when the run started.

The record also includes `generated_at_utc`.

## Atomic lifecycle

Finalization writes `annotated_script.json`, then metadata, then rereads both. The written script must equal the accepted completed entries and the written metadata must equal the constructed metadata. Only after both checks pass is `generation_state.json` cleared.

A failed script or metadata write leaves the checkpoint available for finalization retry.

## Status interpretation

The model-free status layer distinguishes:

- valid current provenance;
- legacy script without metadata;
- metadata without a script;
- corrupt metadata JSON;
- invalid schema or field type;
- script fingerprint mismatch;
- pending generation/finalization;
- source or generation incompatibility.

Legacy scripts remain loadable. Missing metadata is not fabricated from guesses.

## Saved scripts

The script library stores script bundles and companion metadata when available. Loading a saved script refreshes generation status and provenance immediately.

A saved legacy script without metadata remains supported. The library does not invent a source fingerprint or model history for it.

## Speaker-management changes

Rename, merge, split, alias, and reassignment operations recalculate script metadata when valid metadata exists. They update the result fingerprint, entry count, and speaker labels while preserving source and original generation ownership.

Generated audio affected by a speaker change is marked stale separately. Metadata recalculation does not claim that old audio remains valid.

## Security and privacy

The default browser provenance view does not render:

- system or user prompts;
- API keys;
- raw telemetry;
- full runtime configuration;
- source paths beyond the safe basename.

Technical fingerprints and effective identity are available only through progressive disclosure or direct artifact inspection.

## Validation

Metadata rejects unsupported schema versions, negative counts, a resume count greater than the source chunk count, invalid entry arrays, and inconsistent fingerprints.

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_generation_metadata \
  tests.test_generation_status \
  tests.test_script_library
```

See [Resumable Generation](RESUMABLE_GENERATION.md) for checkpoint compatibility.
