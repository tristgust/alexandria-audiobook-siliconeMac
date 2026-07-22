from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from typing import Any, Callable, Iterable, Mapping

from generation_state import fingerprint_value
from project_flow import build_project_flow_summary


PROJECT_CATALOG_SCHEMA_VERSION = 1
PROJECT_MANIFEST_SCHEMA_VERSION = 1
PROJECT_MANIFEST_FILENAME = "alexandria-project.json"
PROJECT_CATALOG_FILENAME = "projects.json"
PROJECTS_DIRECTORY_NAME = "Projects"
PROJECT_TRASH_DIRECTORY_NAME = "Trash"
PROJECT_DATA_ROOT_ENV = "ALEXANDRIA_DATA_ROOT"
SUPPORTED_GENERATION_METHODS = frozenset(
    {"local", "chatgpt_task_bundle", "import_existing_script"}
)
SUPPORTED_PROJECT_PRESETS = frozenset(
    {"standard", "maximum_fidelity", "faster_draft", "custom"}
)
SOURCE_METHOD_SUFFIXES = {
    "local": frozenset({".txt", ".epub"}),
    "chatgpt_task_bundle": frozenset({".txt", ".epub"}),
    "import_existing_script": frozenset({".json"}),
}

# Authoritative user/project artifacts that may be carried into a managed
# duplicate. Application code, environments, caches, and model snapshots are
# intentionally absent.
DUPLICATE_FILE_ALLOWLIST = frozenset(
    {
        "state.json",
        "annotated_script.json",
        "annotated_script.meta.json",
        "chunks.json",
        "voice_config.json",
        "character_roster.draft.json",
        "character_roster.json",
        "audio_validity.json",
        "cloned_audiobook.mp3",
        "audiobook.mp3",
        "audiobook.m4b",
        "audacity_export.zip",
        "m4b_cover.jpg",
        "migration_state.json",
    }
)
DUPLICATE_DIRECTORY_ALLOWLIST = frozenset(
    {
        "sources",
        "uploads",
        "imports",
        "scripts",
        "voicelines",
        "designed_voices",
        "clone_voices",
        "persona_refs",
        "character_roster_history",
        "voice_training_projects",
        "speaker_management_history",
        "lora_models",
        "lora_datasets",
        "dataset_builder",
        "preparer_output",
        "external_workflows",
    }
)
DUPLICATE_TRANSIENT_NAMES = frozenset(
    {
        "generation_state.json",
        "character_roster_state.json",
        "persona_visual_state.json",
        "temp_m4b_combined.wav",
        "temp_m4b_meta.txt",
        ".DS_Store",
        "__pycache__",
    }
)


class ProjectCatalogError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.context = dict(context or {})

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.detail,
            "context": self.context,
        }


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def application_data_root(
    *,
    environment: Mapping[str, str] | None = None,
    home: str | Path | None = None,
    platform_name: str | None = None,
) -> Path:
    environment_value = environment if environment is not None else os.environ
    configured = str(environment_value.get(PROJECT_DATA_ROOT_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    home_path = Path(home).expanduser().resolve() if home is not None else Path.home()
    platform_value = platform_name or sys.platform
    if platform_value == "darwin":
        return home_path / "Library" / "Application Support" / "Alexandria"
    return home_path / ".alexandria"


def project_catalog_path(data_root: str | Path) -> Path:
    return Path(data_root).expanduser().resolve() / PROJECT_CATALOG_FILENAME


def managed_projects_root(data_root: str | Path) -> Path:
    return Path(data_root).expanduser().resolve() / PROJECTS_DIRECTORY_NAME


def project_trash_root(data_root: str | Path) -> Path:
    return Path(data_root).expanduser().resolve() / PROJECT_TRASH_DIRECTORY_NAME


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _safe_name(value: Any, *, field: str, maximum: int = 160) -> str:
    text = _text(value)
    if text is None:
        raise ProjectCatalogError(
            status_code=422,
            code=f"{field}_required",
            detail=f"{field.replace('_', ' ').title()} is required.",
        )
    if len(text) > maximum:
        raise ProjectCatalogError(
            status_code=422,
            code=f"{field}_too_long",
            detail=f"{field.replace('_', ' ').title()} must be {maximum} characters or fewer.",
        )
    if any(ord(character) < 32 for character in text):
        raise ProjectCatalogError(
            status_code=422,
            code=f"{field}_invalid",
            detail=f"{field.replace('_', ' ').title()} contains control characters.",
        )
    return text


def _safe_basename(value: Any) -> str:
    text = _safe_name(value, field="filename", maximum=255)
    basename = Path(text).name
    if basename != text or text in {".", ".."}:
        raise ProjectCatalogError(
            status_code=422,
            code="source_filename_unsafe",
            detail="The source filename is unsafe.",
        )
    return basename


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:56] or "project"


def _stable_identifier(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:20]}"


def _new_project_id() -> str:
    return f"project_{uuid.uuid4().hex[:20]}"


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _empty_catalog() -> dict[str, Any]:
    return {
        "schema_version": PROJECT_CATALOG_SCHEMA_VERSION,
        "updated_at_utc": None,
        "last_selected_project_id": None,
        "projects": [],
        "trash": [],
    }


def _catalog_fingerprint(catalog: Mapping[str, Any]) -> str:
    normalized = {
        "schema_version": catalog.get("schema_version"),
        "last_selected_project_id": catalog.get("last_selected_project_id"),
        "projects": _list(catalog.get("projects")),
        "trash": _list(catalog.get("trash")),
    }
    return fingerprint_value(normalized)


def _manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    normalized = dict(manifest)
    normalized.pop("manifest_fingerprint", None)
    return fingerprint_value(normalized)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProjectCatalogError(
            status_code=409,
            code="json_unreadable",
            detail=f"Could not read {path.name}: {exc}",
            context={"filename": path.name},
        ) from exc


