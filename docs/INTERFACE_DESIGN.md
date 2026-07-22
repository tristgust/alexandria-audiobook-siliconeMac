# Alexandria Interface Design

Alexandria uses **Soft Editorial Instrumentation**: a warm, literary desktop workspace with mostly flat operational surfaces, selective tactile audio controls, restrained illustration, and no generic dashboard styling.

This document describes the current product system. Historical Setup/Characters/Editor/Result interface evidence remains under `.omo/evidence/` and does not define current labels, routing, or layout.

## Application structure

### Global mode

The global navigation is:

```text
Home
Library
Voices
Templates

Settings
More
```

- **Home** is Project Home, the multi-project entry point.
- **Library** inventories existing books, production audio, Voices, references, datasets, adapters, and outputs without copying them.
- **Voices** is the reusable Voice library; it does not replace project Cast assignments.
- **Templates** stores user-facing project and Script-generation intent without exposing model internals.
- **Settings** contains ordinary preferences and defaults.
- **More** is the quiet directory for advanced identity operations, Voice Lab, Maintenance, Help, and other specialist tools.

Project-stage navigation is absent until a project is open.

### Project mode

The production flow is:

```text
Script
Cast
Produce
Export
```

The project header always contains the actual project title, save state, the four-stage tracker, and one concise workflow status. Save state and workflow status are separate. A page may expose at most one filled page-level primary action.

Supporting destinations never become additional numbered stages and never duplicate project, Script, Cast, Produce, Export, roster, Voice, or artifact state.

## Shell geometry

At the 1536 × 1024 reference viewport:

- navigation rail: 224 px fixed;
- project header: 104 px;
- global header: 88 px;
- workspace padding: 32 px horizontal and 24 px vertical;
- open context inspector: 360 px;
- active persistent player: 80 px;
- inactive player: 48 px or hidden.

At 1024 × 768:

- navigation rail: 184 px;
- workspace padding: 24 px horizontal and 20 px vertical;
- the context inspector becomes an overlay below 1180 px;
- components reflow rather than scaling the entire application;
- navigation labels remain visible.

The bottom player is the single source of playback state. Pages may show compact play buttons and linked waveforms, but never a second full transport.

## Visual foundation

- Canvas: `#F6F3EC`.
- Primary surface: `#FAF8F2`.
- Secondary surface: `#ECE7DF`.
- Primary text: `#23211E`.
- Mineral teal: primary actions, focus, and selected controls.
- Terracotta: current-stage accents and restrained illustration details.
- Ordinary panels and rows have no shadow.
- The primary play control may use tactile depth.
- Panel radius is 8 px; controls use 6 px; modals use 12 px.
- One enclosing panel with internal separators is preferred over nested cards.

Page and section titles use Source Serif 4. Controls, metadata, navigation, and body copy use IBM Plex Sans. Timecode uses IBM Plex Mono. No application text is below 13 px.

## Core surfaces

### Project Home

Project Home opens, resumes, resolves, or creates projects.

- One filled primary action: **New Project**.
- Project rows form one flat list with dividers, not independent cards.
- Each row shows concise state and next action rather than repeating the full stage tracker.
- Imported source covers and metadata provide editorial presence.
- Search, sorting, and filtering update in place and announce result counts.

### New Project

New Project is one modal form, not a wizard.

Visible sections:

1. Choose source file.
2. Confirm title and author.
3. Select source and output language.
4. Choose Script creation method.
5. Select a preset.

Normal creation does not expose model names, cache locations, context length, prompt templates, or training internals.

### Script

Script is a source-faithful review surface centered on text.

- The selected issue and exact source context are visually primary.
- Required issue categories are Uncertain speaker, Delivery direction, and Source mismatch.
- **Approve Script** remains disabled while blocking issues exist.
- Source-versus-Script comparison, correction controls, previous/next issue, versions, provenance, and generation details remain subordinate to the text.
- Task Bundle export/import is one collapsed secondary workflow; internal handoff IDs are never shown.

