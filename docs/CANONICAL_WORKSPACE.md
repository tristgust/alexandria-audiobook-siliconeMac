# Canonical Alexandria workspace

Alexandria uses one canonical development/control checkout while keeping user
projects and other runtime-owned material outside Git.

The human-facing entry point, next to the Tarial maintenance folder, is:

```text
/Users/tristan/Documents/Codex/Miscellaneous/plugins/alexandria
```

That folder contains links to the single authoritative locations below. It is
not a second checkout or a copied documentation tree.

## Canonical roots

| Role | Canonical location |
| --- | --- |
| Human-facing Alexandria entry point | `/Users/tristan/Documents/Codex/Miscellaneous/plugins/alexandria` |
| Pinokio launcher, source code, Git, documentation and `.omo` control plane | `/Users/tristan/pinokio/api/alexandria-audiobook.git` |
| Runtime and user-data umbrella | `/Users/tristan/Library/Application Support/Alexandria` |
| Managed audiobook projects | `/Users/tristan/Library/Application Support/Alexandria/Projects` |
| Local immutable evidence archive | `/Users/tristan/Library/Application Support/Alexandria/Archives/worktree-evidence.git` |
| External Voice/source library | `/Users/tristan/Library/Application Support/Alexandria/Sources/Voice Sources` |
| Temporary task worktrees | `/Users/tristan/.devspace/worktrees` |
| Persistent model/evaluation cache | `/Users/tristan/pinokio/cache/alexandria-evaluation` |

The Pinokio checkout must remain under `PINOKIO_HOME/api`. Moving it elsewhere
would change the launcher's working-directory contract. The runtime umbrella has
a `Workspace` symlink back to the checkout so Alexandria can still be reached
from one local umbrella folder without weakening Pinokio.

Legacy paths under `~/Documents` remain compatibility symlinks. They are not
separate authorities.

## Worktree policy

- A worktree exists only while a task or named research lane is active.
- Clean completed implementation worktrees are unmounted after their commit is
  integrated; their branches and tags remain in Git.
- Clean historical research may be unmounted when its tracked state is on a
  branch and its ignored evidence is archived or proven disposable.
- Dirty worktrees are quarantined. They are never removed by a clean-worktree
  cleanup operation.
- The expressive/Fish/multimodel research lanes stay mounted until their named
  roadmap work is closed.
- `git clean`, broad filesystem deletion and cache cleanup are not part of this
  policy.

## Machine-specific manifest

The ignored local manifest is:

```text
.omo/state/alexandria-canonical-locations.json
```

It records absolute paths, active research branches, explicitly retired task
branches, compatibility links and protected cache roots. It is the local source
of truth consumed by `tools/alexandria_workspace.py`.

## Verification and cleanup

Inventory is read-only:

```bash
python tools/alexandria_workspace.py inventory
```

Generate a fingerprinted cleanup plan:

```bash
python tools/alexandria_workspace.py cleanup-plan \
  --output .omo/evidence/workspace-canonicalization/cleanup-plan.json
```

Applying a plan requires the exact displayed fingerprint and writes a receipt:

```bash
python tools/alexandria_workspace.py apply-cleanup \
  --plan .omo/evidence/workspace-canonicalization/cleanup-plan.json \
  --expected-fingerprint <sha256> \
  --receipt .omo/evidence/workspace-canonicalization/cleanup-receipt.json
```

Final verification checks canonical paths, compatibility symlinks, archive Git
integrity, the clean source checkout and the absence of mounted retired
worktrees:

```bash
python tools/alexandria_workspace.py verify
```

The verifier does not delete user projects, Voice sources, archives, caches or
dirty research.
