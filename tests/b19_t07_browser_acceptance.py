from __future__ import annotations

import argparse
import json
import math
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

from audio_artifacts import sha256_file
from b19_t07_acceptance_contract import CaseExpectation, expected_cases, load_manifest
from b19_t07_hostile_fixture import HostileFixtureManifest, build_hostile_fixture
from b19_t07_protected_state import compare_protected_state, snapshot_protected_state
from b19_t06_browser_acceptance import write_fixture_data
from phase17e_api_harness import _copy_fixture
from project import ProjectManager


ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST: Final = ROOT / "tests" / "b19_t07_routes.json"
DEFAULT_PYTHON: Final = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python")
REQUIRED_PROOF_KINDS: Final = frozenset(("screenshot", "ax_tree", "focus_trace", "live_region", "console_network_log", "identity"))


@dataclass(frozen=True, slots=True)
class BoundedPlan:
    expected_case_count: int
    probe_case: CaseExpectation


@dataclass(frozen=True, slots=True)
class DisposableFixture:
    hostile: HostileFixtureManifest
    protected_state_unchanged: bool


@dataclass(frozen=True, slots=True)
class ProofResult:
    status: str
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    artifacts: Path
    repo_root: Path
    python: Path
    manifest: Path
    include_b19_t06: bool
    fresh_only: bool


@dataclass(frozen=True, slots=True)
class RegressionCommand:
    name: str
    arguments: tuple[str, ...]
    timeout: int


def run_plan(manifest_file: Path = MANIFEST) -> BoundedPlan:
    manifest = load_manifest(manifest_file)
    cases = expected_cases(manifest)
    return BoundedPlan(expected_case_count=len(cases), probe_case=cases[0])


def prepare_disposable_fixture(root: Path) -> DisposableFixture:
    before = snapshot_protected_state(root)
    hostile = build_hostile_fixture(root)
    after = snapshot_protected_state(root)
    return DisposableFixture(hostile=hostile, protected_state_unchanged=compare_protected_state(before, after).ok)


def missing_full_proof(observed: set[str]) -> ProofResult:
    missing = tuple(sorted(REQUIRED_PROOF_KINDS - observed))
    return ProofResult("PASS" if not missing else "RED", missing)


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait(url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.05)
    raise RuntimeError(f"isolated server did not become ready: {url}")


def _json_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def diagnostics_directory(artifacts: Path) -> Path:
    if artifacts.name == "browser":
        return artifacts.parent / "task-09-integration" / "runtime"
    return artifacts.parent / f"{artifacts.name}-runtime"


def b19_t06_regression_commands(
    config: RunnerConfig,
    base_url: str,
    fixture_root: Path,
    diagnostics: Path,
) -> tuple[RegressionCommand, ...]:
    regression_root = diagnostics / "b19-t06"
    widths = "1536x1024,1024x768,1440x1000,390x844"
    return (
        RegressionCommand(
            name="browser-acceptance",
            arguments=(
                str(config.python),
                str(config.repo_root / "tests" / "b19_t06_browser_acceptance.py"),
                "--repo-root", str(config.repo_root),
                "--routes-file", str(config.repo_root / "tests" / "b19_t06_routes.json"),
                "--artifacts", str(regression_root / "browser"),
                "--python", str(config.python),
                "--widths", widths,
                "--url", base_url,
                "--fixture-root", str(fixture_root),
            ),
            timeout=900,
        ),
        RegressionCommand(
            name="viewport-integrity",
            arguments=(
                "node", str(config.repo_root / "tests" / "b19_t06_viewport_integrity.js"),
                "--url", base_url,
                "--artifacts", str(regression_root / "viewport"),
                "--viewports", widths,
            ),
            timeout=600,
        ),
        RegressionCommand(
            name="produce-nested-keyboard",
            arguments=(
                "node", str(config.repo_root / "tests" / "b19_t06_produce_nested_keyboard.js"),
                "--url", base_url,
                "--artifacts", str(regression_root / "nested"),
            ),
            timeout=300,
        ),
    )


