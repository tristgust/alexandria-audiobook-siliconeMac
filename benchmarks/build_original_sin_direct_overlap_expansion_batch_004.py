#!/usr/bin/env python3
"""Build expansion batch 004 using the corrected extraction contract."""
from __future__ import annotations

import sys
from pathlib import Path

import benchmarks.build_original_sin_direct_overlap_expansion_batch_002 as base


ROUND_ID = "alexandria_original_sin_direct_overlap_expansion_batch_004"
DEFAULT_PLAN = Path(__file__).with_name("original_sin_direct_overlap_expansion_batch_004_plan.json")
DEFAULT_OUTPUT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665/external_workflows/big_finish_overlap_reference_v1/"
    "direct_overlap_expansion_batch_004"
)


def main() -> int:
    base.ROUND_ID = ROUND_ID
    base.DEFAULT_PLAN = DEFAULT_PLAN
    base.REVIEW_HTML = (
        base.REVIEW_HTML
        .replace("Original Sin direct overlap batch 2", "Original Sin direct overlap batch 4")
        .replace("batch 2</h1>", "batch 4</h1>")
    )
    argv = list(sys.argv)
    if "--plan" not in argv:
        argv.extend(["--plan", str(DEFAULT_PLAN)])
    if "--output-root" not in argv:
        argv.extend(["--output-root", str(DEFAULT_OUTPUT)])
    sys.argv = argv
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
