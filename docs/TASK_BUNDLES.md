# Alexandria Task Bundles

Alexandria Task Bundles move bounded project work between Alexandria and
ordinary ChatGPT without granting ChatGPT direct access to the project,
filesystem, credentials, providers, models, or production state.

## Current Version 2 workflow

Every visible **Export task** action creates a deterministic
`*.alexandria-task.zip` Version 2 bundle from the central task registry. A
bundle contains an immutable manifest, human instructions, bounded input,
closed output schema, reviewed guidance, source policy, and checksums. The
completed result returns either as a self-contained
`*.alexandria-completed-task.zip` or as a Version 2 result JSON paired with the
original task ZIP.

Import validates the original members, manifest, registered contract,
checksums, source and artifact fingerprints, task identity, result schema, and
current optimistic state. Valid output enters the task's native review surface.
Import never means automatic approval, assignment, Script replacement, Voice
promotion, or audio overwrite.

## Version 1 compatibility

Version 1 handoff bundles remain readable when the original ZIP is supplied.
They are compatibility input only. The current interface never exports Version
1, asks the user for an internal handoff identifier, or exposes obsolete prompt
copying and Finder-folder steps. A bare legacy JSON result cannot identify its
contract safely and therefore requires its original Version 1 bundle.

## Library states

The task library reports only user-facing states:

- `awaiting_import` — exported and waiting for a completed result;
- `imported` — validated and stored as a review candidate;
- `stale` — source or dependent project artifacts changed;
- `failed` — the local bundle or import record is unavailable or invalid;
- `transferred` — the validated candidate entered its native review workflow.

Duplicate completed results return the existing candidate rather than creating
another copy. Out-of-order results remain separate immutable candidates and
cannot overwrite later work. A transferred candidate cannot be transferred a
second time. Candidate and task-library records are durable across application
restart; failed transactional writes roll back the new candidate and preserve
the prior task record.

## Security and offline behavior

Task ZIPs are read in memory and never extracted. Alexandria rejects path traversal,
absolute or drive-qualified names, backslashes, control characters,
directory entries, symbolic links, encrypted members, duplicate members,
oversized members, oversized archives, suspicious compression ratios,
unexpected members, invalid UTF-8 or JSON, unsupported manifest versions,
wrong tasks, changed registered schemas, and invalid checksums. Stale source or
artifact fingerprints fail closed before native transfer.

Export, inspection, import, task-library reads, and native review transfer are
offline operations. They do not call a model or provider. Sensitive field names
such as credentials, authorization values, API keys, and tokens are rejected
before export.

## Native review destinations

The central registry binds every task type to its output contract and native
review destination. Script results enter Script review; roster and Cast dossier
results enter Cast reconciliation; Voice and visual results remain unapproved
drafts; delivery directions enter their existing review surfaces; and
`pronunciation_guidance` produces exact-occurrence draft candidates in the
Script pronunciation review. Pronunciation import and preview are file-pure;
only explicit **Accept guidance** writes through the reviewed pronunciation
registry and its selective audio-invalidation transaction.
