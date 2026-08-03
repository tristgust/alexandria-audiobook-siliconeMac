from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


NARRATOR_LABELS = frozenset(
    {
        "NARRATOR",
        "NARRATION",
        "NARRATIVE",
    }
)

PRONOUN_SPEAKER_LABELS = frozenset(
    {
        "I",
        "HE",
        "SHE",
        "THEY",
        "WE",
        "YOU",
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


ASCII_LEADING_APOSTROPHE_WORDS = frozenset(
    {
        "bout",
        "cause",
        "em",
        "fore",
        "gainst",
        "neath",
        "round",
        "til",
        "tis",
        "twas",
        "twere",
        "twill",
        "twould",
    }
)

ASCII_EPIGRAPH_PATTERN = re.compile(
    r"(?m)^[ \t]*'[^\n]+'[ \t]*\n[ \t]*[—–-][ \t]*[^\n]+$"
)
EPIGRAPH_ATTRIBUTION_LINE_PATTERN = re.compile(
    r"^[ \t]*[A-Z][^,\n]{1,80},[ \t]+(?:The|A|An)[ \t]+[^\n]{2,160}$"
)
ASCII_SINGLE_QUOTE_FOLLOWERS = frozenset(
    {
        "a",
        "an",
        "he",
        "her",
        "hers",
        "him",
        "his",
        "i",
        "it",
        "its",
        "she",
        "the",
        "their",
        "them",
        "they",
        "we",
        "you",
        "your",
        *ATTRIBUTION_VERBS,
    }
)


def _next_word(text: str, index: int) -> str:
    match = re.search(
        r"[A-Za-zÀ-ÖØ-öø-ÿ]+",
        text[index:],
    )
    return match.group(0) if match else ""


def _previous_word(text: str, index: int) -> str:
    match = re.search(
        r"[A-Za-zÀ-ÖØ-öø-ÿ]+$",
        text[:index],
    )
    return match.group(0) if match else ""


def _is_ascii_word_apostrophe(text: str, index: int) -> bool:
    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    return previous.isalnum() and following.isalnum()


def _is_ascii_single_quote_closing(
    text: str,
    index: int,
) -> bool:
    if _is_escaped(text, index) or _is_ascii_word_apostrophe(text, index):
        return False

    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if not previous:
        return False
    if previous.isspace():
        cursor = index - 1
        while cursor >= 0 and text[cursor].isspace():
            cursor -= 1
        previous_nonspace = text[cursor] if cursor >= 0 else ""
        if (
            previous_nonspace
            and (not following or following in "\r\n")
        ):
            return True
        if (
            previous_nonspace in ",.;:!?…—–-"
            and following.isspace()
        ):
            return True
        return False
    if not following:
        return True
    if following in "\r\n":
        previous_word = _previous_word(text, index)
        next_word = _next_word(text, index + 1)
        previous_lower = previous_word.casefold()
        if next_word and next_word[0].islower():
            if (
                previous_lower == "an"
                or (len(previous_lower) > 3 and previous_lower.endswith("in"))
                or previous_lower in {"ol", "cos", "cause"}
            ):
                return False
            if (
                previous_lower.endswith("s")
                and next_word.casefold() not in ASCII_SINGLE_QUOTE_FOLLOWERS
            ):
                return False
        return True
    if previous in ',.;:!?…—–)]}"':
        return True
    if not following.isspace():
        return True

    # A bare apostrophe after a word can be a possessive or elision rather
    # than dialogue punctuation: James' hat, dogs' collars, somethin' strange.
    # Preserve it unless the following token looks like narration resuming
    # after an unpunctuated quote.
    previous_word = _previous_word(text, index)
    next_word = _next_word(text, index + 1)
    previous_lower = previous_word.casefold()
    if (
        next_word
        and next_word[0].islower()
        and (
            previous_lower == "an"
            or (len(previous_lower) > 3 and previous_lower.endswith("in"))
            or previous_lower in {"ol", "cos", "cause"}
        )
    ):
        return False
    if next_word and next_word[0].isupper():
        return True
    if next_word.lower() in ASCII_SINGLE_QUOTE_FOLLOWERS:
        return True
    # Quoted terms such as 'Human' is should close even without punctuation.
    # Plural possessives and names ending in s remain protected, including
    # dogs' collars and James' hat.
    if previous_word and not previous_word.casefold().endswith("s"):
        return True
    if previous_word:
        return False
    return True


def _has_ascii_single_quote_closer(text: str, start: int) -> bool:
    cursor = start
    while cursor < len(text):
        cursor = text.find("'", cursor)
        if cursor < 0:
            return False
        if _is_ascii_single_quote_closing(text, cursor):
            return True
        cursor += 1
    return False


def _is_ascii_leading_apostrophe(text: str, index: int) -> bool:
    following = text[index + 1] if index + 1 < len(text) else ""
    if following.isdigit():
        return True
    word = _next_word(text, index + 1).casefold()
    return word in ASCII_LEADING_APOSTROPHE_WORDS


def _ascii_inline_term_quote_indexes(text: str) -> frozenset[int]:
    """Identify prose terms quoted inline, not spoken dialogue.

    The closing quote is found with the same apostrophe-aware predicate used by
    the scanner, so contractions and possessives inside real dialogue cannot
    create a false short match (for example, ``'It's James' hat,'``).
    """
    indexes: set[int] = set()
    opening = 0
    while opening < len(text):
        opening = text.find("'", opening)
        if opening < 0:
            break
        if _is_escaped(text, opening) or _is_ascii_word_apostrophe(text, opening):
            opening += 1
            continue
        previous = text[opening - 1] if opening > 0 else ""
        following = text[opening + 1] if opening + 1 < len(text) else ""
        if previous and previous.isalnum() or not following or following.isspace():
            opening += 1
            continue

        closing = opening + 1
        while closing < len(text):
            closing = text.find("'", closing)
            if closing < 0 or _is_ascii_single_quote_closing(text, closing):
                break
            closing += 1
        if closing < 0:
            break

        body = text[opening + 1:closing].strip()
        previous_nonspace = ""
        cursor = opening - 1
        while cursor >= 0 and text[cursor] in " \t":
            cursor -= 1
        if cursor >= 0 and text[cursor] not in "\r\n":
            previous_nonspace = text[cursor]
        next_word = _next_word(text, closing + 1)
        if (
            body
            and body[-1:] not in ",.;:!?…—–"
            and (previous_nonspace.isalnum() or previous_nonspace in ")]}")
            and next_word
            and next_word[0].islower()
            and next_word.casefold() not in ASCII_SINGLE_QUOTE_FOLLOWERS
        ):
            indexes.add(opening)
            indexes.add(closing)
        opening = closing + 1
    return frozenset(indexes)


def _ascii_inline_interrupted_dialogue_spans(text: str) -> dict[int, int]:
    """Find action prose embedded between two spoken fragments.

    Handles ebook punctuation such as ``'Question - ' Smith gestured, why?'
    `` where the continuation has a final closing quote but no reopening quote.
    The returned mapping is closing-quote index to resumed-dialogue index.
    """
    dialogue_starter = re.compile(
        r",\s+(?=(?:why|what|how|who|where|when|which|can|could|"
        r"would|will|do|did|does|is|are|was|were|have|has|i|you|"
        r"we|they|he|she)\b)",
        flags=re.IGNORECASE,
    )
    action_verb = re.compile(
        r"\b(?:said|asked|replied|answered|threw|caught|produced|"
        r"appeared|turned|looked|gestured|paused|smiled|nodded|"
        r"shrugged|walked|stepped|raised|lowered|picked|put|took|"
        r"held|opened|closed|moved|glanced|stared)\b",
        flags=re.IGNORECASE,
    )
    spans: dict[int, int] = {}
    for index, character in enumerate(text):
        if character != "'" or _is_escaped(text, index):
            continue
        previous = text[index - 1] if index > 0 else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        if not previous.isspace() or not following.isspace():
            continue
        cursor = index - 1
        while cursor >= 0 and text[cursor].isspace():
            cursor -= 1
        if cursor < 0 or text[cursor] not in ",.;:!?…—–-":
            continue

        closing = index + 1
        while closing < len(text):
            closing = text.find("'", closing)
            if closing < 0:
                break
            if _is_ascii_single_quote_closing(text, closing):
                break
            closing += 1
        if closing < 0:
            continue

        between = text[index + 1:closing]
        if "\n\n" in between:
            continue
        transitions = list(dialogue_starter.finditer(between))
        if not transitions:
            continue
        transition = transitions[-1]
        action_text = between[:transition.start() + 1]
        if not action_verb.search(action_text):
            continue
        spans[index] = index + 1 + transition.end()
    return spans


def _ascii_interrupted_dialogue_boundaries(text: str) -> frozenset[int]:
    """Recover omitted closing quotes around intervening prose paragraphs.

    Some ebooks contain ``'Question?`` followed by an unquoted attribution or
    action paragraph and then a new ``'Answer.'`` paragraph. Treat the first
    paragraph boundary as the end of dialogue without inventing punctuation.
    """
    paragraphs = list(
        re.finditer(
            r"\S(?:.*?\S)?(?=\n[ \t]*\n|\Z)",
            text,
            re.DOTALL,
        )
    )
    boundaries: set[int] = set()
    for first, middle, following in zip(
        paragraphs,
        paragraphs[1:],
        paragraphs[2:],
    ):
        first_text = first.group(0).lstrip()
        middle_text = middle.group(0).lstrip()
        following_text = following.group(0).lstrip()
        if (
            not first_text.startswith("'")
            or not middle_text
            or middle_text.startswith("'")
            or not following_text.startswith("'")
        ):
            continue
        opening = first.start() + (len(first.group(0)) - len(first_text))
        cursor = opening + 1
        has_closer = False
        while cursor < first.end():
            cursor = text.find("'", cursor, first.end())
            if cursor < 0:
                break
            if _is_ascii_single_quote_closing(text, cursor):
                has_closer = True
                break
            cursor += 1
        if not has_closer:
            boundaries.add(first.end())
    return frozenset(boundaries)


def _ascii_epigraph_quote_indexes(text: str) -> frozenset[int]:
    indexes: set[int] = set()
    for match in ASCII_EPIGRAPH_PATTERN.finditer(text):
        opening = text.find("'", match.start(), match.end())
        closing = text.rfind("'", match.start(), match.end())
        if opening >= 0 and closing > opening:
            indexes.add(opening)
            indexes.add(closing)
    return frozenset(indexes)


def _curly_epigraph_opening_indexes(text: str) -> frozenset[int]:
    lines = list(re.finditer(r"(?m)^.*(?:\n|$)", text))
    indexes: set[int] = set()
    for line_index, line in enumerate(lines):
        raw_line = line.group(0).rstrip("\r\n")
        stripped = raw_line.lstrip(" \t")
        if not stripped.startswith("‘"):
            continue
        opening = line.start() + (len(raw_line) - len(stripped))
        for following in lines[line_index + 1:line_index + 9]:
            candidate = following.group(0).strip()
            if not candidate:
                break
            if EPIGRAPH_ATTRIBUTION_LINE_PATTERN.fullmatch(candidate):
                indexes.add(opening)
                break
    return frozenset(indexes)


def _inline_curly_term_opening_indexes(
    text: str,
    *,
    opening_quote: str,
    closing_quote: str,
) -> frozenset[int]:
    indexes: set[int] = set()
    opening = 0
    while opening < len(text):
        opening = text.find(opening_quote, opening)
        if opening < 0:
            break
        closing = opening + 1
        while closing < len(text):
            closing = text.find(closing_quote, closing)
            if closing < 0:
                break
            if closing_quote != "’" or _is_ascii_single_quote_closing(text, closing):
                break
            closing += 1
        if closing < 0:
            break

        body = text[opening + 1:closing].strip()
        cursor = opening - 1
        while cursor >= 0 and text[cursor] in " \t":
            cursor -= 1
        previous_nonspace = text[cursor] if cursor >= 0 and text[cursor] not in "\r\n" else ""
        cursor = closing + 1
        while cursor < len(text) and text[cursor] in " \t":
            cursor += 1
        following_nonspace = text[cursor] if cursor < len(text) else ""
        next_word = _next_word(text, closing + 1)
        inline_context = (
            not previous_nonspace
            or previous_nonspace.isalnum()
            or previous_nonspace in ")]}"
        )
        prose_continuation = (
            next_word
            and next_word[0].islower()
            and next_word.casefold() not in ASCII_SINGLE_QUOTE_FOLLOWERS
        )
        prose_punctuation = following_nonspace in ".,;:)]}"
        quoted_term = (
            bool(body)
            and "\n" not in body
            and len(body) <= 80
            and body[-1:] not in ",.;:!?…—–"
            and inline_context
            and (
                prose_continuation
                or (body[0].islower() and prose_punctuation)
            )
        )
        parenthetical = (
            bool(body)
            and previous_nonspace == "("
            and following_nonspace == ")"
        )
        if quoted_term or parenthetical:
            indexes.add(opening)
        opening = closing + 1
    return frozenset(indexes)


def _curly_inline_term_opening_indexes(text: str) -> frozenset[int]:
    return (
        _inline_curly_term_opening_indexes(
            text,
            opening_quote="‘",
            closing_quote="’",
        )
        | _inline_curly_term_opening_indexes(
            text,
            opening_quote="“",
            closing_quote="”",
        )
    )


def _is_ascii_single_quote_opening(text: str, index: int) -> bool:
    if _is_escaped(text, index) or _is_ascii_word_apostrophe(text, index):
        return False
    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if previous and previous.isalnum():
        return False
    if not following or following.isspace():
        return False
    if _is_ascii_leading_apostrophe(text, index):
        return False
    # A plausible opening quote is treated as dialogue even when its closer is
    # missing. The main scanner will then fail closed with an unbalanced-quote
    # error instead of silently treating dialogue as narration.
    return True


def split_source_segments(
    source_text: str,
) -> list[SourceSegment]:
    text = source_text or ""
    segments: list[SourceSegment] = []

    buffer: list[str] = []
    buffer_start = 0
    current_kind = "narration"
    closing_quote: str | None = None
    non_dialogue_quote_indexes = (
        _ascii_epigraph_quote_indexes(text)
        | _ascii_inline_term_quote_indexes(text)
        | _curly_epigraph_opening_indexes(text)
        | _curly_inline_term_opening_indexes(text)
    )
    ascii_interrupted_dialogue_boundaries = (
        _ascii_interrupted_dialogue_boundaries(text)
    )
    ascii_inline_interrupted_dialogue_spans = (
        _ascii_inline_interrupted_dialogue_spans(text)
    )
    ascii_inline_dialogue_resume_indexes = frozenset(
        ascii_inline_interrupted_dialogue_spans.values()
    )

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

        if (
            closing_quote == "'"
            and index in ascii_inline_interrupted_dialogue_spans
        ):
            flush(index)
            current_kind = "narration"
            closing_quote = None
            buffer_start = index + 1
            index += 1
            continue

        if (
            closing_quote == "'"
            and index in ascii_interrupted_dialogue_boundaries
        ):
            flush(index)
            current_kind = "narration"
            closing_quote = None
            buffer_start = index

        if (
            closing_quote is None
            and index in ascii_inline_dialogue_resume_indexes
        ):
            flush(index)
            current_kind = "dialogue"
            closing_quote = "'"
            buffer_start = index

        if closing_quote is None:
            is_ascii_opening = (
                character == "'"
                and index not in non_dialogue_quote_indexes
                and _is_ascii_single_quote_opening(text, index)
            )
            if (
                is_ascii_opening
                or (
                    character in QUOTE_PAIRS
                    and index not in non_dialogue_quote_indexes
                    and not _is_escaped(
                        text,
                        index,
                    )
                )
            ):
                flush(index)

                current_kind = "dialogue"
                closing_quote = (
                    "'"
                    if is_ascii_opening
                    else QUOTE_PAIRS[character]
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
            and (
                closing_quote not in {"'", "’"}
                or _is_ascii_single_quote_closing(text, index)
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

        normalized_speaker = speaker.strip().upper()
        if normalized_speaker in PRONOUN_SPEAKER_LABELS:
            issues.append(
                AuditIssue(
                    code="pronoun_speaker_label",
                    severity="blocking",
                    message=(
                        f"Entry {index} uses pronoun speaker label "
                        f"{speaker.strip()!r}. Use the established character "
                        "name, or NARRATOR for prose."
                    ),
                    output_indices=(index,),
                )
            )

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
            if normalized_speaker in NARRATOR_LABELS
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
    source_count = len(source_segments)
    output_count = len(output_segments)

    if source_count == 0:
        return [] if output_count == 0 else None
    if output_count == 0:
        return None

    def build_frame(
        source_index: int,
        output_index: int,
        incoming_match: SegmentMatch | None,
    ) -> dict[str, Any]:
        run_end = output_index
        if (
            source_index < source_count
            and output_index < output_count
            and output_segments[output_index].kind
            == source_segments[source_index].kind
        ):
            kind = source_segments[source_index].kind
            while (
                run_end < output_count
                and output_segments[run_end].kind == kind
            ):
                run_end += 1
        return {
            "source_index": source_index,
            "output_index": output_index,
            "next_end": output_index + 1,
            "run_end": run_end,
            "incoming_match": incoming_match,
        }

    dead_states: set[tuple[int, int]] = set()
    stack = [build_frame(0, 0, None)]

    while stack:
        frame = stack[-1]
        source_index = frame["source_index"]
        output_index = frame["output_index"]
        state = (source_index, output_index)

        if source_index == source_count:
            if output_index == output_count:
                return [
                    candidate["incoming_match"]
                    for candidate in stack[1:]
                    if candidate["incoming_match"] is not None
                ]
            dead_states.add(state)
            stack.pop()
            continue

        if (
            output_index >= output_count
            or frame["run_end"] <= output_index
        ):
            dead_states.add(state)
            stack.pop()
            continue

        source = source_segments[source_index]
        descended = False

        while frame["next_end"] <= frame["run_end"]:
            end = frame["next_end"]
            frame["next_end"] += 1
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

            next_state = (source_index + 1, end)
            if next_state in dead_states:
                continue

            match = SegmentMatch(
                source_index=source_index,
                output_start=output_index,
                output_end=end,
                mode=mode,
            )
            stack.append(
                build_frame(
                    source_index + 1,
                    end,
                    match,
                )
            )
            descended = True
            break

        if descended:
            continue

        dead_states.add(state)
        stack.pop()

    return None


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
            current_mode is None
            and source.kind == "dialogue"
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
