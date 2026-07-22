# Alexandria Settings

Settings is the ordinary application-preference destination in Alexandria’s global shell. It edits the existing `config.json`; it does not create a second settings store.

The canonical service is `app/application_settings.py`. The normal API is:

```text
GET /api/settings
PUT /api/settings
```

No POST, PATCH, DELETE, repair, download, migration, or cleanup action is available through the Settings endpoint.

## Normal Settings scope

The canonical Settings page contains the decisions that a user can reasonably make without entering a diagnostic or specialist workflow.

### Project preferences

- default source language;
- default output language;
- current default project template;
- confirmation before destructive actions;
- remembering the last valid managed project.

Changing these defaults does not rewrite an existing project.

The default template is read from the authoritative Templates catalog. **Manage Templates** opens the Templates destination with a Settings return route.

### Language-model provider

Settings exposes approved connection and runtime controls:

- provider: Auto detect, Native Ollama, or OpenAI-compatible;
- model name;
- base URL;
- explicit API-key action: preserve, replace, or clear;
- context length;
- keep-alive policy;
- timeout;
- thinking where supported;
- corrective retry.

Structured output is required and cannot be disabled in the normal interface because Alexandria’s Script, roster, and other structured workflow contracts depend on it.

Native Ollama must use a local host URL. OpenAI-compatible providers require a key when no saved key is preserved. URLs cannot contain embedded credentials.

### Speech defaults

- local or external speech mode;
- external server URL when required;
- speech language;
- parallel workers;
- pause between speakers;
- pause between lines from the same speaker.

These values affect future synthesis. Saving them does not regenerate, invalidate, replace, or approve current audio.

### Accessibility and density

- follow system, reduce, or allow motion;
- follow system, higher, or standard contrast;
- comfortable or compact density;
- live-region status announcements.

The page previews motion, contrast, density, and status-announcement changes immediately. A failed save leaves the edited values visible so the user can correct them. Successful settings persist in `config.json` and are restored after reload.

### Storage policy

Settings records:

- rollback retention days;
- intermediate retention days;
- maximum backup storage in GiB.

The current cleanup mode is `manual_only`. The interface explicitly states that retention values are saved now while guarded cleanup enforcement belongs to Maintenance and the audio-safety boundary. Saving a policy does not delete any file.

## Excluded from normal Settings

The following stay in Maintenance, Local model cache, or another specialist destination:

- runtime health and request metrics;
- model preload and unload;
- model downloads and repair;
- cache diagnostics;
- migration and recovery operations;
- prompt-template editing;
- raw sampling and chunking controls;
- evidence-gated stage model profiles;
- destructive cleanup.

Settings provides explicit route-aware entry points for:

- Stage model profiles;
- Runtime diagnostics;
- Local model cache;
- Advanced generation.

These routes preserve `#/settings` as the return destination. The canonical Settings form is hidden while canonical Maintenance shows recovery, dependency, model, project, and migration status. Existing low-level runtime, stage-profile, and advanced-generation panes remain isolated specialist modes inside Maintenance.

## Secret handling

`GET /api/settings` never returns an API-key value. It reports only whether a key is configured and returns a blank editor field.

A save must state one of three intentions:

- `preserve` — retain the saved key without exposing it;
- `replace` — store the newly entered key;
- `clear` — remove the saved key when the provider contract allows it.

Prompt text is also excluded from the normal settings response.

## Optimistic persistence

Every settings update submits the exact `config_fingerprint` returned by the last GET or successful PUT. A stale update fails with `settings_config_conflict` instead of overwriting newer configuration.

The service validates the complete supported payload before writing. A failed validation leaves `config.json` unchanged. The interface retains the user’s invalid edits and reports the specific problem rather than reloading and discarding the form.

Writes are atomic. Symbolic-link or oversized configuration files fail closed.

## Preservation of advanced configuration

The canonical update modifies only its owned fields. It preserves:

- prompt configuration;
- generation and sampling configuration;
- evidence-gated LLM stage profiles;
- unexposed TTS fields;
- unknown forward-compatible keys.

A successful provider or speech change releases the current TTS engine instance so the next generation uses the saved configuration.

## Interface behavior

Settings uses Soft Editorial Instrumentation:

- six flat settings sections;
- one sticky summary inspector at wide widths;
- one sticky Save Settings bar;
- one primary save action;
- ordinary controls with strong labels and inline help;
- advanced destinations as quiet rows rather than embedded diagnostic panels;
- responsive single-column composition at compact widths.

The page contains loading, recoverable load error, saved, dirty, saving, validation error, and stale-conflict states.

`Command-S` on macOS and `Ctrl-S` elsewhere save while Settings is active.

## Verification

Service and route tests cover:

- file-pure model-free reads;
- API-key and prompt redaction;
- explicit preserve, replace, and clear actions;
- stale-write rejection;
- invalid-input nonmutation;
- local-Ollama URL enforcement;
- embedded-credential rejection;
- structured-output enforcement;
- speech and numeric range validation;
- atomic preservation of prompts, generation settings, profiles, and unknown fields;
- symlink and size confinement;
- default-template integration;
- GET/PUT-only route behavior.

Static interface tests cover:

- the canonical Settings and canonical Maintenance separation, with legacy low-level diagnostics isolated by mode;
- absence of prompt, cache, repair, runtime, and recovery controls from normal Settings;
- immediate accessibility preferences;
- keyboard save;
- truthful deferred-cleanup language;
- responsive layout.

The real Chrome Boundary 13 audit verifies:

- wide and compact composition without horizontal overflow;
- API-key redaction;
- failed remote Ollama validation with edited values retained;
- successful corrected save;
- persistence after a full page reload;
- immediate motion, contrast, and density application;
- Runtime diagnostics opening in Maintenance with return context;
- Manage Templates opening Templates with return context;
- zero console, runtime, and unexpected network errors.

The browser audit uses `ALEXANDRIA_CONFIG_PATH` to isolate a disposable configuration copy. Repository `app/config.json` is never mutated by browser acceptance.
