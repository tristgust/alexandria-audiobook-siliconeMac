# Alexandria UI System Memory

## Current decision

The stable interface at commit `92c89d8` is Alexandria's **visual baseline**. It is materially better than the first modular rebuild and should be preserved and improved rather than replaced with a flatter, more generic interpretation.

This is a visual decision, not an architecture rollback:

- Keep the modular shell, components, route owners, API contracts, and direct DOM ownership introduced by B19-T06.
- Do not restore Bootstrap, the monolithic `canonical_interface.js`, retained legacy workspaces, or hidden/reparented legacy DOM.
- Correct the stable build's inaccurate workflow language, stale backend assumptions, accessibility gaps, and incomplete states using current product truth.
- A rebuilt surface is acceptable only when it retains the stable build's hierarchy, density, editorial character, and transport quality while becoming more accurate and maintainable.

`DESIGN.md` remains the detailed token and component contract. This file is the concise cross-session memory.

## Visual thesis

**Soft Editorial Instrumentation:** a warm working edition for audiobook production.

The interface should feel like a serious publishing tool, not a generic SaaS dashboard. Literary hierarchy and calm paper surfaces organize the work; precise controls, state markers, waveforms, and timecodes handle production detail.

## Stable baseline traits to preserve

- Warm parchment canvas with a distinct slightly deeper rail.
- Source Serif 4 for editorial hierarchy and IBM Plex Sans for interface copy.
- Large regular-weight serif page headings, not heavy dashboard headings.
- Compact 224px rail with three persistent groups:
  - Project: Home, Script, Cast, Produce, Export
  - Library: Library, Voices, Templates
  - Settings: Settings, More
- A 3px terracotta current-page rule with quiet ivory selection fill.
- 88px wide/compact global header and 104px project header; narrow headers reflow without duplicating the page title.
- Project header with actual project title, Saved state, centered four-stage tracker, concise blocker/readiness state, and at most one primary action.
- Persistent 80px transport with one tactile 56px play control, thin timeline, context, volume, queue, and overflow.
- Dense bordered publication/list surfaces with internal dividers rather than disconnected cards. Long Script/Produce collections use responsive bounded pages and explicit Load More instead of enormous initial mobile documents.
- Square parchment monograms and restrained cover placeholders rather than generic blue circles.
- Master/detail pages that begin directly with the work. Cast uses visible `Characters` as its h1 rather than wasting vertical space on a redundant Cast title band.
- Project Home with a bordered current-audiobook panel, compact stage trackers, covers, activity, next step, state, and direct actions.
- Script with numbered editorial rows, source context, rectangular issue filters, clear selected-row treatment, and a real context inspector.

## Product truth that overrides the stable snapshot

- Current Project Home → Script → Cast → Produce → Export workflow and terminology.
- Current modular API and route contracts.
- Exact source fidelity, Script lifecycle, roster reconciliation, production Voice validity, audio-currentness, and verified export rules.
- Persona Visual belongs inside the selected Cast profile's Appearance section.
- Selection and workflow status are separate concepts.
- Generated audio is retained as Takes according to current retention rules.
- Model/cache/download/training claims must remain technically truthful.
- Loading, empty, blocked, recoverable, running, canceled, dense, and narrow states are required.

## Foundations

### Color

- Canvas: `#F6F3EC`
- Primary surface: `#FAF8F2`
- Control surface: `#FFFDF9`
- Secondary surface: `#ECE7DF`
- Rail: `#EEE8DE`
- Ink: `#23211E`
- Muted text: `#68635D`
- Mineral teal action: `#3F6E6A`
- Teal soft selection: `#E3ECE8`
- Terracotta current accent: `#C4553D`
- Border: `#D8D0C5`; strong border: `#BDB2A5`

Accent is semantic. Teal means action/selection/focus. Terracotta means current location. Character colors identify a person only.

### Typography

- Editorial: Source Serif 4 with truthful serif fallbacks.
- Interface: IBM Plex Sans with truthful system fallbacks.
- Data/time: IBM Plex Mono.
- Wide page heading: 48/52, regular.
- Compact page heading: 40/44, regular.
- Section heading: 24/30, regular.
- Body: 15/22.
- Metadata floor: 13/18.
- Script prose: 16/24 or slightly larger when space permits.
- Interactive text controls remain 14px at desktop densities but use the dedicated 16px touch-control token in narrow layouts to prevent mobile focus zoom.

