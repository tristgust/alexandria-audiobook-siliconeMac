from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET


_METADATA_FIELDS = ("title", "author", "narrator", "year", "description")
_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_EXPORT_COVER_BYTES = 10 * 1024 * 1024
_MAX_XML_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExportCover:
    kind: str
    relative_path: str
    media_type: str
    sha256: str
    data: bytes


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_object(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return _mapping(value)


def detect_export_cover_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_publication_metadata(
    *,
    root_dir: str | Path,
    receipt_metadata: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    root = Path(root_dir).expanduser().resolve()
    manifest = _json_object(root / "alexandria-project.json")
    source = _mapping(manifest.get("source"))
    state = _json_object(root / "state.json")
    publication = _mapping(_mapping(config).get("publication"))
    result = {
        "title": _text(source.get("title")) or _text(state.get("book_title")),
        "author": _text(source.get("author")) or _text(state.get("author")),
        "narrator": _text(publication.get("narrator")),
        "year": "",
        "description": "",
    }
    for field in _METADATA_FIELDS:
        override = _text(_mapping(receipt_metadata).get(field))
        if override:
            result[field] = override
    return result


def _regular_cover(path: Path, *, kind: str, root: Path) -> ExportCover | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        size = path.stat().st_size
        data = path.read_bytes() if 0 < size <= MAX_EXPORT_COVER_BYTES else b""
    except OSError:
        return None
    media_type = detect_export_cover_media_type(data)
    if not media_type:
        return None
    return ExportCover(
        kind=kind,
        relative_path=path.relative_to(root).as_posix(),
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )


def _safe_member_name(base: PurePosixPath, reference: str) -> str | None:
    path_text = unquote(urlsplit(reference).path)
    if not path_text or "\\" in path_text:
        return None
    candidate = base / PurePosixPath(path_text)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate.as_posix()


def _read_member(
    archive: zipfile.ZipFile,
    name: str,
    *,
    limit: int,
) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.is_dir() or info.file_size <= 0 or info.file_size > limit:
        return None
    if info.compress_size <= 0 or info.file_size > max(1024, info.compress_size * 200):
        return None
    try:
        data = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return None
    return data if len(data) == info.file_size else None


def _epub_cover(source: Path, *, root: Path) -> ExportCover | None:
    try:
        with zipfile.ZipFile(source) as archive:
            container_bytes = _read_member(
                archive,
                "META-INF/container.xml",
                limit=_MAX_XML_BYTES,
            )
            if container_bytes is None:
                return None
            container = ET.fromstring(container_bytes)
            rootfile = next(
                (
                    _text(item.get("full-path"))
                    for item in container.iter()
                    if item.tag.rsplit("}", 1)[-1] == "rootfile"
                    and _text(item.get("full-path"))
                ),
                "",
            )
            opf_name = _safe_member_name(PurePosixPath(), rootfile)
            if not opf_name:
                return None
            opf_bytes = _read_member(archive, opf_name, limit=_MAX_XML_BYTES)
            if opf_bytes is None:
                return None
            package = ET.fromstring(opf_bytes)
            cover_id = next(
                (
                    _text(item.get("content"))
                    for item in package.iter()
                    if item.tag.rsplit("}", 1)[-1] == "meta"
                    and _text(item.get("name")).casefold() == "cover"
                ),
                "",
            )
            candidates: list[tuple[str, str, str, set[str]]] = []
            for item in package.iter():
                if item.tag.rsplit("}", 1)[-1] != "item":
                    continue
                candidates.append(
                    (
                        _text(item.get("id")),
                        _text(item.get("href")),
                        _text(item.get("media-type")).casefold(),
                        set(_text(item.get("properties")).casefold().split()),
                    )
                )
            selected = next(
                (item for item in candidates if "cover-image" in item[3]),
                next((item for item in candidates if cover_id and item[0] == cover_id), None),
            )
            if selected is None or selected[2] not in set(_IMAGE_TYPES.values()):
                return None
            member = _safe_member_name(PurePosixPath(opf_name).parent, selected[1])
            data = _read_member(archive, member or "", limit=MAX_EXPORT_COVER_BYTES)
            media_type = detect_export_cover_media_type(data or b"")
            if data is None or media_type is None:
                return None
            relative = source.relative_to(root).as_posix() + "#" + str(member)
            return ExportCover(
                kind="source_epub",
                relative_path=relative,
                media_type=media_type,
                sha256=hashlib.sha256(data).hexdigest(),
                data=data,
            )
    except (OSError, zipfile.BadZipFile, ET.ParseError, ValueError):
        return None


def resolve_export_cover(root_dir: str | Path) -> ExportCover | None:
    root = Path(root_dir).expanduser().resolve()
    uploaded = _regular_cover(root / "m4b_cover.jpg", kind="uploaded", root=root)
    if uploaded:
        return uploaded
    project_cover_names = ("project_cover.jpg", "project_cover.jpeg", "project_cover.png", "project_cover.webp")
    for name in project_cover_names:
        cover = _regular_cover(root / name, kind="project", root=root)
        if cover:
            return cover
    source = _mapping(_json_object(root / "alexandria-project.json").get("source"))
    relative = _text(source.get("original_relative_path"))
    if not relative:
        return None
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    source_path = root / relative_path
    cursor = root
    for part in relative_path.parts:
        cursor /= part
        if cursor.is_symlink():
            return None
    try:
        source_path = source_path.resolve(strict=True)
        source_path.relative_to(root)
    except (OSError, ValueError):
        return None
    if source_path.suffix.casefold() != ".epub":
        return None
    return _epub_cover(source_path, root=root)


def export_cover_status(cover: ExportCover | None) -> dict[str, Any]:
    return {
        "exists": cover is not None,
        "sha256": cover.sha256 if cover else None,
        "relative_path": cover.relative_path if cover else None,
        "kind": cover.kind if cover else None,
        "media_type": cover.media_type if cover else None,
        "user_provided": bool(cover and cover.kind == "uploaded"),
    }


@contextmanager
def materialized_export_cover(
    cover: ExportCover | None,
    *,
    directory: str | Path,
) -> Iterator[Path | None]:
    if cover is None:
        yield None
        return
    suffix = next(
        (key for key, value in _IMAGE_TYPES.items() if value == cover.media_type),
        ".jpg",
    )
    handle, temp_name = tempfile.mkstemp(prefix=".export-cover.", suffix=suffix, dir=directory)
    temporary = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(cover.data)
            stream.flush()
            os.fsync(stream.fileno())
        yield temporary
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
