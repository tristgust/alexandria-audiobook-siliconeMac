---
schema_version: 1
slug: produce
title: Generate and review production audio
summary: Generate missing or stale chunks, recover failures, and listen until every required chunk is current.
version: "1.2"
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

Long chunks may be generated through several internal backend windows, but they
remain one Script row and one canonical Take. Alexandria requires every planned
internal segment, validates each result, applies the backend’s explicit gap,
crossfade, or overlap-discard rule, and admits only the fully validated joined
audio. Missing or incompatible segments fail the row rather than producing a
shortened survivor-only result.

Every accepted Generate action now has a persistent request ID. Repeating the
same active request does not start duplicate work. If Alexandria restarts, a
matching request can resume from verified completed internal segments instead
of regenerating them. A changed Voice, pronunciation, Script, seed, setting, or
segment plan requires a different request and cannot reuse stale progress.

Compact play controls load the persistent player. A stale or replaced file may remain on disk as rollback evidence while being immediately ineligible as current production audio.

## Recover failures

Retry failed queues only currently eligible failed chunks. Blocked rows link to
the missing Voice, invalid dependency, or recovery destination that must be
resolved first. Cancel is persisted, stops remaining queued work, and prevents
a late model result from publishing after cancellation; already completed valid
chunks remain. An explicitly requested replacement of running work waits until
the prior request is terminal; accepted work that never started is replaced
immediately. Only one pending replacement is allowed.

If the client disconnects before Alexandria accepts the request, no generation
worker is scheduled. After acceptance, the request is durable and continues
independently of the browser connection.

## Destructive regeneration

Regenerate all audio remains in the overflow menu and requires explicit confirmation. It never becomes the default page action. Export cannot consume stale, failed, blocked, hash-invalid, or required-unreviewed audio.
