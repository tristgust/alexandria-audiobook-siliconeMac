from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any


NARRATOR_LABELS = frozenset(
    {
        "NARRATOR",
        "NARRATION",
        "NARRATIVE",
    }
)

ATTRIBUTION_VERBS = (
    "said",
    "asked",
    "replied",
    "answered",
    "continued",
    "added",
    "called",
    "cried",
    "whispered",
    "murmured",
    "muttered",
    "shouted",
    "yelled",
    "observed",
    "remarked",
    "declared",
    "announced",
    "insisted",
    "suggested",
    "explained",
    "admitted",
    "agreed",
    "protested",
    "demanded",
    "warned",
)

ATTRIBUTION_VERB_PATTERN = (
    "(?:"
    + "|".join(
        re.escape(verb)
        for verb in ATTRIBUTION_VERBS
    )
    + ")"
)

ATTRIBUTION_SUBJECT_PATTERN = re.compile(
    rf"\b(he|she|they|it)\b"
    rf"(?=\s+{ATTRIBUTION_VERB_PATTERN}\b)",
    flags=re.IGNORECASE,
)

ANY_ATTRIBUTION_PATTERN = re.compile(
    rf"\b{ATTRIBUTION_VERB_PATTERN}\b",
    flags=re.IGNORECASE,
)

PROPER_NAME_PATTERN = (
    r"(?:"
    r"(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]*)"
    r"(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]*){0,3}"
    r"|"
    r"(?:the|The)\s+"
    r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]*"
    r")"
)

QUOTE_PAIRS = {
    '"': '"',
    "“": "”",
    "‘": "’",
}

ROMAN_CHAPTERS = {
    "I": "one",
    "II": "two",
    "III": "three",
    "IV": "four",
    "V": "five",
    "VI": "six",
    "VII": "seven",
    "VIII": "eight",
    "IX": "nine",
    "X": "ten",
    "XI": "eleven",
    "XII": "twelve",
    "XIII": "thirteen",
    "XIV": "fourteen",
    "XV": "fifteen",
    "XVI": "sixteen",
    "XVII": "seventeen",
    "XVIII": "eighteen",
    "XIX": "nineteen",
    "XX": "twenty",
}

ORDINALS = {
    "1st": "first",
    "2nd": "second",
    "3rd": "third",
    "4th": "fourth",
    "5th": "fifth",
    "6th": "sixth",
    "7th": "seventh",
    "8th": "eighth",
    "9th": "ninth",
    "10th": "tenth",
    "11th": "eleventh",
    "12th": "twelfth",
    "13th": "thirteenth",
    "14th": "fourteenth",
    "15th": "fifteenth",
    "16th": "sixteenth",
    "17th": "seventeenth",
    "18th": "eighteenth",
    "19th": "nineteenth",
    "20th": "twentieth",
}


@dataclass(frozen=True)
class SourceSegment:
    kind: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class OutputSegment:
    kind: str
    speaker: str
    text: str
    entry_index: int


@dataclass(frozen=True)
class SegmentMatch:
    source_index: int
    output_start: int
    output_end: int
    mode: str


@dataclass(frozen=True)
class AuditIssue:
    code: str
    severity: str
    message: str
    source_text: str = ""
    output_text: str = ""
    source_index: int | None = None
    output_indices: tuple[int, ...] = ()


@dataclass
class ScriptAuditResult:
    source_segments: list[SourceSegment]
    output_segments: list[OutputSegment]
    matches: list[SegmentMatch]
    issues: list[AuditIssue]
    metrics: dict[str, Any]

    @property
    def blocking_issues(self) -> list[AuditIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == "blocking"
        ]

    @property
    def warnings(self) -> list[AuditIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == "warning"
        ]

    @property
    def passed(self) -> bool:
        return not self.blocking_issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blocking_count": len(
                self.blocking_issues
            ),
            "warning_count": len(
                self.warnings
            ),
            "metrics": dict(self.metrics),
            "issues": [
                asdict(issue)
                for issue in self.issues
            ],
            "matches": [
                asdict(match)
                for match in self.matches
            ],
        }


class UnbalancedDialogueQuotesError(ValueError):
    pass


def _is_escaped(
    text: str,
    index: int,
) -> bool:
    backslashes = 0
    cursor = index - 1

    while (
        cursor >= 0
        and text[cursor] == "\\"
    ):
        backslashes += 1
        cursor -= 1

    return backslashes % 2 == 1


