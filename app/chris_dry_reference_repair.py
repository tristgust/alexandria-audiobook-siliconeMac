from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any


REPAIR_ID = "chris_dry_mossformer2_blend70_v1"
SOURCE_SHA256 = "105ad3ab6308374096b15c32b4ed37076a70f4b1f02f7683da829d6fe161695e"
OUTPUT_SHA256 = "5a6224e4d4c97010b8695de2d7a9d8b62d6557f370ac731aaa7c67e4b87cd135"
MODEL_REPO = "starkdmi/MossFormer2_SE_48K_MLX"
MODEL_REVISION = "ccd0ded00e26f38e9f5b0ba21608aa6a0bcd6434"
WEIGHTS_FILENAME = "model_fp16.safetensors"
WEIGHTS_SHA256 = "61e63484df9c2be7e1111ca0346d431422a98b263331021a67c2d7ddb2f67a85"
ENHANCED_WEIGHT = 0.70


class ChrisDryReferenceRepairError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_reviewed_chris_dry_reference(
    path: str | Path,
) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ChrisDryReferenceRepairError(
            f"Reviewed Chris dry repair is missing: {source}"
        )
    actual = sha256_file(source)
    if actual != OUTPUT_SHA256:
        raise ChrisDryReferenceRepairError(
            "Reviewed Chris dry repair changed; "
            f"expected {OUTPUT_SHA256}, got {actual}."
        )
    return {
        "schema_version": 1,
        "repair_id": REPAIR_ID,
        "reviewed_asset_path": str(source),
        "source_sha256": SOURCE_SHA256,
        "output_sha256": actual,
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "weights_filename": WEIGHTS_FILENAME,
        "weights_sha256": WEIGHTS_SHA256,
        "enhanced_weight": ENHANCED_WEIGHT,
        "regeneration_allowed": False,
    }


def install_reviewed_chris_dry_reference(
    *,
    source: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    receipt = validate_reviewed_chris_dry_reference(source)
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=destination_path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with source_path.open("rb") as input_handle, temporary.open("wb") as output_handle:
            for block in iter(lambda: input_handle.read(1024 * 1024), b""):
                output_handle.write(block)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary, destination_path)
    finally:
        temporary.unlink(missing_ok=True)
    installed = sha256_file(destination_path)
    if installed != OUTPUT_SHA256:
        destination_path.unlink(missing_ok=True)
        raise ChrisDryReferenceRepairError(
            "Installed Chris dry repair failed its reviewed fingerprint check."
        )
    return {
        **receipt,
        "installed_path": str(destination_path),
        "installed_sha256": installed,
    }
