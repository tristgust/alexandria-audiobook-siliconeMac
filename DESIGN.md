# Alexandria Design System

This is the implementation contract for Alexandria's canonical interface. It replaces the implicit deep-blue/system-sans rules in `.design-system/codex-ui-system.md` for all B19-T06 work. Product surfaces must use the tokens and primitives named here; they must not restyle, hide, move, or query a retained legacy workspace.

## 0. Reference authority and extraction map

### Precedence

When sources disagree, separate product truth from visual truth instead of averaging measurements:

1. `Alexandria_Interface_System_Audit.docx` and `Alexandria_Complete_Implementation_Specification.docx` govern workflow, terminology, data truth, state, accessibility, and safety.
2. Stable interface snapshot `92c89d8` governs the proven visual hierarchy, density, editorial typography, rail/header/player composition, list treatment, and control character.
3. Final page concept for the surface governs missing content and page-specific composition, corrected to the stable visual language.
4. Relevant workflow component sheet governs component anatomy.
5. Foundation design-system board governs palette and type roles.
6. Shell B governs structural intent; Shell A is the same shell with its inspector open.

Static labels, states, geometry, and action hierarchy are normative. Sample book titles, people, covers, counts, durations, paths, dates, and generated portraits are illustrative data only.

### Packet map

| Source | Authority and extraction |
| --- | --- |
| `Alexandria_Interface_System_Audit.docx` | Highest visual authority. Supplies Soft Editorial Instrumentation, fixed shell, action hierarchy, accessible color roles, flatness, status vocabulary, and corrections. |
| `Alexandria_Complete_Implementation_Specification.docx` | Product-state, workflow, labeling, keyboard, screen-reader, responsive, and acceptance contract. Visual geometry is reconciled through the approved stable baseline below. |
| Stable interface snapshot `92c89d8` | Primary visual baseline. Preserve its warm editorial hierarchy, 88px global header, 104px project header, persistent tactile transport, compact rail groups, dense master/detail composition, square parchment monograms, and restrained bordered surfaces. Do not preserve its inaccurate workflow labels, stale backend assumptions, Bootstrap dependency, or monolithic DOM. |
| `phase1_designBoard.png` | Supporting palette, type families, control anatomy, character/audio language. Black rectangle lockup, undersized type, free accent use, and decorative furniture are rejected. |
| `phase2_shellA.png` | Open-inspector state only. The 360px inspector is useful; vertical label, feather, and differing proportions are rejected. |
| `phase2_shellB.png` | Structural shell baseline. Audit/Spec geometry and navigation inventory override its omissions. |
| `phase3a_castComponents.png` | Character-list and voice-control anatomy. Selected detail is flattened; Identity conflict becomes Identity review; Selected is interaction state. |
| `phase3b1_scriptComponents.png` | Primary Script component contributor. Text-led editorial rows survive; decorative board furniture and visible backend provenance do not. |
| `phase3b2_scriptComponents.png` | Supporting Script-entry anatomy only. Its competing portrait/density/status system is rejected. |
| `phase3c_audioProductionComponents.png` | Produce operational source. Selection and audio state stay separate; the selected sample is Stale; page transport is compact. |
| `phase3d_navigationStatusComponents.png` | Navigation, status, notice, progress, and dialog anatomy. Audit/Spec inventory, keyboard states, and restrained semantics override it. |
| `phase4a_home.png` | Home content and continuation anatomy, rendered in the stable baseline's Project/Library/Settings rail, bordered continuation publication panel, compact stage trackers, dense project rows, and persistent 80px player. |
| `phase4b_newProject.png` | New Project content as one coherent form with flat options, the reference five-step orientation strip, and a quiet Advanced disclosure; the strip does not create separate wizard pages. |
| `phase4c_scripts.png` | Script page composition, corrected to canonical title size, flatter source/rows, clearer review actions, one blocking summary. |
| `phase4d_cast.png` | Cast content contract. The stable baseline's direct Characters master/detail composition is preferred over a redundant full-width Cast title band; the visible `Characters` heading is the route h1. Voice remains first, with quiet Saved state and subordinate Appearance. |
| `phase4e_production.png` | Invalid. SHA-256 matches `phase4d_cast.png`; it must never govern Produce. |
| `phase4f_export.png` | Export composition, corrected to one readiness signal, flatter sections/radios, explicit filenames, and no second full transport. |

