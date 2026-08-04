from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPOSITORY_ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from voice_dossier_repair import (  # noqa: E402
    apply_voice_dossier_repair,
    inspect_voice_dossier_repair,
    rollback_voice_dossier_repair,
)


DEFAULT_MANIFEST = (
    REPOSITORY_ROOT / "benchmarks" / "original_sin_unsaved_voice_dossier_repairs_v1.json"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--rollback-operation-id")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    if args.apply:
        result = apply_voice_dossier_repair(
            project_root=args.project_root,
            manifest_path=args.manifest,
            confirm_repair=args.confirm,
        )
    elif args.inspect:
        result = inspect_voice_dossier_repair(
            project_root=args.project_root,
            manifest_path=args.manifest,
        )
    else:
        result = rollback_voice_dossier_repair(
            project_root=args.project_root,
            operation_id=args.rollback_operation_id,
            confirm_rollback=args.confirm,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
