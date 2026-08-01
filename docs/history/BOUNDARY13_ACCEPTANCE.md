# Boundary 13 Supporting-Surface Acceptance

Boundary 13 closes Alexandria’s supporting destinations and managed project activation as one canonical product system. The accepted surfaces are Project Home, Library, Voices, Templates, Settings, More, Help Center, Maintenance, Advanced identity operations, and Voice Lab.

This acceptance does not replace the primary Script, Cast, Produce, or Export contracts. It verifies that the supporting surfaces share the same shell, semantic route model, authoritative project state, accessibility behavior, and protected-artifact discipline.

## Canonical surface inventory

The final read-only browser pass opens these ten surfaces:

- Project Home;
- Library;
- Voices;
- Templates;
- Settings;
- More;
- Help Center;
- Maintenance;
- Advanced identity operations;
- Voice Lab.

Each surface renders inside the one canonical `main` landmark, exposes exactly one visible page-level `h1`, and preserves the global navigation landmark. Specialist routes remain semantic `more` destinations with a stable `tool` value rather than becoming duplicate stages or standalone page systems.

## Reference widths

The same implementation is accepted at:

- 1536 × 1024;
- 1024 × 768.

The standard Boundary 13 browser matrix renders Library, Voices, Settings, Maintenance, More, and Help Center at both widths. Templates is rendered at the wide reference and its compact behavior is covered by the shared responsive master/detail contract. No accepted surface produces horizontal page overflow.

The final acceptance also applies intentionally expanded Swedish text to the compact More surface. Navigation labels, the page title, subtitle, tool titles, and descriptions become substantially longer without escaping the viewport or losing any of the eight specialist tools.

## Keyboard and accessibility tree

The final pass uses a real keyboard Tab event and Chrome’s full accessibility tree through `Accessibility.getFullAXTree`.

For every accepted surface it verifies:

- one `main` landmark;
- at least one navigation landmark;
- the visible page title appears as an accessibility-tree heading;
- every visible interactive control has an accessible name;
- keyboard focus is visible, treated, and inside the viewport;
- current navigation is exposed with `aria-current`;
- no dialog remains open after navigation;
- no duplicate visible IDs exist;
- status information is expressed in text rather than color alone.

Library, Voices, Templates, and Help use valid single-selection listboxes. They expose one selected option, one roving `tabindex="0"` option, a valid `aria-activedescendant`, and Arrow Up, Arrow Down, Home, and End navigation. The selected option remains a real button, so Enter and Space retain native activation.

The Voice Lab test controls use explicit `for`/`id` label relationships. More and every specialist route expose the More-tools control as the current page rather than relying on visual active styling alone.

## Primary-action hierarchy

Supporting pages do not create competing filled primary actions.

- Project Home retains the shell’s New Project primary action; project continuation and row actions are secondary.
- Templates retains the shell’s New Template primary action; Use Template is a secondary contextual action.
- Voices retains Create Voice as its one filled primary action.
- Settings retains Save Settings.
- More, Help, Maintenance, and specialist tools expose no duplicate shell-level filled primary action.

The final audit also rejects duplicate native audio transports. Supporting surfaces use the persistent player or compact controls rather than introducing another full `audio[controls]` transport.

## Legacy redirects

Real Chrome startup verifies that representative legacy hashes are replaced with canonical semantic routes:

| Legacy hash | Canonical route |
| --- | --- |
| `#library` | `#/library` |
| `#voices` | `#/voices` |
| `#designer` | `#/more?tool=voice-designer` |
| `#project-recovery` | `#/more?tool=maintenance` |
| `#models` | `#/more?tool=model-cache` |
| `#help` | `#/more?tool=help-center` |
| `#training` | `#/more?tool=voice-training` |
| `#settings` | `#/settings` |

Each replacement keeps the canonical route object and browser hash identical and produces no horizontal overflow. The old `#voices` alias now correctly resolves to the global reusable Voices destination; the separate `#voice-casting`, `#characters`, `#voice-projects`, and `#cast` aliases continue to resolve to Cast.

## Runtime purity

The final acceptance mode is read-only. It runs in a disposable copied project root and records three independent purity checks:

1. filesystem snapshots before application startup and before browser navigation;
2. filesystem snapshots before and after the browser surface pass;
3. canonical JSON hashes for the authoritative read models before and after browser navigation.

The filesystem snapshot covers current project state, Script, Script metadata, approved roster, Voice configuration, chunk/audio state, export receipt, migration state/history, project catalog, isolated browser configuration, Voice assets, datasets, adapters, preparation output, training projects, production audio, external workflows, task bundles, managed projects, and Trash-related storage.

The API snapshot covers:

- model registry status;
- Projects;
- Library;
- Help Center;
- More registry;
- Settings;
- recovery status;
- migration status.

All three comparisons must remain byte- or JSON-identical. The machine-readable receipt records this as `startup_and_read_unchanged`, `browser_unchanged`, and `api_unchanged`. The browser request log permits GET, HEAD, OPTIONS, and the existing file-pure accent-status POST. Any other mutating request fails acceptance.

## Raw-state exclusions

The normal supporting surfaces are rejected if they expose:

- absolute local paths;
- raw 64-character fingerprints;
- internal project, character, artifact, template, migration, or operation IDs;
- placeholder labels such as `Status / Blocker` or `Primary action`;
- restart-required project-switching claims;
- duplicate full transports;
- more than one filled page primary action.

Technical details remain behind the appropriate collapsed disclosure or guarded impact review. Production Voice assignment remains a Cast action. Model Download, Repair, migration, rollback, deletion, training, and generation remain explicit guarded operations and are not executed by final acceptance.

## Verification

Focused and adjacent Boundary 13 tests cover managed project activation, project catalog, Library inventory, reusable Voices, Templates, Settings, More, Help Center, Maintenance, model registry, migration, recovery, stable character operations, Voice Lab, semantic navigation, canonical shell behavior, accessibility fixes, and documentation truth.

The final browser mode is:

```bash
PYTHONPATH=app ./app/env/bin/python tests/interface_browser_audit.py \
  --repo-root . \
  --mode boundary13-final \
  --output-dir .omo/evidence/b13-t10-boundary13-final
```

Its evidence includes:

- `boundary13-final-supporting-wide.png`;
- `boundary13-final-localization-compact.png`;
- canonical wide and compact screenshots for Library, Voices, Settings, Maintenance, More, and Help Center;
- runtime filesystem and API snapshots in the machine-readable verification receipt.

Closure requires zero browser failures, zero console errors, zero runtime errors, zero network errors, unchanged repository protected hashes, and a clean scoped `git diff --check`.
