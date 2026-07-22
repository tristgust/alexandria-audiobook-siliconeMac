# Alexandria Interface Design and Acceptance

## Product direction

Alexandria is a desktop audiobook-production workspace. It is not a generic SaaS dashboard, a marketing page, or a visual imitation of iOS.

The interface should feel calm, precise, editorial, and technically trustworthy. The current task, blocking issue, and next safe action take priority over decoration.

## Accepted application shell

- Compact dark horizontal topbar.
- Five numbered production stages: `1 Setup`, `2 Script`, `3 Characters`, `4 Editor`, `5 Result`.
- Auxiliary workflows remain in a menu labeled exactly `Tools`.
- `Tools` contains Advanced identity operations, Voice designer, Audio preparer, Dataset builder, and Voice training. Character roster, voice personas, production casting, and visual dossiers are not separate destinations; they belong to the selected character.
- Alexandria brand only; no subtitle.
- No active underline or progress rail in navigation.
- Compute and Disk remain flat icon/value readouts. CUDA systems show GPU memory; Apple Silicon shows live CPU activity with system memory, Alexandria RSS, and MLX active memory in the tooltip. A quiet em dash is reserved for genuine measurement failure.
- Stable hash navigation, `aria-current`, skip navigation, visible focus, and responsive compact navigation.

Rejected shell experiments:

- Persistent left sidebar.
- Full-page white content shell.
- Contents rail, vertical guide, timeline treatment, or sticky horizontal Contents navigator on Setup.
- Generic cards used only to fill empty space.

## Workspace patterns

### Setup

Setup uses an open settings editor plus a sticky `Current configuration` inspector. One divider separates major setting groups. Save settings remains in the bottom action bar.

Runtime diagnostics, Stage model profiles, and Advanced generation share one disclosure system. Closed chevrons point right; open chevrons point down. Prompt templates are separate nested disclosures.

Stage model profiles use one inherited-by-default master/detail inspector. Same-model runtime overrides save without comparison evidence. A proposed model change reveals the measured evidence contract and is rejected until benchmark, quality, fidelity, runtime, and regression gates all pass. Profile saves use optimistic fingerprints and remain separate from the global Setup save state.

Required Model name and Base URL controls include real initial values rather than placeholder-only defaults, so browser validation is truthful before configuration hydration. `Project status` is one compact collapsed disclosure by default. Its expanded view restores the saved source and renders the seven recovery stages as flat operational rows with plain dividers, exact actions, and progressively disclosed capped logs. Polling preserves open logs and manual scroll position; it stops outside Setup.

The Script workspace also owns one secondary collapsed `Work with ChatGPT` disclosure. It is not a parallel dashboard or setup wizard. Alexandria Task Bundles reduce the surface to two actions: a registry-driven **Export task** chooser and one **Import completed task** picker. The interface does not expose an internal task/handoff ID, code, reference field, prompt-copy step, Finder handoff folder, or an `Other structured tasks` dumping ground. A completed ZIP is self-contained; fallback JSON is matched to the local task library automatically or asks for the original task ZIP. Import identifies the task and opens Script review, Characters, or Editor as appropriate. Roster and visual results route to the relevant Characters review state; voice-persona results route to the matching selected character. Import never approves, assigns, or silently replaces project state. Conflicts remain persisted and show `Reconciliation required` or `Review blocked` plus a visible native-review action. Bulk voice-persona conflicts compare Current and Imported per character and apply only explicitly selected replacements. Verified and unverified Script provenance remain visually and semantically distinct, and Script review shows current-to-candidate count deltas plus checkpoint, voice, metadata, chunk, and stale-audio consequences before `Use this script`.

### Script

Script separates source/generation from the current run. Generate is the dominant action. Review, provenance, recovery, and discard controls remain secondary or progressively disclosed.

Setup includes one collapsed **Local model cache** inventory. It uses plain resource rows for every pinned model, showing purpose, required/optional state, immutable revision, location, installed/estimated size, validation failures, and one explicit Download or Repair action. Opening Setup is read-only. A normal synthesis, transcription, or training-preparation path never downloads a model; missing and incomplete snapshots fail with a direct path back to this inventory. Background operations name the current model and count, and disk/network failures remain actionable.

### Characters

Characters is the canonical master/detail workspace. One list covers roster drafts and approved characters; there is no competing Voice casting, Voice profiles, or Visual dossiers list. Draft rows emphasize only unresolved or duplicate identity decisions. Resolved rows need no individual approval and enter the approved roster through one bulk action. A reviewed reconciliation draft can replace the current approval through a two-fingerprint guard and exact versioned rollback.

For a resolved speaking character, inspector order is task-first: character header, one primary **Voice** section, collapsed **Appearance**, collapsed **Character details**, and collapsed **More voice tools**. Non-speaking characters keep identity and appearance information without irrelevant voice controls. Preparation-only identity fields never appear beside or beneath Voice by default.

