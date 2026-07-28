from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import wave
from pathlib import Path
from typing import Any

from community_qwen_pack_store import (
    PACKS_DIRECTORY,
    STORE_LOCK,
    CommunityQwenPackError,
    read_manifest,
    require_pack,
    store_root,
    utc_now,
    write_manifest,
)


def record_qvoice_preview(
    *,
    pack_id: str,
    preview_path: str | Path,
    persistent_description: str,
    direction: str,
    reusable_root: str | Path,
) -> dict[str, Any]:
    source = Path(preview_path).expanduser().resolve()
    try:
        with wave.open(str(source), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getnframes() <= 0:
                raise CommunityQwenPackError(
                    "qwen_pack_preview_invalid",
                    "The preview must be a non-empty mono WAV file.",
                )
    except (OSError, EOFError, wave.Error) as exc:
        raise CommunityQwenPackError(
            "qwen_pack_preview_invalid",
            "The generated preview is not a valid WAV file.",
        ) from exc
    with STORE_LOCK:
        packs = read_manifest(reusable_root)
        item = require_pack(packs, pack_id)
        pack_dir = store_root(reusable_root) / pack_id
        destination = pack_dir / "preview.wav"
        pending = pack_dir / f".preview-{secrets.token_hex(6)}.wav"
        shutil.copy2(source, pending)
        os.replace(pending, destination)
        preview_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "pack_sha256": item["sha256"],
                    "preview_sha256": preview_sha,
                    "persistent_description": persistent_description.strip(),
                    "direction": direction.strip(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        item.update(
            {
                "state": "review_required",
                "production_supported": False,
                "preview": f"{PACKS_DIRECTORY}/{pack_id}/preview.wav",
                "preview_sha256": preview_sha,
                "preview_fingerprint": fingerprint,
                "approval_fingerprint": None,
                "persistent_description": persistent_description.strip(),
                "preview_direction": direction.strip(),
            }
        )
        packs[pack_id] = item
        write_manifest(reusable_root, packs)
        return dict(item)


def approve_qvoice_pack(
    *,
    pack_id: str,
    expected_preview_fingerprint: str,
    reusable_root: str | Path,
) -> dict[str, Any]:
    with STORE_LOCK:
        packs = read_manifest(reusable_root)
        item = require_pack(packs, pack_id)
        fingerprint = str(item.get("preview_fingerprint") or "")
        if not fingerprint:
            raise CommunityQwenPackError(
                "qwen_pack_preview_required",
                "Generate and listen to a preview before approving this Voice.",
            )
        if fingerprint != expected_preview_fingerprint:
            raise CommunityQwenPackError(
                "qwen_pack_preview_changed",
                "The preview changed before approval. Listen to the current preview.",
            )
        resolve_qvoice_preview(item=item, reusable_root=reusable_root)
        item.update(
            {
                "state": "approved",
                "production_supported": True,
                "approval_fingerprint": fingerprint,
                "approved_at_utc": utc_now(),
            }
        )
        packs[pack_id] = item
        write_manifest(reusable_root, packs)
        return dict(item)


def resolve_qvoice_preview(
    *,
    item: dict[str, Any],
    reusable_root: str | Path,
) -> Path:
    root = Path(reusable_root).expanduser().resolve()
    relative = str(item.get("preview") or "").strip()
    preview = (root / relative).resolve() if relative else None
    if preview is None or not preview.is_relative_to(root) or not preview.is_file():
        raise CommunityQwenPackError(
            "qwen_pack_preview_required",
            "This community Voice has no generated preview yet.",
        )
    expected = str(item.get("preview_sha256") or "")
    digest = hashlib.sha256()
    with preview.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if not expected or digest.hexdigest() != expected:
        raise CommunityQwenPackError(
            "qwen_pack_preview_changed",
            "The stored preview changed. Generate and listen to a new preview.",
        )
    return preview
