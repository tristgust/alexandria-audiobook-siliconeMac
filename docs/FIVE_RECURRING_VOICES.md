# Five Recurring Voices

Status: implemented in an isolated feature worktree; production-root installation remains gated by the final listening review.

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

## Initial Chris and Roz routing

The reviewed initial assignments use clean actor recordings for identity and same-character audio only for delivery control.

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

Each result is normalized and automatically checked for the first authored word and acceptable transcript error. Qwen fallback is used only after all three Fish attempts fail or the required Fish/verifier capability is unavailable.

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

Human review completed on 2026-07-30. Five lines were accepted. Two routes remain blocking:

- Chris dry humour failed for bad audio quality and echo.
- Roz urgent authority failed functionally because the delivery did not sound urgent or authoritative, despite the reviewer selecting Pass.

The focused repair round `alexandria_five_recurring_voice_repair_v1` holds the accepted production lines and identity anchors fixed while testing repaired Chris performance conditioning and stronger Roz authority controls. Production-root promotion remains blocked until that round identifies acceptable replacements for both routes.
