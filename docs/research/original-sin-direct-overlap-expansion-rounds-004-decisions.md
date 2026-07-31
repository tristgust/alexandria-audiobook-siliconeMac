# Original Sin direct-overlap decisions through batch 004

The completed boundary-repair v4 and batch-004 blind reviews were unblinded under the strict direct-substitution contract: every intended word must be complete; only the correct speaker may be present; music, unrelated effects, spill, echo, and separator artifacts are disqualifying; and a blocking written note overrides a nominal pass selection.

## Boundary repair v4

All six repaired lines are exact-line substitution eligible:

| Chunk | Character | Selected candidate | Treatment |
|---:|---|---|---|
| 2720 | Beltempest | `25fef848b0f8a393` | MossFormer2, 20 ms post-roll |
| 4432 | Beltempest | `197e011ae956837c` | MossFormer2, 140 ms bounded tail recovery |
| 658 | Chris Cwej | `b4a489cca7aa6220` | MossFormer2 start trim |
| 1575 | Doctor | `69e01bb79f97bcf6` | MossFormer2 start trim |
| 3989 | Doctor | `281e8463c858ec7c` | MossFormer2 start trim |
| 3036 | Tobias Vaughn | `57e88c264ca8a9a4` | MossFormer2 start trim |

## Expansion batch 004

Seven lines are exact-line substitution eligible:

| Chunk | Character | Selected candidate | Treatment |
|---:|---|---|---|
| 2716 | Beltempest | `ead49487083adbff` | MossFormer2 |
| 2737 | Beltempest | `974adae0dbf55f64` | MossFormer2 |
| 66 | Bernice | `fab9ce181d9efde1` | MossFormer2 |
| 1995 | Bernice | `3567290b0ec0a1bc` | MossFormer2 |
| 1259 | Bernice | `494d672d79c91733` | Mel-RoFormer |
| 4866 | Chris Cwej | `d21743b63b1b9436` | MossFormer2 |
| 636 | Roz Forrester | `649bcd1cba789c12` | MossFormer2 |

Repair classifications:

- first-word start trim: chunks `2047`, `2555`, and `506`;
- final-word micro-tail recovery: chunks `2979` and `4758`;
- contamination/source blocked: chunks `5018` and `4687`;
- source blocked because clipping and a tea sound coexist: chunk `4780`;
- wrong-speaker textual match: chunk `1676`, spoken by the Doctor rather than Chris Cwej.

The expansion has 41 blind-approved exact substitutions beyond the original six-line pilot, or 47 approved exact adaptation substitutions total. No project audio or chunk binding was changed.
