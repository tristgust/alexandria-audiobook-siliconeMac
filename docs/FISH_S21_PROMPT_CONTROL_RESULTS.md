# Fish Audio S2.1 Prompt-Control Results

Date analyzed: 2026-07-28

Branch: `research/fish-s21-prompt-calibration`

This report decodes Tristan's four completed blind-review exports against the private prompt-control answer keys for Ryan, Narrator, Benny, and Doctor.

## Data quality

- 176 scored rows were submitted across four 44-candidate reviews.
- 174 rows contain every required rating and are included in aggregate calculations.
- Two Narrator sarcasm rows are missing only `naturalness_1_to_5` and are excluded rather than imputed:
  - `5e53f649c7a3e393e5a9`: IndexTTS2 baseline.
  - `dff1e88fd336c14d2c89`: Fish simple-tag condition.
- This is one reviewer's evaluation with two Fish generations per prompt/style/identity cell. It is strong directional evidence, not a population study.
- Two rows have an internally unusual combination of artifact severity `5`, naturalness `5`, and approval `true`—Ryan rich-tag neutral repeat 2 and Narrator full-tag sarcasm repeat 2. Artifact averages should therefore be treated as secondary to delivery, identity, text fidelity, and the review decision. The recorded values are preserved unchanged.

## Overall comparison

| Candidate family | Samples | Identity | Delivery | Naturalness | Artifact severity | Retain rate | Mode clear | Text match |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fish S2.1 Pro cloud | 127 | 4.48 | 3.76 | 4.95 | 1.13 | 59.8% | 59.8% | 97.6% |
| Local baselines | 47 | 3.17 | 3.40 | 4.13 | 1.53 | 27.7% | 53.2% | 93.6% |

Fish S2.1 Pro is the clear quality leader among the tested candidates. Its largest gains are clone identity, naturalness, and clean output. Its remaining weakness is reliable delivery control—especially fear—not basic voice quality.

## Prompt-form comparison

| Fish prompt form | Samples | Identity | Delivery | Naturalness | Artifacts | Retain rate | Mode clear | Text match |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full Alexandria tag | 32 | 4.50 | **3.91** | 4.91 | 1.13 | **68.8%** | **62.5%** | 96.9% |
| Rich natural-language tag | 32 | 4.34 | 3.78 | 4.97 | 1.22 | 43.8% | 56.3% | 96.9% |
| Untagged | 32 | **4.56** | 3.72 | **5.00** | **1.06** | 62.5% | **62.5%** | 96.9% |
| Simple canonical tag | 31 | 4.52 | 3.65 | 4.94 | 1.13 | 64.5% | 58.1% | **100%** |

There is no universal best prompt. The full Alexandria tag is the strongest general-purpose control, but style and identity overrides materially improve results.

## Recommended prompt routing

### Neutral — simple tag

Use a short canonical neutral tag such as `[neutral]`.

Across identities it produced:

- 4.75 identity;
- 4.63 delivery;
- 100% retain rate;
- 100% mode-clear rate;
- 100% text match.

The full Alexandria tag scored slightly higher on delivery at 4.75, but it reduced identity, retained only 62.5%, and had one text mismatch. Long instructions add no useful value to an ordinary neutral line.

### Grief — full Alexandria tag

Use the full line-specific Alexandria delivery instruction inside brackets.

It produced the strongest grief result:

- 3.75 delivery;
- 75% retain rate;
- 100% text match.

Simple tags underperformed, while concise rich tags were less consistently retained. Grief benefits from the complete description of restraint, pain, and the effort not to break.

### Sarcasm — rich natural-language tag

Use the concise rich tag rather than a generic `[sarcastic]` label.

It produced:

- 4.50 delivery—the strongest sarcasm result;
- 4.63 identity;
- 87.5% mode-clear rate;
- 75% retain rate;
- 100% text match.

Untagged sarcasm was surprisingly usable and had a 100% retain rate, but rich tagging made the requested subtext clearer. Untagged generation is a sensible fallback candidate when rich-tag output sounds overperformed.

### Fear — full Alexandria tag, review required

The full Alexandria tag was best, but fear remains below production confidence:

- 3.00 delivery;
- 37.5% mode-clear rate;
- 62.5% retain rate.

All four prompt forms struggled. Fear should remain a review-required delivery with multiple candidates. A focused follow-up should test tag placement inside the sentence and paralinguistic cues such as uneven breathing, whispers, pauses, or a startled interruption rather than only a leading emotion description.

