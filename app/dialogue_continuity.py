from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


SPOKEN_CONTINUITY_CONTRACT_VERSION = 2

_REPORTING_VERBS = {
    "added",
    "announced",
    "answered",
    "asked",
    "began",
    "bellowed",
    "called",
    "continued",
    "cried",
    "declared",
    "demanded",
    "exclaimed",
    "gruffed",
    "growled",
    "hissed",
    "insisted",
    "laughed",
    "murmured",
    "muttered",
    "observed",
    "opined",
    "remarked",
    "replied",
    "said",
    "shouted",
    "sighed",
    "snapped",
    "stammered",
    "told",
    "whispered",
    "yelled",
}

_SPECIAL_SPEAKER_ALIASES = {
    "bernice": {"benny", "bernice"},
    "narrator benny": {"benny", "bernice", "i"},
    "the doctor": {"doctor"},
    "john smith": {"john", "smith"},
    "joan redfern": {"joan", "redfern"},
    "alexander shuttleworth": {"alexander", "shuttleworth"},
    "timothy dean": {"tim", "timothy", "dean"},
    "george rocastle": {"george", "rocastle"},
    "constance harding": {"constance", "harding"},
}

_OPEN_ENDINGS = (",", "—", "–", "-")
_TRAILING_ENDINGS = ("…", "...")
_TERMINAL_ENDINGS = ("!", "?")


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized_words(value: object) -> list[str]:
    return re.findall(r"[a-z0-9']+", _text(value).casefold())


def is_narrator(speaker: object) -> bool:
    return _text(speaker).casefold().startswith("narrator")


def _speaker_aliases(speaker: object) -> set[str]:
    normalized = " ".join(_normalized_words(speaker))
    aliases = set(_normalized_words(speaker))
    aliases.discard("the")
    aliases.discard("mr")
    aliases.discard("mrs")
    aliases.discard("miss")
    aliases.discard("dr")
    aliases.discard("doctor") if normalized != "the doctor" else None
    aliases.update(_SPECIAL_SPEAKER_ALIASES.get(normalized, set()))
    if normalized == "the doctor":
        aliases.add("doctor")
    return {value for value in aliases if len(value) > 1 or value == "i"}


def _first_sentence_or_clause(text: object) -> str:
    value = _text(text)
    if not value:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", value, maxsplit=1)[0]
    return sentence[:220]


def _contains_reporting_verb(value: str) -> bool:
    words = set(_normalized_words(value))
    return bool(words & _REPORTING_VERBS)


def is_attached_dialogue_tag(text: object, prior_speaker: object) -> bool:
    """Return True when narration starts as a tag attached to prior speech.

    This deliberately evaluates only the first sentence or clause. A later
    reporting verb in independent narration must not turn the whole chunk into
    a dialogue tag.
    """

    clause = _first_sentence_or_clause(text)
    words = _normalized_words(clause)
    if not words or not _contains_reporting_verb(clause):
        return False

    first = words[0]
    if first in _REPORTING_VERBS:
        # Inverted tags: "said Smith", "shouted the Doctor".
        return True

    reporting_positions = [
        index for index, word in enumerate(words[:18]) if word in _REPORTING_VERBS
    ]
    if not reporting_positions:
        return False
    first_reporting = reporting_positions[0]

    if first in {"he", "she", "i", "they", "we"}:
        return first_reporting <= 8
    if words[:2] in (["a", "voice"], ["the", "voice"]):
        return first_reporting <= 8

    aliases = _speaker_aliases(prior_speaker)
    subject_window = set(words[: min(first_reporting + 1, 8)])
    return bool(aliases & subject_window)


def _ending_kind(text: object) -> str:
    value = _text(text).rstrip('"\'”’')
    if value.endswith(_TRAILING_ENDINGS):
        return "trailing"
    if value.endswith("!"):
        return "exclamation"
    if value.endswith("?"):
        return "question"
    if value.endswith(_OPEN_ENDINGS):
        return "open"
    if value.endswith("."):
        return "period"
    return "none"


