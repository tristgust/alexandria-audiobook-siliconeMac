# Alexandria UI System

## Direction

Alexandria is a calm editorial production console for turning long-form text into a finished audiobook. It should feel precise, durable, and information-rich without looking like a generic SaaS dashboard.

Visual thesis: ink-and-paper editorial restraint with technical clarity.

Interaction thesis: keep the current task and next safe action visible; reveal evidence, provenance, recovery, and diagnostics only when needed.

## Existing constraints

- The application is a single FastAPI-served `app/static/index.html` using Bootstrap 5 and Font Awesome.
- Preserve the existing dependency footprint unless a production dependency is explicitly approved.
- Reuse existing IDs, routes, state contracts, and test extraction anchors where practical.
- Do not expose prompts, API keys, base URLs, raw exceptions, or opaque telemetry in ordinary workflow views.

## Foundations

### Color

- Canvas: warm near-white rather than blue-gray.
- Primary text: near-black ink.
- Secondary text: cool neutral gray with sufficient contrast.
- Dividers: quiet neutral lines.
- Primary action: one deep blue accent.
- Success, warning, and danger colors communicate actual state only; they are not decorative section colors.
- Avoid gradients, multiple competing accents, and large tinted panels for routine content.

### Typography

- Use the system sans stack for interface text.
- Use a readable serif stack only for source excerpts, quotations, and editorial preview text where it improves comprehension.
- Default body size should remain comfortable at dense desktop widths.
- Headings identify the working surface; avoid marketing language and oversized hero treatment.
- Keep line length controlled in prose, evidence, and diagnostics.
- Monospace is reserved for fingerprints, file paths, model identifiers, and exact machine values.

### Spacing and shape

- Use an 8px-derived spacing rhythm.
- Prefer open sections, columns, and thin dividers over stacked cards.
- Use one modest radius system for controls and interactive surfaces.
- Avoid decorative shadows. A surface may use subtle elevation only when it floats or overlays.
- Do not add a border around a region that is already separated by layout and spacing.

## App shell

- Navigation separates the five core production stages from advanced tools without assigning a different decorative color to every group.
- Each tab begins with a concise page heading, current source/context, and the primary workflow action.
- System status remains compact and secondary.
- Page width supports dense editorial work while keeping prose and inspectors readable.
- At narrow widths, toolbars wrap cleanly and master/detail layouts become a single-column sequence.

## Information hierarchy

1. Current state and blocking issue.
2. Primary action or next safe action.
3. Work content.
4. Secondary controls.
5. Evidence and provenance.
6. Recovery, destructive actions, and technical details.

- Do not repeat the same state in a badge, heading, paragraph, and counter strip.
- Opaque IDs and fingerprints are hidden in expandable technical details unless they are needed to resolve a problem.
- Counts appear only when they help the user decide or navigate.
- Helper text explains behavior or consequence once; remove restatements.

## Components

### Buttons

- One dominant primary button per workflow region.
- Secondary actions use quieter buttons or text actions.
- Destructive and recovery actions are separated from the primary cluster and require explicit confirmation where data is removed.
- Disabled controls retain an accessible explanation nearby or through state copy.
- Icon-only buttons require an accessible label and visible tooltip/title.

### Status

- Prefer a short plain-language status line with a restrained state marker.
- Badges are reserved for compact categorical state, not every count or property.
- Running states show useful progress and the latest meaningful activity.
- Errors state what failed, what remains safe, and the next recovery action.

### Forms

- Labels remain visible; placeholders are examples, not labels.
- Group related settings into logical sections and collapse advanced controls.
- Explain side effects and persistence at the point of action.
- Preserve paste, autocomplete, keyboard, and validation behavior.

### Lists and tables

- Use plain rows with alignment and dividers before card-per-row layouts.
- Provide search/filtering for long collections.
- Rows show identity, meaningful state, and the next useful action.
- Long text wraps deliberately; machine identifiers truncate or wrap only in technical details.
- Keep selection and scroll context across polling refreshes when the underlying item still exists.

### Master/detail workspaces

Use for character rosters, visual dossiers, voices, datasets, and other evidence-rich collections.

- Left/master: searchable list, selection, concise state.
- Right/detail: selected identity, primary action, grouped content, evidence, and technical details.
- Empty detail state tells the user what to select or create.
- On narrow screens, detail follows the list and receives focus after an explicit open action.

### Evidence and provenance

- Source quotes use editorial typography and clear source location.
- Group evidence beneath the conclusion it supports.
- Separate stable facts, scene variants, conflicts, and unknowns.
- Provenance is collapsed by default unless it blocks trust or compatibility.
- Derived summaries are labeled as derived and never presented as the evidence authority.

## Required states

Every applicable workflow must deliberately handle:

- loading
- empty or not started
- ready
- running
- resumable
- finalization pending
- complete
- stale
- incompatible source/config/roster
- corrupt or invalid state
- canceled
- API/model error
- dense data
- narrow viewport

Do not use a generic empty card or a permanent spinner for these states.

## Accessibility and motion

- Use semantic headings, sections, lists, buttons, labels, and disclosure controls.
- Maintain visible `:focus-visible` treatment with adequate contrast.
- Preserve logical keyboard order and move focus deliberately when opening an inspector or modal.
- Announce meaningful asynchronous status changes through an appropriate live region.
- Respect `prefers-reduced-motion`; transitions must not be required to understand state.
- Keep tap targets practical on narrow screens.

## Phase 18D visual dossier pattern

- Visual dossiers are a sibling workflow to roster approval, not a nested card inside the roster card.
- Use a searchable approved-character master list and a dedicated dossier inspector.
- Default list rows show character name, dossier state, and selection. Hide character IDs and observation counters in technical details.
- The inspector separates Overview, Stable profile, Scene variants, Conflicts, Unknowns, and Evidence.
- Optional collection is disabled by default but should not fill the workspace with disabled chrome.
- The primary action is Collect dossiers for the current selection.
- Cancel and discard-progress actions appear only in relevant running or recoverable states.
- Preserve selected character and checked rows across status polling.

## Review checklist

- Can the user identify the current state and next action in one scan?
- Is any status repeated without adding decision value?
- Can a card become spacing and a divider without losing meaning?
- Are raw IDs, fingerprints, counters, or telemetry visible before they are useful?
- Do long names, many rows, long evidence, and missing values remain readable?
- Are loading, error, stale, incompatible, and destructive states explicit?
- Does keyboard focus remain visible and logical?
- Does the layout work at desktop and narrow widths?
- Are console errors absent during the verified workflow?
