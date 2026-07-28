# Local Alexandria Archives

Alexandria's normal runtime and integration checkout remains:

```text
/Users/tristan/pinokio/api/alexandria-audiobook.git
```

Historical code is preserved in the main repository through branches and `archive/*` tags. Large ignored evidence from retired worktrees is stored separately so audio, screenshots, and review packages do not inflate the application repository or its GitHub remote.

## Evidence archive

Local bare repository:

```text
/Users/tristan/Documents/alexandria-worktree-evidence.git
```

This repository is intentionally local-only. It contains 14 immutable tagged snapshots and passed `git fsck --full` after creation and packing.

Archived snapshots:

- `evidence/b17-t14-t49-voice-research`
- `evidence/b17-t50-t60-source-atlas`
- `evidence/b17-t61-t63-combined-bank`
- `evidence/b17-t64-t65-validated-core`
- `evidence/b17-t66-historical-provenance`
- `evidence/b17-t67-t69-provenance-followups`
- `evidence/b17-t70-t71-final-boundaries`
- `evidence/b17-t72-t73-validated-bank`
- `evidence/b17-t74-final-bank-benchmark`
- `evidence/b17-t75-review-applied`
- `evidence/b17-t76-paired-seed-reliability`
- `evidence/b17-t77-paired-seed-review-applied`
- `evidence/b19-t06-reference-fidelity`
- `evidence/b19-t06-stable-editorial-enhancement`

The protected multimodel Round 1 and Round 1 v2 usable packages remain in the active `research/multimodel-voice-benchmark` worktree rather than this archive. The active expressive-voice runtime proofs remain in `research/expressive-voice-validation`.

## Inspect the archive

```bash
git --git-dir="$HOME/Documents/alexandria-worktree-evidence.git" tag --list
git --git-dir="$HOME/Documents/alexandria-worktree-evidence.git" show --stat evidence/b17-t50-t60-source-atlas
```

## Restore a snapshot

Restore into a new, disposable directory. Do not restore over the normal Alexandria checkout.

```bash
mkdir -p "$HOME/Documents/Alexandria Evidence Restore"
git --git-dir="$HOME/Documents/alexandria-worktree-evidence.git" \
  --work-tree="$HOME/Documents/Alexandria Evidence Restore" \
  checkout -f evidence/b17-t50-t60-source-atlas -- .
```

The restored files retain their original `.omo/evidence/...` paths.

## Persistent evaluation cache

The following directory is **not** a Git worktree and must not be removed as worktree debris:

```text
/Users/tristan/pinokio/cache/alexandria-evaluation
```

It contains the persistent IndexTTS2 and Chatterbox v3 source, environments, and model caches used by the active multimodel and expressive-clone research. It is approximately 14 GB and remains intentionally outside the application checkout and evidence archive. Rebuilding it would require restoring dependencies and model data; preserve it until that research is explicitly retired.

## Active worktrees

Only active work is kept as a Git worktree:

```text
main
  /Users/tristan/pinokio/api/alexandria-audiobook.git

research/expressive-voice-validation
  /Users/tristan/.devspace/worktrees/alexandria-research-expressive-voice-validation

research/fish-s21-blind-test
  /Users/tristan/.devspace/worktrees/alexandria-research-fish-s21-blind-test

research/multimodel-voice-benchmark
  /Users/tristan/.devspace/worktrees/alexandria-research-multimodel-voice-benchmark

wip/audio-invalidation-safety
  /Users/tristan/.devspace/worktrees/alexandria-wip-audio-invalidation-safety
```

Retired checkouts, duplicate checkout caches, Python bytecode, logs, temporary visual comparisons, and duplicate environments were removed after their commits and meaningful evidence were proven recoverable.