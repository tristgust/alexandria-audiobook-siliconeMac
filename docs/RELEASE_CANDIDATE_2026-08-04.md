# Alexandria release candidate — 2026-08-04

Commit `8a85585a69da54f902bd90652884e91eb9340c7f` is qualified as the current
Alexandria release-candidate code tip. It retains the earlier qualified
candidate and adds the live Script re-acceptance repair, unique unsaved
Original Sin Voice dossiers, stable character-ID dossier lookup, and the
Designed Voice audition timeout/variance-retry repair.

## Verification

- Canonical offline suite: `2,538/2,538`.
- Focused audition and adjacent route verification: `27/27`.
- The exact failed KAN NBARO audition replay returned HTTP 200 in 27.349 seconds
  with happy, sad, and angry variance evidence counts of 2, 2, and 3.
- Range auditions use a five-minute request budget rather than the generic
  twenty-second API timeout.
- A flat emotional lane receives one short-text, delivery-gated retry; the
  anti-flatness gate remains mandatory.
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
`benchmarks/b27_voice_audition_timeout_repair_20260804.json`. Boundary 26 and
Boundary 25 evidence remain preserved as historical qualification evidence.
