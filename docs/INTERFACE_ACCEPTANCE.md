# Alexandria Interface Acceptance

Date: 2026-07-22

This is the current repeatable acceptance gate for Alexandria’s canonical interface. Historical Phase 24 screenshots and old Setup/Characters/Editor/Result evidence remain under `.omo/evidence/`; they do not define current labels or release criteria.

## Authority

Acceptance follows this order:

1. repository-local master plan;
2. Alexandria Interface System Audit;
3. Alexandria Complete Implementation Specification;
4. final page concepts and component sheets under `.omo/reference/interface/`;
5. this executable summary.

The current application model is Project Home plus Script, Cast, Produce, and Export, with Library, Voices, Templates, Settings, More, Maintenance, Help, Advanced identity operations, and Voice Lab as supporting destinations.

## Required verification

Run from a clean named worktree at the intended integration commit.

### Complete offline suite

```bash
PYTHONPATH=app ./app/env/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Record the actual count from the current checkout. Do not substitute a historical target. The recovered baseline at `ac878bb` passed 1,662/1,662 tests before the B19-T03 cleanup slice.

### Syntax and diff integrity

```bash
./app/env/bin/python -m compileall -q app tests
node --check app/static/canonical_interface.js
node --check app/static/navigation_routes.js
git diff --check
```

### Browser acceptance

Use the repository-owned browser audit against an isolated project/configuration root:

```bash
PYTHONPATH=app ./app/env/bin/python tests/interface_browser_audit.py \
  --repo-root . \
  --output-dir /tmp/alexandria-interface-audit
