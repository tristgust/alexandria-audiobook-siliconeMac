# Alexandria release candidate — 2026-08-04

Commit `ff21351fd7ef74b9f0d510b6253843cc713dae15` is qualified as the current
Alexandria release-candidate code tip. It retains the earlier qualified
candidate and adds the live Script re-acceptance repair, unique unsaved
Original Sin Voice dossiers, stable character-ID dossier lookup, and the
shared-identity, listen-first, individually regenerable Designed Voice audition
workflow, now with a redesigned review surface and complete audition-package
saving. Supplied-recording Voices generate range auditions inline instead of
falling through to the standalone Voice designer. Designed Voice auditions now
use the VoiceDesign identity itself as Baseline and three bounded Fish emotion
lanes, eliminating the live authored-text failure reported for Archer McElwee.

## Verification

- Canonical offline suite: `2,551/2,551`.
- Focused Designed Voice, Fish, Cast, save, timeout, and API verification:
  `50/50`.
- The exact failing Archer McElwee request was replayed from the live Cast
  object. The old path returned HTTP 409 after six transcription failures and
  zero identity failures, following roughly two to three minutes of work.
- The repaired exact Archer replay returned HTTP 200 in 12 seconds. Baseline,
  Happy, Sad, and Angry reported `neutral`, `joy`, `grief`, and `anger`; all
  four transcript checks passed with WER `0.0`.
- Archer's Happy, Sad, and Angry clips were 0.917, 1.259, and 1.067 seconds.
  The prior contaminated prompts had produced 24–25 second Sad and Angry clips
  and misclassified both as fear.
- The VoiceDesign identity is copied directly into the Baseline lane. The
  served identity, session reference, and Baseline segment shared SHA-256
  `7ca85c2bbba73688be89b62609d7887f0720542f5c5856fb5267ad8af3267e52`.
- Production generation retains the strict authored-text gate. Designed Voice
  audition lanes may retain identity-safe, audio-integrity-safe speech when
  automatic transcription is uncertain; the Cast UI marks that lane
  **Listen-check** instead of discarding the complete audition.
- Sad-only regeneration returned HTTP 200 in 1 second, advanced the montage to
  revision 1, and preserved the identity and other three lanes.
- Supplied-recording Voices without an audition now expose **Generate supplied
  Voice audition** in the character’s Cast editor. The range uses the saved
  recording and exact transcript; no Designed Voice identity is created.
- The live KAN NBARO supplied audition returned HTTP 200 in 23.729 seconds.
- Designed Voice identity generation now receives a short anatomy-only prompt:
  age/gender presentation, register, pitch, resonance, timbre, and accent.
  Persona, cadence, and emotion are applied only to downstream Fish delivery.
- The audition cache schema advanced to version 7 so older mixed-prompt and
  fail-closed audition sessions cannot be reused.
- A fixed-seed KAN probe showed the old mixed prompt at 190.333 Hz, a rejected
  long meta-wrapper at 90.215 Hz, and the accepted minimal anatomy prompt at
  182.125 Hz. Opposite fixed-seed definitions also produced materially distinct
  pitch profiles while identical prompt+seed runs were byte-identical.
- The audition review is one cohesive responsive card with clear hierarchy,
  numbered lanes, refresh-icon controls for Happy/Sad/Angry, animated waveform
  and spinner feedback during generation, and live status text.
- **Save audition as Production Voice** is now one action. It stores the neutral
  identity, reference identity, all four reviewed lane files, combined montage,
  and metadata as one fingerprint-bound Voice package before assigning it.
  Failed assignment rolls the saved package back.
- Designed Voice auditions create exactly one neutral identity recording. That
  recording is Baseline; Fish generates only the short Happy, Sad, and Angry
  lanes from the same reference audio and transcript.
- The exact KAN NBARO replay used one `reference_identity.wav` file. All four
  lanes reported `shared_neutral_identity` with reference identity score 1.0;
  selected Fish outputs retained identity scores from 0.980215 to 0.992733.
- Angry-only regeneration completed in 2.406 seconds and changed only Angry
  plus the rebuilt montage. The identity, Baseline, Happy, and Sad hashes were
  unchanged.
- **Regenerate full audition** bypassed the cache, reran the neutral identity
  step, regenerated all four Fish lanes in 11.097 seconds, and changed all four
  lane hashes plus the montage.
- Reopening the same audition returned the current revision from cache in
  0.039 seconds.
- Range auditions use a five-minute request budget rather than the generic
  twenty-second API timeout.
- Valid but subtle audition lanes remain listenable and are clearly marked.
  Happy, Sad, and Angry each expose an individual regeneration action; after
  one lane changes, Alexandria replays the complete four-part montage.
- Cast browser acceptance passed at 1536×1024, 1440×1000, 1024×768,
  768×900, and 390×844, including individual-lane and full-regeneration flows.
- The current live route matrix passed `249/249`. The actual Archer Cast screen
  showed his source-grounded description and Designed Voice controls with zero
  rendered errors, console errors, runtime exceptions, server errors, or failed
  requests.
- All 45 Original Sin Voice dossiers now include explicit age context and
  source-supported gender when known. Unknown gender is not invented.
- KAN NBARO is corrected from “age and gender unknown” to the source-backed
  **110-year-old woman**. Her saved clone route and reference remain unchanged;
  Alexandria recorded the description update as an undoable dependency change
  affecting KAN only and invalidating zero chunks.
- `start.js` preflight and the Pinokio single-interface launcher contract pass.
- The launcher-equivalent verification runtime was stopped cleanly after the
  live replay; ports 4200 and 4201 are free.

## Protected state

The Original Sin project retained exact hashes for `voice_config.json`,
`chunks.json`, `voice_route_listening_decisions.json`, `audio_validity.json`,
the current Script, Script metadata, and the character roster. Runtime startup
created only the expected zero-byte scheduler lock at `background_work/.lock`.

The current 5,470-entry Script is accepted as immutable version
`script_version_6cd27fd34f6aca505dc4b490`. The accepted delta consists only of
the authorized sixteen speaker-label corrections: nine `BOT` labels became
`SECURITYBOT`, and seven became `TOBIAS VAUGHN`. Text, delivery directions,
source order, and entry count are unchanged.

All 45 currently unsaved speaking identities now have distinct,
source-grounded Voice descriptions. The 26 saved Voice records, selected
routes, listening decisions, approved audio, and chunks are unchanged. The
repair is hash-gated, receipt-backed, idempotent, and exactly rollbackable.

## Release policy

The annotated `qwen35-native-json-v1` tag remains an immutable historical
baseline at `daf1c792a56484b06a416168ca3c5254d19eec91`; it must not be moved.
Both local `main` and `origin/main` are ancestors of the qualified candidate,
so a later release task can fast-forward `main` and create a new additive
version tag without rewriting history.

This qualification does not itself publish a release, move `main`, or create a
tag. Exact machine-readable evidence is in
`benchmarks/b32_designed_voice_audition_repair_20260804.json`. Boundary 31,
Boundary 30, Boundary 29, Boundary 28, Boundary 27, Boundary 26, and Boundary
25 evidence remain preserved as historical qualification evidence.
