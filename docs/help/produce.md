---
schema_version: 1
slug: produce
title: Generate and review production audio
summary: Generate missing or stale chunks, recover failures, and listen until every required chunk is current.
version: "1.1"
context_ids: ["produce", "audio-generation", "audio-review"]
destinations: ["produce"]
related: ["cast", "export", "maintenance"]
---
# Generate and review production audio

Produce uses one authoritative chunk collection. Filters, chapter groups, selected-chunk inspection, retry, and regeneration are views over the same audio state.

## Normal production

Use Generate missing and stale audio for the default queue. A chunk can be Ready to generate, Generating, Needs listening, Current, Stale, Failed, or Blocked.

Reviewed pronunciation guidance is applied only to the synthesis request. The
Script and chunk wording remain unchanged. Produce treats a changed
pronunciation request like any other audio dependency: only the anchored chunk
becomes stale, the old audio remains rollback evidence, and regeneration records
every applied or bypassed pronunciation decision.

Compact play controls load the persistent player. A stale or replaced file may remain on disk as rollback evidence while being immediately ineligible as current production audio.

## Recover failures

Retry failed queues only currently eligible failed chunks. Blocked rows link to the missing Voice, invalid dependency, or recovery destination that must be resolved first. Cancel stops remaining queued work; already completed valid chunks remain.

## Destructive regeneration

Regenerate all audio remains in the overflow menu and requires explicit confirmation. It never becomes the default page action. Export cannot consume stale, failed, blocked, hash-invalid, or required-unreviewed audio.
