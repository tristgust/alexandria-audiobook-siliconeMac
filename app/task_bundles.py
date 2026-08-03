from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from chatgpt_handoff import (
    HandoffConflictError,
    HandoffValidationError,
    MAX_JSON_DEPTH,
    MAX_MEMBER_BYTES,
    MAX_RESULT_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
)
from generation_state import fingerprint_text, fingerprint_value
from llm_schemas import ContractValidationError, get_schema, validate_contract


TASK_BUNDLE_SCHEMA_VERSION = 2
TASK_COMPLETION_SCHEMA_VERSION = 2
TASK_GUIDANCE_SCHEMA_VERSION = 1
DEFAULT_RESULT_PATH = "result/result.json"
COMPLETION_PATH = "result/completion.json"
TASK_MANIFEST_PATH = "manifest.json"
TASK_INSTRUCTIONS_PATH = "instructions.md"
TASK_INPUT_PATH = "input.json"
TASK_SCHEMA_PATH = "schema.json"
TASK_CHECKSUMS_PATH = "checksums.json"
TASK_GUIDANCE_PATH = "guidance/task-guidance.md"
GUIDANCE_MANIFEST_PATH = "guidance/voice-reference.json"
SOURCE_POLICY_PATH = "guidance/source-policy.md"
NONHUMAN_GUIDANCE_PATH = "guidance/nonhuman-speakers.md"
PERSONA_GUIDANCE_PATH = "guidance/persona.md"
VOICE_IDENTITY_GUIDANCE_PATH = "guidance/voice-identity.md"
LINE_DIRECTION_GUIDANCE_PATH = "guidance/line-direction.md"
CAST_DOSSIER_GUIDANCE_PATH = "guidance/cast-dossier.md"
GUIDANCE_ROOT = Path(__file__).resolve().parent / "task_guidance"
SAFE_TASK_ID = re.compile(r"^task_[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class TaskDependencyPolicy:
    source: str
    artifact_keys: frozenset[str]
    stale_behavior: str = "reject"


@dataclass(frozen=True)
class TaskTransfer:
    handler: str
    supported: bool = False
    action_label: str = "Native transfer unavailable"
    tab: str | None = None


@dataclass(frozen=True)
class TaskDefinition:
    task_type: str
    label: str
    contract: str
    stage: str
    native_destination: str
    transfer_policy: str
    target_kind: str | None
    guidance_profile: str | None
    text_mutation_permitted: bool
    purpose: str
    input_builder: str
    dependency_policy: TaskDependencyPolicy
    result_validator: str
    transfer: TaskTransfer
    legacy_v1_supported: bool
    required_input: frozenset[str]
    allowed_input: frozenset[str]
    instructions: str


_SCRIPT_INPUT = frozenset(
    {
        "source_text",
        "part_number",
        "part_count",
        "previous_entries",
        "source_context",
        "generation_constraints",
    }
)
_REVIEW_INPUT = frozenset(
    {
        "entries",
        "context_before",
        "context_after",
        "review_constraints",
        "current_personas",
        "source_context",
    }
)
_PERSONA_INPUT = frozenset(
    {
        "speaker",
        "sample_lines",
        "narrator_context",
        "roster_entry",
        "existing_persona",
        "current_voice_assignment",
        "current_voice_mode",
        "evidence",
        "source_locations",
        "unresolved_questions",
        "advanced",
    }
)
_PERSONA_CATALOG_INPUT = frozenset(
    {
        "speakers",
        "source_context",
    }
)
_COMPLETE_CAST_DOSSIER_INPUT = frozenset(
    {
        "requested_sections",
        "source_text",
        "source_context",
        "script_speakers",
        "existing_roster",
        "current_voice_assignments",
    }
)
_BACKEND_RENDER_PLAN_INPUT = frozenset(
    {
        "script_fingerprint",
        "chunks_fingerprint",
        "chunks",
        "backend_guidance",
        "source_context",
    }
)
_PRONUNCIATION_GUIDANCE_INPUT = frozenset(
    {
        "schema_version",
        "script_fingerprint",
        "chunks_fingerprint",
        "registry_fingerprint",
        "chunks",
        "existing_entries",
        "source_context",
    }
)


def _dependencies(
    *artifact_keys: str,
    source: str = "required",
    stale_behavior: str = "reject",
) -> TaskDependencyPolicy:
    return TaskDependencyPolicy(
        source=source,
        artifact_keys=frozenset(artifact_keys),
        stale_behavior=stale_behavior,
    )


def _transfer(
    handler: str,
    *,
    supported: bool = False,
    action_label: str = "Native transfer unavailable",
    tab: str | None = None,
) -> TaskTransfer:
    return TaskTransfer(
        handler=handler,
        supported=supported,
        action_label=action_label,
        tab=tab,
    )


def _task(
    task_type: str,
    label: str,
    contract: str,
    stage: str,
    destination: str,
    transfer_policy: str,
    *,
    input_builder: str,
    dependency_policy: TaskDependencyPolicy,
    transfer: TaskTransfer,
    target_kind: str | None = None,
    guidance_profile: str | None = None,
    text_mutation_permitted: bool = False,
    purpose: str | None = None,
    result_validator: str = "native_contract",
    legacy_v1_supported: bool = False,
    required_input: Iterable[str],
    allowed_input: Iterable[str],
    instructions: str,
) -> TaskDefinition:
    return TaskDefinition(
        task_type=task_type,
        label=label,
        contract=contract,
        stage=stage,
        native_destination=destination,
        transfer_policy=transfer_policy,
        target_kind=target_kind,
        guidance_profile=guidance_profile,
        text_mutation_permitted=text_mutation_permitted,
        purpose=(purpose or instructions).strip(),
        input_builder=input_builder,
        dependency_policy=dependency_policy,
        result_validator=result_validator,
        transfer=transfer,
        legacy_v1_supported=legacy_v1_supported,
        required_input=frozenset(required_input),
        allowed_input=frozenset(allowed_input),
        instructions=instructions,
    )


def _build_task_registry(
    definitions: Iterable[TaskDefinition],
) -> dict[str, TaskDefinition]:
    registry: dict[str, TaskDefinition] = {}
    valid_sources = {"none", "tracked_if_present", "required"}
    valid_stale_behaviors = {"reject"}
    for definition in definitions:
        if definition.task_type in registry:
            raise ValueError(
                f"Duplicate Alexandria task definition: {definition.task_type!r}."
            )
        if not definition.required_input.issubset(definition.allowed_input):
            raise ValueError(
                f"Task {definition.task_type!r} requires fields outside its allowlist."
            )
        if definition.dependency_policy.source not in valid_sources:
            raise ValueError(
                f"Task {definition.task_type!r} has an invalid source dependency policy."
            )
        if definition.dependency_policy.stale_behavior not in valid_stale_behaviors:
            raise ValueError(
                f"Task {definition.task_type!r} has an invalid stale-result policy."
            )
        if definition.result_validator != "native_contract":
            raise ValueError(
                f"Task {definition.task_type!r} has an unsupported result validator."
            )
        if not definition.input_builder or not definition.transfer.handler:
            raise ValueError(
                f"Task {definition.task_type!r} is missing registry routing metadata."
            )
        if definition.transfer.supported and (
            not definition.transfer.action_label
            or not definition.transfer.tab
        ):
            raise ValueError(
                f"Task {definition.task_type!r} has an incomplete native transfer."
            )
        get_schema(definition.contract)
        registry[definition.task_type] = definition
    return registry


_TASK_DEFINITIONS: tuple[TaskDefinition, ...] = (
        _task(
            "script_generation",
            "Generate annotated Script",
            "script",
            "script",
            "script_review",
            "script_candidate",
            input_builder="script_generation",
            dependency_policy=_dependencies(source="required"),
            transfer=_transfer("script_candidate"),
            legacy_v1_supported=True,
            required_input={"source_text"},
            allowed_input=_SCRIPT_INPUT,
            instructions=(
                "Convert the supplied source into a complete Alexandria audiobook "
                "Script. Preserve every source word in spoken order, use NARRATOR "
                "for non-dialogue, keep canonical uppercase speaker labels, and "
                "provide one concise performable instruct value per entry."
            ),
        ),
        _task(
            "script_review",
            "Review annotated Script",
            "review",
            "script",
            "script_review",
            "script_candidate",
            input_builder="script_review",
            dependency_policy=_dependencies(
                "annotated_script",
                "voice_config",
                source="required",
            ),
            transfer=_transfer("script_candidate"),
            legacy_v1_supported=True,
            required_input={"entries"},
            allowed_input=_REVIEW_INPUT,
            instructions=(
                "Review speaker boundaries and structural assignments without "
                "rewriting, adding, omitting, or reordering spoken wording. Return "
                "every changed and unchanged entry."
            ),
        ),
        _task(
            "complete_cast_dossier",
            "Create complete Cast dossier",
            "complete_cast_dossier",
            "cast_dossier",
            "cast_dossier_review",
            "cast_dossier_package",
            input_builder="complete_cast_dossier",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "cast_dossier_package",
                supported=True,
                action_label="Review complete Cast dossier",
                tab="characters",
            ),
            guidance_profile="cast_dossier",
            purpose=(
                "Create one selectable Cast package containing a source-evidenced "
                "roster and relationships, detailed Voice personas and synthesis-ready "
                "Designed Voice definitions, and source-supported visual dossiers."
            ),
            required_input={
                "requested_sections",
                "source_text",
                "source_context",
                "script_speakers",
            },
            allowed_input=_COMPLETE_CAST_DOSSIER_INPUT,
            instructions=(
                "Complete every section enabled in input.requested_sections and set "
                "every disabled section to null. For roster_and_relationships, "
                "discover all identities and build the most complete source-supported "
                "relationship record possible without inventing facts. For "
                "voice_personas_and_designs, return every supplied Script speaker "
                "exactly once. Separate character persona from acoustic design; make "
                "designed_voice_description a compact synthesis-ready persistent Voice "
                "definition. For each acoustic trait, mark whether it is explicit, "
                "inferred, a casting recommendation, or unknown, and quote evidence for "
                "explicit or inferred claims. Casting recommendations are allowed only "
                "when labeled as recommendations, never as source facts. ref_text must "
                "be one exact supplied sample line for that speaker. For visual_dossiers, "
                "extract source-supported observations and compile them into dossiers "
                "with stable traits, scene variants, conflicts, and unknowns. Use the "
                "returned roster identity_seed, canonical_name, or display_name as each "
                "visual character_id. Coordinate all sections so aliases, relationships, "
                "Voice personas, and visual dossiers refer to the same identities."
            ),
        ),
        _task(
            "roster_discovery",
            "Discover source-evidenced roster",
            "roster_discovery",
            "roster",
            "character_roster",
            "roster_observations",
            input_builder="roster_discovery",
            dependency_policy=_dependencies(source="required"),
            transfer=_transfer(
                "roster_discovery",
                supported=True,
                action_label="Send observations to Character roster",
                tab="characters",
            ),
            legacy_v1_supported=True,
            required_input={"source_passage"},
            allowed_input={
                "source_passage",
                "passage_number",
                "passage_count",
                "existing_observations",
            },
            instructions=(
                "Discover every person, speaking entity, narrator role, group, and "
                "recurring named non-speaker across the whole source. Capture "
                "evidence-backed names, aliases, titles, roles, pronouns, species or "
                "type, speaking status, and relationships. Return observations only; "
                "do not merge or approve identities during discovery."
            ),
        ),
        _task(
            "roster_reconciliation",
            "Reconcile and enrich full Character roster",
            "roster_reconciliation",
            "roster",
            "character_roster",
            "roster_draft",
            input_builder="roster_reconciliation",
            dependency_policy=_dependencies(
                "roster_discovery_state",
                "character_roster",
                "character_roster_draft",
                source="required",
            ),
            transfer=_transfer(
                "roster_reconciliation",
                supported=True,
                action_label="Create roster draft for review",
                tab="characters",
            ),
            legacy_v1_supported=True,
            required_input={"observations"},
            allowed_input={
                "observations",
                "source_summary",
                "existing_roster",
            },
            instructions=(
                "Reconcile every supplied observation into canonical candidates for "
                "the full roster. Preserve evidence, aliases, titles, roles, "
                "relationships, speaking status, uncertainty, exclusions, groups, "
                "and duplicate candidates, and account for every observation ID."
            ),
        ),
        _task(
            "persona_catalog_generation",
            "Create voice profiles for all speaking identities",
            "persona_catalog",
            "persona",
            "expressive_voices",
            "persona_catalog_drafts",
            input_builder="persona_catalog_generation",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_catalog",
                supported=True,
                action_label="Review all Persona drafts",
                tab="voice-projects",
            ),
            guidance_profile="persona",
            purpose=(
                "Normal first-run choice: create one draft Voice profile for every "
                "speaker in the current Script using the approved Character roster."
            ),
            required_input={"speakers"},
            allowed_input=_PERSONA_CATALOG_INPUT,
            instructions=(
                "Create one source-backed persistent Persona for every supplied "
                "speaker. Return every speaker exactly once, preserve each canonical "
                "uppercase label, keep stable acoustic identity separate from line "
                "delivery, and choose exact representative ref_text from that "
                "speaker's supplied sample lines."
            ),
        ),
        _task(
            "persona_generation",
            "Create one Voice profile",
            "persona",
            "persona",
            "expressive_voices",
            "persona_draft",
            input_builder="persona_single",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_single",
                supported=True,
                action_label="Review Persona proposal",
                tab="voice-projects",
            ),
            legacy_v1_supported=True,
            target_kind="speaker",
            guidance_profile="persona",
            purpose="Repair option: create a draft Voice profile for one selected speaker.",
            required_input={"speaker", "sample_lines"},
            allowed_input=_PERSONA_INPUT,
            instructions=(
                "Create one source-backed persistent Persona for the selected "
                "speaker, including a concise stable description and exact "
                "representative ref_text."
            ),
        ),
        _task(
            "persona_refinement",
            "Refine one Voice profile",
            "persona",
            "persona",
            "expressive_voices",
            "persona_draft",
            input_builder="persona_single",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_single",
                supported=True,
                action_label="Review Persona refinement",
                tab="voice-projects",
            ),
            target_kind="speaker",
            guidance_profile="persona",
            purpose="Repair option: improve one existing Voice profile without changing the roster or voice assignment.",
            required_input={"speaker", "sample_lines", "existing_persona"},
            allowed_input=_PERSONA_INPUT,
            instructions=(
                "Refine the existing Persona only where the supplied evidence "
                "supports a clearer persistent identity or safer representative "
                "reference passage. Preserve source wording in ref_text."
            ),
        ),
        _task(
            "persona_reconciliation",
            "Reconcile one Voice profile after roster changes",
            "persona",
            "persona",
            "expressive_voices",
            "persona_draft",
            input_builder="persona_single",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_single",
                supported=True,
                action_label="Reconcile Persona proposal",
                tab="voice-projects",
            ),
            target_kind="speaker",
            guidance_profile="persona",
            purpose="Use after Character roster identity or alias changes to bring one Voice profile back into alignment.",
            required_input={"speaker", "sample_lines", "roster_entry"},
            allowed_input=_PERSONA_INPUT,
            instructions=(
                "Resolve the proposed Persona against the canonical roster identity, "
                "aliases, existing Persona, and current voice context. Return one "
                "reviewable Persona candidate without changing assignments."
            ),
        ),
        _task(
            "persona_audit",
            "Audit one Voice profile",
            "persona",
            "persona",
            "expressive_voices",
            "persona_draft",
            input_builder="persona_single",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_single",
                supported=True,
                action_label="Review Persona audit",
                tab="voice-projects",
            ),
            target_kind="speaker",
            guidance_profile="persona",
            purpose="Check one existing Voice profile for unsupported traits, unstable wording, or unsuitable reference text.",
            required_input={"speaker", "sample_lines", "existing_persona"},
            allowed_input=_PERSONA_INPUT,
            instructions=(
                "Audit the existing Persona for unsupported identity claims, "
                "unstable acoustic wording, unsuitable or altered reference text, "
                "and roster mismatch. Return the safest corrected candidate for "
                "native comparison; do not change voice assignments."
            ),
        ),
        _task(
            "visual_discovery",
            "Discover visual evidence",
            "visual_discovery",
            "visual",
            "visual_dossiers",
            "visual_observations",
            input_builder="visual_discovery",
            dependency_policy=_dependencies(
                "character_roster",
                "character_roster_draft",
                source="required",
            ),
            transfer=_transfer(
                "visual_discovery",
                supported=True,
                action_label="Review visual observations",
                tab="characters",
            ),
            legacy_v1_supported=True,
            target_kind="character",
            required_input={"roster_entry", "source_passage"},
            allowed_input={
                "roster_entry",
                "source_passage",
                "existing_dossier",
                "passage_number",
                "passage_count",
            },
            instructions=(
                "Extract only source-supported visual facts for the selected roster "
                "entry. Distinguish stable traits, clothing, temporary state, "
                "setting, evidence, and uncertainty."
            ),
        ),
        _task(
            "visual_reconciliation",
            "Compile visual dossier",
            "visual_reconciliation",
            "visual",
            "visual_dossiers",
            "visual_dossier_review",
            input_builder="visual_reconciliation",
            dependency_policy=_dependencies(
                "character_roster",
                "visual_discovery_state",
                source="required",
            ),
            transfer=_transfer(
                "visual_reconciliation",
                supported=True,
                action_label="Review compiled visual dossiers",
                tab="characters",
            ),
            target_kind="character",
            required_input={"observations", "approved_roster"},
            allowed_input={
                "observations",
                "approved_roster",
                "existing_dossiers",
                "source_summary",
            },
            instructions=(
                "Compile the supplied visual observations into Alexandria visual "
                "dossiers. Preserve stable facts, scene variants, conflicts, "
                "unknowns, and exact evidence without inventing detail."
            ),
        ),
        _task(
            "persistent_voice_description_generation",
            "Create acoustic identity for one speaker",
            "persona",
            "persona_advanced",
            "expressive_voices",
            "persona_draft",
            input_builder="persona_single",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_single",
                supported=True,
                action_label="Review voice description",
                tab="voice-projects",
            ),
            target_kind="speaker",
            guidance_profile="voice_identity",
            purpose="Advanced repair: generate only the stable acoustic-identity portion of one Voice profile.",
            required_input={"speaker", "sample_lines"},
            allowed_input=_PERSONA_INPUT,
            instructions=(
                "Create a stable acoustic voice description for the selected "
                "speaker and choose exact representative ref_text. The description "
                "must contain identity rather than line delivery."
            ),
        ),
        _task(
            "persistent_voice_description_refinement",
            "Refine acoustic identity for one speaker",
            "persona",
            "persona_advanced",
            "expressive_voices",
            "persona_draft",
            input_builder="persona_single",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_single",
                supported=True,
                action_label="Review voice-description refinement",
                tab="voice-projects",
            ),
            target_kind="speaker",
            guidance_profile="voice_identity",
            purpose="Advanced repair: refine only the acoustic-identity wording inside one existing Voice profile.",
            required_input={"speaker", "sample_lines", "existing_persona"},
            allowed_input=_PERSONA_INPUT,
            instructions=(
                "Refine the existing persistent voice description using only "
                "supported acoustic evidence. Remove transient performance wording "
                "and preserve exact source-backed ref_text."
            ),
        ),
        _task(
            "persistent_voice_description_audit",
            "Audit acoustic identity for one speaker",
            "persona",
            "persona_advanced",
            "expressive_voices",
            "persona_draft",
            input_builder="persona_single",
            dependency_policy=_dependencies(
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "voice_config",
                source="required",
            ),
            transfer=_transfer(
                "persona_single",
                supported=True,
                action_label="Review voice-description audit",
                tab="voice-projects",
            ),
            target_kind="speaker",
            guidance_profile="voice_identity",
            purpose="Advanced repair: audit only the acoustic-identity wording inside one Voice profile.",
            required_input={"speaker", "sample_lines", "existing_persona"},
            allowed_input=_PERSONA_INPUT,
            instructions=(
                "Audit the current persistent description for unsupported anatomy, "
                "age, gender, accent, contradictory acoustics, or line-specific "
                "performance direction. Return a corrected review candidate."
            ),
        ),
        _task(
            "backend_render_plan_generation",
            "Create Qwen and Fish delivery plan",
            "backend_render_plan",
            "script",
            "script_review",
            "backend_render_plan",
            input_builder="backend_render_plan",
            dependency_policy=_dependencies(
                "annotated_script",
                "chunks",
                source="tracked_if_present",
            ),
            transfer=_transfer(
                "backend_render_plan",
                supported=True,
                action_label="Apply delivery plan to Script",
                tab="script",
            ),
            purpose=(
                "Create one fingerprint-bound synthesis plan for every current chunk, "
                "with a Qwen-optimized whole-line instruction and a Fish S2.1 global "
                "direction plus sparse inline cue anchors."
            ),
            required_input={
                "script_fingerprint",
                "chunks_fingerprint",
                "chunks",
                "backend_guidance",
            },
            allowed_input=_BACKEND_RENDER_PLAN_INPUT,
            instructions=(
                "Return every supplied chunk exactly once in the same index order. "
                "Copy index, chunk_id, speaker, text_sha256, script_fingerprint, and "
                "chunks_fingerprint exactly. Never rewrite or return spoken text. For "
                "qwen_instruction, write one concise whole-line actor direction suited "
                "to Qwen3-TTS: immediate objective, emotion, pacing, emphasis, and "
                "restraint only where audible. Derive fish_direction and fish_cues from "
                "that same accepted Qwen performance intent; translate it into a shorter "
                "acoustically concrete Fish plan without inventing a different reading. "
                "Add Fish cues only when a "
                "local change is materially useful; use exact case-sensitive phrase "
                "anchors from the supplied canonical text, keep cues sparse, prefer "
                "documented or established tags, and add reset cues after temporary "
                "effects when needed. Preserve punctuation and spoken-continuity roles. "
                "Put uncertainty or unavoidable limitations in entry warnings or the "
                "top-level warnings array."
            ),
        ),
        _task(
            "pronunciation_guidance",
            "Create pronunciation and name guidance",
            "pronunciation_guidance",
            "pronunciation",
            "pronunciation_registry",
            "pronunciation_candidates",
            input_builder="pronunciation_guidance",
            dependency_policy=_dependencies(
                "annotated_script",
                "chunks",
                "pronunciation_registry",
                source="tracked_if_present",
            ),
            transfer=_transfer(
                "pronunciation_guidance",
                supported=True,
                action_label="Review pronunciation guidance",
                tab="script",
            ),
            purpose=(
                "Find names, invented terms, foreign words, and other exact Script "
                "occurrences that may need reviewed synthesis-only pronunciation guidance."
            ),
            required_input={
                "schema_version",
                "script_fingerprint",
                "chunks_fingerprint",
                "registry_fingerprint",
                "chunks",
            },
            allowed_input=_PRONUNCIATION_GUIDANCE_INPUT,
            instructions=(
                "Return only pronunciation candidates that materially help synthesis. "
                "Copy chunk_index and chunk_text_sha256 exactly. Choose only a case-sensitive "
                "substring from the supplied chunk text, and report its exact start_char, "
                "end_char, and original spelling. Do not "
                "rewrite Script text, invent a different source spelling, overlap "
                "candidate spans, or return approval state or pronunciation IDs. "
                "Provide a spoken_form, a phonetic_hint, or both; keep engine, language, "
                "character, and Voice limits empty unless the evidence requires them. "
                "Imported results remain draft review candidates until the user previews "
                "and explicitly accepts them in Alexandria's pronunciation registry."
            ),
        ),
        _task(
            "line_direction_generation",
            "Create line directions",
            "review",
            "editor",
            "editor",
            "line_direction_review",
            input_builder="line_direction",
            dependency_policy=_dependencies(
                "annotated_script",
                "voice_config",
                source="required",
            ),
            transfer=_transfer("line_direction_review"),
            guidance_profile="line_direction",
            required_input={"entries"},
            allowed_input=_REVIEW_INPUT,
            instructions=(
                "Keep every speaker and spoken word exact. Create or improve only "
                "the immediate performable instruct value for each supplied line."
            ),
        ),
        _task(
            "line_direction_audit",
            "Audit line directions",
            "review",
            "editor",
            "editor",
            "line_direction_review",
            input_builder="line_direction",
            dependency_policy=_dependencies(
                "annotated_script",
                "voice_config",
                source="required",
            ),
            transfer=_transfer("line_direction_review"),
            guidance_profile="line_direction",
            required_input={"entries"},
            allowed_input=_REVIEW_INPUT,
            instructions=(
                "Keep every speaker and spoken word exact. Audit only instruct "
                "values for generic templates, identity restatement, unsupported "
                "acting inventions, and unclear or unperformable direction."
            ),
        ),
)

