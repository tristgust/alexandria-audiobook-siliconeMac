# Alexandria Project Templates

Templates are reusable New Project configurations. They preserve named production intent, Script-entry method, preset, source language, and output language. They do not create a second runtime configuration system and they do not expose low-level model or prompt settings.

## Scope

The Templates destination is a global-shell page. It is not a project stage. A template may be inspected, duplicated, edited when custom, made the default, or applied to the New Project modal.

The authoritative service is `app/project_templates.py`. The normal API surface is:

```text
GET    /api/templates
POST   /api/templates
PUT    /api/templates/{template_id}
POST   /api/templates/{template_id}/duplicate
POST   /api/templates/{template_id}/default
GET    /api/templates/{template_id}/delete-impact
DELETE /api/templates/{template_id}
```

The catalog is stored as `templates.json` under Alexandria’s application-data root. Reading built-in templates is file-pure; the catalog file is created only after a custom mutation.

## Built-in templates

Alexandria provides six immutable starting points:

- Standard;
- Maximum fidelity;
- Faster draft;
- Custom;
- ChatGPT task bundle;
- Import Alexandria Script.

Built-ins may be used, made the default, or duplicated. They cannot be edited or deleted. Duplicate creates an independent custom template.

## Custom template fields

A custom template contains only:

- name;
- concise production intent;
- description;
- generation method;
- preset;
- source language;
- output language.

Generation methods are the same methods available in New Project:

- `local`;
- `chatgpt_task_bundle`;
- `import_existing_script`.

Presets are the same four New Project presets:

- `standard`;
- `maximum_fidelity`;
- `faster_draft`;
- `custom`.

Import templates use Standard because Script generation is skipped.

## Deliberately excluded internals

Normal template records and the template editor do not expose or store:

- model names;
- prompt templates;
- context length;
- cache locations;
- API keys or credentials;
- model-cache fingerprints;
- low-level runtime switches.

Those belong to Settings, capability declarations, or specialist tools. A template describes user intent, not implementation detail.

## Optimistic concurrency

Every catalog mutation submits the current catalog fingerprint. Editing or deleting a custom template also submits the selected template fingerprint. A stale mutation is rejected rather than overwriting newer state.

Fingerprints are transaction fields only. The normal Templates interface never displays raw fingerprints.

## Default template

Exactly one template is the catalog default. The default may be built-in or custom. A custom default cannot be deleted until another template becomes the default.

Changing the default does not create a project and does not rewrite existing projects.

## Delete impact

Built-in templates have no delete route.

Before deleting a custom template, Alexandria returns a delete-impact record containing:

- current catalog and template fingerprints;
- whether the template is the default;
- historical managed-project usage;
- blocking reasons;
- whether usage acknowledgement is required;
- the exact template name required for confirmation.

Historical usage is nonblocking because each project already contains materialized method, preset, and language settings. Deleting a template never rewrites or deletes an existing project. The user must acknowledge historical usage and type the exact template name before deletion.

## Applying a template

Use Template opens the existing New Project modal and applies:

- generation method;
- preset;
- source language;
- output language;
- template identity.

The modal visibly names the applied template. If the user changes method, preset, source language, or output language, Alexandria clears template provenance rather than falsely claiming that the project still matches the template.

When a source file is inspected after a template is applied, extracted book identity may populate title and author, but detected language does not overwrite the template’s selected languages.

Project creation validates that submitted method, preset, and languages still match the selected template. A mismatch fails closed with `template_application_mismatch`. Successful managed-project creation records `creation.template_id` in `alexandria-project.json`, `template_id` in `state.json`, and the project catalog entry.

## Interface

Templates uses Alexandria’s flat supporting-destination master/detail pattern:

- one search field;
- one Built-in/Custom scope filter;
- URL-backed selected template and filter state;
- one selected row;
- detail with method, preset, languages, default state, and actions;
- loading, empty-filter, recoverable-error, and dense states;
- a modal editor for custom-template fields;
- one New Template primary action in the shared page header.

The page uses the approved Soft Editorial Instrumentation tokens. Ordinary rows and panels remain flat; modal depth is reserved for the editor and confirmation workflows.

## Verification

Service and route tests cover:

- file-pure built-in inventory;
- create, edit, duplicate, default, and delete round trips;
- built-in immutability;
- stale catalog and template conflicts;
- exact-name delete confirmation;
- historical-usage acknowledgement without project rewriting;
- invalid import-preset combinations;
- template provenance in managed-project manifest, state, and catalog;
- rejection of unsafe template IDs;
- absence of runtime internals from the public contract.

The real Chrome Boundary 13 audit covers:

- six built-in rows;
- custom create and edit;
- duplicate and default selection;
- guarded deletion;
- search and scope encoded in the route;
- browser Back restoring the same result set;
- applying a Swedish-output template to New Project;
- no horizontal overflow at 1536×1024 or 1024×768;
- no console, runtime, or network errors.

Evidence is stored under `.omo/evidence/b13-t04-templates-browser-final/` and the task verification receipt.
