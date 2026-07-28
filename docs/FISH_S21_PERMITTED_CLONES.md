# Fish S2.1 Permitted Human Clone Evaluation

This evaluation extends the Fish Audio S2.1 Pro blind test to the Narrator, Benny, and Doctor recordings after the user explicitly confirmed permission to upload and clone all three references for this evaluation on July 28, 2026.

The generated evidence is isolated on `research/fish-s21-permitted-clones`. It is not a production provider integration and does not change Alexandria voice assignments or generated project audio.

## Evaluation matrix

Each identity has a separate blind reviewer so identity scoring remains coherent.

For each of Narrator, Benny, and Doctor:

- 2 Fish reference tiers: the existing conditioning clip and the complete available source recording;
- 4 delivery tests: neutral, grief, sarcasm, and fear;
- 2 instruction forms: Alexandria's complete instruction and a concise Fish bracket instruction;
- 2 independent Fish generations per cell;
- 4 matching local baselines per delivery: IndexTTS2, VoxCPM2, local Fish S2 Pro, and Chatterbox Multilingual V3.

That produces:

```text
32 Fish S2.1 candidates per identity
16 local baseline candidates per identity
48 blind candidates per identity
144 blind candidates across all three identities
```

Exact reference durations:

```text
Narrator  conditioning  9.6 seconds
Narrator  full source  35.0 seconds
Benny     conditioning  8.4 seconds
Benny     full source  29.2 seconds
Doctor    conditioning  6.2 seconds
Doctor    full source  24.1 seconds
```

The full-source MP3 files are normalized into private mono, 24 kHz, 16-bit PCM WAV copies before upload. The originals are not modified. Normalization receipts record the source and prepared hashes.

## Evidence paths

From the active permitted-clone worktree:

```text
.omo/evidence/fish-s21-permitted-clones/
  narrator/
  benny/
  doctor/
```

Each identity directory contains:

```text
review/                         Public blind reviewer
private/answer-key.json         Private provider/model/configuration key
private/fish-voice-models.json  Private Fish model IDs and fingerprints
private/prepared-references/    Hash-recorded normalized reference copies
outputs/fish_s21_pro/           Fish outputs and generation receipts
browser-smoke/                  Desktop and mobile reviewer verification
manifest.json                   Completion, permission, and integrity record
```

Do not inspect an identity's answer key before finishing that identity's blind review.

## Open a review

Keep port `8766` so all Fish reviews share the same browser origin while retaining separate round-specific local-storage keys.

Narrator:

```bash
cd /Users/tristan/.devspace/worktrees/alexandria-research-fish-s21-permitted-clones

PYTHONPATH=benchmarks python3 benchmarks/serve_fish_s21_review.py \
  --review-root .omo/evidence/fish-s21-permitted-clones/narrator/review \
  --port 8766
```

Benny:

```bash
PYTHONPATH=benchmarks python3 benchmarks/serve_fish_s21_review.py \
  --review-root .omo/evidence/fish-s21-permitted-clones/benny/review \
  --port 8766
```

Doctor:

```bash
PYTHONPATH=benchmarks python3 benchmarks/serve_fish_s21_review.py \
  --review-root .omo/evidence/fish-s21-permitted-clones/doctor/review \
  --port 8766
```

Open `http://127.0.0.1:8766/?reviewer=tristan`. Stop the current identity with Control+C before starting the next one. Scores autosave in Firefox local storage. Use **Export results** after completing each identity.

## Resume or rebuild

The API credential is read only from `FISH_API_KEY` or `FISH_AUDIO_API_KEY` and is not stored in source, receipts, manifests, or review packages.

Resume generation and packaging:

```bash
export FISH_API_KEY="..."
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_permitted_clones.py
```

Rebuild all three reviewers without API calls:

```bash
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_permitted_clones.py --package-only
```

Limit an operation to one identity with `--identity narrator`, `--identity benny`, or `--identity doctor`.

## Delete the six remote Fish voice models

Do this only after regeneration is no longer needed:

```bash
export FISH_API_KEY="..."
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_permitted_clones.py --delete-remote-voices
```

This deletes the conditioning and full-source private model for each identity. It does not delete local audio, reviewer results, or answer keys.

## Verification

The completed evaluation records:

- 96 Fish S2.1 outputs;
- 144 public blind-review audio files;
- 144 matching private answer rows;
- six trained, private Fish reference models;
- exact public audio hashes and decodable mono audio;
- no provider, model, prompt-form, reference-tier, or remote-ID leakage in public reviewers;
- no API-key occurrence in source or evidence;
- 1280×900 and 390×844 reviewer smoke tests for all three identities;
- byte-range-compatible serving and lazy/retryable audio controls.
