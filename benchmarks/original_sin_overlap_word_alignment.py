#!/usr/bin/env python3
"""Pure helpers for exact word-level Original Sin adaptation alignment."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


class WordAlignmentError(RuntimeError):
    """Raised when an expected line cannot be located exactly in timed words."""


def normalized_words(text: str) -> list[str]:
    raw_tokens = re.findall(
        r"[a-z0-9']+",
        str(text or "").casefold().replace("’", "'"),
    )
    return [token.strip("'") for token in raw_tokens if token.strip("'")]


def word_error_rate(expected: str, observed: str) -> float:
    left = normalized_words(expected)
    right = normalized_words(observed)
    previous = list(range(len(right) + 1))
    for row, left_word in enumerate(left, 1):
        current = [row]
        for column, right_word in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_word != right_word),
                )
            )
        previous = current
    return previous[-1] / max(1, len(left))


def _normalized_aliases(
    word_aliases: Mapping[str, Sequence[str]] | None,
) -> dict[str, set[str]]:
    normalized: dict[str, set[str]] = {}
    for raw_expected, raw_aliases in (word_aliases or {}).items():
        expected_tokens = normalized_words(raw_expected)
        if len(expected_tokens) != 1:
            raise WordAlignmentError(
                f"Alignment alias key must normalize to one word: {raw_expected!r}"
            )
        aliases: set[str] = set()
        for raw_alias in raw_aliases:
            alias_tokens = normalized_words(raw_alias)
            if len(alias_tokens) != 1:
                raise WordAlignmentError(
                    f"Alignment alias must normalize to one word: {raw_alias!r}"
                )
            aliases.add(alias_tokens[0])
        normalized[expected_tokens[0]] = aliases
    return normalized


def _words_equivalent(expected: str, observed: str, aliases: dict[str, set[str]]) -> bool:
    return expected == observed or observed in aliases.get(expected, set())


def alias_aware_word_error_rate(
    expected: str,
    observed: str,
    word_aliases: Mapping[str, Sequence[str]] | None = None,
) -> float:
    left = normalized_words(expected)
    right = normalized_words(observed)
    aliases = _normalized_aliases(word_aliases)
    previous = list(range(len(right) + 1))
    for row, left_word in enumerate(left, 1):
        current = [row]
        for column, right_word in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1]
                    + (not _words_equivalent(left_word, right_word, aliases)),
                )
            )
        previous = current
    return previous[-1] / max(1, len(left))


def exact_transcript_check(expected: str, observed: str) -> dict[str, Any]:
    left = normalized_words(expected)
    right = normalized_words(observed)
    return {
        "automatic_transcript": observed,
        "word_error_rate": word_error_rate(expected, observed),
        "first_word_present": bool(left and right and left[0] == right[0]),
        "last_word_present": bool(left and right and left[-1] == right[-1]),
    }


def accepted_transcript_check(
    accepted_transcripts: list[str],
    observed: str,
) -> dict[str, Any]:
    if not accepted_transcripts:
        raise WordAlignmentError("At least one accepted transcript is required")
    checks = []
    for expected in accepted_transcripts:
        check = exact_transcript_check(expected, observed)
        check["accepted_transcript"] = expected
        checks.append(check)
    checks.sort(
        key=lambda check: (
            check["word_error_rate"],
            not check["first_word_present"],
            not check["last_word_present"],
        )
    )
    return checks[0]


def transcript_check_eligible(check: dict[str, Any]) -> bool:
    return (
        check["word_error_rate"] == 0.0
        and check["first_word_present"] is True
        and check["last_word_present"] is True
    )


@dataclass(frozen=True)
class TimedWord:
    token: str
    start: float
    end: float
    probability: float | None


def flatten_timed_words(segments: Iterable[dict[str, Any]]) -> list[TimedWord]:
    flattened: list[TimedWord] = []
    for segment in segments:
        for word in segment.get("words") or []:
            tokens = normalized_words(str(word.get("word") or ""))
            for token in tokens:
                probability = word.get("probability")
                flattened.append(
                    TimedWord(
                        token=token,
                        start=float(word["start"]),
                        end=float(word["end"]),
                        probability=float(probability) if probability is not None else None,
                    )
                )
    return flattened


def locate_exact_span(expected: str, timed_words: list[TimedWord]) -> tuple[int, int]:
    match = locate_declared_span([expected], timed_words)
    return int(match["first_index"]), int(match["last_index"])


def locate_declared_span(
    expected_transcripts: Sequence[str],
    timed_words: list[TimedWord],
    word_aliases: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    aliases = _normalized_aliases(word_aliases)
    observed_tokens = [word.token for word in timed_words]
    matches: list[tuple[int, int, int, str, list[dict[str, Any]]]] = []
    for transcript_index, expected in enumerate(expected_transcripts):
        expected_tokens = normalized_words(expected)
        if not expected_tokens:
            raise WordAlignmentError("Expected transcript is empty")
        width = len(expected_tokens)
        for start in range(0, len(observed_tokens) - width + 1):
            observed_span = observed_tokens[start : start + width]
            if not all(
                _words_equivalent(expected_word, observed_word, aliases)
                for expected_word, observed_word in zip(expected_tokens, observed_span)
            ):
                continue
            aliases_used = [
                {
                    "position": position,
                    "expected": expected_word,
                    "observed": observed_word,
                }
                for position, (expected_word, observed_word) in enumerate(
                    zip(expected_tokens, observed_span)
                )
                if expected_word != observed_word
            ]
            matches.append(
                (len(aliases_used), transcript_index, start, expected, aliases_used)
            )
    if not matches:
        raise WordAlignmentError(
            "No declared transcript was found as one contiguous timed-word span: "
            f"expected={[normalized_words(value) for value in expected_transcripts]!r}, "
            f"observed={observed_tokens!r}"
        )
    alias_count, transcript_index, start, matched, aliases_used = min(matches)
    width = len(normalized_words(matched))
    return {
        "matched_transcript": matched,
        "matched_transcript_index": transcript_index,
        "first_index": start,
        "last_index": start + width - 1,
        "word_aliases_used": aliases_used,
        "word_alias_count": alias_count,
    }


def exact_alignment_record(
    expected: str,
    whisper_result: dict[str, Any],
    *,
    accepted_transcripts: Sequence[str] = (),
    word_aliases: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    timed_words = flatten_timed_words(whisper_result.get("segments") or [])
    transcripts = [expected, *accepted_transcripts]
    match = locate_declared_span(transcripts, timed_words, word_aliases)
    first_index = int(match["first_index"])
    last_index = int(match["last_index"])
    selected = timed_words[first_index : last_index + 1]
    probabilities = [word.probability for word in selected if word.probability is not None]
    return {
        "expected_transcript": expected,
        "accepted_transcripts": list(accepted_transcripts),
        "matched_alignment_transcript": match["matched_transcript"],
        "matched_accepted_transcript": int(match["matched_transcript_index"]) > 0,
        "alignment_word_aliases_used": match["word_aliases_used"],
        "observed_transcript": str(whisper_result.get("text") or "").strip(),
        "expected_words": normalized_words(str(match["matched_transcript"])),
        "word_start_seconds": selected[0].start,
        "word_end_seconds": selected[-1].end,
        "first_word": selected[0].token,
        "last_word": selected[-1].token,
        "minimum_word_probability": min(probabilities) if probabilities else None,
        "mean_word_probability": (
            sum(probabilities) / len(probabilities) if probabilities else None
        ),
    }


def transcript_comparison(
    expected_transcripts: Sequence[str],
    observed: str,
    word_aliases: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    if not expected_transcripts:
        raise WordAlignmentError("At least one expected transcript is required")
    aliases = _normalized_aliases(word_aliases)
    observed_tokens = normalized_words(observed)
    ranked: list[tuple[float, int, str]] = []
    for index, expected in enumerate(expected_transcripts):
        ranked.append(
            (alias_aware_word_error_rate(expected, observed, word_aliases), index, expected)
        )
    error_rate, matched_index, matched = min(ranked)
    expected_tokens = normalized_words(matched)
    aliases_used = [
        {"position": index, "expected": left, "observed": right}
        for index, (left, right) in enumerate(zip(expected_tokens, observed_tokens))
        if left != right and _words_equivalent(left, right, aliases)
    ]
    return {
        "matched_expected_transcript": matched,
        "matched_accepted_transcript": matched_index > 0,
        "word_error_rate": error_rate,
        "first_word_present": bool(
            expected_tokens
            and observed_tokens
            and _words_equivalent(expected_tokens[0], observed_tokens[0], aliases)
        ),
        "last_word_present": bool(
            expected_tokens
            and observed_tokens
            and _words_equivalent(expected_tokens[-1], observed_tokens[-1], aliases)
        ),
        "transcript_word_aliases_used": aliases_used,
    }