def run_b19_t06_regressions(
    config: RunnerConfig,
    base_url: str,
    fixture_root: Path,
    diagnostics: Path,
) -> bool:
    regression_root = diagnostics / "b19-t06"
    regression_root.mkdir(parents=True, exist_ok=True)
    receipts: list[dict[str, object]] = []
    for command in b19_t06_regression_commands(config, base_url, fixture_root, diagnostics):
        result = subprocess.run(
            list(command.arguments),
            cwd=config.repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=command.timeout,
        )
        stdout_path = regression_root / f"{command.name}.stdout.txt"
        stderr_path = regression_root / f"{command.name}.stderr.txt"
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        receipts.append({
            "name": command.name,
            "arguments": list(command.arguments),
            "exit_code": result.returncode,
            "stdout_sha256": sha256_file(stdout_path),
            "stderr_sha256": sha256_file(stderr_path),
        })

    def read_report(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    browser_manifest = read_report(regression_root / "browser" / "manifest.json")
    viewport_report = read_report(regression_root / "viewport" / "report.json")
    nested_report = read_report(regression_root / "nested" / "report.json")
    nested_cleanup = read_report(regression_root / "nested" / "cleanup.json")
    checks = {
        "all_processes_exit_zero": all(item["exit_code"] == 0 for item in receipts),
        "browser_acceptance_pass": browser_manifest.get("status") == "PASS",
        "browser_acceptance_external_server": browser_manifest.get("cleanup", {}).get("externalServer") is True,
        "viewport_integrity_pass": viewport_report.get("status") == "PASS",
        "nested_keyboard_pass": nested_report.get("status") == "PASS",
        "nested_provider_calls_prevented": nested_cleanup.get("providerCallsPrevented") is True,
        "same_disposable_server": True,
        "same_disposable_fixture_root": True,
    }
    receipt = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "base_url": base_url,
        "fixture_root": str(fixture_root.resolve()),
        "commands": receipts,
        "checks": checks,
    }
    (regression_root / "index.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt["status"] == "PASS"


def isolated_server_environment(fixture_root: Path) -> dict[str, str]:
    sensitive_markers = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in sensitive_markers)
    }
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "ALEXANDRIA_CONFIG_PATH": str((fixture_root / "app" / "config.json").resolve()),
        "ALEXANDRIA_DATA_ROOT": str((fixture_root / "application-data").resolve()),
        "ALEXANDRIA_LEGACY_ROOT_DIR": str(fixture_root.resolve()),
    })
    return environment


