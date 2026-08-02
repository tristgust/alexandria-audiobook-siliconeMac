# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app app/env/bin/python tests/b19_t06_browser_acceptance.py \
#   --routes-file tests/b19_t06_routes.json --artifacts .omo/evidence/b19-t06-red/browser \
#   --widths 1536x1024,1024x768,1440x1000,390x844

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Sequence

from phase17e_api_harness import _copy_fixture


DEFAULT_PYTHON: Final = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python")
EXPECTED_WIDTHS: Final = "1536x1024,1024x768,1440x1000,390x844"
PROTECTED_NAMES: Final = (
    "app/config.json", "state.json", "annotated_script.json",
    "annotated_script.meta.json", "generation_state.json", "character_roster.json",
    "persona_visual_state.json", "voice_config.json", "chunks.json",
    "references/bernice.wav",
)


@dataclass(frozen=True, slots=True)
class RunConfig:
    repo_root: Path
    routes_file: Path
    artifacts: Path
    python: Path
    widths: str
    url: str | None
    fixture_root: Path | None


@dataclass(frozen=True, slots=True)
class ChildResult:
    name: str
    exit_code: int
    stdout_file: str
    stderr_file: str


@dataclass(frozen=True, slots=True)
class AcceptanceError(Exception):
    """Describes a browser-acceptance setup or safety failure."""

    message: str

    def __str__(self) -> str:
        return self.message


