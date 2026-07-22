# Alexandria Library

Library is a read-only inventory over the active project. It does not copy assets, create a second asset store, or replace the authoritative Script, Cast, Produce, Export, Voice, dataset, adapter, or project state.

## Inventory families

Library reads the native project locations for:

- source book and source metadata;
- production audio, chunk state, and audio-validity state;
- finished MP3, M4B, and Audacity outputs;
- designed Voices and supplied-recording clone references;
- expressive reference-bank entries;
- dataset-builder and preparer projects;
- owned datasets and LoRA datasets;
- LoRA adapters and merged MLX checkpoints.

Every row exposes a stable artifact ID, artifact family, native relative path, aggregate size, file count, modified time, validity state, provenance, usage, dependency counts, and native destination. Raw fingerprints remain under the collapsed technical-details disclosure.

## Source, audio, and output truth

The source-book entry resolves `alexandria-project.json` or `state.json` without reading outside the active project root. A missing, symbolic-link, or external source is inventoried as invalid rather than followed.

Production audio is one aggregate over `chunks.json`, `audio_validity.json`, and the existing `voicelines/` directory. Library reports total, current, pending, stale, and failed chunk counts plus the actual audio-file count. It does not claim that an audio folder is current merely because files exist.

Finished outputs are reconciled against `export_build.json`. A present output with a matching recorded SHA-256 is available. A missing or mismatched receipt output is invalid. A pre-receipt legacy output is visible as `legacy_unverified`; Library does not silently upgrade it to a verified build.

## Native routing

Library opens each artifact at its authoritative workflow destination:

- source book → Script;
- production audio → Produce;
- finished output → Export;
- Voice, reference, dataset, and adapter artifacts → the appropriate Voice Lab mode under More.

Project, character, source artifact, tool mode, filters, search, and return destination are preserved in semantic route state. Search plus type/state filters use the URL so Back and Forward restore the same result set.

## Dependency and deletion policy

Opening Library is model-free, network-free, and side-effect-free. It never loads TTS, downloads a model, calls an LLM, or mutates an artifact.

Deletion is unavailable for source books, production audio, and finished outputs. Existing deletion handlers remain the only mutation path for artifact families that already support guarded deletion. Library first builds a current dependency report, shows current and historical usage, requires exact confirmation, and rechecks inventory and artifact fingerprints immediately before dispatch. Any current dependency, historical provenance dependency, running operation, unsafe filename, stale fingerprint, or unsupported artifact fails closed.

## Interface states

The canonical global shell covers:

- loading;
- true empty project;
- no filter matches;
- recoverable API error with Retry;
- invalid artifact metadata;
- dense inventory;
- compact 1024 × 768 and wide 1536 × 1024 layouts.

The list remains one flat master list with internal rows. Selection is interaction state, not workflow status. The detail pane contains one secondary native-destination action and a destructive action only when the existing guarded-delete contract says it is supported.

## Verification

Focused contracts cover inventory purity, source confinement, export hashes, audio aggregation, dependency reporting, deletion impact, route APIs, empty/error cases, and unsupported deletion. The Boundary 13 Chrome audit renders 71 artifacts at both reference widths, restores URL-backed search/type state after Back navigation, and opens source, audio, output, and Voice artifacts at their native destinations without console, runtime, or network errors.

Browser evidence: `.omo/evidence/b13-t02-library-browser-final/`.
