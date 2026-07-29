# Chris Cwej and Roz Forrester Multimodel Round 1

Date: 2026-07-29

Status: generated, objectively evaluated, blind review packaged, and served locally. Human listening remains required before any production routing decision.

## Scope

This is the first matched model comparison after the completed source review and cleanup pass.

The round contains exactly 96 candidates:

- two identities: Chris Cwej and Roz Forrester;
- two identity-reference tiers per character: clean actor and cleaned in-character;
- four delivery families per character: neutral, dry humour, urgent authority, and restrained vulnerability/concern;
- three models: Fish S2.1 Pro Free cloud, corrected VoxCPM2 controllable cloning, and IndexTTS2 matched control;
- two generations per matched cell.

Model, reference tier, repeat, seed, and control details are hidden from the public review. The private answer key preserves all mappings and generation receipts.

T'Nia Miller is absent from the configuration, references, generated samples, public review, and private answer key.

## Reference handling

The two identity tiers come from `.omo/evidence/chris-roz-cleanup-v1/reference-bank.json` in the completed source-selection worktree.

Chris:

- clean actor: cleaned Travis Oliver `Original Sin` interview;
- canonical: cleaned Chris dialogue from `The Trial of a Time Machine`.

Roz:

- clean actor: Yasmin Bannerman's narrative studio reel;
- canonical: cleaned Roz dialogue from `Original Sin`.

IndexTTS2 receives same-character delivery references only. The one transcript-gated Chris dry-humour clip is explicitly allowed only as an emotion prompt, never as identity conditioning.

## Model handling

Fish:

- private permitted voice models for all four character/reference-tier combinations;
- API header `s2.1-pro-free`;
- simple neutral tags, concise rich dry-humour tags, and full Alexandria instructions for urgent and vulnerable lines;
- two independent generations per cell.

The current paid `s2-pro` API header returned HTTP 402 because Fish API credit is separate from platform credit. The previously proven free header remained available and generated all 32 Fish samples.

VoxCPM2:

- pinned `mlx-community/VoxCPM2-4bit@dc9e5c187858da5f4a13dc4c247e297339216381`;
- exact reference transcript;
- semantic instruction control;
- CFG 2.0 and ten diffusion steps;
- two fixed-seed generations per cell.

IndexTTS2:

- pinned `IndexTeam/IndexTTS-2@740dcaff396282ffb241903d150ac011cd4b1ede`;
- FP32 MPS, greedy one-beam generation, eight diffusion steps;
- two persistent workers;
- same-character delivery-reference audio at configured strengths;
- two fixed-seed generations per cell.

## Objective evidence

All 96 WAVs and 96 generation receipts are durable.

Automatic evaluation used:

- `mlx-community/whisper-large-v3-turbo` for text fidelity;
- pinned SpeechBrain ECAPA speaker verification against the clean actor anchor;
- duration, level, clipping, and silence diagnostics.

Summary:

- 71 exact normalized transcripts;
- 25 candidates with nonzero automatic WER, retained for explicit human text scoring;
- maximum WER 0.142857;
- ECAPA speaker cosine range 0.208867–0.905474.

Objective measurements do not decide winners. The lower identity scores are intentionally visible to the blind reviewer rather than used to remove a model or reference tier before listening.

## Blind review

Open:

`http://127.0.0.1:8878/?reviewer=tristan`

The review is served by Alexandria's byte-range-capable localhost server. It supports autosave, independent reviewer/session keys, partial or cumulative export, import/merge, keyboard navigation, and fixed clean actor identity anchors.

Private answer-key paths are outside the served root and return 404. Candidate audio supports `206 Partial Content` range requests.

The packaged browser smoke passes:

- public counts and identity references;
- autosave isolation;
- persistent reference drawer;
- import conflict resolution and malformed-import rejection;
- next-incomplete termination;
- keyboard focus guards;
- style, group, and cumulative exports;
- desktop and tablet layouts;
- clean browser console.

## Evidence paths

Evidence root:

`.omo/evidence/chris-roz-multimodel-round1-v1/`

Important files:

- `manifest.json`
- `private/internal-manifest.json`
- `private/answer-key.json`
- `private/objective-summary.json`
- `private/objective/*.json`
- `private/fish-voice-models.json`
- `private/indextts2-roz-generation-summary.json`
- `review/`
- `browser-smoke/`

## Product state

- No Alexandria Voice assignment changed.
- No production audiobook audio changed.
- No user-owned source file changed.
- No candidate is production-approved.
- Human blind review remains the authoritative next gate.
