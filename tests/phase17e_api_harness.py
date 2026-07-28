from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any


REPORT_PREFIX = "PHASE17E_REPORT="
PROMPT_FILES = (
    "default_prompts.txt",
    "review_prompts.txt",
    "persona_prompts.txt",
)
RUNTIME_DIRECTORIES = (
    "scripts",
    "designed_voices",
    "clone_voices",
    "lora_models",
    "lora_datasets",
    "builtin_lora",
    "dataset_builder",
    "preparer_output",
    "voicelines",
)


def _copy_fixture(source_root: Path, target_root: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {
            name
            for name in names
            if name in {
                "env",
                "uploads",
                "__pycache__",
                "config.json",
            }
            or name.endswith((".pyc", ".pyo"))
        }
        return ignored

    shutil.copytree(
        source_root / "app",
        target_root / "app",
        ignore=ignore,
    )
    shutil.copytree(
        source_root / "docs" / "help",
        target_root / "docs" / "help",
    )

    for filename in PROMPT_FILES:
        shutil.copy2(
            source_root / filename,
            target_root / filename,
        )

    for directory in RUNTIME_DIRECTORIES:
        (target_root / directory).mkdir(
            parents=True,
            exist_ok=True,
        )

    (target_root / "app" / "uploads").mkdir(
        parents=True,
        exist_ok=True,
    )

    config = {
        "llm": {
            "backend": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "local-test-only",
            "model_name": "qwen3.5:35b-mlx",
            "thinking": False,
            "structured_output": True,
            "corrective_retry": True,
            "timeout": 1,
            "keep_alive": "0",
            "context_length": 4096,
        },
        "generation": {
            "chunk_size": 80,
            "max_tokens": 256,
            "temperature": 0.0,
            "top_p": 1.0,
            "top_k": 0,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "banned_tokens": [],
        },
        "prompts": {},
        "tts": {
            "mode": "local",
            "url": "http://127.0.0.1:9",
        },
    }
    (target_root / "app" / "config.json").write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )


