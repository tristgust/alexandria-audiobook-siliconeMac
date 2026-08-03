# Alexandria history consolidation — 2026-08-03

## Current authority

Alexandria now has one local development branch:

`alexandria/development-integration-20260803`

The branch contains the complete accepted implementation line plus the Git
ancestry of every previously unmerged local feature, fix, research, and WIP
branch. The history-only consolidation merge is
`d1a3748de864a1ac43a37c56c34da23d7efeb956`.

History-only means the current verified tree was not replaced by obsolete
research or WIP snapshots. Those snapshots remain reachable through merge
parents, while the current tree keeps the accepted implementations.

## Material promoted into the current tree

The following files had existed only as uncommitted research work and were
promoted into the consolidated branch:

- Fish S2.1 selector, feature-ablation, stability, and preference-model
  evaluation tools plus the completed human-score input;
- Chris/Roz source acquisition, transcription, candidate retrieval, finalist
  preparation, and source/reference manifests.

These additions are research tooling. They do not admit a provider, assign a
Voice, accept a model license, or change production routing.

## WIP snapshots absorbed as ancestry

The following previously dirty worktrees were committed before consolidation:

- launcher/runtime WIP: `62e5c3c70bb9497c6a4e1144ed8012dd66ab3a37`;
- Full Cast WIP: `644b95cb872271701f71e50d84b24676d1d4b9d3`;
- audio provenance WIP: `debc984dc0be3d295bb50fde04b6d3bde714e23b`;
- interface audit WIP: `10f0a6766fca4e2559756b866cc3b20843d9a2ee`;
- Fish selector research: `71903e6701762b7c7150ec3328a9adc4b32ba95c`;
- Chris/Roz source research: `8326215f18222a00e2781a1439f21eaf0d1b65a2`.

The 179-entry Full Cast worktree contained no unique file blob outside the
consolidated branch's current tree or history: 113 files matched the current
tree and 66 matched earlier canonical blobs.

## Large research artifacts

The Chris/Roz consolidated-review worktree contained approximately 852 MiB of
MossFormer checkpoints and a profiling file. These were intentionally not
committed to Git. They are preserved with a per-file SHA-256 manifest at:

`.omo/evidence/archived-worktree-artifacts/chris-roz-consolidated-review-v2-20260803/`

## Branch policy after consolidation

- New Alexandria implementation work starts from
  `alexandria/development-integration-20260803`.
- Do not create another integration branch for ordinary roadmap continuation.
- Use short-lived task worktrees only when isolation is necessary; merge the
  completed commit back immediately and remove the worktree.
- Research and WIP must be either committed into the development branch's
  ancestry or stored under the canonical evidence root before cleanup.
- The normal Pinokio checkout may track this branch after verification; ignored
  runtime configuration remains local state rather than Git content.