### Resolved contradictions and approved deviations

- The product-wide 13px floor overrides the nominal 12px utility/timecode examples. `--type-utility-size` and `--type-mono-size` are 13px; line heights remain 16px/18px as documented below.
- Shell A and Shell B are states of one shell; geometry does not change between them.
- `phase4e_production.png` is excluded. Produce uses the Spec plus `phase3c_audioProductionComponents.png`.
- Settings and Maintenance have no final PNG. They inherit this exact shell and primitive system; their information architecture comes from their established product contracts, never legacy Setup markup.
- No external font download is permitted in this task. Named font stacks are truthful and fall back locally until licensed font files are supplied; see §8, `FND-001`.
- No decorative gradients are part of Alexandria's product material. Real cover art may contain gradients; interface depth comes from paper tones, borders, whitespace, and the four narrowly permitted shadow recipes.
- Stable `92c89d8` is a visual reference only. The implementation remains modular and may not query, move, embed, or restore its legacy DOM or Bootstrap scaffolding.

## 1. Atmosphere and identity

**Soft Editorial Instrumentation** feels like a well-made working edition: warm parchment and ivory, near-black ink, literary serif hierarchy, and precise production controls. The signature is the tension between calm bookmaking surfaces and exact audio instrumentation: most content is flat and editorial; the primary play control alone feels tactile.

Use the terracotta open-book mark with a black wordmark when a brand mark is needed. Do not use the black rectangular lockup. Restraint, typography, source material, and measured spacing carry the literary character. Feathers are reserved for a genuine empty state or rare completion moment, never shell furniture.

Anti-references are generic blue SaaS dashboards, gradients, glass, nested card stacks, ornamental component boards, badge walls, fake covers/portraits, repeated status telemetry, and backend detail presented as normal workflow content.

## 2. Color

### Canonical tokens

| Role | CSS token | Value | Permitted use |
| --- | --- | --- | --- |
| Canvas | `--color-canvas` | `#F6F3EC` | Application and showcase canvas |
| Primary surface | `--color-surface-primary` | `#FAF8F2` | Main panels, fields, modal body |
| Secondary surface | `--color-surface-secondary` | `#ECE7DF` | Warm grouping and separators |
| Control surface | `--color-surface-control` | `#FFFDF9` | Fields, buttons, player, focused paper surfaces |
| Rail surface | `--color-surface-rail` | `#EEE8DE` | Stable navigation rail |
| Primary text | `--color-text-primary` | `#23211E` | Body, headings, icons |
| Secondary text | `--color-text-secondary` | `#68635D` | Supporting copy; must retain AA contrast |
| Disabled text | `--color-text-disabled` | `#7A746D` | Disabled labels on light neutral surfaces only |
| Primary action/focus | `--color-action-primary` | `#3F6E6A` | Filled primary actions, selected controls, focus |
| Primary action hover | `--color-action-hover` | `#315B58` | Primary hover |
| Primary action pressed | `--color-action-pressed` | `#294E4B` | Primary pressed |
| Primary action soft | `--color-action-soft` | `#E3ECE8` | Selected editorial row/control tint |
| Current accent | `--color-accent-current` | `#C4553D` | Stage/node/nav rule/illustration; never small text |
| Current text | `--color-accent-current-text` | `#9B3F2C` | Accessible terracotta text/link treatment |
| Success | `--color-success` | `#356A54` | Icons and restrained tints |
| Success text | `--color-success-text` | `#2F5E42` | Success language |
| Warning | `--color-warning` | `#8A631A` | Icons and pale tints |
| Warning text | `--color-warning-text` | `#704C0B` | Warning language |
| Error/destructive | `--color-error` | `#9A4038` | Error language/icons and final destructive action |
| Information | `--color-information` | `#496C7C` | Information icon and restrained tint only |
| Information text | `--color-information-text` | `#365866` | Information language |
| Character moss | `--color-character-moss` | `#5E7659` | Character identity only |
| Character plum | `--color-character-plum` | `#6A4D7E` | Character identity only |
| Character steel | `--color-character-steel` | `#38566D` | Character identity only |
| Character ochre | `--color-character-ochre` | `#C8963C` | Character identity only; not small text |
| Subtle border | `--color-border-subtle` | `#D8D0C5` | Standard dividers/borders |
| Strong border | `--color-border-strong` | `#BDB2A5` | Hover/emphasis border |
| Selected tint | `--color-selected-tint` | `#E3ECE8` | Selected row/panel background |
| Scrim | `--color-scrim` | `rgba(35, 33, 30, 0.46)` | Modal/drawer backdrop only |

