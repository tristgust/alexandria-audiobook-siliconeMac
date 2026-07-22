#!/usr/bin/env python3
"""Canonical Round 1 multimodel expressive-clone test contract."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ROUND_ID = "alexandria_multimodel_expressive_clone_round1_v1"
ROOT = Path(__file__).resolve().parent
STYLE_ROOT = ROOT / "multimodel_round1_styles"

GROUP_ORDER = (
    "baseline_positive",
    "sorrow_vulnerability",
    "threat_conflict",
    "subtext_cognition",
    "vocal_modes_events",
)


def load_contract() -> dict[str, Any]:
    groups: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    styles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group_key in GROUP_ORDER:
        path = STYLE_ROOT / f"{group_key}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("key") != group_key:
            raise ValueError(f"Group key mismatch in {path}.")
        group_styles = list(payload.get("styles") or [])
        if not group_styles:
            raise ValueError(f"No styles declared in {path}.")
        for style in group_styles:
            key = str(style.get("key") or "").strip()
            if not key or key in seen:
                raise ValueError(f"Duplicate or empty style key: {key!r}.")
            seen.add(key)
            if style.get("group") != group_key:
                raise ValueError(f"Style {key!r} has the wrong group.")
            if style.get("target_text") == style.get("acted_reference_text"):
                raise ValueError(f"Acted reference text must differ for {key!r}.")
            alpha = float(style.get("index_alpha"))
            if not 0.0 <= alpha <= 1.0:
                raise ValueError(f"Invalid IndexTTS2 alpha for {key!r}.")
        groups[group_key] = {
            "key": group_key,
            "label": payload["label"],
            "description": payload["description"],
            "styles": [item["key"] for item in group_styles],
        }
        styles.extend(group_styles)
    return {
        "schema_version": SCHEMA_VERSION,
        "round_id": ROUND_ID,
        "groups": groups,
        "styles": styles,
        "style_by_key": {item["key"]: item for item in styles},
    }


CONTRACT = load_contract()
STYLE_GROUPS = CONTRACT["groups"]
STYLES = tuple(CONTRACT["styles"])
STYLE_BY_KEY = CONTRACT["style_by_key"]

if __name__ == "__main__":
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "round_id": ROUND_ID,
                "group_count": len(STYLE_GROUPS),
                "style_count": len(STYLES),
                "groups": STYLE_GROUPS,
            },
            indent=2,
        )
    )
