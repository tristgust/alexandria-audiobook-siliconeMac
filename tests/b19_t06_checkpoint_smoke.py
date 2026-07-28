# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app app/env/bin/python \
#   tests/b19_t06_checkpoint_smoke.py --repo-root "$PWD" \
#   --artifacts .omo/evidence/b19-t06-manual-checkpoint/browser \
#   --widths 1536x1024,1024x768,768x1024,390x844

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

from b19_t06_browser_acceptance import PROTECTED_NAMES, digest, write_fixture_data
from phase17e_api_harness import _copy_fixture


DEFAULT_PYTHON: Final = Path(
    "/Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python"
)
DEFAULT_WIDTHS: Final = "1536x1024,1024x768,768x1024,390x844"


@dataclass(frozen=True, slots=True)
class RunConfig:
    repo_root: Path
    artifacts: Path
    python: Path
    widths: str


@dataclass(frozen=True, slots=True)
class CheckpointError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def parse_cli(argv: Sequence[str]) -> RunConfig:
    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        name = argv[index]
        if not name.startswith("--") or index + 1 >= len(argv):
            raise CheckpointError(f"Expected --name value, got {name!r}")
        values[name[2:]] = argv[index + 1]
        index += 2
    repo_root = Path(values.get("repo-root", str(Path(__file__).parents[1])))
    artifacts = Path(
        values.get(
            "artifacts",
            repo_root / ".omo/evidence/b19-t06-manual-checkpoint/browser",
        )
    )
    return RunConfig(
        repo_root=repo_root.resolve(),
        artifacts=artifacts.resolve(),
        python=Path(values.get("python", str(DEFAULT_PYTHON))).absolute(),
        widths=values.get("widths", DEFAULT_WIDTHS),
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
    raise CheckpointError(f"Isolated server did not become ready: {last_error}")


def main() -> int:
    config = parse_cli(sys.argv[1:])
    config.artifacts.mkdir(parents=True, exist_ok=True)
    port = free_port()
    fixture_path = ""
    server_pid = 0
    node_exit = 2
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(
        prefix="alexandria-b19-t06-checkpoint-"
    ) as temporary:
        fixture_path = temporary
        fixture_root = Path(temporary)
        _copy_fixture(config.repo_root, fixture_root)
        shutil.copytree(
            config.repo_root / "docs/help",
            fixture_root / "docs/help",
        )
        write_fixture_data(fixture_root)
        before = {name: digest(fixture_root / name) for name in PROTECTED_NAMES}
        server_stdout = config.artifacts / "server.stdout.txt"
        server_stderr = config.artifacts / "server.stderr.txt"
        with server_stdout.open("w", encoding="utf-8") as stdout, server_stderr.open(
            "w", encoding="utf-8"
        ) as stderr:
            server = subprocess.Popen(
                [
                    str(config.python),
                    "-m",
                    "uvicorn",
                    "app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=fixture_root / "app",
                env=environment,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            server_pid = server.pid
            base_url = f"http://127.0.0.1:{port}/"
            try:
                wait_for_server(base_url)
                result = subprocess.run(
                    [
                        "node",
                        str(config.repo_root / "tests/b19_t06_checkpoint_smoke.js"),
                        "--url",
                        base_url,
                        "--artifacts",
                        str(config.artifacts),
                        "--widths",
                        config.widths,
                    ],
                    cwd=config.repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=360,
                )
                node_exit = result.returncode
                (config.artifacts / "action.log").write_text(
                    result.stdout, encoding="utf-8"
                )
                (config.artifacts / "browser.stderr.txt").write_text(
                    result.stderr, encoding="utf-8"
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        after = {name: digest(fixture_root / name) for name in PROTECTED_NAMES}
        if before != after:
            raise CheckpointError("Checkpoint smoke modified protected fixture files")
    cleanup_receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(config.artifacts.glob("*/cleanup.json"))
    ]
    cleanup = {
        "serverPid": server_pid,
        "serverExited": server.poll() is not None,
        "port": port,
        "temporaryPath": fixture_path,
        "temporaryRemoved": not Path(fixture_path).exists(),
        "browserSessionCount": len(cleanup_receipts),
        "allBrowsersExited": bool(cleanup_receipts)
        and all(item["browserExited"] for item in cleanup_receipts),
        "allBrowserProfilesRemoved": bool(cleanup_receipts)
        and all(item["profileRemoved"] for item in cleanup_receipts),
    }
    expected_sessions = len(config.widths.split(","))
    cleanup_passed = (
        cleanup["serverExited"]
        and cleanup["temporaryRemoved"]
        and cleanup["browserSessionCount"] == expected_sessions
        and cleanup["allBrowsersExited"]
        and cleanup["allBrowserProfilesRemoved"]
    )
    manifest = {
        "status": "PASS" if node_exit == 0 and cleanup_passed else "RED",
        "nodeExit": node_exit,
        "widths": config.widths,
        "protectedBefore": before,
        "protectedAfter": after,
        "cleanup": cleanup,
    }
    (config.artifacts / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print("B19_T06_CHECKPOINT_SMOKE=" + json.dumps(manifest, sort_keys=True))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, CheckpointError, subprocess.SubprocessError) as error:
        print(f"B19_T06_CHECKPOINT_ERROR={error}", file=sys.stderr)
        raise SystemExit(2) from error
