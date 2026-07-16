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

from character_roster import (
    build_draft_roster,
    build_source_snapshot,
    read_character_roster,
    save_character_roster,
    stable_entry_id,
)
from phase17e_api_harness import _copy_fixture


REPORT_PREFIX = "PHASE18C_BROWSER_REPORT="
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


def _wait_url(url: str, timeout: float = 20.0) -> None:
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
        f"URL did not become ready: {url}: {last_error}"
    )


def _digest(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(
    source_text: str,
    quote: str,
    category: str = "name",
) -> dict[str, Any]:
    start = source_text.index(quote)
    return {
        "source_quote": quote,
        "source_location": f"characters {start}-{start + len(quote)}",
        "start_char": start,
        "end_char": start + len(quote),
        "passage_index": 1,
        "entry_index": None,
        "batch_index": 1,
        "category": category,
        "confidence": 1.0,
        "basis": "explicit",
    }


def _entry(
    *,
    source_text: str,
    identity_seed: str,
    canonical_name: str,
    display_name: str,
    quote: str,
    duplicate_id: str,
) -> dict[str, Any]:
    return {
        "id": stable_entry_id(identity_seed),
        "canonical_name": canonical_name,
        "display_name": display_name,
        "entity_kind": "character",
        "speaking_status": "uncertain",
        "titles": [],
        "aliases": [],
        "nicknames": [],
        "pronouns": [],
        "species": [],
        "relationships": [],
        "first_evidence_location": _evidence(
            source_text,
            quote,
        )["source_location"],
        "additional_evidence_locations": [],
        "confidence": 0.7,
        "resolution_status": "duplicate_candidate",
        "possible_duplicate_ids": [duplicate_id],
        "mistaken_merge_risk": True,
        "unresolved_questions": [
            "Are these descriptions the same person?"
        ],
        "evidence": [_evidence(source_text, quote)],
        "voice_clues": [],
        "sample_lines": [],
    }


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
            "reason": "No installed supported Chrome-family browser.",
        }

    with tempfile.TemporaryDirectory(
        prefix="alexandria-phase18c-browser-"
    ) as temporary:
        fixture_root = Path(temporary).resolve()
        _copy_fixture(repo_root, fixture_root)

        source_text = (
            "The Doctor waited beside the TARDIS. "
            "A short man in a battered hat stood nearby."
        )
        source_path = fixture_root / "app" / "uploads" / "book.txt"
        source_path.write_text(source_text, encoding="utf-8")
        (fixture_root / "state.json").write_text(
            json.dumps(
                {"input_file_path": str(source_path)},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        source, normalized = build_source_snapshot(source_path)
        first_id = stable_entry_id(
            f"{source['fingerprint']}:doctor"
        )
        second_id = stable_entry_id(
            f"{source['fingerprint']}:short-man"
        )
        first = _entry(
            source_text=normalized,
            identity_seed=f"{source['fingerprint']}:doctor",
            canonical_name="THE DOCTOR",
            display_name="The Doctor",
            quote="The Doctor",
            duplicate_id=second_id,
        )
        second = _entry(
            source_text=normalized,
            identity_seed=f"{source['fingerprint']}:short-man",
            canonical_name="THE SHORT MAN",
            display_name="The short man",
            quote="A short man in a battered hat",
            duplicate_id=first_id,
        )
        draft = build_draft_roster(
            source=source,
            discovery={
                "created_at_utc": "2026-07-16T22:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "browser-generation",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[first, second],
            duplicate_candidates=[
                {
                    "entry_ids": [first_id, second_id],
                    "reason": "The source may describe one person twice.",
                    "confidence": 0.7,
                    "evidence": [
                        _evidence(normalized, "The Doctor"),
                        _evidence(
                            normalized,
                            "A short man in a battered hat",
                        ),
                    ],
                }
            ],
            source_text=normalized,
        )
        draft_path = fixture_root / "character_roster.draft.json"
        approved_path = fixture_root / "character_roster.json"
        save_character_roster(
            draft,
            draft_path,
            source_text=normalized,
            expected_status="draft",
        )

        sentinel_paths = {
            "script": fixture_root / "annotated_script.json",
            "metadata": fixture_root / "annotated_script.meta.json",
            "voice": fixture_root / "voice_config.json",
            "chunks": fixture_root / "chunks.json",
        }
        sentinel_paths["script"].write_text(
            '[{"speaker":"NARRATOR","text":"Sentinel.","instruct":"Neutral."}]\n',
            encoding="utf-8",
        )
        sentinel_paths["metadata"].write_text(
            '{"sentinel":"metadata"}\n',
            encoding="utf-8",
        )
        sentinel_paths["voice"].write_text(
            '{"sentinel":"voice"}\n',
            encoding="utf-8",
        )
        sentinel_paths["chunks"].write_text(
            '[{"sentinel":"chunks"}]\n',
            encoding="utf-8",
        )
        before_hashes = {
            name: _digest(path)
            for name, path in sentinel_paths.items()
        }

        server_port = _free_port()
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
                str(server_port),
                "--log-level",
                "warning",
            ],
            cwd=fixture_root / "app",
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        browser = None

        try:
            page_url = f"http://127.0.0.1:{server_port}/"
            _wait_url(page_url)
            debug_port = _free_port()
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            _wait_url(
                f"http://127.0.0.1:{debug_port}/json/version"
            )
            probe = subprocess.run(
                [
                    "node",
                    str(
                        repo_root
                        / "tests"
                        / "phase18c_roster_cdp_probe.js"
                    ),
                    "--port",
                    str(debug_port),
                    "--url",
                    page_url,
                    "--entry-ids-json",
                    json.dumps([first_id, second_id]),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )
            if probe.returncode != 0:
                raise RuntimeError(
                    "Phase 18C Chrome probe failed.\n"
                    f"STDOUT:\n{probe.stdout}\n"
                    f"STDERR:\n{probe.stderr}"
                )
            lines = [
                line
                for line in probe.stdout.splitlines()
                if line.startswith("PHASE18C_CDP_RESULT=")
            ]
            if len(lines) != 1:
                raise RuntimeError(
                    "Phase 18C Chrome probe emitted no unique result."
                )
            browser_result = json.loads(
                lines[0].split("=", 1)[1]
            )
        finally:
            if browser is not None:
                browser.terminate()
                try:
                    browser.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait(timeout=5)
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

        approved = read_character_roster(
            approved_path,
            source_text=normalized,
            expected_status="approved",
        )
        after_hashes = {
            name: _digest(path)
            for name, path in sentinel_paths.items()
        }
        mutation = browser_result["mutation"]
        checks = {
            "initial_draft_rendered": (
                browser_result["initial"]["badge"] == "Draft"
                and browser_result["initial"]["actionCount"] > 0
            ),
            "stale_conflict": mutation["stale"]
            == {"status": 409, "code": "stale_draft"},
            "rename_applied": mutation["renamedName"]
            == "THE SEVENTH DOCTOR",
            "duplicate_resolved": mutation["duplicateCount"] == 0,
            "review_history": mutation["reviewActions"]
            == ["rename", "keep_separate", "confirm", "confirm"],
            "approved_rendered_read_only": (
                browser_result["final"]["badge"] == "Approved"
                and browser_result["final"]["actionCount"] == 0
                and browser_result["final"]["approvalDisplay"]
                == "none"
                and browser_result["final"]["discoverDisplay"]
                == "none"
            ),
            "approved_artifact_verified": (
                approved["status"] == "approved"
                and approved["entries"][0]["canonical_name"]
                == "THE SEVENTH DOCTOR"
                and not approved["duplicate_candidates"]
            ),
            "downstream_unchanged": before_hashes == after_hashes,
            "draft_preserved": draft_path.exists(),
        }

        if not all(checks.values()):
            raise AssertionError(
                f"Phase 18C browser smoke failed: {checks}"
            )

        return {
            "status": "PASS",
            "browser": str(chrome),
            "checks": checks,
            "browser_result": browser_result,
            "approved_fingerprint": approved[
                "roster_fingerprint"
            ],
        }


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
