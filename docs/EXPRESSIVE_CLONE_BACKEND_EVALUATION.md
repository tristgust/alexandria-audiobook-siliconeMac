# Expressive supplied-voice backend evaluation

Status: benchmark foundation implemented. Fish S2 Pro, Chatterbox, Qwen, and VoxCPM2 were subsequently rerun with the real narrator clone and rejected as emotionally flat in manual listening. The follow-up evaluation of OpenVoice V2, IndexTTS2, and CosyVoice 3 is complete for speed, memory, speaker similarity, and transcription accuracy; see `docs/EMOTIONAL_CLONE_FOLLOWUP_EVALUATION.md`. Blinded emotional-delivery listening remains open.

## Requirement

Alexandria needs one backend that can preserve a supplied speaker identity while changing performance line by line. A valid backend must prove all of the following on the same target text and reference material:

- recognizable speaker identity;
- directionality across neutral, urgent, restrained anger, panic, grief, whisper, and sarcasm;
- acceptable transcription accuracy;
- stable output across multiple deterministic seeds;
- usable speed and memory on the target Apple Silicon machine;
- acceptable naturalness and artifacts in blinded listening.

No model is production-supported merely because its API exposes an instruction, tag, exaggeration value, or reference input.

## Current local findings

The Alexandria environment contains MLX-Audio 0.4.5. Its installed adapters include Fish Audio S2 Pro, original Chatterbox, Chatterbox-Turbo, TADA, MOSS-TTS, and MOSS-TTS Nano.

The exact pinned Fish S2 Pro, original Chatterbox, S3TokenizerV2, and Chatterbox-Turbo MLX snapshots are now cached explicitly for evaluation. Their authoritative snapshot inventory is 12,531,600,554 bytes. They are not added to Alexandria Setup, the production registry, or any Voice assignment. TADA 1B, MOSS-TTS Nano, and MOSS-TTS Local Transformer v1.5 remain absent.

Fish's SDK transfer repeatedly failed through the workspace transport. The required codec and model shards were resumed from their largest partials, then checked against the published exact LFS sizes and SHA-256 hashes before becoming canonical cache blobs. Four redundant incomplete blobs totaling 7,276,502,015 bytes remain untouched because removing cache data is a separate destructive cleanup decision.

An upstream `ResembleAI/chatterbox-turbo` PyTorch snapshot also exists, but it is not treated as the pinned MLX conversion. The existing Qwen Base and VoxCPM2 models remain comparison paths only. The Qwen path prepends an untrained instruction embedding to the Base clone input; the benchmark deliberately bypasses Alexandria's post-generation tempo, volume, and pause enforcement so model behavior is not confused with deterministic audio processing.

The installed SciPy `_spropack` binary is malformed, so Alexandria no longer imports the general SciPy installation for benchmark transcription. The repository-owned evaluator pins `mlx-whisper==0.4.3` and `mlx-community/whisper-base-mlx@1e3e249fb8d01c655324bd6841b1deadffd6d04c`, then supplies the one median-filter operation MLX Whisper needs through `alexandria_scipy_free_signal_shim_v1`. The worker remains local-only and refuses an unpinned runtime or model revision.

A real macOS `say` fixture completed through the isolated MLX worker with WER `0.0`; evidence is stored in `.omo/evidence/b17-t04-transcription-evaluator/known-transcript-result.json`. Required transcription mode now fails if the evaluator is unavailable or if even one requested sample lacks a successful result. Historical Fish and Chatterbox summaries still truthfully record that transcription was unavailable when those runs occurred; they are not rewritten retroactively.

## Pinned candidate set