def write_accessibility_fixture_data(root: Path) -> None:
    write_fixture_data(root)
    (root / "state.json").write_text(
        json.dumps(
            {
                "project_id": "fixture-project",
                "project_name": "B19-T07 disposable accessibility fixture",
                "source_language": "English",
                "output_language": "English",
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    audio_path = root / "voicelines" / "fixture-current.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 24000
    with wave.open(str(audio_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(
            struct.pack(
                "<h",
                int(32767 * 0.1 * math.sin(2 * math.pi * 220 * index / sample_rate)),
            )
            for index in range(sample_rate)
        ))
    chunks_path = root / "chunks.json"
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    voice_config = json.loads((root / "voice_config.json").read_text(encoding="utf-8"))
    chunk = chunks[0]
    ready_chunk = dict(chunk)
    ready_chunk.update({
        "id": 1,
        "text": "A second disposable chunk remains ready for keyboard generation proof.",
        "instruct": "Neutral test delivery.",
        "status": "pending",
        "audio_path": None,
    })
    chunk.update({
        "status": "done",
        "audio_path": "voicelines/fixture-current.wav",
        "audio_state": "current",
        "audio_sha256": sha256_file(audio_path),
        "audio_size_bytes": audio_path.stat().st_size,
        "audio_duration_ms": 1000,
        "audio_format": "wav",
        "stale_audio_path": None,
    })
    chunks.append(ready_chunk)
    manager = ProjectManager(
        str(root),
        config_path=str(root / "app" / "config.json"),
    )
    chunk["audio_fingerprint"] = manager._audio_binding(
        chunk,
        voice_config,
        resolved_speaker="BERNICE",
    )
    chunks_path.write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def assert_isolated_runtime(base_url: str, fixture_root: Path) -> dict:
    runtime = _json_get(f"{base_url}api/runtime_status")
    expected_root = str(fixture_root.resolve())
    if runtime.get("active_project_root") != expected_root:
        raise RuntimeError(
            "isolated browser server activated a non-disposable project: "
            f"{runtime.get('active_project_root')}"
        )
    if runtime.get("active_project_storage_kind") != "legacy_checkout":
        raise RuntimeError("isolated browser server did not use the disposable legacy project")
    if runtime.get("active_project_id") != "fixture-project":
        raise RuntimeError("isolated browser server did not use the declared fixture project ID")
    produce = _json_get(f"{base_url}api/produce")
    chunks = produce.get("chunks") or []
    current = next((item for item in chunks if item.get("chunk_id") == "chunk:0"), None)
    if not current or not current.get("audio", {}).get("available"):
        raise RuntimeError("isolated browser server did not expose the synthetic current audio fixture")
    ready = next((item for item in chunks if item.get("chunk_id") == "chunk:1"), None)
    if not ready or ready.get("state") != "ready":
        raise RuntimeError("isolated browser server did not expose the synthetic ready chunk fixture")
    return {"runtime": runtime, "produce_chunk": current, "ready_chunk": ready}


def parse_cli(argv: Sequence[str]) -> RunnerConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--viewports", default="390x844,768x1024,1024x768,1536x1024")
    parser.add_argument("--fresh-only", action="store_true")
    parser.add_argument("--include-b19-t06", action="store_true")
    values = parser.parse_args(argv)
    if values.artifacts is None and values.evidence_dir is None:
        parser.error("one of --artifacts or --evidence-dir is required")
    if values.artifacts is not None and values.evidence_dir is not None:
        parser.error("use only one of --artifacts or --evidence-dir")
    if values.viewports != "390x844,768x1024,1024x768,1536x1024":
        parser.error("exact B19-T07 viewport matrix is required")
    artifact_root = values.evidence_dir or values.artifacts
    return RunnerConfig(
        artifact_root.resolve(),
        values.repo_root.resolve(),
        values.python.absolute(),
        values.manifest.resolve(),
        values.include_b19_t06,
        values.fresh_only,
    )


def run_matrix(config: RunnerConfig) -> int:
    plan = run_plan(config.manifest)
    if config.fresh_only and config.artifacts.exists() and any(config.artifacts.iterdir()):
        raise RuntimeError(f"fresh evidence directory is not empty: {config.artifacts}")
    config.artifacts.mkdir(parents=True, exist_ok=True)
    diagnostics = diagnostics_directory(config.artifacts)
    diagnostics.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="alexandria-b19-t07-") as temporary:
        root = Path(temporary)
        fixture_root = root / "app-fixture"
        hostile_root = root / "hostile-catalog"
        _copy_fixture(config.repo_root, fixture_root)
        write_accessibility_fixture_data(fixture_root)
        hostile_root.mkdir()
        hostile = prepare_disposable_fixture(hostile_root)
        hostile_project = (hostile.hostile.catalog_root / "project.json").read_text(encoding="utf-8")
        hostile_rows = (hostile.hostile.catalog_root / "script_rows.json").read_text(encoding="utf-8")
        (fixture_root / "hostile-project.json").write_text(hostile_project, encoding="utf-8")
        (fixture_root / "annotated_script.json").write_text(hostile_rows, encoding="utf-8")
        port = _port()
        environment = isolated_server_environment(fixture_root)
        server_stdout = diagnostics / "server.stdout.txt"
        server_stderr = diagnostics / "server.stderr.txt"
        with server_stdout.open("w", encoding="utf-8") as stdout, server_stderr.open(
            "w", encoding="utf-8"
        ) as stderr:
            server = subprocess.Popen(
                [str(config.python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                cwd=fixture_root / "app", env=environment, stdout=stdout, stderr=stderr,
            )
            try:
                base_url = f"http://127.0.0.1:{port}/"
                _wait(base_url)
                runtime_receipt = assert_isolated_runtime(base_url, fixture_root)
                (diagnostics / "isolated-runtime.json").write_text(
                    json.dumps(runtime_receipt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                before = snapshot_protected_state(fixture_root)
                regression_ok = True
                if config.include_b19_t06:
                    regression_ok = run_b19_t06_regressions(
                        config, base_url, fixture_root, diagnostics
                    )
                child = subprocess.run(
                    ["node", str(config.repo_root / "tests" / "b19_t07_keyboard_ax_browser.js"), "--matrix",
                     "--url", base_url, "--manifest", str(config.manifest), "--artifacts", str(config.artifacts)],
                    cwd=config.repo_root, text=True, check=False, timeout=1800,
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        after = snapshot_protected_state(fixture_root)
        if not hostile.protected_state_unchanged or not compare_protected_state(before, after).ok:
            raise RuntimeError("browser matrix changed protected disposable state")
        if child.returncode != 0:
            return child.returncode
        if not regression_ok:
            return 1
    return 0 if plan.expected_case_count == 234 else 2


def main(argv: Sequence[str] | None = None) -> int:
    return run_matrix(parse_cli(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