### Cast

Cast assigns and verifies one valid production Voice for every speaking character.

- One character list only.
- The selected-character inspector order is Voice, reference audio/transcript, preview, Character summary, Appearance summary, then advanced details and history.
- Production Voice assignment remains in Cast.
- Advanced preparation, reference banks, datasets, and experimental training remain under **More voice tools** or contextual Voice Lab routes.
- Identity review, missing Voice, invalid reference/transcript, and required unapproved preview are blocking.
- Optional visual dossiers and experimental training are not ordinary Cast blockers.

### Produce

Produce generates, reviews, recovers, and keeps every required audio chunk current.

- Page primary: **Generate missing and stale audio**.
- Audio rows are grouped by chapter or scene and use columnar operational density.
- Selection is not a workflow state.
- The selected-chunk inspector shows full text, speaker, delivery direction, pause, production Voice, waveform, reason, history, and **Regenerate this chunk**.
- **Regenerate all audio** remains secondary, destructive, and confirmed.
- Successful current chunks use the state **Current**.

Generated audio is a set of reviewed **Takes**, not disposable temporary output. The accepted Produce design must support current and prior takes, play/compare, **Use this take**, Keep/pin, individual deletion, and reviewed cleanup. Prior takes remain non-current for Export but are not deleted merely because regeneration succeeds. This workflow remains assigned to Boundary 16 until the take registry, retention service, and browser acceptance are complete.

### Export

Export validates, assembles, and builds the final publication.

- Page primary: **Build Audiobook**.
- Metadata, cover, credits, chapters, duration, formats, output location, and validation remain one publication workflow.
- Supported labels are M4B audiobook, MP3 audio file, Audacity project package, and Separate chapter files where backend support exists.
- Build is transactionally blocked by stale, missing, failed, hash-invalid, fingerprint-mismatched, or required-unreviewed audio.
- Failed or canceled builds preserve the previous valid delivery.

## Supporting tools

**More** contains semantic routes to:

- Advanced identity operations;
- Voice Lab;
- Maintenance;
- Help Center;
- Local model cache and other approved specialist modes.

Voice Lab contextual modes include Voice designer, Audio preparer, Dataset builder, expressive reference preparation, and experimental training. These tools preserve the selected project, stable character ID, Script label, mode, and exact return route. Specialist preparation never silently assigns a production Voice.

Maintenance is read-only first. Model Download/Repair, migration, rollback, deletion, and cleanup appear only after explicit impact review and confirmation. Normal synthesis, preview, testing, transcription, or preparation never starts an implicit model or adapter download.

## Interaction and accessibility

- Default controls are 40 px high; compact controls are 32 px; page primaries are 44 px.
- Standard targets are at least 40 × 40 px; the absolute minimum is 32 × 32 px.
- Focus uses a 2 px mineral-teal ring with 2 px offset.
- Selection and status are never conveyed by color alone.
- Icon-only controls require accessible names and visible tooltips.
- Route changes focus the page title.
- Modals and drawers trap focus and restore it to the invoking control.
- Recoverable form failures retain entered data.
- Progress exposes numeric semantics and polite announcements.
- Seekable waveforms use slider semantics and numeric equivalents.
- Reduced motion, increased contrast, reduced transparency, long content, localization, missing art, and large counts are first-class acceptance states.

## Prohibited patterns

- generic KPI dashboards;
- excessive cards or badges;
- decorative gradients or marketing hero sections;
- duplicate workflow authority;
- duplicate full transports;
- hidden automatic downloads or destructive cleanup;
- raw paths, fingerprints, credentials, or internal IDs in normal workflow copy;
- `Selected` as a workflow status;
- `Complete` as an audio-chunk state;
- retaining stale audio at a canonical current path;
- deleting prior generated audio merely because a replacement succeeds.

The canonical plan and repository-local interface specification under `.omo/reference/interface/` remain authoritative when this summary is silent.
