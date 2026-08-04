from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from pydub import AudioSegment

from audio_edge_safety import ensure_click_safe_fade_in, needs_click_safe_fade_in
from backend_render_plan import applied_binding_fields
from audio_processing import AudioProcessingError, validate_generated_speech_duration
from fish_hybrid_policy import FISH_HYBRID_POLICY_FIELDS
from synthesis_windows import synthesis_binding_fields


AUDIO_BINDING_CONTRACT_VERSION = 1
MIN_MP3_BYTES = 1024


class AudioArtifactError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _voice_binding_value(
    voice_data: dict[str, Any] | None,
    chunk: dict[str, Any],
) -> dict[str, Any]:
    voice = dict(voice_data or {})
    for field in FISH_HYBRID_POLICY_FIELDS:
        voice.pop(field, None)
    if chunk.get("cloud_provider") == "fish_s21_cloud":
        voice["installed_generator"] = {
            "provider": "fish_s21_cloud",
            "model": chunk.get("cloud_model"),
            "style_route": chunk.get("cloud_style_route"),
            "reference_fingerprint": chunk.get("cloud_reference_fingerprint"),
        }
    return voice


def audio_binding_fingerprint(
    *,
    chunk: dict[str, Any],
    resolved_speaker: str,
    voice_config: dict[str, Any],
    synthesis_config: dict[str, Any] | None = None,
) -> str:
    payload = {
        "contract_version": AUDIO_BINDING_CONTRACT_VERSION,
        "speaker": chunk.get("speaker", ""),
        "resolved_speaker": resolved_speaker,
        "text": chunk.get("text", ""),
        "instruct": chunk.get("instruct", ""),
        "voice": _voice_binding_value(
            voice_config.get(resolved_speaker, {}),
            chunk,
        ),
        "synthesis": synthesis_config or {},
    }
    backend_binding = applied_binding_fields(chunk)
    if backend_binding is not None:
        payload["backend_render_plan"] = backend_binding
    elif (
        chunk.get("fish_render_plan") is not None
        and not chunk.get("backend_render_plan_fingerprint")
    ):
        # Compatibility for direct inline plans created before backend render plans.
        payload["fish_render_plan"] = chunk.get("fish_render_plan")
    if (
        chunk.get("spoken_continuity_binding_enabled") is True
        or chunk.get("spoken_continuity_applied") is not None
    ):
        payload["spoken_continuity"] = chunk.get("spoken_continuity")
        payload["spoken_continuity_synthesis"] = {
            "mode": chunk.get("spoken_continuity_synthesis_mode"),
            "text_sha256": chunk.get(
                "spoken_continuity_synthesis_text_sha256"
            ),
        }
    pronunciation_request_fingerprint = str(
        chunk.get("pronunciation_request_fingerprint") or ""
    ).strip()
    if pronunciation_request_fingerprint:
        payload["pronunciation"] = {
            "chunk_entry_fingerprint": chunk.get(
                "pronunciation_chunk_entry_fingerprint"
            ),
            "request_fingerprint": pronunciation_request_fingerprint,
            "synthesis_text_sha256": chunk.get(
                "pronunciation_synthesis_text_sha256"
            ),
            "applied_count": int(
                chunk.get("pronunciation_applied_count") or 0
            ),
            "bypassed_count": int(
                chunk.get("pronunciation_bypassed_count") or 0
            ),
        }
    synthesis_window_binding = synthesis_binding_fields(chunk)
    if synthesis_window_binding is not None:
        payload["synthesis_windows"] = synthesis_window_binding
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def confined_audio_path(root_dir: str | Path, relative_path: str) -> Path:
    root = Path(root_dir).expanduser().resolve()
    value = Path(str(relative_path or ""))
    if not relative_path or value.is_absolute() or ".." in value.parts:
        raise AudioArtifactError(
            "unsafe_audio_path",
            f"Audio path is not project-confined: {relative_path!r}.",
        )
    target = (root / value).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise AudioArtifactError(
            "unsafe_audio_path",
            f"Audio path escaped the project root: {relative_path!r}.",
        ) from exc
    return target


