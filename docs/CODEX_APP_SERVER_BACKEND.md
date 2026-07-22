# Codex app-server integration research — closed

## Status

**Closed on 2026-07-20. Not an active Alexandria feature, roadmap item, release gate, or supported backend.**

The user wanted Alexandria to use ordinary ChatGPT chats directly, without Task Bundle export/import and without depending on a separate usage pool. The investigated `codex app-server` route does not provide that product behavior: it uses the user's separate Codex allowance and optional Codex credits.

Ordinary personal ChatGPT chats are not exposed as a supported synchronous backend API that Alexandria can call from its own Generate controls.

A ChatGPT App/MCP connector was also considered and closed for the current product direction. That approach would move the primary workflow into ChatGPT, require connector/publication or developer-mode setup, and make the user invoke Alexandria from chats rather than work normally inside Alexandria.

## Final product decision

Alexandria supports these structured-generation paths:

1. **Native Ollama** for direct in-application local generation.
2. **Task Bundle v2** for ordinary ChatGPT chats.

Alexandria will not currently add:

- a Codex provider option;
- Codex sign-in, account, rate-limit, reset, or credit UI;
- a supervised app-server runtime;
- a local isolated Codex worker;
- a hosted per-user Codex worker or WebSocket gateway;
- Codex model/parity benchmarks;
- Persona, roster, or Script acceptance gates for Codex;
- launcher startup or process management for Codex;
- a ChatGPT App/MCP connector;
- any requirement to wait for Codex usage to reset or purchase credits.

Reopening either Codex app-server or a ChatGPT App/MCP connector requires an explicit future user decision.

## Research findings retained

The spike established the following technical facts on this machine:

- binary: `/Users/tristan/.local/bin/codex`;
- version: `codex-cli 0.144.1`;
- `codex app-server --stdio` initialization: passed;
- ChatGPT-managed Codex authentication and plan-state reporting: passed;
- rate-limit reporting: passed;
- current Codex usage was exhausted during the probe;
- ephemeral thread creation with `path: null`: passed;
- no-approval/read-only settings were accepted;
- native `outputSchema` support exists in the protocol;
- a real model turn was not run;
- no output-quality or ordinary-ChatGPT parity claim was established;
- no Alexandria core LLM configuration, API route, interface, launcher, project file, audio, or production assignment was integrated.

The protocol proof lives temporarily in:

- `app/codex_app_server.py`;
- `tests/test_codex_app_server.py`.

Those files are **research-only**. They are not imported by Alexandria and are not release requirements. During final clean-commit/release cleanup, exclude or discard them unless the user explicitly reopens the direction. This document may remain as the historical decision record.

## Why the original claim was rejected

The claim under review was effectively:

> Host Codex app-server, add Sign in with ChatGPT, and use a user's existing flat-rate ChatGPT subscription as a free general backend.

The relevant distinctions are:

- Codex app-server is a Codex agent runtime, not an ordinary ChatGPT-chat API.
- ChatGPT-managed authentication does not merge ordinary chat usage with Codex usage.
- Codex capacity is bounded and can be exhausted independently.
- A hosted implementation would still require isolation, authentication, storage, operations, and support.
- Ordinary ChatGPT chats remain accessible to Alexandria through the explicit Task Bundle workflow, not as an invisible synchronous backend.

## Verification record

Before closure, the isolated spike passed:

- 11 focused protocol tests;
- 32 focused Codex plus adjacent LLM tests;
- Python compilation;
- a real privacy-reduced initialize/account/rate-limit/ephemeral-thread probe.

A complete shared-tree run executed 1,286 tests and reported eight failures plus seven errors in unrelated concurrent SciPy/audio, roster-fixture, Characters, recovery, and external-workflow contracts. No failure came from the Codex research lane.

Those results document the spike; they are no longer an active acceptance matrix.

## Supported ordinary-ChatGPT workflow

Task Bundle v2 remains the ordinary-ChatGPT path:

1. Alexandria exports one self-contained task ZIP.
2. The user attaches it to a normal ChatGPT conversation.
3. ChatGPT returns a completed ZIP or versioned JSON envelope.
4. Alexandria validates the source, dependencies, schema, and native contract.
5. The result opens in the correct Alexandria review destination.
6. Nothing is approved, assigned, or applied automatically.

The remaining product work should improve that workflow rather than adding a Codex-dependent provider.