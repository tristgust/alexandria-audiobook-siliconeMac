# Alexandria release candidate — 2026-08-04

Commit `d468a18c7df3b56539cdea10cbd6734b0d68e534` is qualified as the current
Alexandria release-candidate code tip, with qualification and deterministic
browser-harness fixes through `f8df3a910a153a8025f243ef1e177539394b295c`.
It retains the earlier qualified
candidate and adds the live Script re-acceptance repair, unique unsaved
Original Sin Voice dossiers, stable character-ID dossier lookup, and the
shared-identity, listen-first, individually regenerable Designed Voice audition
workflow, now with a redesigned review surface and complete audition-package
saving. Supplied-recording Voices generate range auditions inline instead of
falling through to the standalone Voice designer. Designed Voice auditions now
use the VoiceDesign identity itself as Baseline and three bounded Fish emotion
lanes, eliminating the live authored-text failure reported for Archer McElwee.
Supplied responsive project Voices now audition from their actual saved identity
instead of resolving production-route assets inside a temporary preview folder.
Spoken-continuity contract v2 also binds attached attributions and resumed speech
at the synthesis-text layer without changing the accepted Script. Cast now has
exactly four production modes—Built-in Voice, Existing Voice, Designed Voice,
and Sound effect. Existing Voice owns project/saved/supplied reuse, linked and
independent-copy semantics, character-specific direction/pitch/pace/level
overlays, and one four-part inline audition path.

## Verification

- Canonical offline suite: `2,580/2,580`.
- Focused spoken-continuity, generation-path, pronunciation, Take, lifecycle,
  invalidation, and audio-safety verification: `146/146`.
- The exact Original Sin `Not if I can help it,` → `Bernice said,` boundary now
  carries one continuity instruction and synthesis-only text beginning
  `, bernice said,`. The accepted Script text remains unchanged.
- The continuity cue reaches single, parallel, and batch generation; its mode
  and text hash persist in the immutable Take receipt and audio dependency
  fingerprint.
- The prior Take `take_1785879717998617000_f38d7a6906ef` remains preserved and
  is correctly stale under the v2 binding. No live replacement was generated.
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
- Audition emotional lanes now receive one bounded identity retry rather than
  failing the complete four-part audition after the first identity mismatch.
- A fresh retry-capable Archer replay at the final Cast boundary returned HTTP
  200 in 14.099 seconds. Baseline, Happy, Sad, and Angry all returned audio;
  uncertain Sad transcription was retained as an explicit listen-check rather
  than failing the complete audition.
- The exact HATER OF HUMANS supplied audition returned HTTP 200 in 10 seconds
  after bypassing the project responsive router for audition generation.
- Existing Voices expose **Generate Existing Voice audition** in the character’s
  Cast editor even when a one-clip preview already exists. The range uses the
  exact selected identity, saved transcript or reviewed pack, and the current
  character-specific overlay; it does not open standalone Voice Designer.
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
- The Cast production-mode selector contains exactly **Built-in Voice**,
  **Existing Voice**, **Designed Voice**, and **Sound effect**. Technical clone,
  adapter, alias, responsive-router, and reusable Designed Voice distinctions
  remain internal to Existing Voice.
- Existing Voice can reuse another Cast Voice as **Linked** or **Independent
  copy**. Linked assignments follow the source configuration; independent
  copies duplicate project-confined assets under approved roots and recompute
  routing fingerprints. Neither path mutates the source Voice.
- Reused Voices support per-character direction text plus bounded pitch,
  pace, and level adjustments. These settings reach single and batch synthesis,
  are applied deterministically to output audio where needed, and participate
  in immutable-Take compatibility without changing the source Voice.
- Saved Designed Voice identities and approved community packs are assignable
  Existing Voices backed by their exact saved identity/package rather than a
  regenerated approximation from description text.
- The live Original Sin catalog exposes **Computer**, **Securitybot**,
  **Heddolli**, **Kan Nbaro**, **Homeless Forsaken**, and **Powerless
  Friendless** as assignable Existing Voices with previews. The read-only proof
  left `voice_config.json` unchanged at SHA-256
  `8522e37f90959377699896b587cec806b6d7b8354d346ad9ce79855cda7b3177`.
- Sound effect is a persistent non-speech Cast mode with stable definitions for
  Wolsey-style cat sounds and rat squeaks/rustling/skittering. No approved SFX
  backend is installed, so Alexandria reports a precise blocker and terminates
  before speech TTS initialization rather than synthesizing human speech.
- Valid but subtle audition lanes remain listenable and are clearly marked.
  Happy, Sad, and Angry each expose an individual regeneration action; after
  one lane changes, Alexandria replays the complete four-part montage.
- Cast browser acceptance passed at 1536×1024, 1440×1000, 1024×768,
  768×900, and 390×844, including individual-lane and full-regeneration flows.
- The current combined route/accessibility gate passed `249/249`: `234/234`
  core keyboard/accessibility cases plus the complete B19-T06 regression gate.
  Nested Produce keyboard activation intercepted provider calls, and every
  browser exited with its temporary profile removed. The actual Archer Cast screen
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
- The launcher-equivalent runtime is online at PID 90052 with
  `restart_required: false`, no changed sources, the Original Sin project
  active, and matching loaded/current static asset versions.

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
`benchmarks/b35_spoken_continuity_v2_20260804.json` and
`benchmarks/b32_designed_voice_audition_repair_20260804.json`, plus the Cast
closure receipt `benchmarks/b33_cast_voice_modes_reuse_20260804.json`. Boundary 31,
Boundary 30, Boundary 29, Boundary 28, Boundary 27, Boundary 26, and Boundary
25 evidence remain preserved as historical qualification evidence.
