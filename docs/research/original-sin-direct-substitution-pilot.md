# Original Sin direct adaptation substitution pilot

Round: `alexandria_original_sin_direct_substitution_pilot_v1`

The pilot contains six exact book chunks and ten hidden production-format MP3 candidates. Every candidate passed the following gates before entering the review:

- exact character and chunk binding;
- exact normalized book/adaptation transcript;
- word-level source alignment with bounded leading and trailing margins;
- first- and final-word preservation on the extracted WAV;
- first- and final-word preservation after MP3 encoding;
- MP3, 44.1 kHz, stereo production-proxy verification;
- stable source and output SHA-256 evidence;
- unchanged `voice_config.json` and `chunks.json` hashes.

## Pilot contents

| Character | Chunk | Transcript-safe candidates | Rejected before review |
|---|---:|---|---|
| Roz Forrester | 1684 | source mix; MossFormer2 source mix | — |
| Rashid | 405 | source mix; MossFormer2 source mix | — |
| Powerless Friendless | 5207 | source mix; MossFormer2 source mix | — |
| Zebulon Pryce | 3106 | source mix | MossFormer2 and Mel-RoFormer changed “name terms” to “main turn” |
| Hater of Humans | 3908 | source mix; Mel-RoFormer vocal | — |
| Securitybot | 493 | source mix | MossFormer2 changed “Ident confirmed” to “I then confirmed” |

Candidate IDs, source ranges, and production-proxy fingerprints are recorded in `benchmarks/original_sin_direct_substitution_pilot_summary.json`.

Those fingerprints bind the exact built artifact set dated `2026-07-31T02:17:33.822563Z`. Rebuilding processed candidates creates a new artifact set that must receive new candidate IDs and a new review; an earlier listening decision cannot be transferred by treatment name alone.

## Chris Cwej semantic variant

The adaptation says “gonna let” where the book says “going to let.” The user explicitly accepted these as semantically equivalent. That line may enter a future direct-substitution round after the current Chris extraction candidate passes the separate repair/identity listening gate. The accepted wording difference must remain explicit in provenance.

## Promotion boundary

This pilot does not modify project Voices or chunks. A candidate becomes substitution-eligible only after the completed blind review confirms complete boundaries, no adjacent speaker, no unapproved music/effects, and sufficient production usefulness. Installation still requires a separate promotion receipt and rollback evidence.
