# Original Sin final repair decisions

These decisions are evidence-only. No Alexandria Voice assignment, reference-bank approval, chunk audio, or production routing changed during analysis.

## Character references

| Character | Outcome | Candidate | Treatment |
|---|---|---|---|
| The Doctor | Requires a cleaner adaptation performance reference; recurring approved identity remains unchanged | — | — |
| Under-Sergeant | Approved neutral identity anchor | `7d0621147f4f59ce` | `mel_roformer_vocal` |
| Computer | Requires boundary repair; “classified” remained audibly incomplete | — | — |
| Evan Claple | Approved neutral identity anchor | `9d2022213c24f700` | `mossformer2_source_mix` |
| Shythe Shahid | Requires alternate source or stronger music removal | — | — |
| Tobias Vaughn / Robot | Approved neutral robotic identity anchor | `656021bc660487ba` | `mossformer2_source_mix` |

The earlier Beltempest clip remains performance-only and is not a stable identity anchor. A replacement line—“I stand corrected. What would you prefer?”—is in the next blind repair round.

## Direct adaptation substitution

| Chunk | Character | Outcome | Candidate | Treatment |
|---:|---|---|---|---|
| `618` | Securitybot | Exact-line substitution eligible | `105ec030fa6bc76c` | `source_mix` |
| `5207` | Powerless Friendless | Boundary repair required | — | — |
| `3908` | Hater of Humans | Boundary repair required | — | — |
| `3098` | Zebulon Pryce | Boundary repair required | — | — |

Previously approved exact-line candidates remain Roz Forrester chunk `1684` and Rashid chunk `405`. None has been installed.

## Next blind rounds

- Reference boundary and replacement-anchor round: 11 candidates across Beltempest, Doctor, Computer, and Shythe Shahid.
- Direct boundary repair round: 6 production-format candidates across Powerless Friendless, Hater of Humans, and Zebulon Pryce.
- Unseen-line expressive generation round: 29 candidates across 8 groups, comparing Qwen, VoxCPM2, Fish S2.1 Pro Free inline zero-shot, and current Alexandria controls where applicable.

All expressive test lines are book dialogue absent from the adaptation. Production promotion remains prohibited until blind review and a separate rollback-backed promotion receipt.
