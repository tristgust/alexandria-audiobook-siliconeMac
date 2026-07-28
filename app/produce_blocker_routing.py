from __future__ import annotations

from typing import TypedDict


class ProduceBlockerRoute(TypedDict):
    native_destination: str
    target_id: str


def missing_voice_blocker_route(
    *,
    character_id: str | None,
    speaker: str | None,
) -> ProduceBlockerRoute:
    if character_id:
        return {
            "native_destination": "cast",
            "target_id": f"cast:character:{character_id}",
        }
    return {
        "native_destination": "more/advanced-character-operations",
        "target_id": speaker or "UNKNOWN",
    }
