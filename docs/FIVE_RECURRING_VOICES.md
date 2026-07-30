# Five Recurring Voices

Status: implemented and human-approved in an isolated feature branch. The live Alexandria root remains untouched pending the separate reviewed-pack installation operation.

## Recurring characters

Alexandria treats these Script Voice labels as recurring across projects:

- `NARRATOR`
- `BERNICE` (Benny)
- `THE DOCTOR`
- `CHRIS`
- `ROZ`

Their aliases are copied with the authoritative assignments, including `BENNY`, `DOCTOR`, `CHRIS CWEJ`, and `ROZ FORRESTER` variants.

When Alexandria creates a new project, it copies the **current production-valid Voices-page assignment** for each recurring character. Replacing one of these Voices through the normal Voices workflow changes what later projects inherit without disabling the other recurring Voices.

Supported portable assignments are:

- built-in Voices;
- standard supplied-recording clones;
- approved Qwen instruction-controlled clones;
- reviewed Alexandria responsive-router clones;
- designed Voices;
- approved community Qwen Voices.

An unsupported, missing, tampered, or unapproved assignment blocks starter-pack materialization instead of silently substituting another Voice.

## Final Chris and Roz routing

The reviewed assignments use clean actor recordings for identity and same-character audio only for delivery control. Chris dry humour uses the signed 70% MossFormer2-enhanced / 30% original performance reference selected in the focused repair round; Alexandria copies that exact reviewed WAV by SHA-256 and does not regenerate the nondeterministic enhancement.

| Character | Delivery | Backend |
|---|---|---|
| Chris | Neutral | IndexTTS2 |
| Chris | Dry humour | IndexTTS2 |
| Chris | Urgent/protective authority | IndexTTS2 |
| Chris | Vulnerability | Fish S2.1 Pro Free |
| Roz | Neutral | Fish S2.1 Pro Free |
| Roz | Dry humour | VoxCPM2 |
| Roz | Urgent/tactical authority | IndexTTS2 |
| Roz | Vulnerability/concern | Fish S2.1 Pro Free |

T’Nia Miller is absent from every active identity, style, prompt, routing, and asset contract.

## Runtime boundaries

### IndexTTS2

- exact reviewed model revision;
- isolated persistent sidecar;
- Apple-Silicon MPS, FP32;
- greedy one-beam generation;
- eight diffusion steps;
- same-character performance audio;
- deterministic Alexandria generation seed.

### VoxCPM2

The reviewed Roz dry-humour route uses `warmup_patches=0`. The earlier value of one caused VoxCPM2 to generate and then discard the first acoustic patch, which could remove short opening words such as “Yeah.” Alexandria keeps click safety through its normal output fade instead of deleting speech.

### Fish

Fish does not expose a deterministic request seed. Alexandria therefore preserves Fish through a bounded verified recovery sequence:

1. reviewed primary request;
2. lower-variance Fish retry;
3. concise-tag Fish retry.

Each result is normalized and checked twice: first as the specialist WAV, then again after passing through Alexandria's exact canonical production encoder. A Fish take is accepted only when the production-formatted artifact preserves the first authored word and acceptable transcript error. Qwen fallback is used only after all three Fish attempts fail or the required Fish/verifier capability is unavailable.

### Qwen fallback

Fallback always uses the clean actor identity anchor. Delivery remains in the line instruction; a character performance clip is never silently promoted to identity conditioning.

## Provenance

Every routed chunk records:

- requested route and specialist backend;
- backend actually used;
- whether fallback occurred;
- specialist attempt count;
- repair strategy;
- automatic transcript, first-word result, and word-error rate;
- routing evidence and configuration fingerprints.

Receipt state is thread-local so parallel generation cannot attach one line’s backend evidence to another line.

## Mutation and approval rules

Ordinary Voice saves may:

- retain an unchanged reviewed responsive Voice;
- replace it with another Voice assignment.

They may not create or alter responsive backend routing. New or changed specialist routing must arrive through a reviewed Voice Library assignment or the explicit installer. Voice Library assignment copies all required assets into the destination project, verifies their hashes, revalidates the routing policy there, and checks the reviewed fingerprint before writing `voice_config.json`.

## Acceptance evidence

The production-context audition contains seven sequential lines covering:

- Narrator, Benny, and Doctor through their existing Qwen routes;
- Chris dry humour through IndexTTS2;
- Roz dry humour through VoxCPM2;
- Chris vulnerability through Fish;
- Roz tactical authority through IndexTTS2.

Automatic acceptance results:

- all seven opening words present;
- six exact normalized transcripts;
- one spelling-only `center`/`centre` difference;
- no fallback;
- no clipped output;
- desktop and mobile review smoke passed;
- autosave and cumulative review export passed.

The first human review completed on 2026-07-30. Five lines were accepted and two routes were sent to the focused repair round `alexandria_five_recurring_voice_repair_v1`:

- Chris dry humour had failed for bad audio quality and echo.
- Roz urgent authority had failed functionally because the delivery did not sound urgent or authoritative, despite the reviewer selecting Pass.

The repair round closed both blockers:

- Chris dry humour selected `index_mossformer2_blend70` with quality 4/5, delivery 5/5, and identity 5/5.
- Roz urgent authority selected the pinned current IndexTTS2 route with quality 5/5, delivery 5/5, and identity 4/5.

Two repeated generations of each selected IndexTTS2 route were byte-identical in one persistent pinned sidecar. Passing the exact reviewed WAVs through Alexandria's production installer produced the exact MP3 hashes present in the final isolated seven-line pack, so no unreviewed synthesis was substituted during production formatting.

The branch-level listening gate is closed. The final evidence pack `alexandria_five_recurring_voice_final_approved_v1` performs no synthesis: it preserves the five exact production MP3s accepted in the first review and converts the two exact focused-repair winner WAVs through Alexandria's deterministic installer. Independent transcription checks pass all seven opening words, with no clipping and only the expected `center`/`centre` spelling difference.

The live production root is still unchanged; installation remains an explicit operator action using the reviewed reference bank and exact reviewed Chris repair asset. The repaired Chris source is a signed human-reviewed input and is never regenerated because the evaluated enhancement converter leaves dropout active and is not deterministically reproducible.
