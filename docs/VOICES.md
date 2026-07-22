# Alexandria Voices

Voices is the reusable Voice-library destination in Alexandria’s global shell. It is a read-only view over the same Voice configurations, references, aliases, adapters, and Cast assignments used by the production workflow. Cast remains the only place where a production Voice is assigned or changed.

Implementation:

- aggregate: `app/voice_library.py`;
- route: `GET /api/voice-library`;
- interface: the canonical Library master/detail surface in Voices mode.

## Supported Voice methods

Voices presents six method families without flattening their capabilities into one generic “voice” label.

### Built-in Voice

Pinned Qwen CustomVoice speakers such as Ryan, Aiden, Vivian, and Serena. These are reusable speaker identities. They do not accept arbitrary per-line delivery instructions.

### Designed Voice

Reusable VoiceDesign artifacts created through Voice Lab. The persistent design description is part of the Voice identity. Preview remains available through the existing Voice Lab entry point.

### Supplied recording

Standard Qwen supplied-recording clone. It uses exact reference audio and transcript to retain identity. Alexandria does **not** send the Script line’s delivery instruction to this clone model. The Voices detail states `Line instruction: Not supported` rather than implying expressive control.

### Instruction-controlled clone

Experimental Qwen supplied-recording path with an explicit instruction channel. The channel is present, but production capability is not approved. The detail shows:

- production: not approved;
- preview: available when the model and reference are valid;
- line instruction: channel present;
- preview and manual listening required before assignment.

Legacy VoxCPM2 controlled assignments remain visible as blocked legacy state. Voices does not silently migrate or reassign them.

### Voice adapter

Existing LoRA or merged-MLX Voice artifacts. Technical completion, checkpoint output, or a changed audio hash does not imply listening approval or production suitability.

### Voice alias

A stable Script-label alias that resolves to another authoritative Voice configuration. The alias row exposes its target and resolution chain; it does not duplicate the target Voice.

## Capability truth

The method summary is derived from `voice_backend_capabilities.py`, the pinned model registry, and current cache state. It reports production support, preview support, instruction-channel support, and an operator-facing explanation.

Voices never upgrades an experimental or blocked method because an asset directory exists. It never treats a standard supplied-recording clone as instruction-aware. It never treats a technical adapter receipt as a listening decision.

## Cast usage

Every Voice row shows current Cast usage. A usage record includes:

- stable character ID;
- character name;
- canonical Script label;
- production method and backend;
- Voice validity;
- preview state;
- a semantic Cast route with project, character, source, and return context.

Selecting a usage row opens that character in Cast. Back returns to the same Voices route. When a Voice has multiple assignments, the main Cast action opens Cast without inventing a primary character.

The route and aggregate explicitly publish:

```json
{
  "cast_is_authoritative": true,
  "assignment_mutation_supported": false
}
```

There is no POST, PUT, PATCH, or DELETE Voice-library route.

## Listening

Physical Voice references and designed-Voice previews expose a **Listen** action when an existing audio file is available. Listen loads the preview into Alexandria’s single persistent player. Voices does not render a second complete transport.

Preview loading does not approve the Voice, change Cast, generate new audio, or save a configuration. Instruction-controlled assignment still requires the existing approved-preview receipt and Cast validation.

## Native destinations

Voice resources open at their existing authoritative destination:

- built-in and designed Voices → Voice Lab under More;
- standard supplied recordings → the existing preparation/reference destination;
- instruction-controlled assignments and aliases → Cast;
- adapters → the existing training/adapter destination under More.

Project, character, source, tool mode, and return route are preserved in semantic route state.

## Interface states

Voices reuses Alexandria’s flat supporting-destination master/detail layout and covers:

- loading;
- true empty state;
- search or filter with no matches;
- recoverable API error with Retry;
- invalid or blocked Voice state;
- dense Voice inventory;
- wide 1536 × 1024 and compact 1024 × 768 layouts.

The master list contains one row per reusable Voice resource. Selection is interaction state, not workflow status. Search plus method/state filters remain URL-backed so browser history restores the same result set.

## Verification

Focused contracts cover:

- all six method families;
- capability truth for standard and controlled clones;
- deterministic output without raw reference transcripts or identity descriptions;
- preview and native entry points;
- alias resolution without duplication;
- Cast usage and return routes;
- GET-only API behavior and machine-readable failures;
- absence of assignment mutation.

The real Chrome Boundary 13 audit verifies the Voices surface at both reference widths, persistent-player preview loading, controlled and standard-clone disclosure, alias presence, Cast character routing, Back restoration, and absence of console, runtime, or network errors.

Evidence: `.omo/evidence/b13-t03-voices-browser/`.
