# Benchmarking

Alexandria requires measured evidence before changing production models or claiming a performance feature. Benchmarks are committed as structured artifacts so a later review can distinguish observed results from assumptions.

## LLM benchmark corpus

The LLM corpus lives under `benchmarks/`:

- `benchmarks/manifest.json` defines required cases and run counts;
- `benchmarks/passages/` contains source fixtures;
- `benchmarks/expected/` contains expected structural and audit references;
- `benchmarks/run_benchmarks.py` executes the model suite;
- `benchmarks/results/` stores raw and summarized results.

Coverage includes narration, simple and interrupted dialogue, inverted attribution, pronouns, three speakers, aliases/titles, internal quotations, letters read aloud, chapter headings, long narration, emotional changes, nonhuman speakers, multi-chunk continuity, and ordinary/contextual Review.

The production-readiness result for `qwen3.5:35b-mlx` is:

`benchmarks/results/20260716T030056Z_qwen35_production_readiness.summary.json`

The follow-up addendum records the targeted attribution/nonhuman corrections and promotion decision. The Phase 22 aggregate records 100% schema, Script-audit, and Review-audit pass rates across 51 runs, with **67.27 output tokens/s** and **2.15 seconds per case** on average.

## Required LLM metrics

A stage-model comparison must record:

- model and effective runtime settings;
- schema success rate;
- internal corrective retry rate;
- outer retry rate;
- Script audit pass rate;
- Review audit pass rate;
- missing-word count;
- punctuation accuracy;
- speaker accuracy;
- narrator/dialogue accuracy;
- alias consistency;
- prompt and output token rates;
- average and total elapsed time;
- all failing case IDs and categories.

A faster model is not promotable if fidelity or regression gates fall.

## Stage model evidence

Changing a model in `llm.profiles` requires an evidence object containing:

- a benchmark identifier;
- inherited and target model names;
- quality comparison PASS;
- fidelity validation PASS;
- runtime measurement completed;
- regression tests PASS;
- approval time and notes.

The backend validates this record. The Setup UI cannot bypass it.

## Apple Silicon TTS benchmark

`benchmarks/run_phase22_benchmarks.py` runs real TTS paths in isolated subprocesses so model-load time and peak process RSS do not bleed across measurements.

The stable result is:

`benchmarks/results/20260717T014952Z_phase22_apple_silicon.json`

It records:

- hardware and package versions;
- PyTorch MPS availability and basic autograd;
- the actual `qwen_tts` import result;
- existing LLM readiness metrics;
- script-fingerprinting cost;
- VoiceDesign cold/warm generation;
- VoiceDesign-generated Qwen Clone cold/warm generation;
- controlled supplied-clip VoxCPM2 generation, identity similarity, and real `TTSEngine` routing;
- CustomVoice cold/warm generation;
- accent-pipeline cost;
- mixed-length orchestration behavior;
- the committed `unsupported` in-process MLX LoRA outcome and blockers.

## Interpreting RTF

Real-time factor is:

```text
generation seconds / output audio seconds
```

- below 1.0: faster than real time;
- equal to 1.0: real time;
- above 1.0: slower than real time.

Cold RTF includes first model load in a fresh worker. Warm RTF measures a second generation with the model resident.

## Measured M2 Max summary

| Path | Cold RTF | Warm/aggregate RTF | Peak RSS |
| --- | ---: | ---: | ---: |
| VoiceDesign | 2.32 | 0.30 | 3.34 GiB |
| VoiceDesign-generated Qwen Clone | 1.63 | 0.33 | 3.37 GiB |
| Controlled supplied-clip Clone | 4.38 s model load | 0.78–0.85 | 5.65 GiB* |
| CustomVoice | 2.07 | 0.29 | 3.35 GiB |
| Accent pipeline | 3.32 | — | 6.26 GiB |
| Mixed-length CustomVoice | — | 0.29 | 3.34 GiB |

The controlled supplied-clip result records speaker cosine similarity of 0.976 for neutral delivery and 0.960 for expressive delivery. Its peak RSS includes VoxCPM2 synthesis and the separate Qwen speaker-evaluation model resident in the same process. The real `TTSEngine` smoke generated 3.20 seconds of audio in 2.97 seconds after a 3.39-second model load.

The mixed-length method is a **sequential loop**, explicitly recorded as `sequential_loop`, not true tensor batching.

## Reproducing measurements

LLM suite:

```bash
./app/env/bin/python benchmarks/run_benchmarks.py --help
```

Apple Silicon TTS suite:

```bash
./app/env/bin/python benchmarks/run_phase22_benchmarks.py
```

The TTS command loads large local models and produces temporary audio. It should be run deliberately, not as part of ordinary unit testing.

## Benchmark integrity rules

- Record the Git HEAD and environment.
- Do not overwrite an older result unless reproducing the exact named boundary intentionally.
- Preserve raw results alongside summaries.
- Do not infer subjective preference from speed metrics.
- Do not claim a backend is supported because a lower-level framework probe passes.
- Record unavailable paths as unavailable; do not substitute a different architecture and call it equivalent.
- Rerun the complete offline suite after adopting any measured optimization.
