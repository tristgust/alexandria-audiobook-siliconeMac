"""HuggingFace download utilities for built-in LoRA adapters."""
import json
import logging
import os
import shutil
import time

from hf_access import hf_hub_download_with_public_fallback

logger = logging.getLogger("AlexandriaUI")

BUILTIN_LORA_HF_REPO = "Finrandojin/Alexandria"
BUILTIN_LORA_HF_REVISION = "d5a523e68181faf92daf696d62da02bd6bb2e7fe"

REQUIRED_ADAPTER_FILES = [
    "adapter_config.json",
    "adapter_model.safetensors",
    "ref_sample.wav",
    "training_meta.json",
]
OPTIONAL_ADAPTER_FILES = ["preview_sample.wav"]

# In-memory manifest cache
_manifest_cache = None
_manifest_cache_time = 0
_MANIFEST_TTL = 3600  # 1 hour


def fetch_builtin_manifest(
    builtin_dir,
    hf_repo=BUILTIN_LORA_HF_REPO,
    *,
    revision=BUILTIN_LORA_HF_REVISION,
    refresh=False,
):
    """Read the pinned local manifest; refresh remotely only when requested."""
    global _manifest_cache, _manifest_cache_time

    now = time.time()
    if (
        not refresh
        and _manifest_cache is not None
        and (now - _manifest_cache_time) < _MANIFEST_TTL
    ):
        return _manifest_cache

    local_path = os.path.join(
        builtin_dir,
        "manifest.json",
    )
    local_entries = None
    if os.path.exists(local_path):
        try:
            with open(
                local_path,
                "r",
                encoding="utf-8",
            ) as f:
                local_entries = json.load(f)
            if not refresh:
                _manifest_cache = local_entries
                _manifest_cache_time = now
                return local_entries
        except (
            OSError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            logger.warning(
                "Ignoring invalid cached LoRA manifest: %s",
                exc,
            )

    # Refresh a missing or stale local manifest.
    try:
        cached_path = hf_hub_download_with_public_fallback(
            repo_id=hf_repo,
            filename="manifest.json",
            revision=revision,
            force_download=bool(refresh),
        )
        with open(cached_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        # Save local copy for offline fallback
        os.makedirs(builtin_dir, exist_ok=True)
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to fetch remote LoRA manifest, using local fallback: {e}")
        entries = local_entries if local_entries is not None else []

    _manifest_cache = entries
    _manifest_cache_time = now
    return entries


def download_builtin_adapter(adapter_id, builtin_dir, hf_repo=BUILTIN_LORA_HF_REPO):
    """Download a built-in LoRA adapter from HF to builtin_dir/adapter_id/.

    Args:
        adapter_id: Local adapter ID (e.g. "builtin_watson")
        builtin_dir: Path to the builtin_lora directory
        hf_repo: HF repo ID

    Returns:
        Path to the adapter directory on disk.

    Raises:
        RuntimeError: If a required file fails to download.
    """
    # Strip builtin_ prefix to get HF subfolder name
    hf_name = adapter_id.replace("builtin_", "", 1)
    adapter_dir = os.path.join(builtin_dir, adapter_id)
    os.makedirs(adapter_dir, exist_ok=True)

    for filename in REQUIRED_ADAPTER_FILES + OPTIONAL_ADAPTER_FILES:
        local_path = os.path.join(adapter_dir, filename)
        if os.path.exists(local_path):
            continue
        try:
            cached = hf_hub_download_with_public_fallback(
                repo_id=hf_repo,
                filename=f"{hf_name}/{filename}",
                revision=BUILTIN_LORA_HF_REVISION,
            )
            shutil.copy2(cached, local_path)
            logger.info(f"Downloaded {hf_name}/{filename} -> {local_path}")
        except Exception as e:
            if filename in REQUIRED_ADAPTER_FILES:
                raise RuntimeError(
                    f"Failed to download {hf_name}/{filename} for {adapter_id}: {e}"
                )
            logger.warning(f"Optional file {hf_name}/{filename} not available: {e}")

    return adapter_dir


def is_adapter_downloaded(adapter_id, builtin_dir):
    """Check if all required files exist for a built-in adapter."""
    adapter_dir = os.path.join(builtin_dir, adapter_id)
    return (
        os.path.isdir(adapter_dir)
        and all(
            os.path.exists(os.path.join(adapter_dir, f))
            for f in REQUIRED_ADAPTER_FILES
        )
    )
