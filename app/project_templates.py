from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from generation_state import fingerprint_value


TEMPLATE_SCHEMA_VERSION = 1
TEMPLATE_CATALOG_FILENAME = "templates.json"
MAX_TEMPLATE_CATALOG_BYTES = 1 * 1024 * 1024
TEMPLATE_NAME_RE = re.compile(r"[^\x00-\x1f\x7f]{1,80}")
TEMPLATE_ID_RE = re.compile(r"template_[0-9a-f]{20}")
GENERATION_METHODS = {
    "local",
    "chatgpt_task_bundle",
    "import_existing_script",
}
PRESETS = {
    "standard",
    "maximum_fidelity",
    "faster_draft",
    "custom",
}
BUILT_IN_TEMPLATES = (
    {
        "id": "builtin_standard",
        "name": "Standard",
        "description": "Balanced source fidelity, review depth, and local generation time.",
        "generation_method": "local",
        "preset": "standard",
        "source_language": "English",
        "output_language": "English",
        "intent": "Balanced production",
    },
    {
        "id": "builtin_maximum_fidelity",
        "name": "Maximum fidelity",
        "description": "Prioritize source fidelity and review depth over generation speed.",
        "generation_method": "local",
        "preset": "maximum_fidelity",
        "source_language": "English",
        "output_language": "English",
        "intent": "Highest source fidelity",
    },
    {
        "id": "builtin_faster_draft",
        "name": "Faster draft",
        "description": "Create a reviewable draft with lighter generation effort.",
        "generation_method": "local",
        "preset": "faster_draft",
        "source_language": "English",
        "output_language": "English",
        "intent": "Faster first pass",
    },
    {
        "id": "builtin_custom",
        "name": "Custom",
        "description": "Start with the normal guided flow and open Advanced options only when needed.",
        "generation_method": "local",
        "preset": "custom",
        "source_language": "English",
        "output_language": "English",
        "intent": "Custom production starting point",
    },
    {
        "id": "builtin_chatgpt_bundle",
        "name": "ChatGPT task bundle",
        "description": "Prepare a portable task bundle for Script creation outside the local model runtime.",
        "generation_method": "chatgpt_task_bundle",
        "preset": "maximum_fidelity",
        "source_language": "English",
        "output_language": "English",
        "intent": "External high-fidelity Script preparation",
    },
    {
        "id": "builtin_import_script",
        "name": "Import Alexandria Script",
        "description": "Create a project from an existing reviewed Alexandria Script JSON file.",
        "generation_method": "import_existing_script",
        "preset": "standard",
        "source_language": "English",
        "output_language": "English",
        "intent": "Continue from an existing Script",
    },
)
_LOCK = threading.RLock()


class ProjectTemplateError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        context: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.context = dict(context or {})

    def as_detail(self) -> dict[str, Any]:
        value = {"code": self.code, "message": str(self)}
        if self.context:
            value["context"] = copy.deepcopy(self.context)
        return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def template_catalog_path(data_root: str | Path) -> Path:
    return Path(data_root).expanduser().resolve() / TEMPLATE_CATALOG_FILENAME