def _validate_audio(
    path: Path,
    *,
    format_hint: str | None = None,
    decoder: Callable[..., AudioSegment] | None = None,
    expected_text: str | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise AudioArtifactError(
            "audio_file_missing",
            f"Generated audio file does not exist: {path}.",
        )
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise AudioArtifactError(
            "audio_file_empty",
            f"Generated audio file is empty: {path}.",
        )
    try:
        if decoder is None:
            audio_format = (format_hint or path.suffix).lower().lstrip(".")
            if audio_format in {"wav", "wave"}:
                with path.open("rb") as source:
                    segment = AudioSegment.from_file(source, format="wav")
            else:
                segment = AudioSegment.from_file(path, format=format_hint)
        else:
            with path.open("rb") as source:
                segment = decoder(source, format=format_hint)
    except Exception as exc:
        raise AudioArtifactError(
            "audio_decode_failed",
            f"Generated audio could not be decoded: {path}.",
        ) from exc
    duration_ms = len(segment)
    if duration_ms <= 0:
        raise AudioArtifactError(
            "audio_duration_empty",
            f"Generated audio has zero duration: {path}.",
        )
    if expected_text is not None:
        try:
            validate_generated_speech_duration(duration_ms / 1000.0, expected_text)
        except AudioProcessingError as exc:
            code = (
                "audio_duration_excessive"
                if "too long" in str(exc)
                else "audio_duration_insufficient"
            )
            raise AudioArtifactError(code, str(exc)) from exc
    sample_rate = int(getattr(segment, "frame_rate", 1000) or 1000)
    channels = int(getattr(segment, "channels", 1) or 1)
    sample_width = int(getattr(segment, "sample_width", 2) or 2)
    frame_counter = getattr(segment, "frame_count", None)
    sample_count = (
        int(round(frame_counter()))
        if callable(frame_counter)
        else int(round(duration_ms * sample_rate / 1000.0))
    )
    return {
        "size_bytes": size_bytes,
        "duration_ms": duration_ms,
        "sha256": sha256_file(path),
        "sample_rate": sample_rate,
        "sample_count": sample_count,
        "channels": channels,
        "sample_width": sample_width,
        "segment": segment,
    }


def validate_audio_file(
    path: str | Path,
    *,
    format_hint: str | None = None,
    decoder: Callable[..., AudioSegment] | None = None,
) -> dict[str, Any]:
    result = _validate_audio(
        Path(path).expanduser().resolve(),
        format_hint=format_hint,
        decoder=decoder,
    )
    return {key: value for key, value in result.items() if key != "segment"}


def _safe_filename_base(filename_base: str) -> str:
    value = str(filename_base or "").strip()
    if (
        not value
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise AudioArtifactError(
            "unsafe_audio_filename",
            f"Unsafe canonical audio filename: {filename_base!r}.",
        )
    return value


def is_operation_audio_backup_path(relative_path: str | None) -> bool:
    if not relative_path:
        return False
    value = Path(str(relative_path))
    parts = value.parts
    return (
        len(parts) >= 3
        and parts[-2] == "audio"
        and value.suffix == ".bin"
        and len(value.stem) == 64
        and all(character in "0123456789abcdef" for character in value.stem)
    )


def _remove_confined_path(
    root: Path,
    relative_path: str | None,
    *,
    allow_operation_backup: bool = False,
) -> None:
    if not relative_path:
        return
    if (
        not allow_operation_backup
        and is_operation_audio_backup_path(relative_path)
    ):
        return
    try:
        target = confined_audio_path(root, relative_path)
    except AudioArtifactError:
        return
    try:
        target.unlink()
    except FileNotFoundError:
        pass


def atomic_export_audio_segment(
    *,
    segment: AudioSegment,
    target_path: str | Path,
    audio_format: str,
    decoder: Callable[..., AudioSegment] | None = None,
) -> dict[str, Any]:
    target = Path(target_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=f".{audio_format}",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as output:
            segment.export(output, format=audio_format)
        if audio_format == "mp3" and temporary.stat().st_size < MIN_MP3_BYTES:
            raise AudioArtifactError(
                "invalid_mp3_export",
                "MP3 export produced a header-only or otherwise invalid file.",
            )
        validation = _validate_audio(
            temporary,
            format_hint=audio_format,
            decoder=decoder,
        )
        os.replace(temporary, target)
        return {
            "path": str(target),
            "format": audio_format,
            **{key: value for key, value in validation.items() if key != "segment"},
        }
    except AudioArtifactError:
        raise
    except Exception as exc:
        raise AudioArtifactError(
            "audio_output_export_failed",
            f"Audio output could not be exported atomically: {target}.",
        ) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def install_generated_audio(
    *,
    root_dir: str | Path,
    voicelines_dir: str | Path,
    source_audio_path: str | Path,
    filename_base: str,
    binding_fingerprint: str,
    previous_audio_path: str | None = None,
    prefer_mp3: bool = True,
    decoder: Callable[..., AudioSegment] | None = None,
    text: str | None = None,
    before_commit: Callable[[Path, bytes], None] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    destination_dir = Path(voicelines_dir).expanduser().resolve()
    try:
        destination_dir.relative_to(root)
    except ValueError as exc:
        raise AudioArtifactError(
            "unsafe_audio_directory",
            "The canonical voicelines directory must remain inside the project root.",
        ) from exc
    destination_dir.mkdir(parents=True, exist_ok=True)
    source = Path(source_audio_path).expanduser().resolve()
    source_info = _validate_audio(source, decoder=decoder, expected_text=text)
    segment = ensure_click_safe_fade_in(source_info["segment"])
    safe_base = _safe_filename_base(filename_base)

    formats = ("mp3", "wav") if prefer_mp3 else ("wav",)
    last_error: Exception | None = None
    selected: dict[str, Any] | None = None
    for audio_format in formats:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{safe_base}.",
            suffix=f".{audio_format}.tmp",
            dir=destination_dir,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as output:
                segment.export(output, format=audio_format)
            if audio_format == "mp3" and temporary.stat().st_size < MIN_MP3_BYTES:
                raise AudioArtifactError(
                    "invalid_mp3_export",
                    "MP3 export produced a header-only or otherwise invalid file.",
                )
            validation = _validate_audio(
                temporary,
                format_hint=audio_format,
                decoder=decoder,
                expected_text=text,
            )
            if audio_format == "mp3" and needs_click_safe_fade_in(
                validation["segment"]
            ):
                raise AudioArtifactError(
                    "unsafe_mp3_start",
                    "MP3 encoding reopened an abrupt audio start.",
                )
            canonical = destination_dir / f"{safe_base}.{audio_format}"
            if before_commit is not None:
                before_commit(canonical, temporary.read_bytes())
            os.replace(temporary, canonical)
            selected = {
                "canonical": canonical,
                "format": audio_format,
                **{key: value for key, value in validation.items() if key != "segment"},
            }
            break
        except Exception as exc:
            last_error = exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    if selected is None:
        if isinstance(last_error, AudioArtifactError):
            raise last_error
        raise AudioArtifactError(
            "audio_install_failed",
            "Generated audio could not be installed in MP3 or WAV format.",
        ) from last_error

    canonical = selected["canonical"]
    relative = canonical.relative_to(root).as_posix()
    obsolete = destination_dir / (
        f"{safe_base}.wav" if selected["format"] == "mp3" else f"{safe_base}.mp3"
    )
    if obsolete != canonical:
        try:
            obsolete.unlink()
        except FileNotFoundError:
            pass
    return {
        "audio_path": relative,
        "audio_state": "current",
        "audio_fingerprint": binding_fingerprint,
        "audio_sha256": selected["sha256"],
        "audio_size_bytes": selected["size_bytes"],
        "audio_duration_ms": selected["duration_ms"],
        "audio_format": selected["format"],
        "audio_sample_rate": selected["sample_rate"],
        "audio_sample_count": selected["sample_count"],
        "audio_channels": selected["channels"],
        "audio_sample_width": selected["sample_width"],
        "stale_audio_path": None,
    }


def plan_verified_audio_install(
    *,
    root_dir: str | Path,
    voicelines_dir: str | Path,
    source_audio_path: str | Path,
    filename_base: str,
    binding_fingerprint: str,
    expected_sha256: str,
    decoder: Callable[..., AudioSegment] | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    destination_dir = Path(voicelines_dir).expanduser().resolve()
    try:
        destination_dir.relative_to(root)
    except ValueError as exc:
        raise AudioArtifactError(
            "unsafe_audio_directory",
            "The canonical voicelines directory must remain inside the project root.",
        ) from exc
    source = Path(source_audio_path).expanduser().resolve()
    expected = str(expected_sha256 or "")
    if sha256_file(source) != expected:
        raise AudioArtifactError(
            "approved_audio_hash_mismatch",
            "Approved audio no longer matches its reviewed SHA-256 fingerprint.",
        )
    suffix = source.suffix.casefold().lstrip(".")
    if suffix == "wave":
        suffix = "wav"
    if suffix not in {"mp3", "wav"}:
        raise AudioArtifactError(
            "approved_audio_format_unsupported",
            "Approved audio must be an MP3 or WAV file.",
        )
    source_info = _validate_audio(
        source,
        format_hint=suffix,
        decoder=decoder,
        expected_text=text,
    )
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise AudioArtifactError(
            "approved_audio_source_changed",
            "Approved audio changed while its durable install was planned.",
        )
    safe_base = _safe_filename_base(filename_base)
    canonical = destination_dir / f"{safe_base}.{suffix}"
    relative = canonical.relative_to(root).as_posix()
    obsolete = destination_dir / (
        f"{safe_base}.wav" if suffix == "mp3" else f"{safe_base}.mp3"
    )
    return {
        "artifact": {
            "audio_path": relative,
            "audio_state": "current",
            "audio_fingerprint": binding_fingerprint,
            "audio_sha256": expected,
            "audio_size_bytes": source_info["size_bytes"],
            "audio_duration_ms": source_info["duration_ms"],
            "audio_format": suffix,
            "audio_sample_rate": source_info["sample_rate"],
            "audio_sample_count": source_info["sample_count"],
            "audio_channels": source_info["channels"],
            "audio_sample_width": source_info["sample_width"],
            "stale_audio_path": None,
            "approved_source_size_bytes": source_info["size_bytes"],
        },
        "content": content,
        "obsolete_relative_path": obsolete.relative_to(root).as_posix(),
    }


def install_verified_audio(
    *,
    root_dir: str | Path,
    voicelines_dir: str | Path,
    source_audio_path: str | Path,
    filename_base: str,
    binding_fingerprint: str,
    expected_sha256: str,
    previous_audio_path: str | None = None,
    decoder: Callable[..., AudioSegment] | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    """Install already-reviewed audio without changing its bytes.

    Generated audio normally passes through Alexandria's encoder and click-safe
    fade. Human-approved imports must instead preserve the exact reviewed file,
    while still receiving the same confined path, metadata, and binding checks.
    """
    root = Path(root_dir).expanduser().resolve()
    destination_dir = Path(voicelines_dir).expanduser().resolve()
    try:
        destination_dir.relative_to(root)
    except ValueError as exc:
        raise AudioArtifactError(
            "unsafe_audio_directory",
            "The canonical voicelines directory must remain inside the project root.",
        ) from exc
    destination_dir.mkdir(parents=True, exist_ok=True)
    source = Path(source_audio_path).expanduser().resolve()
    if sha256_file(source) != str(expected_sha256 or ""):
        raise AudioArtifactError(
            "approved_audio_hash_mismatch",
            "Approved audio no longer matches its reviewed SHA-256 fingerprint.",
        )
    suffix = source.suffix.casefold().lstrip(".")
    if suffix == "wave":
        suffix = "wav"
    if suffix not in {"mp3", "wav"}:
        raise AudioArtifactError(
            "approved_audio_format_unsupported",
            "Approved audio must be an MP3 or WAV file.",
        )
    source_info = _validate_audio(
        source,
        format_hint=suffix,
        decoder=decoder,
        expected_text=text,
    )
    safe_base = _safe_filename_base(filename_base)
    canonical = destination_dir / f"{safe_base}.{suffix}"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{safe_base}.",
        suffix=f".{suffix}.tmp",
        dir=destination_dir,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        if sha256_file(temporary) != expected_sha256:
            raise AudioArtifactError(
                "approved_audio_copy_mismatch",
                "Approved audio changed while it was copied into the project.",
            )
        os.replace(temporary, canonical)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    installed = _validate_audio(
        canonical,
        format_hint=suffix,
        decoder=decoder,
        expected_text=text,
    )
    installed_hash = sha256_file(canonical)
    if installed_hash != expected_sha256:
        raise AudioArtifactError(
            "approved_audio_install_mismatch",
            "Installed approved audio does not match its reviewed fingerprint.",
        )
    relative = canonical.relative_to(root).as_posix()
    obsolete = destination_dir / (
        f"{safe_base}.wav" if suffix == "mp3" else f"{safe_base}.mp3"
    )
    if obsolete != canonical:
        try:
            obsolete.unlink()
        except FileNotFoundError:
            pass
    return {
        "audio_path": relative,
        "audio_state": "current",
        "audio_fingerprint": binding_fingerprint,
        "audio_sha256": installed_hash,
        "audio_size_bytes": installed["size_bytes"],
        "audio_duration_ms": installed["duration_ms"],
        "audio_format": suffix,
        "audio_sample_rate": installed["sample_rate"],
        "audio_sample_count": installed["sample_count"],
        "audio_channels": installed["channels"],
        "audio_sample_width": installed["sample_width"],
        "stale_audio_path": None,
        "approved_source_size_bytes": source_info["size_bytes"],
    }


def _confined_operation_directory(
    root_dir: str | Path,
    operation_dir: str | Path,
) -> tuple[Path, Path]:
    root = Path(root_dir).expanduser().resolve()
    directory = Path(operation_dir).expanduser().resolve()
    try:
        directory.relative_to(root)
    except ValueError as exc:
        raise AudioArtifactError(
            "unsafe_audio_backup_directory",
            "Audio operation backups must remain inside the project root.",
        ) from exc
    return root, directory


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def audio_backup_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(record["original_path"]): record
        for record in records
    }


def backup_operation_audio(
    *,
    root_dir: str | Path,
    operation_dir: str | Path,
    relative_paths: list[str | None] | tuple[str | None, ...],
) -> list[dict[str, Any]]:
    root, directory = _confined_operation_directory(root_dir, operation_dir)
    backup_dir = directory / "audio"
    records: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    created_backups: set[Path] = set()
    seen_paths: set[str] = set()
    try:
        for value in relative_paths:
            if not value or value in seen_paths:
                continue
            seen_paths.add(value)
            source = confined_audio_path(root, value)
            if not source.is_file():
                continue
            digest = sha256_file(source)
            size_bytes = source.stat().st_size
            backup = backup_dir / f"{digest}.bin"
            if backup.exists():
                if not backup.is_file() or sha256_file(backup) != digest:
                    raise AudioArtifactError(
                        "audio_backup_collision",
                        f"Audio backup collision for {value!r}.",
                    )
            else:
                _atomic_copy(source, backup)
                if sha256_file(backup) != digest:
                    raise AudioArtifactError(
                        "audio_backup_hash_mismatch",
                        f"Audio backup verification failed for {value!r}.",
                    )
                created_backups.add(backup)
            record = {
                "original_path": source.relative_to(root).as_posix(),
                "backup_path": backup.relative_to(root).as_posix(),
                "sha256": digest,
                "size_bytes": size_bytes,
            }
            source.unlink()
            records.append(record)
            removed.append(record)
        return records
    except Exception:
        try:
            restore_operation_audio(
                root_dir=root,
                records=removed,
                require_original_absent=False,
                consume_backups=False,
            )
        finally:
            for backup in created_backups:
                try:
                    backup.unlink()
                except FileNotFoundError:
                    pass
        raise


def validate_operation_audio_backups(
    *,
    root_dir: str | Path,
    records: list[dict[str, Any]],
    require_original_absent: bool = True,
) -> None:
    root = Path(root_dir).expanduser().resolve()
    for record in records:
        original = confined_audio_path(root, str(record.get("original_path") or ""))
        backup = confined_audio_path(root, str(record.get("backup_path") or ""))
        expected = str(record.get("sha256") or "")
        if len(expected) != 64 or not backup.is_file():
            raise AudioArtifactError(
                "audio_backup_missing",
                f"Operation audio backup is missing for {record.get('original_path')!r}.",
            )
        if sha256_file(backup) != expected:
            raise AudioArtifactError(
                "audio_backup_hash_mismatch",
                f"Operation audio backup changed for {record.get('original_path')!r}.",
            )
        if original.exists():
            if require_original_absent:
                raise AudioArtifactError(
                    "audio_rollback_conflict",
                    f"A newer audio file exists at {record.get('original_path')!r}.",
                )
            if not original.is_file() or sha256_file(original) != expected:
                raise AudioArtifactError(
                    "audio_rollback_conflict",
                    f"Audio path changed while restoring {record.get('original_path')!r}.",
                )


def consume_operation_audio_backups(
    *,
    root_dir: str | Path,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    backup_paths = sorted(
        {str(record.get("backup_path") or "") for record in records}
    )
    for backup_path in backup_paths:
        if not is_operation_audio_backup_path(backup_path):
            raise AudioArtifactError(
                "unsafe_audio_backup_path",
                f"Operation audio backup path is invalid: {backup_path!r}.",
            )
        confined_audio_path(root, backup_path)

    removed: list[str] = []
    missing: list[str] = []
    failed: list[dict[str, str]] = []
    parent_dirs: set[Path] = set()
    for backup_path in backup_paths:
        backup = confined_audio_path(root, backup_path)
        parent_dirs.add(backup.parent)
        try:
            backup.unlink()
        except FileNotFoundError:
            missing.append(backup_path)
        except OSError as exc:
            failed.append(
                {
                    "backup_path": backup_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            removed.append(backup_path)
    for directory in sorted(parent_dirs, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return {
        "status": "complete" if not failed else "partial",
        "removed_paths": removed,
        "already_missing_paths": missing,
        "failed_paths": failed,
    }


def restore_operation_audio(
    *,
    root_dir: str | Path,
    records: list[dict[str, Any]],
    require_original_absent: bool = True,
    consume_backups: bool = False,
) -> list[str]:
    root = Path(root_dir).expanduser().resolve()
    validate_operation_audio_backups(
        root_dir=root,
        records=records,
        require_original_absent=require_original_absent,
    )
    restored: list[str] = []
    for record in records:
        original = confined_audio_path(root, record["original_path"])
        backup = confined_audio_path(root, record["backup_path"])
        if not original.exists():
            _atomic_copy(backup, original)
        if sha256_file(original) != record["sha256"]:
            raise AudioArtifactError(
                "audio_restore_hash_mismatch",
                f"Restored audio verification failed for {record['original_path']!r}.",
            )
        restored.append(record["original_path"])
    if consume_backups:
        consume_operation_audio_backups(
            root_dir=root,
            records=records,
        )
    return restored


def remove_restored_operation_audio(
    *,
    root_dir: str | Path,
    records: list[dict[str, Any]],
) -> None:
    root = Path(root_dir).expanduser().resolve()
    for record in records:
        original = confined_audio_path(root, record["original_path"])
        if original.is_file() and sha256_file(original) == record.get("sha256"):
            original.unlink()


def inspect_chunk_audio(
    *,
    root_dir: str | Path,
    chunk: dict[str, Any],
    expected_fingerprint: str,
    decoder: Callable[..., AudioSegment] | None = None,
) -> dict[str, Any]:
    status = str(chunk.get("status") or "pending")
    path_value = chunk.get("audio_path")
    if status == "generating":
        return {"state": "generating", "ready": False, "reason": "generation_running"}
    if status == "error" or chunk.get("audio_state") == "failed":
        return {"state": "failed", "ready": False, "reason": "generation_failed"}
    if status != "done":
        return {"state": "pending", "ready": False, "reason": "generation_incomplete"}
    if not path_value:
        return {"state": "missing", "ready": False, "reason": "audio_path_missing"}
    if chunk.get("audio_state") != "current":
        return {"state": "stale", "ready": False, "reason": "audio_not_current"}
    if chunk.get("audio_research_only") is True:
        return {
            "state": "research_only",
            "ready": False,
            "reason": "experimental_prompt_not_production_eligible",
        }
    if chunk.get("audio_fingerprint") != expected_fingerprint:
        return {"state": "stale", "ready": False, "reason": "audio_fingerprint_mismatch"}
    try:
        path = confined_audio_path(root_dir, str(path_value))
    except AudioArtifactError as exc:
        return {"state": "failed", "ready": False, "reason": exc.code}
    if not path.is_file():
        return {"state": "missing", "ready": False, "reason": "audio_file_missing"}
    try:
        validation = _validate_audio(
            path,
            decoder=decoder,
            expected_text=str(chunk.get("text") or ""),
        )
    except AudioArtifactError as exc:
        return {"state": "failed", "ready": False, "reason": exc.code}
    recorded_hash = chunk.get("audio_sha256")
    if not recorded_hash or recorded_hash != validation["sha256"]:
        return {"state": "stale", "ready": False, "reason": "audio_hash_mismatch"}
    return {
        "state": "current",
        "ready": True,
        "reason": None,
        "path": str(path),
        "relative_path": str(path_value),
        "duration_ms": validation["duration_ms"],
        "size_bytes": validation["size_bytes"],
        "sha256": validation["sha256"],
        "segment": validation["segment"],
    }


def require_current_project_audio(
    *,
    root_dir: str | Path,
    chunks: list[dict[str, Any]],
    expected_fingerprint: Callable[[dict[str, Any]], str],
    decoder: Callable[..., AudioSegment] | None = None,
    progress_callback: Callable[[int, int, int, dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[tuple[dict[str, Any], AudioSegment]]:
    current: list[tuple[dict[str, Any], AudioSegment]] = []
    blockers: list[dict[str, Any]] = []
    eligible = [
        (index, chunk)
        for index, chunk in enumerate(chunks)
        if str(chunk.get("text") or "").strip()
    ]
    total = len(eligible)
    for completed, (index, chunk) in enumerate(eligible, start=1):
        if cancel_check and cancel_check():
            raise AudioArtifactError(
                "audio_export_cancelled",
                "Final audio export was cancelled.",
            )
        inspection = inspect_chunk_audio(
            root_dir=root_dir,
            chunk=chunk,
            expected_fingerprint=expected_fingerprint(chunk),
            decoder=decoder,
        )
        if inspection["ready"]:
            current.append((chunk, inspection["segment"]))
        else:
            blockers.append(
                {
                    "index": index,
                    "speaker": chunk.get("speaker"),
                    "state": inspection["state"],
                    "reason": inspection["reason"],
                }
            )
        if progress_callback:
            progress_callback(completed, total, index, chunk)
    if blockers:
        summary = ", ".join(
            f"chunk {item['index'] + 1}: {item['state']}"
            for item in blockers[:8]
        )
        if len(blockers) > 8:
            summary += f", and {len(blockers) - 8} more"
        raise AudioArtifactError(
            "project_audio_not_ready",
            f"Final audio export is blocked: {summary}.",
            details=blockers,
        )
    if not current:
        raise AudioArtifactError(
            "project_audio_empty",
            "Final audio export is blocked because no current audio exists.",
            details=[],
        )
    return current