### Semantic boundaries

- Accent is semantic, never decorative. Mineral teal means action, selection, or focus. Terracotta means current position. Character colors identify a person, never workflow state.
- Essential body text must meet 4.5:1. Large text and interface graphics must meet 3:1. Terracotta, ochre, warning, and success base colors are not small-text colors; use their dark text variants.
- Selection is expressed by teal border/tint plus structure and ARIA, never color alone or a `Selected` badge.
- Normal surfaces do not use a deep-blue replacement system. `--color-information` is the sole blue and only communicates information.
- Do not introduce raw color values outside `tokens.css`, except `currentColor`, `transparent`, and SVG stroke/fill inheritance.

## 3. Typography

### Family stacks

| Role | CSS token | Stack |
| --- | --- | --- |
| Editorial/display | `--font-editorial` | `"Source Serif 4", Georgia, "Times New Roman", serif` |
| Interface | `--font-interface` | `"IBM Plex Sans", "Helvetica Neue", Arial, sans-serif` |
| Time/system | `--font-mono` | `"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` |

The three named families are explicitly required by the controlling Spec, which is the justification for three roles. No bundled or installed Source Serif 4/IBM Plex files were found at foundation time. The fallbacks are truthful; CSS must not claim the named files are loaded and must not fetch them from a network.

### Scale

| Token | Family | Size / line | Weight / tracking | Use |
| --- | --- | --- | --- | --- |
| `--type-page-size`, `--type-page-line` | Editorial | 48px / 52px | 400 / `-0.01em` | Wide page titles and publication-stage headings |
| `--type-page-compact-size`, `--type-page-compact-line` | Editorial | 40px / 44px | 400 / `-0.01em` | Compact page titles and Project Home heading |
| `--type-section-size`, `--type-section-line` | Editorial | 24px / 30px | 400 / normal | Project and primary section titles |
| `--type-entity-size`, `--type-entity-line` | Editorial | 20px / 26px | 400 / normal | Entity, dialog, selected profile titles |
| `--type-body-size`, `--type-body-line` | Interface | 15px / 22px | 400 / normal | Normal content |
| `--type-control-size`, `--type-control-line` | Interface | 14px / 20px | 500 / normal | Controls and options |
| `--type-metadata-size`, `--type-metadata-line` | Interface | 13px / 18px | 400 / normal | Secondary details; application floor |
| `--type-utility-size`, `--type-utility-line` | Interface | 13px / 18px | 600 / `0.12em` | Uppercase utility headings |
| `--type-mono-size`, `--type-mono-line` | Mono | 13px / 18px | 400 / normal | Timecodes/system values, tabular numbers |
| `--type-delivery-size`, `--type-delivery-line` | Editorial italic | 14px / 20px | 400 / normal | Labelled delivery direction only |
| `--type-script-size`, `--type-script-line` | Editorial | 16px / 24px | 400 / normal | Source-faithful Script prose |

Rules: body copy never drops below 15px; metadata, utility headings, mono values, and helper/error copy never drop below 13px. Delivery directions always include a label/structure and never rely on italics alone. Page-title wrapping uses responsive size tokens rather than global scaling.

## 4. Spacing and layout

### Spacing, shape, and control tokens

