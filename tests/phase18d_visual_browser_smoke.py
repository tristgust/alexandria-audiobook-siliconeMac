from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
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


REPORT_PREFIX = "PHASE18D_VISUAL_BROWSER_REPORT="


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _protected_hashes(root: Path) -> dict[str, str]:
    names = [
        "app/config.json",
        "state.json",
        "annotated_script.json",
        "annotated_script.meta.json",
        "generation_state.json",
        "chunks.json",
        "voice_config.json",
        "character_roster.json",
        "character_roster.draft.json",
        "character_roster_state.json",
        "persona_visual_state.json",
    ]
    result = {}
    for name in names:
        path = root / name
        result[name] = (
            _digest(path)
            if path.exists()
            else "<absent>"
        )
    result["persona_refs"] = json.dumps(
        _hash_tree(root / "persona_refs"),
        sort_keys=True,
    )
    return result


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"Screenshot is not a PNG: {path}")
    return struct.unpack(">II", data[16:24])


def _probe(
    *,
    chrome: Path,
    url: str,
    profile: Path,
    probe: Path,
    mode: str,
    width: int,
    height: int,
    screenshot: Path,
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
                "--mode",
                mode,
                "--width",
                str(width),
                "--height",
                str(height),
                "--screenshot",
                str(screenshot),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Phase 18D visual Chrome probe failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("PHASE18D_VISUAL_CDP_RESULT=")
        ]
        if len(lines) != 1:
            raise RuntimeError(
                "Phase 18D visual probe emitted no unique result.\n"
                f"STDOUT:\n{result.stdout}"
            )
        if not screenshot.exists():
            raise RuntimeError(
                f"Phase 18D probe did not write {screenshot}."
            )
        width_px, height_px = _png_dimensions(screenshot)
        payload = json.loads(lines[0].split("=", 1)[1])
        payload["screenshot"] = {
            "width": width_px,
            "height": height_px,
            "sha256": _digest(screenshot),
            "bytes": screenshot.stat().st_size,
        }
        return payload
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
            "reason": "No installed supported Chrome-family browser.",
        }

    with tempfile.TemporaryDirectory(
        prefix="alexandria-phase18d-visual-browser-"
    ) as temporary:
        fixture_root = Path(temporary).resolve()
        _copy_fixture(repo_root, fixture_root)
        (fixture_root / "persona_refs").mkdir(
            parents=True,
            exist_ok=True,
        )
        artifacts = fixture_root / "browser-artifacts"
        artifacts.mkdir()
        old_cwd = Path.cwd()
        old_path = list(sys.path)
        sys.dont_write_bytecode = True

        try:
            os.chdir(fixture_root / "app")
            sys.path.insert(0, str(fixture_root / "app"))

            import character_roster
            from character_roster_actions import (
                build_approved_roster,
            )
            from character_visuals import (
                PROFILE_BUCKETS,
                build_visual_dossier,
                persona_reference_targets,
                write_visual_dossier,
            )

            source_text = (
                "The Khepri had <svg onload=alert(1)>four translucent "
                "wings</svg>. The dossier called the silhouette "
                "<script>alert(1)</script>."
            )
            source_path = (
                fixture_root
                / "app"
                / "uploads"
                / "visual-browser.txt"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                source_text,
                encoding="utf-8",
            )
            (fixture_root / "state.json").write_text(
                json.dumps(
                    {"input_file_path": str(source_path)}
                ),
                encoding="utf-8",
            )
            source, _ = character_roster.build_source_snapshot(
                source_path
            )
            name_quote = "The Khepri"
            name_start = source_text.index(name_quote)
            name_end = name_start + len(name_quote)
            entry = {
                "id": character_roster.stable_entry_id(
                    "phase18d-browser-khepri"
                ),
                "canonical_name": "THE KHEPRI",
                "display_name": "<img src=x onerror=alert(1)>",
                "entity_kind": "creature",
                "speaking_status": "speaker",
                "titles": [],
                "aliases": ["KHEPRI"],
                "nicknames": [],
                "pronouns": [],
                "species": ["Khepri"],
                "relationships": [],
                "first_evidence_location": (
                    f"characters {name_start}-{name_end}"
                ),
                "additional_evidence_locations": [],
                "confidence": 0.95,
                "resolution_status": "resolved",
                "possible_duplicate_ids": [],
                "mistaken_merge_risk": False,
                "unresolved_questions": [],
                "evidence": [
                    {
                        "source_quote": name_quote,
                        "source_location": (
                            f"characters {name_start}-{name_end}"
                        ),
                        "start_char": name_start,
                        "end_char": name_end,
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
            draft = character_roster.build_draft_roster(
                source=source,
                discovery={
                    "created_at_utc": "2026-07-16T23:30:00Z",
                    "model_name": "qwen3.5:35b-mlx",
                    "backend": "ollama-native",
                    "generation_fingerprint": "visual-browser",
                    "batch_count": 1,
                    "completed_batches": 1,
                },
                entries=[entry],
                source_text=source_text,
            )
            approved = build_approved_roster(
                draft,
                expected_fingerprint=draft["draft_fingerprint"],
                source_fingerprint=source["fingerprint"],
                source_text=source_text,
                acknowledged_unresolved=False,
                approved_at_utc="2026-07-16T23:35:00Z",
            )
            character_roster.save_character_roster(
                approved,
                fixture_root / "character_roster.json",
                source_text=source_text,
                expected_status="approved",
            )

            port = _free_port()
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            server = subprocess.Popen(
                [
                    str(
                        repo_root
                        / "app"
                        / "env"
                        / "bin"
                        / "python"
                    ),
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
                probe = (
                    repo_root
                    / "tests"
                    / "phase18d_visual_cdp_probe.js"
                )
                profile = fixture_root / "chrome-profile"
                before_idle = _protected_hashes(fixture_root)
                disabled = _probe(
                    chrome=chrome,
                    url=url,
                    profile=profile / "disabled",
                    probe=probe,
                    mode="disabled-idle",
                    width=1440,
                    height=1100,
                    screenshot=artifacts / "disabled-desktop.png",
                )
                after_idle = _protected_hashes(fixture_root)
                if before_idle != after_idle:
                    raise RuntimeError(
                        "Opening the disabled visual workspace modified files."
                    )

                enabled = _probe(
                    chrome=chrome,
                    url=url,
                    profile=profile / "enabled",
                    probe=probe,
                    mode="enable-selection",
                    width=1440,
                    height=1100,
                    screenshot=artifacts / "enabled-desktop.png",
                )
                after_enable = _protected_hashes(fixture_root)
                if after_idle != after_enable:
                    raise RuntimeError(
                        "Enabling and selecting visual controls modified "
                        "files before collection."
                    )

                quote = (
                    "<svg onload=alert(1)>four translucent wings</svg>"
                )
                start = source_text.index(quote)
                end = start + len(quote)
                observation_id = "visual_browser_wings"
                profile_data = {
                    bucket: []
                    for bucket in PROFILE_BUCKETS
                }
                profile_data["nonhuman_anatomy"] = [
                    {
                        "detail": "<b>four translucent wings</b>",
                        "certainty": 0.96,
                        "observation_ids": [observation_id],
                    }
                ]
                visual = build_visual_dossier(
                    observations=[
                        {
                            "observation_id": observation_id,
                            "category": "nonhuman_anatomy",
                            "detail": "<b>four translucent wings</b>",
                            "scope": "stable",
                            "certainty": 0.96,
                            "basis": "explicit",
                            "quote": quote,
                            "source_location": (
                                f"characters {start}-{end}"
                            ),
                            "start_char": start,
                            "end_char": end,
                            "passage_index": 1,
                        }
                    ],
                    profile=profile_data,
                    unknowns=[
                        {
                            "category": "eyes",
                            "question": (
                                "<script>alert(1)</script> eye colour "
                                "is unknown."
                            ),
                        }
                    ],
                    source_text=source_text,
                )
                ownership = [
                    {
                        "entry_id": entry["id"],
                        "character_name": entry["canonical_name"],
                    }
                ]
                target = persona_reference_targets(
                    persona_refs_dir=(
                        fixture_root / "persona_refs"
                    ),
                    selected_entries=ownership,
                    all_entries=ownership,
                )[entry["id"]]
                write_visual_dossier(
                    persona_ref_path=target,
                    visual=visual,
                    character_name=entry["canonical_name"],
                    aliases=entry["aliases"],
                    source_text=source_text,
                    entry_id=entry["id"],
                    source_fingerprint=source["fingerprint"],
                    roster_fingerprint=approved[
                        "roster_fingerprint"
                    ],
                )
                before_detail = _protected_hashes(fixture_root)
                complete = _probe(
                    chrome=chrome,
                    url=url,
                    profile=profile / "complete",
                    probe=probe,
                    mode="complete-detail",
                    width=1440,
                    height=1100,
                    screenshot=artifacts / "complete-desktop.png",
                )
                narrow = _probe(
                    chrome=chrome,
                    url=url,
                    profile=profile / "narrow",
                    probe=probe,
                    mode="narrow-complete-detail",
                    width=390,
                    height=900,
                    screenshot=artifacts / "complete-narrow.png",
                )
                after_detail = _protected_hashes(fixture_root)
                if before_detail != after_detail:
                    raise RuntimeError(
                        "Viewing completed visual dossiers modified files."
                    )

                return {
                    "status": "PASS",
                    "browser": str(chrome),
                    "checks": {
                        "disabled_default_no_write": True,
                        "enable_selection_no_write": True,
                        "complete_detail_safe": True,
                        "detail_read_only": True,
                        "desktop_capture": True,
                        "narrow_capture": True,
                    },
                    "snapshots": {
                        "disabled": disabled,
                        "enabled": enabled,
                        "complete": complete,
                        "narrow": narrow,
                    },
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
