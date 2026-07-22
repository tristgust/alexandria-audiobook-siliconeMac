# Alexandria Canonical Interface System

**Status:** Approved implementation contract

**Direction:** Soft Editorial Instrumentation

**Updated:** 2026-07-20

**Applies to:** Alexandria desktop shell, Project Home, New Project, Script, Cast, Produce, Export, Library, Settings, and specialist tools.

This document replaces the previous dark horizontal-topbar system. The approved application is a warm, literary, mostly flat desktop instrument with selective tactile treatment for playback and other physical-feeling controls. It is not a generic dashboard, an engineering console, or a stack of decorative cards.

## 1. Product character

Alexandria should feel:

- editorial rather than corporate;
- calm rather than sparse;
- precise rather than technical-looking;
- warm without becoming nostalgic or ornamental;
- mostly flat, with depth reserved for things that move, float, or require confirmation;
- text-centered in Script, audio-centered in Produce, and publication-centered in Export.

The interface may use restrained whimsy in book covers, character portraits, empty states, and small illustrations. Whimsy must never weaken workflow clarity.

## 2. Canonical tokens

### Color

| Token | Value | Use |
|---|---:|---|
| Canvas | `#F6F3EC` | Application background |
| Surface | `#FAF8F2` | Panels, modal surfaces, rail highlights |
| Muted surface | `#ECE7DF` | Quiet control fill, separators, subtle selected backgrounds |
| Ink | `#23211E` | Primary text |
| Muted ink | `#68635D` | Secondary text and metadata |
| Line | `#D8D0C5` | Ordinary borders and dividers |
| Strong line | `#BDB2A5` | Controls and higher-emphasis boundaries |
| Teal | `#3F6E6A` | Primary actions, progress, focus, active audio controls |
| Teal hover | `#315B58` | Primary hover and pressed state |
| Teal soft | `#E3ECE8` | Quiet selected and current-stage state |
| Terracotta | `#C4553D` | Brand mark and selective warm emphasis |
| Terracotta text | `#9B3F2C` | Warm labels with sufficient contrast |
| Success | `#356A54` | Complete and approved states |
| Warning | `#8A631A` | Review, stale, and attention states |
| Danger | `#9A4038` | Failed, blocked, destructive, and invalid states |
| Information | `#496C7C` | Informational notices |

Do not introduce a second primary accent. Teal owns primary actions. Terracotta is a warm identity accent, not a competing button color.

### Typography

| Role | Family | Size | Weight | Notes |
|---|---|---:|---:|---|
| Page title | Source Serif 4 | 40 px wide / 36 px compact | 600 | Tight editorial tracking |
| Section title | Source Serif 4 | 24 px | 600 | Used sparingly |
| Modal title | Source Serif 4 | 28 px | 600 | One per dialog |
| Row or control heading | IBM Plex Sans | 15–16 px | 650–700 | Operational hierarchy |
| Body | IBM Plex Sans | 15 px | 400–500 | Default application copy |
| Secondary body | IBM Plex Sans | 14 px | 400–500 | Supporting explanations |
| Metadata and labels | IBM Plex Sans | 13 px | 600–700 | Never below 13 px |
| Technical data | IBM Plex Mono | 13 px | 400–600 | Time, hashes, filenames, numeric state |

No visible shell or workflow text may render below 13 px. Avoid all-caps except short metadata kickers and rail section labels.

### Spacing

Use a 4 px base scale. Preferred values:

- 4 px: micro separation;
- 8 px: related inline controls;
- 12 px: compact row padding;
- 16 px: standard row and field gap;
- 20 px: compact panel padding;
- 24 px: section and wide panel padding;
- 32 px: wide page gutter and major section spacing;
- 40–48 px: rare page-level separation.

### Borders and radii

- control radius: 6 px;
- panel radius: 8 px;
- modal radius: 12 px;
- pill radius: only progress tracks, compact state dots, or genuinely pill-shaped controls;
- ordinary border: 1 px solid Line;
- emphasized control border: 1 px solid Strong line.

Do not use large soft radii on ordinary panels. Avoid nested rounded rectangles.

### Shadows

Ordinary panels, rows, headers, and cards use no shadow.

Depth is reserved for:

- tactile playback controls;
- popovers and menus;
- overlay inspectors;
- modal dialogs;
- drag previews or transient floating objects.

## 3. Application shell

### Wide reference: 1536 × 1024

- left rail: 224 px;
- content gutter: 32 px left and right;
- global header: 88 px;
- project header: 104 px;
- persistent player: 80 px when a track exists;
- persistent player: hidden when no track exists;
- inspector: 360 px expanded, 40 px collapsed.

### Compact reference: 1024 × 768

- left rail: 184 px;
- content gutter: 24 px left and right;
- global header: 88 px;
- project header: 104 px;
- player: 80 px when active;
- inspectors become overlays below 1180 px rather than crushing the work area.

### Small window

Below 760 px:

