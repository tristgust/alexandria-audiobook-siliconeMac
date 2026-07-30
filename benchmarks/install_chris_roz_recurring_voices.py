#!/usr/bin/env python3
"""Install the reviewed Chris/Roz recurring Voice pack into an Alexandria root."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from chris_roz_recurring_voices import install_chris_roz_recurring_voices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--reference-bank", required=True)
    parser.add_argument("--reviewed-chris-dry-reference", required=True)
    parser.add_argument("--confirm-production-opt-in", action="store_true")
    parser.add_argument("--approved-at-utc")
    args = parser.parse_args()
    result = install_chris_roz_recurring_voices(
        project_root=args.project_root,
        reference_bank_path=args.reference_bank,
        reviewed_chris_dry_reference_path=(
            args.reviewed_chris_dry_reference
        ),
        confirm_production_opt_in=args.confirm_production_opt_in,
        approved_at_utc=args.approved_at_utc,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
