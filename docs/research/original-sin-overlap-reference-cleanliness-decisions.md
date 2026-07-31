# Original Sin overlap reference cleanliness decisions

Round: `alexandria_original_sin_overlap_reference_cleanliness_v1`

Candidates: 51 across 17 characters.

This analysis made no Alexandria Voice or chunk assignments. Exact-line substitution remains gated by a separate blind round.

## Character decisions

| Character | Outcome | Winner | Treatment |
|---|---|---|---|
| Bernice Summerfield | useful after bounded repair | — | — |
| The Doctor | requires a replacement source or new extraction | — | — |
| Chris Cwej | useful after bounded repair | — | — |
| Roz Forrester | approved neutral identity anchor | `f65ced4c8b19fa45` | `mossformer2_source_mix` |
| Beltempest | useful after bounded repair | — | — |
| Under-Sergeant | useful after bounded repair | — | — |
| Rashid | approved neutral identity anchor | `636fc8e6fc622e73` | `mossformer2_source_mix` |
| Computer | requires a replacement source or new extraction | — | — |
| Doc Dantalion | requires a replacement source or new extraction | — | — |
| Homeless Forsaken | requires a replacement source or new extraction | — | — |
| Powerless Friendless | approved neutral identity anchor | `98968650c708e630` | `mossformer2_source_mix` |
| Zebulon Pryce | approved neutral identity anchor | `a95cd3ed32e029c8` | `mossformer2_source_mix` |
| Hater of Humans | approved performance-only reference | `1507f477408ce915` | `mel_roformer_vocal` |
| Evan Claple | useful after bounded repair | — | — |
| Shythe Shahid | requires a replacement source or new extraction | — | — |
| Securitybot | approved neutral identity anchor | `5a64fec21e6a4c0e` | `mossformer2_source_mix` |
| Tobias Vaughn / Robot | useful after bounded repair | — | — |

## Candidate evidence

