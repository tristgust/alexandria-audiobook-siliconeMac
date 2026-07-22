---
schema_version: 1
slug: project-home
title: Project Home and New Project
summary: Open, resume, or create a managed audiobook project without exposing runtime internals.
version: "1.1"
context_ids: ["projects", "new-project", "project-open"]
destinations: ["projects"]
related: ["script", "settings", "maintenance"]
---
# Project Home and New Project

Project Home is Alexandria's multi-project entry point. Open a project to reveal the four production stages: Script, Cast, Produce, and Export.

## Create a project

1. Choose a supported source file.
2. Confirm the extracted title and author.
3. Choose source and output languages.
4. Choose Local generation, ChatGPT Task Bundle, or Import an existing Alexandria Script.
5. Choose Standard, Maximum fidelity, Faster draft, or Custom.

Alexandria validates a replacement source before discarding the last valid selection. Managed projects switch dynamically; a normal project change does not require restarting Alexandria.

## Resume safely

Each project row states the current recommended stage and any blocker. Opening a managed project is refused while a guarded generation, preparation, training, migration, or export operation could be corrupted.

## When a project needs attention

Follow the stated next stage for normal work. Recovery, dependency, migration, and storage evidence remains in More > Maintenance. The active project cannot be deleted. Archived managed projects can move to recoverable Alexandria Trash only after impact review and exact confirmation.
