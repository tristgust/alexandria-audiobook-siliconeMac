# Alexandria release candidate — 2026-08-04

Commit `7d72dd5d05c603975b45fa54d52db07fb4dcb90f` is qualified as the current
Alexandria release-candidate code tip. It retains the earlier qualified
candidate and adds the live Script re-acceptance repair, unique unsaved
Original Sin Voice dossiers, stable character-ID dossier lookup, and the
listen-first, individually regenerable Designed Voice audition workflow.

## Verification

- Canonical offline suite: `2,541/2,541`.
- Focused audition and adjacent route verification: `30/30`.
- The exact KAN NBARO audition returned HTTP 200 in 22.879 seconds with all four
  lanes available for listening. Happy and Angry were surfaced as subtle
  rather than causing the audition to be discarded.
- Angry-only regeneration completed in 2.529 seconds. The identity seed and
  Baseline, Happy, and Sad segment hashes remained exact; only Angry and the
  rebuilt four-part montage changed.
- Reopening the same audition is cached and returned in 0.046 seconds on the
  normal Pinokio runtime.
- Range auditions use a five-minute request budget rather than the generic
  twenty-second API timeout.
- Valid but subtle audition lanes remain listenable and are clearly marked.
  Happy, Sad, and Angry each expose an individual regeneration action; after
  one lane changes, Alexandria replays the complete four-part montage.
- `start.js` preflight passed at the qualified code tip.
- The exact backend command used by `start.js` reached
  `http://127.0.0.1:4200/` with no changed sources and no restart request.
- The live route matrix passed `249/249` checks.
- Fish settings passed `14/14` checks at 1280×900 and 390×844.
- The Pinokio single-interface launcher contract passed.
- Runtime shutdown completed and ports 4200/4201 are stopped.

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
`benchmarks/b28_voice_audition_lane_regeneration_20260804.json`. Boundary 27,
Boundary 26, and Boundary 25 evidence remain preserved as historical
qualification evidence.
