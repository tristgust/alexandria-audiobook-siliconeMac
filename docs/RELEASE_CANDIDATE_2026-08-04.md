# Alexandria release candidate — 2026-08-04

Commit `937c32644a4adc2eddd9c43f06f46b663e48ee6f` is qualified as the current
Alexandria release-candidate code tip.

## Verification

- Canonical offline suite: `2,529/2,529`.
- Focused local-Fish, routing, registry, capability, memory, transaction, and
  delivery-plan verification: `93/93`.
- `start.js` preflight passed with all 10 runtime files, 128 JavaScript files,
  and 165 Python files validated.
- The exact backend command used by `start.js` reached
  `http://127.0.0.1:4200/` with no changed sources and no restart request.
- The live route matrix passed `249/249` checks.
- Fish settings passed `14/14` checks at 1280×900 and 390×844.
- The Pinokio single-interface launcher contract passed.
- Runtime shutdown completed and ports 4200/4201 are stopped.

## Protected state

The Original Sin project retained exact hashes for `voice_config.json`,
`chunks.json`, and `voice_route_listening_decisions.json`. Runtime startup
created only the expected scheduler bookkeeping directory and zero-byte lock:
`background_work/.lock`. It did not change Script, Voice, chunks, generated
audio, or listening decisions.

## Release policy

The annotated `qwen35-native-json-v1` tag remains an immutable historical
baseline at `daf1c792a56484b06a416168ca3c5254d19eec91`; it must not be moved.
Both local `main` and `origin/main` are ancestors of the qualified candidate,
so a later release task can fast-forward `main` and create a new additive
version tag without rewriting history.

This qualification does not itself publish a release, move `main`, or create a
tag. Exact machine-readable evidence is in
`benchmarks/b25_release_candidate_qualification_20260804.json`.
