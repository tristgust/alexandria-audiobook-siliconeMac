from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from audio_invalidation import apply_project_audio_invalidation
from generation_state import atomic_json_write, fingerprint_value
from voice_aliases import validate_voice_aliases


PENDING_VOICE_IMPORT_SCHEMA_VERSION = 1
PENDING_VOICE_IMPORT_FILENAME = ".alexandria-pending-voice-imports.json"
VOICE_IMPORT_RECEIPTS_DIRNAME = "voice_import_receipts"
_ALLOWED_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".flac", ".m4a", ".ogg"})
_SAFE_DESTINATION_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")


class PendingVoiceImportError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PendingVoiceImportError(f"{label} could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise PendingVoiceImportError(f"{label} must contain a JSON object.")
    return value


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PendingVoiceImportError(f"Clone voice manifest could not be read: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PendingVoiceImportError("Clone voice manifest must contain a JSON array of objects.")
    return copy.deepcopy(value)


def _atomic_bytes(path: Path, value: bytes | None) -> None:
    if value is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_handle, temporary.open("wb") as output_handle:
            for block in iter(lambda: input_handle.read(1024 * 1024), b""):
                output_handle.write(block)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _require_text(value: Any, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PendingVoiceImportError(f"{label} is required.")
    text = value.strip()
    if len(text) > maximum:
        raise PendingVoiceImportError(f"{label} is too long.")
    return text


def _normalize_sha256(value: Any, label: str) -> str:
    expected = _require_text(value, label, maximum=64).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise PendingVoiceImportError(f"{label} is invalid.")
    return expected


def _normalize_source_segments(value: Any, *, index: int, speaker: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > 16:
        raise PendingVoiceImportError(
            f"Voice import {index + 1} source_segments must contain between 1 and 16 entries."
        )
    result: list[dict[str, str]] = []
    for segment_index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise PendingVoiceImportError(
                f"Voice import {index + 1} source segment {segment_index + 1} must be an object."
            )
        url = _require_text(
            raw.get("url"),
            f"Voice import {index + 1} source segment {segment_index + 1} URL",
            maximum=4096,
        )
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise PendingVoiceImportError(
                f"Voice import {index + 1} source segment {segment_index + 1} must use HTTPS."
            )
        expected_hash = _normalize_sha256(
            raw.get("sha256"),
            f"Voice import {index + 1} source segment {segment_index + 1} SHA-256",
        )
        result.append({"url": url, "sha256": expected_hash})
    return result


def _normalize_entry(value: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PendingVoiceImportError(f"Voice import {index + 1} must be an object.")
    speaker = _require_text(value.get("speaker"), f"Voice import {index + 1} speaker", maximum=160)
    source_audio = value.get("source_audio")
    source_segments_value = value.get("source_segments")
    if bool(source_audio) == bool(source_segments_value):
        raise PendingVoiceImportError(
            f"Voice import {index + 1} must provide exactly one of source_audio or source_segments."
        )
    source: Path | None = None
    expected_hash: str | None = None
    source_segments: list[dict[str, str]] = []
    if source_audio:
        source_text = _require_text(
            source_audio,
            f"Voice import {index + 1} source audio",
            maximum=2048,
        )
        source = Path(source_text).expanduser().resolve()
        if not source.is_absolute() or not source.is_file() or source.is_symlink():
            raise PendingVoiceImportError(f"Source audio for {speaker} is unavailable or unsafe.")
        if source.suffix.casefold() not in _ALLOWED_AUDIO_SUFFIXES:
            raise PendingVoiceImportError(f"Source audio format for {speaker} is unsupported.")
        expected_hash = _normalize_sha256(
            value.get("source_sha256"),
            f"Voice import {index + 1} source SHA-256",
        )
        actual_hash = sha256_file(source)
        if actual_hash != expected_hash:
            raise PendingVoiceImportError(
                f"Source audio for {speaker} changed; expected {expected_hash}, got {actual_hash}."
            )
    else:
        source_segments = _normalize_source_segments(
            source_segments_value,
            index=index,
            speaker=speaker,
        )
    destination_filename = _require_text(
        value.get("destination_filename"),
        f"Voice import {index + 1} destination filename",
        maximum=128,
    ).casefold()
    if not _SAFE_DESTINATION_RE.fullmatch(destination_filename):
        raise PendingVoiceImportError(f"Destination filename for {speaker} is invalid.")
    if Path(destination_filename).suffix.casefold() not in _ALLOWED_AUDIO_SUFFIXES:
        raise PendingVoiceImportError(f"Destination audio format for {speaker} is unsupported.")
    if source_segments and Path(destination_filename).suffix.casefold() != ".wav":
        raise PendingVoiceImportError(
            f"Composite source segments for {speaker} must target a WAV destination."
        )
    transcript = _require_text(
        value.get("transcript"), f"Voice import {index + 1} transcript", maximum=12000
    )
    display_name = str(value.get("display_name") or speaker).strip() or speaker
    source_url = str(value.get("source_url") or "").strip() or None
    reusable_configuration_key = _require_text(
        value.get("reusable_configuration_key") or speaker,
        f"Voice import {index + 1} reusable configuration key",
        maximum=160,
    )
    raw_assign_speakers = value.get("assign_speakers")
    if raw_assign_speakers is None:
        assign_speakers = [speaker]
    elif not isinstance(raw_assign_speakers, list) or not raw_assign_speakers:
        raise PendingVoiceImportError(
            f"Voice import {index + 1} assign_speakers must be a non-empty array."
        )
    else:
        assign_speakers = [
            _require_text(
                item,
                f"Voice import {index + 1} assignment speaker",
                maximum=160,
            )
            for item in raw_assign_speakers
        ]
    if len(assign_speakers) > 32 or len({item.casefold() for item in assign_speakers}) != len(assign_speakers):
        raise PendingVoiceImportError(
            f"Voice import {index + 1} has invalid or duplicate assignment speakers."
        )
    return {
        "speaker": speaker,
        "source": source,
        "source_sha256": expected_hash,
        "source_segments": source_segments,
        "destination_filename": destination_filename,
        "transcript": transcript,
        "display_name": display_name,
        "source_url": source_url,
        "reusable_configuration_key": reusable_configuration_key,
        "assign_speakers": assign_speakers,
    }


def _download_segment_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Alexandria/1.0 voice-import"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 25 * 1024 * 1024:
                raise PendingVoiceImportError("Remote voice segment is too large.")
            data = response.read(25 * 1024 * 1024 + 1)
    except (OSError, ValueError) as exc:
        raise PendingVoiceImportError(
            f"Remote voice segment could not be downloaded: {exc}"
        ) from exc
    if not data or len(data) > 25 * 1024 * 1024:
        raise PendingVoiceImportError("Remote voice segment is empty or too large.")
    return data


def _decode_audio_to_pcm(audio_bytes: bytes) -> bytes:
    try:
        process = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                "pipe:1",
            ],
            input=audio_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PendingVoiceImportError(
            f"Remote voice segment could not be decoded: {exc}"
        ) from exc
    if process.returncode != 0 or not process.stdout:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise PendingVoiceImportError(
            "Remote voice segment could not be decoded"
            + (f": {detail}" if detail else ".")
        )
    return process.stdout


def _materialize_entry_source(
    item: Mapping[str, Any],
    *,
    temporary_paths: list[Path],
) -> tuple[Path, str]:
    source = item.get("source")
    if isinstance(source, Path):
        expected = str(item.get("source_sha256") or "")
        return source, expected
    segments = item.get("source_segments")
    if not isinstance(segments, list) or not segments:
        raise PendingVoiceImportError(
            f"Voice import for {item.get('speaker')!r} has no source audio."
        )
    pcm_segments: list[bytes] = []
    for segment in segments:
        url = str(segment["url"])
        expected_hash = str(segment["sha256"])
        payload = _download_segment_bytes(url)
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != expected_hash:
            raise PendingVoiceImportError(
                f"Remote voice segment changed; expected {expected_hash}, got {actual_hash}."
            )
        pcm_segments.append(_decode_audio_to_pcm(payload))
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="alexandria-voice-composite-",
        suffix=".wav",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary_paths.append(temporary)
    silence = b"\x00\x00" * int(24000 * 0.25)
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        for segment_index, pcm in enumerate(pcm_segments):
            if segment_index:
                handle.writeframes(silence)
            handle.writeframes(pcm)
    return temporary, sha256_file(temporary)


def _collect_project_character_names(root: Path) -> set[str]:
    names: set[str] = set()
    recognized_keys = {
        "speaker",
        "name",
        "canonical_name",
        "display_name",
        "script_label",
        "resolved_script_voice_label",
    }

    def visit(value: Any, key: str | None = None) -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
            return
        if isinstance(value, list):
            for child in value:
                visit(child, key)
            return
        if key in recognized_keys and isinstance(value, str) and value.strip():
            names.add(value.strip().casefold())

    for filename in (
        "character_roster.json",
        "character_roster.draft.json",
        "annotated_script.json",
    ):
        path = root / filename
        if not path.is_file():
            continue
        try:
            visit(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return names


def _reusable_voice_id(configuration_key: str) -> str:
    digest = hashlib.sha256(
        f"supplied_recording\x1freusable:{configuration_key}".encode("utf-8")
    ).hexdigest()[:24]
    return f"voice_{digest}"


def _simple_clone_config(existing: Mapping[str, Any], *, ref_audio: str, ref_text: str) -> dict[str, Any]:
    updated = copy.deepcopy(dict(existing))
    updated.update(
        {
            "alias_of": None,
            "library_voice_id": None,
            "type": "clone",
            "voice": "Ryan",
            "character_style": "",
            "default_style": "",
            "seed": "-1",
            "ref_audio": ref_audio,
            "ref_text": ref_text,
            "clone_backend": "qwen3_base",
            "expressive_clone_cfg_value": 2.0,
            "expressive_clone_steps": 10,
            "expressive_clone_max_tokens": 2000,
            "instruction_clone_temperature": 0.75,
            "instruction_clone_top_k": 50,
            "instruction_clone_top_p": 0.95,
            "instruction_clone_repetition_penalty": 1.5,
            "instruction_clone_max_tokens": 2000,
            "controlled_clone_configuration_fingerprint": None,
            "reference_bank_path": None,
            "reference_bank_character_id": None,
            "reference_bank_fingerprint": None,
            "adapter_id": None,
            "adapter_path": None,
            "mlx_model_path": None,
            "instruction_propagation": None,
            "experimental_prompt_routing": None,
            "description": "",
        }
    )
    return updated


def consume_pending_voice_import_queue(
    *,
    queue_path: str | Path,
    project_root: str | Path,
    project_id: str,
    reusable_library_root: str | Path | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    queue = Path(queue_path).expanduser().resolve()
    if not queue.is_file():
        return {"status": "none", "imported_speakers": []}
    root = Path(project_root).expanduser().resolve()
    payload = _read_json_object(queue, "Pending voice import queue")
    if payload.get("schema_version") != PENDING_VOICE_IMPORT_SCHEMA_VERSION:
        raise PendingVoiceImportError("Pending voice import queue schema is unsupported.")
    target_project_id = _require_text(payload.get("target_project_id"), "Target project ID", maximum=160)
    target_project_root = Path(
        _require_text(payload.get("target_project_root"), "Target project root", maximum=2048)
    ).expanduser().resolve()
    if target_project_id != str(project_id).strip() or target_project_root != root:
        return {
            "status": "not_target",
            "target_project_id": target_project_id,
            "active_project_id": str(project_id).strip(),
            "imported_speakers": [],
        }
    raw_imports = payload.get("imports")
    if not isinstance(raw_imports, list) or not raw_imports or len(raw_imports) > 16:
        raise PendingVoiceImportError("Pending voice imports must contain between 1 and 16 entries.")
    imports = [_normalize_entry(item, index=index) for index, item in enumerate(raw_imports)]
    source_speakers = [item["speaker"] for item in imports]
    if len({speaker.casefold() for speaker in source_speakers}) != len(source_speakers):
        raise PendingVoiceImportError("Pending voice imports contain duplicate speakers.")
    speakers = [
        speaker
        for item in imports
        for speaker in item["assign_speakers"]
    ]
    if len({speaker.casefold() for speaker in speakers}) != len(speakers):
        raise PendingVoiceImportError(
            "Pending voice imports assign more than one Voice to the same speaker."
        )
    publish_reusable = payload.get("publish_reusable") is True
    reusable_root = (
        Path(reusable_library_root).expanduser().resolve()
        if reusable_library_root is not None
        else None
    )
    if publish_reusable and (
        reusable_root is None
        or not reusable_root.is_dir()
        or reusable_root.is_symlink()
    ):
        raise PendingVoiceImportError(
            "The reusable Voice library root is unavailable or unsafe."
        )
    raw_hidden = payload.get("hide_reusable_configuration_keys") or {}
    if not isinstance(raw_hidden, Mapping):
        raise PendingVoiceImportError(
            "hide_reusable_configuration_keys must be an object of aliases."
        )
    hidden_reusable_keys = {
        _require_text(key, "Hidden reusable configuration key", maximum=160):
        _require_text(target, "Hidden reusable alias target", maximum=160)
        for key, target in raw_hidden.items()
    }

    voice_config_path = root / "voice_config.json"
    if not voice_config_path.is_file():
        raise PendingVoiceImportError(f"Voice configuration is missing: {voice_config_path}")
    config = _read_json_object(voice_config_path, "Voice configuration")
    project_character_names = _collect_project_character_names(root)
    for item in imports:
        for assignment_speaker in item["assign_speakers"]:
            existing = config.get(assignment_speaker)
            if isinstance(existing, dict):
                continue
            if assignment_speaker.casefold() not in project_character_names:
                raise PendingVoiceImportError(
                    f"Speaker {assignment_speaker!r} does not exist in the active project roster or script."
                )

    clone_dir = root / "clone_voices"
    manifest_path = clone_dir / "manifest.json"
    receipt_dir = root / VOICE_IMPORT_RECEIPTS_DIRNAME
    at = at_utc or utc_timestamp()
    queue_id = str(payload.get("queue_id") or "").strip() or fingerprint_value(payload)[:24]
    operation_id = "voice_import_" + fingerprint_value(
        {"queue_id": queue_id, "project_id": project_id, "speakers": speakers}
    )[:24]
    receipt_path = receipt_dir / f"{operation_id}.json"
    before_config = voice_config_path.read_bytes()
    before_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    before_destinations: dict[Path, bytes | None] = {}
    manifest = _read_manifest(manifest_path)

    reusable_config_path = reusable_root / "voice_config.json" if publish_reusable and reusable_root else None
    reusable_clone_dir = reusable_root / "clone_voices" if publish_reusable and reusable_root else None
    reusable_manifest_path = (
        reusable_clone_dir / "manifest.json" if reusable_clone_dir is not None else None
    )
    if publish_reusable and reusable_config_path is not None and not reusable_config_path.is_file():
        raise PendingVoiceImportError(
            f"Reusable Voice configuration is missing: {reusable_config_path}"
        )
    reusable_config = (
        _read_json_object(reusable_config_path, "Reusable Voice configuration")
        if reusable_config_path is not None
        else {}
    )
    reusable_manifest = (
        _read_manifest(reusable_manifest_path)
        if reusable_manifest_path is not None
        else []
    )
    before_reusable_config = (
        reusable_config_path.read_bytes() if reusable_config_path is not None else None
    )
    before_reusable_manifest = (
        reusable_manifest_path.read_bytes()
        if reusable_manifest_path is not None and reusable_manifest_path.exists()
        else None
    )
    before_reusable_destinations: dict[Path, bytes | None] = {}
    temporary_paths: list[Path] = []
    try:
        for item in imports:
            materialized_source, materialized_hash = _materialize_entry_source(
                item,
                temporary_paths=temporary_paths,
            )
            item["source"] = materialized_source
            item["source_sha256"] = materialized_hash
    except Exception:
        for temporary in temporary_paths:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        raise

    try:
        for item in imports:
            reusable_voice_id = (
                _reusable_voice_id(item["reusable_configuration_key"])
                if publish_reusable
                else None
            )
            if publish_reusable and reusable_clone_dir is not None:
                reusable_destination = reusable_clone_dir / item["destination_filename"]
                before_reusable_destinations[reusable_destination] = (
                    reusable_destination.read_bytes()
                    if reusable_destination.exists()
                    else None
                )
                _atomic_copy(item["source"], reusable_destination)
                if sha256_file(reusable_destination) != item["source_sha256"]:
                    raise PendingVoiceImportError(
                        f"Reusable audio for {item['speaker']} failed SHA-256 verification."
                    )
                reusable_relative = reusable_destination.relative_to(reusable_root).as_posix()
                reusable_existing = reusable_config.get(
                    item["reusable_configuration_key"]
                )
                reusable_voice = _simple_clone_config(
                    reusable_existing if isinstance(reusable_existing, Mapping) else {},
                    ref_audio=reusable_relative,
                    ref_text=item["transcript"],
                )
                reusable_voice.pop("library_voice_id", None)
                reusable_config[item["reusable_configuration_key"]] = reusable_voice
                raw_voice_id = Path(item["destination_filename"]).stem
                reusable_manifest = [
                    row
                    for row in reusable_manifest
                    if str(row.get("id") or "") != raw_voice_id
                ]
                reusable_manifest.append(
                    {
                        "id": raw_voice_id,
                        "name": item["display_name"],
                        "filename": item["destination_filename"],
                    }
                )

            destination = clone_dir / item["destination_filename"]
            before_destinations[destination] = (
                destination.read_bytes() if destination.exists() else None
            )
            _atomic_copy(item["source"], destination)
            copied_hash = sha256_file(destination)
            if copied_hash != item["source_sha256"]:
                raise PendingVoiceImportError(
                    f"Copied audio for {item['speaker']} failed SHA-256 verification."
                )
            relative_audio = destination.relative_to(root).as_posix()
            for assignment_speaker in item["assign_speakers"]:
                existing_config = config.get(assignment_speaker)
                assigned_voice = _simple_clone_config(
                    existing_config if isinstance(existing_config, Mapping) else {},
                    ref_audio=relative_audio,
                    ref_text=item["transcript"],
                )
                assigned_voice["library_voice_id"] = reusable_voice_id
                config[assignment_speaker] = assigned_voice
            raw_voice_id = Path(item["destination_filename"]).stem
            manifest = [
                row for row in manifest if str(row.get("id") or "") != raw_voice_id
            ]
            manifest.append(
                {
                    "id": raw_voice_id,
                    "name": item["display_name"],
                    "filename": item["destination_filename"],
                }
            )

        if publish_reusable:
            for hidden_key, alias_target in hidden_reusable_keys.items():
                if alias_target not in reusable_config:
                    raise PendingVoiceImportError(
                        f"Reusable alias target {alias_target!r} does not exist."
                    )
                reusable_config[hidden_key] = {"alias_of": alias_target}
            validate_voice_aliases(reusable_config)
            reusable_manifest.sort(
                key=lambda row: (
                    str(row.get("name") or "").casefold(),
                    str(row.get("id") or ""),
                )
            )
            atomic_json_write(reusable_config, reusable_config_path)
            atomic_json_write(reusable_manifest, reusable_manifest_path)

        validate_voice_aliases(config)
        manifest.sort(
            key=lambda row: (
                str(row.get("name") or "").casefold(),
                str(row.get("id") or ""),
            )
        )
        atomic_json_write(config, voice_config_path)
        atomic_json_write(manifest, manifest_path)
        dependency_before: dict[Path, bytes | None] = {
            voice_config_path: before_config,
            manifest_path: before_manifest,
        }
        dependency_before.update(before_destinations)
        invalidation = apply_project_audio_invalidation(
            project_root=root,
            operation_id=operation_id,
            operation="supplied_recording_clone_import",
            at_utc=at,
            speakers=set(speakers),
            reason="speaker changed to an imported supplied-recording clone",
            dependency_before=dependency_before,
        )
        receipt = {
            "schema_version": 1,
            "operation_id": operation_id,
            "queue_id": queue_id,
            "status": "applied",
            "project_id": str(project_id).strip(),
            "project_root": str(root),
            "applied_at_utc": at,
            "mode": "supplied_recording_clone",
            "published_to_reusable_library": publish_reusable,
            "hidden_reusable_configuration_keys": hidden_reusable_keys,
            "imports": [
                {
                    "speaker": item["speaker"],
                    "assign_speakers": item["assign_speakers"],
                    "reusable_configuration_key": item[
                        "reusable_configuration_key"
                    ],
                    "reusable_voice_id": (
                        _reusable_voice_id(item["reusable_configuration_key"])
                        if publish_reusable
                        else None
                    ),
                    "display_name": item["display_name"],
                    "ref_audio": f"clone_voices/{item['destination_filename']}",
                    "ref_audio_sha256": item["source_sha256"],
                    "ref_text": item["transcript"],
                    "source_url": item["source_url"],
                    "clone_backend": "qwen3_base",
                }
                for item in imports
            ],
            "audio_invalidation": invalidation,
        }
        atomic_json_write(receipt, receipt_path)
        queue.unlink()
        return {
            "status": "applied",
            "operation_id": operation_id,
            "receipt_path": str(receipt_path),
            "imported_speakers": speakers,
            "published_reusable_voices": [
                item["reusable_configuration_key"] for item in imports
            ] if publish_reusable else [],
        }
    except Exception:
        _atomic_bytes(voice_config_path, before_config)
        _atomic_bytes(manifest_path, before_manifest)
        for destination, previous in before_destinations.items():
            _atomic_bytes(destination, previous)
        if reusable_config_path is not None:
            _atomic_bytes(reusable_config_path, before_reusable_config)
        if reusable_manifest_path is not None:
            _atomic_bytes(reusable_manifest_path, before_reusable_manifest)
        for destination, previous in before_reusable_destinations.items():
            _atomic_bytes(destination, previous)
        raise
    finally:
        for temporary in temporary_paths:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