| Candidate | Pinned model | Approximate local requirement | Native control translated by the runner | Current claim |
|---|---|---:|---|---|
| Fish Audio S2 Pro | `mlx-community/fish-audio-s2-pro@eccd57bf5c1ebc13cb2f993df867f4e49931a36a` | 11.0 GB | Free-form inline tags such as `[angry]`, `[whisper]`, and `[sad]` | All seven control paths measured; listening acceptance open |
| Chatterbox | `mlx-community/chatterbox-4bit@f1d7b9696e1b6242e64eb8c4a823b6d1a50425a8` plus pinned S3TokenizerV2 | 1.1 GB | Numeric exaggeration and CFG proxies | Identity plus intensity proxy; not arbitrary direction |
| Chatterbox-Turbo | `mlx-community/chatterbox-turbo-4bit@c63817725071d7b5269c7b558772d6e8cbf59cec` | 417 MB | Native paralinguistic event tags only | Fast hypothesis; does not pretend to understand arbitrary stage direction |
| TADA 1B | `HumeAI/mlx-tada-1b@b9e0e8c8f527464b9abd72c6fe3786f1f05ed1eb` | 4.59 GB | Direction-specific reference bank | Identity-first hypothesis; non-neutral lines require approved style references |
| MOSS-TTS Nano | `mlx-community/MOSS-TTS-Nano-100M@229a9c51bb0ffff6fd0dbe53b5bf0c441e438a79` plus pinned Nano tokenizer | 329 MB | Direction-specific reference bank | Small speed/throughput baseline |
| MOSS-TTS Local Transformer v1.5 | `OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5@be7766a6735b98bd793f7c79fb720b4d0f5d13b8` plus pinned tokenizer v2 | 17.6 GB | Instruction field and explicit pause syntax | Long-form/control hypothesis; very large footprint |
| Existing Qwen patch | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit` at Alexandria's pinned revision | Existing cache | Untrained instruction-embedding injection | Comparison only |
| Existing VoxCPM2 | `mlx-community/VoxCPM2-4bit` at Alexandria's pinned revision | Existing cache | Free-form instruction field | Comparison only |

Repository license metadata is captured in `app/expressive_clone_candidates.py`. That metadata is not a release-license approval. Fish reports a nonstandard license, TADA uses Llama 3.2 terms, and the selected Chatterbox/MOSS conversions report Apache-2.0.

## Implemented benchmark contract

`benchmarks/run_expressive_clone_matrix.py` now provides:

- exact revision and required-file checks before model loading;
- local-only worker processes with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`;
- one isolated process per candidate so large models can release memory between runs;
- fixed default directions and three deterministic seeds;
- the same primary reference, transcript, target text, and seed policy across compatible candidates;
- an optional direction-to-reference JSON map for TADA and MOSS Nano;
- explicit skip reasons when a model cannot represent a direction;
- raw model output only, with no Alexandria prosody post-processing;
- cold load time, generation time, real-time factor, peak process memory, duration, loudness, silence, dynamic range, zero-crossing rate, and words per second;
- optional Qwen speaker-embedding cosine similarity when the evaluator is cached;
- pinned MLX Whisper Base word error rate through the repository-owned SciPy-free evaluator; `required` mode rejects unavailable or partial results;
- SHA-256 fingerprints instead of stored source text or reference transcripts;
- randomized sample filenames, a blinded listening sheet, and a separate answer key;
- duration variation across seeds;
- a hard `production_promotion_allowed: false` result until the full matrix and listening gate are complete.

Chatterbox-Turbo is intentionally skipped for requested emotions that have no native event-tag translation. Original Chatterbox runs numeric proxies but records that they are not semantic instruction support. TADA and MOSS Nano skip non-neutral directions unless a matching reference-bank clip and transcript are supplied.

The real Chatterbox run exposed two MLX-Audio integration defects that the benchmark now confines locally. Original Chatterbox tried to resolve `S3TokenizerV2` at an unpinned `main` revision from inside its post-load hook; the worker now routes that lookup only to the exact registered snapshot. Original and Turbo also called MLX-Audio's SciPy resampler during reference conditioning; the worker now injects Alexandria's SOXR-based, SciPy-free resampling path without modifying the installed package or production runtime.

The first Fish run exposed another adapter mismatch. The installed model's packaged README shows a file path for `ref_audio`, but the implementation expects an MLX array. The worker now normalizes the reference to the model sample rate, reads float32 mono audio, and passes the array without modifying MLX-Audio itself.

## Fish S2 Pro candidate matrix

Fish used the same synthetic Ryan identity reference, target line, and seeds 1001/1002. It generated neutral, urgent, restrained anger, panic, grief, whisper, and sarcasm using the tags `urgent`, `angry`, `panicked`, `sad`, `whisper`, and `sarcastic`. The saved summary is `benchmarks/results/20260721T154722Z_fish_s2_pro_expressive_clone_matrix.json`.

