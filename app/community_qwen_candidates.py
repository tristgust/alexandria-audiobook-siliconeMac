from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from community_qwen_mlx_runtime import DISK_RESERVE_BYTES
from community_qwen_pack_store import (
    STORE_LOCK,
    CommunityQwenPackError,
    read_manifest,
    require_pack,
    write_manifest,
)
from community_qwen_packs import (
    inspect_qwen_pack_path,
    install_community_qwen_pack,
)
from hf_access import HuggingFaceAccessError, shared_huggingface_cache_dir
from model_registry import (
    ModelCacheOperationError,
    download_or_repair_model,
    model_cache_status,
    model_spec,
    resolve_model_path,
)
from qwen_voice_packs import CommunityPackFamily


CURATED_COMMUNITY_QWEN_CANDIDATES = {
    "scrappylabs_narrator": {
        "key": "scrappylabs_narrator",
        "name": "ScrappyLabs Narrator",
        "model_key": "pytorch_scrappylabs_narrator",
        "speaker": "narrator",
        "summary": (
            "Warm English narrator trained for storytelling, suspense, education, "
            "and conversational delivery. Emotional responsiveness is publisher-"
            "claimed and still requires Alexandria listening review."
        ),
        "license_name": "Apache-2.0",
        "evidence_status": "publisher_claimed_unverified",
        "default_description": (
            "Warm, expressive English narrator with clear diction, steady identity, "
            "and a broad storytelling range."
        ),
        "default_preview_text": (
            "I thought I had lost you. Then the door opened, and there you were."
        ),
        "default_direction": (
            "Begin hushed and grief-stricken. After the door opens, shift into "
            "startled relief and restrained warmth without changing speaker identity."
        ),
        "estimated_mlx_bytes": {
            8: 3_080_198_266,
            4: 2_242_832_325,
        },
    },
}


def _candidate(candidate_key: str) -> dict[str, Any]:
    key = str(candidate_key or "").strip()
    candidate = CURATED_COMMUNITY_QWEN_CANDIDATES.get(key)
    if candidate is None:
        raise CommunityQwenPackError(
            "qwen_candidate_not_found",
            "The selected curated Qwen Voice candidate is unavailable.",
        )
    return dict(candidate)


