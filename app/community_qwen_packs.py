from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from community_qwen_pack_review import (
    approve_qvoice_pack,
    record_qvoice_preview,
    resolve_qvoice_preview,
)
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
from qvoice_format import QwenVoicePackError
from qwen_voice_packs import inspect_community_pack


def _inspection_payload(source_path: str | Path) -> dict[str, Any]:
    try:
        result = inspect_community_pack(source_path)
    except QwenVoicePackError as exc:
        raise CommunityQwenPackError(exc.code, str(exc)) from exc
    qvoice = result.qvoice
    return {
        "family": result.family.value,
        "state": result.state.value,
        "name": result.name,
        "speakers": list(result.speakers),
        "model_size": result.model_size,
        "license_name": result.license_name,
        "production_supported": result.production_supported,
        "message": result.message,
        "sha256": qvoice.sha256 if qvoice else None,
        "language": qvoice.language if qvoice else None,
        "prompt_mode": (
            "xvector" if qvoice and qvoice.xvector_only else "icl" if qvoice else None
        ),
        "sections": list(qvoice.sections) if qvoice else [],
    }


def inspect_qvoice_upload(*, source_path: str | Path) -> dict[str, Any]:
    return _inspection_payload(source_path)


def list_qwen_packs(*, reusable_root: str | Path) -> list[dict[str, Any]]:
    with STORE_LOCK:
        return [dict(item) for _, item in sorted(read_manifest(reusable_root).items())]


def resolve_qvoice_pack(
    *,
    pack_id: str,
    reusable_root: str | Path,
    require_approved: bool = False,
) -> tuple[dict[str, Any], Path]:
    with STORE_LOCK:
        item = require_pack(read_manifest(reusable_root), pack_id)
        source = (Path(reusable_root).expanduser().resolve() / item["relative_path"]).resolve()
        if not source.is_relative_to(Path(reusable_root).expanduser().resolve()):
            raise CommunityQwenPackError(
                "qwen_pack_path_invalid",
                "The installed community Qwen pack has an unsafe path.",
            )
        inspection = _inspection_payload(source)
        if inspection["sha256"] != item.get("sha256"):
            raise CommunityQwenPackError(
                "qwen_pack_integrity_failed",
                "The installed community Qwen pack no longer matches its import hash.",
            )
        if require_approved and (
            item.get("state") != "approved"
            or item.get("production_supported") is not True
            or item.get("approval_fingerprint") != item.get("preview_fingerprint")
        ):
            raise CommunityQwenPackError(
                "qwen_pack_review_required",
                "Listen to and approve this community Voice before assigning it.",
            )
        return item, source


def install_qvoice_pack(
    *,
    source_path: str | Path,
    reusable_root: str | Path,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    inspection = _inspection_payload(source)
    if inspection["family"] != "qvoice_graft":
        raise CommunityQwenPackError(
            "qwen_pack_import_unsupported",
            "Only compatible .qvoice grafts can currently be installed.",
        )
    if inspection["state"] != "ready_for_review":
        raise CommunityQwenPackError(
            "qwen_pack_runtime_unsupported",
            inspection["message"],
        )
    pack_id = f"qvoice_{inspection['sha256'][:24]}"
    with STORE_LOCK:
        packs = read_manifest(reusable_root)
        if pack_id in packs:
            return dict(packs[pack_id])
        store = store_root(reusable_root)
        target = store / pack_id
        if target.exists():
            raise CommunityQwenPackError(
                "qwen_pack_storage_conflict",
                "A different unindexed pack already uses this storage location.",
            )
        store.mkdir(parents=True, exist_ok=True)
        staging = store / f".{pack_id}.import-{secrets.token_hex(6)}"
        staging.mkdir()
        try:
            staged_pack = staging / "voice.qvoice"
            shutil.copy2(source, staged_pack)
            if _inspection_payload(staged_pack)["sha256"] != inspection["sha256"]:
                raise CommunityQwenPackError(
                    "qwen_pack_copy_mismatch",
                    "The imported pack changed while it was being copied.",
                )
            os.replace(staging, target)
            entry = {
                **inspection,
                "pack_id": pack_id,
                "state": "review_required",
                "production_supported": False,
                "relative_path": f"{PACKS_DIRECTORY}/{pack_id}/voice.qvoice",
                "installed_at_utc": utc_now(),
                "preview": None,
                "preview_fingerprint": None,
                "approval_fingerprint": None,
            }
            packs[pack_id] = entry
            try:
                write_manifest(reusable_root, packs)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return dict(entry)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def remove_qvoice_pack(
    *,
    pack_id: str,
    reusable_root: str | Path,
) -> dict[str, str]:
    with STORE_LOCK:
        packs = read_manifest(reusable_root)
        require_pack(packs, pack_id)
        pack_dir = store_root(reusable_root) / pack_id
        tombstone = pack_dir.with_name(f".{pack_id}.remove-{secrets.token_hex(6)}")
        if pack_dir.exists():
            os.replace(pack_dir, tombstone)
        del packs[pack_id]
        try:
            write_manifest(reusable_root, packs)
        except Exception:
            if tombstone.exists():
                os.replace(tombstone, pack_dir)
            raise
        shutil.rmtree(tombstone, ignore_errors=True)
        return {"status": "removed", "pack_id": pack_id}
