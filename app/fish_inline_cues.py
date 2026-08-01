from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_TAG_LENGTH = 180
_ALLOWED_ANCHORS = frozenset({"start", "before_phrase", "after_phrase", "end"})
_ALLOWED_KINDS = frozenset({"delivery", "reaction", "reset"})
_REACTION_END_TAGS = frozenset(
    {
        "sigh",
        "inhale",
        "exhale",
        "gasp",
        "laugh",
        "laughing",
        "chuckle",
        "chuckling",
        "giggle",
        "groan",
        "sobbing",
        "crying",
        "clears throat",
        "clearing throat",
        "tsk",
    }
)
_TAG_PATTERN = re.compile(r"\[[^\[\]\r\n]{1,180}\]")


class FishInlineCueError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FishInlineCue:
    anchor: str
    tag: str
    kind: str
    phrase: str | None = None
    occurrence: int = 1

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "anchor": self.anchor,
            "tag": self.tag,
            "kind": self.kind,
        }
        if self.phrase is not None:
            value["phrase"] = self.phrase
            value["occurrence"] = self.occurrence
        return value


@dataclass(frozen=True)
class FishInlinePlan:
    schema_version: int
    text_sha256: str
    cues: tuple[FishInlineCue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "text_sha256": self.text_sha256,
            "cues": [cue.as_dict() for cue in self.cues],
        }


def text_sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def plan_fingerprint(plan: Mapping[str, Any] | FishInlinePlan | None) -> str | None:
    if plan is None:
        return None
    normalized = plan.as_dict() if isinstance(plan, FishInlinePlan) else dict(plan)
    return hashlib.sha256(_canonical_json(normalized)).hexdigest()


def _normalized_tag(value: Any) -> str:
    tag = " ".join(str(value or "").strip().split())
    if not tag:
        raise FishInlineCueError("fish_inline_tag_required", "Every Fish cue requires a tag.")
    if len(tag) > MAX_TAG_LENGTH:
        raise FishInlineCueError(
            "fish_inline_tag_too_long",
            f"Fish cue tags must be {MAX_TAG_LENGTH} characters or fewer.",
        )
    if "[" in tag or "]" in tag or any(ord(char) < 32 for char in tag):
        raise FishInlineCueError(
            "fish_inline_tag_invalid",
            "Fish cue tags cannot contain brackets or control characters.",
        )
    return tag


def _normalize_cue(raw: Any, *, index: int) -> FishInlineCue:
    if not isinstance(raw, Mapping):
        raise FishInlineCueError(
            "fish_inline_cue_invalid",
            f"Fish cue {index + 1} must be an object.",
        )
    allowed = {"anchor", "tag", "kind", "phrase", "occurrence"}
    extra = set(raw) - allowed
    if extra:
        raise FishInlineCueError(
            "fish_inline_cue_fields_invalid",
            f"Fish cue {index + 1} has unsupported fields: {sorted(extra)}.",
        )
    anchor = str(raw.get("anchor") or "").strip()
    if anchor not in _ALLOWED_ANCHORS:
        raise FishInlineCueError(
            "fish_inline_anchor_invalid",
            f"Fish cue {index + 1} has unsupported anchor {anchor!r}.",
        )
    kind = str(raw.get("kind") or "delivery").strip()
    if kind not in _ALLOWED_KINDS:
        raise FishInlineCueError(
            "fish_inline_kind_invalid",
            f"Fish cue {index + 1} has unsupported kind {kind!r}.",
        )
    tag = _normalized_tag(raw.get("tag"))
    phrase = raw.get("phrase")
    occurrence = raw.get("occurrence", 1)
    if anchor in {"before_phrase", "after_phrase"}:
        if not isinstance(phrase, str) or not phrase:
            raise FishInlineCueError(
                "fish_inline_phrase_required",
                f"Fish cue {index + 1} requires an exact phrase anchor.",
            )
        if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
            raise FishInlineCueError(
                "fish_inline_occurrence_invalid",
                f"Fish cue {index + 1} occurrence must be a positive integer.",
            )
    else:
        if phrase not in (None, "") or "occurrence" in raw:
            raise FishInlineCueError(
                "fish_inline_phrase_unexpected",
                f"Fish cue {index + 1} cannot use phrase or occurrence with {anchor!r}.",
            )
        phrase = None
        occurrence = 1
    if anchor == "end" and kind != "reaction":
        raise FishInlineCueError(
            "fish_inline_end_requires_reaction",
            "A Fish cue at the end of a line must be a paralinguistic reaction.",
        )
    if anchor == "end" and tag.casefold() not in _REACTION_END_TAGS:
        raise FishInlineCueError(
            "fish_inline_end_reaction_unproven",
            "End-of-line Fish cues are limited to well-tested reaction tags.",
        )
    return FishInlineCue(
        anchor=anchor,
        tag=tag,
        kind=kind,
        phrase=phrase,
        occurrence=occurrence,
    )