def _context(
    role: str,
    instruction: str,
    *,
    boundary_before: str,
    boundary_after: str,
    punctuation: str,
    suggested_pause_after_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": SPOKEN_CONTINUITY_CONTRACT_VERSION,
        "role": role,
        "boundary_before": boundary_before,
        "boundary_after": boundary_after,
        "preceding_or_terminal_punctuation": punctuation,
        "suggested_pause_after_ms": suggested_pause_after_ms,
        "instruction": instruction,
    }


def resolve_spoken_continuity(
    chunks: Sequence[Mapping[str, Any]],
    index: int,
) -> dict[str, Any] | None:
    if not 0 <= index < len(chunks):
        return None

    current = chunks[index]
    current_speaker = _text(current.get("speaker"))
    current_text = _text(current.get("text"))
    previous = chunks[index - 1] if index > 0 else None
    following = chunks[index + 1] if index + 1 < len(chunks) else None

    # Narrator attribution inserted inside one character's quoted sentence.
    if previous and following and is_narrator(current_speaker):
        previous_speaker = _text(previous.get("speaker"))
        following_speaker = _text(following.get("speaker"))
        if (
            previous_speaker
            and previous_speaker == following_speaker
            and not is_narrator(previous_speaker)
            and _ending_kind(previous.get("text")) in {"open", "trailing"}
            and _ending_kind(current_text) == "open"
            and is_attached_dialogue_tag(current_text, previous_speaker)
        ):
            return _context(
                "parenthetical_attribution_between_dialogue",
                (
                    "Spoken continuity: begin mid-sentence as a parenthetical "
                    "dialogue attribution attached to the preceding quoted speech. "
                    "Use a low-reset, lightly subordinated onset rather than a new-"
                    "sentence or new-paragraph opening. Keep the ending open because "
                    "the same character resumes the quoted sentence immediately after."
                ),
                boundary_before="attached",
                boundary_after="open",
                punctuation=_ending_kind(previous.get("text")),
                suggested_pause_after_ms=130,
            )

    # Character speech immediately before an attached narrator tag.
    if following and not is_narrator(current_speaker) and is_narrator(following.get("speaker")):
        if is_attached_dialogue_tag(following.get("text"), current_speaker):
            ending = _ending_kind(current_text)
            if ending == "open":
                return _context(
                    "dialogue_open_before_attribution",
                    (
                        "Spoken continuity: this quoted speech is not sentence-final. "
                        "End with an open, suspended cadence that hands directly to an "
                        "attached narrator attribution; do not sound complete."
                    ),
                    boundary_before="normal",
                    boundary_after="open",
                    punctuation=ending,
                    suggested_pause_after_ms=130,
                )
            if ending == "trailing":
                return _context(
                    "dialogue_trailing_before_attribution",
                    (
                        "Spoken continuity: preserve the authored trailing or hesitant "
                        "ellipsis, then hand directly to the attached narrator tag. Do "
                        "not replace the ellipsis with an ordinary sentence ending."
                    ),
                    boundary_before="normal",
                    boundary_after="trailing",
                    punctuation=ending,
                    suggested_pause_after_ms=160,
                )
            if ending in {"question", "exclamation"}:
                punctuation_name = "question" if ending == "question" else "exclamation"
                return _context(
                    "dialogue_terminal_before_attribution",
                    (
                        f"Spoken continuity: this quoted speech ends with a full "
                        f"{punctuation_name} cadence. A narrator tag follows, but do "
                        "not trail off, flatten, or weaken the terminal punctuation."
                    ),
                    boundary_before="normal",
                    boundary_after="terminal",
                    punctuation=ending,
                    suggested_pause_after_ms=180,
                )

    # Narrator tag attached to the immediately preceding speech.
    if previous and is_narrator(current_speaker) and not is_narrator(previous.get("speaker")):
        previous_speaker = _text(previous.get("speaker"))
        if is_attached_dialogue_tag(current_text, previous_speaker):
            ending = _ending_kind(previous.get("text"))
            if ending in {"question", "exclamation"}:
                return _context(
                    "attached_attribution_after_terminal_dialogue",
                    (
                        "Spoken continuity: begin as a grammatically attached dialogue "
                        "tag after a completed question or exclamation. Use a low-reset, "
                        "lightly subordinated onset rather than a new-sentence or new-"
                        "paragraph opening. Continue normally only after any real "
                        "sentence boundary inside this narrator chunk."
                    ),
                    boundary_before="attached_after_terminal",
                    boundary_after="normal",
                    punctuation=ending,
                )
            if ending in {"open", "trailing"}:
                return _context(
                    "attached_attribution_after_open_dialogue",
                    (
                        "Spoken continuity: begin mid-sentence as an attached dialogue "
                        "tag continuing directly from the preceding quoted speech. Use "
                        "a low-reset, unstressed onset; do not sound like a fresh "
                        "sentence or paragraph."
                    ),
                    boundary_before="attached",
                    boundary_after=(
                        "open" if _ending_kind(current_text) == "open" else "normal"
                    ),
                    punctuation=ending,
                    suggested_pause_after_ms=(
                        130 if _ending_kind(current_text) == "open" else None
                    ),
                )

    # Character resumes the quoted sentence after a narrator parenthetical.
    if previous and index >= 2 and not is_narrator(current_speaker) and is_narrator(previous.get("speaker")):
        before_previous = chunks[index - 2]
        if (
            _text(before_previous.get("speaker")) == current_speaker
            and _ending_kind(previous.get("text")) == "open"
            and is_attached_dialogue_tag(previous.get("text"), current_speaker)
            and _ending_kind(before_previous.get("text")) in {"open", "trailing"}
        ):
            return _context(
                "dialogue_resume_after_attribution",
                (
                    "Spoken continuity: resume the same quoted sentence after a "
                    "parenthetical narrator attribution. Enter without a fresh-sentence "
                    "reset or paragraph-opening emphasis, while preserving the authored "
                    "punctuation at this chunk's end."
                ),
                boundary_before="resume",
                boundary_after="authored",
                punctuation=_ending_kind(current_text),
            )

    return None


