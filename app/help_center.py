from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
MAX_TOPIC_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_TOPIC_COUNT = 100
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RAW_HTML_PATTERN = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
UNSAFE_CONTROL_PATTERN = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")
ALLOWED_DESTINATIONS = frozenset(
    {
        "projects",
        "script",
        "cast",
        "produce",
        "export",
        "library",
        "voices",
        "templates",
        "settings",
        "more:advanced-character-operations",
        "more:voice-designer",
        "more:audio-preparer",
        "more:dataset-builder",
        "more:voice-training",
        "more:maintenance",
        "more:model-cache",
        "more:help-center",
    }
)


class HelpCenterError(RuntimeError):
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
            "context": dict(self.context),
        }


def _safe_text(value: Any, *, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > maximum
        or UNSAFE_CONTROL_PATTERN.search(text)
    ):
        raise HelpCenterError(
            status_code=409,
            code="help_topic_metadata_invalid",
            detail=f"Help topic {field} is missing or invalid.",
            context={"field": field},
        )
    return text


def _safe_slug(value: Any, *, field: str = "slug") -> str:
    slug = str(value or "").strip()
    if not SLUG_PATTERN.fullmatch(slug):
        raise HelpCenterError(
            status_code=422,
            code="help_topic_slug_invalid",
            detail="The requested Help Center topic is invalid.",
            context={"field": field, "value": slug},
        )
    return slug


def _safe_context_id(value: Any) -> str:
    context_id = str(value or "").strip()
    if not SLUG_PATTERN.fullmatch(context_id):
        raise HelpCenterError(
            status_code=409,
            code="help_topic_context_invalid",
            detail="Help topic context IDs must be stable lowercase identifiers.",
            context={"context_id": context_id},
        )
    return context_id


def _parse_frontmatter_value(raw: str) -> Any:
    value = raw.strip()
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise HelpCenterError(
                status_code=409,
                code="help_topic_frontmatter_invalid",
                detail="Help topic front matter contains invalid JSON.",
            ) from exc
    if value.isdigit():
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_frontmatter(text: str, *, filename: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise HelpCenterError(
            status_code=409,
            code="help_topic_frontmatter_missing",
            detail=f"{filename} is missing Help Center front matter.",
        )
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_frontmatter_invalid",
            detail=f"{filename} has unterminated Help Center front matter.",
        )
    metadata: dict[str, Any] = {}
    for line in text[4:marker].splitlines():
        if not line.strip():
            continue
        key, separator, raw_value = line.partition(":")
        key = key.strip()
        if not separator or not key or key in metadata:
            raise HelpCenterError(
                status_code=409,
                code="help_topic_frontmatter_invalid",
                detail=f"{filename} has invalid or duplicate front matter fields.",
                context={"line": line},
            )
        metadata[key] = _parse_frontmatter_value(raw_value)
    return metadata, text[marker + 5 :].strip()


def _safe_string_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HelpCenterError(
            status_code=409,
            code="help_topic_metadata_invalid",
            detail=f"Help topic {field} must be a list of strings.",
            context={"field": field},
        )
    result = [item.strip() for item in value]
    if any(not item for item in result) or len(result) != len(set(result)):
        raise HelpCenterError(
            status_code=409,
            code="help_topic_metadata_invalid",
            detail=f"Help topic {field} contains empty or duplicate values.",
            context={"field": field},
        )
    return result


def _safe_regular_file(
    path: Path,
    *,
    root: Path,
    code: str,
    detail: str,
) -> Path:
    if path.is_symlink() or not path.is_file():
        raise HelpCenterError(
            status_code=409,
            code=code,
            detail=detail,
            context={"path": path.name},
        )
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HelpCenterError(
            status_code=409,
            code=code,
            detail=detail,
            context={"path": path.name},
        ) from exc
    return resolved