def _write_bytes_atomic(value: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomic(value: Any, path: Path) -> None:
    encoded = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _write_bytes_atomic(encoded, path)


def _validate_catalog(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectCatalogError(
            status_code=409,
            code="project_catalog_invalid",
            detail="The project catalog must be a JSON object.",
        )
    if value.get("schema_version") != PROJECT_CATALOG_SCHEMA_VERSION:
        raise ProjectCatalogError(
            status_code=409,
            code="project_catalog_version_unsupported",
            detail="The project catalog schema version is unsupported.",
            context={"schema_version": value.get("schema_version")},
        )
    projects = _list(value.get("projects"))
    identifiers: set[str] = set()
    normalized_projects: list[dict[str, Any]] = []
    for index, entry_value in enumerate(projects):
        entry = _mapping(entry_value)
        project_id = _text(entry.get("id"))
        root_path = _text(entry.get("root_path"))
        name = _text(entry.get("name"))
        if project_id is None or root_path is None or name is None:
            raise ProjectCatalogError(
                status_code=409,
                code="project_catalog_entry_invalid",
                detail=f"Project catalog entry {index} is incomplete.",
            )
        if project_id in identifiers:
            raise ProjectCatalogError(
                status_code=409,
                code="project_catalog_duplicate_id",
                detail=f"Project ID {project_id} appears more than once.",
            )
        identifiers.add(project_id)
        normalized_projects.append(dict(entry))
    normalized = {
        "schema_version": PROJECT_CATALOG_SCHEMA_VERSION,
        "updated_at_utc": _text(value.get("updated_at_utc")),
        "last_selected_project_id": _text(value.get("last_selected_project_id")),
        "projects": normalized_projects,
        "trash": [dict(item) for item in _list(value.get("trash")) if isinstance(item, Mapping)],
    }
    return normalized


def load_project_catalog(catalog_path: str | Path) -> dict[str, Any]:
    path = Path(catalog_path).expanduser().resolve()
    if not path.exists():
        catalog = _empty_catalog()
    else:
        catalog = _validate_catalog(_read_json(path))
    return {
        **catalog,
        "catalog_fingerprint": _catalog_fingerprint(catalog),
    }


def _catalog_for_write(catalog: Mapping[str, Any], *, at_utc: str) -> dict[str, Any]:
    return {
        "schema_version": PROJECT_CATALOG_SCHEMA_VERSION,
        "updated_at_utc": at_utc,
        "last_selected_project_id": catalog.get("last_selected_project_id"),
        "projects": [dict(item) for item in _list(catalog.get("projects"))],
        "trash": [dict(item) for item in _list(catalog.get("trash"))],
    }


def _assert_catalog_fingerprint(
    catalog: Mapping[str, Any],
    expected_catalog_fingerprint: str | None,
) -> None:
    if expected_catalog_fingerprint is None:
        return
    current = _catalog_fingerprint(catalog)
    if expected_catalog_fingerprint != current:
        raise ProjectCatalogError(
            status_code=409,
            code="stale_project_catalog",
            detail="The project catalog changed after this view was loaded.",
            context={"current_catalog_fingerprint": current},
        )


@contextlib.contextmanager
def _catalog_lock(data_root: Path):
    data_root.mkdir(parents=True, exist_ok=True)
    lock_path = data_root / ".projects.lock"
    handle = lock_path.open("a+b")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def _validate_source(
    source_path: str | Path,
    *,
    generation_method: str,
    maximum_bytes: int = 512 * 1024 * 1024,
) -> dict[str, Any]:
    if generation_method not in SUPPORTED_GENERATION_METHODS:
        raise ProjectCatalogError(
            status_code=422,
            code="generation_method_unsupported",
            detail="Generation method must be local, chatgpt_task_bundle, or import_existing_script.",
        )
    path = Path(source_path).expanduser().resolve()
    if not path.exists():
        raise ProjectCatalogError(
            status_code=422,
            code="project_source_missing",
            detail="The selected source file does not exist.",
        )
    if not path.is_file():
        raise ProjectCatalogError(
            status_code=422,
            code="project_source_not_file",
            detail="The selected source must be a regular file.",
        )
    if path.is_symlink():
        raise ProjectCatalogError(
            status_code=422,
            code="project_source_symlink_unsupported",
            detail="Project creation does not accept a symbolic-link source.",
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ProjectCatalogError(
            status_code=422,
            code="project_source_unreadable",
            detail=f"The selected source could not be inspected: {exc}",
        ) from exc
    if size <= 0:
        raise ProjectCatalogError(
            status_code=422,
            code="project_source_empty",
            detail="The selected source is empty.",
        )
    if size > maximum_bytes:
        raise ProjectCatalogError(
            status_code=413,
            code="project_source_too_large",
            detail="The selected source exceeds the project-creation size limit.",
            context={"size_bytes": size, "maximum_bytes": maximum_bytes},
        )
    suffix = path.suffix.casefold()
    allowed = SOURCE_METHOD_SUFFIXES[generation_method]
    if suffix not in allowed:
        raise ProjectCatalogError(
            status_code=422,
            code="project_source_type_unsupported",
            detail=(
                f"{generation_method} accepts: "
                + ", ".join(sorted(allowed))
                + "."
            ),
            context={"suffix": suffix},
        )
    try:
        fingerprint = _sha256_file(path)
    except OSError as exc:
        raise ProjectCatalogError(
            status_code=422,
            code="project_source_unreadable",
            detail=f"The selected source could not be read: {exc}",
        ) from exc
    return {
        "path": path,
        "basename": _safe_basename(path.name),
        "suffix": suffix,
        "size_bytes": size,
        "fingerprint": fingerprint,
    }


class _ProjectHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "head"}:
            self.ignored_depth += 1
        elif tag.casefold() in {"p", "div", "br", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "head"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag.casefold() in {"p", "div", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _safe_epub_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    total_uncompressed = 0
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            raise ProjectCatalogError(
                status_code=422,
                code="epub_member_unsafe",
                detail=f"EPUB member path is unsafe: {info.filename!r}.",
            )
        total_uncompressed += max(info.file_size, 0)
        if total_uncompressed > 1024 * 1024 * 1024:
            raise ProjectCatalogError(
                status_code=413,
                code="epub_expansion_too_large",
                detail="EPUB expanded content exceeds the safety limit.",
            )
        if info.compress_size > 0 and info.file_size / info.compress_size > 250:
            raise ProjectCatalogError(
                status_code=422,
                code="epub_compression_ratio_unsafe",
                detail=f"EPUB member has an unsafe compression ratio: {info.filename!r}.",
            )
        members.append(info)
    return members


def _normalized_epub_member(base: PurePosixPath, href: str) -> str:
    decoded = unquote(urlsplit(href).path)
    candidate = base / PurePosixPath(decoded)
    normalized: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_member_unsafe",
                    detail=f"EPUB reference escapes the archive root: {href!r}.",
                )
            normalized.pop()
            continue
        normalized.append(part)
    if not normalized:
        raise ProjectCatalogError(
            status_code=422,
            code="epub_member_unsafe",
            detail=f"EPUB reference is empty or unsafe: {href!r}.",
        )
    return "/".join(normalized)


def _epub_metadata(source: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(source) as archive:
            members = _safe_epub_members(archive)
            names = {info.filename for info in members}
            container_name = "META-INF/container.xml"
            if container_name not in names:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_container_missing",
                    detail="The EPUB is missing META-INF/container.xml.",
                )
            try:
                container = ET.fromstring(archive.read(container_name))
            except (ET.ParseError, KeyError, RuntimeError, OSError) as exc:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_container_invalid",
                    detail=f"The EPUB container metadata is invalid: {exc}",
                ) from exc
            namespace = {
                "container": "urn:oasis:names:tc:opendocument:xmlns:container"
            }
            rootfile = container.find(".//container:rootfile", namespace)
            opf_reference = rootfile.get("full-path") if rootfile is not None else None
            if not opf_reference:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_rootfile_missing",
                    detail="The EPUB container does not identify a package document.",
                )
            opf_name = _normalized_epub_member(PurePosixPath(), opf_reference)
            if opf_name not in names:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_rootfile_unavailable",
                    detail="The EPUB package document is unavailable.",
                )
            try:
                opf = ET.fromstring(archive.read(opf_name))
            except (ET.ParseError, KeyError, RuntimeError, OSError) as exc:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_package_invalid",
                    detail=f"The EPUB package document is invalid: {exc}",
                ) from exc
            opf_namespace = opf.tag.split("}")[0] + "}" if "}" in opf.tag else ""
            opf_base = PurePosixPath(opf_name).parent

            def first_metadata(local_name: str) -> str | None:
                for item in opf.iter():
                    if item.tag.rsplit("}", 1)[-1].casefold() != local_name.casefold():
                        continue
                    value = _text(item.text)
                    if value:
                        return value
                return None

            manifest: dict[str, dict[str, Any]] = {}
            cover_id: str | None = None
            for meta in opf.iter():
                if meta.tag.rsplit("}", 1)[-1].casefold() != "meta":
                    continue
                if str(meta.get("name") or "").casefold() == "cover":
                    cover_id = _text(meta.get("content"))
                    break
            for item in opf.findall(f".//{opf_namespace}item"):
                item_id = _text(item.get("id"))
                href = _text(item.get("href"))
                if not item_id or not href:
                    continue
                member_name = _normalized_epub_member(opf_base, href)
                manifest[item_id] = {
                    "member_name": member_name,
                    "media_type": str(item.get("media-type") or "").casefold(),
                    "properties": {
                        value.casefold()
                        for value in str(item.get("properties") or "").split()
                        if value.strip()
                    },
                }
            spine_ids = [
                _text(itemref.get("idref"))
                for itemref in opf.findall(f".//{opf_namespace}itemref")
            ]
            chapter_count = sum(
                1
                for item_id in spine_ids
                if item_id
                and item_id in manifest
                and (
                    "html" in manifest[item_id]["media_type"]
                    or PurePosixPath(manifest[item_id]["member_name"]).suffix.casefold()
                    in {".xhtml", ".html", ".htm"}
                )
            )
            cover_item = next(
                (
                    item
                    for item in manifest.values()
                    if "cover-image" in item["properties"]
                ),
                manifest.get(cover_id) if cover_id else None,
            )
            cover_data_url: str | None = None
            if cover_item:
                member_name = str(cover_item["member_name"])
                media_type = str(cover_item["media_type"])
                if (
                    member_name in names
                    and media_type.startswith("image/")
                    and archive.getinfo(member_name).file_size <= 10 * 1024 * 1024
                ):
                    try:
                        encoded = base64.b64encode(archive.read(member_name)).decode("ascii")
                    except (KeyError, RuntimeError, OSError) as exc:
                        raise ProjectCatalogError(
                            status_code=422,
                            code="epub_cover_unreadable",
                            detail=f"Could not read the EPUB cover image: {exc}",
                        ) from exc
                    cover_data_url = f"data:{media_type};base64,{encoded}"
            return {
                "title": first_metadata("title"),
                "author": first_metadata("creator"),
                "language": first_metadata("language"),
                "chapter_count": chapter_count,
                "cover_data_url": cover_data_url,
            }
    except zipfile.BadZipFile as exc:
        raise ProjectCatalogError(
            status_code=422,
            code="epub_invalid",
            detail=f"The EPUB archive is invalid: {exc}",
        ) from exc


