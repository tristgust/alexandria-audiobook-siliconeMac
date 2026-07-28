# Fish S2.1 Four-Identity Prompt-Control Evaluation

This coordinated blind evaluation tests Fish S2.1 bracket control across Ryan, Narrator, Benny, and Doctor. The review is complete; see [Fish S2.1 Prompt-Control Results](FISH_S21_PROMPT_CONTROL_RESULTS.md) for the decoded findings and routing decision.

Fish documents bracket syntax as the S2/S2.1 natural-language control mechanism for emotion and paralinguistic delivery. Tags are not limited to a fixed vocabulary. The standard TTS endpoint also exposes temperature, but temperature controls variation rather than selecting a requested emotion. This round therefore isolates bracket form while holding generation settings and each identity reference constant.

Official references:

- https://docs.fish.audio/developer-guide/getting-started/changelog
- https://docs.fish.audio/developer-guide/models-pricing/models-overview
- https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech
- https://docs.fish.audio/developer-guide/best-practices/voice-cloning

## Identities and fixed references

| Identity | Reference used | Duration |
| --- | --- | ---: |
| Ryan | Existing standard Ryan reference model | 12.0 seconds |
| Narrator | Existing private full-source reference model | 35.0 seconds |
| Benny | Existing private full-source reference model | 29.2 seconds |
| Doctor | Existing private full-source reference model | 24.1 seconds |

The user explicitly confirmed permission to upload and clone Narrator, Benny, and Doctor on July 28, 2026. All retained Fish reference models are private.

## Prompt conditions

Each delivery is generated twice under four conditions:

1. **Untagged** — target text only.
2. **Simple tag** — `[neutral]`, `[sad]`, `[sarcastic]`, or `[scared]`.
3. **Rich Fish tag** — a concise natural-language description such as `[deep restrained grief, pain held back, close to breaking]`.
4. **Full Alexandria tag** — Alexandria's complete delivery instruction inside brackets.

The four tested deliveries remain neutral, grief, sarcasm, and fear.

## Candidate counts

Each identity review contains:

```text
4 Fish prompt conditions
× 4 deliveries
× 2 repeats
= 32 Fish candidates

3 local baselines
× 4 deliveries
= 12 baseline candidates

44 candidates per identity
```

Across four identities, the complete launcher contains 176 blinded candidates:

```text
128 Fish S2.1 candidates
48 local baseline candidates
176 total
```

The balanced baseline set is IndexTTS2, VoxCPM2, and Chatterbox Multilingual V3. The local Fish S2 Pro conversion is excluded because the completed Ryan review scored it 1/5 for identity, delivery, and naturalness across all four styles and repeatedly identified chipmunk output. MOSS is excluded because its previous multimodel cells were not uniformly review-eligible for every identity and delivery.

## Generated evidence

The generated evidence is stored in the active prompt-control research worktree:

```text
.omo/evidence/fish-s21-prompt-controls
```

The root contains a launcher. Each identity contains its own review and private answer key:

```text
index.html
manifest.json
ryan_synthetic/review/
ryan_synthetic/private/answer-key.json
narrator/review/
narrator/private/answer-key.json
benny/review/
benny/private/answer-key.json
doctor/review/
doctor/private/answer-key.json
```

Existing rich and full-tag samples were reused only after verifying their audio hash, prompt hash, reference fingerprint, and generation settings. Untagged and simple-tag samples were newly generated. The API credential is absent from source and evidence.

## Open the coordinated review

From the prompt-control research worktree:

```bash
PYTHONPATH=benchmarks python3 benchmarks/serve_fish_s21_review.py \
  --review-root .omo/evidence/fish-s21-prompt-controls \
  --port 8766
```

Open:

```text
http://127.0.0.1:8766/
```

The launcher links to all four identities. Complete each review separately. Scores autosave under its unique round ID. Export filenames include the round and identity, preventing one result from overwriting another.

## Regenerate or resume missing cells

Set the Fish key only for the process:

```bash
export FISH_API_KEY="..."
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_prompt_controls.py
```

The runner resumes hash-verified untagged and simple-tag receipts and reuses the verified rich/full samples from the earlier rounds. It does not create duplicate reference models.

## Rebuild review packages without API calls

```bash
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/run_fish_s21_prompt_controls.py --package-only
```

## Decode completed exports

After exporting all four reviews, analyze them together without manually opening the answer keys:

```bash
PYTHONPATH=benchmarks \
  /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python \
  benchmarks/analyze_fish_s21_prompt_controls.py \
  ~/Downloads/alexandria_fish_s21_prompt_controls_v1_ryan_synthetic_tristan.json \
  ~/Downloads/alexandria_fish_s21_prompt_controls_v1_narrator_tristan.json \
  ~/Downloads/alexandria_fish_s21_prompt_controls_v1_benny_tristan.json \
  ~/Downloads/alexandria_fish_s21_prompt_controls_v1_doctor_tristan.json \
  --output .omo/evidence/fish-s21-prompt-controls/decoded-results.json
```

The analyzer validates every sample ID and required score, joins each export to its matching private answer key, ranks prompt conditions primarily by delivery score and mode clarity, and reports identity-specific and delivery-specific results.

The completed July 28 review contained 176 submitted rows. Two Narrator sarcasm rows omitted only the naturalness rating; the final analysis excludes those two from aggregate means rather than imputing values. The remaining 174 complete rows support the routing policy in [Fish S2.1 Prompt-Control Results](FISH_S21_PROMPT_CONTROL_RESULTS.md).

## Verification requirements

- 44 public candidates and 44 private answer rows per identity;
- eight Fish samples per prompt condition per identity;
- four baseline samples per model per identity;
- exact public audio hashes and decodable mono audio;
- no provider, model, prompt mode, or reference-tier leakage into public data;
- byte-range-capable localhost serving;
- desktop and mobile browser smoke checks;
- no API-key occurrence in source or evidence.