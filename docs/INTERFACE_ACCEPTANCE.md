# Alexandria Interface Acceptance

Date: 2026-07-17

This document is the repeatable Phase 24 acceptance gate for Alexandria’s browser interface. It covers the application shell, every production stage, every Tools workspace, responsive behavior, representative workflow states, motion, accessibility, and interface-owned contract tests.

It does not by itself declare production LoRA quality or replace backend benchmark, listening, and phase-specific acceptance.

## Required commands

Run from the repository root.

### Complete offline regression

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest discover -s tests
```

Accepted result at this boundary:

```text
Ran 921 tests
OK
```

The explicit `PYTHONPATH` is required so tests import `app/app.py` rather than treating the `app/` directory as a namespace package.

### Focused interface contracts

```bash
./app/env/bin/python -m unittest \
  tests.test_accent_status_ui \
  tests.test_llm_setup_ui \
  tests.test_llm_runtime_ui \
  tests.test_persona_visual_ui \
  tests.test_character_roster_ui \
  tests.test_character_roster_ui_behavior \
  tests.test_phase17e_ui_behavior \
  tests.test_clone_voice_interface_contract \
  tests.test_voice_training_interface_contract \
  tests.test_user_test_repairs
```

Accepted result:

```text
Ran 93 tests
OK
```

### Real-browser interface audit

```bash
PYTHONPATH=app:tests ./app/env/bin/python tests/interface_browser_audit.py \
  --repo-root . \
  --output-dir /tmp/alexandria-interface-audit
```

The audit launches an already-installed Chrome-family browser against an isolated FastAPI fixture. It installs no package and downloads no browser.

Accepted result:

```text
status: PASS
failures: []
```

### Diff and motion checks

```bash
git diff --check

rg -n --pcre2 \
  "transition:\\s*all|\\bease-in\\b(?!-out)|\\bscale\\(0\\)|@keyframes|animation-name" \
  app/static/index.html