Voice chooses the actual model, clone, controlled clone, designed voice, adapter, or alias. Full roster names do not have to equal short Script labels: a shared deterministic resolver uses canonical/display names, alternate names, then unique representative-line evidence. Clone editors use explicit Reference source, Exact reference transcript, Persistent identity note, and Reference audio fields. Uploading a reference restores the same selected character and disclosure state. Standard Qwen clone remains the saved default. Controlled supplied-clip use requires a non-mutating preview, completed playback, and a server receipt bound to the exact character voice configuration; any relevant edit invalidates it and restores the standard backend.

The production Voice controls own the ordinary identity description, clone transcript/audio, persistent note, preview/listen state, and save behavior. Existing imported preparation-identity conflicts are shown Current versus Imported per character, and only explicitly selected replacements are applied. Separate reference/training identity metadata is visible only inside **More voice tools**, together with synthetic or owned-recording preparation, reference-bank review, datasets, adapter provenance, and technical fingerprints.

Appearance is a compact selected-character disclosure. It shows source-backed summary and a few stable traits first, with variants, conflicts, unknowns, and evidence disclosed below. Collect, resume, cancel, discard, and refresh actions remain contextual to that character.

Routine approved-identity work—canonical/display rename, add/remove aliases, and compact exact Script-line inspection—lives in Characters. Advanced identity operations remains a contextual tool for reassignment, merge, split, operation history, audio invalidation, and exact undo because those actions affect multiple characters or chunk groups. Production voice aliases remain edited in the selected character’s production voice card; dormant independent settings survive reversible unaliasing.

Specialist Voice designer, Audio preparer, Dataset builder, Voice training, and Advanced identity operations preserve the selected stable character ID, show a contextual banner, prefill sensible character-specific fields where applicable, and return to the same Characters inspector.

The expressive reference bank lives in the selected character. Its first decision is the identity source. For a user-owned clone, the supplied recording, exact transcript, and audio fingerprint are canonical; VoiceDesign cannot silently redefine the speaker. Required styles appear as compact full-width rows with source kind, hash-verified audio, identity/drift/emotion/pronunciation/pace checks, notes, and explicit replacement controls. Neutral remains the required fallback. The fixed comparison aligns the bank, single neutral clone, and direct-design comparator on identical lines and adds identity-consistency plus long-form-drift review. Explicit bank approval precedes a separate assignment decision. Raw mapping diagnostics, paths, hashes, and model provenance remain collapsed.

### Data and audio tools

Audio preparer separates source intake from filtering/output settings. It supports confined single or batch uploads, MLX-Whisper transcription, clip-confidence/SNR filters, a live log, and a downloadable review-required dataset ZIP. Prepared output is not silently treated as approved training data.

Dataset builder uses a dense desktop table and labeled stacked sample rows on narrow screens. Loading, empty, and error states replace the table instead of appearing as fake table rows. Progress count and percentage sit outside the fill bar so they remain readable at low completion values.

Voice training treats expressive custom voices as the primary workflow. When opened from Characters, it retains the selected character context and returns there directly. The default surface explains the supplied-identity → reviewed-reference → per-line matching path and presents measured MLX performance without implying that clone expressivity depends on LoRA. Dataset intake remains available. Adapter settings, progress, testing, and downloads live in a collapsed experimental disclosure and remain disabled until a separately validated backend reports support. Existing adapter artifacts remain visible as responsive rows with deletion available even when inference is unavailable.

The technically validated isolated training sidecar belongs in that existing experimental disclosure rather than a separate page. Its order remains environment/binary readiness, model and LoRA-target probes, bounded train → merge → MLX export setup, progress/logs, validation audio, then an experimental artifact manifest. Production assignment remains unavailable until manual listening and multi-sample/multi-epoch quality gates pass.

Editor keeps the chunk table as the primary workspace. Loading, empty, and error states replace the table. Render progress is hidden until real chunk data exists.

Result provides the player/export workflow when complete and a direct Editor handoff when empty.

## Component rules

- One dominant primary action per workflow region.
- Destructive actions are separated and confirmed.
- Helper text must explain behavior, consequence, or a non-obvious constraint. It must not repeat the label.
- Visible native browser file-input chrome is prohibited. Use the shared accessible picker with selected filename, accepted formats, choose/replace action, keyboard focus, and drag/drop feedback.
- Alerts, warnings, errors, toasts, summaries, and status callouts never use colored left-edge accent stripes. Use a quiet uniform border; state comes from wording and a restrained semantic icon or marker.
- Resource libraries use aligned responsive rows. Tables are reserved for tasks that require column comparison.
- Dashboard-style KPI tiles are prohibited for workflow readiness. Use aligned label/value facts and one explicit blocker list.
- Technical IDs, fingerprints, paths, provenance, and diagnostics remain progressively disclosed unless they block trust or compatibility.

## Motion contract

Motion is allowed only for feedback, spatial consistency, state indication, explanation, or preventing a jarring change.