| Token | Value | Use |
| --- | --- | --- |
| `--space-1` | 4px | Micro alignment, title-to-subtitle |
| `--space-2` | 8px | Base rhythm, related rows |
| `--space-3` | 12px | Compact internal gap |
| `--space-4` | 16px | Panel/inspector padding, standard gutter |
| `--space-5` | 20px | Title block to content, compact major gap |
| `--space-6` | 24px | Related sections, major gutter |
| `--space-7` | 28px | Stable editorial panel separation |
| `--space-8` | 32px | Major page sections |
| `--radius-control` | 6px | Inputs and buttons |
| `--radius-panel` | 8px | Panels, rows, portraits |
| `--radius-modal` | 12px | Modal/drawer surface only |
| `--control-compact` | 32px | Minimum supported compact control |
| `--control-default` | 40px | Standard control |
| `--control-large` | 44px | Search and page-level primary |
| `--control-primary-min` | 128px | Page-primary width floor |
| `--control-play` | 56px | Primary tactile play control |
| `--popover-min` | 208px | Compact anchored menu floor |
| `--dialog-default` | 560px | Standard modal width before viewport clamping |
| `--dialog-new-project` | 1080px | New Project maximum wide-dialog width |
| `--dialog-new-project-max-height` | 848px | New Project maximum wide-dialog height |
| `--dialog-new-project-source` | 248px | New Project editorial source column |
| `--dialog-new-project-header` | 80px | New Project header band |
| `--dialog-new-project-footer` | 72px | New Project fixed action band |

All spacing derives from the 4px unit, with 8px as the default rhythm. Labels sit 8px above controls. Major page spacing is 32px; related groups are 24px. Do not stack outer panel, section divider, inner card, and row borders around the same content.

### Shell geometry

| Region | Reference width | Compact width |
| --- | --- | --- |
| Navigation rail | 224px | 184px at 1024–1199px |
| Project header | 104px | 104px |
| Global header | 88px | 88px |
| Workspace padding | 32px horizontal / 24px vertical | 24px horizontal / 20px vertical |
| Active player | 80px | 80px |
| Inactive player | 80px | 80px |
| Context inspector | 360px inline at ≥1180px | 360px overlay below 1180px |
| Collapsed inspector | 40px icon rail | 40px trigger; overlay content |

All implementation geometry is named in `tokens.css`. The responsive thresholds are `--breakpoint-compact: 1200px` and `--breakpoint-narrow: 640px`; `AppShell` reads those CSS tokens and exposes `data-layout="wide|compact|narrow"` on both the shell and `body`, so in-shell and portal/overlay selectors share one production breakpoint state. Other named geometry includes `--nav-current-rule`, `--nav-item-leading`, `--project-context-wide-min/max`, `--project-context-compact-min/max`, `--stage-wide-min`, `--stage-compact-min`, `--stage-step-min`, `--stage-line-thickness`, `--master-wide`, `--master-compact`, `--cast-workflow-wide`, `--home-continue-cover-column`, `--home-continue-cover-width`, `--home-continue-next`, `--home-continue-action`, `--home-row-cover-column`, `--home-row-cover-width`, `--home-row-status`, `--home-row-action`, `--home-row-overflow`, `--dialog-new-project-source-compact`, `--new-project-cover-width`, `--new-project-cover-compact`, `--script-speaker-width`, `--script-direction-min/max`, `--produce-character-min`, `--produce-character-compact`, `--produce-direction-width`, `--produce-duration-width`, `--produce-audio-width`, `--produce-audio-compact`, `--produce-state-width`, `--produce-state-compact`, `--export-cover-width`, `--export-publication-ratio`, `--export-chapters-ratio`, `--export-output-ratio`, `--player-track-min`, `--player-volume-width`, and `--showcase-min`. Raw pixel dimensions belong only in the token definition file.

At 1536×1024 project mode the rail ends at y944 above the 80px player; header begins at x224 and workspace begins at x224/y104. Global mode uses the same rail and an 88px title-and-action header. Navigation rows are 40px high with 20px icons, 14/20 semibold labels, 8px radius, 16px rail inset, and 24px group gaps. The rail always presents Project (Home, Script, Cast, Produce, Export), Library (Library, Voices, Templates), and Settings (Settings, More); the current project is named in the project header rather than repeated in a sidebar card.

