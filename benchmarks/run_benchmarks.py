#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
BENCHMARK_DIR = ROOT / "benchmarks"
DEFAULT_MANIFEST = BENCHMARK_DIR / "manifest.json"
DEFAULT_CONFIG = APP_DIR / "config.json"
DEFAULT_RESULTS = BENCHMARK_DIR / "results"
EXPECTED = BENCHMARK_DIR / "expected"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import generate_script  # noqa: E402
import llm_telemetry  # noqa: E402
import review_script  # noqa: E402
from default_prompts import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_PROMPT,
)
from llm_config import (  # noqa: E402
    DEFAULT_MODEL_NAME,
    build_runtime_client,
    normalized_llm_section,
)
from review_audit import (  # noqa: E402
    audit_review_batch,
    normalize_review_text,
)
from review_prompts import (  # noqa: E402
    REVIEW_SYSTEM_PROMPT,
    REVIEW_USER_PROMPT,
)
from script_audit import audit_script_chunk  # noqa: E402


WORD_PATTERN = re.compile(
    r"[^\W_]+(?:['’][^\W_]+)*",
    flags=re.UNICODE,
)


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def atomic_json_write(
    path: Path,
    value: Any,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )

    try:
        with temporary.open(
            "w",
            encoding="utf-8",
        ) as output:
            json.dump(
                value,
                output,
                indent=2,
                ensure_ascii=False,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())

        os.replace(
            temporary,
            path,
        )
    finally:
        temporary.unlink(
            missing_ok=True
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for block in iter(
            lambda: source.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def git_head() -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
    ):
        return None

    return result.stdout.strip() or None


def safe_filename(value: str) -> str:
    normalized = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        value,
    ).strip("._-")

    return normalized or "model"


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    benchmark_dir: Path = BENCHMARK_DIR,
) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != 1:
        raise ValueError(
            "Unsupported benchmark manifest schema"
        )

    cases = manifest.get("cases")

    if not isinstance(cases, list) or not cases:
        raise ValueError(
            "Benchmark manifest has no cases"
        )

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for raw_case in cases:
        if not isinstance(raw_case, Mapping):
            raise ValueError(
                "Benchmark case is not an object"
            )

        case = dict(raw_case)
        case_id = case.get("id")
        kind = case.get("kind")

        if (
            not isinstance(case_id, str)
            or not case_id
        ):
            raise ValueError(
                "Benchmark case has no valid ID"
            )

        if case_id in seen_ids:
            raise ValueError(
                f"Duplicate benchmark case: {case_id}"
            )

        if kind not in {
            "script",
            "review",
        }:
            raise ValueError(
                f"Case {case_id} has invalid kind"
            )

        for field in (
            "input",
            "expected",
        ):
            relative = case.get(field)

            if (
                not isinstance(relative, str)
                or not relative
            ):
                raise ValueError(
                    f"Case {case_id} has invalid {field}"
                )

            path = benchmark_dir / relative

            if not path.is_file():
                raise ValueError(
                    f"Case {case_id} is missing {field}: {path}"
                )

        seen_ids.add(case_id)
        validated.append(case)

    return validated


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    value = load_json(path)

    if not isinstance(value, dict):
        raise ValueError(
            "Application config root is not an object"
        )

    return value


def normalize_speaker(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    return re.sub(
        r"\s+",
        " ",
        value.strip().upper(),
    )


def alias_lookup(
    expected: Mapping[str, Any],
) -> dict[str, str]:
    lookup: dict[str, str] = {
        "NARRATOR": "NARRATOR",
    }

    groups = expected.get(
        "canonical_speakers",
        {},
    )

    if not isinstance(groups, Mapping):
        return lookup

    for canonical, aliases in groups.items():
        normalized_canonical = normalize_speaker(
            canonical
        )

        if not normalized_canonical:
            continue

        lookup[
            normalized_canonical
        ] = normalized_canonical

        if not isinstance(aliases, list):
            continue

        for alias in aliases:
            normalized_alias = normalize_speaker(
                alias
            )

            if normalized_alias:
                lookup[
                    normalized_alias
                ] = normalized_canonical

    return lookup


def canonical_speaker(
    value: Any,
    lookup: Mapping[str, str],
) -> str:
    normalized = normalize_speaker(value)

    return lookup.get(
        normalized,
        normalized,
    )


def valid_entry_shape(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and set(entry)
        == {
            "speaker",
            "text",
            "instruct",
        }
        and all(
            isinstance(
                entry[field],
                str,
            )
            and bool(
                entry[field].strip()
            )
            for field in (
                "speaker",
                "text",
                "instruct",
            )
        )
    )


def entries_have_valid_shape(
    entries: Any,
) -> bool:
    return (
        isinstance(entries, list)
        and bool(entries)
        and all(
            valid_entry_shape(entry)
            for entry in entries
        )
    )


def combined_text(
    entries: Iterable[Mapping[str, Any]],
) -> str:
    return "\n".join(
        str(entry.get("text", ""))
        for entry in entries
    )


def word_labels(
    entries: Iterable[Mapping[str, Any]],
    token_normalizations: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    labeled: list[tuple[str, str]] = []
    normalizations = {
        str(key).casefold(): str(value).casefold()
        for key, value in (
            token_normalizations or {}
        ).items()
    }

    for entry in entries:
        speaker = normalize_speaker(
            entry.get("speaker")
        )

        for match in WORD_PATTERN.finditer(
            str(entry.get("text", ""))
        ):
            token = match.group(0).casefold()
            token = normalizations.get(
                token,
                token,
            )

            labeled.append(
                (
                    token,
                    speaker,
                )
            )

    return labeled

def edit_distance(
    left: list[str],
    right: list[str],
) -> int:
    if len(left) < len(right):
        left, right = right, left

    previous = list(
        range(len(right) + 1)
    )

    for left_index, left_value in enumerate(
        left,
        start=1,
    ):
        current = [left_index]

        for right_index, right_value in enumerate(
            right,
            start=1,
        ):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1]
                    + (
                        left_value
                        != right_value
                    ),
                )
            )

        previous = current

    return previous[-1]


