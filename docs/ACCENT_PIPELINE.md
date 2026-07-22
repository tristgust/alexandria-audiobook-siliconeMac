# Accent Pipeline

Alexandria’s accent pipeline is used when a VoiceDesign description explicitly requests one of the supported non-English accent families. It creates a native-language reference with VoiceDesign, then clones that identity into the requested output language.

## Why the pipeline exists

A direct English VoiceDesign prompt can flatten or caricature an accent. The two-stage path gives the model a native-language acoustic reference first:

1. detect a supported accent from the description;
2. create the same character speaking a fixed native-language seed passage;
3. save that native reference under `designed_voices/accent_seeds/`;
4. use the Base/Clone model to speak the user’s preview text;
5. record the relationship in `designed_voices/accent_registry/`.

The English preview remains compatible with Alexandria’s normal designed-voice save workflow.

## Supported accent families

The current registry defines:

- French, including southern French and Occitan wording;
- Spanish;
- German, Austrian, and Swiss German wording;
- Italian;
- Portuguese;
- Russian.

Detection is conservative and pattern-based. Unsupported descriptions follow ordinary VoiceDesign. For example, a Scottish or Dunoonian accent does not currently activate this pipeline.

Descriptions can use either natural wording such as `soft French accent` or the explicit marker form `[accent: French]`.

## Provenance

Each accent preview record includes:

- preview audio path and hash;
- native seed audio path and hash;
- exact native seed text;
- native language;
- English/output preview text;
- creation metadata.

Clone generation resolves the preview back to its native reference and exact transcript. If the registry entry or file hash is invalid, Alexandria does not silently substitute unrelated text.

The registry is additive. Older designed voices without an accent record remain valid ordinary clone references.

## Long text behavior

For an accent-derived clone, Alexandria splits longer text into short segments and reuses the same native reference for each segment. A small pause is inserted between segments. This reduces accent drift across a long sentence while retaining one voice identity.

## Measured Apple Silicon cost

On the Phase 22 M2 Max measurement, the French accent pipeline:

- generated 2.8 seconds of preview audio;
- took 9.30 seconds total;
- measured 3.32 RTF;
- reached approximately 6.26 GiB peak process RSS;
- loaded both the VoiceDesign and Clone models.

It is therefore functional but slower and heavier than ordinary warm VoiceDesign, Clone, or CustomVoice inference. The UI labels it as an accent pipeline rather than presenting it as the ordinary route.

## Status API

`POST /api/voice_design/accent_status` reports whether the description selects:

- the standard direct VoiceDesign route; or
- a named native-reference accent route.

The status call is model-free and does not generate audio or create registry files.

## File safety

- Seed and preview files are user-generated runtime artifacts and are ignored by Git.
- Registry reads validate paths and hashes.
- Removing a registry file does not rewrite a saved voice configuration.
- Migration does not add accent metadata to older references or delete existing seeds.

## Testing

Focused coverage includes detection, false positives, provenance, hash mismatch, native-reference resolution, route status purity, MLX migration parity, and browser rendering.

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_accent_pipeline \
  tests.test_accent_status_api \
  tests.test_accent_status_ui \
  tests.test_mlx_accent_pipeline_migration
```
