# Ordinary ChatGPT structured work

Alexandria uses version 2 Task Bundles for ordinary-ChatGPT structured work. See [TASK_BUNDLES.md](TASK_BUNDLES.md) for the archive contract, registry, guidance profiles, native destinations, validation rules, and compatibility behavior.

The workflow is file-based and does not automate the ChatGPT website, require an API key, expose the project directory, or require a ChatGPT app/MCP connection:

1. Choose **Export task**.
2. Select a plain-language task and any required speaker or character scope.
3. Alexandria downloads one self-contained `*.alexandria-task.zip`.
4. Attach the ZIP to an ordinary ChatGPT conversation.
5. Import the returned `*.alexandria-completed-task.zip` or fallback JSON as the completed task.
6. Alexandria identifies the task, validates the original members and completed result, then opens the native review workflow.

There is no user-visible handoff ID, code, reference field, copied prompt, Finder handoff-folder step, or `Other structured tasks` section. When fallback JSON cannot be matched to Alexandria's local task library, the interface asks for the original task ZIP rather than an identifier.

## Registered task families

The shared task registry covers:

- Script generation and Script review;
- Character-roster discovery and Character-roster reconciliation;
- bulk preparation-identity generation for all approved speaking characters plus single Persona-contract creation, refinement, reconciliation, and audit;
- visual discovery and visual-dossier compilation;
- advanced acoustic-identity generation, refinement, and audit for one preparation identity;
- per-line delivery-direction generation and audit.

Every offered task has an explicit native JSON contract, minimized input builder, stale-result fingerprint policy, task-specific guidance profile, native review destination, and transfer policy. Alexandria does not export a generic free-form prompt for unsupported tasks.

## Native review routing

A completed result is never approved merely because validation succeeds:

- Script results become inspected Script candidates and retain the explicit **Use this script** boundary.
- Roster discovery creates evidence-backed observations.
- Roster reconciliation creates the normal reviewable roster draft.
- bulk and single Persona-contract results create unapproved reference/training identity drafts inside **More voice tools** on the matching Characters entries.
- visual discovery and compilation enter inline **Appearance** review on the matching character.
- line-direction results enter exact-text-preserving Script/Editor review.

Character roster is the upstream identity authority. It determines canonical names, aliases, narrator roles, duplicate identities, and which Script labels belong to the same entity. Roster discovery may collect source-backed voice clues and dialogue samples, but preparation identities are generated only for approved speaking characters.

When a bulk Persona-contract result meets existing preparation identities, Alexandria shows each **Current** and **Imported** draft side by side. New drafts can be added, existing drafts remain unchanged by default, and only characters explicitly selected for replacement are updated. Actual model, clone, controlled clone, designed voice, adapter, alias, preview, and production assignment remain in the selected character’s primary **Voice** section.

Other project-state conflicts remain persisted. The interface shows `Reconciliation required` or `Review blocked`, explains what failed and what remains safe, and provides a visible action to open the native destination. Alexandria does not silently duplicate, replace, discard, approve, or assign the result.

## Voice guidance

Voice-related task bundles contain a reviewed, versioned Voice Reference guidance snapshot. Persona-contract and advanced acoustic-repair tasks keep stable preparation identity separate from immediate line performance. They use supported register, timbre, texture, resonance, weight, placement, accent, anatomy, and vocal mechanics; they exclude momentary emotion, pacing, scene context, intention, urgency, and line-specific emphasis.

`ref_text` must be exact representative source dialogue. Invented or altered quotations are not permitted. Nonhuman speakers use source-supported anatomy or synthetic mechanisms rather than generic alien, gravel, echo, or distortion language.

Line-direction tasks preserve every speaker label and spoken word and edit only performable `instruct` values: emotional objective, subtext, intensity, pacing, rhythm, articulation, projection, restraint, pauses, and emphasis.

## Script-result review

Script generation, Script review, and line-direction results use the existing annotated-script candidate model. A result is never applied merely because upload completed.

The review shows:

- `Source verified` or `Source not verified` provenance;
- imported entry, speaker, and spoken-character totals;
- current-to-imported count deltas;
- metadata and voice-configuration consequences;
- whether completed audio will become stale;
- warnings;
- the exact checkpoint keep/discard/cancel decision required before application.

Application sends an opaque candidate ID rather than the full Script or backup payload. Alexandria revalidates the candidate and aliases, snapshots every touched file, rebuilds all chunks as `pending`, preserves prior generated audio but records them as stale, applies only the explicit checkpoint choice, and records exact pre-import bytes for same-session undo. Rollback is rejected if any affected file changed after the import.

Direct annotated-script JSON and versioned annotated-script ZIP imports continue to use the same review/apply/rollback boundary.

## Security and compatibility

No Task Bundle exports API keys, access tokens, cookies, passwords, unrestricted project state, or arbitrary paths. Archive members, filenames, sizes, compression ratios, UTF-8 encoding, JSON depth, finite values, sensitive keys, checksums, guidance hashes, native schemas, source fingerprints, and artifact fingerprints are validated before native routing.

Legacy version 1 handoff ZIPs and returned JSON remain readable during the compatibility period when supplied together. Their old user-interface controls remain removed; compatibility does not reintroduce a code/reference workflow.
