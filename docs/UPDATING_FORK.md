# Updating the Apple Silicon Fork

This fork contains product changes that are not present in the upstream Alexandria repository. Do not replace the fork branch with upstream or run an unreviewed force update.

## Repository layout

Current remotes:

- `origin` / `tristgust` — `tristgust/alexandria-audiobook-siliconeMac`;
- `upstream` — `Finrandojin/alexandria-audiobook`.

Primary development branch:

```text
feature/native-ollama-structured-json
```

Parent Apple Silicon branch:

```text
feature/apple-silicon-alexandria
```

The local checkout and its runtime artifacts are the source of truth during an update.

## Before updating

1. Stop Alexandria and any active generation, roster discovery, visual discovery, audio preparation, dataset generation, or training-sidecar process.
2. Confirm the branch and worktree state:

```bash
git branch --show-current
git status --short
```

3. Do not proceed with uncommitted source changes unless they are deliberately preserved in a separate commit or worktree.
4. Back up user/runtime data outside the repository if the installation contains irreplaceable work.
5. Read the release notes and migration status before applying schema changes.

Important runtime artifacts include:

- `config.json`;
- `state.json`;
- source uploads and selected book;
- annotated scripts and metadata;
- voice configuration;
- generated chunks and final audio;
- saved scripts;
- character roster state/draft/approval;
- persona and visual references;
- expressive voice projects;
- Dataset builder projects and exported datasets;
- clone/design/reference audio;
- adapter artifacts and manifests;
- speaker-management and migration history.

These paths are ignored by Git where appropriate. A Git merge does not replace a real backup.

## Fetch without changing the branch

```bash
git fetch --all --prune
git log --oneline --decorate --graph --max-count=30 HEAD upstream/main
```

Inspect the upstream range before merging:

```bash
git log --oneline HEAD..upstream/main
git diff --stat HEAD...upstream/main
```

Use the actual upstream default branch name when it differs from `main`.

## Integration strategy

Prefer a dedicated integration branch or worktree:

```bash
git switch -c integration/upstream-YYYYMMDD
git merge --no-commit --no-ff upstream/main
```

Resolve conflicts by preserving the fork’s verified contracts, including:

- Apple Silicon MLX runtime selection and dependencies;
- native Ollama structured output and telemetry;
- source-fidelity and Review text audits;
- resumable generation and metadata;
- canonical roster discovery/approval/enforcement;
- speaker-management transactions and audio invalidation;
- stage model profiles and evidence gates;
- expressive voice project ownership;
- capability-driven adapter behavior;
- backward-compatible migration;
- accepted interface IDs, navigation, and browser tests.

Do not resolve a conflict by taking an entire upstream file when the fork has substantial changes in the same file. Reconcile the behavior and rerun focused tests after each high-risk file.

## Dependency changes

Apple Silicon uses a distinct dependency contract. Before accepting upstream package changes, compare:

```text
app/requirements.txt
app/requirements-apple-silicon.txt
install.js
torch.js
```

Do not add `qwen-tts` to the production Apple Silicon environment while it conflicts with MLX-Audio’s Transformers line. Experimental PyTorch fine-tuning belongs in a separate sidecar environment.

Do not run the installer merely to make a merge compile. First understand whether a dependency change is required by upstream runtime code or only by a platform path the fork does not use.

## Migration dry run

After merging and before using the application on real project data:

```text
GET /api/migration/status
```

The status read is file-pure. Review:

- planned actions;
- blockers;
- preserved-artifact inventory;
- current plan fingerprint;
- last migration record.

The current migration only adds an empty `llm.profiles` object when a valid existing LLM configuration lacks it. Future migrations must remain explicit.

Apply requires the current fingerprint and confirmation:

```text
POST /api/migration/apply
```

```json
{
  "plan_fingerprint": "<current fingerprint>",
  "confirm": true
}
```

Rollback uses the recorded operation ID:

```text
POST /api/migration/rollback
```

```json
{
  "operation_id": "migration_..."
}
```

Rollback restores exact previous bytes only if the migrated files have not changed since application.

## Verification sequence

### Compile and focused contracts

```bash
./app/env/bin/python -m compileall -q app tests
```

Run the focused tests for every conflicted subsystem. At minimum for a broad update:

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_llm_config_persistence \
  tests.test_phase17e_api_behavior \
  tests.test_phase17e_ui_behavior \
  tests.test_roster_pipeline_integration \
  tests.test_speaker_management \
  tests.test_llm_profile_routing \
  tests.test_voice_backend_capability_routes \
  tests.test_migration
```

### Complete offline suite

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest discover -s tests
```

### Interface acceptance

```bash
PYTHONPATH=app:tests ./app/env/bin/python tests/interface_browser_audit.py \
  --repo-root . \
  --output-dir /tmp/alexandria-interface-audit
```

Then run:

```bash
git diff --check
```

Follow the full matrix in [Interface Acceptance](INTERFACE_ACCEPTANCE.md).

## Live checks

Use an isolated or disposable project first. Verify:

- Setup loads and saves without deleting stage profiles;
- the selected LLM runtime still works;
- Script can start, resume, finalize, and reject incompatible checkpoints;
- fidelity and Review audits remain active;
- approved roster context reaches downstream stages;
- speaker mutations invalidate stale audio and undo safely;
- CustomVoice, Clone, and VoiceDesign use MLX on Apple Silicon;
- capability reporting prevents unsupported adapter actions;
- saved scripts and legacy projects still load;
- final audiobook assembly succeeds.

Do not use a full production book as the first post-merge test.

## Completing the update

When verification passes:

```bash
git status --short
git diff --check
git commit
```

Merge the integration branch into the intended feature/release branch without squashing away evidence commits unless the project’s release process explicitly calls for it.

Push only to the fork remote:

```bash
git push origin <branch>
```

Do not push to `upstream`.

## Pinokio Update button

The current `update.js` runs `git pull` and then reruns `install.js`. That is suitable only for ordinary fast-forward updates on the configured branch. It is not a substitute for the controlled upstream-integration process above.

If the checkout has local source changes, diverged history, or new upstream conflicts, do not use the one-click update path until the Git state has been reconciled manually.

See [Apple Silicon](APPLE_SILICON.md), [Native Ollama](NATIVE_OLLAMA.md), and [Generation Metadata](GENERATION_METADATA.md).
