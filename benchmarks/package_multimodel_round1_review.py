#!/usr/bin/env python3
"""Package the cumulative, locally-openable Round 1 blind review application."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Final

from multimodel_round1_handoff import resolve_round1_handoff_paths
from multimodel_round1_paths import (
    ContainedPath,
    contained_path,
    safe_file_stat,
    safe_read_text,
)
from multimodel_round1_review_output import (
    REVIEW_ASSET_FILES,
    write_review_package,
)
from multimodel_round1_review_package import build_review_package
from multimodel_round1_runtime import require_disk_headroom


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/b17-t05-multimodel-round1"
PACKAGING_METADATA_RESERVE_BYTES: Final = 64 * 1024**2


def _read_internal(evidence: Path) -> dict[str, Any]:
    target = contained_path(evidence, "round1_internal_manifest.json")
    return json.loads(safe_read_text(target))


def _existing_size(target: ContainedPath) -> int:
    try:
        return safe_file_stat(target).st_size
    except FileNotFoundError:
        return 0


def projected_package_bytes(evidence: Path, internal: dict[str, Any]) -> int:
    sources: set[ContainedPath] = set()
    for sample in internal["sample_specs"]:
        sources.add(contained_path(evidence, str(sample["output_file"])))
        reference = sample["reference"]
        for field in ("source_file", "conditioning_file"):
            value = reference.get(field)
            if value:
                sources.add(contained_path(evidence, f"references/{value}"))
    sources.update(
        contained_path(ROOT, f"benchmarks/multimodel_review_assets/{filename}")
        for filename in REVIEW_ASSET_FILES
    )
    return PACKAGING_METADATA_RESERVE_BYTES + sum(
        _existing_size(source) for source in sources
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--output-root")
    parser.add_argument("--answer-key-root")
    args = parser.parse_args()
    handoff = resolve_round1_handoff_paths(
        Path(args.evidence_root),
        public_root=Path(args.output_root) if args.output_root else None,
        answer_key_root=(
            Path(args.answer_key_root) if args.answer_key_root else None
        ),
    )
    internal = _read_internal(handoff.evidence_root)
    require_disk_headroom(
        handoff.evidence_root,
        projected_bytes=projected_package_bytes(handoff.evidence_root, internal),
        receipt_path=handoff.evidence_root / "recovery/disk-headroom.jsonl",
        stage="package:before-copy",
    )
    build = build_review_package(handoff, internal)
    summary = write_review_package(handoff, build)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