```

The audit must report no unexplained failures, console errors, runtime errors, unexpected network requests, or protected-artifact mutations.

## Viewport matrix

Required references:

- 1536 × 1024;
- 1024 × 768;
- supported narrow fallback where the page contract requires it.

Components reflow; the full interface is never scaled down. Required labels remain visible. Long titles truncate with accessible full text. No surface may create horizontal page overflow or place controls outside the viewport.

## Surface matrix

### Global mode

Verify:

- Project Home;
- Library;
- Voices;
- Templates;
- Settings;
- More;
- Maintenance;
- Help Center.

Project-stage navigation must be absent when no project is open.

### Project mode

Verify:

- Script;
- Cast;
- Produce;
- Export;
- project header and four-stage tracker;
- persistent player;
- context inspector wide and overlay behavior.

### Contextual specialist tools

Verify semantic entry and exact return context for:

- Advanced identity operations;
- Voice designer;
- Audio preparer;
- Dataset builder;
- expressive reference preparation;
- experimental Voice training;
- model-cache diagnostics.

Specialist routes must preserve project, stable character ID, Script label, source, mode, and return destination. No specialist tool may silently approve or assign a production Voice.

## State matrix

Each applicable surface must cover:

- loading;
- true empty;
- no search/filter match;
- recoverable error;
- invalid or incompatible data;
- dense realistic data;
- running;
- resumable or restart-required operation state where the operation contract genuinely requires it;
- blocked;
- stale;
- failed;
- successful/current;
- destructive confirmation;
- optimistic-concurrency conflict.

A loading, empty, or error state replaces the affected data region; it is not rendered as a fake table row.

## Project Home and New Project

- One filled primary action: **New Project**.
- Resume/open and row actions are secondary.
- Project rows form one list with internal dividers.
- Search, sorting, and filtering announce result counts.
- New Project is one modal with five numbered sections, not a wizard.
- Normal creation exposes no model name, cache path, prompt template, or context length.
- Managed project selection reaches `activation_state: current` before project routing.

## Script

- Text is the visual center.
- Selected issue and exact source context are primary.
- Issue categories use current user-facing language.
- **Approve Script** remains disabled while blockers exist.
- Source comparison, corrections, previous/next issue, versions, and provenance are keyboard accessible.
- Task Bundle export/import remains collapsed and exposes no internal handoff ID.
- Verified and unverified provenance are distinguishable.
- Import never auto-approves or silently replaces project state.

## Cast

- One character list only.
- Selected-character order matches the approved contract.
- Voice is the dominant working section.
- Reference audio and exact transcript are visible when relevant.
- Controlled-clone assignment requires the matching preview/listen receipt.
- Missing Voice, identity review, invalid reference/transcript, and required preview are blocking.
- Appearance and advanced preparation remain compact and subordinate.
- Production Voice assignment occurs only in Cast.

## Produce

- Page primary: **Generate missing and stale audio**.
- Current, Stale, Failed, Needs listening, and Ready-to-generate states are distinct.
- Selected row state and selection styling are separate.
- Compact play actions load the persistent player; no duplicate full transport exists.
- Regenerate-all remains secondary, destructive, and confirmed.
- Current audio requires matching dependency fingerprint and validated integrity.
- Export cannot consume a stale or unknown take.

Generated-Takes acceptance remains open until Boundary 16 closes. Release acceptance must eventually prove:

- prior takes remain after successful regeneration;
- current and prior takes appear newest first;
- play/compare works without changing current state;
- **Use this take** atomically promotes a reviewed prior take;
- Keep/pin protects selected takes;
- one-take deletion and bulk cleanup show impact before mutation;
- cleanup cannot delete the current take, pinned takes, rollback evidence, active jobs or receipts, reference/source material, or project-linked artifacts.

Until that contract is implemented and accepted, the interface must not claim generated-take cleanup is complete.

## Export

- Page primary: **Build Audiobook**.
- Save state remains separate from readiness.
- Metadata, cover, credits, chapters, duration, formats, output location, and validation form one publication workflow.
- Format choices remain one radiogroup with clear resulting naming behavior.
- Build is blocked by stale, missing, failed, hash-invalid, fingerprint-mismatched, or required-unreviewed audio.
- Failed or canceled build preserves the prior valid delivery and never reports Built.

## Model and adapter acquisition

- Opening Settings, Maintenance, Cast, Produce, Voice Lab, preview, testing, or synthesis performs no implicit model or adapter download.
- Model Download and Repair occur only through explicit Maintenance actions.
- Built-in adapter download occurs only through its explicit download route/action.
- Missing local assets fail before model initialization with a direct operator action.
- Status reads make no Hub request when a complete pinned snapshot exists.

## Accessibility

Every accepted surface must prove:

- one `main` landmark and correct page-title heading;
- named visible controls in the DOM and accessibility tree;
- visible keyboard focus;
- correct `aria-current` navigation;
- route changes focus the page title;
- selection is not color-only;
- status is not color-only;
- dialogs/drawers trap and restore focus;
- icon-only controls have names and tooltips;
- listboxes use valid single selection and roving focus;
- progress exposes numeric semantics and polite announcements;
- waveforms have slider/numeric equivalents where seekable;
- reduced motion, increased contrast, and reduced transparency remain usable;
- long titles, names, paths, localized copy, missing covers/portraits, and large counts remain accessible.

## Runtime purity and safety

Browser acceptance must use disposable or copied state. Record before/after hashes for:

- project catalog and selected project;
- Script and metadata;
- roster and Voice configuration;
- chunks and production audio;
- output receipts and final files;
- migration and recovery state;
- model registry/cache status;
- task bundles and external-workflow records;
- datasets, training projects, references, and adapters.

Read-only navigation must not change those hashes. No test may mutate real project audio, production Voice assignment, model cache, or release state without the task-specific authorization and protected before/after evidence.

## Visual rejection gate

Block acceptance for:

- generic dashboard KPI cards;
- nested card clutter;
- badge soup;
- duplicate full transports;
- more than one filled page primary action;
- colored edge stripes used as status;
- raw paths, fingerprints, credentials, or internal IDs in normal workflow copy;
- visible native file-input chrome;
- decorative motion or layout-property animation;
- stale historical labels presented as current navigation;
- automatic downloads or silent destructive cleanup;
- deleting prior generated audio merely because a replacement succeeds.

## Closure evidence

A release-ready acceptance record must include:

- exact commit;
- exact test count and command;
- syntax and `git diff --check` result;
- screenshots at required widths for all core surfaces and representative supporting surfaces;
- accessibility report;
- console/runtime/network report;
- protected-state before/after hashes;
- explicit intentionally excluded or blocked human decisions.

Subjective identity, delivery, pronunciation, naturalness, and long-form listening decisions remain human gates. Automated metrics and successful generation do not replace them.