At 1024×768, components reflow; nothing is scaled. At 390×844, the DOM/reading order remains navigation → header → main → player. The rail becomes a full-width labelled navigation region with wrapped groups, the main uses 16px horizontal/20px vertical padding, toolbars wrap, master/detail becomes sequential, dialogs use the viewport with 16px insets, and the player remains one full-width region. No separate mobile mockup or hidden duplicate tree exists.

### Page geometry and ownership anchors

- Cast wide master/detail: `400px minmax(0, 1fr)` in one bordered surface with an internal divider and no redundant page-title band. The visible `Characters` heading is the route h1. Compact: `280px minmax(0, 1fr)`. Narrow: list then selected detail. Specialist Cast workflows mount in the existing shell overlay as a maximum 760px drawer, or full viewport width below 640px; they do not replace the Cast route or create a legacy workspace.
- Inspector/content split reserves a 360px inspector only when required. Below 1180px it overlays instead of compressing content.
- Produce owns one modular route controller plus separate state model, page activity, filter/action, grouped-list, and selected-inspector modules. Rows are chapter/scene grouped from explicit source metadata or stable heading chunks; filters remain Ready to generate, Needs listening, Failed, Stale, and Current.
- Export owns one modular route controller plus separate publication/cover, chapters, output/validation, and build transaction modules. Cover changes and verified-output downloads remain publication actions; build, cancellation, and format state do not leak into unrelated sections.
- One enclosing surface with internal separators is preferred. Dense lists use flat rows and dividers.
- Source covers retain their source aspect ratio inside stable frames; portraits are 48×48 list, 104×120 selected, 88×104 compact selected, all 8px radius. Monograms use the stable parchment square treatment with restrained character initials; they are not generic blue circles.

## 5. Components and page ownership

Every primitive returns live semantic DOM, sets text with `textContent`, and sets known attributes explicitly. Page modules own domain state; primitives own anatomy, presentation variants, keyboard semantics, and state feedback. No primitive queries, moves, or mutates a legacy parent/control.

Every rendered primitive root carries both `data-primitive` and `data-production-factory`. The showcase may compose these factories, but it may not hardcode a labelled substitute. Structural factories (`AppShell`, `ShellInspector`, `NavRail`, both headers, `StageTracker`, `PageTitleBlock`, `FlatSection`, `DividerList`, `MasterDetail`, `Portrait`, `Monogram`, and `SourceCover`) are held to the same provenance rule as interactive controls.

### Shell and structural primitives

| Primitive | Contract |
| --- | --- |
| `AppShell` | One nav rail, one header, one main destination root, one overlay root, and at most one persistent player. Global/project, player absent/inactive/active, inspector collapsed/open/overlay, loading/error. |
| `ShellInspector` | One labelled aside with one named 40px trigger and one controlled body. `setState` covers collapsed/open/overlay; collapsed hides the body, open reserves the 360px inline slot at ≥1180px, and overlay is passable to the shell's singular overlay root below 1180px. |
| `NavRail` | Always-visible Project Home/Script/Cast/Produce/Export; Library/Voices/Templates; Settings/More. Default, hover, focus, current, long label. Current uses a 3px terracotta left rule plus quiet ivory fill and `aria-current`; no duplicate current-project card. |
| `GlobalHeader` | 88px, visible title/subtitle, controls and at most one page primary. Loading/error retain stable height. |
| `ProjectHeader` | 104px, actual project title, independent save state, ordered tracker, one concise workflow state, at most one primary. Long titles truncate with full accessible name/tooltip. |
| `StageTracker` | Ordered list; completed links may navigate; current has `aria-current="step"`; future/blocked are not links. Completed/current/blocked/future use text/shape/icon, not color alone. |
| `PageTitleBlock` | One focused h1, optional concise subtitle, stable title-to-content band; 48/52 wide and 40/44 compact. Cast deliberately uses `Characters` as its visible route h1 inside the master pane instead of duplicating a separate title band. |
| `FlatSection` | Semantic section with heading and internal dividers. No shadow and no nested-card hierarchy. |
| `DividerList` / `Listbox` | Flat rows; default/hover/focus/selected/status are independent. Single-select uses roving tabindex, arrows, Home/End, Enter, and `aria-activedescendant` or option focus. |
| `MasterDetail` | Stable list then detail DOM order; wide/compact columns and narrow sequence; selection does not steal row focus. |
| `Portrait` / `Monogram` / `SourceCover` | Fixed frames, evidence-backed image/crop, useful alt text. No evidence uses monogram and explicit unavailable copy; never generated replacement art. |

