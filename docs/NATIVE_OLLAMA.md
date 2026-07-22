# Native Ollama Runtime

Alexandria can use Ollama through its native JSON chat API instead of routing every request through the OpenAI-compatible endpoint. Native mode supplies the contract schema directly, controls thinking explicitly, records Ollama metrics, and supports preload/unload lifecycle actions.

## Default runtime

The default LLM configuration is:

```json
{
  "base_url": "http://localhost:11434/v1",
  "api_key": "local",
  "model_name": "qwen3.5:35b-mlx",
  "backend": "auto",
  "context_length": 40960,
  "keep_alive": -1,
  "thinking": false,
  "structured_output": true,
  "corrective_retry": true,
  "timeout": 1800
}
```

`backend: "auto"` detects a local Ollama URL and selects `ollama-native`. Use `backend: "ollama"` to require native Ollama or `backend: "openai"` to force the OpenAI-compatible path.

## Structured contracts

Every structured stage names a contract owned by `app/llm_schemas.py`, including Script, Review, Persona, alias resolution, roster discovery/reconciliation, and visual discovery/reconciliation.

When structured output is enabled, the native request includes the JSON schema. Alexandria then validates the returned value locally. If structural validation fails and corrective retry is enabled, it sends one bounded correction request containing the validation error and requires a complete replacement object.

This is distinct from script and review fidelity retries. A response can be structurally valid but still fail source-text auditing.

## Thinking control

`thinking: false` is sent explicitly to native Ollama. Alexandria does not rely on prompt phrases to suppress reasoning. If a server still returns thinking text, telemetry records its presence but only the validated JSON content enters the pipeline.

The generic parser also tolerates fenced JSON, harmless surrounding prose, and `<think>` or `<thinking>` blocks for compatibility with non-native servers.

## Context and residency

- `context_length` becomes Ollama `num_ctx`.
- `keep_alive: -1` keeps the model resident until explicitly unloaded.
- Preload makes a minimal native request before pipeline work.
- Unload sends `keep_alive: 0`.

Runtime diagnostics are available through the Setup interface and `/api/llm/status`, `/api/llm/preload`, and `/api/llm/unload`.

## Stage profiles

`llm.profiles` can override the runtime for eight stages:

- Script
- Review
- Persona
- Roster
- Visual Discovery
- Visual Compilation
- Dataset Text
- Transcript Cleanup

Profiles inherit the global runtime by default. A same-model connection or context override can be saved directly. Changing a stage model requires an evidence record showing benchmark, quality, fidelity, runtime, and regression gates all passed.

See the Stage model profiles section in [Benchmarking](BENCHMARKING.md).

## Telemetry

Native requests record bounded runtime telemetry in `logs/llm_runtime.json`, including:

- stage/contract;
- backend and model;
- validation mode;
- prompt and output token counts;
- prompt and output token rates;
- elapsed time;
- retry/failure classification;
- whether thinking content was present.

Raw prompts and API keys are not exposed in the browser status view.

## Recommended production model

The measured production model is `qwen3.5:35b-mlx`. The committed readiness result records:

- schema success: 100%;
- Script fidelity audit pass: 100%;
- Review audit pass: 100%;
- output rate: approximately 67.27 tokens/s;
- average case time: approximately 2.15 seconds.

The source evidence is `benchmarks/results/20260716T030056Z_qwen35_production_readiness.summary.json` and the follow-up addendum.

## Troubleshooting

**Runtime stays on OpenAI-compatible mode**

Confirm the base URL points to local Ollama, normally `http://localhost:11434/v1`, and that backend is `auto` or `ollama`.

**Preload fails**

Confirm Ollama is running and the exact model name exists. A failed preload does not necessarily prevent the first generation request from loading the model.

**Checkpoint becomes incompatible after a profile edit**

This is intentional. Effective model/runtime identity is part of resumable generation. Restore the prior profile or explicitly discard the checkpoint.

**Model change is rejected**

Supply a complete evidence record or continue inheriting the production model. The backend does not permit an unmeasured per-stage model swap.
