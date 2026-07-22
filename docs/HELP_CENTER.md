# Alexandria Offline Help Center

The Help Center is a bundled, searchable, offline documentation surface inside the canonical global shell. It is not a remote search client, runtime-generated assistant, or second workflow database. Every visible topic is versioned Markdown under `docs/help/` and is validated against `docs/help/manifest.json` before it is listed or rendered.

## Bundle manifest

`manifest.json` is the ordered inventory and integrity boundary. It contains:

- `schema_version`;
- `bundle_version`;
- one entry per topic with `slug`, exact filename, and reviewed full-source `content_sha256`, covering front matter and Markdown body.

The loader rejects:

- a missing, unreadable, oversized, non-JSON, or unsupported manifest;
- unknown manifest fields or malformed topic entries;
- duplicate slugs or filenames;
- filenames that do not exactly match `<slug>.md`;
- non-lowercase or non-SHA-256 hashes;
- unlisted Markdown files or listed files that are missing;
- a topic whose current full-source hash no longer matches the manifest.

The manifest is a review aid, not mutable runtime state. Opening Help performs no write.

## Topic front matter

Every topic declares:

- `schema_version`;
- stable `slug` matching the filename;
- current `title` and concise `summary`;
- topic `version`;
- one or more globally unique stable `context_ids`;
- allowlisted semantic `destinations`;
- valid `related` topic slugs.

Context IDs describe product situations rather than file names. Examples include `new-project`, `script-review`, `voice-assignment`, `audio-review`, `publication-build`, `accessibility`, `migration`, and `cache-repair`. `GET /api/help/context/{context_id}` resolves a context to its current topic without exposing manifest internals.

A context ID can belong to only one topic. Missing related topics, duplicate context IDs, unsupported destinations, invalid UTF-8, unsafe control characters, symbolic links, root escapes, oversized topics, and empty bodies fail closed.

## Sanitization and Markdown subset

Raw HTML is rejected by the backend. The browser renderer does not call an HTML parser or assign topic Markdown to `innerHTML`. It creates DOM nodes directly and writes all content through `textContent` or `createTextNode`.

The supported subset is deliberately small:

- level 1–4 Markdown headings;
- unordered and ordered lists;
- fenced code blocks;
- inline code;
- plain paragraphs.

Unsupported Markdown syntax remains literal text. Scripts, event handlers, iframes, SVG, forms, images, raw links, and executable elements cannot be created from topic content. Related topics and workflow destinations are generated from validated structured metadata rather than from Markdown links.

## Offline full-content search

`GET /api/help?search=...` searches the bundled title, summary, body, context IDs, and semantic destinations. Search is Unicode-aware and deterministic. All normalized search terms must occur in a topic’s local content.

The browser stores search in the semantic route’s `search` context and debounces the local API request. It does not contact a remote service. Search results retain manifest order, show the visible count against the full bundle count, and restore through Back/Forward.

## Semantic route state

Help uses dedicated route keys:

- `help` — stable contextual entry ID;
- `topic` — currently selected topic slug;
- `search` — current offline search.

The contextual Help route does not overwrite the original `source`. Contextual entry preserves project, character, source, issue, chunk, chapter, mode, and exact return route. A representative route is:

```text
#/more?project=project_1&character=character_1&issue=issue_1&tool=help-center&mode=review&help=voice-assignment&topic=cast&return=%23%2Fcast%3Fproject%3Dproject_1&source=library_1
```

The `?` control in the global or project header maps the current destination to a stable Help context. It is hidden while Help itself is open. Opening a workflow from a topic removes only Help-specific `help`, `topic`, `search`, and `filter` values; it preserves the originating product context and replaces `return` with the exact current Help route. The Return control follows the exact incoming return route, falling back to More only for a non-contextual Help entry.

## Keyboard and screen-reader behavior

The topic list is a `listbox` with button options, one `tabindex="0"` selected row, `aria-selected`, and `aria-activedescendant`. Arrow Up and Arrow Down move through topics. Home and End move to the first and last topic. Enter and Space retain native button activation.

Selection retains focus on the selected row and updates the detail article through a polite live region. Search result counts use a polite status region. Route changes use the canonical page title and shell landmarks. The contextual `?` controls have accessible names and visible focus treatment.

## Current content truth

The bundle covers:

- Project Home and New Project;
- Script review and approval;
- Cast and Cast-only production Voice assignment;
- Produce audio states and recovery;
- Export validation and atomic build behavior;
- Library, Voices, Templates, and guarded deletion;
- Application Settings and secret redaction;
- canonical Maintenance, migration history, dependencies, and recoverable Trash;
- explicit local model Download and Repair.

Content contracts reject stale or unsupported labels such as Voice casting, Generate Audio, specialist production-assignment controls, Setup → Local model cache, automatic model downloads, and restart-required project switching.

## API routes

- `GET /api/help` — manifest-validated inventory and optional full-content search;
- `GET /api/help/context/{context_id}` — resolve stable product context;
- `GET /api/help/{slug}` — full topic with related summaries.

The context route is registered before the dynamic slug route so `context` cannot be captured as a topic slug.

## Verification

Focused tests cover manifest safety, exact inventory, content hashes, related-topic integrity, context uniqueness, context lookup, Unicode and body search, traversal, symbolic links, raw HTML, unsafe controls, missing topics, and route ordering.

Static interface contracts cover DOM-only rendering, route keys, contextual `?` controls, search synchronization, exact context preservation, listbox semantics, and keyboard behavior. The content-truth audit validates current labels and rejects obsolete claims.

Browser acceptance covers:

- global and project `?` entry;
- stable context resolution without overwriting `source`;
- body-only search with URL state;
- Arrow/Home/End selection and focus;
- related-topic navigation;
- workflow links with exact Help return state;
- Return to the originating route;
- long topic content at 1536 × 1024 and 1024 × 768;
- zero executable topic elements, console errors, network errors, or runtime errors.