def sequence_accuracy(
    expected: list[str],
    actual: list[str],
) -> float:
    denominator = max(
        len(expected),
        len(actual),
        1,
    )

    return max(
        0.0,
        1.0
        - (
            edit_distance(
                expected,
                actual,
            )
            / denominator
        ),
    )


def punctuation_sequence(text: str) -> list[str]:
    return [
        character
        for character in text
        if (
            not character.isalnum()
            and not character.isspace()
        )
    ]


def aligned_script_metrics(
    expected_entries: list[dict[str, Any]],
    actual_entries: list[dict[str, Any]],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    raw_normalizations = expected.get(
        "metric_word_normalizations",
        {},
    )
    token_normalizations = (
        raw_normalizations
        if isinstance(
            raw_normalizations,
            Mapping,
        )
        else {}
    )

    expected_words = word_labels(
        expected_entries,
        token_normalizations,
    )
    actual_words = word_labels(
        actual_entries,
        token_normalizations,
    )

    expected_tokens = [
        token
        for token, _ in expected_words
    ]
    actual_tokens = [
        token
        for token, _ in actual_words
    ]

    matcher = difflib.SequenceMatcher(
        None,
        expected_tokens,
        actual_tokens,
        autojunk=False,
    )

    lookup = alias_lookup(expected)
    matched_words = 0
    correct_speakers = 0
    correct_roles = 0
    alias_observations: dict[
        str,
        set[str],
    ] = {}

    canonical_groups = expected.get(
        "canonical_speakers",
        {},
    )
    canonical_names = {
        normalize_speaker(name)
        for name in (
            canonical_groups.keys()
            if isinstance(
                canonical_groups,
                Mapping,
            )
            else []
        )
    }

    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            expected_token, expected_speaker = (
                expected_words[
                    block.a + offset
                ]
            )
            actual_token, actual_speaker = (
                actual_words[
                    block.b + offset
                ]
            )

            del expected_token
            del actual_token

            matched_words += 1

            expected_canonical = (
                canonical_speaker(
                    expected_speaker,
                    lookup,
                )
            )
            actual_canonical = (
                canonical_speaker(
                    actual_speaker,
                    lookup,
                )
            )

            if (
                expected_canonical
                == actual_canonical
            ):
                correct_speakers += 1

            expected_role = (
                "narrator"
                if expected_canonical
                == "NARRATOR"
                else "character"
            )
            actual_role = (
                "narrator"
                if actual_canonical
                == "NARRATOR"
                else "character"
            )

            if expected_role == actual_role:
                correct_roles += 1

            if (
                expected_canonical
                in canonical_names
            ):
                alias_observations.setdefault(
                    expected_canonical,
                    set(),
                ).add(actual_speaker)

    expected_count = len(expected_words)
    actual_count = len(actual_words)

    alias_scores: list[float] = []

    for canonical in canonical_names:
        observed = alias_observations.get(
            canonical,
            set(),
        )

        consistent = (
            len(observed) == 1
            and canonical_speaker(
                next(iter(observed)),
                lookup,
            )
            == canonical
        )

        alias_scores.append(
            1.0 if consistent else 0.0
        )

    expected_text = combined_text(
        expected_entries
    )
    actual_text = combined_text(
        actual_entries
    )

    return {
        "expected_word_count": expected_count,
        "actual_word_count": actual_count,
        "matched_word_count": matched_words,
        "missing_word_count": max(
            0,
            expected_count - matched_words,
        ),
        "extra_word_count": max(
            0,
            actual_count - matched_words,
        ),
        "speaker_accuracy": (
            correct_speakers
            / expected_count
            if expected_count
            else 1.0
        ),
        "narrator_dialogue_accuracy": (
            correct_roles
            / expected_count
            if expected_count
            else 1.0
        ),
        "punctuation_accuracy": (
            sequence_accuracy(
                punctuation_sequence(
                    expected_text
                ),
                punctuation_sequence(
                    actual_text
                ),
            )
        ),
        "alias_consistency": (
            sum(alias_scores)
            / len(alias_scores)
            if alias_scores
            else None
        ),
    }


def review_quality_metrics(
    target: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_text = combined_text(target)
    actual_text = combined_text(actual)
    expected_words = [
        match.group(0).casefold()
        for match in WORD_PATTERN.finditer(
            expected_text
        )
    ]
    actual_words = [
        match.group(0).casefold()
        for match in WORD_PATTERN.finditer(
            actual_text
        )
    ]

    matcher = difflib.SequenceMatcher(
        None,
        expected_words,
        actual_words,
        autojunk=False,
    )

    matched = sum(
        block.size
        for block in matcher.get_matching_blocks()
    )

    return {
        "exact_text_match": (
            normalize_review_text(
                expected_text
            )
            == normalize_review_text(
                actual_text
            )
        ),
        "expected_word_count": len(
            expected_words
        ),
        "actual_word_count": len(
            actual_words
        ),
        "matched_word_count": matched,
        "missing_word_count": max(
            0,
            len(expected_words) - matched,
        ),
        "extra_word_count": max(
            0,
            len(actual_words) - matched,
        ),
        "punctuation_accuracy": (
            sequence_accuracy(
                punctuation_sequence(
                    expected_text
                ),
                punctuation_sequence(
                    actual_text
                ),
            )
        ),
    }


def weighted_rate(
    records: Iterable[Mapping[str, Any]],
    token_key: str,
    rate_key: str,
) -> tuple[float | None, int, float]:
    total_tokens = 0
    estimated_seconds = 0.0

    for record in records:
        metrics = record.get(
            "metrics",
            {},
        )

        if not isinstance(metrics, Mapping):
            continue

        tokens = metrics.get(token_key)
        rate = metrics.get(rate_key)

        if (
            isinstance(tokens, (int, float))
            and isinstance(rate, (int, float))
            and tokens > 0
            and rate > 0
        ):
            total_tokens += int(tokens)
            estimated_seconds += (
                float(tokens)
                / float(rate)
            )

    calculated = (
        total_tokens
        / estimated_seconds
        if estimated_seconds > 0
        else None
    )

    return (
        calculated,
        total_tokens,
        estimated_seconds,
    )


def summarize_requests(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    successful = [
        record
        for record in records
        if record.get("status")
        == "success"
    ]
    corrective = sum(
        record.get("validation_mode")
        == "corrective_retry"
        for record in successful
    )

    (
        prompt_rate,
        prompt_tokens,
        prompt_seconds,
    ) = weighted_rate(
        successful,
        "prompt_tokens",
        "prompt_tokens_per_second",
    )

    (
        output_rate,
        output_tokens,
        output_seconds,
    ) = weighted_rate(
        successful,
        "output_tokens",
        "output_tokens_per_second",
    )

    return {
        "request_count": len(records),
        "successful_request_count": len(
            successful
        ),
        "failed_request_count": (
            len(records) - len(successful)
        ),
        "corrective_retry_requests": (
            corrective
        ),
        "internal_corrective_retry_rate": (
            corrective
            / len(successful)
            if successful
            else None
        ),
        "prompt_tokens": prompt_tokens,
        "prompt_seconds_estimate": (
            prompt_seconds
        ),
        "prompt_tokens_per_second": (
            prompt_rate
        ),
        "output_tokens": output_tokens,
        "output_seconds_estimate": (
            output_seconds
        ),
        "output_tokens_per_second": (
            output_rate
        ),
    }


@contextmanager
def capture_runtime_requests(
    runtime: Any,
    *,
    seed: int,
):
    records: list[dict[str, Any]] = []

    if runtime is None or not hasattr(
        runtime,
        "complete_json",
    ):
        yield records
        return

    original = runtime.complete_json

    def wrapped(**kwargs):
        request_kwargs = dict(kwargs)
        request_kwargs["seed"] = seed
        started = time.perf_counter()

        try:
            result = original(
                **request_kwargs
            )
        except Exception as exc:
            records.append(
                {
                    "status": "error",
                    "contract": request_kwargs.get(
                        "contract"
                    ),
                    "elapsed_seconds": (
                        time.perf_counter()
                        - started
                    ),
                    "error": str(exc),
                    "metrics": {},
                }
            )
            raise

        records.append(
            {
                "status": "success",
                "contract": getattr(
                    result,
                    "contract",
                    request_kwargs.get(
                        "contract"
                    ),
                ),
                "backend": getattr(
                    result,
                    "backend",
                    None,
                ),
                "validation_mode": getattr(
                    result,
                    "validation_mode",
                    None,
                ),
                "elapsed_seconds": (
                    time.perf_counter()
                    - started
                ),
                "metrics": dict(
                    getattr(
                        result,
                        "metrics",
                        {},
                    )
                    or {}
                ),
            }
        )

        return result

    runtime.complete_json = wrapped

    try:
        yield records
    finally:
        runtime.complete_json = original


def reset_telemetry(path: Path) -> None:
    path.unlink(missing_ok=True)


def latest_pipeline(
    path: Path,
) -> dict[str, Any] | None:
    snapshot = (
        llm_telemetry
        .read_llm_telemetry(
            path=path
        )
    )
    latest = snapshot.get(
        "latest_request"
    )

    if not isinstance(latest, Mapping):
        return None

    pipeline = latest.get("pipeline")

    return (
        dict(pipeline)
        if isinstance(pipeline, Mapping)
        else None
    )


def generation_settings(
    config: Mapping[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    generation = config.get(
        "generation",
        {},
    )

    if not isinstance(
        generation,
        Mapping,
    ):
        generation = {}

    return {
        "max_tokens": (
            args.max_tokens
            if args.max_tokens is not None
            else int(
                generation.get(
                    "max_tokens",
                    4096,
                )
            )
        ),
        "temperature": (
            args.temperature
            if args.temperature is not None
            else float(
                generation.get(
                    "temperature",
                    0.6,
                )
            )
        ),
        "top_p": float(
            generation.get(
                "top_p",
                0.8,
            )
        ),
        "top_k": int(
            generation.get(
                "top_k",
                0,
            )
        ),
        "min_p": float(
            generation.get(
                "min_p",
                0,
            )
        ),
        "presence_penalty": float(
            generation.get(
                "presence_penalty",
                0.0,
            )
        ),
        "banned_tokens": list(
            generation.get(
                "banned_tokens",
                [],
            )
            or []
        ),
    }


def script_prompts(
    config: Mapping[str, Any],
) -> tuple[str, str]:
    prompts = config.get(
        "prompts",
        {},
    )

    if not isinstance(prompts, Mapping):
        prompts = {}

    return (
        prompts.get("system_prompt")
        or DEFAULT_SYSTEM_PROMPT,
        prompts.get("user_prompt")
        or DEFAULT_USER_PROMPT,
    )


def review_prompts(
    config: Mapping[str, Any],
) -> tuple[str, str]:
    prompts = config.get(
        "prompts",
        {},
    )

    if not isinstance(prompts, Mapping):
        prompts = {}

    return (
        prompts.get(
            "review_system_prompt"
        )
        or REVIEW_SYSTEM_PROMPT,
        prompts.get(
            "review_user_prompt"
        )
        or REVIEW_USER_PROMPT,
    )


def run_script_case(
    case: Mapping[str, Any],
    expected: Mapping[str, Any],
    client: Any,
    *,
    config: Mapping[str, Any],
    args: argparse.Namespace,
    telemetry_path: Path,
) -> dict[str, Any]:
    source = (
        BENCHMARK_DIR
        / str(case["input"])
    ).read_text(encoding="utf-8")

    reference_entries = list(
        expected["reference_entries"]
    )

    settings = generation_settings(
        config,
        args,
    )
    system_prompt, user_prompt = (
        script_prompts(config)
    )

    if "chunk_size" in case:
        chunks = (
            generate_script
            .split_into_chunks(
                source,
                max_size=int(
                    case["chunk_size"]
                ),
            )
        )
    else:
        chunks = [source]

    all_entries: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    started = time.perf_counter()

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):
        reset_telemetry(
            telemetry_path
        )
        unit_started = time.perf_counter()

        entries = (
            generate_script.process_chunk(
                client,
                str(
                    config["llm"][
                        "model_name"
                    ]
                ),
                chunk,
                index,
                len(chunks),
                previous_entries=(
                    all_entries or None
                ),
                max_retries=args.max_retries,
                system_prompt=system_prompt,
                user_prompt_template=(
                    user_prompt
                ),
                **settings,
            )
        )

        unit_elapsed = (
            time.perf_counter()
            - unit_started
        )
        pipeline = latest_pipeline(
            telemetry_path
        )

        units.append(
            {
                "unit_index": index,
                "unit_total": len(chunks),
                "elapsed_seconds": (
                    unit_elapsed
                ),
                "entry_count": len(
                    entries or []
                ),
                "pipeline": pipeline,
            }
        )

        if not entries:
            break

        all_entries.extend(entries)

    elapsed = time.perf_counter() - started
    audit = audit_script_chunk(
        source,
        all_entries,
    )
    quality = aligned_script_metrics(
        reference_entries,
        all_entries,
        expected,
    )

    return {
        "kind": "script",
        "status": (
            "success"
            if all_entries
            else "error"
        ),
        "schema_success": (
            entries_have_valid_shape(
                all_entries
            )
        ),
        "entry_count": len(all_entries),
        "chunk_count": len(chunks),
        "units": units,
        "outer_retry_units": sum(
            (
                unit.get(
                    "pipeline"
                )
                or {}
            ).get(
                "outer_retry_used"
            )
            is True
            for unit in units
        ),
        "audit_passed": audit.passed,
        "audit": audit.to_dict(),
        "quality": quality,
        "elapsed_seconds": elapsed,
        "output_entries": all_entries,
    }


def contextual_review_text(
    payload: Mapping[str, Any],
) -> str:
    lines = [
        (
            "IMPORTANT: PREVIOUS and NEXT entries "
            "are context only. Review and return "
            "ONLY the TARGET BATCH entries."
        )
    ]

    before = payload.get(
        "before",
        [],
    )
    after = payload.get(
        "after",
        [],
    )

    if before:
        lines.append(
            "\n--- PREVIOUS ENTRIES "
            "(Context Only) ---"
        )
        lines.extend(
            json.dumps(
                entry,
                ensure_ascii=False,
            )
            for entry in before
        )

    if after:
        lines.append(
            "\n--- NEXT ENTRIES "
            "(Context Only) ---"
        )
        lines.extend(
            json.dumps(
                entry,
                ensure_ascii=False,
            )
            for entry in after
        )

    return "\n".join(lines)


def context_leakage(
    payload: Mapping[str, Any],
    output: list[dict[str, Any]],
) -> bool:
    output_stream = normalize_review_text(
        combined_text(output)
    )

    for key in (
        "before",
        "after",
    ):
        neighbors = payload.get(
            key,
            [],
        )

        if not isinstance(neighbors, list):
            continue

        for entry in neighbors:
            text = normalize_review_text(
                str(
                    entry.get(
                        "text",
                        "",
                    )
                )
            )

            if text and text in output_stream:
                return True

    return False


def run_review_case(
    case: Mapping[str, Any],
    expected: Mapping[str, Any],
    client: Any,
    *,
    config: Mapping[str, Any],
    args: argparse.Namespace,
    telemetry_path: Path,
) -> dict[str, Any]:
    payload = load_json(
        BENCHMARK_DIR
        / str(case["input"])
    )
    target = list(
        payload["target"]
    )
    settings = generation_settings(
        config,
        args,
    )
    system_prompt, user_prompt = (
        review_prompts(config)
    )

    source_context = (
        contextual_review_text(
            payload
        )
        if case.get("mode")
        == "contextual"
        else None
    )

    reset_telemetry(
        telemetry_path
    )
    started = time.perf_counter()

    corrected = review_script.review_batch(
        client,
        str(
            config["llm"]["model_name"]
        ),
        target,
        1,
        1,
        previous_tail=None,
        source_context=source_context,
        max_retries=args.max_retries,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt,
        max_tokens=max(
            settings["max_tokens"],
            8000,
        ),
        temperature=settings[
            "temperature"
        ],
        top_p=settings["top_p"],
        top_k=settings["top_k"],
        min_p=settings["min_p"],
        presence_penalty=settings[
            "presence_penalty"
        ],
        banned_tokens=settings[
            "banned_tokens"
        ],
    )

    elapsed = time.perf_counter() - started
    output = corrected or target
    retained_original = not bool(
        corrected
    )
    audit = audit_review_batch(
        target,
        output,
    )
    pipeline = latest_pipeline(
        telemetry_path
    )
    leaked = context_leakage(
        payload,
        output,
    )

    return {
        "kind": "review",
        "mode": case.get("mode"),
        "status": "success",
        "schema_success": (
            entries_have_valid_shape(
                output
            )
        ),
        "entry_count": len(output),
        "safe_original_retained": (
            retained_original
        ),
        "context_leakage": leaked,
        "units": [
            {
                "unit_index": 1,
                "unit_total": 1,
                "elapsed_seconds": elapsed,
                "entry_count": len(output),
                "pipeline": pipeline,
            }
        ],
        "outer_retry_units": (
            1
            if (
                pipeline
                and pipeline.get(
                    "outer_retry_used"
                )
                is True
            )
            else 0
        ),
        "audit_passed": (
            audit.passed
            and not leaked
        ),
        "audit": audit.to_dict(),
        "quality": (
            review_quality_metrics(
                target,
                output,
            )
        ),
        "elapsed_seconds": elapsed,
        "output_entries": output,
    }


def average(
    values: Iterable[Any],
) -> float | None:
    numeric = [
        float(value)
        for value in values
        if isinstance(
            value,
            (int, float),
        )
    ]

    return (
        sum(numeric) / len(numeric)
        if numeric
        else None
    )


def aggregate_model_results(
    case_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    completed = [
        result
        for result in case_runs
        if result.get("status")
        == "success"
    ]
    script_runs = [
        result
        for result in case_runs
        if result.get("kind")
        == "script"
    ]
    review_runs = [
        result
        for result in case_runs
        if result.get("kind")
        == "review"
    ]

    total_requests = sum(
        result.get(
            "requests",
            {},
        ).get(
            "request_count",
            0,
        )
        for result in case_runs
    )
    successful_requests = sum(
        result.get(
            "requests",
            {},
        ).get(
            "successful_request_count",
            0,
        )
        for result in case_runs
    )
    corrective_requests = sum(
        result.get(
            "requests",
            {},
        ).get(
            "corrective_retry_requests",
            0,
        )
        for result in case_runs
    )

    prompt_tokens = sum(
        result.get(
            "requests",
            {},
        ).get(
            "prompt_tokens",
            0,
        )
        for result in case_runs
    )
    prompt_seconds = sum(
        result.get(
            "requests",
            {},
        ).get(
            "prompt_seconds_estimate",
            0.0,
        )
        for result in case_runs
    )
    output_tokens = sum(
        result.get(
            "requests",
            {},
        ).get(
            "output_tokens",
            0,
        )
        for result in case_runs
    )
    output_seconds = sum(
        result.get(
            "requests",
            {},
        ).get(
            "output_seconds_estimate",
            0.0,
        )
        for result in case_runs
    )

    total_units = sum(
        len(
            result.get(
                "units",
                [],
            )
        )
        for result in case_runs
    )
    outer_retry_units = sum(
        result.get(
            "outer_retry_units",
            0,
        )
        for result in case_runs
    )

    return {
        "case_run_count": len(
            case_runs
        ),
        "completed_case_run_count": len(
            completed
        ),
        "schema_success_rate": average(
            1.0
            if result.get(
                "schema_success"
            )
            else 0.0
            for result in case_runs
        ),
        "internal_corrective_retry_rate": (
            corrective_requests
            / successful_requests
            if successful_requests
            else None
        ),
        "outer_retry_rate": (
            outer_retry_units
            / total_units
            if total_units
            else None
        ),
        "script_audit_pass_rate": average(
            1.0
            if result.get(
                "audit_passed"
            )
            else 0.0
            for result in script_runs
        ),
        "review_audit_pass_rate": average(
            1.0
            if result.get(
                "audit_passed"
            )
            else 0.0
            for result in review_runs
        ),
        "average_missing_word_count": average(
            result.get(
                "quality",
                {},
            ).get(
                "missing_word_count"
            )
            for result in case_runs
        ),
        "average_punctuation_accuracy": average(
            result.get(
                "quality",
                {},
            ).get(
                "punctuation_accuracy"
            )
            for result in case_runs
        ),
        "average_speaker_accuracy": average(
            result.get(
                "quality",
                {},
            ).get(
                "speaker_accuracy"
            )
            for result in script_runs
        ),
        "average_narrator_dialogue_accuracy": average(
            result.get(
                "quality",
                {},
            ).get(
                "narrator_dialogue_accuracy"
            )
            for result in script_runs
        ),
        "average_alias_consistency": average(
            result.get(
                "quality",
                {},
            ).get(
                "alias_consistency"
            )
            for result in script_runs
        ),
        "prompt_tokens_per_second": (
            prompt_tokens
            / prompt_seconds
            if prompt_seconds > 0
            else None
        ),
        "output_tokens_per_second": (
            output_tokens
            / output_seconds
            if output_seconds > 0
            else None
        ),
        "average_case_elapsed_seconds": average(
            result.get(
                "elapsed_seconds"
            )
            for result in case_runs
        ),
        "total_elapsed_seconds": sum(
            float(
                result.get(
                    "elapsed_seconds",
                    0.0,
                )
            )
            for result in case_runs
        ),
        "request_count": total_requests,
        "successful_request_count": (
            successful_requests
        ),
        "corrective_retry_requests": (
            corrective_requests
        ),
        "unit_count": total_units,
        "outer_retry_units": (
            outer_retry_units
        ),
    }


def validate_references(
    cases: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []

    for case in cases:
        expected = load_json(
            BENCHMARK_DIR
            / str(case["expected"])
        )

        if case["kind"] == "script":
            if not expected.get(
                "audit_reference",
                True,
            ):
                continue

            source = (
                BENCHMARK_DIR
                / str(case["input"])
            ).read_text(encoding="utf-8")

            result = audit_script_chunk(
                source,
                expected[
                    "reference_entries"
                ],
            )
        else:
            payload = load_json(
                BENCHMARK_DIR
                / str(case["input"])
            )
            target = payload["target"]
            result = audit_review_batch(
                target,
                target,
            )

        if not result.passed:
            failures.append(
                f"{case['id']}: "
                + json.dumps(
                    result.to_dict(),
                    ensure_ascii=False,
                )
            )

    return failures


def selected_cases(
    cases: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    selected = cases

    if args.kind != "all":
        selected = [
            case
            for case in selected
            if case["kind"] == args.kind
        ]

    if args.case:
        requested = set(args.case)
        known = {
            case["id"]
            for case in cases
        }
        unknown = requested - known

        if unknown:
            raise ValueError(
                "Unknown benchmark cases: "
                + ", ".join(
                    sorted(unknown)
                )
            )

        selected = [
            case
            for case in selected
            if case["id"] in requested
        ]

    if not selected:
        raise ValueError(
            "No benchmark cases selected"
        )

    return selected


def configured_models(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> list[str]:
    if args.model:
        return list(
            dict.fromkeys(args.model)
        )

    llm = normalized_llm_section(
        config.get("llm")
        if isinstance(config, Mapping)
        else None
    )

    return [
        str(
            llm.get(
                "model_name",
                DEFAULT_MODEL_NAME,
            )
        )
    ]


def build_model_config(
    base_config: Mapping[str, Any],
    model: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    config = copy.deepcopy(
        dict(base_config)
    )
    llm = normalized_llm_section(
        config.get("llm")
    )
    llm["model_name"] = model

    if args.backend is not None:
        llm["backend"] = args.backend

    config["llm"] = llm

    return config


def run_model(
    model: str,
    cases: list[dict[str, Any]],
    *,
    base_config: Mapping[str, Any],
    args: argparse.Namespace,
    telemetry_path: Path,
) -> dict[str, Any]:
    config = build_model_config(
        base_config,
        model,
        args,
    )
    probe = build_runtime_client(config)
    initial_status = probe.status()
    initially_loaded = initial_status.get(
        "loaded"
    )

    script_cases = [
        case
        for case in cases
        if case["kind"] == "script"
    ]
    review_cases = [
        case
        for case in cases
        if case["kind"] == "review"
    ]

    script_runtime = None
    script_client = None
    review_runtime = None
    review_client = None
    preload_result = None
    restoration = None
    case_runs: list[dict[str, Any]] = []

    started = time.perf_counter()

    try:
        if (
            not args.no_preload
            and probe.native_root is not None
        ):
            success, message = (
                probe.preload()
            )
            preload_result = {
                "success": success,
                "message": message,
            }

            if not success:
                raise RuntimeError(message)

        if script_cases:
            (
                script_runtime,
                script_client,
            ) = (
                generate_script
                ._build_script_llm_client(
                    config
                )
            )

        if review_cases:
            llm = config["llm"]
            (
                review_client,
                review_runtime,
            ) = (
                review_script
                ._create_review_client(
                    llm["base_url"],
                    llm["api_key"],
                    llm["model_name"],
                    llm,
                )
            )

        for run_index in range(
            1,
            args.runs + 1,
        ):
            for case_index, case in enumerate(
                cases,
                start=1,
            ):
                case_id = str(case["id"])
                expected = load_json(
                    BENCHMARK_DIR
                    / str(case["expected"])
                )
                seed = (
                    args.seed
                    + (
                        (run_index - 1)
                        * 1000
                    )
                    + case_index
                )
                runtime = (
                    script_runtime
                    if case["kind"]
                    == "script"
                    else review_runtime
                )
                client = (
                    script_client
                    if case["kind"]
                    == "script"
                    else review_client
                )

                print(
                    f"[{model}] run "
                    f"{run_index}/{args.runs} "
                    f"case {case_id}"
                )

                case_started = (
                    time.perf_counter()
                )
                requests: list[dict[str, Any]] = []

                try:
                    with capture_runtime_requests(
                        runtime,
                        seed=seed,
                    ) as requests:
                        if case["kind"] == "script":
                            result = run_script_case(
                                case,
                                expected,
                                client,
                                config=config,
                                args=args,
                                telemetry_path=(
                                    telemetry_path
                                ),
                            )
                        else:
                            result = run_review_case(
                                case,
                                expected,
                                client,
                                config=config,
                                args=args,
                                telemetry_path=(
                                    telemetry_path
                                ),
                            )

                    result["requests"] = (
                        summarize_requests(
                            requests
                        )
                    )
                except Exception as exc:
                    result = {
                        "kind": case["kind"],
                        "status": "error",
                        "schema_success": False,
                        "audit_passed": False,
                        "elapsed_seconds": (
                            time.perf_counter()
                            - case_started
                        ),
                        "error": (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                        "requests": (
                            summarize_requests(
                                requests
                            )
                        ),
                        "units": [],
                        "outer_retry_units": 0,
                        "quality": {},
                        "output_entries": [],
                    }

                    if args.fail_fast:
                        raise

                result.update(
                    {
                        "case_id": case_id,
                        "title": case.get(
                            "title",
                            case_id,
                        ),
                        "run": run_index,
                        "seed": seed,
                    }
                )
                case_runs.append(result)

    finally:
        if (
            initially_loaded is False
            and probe.native_root is not None
        ):
            success, message = (
                probe.unload()
            )
            restoration = {
                "requested_state": (
                    "unloaded"
                ),
                "success": success,
                "message": message,
            }
        elif initially_loaded is True:
            restoration = {
                "requested_state": (
                    "loaded"
                ),
                "success": True,
                "message": (
                    "Initial loaded state "
                    "left unchanged"
                ),
            }
        else:
            restoration = {
                "requested_state": (
                    "unknown"
                ),
                "success": True,
                "message": (
                    "Initial state was unknown; "
                    "no restoration action taken"
                ),
            }

    elapsed = time.perf_counter() - started

    return {
        "model_name": model,
        "runtime": {
            "backend": probe.backend,
            "backend_preference": (
                probe.backend_preference
            ),
            "base_url": probe.base_url,
            "context_length": (
                probe.context_length
            ),
            "keep_alive": probe.keep_alive,
            "thinking": probe.thinking,
            "structured_output": (
                probe.structured_output
            ),
            "corrective_retry": (
                probe.corrective_retry
            ),
            "timeout": probe.timeout,
            "initial_status": initial_status,
            "preload": preload_result,
            "restoration": restoration,
        },
        "settings": generation_settings(
            config,
            args,
        ),
        "case_runs": case_runs,
        "summary": (
            aggregate_model_results(
                case_runs
            )
        ),
        "elapsed_seconds": elapsed,
    }


def result_path(
    models: list[str],
    args: argparse.Namespace,
) -> Path:
    if args.output:
        return Path(args.output).expanduser()

    stamp = dt.datetime.now(
        dt.timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    model_part = "__".join(
        safe_filename(model)
        for model in models
    )

    return (
        DEFAULT_RESULTS
        / f"{stamp}_{model_part}.json"
    )


def print_summary(
    result: Mapping[str, Any],
    output_path: Path,
) -> None:
    print()
    print("=" * 72)
    print("ALEXANDRIA BENCHMARK SUMMARY")
    print("=" * 72)

    for model in result["models"]:
        summary = model["summary"]

        print()
        print(model["model_name"])
        print(
            "  schema success: "
            f"{summary['schema_success_rate']}"
        )
        print(
            "  script audit pass: "
            f"{summary['script_audit_pass_rate']}"
        )
        print(
            "  review audit pass: "
            f"{summary['review_audit_pass_rate']}"
        )
        print(
            "  internal corrective retry: "
            f"{summary['internal_corrective_retry_rate']}"
        )
        print(
            "  outer retry: "
            f"{summary['outer_retry_rate']}"
        )
        print(
            "  prompt speed: "
            f"{summary['prompt_tokens_per_second']}"
        )
        print(
            "  output speed: "
            f"{summary['output_tokens_per_second']}"
        )
        print(
            "  total elapsed: "
            f"{summary['total_elapsed_seconds']:.2f}s"
        )

    print()
    print(f"Results: {output_path}")


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Alexandria's reproducible "
            "production-path benchmark suite."
        )
    )

    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
    )
    parser.add_argument(
        "--model",
        action="append",
        help=(
            "Model name. Repeat for multiple "
            "models."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=(
            "auto",
            "ollama",
            "openai",
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
    )
    parser.add_argument(
        "--case",
        action="append",
        help=(
            "Case ID. Repeat to select "
            "multiple cases."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=(
            "all",
            "script",
            "review",
        ),
        default="all",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1729,
    )
    parser.add_argument(
        "--temperature",
        type=float,
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--output",
    )
    parser.add_argument(
        "--no-preload",
        action="store_true",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
    )

    args = parser.parse_args(argv)

    if args.runs is not None and args.runs < 1:
        parser.error(
            "--runs must be at least 1"
        )

    if args.max_retries < 0:
        parser.error(
            "--max-retries cannot be negative"
        )

    if (
        args.max_tokens is not None
        and args.max_tokens < 1
    ):
        parser.error(
            "--max-tokens must be positive"
        )

    return args


def main(
    argv: list[str] | None = None,
) -> int:
    args = parse_args(argv)
    manifest_path = Path(
        args.manifest
    ).expanduser()
    manifest = load_json(
        manifest_path
    )

    if not isinstance(manifest, Mapping):
        raise ValueError(
            "Benchmark manifest root "
            "is not an object"
        )

    cases = validate_manifest(
        manifest,
        benchmark_dir=manifest_path.parent,
    )

    if args.list_cases:
        for case in cases:
            print(
                f"{case['id']}\t"
                f"{case['kind']}\t"
                f"{case.get('title', '')}"
            )

        return 0

    cases = selected_cases(
        cases,
        args,
    )

    failures = validate_references(
        cases
    )

    if failures:
        print(
            "Benchmark corpus validation failed:",
            file=sys.stderr,
        )

        for failure in failures:
            print(
                f"- {failure}",
                file=sys.stderr,
            )

        return 1

    if args.validate_only:
        print(
            "Benchmark corpus validation: PASS"
        )
        print(
            f"Selected cases: {len(cases)}"
        )
        return 0

    config_path = Path(
        args.config
    ).expanduser()
    config = load_config(
        config_path
    )
    models = configured_models(
        args,
        config,
    )

    args.runs = (
        args.runs
        if args.runs is not None
        else int(
            manifest.get(
                "required_runs_per_model",
                3,
            )
        )
    )

    output_path = result_path(
        models,
        args,
    ).resolve()

    previous_telemetry_path = os.environ.get(
        "ALEXANDRIA_LLM_TELEMETRY_PATH"
    )

    started = time.perf_counter()

    try:
        with tempfile.TemporaryDirectory(
            prefix=(
                "alexandria-benchmark-telemetry-"
            )
        ) as temp:
            telemetry_path = (
                Path(temp)
                / "llm_runtime.json"
            )

            os.environ[
                "ALEXANDRIA_LLM_TELEMETRY_PATH"
            ] = str(telemetry_path)

            model_results = [
                run_model(
                    model,
                    cases,
                    base_config=config,
                    args=args,
                    telemetry_path=(
                        telemetry_path
                    ),
                )
                for model in models
            ]
    finally:
        if previous_telemetry_path is None:
            os.environ.pop(
                "ALEXANDRIA_LLM_TELEMETRY_PATH",
                None,
            )
        else:
            os.environ[
                "ALEXANDRIA_LLM_TELEMETRY_PATH"
            ] = previous_telemetry_path

    result = {
        "schema_version": 1,
        "suite": manifest.get(
            "suite"
        ),
        "created_at": (
            dt.datetime.now(
                dt.timezone.utc
            ).isoformat()
        ),
        "git_head": git_head(),
        "manifest_path": str(
            manifest_path.resolve()
        ),
        "manifest_sha256": sha256_file(
            manifest_path
        ),
        "config_path": str(
            config_path.resolve()
        ),
        "runs_per_case": args.runs,
        "base_seed": args.seed,
        "selected_cases": [
            case["id"]
            for case in cases
        ],
        "models": model_results,
        "elapsed_seconds": (
            time.perf_counter()
            - started
        ),
    }

    atomic_json_write(
        output_path,
        result,
    )
    print_summary(
        result,
        output_path,
    )

    has_errors = any(
        case_run.get("status")
        != "success"
        for model in model_results
        for case_run in model[
            "case_runs"
        ]
    )

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