### Actions

`Button` variants are primary, secondary, quiet, and destructive; sizes are compact, default, and large. Required states are default, hover, pressed, focused, disabled, and loading. Loading keeps a meaningful label and fixed width. Success becomes separate status/next action, never a permanently green primary. Destructive fill appears only at the final decision.

`IconButton` is 40×40 or 32×32 compact, requires an accessible name and tooltip, and uses a real inline SVG or CSS-drawn neutral mark. No emoji icons. It supports the same interactive states.

### Forms and selection

| Primitive | Contract |
| --- | --- |
| `Field` / `Textarea` / `Select` | Visible label, optional description, filled/read-only/disabled/loading, focused, invalid with linked message; preserves input on recoverable error. |
| `SearchField` | 44px large or 40px default; semantic search input, clear action with name, stable width. |
| `Checkbox` | Native input and label; checked/unchecked/focus/disabled/indeterminate; Space toggles. |
| `RadioGroup` | One labelled group with flat options; arrows move/choose, not separate option cards. |
| `Toggle` | Native checkbox/switch semantics; labelled on/off, focus and disabled; never color-only. |
| `SegmentedControl` | One selected option, arrow-key roving, `aria-pressed` or radio semantics; compact but ≥32px. |
| `FilterChip` | Single-select or multi-select semantics are explicit. Selected includes check/ARIA, not border color alone; removable chips have named remove controls. |
| Secret field | Explicit preserve/replace/clear modes; stored value never appears. Mode changes are announced and recoverable. |

### State and feedback

`StatusIndicator` uses concise text plus an optional decorative icon. Canonical state vocabularies are: workflow Not started/In progress/Needs review/Blocked/Complete; Cast Voice assigned/Missing voice/Identity review/Preview recommended/Non-speaking; audio Ready to generate/Generating/Needs listening/Current/Stale/Failed/Blocked; Export Ready to build/Building/Built/Failed. Selected is never a status.

`Notice` variants are information, warning, blocking error, and success. It has a heading, useful next-step copy, optional quiet action, and optional dismiss control. A notice does not repeat a state already clear in the header and row.

`Progress` covers idle, running, resumable, canceled, complete, and error. Numeric progress uses `role="progressbar"`, `aria-valuemin/max/now`, visible percentage, and polite milestone announcements. Indeterminate progress uses meaningful loading text without a fake percentage.

`Skeleton`, `EmptyState`, `InlineSaveState`, and recoverable error states reserve stable space. Skeletons are `aria-hidden`; a nearby live region names loading. Empty states explain why and the valid next action. Save states are clean, dirty, validating, saving, saved, conflict, and recoverable error.

### Disclosure and overlays

`Disclosure` is a button with `aria-expanded` and `aria-controls`; Enter/Space toggle it. Advanced/provenance content is collapsed by default and uses no fake card title.

`Popover` is positioned from its opener, Escape dismisses, outside activation dismisses, and focus returns to the opener. It is for compact actions, not primary workflows.

`Modal` and `Drawer` use one labelled dialog, a scrim, initial focus, Tab/Shift+Tab trap, Escape close, and focus restoration. Dirty state changes Escape/close into Save/Discard/Cancel confirmation. Modal radius is 12px with one primary and a secondary Cancel; destructive red appears only on confirmed irreversible action. Compact dialogs keep actions visible.

### Audio

`CompactPlay` is a 40px page/row control with loading, ready, playing, paused, failed, and disabled states. The single tactile 56px play control belongs only to `PersistentPlayer`.