def inspect_project_source(
    source_path: str | Path,
    *,
    generation_method: str,
) -> dict[str, Any]:
    source_info = _validate_source(
        source_path,
        generation_method=generation_method,
    )
    path = Path(source_info["path"])
    suffix = str(source_info["suffix"])
    title = path.stem
    author: str | None = None
    language: str | None = None
    chapter_count: int | None = None
    cover_data_url: str | None = None
    source_type = "text"
    entry_count: int | None = None

    if generation_method == "import_existing_script":
        entries = _validate_import_script(path)
        source_type = "alexandria_script"
        entry_count = len(entries)
    elif suffix == ".epub":
        metadata = _epub_metadata(path)
        readable_text = _extract_epub_text(path)
        source_type = "epub"
        title = _text(metadata.get("title")) or title
        author = _text(metadata.get("author"))
        language = _text(metadata.get("language"))
        chapter_count = int(metadata.get("chapter_count") or 0)
        cover_data_url = _text(metadata.get("cover_data_url"))
        if not readable_text.strip():
            raise ProjectCatalogError(
                status_code=422,
                code="epub_text_missing",
                detail="No readable text content was found in the EPUB spine.",
            )
    else:
        try:
            source_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProjectCatalogError(
                status_code=422,
                code="project_source_text_invalid",
                detail=f"The selected text source is not valid UTF-8: {exc}",
            ) from exc
        if not source_text.strip():
            raise ProjectCatalogError(
                status_code=422,
                code="project_source_text_empty",
                detail="The selected text source has no readable content.",
            )

    return {
        "schema_version": 1,
        "valid": True,
        "filename": source_info["basename"],
        "source_type": source_type,
        "size_bytes": source_info["size_bytes"],
        "fingerprint": source_info["fingerprint"],
        "generation_method": generation_method,
        "title": title,
        "author": author,
        "language": language,
        "chapter_count": chapter_count,
        "entry_count": entry_count,
        "cover_data_url": cover_data_url,
        "suggested_project_name": title,
    }


def _extract_epub_text(source: Path) -> str:
    try:
        with zipfile.ZipFile(source) as archive:
            members = _safe_epub_members(archive)
            names = {info.filename for info in members}
            container_name = "META-INF/container.xml"
            if container_name not in names:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_container_missing",
                    detail="The EPUB is missing META-INF/container.xml.",
                )
            try:
                container = ET.fromstring(archive.read(container_name))
            except (ET.ParseError, KeyError, RuntimeError, OSError) as exc:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_container_invalid",
                    detail=f"The EPUB container metadata is invalid: {exc}",
                ) from exc
            namespace = {
                "container": "urn:oasis:names:tc:opendocument:xmlns:container"
            }
            rootfile = container.find(".//container:rootfile", namespace)
            opf_reference = rootfile.get("full-path") if rootfile is not None else None
            if not opf_reference:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_rootfile_missing",
                    detail="The EPUB container does not identify a package document.",
                )
            opf_name = _normalized_epub_member(PurePosixPath(), opf_reference)
            if opf_name not in names:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_rootfile_unavailable",
                    detail="The EPUB package document is unavailable.",
                )
            try:
                opf = ET.fromstring(archive.read(opf_name))
            except (ET.ParseError, KeyError, RuntimeError, OSError) as exc:
                raise ProjectCatalogError(
                    status_code=422,
                    code="epub_package_invalid",
                    detail=f"The EPUB package document is invalid: {exc}",
                ) from exc
            opf_namespace = opf.tag.split("}")[0] + "}" if "}" in opf.tag else ""
            opf_base = PurePosixPath(opf_name).parent
            manifest: dict[str, str] = {}
            for item in opf.findall(f".//{opf_namespace}item"):
                item_id = item.get("id")
                href = item.get("href")
                media_type = str(item.get("media-type") or "").casefold()
                if not item_id or not href:
                    continue
                member_name = _normalized_epub_member(opf_base, href)
                suffix = PurePosixPath(member_name).suffix.casefold()
                if "html" in media_type or suffix in {".xhtml", ".html", ".htm"}:
                    manifest[item_id] = member_name
            spine = [
                itemref.get("idref")
                for itemref in opf.findall(f".//{opf_namespace}itemref")
                if itemref.get("idref")
            ]
            text_parts: list[str] = []
            for item_id in spine:
                member_name = manifest.get(str(item_id))
                if member_name is None:
                    continue
                if member_name not in names:
                    raise ProjectCatalogError(
                        status_code=422,
                        code="epub_spine_member_unavailable",
                        detail=f"EPUB spine member is unavailable: {member_name!r}.",
                    )
                try:
                    raw = archive.read(member_name)
                except (KeyError, RuntimeError, OSError) as exc:
                    raise ProjectCatalogError(
                        status_code=422,
                        code="epub_member_unreadable",
                        detail=f"Could not read EPUB member {member_name!r}: {exc}",
                    ) from exc
                extractor = _ProjectHTMLTextExtractor()
                extractor.feed(raw.decode("utf-8", errors="replace"))
                text = extractor.text()
                if text:
                    text_parts.append(text)
    except zipfile.BadZipFile as exc:
        raise ProjectCatalogError(
            status_code=422,
            code="epub_invalid",
            detail=f"The EPUB archive is invalid: {exc}",
        ) from exc
    combined = "\n\n".join(text_parts).strip()
    if not combined:
        raise ProjectCatalogError(
            status_code=422,
            code="epub_text_missing",
            detail="No readable text content was found in the EPUB spine.",
        )
    return combined


