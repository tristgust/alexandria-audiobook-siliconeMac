from __future__ import annotations

from typing import Literal, TypedDict


class RecoveryLine(TypedDict, total=False):
    index: int
    speaker: str
    text: str
    instruct: str


class ExclusionEvidence(TypedDict, total=False):
    source_quote: str
    source_location: str
    start_char: int
    end_char: int
    passage_index: int | None
    entry_index: int
    batch_index: int
    category: str
    confidence: float
    basis: str


class ExcludedAudit(TypedDict, total=False):
    name: str
    reason: str
    evidence: list[ExclusionEvidence]


class RecoveryEntry(TypedDict, total=False):
    character_id: str
    canonical_name: str
    display_name: str
    script_voice_name: str | None


class _RecoveryCore(TypedDict):
    script_speaker: str
    display_name: str
    line_count: int
    sample_lines: list[RecoveryLine]
    sample_lines_truncated: bool
    excluded_audit: list[ExcludedAudit]


class EligibleSpeakerRecovery(_RecoveryCore):
    state: Literal["eligible"]
    blocked_reason: None
    eligible: Literal[True]
    active_character_id: None


class ActiveSpeakerRecovery(_RecoveryCore):
    state: Literal["active"]
    blocked_reason: None
    eligible: Literal[False]
    active_character_id: str


class NoAuditSpeakerRecovery(_RecoveryCore):
    state: Literal["blocked_no_audit"]
    blocked_reason: str
    eligible: Literal[False]
    active_character_id: None


class NoLinesSpeakerRecovery(_RecoveryCore):
    state: Literal["blocked_no_lines"]
    blocked_reason: str
    eligible: Literal[False]
    active_character_id: None


SpeakerRecovery = (
    EligibleSpeakerRecovery
    | ActiveSpeakerRecovery
    | NoAuditSpeakerRecovery
    | NoLinesSpeakerRecovery
)


class HistorySummary(TypedDict):
    operation_id: str
    operation: str
    at_utc: str
    affected_speakers: list[str]
    changed_script_indices: list[int]
    audio_invalidation_count: int
    source_script_fingerprint: str | None
    result_script_fingerprint: str | None
    undoes_operation_id: str | None
    undone: bool
    undoable: bool
    undo_blocked_reason: str | None


class SpeakerManagementStatusPayload(TypedDict, total=False):
    available: bool
    reason: str | None
    roster_fingerprint: str | None
    entries: list[RecoveryEntry]
    speaker_recovery: SpeakerRecovery | None
    history: list[HistorySummary]
    lines: list[RecoveryLine]
    selected_script_voice: str | None
    script_fingerprint: str
    entry_count: int
    speaker_counts: dict[str, int]