- rail becomes an off-canvas drawer;
- page header stacks cleanly;
- project stages remain horizontally scrollable;
- modal form fields collapse to one column;
- persistent player reduces to primary playback and identity;
- primary actions remain visible without horizontal overflow.

## 4. Navigation

### Global navigation

The rail always provides:

- Home;
- Library;
- Settings;
- More.

Home and Library are global destinations. Settings and More are utilities. They are not production stages.

### Project navigation

Script, Cast, Produce, and Export appear only when a project stage is active. The project-stage group must not occupy the global Home or Library state.

Selected navigation is quiet:

- warm surface fill;
- 3 px terracotta indicator;
- dark ink label;
- no oversized pill, inset shadow, or glowing treatment.

Project stages may show numbered circles before completion and check marks after completion. Complete, current, blocked, and future states must remain distinguishable without relying on color alone.

## 5. Shared page header

Each destination has one shared header.

Global header contains:

- page title;
- one-line purpose or state;
- optional single primary action.

Project header contains:

- project identity eyebrow;
- page title;
- concise stage state;
- save state and workflow state;
- optional single primary action.

Do not repeat a second page title inside the page surface. Old internal pane headings may be hidden when the shared header owns the title.

The project stage tracker sits immediately below the project header. It is not shown on global pages.

## 6. Actions and controls

### Primary action

A page has one primary action. It uses teal and appears in the shared header or the dominant workflow region—not both.

Examples:

- Home: New Project;
- Script: Approve Script;
- Produce: Generate missing and stale audio;
- Export: Build Audiobook.

Disabled primary actions must remain legible and must have a nearby explanation of what blocks them.

### Secondary and tertiary actions

- secondary: bordered neutral button;
- tertiary: text or icon button with explicit accessible name;
- destructive: danger styling only inside a clear destructive context;
- icon-only controls: minimum 40 × 40 px with `aria-label` or title.

### Forms

- controls are 40 px minimum height;
- compact controls are 32 px minimum height;
- labels sit above fields;
- help text explains consequence, not implementation;
- validation appears next to the affected field or transaction;
- changing a source or candidate never destroys the previous valid selection until the replacement validates.

### Radio choices

Method and preset choices use one flat bordered group. Each choice contains:

- native radio control;
- concise label;
- one-line consequence.

Selected choices receive a quiet teal-tinted background. Do not turn each choice into a separate elevated card.

## 7. Panels, rows, and hierarchy

Prefer hierarchy through:

- typography;
- spacing;
- rules and dividers;
- full-width rows;
- quiet surface contrast;
- one selected state.

Use a panel only when content needs a real boundary. Do not wrap every subsection in its own card.

A standard flat panel uses:

- Surface background;
- 1 px Line border;
- 8 px radius;
- no shadow.

Rows inside a panel share the outer boundary and use internal dividers.

## 8. Status and validation

Use operational language:

- Ready;
- Needs review;
- Missing voice;
- Identity conflict;
- Needs listening;
- Stale;
- Failed;
- Complete;
- Blocked.

Notices use a full border and restrained tinted background. Avoid large decorative warning panels and left-side status stripes.

A notice contains:

- state icon;
- concise statement;
- optional action.

Failures must explain the next safe action. Completion states should be calm, not celebratory marketing panels.

## 9. Progress

Progress is shown only for work that is actually running or queued.

A progress treatment includes:

- plain label;
- numeric or item count when known;
- 8 px progress track;
- cancel or retry only when supported.

Do not use decorative charts for production progress.

## 10. Inspector

Canonical inspector widths:

- expanded: 360 px;
- collapsed: 40 px.

The inspector contains selected-object detail and secondary actions. It must not duplicate the master list.

Below 1180 px, an expanded inspector overlays the right side of the content with a popover shadow. It remains dismissible and does not permanently compress the central work area.

## 11. Persistent player

There is one persistent bottom player for completed or previewable audiobook audio.

When a track exists, the 80 px player contains:

- tactile circular play/pause;
- current title and context;
- skip backward and forward;
- timeline;
- elapsed and total time;
- volume;
- playback speed.

At compact sizes, secondary controls may collapse. Playback identity and play/pause remain.

When no track exists, hide the player. Do not show an empty transport and do not render a second full transport elsewhere. Embedded compact players may exist for Script entries, character previews, and Produce chunks, but they must not duplicate the full persistent transport.

## 12. Modal workflow

Modal dialogs use:

- 12 px radius;
- Surface background;
- modal shadow;
- clear title and consequence;
- scrollable body;
- footer that remains reachable and visible;
- Cancel before the primary action;
- no unrelated page navigation inside the dialog.

### New Project

New Project is one coherent scrollable form, not a wizard and not a faux stepper.

Required sections:

1. Source file;
2. Project and book identity;
3. Source and output language;
4. Generation method;
5. Preset.

A valid EPUB inspection shows:

- cover when present;
- extracted title;
- extracted author;
- language;
- chapter count;
- source filename.