def effective_delivery_instruction(
    authored_instruction: object,
    continuity: Mapping[str, Any] | None,
) -> str:
    authored = _text(authored_instruction)
    supplemental = _text((continuity or {}).get("instruction"))
    if authored and supplemental and supplemental.casefold() in authored.casefold():
        return authored
    if authored and supplemental:
        return f"{authored} {supplemental}"
    return authored or supplemental


_CONTINUATION_TEXT_ROLES = {
    "parenthetical_attribution_between_dialogue",
    "attached_attribution_after_open_dialogue",
    "attached_attribution_after_terminal_dialogue",
    "dialogue_resume_after_attribution",
}


def continuity_synthesis_text(
    authored_text: object,
    continuity: Mapping[str, Any] | None,
) -> str:
    """Add a synthesis-only grammatical continuation cue.

    The authored Script text remains unchanged. Some TTS backends treat a
    capitalized chunk boundary as a fresh sentence even when the delivery
    instruction says otherwise. A leading comma and lowercase initial provide
    an acoustic continuation cue for attached attributions and resumed speech.
    """

    text = _text(authored_text)
    role = _text((continuity or {}).get("role"))
    if not text or role not in _CONTINUATION_TEXT_ROLES:
        return text
    match = re.search(r"[A-Za-z]", text)
    if match is None:
        return text
    index = match.start()
    continued = text[:index] + text[index].lower() + text[index + 1 :]
    return f", {continued.lstrip()}"


def effective_pause_after_ms(chunk: Mapping[str, Any]) -> int | None:
    if "pause_after" in chunk and chunk.get("pause_after") is not None:
        return max(0, int(chunk.get("pause_after")))
    continuity = chunk.get("spoken_continuity")
    if not isinstance(continuity, Mapping):
        return None
    if not (
        chunk.get("spoken_continuity_binding_enabled") is True
        or chunk.get("spoken_continuity_applied") is not None
    ):
        return None
    value = continuity.get("suggested_pause_after_ms")
    return max(0, int(value)) if value is not None else None