`Waveform` is live DOM/SVG-like bars, never a raster mock. Seekable waveforms expose slider role, current/min/max, arrow-key stepping, Home/End, visible focus, and a numeric text alternative. Waveform bars do not carry status by color alone.

`PersistentPlayer` is the single full transport. States are absent, inactive, active, loading, playing, paused, and failed. Both inactive and active states occupy the approved 80px shell band; inactive keeps all controls visibly disabled and names what must be selected, while active provides one tactile play, distinct previous/next and skip controls, timecodes, chapter context, volume, queue, and overflow.

### Page ownership

- W3 shell/router owns `AppShell`, history, lifecycle, title focus, header/tracker/player slots, and route aliases as translations only.
- Projects/New Project/Script/Library/Voices/Templates own their direct page DOM. Voices is read-only library context, not a second assignment source.
- Cast owns one character list and one selected profile. Order is identity → dominant Voice → reference/transcript → approved preview → Character summary → subordinate Appearance summary → Advanced. Persona Visual expands inside Appearance; it is never a global page or second list.
- Produce owns audio rows and selected-chunk inspector from Spec + phase3c. Selection is independent of Stale/Current/etc.; only compact play/waveform appears in page.
- Export owns readiness/blockers/delivery configuration and links its waveform to the persistent player; it does not add a second full transport.
- Settings is a global preferences destination. Maintenance is a separate More destination; deep links preserve return context without embedding or moving its page.
- More and every specialist route own their markup/lifecycle/state. They do not activate or depend on a legacy tab.

## 6. Motion and interaction

| Token | Value | Use |
| --- | --- | --- |
| `--motion-instant` | 0ms | Route/content replacement and reduced-motion alternative |
| `--motion-micro` | 120ms | Press feedback and small opacity changes |
| `--motion-standard` | 180ms | Popover/dialog opacity and small transform |
| `--motion-emphasis` | 240ms | Drawer relationship/attention transition |
| `--ease-standard` | `cubic-bezier(0.2, 0, 0, 1)` | All non-instant motion |

Motion only explains affordance, state change, or spatial relation. Animate `transform` and `opacity` only; do not animate width, height, position, margin, padding, grid, or scroll geometry. Hover does not move ordinary controls. The tactile play control may translate down 1px while pressed. Route swaps do not cross-fade through a blank/legacy state.

Focus follows visible DOM order. Route changes focus the visible h1. List selection retains row focus and announces detail context. Settings deep links place and focus the visible section below the 128px global header and workspace inset; Back/Forward restores route context. Dialogs/drawers trap and restore focus. `prefers-reduced-motion: reduce` sets all authored animation/transition duration to 0ms while preserving states and feedback.

## 7. Depth and surface

Depth strategy is **mostly flat with selective tactile/elevated layers**:

| Token | Value | Only permitted use |
| --- | --- | --- |
| `--shadow-none` | `none` | Ordinary rows, sections, panels |
| `--shadow-tactile` | `0 1px 2px rgba(35, 33, 30, 0.14), 0 3px 8px rgba(35, 33, 30, 0.08)` | Primary play control only |
| `--shadow-popover` | `0 10px 28px rgba(35, 33, 30, 0.16)` | Popover/menu only |
| `--shadow-modal` | `0 24px 64px rgba(35, 33, 30, 0.22)` | Modal/drawer only |
| `--shadow-cover` | `0 4px 14px rgba(35, 33, 30, 0.12)` | Imported source cover only |

Layer order is also tokenized: `--layer-base: 0`, `--layer-sticky: 10`, `--layer-popover: 30`, `--layer-overlay: 40`, and `--layer-modal: 50`. Higher layers are reserved for the named transient surface; product pages do not create private stacking systems.

Primary and secondary paper tones create hierarchy. Ordinary panels/rows have one subtle border or an internal divider and no shadow. Selected items use a 1px mineral-teal border plus selected tint and no shadow. No gradients, glass, glow, raised ordinary cards, or card-inside-card stacks.

## 8. Accessibility constraints, debt, and handoff

### Binding constraints

