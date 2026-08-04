#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT / "benchmarks" / "b25_release_candidate_qualification_20260804.json"
)
DEFAULT_PROJECT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Alexandria"
    / "Projects"
    / "original-sin--e6286665"
)


class ReleaseCandidateVerificationError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateVerificationError(
            f"Could not read release-candidate manifest: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ReleaseCandidateVerificationError(
            "Release-candidate manifest must contain an object."
        )
    return value


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseCandidateVerificationError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise ReleaseCandidateVerificationError(
            f"Could not compare Git ancestry: {result.stderr.strip()}"
        )
    return result.returncode == 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _port_stopped(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def verify(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    project_root: str | Path = DEFAULT_PROJECT,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser().resolve()
    project = Path(project_root).expanduser().resolve()
    manifest = _read_json(manifest_file)
    if manifest.get("schema_version") != 1:
        raise ReleaseCandidateVerificationError(
            "Release-candidate manifest schema is unsupported."
        )
    if manifest.get("status") != "qualified_release_candidate":
        raise ReleaseCandidateVerificationError(
            "Manifest does not declare a qualified release candidate."
        )

    candidate = str(manifest.get("candidate_commit") or "")
    current_head = _git("rev-parse", "HEAD")
    origin_dev = _git("rev-parse", "origin/dev")
    tag = manifest.get("historical_production_tag")
    if not isinstance(tag, dict):
        raise ReleaseCandidateVerificationError(
            "Historical production-tag record is missing."
        )
    tag_name = str(tag.get("name") or "")
    expected_tag_target = str(tag.get("target_commit") or "")
    actual_tag_target = _git("rev-parse", f"{tag_name}^{{}}")

    protected = manifest.get("protected_project_state")
    if not isinstance(protected, dict):
        raise ReleaseCandidateVerificationError(
            "Protected project-state record is missing."
        )
    file_expectations = {
        "voice_config.json": str(protected.get("voice_config_sha256") or ""),
        "chunks.json": str(protected.get("chunks_sha256") or ""),
        "voice_route_listening_decisions.json": str(
            protected.get("voice_route_listening_decisions_sha256") or ""
        ),
    }
    file_results = {}
    for relative, expected in file_expectations.items():
        target = project / relative
        actual = _sha256(target)
        file_results[relative] = {
            "expected_sha256": expected,
            "actual_sha256": actual,
            "matches": actual == expected,
        }

    checks = {
        "candidate_exists": _git("cat-file", "-t", candidate) == "commit",
        "candidate_is_current_ancestor": _is_ancestor(candidate, current_head),
        "origin_dev_matches_head": origin_dev == current_head,
        "local_main_is_candidate_ancestor": _is_ancestor("main", candidate),
        "origin_main_is_candidate_ancestor": _is_ancestor("origin/main", candidate),
        "historical_tag_unchanged": actual_tag_target == expected_tag_target,
        "release_tag_not_created_by_manifest": manifest.get("release_tag_created")
        is False,
        "production_cutover_not_performed": manifest.get(
            "production_cutover_performed"
        )
        is False,
        "protected_project_hashes_match": all(
            item["matches"] for item in file_results.values()
        ),
        "ports_stopped": all(_port_stopped(port) for port in (4200, 4201)),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    result = {
        "schema_version": 1,
        "status": "PASS" if not failed else "FAIL",
        "manifest": str(manifest_file),
        "candidate_commit": candidate,
        "current_head": current_head,
        "origin_dev": origin_dev,
        "historical_tag_target": actual_tag_target,
        "checks": checks,
        "protected_files": file_results,
        "failed_checks": failed,
    }
    if failed:
        raise ReleaseCandidateVerificationError(
            "Release-candidate verification failed: " + ", ".join(failed)
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT))
    args = parser.parse_args()
    result = verify(
        manifest_path=args.manifest,
        project_root=args.project_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