## Identity-specific behavior

| Identity | Best delivery form | Fish identity | Fish delivery | Fish naturalness | Fish retain rate | Local identity | Local retain rate |
|---|---|---:|---:|---:|---:|---:|---:|
| Ryan | Full Alexandria tag | 4.81 overall | 3.63 overall | 5.00 | 65.6% | 4.42 | 41.7% |
| Narrator | Full Alexandria tag | 4.77 overall | 3.68 overall | 5.00 | 61.3% | 2.18 | 27.3% |
| Benny | Rich tag | 4.50 overall | 3.75 overall | 4.97 | 65.6% | 4.00 | 33.3% |
| Doctor | Untagged | 3.84 overall | 4.00 overall | 4.84 | 46.9% | 2.00 | 8.3% |

### Doctor override

Doctor is the clearest case where elaborate tags can distort identity. Untagged Fish generation was the best identity-level condition:

- 4.38 identity;
- 4.13 delivery;
- 5.00 naturalness;
- 75% retain rate.

Default Doctor lines to untagged generation. Use the style router only when the line genuinely requires a stronger performance, and generate alternatives rather than trusting the first result.

Doctor also produced all three Fish text mismatches in the complete dataset, all on the neutral target line and across different prompt forms. That points to a voice/text interaction rather than one bad tag. Exact-text validation and automatic retry are mandatory for this provider.

### Narrator

Fish preserves Narrator identity dramatically better than every local baseline. Full Alexandria tags produced the highest delivery, but sarcasm was exceptionally strong under both rich and full tags while fear remained ineffective under every prompt form.

### Benny

Benny is the most balanced human clone. Rich tags produced the highest delivery at 4.00, while full and simple tags each retained 75%. The default style router is appropriate; no broad Benny-specific override is needed.

### Ryan

Ryan remains the strongest and most stable identity. Full Alexandria tags were retained for all eight samples. Even so, Ryan fear remained weak, confirming that the fear problem is model/control related rather than caused only by human-reference quality.

## Local fallback ranking

| Local model | Samples | Identity | Delivery | Naturalness | Artifacts | Retain rate |
|---|---:|---:|---:|---:|---:|---:|
| VoxCPM2 | 16 | **3.44** | **3.94** | **4.13** | **1.31** | **50.0%** |
| IndexTTS2 | 15 | 3.13 | 3.80 | 4.07 | 2.07 | 33.3% |
| Chatterbox Multilingual V3 | 16 | 2.94 | 2.50 | 4.19 | 1.25 | 0% |

VoxCPM2 is the best current local fallback overall, particularly for Ryan and Benny. None of the local candidates is a convincing high-fidelity fallback for Narrator or Doctor. Chatterbox V3 should be removed from expressive-clone finalist comparisons; it may remain useful in other TTS roles, but it was not retained once in this test.

## Repeatability and generation policy

Among 63 complete two-generation Fish cells:

- 25 cells—39.7%—split on whether the candidate should be retained;
- 12 cells—19.0%—differed by at least two delivery points;
- 3 cells differed by three delivery points;
- 3 cells contained a text mismatch;
- 4 cells contained at least one artifact-severity score of 3 or higher.

A single Fish generation is not reliable enough to become authoritative audio.

Recommended production policy:

1. Generate two candidates for normal expressive dialogue.
2. Generate at least three candidates for fear, Doctor emotional lines, and other review-required deliveries.
3. Run exact-text or ASR alignment validation before publication.
4. Reject and retry text mismatches automatically.
5. Keep candidate selection and provenance in Alexandria rather than delegating final choice to the provider.

## Product decision

Fish S2.1 Pro should become Alexandria's **accepted optional cloud-quality provider and current quality ceiling**, not an unconditional replacement for the local stack.

Recommended provider policy:

- keep local generation as the privacy-preserving and offline path;
- expose Fish only after explicit cloud-processing consent;
- use the style router above rather than one universal tag format;
- mark fear and Doctor expressive lines review-required;
- generate multiple candidates and validate exact text;
- keep Fish-generated audio out of automatic production promotion until a human chooses a candidate;
- retain VoxCPM2 as the strongest local expressive-clone fallback;
- remove the failed local Fish conversion and Chatterbox clone lane from finalist status.

Commercial terms, retention policy, and post-free-tier pricing remain separate deployment decisions. This listening test establishes quality and control behavior only.
