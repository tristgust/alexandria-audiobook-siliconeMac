# Fish S2.1 Cloud Voice Provider

Alexandria can use Fish Audio S2.1 as an optional cloud synthesis backend for supplied-recording clone Voices. The local Qwen clone remains available and is not replaced globally.

## Setup

Open **Settings → Speech & voice providers**. The dedicated **Fish Audio S2.1 Pro** panel reports one of three states:

- **Not connected** — no Fish credential is available;
- **Connected · off** — the credential is present, but Fish is disabled;
- **Ready** — Fish is connected and enabled for assignment in Cast.

The intended setup path is:

1. Select **Connect Fish Audio**.
2. Enter the Fish API key in the Fish-specific credential field.
3. Save the Fish setup.
4. Switch eligible clone Voices in bulk, or select **Use Fish automatic generation** from an individual Cast profile.

The generic API-key field under **Language model** does not configure Fish.

On macOS, a replacement Fish key is stored in Keychain. The key is never returned by the Settings API, written to `config.json`, embedded in project files, included in generation receipts, or committed to Git. An environment key remains process-scoped.

The `s2.1-pro-free` model header represents Fish's temporary free offer. Alexandria does not assume that offer, its capacity, or its commercial terms are permanent. The paid `s2-pro` header remains selectable separately.

## Reversible Voice assignment

Fish is assigned per clone Voice through:

```json
{
  "type": "clone",
  "clone_backend": "fish_s21_cloud"
}
```

Changing the backend preserves:

- the supplied reference recording;
- the exact reference transcript;
- the stable identity description;
- the Script speaker assignment;
- the authored Script text;
- every authored `instruct` line.

The change correctly marks existing generated audio stale because its synthesis binding has changed. **Return Fish Voices to local clone** restores `qwen3_base`; individual Cast profiles expose the same reversible choice.

## Script and Fish bracket instructions

The Script is provider-neutral. It remains similar to:

```json
{
  "text": "There was no goodbye, only the empty chair.",
  "instruct": "Deep personal grief, restrained, trying not to break."
}
```

At request time only, the Fish adapter turns the instruction into a provider-specific bracket cue:

```text
[Deep personal grief, restrained, trying not to break.] There was no goodbye, only the empty chair.
```

The generated bracket cue is not saved back into `annotated_script.json`, so selecting Fish does not rewrite the book or weaken source-fidelity auditing.

## Prompt routing

The current route is derived from the completed four-identity blind evaluation:

| Delivery | Primary Fish request |
| --- | --- |
| Neutral | concise neutral bracket cue |
| Grief | full Alexandria instruction inside brackets |
| Sarcasm | concise rich sarcasm cue |
| Fear | full Alexandria instruction, followed by progressively stronger breathing and paralinguistic cues when needed |
| Other expressive delivery | full Alexandria instruction |

Alexandria repeats the preferred route before trying a fallback. It does not compare every prompt indiscriminately or allow a weaker route to win merely because it scored slightly higher on a generic acoustic measure.

## Automatic generation and validation

For each prompt stage Alexandria:

1. generates multiple stochastic takes;
2. transcribes each take and compares it with the exact authored text;
3. rejects text drift above the configured word-error limit;
4. verifies speaker identity against the supplied recording;
5. rejects invalid, clipped, nearly silent, or otherwise unusable audio;
6. checks for minimum delivery evidence appropriate to the routed style;
7. ranks only the passing repeats from that prompt stage;
8. installs the selected take atomically.

The repeat selector is local and explainable. It uses identity similarity, audio integrity, and style-specific acoustic relationships validated against the completed Ryan, Narrator, Benny, and Doctor blind results. It does not require CLAP, an external emotion model, or a permanent manual-review rule.

The blind evidence showed the final local repeat selector retaining 15 of 16 routed identity/style selections, with mean human delivery of 4.25/5. The remaining failure was Narrator fear, where neither original primary take was convincing. That result informed the staged fear fallback rather than a permanent Narrator or fear review gate.

## Failure behavior

Alexandria never installs a take merely because Fish returned audio.

- If every take changes the authored wording, generation fails.
- If every take drifts below the speaker-identity floor, generation fails.
- If every take fails audio-integrity validation, generation fails.
- If valid speech exists but no prompt stage establishes the requested delivery, generation fails with `fish_delivery_not_achieved` and leaves the prior audio non-current but auditable.

Regenerating the line produces a new stochastic candidate set. No delivery category or named Voice is permanently forced into manual review.

## Generation metadata

A selected Fish take records non-secret evidence on the chunk, including:

- provider and actual model header;
- routed style and selected prompt variant;
- attempted candidate count;
- text-validation result and word-error rate;
- speaker-identity score and scoring mode;
- delivery, quality, and repeat-selection scores;
- reference fingerprint and whether its private remote model was reused;
- automatic-selection status.

The API key, remote authorization header, and raw credential source are excluded.

## Privacy and remote references

Fish reference models are created with private visibility. Alexandria reuses a matching private remote model by reference fingerprint when available instead of uploading the same reference repeatedly.

Only recordings that the user has permission to upload and clone should be assigned to Fish. Disabling Fish or returning a Voice to Qwen does not automatically delete its private Fish reference model; remote cleanup should remain an explicit operation so reproducibility is not destroyed silently.