def validate_plan(text: str, raw_plan: Mapping[str, Any] | FishInlinePlan) -> FishInlinePlan:
    if isinstance(raw_plan, FishInlinePlan):
        plan = raw_plan
    else:
        if not isinstance(raw_plan, Mapping):
            raise FishInlineCueError(
                "fish_inline_plan_invalid",
                "Fish inline render plan must be an object.",
            )
        allowed = {"schema_version", "text_sha256", "cues"}
        extra = set(raw_plan) - allowed
        if extra:
            raise FishInlineCueError(
                "fish_inline_plan_fields_invalid",
                f"Fish inline render plan has unsupported fields: {sorted(extra)}.",
            )
        schema_version = raw_plan.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            raise FishInlineCueError(
                "fish_inline_schema_unsupported",
                f"Fish inline render plan schema must be {SCHEMA_VERSION}.",
            )
        expected_hash = raw_plan.get("text_sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise FishInlineCueError(
                "fish_inline_text_hash_invalid",
                "Fish inline render plan requires a lowercase SHA-256 text hash.",
            )
        cues = raw_plan.get("cues")
        if not isinstance(cues, list) or not cues:
            raise FishInlineCueError(
                "fish_inline_cues_required",
                "Fish inline render plan requires at least one cue.",
            )
        plan = FishInlinePlan(
            schema_version=SCHEMA_VERSION,
            text_sha256=expected_hash,
            cues=tuple(_normalize_cue(cue, index=index) for index, cue in enumerate(cues)),
        )
    actual_hash = text_sha256(text)
    if plan.text_sha256 != actual_hash:
        raise FishInlineCueError(
            "fish_inline_text_changed",
            "Fish inline render plan no longer matches the canonical line text.",
        )
    _cue_insertions(str(text or ""), plan.cues)
    return plan


def _phrase_offset(text: str, phrase: str, occurrence: int) -> int:
    start = 0
    found = -1
    for _ in range(occurrence):
        found = text.find(phrase, start)
        if found < 0:
            raise FishInlineCueError(
                "fish_inline_phrase_not_found",
                f"Fish cue phrase {phrase!r} occurrence {occurrence} was not found.",
            )
        start = found + len(phrase)
    return found


def _cue_insertions(text: str, cues: tuple[FishInlineCue, ...]) -> list[tuple[int, int, FishInlineCue]]:
    insertions: list[tuple[int, int, FishInlineCue]] = []
    for order, cue in enumerate(cues):
        if cue.anchor == "start":
            offset = 0
        elif cue.anchor == "end":
            offset = len(text)
        else:
            assert cue.phrase is not None
            phrase_offset = _phrase_offset(text, cue.phrase, cue.occurrence)
            offset = (
                phrase_offset
                if cue.anchor == "before_phrase"
                else phrase_offset + len(cue.phrase)
            )
        insertions.append((offset, order, cue))
    return insertions


def _tag_text(cue: FishInlineCue) -> str:
    return f"[{cue.tag}]"


def compile_inline_text(
    text: str,
    raw_plan: Mapping[str, Any] | FishInlinePlan,
) -> tuple[str, FishInlinePlan]:
    canonical = str(text or "")
    plan = validate_plan(canonical, raw_plan)
    grouped: dict[int, list[tuple[int, FishInlineCue]]] = {}
    for offset, order, cue in _cue_insertions(canonical, plan.cues):
        grouped.setdefault(offset, []).append((order, cue))

    pieces: list[str] = []
    cursor = 0
    for offset in sorted(grouped):
        pieces.append(canonical[cursor:offset])
        cues = [cue for _, cue in sorted(grouped[offset], key=lambda item: item[0])]
        tags = " ".join(_tag_text(cue) for cue in cues)
        before = canonical[offset - 1 : offset] if offset > 0 else ""
        after = canonical[offset : offset + 1]
        if pieces and pieces[-1] and not pieces[-1][-1].isspace() and before not in {"", " ", "\n", "\t"}:
            pieces.append(" ")
        pieces.append(tags)
        if after and not after.isspace():
            pieces.append(" ")
        cursor = offset
    pieces.append(canonical[cursor:])
    rendered = "".join(pieces).strip()
    if normalize_spoken_text(strip_inline_tags(rendered)) != normalize_spoken_text(canonical):
        raise FishInlineCueError(
            "fish_inline_text_mutated",
            "Compiling Fish inline cues changed the canonical spoken text.",
        )
    return rendered, plan


def strip_inline_tags(value: str) -> str:
    return _TAG_PATTERN.sub("", str(value or ""))


def normalize_spoken_text(value: str) -> str:
    return " ".join(str(value or "").split())


def copy_plan(plan: Mapping[str, Any] | FishInlinePlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    if isinstance(plan, FishInlinePlan):
        return plan.as_dict()
    return copy.deepcopy(dict(plan))
