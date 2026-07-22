from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping
from urllib.parse import urlencode

from generation_state import fingerprint_value


MORE_TOOLS_SCHEMA_VERSION = 1
MAX_CONTEXT_VALUE = 512

MORE_TOOL_DEFINITIONS = (
    {
        "tool": "advanced-character-operations",
        "title": "Advanced identity operations",
        "description": "Review guarded speaker-label, alias, identity, and rollback operations.",
        "category": "character",
        "category_label": "Character and Voice",
        "icon": "fa-user-gear",
        "legacy_tab": "speaker-management",
        "context_scope": "character_optional",
        "mutates_project": True,
        "danger_level": "guarded",
    },
    {
        "tool": "voice-designer",
        "title": "Voice designer",
        "description": "Create and preview reusable Voice material without assigning it automatically.",
        "category": "character",
        "category_label": "Character and Voice",
        "icon": "fa-wand-magic-sparkles",
        "legacy_tab": "designer",
        "context_scope": "character_optional",
        "mutates_project": True,
        "danger_level": "ordinary",
    },
    {
        "tool": "audio-preparer",
        "title": "Audio preparer",
        "description": "Transcribe and segment owned recordings into reviewable Voice material.",
        "category": "character",
        "category_label": "Character and Voice",
        "icon": "fa-wave-square",
        "legacy_tab": "preparer",
        "context_scope": "character_optional",
        "mutates_project": True,
        "danger_level": "ordinary",
    },
    {
        "tool": "dataset-builder",
        "title": "Dataset builder",
        "description": "Review and package prepared Voice clips without starting training.",
        "category": "character",
        "category_label": "Character and Voice",
        "icon": "fa-table-list",
        "legacy_tab": "dataset-builder",
        "context_scope": "character_optional",
        "mutates_project": True,
        "danger_level": "ordinary",
    },
    {
        "tool": "voice-training",
        "title": "Voice Lab",
        "description": "Inspect preparation, reference banks, and experimental training artifacts.",
        "category": "character",
        "category_label": "Character and Voice",
        "icon": "fa-flask",
        "legacy_tab": "training",
        "context_scope": "character_optional",
        "mutates_project": True,
        "danger_level": "experimental",
    },
    {
        "tool": "maintenance",
        "title": "Maintenance",
        "description": "Inspect health, recovery, migration, diagnostics, and guarded technical actions.",
        "category": "system",
        "category_label": "Application",
        "icon": "fa-screwdriver-wrench",
        "legacy_tab": "project-recovery",
        "context_scope": "project_optional",
        "mutates_project": True,
        "danger_level": "guarded",
    },
    {
        "tool": "model-cache",
        "title": "Local model cache",
        "description": "Inspect pinned model availability and explicit Download or Repair actions.",
        "category": "system",
        "category_label": "Application",
        "icon": "fa-box-archive",
        "legacy_tab": "project-recovery",
        "context_scope": "global",
        "mutates_project": False,
        "danger_level": "guarded",
    },
    {
        "tool": "help-center",
        "title": "Help Center",
        "description": "Search versioned guidance bundled with Alexandria for offline use.",
        "category": "help",
        "category_label": "Help",
        "icon": "fa-circle-question",
        "legacy_tab": "speaker-management",
        "context_scope": "global",
        "mutates_project": False,
        "danger_level": "read_only",
    },
)


class MoreToolsError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.context = copy.deepcopy(dict(context or {}))

    def as_detail(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
        }
        if self.context:
            result["context"] = copy.deepcopy(self.context)
        return result


