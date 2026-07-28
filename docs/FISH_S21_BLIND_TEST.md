# Fish Audio S2.1 Pro Blind Test

This evaluation compares Fish Audio S2.1 Pro against four existing Alexandria local baselines without uploading a human or licensed audiobook performance.

## Scope

The reference identity is the synthetic Qwen CustomVoice `Ryan` anchor from Alexandria's existing multimodel research. The binding matrix contains:

- 3 Fish reference tiers: 4.08 seconds, 12.0 seconds, and 31.28 seconds;
- 4 delivery tests: neutral, grief, sarcasm, and fear;
- 2 Fish instruction forms: Alexandria's complete instruction and a concise Fish bracket instruction;
- 2 independent Fish generations per cell;
- 4 existing local baseline candidates per delivery: IndexTTS2, VoxCPM2, the local Fish S2 Pro conversion, and Chatterbox Multilingual V3.

The complete package therefore contains 64 randomized candidates:

```text
48 Fish S2.1 Pro candidates
16 existing local baselines
64 total
```

Candidate configuration, provider identity, model identity, reference length, prompt form, repeat number, and remote reference IDs are kept outside the public review package.

## Generated evaluation

The current generated evidence lives in the active `research/fish-s21-blind-test` worktree:

```text
.omo/evidence/fish-s21-pro-blind-test
```

Important paths:

```text
review/                    Blind reviewer opened in a browser
private/answer-key.json    Configuration and model answer key
private/fish-voice-models.json
                           Private Fish reference-model IDs and fingerprints
outputs/fish_s21_pro/      Raw Fish outputs and generation receipts
manifest.json              Private completion and integrity summary
browser-smoke/             Desktop and mobile rendered verification
```

The API key is not stored in any file. A repository-wide and evidence-wide scan found no copy of the credential.

## Open the review

From the research worktree:

```bash
cd .omo/evidence/fish-s21-pro-blind-test/review
python3 -m http.server 8765 --bind 127.0.0.1
```

Then open:

```text
http://127.0.0.1:8765/?reviewer=tristan
```

Scores autosave in browser local storage. Use **Export results** to download the completed JSON review.

## Regenerate or resume

Set the Fish key only in the process environment:

```bash
export FISH_API_KEY="..."
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_blind_test.py
```

The runner resumes from hash-verified receipts and reuses matching private Fish voice models. It uses Fish's free evaluation model header:

```text
s2.1-pro-free
```

Do not place the key in `.env`, source, shell scripts, manifests, or task evidence.

## Rebuild the reviewer without API calls

```bash
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_blind_test.py --package-only
```

## Delete the remote Fish voice models

Delete them only after the blind review no longer needs to be regenerated:

```bash
export FISH_API_KEY="..."
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_blind_test.py --delete-remote-voices
```

This removes the three private Fish models recorded in `private/fish-voice-models.json`. It does not delete local generated audio or the answer key.

## Verification

Focused contract tests:

```bash
PYTHONPATH=benchmarks:tests \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  -m unittest -v tests.test_fish_s21_blind_test
```

Rendered reviewer smoke test:

```bash
node tests/fish_s21_review_smoke.js \
  --review-root .omo/evidence/fish-s21-pro-blind-test/review \
  --artifacts .omo/evidence/fish-s21-pro-blind-test/browser-smoke
```

The generated package currently passes:

- 64 unique public candidate IDs;
- 64 matching private answer rows;
- 48 Fish outputs and 16 local baselines;
- exact public audio hashes and decodable mono audio;
- zero provider/model/configuration leakage into public `data.js`;
- zero API-key occurrences;
- 1280×900 and 390×844 rendered smoke checks with no horizontal overflow, unnamed controls, console errors, or runtime exceptions.
