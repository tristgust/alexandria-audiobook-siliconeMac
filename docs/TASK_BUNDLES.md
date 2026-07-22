# Alexandria Task Bundles

Alexandria Task Bundles are the ordinary-ChatGPT workflow for structured audiobook work. The user chooses a task and scope, exports one self-contained ZIP, attaches it to a normal ChatGPT conversation, and imports either a completed ZIP or a fallback JSON result. Alexandria identifies the task and opens the correct native review destination. The user never copies or types an internal task ID, handoff code, fingerprint, or operation ID.

## Exported task

A version 2 task ZIP contains:

```text
manifest.json
instructions.md
input.json
schema.json
checksums.json
guidance/
  source-policy.md
  task-guidance.md
  voice-reference.json
  nonhuman-speakers.md  # voice-related tasks only
```

`manifest.json` binds the task type, plain-language label, Alexandria/native contract versions, source and dependent-artifact fingerprints, selected target, native review destination, transfer policy, reviewed guidance version, member sizes, and hashes into an immutable `task_id`. The identifier remains internal and is used only for verification and local-library lookup.

The task builder includes only the fields allowed by that task's registry entry. It rejects secrets, unsupported values, unsafe control characters, non-finite numbers, unexpected input fields, and missing required context.

For identical task content and an identical creation timestamp, export is byte-reproducible. Members are written in lexical order with a fixed ZIP timestamp, fixed permissions, and deterministic deflate settings. Member and total uncompressed sizes are bounded before publication, filenames remain confined, and the final archive is atomically replaced only after successful construction.

## Completed task

The preferred completed ZIP preserves every original task member byte-for-byte and adds:

```text
result/result.json
result/completion.json
```

Every original ZIP member must remain byte-identical. ZIP compression metadata may differ, but the uncompressed bytes for `manifest.json`, `instructions.md`, `input.json`, `schema.json`, `checksums.json`, and every guidance member must not be parsed, reformatted, normalized, omitted, or replaced.

All completion hashes are lowercase SHA-256 hexadecimal digests of the exact member bytes. `result_size_bytes` is the exact byte length of `result/result.json`. `result/completion.json` uses exactly this shape:

```json
{
  "schema_version": 2,
  "task_id": "copy manifest.json task_id exactly",
  "manifest_fingerprint": "SHA-256 of the exact original manifest.json bytes",
  "result_path": "result/result.json",
  "result_size_bytes": 0,
  "result_sha256": "SHA-256 of the exact result/result.json bytes",
  "completed_at_utc": "RFC 3339 UTC timestamp, for example 2026-07-19T21:00:00Z"
}
```

Replace the byte count and hash placeholders with computed values. This binds the result to the original task without requiring repository access. A completed ZIP is therefore self-contained and can be imported without Alexandria retaining the exported copy.

Clients that cannot return a ZIP may return a JSON envelope:

```json
{
  "alexandria_task": {
    "schema_version": 2,
    "task_id": "task_...",
    "manifest_fingerprint": "..."
  },
  "result": {}
}
```

For the fallback envelope, `manifest_fingerprint` is likewise the lowercase SHA-256 hexadecimal digest of the exact original `manifest.json` member bytes—not a digest of parsed or reformatted JSON. Alexandria locates the original task in its local task library automatically. When the exported task is no longer available locally, the importer asks for the original ZIP file. It never asks the user to type an identifier. Legacy version 1 result JSON remains readable when supplied together with its original legacy ZIP.

## Registered tasks

| Task family | Registered tasks | Native review destination |
| --- | --- | --- |
| Script | Generate annotated Script; review annotated Script | Existing Script candidate review and explicit **Use this script** boundary |
| Character identity | Discover character roster; reconcile character roster | Characters draft or focused import review; this establishes who each character is |
| Reference/training identity | Create identity drafts for all speaking characters; create, refine, reconcile, or audit one draft | Unapproved preparation identity inside **More voice tools** for the matching character; it never creates a second production Voice editor |
| Advanced acoustic repair | Create, refine, or audit only the acoustic-identity field for one preparation draft | The same preparation identity inside **More voice tools**; ordinary production Voice remains authoritative |
| Visual dossier | Discover visual evidence; compile visual dossier | Inline **Appearance** review for the matching character |
| Line delivery | Create or audit per-line directions | Exact-text-preserving Script/Editor review |

