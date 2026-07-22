# Persona and Visual References

Persona references are per-character JSON artifacts under `persona_refs/`. They collect voice-relevant character context and may optionally contain a source-backed visual dossier. Voice and visual data share identity ownership but remain separate contracts.

## Base persona references

Advanced Persona generation creates one reference per canonical speaker. A reference can include:

- name and aliases;
- observed features;
- personality;
- voice clues;
- relationships;
- representative sample lines;
- batch observations and evidence;
- final voice description and reference text ownership fields.

Older persona refs with only these fields remain valid. Migration does not add an empty visual object.

## Approved-roster ownership

When a canonical roster exists, persona references can record:

- stable roster entry ID;
- source fingerprint;
- approved roster fingerprint.

Duplicate canonical names receive identity-safe filenames with a stable roster-ID suffix. A rename updates ownership and names without changing the stable identity.

Persona generation canonicalizes script speaker labels through the approved roster before collecting samples. It disables legacy fuzzy or LLM-driven alias merging in roster mode.

## Optional visual dossier

Visual collection is disabled by default. Disabled operation is a true no-op: it does not read unavailable inputs, call a model, create progress, or add a `visual` field.

When explicitly enabled for selected approved characters, discovery scans the whole source and stores exact evidence observations. Required profile buckets include:

- apparent age;
- build;
- face;
- eyes;
- hair;
- skin;
- clothing;
- distinguishing features;
- nonhuman anatomy.

Missing traits remain empty and appear as unknowns. The model may not infer appearance from name, gender, role, personality, voice, or species stereotype.

## Evidence model

Every observation records:

- stable observation ID;
- category;
- summarized detail;
- scope;
- certainty and basis;
- exact quote;
- source character offsets;
- passage index.

Scopes distinguish stable traits from scene-specific clothing, temporary conditions, injury, disguise, transformation, age variants, and unknown state.

Stable profile facts can cite only stable observations of the same category. Temporary details belong in variants. Genuine contradictions remain conflicts. Complementary details are not flattened into conflicts.

## Deterministic prompt summary

`image_prompt_summary` is generated deterministically from the validated dossier. It is not free model prose and cannot introduce cinematic styling, lighting, or unsupported traits. Tampering with the summary causes validation failure.

## Discovery and storage

- `persona_visual_state.json` stores resumable discovery/reconciliation progress.
- Visual progress is bound to source, roster fingerprint, character selection, model/runtime identity, passage layout, and contracts.
- Final writes update selected persona refs transactionally.
- Read-only status and dossier views create no files.

## UI

Character roster contains a separate Visual dossiers master/detail workspace. The default list shows identity and concise completion/error state. Profile, variants, conflicts, unknowns, evidence, and technical ownership are progressively disclosed.

## API

- `GET /api/character_visuals/status`
- `GET /api/character_visuals/{entry_id}`
- `POST /api/character_visuals/discover`
- `POST /api/character_visuals/cancel`
- `POST /api/character_visuals/discard-progress`

## Testing

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_character_visuals \
  tests.test_visual_discovery \
  tests.test_visual_discovery_resume \
  tests.test_discover_persona_visuals \
  tests.test_persona_visual_api \
  tests.test_persona_visual_ui
```
