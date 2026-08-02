from __future__ import annotations

import json
import sys
from pathlib import Path

from b19_t07_acceptance_contract import DEFAULT_MANIFEST, validate_evidence


def main(arguments: list[str]) -> int:
    if len(arguments) not in (1, 2):
        print("usage: verify_b19_t07_evidence.py EVIDENCE_DIR [MANIFEST_PATH]", file=sys.stderr)
        return 2
    evidence_dir = Path(arguments[0])
    manifest_path = Path(arguments[1]) if len(arguments) == 2 else DEFAULT_MANIFEST
    result = validate_evidence(evidence_dir, manifest_path)
    print(json.dumps({
        "errors": list(result.errors),
        "expected_case_count": result.expected_case_count,
        "observed_case_count": result.observed_case_count,
        "ok": result.ok,
    }, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
