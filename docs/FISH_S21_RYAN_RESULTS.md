# Fish S2.1 Ryan Calibration Results

This document records the decoded results of Tristan's complete 64-candidate Ryan review exported on July 28, 2026. The public review contained 48 Fish S2.1 Pro cloud generations and 16 local baselines. Scores were joined to the private answer key only after all 64 candidates were reviewed.

## Headline result

Fish S2.1 Pro was substantially stronger than the local comparison set for identity and naturalness, but its emotion and delivery control was inconsistent.

| Group | Identity | Delivery | Naturalness | Artifacts | Approved | Requested mode clear |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fish S2.1 cloud, 48 samples | 4.60 | 3.75 | 4.90 | 1.00 | 56.2% | 39.6% |
| Local baselines, 16 samples | 3.38 | 2.75 | 3.81 | 1.88 | 18.8% | 37.5% |

Artifacts use the review scale where 1 is clean and 5 is severely broken.

## Prompt-form comparison

Both Fish conditions in this first round used bracket syntax. There was no untagged control.

| Bracket condition | Identity | Delivery | Naturalness | Approved | Requested mode clear |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full Alexandria instruction | 4.58 | 3.88 | 4.88 | 54.2% | 41.7% |
| Concise Fish-oriented description | 4.63 | 3.63 | 4.92 | 58.3% | 37.5% |

The full Alexandria instruction produced slightly better delivery and clarity. The concise tag produced a slightly higher keep rate and identity score. The difference is too small and confounded by reference length to select a production prompt form.

## Reference-length comparison

| Reference tier | Identity | Delivery | Naturalness | Approved | Requested mode clear |
| --- | ---: | ---: | ---: | ---: | ---: |
| Approximately 5 seconds | 4.31 | 3.50 | 4.88 | 37.5% | 31.2% |
| Approximately 12 seconds | 4.88 | 3.69 | 5.00 | 56.2% | 37.5% |
| Approximately 31 seconds | 4.62 | 4.06 | 4.81 | 75.0% | 50.0% |

The long reference was best overall for emotional delivery, but it was not neutral. It combined several acted Ryan clips, including sad and grief material, and contaminated neutral delivery. Neutral samples using the long tier averaged 3.25 for delivery and were described as unexpectedly sad. The 12-second tier is therefore the cleanest fixed reference for prompt-control testing.

## Delivery-specific Fish result

| Delivery | Identity | Delivery score | Naturalness | Approved | Requested mode clear |
| --- | ---: | ---: | ---: | ---: | ---: |
| Neutral | 4.42 | 4.33 | 4.83 | 75.0% | 75.0% |
| Grief | 4.75 | 4.00 | 5.00 | 58.3% | 58.3% |
| Sarcasm | 4.67 | 3.92 | 5.00 | 66.7% | 8.3% |
| Fear | 4.58 | 2.75 | 4.75 | 25.0% | 16.7% |

Fear is the clearest failure. Sarcasm often sounded good and remained highly natural, but the requested ironic delivery was rarely unmistakable. This supports a dedicated prompt-control round rather than treating naturalness as evidence of instruction compliance.

## Local baseline findings

| Model | Identity | Delivery | Naturalness | Artifacts | Approved |
| --- | ---: | ---: | ---: | ---: | ---: |
| VoxCPM2 | 3.75 | 4.50 | 4.75 | 1.75 | 50.0% |
| IndexTTS2 | 4.50 | 3.25 | 4.75 | 1.75 | 0.0% |
| Chatterbox Multilingual V3 | 4.25 | 2.25 | 4.75 | 1.00 | 25.0% |
| Local Fish S2 Pro conversion | 1.00 | 1.00 | 1.00 | 3.00 | 0.0% |

The local Fish S2 Pro conversion produced the repeatedly noted chipmunk output and failed every delivery. It is excluded from the corrected prompt-control comparison. MOSS is also excluded from the coordinated four-identity round because the prior multimodel evidence does not provide a uniformly review-eligible cell for every identity and delivery. The balanced baseline set is IndexTTS2, VoxCPM2, and Chatterbox V3.

## Corrective action

The follow-up evaluation holds one reference model constant per identity and compares:

1. no bracket tag;
2. a simple bracket tag such as `[sad]` or `[scared]`;
3. a rich Fish-oriented natural-language bracket tag;
4. the complete Alexandria delivery instruction in brackets.

It covers Ryan, Narrator, Benny, and Doctor in separate blind identity reviews under one launcher.