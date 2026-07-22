# Semantic Navigation and Compatibility Routes

Alexandria now exposes one canonical route contract while retaining every established workspace tab as a compatibility destination.

Implemented in:

- `app/static/navigation_routes.js` — pure route parsing, normalization, serialization, legacy aliases, semantic destinations, tool mapping, and context updates;
- `app/static/index.html` — canonical top-level labels, route-aware activation, history push/replace, Back/Forward restoration, stable entity selection, filter/search persistence, and contextual specialist return;
- `tests/navigation_routes_harness.js` — pure route and alias coverage;
- `tests/navigation_shell_harness.js` — VM coverage against the shipped inline history/context functions;
- `tests/test_navigation_routes.py` and `tests/test_navigation_shell_contract.py` — structural and compatibility contracts.

## Canonical destinations

Canonical hashes use this form:

```text
#/<destination>?<context>
```

Supported destinations:

| Destination | Current compatibility pane |
| --- | --- |
| `projects` | `setup` |
| `script` | `script` |
| `cast` | `characters` |
| `produce` | `editor` |
| `export` | `audio` |
| `library` | canonical Library surface inside `designer` |
| `voices` | canonical Voices surface inside `designer` |
| `templates` | canonical Templates surface inside `designer` |
| `settings` | canonical Settings surface inside `setup` |
| `more` | quiet specialist directory or compatibility pane selected by `tool` |

The compatibility-pane mapping is deliberate. Canonical routing reuses authoritative project, Voice, artifact, and specialist-tool state rather than creating a second workflow or data store.

## Primary navigation

Global navigation reads:

```text
Home
Library
Voices
Templates

Settings
More
```

When a project is open, its production navigation adds:

```text
Script
Cast
Produce
Export
```

`More` is a quiet, searchable directory. Its GET-only registry exposes these semantic tools without duplicating their authoritative state:

- `advanced-character-operations` — Advanced identity operations;
- `voice-designer` — Voice designer;
- `audio-preparer` — Audio preparer;
- `dataset-builder` — Dataset builder;
- `voice-training` — Voice Lab;
- `maintenance` — Maintenance and recovery;
- `model-cache` — Local model cache;
- `help-center` — bundled offline Help Center.

Opening a row activates the existing specialist surface and preserves project, character, source, mode, and exact return context. The More landing performs no project mutation.

## Route context

Canonical routes may preserve:

| Key | Purpose |
| --- | --- |
| `project` | Stable selected project ID. |
| `character` | Stable character ID. |
| `chunk` | Stable Produce chunk ID such as `chunk:42`. |
| `chapter` | Stable Export chapter ID. |
| `issue` | Stable reconciliation or review issue ID. |
| `tool` | Contextual `More` destination. |
| `mode` | Tool or workflow mode. |
| `help` | Stable contextual Help ID such as `voice-assignment`. |
| `topic` | Selected bundled Help topic slug. |
| `return` | Exact encoded return route. |
| `source` | Selected source identity where required. |
| `filter` | Current list filter. |
| `search` | Current search text. |

Unknown keys are ignored. Empty, oversized, and control-character values are rejected. Serialization order is deterministic, so equivalent routes produce the same canonical hash.

Examples:

```text
#/projects?project=project_123
#/cast?project=project_123&character=character_abc&filter=needs_attention
#/produce?project=project_123&chunk=chunk%3A42
#/export?project=project_123&chapter=chapter%3A3
#/more?project=project_123&character=character_abc&tool=voice-designer&mode=preview&return=%23%2Fcast%3Fproject%3Dproject_123%26character%3Dcharacter_abc
#/more?project=project_123&character=character_abc&issue=issue_1&tool=help-center&mode=review&help=voice-assignment&topic=cast&return=%23%2Fcast%3Fproject%3Dproject_123%26character%3Dcharacter_abc&source=library_reference_1
```

## History behavior

- Initial load parses the current hash and activates the mapped pane.
- A legacy hash is replaced with its canonical semantic route; it does not add a redundant history entry.
- Explicit navigation and entity selection use `history.pushState`.
- Search typing uses `replaceState` to avoid one history entry per keystroke.
- Filter changes use `pushState`.
- `popstate` and `hashchange` restore the route without writing it again.
- Browser Back/Forward restores the mapped pane plus project, character, chunk, chapter, issue, tool, mode, Help context, topic, filter, search, source, and return context.

The inline shell retains `activateWorkspaceTab(tabName, options)` for compatibility. Existing callers may continue to activate legacy pane IDs. New callers can use:

```javascript
window.AlexandriaNavigation.navigate(destination, context, options)
window.AlexandriaNavigation.updateContext(changes, options)
window.AlexandriaNavigation.current()
```

## Entity restoration

The shell reuses existing authoritative selection state:

- Cast character routes set `voiceTrainingSelectedId` before the existing character refresh runs.
- Produce chunk routes target the existing `tr[data-id]` row.
- Issue and chapter routes target existing stable data attributes or IDs.
- Current visible search and filter controls are restored and receive their ordinary `input` or `change` event.
- Project, character, chunk, chapter, and issue clicks update the route without forcing a second data model or rerender.

When a contextual specialist tool opens from Cast, Alexandria records the exact canonical return route. `Return to character` parses that route and restores the same project and selected character.

Contextual Help uses `help` for the stable product situation and `topic` for the selected document. It preserves the original `source`, issue, chunk, chapter, mode, project, character, and return route rather than overloading one of those keys with a topic slug.

## Legacy aliases

The compatibility window accepts these hashes:

| Legacy hash | Canonical destination |
| --- | --- |
| `#setup`, `#projects` | `#/projects` |
| `#script` | `#/script` |
| `#characters`, `#voice-casting`, `#voice-projects`, `#cast` | `#/cast` |
| `#voices` | `#/voices` |
| `#editor`, `#produce` | `#/produce` |
| `#audio`, `#result`, `#export` | `#/export` |
| `#library` | `#/library` |
| `#settings` | `#/settings` |
| `#speaker-management`, `#speakers` | `#/more?tool=advanced-character-operations` |
| `#designer` | `#/more?tool=voice-designer` |
| `#preparer` | `#/more?tool=audio-preparer` |
| `#dataset-builder` | `#/more?tool=dataset-builder` |
| `#training` | `#/more?tool=voice-training` |
| `#project-recovery`, `#recovery` | `#/more?tool=maintenance` |
| `#models` | `#/more?tool=model-cache` |
| `#help`, `#help-center` | `#/more?tool=help-center` |

Unknown destinations fall back to Home while retaining safe recognized context.

## Compatibility policy

The compatibility window retains:

- legacy pane IDs and hash aliases;
- existing specialist APIs and state stores;
- existing `activateWorkspaceTab` callers while canonical callers use semantic routes;
- browser Back/Forward restoration without rewriting history;
- exact return routes from contextual project or character entry.

Compatibility does not authorize duplicate visible workflows. Canonical Home, Library, Voices, Templates, Settings, and More remain the user-facing destinations; legacy panes exist only behind their semantic routes until final cleanup acceptance.