| Candidate | Character | Treatment | Transcript | First word | Scores I/N/ID/U | Human | Classification | Notes |
|---|---|---|---|---|---|---|---|---|
| `76a27f6f82239654` | Bernice Summerfield | `source_mix` | pass (WER 0.000) | pass | 1/5/5/2 | fail | human-rejected | — |
| `81f137c30a86842e` | Bernice Summerfield | `mossformer2_source_mix` | pass (WER 0.000) | pass | 2/5/5/4 | pass | useful after bounded repair | useful line for sure but it has another voice in there that needs to be removed and at least one background sound |
| `88cb6f72bbc88146` | Bernice Summerfield | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/5/4 | pass | useful after bounded repair | useful line for sure but it has another voice in there that needs to be removed and at least one background sound |
| `2824aaa3dcfe7a50` | The Doctor | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/5/3 | fail | human-rejected | — |
| `d0b70f7ee1c7cf22` | The Doctor | `source_mix` | pass (WER 0.000) | pass | 1/5/5/2 | fail | human-rejected | — |
| `e771965daeba450a` | The Doctor | `mossformer2_source_mix` | fail (WER 0.111) | fail | 5/5/5/5 | pass | objective-ineligible | — |
| `188f071a11c5a461` | Chris Cwej | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/5/3 | fail | human-rejected | — |
| `2cccf36317dfd8b0` | Chris Cwej | `source_mix` | pass (WER 0.000) | pass | 1/5/5/2 | fail | human-rejected | — |
| `4b9ad9d50fd11abd` | Chris Cwej | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/5/5 | pass | useful after bounded repair | cut off a bit early at the end of direction there with an artifact too |
| `52a144fa293c45b9` | Roz Forrester | `source_mix` | pass (WER 0.000) | pass | 2/5/5/3 | fail | useful after bounded repair | dog bark and other sound effects in background... |
| `6068535c66b1cfd2` | Roz Forrester | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/5/4 | fail | useful after bounded repair | dog bark in the background... |
| `f65ced4c8b19fa45` | Roz Forrester | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/5/5 | pass | approved neutral identity anchor **SELECTED** | — |
| `8aad0ca915f3bc2c` | Beltempest | `mossformer2_source_mix` | pass (WER 0.000) | pass | 4/5/5/5 | pass | useful after bounded repair | cut off the last word (rear) a bit early. |
| `98379a74fa867490` | Beltempest | `source_mix` | pass (WER 0.000) | pass | 2/5/5/1 | fail | useful after bounded repair | cut off the last word (rear) a bit early, tons of background music and effects |
| `decdf516ed676d4e` | Beltempest | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/5/3 | fail | useful after bounded repair | cut off the last word (rear) a bit early. bit of an echo |
| `15fa34d9b25cafea` | Under-Sergeant | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/5/3 | pass | useful after bounded repair | sounds like he might be talking over a radio or speaker or something? do we have a version where he does not sound like that? or is that just how he sounds |
| `1624205c4ba06e45` | Under-Sergeant | `mossformer2_source_mix` | fail (WER 0.667) | fail | 3/5/5/5 | fail | objective-ineligible | — |
| `6f9c1dc53fc913e0` | Under-Sergeant | `source_mix` | fail (WER 0.167) | pass | 2/5/5/2 | fail | objective-ineligible | — |
| `10c9c24bd28b220b` | Rashid | `source_mix` | pass (WER 0.000) | pass | 3/5/5/3 | fail | human-rejected | — |
| `636fc8e6fc622e73` | Rashid | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/5/5 | pass | approved neutral identity anchor **SELECTED** | — |
| `bdd0cf048ab12481` | Rashid | `mel_roformer_vocal` | fail (WER 0.667) | fail | 4/5/5/4 | fail | objective-ineligible | — |
| `81af00878812b2d2` | Computer | `mel_roformer_vocal` | fail (WER 0.111) | pass | 4/5/5/3 | fail | objective-ineligible | artifact at the start |
| `a235f945bfacfccb` | Computer | `source_mix` | fail (WER 0.111) | pass | 3/5/5/5 | pass | objective-ineligible | this has classic computery sounds which makes sense to be part of her voice, so it could be an option |
| `f4f3c287df6012ae` | Computer | `mossformer2_source_mix` | fail (WER 0.111) | pass | 5/5/5/5 | pass | objective-ineligible | — |
| `051d2d8f04a015fa` | Doc Dantalion | `source_mix` | pass (WER 0.000) | pass | 2/5/5/2 | fail | human-rejected | — |
| `1b3f56062121451c` | Doc Dantalion | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/4/2 | fail | human-rejected | — |
| `521502618d493dcb` | Doc Dantalion | `mossformer2_source_mix` | fail (WER 0.067) | fail | 5/5/5/5 | pass | objective-ineligible | — |
| `35645b11501097f8` | Homeless Forsaken | `mossformer2_source_mix` | fail (WER 0.500) | fail | 3/5/4/2 | pass | objective-ineligible | some artifacts but easier to identify them... not good enough but the best option |
| `7784a88ecc5b3b94` | Homeless Forsaken | `source_mix` | pass (WER 0.000) | pass | 1/5/5/2 | fail | useful after bounded repair | easily identifiable but so much background noise wtf |
| `91966fb93ebf3c1a` | Homeless Forsaken | `mel_roformer_vocal` | fail (WER 1.000) | fail | 4/5/1/1 | fail | objective-ineligible | cuts off before dying is said... very muffled too |
| `37d1fb08eeee50fe` | Powerless Friendless | `mel_roformer_vocal` | pass (WER 0.000) | pass | 4/5/5/3 | fail | human-rejected | — |
| `8fdbbd032880d9db` | Powerless Friendless | `source_mix` | pass (WER 0.000) | pass | 3/5/5/3 | fail | human-rejected | — |
| `98968650c708e630` | Powerless Friendless | `mossformer2_source_mix` | pass (WER 0.000) | pass | 4/5/5/5 | pass | approved neutral identity anchor **SELECTED** | — |
| `a95cd3ed32e029c8` | Zebulon Pryce | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/5/5 | pass | approved neutral identity anchor **SELECTED** | — |
| `ab19ae63e710b4dc` | Zebulon Pryce | `mel_roformer_vocal` | pass (WER 0.000) | pass | 5/5/5/5 | pass | approved neutral identity anchor | — |
| `d2c0d2c92ece5c9a` | Zebulon Pryce | `source_mix` | pass (WER 0.000) | pass | 1/5/5/1 | fail | human-rejected | — |
| `1507f477408ce915` | Hater of Humans | `mel_roformer_vocal` | pass (WER 0.000) | pass | 4/5/5/3 | pass | approved performance-only reference **SELECTED** | — |
| `6f76fd8f161cd0a6` | Hater of Humans | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/5/4 | pass | useful after bounded repair | cuts off at the end a bit too early |
| `add3c82681e20da1` | Hater of Humans | `source_mix` | pass (WER 0.000) | pass | 2/5/5/2 | fail | human-rejected | — |
| `53c6bce3b61cb174` | Evan Claple | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/5/5 | pass | useful after bounded repair | cuts off voice too early |
| `81296b7fb29e8e59` | Evan Claple | `mel_roformer_vocal` | pass (WER 0.000) | pass | 3/5/5/3 | fail | useful after bounded repair | artifacts here and there |
| `a1efdb66f5b30a8a` | Evan Claple | `source_mix` | pass (WER 0.000) | pass | 1/5/5/1 | fail | useful after bounded repair | music in background, cuts off voice too early |
| `d699f88e6197a94f` | Shythe Shahid | `mossformer2_source_mix` | fail (WER 0.125) | pass | 3/5/5/3 | fail | objective-ineligible | cuts off too early, some artifacts here and there |
| `e6e13ce8ce597c3c` | Shythe Shahid | `mel_roformer_vocal` | fail (WER 0.125) | pass | 2/5/5/2 | fail | objective-ineligible | cuts off too early, some artifacts here and there |
| `ebd175deca3c426a` | Shythe Shahid | `source_mix` | fail (WER 0.188) | pass | 1/5/5/1 | fail | objective-ineligible | cuts off too early, some background sounds and music clearly audible |
| `1fc7faf52324163c` | Securitybot | `mel_roformer_vocal` | pass (WER 0.000) | pass | 4/5/5/3 | fail | useful after bounded repair | bit of an artifact or sound at the very end there |
| `5a64fec21e6a4c0e` | Securitybot | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/5/5 | pass | approved neutral identity anchor **SELECTED** | — |
| `b473f8e03566416b` | Securitybot | `source_mix` | pass (WER 0.000) | pass | 2/5/5/5 | fail | human-rejected | — |
| `81460e3279f2acfe` | Tobias Vaughn / Robot | `mel_roformer_vocal` | pass (WER 0.000) | pass | 5/5/3/4 | pass | useful after bounded repair | cut off a bit early. also is this tobias vaughn's actual voice? or is he imitating a robot here. if so we should find out the voice actor and see if we can get their voice in a roll where they play it with a similar character voice |
| `9628707e2a440334` | Tobias Vaughn / Robot | `mossformer2_source_mix` | pass (WER 0.000) | pass | 5/5/3/4 | pass | useful after bounded repair | cut off a bit early. also is this tobias vaughn's actual voice? or is he imitating a robot here. if so we should find out the voice actor and see if we can get their voice in a roll where they play it with a similar character voice |
| `f1f00fba221c794d` | Tobias Vaughn / Robot | `source_mix` | pass (WER 0.000) | pass | 1/5/3/1 | fail | human-rejected | — |