TASK_REGISTRY: dict[str, TaskDefinition] = _build_task_registry(
    _TASK_DEFINITIONS
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _contains_unsafe_control(value: str) -> bool:
    return any(
        (
            ord(character) < 32
            and character not in {"\t", "\n", "\r"}
        )
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _safe_json_value(value: Any, *, path: str = "$", depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise HandoffValidationError(
            "json_too_deep",
            f"{path} exceeds the maximum JSON nesting depth.",
        )
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HandoffValidationError(
                "invalid_number",
                f"{path} contains a non-finite number.",
            )
        return value
    if isinstance(value, str):
        if _contains_unsafe_control(value):
            raise HandoffValidationError(
                "unsafe_text",
                f"{path} contains an unsupported control character.",
            )
        return value
    if isinstance(value, list):
        return [
            _safe_json_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise HandoffValidationError(
                    "invalid_json_key",
                    f"{path} contains a non-text or empty key.",
                )
            folded = key.casefold().replace("-", "_").replace(" ", "_")
            if any(
                fragment in folded
                for fragment in (
                    "api_key",
                    "apikey",
                    "authorization",
                    "access_token",
                    "refresh_token",
                    "password",
                    "secret",
                    "cookie",
                    "token_path",
                    "hf_token",
                    "openai_key",
                )
            ):
                raise HandoffValidationError(
                    "sensitive_field",
                    f"{path}.{key} is not permitted in a task bundle.",
                )
            normalized[key] = _safe_json_value(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return normalized
    raise HandoffValidationError(
        "non_json_value",
        f"{path} contains a value that cannot be represented in JSON.",
    )


def get_task_definition(task_type: str) -> TaskDefinition:
    definition = TASK_REGISTRY.get(str(task_type or "").strip())
    if definition is None:
        raise HandoffValidationError(
            "unsupported_task",
            f"Unsupported Alexandria task: {task_type!r}.",
        )
    return definition


def get_task_schema(task: str | TaskDefinition) -> dict[str, Any]:
    definition = task if isinstance(task, TaskDefinition) else get_task_definition(task)
    return copy.deepcopy(get_schema(definition.contract))


def get_task_transfer_contract(task_type: str) -> dict[str, Any]:
    definition = get_task_definition(task_type)
    transfer = definition.transfer
    return {
        "supported": transfer.supported,
        "destination": definition.native_destination if transfer.supported else None,
        "label": transfer.action_label,
        "tab": transfer.tab,
    }


def task_definition_contract(task: str | TaskDefinition) -> dict[str, Any]:
    definition = task if isinstance(task, TaskDefinition) else get_task_definition(task)
    schema = get_task_schema(definition)
    transfer = get_task_transfer_contract(definition.task_type)
    return {
        "task_type": definition.task_type,
        "label": definition.label,
        "purpose": definition.purpose,
        "stage": definition.stage,
        "target_kind": definition.target_kind,
        "schema": {
            "contract": definition.contract,
            "fingerprint": fingerprint_value(schema),
        },
        "minimized_input": {
            "builder": definition.input_builder,
            "required": sorted(definition.required_input),
            "allowed": sorted(definition.allowed_input),
        },
        "dependencies": {
            "source": definition.dependency_policy.source,
            "artifacts": sorted(definition.dependency_policy.artifact_keys),
            "fingerprint": "sha256",
        },
        "guidance": {
            "profile": definition.guidance_profile or "source_only",
            "schema_version": TASK_GUIDANCE_SCHEMA_VERSION,
        },
        "validator": {
            "kind": definition.result_validator,
            "contract": definition.contract,
        },
        "native_destination": definition.native_destination,
        "transfer_policy": definition.transfer_policy,
        "transfer_handler": definition.transfer.handler,
        "native_transfer": transfer,
        "stale_result": {
            "behavior": definition.dependency_policy.stale_behavior,
            "source_error": "stale_source",
            "artifact_error": "stale_artifact",
        },
        "legacy_v1_supported": definition.legacy_v1_supported,
        "text_mutation_permitted": definition.text_mutation_permitted,
    }


def list_task_definitions() -> list[dict[str, Any]]:
    return [task_definition_contract(item) for item in TASK_REGISTRY.values()]


def validate_task_artifact_dependencies(
    definition: TaskDefinition,
    artifact_fingerprints: dict[str, Any] | None,
) -> None:
    names = set((artifact_fingerprints or {}).keys())
    unexpected = sorted(names - definition.dependency_policy.artifact_keys)
    if unexpected:
        raise HandoffValidationError(
            "unexpected_artifact_dependency",
            "Unexpected task artifact dependency/dependencies: "
            + ", ".join(unexpected)
            + ".",
        )


def validate_task_input(
    definition: TaskDefinition,
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HandoffValidationError(
            "invalid_input",
            "Task input must be a JSON object.",
        )
    normalized = _safe_json_value(value, path="input")
    keys = set(normalized)
    missing = sorted(definition.required_input - keys)
    unexpected = sorted(keys - definition.allowed_input)
    if missing:
        raise HandoffValidationError(
            "missing_input_fields",
            "Missing required task input field(s): " + ", ".join(missing) + ".",
        )
    if unexpected:
        raise HandoffValidationError(
            "unexpected_input_fields",
            "Unexpected task input field(s): "
            + ", ".join(unexpected)
            + ".",
        )
    for key in definition.required_input:
        if normalized[key] in (None, "", [], {}):
            raise HandoffValidationError(
                "empty_input_field",
                f"Required task input field {key!r} is empty.",
            )
    return normalized


def _read_guidance_manifest() -> tuple[dict[str, Any], bytes]:
    path = GUIDANCE_ROOT / "voice-reference.json"
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffValidationError(
            "guidance_unavailable",
            f"Could not load the reviewed Voice Reference guidance: {exc}",
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise HandoffValidationError(
            "guidance_invalid",
            "The reviewed Voice Reference guidance manifest is invalid.",
        )
    if (
        value.get("alexandria_task_bundle_schema_version")
        != TASK_BUNDLE_SCHEMA_VERSION
        or value.get("alexandria_guidance_schema_version")
        != TASK_GUIDANCE_SCHEMA_VERSION
    ):
        raise HandoffValidationError(
            "guidance_schema_mismatch",
            "The reviewed Voice Reference guidance is not bound to this Task Bundle schema.",
        )
    source = value.get("source")
    if not isinstance(source, dict) or not all(
        isinstance(source.get(key), str) and source[key].strip()
        for key in ("url", "upstream_commit", "reviewed_at_utc", "kind")
    ):
        raise HandoffValidationError(
            "guidance_source_invalid",
            "The reviewed Voice Reference guidance has incomplete source provenance.",
        )
    content_hashes = value.get("content_sha256")
    if not isinstance(content_hashes, dict) or not content_hashes:
        raise HandoffValidationError(
            "guidance_hashes_missing",
            "The reviewed Voice Reference guidance has no immutable content hashes.",
        )
    for filename, expected_hash in content_hashes.items():
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(expected_hash, str)
            or not SHA256_PATTERN.fullmatch(expected_hash)
        ):
            raise HandoffValidationError(
                "guidance_hash_invalid",
                "The reviewed Voice Reference guidance contains an invalid content hash binding.",
            )
        try:
            actual_hash = _sha256((GUIDANCE_ROOT / filename).read_bytes())
        except OSError as exc:
            raise HandoffValidationError(
                "guidance_unavailable",
                f"Could not load reviewed guidance member {filename!r}: {exc}",
            ) from exc
        if actual_hash != expected_hash:
            raise HandoffValidationError(
                "guidance_content_mismatch",
                f"Reviewed guidance member {filename!r} does not match its pinned hash.",
            )
    return value, payload


def _guidance_payloads(
    definition: TaskDefinition,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    manifest, manifest_payload = _read_guidance_manifest()
    payloads: dict[str, bytes] = {
        GUIDANCE_MANIFEST_PATH: manifest_payload,
    }
    source_policy = GUIDANCE_ROOT / "source-policy.md"
    try:
        payloads[SOURCE_POLICY_PATH] = source_policy.read_bytes()
    except OSError as exc:
        raise HandoffValidationError(
            "guidance_unavailable",
            f"Could not load source policy guidance: {exc}",
        ) from exc

    if definition.guidance_profile is None:
        task_guidance = (
            "# Task guidance\n\nFollow instructions.md and the bundled source "
            "policy. Keep every unsupported conclusion unresolved.\n"
        ).encode("utf-8")
    else:
        profiles = manifest.get("profiles") or {}
        bindings = manifest.get("task_bindings") or {}
        bound_tasks = bindings.get(definition.guidance_profile)
        if (
            not isinstance(bound_tasks, list)
            or definition.task_type not in bound_tasks
            or any(not isinstance(task, str) for task in bound_tasks)
        ):
            raise HandoffValidationError(
                "guidance_binding_mismatch",
                f"Task {definition.task_type!r} is not bound to guidance profile {definition.guidance_profile!r}.",
            )
        filename = profiles.get(definition.guidance_profile)
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise HandoffValidationError(
                "guidance_invalid",
                f"Unknown guidance profile {definition.guidance_profile!r}.",
            )
        try:
            task_guidance = (GUIDANCE_ROOT / filename).read_bytes()
            payloads[f"guidance/{filename}"] = task_guidance
            payloads[NONHUMAN_GUIDANCE_PATH] = (
                GUIDANCE_ROOT / "nonhuman-speakers.md"
            ).read_bytes()
            if definition.task_type == "complete_cast_dossier":
                payloads[PERSONA_GUIDANCE_PATH] = (
                    GUIDANCE_ROOT / "persona.md"
                ).read_bytes()
                payloads[VOICE_IDENTITY_GUIDANCE_PATH] = (
                    GUIDANCE_ROOT / "voice-identity.md"
                ).read_bytes()
                payloads[LINE_DIRECTION_GUIDANCE_PATH] = (
                    GUIDANCE_ROOT / "line-direction.md"
                ).read_bytes()
                payloads[CAST_DOSSIER_GUIDANCE_PATH] = (
                    GUIDANCE_ROOT / "cast-dossier.md"
                ).read_bytes()
        except OSError as exc:
            raise HandoffValidationError(
                "guidance_unavailable",
                f"Could not load task guidance: {exc}",
            ) from exc
    payloads[TASK_GUIDANCE_PATH] = task_guidance
    source_hash_seed = {
        "manifest": _sha256(manifest_payload),
        "members": {
            name: _sha256(payload)
            for name, payload in sorted(payloads.items())
        },
    }
    info = {
        "schema_version": TASK_GUIDANCE_SCHEMA_VERSION,
        "version": manifest.get("guidance_version"),
        "profile": definition.guidance_profile or "source_only",
        "source": copy.deepcopy(manifest.get("source") or {}),
        "source_hash": fingerprint_value(source_hash_seed),
        "members": sorted(payloads),
    }
    return payloads, info


def _instructions_document(definition: TaskDefinition) -> str:
    guidance_map = ""
    if definition.task_type == "complete_cast_dossier":
        guidance_map = (
            "## Guidance map\n\n"
            "Use `guidance/persona.md` to write `persona_summary`. Use "
            "`guidance/voice-identity.md` to write `designed_voice_description` "
            "and the structured acoustic traits. Use `guidance/line-direction.md` "
            "as the exclusion boundary: momentary emotion and one-line delivery "
            "belong in Script directions, not the persistent Voice identity. Use "
            "`guidance/nonhuman-speakers.md` for creatures, collectives, synthetic "
            "voices, and other nonhuman speakers. `guidance/cast-dossier.md` governs "
            "coordination across roster, Voice, and visual sections.\n\n"
            "Do not treat `guidance/voice-reference.json` as the guidance itself; it "
            "is the provenance and hash index for the readable Markdown files above.\n\n"
        )
    return (
        "# Alexandria Task Bundle\n\n"
        f"Task: **{definition.label}** (`{definition.task_type}`)\n\n"
        "Read manifest.json, input.json, schema.json, and every file under "
        "guidance/. Complete only this task. Preserve supplied wording, "
        "identifiers, evidence, and offsets unless the task explicitly permits "
        "a change. Do not add commentary, markdown fences, or fields outside "
        "schema.json.\n\n"
        "Return valid JSON matching schema.json.\n\n"
        f"{guidance_map}"
        "## Preferred completed ZIP contract\n\n"
        "When your client can create ZIP files, preserve every original ZIP member "
        "byte-for-byte and add only result/result.json and result/completion.json. "
        "All hashes below are lowercase SHA-256 hexadecimal digests of the exact "
        "member bytes, not parsed, reformatted, or normalized JSON. "
        "result_size_bytes is the exact byte length of result/result.json. Use "
        "exactly this completion object shape:\n\n"
        "```json\n"
        "{\n"
        "  \"schema_version\": 2,\n"
        "  \"task_id\": \"copy manifest.json task_id exactly\",\n"
        "  \"manifest_fingerprint\": \"SHA-256 of the exact original manifest.json bytes\",\n"
        "  \"result_path\": \"result/result.json\",\n"
        "  \"result_size_bytes\": 0,\n"
        "  \"result_sha256\": \"SHA-256 of the exact result/result.json bytes\",\n"
        "  \"completed_at_utc\": \"RFC 3339 UTC timestamp, for example 2026-07-19T21:00:00Z\"\n"
        "}\n"
        "```\n\n"
        "Replace result_size_bytes and the hash placeholders with computed values. "
        "Do not alter, recompress through JSON rewriting, omit, or replace any "
        "original member content. ZIP compression metadata may differ; member bytes "
        "must not.\n\n"
        "## Fallback JSON envelope\n\n"
        "When your client cannot create a ZIP, return one JSON object with the "
        "completed value under result and this metadata shape:\n\n"
        "```json\n"
        "{\n"
        "  \"alexandria_task\": {\n"
        "    \"schema_version\": 2,\n"
        "    \"task_id\": \"copy manifest.json task_id exactly\",\n"
        "    \"manifest_fingerprint\": \"SHA-256 of the exact original manifest.json bytes\"\n"
        "  },\n"
        "  \"result\": {}\n"
        "}\n"
        "```\n\n"
        "The user never needs to copy or type an identifier.\n\n"
        "## Task instructions\n\n"
        f"{definition.instructions.strip()}\n"
    )


def _member_record(name: str, payload: bytes) -> dict[str, Any]:
    return {
        "path": name,
        "size_bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _safe_bundle_name(
    definition: TaskDefinition,
    bundle_name: str | None,
    created_at_utc: str,
) -> str:
    if bundle_name is None:
        stamp = re.sub(r"[^0-9]", "", created_at_utc)[:14]
        return f"{definition.task_type}-{stamp}.alexandria-task.zip"
    name = str(bundle_name or "").strip()
    if Path(name).name != name or not SAFE_FILENAME.fullmatch(name):
        raise HandoffValidationError(
            "unsafe_bundle_name",
            "Task bundle name must be a confined filename.",
        )
    return name if name.endswith(".zip") else name + ".alexandria-task.zip"


def create_task_bundle(
    *,
    output_dir: str | Path,
    task_type: str,
    input_payload: dict[str, Any],
    application_version: str,
    source_fingerprint: str | None = None,
    artifact_fingerprints: dict[str, str] | None = None,
    target: dict[str, str] | None = None,
    bundle_name: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    definition = get_task_definition(task_type)
    normalized_input = validate_task_input(definition, input_payload)
    if not isinstance(application_version, str) or not application_version.strip():
        raise HandoffValidationError(
            "invalid_application_version",
            "application_version must be non-empty text.",
        )
    if source_fingerprint is not None and not SHA256_PATTERN.fullmatch(
        source_fingerprint
    ):
        raise HandoffValidationError(
            "invalid_fingerprint",
            "source_fingerprint must be a lowercase SHA-256 fingerprint.",
        )
    normalized_artifacts: dict[str, str] = {}
    for name, fingerprint in (artifact_fingerprints or {}).items():
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or "\\" in name
            or not SHA256_PATTERN.fullmatch(str(fingerprint or ""))
        ):
            raise HandoffValidationError(
                "invalid_artifact_fingerprint",
                "Artifact names must be confined and fingerprints must be SHA-256.",
            )
        normalized_artifacts[name] = fingerprint
    normalized_target = None
    if target is not None:
        normalized_target = _safe_json_value(target, path="target")
        if set(normalized_target) != {"kind", "value"}:
            raise HandoffValidationError(
                "invalid_target",
                "Task target must contain exactly kind and value.",
            )
        if definition.target_kind is None:
            raise HandoffValidationError(
                "unexpected_target",
                "This task does not accept a target.",
            )
        if normalized_target["kind"] != definition.target_kind:
            raise HandoffValidationError(
                "invalid_target",
                f"This task requires a {definition.target_kind} target.",
            )
        if not isinstance(normalized_target["value"], str) or not normalized_target[
            "value"
        ].strip():
            raise HandoffValidationError(
                "invalid_target",
                "Task target value must be non-empty text.",
            )
    elif definition.target_kind is not None:
        raise HandoffValidationError(
            "target_required",
            f"{definition.label} requires a {definition.target_kind} target.",
        )

    created = created_at_utc or utc_timestamp()
    guidance_payloads, guidance = _guidance_payloads(definition)
    schema = get_task_schema(definition)
    payloads: dict[str, bytes] = {
        TASK_INSTRUCTIONS_PATH: _instructions_document(definition).encode("utf-8"),
        TASK_INPUT_PATH: _json_bytes(normalized_input),
        TASK_SCHEMA_PATH: _json_bytes(schema),
        **guidance_payloads,
    }
    checksums = {
        "schema_version": 1,
        "members": [
            _member_record(name, payload)
            for name, payload in sorted(payloads.items())
        ],
    }
    payloads[TASK_CHECKSUMS_PATH] = _json_bytes(checksums)
    if any(len(payload) > MAX_MEMBER_BYTES for payload in payloads.values()):
        raise HandoffValidationError(
            "task_bundle_too_large",
            "A task bundle member exceeds the supported size limit.",
        )
    if sum(len(payload) for payload in payloads.values()) > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise HandoffValidationError(
            "task_bundle_too_large",
            "The task bundle exceeds the supported total size limit.",
        )
    manifest_seed = {
        "schema_version": TASK_BUNDLE_SCHEMA_VERSION,
        "task_type": definition.task_type,
        "task_label": definition.label,
        "application_version": application_version.strip(),
        "contract": definition.contract,
        "contract_fingerprint": fingerprint_value(schema),
        "created_at_utc": created,
        "source_fingerprint": source_fingerprint,
        "artifact_fingerprints": normalized_artifacts,
        "target": normalized_target,
        "native_destination": definition.native_destination,
        "transfer_policy": definition.transfer_policy,
        "text_mutation_permitted": definition.text_mutation_permitted,
        "expected_result_path": DEFAULT_RESULT_PATH,
        "guidance": guidance,
        "members": [
            _member_record(name, payload)
            for name, payload in sorted(payloads.items())
        ],
    }
    task_id = "task_" + fingerprint_value(manifest_seed)[:32]
    manifest = {**manifest_seed, "task_id": task_id}
    manifest_payload = _json_bytes(manifest)
    all_payloads = {TASK_MANIFEST_PATH: manifest_payload, **payloads}
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = _safe_bundle_name(definition, bundle_name, created)
    target_path = directory / filename
    temporary = target_path.with_name(target_path.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, mode="w") as archive:
            for name, payload in sorted(all_payloads.items()):
                archive.writestr(_zip_info(name), payload)
        os.replace(temporary, target_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "path": str(target_path),
        "filename": filename,
        "task_id": task_id,
        "task_type": definition.task_type,
        "task_label": definition.label,
        "native_destination": definition.native_destination,
        "target": copy.deepcopy(normalized_target),
        "manifest_fingerprint": _sha256(manifest_payload),
        "guidance": copy.deepcopy(guidance),
        "size_bytes": target_path.stat().st_size,
    }


def _validate_member_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name) is not None
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise HandoffValidationError(
            "unsafe_archive_member",
            f"Unsafe archive member path: {name!r}.",
        )


def _read_zip_members(path: str | Path) -> dict[str, bytes]:
    target = Path(path)
    if not target.is_file():
        raise HandoffValidationError(
            "bundle_missing",
            f"Task bundle was not found: {target}.",
        )
    members: dict[str, bytes] = {}
    total_size = 0
    try:
        with zipfile.ZipFile(target, mode="r") as archive:
            for info in archive.infolist():
                _validate_member_name(info.filename)
                if info.filename in members:
                    raise HandoffValidationError(
                        "duplicate_archive_member",
                        f"Duplicate archive member: {info.filename}.",
                    )
                if info.is_dir():
                    raise HandoffValidationError(
                        "unexpected_archive_member",
                        "Task bundles may not contain directory entries.",
                    )
                file_mode = (info.external_attr >> 16) & 0o170000
                if file_mode == stat.S_IFLNK:
                    raise HandoffValidationError(
                        "archive_symlink",
                        f"Archive member {info.filename!r} is a symbolic link.",
                    )
                if info.flag_bits & 0x1:
                    raise HandoffValidationError(
                        "encrypted_archive_member",
                        "Encrypted task bundle members are not supported.",
                    )
                if info.file_size > MAX_MEMBER_BYTES:
                    raise HandoffValidationError(
                        "task_bundle_too_large",
                        f"Archive member {info.filename!r} exceeds the size limit.",
                    )
                total_size += info.file_size
                if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise HandoffValidationError(
                        "task_bundle_too_large",
                        "The task bundle exceeds the supported total size limit.",
                    )
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > 200
                ):
                    raise HandoffValidationError(
                        "suspicious_compression_ratio",
                        f"Archive member {info.filename!r} has an unsafe compression ratio.",
                    )
                members[info.filename] = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise HandoffValidationError(
            "invalid_bundle",
            "The task bundle is not a valid ZIP archive.",
        ) from exc
    return members


def _parse_json(payload: bytes, name: str) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HandoffValidationError(
            "invalid_encoding",
            f"{name} must be UTF-8 text.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HandoffValidationError(
            "invalid_json",
            f"{name} does not contain valid JSON.",
        ) from exc


def _inspect_task_members(members: dict[str, bytes]) -> dict[str, Any]:
    required = {
        TASK_MANIFEST_PATH,
        TASK_INSTRUCTIONS_PATH,
        TASK_INPUT_PATH,
        TASK_SCHEMA_PATH,
        TASK_CHECKSUMS_PATH,
        TASK_GUIDANCE_PATH,
        GUIDANCE_MANIFEST_PATH,
        SOURCE_POLICY_PATH,
    }
    if not required.issubset(members):
        missing = sorted(required - set(members))
        raise HandoffValidationError(
            "invalid_bundle_members",
            "Task bundle is missing: " + ", ".join(missing) + ".",
        )
    manifest = _parse_json(members[TASK_MANIFEST_PATH], TASK_MANIFEST_PATH)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise HandoffValidationError(
            "unsupported_manifest_schema",
            "Unsupported Alexandria Task Bundle manifest schema.",
        )
    definition = get_task_definition(manifest.get("task_type"))
    task_id = manifest.get("task_id")
    if not isinstance(task_id, str) or not SAFE_TASK_ID.fullmatch(task_id):
        raise HandoffValidationError(
            "invalid_task_id",
            "Task manifest contains an invalid task identifier.",
        )
    if manifest.get("task_label") != definition.label:
        raise HandoffValidationError(
            "invalid_manifest",
            "Task label does not match the registry.",
        )
    if manifest.get("contract") != definition.contract:
        raise HandoffValidationError(
            "invalid_manifest",
            "Task contract does not match the registry.",
        )
    if manifest.get("native_destination") != definition.native_destination:
        raise HandoffValidationError(
            "invalid_manifest",
            "Native destination does not match the task registry.",
        )
    if manifest.get("transfer_policy") != definition.transfer_policy:
        raise HandoffValidationError(
            "invalid_manifest",
            "Transfer policy does not match the task registry.",
        )
    member_records = manifest.get("members")
    if not isinstance(member_records, list) or not member_records:
        raise HandoffValidationError(
            "invalid_manifest",
            "Task manifest member records are missing.",
        )
    expected_names: set[str] = {TASK_MANIFEST_PATH}
    for record in member_records:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise HandoffValidationError(
                "invalid_manifest",
                "Task manifest contains an invalid member record.",
            )
        name = record["path"]
        _validate_member_name(name)
        if name == TASK_MANIFEST_PATH or name in expected_names:
            raise HandoffValidationError(
                "invalid_manifest",
                "Task manifest contains a duplicate or self member record.",
            )
        expected_names.add(name)
        payload = members.get(name)
        if payload is None:
            raise HandoffValidationError(
                "invalid_bundle_members",
                f"Task bundle member {name!r} is missing.",
            )
        if record["size_bytes"] != len(payload) or record["sha256"] != _sha256(
            payload
        ):
            raise HandoffValidationError(
                "bundle_fingerprint_mismatch",
                f"Task bundle member {name!r} failed its hash or size check.",
            )
    original_names = set(members) - {DEFAULT_RESULT_PATH, COMPLETION_PATH}
    if original_names != expected_names:
        unexpected = sorted(original_names - expected_names)
        raise HandoffValidationError(
            "invalid_bundle_members",
            "Task bundle contains unexpected members: "
            + ", ".join(unexpected)
            + ".",
        )
    checksums = _parse_json(members[TASK_CHECKSUMS_PATH], TASK_CHECKSUMS_PATH)
    expected_checksums = {
        "schema_version": 1,
        "members": [
            _member_record(name, members[name])
            for name in sorted(
                expected_names - {TASK_MANIFEST_PATH, TASK_CHECKSUMS_PATH}
            )
        ],
    }
    if checksums != expected_checksums:
        raise HandoffValidationError(
            "bundle_fingerprint_mismatch",
            "checksums.json does not match the task bundle content.",
        )
    input_payload = _parse_json(members[TASK_INPUT_PATH], TASK_INPUT_PATH)
    normalized_input = validate_task_input(definition, input_payload)
    schema = _parse_json(members[TASK_SCHEMA_PATH], TASK_SCHEMA_PATH)
    canonical_schema = get_schema(definition.contract)
    if fingerprint_value(schema) != fingerprint_value(canonical_schema):
        raise HandoffValidationError(
            "schema_contract_mismatch",
            "schema.json does not match the registered native contract.",
        )
    if manifest.get("contract_fingerprint") != fingerprint_value(canonical_schema):
        raise HandoffValidationError(
            "bundle_fingerprint_mismatch",
            "Manifest contract fingerprint does not match schema.json.",
        )
    guidance = manifest.get("guidance")
    if not isinstance(guidance, dict):
        raise HandoffValidationError(
            "guidance_invalid",
            "Task manifest guidance record is invalid.",
        )
    guidance_members = guidance.get("members")
    if not isinstance(guidance_members, list) or sorted(guidance_members) != sorted(
        name for name in expected_names if name.startswith("guidance/")
    ):
        raise HandoffValidationError(
            "guidance_invalid",
            "Task guidance member list does not match the bundle.",
        )
    source_hash_seed = {
        "manifest": _sha256(members[GUIDANCE_MANIFEST_PATH]),
        "members": {
            name: _sha256(members[name]) for name in sorted(guidance_members)
        },
    }
    if guidance.get("source_hash") != fingerprint_value(source_hash_seed):
        raise HandoffValidationError(
            "guidance_fingerprint_mismatch",
            "Task guidance does not match Alexandria's reviewed snapshot.",
        )
    manifest_seed = {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key != "task_id"
    }
    if task_id != "task_" + fingerprint_value(manifest_seed)[:32]:
        raise HandoffValidationError(
            "bundle_fingerprint_mismatch",
            "Task identifier does not match the immutable manifest content.",
        )
    return {
        "manifest": copy.deepcopy(manifest),
        "definition": definition,
        "input": normalized_input,
        "schema": copy.deepcopy(canonical_schema),
        "instructions": members[TASK_INSTRUCTIONS_PATH].decode("utf-8"),
        "manifest_fingerprint": _sha256(members[TASK_MANIFEST_PATH]),
        "members": {
            name: bytes(payload)
            for name, payload in members.items()
            if name not in {DEFAULT_RESULT_PATH, COMPLETION_PATH}
        },
    }


def inspect_task_bundle(path: str | Path) -> dict[str, Any]:
    return _inspect_task_members(_read_zip_members(path))


def _validate_current_state(
    manifest: dict[str, Any],
    *,
    current_source_fingerprint: str | None,
    current_artifact_fingerprints: dict[str, str] | None,
) -> None:
    expected_source = manifest.get("source_fingerprint")
    if expected_source is not None:
        if current_source_fingerprint != expected_source:
            raise HandoffConflictError(
                "stale_source",
                "The selected source changed after this task was exported.",
            )
    current_artifacts = current_artifact_fingerprints or {}
    for name, expected in (manifest.get("artifact_fingerprints") or {}).items():
        if current_artifacts.get(name) != expected:
            raise HandoffConflictError(
                "stale_artifact",
                f"Artifact {name!r} changed after this task was exported.",
            )


def _validate_complete_cast_result_against_input(
    *,
    inspected: dict[str, Any],
    result: dict[str, Any],
) -> None:
    input_payload = inspected.get("input") or {}
    requested = input_payload.get("requested_sections") or {}
    if result.get("selected_sections") != requested:
        raise HandoffValidationError(
            "cast_dossier_section_mismatch",
            "The completed Cast dossier sections do not match the exported choices.",
        )
    if requested.get("voice_personas_and_designs"):
        subjects = input_payload.get("script_speakers") or []
        expected = [str(item.get("speaker") or "") for item in subjects]
        returned = [
            str(item.get("speaker") or "")
            for item in (result.get("voice_dossiers") or {}).get("voices") or []
        ]
        if set(returned) != set(expected) or len(returned) != len(expected):
            raise HandoffValidationError(
                "cast_dossier_voice_catalog_incomplete",
                "The completed Cast dossier must return every exported Script speaker exactly once.",
            )
        samples = {
            str(item.get("speaker") or ""): set(item.get("sample_lines") or [])
            for item in subjects
        }
        invalid_ref = sorted(
            item["speaker"]
            for item in result["voice_dossiers"]["voices"]
            if item["ref_text"] not in samples.get(item["speaker"], set())
        )
        if invalid_ref:
            raise HandoffValidationError(
                "cast_dossier_ref_text_not_exact",
                "Designed Voice ref_text must be one exact supplied Script line for: "
                + ", ".join(invalid_ref),
            )
    if requested.get("visual_dossiers"):
        roster_entities = (
            (result.get("roster") or {}).get("entities")
            if requested.get("roster_and_relationships")
            else (input_payload.get("existing_roster") or {}).get("entries")
        ) or []
        if not roster_entities:
            raise HandoffValidationError(
                "cast_dossier_visual_roster_required",
                "Visual dossier completion requires a roster identity list.",
            )
        label_to_seeds: dict[str, set[str]] = {}
        expected_seeds: set[str] = set()
        for index, entity in enumerate(roster_entities):
            seed = str(
                entity.get("identity_seed")
                or entity.get("id")
                or f"entity-{index}"
            ).strip()
            expected_seeds.add(seed)
            labels = [
                seed,
                entity.get("canonical_name"),
                entity.get("display_name"),
                entity.get("speaker_label"),
                *(entity.get("aliases") or []),
                *(entity.get("nicknames") or []),
                *(entity.get("titles") or []),
            ]
            for raw in labels:
                label = str(raw or "").strip().casefold()
                if label:
                    label_to_seeds.setdefault(label, set()).add(seed)
        returned_seeds: set[str] = set()
        ambiguous: list[str] = []
        unknown: list[str] = []
        for dossier in (result.get("visual_dossiers") or {}).get("characters") or []:
            label = str(dossier.get("character_id") or "").strip().casefold()
            matches = label_to_seeds.get(label, set())
            if len(matches) == 1:
                returned_seeds.update(matches)
            elif len(matches) > 1:
                ambiguous.append(str(dossier.get("character_id") or ""))
            else:
                unknown.append(str(dossier.get("character_id") or ""))
        if ambiguous or unknown or returned_seeds != expected_seeds:
            missing = sorted(expected_seeds - returned_seeds)
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unknown:
                details.append("unknown: " + ", ".join(sorted(unknown)))
            if ambiguous:
                details.append("ambiguous: " + ", ".join(sorted(ambiguous)))
            raise HandoffValidationError(
                "cast_dossier_visual_catalog_incomplete",
                "The completed visual dossier catalog must account for every roster identity ("
                + "; ".join(details)
                + ").",
            )


def _validate_result(
    inspected: dict[str, Any],
    value: Any,
) -> Any:
    safe = _safe_json_value(value, path="result")
    definition: TaskDefinition = inspected["definition"]
    try:
        normalized = validate_contract(definition.contract, safe)
    except ContractValidationError as exc:
        raise HandoffValidationError(
            "stage_contract_validation_failed",
            f"The completed result failed the {definition.contract!r} contract: {exc}",
        ) from exc
    if definition.task_type == "complete_cast_dossier":
        _validate_complete_cast_result_against_input(
            inspected=inspected,
            result=normalized,
        )
    return normalized


def create_completed_task_bundle(
    *,
    task_bundle_path: str | Path,
    result: Any,
    output_path: str | Path,
    completed_at_utc: str | None = None,
) -> dict[str, Any]:
    inspected = inspect_task_bundle(task_bundle_path)
    normalized = _validate_result(inspected, result)
    result_payload = _json_bytes(normalized)
    if len(result_payload) > MAX_RESULT_BYTES:
        raise HandoffValidationError(
            "result_too_large",
            "The completed task result exceeds the supported size limit.",
        )
    completion = {
        "schema_version": TASK_COMPLETION_SCHEMA_VERSION,
        "task_id": inspected["manifest"]["task_id"],
        "manifest_fingerprint": inspected["manifest_fingerprint"],
        "result_path": DEFAULT_RESULT_PATH,
        "result_size_bytes": len(result_payload),
        "result_sha256": _sha256(result_payload),
        "completed_at_utc": completed_at_utc or utc_timestamp(),
    }
    payloads = {
        **inspected["members"],
        DEFAULT_RESULT_PATH: result_payload,
        COMPLETION_PATH: _json_bytes(completion),
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, mode="w") as archive:
            for name, payload in sorted(payloads.items()):
                archive.writestr(_zip_info(name), payload)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "path": str(target),
        "task_id": inspected["manifest"]["task_id"],
        "manifest_fingerprint": inspected["manifest_fingerprint"],
        "result_fingerprint": fingerprint_value(normalized),
    }


def inspect_completed_task_bundle(
    *,
    path: str | Path,
    current_source_fingerprint: str | None = None,
    current_artifact_fingerprints: dict[str, str] | None = None,
) -> dict[str, Any]:
    members = _read_zip_members(path)
    if DEFAULT_RESULT_PATH not in members or COMPLETION_PATH not in members:
        raise HandoffValidationError(
            "incomplete_task_bundle",
            "The ZIP is an exported task, not a completed task result.",
        )
    inspected = _inspect_task_members(members)
    completion = _parse_json(members[COMPLETION_PATH], COMPLETION_PATH)
    if not isinstance(completion, dict) or completion.get("schema_version") != 2:
        raise HandoffValidationError(
            "invalid_completion",
            "result/completion.json is invalid.",
        )
    result_payload = members[DEFAULT_RESULT_PATH]
    expected_completion = {
        "task_id": inspected["manifest"]["task_id"],
        "manifest_fingerprint": inspected["manifest_fingerprint"],
        "result_path": DEFAULT_RESULT_PATH,
        "result_size_bytes": len(result_payload),
        "result_sha256": _sha256(result_payload),
    }
    for field, expected in expected_completion.items():
        if completion.get(field) != expected:
            raise HandoffValidationError(
                "completion_fingerprint_mismatch",
                f"Completion field {field!r} does not match the task or result.",
            )
    _validate_current_state(
        inspected["manifest"],
        current_source_fingerprint=current_source_fingerprint,
        current_artifact_fingerprints=current_artifact_fingerprints,
    )
    result = _parse_json(result_payload, DEFAULT_RESULT_PATH)
    normalized = _validate_result(inspected, result)
    return _completed_result(inspected, normalized, Path(path).name, "completed_zip")


def create_result_envelope(
    *,
    task_bundle_path: str | Path,
    result: Any,
) -> dict[str, Any]:
    inspected = inspect_task_bundle(task_bundle_path)
    normalized = _validate_result(inspected, result)
    return {
        "alexandria_task": {
            "schema_version": TASK_BUNDLE_SCHEMA_VERSION,
            "task_id": inspected["manifest"]["task_id"],
            "manifest_fingerprint": inspected["manifest_fingerprint"],
        },
        "result": normalized,
    }


def inspect_result_envelope(
    *,
    envelope_path: str | Path,
    task_bundle_path: str | Path,
    current_source_fingerprint: str | None = None,
    current_artifact_fingerprints: dict[str, str] | None = None,
) -> dict[str, Any]:
    target = Path(envelope_path)
    if not target.is_file() or target.stat().st_size > MAX_RESULT_BYTES:
        raise HandoffValidationError(
            "result_missing",
            "The completed task JSON is missing or too large.",
        )
    envelope = _parse_json(target.read_bytes(), target.name)
    if not isinstance(envelope, dict) or set(envelope) != {
        "alexandria_task",
        "result",
    }:
        raise HandoffValidationError(
            "invalid_result_envelope",
            "Completed task JSON must contain alexandria_task and result.",
        )
    metadata = envelope["alexandria_task"]
    if not isinstance(metadata, dict) or set(metadata) != {
        "schema_version",
        "task_id",
        "manifest_fingerprint",
    }:
        raise HandoffValidationError(
            "invalid_result_envelope",
            "alexandria_task metadata is invalid.",
        )
    inspected = inspect_task_bundle(task_bundle_path)
    if metadata.get("schema_version") != 2:
        raise HandoffValidationError(
            "unsupported_result_schema",
            "Unsupported completed task JSON schema.",
        )
    if metadata.get("task_id") != inspected["manifest"]["task_id"]:
        raise HandoffValidationError(
            "task_id_mismatch",
            "The completed JSON belongs to a different Alexandria task.",
        )
    if metadata.get("manifest_fingerprint") != inspected["manifest_fingerprint"]:
        raise HandoffValidationError(
            "manifest_fingerprint_mismatch",
            "The completed JSON does not match the original task bundle.",
        )
    _validate_current_state(
        inspected["manifest"],
        current_source_fingerprint=current_source_fingerprint,
        current_artifact_fingerprints=current_artifact_fingerprints,
    )
    normalized = _validate_result(inspected, envelope["result"])
    return _completed_result(inspected, normalized, target.name, "result_envelope")


def _completed_result(
    inspected: dict[str, Any],
    normalized: Any,
    filename: str,
    container: str,
) -> dict[str, Any]:
    manifest = inspected["manifest"]
    definition: TaskDefinition = inspected["definition"]
    return {
        "schema_version": TASK_BUNDLE_SCHEMA_VERSION,
        "task_id": manifest["task_id"],
        "task_type": definition.task_type,
        "task_label": definition.label,
        "stage": definition.stage,
        "native_destination": definition.native_destination,
        "transfer_policy": definition.transfer_policy,
        "target": copy.deepcopy(manifest.get("target")),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "artifact_fingerprints": copy.deepcopy(
            manifest.get("artifact_fingerprints") or {}
        ),
        "manifest_fingerprint": inspected["manifest_fingerprint"],
        "result": copy.deepcopy(normalized),
        "result_fingerprint": fingerprint_value(normalized),
        "result_filename": filename,
        "container": container,
        "guidance": copy.deepcopy(manifest.get("guidance") or {}),
        "review": {
            "root_type": (
                "array"
                if isinstance(normalized, list)
                else "object"
            ),
            "item_count": (
                len(normalized)
                if isinstance(normalized, (list, dict))
                else None
            ),
            "source_fingerprint_verified": manifest.get(
                "source_fingerprint"
            )
            is not None,
            "artifact_fingerprints_verified": sorted(
                (manifest.get("artifact_fingerprints") or {}).keys()
            ),
        },
    }
