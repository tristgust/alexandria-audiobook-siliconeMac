"""Deterministic, metadata-free audio remuxing for public Round 1 review."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Final, NoReturn, Protocol


_FORBIDDEN_TAG_KEYS: Final = frozenset(
    {"artist", "author", "title", "comment", "path", "vendor", "source"}
)
_FORBIDDEN_BYTES: Final = (
    b"derricksjones", b"7thdoctorspeeches", b"model/vendor/source",
    b"model\\vendor\\source", b"/users/", b"models--",
)


class PathGuard(Protocol):
    def __call__(self, path: Path, *, allow_missing_leaf: bool) -> None: ...


class PublicAudioError(RuntimeError):
    def __init__(self, code: str, subject: str):
        self.code = code
        super().__init__(f"{code}: {subject}")


@dataclass(frozen=True, slots=True)
class SanitizedAudio:
    path: Path
    public_name: str
    sha256: str
    decoded_sha256: str
    size_bytes: int
    container: str
    codec_name: str


@dataclass(frozen=True, slots=True)
class _ContainerPolicy:
    name: str
    suffix: str
    codecs: frozenset[str]
    muxer_arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AudioProbe:
    format_name: str
    codec_name: str
    format_tags: dict[str, str]
    stream_tags: dict[str, str]
    tag_values: tuple[str, ...]
    audio_stream_count: int
    total_stream_count: int
    section_count: int


_WAV: Final = _ContainerPolicy(
    "wav", ".wav",
    frozenset({"pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le",
               "pcm_f64le", "pcm_alaw", "pcm_mulaw"}),
    (),
)
_MP3: Final = _ContainerPolicy(
    "mp3", ".mp3", frozenset({"mp3"}),
    ("-id3v2_version", "0", "-write_id3v1", "0", "-write_xing", "1"),
)


def _reject_symlinks(path: Path, *, allow_missing_leaf: bool) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            status = os.lstat(current)
        except FileNotFoundError:
            if allow_missing_leaf and current == path:
                return
            raise PublicAudioError("path_missing", str(current)) from None
        if stat.S_ISLNK(status.st_mode):
            raise PublicAudioError("path_symlink", str(current))


@contextmanager
def _regular_fd(path: Path) -> Iterator[int]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise PublicAudioError("path_unsafe", str(path)) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PublicAudioError("path_not_regular", str(path))
        yield descriptor
    finally:
        os.close(descriptor)


def _tool(arguments: list[str], descriptors: tuple[int, ...], subject: str) -> str:
    try:
        completed = subprocess.run(
            arguments, pass_fds=descriptors, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise PublicAudioError("tool_missing", arguments[0]) from exc
    if completed.returncode:
        raise PublicAudioError("tool_failed", f"{subject}: {completed.stderr.strip()[-500:]}")
    return completed.stdout


def _tags(value: dict[str, Any] | None) -> dict[str, str]:
    return {str(key).casefold(): str(item) for key, item in (value or {}).items()}


def _probe_fd(descriptor: int, subject: str) -> _AudioProbe:
    os.lseek(descriptor, 0, os.SEEK_SET)
    output = _tool([
        "ffprobe", "-v", "error", "-show_entries",
        "format=format_name:format_tags:stream=codec_name,codec_type:stream_tags:chapter:program:stream_group",
        "-of", "json", f"/dev/fd/{descriptor}",
    ], (descriptor,), subject)
    try:
        payload: dict[str, Any] = json.loads(output)
    except json.JSONDecodeError as exc:
        raise PublicAudioError("probe_invalid", subject) from exc
    format_section = payload.get("format") or {}
    rows: list[dict[str, Any]] = payload.get("streams", [])
    audio_rows = [row for row in rows if row.get("codec_type") == "audio"]
    selected = audio_rows[0] if len(audio_rows) == 1 else {}
    format_tags = _tags(format_section.get("tags"))
    row_tags = tuple(_tags(row.get("tags")) for row in rows)
    return _AudioProbe(
        str(format_section.get("format_name", "")), str(selected.get("codec_name", "")),
        format_tags, _tags(selected.get("tags")),
        tuple(value for tags in (format_tags, *row_tags) for value in tags.values()),
        len(audio_rows), len(rows),
        sum(len(payload.get(key, [])) for key in ("chapters", "programs", "stream_groups")),
    )


def assert_never(value: str, subject: str) -> NoReturn:
    raise PublicAudioError("container_unsupported", f"{subject}: {value}")


def _policy(format_name: str, subject: str) -> _ContainerPolicy:
    match format_name:
        case "wav":
            return _WAV
        case "mp3":
            return _MP3
        case unsupported:
            assert_never(unsupported, subject)


def _validate_public(probe: _AudioProbe, suffix: str, subject: str) -> _ContainerPolicy:
    policy = _policy(probe.format_name, subject)
    if suffix.casefold() != policy.suffix:
        raise PublicAudioError("container_suffix", subject)
    if probe.audio_stream_count != 1 or probe.total_stream_count != 1:
        raise PublicAudioError("stream_layout", subject)
    if probe.codec_name not in policy.codecs or probe.section_count:
        raise PublicAudioError("container_fields", subject)
    all_tags = (probe.format_tags, probe.stream_tags)
    if any(_FORBIDDEN_TAG_KEYS.intersection(tags) for tags in all_tags):
        raise PublicAudioError("metadata_forbidden", subject)
    safe_stream_tags = {"encoder": "Lavf"} if policy.name == "mp3" else {}
    if probe.format_tags or probe.stream_tags not in ({}, safe_stream_tags):
        raise PublicAudioError("metadata_unexpected", subject)
    return policy


def _decoded_sha256(descriptor: int, subject: str) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    output = _tool([
        "ffmpeg", "-nostdin", "-v", "error", "-i", f"/dev/fd/{descriptor}",
        "-map", "0:a:0", "-c:a", "pcm_s64le", "-fflags", "+bitexact",
        "-flags:a", "+bitexact", "-f", "hash", "-hash", "sha256", "-",
    ], (descriptor,), subject).strip()
    digest = output.removeprefix("SHA256=")
    if len(digest) != 64:
        raise PublicAudioError("decode_hash_invalid", subject)
    return digest


def decoded_audio_sha256(
    source: Path,
    *,
    path_guard: PathGuard | None = None,
) -> str:
    """Hash decoded PCM through the same seekable descriptor path as sanitation."""

    source = Path(os.path.abspath(source.expanduser()))
    _reject_symlinks(source, allow_missing_leaf=False)
    if path_guard is not None:
        path_guard(source, allow_missing_leaf=False)
    with _regular_fd(source) as descriptor:
        return _decoded_sha256(descriptor, str(source))


def _file_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _assert_markers_absent(descriptor: int, markers: tuple[bytes, ...], subject: str) -> None:
    carry = b""
    width = max(map(len, markers))
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        block = (carry + chunk).lower()
        if any(marker in block for marker in markers):
            raise PublicAudioError("provenance_bytes", subject)
        carry = block[-width + 1:]


def _artifact(descriptor: int, path: Path) -> SanitizedAudio:
    probe = _probe_fd(descriptor, str(path))
    policy = _validate_public(probe, path.suffix, str(path))
    _assert_markers_absent(descriptor, _FORBIDDEN_BYTES, str(path))
    return SanitizedAudio(
        path, path.name, _file_sha256(descriptor), _decoded_sha256(descriptor, str(path)),
        os.fstat(descriptor).st_size, policy.name, probe.codec_name,
    )


def sanitize_public_audio(source: Path, target: Path, *, path_guard: PathGuard | None = None) -> SanitizedAudio:
    source, target = (
        Path(os.path.abspath(source.expanduser())),
        Path(os.path.abspath(target.expanduser())),
    )
    if source == target:
        raise PublicAudioError("source_is_target", str(source))
    _reject_symlinks(source, allow_missing_leaf=False)
    _reject_symlinks(target, allow_missing_leaf=True)
    if path_guard is not None:
        path_guard(source, allow_missing_leaf=False)
        path_guard(target, allow_missing_leaf=True)
    with _regular_fd(source) as source_fd:
        source_probe = _probe_fd(source_fd, str(source))
        policy = _policy(source_probe.format_name, str(source))
        if source_probe.audio_stream_count != 1 or source_probe.codec_name not in policy.codecs:
            raise PublicAudioError("source_stream_layout", str(source))
        if target.suffix.casefold() != policy.suffix:
            raise PublicAudioError("container_suffix", str(target))
        decoded_source = _decoded_sha256(source_fd, str(source))
        temporary_fd, temporary_value = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".partial", dir=target.parent
        )
        temporary = Path(temporary_value)
        published = False
        try:
            os.lseek(source_fd, 0, os.SEEK_SET)
            _tool([
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", f"/dev/fd/{source_fd}",
                "-map", "0:a:0", "-map_metadata", "-1", "-map_metadata:s:a:0", "-1",
                "-c:a", "copy", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                *policy.muxer_arguments, "-f", policy.name, f"/dev/fd/{temporary_fd}",
            ], (source_fd, temporary_fd), str(source))
            artifact = _artifact(temporary_fd, target)
            if artifact.codec_name != source_probe.codec_name:
                raise PublicAudioError("audio_codec_changed", str(source))
            if artifact.decoded_sha256 != decoded_source:
                raise PublicAudioError("audio_payload_changed", str(source))
            source_markers = tuple(
                value.casefold().encode() for value in source_probe.tag_values if len(value) >= 4
            )
            _assert_markers_absent(temporary_fd, _FORBIDDEN_BYTES + source_markers, str(target))
            _reject_symlinks(target, allow_missing_leaf=True)
            if path_guard is not None:
                path_guard(target, allow_missing_leaf=True)
            listed, opened = os.lstat(temporary), os.fstat(temporary_fd)
            if (listed.st_dev, listed.st_ino) != (opened.st_dev, opened.st_ino):
                raise PublicAudioError("temporary_replaced", str(target))
            os.fchmod(temporary_fd, 0o644)
            os.fsync(temporary_fd)
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.replace(
                    temporary.name, target.name,
                    src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                )
                published = True
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return artifact
        finally:
            os.close(temporary_fd)
            if not published:
                temporary.unlink(missing_ok=True)


def verify_public_audio(path: Path, *, path_guard: PathGuard | None = None) -> SanitizedAudio:
    path = Path(os.path.abspath(path.expanduser()))
    _reject_symlinks(path, allow_missing_leaf=False)
    if path_guard is not None:
        path_guard(path, allow_missing_leaf=False)
    with _regular_fd(path) as descriptor:
        return _artifact(descriptor, path)