The user can correct extracted title and author before creation.

Normal New Project flow must not expose:

- model names;
- cache locations;
- context length;
- prompt templates;
- low-level runtime switches.

Advanced options are collapsed by default and explain only information necessary to understand the transaction. Project creation routes onward only after managed runtime activation succeeds; a failed activation keeps the safely created project available and reports the failure without pretending it is active.

## 13. Page-specific emphasis

### Home

- continuation card first when an active or selected project exists;
- search, sort, and filter in one compact toolbar;
- project rows, not a mosaic of cards;
- each row shows identity, current state, next stage, activity, and one clear open/resume action.

### Script

- text is the visual center;
- selected issue and exact source comparison dominate;
- issue filters and search remain secondary;
- approval is the sole primary action;
- provenance and generation details stay collapsed.

### Cast

- one character list;
- selected character inspector;
- production voice first;
- compact Character and Appearance summaries;
- identity conflicts and missing voices are explicit;
- expressive clone validation includes exact reference text, listening evidence, and identity-retention state.

### Produce

- audio rows are the center;
- filters correspond to actionable production states;
- waveform and playback are compact but clear;
- failures explain cause and safe retry;
- Regenerate All remains destructive and visually subordinate.

### Export

- readiness and Build Audiobook first;
- metadata, cover, chapters, format, and destination read as one publication workflow;
- technical detail is lower in the hierarchy;
- completion is warm and restrained.

### Templates

- use the same flat supporting master/detail layout as Library and Help;
- one shared-header primary action creates a custom template;
- rows show named intent, generation method, preset, language, and default state;
- built-ins are visibly immutable but remain duplicable;
- custom edit uses one coherent modal with ordinary New Project fields only;
- model names, prompts, context limits, cache locations, credentials, and fingerprints remain absent from normal UI;
- delete impact explains historical project usage and confirms that existing projects are not rewritten;
- applying a template is visibly acknowledged inside New Project and provenance clears when method, preset, or language changes.

### Settings

- normal Settings contains ordinary project defaults, provider connection, speech defaults, accessibility, and storage policy only;
- use flat full-width sections plus one restrained sticky summary at wide widths;
- use one sticky Save Settings action bar; validation stays inline and failed saves retain edits;
- API-key values are never populated into the form; show configured state and explicit preserve/replace/clear intent;
- required workflow contracts such as structured output are visible but not user-disableable;
- runtime diagnostics, preload/unload, model-cache repair/download, migration, recovery, raw prompts, and sampling controls route to Maintenance or another specialist destination;
- retention policy must state whether enforcement exists; never imply that saving policy deleted or scheduled files;
- motion, contrast, density, and status-announcement preferences preview immediately and persist only after save;
- compact layouts collapse the summary and fields to one column without replacing labels with tooltips.

## 14. Accessibility

- visible keyboard focus uses a 2 px teal outline with 2 px offset;
- programmatically focused page roots do not receive a full-page outline;
- interactive targets are at least 40 × 40 px unless compact native controls provide equivalent usability;
- state is not communicated by color alone;
- icon-only controls have accessible names;
- dialogs have labelled titles and descriptions;
- live transactional states use polite status regions;
- text remains readable at compact width without horizontal scrolling;
- reduced-motion preference disables nonessential transitions and shimmer.

## 15. Prohibited patterns

Do not reintroduce:

- dark horizontal topbar navigation;
- global production stages always visible;
- Setup as the Home page;
- duplicate page titles;
- decorative dashboard analytics;
- cards around every subsection;
- asymmetric warning side stripes;
- ordinary panel shadows;
- large pill-selected navigation;
- faux multistep indicators in a single form;
- multiple equivalent primary actions;
- duplicate full audio transports;
- shell text below 13 px;
- generic placeholder copy such as “Status / Blocker” or “Primary action.”

## 16. External Script workflow boundary

Ordinary-ChatGPT work remains an explicit Alexandria Task Bundle workflow. It is not an embedded provider and must not be presented as ordinary in-app generation.

The canonical Script interface preserves the **Phase 24C external-workflow browser evidence** contract:

- Work with ChatGPT creates or resumes an opaque Task Bundle;
- the user carries the bundle through an ordinary ChatGPT conversation;
- returned candidates enter Alexandria through native review and source-fidelity validation;
- source application, checkpoints, rollback, and provenance remain Alexandria-owned;
- the external workflow must not create a second Script editor, project state store, or hidden API dependency.

## 17. Implementation map

The canonical shell currently lives in:

- `app/static/index.html` — shell markup, canonical tokens, responsive rules, modal and player surfaces;
- `app/static/navigation_routes.js` — semantic routes, history, legacy aliases, and entity context;
- `app/static/canonical_interface.js` — shared shell state, Project Home, New Project, stage tracker, and persistent-player behavior.

Interface changes must preserve existing backend API contracts unless a proven workflow gap is documented before the additive route or service change.