def _text(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ProjectTemplateError(
            "template_field_required",
            f"{field} must be text.",
            status_code=422,
            context={"field": field},
        )
    normalized = " ".join(value.split())
    if not normalized and not allow_empty:
        raise ProjectTemplateError(
            "template_field_required",
            f"{field} is required.",
            status_code=422,
            context={"field": field},
        )
    if len(normalized) > maximum:
        raise ProjectTemplateError(
            "template_field_too_long",
            f"{field} exceeds {maximum} characters.",
            status_code=422,
            context={"field": field, "maximum": maximum},
        )
    return normalized


def _language(value: Any, *, field: str) -> str:
    language = _text(value, field=field, maximum=80)
    if any(character in language for character in "<>\x00"):
        raise ProjectTemplateError(
            "template_language_invalid",
            f"{field} contains unsupported characters.",
            status_code=422,
            context={"field": field},
        )
    return language


def _normalize_template_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    name = _text(value.get("name"), field="name", maximum=80)
    if not TEMPLATE_NAME_RE.fullmatch(name):
        raise ProjectTemplateError(
            "template_name_invalid",
            "Template name contains unsupported control characters.",
            status_code=422,
            context={"field": "name"},
        )
    method = _text(
        value.get("generation_method"),
        field="generation_method",
        maximum=40,
    )
    if method not in GENERATION_METHODS:
        raise ProjectTemplateError(
            "template_generation_method_invalid",
            "Template generation method is unsupported.",
            status_code=422,
            context={"field": "generation_method"},
        )
    preset = _text(value.get("preset"), field="preset", maximum=40)
    if preset not in PRESETS:
        raise ProjectTemplateError(
            "template_preset_invalid",
            "Template preset is unsupported.",
            status_code=422,
            context={"field": "preset"},
        )
    if method == "import_existing_script" and preset != "standard":
        raise ProjectTemplateError(
            "template_import_preset_invalid",
            "Import templates use the Standard preset because Script generation is skipped.",
            status_code=422,
            context={"field": "preset"},
        )
    return {
        "name": name,
        "description": _text(
            value.get("description", ""),
            field="description",
            maximum=300,
            allow_empty=True,
        ),
        "generation_method": method,
        "preset": preset,
        "source_language": _language(
            value.get("source_language"),
            field="source_language",
        ),
        "output_language": _language(
            value.get("output_language"),
            field="output_language",
        ),
        "intent": _text(
            value.get("intent", name),
            field="intent",
            maximum=120,
        ),
    }


def _empty_catalog() -> dict[str, Any]:
    return {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "updated_at_utc": None,
        "default_template_id": "builtin_standard",
        "custom_templates": [],
    }


def _read_json(path: Path) -> Any:
    if path.is_symlink():
        raise ProjectTemplateError(
            "template_catalog_unsafe",
            "The template catalog must be a regular file inside Alexandria application data.",
            status_code=409,
        )
    try:
        if path.is_file() and path.stat().st_size > MAX_TEMPLATE_CATALOG_BYTES:
            raise ProjectTemplateError(
                "template_catalog_too_large",
                "The template catalog exceeds the supported size limit.",
                status_code=409,
            )
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectTemplateError(
            "template_catalog_invalid",
            f"The template catalog could not be read: {exc}",
            status_code=409,
        ) from exc


def _write_json_atomic(value: Mapping[str, Any], path: Path) -> None:
    if path.is_symlink():
        raise ProjectTemplateError(
            "template_catalog_unsafe",
            "The template catalog must be a regular file inside Alexandria application data.",
            status_code=409,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _template_fingerprint(value: Mapping[str, Any]) -> str:
    return fingerprint_value(
        {
            key: value.get(key)
            for key in (
                "id",
                "name",
                "description",
                "generation_method",
                "preset",
                "source_language",
                "output_language",
                "intent",
                "built_in",
                "created_at_utc",
                "updated_at_utc",
            )
        }
    )


def _public_template(
    value: Mapping[str, Any],
    *,
    default_template_id: str,
) -> dict[str, Any]:
    result = {
        "id": value["id"],
        "name": value["name"],
        "description": value["description"],
        "generation_method": value["generation_method"],
        "preset": value["preset"],
        "source_language": value["source_language"],
        "output_language": value["output_language"],
        "intent": value["intent"],
        "built_in": value.get("built_in") is True,
        "default": value["id"] == default_template_id,
        "editable": value.get("built_in") is not True,
        "duplicable": True,
        "deletable": value.get("built_in") is not True,
        "created_at_utc": value.get("created_at_utc"),
        "updated_at_utc": value.get("updated_at_utc"),
        "technical_details": {
            "preset_key": value["preset"],
            "generation_method_key": value["generation_method"],
        },
    }
    result["fingerprint"] = _template_fingerprint(result)
    return result


def _validate_catalog(value: Any) -> dict[str, Any]:
    if value is None:
        return _empty_catalog()
    if not isinstance(value, Mapping):
        raise ProjectTemplateError(
            "template_catalog_invalid",
            "The template catalog must contain an object.",
        )
    if value.get("schema_version") != TEMPLATE_SCHEMA_VERSION:
        raise ProjectTemplateError(
            "template_catalog_schema_unsupported",
            "The template catalog schema is unsupported.",
        )
    default_id = str(value.get("default_template_id") or "builtin_standard")
    custom_rows = value.get("custom_templates")
    if not isinstance(custom_rows, list):
        raise ProjectTemplateError(
            "template_catalog_invalid",
            "custom_templates must be an array.",
        )
    normalized = []
    identifiers = {item["id"] for item in BUILT_IN_TEMPLATES}
    names = {item["name"].casefold() for item in BUILT_IN_TEMPLATES}
    for index, row in enumerate(custom_rows):
        if not isinstance(row, Mapping):
            raise ProjectTemplateError(
                "template_catalog_invalid",
                f"Custom template {index + 1} must be an object.",
            )
        identifier = str(row.get("id") or "")
        if not TEMPLATE_ID_RE.fullmatch(identifier) or identifier in identifiers:
            raise ProjectTemplateError(
                "template_id_invalid",
                "A custom template has an invalid or duplicate ID.",
            )
        fields = _normalize_template_fields(row)
        if fields["name"].casefold() in names:
            raise ProjectTemplateError(
                "template_name_conflict",
                f"Template name already exists: {fields['name']}.",
            )
        identifiers.add(identifier)
        names.add(fields["name"].casefold())
        normalized.append(
            {
                "id": identifier,
                **fields,
                "built_in": False,
                "created_at_utc": str(row.get("created_at_utc") or ""),
                "updated_at_utc": str(row.get("updated_at_utc") or ""),
            }
        )
    if default_id not in identifiers:
        raise ProjectTemplateError(
            "template_default_invalid",
            "The default template does not exist.",
        )
    return {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "updated_at_utc": value.get("updated_at_utc"),
        "default_template_id": default_id,
        "custom_templates": normalized,
    }


def load_template_catalog(data_root: str | Path) -> dict[str, Any]:
    return _validate_catalog(_read_json(template_catalog_path(data_root)))


def _catalog_fingerprint(catalog: Mapping[str, Any]) -> str:
    return fingerprint_value(
        {
            "schema_version": catalog["schema_version"],
            "default_template_id": catalog["default_template_id"],
            "custom_templates": [
                _template_fingerprint(item)
                for item in catalog["custom_templates"]
            ],
        }
    )


def _assert_catalog_fingerprint(
    catalog: Mapping[str, Any],
    expected: str,
) -> None:
    actual = _catalog_fingerprint(catalog)
    if expected != actual:
        raise ProjectTemplateError(
            "template_catalog_conflict",
            "Templates changed since this view was loaded. Reload and try again.",
            status_code=409,
            context={"actual_catalog_fingerprint": actual},
        )


def _all_templates(catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    built_in = [
        {
            **item,
            "built_in": True,
            "created_at_utc": None,
            "updated_at_utc": None,
        }
        for item in BUILT_IN_TEMPLATES
    ]
    return built_in + copy.deepcopy(catalog["custom_templates"])


def _project_usage(data_root: Path, template_id: str) -> list[dict[str, Any]]:
    projects_root = data_root / "Projects"
    if not projects_root.is_dir():
        return []
    usage = []
    for manifest_path in sorted(projects_root.glob("*/alexandria-project.json")):
        if manifest_path.is_symlink():
            continue
        value = _read_json(manifest_path)
        if not isinstance(value, Mapping):
            continue
        creation = value.get("creation")
        configured = None
        if isinstance(creation, Mapping):
            configured = creation.get("template_id")
        configured = configured or value.get("template_id")
        if configured != template_id:
            continue
        usage.append(
            {
                "project_id": value.get("project_id"),
                "project_name": value.get("name") or (
                    value.get("book", {}).get("title")
                    if isinstance(value.get("book"), Mapping)
                    else None
                ),
                "manifest_relative_path": manifest_path.relative_to(data_root).as_posix(),
                "historical": True,
                "blocking": False,
                "message": "The project already contains materialized settings; deleting the template does not rewrite it.",
            }
        )
    return usage


def list_project_templates(data_root: str | Path) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    catalog = load_template_catalog(root)
    templates = [
        _public_template(
            item,
            default_template_id=catalog["default_template_id"],
        )
        for item in _all_templates(catalog)
    ]
    templates.sort(
        key=lambda item: (
            not item["default"],
            not item["built_in"],
            item["name"].casefold(),
        )
    )
    return {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "catalog_fingerprint": _catalog_fingerprint(catalog),
        "default_template_id": catalog["default_template_id"],
        "summary": {
            "template_count": len(templates),
            "built_in_count": sum(item["built_in"] for item in templates),
            "custom_count": sum(not item["built_in"] for item in templates),
        },
        "filters": {
            "generation_methods": sorted(GENERATION_METHODS),
            "presets": [
                "standard",
                "maximum_fidelity",
                "faster_draft",
                "custom",
            ],
        },
        "templates": templates,
        "hidden_from_normal_ui": [
            "model",
            "prompt",
            "context_length",
            "cache_location",
            "catalog_fingerprint",
            "template_fingerprint",
        ],
    }


def resolve_project_template(
    *,
    data_root: str | Path,
    template_id: str,
) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    catalog = load_template_catalog(root)
    template = next(
        (item for item in _all_templates(catalog) if item["id"] == template_id),
        None,
    )
    if template is None:
        raise ProjectTemplateError(
            "template_not_found",
            "The requested template was not found.",
            status_code=404,
        )
    return _public_template(
        template,
        default_template_id=catalog["default_template_id"],
    )


def _find_custom(catalog: Mapping[str, Any], template_id: str) -> tuple[int, dict[str, Any]]:
    for index, row in enumerate(catalog["custom_templates"]):
        if row["id"] == template_id:
            return index, row
    if any(item["id"] == template_id for item in BUILT_IN_TEMPLATES):
        raise ProjectTemplateError(
            "template_builtin_immutable",
            "Built-in templates cannot be edited or deleted. Duplicate it first.",
            status_code=409,
        )
    raise ProjectTemplateError(
        "template_not_found",
        "The requested template was not found.",
        status_code=404,
    )


def _assert_unique_name(
    catalog: Mapping[str, Any],
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    for row in _all_templates(catalog):
        if row["id"] != exclude_id and row["name"].casefold() == name.casefold():
            raise ProjectTemplateError(
                "template_name_conflict",
                "A template with that name already exists.",
                status_code=409,
                context={"name": name},
            )


def create_project_template(
    *,
    data_root: str | Path,
    fields: Mapping[str, Any],
    expected_catalog_fingerprint: str,
) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    with _LOCK:
        catalog = load_template_catalog(root)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        normalized = _normalize_template_fields(fields)
        _assert_unique_name(catalog, normalized["name"])
        timestamp = utc_now()
        row = {
            "id": f"template_{uuid.uuid4().hex[:20]}",
            **normalized,
            "built_in": False,
            "created_at_utc": timestamp,
            "updated_at_utc": timestamp,
        }
        catalog["custom_templates"].append(row)
        catalog["updated_at_utc"] = timestamp
        _write_json_atomic(catalog, template_catalog_path(root))
        result = list_project_templates(root)
        result["template"] = next(
            item for item in result["templates"] if item["id"] == row["id"]
        )
        return result


def update_project_template(
    *,
    data_root: str | Path,
    template_id: str,
    fields: Mapping[str, Any],
    expected_catalog_fingerprint: str,
    expected_template_fingerprint: str,
) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    with _LOCK:
        catalog = load_template_catalog(root)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        index, existing = _find_custom(catalog, template_id)
        public = _public_template(
            existing,
            default_template_id=catalog["default_template_id"],
        )
        if public["fingerprint"] != expected_template_fingerprint:
            raise ProjectTemplateError(
                "template_conflict",
                "This template changed since it was loaded. Reload and try again.",
                status_code=409,
            )
        normalized = _normalize_template_fields(fields)
        _assert_unique_name(catalog, normalized["name"], exclude_id=template_id)
        timestamp = utc_now()
        catalog["custom_templates"][index] = {
            **existing,
            **normalized,
            "updated_at_utc": timestamp,
        }
        catalog["updated_at_utc"] = timestamp
        _write_json_atomic(catalog, template_catalog_path(root))
        result = list_project_templates(root)
        result["template"] = next(
            item for item in result["templates"] if item["id"] == template_id
        )
        return result


def duplicate_project_template(
    *,
    data_root: str | Path,
    template_id: str,
    name: str,
    expected_catalog_fingerprint: str,
) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    with _LOCK:
        catalog = load_template_catalog(root)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        source = next(
            (item for item in _all_templates(catalog) if item["id"] == template_id),
            None,
        )
        if source is None:
            raise ProjectTemplateError(
                "template_not_found",
                "The requested template was not found.",
                status_code=404,
            )
        fields = {
            key: source[key]
            for key in (
                "description",
                "generation_method",
                "preset",
                "source_language",
                "output_language",
                "intent",
            )
        }
        fields["name"] = _text(name, field="name", maximum=80)
        normalized = _normalize_template_fields(fields)
        _assert_unique_name(catalog, normalized["name"])
        timestamp = utc_now()
        row = {
            "id": f"template_{uuid.uuid4().hex[:20]}",
            **normalized,
            "built_in": False,
            "created_at_utc": timestamp,
            "updated_at_utc": timestamp,
        }
        catalog["custom_templates"].append(row)
        catalog["updated_at_utc"] = timestamp
        _write_json_atomic(catalog, template_catalog_path(root))
        result = list_project_templates(root)
        result["template"] = next(
            item for item in result["templates"] if item["id"] == row["id"]
        )
        result["duplicated_from"] = template_id
        return result


def set_default_project_template(
    *,
    data_root: str | Path,
    template_id: str,
    expected_catalog_fingerprint: str,
) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    with _LOCK:
        catalog = load_template_catalog(root)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        if not any(item["id"] == template_id for item in _all_templates(catalog)):
            raise ProjectTemplateError(
                "template_not_found",
                "The requested template was not found.",
                status_code=404,
            )
        catalog["default_template_id"] = template_id
        catalog["updated_at_utc"] = utc_now()
        _write_json_atomic(catalog, template_catalog_path(root))
        return list_project_templates(root)


def project_template_delete_impact(
    *,
    data_root: str | Path,
    template_id: str,
) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    catalog = load_template_catalog(root)
    index, row = _find_custom(catalog, template_id)
    del index
    public = _public_template(
        row,
        default_template_id=catalog["default_template_id"],
    )
    usage = _project_usage(root, template_id)
    is_default = catalog["default_template_id"] == template_id
    blockers = []
    if is_default:
        blockers.append(
            {
                "code": "template_is_default",
                "message": "Choose another default template before deleting this one.",
            }
        )
    return {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "template": public,
        "catalog_fingerprint": _catalog_fingerprint(catalog),
        "usage": usage,
        "usage_count": len(usage),
        "blocking_reasons": blockers,
        "safe_to_delete": not blockers,
        "requires_usage_acknowledgement": bool(usage),
        "confirmation_text": public["name"],
        "message": (
            "Deleting this template does not rewrite existing projects."
            if usage
            else "This custom template is not referenced by an existing managed project."
        ),
    }


def delete_project_template(
    *,
    data_root: str | Path,
    template_id: str,
    expected_catalog_fingerprint: str,
    expected_template_fingerprint: str,
    confirmation_text: str,
    acknowledge_usage: bool,
) -> dict[str, Any]:
    root = Path(data_root).expanduser().resolve()
    with _LOCK:
        impact = project_template_delete_impact(
            data_root=root,
            template_id=template_id,
        )
        if impact["catalog_fingerprint"] != expected_catalog_fingerprint:
            raise ProjectTemplateError(
                "template_catalog_conflict",
                "Templates changed since the delete impact was loaded.",
                status_code=409,
            )
        template = impact["template"]
        if template["fingerprint"] != expected_template_fingerprint:
            raise ProjectTemplateError(
                "template_conflict",
                "The template changed since the delete impact was loaded.",
                status_code=409,
            )
        if not impact["safe_to_delete"]:
            raise ProjectTemplateError(
                "template_delete_blocked",
                impact["blocking_reasons"][0]["message"],
                status_code=409,
            )
        if confirmation_text != impact["confirmation_text"]:
            raise ProjectTemplateError(
                "template_delete_confirmation_invalid",
                "Type the exact template name to confirm deletion.",
                status_code=409,
            )
        if impact["requires_usage_acknowledgement"] and not acknowledge_usage:
            raise ProjectTemplateError(
                "template_delete_usage_acknowledgement_required",
                "Acknowledge the historical project usage before deleting this template.",
                status_code=409,
            )
        catalog = load_template_catalog(root)
        _assert_catalog_fingerprint(catalog, expected_catalog_fingerprint)
        index, _ = _find_custom(catalog, template_id)
        del catalog["custom_templates"][index]
        catalog["updated_at_utc"] = utc_now()
        _write_json_atomic(catalog, template_catalog_path(root))
        result = list_project_templates(root)
        result["deleted_template_id"] = template_id
        return result
