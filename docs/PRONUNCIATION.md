# Pronunciation Registry

Alexandria stores reviewed pronunciation guidance in the active project’s
`pronunciation_registry.json`. The registry changes only the text sent to a
speech engine. It never rewrites the imported source, accepted Script, or the
canonical text stored on a chunk.

## Exact occurrence anchors

Every entry targets one exact occurrence in one accepted Script chunk. The
anchor records:

- the chunk index;
- zero-based start and end character offsets;
- the exact original spelling at that span;
- the SHA-256 of the complete chunk text;
- an explicit `accepted_script_chunk` source record containing the same quote,
  offsets, chunk index, and chunk-text hash.

Alexandria does not perform a global find-and-replace. Two identical words in
the same line require separate entries. If the chunk text or anchored spelling
changes, the entry becomes stale and is bypassed until it is reviewed again.
Approved entries may not overlap.

## Spoken output and provenance

An entry contains a reviewed `spoken_form`, a `phonetic_hint`, or both. It also
records:

- the manual, engine, or G2P source and optional revision/alphabet;
- an explicit fallback strategy;
- language, character-label, production-Voice, and engine limits;
- draft, approved, or rejected review state;
- reviewer, review time, notes, and supporting provenance.

Current production backends consume a spoken-form string. A phonetic hint is
therefore applied only when a backend declares a compatible phonetic channel.
Otherwise Alexandria either uses the entry’s explicit spoken-form fallback or
records a visible bypass reason. It never guesses a fallback silently.

## Request-time application

Before single or batch synthesis, Alexandria resolves only the entries anchored
to that chunk. The original chunk text remains unchanged. A separate synthesis
string is constructed by replacing approved spans from right to left so offsets
remain stable.

Each request records:

- the chunk-local pronunciation-entry fingerprint;
- the original and synthesis text hashes;
- every applied and bypassed decision;
- the exact fallback, review, engine-source, and provenance record;
- one request fingerprint used by audio-integrity validation.

The whole-registry fingerprint is retained for audit visibility, but it is not
used as another chunk’s audio dependency. Adding a pronunciation to chunk 200
does not make chunk 10 stale.

Fish inline cue plans are phrase-bound to the original chunk text. When a
pronunciation changes the synthesis string, Alexandria preserves the global
Fish direction but explicitly bypasses the stale inline cue plan for that
request and records `pronunciation_changed_plan_text`.

## Preview

`POST /api/pronunciation-registry/preview` is file-pure by default. It returns
the original text, synthesis text, constraint decisions, and request receipt.
Passing `generate_audio: true` creates a separate listenable WAV under
`pronunciation_previews/`. Preview generation does not modify the registry,
Script, chunks, canonical production audio, or current-audio selection.

Preview audio is retrieved through:

```text
GET /api/pronunciation-registry/previews/{preview_fingerprint}
```

## ChatGPT Task Bundle guidance

After the Script is accepted, the Script workflow can download a
`pronunciation_guidance` Task Bundle. The bundle contains only the current
accepted chunk text, exact chunk hashes and IDs, the current registry snapshot,
and the existing source-context fingerprint when one is available. It does not
contain audio, credentials, model caches, or mutable project internals.

Returned guidance must identify every proposed occurrence with the exact chunk
index, character offsets, spelling, and chunk-text SHA-256 from the exported
bundle. Alexandria rejects changed, missing, overlapping, or stale anchors.
The model cannot return a canonical pronunciation ID or an approval state.

Import validates the completed ZIP and stores normalized entries only inside
the imported structured-result candidate. It does **not** write
`pronunciation_registry.json`, modify Script or chunks, mark audio stale, or
start synthesis. The Script workflow displays each returned item as a draft.
**Preview text** evaluates that draft in a temporary in-memory registry; it
does not promote or persist the entry. Only **Accept guidance** submits an
approved entry through the ordinary registry endpoint with the current
registry fingerprint. That explicit save then uses the existing selective
audio-invalidation and undo transaction described below.

## Saving, deleting, invalidation, and undo

The registry API is optimistic-concurrency guarded:

```text
GET    /api/pronunciation-registry
POST   /api/pronunciation-registry/preview
POST   /api/pronunciation-registry/entries
DELETE /api/pronunciation-registry/entries/{pronunciation_id}
```

Save and delete requests may include `expected_registry_fingerprint`. A stale
fingerprint fails with HTTP 409 and does not write anything. An identical save
returns `unchanged` and creates no history operation.

Changing an entry invalidates only audio for the chunk indices owned by the old
or new version of that entry. Unrelated chunks remain current. Approved imported
human performances keep their content-bound lock and are not regenerated or
invalidated merely because pronunciation metadata exists.

Registry and audio changes use the canonical audio-invalidation transaction.
The operation stores exact JSON snapshots and content-addressed audio backups.
The normal endpoint reverses both the registry and affected audio:

```text
POST /api/audio-invalidation/{operation_id}/undo
```

Undo refuses to overwrite later edits or a newer canonical audio file.

## Repair and export safety

Current-audio validation recomputes the chunk-local pronunciation request. A
manual or out-of-band registry change makes prior audio stale. The rebind repair
path refuses to certify that audio under the new pronunciation because it did
not provably speak the new synthesis string. Regeneration is required.

Export continues to consume only audio whose content, Voice, synthesis settings,
pronunciation request, byte hash, and current-state metadata all match.