### Shape and depth

- 4px base spacing; 8px normal rhythm.
- 6px control radius, 8px panel radius, 12px modal radius.
- Borders and whitespace provide normal separation.
- Shadow is reserved for covers, overlays, popovers, and the tactile play control.
- No glass, glossy chrome, arbitrary gradients, or card stacks.

## Shell behavior

- Rail remains visible in global and project routes; stage links may be unavailable until a project is selected, but the navigation structure does not disappear. Narrow mode is one contained horizontal strip with sticky Alexandria/read-only context, not wrapped multi-row navigation.
- Do not repeat the current project in a sidebar card. The project header owns project identity.
- Global pages use the title/action header as their single visible h1 and do not repeat a second in-page title band.
- Project pages use the 104px project/tracker/action header. Between 641px and 900px, reflow it into a contained two-row header; tracker and actions must never extend below the header over the workspace.
- Page-title bands may be used for Script, Produce, Export, and supporting pages. Cast deliberately starts with the master/detail workspace.
- Inspector is inline at wide widths and overlay below the inspector breakpoint.
- Narrow layout preserves one semantic DOM tree and reflows rather than scaling.

## Interaction hierarchy

1. Current state or blocker.
2. Primary/next safe action.
3. Work content.
4. Secondary controls.
5. Evidence and provenance.
6. Recovery, destructive actions, and technical detail.

Use one page primary. Do not repeat the same state as a badge, banner, paragraph, counter, and disabled button explanation.

External task workflows must show the complete round trip at the point of use. On Script, the required order is: create/export Script → import completed Script task → review and approve Script → create/export Qwen and Fish delivery plan → import completed delivery plan. Never place an importer after an unrelated later stage or rely on copy such as “import below” across a long panel.

Shared composite components must own their base styling in an always-loaded component stylesheet; never make a reusable importer depend on a destination-only stylesheet. Task-import surfaces use the editorial hierarchy, a 20px upload/document icon inside a 40px action-soft tile, a bounded outlined dropzone, compact workflow details, and one stable footer action. Long Script workflows belong in a fixed-header modal with only the modal body scrolling.

## Motion and loading

- One loading signal per state. Do not combine a spinner with animated dots, pulsing skeletons, or an animated indeterminate bar.
- Route transitions use one compact spinner and one stable status line. The shell retains the last resolved project title and never paints an internal project ID.
- Destination-owned headers appear only after the destination mounts; loading chrome must not create a second heading.
- Structure-matched skeletons may accompany one loading status when they clarify the expected layout, but skeletons remain static.
- All decorative motion is disabled under `prefers-reduced-motion: reduce`.
- Determinate progress uses actual values. Indeterminate work must not imply fake percentage progress.

## Component rules

### Buttons and controls

- Primary: mineral teal fill.
- Secondary: ivory fill with strong border.
- Quiet: teal text, transparent surface.
- Destructive: final destructive decision only.
- Fields use visible labels and ivory control surfaces.
- Filter/segment controls are compact rectangles, not pill-heavy badge furniture.
- Icon-only controls require a name and tooltip.
- On narrow screens, stretch the main form submit when useful; keep Return, row actions, and specialist utility actions content-width rather than turning every button into a full-width bar.

### Lists

- Prefer flat rows, alignment, and dividers.
- Each row shows identity, meaningful state, and the next useful action. Do not render disabled play or overflow controls when no action exists.
- Selected rows use teal border/rule plus soft tint; selection is not a status badge.
- Preserve scroll and selection through refreshes. Script pages 30/60/120 rows and Produce pages 30/75/150 rows at narrow/compact/wide widths, with selected-row pinning and Load More.

### Audio

- One persistent full transport per shell.
- Page rows use compact play/waveform controls only.
- The persistent play button is the sole strongly tactile control.
- The shell transport uses back ten seconds, play/pause, forward ten seconds, timecodes, context, volume, queue, and overflow; it does not add redundant previous/next track buttons.
- Waveforms expose numeric alternatives and keyboard seeking.

### Master/detail