| Result | Fish S2 Pro |
|---|---:|
| Raw samples | 14 |
| Candidate errors | 0 |
| Explicitly skipped | none |
| Cold load | 2.93–3.84 s |
| Peak process RSS | 10.799 GiB |
| Mean real-time factor | 1.560 |
| RTF range | 1.386–2.346 |
| Speaker cosine range | 0.9567–0.9865 |
| Duration range | 3.437–4.412 s |

Objective behavior changed by tag. Urgent and panic were louder than the neutral samples; whisper was roughly 11–14 dB quieter than neutral and had the lowest speaker cosine. These are useful sanity checks, not semantic acceptance. The benchmark cannot determine whether anger sounded restrained, grief sounded convincing, sarcasm landed, or the cloned identity remained acceptable to a listener. All 14 samples and one merged blind sheet are in `/tmp/alexandria-fish-s2-pro-blinded-review`; open `listening_review_blinded.json` before the separate answer key.

Fish is materially heavier and slower than both Chatterbox candidates on this machine. It is the only measured candidate in this slice that exercised all requested line-level conditions through a model-native tag interface. That makes it the strongest functional test result so far, but not an accepted backend.

## Chatterbox candidate matrix

The same repository-owned synthetic Ryan reference, target line, and seeds 1001/1002 were used for both Chatterbox candidates. The saved summary is `benchmarks/results/20260721T151508Z_chatterbox_expressive_clone_matrix.json`; raw audio and blinded-review files remain in `/tmp/alexandria-chatterbox-matrix`.

| Result | Original Chatterbox | Chatterbox-Turbo |
|---|---:|---:|
| Raw samples | 6 | 4 |
| Candidate errors | 0 | 0 |
| Generated controls | neutral, urgent, sarcasm | neutral, sarcasm |
| Explicitly skipped | none | urgent |
| Cold load | 2.385 s | 1.976 s |
| Peak process RSS | 1.399 GiB | 0.828 GiB |
| Mean real-time factor | 0.328 | 0.280 |
| RTF range | 0.261–0.644 | 0.241–0.353 |
| Speaker cosine range | 0.9769–0.9782 | 0.9442–0.9601 |
| Duration range | 3.84–4.76 s | 3.717–4.957 s |

Original Chatterbox produced every requested test condition, but `urgent` and `sarcasm` are numeric exaggeration/CFG mappings rather than proof that the model understood those descriptions. Turbo was smaller and faster, but its event-tag interface could not represent `urgent`; the runner refused to substitute an unrelated tag. Turbo also showed lower speaker cosine on this one synthetic fixture. None of those objective values establishes delivery adherence. The evaluator is now available, but these candidate outputs still require a same-corpus transcription rerun and blinded listening.

## Baseline harness smoke

A repository-owned synthetic QA reference was used to exercise the complete worker path without touching a user voice or production project. Qwen and VoxCPM2 each generated neutral and urgent samples at two deterministic seeds. All eight raw samples completed without candidate errors, the Qwen speaker evaluator ran, and blinded-review manifests were produced. The saved summary is `benchmarks/results/20260721T145449Z_expressive_clone_baseline_smoke.json`.

This validates model isolation, reference preparation, raw-audio measurement, multi-seed output, speaker evaluation, and review-manifest generation. It does not establish that either comparison backend followed the requested delivery. The evaluator repair was completed later and does not retroactively add WER to this saved run. Manual blinded listening and a same-corpus transcription rerun remain pending; both backends remain comparison-only.

## Instruction-control regression classification — July 21, 2026

The apparent regression is **configuration- and backend-policy-specific**, not a shared request-path failure.

A read-only trace through the actual `TTSEngine` proves three distinct contracts:

- `qwen3_base` receives the target text and supplied reference but does not receive the line `instruct` field. This is intentional standard-clone behavior, not a dropped parameter inside the backend.
- `qwen3_instruction_controlled` receives the exact line direction combined with the persistent identity constraint. The MLX patch tokenizes that combined instruction once and prepends its embedding ahead of the unchanged ICL reference/target prefill, matching the order used by the official PyTorch Qwen generator.
- `voxcpm2_controlled` is a legacy assignment and is now rejected by production because it did not establish a truthful, reliable per-line control contract.

