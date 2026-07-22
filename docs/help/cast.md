---
schema_version: 1
slug: cast
title: Assign and verify Voices
summary: Give every speaking character one valid production Voice and review required evidence.
version: "1.1"
context_ids: ["cast", "voice-assignment", "character-identity"]
destinations: ["cast", "voices"]
related: ["script", "produce", "voices-library"]
---
# Assign and verify Voices

Cast is the user-facing source of truth for character identity and production Voice. Select a character, then review Voice, reference audio and exact transcript, preview, Character summary, Appearance summary, and any current blocker.

## Blocking states

Continue to Produce is blocked by a missing Voice, unresolved identity review, invalid clone reference or transcript, or a required unapproved preview.

## Assignment authority

Production Voice assignment happens only in Cast. Voice Lab can design, prepare, compare, review, train, or approve reusable Voice material, but those specialist actions never silently assign or remove a production Voice.

## Capability truth

Standard supplied-recording clones do not follow line delivery instructions unless the active method explicitly supports that control path. Instruction-controlled and expressive methods remain experimental or restricted until current listening evidence proves the active backend follows delivery directions reliably.

Aliases reuse another character's assigned production Voice; they are not a second synthesis backend. Stable character IDs remain authoritative when display names or Script labels change.
