# Local Fish S2 Pro routing

Alexandria can use the pinned MLX Fish S2 Pro snapshot for reviewed responsive
Voice routes. This path is restricted to `noncommercial_research` use under the
accepted Fish Audio Research License decision. It is not a general commercial
production dependency.

The routed fallback order is explicit:

1. local `fish_s2_pro_local` on Apple Silicon when the exact pinned snapshot is
   cached and memory admission succeeds;
2. hosted `fish_s2_pro_cloud` using `s2.1-pro-free` and the same private inline
   zero-shot identity reference;
3. the Voice policy's Qwen fallback.

Receipts report the backend that actually generated the audio. A local failure
therefore cannot be mislabeled as a local success when hosted Fish takes over.
Both Fish paths retain authored-text verification before an output is admitted.

The first integrated route is Roz Forrester's reviewed dry-banter mode. The
local candidate scored 5 identity, 4 delivery, and 5 naturalness in the B18
blind review. No other Voice route is migrated by this decision.