def _read_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_FILENAME
    _safe_regular_file(
        path,
        root=root,
        code="help_manifest_unsafe",
        detail="The Help Center manifest must be a regular bundled file.",
    )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_unreadable",
            detail="The bundled Help Center manifest could not be read.",
        ) from exc
    if len(content) > MAX_MANIFEST_BYTES:
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_too_large",
            detail="The bundled Help Center manifest exceeds the supported limit.",
        )
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_invalid",
            detail="The bundled Help Center manifest must be valid UTF-8 JSON.",
        ) from exc
    if not isinstance(value, dict):
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_invalid",
            detail="The bundled Help Center manifest must be a JSON object.",
        )
    if set(value) != {"schema_version", "bundle_version", "topics"}:
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_invalid",
            detail="The bundled Help Center manifest has unsupported fields.",
        )
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_schema_unsupported",
            detail="The bundled Help Center manifest schema is unsupported.",
        )
    bundle_version = _safe_text(
        value.get("bundle_version"),
        field="bundle_version",
        maximum=40,
    )
    entries = value.get("topics")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_TOPIC_COUNT:
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_topics_invalid",
            detail="The Help Center manifest must list a supported number of topics.",
        )
    parsed_entries: list[dict[str, str]] = []
    slugs: set[str] = set()
    filenames: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise HelpCenterError(
                status_code=500,
                code="help_manifest_topic_invalid",
                detail="Every Help Center manifest topic must be an object.",
                context={"index": index},
            )
        if set(entry) != {"slug", "filename", "content_sha256"}:
            raise HelpCenterError(
                status_code=500,
                code="help_manifest_topic_invalid",
                detail="Help Center manifest topics have unsupported fields.",
                context={"index": index},
            )
        slug = _safe_slug(entry.get("slug"), field="manifest.slug")
        filename = str(entry.get("filename") or "").strip()
        content_sha256 = str(entry.get("content_sha256") or "").strip()
        if filename != f"{slug}.md":
            raise HelpCenterError(
                status_code=500,
                code="help_manifest_filename_invalid",
                detail="Help Center manifest filenames must match topic slugs.",
                context={"slug": slug, "filename": filename},
            )
        if not SHA256_PATTERN.fullmatch(content_sha256):
            raise HelpCenterError(
                status_code=500,
                code="help_manifest_hash_invalid",
                detail="Help Center manifest topic hashes must be lowercase SHA-256 values.",
                context={"slug": slug},
            )
        if slug in slugs or filename in filenames:
            raise HelpCenterError(
                status_code=500,
                code="help_manifest_topic_duplicate",
                detail="Help Center manifest topics must be unique.",
                context={"slug": slug},
            )
        slugs.add(slug)
        filenames.add(filename)
        parsed_entries.append(
            {
                "slug": slug,
                "filename": filename,
                "content_sha256": content_sha256,
            }
        )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "bundle_version": bundle_version,
        "topics": parsed_entries,
        "manifest_sha256": hashlib.sha256(content).hexdigest(),
    }