def _installed_candidate(
    *,
    candidate_key: str,
    reusable_root: str | Path,
) -> dict[str, Any] | None:
    for item in read_manifest(reusable_root).values():
        if item.get("catalog_key") == candidate_key:
            return dict(item)
    return None


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(
        item.stat().st_size
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def curated_qwen_candidate_catalog(
    *,
    reusable_root: str | Path,
) -> list[dict[str, Any]]:
    root = Path(reusable_root).expanduser().resolve()
    available_free = shutil.disk_usage(root).free
    with STORE_LOCK:
        installed = read_manifest(reusable_root)
        installed_by_catalog = {
            str(item.get("catalog_key")): dict(item)
            for item in installed.values()
            if item.get("catalog_key")
        }
    result = []
    for key, candidate in CURATED_COMMUNITY_QWEN_CANDIDATES.items():
        spec = model_spec(candidate["model_key"])
        cache = model_cache_status(spec.key)
        source_bytes_needed = 0 if cache["cached"] else spec.estimated_size_bytes
        estimates = {
            str(bits): {
                "estimated_output_bytes": int(output_bytes),
                "required_peak_free_bytes": int(
                    source_bytes_needed + output_bytes + DISK_RESERVE_BYTES
                ),
                "allowed": available_free
                >= source_bytes_needed + output_bytes + DISK_RESERVE_BYTES,
            }
            for bits, output_bytes in candidate["estimated_mlx_bytes"].items()
        }
        installed_item = installed_by_catalog.get(key)
        result.append(
            {
                **candidate,
                "repo_id": spec.repo_id,
                "revision": spec.revision,
                "source_size_bytes": spec.estimated_size_bytes,
                "source_cached": cache["cached"],
                "source_cache_state": cache["state"],
                "available_free_bytes": available_free,
                "reserved_free_bytes": DISK_RESERVE_BYTES,
                "conversion_estimates": estimates,
                "installed": installed_item is not None,
                "installed_pack_id": (
                    installed_item.get("pack_id") if installed_item else None
                ),
                "installed_state": (
                    installed_item.get("state") if installed_item else None
                ),
            }
        )
    return result


def install_curated_qwen_candidate(
    *,
    candidate_key: str,
    reusable_root: str | Path,
    q_bits: int = 8,
    cleanup_downloaded_source: bool = True,
) -> dict[str, Any]:
    if q_bits not in {4, 8}:
        raise CommunityQwenPackError(
            "qwen_candidate_quantization_invalid",
            "Curated Qwen conversion supports only 4-bit or 8-bit output.",
        )
    candidate = _candidate(candidate_key)
    with STORE_LOCK:
        existing = _installed_candidate(
            candidate_key=candidate_key,
            reusable_root=reusable_root,
        )
    if existing is not None:
        return existing

    spec = model_spec(candidate["model_key"])
    cache_before = model_cache_status(spec.key)
    output_bytes = int(candidate["estimated_mlx_bytes"][q_bits])
    source_bytes_needed = 0 if cache_before["cached"] else spec.estimated_size_bytes
    free_bytes = shutil.disk_usage(Path(reusable_root).expanduser().resolve()).free
    required_peak = source_bytes_needed + output_bytes + DISK_RESERVE_BYTES
    if free_bytes < required_peak:
        raise CommunityQwenPackError(
            "qwen_candidate_disk_space_guard",
            "Candidate installation was blocked to preserve disk space. The source "
            "download, converted MLX model, and 16 GiB reserve do not fit together.",
        )

    downloaded_here = not cache_before["cached"]
    try:
        if not cache_before["cached"]:
            download_or_repair_model(
                spec.key,
                repair=cache_before["state"] == "incomplete",
                minimum_headroom_bytes=output_bytes + DISK_RESERVE_BYTES,
            )
        source = resolve_model_path(spec.key, local_files_only=True)
    except (ModelCacheOperationError, HuggingFaceAccessError) as exc:
        raise CommunityQwenPackError(exc.code, str(exc)) from exc

    inspection = inspect_qwen_pack_path(
        source_path=source,
        reusable_root=reusable_root,
        q_bits=q_bits,
    )
    if (
        inspection["family"]
        != CommunityPackFamily.FULL_CUSTOM_VOICE_CHECKPOINT.value
        or candidate["speaker"] not in inspection["speakers"]
    ):
        raise CommunityQwenPackError(
            "qwen_candidate_source_mismatch",
            "The pinned candidate snapshot no longer matches its expected CustomVoice "
            "speaker configuration.",
        )

    installed = install_community_qwen_pack(
        source_path=source,
        reusable_root=reusable_root,
        q_bits=q_bits,
    )
    with STORE_LOCK:
        packs = read_manifest(reusable_root)
        item = require_pack(packs, installed["pack_id"])
        item.update(
            {
                "catalog_key": candidate_key,
                "name": candidate["name"],
                "publisher": "ScrappyLabs",
                "source_repo_id": spec.repo_id,
                "source_revision": spec.revision,
                "evidence_status": candidate["evidence_status"],
                "license_name": candidate["license_name"],
                "language": "English",
                "persistent_description": candidate["default_description"],
                "preview_text_default": candidate["default_preview_text"],
                "preview_direction": candidate["default_direction"],
            }
        )
        packs[item["pack_id"]] = item
        write_manifest(reusable_root, packs)

    cleanup = {
        "attempted": False,
        "removed": False,
        "reclaimed_bytes": 0,
        "warning": None,
    }
    if cleanup_downloaded_source and downloaded_here:
        cache_root = shared_huggingface_cache_dir()
        repository_cache = (cache_root / spec.cache_name).resolve()
        cleanup["attempted"] = True
        try:
            if not repository_cache.is_relative_to(cache_root.resolve()):
                raise OSError("Candidate cache path escaped the shared cache root.")
            reclaimed = _tree_size(repository_cache)
            shutil.rmtree(repository_cache)
            cleanup.update(
                {
                    "removed": True,
                    "reclaimed_bytes": reclaimed,
                }
            )
        except OSError as exc:
            cleanup["warning"] = str(exc)

    return {
        **item,
        "candidate_install": {
            "q_bits": q_bits,
            "source_downloaded_here": downloaded_here,
            "source_cache_cleanup": cleanup,
        },
    }


__all__ = [
    "CURATED_COMMUNITY_QWEN_CANDIDATES",
    "curated_qwen_candidate_catalog",
    "install_curated_qwen_candidate",
]