- Master list: searchable, filterable, compact state.
- Detail: selected identity, dominant workflow content, evidence, then advanced/provenance.
- Cast defaults to a read-only selected profile. Voice exposes an explicit Edit Voice mode; Character, Appearance, and Advanced are collapsed until requested.
- Edit Voice uses one compact divided configuration sheet with method-specific controls and icon-led facts. Do not rebuild it as disconnected cards, giant read-only fields, or a three-column wall of oversized controls.
- Cast order: identity → Voice → reference/transcript → approved preview → Character → Appearance → Advanced.
- On narrow screens, detail follows the list without creating duplicate DOM. Supporting master lists are height-bounded so the detail is reachable; large secondary identity/usage lists default to a concise summary plus disclosure.
- On desktop, Library, Voices, and Templates use a viewport-bounded master/detail workspace with independent list and detail scrolling. Mixed inventories use quiet semantic group labels rather than one undifferentiated stream.

### Export finishing workflow

- Export is ordered by decision sequence: finish-line readiness and action → publication identity and playback → chapters → output format/location → collapsed technical details.
- The finish line owns the sole Build Audiobook action. Do not duplicate it in the project header.
- Blocked Export states remain actionable: focus missing metadata directly and provide native-stage actions for production or Script/Cast blockers.
- Desktop Export uses one broad publication surface and one narrower assembly column. It must not regress to three equal cards with validation stranded below Output.
- Validation is one compact preflight band, not a second dashboard or repeated set of notices.

## Required states

Every applicable workflow deliberately handles:

- loading
- empty/not started
- ready
- blocked/review required
- running/generating/building
- resumable
- complete/current
- stale
- failed/recoverable error
- canceled
- dense data
- missing cover/portrait/evidence
- narrow viewport

## Navigation and loading performance

- Warm navigation reuses successful page modules and the last verified project catalog; do not repeat module-availability probes or full catalog scans when the same data is already in memory.
- Project Home renders the cached catalog immediately and revalidates in place. A background refresh must never replace usable project rows with a skeleton or disruptive error state.
- Heavy synchronous read routes run in FastAPI's worker pool so a filesystem scan cannot block JavaScript, CSS, or unrelated API responses.
- Hold the runtime-project lock only while a reader depends on mutable global project bindings. Readers that capture explicit immutable project paths release the lock before scanning.
- Dependency indexing accepts plausible path-sized references only. Embedded transcripts, binary payloads, and other multi-megabyte strings are content, not artifact aliases.
- Keep one restrained loading indicator per surface. Faster data flow must not reintroduce competing spinners, animated dots, layout jumps, or fake progress.

### 2026-07-29 measured performance baseline

The Human Nature managed project contains 5,328 Produce chunks. Against that realistic dense state:

- Project Home warm revisit: 5.4–5.6 ms to usable cached content, followed by quiet revalidation;
- Cast: about 0.38 seconds;
- Produce: about 1.25–1.55 seconds, including a 12.5 MB aggregate;
- Export: about 1.58 seconds;
- Library and Voices: generally 1.4–2.8 seconds depending on concurrent scans, down from about 11.2 seconds;
- Templates, Settings, and More: about 13–17 ms.

A background Voice scan must not delay a static asset request or serialize the next Produce read after it has captured the active project paths.

## Verification standard

Before integration:

- Render Project Home, New Project, Script, Cast, Produce, Export, Library, Voices, Templates, Settings, and More against realistic data.
- Compare directly with the stable `92c89d8` baseline and approved references.
- Verify 1536×1024, 1440×1000, 1024×768, and 390×844.
- No horizontal overflow, console exceptions, inaccessible names, hidden duplicate workspaces, raw internal IDs, generic fallback copy, or raw legacy dependencies.
- Keyboard order, focus restoration, forced colors, reduced motion, 200% text reflow, touch-target sizing, and mobile input zoom remain valid.
- Use `tests/interface_holistic_audit.js` as a broad rendered regression; retain the narrower route-specific browser contracts for workflow behavior.
- The stable writable build stays available until the enhanced modular build is visibly and operationally better.

## 2026-07-29 rendered audit baseline

The live modified runtime was checked across 10 primary routes at 1536×1024, 1024×768, and 390×844 (30 route/viewport combinations):

- zero browser errors;
- zero horizontal overflow;
- exactly one visible h1 per route;
- zero heading-level skips;
- zero unlabeled visible controls or fields;
- zero undersized visible interactive targets after excluding the intentionally hidden skip link;
- zero raw project-ID leaks;
- zero generic fallback copy;
- no route with more than one primary action.

The earlier systemic failure—13 visible phone-layout text/search/select controls at 14px—is resolved by the shared narrow control rule and `--type-control-touch-size` token. The post-fix audit reports zero mobile controls below 16px without changing desktop density.