def _validate_import_script(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProjectCatalogError(
            status_code=422,
            code="import_script_invalid_json",
            detail=f"The imported Alexandria Script is invalid JSON: {exc}",
        ) from exc
    if not isinstance(value, list) or not value:
        raise ProjectCatalogError(
            status_code=422,
            code="import_script_invalid_shape",
            detail="The imported Alexandria Script must be a non-empty JSON array.",
        )
    entries: list[dict[str, Any]] = []
    for index, entry_value in enumerate(value):
        entry = _mapping(entry_value)
        if not all(isinstance(entry.get(field), str) for field in ("speaker", "text", "instruct")):
            raise ProjectCatalogError(
                status_code=422,
                code="import_script_entry_invalid",
                detail=f"Imported Script entry {index} must contain string speaker, text, and instruct fields.",
            )
        if not _text(entry.get("speaker")) or not _text(entry.get("text")):
            raise ProjectCatalogError(
                status_code=422,
                code="import_script_entry_empty",
                detail=f"Imported Script entry {index} has an empty speaker or text field.",
            )
        entries.append(dict(entry))
    return entries


def _initial_flow_snapshot(
    *,
    project_id: str,
    project_name: str,
    source: Mapping[str, Any],
    generation_method: str,
    at_utc: str,
) -> dict[str, Any]:
    import_candidate = generation_method == "import_existing_script"
    script = {
        "source_available": True,
        "process": {"running": False},
        "resumable": False,
        "failed": False,
        "artifact_exists": False,
        "import_candidate_exists": import_candidate,
        "structure_valid": None,
        "attribution_valid": None,
        "fidelity_valid": None,
        "artifact_current": None,
        "provenance_recorded": None,
        "finalization_complete": None,
        "review_required": import_candidate,
        "accepted": False,
        "fingerprints": {
            "source": source.get("fingerprint"),
            "script": None,
            "generation": None,
        },
    }
    return build_project_flow_summary(
        project={
            "id": project_id,
            "name": project_name,
            "latest_meaningful_activity": at_utc,
            "archive_state": "active",
        },
        source={
            "selected": True,
            "available": True,
            "title": source.get("title"),
            "filename": source.get("original_filename"),
            "type": source.get("type"),
            "source_language": source.get("source_language"),
            "output_language": source.get("output_language"),
            "fingerprint": source.get("fingerprint"),
            "error": None,
        },
        script=script,
        cast={
            "process": {"running": False},
            "resumable": False,
            "failed": False,
            "roster_exists": False,
            "review_required": False,
            "roster_approved": False,
            "roster_current": None,
            "required_speaking_characters": 0,
            "valid_production_voices": 0,
            "unresolved_identity_ids": [],
            "ambiguous_mapping_ids": [],
            "missing_voice_ids": [],
            "invalid_voice_ids": [],
            "invalid_clone_ids": [],
            "controlled_clone_approval_missing_ids": [],
            "invalid_adapter_ids": [],
            "stale_voice_ids": [],
            "fingerprints": {"script": None, "roster": None, "voice_config": None},
        },
        produce={
            "process": {"running": False},
            "resumable": False,
            "required_chunks": 0,
            "current_chunks": 0,
            "missing_chunk_ids": [],
            "stale_chunk_ids": [],
            "failed_chunk_ids": [],
            "hash_invalid_chunk_ids": [],
            "review_chunk_ids": [],
            "listening_chunk_ids": [],
            "fingerprints": {"chunks": None, "voice_config": None, "synthesis": None},
        },
        export={
            "process": {"running": False},
            "failed": False,
            "missing_metadata_fields": [
                field
                for field in ("title", "author")
                if not _text(source.get(field))
            ],
            "invalid_chapter_ids": [],
            "unavailable_formats": [],
            "output_exists": False,
            "output_current": False,
            "output_valid": False,
            "fingerprints": {"build_dependencies": None, "output": None},
        },
        compatibility={"state": "current"},
        generated_at_utc=at_utc,
    )


def _manifest_from_request(
    *,
    project_id: str,
    project_name: str,
    source: Mapping[str, Any],
    generation_method: str,
    preset: str,
    template_id: str | None,
    at_utc: str,
) -> dict[str, Any]:
    manifest = {
        "schema_version": PROJECT_MANIFEST_SCHEMA_VERSION,
        "project_id": project_id,
        "name": project_name,
        "created_at_utc": at_utc,
        "updated_at_utc": at_utc,
        "archive_state": "active",
        "source": dict(source),
        "generation": {
            "method": generation_method,
            "preset": preset,
        },
        "creation": {
            "template_id": template_id,
        },
        "runtime": {
            "storage_kind": "managed",
            "activation_contract": "managed_runtime",
        },
        "flow_snapshot": _initial_flow_snapshot(
            project_id=project_id,
            project_name=project_name,
            source=source,
            generation_method=generation_method,
            at_utc=at_utc,
        ),
    }
    manifest["manifest_fingerprint"] = _manifest_fingerprint(manifest)
    return manifest


def _validate_manifest(value: Any, *, expected_project_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectCatalogError(
            status_code=409,
            code="project_manifest_invalid",
            detail="The project manifest must be a JSON object.",
        )
    if value.get("schema_version") != PROJECT_MANIFEST_SCHEMA_VERSION:
        raise ProjectCatalogError(
            status_code=409,
            code="project_manifest_version_unsupported",
            detail="The project manifest schema version is unsupported.",
        )
    project_id = _text(value.get("project_id"))
    name = _text(value.get("name"))
    if project_id is None or name is None:
        raise ProjectCatalogError(
            status_code=409,
            code="project_manifest_incomplete",
            detail="The project manifest does not contain a stable ID and name.",
        )
    if expected_project_id is not None and project_id != expected_project_id:
        raise ProjectCatalogError(
            status_code=409,
            code="project_manifest_identity_mismatch",
            detail="The project manifest identity does not match the catalog entry.",
        )
    expected_fingerprint = _text(value.get("manifest_fingerprint"))
    actual_fingerprint = _manifest_fingerprint(value)
    if expected_fingerprint != actual_fingerprint:
        raise ProjectCatalogError(
            status_code=409,
            code="project_manifest_fingerprint_invalid",
            detail="The project manifest fingerprint does not match its content.",
            context={"actual_manifest_fingerprint": actual_fingerprint},
        )
    return dict(value)


def _catalog_entry_from_manifest(manifest: Mapping[str, Any], project_root: Path) -> dict[str, Any]:
    source = _mapping(manifest.get("source"))
    generation = _mapping(manifest.get("generation"))
    creation = _mapping(manifest.get("creation"))
    return {
        "id": manifest["project_id"],
        "name": manifest["name"],
        "root_path": str(project_root.resolve()),
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "source_title": source.get("title"),
        "source_author": source.get("author"),
        "source_filename": source.get("original_filename"),
        "source_type": source.get("type"),
        "source_language": source.get("source_language"),
        "output_language": source.get("output_language"),
        "generation_method": generation.get("method"),
        "preset": generation.get("preset"),
        "template_id": creation.get("template_id"),
        "archive_state": manifest.get("archive_state", "active"),
        "created_at_utc": manifest.get("created_at_utc"),
        "updated_at_utc": manifest.get("updated_at_utc"),
        "last_opened_at_utc": None,
        "storage_kind": "managed",
    }


def _write_new_project_contents(
    *,
    staging: Path,
    final_root: Path,
    source_info: Mapping[str, Any],
    project_id: str,
    project_name: str,
    book_title: str,
    author: str | None,
    source_language: str,
    output_language: str,
    generation_method: str,
    preset: str,
    template_id: str | None,
    at_utc: str,
) -> dict[str, Any]:
    staging.mkdir(parents=True, exist_ok=False)
    sources_dir = staging / "sources"
    sources_dir.mkdir()
    original = Path(source_info["path"])
    original_name = _safe_basename(source_info["basename"])
    stored_original = sources_dir / original_name
    shutil.copy2(original, stored_original)

    prepared_relative: str
    candidate_relative: str | None = None
    if generation_method == "import_existing_script":
        entries = _validate_import_script(original)
        imports_dir = staging / "imports"
        imports_dir.mkdir()
        candidate = imports_dir / "script-candidate.json"
        _write_json_atomic(entries, candidate)
        prepared_relative = candidate.relative_to(staging).as_posix()
        candidate_relative = prepared_relative
        source_type = "alexandria_script"
    elif source_info["suffix"] == ".epub":
        text = _extract_epub_text(original)
        prepared = sources_dir / f"{Path(original_name).stem}.txt"
        prepared.write_text(text + "\n", encoding="utf-8")
        prepared_relative = prepared.relative_to(staging).as_posix()
        source_type = "epub"
    else:
        try:
            text = original.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProjectCatalogError(
                status_code=422,
                code="project_source_text_invalid",
                detail=f"The selected text source is not valid UTF-8: {exc}",
            ) from exc
        if not text.strip():
            raise ProjectCatalogError(
                status_code=422,
                code="project_source_text_empty",
                detail="The selected text source has no readable content.",
            )
        prepared_relative = stored_original.relative_to(staging).as_posix()
        source_type = "text"

    final_prepared_path = final_root / prepared_relative
    source = {
        "title": book_title,
        "author": author,
        "original_filename": original_name,
        "type": source_type,
        "source_language": source_language,
        "output_language": output_language,
        "original_relative_path": stored_original.relative_to(staging).as_posix(),
        "prepared_relative_path": prepared_relative,
        "import_candidate_relative_path": candidate_relative,
        "fingerprint": source_info["fingerprint"],
        "size_bytes": source_info["size_bytes"],
    }
    manifest = _manifest_from_request(
        project_id=project_id,
        project_name=project_name,
        source=source,
        generation_method=generation_method,
        preset=preset,
        template_id=template_id,
        at_utc=at_utc,
    )
    state = {
        "schema_version": 1,
        "project_id": project_id,
        "project_name": project_name,
        "book_title": book_title,
        "author": author,
        "input_file_path": str(final_prepared_path),
        "source_relative_path": prepared_relative,
        "source_language": source_language,
        "output_language": output_language,
        "generation_method": generation_method,
        "preset": preset,
        "template_id": template_id,
    }
    _write_json_atomic(state, staging / "state.json")
    _write_json_atomic(manifest, staging / PROJECT_MANIFEST_FILENAME)
    _validate_manifest(
        _read_json(staging / PROJECT_MANIFEST_FILENAME),
        expected_project_id=project_id,
    )
    if not (staging / prepared_relative).is_file():
        raise ProjectCatalogError(
            status_code=500,
            code="project_creation_validation_failed",
            detail="The prepared source was not written into the new project.",
        )
    return manifest


def _public_project(
    *,
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
    availability_state: str,
    current_project_id: str | None,
    selected_project_id: str | None,
    compatibility_state: str = "current",
    error: str | None = None,
) -> dict[str, Any]:
    flow = _mapping(_mapping(manifest).get("flow_snapshot"))
    stage_map = _mapping(flow.get("stage_map"))
    recommended_stage = _text(flow.get("recommended_stage"))
    recommended = _mapping(stage_map.get(recommended_stage)) if recommended_stage else {}
    project_id = str(entry.get("id"))
    source = _mapping(_mapping(manifest).get("source"))
    source_type = entry.get("source_type") or source.get("type")
    is_current = project_id == current_project_id
    activation_state = (
        "current"
        if is_current
        else "available"
        if availability_state == "available" and entry.get("archive_state") != "archived"
        else availability_state
    )
    return {
        "id": project_id,
        "name": entry.get("name"),
        "source_title": entry.get("source_title") or source.get("title"),
        "source_author": entry.get("source_author") or source.get("author"),
        "source_filename": entry.get("source_filename") or source.get("original_filename"),
        "source_type": source_type,
        "cover_url": (
            f"/api/projects/{project_id}/cover"
            if source_type == "epub"
            else None
        ),
        "source_language": entry.get("source_language"),
        "output_language": entry.get("output_language"),
        "generation_method": entry.get("generation_method"),
        "preset": entry.get("preset"),
        "template_id": entry.get("template_id") or _mapping(_mapping(manifest).get("creation")).get("template_id"),
        "current_recommended_stage": recommended_stage,
        "stage_summary": recommended.get("summary"),
        "stage_states": {
            key: _mapping(stage_map.get(key)).get("state")
            for key in ("script", "cast", "produce", "export")
        },
        "blocker_count": int(flow.get("blocker_count") or 0),
        "latest_meaningful_activity": (
            _mapping(flow.get("project")).get("latest_meaningful_activity")
            or entry.get("updated_at_utc")
        ),
        "resumable_operation": flow.get("resumable_operation"),
        "compatibility_state": compatibility_state,
        "completion_state": flow.get("completion_state") or "requires_work",
        "archive_state": entry.get("archive_state") or "active",
        "availability_state": availability_state,
        "activation_state": activation_state,
        "current": is_current,
        "selected": project_id == selected_project_id,
        "storage_kind": entry.get("storage_kind") or "managed",
        "error": error,
        "safe_next_action": flow.get("safe_next_action"),
        "technical_details": {
            "project_path": entry.get("root_path"),
            "manifest_fingerprint": entry.get("manifest_fingerprint"),
        },
    }


def _legacy_entry(
    *,
    current_project_root: str | Path,
    current_flow_summary: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(current_project_root).expanduser().resolve()
    project = _mapping(current_flow_summary.get("project"))
    source = _mapping(current_flow_summary.get("source"))
    project_id = _text(project.get("id")) or _stable_identifier("project", root)
    entry = {
        "id": project_id,
        "name": _text(project.get("name")) or "Legacy Alexandria project",
        "root_path": str(root),
        "manifest_fingerprint": fingerprint_value(
            {
                "project_id": project_id,
                "root_path": str(root),
                "source_fingerprint": source.get("fingerprint"),
            }
        ),
        "source_title": source.get("title"),
        "source_author": source.get("author"),
        "source_filename": source.get("filename"),
        "source_type": source.get("type"),
        "source_language": source.get("source_language"),
        "output_language": source.get("output_language"),
        "generation_method": _mapping(_mapping(current_flow_summary.get("stage_map")).get("script")).get("provenance", {}).get("method") if isinstance(_mapping(_mapping(current_flow_summary.get("stage_map")).get("script")).get("provenance"), Mapping) else None,
        "preset": None,
        "template_id": None,
        "archive_state": project.get("archive_state") or "active",
        "created_at_utc": None,
        "updated_at_utc": project.get("latest_meaningful_activity"),
        "last_opened_at_utc": project.get("latest_meaningful_activity"),
        "storage_kind": "legacy_checkout",
    }
    manifest = {
        "schema_version": PROJECT_MANIFEST_SCHEMA_VERSION,
        "project_id": project_id,
        "name": entry["name"],
        "archive_state": entry["archive_state"],
        "source": {
            "title": source.get("title"),
            "author": source.get("author"),
            "original_filename": source.get("filename"),
            "type": source.get("type"),
            "source_language": source.get("source_language"),
            "output_language": source.get("output_language"),
            "fingerprint": source.get("fingerprint"),
        },
        "runtime": {
            "storage_kind": "legacy_checkout",
            "activation_contract": "current",
        },
        "flow_snapshot": dict(current_flow_summary),
        "manifest_fingerprint": entry["manifest_fingerprint"],
    }
    return entry, manifest


def _inspect_catalog_entry(entry: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str, str | None]:
    root_text = _text(entry.get("root_path"))
    if root_text is None:
        return None, "invalid", "Catalog entry has no project path."
    root = Path(root_text).expanduser()
    if not root.exists():
        return None, "unavailable", "The project directory is unavailable."
    if not root.is_dir():
        return None, "invalid", "The project path is not a directory."
    manifest_path = root / PROJECT_MANIFEST_FILENAME
    if not manifest_path.exists():
        return None, "invalid", "The managed project manifest is missing."
    try:
        manifest = _validate_manifest(
            _read_json(manifest_path),
            expected_project_id=_text(entry.get("id")),
        )
    except ProjectCatalogError as exc:
        return None, "invalid", exc.detail
    if _text(entry.get("manifest_fingerprint")) != manifest.get("manifest_fingerprint"):
        return manifest, "invalid", "The catalog and project manifest fingerprints disagree."
    return manifest, "available", None


def list_project_summaries(
    *,
    data_root: str | Path,
    current_project_root: str | Path,
    current_flow_summary: Mapping[str, Any],
) -> dict[str, Any]:
    catalog_path = project_catalog_path(data_root)
    catalog = load_project_catalog(catalog_path)
    legacy_entry, legacy_manifest = _legacy_entry(
        current_project_root=current_project_root,
        current_flow_summary=current_flow_summary,
    )
    current_project_id = legacy_entry["id"]
    selected_project_id = _text(catalog.get("last_selected_project_id")) or current_project_id
    projects = [
        _public_project(
            entry=legacy_entry,
            manifest=legacy_manifest,
            availability_state="available",
            current_project_id=current_project_id,
            selected_project_id=selected_project_id,
            compatibility_state=_text(_mapping(current_flow_summary.get("compatibility")).get("state")) or "current",
        )
    ]
    for entry in _list(catalog.get("projects")):
        manifest, availability, error = _inspect_catalog_entry(_mapping(entry))
        compatibility = "current"
        if manifest is not None:
            compatibility = _text(_mapping(_mapping(manifest.get("flow_snapshot")).get("compatibility")).get("state")) or "current"
        projects.append(
            _public_project(
                entry=_mapping(entry),
                manifest=manifest,
                availability_state=availability,
                current_project_id=current_project_id,
                selected_project_id=selected_project_id,
                compatibility_state=compatibility,
                error=error,
            )
        )
    projects.sort(
        key=lambda item: str(item.get("latest_meaningful_activity") or ""),
        reverse=True,
    )
    projects.sort(key=lambda item: item.get("archive_state") == "archived")
    projects.sort(key=lambda item: not bool(item.get("current")))
    return {
        "schema_version": PROJECT_CATALOG_SCHEMA_VERSION,
        "catalog_fingerprint": catalog["catalog_fingerprint"],
        "current_project_id": current_project_id,
        "last_selected_project_id": selected_project_id,
        "projects": projects,
        "trash_count": len(_list(catalog.get("trash"))),
        "storage": {
            "managed_projects_available": True,
            "activation_contract": "managed_runtime",
            "technical_details": {
                "data_root": str(Path(data_root).expanduser().resolve()),
                "catalog_path": str(catalog_path),
            },
        },
    }


def create_managed_project(
    *,
    data_root: str | Path,
    project_name: str,
    source_path: str | Path,
    book_title: str | None = None,
    author: str | None = None,
    source_language: str,
    output_language: str,
    generation_method: str,
    preset: str = "standard",
    template_id: str | None = None,
    expected_catalog_fingerprint: str | None = None,
    reserved_names: Iterable[str] = (),
    at_utc: str | None = None,
) -> dict[str, Any]:
    name = _safe_name(project_name, field="project_name")
    source_language_value = _safe_name(source_language, field="source_language", maximum=80)
    output_language_value = _safe_name(output_language, field="output_language", maximum=80)
    generation_method_value = _safe_name(generation_method, field="generation_method", maximum=80)
    preset_value = _safe_name(preset, field="preset", maximum=80)
    template_id_value = (
        _safe_name(template_id, field="template_id", maximum=80)
        if _text(template_id)
        else None
    )
    if template_id_value is not None and not re.fullmatch(
        r"(?:builtin_[a-z0-9_]{1,56}|template_[0-9a-f]{20})",
        template_id_value,
    ):
        raise ProjectCatalogError(
            status_code=422,
            code="template_id_invalid",
            detail="Template ID is invalid.",
        )
    if generation_method_value not in SUPPORTED_GENERATION_METHODS:
        raise ProjectCatalogError(
            status_code=422,
            code="generation_method_unsupported",
            detail="Generation method is unsupported.",
        )
    if preset_value not in SUPPORTED_PROJECT_PRESETS:
        raise ProjectCatalogError(
            status_code=422,
            code="project_preset_unsupported",
            detail="Project preset is unsupported.",
        )
    source_info = _validate_source(
        source_path,
        generation_method=generation_method_value,
    )
    book_title_value = (
        _safe_name(book_title, field="book_title", maximum=240)
        if _text(book_title)
        else Path(str(source_info["basename"])).stem
    )
    author_value = (
        _safe_name(author, field="author", maximum=240)
        if _text(author)
        else None
    )
    data_root_path = Path(data_root).expanduser().resolve()
    catalog_path = project_catalog_path(data_root_path)
    projects_root = managed_projects_root(data_root_path)
    at = at_utc or utc_timestamp()

    with _catalog_lock(data_root_path):
        catalog = load_project_catalog(catalog_path)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        existing_names = {
            str(item.get("name", "")).casefold()
            for item in _list(catalog.get("projects"))
        }
        existing_names.update(str(item).strip().casefold() for item in reserved_names)
        if name.casefold() in existing_names:
            raise ProjectCatalogError(
                status_code=409,
                code="project_name_conflict",
                detail="A project with this name already exists.",
            )
        project_id = _new_project_id()
        directory_name = f"{_slug(name)}--{project_id.removeprefix('project_')[:8]}"
        final_root = projects_root / directory_name
        staging = projects_root / f".{directory_name}.pending-{uuid.uuid4().hex[:8]}"
        if final_root.exists():
            raise ProjectCatalogError(
                status_code=409,
                code="project_destination_conflict",
                detail="The managed project destination already exists.",
            )
        projects_root.mkdir(parents=True, exist_ok=True)
        published = False
        try:
            manifest = _write_new_project_contents(
                staging=staging,
                final_root=final_root,
                source_info=source_info,
                project_id=project_id,
                project_name=name,
                book_title=book_title_value,
                author=author_value,
                source_language=source_language_value,
                output_language=output_language_value,
                generation_method=generation_method_value,
                preset=preset_value,
                template_id=template_id_value,
                at_utc=at,
            )
            os.replace(staging, final_root)
            published = True
            _validate_manifest(
                _read_json(final_root / PROJECT_MANIFEST_FILENAME),
                expected_project_id=project_id,
            )
            entry = _catalog_entry_from_manifest(manifest, final_root)
            updated = _catalog_for_write(catalog, at_utc=at)
            updated["projects"].append(entry)
            updated["last_selected_project_id"] = project_id
            _write_json_atomic(updated, catalog_path)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if published and final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            raise

    public = _public_project(
        entry=entry,
        manifest=manifest,
        availability_state="available",
        current_project_id=None,
        selected_project_id=project_id,
    )
    return {
        "project": public,
        "catalog_fingerprint": _catalog_fingerprint(updated),
        "activation": {
            "state": "available",
            "native_destination": public.get("current_recommended_stage") or "script",
            "project_id": project_id,
            "message": "The project was created safely and is ready for managed runtime activation.",
        },
    }


def _catalog_project_entry(catalog: Mapping[str, Any], project_id: str) -> tuple[int, dict[str, Any]]:
    for index, entry_value in enumerate(_list(catalog.get("projects"))):
        entry = dict(_mapping(entry_value))
        if entry.get("id") == project_id:
            return index, entry
    raise ProjectCatalogError(
        status_code=404,
        code="project_not_found",
        detail="The requested managed project was not found.",
        context={"project_id": project_id},
    )


def select_project(
    *,
    data_root: str | Path,
    project_id: str,
    current_project_id: str,
    legacy_project_ids: Iterable[str] = (),
    expected_catalog_fingerprint: str | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    identifier = _safe_name(project_id, field="project_id", maximum=80)
    data_root_path = Path(data_root).expanduser().resolve()
    catalog_path = project_catalog_path(data_root_path)
    at = at_utc or utc_timestamp()
    legacy = set(legacy_project_ids) | {current_project_id}
    with _catalog_lock(data_root_path):
        catalog = load_project_catalog(catalog_path)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        entry = None
        if identifier not in legacy:
            index, entry = _catalog_project_entry(catalog, identifier)
            manifest, availability, error = _inspect_catalog_entry(entry)
            if availability != "available" or manifest is None:
                raise ProjectCatalogError(
                    status_code=409,
                    code=f"project_{availability}",
                    detail=error or "The project is not available.",
                )
            if entry.get("archive_state") == "archived":
                raise ProjectCatalogError(
                    status_code=409,
                    code="project_archived",
                    detail="Unarchive the project before opening it.",
                )
            entry["last_opened_at_utc"] = at
            entry["updated_at_utc"] = at
            catalog["projects"][index] = entry
        catalog["last_selected_project_id"] = identifier
        updated = _catalog_for_write(catalog, at_utc=at)
        _write_json_atomic(updated, catalog_path)
    activation_state = "current" if identifier == current_project_id else "available"
    return {
        "project_id": identifier,
        "selected": True,
        "activation_state": activation_state,
        "native_destination": None,
        "safe_action": (
            {
                "id": "activate_selected_project",
                "label": "Activate selected project",
                "native_destination": "projects",
                "target_id": identifier,
            }
            if activation_state == "available"
            else None
        ),
        "catalog_fingerprint": _catalog_fingerprint(updated),
    }


def _assert_no_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ProjectCatalogError(
                status_code=409,
                code="project_duplicate_symlink_unsupported",
                detail="Project duplication refuses symbolic links.",
                context={"relative_path": path.relative_to(root).as_posix()},
            )


def _copy_allowed_project_artifacts(source_root: Path, staging: Path) -> list[str]:
    copied: list[str] = []
    for name in sorted(DUPLICATE_FILE_ALLOWLIST):
        source = source_root / name
        if not source.exists():
            continue
        if source.is_symlink() or not source.is_file():
            raise ProjectCatalogError(
                status_code=409,
                code="project_duplicate_artifact_invalid",
                detail=f"Project artifact {name!r} is not a safe file.",
            )
        shutil.copy2(source, staging / name)
        copied.append(name)
    for name in sorted(DUPLICATE_DIRECTORY_ALLOWLIST):
        source = source_root / name
        if not source.exists():
            continue
        if source.is_symlink() or not source.is_dir():
            raise ProjectCatalogError(
                status_code=409,
                code="project_duplicate_artifact_invalid",
                detail=f"Project artifact {name!r} is not a safe directory.",
            )
        _assert_no_symlinks(source)
        destination = staging / name
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(*DUPLICATE_TRANSIENT_NAMES),
        )
        copied.append(name + "/")
    return copied


def _selected_source_from_state(source_root: Path) -> Path | None:
    state_path = source_root / "state.json"
    if not state_path.is_file():
        return None
    try:
        state = _mapping(_read_json(state_path))
    except ProjectCatalogError:
        return None
    value = _text(state.get("input_file_path"))
    if value is None:
        return None
    source = Path(value).expanduser()
    if not source.is_absolute():
        source = source_root / source
    try:
        source = source.resolve()
    except OSError:
        return None
    return source if source.is_file() and not source.is_symlink() else None


def duplicate_project(
    *,
    data_root: str | Path,
    project_id: str,
    new_name: str,
    expected_catalog_fingerprint: str | None,
    source_project_root: str | Path | None = None,
    source_flow_summary: Mapping[str, Any] | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    identifier = _safe_name(project_id, field="project_id", maximum=80)
    name = _safe_name(new_name, field="project_name")
    data_root_path = Path(data_root).expanduser().resolve()
    catalog_path = project_catalog_path(data_root_path)
    projects_root = managed_projects_root(data_root_path)
    at = at_utc or utc_timestamp()
    with _catalog_lock(data_root_path):
        catalog = load_project_catalog(catalog_path)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        if any(str(item.get("name", "")).casefold() == name.casefold() for item in _list(catalog.get("projects"))):
            raise ProjectCatalogError(
                status_code=409,
                code="project_name_conflict",
                detail="A project with this name already exists.",
            )
        source_entry = None
        source_manifest = None
        if source_project_root is None:
            _, source_entry = _catalog_project_entry(catalog, identifier)
            source_manifest, availability, error = _inspect_catalog_entry(source_entry)
            if availability != "available" or source_manifest is None:
                raise ProjectCatalogError(
                    status_code=409,
                    code=f"project_{availability}",
                    detail=error or "The source project is unavailable.",
                )
            source_root = Path(str(source_entry["root_path"])).resolve()
        else:
            source_root = Path(source_project_root).expanduser().resolve()
            if not source_root.is_dir():
                raise ProjectCatalogError(
                    status_code=409,
                    code="legacy_project_unavailable",
                    detail="The legacy project root is unavailable.",
                )
            flow = dict(source_flow_summary or {})
            project = _mapping(flow.get("project"))
            source = _mapping(flow.get("source"))
            source_manifest = {
                "schema_version": PROJECT_MANIFEST_SCHEMA_VERSION,
                "project_id": identifier,
                "name": _text(project.get("name")) or "Legacy Alexandria project",
                "created_at_utc": None,
                "updated_at_utc": project.get("latest_meaningful_activity"),
                "archive_state": "active",
                "source": {
                    "title": source.get("title"),
                    "original_filename": source.get("filename"),
                    "type": source.get("type"),
                    "source_language": source.get("source_language"),
                    "output_language": source.get("output_language"),
                    "fingerprint": source.get("fingerprint"),
                },
                "generation": {"method": None, "preset": None},
                "flow_snapshot": flow,
            }
        new_id = _new_project_id()
        directory_name = f"{_slug(name)}--{new_id.removeprefix('project_')[:8]}"
        final_root = projects_root / directory_name
        staging = projects_root / f".{directory_name}.pending-{uuid.uuid4().hex[:8]}"
        projects_root.mkdir(parents=True, exist_ok=True)
        published = False
        try:
            staging.mkdir()
            copied = _copy_allowed_project_artifacts(source_root, staging)
            selected_source = _selected_source_from_state(source_root)
            copied_source_relative = None
            if selected_source is not None:
                sources_dir = staging / "sources"
                sources_dir.mkdir(exist_ok=True)
                destination = sources_dir / _safe_basename(selected_source.name)
                if destination.exists():
                    existing_hash = _sha256_file(destination)
                    source_hash = _sha256_file(selected_source)
                    if existing_hash != source_hash:
                        destination = sources_dir / (
                            f"selected-{source_hash[:12]}-{_safe_basename(selected_source.name)}"
                        )
                if not destination.exists():
                    shutil.copy2(selected_source, destination)
                copied_source_relative = destination.relative_to(staging).as_posix()
            old_source = dict(_mapping(source_manifest.get("source")))
            if copied_source_relative is not None:
                old_source["prepared_relative_path"] = copied_source_relative
                old_source["original_relative_path"] = copied_source_relative
                old_source["original_filename"] = Path(copied_source_relative).name
            old_source.setdefault("source_language", None)
            old_source.setdefault("output_language", None)
            generation = dict(_mapping(source_manifest.get("generation")))
            manifest = {
                "schema_version": PROJECT_MANIFEST_SCHEMA_VERSION,
                "project_id": new_id,
                "name": name,
                "created_at_utc": at,
                "updated_at_utc": at,
                "archive_state": "active",
                "source": old_source,
                "generation": generation,
                "runtime": {
                    "storage_kind": "managed",
                    "activation_contract": "managed_runtime",
                },
                "flow_snapshot": dict(_mapping(source_manifest.get("flow_snapshot"))),
                "duplicate": {
                    "source_project_id": identifier,
                    "created_at_utc": at,
                    "copied_artifacts": copied,
                    "active_operations_copied": False,
                },
            }
            flow_project = dict(_mapping(_mapping(manifest.get("flow_snapshot")).get("project")))
            if flow_project:
                flow_project.update(
                    {
                        "id": new_id,
                        "name": name,
                        "latest_meaningful_activity": at,
                        "archive_state": "active",
                    }
                )
                manifest["flow_snapshot"] = {
                    **dict(_mapping(manifest.get("flow_snapshot"))),
                    "project": flow_project,
                    "running_operation": None,
                    "resumable_operation": None,
                }
            manifest["manifest_fingerprint"] = _manifest_fingerprint(manifest)
            state_path = staging / "state.json"
            state = dict(_mapping(_read_json(state_path))) if state_path.exists() else {}
            state.update(
                {
                    "schema_version": 1,
                    "project_id": new_id,
                    "project_name": name,
                    "source_relative_path": copied_source_relative,
                    "input_file_path": (
                        str(final_root / copied_source_relative)
                        if copied_source_relative
                        else None
                    ),
                }
            )
            _write_json_atomic(state, state_path)
            for transient in DUPLICATE_TRANSIENT_NAMES:
                transient_path = staging / transient
                if transient_path.is_file():
                    transient_path.unlink()
                elif transient_path.is_dir():
                    shutil.rmtree(transient_path)
            _write_json_atomic(manifest, staging / PROJECT_MANIFEST_FILENAME)
            _validate_manifest(
                _read_json(staging / PROJECT_MANIFEST_FILENAME),
                expected_project_id=new_id,
            )
            os.replace(staging, final_root)
            published = True
            entry = _catalog_entry_from_manifest(manifest, final_root)
            updated = _catalog_for_write(catalog, at_utc=at)
            updated["projects"].append(entry)
            updated["last_selected_project_id"] = new_id
            _write_json_atomic(updated, catalog_path)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if published and final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            raise
    return {
        "project": _public_project(
            entry=entry,
            manifest=manifest,
            availability_state="available",
            current_project_id=None,
            selected_project_id=new_id,
        ),
        "catalog_fingerprint": _catalog_fingerprint(updated),
        "copied_artifacts": copied,
        "active_operations_copied": False,
        "activation_state": "available",
    }


def _update_manifest_archive(
    *,
    project_root: Path,
    project_id: str,
    archive_state: str,
    at_utc: str,
) -> tuple[dict[str, Any], bytes]:
    path = project_root / PROJECT_MANIFEST_FILENAME
    previous = path.read_bytes()
    manifest = _validate_manifest(_read_json(path), expected_project_id=project_id)
    manifest["archive_state"] = archive_state
    manifest["updated_at_utc"] = at_utc
    flow = dict(_mapping(manifest.get("flow_snapshot")))
    flow_project = dict(_mapping(flow.get("project")))
    if flow_project:
        flow_project["archive_state"] = archive_state
        flow_project["latest_meaningful_activity"] = at_utc
        flow["project"] = flow_project
        manifest["flow_snapshot"] = flow
    manifest["manifest_fingerprint"] = _manifest_fingerprint(manifest)
    _write_json_atomic(manifest, path)
    return manifest, previous


def set_project_archived(
    *,
    data_root: str | Path,
    project_id: str,
    archived: bool,
    expected_catalog_fingerprint: str | None,
    expected_project_fingerprint: str | None,
    current_project_id: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    identifier = _safe_name(project_id, field="project_id", maximum=80)
    if identifier == current_project_id and archived:
        raise ProjectCatalogError(
            status_code=409,
            code="current_project_archive_blocked",
            detail="Select another project before archiving the active project.",
        )
    data_root_path = Path(data_root).expanduser().resolve()
    catalog_path = project_catalog_path(data_root_path)
    at = at_utc or utc_timestamp()
    with _catalog_lock(data_root_path):
        catalog = load_project_catalog(catalog_path)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        index, entry = _catalog_project_entry(catalog, identifier)
        manifest, availability, error = _inspect_catalog_entry(entry)
        if availability != "available" or manifest is None:
            raise ProjectCatalogError(
                status_code=409,
                code=f"project_{availability}",
                detail=error or "The project is unavailable.",
            )
        current_manifest_fingerprint = _text(manifest.get("manifest_fingerprint"))
        if expected_project_fingerprint != current_manifest_fingerprint:
            raise ProjectCatalogError(
                status_code=409,
                code="stale_project_manifest",
                detail="The project changed after this view was loaded.",
                context={"current_project_fingerprint": current_manifest_fingerprint},
            )
        root = Path(str(entry["root_path"])).resolve()
        archive_state = "archived" if archived else "active"
        updated_manifest, previous_manifest = _update_manifest_archive(
            project_root=root,
            project_id=identifier,
            archive_state=archive_state,
            at_utc=at,
        )
        try:
            entry.update(
                {
                    "archive_state": archive_state,
                    "updated_at_utc": at,
                    "manifest_fingerprint": updated_manifest["manifest_fingerprint"],
                }
            )
            catalog["projects"][index] = entry
            if archived and catalog.get("last_selected_project_id") == identifier:
                catalog["last_selected_project_id"] = current_project_id
            updated = _catalog_for_write(catalog, at_utc=at)
            _write_json_atomic(updated, catalog_path)
        except Exception:
            _write_bytes_atomic(previous_manifest, root / PROJECT_MANIFEST_FILENAME)
            raise
    return {
        "project_id": identifier,
        "archive_state": archive_state,
        "project_fingerprint": updated_manifest["manifest_fingerprint"],
        "catalog_fingerprint": _catalog_fingerprint(updated),
    }


def project_dependency_report(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ProjectCatalogError(
            status_code=409,
            code="project_unavailable",
            detail="The project directory is unavailable.",
        )
    file_count = 0
    total_bytes = 0
    categories = {
        "source_files": 0,
        "script_artifacts": 0,
        "audio_artifacts": 0,
        "voice_assets": 0,
        "training_assets": 0,
        "export_outputs": 0,
        "history_records": 0,
    }
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ProjectCatalogError(
                status_code=409,
                code="project_dependency_symlink_unsupported",
                detail="Dependency inspection refuses symbolic links.",
            )
        if not path.is_file():
            continue
        file_count += 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
        relative = path.relative_to(root).as_posix()
        first = relative.split("/", 1)[0]
        if first in {"sources", "uploads", "imports"}:
            categories["source_files"] += 1
        if path.name in {"annotated_script.json", "annotated_script.meta.json", "chunks.json", "character_roster.json", "character_roster.draft.json"}:
            categories["script_artifacts"] += 1
        if first == "voicelines" or path.suffix.casefold() in {".wav", ".mp3", ".m4a", ".flac"}:
            categories["audio_artifacts"] += 1
        if first in {"designed_voices", "clone_voices", "persona_refs", "voice_training_projects"}:
            categories["voice_assets"] += 1
        if first in {"lora_models", "lora_datasets", "dataset_builder", "preparer_output"}:
            categories["training_assets"] += 1
        if path.name in {"cloned_audiobook.mp3", "audiobook.mp3", "audiobook.m4b", "audacity_export.zip"}:
            categories["export_outputs"] += 1
        if "history" in first or first == "external_workflows":
            categories["history_records"] += 1
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "categories": categories,
        "blocking_dependencies_present": any(categories.values()),
    }


def project_delete_impact(
    *,
    data_root: str | Path,
    project_id: str,
    current_project_id: str,
) -> dict[str, Any]:
    identifier = _safe_name(project_id, field="project_id", maximum=80)
    if identifier == current_project_id:
        return {
            "project_id": identifier,
            "deletable": False,
            "blocking_code": "current_project_delete_blocked",
            "blocking_reason": "The active project cannot be deleted.",
            "dependencies": None,
            "project_fingerprint": None,
            "catalog_fingerprint": load_project_catalog(
                project_catalog_path(data_root)
            )["catalog_fingerprint"],
        }
    catalog = load_project_catalog(project_catalog_path(data_root))
    _, entry = _catalog_project_entry(catalog, identifier)
    manifest, availability, error = _inspect_catalog_entry(entry)
    if availability != "available" or manifest is None:
        return {
            "project_id": identifier,
            "deletable": False,
            "blocking_code": f"project_{availability}",
            "blocking_reason": error or "The project is unavailable.",
            "dependencies": None,
            "project_fingerprint": None,
            "catalog_fingerprint": catalog["catalog_fingerprint"],
        }
    dependencies = project_dependency_report(entry["root_path"])
    archive_required = entry.get("archive_state") != "archived"
    return {
        "project_id": identifier,
        "deletable": not archive_required,
        "blocking_code": "project_delete_requires_archive" if archive_required else None,
        "blocking_reason": (
            "Archive the project before deleting it."
            if archive_required
            else None
        ),
        "dependencies": dependencies,
        "project_fingerprint": manifest["manifest_fingerprint"],
        "catalog_fingerprint": catalog["catalog_fingerprint"],
        "recoverable_delete": True,
    }


def delete_project_to_trash(
    *,
    data_root: str | Path,
    project_id: str,
    confirm_project_id: str,
    expected_catalog_fingerprint: str | None,
    expected_project_fingerprint: str | None,
    current_project_id: str,
    confirm_dependencies: bool,
    at_utc: str | None = None,
) -> dict[str, Any]:
    identifier = _safe_name(project_id, field="project_id", maximum=80)
    if confirm_project_id != identifier:
        raise ProjectCatalogError(
            status_code=422,
            code="project_delete_confirmation_mismatch",
            detail="Delete confirmation must match the exact project ID.",
        )
    if identifier == current_project_id:
        raise ProjectCatalogError(
            status_code=409,
            code="current_project_delete_blocked",
            detail="The active project cannot be deleted.",
        )
    data_root_path = Path(data_root).expanduser().resolve()
    catalog_path = project_catalog_path(data_root_path)
    trash_root = project_trash_root(data_root_path)
    projects_root = managed_projects_root(data_root_path).resolve()
    at = at_utc or utc_timestamp()
    with _catalog_lock(data_root_path):
        catalog = load_project_catalog(catalog_path)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        index, entry = _catalog_project_entry(catalog, identifier)
        manifest, availability, error = _inspect_catalog_entry(entry)
        if availability != "available" or manifest is None:
            raise ProjectCatalogError(
                status_code=409,
                code=f"project_{availability}",
                detail=error or "The project is unavailable.",
            )
        if entry.get("archive_state") != "archived":
            raise ProjectCatalogError(
                status_code=409,
                code="project_delete_requires_archive",
                detail="Archive the project before deleting it.",
            )
        current_manifest_fingerprint = _text(manifest.get("manifest_fingerprint"))
        if expected_project_fingerprint != current_manifest_fingerprint:
            raise ProjectCatalogError(
                status_code=409,
                code="stale_project_manifest",
                detail="The project changed after this view was loaded.",
                context={"current_project_fingerprint": current_manifest_fingerprint},
            )
        root = Path(str(entry["root_path"])).expanduser().resolve()
        try:
            root.relative_to(projects_root)
        except ValueError as exc:
            raise ProjectCatalogError(
                status_code=409,
                code="project_delete_outside_managed_root",
                detail="Only self-contained managed projects can be deleted through Project Home.",
            ) from exc
        dependencies = project_dependency_report(root)
        if dependencies["blocking_dependencies_present"] and not confirm_dependencies:
            raise ProjectCatalogError(
                status_code=409,
                code="project_delete_dependencies_unconfirmed",
                detail="Confirm the reported project artifacts before deletion.",
                context={"dependencies": dependencies},
            )
        trash_root.mkdir(parents=True, exist_ok=True)
        timestamp = at.replace(":", "").replace("-", "").replace(".", "")
        trash_destination = trash_root / f"{identifier}--{timestamp}"
        if trash_destination.exists():
            raise ProjectCatalogError(
                status_code=409,
                code="project_trash_destination_conflict",
                detail="A recoverable Trash destination already exists for this operation.",
            )
        moved = False
        try:
            os.replace(root, trash_destination)
            moved = True
            removed = catalog["projects"].pop(index)
            catalog["trash"].append(
                {
                    "id": identifier,
                    "name": removed.get("name"),
                    "trashed_at_utc": at,
                    "trash_path": str(trash_destination),
                    "project_fingerprint": current_manifest_fingerprint,
                    "dependencies": dependencies,
                }
            )
            if catalog.get("last_selected_project_id") == identifier:
                catalog["last_selected_project_id"] = current_project_id
            updated = _catalog_for_write(catalog, at_utc=at)
            _write_json_atomic(updated, catalog_path)
        except Exception:
            if moved and trash_destination.exists() and not root.exists():
                os.replace(trash_destination, root)
            raise
    return {
        "project_id": identifier,
        "deleted": True,
        "recoverable": True,
        "dependencies": dependencies,
        "catalog_fingerprint": _catalog_fingerprint(updated),
        "technical_details": {
            "trash_path": str(trash_destination),
        },
    }
