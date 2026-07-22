---
schema_version: 1
slug: settings
title: Application Settings
summary: Manage ordinary defaults, provider controls, storage policy, accessibility, and reusable templates.
version: "1.0"
context_ids: ["settings", "preferences", "generation-defaults", "accessibility"]
destinations: ["settings", "templates", "more:maintenance"]
related: ["project-home", "voices-library", "maintenance", "model-cache"]
---
# Application Settings

Settings contains ordinary application preferences and approved generation defaults. It does not create a second configuration store; validated values persist through the existing `config.json` contract.

## Normal controls

Settings includes the default project template, source and output language defaults, approved provider connection fields, speech defaults, storage-policy values, and accessibility preferences for motion, contrast, density, and status announcements.

Structured output remains required. Invalid edits stay visible with a clear Not saved state while the persisted configuration remains unchanged.

## Secrets

Alexandria never returns an API-key value to the browser. The editor is blank and save intent is explicit: preserve the configured secret, replace it, or clear it.

## Separate technical work

Runtime diagnostics, model preload or unload, model Download or Repair, recovery, migration, dependency impact, cleanup, and raw prompt editing remain in More > Maintenance or another specialist destination. Saving retention values does not delete files; guarded cleanup is a separate future Maintenance action.

## Keyboard

Command-S or Control-S saves the current valid Settings form. Route changes preserve the exact return destination when opening Templates or Maintenance.
