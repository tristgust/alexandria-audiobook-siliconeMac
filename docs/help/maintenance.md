---
schema_version: 1
slug: maintenance
title: Maintenance and recovery
summary: Inspect recovery, dependencies, migration history, model state, and guarded technical actions.
version: "1.1"
context_ids: ["maintenance", "recovery", "migration", "dependencies"]
destinations: ["more:maintenance", "projects", "library"]
related: ["project-home", "model-cache", "export", "settings"]
---
# Maintenance and recovery

Maintenance is read-only first. It combines existing recovery, model-registry, Library, Projects, migration-status, and migration-history evidence without creating a second technical-state store.

## Recovery and health

The page reports the saved source plus Script, roster, visual, Persona, dataset, audio, and experimental-training checkpoint states. Open the linked native destination for normal work. Resume, finalize, retry, discard, or restart actions remain available only when the authoritative recovery contract advertises them.

## Dependencies and deletion

Review impact before deleting anything. Active projects, unsupported artifacts, running operations, stale fingerprints, and blocking dependencies fail closed. Archived managed projects can move to recoverable Alexandria Trash after exact confirmation. Library artifacts delete only through their existing authoritative route.

## Migration history

Migration remains a dry run until the exact current plan is reviewed and `APPLY MIGRATION` is typed. History shows safe operation summaries, not backup bytes. Rollback verifies every post-migration file hash and refuses to overwrite later edits.

## Model actions

Download and Repair are explicit reviewed actions. Opening Maintenance or normal generation never silently downloads a model. Low-level runtime, stage-profile, and advanced-generation diagnostics remain isolated specialist modes.