```

The motion search must return no prohibited interface motion. Purposeful running-state icons may still use Font Awesome’s spinner class; reduced-motion CSS disables their animation.

## Viewport matrix

| Viewport | Coverage | Required result |
| --- | --- | --- |
| 1440 × 1000 | Every production stage and Tools workspace | No horizontal overflow or out-of-bounds controls |
| 390 × 844 | Every production stage and Tools workspace | No horizontal overflow; controls stack without loss of meaning |

Audited destinations:

- Setup
- Script
- Characters, including draft review, one unified Voice section, compact Appearance and Character details disclosures, advanced reference/training identity, expressive preparation, and contextual tool handoffs
- Editor
- Result
- Advanced identity operations
- Voice designer
- Audio preparer
- Dataset builder
- Voice training

## Representative state matrix

| State | Acceptance |
| --- | --- |
| Setup runtime diagnostics open | Two readable panels; secondary details remain nested; open chevron points down |
| Setup local model cache | Required/optional pinned models show Cached, Missing, or Repair needed; Download/Repair are explicit; desktop/narrow layouts remain readable; running progress and actionable failure are visible |
| Setup advanced generation open | Generation/sampling visible; Script prompt group opens independently |
| Character Voice default | Voice is the only primary working section; exactly one character list remains; Appearance, Character details, and More voice tools are collapsed; preparation-only identity fields are not visible |
| Clone audio upload | Re-rendering after upload restores the same open speaker panel, controlled-clone disclosure state, and newly selected reference |
| Clone reference playback | Play changes to Pause while the reference is playing, returns to Play when paused or ended, and keeps the accessible label synchronized |
| Controlled clone approval | Capability loads before the Voices editor renders; preview response explicitly requires listening; the backend stays locked until playback starts and reaches the end; saved backend and settings survive reload |
| Controlled clone invalidation | Editing reference, transcript, identity note, preview text, instruction, or generation settings clears fingerprint/play/listen approval, restores Qwen Base, and persists that fallback after reload |
| Dataset project loaded | Table shell visible; no empty/loading state remains over it |
| Dataset generation progress | Count and percent are readable outside the fill bar; the bar carries only visual progress and ARIA value |
| Dataset project not selected | Purposeful project empty state and one primary New project action |
| Editor populated | Progress reflects real chunks; safe and destructive actions are distinct |
| Editor loading/empty/error | State replaces the table rather than becoming a fake table row |
| File picker drag-over | Accepted picker shows clear drag feedback without exposing native file chrome |
| Mobile navigation open | All five numbered stages and Tools remain reachable |
| Success toast | Uniform border, semantic icon, no colored edge stripe |
| Destructive confirmation | Explicit action verb and destructive button styling |
| Text-entry dialog | Explicit title, visible label, validation, and task-specific submit label |
| Character source mismatch | Uniform border on all sides; no warning stripe |
| Active master-list selection | Full-row background only; no inset colored edge bar |
| Reference/training identity draft | Existing or new preparation identity appears only after More voice tools opens; draft description and representative text remain editable; no production Voice setting changes |
| Expressive preparation synthetic path | Explicit creation persists as `draft`; no training or assignment occurs automatically |
| Expressive preparation readiness | Aligned facts and blocker list; no KPI tiles |
| Routine identity edit in Characters | Mapped Script label and exact lines remain visible; rename and aliases follow the stable character; affected audio is recorded stale |
| Advanced identity rename/undo | Reassign/merge/split remain outside the routine inspector; stable character ID survives exact undo |
| Contextual specialist tool | Selected character name, stable ID, Script voice, and sensible defaults survive the handoff and Return to character restores the same inspector |
| Voice training capability | Characters remains the primary handoff; measured MLX and controlled-clone paths are readable; unsupported shared-runtime adapter train/test/download/preview actions are absent |
| Apple Silicon compute readout | Topbar shows real CPU activity when no discrete CUDA GPU statistics exist; tooltip retains system memory, Alexandria RSS, and MLX active memory |
| Audio preparer | Single and batch uploads are project-confined; supported formats are explicit; preparation logs and finished ZIP downloads remain accessible |

## Navigation acceptance

- Brand is `Alexandria` only.
- Desktop topbar shows `1 Setup`, `2 Script`, `3 Characters`, `4 Editor`, `5 Result`.
- The auxiliary menu label is exactly `Tools`.
- Tools contains Advanced identity operations, Voice designer, Audio preparer, Dataset builder, and Voice training. No duplicate Character roster, Voice casting, Voice profiles, or Visual dossiers destination is present.
- No active underline or sidebar is used.
- Compute and Disk are flat icon/value readouts. Apple Silicon uses live CPU activity rather than showing a false unavailable state; detailed memory values remain in the tooltip.
- Hash navigation, `aria-current`, skip navigation, and compact-menu closure work without decorative transition.

## Accessibility acceptance

- Semantic `main`, page headings, labels, buttons, lists, tables, and native `details` elements are used.
- Programmatic page navigation does not draw a page-sized focus outline.
- Actual interactive controls retain visible `:focus-visible` treatment.
- Modal focus is trapped by Bootstrap and returned after dismissal.
- File inputs remain real, keyboard-accessible controls behind the custom picker surface.
- Meaning is not dependent on color or animation.
- `prefers-reduced-motion`, `prefers-reduced-transparency`, `prefers-contrast: more`, and hover capability receive explicit adaptations.

## Motion review verdict

Approved motion is limited to:

- pointer press feedback;
- right-to-down disclosure indication;
- focus, hover, drag, and selection feedback;
- real progress changes;
- occasional modal and toast entrance/exit;
- running-state indication.

Prohibited motion includes:

- decorative loops;
- animated navigation;
- layout-property animation;
- `transition: all`;
- standalone `ease-in`;
- `scale(0)`;
- fake indeterminate progress bars used only for visual activity.

**Verdict: APPROVE.**

## Visual anti-pattern gate

Block release when any of the following appear:

- colored left-edge stripes on notices or toasts;
- inset colored edge bars on active master-list rows;
- dashboard KPI tiles for workflow readiness;
- pill or badge soup;
- generic cards used only to fill space;
- native Browse controls on visible upload surfaces;
- raw browser prompts or confirmations;
- oversized empty rails or page shells;
- decorative gradients, hero sections, or ornamental counters;
- technical fingerprints, paths, or telemetry in the default workflow;
- helper descriptions that merely restate labels.

## Phase 24B interface result

At this boundary:

- complete offline regression passes 983/983;
- focused Phase 24B recovery, Setup, process, log, UI, repair, and documentation contracts pass 107/107;
- all default and representative browser states pass at 1440×1000 and 390×844;
- the compact `Project status` disclosure is closed by default and expands into seven flat operational stage rows rather than dashboard cards;
- the expanded recovery state restores the saved source, renders exact stage capability/action labels, preserves the open roster-log disclosure, follows new log lines while at the tail, and preserves manual scroll position after the user scrolls away;
- recovery polling runs while Setup is active and stops outside Setup;
- recovery rows use transparent backgrounds and plain dividers; a late regression contract prevents state-colored left-edge stripes from returning;
- required Setup Model name and Base URL controls contain real initial values and remain natively valid before asynchronous config hydration;
- no console or runtime error is recorded by the audit;
- no horizontal overflow, out-of-bounds control, visible native file input, or fake loading row remains;
- the motion review has no blocking finding;
- browser evidence is `/private/tmp/alexandria-phase24b-recovery-audit-final`;
- clone-upload restoration, clone play/pause, readable partial dataset progress, and live Apple Silicon compute values remain covered;
- voice aliases validate targets, self-reference, cycles, and transitive resolution before atomic save; failed updates leave the voice file unchanged;
- inherited Voice cards expose the resolved target, chain, type, and source while keeping prior independent settings dormant and inaccessible;
- changing a target updates dependent summaries without copying configuration, and clearing an alias restores the dormant voice configuration;
- actual single and batch synthesis route only through the resolved target and reject invalid legacy aliases before model initialization or chunk-state writes;
- no `generation_state.json` existed at the start or end of this acceptance run; the suite did not create or discard a production checkpoint;
- `state.json`, `annotated_script.json`, `chunks.json`, `voice_config.json`, and the live capped roster log remained byte-identical across the final suite; `annotated_script.meta.json` remained absent;
- the Audio preparer has a real MLX-Whisper pipeline, confined upload routes, review-required dataset output, and downloadable ZIP results.

## Phase 24C external structured workflow result

At this boundary:

- ordinary ChatGPT handoffs are portable ZIP files; no browser automation, API key, remote bridge, or project-directory access is required;
- Script generation, Script review, roster discovery, roster reconciliation, Persona generation, and visual discovery export Alexandria's native JSON contracts with only explicitly allowed input and stale-result fingerprints;
- handoff ZIPs can be downloaded, their exact prompt can be copied, and their confined folder can be opened in Finder on macOS;
- returned JSON must match the handoff identity, current source/artifact fingerprints, root type, and native stage contract before review;
- roster discovery, roster reconciliation, and Persona results require a separate explicit transfer into their native review artifacts; transfer creates observations, a roster draft, or a Persona draft but never approves them, while visual results remain validation-only;
- direct Script JSON and versioned annotated-script bundles remain non-destructive until explicit candidate application;
- Script review shows verified or unverified provenance, current-to-candidate count deltas, voice/metadata consequences, chunk rebuild, stale-audio impact, warnings, and checkpoint keep/discard/cancel choices;
- unverified imports store no source fingerprint and cannot be presented as source-verified generation;
- the browser sends an opaque candidate ID rather than the full Script or backup payload when applying;
- apply validates the candidate and voice aliases again, snapshots every touched file, rebuilds chunks as pending, preserves prior audio files while marking them stale, and performs exact rollback on write failure;
- same-session undo restores exact pre-import bytes and refuses to overwrite any file changed after the import;
- real-browser verified Script, unverified Script, and structured roster-result native-review states pass at 1440×1000 and 390×844 with no exposed native file input, horizontal overflow, left-edge status stripe, console error, or runtime error;
- final browser evidence is `/private/tmp/alexandria-phase24c-interface-audit-final`.

## Phase 24D Script and roster performance result

At this boundary:

- permanent offline timing coverage runs Alexandria's real Script contract validation, canonical fingerprinting, exact source-fidelity audit, and speaker-run chunk grouping over 6,000 valid entries;
- permanent roster timing coverage validates and fingerprints enough native-schema roster-discovery payloads to cover at least 1,500 observations while respecting any native per-response item cap;
- correctness remains part of the performance gate: silent normalization, malformed fingerprints, fidelity failure, empty chunk output, and invalid native roster results fail before timing is accepted;
- explicit regression budgets reject accidental pathological local behavior without pretending deterministic JSON work predicts Ollama or TTS inference time;
- the Chrome audit records synchronous review-render duration for verified Script import, unverified Script import, and a review-only roster structured-result state containing 1,500 observations;
- each measured browser render must complete within 250 ms while retaining the existing hidden-input, overflow, stripe-free layout, console-error, and runtime-error checks;
- no production validation, fidelity, provenance, candidate-review, or rollback safety was weakened to improve a benchmark;
- browser evidence is `/private/tmp/alexandria-phase24d-performance-audit`.

### Phase 24E external handoff and expressive-audio completion result

The resumed external ChatGPT → Alexandria → expressive-audio lane additionally proves:

- the Script utility-row status is anchored at the far right before the disclosure chevron at desktop and narrow widths rather than floating in the row center;
- roster discovery, roster reconciliation, and Persona results persist as inspected candidates and enter Character roster or Expressive voices only through a separate explicit native-transfer action;
- native transfer revalidates source and artifact fingerprints, rejects competing progress, rolls back incomplete writes, creates only observations/drafts, and never approves a roster, Persona, bank, or production voice;
- Script inspection uses `Current → candidate`, `Use this script`, and `Applied`; unverified source wording receives a prominent warning distinct from ordinary import notes;
- controlled-clone preview generation returns a trusted configuration fingerprint; completed playback is followed by a server confirmation route that issues a short-lived one-time receipt bound to the speaker and exact identity/generation configuration;
- saving a new or changed controlled clone recomputes the configuration fingerprint server-side, consumes the matching receipt, persists no token, rejects replay/mismatch, and requires a new listen after reference-audio bytes or settings change;
- the Expressive voices inspector contains the native reference-bank workflow: owned-identity creation, full-width style rows, hash-verified audio playback, per-reference identity/drift/emotion/pronunciation/pace review, fixed three-mode comparison including long-form drift, explicit bank approval, and separate production assignment;
- reference and comparison audio routes resolve only assets recorded in the current validated bank and reject unknown, missing, escaped, or hash-changed files;
- focused external handoff, controlled-clone, expressive-bank, route, documentation, VM, and interface coverage passes `119/119`;
- complete offline regression passes `1138/1138`;
- real Chrome/CDP acceptance passes all `33` default/state views at desktop and narrow widths with no failures, console/runtime errors, horizontal overflow, or out-of-bounds controls. Evidence: `/private/tmp/alexandria-phase24e-final-browser`;
- the protected production-artifact comparison remains `PROTECTED_ARTIFACTS_PASS`. Evidence: `/private/tmp/alexandria-phase24e-protected-artifacts-final.log`.

## Phase 24F Alexandria Task Bundle v2 result

The current ordinary-ChatGPT workflow additionally proves:

- one shared registry exposes 16 safe task contracts across Script generation/review, roster discovery/reconciliation, bulk Voice-profile generation, single-profile creation/refinement/reconciliation/audit, visual discovery/compilation, advanced acoustic-identity repair, and per-line delivery generation/audit;
- every exported task is one self-contained version 2 ZIP containing immutable task metadata, instructions, minimized input, Alexandria's native schema, reviewed task-specific guidance, checksums, source/dependency fingerprints, and its native review destination;
- a preferred completed ZIP preserves the original members and adds a hash-bound result/completion pair; fallback JSON is matched to the local task library automatically or requests the original task ZIP rather than a typed identifier;
- the visible interface contains one registry-driven **Export task** chooser and one **Import completed task** picker. It contains no handoff ID, code, reference field, copied-prompt step, Finder handoff-folder action, explicit-transfer dead end, or `Other structured tasks` bucket;
- Voice-profile and advanced acoustic-repair bundles carry reviewed Voice Reference guidance that separates stable acoustic identity from line performance, requires exact source-backed `ref_text`, and handles nonhuman anatomy or synthetic mechanisms without generic effects;
- Character roster is the upstream identity authority. Approved entries expose downstream Voice-profile state and the bulk **Create profiles for all speaking identities** action; roster discovery may collect voice clues but cannot create final casting against unresolved identities;
- bulk Voice-profile conflicts render Current and Imported side by side per speaker. New profiles are added as drafts, current profiles remain untouched by default, and only explicitly checked replacements are applied;
- successful import opens Script review, Character roster, Voice profiles & preparation, Visual dossiers, or Editor automatically. Existing-state conflicts preserve the completed result, expose `Reconciliation required` or `Review blocked`, explain what remains safe, and retain an obvious native-review action;
- Voice casting remains the separate production surface for actual model, clone, designed voice, alias, preview, and assignment settings;
- import does not approve a roster, Voice profile, acoustic description, visual dossier, voice assignment, reference bank, adapter, or production output;
- visual discovery/compilation and persistent voice-description tasks are covered through the real native state/draft services and remain unapproved;
- version 1 handoff ZIP/result pairs remain readable for compatibility without restoring their removed user-interface controls;
- focused Task Bundle service, route, transfer, completion-contract, documentation, VM, and interface coverage passes `63/63`;
- the broader Voice-profile architecture, Character roster, Voice-training, Task Bundle, documentation, and UI set passes `92/92`;
- complete offline regression passes `1224/1224`;
- the complete Chrome/CDP matrix passes and records `60` desktop, narrow, and representative-state screenshots, including expanded Task Bundle export/import, the approved-roster Voice-profile handoff, a 1,500-item roster result requiring reconciliation, and narrow Current/Imported Voice-profile comparison;
- no horizontal overflow, out-of-bounds controls, visible native file input, console error, runtime error, or centered disclosure-status regression is present;
- manually reviewed previews are stored under `.omo/evidence/task-bundle-preview`; complete browser evidence is `/private/tmp/alexandria-task-bundle-browser-expanded`;
- protected project/runtime artifacts remain byte-identical or absent. Durable logs and hash receipts are under `.omo/evidence/task-bundle-v2-20260719`.

Automated generation, routing, state, and gate behavior are verified. Human identity, expressivity, pronunciation, pace, and long-form listening judgments remain deliberate user review steps and are not claimed by automated tests. The controlled supplied-clip path is accepted for user testing. Experimental LoRA remains blocked from production assignment until its separate listening and validation gates pass.