- Target WCAG 2.2 AA: 4.5:1 essential text; 3:1 large text and interface graphics.
- Minimum application text is 13px. Body is 15px. Zoom and text resizing must reflow without clipping at 200%.
- Compact target and measured test floor are 32×32px, standard target 40×40px, and primary play 56×56px. No shipping control may rely on the looser WCAG exception as its normal target.
- Every interactive element has a visible 2px mineral-teal focus ring with 2px offset. Focus is not hidden under sticky headers/players.
- The first keyboard-focusable skip link is visually hidden at rest and becomes a token-driven, fully visible control inside the viewport on `:focus-visible`.
- Keyboard supports Tab order, Enter/Space activation, arrow-key roving, Home/End where expected, Escape dismissal, dialog trap/restoration, and waveform numeric stepping.
- Native semantics are preferred. Landmarks have unique labels. Status never relies on color. Icon-only controls have accessible names and visible tooltips.
- Errors use `aria-invalid` and `aria-describedby`; recoverable failures retain values and focus the first invalid field after submission.
- Progress/save/generation/build milestones use polite live regions. Blocking errors use assertive announcements only when immediate intervention is required.
- `prefers-reduced-motion`, `forced-colors`, increased text, keyboard-only, screen reader, narrow/reflow, dense-data, empty, loading, partial-error, recoverable-error, saved/success, and long-label states are first-class.

### Inclusive personas

| Persona | Context | Foundation pass condition |
| --- | --- | --- |
| Keyboard editor | Cannot use a pointer during long review sessions | Reaches and operates every primitive; overlays trap/restore; focus remains visible. |
| Low-vision producer | 200% zoom, increased contrast, narrow effective viewport | Text remains ≥13px before zoom; content reflows without horizontal page overflow; state is not color-only. |
| Motion-sensitive listener | Reduced-motion preference | All authored transition/animation durations resolve to 0ms without removing feedback. |
| Screen-reader publisher | Navigates headings, forms, status, progress, and audio alternatives | Names/roles/descriptions are complete; live updates are concise; waveform has numeric control/output. |
| Distracted operator | Dense project and intermittent errors | One primary action, stable dimensions, explicit recovery, low memory burden, no duplicate status/player. |

### Design debt register

| ID | Severity | Location | Affected users | Issue and status | Owner / exit |
| --- | --- | --- | --- | --- | --- |
| `FND-001` | Major fidelity limitation, not accepted product debt | Typography across canonical UI | All users; strongest effect on editorial hierarchy and text metrics | Source Serif 4, IBM Plex Sans, and IBM Plex Mono files are not bundled or installed. Truthful fallbacks are active. No network asset is fetched. | Product owner supplies licensed local font files; implementation owner adds explicit local `@font-face` with `font-display` and re-runs all visual/overflow tests. Until then, pixel-perfect type fidelity is open, not claimed. |

No Critical/Major accessibility debt is accepted. New debt requires location, affected users, fix, owner, exit, and explicit user acknowledgement when accessibility is involved.

### Primitive showcase gate and evidence

Before product pages, the live showcase must load the production token/component files and pass at 1536×1024, 1440×1000, 1024×768, and 390×844. It covers all action/form/selection/status/notice/progress/disclosure/overlay/audio/content states, Cast Voice-first/Appearance-subordinate composition, Persona Visual expanded/no-evidence states, and Settings-to-Maintenance deep-link/Back focus restoration. The binary state inventory includes checkbox checked/unchecked/indeterminate/focused/disabled; disabled radio/toggle/segment/filter; secret preserve/replace/clear behavior and announcements; progress idle/running/resumable/canceled/complete/error; information/warning/success/blocking notices; partial/recoverable/dense content; popover open/Escape/outside/restore; dirty Save/Discard/Cancel; all compact-play states; and persistent-player loading/playing/paused/failed with full transport controls.

Required evidence is stored under `.omo/evidence/b19-t06-primitive-showcase/` with screenshots, DOM/factory/state metrics, keyboard action log, console/error inventory, overflow/text/32px-target measurements, 200% text-resize reflow, forced-colors proof, rest/midpoint/settled and reduced-motion frames, direct phase1/phase2/phase3d reference measurements, source-token audit, and cleanup receipt.
