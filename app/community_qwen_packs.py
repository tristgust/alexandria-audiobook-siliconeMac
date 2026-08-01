from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from community_qwen_mlx_runtime import (
    CommunityQwenRuntimeError,
    conversion_plan,
    convert_full_checkpoint_low_disk,
    inventory_fingerprint,
    resolve_descriptor_runtime,
    sha256_file,
    source_inventory,
    write_descriptor,
)
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
from qwen_voice_packs import (
    CommunityPackFamily,
    CommunityPackState,
    inspect_community_pack,
)


def _raise_runtime(exc: CommunityQwenRuntimeError) -> None:
    raise CommunityQwenPackError("qwen_pack_runtime_invalid", str(exc)) from exc


def _inspection_payload(
    source_path: str | Path,
    *,
    reusable_root: str | Path | None = None,
    q_bits: int = 8,
) -> dict[str, Any]:
    try:
        result = inspect_community_pack(source_path)
    except QwenVoicePackError as exc:
        raise CommunityQwenPackError(exc.code, str(exc)) from exc
    qvoice = result.qvoice
    payload = {
        "family": result.family.value,
        "state": result.state.value,
        "name": result.name,
        "speakers": list(result.speakers),
        "model_size": result.model_size,
        "license_name": result.license_name,
        "production_supported": result.production_supported,
        "message": result.message,
        "runtime": result.runtime,
        "conversion_supported": result.conversion_supported,
        "sha256": qvoice.sha256 if qvoice else None,
        "language": qvoice.language if qvoice else None,
        "prompt_mode": (
            "xvector" if qvoice and qvoice.xvector_only else "icl" if qvoice else None
        ),
        "sections": list(qvoice.sections) if qvoice else [],
    }
    if (
        result.family is CommunityPackFamily.FULL_CUSTOM_VOICE_CHECKPOINT
        and reusable_root is not None
    ):
        try:
            payload["conversion_plan"] = conversion_plan(
                result.path,
                store_root(reusable_root),
                q_bits=q_bits,
            )
        except (OSError, CommunityQwenRuntimeError) as exc:
            _raise_runtime(
                exc
                if isinstance(exc, CommunityQwenRuntimeError)
                else CommunityQwenRuntimeError(str(exc))
            )
    return payload


def inspect_qvoice_upload(*, source_path: str | Path) -> dict[str, Any]:
    return _inspection_payload(source_path)


def inspect_qwen_pack_path(
    *,
    source_path: str | Path,
    reusable_root: str | Path,
    q_bits: int = 8,
) -> dict[str, Any]:
    return _inspection_payload(
        source_path,
        reusable_root=reusable_root,
        q_bits=q_bits,
    )


def list_qwen_packs(*, reusable_root: str | Path) -> list[dict[str, Any]]:
    with STORE_LOCK:
        return [dict(item) for _, item in sorted(read_manifest(reusable_root).items())]


