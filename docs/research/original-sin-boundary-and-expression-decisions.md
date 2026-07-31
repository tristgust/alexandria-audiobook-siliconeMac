# Original Sin boundary and unseen-expression decisions

No Alexandria project Voice, reference bank, or chunk audio was changed by this analysis.

## Reference anchors

| Character | Outcome | Winner | Treatment |
|---|---|---|---|
| Beltempest | approved neutral identity anchor | `52c386b56c630e95` | `mossformer2_source_mix` |
| The Doctor | requires replacement source or bounded repair | — | — |
| Computer | approved neutral identity anchor | `048a5ca161610aad` | `mossformer2_source_mix` |
| Shythe Shahid | requires replacement source or bounded repair | — | — |

## Direct substitutions

| Chunk | Character | Outcome |
|---:|---|---|
| 5207 | Powerless Friendless | requires alternate exact source line |
| 3908 | Hater of Humans | requires alternate exact source line |
| 3098 | Zebulon Pryce | requires alternate exact source line |

## Unseen-line expression

| Character | Mode | Outcome | Winner | Backend |
|---|---|---|---|---|
| Bernice Summerfield | urgent concern | requires expressive-route repair | — | — |
| Bernice Summerfield | dry irony | requires expressive-route repair | — | — |
| Chris Cwej | urgent authority | requires expressive-route repair | — | — |
| Chris Cwej | protective concern | approved expressive generation route | `30e3a71f0971b671` | `voxcpm2_controllable_clone` |
| Roz Forrester | command authority | requires expressive-route repair | — | — |
| Under-Sergeant | cold authority | approved expressive generation route | `29f951673070dd39` | `qwen3_instruction_controlled` |
| Tobias Vaughn / Robot | controlled anger | approved expressive generation route | `9ae61993697e70eb` | `voxcpm2_controllable_clone` |
| Tobias Vaughn / Robot | existential fear | approved expressive generation route | `d4b6c554606669d7` | `fish_s2.1_pro_free_inline_zero_shot` |

## Load-bearing findings

- Beltempest and Computer now have clean neutral anchors.
- Doctor and Shythe remain source/boundary blocked.
- All three direct-boundary groups failed because a foreign onset remained; alternate exact lines are required.
- Expressive winners exist for Chris protective concern, Under-Sergeant cold authority, Vaughn controlled anger, and Vaughn existential fear.
- Bernice urgent concern, Bernice dry irony, Chris urgent authority, and Roz command authority require a focused expressive repair round.
