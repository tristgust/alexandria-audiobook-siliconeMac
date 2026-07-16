from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from phase17e_api_harness import _copy_fixture
from phase17e_browser_smoke import (
    CHROME_CANDIDATES,
    _free_port,
    _wait_for_debugger,
    _wait_for_server,
)


REPORT_PREFIX = "PHASE18C_BROWSER_REPORT="


def _inspect_dom(
    *,
    chrome: Path,
    url: str,
    profile: Path,
    probe: Path,
    expected: list[str],
    action_mode: str,
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
                "--action-mode",
                action_mode,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Phase 18C Chrome probe failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("PHASE18C_CDP_RESULT=")
        ]
        if len(lines) != 1:
            raise RuntimeError(
                "Phase 18C probe emitted no unique result.\n"
                f"STDOUT:\n{result.stdout}"
            )
        return json.loads(lines[0].split("=", 1)[1])
    finally:
        browser.terminate()
        try:
            browser.wait(timeout=10)
        except subprocess.TimeoutExpired:
            browser.kill()
            browser.wait(timeout=5)


def _entry(
    *,
    roster_module: Any,
    source_text: str,
    name: str,
    display_name: str,
    quote: str,
) -> dict[str, Any]:
    start = source_text.index(quote)
    end = start + len(quote)
    return {
        "id": roster_module.stable_entry_id(
            f"browser:{start}:{name}"
        ),
        "canonical_name": name,
        "display_name": display_name,
        "entity_kind": "character",
        "speaking_status": "speaker",
        "titles": [],
        "aliases": [],
        "nicknames": [],
        "pronouns": [],
        "species": [],
        "relationships": [],
        "first_evidence_location": f"characters {start}-{end}",
        "additional_evidence_locations": [],
        "confidence": 0.9,
        "resolution_status": "resolved",
        "possible_duplicate_ids": [],
        "mistaken_merge_risk": False,
        "unresolved_questions": [],
        "evidence": [
            {
                "source_quote": quote,
                "source_location": f"characters {start}-{end}",
                "start_char": start,
                "end_char": end,
                "passage_index": 1,
                "entry_index": None,
                "batch_index": 1,
                "category": "name",
                "confidence": 1.0,
                "basis": "explicit",
            }
        ],
        "voice_clues": [],
        "sample_lines": [],
    }


def run(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    chrome = next(
        (candidate for candidate in CHROME_CANDIDATES if candidate.exists()),
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
        old_cwd = Path.cwd()
        old_path = list(sys.path)
        sys.dont_write_bytecode = True

        try:
            os.chdir(fixture_root / "app")
            sys.path.insert(0, str(fixture_root / "app"))

            import character_roster
            from character_roster_actions import build_approved_roster

            source_text = (
                "The Doctor said, \"<script>alert(1)</script>\". "
                "Roz replied."
            )
            source_path = fixture_root / "app" / "uploads" / "browser.txt"
            source_path.write_text(source_text, encoding="utf-8")
            (fixture_root / "state.json").write_text(
                json.dumps({"input_file_path": str(source_path)}),
                encoding="utf-8",
            )
            source, _ = character_roster.build_source_snapshot(source_path)
            doctor = _entry(
                roster_module=character_roster,
                source_text=source_text,
                name="THE DOCTOR",
                display_name="<img src=x onerror=alert(1)>",
                quote="The Doctor",
            )
            roz = _entry(
                roster_module=character_roster,
                source_text=source_text,
                name="ROZ",
                display_name="Roz",
                quote="Roz",
            )
            discovery = {
                "created_at_utc": "2026-07-16T21:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "browser-generation",
                "batch_count": 1,
                "completed_batches": 1,
            }
            draft_path = fixture_root / "character_roster.draft.json"
            approved_path = fixture_root / "character_roster.json"

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
                probe = repo_root / "tests" / "phase18c_cdp_probe.js"
                profile = fixture_root / "chrome-profile"
                snapshots = {}

                snapshots["empty"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "empty",
                    probe=probe,
                    expected=[
                        "No character roster exists for this source",
                        "Discover Whole-Book Roster",
                    ],
                    action_mode="absent",
                )

                draft = character_roster.build_draft_roster(
                    source=source,
                    discovery=discovery,
                    entries=[doctor, roz],
                    duplicate_candidates=[
                        {
                            "entry_ids": [doctor["id"], roz["id"]],
                            "reason": "Browser duplicate comparison.",
                            "confidence": 0.5,
                            "evidence": [
                                *doctor["evidence"],
                                *roz["evidence"],
                            ],
                        }
                    ],
                    source_text=source_text,
                )
                character_roster.save_character_roster(
                    draft,
                    draft_path,
                    source_text=source_text,
                    expected_status="draft",
                )
                snapshots["draft"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "draft",
                    probe=probe,
                    expected=[
                        "Roster draft ready for review",
                        "Possible duplicate identities",
                        "Approve Canonical Roster",
                    ],
                    action_mode="present",
                )

                approved_draft = character_roster.build_draft_roster(
                    source=source,
                    discovery=discovery,
                    entries=[doctor],
                    source_text=source_text,
                )
                approved = build_approved_roster(
                    approved_draft,
                    expected_fingerprint=approved_draft[
                        "draft_fingerprint"
                    ],
                    source_fingerprint=source["fingerprint"],
                    source_text=source_text,
                    acknowledged_unresolved=False,
                    approved_at_utc="2026-07-16T21:10:00Z",
                )
                character_roster.save_character_roster(
                    approved,
                    approved_path,
                    source_text=source_text,
                    expected_status="approved",
                )
                snapshots["approved"] = _inspect_dom(
                    chrome=chrome,
                    url=url,
                    profile=profile / "approved",
                    probe=probe,
                    expected=[
                        "Canonical roster approved",
                        "read-only in Phase 18",
                    ],
                    action_mode="absent",
                )

                return {
                    "status": "PASS",
                    "browser": str(chrome),
                    "checks": {
                        "empty": True,
                        "draft": True,
                        "approved": True,
                        "escaped_dynamic_content": True,
                        "approved_read_only": True,
                    },
                    "snapshots": snapshots,
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
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.repo_root)
    print(REPORT_PREFIX + json.dumps(report, sort_keys=True))
    return 0 if report["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