def _context_value(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MoreToolsError(
            "more_context_invalid",
            f"{field} must be text.",
            context={"field": field},
        )
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_CONTEXT_VALUE or any(
        ord(character) < 32 or ord(character) == 127
        for character in normalized
    ):
        raise MoreToolsError(
            "more_context_invalid",
            f"{field} contains unsupported characters or is too long.",
            context={"field": field},
        )
    return normalized


def _tool_route(
    *,
    tool: str,
    project_id: str | None,
    character_id: str | None,
    source: str | None,
    return_route: str,
) -> dict[str, Any]:
    context = {
        "tool": tool,
        "return": return_route,
    }
    if project_id:
        context["project"] = project_id
    if character_id:
        context["character"] = character_id
    if source:
        context["source"] = source
    query = urlencode(context)
    return {
        "destination": "more",
        "context": context,
        "hash": f"#/more?{query}",
    }


def inspect_more_tools(
    *,
    project_id: str | None = None,
    character_id: str | None = None,
    source: str | None = None,
    return_route: str | None = None,
) -> dict[str, Any]:
    safe_project = _context_value(project_id, field="project_id")
    safe_character = _context_value(character_id, field="character_id")
    safe_source = _context_value(source, field="source")
    safe_return = _context_value(return_route, field="return_route") or "#/more"
    if safe_character and not safe_project:
        raise MoreToolsError(
            "more_character_project_required",
            "Character context requires a project context.",
            context={"character_id": safe_character},
        )

    tools = []
    for definition in MORE_TOOL_DEFINITIONS:
        item = copy.deepcopy(definition)
        item["route"] = _tool_route(
            tool=item["tool"],
            project_id=safe_project,
            character_id=safe_character,
            source=safe_source,
            return_route=safe_return,
        )
        item["context"] = {
            "project_available": bool(safe_project),
            "character_available": bool(safe_character),
            "project_id": safe_project,
            "character_id": safe_character,
            "source": safe_source,
            "label": (
                "Selected character"
                if safe_character
                else "Current project"
                if safe_project
                else "Global"
            ),
        }
        item["availability"] = {
            "state": "available",
            "message": (
                "Opens for the selected character."
                if safe_character
                and item["context_scope"] == "character_optional"
                else "Opens for the current project. Choose a character inside the tool if needed."
                if safe_project
                and item["context_scope"] == "character_optional"
                else "Opens without project context. Select a project or character inside the tool if required."
                if item["context_scope"] != "global"
                else "Available globally."
            ),
        }
        item["tool_id"] = "more_tool_" + hashlib.sha256(
            item["tool"].encode("utf-8")
        ).hexdigest()[:20]
        item["fingerprint"] = fingerprint_value(
            {
                key: item[key]
                for key in (
                    "tool",
                    "title",
                    "description",
                    "category",
                    "legacy_tab",
                    "context_scope",
                    "mutates_project",
                    "danger_level",
                    "route",
                    "availability",
                )
            }
        )
        tools.append(item)

    categories = []
    for category in ("character", "system", "help"):
        matching = [item for item in tools if item["category"] == category]
        if not matching:
            continue
        categories.append(
            {
                "id": category,
                "label": matching[0]["category_label"],
                "tool_count": len(matching),
            }
        )

    return {
        "schema_version": MORE_TOOLS_SCHEMA_VERSION,
        "context": {
            "project_id": safe_project,
            "character_id": safe_character,
            "source": safe_source,
            "return_route": safe_return,
            "label": (
                "Selected character"
                if safe_character
                else "Current project"
                if safe_project
                else "Global"
            ),
        },
        "summary": {
            "tool_count": len(tools),
            "read_only_count": sum(
                item["danger_level"] == "read_only" for item in tools
            ),
            "guarded_count": sum(
                item["danger_level"] == "guarded" for item in tools
            ),
            "experimental_count": sum(
                item["danger_level"] == "experimental" for item in tools
            ),
        },
        "categories": categories,
        "tools": tools,
        "landing_mutation_supported": False,
        "fingerprint": fingerprint_value(
            {
                "context": {
                    "project_id": safe_project,
                    "character_id": safe_character,
                    "source": safe_source,
                    "return_route": safe_return,
                },
                "tools": [item["fingerprint"] for item in tools],
            }
        ),
    }
