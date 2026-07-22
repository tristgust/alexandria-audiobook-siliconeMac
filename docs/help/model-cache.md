---
schema_version: 1
slug: model-cache
title: Local model cache
summary: Understand pinned local model status, explicit Download or Repair actions, and offline operation.
version: "1.1"
context_ids: ["model-cache", "cache-download", "cache-repair"]
destinations: ["more:model-cache", "more:maintenance"]
related: ["maintenance", "voices-library", "settings"]
---
# Local model cache

Alexandria uses one pinned model registry and shared local cache identity across generation, transcription, Voice preparation, training, conversion, and diagnostics.

## Cache states

Cached means all required files are present at the pinned revision. Missing means no complete local snapshot is available. Repair needed means a snapshot exists but required files or links are incomplete.

## Explicit actions

Normal generation does not silently download a multi-gigabyte model. Open More > Maintenance or Local model cache, review the model purpose, runtime, estimated size, current state, and missing required files, then explicitly choose Download or Repair.

The action rechecks free-space headroom, pins the immutable revision, validates required files after transfer, and permits only one cache operation at a time. A failed or interrupted transfer does not become Cached.

## Offline truth

Status reads inspect local cache structure without contacting the model Hub. Missing or incomplete required models fail before runtime initialization and direct the user back to the explicit cache action. Broader blocked-network, memory-admission, and repeated-launch proof remains part of the model-cache completion boundary.
