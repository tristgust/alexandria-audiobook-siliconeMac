from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from phase17e_api_harness import _copy_fixture


REPORT_PREFIX = "PHASE17E_BROWSER_REPORT="
CHROME_CANDIDATES = (
    Path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    ),
    Path(
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    ),
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)

    raise RuntimeError(
        f"Isolated server did not become ready: {last_error}"
    )


def _wait_for_debugger(
    port: int,
    timeout: float = 15.0,
) -> None:
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)

    raise RuntimeError(
        f"Chrome debugger did not become ready: {last_error}"
    )


def _inspect_dom(
    *,
    chrome: Path,
    url: str,
    profile: Path,
    probe: Path,
    expected: list[str],
) -> dict[str, Any]:
    debug_port = _free_port()
    browser = subprocess.Popen(
        [
            str(chrome),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--mute-audio",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        _wait_for_debugger(debug_port)
        result = subprocess.run(
            [
                "node",
                str(probe),
                "--port",
                str(debug_port),
                "--url",
                url,
                "--expected-json",
                json.dumps(expected),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Chrome DevTools probe failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("PHASE17E_CDP_RESULT=")
        ]
        if len(lines) != 1:
            raise RuntimeError(
                "Chrome DevTools probe emitted no unique result.\n"
                f"STDOUT:\n{result.stdout}"
            )
        return json.loads(
            lines[0].split("=", 1)[1]
        )
    finally:
        browser.terminate()
        try:
            browser.wait(timeout=10)
        except subprocess.TimeoutExpired:
            browser.kill()
            browser.wait(timeout=5)


def run(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    chrome = next(
        (
            candidate
            for candidate in CHROME_CANDIDATES
            if candidate.exists()
        ),
        None,
    )
    if chrome is None:
        return {
            "status": "SKIP",
            "reason": "No supported installed Chrome-family browser.",
        }

    with tempfile.TemporaryDirectory(
        prefix="alexandria-phase17e-browser-"
    ) as temporary:
        fixture_root = Path(temporary).resolve()
        _copy_fixture(repo_root.resolve(), fixture_root)

        old_cwd = Path.cwd()
        old_path = list(sys.path)
        sys.dont_write_bytecode = True

        try:
            os.chdir(fixture_root / "app")
            sys.path.insert(0, str(fixture_root / "app"))

            import generate_script
            from generation_state import (
                atomic_json_write,
                checkpoint_completed_chunk,
                new_generation_state,
            )

            source_path = fixture_root / "app" / "uploads" / "browser.txt"
            source_path.write_text(
                "\n\n".join(
                    f"Browser paragraph {index}. "
                    "The exact text remains stable for the smoke test."
                    for index in range(1, 13)
                ),
                encoding="utf-8",
            )
            (fixture_root / "state.json").write_text(
                json.dumps(
                    {"input_file_path": str(source_path)},
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            snapshot = generate_script.build_script_generation_snapshot(
                str(source_path)
            )
            checkpoint_path = fixture_root / "generation_state.json"

            def write_checkpoint(completed: int) -> None:
                state = new_generation_state(
                    source_fingerprint=snapshot["source_fingerprint"],
                    generation_fingerprint=snapshot[
                        "generation_fingerprint"
                    ],
                    chunk_fingerprints=snapshot[
                        "chunk_fingerprints"
                    ],
                    generation_identity=snapshot[
                        "generation_identity"
                    ],
                    source={
                        "path": snapshot["source_path"],
                        "basename": snapshot["source_basename"],
                    },
                    auditor_contract_version=snapshot[
                        "auditor_contract_version"
                    ],
                )
                atomic_json_write(state, checkpoint_path)
                for index in range(1, completed + 1):
                    state = checkpoint_completed_chunk(
                        state=state,
                        path=checkpoint_path,
                        index=index,
                        chunk_fingerprint=snapshot[
                            "chunk_fingerprints"
                        ][index - 1],
                        entries=[
                            {
                                "speaker": "NARRATOR",
                                "text": f"Browser chunk {index}.",
                                "instruct": "Neutral narration.",
                            }
                        ],
                    )

            port = _free_port()
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            server = subprocess.Popen(
                [
                    str(repo_root / "app" / "env" / "bin" / "python"),
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                url = f"http://127.0.0.1:{port}/"
                _wait_for_server(url)
                profile = fixture_root / "chrome-profile"
                probe = repo_root / "tests" / "phase17e_cdp_probe.js"
                checks: dict[str, bool] = {}
                snapshots: dict[str, dict[str, Any]] = {}

                try:
                    checkpoint_path.unlink()
                except FileNotFoundError:
                    pass
                snapshots["new"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "new",
                    probe=probe,
                    expected=[
                        "No saved generation progress",
                        "Generate Annotated Script",
                    ],
                )
                checks["new"] = True

                write_checkpoint(1)
                snapshots["resume"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "resume",
                    probe=probe,
                    expected=[
                        "Resume from chunk 2 of",
                        "Resume Script",
                    ],
                )
                checks["resume"] = True

                write_checkpoint(snapshot["total_chunks"])
                snapshots["finalize"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "finalize",
                    probe=probe,
                    expected=[
                        "All chunks are complete",
                        "Retry Finalization",
                    ],
                )
                checks["finalize"] = True

                write_checkpoint(1)
                source_path.write_text(
                    source_path.read_text(encoding="utf-8")
                    + "\nChanged after checkpoint.",
                    encoding="utf-8",
                )
                snapshots["incompatible"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "incompatible",
                    probe=probe,
                    expected=[
                        "Saved progress cannot be resumed",
                        "Source changed",
                    ],
                )
                checks["incompatible"] = True

                checkpoint_path.write_text(
                    "{corrupt",
                    encoding="utf-8",
                )
                snapshots["corrupt"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "corrupt",
                    probe=probe,
                    expected=[
                        "Saved progress is unusable",
                        "Discard",
                    ],
                )
                checks["corrupt"] = True

                if not all(checks.values()):
                    raise AssertionError(
                        f"Browser smoke state failed: {checks}"
                    )

                return {
                    "status": "PASS",
                    "browser": str(chrome),
                    "checks": checks,
                    "snapshots": snapshots,
                    "total_chunks": snapshot["total_chunks"],
                }
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        finally:
            os.chdir(old_cwd)
            sys.path[:] = old_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
    )
    args = parser.parse_args()
    report = run(args.repo_root)
    print(REPORT_PREFIX + json.dumps(report, sort_keys=True))
    return 0 if report["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
