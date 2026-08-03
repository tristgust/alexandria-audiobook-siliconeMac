# Production Voice evidence

Alexandria Production Voices may use an approved, ordered set of reference
samples instead of one implicit `ref_audio` / `ref_text` pair. The evidence set
is a project-owned JSON contract consumed by preview and production through the
same resolver.

This contract currently applies only to the accepted
`qwen3_instruction_controlled` clone route. Other backends must prove their own
multi-sample preview/production parity before they may consume the field.

## Voice configuration

An approved Voice stores:

```json
{
  "type": "clone",
  "clone_backend": "qwen3_instruction_controlled",
  "production_voice_evidence_path": "production_voice_evidence/doctor/evidence.json",
  "production_voice_evidence_fingerprint": "<sha256>",
  "production_voice_language": "English",
  "controlled_clone_configuration_fingerprint": "<sha256>"
}
```

The evidence fingerprint is recomputed by Alexandria. A submitted fingerprint
is never trusted. Changing the evidence file requires a new controlled-clone
preview and listening confirmation.

## Evidence-set requirements

Each set records:

- one stable Voice ID, canonical name, optional Cast character ID, and language;
- an explicit identity binding from Cast or a recorded user review;
- one or more deterministically ordered samples;
- one approved default sample;
- an explicit review disposition when speaker or diarization evidence conflicts;
- one fingerprint over the complete normalized set.

Each sample records:

- sample ID and order;
- project-confined audio path and SHA-256;
- exact transcript and transcript SHA-256;
- source provenance and permission basis;
- human quality, naturalness, artifact, text-match, and delivery evidence;
- compatible backend, language, and speaker classes;
- exact preprocessing pipeline and fingerprint;
- pronunciation-registry fingerprint and entry IDs;
- advisory speaker label, diarization cluster, embedding fingerprint, ASR tags,
  and learned emotion labels.

Anonymous clusters, embeddings, ASR tags, and learned emotion labels are
advisory evidence only. They never establish Cast identity, listening approval,
or production eligibility.

## Deterministic prompt construction

Alexandria filters to samples that are human-approved, text-exact, and compatible
with the requested backend and language. It then selects in this order:

1. an explicit `[sample:sample_id]` instruction tag;
2. the strongest deterministic delivery-label match;
3. the approved default sample;
4. the first compatible sample by stored order and sample ID.

The generated prompt record includes the selected sample, exact audio and
transcript fingerprints, source and review evidence, compatibility,
preprocessing, pronunciation provenance, evidence-set fingerprint, dependency
fingerprint, and final prompt fingerprint.

The dependency fingerprint changes when a sample is added or removed, order is
changed, transcript or audio changes, preprocessing changes, pronunciation
provenance changes, review evidence changes, compatibility changes, or the
selected delivery instruction changes. PyTorch clone-prompt caching consumes
these fingerprints rather than only the reference path.

## Preview and production parity

`/api/clone_voices/controlled_preview` accepts
`production_voice_evidence_path` and `language`. It resolves the same sample,
transcript, instruction, evidence fingerprint, dependency fingerprint, and
prompt fingerprint used by `TTSEngine` during production.

The configuration approval binds the complete evidence set, sampling settings,
persistent Voice style, and seed. It does not bind one incidental per-line
sample selection. Per-line selections remain receipt-backed through their prompt
and dependency fingerprints.

## Failure behavior

Alexandria fails closed when:

- the evidence path escapes the project or is missing;
- audio or transcript fingerprints do not match;
- an approved sample lacks review, text fidelity, delivery evidence, or
  compatibility;
- identity has not been explicitly approved;
- advisory speaker evidence conflicts without an explicit human disposition;
- the saved evidence fingerprint changed after approval;
- an explicit sample tag names an unavailable or incompatible sample;
- a backend other than the accepted Qwen instruction-controlled route attempts
  to use the evidence set.

No failure path silently falls back to anonymous speaker evidence or changes a
Cast identity.
