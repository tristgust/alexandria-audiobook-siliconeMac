from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TypeAlias


PHASE24D_RENDER_STATES = (
    "script-external-import-verified",
    "script-external-import-unverified-narrow",
    "script-external-structured-result-narrow",
)
PHASE24D_RENDER_LIMIT_MS = 250.0
RenderValue: TypeAlias = str | int | float | bool | None


def phase24d_render_failures(
    states: Mapping[str, Mapping[str, RenderValue]],
    *,
    limit_ms: float = PHASE24D_RENDER_LIMIT_MS,
) -> list[str]:
    failures: list[str] = []
    for state_name in PHASE24D_RENDER_STATES:
        render_ms = states.get(state_name, {}).get("renderMs")
        if (
            isinstance(render_ms, bool)
            or not isinstance(render_ms, (int, float))
            or not math.isfinite(render_ms)
            or render_ms < 0
        ):
            failures.append(
                f"state:{state_name}: render timing was not recorded"
            )
        elif render_ms > limit_ms:
            failures.append(
                f"state:{state_name}: render took {render_ms:.3f} ms"
            )
    return failures
