# Alexandria release candidate — 2026-08-04

Commit `497ce8ecba5c61bb8d06a6b8b3d44d582fd7a41e` is qualified as the current
Alexandria release-candidate code tip. It retains the earlier qualified
candidate and adds the live Script re-acceptance repair, unique unsaved
Original Sin Voice dossiers, and stable character-ID dossier lookup.

## Verification

- Canonical offline suite: `2,536/2,536`.
- Focused Boundary 26 verification: `82/82`.
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
`benchmarks/b26_release_candidate_qualification_20260804.json`. The previous
Boundary 25 evidence remains preserved as historical qualification evidence.