def split_source_segments(
    source_text: str,
) -> list[SourceSegment]:
    text = source_text or ""
    segments: list[SourceSegment] = []

    buffer: list[str] = []
    buffer_start = 0
    current_kind = "narration"
    closing_quote: str | None = None

    def flush(end_index: int) -> None:
        nonlocal buffer
        nonlocal buffer_start

        value = "".join(buffer).strip()

        if value:
            segments.append(
                SourceSegment(
                    kind=current_kind,
                    text=value,
                    start=buffer_start,
                    end=end_index,
                )
            )

        buffer = []

    index = 0

    while index < len(text):
        character = text[index]

        if closing_quote is None:
            if (
                character in QUOTE_PAIRS
                and not _is_escaped(
                    text,
                    index,
                )
            ):
                flush(index)

                current_kind = "dialogue"
                closing_quote = (
                    QUOTE_PAIRS[character]
                )
                buffer_start = index + 1
                index += 1
                continue

        elif (
            character == closing_quote
            and not _is_escaped(
                text,
                index,
            )
        ):
            flush(index)

            current_kind = "narration"
            closing_quote = None
            buffer_start = index + 1
            index += 1
            continue

        buffer.append(character)
        index += 1

    if closing_quote is not None:
        raise UnbalancedDialogueQuotesError(
            "Source text contains an unclosed "
            "spoken-dialogue quotation."
        )

    flush(len(text))

    return segments


def build_output_segments(
    entries: list[dict[str, Any]],
) -> tuple[
    list[OutputSegment],
    list[AuditIssue],
]:
    segments: list[OutputSegment] = []
    issues: list[AuditIssue] = []

    if not isinstance(entries, list):
        return (
            [],
            [
                AuditIssue(
                    code="invalid_output_type",
                    severity="blocking",
                    message=(
                        "Script output is not a JSON array."
                    ),
                )
            ],
        )

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            issues.append(
                AuditIssue(
                    code="invalid_entry_type",
                    severity="blocking",
                    message=(
                        f"Entry {index} is not an object."
                    ),
                    output_indices=(index,),
                )
            )
            continue

        if set(entry) != {
            "speaker",
            "text",
            "instruct",
        }:
            issues.append(
                AuditIssue(
                    code="invalid_entry_fields",
                    severity="blocking",
                    message=(
                        f"Entry {index} does not contain "
                        "exactly speaker, text, and instruct."
                    ),
                    output_indices=(index,),
                )
            )
            continue

        speaker = entry["speaker"]
        text = entry["text"]
        instruct = entry["instruct"]

        if (
            not isinstance(speaker, str)
            or not speaker.strip()
        ):
            issues.append(
                AuditIssue(
                    code="invalid_speaker",
                    severity="blocking",
                    message=(
                        f"Entry {index} has an invalid speaker."
                    ),
                    output_indices=(index,),
                )
            )
            continue

        if (
            not isinstance(text, str)
            or not text.strip()
        ):
            issues.append(
                AuditIssue(
                    code="empty_text",
                    severity="blocking",
                    message=(
                        f"Entry {index} has empty text."
                    ),
                    output_indices=(index,),
                )
            )
            continue

        if not isinstance(instruct, str):
            issues.append(
                AuditIssue(
                    code="invalid_instruct",
                    severity="blocking",
                    message=(
                        f"Entry {index} has a non-string instruct."
                    ),
                    output_indices=(index,),
                )
            )
            continue

        kind = (
            "narration"
            if speaker.strip().upper()
            in NARRATOR_LABELS
            else "dialogue"
        )

        segments.append(
            OutputSegment(
                kind=kind,
                speaker=speaker.strip(),
                text=text.strip(),
                entry_index=index,
            )
        )

    return segments, issues


def _normalize_typography(
    text: str,
) -> str:
    value = unicodedata.normalize(
        "NFKC",
        text or "",
    )

    replacements = {
        "\u00a0": " ",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "…": "...",
    }

    for old, new in replacements.items():
        value = value.replace(
            old,
            new,
        )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    value = re.sub(
        r"\s+([,.;:!?])",
        r"\1",
        value,
    )

    return value.strip()


