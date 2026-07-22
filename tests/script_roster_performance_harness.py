from __future__ import annotations

import argparse
import copy
import inspect
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from generation_state import fingerprint_value  # noqa: E402
from llm_schemas import get_schema, validate_contract  # noqa: E402
from project import group_into_chunks  # noqa: E402
from script_audit import audit_script_chunk  # noqa: E402


REPORT_PREFIX = "SCRIPT_ROSTER_PERFORMANCE_REPORT="
SCRIPT_ENTRY_COUNT = 6_000
ROSTER_ITEM_COUNT = 1_500
REPEATS = 5


class PerformanceHarnessError(RuntimeError):
    pass


def _time_ms(function: Callable[..., Any], *args, **kwargs) -> tuple[float, Any]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    elapsed = (time.perf_counter() - started) * 1_000.0
    return elapsed, result


def _median_ms(
    function: Callable[..., Any],
    *args,
    repeats: int = REPEATS,
    **kwargs,
) -> tuple[float, Any]:
    timings: list[float] = []
    result: Any = None
    for _ in range(repeats):
        elapsed, result = _time_ms(function, *args, **kwargs)
        timings.append(elapsed)
    return statistics.median(timings), result


def _script_fixture(count: int) -> tuple[str, list[dict[str, str]]]:
    entries: list[dict[str, str]] = []
    source_parts: list[str] = []
    speakers = ("NARRATOR", "DOCTOR", "ROZ", "CHRIS")
    for index in range(count):
        text = f"Measured source sentence {index}."
        entries.append(
            {
                "speaker": speakers[index % len(speakers)],
                "text": text,
                "instruct": "Measured neutral delivery.",
            }
        )
        source_parts.append(
            text
            if speakers[index % len(speakers)] == "NARRATOR"
            else f"“{text}”"
        )
    return " ".join(source_parts), entries


def _roster_discovery_fixture(count: int) -> dict[str, Any]:
    entities = []
    for index in range(count):
        display_name = f"Character {index}"
        entities.append(
            {
                "identity_seed": f"character-{index}",
                "canonical_name": display_name.upper(),
                "display_name": display_name,
                "entity_kind": "character",
                "speaking_status": "speaker",
                "titles": [],
                "aliases": [],
                "nicknames": [],
                "pronouns": [],
                "species": [],
                "relationships": [],
                "voice_clues": [],
                "sample_lines": [],
                "confidence": 1.0,
                "resolution_status": "resolved",
                "unresolved_questions": [],
                "evidence": [
                    {
                        "quote": display_name,
                        "start_char": 0,
                        "end_char": len(display_name),
                        "category": "name",
                        "confidence": 1.0,
                        "basis": "explicit",
                    }
                ],
            }
        )
    return {
        "entities": entities,
        "warnings": [],
    }


def _resolve_schema(
    schema: dict[str, Any],
    root_schema: dict[str, Any],
) -> dict[str, Any]:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        resolved: Any = root_schema
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            resolved = resolved[token]
        if not isinstance(resolved, dict):
            raise PerformanceHarnessError(
                f"Schema reference {reference!r} did not resolve to an object."
            )
        return _resolve_schema(resolved, root_schema)

    for keyword in ("oneOf", "anyOf"):
        options = schema.get(keyword)
        if isinstance(options, list) and options:
            return _resolve_schema(options[0], root_schema)

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        merged: dict[str, Any] = {}
        for item in all_of:
            resolved = _resolve_schema(item, root_schema)
            for key, value in resolved.items():
                if key == "properties":
                    merged.setdefault("properties", {}).update(value)
                elif key == "required":
                    merged.setdefault("required", [])
                    for required_key in value:
                        if required_key not in merged["required"]:
                            merged["required"].append(required_key)
                else:
                    merged[key] = value
        return merged

    return schema


def _minimal_value(
    schema: dict[str, Any],
    *,
    index: int,
    expand_first_array: bool,
    expanded: list[bool],
    root_schema: dict[str, Any],
) -> Any:
    schema = _resolve_schema(schema, root_schema)
    if "const" in schema:
        return copy.deepcopy(schema["const"])
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return copy.deepcopy(enum[0])

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next(
            (value for value in schema_type if value != "null"),
            "null",
        )
    if schema_type is None:
        if "properties" in schema:
            schema_type = "object"
        elif "items" in schema:
            schema_type = "array"

    if schema_type == "object":
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        return {
            key: _minimal_value(
                properties.get(key, {}),
                index=index,
                expand_first_array=expand_first_array,
                expanded=expanded,
                root_schema=root_schema,
            )
            for key in required
        }

    if schema_type == "array":
        minimum = int(schema.get("minItems") or 0)
        count = minimum
        if expand_first_array and not expanded[0]:
            maximum = schema.get("maxItems")
            count = max(count, ROSTER_ITEM_COUNT)
            if isinstance(maximum, int):
                count = min(count, maximum)
            expanded[0] = True
        item_schema = schema.get("items") or {}
        return [
            _minimal_value(
                item_schema,
                index=item_index,
                expand_first_array=False,
                expanded=expanded,
                root_schema=root_schema,
            )
            for item_index in range(count)
        ]

    if schema_type == "string":
        minimum = max(int(schema.get("minLength") or 1), 1)
        base = f"item-{index}"
        if len(base) < minimum:
            base += "x" * (minimum - len(base))
        return base
    if schema_type == "integer":
        return int(schema.get("minimum") or 0)
    if schema_type == "number":
        return float(schema.get("minimum") or 0.0)
    if schema_type == "boolean":
        return False
    if schema_type == "null":
        return None
    return {}