| Surface | Behavior | Review | Verdict |
| --- | --- | --- | --- |
| Navigation | Color/background feedback, 120 ms | Frequent; no displacement | Approved |
| Button press | 1 px / 0.985 pointer press, 100 ms | Gated to fine pointers; keyboard actions do not move | Approved |
| Disclosures | Reversible right-to-down chevron, 160 ms | Communicates state and origin | Approved |
| File picker | Border/background/focus feedback, 140 ms | Explains hover, focus, and drag state | Approved |
| Master-list rows | Background/selection feedback, 120 ms | Near-instant; no page movement | Approved |
| Progress updates | Instant width updates; color may transition 140 ms | Removed animated width because polling updates are frequent | Approved after remediation |
| Spinners | Running-state indication only | Disabled for reduced motion | Approved |
| Modals and toasts | Existing Bootstrap entrance/exit with reduced-motion override | Occasional and centered/anchored | Approved |
| Decorative looping animation | None | Prohibited | Approved |

Static audit confirms no `transition: all`, standalone `ease-in`, `scale(0)`, decorative keyframes, or transitions of width/height/margin/padding/top/left.

`prefers-reduced-motion` removes displacement and looping spin. Reduced transparency and increased contrast receive solid-background and stronger-border adaptations.

**Motion verdict: APPROVE.** No blocking motion finding remains.

## Acceptance evidence

Current verified interface evidence:

- Complete combined offline suite: 983/983 tests pass at the Phase 24B boundary.
- Focused Phase 24B recovery, Setup, process, log, UI, repair, and documentation contracts: 107/107 PASS.
- All-tab Chrome/CDP audit at 1440×1000 and 390×844 passes; evidence is `/private/tmp/alexandria-phase24b-recovery-audit-final`.
- The recovery disclosure is compact when closed and verifies saved-source restoration, all seven stage rows, exact actions, persisted roster logs, tail following, manual-scroll preservation, and polling shutdown outside Setup when open.
- The external structured workflow is collapsed by default; native file inputs remain visually hidden behind explicit labels; verified Script, unverified Script, and review-only structured-result states pass at desktop and narrow widths.
- External workflow command rows use plain dividers rather than colored left-edge status stripes. Candidate review exposes exact current-to-imported totals, persistent provenance, explicit checkpoint choices, and a same-session exact rollback action.
- Verified Script review, unverified Script review, and review-only roster-result rendering are measured in the real Chrome audit. Each synchronous review render must remain within 250 ms at its tested desktop or narrow viewport without weakening existing accessibility or layout checks.
- No horizontal overflow or out-of-bounds controls.
- No visible native file inputs.
- No loading state rendered as a fake table row.
- No console or runtime errors in the audited workflow.
- File-picker drag state, Setup disclosures, mobile navigation, custom dialogs, stripe-free toasts, dense Dataset Builder data, Character visuals, and Voice profiles & preparation are captured as representative states.
- Expressive voice persona approval persists and refreshes the project fingerprint.
- Explicit synthetic-project creation persists as `draft`.
- Speaker management renders real script lines, renames a speaker through the production route, records generated-audio invalidation, preserves the stable character ID, and restores the exact previous identity through undo.
- Stage profiles save a Script-only context override while retaining the global model, reject an incomplete model-changing edit with the evidence panel visible, and remove cleanly back to global inheritance.
- Voice training renders the measured MLX and controlled-clone paths at both widths, preserves dataset management, and does not expose unsupported shared-runtime adapter train/test/download actions. The controlled-clone production handoff is in Voice casting; Voice profiles & preparation remains the multi-reference/dataset path.
- The controlled supplied-clip browser flow verifies direct capability initialization, an explicit response listen gate, playback-plus-ended approval, persisted backend/settings after reload, and automatic persisted fallback to Qwen Base after any bound edit.
- User-test regressions verify that clone upload restores the active panel, reference audio visibly toggles Play/Pause, partial dataset progress remains readable, and Apple Silicon displays live compute activity instead of a misleading em dash.
- Audio preparer source contracts and live smoke evidence cover public model download under Pinokio's stale-token environment, transcription, segmentation, quality filtering, atomic review package creation, and output download.
- Source-mismatch warnings are regression-tested with uniform 1 px borders on all sides; no accent stripe is permitted.
- Recovery stages are transparent flat rows, not status cards. A late CSS contract prevents state-colored left-edge stripes from returning at either viewport.

Live user review established the accepted shell and rejected the sidebar, full content shell, Contents navigation experiments, excessive rules, generic disclosure styling, native Browse controls, generic loading tables, and alert side stripes.

## Final review checklist

- Current state and next safe action are identifiable in one scan.
- No routine section exists only to fill space.
- No auxiliary tool is promoted into the five-stage production sequence.
- No alert, toast, warning, or callout uses a colored edge stripe.
- No readiness section falls back to KPI tiles or dashboard counters.
- No important technical detail dominates the default workflow.
- Loading, empty, error, running, stale, incompatible, dense, destructive, and narrow states are deliberate.
- Focus order and focus visibility remain usable.
- Motion passes the purpose/frequency gate and is not required for comprehension.
