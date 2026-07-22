---
schema_version: 1
slug: voices-library
title: Library, Voices, and Templates
summary: Inspect reusable project material, dependencies, native tools, and project presets.
version: "1.1"
context_ids: ["library", "voices", "templates", "voice-library"]
destinations: ["library", "voices", "templates", "cast"]
related: ["cast", "maintenance", "settings"]
---
# Library, Voices, and Templates

Library reads the current project's reusable material without copying or reformatting it. Voices is a focused read-only view of reusable Voice material. Templates reuse the same method, preset, and language choices offered by New Project.

## Inspect material

Select an item to review its state, size, current usage, provenance, and native specialist destination. Raw paths and fingerprints remain outside normal rows and are shown only in explicit technical details where supported.

## Voice usage

Voices can preview a reusable Voice and show which Cast characters currently use it. It never changes an assignment. Open the relevant Cast character to assign, replace, or remove a production Voice.

## Delete safely

Deletion first requests a current dependency impact. Referenced, invalid, unsupported, running, or changed material fails closed. Alexandria deletes only through the artifact's existing authoritative route after exact-name confirmation and fingerprint revalidation.

## Template safety

Built-in templates are immutable. Custom template deletion requires the current catalog and template fingerprints, exact-name confirmation, and acknowledgement of historical project usage. Existing projects are never rewritten when a template changes.