def _large_contract_value(contract: str) -> tuple[Any, int]:
    schema = get_schema(contract)
    if contract == "roster_discovery":
        entities_schema = schema["properties"]["entities"]
        maximum = entities_schema.get("maxItems")
        count = (
            min(ROSTER_ITEM_COUNT, maximum)
            if isinstance(maximum, int)
            else ROSTER_ITEM_COUNT
        )
        return _roster_discovery_fixture(count), count

    expanded = [False]
    value = _minimal_value(
        schema,
        index=0,
        expand_first_array=True,
        expanded=expanded,
        root_schema=schema,
    )
    if not expanded[0]:
        raise PerformanceHarnessError(
            f"{contract} schema did not expose a representative array."
        )

    def largest_list(current: Any) -> int:
        if isinstance(current, list):
            return max(
                [len(current)]
                + [largest_list(item) for item in current]
            )
        if isinstance(current, dict):
            return max(
                [0]
                + [largest_list(item) for item in current.values()]
            )
        return 0

    return value, largest_list(value)


def _run_fidelity_audit(
    source: str,
    entries: list[dict[str, str]],
) -> Any:
    parameters = inspect.signature(audit_script_chunk).parameters
    if "source_text" in parameters and "entries" in parameters:
        return audit_script_chunk(
            source_text=source,
            entries=entries,
        )
    if "source" in parameters and "script_entries" in parameters:
        return audit_script_chunk(
            source=source,
            script_entries=entries,
        )
    return audit_script_chunk(source, entries)


def run() -> dict[str, Any]:
    source, entries = _script_fixture(SCRIPT_ENTRY_COUNT)
    metrics: dict[str, float] = {}

    metrics["script_contract_median_ms"], normalized = _median_ms(
        validate_contract,
        "script",
        entries,
    )
    if normalized != entries:
        raise PerformanceHarnessError(
            "Script performance fixture required normalization."
        )

    metrics["script_fingerprint_median_ms"], script_fingerprint = _median_ms(
        fingerprint_value,
        entries,
    )
    if not isinstance(script_fingerprint, str) or len(script_fingerprint) != 64:
        raise PerformanceHarnessError("Script fingerprint output was invalid.")

    metrics["script_fidelity_audit_median_ms"], audit = _median_ms(
        _run_fidelity_audit,
        source,
        entries,
        repeats=3,
    )
    audit_passed = (
        bool(audit.get("passed"))
        if isinstance(audit, dict)
        else bool(getattr(audit, "passed", False))
    )
    if not audit_passed:
        audit_details = (
            audit
            if isinstance(audit, dict)
            else (
                audit.to_dict()
                if hasattr(audit, "to_dict")
                else repr(audit)
            )
        )
        raise PerformanceHarnessError(
            f"Representative Script fidelity audit failed: {audit_details}"
        )

    metrics["script_chunk_grouping_median_ms"], chunks = _median_ms(
        group_into_chunks,
        entries,
    )
    if not chunks:
        raise PerformanceHarnessError("Representative Script chunk grouping was empty.")

    roster_value, roster_items_per_payload = _large_contract_value(
        "roster_discovery"
    )
    if roster_items_per_payload <= 0:
        raise PerformanceHarnessError(
            "Roster performance fixture contained no observations."
        )
    roster_payload_count = max(
        1,
        math.ceil(ROSTER_ITEM_COUNT / roster_items_per_payload),
    )
    roster_values = [
        copy.deepcopy(roster_value)
        for _ in range(roster_payload_count)
    ]

    def validate_roster_values() -> list[Any]:
        return [
            validate_contract("roster_discovery", value)
            for value in roster_values
        ]

    metrics["roster_contract_median_ms"], roster_results = _median_ms(
        validate_roster_values,
        repeats=3,
    )
    if roster_results != roster_values:
        raise PerformanceHarnessError(
            "Roster performance fixtures required normalization."
        )

    metrics["roster_fingerprint_median_ms"], roster_fingerprint = _median_ms(
        fingerprint_value,
        roster_values,
    )
    if not isinstance(roster_fingerprint, str) or len(roster_fingerprint) != 64:
        raise PerformanceHarnessError("Roster fingerprint output was invalid.")

    limits_ms = {
        "script_contract_median_ms": 2_500.0,
        "script_fingerprint_median_ms": 2_500.0,
        "script_fidelity_audit_median_ms": 6_000.0,
        "script_chunk_grouping_median_ms": 2_500.0,
        "roster_contract_median_ms": 4_000.0,
        "roster_fingerprint_median_ms": 2_500.0,
    }
    failures = {
        name: {
            "measured_ms": round(metrics[name], 3),
            "limit_ms": limit,
        }
        for name, limit in limits_ms.items()
        if metrics[name] > limit
    }
    return {
        "schema_version": 1,
        "script_entry_count": SCRIPT_ENTRY_COUNT,
        "roster_target_item_count": ROSTER_ITEM_COUNT,
        "roster_actual_item_count": (
            roster_items_per_payload * roster_payload_count
        ),
        "roster_items_per_payload": roster_items_per_payload,
        "roster_payload_count": roster_payload_count,
        "script_chunk_count": len(chunks),
        "metrics_ms": {
            name: round(value, 3)
            for name, value in metrics.items()
        },
        "limits_ms": limits_ms,
        "failures": failures,
        "passed": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = run()
    if args.pretty:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(REPORT_PREFIX + json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
