#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from production_prompt_routes import (
    ProductionPromptRouteError,
    install_primary_responsive_voices,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install export-eligible, instruction-controlled primary voices and "
            "the two human-selected production prompt routes."
        )
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--benny-prompt-source", required=True)
    parser.add_argument("--doctor-prompt-source", required=True)
    parser.add_argument(
        "--confirm-production-opt-in",
        action="store_true",
        help=(
            "Confirm that the operator wants the reviewed route prompts to be "
            "eligible for final audiobook exports."
        ),
    )
    parser.add_argument("--approved-at-utc")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = install_primary_responsive_voices(
            project_root=args.project_root,
            benny_prompt_source=args.benny_prompt_source,
            doctor_prompt_source=args.doctor_prompt_source,
            confirm_production_opt_in=args.confirm_production_opt_in,
            approved_at_utc=args.approved_at_utc,
        )
    except (ProductionPromptRouteError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        )
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
