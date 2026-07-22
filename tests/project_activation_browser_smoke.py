from __future__ import annotations

import argparse
import hashlib
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

import httpx

from phase17e_api_harness import _copy_fixture


REPORT_PREFIX = "PROJECT_ACTIVATION_BROWSER="
CHROME_CANDIDATES = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
)
PROTECTED_NAMES = (
    "annotated_script.json",
    "annotated_script.meta.json",
    "chunks.json",
    "voice_config.json",
    "audio_validity.json",
    "generation_state.json",
    "character_roster.json",
    "character_roster_state.json",
    "cloned_audiobook.mp3",
    "audiobook.m4b",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_url(url: str, timeout: float = 30.0) -> None:
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
    raise RuntimeError(f"URL did not become ready: {url}: {last_error}")


def _hash(path: Path) -> str:
    if not path.is_file():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes(root: Path) -> dict[str, str]:
    return {name: _hash(root / name) for name in PROTECTED_NAMES}


def _create_project(
    client: httpx.Client,
    *,
    name: str,
    source_name: str,
    fingerprint: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "book_title": name,
            "author": "Alexandria QA",
            "source_language": "English",
            "output_language": "English",
            "generation_method": "local",
            "preset": "standard",
            "expected_catalog_fingerprint": fingerprint,
        },
        files={
            "source_file": (
                source_name,
                f"Source text for {name}.\n".encode("utf-8"),
                "text/plain",
            )
        },
    )
    response.raise_for_status()
    return response.json()


def _terminate(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(repo_root: Path, output_dir: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    chrome = next((item for item in CHROME_CANDIDATES if item.is_file()), None)
    if chrome is None:
        return {"status": "SKIP", "reason": "No supported Chrome-family browser."}

    repo_before = _protected_hashes(repo_root)
    server: subprocess.Popen[Any] | None = None
    browser: subprocess.Popen[Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix="alexandria-project-activation-browser-"
    ) as temporary:
        fixture_root = Path(temporary).resolve()
        _copy_fixture(repo_root, fixture_root)
        source = fixture_root / "legacy-source.txt"
        source.write_text("Legacy source.\n", encoding="utf-8")
        (fixture_root / "state.json").write_text(
            json.dumps(
                {
                    "input_file_path": str(source),
                    "book_title": "Legacy Browser Fixture",
                    "author": "Alexandria QA",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        fixture_before = _protected_hashes(fixture_root)
        data_root = fixture_root / "application-data"
        server_port = _free_port()
        debug_port = _free_port()
        base_url = f"http://127.0.0.1:{server_port}"
        server_log = (output_dir / "server.log").open("w", encoding="utf-8")
        browser_log = (output_dir / "browser.log").open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "ALEXANDRIA_HOST": "127.0.0.1",
                "ALEXANDRIA_PORT": str(server_port),
                "ALEXANDRIA_DATA_ROOT": str(data_root),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(fixture_root / "app"),
            }
        )
        try:
            server = subprocess.Popen(
                [sys.executable, str(fixture_root / "app" / "app.py")],
                cwd=fixture_root / "app",
                env=environment,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_for_url(f"{base_url}/api/runtime_status")
            with httpx.Client(base_url=base_url, timeout=30.0) as client:
                catalog = client.get("/api/projects")
                catalog.raise_for_status()
                first = _create_project(
                    client,
                    name="Browser Project One",
                    source_name="browser-one.txt",
                    fingerprint=catalog.json()["catalog_fingerprint"],
                )
                second = _create_project(
                    client,
                    name="Browser Project Two",
                    source_name="browser-two.txt",
                    fingerprint=first["catalog_fingerprint"],
                )
                target_project_id = first["project"]["id"]
                starting_project_id = second["project"]["id"]
                runtime = client.get("/api/runtime_status")
                runtime.raise_for_status()
                if runtime.json().get("active_project_id") != starting_project_id:
                    raise AssertionError("Fixture did not begin on the second project.")

            profile = fixture_root / "chrome-profile"
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
                stdout=browser_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_for_url(f"http://127.0.0.1:{debug_port}/json/version")
            screenshot = output_dir / "managed-project-activated.png"
            probe = subprocess.run(
                [
                    "node",
                    str(repo_root / "tests" / "project_activation_cdp.js"),
                    "--port",
                    str(debug_port),
                    "--url",
                    f"{base_url}/#/projects",
                    "--project-id",
                    target_project_id,
                    "--screenshot",
                    str(screenshot),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            (output_dir / "probe.stdout.log").write_text(
                probe.stdout, encoding="utf-8"
            )
            (output_dir / "probe.stderr.log").write_text(
                probe.stderr, encoding="utf-8"
            )
            if probe.returncode != 0:
                raise RuntimeError(
                    "Project activation browser probe failed.\n"
                    f"STDOUT:\n{probe.stdout}\nSTDERR:\n{probe.stderr}"
                )
            line = next(
                (
                    item
                    for item in probe.stdout.splitlines()
                    if item.startswith("PROJECT_ACTIVATION_CDP=")
                ),
                None,
            )
            if line is None:
                raise RuntimeError("Browser probe emitted no result marker.")
            browser_report = json.loads(line.split("=", 1)[1])
            fixture_after = _protected_hashes(fixture_root)
            repo_after = _protected_hashes(repo_root)
            active_root = Path(
                browser_report["afterActiveProjectRoot"]
            ).resolve()
            if not active_root.is_relative_to(data_root.resolve()):
                raise AssertionError(
                    f"Browser fixture escaped its isolated data root: {active_root}"
                )
            report = {
                "status": "PASS",
                "isolated_data_root": str(data_root.resolve()),
                "target_project_id": target_project_id,
                "starting_project_id": starting_project_id,
                "browser": browser_report,
                "repo_protected_hashes_unchanged": repo_before == repo_after,
                "fixture_legacy_hashes_unchanged": fixture_before == fixture_after,
                "repo_protected_hashes_before": repo_before,
                "repo_protected_hashes_after": repo_after,
                "fixture_legacy_hashes_before": fixture_before,
                "fixture_legacy_hashes_after": fixture_after,
            }
            if not report["repo_protected_hashes_unchanged"]:
                raise AssertionError("The browser proof changed repository project artifacts.")
            if not report["fixture_legacy_hashes_unchanged"]:
                raise AssertionError("The browser proof changed the legacy fixture artifacts.")
            (output_dir / "report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return report
        finally:
            _terminate(browser)
            _terminate(server)
            server_log.close()
            browser_log.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = run(Path(args.repo_root), Path(args.output_dir))
    print(REPORT_PREFIX + json.dumps(report, sort_keys=True))
    if report.get("status") not in {"PASS", "SKIP"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