def _canonicalize_tts(
    text: str,
) -> str:
    value = _normalize_typography(text)

    # Period-bearing abbreviations cannot use a trailing \b
    # after the period. Use whitespace/end lookahead instead.
    substitutions = (
        (
            r"\bDr\.(?=\s|$)",
            "doctor",
        ),
        (
            r"\bDoctor\b",
            "doctor",
        ),
        (
            r"\bMr\.(?=\s|$)",
            "mister",
        ),
        (
            r"\bMister\b",
            "mister",
        ),
        (
            r"\bMrs\.(?=\s|$)",
            "missus",
        ),
        (
            r"\bMissus\b",
            "missus",
        ),
        (
            r"\bMs\.(?=\s|$)",
            "miss",
        ),
        (
            r"\bMiss\b",
            "miss",
        ),
    )

    for pattern, replacement in substitutions:
        value = re.sub(
            pattern,
            replacement,
            value,
            flags=re.IGNORECASE,
        )

    for roman, word in sorted(
        ROMAN_CHAPTERS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        value = re.sub(
            rf"\bChapter\s+{roman}\b",
            f"chapter {word}",
            value,
            flags=re.IGNORECASE,
        )

        value = re.sub(
            rf"\bChapter\s+{word}\b",
            f"chapter {word}",
            value,
            flags=re.IGNORECASE,
        )

    for ordinal, word in ORDINALS.items():
        value = re.sub(
            rf"\b{re.escape(ordinal)}\b",
            word,
            value,
            flags=re.IGNORECASE,
        )

        value = re.sub(
            rf"\b{word}\b",
            word,
            value,
            flags=re.IGNORECASE,
        )

    value = re.sub(
        r"\s*&\s*",
        " and ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    value = re.sub(
        r"\s+([,.;:!?])",
        r"\1",
        value,
    )

    return value.strip()


def _escape_flexible_whitespace(
    text: str,
) -> str:
    return re.escape(text).replace(
        r"\ ",
        r"\s+",
    )


def _allows_attribution_subject_resolution(
    source_text: str,
    output_text: str,
) -> bool:
    source = _canonicalize_tts(
        source_text
    )

    output = _canonicalize_tts(
        output_text
    )

    for match in (
        ATTRIBUTION_SUBJECT_PATTERN.finditer(
            source
        )
    ):
        prefix = source[:match.start()]
        suffix = source[match.end():]

        pattern = (
            "^"
            + _escape_flexible_whitespace(
                prefix
            )
            + PROPER_NAME_PATTERN
            + _escape_flexible_whitespace(
                suffix
            )
            + "$"
        )

        if re.fullmatch(
            pattern,
            output,
        ):
            return True

    return False


def segment_equivalence_mode(
    source_text: str,
    output_text: str,
    *,
    kind: str,
) -> str | None:
    if (
        _normalize_typography(source_text)
        == _normalize_typography(output_text)
    ):
        return "exact"

    if (
        _canonicalize_tts(source_text)
        == _canonicalize_tts(output_text)
    ):
        return "tts_conversion"

    if (
        kind == "narration"
        and _allows_attribution_subject_resolution(
            source_text,
            output_text,
        )
    ):
        return "attribution_subject_clarification"

    return None


def _join_output_text(
    segments: list[OutputSegment],
    start: int,
    end: int,
) -> str:
    return " ".join(
        segment.text
        for segment in segments[start:end]
    )


def align_segments(
    source_segments: list[SourceSegment],
    output_segments: list[OutputSegment],
) -> list[SegmentMatch] | None:
    @lru_cache(maxsize=None)
    def solve(
        source_index: int,
        output_index: int,
    ) -> tuple[SegmentMatch, ...] | None:
        if source_index == len(
            source_segments
        ):
            return (
                ()
                if output_index
                == len(output_segments)
                else None
            )

        if output_index >= len(
            output_segments
        ):
            return None

        source = source_segments[
            source_index
        ]

        if (
            output_segments[output_index].kind
            != source.kind
        ):
            return None

        end = output_index

        while (
            end < len(output_segments)
            and output_segments[end].kind
            == source.kind
        ):
            end += 1

            mode = segment_equivalence_mode(
                source.text,
                _join_output_text(
                    output_segments,
                    output_index,
                    end,
                ),
                kind=source.kind,
            )

            if mode is None:
                continue

            remainder = solve(
                source_index + 1,
                end,
            )

            if remainder is not None:
                return (
                    SegmentMatch(
                        source_index=source_index,
                        output_start=output_index,
                        output_end=end,
                        mode=mode,
                    ),
                    *remainder,
                )

        return None

    solved = solve(0, 0)

    return (
        list(solved)
        if solved is not None
        else None
    )


def _word_tokens(
    text: str,
) -> list[str]:
    return re.findall(
        r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?",
        _canonicalize_tts(text).lower(),
    )


def _contains_token_sequence(
    haystack: str,
    needle: str,
) -> bool:
    haystack_tokens = _word_tokens(
        haystack
    )

    needle_tokens = _word_tokens(
        needle
    )

    if not needle_tokens:
        return False

    width = len(needle_tokens)

    return any(
        haystack_tokens[
            index:index + width
        ]
        == needle_tokens
        for index in range(
            0,
            len(haystack_tokens)
            - width
            + 1,
        )
    )


def _diagnose_alignment_failure(
    source_segments: list[SourceSegment],
    output_segments: list[OutputSegment],
) -> list[AuditIssue]:
    source_index = 0
    output_index = 0

    while source_index < len(
        source_segments
    ):
        source = source_segments[
            source_index
        ]

        if output_index >= len(
            output_segments
        ):
            return [
                AuditIssue(
                    code="missing_source_segment",
                    severity="blocking",
                    message=(
                        "A source segment is missing "
                        "from the generated script."
                    ),
                    source_text=source.text,
                    source_index=source_index,
                )
            ]

        output = output_segments[
            output_index
        ]

        if source.kind != output.kind:
            if (
                source.kind == "dialogue"
                and output.kind == "narration"
            ):
                code = "dialogue_as_narrator"
                message = (
                    "Spoken dialogue was assigned "
                    "to NARRATOR."
                )

            elif (
                source.kind == "narration"
                and output.kind == "dialogue"
                and ANY_ATTRIBUTION_PATTERN.search(
                    source.text
                )
            ):
                code = "missing_attribution_boundary"
                message = (
                    "Attribution narration is missing "
                    "or dialogue crossed its boundary."
                )

            else:
                code = "narration_as_character"
                message = (
                    "Narrator prose was assigned "
                    "to a character."
                )

            return [
                AuditIssue(
                    code=code,
                    severity="blocking",
                    message=message,
                    source_text=source.text,
                    output_text=output.text,
                    source_index=source_index,
                    output_indices=(
                        output.entry_index,
                    ),
                )
            ]

        current_mode = segment_equivalence_mode(
            source.text,
            output.text,
            kind=source.kind,
        )

        # Two separate character entries with the narrator
        # attribution omitted.
        if (
            current_mode is not None
            and source.kind == "dialogue"
            and source_index + 1
            < len(source_segments)
            and source_segments[
                source_index + 1
            ].kind == "narration"
            and output_index + 1
            < len(output_segments)
            and output_segments[
                output_index + 1
            ].kind == "dialogue"
        ):
            missing = source_segments[
                source_index + 1
            ]

            next_output = output_segments[
                output_index + 1
            ]

            return [
                AuditIssue(
                    code="missing_attribution_boundary",
                    severity="blocking",
                    message=(
                        "Attribution narration is missing "
                        "between character entries."
                    ),
                    source_text=missing.text,
                    output_text=next_output.text,
                    source_index=source_index + 1,
                    output_indices=(
                        next_output.entry_index,
                    ),
                )
            ]

        # One character entry containing dialogue from both
        # sides of an omitted narrator interruption.
        if (
            source.kind == "dialogue"
            and source_index + 2
            < len(source_segments)
            and source_segments[
                source_index + 1
            ].kind == "narration"
            and source_segments[
                source_index + 2
            ].kind == "dialogue"
            and _contains_token_sequence(
                output.text,
                source.text,
            )
            and _contains_token_sequence(
                output.text,
                source_segments[
                    source_index + 2
                ].text,
            )
        ):
            return [
                AuditIssue(
                    code=(
                        "merged_across_narrator_boundary"
                    ),
                    severity="blocking",
                    message=(
                        "Character dialogue was merged "
                        "across intervening narrator text."
                    ),
                    source_text=source.text,
                    output_text=output.text,
                    source_index=source_index,
                    output_indices=(
                        output.entry_index,
                    ),
                )
            ]

        candidate_parts: list[str] = []
        candidate_indices: list[int] = []
        cursor = output_index

        while (
            cursor < len(output_segments)
            and output_segments[cursor].kind
            == source.kind
        ):
            candidate_parts.append(
                output_segments[cursor].text
            )

            candidate_indices.append(
                output_segments[
                    cursor
                ].entry_index
            )

            candidate = " ".join(
                candidate_parts
            )

            mode = segment_equivalence_mode(
                source.text,
                candidate,
                kind=source.kind,
            )

            if mode is not None:
                output_index = cursor + 1
                source_index += 1
                break

            cursor += 1

        else:
            candidate = " ".join(
                candidate_parts
            )

            if (
                source.kind == "narration"
                and ANY_ATTRIBUTION_PATTERN.search(
                    source.text
                )
            ):
                code = "attribution_changed"
                message = (
                    "Attribution wording, grammar, "
                    "punctuation, or structure changed."
                )

            elif (
                _word_tokens(source.text)
                == _word_tokens(candidate)
            ):
                code = "punctuation_changed"
                message = (
                    "Segment words match, but punctuation "
                    "or textual structure changed."
                )

            else:
                code = "source_text_changed"
                message = (
                    "Generated text does not preserve "
                    "the corresponding source segment."
                )

            return [
                AuditIssue(
                    code=code,
                    severity="blocking",
                    message=message,
                    source_text=source.text,
                    output_text=candidate,
                    source_index=source_index,
                    output_indices=tuple(
                        candidate_indices
                    ),
                )
            ]

    if output_index < len(
        output_segments
    ):
        remaining = output_segments[
            output_index:
        ]

        return [
            AuditIssue(
                code="extra_output",
                severity="blocking",
                message=(
                    "Generated script contains text "
                    "with no corresponding source segment."
                ),
                output_text=" ".join(
                    segment.text
                    for segment in remaining
                ),
                output_indices=tuple(
                    segment.entry_index
                    for segment in remaining
                ),
            )
        ]

    return []


def audit_script_chunk(
    source_text: str,
    entries: list[dict[str, Any]],
) -> ScriptAuditResult:
    issues: list[AuditIssue] = []

    try:
        source_segments = (
            split_source_segments(
                source_text
            )
        )

    except UnbalancedDialogueQuotesError as exc:
        source_segments = []

        issues.append(
            AuditIssue(
                code="unbalanced_source_quotes",
                severity="blocking",
                message=str(exc),
            )
        )

    output_segments, output_issues = (
        build_output_segments(entries)
    )

    issues.extend(output_issues)

    matches: list[SegmentMatch] = []

    if not issues:
        aligned = align_segments(
            source_segments,
            output_segments,
        )

        if aligned is not None:
            matches = aligned

        else:
            issues.extend(
                _diagnose_alignment_failure(
                    source_segments,
                    output_segments,
                )
            )

    mode_counts = {
        "exact": 0,
        "tts_conversion": 0,
        "attribution_subject_clarification": 0,
    }

    for match in matches:
        mode_counts[match.mode] += 1

    metrics = {
        "source_segment_count": len(
            source_segments
        ),
        "output_entry_count": len(
            output_segments
        ),
        "matched_segment_count": len(
            matches
        ),
        "dialogue_segment_count": sum(
            segment.kind == "dialogue"
            for segment in source_segments
        ),
        "narration_segment_count": sum(
            segment.kind == "narration"
            for segment in source_segments
        ),
        "exact_match_count": (
            mode_counts["exact"]
        ),
        "tts_conversion_count": (
            mode_counts["tts_conversion"]
        ),
        "attribution_clarification_count": (
            mode_counts[
                "attribution_subject_clarification"
            ]
        ),
        "blocking_count": sum(
            issue.severity == "blocking"
            for issue in issues
        ),
        "warning_count": sum(
            issue.severity == "warning"
            for issue in issues
        ),
    }

    return ScriptAuditResult(
        source_segments=source_segments,
        output_segments=output_segments,
        matches=matches,
        issues=issues,
        metrics=metrics,
    )


def format_audit_summary(
    result: ScriptAuditResult,
) -> list[str]:
    status = (
        "PASS"
        if result.passed
        else "BLOCKED"
    )

    lines = [
        (
            f"Fidelity audit: {status} | "
            f"segments "
            f"{result.metrics.get('matched_segment_count', 0)}/"
            f"{result.metrics.get('source_segment_count', 0)} | "
            f"exact "
            f"{result.metrics.get('exact_match_count', 0)} | "
            f"TTS conversions "
            f"{result.metrics.get('tts_conversion_count', 0)} | "
            f"attribution clarifications "
            f"{result.metrics.get('attribution_clarification_count', 0)}"
        )
    ]

    for issue in result.issues:
        line = (
            f"  [{issue.severity.upper()}] "
            f"{issue.code}: {issue.message}"
        )

        if issue.source_text:
            line += (
                " Source="
                + repr(
                    issue.source_text[:180]
                )
            )

        if issue.output_text:
            line += (
                " Output="
                + repr(
                    issue.output_text[:180]
                )
            )

        lines.append(line)

    return lines
