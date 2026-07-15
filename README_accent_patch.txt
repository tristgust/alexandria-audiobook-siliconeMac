Alexandria Accent-Aware VoiceDesign Patch
Target commit: ee8ec61e34a217ae4c94c442cc01e9b58f7fc527
Requires: the Alexandria MLX patch already installed

WHAT IT DOES
When a Voice Designer description asks for a recognized non-English accent, Alexandria now:

1. Designs the character speaking the accent's native language.
2. Saves that hidden native-language reference.
3. Uses the MLX Qwen Base clone model to make the requested English preview.
4. Returns the English accented preview to the normal Alexandria Voice Designer UI.
5. When you save the voice, Alexandria stores that accented English preview as the reusable clone reference, exactly as it already does for designed voices.

French is the tested path. Spanish, German, Italian, Portuguese, and Russian mappings are included but should be treated as experimental.

INSTALL
1. Unzip this package into the Alexandria repository root:
   /Users/tristan/pinokio/api/alexandria-audiobook.git

2. In Terminal, from that folder, run:

   ./app/env/bin/python ./apply_accent_aware_voicedesign_patch.py

3. Restart Alexandria.

USE
Your existing description should trigger it:

An adult man with a resonant, weathered baritone and a light southern French accent. His speech is measured, honourable, and quietly weary, with the disciplined restraint of a former knight who rarely raises his voice unless action demands it.

For guaranteed detection, add this anywhere in the description:

[accent: French]

To force the ordinary one-stage VoiceDesign path, add:

[accent: none]

EXPECTED TERMINAL MESSAGE
MLX VoiceDesign accent pipeline: French native reference -> English preview clone

The first accented preview in a session loads both the VoiceDesign and Base clone models, so it takes longer. Later attempts reuse both loaded models.

FILES
Hidden native-language references are kept in:
  designed_voices/accent_seeds/

The normal English previews remain in:
  designed_voices/previews/

UNDO
From the Alexandria repository root:

  cp accent_pipeline_backup/mlx_backend.before_accent_pipeline.py app/mlx_backend.py
  rm -f accent_pipeline_patch_applied.json

Then restart Alexandria.