def _digest(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_hashes(root: Path) -> dict[str, str]:
    names = (
        "state.json",
        "generation_state.json",
        "annotated_script.json",
        "annotated_script.meta.json",
        "chunks.json",
        "voice_config.json",
        "app/config.json",
    )
    return {
        name: _digest(root / name)
        for name in names
    }


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _require(
    checks: dict[str, dict[str, Any]],
    name: str,
    condition: bool,
    **details: Any,
) -> None:
    checks[name] = {
        "ok": bool(condition),
        **details,
    }
    if not condition:
        raise AssertionError(
            f"Phase 17E API check failed: {name}: {details}"
        )


def run_harness(repo_root: Path) -> dict[str, Any]:
    source_root = repo_root.resolve()
    checks: dict[str, dict[str, Any]] = {}

    with tempfile.TemporaryDirectory(
        prefix="alexandria-phase17e-api-"
    ) as temporary:
        fixture_root = Path(temporary).resolve()
        _copy_fixture(source_root, fixture_root)

        old_cwd = Path.cwd()
        old_path = list(sys.path)
        old_data_root = os.environ.get("ALEXANDRIA_DATA_ROOT")
        os.environ["ALEXANDRIA_DATA_ROOT"] = str(
            fixture_root / "application-data"
        )
        sys.dont_write_bytecode = True

        try:
            os.chdir(fixture_root / "app")
            sys.path.insert(0, str(fixture_root / "app"))

            from fastapi.testclient import TestClient

            import app as app_module
            from generation_metadata import build_generation_metadata
            from generation_state import (
                atomic_json_write,
                checkpoint_completed_chunk,
                fingerprint_text,
                new_generation_state,
            )

            recorded_commands: list[dict[str, Any]] = []

            async def fake_run_process(
                command: list[str],
                task_name: str,
            ) -> None:
                recorded_commands.append(
                    {
                        "command": list(command),
                        "task_name": task_name,
                    }
                )

            app_module.run_process = fake_run_process

            state_path = fixture_root / "state.json"
            checkpoint_path = (
                fixture_root / "generation_state.json"
            )
            script_path = fixture_root / "annotated_script.json"
            metadata_path = (
                fixture_root / "annotated_script.meta.json"
            )
            chunks_path = fixture_root / "chunks.json"
            voice_path = fixture_root / "voice_config.json"
            uploads_dir = fixture_root / "app" / "uploads"
            scripts_dir = fixture_root / "scripts"

            def reset_runtime() -> None:
                for path in (
                    state_path,
                    checkpoint_path,
                    script_path,
                    metadata_path,
                    chunks_path,
                    voice_path,
                ):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass

                for directory in (uploads_dir, scripts_dir):
                    for child in directory.iterdir():
                        if child.is_dir():
                            shutil.rmtree(child)
                        else:
                            child.unlink()

                app_module.process_state["script"] = {
                    "running": False,
                    "logs": [],
                }
                app_module.process_state["audio"]["running"] = False
                recorded_commands.clear()

            def make_source(
                name: str = "source.txt",
                content: str | None = None,
            ) -> Path:
                source = uploads_dir / name
                source.write_text(
                    content
                    or "\n\n".join(
                        (
                            f"Paragraph {index}: "
                            + "The exact source text must remain stable. "
                            + "Dialogue and narration retain their order."
                        )
                        for index in range(1, 13)
                    ),
                    encoding="utf-8",
                )
                return source

            def select_source(source: Path) -> dict[str, Any]:
                _json_write(
                    state_path,
                    {"input_file_path": str(source)},
                )
                return app_module.build_script_generation_snapshot(
                    str(source)
                )

            def write_checkpoint(
                snapshot: dict[str, Any],
                completed: int,
            ) -> dict[str, Any]:
                state = new_generation_state(
                    source_fingerprint=(
                        snapshot["source_fingerprint"]
                    ),
                    generation_fingerprint=(
                        snapshot["generation_fingerprint"]
                    ),
                    chunk_fingerprints=(
                        snapshot["chunk_fingerprints"]
                    ),
                    generation_identity=(
                        snapshot["generation_identity"]
                    ),
                    source={
                        "path": snapshot["source_path"],
                        "basename": snapshot[
                            "source_basename"
                        ],
                    },
                    auditor_contract_version=(
                        snapshot["auditor_contract_version"]
                    ),
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
                                "text": f"Completed chunk {index}.",
                                "instruct": "Neutral narration.",
                            }
                        ],
                    )

                return state

            fixture_files = {
                filename: (fixture_root / filename).exists()
                for filename in PROMPT_FILES
            }
            _require(
                checks,
                "fixture_manifest",
                all(fixture_files.values())
                and (fixture_root / "app" / "app.py").exists()
                and (fixture_root / "app" / "static" / "index.html").exists(),
                prompt_files=fixture_files,
            )

            with TestClient(app_module.app) as client:
                response = client.get("/")
                markup = response.text
                script_response = client.get("/static/pages/script.js")
                script_source = script_response.text
                required_markup = (
                    "canonical-destination-root",
                    "/static/app_shell.js",
                    "data-shell-mount",
                )
                required_script = (
                    "owner.dataset.page = route.path",
                    "/api/script_lifecycle/status",
                    "/api/annotated_script",
                    "Approve Script",
                    "data-script-approve",
                )
                _require(
                    checks,
                    "served_script_controls",
                    response.status_code == 200
                    and script_response.status_code == 200
                    and all(token in markup for token in required_markup)
                    and all(token in script_source for token in required_script),
                    status_code=response.status_code,
                    script_status_code=script_response.status_code,
                    required_markup=list(required_markup),
                    required_script=list(required_script),
                )

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                _require(
                    checks,
                    "snapshot_has_multiple_chunks",
                    snapshot["total_chunks"] >= 3,
                    total_chunks=snapshot["total_chunks"],
                )
                status_response = client.get(
                    "/api/script_generation/status"
                )
                status = status_response.json()
                _require(
                    checks,
                    "initial_no_checkpoint_state",
                    status_response.status_code == 200
                    and status["checkpoint"]["status"] == "none"
                    and status["result"]["status"] == "missing",
                    status=status,
                )
                response = client.post(
                    "/api/generate_script",
                    json={},
                )
                new_payload = response.json()
                _require(
                    checks,
                    "new_generation_mode",
                    response.status_code == 200
                    and new_payload["mode"] == "new"
                    and bool(recorded_commands)
                    and "--finalize-only"
                    not in recorded_commands[-1]["command"],
                    response=new_payload,
                    command=(
                        recorded_commands[-1]
                        if recorded_commands
                        else None
                    ),
                )

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                write_checkpoint(snapshot, 1)
                response = client.post(
                    "/api/generate_script",
                    json={},
                )
                resume_payload = response.json()
                _require(
                    checks,
                    "resume_generation_mode",
                    response.status_code == 200
                    and resume_payload["mode"] == "resume"
                    and resume_payload["completed_chunks"] == 1
                    and resume_payload["next_chunk"] == 2
                    and bool(recorded_commands)
                    and "--finalize-only"
                    not in recorded_commands[-1]["command"],
                    response=resume_payload,
                    command=(
                        recorded_commands[-1]
                        if recorded_commands
                        else None
                    ),
                )

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                write_checkpoint(
                    snapshot,
                    snapshot["total_chunks"],
                )
                response = client.post(
                    "/api/generate_script",
                    json={},
                )
                finalize_payload = response.json()
                _require(
                    checks,
                    "finalization_generation_mode",
                    response.status_code == 200
                    and finalize_payload["mode"] == "finalize"
                    and bool(recorded_commands)
                    and "--finalize-only"
                    in recorded_commands[-1]["command"],
                    response=finalize_payload,
                    command=(
                        recorded_commands[-1]
                        if recorded_commands
                        else None
                    ),
                )

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                write_checkpoint(snapshot, 1)
                source.write_text(
                    source.read_text(encoding="utf-8")
                    + "\nChanged source text.",
                    encoding="utf-8",
                )
                response = client.post(
                    "/api/generate_script",
                    json={},
                )
                incompatible_detail = response.json().get(
                    "detail",
                    {},
                )
                _require(
                    checks,
                    "incompatible_checkpoint_blocked",
                    response.status_code == 409
                    and incompatible_detail.get(
                        "checkpoint_status"
                    )
                    == "incompatible"
                    and "source_changed"
                    in incompatible_detail.get(
                        "reason_codes",
                        [],
                    )
                    and not recorded_commands,
                    detail=incompatible_detail,
                )

                reset_runtime()
                source = make_source()
                select_source(source)
                checkpoint_path.write_text(
                    "{not valid json",
                    encoding="utf-8",
                )
                response = client.post(
                    "/api/generate_script",
                    json={},
                )
                corrupt_detail = response.json().get(
                    "detail",
                    {},
                )
                _require(
                    checks,
                    "corrupt_checkpoint_blocked",
                    response.status_code == 409
                    and corrupt_detail.get(
                        "checkpoint_status"
                    )
                    == "corrupt"
                    and not recorded_commands,
                    detail=corrupt_detail,
                )

                reset_runtime()
                source = make_source()
                select_source(source)
                _json_write(checkpoint_path, [])
                response = client.post(
                    "/api/generate_script",
                    json={},
                )
                invalid_detail = response.json().get(
                    "detail",
                    {},
                )
                _require(
                    checks,
                    "invalid_checkpoint_blocked",
                    response.status_code == 409
                    and invalid_detail.get(
                        "checkpoint_status"
                    )
                    == "invalid"
                    and not recorded_commands,
                    detail=invalid_detail,
                )

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                write_checkpoint(snapshot, 1)
                missing_source = uploads_dir / "missing.txt"
                _json_write(
                    state_path,
                    {"input_file_path": str(missing_source)},
                )
                response = client.post(
                    "/api/generate_script",
                    json={},
                )
                unknown_detail = response.json().get(
                    "detail",
                    {},
                )
                _require(
                    checks,
                    "unknown_checkpoint_blocked",
                    response.status_code == 409
                    and unknown_detail.get(
                        "checkpoint_status"
                    )
                    == "unknown"
                    and "current_inputs_unavailable"
                    in unknown_detail.get(
                        "reason_codes",
                        [],
                    )
                    and not recorded_commands,
                    detail=unknown_detail,
                )

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                write_checkpoint(snapshot, 1)
                app_module.process_state["script"][
                    "running"
                ] = True
                generate_response = client.post(
                    "/api/generate_script",
                    json={},
                )
                discard_response = client.post(
                    "/api/script_generation/discard",
                    json={},
                )
                _require(
                    checks,
                    "running_process_guards",
                    generate_response.status_code == 409
                    and discard_response.status_code == 409
                    and checkpoint_path.exists()
                    and not recorded_commands,
                    generate=generate_response.json(),
                    discard=discard_response.json(),
                )
                app_module.process_state["script"][
                    "running"
                ] = False

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                write_checkpoint(snapshot, 1)
                script_bytes = b'[{"speaker":"NARRATOR","text":"Existing.","instruct":"Neutral."}]\n'
                metadata_bytes = b'{"sentinel":"keep"}\n'
                script_path.write_bytes(script_bytes)
                metadata_path.write_bytes(metadata_bytes)
                response = client.post(
                    "/api/script_generation/discard",
                    json={},
                )
                absent_response = client.post(
                    "/api/script_generation/discard",
                    json={},
                )
                _require(
                    checks,
                    "checkpoint_only_discard",
                    response.status_code == 200
                    and response.json()["status"] == "discarded"
                    and absent_response.status_code == 200
                    and absent_response.json()["status"] == "absent"
                    and not checkpoint_path.exists()
                    and script_path.read_bytes() == script_bytes
                    and metadata_path.read_bytes()
                    == metadata_bytes,
                    first=response.json(),
                    second=absent_response.json(),
                )

                reset_runtime()
                source = make_source()
                snapshot = select_source(source)
                write_checkpoint(snapshot, 1)
                before_hashes = _runtime_hashes(fixture_root)
                first_status = client.get(
                    "/api/script_generation/status"
                )
                second_status = client.get(
                    "/api/script_generation/status"
                )
                after_hashes = _runtime_hashes(fixture_root)
                _require(
                    checks,
                    "status_read_file_purity",
                    first_status.status_code == 200
                    and second_status.status_code == 200
                    and before_hashes == after_hashes,
                    before=before_hashes,
                    after=after_hashes,
                )

                reset_runtime()
                response = client.post(
                    "/api/upload",
                    files={
                        "file": (
                            "uploaded.txt",
                            b"Uploaded source text.",
                            "text/plain",
                        )
                    },
                )
                upload_payload = response.json()
                uploaded_path = Path(
                    upload_payload.get("path", "")
                ).resolve()
                selected_state = json.loads(
                    state_path.read_text(encoding="utf-8")
                )
                _require(
                    checks,
                    "upload_selection",
                    response.status_code == 200
                    and uploaded_path.is_relative_to(fixture_root)
                    and uploaded_path.read_bytes()
                    == b"Uploaded source text."
                    and selected_state["input_file_path"]
                    == str(uploaded_path),
                    response=upload_payload,
                    selected_state=selected_state,
                )

                reset_runtime()
                saved_entries = [
                    {
                        "speaker": "NARRATOR",
                        "text": "Saved script text.",
                        "instruct": "Neutral narration.",
                    },
                    {
                        "speaker": "DOCTOR",
                        "text": "Saved dialogue.",
                        "instruct": "Measured urgency.",
                    },
                ]
                saved_source = make_source(
                    "saved-source.txt",
                    "Saved source text.",
                )
                saved_metadata = build_generation_metadata(
                    source_path=saved_source,
                    source_fingerprint=fingerprint_text(
                        saved_source.read_text(encoding="utf-8")
                    ),
                    source_character_count=len(
                        saved_source.read_text(encoding="utf-8")
                    ),
                    source_chunk_count=1,
                    generation_fingerprint="generation-test",
                    generation_identity={
                        "model_name": "qwen3.5:35b-mlx",
                        "backend": "ollama",
                    },
                    entries=saved_entries,
                    resumed=True,
                    previously_completed_chunks=1,
                    generated_at_utc="2026-07-16T12:00:00Z",
                )
                _json_write(
                    scripts_dir / "demo.json",
                    saved_entries,
                )
                _json_write(
                    scripts_dir / "demo.meta.json",
                    saved_metadata,
                )
                list_response = client.get("/api/scripts")
                load_response = client.post(
                    "/api/scripts/load",
                    json={"name": "demo"},
                )
                saved_status_response = client.get(
                    "/api/script_generation/status"
                )
                annotated_response = client.get(
                    "/api/annotated_script"
                )
                saved_status = saved_status_response.json()
                records = list_response.json()
                _require(
                    checks,
                    "saved_script_provenance_status",
                    list_response.status_code == 200
                    and records
                    and records[0]["name"] == "demo"
                    and records[0]["has_metadata"] is True
                    and load_response.status_code == 200
                    and saved_status_response.status_code == 200
                    and saved_status["result"]["status"]
                    == "complete"
                    and saved_status["result"]["metadata"][
                        "source"
                    ]["basename"]
                    == "saved-source.txt"
                    and saved_status["result"]["metadata"][
                        "generation"
                    ]["effective_identity"]["model_name"]
                    == "qwen3.5:35b-mlx",
                    records=records,
                    load=load_response.json(),
                    status=saved_status,
                )
                _require(
                    checks,
                    "annotated_script_plain_array",
                    annotated_response.status_code == 200
                    and annotated_response.json()
                    == saved_entries
                    and isinstance(
                        annotated_response.json(),
                        list,
                    ),
                    response=annotated_response.json(),
                )

            written_paths = [
                path.resolve()
                for path in fixture_root.rglob("*")
                if path.is_file()
            ]
            escaped_paths = [
                str(path)
                for path in written_paths
                if not path.is_relative_to(fixture_root)
            ]
            _require(
                checks,
                "temporary_root_confinement",
                not escaped_paths,
                escaped_paths=escaped_paths,
                fixture_root=str(fixture_root),
            )

        finally:
            if old_data_root is None:
                os.environ.pop("ALEXANDRIA_DATA_ROOT", None)
            else:
                os.environ["ALEXANDRIA_DATA_ROOT"] = old_data_root
            os.chdir(old_cwd)
            sys.path[:] = old_path

    return {
        "status": "PASS",
        "checks": checks,
        "check_count": len(checks),
        "source_root": str(source_root),
        "fixture_destroyed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
    )
    args = parser.parse_args()

    try:
        report = run_harness(args.repo_root)
    except Exception:
        traceback.print_exc()
        return 1

    print(
        REPORT_PREFIX
        + json.dumps(
            report,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