def parse_cli(argv: Sequence[str]) -> RunConfig:
    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        name = argv[index]
        if not name.startswith("--") or index + 1 >= len(argv):
            raise AcceptanceError(f"Expected --name value, got {name!r}")
        values[name[2:]] = argv[index + 1]
        index += 2
    repo_root = Path(values.get("repo-root", str(Path(__file__).parents[1])))
    routes_file = Path(values.get("routes-file", repo_root / "tests/b19_t06_routes.json"))
    artifacts = Path(values.get("artifacts", repo_root / ".omo/evidence/b19-t06-red/browser"))
    url = values.get("url")
    fixture_root_value = values.get("fixture-root")
    if bool(url) != bool(fixture_root_value):
        raise AcceptanceError("--url and --fixture-root must be provided together")
    return RunConfig(
        repo_root=repo_root.resolve(),
        routes_file=routes_file.resolve(),
        artifacts=artifacts.resolve(),
        # Preserve the virtual-environment launcher path. Resolving its symlink
        # would execute the base interpreter without the environment packages.
        python=Path(values.get("python", DEFAULT_PYTHON)).absolute(),
        widths=values.get("widths", EXPECTED_WIDTHS),
        url=url,
        fixture_root=Path(fixture_root_value).resolve() if fixture_root_value else None,
    )


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_server(url: str) -> None:
    deadline = time.monotonic() + 20
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = error
            time.sleep(0.05)
    raise AcceptanceError(f"Isolated server did not become ready: {last_error}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "<absent>"


def write_fixture_data(root: Path) -> None:
    reference = root / "references/bernice.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"b19-t06-reference-audio")
    values = {
        "character_roster.json": {
            "entries": [{
                "id": "character_bernice",
                "canonical_name": "Bernice Summerfield",
                "display_name": "Bernice Summerfield",
                "resolution_status": "resolved",
                "speaking_status": "speaking",
            }]
        },
        "annotated_script.json": [{
            "speaker": "BERNICE",
            "text": "I know what I saw.",
            "instruct": "Controlled insistence.",
        }],
        "voice_config.json": {
            "BERNICE": {"type": "custom", "voice": "benny-main"}
        },
        "chunks.json": [{
            "id": 0,
            "speaker": "BERNICE",
            "text": "I know what I saw.",
            "instruct": "Controlled insistence.",
            "status": "pending",
            "audio_path": None,
        }],
    }
    for filename, value in values.items():
        (root / filename).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run_child(config: RunConfig, name: str, arguments: Sequence[str]) -> ChildResult:
    stdout_path = config.artifacts / f"{name}.stdout.txt"
    stderr_path = config.artifacts / f"{name}.stderr.txt"
    result = subprocess.run(
        ["node", str(config.repo_root / "tests" / f"b19_t06_{name}.js"), *arguments],
        cwd=config.repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return ChildResult(
        name=name,
        exit_code=result.returncode,
        stdout_file=str(stdout_path),
        stderr_file=str(stderr_path),
    )


def run_acceptance_children(config: RunConfig, base_url: str) -> list[ChildResult]:
    common = ["--url", base_url]
    commands = (
        ("bootstrap_red", [*common, "--artifacts", str(config.artifacts / "bootstrap")]),
        ("dom_ownership", [*common, "--artifacts", str(config.artifacts / "ownership")]),
        ("settings_navigation", [*common, "--artifacts", str(config.artifacts / "settings")]),
        ("route_matrix", [*common, "--routes", str(config.routes_file),
                          "--artifacts", str(config.artifacts / "routes")]),
        ("visual_compare", [*common, "--artifacts", str(config.artifacts / "visual")]),
        ("accessibility", [*common, "--artifacts", str(config.artifacts / "accessibility")]),
    )
    return [run_child(config, name, arguments) for name, arguments in commands]


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


def main() -> int:
    try:
        config = parse_cli(sys.argv[1:])
        if config.widths != EXPECTED_WIDTHS:
            raise AcceptanceError(f"Exact viewport matrix required: {EXPECTED_WIDTHS}")
        config.artifacts.mkdir(parents=True, exist_ok=True)
        temporary_path = ""
        children: list[ChildResult] = []
        server_pid = 0
        port = 0
        external_server = config.url is not None
        server_exited: bool | None = None
        if external_server:
            assert config.url is not None and config.fixture_root is not None
            fixture_root = config.fixture_root
            before = {name: digest(fixture_root / name) for name in PROTECTED_NAMES}
            children = run_acceptance_children(config, config.url)
            after = {name: digest(fixture_root / name) for name in PROTECTED_NAMES}
        else:
            port = free_port()
            with tempfile.TemporaryDirectory(prefix="alexandria-b19-t06-browser-") as temporary:
                temporary_path = temporary
                fixture_root = Path(temporary)
                _copy_fixture(config.repo_root, fixture_root)
                write_fixture_data(fixture_root)
                before = {name: digest(fixture_root / name) for name in PROTECTED_NAMES}
                server_stdout = config.artifacts / "server.stdout.txt"
                server_stderr = config.artifacts / "server.stderr.txt"
                with server_stdout.open("w", encoding="utf-8") as stdout, server_stderr.open(
                    "w", encoding="utf-8"
                ) as stderr:
                    server = subprocess.Popen(
                        [str(config.python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
                         "--port", str(port), "--log-level", "warning"],
                        cwd=fixture_root / "app",
                        env=isolated_server_environment(fixture_root),
                        stdout=stdout,
                        stderr=stderr,
                        text=True,
                    )
                    server_pid = server.pid
                    base_url = f"http://127.0.0.1:{port}/"
                    try:
                        wait_for_server(base_url)
                        children = run_acceptance_children(config, base_url)
                    finally:
                        server.terminate()
                        try:
                            server.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            server.kill()
                            server.wait(timeout=5)
                    server_exited = server.poll() is not None
                after = {name: digest(fixture_root / name) for name in PROTECTED_NAMES}
        if before != after:
            raise AcceptanceError("Browser acceptance modified protected fixture files")
        setup_errors = [item for item in children if item.exit_code not in {0, 1}]
        status = "ERROR" if setup_errors else (
            "PASS" if all(item.exit_code == 0 for item in children) else "RED"
        )
        visual_report = json.loads(
            (config.artifacts / "visual/report.json").read_text(encoding="utf-8")
        )
        accessibility_report = json.loads(
            (config.artifacts / "accessibility/report.json").read_text(encoding="utf-8")
        )
        browser_receipts = [
            {
                "path": str(path.relative_to(config.artifacts)),
                **json.loads(path.read_text(encoding="utf-8")),
            }
            for path in sorted(config.artifacts.rglob("cleanup.json"))
        ]
        if len(browser_receipts) != 15 or not all(
            receipt["browserExited"] and receipt["profileRemoved"]
            for receipt in browser_receipts
        ):
            raise AcceptanceError("Browser cleanup receipt set is incomplete")
        manifest = {
            "status": status,
            "baseHead": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=config.repo_root, text=True
            ).strip(),
            "widths": config.widths,
            "port": port,
            "serverPid": server_pid,
            "children": [asdict(item) for item in children],
            "protectedBefore": before,
            "protectedAfter": after,
            "visualCaptures": visual_report["captures"],
            "accessibilityCaptures": accessibility_report["captures"],
            "cleanup": {
                "externalServer": external_server,
                "serverExited": server_exited,
                "temporaryRemoved": external_server or not Path(temporary_path).exists(),
                "temporaryPath": temporary_path,
                "browserSessionCount": len(browser_receipts),
                "allBrowsersExited": all(
                    receipt["browserExited"] for receipt in browser_receipts
                ),
                "allBrowserProfilesRemoved": all(
                    receipt["profileRemoved"] for receipt in browser_receipts
                ),
                "browserReceipts": browser_receipts,
            },
        }
        (config.artifacts / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        print("B19_T06_BROWSER_ACCEPTANCE=" + json.dumps(manifest, sort_keys=True))
        return 2 if status == "ERROR" else (0 if status == "PASS" else 1)
    except (OSError, AcceptanceError, subprocess.SubprocessError) as error:
        print(f"B19_T06_BROWSER_ACCEPTANCE_ERROR={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