def _load_topic(path: Path, *, root: Path) -> dict[str, Any]:
    _safe_regular_file(
        path,
        root=root,
        code="help_topic_file_unsafe",
        detail="Help Center topics must be regular bundled files.",
    )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_unreadable",
            detail=f"{path.name} could not be read.",
        ) from exc
    if size > MAX_TOPIC_BYTES:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_too_large",
            detail=f"{path.name} exceeds the Help Center topic limit.",
            context={"size_bytes": size, "maximum_bytes": MAX_TOPIC_BYTES},
        )
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_unreadable",
            detail=f"{path.name} must be valid UTF-8 text.",
        ) from exc
    metadata, body = _parse_frontmatter(source, filename=path.name)
    expected_metadata = {
        "schema_version",
        "slug",
        "title",
        "summary",
        "version",
        "context_ids",
        "destinations",
        "related",
    }
    if set(metadata) != expected_metadata:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_metadata_invalid",
            detail=f"{path.name} has missing or unsupported front matter fields.",
            context={
                "missing": sorted(expected_metadata - set(metadata)),
                "unsupported": sorted(set(metadata) - expected_metadata),
            },
        )
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_schema_unsupported",
            detail=f"{path.name} uses an unsupported Help Center schema.",
        )
    slug = _safe_slug(metadata.get("slug"))
    if slug != path.stem:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_slug_mismatch",
            detail=f"{path.name} does not match its declared topic slug.",
        )
    title = _safe_text(metadata.get("title"), field="title", maximum=120)
    summary = _safe_text(metadata.get("summary"), field="summary", maximum=280)
    version = _safe_text(metadata.get("version"), field="version", maximum=40)
    context_ids = [
        _safe_context_id(item)
        for item in _safe_string_list(
            metadata.get("context_ids"), field="context_ids"
        )
    ]
    if not context_ids:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_context_missing",
            detail=f"{path.name} must declare at least one stable context ID.",
        )
    destinations = _safe_string_list(
        metadata.get("destinations"), field="destinations"
    )
    invalid_destinations = [
        item for item in destinations if item not in ALLOWED_DESTINATIONS
    ]
    if invalid_destinations:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_destination_invalid",
            detail=f"{path.name} contains an unsupported destination reference.",
            context={"destinations": invalid_destinations},
        )
    related = [
        _safe_slug(item, field="related")
        for item in _safe_string_list(metadata.get("related"), field="related")
    ]
    if not body:
        raise HelpCenterError(
            status_code=409,
            code="help_topic_body_missing",
            detail=f"{path.name} has no Help Center content.",
        )
    if UNSAFE_CONTROL_PATTERN.search(body):
        raise HelpCenterError(
            status_code=409,
            code="help_topic_body_invalid",
            detail=f"{path.name} contains unsafe control characters.",
        )
    if RAW_HTML_PATTERN.search(body):
        raise HelpCenterError(
            status_code=409,
            code="help_topic_html_forbidden",
            detail=f"{path.name} contains raw HTML. Help topics support Markdown text only.",
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "title": title,
        "summary": summary,
        "version": version,
        "context_ids": context_ids,
        "destinations": destinations,
        "related": related,
        "markdown": body,
        "content_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "markdown_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def _load_bundle(help_dir: str | Path) -> dict[str, Any]:
    root = Path(help_dir).expanduser().resolve()
    if not root.is_dir():
        raise HelpCenterError(
            status_code=500,
            code="help_center_missing",
            detail="Bundled Help Center documentation is missing.",
        )
    manifest = _read_manifest(root)
    manifest_filenames = [entry["filename"] for entry in manifest["topics"]]
    candidates = sorted(path.name for path in root.glob("*.md"))
    if candidates != sorted(manifest_filenames):
        raise HelpCenterError(
            status_code=500,
            code="help_manifest_inventory_mismatch",
            detail="The Help Center manifest and bundled Markdown files do not match.",
            context={
                "unlisted": sorted(set(candidates) - set(manifest_filenames)),
                "missing": sorted(set(manifest_filenames) - set(candidates)),
            },
        )
    topics: list[dict[str, Any]] = []
    for entry in manifest["topics"]:
        topic = _load_topic(root / entry["filename"], root=root)
        if topic["slug"] != entry["slug"]:
            raise HelpCenterError(
                status_code=500,
                code="help_manifest_slug_mismatch",
                detail="A Help Center topic does not match its manifest slug.",
                context={"slug": entry["slug"]},
            )
        if topic["content_sha256"] != entry["content_sha256"]:
            raise HelpCenterError(
                status_code=500,
                code="help_manifest_content_mismatch",
                detail="A bundled Help Center topic changed without a manifest update.",
                context={"slug": topic["slug"]},
            )
        topics.append(topic)
    slugs = {topic["slug"] for topic in topics}
    for topic in topics:
        missing = [slug for slug in topic["related"] if slug not in slugs]
        if missing:
            raise HelpCenterError(
                status_code=500,
                code="help_topic_related_missing",
                detail=f"{topic['slug']} references missing related topics.",
                context={"missing": missing},
            )
    context_index: dict[str, str] = {}
    for topic in topics:
        for context_id in topic["context_ids"]:
            previous = context_index.get(context_id)
            if previous is not None:
                raise HelpCenterError(
                    status_code=500,
                    code="help_topic_context_duplicate",
                    detail="Stable Help Center context IDs must be globally unique.",
                    context={
                        "context_id": context_id,
                        "topics": [previous, topic["slug"]],
                    },
                )
            context_index[context_id] = topic["slug"]
    return {
        "root": root,
        "manifest": manifest,
        "topics": topics,
        "context_index": context_index,
    }


def _topic_summary(topic: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(topic[key])
        for key in (
            "slug",
            "title",
            "summary",
            "version",
            "context_ids",
            "destinations",
            "related",
            "content_sha256",
            "markdown_sha256",
        )
    }


def inspect_help_center(
    *, help_dir: str | Path, search: str | None = None
) -> dict[str, Any]:
    bundle = _load_bundle(help_dir)
    topics = bundle["topics"]
    normalized = " ".join(str(search or "").casefold().split())
    visible = topics
    if normalized:
        terms = normalized.split()
        visible = []
        for topic in topics:
            haystack = " ".join(
                [
                    topic["title"],
                    topic["summary"],
                    topic["markdown"],
                    *topic["context_ids"],
                    *topic["destinations"],
                ]
            ).casefold()
            if all(term in haystack for term in terms):
                visible.append(topic)
    summaries = [_topic_summary(topic) for topic in visible]
    manifest = bundle["manifest"]
    inventory_value = {
        "bundle_version": manifest["bundle_version"],
        "manifest_sha256": manifest["manifest_sha256"],
        "topics": [
            (
                topic["slug"],
                topic["content_sha256"],
                topic["context_ids"],
            )
            for topic in topics
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": manifest["bundle_version"],
        "manifest_sha256": manifest["manifest_sha256"],
        "summary": {
            "topic_count": len(topics),
            "visible_count": len(summaries),
        },
        "search": str(search or ""),
        "context_index": copy.deepcopy(bundle["context_index"]),
        "topics": summaries,
        "inventory_sha256": hashlib.sha256(
            json.dumps(
                inventory_value,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def get_help_topic(*, help_dir: str | Path, slug: str) -> dict[str, Any]:
    safe_slug = _safe_slug(slug)
    bundle = _load_bundle(help_dir)
    topic = next(
        (item for item in bundle["topics"] if item["slug"] == safe_slug),
        None,
    )
    if topic is None:
        raise HelpCenterError(
            status_code=404,
            code="help_topic_not_found",
            detail="The requested Help Center topic was not found.",
            context={"slug": safe_slug},
        )
    summaries = {
        item["slug"]: _topic_summary(item)
        for item in bundle["topics"]
    }
    result = copy.deepcopy(topic)
    result["bundle_version"] = bundle["manifest"]["bundle_version"]
    result["manifest_sha256"] = bundle["manifest"]["manifest_sha256"]
    result["related_topics"] = [
        summaries[related]
        for related in result["related"]
        if related in summaries
    ]
    return result


def get_help_topic_by_context(
    *, help_dir: str | Path, context_id: str
) -> dict[str, Any]:
    safe_context = _safe_context_id(context_id)
    bundle = _load_bundle(help_dir)
    slug = bundle["context_index"].get(safe_context)
    if slug is None:
        raise HelpCenterError(
            status_code=404,
            code="help_context_not_found",
            detail="No bundled Help Center topic matches this context.",
            context={"context_id": safe_context},
        )
    return get_help_topic(help_dir=help_dir, slug=slug)
