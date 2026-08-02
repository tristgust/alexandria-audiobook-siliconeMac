from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST: Final = ROOT / "tests" / "b19_t07_routes.json"
SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA: Final = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_ARTIFACTS: Final = ("screenshot", "ax_tree", "focus_trace", "live_region", "console_network_log", "identity")


@dataclass(frozen=True, slots=True)
class CaseExpectation:
    case_id: str
    mode: str
    surface: str
    viewport: str
    profile: str
    requested_url: str
    destination: str
    route_path: str
    route_owner: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    errors: tuple[str, ...]
    expected_case_count: int
    observed_case_count: int

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, object]:
    manifest = _read_json(path)
    if manifest is None:
        raise FileNotFoundError(path)
    return manifest


def expected_cases(manifest: dict[str, object]) -> tuple[CaseExpectation, ...]:
    surfaces = manifest["surfaces"]
    modes = manifest["mode_matrix"]
    required_artifacts = manifest.get("required_artifacts")
    if not isinstance(surfaces, list) or not isinstance(modes, dict) or not isinstance(required_artifacts, list):
        raise ValueError("malformed manifest")
    if tuple(required_artifacts) != REQUIRED_ARTIFACTS:
        raise ValueError("malformed artifact manifest")
    cases: list[CaseExpectation] = []
    for mode, configuration in modes.items():
        if not isinstance(mode, str) or not isinstance(configuration, dict):
            raise ValueError("malformed mode")
        viewports = configuration.get("viewports")
        profiles = configuration.get("profiles", ["default"])
        if not isinstance(viewports, list) or not isinstance(profiles, list):
            raise ValueError("malformed mode matrix")
        for surface in surfaces:
            if not isinstance(surface, dict):
                raise ValueError("malformed surface")
            for viewport in viewports:
                for profile in profiles:
                    values = (
                        _text(surface.get("id")), _text(viewport), _text(profile),
                        _text(surface.get("requested_url")), _text(surface.get("destination")),
                        _text(surface.get("route_path")), _text(surface.get("route_owner")),
                    )
                    if any(value is None for value in values):
                        raise ValueError("malformed surface value")
                    surface_id, viewport_id, profile_id, url, destination, route_path, owner = values
                    if not all(isinstance(value, str) for value in values):
                        raise ValueError("malformed surface value")
                    cases.append(CaseExpectation(
                        case_id=f"{mode}:{surface_id}:{viewport_id}:{profile_id}", mode=mode,
                        surface=surface_id, viewport=viewport_id, profile=profile_id,
                        requested_url=url, destination=destination, route_path=route_path, route_owner=owner,
                    ))
    expected_count = manifest.get("expected_case_count")
    if not isinstance(expected_count, int) or len(cases) != expected_count:
        raise ValueError("case matrix count mismatch")
    return tuple(cases)


def _artifact_error(record: dict[str, object], evidence_dir: Path, kinds: set[str]) -> str | None:
    kind = _text(record.get("kind"))
    relative = _text(record.get("path"))
    digest = _text(record.get("sha256"))
    if kind not in kinds or relative is None or digest is None or not SHA256.fullmatch(digest):
        return "malformed_artifact"
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return "malformed_artifact"
    path = evidence_dir / candidate
    if not path.is_file():
        return "missing_artifact"
    return None if _sha256(path) == digest else "sha_mismatch"


def validate_evidence(evidence_dir: Path, manifest_path: Path = DEFAULT_MANIFEST) -> ValidationResult:
    try:
        manifest = load_manifest(manifest_path)
        expectations = expected_cases(manifest)
    except (FileNotFoundError, ValueError):
        return ValidationResult(("malformed_manifest",), 0, 0)
    errors: list[str] = []
    run = _read_json(evidence_dir / "run.json")
    matrix = _read_json(evidence_dir / "matrix.json")
    if run is None or matrix is None:
        return ValidationResult(("missing_artifact",), len(expectations), 0)
    run_id = _text(run.get("run_id"))
    base_sha = _text(run.get("base_sha"))
    final_sha = _text(run.get("final_sha"))
    started_at = _timestamp(run.get("started_at"))
    manifest_sha = _text(run.get("manifest_sha256"))
    if None in (run_id, base_sha, final_sha, started_at, manifest_sha) or not COMMIT_SHA.fullmatch(base_sha or "") or not COMMIT_SHA.fullmatch(final_sha or "") or not SHA256.fullmatch(manifest_sha or ""):
        return ValidationResult(("malformed_artifact",), len(expectations), 0)
    if manifest_sha != _sha256(manifest_path):
        errors.append("stale_artifact")
    records = matrix.get("cases")
    if not isinstance(records, list):
        return ValidationResult(("malformed_artifact",), len(expectations), 0)
    expected = {case.case_id: case for case in expectations}
    seen: set[str] = set()
    artifact_paths: set[str] = {"run.json", "matrix.json"}
    kinds = set(manifest.get("required_artifacts", []))
    for raw_record in records:
        if not isinstance(raw_record, dict):
            errors.append("malformed_artifact")
            continue
        case_id = _text(raw_record.get("case_id"))
        if case_id is None:
            errors.append("malformed_artifact")
            continue
        if case_id in seen:
            errors.append("duplicate_artifact")
            continue
        seen.add(case_id)
        case = expected.get(case_id)
        if case is None:
            errors.append("unexpected_artifact")
            continue
        if raw_record.get("run_id") != run_id or raw_record.get("base_sha") != base_sha or raw_record.get("final_sha") != final_sha:
            errors.append("stale_artifact")
        if (raw_record.get("mode"), raw_record.get("surface"), raw_record.get("viewport"), raw_record.get("profile")) != (case.mode, case.surface, case.viewport, case.profile):
            errors.append("malformed_artifact")
        if _timestamp(raw_record.get("captured_at")) is None or _timestamp(raw_record.get("captured_at")) < started_at:
            errors.append("stale_artifact")
        if raw_record.get("requested_url") != case.requested_url or raw_record.get("final_url") != case.requested_url:
            errors.append("url_mismatch")
        if raw_record.get("body_destination") != case.destination or raw_record.get("body_route_path") != case.route_path or raw_record.get("route_owner") != case.route_owner:
            errors.append("route_mismatch")
        artifacts = raw_record.get("artifacts")
        if not isinstance(artifacts, list):
            errors.append("malformed_artifact")
            continue
        present_kinds: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                errors.append("malformed_artifact")
                continue
            kind = _text(artifact.get("kind"))
            if kind in present_kinds:
                errors.append("duplicate_artifact")
            elif kind is not None:
                present_kinds.add(kind)
            error = _artifact_error(artifact, evidence_dir, kinds)
            if error is not None:
                errors.append(error)
            else:
                path = _text(artifact.get("path"))
                if path is not None:
                    artifact_paths.add(path)
        if present_kinds != kinds:
            errors.append("missing_artifact" if present_kinds < kinds else "unexpected_artifact")
    if set(expected) != seen:
        errors.append("missing_artifact")
    actual_paths = {path.relative_to(evidence_dir).as_posix() for path in evidence_dir.rglob("*") if path.is_file()}
    if actual_paths != artifact_paths:
        errors.append("unexpected_artifact")
    return ValidationResult(tuple(sorted(set(errors))), len(expectations), len(records))
