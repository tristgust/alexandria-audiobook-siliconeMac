# Script and Character-Roster Performance

Phase 24D covers two separate performance boundaries:

1. permanent model-free regression gates for Alexandria's deterministic Script and Character-roster work; and
2. persisted end-to-end timing for real Script-generation and roster-discovery runs.

The offline gates catch pathological local regressions. The runtime records explain where an actual Ollama-backed run spends time without claiming that one machine, model, prompt, or thermal state predicts another.

## Persisted runtime measurements

The live command paths write independent, capped JSON sidecars:

- `logs/stages/script_metrics.json`
- `logs/stages/roster_metrics.json`

These files use `stage_metrics` schema version 1. They are not part of `generation_state.json` or `character_roster_state.json`, do not affect source or generation fingerprints, and cannot make a valid generation result fail. Missing or corrupt metrics disable timing with a warning while the underlying Script or roster work continues unchanged.

Each sidecar records:

- stage and generation run identity;
- total units and any completed resume baseline;
- ordered measured units;
- running, failed, or complete status;
- reconciliation and finalization events where applicable;
- UTC start/update timestamps and a failure message when present.

Interrupted runs retain measured units and continue from the existing checkpoint. A completed run starts a fresh metrics document when the same source and configuration are deliberately run again.

Timing sidecars are accepted only when their measured unit indices are contiguous after the persisted resume baseline. If a checkpoint write succeeds but the following timing-sidecar write is interrupted, the next invocation re-baselines timing to the authoritative checkpoint and discards the now-ambiguous older sample window. A sidecar cannot report `complete` until its baseline plus measured units accounts for every expected unit. Non-finite native runtime values are ignored in favor of finite local wall time, so `NaN` or infinity cannot leak into JSON, throughput, or ETA calculations.

### Script unit timing

Every newly generated and checkpointed Script chunk records:

- prompt assembly;
- complete request wall time;
- native Ollama total, load, prompt-evaluation, and generation time when supplied by the runtime;
- native schema-validation time;
- JSON extraction, repair, salvage, and deterministic source-segment normalization;
- exact source-fidelity audit;
- checkpoint write;
- full unit wall time;
- logical/model attempt count and corrective-retry count;
- prompt and output token counts;
- validation mode and output-entry count.

Native corrective retries aggregate both model calls instead of reporting only the successful correction. Script finalization is recorded separately, including a finalize-only recovery retry when an already complete checkpoint is converted into final artifacts.

### Character-roster timing

Every newly discovered and checkpointed roster passage records:

- continuity-context and prompt assembly;
- complete request wall time;
- native Ollama total, load, prompt-evaluation, and generation time when supplied by the runtime;
- native schema-validation time;
- exact source-evidence validation;
- checkpoint write;
- full unit wall time;
- attempts, corrective retries, token counts, validation mode, and observation count.

Whole-book reconciliation is a separate event with its own prompt, request/model, schema, reconciliation-integrity, token, retry, and wall timings. Draft construction, verified artifact write, state cleanup, and total finalization time are recorded separately.

## Throughput and ETA

After each measured unit Alexandria calculates rolling throughput from at most the five most recent units. Unit wall time has a conservative lower bound equal to the non-overlapping measured work, preventing impossible throughput when a runtime reports a longer request duration than the caller's local timer.

An ETA is shown only when all of the following are true:

- the stage is still running;
- at least three recent units have completed;
- work remains;
- none of the recent units required a corrective retry; and
- the most recent persisted update is a valid timezone-aware timestamp no more than 15 minutes old and not in the future.

The estimate uses the slower of the recent mean and median unit duration, multiplies it by remaining units, and adds a 15 percent buffer. Otherwise the sidecar reports why ETA is unavailable, such as insufficient completed units, recent retries, a non-running stage, or completion. ETA is deliberately suppressed rather than presenting unstable precision.

Schema-1 sidecars remain readable when an older producer omitted a unit wall-time sample or wrote a non-parseable timestamp. Those legacy samples cannot publish inflated throughput or a reliable ETA. New writes require a positive `unit_wall` for every unit and normalize explicit timestamps to timezone-aware UTC.

## Representative offline workloads

`tests/script_roster_performance_harness.py` runs medians over:

- 6,000 valid Script entries through Alexandria's native Script contract validator;
- the same 6,000 entries through canonical JSON fingerprinting;
- exact source-fidelity audit against the combined representative source;
- Script speaker-run chunk grouping;
- enough roster-discovery payloads to cover at least 1,500 observations through the native roster-discovery schema validator, respecting any per-response item cap;
- the same roster payload set through canonical JSON fingerprinting.

The harness verifies output correctness before accepting timings. It fails if validation silently normalizes the fixture, fidelity fails, chunks are empty, fingerprints are malformed, or a measured median exceeds its regression budget.

Current regression budgets are intentionally generous enough for offline development and CI variance while still rejecting accidental quadratic behavior:

| Boundary | Median budget |
|---|---:|
| Script native contract, 6,000 entries | 2,500 ms |
| Script canonical fingerprint, 6,000 entries | 2,500 ms |
| Script source-fidelity audit, 6,000 entries | 6,000 ms |
| Script chunk grouping, 6,000 entries | 2,500 ms |
| Roster native contract, at least 1,500 observations | 4,000 ms |
| Roster canonical fingerprint, at least 1,500 observations | 2,500 ms |

The harness emits a machine-readable `SCRIPT_ROSTER_PERFORMANCE_REPORT=` JSON record containing measured medians, limits, workload sizes, chunk count, and failures.

## Real-browser timing

The Chrome CDP interface audit records the synchronous render duration for:

- verified Script import review at 1440×1000;
- unverified Script import review at 390×844;
- review-only roster structured-result rendering with 1,500 observations at 390×844.

Each render must complete within 250 ms. These measurements isolate Alexandria's own DOM update work and do not include model inference or network transfer. The same states remain subject to the existing overflow, hidden-native-input, stripe-free layout, console-error, and runtime-error checks.

The CDP probe records `renderMs` on the state report itself, and the Python verifier reads that same report field. Each measured review state explicitly opens its real disclosure before visibility, layout, and screenshot acceptance is evaluated.

Browser evidence is written to:

`/private/tmp/alexandria-phase24d-performance-audit`

## Interpretation and optimization policy

Passing the offline gate means Alexandria's local contract validation, fingerprinting, fidelity audit, chunk grouping, and structured-review rendering remain bounded at representative book-scale sizes. It does not claim a fixed end-to-end generation time. Runtime sidecars then identify whether a real run is dominated by prompt work, model load, prompt evaluation, token generation, validation, fidelity/evidence checks, checkpoint I/O, reconciliation, or finalization.

Production optimization must follow measured evidence. Do not weaken source fidelity, native schema validation, stale-result fingerprints, candidate review, checkpoint safety, or rollback behavior to improve a benchmark. Script generation and roster discovery remain intentionally sequential because later units depend on persisted continuity and evidence; no unverified model concurrency is introduced by Phase 24D.
