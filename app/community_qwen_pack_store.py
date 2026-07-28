from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from utils import atomic_json_write


PACKS_DIRECTORY = "community_qwen_packs"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1
STORE_LOCK = threading.RLock()


class CommunityQwenPackError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def store_root(reusable_root: str | Path) -> Path:
    root = Path(reusable_root).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise CommunityQwenPackError(
            "qwen_pack_root_invalid",
            "The reusable Voice collection is unavailable or unsafe.",
        )
    return root / PACKS_DIRECTORY


def manifest_path(reusable_root: str | Path) -> Path:
    return store_root(reusable_root) / MANIFEST_NAME


def read_manifest(reusable_root: str | Path) -> dict[str, dict[str, Any]]:
    path = manifest_path(reusable_root)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommunityQwenPackError(
            "qwen_pack_manifest_invalid",
            "The community Qwen pack manifest is unreadable.",
        ) from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise CommunityQwenPackError(
            "qwen_pack_manifest_invalid",
            "The community Qwen pack manifest has an unsupported schema.",
        )
    packs = value.get("packs")
    if not isinstance(packs, Mapping):
        raise CommunityQwenPackError(
            "qwen_pack_manifest_invalid",
            "The community Qwen pack manifest has no pack index.",
        )
    return {
        str(key): dict(item)
        for key, item in packs.items()
        if isinstance(key, str) and isinstance(item, Mapping)
    }


def write_manifest(
    reusable_root: str | Path,
    packs: Mapping[str, Mapping[str, Any]],
) -> None:
    path = manifest_path(reusable_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "updated_at_utc": utc_now(),
            "packs": dict(sorted(packs.items())),
        },
        str(path),
    )


def require_pack(
    packs: Mapping[str, dict[str, Any]],
    pack_id: str,
) -> dict[str, Any]:
    item = packs.get(pack_id)
    if item is None:
        raise CommunityQwenPackError(
            "qwen_pack_not_found",
            "The selected community Qwen pack no longer exists.",
        )
    return dict(item)
