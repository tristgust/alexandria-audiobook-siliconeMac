# Chris Cwej and Roz Forrester Voice Reference Evaluation

Date: 2026-07-29

Branch basis: `research/fish-s21-prompt-calibration` at `9659cea`

Status: consolidated source-selection and focused Fish review packs generated and served together; full Chris/Roz multimodel generation intentionally waits for the blind source-selection export.

## Casting and source authority

Big Finish's official casting identifies:

- Chris Cwej — Travis Oliver
- Roz Forrester — Yasmin Bannerman

The primary corpus is the user's six owned Big Finish MP3s:

| Source | SHA-256 | Timestamped ASR segments | Duration represented |
|---|---|---:|---:|
| Original Sin | `b4c6863e815f7e1dfffd2f5238709eb1120e47cdefb22404b641e9f2c4171f17` | 3,074 | 8,277.34 s |
| Damaged Goods | `c5ed0bc0c33f965d05e9ddceec212ced7b36201787e79c42966abbf867fa1e84` | 2,580 | 6,901.54 s |
| The Trial of a Time Machine | `fc206f335c8fcacd59b601e3f518d237011db25626eb2f18051d6abf949c2941` | 1,106 | 3,490.90 s |
| Vanguard | `67cb5e1e8f9446287de5d00af7846dc8f5ba5b9ab92bdaf79f6c82c7eea45476` | 1,232 | 3,477.94 s |
| The Jabari Countdown | `4ee5436eb2961c639ba71f5b1211936069a4c13c78d0d7c395dd20af7aa3bc8d` | 1,361 | 3,652.62 s |
| The Dread of Night | `93057cdefa379fd7e392df1de26ac2cd2f8f782653a809b4c878065c29bf54cf` | 1,269 | 3,585.76 s |

All six local files were SHA-256 matched against the six files uploaded in ChatGPT. The total corpus contains 10,622 timestamped Whisper segments.

No usable public rehearsal script was found for these releases. Exact public script anthologies located during research cover unrelated, earlier Big Finish titles. The evaluation therefore uses pinned local Whisper Base MLX transcripts as search and verification aids, followed by manual transcript correction for curated clips and human listening as the authority.

## External reference pool

Downloaded only as supplementary material:

- Yasmin Bannerman narrative studio reel — clean identity anchor.
- Yasmin Bannerman commercial studio reel — phonetic and delivery coverage.
- `Cwej: Down the Middle Coda` — short in-character Travis Oliver source, but mixed-speaker and too short to be the default anchor.
- Travis Oliver's appended `Original Sin` backstage interview — clean out-of-character fallback and speaker-verification anchor.
- T'Nia Miller BFI `What's in a name?` speech — measured gravitas and controlled authority.
- T'Nia Miller Radio Times interview — natural British conversational delivery.

T'Nia Miller audio is never included in Yasmin Bannerman's speaker-identity pool. It is available only as a separately scored style-reference candidate.

## Processing and selection

1. Transcribed all six owned titles with pinned `mlx-community/whisper-base-mlx@1e3e249fb8d01c655324bd6841b1deadffd6d04c` and word timestamps.
2. Prepared clean actor anchors:
   - Travis Oliver: `Original Sin` backstage interview, 7,413.46–7,434.90 s.
   - Yasmin Bannerman: narrative studio reel, 0.00–51.36 s.
3. Ranked plausible single speech turns with `microsoft/wavlm-base-plus-sv` against both actor anchors.
4. Split candidate turns into halves to detect speaker-boundary crossings and identity instability.
5. Generated 36 ranked preview WAVs for Chris and 36 for Roz.
6. Reduced the human review to:
   - 10 Chris identity candidates.
   - 8 Roz identity candidates that passed the stricter floor.
   - 4 curated Chris performance-bank candidates.
   - 4 curated Roz performance-bank candidates.
   - 4 T'Nia Miller style-layer candidates.
7. Preserved exact source paths, source hashes, trim bounds, transcripts, objective measurements, and blind mappings in a private answer key.
8. Ran an independent full-corpus pass with Whisper large-v3/turbo plus pinned SpeechBrain ECAPA speaker verification. The supplemental pass scored 3,155 eligible turns and validated finalist clips with whole-clip and sliding-window speaker consistency.
9. Added only 15 non-duplicative ECAPA finalists to the original 30-row WavLM review: four cleaner identity alternatives, nine missing canonical delivery states, and two additional T'Nia style references. Direct overlaps, the mixed-speaker Roz forceful control, and redundant T'Nia decisive material were excluded.

The rejected `Trial of a Time Machine` Roz laws/pension cut crossed the objective identity boundary and was removed from the final review rather than allowed to contaminate cloning tests. The consolidated human review now contains 45 blind candidates rather than requiring two overlapping review sessions.

## Fish preferred-router retest

Generated 24 Fish Audio S2.1 Pro Free samples using existing private Narrator, Benny, and Doctor voice models:

- Narrator: four styles, full Alexandria bracket instructions, two generations each.
- Benny: four styles, rich natural-language bracket tags, two generations each.
- Doctor: calm authority and dry sarcasm untagged; urgent warning and restrained grief use full Alexandria tags; two generations each.

One Doctor grief generation said `every day` instead of `every name`. The mismatched output and receipt were retained as rejected evidence, the sample was regenerated, and the final review pack is 24/24 exact under pinned Whisper validation.

## Review packages

Local review hub:

`http://127.0.0.1:8877/alexandria-voice-review-hub/`

Direct pages:

- `http://127.0.0.1:8877/alexandria-voice-review-hub/source-review/`
- `http://127.0.0.1:8877/alexandria-voice-review-hub/fish-review/`

The review pages preserve local progress and export JSON score bundles. They are copied into the public-only `/private/tmp/alexandria-voice-review-server-root` and served by Alexandria's byte-range-capable review server, not stock `python -m http.server`. Private answer keys remain outside that server root; direct and path-traversal probes return `404`. The hub, consolidated 45-candidate source review, and 24-sample Fish review passed desktop `1280×900` and mobile `390×844` smoke checks for candidate counts, audio loading, controls, local persistence, export availability, hidden answer-key details, horizontal overflow, and runtime errors. A direct range request returned `206 Partial Content` with `Accept-Ranges: bytes` and the expected 100-byte payload.

## Next gated stage

After the source-selection export is returned:

1. Select one primary and at least one alternate identity reference per actor.
2. Correct the retained reference transcripts by ear.
3. Run the bounded Chris/Roz multimodel blind round:
   - Fish S2.1 Pro Free.
   - VoxCPM2 controllable cloning.
   - IndexTTS2 matched control.
   - Any other currently screened clone-plus-control candidate that passes its model-specific handling contract.
4. Use identical test lines, matched references, multiple generations, exact-text checks, and hidden model identities.
5. For Roz, compare Yasmin-only output against separate T'Nia style references at conservative strengths. Never merge their speaker datasets.
6. Record a per-character, per-delivery production routing profile rather than one universal winner.

## Safety and product state

- No Alexandria Voice assignment changed.
- No production audio changed.
- No user-owned source file changed.
- Generated artifacts remain research-only and require human listening before use.