The active project currently contains 28 supplied-recording clone assignments: 27 use standard `qwen3_base`, one uses blocked legacy `voxcpm2_controlled`, and none uses `qwen3_instruction_controlled`. The historical Doctor VoxCPM2 benchmark and the current Doctor assignments use the same reference-audio hash, so the observed loss is not explained by a changed Doctor reference file.

The Qwen instruction channel is present and can generate comparison previews, but it is not an accepted production capability. The current Qwen smoke produced four neutral/urgent samples without generation errors while deliberately bypassing deterministic post-generation prosody; delivery adherence remains unapproved and blinded listening remains pending. Production controlled-clone output separately applies deterministic prosody after model generation, so an audible tempo or level difference in production cannot be attributed to model-level instruction understanding without a raw pre-prosody comparison.

Evidence:

- `.omo/evidence/b17-t06-instruction-trace/result.json`
- `benchmarks/results/20260717T031401Z_voxcpm2_controlled_clone.json`
- `benchmarks/results/20260721T145449Z_expressive_clone_baseline_smoke.json`

Required product disposition remains unchanged: label standard clones as instruction-inert, require an exact preview/listen receipt before assigning the Qwen controlled path, and do not claim expressive adherence until same-corpus no/neutral/contrasting raw outputs pass objective checks and blinded listening.

## Commands

Inspect local readiness without loading or downloading a model:

```bash
PYTHONPATH=app:benchmarks ./app/env/bin/python \
  benchmarks/run_expressive_clone_matrix.py \
  --probe
```

Run selected candidates after their exact pinned snapshots and dependencies have been explicitly cached:

```bash
PYTHONPATH=app:benchmarks ./app/env/bin/python \
  benchmarks/run_expressive_clone_matrix.py \
  --candidate fish_s2_pro \
  --candidate chatterbox_original \
  --candidate chatterbox_turbo \
  --candidate tada_1b \
  --candidate moss_tts_nano \
  --candidate moss_tts_local_v15 \
  --include-comparison-baselines \
  --reference-audio /absolute/path/reference.wav \
  --reference-text-file /absolute/path/reference.txt \
  --text-file /absolute/path/target.txt \
  --reference-map /absolute/path/reference-bank.json \
  --output-dir /absolute/path/expressive-clone-matrix
```

Limit a diagnostic run without changing the active direction definitions:

```bash
PYTHONPATH=app:benchmarks ./app/env/bin/python \
  benchmarks/run_expressive_clone_matrix.py \
  --candidate fish_s2_pro \
  --reference-audio /absolute/path/reference.wav \
  --reference-text-file /absolute/path/reference.txt \
  --text-file /absolute/path/target.txt \
  --direction neutral \
  --direction urgent \
  --seeds 1001,1002 \
  --output-dir /absolute/path/diagnostic-run
```

Use `--require-all-candidates` when a partial run would be misleading. Use `--transcription-evaluation required` for any acceptance-oriented matrix; the command exits without publishing a successful result if the pinned evaluator is unavailable or any sample transcription fails. Fish and Chatterbox evaluation snapshots were explicitly cached, but candidate downloads are not added to Alexandria Setup or the production model registry in this boundary. Downloading every remaining candidate before an acquisition/license decision would expose additional unaccepted models and tokenizer dependencies.

Reference-bank format:

```json
{
  "urgent": {
    "audio": "/absolute/path/urgent.wav",
    "text": "Exact transcript for the urgent reference."
  },
  "grief": {
    "audio": "/absolute/path/grief.wav",
    "text": "Exact transcript for the grief reference."
  }
}
```

## Promotion gate

A candidate may move from evaluation to Alexandria integration only after:

1. every supported direction is generated with the fixed corpus and all seeds;
2. speaker similarity and transcription accuracy are available rather than omitted;
3. output duration and stability are acceptable for audiobook production;
4. blinded human ratings show reliable directionality without unacceptable identity loss or artifacts;
5. model license and disk footprint are accepted;
6. the selected backend receives a separate model-registry, Setup, runtime, UI, and migration implementation boundary.

Until then, the existing patched Qwen clone remains experimental and no alternative is the production default.
