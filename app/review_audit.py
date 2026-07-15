from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any


REQUIRED_FIELDS = frozenset(
    {
        "speaker",
        "text",
        "instruct",
    }
)


@dataclass(frozen=True)
class ReviewAuditIssue:
    code: str
    severity: str
    message: str
    offset: int | None = None
    original_context: str = ""
    corrected_context: str = ""
    entry_index: int | None = None


@dataclass
class ReviewAuditResult:
    issues: list[ReviewAuditIssue]
    metrics: dict[str, Any]
    original_stream: str
    corrected_stream: str

    @property
    def blocking_issues(
        self,
    ) -> list[ReviewAuditIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == "blocking"
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
            "metrics": dict(self.metrics),
            "issues": [
                asdict(issue)
                for issue in self.issues
            ],
        }


def normalize_review_text(
    text: str,
) -> str:
    value = unicodedata.normalize(
        "NFC",
        text,
    )

    value = value.replace(
        "\u00a0",
        " ",
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def _validate_entries(
    entries: Any,
    *,
    label: str,
) -> tuple[
    list[dict[str, str]],
    list[ReviewAuditIssue],
]:
    issues: list[ReviewAuditIssue] = []
    valid_entries: list[dict[str, str]] = []

    if not isinstance(entries, list):
        return (
            [],
            [
                ReviewAuditIssue(
                    code=f"invalid_{label}_type",
                    severity="blocking",
                    message=(
                        f"{label.capitalize()} data "
                        "is not a JSON array."
                    ),
                )
            ],
        )

    if not entries:
        return (
            [],
            [
                ReviewAuditIssue(
                    code=f"empty_{label}_entries",
                    severity="blocking",
                    message=(
                        f"{label.capitalize()} data "
                        "contains no entries."
                    ),
                )
            ],
        )

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            issues.append(
                ReviewAuditIssue(
                    code=f"invalid_{label}_entry_type",
                    severity="blocking",
                    message=(
                        f"{label.capitalize()} entry "
                        f"{index} is not an object."
                    ),
                    entry_index=index,
                )
            )
            continue

        if set(entry) != REQUIRED_FIELDS:
            issues.append(
                ReviewAuditIssue(
                    code=f"invalid_{label}_entry_fields",
                    severity="blocking",
                    message=(
                        f"{label.capitalize()} entry "
                        f"{index} does not contain exactly "
                        "speaker, text, and instruct."
                    ),
                    entry_index=index,
                )
            )
            continue

        entry_is_valid = True

        for field in REQUIRED_FIELDS:
            value = entry[field]

            if (
                not isinstance(value, str)
                or not value.strip()
            ):
                issues.append(
                    ReviewAuditIssue(
                        code=(
                            f"invalid_{label}_{field}"
                        ),
                        severity="blocking",
                        message=(
                            f"{label.capitalize()} entry "
                            f"{index} has invalid "
                            f"{field!r}."
                        ),
                        entry_index=index,
                    )
                )

                entry_is_valid = False

        if entry_is_valid:
            valid_entries.append(entry)

    return valid_entries, issues


def build_review_text_stream(
    entries: list[dict[str, str]],
) -> str:
    return " ".join(
        normalize_review_text(
            entry["text"]
        )
        for entry in entries
    )


def _word_tokens(
    text: str,
) -> list[str]:
    return re.findall(
        r"\w+(?:['’]\w+)*",
        text,
        flags=re.UNICODE,
    )


def _first_difference(
    original: str,
    corrected: str,
) -> int:
    limit = min(
        len(original),
        len(corrected),
    )

    for index in range(limit):
        if original[index] != corrected[index]:
            return index

    return limit


def _context_window(
    text: str,
    offset: int,
    radius: int = 70,
) -> str:
    return text[
        max(0, offset - radius):
        min(len(text), offset + radius)
    ]


def _classify_text_change(
    original: str,
    corrected: str,
) -> str:
    original_words = _word_tokens(
        original
    )

    corrected_words = _word_tokens(
        corrected
    )

    if original_words == corrected_words:
        return "punctuation_or_structure_changed"

    matcher = SequenceMatcher(
        None,
        original_words,
        corrected_words,
        autojunk=False,
    )

    change_tags = {
        tag
        for tag, *_ in matcher.get_opcodes()
        if tag != "equal"
    }

    # A replace operation always means wording or order
    # changed, even when the replacement has fewer words.
    if "replace" in change_tags:
        return "wording_or_order_changed"

    if change_tags == {"delete"}:
        return "text_omitted"

    if change_tags == {"insert"}:
        return "text_added"

    # Delete+insert commonly represents reordering.
    return "wording_or_order_changed"


def audit_review_batch(
    original_entries: Any,
    corrected_entries: Any,
) -> ReviewAuditResult:
    original, original_issues = _validate_entries(
        original_entries,
        label="original",
    )

    corrected, corrected_issues = _validate_entries(
        corrected_entries,
        label="corrected",
    )

    issues = [
        *original_issues,
        *corrected_issues,
    ]

    original_stream = (
        build_review_text_stream(original)
        if original
        else ""
    )

    corrected_stream = (
        build_review_text_stream(corrected)
        if corrected
        else ""
    )

    if (
        not issues
        and original_stream != corrected_stream
    ):
        offset = _first_difference(
            original_stream,
            corrected_stream,
        )

        code = _classify_text_change(
            original_stream,
            corrected_stream,
        )

        messages = {
            "punctuation_or_structure_changed": (
                "Review output changed punctuation "
                "or textual structure."
            ),
            "text_omitted": (
                "Review output omitted original text."
            ),
            "text_added": (
                "Review output added text that was "
                "not present in the original batch."
            ),
            "wording_or_order_changed": (
                "Review output changed wording "
                "or text order."
            ),
        }

        issues.append(
            ReviewAuditIssue(
                code=code,
                severity="blocking",
                message=messages[code],
                offset=offset,
                original_context=_context_window(
                    original_stream,
                    offset,
                ),
                corrected_context=_context_window(
                    corrected_stream,
                    offset,
                ),
            )
        )

    original_words = _word_tokens(
        original_stream
    )

    corrected_words = _word_tokens(
        corrected_stream
    )

    similarity = SequenceMatcher(
        None,
        original_stream,
        corrected_stream,
        autojunk=False,
    ).ratio()

    metrics = {
        "original_entry_count": len(original),
        "corrected_entry_count": len(corrected),
        "entry_delta": (
            len(corrected)
            - len(original)
        ),
        "original_character_count": len(
            original_stream
        ),
        "corrected_character_count": len(
            corrected_stream
        ),
        "original_word_count": len(
            original_words
        ),
        "corrected_word_count": len(
            corrected_words
        ),
        "exact_text_match": (
            original_stream
            == corrected_stream
        ),
        "similarity_ratio": similarity,
        "blocking_count": sum(
            issue.severity == "blocking"
            for issue in issues
        ),
    }

    return ReviewAuditResult(
        issues=issues,
        metrics=metrics,
        original_stream=original_stream,
        corrected_stream=corrected_stream,
    )


def format_review_audit_summary(
    result: ReviewAuditResult,
) -> list[str]:
    status = (
        "PASS"
        if result.passed
        else "BLOCKED"
    )

    lines = [
        (
            f"Review text audit: {status} | "
            f"entries "
            f"{result.metrics.get('original_entry_count', 0)}"
            "→"
            f"{result.metrics.get('corrected_entry_count', 0)}"
            " | words "
            f"{result.metrics.get('original_word_count', 0)}"
            "→"
            f"{result.metrics.get('corrected_word_count', 0)}"
            " | exact="
            f"{result.metrics.get('exact_text_match', False)}"
        )
    ]

    for issue in result.issues:
        line = (
            f"  [{issue.severity.upper()}] "
            f"{issue.code}: {issue.message}"
        )

        if issue.original_context:
            line += (
                " Original="
                + repr(issue.original_context)
            )

        if issue.corrected_context:
            line += (
                " Corrected="
                + repr(issue.corrected_context)
            )

        lines.append(line)

    return lines
