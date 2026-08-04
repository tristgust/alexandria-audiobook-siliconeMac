# Alexandria release candidate — 2026-08-04

Commit `9369654eacbcda5655513c8a8b398cc5f8960194` is qualified as the current
Alexandria release-candidate code tip. It retains the earlier qualified
candidate and adds the live Script re-acceptance repair, unique unsaved
Original Sin Voice dossiers, stable character-ID dossier lookup, and the
shared-identity, listen-first, individually regenerable Designed Voice audition
workflow.

## Verification

- Canonical offline suite: `2,542/2,542`.
- Focused audition and adjacent route verification: `31/31`.
- Designed Voice auditions now create exactly one neutral identity recording.
  Baseline, Happy, Sad, and Angry all use that exact same reference audio and
  transcript; Fish changes delivery only.
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
- `start.js` preflight and the Pinokio single-interface launcher contract pass.
- The normal Pinokio runtime is online at port 4200 with no changed sources or
  restart request.

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
`benchmarks/b29_shared_identity_auditions_20260804.json`. Boundary 28,
Boundary 27, Boundary 26, and Boundary 25 evidence remain preserved as
historical qualification evidence.
