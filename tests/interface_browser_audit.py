from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
for import_root in (APP_ROOT, Path(__file__).resolve().parent):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from character_roster import save_character_roster
from generation_state import fingerprint_text, fingerprint_value
from interface_phase24d_performance import phase24d_render_failures
from phase17e_api_harness import _copy_fixture
from phase17e_browser_smoke import (
    CHROME_CANDIDATES,
    _free_port,
    _wait_for_debugger,
    _wait_for_server,
)
from test_voice_training_projects import VoiceTrainingProjectFixture
from voice_training_projects import (
    build_voice_training_project,
    save_voice_training_project,
    voice_training_project_path,
)


REPORT_PREFIX = "INTERFACE_BROWSER_AUDIT="


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_path_snapshot(root: Path, browser_config: Path) -> dict[str, Any]:
    file_paths = (
        "state.json",
        "generation_state.json",
        "annotated_script.json",
        "annotated_script.meta.json",
        "character_roster.json",
        "voice_config.json",
        "chunks.json",
        "audio_validity.json",
        "export_build.json",
        "migration_state.json",
        "application-data/projects.json",
    )
    directory_paths = (
        "clone_voices",
        "designed_voices",
        "dataset_builder",
        "lora_datasets",
        "lora_models",
        "preparer_output",
        "voice_training_projects",
        "voicelines",
        "exports",
        "external_workflows",
        "task_bundles",
        "migration_backups",
        "application-data/Projects",
    )
    files: dict[str, Any] = {}
    for relative in file_paths:
        path = root / relative
        files[relative] = (
            {"exists": True, "size": path.stat().st_size, "sha256": _sha256_file(path)}
            if path.is_file()
            else {"exists": False}
        )
    files["browser-config.json"] = (
        {
            "exists": True,
            "size": browser_config.stat().st_size,
            "sha256": _sha256_file(browser_config),
        }
        if browser_config.is_file()
        else {"exists": False}
    )
    directories: dict[str, Any] = {}
    for relative in directory_paths:
        directory = root / relative
        records = []
        if directory.is_dir():
            for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                records.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": _sha256_file(path),
                    }
                )
        directories[relative] = records
    payload = {"files": files, "directories": directories}
    return {
        **payload,
        "snapshot_sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def _read_json_url(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _seed_fixture(root: Path) -> None:
    evidence_names = (
        "20260717T014952Z_phase22_apple_silicon.json",
        "20260717T031401Z_voxcpm2_controlled_clone.json",
        "20260719T213000Z_mps_lora_merged_mlx.json",
    )
    evidence_directory = root / "benchmarks" / "results"
    evidence_directory.mkdir(parents=True, exist_ok=True)
    for evidence_name in evidence_names:
        evidence_source = REPO_ROOT / "benchmarks" / "results" / evidence_name
        shutil.copy2(evidence_source, evidence_directory / evidence_name)

    lora_root = root / "lora_models"
    lora_model = lora_root / "browser_lora_pilot" / "mlx_model"
    (lora_model / "speech_tokenizer").mkdir(parents=True, exist_ok=True)
    for relative in (
        "model.safetensors",
        "config.json",
        "ref_sample.wav",
        "ref_sample.txt",
        "validation_neutral.wav",
        "validation_expressive.wav",
        "speech_tokenizer/model.safetensors",
    ):
        path = lora_model / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"browser-lora-fixture")
    export_fingerprint = "e" * 64
    (lora_model / "mlx_export_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_format": "merged_mlx_qwen_checkpoint",
                "status": "validated_experimental",
                "technical_validation_passed": True,
                "production_assignment_supported": False,
                "export_fingerprint": export_fingerprint,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (lora_model.parent / "preview_sample.wav").write_bytes(
        b"browser-preview-fixture"
    )
    (lora_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": "browser_lora_pilot",
                    "name": "Browser LoRA pilot",
                    "experimental": True,
                    "technical_validation_passed": True,
                    "production_assignment_supported": False,
                    "manual_audio_review_status": "pending",
                    "export_fingerprint": export_fingerprint,
                    "base_model_revision": "a" * 40,
                    "adapter_path": "lora_models/browser_lora_pilot",
                    "mlx_model_path": (
                        "lora_models/browser_lora_pilot/mlx_model"
                    ),
                    "neutral_rtf": 0.6,
                    "expressive_rtf": 0.4,
                    "speaker_cosine_floor": 0.98,
                    "created": 0,
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    source_path = root / "book.txt"
    source_path.write_text(
        VoiceTrainingProjectFixture.SOURCE_TEXT,
        encoding="utf-8",
    )
    (root / "state.json").write_text(
        json.dumps({"input_file_path": str(source_path)}, indent=2),
        encoding="utf-8",
    )
    roster = VoiceTrainingProjectFixture.approved_roster(source_path)
    save_character_roster(
        roster,
        root / "character_roster.json",
        source_text=VoiceTrainingProjectFixture.SOURCE_TEXT,
        expected_status="approved",
    )
    doctor = next(
        entry
        for entry in roster["entries"]
        if entry["canonical_name"] == "THE DOCTOR"
    )
    candidate = build_voice_training_project(
        approved_roster=roster,
        character_id=doctor["id"],
        priority="primary",
        desired_description=(
            "An alert older traveler with a compact, incisive delivery and "
            "controlled warmth."
        ),
        desired_ref_text="Tell me exactly what happened.",
        created_at_utc="2026-07-16T20:00:00Z",
    )
    project_path = voice_training_project_path(
        root / "voice_training_projects",
        doctor["id"],
    )
    save_voice_training_project(candidate, project_path)

    script = [
        {
            "speaker": "THE DOCTOR",
            "text": "The library is never empty; it merely changes who is listening.",
            "instruct": "Quick, precise, and quietly delighted.",
        },
        {
            "speaker": "ROZ",
            "text": "That is not remotely reassuring.",
            "instruct": "Dry skepticism with controlled unease.",
        },
        {
            "speaker": "NARRATOR",
            "text": "The library doors closed behind them, muting the storm outside.",
            "instruct": "Measured narration with quiet tension.",
        },
        {
            "speaker": "ELENA",
            "text": "We should not have come here after dark.",
            "instruct": "Low and controlled, uneasy but decisive.",
        },
        {
            "speaker": "MARCUS",
            "text": "You said the archive would be empty.",
            "instruct": "Dry skepticism masking concern.",
        },
        {
            "speaker": "NARRATOR",
            "text": "A light appeared between the stacks where no lamp had been burning.",
            "instruct": "Slow, ominous narration.",
        },
        {
            "speaker": "ARCHIVIST",
            "text": "Empty is not the same as unoccupied.",
            "instruct": "Ancient, precise, and dispassionately amused.",
        },
    ]
    (root / "annotated_script.json").write_text(
        json.dumps(script, indent=2),
        encoding="utf-8",
    )
    script_identity = {
        "mode": "native",
        "backend": "ollama",
        "model_name": "browser-audit-local-model",
    }
    (root / "annotated_script.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-07-21T12:00:00Z",
                "source": {
                    "basename": source_path.name,
                    "fingerprint": fingerprint_text(
                        VoiceTrainingProjectFixture.SOURCE_TEXT
                    ),
                    "verification_status": "verified",
                    "character_count": len(
                        VoiceTrainingProjectFixture.SOURCE_TEXT
                    ),
                    "chunk_count": 1,
                },
                "generation": {
                    "fingerprint": fingerprint_value(script_identity),
                    "effective_identity": script_identity,
                },
                "result": {
                    "script_fingerprint": fingerprint_value(script),
                    "entry_count": len(script),
                    "speaker_labels": sorted(
                        {entry["speaker"] for entry in script}
                    ),
                },
                "resume": {
                    "resumed": False,
                    "previously_completed_chunks": 0,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    voice_config = {
        "THE DOCTOR": {
            "type": "clone",
            "ref_audio": "clone_voices/doctor_reference.wav",
            "ref_text": "The library is never empty; it merely changes who is listening.",
            "character_style": "Quick, precise, and alert with controlled warmth.",
            "clone_backend": "qwen3_base",
        },
        "ROZ": {
            "type": "design",
            "description": "A grounded lower female voice with dry authority and crisp diction.",
        },
        "NARRATOR": {
            "type": "custom",
            "voice": "Ryan",
            "character_style": "Warm literary narration with restrained tension.",
        },
        "ELENA": {
            "type": "design",
            "description": "A composed lower female voice with crisp diction and controlled intensity.",
        },
        "MARCUS": {
            "type": "custom",
            "voice": "Aiden",
            "character_style": "Dry, intelligent, guarded delivery.",
            "alias_of": "NARRATOR",
        },
        "ARCHIVIST": {
            "type": "builtin_lora",
            "adapter_id": "",
            "character_style": "Ageless, precise, faintly amused.",
        },
    }
    (root / "voice_config.json").write_text(
        json.dumps(voice_config, indent=2),
        encoding="utf-8",
    )
    clone_reference = root / "clone_voices" / "doctor_reference.wav"
    clone_reference.parent.mkdir(parents=True, exist_ok=True)
    clone_reference.write_bytes(b"RIFFfixture-clone-reference")

    chunks = []
    for index, entry in enumerate(script):
        chunks.append(
            {
                "id": index,
                "speaker": entry["speaker"],
                "text": entry["text"],
                "instruct": entry["instruct"],
                "status": "done" if index in {0, 1} else "pending",
                "audio_path": "voicelines/chunk_0.wav" if index == 0 else None,
                "pause_after": None,
            }
        )
    (root / "chunks.json").write_text(
        json.dumps(chunks, indent=2),
        encoding="utf-8",
    )
    generated_line = root / "voicelines" / "chunk_0.wav"
    generated_line.parent.mkdir(parents=True, exist_ok=True)
    generated_line.write_bytes(b"RIFFfixture-wave-data")
    (root / "audio_validity.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stale": False,
                "updated_at_utc": "2026-07-21T12:15:00Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    built_output = root / "cloned_audiobook.mp3"
    built_output.write_bytes(b"browser-library-export-output")
    (root / "export_build.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "built_at_utc": "2026-07-21T12:30:00Z",
                "outputs": {
                    "mp3": {
                        "sha256": hashlib.sha256(
                            built_output.read_bytes()
                        ).hexdigest(),
                        "size_bytes": built_output.stat().st_size,
                        "duration_ms": 60000,
                        "built_at_utc": "2026-07-21T12:30:00Z",
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    dataset_project = root / "dataset_builder" / "editorial_voice"
    dataset_project.mkdir(parents=True, exist_ok=True)
    dataset_state = {
        "description": (
            "A measured contralto with dry intelligence, stable identity, "
            "and a restrained dramatic range."
        ),
        "global_seed": 1842,
        "samples": [
            {
                "emotion": "Quiet authority",
                "text": "You may enter, but do not mistake permission for trust.",
                "seed": 1842,
                "status": "done",
                "audio_url": None,
            },
            {
                "emotion": "Contained alarm",
                "text": "The archive seal is moving, and no one is touching it.",
                "seed": 1842,
                "status": "pending",
                "audio_url": None,
            },
            {
                "emotion": "Dry amusement",
                "text": "That was almost a convincing explanation.",
                "seed": 1842,
                "status": "pending",
                "audio_url": None,
            },
            {
                "emotion": "Low grief",
                "text": "I kept the light on long after I knew you were not coming back.",
                "seed": 1842,
                "status": "pending",
                "audio_url": None,
            },
        ],
    }
    (dataset_project / "state.json").write_text(
        json.dumps(dataset_state, indent=2),
        encoding="utf-8",
    )
    for index in range(64):
        dense_project = root / "dataset_builder" / f"dense_voice_{index:02d}"
        dense_project.mkdir(parents=True, exist_ok=True)
        (dense_project / "state.json").write_text(
            json.dumps(
                {
                    "name": f"Dense Voice {index + 1:02d}",
                    "status": "ready" if index % 3 == 0 else "draft",
                    "samples": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    roster_log_path = root / "logs" / "stages" / "roster.json"
    roster_log_path.parent.mkdir(parents=True, exist_ok=True)
    roster_log_entries = [
        {
            "timestamp": f"2026-07-17T12:{index:02d}:00Z",
            "level": "progress",
            "message": (
                f"Passage {index + 1} of 24 validated."
                if index < 23
                else "Global reconciliation completed; roster draft is ready for review."
            ),
        }
        for index in range(24)
    ]
    roster_log_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stage": "roster",
                "entries": roster_log_entries,
                "updated_at": roster_log_entries[-1]["timestamp"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _launch_chrome(
    *,
    chrome: Path,
    url: str,
    profile: Path,
    probe: Path,
    output_dir: Path,
    mode: str = "full",
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
                "--output-dir",
                str(output_dir),
                "--mode",
                mode,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=240,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Interface Chrome audit failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("INTERFACE_CDP_AUDIT=")
        ]
        if len(lines) != 1:
            raise RuntimeError(
                "Interface audit emitted no unique report.\n"
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


def _write_new_project_audit_fixtures(output_dir: Path) -> None:
    epub = output_dir / "new-project-valid.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Browser Audit Book</dc:title>
    <dc:creator>Audit Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>
    <item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="one"/><itemref idref="two"/></spine>
</package>""",
        )
        archive.writestr("OEBPS/cover.png", b"\x89PNG\r\n\x1a\ninterface-audit-cover")
        archive.writestr("OEBPS/one.xhtml", "<html><body><p>First chapter.</p></body></html>")
        archive.writestr("OEBPS/two.xhtml", "<html><body><p>Second chapter.</p></body></html>")
    (output_dir / "new-project-valid-script.json").write_text(
        json.dumps(
            [
                {
                    "speaker": "NARRATOR",
                    "text": "Exact imported Script text.",
                    "instruct": "Measured narration.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "new-project-invalid.pdf").write_bytes(b"not a supported source")


def run(
    repo_root: Path,
    output_dir: Path,
    *,
    mode: str = "full",
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    chrome = next(
        (candidate for candidate in CHROME_CANDIDATES if candidate.exists()),
        None,
    )
    if chrome is None:
        return {
            "status": "SKIP",
            "reason": "No installed Chrome-family browser.",
        }

    output_dir = output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    _write_new_project_audit_fixtures(output_dir)

    with tempfile.TemporaryDirectory(
        prefix="alexandria-interface-audit-"
    ) as temporary:
        fixture_root = Path(temporary).resolve()
        _copy_fixture(repo_root, fixture_root)
        help_source = repo_root / "docs" / "help"
        if help_source.is_dir():
            shutil.copytree(
                help_source,
                fixture_root / "docs" / "help",
                dirs_exist_ok=True,
            )
        _seed_fixture(fixture_root)
        _augment_voice_library_fixture(fixture_root)
        browser_config = fixture_root / "browser-config.json"
        shutil.copy2(repo_root / "app" / "config.json", browser_config)
        runtime_before_start = (
            _runtime_path_snapshot(fixture_root, browser_config)
            if mode == "boundary13-final"
            else None
        )
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["ALEXANDRIA_DATA_ROOT"] = str(
            (fixture_root / "application-data").resolve()
        )
        environment["ALEXANDRIA_CONFIG_PATH"] = str(browser_config.resolve())
        port = _free_port()
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
            api_paths = (
                "api/model_registry/status",
                "api/projects",
                "api/library",
                "api/help",
                "api/more",
                "api/settings",
                "api/recovery/status",
                "api/migration/status",
            )
            api_before = (
                {path: _read_json_url(url + path) for path in api_paths}
                if mode == "boundary13-final"
                else None
            )
            runtime_before_browser = (
                _runtime_path_snapshot(fixture_root, browser_config)
                if mode == "boundary13-final"
                else None
            )
            payload = _launch_chrome(
                chrome=chrome,
                url=url,
                profile=fixture_root / "chrome-profile",
                probe=repo_root / "tests" / "interface_cdp_audit.js",
                output_dir=output_dir,
                mode=mode,
            )
            if mode == "boundary13-final":
                api_after = {path: _read_json_url(url + path) for path in api_paths}
                runtime_after_browser = _runtime_path_snapshot(
                    fixture_root,
                    browser_config,
                )
                payload["runtimePurity"] = {
                    "before_start": runtime_before_start,
                    "before_browser": runtime_before_browser,
                    "after_browser": runtime_after_browser,
                    "startup_and_read_unchanged": runtime_before_start == runtime_before_browser,
                    "browser_unchanged": runtime_before_browser == runtime_after_browser,
                    "api_before_sha256": {
                        path: _json_sha256(value)
                        for path, value in api_before.items()
                    },
                    "api_after_sha256": {
                        path: _json_sha256(value)
                        for path, value in api_after.items()
                    },
                    "api_unchanged": api_before == api_after,
                }
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

    if mode == "shell":
        failures: list[str] = []
        expectations = {
            "home-wide": {
                "destination": "projects",
                "rail": 224,
                "header": 88,
                "padding": 32,
                "project_navigation": True,
                "stage_tracker": False,
                "title": "Project Home",
                "action": "New Project",
            },
            "home-compact": {
                "destination": "projects",
                "rail": 184,
                "header": 88,
                "padding": 24,
                "project_navigation": True,
                "stage_tracker": False,
                "title": "Project Home",
                "action": "New Project",
            },
            "script-wide": {
                "destination": "script",
                "rail": 224,
                "header": 104,
                "padding": 32,
                "project_navigation": True,
                "stage_tracker": True,
                "title": "Script",
                "action": None,
            },
            "script-compact": {
                "destination": "script",
                "rail": 184,
                "header": 104,
                "padding": 24,
                "project_navigation": True,
                "stage_tracker": True,
                "title": "Script",
                "action": None,
            },
            "cast-wide": {
                "destination": "cast", "rail": 224, "header": 104, "padding": 32,
                "project_navigation": True, "stage_tracker": True, "title": "Cast",
                "action": "Continue to Produce",
            },
            "cast-compact": {
                "destination": "cast", "rail": 184, "header": 104, "padding": 24,
                "project_navigation": True, "stage_tracker": True, "title": "Cast",
                "action": "Continue to Produce",
            },
            "produce-wide": {
                "destination": "produce", "rail": 224, "header": 104, "padding": 32,
                "project_navigation": True, "stage_tracker": True, "title": "Produce",
                "action": "Generate missing and stale audio",
            },
            "produce-compact": {
                "destination": "produce", "rail": 184, "header": 104, "padding": 24,
                "project_navigation": True, "stage_tracker": True, "title": "Produce",
                "action": "Generate missing and stale audio",
            },
            "export-wide": {
                "destination": "export", "rail": 224, "header": 104, "padding": 32,
                "project_navigation": True, "stage_tracker": True, "title": "Export",
                "action": "Build Audiobook",
            },
            "export-compact": {
                "destination": "export", "rail": 184, "header": 104, "padding": 24,
                "project_navigation": True, "stage_tracker": True, "title": "Export",
                "action": "Build Audiobook",
            },
        }
        for name, expected in expectations.items():
            report = payload.get("canonicalShell", {}).get(name, {})
            if report.get("destination") != expected["destination"]:
                failures.append(f"canonical:{name}: wrong destination")
            for field, expected_value in (
                ("railRect", expected["rail"]),
                ("headerRect", expected["header"]),
            ):
                dimension = "width" if field == "railRect" else "height"
                value = float((report.get(field) or {}).get(dimension) or 0)
                if abs(value - expected_value) > 1:
                    failures.append(
                        f"canonical:{name}: {field} {value} != {expected_value}"
                    )
            padding = report.get("mainPadding") or {}
            for side in ("left", "right"):
                value = float(padding.get(side) or 0)
                if abs(value - expected["padding"]) > 1:
                    failures.append(
                        f"canonical:{name}: {side} padding {value} != {expected['padding']}"
                    )
            if report.get("projectNavigationVisible") is not expected["project_navigation"]:
                failures.append(f"canonical:{name}: wrong project navigation visibility")
            if report.get("stageTrackerVisible") is not expected["stage_tracker"]:
                failures.append(f"canonical:{name}: wrong stage tracker visibility")
            if report.get("pageTitle") != expected["title"]:
                failures.append(f"canonical:{name}: wrong page title")
            if expected["action"] and report.get("primaryAction") != expected["action"]:
                failures.append(f"canonical:{name}: wrong primary action")
            if report.get("horizontalOverflow"):
                failures.append(f"canonical:{name}: horizontal overflow")
            if report.get("duplicateFullTransportCount"):
                failures.append(f"canonical:{name}: duplicate full transport")
            if report.get("placeholderCopyVisible"):
                failures.append(f"canonical:{name}: placeholder copy visible")
            minimum_font = float(report.get("minimumShellFontSize") or 0)
            if minimum_font < 13:
                failures.append(
                    f"canonical:{name}: shell text below 13px ({minimum_font})"
                )

        for name, expected_position in (
            ("script-wide", "sticky"),
            ("script-compact", "fixed"),
        ):
            report = payload.get("canonicalShell", {}).get(name, {})
            script = report.get("scriptReview") or {}
            if script.get("workspaceVisible") is not True:
                failures.append(f"canonical:{name}: Script review workspace was not visible")
            if script.get("primaryActionLabel") != "Approve Script":
                failures.append(f"canonical:{name}: wrong Script primary action")
            if script.get("primaryActionDisabled") is not True:
                failures.append(f"canonical:{name}: approval was enabled with a blocking issue")
            if script.get("approvalReasonVisible") is not True:
                failures.append(f"canonical:{name}: disabled approval reason was not visible")
            if "Resolve 1 blocking issue" not in str(
                script.get("approvalReasonText") or ""
            ):
                failures.append(f"canonical:{name}: wrong disabled approval reason")
            if script.get("blockerSummaryVisible") is not True:
                failures.append(f"canonical:{name}: blocker summary was not visible")
            if script.get("blockerTitle") != "1 blocking issue remaining":
                failures.append(f"canonical:{name}: wrong blocker summary title")
            filter_labels = [
                str(value or "")
                for value in (script.get("filterLabels") or [])
            ]
            for expected_label in (
                "All 1",
                "Uncertain speaker 0",
                "Delivery direction 0",
                "Source mismatch 1",
            ):
                if expected_label not in filter_labels:
                    failures.append(
                        f"canonical:{name}: missing Script filter {expected_label!r}"
                    )
            if script.get("enabledIssueFilterCount") != 1:
                failures.append(f"canonical:{name}: wrong enabled issue-filter count")
            if abs(float(script.get("listBorderWidth") or 0) - 1) > 0.2:
                failures.append(f"canonical:{name}: Script list was not one bordered surface")
            if abs(float(script.get("listRowGap") or 0)) > 0.2:
                failures.append(f"canonical:{name}: Script rows retained card gaps")
            if script.get("rowShadowCount") != 0:
                failures.append(f"canonical:{name}: Script rows retained shadows")
            if script.get("selectedRowCount") not in {0, 1}:
                failures.append(f"canonical:{name}: multiple Script rows were selected")
            if script.get("selectedRowCount") == 1 and float(
                script.get("selectedRowOutlineWidth") or 0
            ) < 1.5:
                failures.append(f"canonical:{name}: selected Script row lacked teal outline")
            if script.get("issueNavigationVisible") is not True:
                failures.append(f"canonical:{name}: issue navigation was not visible")
            if script.get("noticeVisible") is not False:
                failures.append(f"canonical:{name}: duplicate review notice remained visible")
            if script.get("disclosureCount") != 2:
                failures.append(f"canonical:{name}: wrong Script disclosure count")
            if script.get("fullTransportCount") != 0:
                failures.append(f"canonical:{name}: full transport appeared inside Script")
            if script.get("inspectorVisible") is not True:
                failures.append(f"canonical:{name}: selected-issue inspector was not visible")
            inspector_width = float((script.get("inspectorRect") or {}).get("width") or 0)
            if abs(inspector_width - 360) > 1:
                failures.append(
                    f"canonical:{name}: inspector width {inspector_width} != 360"
                )
            if script.get("inspectorPosition") != expected_position:
                failures.append(
                    f"canonical:{name}: inspector position was not {expected_position}"
                )
            if script.get("inspectorIssueTypeVisible") is not True:
                failures.append(f"canonical:{name}: issue category was not visible")
            if script.get("inspectorSourceVisible") is not True:
                failures.append(f"canonical:{name}: source comparison was not visible")
            if script.get("contextualActionVisible") is not True:
                failures.append(f"canonical:{name}: contextual correction action was not visible")
            if script.get("contextualActionLabel") != "Replace mismatched Script":
                failures.append(f"canonical:{name}: wrong contextual correction action")

        for name, expected_detail_size in (
            ("cast-wide", (104, 120)),
            ("cast-compact", (88, 104)),
        ):
            report = payload.get("canonicalShell", {}).get(name, {})
            cast = report.get("castReview") or {}
            if cast.get("workspaceVisible") is not True:
                failures.append(f"canonical:{name}: Cast workspace was not visible")
            if cast.get("heading") != "Characters":
                failures.append(f"canonical:{name}: section heading was not Characters")
            if cast.get("listboxCount") != 1:
                failures.append(f"canonical:{name}: Cast did not have exactly one character list")
            row_count = int(cast.get("rowCount") or 0)
            if row_count < 3:
                failures.append(f"canonical:{name}: too few character rows rendered")
            if cast.get("selectedRowCount") != 1:
                failures.append(f"canonical:{name}: Cast did not have one selected row")
            if cast.get("selectedBadgeCount") != 0:
                failures.append(f"canonical:{name}: obsolete Selected badge remained")
            if cast.get("statusTreatmentCount") != row_count:
                failures.append(f"canonical:{name}: rows did not have one state treatment each")
            portrait_widths = [
                float(value)
                for value in (cast.get("portraitWidths") or [])
            ]
            portrait_heights = [
                float(value)
                for value in (cast.get("portraitHeights") or [])
            ]
            if len(portrait_widths) != 1 or abs(portrait_widths[0] - 48) > 1:
                failures.append(f"canonical:{name}: inconsistent character portrait widths")
            if len(portrait_heights) != 1 or abs(portrait_heights[0] - 48) > 1:
                failures.append(f"canonical:{name}: inconsistent character portrait heights")
            detail_rect = cast.get("detailPortraitRect") or {}
            detail_width = float(detail_rect.get("width") or 0)
            detail_height = float(detail_rect.get("height") or 0)
            if abs(detail_width - expected_detail_size[0]) > 1:
                failures.append(f"canonical:{name}: wrong selected portrait width")
            if abs(detail_height - expected_detail_size[1]) > 1:
                failures.append(f"canonical:{name}: wrong selected portrait height")
            if not cast.get("detailName"):
                failures.append(f"canonical:{name}: selected character name was missing")
            if not cast.get("detailState"):
                failures.append(f"canonical:{name}: selected character state was missing")
            if cast.get("voiceSavedState") != "Saved":
                failures.append(f"canonical:{name}: unchanged Voice state was not Saved")
            if cast.get("saveChangesVisible") is not False:
                failures.append(f"canonical:{name}: Save changes appeared in unchanged state")
            if cast.get("editVoiceVisible") is not True:
                failures.append(f"canonical:{name}: Voice edit action was not visible")
            section_tops = [
                float(value)
                for value in (cast.get("sectionOrderTop") or [])
            ]
            if cast.get("sectionCount") != 6 or section_tops != sorted(section_tops):
                failures.append(f"canonical:{name}: Voice-first section order drifted")
            if cast.get("fullTransportCount") != 0:
                failures.append(f"canonical:{name}: full transport appeared inside Cast")

        for name, expected_position in (
            ("produce-wide", "sticky"),
            ("produce-compact", "fixed"),
        ):
            report = payload.get("canonicalShell", {}).get(name, {})
            produce = report.get("produceReview") or {}
            if produce.get("workspaceVisible") is not True:
                failures.append(f"canonical:{name}: Produce workspace was not visible")
            expected_counts = {
                "all": 203,
                "ready": 12,
                "needs_listening": 7,
                "failed": 2,
                "stale": 4,
                "current": 178,
                "blocked": 0,
            }
            if produce.get("counts") != expected_counts:
                failures.append(f"canonical:{name}: mixed Produce counts drifted")
            if produce.get("reconciledCount") != 203:
                failures.append(f"canonical:{name}: Produce counts did not reconcile to 203")
            if int(produce.get("rowCount") or 0) < 7:
                failures.append(f"canonical:{name}: representative Produce rows were missing")
            if int(produce.get("chapterGroupCount") or 0) < 2:
                failures.append(f"canonical:{name}: Produce rows were not grouped by chapter or scene")
            if produce.get("selectedRowCount") != 1:
                failures.append(f"canonical:{name}: Produce did not retain one selected row")
            if produce.get("selectedRowState") != "Stale":
                failures.append(f"canonical:{name}: selected Produce row was not Stale")
            if produce.get("inspectorState") != "Stale":
                failures.append(f"canonical:{name}: selected inspector state disagreed with the row")
            if "changed after this audio was created" not in str(produce.get("inspectorReason") or ""):
                failures.append(f"canonical:{name}: stale reason did not explain the dependency change")
            if produce.get("internalChunkIdVisible") is not False:
                failures.append(f"canonical:{name}: raw chunk ID leaked into the normal inspector")
            if produce.get("inspectorPosition") != expected_position:
                failures.append(f"canonical:{name}: Produce inspector was not {expected_position}")
            if "btn-primary" in str(produce.get("regenerateButtonClass") or ""):
                failures.append(f"canonical:{name}: per-chunk regeneration competed with the page primary")
            if produce.get("filledPrimaryCount") != 0:
                failures.append(f"canonical:{name}: Produce workspace contained a second filled primary")
            if produce.get("regenerateAllInOverflow") is not True:
                failures.append(f"canonical:{name}: Regenerate all escaped the overflow menu")
            if produce.get("fullTransportCount") != 0:
                failures.append(f"canonical:{name}: full transport appeared inside Produce")

        required_export_labels = [
            "M4B audiobook",
            "MP3 audio file",
            "Audacity project package",
            "Separate chapter files",
        ]
        for name in ("export-wide", "export-compact"):
            report = payload.get("canonicalShell", {}).get(name, {})
            export = report.get("exportReview") or {}
            if export.get("workspaceVisible") is not True:
                failures.append(f"canonical:{name}: Export workspace was not visible")
            if export.get("workflowState") != "Ready to build":
                failures.append(f"canonical:{name}: readiness label was not Ready to build")
            if export.get("primaryActionLabel") != "Build Audiobook":
                failures.append(f"canonical:{name}: wrong Export primary action")
            if export.get("primaryActionDisabled") is not False:
                failures.append(f"canonical:{name}: ready Export primary action was disabled")
            if not export.get("saveState"):
                failures.append(f"canonical:{name}: project save state disappeared")
            if export.get("publicationTitle") != "The Shadows of Avalon":
                failures.append(f"canonical:{name}: publication title was not rendered")
            if export.get("publicationAuthor") != "Paul Cornell":
                failures.append(f"canonical:{name}: publication author was not rendered")
            if export.get("filename") != "cloned_audiobook.mp3":
                failures.append(f"canonical:{name}: MP3 filename behavior was not visible")
            if ".mp3" not in str(export.get("filenameBehavior") or ""):
                failures.append(f"canonical:{name}: MP3 extension behavior was not explained")
            if export.get("formatLabels") != required_export_labels:
                failures.append(f"canonical:{name}: canonical format labels drifted")
            if export.get("enabledFormatCount") != 3:
                failures.append(f"canonical:{name}: wrong supported-format count")
            if export.get("selectedFormat") != "mp3":
                failures.append(f"canonical:{name}: initial Export format was not MP3")
            if export.get("validationSummary") != "No blocking issues":
                failures.append(f"canonical:{name}: Final validation did not report a clear ready state")
            if export.get("validationRowCount") != 4:
                failures.append(f"canonical:{name}: Final validation row count drifted")
            if export.get("repeatedPassedCount") != 0:
                failures.append(f"canonical:{name}: validation repeated Passed text")
            if export.get("chapterRowCount") != 4:
                failures.append(f"canonical:{name}: chapter list did not render")
            if export.get("waveformDisabled") is not True:
                failures.append(f"canonical:{name}: pre-build waveform was incorrectly playable")
            if export.get("builtConfirmationVisible") is not False:
                failures.append(f"canonical:{name}: ready state falsely reported a built output")
            if export.get("technicalDetailsOpen") is not False:
                failures.append(f"canonical:{name}: technical details opened by default")
            if export.get("legacyResultVisible") is not False:
                failures.append(f"canonical:{name}: legacy Result workflow remained visible")
            if export.get("filledPrimaryCount") != 0:
                failures.append(f"canonical:{name}: Export workspace contained a second filled primary")
            if export.get("fullTransportCount") != 0:
                failures.append(f"canonical:{name}: full transport appeared inside Export")

        boundary12 = payload.get("boundary12Interactions") or {}
        built = boundary12.get("built") or {}
        build_request = built.get("request") or {}
        build_body = build_request.get("body") or {}
        if build_request.get("path") != "/api/export/build":
            failures.append("canonical:boundary12-build: canonical build route was not used")
        if build_body.get("formats") != ["mp3"]:
            failures.append("canonical:boundary12-build: selected format was not preserved")
        if build_body.get("chapter_mode") != "smart":
            failures.append("canonical:boundary12-build: chapter mode drifted")
        if build_body.get("plan_fingerprint") != "export-plan-mp3":
            failures.append("canonical:boundary12-build: reviewed plan fingerprint was not submitted")
        if build_body.get("dependency_fingerprint") != "export-dependency-mp3":
            failures.append("canonical:boundary12-build: reviewed dependency fingerprint was not submitted")
        metadata = build_body.get("metadata") or {}
        if metadata.get("title") != "The Shadows of Avalon" or metadata.get("author") != "Paul Cornell":
            failures.append("canonical:boundary12-build: visible publication metadata was not submitted")
        if built.get("workflowState") != "Built":
            failures.append("canonical:boundary12-build: terminal state was not Built")
        if built.get("primaryAction") != "Build Audiobook":
            failures.append("canonical:boundary12-build: sole build action drifted after completion")
        if built.get("builtConfirmationVisible") is not True:
            failures.append("canonical:boundary12-build: completion confirmation was not visible")
        if "cloned_audiobook.mp3" not in str(built.get("builtCopy") or ""):
            failures.append("canonical:boundary12-build: built filename was not confirmed")
        if built.get("waveformDisabled") is not False:
            failures.append("canonical:boundary12-build: built waveform was not enabled")
        if not str(built.get("playerSource") or "").startswith("data:audio/wav"):
            failures.append("canonical:boundary12-build: persistent player was not bound to built audio")
        if built.get("playerTitle") != "The Shadows of Avalon":
            failures.append("canonical:boundary12-build: persistent player title drifted")
        if built.get("playerContext") != "MP3 audio file":
            failures.append("canonical:boundary12-build: persistent player context drifted")
        if built.get("validationSummary") != "No blocking issues":
            failures.append("canonical:boundary12-build: built validation summary drifted")

        failed = boundary12.get("failed") or {}
        if failed.get("workflowState") != "Failed":
            failures.append("canonical:boundary12-failed: failed build did not report Failed")
        if failed.get("builtConfirmationVisible") is not False:
            failures.append("canonical:boundary12-failed: failed build falsely reported completion")
        failed_text = " ".join(str(value or "") for value in (failed.get("validationText") or []))
        if "Synthetic validation failure." not in failed_text:
            failures.append("canonical:boundary12-failed: exact failure was not visible")
        if "previous valid output was preserved" not in failed_text:
            failures.append("canonical:boundary12-failed: preserved-output guarantee was not visible")
        if failed.get("primaryAction") != "Build Audiobook":
            failures.append("canonical:boundary12-failed: build action was not available for retry")
        if str(failed.get("currentOutputStillVisible") or "") in {"", "—", "Calculated after build"}:
            failures.append("canonical:boundary12-failed: preserved current output disappeared")

        confirmation = boundary12.get("confirmation") or {}
        if confirmation.get("visible") is not True:
            failures.append("canonical:boundary12-regenerate-all: destructive confirmation was not visible")
        if confirmation.get("title") != "Confirm destructive action":
            failures.append("canonical:boundary12-regenerate-all: wrong confirmation title")
        if "Regenerate all audio?" not in str(confirmation.get("body") or ""):
            failures.append("canonical:boundary12-regenerate-all: consequence copy was missing")
        if confirmation.get("action") != "Regenerate":
            failures.append("canonical:boundary12-regenerate-all: wrong confirmation action")
        if "btn-danger" not in str(confirmation.get("actionClass") or ""):
            failures.append("canonical:boundary12-regenerate-all: destructive action was not danger-styled")
        regenerate = boundary12.get("regenerateAll") or {}
        regenerate_request = regenerate.get("request") or {}
        regenerate_body = regenerate_request.get("body") or {}
        if regenerate_request.get("path") != "/api/produce/generate":
            failures.append("canonical:boundary12-regenerate-all: canonical generation route was not used")
        if regenerate_body.get("mode") != "regenerate_all":
            failures.append("canonical:boundary12-regenerate-all: destructive mode was not preserved")
        if regenerate_body.get("confirm_regenerate_all") is not True:
            failures.append("canonical:boundary12-regenerate-all: explicit confirmation was not submitted")
        if regenerate_body.get("plan_fingerprint") != "produce-plan-regenerate_all":
            failures.append("canonical:boundary12-regenerate-all: reviewed plan fingerprint was not submitted")
        if regenerate_body.get("chunks_fingerprint") != "produce-chunks-browser":
            failures.append("canonical:boundary12-regenerate-all: reviewed chunk fingerprint was not submitted")
        if regenerate.get("confirmationHidden") is not True:
            failures.append("canonical:boundary12-regenerate-all: confirmation did not close after submission")

        cast_editor = payload.get("castVoiceEditor") or {}
        cast_before = cast_editor.get("before") or {}
        cast_after = cast_editor.get("after") or {}
        cast_saved = cast_editor.get("saved") or {}
        cast_reopened = cast_editor.get("reopened") or {}
        if cast_before.get("destination") != "cast":
            failures.append("canonical:cast-editor: wrong destination")
        if cast_before.get("canonicalCastVisible") is not True:
            failures.append("canonical:cast-editor: approved Cast surface disappeared")
        if cast_before.get("legacyCharacterWorkspaceVisible") is not False:
            failures.append("canonical:cast-editor: legacy Character workspace became visible")
        if cast_before.get("legacyModeClassPresent") is not False:
            failures.append("canonical:cast-editor: legacy mode class returned")
        if cast_before.get("editorVisible") is not True:
            failures.append("canonical:cast-editor: inline editor did not open")
        if not cast_before.get("editorVoiceName"):
            failures.append("canonical:cast-editor: no Voice card was mounted")
        if cast_before.get("editActionVisible") is not False:
            failures.append("canonical:cast-editor: duplicate edit action remained visible")
        if cast_before.get("saveActionVisible") is not False:
            failures.append("canonical:cast-editor: Save changes appeared before a change")
        if cast_after.get("canonicalCastVisible") is not True:
            failures.append("canonical:cast-editor-dirty: approved Cast surface disappeared")
        if cast_after.get("legacyCharacterWorkspaceVisible") is not False:
            failures.append("canonical:cast-editor-dirty: legacy Character workspace became visible")
        if cast_after.get("legacyModeClassPresent") is not False:
            failures.append("canonical:cast-editor-dirty: legacy mode class returned")
        if cast_after.get("editorVisible") is not True:
            failures.append("canonical:cast-editor-dirty: editor closed after a field change")
        if cast_after.get("saveActionVisible") is not True:
            failures.append("canonical:cast-editor-dirty: Save changes did not appear")
        if cast_after.get("saveActionLabel") != "Save changes":
            failures.append("canonical:cast-editor-dirty: wrong save label")
        if cast_after.get("savedState") != "Unsaved changes":
            failures.append("canonical:cast-editor-dirty: dirty state was not shown")
        if cast_after.get("horizontalOverflow"):
            failures.append("canonical:cast-editor-dirty: horizontal overflow")
        if cast_saved.get("responseStatus") != 200:
            failures.append("canonical:cast-editor-save: Voice readback failed")
        if cast_saved.get("editorHidden") is not True:
            failures.append("canonical:cast-editor-save: editor did not close")
        if cast_saved.get("saveActionHidden") is not True:
            failures.append("canonical:cast-editor-save: Save changes did not clear")
        if "Voice configuration saved" not in str(cast_saved.get("liveStatus") or ""):
            failures.append("canonical:cast-editor-save: success state was not shown")
        if cast_saved.get("savedVoiceCount", 0) < 2:
            failures.append("canonical:cast-editor-save: Voice configuration was truncated")
        if "browser-audit-change" not in str(cast_saved.get("savedValue") or ""):
            failures.append("canonical:cast-editor-save: edited value did not persist")
        if cast_saved.get("otherVoicePreserved") is not True:
            failures.append("canonical:cast-editor-save: unrelated Voice was not preserved")
        if cast_reopened.get("editorVisible") is not True:
            failures.append("canonical:cast-editor-reopen: editor did not reopen")
        if cast_reopened.get("editorVoiceName") != cast_before.get("editorVoiceName"):
            failures.append("canonical:cast-editor-reopen: wrong Voice reopened")
        if "browser-audit-change" not in str(cast_reopened.get("activeValue") or ""):
            failures.append("canonical:cast-editor-reopen: saved value reloaded stale")
        if cast_reopened.get("saveActionHidden") is not True:
            failures.append("canonical:cast-editor-reopen: dirty state leaked after save")
        if cast_reopened.get("savedState") != "Saved":
            failures.append("canonical:cast-editor-reopen: saved state was not restored")
        if cast_reopened.get("legacyCharacterWorkspaceVisible") is not False:
            failures.append("canonical:cast-editor-reopen: legacy workspace became visible")
        network_errors = payload.get("networkErrors") or []
        expected_script_audit_conflict = any(
            error.get("status") == 409
            and str(error.get("url") or "").endswith(
                "/api/script_lifecycle/accept"
            )
            for error in network_errors
        )
        unexpected_network_errors = [
            error
            for error in network_errors
            if not (
                error.get("status") == 409
                and str(error.get("url") or "").endswith(
                    "/api/script_lifecycle/accept"
                )
            )
        ]
        console_errors = list(payload.get("consoleErrors") or [])
        if expected_script_audit_conflict:
            expected_console = (
                "Failed to load resource: the server responded with a status "
                "of 409 (Conflict)"
            )
            console_errors = [
                error
                for error in console_errors
                if error != expected_console
            ]
        if unexpected_network_errors:
            failures.append("network errors")
        if console_errors:
            failures.append("console errors")
        if payload.get("runtimeErrors"):
            failures.append("runtime errors")
        return {
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "output_dir": str(output_dir),
            "audit": payload,
        }

    if mode == "boundary12":
        failures: list[str] = []
        if not payload.get("boundary12Interactions"):
            failures.append("Boundary 12 interaction evidence was not produced")
        if payload.get("networkErrors"):
            failures.append("network errors")
        if payload.get("consoleErrors"):
            failures.append("console errors")
        if payload.get("runtimeErrors"):
            failures.append("runtime errors")
        return {
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "output_dir": str(output_dir),
            "audit": payload,
        }

    if mode == "boundary13":
        failures: list[str] = []
        expectations = {
            "library-wide": ("library", "Library", 224, 32),
            "library-compact": ("library", "Library", 184, 24),
            "voices-wide": ("voices", "Voices", 224, 32),
            "voices-compact": ("voices", "Voices", 184, 24),
            "templates-wide": ("templates", "Templates", 224, 32),
            "settings-wide": ("settings", "Settings", 224, 32),
            "settings-compact": ("settings", "Settings", 184, 24),
            "maintenance-wide": ("more", "Maintenance", 224, 32),
            "maintenance-compact": ("more", "Maintenance", 184, 24),
            "more-wide": ("more", "More", 224, 32),
            "more-compact": ("more", "More", 184, 24),
            "help-wide": ("more", "Help Center", 224, 32),
            "help-compact": ("more", "Help Center", 184, 24),
        }
        for name, (destination, title, rail, padding) in expectations.items():
            report = payload.get("canonicalShell", {}).get(name, {})
            supporting = report.get("supportingReview") or {}
            if report.get("destination") != destination:
                failures.append(f"canonical:{name}: wrong destination")
            if report.get("pageTitle") != title:
                failures.append(f"canonical:{name}: wrong title")
            if round((report.get("railRect") or {}).get("width", 0)) != rail:
                failures.append(f"canonical:{name}: wrong rail width")
            if round((report.get("mainPadding") or {}).get("left", 0)) != padding:
                failures.append(f"canonical:{name}: wrong content padding")
            if report.get("horizontalOverflow"):
                failures.append(f"canonical:{name}: horizontal overflow")
            if report.get("stageTrackerVisible"):
                failures.append(f"canonical:{name}: project tracker leaked into global destination")
            if report.get("projectNavigationVisible"):
                failures.append(f"canonical:{name}: project navigation leaked into global destination")
            if supporting.get("legacyWorkflowVisible"):
                failures.append(f"canonical:{name}: legacy workflow remained visible")

        for name in ("library-wide", "library-compact"):
            supporting = payload["canonicalShell"][name].get("supportingReview") or {}
            if supporting.get("libraryWorkspaceVisible") is not True or supporting.get("inventoryViewVisible") is not True:
                failures.append(f"canonical:{name}: Library inventory was not visible")
            if int(supporting.get("libraryRowCount") or 0) < 1:
                failures.append(f"canonical:{name}: Library rows were missing")
            if supporting.get("librarySelectedRowCount") != 1:
                failures.append(f"canonical:{name}: Library did not retain one selection")
            if not supporting.get("libraryDetailTitle"):
                failures.append(f"canonical:{name}: Library detail was not rendered")
            if supporting.get("libraryTechnicalDetailsOpen") is not False:
                failures.append(f"canonical:{name}: technical details opened by default")
            if supporting.get("visibleRawFingerprint") is not False:
                failures.append(f"canonical:{name}: raw fingerprint leaked into normal detail")
            if int(supporting.get("deleteButtonCount") or 0) != 1:
                failures.append(f"canonical:{name}: guarded delete decision was not visible")

        library_count = int((payload["canonicalShell"]["library-wide"].get("supportingReview") or {}).get("libraryRowCount") or 0)
        if library_count < 64:
            failures.append("canonical:library-wide: dense inventory fixture was not rendered")
        for name in ("voices-wide", "voices-compact"):
            supporting = payload["canonicalShell"][name].get("supportingReview") or {}
            voice_count = int(supporting.get("libraryRowCount") or 0)
            if supporting.get("inventoryViewVisible") is not True or voice_count < 1:
                failures.append(f"canonical:{name}: Voice inventory was not visible")
            if voice_count > library_count:
                failures.append(f"canonical:{name}: Voices was not a filtered Library view")
            if supporting.get("primaryAction") != "Create Voice":
                failures.append(f"canonical:{name}: wrong Voice primary action")
            if supporting.get("visibleRawFingerprint") is not False:
                failures.append(f"canonical:{name}: raw fingerprint leaked into Voices")

        templates = payload["canonicalShell"]["templates-wide"].get("supportingReview") or {}
        if templates.get("templatesWorkspaceVisible") is not True or int(templates.get("templateCount") or 0) < 6:
            failures.append("canonical:templates-wide: template inventory was not rendered")
        if int(templates.get("templateSelectedCount") or 0) != 1:
            failures.append("canonical:templates-wide: template selection state was invalid")
        if not templates.get("templateDetailTitle"):
            failures.append("canonical:templates-wide: template detail was empty")
        if templates.get("templateRawFingerprintVisible") is not False:
            failures.append("canonical:templates-wide: raw fingerprint leaked into normal UI")
        if templates.get("inventoryViewVisible") is not False:
            failures.append("canonical:templates-wide: Library inventory remained visible")
        if templates.get("primaryAction") != "New Template":
            failures.append("canonical:templates-wide: wrong Templates primary action")

        for name in ("settings-wide", "settings-compact"):
            settings_review = payload["canonicalShell"][name].get("settingsReview") or {}
            if settings_review.get("workspaceVisible") is not True or settings_review.get("formVisible") is not True:
                failures.append(f"canonical:{name}: canonical Settings form was not visible")
            if settings_review.get("legacyVisible") is not False or settings_review.get("recoveryVisible") is not False:
                failures.append(f"canonical:{name}: Maintenance controls leaked into Settings")
            if int(settings_review.get("sectionCount") or 0) < 6:
                failures.append(f"canonical:{name}: Settings sections were incomplete")
            if settings_review.get("saveButtonCount") != 1:
                failures.append(f"canonical:{name}: Settings save action count was wrong")
            if settings_review.get("summaryVisible") is not True:
                failures.append(f"canonical:{name}: Settings summary was not visible")
            if not settings_review.get("defaultTemplate"):
                failures.append(f"canonical:{name}: default template was not shown")
            if settings_review.get("apiKeyValue"):
                failures.append(f"canonical:{name}: API key value leaked into the form")
            if "not displayed" not in str(settings_review.get("apiKeyState") or ""):
                failures.append(f"canonical:{name}: API key redaction state was unclear")
            if settings_review.get("structuredOutputDisabled") is not True:
                failures.append(f"canonical:{name}: structured-output contract was editable")
            if int(settings_review.get("advancedActionCount") or 0) != 4:
                failures.append(f"canonical:{name}: advanced destination count was wrong")
            if any(
                settings_review.get(key) is True
                for key in (
                    "promptEditorVisible",
                    "modelCacheVisible",
                    "runtimeVisible",
                    "repairControlVisible",
                )
            ):
                failures.append(f"canonical:{name}: diagnostics or repair leaked into normal Settings")
            if settings_review.get("rawFingerprintVisible") is not False:
                failures.append(f"canonical:{name}: raw fingerprint leaked into Settings")
            if "separately" not in str(settings_review.get("storageTruth") or ""):
                failures.append(f"canonical:{name}: retention enforcement truth was missing")

        for name in ("maintenance-wide", "maintenance-compact"):
            maintenance = payload["canonicalShell"][name].get("maintenanceReview") or {}
            if maintenance.get("workspaceVisible") is not True:
                failures.append(f"canonical:{name}: canonical Maintenance was not visible")
            if maintenance.get("settingsWorkspaceVisible") is not False:
                failures.append(f"canonical:{name}: Settings remained visible in Maintenance")
            if maintenance.get("legacyVisible") is not False or maintenance.get("legacyRecoveryVisible") is not False:
                failures.append(f"canonical:{name}: legacy Maintenance surfaces leaked into the canonical page")
            if maintenance.get("summaryVisible") is not True:
                failures.append(f"canonical:{name}: Maintenance summary was not visible")
            if int(maintenance.get("healthRowCount") or 0) < 2:
                failures.append(f"canonical:{name}: recovery and health rows were incomplete")
            if int(maintenance.get("modelRowCount") or 0) < 1:
                failures.append(f"canonical:{name}: model diagnostics were missing")
            if int(maintenance.get("projectRowCount") or 0) < 1:
                failures.append(f"canonical:{name}: project inventory was missing")
            if int(maintenance.get("impactButtonCount") or 0) < 1:
                failures.append(f"canonical:{name}: guarded impact review was unavailable")
            if maintenance.get("rawAbsolutePathVisible") is not False:
                failures.append(f"canonical:{name}: raw absolute path leaked into Maintenance")
            if maintenance.get("rawFingerprintVisible") is not False:
                failures.append(f"canonical:{name}: raw fingerprint leaked into Maintenance")
            if maintenance.get("rawSnapshotLabelVisible") is not False:
                failures.append(f"canonical:{name}: raw cache internals leaked into Maintenance")
            if maintenance.get("dialogVisible") is not False:
                failures.append(f"canonical:{name}: guarded dialog opened without user action")
            if int(maintenance.get("primaryFilledCount") or 0) != 0:
                failures.append(f"canonical:{name}: Maintenance exposed a competing filled primary action")
            if int(maintenance.get("destructiveButtonVisibleWithoutDialog") or 0) != 0:
                failures.append(f"canonical:{name}: destructive action was visible before impact review")
            expected_columns = 5 if name.endswith("wide") else 2
            if int(maintenance.get("summaryColumnCount") or 0) != expected_columns:
                failures.append(f"canonical:{name}: summary geometry did not match the viewport")

        for name in ("more-wide", "more-compact"):
            more = payload["canonicalShell"][name].get("supportingReview") or {}
            if more.get("moreWorkspaceVisible") is not True or int(more.get("moreToolCount") or 0) < 8:
                failures.append(f"canonical:{name}: supporting tool landing was incomplete")
            if more.get("helpWorkspaceVisible"):
                failures.append(f"canonical:{name}: Help Center leaked into More landing")

        for name in ("help-wide", "help-compact"): 
            help_review = payload["canonicalShell"][name].get("supportingReview") or {}
            if help_review.get("helpWorkspaceVisible") is not True:
                failures.append(f"canonical:{name}: Help Center was not visible")
            if int(help_review.get("helpTopicCount") or 0) < 8:
                failures.append(f"canonical:{name}: bundled topics were missing")
            if not help_review.get("helpDetailTitle"):
                failures.append(f"canonical:{name}: selected topic was not rendered")
            if help_review.get("helpScriptElementCount") != 0:
                failures.append(f"canonical:{name}: Help Center created script elements")
            if help_review.get("moreWorkspaceVisible"):
                failures.append(f"canonical:{name}: More landing remained visible")

        interactions = payload.get("boundary13Interactions") or {}
        create_voice = interactions.get("createVoice") or {}
        if create_voice.get("destination") != "more" or create_voice.get("tool") != "voice-designer":
            failures.append("canonical:boundary13-create-voice: Voice primary did not open the native tool")
        if create_voice.get("returnRoute") != "#/voices":
            failures.append("canonical:boundary13-create-voice: Voice return route was not preserved")
        if create_voice.get("pageTitle") != "Voice designer":
            failures.append("canonical:boundary13-create-voice: specialist header title drifted")

        template_crud = interactions.get("templateCrud") or {}
        if int(template_crud.get("initialRowCount") or 0) < 6:
            failures.append("canonical:boundary13-template: built-in template inventory was incomplete")
        if not template_crud.get("createdTemplateId") or not str(
            template_crud.get("createdTemplateId")
        ).startswith("template_"):
            failures.append("canonical:boundary13-template: custom create did not return a stable template ID")
        if not template_crud.get("defaultCopyId") or not str(
            template_crud.get("defaultCopyId")
        ).startswith("template_"):
            failures.append("canonical:boundary13-template: duplicate/default transaction did not complete")
        if template_crud.get("search") != "Copy" or template_crud.get("scope") != "custom":
            failures.append("canonical:boundary13-template: URL-backed search/scope state drifted")
        if int(template_crud.get("rowCount") or 0) != 1:
            failures.append("canonical:boundary13-template: filtered custom result count was wrong")
        if template_crud.get("deletedOriginal") is not True:
            failures.append("canonical:boundary13-template: guarded delete did not remove the selected custom template")
        if template_crud.get("hiddenInternalLabels"):
            failures.append("canonical:boundary13-template: runtime internals leaked into normal template fields")

        template_applied = interactions.get("templateApplied") or {}
        if template_applied.get("destination") != "projects":
            failures.append("canonical:boundary13-template: Use Template did not return to Project Home")
        if template_applied.get("modalVisible") is not True:
            failures.append("canonical:boundary13-template: New Project did not open")
        if template_applied.get("templateName") != "QA Swedish Publication Copy":
            failures.append("canonical:boundary13-template: template identity was not shown in New Project")
        if template_applied.get("method") != "local" or template_applied.get("preset") != "standard":
            failures.append("canonical:boundary13-template: method/preset were not applied")
        if template_applied.get("sourceLanguage") != "English" or template_applied.get("outputLanguage") != "Swedish":
            failures.append("canonical:boundary13-template: languages were not applied")
        if template_applied.get("advancedOpen") is not False:
            failures.append("canonical:boundary13-template: non-Custom template opened Advanced options")

        template_restored = interactions.get("templateRestored") or {}
        if template_restored.get("destination") != "templates":
            failures.append("canonical:boundary13-template: Back did not restore Templates")
        if template_restored.get("search") != "Copy" or template_restored.get("scope") != "custom":
            failures.append("canonical:boundary13-template: Back did not restore filter state")
        if int(template_restored.get("rowCount") or 0) != 1:
            failures.append("canonical:boundary13-template: restored result set was wrong")

        help_interaction = interactions.get("help") or {}
        if help_interaction.get("destination") != "more" or help_interaction.get("tool") != "help-center":
            failures.append("canonical:boundary13-help: contextual Help route drifted")
        if help_interaction.get("helpContext") != "cast":
            failures.append("canonical:boundary13-help: stable Cast context ID was not preserved")
        if help_interaction.get("source") != "library_fixture_source":
            failures.append("canonical:boundary13-help: original source context was overwritten")
        if help_interaction.get("issue") != "issue_help_context" or help_interaction.get("mode") != "review":
            failures.append("canonical:boundary13-help: issue or mode context was lost")
        if help_interaction.get("returnRoute") != help_interaction.get("origin"):
            failures.append("canonical:boundary13-help: exact originating route was not preserved")
        if help_interaction.get("detailTitle") != "Assign and verify Voices":
            failures.append("canonical:boundary13-help: context did not resolve to the Cast topic")
        if int(help_interaction.get("topicCount") or 0) != 9:
            failures.append("canonical:boundary13-help: manifest topic inventory was incomplete")
        if help_interaction.get("helpButtonsVisible") is not False:
            failures.append("canonical:boundary13-help: contextual ? control remained visible inside Help")

        body_search = help_interaction.get("bodySearch") or {}
        if body_search.get("search") != "post-migration file hash" or body_search.get("routeSearch") != "post-migration file hash":
            failures.append("canonical:boundary13-help: full-content search was not URL-backed")
        if body_search.get("rowCount") != 1 or body_search.get("selectedSlug") != "maintenance":
            failures.append("canonical:boundary13-help: body-only search did not isolate Maintenance")
        if body_search.get("resultCount") != "1 of 9":
            failures.append("canonical:boundary13-help: result count lost full bundle size")
        if body_search.get("detailTitle") != "Maintenance and recovery":
            failures.append("canonical:boundary13-help: body search loaded the wrong topic")
        if body_search.get("horizontalOverflow") is not False:
            failures.append("canonical:boundary13-help: compact long-content search overflowed horizontally")
        viewport = body_search.get("viewport") or {}
        if viewport.get("width") != 1024 or viewport.get("height") != 768:
            failures.append("canonical:boundary13-help: compact search evidence used the wrong viewport")
        if int(body_search.get("pageScrollHeight") or 0) <= int(body_search.get("pageClientHeight") or 0):
            failures.append("canonical:boundary13-help: compact long topic did not require document scrolling")
        if int(body_search.get("screenshotBytes") or 0) <= 0:
            failures.append("canonical:boundary13-help: compact search screenshot was empty")
        if int(help_interaction.get("screenshotBytes") or 0) <= 0:
            failures.append("canonical:boundary13-help: contextual Help screenshot was empty")

        keyboard_end = help_interaction.get("keyboardEnd") or {}
        if keyboard_end.get("selectedSlug") != "model-cache" or keyboard_end.get("focusedSlug") != "model-cache":
            failures.append("canonical:boundary13-help: End did not select and focus the last topic")
        if keyboard_end.get("routeTopic") != "model-cache" or keyboard_end.get("detailTitle") != "Local model cache":
            failures.append("canonical:boundary13-help: End selection route/detail drifted")
        keyboard_home = help_interaction.get("keyboardHome") or {}
        if keyboard_home.get("selectedSlug") != "project-home" or keyboard_home.get("focusedSlug") != "project-home":
            failures.append("canonical:boundary13-help: Home did not select and focus the first topic")
        if keyboard_home.get("routeTopic") != "project-home" or keyboard_home.get("detailTitle") != "Project Home and New Project":
            failures.append("canonical:boundary13-help: Home selection route/detail drifted")

        related = help_interaction.get("related") or {}
        if related.get("selectedSlug") != "produce" or related.get("routeTopic") != "produce":
            failures.append("canonical:boundary13-help: related-topic navigation did not update selection and route")
        if related.get("detailTitle") != "Generate and review production audio":
            failures.append("canonical:boundary13-help: related-topic detail drifted")

        workflow_route = help_interaction.get("workflowRoute") or {}
        if workflow_route.get("destination") != "voices":
            failures.append("canonical:boundary13-help: workflow link did not open Voices")
        if workflow_route.get("returnRoute") != help_interaction.get("helpRouteBeforeWorkflow"):
            failures.append("canonical:boundary13-help: workflow link lost exact Help return state")
        for key, expected in (
            ("project", "help-browser-project"),
            ("character", help_interaction.get("character")),
            ("source", "library_fixture_source"),
            ("issue", "issue_help_context"),
            ("mode", "review"),
        ):
            if workflow_route.get(key) != expected:
                failures.append(f"canonical:boundary13-help: workflow link lost {key}")
        for key in ("help", "topic", "search"):
            if workflow_route.get(key):
                failures.append(f"canonical:boundary13-help: workflow link leaked Help-specific {key}")

        help_back = help_interaction.get("back") or {}
        if help_back.get("hash") != help_interaction.get("helpRouteBeforeWorkflow") or help_back.get("locationHash") != help_interaction.get("helpRouteBeforeWorkflow"):
            failures.append("canonical:boundary13-help: Back did not restore exact Help state")
        if help_back.get("detailTitle") != "Assign and verify Voices":
            failures.append("canonical:boundary13-help: Back restored the wrong topic")
        returned = help_interaction.get("returned") or {}
        if returned.get("hash") != help_interaction.get("origin") or returned.get("locationHash") != help_interaction.get("origin"):
            failures.append("canonical:boundary13-help: Return did not restore the originating route")
        if returned.get("destination") != "cast":
            failures.append("canonical:boundary13-help: Return did not open Cast")
        for key, expected in (
            ("project", "help-browser-project"),
            ("character", help_interaction.get("character")),
            ("source", "library_fixture_source"),
            ("issue", "issue_help_context"),
            ("mode", "review"),
        ):
            if returned.get(key) != expected:
                failures.append(f"canonical:boundary13-help: Return lost {key}")
        if "Cast" not in (help_interaction.get("workflowActionLabels") or []) or "Voices" not in (help_interaction.get("workflowActionLabels") or []):
            failures.append("canonical:boundary13-help: current workflow labels were missing")
        if int(help_interaction.get("relatedCount") or 0) < 3:
            failures.append("canonical:boundary13-help: related-topic inventory was incomplete")
        if help_interaction.get("scriptElementCount") != 0:
            failures.append("canonical:boundary13-help: rendered topic created executable elements")

        native_tool = interactions.get("nativeTool") or {}
        if native_tool.get("destination") != "more" or native_tool.get("tool") != "voice-designer":
            failures.append("canonical:boundary13-library: native artifact route was not opened")
        if not str(native_tool.get("returnRoute") or "").startswith("#/library"):
            failures.append("canonical:boundary13-library: Library return route was not preserved")

        filtered_library = interactions.get("filteredLibrary") or {}
        if filtered_library.get("search") != "book" or filtered_library.get("kind") != "source_book":
            failures.append("canonical:boundary13-library: search and type filter were not applied")
        if filtered_library.get("rowCount") != 1 or filtered_library.get("rowKind") != "source_book":
            failures.append("canonical:boundary13-library: source-book filter did not isolate one row")
        filtered_hash = str(filtered_library.get("hash") or "")
        if "search=book" not in filtered_hash or "filter=" not in filtered_hash:
            failures.append("canonical:boundary13-library: filter state was not encoded in the route")

        restored_library = interactions.get("restoredLibrary") or {}
        if restored_library.get("search") != "book" or restored_library.get("kind") != "source_book":
            failures.append("canonical:boundary13-library: Back navigation did not restore filters")
        if restored_library.get("rowCount") != 1:
            failures.append("canonical:boundary13-library: restored route did not restore results")
        if restored_library.get("hash") != filtered_library.get("hash"):
            failures.append("canonical:boundary13-library: restored route hash drifted")

        workflow_routes = interactions.get("workflowRoutes") or {}
        for key, destination in (
            ("sourceBook", "script"),
            ("productionAudio", "produce"),
            ("exportOutput", "export"),
        ):
            route = workflow_routes.get(key) or {}
            if route.get("destination") != destination:
                failures.append(
                    f"canonical:boundary13-library: {key} did not open {destination}"
                )
            if not route.get("source"):
                failures.append(
                    f"canonical:boundary13-library: {key} lost artifact source context"
                )
            if not str(route.get("returnRoute") or "").startswith("#/library"):
                failures.append(
                    f"canonical:boundary13-library: {key} lost its Library return route"
                )

        voice_library = interactions.get("voiceLibrary") or {}
        methods = set(voice_library.get("methods") or [])
        for method in (
            "built_in",
            "supplied_recording",
            "instruction_controlled",
            "alias",
        ):
            if method not in methods:
                failures.append(
                    f"canonical:boundary13-voices: missing {method} method row"
                )
        if int(voice_library.get("rowCount") or 0) < 10:
            failures.append("canonical:boundary13-voices: reusable Voice inventory is unexpectedly sparse")
        if voice_library.get("searchPlaceholder") != "Search Voices…":
            failures.append("canonical:boundary13-voices: shared search did not adapt to Voices")
        if voice_library.get("listHeading") != "Reusable Voices":
            failures.append("canonical:boundary13-voices: shared list heading did not adapt")

        controlled_voice = interactions.get("controlledVoice") or {}
        controlled_text = str(controlled_voice.get("text") or "")
        for phrase in ("Production", "Not approved", "Preview", "Line instruction", "Channel present"):
            if phrase not in controlled_text:
                failures.append(
                    f"canonical:boundary13-voices: controlled Voice omitted {phrase!r} truth"
                )
        if not controlled_voice.get("hasCastAction"):
            failures.append("canonical:boundary13-voices: controlled Voice lacks Cast entry")

        supplied_voice = interactions.get("suppliedVoice") or {}
        supplied_text = str(supplied_voice.get("text") or "")
        if "Line instruction" not in supplied_text or "Not supported" not in supplied_text:
            failures.append("canonical:boundary13-voices: standard clone did not disclose instruction-inert behavior")
        if supplied_voice.get("playerHidden"):
            failures.append("canonical:boundary13-voices: Voice preview did not activate the persistent player")
        if not supplied_voice.get("audioSource"):
            failures.append("canonical:boundary13-voices: Voice preview did not load an audio source")
        if int(supplied_voice.get("usageCount") or 0) < 1:
            failures.append("canonical:boundary13-voices: assigned Voice did not expose Cast usage")

        voice_cast_route = interactions.get("voiceCastRoute") or {}
        if voice_cast_route.get("destination") != "cast":
            failures.append("canonical:boundary13-voices: usage link did not open Cast")
        if not voice_cast_route.get("character"):
            failures.append("canonical:boundary13-voices: usage link lost character context")
        if voice_cast_route.get("returnRoute") != interactions.get("voiceReturnRoute"):
            failures.append("canonical:boundary13-voices: Cast return route drifted")
        restored_voices = interactions.get("restoredVoices") or {}
        if restored_voices.get("destination") != "voices":
            failures.append("canonical:boundary13-voices: Back did not restore Voices")
        if int(restored_voices.get("rowCount") or 0) < 10:
            failures.append("canonical:boundary13-voices: restored Voice inventory was not rendered")

        more_contextual = interactions.get("moreContextual") or {}
        if more_contextual.get("destination") != "more" or more_contextual.get("tool"):
            failures.append("canonical:boundary13-more: contextual route did not open the More landing")
        more_project = more_contextual.get("project")
        more_character = more_contextual.get("character")
        more_source = more_contextual.get("source")
        more_return = more_contextual.get("returnRoute")
        if not more_project or not more_character:
            failures.append("canonical:boundary13-more: project or character context was missing")
        if more_source != f"cast:character:{more_character}":
            failures.append("canonical:boundary13-more: source context drifted")
        if not str(more_return or "").startswith("#/cast?"):
            failures.append("canonical:boundary13-more: exact Cast return route was missing")
        if more_contextual.get("search") != "voice" or more_contextual.get("searchValue") != "voice":
            failures.append("canonical:boundary13-more: URL-backed search was not applied")
        if more_contextual.get("bannerLabel") != "Selected character":
            failures.append("canonical:boundary13-more: selected-character context was not announced")
        if more_contextual.get("returnLabel") != "Return to character":
            failures.append("canonical:boundary13-more: contextual return action was ambiguous")
        if int(more_contextual.get("visibleToolCount") or 0) < 2:
            failures.append("canonical:boundary13-more: contextual search results were incomplete")
        if more_contextual.get("hash") != more_contextual.get("locationHash"):
            failures.append("canonical:boundary13-more: contextual route and browser hash diverged")

        more_tool_opened = interactions.get("moreToolOpened") or {}
        if more_tool_opened.get("destination") != "more" or more_tool_opened.get("tool") != "voice-designer":
            failures.append("canonical:boundary13-more: Voice designer did not open semantically")
        if more_tool_opened.get("pageTitle") != "Voice designer":
            failures.append("canonical:boundary13-more: specialist header title drifted")
        for key, expected in (
            ("project", more_project),
            ("character", more_character),
            ("source", more_source),
            ("returnRoute", more_return),
        ):
            if more_tool_opened.get(key) != expected:
                failures.append(f"canonical:boundary13-more: tool route lost {key}")
        if more_tool_opened.get("hash") != more_tool_opened.get("locationHash"):
            failures.append("canonical:boundary13-more: tool route and browser hash diverged")
        if int(more_tool_opened.get("historyLength") or 0) != int(more_contextual.get("historyLength") or 0) + 1:
            failures.append("canonical:boundary13-more: specialist navigation did not push one history entry")

        more_back = interactions.get("moreBackRestored") or {}
        if more_back.get("destination") != "more" or more_back.get("tool"):
            failures.append("canonical:boundary13-more: Back did not restore the More landing")
        if more_back.get("workspaceVisible") is not True:
            failures.append("canonical:boundary13-more: Back restored a hidden landing")
        if more_back.get("search") != "voice" or more_back.get("searchValue") != "voice":
            failures.append("canonical:boundary13-more: Back lost URL-backed search")
        for key, expected in (
            ("project", more_project),
            ("character", more_character),
            ("source", more_source),
            ("returnRoute", more_return),
        ):
            if more_back.get(key) != expected:
                failures.append(f"canonical:boundary13-more: Back lost {key}")
        if more_back.get("hash") != more_back.get("locationHash"):
            failures.append("canonical:boundary13-more: Back route and browser hash diverged")
        if more_back.get("hash") != more_contextual.get("hash"):
            failures.append("canonical:boundary13-more: Back restored a different More route")
        if more_back.get("historyLength") != more_tool_opened.get("historyLength"):
            failures.append("canonical:boundary13-more: Back mutated browser history length")

        more_forward = interactions.get("moreForwardRestored") or {}
        if more_forward.get("destination") != "more" or more_forward.get("tool") != "voice-designer":
            failures.append("canonical:boundary13-more: Forward did not restore Voice designer")
        if more_forward.get("pageTitle") != "Voice designer":
            failures.append("canonical:boundary13-more: Forward restored the wrong header")
        for key, expected in (
            ("project", more_project),
            ("character", more_character),
            ("source", more_source),
            ("returnRoute", more_return),
        ):
            if more_forward.get(key) != expected:
                failures.append(f"canonical:boundary13-more: Forward lost {key}")
        if more_forward.get("hash") != more_forward.get("locationHash"):
            failures.append("canonical:boundary13-more: Forward route and browser hash diverged")
        if more_forward.get("hash") != more_tool_opened.get("hash"):
            failures.append("canonical:boundary13-more: Forward restored a different specialist route")
        if more_forward.get("historyLength") != more_tool_opened.get("historyLength"):
            failures.append("canonical:boundary13-more: Forward mutated browser history length")

        more_returned = interactions.get("moreReturned") or {}
        if more_returned.get("destination") != "cast":
            failures.append("canonical:boundary13-more: return action did not open Cast")
        if more_returned.get("project") != more_project or more_returned.get("character") != more_character:
            failures.append("canonical:boundary13-more: return action lost project or character")
        if more_returned.get("filter") != "needs_attention":
            failures.append("canonical:boundary13-more: return action did not restore exact route state")

        advanced_wide = interactions.get("advancedWide") or {}
        advanced_expected = {
            "destination": "more",
            "tool": "advanced-character-operations",
            "mode": "identity",
            "project": more_project,
            "character": more_character,
            "source": more_source,
            "returnRoute": more_return,
            "pageTitle": "Advanced identity operations",
            "kicker": "More · Character identity",
            "contextVisible": True,
            "returnLabel": "Return to character",
            "selectedId": more_character,
            "assignmentMutationControlCount": 0,
            "referenceAssignmentActionCount": 0,
            "horizontalOverflow": False,
        }
        for key, expected in advanced_expected.items():
            if advanced_wide.get(key) != expected:
                failures.append(
                    f"canonical:boundary13-specialists: advanced {key}={advanced_wide.get(key)!r} != {expected!r}"
                )
        if advanced_wide.get("hash") != advanced_wide.get("locationHash"):
            failures.append("canonical:boundary13-specialists: advanced route and browser hash diverged")
        if not advanced_wide.get("contextName") or advanced_wide.get("contextName") == "Selected character":
            failures.append("canonical:boundary13-specialists: advanced route did not hydrate the character name")
        if "Script label:" not in str(advanced_wide.get("contextMeta") or ""):
            failures.append("canonical:boundary13-specialists: advanced route omitted the Script label")
        if int(advanced_wide.get("statusEntryCount") or 0) < 1:
            failures.append("canonical:boundary13-specialists: advanced roster status did not load")
        advanced_authority = str(advanced_wide.get("authorityCopy") or "")
        for phrase in ("stable character IDs", "current Script fingerprint", "assignment remains in Cast"):
            if phrase not in advanced_authority:
                failures.append(
                    f"canonical:boundary13-specialists: advanced authority copy omitted {phrase!r}"
                )

        advanced_compact = interactions.get("advancedCompact") or {}
        if advanced_compact.get("contextVisible") is not True:
            failures.append("canonical:boundary13-specialists: compact advanced context was hidden")
        if advanced_compact.get("contextName") != advanced_wide.get("contextName"):
            failures.append("canonical:boundary13-specialists: compact advanced character drifted")
        if advanced_compact.get("contextMeta") != advanced_wide.get("contextMeta"):
            failures.append("canonical:boundary13-specialists: compact advanced Script context drifted")
        if advanced_compact.get("horizontalOverflow") is not False:
            failures.append("canonical:boundary13-specialists: compact advanced route overflowed horizontally")
        advanced_rect = advanced_compact.get("bannerRect") or {}
        if float(advanced_rect.get("left") or 0) < -1 or float(advanced_rect.get("right") or 0) > 1025:
            failures.append("canonical:boundary13-specialists: compact advanced context escaped the viewport")

        voice_lab_wide = interactions.get("voiceLabWide") or {}
        voice_lab_expected = {
            "destination": "more",
            "tool": "voice-training",
            "mode": "training",
            "project": more_project,
            "character": more_character,
            "source": more_source,
            "returnRoute": more_return,
            "pageTitle": "Voice Lab",
            "kicker": "Voice Lab · Train",
            "contextVisible": True,
            "returnLabel": "Return to character",
            "selectedId": more_character,
            "assignmentMutationControlCount": 0,
            "referenceAssignmentActionCount": 0,
            "horizontalOverflow": False,
        }
        for key, expected in voice_lab_expected.items():
            if voice_lab_wide.get(key) != expected:
                failures.append(
                    f"canonical:boundary13-specialists: Voice Lab {key}={voice_lab_wide.get(key)!r} != {expected!r}"
                )
        if voice_lab_wide.get("hash") != voice_lab_wide.get("locationHash"):
            failures.append("canonical:boundary13-specialists: Voice Lab route and browser hash diverged")
        if not voice_lab_wide.get("contextName") or voice_lab_wide.get("contextName") == "Selected character":
            failures.append("canonical:boundary13-specialists: Voice Lab did not hydrate the character name")
        if "Script label:" not in str(voice_lab_wide.get("contextMeta") or ""):
            failures.append("canonical:boundary13-specialists: Voice Lab omitted the Script label")
        if int(voice_lab_wide.get("statusEntryCount") or 0) < 1:
            failures.append("canonical:boundary13-specialists: Voice Lab roster status did not load")
        voice_lab_authority = str(voice_lab_wide.get("authorityCopy") or "")
        for phrase in ("do not change the production Voice", "Return to Cast"):
            if phrase not in voice_lab_authority:
                failures.append(
                    f"canonical:boundary13-specialists: Voice Lab authority copy omitted {phrase!r}"
                )

        voice_lab_compact = interactions.get("voiceLabCompact") or {}
        if voice_lab_compact.get("contextVisible") is not True:
            failures.append("canonical:boundary13-specialists: compact Voice Lab context was hidden")
        if voice_lab_compact.get("contextName") != voice_lab_wide.get("contextName"):
            failures.append("canonical:boundary13-specialists: compact Voice Lab character drifted")
        if voice_lab_compact.get("contextMeta") != voice_lab_wide.get("contextMeta"):
            failures.append("canonical:boundary13-specialists: compact Voice Lab Script context drifted")
        if voice_lab_compact.get("horizontalOverflow") is not False:
            failures.append("canonical:boundary13-specialists: compact Voice Lab route overflowed horizontally")
        voice_lab_rect = voice_lab_compact.get("bannerRect") or {}
        if float(voice_lab_rect.get("left") or 0) < -1 or float(voice_lab_rect.get("right") or 0) > 1025:
            failures.append("canonical:boundary13-specialists: compact Voice Lab context escaped the viewport")

        specialist_cast = interactions.get("specialistCastReturned") or {}
        if specialist_cast.get("destination") != "cast":
            failures.append("canonical:boundary13-specialists: assignment handoff did not open Cast")
        if specialist_cast.get("project") != more_project or specialist_cast.get("character") != more_character:
            failures.append("canonical:boundary13-specialists: assignment handoff lost project or character")
        if specialist_cast.get("filter") != "needs_attention":
            failures.append("canonical:boundary13-specialists: assignment handoff lost exact Cast filter state")
        if specialist_cast.get("productionAssignmentControlVisible") is not True:
            failures.append("canonical:boundary13-specialists: Cast assignment authority was not visible")

        for label, report, baseline in (
            ("Voice Lab Back", interactions.get("voiceLabBack") or {}, voice_lab_wide),
            ("advanced Back", interactions.get("advancedBack") or {}, advanced_wide),
            ("Voice Lab Forward", interactions.get("voiceLabForward") or {}, voice_lab_wide),
        ):
            for key in (
                "destination",
                "tool",
                "mode",
                "project",
                "character",
                "source",
                "returnRoute",
                "hash",
                "contextName",
                "contextMeta",
            ):
                if report.get(key) != baseline.get(key):
                    failures.append(
                        f"canonical:boundary13-specialists: {label} lost {key}"
                    )
            if report.get("locationHash") != baseline.get("hash"):
                failures.append(
                    f"canonical:boundary13-specialists: {label} browser hash diverged"
                )
            if report.get("contextVisible") is not True:
                failures.append(
                    f"canonical:boundary13-specialists: {label} restored a hidden context"
                )

        maintenance_initial = interactions.get("maintenanceInitial") or {}
        maintenance_hash = maintenance_initial.get("hash")
        expected_maintenance = {
            "destination": "more",
            "tool": "maintenance",
            "mode": "dependencies",
            "project": more_project,
            "character": more_character,
            "returnRoute": "#/settings",
            "workspaceVisible": True,
            "settingsHidden": True,
            "legacyHidden": True,
            "recoveryHidden": True,
            "rawAbsolutePathVisible": False,
            "rawFingerprintVisible": False,
            "destructiveButtonVisible": False,
        }
        for key, expected in expected_maintenance.items():
            if maintenance_initial.get(key) != expected:
                failures.append(
                    f"canonical:boundary13-maintenance: {key}={maintenance_initial.get(key)!r} != {expected!r}"
                )
        if maintenance_hash != maintenance_initial.get("locationHash"):
            failures.append("canonical:boundary13-maintenance: route and browser hash diverged")
        if int(maintenance_initial.get("healthRows") or 0) < 2:
            failures.append("canonical:boundary13-maintenance: recovery and health rows were incomplete")
        if int(maintenance_initial.get("modelRows") or 0) < 1:
            failures.append("canonical:boundary13-maintenance: model diagnostics were missing")
        if int(maintenance_initial.get("libraryRows") or 0) < 1:
            failures.append("canonical:boundary13-maintenance: Library dependency rows were missing")
        if int(maintenance_initial.get("projectRows") or 0) < 1:
            failures.append("canonical:boundary13-maintenance: project rows were missing")
        if int(maintenance_initial.get("impactButtons") or 0) < 1:
            failures.append("canonical:boundary13-maintenance: no guarded impact review was available")

        maintenance_impact_initial = interactions.get("maintenanceImpactInitial") or {}
        safe_artifact_id = maintenance_impact_initial.get("safeArtifactId")
        if not safe_artifact_id:
            failures.append("canonical:boundary13-maintenance: no safe fixture artifact was available for impact review")
        expected_impact = {
            "dialogOpen": True,
            "kind": "library",
            "actionLabel": "Delete artifact",
            "actionDisabled": True,
            "activeElement": "maintenance-confirm-input",
        }
        for key, expected in expected_impact.items():
            if maintenance_impact_initial.get(key) != expected:
                failures.append(
                    f"canonical:boundary13-maintenance: impact {key}={maintenance_impact_initial.get(key)!r} != {expected!r}"
                )
        if not maintenance_impact_initial.get("confirmText"):
            failures.append("canonical:boundary13-maintenance: impact review omitted typed confirmation")
        if safe_artifact_id and safe_artifact_id not in str(maintenance_impact_initial.get("title") or ""):
            # Human title uses the artifact name rather than its internal ID.
            if not str(maintenance_impact_initial.get("title") or "").startswith("Delete "):
                failures.append("canonical:boundary13-maintenance: impact review title was unclear")

        maintenance_wrong = interactions.get("maintenanceImpactWrong") or {}
        if maintenance_wrong.get("actionDisabled") is not True:
            failures.append("canonical:boundary13-maintenance: wrong confirmation enabled deletion")
        maintenance_exact = interactions.get("maintenanceImpactExact") or {}
        if maintenance_exact.get("actionDisabled") is not False:
            failures.append("canonical:boundary13-maintenance: exact confirmation did not enable the guarded action")
        if maintenance_exact.get("inputValue") != maintenance_impact_initial.get("confirmText"):
            failures.append("canonical:boundary13-maintenance: confirmation text drifted")
        maintenance_closed = interactions.get("maintenanceImpactClosed") or {}
        if maintenance_closed.get("dialogOpen") is not False:
            failures.append("canonical:boundary13-maintenance: impact dialog did not close")
        if safe_artifact_id and maintenance_closed.get("focusRestored") != safe_artifact_id:
            failures.append("canonical:boundary13-maintenance: impact dialog did not restore focus")

        maintenance_native = interactions.get("maintenanceNativeRoute") or {}
        if not maintenance_native.get("nativeArtifactId"):
            failures.append("canonical:boundary13-maintenance: no native artifact link was available")
        if not maintenance_native.get("destination"):
            failures.append("canonical:boundary13-maintenance: native artifact link did not navigate")
        if maintenance_native.get("returnRoute") != maintenance_hash:
            failures.append("canonical:boundary13-maintenance: native artifact link lost exact Maintenance return state")
        maintenance_back = interactions.get("maintenanceBack") or {}
        if maintenance_back.get("hash") != maintenance_hash or maintenance_back.get("locationHash") != maintenance_hash:
            failures.append("canonical:boundary13-maintenance: Back did not restore the exact Maintenance route")
        if maintenance_back.get("workspaceVisible") is not True or maintenance_back.get("contentVisible") is not True:
            failures.append("canonical:boundary13-maintenance: Back restored hidden Maintenance content")

        settings_initial = interactions.get("settingsInitial") or {}
        if settings_initial.get("workspaceVisible") is not True:
            failures.append("canonical:boundary13-settings: canonical Settings did not open")
        if settings_initial.get("legacyHidden") is not True or settings_initial.get("recoveryHidden") is not True:
            failures.append("canonical:boundary13-settings: Maintenance surfaces were not hidden")
        if not settings_initial.get("defaultTemplate"):
            failures.append("canonical:boundary13-settings: default template was missing")
        if settings_initial.get("apiKeyValue"):
            failures.append("canonical:boundary13-settings: API key value leaked")
        if "not displayed" not in str(settings_initial.get("apiKeyState") or ""):
            failures.append("canonical:boundary13-settings: secret redaction copy was missing")
        if int(settings_initial.get("advancedActionCount") or 0) != 4:
            failures.append("canonical:boundary13-settings: advanced destinations were incomplete")
        if settings_initial.get("rawFingerprintVisible") is not False:
            failures.append("canonical:boundary13-settings: raw fingerprint was visible")

        settings_invalid = interactions.get("settingsInvalid") or {}
        if settings_invalid.get("saveState") != "Not saved":
            failures.append("canonical:boundary13-settings: invalid save state was not retained")
        if "Native Ollama" not in str(settings_invalid.get("error") or ""):
            failures.append("canonical:boundary13-settings: invalid provider error was unclear")
        if settings_invalid.get("outputLanguage") != "Swedish":
            failures.append("canonical:boundary13-settings: invalid edit was discarded")
        if settings_invalid.get("providerUrl") != "https://remote.example/v1":
            failures.append("canonical:boundary13-settings: invalid URL was not retained")
        if settings_invalid.get("motion") != "reduced" or settings_invalid.get("bodyMotion") != "reduced":
            failures.append("canonical:boundary13-settings: accessibility preview was not immediate")

        settings_saved = interactions.get("settingsSaved") or {}
        expected_saved = {
            "uiSaveState": "Saved",
            "persistedOutputLanguage": "Swedish",
            "persistedProviderUrl": "http://127.0.0.1:11434/v1",
            "persistedMotion": "reduced",
            "persistedContrast": "more",
            "persistedDensity": "compact",
            "persistedRollbackDays": 45,
            "persistedIntermediateDays": 10,
            "persistedBackupGib": 24,
            "apiKeyValueExposed": None,
            "apiKeyConfigured": True,
        }
        for key, expected in expected_saved.items():
            if settings_saved.get(key) != expected:
                failures.append(
                    f"canonical:boundary13-settings: {key}={settings_saved.get(key)!r} != {expected!r}"
                )

        settings_reloaded = interactions.get("settingsReloaded") or {}
        if settings_reloaded.get("destination") != "settings":
            failures.append("canonical:boundary13-settings: reload did not restore Settings")
        for key, expected in (
            ("outputLanguage", "Swedish"),
            ("providerUrl", "http://127.0.0.1:11434/v1"),
            ("motion", "reduced"),
            ("contrast", "more"),
            ("density", "compact"),
            ("bodyMotion", "reduced"),
            ("bodyContrast", "more"),
            ("bodyDensity", "compact"),
        ):
            if settings_reloaded.get(key) != expected:
                failures.append(f"canonical:boundary13-settings: reload lost {key}")
        if settings_reloaded.get("apiKeyValue"):
            failures.append("canonical:boundary13-settings: reload exposed API key")

        settings_maintenance = interactions.get("settingsMaintenance") or {}
        if settings_maintenance.get("destination") != "more" or settings_maintenance.get("tool") != "maintenance":
            failures.append("canonical:boundary13-settings: diagnostics did not open Maintenance")
        if settings_maintenance.get("mode") != "runtime":
            failures.append("canonical:boundary13-settings: diagnostics mode context was lost")
        if settings_maintenance.get("returnRoute") != "#/settings":
            failures.append("canonical:boundary13-settings: Maintenance return route drifted")
        if settings_maintenance.get("canonicalHidden") is not True or settings_maintenance.get("legacyVisible") is not True:
            failures.append("canonical:boundary13-settings: Settings/Maintenance separation failed")
        if settings_maintenance.get("runtimeOpen") is not True:
            failures.append("canonical:boundary13-settings: runtime diagnostics did not open")

        settings_templates = interactions.get("settingsTemplates") or {}
        if settings_templates.get("destination") != "templates":
            failures.append("canonical:boundary13-settings: Manage Templates did not open Templates")
        if settings_templates.get("returnRoute") != "#/settings":
            failures.append("canonical:boundary13-settings: Templates return route drifted")

        if payload.get("networkErrors"): 
            failures.append("network errors")
        if payload.get("consoleErrors"):
            failures.append("console errors")
        if payload.get("runtimeErrors"):
            failures.append("runtime errors")
        return {
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "output_dir": str(output_dir),
            "audit": payload,
        }

    if mode == "boundary13-final":
        failures: list[str] = []
        acceptance = payload.get("boundary13FinalAcceptance") or {}
        surfaces = acceptance.get("surfaces") or []
        expected_surfaces = {
            "projects",
            "library",
            "voices",
            "templates",
            "settings",
            "more",
            "help",
            "maintenance",
            "advanced",
            "voiceLab",
        }
        reports = {item.get("name"): item for item in surfaces}
        if set(reports) != expected_surfaces:
            failures.append(
                "boundary13-final: semantic surface inventory was incomplete"
            )
        for name in sorted(expected_surfaces):
            report = reports.get(name) or {}
            dom = report.get("dom") or {}
            ax = report.get("ax") or {}
            if report.get("ready") is not True:
                failures.append(f"boundary13-final:{name}: surface did not settle")
            if dom.get("mainCount") != 1 or dom.get("visibleMainCount") != 1:
                failures.append(f"boundary13-final:{name}: main landmark count was wrong")
            if int(dom.get("h1Count") or 0) != 1:
                failures.append(f"boundary13-final:{name}: visible H1 count was wrong")
            if not dom.get("pageTitle"):
                failures.append(f"boundary13-final:{name}: page title was missing")
            if dom.get("unnamedInteractive"):
                failures.append(f"boundary13-final:{name}: unnamed interactive controls")
            if dom.get("duplicateVisibleIds"):
                failures.append(f"boundary13-final:{name}: duplicate visible IDs")
            for listbox in dom.get("listboxes") or []:
                if listbox.get("invalidSelectedValues"):
                    failures.append(
                        f"boundary13-final:{name}: invalid aria-selected value"
                    )
                if not listbox.get("activeDescendantExists"):
                    failures.append(
                        f"boundary13-final:{name}: invalid aria-activedescendant"
                    )
                if int(listbox.get("optionCount") or 0) > 0:
                    if int(listbox.get("selectedCount") or 0) != 1:
                        failures.append(
                            f"boundary13-final:{name}: listbox selection count was wrong"
                        )
                    if int(listbox.get("rovingCount") or 0) != 1:
                        failures.append(
                            f"boundary13-final:{name}: listbox roving tabindex was invalid"
                        )
            if int(dom.get("liveRegionCount") or 0) < 1:
                failures.append(f"boundary13-final:{name}: no visible live region")
            if dom.get("statusWithoutText"):
                failures.append(f"boundary13-final:{name}: color-only or unnamed status")
            active = dom.get("activeElement") or {}
            if not active.get("name"):
                failures.append(f"boundary13-final:{name}: keyboard focus lacked a name")
            if active.get("focusVisible") is not True:
                failures.append(f"boundary13-final:{name}: keyboard focus was not visible")
            if active.get("focusTreatment") is not True:
                failures.append(f"boundary13-final:{name}: keyboard focus had no treatment")
            if active.get("inViewport") is not True:
                failures.append(f"boundary13-final:{name}: keyboard focus escaped viewport")
            if int(dom.get("ariaCurrentCount") or 0) < 1:
                failures.append(f"boundary13-final:{name}: current navigation was not exposed")
            if int(dom.get("dialogOpenCount") or 0) != 0:
                failures.append(f"boundary13-final:{name}: dialog leaked open")
            if int(dom.get("filledPrimaryCount") or 0) > 1:
                failures.append(f"boundary13-final:{name}: duplicate filled primary actions")
            if int(dom.get("fullTransportCount") or 0) != 0:
                failures.append(f"boundary13-final:{name}: duplicate full transport")
            if dom.get("horizontalOverflow"):
                failures.append(f"boundary13-final:{name}: horizontal overflow")
            if dom.get("absolutePathVisible"):
                failures.append(f"boundary13-final:{name}: absolute path leaked")
            if dom.get("rawFingerprintVisible"):
                failures.append(f"boundary13-final:{name}: raw fingerprint leaked")
            if dom.get("internalIdVisible"):
                failures.append(f"boundary13-final:{name}: internal ID leaked")
            if dom.get("placeholderCopyVisible"):
                failures.append(f"boundary13-final:{name}: placeholder copy leaked")
            if dom.get("restartRequiredVisible"):
                failures.append(f"boundary13-final:{name}: restart-required claim leaked")
            if ax.get("mainCount") != 1:
                failures.append(f"boundary13-final:{name}: accessibility tree main count was wrong")
            if int(ax.get("navigationCount") or 0) < 1:
                failures.append(f"boundary13-final:{name}: accessibility tree had no navigation")
            if int(ax.get("headingCount") or 0) < 1:
                failures.append(f"boundary13-final:{name}: accessibility tree had no heading")
            if ax.get("unnamedInteractive"):
                failures.append(f"boundary13-final:{name}: AX tree had unnamed controls")
            if ax.get("pageTitleHeadingFound") is not True:
                failures.append(f"boundary13-final:{name}: AX tree missed the page title")

        localization = acceptance.get("localization") or {}
        if localization.get("lang") != "sv":
            failures.append("boundary13-final: localization language marker was not applied")
        if localization.get("viewport") != {"width": 1024, "height": 768}:
            failures.append("boundary13-final: localization viewport was wrong")
        if localization.get("horizontalOverflow"):
            failures.append("boundary13-final: localization caused horizontal overflow")
        if localization.get("outOfBounds"):
            failures.append("boundary13-final: localized controls escaped the viewport")
        if int(localization.get("visibleToolCount") or 0) < 8:
            failures.append("boundary13-final: localization lost specialist tools")
        if int(localization.get("screenshotBytes") or 0) < 1000:
            failures.append("boundary13-final: localization screenshot was empty")

        redirect_expectations = {
            "#library": ("library", None, "#/library"),
            "#voices": ("voices", None, "#/voices"),
            "#designer": ("more", "voice-designer", "#/more?"),
            "#project-recovery": ("more", "maintenance", "#/more?"),
            "#models": ("more", "model-cache", "#/more?"),
            "#help": ("more", "help-center", "#/more?"),
            "#training": ("more", "voice-training", "#/more?"),
            "#settings": ("settings", None, "#/settings"),
        }
        redirect_reports = {
            item.get("alias"): item
            for item in acceptance.get("legacyRedirects") or []
        }
        if set(redirect_reports) != set(redirect_expectations):
            failures.append("boundary13-final: legacy redirect inventory was incomplete")
        for alias, (destination, tool, prefix) in redirect_expectations.items():
            report = redirect_reports.get(alias) or {}
            if report.get("destination") != destination:
                failures.append(f"boundary13-final:{alias}: wrong destination")
            if report.get("tool") != tool:
                failures.append(f"boundary13-final:{alias}: wrong tool")
            if not str(report.get("hash") or "").startswith(prefix):
                failures.append(f"boundary13-final:{alias}: route was not canonical")
            if report.get("hash") != report.get("locationHash"):
                failures.append(f"boundary13-final:{alias}: route and hash diverged")
            if report.get("canonicalized") is not True:
                failures.append(f"boundary13-final:{alias}: alias remained visible")
            if report.get("horizontalOverflow"):
                failures.append(f"boundary13-final:{alias}: redirected surface overflowed")

        canonical_expectations = {
            "library-wide": ("library", "Library"),
            "library-compact": ("library", "Library"),
            "voices-wide": ("voices", "Voices"),
            "voices-compact": ("voices", "Voices"),
            "templates-wide": ("templates", "Templates"),
            "settings-wide": ("settings", "Settings"),
            "settings-compact": ("settings", "Settings"),
            "maintenance-wide": ("more", "Maintenance"),
            "maintenance-compact": ("more", "Maintenance"),
            "more-wide": ("more", "More"),
            "more-compact": ("more", "More"),
            "help-wide": ("more", "Help Center"),
            "help-compact": ("more", "Help Center"),
        }
        for name, (destination, title) in canonical_expectations.items():
            report = (payload.get("canonicalShell") or {}).get(name) or {}
            if report.get("destination") != destination:
                failures.append(f"boundary13-final:{name}: wrong destination")
            if report.get("pageTitle") != title:
                failures.append(f"boundary13-final:{name}: wrong title")
            if report.get("horizontalOverflow"):
                failures.append(f"boundary13-final:{name}: horizontal overflow")

        runtime_purity = payload.get("runtimePurity") or {}
        if runtime_purity.get("startup_and_read_unchanged") is not True:
            failures.append("boundary13-final: startup or read APIs mutated runtime state")
        if runtime_purity.get("browser_unchanged") is not True:
            failures.append("boundary13-final: read-only browser audit mutated runtime state")
        if runtime_purity.get("api_unchanged") is not True:
            failures.append("boundary13-final: authoritative read models drifted")
        read_only_post_paths = {
            "/api/voice_design/accent_status",
        }
        non_read_requests = []
        for request in payload.get("networkRequests") or []:
            url = str(request.get("url") or "")
            method = str(request.get("method") or "").upper()
            if not url.startswith("http://127.0.0.1:"):
                continue
            path = "/" + url.split("/", 3)[-1].split("?", 1)[0]
            if method in {"GET", "HEAD", "OPTIONS"}:
                continue
            if method == "POST" and path in read_only_post_paths:
                continue
            non_read_requests.append(request)
        if non_read_requests:
            failures.append("boundary13-final: browser issued mutating HTTP requests")
        if payload.get("networkErrors"):
            failures.append("network errors")
        if payload.get("consoleErrors"):
            failures.append("console errors")
        if payload.get("runtimeErrors"):
            failures.append("runtime errors")
        if int(acceptance.get("screenshotBytes") or 0) < 1000:
            failures.append("boundary13-final: wide supporting screenshot was empty")
        return {
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "output_dir": str(output_dir),
            "audit": payload,
        }

    if mode == "new-project":
        failures: list[str] = []
        states = payload.get("states", {})
        required_states = (
            "new-project-empty",
            "new-project-empty-compact",
            "new-project-valid-epub",
            "new-project-import-script",
            "new-project-invalid-replacement",
            "new-project-create-success",
        )
        for state_name in required_states:
            report = states.get(state_name, {})
            modal = report.get("newProject", {})
            if report.get("horizontalOverflow"):
                failures.append(f"state:{state_name}: horizontal overflow")
            if report.get("outOfBounds"):
                failures.append(f"state:{state_name}: out-of-bounds controls")
            modal_expected = state_name != "new-project-create-success"
            if modal_expected and report.get("visibleModal") != "newProjectModal":
                failures.append(f"state:{state_name}: modal was not visible")
            if modal_expected and modal.get("visible") is not True:
                failures.append(f"state:{state_name}: modal metrics were not visible")
            if not modal_expected and modal.get("visible") is not False:
                failures.append(f"state:{state_name}: modal did not close after activation")
            if modal.get("sectionCount") != 5:
                failures.append(f"state:{state_name}: expected five form sections")
            if modal.get("radiogroupCount") != 2:
                failures.append(f"state:{state_name}: expected two flat radiogroups")
            if modal.get("fauxStepperCount") != 0:
                failures.append(f"state:{state_name}: faux stepper was present")
            if modal_expected and modal.get("footerVisible") is not True:
                failures.append(f"state:{state_name}: footer was not visible")
            if modal_expected and modal.get("footerWithinViewport") is not True:
                failures.append(f"state:{state_name}: footer escaped the viewport")
            if modal.get("disallowedRuntimeCopy"):
                failures.append(f"state:{state_name}: runtime internals were exposed")
            if modal.get("advancedOpen"):
                failures.append(f"state:{state_name}: Advanced options opened by default")

        for state_name in ("new-project-empty", "new-project-empty-compact"):
            modal = states.get(state_name, {}).get("newProject", {})
            if modal.get("submitDisabled") is not True:
                failures.append(f"state:{state_name}: Create Project enabled without source")
            if modal.get("sourceSummaryVisible"):
                failures.append(f"state:{state_name}: empty source summary was visible")
        compact = states.get("new-project-empty-compact", {}).get("newProject", {})
        if compact.get("bodyScrollable") is not True:
            failures.append("state:new-project-empty-compact: body was not scrollable")

        valid_epub = states.get("new-project-valid-epub", {}).get("newProject", {})
        expected_epub = {
            "inspectionValid": True,
            "inspectionMethod": "local",
            "sourceSummaryVisible": True,
            "sourceFilename": "new-project-valid.epub",
            "bookTitle": "The Browser Audit Book",
            "author": "Audit Author",
            "sourceLanguage": "English",
            "outputLanguage": "English",
            "submitDisabled": False,
            "statusState": "success",
        }
        for key, expected in expected_epub.items():
            if valid_epub.get(key) != expected:
                failures.append(
                    f"state:new-project-valid-epub: {key}={valid_epub.get(key)!r} != {expected!r}"
                )

        imported = states.get("new-project-import-script", {}).get("newProject", {})
        expected_import = {
            "inspectionValid": True,
            "method": "import_existing_script",
            "inspectionMethod": "import_existing_script",
            "sourceFilename": "new-project-valid-script.json",
            "submitDisabled": False,
        }
        for key, expected in expected_import.items():
            if imported.get(key) != expected:
                failures.append(
                    f"state:new-project-import-script: {key}={imported.get(key)!r} != {expected!r}"
                )

        replacement = states.get("new-project-invalid-replacement", {}).get("newProject", {})
        if replacement.get("inspectionValid") is not True:
            failures.append("state:new-project-invalid-replacement: prior inspection was lost")
        if replacement.get("sourceFilename") != "new-project-valid.epub":
            failures.append("state:new-project-invalid-replacement: prior source was lost")
        if replacement.get("statusState") != "error":
            failures.append("state:new-project-invalid-replacement: error was not visible")
        if "previously validated source" not in str(replacement.get("statusText") or ""):
            failures.append("state:new-project-invalid-replacement: preservation message missing")
        if replacement.get("submitDisabled") is not False:
            failures.append("state:new-project-invalid-replacement: preserved source became unusable")

        created = states.get("new-project-create-success", {}).get("newProject", {})
        if created.get("completed") is not True:
            failures.append("state:new-project-create-success: create transaction did not complete")
        if created.get("submitLabel") != "Done":
            failures.append("state:new-project-create-success: completion action was not Done")
        if created.get("destination") != "script":
            failures.append("state:new-project-create-success: active project did not open Script")
        if created.get("pageTitle") != "Script":
            failures.append("state:new-project-create-success: Script page title was missing")
        if created.get("primaryAction") != "Generate Script":
            failures.append("state:new-project-create-success: empty Script action was not Generate Script")
        if "restart" in str(created.get("statusText") or "").casefold():
            failures.append("state:new-project-create-success: obsolete restart copy remained")

        if payload.get("consoleErrors"):
            failures.append("console errors")
        if payload.get("runtimeErrors"):
            failures.append("runtime errors")
        return {
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "output_dir": str(output_dir),
            "audit": payload,
        }

    failures: list[str] = []
    for viewport_name in ("desktop", "narrow"):
        for tab, report in payload.get(viewport_name, {}).items():
            if report.get("horizontalOverflow"):
                failures.append(f"{viewport_name}:{tab}: horizontal overflow")
            if report.get("outOfBounds"):
                failures.append(
                    f"{viewport_name}:{tab}: out-of-bounds controls"
                )
            if report.get("legacyStepIndicators"):
                failures.append(
                    f"{viewport_name}:{tab}: legacy step indicators"
                )
            if report.get("visibleNativeFileInputCount"):
                failures.append(
                    f"{viewport_name}:{tab}: visible native file input"
                )
            if report.get("loadingTableRowCount"):
                failures.append(
                    f"{viewport_name}:{tab}: loading state rendered as table row"
                )
            if report.get("unlabeledIconButtonCount"):
                failures.append(
                    f"{viewport_name}:{tab}: unlabeled icon-only controls"
                )
            if report.get("activeInsetShadowCount"):
                failures.append(
                    f"{viewport_name}:{tab}: active master row uses inset edge bar"
                )
    compute_text = str(
        payload.get("desktop", {})
        .get("setup", {})
        .get("nav", {})
        .get("computeText")
        or ""
    )
    if not compute_text.startswith(("CPU ", "GPU ")):
        failures.append(
            f"desktop:nav: compute activity remained unavailable ({compute_text!r})"
        )

    for viewport_name in ("desktop", "narrow"):
        external_workflow = (
            payload.get(viewport_name, {})
            .get("script", {})
            .get("externalWorkflow")
            or {}
        )
        if external_workflow.get("open") is not False:
            failures.append(
                f"{viewport_name}:script: external workflow was not collapsed by default"
            )
        if external_workflow.get("candidateVisible") is not False:
            failures.append(
                f"{viewport_name}:script: external import candidate was visible before inspection"
            )
        if external_workflow.get("structuredVisible") is not False:
            failures.append(
                f"{viewport_name}:script: structured stage result was visible before inspection"
            )
        if external_workflow.get("resultInputVisible") is not False:
            failures.append(
                f"{viewport_name}:script: native ChatGPT result input was visible"
            )
        if external_workflow.get("importInputVisible") is not False:
            failures.append(
                f"{viewport_name}:script: native annotated-script input was visible"
            )

        recovery = (
            payload.get(viewport_name, {})
            .get("setup", {})
            .get("recovery")
            or {}
        )
        if recovery.get("open") is not False:
            failures.append(
                f"{viewport_name}:setup: recovery disclosure was not collapsed by default"
            )
        if recovery.get("bodyVisible") is not False:
            failures.append(
                f"{viewport_name}:setup: recovery details remained visually dominant"
            )
        summary_height = float(
            (recovery.get("summaryRect") or {}).get("height") or 0
        )
        if not (1 <= summary_height <= 48):
            failures.append(
                f"{viewport_name}:setup: recovery summary height was not compact ({summary_height})"
            )
        if recovery.get("overallState") not in {
            "idle",
            "complete",
            "running",
            "warning",
            "error",
        }:
            failures.append(
                f"{viewport_name}:setup: recovery status light had no valid state"
            )
        if not recovery.get("overallText") or "Checking" in str(
            recovery.get("overallText")
        ):
            failures.append(
                f"{viewport_name}:setup: recovery status did not finish loading"
            )

    canonical_shell = payload.get("canonicalShell", {})
    shell_expectations = {
        "home-wide": {
            "destination": "projects",
            "rail": 224,
            "header": 88,
            "padding": 32,
            "project_navigation": True,
            "stage_tracker": False,
            "title": "Project Home",
            "action": "New Project",
        },
        "home-compact": {
            "destination": "projects",
            "rail": 184,
            "header": 88,
            "padding": 24,
            "project_navigation": True,
            "stage_tracker": False,
            "title": "Project Home",
            "action": "New Project",
        },
        "script-wide": {
            "destination": "script",
            "rail": 224,
            "header": 104,
            "padding": 32,
            "project_navigation": True,
            "stage_tracker": True,
            "title": "Script",
            "action": None,
        },
        "script-compact": {
            "destination": "script",
            "rail": 184,
            "header": 104,
            "padding": 24,
            "project_navigation": True,
            "stage_tracker": True,
            "title": "Script",
            "action": None,
        },
        "cast-wide": {
            "destination": "cast", "rail": 224, "header": 104, "padding": 32,
            "project_navigation": True, "stage_tracker": True, "title": "Cast",
            "action": "Continue to Produce",
        },
        "cast-compact": {
            "destination": "cast", "rail": 184, "header": 104, "padding": 24,
            "project_navigation": True, "stage_tracker": True, "title": "Cast",
            "action": "Continue to Produce",
        },
        "produce-wide": {
            "destination": "produce", "rail": 224, "header": 104, "padding": 32,
            "project_navigation": True, "stage_tracker": True, "title": "Produce",
            "action": "Generate missing and stale audio",
        },
        "produce-compact": {
            "destination": "produce", "rail": 184, "header": 104, "padding": 24,
            "project_navigation": True, "stage_tracker": True, "title": "Produce",
            "action": "Generate missing and stale audio",
        },
        "export-wide": {
            "destination": "export", "rail": 224, "header": 104, "padding": 32,
            "project_navigation": True, "stage_tracker": True, "title": "Export",
            "action": "Build Audiobook",
        },
        "export-compact": {
            "destination": "export", "rail": 184, "header": 104, "padding": 24,
            "project_navigation": True, "stage_tracker": True, "title": "Export",
            "action": "Build Audiobook",
        },
    }
    for name, expected in shell_expectations.items():
        report = canonical_shell.get(name, {})
        if report.get("destination") != expected["destination"]:
            failures.append(f"canonical:{name}: wrong destination")
        rail_width = float((report.get("railRect") or {}).get("width") or 0)
        if abs(rail_width - expected["rail"]) > 1:
            failures.append(
                f"canonical:{name}: rail width {rail_width} != {expected['rail']}"
            )
        header_height = float((report.get("headerRect") or {}).get("height") or 0)
        if abs(header_height - expected["header"]) > 1:
            failures.append(
                f"canonical:{name}: header height {header_height} != {expected['header']}"
            )
        main_left = float((report.get("mainRect") or {}).get("left") or 0)
        if abs(main_left - expected["rail"]) > 1:
            failures.append(
                f"canonical:{name}: main origin {main_left} != rail {expected['rail']}"
            )
        padding = report.get("mainPadding") or {}
        for side in ("left", "right"):
            value = float(padding.get(side) or 0)
            if abs(value - expected["padding"]) > 1:
                failures.append(
                    f"canonical:{name}: {side} padding {value} != {expected['padding']}"
                )
        if report.get("globalNavigationVisible") is not True:
            failures.append(f"canonical:{name}: global navigation missing")
        if report.get("projectNavigationVisible") is not expected["project_navigation"]:
            failures.append(f"canonical:{name}: wrong project navigation visibility")
        if report.get("stageTrackerVisible") is not expected["stage_tracker"]:
            failures.append(f"canonical:{name}: wrong stage tracker visibility")
        if report.get("pageTitle") != expected["title"]:
            failures.append(f"canonical:{name}: wrong page title")
        if expected["action"] and report.get("primaryAction") != expected["action"]:
            failures.append(f"canonical:{name}: wrong primary action")
        if report.get("playerVisible") is not False:
            failures.append(f"canonical:{name}: no-track player remained visible")
        minimum_font = float(report.get("minimumShellFontSize") or 0)
        if minimum_font < 13:
            failures.append(
                f"canonical:{name}: shell text below 13px ({minimum_font})"
            )
        if report.get("ordinaryPanelShadowCount"):
            failures.append(f"canonical:{name}: ordinary panel shadow")
        if report.get("horizontalOverflow"):
            failures.append(f"canonical:{name}: horizontal overflow")
        if report.get("duplicateFullTransportCount"):
            failures.append(f"canonical:{name}: duplicate full transport")
        if report.get("placeholderCopyVisible"):
            failures.append(f"canonical:{name}: placeholder shell copy visible")

    if payload.get("consoleErrors"):
        failures.append("console errors")
    if payload.get("runtimeErrors"):
        failures.append("runtime errors")

    states = payload.get("states", {})
    for state_name, report in states.items():
        if report.get("horizontalOverflow"):
            failures.append(f"state:{state_name}: horizontal overflow")
        if report.get("outOfBounds"):
            failures.append(f"state:{state_name}: out-of-bounds controls")
        if report.get("mainOutline", {}).get("style") not in {None, "none"}:
            failures.append(f"state:{state_name}: page-level focus outline")

    new_project_states = (
        "new-project-empty",
        "new-project-empty-compact",
        "new-project-valid-epub",
        "new-project-import-script",
        "new-project-invalid-replacement",
        "new-project-create-success",
    )
    for state_name in new_project_states:
        report = states.get(state_name, {})
        modal = report.get("newProject", {})
        if report.get("visibleModal") != "newProjectModal":
            failures.append(f"state:{state_name}: New Project modal was not visible")
        if modal.get("visible") is not True:
            failures.append(f"state:{state_name}: New Project metrics were not visible")
        if modal.get("sectionCount") != 5:
            failures.append(f"state:{state_name}: New Project did not contain five sections")
        if modal.get("radiogroupCount") != 2:
            failures.append(f"state:{state_name}: method and preset radiogroups were not flat")
        if modal.get("fauxStepperCount") != 0:
            failures.append(f"state:{state_name}: faux stepper decoration was present")
        if modal.get("footerVisible") is not True:
            failures.append(f"state:{state_name}: modal footer was not visible")
        if modal.get("footerWithinViewport") is not True:
            failures.append(f"state:{state_name}: modal footer escaped the viewport")
        if modal.get("disallowedRuntimeCopy"):
            failures.append(f"state:{state_name}: normal flow exposed runtime internals")
        if modal.get("advancedOpen"):
            failures.append(f"state:{state_name}: Advanced options opened by default")

    for state_name in ("new-project-empty", "new-project-empty-compact"):
        modal = states.get(state_name, {}).get("newProject", {})
        if modal.get("submitDisabled") is not True:
            failures.append(f"state:{state_name}: Create Project was enabled without a source")
        if modal.get("sourceSummaryVisible"):
            failures.append(f"state:{state_name}: empty modal showed a source summary")
        if modal.get("inspectionValid"):
            failures.append(f"state:{state_name}: empty modal reported a valid inspection")
    if states.get("new-project-empty-compact", {}).get("newProject", {}).get("bodyScrollable") is not True:
        failures.append("state:new-project-empty-compact: compact modal body was not scrollable")

    valid_epub = states.get("new-project-valid-epub", {}).get("newProject", {})
    if valid_epub.get("inspectionValid") is not True:
        failures.append("state:new-project-valid-epub: EPUB inspection was not valid")
    if valid_epub.get("inspectionMethod") != "local":
        failures.append("state:new-project-valid-epub: EPUB used the wrong method")
    if valid_epub.get("sourceSummaryVisible") is not True:
        failures.append("state:new-project-valid-epub: extracted source summary was hidden")
    if valid_epub.get("sourceFilename") != "new-project-valid.epub":
        failures.append("state:new-project-valid-epub: wrong source filename")
    if valid_epub.get("bookTitle") != "The Browser Audit Book":
        failures.append("state:new-project-valid-epub: title was not extracted")
    if valid_epub.get("author") != "Audit Author":
        failures.append("state:new-project-valid-epub: author was not extracted")
    if valid_epub.get("sourceLanguage") != "English":
        failures.append("state:new-project-valid-epub: source language was not normalized")
    if valid_epub.get("outputLanguage") != "English":
        failures.append("state:new-project-valid-epub: output language was not initialized")
    if valid_epub.get("submitDisabled") is not False:
        failures.append("state:new-project-valid-epub: valid form could not be submitted")
    if valid_epub.get("statusState") != "success":
        failures.append("state:new-project-valid-epub: validation success was not announced")

    imported_script = states.get("new-project-import-script", {}).get("newProject", {})
    if imported_script.get("inspectionValid") is not True:
        failures.append("state:new-project-import-script: Script inspection was not valid")
    if imported_script.get("method") != "import_existing_script":
        failures.append("state:new-project-import-script: import method was not selected")
    if imported_script.get("inspectionMethod") != "import_existing_script":
        failures.append("state:new-project-import-script: Script used the wrong inspection method")
    if imported_script.get("sourceFilename") != "new-project-valid-script.json":
        failures.append("state:new-project-import-script: wrong imported Script filename")
    if imported_script.get("submitDisabled") is not False:
        failures.append("state:new-project-import-script: valid Script could not be submitted")

    replacement = states.get("new-project-invalid-replacement", {}).get("newProject", {})
    if replacement.get("inspectionValid") is not True:
        failures.append("state:new-project-invalid-replacement: prior valid inspection was lost")
    if replacement.get("sourceFilename") != "new-project-valid.epub":
        failures.append("state:new-project-invalid-replacement: prior source was not preserved")
    if replacement.get("statusState") != "error":
        failures.append("state:new-project-invalid-replacement: invalid replacement was not reported")
    if "previously validated source" not in str(replacement.get("statusText") or ""):
        failures.append("state:new-project-invalid-replacement: preservation message was missing")
    if replacement.get("submitDisabled") is not False:
        failures.append("state:new-project-invalid-replacement: preserved valid source became unusable")

    created = states.get("new-project-create-success", {}).get("newProject", {})
    if created.get("completed") is not True:
        failures.append("state:new-project-create-success: create transaction did not complete")
    if created.get("submitLabel") != "Done":
        failures.append("state:new-project-create-success: completion action was unclear")
    if created.get("statusState") != "success":
        failures.append("state:new-project-create-success: completion was not announced")
    if "restart" not in str(created.get("statusText") or "").casefold():
        failures.append("state:new-project-create-success: activation contract was concealed")

    for state_name in (
        "character-designer-context",
        "character-designer-context-narrow",
    ):
        context = states.get(state_name, {}).get("characterToolContext", {})
        if context.get("visible") is not True:
            failures.append(
                f"state:{state_name}: character tool context was not visible"
            )
        if context.get("tool") != "designer":
            failures.append(
                f"state:{state_name}: character tool context targeted the wrong tool"
            )
        if context.get("name") != "THE DOCTOR":
            failures.append(
                f"state:{state_name}: selected character name was not preserved"
            )
        if context.get("designerName") != context.get("name"):
            failures.append(
                f"state:{state_name}: Voice designer name was not prefilled"
            )
        if "Script voice: THE DOCTOR" not in str(context.get("meta") or ""):
            failures.append(
                f"state:{state_name}: Script voice context was missing"
            )
        if context.get("returnLabel") != "Return to character":
            failures.append(
                f"state:{state_name}: character return action was unclear"
            )

    expected_open_details = {
        "setup-recovery-log-behavior": {"recovery-center"},
        "characters-roster-log-behavior": {
            "character-roster-log-disclosure",
        },
        "setup-runtime-open": {"llm-runtime-panel"},
        "setup-advanced-prompts-open": {
            "promptSettings",
            "advanced-prompt-disclosure",
        },
        "script-external-import-verified": {
            "script-external-workflow",
        },
        "script-external-import-unverified-narrow": {
            "script-external-workflow",
        },
        "script-external-structured-result-narrow": {
            "script-external-workflow",
        },
        "voices-clone-upload-restored": {
            "utility-disclosure clone-controlled-disclosure",
        },
        "voices-controlled-preview-approved": {
            "utility-disclosure clone-controlled-disclosure",
        },
        "voice-capability-adapter-open": {"voice-capability-adapter-panel"},
    }
    for state_name, expected in expected_open_details.items():
        actual = set(states.get(state_name, {}).get("openDetails", []))
        missing = expected - actual
        if missing:
            failures.append(
                f"state:{state_name}: missing open disclosure {sorted(missing)}"
            )

    recovery_state = states.get("setup-recovery-log-behavior", {}).get(
        "recovery", {}
    )
    if recovery_state.get("open") is not True:
        failures.append("state:setup-recovery-log-behavior: recovery disclosure did not open")
    if recovery_state.get("bodyVisible") is not True:
        failures.append("state:setup-recovery-log-behavior: recovery body remained hidden")
    if int(recovery_state.get("stageCount") or 0) < 2:
        failures.append("state:setup-recovery-log-behavior: recoverable stages were not rendered")
    if recovery_state.get("sourceState") != "complete":
        failures.append("state:setup-recovery-log-behavior: saved source was not restored")
    if "book.txt" not in str(recovery_state.get("sourceText") or ""):
        failures.append("state:setup-recovery-log-behavior: saved source identity was missing")
    roster_log = states.get("characters-roster-log-behavior", {}).get(
        "characterRosterLog", {}
    )
    roster_log_audit = roster_log.get("audit") or {}
    if roster_log.get("open") is not True:
        failures.append(
            "state:characters-roster-log-behavior: roster log disclosure did not stay open"
        )
    if int(roster_log.get("lineCount") or 0) < 24:
        failures.append(
            "state:characters-roster-log-behavior: persisted roster log was incomplete"
        )
    for field in (
        "openedAtTail",
        "followedTailAfterRefresh",
        "manualPositionHadOverflow",
        "manualScrollPreserved",
    ):
        if roster_log_audit.get(field) is not True:
            failures.append(
                f"state:characters-roster-log-behavior: failed at {field}"
            )
    if states.get("dataset-project-loaded", {}).get("recoveryPollingActive") is not False:
        failures.append(
            "state:dataset-project-loaded: recovery polling continued outside Setup"
        )

    verified_state = states.get("script-external-import-verified", {})
    verified_external = verified_state.get("externalWorkflow", {})
    if verified_external.get("open") is not True:
        failures.append(
            "state:script-external-import-verified: workflow disclosure did not open"
        )
    if verified_external.get("candidateVisible") is not True:
        failures.append(
            "state:script-external-import-verified: candidate review remained hidden"
        )
    if verified_external.get("provenanceState") != "complete":
        failures.append(
            "state:script-external-import-verified: verified provenance state was not complete"
        )
    if "source verified" not in str(
        verified_external.get("provenanceText") or ""
    ).lower():
        failures.append(
            "state:script-external-import-verified: verified provenance wording was missing"
        )
    for field, expected in (
        ("entryText", "1,472"),
        ("speakerText", "18"),
        ("characterText", "184,230"),
        ("checkpointRadioCount", 3),
        ("warningCount", 1),
    ):
        if verified_external.get(field) != expected:
            failures.append(
                f"state:script-external-import-verified: {field} was not {expected!r}"
            )
    for field in ("checkpointVisible", "applyVisible"):
        if verified_external.get(field) is not True:
            failures.append(
                f"state:script-external-import-verified: {field} was not visible"
            )
    for field in (
        "rollbackVisible",
        "resultInputVisible",
        "importInputVisible",
    ):
        if verified_external.get(field) is not False:
            failures.append(
                f"state:script-external-import-verified: {field} should remain hidden"
            )
    verified_comparison = str(
        verified_external.get("comparisonText") or ""
    )
    if "1,360 → 1,472 entries (+112)" not in verified_comparison:
        failures.append(
            "state:script-external-import-verified: current-to-imported entry delta was missing"
        )
    if "177,000 → 184,230 spoken characters (+7,230)" not in verified_comparison:
        failures.append(
            "state:script-external-import-verified: current-to-imported character delta was missing"
        )
    verified_consequence = str(
        verified_external.get("consequenceText") or ""
    ).lower()
    if "marked stale" not in verified_consequence:
        failures.append(
            "state:script-external-import-verified: stale-audio consequence was missing"
        )
    if "voice configuration will be preserved" not in verified_consequence:
        failures.append(
            "state:script-external-import-verified: preserved voice consequence was missing"
        )
    if verified_external.get("sourceWarningVisible") is not False:
        failures.append(
            "state:script-external-import-verified: unverified-source warning remained visible"
        )
    for row in verified_external.get("utilityStatusRows") or []:
        right_gap = row.get("statusRightGap")
        center_ratio = row.get("statusCenterRatio")
        if right_gap is None or float(right_gap) > 56:
            failures.append(
                "state:script-external-import-verified: utility status was not aligned to the right edge"
            )
            break
        if center_ratio is None or float(center_ratio) < 0.72:
            failures.append(
                "state:script-external-import-verified: utility status remained centered in its row"
            )
            break

    unverified_state = states.get(
        "script-external-import-unverified-narrow", {}
    )
    unverified_external = unverified_state.get("externalWorkflow", {})
    if unverified_external.get("open") is not True:
        failures.append(
            "state:script-external-import-unverified-narrow: workflow disclosure did not open"
        )
    if unverified_external.get("candidateVisible") is not True:
        failures.append(
            "state:script-external-import-unverified-narrow: candidate review remained hidden"
        )
    if unverified_external.get("provenanceState") != "warning":
        failures.append(
            "state:script-external-import-unverified-narrow: unverified provenance was not warning state"
        )
    if "not verified" not in str(
        unverified_external.get("provenanceText") or ""
    ).lower():
        failures.append(
            "state:script-external-import-unverified-narrow: unverified provenance wording was missing"
        )
    if unverified_external.get("checkpointVisible") is not False:
        failures.append(
            "state:script-external-import-unverified-narrow: unnecessary checkpoint choice was visible"
        )
    if unverified_external.get("applyVisible") is not True:
        failures.append(
            "state:script-external-import-unverified-narrow: apply action was not visible"
        )
    if unverified_external.get("rollbackVisible") is not False:
        failures.append(
            "state:script-external-import-unverified-narrow: rollback action was visible before apply"
        )
    if unverified_external.get("warningCount") != 1:
        failures.append(
            "state:script-external-import-unverified-narrow: import warning list was missing"
        )
    if unverified_external.get("sourceWarningVisible") is not True:
        failures.append(
            "state:script-external-import-unverified-narrow: explicit source warning was missing"
        )
    unverified_consequence = str(
        unverified_external.get("consequenceText") or ""
    ).lower()
    if "replace the current voice configuration" not in unverified_consequence:
        failures.append(
            "state:script-external-import-unverified-narrow: replacement consequence was missing"
        )
    if "no completed audio" not in unverified_consequence:
        failures.append(
            "state:script-external-import-unverified-narrow: no-audio consequence was missing"
        )
    if "No valid current script was available" not in str(
        unverified_external.get("comparisonText") or ""
    ):
        failures.append(
            "state:script-external-import-unverified-narrow: unavailable comparison was not explicit"
        )

    for state_name, selected_task, target_label, destination in (
        (
            "script-task-bundle-open",
            "persona_generation",
            "Speaker",
            "Characters",
        ),
        (
            "script-task-bundle-open-narrow",
            "visual_discovery",
            "Character",
            "Visual dossiers",
        ),
    ):
        task_state = states.get(state_name, {})
        task_external = task_state.get("externalWorkflow", {})
        if task_external.get("open") is not True:
            failures.append(f"state:{state_name}: Task Bundle workflow did not open")
        if task_external.get("taskOptionCount", 0) < 16:
            failures.append(f"state:{state_name}: full task registry did not render")
        if task_external.get("taskSelectedValue") != selected_task:
            failures.append(f"state:{state_name}: selected task did not persist")
        if task_external.get("targetLabel") != target_label:
            failures.append(f"state:{state_name}: target label did not match scope")
        if task_external.get("targetFieldHidden") is not False:
            failures.append(f"state:{state_name}: required target field remained hidden")
        if destination not in str(task_external.get("taskSummaryText") or ""):
            failures.append(f"state:{state_name}: native destination summary was missing")
        if task_external.get("taskPanelCount") != 2:
            failures.append(f"state:{state_name}: export/import hierarchy was not two-part")
        if task_external.get("obsoleteControlCount") != 0:
            failures.append(f"state:{state_name}: obsolete handoff controls remained in the DOM")
        if task_external.get("importButtonVisible") is not True:
            failures.append(f"state:{state_name}: completed-task import action was missing")

    structured_state = states.get(
        "script-external-structured-result-narrow", {}
    )
    structured_external = structured_state.get("externalWorkflow", {})
    if structured_external.get("open") is not True:
        failures.append(
            "state:script-external-structured-result-narrow: workflow disclosure did not open"
        )
    if structured_external.get("structuredVisible") is not True:
        failures.append(
            "state:script-external-structured-result-narrow: validated result review remained hidden"
        )
    if structured_external.get("structuredState") != "warning":
        failures.append(
            "state:script-external-structured-result-narrow: reconciliation state was not explicit"
        )
    if "reconciliation required" not in str(
        structured_external.get("structuredStatus") or ""
    ).lower():
        failures.append(
            "state:script-external-structured-result-narrow: reconciliation wording was missing"
        )
    for field, expected in (
        ("structuredTask", "Discover character roster"),
        ("structuredDestination", "Characters"),
        ("structuredCount", "1,500"),
    ):
        if structured_external.get(field) != expected:
            failures.append(
                f"state:script-external-structured-result-narrow: {field} was not {expected!r}"
            )
    structured_json = str(structured_external.get("structuredJson") or "")
    try:
        structured_payload = json.loads(structured_json)
        structured_observations = structured_payload.get("observations", [])
    except (json.JSONDecodeError, AttributeError):
        structured_observations = []
    if (
        len(structured_observations) != 1500
        or structured_observations[0].get("mention_text") != "The Doctor"
        or structured_observations[-1].get("mention_text") != "Roz"
    ):
        failures.append(
            "state:script-external-structured-result-narrow: validated JSON review was incomplete"
        )
    if structured_external.get("candidateVisible") is not False:
        failures.append(
            "state:script-external-structured-result-narrow: script apply candidate was visible for structured stage result"
        )
    if structured_external.get("openDestinationVisible") is not True:
        failures.append(
            "state:script-external-structured-result-narrow: native review action was missing"
        )
    for field in ("resultInputVisible", "importInputVisible"):
        if structured_external.get(field) is not False:
            failures.append(
                f"state:script-external-structured-result-narrow: {field} should remain hidden"
            )

    profile_state = states.get(
        "script-voice-profile-comparison-narrow", {}
    )
    profile_external = profile_state.get("externalWorkflow", {})
    if profile_external.get("open") is not True:
        failures.append(
            "state:script-voice-profile-comparison-narrow: workflow disclosure did not open"
        )
    if profile_external.get("structuredState") != "warning":
        failures.append(
            "state:script-voice-profile-comparison-narrow: comparison state was not explicit"
        )
    if profile_external.get("structuredTask") != (
        "Create voice profiles for all speaking identities"
    ):
        failures.append(
            "state:script-voice-profile-comparison-narrow: bulk task label was unclear"
        )
    if profile_external.get("structuredDestination") != "Characters":
        failures.append(
            "state:script-voice-profile-comparison-narrow: Characters destination was unclear"
        )
    if profile_external.get("personaConflictVisible") is not True:
        failures.append(
            "state:script-voice-profile-comparison-narrow: current/imported comparison remained hidden"
        )
    if profile_external.get("personaConflictCount") != 2:
        failures.append(
            "state:script-voice-profile-comparison-narrow: expected two speaker conflicts"
        )
    if profile_external.get("personaReplacementCount") != 2:
        failures.append(
            "state:script-voice-profile-comparison-narrow: per-speaker replacement controls were missing"
        )
    if profile_external.get("personaComparisonColumnCount") != 4:
        failures.append(
            "state:script-voice-profile-comparison-narrow: Current and Imported columns were incomplete"
        )
    if "1 new identity draft" not in str(
        profile_external.get("personaNewCount") or ""
    ):
        failures.append(
            "state:script-voice-profile-comparison-narrow: new-profile consequence was missing"
        )

    failures.extend(phase24d_render_failures(states))

    for state_name in (
        "model-cache-inventory",
        "model-cache-inventory-narrow",
    ):
        inventory = states.get(state_name, {}).get("modelCache", {})
        if inventory.get("open") is not True:
            failures.append(f"state:{state_name}: model-cache disclosure did not open")
        if inventory.get("rowCount") != 3:
            failures.append(f"state:{state_name}: model inventory rows were incomplete")
        if set(inventory.get("stateLabels") or []) != {
            "Cached",
            "Missing",
            "Repair needed",
        }:
            failures.append(f"state:{state_name}: cache-state labels were incomplete")
        if set(inventory.get("actionLabels") or []) != {"Download", "Repair"}:
            failures.append(f"state:{state_name}: explicit Download/Repair actions were missing")
        if inventory.get("technicalCount") != 3:
            failures.append(f"state:{state_name}: model locations and validation details were incomplete")
        if inventory.get("requiredButtonDisabled") is not False:
            failures.append(f"state:{state_name}: required-model action was incorrectly disabled")
        if "1 required model" not in str(
            inventory.get("requiredButtonLabel") or ""
        ):
            failures.append(f"state:{state_name}: required-model consequence was unclear")
        if inventory.get("progressVisible") is not False:
            failures.append(f"state:{state_name}: idle model progress remained visible")
        if inventory.get("errorVisible") is not False:
            failures.append(f"state:{state_name}: idle model error remained visible")

    running_cache = states.get("model-cache-running", {}).get("modelCache", {})
    if running_cache.get("badge") != "Working":
        failures.append("state:model-cache-running: running status was unclear")
    if running_cache.get("progressVisible") is not True:
        failures.append("state:model-cache-running: progress was hidden")
    if "Downloading mlx-community/Qwen3-TTS" not in str(
        running_cache.get("progressLabel") or ""
    ):
        failures.append("state:model-cache-running: current model was not identified")
    if running_cache.get("progressCount") != "0 of 1":
        failures.append("state:model-cache-running: model progress count was wrong")
    if running_cache.get("progressNow") != "0":
        failures.append("state:model-cache-running: progress aria value was wrong")
    if running_cache.get("requiredButtonDisabled") is not True:
        failures.append("state:model-cache-running: concurrent required-model action remained enabled")

    failed_cache = states.get("model-cache-failure", {}).get("modelCache", {})
    if failed_cache.get("errorVisible") is not True:
        failures.append("state:model-cache-failure: actionable failure was hidden")
    if "disk space" not in str(failed_cache.get("errorText") or "").casefold():
        failures.append("state:model-cache-failure: failure did not explain the required action")

    default_character_states = (
        "characters-existing-preparation",
        "characters-existing-preparation-narrow",
        "characters-absent-preparation",
        "voices-speaker-open",
        "voices-clone-open-narrow",
        "voices-controlled-preview-approved",
        "characters-designed-voice",
        "characters-alias",
        "characters-missing-voice",
        "characters-visual-complete",
        "characters-visual-incompatible",
        "characters-long-dense-data",
    )
    for state_name in default_character_states:
        inspector = states.get(state_name, {}).get("characterInspector", {})
        if inspector.get("sectionHeadings") != ["Voice"]:
            failures.append(
                f"state:{state_name}: Voice was not the single primary working section"
            )
        expected_disclosures = {"Appearance", "Character details", "More voice tools"}
        if not expected_disclosures.issubset(
            set(inspector.get("disclosureHeadings") or [])
        ):
            failures.append(
                f"state:{state_name}: compact character disclosures were incomplete"
            )
        if inspector.get("oldTopLevelHeadingCount") != 0:
            failures.append(
                f"state:{state_name}: obsolete Voice persona or Production voice heading returned"
            )
        if inspector.get("createVoicePersonaCount") != 0:
            failures.append(
                f"state:{state_name}: Create voice persona remained visible"
            )
        if inspector.get("visibleCharacterListCount") != 1:
            failures.append(
                f"state:{state_name}: Characters did not retain exactly one visible list"
            )
        if inspector.get("preparationIdentityVisible") is not False:
            failures.append(
                f"state:{state_name}: reference/training fields leaked into the default inspector"
            )

    existing_open = states.get(
        "characters-existing-preparation-open", {}
    ).get("characterInspector", {})
    if existing_open.get("preparationIdentityVisible") is not True:
        failures.append(
            "state:characters-existing-preparation-open: saved preparation identity did not appear inside More voice tools"
        )
    if existing_open.get("descriptionEditable") is not True:
        failures.append(
            "state:characters-existing-preparation-open: draft identity description was not editable"
        )
    if existing_open.get("representativeTextEditable") is not True:
        failures.append(
            "state:characters-existing-preparation-open: draft representative text was not editable"
        )
    if "More voice tools" not in set(existing_open.get("openDisclosures") or []):
        failures.append(
            "state:characters-existing-preparation-open: advanced tools did not open"
        )

    absent_open = states.get(
        "characters-absent-preparation-open", {}
    ).get("characterInspector", {})
    if absent_open.get("preparationIdentityVisible") is not True:
        failures.append(
            "state:characters-absent-preparation-open: setup identity did not appear inside More voice tools"
        )
    if absent_open.get("guidanceDraftButtonVisible") is not True:
        failures.append(
            "state:characters-absent-preparation-open: advanced identity setup action was missing"
        )
    if absent_open.get("descriptionEditable") is not True:
        failures.append(
            "state:characters-absent-preparation-open: identity description was not editable"
        )
    if absent_open.get("representativeTextEditable") is not True:
        failures.append(
            "state:characters-absent-preparation-open: representative text was not editable"
        )

    expected_voice_states = {
        "voices-speaker-open": ("clone", None),
        "voices-clone-open-narrow": ("clone", None),
        "voices-controlled-preview-approved": ("clone", None),
        "characters-designed-voice": ("design", None),
        "characters-alias": ("custom", "NARRATOR"),
        "characters-missing-voice": (None, None),
    }
    for state_name, (voice_type, alias_target) in expected_voice_states.items():
        inspector = states.get(state_name, {}).get("characterInspector", {})
        if inspector.get("voiceType") != voice_type:
            failures.append(
                f"state:{state_name}: expected voice type {voice_type!r} was not rendered"
            )
        if inspector.get("aliasTarget") != alias_target:
            failures.append(
                f"state:{state_name}: alias target did not match expected state"
            )

    unresolved_inspector = states.get(
        "characters-unresolved", {}
    ).get("characterInspector", {})
    if unresolved_inspector.get("state") != "Identity unresolved":
        failures.append(
            "state:characters-unresolved: identity problem state was unclear"
        )
    if "Character details" not in set(
        unresolved_inspector.get("openDisclosures") or []
    ):
        failures.append(
            "state:characters-unresolved: unresolved character details did not open automatically"
        )

    for state_name in (
        "characters-visual-complete",
        "characters-visual-incompatible",
    ):
        inspector = states.get(state_name, {}).get("characterInspector", {})
        if "Appearance" not in set(inspector.get("openDisclosures") or []):
            failures.append(
                f"state:{state_name}: requested appearance state was not inspectable"
            )

    for state_name in ("voices-speaker-open", "voices-clone-open-narrow"):
        clone_editor = states.get(state_name, {}).get("cloneEditor", {})
        if clone_editor.get("speaker") != "THE DOCTOR":
            failures.append(f"state:{state_name}: supplied-clip speaker did not open")
        if clone_editor.get("voiceType") != "clone":
            failures.append(f"state:{state_name}: clone editor did not render")
        if clone_editor.get("savedBackend") != "qwen3_base":
            failures.append(f"state:{state_name}: standard clone was not the saved default")
        if clone_editor.get("cloneOptionsVisible") is not True:
            failures.append(f"state:{state_name}: clone options were not visible")
        if clone_editor.get("labeledFieldCount", 0) < 4:
            failures.append(f"state:{state_name}: clone identity fields were not labeled")
        if "supplied recording" not in str(clone_editor.get("identityCopy") or "").lower():
            failures.append(f"state:{state_name}: supplied identity authority was unclear")
        if not clone_editor.get("referenceText"):
            failures.append(f"state:{state_name}: exact reference transcript was missing")
        if not clone_editor.get("characterStyle"):
            failures.append(f"state:{state_name}: persistent identity note was missing")
        if clone_editor.get("controlledDisclosureRendered") is not True:
            failures.append(
                f"state:{state_name}: controlled clone capability did not load before voice rendering"
            )

    upload_clone = states.get("voices-clone-upload-restored", {}).get(
        "cloneEditor", {}
    )
    upload_audit = upload_clone.get("uploadAudit") or {}
    for field in ("panelOpen", "controlledOpen", "selectedUploadedClone"):
        if upload_audit.get(field) is not True:
            failures.append(
                f"state:voices-clone-upload-restored: failed at {field}"
            )
    if not str(upload_audit.get("referencePath") or "").startswith(
        "clone_voices/audit_uploaded_clone_"
    ):
        failures.append(
            "state:voices-clone-upload-restored: uploaded reference was not selected"
        )

    audio_clone = states.get("voices-clone-play-pause", {}).get(
        "cloneEditor", {}
    )
    audio_audit = audio_clone.get("audioAudit") or {}
    for field in ("pauseShownWhilePlaying", "playShownWhilePaused"):
        if audio_audit.get(field) is not True:
            failures.append(
                f"state:voices-clone-play-pause: failed at {field}"
            )
    if audio_audit.get("pauseLabel") != "Pause reference audio":
        failures.append("state:voices-clone-play-pause: pause label was wrong")
    if audio_audit.get("playLabel") != "Play reference audio":
        failures.append("state:voices-clone-play-pause: play label was wrong")

    # Alias inheritance remains covered by the dedicated VM behavior harness.
    # The roster-led Characters fixture intentionally excludes legacy Script-only
    # speakers such as MARCUS and NARRATOR from the visible character list, so
    # those old Voice-casting-only browser states are no longer meaningful.

    partial_progress = states.get("dataset-progress-partial", {}).get(
        "datasetProgress", {}
    )
    if partial_progress.get("hidden") is not False:
        failures.append("state:dataset-progress-partial: progress remained hidden")
    if partial_progress.get("label") != "1 of 2 complete · 50%":
        failures.append("state:dataset-progress-partial: progress label was not readable")
    if partial_progress.get("barText"):
        failures.append("state:dataset-progress-partial: text remained inside progress bar")
    if partial_progress.get("ariaNow") != "50":
        failures.append("state:dataset-progress-partial: aria progress was wrong")

    approved_clone = states.get("voices-controlled-preview-approved", {}).get(
        "cloneEditor", {}
    )
    approved_audit = approved_clone.get("audit") or {}
    if approved_clone.get("speaker") != "THE DOCTOR":
        failures.append("state:voices-controlled-preview-approved: wrong speaker")
    if approved_clone.get("savedBackend") != "voxcpm2_controlled":
        failures.append(
            "state:voices-controlled-preview-approved: controlled backend did not persist"
        )
    if approved_clone.get("controlledDisclosureVisible") is not True:
        failures.append(
            "state:voices-controlled-preview-approved: controlled disclosure missing"
        )
    if approved_clone.get("controlledBackendStatus") != "Active":
        failures.append(
            "state:voices-controlled-preview-approved: backend status was not active"
        )
    if approved_clone.get("controlledUseButtonDisabled") is not True:
        failures.append(
            "state:voices-controlled-preview-approved: active backend action was not locked"
        )
    if approved_clone.get("controlledUseButtonLabel") != "Active":
        failures.append(
            "state:voices-controlled-preview-approved: active backend action label was wrong"
        )
    if approved_clone.get("controlledStandardButtonHidden") is not False:
        failures.append(
            "state:voices-controlled-preview-approved: standard fallback action was hidden"
        )
    expected_controlled_settings = {
        "controlledCfg": "2.5",
        "controlledSteps": "12",
        "controlledMaxTokens": "1536",
    }
    for field, expected in expected_controlled_settings.items():
        if approved_clone.get(field) != expected:
            failures.append(
                f"state:voices-controlled-preview-approved: {field} did not persist"
            )
    for field in (
        "lockedBeforePlayback",
        "lockedAfterEndedWithoutPlay",
        "lockedAfterPlay",
        "enabledAfterPlayedToEnd",
        "receiptRecorded",
        "savedAfterReceipt",
        "approvalTokenSent",
        "approvalTokenCleared",
    ):
        if approved_audit.get(field) is not True:
            failures.append(
                f"state:voices-controlled-preview-approved: listen gate failed at {field}"
            )
    if approved_clone.get("configurationFingerprint") != (
        "audit-controlled-configuration-fingerprint"
    ):
        failures.append(
            "state:voices-controlled-preview-approved: configuration fingerprint was not bound to the preview"
        )
    if approved_clone.get("approvalTokenPresent") is not False:
        failures.append(
            "state:voices-controlled-preview-approved: one-time approval token remained after save"
        )

    fallback_clone = states.get("voices-controlled-edit-fallback", {}).get(
        "cloneEditor", {}
    )
    fallback_audit = fallback_clone.get("audit") or {}
    if fallback_clone.get("savedBackend") != "qwen3_base":
        failures.append(
            "state:voices-controlled-edit-fallback: standard fallback did not persist"
        )
    if fallback_clone.get("previewFingerprint") is not None:
        failures.append(
            "state:voices-controlled-edit-fallback: preview fingerprint remained approved"
        )
    if fallback_clone.get("previewPlayed") is not None:
        failures.append(
            "state:voices-controlled-edit-fallback: played state remained approved"
        )
    if fallback_clone.get("previewListened") is not None:
        failures.append(
            "state:voices-controlled-edit-fallback: listened state remained approved"
        )
    if fallback_clone.get("controlledStandardButtonHidden") is not True:
        failures.append(
            "state:voices-controlled-edit-fallback: standard fallback action remained visible"
        )
    for field in ("fallbackImmediate", "fallbackPersistedAfterReload"):
        if fallback_audit.get(field) is not True:
            failures.append(
                f"state:voices-controlled-edit-fallback: invalidation failed at {field}"
            )

    for state_name in (
        "setup-runtime-open",
        "setup-advanced-prompts-open",
    ):
        chevrons = states.get(state_name, {}).get("chevrons", [])
        if not any(
            item.get("open") and item.get("transform") not in {None, "none"}
            for item in chevrons
        ):
            failures.append(f"state:{state_name}: open chevron did not rotate")

    dataset_state = states.get("dataset-project-loaded", {})
    if not dataset_state.get("tableShellVisible"):
        failures.append("state:dataset-project-loaded: table shell not visible")
    if dataset_state.get("visibleTableState"):
        failures.append("state:dataset-project-loaded: empty/loading state remained visible")

    if states.get("file-picker-drag-state", {}).get("dragActiveCount", 0) < 1:
        failures.append("state:file-picker-drag-state: drag feedback not visible")
    if not states.get("mobile-navigation-open", {}).get("mobileNavigationOpen"):
        failures.append("state:mobile-navigation-open: navigation did not open")
    if states.get("success-toast", {}).get("visibleToast") != "success":
        failures.append("state:success-toast: success toast not visible")

    confirm_state = states.get("destructive-confirmation", {})
    if confirm_state.get("visibleModal") != "confirmModal":
        failures.append("state:destructive-confirmation: confirmation modal missing")
    if confirm_state.get("modalPrimaryLabel") != "Regenerate":
        failures.append("state:destructive-confirmation: wrong action label")
    if "btn-danger" not in str(confirm_state.get("modalPrimaryClass") or ""):
        failures.append("state:destructive-confirmation: destructive action not styled")

    prompt_state = states.get("text-entry-dialog", {})
    if prompt_state.get("visibleModal") != "textPromptModal":
        failures.append("state:text-entry-dialog: text prompt modal missing")
    if prompt_state.get("modalTitle") != "New dataset project":
        failures.append("state:text-entry-dialog: wrong title")
    if prompt_state.get("modalPrimaryLabel") != "Create project":
        failures.append("state:text-entry-dialog: wrong action label")

    runtime_profile = states.get("llm-profile-runtime-override", {}).get(
        "llmProfiles", {}
    )
    if runtime_profile.get("selectedStage") != "script":
        failures.append("LLM stage-profile selection did not persist")
    if not runtime_profile.get("configured") or not runtime_profile.get("enabled"):
        failures.append("same-model runtime profile was not saved")
    if runtime_profile.get("modelChanged"):
        failures.append("same-model runtime override was incorrectly treated as a model change")
    if runtime_profile.get("contextOverride") != 8192:
        failures.append("stage-specific context override did not persist")
    if runtime_profile.get("evidenceVisible"):
        failures.append("same-model runtime override incorrectly requires evidence")

    evidence_gate = states.get("llm-profile-model-evidence-required", {}).get(
        "llmProfiles", {}
    )
    if not evidence_gate.get("evidenceVisible"):
        failures.append("model-changing profile did not reveal the evidence gate")
    if evidence_gate.get("modelChanged"):
        failures.append("unverified model-changing profile was persisted")
    if evidence_gate.get("modelInput") != "qwen3.5:32b-unverified":
        failures.append("model-change evidence state did not retain the attempted model")

    removed_profile = states.get("llm-profile-removed", {}).get(
        "llmProfiles", {}
    )
    if removed_profile.get("configured"):
        failures.append("stage-profile removal did not return to inheritance")
    if removed_profile.get("inheritsGlobal") is not True:
        failures.append("removed stage profile does not inherit the global runtime")

    renamed_speaker = states.get("speaker-management-renamed", {}).get(
        "speakerManagement", {}
    )
    if renamed_speaker.get("selectedName") != "THE TRAVELER":
        failures.append("speaker-management rename did not persist")
    if renamed_speaker.get("historyCount", 0) < 1:
        failures.append("speaker-management rename was not recorded in history")
    if renamed_speaker.get("selectedLineCount", 0) < 1:
        failures.append("speaker-management line inspection did not survive rename")
    if renamed_speaker.get("latestAudioInvalidationCount", 0) < 1:
        failures.append("speaker-management rename did not record audio invalidation")

    restored_speaker = states.get("speaker-management-undo", {}).get(
        "speakerManagement", {}
    )
    if restored_speaker.get("selectedName") != "THE DOCTOR":
        failures.append("speaker-management undo did not restore the prior identity")
    if (
        renamed_speaker.get("selectedEntryId")
        and restored_speaker.get("selectedEntryId")
        and renamed_speaker["selectedEntryId"] != restored_speaker["selectedEntryId"]
    ):
        failures.append("speaker-management undo changed stable character identity")

    capability = states.get("voice-capability-adapter-open", {}).get(
        "voiceCapabilities", {}
    )
    if capability.get("stableOutcome") != "unsupported":
        failures.append("voice capability did not report the measured integrated LoRA outcome")
    if capability.get("trainingSupported") is not False:
        failures.append("unsupported in-process LoRA training was not fail-closed")
    if capability.get("sidecarTrainingSupported") is not True:
        failures.append("validated isolated MPS LoRA training was not surfaced")
    if capability.get("inferenceSupported") is not True:
        failures.append("validated standalone MLX LoRA inference was not surfaced")
    if capability.get("controlledCloneSupported") is not True:
        failures.append("measured controlled supplied-clip clone was not surfaced")
    if capability.get("measurementRows", 0) < 7:
        failures.append("all measured expressive inference paths were not rendered")
    if capability.get("trainingControlsDisabled") is not False:
        failures.append("validated isolated adapter controls remained disabled")
    if capability.get("trainingButtonDisabled") is not False:
        failures.append("validated isolated training action remained disabled")
    if capability.get("trainingButtonLabel") != "Train, validate, and install":
        failures.append("LoRA training action does not describe the complete pipeline")
    if capability.get("targetProfilePresent") is not True:
        failures.append("LoRA target-profile control was not served")
    if capability.get("validationFractionPresent") is not True:
        failures.append("LoRA validation control was not served")
    if capability.get("testFormVisible") is not True:
        failures.append("validated adapter test form remained hidden")
    if capability.get("primaryLabel") != "Open Characters":
        failures.append("Characters is not the primary voice-training handoff")

    warning_borders = states.get("voice-project-source-warning", {}).get(
        "noticeBorders"
    )
    if warning_borders and len(set(warning_borders.values())) != 1:
        failures.append("warning notice uses asymmetric side-stripe border")

    approved = states.get("voice-project-persona-approved", {}).get(
        "voiceProject", {}
    )
    if approved.get("personaStatus") != "approved":
        failures.append("voice-project persona approval did not persist")
    synthetic = states.get("voice-project-synthetic-created", {}).get(
        "voiceProject", {}
    )
    if synthetic.get("syntheticStatus") != "draft":
        failures.append("voice-project synthetic path was not created")
    if not synthetic.get("fingerprint"):
        failures.append("voice-project mutation did not refresh fingerprint")

    reference_bank = states.get(
        "voice-project-reference-bank-review", {}
    ).get("voiceProject", {})
    if reference_bank.get("referenceBankVisible") is not True:
        failures.append(
            "state:voice-project-reference-bank-review: native reference-bank section was hidden"
        )
    if reference_bank.get("referenceBankStatus") != "draft":
        failures.append(
            "state:voice-project-reference-bank-review: draft status was not rendered"
        )
    if reference_bank.get("referenceBankStyleCount") != 2:
        failures.append(
            "state:voice-project-reference-bank-review: required style cards were incomplete"
        )
    if reference_bank.get("referenceBankAudioCount") != 5:
        failures.append(
            "state:voice-project-reference-bank-review: reference or comparison audio controls were missing"
        )
    if reference_bank.get("referenceBankComparisonOutputCount") != 3:
        failures.append(
            "state:voice-project-reference-bank-review: fixed three-mode comparison was incomplete"
        )
    if reference_bank.get("referenceBankReviewButtonCount") != 2:
        failures.append(
            "state:voice-project-reference-bank-review: per-reference listening actions were missing"
        )
    if reference_bank.get("referenceBankApproveDisabled") is not True:
        failures.append(
            "state:voice-project-reference-bank-review: incomplete listening gates did not block approval"
        )
    if reference_bank.get("referenceBankAssignVisible") is not False:
        failures.append(
            "state:voice-project-reference-bank-review: production assignment appeared before approval"
        )
    reference_copy = str(
        reference_bank.get("referenceBankIdentityCopy") or ""
    ).casefold()
    for phrase in (
        "identity authority",
        "owned recording",
        "direct design comparator",
        "approve every required style",
    ):
        if phrase not in reference_copy:
            failures.append(
                "state:voice-project-reference-bank-review: "
                f"missing workflow copy {phrase!r}"
            )
    if reference_bank.get("referenceBankSummaryColumns") != 4:
        failures.append(
            "state:voice-project-reference-bank-review: desktop summary did not use four columns"
        )
    if reference_bank.get("referenceBankStyleColumns") != 1:
        failures.append(
            "state:voice-project-reference-bank-review: style references did not remain plain full-width rows"
        )

    narrow_reference_bank = states.get(
        "voice-project-reference-bank-review-narrow", {}
    ).get("voiceProject", {})
    if narrow_reference_bank.get("referenceBankVisible") is not True:
        failures.append(
            "state:voice-project-reference-bank-review-narrow: reference bank disappeared"
        )
    if narrow_reference_bank.get("referenceBankSummaryColumns") != 2:
        failures.append(
            "state:voice-project-reference-bank-review-narrow: summary did not collapse to two columns"
        )
    if narrow_reference_bank.get("referenceBankStyleColumns") != 1:
        failures.append(
            "state:voice-project-reference-bank-review-narrow: style reviews did not collapse to one column"
        )

    if mode == "legacy":
        failures = [
            failure
            for failure in failures
            if not failure.startswith("state:new-project")
        ]

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "output_dir": str(output_dir),
        "audit": payload,
    }



def _augment_voice_library_fixture(root: Path) -> None:
    config_path = root / "voice_config.json"
    if not config_path.is_file():
        return
    value = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return
    clone_target = next(
        (
            (key, dict(item))
            for key, item in value.items()
            if isinstance(item, dict)
            and item.get("type") == "clone"
            and item.get("ref_audio")
            and item.get("ref_text")
        ),
        None,
    )
    if clone_target is None:
        return
    target_key, controlled = clone_target
    controlled["clone_backend"] = "qwen3_instruction_controlled"
    controlled["character_style"] = (
        controlled.get("character_style")
        or controlled.get("default_style")
        or "Preserve the supplied speaker identity and accent."
    )
    controlled.pop("controlled_clone_configuration_fingerprint", None)
    value["VOICE LIBRARY CONTROLLED QA"] = controlled
    value["VOICE LIBRARY ALIAS QA"] = {
        "type": "alias",
        "alias_of": target_key,
    }
    config_path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/alexandria-interface-audit"),
    )
    parser.add_argument(
        "--mode",
        choices=("legacy", "shell", "boundary12", "boundary13", "boundary13-final"),
        default="legacy",
    )
    args = parser.parse_args()
    report = run(args.repo_root, args.output_dir, mode=args.mode)
    print(REPORT_PREFIX + json.dumps(report, sort_keys=True))
    return 0 if report["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