The API and interface derive from the same registry. A task without a native contract and review destination is not offered as a generic prompt.

## Voice Reference guidance

Voice-related task ZIPs include a reviewed, versioned, paraphrased guidance snapshot pinned to the upstream Alexandria Voice Reference revision recorded in `guidance/voice-reference.json`.

The internal Persona and advanced acoustic-repair contracts treat preparation identity as a stable acoustic identity:

- supported register or pitch range;
- two or three concrete timbre, texture, resonance, weight, placement, or tonal-character traits;
- accent, age impression, anatomy, or vocal mechanics only when source evidence supports them;
- exact representative `ref_text` taken from the supplied character dialogue.

They exclude momentary emotion, pacing, scene context, intention, urgency, line-specific emphasis, and acting direction from the persistent description. Those belong in line `instruct` values.

Line-direction tasks keep every speaker label and spoken word exact and edit only immediate performable direction: emotional objective, subtext, intensity, pacing, rhythm, articulation, projection, restraint, pauses, and emphasis.

Nonhuman-speaker guidance converts only source-supported anatomy, resonance chambers, breath or synthetic mechanisms, multiplicity, interference, filtering, or transmission artifacts into acoustic consequences. It does not default every creature, machine, collective, or alien to generic gravel, echo, or distortion.

## Import and native routing

Import performs archive confinement, member/hash verification, guidance verification, source/dependency drift checks, native schema validation, and task-specific contract validation before any native artifact changes.

After validation:

- Script-shaped results become inspected Script candidates.
- Roster discovery becomes evidence-backed observations.
- Roster reconciliation becomes a normal roster draft.
- Bulk and single Persona-contract results become unapproved reference/training identity drafts inside **More voice tools** for the matching Characters entries.
- Visual discovery and compilation enter inline **Appearance** review state on the matching character.
- Line-direction results enter exact-text-preserving Script/Editor review.

Character roster is upstream: it resolves canonical names, aliases, narrator roles, duplicate identities, and which Script labels refer to the same entity. Preparation identities are generated only for approved speaking characters plus their exact Script dialogue. Roster discovery may collect voice clues and sample lines, but it does not create production Voice settings against unresolved identities.

When a bulk Persona-contract import meets existing preparation identities, Characters shows **Current** and **Imported** side by side for each affected character. New drafts can be added, current drafts remain untouched by default, and only explicitly checked replacements are saved as new unapproved preparation identities. Actual model, clone, controlled clone, designed voice, adapter, alias, preview, and production assignment remain in the selected character’s primary **Voice** section.

Other project-state conflicts remain persisted and show **Reconciliation required** or **Review blocked**, explain the conflict, and provide a visible native-review action. Results are not silently duplicated, replaced, or discarded.

## Mutation rules

Validation and navigation never approve or assign anything. Existing native review controls remain authoritative:

- Script replacement requires explicit **Use this script** and preserves its checkpoint/rollback rules.
- Character roster changes require native reconciliation and explicit approval.
- Persona-contract and advanced acoustic-repair changes remain preparation drafts until explicitly approved inside **More voice tools**.
- Visual dossiers remain review state until accepted in the selected character’s **Appearance** disclosure.
- Voice assignments, reference-bank approval, adapter assignment, and production output are never changed by Task Bundle import.

## Security and compatibility

Task Bundle validation retains or strengthens Alexandria's previous external-workflow protections:

- no absolute, parent-traversal, duplicate, symbolic-link, directory, or encrypted archive members;
- bounded member and total sizes plus compression-ratio checks;
- UTF-8, JSON-depth, finite-number, safe-key, and sensitive-field checks;
- exact schema, guidance, member, manifest, completion, source, and artifact fingerprints;
- native contract validation with no silent normalization;
- duplicate completed-result detection;
- version 1 ZIP/result compatibility without exposing the old code/reference workflow.
