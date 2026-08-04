# Voice Types

Alexandria supports several voice paths. Availability depends on platform and backend capability. A voice type should be chosen for its actual behavior, not because it appears higher in a quality hierarchy.

## CustomVoice

CustomVoice uses a named built-in speaker and per-line instructions.

Configuration fields typically include:

- `type: "custom"`;
- built-in `voice` name;
- optional persistent `character_style`;
- optional seed.

On Apple Silicon this uses the MLX CustomVoice model. It supports instruction-driven delivery and is the simplest production path for many characters.

Measured warm M2 Max performance was approximately 0.29 RTF with about 3.35 GiB peak process RSS for the benchmark prompt.

## Clone

Clone uses a reference audio file and its exact transcript.

Configuration fields include:

- `type: "clone"`;
- `ref_audio`;
- `ref_text`;
- optional `character_style`;
- `clone_backend`, either `qwen3_base` or `voxcpm2_controlled`;
- controlled-clone generation settings when the opt-in backend is used.

The transcript must match the spoken reference. A mismatched transcript weakens identity and can introduce artifacts.

`qwen3_base` is the default and generally ignores free-form per-line voice design instructions. `voxcpm2_controlled` is an opt-in supplied-clip backend that passes the line’s `instruct` and persistent identity note while retaining the exact supplied reference audio/text as the identity anchor. It is processed per line so instructions are not discarded.

Cast Designed Voice auditions use Qwen VoiceDesign to create one clean,
persona-informed neutral identity recording. Fish S2.1 uses that exact same
reference audio and transcript for baseline, happy, sad, and angry, changing
only line delivery. Valid but acoustically subtle lanes remain listenable and
are marked for review. Happy, Sad, and Angry can each be regenerated without
changing the identity or the other three lanes; the full montage is rebuilt
and replayed after the replacement. **Regenerate full audition** rebuilds all
four Fish lanes and reruns the neutral VoiceDesign identity step. Explicit
clone conversion preserves only the neutral identity recording and its exact
transcript.

For stronger delivery control, an expressive reference bank can keep several approved emotion/prosody references for the same supplied speaker and choose the closest reference per line. For the owned-recording path, the uploaded reference audio, exact transcript, and audio fingerprint define the identity; VoiceDesign cannot silently replace it. The Expressive voices inspector exposes every required style as a listening-review row, permits same-speaker owned clips or controlled experimental variants, and requires identity, drift, emotion, pronunciation, and pace review. A fixed bank/single-reference/direct-design comparison adds identity consistency and long-form drift. The reference transcript must still match exactly, a neutral approved reference remains the fallback, and bank approval remains separate from production assignment.

On Apple Silicon the default clone uses the MLX Qwen Base model. A VoiceDesign-generated reference followed by warm Qwen Clone measured approximately 0.33 RTF.

The controlled supplied-clip backend uses `mlx-community/VoxCPM2-4bit`. On the measured M2 Max it produced neutral and expressive outputs at approximately 0.85 and 0.78 RTF, with speaker cosine similarity of 0.976 and 0.960 to the supplied clip. It remains opt-in. Changing the saved backend requires preview generation, completed playback, and a short-lived one-time server receipt bound to the speaker and exact identity/generation configuration; the browser cannot authorize it with local state alone.

## VoiceDesign

VoiceDesign can synthesize directly from a natural-language description for a
standalone preview or an explicitly saved `type: "design"` Voice. In the Cast
and automatic-persona paths, it instead creates a stable neutral identity
reference. Cast auditions use that single neutral recording as the identity
authority for every emotional delivery; they do not generate separate
emotion-conditioned VoiceDesign identities.

Configuration fields include:

- `type: "design"`;
- `description`;
- optional `ref_text` for saved previews.

For direct `type: "design"` generation, the line’s instruction is combined with
the base description. For automatic persona generation, the generated WAV is
saved as `ref_audio` with its exact `ref_text`, the local Qwen clone remains the
fallback, and Fish hybrid routing handles fear, grief, sarcasm, and other
expressive lines from the VoiceDesign identity.

On Apple Silicon warm VoiceDesign measured approximately 0.30 RTF.

Descriptions that explicitly request a supported accent may activate the native-reference [Accent Pipeline](ACCENT_PIPELINE.md), which is slower and uses both Design and Clone models.

## Alias

An alias is not a separate synthesis backend. `alias_of` points a speaker label to another speaker’s production voice configuration.

Aliases resolve transitively. Saves reject missing targets, self-aliases, cycles, and chains that cannot reach an independent configured voice before changing `voice_config.json`. The approved character roster remains the authority for canonical identity; manual production aliases do not rewrite roster evidence.

While an alias is active, synthesis ignores the speaker's own backend fields and uses only the fully resolved target. The selected character’s Cast Voice editor shows the resolved target, chain, type, and source instead of editable backend controls. The speaker's prior independent settings remain dormant so clearing the alias restores them rather than fabricating a new voice.

Use **More → Advanced identity operations** for canonical rename, merge, split, or reassignment that must propagate through the whole project. Use a voice alias only when separate Script labels intentionally share one production Voice.

## Expressive voice project

An expressive voice project is preparation data, not a production voice type. It can collect:

- a reviewed desired persona;
- synthetic VoiceDesign samples; or
- owned/permissive same-speaker recordings;
- reviewed clips and transcripts;
- an approved dataset;
- one explicit reference sample;
- readiness for later feasibility review.

Creating or approving the project does not alter `voice_config.json`, train an adapter, or assign a production voice.

See [Voice Training and Expressive Preparation](VOICE_TRAINING.md).

## LoRA

LoRA is represented in legacy configuration and artifact-management surfaces for compatibility. Training and adapter inference are currently **unsupported inside the shared Apple Silicon MLX runtime**. The UI removes runnable actions when the capability route reports unsupported.

An isolated PyTorch SFT/PEFT sidecar remains under active investigation. It must use a separate dependency environment and pass real output-quality and inference validation before the application can enable it. Existing artifacts are preserved and can be explicitly deleted. Dataset preparation remains available.

See [LoRA on Apple Silicon](LORA_APPLE_SILICON.md).

## Platform summary

| Path | Apple Silicon stable status |
| --- | --- |
| CustomVoice | Supported through MLX |
| Qwen Base Clone | Supported through MLX; default |
| Controlled supplied-clip Clone | Supported backend; opt-in preview required |
| VoiceDesign | Supported through MLX |
| Accent pipeline | Supported, slower/heavier |
| External Gradio server | Supported when configured |
| LoRA training | Unsupported in current MLX runtime; isolated sidecar under investigation |
| LoRA adapter inference | Unsupported in current MLX runtime |

## Choosing a path

- Start with CustomVoice when a built-in identity and instructions are sufficient.
- Use Qwen Base Clone when you have a clean, legally usable reference with an exact transcript and neutral/reference-led delivery is sufficient.
- Use controlled supplied-clip Clone when the uploaded identity must respond to per-line delivery instructions; preview and listen before opting in.
- Use VoiceDesign for fast voice exploration or per-line designed delivery.
- Use **More voice tools** or the contextual Voice Lab Dataset builder to prepare a reviewed reference bank or training material without assuming an adapter backend exists.
- Do not select LoRA on Apple Silicon merely because a dataset can be built; wait for a validated isolated training and inference contract.