def _resolved_installed_path(
    *,
    item: dict[str, Any],
    reusable_root: str | Path,
) -> Path:
    root = Path(reusable_root).expanduser().resolve()
    relative = Path(str(item.get("relative_path") or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise CommunityQwenPackError(
            "qwen_pack_path_invalid",
            "The installed community Qwen pack has an unsafe path.",
        )
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise CommunityQwenPackError(
            "qwen_pack_missing",
            "The installed community Qwen pack is missing.",
        )
    expected = str(item.get("sha256") or "")
    if not expected or sha256_file(source) != expected:
        raise CommunityQwenPackError(
            "qwen_pack_integrity_failed",
            "The installed community Qwen pack no longer matches its import hash.",
        )
    if item.get("family") != CommunityPackFamily.QVOICE_GRAFT.value:
        try:
            resolve_descriptor_runtime(source)
        except CommunityQwenRuntimeError as exc:
            _raise_runtime(exc)
    return source


def resolve_community_qwen_pack(
    *,
    pack_id: str,
    reusable_root: str | Path,
    require_approved: bool = False,
) -> tuple[dict[str, Any], Path]:
    with STORE_LOCK:
        item = require_pack(read_manifest(reusable_root), pack_id)
        source = _resolved_installed_path(item=item, reusable_root=reusable_root)
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


def resolve_qvoice_pack(
    *,
    pack_id: str,
    reusable_root: str | Path,
    require_approved: bool = False,
) -> tuple[dict[str, Any], Path]:
    return resolve_community_qwen_pack(
        pack_id=pack_id,
        reusable_root=reusable_root,
        require_approved=require_approved,
    )


def _base_entry(
    *,
    inspection: dict[str, Any],
    pack_id: str,
    relative_path: str,
    sha256: str,
) -> dict[str, Any]:
    return {
        **inspection,
        "pack_id": pack_id,
        "sha256": sha256,
        "state": "review_required",
        "production_supported": False,
        "relative_path": relative_path,
        "installed_at_utc": utc_now(),
        "preview": None,
        "preview_fingerprint": None,
        "approval_fingerprint": None,
    }


def _install_qvoice(
    *,
    source: Path,
    inspection: dict[str, Any],
    reusable_root: str | Path,
) -> dict[str, Any]:
    if inspection["state"] != CommunityPackState.READY_FOR_REVIEW.value:
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
            entry = _base_entry(
                inspection=inspection,
                pack_id=pack_id,
                relative_path=f"{PACKS_DIRECTORY}/{pack_id}/voice.qvoice",
                sha256=inspection["sha256"],
            )
            packs[pack_id] = entry
            try:
                write_manifest(reusable_root, packs)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return dict(entry)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _directory_inventory(source: Path, family: str) -> list[dict[str, Any]]:
    if family == CommunityPackFamily.PEFT_SPEAKER_BUNDLE.value:
        paths = [
            source / name
            for name in (
                "adapter_config.json",
                "adapter_model.safetensors",
                "speaker_embedding.safetensors",
                "tts_config.json",
                "voice_pack.json",
            )
            if (source / name).is_file()
        ]
        return source_inventory(source, paths=paths, include_hashes=True)
    return source_inventory(source, include_hashes=True)


def _install_directory_pack(
    *,
    source: Path,
    inspection: dict[str, Any],
    reusable_root: str | Path,
    q_bits: int,
) -> dict[str, Any]:
    family = str(inspection["family"])
    preflight = None
    if family == CommunityPackFamily.FULL_CUSTOM_VOICE_CHECKPOINT.value:
        preflight = conversion_plan(source, store_root(reusable_root), q_bits=q_bits)
        if not preflight["allowed"]:
            raise CommunityQwenPackError(
                "qwen_pack_disk_space_guard",
                "Conversion was blocked to preserve disk space. Free space must "
                "cover the estimated MLX output plus a 16 GiB safety reserve.",
            )
    inventory = _directory_inventory(source, family)
    source_fingerprint = inventory_fingerprint(inventory)
    prefix = (
        "qpeft"
        if family == CommunityPackFamily.PEFT_SPEAKER_BUNDLE.value
        else "qcustom"
    )
    pack_id = f"{prefix}_{source_fingerprint[:24]}"
    with STORE_LOCK:
        packs = read_manifest(reusable_root)
        if pack_id in packs:
            return dict(packs[pack_id])
        store = store_root(reusable_root)
        store.mkdir(parents=True, exist_ok=True)
        target = store / pack_id
        if target.exists():
            raise CommunityQwenPackError(
                "qwen_pack_storage_conflict",
                "A different unindexed pack already uses this storage location.",
            )
        staging = store / f".{pack_id}.import-{secrets.token_hex(6)}"
        staging.mkdir()
        try:
            descriptor_payload: dict[str, Any]
            conversion = None
            if family == CommunityPackFamily.PEFT_SPEAKER_BUNDLE.value:
                descriptor_payload = {
                    "family": family,
                    "runtime": "mlx_peft_overlay",
                    "source_path": str(source),
                    "source_inventory": inventory,
                    "source_fingerprint": source_fingerprint,
                    "speaker": inspection["speakers"][0],
                }
            elif family == CommunityPackFamily.FULL_CUSTOM_VOICE_CHECKPOINT.value:
                try:
                    conversion = convert_full_checkpoint_low_disk(
                        source_dir=source,
                        output_dir=staging / "mlx_model",
                        q_bits=q_bits,
                    )
                    conversion = {
                        **conversion,
                        "output_dir": f"{PACKS_DIRECTORY}/{pack_id}/mlx_model",
                    }
                except CommunityQwenRuntimeError as exc:
                    _raise_runtime(exc)
                descriptor_payload = {
                    "family": family,
                    "runtime": "mlx_checkpoint",
                    "source_fingerprint": source_fingerprint,
                    "model_path": "mlx_model",
                    "speaker": inspection["speakers"][0],
                    "conversion": conversion,
                }
            else:
                raise CommunityQwenPackError(
                    "qwen_pack_import_unsupported",
                    "This community Qwen directory format is unsupported.",
                )
            descriptor = write_descriptor(
                staging / "pack.json",
                descriptor_payload,
            )
            descriptor_sha = sha256_file(descriptor)
            os.replace(staging, target)
            entry = _base_entry(
                inspection=inspection,
                pack_id=pack_id,
                relative_path=f"{PACKS_DIRECTORY}/{pack_id}/pack.json",
                sha256=descriptor_sha,
            )
            entry["source_fingerprint"] = source_fingerprint
            entry["storage_mode"] = (
                "linked_source" if family == "peft_speaker_bundle" else "converted_mlx"
            )
            if conversion is not None:
                entry["conversion"] = conversion
            packs[pack_id] = entry
            try:
                write_manifest(reusable_root, packs)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return dict(entry)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def install_community_qwen_pack(
    *,
    source_path: str | Path,
    reusable_root: str | Path,
    q_bits: int = 8,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    inspection = _inspection_payload(
        source,
        reusable_root=reusable_root,
        q_bits=q_bits,
    )
    if inspection["family"] == CommunityPackFamily.QVOICE_GRAFT.value:
        return _install_qvoice(
            source=source,
            inspection=inspection,
            reusable_root=reusable_root,
        )
    if not source.is_dir():
        raise CommunityQwenPackError(
            "qwen_pack_directory_required",
            "This community Qwen format must be imported from its directory.",
        )
    if inspection["state"] not in {
        CommunityPackState.READY_FOR_REVIEW.value,
        CommunityPackState.MLX_CONVERSION_AVAILABLE.value,
    }:
        raise CommunityQwenPackError(
            "qwen_pack_runtime_unsupported",
            inspection["message"],
        )
    return _install_directory_pack(
        source=source,
        inspection=inspection,
        reusable_root=reusable_root,
        q_bits=q_bits,
    )


def install_qvoice_pack(
    *,
    source_path: str | Path,
    reusable_root: str | Path,
) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    inspection = _inspection_payload(source)
    if inspection["family"] != CommunityPackFamily.QVOICE_GRAFT.value:
        raise CommunityQwenPackError(
            "qwen_pack_import_unsupported",
            "Use the directory import workflow for this Qwen format.",
        )
    return _install_qvoice(
        source=source,
        inspection=inspection,
        reusable_root=reusable_root,
    )


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


__all__ = [
    "approve_qvoice_pack",
    "inspect_qvoice_upload",
    "inspect_qwen_pack_path",
    "install_community_qwen_pack",
    "install_qvoice_pack",
    "list_qwen_packs",
    "record_qvoice_preview",
    "remove_qvoice_pack",
    "resolve_community_qwen_pack",
    "resolve_qvoice_pack",
    "resolve_qvoice_preview",
]
