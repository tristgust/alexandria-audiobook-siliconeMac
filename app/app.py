import asyncio
import copy
import hashlib
import inspect
import os
import sys
import gc
import json
import shutil
import secrets
import logging
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from accent_pipeline import (
        detect_accent_pipeline,
        normalize_output_language,
    )
except ImportError:
    from .accent_pipeline import (
        detect_accent_pipeline,
        normalize_output_language,
    )
from typing import Any, Dict, Iterable, List, Literal, Optional, Union
import re
import time
import queue
import threading
import zipfile
import subprocess
import tempfile
import aiofiles
from pydub import AudioSegment
from datetime import datetime, timezone
from pathlib import Path
from utils import atomic_json_write
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from math import ceil

# Import ProjectManager
from project import ProjectManager
from approved_audio import (
    ApprovedAudioLockedError,
    active_approved_audio_lock,
    require_regeneration_unlocked,
)
from approved_audio_promotion import (
    ApprovedAudioPromotionError,
    promote_approved_adaptation_audio,
    rollback_approved_adaptation_audio,
)
from audio_invalidation import (
    AudioInvalidationError,
    affected_voice_dependency_speakers,
    apply_speaker_audio_dependency_change,
    undo_project_audio_invalidation,
)
from audio_generation_lifecycle import (
    ACTIVE_STATES as AUDIO_REQUEST_ACTIVE_STATES,
    TERMINAL_STATES as AUDIO_REQUEST_TERMINAL_STATES,
    AudioGenerationLifecycleError,
    claim_request as claim_audio_generation_request,
    finalize_request as finalize_audio_generation_request,
    list_requests as list_audio_generation_requests,
    load_request as load_audio_generation_request,
    normalize_request_manifest as normalize_audio_generation_manifest,
    pending_replacement as pending_audio_generation_replacement,
    prepare_request as prepare_audio_generation_request,
    reconcile_interrupted_requests as reconcile_interrupted_audio_requests,
    record_chunk_failed as record_audio_generation_chunk_failed,
    request_cancel as cancel_audio_generation_request,
    request_context as audio_generation_request_context,
    should_cancel as audio_generation_should_cancel,
)
from audio_crash_reconciliation import reconcile_audio_transitions
from audio_artifacts import validate_audio_file
from audio_takes import AudioTakeError
from pronunciation_registry import (
    PronunciationRegistryError,
    apply_pronunciation_registry_change,
    empty_pronunciation_registry,
    load_pronunciation_registry,
    normalize_pronunciation_registry,
    remove_pronunciation_entry,
    resolve_pronunciation_request,
    upsert_pronunciation_entry,
)
from default_prompts import load_default_prompts
from review_prompts import load_review_prompts
from persona_prompts import load_persona_prompts
from llm_config import (
    DEFAULT_API_KEY,
    DEFAULT_BACKEND,
    DEFAULT_BASE_URL,
    DEFAULT_CONTEXT_LENGTH,
    DEFAULT_CORRECTIVE_RETRY,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MODEL_NAME,
    DEFAULT_STRUCTURED_OUTPUT,
    DEFAULT_THINKING,
    DEFAULT_TIMEOUT,
    build_runtime_client,
    normalized_llm_section,
)
from llm_telemetry import read_llm_telemetry
from hf_utils import fetch_builtin_manifest, download_builtin_adapter, is_adapter_downloaded
from hf_access import shared_huggingface_cache_dir
from model_memory import (
    ModelMemoryCoordinator,
    ModelMemoryError,
    default_model_memory_policy_path,
    memory_snapshot,
)
from model_registry import (
    ModelCacheOperationError,
    ModelRegistryError,
    download_or_repair_model,
    model_registry_status,
    model_spec,
    registered_models,
)

from script_library import (
    current_metadata_path,
    delete_script_bundle,
    list_saved_script_records,
    load_script_bundle,
    save_script_bundle,
)

from generate_script import (
    build_script_generation_snapshot,
    fix_mojibake,
)
from review_audit import build_review_text_stream, normalize_review_text
from script_audit import UnbalancedDialogueQuotesError, split_source_segments
from legacy_script_repair import (
    LegacyScriptRepairError,
    normalized_source_for_legacy_repair,
    repair_legacy_curly_apostrophe_script,
)
from character_roster import (
    CharacterRosterError,
    CharacterRosterSourceMismatchError,
    CharacterRosterValidationError,
    build_character_roster_status,
    build_source_snapshot,
    read_character_roster,
)
from character_roster_actions import (
    CharacterRosterActionError,
    CharacterRosterConflictError,
    approve_character_roster_file,
    list_character_roster_revisions,
    mutate_character_roster_draft_file,
    replace_approved_character_roster_file,
    rollback_approved_character_roster_file,
)
from roster_discovery import (
    clear_roster_discovery_state,
    completed_observations,
    inspect_roster_discovery_state,
    load_roster_discovery_state,
)
from character_visuals import (
    build_visual_status,
    load_persona_reference,
    persona_reference_targets,
    validate_visual_dossier,
)
from visual_discovery import (
    clear_visual_discovery_state,
    completed_visual_observations,
    inspect_visual_discovery_state,
    load_visual_discovery_state,
)
from voice_training_api import (
    VoiceTrainingApiError,
    apply_voice_training_action_payload,
    create_voice_training_candidate_payload,
    get_voice_training_project_payload,
    get_voice_training_status_payload,
)
from expressive_reference_bank import (
    COMPARISON_MODES,
    reference_bank_path,
    sha256_file,
)
from expressive_reference_bank_api import (
    ExpressiveReferenceBankApiError,
    apply_reference_bank_action_payload,
    assign_reference_bank_payload,
    create_reference_bank_payload,
    generate_comparison_payload,
    generate_reference_payload,
    get_reference_bank_payload,
    get_reference_bank_status_payload,
)
from speaker_management_api import (
    SpeakerManagementApiError,
    apply_speaker_operation_payload,
    get_speaker_management_status_payload,
    get_speaker_operation_payload,
    undo_speaker_operation_payload,
)
from llm_profiles_api import (
    LLMProfilesApiError,
    get_llm_profiles_payload,
    get_llm_stage_profile_payload,
    remove_llm_stage_profile_payload,
    update_llm_stage_profile_payload,
)
from migration_api import (
    MigrationApiError,
    apply_migration_payload,
    get_migration_history_payload,
    get_migration_operation_payload,
    get_migration_status_payload,
    rollback_migration_payload,
)
from instruction_propagation import (
    InstructionPropagationError,
    validate_instruction_propagation_contract,
)
from voice_backend_capabilities import (
    VoiceBackendCapabilityError,
    build_voice_backend_capabilities,
    require_lora_training_supported,
)
from application_settings import (
    ApplicationSettingsError,
    get_application_settings,
    update_application_settings,
)
from fish_hybrid_migration import (
    FishHybridMigrationError,
    migrate_fish_hybrid_policy,
)
from more_tools import MoreToolsError, inspect_more_tools
from voice_library import (
    BUILT_IN_VOICES,
    VoiceLibraryError,
    build_voice_library,
    resolve_voice_library_assignment,
    resolve_voice_library_preview,
)
from community_qwen_candidates import (
    curated_qwen_candidate_catalog,
    install_curated_qwen_candidate,
)
from community_qwen_packs import (
    CommunityQwenPackError,
    approve_qvoice_pack,
    inspect_qvoice_upload,
    inspect_qwen_pack_path,
    install_community_qwen_pack,
    install_qvoice_pack,
    list_qwen_packs,
    record_qvoice_preview,
    remove_qvoice_pack,
    resolve_qvoice_pack,
    resolve_qvoice_preview,
)
from controlled_clone_preview import (
    ControlledClonePreviewError,
    ControlledClonePreviewUnavailableError,
    ControlledClonePreviewValidationError,
    build_controlled_clone_configuration_fingerprint,
    generate_controlled_clone_preview,
)
from experimental_prompt_routing import (
    ExperimentalPromptRoutingError,
    prompt_routing_fingerprint,
    validate_experimental_prompt_routing,
)
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    RecurringVoiceRoutingError,
    routing_fingerprint as recurring_routing_fingerprint,
    validate_recurring_voice_routing,
)
from production_prompt_routes import (
    inspect_primary_responsive_voice_pack,
)
# Startup imports may materialize hash-verified dry composites from remote segments.
from pending_voice_imports import (
    PENDING_VOICE_IMPORT_FILENAME,
    consume_pending_voice_import_queue,
)
from controlled_clone_approval import (
    ControlledCloneApprovalConflictError,
    ControlledCloneApprovalValidationError,
    clear_controlled_clone_approvals,
    confirm_controlled_clone_preview,
    consume_controlled_clone_approvals,
    register_controlled_clone_preview,
)
from training_sidecar_api import (
    TrainingSidecarApiError,
    create_training_sidecar_job_payload,
    execute_training_sidecar_job_payload,
    get_training_sidecar_job_payload,
    get_training_sidecar_status_payload,
    import_training_sidecar_artifact_payload,
    install_training_sidecar_mlx_artifact_payload,
)
from generation_status import (
    build_generation_status,
)
from project_flow import (
    ProjectFlowError,
    inspect_project_flow,
)
from cast_aggregate import (
    CastAggregateError,
    inspect_cast_project,
)
from produce_aggregate import (
    ProduceAggregateError,
    build_produce_generation_plan,
    inspect_produce_project,
)
from export_aggregate import (
    ExportAggregateError,
    build_export_plan,
    execute_export_build,
    inspect_export_project,
)
from export_publication import (
    MAX_EXPORT_COVER_BYTES,
    detect_export_cover_media_type,
    resolve_export_cover,
)
from library_inventory import (
    LibraryInventoryError,
    build_library_delete_impact,
    get_library_artifact,
    inspect_library_inventory,
    validate_library_delete_request,
)
from help_center import (
    HelpCenterError,
    get_help_topic,
    get_help_topic_by_context,
    inspect_help_center,
)
from script_lifecycle import (
    ScriptLifecycleError,
    accept_current_script,
    inspect_script_lifecycle,
    mark_discovery_handoff,
    reject_current_script,
    rollback_script_version,
)
from project_catalog import (
    ProjectCatalogError,
    application_data_root,
    create_managed_project,
    delete_project_to_trash,
    duplicate_project,
    inspect_project_source,
    list_project_summaries,
    load_project_catalog,
    project_catalog_path,
    project_delete_impact,
    select_project,
    set_project_archived,
)
from project_templates import (
    ProjectTemplateError,
    create_project_template,
    delete_project_template,
    duplicate_project_template,
    list_project_templates,
    project_template_delete_impact,
    resolve_project_template,
    set_default_project_template,
    update_project_template,
)
from voice_aliases import (
    VoiceAliasError,
    merge_voice_config_updates,
    resolve_voice_alias,
)
from voice_identity_context import build_script_speaker_roster
from recovery_status import build_recovery_summary
from backend_render_plan import (
    build_task_chunks as build_backend_render_plan_task_chunks,
    chunks_fingerprint as backend_render_plan_chunks_fingerprint,
    inspect_backend_render_plan,
    task_guidance as backend_render_plan_task_guidance,
)
from stage_logs import (
    StageLogError,
    append_stage_log,
    read_stage_log,
    reset_stage_log,
)
from chatgpt_handoff import ChatGPTHandoffError
from external_workflows import (
    ExternalWorkflowConflictError,
    ExternalWorkflowError,
    ExternalWorkflowValidationError,
    apply_annotated_script_candidate,
    create_stored_handoff,
    create_stored_task_bundle,
    get_annotated_script_candidate,
    get_handoff_bundle_path,
    list_annotated_script_candidates,
    get_handoff_prompt,
    get_structured_result_candidate,
    get_task_bundle_path,
    list_task_library,
    inspect_annotated_script_upload,
    inspect_completed_task_upload,
    inspect_stored_handoff_result,
    open_handoff_folder,
    rollback_annotated_script_import,
)
from external_stage_transfers import (
    ExternalStageTransferConflictError,
    ExternalStageTransferValidationError,
    transfer_structured_result_candidate,
)
from roster_import_reconciliation import (
    RosterImportReconciliationConflictError,
    RosterImportReconciliationValidationError,
    apply_issue_focused_roster_import_reconciliation,
    apply_roster_import_reconciliation,
    build_issue_focused_roster_import_reconciliation,
    build_roster_import_reconciliation,
    get_pending_roster_import_reconciliation,
    restore_transferred_roster_import_draft,
)
from roster_reconciliation import (
    RosterReconciliationError,
    inspect_roster_reconciliation_project,
)
from cast_dossier_package import (
    CastDossierPackageError,
    activate_complete_cast_dossier,
    get_cast_dossier_package,
    inspect_visual_identity_review,
    package_for_roster_candidate,
    package_for_roster_draft,
    split_complete_cast_dossier_candidate,
)
from roster_enrichment import (
    RosterEnrichmentError,
    load_plan as load_roster_enrichment_plan,
    save_plan as save_roster_enrichment_plan,
    update_plan as update_roster_enrichment_plan,
)
from generation_state import fingerprint_text, fingerprint_value
from llm_schemas import get_schema
from task_bundles import get_task_definition, list_task_definitions

from generation_actions import (
    GenerationActionBlockedError,
    choose_generation_action,
    discard_generation_checkpoint,
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AlexandriaUI")

app = FastAPI(title="Alexandria Audiobook")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HELP_CENTER_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "docs", "help"))
_CONFIG_ENV_PATH = os.environ.get("ALEXANDRIA_CONFIG_PATH")
_DEFAULT_LEGACY_ROOT = (
    Path(_CONFIG_ENV_PATH).expanduser().resolve().parent
    if _CONFIG_ENV_PATH
    else Path(BASE_DIR).resolve().parent
)
LEGACY_ROOT_DIR = str(
    Path(
        os.environ.get(
            "ALEXANDRIA_LEGACY_ROOT_DIR",
            str(_DEFAULT_LEGACY_ROOT),
        )
    ).expanduser().resolve()
)
ROOT_DIR = LEGACY_ROOT_DIR
PROJECTS_DATA_ROOT = application_data_root()
CONFIG_PATH = (
    _CONFIG_ENV_PATH
    or next(
        (
            str(candidate)
            for candidate in (
                Path(BASE_DIR, "config.json"),
                Path(BASE_DIR).resolve().parent / "config.json",
            )
            if candidate.is_file()
        ),
        os.path.join(BASE_DIR, "config.json"),
    )
)
# Configuration migration is launcher-global. Managed project activation may change
# ROOT_DIR, but it must not move migration receipts or reinterpret CONFIG_PATH as
# project-local state.
MIGRATION_ROOT_DIR = LEGACY_ROOT_DIR
_RUNTIME_PROJECT_LOCK = threading.RLock()
_PRODUCE_AGGREGATE_CACHE_LOCK = threading.RLock()
_PRODUCE_AGGREGATE_CACHE: dict[str, object | None] = {
    "signature": None,
    "aggregate": None,
}


def _clear_produce_aggregate_cache() -> None:
    with _PRODUCE_AGGREGATE_CACHE_LOCK:
        _PRODUCE_AGGREGATE_CACHE["signature"] = None
        _PRODUCE_AGGREGATE_CACHE["aggregate"] = None


def _update_stat_digest(digest, path: Path) -> None:
    digest.update(str(path).encode("utf-8"))
    try:
        stat = path.stat()
    except FileNotFoundError:
        digest.update(b"\0missing")
        return
    digest.update(f"\0{stat.st_mtime_ns}\0{stat.st_size}".encode("ascii"))


def _produce_input_signature(process: dict) -> str:
    root = Path(ROOT_DIR).expanduser().resolve()
    digest = hashlib.sha256()
    _update_stat_digest(digest, Path(CONFIG_PATH).expanduser().resolve())
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        _update_stat_digest(digest, path)
    voicelines = root / "voicelines"
    digest.update(str(voicelines).encode("utf-8"))
    try:
        entries = sorted(
            (
                entry
                for entry in os.scandir(voicelines)
                if entry.is_file(follow_symlinks=False)
            ),
            key=lambda entry: entry.name,
        )
    except FileNotFoundError:
        entries = []
    for entry in entries:
        stat = entry.stat(follow_symlinks=False)
        digest.update(entry.name.encode("utf-8"))
        digest.update(f"\0{stat.st_mtime_ns}\0{stat.st_size}".encode("ascii"))
    takes_root = voicelines / "takes"
    if takes_root.is_dir() and not takes_root.is_symlink():
        for path in sorted(takes_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(voicelines).as_posix()
            stat = path.stat()
            digest.update(relative.encode("utf-8"))
            digest.update(
                f"\0{stat.st_mtime_ns}\0{stat.st_size}".encode("ascii")
            )
    digest.update(
        json.dumps(
            process,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _read_runtime_project(reader, /, *args, **kwargs):
    with _RUNTIME_PROJECT_LOCK:
        return reader(*args, **kwargs)


def _json_payload_response(payload: object) -> Response:
    return Response(
        content=json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ),
        media_type="application/json",
    )


ACTIVE_PROJECT_ID: str | None = None
ACTIVE_PROJECT_STORAGE_KIND = "legacy_checkout"
LEGACY_PROJECT_ID: str | None = None
LEGACY_FLOW_SNAPSHOT: dict | None = None
VOICE_CONFIG_PATH = os.path.join(ROOT_DIR, "voice_config.json")
SCRIPT_PATH = os.path.join(ROOT_DIR, "annotated_script.json")
AUDIOBOOK_PATH = os.path.join(ROOT_DIR, "cloned_audiobook.mp3")
M4B_PATH = os.path.join(ROOT_DIR, "audiobook.m4b")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
CHUNKS_PATH = os.path.join(ROOT_DIR, "chunks.json")
GENERATION_STATE_PATH = os.path.join(
    ROOT_DIR,
    "generation_state.json",
)
CHARACTER_ROSTER_DRAFT_PATH = os.path.join(
    ROOT_DIR,
    "character_roster.draft.json",
)
CHARACTER_ROSTER_PATH = os.path.join(
    ROOT_DIR,
    "character_roster.json",
)
CHARACTER_ROSTER_STATE_PATH = os.path.join(
    ROOT_DIR,
    "character_roster_state.json",
)
CHARACTER_ROSTER_HISTORY_DIR = os.path.join(
    ROOT_DIR,
    "character_roster_history",
)
PERSONA_VISUAL_STATE_PATH = os.path.join(
    ROOT_DIR,
    "persona_visual_state.json",
)
PERSONA_REFS_DIR = os.path.join(ROOT_DIR, "persona_refs")
VOICE_TRAINING_PROJECTS_DIR = os.path.join(
    ROOT_DIR,
    "voice_training_projects",
)
SCRIPT_METADATA_PATH = current_metadata_path(
    SCRIPT_PATH
)
SCRIPT_LIFECYCLE_PATH = os.path.join(
    ROOT_DIR,
    "script_lifecycle.json",
)
AUDIO_VALIDITY_PATH = os.path.join(
    ROOT_DIR,
    "audio_validity.json",
)
DESIGNED_VOICES_DIR = os.path.join(ROOT_DIR, "designed_voices")

RUNTIME_STARTED_AT = datetime.now(timezone.utc)
RUNTIME_SOURCE_PATHS = (
    Path(__file__).resolve(),
    Path(BASE_DIR, "project.py").resolve(),
    Path(BASE_DIR, "tts.py").resolve(),
    Path(BASE_DIR, "mlx_backend.py").resolve(),
    Path(BASE_DIR, "controlled_clone_preview.py").resolve(),
    Path(BASE_DIR, "static", "index.html").resolve(),
    Path(BASE_DIR, "static", "canonical_interface.js").resolve(),
    Path(BASE_DIR, "static", "canonical_pages.css").resolve(),
)
RUNTIME_SOURCE_SNAPSHOT = {
    path: path.stat().st_mtime_ns if path.is_file() else None
    for path in RUNTIME_SOURCE_PATHS
}


def _runtime_changed_sources() -> list[str]:
    changed: list[str] = []
    root = Path(ROOT_DIR).resolve()
    for path, started_mtime in RUNTIME_SOURCE_SNAPSHOT.items():
        current_mtime = path.stat().st_mtime_ns if path.is_file() else None
        if current_mtime == started_mtime:
            continue
        try:
            changed.append(path.relative_to(root).as_posix())
        except ValueError:
            changed.append(path.name)
    return changed


@app.middleware("http")
async def prevent_stale_frontend_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.middleware("http")
async def log_api_request(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    request_id = secrets.token_hex(6)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed = time.perf_counter() - started
        logger.exception(
            "api_request %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "elapsed_seconds": round(elapsed, 3),
                },
                sort_keys=True,
            ),
        )
        raise
    elapsed = time.perf_counter() - started
    payload = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "elapsed_seconds": round(elapsed, 3),
    }
    if response.status_code >= 400:
        logger.warning("api_request %s", json.dumps(payload, sort_keys=True))
    elif elapsed >= 0.75:
        logger.warning("slow_api_request %s", json.dumps(payload, sort_keys=True))
    else:
        logger.info("api_request %s", json.dumps(payload, sort_keys=True))
    response.headers["X-Alexandria-Request-Id"] = request_id
    return response


@app.get("/api/runtime_status")
async def runtime_status():
    changed_sources = _runtime_changed_sources()
    return {
        "process_id": os.getpid(),
        "started_at": RUNTIME_STARTED_AT.isoformat().replace("+00:00", "Z"),
        "restart_required": bool(changed_sources),
        "changed_sources": changed_sources,
        "static_asset_version": _current_static_asset_version(),
        "loaded_static_asset_version": STATIC_ASSET_VERSION,
        "active_project_id": ACTIVE_PROJECT_ID,
        "active_project_root": ROOT_DIR,
        "active_project_storage_kind": ACTIVE_PROJECT_STORAGE_KIND,
        "project_switching": "dynamic",
    }
CLONE_VOICES_DIR = os.path.join(ROOT_DIR, "clone_voices")
LORA_MODELS_DIR = os.path.join(ROOT_DIR, "lora_models")
LORA_DATASETS_DIR = os.path.join(ROOT_DIR, "lora_datasets")
BUILTIN_LORA_DIR = os.path.join(ROOT_DIR, "builtin_lora")
DATASET_BUILDER_DIR = os.path.join(ROOT_DIR, "dataset_builder")
PREPARER_SCRIPT_PATH = os.path.join(BASE_DIR, "alexandria_preparer.py")
PREPARER_OUTPUT_DIR = os.path.join(ROOT_DIR, "preparer_output")
STAGE_LOG_DIR = os.path.join(ROOT_DIR, "logs", "stages")
STAGE_LOG_SPECS = {
    "script": ("script", os.path.join(STAGE_LOG_DIR, "script.json")),
    "persona": ("persona", os.path.join(STAGE_LOG_DIR, "persona.json")),
    "roster": ("roster", os.path.join(STAGE_LOG_DIR, "roster.json")),
    "visual": ("visual", os.path.join(STAGE_LOG_DIR, "visual.json")),
    "audio": ("audio", os.path.join(STAGE_LOG_DIR, "audio.json")),
    "dataset_builder": (
        "dataset_builder",
        os.path.join(STAGE_LOG_DIR, "dataset_builder.json"),
    ),
}
ROSTER_LOG_PATH = STAGE_LOG_SPECS["roster"][1]
EXTERNAL_WORKFLOW_UPLOAD_DIR = os.path.join(
    ROOT_DIR,
    "external_workflows",
    "uploads",
)
EXTERNAL_RESULT_MAX_BYTES = 24 * 1024 * 1024
EXTERNAL_IMPORT_MAX_BYTES = 192 * 1024 * 1024
ALEXANDRIA_APPLICATION_VERSION = "alexandria-apple-phase24c"

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(SCRIPTS_DIR, exist_ok=True)
os.makedirs(DESIGNED_VOICES_DIR, exist_ok=True)
os.makedirs(CLONE_VOICES_DIR, exist_ok=True)
os.makedirs(LORA_MODELS_DIR, exist_ok=True)
os.makedirs(LORA_DATASETS_DIR, exist_ok=True)
os.makedirs(DATASET_BUILDER_DIR, exist_ok=True)
os.makedirs(PREPARER_OUTPUT_DIR, exist_ok=True)
os.makedirs(PERSONA_REFS_DIR, exist_ok=True)

# Mount static files with absolute path
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
def _current_static_asset_version() -> str:
    return str(
        max(
            (
                path.stat().st_mtime_ns
                for path in Path(STATIC_DIR).rglob("*")
                if path.is_file()
            ),
            default=0,
        )
    )


STATIC_ASSET_VERSION = _current_static_asset_version()
_STATIC_INDEX_URL_PATTERN = re.compile(
    r"(?P<quote>['\"])(?P<url>/static/[^'\"]+)(?P=quote)"
)


def _render_index_html() -> str:
    template = Path(STATIC_DIR, "index.html").read_text(encoding="utf-8")
    asset_version = _current_static_asset_version()
    return _STATIC_INDEX_URL_PATTERN.sub(
        lambda match: (
            f"{match.group('quote')}{match.group('url')}"
            f"?v={asset_version}{match.group('quote')}"
        ),
        template,
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Project-scoped static directories are rebound atomically when the active
# project changes. StaticFiles caches its lookup roots, so keep explicit
# instances rather than anonymous mounts.
VOICELINES_DIR = os.path.join(ROOT_DIR, "voicelines")
os.makedirs(VOICELINES_DIR, exist_ok=True)
_voicelines_static = StaticFiles(directory=VOICELINES_DIR)
app.mount("/voicelines", _voicelines_static, name="voicelines")

_designed_voices_static = StaticFiles(directory=DESIGNED_VOICES_DIR)
app.mount("/designed_voices", _designed_voices_static, name="designed_voices")

_clone_voices_static = StaticFiles(directory=CLONE_VOICES_DIR)
app.mount("/clone_voices", _clone_voices_static, name="clone_voices")

_lora_models_static = StaticFiles(directory=LORA_MODELS_DIR)
app.mount("/lora_models", _lora_models_static, name="lora_models")

os.makedirs(BUILTIN_LORA_DIR, exist_ok=True)
_builtin_lora_static = StaticFiles(directory=BUILTIN_LORA_DIR)
app.mount("/builtin_lora", _builtin_lora_static, name="builtin_lora")

_dataset_builder_static = StaticFiles(directory=DATASET_BUILDER_DIR)
app.mount("/dataset_builder", _dataset_builder_static, name="dataset_builder")

# Initialize the legacy checkout. The startup hook may immediately activate the
# last-selected managed project without restarting the Python process.
project_manager = ProjectManager(ROOT_DIR, config_path=CONFIG_PATH)

# Reset any chunks stuck in "generating" from a prior interrupted session
_startup_chunks = project_manager.load_chunks()
if _startup_chunks:
    _reset_count = 0
    for chunk in _startup_chunks:
        if chunk.get("status") == "generating":
            chunk["status"] = "pending"
            chunk["audio_state"] = (
                "stale" if chunk.get("stale_audio_path") else "pending"
            )
            _reset_count += 1
    if _reset_count:
        project_manager.save_chunks(_startup_chunks)
        print(f"Startup: reset {_reset_count} stuck 'generating' chunk(s) to 'pending'")
    del _startup_chunks, _reset_count

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- System Helpers ---

def get_gpu_stats():
    """Get current GPU memory and utilization stats."""
    try:
        import torch
    except ImportError:
        return None

    if not torch.cuda.is_available():
        return None

    stats = {}
    try:
        # Memory stats (works for both NVIDIA and AMD ROCm)
        allocated = torch.cuda.memory_allocated() / 1e9  # GB
        reserved = torch.cuda.memory_reserved() / 1e9    # GB
        total = torch.cuda.get_device_properties(0).total_memory / 1e9  # GB

        stats['allocated_gb'] = allocated
        stats['reserved_gb'] = reserved
        stats['total_gb'] = total
        stats['allocated_percent'] = (allocated / total * 100) if total > 0 else 0

        # Try to get utilization via rocm-smi for AMD GPUs
        try:
            result = subprocess.run(
                ['/opt/rocm/bin/rocm-smi', '--showuse', '--json'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for card_key, card_data in data.items():
                    if not isinstance(card_data, dict):
                        continue
                    for key in ('GPU use (%)', 'GPU Use (%)', 'GPU Activity'):
                        gpu_use_str = card_data.get(key)
                        if gpu_use_str is not None and gpu_use_str != 'N/A':
                            stats['utilization_percent'] = float(gpu_use_str)
                            break
                    break
        except Exception:
            stats['utilization_percent'] = None

    except Exception as e:
        logger.debug(f"Could not get GPU stats: {e}")
        return None

    return stats

def get_compute_stats():
    """Return cross-platform compute activity for the application header."""
    try:
        import platform
        import psutil

        cpu_percent = psutil.cpu_percent(interval=0.05)
        memory = psutil.virtual_memory()
        process = psutil.Process(os.getpid())
        process_rss_gb = process.memory_info().rss / (1024 ** 3)
        machine = platform.machine().lower()
        system = platform.system().lower()
        apple_silicon = system == "darwin" and machine == "arm64"

        stats = {
            "kind": "system_cpu",
            "label": "CPU",
            "scope": "system",
            "utilization_percent": round(float(cpu_percent), 1),
            "system_memory_percent": round(float(memory.percent), 1),
            "process_rss_gb": round(float(process_rss_gb), 2),
            "platform": "Apple Silicon" if apple_silicon else platform.machine(),
        }

        if apple_silicon:
            try:
                import mlx.core as mx

                stats["mlx_active_gb"] = round(
                    float(mx.get_active_memory()) / (1024 ** 3),
                    2,
                )
                stats["mlx_cache_gb"] = round(
                    float(mx.get_cache_memory()) / (1024 ** 3),
                    2,
                )
            except Exception:
                stats["mlx_active_gb"] = None
                stats["mlx_cache_gb"] = None

        return stats
    except Exception as exc:
        logger.debug(f"Could not get compute stats: {exc}")
        return None


def check_disk_space(path, required_gb):
    """Check if disk has enough space. Returns (has_space, free_gb)."""
    try:
        stat = shutil.disk_usage(path)
        free_gb = stat.free / (1024 ** 3)
        return free_gb >= required_gb, free_gb
    except Exception:
        return True, 0

@app.get("/api/system/stats")
async def get_system_stats():
    """Return GPU and Disk statistics."""
    gpu = get_gpu_stats()
    compute = get_compute_stats()
    # Check root dir for disk space
    has_space, free_gb = check_disk_space(ROOT_DIR, 1.0) # 1GB threshold for generic warning

    return {
        "gpu": gpu,
        "compute": compute,
        "platform": compute.get("platform") if compute else None,
        "disk": {
            "free_gb": round(free_gb, 2),
            "low_space": not has_space
        }
    }

def _deep_merge_config(
    existing: dict,
    incoming: dict,
) -> dict:
    result = dict(existing)

    for key, value in incoming.items():
        current = result.get(key)

        if (
            isinstance(current, dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge_config(
                current,
                value,
            )
        else:
            result[key] = value

    return result


# Data Models
class LLMConfig(BaseModel):
    base_url: str = DEFAULT_BASE_URL
    api_key: str = DEFAULT_API_KEY
    model_name: str = DEFAULT_MODEL_NAME
    backend: Literal[
        "auto",
        "ollama",
        "openai",
    ] = DEFAULT_BACKEND
    context_length: int = Field(
        default=DEFAULT_CONTEXT_LENGTH,
        ge=1,
    )
    keep_alive: Union[
        int,
        str,
    ] = DEFAULT_KEEP_ALIVE
    thinking: bool = DEFAULT_THINKING
    structured_output: bool = (
        DEFAULT_STRUCTURED_OUTPUT
    )
    corrective_retry: bool = (
        DEFAULT_CORRECTIVE_RETRY
    )
    profiles: Dict[str, object] = Field(
        default_factory=dict,
    )
    timeout: int = Field(
        default=DEFAULT_TIMEOUT,
        ge=1,
    )

class TTSConfig(BaseModel):
    mode: str = "local"  # "local" or "external"
    url: str = "http://127.0.0.1:7860"  # external mode only
    device: str = "auto"  # local mode: "auto", "cuda:0", "cpu", etc.
    language: str = "English"  # TTS language
    parallel_workers: int = 2  # concurrent TTS workers
    batch_seed: Optional[int] = None  # Single seed for batch mode, None/-1 = random
    deterministic_seed_enabled: bool = True
    deterministic_seed_base: Optional[int] = Field(default=None, ge=0)
    compile_codec: bool = False  # torch.compile the codec for ~3-4x batch throughput (slow first run)
    sub_batch_enabled: bool = True  # split batch by text length to reduce padding waste
    sub_batch_min_size: int = 4  # minimum chunks per sub-batch before allowing a split
    sub_batch_ratio: float = 5.0  # max longest/shortest length ratio before splitting
    sub_batch_max_items: int = 0  # hard cap on sequences per sub-batch (0 = auto from VRAM estimate)
    batch_group_by_type: bool = False  # group chunks by voice type for efficient batching
    pause_between_speakers_ms: int = 500  # silence (ms) between different speakers during merge
    pause_same_speaker_ms: int = 250  # silence (ms) when same speaker continues during merge
    fish_cloud_enabled: bool = False
    fish_model: Literal["s2.1-pro-free", "s2-pro"] = "s2.1-pro-free"
    fish_candidate_count: int = Field(default=2, ge=2, le=6)
    fish_difficult_candidate_count: int = Field(default=4, ge=2, le=8)
    fish_text_wer_limit: float = Field(default=0.08, ge=0.0, le=0.5)
    fish_timeout_seconds: int = Field(default=240, ge=30, le=600)

class GenerationConfig(BaseModel):
    chunk_size: int = 3000
    max_tokens: int = 4096
    temperature: float = 0.6
    top_p: float = 0.8
    top_k: int = 0
    min_p: float = 0
    presence_penalty: float = 0.0
    banned_tokens: List[str] = []
    merge_narrators: bool = False

class PromptConfig(BaseModel):
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    review_system_prompt: Optional[str] = None
    review_user_prompt: Optional[str] = None
    persona_system_prompt: Optional[str] = None
    persona_user_prompt: Optional[str] = None
    persona_advanced_prompt: Optional[str] = None

class AppConfig(BaseModel):
    llm: LLMConfig
    tts: TTSConfig
    prompts: Optional[PromptConfig] = None
    generation: Optional[GenerationConfig] = None


class ApplicationSettingsUpdateRequest(BaseModel):
    expected_config_fingerprint: str
    settings: Dict[str, object]


class RecoveryActionRequest(BaseModel):
    stage_id: str
    action: str


class ProjectOpenRequest(BaseModel):
    expected_catalog_fingerprint: Optional[str] = None


class ProjectDuplicateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    expected_catalog_fingerprint: str


class ProjectArchiveRequest(BaseModel):
    archived: bool = True
    expected_catalog_fingerprint: str
    expected_project_fingerprint: str


class ProjectDeleteRequest(BaseModel):
    confirm_project_id: str
    expected_catalog_fingerprint: str
    expected_project_fingerprint: str
    confirm_dependencies: bool = False


class ProjectTemplateFieldsRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    generation_method: Literal[
        "local",
        "chatgpt_task_bundle",
        "import_existing_script",
    ] = "local"
    preset: Literal[
        "standard",
        "maximum_fidelity",
        "faster_draft",
        "custom",
    ] = "standard"
    source_language: str = Field(default="English", min_length=1, max_length=80)
    output_language: str = Field(default="English", min_length=1, max_length=80)
    intent: str = Field(min_length=1, max_length=120)


class ProjectTemplateCreateRequest(BaseModel):
    expected_catalog_fingerprint: str
    template: ProjectTemplateFieldsRequest


class ProjectTemplateUpdateRequest(BaseModel):
    expected_catalog_fingerprint: str
    expected_template_fingerprint: str
    template: ProjectTemplateFieldsRequest


class ProjectTemplateDuplicateRequest(BaseModel):
    expected_catalog_fingerprint: str
    name: str = Field(min_length=1, max_length=80)


class ProjectTemplateDefaultRequest(BaseModel):
    expected_catalog_fingerprint: str


class ProjectTemplateDeleteRequest(BaseModel):
    expected_catalog_fingerprint: str
    expected_template_fingerprint: str
    confirmation_text: str = Field(min_length=1, max_length=80)
    acknowledge_usage: bool = False


class ScriptLifecycleAcceptRequest(BaseModel):
    expected_script_fingerprint: str
    expected_metadata_fingerprint: str
    expected_source_fingerprint: str
    expected_state_fingerprint: Optional[str] = None
    allow_reviewed_source_differences: bool = False
    expected_audit_fingerprint: Optional[str] = None


class ScriptLifecycleRejectRequest(BaseModel):
    expected_script_fingerprint: str
    expected_state_fingerprint: Optional[str] = None
    reason: str = Field(min_length=1, max_length=2000)


class ScriptLifecycleHandoffRequest(BaseModel):
    expected_state_fingerprint: Optional[str] = None


class ManagedScriptImportApplyRequest(BaseModel):
    expected_candidate_fingerprint: str


class ScriptLifecycleRollbackRequest(BaseModel):
    expected_current_script_fingerprint: str
    expected_source_fingerprint: str
    expected_state_fingerprint: Optional[str] = None


class ScriptCandidateAcceptRequest(BaseModel):
    expected_current_script_fingerprint: Optional[str] = None
    expected_current_metadata_fingerprint: Optional[str] = None
    expected_current_voice_config_fingerprint: Optional[str] = None
    expected_current_chunks_fingerprint: Optional[str] = None
    checkpoint_decision: Optional[
        Literal["keep", "discard", "cancel"]
    ] = None
    expected_source_fingerprint: str
    expected_lifecycle_state_fingerprint: Optional[str] = None


class ChatGPTHandoffExportRequest(BaseModel):
    task_type: Literal[
        "script_generation",
        "script_review",
        "roster_discovery",
        "roster_reconciliation",
        "persona_generation",
        "visual_discovery",
    ] = "script_generation"
    target: Optional[str] = None


class TaskBundleExportRequest(BaseModel):
    task_type: str
    target: Optional[str] = None
    options: Dict[str, bool] = Field(default_factory=dict)


class StructuredResultTransferRequest(BaseModel):
    result_fingerprint: str
    replace_persona_draft: bool = False
    persona_catalog_decision: bool = False
    replace_persona_speakers: List[str] = Field(default_factory=list)


class AnnotatedScriptApplyRequest(BaseModel):
    candidate_id: str
    checkpoint_decision: Optional[
        Literal["keep", "discard", "cancel"]
    ] = None


class AnnotatedScriptRollbackRequest(BaseModel):
    operation_id: str


class VoiceLibraryAssignRequest(BaseModel):
    character_id: str
    voice_id: str
    expected_voice_config_fingerprint: Optional[str] = None


class VoiceLibraryClearRequest(BaseModel):
    character_id: str
    expected_voice_config_fingerprint: Optional[str] = None


class PronunciationPreviewRequest(BaseModel):
    chunk_index: int = Field(ge=0)
    candidate_entry: Optional[Dict[str, Any]] = None
    generate_audio: bool = False


class PronunciationUpsertRequest(BaseModel):
    entry: Dict[str, Any]
    expected_registry_fingerprint: Optional[str] = None


class PronunciationDeleteRequest(BaseModel):
    expected_registry_fingerprint: Optional[str] = None


class CommunityQwenPackApproveRequest(BaseModel):
    expected_preview_fingerprint: str = Field(min_length=64, max_length=64)


class CommunityQwenPackPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    persistent_description: str = Field(min_length=1, max_length=1000)
    direction: str = Field(min_length=1, max_length=500)
    generation_seed: int = Field(default=130363, ge=0, le=2_147_483_647)


class CommunityQwenDirectoryRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4096)
    q_bits: Literal[4, 8] = 8


class CommunityQwenCandidateInstallRequest(BaseModel):
    q_bits: Literal[4, 8] = 8
    cleanup_downloaded_source: bool = True


class VoiceConfigItem(BaseModel):
    alias_of: Optional[str] = None
    library_voice_id: Optional[str] = None
    type: str = "custom"
    voice: Optional[str] = "Ryan"
    character_style: Optional[str] = ""
    default_style: Optional[str] = ""  # backward compat, prefer character_style
    seed: Optional[str] = "-1"
    ref_audio: Optional[str] = None
    ref_text: Optional[str] = None
    clone_backend: Optional[Literal[
        "qwen3_base",
        "qwen3_instruction_controlled",
        "voxcpm2_controlled",
        "fish_s21_cloud",
        "alexandria_responsive_router",
    ]] = "qwen3_base"
    expressive_clone_cfg_value: float = 2.0
    expressive_clone_steps: int = 10
    expressive_clone_max_tokens: int = 2000
    instruction_clone_temperature: float = 0.75
    instruction_clone_top_k: int = 50
    instruction_clone_top_p: float = 0.95
    instruction_clone_repetition_penalty: float = 1.5
    instruction_clone_max_tokens: int = 2000
    fish_temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    fish_top_p: float = Field(default=0.7, ge=0.0, le=1.0)
    fish_repetition_penalty: float = Field(default=1.2, ge=1.0, le=3.0)
    fish_latency: Literal["normal", "balanced"] = "normal"
    fish_hybrid_enabled: bool = False
    fish_hybrid_styles: List[
        Literal["fear", "grief", "sarcasm", "expressive"]
    ] = Field(
        default_factory=lambda: ["fear", "grief", "sarcasm", "expressive"]
    )
    fish_hybrid_use_approved_routes: bool = True
    fish_hybrid_fallback_to_local: bool = True
    controlled_clone_approval_token: Optional[str] = None
    controlled_clone_configuration_fingerprint: Optional[str] = None
    reference_bank_path: Optional[str] = None
    reference_bank_character_id: Optional[str] = None
    reference_bank_fingerprint: Optional[str] = None
    approved_adaptation_profile_path: Optional[str] = None
    approved_adaptation_profile_fingerprint: Optional[str] = None
    approved_adaptation_identity_candidate_id: Optional[str] = None
    approved_adaptation_identity_basis: Optional[str] = None
    approved_adaptation_alignment_count: Optional[int] = None
    approved_adaptation_expressive_reference_count: Optional[int] = None
    approved_adaptation_style_source: Optional[str] = None
    approved_adaptation_style_source_path: Optional[str] = None
    approved_adaptation_style_approval_status: Optional[str] = None
    community_pack_id: Optional[str] = None
    community_pack_path: Optional[str] = None
    community_pack_family: Optional[str] = None
    community_pack_runtime: Optional[str] = None
    community_pack_sha256: Optional[str] = None
    community_pack_approval_fingerprint: Optional[str] = None
    adapter_id: Optional[str] = None
    adapter_path: Optional[str] = None
    mlx_model_path: Optional[str] = None
    lora_mlx_temperature: float = 0.9
    lora_mlx_top_k: int = 50
    lora_mlx_top_p: float = 1.0
    lora_mlx_repetition_penalty: float = 1.5
    lora_mlx_max_tokens: int = 2000
    instruction_propagation: Optional[Dict[str, object]] = None
    experimental_prompt_routing: Optional[Dict[str, object]] = None
    responsive_backend_routing: Optional[Dict[str, object]] = None
    responsive_backend_configuration_fingerprint: Optional[str] = None
    description: Optional[str] = ""  # voice description (for design type)

class FishHybridMigrationRequest(BaseModel):
    enabled: bool = True
    dry_run: bool = False


class ChunkUpdate(BaseModel):
    text: Optional[str] = None
    instruct: Optional[str] = None
    speaker: Optional[str] = None
    pause_after: Optional[int] = None

class ChunkGenerateRequest(BaseModel):
    generation_seed: Optional[int] = Field(default=None, ge=0)
    replace_active: bool = False


class ApprovedAudioPromotionRequest(BaseModel):
    manifest_path: str
    confirm_installation: bool = False
    include_restricted: bool = False
    promote_voice_evidence: bool = True


class ApprovedAudioRollbackRequest(BaseModel):
    receipt_path: str
    confirm_rollback: bool = False


class BatchGenerateRequest(BaseModel):
    indices: List[int]
    generation_seed: Optional[int] = Field(default=None, ge=0)
    replace_active: bool = False
    worker_count: Optional[int] = Field(default=None, ge=1, le=8)
    batch_size: Optional[int] = Field(default=None, ge=1, le=32)
    group_by_type: Optional[bool] = None
    operation_id: Optional[str] = None
    operation_mode: Optional[str] = None
    plan_fingerprint: Optional[str] = None
    chunks_fingerprint: Optional[str] = None


class ProducePlanRequest(BaseModel):
    replace_active: bool = False
    mode: Literal[
        "missing_stale",
        "ready_only",
        "retry_failed",
        "regenerate_all",
        "selected",
    ] = "missing_stale"
    selected_chunk_ids: List[str] = Field(default_factory=list)


class ProduceExecuteRequest(ProducePlanRequest):
    plan_fingerprint: str
    chunks_fingerprint: str
    confirm_regenerate_all: bool = False


class ProduceInvalidateRequest(BaseModel):
    selected_chunk_ids: List[str]
    chunks_fingerprint: str
    reason: str = Field(min_length=1, max_length=300)


class AudioTakeSelectionRequest(BaseModel):
    take_id: str = Field(min_length=1, max_length=160)
    registry_fingerprint: str = Field(min_length=64, max_length=64)
    record_fingerprint: str = Field(min_length=64, max_length=64)


class AudioTakeKeepRequest(AudioTakeSelectionRequest):
    kept: bool


class AudioTakeDeleteRequest(BaseModel):
    take_id: str = Field(min_length=1, max_length=160)
    impact_fingerprint: str = Field(min_length=64, max_length=64)


class AudioTakeCleanupRequest(BaseModel):
    older_than_days: int = Field(default=30, ge=0, le=36500)
    reclaim_at_least_bytes: int = Field(default=0, ge=0)


class AudioTakeCleanupApplyRequest(AudioTakeCleanupRequest):
    impact_fingerprint: str = Field(min_length=64, max_length=64)


class AudioTakeUndoRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=160)
    registry_fingerprint: str = Field(min_length=64, max_length=64)


class ExportMetadataRequest(BaseModel):
    title: str = ""
    author: str = ""
    narrator: str = ""
    year: str = ""
    description: str = ""


class ExportPlanRequest(BaseModel):
    metadata: ExportMetadataRequest = Field(
        default_factory=ExportMetadataRequest
    )
    formats: List[str] = Field(default_factory=lambda: ["mp3"])
    chapter_mode: Literal["smart", "per_chunk", "none"] = "smart"


class ExportBuildRequest(ExportPlanRequest):
    plan_fingerprint: str
    dependency_fingerprint: str


class LibraryContextRequest(BaseModel):
    project_id: Optional[str] = None
    character_id: Optional[str] = None
    return_route: Optional[str] = "#/library"


class LibraryDeleteRequest(LibraryContextRequest):
    expected_inventory_fingerprint: str
    expected_artifact_fingerprint: str
    confirm_name: str


class VoiceDesignPreviewRequest(BaseModel):
    description: str
    sample_text: str
    language: Optional[str] = None


class VoiceDesignRangePreviewRequest(VoiceDesignPreviewRequest):
    persona_context: str = Field(default="", max_length=6000)


class BuiltInVoiceRangePreviewRequest(BaseModel):
    voice: str
    persistent_description: str = Field(default="", max_length=2000)


class ControlledClonePreviewRequest(BaseModel):
    speaker: str
    ref_audio: str
    ref_text: str
    text: str
    instruct: str
    character_style: str = ""
    temperature: float = 0.75
    top_k: int = Field(default=50, ge=1, le=200)
    top_p: float = 0.95
    repetition_penalty: float = 1.5
    max_tokens: int = 2000
    seed: int = Field(default=-1, ge=-1)


class ControlledClonePreviewConfirmRequest(BaseModel):
    speaker: str
    preview_fingerprint: str
    configuration_fingerprint: str


class AccentPipelineStatusRequest(BaseModel):
    description: str = ""
    output_language: Optional[str] = None

class VoiceDesignSaveRequest(BaseModel):
    name: str
    description: str
    sample_text: str
    preview_file: str
    scope: Literal["project", "reusable"] = "project"

class LoraTrainingRequest(BaseModel):
    name: str
    dataset_id: str
    epochs: int = Field(default=5, ge=1, le=200)
    lr: float = Field(default=5e-6, gt=0, le=0.01)
    batch_size: int = Field(default=1, ge=1, le=8)
    lora_r: int = Field(default=32, ge=1, le=256)
    lora_alpha: int = Field(default=128, ge=1, le=1024)
    gradient_accumulation_steps: int = Field(default=8, ge=1, le=256)
    language: str = "english"
    lora_target_profile: Literal["attention", "attention_mlp"] = "attention"
    validation_fraction: float = Field(default=0.1, ge=0, lt=1)
    seed: int = 1337
    instruction_mode: Literal["identity_only", "per_record"] = "identity_only"
    max_samples: Optional[int] = Field(default=None, ge=2)
    max_audio_seconds: float = Field(default=30.0, gt=0, le=120)
    local_files_only: bool = True

class LoraTestRequest(BaseModel):
    adapter_id: str
    text: str
    instruct: str = ""

class LoraDatasetSample(BaseModel):
    emotion: str = ""
    text: str

class LoraGenerateDatasetRequest(BaseModel):
    name: str
    description: str  # root voice description
    samples: Optional[List[LoraDatasetSample]] = None  # emotion+text pairs
    texts: Optional[List[str]] = None  # legacy: flat text list (no emotions)
    language: Optional[str] = None

class DatasetSampleGenRequest(BaseModel):
    description: str      # full voice description (root + emotion already combined by frontend)
    text: str
    dataset_name: str     # working directory name
    sample_index: int     # row number
    seed: int = -1        # -1 = random, >= 0 = manual seed

class DatasetBatchGenRequest(BaseModel):
    name: str
    description: str      # root voice description
    samples: List[LoraDatasetSample]
    indices: Optional[List[int]] = None  # which rows to generate (None = all)
    global_seed: int = -1 # -1 = random, >= 0 = same seed for all lines
    seeds: Optional[List[int]] = None  # per-line seeds (overrides global_seed)

class DatasetSaveRequest(BaseModel):
    name: str
    ref_index: int = 0    # which sample to use as ref.wav

class DatasetBuilderCreateRequest(BaseModel):
    name: str

class DatasetBuilderUpdateMetaRequest(BaseModel):
    name: str
    description: str = ""
    global_seed: str = ""

class DatasetBuilderUpdateRowsRequest(BaseModel):
    name: str
    rows: List[dict]  # [{emotion, text, seed}]

class ContextualReviewRequest(BaseModel):
    window_size: int = 4


class LegacyScriptRepairRequest(BaseModel):
    confirm: bool = False
    start_marker: Optional[str] = None


class GeneratePersonasRequest(BaseModel):
    advanced: bool = False
    batch_size: int = 40


class CharacterRosterDiscoverRequest(BaseModel):
    replace_draft: bool = False
    passage_size: int = 12000
    overlap_chars: int = 1200


class CharacterRosterActionRequest(BaseModel):
    draft_fingerprint: str
    action: Literal[
        "confirm",
        "rename",
        "add_alias",
        "reject_alias",
        "keep_separate",
        "merge",
        "mark_unresolved",
        "exclude",
    ]
    entry_id: Optional[str] = None
    other_entry_id: Optional[str] = None
    value: Optional[str] = None
    display_name: Optional[str] = None
    reason: Optional[str] = None
    preserve_old_as_alias: bool = True


class CharacterRosterApproveRequest(BaseModel):
    draft_fingerprint: str
    acknowledged_unresolved: bool = False
    replace_existing: bool = False
    expected_approved_fingerprint: Optional[str] = None


class CharacterRosterRollbackRequest(BaseModel):
    revision_id: str
    expected_current_fingerprint: str


class RosterImportDecision(BaseModel):
    import_id: str
    action: Literal["merge", "add", "exclude", "unresolved"]
    current_entry_id: Optional[str] = None


class RosterImportApplyRequest(BaseModel):
    candidate_id: str
    result_fingerprint: str
    current_kind: Literal["none", "draft", "approved"]
    current_fingerprint: Optional[str] = None
    decisions: List[RosterImportDecision]
    create_designed_voice_profiles: bool = True
    discover_visual_details: bool = True


class RosterDraftRestoreRequest(BaseModel):
    candidate_id: str
    result_fingerprint: str
    draft_fingerprint: str
    expected_approved_fingerprint: Optional[str] = None
    decisions: List[RosterImportDecision]


class RosterEnrichmentStartRequest(BaseModel):
    expected_plan_fingerprint: str
    expected_roster_fingerprint: str


class RosterEnrichmentRunSelectedRequest(BaseModel):
    expected_roster_fingerprint: str
    create_designed_voice_profiles: bool = True
    discover_visual_details: bool = True


class CastDossierActivateRequest(BaseModel):
    expected_roster_fingerprint: str
    import_voice_dossiers: bool = True
    import_visual_dossiers: bool = True
    identity_crosswalk: Dict[str, str] = Field(default_factory=dict)
    excluded_visual_identity_keys: List[str] = Field(default_factory=list)


class RosterIssueApplyRequest(BaseModel):
    candidate_id: str
    result_fingerprint: str
    current_kind: Literal["none", "draft", "approved"]
    current_fingerprint: Optional[str] = None
    decisions: List[RosterImportDecision] = Field(default_factory=list)
    create_designed_voice_profiles: bool = True
    discover_visual_details: bool = True


class RosterReconciliationApproveRequest(BaseModel):
    action: Literal["approve_resolved", "approve_with_unresolved"]
    draft_fingerprint: str
    expected_approved_fingerprint: Optional[str] = None


class CharacterVisualDiscoverRequest(BaseModel):
    enabled: bool = False
    entry_ids: List[str] = Field(default_factory=list)
    passage_size: int = 12000
    overlap_chars: int = 1200


class VoiceTrainingCreateRequest(BaseModel):
    priority: Literal["primary", "secondary", "experimental"]
    desired_description: str = ""
    desired_ref_text: str = ""


class VoiceTrainingActionRequest(BaseModel):
    project_fingerprint: str
    action: Literal[
        "update_persona",
        "approve_persona",
        "create_synthetic_project",
        "add_synthetic_sample",
        "review_synthetic_sample",
        "create_recording_project",
        "add_recording_file",
        "add_recording_clip",
        "review_recording_clip",
        "approve_dataset",
        "record_dataset_export",
        "select_reference",
        "refresh_readiness",
        "record_adapter_provenance",
        "record_adapter_validation",
        "assign_adapter",
    ]
    payload: Dict[str, object] = Field(default_factory=dict)


class ExpressiveReferenceBankCreateRequest(BaseModel):
    identity_seed: Optional[int] = Field(default=None, ge=0)
    source_clip_id: Optional[str] = None


class ExpressiveReferenceBankGenerateRequest(BaseModel):
    bank_fingerprint: str
    style_key: str
    reference_text: str
    instruction: Optional[str] = None


class ExpressiveReferenceBankActionRequest(BaseModel):
    bank_fingerprint: str
    action: Literal[
        "add_owned_recording_reference",
        "review_reference",
        "review_comparison",
        "approve_bank",
        "return_to_draft",
    ]
    payload: Dict[str, object] = Field(default_factory=dict)


class ExpressiveReferenceBankComparisonLine(BaseModel):
    text: str
    instruct: str = ""


class ExpressiveReferenceBankCompareRequest(BaseModel):
    bank_fingerprint: str
    lines: List[ExpressiveReferenceBankComparisonLine]


class ExpressiveReferenceBankAssignRequest(BaseModel):
    bank_fingerprint: str
    assign: bool = True
    voice_name: Optional[str] = None


class SpeakerManagementActionRequest(BaseModel):
    operation: Literal[
        "add",
        "resolve",
        "rename",
        "add_alias",
        "remove_alias",
        "mark_unresolved",
        "merge",
        "exclude",
        "split",
        "reassign",
    ]
    expected_script_fingerprint: str
    payload: Dict[str, object] = Field(default_factory=dict)


class SpeakerManagementUndoRequest(BaseModel):
    operation_id: str


class LLMProfileUpdateRequest(BaseModel):
    expected_profiles_fingerprint: str
    profile: Dict[str, object]


class LLMProfileRemoveRequest(BaseModel):
    expected_profiles_fingerprint: str


class TrainingSidecarCreateJobRequest(BaseModel):
    action: Literal[
        "setup",
        "environment",
        "model_probe",
        "inspect_targets",
        "train_sft",
        "train_lora",
        "merge_lora",
        "export_mlx",
    ]
    payload: Dict[str, object] = Field(default_factory=dict)


class TrainingSidecarExecuteRequest(BaseModel):
    timeout: Optional[float] = None


class TrainingSidecarImportRequest(BaseModel):
    source_path: str


class MigrationApplyRequest(BaseModel):
    plan_fingerprint: str
    confirm: bool = False


class MigrationRollbackRequest(BaseModel):
    operation_id: str


class ModelRegistryActionRequest(BaseModel):
    action: Literal["download", "repair", "download_required"]
    model_key: Optional[str] = None


class ModelMemoryPolicyRequest(BaseModel):
    minimum_headroom_bytes: int
    idle_unload_seconds: int
    release_and_retry_on_oom: bool


class PreparerConfig(BaseModel):
    audio_filename: str
    output_filename: str = "alexandria_dataset.zip"
    lang: str = "en"
    min_confidence: float = 0.85
    min_snr: int = 25

class BatchPreparerTask(BaseModel):
    audio_filename: str
    output_filename: str

class BatchPreparerRequest(BaseModel):
    tasks: List[BatchPreparerTask]
    lang: str = "en"
    min_confidence: float = 0.85
    min_snr: int = 25

# Global state for process tracking
_MODEL_CACHE_OPERATION_LOCK = threading.Lock()


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _voice_config_project_root() -> Path:
    return Path(VOICE_CONFIG_PATH).expanduser().resolve().parent


def _audio_invalidation_http_error(exc: AudioInvalidationError) -> None:
    conflict_codes = {
        "audio_invalidation_already_undone",
        "audio_invalidation_operation_exists",
        "audio_invalidation_undo_conflict",
    }
    validation_codes = {
        "audio_invalidation_change_conflict",
        "audio_invalidation_chunks_invalid",
        "audio_invalidation_dependency_unsafe",
        "audio_invalidation_operation_invalid",
        "audio_invalidation_operation_missing",
        "audio_invalidation_snapshot_invalid",
    }
    raise HTTPException(
        status_code=(
            409
            if exc.code in conflict_codes
            else 422
            if exc.code in validation_codes
            else 500
        ),
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def _audio_invalidation_summary(record: dict[str, Any]) -> dict[str, Any]:
    canonical = record.get("audio_invalidation")
    canonical = canonical if isinstance(canonical, dict) else {}
    invalidated = canonical.get("invalidated_chunks")
    invalidated = invalidated if isinstance(invalidated, list) else []
    return {
        "operation_id": record.get("operation_id"),
        "affected_speakers": list(record.get("affected_speakers") or []),
        "affected_chunk_ids": list(canonical.get("affected_chunk_ids") or []),
        "invalidated_count": len(invalidated),
        "undo_available": bool(record.get("files")),
    }


def _apply_voice_config_dependency_change(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    operation: str,
    reason: str,
    byte_changes: dict[Path, bytes | None] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if before == after and not byte_changes:
        return None
    at_utc = _utc_now_text()
    affected_speakers = affected_voice_dependency_speakers(before, after)
    operation_id = operation + "_" + fingerprint_value(
        {
            "before": before,
            "after": after,
            "byte_paths": sorted(
                str(path) for path in (byte_changes or {})
            ),
            "at_utc": at_utc,
        }
    )[:24]
    record_metadata = {
        "affected_speakers": affected_speakers,
        **copy.deepcopy(metadata or {}),
    }
    try:
        return apply_speaker_audio_dependency_change(
            project_root=_voice_config_project_root(),
            operation_id=operation_id,
            operation=operation,
            at_utc=at_utc,
            speakers=affected_speakers,
            reason=reason,
            changes={
                Path(VOICE_CONFIG_PATH): after if after else None,
            },
            byte_changes=byte_changes,
            dependency_kind="production_voice",
            record_metadata=record_metadata,
        )
    except AudioInvalidationError as exc:
        _audio_invalidation_http_error(exc)


process_state = {
    "script": {"running": False, "logs": []},
    "render_plan": {
        "running": False,
        "logs": [],
        "cancel": False,
        "process": None,
        "started_at": None,
        "finished_at": None,
        "last_error": None,
    },
    "persona": {"running": False, "logs": [], "cancel": False, "process": None},
    "roster": {"running": False, "logs": [], "cancel": False, "process": None},
    "roster_enrichment": {
        "running": False,
        "logs": [],
        "cancel": False,
        "stage": "idle",
        "started_at": None,
        "finished_at": None,
        "error": None,
    },
    "visual": {"running": False, "logs": [], "cancel": False, "process": None},
    "audio": {
        "running": False,
        "logs": [],
        "cancel": False,
        "operation_id": None,
        "mode": None,
        "plan_fingerprint": None,
        "chunks_fingerprint": None,
        "queued_chunk_ids": [],
        "total_count": 0,
        "completed_count": 0,
        "failed_count": 0,
        "cancelled_count": 0,
        "worker_limit": None,
        "started_at": None,
        "finished_at": None,
        "last_error": None,
        "request_id": None,
        "request_fingerprint": None,
        "owner_token": None,
        "replacement_request_id": None,
    },
    "audacity_export": {"running": False, "logs": []},
    "m4b_export": {"running": False, "logs": []},
    "export": {
        "running": False,
        "logs": [],
        "cancel": False,
        "cancel_requested": False,
        "operation_id": None,
        "plan_fingerprint": None,
        "dependency_fingerprint": None,
        "formats": [],
        "phase": "idle",
        "phase_label": "Idle",
        "completed_count": 0,
        "total_count": 0,
        "overall_percent": 0,
        "progress_message": None,
        "started_at": None,
        "finished_at": None,
        "last_error": None,
        "result": None,
    },
    "review": {
        "running": False,
        "logs": [],
        "process": None,
        "mode": None,
        "window_size": 0,
        "total_entries": 0,
        "batch_size": 25,
        "estimated_calls": 0,
        "started_at": None,
        "finished_at": None,
        "return_code": None,
        "last_error": None,
    },
    "lora_training": {
        "running": False,
        "logs": [],
        "stage": "idle",
        "adapter_id": None,
        "job_id": None,
        "result": None,
        "error": None,
        "failed_stage": None,
    },
    "dataset_gen": {"running": False, "logs": []},
    "dataset_builder": {"running": False, "logs": [], "cancel": False},
    "preparer": {"running": False, "logs": [], "cancel": False, "process": None, "status": "idle", "output_file": None},
    "model_cache": {
        "running": False,
        "logs": [],
        "status": "idle",
        "action": None,
        "model_keys": [],
        "current_model_key": None,
        "current_operation": None,
        "completed_count": 0,
        "total_count": 0,
        "results": [],
        "error": None,
        "error_code": None,
        "started_at": None,
        "finished_at": None,
    },
    "batch_preparer": {"running": False, "logs": [], "cancel": False, "process": None, "status": "idle", "tasks": [], "current_task_idx": -1},
}

_PROJECT_SCOPED_PROCESS_KEYS = (
    "script",
    "render_plan",
    "persona",
    "roster",
    "roster_enrichment",
    "visual",
    "audio",
    "audacity_export",
    "m4b_export",
    "export",
    "review",
    "lora_training",
    "dataset_gen",
    "dataset_builder",
    "preparer",
    "batch_preparer",
)
_PROJECT_PROCESS_DEFAULTS = {
    key: copy.deepcopy(process_state[key])
    for key in _PROJECT_SCOPED_PROCESS_KEYS
}


def _project_switch_blockers() -> list[str]:
    return [
        key
        for key in _PROJECT_SCOPED_PROCESS_KEYS
        if process_state.get(key, {}).get("running") is True
    ]


def _assert_runtime_project_switch_available() -> None:
    blockers = _project_switch_blockers()
    if not blockers:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "project_activation_operation_running",
            "message": (
                "Finish or cancel the current project operation before "
                "switching projects."
            ),
            "context": {"operations": blockers},
        },
    )


def _reset_project_process_state() -> None:
    for key, default in _PROJECT_PROCESS_DEFAULTS.items():
        process_state[key].clear()
        process_state[key].update(copy.deepcopy(default))


def _set_static_directory(static_app: StaticFiles, directory: str) -> None:
    resolved = str(Path(directory).expanduser().resolve())
    Path(resolved).mkdir(parents=True, exist_ok=True)
    static_app.directory = resolved
    static_app.all_directories = [resolved]
    static_app.config_checked = False


def _runtime_project_path_map(root_dir: str | Path) -> dict[str, str]:
    root = Path(root_dir).expanduser().resolve()
    script = root / "annotated_script.json"
    stage_logs = root / "logs" / "stages"
    return {
        "root": str(root),
        "voice_config": str(root / "voice_config.json"),
        "script": str(script),
        "audiobook": str(root / "cloned_audiobook.mp3"),
        "m4b": str(root / "audiobook.m4b"),
        "scripts": str(root / "scripts"),
        "chunks": str(root / "chunks.json"),
        "generation_state": str(root / "generation_state.json"),
        "roster_draft": str(root / "character_roster.draft.json"),
        "roster": str(root / "character_roster.json"),
        "roster_state": str(root / "character_roster_state.json"),
        "roster_history": str(root / "character_roster_history"),
        "visual_state": str(root / "persona_visual_state.json"),
        "persona_refs": str(root / "persona_refs"),
        "voice_training": str(root / "voice_training_projects"),
        "script_metadata": str(current_metadata_path(script)),
        "script_lifecycle": str(root / "script_lifecycle.json"),
        "audio_validity": str(root / "audio_validity.json"),
        "designed_voices": str(root / "designed_voices"),
        "clone_voices": str(root / "clone_voices"),
        "lora_models": str(root / "lora_models"),
        "lora_datasets": str(root / "lora_datasets"),
        "builtin_lora": str(root / "builtin_lora"),
        "dataset_builder": str(root / "dataset_builder"),
        "preparer_output": str(root / "preparer_output"),
        "stage_logs": str(stage_logs),
        "external_uploads": str(root / "external_workflows" / "uploads"),
        "voicelines": str(root / "voicelines"),
    }


_RUNTIME_PROJECT_BINDING_NAMES = (
    "ROOT_DIR",
    "VOICE_CONFIG_PATH",
    "SCRIPT_PATH",
    "AUDIOBOOK_PATH",
    "M4B_PATH",
    "SCRIPTS_DIR",
    "CHUNKS_PATH",
    "GENERATION_STATE_PATH",
    "CHARACTER_ROSTER_DRAFT_PATH",
    "CHARACTER_ROSTER_PATH",
    "CHARACTER_ROSTER_STATE_PATH",
    "CHARACTER_ROSTER_HISTORY_DIR",
    "PERSONA_VISUAL_STATE_PATH",
    "PERSONA_REFS_DIR",
    "VOICE_TRAINING_PROJECTS_DIR",
    "SCRIPT_METADATA_PATH",
    "SCRIPT_LIFECYCLE_PATH",
    "AUDIO_VALIDITY_PATH",
    "DESIGNED_VOICES_DIR",
    "CLONE_VOICES_DIR",
    "LORA_MODELS_DIR",
    "LORA_DATASETS_DIR",
    "BUILTIN_LORA_DIR",
    "DATASET_BUILDER_DIR",
    "PREPARER_OUTPUT_DIR",
    "STAGE_LOG_DIR",
    "STAGE_LOG_SPECS",
    "ROSTER_LOG_PATH",
    "EXTERNAL_WORKFLOW_UPLOAD_DIR",
    "VOICELINES_DIR",
    "DESIGNED_VOICES_MANIFEST",
    "CLONE_VOICES_MANIFEST",
    "LORA_MODELS_MANIFEST",
    "project_manager",
    "ACTIVE_PROJECT_ID",
    "ACTIVE_PROJECT_STORAGE_KIND",
)

_RUNTIME_PROJECT_STATIC_APPS = (
    _voicelines_static,
    _designed_voices_static,
    _clone_voices_static,
    _lora_models_static,
    _builtin_lora_static,
    _dataset_builder_static,
)


def _copy_project_process_state(value: dict) -> dict:
    copied = {}
    for key, item in value.items():
        if key == "process":
            copied[key] = item
            continue
        try:
            copied[key] = copy.deepcopy(item)
        except (TypeError, ValueError):
            copied[key] = item
    return copied


def _capture_runtime_project_binding() -> dict:
    captured_globals = {}
    for name in _RUNTIME_PROJECT_BINDING_NAMES:
        value = globals()[name]
        captured_globals[name] = (
            copy.deepcopy(value) if name == "STAGE_LOG_SPECS" else value
        )
    return {
        "globals": captured_globals,
        "process_state": {
            key: _copy_project_process_state(process_state[key])
            for key in _PROJECT_SCOPED_PROCESS_KEYS
        },
        "static_apps": [
            {
                "app": static_app,
                "directory": static_app.directory,
                "all_directories": list(static_app.all_directories),
                "config_checked": static_app.config_checked,
            }
            for static_app in _RUNTIME_PROJECT_STATIC_APPS
        ],
    }


def _restore_runtime_project_binding(snapshot: dict) -> None:
    for name, value in snapshot["globals"].items():
        globals()[name] = value
    for key, value in snapshot["process_state"].items():
        process_state[key].clear()
        process_state[key].update(_copy_project_process_state(value))
    for item in snapshot["static_apps"]:
        static_app = item["app"]
        static_app.directory = item["directory"]
        static_app.all_directories = list(item["all_directories"])
        static_app.config_checked = item["config_checked"]


def _runtime_project_candidate_globals(
    *,
    paths: dict[str, str],
    project_id: str,
    storage_kind: str,
    manager: ProjectManager,
) -> dict:
    stage_log_specs = {
        "script": ("script", os.path.join(paths["stage_logs"], "script.json")),
        "persona": ("persona", os.path.join(paths["stage_logs"], "persona.json")),
        "roster": ("roster", os.path.join(paths["stage_logs"], "roster.json")),
        "visual": ("visual", os.path.join(paths["stage_logs"], "visual.json")),
        "audio": ("audio", os.path.join(paths["stage_logs"], "audio.json")),
        "dataset_builder": (
            "dataset_builder",
            os.path.join(paths["stage_logs"], "dataset_builder.json"),
        ),
    }
    return {
        "ROOT_DIR": paths["root"],
        "VOICE_CONFIG_PATH": paths["voice_config"],
        "SCRIPT_PATH": paths["script"],
        "AUDIOBOOK_PATH": paths["audiobook"],
        "M4B_PATH": paths["m4b"],
        "SCRIPTS_DIR": paths["scripts"],
        "CHUNKS_PATH": paths["chunks"],
        "GENERATION_STATE_PATH": paths["generation_state"],
        "CHARACTER_ROSTER_DRAFT_PATH": paths["roster_draft"],
        "CHARACTER_ROSTER_PATH": paths["roster"],
        "CHARACTER_ROSTER_STATE_PATH": paths["roster_state"],
        "CHARACTER_ROSTER_HISTORY_DIR": paths["roster_history"],
        "PERSONA_VISUAL_STATE_PATH": paths["visual_state"],
        "PERSONA_REFS_DIR": paths["persona_refs"],
        "VOICE_TRAINING_PROJECTS_DIR": paths["voice_training"],
        "SCRIPT_METADATA_PATH": paths["script_metadata"],
        "SCRIPT_LIFECYCLE_PATH": paths["script_lifecycle"],
        "AUDIO_VALIDITY_PATH": paths["audio_validity"],
        "DESIGNED_VOICES_DIR": paths["designed_voices"],
        "CLONE_VOICES_DIR": paths["clone_voices"],
        "LORA_MODELS_DIR": paths["lora_models"],
        "LORA_DATASETS_DIR": paths["lora_datasets"],
        "BUILTIN_LORA_DIR": paths["builtin_lora"],
        "DATASET_BUILDER_DIR": paths["dataset_builder"],
        "PREPARER_OUTPUT_DIR": paths["preparer_output"],
        "STAGE_LOG_DIR": paths["stage_logs"],
        "STAGE_LOG_SPECS": stage_log_specs,
        "ROSTER_LOG_PATH": stage_log_specs["roster"][1],
        "EXTERNAL_WORKFLOW_UPLOAD_DIR": paths["external_uploads"],
        "VOICELINES_DIR": paths["voicelines"],
        "DESIGNED_VOICES_MANIFEST": os.path.join(
            paths["designed_voices"], "manifest.json"
        ),
        "CLONE_VOICES_MANIFEST": os.path.join(
            paths["clone_voices"], "manifest.json"
        ),
        "LORA_MODELS_MANIFEST": os.path.join(
            paths["lora_models"], "manifest.json"
        ),
        "project_manager": manager,
        "ACTIVE_PROJECT_ID": project_id,
        "ACTIVE_PROJECT_STORAGE_KIND": storage_kind,
    }


def _activate_runtime_project(
    *,
    root_dir: str | Path,
    project_id: str,
    storage_kind: str,
) -> dict:
    target = Path(root_dir).expanduser().resolve()
    if not target.is_dir() or target.is_symlink():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_activation_root_invalid",
                "message": "The selected project directory is unavailable or unsafe.",
                "context": {"project_id": project_id},
            },
        )

    with _RUNTIME_PROJECT_LOCK:
        _assert_runtime_project_switch_available()
        if Path(ROOT_DIR).resolve() == target and ACTIVE_PROJECT_ID == project_id:
            return {
                "state": "current",
                "project_id": project_id,
                "root_path": str(target),
                "native_destination": "script",
            }

        paths = _runtime_project_path_map(target)
        directories = (
            paths["scripts"],
            paths["roster_history"],
            paths["persona_refs"],
            paths["voice_training"],
            paths["designed_voices"],
            paths["clone_voices"],
            paths["lora_models"],
            paths["lora_datasets"],
            paths["builtin_lora"],
            paths["dataset_builder"],
            paths["preparer_output"],
            paths["stage_logs"],
            paths["external_uploads"],
            paths["voicelines"],
        )
        for directory in directories:
            Path(directory).mkdir(parents=True, exist_ok=True)

        candidate_manager = ProjectManager(target, config_path=CONFIG_PATH)
        candidate_globals = _runtime_project_candidate_globals(
            paths=paths,
            project_id=project_id,
            storage_kind=storage_kind,
            manager=candidate_manager,
        )
        snapshot = _capture_runtime_project_binding()
        old_manager = snapshot["globals"]["project_manager"]
        try:
            for name, value in candidate_globals.items():
                globals()[name] = value
            _set_static_directory(_voicelines_static, paths["voicelines"])
            _set_static_directory(_designed_voices_static, paths["designed_voices"])
            _set_static_directory(_clone_voices_static, paths["clone_voices"])
            _set_static_directory(_lora_models_static, paths["lora_models"])
            _set_static_directory(_builtin_lora_static, paths["builtin_lora"])
            _set_static_directory(_dataset_builder_static, paths["dataset_builder"])
            _reset_project_process_state()
            _clear_produce_aggregate_cache()
        except Exception:
            candidate_manager.engine = None
            _restore_runtime_project_binding(snapshot)
            raise

        if old_manager is not None and old_manager is not candidate_manager:
            old_manager.engine = None
        clear_controlled_clone_approvals()
        gc.collect()
        logger.info(
            "runtime_project_activated %s",
            json.dumps(
                {
                    "project_id": project_id,
                    "root_path": str(target),
                    "storage_kind": storage_kind,
                },
                sort_keys=True,
            ),
        )
        return {
            "state": "current",
            "project_id": project_id,
            "root_path": str(target),
            "native_destination": "script",
        }

def _persisted_stage_log_spec(
    task_name: str,
) -> tuple[str, str] | None:
    if task_name == "roster":
        return ("roster", ROSTER_LOG_PATH)
    return STAGE_LOG_SPECS.get(task_name)


def _persisted_stage_log_path(task_name: str) -> str | None:
    spec = _persisted_stage_log_spec(task_name)
    return spec[1] if spec else None


def _reset_process_logs(task_name: str) -> None:
    state = process_state[task_name]
    state["logs"] = []
    spec = _persisted_stage_log_spec(task_name)
    if not spec:
        return
    stage_name, log_path = spec
    try:
        reset_stage_log(log_path, stage=stage_name)
    except (OSError, StageLogError) as exc:
        logger.warning(
            "Could not reset persisted %s log: %s",
            task_name,
            exc,
        )


def _persist_process_log(
    task_name: str,
    message: str,
    *,
    level: str = "info",
) -> None:
    spec = _persisted_stage_log_spec(task_name)
    if not spec:
        return
    stage_name, log_path = spec
    safe_message = " ".join(str(message).splitlines()).strip()
    if len(safe_message) > 1200:
        safe_message = safe_message[:1197] + "..."
    if not safe_message:
        return
    try:
        append_stage_log(
            log_path,
            stage=stage_name,
            message=safe_message,
            level=level,
            max_entries=400,
        )
    except (OSError, StageLogError) as exc:
        logger.warning(
            "Could not persist %s log entry: %s",
            task_name,
            exc,
        )


def _append_process_log(
    task_name: str,
    message: str,
    *,
    level: str = "info",
    max_logs: int = 5000,
) -> None:
    state = process_state[task_name]
    state.setdefault("logs", []).append(message)
    if len(state["logs"]) > max_logs:
        state["logs"] = state["logs"][-max_logs:]
    _persist_process_log(task_name, message, level=level)


def _current_process_status(
    task_name: str,
    *,
    limit: int = 200,
) -> dict:
    state = dict(process_state.get(task_name, {}))
    state.pop("process", None)
    memory_logs = [str(line) for line in state.get("logs") or []]
    spec = _persisted_stage_log_spec(task_name)
    if not spec:
        state["logs"] = memory_logs[-limit:]
        state["log_source"] = "memory"
        state["log_line_count"] = len(memory_logs)
        state["log_truncated"] = len(memory_logs) > limit
        state["log_updated_at"] = None
        state["log_error"] = None
        return state

    stage_name, log_path = spec
    persisted = read_stage_log(
        log_path,
        stage=stage_name,
        limit=limit,
    )
    if persisted.get("exists") and not persisted.get("error"):
        state["logs"] = list(persisted.get("lines") or [])
        state["log_source"] = "persisted"
        state["log_line_count"] = persisted.get("line_count", 0)
        state["log_truncated"] = bool(persisted.get("truncated"))
        state["log_updated_at"] = persisted.get("updated_at")
        state["log_error"] = None
    else:
        state["logs"] = memory_logs[-limit:]
        state["log_source"] = "memory"
        state["log_line_count"] = len(memory_logs)
        state["log_truncated"] = len(memory_logs) > limit
        state["log_updated_at"] = None
        state["log_error"] = persisted.get("error")
    return state


def _managed_import_roster_available() -> bool:
    lifecycle = _current_script_lifecycle_status()
    artifact = lifecycle.get("artifact") or {}
    return bool(
        lifecycle.get("state") == "accepted"
        and lifecycle.get("accepted") is True
        and lifecycle.get("generation_method") == "import_existing_script"
        and artifact.get("script_exists") is True
        and os.path.exists(SCRIPT_PATH)
    )


def _replaceable_script_speaker_roster() -> bool:
    path = Path(CHARACTER_ROSTER_PATH)
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    discovery = value.get("discovery") if isinstance(value, dict) else None
    return bool(
        isinstance(discovery, dict)
        and discovery.get("model_name") == "script-speaker-catalog"
        and discovery.get("backend") == "local"
    )


def _bootstrap_imported_script_roster(
    *,
    replace_existing_script_speaker: bool = False,
) -> dict:
    if os.path.exists(CHARACTER_ROSTER_PATH) and not (
        replace_existing_script_speaker and _replaceable_script_speaker_roster()
    ):
        raise RuntimeError(
            "An approved character roster already exists and cannot be overwritten."
        )
    source = _current_character_roster_source_context()
    roster = build_script_speaker_roster(
        root_dir=ROOT_DIR,
        source_text=source["source_text"],
        current_source_fingerprint=source["source_fingerprint"],
        script_path=SCRIPT_PATH,
    )
    atomic_json_write(roster, CHARACTER_ROSTER_PATH)
    return roster


def _roster_discovery_command(
    *,
    source_path: str,
    passage_size: int,
    overlap_chars: int,
    replace_draft: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        "discover_character_roster.py",
        source_path,
        "--passage-size",
        str(passage_size),
        "--overlap-chars",
        str(overlap_chars),
        "--config-path",
        CONFIG_PATH,
        "--state-path",
        CHARACTER_ROSTER_STATE_PATH,
        "--draft-path",
        CHARACTER_ROSTER_DRAFT_PATH,
        "--approved-path",
        CHARACTER_ROSTER_PATH,
        "--metrics-path",
        os.path.join(STAGE_LOG_DIR, "roster_metrics.json"),
    ]
    if replace_draft:
        command.append("--replace-draft")
    return command


def _automatic_roster_after_script() -> int | None:
    """Run the post-Script roster stage under roster-only process state."""
    roster_state = process_state["roster"]
    source_path = _selected_script_input_path()

    try:
        if not source_path or not os.path.exists(source_path):
            _append_process_log(
                "roster",
                "Character roster was not generated because the selected source is unavailable.",
                level="error",
            )
            return 2

        roster_status = _current_character_roster_status()
        approved = roster_status.get("approved") or {}
        if approved.get("status") == "approved":
            _append_process_log(
                "roster",
                "Compatible approved character roster preserved; automatic discovery was skipped.",
            )
            return None
        if approved.get("exists"):
            _append_process_log(
                "roster",
                "Character roster generation is blocked because the existing approved roster belongs to another source or is invalid. Resolve it in Characters before continuing.",
                level="error",
            )
            return 3

        command = _roster_discovery_command(
            source_path=source_path,
            passage_size=12000,
            overlap_chars=1200,
            replace_draft=bool((roster_status.get("draft") or {}).get("exists")),
        )

        return_code = _stream_subprocess_to_logs(
            command,
            BASE_DIR,
            roster_state,
            log_sink=lambda line: _persist_process_log(
                "roster",
                line,
                level="progress",
            ),
        )
        if roster_state.get("cancel"):
            _append_process_log(
                "roster",
                "Character roster generation was cancelled.",
                level="warning",
            )
            return 130
        if return_code == 0:
            _append_process_log(
                "roster",
                "Character roster draft completed and is ready for review.",
            )
        else:
            _append_process_log(
                "roster",
                f"Character roster generation failed with return code {return_code}.",
                level="error",
            )
        return return_code
    except Exception as exc:
        logger.error("Automatic character roster failed: %s", exc)
        _append_process_log(
            "roster",
            f"Automatic character roster failed: {exc}",
            level="error",
        )
        return 1
    finally:
        roster_state["process"] = None
        roster_state["running"] = False
        roster_state["cancel"] = False


def _start_automatic_roster_after_script() -> bool:
    """Start roster discovery only after Script has reached terminal state."""
    roster_state = process_state["roster"]
    if roster_state.get("running"):
        logger.warning(
            "Automatic roster handoff skipped because roster discovery is already running."
        )
        return False

    roster_state["running"] = True
    roster_state["cancel"] = False
    roster_state["process"] = None
    _reset_process_logs("roster")
    _append_process_log(
        "roster",
        "Annotated script complete. Starting character roster discovery as a separate stage.",
    )
    worker = threading.Thread(
        target=_automatic_roster_after_script,
        name="alexandria-roster-after-script",
        daemon=True,
    )
    worker.start()
    return True


def _backend_render_plan_command() -> list[str]:
    return [
        sys.executable,
        "-u",
        "generate_backend_render_plan.py",
        "--root-dir",
        ROOT_DIR,
        "--config-path",
        CONFIG_PATH,
    ]


def _resume_roster_after_backend_render_plan() -> dict | None:
    lifecycle = _current_script_lifecycle_status()
    accepted_version_id = lifecycle.get("accepted_version_id")
    if not lifecycle.get("accepted") or not accepted_version_id:
        return None
    discovery = lifecycle.get("discovery_handoff") or {}
    if discovery.get("status") in {
        "running",
        "resumable",
        "complete",
        "not_required",
    }:
        return {
            "discovery_handoff": copy.deepcopy(discovery),
            "state_fingerprint": lifecycle.get("state_fingerprint"),
        }
    return _mark_accepted_script_handoff(
        accepted_version_id=accepted_version_id,
        expected_state_fingerprint=lifecycle.get("state_fingerprint"),
    )


def _with_backend_render_plan_follow_on(result: dict) -> dict:
    if result.get("task_type") != "backend_render_plan_generation":
        return result
    updated = copy.deepcopy(result)
    try:
        resumed = _resume_roster_after_backend_render_plan()
    except Exception as exc:
        updated["follow_on"] = {
            "status": "failed",
            "stage": "character_roster",
            "message": (
                "The delivery plan was applied, but character roster handoff "
                f"could not resume: {type(exc).__name__}: {exc}"
            ),
        }
        return updated
    updated["follow_on"] = {
        "status": "resumed" if resumed is not None else "not_required",
        "stage": "character_roster",
        "discovery_handoff": (
            copy.deepcopy(resumed.get("discovery_handoff"))
            if isinstance(resumed, dict)
            else None
        ),
    }
    return updated


def _run_backend_render_plan_process() -> int:
    state = process_state["render_plan"]
    state["started_at"] = _utc_now_text()
    state["finished_at"] = None
    state["last_error"] = None
    return_code = run_process(
        _backend_render_plan_command(),
        "render_plan",
    )
    state["finished_at"] = _utc_now_text()
    if return_code == 0 and not state.get("cancel"):
        try:
            resumed = _resume_roster_after_backend_render_plan()
            if resumed is not None:
                _append_process_log(
                    "render_plan",
                    "Delivery plan complete. Character roster handoff resumed.",
                )
        except Exception as exc:
            state["last_error"] = (
                "Delivery plan completed, but roster handoff could not resume: "
                f"{type(exc).__name__}: {exc}"
            )
            _append_process_log(
                "render_plan",
                state["last_error"],
                level="error",
            )
    elif return_code != 0 and not state.get("cancel"):
        state["last_error"] = (
            state.get("logs", [])[-1]
            if state.get("logs")
            else f"Delivery planning failed with return code {return_code}."
        )
    return return_code


def _start_backend_render_plan_thread() -> bool:
    state = process_state["render_plan"]
    if state.get("running"):
        return False
    chunks = project_manager.load_chunks()
    if not chunks:
        return False
    state["running"] = True
    state["cancel"] = False
    worker = threading.Thread(
        target=_run_backend_render_plan_process,
        name="alexandria-backend-render-plan",
        daemon=True,
    )
    worker.start()
    return True


def run_process(command: List[str], task_name: str) -> int:
    """Run one stage subprocess and stream output into that stage only."""
    state = process_state[task_name]
    state["running"] = True
    if "cancel" in state:
        state["cancel"] = False
    _reset_process_logs(task_name)
    logger.info(f"Starting task {task_name}: {' '.join(command)}")

    try:
        return_code = _stream_subprocess_to_logs(
            command,
            BASE_DIR,
            state,
            log_sink=(
                lambda line: _persist_process_log(
                    task_name,
                    line,
                    level="progress",
                )
                if _persisted_stage_log_path(task_name)
                else None
            ),
        )

        if state.get("cancel"):
            _append_process_log(
                task_name,
                f"Task {task_name} cancelled.",
                level="warning",
            )
        elif return_code == 0:
            _append_process_log(
                task_name,
                f"Task {task_name} completed successfully.",
            )
        else:
            _append_process_log(
                task_name,
                f"Task {task_name} failed with return code {return_code}.",
                level="error",
            )
        return return_code

    except Exception as e:
        logger.error(f"Error running {task_name}: {e}")
        _append_process_log(
            task_name,
            f"Error: {str(e)}",
            level="error",
        )
        return -1
    finally:
        state["process"] = None
        state["running"] = False



def _stream_subprocess_to_logs(
    command: List[str],
    cwd: str,
    state: dict,
    log_prefix: str = "",
    max_logs: int = 5000,
    log_sink=None,
) -> int:
    """Run a subprocess, appending its merged stdout/stderr into state['logs'].

    Uses a reader thread + Queue so the drain loop can check state['cancel']
    between reads without any platform-specific I/O multiplexing (e.g. no
    select.select(), which does not work on Windows pipes).

    Returns the process exit code.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        env=os.environ.copy(),
    )

    if "process" in state:
        state["process"] = process

    log_queue: queue.Queue = queue.Queue()

    def _reader(stream, q):
        try:
            for line in stream:
                q.put(line)
        finally:
            q.put(None)

    reader = threading.Thread(target=_reader, args=(process.stdout, log_queue), daemon=True)
    reader.start()

    while True:
        try:
            line = log_queue.get(timeout=0.05)
        except queue.Empty:
            if state.get("cancel"):
                process.terminate()
            continue
        if line is None:
            break
        log_line = line.strip()
        if log_line:
            entry = f"{log_prefix}{log_line}" if log_prefix else log_line
            state["logs"].append(entry)
            if len(state["logs"]) > max_logs:
                state["logs"].pop(0)
            if log_sink is not None:
                try:
                    log_sink(entry)
                except Exception as exc:
                    logger.warning(
                        "Could not persist subprocess log entry: %s",
                        exc,
                    )

    reader.join()
    process.wait()
    return process.returncode


_llm_runtime_activity = {
    "last_action": None,
    "last_action_success": None,
    "last_action_message": None,
    "last_action_at": None,
    "last_action_elapsed_seconds": None,
    "last_action_metrics": {},
}


def _configured_llm_runtime():
    config = {}

    if os.path.exists(CONFIG_PATH):
        try:
            with open(
                CONFIG_PATH,
                "r",
                encoding="utf-8",
            ) as config_file:
                loaded = json.load(config_file)

            if isinstance(loaded, dict):
                config = loaded
        except (
            OSError,
            json.JSONDecodeError,
            ValueError,
        ):
            config = {}

    llm_section = normalized_llm_section(
        config.get("llm")
    )

    return build_runtime_client(
        {
            "llm": llm_section,
        }
    )


def _duration_seconds(
    value,
):
    if not isinstance(value, (int, float)):
        return None

    return value / 1_000_000_000


def _llm_operation_metrics(
    result,
):
    if not isinstance(result, dict):
        return {}

    metrics = {}

    passthrough = (
        "done",
        "done_reason",
        "model",
        "prompt_eval_count",
        "eval_count",
    )

    for key in passthrough:
        if key in result:
            metrics[key] = result[key]

    duration_fields = (
        "total_duration",
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
    )

    for key in duration_fields:
        if key not in result:
            continue

        metrics[key] = result[key]
        metrics[f"{key}_seconds"] = (
            _duration_seconds(result[key])
        )

    return metrics


def _record_llm_runtime_action(
    *,
    action,
    success,
    message,
    elapsed_seconds,
    result,
):
    _llm_runtime_activity.update(
        {
            "last_action": action,
            "last_action_success": success,
            "last_action_message": message,
            "last_action_at": time.time(),
            "last_action_elapsed_seconds": (
                elapsed_seconds
            ),
            "last_action_metrics": (
                _llm_operation_metrics(result)
            ),
        }
    )


# Endpoints

@app.get("/")
async def read_index():
    return Response(
        content=_render_index_html(),
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )

@app.get("/favicon.ico")
async def read_favicon():
    favicon_path = os.path.join(ROOT_DIR, "icon.png")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Favicon not found")



def _model_cache_operation_payload() -> dict[str, Any]:
    with _MODEL_CACHE_OPERATION_LOCK:
        return copy.deepcopy(process_state["model_cache"])


def _run_model_cache_operation(
    model_keys: list[str],
    action: str,
) -> None:
    state = process_state["model_cache"]
    try:
        for index, model_key in enumerate(model_keys):
            with _MODEL_CACHE_OPERATION_LOCK:
                if state.get("cancel_requested"):
                    state["status"] = "cancelled"
                    state["logs"].append(
                        "Model cache operation cancelled before the next model."
                    )
                    break
            spec = model_spec(model_key)
            repair = action == "repair"
            if action == "download_required":
                before = model_registry_status()
                current = next(
                    item
                    for item in before["models"]
                    if item["model"]["key"] == model_key
                )
                repair = current["state"] == "incomplete"
            current_operation = "repair" if repair else "download"
            with _MODEL_CACHE_OPERATION_LOCK:
                state["current_model_key"] = model_key
                state["current_operation"] = current_operation
                state["status"] = "running"
                state["logs"].append(
                    f"[{index + 1}/{len(model_keys)}] {current_operation.title()} {spec.repo_id} at pinned revision {spec.revision}."
                )
            result = download_or_repair_model(
                model_key,
                repair=repair,
            )
            with _MODEL_CACHE_OPERATION_LOCK:
                state["results"].append(
                    {
                        "model_key": model_key,
                        "operation": result["operation"],
                        "state": result["state"],
                        "snapshot_path": result.get("snapshot_path"),
                        "size_bytes": result.get("size_bytes", 0),
                    }
                )
                state["completed_count"] = index + 1
                state["logs"].append(
                    f"{spec.repo_id}: {result['operation']} and validated."
                )
                if state.get("cancel_requested"):
                    state["status"] = "cancelled"
                    state["logs"].append(
                        "Cancellation took effect after the active snapshot operation completed safely."
                    )
                    break
        with _MODEL_CACHE_OPERATION_LOCK:
            if state["status"] != "cancelled":
                state["status"] = "complete"
                state["logs"].append(
                    "All requested pinned model snapshots passed Alexandria validation."
                )
    except Exception as exc:
        with _MODEL_CACHE_OPERATION_LOCK:
            state["status"] = "failed"
            state["error"] = str(exc)
            state["error_code"] = getattr(
                exc,
                "code",
                "model_cache_operation_failed",
            )
            state["logs"].append(
                f"Model cache operation failed: {type(exc).__name__}."
            )
    finally:
        with _MODEL_CACHE_OPERATION_LOCK:
            state["running"] = False
            state["current_model_key"] = None
            state["current_operation"] = None
            state["finished_at"] = _utc_now_text()


def _start_model_cache_operation(
    model_keys: list[str],
    action: str,
) -> dict[str, Any]:
    with _MODEL_CACHE_OPERATION_LOCK:
        state = process_state["model_cache"]
        if state["running"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "model_cache_operation_running",
                    "message": "A model download or repair is already running.",
                },
            )
        state.update(
            {
                "running": True,
                "logs": [],
                "status": "starting",
                "action": action,
                "model_keys": list(model_keys),
                "current_model_key": None,
                "current_operation": None,
                "completed_count": 0,
                "total_count": len(model_keys),
                "results": [],
                "error": None,
                "error_code": None,
                "cancel_requested": False,
                "started_at": _utc_now_text(),
                "finished_at": None,
            }
        )
    thread = threading.Thread(
        target=_run_model_cache_operation,
        args=(list(model_keys), action),
        daemon=True,
        name="alexandria-model-cache",
    )
    thread.start()
    return _model_cache_operation_payload()


def _loaded_model_registry_keys() -> list[str]:
    """Inspect already-loaded engine state without creating or loading a backend."""
    engine = getattr(project_manager, "engine", None)
    if engine is None:
        return []
    loaded: set[str] = set()
    mlx_backend = getattr(engine, "_mlx_backend", None)
    mlx_models = getattr(mlx_backend, "_models", {}) if mlx_backend is not None else {}
    if isinstance(mlx_models, dict):
        mapping = {
            "clone": "mlx_clone",
            "custom": "mlx_custom_voice",
            "design": "mlx_voice_design",
            "expressive_clone": "mlx_controlled_clone",
        }
        loaded.update(
            model_key
            for kind, model_key in mapping.items()
            if mlx_models.get(kind) is not None
        )
    pytorch_mapping = {
        "_local_custom_model": "pytorch_qwen_custom_voice",
        "_local_clone_model": "pytorch_qwen_base",
        "_local_design_model": "pytorch_qwen_voice_design",
    }
    loaded.update(
        model_key
        for attribute, model_key in pytorch_mapping.items()
        if getattr(engine, attribute, None) is not None
    )
    return sorted(loaded)


@app.get("/api/model_registry/status")
async def get_model_registry_status():
    try:
        status = model_registry_status(
            loaded_model_keys=_loaded_model_registry_keys(),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "model_registry_status_failed",
                "message": f"Could not inspect the model cache: {exc}",
            },
        ) from exc
    status["cache_dir"] = str(shared_huggingface_cache_dir())
    status["operation"] = _model_cache_operation_payload()
    return status


def _loaded_mlx_backend():
    engine = getattr(project_manager, "engine", None)
    return getattr(engine, "_mlx_backend", None) if engine is not None else None


@app.get("/api/model_registry/memory")
async def get_model_registry_memory():
    coordinator = ModelMemoryCoordinator(
        policy_path=default_model_memory_policy_path()
    )
    backend = _loaded_mlx_backend()
    active_jobs = (
        backend._memory.active_jobs
        if backend is not None and hasattr(backend, "_memory")
        else 0
    )
    return {
        "policy": coordinator.policy(),
        "memory": memory_snapshot(),
        "active_jobs": active_jobs,
        "loaded_model_keys": _loaded_model_registry_keys(),
    }


@app.put("/api/model_registry/memory/policy")
async def update_model_registry_memory_policy(
    request: ModelMemoryPolicyRequest,
):
    coordinator = ModelMemoryCoordinator(
        policy_path=default_model_memory_policy_path()
    )
    try:
        policy = coordinator.update_policy(
            {
                "schema_version": 1,
                **request.model_dump(),
            }
        )
    except ModelMemoryError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return {"status": "updated", "policy": policy}


@app.post("/api/model_registry/memory/release")
async def release_model_registry_memory():
    backend = _loaded_mlx_backend()
    if backend is None:
        return {
            "status": "released",
            "released": False,
            "reason": "no_loaded_mlx_backend",
            "active_jobs": 0,
        }
    try:
        result = backend.release_models_manually()
    except ModelMemoryError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": str(exc),
                "context": exc.details,
            },
        ) from exc
    return {"status": "released", **result}


@app.post("/api/model_registry/action/cancel")
async def cancel_model_registry_action():
    with _MODEL_CACHE_OPERATION_LOCK:
        state = process_state["model_cache"]
        if not state.get("running"):
            return {
                "status": state.get("status") or "idle",
                "cancel_requested": False,
                "message": "No model-cache operation is running.",
            }
        state["cancel_requested"] = True
        state["status"] = "cancelling"
        state["logs"].append(
            "Cancellation requested. The current snapshot operation will finish safely before stopping."
        )
        return {
            "status": "cancelling",
            "cancel_requested": True,
            "message": "Cancellation requested.",
        }


@app.post("/api/model_registry/action")
async def apply_model_registry_action(
    request: ModelRegistryActionRequest,
):
    try:
        if request.action == "download_required":
            status = model_registry_status()
            model_keys = [
                item["model"]["key"]
                for item in status["models"]
                if item["model"]["required_by_default"]
                and not item["cached"]
            ]
            if not model_keys:
                return {
                    "status": "already_cached",
                    "operation": _model_cache_operation_payload(),
                }
        else:
            if not request.model_key:
                raise ModelRegistryError(
                    "model_key is required for a single-model operation."
                )
            model_keys = [model_spec(request.model_key).key]
        operation = _start_model_cache_operation(
            model_keys,
            request.action,
        )
    except ModelRegistryError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_model_registry_action",
                "message": str(exc),
            },
        ) from exc
    return {
        "status": "started",
        "operation": operation,
    }


@app.get("/api/llm/status")
async def get_llm_status():
    try:
        runtime = _configured_llm_runtime()
        status = runtime.status()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to construct the configured "
                f"LLM runtime: {exc}"
            ),
        ) from exc

    status["lifecycle"] = dict(
        _llm_runtime_activity
    )
    status["telemetry"] = (
        read_llm_telemetry()
    )

    return status


@app.post("/api/llm/preload")
async def preload_llm():
    try:
        runtime = _configured_llm_runtime()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to construct the configured "
                f"LLM runtime: {exc}"
            ),
        ) from exc

    if runtime.native_root is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Model preload is available only for "
                "native Ollama runtimes."
            ),
        )

    started = time.perf_counter()
    success, message = runtime.preload()
    elapsed = time.perf_counter() - started

    _record_llm_runtime_action(
        action="preload",
        success=success,
        message=message,
        elapsed_seconds=elapsed,
        result=runtime.last_preload_result,
    )

    if not success:
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "action": "preload",
                "message": message,
                "lifecycle": dict(
                    _llm_runtime_activity
                ),
            },
        )

    return {
        "status": "preloaded",
        "action": "preload",
        "message": message,
        "lifecycle": dict(
            _llm_runtime_activity
        ),
        "runtime": runtime.status(),
    }


@app.post("/api/llm/unload")
async def unload_llm():
    try:
        runtime = _configured_llm_runtime()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to construct the configured "
                f"LLM runtime: {exc}"
            ),
        ) from exc

    if runtime.native_root is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Model unload is available only for "
                "native Ollama runtimes."
            ),
        )

    started = time.perf_counter()
    success, message = runtime.unload()
    elapsed = time.perf_counter() - started

    _record_llm_runtime_action(
        action="unload",
        success=success,
        message=message,
        elapsed_seconds=elapsed,
        result=runtime.last_unload_result,
    )

    if not success:
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "action": "unload",
                "message": message,
                "lifecycle": dict(
                    _llm_runtime_activity
                ),
            },
        )

    return {
        "status": "unloaded",
        "action": "unload",
        "message": message,
        "lifecycle": dict(
            _llm_runtime_activity
        ),
        "runtime": runtime.status(),
    }

def _raise_application_settings_http_error(exc: ApplicationSettingsError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_more_tools_http_error(exc: MoreToolsError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


@app.get("/api/more")
async def get_more_tools(
    project_id: Optional[str] = None,
    character_id: Optional[str] = None,
    source: Optional[str] = None,
    return_route: Optional[str] = "#/more",
):
    try:
        return inspect_more_tools(
            project_id=project_id or ACTIVE_PROJECT_ID,
            character_id=character_id,
            source=source,
            return_route=return_route,
        )
    except MoreToolsError as exc:
        _raise_more_tools_http_error(exc)


def _settings_with_template_default(payload: dict) -> dict:
    try:
        templates = list_project_templates(PROJECTS_DATA_ROOT)
    except ProjectTemplateError as exc:
        raise ApplicationSettingsError(
            "settings_template_catalog_unavailable",
            "The default project template could not be read.",
            status_code=409,
            context={"template_error": exc.as_detail()},
        ) from exc
    default_id = templates.get("default_template_id")
    selected = next(
        (
            item
            for item in templates.get("templates", [])
            if item.get("id") == default_id
        ),
        None,
    )
    result = copy.deepcopy(payload)
    result["generation_defaults"] = {
        "default_template": (
            {
                key: selected.get(key)
                for key in (
                    "id",
                    "name",
                    "intent",
                    "generation_method",
                    "preset",
                    "source_language",
                    "output_language",
                    "built_in",
                )
            }
            if selected
            else None
        ),
        "manage_route": {
            "destination": "templates",
            "context": {"return": "#/settings"},
        },
    }
    return result


@app.get("/api/settings")
async def get_settings():
    try:
        return _settings_with_template_default(
            get_application_settings(config_path=CONFIG_PATH)
        )
    except ApplicationSettingsError as exc:
        _raise_application_settings_http_error(exc)


@app.put("/api/settings")
async def put_settings(request: ApplicationSettingsUpdateRequest):
    try:
        payload = update_application_settings(
            config_path=CONFIG_PATH,
            expected_config_fingerprint=request.expected_config_fingerprint,
            settings=request.settings,
        )
        project_manager.engine = None
        return _settings_with_template_default(payload)
    except ApplicationSettingsError as exc:
        _raise_application_settings_http_error(exc)


@app.get("/api/config")
async def get_config():
    default_config = {
        "llm": normalized_llm_section({}),
        "tts": {
            "mode": "local",
            "url": "http://127.0.0.1:7860",
            "device": "auto"
        },
        "prompts": {
            "system_prompt": "",
            "user_prompt": ""
        }
    }

    if not os.path.exists(CONFIG_PATH):
        sys_prompt, usr_prompt = load_default_prompts()
        default_config["prompts"]["system_prompt"] = sys_prompt
        default_config["prompts"]["user_prompt"] = usr_prompt
        try:
            rev_sys, rev_usr = load_review_prompts()
            default_config["prompts"]["review_system_prompt"] = rev_sys
            default_config["prompts"]["review_user_prompt"] = rev_usr
        except RuntimeError:
            pass
        try:
            per_sys, per_usr, per_adv = load_persona_prompts()
            default_config["prompts"]["persona_system_prompt"] = per_sys
            default_config["prompts"]["persona_user_prompt"] = per_usr
            default_config["prompts"]["persona_advanced_prompt"] = per_adv
        except RuntimeError:
            pass
        config = default_config
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)

    if not isinstance(config, dict):
        config = {}

    config["llm"] = normalized_llm_section(
        config.get("llm")
    )

    # Ensure prompts section exists with defaults from file
    if "prompts" not in config:
        sys_prompt, usr_prompt = load_default_prompts()
        prompts = {"system_prompt": sys_prompt, "user_prompt": usr_prompt}
        try:
            rev_sys, rev_usr = load_review_prompts()
            prompts["review_system_prompt"] = rev_sys
            prompts["review_user_prompt"] = rev_usr
        except RuntimeError:
            pass
        try:
            per_sys, per_usr, per_adv = load_persona_prompts()
            prompts["persona_system_prompt"] = per_sys
            prompts["persona_user_prompt"] = per_usr
            prompts["persona_advanced_prompt"] = per_adv
        except RuntimeError:
            pass
        config["prompts"] = prompts
    else:
        if not config["prompts"].get("system_prompt") or not config["prompts"].get("user_prompt"):
            sys_prompt, usr_prompt = load_default_prompts()
            if not config["prompts"].get("system_prompt"):
                config["prompts"]["system_prompt"] = sys_prompt
            if not config["prompts"].get("user_prompt"):
                config["prompts"]["user_prompt"] = usr_prompt
        if not config["prompts"].get("review_system_prompt") or not config["prompts"].get("review_user_prompt"):
            try:
                rev_sys, rev_usr = load_review_prompts()
                if not config["prompts"].get("review_system_prompt"):
                    config["prompts"]["review_system_prompt"] = rev_sys
                if not config["prompts"].get("review_user_prompt"):
                    config["prompts"]["review_user_prompt"] = rev_usr
            except RuntimeError:
                pass  # review_prompts.txt missing or malformed — leave fields empty
        if not config["prompts"].get("persona_system_prompt") or not config["prompts"].get("persona_user_prompt") or not config["prompts"].get("persona_advanced_prompt"):
            try:
                per_sys, per_usr, per_adv = load_persona_prompts()
                if not config["prompts"].get("persona_system_prompt"):
                    config["prompts"]["persona_system_prompt"] = per_sys
                if not config["prompts"].get("persona_user_prompt"):
                    config["prompts"]["persona_user_prompt"] = per_usr
                if not config["prompts"].get("persona_advanced_prompt"):
                    config["prompts"]["persona_advanced_prompt"] = per_adv
            except RuntimeError:
                pass

    # Include current input file info if available
    state_path = os.path.join(ROOT_DIR, "state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as sf:
                state = json.load(sf)
            input_path = state.get("input_file_path", "")
            if input_path and os.path.exists(input_path):
                config["current_file"] = os.path.basename(input_path)
        except (json.JSONDecodeError, ValueError):
            pass

    return config

@app.get("/api/default_prompts")
async def get_default_prompts():
    system_prompt, user_prompt = load_default_prompts()
    result = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt
    }
    try:
        review_sys, review_usr = load_review_prompts()
        result["review_system_prompt"] = review_sys
        result["review_user_prompt"] = review_usr
    except RuntimeError:
        pass
    try:
        persona_sys, persona_usr, persona_adv = load_persona_prompts()
        result["persona_system_prompt"] = persona_sys
        result["persona_user_prompt"] = persona_usr
        result["persona_advanced_prompt"] = persona_adv
    except RuntimeError:
        pass
    return result

@app.post("/api/config")
async def save_config(config: AppConfig):
    existing = {}

    if os.path.exists(CONFIG_PATH):
        try:
            with open(
                CONFIG_PATH,
                "r",
                encoding="utf-8",
            ) as existing_file:
                loaded = json.load(existing_file)

            if isinstance(loaded, dict):
                existing = loaded
        except (
            OSError,
            json.JSONDecodeError,
            ValueError,
        ):
            existing = {}

    incoming = config.model_dump(
        exclude_unset=True
    )

    merged = _deep_merge_config(
        existing,
        incoming,
    )

    merged["llm"] = normalized_llm_section(
        merged.get("llm")
    )

    config_directory = os.path.dirname(CONFIG_PATH)
    if config_directory:
        os.makedirs(config_directory, exist_ok=True)

    temporary_path = f"{CONFIG_PATH}.tmp"

    try:
        with open(
            temporary_path,
            "w",
            encoding="utf-8",
        ) as config_file:
            json.dump(
                merged,
                config_file,
                indent=2,
                ensure_ascii=False,
            )
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())

        os.replace(
            temporary_path,
            CONFIG_PATH,
        )
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)

    # Reset engine so it picks up new TTS settings on next use
    project_manager.engine = None

    return {"status": "saved"}

class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags from EPUB content, preserving block-level structure."""
    BLOCK_TAGS = frozenset({
        'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'li', 'blockquote', 'br', 'hr', 'tr', 'section', 'article',
    })
    SKIP_TAGS = frozenset({'style', 'script'})

    def __init__(self):
        super().__init__()
        self.parts = []
        self._pending_newline = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._pending_newline = True

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        if self._pending_newline and self.parts:
            self.parts.append('\n')
            self._pending_newline = False
        self.parts.append(data)

    def get_text(self):
        return ''.join(self.parts)


def extract_epub_text(epub_path: str) -> str:
    """Extract plain text from an EPUB file, ordered by spine (reading order).

    Parses the EPUB ZIP structure directly using stdlib only:
    META-INF/container.xml -> .opf manifest+spine -> XHTML content files.
    """
    with zipfile.ZipFile(epub_path, 'r') as zf:
        # 1. Find the OPF file path from container.xml
        container_xml = zf.read('META-INF/container.xml')
        container = ET.fromstring(container_xml)
        ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        rootfile_el = container.find('.//c:rootfile', ns)
        if rootfile_el is None:
            raise ValueError("Invalid EPUB: no rootfile found in container.xml")
        opf_path = rootfile_el.get('full-path')

        # 2. Parse the OPF to get manifest (id->href) and spine (reading order)
        opf_xml = zf.read(opf_path)
        opf = ET.fromstring(opf_xml)
        # Detect OPF namespace (varies between EPUB 2 and 3)
        opf_ns = opf.tag.split('}')[0] + '}' if '}' in opf.tag else ''

        # Build manifest: id -> href (resolve relative to OPF directory)
        opf_dir = opf_path.rsplit('/', 1)[0] + '/' if '/' in opf_path else ''
        manifest = {}
        for item in opf.findall(f'.//{opf_ns}item'):
            item_id = item.get('id')
            href = item.get('href')
            media_type = item.get('media-type', '')
            if item_id and href and 'html' in media_type:
                manifest[item_id] = opf_dir + href

        # Get spine order
        spine_ids = []
        for itemref in opf.findall(f'.//{opf_ns}itemref'):
            idref = itemref.get('idref')
            if idref:
                spine_ids.append(idref)

        # 3. Extract text from each spine item in order
        chapters = []
        for item_id in spine_ids:
            href = manifest.get(item_id)
            if href is None:
                continue
            try:
                html_bytes = zf.read(href)
            except KeyError:
                continue
            html_content = html_bytes.decode('utf-8', errors='replace')
            extractor = _HTMLTextExtractor()
            extractor.feed(html_content)
            text = extractor.get_text().strip()
            if text:
                chapters.append(text)

    return '\n\n'.join(chapters)


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    file_path = os.path.join(UPLOADS_DIR, file.filename)
    async with aiofiles.open(file_path, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)

    # Convert EPUB to plain text
    if file.filename.lower().endswith('.epub'):
        try:
            text = extract_epub_text(file_path)
        except Exception as e:
            os.remove(file_path)
            raise HTTPException(status_code=400, detail=f"Failed to process EPUB: {e}")
        if not text.strip():
            os.remove(file_path)
            raise HTTPException(status_code=400, detail="No readable text content found in EPUB.")
        txt_path = file_path.rsplit('.', 1)[0] + '.txt'
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(text)
        file_path = txt_path

    # Save input path to state.json to be compatible with original scripts if needed
    state_path = os.path.join(ROOT_DIR, "state.json")
    state = {}
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            try:
                state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass

    state["input_file_path"] = file_path
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    return {"filename": file.filename, "path": file_path}

@app.post("/api/generate_script")
async def generate_script(
    background_tasks: BackgroundTasks,
):
    input_file = (
        _selected_script_input_path()
    )

    if not input_file:
        raise HTTPException(
            status_code=400,
            detail=(
                "No input file selected."
            ),
        )

    if process_state[
        "script"
    ].get("running"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Script generation is "
                "already running."
            ),
        )

    status = (
        _current_script_generation_status()
    )

    try:
        mode = choose_generation_action(
            status
        )
    except GenerationActionBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "checkpoint_status": (
                    exc.checkpoint_status
                ),
                "reason_codes": (
                    exc.reason_codes
                ),
            },
        ) from exc

    command = [
        sys.executable,
        "-u",
        "generate_script.py",
    ]

    if mode == "finalize":
        command.append(
            "--finalize-only"
        )

    command.append(
        input_file
    )

    background_tasks.add_task(
        run_process,
        command,
        "script",
    )

    checkpoint = status.get(
        "checkpoint",
        {},
    )

    return {
        "status": "started",
        "mode": mode,
        "completed_chunks": (
            checkpoint.get(
                "completed_chunks",
                0,
            )
        ),
        "total_chunks": (
            checkpoint.get(
                "total_chunks",
                0,
            )
        ),
        "next_chunk": (
            checkpoint.get(
                "next_chunk"
            )
        ),
    }

def _selected_script_input_path():
    state_path = os.path.join(
        ROOT_DIR,
        "state.json",
    )

    if not os.path.exists(state_path):
        return None

    try:
        with open(
            state_path,
            "r",
            encoding="utf-8",
        ) as handle:
            state = json.load(handle)
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None

    value = state.get("input_file_path")

    if not isinstance(value, str):
        return None

    value = value.strip()
    return value or None


def _current_character_roster_source():
    managed_import = _managed_script_import_candidate(pending_only=False)
    if managed_import.get("status") == "ready":
        source_text = "\n".join(
            entry["text"]
            if entry["speaker"].strip().upper() == "NARRATOR"
            else f'"{entry["text"]}"'
            for entry in managed_import["entries"]
        )
        try:
            snapshot, _ = build_source_snapshot(
                managed_import["_path"],
                normalizer=fix_mojibake,
            )
        except Exception as exc:
            return None, None, str(exc)
        snapshot["fingerprint"] = fingerprint_text(source_text)
        snapshot["character_count"] = len(source_text)
        return snapshot, source_text, None

    selected_input = _selected_script_input_path()

    if not selected_input:
        return (
            None,
            None,
            "No source file is currently selected.",
        )

    try:
        snapshot, source_text = build_source_snapshot(
            selected_input,
            normalizer=fix_mojibake,
        )
    except Exception as exc:
        return None, None, str(exc)

    return snapshot, source_text, None


def _current_character_roster_source_context() -> dict:
    snapshot, source_text, source_error = _current_character_roster_source()
    if snapshot is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_unavailable",
                "message": source_error or "A readable selected source is required.",
            },
        )
    return {
        "source": snapshot,
        "source_text": source_text,
        "source_fingerprint": snapshot["fingerprint"],
    }


def _current_roster_process_status() -> dict:
    return _current_process_status("roster")


def _has_reviewed_character_roster_replacement_draft() -> bool:
    if not (
        os.path.exists(CHARACTER_ROSTER_DRAFT_PATH)
        and os.path.exists(CHARACTER_ROSTER_PATH)
    ):
        return False
    try:
        draft = read_character_roster(
            CHARACTER_ROSTER_DRAFT_PATH,
            expected_status="draft",
        )
        approved = read_character_roster(
            CHARACTER_ROSTER_PATH,
            expected_status="approved",
        )
    except CharacterRosterError:
        return False
    return (
        draft["draft_fingerprint"]
        != approved["approved_draft_fingerprint"]
    )


def _current_character_roster_status():
    source, source_text, source_error = (
        _current_character_roster_source()
    )

    status = build_character_roster_status(
        draft_path=CHARACTER_ROSTER_DRAFT_PATH,
        approved_path=CHARACTER_ROSTER_PATH,
        current_source=source,
        current_source_text=source_text,
        current_source_error=source_error,
    )
    working_draft = status["draft"]["status"] == "draft"
    if working_draft and status["approved"]["status"] == "approved":
        try:
            approved = read_character_roster(
                CHARACTER_ROSTER_PATH,
                source_text=source_text,
                expected_status="approved",
            )
            working_draft = (
                status["draft"]["fingerprint"]
                != approved["approved_draft_fingerprint"]
            )
        except CharacterRosterError:
            working_draft = False
    status["working_draft"] = working_draft
    status["process"] = _current_roster_process_status()
    status["progress"] = inspect_roster_discovery_state(
        CHARACTER_ROSTER_STATE_PATH,
        current_source=source,
    )
    revisions = list_character_roster_revisions(
        CHARACTER_ROSTER_HISTORY_DIR
    )
    current_approved_fingerprint = (
        status.get("approved", {}).get("fingerprint")
        if status.get("approved", {}).get("status") == "approved"
        else None
    )
    latest_available = next(
        (
            revision
            for revision in revisions
            if revision.get("status") == "available"
            and revision.get("replacement_roster_fingerprint")
            == current_approved_fingerprint
        ),
        None,
    )
    status["revision_history"] = {
        "count": len(revisions),
        "latest_available": (
            {
                "revision_id": latest_available.get("revision_id"),
                "created_at_utc": latest_available.get("created_at_utc"),
                "previous_roster_fingerprint": latest_available.get(
                    "previous_roster_fingerprint"
                ),
                "replacement_roster_fingerprint": latest_available.get(
                    "replacement_roster_fingerprint"
                ),
            }
            if latest_available is not None
            else None
        ),
    }
    return status


def _current_script_generation_status():
    script_state = _current_process_status("script")
    selected_input = (
        _selected_script_input_path()
    )
    current_snapshot = None
    current_error = None

    if os.path.exists(
        GENERATION_STATE_PATH
    ):
        if selected_input:
            try:
                current_snapshot = (
                    build_script_generation_snapshot(
                        selected_input
                    )
                )
            except Exception as exc:
                current_error = str(exc)
        else:
            current_error = (
                "No source file is currently "
                "selected."
            )

    return build_generation_status(
        checkpoint_path=(
            GENERATION_STATE_PATH
        ),
        script_path=SCRIPT_PATH,
        metadata_path=SCRIPT_METADATA_PATH,
        current_snapshot=current_snapshot,
        current_error=current_error,
        process_running=bool(
            script_state.get("running")
        ),
        process_logs=script_state.get(
            "logs",
            [],
        ),
    )


def _recovery_process_state(task_name: str) -> dict:
    return _current_process_status(task_name)


def _recovery_file_timestamp(path: str) -> str | None:
    try:
        modified = os.path.getmtime(path)
    except OSError:
        return None
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(modified),
    )


def _selected_source_recovery_status() -> dict:
    state_path = os.path.join(ROOT_DIR, "state.json")
    status = {
        "state_file_exists": os.path.exists(state_path),
        "persisted": False,
        "path": None,
        "basename": None,
        "exists": False,
        "readable": False,
        "error": None,
    }
    if not status["state_file_exists"]:
        return status
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        status["error"] = f"Could not read the saved source selection: {exc}"
        return status
    if not isinstance(state, dict):
        status["error"] = "The saved source selection is not a JSON object."
        return status
    selected = state.get("input_file_path")
    if not isinstance(selected, str) or not selected.strip():
        return status
    selected = selected.strip()
    status.update(
        {
            "persisted": True,
            "path": selected,
            "basename": os.path.basename(selected),
            "exists": os.path.isfile(selected),
        }
    )
    if not status["exists"]:
        status["error"] = "The saved source book no longer exists."
        return status
    try:
        with open(selected, "rb") as handle:
            handle.read(1)
        status["readable"] = True
    except OSError as exc:
        status["error"] = f"The saved source book cannot be read: {exc}"
    return status


def _current_persona_recovery_inputs() -> dict:
    process = _recovery_process_state("persona")
    result = {
        "process": process,
        "script_available": False,
        "configured_speakers": 0,
        "total_speakers": 0,
        "error": None,
    }
    if not os.path.exists(SCRIPT_PATH):
        return result
    try:
        with open(SCRIPT_PATH, "r", encoding="utf-8") as handle:
            script = json.load(handle)
        if not isinstance(script, list):
            raise ValueError("annotated_script.json must contain a JSON array")
        speakers = {
            str(entry.get("speaker") or entry.get("type") or "").strip()
            for entry in script
            if isinstance(entry, dict)
        }
        speakers.discard("")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["error"] = f"Could not inspect the annotated script: {exc}"
        return result
    result["script_available"] = True
    result["total_speakers"] = len(speakers)
    voice_config = {}
    if os.path.exists(VOICE_CONFIG_PATH):
        try:
            with open(VOICE_CONFIG_PATH, "r", encoding="utf-8") as handle:
                voice_config = json.load(handle)
            if not isinstance(voice_config, dict):
                raise ValueError("voice_config.json must contain a JSON object")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            result["error"] = f"Could not inspect the voice configuration: {exc}"
            return result
    result["configured_speakers"] = sum(
        1
        for speaker in speakers
        if isinstance(voice_config.get(speaker), dict)
    )
    return result


def _current_dataset_recovery_inputs() -> tuple[dict, str | None]:
    projects = []
    errors = []
    newest_state_path = None
    newest_mtime = -1.0
    if os.path.isdir(DATASET_BUILDER_DIR):
        for name in sorted(os.listdir(DATASET_BUILDER_DIR)):
            state_path = os.path.join(DATASET_BUILDER_DIR, name, "state.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, "r", encoding="utf-8") as handle:
                    state = json.load(handle)
                if not isinstance(state, dict):
                    raise ValueError("state must be a JSON object")
                samples = state.get("samples", [])
                if not isinstance(samples, list):
                    raise ValueError("samples must be a JSON array")
                modified = os.path.getmtime(state_path)
                projects.append(
                    {
                        "name": name,
                        "sample_count": len(samples),
                        "done_count": sum(
                            1
                            for sample in samples
                            if isinstance(sample, dict)
                            and sample.get("status") == "done"
                        ),
                        "modified_at": _recovery_file_timestamp(state_path),
                        "modified_epoch": modified,
                    }
                )
                if modified > newest_mtime:
                    newest_mtime = modified
                    newest_state_path = state_path
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{name}: {exc}")
    projects.sort(
        key=lambda item: (
            -float(item.get("modified_epoch") or 0),
            str(item.get("name") or ""),
        )
    )
    selected_project = projects[0]["name"] if projects else None
    for item in projects:
        item.pop("modified_epoch", None)
    return (
        {
            "projects": projects,
            "selected_project": selected_project,
            "process": _recovery_process_state("dataset_builder"),
            "error": (
                "Invalid Dataset builder state: " + "; ".join(errors)
                if errors
                else None
            ),
        },
        newest_state_path,
    )


def _current_audio_recovery_inputs() -> dict:
    result = {
        "chunks": [],
        "process": _recovery_process_state("audio"),
        "error": None,
    }
    if not os.path.exists(CHUNKS_PATH):
        return result
    try:
        with open(CHUNKS_PATH, "r", encoding="utf-8") as handle:
            chunks = json.load(handle)
        if not isinstance(chunks, list):
            raise ValueError("chunks.json must contain a JSON array")
        if any(not isinstance(chunk, dict) for chunk in chunks):
            raise ValueError("every chunk must be a JSON object")
        result["chunks"] = chunks
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["error"] = f"Could not inspect audio chunks: {exc}"
    return result


def _current_recovery_status() -> dict:
    source = _selected_source_recovery_status()
    dataset, dataset_state_path = _current_dataset_recovery_inputs()
    try:
        training_status = get_training_sidecar_status_payload(
            root_dir=ROOT_DIR,
        )
    except TrainingSidecarApiError as exc:
        training_status = {
            "environment_exists": False,
            "experimental": True,
            "jobs": [],
            "error": exc.detail,
        }
    return build_recovery_summary(
        source=source,
        script_status=_current_script_generation_status(),
        roster_status=_current_character_roster_status(),
        visual_status=_current_character_visual_status(),
        persona=_current_persona_recovery_inputs(),
        dataset=dataset,
        audio=_current_audio_recovery_inputs(),
        training_status=training_status,
        training_process=_recovery_process_state("lora_training"),
        timestamps={
            "script": _recovery_file_timestamp(GENERATION_STATE_PATH),
            "roster": _recovery_file_timestamp(CHARACTER_ROSTER_STATE_PATH),
            "visual": _recovery_file_timestamp(PERSONA_VISUAL_STATE_PATH),
            "persona": _recovery_file_timestamp(VOICE_CONFIG_PATH),
            "dataset_builder": (
                _recovery_file_timestamp(dataset_state_path)
                if dataset_state_path
                else None
            ),
            "audio": _recovery_file_timestamp(CHUNKS_PATH),
        },
    )


def _managed_script_import_candidate(*, pending_only: bool = True) -> dict:
    root = Path(ROOT_DIR).expanduser().resolve()
    if pending_only and Path(SCRIPT_PATH).is_file():
        return {"status": "none"}
    manifest_path = root / "alexandria-project.json"
    if not manifest_path.is_file():
        return {"status": "none"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "code": "managed_script_import_manifest_invalid",
            "message": f"The managed project manifest could not be read: {exc}",
        }
    if not isinstance(manifest, dict):
        return {"status": "none"}
    generation = manifest.get("generation")
    source = manifest.get("source")
    if not isinstance(generation, dict) or not isinstance(source, dict):
        return {"status": "none"}
    if generation.get("method") != "import_existing_script":
        return {"status": "none"}
    relative = str(source.get("import_candidate_relative_path") or "").strip()
    if not relative:
        return {
            "status": "invalid",
            "code": "managed_script_import_candidate_missing",
            "message": "The managed project does not identify its imported Script candidate.",
        }
    candidate_path = (root / relative).resolve()
    if not candidate_path.is_relative_to(root) or not candidate_path.is_file():
        return {
            "status": "invalid",
            "code": "managed_script_import_candidate_unavailable",
            "message": "The stored imported Script candidate is unavailable.",
        }
    try:
        entries = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "code": "managed_script_import_candidate_invalid",
            "message": f"The stored imported Script candidate could not be read: {exc}",
        }
    if not isinstance(entries, list) or not entries:
        return {
            "status": "invalid",
            "code": "managed_script_import_candidate_invalid",
            "message": "The stored imported Script candidate must contain at least one entry.",
        }
    normalized: list[dict[str, str]] = []
    for index, value in enumerate(entries):
        if not isinstance(value, dict) or not all(
            isinstance(value.get(field), str)
            for field in ("speaker", "text", "instruct")
        ):
            return {
                "status": "invalid",
                "code": "managed_script_import_candidate_invalid",
                "message": (
                    f"Imported Script entry {index} must contain string speaker, "
                    "text, and instruct fields."
                ),
            }
        if not value["speaker"].strip() or not value["text"].strip():
            return {
                "status": "invalid",
                "code": "managed_script_import_candidate_invalid",
                "message": f"Imported Script entry {index} has an empty speaker or text field.",
            }
        normalized.append({
            "speaker": value["speaker"],
            "text": value["text"],
            "instruct": value["instruct"],
        })
    return {
        "status": "ready",
        "filename": candidate_path.name,
        "entry_count": len(normalized),
        "speaker_count": len({entry["speaker"] for entry in normalized}),
        "speakers": sorted({entry["speaker"] for entry in normalized}),
        "fingerprint": fingerprint_value(normalized),
        "entries": normalized,
        "_path": str(candidate_path),
    }


def _current_script_lifecycle_status() -> dict:
    source_status = _selected_source_recovery_status()
    source_fingerprint = None
    source_available = bool(
        source_status.get("persisted")
        and source_status.get("exists")
        and source_status.get("readable")
    )
    if source_available:
        try:
            source_fingerprint = _current_character_roster_source_context()[
                "source_fingerprint"
            ]
        except HTTPException:
            source_available = False
    candidate_count = 0
    try:
        candidate_count = len(
            list_annotated_script_candidates(root_dir=ROOT_DIR)
        )
    except ExternalWorkflowError:
        candidate_count = 0
    managed_candidate = _managed_script_import_candidate()
    if managed_candidate.get("status") in {"ready", "invalid"}:
        candidate_count += 1
    return inspect_script_lifecycle(
        root_dir=ROOT_DIR,
        script_path=SCRIPT_PATH,
        metadata_path=SCRIPT_METADATA_PATH,
        lifecycle_path=SCRIPT_LIFECYCLE_PATH,
        generation_status=_current_script_generation_status(),
        source_fingerprint=source_fingerprint,
        source_available=source_available,
        import_candidate_count=candidate_count,
    )


def _current_project_flow_status() -> dict:
    source_status = _selected_source_recovery_status()
    generation_status = _current_script_generation_status()
    roster_status = _current_character_roster_status()
    lifecycle_status = _current_script_lifecycle_status()
    try:
        cast_aggregate_status = inspect_cast_project(root_dir=ROOT_DIR)
    except CastAggregateError as exc:
        cast_aggregate_status = {
            "schema_version": 1,
            "summary": {
                "state": "failed",
                "character_count": 0,
                "required_speaking_count": 0,
                "ready_required_count": 0,
                "blocker_count": 1,
                "complete": False,
            },
            "characters": [],
            "blockers": [
                {
                    "code": exc.code,
                    "title": "Cast aggregate is unavailable",
                    "explanation": exc.detail,
                    "native_destination": "cast",
                    "target_id": "cast:status",
                    "blocking": True,
                }
            ],
            "fingerprints": {},
            "compatibility": {
                "state": "invalid",
                "warnings": [],
                "roster_source": "invalid",
            },
        }
    try:
        produce_aggregate_status = _current_produce_status(
            cast=cast_aggregate_status,
        )
    except ProduceAggregateError as exc:
        produce_aggregate_status = {
            "schema_version": 1,
            "state": "failed",
            "summary": {
                "required_chunk_count": 0,
                "current_count": 0,
                "failed_count": 1,
                "complete": False,
            },
            "chunks": [],
            "process": _current_process_status("audio"),
            "fingerprints": {},
            "error": exc.as_detail(),
        }
    try:
        config_value = _external_read_json(CONFIG_PATH)
        if not isinstance(config_value, dict):
            config_value = {}
        export_aggregate_status = inspect_export_project(
            root_dir=ROOT_DIR,
            produce=produce_aggregate_status,
            process=_current_process_status("export"),
            config=config_value,
        )
    except ExportAggregateError as exc:
        export_aggregate_status = {
            "schema_version": 1,
            "state": "failed",
            "metadata": {},
            "formats": ["mp3"],
            "chapters": [],
            "outputs": {},
            "selected_outputs": [],
            "blockers": [
                {
                    "code": exc.code,
                    "title": "Export aggregate is unavailable",
                    "explanation": exc.detail,
                    "native_destination": "export",
                    "target_id": "export:status",
                    "blocking": True,
                }
            ],
            "process": _current_process_status("export"),
            "fingerprints": {},
        }
    source_fingerprint = (
        roster_status.get("source", {}).get("fingerprint")
        if isinstance(roster_status.get("source"), dict)
        else None
    )
    if source_fingerprint:
        source_status = {
            **source_status,
            "fingerprint": source_fingerprint,
        }
    migration_status = None
    migration_error = None
    if ACTIVE_PROJECT_STORAGE_KIND == "managed":
        # Managed projects are created in the current schema. Running the
        # legacy-checkout migration inspector against an empty managed project
        # falsely marks it incompatible before Script generation can begin.
        migration_status = {
            "migration_required": False,
            "migration_blocked": False,
            "plan_fingerprint": None,
            "actions": [],
            "blockers": [],
        }
    else:
        try:
            migration_status = get_migration_status_payload(
                root_dir=MIGRATION_ROOT_DIR,
                config_path=CONFIG_PATH,
            )
        except MigrationApiError as exc:
            migration_error = exc.detail
    return inspect_project_flow(
        root_dir=ROOT_DIR,
        config_path=CONFIG_PATH,
        script_path=SCRIPT_PATH,
        script_metadata_path=SCRIPT_METADATA_PATH,
        chunks_path=CHUNKS_PATH,
        voice_config_path=VOICE_CONFIG_PATH,
        roster_path=CHARACTER_ROSTER_PATH,
        state_path=os.path.join(ROOT_DIR, "state.json"),
        audiobook_path=AUDIOBOOK_PATH,
        m4b_path=M4B_PATH,
        source_status=source_status,
        generation_status=generation_status,
        script_lifecycle_status=lifecycle_status,
        cast_aggregate_status=cast_aggregate_status,
        produce_aggregate_status=produce_aggregate_status,
        export_aggregate_status=export_aggregate_status,
        roster_status=roster_status,
        audio_process=_current_process_status("audio"),
        export_process=_current_process_status("export"),
        migration_status=migration_status,
        migration_error=migration_error,
    )


@app.get("/api/script_lifecycle/status")
async def get_script_lifecycle_status():
    try:
        return _current_script_lifecycle_status()
    except ScriptLifecycleError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc


def _mark_accepted_script_handoff(
    *,
    accepted_version_id: str,
    expected_state_fingerprint: str,
) -> dict:
    roster_status = _current_character_roster_status()
    process = roster_status.get("process", {})
    approved = roster_status.get("approved", {})
    progress = roster_status.get("progress", {})
    if (
        approved.get("status") == "approved"
        and approved.get("compatible_source") is not False
    ):
        return mark_discovery_handoff(
            lifecycle_path=SCRIPT_LIFECYCLE_PATH,
            accepted_version_id=accepted_version_id,
            status="not_required",
            expected_state_fingerprint=expected_state_fingerprint,
        )
    if process.get("running"):
        return mark_discovery_handoff(
            lifecycle_path=SCRIPT_LIFECYCLE_PATH,
            accepted_version_id=accepted_version_id,
            status="running",
            expected_state_fingerprint=expected_state_fingerprint,
        )
    if _managed_import_roster_available():
        try:
            clear_roster_discovery_state(CHARACTER_ROSTER_STATE_PATH)
            roster = _bootstrap_imported_script_roster()
            _append_process_log(
                "roster",
                (
                    f"Created {len(roster.get('entries') or [])} Cast identities "
                    "from the accepted imported Script without LLM discovery."
                ),
            )
            return mark_discovery_handoff(
                lifecycle_path=SCRIPT_LIFECYCLE_PATH,
                accepted_version_id=accepted_version_id,
                status="complete",
                expected_state_fingerprint=expected_state_fingerprint,
            )
        except Exception as exc:
            return mark_discovery_handoff(
                lifecycle_path=SCRIPT_LIFECYCLE_PATH,
                accepted_version_id=accepted_version_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                expected_state_fingerprint=expected_state_fingerprint,
            )
    if progress.get("status") == "resumable":
        return mark_discovery_handoff(
            lifecycle_path=SCRIPT_LIFECYCLE_PATH,
            accepted_version_id=accepted_version_id,
            status="resumable",
            expected_state_fingerprint=expected_state_fingerprint,
        )
    try:
        started = _start_automatic_roster_after_script()
    except Exception as exc:
        return mark_discovery_handoff(
            lifecycle_path=SCRIPT_LIFECYCLE_PATH,
            accepted_version_id=accepted_version_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            expected_state_fingerprint=expected_state_fingerprint,
        )
    return mark_discovery_handoff(
        lifecycle_path=SCRIPT_LIFECYCLE_PATH,
        accepted_version_id=accepted_version_id,
        status="running" if started else "pending",
        expected_state_fingerprint=expected_state_fingerprint,
    )


def _accept_current_script_request(
    request: ScriptLifecycleAcceptRequest,
    *,
    origin: Optional[dict] = None,
) -> dict:
    lifecycle_status = _current_script_lifecycle_status()
    if lifecycle_status.get("state") in {"running", "resumable"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "script_acceptance_generation_active",
                "message": "Finish or discard the current generation checkpoint before accepting the Script.",
            },
        )
    source = _current_character_roster_source_context()
    try:
        accepted = accept_current_script(
            root_dir=ROOT_DIR,
            script_path=SCRIPT_PATH,
            metadata_path=SCRIPT_METADATA_PATH,
            lifecycle_path=SCRIPT_LIFECYCLE_PATH,
            source_text=source["source_text"],
            source_fingerprint=source["source_fingerprint"],
            expected_script_fingerprint=request.expected_script_fingerprint,
            expected_metadata_fingerprint=request.expected_metadata_fingerprint,
            expected_source_fingerprint=request.expected_source_fingerprint,
            expected_state_fingerprint=request.expected_state_fingerprint,
            allow_reviewed_source_differences=request.allow_reviewed_source_differences,
            expected_audit_fingerprint=request.expected_audit_fingerprint,
            origin=origin,
        )
        plan_status = inspect_backend_render_plan(ROOT_DIR)
        generation_method = (
            (accepted.get("version") or {}).get("generation_method")
        )
        defer_roster = (
            generation_method == "local"
            and not plan_status.get("current")
        )
        if defer_roster:
            handoff = mark_discovery_handoff(
                lifecycle_path=SCRIPT_LIFECYCLE_PATH,
                accepted_version_id=accepted["version"]["version_id"],
                status="pending",
                expected_state_fingerprint=accepted["state_fingerprint"],
            )
        else:
            handoff = _mark_accepted_script_handoff(
                accepted_version_id=accepted["version"]["version_id"],
                expected_state_fingerprint=accepted["state_fingerprint"],
            )
    except ScriptLifecycleError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc
    delivery_handoff = {
        "status": "current" if plan_status.get("current") else "pending",
        "generation_method": generation_method,
        "local_started": False,
        "plan_fingerprint": plan_status.get("plan_fingerprint"),
    }
    if defer_roster:
        project_manager.load_chunks()
        started = _start_backend_render_plan_thread()
        delivery_handoff.update(
            {
                "status": "running" if started else "pending",
                "local_started": started,
            }
        )
        if not started:
            handoff = _mark_accepted_script_handoff(
                accepted_version_id=accepted["version"]["version_id"],
                expected_state_fingerprint=handoff["state_fingerprint"],
            )
    return {
        **accepted,
        "discovery_handoff": handoff["discovery_handoff"],
        "delivery_plan_handoff": delivery_handoff,
        "state_fingerprint": handoff["state_fingerprint"],
    }


@app.get("/api/script_lifecycle/import-candidate")
async def get_managed_script_import_candidate():
    candidate = _managed_script_import_candidate()
    return {
        key: copy.deepcopy(value)
        for key, value in candidate.items()
        if not key.startswith("_")
    }


@app.post("/api/script_lifecycle/import-candidate/apply")
async def apply_managed_script_import_candidate(
    request: ManagedScriptImportApplyRequest,
):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "managed_script_import_busy",
                "message": (
                    f"Stop the active {busy_stage} process before applying "
                    "the imported Script."
                ),
                "stage": busy_stage,
            },
        )
    managed = _managed_script_import_candidate()
    if managed.get("status") != "ready":
        raise HTTPException(
            status_code=409,
            detail={
                "code": managed.get("code") or "managed_script_import_candidate_unavailable",
                "message": managed.get("message") or "No managed imported Script is ready to apply.",
            },
        )
    if managed["fingerprint"] != request.expected_candidate_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "managed_script_import_candidate_changed",
                "message": "The imported Script candidate changed after it was reviewed.",
                "context": {"current_candidate_fingerprint": managed["fingerprint"]},
            },
        )
    source_context, source_text, source_error = _external_source_context()
    if source_context is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_source_required",
                "message": source_error or "The selected source is required to apply the imported Script.",
            },
        )
    script_state = _external_script_state()
    try:
        inspected = inspect_annotated_script_upload(
            root_dir=ROOT_DIR,
            import_path=managed["_path"],
            source_text=source_text,
            source_context=source_context,
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            generated_audio_count=script_state["generated_audio_count"],
        )
        applied = apply_annotated_script_candidate(
            root_dir=ROOT_DIR,
            candidate_id=inspected["candidate_id"],
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            checkpoint_decision=(
                "keep"
                if inspected.get("consequences", {}).get("checkpoint_decision_required")
                else None
            ),
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc
    return {
        **applied,
        "managed_candidate_fingerprint": managed["fingerprint"],
    }


@app.post("/api/script_lifecycle/accept")
async def accept_script_lifecycle(request: ScriptLifecycleAcceptRequest):
    return _accept_current_script_request(request)


@app.post("/api/script_lifecycle/reject")
async def reject_script_lifecycle(request: ScriptLifecycleRejectRequest):
    status = _current_script_lifecycle_status()
    if status.get("fingerprints", {}).get("script") != request.expected_script_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_script_review",
                "message": "The Script changed before rejection.",
                "context": {
                    "current_script_fingerprint": status.get("fingerprints", {}).get("script")
                },
            },
        )
    try:
        return reject_current_script(
            lifecycle_path=SCRIPT_LIFECYCLE_PATH,
            current_script_fingerprint=request.expected_script_fingerprint,
            reason=request.reason,
            expected_state_fingerprint=request.expected_state_fingerprint,
        )
    except ScriptLifecycleError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc


@app.post("/api/script_lifecycle/discovery-handoff")
async def retry_script_discovery_handoff(
    request: ScriptLifecycleHandoffRequest,
):
    status = _current_script_lifecycle_status()
    version_id = status.get("accepted_version_id")
    if not status.get("accepted") or not version_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "script_not_accepted",
                "message": "Accept the current Script before starting character discovery.",
            },
        )
    try:
        return _mark_accepted_script_handoff(
            accepted_version_id=version_id,
            expected_state_fingerprint=(
                request.expected_state_fingerprint
                or status["state_fingerprint"]
            ),
        )
    except ScriptLifecycleError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc


@app.get("/api/script_lifecycle/versions")
async def get_script_lifecycle_versions():
    status = _current_script_lifecycle_status()
    return {
        "schema_version": status["schema_version"],
        "accepted_version_id": status["accepted_version_id"],
        "versions": status["versions"],
        "state_fingerprint": status["state_fingerprint"],
    }


@app.post("/api/script_lifecycle/versions/{version_id}/rollback")
async def rollback_script_lifecycle_version(
    version_id: str,
    request: ScriptLifecycleRollbackRequest,
):
    source = _current_character_roster_source_context()
    if source["source_fingerprint"] != request.expected_source_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_script_source",
                "message": "The selected source changed before rollback.",
                "context": {
                    "current_source_fingerprint": source["source_fingerprint"]
                },
            },
        )
    try:
        result = rollback_script_version(
            root_dir=ROOT_DIR,
            script_path=SCRIPT_PATH,
            metadata_path=SCRIPT_METADATA_PATH,
            chunks_path=CHUNKS_PATH,
            audio_validity_path=AUDIO_VALIDITY_PATH,
            lifecycle_path=SCRIPT_LIFECYCLE_PATH,
            version_id=version_id,
            current_source_fingerprint=source["source_fingerprint"],
            expected_current_script_fingerprint=request.expected_current_script_fingerprint,
            expected_state_fingerprint=request.expected_state_fingerprint,
        )
        handoff = _mark_accepted_script_handoff(
            accepted_version_id=version_id,
            expected_state_fingerprint=result["state_fingerprint"],
        )
    except ScriptLifecycleError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc
    return {
        **result,
        "discovery_handoff": handoff["discovery_handoff"],
        "state_fingerprint": handoff["state_fingerprint"],
    }


@app.post("/api/script_lifecycle/candidates/{candidate_id}/accept")
async def accept_script_candidate_lifecycle(
    candidate_id: str,
    request: ScriptCandidateAcceptRequest,
):
    try:
        applied = apply_annotated_script_candidate(
            root_dir=ROOT_DIR,
            candidate_id=candidate_id,
            expected_current_script_fingerprint=(
                request.expected_current_script_fingerprint
            ),
            expected_current_metadata_fingerprint=(
                request.expected_current_metadata_fingerprint
            ),
            expected_current_voice_config_fingerprint=(
                request.expected_current_voice_config_fingerprint
            ),
            expected_current_chunks_fingerprint=(
                request.expected_current_chunks_fingerprint
            ),
            checkpoint_decision=request.checkpoint_decision,
        )
    except ExternalWorkflowError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc
    try:
        return _accept_current_script_request(
            ScriptLifecycleAcceptRequest(
                expected_script_fingerprint=applied["script_fingerprint"],
                expected_metadata_fingerprint=applied["metadata_fingerprint"],
                expected_source_fingerprint=request.expected_source_fingerprint,
                expected_state_fingerprint=(
                    request.expected_lifecycle_state_fingerprint
                ),
            ),
            origin={
                "candidate_id": candidate_id,
                "operation_id": applied["operation_id"],
                "workflow": "annotated_script_candidate",
            },
        )
    except HTTPException as acceptance_error:
        try:
            rollback_annotated_script_import(
                root_dir=ROOT_DIR,
                operation_id=applied["operation_id"],
                expected_current_script_fingerprint=applied[
                    "script_fingerprint"
                ],
                expected_current_metadata_fingerprint=applied[
                    "metadata_fingerprint"
                ],
                expected_current_voice_config_fingerprint=applied[
                    "voice_config_fingerprint"
                ],
                expected_current_chunks_fingerprint=applied[
                    "chunks_fingerprint"
                ],
            )
        except ExternalWorkflowError as rollback_error:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "script_candidate_acceptance_rollback_failed",
                    "message": "Script acceptance failed after candidate application, and exact rollback also failed.",
                    "context": {
                        "acceptance_error": acceptance_error.detail,
                        "rollback_error": rollback_error.as_detail(),
                        "operation_id": applied["operation_id"],
                    },
                },
            ) from rollback_error
        raise acceptance_error


@app.get("/api/project_flow/status")
def get_project_flow_status():
    try:
        return _read_runtime_project(_current_project_flow_status)
    except ProjectFlowError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_flow_invalid",
                "message": str(exc),
            },
        ) from exc


def _raise_cast_aggregate_http_error(exc: CastAggregateError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _current_cast_aggregate(
    *,
    selected_character_id: Optional[str] = None,
    filter_key: str = "all",
    search: Optional[str] = None,
) -> dict:
    aggregate = inspect_cast_project(
        root_dir=ROOT_DIR,
        selected_character_id=selected_character_id,
        filter_key=filter_key,
        search=search,
    )
    active_root = Path(ROOT_DIR).expanduser().resolve()
    roster_root = Path(CHARACTER_ROSTER_PATH).expanduser().resolve().parent
    roster_status = (
        _current_character_roster_status()
        if roster_root == active_root
        else {}
    )
    process = copy.deepcopy(roster_status.get("process") or {})
    progress = copy.deepcopy(roster_status.get("progress") or {})
    aggregate["process"] = process
    aggregate["progress"] = progress
    summary = aggregate.get("summary") or {}
    if not (aggregate.get("characters") or []):
        if process.get("running") is True:
            summary["state"] = "running"
        elif any(
            "failed" in str(line).casefold() or "error" in str(line).casefold()
            for line in process.get("logs") or []
        ):
            summary["state"] = "failed"
        elif progress.get("status") == "resumable":
            summary["state"] = "resumable"
    aggregate["summary"] = summary
    return aggregate


@app.get("/api/cast")
def get_cast_aggregate(
    selected_character_id: Optional[str] = None,
    filter: str = "all",
    search: Optional[str] = None,
):
    try:
        return _read_runtime_project(
            _current_cast_aggregate,
            selected_character_id=selected_character_id,
            filter_key=filter,
            search=search,
        )
    except CastAggregateError as exc:
        _raise_cast_aggregate_http_error(exc)


@app.get("/api/cast/characters/{character_id}")
async def get_cast_character(character_id: str):
    try:
        aggregate = _current_cast_aggregate(
            selected_character_id=character_id,
        )
    except CastAggregateError as exc:
        _raise_cast_aggregate_http_error(exc)
    character = aggregate.get("selected_character")
    if not isinstance(character, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "cast_character_not_found",
                "message": "The requested Cast character was not found.",
                "context": {"character_id": character_id},
            },
        )
    return character


def _raise_produce_aggregate_http_error(exc: ProduceAggregateError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_audio_take_http_error(exc: AudioTakeError) -> None:
    not_found_codes = {
        "audio_take_chunk_missing",
        "audio_take_missing",
        "audio_take_operation_missing",
    }
    validation_codes = {
        "audio_take_identifier_invalid",
        "audio_take_registry_invalid",
        "audio_take_record_invalid",
        "audio_take_cleanup_invalid",
    }
    raise HTTPException(
        status_code=(
            404
            if exc.code in not_found_codes
            else 422
            if exc.code in validation_codes
            else 409
        ),
        detail={
            "code": exc.code,
            "message": str(exc),
            "context": copy.deepcopy(exc.context),
        },
    ) from exc


def _clear_produce_aggregate_cache() -> None:
    with _PRODUCE_AGGREGATE_CACHE_LOCK:
        _PRODUCE_AGGREGATE_CACHE["signature"] = None
        _PRODUCE_AGGREGATE_CACHE["aggregate"] = None


def _require_audio_take_mutation_idle() -> None:
    active = [
        item
        for item in list_audio_generation_requests(ROOT_DIR)
        if item.get("state") in AUDIO_REQUEST_ACTIVE_STATES
    ]
    if active or process_state["audio"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "audio_take_generation_active",
                "message": (
                    "Finish or cancel production-audio generation before changing "
                    "Take selection, retention, or cleanup."
                ),
                "context": {
                    "active_request_ids": [
                        str(item.get("request_id"))
                        for item in active[:20]
                    ]
                },
            },
        )


def _produce_chunk_index(chunk_id: str) -> int:
    stable_id = chunk_id if chunk_id.startswith("chunk:") else f"chunk:{chunk_id}"
    try:
        aggregate = _current_produce_status(selected_chunk_id=stable_id)
    except ProduceAggregateError as exc:
        _raise_produce_aggregate_http_error(exc)
    selected = aggregate.get("selected_chunk")
    if not isinstance(selected, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "produce_chunk_not_found",
                "message": "The requested Produce chunk was not found.",
                "context": {"chunk_id": stable_id},
            },
        )
    return int(selected["index"])


def _current_produce_status(
    *,
    selected_chunk_id: Optional[str] = None,
    filter_key: str = "all",
    search: Optional[str] = None,
    cast: Optional[dict] = None,
) -> dict:
    process = _current_process_status("audio")
    cacheable = (
        selected_chunk_id is None
        and filter_key == "all"
        and not search
    )
    signature = _produce_input_signature(process) if cacheable else None
    if signature is not None:
        with _PRODUCE_AGGREGATE_CACHE_LOCK:
            cached_signature = _PRODUCE_AGGREGATE_CACHE.get("signature")
            cached_aggregate = _PRODUCE_AGGREGATE_CACHE.get("aggregate")
            if cached_signature == signature and isinstance(cached_aggregate, dict):
                return cached_aggregate
    aggregate = inspect_produce_project(
        root_dir=ROOT_DIR,
        config_path=CONFIG_PATH,
        selected_chunk_id=selected_chunk_id,
        filter_key=filter_key,
        search=search,
        process=process,
        cast=cast,
    )
    if signature is not None:
        with _PRODUCE_AGGREGATE_CACHE_LOCK:
            _PRODUCE_AGGREGATE_CACHE["signature"] = signature
            _PRODUCE_AGGREGATE_CACHE["aggregate"] = aggregate
    return aggregate


def _current_produce_plan(request: ProducePlanRequest) -> dict:
    aggregate = _current_produce_status()
    return build_produce_generation_plan(
        aggregate,
        mode=request.mode,
        selected_chunk_ids=request.selected_chunk_ids,
    )


@app.get("/api/produce")
def get_produce_aggregate(
    selected_chunk_id: Optional[str] = None,
    filter: str = "all",
    search: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1, le=500),
):
    try:
        aggregate = _read_runtime_project(
            _current_produce_status,
            selected_chunk_id=selected_chunk_id,
            filter_key=filter,
            search=search,
        )
        if limit is None:
            return _json_payload_response(aggregate)
        chunks = list(aggregate.get("chunks") or [])
        filtered_count = len(chunks)
        page = chunks[offset : offset + limit]
        return _json_payload_response(
            {
                **aggregate,
                "chunks": page,
                "returned_chunk_count": len(page),
                "page": {
                    "offset": offset,
                    "limit": limit,
                    "filtered_chunk_count": filtered_count,
                    "has_more": offset + len(page) < filtered_count,
                    "next_offset": (
                        offset + len(page)
                        if offset + len(page) < filtered_count
                        else None
                    ),
                },
            }
        )
    except ProduceAggregateError as exc:
        _raise_produce_aggregate_http_error(exc)


@app.get("/api/produce/chunks/{chunk_id}")
async def get_produce_chunk(chunk_id: str):
    stable_id = chunk_id if chunk_id.startswith("chunk:") else f"chunk:{chunk_id}"
    try:
        aggregate = _current_produce_status(selected_chunk_id=stable_id)
    except ProduceAggregateError as exc:
        _raise_produce_aggregate_http_error(exc)
    selected = aggregate.get("selected_chunk")
    if not isinstance(selected, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "produce_chunk_not_found",
                "message": "The requested Produce chunk was not found.",
                "context": {"chunk_id": stable_id},
            },
        )
    return selected


@app.get("/api/produce/chunks/{chunk_id}/takes")
async def get_produce_chunk_takes(chunk_id: str):
    try:
        return project_manager.audio_take_status(_produce_chunk_index(chunk_id))
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "audio_take_conflict", "message": str(exc)},
        ) from exc


@app.post("/api/produce/chunks/{chunk_id}/takes/use")
async def use_produce_audio_take(
    chunk_id: str,
    request: AudioTakeSelectionRequest,
):
    _require_audio_take_mutation_idle()
    try:
        result = project_manager.promote_audio_take(
            _produce_chunk_index(chunk_id),
            take_id=request.take_id,
            expected_registry_fingerprint=request.registry_fingerprint,
            expected_record_fingerprint=request.record_fingerprint,
        )
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "audio_take_promotion_conflict", "message": str(exc)},
        ) from exc
    _clear_produce_aggregate_cache()
    return {
        **result,
        "produce": _current_produce_status(
            selected_chunk_id=(
                chunk_id if chunk_id.startswith("chunk:") else f"chunk:{chunk_id}"
            )
        ),
    }


@app.post("/api/produce/chunks/{chunk_id}/takes/keep")
async def keep_produce_audio_take(
    chunk_id: str,
    request: AudioTakeKeepRequest,
):
    _require_audio_take_mutation_idle()
    try:
        result = project_manager.set_audio_take_kept(
            _produce_chunk_index(chunk_id),
            take_id=request.take_id,
            kept=request.kept,
            expected_registry_fingerprint=request.registry_fingerprint,
            expected_record_fingerprint=request.record_fingerprint,
        )
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "audio_take_keep_conflict", "message": str(exc)},
        ) from exc
    _clear_produce_aggregate_cache()
    return result


@app.get("/api/produce/chunks/{chunk_id}/takes/{take_id}/delete-impact")
async def get_produce_audio_take_delete_impact(
    chunk_id: str,
    take_id: str,
):
    try:
        return project_manager.audio_take_delete_impact(
            _produce_chunk_index(chunk_id),
            take_id=take_id,
        )
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "audio_take_delete_conflict", "message": str(exc)},
        ) from exc


@app.delete("/api/produce/chunks/{chunk_id}/takes/{take_id}")
async def delete_produce_audio_take(
    chunk_id: str,
    take_id: str,
    request: AudioTakeDeleteRequest,
):
    _require_audio_take_mutation_idle()
    if request.take_id != take_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "audio_take_delete_id_mismatch",
                "message": "The reviewed Take does not match the requested deletion.",
            },
        )
    try:
        result = project_manager.delete_audio_take(
            _produce_chunk_index(chunk_id),
            take_id=take_id,
            expected_impact_fingerprint=request.impact_fingerprint,
        )
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "audio_take_delete_conflict", "message": str(exc)},
        ) from exc
    _clear_produce_aggregate_cache()
    return result


@app.post("/api/produce/takes/cleanup-impact")
async def get_produce_audio_take_cleanup_impact(
    request: AudioTakeCleanupRequest,
):
    try:
        return project_manager.audio_take_cleanup_impact(
            older_than_days=request.older_than_days,
            reclaim_at_least_bytes=request.reclaim_at_least_bytes,
        )
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)


@app.post("/api/produce/takes/cleanup")
async def cleanup_produce_audio_takes(
    request: AudioTakeCleanupApplyRequest,
):
    _require_audio_take_mutation_idle()
    try:
        result = project_manager.cleanup_audio_takes(
            older_than_days=request.older_than_days,
            reclaim_at_least_bytes=request.reclaim_at_least_bytes,
            expected_impact_fingerprint=request.impact_fingerprint,
        )
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)
    _clear_produce_aggregate_cache()
    return result


@app.post("/api/produce/takes/undo")
async def undo_produce_audio_take_operation(
    request: AudioTakeUndoRequest,
):
    _require_audio_take_mutation_idle()
    try:
        result = project_manager.undo_audio_take_operation(
            operation_id=request.operation_id,
            expected_registry_fingerprint=request.registry_fingerprint,
        )
    except AudioTakeError as exc:
        _raise_audio_take_http_error(exc)
    _clear_produce_aggregate_cache()
    return result


@app.post("/api/produce/plan")
async def get_produce_generation_plan(request: ProducePlanRequest):
    try:
        return _current_produce_plan(request)
    except ProduceAggregateError as exc:
        _raise_produce_aggregate_http_error(exc)


@app.post("/api/produce/invalidate-selected")
async def invalidate_selected_produce_audio(request: ProduceInvalidateRequest):
    if process_state["audio"]["running"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_generation_already_running",
                "message": "Cancel or finish generation before invalidating audio.",
            },
        )
    aggregate = _current_produce_status()
    current_fingerprint = aggregate.get("fingerprints", {}).get("chunks")
    if current_fingerprint != request.chunks_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_chunks_changed",
                "message": "Produce chunks changed before invalidation.",
                "context": {"current_chunks_fingerprint": current_fingerprint},
            },
        )
    by_id = {
        str(item.get("chunk_id")): item
        for item in aggregate.get("chunks", [])
        if isinstance(item, dict)
    }
    selected_ids = sorted({str(value) for value in request.selected_chunk_ids})
    unknown = [value for value in selected_ids if value not in by_id]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "produce_chunk_not_found",
                "message": "One or more selected chunks no longer exist.",
                "context": {"chunk_ids": unknown[:20]},
            },
        )
    operation_id = "audio_repair_" + fingerprint_value(
        {
            "chunk_ids": selected_ids,
            "chunks_fingerprint": current_fingerprint,
            "reason": request.reason,
        }
    )[:24]
    try:
        changed = project_manager.invalidate_chunk_audio(
            [int(by_id[value]["index"]) for value in selected_ids],
            operation_id=operation_id,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_invalidation_conflict",
                "message": str(exc),
            },
        ) from exc
    return {
        "status": "invalidated",
        "operation_id": operation_id,
        "invalidated_count": len(changed),
        "chunk_ids": [f"chunk:{index}" for index in changed],
        "produce": _current_produce_status(),
    }


@app.post("/api/produce/rebind-selected")
async def rebind_selected_produce_audio(request: ProduceInvalidateRequest):
    if process_state["audio"]["running"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_generation_already_running",
                "message": "Cancel or finish generation before rebinding audio.",
            },
        )
    aggregate = _current_produce_status()
    current_fingerprint = aggregate.get("fingerprints", {}).get("chunks")
    if current_fingerprint != request.chunks_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_chunks_changed",
                "message": "Produce chunks changed before rebinding.",
                "context": {"current_chunks_fingerprint": current_fingerprint},
            },
        )
    by_id = {
        str(item.get("chunk_id")): item
        for item in aggregate.get("chunks", [])
        if isinstance(item, dict)
    }
    selected_ids = sorted({str(value) for value in request.selected_chunk_ids})
    unknown = [value for value in selected_ids if value not in by_id]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "produce_chunk_not_found",
                "message": "One or more selected chunks no longer exist.",
                "context": {"chunk_ids": unknown[:20]},
            },
        )
    operation_id = "audio_rebind_" + fingerprint_value(
        {
            "chunk_ids": selected_ids,
            "chunks_fingerprint": current_fingerprint,
            "reason": request.reason,
        }
    )[:24]
    try:
        changed = project_manager.rebind_chunk_audio(
            [int(by_id[value]["index"]) for value in selected_ids],
            operation_id=operation_id,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_rebind_conflict",
                "message": str(exc),
            },
        ) from exc
    return {
        "status": "rebound",
        "operation_id": operation_id,
        "rebound_count": len(changed),
        "chunk_ids": [f"chunk:{index}" for index in changed],
        "produce": _current_produce_status(),
    }


async def _execute_produce_plan(
    request: ProduceExecuteRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    try:
        plan = _current_produce_plan(request)
    except ProduceAggregateError as exc:
        _raise_produce_aggregate_http_error(exc)
    if plan["chunks_fingerprint"] != request.chunks_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_chunks_changed",
                "message": "Produce chunks changed after this plan was reviewed.",
                "context": {
                    "current_chunks_fingerprint": plan["chunks_fingerprint"]
                },
            },
        )
    if plan["plan_fingerprint"] != request.plan_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_plan_stale",
                "message": "The Produce generation plan changed before execution.",
                "context": {
                    "current_plan_fingerprint": plan["plan_fingerprint"]
                },
            },
        )
    if request.mode == "regenerate_all" and not request.confirm_regenerate_all:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "produce_regenerate_all_confirmation_required",
                "message": "Explicitly confirm destructive regeneration of all audio.",
            },
        )
    if not plan["safe_to_execute"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "produce_plan_blocked",
                "message": plan.get("empty_reason")
                or "The Produce generation plan is blocked.",
                "context": {"blockers": plan["blockers"]},
            },
        )
    result = await generate_batch_endpoint(
        BatchGenerateRequest(
            indices=plan["indices"],
            replace_active=request.replace_active,
            operation_mode=request.mode,
            plan_fingerprint=plan["plan_fingerprint"],
            chunks_fingerprint=plan["chunks_fingerprint"],
        ),
        background_tasks,
        http_request,
    )
    return {
        "status": "accepted",
        "plan": plan,
        "generation": result,
    }


@app.post("/api/produce/generate")
async def execute_produce_generation(
    request: ProduceExecuteRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    return await _execute_produce_plan(
        request,
        background_tasks,
        http_request,
    )


@app.post("/api/produce/retry-failed")
async def retry_failed_produce_generation(
    request: ProduceExecuteRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    if request.mode != "retry_failed":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "produce_retry_mode_required",
                "message": "Retry failed audio requires mode retry_failed.",
            },
        )
    return await _execute_produce_plan(
        request,
        background_tasks,
        http_request,
    )


@app.post("/api/produce/cancel")
async def cancel_produce_generation():
    result = await cancel_audio()
    return {
        "status": result.get("status"),
        "result": result,
        "process": _current_process_status("audio"),
    }


def _raise_export_aggregate_http_error(exc: ExportAggregateError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _export_request_metadata(request: ExportPlanRequest) -> dict:
    metadata = request.metadata
    return (
        metadata.model_dump()
        if hasattr(metadata, "model_dump")
        else metadata.dict()
    )


def _current_export_plan(
    request: ExportPlanRequest,
    *,
    produce: Optional[dict] = None,
) -> dict:
    produce_value = produce or _current_produce_status()
    config = _external_read_json(CONFIG_PATH)
    if not isinstance(config, dict):
        config = {}
    cover = resolve_export_cover(ROOT_DIR)
    cover_hash = cover.sha256 if cover else None
    return build_export_plan(
        produce=produce_value,
        metadata=_export_request_metadata(request),
        formats=request.formats,
        chapter_mode=request.chapter_mode,
        config=config,
        cover_sha256=cover_hash,
    )


def _current_export_status() -> dict:
    produce = _current_produce_status()
    config = _external_read_json(CONFIG_PATH)
    if not isinstance(config, dict):
        config = {}
    return inspect_export_project(
        root_dir=ROOT_DIR,
        produce=produce,
        process=_current_process_status("export"),
        config=config,
    )


@app.get("/api/export")
def get_export_aggregate():
    try:
        return _read_runtime_project(_current_export_status)
    except ExportAggregateError as exc:
        _raise_export_aggregate_http_error(exc)


@app.post("/api/export/plan")
async def get_export_plan(request: ExportPlanRequest):
    try:
        return _current_export_plan(request)
    except ExportAggregateError as exc:
        _raise_export_aggregate_http_error(exc)


@app.post("/api/export/build")
async def execute_export_plan(
    request: ExportBuildRequest,
    background_tasks: BackgroundTasks,
):
    state = process_state["export"]
    if state.get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "export_build_running",
                "message": "An Export build is already running.",
            },
        )
    try:
        produce = _current_produce_status()
        plan = _current_export_plan(request, produce=produce)
    except ExportAggregateError as exc:
        _raise_export_aggregate_http_error(exc)
    if plan["dependency_fingerprint"] != request.dependency_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "export_dependencies_changed",
                "message": "Export dependencies changed after this plan was reviewed.",
                "context": {
                    "current_dependency_fingerprint": plan[
                        "dependency_fingerprint"
                    ]
                },
            },
        )
    if plan["plan_fingerprint"] != request.plan_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "export_plan_stale",
                "message": "The Export plan changed before execution.",
                "context": {
                    "current_plan_fingerprint": plan["plan_fingerprint"]
                },
            },
        )
    if not plan["safe_to_execute"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "export_plan_blocked",
                "message": "Resolve Export blockers before building outputs.",
                "context": {"blockers": plan["blockers"]},
            },
        )

    operation_id = f"export_request_{secrets.token_hex(12)}"
    state.update(
        {
            "running": True,
            "logs": [
                "Starting Export build for "
                + ", ".join(plan["formats"])
                + "."
            ],
            "cancel": False,
            "cancel_requested": False,
            "operation_id": operation_id,
            "plan_fingerprint": plan["plan_fingerprint"],
            "dependency_fingerprint": plan["dependency_fingerprint"],
            "formats": list(plan["formats"]),
            "phase": "preparing_export",
            "phase_label": "Preparing Export",
            "completed_count": 0,
            "total_count": 0,
            "overall_percent": 1,
            "progress_message": "Preparing the protected Export transaction.",
            "started_at": _utc_now_text(),
            "finished_at": None,
            "last_error": None,
            "result": None,
        }
    )

    def task():
        last_phase = state.get("phase")

        def update_progress(event):
            nonlocal last_phase
            value = dict(event or {})
            phase = str(value.get("phase") or state.get("phase") or "running")
            if phase != last_phase:
                label = str(value.get("phase_label") or phase.replace("_", " ").title())
                state.setdefault("logs", []).append(label + ".")
                last_phase = phase
            state["phase"] = phase
            state["phase_label"] = str(
                value.get("phase_label") or state.get("phase_label") or phase
            )
            state["completed_count"] = int(value.get("completed_count") or 0)
            state["total_count"] = int(value.get("total_count") or 0)
            percent = value.get("overall_percent")
            state["overall_percent"] = (
                max(0, min(100, round(float(percent), 1)))
                if percent is not None
                else None
            )
            state["progress_message"] = str(
                value.get("progress_message")
                or value.get("message")
                or state.get("phase_label")
            )

        try:
            result = execute_export_build(
                root_dir=ROOT_DIR,
                project_manager=project_manager,
                plan=plan,
                cancel_check=lambda: bool(state.get("cancel")),
                progress_callback=update_progress,
            )
            state["result"] = result
            if result.get("status") == "cancelled":
                state.update(
                    {
                        "phase": "cancelled",
                        "phase_label": "Export cancelled",
                        "progress_message": "No output was committed.",
                    }
                )
                state["logs"].append("Export build cancelled before commit.")
            else:
                state.update(
                    {
                        "phase": "complete",
                        "phase_label": "Audiobook ready",
                        "completed_count": 1,
                        "total_count": 1,
                        "overall_percent": 100,
                        "progress_message": "The verified audiobook is ready.",
                    }
                )
                state["logs"].append(
                    f"Export build complete: {result.get('build_id')}"
                )
        except ExportAggregateError as exc:
            state["last_error"] = exc.detail
            state["phase"] = "failed"
            state["phase_label"] = "Export failed"
            state["progress_message"] = exc.detail
            state["logs"].append(f"Export build failed: {exc.detail}")
        except Exception as exc:
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            state["phase"] = "failed"
            state["phase_label"] = "Export failed"
            state["progress_message"] = state["last_error"]
            state["logs"].append(
                f"Export build failed: {type(exc).__name__}: {exc}"
            )
        finally:
            state["running"] = False
            state["cancel"] = False
            state["cancel_requested"] = False
            state["finished_at"] = _utc_now_text()

    background_tasks.add_task(task)
    return {
        "status": "started",
        "operation_id": operation_id,
        "plan": plan,
    }


@app.post("/api/export/cancel")
async def cancel_export_build():
    state = process_state["export"]
    if not state.get("running"):
        return {
            "status": "idle",
            "process": _current_process_status("export"),
        }
    state["cancel"] = True
    state["cancel_requested"] = True
    state["phase_label"] = "Cancelling Export"
    state["progress_message"] = "Stopping at the next safe boundary."
    state["logs"].append("Export cancellation requested.")
    return {
        "status": "cancelling",
        "process": _current_process_status("export"),
    }


def _raise_library_inventory_http_error(exc: LibraryInventoryError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _current_library_inventory(
    *,
    kind: Optional[str] = None,
    state: Optional[str] = None,
    search: Optional[str] = None,
    project_id: Optional[str] = None,
    character_id: Optional[str] = None,
    return_route: Optional[str] = "#/library",
) -> dict:
    return inspect_library_inventory(
        root_dir=ROOT_DIR,
        kind=kind,
        state=state,
        search=search,
        project_id=project_id,
        character_id=character_id,
        return_route=return_route,
    )


def _library_context(request: LibraryContextRequest) -> dict:
    return {
        "project_id": request.project_id,
        "character_id": request.character_id,
        "return_route": request.return_route,
    }


def _raise_help_center_http_error(exc: HelpCenterError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


@app.get("/api/help")
async def get_help_center(search: Optional[str] = None):
    try:
        return inspect_help_center(
            help_dir=HELP_CENTER_DIR,
            search=search,
        )
    except HelpCenterError as exc:
        _raise_help_center_http_error(exc)


@app.get("/api/help/context/{context_id}")
async def get_help_center_context_topic(context_id: str):
    try:
        return get_help_topic_by_context(
            help_dir=HELP_CENTER_DIR,
            context_id=context_id,
        )
    except HelpCenterError as exc:
        _raise_help_center_http_error(exc)


@app.get("/api/help/{slug}")
async def get_help_center_topic(slug: str):
    try:
        return get_help_topic(
            help_dir=HELP_CENTER_DIR,
            slug=slug,
        )
    except HelpCenterError as exc:
        _raise_help_center_http_error(exc)


def _raise_voice_library_http_error(exc: VoiceLibraryError):
    raise HTTPException(
        status_code=409,
        detail=exc.as_detail(),
    ) from exc


def _raise_community_qwen_pack_http_error(exc: CommunityQwenPackError):
    raise HTTPException(
        status_code=409,
        detail=exc.as_detail(),
    ) from exc


async def _store_qvoice_upload(file: UploadFile, directory: str | Path) -> Path:
    original_name = str(file.filename or "").strip()
    basename = Path(original_name).name
    if (
        not basename
        or basename != original_name
        or Path(basename).suffix.casefold() != ".qvoice"
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "qwen_pack_filename_invalid",
                "message": "Choose a single .qvoice file with a safe filename.",
            },
        )
    target = Path(directory) / basename
    size = 0
    try:
        async with aiofiles.open(target, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 300 * 1024 * 1024:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "qwen_pack_upload_too_large",
                            "message": "The .qvoice exceeds Alexandria's 300 MB limit.",
                        },
                    )
                await handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail={
                "code": "qwen_pack_upload_empty",
                "message": "The uploaded .qvoice is empty.",
            },
        )
    return target


@app.get("/api/community-qwen-packs")
async def get_community_qwen_packs():
    try:
        return {"packs": list_qwen_packs(reusable_root=LEGACY_ROOT_DIR)}
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.get("/api/community-qwen-packs/catalog")
async def get_community_qwen_pack_catalog():
    try:
        return {
            "candidates": await asyncio.to_thread(
                curated_qwen_candidate_catalog,
                reusable_root=LEGACY_ROOT_DIR,
            )
        }
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.post("/api/community-qwen-packs/catalog/{candidate_key}/install")
async def install_community_qwen_pack_candidate(
    candidate_key: str,
    request: CommunityQwenCandidateInstallRequest,
):
    try:
        return await asyncio.to_thread(
            install_curated_qwen_candidate,
            candidate_key=candidate_key,
            reusable_root=LEGACY_ROOT_DIR,
            q_bits=request.q_bits,
            cleanup_downloaded_source=request.cleanup_downloaded_source,
        )
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.post("/api/community-qwen-packs/inspect")
async def inspect_community_qwen_pack(file: UploadFile = File(...)):
    with tempfile.TemporaryDirectory(prefix="alexandria-qvoice-inspect-") as temporary:
        source = await _store_qvoice_upload(file, temporary)
        try:
            return inspect_qvoice_upload(source_path=source)
        except CommunityQwenPackError as exc:
            _raise_community_qwen_pack_http_error(exc)


@app.post("/api/community-qwen-packs/import")
async def import_community_qwen_pack(file: UploadFile = File(...)):
    with tempfile.TemporaryDirectory(prefix="alexandria-qvoice-import-") as temporary:
        source = await _store_qvoice_upload(file, temporary)
        try:
            return install_qvoice_pack(
                source_path=source,
                reusable_root=LEGACY_ROOT_DIR,
            )
        except CommunityQwenPackError as exc:
            _raise_community_qwen_pack_http_error(exc)


@app.post("/api/community-qwen-packs/inspect-directory")
async def inspect_community_qwen_directory(
    request: CommunityQwenDirectoryRequest,
):
    try:
        return await asyncio.to_thread(
            inspect_qwen_pack_path,
            source_path=request.source_path,
            reusable_root=LEGACY_ROOT_DIR,
            q_bits=request.q_bits,
        )
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.post("/api/community-qwen-packs/import-directory")
async def import_community_qwen_directory(
    request: CommunityQwenDirectoryRequest,
):
    try:
        return await asyncio.to_thread(
            install_community_qwen_pack,
            source_path=request.source_path,
            reusable_root=LEGACY_ROOT_DIR,
            q_bits=request.q_bits,
        )
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.post("/api/community-qwen-packs/{pack_id}/approve")
async def approve_community_qwen_pack(
    pack_id: str,
    request: CommunityQwenPackApproveRequest,
):
    try:
        return approve_qvoice_pack(
            pack_id=pack_id,
            expected_preview_fingerprint=request.expected_preview_fingerprint,
            reusable_root=LEGACY_ROOT_DIR,
        )
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.post("/api/community-qwen-packs/{pack_id}/preview")
async def generate_community_qwen_pack_preview(
    pack_id: str,
    request: CommunityQwenPackPreviewRequest,
):
    try:
        item, pack_path = resolve_qvoice_pack(
            pack_id=pack_id,
            reusable_root=LEGACY_ROOT_DIR,
        )
        engine = project_manager.get_engine()
        if engine is None:
            raise CommunityQwenPackError(
                "qwen_pack_engine_unavailable",
                "Alexandria could not initialize the MLX voice engine.",
            )
        instruction = " ".join(
            (
                request.persistent_description.strip(),
                request.direction.strip(),
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="alexandria-qvoice-preview-"
        ) as temporary:
            generated = Path(temporary) / "preview.wav"
            engine._init_mlx().generate_community_qwen_pack(
                text=request.text.strip(),
                pack_path=str(pack_path),
                family=str(item.get("family") or "qvoice_graft"),
                expected_sha256=str(item.get("sha256") or ""),
                approval_fingerprint="",
                instruct=instruction,
                language="English",
                output_path=str(generated),
                seed=request.generation_seed,
                request_label=f"preview:{pack_id}",
                review_mode=True,
            )
            reviewed = record_qvoice_preview(
                pack_id=pack_id,
                preview_path=generated,
                persistent_description=request.persistent_description,
                direction=request.direction,
                reusable_root=LEGACY_ROOT_DIR,
            )
        return {
            **reviewed,
            "audio_url": f"/api/community-qwen-packs/{pack_id}/preview",
            "preview_text": request.text.strip(),
            "generation_seed": request.generation_seed,
        }
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.get("/api/community-qwen-packs/{pack_id}/preview")
async def get_community_qwen_pack_preview(pack_id: str):
    try:
        item, _ = resolve_qvoice_pack(
            pack_id=pack_id,
            reusable_root=LEGACY_ROOT_DIR,
        )
        preview = resolve_qvoice_preview(
            item=item,
            reusable_root=LEGACY_ROOT_DIR,
        )
        return FileResponse(preview, filename=preview.name, media_type="audio/wav")
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.delete("/api/community-qwen-packs/{pack_id}")
async def delete_community_qwen_pack(pack_id: str):
    try:
        return remove_qvoice_pack(
            pack_id=pack_id,
            reusable_root=LEGACY_ROOT_DIR,
        )
    except CommunityQwenPackError as exc:
        _raise_community_qwen_pack_http_error(exc)


@app.get("/api/voice-library")
def get_voice_library(
    project_id: Optional[str] = None,
    return_route: Optional[str] = "#/voices",
):
    try:
        with _RUNTIME_PROJECT_LOCK:
            root_dir = ROOT_DIR
            resolved_project_id = project_id or ACTIVE_PROJECT_ID
            reusable_root_dir = LEGACY_ROOT_DIR
        return build_voice_library(
            root_dir=root_dir,
            project_id=resolved_project_id,
            return_route=return_route,
            reusable_root_dir=reusable_root_dir,
        )
    except VoiceLibraryError as exc:
        _raise_voice_library_http_error(exc)


@app.post("/api/voice-library/built-in-range-preview")
async def preview_built_in_voice_range(
    request: BuiltInVoiceRangePreviewRequest,
):
    requested_voice = request.voice.strip().replace(" ", "_").casefold()
    voice = next(
        (
            candidate
            for candidate in BUILT_IN_VOICES
            if candidate.casefold() == requested_voice
        ),
        None,
    )
    if voice is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "built_in_voice_invalid",
                "message": "Choose an available built-in Voice.",
            },
        )
    persistent_description = request.persistent_description.strip()
    if not persistent_description:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "persistent_voice_description_required",
                "message": "Add a persistent voice description before previewing the delivery range.",
            },
        )
    sequence = [
        {
            "id": "baseline",
            "label": "Baseline",
            "text": "I knew you would come back before the lights went out.",
            "instruction": "Natural, neutral delivery with clear diction.",
        },
        {
            "id": "happy",
            "label": "Happy",
            "text": "You came back—oh, this is wonderful!",
            "instruction": "Openly happy, bright, warm, and delighted.",
        },
        {
            "id": "sad",
            "label": "Sad",
            "text": "I kept your chair by the window, even after I knew.",
            "instruction": "Quietly sad, vulnerable, restrained, and reflective.",
        },
        {
            "id": "angry",
            "label": "Angry",
            "text": "You knew the cost, and you did it anyway.",
            "instruction": "Controlled anger, firm, intense, and accusatory without shouting.",
        },
    ]
    preview_fingerprint = fingerprint_value(
        {
            "schema_version": 1,
            "voice": voice,
            "persistent_description": persistent_description,
            "sequence": sequence,
        }
    )
    preview_dir = Path(DESIGNED_VOICES_DIR, "previews")
    preview_dir.mkdir(parents=True, exist_ok=True)
    filename = f"built_in_range_{preview_fingerprint[:20]}.wav"
    preview_path = preview_dir / filename
    status = "cached" if preview_path.is_file() else "generated"
    if not preview_path.is_file():
        engine = project_manager.get_engine()
        if not engine:
            raise HTTPException(
                status_code=500,
                detail="Failed to initialize the built-in Voice engine.",
            )
        speaker = "_built_in_range_preview_"
        voice_config = {
            speaker: {
                "type": "custom",
                "voice": voice,
                "description": persistent_description,
                "character_style": persistent_description,
            }
        }
        try:
            with tempfile.TemporaryDirectory(
                prefix="built-in-range-",
                dir=preview_dir,
            ) as temporary_dir:
                combined = AudioSegment.empty()
                for index, item in enumerate(sequence):
                    segment_path = Path(temporary_dir, f"{index:02d}_{item['id']}.wav")
                    generated = engine.generate_voice(
                        item["text"],
                        item["instruction"],
                        speaker,
                        voice_config,
                        str(segment_path),
                    )
                    if generated is False or not segment_path.is_file():
                        raise RuntimeError(
                            f"The {item['label'].lower()} preview did not produce audio."
                        )
                    if index:
                        combined += AudioSegment.silent(duration=550)
                    with segment_path.open("rb") as segment_file:
                        combined += AudioSegment.from_file(segment_file, format="wav")
                staged_path = Path(temporary_dir, filename)
                export_handle = combined.export(staged_path, format="wav")
                export_handle.close()
                os.replace(staged_path, preview_path)
        except Exception as exc:
            logger.error("Built-in Voice range preview failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "built_in_voice_range_preview_failed",
                    "message": str(exc),
                },
            ) from exc
    return {
        "status": status,
        "audio_url": f"/designed_voices/previews/{filename}",
        "voice": voice.replace("_", " "),
        "persistent_description": persistent_description,
        "preview_fingerprint": preview_fingerprint,
        "sequence": sequence,
    }


@app.get("/api/voice-library/{voice_id}/preview")
async def get_voice_library_preview(voice_id: str):
    try:
        path = resolve_voice_library_preview(
            voice_id=voice_id,
            reusable_root_dir=LEGACY_ROOT_DIR,
        )
    except VoiceLibraryError as exc:
        _raise_voice_library_http_error(exc)
    media_type = (
        "audio/mpeg"
        if path.suffix.casefold() == ".mp3"
        else "audio/flac"
        if path.suffix.casefold() == ".flac"
        else "audio/ogg"
        if path.suffix.casefold() == ".ogg"
        else "audio/wav"
    )
    return FileResponse(path, filename=path.name, media_type=media_type)


@app.post("/api/voice-library/assign")
async def assign_voice_library_voice(request: VoiceLibraryAssignRequest):
    try:
        assignment = resolve_voice_library_assignment(
            voice_id=request.voice_id,
            reusable_root_dir=LEGACY_ROOT_DIR,
            project_root_dir=ROOT_DIR,
        )
    except VoiceLibraryError as exc:
        _raise_voice_library_http_error(exc)

    try:
        aggregate = inspect_cast_project(
            root_dir=ROOT_DIR,
            selected_character_id=request.character_id,
        )
    except CastAggregateError as exc:
        _raise_cast_aggregate_http_error(exc)
    character = aggregate.get("selected_character")
    if not isinstance(character, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "cast_character_not_found",
                "message": "The selected Cast character no longer exists.",
            },
        )
    script = character.get("script_connection") or {}
    script_label = str(
        script.get("resolved_script_voice_label")
        or character.get("canonical_name")
        or character.get("display_name")
        or ""
    ).strip()
    if not script_label:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cast_script_label_unresolved",
                "message": "Resolve this character's Script label before assigning a Voice.",
            },
        )

    current_config: dict[str, dict] = {}
    if os.path.exists(VOICE_CONFIG_PATH):
        try:
            with open(VOICE_CONFIG_PATH, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "voice_config_invalid",
                    "message": "The saved Voice configuration is invalid and was not changed.",
                },
            ) from exc
        if not isinstance(loaded, dict):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "voice_config_invalid",
                    "message": "The saved Voice configuration must contain an object.",
                },
            )
        current_config = loaded
    current_fingerprint = fingerprint_value(current_config)
    if (
        request.expected_voice_config_fingerprint
        and request.expected_voice_config_fingerprint != current_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "voice_config_changed",
                "message": "Voice assignments changed before this save. Reload Cast and choose again.",
            },
        )

    project_root = _voice_config_project_root()
    byte_changes: dict[Path, bytes | None] = {}
    validation_assets: dict[Path, bytes] = {}
    try:
        for asset in assignment.get("assets") or []:
            source = Path(asset["source_path"]).resolve()
            relative = Path(str(asset["relative_path"]))
            destination = (project_root / relative).resolve()
            if not destination.is_relative_to(project_root):
                raise VoiceLibraryError(
                    "voice_library_asset_path_invalid",
                    "The reusable Voice contains an unsafe asset path.",
                )
            source_bytes = source.read_bytes()
            validation_assets[relative] = source_bytes
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.read_bytes() != source_bytes:
                    raise VoiceLibraryError(
                        "voice_library_asset_conflict",
                        f"A different project asset already uses {destination.name}.",
                    )
                continue
            byte_changes[destination] = source_bytes

        update = copy.deepcopy(dict(assignment["configuration"]))
        if assignment.get("kind") == "project_voice_alias":
            target_key = str(assignment.get("target_configuration_key") or "").strip()
            target_config = current_config.get(target_key)
            if not target_key or not isinstance(target_config, dict):
                raise VoiceLibraryError(
                    "voice_library_voice_not_found",
                    "The selected project Voice is no longer available.",
                )
            if target_key.casefold() == script_label.casefold():
                update = copy.deepcopy(target_config)
                update["library_voice_id"] = assignment["voice_id"]
        if validation_assets:
            with tempfile.TemporaryDirectory(
                prefix=".voice-assignment-validation-",
                dir=project_root,
            ) as validation_directory:
                validation_root = Path(validation_directory)
                for relative, source_bytes in validation_assets.items():
                    staged = (validation_root / relative).resolve()
                    if not staged.is_relative_to(validation_root.resolve()):
                        raise VoiceLibraryError(
                            "voice_library_asset_path_invalid",
                            "The reusable Voice contains an unsafe validation asset path.",
                        )
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(source_bytes)
                _validate_voice_library_assignment_update(
                    update,
                    validation_root=validation_root,
                )
        else:
            _validate_voice_library_assignment_update(
                update,
                validation_root=project_root,
            )
        candidate, alias_diagnostics = merge_voice_config_updates(
            current_config,
            {script_label: update},
        )
        invalidation = _apply_voice_config_dependency_change(
            before=current_config,
            after=candidate,
            operation="voice_library_assign",
            reason="Production Voice Library assignment changed.",
            byte_changes=byte_changes,
            metadata={
                "route": "/api/voice-library/assign",
                "voice_id": assignment["voice_id"],
                "character_id": request.character_id,
                "script_label": script_label,
            },
        )
    except VoiceLibraryError as exc:
        _raise_voice_library_http_error(exc)

    try:
        refreshed = inspect_cast_project(
            root_dir=ROOT_DIR,
            selected_character_id=request.character_id,
        )
    except CastAggregateError as exc:
        _raise_cast_aggregate_http_error(exc)
    return {
        "status": "assigned",
        "voice_id": assignment["voice_id"],
        "voice_name": assignment["name"],
        "character_id": request.character_id,
        "script_label": script_label,
        "voice_config_fingerprint": fingerprint_value(candidate),
        "aliases": alias_diagnostics,
        "audio_invalidation": (
            _audio_invalidation_summary(invalidation)
            if invalidation is not None
            else None
        ),
        "character": refreshed.get("selected_character"),
    }


@app.post("/api/voice-library/clear")
async def clear_voice_library_assignment(request: VoiceLibraryClearRequest):
    try:
        aggregate = inspect_cast_project(
            root_dir=ROOT_DIR,
            selected_character_id=request.character_id,
        )
    except CastAggregateError as exc:
        _raise_cast_aggregate_http_error(exc)
    character = aggregate.get("selected_character")
    if not isinstance(character, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "cast_character_not_found",
                "message": "The selected Cast character no longer exists.",
            },
        )
    script = character.get("script_connection") or {}
    script_label = str(
        script.get("resolved_script_voice_label")
        or character.get("canonical_name")
        or character.get("display_name")
        or ""
    ).strip()
    if not script_label:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cast_script_label_unresolved",
                "message": "Resolve this character's Script label before clearing its Voice.",
            },
        )

    config_path = Path(VOICE_CONFIG_PATH)
    if not config_path.is_file():
        return {
            "status": "absent",
            "character_id": request.character_id,
            "script_label": script_label,
            "voice_config_fingerprint": fingerprint_value({}),
            "removed_assets": [],
            "character": character,
        }
    try:
        current_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "voice_config_invalid",
                "message": "The saved Voice configuration is invalid and was not changed.",
            },
        ) from exc
    if not isinstance(current_config, dict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "voice_config_invalid",
                "message": "The saved Voice configuration must contain an object.",
            },
        )
    current_fingerprint = fingerprint_value(current_config)
    if (
        request.expected_voice_config_fingerprint
        and request.expected_voice_config_fingerprint != current_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "voice_config_changed",
                "message": "Voice assignments changed before this clear action. Reload Cast and try again.",
            },
        )
    removed = current_config.get(script_label)
    if not isinstance(removed, dict):
        return {
            "status": "absent",
            "character_id": request.character_id,
            "script_label": script_label,
            "voice_config_fingerprint": current_fingerprint,
            "removed_assets": [],
            "character": character,
        }
    candidate = copy.deepcopy(current_config)
    candidate.pop(script_label, None)
    try:
        for key, value in candidate.items():
            if isinstance(value, dict) and value.get("alias_of") == script_label:
                raise VoiceLibraryError(
                    "voice_library_assignment_in_use",
                    f"{key} still shares this Voice. Clear or reassign that alias first.",
                )
    except VoiceLibraryError as exc:
        _raise_voice_library_http_error(exc)

    removed_assets: list[str] = []
    cleanup_warnings: list[str] = []
    library_voice_id = str(removed.get("library_voice_id") or "").strip()
    assignment = None
    if library_voice_id:
        try:
            assignment = resolve_voice_library_assignment(
                voice_id=library_voice_id,
                reusable_root_dir=LEGACY_ROOT_DIR,
            )
        except VoiceLibraryError as exc:
            cleanup_warnings.append(str(exc))
    remaining_serialized = json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
    )
    project_root = _voice_config_project_root()
    byte_changes: dict[Path, bytes | None] = {}
    for asset in (assignment or {}).get("assets") or []:
        relative_text = str(asset.get("relative_path") or "").strip()
        if not relative_text or relative_text in remaining_serialized:
            continue
        source = Path(asset["source_path"]).resolve()
        destination = (project_root / relative_text).resolve()
        if not destination.is_relative_to(project_root):
            cleanup_warnings.append(f"Skipped unsafe asset path {relative_text}.")
            continue
        try:
            if (
                destination.is_file()
                and source.is_file()
                and destination.read_bytes() == source.read_bytes()
            ):
                byte_changes[destination] = None
                removed_assets.append(relative_text)
        except OSError as exc:
            cleanup_warnings.append(
                f"Could not remove unused asset {relative_text}: {exc}"
            )

    invalidation = _apply_voice_config_dependency_change(
        before=current_config,
        after=candidate,
        operation="voice_library_clear",
        reason="Production Voice Library assignment was cleared.",
        byte_changes=byte_changes,
        metadata={
            "route": "/api/voice-library/clear",
            "character_id": request.character_id,
            "script_label": script_label,
        },
    )

    try:
        refreshed = inspect_cast_project(
            root_dir=ROOT_DIR,
            selected_character_id=request.character_id,
        )
    except CastAggregateError as exc:
        _raise_cast_aggregate_http_error(exc)
    return {
        "status": "cleared",
        "character_id": request.character_id,
        "script_label": script_label,
        "voice_config_fingerprint": fingerprint_value(candidate),
        "removed_assets": removed_assets,
        "cleanup_warnings": cleanup_warnings,
        "audio_invalidation": (
            _audio_invalidation_summary(invalidation)
            if invalidation is not None
            else None
        ),
        "character": refreshed.get("selected_character"),
    }


@app.get("/api/library")
def get_library_inventory(
    kind: Optional[str] = None,
    state: Optional[str] = None,
    search: Optional[str] = None,
    project_id: Optional[str] = None,
    character_id: Optional[str] = None,
    return_route: Optional[str] = "#/library",
):
    try:
        return _read_runtime_project(
            _current_library_inventory,
            kind=kind,
            state=state,
            search=search,
            project_id=project_id,
            character_id=character_id,
            return_route=return_route,
        )
    except LibraryInventoryError as exc:
        _raise_library_inventory_http_error(exc)


@app.get("/api/library/artifacts/{artifact_id}")
async def get_library_artifact_route(
    artifact_id: str,
    project_id: Optional[str] = None,
    character_id: Optional[str] = None,
    return_route: Optional[str] = "#/library",
):
    try:
        inventory = _current_library_inventory(
            project_id=project_id,
            character_id=character_id,
            return_route=return_route,
        )
        return get_library_artifact(inventory, artifact_id)
    except LibraryInventoryError as exc:
        _raise_library_inventory_http_error(exc)


@app.post("/api/library/artifacts/{artifact_id}/delete-impact")
async def get_library_delete_impact(
    artifact_id: str,
    request: LibraryContextRequest,
):
    try:
        inventory = _current_library_inventory(**_library_context(request))
        return build_library_delete_impact(
            inventory=inventory,
            artifact_id=artifact_id,
        )
    except LibraryInventoryError as exc:
        _raise_library_inventory_http_error(exc)


def _active_library_operations() -> list[str]:
    operation_keys = (
        "audio",
        "dataset_gen",
        "dataset_builder",
        "preparer",
        "batch_preparer",
        "lora_training",
    )
    return [
        key
        for key in operation_keys
        if bool(process_state.get(key, {}).get("running"))
    ]


async def _dispatch_library_delete(impact: dict) -> dict:
    kind = impact["kind"]
    key = impact["key"]
    if kind == "designed_voice":
        return await voice_design_delete(key)
    if kind == "clone_reference":
        return await clone_voices_delete(key)
    if kind == "dataset_builder_project":
        return await dataset_builder_delete(key)
    if kind == "lora_dataset":
        return await lora_delete_dataset(key)
    if kind == "lora_adapter":
        return await lora_delete_model(key)
    raise HTTPException(
        status_code=409,
        detail={
            "code": "library_delete_unsupported",
            "message": "This Library artifact has no authoritative delete route.",
            "context": {"kind": kind},
        },
    )


@app.delete("/api/library/artifacts/{artifact_id}")
async def delete_library_artifact(
    artifact_id: str,
    request: LibraryDeleteRequest,
):
    running = _active_library_operations()
    if running:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "library_operation_running",
                "message": (
                    "Library deletion is blocked while an audio or Voice "
                    "operation is running."
                ),
                "context": {"operations": running},
            },
        )
    try:
        inventory = _current_library_inventory(**_library_context(request))
        impact = validate_library_delete_request(
            inventory=inventory,
            artifact_id=artifact_id,
            expected_inventory_fingerprint=(
                request.expected_inventory_fingerprint
            ),
            expected_artifact_fingerprint=(
                request.expected_artifact_fingerprint
            ),
            confirm_name=request.confirm_name,
        )
    except LibraryInventoryError as exc:
        _raise_library_inventory_http_error(exc)
    result = await _dispatch_library_delete(impact)
    try:
        updated = _current_library_inventory(**_library_context(request))
        get_library_artifact(updated, artifact_id)
    except LibraryInventoryError as exc:
        if exc.code != "library_artifact_not_found":
            _raise_library_inventory_http_error(exc)
        return {
            "status": "deleted",
            "artifact_id": artifact_id,
            "kind": impact["kind"],
            "result": result,
            "inventory_fingerprint": updated["inventory_fingerprint"],
        }
    raise HTTPException(
        status_code=409,
        detail={
            "code": "library_delete_incomplete",
            "message": (
                "The authoritative delete route returned without removing "
                "the Library artifact."
            ),
            "context": {"artifact_id": artifact_id},
        },
    )


def _raise_project_catalog_http_error(exc: ProjectCatalogError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _current_project_identity() -> tuple[dict, str]:
    flow = _current_project_flow_status()
    project_id = str(
        ACTIVE_PROJECT_ID
        or flow.get("project", {}).get("id")
        or ""
    ).strip()
    if not project_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "current_project_identity_missing",
                "message": "The active project has no stable identity.",
            },
        )
    return flow, project_id


def _project_catalog_payload() -> dict:
    global ACTIVE_PROJECT_ID, ACTIVE_PROJECT_STORAGE_KIND
    global LEGACY_FLOW_SNAPSHOT, LEGACY_PROJECT_ID
    legacy_flow = LEGACY_FLOW_SNAPSHOT
    if legacy_flow is None:
        legacy_flow = _current_project_flow_status()
        LEGACY_FLOW_SNAPSHOT = copy.deepcopy(legacy_flow)
        LEGACY_PROJECT_ID = str(
            legacy_flow.get("project", {}).get("id") or ""
        ).strip() or None
        if ACTIVE_PROJECT_ID is None:
            ACTIVE_PROJECT_ID = LEGACY_PROJECT_ID
            ACTIVE_PROJECT_STORAGE_KIND = "legacy_checkout"

    legacy_root_text = str(
        legacy_flow.get("project", {})
        .get("technical_details", {})
        .get("project_path")
        or LEGACY_ROOT_DIR
    ).strip()
    legacy_root = Path(legacy_root_text).expanduser().resolve()
    payload = list_project_summaries(
        data_root=PROJECTS_DATA_ROOT,
        current_project_root=legacy_root,
        current_flow_summary=legacy_flow,
    )
    active_id = str(
        ACTIVE_PROJECT_ID
        or LEGACY_PROJECT_ID
        or payload.get("current_project_id")
        or ""
    ).strip()
    selected_id = str(payload.get("last_selected_project_id") or active_id)
    live_flow = _current_project_flow_status()
    live_stage_map = live_flow.get("stage_map", {})
    live_recommended_stage = str(live_flow.get("recommended_stage") or "").strip()
    live_recommended = (
        live_stage_map.get(live_recommended_stage, {})
        if isinstance(live_stage_map, dict) and live_recommended_stage
        else {}
    )
    for project in payload.get("projects", []):
        identifier = str(project.get("id") or "")
        current = identifier == active_id
        project["current"] = current
        project["selected"] = identifier == selected_id
        if project.get("availability_state") == "available":
            project["activation_state"] = "current" if current else "available"
        if current:
            project["storage_kind"] = ACTIVE_PROJECT_STORAGE_KIND
            project["current_recommended_stage"] = live_recommended_stage or None
            project["stage_summary"] = live_recommended.get("summary")
            project["stage_states"] = {
                key: (
                    live_stage_map.get(key, {}).get("state")
                    if isinstance(live_stage_map.get(key), dict)
                    else None
                )
                for key in ("script", "cast", "produce", "export")
            }
            project["blocker_count"] = int(live_flow.get("blocker_count") or 0)
            project["latest_meaningful_activity"] = (
                live_flow.get("project", {}).get("latest_meaningful_activity")
                or project.get("latest_meaningful_activity")
            )
            project["resumable_operation"] = live_flow.get("resumable_operation")
            project["compatibility_state"] = (
                live_flow.get("compatibility", {}).get("state") or "current"
            )
            project["completion_state"] = (
                live_flow.get("completion_state") or "requires_work"
            )
            project["safe_next_action"] = live_flow.get("safe_next_action")
    payload["current_project_id"] = active_id
    payload["storage"]["activation_contract"] = "dynamic"
    payload["projects"].sort(
        key=lambda item: not bool(item.get("current"))
    )
    return payload


def _project_summary(project_id: str) -> tuple[dict, dict]:
    payload = _project_catalog_payload()
    project = next(
        (
            item
            for item in payload.get("projects", [])
            if item.get("id") == project_id
        ),
        None,
    )
    if not isinstance(project, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "project_not_found",
                "message": "The requested project was not found.",
            },
        )
    return payload, project


def _project_activation_legacy_ids(flow: dict) -> list[str]:
    return [
        value
        for value in (
            LEGACY_PROJECT_ID,
            str(flow.get("project", {}).get("id") or ""),
        )
        if value
    ]


def _restore_catalog_selection_after_failed_activation(
    *,
    project_id: str,
    current_project_id: str,
    legacy_project_ids: list[str],
    expected_catalog_fingerprint: str,
) -> dict:
    try:
        return select_project(
            data_root=PROJECTS_DATA_ROOT,
            project_id=project_id,
            current_project_id=current_project_id,
            legacy_project_ids=legacy_project_ids,
            expected_catalog_fingerprint=expected_catalog_fingerprint,
        )
    except ProjectCatalogError as exc:
        logger.exception(
            "Could not restore project selection after activation failure: %s",
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "project_activation_selection_rollback_failed",
                "message": (
                    "The project runtime stayed on the previous project, but "
                    "Alexandria could not restore the previous catalog selection."
                ),
                "context": {
                    "project_id": project_id,
                    "catalog_error": exc.as_detail(),
                },
            },
        ) from exc


async def _store_project_creation_upload(
    file: UploadFile,
    directory: str | Path,
    *,
    maximum_bytes: int = 512 * 1024 * 1024,
) -> Path:
    original_name = str(file.filename or "").strip()
    basename = Path(original_name).name
    if not basename or basename != original_name or basename in {".", ".."}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "project_source_filename_unsafe",
                "message": "The project source filename is unsafe.",
            },
        )
    target = Path(directory) / basename
    size = 0
    try:
        async with aiofiles.open(target, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "project_source_too_large",
                            "message": "The project source exceeds the supported size limit.",
                        },
                    )
                await handle.write(chunk)
        if size <= 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "project_source_empty",
                    "message": "The project source is empty.",
                },
            )
        return target
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def _raise_project_template_http_error(exc: ProjectTemplateError):
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


@app.get("/api/templates")
async def get_project_templates():
    try:
        return list_project_templates(PROJECTS_DATA_ROOT)
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)


@app.post("/api/templates")
async def create_project_template_route(
    request: ProjectTemplateCreateRequest,
):
    try:
        return create_project_template(
            data_root=PROJECTS_DATA_ROOT,
            fields=request.template.model_dump(),
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
        )
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)


@app.put("/api/templates/{template_id}")
async def update_project_template_route(
    template_id: str,
    request: ProjectTemplateUpdateRequest,
):
    try:
        return update_project_template(
            data_root=PROJECTS_DATA_ROOT,
            template_id=template_id,
            fields=request.template.model_dump(),
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
            expected_template_fingerprint=request.expected_template_fingerprint,
        )
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)


@app.post("/api/templates/{template_id}/duplicate")
async def duplicate_project_template_route(
    template_id: str,
    request: ProjectTemplateDuplicateRequest,
):
    try:
        return duplicate_project_template(
            data_root=PROJECTS_DATA_ROOT,
            template_id=template_id,
            name=request.name,
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
        )
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)


@app.post("/api/templates/{template_id}/default")
async def set_default_project_template_route(
    template_id: str,
    request: ProjectTemplateDefaultRequest,
):
    try:
        return set_default_project_template(
            data_root=PROJECTS_DATA_ROOT,
            template_id=template_id,
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
        )
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)


@app.get("/api/templates/{template_id}/delete-impact")
async def get_project_template_delete_impact(template_id: str):
    try:
        return project_template_delete_impact(
            data_root=PROJECTS_DATA_ROOT,
            template_id=template_id,
        )
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)


@app.delete("/api/templates/{template_id}")
async def delete_project_template_route(
    template_id: str,
    request: ProjectTemplateDeleteRequest,
):
    try:
        return delete_project_template(
            data_root=PROJECTS_DATA_ROOT,
            template_id=template_id,
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
            expected_template_fingerprint=request.expected_template_fingerprint,
            confirmation_text=request.confirmation_text,
            acknowledge_usage=request.acknowledge_usage,
        )
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)


def _validate_project_template_application(
    *,
    template_id: str | None,
    generation_method: str,
    preset: str,
    source_language: str,
    output_language: str,
) -> str | None:
    if not str(template_id or "").strip():
        return None
    template = resolve_project_template(
        data_root=PROJECTS_DATA_ROOT,
        template_id=str(template_id).strip(),
    )
    actual = {
        "generation_method": generation_method,
        "preset": preset,
        "source_language": str(source_language).strip(),
        "output_language": str(output_language).strip(),
    }
    mismatches = {
        key: {
            "template": template.get(key),
            "submitted": value,
        }
        for key, value in actual.items()
        if (
            str(template.get(key) or "").casefold()
            != str(value or "").casefold()
        )
    }
    if mismatches:
        raise ProjectTemplateError(
            "template_application_mismatch",
            "The New Project settings no longer match the selected template. Reapply the template or continue without template provenance.",
            status_code=409,
            context={
                "template_id": template["id"],
                "mismatches": mismatches,
            },
        )
    return str(template["id"])


@app.get("/api/projects")
def get_projects():
    try:
        return _read_runtime_project(_project_catalog_payload)
    except ProjectCatalogError as exc:
        _raise_project_catalog_http_error(exc)


@app.get("/api/projects/{project_id}/cover")
async def get_project_cover(project_id: str):
    root = (
        Path(ROOT_DIR).expanduser().resolve()
        if project_id == str(ACTIVE_PROJECT_ID or "")
        else None
    )
    if root is None:
        try:
            catalog = _project_catalog_payload()
        except ProjectCatalogError as exc:
            _raise_project_catalog_http_error(exc)
        project = next(
            (item for item in catalog.get("projects", []) if item.get("id") == project_id),
            None,
        )
        if not isinstance(project, dict):
            raise HTTPException(status_code=404, detail={"code": "project_not_found", "message": "Project cover is unavailable."})
        root_text = str(project.get("technical_details", {}).get("project_path") or "").strip()
        root = Path(root_text).expanduser().resolve() if root_text else None
    if root is None or not root.is_dir():
        raise HTTPException(status_code=404, detail={"code": "project_cover_unavailable", "message": "Project cover is unavailable."})

    cover = resolve_export_cover(root)
    if cover and cover.data:
        return Response(content=cover.data, media_type=cover.media_type)
    raise HTTPException(status_code=404, detail={"code": "project_cover_unavailable", "message": "Project cover is unavailable."})


@app.post("/api/projects/inspect-source")
async def inspect_project_source_route(
    generation_method: Literal[
        "local",
        "chatgpt_task_bundle",
        "import_existing_script",
    ] = Form(...),
    source_file: UploadFile = File(...),
):
    with tempfile.TemporaryDirectory(
        prefix="alexandria-project-source-inspection-"
    ) as temporary:
        source_path = await _store_project_creation_upload(
            source_file,
            temporary,
        )
        try:
            return inspect_project_source(
                source_path,
                generation_method=generation_method,
            )
        except ProjectCatalogError as exc:
            _raise_project_catalog_http_error(exc)


@app.post("/api/projects")
async def create_project(
    project_name: str = Form(...),
    book_title: str | None = Form(None),
    author: str | None = Form(None),
    source_language: str = Form(...),
    output_language: str = Form(...),
    generation_method: Literal[
        "local",
        "chatgpt_task_bundle",
        "import_existing_script",
    ] = Form(...),
    preset: Literal[
        "standard",
        "maximum_fidelity",
        "faster_draft",
        "custom",
    ] = Form("standard"),
    template_id: str | None = Form(None),
    expected_catalog_fingerprint: str = Form(...),
    source_file: UploadFile = File(...),
):
    try:
        applied_template_id = _validate_project_template_application(
            template_id=template_id,
            generation_method=generation_method,
            preset=preset,
            source_language=source_language,
            output_language=output_language,
        )
    except ProjectTemplateError as exc:
        _raise_project_template_http_error(exc)
    flow, current_project_id = _current_project_identity()
    _assert_runtime_project_switch_available()
    catalog_before = _project_catalog_payload()
    previous_selected_project_id = str(
        catalog_before.get("last_selected_project_id") or current_project_id
    ).strip()
    legacy_project_ids = _project_activation_legacy_ids(flow)
    legacy_name = str(flow.get("project", {}).get("name") or "").strip()
    with tempfile.TemporaryDirectory(
        prefix="alexandria-project-source-"
    ) as temporary:
        source_path = await _store_project_creation_upload(
            source_file,
            temporary,
        )
        try:
            starter_voice_pack = inspect_primary_responsive_voice_pack(
                LEGACY_ROOT_DIR
            )
            responsive_pack_expected = (
                Path(LEGACY_ROOT_DIR, "production_prompt_routes").exists()
                or Path(
                    LEGACY_ROOT_DIR,
                    "primary_responsive_voice_pack.json",
                ).exists()
            )
            if (
                responsive_pack_expected
                and starter_voice_pack.get("ready") is not True
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "primary_responsive_voice_pack_unavailable",
                        "message": (
                            "The installed responsive Narrator, Benny, and "
                            "Doctor voice pack failed validation. Repair the "
                            "pack before creating this project so it is not "
                            "silently omitted."
                        ),
                        "context": {
                            "error": starter_voice_pack.get("error"),
                        },
                    },
                )
            result = create_managed_project(
                data_root=PROJECTS_DATA_ROOT,
                project_name=project_name,
                source_path=source_path,
                book_title=book_title,
                author=author,
                source_language=source_language,
                output_language=output_language,
                generation_method=generation_method,
                preset=preset,
                template_id=applied_template_id,
                starter_voice_pack_root=(
                    LEGACY_ROOT_DIR
                    if starter_voice_pack.get("ready") is True
                    else None
                ),
                expected_catalog_fingerprint=expected_catalog_fingerprint,
                reserved_names=[legacy_name] if legacy_name else [],
            )
            project = result.get("project") or {}
            root_path = str(
                project.get("technical_details", {}).get("project_path")
                or ""
            ).strip()
            project_id = str(project.get("id") or "").strip()
            try:
                activation = _activate_runtime_project(
                    root_dir=root_path,
                    project_id=project_id,
                    storage_kind="managed",
                )
            except Exception as activation_exc:
                _restore_catalog_selection_after_failed_activation(
                    project_id=previous_selected_project_id,
                    current_project_id=current_project_id,
                    legacy_project_ids=legacy_project_ids,
                    expected_catalog_fingerprint=result[
                        "catalog_fingerprint"
                    ],
                )
                activation_detail = (
                    activation_exc.detail
                    if isinstance(activation_exc, HTTPException)
                    else {
                        "code": "project_activation_failed",
                        "message": str(activation_exc),
                    }
                )
                raise HTTPException(
                    status_code=(
                        activation_exc.status_code
                        if isinstance(activation_exc, HTTPException)
                        else 500
                    ),
                    detail={
                        "code": "project_created_activation_failed",
                        "message": (
                            "The project was created safely but could not be "
                            "activated. It remains available in Projects."
                        ),
                        "context": {
                            "project_id": project_id,
                            "project_path": root_path,
                            "activation_error": activation_detail,
                            "previous_selection_restored": True,
                        },
                    },
                ) from activation_exc
            result["activation"] = activation
            result["activation_state"] = "current"
            project["current"] = True
            project["selected"] = True
            project["activation_state"] = "current"
            return result
        except ProjectCatalogError as exc:
            _raise_project_catalog_http_error(exc)


@app.post("/api/projects/{project_id}/open")
async def open_project(
    project_id: str,
    request: ProjectOpenRequest,
):
    flow, current_project_id = _current_project_identity()
    try:
        catalog, project = _project_summary(project_id)
        previous_selected_project_id = str(
            catalog.get("last_selected_project_id") or current_project_id
        ).strip()
        legacy_project_ids = _project_activation_legacy_ids(flow)
        selection = select_project(
            data_root=PROJECTS_DATA_ROOT,
            project_id=project_id,
            current_project_id=current_project_id,
            legacy_project_ids=legacy_project_ids,
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
        )
        root_path = str(
            project.get("technical_details", {}).get("project_path")
            or ""
        ).strip()
        try:
            activation = _activate_runtime_project(
                root_dir=root_path,
                project_id=project_id,
                storage_kind=str(project.get("storage_kind") or "managed"),
            )
        except Exception:
            _restore_catalog_selection_after_failed_activation(
                project_id=previous_selected_project_id,
                current_project_id=current_project_id,
                legacy_project_ids=legacy_project_ids,
                expected_catalog_fingerprint=selection[
                    "catalog_fingerprint"
                ],
            )
            raise
        return {
            **selection,
            "activation_state": "current",
            "native_destination": (
                project.get("current_recommended_stage") or "script"
            ),
            "activation": {
                **activation,
                "native_destination": (
                    project.get("current_recommended_stage") or "script"
                ),
            },
            "catalog_fingerprint": selection.get("catalog_fingerprint")
            or catalog.get("catalog_fingerprint"),
        }
    except ProjectCatalogError as exc:
        _raise_project_catalog_http_error(exc)


@app.post("/api/projects/{project_id}/duplicate")
async def duplicate_project_route(
    project_id: str,
    request: ProjectDuplicateRequest,
):
    flow, current_project_id = _current_project_identity()
    try:
        if project_id == current_project_id:
            return duplicate_project(
                data_root=PROJECTS_DATA_ROOT,
                project_id=project_id,
                new_name=request.name,
                expected_catalog_fingerprint=request.expected_catalog_fingerprint,
                source_project_root=ROOT_DIR,
                source_flow_summary=flow,
            )
        return duplicate_project(
            data_root=PROJECTS_DATA_ROOT,
            project_id=project_id,
            new_name=request.name,
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
        )
    except ProjectCatalogError as exc:
        _raise_project_catalog_http_error(exc)


@app.post("/api/projects/{project_id}/archive")
async def archive_project_route(
    project_id: str,
    request: ProjectArchiveRequest,
):
    _, current_project_id = _current_project_identity()
    try:
        return set_project_archived(
            data_root=PROJECTS_DATA_ROOT,
            project_id=project_id,
            archived=request.archived,
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
            expected_project_fingerprint=request.expected_project_fingerprint,
            current_project_id=current_project_id,
        )
    except ProjectCatalogError as exc:
        _raise_project_catalog_http_error(exc)


@app.get("/api/projects/{project_id}/delete-impact")
async def get_project_delete_impact(project_id: str):
    _, current_project_id = _current_project_identity()
    try:
        return project_delete_impact(
            data_root=PROJECTS_DATA_ROOT,
            project_id=project_id,
            current_project_id=current_project_id,
        )
    except ProjectCatalogError as exc:
        _raise_project_catalog_http_error(exc)


@app.post("/api/projects/{project_id}/delete")
async def delete_project_route(
    project_id: str,
    request: ProjectDeleteRequest,
):
    _, current_project_id = _current_project_identity()
    try:
        return delete_project_to_trash(
            data_root=PROJECTS_DATA_ROOT,
            project_id=project_id,
            confirm_project_id=request.confirm_project_id,
            expected_catalog_fingerprint=request.expected_catalog_fingerprint,
            expected_project_fingerprint=request.expected_project_fingerprint,
            current_project_id=current_project_id,
            confirm_dependencies=request.confirm_dependencies,
        )
    except ProjectCatalogError as exc:
        _raise_project_catalog_http_error(exc)


@app.get("/api/recovery/status")
async def get_recovery_status():
    return _current_recovery_status()


def _advertised_recovery_action(
    recovery_status: dict,
    *,
    stage_id: str,
    action_kind: str,
) -> tuple[dict, dict]:
    stage = next(
        (
            item
            for item in recovery_status.get("stages", [])
            if item.get("id") == stage_id
        ),
        None,
    )
    if stage is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "recovery_stage_unknown",
                "message": f"Unknown recovery stage: {stage_id}",
            },
        )
    advertised = [
        item
        for item in (
            stage.get("primary_action"),
            stage.get("discard_action"),
        )
        if isinstance(item, dict)
    ]
    selected = next(
        (
            item
            for item in advertised
            if item.get("kind") == action_kind
        ),
        None,
    )
    if selected is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "recovery_action_stale",
                "message": (
                    f"{action_kind} is not available while {stage_id} is "
                    f"{stage.get('state')}. Refresh recovery status."
                ),
                "stage_id": stage_id,
                "stage_state": stage.get("state"),
            },
        )
    return stage, selected


@app.post("/api/recovery/action")
async def run_recovery_action(
    request: RecoveryActionRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    recovery_status = _current_recovery_status()
    stage, selected = _advertised_recovery_action(
        recovery_status,
        stage_id=request.stage_id.strip(),
        action_kind=request.action.strip(),
    )
    kind = selected["kind"]
    payload = selected.get("payload") or {}

    if kind in {
        "start_script",
        "resume_script",
        "retry_script_finalization",
    }:
        result = await generate_script(background_tasks)
    elif kind == "discard_script_checkpoint":
        result = await discard_script_generation_state()
    elif kind in {
        "start_roster",
        "resume_roster",
        "reconcile_roster",
        "finalize_roster",
    }:
        result = await discover_character_roster(
            background_tasks,
            CharacterRosterDiscoverRequest(**payload),
        )
    elif kind == "cancel_roster":
        result = await cancel_character_roster_discovery()
    elif kind == "discard_roster_checkpoint":
        result = await discard_character_roster_progress()
    elif kind in {"resume_visuals", "finalize_visuals"}:
        result = await discover_character_visuals(
            background_tasks,
            CharacterVisualDiscoverRequest(**payload),
        )
    elif kind == "cancel_visuals":
        result = await cancel_character_visuals()
    elif kind == "discard_visual_checkpoint":
        result = await discard_character_visual_progress()
    elif kind in {"start_persona", "restart_persona"}:
        result = await generate_personas(
            background_tasks,
            GeneratePersonasRequest(**payload),
        )
    elif kind == "cancel_persona":
        result = await cancel_persona()
    elif kind == "cancel_dataset":
        result = await dataset_builder_cancel()
    elif kind in {"start_audio", "resume_audio"}:
        audio = _current_audio_recovery_inputs()
        if audio.get("error"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "audio_recovery_invalid",
                    "message": str(audio["error"]),
                },
            )
        pending_indices = [
            index
            for index, chunk in enumerate(audio.get("chunks") or [])
            if not (
                chunk.get("status") == "done"
                and chunk.get("audio_path")
            )
        ]
        if not pending_indices:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "audio_recovery_complete",
                    "message": "No unfinished audio chunks remain.",
                },
            )
        result = await generate_batch_endpoint(
            BatchGenerateRequest(indices=pending_indices),
            background_tasks,
            http_request,
        )
    elif kind == "cancel_audio":
        result = await cancel_audio()
    elif selected.get("tab"):
        return {
            "status": "navigation_required",
            "stage_id": stage["id"],
            "stage_state": stage["state"],
            "action": kind,
            "tab": selected["tab"],
        }
    else:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "recovery_action_unsupported",
                "message": f"Recovery action {kind} is not executable.",
            },
        )

    return {
        "status": "accepted",
        "stage_id": stage["id"],
        "stage_state": stage["state"],
        "action": kind,
        "result": result,
    }


def _external_workflow_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "external_workflow_error")
    details = getattr(exc, "details", None) or {}
    if isinstance(exc, ExternalWorkflowConflictError):
        status_code = 409
    elif code in {
        "candidate_not_found",
        "handoff_not_found",
        "import_operation_not_found",
        "structured_result_not_found",
    }:
        status_code = 404
    else:
        status_code = 400
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": str(exc),
            "details": details,
        },
    )


def _external_stage_transfer_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "external_stage_transfer_error")
    details = getattr(exc, "details", None) or {}
    status_code = (
        409
        if isinstance(
            exc,
            (
                ExternalStageTransferConflictError,
                RosterImportReconciliationConflictError,
            ),
        )
        else 422
    )
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": str(exc),
            "details": details,
        },
    )


def _cast_dossier_package_summary(
    package: dict[str, Any],
) -> dict[str, Any]:
    voices = (package.get("voice_dossiers") or {}).get("voices") or []
    visuals = (package.get("visual_dossiers") or {}).get("characters") or []
    observations = (
        (package.get("visual_observations") or {}).get("observations") or []
    )
    selected = package.get("selected_sections") or {}
    applications = package.get("applications") or {}
    package_complete = (
        package.get("status") == "complete"
        or (
            (
                not selected.get("voice_personas_and_designs")
                or "voice_dossiers" in applications
            )
            and (
                not selected.get("visual_dossiers")
                or "visual_dossiers" in applications
            )
        )
    )
    review_warnings: list[str] = []
    for section in (
        package,
        package.get("voice_dossiers") or {},
        package.get("visual_observations") or {},
        package.get("visual_dossiers") or {},
    ):
        for warning in section.get("warnings") or []:
            text = str(warning or "").strip()
            if text and text not in review_warnings:
                review_warnings.append(text)
    for index, voice in enumerate(voices):
        prefix = f"Complete Cast voice dossier {index}."
        speaker = str(voice.get("speaker") or f"Voice dossier {index}").strip()
        review_warnings = [
            warning.replace(prefix, f"{speaker} · ")
            for warning in review_warnings
        ]
    activation = {
        "ready": False,
        "completed": package_complete,
        "approved_roster_fingerprint": None,
        "reason": (
            "The selected Complete Cast sections have already been applied to the current project."
            if package_complete
            else "Approve a compatible Character roster before importing the remaining dossier sections."
        ),
    }
    visual_identity_review = {
        "required": False,
        "issues": [],
        "approved_entries": [],
    }
    source_snapshot, source_text, source_error = _current_character_roster_source()
    if package_complete:
        activation["approved_roster_fingerprint"] = package.get(
            "approved_roster_fingerprint"
        )
    elif source_snapshot is None or source_text is None:
        activation["reason"] = source_error or activation["reason"]
    elif package.get("source_fingerprint") != source_snapshot.get("fingerprint"):
        activation["reason"] = (
            "The current source differs from the source used by this Cast dossier."
        )
    else:
        try:
            approved = read_character_roster(
                CHARACTER_ROSTER_PATH,
                source_text=source_text,
                expected_status="approved",
            )
            activation = {
                "ready": True,
                "completed": False,
                "approved_roster_fingerprint": approved[
                    "roster_fingerprint"
                ],
                "reason": None,
            }
            try:
                parent = get_structured_result_candidate(
                    root_dir=ROOT_DIR,
                    candidate_id=str(
                        package.get("parent_candidate_id") or ""
                    ).strip(),
                )
                roster_entities = (
                    ((parent.get("result") or {}).get("roster") or {}).get(
                        "entities"
                    )
                    or []
                )
                visual_identity_review = inspect_visual_identity_review(
                    package=package,
                    roster=approved,
                    roster_entities=roster_entities,
                )
            except ExternalWorkflowValidationError:
                pass
        except (FileNotFoundError, CharacterRosterError) as exc:
            activation["reason"] = str(exc)
    return {
        "parent_candidate_id": package.get("parent_candidate_id"),
        "status": package.get("status"),
        "selected_sections": copy.deepcopy(
            package.get("selected_sections") or {}
        ),
        "components": copy.deepcopy(package.get("components") or {}),
        "summary": {
            "voice_dossier_count": len(voices),
            "visual_dossier_count": len(visuals),
            "visual_observation_count": len(observations),
        },
        "voice_preview": [
            {
                "speaker": voice.get("speaker"),
                "persona_summary": voice.get("persona_summary"),
                "designed_voice_description": voice.get(
                    "designed_voice_description"
                ),
            }
            for voice in voices[:6]
        ],
        "visual_preview": [
            {
                "character_id": visual.get("character_id"),
                "trait_count": sum(
                    len(items or [])
                    for items in (visual.get("profile") or {}).values()
                    if isinstance(items, list)
                ),
                "variant_count": len(visual.get("variants") or []),
                "unknown_count": len(visual.get("unknowns") or []),
            }
            for visual in visuals[:6]
        ],
        "applications": copy.deepcopy(package.get("applications") or {}),
        "review_warnings": review_warnings,
        "repair_warnings": [
            warning
            for warning in review_warnings
            if "Alexandria retained the text" in warning
        ],
        "visual_identity_review": visual_identity_review,
        "activation": activation,
    }


def _roster_import_candidate_payload(candidate: dict) -> dict:
    if candidate.get("status") == "transferred":
        application = candidate.get("application") or {}
        candidate["routing"] = {
            "status": "review_ready",
            "native_destination": "character_roster",
            "tab": application.get("tab") or "characters",
            "message": (
                "This roster import already entered Character roster review."
            ),
        }
        package = package_for_roster_candidate(
            root_dir=ROOT_DIR,
            roster_candidate=candidate,
        )
        if package is not None:
            candidate["cast_dossier_package"] = (
                _cast_dossier_package_summary(package)
            )
        return candidate

    source_snapshot, source_text, _ = _current_character_roster_source()
    if source_snapshot is None or source_text is None:
        raise RosterImportReconciliationConflictError(
            "external_source_required",
            "The selected source is required to review imported roster observations.",
        )
    reconciliation = build_roster_import_reconciliation(
        candidate=candidate,
        source_snapshot=source_snapshot,
        source_text=source_text,
        draft_path=CHARACTER_ROSTER_DRAFT_PATH,
        approved_path=CHARACTER_ROSTER_PATH,
    )
    reconciliation = build_issue_focused_roster_import_reconciliation(
        reconciliation
    )
    candidate["reconciliation"] = reconciliation
    candidate["routing"] = {
        "status": "awaiting_reconciliation",
        "native_destination": "cast",
        "tab": "characters",
        "target_id": "cast:issues",
        "code": "roster_import_reconciliation_required",
        "message": (
            f"{reconciliation['summary']['safe_change_count']} safe change"
            + (
                " was"
                if reconciliation['summary']['safe_change_count'] == 1
                else "s were"
            )
            + " prepared automatically. Review only "
            + str(reconciliation['summary']['issue_count'])
            + " roster issue"
            + (
                "."
                if reconciliation['summary']['issue_count'] == 1
                else "s."
            )
        ),
        "details": copy.deepcopy(reconciliation["summary"]),
    }
    package = package_for_roster_candidate(
        root_dir=ROOT_DIR,
        roster_candidate=candidate,
    )
    if package is not None:
        candidate["cast_dossier_package"] = (
            _cast_dossier_package_summary(package)
        )
    return candidate


def _external_source_context() -> tuple[dict | None, str | None, str | None]:
    snapshot, source_text, error = _current_character_roster_source()
    if snapshot is None or source_text is None:
        return None, None, error
    return (
        {
            "basename": snapshot["basename"],
            "fingerprint": snapshot["fingerprint"],
            "character_count": snapshot["character_count"],
            "chunk_count": 1 if source_text else 0,
        },
        source_text,
        None,
    )


def _external_script_state() -> dict:
    status = _current_script_generation_status()
    checkpoint = status.get("checkpoint") or {}
    raw_checkpoint = checkpoint.get("status") or "none"
    if status.get("process", {}).get("running"):
        checkpoint_status = "running"
    else:
        checkpoint_status = {
            "none": "none",
            "compatible": "resumable",
            "finalization_pending": "finalization_only",
            "incompatible": "incompatible",
            "corrupt": "corrupt",
            "invalid": "invalid",
            "unknown": "unknown",
        }.get(raw_checkpoint, "unknown")
    result = status.get("result") or {}
    script_fingerprint = (
        result.get("script_fingerprint")
        if result.get("script_status") == "valid"
        else None
    )
    if script_fingerprint is None:
        script_value = _external_read_json(SCRIPT_PATH)
        if isinstance(script_value, list):
            script_fingerprint = fingerprint_value(script_value)
    audio = _current_audio_recovery_inputs()
    generated_audio_count = sum(
        1
        for chunk in audio.get("chunks") or []
        if chunk.get("status") == "done" or chunk.get("audio_path")
    )
    return {
        "generation": status,
        "checkpoint_status": checkpoint_status,
        "script_fingerprint": script_fingerprint,
        "generated_audio_count": generated_audio_count,
    }


def _external_import_busy_stage() -> str | None:
    for task_name in (
        "script",
        "render_plan",
        "roster",
        "roster_enrichment",
        "persona",
        "visual",
        "audio",
        "review",
    ):
        if process_state.get(task_name, {}).get("running"):
            return task_name
    return None


def _external_read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_artifact_unreadable",
                "message": f"Could not read {os.path.basename(path)}: {exc}",
            },
        ) from exc


def _external_artifact_fingerprint(path: str) -> str | None:
    value = _external_read_json(path)
    return fingerprint_value(value) if value is not None else None


def _external_current_artifact_fingerprints(
    names: Iterable[str],
) -> dict[str, str]:
    paths = {
        "annotated_script": SCRIPT_PATH,
        "character_roster": CHARACTER_ROSTER_PATH,
        "character_roster_draft": CHARACTER_ROSTER_DRAFT_PATH,
        "roster_discovery_state": CHARACTER_ROSTER_STATE_PATH,
        "visual_discovery_state": PERSONA_VISUAL_STATE_PATH,
        "voice_config": VOICE_CONFIG_PATH,
        "chunks": CHUNKS_PATH,
    }
    result: dict[str, str] = {}
    for name in names:
        path = paths.get(name)
        if path is None:
            continue
        if name == "chunks":
            value = _external_read_json(path)
            fingerprint = (
                backend_render_plan_chunks_fingerprint(value)
                if isinstance(value, list)
                else None
            )
        else:
            fingerprint = _external_artifact_fingerprint(path)
        if fingerprint is not None:
            result[name] = fingerprint
    return result


def _external_roster(source_text: str | None) -> tuple[dict | None, str | None]:
    for path, expected_status in (
        (CHARACTER_ROSTER_PATH, "approved"),
        (CHARACTER_ROSTER_DRAFT_PATH, "draft"),
    ):
        try:
            return (
                read_character_roster(
                    path,
                    source_text=source_text,
                    expected_status=expected_status,
                ),
                path,
            )
        except FileNotFoundError:
            continue
        except CharacterRosterError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_roster_invalid",
                    "message": str(exc),
                },
            ) from exc
    return None, None


def _external_find_roster_entry(
    roster: dict | None,
    target: str | None,
) -> dict | None:
    wanted = str(target or "").strip().casefold()
    if not wanted or roster is None:
        return None
    for entry in roster.get("entries") or []:
        labels = {
            str(entry.get(field) or "").strip().casefold()
            for field in (
                "id",
                "entry_id",
                "canonical_name",
                "display_name",
                "speaker_label",
            )
        }
        labels.update(
            str(value).strip().casefold()
            for field in ("aliases", "nicknames", "titles")
            for value in (entry.get(field) or [])
        )
        if wanted in labels:
            return entry
    return None


def _external_script_entries() -> list[dict]:
    entries = _external_read_json(SCRIPT_PATH)
    if not isinstance(entries, list):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_script_required",
                "message": "A valid annotated script is required for this handoff.",
            },
        )
    return entries


def _external_safe_voice_context(
    target: str,
    roster_entry: dict | None,
) -> tuple[dict | None, dict | None]:
    voice_config = _external_read_json(VOICE_CONFIG_PATH)
    if not isinstance(voice_config, dict):
        return None, None
    labels = [target]
    if roster_entry is not None:
        labels.extend(
            str(roster_entry.get(field) or "").strip()
            for field in (
                "canonical_name",
                "display_name",
                "speaker_label",
            )
        )
        labels.extend(str(value).strip() for value in roster_entry.get("aliases") or [])
    config = None
    for label in labels:
        if label and isinstance(voice_config.get(label), dict):
            config = voice_config[label]
            break
    if config is None:
        return None, None
    description = str(
        config.get("description")
        or config.get("character_style")
        or config.get("default_style")
        or ""
    ).strip()
    ref_text = str(config.get("ref_text") or "").strip()
    existing_persona = (
        {"description": description, "ref_text": ref_text}
        if description or ref_text
        else None
    )
    assignment = {
        key: value
        for key, value in {
            "type": config.get("type"),
            "voice": config.get("voice"),
            "alias_of": config.get("alias_of"),
            "description": description or None,
            "character_style": str(config.get("character_style") or "").strip() or None,
        }.items()
        if value not in (None, "")
    }
    return existing_persona, assignment or None


def _external_persona_subject(
    *,
    speaker: str,
    entries: list[dict[str, Any]],
    roster: dict[str, Any],
) -> dict[str, Any]:
    roster_entry = _external_find_roster_entry(roster, speaker)
    if roster_entry is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_persona_roster_entry_required",
                "message": (
                    f"Approved Character roster identity is required for Script "
                    f"speaker {speaker!r}. Resolve the roster before exporting all "
                    "Personas."
                ),
            },
        )
    labels = {speaker.casefold()}
    labels.update(
        str(roster_entry.get(field) or "").strip().casefold()
        for field in (
            "canonical_name",
            "display_name",
            "speaker_label",
        )
    )
    labels.update(
        str(value).strip().casefold()
        for field in ("aliases", "nicknames", "titles")
        for value in (roster_entry.get(field) or [])
    )
    matched = [
        index
        for index, entry in enumerate(entries)
        if str(entry.get("speaker") or "").strip().casefold() in labels
    ]
    sample_lines = [
        str(entries[index].get("text") or "")
        for index in matched[:32]
        if str(entries[index].get("text") or "").strip()
    ]
    if not sample_lines:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_persona_samples_required",
                "message": f"No Script text was found for {speaker!r}.",
            },
        )
    first_index = matched[0]
    narrator_context = " ".join(
        str(entries[index].get("text") or "")
        for index in range(
            max(0, first_index - 3),
            min(len(entries), first_index + 4),
        )
        if entries[index].get("speaker") == "NARRATOR"
    ).strip()
    existing_persona, assignment = _external_safe_voice_context(
        speaker,
        roster_entry,
    )
    subject: dict[str, Any] = {
        "speaker": speaker,
        "sample_lines": sample_lines,
        "narrator_context": narrator_context,
        "roster_entry": roster_entry,
        "evidence": roster_entry.get("evidence") or [],
        "source_locations": [
            item.get("source_location")
            for item in roster_entry.get("evidence") or []
            if item.get("source_location")
        ],
        "unresolved_questions": (
            roster_entry.get("unresolved_questions") or []
        ),
    }
    if existing_persona is not None:
        subject["existing_persona"] = existing_persona
    if assignment is not None:
        subject["current_voice_assignment"] = assignment
        subject["current_voice_mode"] = str(
            assignment.get("type") or ""
        )
    return subject


def _external_cast_dossier_subjects(
    *,
    entries: list[dict[str, Any]],
    roster: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    speakers: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        speaker = str(entry.get("speaker") or "").strip()
        if not speaker or speaker in seen:
            continue
        seen.add(speaker)
        speakers.append(speaker)
    subjects: list[dict[str, Any]] = []
    for speaker in speakers:
        roster_entry = (
            _external_find_roster_entry(roster, speaker)
            if isinstance(roster, dict)
            else None
        )
        labels = {speaker.casefold()}
        if roster_entry is not None:
            labels.update(
                str(roster_entry.get(field) or "").strip().casefold()
                for field in (
                    "canonical_name",
                    "display_name",
                    "speaker_label",
                )
            )
            labels.update(
                str(value).strip().casefold()
                for field in ("aliases", "nicknames", "titles")
                for value in (roster_entry.get(field) or [])
            )
        matched = [
            index
            for index, entry in enumerate(entries)
            if str(entry.get("speaker") or "").strip().casefold() in labels
        ]
        sample_lines = [
            str(entries[index].get("text") or "")
            for index in matched[:32]
            if str(entries[index].get("text") or "").strip()
        ]
        if not sample_lines:
            continue
        first_index = matched[0]
        narrator_context = " ".join(
            str(entries[index].get("text") or "")
            for index in range(
                max(0, first_index - 3),
                min(len(entries), first_index + 4),
            )
            if entries[index].get("speaker") == "NARRATOR"
        ).strip()
        existing_persona, assignment = _external_safe_voice_context(
            speaker,
            roster_entry,
        )
        subject: dict[str, Any] = {
            "speaker": speaker,
            "sample_lines": sample_lines,
            "narrator_context": narrator_context,
        }
        if roster_entry is not None:
            subject["roster_entry"] = roster_entry
            subject["evidence"] = roster_entry.get("evidence") or []
        if existing_persona is not None:
            subject["existing_persona"] = existing_persona
        if assignment is not None:
            subject["current_voice_assignment"] = assignment
        subjects.append(subject)
    return subjects


async def _external_task_export_spec(
    task_type: str,
    target_value: str | None,
    options: dict[str, bool] | None = None,
) -> dict[str, Any]:
    try:
        definition = get_task_definition(task_type)
    except ChatGPTHandoffError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    source_context, source_text, source_error = _external_source_context()
    config = await get_config()
    prompts = config.get("prompts") or {}
    generation = config.get("generation") or {}
    script_state = _external_script_state()
    artifacts: dict[str, str] = {}
    target = None
    supplied_options = dict(options or {})
    if supplied_options and task_type != "complete_cast_dossier":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "external_task_options_unsupported",
                "message": "Task options are supported only by the Complete Cast dossier bundle.",
            },
        )

    if definition.target_kind is not None:
        selected = str(target_value or "").strip()
        if not selected:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "external_target_required",
                    "message": f"Choose a {definition.target_kind} for {definition.label}.",
                },
            )
        target = {"kind": definition.target_kind, "value": selected}

    if task_type == "script_generation":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select a readable source before exporting this task.",
                },
            )
        input_payload = {
            "source_text": source_text,
            "source_context": source_context,
            "generation_constraints": {
                "system_prompt": prompts.get("system_prompt") or "",
                "user_prompt_template": prompts.get("user_prompt") or "",
                "generation": generation,
            },
        }
    elif task_type == "backend_render_plan_generation":
        lifecycle = _current_script_lifecycle_status()
        script_fingerprint = (
            script_state["script_fingerprint"]
            or _external_artifact_fingerprint(SCRIPT_PATH)
        )
        chunks = _external_read_json(CHUNKS_PATH)
        if not script_fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_script_required",
                    "message": "A valid accepted Script is required before creating backend render plans.",
                },
            )
        if (
            not lifecycle.get("accepted")
            or lifecycle.get("fingerprints", {}).get("script") != script_fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "script_not_accepted",
                    "message": "Accept the current canonical Script before exporting its Qwen and Fish delivery-plan task.",
                },
            )
        if (
            not isinstance(chunks, list)
            or not chunks
            or any(not isinstance(chunk, dict) for chunk in chunks)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_chunks_required",
                    "message": "Valid synthesis chunks are required before creating backend render plans.",
                },
            )
        chunks_fingerprint = backend_render_plan_chunks_fingerprint(chunks)
        task_chunks = build_backend_render_plan_task_chunks(chunks)
        if not task_chunks:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_chunks_required",
                    "message": "The current synthesis chunks contain no spoken text.",
                },
            )
        input_payload = {
            "script_fingerprint": script_fingerprint,
            "chunks_fingerprint": chunks_fingerprint,
            "chunks": task_chunks,
            "backend_guidance": backend_render_plan_task_guidance(),
        }
        if source_context is not None:
            input_payload["source_context"] = source_context
        artifacts.update(
            {
                "annotated_script": script_fingerprint,
                "chunks": chunks_fingerprint,
            }
        )
    elif task_type in {
        "script_review",
        "line_direction_generation",
        "line_direction_audit",
    }:
        entries = (
            _reviewable_script_entries()
            if task_type == "script_review"
            else _external_script_entries()
        )
        script_fingerprint = (
            script_state["script_fingerprint"]
            or _external_artifact_fingerprint(SCRIPT_PATH)
        )
        if not script_fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_script_required",
                    "message": "A valid annotated Script is required for this task.",
                },
            )
        input_payload = {
            "entries": entries,
            "context_before": "",
            "context_after": "",
            "source_context": source_context or {},
            "review_constraints": {
                "system_prompt": prompts.get("review_system_prompt") or "",
                "user_prompt_template": prompts.get("review_user_prompt") or "",
            },
        }
        voice_config = _external_read_json(VOICE_CONFIG_PATH)
        if isinstance(voice_config, dict) and voice_config:
            input_payload["current_personas"] = {
                speaker: {
                    key: value
                    for key, value in {
                        "type": record.get("type"),
                        "description": record.get("description"),
                        "character_style": record.get("character_style"),
                    }.items()
                    if value not in (None, "")
                }
                for speaker, record in voice_config.items()
                if isinstance(record, dict)
            }
        artifacts["annotated_script"] = script_fingerprint
        voice_fingerprint = _external_artifact_fingerprint(VOICE_CONFIG_PATH)
        if voice_fingerprint:
            artifacts["voice_config"] = voice_fingerprint
    elif task_type == "complete_cast_dossier":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select a readable source before exporting the Cast dossier.",
                },
            )
        section_keys = {
            "roster_and_relationships",
            "voice_personas_and_designs",
            "visual_dossiers",
        }
        unknown_options = sorted(set(supplied_options) - section_keys)
        if unknown_options:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_cast_dossier_options",
                    "message": "Unknown Complete Cast options: " + ", ".join(unknown_options),
                },
            )
        requested_sections = {
            key: supplied_options.get(key, True)
            for key in sorted(section_keys)
        }
        if any(not isinstance(value, bool) for value in requested_sections.values()):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_cast_dossier_options",
                    "message": "Every Complete Cast option must be true or false.",
                },
            )
        if not any(requested_sections.values()):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "cast_dossier_section_required",
                    "message": "Select at least one Complete Cast dossier section.",
                },
            )
        entries = _external_script_entries()
        script_fingerprint = (
            script_state["script_fingerprint"]
            or _external_artifact_fingerprint(SCRIPT_PATH)
        )
        if not script_fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_script_required",
                    "message": "An accepted Script is required before exporting the Complete Cast dossier.",
                },
            )
        roster, roster_path = _external_roster(source_text)
        subjects = _external_cast_dossier_subjects(
            entries=entries,
            roster=roster,
        )
        if requested_sections["voice_personas_and_designs"] and not subjects:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_persona_speakers_required",
                    "message": "The current Script has no speakers available for Voice dossier generation.",
                },
            )
        input_payload = {
            "requested_sections": requested_sections,
            "source_text": source_text,
            "source_context": source_context,
            "script_speakers": subjects,
        }
        if roster is not None:
            input_payload["existing_roster"] = roster
        voice_config = _external_read_json(VOICE_CONFIG_PATH)
        if isinstance(voice_config, dict) and voice_config:
            input_payload["current_voice_assignments"] = voice_config
        artifacts["annotated_script"] = script_fingerprint
        if roster_path:
            roster_fingerprint = _external_artifact_fingerprint(roster_path)
            if roster_fingerprint:
                artifacts[
                    "character_roster"
                    if roster_path == CHARACTER_ROSTER_PATH
                    else "character_roster_draft"
                ] = roster_fingerprint
        voice_fingerprint = _external_artifact_fingerprint(VOICE_CONFIG_PATH)
        if voice_fingerprint:
            artifacts["voice_config"] = voice_fingerprint
    elif task_type == "roster_discovery":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select a readable source before roster discovery.",
                },
            )
        input_payload = {
            "source_passage": source_text,
            "passage_number": 1,
            "passage_count": 1,
        }
    elif task_type == "roster_reconciliation":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select the source used by roster discovery.",
                },
            )
        try:
            discovery_state = load_roster_discovery_state(
                CHARACTER_ROSTER_STATE_PATH
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_roster_observations_required",
                    "message": f"Validated roster observations are required: {exc}",
                },
            ) from exc
        observations = (
            completed_observations(discovery_state)
            if discovery_state is not None
            else []
        )
        if not observations:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_roster_observations_required",
                    "message": "Roster discovery has no completed observations to reconcile.",
                },
            )
        roster, roster_path = _external_roster(source_text)
        input_payload = {
            "observations": observations,
            "source_summary": source_context,
        }
        if roster is not None:
            input_payload["existing_roster"] = roster
        state_fingerprint = _external_artifact_fingerprint(
            CHARACTER_ROSTER_STATE_PATH
        )
        if state_fingerprint:
            artifacts["roster_discovery_state"] = state_fingerprint
        if roster_path:
            fingerprint = _external_artifact_fingerprint(roster_path)
            if fingerprint:
                artifacts[
                    "character_roster"
                    if roster_path == CHARACTER_ROSTER_PATH
                    else "character_roster_draft"
                ] = fingerprint
    elif task_type == "persona_catalog_generation":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select the source used by the Script and Character roster.",
                },
            )
        entries = _external_script_entries()
        try:
            roster = read_character_roster(
                CHARACTER_ROSTER_PATH,
                source_text=source_text,
                expected_status="approved",
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_approved_roster_required",
                    "message": "Approve the Character roster before creating all Personas.",
                },
            ) from exc
        except CharacterRosterError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_roster_invalid",
                    "message": str(exc),
                },
            ) from exc
        speaker_labels: list[str] = []
        seen_speakers: set[str] = set()
        for entry in entries:
            speaker = str(entry.get("speaker") or "").strip()
            if not speaker or speaker in seen_speakers:
                continue
            seen_speakers.add(speaker)
            speaker_labels.append(speaker)
        if not speaker_labels:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_persona_speakers_required",
                    "message": "The current Script has no speakers to process.",
                },
            )
        input_payload = {
            "speakers": [
                _external_persona_subject(
                    speaker=speaker,
                    entries=entries,
                    roster=roster,
                )
                for speaker in speaker_labels
            ],
            "source_context": source_context,
        }
        script_fingerprint = (
            script_state["script_fingerprint"]
            or _external_artifact_fingerprint(SCRIPT_PATH)
        )
        if script_fingerprint:
            artifacts["annotated_script"] = script_fingerprint
        roster_fingerprint = _external_artifact_fingerprint(
            CHARACTER_ROSTER_PATH
        )
        if roster_fingerprint:
            artifacts["character_roster"] = roster_fingerprint
        voice_fingerprint = _external_artifact_fingerprint(VOICE_CONFIG_PATH)
        if voice_fingerprint:
            artifacts["voice_config"] = voice_fingerprint
    elif task_type in {
        "persona_generation",
        "persona_refinement",
        "persona_reconciliation",
        "persona_audit",
        "persistent_voice_description_generation",
        "persistent_voice_description_refinement",
        "persistent_voice_description_audit",
    }:
        assert target is not None
        selected = target["value"]
        entries = _external_script_entries()
        roster, roster_path = _external_roster(source_text)
        if roster is None or roster_path != CHARACTER_ROSTER_PATH:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_approved_roster_required",
                    "message": (
                        "Approve the Character roster before creating or "
                        "repairing Voice profiles."
                    ),
                },
            )
        roster_entry = _external_find_roster_entry(roster, selected)
        if (
            roster_entry is None
            or roster_entry.get("resolution_status") != "resolved"
            or roster_entry.get("speaking_status")
            not in {"speaker", "narrator"}
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_persona_roster_entry_required",
                    "message": (
                        f"Resolve and approve speaking identity {selected!r} "
                        "in Character roster before exporting this task."
                    ),
                },
            )
        labels = {selected.casefold()}
        labels.update(
            str(roster_entry.get(field) or "").strip().casefold()
            for field in (
                "canonical_name",
                "display_name",
                "speaker_label",
            )
        )
        labels.update(
            str(value).strip().casefold()
            for value in roster_entry.get("aliases") or []
        )
        matched = [
            index
            for index, entry in enumerate(entries)
            if str(entry.get("speaker") or "").strip().casefold() in labels
        ]
        sample_lines = [
            entries[index]["text"]
            for index in matched[:32]
            if isinstance(entries[index].get("text"), str)
        ]
        if not sample_lines:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_persona_samples_required",
                    "message": f"No Script dialogue was found for {selected!r}.",
                },
            )
        first_index = matched[0]
        narrator_context = " ".join(
            str(entries[index].get("text") or "")
            for index in range(
                max(0, first_index - 3),
                min(len(entries), first_index + 4),
            )
            if entries[index].get("speaker") == "NARRATOR"
        ).strip()
        existing_persona, assignment = _external_safe_voice_context(
            selected,
            roster_entry,
        )
        input_payload = {
            "speaker": selected,
            "sample_lines": sample_lines,
            "narrator_context": narrator_context,
            "advanced": True,
        }
        input_payload["roster_entry"] = roster_entry
        input_payload["evidence"] = roster_entry.get("evidence") or []
        input_payload["source_locations"] = [
            item.get("source_location")
            for item in roster_entry.get("evidence") or []
            if item.get("source_location")
        ]
        input_payload["unresolved_questions"] = (
            roster_entry.get("unresolved_questions") or []
        )
        if existing_persona is not None:
            input_payload["existing_persona"] = existing_persona
        if task_type in {
            "persona_refinement",
            "persona_audit",
            "persistent_voice_description_refinement",
            "persistent_voice_description_audit",
        } and existing_persona is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_existing_persona_required",
                    "message": f"{definition.label} requires an existing Persona or voice description.",
                },
            )
        if assignment is not None:
            input_payload["current_voice_assignment"] = assignment
            input_payload["current_voice_mode"] = str(
                assignment.get("type") or ""
            )
        script_fingerprint = (
            script_state["script_fingerprint"]
            or _external_artifact_fingerprint(SCRIPT_PATH)
        )
        if script_fingerprint:
            artifacts["annotated_script"] = script_fingerprint
        if roster_path:
            fingerprint = _external_artifact_fingerprint(roster_path)
            if fingerprint:
                artifacts[
                    "character_roster"
                    if roster_path == CHARACTER_ROSTER_PATH
                    else "character_roster_draft"
                ] = fingerprint
        voice_fingerprint = _external_artifact_fingerprint(VOICE_CONFIG_PATH)
        if voice_fingerprint:
            artifacts["voice_config"] = voice_fingerprint
    elif task_type == "visual_discovery":
        assert target is not None
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select the source used by the visual dossier.",
                },
            )
        roster, roster_path = _external_roster(source_text)
        roster_entry = _external_find_roster_entry(roster, target["value"])
        if roster_entry is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_visual_roster_entry_required",
                    "message": f"No valid roster entry matched {target['value']!r}.",
                },
            )
        input_payload = {
            "roster_entry": roster_entry,
            "source_passage": source_text,
            "passage_number": 1,
            "passage_count": 1,
        }
        if roster_path:
            fingerprint = _external_artifact_fingerprint(roster_path)
            if fingerprint:
                artifacts[
                    "character_roster"
                    if roster_path == CHARACTER_ROSTER_PATH
                    else "character_roster_draft"
                ] = fingerprint
    elif task_type == "visual_reconciliation":
        roster, roster_path = _external_roster(source_text)
        if roster is None or roster_path != CHARACTER_ROSTER_PATH:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_approved_roster_required",
                    "message": "An approved Character roster is required to compile visual dossiers.",
                },
            )
        try:
            visual_state = load_visual_discovery_state(
                PERSONA_VISUAL_STATE_PATH
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_visual_observations_required",
                    "message": f"Validated visual observations are required: {exc}",
                },
            ) from exc
        observations = (
            completed_visual_observations(visual_state)
            if visual_state is not None
            else []
        )
        if not observations:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_visual_observations_required",
                    "message": "Visual discovery has no completed observations to compile.",
                },
            )
        input_payload = {
            "observations": observations,
            "approved_roster": roster,
            "source_summary": source_context or {},
        }
        roster_fingerprint = _external_artifact_fingerprint(
            CHARACTER_ROSTER_PATH
        )
        visual_fingerprint = _external_artifact_fingerprint(
            PERSONA_VISUAL_STATE_PATH
        )
        if roster_fingerprint:
            artifacts["character_roster"] = roster_fingerprint
        if visual_fingerprint:
            artifacts["visual_discovery_state"] = visual_fingerprint
    else:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_task",
                "message": f"No input builder is available for {task_type!r}.",
            },
        )

    return {
        "definition": definition,
        "input_payload": input_payload,
        "source_context": source_context,
        "source_text": source_text,
        "artifact_fingerprints": artifacts,
        "target": target,
    }


async def _store_external_workflow_upload(
    file: UploadFile,
    *,
    allowed_suffixes: set[str],
    max_bytes: int,
) -> str:
    original_name = str(file.filename or "").strip()
    basename = Path(original_name).name
    suffix = Path(basename).suffix.casefold()
    if not basename or basename != original_name or suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_external_upload",
                "message": f"Upload must be a confined {allowed} file.",
            },
        )
    os.makedirs(EXTERNAL_WORKFLOW_UPLOAD_DIR, exist_ok=True)
    target = os.path.join(
        EXTERNAL_WORKFLOW_UPLOAD_DIR,
        f"upload_{secrets.token_hex(12)}{suffix}",
    )
    size = 0
    try:
        async with aiofiles.open(target, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "external_upload_too_large",
                            "message": "The uploaded workflow file exceeds the supported size limit.",
                        },
                    )
                await handle.write(chunk)
        if size <= 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "external_upload_empty",
                    "message": "The uploaded workflow file is empty.",
                },
            )
        return target
    except Exception:
        try:
            os.remove(target)
        except OSError:
            pass
        raise


@app.get("/api/tasks/registry")
async def get_task_bundle_registry():
    return {
        "schema_version": 2,
        "tasks": list_task_definitions(),
    }


@app.get("/api/tasks/library")
async def get_task_bundle_library(
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    source_context, _, _ = _external_source_context()
    artifacts = _external_current_artifact_fingerprints(
        {
            "annotated_script",
            "character_roster",
            "character_roster_draft",
            "roster_discovery_state",
            "visual_discovery_state",
            "voice_config",
            "chunks",
        }
    )
    try:
        tasks = list_task_library(
            root_dir=ROOT_DIR,
            current_source_fingerprint=(
                source_context["fingerprint"]
                if source_context is not None
                else None
            ),
            current_artifact_fingerprints=artifacts,
            status=status,
            query=q,
        )
    except ExternalWorkflowValidationError as exc:
        raise _external_workflow_error(exc) from exc
    return {
        "schema_version": 2,
        "tasks": tasks,
        "counts": {
            state: sum(item["status"] == state for item in tasks)
            for state in (
                "awaiting_import",
                "imported",
                "stale",
                "failed",
                "transferred",
            )
        },
    }


@app.post("/api/tasks/export")
async def export_task_bundle(request: TaskBundleExportRequest):
    spec = await _external_task_export_spec(
        request.task_type,
        request.target,
        request.options,
    )
    source_context = spec["source_context"]
    try:
        task = create_stored_task_bundle(
            root_dir=ROOT_DIR,
            task_type=request.task_type,
            input_payload=spec["input_payload"],
            application_version=ALEXANDRIA_APPLICATION_VERSION,
            source_fingerprint=(
                source_context["fingerprint"]
                if source_context is not None
                else None
            ),
            artifact_fingerprints=spec["artifact_fingerprints"],
            target=spec["target"],
        )
    except (
        ExternalWorkflowValidationError,
        ExternalWorkflowConflictError,
        ChatGPTHandoffError,
    ) as exc:
        raise _external_workflow_error(exc) from exc
    task["download_url"] = f"/api/tasks/{task['task_id']}/download"
    return task


@app.get("/api/tasks/{task_id}/download")
async def download_task_bundle(task_id: str):
    try:
        path, record = get_task_bundle_path(
            root_dir=ROOT_DIR,
            task_id=task_id,
        )
    except (
        ExternalWorkflowValidationError,
        ExternalWorkflowConflictError,
    ) as exc:
        raise _external_workflow_error(exc) from exc
    safe_type = re.sub(r"[^a-z0-9_-]+", "-", record["task_type"])
    return FileResponse(
        path,
        filename=f"alexandria-{safe_type}.alexandria-task.zip",
        media_type="application/zip",
    )


@app.post("/api/tasks/import")
async def import_completed_task(
    file: UploadFile = File(...),
    original_task: Optional[UploadFile] = File(None),
):
    completed_path = await _store_external_workflow_upload(
        file,
        allowed_suffixes={".json", ".zip"},
        max_bytes=EXTERNAL_IMPORT_MAX_BYTES,
    )
    original_path = None
    if original_task is not None and original_task.filename:
        original_path = await _store_external_workflow_upload(
            original_task,
            allowed_suffixes={".zip"},
            max_bytes=EXTERNAL_IMPORT_MAX_BYTES,
        )
    try:
        source_context, source_text, _ = _external_source_context()
        script_state = _external_script_state()
        artifacts = _external_current_artifact_fingerprints(
            {
                "annotated_script",
                "character_roster",
                "character_roster_draft",
                "roster_discovery_state",
                "visual_discovery_state",
                "voice_config",
                "chunks",
            }
        )
        candidate = inspect_completed_task_upload(
            root_dir=ROOT_DIR,
            completed_path=completed_path,
            original_task_path=original_path,
            current_source_fingerprint=(
                source_context["fingerprint"]
                if source_context is not None
                else None
            ),
            current_artifact_fingerprints=artifacts,
            source_text=source_text,
            source_context=source_context,
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            generated_audio_count=script_state["generated_audio_count"],
        )
    except (
        ExternalWorkflowValidationError,
        ExternalWorkflowConflictError,
    ) as exc:
        raise _external_workflow_error(exc) from exc
    finally:
        for path in (completed_path, original_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass

    if candidate["kind"] == "annotated_script":
        destination = (
            candidate.get("origin") or {}
        ).get("native_destination") or "script_review"
        candidate["routing"] = {
            "status": "review_ready",
            "native_destination": destination,
            "tab": "editor" if destination == "editor" else "script",
            "message": (
                "The completed task is ready in the existing Script review. "
                "Nothing has been applied."
            ),
        }
        return candidate

    if candidate.get("task_type") == "complete_cast_dossier":
        try:
            split = split_complete_cast_dossier_candidate(
                root_dir=ROOT_DIR,
                parent=candidate,
            )
            roster_candidate = split.get("roster_candidate")
            package = split["package"]
            if roster_candidate is not None:
                payload = _roster_import_candidate_payload(roster_candidate)
                payload["cast_dossier_package"] = (
                    _cast_dossier_package_summary(package)
                )
                return payload
            parent = split["parent"]
            parent["cast_dossier_package"] = (
                _cast_dossier_package_summary(package)
            )
            parent["routing"] = {
                "status": "ready_for_activation",
                "native_destination": "cast_dossier_review",
                "tab": "characters",
                "message": (
                    "The Complete Cast dossier is validated. Its selected Voice "
                    "and visual sections can enter native review against the current "
                    "approved roster."
                ),
            }
            return parent
        except CastDossierPackageError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.as_detail(),
            ) from exc
        except (
            ExternalWorkflowValidationError,
            ExternalWorkflowConflictError,
            RosterImportReconciliationConflictError,
            RosterImportReconciliationValidationError,
        ) as exc:
            raise _external_stage_transfer_error(exc) from exc

    if candidate.get("task_type") == "roster_discovery":
        try:
            return _roster_import_candidate_payload(candidate)
        except (
            RosterImportReconciliationConflictError,
            RosterImportReconciliationValidationError,
        ) as exc:
            raise _external_stage_transfer_error(exc) from exc

    transfer = candidate.get("native_transfer") or {}
    if candidate.get("status") == "transferred":
        application = candidate.get("application") or {}
        candidate["routing"] = {
            "status": "review_ready",
            "native_destination": application.get("destination"),
            "tab": application.get("tab"),
            "message": (
                "This completed task already entered its native Alexandria "
                "review workflow."
            ),
        }
        return candidate
    if not transfer.get("supported"):
        candidate["routing"] = {
            "status": "unsupported",
            "native_destination": transfer.get("destination"),
            "tab": transfer.get("tab"),
            "message": transfer.get("label") or "No native review is available.",
        }
        return candidate
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        candidate["routing"] = {
            "status": "blocked",
            "native_destination": transfer.get("destination"),
            "tab": transfer.get("tab"),
            "code": "external_transfer_busy",
            "message": f"Stop the active {busy_stage} process, then open this saved review.",
        }
        return candidate
    source_snapshot, current_source_text, _ = _current_character_roster_source()
    try:
        transferred = transfer_structured_result_candidate(
            root_dir=ROOT_DIR,
            candidate_id=candidate["candidate_id"],
            expected_result_fingerprint=candidate["result_fingerprint"],
            source_snapshot=source_snapshot,
            source_text=current_source_text,
            roster_state_path=CHARACTER_ROSTER_STATE_PATH,
            roster_draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            approved_roster_path=CHARACTER_ROSTER_PATH,
            voice_training_projects_root=VOICE_TRAINING_PROJECTS_DIR,
            visual_state_path=PERSONA_VISUAL_STATE_PATH,
            replace_persona_draft=False,
            persona_catalog_decision=False,
            replace_persona_speakers=set(),
        )
        transferred = _with_backend_render_plan_follow_on(transferred)
        application = transferred.get("application") or {}
        is_delivery_plan = (
            transferred.get("task_type") == "backend_render_plan_generation"
        )
        transferred["routing"] = {
            "status": "review_ready",
            "native_destination": application.get("destination"),
            "tab": application.get("tab"),
            "message": (
                "The Qwen and Fish delivery plan was validated and saved to "
                "the accepted Script. Existing audio was not regenerated."
                if is_delivery_plan
                else (
                    "Alexandria opened the completed task in its native review "
                    "workflow. Nothing has been approved automatically."
                )
            ),
        }
        return transferred
    except (
        ExternalStageTransferConflictError,
        ExternalStageTransferValidationError,
    ) as exc:
        candidate["routing"] = {
            "status": "awaiting_reconciliation",
            "native_destination": transfer.get("destination"),
            "tab": transfer.get("tab"),
            "code": exc.code,
            "message": str(exc),
            "details": copy.deepcopy(exc.details),
        }
        return candidate


@app.post("/api/external/handoff/export")
async def export_chatgpt_handoff(request: ChatGPTHandoffExportRequest):
    source_context, source_text, source_error = _external_source_context()
    config = await get_config()
    prompts = config.get("prompts") or {}
    generation = config.get("generation") or {}
    script_state = _external_script_state()

    artifact_fingerprints: dict[str, str] = {}
    if request.task_type == "script_generation":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select a readable source before exporting a Script handoff.",
                },
            )
        stage_prompt = (
            "Convert input.source_text into a complete Alexandria audiobook script. "
            "Preserve every source word exactly in spoken order, classify all narration "
            "as NARRATOR, keep dialogue under canonical uppercase speaker labels, and "
            "provide a concise performance instruction for every entry. The configured "
            "Alexandria prompts are included in input.generation_constraints."
        )
        input_payload = {
            "source_text": source_text,
            "generation_constraints": {
                "system_prompt": prompts.get("system_prompt") or "",
                "user_prompt_template": prompts.get("user_prompt") or "",
                "generation": generation,
            },
        }
        contract = "script"
    elif request.task_type == "script_review":
        entries = _external_script_entries()
        script_fingerprint = (
            script_state["script_fingerprint"]
            or _external_artifact_fingerprint(SCRIPT_PATH)
        )
        if not script_fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_script_required",
                    "message": "A valid annotated script is required before exporting a review handoff.",
                },
            )
        stage_prompt = (
            "Review the supplied Alexandria script for speaker boundaries and structural "
            "assignment errors. Do not rewrite, omit, add, or reorder source wording. "
            "Return every corrected and unchanged entry. The configured review prompts "
            "are included in input.review_constraints."
        )
        input_payload = {
            "entries": entries,
            "context_before": "",
            "context_after": "",
            "review_constraints": {
                "system_prompt": prompts.get("review_system_prompt") or "",
                "user_prompt_template": prompts.get("review_user_prompt") or "",
            },
        }
        contract = "review"
        artifact_fingerprints["annotated_script"] = script_fingerprint
    elif request.task_type == "roster_discovery":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select a readable source before exporting roster discovery.",
                },
            )
        stage_prompt = (
            "Discover every person, speaking entity, narrator role, and recurring named "
            "non-speaker in input.source_passage. Return only evidence-backed observations "
            "matching Alexandria's roster-discovery schema. Do not reconcile or merge "
            "identities in this pass."
        )
        input_payload = {
            "source_passage": source_text,
            "passage_number": 1,
            "passage_count": 1,
        }
        contract = "roster_discovery"
    elif request.task_type == "roster_reconciliation":
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select the source used by roster discovery.",
                },
            )
        try:
            discovery_state = load_roster_discovery_state(
                CHARACTER_ROSTER_STATE_PATH
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_roster_observations_required",
                    "message": f"Validated roster observations are required before reconciliation export: {exc}",
                },
            ) from exc
        if discovery_state is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_roster_observations_required",
                    "message": "Validated roster observations are required before reconciliation export.",
                },
            )
        observations = completed_observations(discovery_state)
        if not observations:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_roster_observations_required",
                    "message": "Roster discovery has no completed observations to reconcile.",
                },
            )
        roster, roster_path = _external_roster(source_text)
        input_payload = {
            "observations": observations,
            "source_summary": {
                "basename": source_context["basename"],
                "fingerprint": source_context["fingerprint"],
                "character_count": source_context["character_count"],
            },
        }
        if roster is not None:
            input_payload["existing_roster"] = roster
        stage_prompt = (
            "Reconcile every validated observation into Alexandria's canonical roster "
            "records without changing evidence. Preserve unresolved ambiguity explicitly, "
            "include duplicate candidates, and account for every observation ID."
        )
        contract = "roster_reconciliation"
        state_fingerprint = _external_artifact_fingerprint(
            CHARACTER_ROSTER_STATE_PATH
        )
        if state_fingerprint:
            artifact_fingerprints["roster_discovery_state"] = state_fingerprint
        if roster_path:
            roster_fingerprint = _external_artifact_fingerprint(roster_path)
            if roster_fingerprint:
                artifact_fingerprints[
                    "character_roster"
                    if roster_path == CHARACTER_ROSTER_PATH
                    else "character_roster_draft"
                ] = roster_fingerprint
    elif request.task_type == "persona_generation":
        target = str(request.target or "").strip()
        if not target:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "external_target_required",
                    "message": "Choose a speaker before exporting a Persona handoff.",
                },
            )
        entries = _external_script_entries()
        roster, roster_path = _external_roster(source_text)
        if roster is None or roster_path != CHARACTER_ROSTER_PATH:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_approved_roster_required",
                    "message": (
                        "Approve the Character roster before exporting a "
                        "Persona handoff."
                    ),
                },
            )
        roster_entry = _external_find_roster_entry(roster, target)
        if (
            roster_entry is None
            or roster_entry.get("resolution_status") != "resolved"
            or roster_entry.get("speaking_status")
            not in {"speaker", "narrator"}
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_persona_roster_entry_required",
                    "message": (
                        f"Resolve and approve speaking identity {target!r} "
                        "before exporting a Persona handoff."
                    ),
                },
            )
        speaker_labels = {target.casefold()}
        speaker_labels.update(
            str(roster_entry.get(field) or "").strip().casefold()
            for field in (
                "canonical_name",
                "display_name",
                "speaker_label",
            )
        )
        speaker_labels.update(
            str(value).strip().casefold()
            for value in roster_entry.get("aliases") or []
        )
        matched_indices = [
            index
            for index, entry in enumerate(entries)
            if str(entry.get("speaker") or "").strip().casefold()
            in speaker_labels
        ]
        sample_lines = [
            entries[index]["text"]
            for index in matched_indices[:24]
            if isinstance(entries[index].get("text"), str)
        ]
        if not sample_lines:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_persona_samples_required",
                    "message": f"No Script dialogue was found for {target!r}.",
                },
            )
        first_index = matched_indices[0]
        narrator_context = " ".join(
            str(entries[index].get("text") or "")
            for index in range(max(0, first_index - 2), min(len(entries), first_index + 3))
            if entries[index].get("speaker") == "NARRATOR"
        ).strip()
        input_payload = {
            "speaker": target,
            "sample_lines": sample_lines,
            "narrator_context": narrator_context,
            "advanced": True,
        }
        input_payload["roster_entry"] = roster_entry
        stage_prompt = (
            "Create one concise Alexandria voice persona for input.speaker. Ground every "
            "age, accent, timbre, cadence, and delivery claim in the supplied roster "
            "evidence, narrator context, and sample lines. Return only description and "
            "ref_text matching the native Persona schema."
        )
        contract = "persona"
        script_fingerprint = (
            script_state["script_fingerprint"]
            or _external_artifact_fingerprint(SCRIPT_PATH)
        )
        if script_fingerprint:
            artifact_fingerprints["annotated_script"] = script_fingerprint
        if roster_path:
            roster_fingerprint = _external_artifact_fingerprint(roster_path)
            if roster_fingerprint:
                artifact_fingerprints[
                    "character_roster"
                    if roster_path == CHARACTER_ROSTER_PATH
                    else "character_roster_draft"
                ] = roster_fingerprint
    else:
        target = str(request.target or "").strip()
        if not target:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "external_target_required",
                    "message": "Choose a character before exporting visual discovery.",
                },
            )
        if source_text is None or source_context is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select the source used by the visual dossier.",
                },
            )
        roster, roster_path = _external_roster(source_text)
        roster_entry = _external_find_roster_entry(roster, target)
        if roster_entry is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_visual_roster_entry_required",
                    "message": f"No valid roster entry matched {target!r}.",
                },
            )
        input_payload = {
            "roster_entry": roster_entry,
            "source_passage": source_text,
            "passage_number": 1,
            "passage_count": 1,
        }
        stage_prompt = (
            "Extract only source-supported visual facts for the supplied roster entry. "
            "Distinguish stable physical traits, clothing, temporary state, and setting. "
            "Attach exact evidence and uncertainty; do not invent cinematic detail."
        )
        contract = "visual_discovery"
        if roster_path:
            roster_fingerprint = _external_artifact_fingerprint(roster_path)
            if roster_fingerprint:
                artifact_fingerprints[
                    "character_roster"
                    if roster_path == CHARACTER_ROSTER_PATH
                    else "character_roster_draft"
                ] = roster_fingerprint

    try:
        handoff = create_stored_handoff(
            root_dir=ROOT_DIR,
            task_type=request.task_type,
            stage_prompt=stage_prompt,
            input_payload=input_payload,
            output_schema=get_schema(contract),
            application_version=ALEXANDRIA_APPLICATION_VERSION,
            source_fingerprint=(
                source_context["fingerprint"]
                if source_context is not None
                else None
            ),
            artifact_fingerprints=artifact_fingerprints,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError, ChatGPTHandoffError) as exc:
        raise _external_workflow_error(exc) from exc
    handoff["download_url"] = f"/api/external/handoff/{handoff['handoff_id']}/download"
    return handoff


@app.get("/api/external/handoff/{handoff_id}/download")
async def download_chatgpt_handoff(handoff_id: str):
    try:
        path, record = get_handoff_bundle_path(
            root_dir=ROOT_DIR,
            handoff_id=handoff_id,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc
    return FileResponse(
        path,
        filename=f"alexandria-{record['task_type']}-{handoff_id[-8:]}.zip",
        media_type="application/zip",
    )


@app.get("/api/external/handoff/{handoff_id}/prompt")
async def get_chatgpt_handoff_prompt(handoff_id: str):
    try:
        return get_handoff_prompt(
            root_dir=ROOT_DIR,
            handoff_id=handoff_id,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc


@app.post("/api/external/handoff/{handoff_id}/open-folder")
async def open_chatgpt_handoff_folder(handoff_id: str):
    try:
        return open_handoff_folder(
            root_dir=ROOT_DIR,
            handoff_id=handoff_id,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc


@app.post("/api/external/handoff/result")
async def inspect_chatgpt_handoff_result(
    handoff_id: str = Form(...),
    file: UploadFile = File(...),
):
    upload_path = await _store_external_workflow_upload(
        file,
        allowed_suffixes={".json"},
        max_bytes=EXTERNAL_RESULT_MAX_BYTES,
    )
    try:
        source_context, source_text, _ = _external_source_context()
        script_state = _external_script_state()
        _, handoff_record = get_handoff_bundle_path(
            root_dir=ROOT_DIR,
            handoff_id=handoff_id,
        )
        artifact_names = (
            handoff_record.get("manifest", {})
            .get("artifact_fingerprints", {})
            .keys()
        )
        artifacts = _external_current_artifact_fingerprints(
            artifact_names
        )
        result = inspect_stored_handoff_result(
            root_dir=ROOT_DIR,
            handoff_id=handoff_id,
            result_path=upload_path,
            current_source_fingerprint=(
                source_context["fingerprint"]
                if source_context is not None
                else None
            ),
            current_artifact_fingerprints=artifacts,
            source_text=source_text,
            source_context=source_context,
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            generated_audio_count=script_state["generated_audio_count"],
        )
        return result
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc
    finally:
        try:
            os.remove(upload_path)
        except OSError:
            pass


@app.get("/api/external/structured-result/{candidate_id}")
async def get_external_structured_result(candidate_id: str):
    try:
        return get_structured_result_candidate(
            root_dir=ROOT_DIR,
            candidate_id=candidate_id,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc


@app.post("/api/external/structured-result/{candidate_id}/transfer")
async def transfer_external_structured_result(
    candidate_id: str,
    request: StructuredResultTransferRequest,
):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_transfer_busy",
                "message": (
                    f"Stop the active {busy_stage} process before transferring "
                    "this result."
                ),
                "stage": busy_stage,
            },
        )
    try:
        candidate = get_structured_result_candidate(
            root_dir=ROOT_DIR,
            candidate_id=candidate_id,
        )
        if candidate.get("task_type") == "roster_discovery":
            if candidate.get("result_fingerprint") != request.result_fingerprint:
                raise ExternalWorkflowConflictError(
                    "stale_structured_result",
                    "The validated result changed before reconciliation opened.",
                )
            return _roster_import_candidate_payload(candidate)
    except (
        ExternalWorkflowConflictError,
        ExternalWorkflowValidationError,
    ) as exc:
        raise _external_workflow_error(exc) from exc
    except (
        RosterImportReconciliationConflictError,
        RosterImportReconciliationValidationError,
    ) as exc:
        raise _external_stage_transfer_error(exc) from exc

    source_snapshot, source_text, _ = _current_character_roster_source()
    try:
        transferred = transfer_structured_result_candidate(
            root_dir=ROOT_DIR,
            candidate_id=candidate_id,
            expected_result_fingerprint=request.result_fingerprint,
            source_snapshot=source_snapshot,
            source_text=source_text,
            roster_state_path=CHARACTER_ROSTER_STATE_PATH,
            roster_draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            approved_roster_path=CHARACTER_ROSTER_PATH,
            voice_training_projects_root=VOICE_TRAINING_PROJECTS_DIR,
            visual_state_path=PERSONA_VISUAL_STATE_PATH,
            replace_persona_draft=request.replace_persona_draft,
            persona_catalog_decision=request.persona_catalog_decision,
            replace_persona_speakers=set(request.replace_persona_speakers),
        )
        return _with_backend_render_plan_follow_on(transferred)
    except (
        ExternalStageTransferConflictError,
        ExternalStageTransferValidationError,
    ) as exc:
        raise _external_stage_transfer_error(exc) from exc


@app.post("/api/script/repair-legacy-import")
async def repair_legacy_imported_script(request: LegacyScriptRepairRequest):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "legacy_script_repair_busy",
                "message": f"Stop the active {busy_stage} process before repairing the Script.",
                "stage": busy_stage,
            },
        )
    source_path = _selected_script_input_path()
    if not source_path or not os.path.isfile(source_path):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "legacy_script_repair_source_required",
                "message": "The selected raw source is required for legacy Script repair.",
            },
        )
    try:
        raw_source = Path(source_path).read_text(encoding="utf-8")
        start_marker = str(request.start_marker or "").strip() or None
        target_source, _ = normalized_source_for_legacy_repair(
            raw_source,
            start_marker=start_marker,
        )
        current_entries = _external_script_entries()
        repaired_entries, repair_summary = repair_legacy_curly_apostrophe_script(
            raw_source=raw_source,
            entries=current_entries,
            start_marker=start_marker,
        )
    except (OSError, UnicodeDecodeError, LegacyScriptRepairError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "legacy_script_repair_failed",
                "message": str(exc),
            },
        ) from exc

    if start_marker:
        source_text = target_source
        source_context = {
            "basename": Path(source_path).name,
            "fingerprint": fingerprint_text(source_text),
            "character_count": len(source_text),
            "chunk_count": 1 if source_text else 0,
        }
    else:
        source_context, source_text, source_error = _external_source_context()
        if source_context is None or source_text is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "legacy_script_repair_source_required",
                    "message": source_error or "The normalized source is unavailable.",
                },
            )
    script_state = _external_script_state()
    os.makedirs(EXTERNAL_WORKFLOW_UPLOAD_DIR, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="legacy-script-repair-",
        dir=EXTERNAL_WORKFLOW_UPLOAD_DIR,
        encoding="utf-8",
        delete=False,
    )
    repair_path = handle.name
    source_replaced = False
    source_repair_dir: Path | None = None
    try:
        with handle:
            json.dump(repaired_entries, handle, ensure_ascii=False, indent=2)
        inspected = inspect_annotated_script_upload(
            root_dir=ROOT_DIR,
            import_path=repair_path,
            source_text=source_text,
            source_context=source_context,
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            generated_audio_count=script_state["generated_audio_count"],
        )
        if not request.confirm:
            return {
                "status": "inspected",
                "candidate": inspected,
                "repair": repair_summary,
                "source_trim_pending": bool(start_marker),
            }
        if start_marker:
            source_repair_id = "source_repair_" + secrets.token_hex(12)
            source_repair_dir = (
                Path(ROOT_DIR)
                / "external_workflows"
                / "source_repairs"
                / source_repair_id
            )
            source_repair_dir.mkdir(parents=True, exist_ok=False)
            (source_repair_dir / "source.before.txt").write_text(
                raw_source,
                encoding="utf-8",
            )
            source_temp_path = Path(str(source_path) + ".source-repair.tmp")
            source_temp_path.write_text(target_source, encoding="utf-8")
            os.replace(source_temp_path, source_path)
            source_replaced = True
        applied = apply_annotated_script_candidate(
            root_dir=ROOT_DIR,
            candidate_id=inspected["candidate_id"],
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            checkpoint_decision=(
                "keep"
                if inspected.get("consequences", {}).get("checkpoint_decision_required")
                else None
            ),
        )
        if source_repair_dir is not None:
            atomic_json_write(
                {
                    "schema_version": 1,
                    "status": "applied",
                    "source_path": str(source_path),
                    "start_marker": start_marker,
                    "source_fingerprint_before": fingerprint_text(raw_source),
                    "source_fingerprint_after": fingerprint_text(target_source),
                    "candidate_id": inspected["candidate_id"],
                    "operation_id": applied.get("operation", {}).get("operation_id"),
                    "created_at_utc": _utc_now_text(),
                },
                source_repair_dir / "receipt.json",
            )
        return {
            "status": "applied",
            "candidate_id": inspected["candidate_id"],
            "repair": repair_summary,
            "application": applied,
            "source_repair_backup": (
                str(source_repair_dir / "source.before.txt")
                if source_repair_dir is not None
                else None
            ),
        }
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        if source_replaced:
            restore_temp_path = Path(str(source_path) + ".source-repair-restore.tmp")
            restore_temp_path.write_text(raw_source, encoding="utf-8")
            os.replace(restore_temp_path, source_path)
        raise _external_workflow_error(exc) from exc
    except Exception:
        if source_replaced:
            restore_temp_path = Path(str(source_path) + ".source-repair-restore.tmp")
            restore_temp_path.write_text(raw_source, encoding="utf-8")
            os.replace(restore_temp_path, source_path)
        raise
    finally:
        try:
            os.remove(repair_path)
        except OSError:
            pass


@app.post("/api/external/annotated-script/inspect")
async def inspect_annotated_script_file(
    verify_source: bool = Form(True),
    file: UploadFile = File(...),
):
    upload_path = await _store_external_workflow_upload(
        file,
        allowed_suffixes={".json", ".zip"},
        max_bytes=EXTERNAL_IMPORT_MAX_BYTES,
    )
    try:
        source_context, source_text, source_error = _external_source_context()
        if verify_source and source_text is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "external_source_required",
                    "message": source_error or "Select a readable source or disable source verification.",
                },
            )
        script_state = _external_script_state()
        return inspect_annotated_script_upload(
            root_dir=ROOT_DIR,
            import_path=upload_path,
            source_text=source_text if verify_source else None,
            source_context=source_context if verify_source else None,
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            generated_audio_count=script_state["generated_audio_count"],
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc
    finally:
        try:
            os.remove(upload_path)
        except OSError:
            pass


@app.get("/api/external/annotated-script/candidate/{candidate_id}")
async def get_external_annotated_script_candidate(candidate_id: str):
    try:
        return get_annotated_script_candidate(
            root_dir=ROOT_DIR,
            candidate_id=candidate_id,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc


@app.post("/api/external/annotated-script/apply")
async def apply_external_annotated_script(request: AnnotatedScriptApplyRequest):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_import_busy",
                "message": f"Stop the active {busy_stage} process before applying an external script.",
                "stage": busy_stage,
            },
        )
    script_state = _external_script_state()
    try:
        return apply_annotated_script_candidate(
            root_dir=ROOT_DIR,
            candidate_id=request.candidate_id,
            current_script_fingerprint=script_state["script_fingerprint"],
            checkpoint_status=script_state["checkpoint_status"],
            checkpoint_decision=request.checkpoint_decision,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc


@app.post("/api/external/annotated-script/rollback")
async def rollback_external_annotated_script(request: AnnotatedScriptRollbackRequest):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_import_busy",
                "message": f"Stop the active {busy_stage} process before rolling back an import.",
                "stage": busy_stage,
            },
        )
    try:
        return rollback_annotated_script_import(
            root_dir=ROOT_DIR,
            operation_id=request.operation_id,
        )
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise _external_workflow_error(exc) from exc


@app.get("/api/script_generation/status")
async def get_script_generation_status():
    return _current_script_generation_status()


@app.get("/api/backend_render_plan/status")
async def get_backend_render_plan_status():
    status = inspect_backend_render_plan(ROOT_DIR)
    lifecycle = _current_script_lifecycle_status()
    accepted = bool(lifecycle.get("accepted"))
    state = process_state["render_plan"]
    return {
        **status,
        "accepted": accepted,
        "available": bool(status.get("available") and accepted),
        "process": {
            "running": bool(state.get("running")),
            "cancel_requested": bool(state.get("cancel")),
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "last_error": state.get("last_error"),
            "logs": list(state.get("logs") or [])[-200:],
        },
    }


@app.post("/api/backend_render_plan/generate")
async def generate_backend_render_plan_locally():
    lifecycle = _current_script_lifecycle_status()
    if not lifecycle.get("accepted"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "script_not_accepted",
                "message": "Accept the current Script before creating its Qwen and Fish delivery plan.",
            },
        )
    state = process_state["render_plan"]
    if state.get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "backend_render_plan_running",
                "message": "Backend delivery planning is already running.",
            },
        )
    chunks = project_manager.load_chunks()
    if not chunks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "synthesis_chunks_required",
                "message": "The accepted Script has no synthesis chunks to plan.",
            },
        )
    current = inspect_backend_render_plan(ROOT_DIR)
    if current.get("current"):
        return {
            "status": "current",
            "started": False,
            "plan": current,
        }
    started = _start_backend_render_plan_thread()
    if not started:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "backend_render_plan_start_failed",
                "message": "Backend delivery planning could not start.",
            },
        )
    return {
        "status": "started",
        "started": True,
        "chunk_count": len(
            [chunk for chunk in chunks if str(chunk.get("text") or "").strip()]
        ),
    }


@app.post("/api/backend_render_plan/cancel")
async def cancel_backend_render_plan():
    state = process_state["render_plan"]
    if not state.get("running"):
        return {"status": "not_running"}
    state["cancel"] = True
    _append_process_log(
        "render_plan",
        "Backend delivery-plan cancellation requested.",
        level="warning",
    )
    return {"status": "cancelling"}


@app.get("/api/character_roster/import-reconciliation")
async def get_character_roster_import_reconciliation(
    candidate_id: Optional[str] = None,
):
    source_snapshot, source_text, _ = _current_character_roster_source()
    try:
        reconciliation = get_pending_roster_import_reconciliation(
            root_dir=ROOT_DIR,
            source_snapshot=source_snapshot,
            source_text=source_text,
            draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            approved_path=CHARACTER_ROSTER_PATH,
            candidate_id=candidate_id,
        )
    except (
        RosterImportReconciliationConflictError,
        RosterImportReconciliationValidationError,
    ) as exc:
        raise _external_stage_transfer_error(exc) from exc
    return reconciliation or {"schema_version": 1, "status": "none"}


@app.post("/api/character_roster/import-reconciliation/apply")
async def apply_character_roster_import_reconciliation(
    request: RosterImportApplyRequest,
):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_transfer_busy",
                "message": (
                    f"Stop the active {busy_stage} process before applying "
                    "the imported roster reconciliation."
                ),
                "stage": busy_stage,
            },
        )
    source_snapshot, source_text, _ = _current_character_roster_source()
    if source_snapshot is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "external_source_required",
                "message": "The selected source is required to apply roster reconciliation.",
            },
        )
    try:
        result = apply_roster_import_reconciliation(
            root_dir=ROOT_DIR,
            candidate_id=request.candidate_id,
            expected_result_fingerprint=request.result_fingerprint,
            expected_current_kind=request.current_kind,
            expected_current_fingerprint=request.current_fingerprint,
            decisions=[
                decision.model_dump()
                if hasattr(decision, "model_dump")
                else decision.dict()
                for decision in request.decisions
            ],
            source_snapshot=source_snapshot,
            source_text=source_text,
            draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            approved_path=CHARACTER_ROSTER_PATH,
        )
    except (
        RosterImportReconciliationConflictError,
        RosterImportReconciliationValidationError,
        ExternalWorkflowConflictError,
        ExternalWorkflowValidationError,
    ) as exc:
        if isinstance(
            exc,
            (ExternalWorkflowConflictError, ExternalWorkflowValidationError),
        ):
            raise _external_workflow_error(exc) from exc
        raise _external_stage_transfer_error(exc) from exc
    draft_fingerprint = str(
        (result.get("draft") or {}).get("draft_fingerprint") or ""
    ).strip()
    if not draft_fingerprint:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "roster_enrichment_plan_unavailable",
                "message": "The roster draft was created without an enrichment-safe fingerprint.",
            },
        )
    package = None
    try:
        roster_candidate = get_structured_result_candidate(
            root_dir=ROOT_DIR,
            candidate_id=request.candidate_id,
        )
        package = package_for_roster_candidate(
            root_dir=ROOT_DIR,
            roster_candidate=roster_candidate,
        )
    except ExternalWorkflowValidationError:
        package = None
    if package is not None:
        result["cast_dossier_package"] = _cast_dossier_package_summary(
            package
        )
        result["enrichment"] = None
        follow_on = (
            "The ChatGPT-produced Voice and visual dossier sections will remain "
            "held until explicit roster approval."
        )
    else:
        try:
            enrichment = save_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                candidate_id=request.candidate_id,
                draft_fingerprint=draft_fingerprint,
                create_designed_voice_profiles=(
                    request.create_designed_voice_profiles
                ),
                discover_visual_details=request.discover_visual_details,
                created_at_utc=_utc_now_text(),
            )
        except RosterEnrichmentError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.as_detail(),
            ) from exc
        result["enrichment"] = enrichment
        follow_on = (
            "Selected local Voice-profile and visual enrichment will begin only "
            "after explicit roster approval."
        )
    result["routing"] = {
        "status": "review_ready",
        "native_destination": "character_roster",
        "tab": "characters",
        "message": (
            "The reconciliation was applied to a reviewable roster draft. "
            "Relationships and identity details are included. " + follow_on
        ),
    }
    return result


def _current_roster_reconciliation_status(
    candidate_id: Optional[str] = None,
) -> dict:
    source_snapshot, source_text, _ = _current_character_roster_source()
    status = inspect_roster_reconciliation_project(
        root_dir=ROOT_DIR,
        source_snapshot=source_snapshot,
        source_text=source_text,
        draft_path=CHARACTER_ROSTER_DRAFT_PATH,
        approved_path=CHARACTER_ROSTER_PATH,
        history_root=CHARACTER_ROSTER_HISTORY_DIR,
        candidate_id=candidate_id,
    )
    if candidate_id is None and status.get("current", {}).get("working_draft"):
        package_match = package_for_roster_draft(
            root_dir=ROOT_DIR,
            draft_fingerprint=str(
                status.get("current", {}).get("draft_fingerprint") or ""
            ),
        )
        if package_match is not None:
            package, roster_candidate_id = package_match
            status = inspect_roster_reconciliation_project(
                root_dir=ROOT_DIR,
                source_snapshot=source_snapshot,
                source_text=source_text,
                draft_path=CHARACTER_ROSTER_DRAFT_PATH,
                approved_path=CHARACTER_ROSTER_PATH,
                history_root=CHARACTER_ROSTER_HISTORY_DIR,
                candidate_id=roster_candidate_id,
            )
            status["cast_dossier_package"] = (
                _cast_dossier_package_summary(package)
            )
            return status
    if candidate_id is None and not status.get("current", {}).get("working_draft"):
        try:
            approved = read_character_roster(
                CHARACTER_ROSTER_PATH,
                source_text=source_text,
                expected_status="approved",
            )
            package_match = package_for_roster_draft(
                root_dir=ROOT_DIR,
                draft_fingerprint=str(
                    approved.get("approved_draft_fingerprint") or ""
                ),
            )
            if package_match is not None:
                package, _ = package_match
                status["cast_dossier_package"] = (
                    _cast_dossier_package_summary(package)
                )
        except (FileNotFoundError, CharacterRosterError):
            pass
    pending = status.get("pending_import") or {}
    pending_id = str(pending.get("candidate_id") or candidate_id or "").strip()
    if pending_id:
        try:
            roster_candidate = get_structured_result_candidate(
                root_dir=ROOT_DIR,
                candidate_id=pending_id,
            )
            package = package_for_roster_candidate(
                root_dir=ROOT_DIR,
                roster_candidate=roster_candidate,
            )
            if package is not None:
                status["cast_dossier_package"] = (
                    _cast_dossier_package_summary(package)
                )
        except ExternalWorkflowValidationError:
            pass
    return status


def _raise_roster_reconciliation_http_error(exc: Exception):
    if isinstance(exc, ExternalWorkflowError):
        raise _external_workflow_error(exc) from exc
    if isinstance(exc, RosterReconciliationError):
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc
    if isinstance(exc, RosterImportReconciliationConflictError):
        status_code = 409
    else:
        status_code = 422
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": getattr(exc, "code", "roster_reconciliation_error"),
            "message": str(exc),
            "details": copy.deepcopy(getattr(exc, "details", {}) or {}),
        },
    ) from exc


@app.get("/api/character_roster/reconciliation")
async def get_issue_focused_character_roster_reconciliation(
    candidate_id: Optional[str] = None,
):
    try:
        return _current_roster_reconciliation_status(candidate_id)
    except (
        RosterReconciliationError,
        RosterImportReconciliationConflictError,
        RosterImportReconciliationValidationError,
    ) as exc:
        _raise_roster_reconciliation_http_error(exc)


@app.post("/api/character_roster/reconciliation/apply")
async def apply_issue_focused_character_roster_reconciliation(
    request: RosterIssueApplyRequest,
):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_reconciliation_busy",
                "message": (
                    f"Stop the active {busy_stage} process before applying "
                    "the roster issue decisions."
                ),
                "stage": busy_stage,
            },
        )
    source_snapshot, source_text, source_error = (
        _current_character_roster_source()
    )
    if source_snapshot is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_unavailable",
                "message": source_error
                or "A readable selected source is required.",
            },
        )
    try:
        result = apply_issue_focused_roster_import_reconciliation(
            root_dir=ROOT_DIR,
            candidate_id=request.candidate_id,
            expected_result_fingerprint=request.result_fingerprint,
            expected_current_kind=request.current_kind,
            expected_current_fingerprint=request.current_fingerprint,
            issue_decisions=[
                decision.model_dump()
                if hasattr(decision, "model_dump")
                else decision.dict()
                for decision in request.decisions
            ],
            source_snapshot=source_snapshot,
            source_text=source_text,
            draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            approved_path=CHARACTER_ROSTER_PATH,
        )
        reconciliation = _current_roster_reconciliation_status(
            request.candidate_id
        )
    except (
        ExternalWorkflowError,
        RosterReconciliationError,
        RosterImportReconciliationConflictError,
        RosterImportReconciliationValidationError,
    ) as exc:
        _raise_roster_reconciliation_http_error(exc)
    draft_fingerprint = str(
        (result.get("draft") or {}).get("draft_fingerprint")
        or reconciliation.get("current", {}).get("draft_fingerprint")
        or ""
    ).strip()
    if not draft_fingerprint:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "roster_enrichment_plan_unavailable",
                "message": "The roster draft was created without an enrichment-safe fingerprint.",
            },
        )
    package = None
    try:
        roster_candidate = get_structured_result_candidate(
            root_dir=ROOT_DIR,
            candidate_id=request.candidate_id,
        )
        package = package_for_roster_candidate(
            root_dir=ROOT_DIR,
            roster_candidate=roster_candidate,
        )
    except ExternalWorkflowValidationError:
        package = None
    if package is not None:
        enrichment = None
        package_summary = _cast_dossier_package_summary(package)
        follow_on = (
            "The included ChatGPT Voice and visual dossier sections remain held "
            "until explicit roster approval."
        )
    else:
        try:
            enrichment = save_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                candidate_id=request.candidate_id,
                draft_fingerprint=draft_fingerprint,
                create_designed_voice_profiles=(
                    request.create_designed_voice_profiles
                ),
                discover_visual_details=request.discover_visual_details,
                created_at_utc=_utc_now_text(),
            )
        except RosterEnrichmentError as exc:
            raise HTTPException(status_code=422, detail=exc.as_detail()) from exc
        package_summary = None
        follow_on = (
            "Selected local Voice-profile and visual enrichment will start after approval."
        )
    return {
        **result,
        "reconciliation": reconciliation,
        "enrichment": enrichment,
        "cast_dossier_package": package_summary,
        "routing": {
            "status": "review_ready",
            "native_destination": "cast",
            "target_id": "cast:issues",
            "message": (
                "Safe changes and explicit issue decisions were applied to a "
                "reviewable roster draft. Relationships are included now. "
                + follow_on
            ),
        },
    }


@app.post("/api/character_roster/reconciliation/restore-applied")
async def restore_applied_character_roster_reconciliation(
    request: RosterDraftRestoreRequest,
):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_restore_busy",
                "message": (
                    f"Stop the active {busy_stage} process before restoring "
                    "the reviewed roster draft."
                ),
                "stage": busy_stage,
            },
        )
    source_snapshot, source_text, source_error = (
        _current_character_roster_source()
    )
    if source_snapshot is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_unavailable",
                "message": source_error
                or "A readable selected source is required.",
            },
        )
    try:
        restored = restore_transferred_roster_import_draft(
            root_dir=ROOT_DIR,
            candidate_id=request.candidate_id,
            expected_result_fingerprint=request.result_fingerprint,
            expected_draft_fingerprint=request.draft_fingerprint,
            expected_approved_fingerprint=(
                request.expected_approved_fingerprint
            ),
            issue_decisions=[
                decision.model_dump()
                if hasattr(decision, "model_dump")
                else decision.dict()
                for decision in request.decisions
            ],
            source_snapshot=source_snapshot,
            source_text=source_text,
            draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            approved_path=CHARACTER_ROSTER_PATH,
        )
        reconciliation = _current_roster_reconciliation_status()
    except (
        ExternalWorkflowError,
        RosterReconciliationError,
        RosterImportReconciliationConflictError,
        RosterImportReconciliationValidationError,
    ) as exc:
        _raise_roster_reconciliation_http_error(exc)
    return {
        **restored,
        "reconciliation": reconciliation,
        "cast_dossier_package": reconciliation.get(
            "cast_dossier_package"
        ),
    }


@app.post("/api/character_roster/reconciliation/approve")
async def approve_issue_focused_character_roster(
    request: RosterReconciliationApproveRequest,
):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_approval_busy",
                "message": (
                    f"Stop the active {busy_stage} process before approving "
                    "the roster."
                ),
                "stage": busy_stage,
            },
        )
    source_snapshot, source_text, source_error = (
        _current_character_roster_source()
    )
    if source_snapshot is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_unavailable",
                "message": source_error
                or "A readable selected source is required.",
            },
        )
    try:
        reconciliation = _current_roster_reconciliation_status()
    except RosterReconciliationError as exc:
        _raise_roster_reconciliation_http_error(exc)
    approval = reconciliation["approval"]
    if approval.get("draft_fingerprint") != request.draft_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_roster_draft",
                "message": "The roster draft changed before approval.",
                "details": {
                    "current_draft_fingerprint": approval.get(
                        "draft_fingerprint"
                    )
                },
            },
        )
    acknowledged_unresolved = (
        request.action == "approve_with_unresolved"
    )
    allowed = (
        approval.get("can_approve_with_unresolved")
        if acknowledged_unresolved
        else approval.get("can_approve_resolved")
    )
    if not allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": (
                    "roster_unresolved_acknowledgement_required"
                    if approval.get("requires_unresolved_acknowledgement")
                    and not acknowledged_unresolved
                    else "roster_approval_blocked"
                ),
                "message": (
                    "Use the unresolved acknowledgment action to preserve "
                    "the displayed unresolved identities."
                    if approval.get("requires_unresolved_acknowledgement")
                    and not acknowledged_unresolved
                    else "Resolve the remaining roster issues before approval."
                ),
                "details": copy.deepcopy(reconciliation["summary"]),
            },
        )
    try:
        if approval["mode"] == "replacement":
            expected_approved = request.expected_approved_fingerprint
            if not expected_approved:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "approved_roster_fingerprint_required",
                        "message": (
                            "The current approved roster fingerprint is required "
                            "for replacement."
                        ),
                    },
                )
            approved, revision = replace_approved_character_roster_file(
                draft_path=CHARACTER_ROSTER_DRAFT_PATH,
                approved_path=CHARACTER_ROSTER_PATH,
                history_root=CHARACTER_ROSTER_HISTORY_DIR,
                source_text=source_text,
                source_fingerprint=source_snapshot["fingerprint"],
                expected_draft_fingerprint=request.draft_fingerprint,
                expected_approved_fingerprint=expected_approved,
                acknowledged_unresolved=acknowledged_unresolved,
            )
            status = "replaced"
        else:
            approved = approve_character_roster_file(
                draft_path=CHARACTER_ROSTER_DRAFT_PATH,
                approved_path=CHARACTER_ROSTER_PATH,
                source_text=source_text,
                source_fingerprint=source_snapshot["fingerprint"],
                expected_fingerprint=request.draft_fingerprint,
                acknowledged_unresolved=acknowledged_unresolved,
            )
            revision = None
            status = "approved"
        current = _current_roster_reconciliation_status()
    except HTTPException:
        raise
    except (
        CharacterRosterActionError,
        CharacterRosterConflictError,
        CharacterRosterError,
    ) as exc:
        raise HTTPException(
            status_code=(
                409 if isinstance(exc, CharacterRosterConflictError) else 422
            ),
            detail={
                "code": "roster_approval_rejected",
                "message": str(exc),
            },
        ) from exc
    enrichment = None
    try:
        pending_enrichment = load_roster_enrichment_plan(ROOT_DIR)
        if (
            pending_enrichment is not None
            and pending_enrichment.get("draft_fingerprint")
            == request.draft_fingerprint
        ):
            enrichment = update_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                changes={
                    "state": "ready",
                    "approved_roster_fingerprint": approved.get(
                        "roster_fingerprint"
                    ),
                    "steps": {
                        "relationships": {
                            "state": "complete",
                            "required": True,
                        }
                    },
                },
            )
    except RosterEnrichmentError as exc:
        logger.warning("Roster enrichment plan could not be activated: %s", exc)
    return {
        "status": status,
        "approved": approved,
        "revision": revision,
        "reconciliation": current,
        "enrichment": enrichment,
    }


def _roster_enrichment_status_payload() -> dict:
    try:
        plan = load_roster_enrichment_plan(ROOT_DIR)
    except RosterEnrichmentError as exc:
        return {
            "schema_version": 1,
            "status": "invalid",
            "running": False,
            "error": str(exc),
            "plan": None,
        }
    runtime = process_state["roster_enrichment"]
    return {
        "schema_version": 1,
        "status": (
            "absent"
            if plan is None
            else "running"
            if runtime.get("running")
            else plan.get("state")
        ),
        "running": runtime.get("running") is True,
        "stage": runtime.get("stage") or "idle",
        "logs": list(runtime.get("logs") or [])[-200:],
        "error": runtime.get("error"),
        "started_at": runtime.get("started_at"),
        "finished_at": runtime.get("finished_at"),
        "plan": plan,
    }


def _roster_enrichment_visual_command(
    *,
    source_path: str,
    entry_ids: list[str],
    passage_size: int = 12000,
    overlap_chars: int = 1200,
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        "discover_persona_visuals.py",
        source_path,
        "--enabled",
        "--passage-size",
        str(passage_size),
        "--overlap-chars",
        str(overlap_chars),
    ]
    for entry_id in entry_ids:
        command.extend(["--entry-id", entry_id])
    return command


def _run_roster_enrichment(
    *,
    plan: dict,
    source_path: str,
    entry_ids: list[str],
    approved_roster_fingerprint: str,
) -> None:
    state = process_state["roster_enrichment"]
    options = plan.get("options") or {}
    voice_selected = options.get("create_designed_voice_profiles") is True
    visual_selected = options.get("discover_visual_details") is True
    outcomes: list[bool] = []
    state.update(
        {
            "running": True,
            "cancel": False,
            "stage": "starting",
            "started_at": _utc_now_text(),
            "finished_at": None,
            "error": None,
        }
    )
    _reset_process_logs("roster_enrichment")
    try:
        update_roster_enrichment_plan(
            root_dir=ROOT_DIR,
            changes={
                "state": "running",
                "started_at_utc": state["started_at"],
                "approved_roster_fingerprint": approved_roster_fingerprint,
            },
        )
        if voice_selected and not state.get("cancel"):
            state["stage"] = "designed_voice_profiles"
            _append_process_log(
                "roster_enrichment",
                "Creating missing designed Voice profiles for approved speaking identities.",
            )
            update_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                changes={
                    "steps": {
                        "designed_voice_profiles": {"state": "running"}
                    }
                },
            )
            if project_manager.engine is not None:
                project_manager.engine = None
                gc.collect()
            voice_code = run_process(
                [
                    sys.executable,
                    "-u",
                    "generate_personas.py",
                    "--advanced",
                    "--new-only",
                    "--batch-size",
                    "40",
                ],
                "persona",
            )
            voice_ok = voice_code == 0
            outcomes.append(voice_ok)
            update_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                changes={
                    "steps": {
                        "designed_voice_profiles": {
                            "state": "complete" if voice_ok else "failed",
                            "return_code": voice_code,
                        }
                    }
                },
            )
            _append_process_log(
                "roster_enrichment",
                (
                    "Designed Voice profiles completed."
                    if voice_ok
                    else "Designed Voice profile generation failed; visual enrichment may still continue."
                ),
                level="progress" if voice_ok else "error",
            )
        if visual_selected and not state.get("cancel"):
            state["stage"] = "visual_details"
            _append_process_log(
                "roster_enrichment",
                "Collecting source-supported visual dossiers for the approved roster.",
            )
            update_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                changes={
                    "steps": {"visual_details": {"state": "running"}}
                },
            )
            visual_code = run_process(
                _roster_enrichment_visual_command(
                    source_path=source_path,
                    entry_ids=entry_ids,
                ),
                "visual",
            )
            visual_ok = visual_code == 0
            outcomes.append(visual_ok)
            update_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                changes={
                    "steps": {
                        "visual_details": {
                            "state": "complete" if visual_ok else "failed",
                            "return_code": visual_code,
                        }
                    }
                },
            )
            _append_process_log(
                "roster_enrichment",
                (
                    "Visual dossier discovery completed."
                    if visual_ok
                    else "Visual dossier discovery failed."
                ),
                level="progress" if visual_ok else "error",
            )
        if state.get("cancel"):
            final_state = "partial" if any(outcomes) else "failed"
            final_error = "Roster enrichment was canceled."
        elif not outcomes or all(outcomes):
            final_state = "complete"
            final_error = None
        elif any(outcomes):
            final_state = "partial"
            final_error = "One selected enrichment stage failed."
        else:
            final_state = "failed"
            final_error = "Selected enrichment stages failed."
        state["stage"] = final_state
        state["error"] = final_error
        state["finished_at"] = _utc_now_text()
        update_roster_enrichment_plan(
            root_dir=ROOT_DIR,
            changes={
                "state": final_state,
                "finished_at_utc": state["finished_at"],
                "error": final_error,
            },
        )
    except Exception as exc:
        logger.exception("Roster enrichment failed")
        state["stage"] = "failed"
        state["error"] = str(exc)
        state["finished_at"] = _utc_now_text()
        _append_process_log(
            "roster_enrichment",
            f"Roster enrichment failed: {exc}",
            level="error",
        )
        try:
            update_roster_enrichment_plan(
                root_dir=ROOT_DIR,
                changes={
                    "state": "failed",
                    "finished_at_utc": state["finished_at"],
                    "error": str(exc),
                },
            )
        except RosterEnrichmentError:
            pass
    finally:
        state["running"] = False


@app.get("/api/character_roster/enrichment")
async def get_character_roster_enrichment():
    return _roster_enrichment_status_payload()


@app.get("/api/cast-dossier/{parent_candidate_id}")
async def get_complete_cast_dossier_package(parent_candidate_id: str):
    try:
        package = get_cast_dossier_package(
            root_dir=ROOT_DIR,
            parent_candidate_id=parent_candidate_id,
        )
    except CastDossierPackageError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc
    return _cast_dossier_package_summary(package)


@app.post("/api/cast-dossier/{parent_candidate_id}/activate")
async def activate_complete_cast_dossier_package(
    parent_candidate_id: str,
    request: CastDossierActivateRequest,
):
    busy_stage = _external_import_busy_stage()
    if busy_stage is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cast_dossier_stage_busy",
                "message": (
                    f"Stop the active {busy_stage} process before importing the "
                    "remaining Complete Cast dossier sections."
                ),
                "stage": busy_stage,
            },
        )
    source_snapshot, source_text, source_error = (
        _current_character_roster_source()
    )
    if source_snapshot is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cast_dossier_source_unavailable",
                "message": source_error
                or "The selected source is unavailable for Complete Cast import.",
            },
        )
    try:
        return activate_complete_cast_dossier(
            root_dir=ROOT_DIR,
            parent_candidate_id=parent_candidate_id,
            expected_roster_fingerprint=request.expected_roster_fingerprint,
            source_snapshot=source_snapshot,
            source_text=source_text,
            approved_roster_path=CHARACTER_ROSTER_PATH,
            voice_training_projects_root=VOICE_TRAINING_PROJECTS_DIR,
            visual_state_path=PERSONA_VISUAL_STATE_PATH,
            import_voice_dossiers=request.import_voice_dossiers,
            import_visual_dossiers=request.import_visual_dossiers,
            identity_crosswalk=dict(request.identity_crosswalk),
            excluded_visual_identity_keys=set(
                request.excluded_visual_identity_keys
            ),
        )
    except CastDossierPackageError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc


@app.post("/api/character_roster/enrichment/start")
async def start_character_roster_enrichment(
    background_tasks: BackgroundTasks,
    request: RosterEnrichmentStartRequest,
):
    try:
        plan = load_roster_enrichment_plan(ROOT_DIR)
    except RosterEnrichmentError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail()) from exc
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "roster_enrichment_plan_missing",
                "message": "No roster import enrichment plan is waiting to run.",
            },
        )
    if plan.get("plan_fingerprint") != request.expected_plan_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_roster_enrichment_plan",
                "message": "The roster enrichment choices changed. Reload before starting.",
            },
        )
    if plan.get("state") not in {"ready", "partial", "failed"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_enrichment_not_ready",
                "message": "Approve the imported roster draft before starting enrichment.",
            },
        )
    if process_state["roster_enrichment"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_enrichment_running",
                "message": "Roster enrichment is already running.",
            },
        )
    if process_state["persona"].get("running") or process_state["visual"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_enrichment_stage_busy",
                "message": "Stop the active Voice-profile or visual discovery process first.",
            },
        )
    source, _, approved, context_error = _current_approved_visual_context()
    if source is None or approved is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_enrichment_context_unavailable",
                "message": context_error or "Approved roster context is unavailable.",
            },
        )
    approved_fingerprint = str(approved.get("roster_fingerprint") or "")
    if approved_fingerprint != request.expected_roster_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_approved_roster",
                "message": "The approved roster changed before enrichment started.",
            },
        )
    if plan.get("approved_roster_fingerprint") != approved_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_enrichment_roster_mismatch",
                "message": "The enrichment plan belongs to a different approved roster.",
            },
        )
    entry_ids = [str(entry["id"]) for entry in approved.get("entries") or []]
    if plan.get("options", {}).get("discover_visual_details") is True:
        progress = inspect_visual_discovery_state(
            PERSONA_VISUAL_STATE_PATH,
            current_source=source,
            roster_fingerprint=approved_fingerprint,
        )
        if progress["status"] in {
            "invalid",
            "incompatible_source",
            "incompatible_roster",
        }:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "visual_progress_blocked",
                    "message": progress.get("error")
                    or "Discard incompatible visual progress before enrichment.",
                },
            )
        if progress["exists"] and progress["character_ids"] != entry_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "visual_selection_changed",
                    "message": "Saved visual progress belongs to another roster selection.",
                },
            )
    process_state["roster_enrichment"].update(
        {
            "running": True,
            "cancel": False,
            "stage": "queued",
            "started_at": _utc_now_text(),
            "finished_at": None,
            "error": None,
        }
    )
    background_tasks.add_task(
        _run_roster_enrichment,
        plan=plan,
        source_path=str(source["path"]),
        entry_ids=entry_ids,
        approved_roster_fingerprint=approved_fingerprint,
    )
    return {
        "status": "started",
        "relationships_included": True,
        "options": copy.deepcopy(plan.get("options") or {}),
        "entry_count": len(entry_ids),
    }


@app.post("/api/character_roster/enrichment/run-selected")
async def run_selected_character_roster_enrichment(
    background_tasks: BackgroundTasks,
    request: RosterEnrichmentRunSelectedRequest,
):
    if not (
        request.create_designed_voice_profiles
        or request.discover_visual_details
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "roster_enrichment_selection_required",
                "message": (
                    "Select Voice-profile or visual enrichment before "
                    "starting local Cast work."
                ),
            },
        )
    if process_state["roster_enrichment"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_enrichment_running",
                "message": "Roster enrichment is already running.",
            },
        )
    if (
        process_state["persona"].get("running")
        or process_state["visual"].get("running")
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_enrichment_stage_busy",
                "message": (
                    "Stop the active Voice-profile or visual discovery "
                    "process first."
                ),
            },
        )
    _, _, approved, context_error = _current_approved_visual_context()
    if approved is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approved_roster_required",
                "message": context_error
                or "Approve the current roster before starting Cast enrichment.",
            },
        )
    approved_fingerprint = str(approved.get("roster_fingerprint") or "")
    if approved_fingerprint != request.expected_roster_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_approved_roster",
                "message": "The approved roster changed before enrichment started.",
            },
        )
    approved_draft_fingerprint = str(
        approved.get("approved_draft_fingerprint")
        or approved_fingerprint
    )
    try:
        save_roster_enrichment_plan(
            root_dir=ROOT_DIR,
            candidate_id=f"local-approved-roster:{approved_fingerprint}",
            draft_fingerprint=approved_draft_fingerprint,
            create_designed_voice_profiles=(
                request.create_designed_voice_profiles
            ),
            discover_visual_details=request.discover_visual_details,
            created_at_utc=_utc_now_text(),
        )
        plan = update_roster_enrichment_plan(
            root_dir=ROOT_DIR,
            changes={
                "state": "ready",
                "approved_roster_fingerprint": approved_fingerprint,
                "steps": {
                    "relationships": {
                        "state": "complete",
                        "required": True,
                    },
                    "designed_voice_profiles": {
                        "state": (
                            "ready"
                            if request.create_designed_voice_profiles
                            else "not_selected"
                        )
                    },
                    "visual_details": {
                        "state": (
                            "ready"
                            if request.discover_visual_details
                            else "not_selected"
                        )
                    },
                },
            },
        )
    except RosterEnrichmentError as exc:
        raise HTTPException(status_code=422, detail=exc.as_detail()) from exc
    return await start_character_roster_enrichment(
        background_tasks,
        RosterEnrichmentStartRequest(
            expected_plan_fingerprint=str(plan["plan_fingerprint"]),
            expected_roster_fingerprint=approved_fingerprint,
        ),
    )


@app.post("/api/character_roster/enrichment/cancel")
async def cancel_character_roster_enrichment():
    state = process_state["roster_enrichment"]
    if not state.get("running"):
        return {"status": "not_running"}
    state["cancel"] = True
    for key in ("persona", "visual"):
        stage = process_state[key]
        stage["cancel"] = True
        process = stage.get("process")
        if process is not None and process.poll() is None:
            process.terminate()
    _append_process_log(
        "roster_enrichment",
        "[CANCEL] Roster enrichment cancellation requested.",
        level="warning",
    )
    return {"status": "cancelling"}


@app.get("/api/character_roster/status")
async def get_character_roster_status():
    return _current_character_roster_status()


@app.get("/api/character_roster/draft")
async def get_character_roster_draft():
    try:
        return read_character_roster(
            CHARACTER_ROSTER_DRAFT_PATH,
            expected_status="draft",
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="No character roster draft found.",
        ) from exc
    except CharacterRosterError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.get("/api/character_roster")
async def get_character_roster():
    try:
        return read_character_roster(
            CHARACTER_ROSTER_PATH,
            expected_status="approved",
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="No approved character roster found.",
        ) from exc
    except CharacterRosterError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.post("/api/character_roster/draft/action")
async def update_character_roster_draft(
    request: CharacterRosterActionRequest,
):
    if process_state["roster"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_running",
                "message": (
                    "Character roster discovery is still running."
                ),
            },
        )

    if (
        os.path.exists(CHARACTER_ROSTER_PATH)
        and not _has_reviewed_character_roster_replacement_draft()
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_already_approved",
                "message": (
                    "No reviewed replacement draft exists for the current "
                    "approved character roster."
                ),
            },
        )

    source, source_text, source_error = (
        _current_character_roster_source()
    )
    if source is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_unavailable",
                "message": source_error
                or "The selected source is unavailable.",
            },
        )

    try:
        updated = mutate_character_roster_draft_file(
            draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            source_text=source_text,
            source_fingerprint=source["fingerprint"],
            expected_fingerprint=request.draft_fingerprint,
            action=request.action,
            entry_id=request.entry_id,
            other_entry_id=request.other_entry_id,
            value=request.value,
            display_name=request.display_name,
            reason=request.reason,
            preserve_old_as_alias=(
                request.preserve_old_as_alias
            ),
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "draft_missing",
                "message": "No character roster draft found.",
            },
        ) from exc
    except CharacterRosterConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_draft",
                "message": str(exc),
            },
        ) from exc
    except CharacterRosterSourceMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_changed",
                "message": str(exc),
            },
        ) from exc
    except (
        CharacterRosterActionError,
        CharacterRosterValidationError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_roster_action",
                "message": str(exc),
            },
        ) from exc

    return {
        "status": "updated",
        "draft": updated,
    }


@app.post("/api/character_roster/approve")
async def approve_character_roster(
    request: CharacterRosterApproveRequest,
):
    if process_state["roster"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_running",
                "message": (
                    "Character roster discovery is still running."
                ),
            },
        )

    if request.replace_existing and not os.path.exists(
        CHARACTER_ROSTER_PATH
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approved_roster_missing",
                "message": "No approved character roster exists to replace.",
            },
        )

    source, source_text, source_error = (
        _current_character_roster_source()
    )
    if source is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_unavailable",
                "message": source_error
                or "The selected source is unavailable.",
            },
        )

    try:
        revision = None
        if request.replace_existing:
            if not request.expected_approved_fingerprint:
                raise CharacterRosterActionError(
                    "expected_approved_fingerprint is required when replacing "
                    "the approved roster."
                )
            approved, revision = replace_approved_character_roster_file(
                draft_path=CHARACTER_ROSTER_DRAFT_PATH,
                approved_path=CHARACTER_ROSTER_PATH,
                history_root=CHARACTER_ROSTER_HISTORY_DIR,
                source_text=source_text,
                source_fingerprint=source["fingerprint"],
                expected_draft_fingerprint=request.draft_fingerprint,
                expected_approved_fingerprint=(
                    request.expected_approved_fingerprint
                ),
                acknowledged_unresolved=(
                    request.acknowledged_unresolved
                ),
            )
        else:
            approved = approve_character_roster_file(
                draft_path=CHARACTER_ROSTER_DRAFT_PATH,
                approved_path=CHARACTER_ROSTER_PATH,
                source_text=source_text,
                source_fingerprint=source["fingerprint"],
                expected_fingerprint=request.draft_fingerprint,
                acknowledged_unresolved=(
                    request.acknowledged_unresolved
                ),
            )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "draft_missing",
                "message": "No character roster draft found.",
            },
        ) from exc
    except CharacterRosterConflictError as exc:
        code = (
            "stale_approved_roster"
            if request.replace_existing
            else (
                "roster_already_approved"
                if os.path.exists(CHARACTER_ROSTER_PATH)
                else "stale_draft"
            )
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": code,
                "message": str(exc),
            },
        ) from exc
    except CharacterRosterSourceMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_changed",
                "message": str(exc),
            },
        ) from exc
    except (
        CharacterRosterActionError,
        CharacterRosterValidationError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "approval_blocked",
                "message": str(exc),
            },
        ) from exc

    return {
        "status": "replaced" if revision is not None else "approved",
        "roster": approved,
        "revision": revision,
    }


@app.post("/api/character_roster/rollback")
async def rollback_character_roster(
    request: CharacterRosterRollbackRequest,
):
    if process_state["roster"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_running",
                "message": "Character roster discovery is still running.",
            },
        )
    source, source_text, source_error = _current_character_roster_source()
    if source is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_unavailable",
                "message": source_error or "The selected source is unavailable.",
            },
        )
    try:
        approved, revision = rollback_approved_character_roster_file(
            draft_path=CHARACTER_ROSTER_DRAFT_PATH,
            approved_path=CHARACTER_ROSTER_PATH,
            history_root=CHARACTER_ROSTER_HISTORY_DIR,
            revision_id=request.revision_id,
            source_text=source_text,
            source_fingerprint=source["fingerprint"],
            expected_current_fingerprint=(
                request.expected_current_fingerprint
            ),
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "roster_revision_missing",
                "message": "The saved character-roster revision was not found.",
            },
        ) from exc
    except CharacterRosterConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_roster_revision",
                "message": str(exc),
            },
        ) from exc
    except CharacterRosterSourceMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_changed",
                "message": str(exc),
            },
        ) from exc
    except (
        CharacterRosterActionError,
        CharacterRosterValidationError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "roster_rollback_blocked",
                "message": str(exc),
            },
        ) from exc
    return {
        "status": "restored",
        "roster": approved,
        "revision": revision,
    }


@app.post("/api/character_roster/discover")
async def discover_character_roster(
    background_tasks: BackgroundTasks,
    request: CharacterRosterDiscoverRequest = (
        CharacterRosterDiscoverRequest()
    ),
):
    source_path = _selected_script_input_path()

    if not source_path:
        raise HTTPException(
            status_code=400,
            detail="No source file is currently selected.",
        )

    if not os.path.exists(source_path):
        raise HTTPException(
            status_code=400,
            detail="The selected source file does not exist.",
        )

    if process_state["roster"].get("running"):
        raise HTTPException(
            status_code=409,
            detail="Character roster discovery is already running.",
        )

    script_speaker_repair = False
    if os.path.exists(CHARACTER_ROSTER_PATH):
        approved_status = (_current_character_roster_status().get("approved") or {}).get("status")
        script_speaker_repair = bool(
            _managed_import_roster_available()
            and approved_status == "invalid"
            and _replaceable_script_speaker_roster()
        )
        if not script_speaker_repair:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An approved character roster already exists and "
                    "cannot be overwritten by discovery."
                ),
            )

    passage_size = int(request.passage_size)
    overlap_chars = int(request.overlap_chars)

    if passage_size < 200:
        raise HTTPException(
            status_code=400,
            detail="Roster passage_size must be at least 200.",
        )

    if overlap_chars < 0 or overlap_chars >= passage_size:
        raise HTTPException(
            status_code=400,
            detail=(
                "Roster overlap_chars must be non-negative and "
                "smaller than passage_size."
            ),
        )

    if (
        os.path.exists(CHARACTER_ROSTER_DRAFT_PATH)
        and not request.replace_draft
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "A character roster draft already exists. "
                "Review it or explicitly allow draft replacement."
            ),
        )

    if _managed_import_roster_available():
        try:
            clear_roster_discovery_state(CHARACTER_ROSTER_STATE_PATH)
            roster = _bootstrap_imported_script_roster(
                replace_existing_script_speaker=script_speaker_repair,
            )
            lifecycle = _current_script_lifecycle_status()
            handoff = mark_discovery_handoff(
                lifecycle_path=SCRIPT_LIFECYCLE_PATH,
                accepted_version_id=lifecycle["accepted_version_id"],
                status="complete",
                expected_state_fingerprint=lifecycle["state_fingerprint"],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "script_speaker_roster_failed",
                    "message": str(exc),
                },
            ) from exc
        _append_process_log(
            "roster",
            (
                f"Created {len(roster.get('entries') or [])} Cast identities "
                "from the accepted imported Script without LLM discovery."
            ),
        )
        return {
            "status": "complete",
            "method": "script_speakers",
            "character_count": len(roster.get("entries") or []),
            "discovery_handoff": handoff["discovery_handoff"],
            "replace_draft": False,
            "passage_size": passage_size,
            "overlap_chars": overlap_chars,
        }

    process_state["roster"]["cancel"] = False
    command = _roster_discovery_command(
        source_path=source_path,
        passage_size=passage_size,
        overlap_chars=overlap_chars,
        replace_draft=bool(request.replace_draft),
    )

    background_tasks.add_task(
        run_process,
        command,
        "roster",
    )

    return {
        "status": "started",
        "replace_draft": bool(request.replace_draft),
        "passage_size": passage_size,
        "overlap_chars": overlap_chars,
    }


@app.post("/api/character_roster/cancel")
async def cancel_character_roster_discovery():
    roster_state = process_state["roster"]

    if not roster_state.get("running"):
        raise HTTPException(
            status_code=400,
            detail="Character roster discovery is not running.",
        )

    roster_state["cancel"] = True
    _append_process_log(
        "roster",
        "[CANCEL] Character roster discovery cancellation requested",
        level="warning",
    )
    process = roster_state.get("process")

    if process is not None:
        try:
            process.terminate()
        except Exception as exc:
            logger.warning(
                "Failed to terminate character roster discovery "
                f"cleanly: {exc}"
            )

    return {"status": "cancelling"}


@app.post("/api/character_roster/discard-progress")
async def discard_character_roster_progress():
    if process_state["roster"].get("running"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot discard character roster discovery "
                "progress while discovery is running."
            ),
        )

    existed = clear_roster_discovery_state(
        CHARACTER_ROSTER_STATE_PATH
    )

    return {
        "status": "discarded" if existed else "absent"
    }


@app.post("/api/script_generation/discard")
async def discard_script_generation_state():
    if process_state[
        "script"
    ].get("running"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot discard saved progress "
                "while script generation is "
                "running."
            ),
        )

    existed = (
        discard_generation_checkpoint(
            GENERATION_STATE_PATH
        )
    )

    return {
        "status": (
            "discarded"
            if existed
            else "absent"
        )
    }

def _current_approved_visual_context():
    source, source_text, source_error = (
        _current_character_roster_source()
    )

    if source is None or source_text is None:
        return None, None, None, (
            source_error
            or "The selected source is unavailable."
        )

    if not os.path.exists(CHARACTER_ROSTER_PATH):
        return source, source_text, None, (
            "Approve a canonical character roster before collecting "
            "optional visual dossiers."
        )

    try:
        approved = read_character_roster(
            CHARACTER_ROSTER_PATH,
            source_text=source_text,
            expected_status="approved",
        )
    except CharacterRosterError as exc:
        return source, source_text, None, str(exc)

    return source, source_text, approved, None


def _character_visual_targets(
    approved: dict,
) -> dict[str, Path]:
    ownership = [
        {
            "entry_id": entry["id"],
            "character_name": (
                entry["canonical_name"]
                or entry["display_name"]
            ),
        }
        for entry in approved["entries"]
    ]
    return persona_reference_targets(
        persona_refs_dir=PERSONA_REFS_DIR,
        selected_entries=ownership,
        all_entries=ownership,
    )


def _current_character_visual_status():
    process = _current_process_status("visual")
    source, source_text, approved, context_error = (
        _current_approved_visual_context()
    )
    roster_fingerprint = (
        approved.get("roster_fingerprint")
        if isinstance(approved, dict)
        else None
    )
    progress = inspect_visual_discovery_state(
        PERSONA_VISUAL_STATE_PATH,
        current_source=source,
        roster_fingerprint=roster_fingerprint,
    )
    dossier_status = build_visual_status(
        approved_roster=approved,
        persona_refs_dir=PERSONA_REFS_DIR,
        source_text=source_text,
    )
    entries = [
        {
            "entry_id": item["character_id"],
            "canonical_name": item["canonical_name"],
            "display_name": item["display_name"],
            "entity_kind": item["entity_kind"],
            "status": item["status"],
            "observation_count": item["observation_count"],
            "variant_count": item["variant_count"],
            "conflict_count": item["conflict_count"],
            "image_prompt_summary": item.get(
                "image_prompt_summary"
            ),
            "error": item["error"],
        }
        for item in dossier_status["entries"]
    ]
    return {
        "enabled_by_default": False,
        "approved_roster_available": approved is not None,
        "context_error": context_error,
        "source_fingerprint": (
            source.get("fingerprint")
            if isinstance(source, dict)
            else None
        ),
        "roster_fingerprint": roster_fingerprint,
        "process": process,
        "progress": progress,
        "complete_count": dossier_status["complete_count"],
        "absent_count": dossier_status["absent_count"],
        "invalid_count": dossier_status["invalid_count"],
        "entries": entries,
    }


def _raise_voice_training_http_error(
    exc: VoiceTrainingApiError,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_expressive_reference_bank_http_error(
    exc: ExpressiveReferenceBankApiError,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_speaker_management_http_error(
    exc: SpeakerManagementApiError,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_llm_profiles_http_error(
    exc: LLMProfilesApiError,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_migration_http_error(
    exc: MigrationApiError,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_training_sidecar_http_error(
    exc: TrainingSidecarApiError,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_controlled_clone_preview_http_error(
    exc: ControlledClonePreviewError,
) -> None:
    if isinstance(exc, ControlledClonePreviewValidationError):
        status_code = 422
        code = "controlled_clone_preview_rejected"
    elif isinstance(exc, ControlledClonePreviewUnavailableError):
        status_code = 409
        code = "controlled_clone_preview_unavailable"
    else:
        status_code = 500
        code = "controlled_clone_preview_failed"
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(exc)},
    ) from exc


def _raise_controlled_clone_approval_http_error(
    exc: Exception,
) -> None:
    code = getattr(exc, "code", "controlled_clone_approval_failed")
    details = getattr(exc, "details", None) or {}
    status_code = (
        422
        if isinstance(exc, ControlledCloneApprovalValidationError)
        else 409
    )
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": str(exc),
            "details": details,
        },
    ) from exc


def _current_voice_backend_capabilities():
    try:
        return build_voice_backend_capabilities(
            root_dir=ROOT_DIR,
        )
    except VoiceBackendCapabilityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "voice_backend_capabilities_unavailable",
                "message": str(exc),
            },
        ) from exc


def _require_lora_capability(action: str):
    capabilities = _current_voice_backend_capabilities()
    supported_key = (
        "lora_training_supported"
        if action == "training"
        else "lora_inference_supported"
    )
    if not capabilities[supported_key]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lora_unsupported",
                "message": capabilities["reason"],
                "action": action,
                "stable_lora_outcome": capabilities[
                    "stable_lora_outcome"
                ],
                "blockers": capabilities["blockers"],
            },
        )
    return capabilities


def _current_voice_training_source_context():
    source, source_text, source_error = (
        _current_character_roster_source()
    )
    return source, source_text, source_error


def _require_voice_training_source_context():
    source, source_text, source_error = (
        _current_voice_training_source_context()
    )
    if source is None or source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "voice_training_context_unavailable",
                "message": (
                    source_error
                    or "The selected source is unavailable."
                ),
            },
        )
    return source, source_text


def _current_voice_training_status():
    source, source_text, source_error = (
        _current_voice_training_source_context()
    )
    try:
        status = get_voice_training_status_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            source_text=source_text,
            current_source_fingerprint=(
                source.get("fingerprint")
                if isinstance(source, dict)
                else None
            ),
            script_path=SCRIPT_PATH,
        )
    except VoiceTrainingApiError as exc:
        _raise_voice_training_http_error(exc)
        raise AssertionError("unreachable")
    if source_error and not status.get("context_error"):
        status["context_error"] = source_error
    if not status.get("available"):
        status["blocking_tab"] = "characters"
        status["blocker_code"] = "approved_roster_required"
        roster_status = _current_character_roster_status()
        if roster_status.get("active") == "draft":
            status["blocker_code"] = "character_roster_approval_required"
            status["reason"] = (
                "Review and approve the current Character roster draft before "
                "creating Voice profiles."
            )
        elif source is not None and source_text is not None:
            try:
                pending = get_pending_roster_import_reconciliation(
                    root_dir=ROOT_DIR,
                    source_snapshot=source,
                    source_text=source_text,
                    draft_path=CHARACTER_ROSTER_DRAFT_PATH,
                    approved_path=CHARACTER_ROSTER_PATH,
                )
            except (
                RosterImportReconciliationConflictError,
                RosterImportReconciliationValidationError,
            ):
                pending = None
            if pending is not None:
                observation_count = int(
                    (pending.get("summary") or {}).get(
                        "imported_observations",
                        0,
                    )
                )
                status["blocker_code"] = (
                    "character_roster_reconciliation_required"
                )
                status["pending_observation_count"] = observation_count
                status["reason"] = (
                    f"Review and apply the saved Character roster reconciliation"
                    f" ({observation_count} imported observations) before "
                    "creating Voice profiles."
                )
        status["preserved_project_count"] = sum(
            1
            for path in Path(VOICE_TRAINING_PROJECTS_DIR).glob(
                "character_*/project.json"
            )
            if path.is_file()
        )
    return status


def _current_expressive_reference_bank_status():
    source, source_text, source_error = (
        _current_voice_training_source_context()
    )
    try:
        status = get_reference_bank_status_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            source_text=source_text,
            current_source_fingerprint=(
                source.get("fingerprint")
                if isinstance(source, dict)
                else None
            ),
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")
    if source_error and not status.get("context_error"):
        status["context_error"] = source_error
    return status


def _expressive_reference_bank_audio_path(
    *,
    character_id: str,
    reference_id: str | None = None,
    comparison_line_index: int | None = None,
    comparison_mode: str | None = None,
) -> Path:
    source, source_text = _require_voice_training_source_context()
    try:
        bank = get_reference_bank_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")

    asset: dict | None = None
    if reference_id is not None:
        asset = next(
            (
                item
                for item in bank["references"]
                if item["reference_id"] == reference_id
            ),
            None,
        )
    elif comparison_line_index is not None and comparison_mode is not None:
        if comparison_mode not in COMPARISON_MODES:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "reference_bank_audio_not_found",
                    "message": "The requested comparison mode does not exist.",
                },
            )
        asset = next(
            (
                item
                for item in bank["comparison"]["outputs"]
                if item["line_index"] == comparison_line_index
                and item["mode"] == comparison_mode
            ),
            None,
        )
    if asset is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "reference_bank_audio_not_found",
                "message": "The requested reference-bank audio was not found.",
            },
        )

    bank_dir = reference_bank_path(
        VOICE_TRAINING_PROJECTS_DIR,
        character_id,
    ).parent.resolve()
    target = (bank_dir / asset["audio_path"]).resolve()
    try:
        target.relative_to(bank_dir)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reference_bank_audio_invalid",
                "message": "The requested audio escaped its speaker project.",
            },
        ) from exc
    if not target.is_file() or sha256_file(target) != asset["audio_sha256"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reference_bank_audio_invalid",
                "message": "The requested audio is missing or changed.",
            },
        )
    return target


def _reference_bank_generation_backend(engine):
    capabilities = _current_voice_backend_capabilities()
    expressive = capabilities.get("expressive_clone", {})
    if not (
        expressive.get("supported") is True
        or expressive.get("experimental_preview_available") is True
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "controlled_clone_unavailable",
                "message": (
                    "The measured controlled-clone backend is unavailable."
                ),
            },
        )
    if not getattr(engine, "_use_mlx", False):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "controlled_clone_unavailable",
                "message": (
                    "Generated style references require the Apple Silicon "
                    "MLX controlled-clone runtime."
                ),
            },
        )
    backend = engine._init_mlx()
    return (
        backend.generate_instruction_controlled_clone,
        "mlx-audio-qwen3-icl-instruction-experimental",
        backend.CLONE_MODEL,
    )


def _reference_bank_clone_generator(engine):
    def generate(*, text, ref_audio, ref_text, output_path):
        config = {
            "_reference_bank_comparison_": {
                "type": "clone",
                "ref_audio": ref_audio,
                "ref_text": ref_text,
                "seed": "-1",
            }
        }
        return engine.generate_clone_voice(
            text,
            "_reference_bank_comparison_",
            config,
            output_path,
            instruct_text="",
        )
    return generate


@app.get("/api/character_visuals/status")
async def get_character_visual_status():
    return _current_character_visual_status()


@app.get("/api/character_visuals/{entry_id}")
async def get_character_visual(entry_id: str):
    source, source_text, approved, context_error = (
        _current_approved_visual_context()
    )
    if source is None or source_text is None or approved is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "visual_context_unavailable",
                "message": context_error
                or "Character visual context is unavailable.",
            },
        )

    entry = next(
        (
            item
            for item in approved["entries"]
            if item["id"] == entry_id
        ),
        None,
    )
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "roster_entry_missing",
                "message": "Approved roster entry was not found.",
            },
        )

    target = _character_visual_targets(approved)[entry_id]
    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "visual_missing",
                "message": (
                    "No optional visual dossier exists for this "
                    "character."
                ),
            },
        )

    try:
        ref = load_persona_reference(target)
        visual = (
            validate_visual_dossier(
                ref["visual"],
                source_text=source_text,
            )
            if "visual" in ref
            else None
        )
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "visual_invalid",
                "message": str(exc),
            },
        ) from exc

    if visual is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "visual_missing",
                "message": (
                    "The persona reference has no optional visual "
                    "dossier."
                ),
            },
        )

    return {
        "entry_id": entry_id,
        "canonical_name": entry["canonical_name"],
        "display_name": entry["display_name"],
        "visual": visual,
    }


@app.post("/api/character_visuals/discover")
async def discover_character_visuals(
    background_tasks: BackgroundTasks,
    request: CharacterVisualDiscoverRequest,
):
    if not request.enabled:
        return {
            "status": "disabled",
            "started": False,
        }

    if process_state["visual"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "visual_running",
                "message": (
                    "Optional character visual discovery is already "
                    "running."
                ),
            },
        )

    if process_state["roster"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roster_running",
                "message": (
                    "Wait for character roster discovery to finish "
                    "before collecting visual dossiers."
                ),
            },
        )

    source, _, approved, context_error = (
        _current_approved_visual_context()
    )
    if source is None or approved is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "visual_context_unavailable",
                "message": context_error
                or "Character visual context is unavailable.",
            },
        )

    entry_ids = list(request.entry_ids)
    if not entry_ids or len(entry_ids) != len(set(entry_ids)):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_visual_selection",
                "message": (
                    "Select at least one unique approved roster entry."
                ),
            },
        )

    approved_ids = {
        entry["id"]
        for entry in approved["entries"]
    }
    unknown_ids = sorted(set(entry_ids) - approved_ids)
    if unknown_ids:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_visual_selection",
                "message": (
                    "Selected roster entries were not found: "
                    + ", ".join(unknown_ids)
                ),
            },
        )

    passage_size = int(request.passage_size)
    overlap = int(request.overlap_chars)
    if passage_size < 100:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_passage_size",
                "message": (
                    "Visual passage size must be at least 100 "
                    "characters."
                ),
            },
        )
    if overlap < 0 or overlap >= passage_size:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_overlap",
                "message": (
                    "Visual overlap must be non-negative and smaller "
                    "than the passage size."
                ),
            },
        )

    progress = inspect_visual_discovery_state(
        PERSONA_VISUAL_STATE_PATH,
        current_source=source,
        roster_fingerprint=approved["roster_fingerprint"],
    )
    if progress["status"] in {
        "invalid",
        "incompatible_source",
        "incompatible_roster",
    }:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "visual_progress_blocked",
                "message": (
                    progress.get("error")
                    or "Discard incompatible visual progress explicitly."
                ),
            },
        )
    if (
        progress["exists"]
        and progress["character_ids"] != entry_ids
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "visual_selection_changed",
                "message": (
                    "Saved visual progress belongs to a different "
                    "character selection. Discard it explicitly before "
                    "starting another selection."
                ),
            },
        )

    command = [
        sys.executable,
        "-u",
        "discover_persona_visuals.py",
        str(source["path"]),
        "--enabled",
        "--passage-size",
        str(passage_size),
        "--overlap-chars",
        str(overlap),
    ]
    for entry_id in entry_ids:
        command.extend(["--entry-id", entry_id])

    background_tasks.add_task(
        run_process,
        command,
        "visual",
    )
    return {
        "status": "started",
        "started": True,
        "mode": "resume" if progress["exists"] else "new",
        "entry_ids": entry_ids,
        "completed_passages": progress["completed_passages"],
        "total_passages": progress["total_passages"],
    }


@app.post("/api/character_visuals/cancel")
async def cancel_character_visuals():
    task_state = process_state["visual"]
    if not task_state.get("running"):
        return {"status": "not_running"}

    task_state["cancel"] = True
    _append_process_log(
        "visual",
        "[CANCEL] Visual discovery cancellation requested",
        level="warning",
    )
    process = task_state.get("process")
    if process is not None and process.poll() is None:
        process.terminate()

    return {"status": "cancelling"}


@app.post("/api/character_visuals/discard-progress")
async def discard_character_visual_progress():
    if process_state["visual"].get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "visual_running",
                "message": (
                    "Cannot discard visual progress while discovery is "
                    "running."
                ),
            },
        )

    existed = clear_visual_discovery_state(
        PERSONA_VISUAL_STATE_PATH
    )
    return {
        "status": "discarded" if existed else "absent"
    }


@app.get("/api/voice_training/status")
async def get_voice_training_status():
    return _current_voice_training_status()


@app.get("/api/voice_training/{character_id}")
async def get_voice_training_project(character_id: str):
    source, source_text = _require_voice_training_source_context()
    try:
        return get_voice_training_project_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except VoiceTrainingApiError as exc:
        _raise_voice_training_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/voice_training/{character_id}/create")
async def create_voice_training_candidate(
    character_id: str,
    request: VoiceTrainingCreateRequest,
):
    source, source_text = _require_voice_training_source_context()
    try:
        return create_voice_training_candidate_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            priority=request.priority,
            desired_description=request.desired_description,
            desired_ref_text=request.desired_ref_text,
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except VoiceTrainingApiError as exc:
        _raise_voice_training_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/voice_training/{character_id}/action")
async def update_voice_training_project(
    character_id: str,
    request: VoiceTrainingActionRequest,
):
    source, source_text = _require_voice_training_source_context()
    try:
        return apply_voice_training_action_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            expected_fingerprint=request.project_fingerprint,
            action=request.action,
            payload=dict(request.payload),
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except VoiceTrainingApiError as exc:
        _raise_voice_training_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/expressive_reference_banks/status")
async def get_expressive_reference_bank_status():
    return _current_expressive_reference_bank_status()


@app.get("/api/expressive_reference_banks/{character_id}")
async def get_expressive_reference_bank(character_id: str):
    source, source_text = _require_voice_training_source_context()
    try:
        return get_reference_bank_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/expressive_reference_banks/{character_id}/audio/reference/{reference_id}"
)
async def get_expressive_reference_audio(
    character_id: str,
    reference_id: str,
):
    return FileResponse(
        _expressive_reference_bank_audio_path(
            character_id=character_id,
            reference_id=reference_id,
        )
    )


@app.get(
    "/api/expressive_reference_banks/{character_id}/audio/comparison/"
    "{line_index}/{mode}"
)
async def get_expressive_reference_comparison_audio(
    character_id: str,
    line_index: int,
    mode: str,
):
    if line_index < 0:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "reference_bank_audio_not_found",
                "message": "The requested comparison line does not exist.",
            },
        )
    return FileResponse(
        _expressive_reference_bank_audio_path(
            character_id=character_id,
            comparison_line_index=line_index,
            comparison_mode=mode,
        )
    )


@app.post("/api/expressive_reference_banks/{character_id}/create")
async def create_expressive_reference_bank(
    character_id: str,
    request: ExpressiveReferenceBankCreateRequest,
):
    source, source_text = _require_voice_training_source_context()
    try:
        return create_reference_bank_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            identity_seed=request.identity_seed,
            source_clip_id=request.source_clip_id,
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/expressive_reference_banks/{character_id}/generate")
async def generate_expressive_reference(
    character_id: str,
    request: ExpressiveReferenceBankGenerateRequest,
):
    source, source_text = _require_voice_training_source_context()
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "tts_engine_unavailable",
                "message": "Failed to initialize the TTS engine.",
            },
        )
    generator, backend, model = _reference_bank_generation_backend(engine)
    try:
        return generate_reference_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            expected_fingerprint=request.bank_fingerprint,
            style_key=request.style_key,
            reference_text=request.reference_text,
            instruction=request.instruction,
            controlled_clone_generator=generator,
            generation_backend=backend,
            model=model,
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/expressive_reference_banks/{character_id}/action")
async def update_expressive_reference_bank(
    character_id: str,
    request: ExpressiveReferenceBankActionRequest,
):
    source, source_text = _require_voice_training_source_context()
    try:
        return apply_reference_bank_action_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            expected_fingerprint=request.bank_fingerprint,
            action=request.action,
            payload=dict(request.payload),
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/expressive_reference_banks/{character_id}/compare")
async def compare_expressive_reference_bank(
    character_id: str,
    request: ExpressiveReferenceBankCompareRequest,
):
    source, source_text = _require_voice_training_source_context()
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "tts_engine_unavailable",
                "message": "Failed to initialize the TTS engine.",
            },
        )
    try:
        return generate_comparison_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            expected_fingerprint=request.bank_fingerprint,
            test_lines=[line.model_dump() for line in request.lines],
            design_generator=engine.generate_voice_design,
            clone_generator=_reference_bank_clone_generator(engine),
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/expressive_reference_banks/{character_id}/assign")
async def assign_expressive_reference_bank(
    character_id: str,
    request: ExpressiveReferenceBankAssignRequest,
):
    source, source_text = _require_voice_training_source_context()
    try:
        return assign_reference_bank_payload(
            approved_roster_path=CHARACTER_ROSTER_PATH,
            projects_root=VOICE_TRAINING_PROJECTS_DIR,
            character_id=character_id,
            expected_fingerprint=request.bank_fingerprint,
            voice_config_path=VOICE_CONFIG_PATH,
            project_root=ROOT_DIR,
            assign=request.assign,
            voice_name=request.voice_name,
            source_text=source_text,
            current_source_fingerprint=source["fingerprint"],
        )
    except ExpressiveReferenceBankApiError as exc:
        _raise_expressive_reference_bank_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/speaker_management/status")
async def get_speaker_management_status(
    speaker: Optional[str] = None,
):
    try:
        return get_speaker_management_status_payload(
            root_dir=ROOT_DIR,
            speaker=speaker,
        )
    except SpeakerManagementApiError as exc:
        _raise_speaker_management_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/speaker_management/history/{operation_id}")
async def get_speaker_management_operation(operation_id: str):
    try:
        return get_speaker_operation_payload(
            root_dir=ROOT_DIR,
            operation_id=operation_id,
        )
    except SpeakerManagementApiError as exc:
        _raise_speaker_management_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/speaker_management/action")
async def manage_speakers(
    request: SpeakerManagementActionRequest,
):
    try:
        return apply_speaker_operation_payload(
            root_dir=ROOT_DIR,
            operation=request.operation,
            expected_script_fingerprint=(
                request.expected_script_fingerprint
            ),
            payload=dict(request.payload),
        )
    except SpeakerManagementApiError as exc:
        _raise_speaker_management_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/speaker_management/undo")
async def undo_speaker_management(
    request: SpeakerManagementUndoRequest,
):
    try:
        return undo_speaker_operation_payload(
            root_dir=ROOT_DIR,
            operation_id=request.operation_id,
        )
    except SpeakerManagementApiError as exc:
        _raise_speaker_management_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/llm_profiles")
async def get_llm_profiles():
    try:
        return get_llm_profiles_payload(
            config_path=CONFIG_PATH,
        )
    except LLMProfilesApiError as exc:
        _raise_llm_profiles_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/llm_profiles/{stage}")
async def get_llm_stage_profile(stage: str):
    try:
        return get_llm_stage_profile_payload(
            config_path=CONFIG_PATH,
            stage=stage,
        )
    except LLMProfilesApiError as exc:
        _raise_llm_profiles_http_error(exc)
        raise AssertionError("unreachable")


@app.put("/api/llm_profiles/{stage}")
async def update_llm_stage_profile(
    stage: str,
    request: LLMProfileUpdateRequest,
):
    try:
        return update_llm_stage_profile_payload(
            config_path=CONFIG_PATH,
            stage=stage,
            profile=dict(request.profile),
            expected_profiles_fingerprint=(
                request.expected_profiles_fingerprint
            ),
        )
    except LLMProfilesApiError as exc:
        _raise_llm_profiles_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/llm_profiles/{stage}/remove")
async def remove_llm_stage_profile(
    stage: str,
    request: LLMProfileRemoveRequest,
):
    try:
        return remove_llm_stage_profile_payload(
            config_path=CONFIG_PATH,
            stage=stage,
            expected_profiles_fingerprint=(
                request.expected_profiles_fingerprint
            ),
        )
    except LLMProfilesApiError as exc:
        _raise_llm_profiles_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/migration/status")
async def get_migration_status():
    try:
        return get_migration_status_payload(
            root_dir=MIGRATION_ROOT_DIR,
            config_path=CONFIG_PATH,
        )
    except MigrationApiError as exc:
        _raise_migration_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/migration/history")
async def get_migration_history():
    try:
        return get_migration_history_payload(root_dir=MIGRATION_ROOT_DIR)
    except MigrationApiError as exc:
        _raise_migration_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/migration/history/{operation_id}")
async def get_migration_operation(operation_id: str):
    try:
        return get_migration_operation_payload(
            root_dir=MIGRATION_ROOT_DIR,
            operation_id=operation_id,
        )
    except MigrationApiError as exc:
        _raise_migration_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/migration/apply")
async def apply_project_migration(
    request: MigrationApplyRequest,
):
    try:
        return apply_migration_payload(
            root_dir=MIGRATION_ROOT_DIR,
            config_path=CONFIG_PATH,
            expected_plan_fingerprint=request.plan_fingerprint,
            confirm=request.confirm,
        )
    except MigrationApiError as exc:
        _raise_migration_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/migration/rollback")
async def rollback_project_migration(
    request: MigrationRollbackRequest,
):
    try:
        return rollback_migration_payload(
            root_dir=MIGRATION_ROOT_DIR,
            config_path=CONFIG_PATH,
            operation_id=request.operation_id,
        )
    except MigrationApiError as exc:
        _raise_migration_http_error(exc)
        raise AssertionError("unreachable")


def _first_text_difference(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    return limit


def _reviewable_script_entries() -> list[dict]:
    entries = _external_script_entries()
    _, source_text, source_error = _external_source_context()
    if source_text is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "review_source_required",
                "message": source_error or "A readable selected source is required before Script review.",
            },
        )
    try:
        source_segments = split_source_segments(source_text)
    except UnbalancedDialogueQuotesError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "review_source_invalid",
                "message": str(exc),
            },
        ) from exc

    source_stream = normalize_review_text(
        " ".join(segment.text for segment in source_segments)
    )
    script_stream = build_review_text_stream(entries)
    if source_stream != script_stream:
        difference = _first_text_difference(source_stream, script_stream)
        preview_start = max(0, difference - 80)
        preview_end = difference + 160
        raise HTTPException(
            status_code=409,
            detail={
                "code": "script_text_fidelity_failed",
                "message": (
                    "The current Script is not reviewable because its spoken text "
                    "does not match the normalized source. Recreate or import a "
                    "corrected Script before running local or external review."
                ),
                "difference_index": difference,
                "source_preview": source_stream[preview_start:preview_end],
                "script_preview": script_stream[preview_start:preview_end],
            },
        )
    return entries


def _review_workload() -> dict[str, int]:
    total_entries = len(_reviewable_script_entries())

    batch_size = 25
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                config = json.load(handle)
            batch_size = max(
                1,
                int(config.get("generation", {}).get("review_batch_size", 25)),
            )
        except (json.JSONDecodeError, ValueError, TypeError, OSError):
            batch_size = 25

    return {
        "total_entries": total_entries,
        "batch_size": batch_size,
        "estimated_calls": (
            ceil(total_entries / batch_size)
            if total_entries
            else 0
        ),
    }


def _review_command(*, context_window: int = 0) -> list[str]:
    command = [
        sys.executable,
        "-u",
        "review_script.py",
        "--project-root",
        ROOT_DIR,
        "--config-path",
        CONFIG_PATH,
    ]
    if context_window > 0:
        command.extend(["--context-window", str(context_window)])
    return command


def _reserve_review_run(*, mode: str, window_size: int) -> dict[str, int]:
    state = process_state["review"]
    if state.get("running"):
        raise HTTPException(
            status_code=409,
            detail="Script review already running",
        )

    workload = _review_workload()
    _reset_process_logs("review")
    state.update(
        {
            "running": True,
            "process": None,
            "mode": mode,
            "window_size": window_size,
            **workload,
            "started_at": _utc_now_text(),
            "finished_at": None,
            "return_code": None,
            "last_error": None,
        }
    )
    return workload


def _run_review_process(command: list[str]) -> int:
    state = process_state["review"]
    return_code = run_process(command, "review")
    state["return_code"] = return_code
    state["finished_at"] = _utc_now_text()
    if return_code == 0:
        state["last_error"] = None
    else:
        logs = state.get("logs") or []
        state["last_error"] = (
            str(logs[-1])
            if logs
            else f"Review failed with return code {return_code}."
        )
    return return_code


@app.post("/api/review_script")
async def review_script(background_tasks: BackgroundTasks):
    if not os.path.exists(SCRIPT_PATH):
        raise HTTPException(
            status_code=400,
            detail="No annotated script found. Generate a script first.",
        )

    workload = _reserve_review_run(mode="standard", window_size=0)
    background_tasks.add_task(
        _run_review_process,
        _review_command(),
    )
    return {
        "status": "started",
        "mode": "standard",
        **workload,
    }


@app.get("/api/review_script_contextual/estimate")
async def review_script_contextual_estimate():
    if not os.path.exists(SCRIPT_PATH):
        raise HTTPException(
            status_code=400,
            detail="No annotated script found. Generate a script first.",
        )
    return _review_workload()


@app.post("/api/review_script_contextual")
async def review_script_contextual(
    request: ContextualReviewRequest,
    background_tasks: BackgroundTasks,
):
    if not os.path.exists(SCRIPT_PATH):
        raise HTTPException(
            status_code=400,
            detail="No annotated script found. Generate a script first.",
        )

    window_size = max(1, min(int(request.window_size or 4), 12))
    workload = _reserve_review_run(
        mode="contextual",
        window_size=window_size,
    )
    background_tasks.add_task(
        _run_review_process,
        _review_command(context_window=window_size),
    )
    return {
        "status": "started",
        "mode": "contextual",
        "window_size": window_size,
        **workload,
    }

@app.get("/api/annotated_script")
async def get_annotated_script():
    """Return the current Script entries, or an empty generation-state list."""
    if not os.path.exists(SCRIPT_PATH):
        return []
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

@app.get("/api/status/{task_name}")
async def get_status(task_name: str):
    if task_name not in process_state:
        raise HTTPException(status_code=404, detail="Task not found")
    state = dict(process_state[task_name])
    state.pop("process", None)
    return state

@app.get("/api/voices")
async def get_voices():
    # Parse voices directly from the current script (no stale cache)
    voices_list = []
    if os.path.exists(SCRIPT_PATH):
        try:
            with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
                script_data = json.load(f)
            voices_set = set()
            for entry in script_data:
                speaker = (entry.get("speaker") or entry.get("type") or "").strip()
                if speaker:
                    voices_set.add(speaker)
            voices_list = sorted(voices_set)
        except (json.JSONDecodeError, ValueError):
            pass

    if not voices_list:
        return []

    script_voice_names = set(voices_list)

    # Combine with config
    voice_config = {}
    if os.path.exists(VOICE_CONFIG_PATH):
        try:
            with open(VOICE_CONFIG_PATH, "r", encoding="utf-8") as f:
                voice_config = json.load(f)
        except (json.JSONDecodeError, ValueError):
            voice_config = {}
    if not isinstance(voice_config, dict):
        voice_config = {}

    # Keep transitive alias targets editable even when they have no spoken line
    # in the current script. Do not surface unrelated stale configuration keys.
    visible_voice_names = set(voices_list)
    pending_alias_names = list(voices_list)
    while pending_alias_names:
        voice_name = pending_alias_names.pop()
        config = voice_config.get(voice_name, {})
        if not isinstance(config, dict):
            continue
        alias_target = config.get("alias_of") or config.get("alias")
        if not isinstance(alias_target, str):
            continue
        alias_target = alias_target.strip()
        if (
            alias_target
            and alias_target in voice_config
            and alias_target not in visible_voice_names
        ):
            visible_voice_names.add(alias_target)
            pending_alias_names.append(alias_target)
    voices_list = sorted(visible_voice_names)

    missing_speakers = {
        voice_name
        for voice_name in script_voice_names
        if voice_name not in voice_config
    }

    result = []
    for voice_name in voices_list:
        config = voice_config.get(voice_name, {})
        try:
            alias_resolution = resolve_voice_alias(
                voice_name,
                voice_config,
            ).as_dict()
        except VoiceAliasError as exc:
            alias_resolution = {
                "is_alias": bool(
                    isinstance(config, dict)
                    and (config.get("alias_of") or config.get("alias"))
                ),
                "alias_of": (
                    config.get("alias_of")
                    if isinstance(config, dict)
                    else None
                ),
                "chain": [voice_name],
                "resolved_target": None,
                "resolved_type": None,
                "resolved_source": None,
                "error": exc.detail(),
            }
        result.append({
            "name": voice_name,
            "config": config,
            "alias_resolution": alias_resolution,
            "persona_pending": voice_name in missing_speakers
        })
    return result


@app.post("/api/generate_personas")
async def generate_personas(background_tasks: BackgroundTasks, request: GeneratePersonasRequest = GeneratePersonasRequest()):
    """Generate LLM-derived voice persona descriptions and VoiceDesign previews.

    This runs `app/generate_personas.py` which:
    - reads `annotated_script.json`,
    - asks the configured LLM to produce a short `description` and `ref_text` for each character,
    - uses the VoiceDesign model to synthesize a preview and saves it,
    - updates `voice_config.json` with a clone-style reference for each character.
    """
    if process_state["persona"]["running"]:
        raise HTTPException(status_code=400, detail="Persona generation already running")

    process_state["persona"]["cancel"] = False

    # Unload TTS engine to free GPU for the subprocess
    if project_manager.engine is not None:
        logger.info("Unloading TTS engine for persona generation...")
        project_manager.engine = None
        gc.collect()

    command = [sys.executable, "-u", "generate_personas.py"]
    if request.advanced:
        batch_size = max(1, min(int(request.batch_size or 40), 200))
        command.extend(["--advanced", "--batch-size", str(batch_size)])
    background_tasks.add_task(run_process, command, "persona")
    return {"status": "started", "advanced": request.advanced}


@app.post("/api/cancel_persona")
async def cancel_persona():
    if not process_state["persona"]["running"]:
        return {"status": "idle"}

    process_state["persona"]["cancel"] = True
    _append_process_log(
        "persona",
        "[CANCEL] Cancellation requested",
        level="warning",
    )

    proc = process_state["persona"].get("process")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception as e:
            logger.warning(f"Failed to terminate persona process cleanly: {e}")

    return {"status": "cancelling"}

def _controlled_clone_legacy_signature(config: dict) -> tuple:
    return (
        str(config.get("ref_audio") or "").strip(),
        str(config.get("ref_text") or "").strip(),
        str(
            config.get("character_style")
            or config.get("default_style")
            or ""
        ).strip(),
        float(config.get("instruction_clone_temperature", 0.75)),
        int(config.get("instruction_clone_top_k", 50)),
        float(config.get("instruction_clone_top_p", 0.95)),
        float(config.get("instruction_clone_repetition_penalty", 1.5)),
        int(config.get("instruction_clone_max_tokens", 2000)),
        int(config.get("seed", -1)),
        json.dumps(
            config.get("experimental_prompt_routing"),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _controlled_clone_configuration_fingerprint(
    config: dict,
    *,
    root_dir: str | Path | None = None,
) -> str:
    resolved_root = Path(root_dir or ROOT_DIR).expanduser().resolve()
    try:
        controlled_fingerprint = build_controlled_clone_configuration_fingerprint(
            root_dir=resolved_root,
            ref_audio=str(config.get("ref_audio") or ""),
            ref_text=str(config.get("ref_text") or ""),
            character_style=str(
                config.get("character_style")
                or config.get("default_style")
                or ""
            ),
            temperature=float(
                config.get("instruction_clone_temperature", 0.75)
            ),
            top_k=int(config.get("instruction_clone_top_k", 50)),
            top_p=float(config.get("instruction_clone_top_p", 0.95)),
            repetition_penalty=float(
                config.get("instruction_clone_repetition_penalty", 1.5)
            ),
            max_tokens=int(
                config.get("instruction_clone_max_tokens", 2000)
            ),
            seed=int(config.get("seed", -1)),
        )
        raw_prompt_routing = config.get("experimental_prompt_routing")
        if raw_prompt_routing is None:
            return controlled_fingerprint
        prompt_routing = validate_experimental_prompt_routing(
            raw_prompt_routing,
            project_root=resolved_root,
            verify_audio=True,
        )
        return fingerprint_value(
            {
                "controlled_clone": controlled_fingerprint,
                "experimental_prompt_routing": prompt_routing_fingerprint(
                    prompt_routing
                ),
            }
        )
    except (
        ControlledClonePreviewValidationError,
        ExperimentalPromptRoutingError,
        TypeError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "controlled_clone_configuration_invalid",
                "message": str(exc),
            },
        ) from exc


def _validate_voice_library_assignment_update(
    update: dict[str, Any],
    *,
    validation_root: str | Path,
) -> None:
    if (
        update.get("type") == "clone"
        and update.get("clone_backend") == "qwen3_instruction_controlled"
    ):
        source_fingerprint = str(
            update.get("controlled_clone_configuration_fingerprint") or ""
        ).strip()
        computed_fingerprint = _controlled_clone_configuration_fingerprint(
            update,
            root_dir=validation_root,
        )
        if source_fingerprint and source_fingerprint != computed_fingerprint:
            raise VoiceLibraryError(
                "voice_library_approval_mismatch",
                "The reusable controlled clone no longer matches its listening approval.",
            )
        update["controlled_clone_configuration_fingerprint"] = computed_fingerprint
    if (
        update.get("type") == "clone"
        and update.get("clone_backend") == ROUTED_CLONE_BACKEND
    ):
        source_fingerprint = str(
            update.get("responsive_backend_configuration_fingerprint") or ""
        ).strip()
        try:
            responsive_policy = validate_recurring_voice_routing(
                update.get("responsive_backend_routing"),
                project_root=validation_root,
                verify_audio=True,
            )
        except RecurringVoiceRoutingError as exc:
            raise VoiceLibraryError(
                "voice_library_approval_mismatch",
                f"The reusable responsive Voice is invalid: {exc}",
            ) from exc
        computed_fingerprint = recurring_routing_fingerprint(
            responsive_policy
        )
        if not source_fingerprint or source_fingerprint != computed_fingerprint:
            raise VoiceLibraryError(
                "voice_library_approval_mismatch",
                "The reusable responsive Voice no longer matches its reviewed routing approval.",
            )
        update["responsive_backend_routing"] = responsive_policy
        update["responsive_backend_configuration_fingerprint"] = (
            computed_fingerprint
        )
        update.pop("controlled_clone_configuration_fingerprint", None)


@app.post("/api/save_voice_config")
async def save_voice_config(config_data: Dict[str, VoiceConfigItem]):
    current_config = {}
    if os.path.exists(VOICE_CONFIG_PATH):
        with open(VOICE_CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                current_config = json.load(f)
            except (json.JSONDecodeError, ValueError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "voice_config_invalid",
                        "message": (
                            "The saved voice configuration is invalid and was not changed."
                        ),
                    },
                ) from exc

    updates: dict[str, dict] = {}
    approval_tokens: dict[str, str] = {}
    for voice_name, config in config_data.items():
        update = config.model_dump(exclude_unset=True)
        approval_token = update.pop(
            "controlled_clone_approval_token",
            None,
        )
        update.pop(
            "controlled_clone_configuration_fingerprint",
            None,
        )
        update.pop(
            "responsive_backend_configuration_fingerprint",
            None,
        )
        current_voice = current_config.get(voice_name)
        if not isinstance(current_voice, dict):
            current_voice = {}
        raw_voice_type = str(
            update.get("type")
            or current_voice.get("type")
            or "custom"
        ).strip().casefold()
        requested_clone_backend = str(
            update.get("clone_backend")
            or current_voice.get("clone_backend")
            or "qwen3_base"
        ).strip()
        current_is_responsive = bool(
            current_voice.get("type") == "clone"
            and current_voice.get("clone_backend") == ROUTED_CLONE_BACKEND
        )
        requested_is_responsive = bool(
            raw_voice_type == "clone"
            and requested_clone_backend == ROUTED_CLONE_BACKEND
        )
        if raw_voice_type in {
            "design",
            "designed",
            "designed_voice",
            "voice_design",
        }:
            description = str(
                update.get("description")
                if "description" in update
                else current_voice.get("description")
                or ""
            ).strip()
            if not description:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "designed_voice_definition_required",
                        "message": (
                            "Designed Voice requires a Voice definition. "
                            "A built-in Voice name cannot be used as the definition."
                        ),
                        "context": {"voice": voice_name},
                    },
                )
            update["type"] = "design"
            update["voice"] = None
            update["description"] = description
        if raw_voice_type == "community_qvoice":
            immutable_fields = {
                "community_pack_id",
                "community_pack_path",
                "community_pack_family",
                "community_pack_runtime",
                "community_pack_sha256",
                "community_pack_approval_fingerprint",
                "description",
                "character_style",
            }
            changed = [
                field
                for field in immutable_fields
                if field in update and update.get(field) != current_voice.get(field)
            ]
            if changed:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "community_qvoice_review_required",
                        "message": (
                            "An approved community Qwen Voice cannot be edited in place. "
                            "Preview and approve the changed Voice, then assign it again."
                        ),
                        "context": {"voice": voice_name, "changed_fields": sorted(changed)},
                    },
                )
        raw_instruction_propagation = update.get(
            "instruction_propagation"
        )
        raw_prompt_routing = update.get(
            "experimental_prompt_routing"
        )
        raw_responsive_routing = update.get(
            "responsive_backend_routing"
        )
        if raw_instruction_propagation is not None:
            try:
                update["instruction_propagation"] = (
                    validate_instruction_propagation_contract(
                        raw_instruction_propagation
                    )
                )
            except InstructionPropagationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "voice_instruction_propagation_invalid",
                        "message": str(exc),
                        "context": {"voice": voice_name},
                    },
                ) from exc
        if raw_prompt_routing is not None:
            try:
                update["experimental_prompt_routing"] = (
                    validate_experimental_prompt_routing(
                        raw_prompt_routing,
                        project_root=ROOT_DIR,
                        verify_audio=True,
                    )
                )
            except ExperimentalPromptRoutingError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "experimental_prompt_routing_invalid",
                        "message": str(exc),
                        "context": {"voice": voice_name},
                    },
                ) from exc
        if requested_is_responsive:
            if not current_is_responsive:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "responsive_voice_review_required",
                        "message": (
                            "Reviewed responsive Voice routing cannot be created through "
                            "the ordinary Voice save. Assign the reviewed Voice from the "
                            "Voice Library instead."
                        ),
                        "context": {"voice": voice_name},
                    },
                )
            try:
                current_policy = validate_recurring_voice_routing(
                    current_voice.get("responsive_backend_routing"),
                    project_root=ROOT_DIR,
                    verify_audio=True,
                )
            except RecurringVoiceRoutingError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "responsive_voice_review_required",
                        "message": (
                            "The saved recurring Voice approval is invalid or stale. "
                            "Reassign the reviewed Voice from the Voice Library."
                        ),
                        "context": {"voice": voice_name, "reason": str(exc)},
                    },
                ) from exc
            current_fingerprint = recurring_routing_fingerprint(current_policy)
            if (
                current_voice.get("responsive_backend_configuration_fingerprint")
                != current_fingerprint
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "responsive_voice_review_required",
                        "message": (
                            "The saved recurring Voice no longer matches its reviewed "
                            "routing approval. Reassign it from the Voice Library."
                        ),
                        "context": {"voice": voice_name},
                    },
                )
            protected_fields = {
                "type",
                "voice",
                "library_voice_id",
                "character_style",
                "default_style",
                "description",
                "seed",
                "ref_audio",
                "ref_text",
                "clone_backend",
                "instruction_clone_temperature",
                "instruction_clone_top_k",
                "instruction_clone_top_p",
                "instruction_clone_repetition_penalty",
                "instruction_clone_max_tokens",
            }
            changed_fields = sorted(
                field
                for field in protected_fields
                if field in update and update.get(field) != current_voice.get(field)
            )
            if changed_fields:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "responsive_voice_review_required",
                        "message": (
                            "A reviewed recurring Voice cannot be edited in place. "
                            "Choose another Voice or reassign the reviewed Voice from "
                            "the Voice Library."
                        ),
                        "context": {
                            "voice": voice_name,
                            "changed_fields": changed_fields,
                        },
                    },
                )
            if raw_responsive_routing is not None:
                try:
                    submitted_policy = validate_recurring_voice_routing(
                        raw_responsive_routing,
                        project_root=ROOT_DIR,
                        verify_audio=True,
                    )
                except RecurringVoiceRoutingError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "responsive_backend_routing_invalid",
                            "message": str(exc),
                            "context": {"voice": voice_name},
                        },
                    ) from exc
                if submitted_policy != current_policy:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "responsive_voice_review_required",
                            "message": (
                                "Responsive backend routing changed. Reassign the "
                                "reviewed Voice from the Voice Library."
                            ),
                            "context": {"voice": voice_name},
                        },
                    )
            update["responsive_backend_routing"] = current_policy
            update["responsive_backend_configuration_fingerprint"] = (
                current_fingerprint
            )
        else:
            update.pop("responsive_backend_routing", None)
            update.pop("responsive_backend_configuration_fingerprint", None)
        updates[voice_name] = update
        if approval_token:
            approval_tokens[voice_name] = approval_token

    try:
        candidate, alias_diagnostics = merge_voice_config_updates(
            current_config,
            updates,
        )
    except VoiceAliasError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.detail(),
        ) from exc

    fish_capability = None
    required_approvals: list[dict[str, str]] = []
    for voice_name in updates:
        voice = candidate.get(voice_name)
        if not isinstance(voice, dict):
            continue
        responsive = (
            not voice.get("alias_of")
            and voice.get("type") == "clone"
            and voice.get("clone_backend") == ROUTED_CLONE_BACKEND
        )
        if responsive:
            try:
                policy = validate_recurring_voice_routing(
                    voice.get("responsive_backend_routing"),
                    project_root=ROOT_DIR,
                    verify_audio=True,
                )
            except RecurringVoiceRoutingError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "responsive_backend_routing_invalid",
                        "message": str(exc),
                        "context": {"voice": voice_name},
                    },
                ) from exc
            voice["responsive_backend_routing"] = policy
            voice["responsive_backend_configuration_fingerprint"] = (
                recurring_routing_fingerprint(policy)
            )
            voice.pop("controlled_clone_configuration_fingerprint", None)
            continue
        voice.pop("responsive_backend_routing", None)
        voice.pop("responsive_backend_configuration_fingerprint", None)
        if (
            not voice.get("alias_of")
            and voice.get("type") == "clone"
            and voice.get("clone_backend") == "fish_s21_cloud"
        ):
            if fish_capability is None:
                fish_capability = _current_voice_backend_capabilities().get(
                    "fish_s21_cloud",
                    {},
                )
            if fish_capability.get("available") is not True:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "fish_cloud_unavailable",
                        "message": (
                            "Enable Fish cloud and configure its API key in Speech settings before assigning it to a Voice."
                        ),
                        "details": {"speaker": voice_name},
                    },
                )
            if not str(voice.get("ref_audio") or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "fish_cloud_reference_audio_required",
                        "message": "Fish cloud requires supplied reference audio.",
                        "details": {"speaker": voice_name},
                    },
                )
            if not str(voice.get("ref_text") or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "fish_cloud_reference_text_required",
                        "message": "Fish cloud requires the exact reference transcript.",
                        "details": {"speaker": voice_name},
                    },
                )
        if voice.get("fish_hybrid_enabled"):
            if voice.get("alias_of") or voice.get("type") != "clone":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "fish_hybrid_clone_required",
                        "message": "Fish hybrid routing is available only for independent clone Voices.",
                        "details": {"speaker": voice_name},
                    },
                )
            if not str(voice.get("ref_audio") or "").strip() or not str(
                voice.get("ref_text") or ""
            ).strip():
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "fish_hybrid_reference_required",
                        "message": "Fish hybrid routing requires reference audio and its exact transcript.",
                        "details": {"speaker": voice_name},
                    },
                )
        controlled = (
            not voice.get("alias_of")
            and voice.get("type") == "clone"
            and voice.get("clone_backend")
            == "qwen3_instruction_controlled"
        )
        if not controlled:
            voice.pop("controlled_clone_configuration_fingerprint", None)
            continue

        configuration_fingerprint = (
            _controlled_clone_configuration_fingerprint(voice)
        )
        current_voice = current_config.get(voice_name)
        if not isinstance(current_voice, dict):
            current_voice = {}
        saved_fingerprint = current_voice.get(
            "controlled_clone_configuration_fingerprint"
        )
        existing_controlled = (
            not current_voice.get("alias_of")
            and current_voice.get("type") == "clone"
            and current_voice.get("clone_backend")
            == "qwen3_instruction_controlled"
        )
        try:
            legacy_unchanged = (
                existing_controlled
                and not saved_fingerprint
                and _controlled_clone_legacy_signature(current_voice)
                == _controlled_clone_legacy_signature(voice)
            )
        except (TypeError, ValueError):
            legacy_unchanged = False
        already_approved = (
            existing_controlled
            and saved_fingerprint == configuration_fingerprint
        )
        if not already_approved and not legacy_unchanged:
            approval_token = approval_tokens.get(voice_name)
            if not approval_token:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "controlled_clone_approval_required",
                        "message": (
                            "Generate, listen through, and confirm the matching "
                            "preview before saving this controlled clone."
                        ),
                        "details": {"speaker": voice_name},
                    },
                )
            required_approvals.append(
                {
                    "speaker": voice_name,
                    "approval_token": approval_token,
                    "configuration_fingerprint": configuration_fingerprint,
                }
            )
        voice[
            "controlled_clone_configuration_fingerprint"
        ] = configuration_fingerprint

    try:
        consume_controlled_clone_approvals(required_approvals)
    except (
        ControlledCloneApprovalConflictError,
        ControlledCloneApprovalValidationError,
    ) as exc:
        _raise_controlled_clone_approval_http_error(exc)

    invalidation = _apply_voice_config_dependency_change(
        before=current_config,
        after=candidate,
        operation="voice_config_save",
        reason="Production Voice configuration changed.",
        metadata={"route": "/api/save_voice_config"},
    )

    return {
        "status": "saved",
        "aliases": alias_diagnostics,
        "audio_invalidation": (
            _audio_invalidation_summary(invalidation)
            if invalidation is not None
            else None
        ),
    }


@app.post("/api/audio-invalidation/{operation_id}/undo")
async def undo_audio_invalidation(operation_id: str):
    safe_id = str(operation_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", safe_id):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "audio_invalidation_operation_invalid",
                "message": "Audio invalidation operation ID is invalid.",
            },
        )
    try:
        return undo_project_audio_invalidation(
            project_root=_voice_config_project_root(),
            operation_id=safe_id,
            undone_at_utc=_utc_now_text(),
        )
    except AudioInvalidationError as exc:
        _audio_invalidation_http_error(exc)


def _raise_pronunciation_http_error(exc: PronunciationRegistryError):
    status_code = 500 if exc.code in {
        "pronunciation_preview_engine_unavailable",
        "pronunciation_preview_generation_failed",
    } else 409 if exc.code in {
        "pronunciation_registry_changed",
        "pronunciation_overlap_conflict",
        "pronunciation_source_fingerprint_mismatch",
        "pronunciation_source_span_mismatch",
        "pronunciation_entry_missing",
    } else 422
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": exc.code,
            "message": str(exc),
            "context": copy.deepcopy(exc.context),
        },
    ) from exc


def _pronunciation_registry_status() -> dict:
    chunks = project_manager.load_chunks()
    registry = load_pronunciation_registry(ROOT_DIR)
    entries = []
    for entry in registry["entries"]:
        item = copy.deepcopy(entry)
        index = int(item["chunk_index"])
        current = False
        if 0 <= index < len(chunks) and isinstance(chunks[index], dict):
            text = str(chunks[index].get("text") or "")
            current = bool(
                hashlib.sha256(text.encode("utf-8")).hexdigest()
                == item["chunk_text_sha256"]
                and item["end_char"] <= len(text)
                and text[item["start_char"]:item["end_char"]] == item["original"]
            )
        item["anchor_state"] = "current" if current else "stale"
        entries.append(item)
    return {
        **registry,
        "entries": entries,
        "summary": {
            "entry_count": len(entries),
            "approved_count": sum(
                item["review"]["state"] == "approved" for item in entries
            ),
            "stale_anchor_count": sum(
                item["anchor_state"] == "stale" for item in entries
            ),
        },
    }


def _preview_pronunciation(
    *,
    chunk_index: int,
    registry: dict,
) -> dict:
    chunks = project_manager.load_chunks()
    if not 0 <= chunk_index < len(chunks):
        raise PronunciationRegistryError(
            "pronunciation_chunk_missing",
            f"Pronunciation preview references missing chunk {chunk_index}.",
        )
    chunk = chunks[chunk_index]
    voice_config = {}
    if Path(VOICE_CONFIG_PATH).is_file():
        try:
            voice_config = json.loads(Path(VOICE_CONFIG_PATH).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PronunciationRegistryError(
                "pronunciation_voice_config_invalid",
                f"Voice configuration could not be read: {exc}",
            ) from exc
    speaker = str(chunk.get("speaker") or "")
    try:
        resolved = project_manager._resolve_alias(speaker, voice_config)
    except VoiceAliasError as exc:
        raise PronunciationRegistryError(
            "pronunciation_voice_alias_invalid",
            str(exc),
        ) from exc
    voice_data = voice_config.get(resolved, {})
    tts_config = project_manager._load_tts_config()
    resolution = resolve_pronunciation_request(
        registry=registry,
        chunk_index=chunk_index,
        text=str(chunk.get("text") or ""),
        speaker=speaker,
        resolved_speaker=resolved,
        voice_data=voice_data,
        language=voice_data.get("language") or tts_config.get("language"),
        engine_id=project_manager._pronunciation_engine_id(voice_data),
        supports_phonetic_hint=False,
    )
    return {
        **resolution,
        "context": {
            "speaker": speaker,
            "resolved_speaker": resolved,
            "voice_id": (
                voice_data.get("library_voice_id")
                or voice_data.get("adapter_id")
                or voice_data.get("voice")
                or voice_data.get("clone_backend")
                or voice_data.get("type")
            ),
            "language": voice_data.get("language") or tts_config.get("language"),
            "engine_id": project_manager._pronunciation_engine_id(voice_data),
        },
    }


def _generate_pronunciation_audio_preview(
    *,
    chunk_index: int,
    resolution: dict,
) -> dict:
    chunks = project_manager.load_chunks()
    chunk = chunks[chunk_index]
    voice_config = {}
    if Path(VOICE_CONFIG_PATH).is_file():
        voice_config = json.loads(Path(VOICE_CONFIG_PATH).read_text(encoding="utf-8"))
    context = resolution["context"]
    resolved_speaker = str(context["resolved_speaker"] or "")
    generation_chunk, _continuity = project_manager._chunk_with_spoken_continuity(
        chunks,
        chunk_index,
        bind=True,
    )
    instruction = str(generation_chunk.get("effective_instruct") or "")
    fish_instruction = str(
        generation_chunk.get("effective_fish_instruct") or instruction
    )
    preview_fingerprint = fingerprint_value(
        {
            "pronunciation_request_fingerprint": resolution["receipt"][
                "request_fingerprint"
            ],
            "resolved_speaker": resolved_speaker,
            "voice": voice_config.get(resolved_speaker, {}),
            "instruction": instruction,
            "fish_instruction": fish_instruction,
        }
    )
    root = Path(ROOT_DIR).expanduser().resolve()
    destination = root / "pronunciation_previews" / f"{preview_fingerprint}.wav"
    temporary = root / f".pronunciation-preview-{preview_fingerprint}.wav"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.unlink(missing_ok=True)
    engine = project_manager.get_engine()
    if engine is None:
        raise PronunciationRegistryError(
            "pronunciation_preview_engine_unavailable",
            "The speech engine could not be initialized for pronunciation preview.",
        )
    generation_kwargs = {}
    try:
        parameters = inspect.signature(engine.generate_voice).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "fish_render_plan" in parameters:
        generation_kwargs["fish_render_plan"] = None
    if "fish_instruction" in parameters:
        generation_kwargs["fish_instruction"] = fish_instruction
    try:
        success = engine.generate_voice(
            resolution["synthesis_text"],
            instruction,
            resolved_speaker,
            voice_config,
            str(temporary),
            **generation_kwargs,
        )
        if not success or not temporary.is_file():
            raise PronunciationRegistryError(
                "pronunciation_preview_generation_failed",
                "The speech engine did not produce a pronunciation preview.",
            )
        validation = validate_audio_file(temporary, format_hint="wav")
        os.replace(temporary, destination)
    except PronunciationRegistryError:
        raise
    except Exception as exc:
        raise PronunciationRegistryError(
            "pronunciation_preview_generation_failed",
            f"Pronunciation preview failed: {exc}",
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "preview_fingerprint": preview_fingerprint,
        "audio_url": (
            f"/api/pronunciation-registry/previews/{preview_fingerprint}"
        ),
        "audio_sha256": validation["sha256"],
        "audio_size_bytes": validation["size_bytes"],
        "audio_duration_ms": validation["duration_ms"],
    }


@app.get("/api/pronunciation-registry")
async def get_pronunciation_registry():
    try:
        return _pronunciation_registry_status()
    except PronunciationRegistryError as exc:
        _raise_pronunciation_http_error(exc)


@app.post("/api/pronunciation-registry/preview")
async def preview_pronunciation(request: PronunciationPreviewRequest):
    try:
        chunks = project_manager.load_chunks()
        registry = load_pronunciation_registry(ROOT_DIR)
        if request.candidate_entry is not None:
            try:
                candidate_index = int(request.candidate_entry.get("chunk_index"))
            except (TypeError, ValueError) as exc:
                raise PronunciationRegistryError(
                    "pronunciation_preview_chunk_mismatch",
                    "The candidate pronunciation must identify the previewed chunk.",
                ) from exc
            if candidate_index != request.chunk_index:
                raise PronunciationRegistryError(
                    "pronunciation_preview_chunk_mismatch",
                    "The candidate pronunciation must target the previewed chunk.",
                )
            registry = upsert_pronunciation_entry(
                registry,
                request.candidate_entry,
                chunks=chunks,
            )
        resolution = _preview_pronunciation(
            chunk_index=request.chunk_index,
            registry=registry,
        )
        audio_preview = (
            _generate_pronunciation_audio_preview(
                chunk_index=request.chunk_index,
                resolution=resolution,
            )
            if request.generate_audio
            else None
        )
        return {
            "status": "ready",
            "registry_fingerprint": registry["registry_fingerprint"],
            "audio_preview": audio_preview,
            **resolution,
        }
    except PronunciationRegistryError as exc:
        _raise_pronunciation_http_error(exc)


@app.get("/api/pronunciation-registry/previews/{preview_fingerprint}")
async def get_pronunciation_preview_audio(preview_fingerprint: str):
    if not re.fullmatch(r"[0-9a-f]{64}", preview_fingerprint):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "pronunciation_preview_id_invalid",
                "message": "Pronunciation preview ID is invalid.",
            },
        )
    path = (
        Path(ROOT_DIR).expanduser().resolve()
        / "pronunciation_previews"
        / f"{preview_fingerprint}.wav"
    )
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "pronunciation_preview_missing",
                "message": "Pronunciation preview was not found.",
            },
        )
    return FileResponse(path, filename=path.name, media_type="audio/wav")


@app.post("/api/pronunciation-registry/entries")
async def upsert_pronunciation(request: PronunciationUpsertRequest):
    try:
        before = load_pronunciation_registry(ROOT_DIR)
        if (
            request.expected_registry_fingerprint
            and request.expected_registry_fingerprint
            != before["registry_fingerprint"]
        ):
            raise PronunciationRegistryError(
                "pronunciation_registry_changed",
                "Pronunciation entries changed before this save. Reload and try again.",
            )
        after = upsert_pronunciation_entry(
            before,
            request.entry,
            chunks=project_manager.load_chunks(),
        )
        if after["registry_fingerprint"] == before["registry_fingerprint"]:
            return {
                "status": "unchanged",
                "registry": before,
                "audio_invalidation": None,
            }
        at_utc = _utc_now_text()
        operation_id = "pronunciation_" + fingerprint_value(
            {
                "operation": "pronunciation_upsert",
                "before": before["registry_fingerprint"],
                "after": after["registry_fingerprint"],
                "at_utc": at_utc,
            }
        )[:24]
        operation = apply_pronunciation_registry_change(
            project_root=ROOT_DIR,
            before=before,
            after=after,
            operation_id=operation_id,
            operation="pronunciation_upsert",
            at_utc=at_utc,
            reason="Reviewed pronunciation guidance changed.",
        )
        return {
            "status": "saved",
            "registry": after,
            "audio_invalidation": _audio_invalidation_summary(operation),
        }
    except PronunciationRegistryError as exc:
        _raise_pronunciation_http_error(exc)


@app.delete("/api/pronunciation-registry/entries/{pronunciation_id}")
async def delete_pronunciation(
    pronunciation_id: str,
    request: PronunciationDeleteRequest,
):
    try:
        before = load_pronunciation_registry(ROOT_DIR)
        if (
            request.expected_registry_fingerprint
            and request.expected_registry_fingerprint
            != before["registry_fingerprint"]
        ):
            raise PronunciationRegistryError(
                "pronunciation_registry_changed",
                "Pronunciation entries changed before this removal. Reload and try again.",
            )
        after = remove_pronunciation_entry(before, pronunciation_id)
        at_utc = _utc_now_text()
        operation_id = "pronunciation_" + fingerprint_value(
            {
                "operation": "pronunciation_delete",
                "pronunciation_id": pronunciation_id,
                "before": before["registry_fingerprint"],
                "after": after["registry_fingerprint"],
                "at_utc": at_utc,
            }
        )[:24]
        operation = apply_pronunciation_registry_change(
            project_root=ROOT_DIR,
            before=before,
            after=after,
            operation_id=operation_id,
            operation="pronunciation_delete",
            at_utc=at_utc,
            reason="Reviewed pronunciation guidance was removed.",
        )
        return {
            "status": "deleted",
            "registry": after,
            "audio_invalidation": _audio_invalidation_summary(operation),
        }
    except PronunciationRegistryError as exc:
        _raise_pronunciation_http_error(exc)

@app.get("/api/audiobook")
async def get_audiobook():
    if not os.path.exists(AUDIOBOOK_PATH):
        raise HTTPException(status_code=404, detail="Audiobook not found")
    return FileResponse(AUDIOBOOK_PATH, filename="audiobook.mp3", media_type="audio/mpeg")

# --- Chunk Management Endpoints ---

@app.get("/api/chunks")
async def get_chunks():
    chunks = project_manager.load_chunks()
    return chunks

class ChunkRestoreRequest(BaseModel):
    chunk: dict
    at_index: int

@app.post("/api/chunks/restore")
async def restore_chunk(request: ChunkRestoreRequest):
    """Re-insert a previously deleted chunk at a specific index."""
    chunks = project_manager.restore_chunk(request.at_index, request.chunk)
    if chunks is None:
        raise HTTPException(status_code=400, detail="Failed to restore chunk")
    return {"status": "ok", "total": len(chunks)}

@app.post("/api/chunks/{index}")
async def update_chunk(index: int, update: ChunkUpdate):
    data = update.model_dump(exclude_unset=True)
    logger.info(f"Updating chunk {index} with data: {data}")
    chunk = project_manager.update_chunk(index, data)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    logger.info(f"Chunk {index} updated, instruct is now: '{chunk.get('instruct', '')}'")
    return chunk

@app.post("/api/chunks/{index}/insert")
async def insert_chunk(index: int):
    """Insert an empty chunk after the given index."""
    chunks = project_manager.insert_chunk(index)
    if chunks is None:
        raise HTTPException(status_code=404, detail="Invalid chunk index")
    return {"status": "ok", "total": len(chunks)}

@app.delete("/api/chunks/{index}")
async def delete_chunk(index: int):
    """Delete a chunk at the given index."""
    result = project_manager.delete_chunk(index)
    if result is None:
        raise HTTPException(status_code=400, detail="Cannot delete chunk (invalid index or last remaining chunk)")
    deleted, chunks = result
    return {"status": "ok", "deleted": deleted, "total": len(chunks)}

@app.post("/api/chunks/{index}/generate")
async def generate_chunk_endpoint(
    index: int,
    background_tasks: BackgroundTasks,
    http_request: Request,
    request: Optional[ChunkGenerateRequest] = None,
):
    chunks = project_manager.load_chunks()
    if not (0 <= index < len(chunks)):
        raise HTTPException(status_code=404, detail="Invalid chunk index")
    if not chunks[index].get("text", "").strip():
        raise HTTPException(status_code=400, detail="Cannot generate audio for an empty line")
    try:
        require_regeneration_unlocked(chunks[index])
    except ApprovedAudioLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": str(exc),
                "context": {
                    "index": index,
                    "chunk_id": chunks[index].get("id", index),
                    "candidate_id": exc.candidate_id,
                },
            },
        ) from exc

    generation_seed = request.generation_seed if request else None
    replace_active = request.replace_active if request else False
    batch_request = BatchGenerateRequest(
        indices=[index],
        generation_seed=generation_seed,
        replace_active=replace_active,
        worker_count=1,
        operation_mode="single",
    )
    try:
        record, dispatch, prepared = _prepare_audio_queue_request(
            batch_request,
            mode="parallel",
            execution={"worker_count": 1},
        )
    except AudioGenerationLifecycleError as exc:
        _audio_lifecycle_http_error(exc)
    record, dispatch, disconnected = await _dispatch_audio_request(
        record,
        dispatch=dispatch,
        background_tasks=background_tasks,
        http_request=http_request,
    )
    return {
        "status": (
            "cancelled"
            if disconnected
            else ("started" if dispatch else "existing")
        ),
        "request": _public_audio_generation_request(record),
        "dispatched": dispatch,
        "duplicate": bool(prepared.get("duplicate")),
        "client_disconnected": disconnected,
    }


def _raise_approved_audio_promotion_http_error(
    exc: ApprovedAudioPromotionError,
) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": exc.code,
            "message": str(exc),
        },
    ) from exc


@app.post("/api/approved-audio/promote")
async def promote_approved_audio_endpoint(
    request: ApprovedAudioPromotionRequest,
):
    try:
        return promote_approved_adaptation_audio(
            project_root=ROOT_DIR,
            manifest_path=request.manifest_path,
            confirm_installation=request.confirm_installation,
            include_restricted=request.include_restricted,
            promote_voice_evidence=request.promote_voice_evidence,
        )
    except ApprovedAudioPromotionError as exc:
        _raise_approved_audio_promotion_http_error(exc)


@app.post("/api/approved-audio/rollback")
async def rollback_approved_audio_endpoint(
    request: ApprovedAudioRollbackRequest,
):
    try:
        return rollback_approved_adaptation_audio(
            project_root=ROOT_DIR,
            receipt_path=request.receipt_path,
            confirm_rollback=request.confirm_rollback,
        )
    except ApprovedAudioPromotionError as exc:
        _raise_approved_audio_promotion_http_error(exc)

@app.post("/api/merge")
async def merge_audio_endpoint(background_tasks: BackgroundTasks):
    # Reuse audio process state for merge if possible, or just background it
    # For simplicity, we just background it and frontend will assume it works
    # Or we can link it to process_state["audio"]

    def task():
        process_state["audio"]["running"] = True
        process_state["audio"]["cancel"] = False
        _reset_process_logs("audio")
        _append_process_log("audio", "Starting audiobook merge...")
        try:
            success, msg = project_manager.merge_audio()
            if success:
                _append_process_log("audio", f"Merge complete: {msg}")
            else:
                _append_process_log(
                    "audio",
                    f"Merge failed: {msg}",
                    level="error",
                )
        except Exception as e:
            _append_process_log(
                "audio",
                f"Merge error: {e}",
                level="error",
            )
        finally:
            process_state["audio"]["running"] = False
            process_state["audio"]["cancel"] = False

    background_tasks.add_task(task)
    return {"status": "started"}

@app.post("/api/export_audacity")
async def export_audacity_endpoint(background_tasks: BackgroundTasks):
    if process_state["audacity_export"]["running"]:
        raise HTTPException(status_code=400, detail="Audacity export already running")

    def task():
        process_state["audacity_export"]["running"] = True
        process_state["audacity_export"]["logs"] = ["Starting Audacity export..."]
        try:
            success, msg = project_manager.export_audacity()
            if success:
                process_state["audacity_export"]["logs"].append(f"Export complete: {msg}")
            else:
                process_state["audacity_export"]["logs"].append(f"Export failed: {msg}")
        except Exception as e:
            process_state["audacity_export"]["logs"].append(f"Export error: {e}")
        finally:
            process_state["audacity_export"]["running"] = False

    background_tasks.add_task(task)
    return {"status": "started"}

@app.get("/api/export_audacity")
async def get_audacity_export():
    zip_path = os.path.join(ROOT_DIR, "audacity_export.zip")
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="Audacity export not found. Generate it first.")
    return FileResponse(zip_path, filename="audacity_export.zip", media_type="application/zip")

class M4bExportRequest(BaseModel):
    per_chunk_chapters: bool = False
    title: str = ""
    author: str = ""
    narrator: str = ""
    year: str = ""
    description: str = ""

@app.post("/api/merge_m4b")
async def merge_m4b_endpoint(request: M4bExportRequest, background_tasks: BackgroundTasks):
    if process_state["m4b_export"]["running"]:
        raise HTTPException(status_code=400, detail="M4B export already running")

    def task():
        process_state["m4b_export"]["running"] = True
        process_state["m4b_export"]["logs"] = ["Starting M4B export..."]
        try:
            meta = {
                "title": request.title,
                "author": request.author,
                "narrator": request.narrator,
                "year": request.year,
                "description": request.description,
                "cover_path": os.path.join(ROOT_DIR, "m4b_cover.jpg") if os.path.exists(os.path.join(ROOT_DIR, "m4b_cover.jpg")) else "",
            }
            success, msg = project_manager.merge_m4b(per_chunk_chapters=request.per_chunk_chapters, metadata=meta)
            if success:
                process_state["m4b_export"]["logs"].append(f"Export complete: {msg}")
            else:
                process_state["m4b_export"]["logs"].append(f"Export failed: {msg}")
        except Exception as e:
            process_state["m4b_export"]["logs"].append(f"Export error: {e}")
        finally:
            process_state["m4b_export"]["running"] = False

    background_tasks.add_task(task)
    return {"status": "started"}

@app.get("/api/audiobook_m4b")
async def get_audiobook_m4b():
    if not os.path.exists(M4B_PATH):
        raise HTTPException(status_code=404, detail="M4B audiobook not found. Export it first.")
    return FileResponse(M4B_PATH, filename="audiobook.m4b", media_type="audio/mp4")

@app.post("/api/m4b_cover")
async def upload_m4b_cover(file: UploadFile = File(...)):
    """Upload a cover image for M4B export."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    cover_path = os.path.join(ROOT_DIR, "m4b_cover.jpg")
    content = await file.read(MAX_EXPORT_COVER_BYTES + 1)
    if (
        len(content) > MAX_EXPORT_COVER_BYTES
        or detect_export_cover_media_type(content) is None
    ):
        raise HTTPException(
            status_code=400,
            detail="Cover must be a JPEG, PNG, or WebP image up to 10 MB",
        )
    with open(cover_path, "wb") as f:
        f.write(content)
    return {"status": "uploaded", "path": cover_path}

@app.delete("/api/m4b_cover")
async def delete_m4b_cover():
    """Remove the uploaded cover image."""
    cover_path = os.path.join(ROOT_DIR, "m4b_cover.jpg")
    if os.path.exists(cover_path):
        os.remove(cover_path)
    return {"status": "removed"}

def _audio_queue_chunk_ids(indices: List[int]) -> list[str]:
    chunks = project_manager.load_chunks()
    result = []
    for index in indices:
        if 0 <= index < len(chunks):
            result.append(f"chunk:{chunks[index].get('id', index)}")
        else:
            result.append(f"chunk:index:{index}")
    return result


def _audio_lifecycle_http_error(exc: AudioGenerationLifecycleError) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": exc.code,
            "message": str(exc),
            "context": copy.deepcopy(exc.context),
        },
    ) from exc


def _public_audio_generation_request(record: dict) -> dict:
    manifest = dict(record.get("manifest") or {})
    return {
        "request_id": record.get("request_id"),
        "request_fingerprint": record.get("request_fingerprint"),
        "operation_id": record.get("operation_id"),
        "state": record.get("state"),
        "mode": manifest.get("mode"),
        "operation_mode": manifest.get("operation_mode"),
        "indices": [
            int(item.get("index"))
            for item in manifest.get("chunks", [])
            if isinstance(item, dict)
        ],
        "attempt_count": int(record.get("attempt_count") or 0),
        "cancel_requested": bool(record.get("cancel_requested")),
        "replacement_request_id": record.get("replacement_request_id"),
        "replaces_request_id": record.get("replaces_request_id"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "terminal_reason": record.get("terminal_reason"),
        "terminal_summary": copy.deepcopy(record.get("terminal_summary")),
        "terminal_receipt_fingerprint": record.get(
            "terminal_receipt_fingerprint"
        ),
        "last_error": record.get("last_error"),
        "progress": copy.deepcopy(record.get("progress") or {}),
    }


def _audio_manifest_request(record: dict) -> dict:
    manifest = dict(record.get("manifest") or {})
    return {
        "indices": [
            int(item["index"])
            for item in manifest.get("chunks", [])
            if isinstance(item, dict) and "index" in item
        ],
        "mode": manifest.get("mode") or "parallel",
        "operation_mode": manifest.get("operation_mode"),
        "generation_seed": manifest.get("generation_seed"),
        "plan_fingerprint": manifest.get("plan_fingerprint"),
        "chunks_fingerprint": manifest.get("chunks_fingerprint"),
        "execution": copy.deepcopy(dict(manifest.get("execution") or {})),
    }


def _prepare_audio_queue_request(
    request: BatchGenerateRequest,
    *,
    mode: str,
    execution: dict,
) -> tuple[dict, bool, dict]:
    manifest = project_manager.build_audio_generation_manifest(
        request.indices,
        mode=mode,
        operation_mode=request.operation_mode,
        generation_seed=request.generation_seed,
        plan_fingerprint=request.plan_fingerprint,
        chunks_fingerprint=request.chunks_fingerprint,
    )
    manifest["execution"] = copy.deepcopy(execution)
    prepared = prepare_audio_generation_request(
        ROOT_DIR,
        manifest,
        operation_id=request.operation_id,
        replace_active=request.replace_active,
    )
    record = prepared["record"]
    dispatch = bool(prepared["dispatch_required"])
    return record, dispatch, prepared


async def _dispatch_audio_request(
    record: dict,
    *,
    dispatch: bool,
    background_tasks: BackgroundTasks,
    http_request: Request,
) -> tuple[dict, bool, bool]:
    """Dispatch only after the accepting client is still connected.

    Once accepted, the request is persistent and deliberately independent of
    the browser connection. A disconnect observed before acceptance cancels
    the prepared request and prevents a worker from being scheduled.
    """
    if not dispatch:
        return record, False, False
    if await http_request.is_disconnected():
        cancelled = cancel_audio_generation_request(
            ROOT_DIR,
            record["request_id"],
            reason="client_disconnected_before_acceptance",
        )
        return cancelled, False, True
    background_tasks.add_task(
        _run_audio_request_controller,
        record["request_id"],
    )
    return record, True, False


def _activate_audio_queue_state(record: dict, owner_token: str) -> None:
    manifest = dict(record.get("manifest") or {})
    execution = dict(manifest.get("execution") or {})
    indices = [
        int(item["index"])
        for item in manifest.get("chunks", [])
        if isinstance(item, dict) and "index" in item
    ]
    process_state["audio"].update(
        {
            "running": True,
            "cancel": False,
            "operation_id": record.get("operation_id") or record["request_id"],
            "request_id": record["request_id"],
            "request_fingerprint": record["request_fingerprint"],
            "owner_token": owner_token,
            "replacement_request_id": record.get("replacement_request_id"),
            "mode": manifest.get("operation_mode") or manifest.get("mode"),
            "plan_fingerprint": manifest.get("plan_fingerprint"),
            "chunks_fingerprint": manifest.get("chunks_fingerprint"),
            "queued_chunk_ids": _audio_queue_chunk_ids(indices),
            "total_count": len(indices),
            "completed_count": 0,
            "failed_count": 0,
            "cancelled_count": 0,
            "worker_limit": execution.get("worker_count")
            or execution.get("batch_size")
            or 1,
            "started_at": record.get("started_at") or _utc_now_text(),
            "finished_at": None,
            "last_error": None,
            "generation_seed": manifest.get("generation_seed"),
        }
    )
    _reset_process_logs("audio")
    _append_process_log(
        "audio",
        f"Starting exact-once audio request {record['request_id']}.",
    )


def _update_audio_queue_progress(completed: int, failed: int) -> None:
    state = process_state["audio"]
    state["completed_count"] = int(completed)
    state["failed_count"] = int(failed)
    total = int(state.get("total_count") or 0)
    _append_process_log(
        "audio",
        f"Progress: {completed + failed}/{total} ({completed} done, {failed} failed)",
        level="progress",
    )


def _finish_audio_queue(
    *,
    completed: int,
    failed: int,
    cancelled: int,
    error: str | None = None,
) -> None:
    state = process_state["audio"]
    state.update(
        {
            "running": False,
            "cancel": False,
            "completed_count": int(completed),
            "failed_count": int(failed),
            "cancelled_count": int(cancelled),
            "finished_at": _utc_now_text(),
            "last_error": error,
            "owner_token": None,
        }
    )


def _run_audio_request_controller(request_id: str) -> None:
    owner_token = None
    record = None
    try:
        record = load_audio_generation_request(ROOT_DIR, request_id)
        claimed = claim_audio_generation_request(
            ROOT_DIR,
            request_id,
            expected_request_fingerprint=record["request_fingerprint"],
            owner_process_id=os.getpid(),
        )
        if claimed["state"] in AUDIO_REQUEST_TERMINAL_STATES:
            return
        owner_token = str(claimed["owner_token"])
        _activate_audio_queue_state(claimed, owner_token)
        manifest_request = _audio_manifest_request(claimed)
        indices = manifest_request["indices"]
        contexts = {}
        for item in claimed["manifest"]["chunks"]:
            context = audio_generation_request_context(
                ROOT_DIR,
                request_id,
                owner_token,
                item["chunk_key"],
            )
            context["manifest_request"] = copy.deepcopy(manifest_request)
            contexts[int(item["index"])] = context

        def progress_callback(completed, failed, _total):
            _update_audio_queue_progress(completed, failed)

        def cancel_check():
            return audio_generation_should_cancel(
                ROOT_DIR,
                request_id,
                owner_token,
            )

        execution = dict(claimed["manifest"].get("execution") or {})
        mode = str(claimed["manifest"].get("mode") or "parallel")
        operation_mode = str(
            claimed["manifest"].get("operation_mode") or ""
        )
        def supported_kwargs(method, values):
            target = getattr(method, "side_effect", None)
            target = target if callable(target) else method
            parameters = inspect.signature(target).parameters
            if any(
                item.kind == inspect.Parameter.VAR_KEYWORD
                for item in parameters.values()
            ):
                return dict(values)
            return {
                key: value
                for key, value in values.items()
                if key in parameters
            }

        if operation_mode == "single" and len(indices) == 1:
            index = indices[0]
            method = project_manager.generate_chunk_audio
            parameters = inspect.signature(method).parameters
            kwargs = {
                "generation_seed": claimed["manifest"].get(
                    "generation_seed"
                )
            }
            if (
                not hasattr(method, "mock_calls")
                and "generation_context" in parameters
            ):
                kwargs["generation_context"] = contexts[index]
            success, message = method(index, **kwargs)
            results = {
                "completed": [index] if success else [],
                "failed": [] if success else [(index, message)],
                "cancelled": 0,
            }
            progress_callback(
                len(results["completed"]),
                len(results["failed"]),
                1,
            )
        elif mode == "fast":
            method = project_manager.generate_chunks_batch
            kwargs = {
                "batch_group_by_type": bool(
                    execution.get("group_by_type", False)
                ),
                "cancel_check": cancel_check,
                "generation_contexts": contexts,
            }
            kwargs = supported_kwargs(method, kwargs)
            results = method(
                indices,
                claimed["manifest"].get("generation_seed")
                if claimed["manifest"].get("generation_seed") is not None
                else -1,
                int(execution.get("batch_size") or 4),
                progress_callback,
                **kwargs,
            )
        else:
            method = project_manager.generate_chunks_parallel
            kwargs = {
                "cancel_check": cancel_check,
                "generation_contexts": contexts,
            }
            if claimed["manifest"].get("generation_seed") is not None:
                kwargs["generation_seed"] = claimed["manifest"].get(
                    "generation_seed"
                )
            kwargs = supported_kwargs(method, kwargs)
            results = method(
                indices,
                int(execution.get("worker_count") or 1),
                progress_callback,
                **kwargs,
            )
        for index, error in results.get("failed", []):
            context = contexts.get(int(index))
            if not context:
                continue
            try:
                record_audio_generation_chunk_failed(
                    ROOT_DIR,
                    request_id,
                    owner_token,
                    context["chunk_key"],
                    error=str(error),
                )
            except AudioGenerationLifecycleError:
                pass
        terminal = finalize_audio_generation_request(
            ROOT_DIR,
            request_id,
            owner_token,
        )
        _finish_audio_queue(
            completed=len(results.get("completed", [])),
            failed=len(results.get("failed", [])),
            cancelled=int(results.get("cancelled") or 0),
            error=terminal.get("last_error"),
        )
        replacement = pending_audio_generation_replacement(ROOT_DIR, request_id)
        if replacement is not None:
            _run_audio_request_controller(replacement["request_id"])
    except Exception as exc:
        logger.exception("Audio generation controller failed: %s", exc)
        if owner_token is not None:
            try:
                terminal = finalize_audio_generation_request(
                    ROOT_DIR,
                    request_id,
                    owner_token,
                    error=str(exc),
                )
                summary = terminal.get("terminal_summary") or {}
                _finish_audio_queue(
                    completed=int(summary.get("completed") or 0),
                    failed=int(summary.get("failed") or 0),
                    cancelled=int(summary.get("cancelled") or 0),
                    error=str(exc),
                )
            except Exception:
                _finish_audio_queue(
                    completed=0,
                    failed=0,
                    cancelled=0,
                    error=str(exc),
                )
        else:
            _finish_audio_queue(
                completed=0,
                failed=0,
                cancelled=0,
                error=str(exc),
            )


@app.post("/api/generate_batch")
async def generate_batch_endpoint(
    request: BatchGenerateRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    """Generate multiple chunks in parallel using configured worker count."""
    chunks = project_manager.load_chunks()
    locked = [
        {
            "index": index,
            "chunk_id": chunks[index].get("id", index),
            "candidate_id": active_approved_audio_lock(chunks[index]).get(
                "candidate_id"
            ),
        }
        for index in request.indices
        if 0 <= index < len(chunks)
        and active_approved_audio_lock(chunks[index]) is not None
    ]
    if locked:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approved_audio_batch_contains_locked_chunks",
                "message": (
                    "Approved adaptation performances cannot be included in a "
                    "TTS generation batch."
                ),
                "context": {"locked_chunks": locked},
            },
        )

    # Load worker count from config
    workers = request.worker_count or 2
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if request.worker_count is None:
                    workers = max(1, cfg.get("tts", {}).get("parallel_workers", 2))
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        record, dispatch, prepared = _prepare_audio_queue_request(
            request,
            mode="parallel",
            execution={"worker_count": workers},
        )
    except AudioGenerationLifecycleError as exc:
        _audio_lifecycle_http_error(exc)
    record, dispatch, disconnected = await _dispatch_audio_request(
        record,
        dispatch=dispatch,
        background_tasks=background_tasks,
        http_request=http_request,
    )
    return {
        "status": (
            "cancelled"
            if disconnected
            else ("started" if dispatch else "existing")
        ),
        "operation_id": record.get("operation_id") or record["request_id"],
        "workers": workers,
        "total_chunks": len(request.indices),
        "request": _public_audio_generation_request(record),
        "dispatched": dispatch,
        "duplicate": bool(prepared.get("duplicate")),
        "client_disconnected": disconnected,
    }

@app.post("/api/generate_batch_fast")
async def generate_batch_fast_endpoint(
    request: BatchGenerateRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    """Generate multiple chunks using batch TTS API with single seed. Faster but less flexible.
    Requires custom Qwen3-TTS with /generate_batch endpoint."""
    chunks = project_manager.load_chunks()
    locked = [
        {
            "index": index,
            "chunk_id": chunks[index].get("id", index),
            "candidate_id": active_approved_audio_lock(chunks[index]).get(
                "candidate_id"
            ),
        }
        for index in request.indices
        if 0 <= index < len(chunks)
        and active_approved_audio_lock(chunks[index]) is not None
    ]
    if locked:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approved_audio_batch_contains_locked_chunks",
                "message": (
                    "Approved adaptation performances cannot be included in a "
                    "TTS generation batch."
                ),
                "context": {"locked_chunks": locked},
            },
        )

    # Load batch_seed and batch_size from config
    batch_seed = -1
    batch_size = request.batch_size or 4
    batch_group_by_type = False
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                tts_cfg = cfg.get("tts", {})
                seed_val = tts_cfg.get("batch_seed")
                if seed_val is not None and seed_val != "":
                    batch_seed = int(seed_val)
                if request.batch_size is None:
                    batch_size = max(1, tts_cfg.get("parallel_workers", 4))
                batch_group_by_type = (
                    request.group_by_type
                    if request.group_by_type is not None
                    else tts_cfg.get("batch_group_by_type", False)
                )
        except (json.JSONDecodeError, ValueError):
            pass

    if request.generation_seed is not None:
        batch_seed = request.generation_seed

    effective_request = request.model_copy(
        update={"generation_seed": batch_seed if batch_seed >= 0 else None}
    )
    try:
        record, dispatch, prepared = _prepare_audio_queue_request(
            effective_request,
            mode="fast",
            execution={
                "batch_size": batch_size,
                "group_by_type": bool(batch_group_by_type),
            },
        )
    except AudioGenerationLifecycleError as exc:
        _audio_lifecycle_http_error(exc)
    record, dispatch, disconnected = await _dispatch_audio_request(
        record,
        dispatch=dispatch,
        background_tasks=background_tasks,
        http_request=http_request,
    )
    return {
        "status": (
            "cancelled"
            if disconnected
            else ("started" if dispatch else "existing")
        ),
        "operation_id": record.get("operation_id") or record["request_id"],
        "batch_seed": batch_seed,
        "batch_size": batch_size,
        "total_chunks": len(request.indices),
        "request": _public_audio_generation_request(record),
        "dispatched": dispatch,
        "duplicate": bool(prepared.get("duplicate")),
        "client_disconnected": disconnected,
    }


@app.post("/api/generate_fast_batch")
async def generate_fast_batch_alias(
    request: BatchGenerateRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
):
    return await generate_batch_fast_endpoint(
        request,
        background_tasks,
        http_request,
    )


@app.get("/api/audio-generation/requests")
async def audio_generation_request_inventory():
    return {
        "requests": [
            _public_audio_generation_request(item)
            for item in list_audio_generation_requests(ROOT_DIR)
        ]
    }


@app.get("/api/audio-generation/requests/{request_id}")
async def audio_generation_request_status(request_id: str):
    try:
        return _public_audio_generation_request(
            load_audio_generation_request(ROOT_DIR, request_id)
        )
    except AudioGenerationLifecycleError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": str(exc)}) from exc

@app.post("/api/cancel_audio")
async def cancel_audio():
    """Cancel ongoing audio generation and reset in-progress chunks."""
    active = [
        item
        for item in list_audio_generation_requests(ROOT_DIR)
        if item.get("state") in AUDIO_REQUEST_ACTIVE_STATES
        and item.get("state") != "queued_replacement"
    ]
    if active:
        request_id = str(process_state["audio"].get("request_id") or active[-1]["request_id"])
        try:
            record = cancel_audio_generation_request(ROOT_DIR, request_id)
        except AudioGenerationLifecycleError as exc:
            _audio_lifecycle_http_error(exc)
        process_state["audio"]["cancel"] = True
        _append_process_log(
            "audio",
            "[CANCEL] Cancellation requested",
            level="warning",
        )
        return {
            "status": "cancelling" if record["state"] == "cancelling" else record["state"],
            "request": _public_audio_generation_request(record),
        }

    if process_state["audio"].get("running"):
        process_state["audio"]["cancel"] = True
        _append_process_log(
            "audio",
            "[CANCEL] Cancellation requested",
            level="warning",
        )
        return {"status": "cancelling"}

    reset_count = 0
    chunks = project_manager.load_chunks()
    if chunks:
        for chunk in chunks:
            if chunk.get("status") == "generating":
                chunk["status"] = "pending"
                chunk["audio_state"] = (
                    "stale" if chunk.get("stale_audio_path") else "pending"
                )
                reset_count += 1
        if reset_count:
            project_manager.save_chunks(chunks)
    if reset_count:
        process_state["audio"]["cancelled_count"] = reset_count
        process_state["audio"]["finished_at"] = _utc_now_text()
    return {"status": "not_running", "reset_chunks": reset_count}


@app.post("/api/cancel_generation")
async def cancel_generation_alias():
    return await cancel_audio()

## ── Saved Scripts ──────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    """Make a string safe for use as a filename."""
    name = re.sub(r'[^\w\- ]', '', name).strip()
    name = re.sub(r'\s+', '_', name)
    return name.lower()

@app.get("/api/scripts")
async def list_saved_scripts():
    """List all saved scripts in the scripts/ directory."""
    return list_saved_script_records(
        SCRIPTS_DIR
    )

class ScriptSaveRequest(BaseModel):
    name: str

@app.post("/api/scripts/save")
async def save_script(request: ScriptSaveRequest):
    """Save the current script and available companions."""
    if not os.path.exists(SCRIPT_PATH):
        raise HTTPException(
            status_code=404,
            detail=(
                "No annotated script to save. "
                "Generate a script first."
            ),
        )

    safe_name = _sanitize_name(
        request.name
    )

    if not safe_name:
        raise HTTPException(
            status_code=400,
            detail="Invalid script name.",
        )

    save_script_bundle(
        scripts_dir=SCRIPTS_DIR,
        name=safe_name,
        script_path=SCRIPT_PATH,
        voice_config_path=(
            VOICE_CONFIG_PATH
        ),
        metadata_path=(
            current_metadata_path(
                SCRIPT_PATH
            )
        ),
    )

    logger.info(
        f"Script saved as '{safe_name}'"
    )

    return {
        "status": "saved",
        "name": safe_name,
    }

class ScriptLoadRequest(BaseModel):
    name: str

@app.post("/api/scripts/load")
async def load_script(request: ScriptLoadRequest):
    """Load a saved script and available companions."""
    if process_state["audio"]["running"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot load a script while "
                "audio generation is running."
            ),
        )

    src = os.path.join(
        SCRIPTS_DIR,
        f"{request.name}.json",
    )

    if not os.path.exists(src):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Saved script "
                f"'{request.name}' not found."
            ),
        )

    load_script_bundle(
        scripts_dir=SCRIPTS_DIR,
        name=request.name,
        script_path=SCRIPT_PATH,
        voice_config_path=(
            VOICE_CONFIG_PATH
        ),
        metadata_path=(
            current_metadata_path(
                SCRIPT_PATH
            )
        ),
        chunks_path=CHUNKS_PATH,
    )

    logger.info(
        f"Script '{request.name}' loaded"
    )

    return {
        "status": "loaded",
        "name": request.name,
    }

@app.delete("/api/scripts/{name}")
async def delete_script(name: str):
    """Delete a saved script and its companions."""
    filepath = os.path.join(
        SCRIPTS_DIR,
        f"{name}.json",
    )

    if not os.path.exists(filepath):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Saved script "
                f"'{name}' not found."
            ),
        )

    delete_script_bundle(
        scripts_dir=SCRIPTS_DIR,
        name=name,
    )

    logger.info(
        f"Script '{name}' deleted"
    )

    return {
        "status": "deleted",
        "name": name,
    }

## ── Voice Designer ──────────────────────────────────────────────

DESIGNED_VOICES_MANIFEST = os.path.join(DESIGNED_VOICES_DIR, "manifest.json")
_VOICE_DESIGN_SAVE_LOCK = threading.RLock()

def _load_manifest(path):
    """Load a JSON manifest file, returning [] on missing or corrupt file."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return []

def _save_manifest(path, manifest):
    """Write a JSON manifest file."""
    atomic_json_write(manifest, path)

@app.post("/api/voice_design/accent_status")
async def voice_design_accent_status(
    request: AccentPipelineStatusRequest,
):
    # Describe the accent path without initializing TTS models.
    pipeline = detect_accent_pipeline(
        request.description
    )
    output_language = normalize_output_language(
        request.output_language
    )

    if pipeline is None:
        return {
            "status": "ordinary_design",
            "accent_detected": False,
            "accent_label": None,
            "native_language": None,
            "output_language": output_language,
            "sequence": "ordinary_design",
            "stages": [
                {
                    "id": "ordinary_design",
                    "label": "VoiceDesign",
                    "language": output_language,
                }
            ],
        }

    return {
        "status": "accent_pipeline",
        "accent_detected": True,
        "accent_label": pipeline["label"],
        "native_language": pipeline["language"],
        "output_language": output_language,
        "sequence": (
            "native_seed_design -> output_clone"
        ),
        "stages": [
            {
                "id": "native_seed_design",
                "label": "Native-language VoiceDesign",
                "language": pipeline["language"],
            },
            {
                "id": "output_clone",
                "label": "Output-language clone",
                "language": output_language,
            },
        ],
    }


@app.post("/api/voice_design/preview")
async def voice_design_preview(request: VoiceDesignPreviewRequest):
    """Generate a preview voice from a text description."""
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    try:
        wav_path, sr = engine.generate_voice_design(
            description=request.description,
            sample_text=request.sample_text,
            language=request.language,
        )
        generated = Path(wav_path).expanduser().resolve()
        preview_dir = Path(DESIGNED_VOICES_DIR, "previews").resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / generated.name
        if generated != preview_path:
            pending = preview_path.with_name(
                f".{preview_path.name}.voice-design-{secrets.token_hex(6)}"
            )
            try:
                shutil.copy2(generated, pending)
                os.replace(pending, preview_path)
            finally:
                pending.unlink(missing_ok=True)
        filename = preview_path.name
        accent = detect_accent_pipeline(request.description)
        accent_applied = accent is not None and bool(getattr(engine, "_use_mlx", False))
        accent_pipeline = {
            "applied": accent_applied,
            "label": accent["label"] if accent is not None else None,
            "native_language": accent["language"] if accent_applied else None,
            "output_language": normalize_output_language(request.language),
            "sequence": (
                "native_seed_design -> output_clone"
                if accent_applied
                else "direct_voice_design"
            ),
        }
        return {
            "status": "ok",
            "audio_url": f"/designed_voices/previews/{filename}",
            "accent_pipeline": accent_pipeline,
        }
    except Exception as e:
        logger.error(f"Voice design preview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/voice_design/range-preview")
async def voice_design_range_preview(request: VoiceDesignRangePreviewRequest):
    """Design one identity, then audition it across four Fish deliveries."""
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")
    preview_dir = Path(DESIGNED_VOICES_DIR, "previews").resolve()
    preview_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = engine.generate_voice_design_range_preview(
            description=request.description,
            persona_context=request.persona_context,
            sample_text=request.sample_text,
            language=request.language,
            output_dir=preview_dir,
        )
        audition_path = Path(result["audio_path"]).expanduser().resolve()
        identity_path = Path(result["identity_seed_path"]).expanduser().resolve()
        for path in (audition_path, identity_path):
            try:
                path.relative_to(preview_dir)
            except ValueError as exc:
                raise RuntimeError(
                    "VoiceDesign range preview escaped the project preview directory."
                ) from exc
            if not path.is_file():
                raise RuntimeError(f"VoiceDesign range preview is missing: {path.name}")
        accent = detect_accent_pipeline(request.description)
        accent_applied = accent is not None and bool(getattr(engine, "_use_mlx", False))
        return {
            "status": "ok",
            "audio_url": f"/designed_voices/previews/{audition_path.name}",
            "clone_source_url": f"/designed_voices/previews/{identity_path.name}",
            "clone_source_text": result["identity_seed_text"],
            "identity_backend": (
                "mlx_qwen3_voice_design"
                if bool(getattr(engine, "_use_mlx", False))
                else "pytorch_qwen3_voice_design"
            ),
            "delivery_backend": result["delivery_backend"],
            "persona_context_applied": bool(request.persona_context.strip()),
            "sequence": result["sequence"],
            "accent_pipeline": {
                "applied": accent_applied,
                "label": accent["label"] if accent is not None else None,
                "native_language": accent["language"] if accent_applied else None,
                "output_language": normalize_output_language(request.language),
                "sequence": (
                    "native_seed_design -> output_clone"
                    if accent_applied
                    else "direct_voice_design"
                ),
            },
        }
    except Exception as exc:
        code = getattr(exc, "code", None)
        logger.error("Voice design range preview failed: %s", exc)
        raise HTTPException(
            status_code=409 if isinstance(code, str) and code.startswith("fish_") else 500,
            detail=(
                {"code": code, "message": str(exc)}
                if code
                else str(exc)
            ),
        ) from exc


@app.post("/api/voice_design/save")
async def voice_design_save(request: VoiceDesignSaveRequest):
    """Save a preview as a project Voice or an explicitly reusable Voice."""
    preview_filename = str(request.preview_file or "").strip()
    if (
        not preview_filename
        or preview_filename != os.path.basename(preview_filename)
        or "\\" in preview_filename
        or not preview_filename.casefold().endswith(".wav")
    ):
        raise HTTPException(status_code=400, detail="Invalid preview file")
    previews_dir = os.path.join(DESIGNED_VOICES_DIR, "previews")
    preview_path = os.path.join(previews_dir, preview_filename)

    if not os.path.isfile(preview_path):
        raise HTTPException(status_code=404, detail="Preview file not found")

    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    target_dir = (
        os.path.join(LEGACY_ROOT_DIR, "designed_voices")
        if request.scope == "reusable"
        else DESIGNED_VOICES_DIR
    )
    os.makedirs(target_dir, exist_ok=True)
    target_manifest = os.path.join(target_dir, "manifest.json")
    with _VOICE_DESIGN_SAVE_LOCK:
        for _attempt in range(8):
            voice_id = f"{safe_name}_{time.time_ns()}_{secrets.token_hex(4)}"
            dest_filename = f"{voice_id}.wav"
            dest_path = os.path.join(target_dir, dest_filename)
            try:
                with open(preview_path, "rb") as source, open(dest_path, "xb") as target:
                    shutil.copyfileobj(source, target)
                break
            except FileExistsError:
                continue
        else:
            raise HTTPException(status_code=409, detail="Could not allocate a unique Voice file")

        try:
            manifest = _load_manifest(target_manifest)
            manifest.append({
                "id": voice_id,
                "name": request.name,
                "description": request.description,
                "sample_text": request.sample_text,
                "filename": dest_filename,
            })
            _save_manifest(target_manifest, manifest)
        except Exception:
            try:
                os.unlink(dest_path)
            except FileNotFoundError:
                pass
            raise

    logger.info(f"Designed voice saved: '{request.name}' as {dest_filename}")
    return {"status": "saved", "voice_id": voice_id, "scope": request.scope}

@app.get("/api/voice_design/list")
async def voice_design_list():
    """List all saved designed voices."""
    return _load_manifest(DESIGNED_VOICES_MANIFEST)

@app.delete("/api/voice_design/{voice_id}")
async def voice_design_delete(voice_id: str):
    """Delete a saved designed voice."""
    manifest = _load_manifest(DESIGNED_VOICES_MANIFEST)
    entry = next((v for v in manifest if v["id"] == voice_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Voice not found")

    # Delete WAV file
    wav_path = os.path.join(DESIGNED_VOICES_DIR, entry["filename"])
    if os.path.exists(wav_path):
        os.remove(wav_path)

    # Remove from manifest
    manifest = [v for v in manifest if v["id"] != voice_id]
    _save_manifest(DESIGNED_VOICES_MANIFEST, manifest)

    logger.info(f"Designed voice deleted: {voice_id}")
    return {"status": "deleted", "voice_id": voice_id}

## ── Clone Voice Uploads ───────────────────────────────────────

CLONE_VOICES_MANIFEST = os.path.join(CLONE_VOICES_DIR, "manifest.json")
ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg"}

@app.post("/api/clone_voices/controlled_preview")
async def controlled_clone_preview(
    request: ControlledClonePreviewRequest,
):
    capabilities = _current_voice_backend_capabilities()
    expressive = capabilities.get("expressive_clone", {})
    if not (
        expressive.get("supported") is True
        or expressive.get("experimental_preview_available") is True
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "controlled_clone_preview_unavailable",
                "message": (
                    "The Qwen instruction-controlled clone preview is unavailable."
                ),
            },
        )
    engine = project_manager.get_engine()
    if engine is None or not getattr(engine, "_use_mlx", False):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "controlled_clone_preview_unavailable",
                "message": (
                    "Controlled clone preview requires the Apple Silicon "
                    "MLX runtime."
                ),
            },
        )
    try:
        mlx_backend = engine._init_mlx()
        result = generate_controlled_clone_preview(
            root_dir=ROOT_DIR,
            ref_audio=request.ref_audio,
            ref_text=request.ref_text,
            text=request.text,
            instruct=request.instruct,
            character_style=request.character_style,
            temperature=request.temperature,
            top_k=request.top_k,
            top_p=request.top_p,
            repetition_penalty=request.repetition_penalty,
            max_tokens=request.max_tokens,
            seed=request.seed,
            generator=mlx_backend.generate_instruction_controlled_clone,
        )
        registration = register_controlled_clone_preview(
            speaker=request.speaker,
            preview_fingerprint=result["preview_fingerprint"],
            configuration_fingerprint=result[
                "configuration_fingerprint"
            ],
        )
        return {
            **result,
            "approval_expires_in_seconds": registration[
                "expires_in_seconds"
            ],
        }
    except ControlledClonePreviewError as exc:
        _raise_controlled_clone_preview_http_error(exc)
        raise AssertionError("unreachable")
    except ControlledCloneApprovalValidationError as exc:
        _raise_controlled_clone_approval_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/clone_voices/controlled_preview/confirm")
async def confirm_controlled_clone_preview_route(
    request: ControlledClonePreviewConfirmRequest,
):
    try:
        return confirm_controlled_clone_preview(
            speaker=request.speaker,
            preview_fingerprint=request.preview_fingerprint,
            configuration_fingerprint=(
                request.configuration_fingerprint
            ),
        )
    except (
        ControlledCloneApprovalConflictError,
        ControlledCloneApprovalValidationError,
    ) as exc:
        _raise_controlled_clone_approval_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/clone_voices/list")
async def clone_voices_list():
    """List all uploaded clone voices."""
    return _load_manifest(CLONE_VOICES_MANIFEST)

@app.post("/api/clone_voices/upload")
async def clone_voices_upload(file: UploadFile = File(...)):
    """Upload an audio file for voice cloning."""
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported format. Use: {', '.join(ALLOWED_AUDIO_EXTS)}")

    base_name = os.path.splitext(file.filename)[0]
    safe_name = _sanitize_name(base_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    voice_id = f"{safe_name}_{int(time.time())}"
    dest_filename = f"{voice_id}{ext}"
    dest_path = os.path.join(CLONE_VOICES_DIR, dest_filename)

    async with aiofiles.open(dest_path, "wb") as out_file:
        content = await file.read()
        await out_file.write(content)

    manifest = _load_manifest(CLONE_VOICES_MANIFEST)
    manifest.append({
        "id": voice_id,
        "name": base_name,
        "filename": dest_filename,
    })
    _save_manifest(CLONE_VOICES_MANIFEST, manifest)

    logger.info(f"Clone voice uploaded: '{base_name}' as {dest_filename}")
    return {"status": "uploaded", "voice_id": voice_id, "filename": dest_filename}

@app.delete("/api/clone_voices/{voice_id}")
async def clone_voices_delete(voice_id: str):
    """Delete an uploaded clone voice."""
    manifest = _load_manifest(CLONE_VOICES_MANIFEST)
    entry = next((v for v in manifest if v["id"] == voice_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Clone voice not found")

    wav_path = os.path.join(CLONE_VOICES_DIR, entry["filename"])
    if os.path.exists(wav_path):
        os.remove(wav_path)

    manifest = [v for v in manifest if v["id"] != voice_id]
    _save_manifest(CLONE_VOICES_MANIFEST, manifest)

    logger.info(f"Clone voice deleted: {voice_id}")
    return {"status": "deleted", "voice_id": voice_id}

## ── LoRA Training ──────────────────────────────────────────────

LORA_MODELS_MANIFEST = os.path.join(LORA_MODELS_DIR, "manifest.json")

def _load_builtin_lora_manifest():
    """Load built-in LoRA manifest from HF (with local fallback). Returns ALL entries with download status."""
    entries = fetch_builtin_manifest(BUILTIN_LORA_DIR)
    result = []
    for entry in entries:
        entry = dict(entry)  # avoid mutating cached list
        local_id = entry["id"] if entry["id"].startswith("builtin_") else f"builtin_{entry['id']}"
        downloaded = is_adapter_downloaded(local_id, BUILTIN_LORA_DIR)
        entry["id"] = local_id
        entry["builtin"] = True
        entry["downloaded"] = downloaded
        entry["adapter_path"] = f"builtin_lora/{local_id}" if downloaded else None
        result.append(entry)
    return result

@app.post("/api/lora/upload_dataset")
async def lora_upload_dataset(file: UploadFile = File(...)):
    """Upload a ZIP containing WAV files and metadata.jsonl."""
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip archive")

    # Derive dataset name from ZIP filename
    dataset_name = re.sub(r'[^\w\- ]', '', os.path.splitext(file.filename)[0]).strip()
    dataset_name = re.sub(r'\s+', '_', dataset_name).lower()
    if not dataset_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name from filename")

    dataset_dir = os.path.join(LORA_DATASETS_DIR, dataset_name)
    if os.path.exists(dataset_dir):
        raise HTTPException(status_code=400, detail=f"Dataset '{dataset_name}' already exists")

    # Save ZIP temporarily, then extract
    tmp_path = os.path.join(LORA_DATASETS_DIR, f"_tmp_{dataset_name}.zip")
    try:
        async with aiofiles.open(tmp_path, "wb") as out_file:
            content = await file.read()
            await out_file.write(content)

        os.makedirs(dataset_dir, exist_ok=True)
        with zipfile.ZipFile(tmp_path, "r") as zf:
            zf.extractall(dataset_dir)

        # Check for metadata.jsonl (may be inside a subdirectory)
        metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
        if not os.path.exists(metadata_path):
            # Check one level deep
            for entry in os.listdir(dataset_dir):
                candidate = os.path.join(dataset_dir, entry, "metadata.jsonl")
                if os.path.isdir(os.path.join(dataset_dir, entry)) and os.path.exists(candidate):
                    # Move contents up
                    nested = os.path.join(dataset_dir, entry)
                    for item in os.listdir(nested):
                        shutil.move(os.path.join(nested, item), os.path.join(dataset_dir, item))
                    os.rmdir(nested)
                    metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
                    break

        if not os.path.exists(metadata_path):
            shutil.rmtree(dataset_dir)
            raise HTTPException(status_code=400, detail="ZIP must contain metadata.jsonl")

        # Count samples
        sample_count = 0
        with open(metadata_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    sample_count += 1

        logger.info(f"LoRA dataset uploaded: '{dataset_name}' ({sample_count} samples)")
        return {"status": "uploaded", "dataset_id": dataset_name, "sample_count": sample_count}

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/lora/generate_dataset")
async def lora_generate_dataset(request: LoraGenerateDatasetRequest, background_tasks: BackgroundTasks):
    """Generate a LoRA training dataset using Voice Designer.

    Generates multiple audio samples with the same voice description,
    saving them as a ready-to-train dataset.
    """
    if process_state["dataset_gen"]["running"]:
        raise HTTPException(status_code=400, detail="Dataset generation already running")

    # Build unified sample list from either format
    sample_list = []
    if request.samples:
        for s in request.samples:
            if s.text.strip():
                sample_list.append({"emotion": s.emotion.strip(), "text": s.text.strip()})
    elif request.texts:
        for t in request.texts:
            if t.strip():
                sample_list.append({"emotion": "", "text": t.strip()})

    if not sample_list:
        raise HTTPException(status_code=400, detail="Provide at least one sample text")

    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")

    dataset_dir = os.path.join(LORA_DATASETS_DIR, safe_name)
    if os.path.exists(dataset_dir):
        raise HTTPException(status_code=400, detail=f"Dataset '{safe_name}' already exists")

    total = len(sample_list)
    root_description = request.description.strip()

    def task():
        process_state["dataset_gen"]["running"] = True
        process_state["dataset_gen"]["logs"] = [
            f"Generating {total} samples with VoiceDesign..."
        ]
        try:
            engine = project_manager.get_engine()
            if not engine:
                process_state["dataset_gen"]["logs"].append("Error: TTS engine not initialized")
                return

            os.makedirs(dataset_dir, exist_ok=True)
            metadata_lines = []
            completed = 0

            for i, sample in enumerate(sample_list):
                text = sample["text"]
                emotion = sample["emotion"]
                # Build full description: root + emotion if provided
                description = f"{root_description}, {emotion}" if emotion else root_description

                process_state["dataset_gen"]["logs"].append(
                    f"[{i+1}/{total}] {('[' + emotion + '] ' if emotion else '')}\"{ text[:60]}{'...' if len(text) > 60 else ''}\""
                )
                try:
                    wav_path, sr = engine.generate_voice_design(
                        description=description,
                        sample_text=text,
                        language=request.language,
                    )
                    # Copy to dataset dir with sequential name
                    dest_filename = f"sample_{i:03d}.wav"
                    dest_path = os.path.join(dataset_dir, dest_filename)
                    shutil.copy2(wav_path, dest_path)

                    # Save first successful sample as ref.wav for consistent speaker embedding
                    if completed == 0:
                        shutil.copy2(wav_path, os.path.join(dataset_dir, "ref.wav"))

                    metadata_lines.append(json.dumps({
                        "audio_filepath": dest_filename,
                        "text": text,
                        "ref_audio": "ref.wav",
                    }, ensure_ascii=False))
                    completed += 1
                    process_state["dataset_gen"]["logs"].append(
                        f"  Saved {dest_filename}"
                    )
                except Exception as e:
                    process_state["dataset_gen"]["logs"].append(
                        f"  Failed: {e}"
                    )

            # Write metadata.jsonl
            metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
            with open(metadata_path, "w", encoding="utf-8") as f:
                f.write("\n".join(metadata_lines) + "\n")

            process_state["dataset_gen"]["logs"].append(
                f"Dataset '{safe_name}' complete: {completed}/{total} samples generated."
            )
            logger.info(f"LoRA dataset generated: '{safe_name}' ({completed} samples)")

        except Exception as e:
            process_state["dataset_gen"]["logs"].append(f"Error: {e}")
            logger.error(f"Dataset generation error: {e}")
            # Clean up partial dataset on failure
            if os.path.exists(dataset_dir):
                shutil.rmtree(dataset_dir)
        finally:
            process_state["dataset_gen"]["running"] = False

    background_tasks.add_task(task)
    return {"status": "started", "dataset_id": safe_name, "total": total}

@app.get("/api/lora/datasets")
async def lora_list_datasets():
    """List uploaded LoRA training datasets."""
    datasets = []
    if not os.path.exists(LORA_DATASETS_DIR):
        return datasets

    for name in sorted(os.listdir(LORA_DATASETS_DIR)):
        dataset_dir = os.path.join(LORA_DATASETS_DIR, name)
        if not os.path.isdir(dataset_dir):
            continue
        metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
        sample_count = 0
        if os.path.exists(metadata_path):
            with open(metadata_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        sample_count += 1
        datasets.append({"dataset_id": name, "sample_count": sample_count})
    return datasets

@app.delete("/api/lora/datasets/{dataset_id}")
async def lora_delete_dataset(dataset_id: str):
    """Delete an uploaded dataset."""
    dataset_dir = os.path.join(LORA_DATASETS_DIR, dataset_id)
    if not os.path.isdir(dataset_dir):
        raise HTTPException(status_code=404, detail="Dataset not found")

    shutil.rmtree(dataset_dir)
    logger.info(f"LoRA dataset deleted: {dataset_id}")
    return {"status": "deleted", "dataset_id": dataset_id}

@app.get("/api/voice_backend/capabilities")
async def get_voice_backend_capabilities():
    return _current_voice_backend_capabilities()


@app.post("/api/voice_backend/fish-hybrid/migrate")
async def migrate_fish_hybrid_voices(request: FishHybridMigrationRequest):
    if process_state["audio"]["running"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "fish_hybrid_migration_generation_running",
                "message": "Finish or cancel audio generation before changing hybrid Voice policy.",
            },
        )
    try:
        result = migrate_fish_hybrid_policy(
            reusable_root=LEGACY_ROOT_DIR,
            managed_projects_root=Path(PROJECTS_DATA_ROOT) / "Projects",
            active_project_root=ROOT_DIR,
            enabled=request.enabled,
            dry_run=request.dry_run,
        )
    except FishHybridMigrationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "fish_hybrid_migration_failed",
                "message": str(exc),
            },
        ) from exc
    return {
        **result,
        "fish_capability": _current_voice_backend_capabilities().get(
            "fish_s21_cloud",
            {},
        ),
    }


@app.get("/api/training_sidecar/status")
async def get_training_sidecar_status():
    try:
        return get_training_sidecar_status_payload(
            root_dir=ROOT_DIR,
        )
    except TrainingSidecarApiError as exc:
        _raise_training_sidecar_http_error(exc)
        raise AssertionError("unreachable")


@app.get("/api/training_sidecar/jobs/{job_id}")
async def get_training_sidecar_job(job_id: str):
    try:
        return get_training_sidecar_job_payload(
            root_dir=ROOT_DIR,
            job_id=job_id,
        )
    except TrainingSidecarApiError as exc:
        _raise_training_sidecar_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/training_sidecar/jobs")
async def create_training_sidecar_job(
    request: TrainingSidecarCreateJobRequest,
):
    try:
        return create_training_sidecar_job_payload(
            root_dir=ROOT_DIR,
            action=request.action,
            payload=dict(request.payload),
        )
    except TrainingSidecarApiError as exc:
        _raise_training_sidecar_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/training_sidecar/jobs/{job_id}/execute")
async def execute_training_sidecar_job(
    job_id: str,
    request: TrainingSidecarExecuteRequest,
):
    try:
        return execute_training_sidecar_job_payload(
            root_dir=ROOT_DIR,
            job_id=job_id,
            timeout=request.timeout,
        )
    except TrainingSidecarApiError as exc:
        _raise_training_sidecar_http_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/training_sidecar/import")
async def import_training_sidecar_artifact(
    request: TrainingSidecarImportRequest,
):
    try:
        return import_training_sidecar_artifact_payload(
            root_dir=ROOT_DIR,
            source_path=request.source_path,
        )
    except TrainingSidecarApiError as exc:
        _raise_training_sidecar_http_error(exc)
        raise AssertionError("unreachable")


def _require_isolated_lora_training() -> dict:
    capabilities = _current_voice_backend_capabilities()
    sidecar = capabilities.get("experimental_lora_sidecar") or {}
    if sidecar.get("training_supported") is not True:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lora_sidecar_unavailable",
                "message": (
                    "The isolated Apple Silicon LoRA trainer is unavailable. "
                    "Shared-runtime PEFT training remains intentionally disabled."
                ),
            },
        )
    return sidecar


def _run_lora_sidecar_stage(
    *,
    stage: str,
    action: str,
    payload: dict,
    timeout: float,
) -> dict:
    state = process_state["lora_training"]
    state["stage"] = stage
    _append_process_log("lora_training", f"[{stage.upper()}] Starting {stage}.")
    job = create_training_sidecar_job_payload(
        root_dir=ROOT_DIR,
        action=action,
        payload=payload,
    )
    state["job_id"] = job["job_id"]
    completed = execute_training_sidecar_job_payload(
        root_dir=ROOT_DIR,
        job_id=job["job_id"],
        timeout=timeout,
    )
    if completed.get("status") != "completed":
        raise RuntimeError(
            completed.get("error")
            or f"The isolated {stage} job did not complete."
        )
    result = completed.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(
            f"The isolated {stage} job returned no structured result."
        )
    _append_process_log("lora_training", f"[{stage.upper()}] Completed.")
    return result


def _run_lora_product_pipeline(
    *,
    request_payload: dict,
    adapter_id: str,
    dataset_relative: str,
    experiment_relative: str,
) -> None:
    state = process_state["lora_training"]
    root = Path(ROOT_DIR).resolve()
    experiment = root / experiment_relative
    training_dir = experiment / "training"
    merged_dir = experiment / "merged"
    mlx_dir = experiment / "mlx"

    def relative(path: Path) -> str:
        return path.resolve().relative_to(root).as_posix()

    try:
        train_payload = {
            "data_dir": dataset_relative,
            "output_dir": relative(training_dir),
            "device": "mps",
            "epochs": request_payload["epochs"],
            "learning_rate": request_payload["lr"],
            "gradient_accumulation_steps": request_payload[
                "gradient_accumulation_steps"
            ],
            "language": request_payload["language"],
            "max_audio_seconds": request_payload["max_audio_seconds"],
            "lora_rank": request_payload["lora_r"],
            "lora_alpha": request_payload["lora_alpha"],
            "lora_target_profile": request_payload[
                "lora_target_profile"
            ],
            "validation_fraction": request_payload[
                "validation_fraction"
            ],
            "seed": request_payload["seed"],
            "instruction_mode": request_payload["instruction_mode"],
            "checkpoint_every_epoch": True,
            "local_files_only": request_payload["local_files_only"],
        }
        if request_payload.get("max_samples") is not None:
            train_payload["max_samples"] = request_payload["max_samples"]
        training = _run_lora_sidecar_stage(
            stage="training",
            action="train_lora",
            payload=train_payload,
            timeout=max(1800.0, float(request_payload["epochs"]) * 900.0),
        )
        if training.get("status") != "completed_experimental":
            raise RuntimeError("LoRA training did not produce an experimental adapter.")
        metrics = training.get("metrics") or {}
        for epoch in metrics.get("validation_metrics") or []:
            validation = epoch.get("validation") or {}
            validation_loss = validation.get("loss")
            suffix = (
                f" validation_loss={validation_loss:.6f}"
                if isinstance(validation_loss, (int, float))
                else " validation_loss=n/a"
            )
            train_loss = epoch.get("train_loss")
            average = (
                f"{train_loss:.6f}"
                if isinstance(train_loss, (int, float))
                else "n/a"
            )
            _append_process_log(
                "lora_training",
                (
                    f"[EPOCH] {epoch.get('epoch')}/{request_payload['epochs']} "
                    f"avg_loss={average}{suffix}"
                ),
            )
        quality_gate = metrics.get("quality_gate") or {}
        if quality_gate.get("dataset_reviewed") is not True:
            _append_process_log(
                "lora_training",
                "[WARNING] Dataset samples are not all reviewed; the installed model will remain experimental and unassigned.",
                level="warning",
            )

        merge = _run_lora_sidecar_stage(
            stage="merge",
            action="merge_lora",
            payload={
                "adapter_dir": relative(training_dir),
                "output_dir": relative(merged_dir),
                "device": "mps",
                "local_files_only": request_payload["local_files_only"],
            },
            timeout=1200.0,
        )
        if merge.get("status") != "merged_experimental":
            raise RuntimeError("LoRA merge did not produce a complete checkpoint.")

        exported = _run_lora_sidecar_stage(
            stage="mlx export",
            action="export_mlx",
            payload={
                "merged_dir": relative(merged_dir),
                "output_dir": relative(mlx_dir),
                "validation_text": (
                    "The corridor was silent, but the silence would not last."
                ),
                "neutral_instruction": (
                    "Calm, precise narration with restrained curiosity. "
                    "Preserve the original speaker identity and accent."
                ),
                "expressive_instruction": (
                    "Tense, urgent narration with controlled intensity and a "
                    "slightly quicker pace. Preserve the original speaker "
                    "identity and accent."
                ),
                "q_group_size": 64,
                "q_bits": 8,
                "max_tokens": 600,
                "cleanup_merged": True,
            },
            timeout=1200.0,
        )
        if (
            exported.get("status") != "validated_experimental"
            or exported.get("technical_validation_passed") is not True
            or exported.get("production_assignment_supported") is not False
        ):
            raise RuntimeError("MLX export failed technical validation.")

        state["stage"] = "installation"
        _append_process_log(
            "lora_training",
            "[INSTALLATION] Validating and installing the MLX model.",
        )
        installed = install_training_sidecar_mlx_artifact_payload(
            root_dir=ROOT_DIR,
            source_path=relative(mlx_dir),
            adapter_id=adapter_id,
            name=request_payload["name"],
            dataset_id=request_payload["dataset_id"],
            training_metrics_path=(
                training_dir / "training_metrics.json"
            ).relative_to(root).as_posix(),
        )
        if installed.get("status") != "installed_experimental_unassigned":
            raise RuntimeError("Validated MLX model installation did not complete.")
        shutil.rmtree(mlx_dir, ignore_errors=True)
        state["stage"] = "complete"
        state["result"] = {
            "adapter_id": adapter_id,
            "adapter_path": installed.get("adapter_path"),
            "mlx_model_path": installed.get("mlx_model_path"),
            "training_output": relative(training_dir),
            "epochs_completed": metrics.get("epochs_completed"),
            "validation_metrics": metrics.get("validation_metrics"),
            "quality_gate": quality_gate,
            "production_assignment_supported": False,
        }
        _append_process_log(
            "lora_training",
            f"[DONE] Installed experimental model {adapter_id}. Production assignment remains blocked pending review.",
        )
    except Exception as exc:
        failed_stage = state.get("stage") or "unknown"
        state["stage"] = "failed"
        state["failed_stage"] = failed_stage
        state["error"] = str(exc)
        logger.exception("Isolated LoRA pipeline failed at %s", failed_stage)
        _append_process_log(
            "lora_training",
            f"[ERROR] {exc}",
            level="error",
        )
    finally:
        state["job_id"] = None
        state["running"] = False


@app.post("/api/lora/train")
async def lora_start_training(
    request: LoraTrainingRequest,
    background_tasks: BackgroundTasks,
):
    """Run the isolated MPS train, merge, MLX export, and install pipeline."""
    sidecar = _require_isolated_lora_training()
    state = process_state["lora_training"]
    if state["running"]:
        raise HTTPException(
            status_code=409,
            detail="LoRA training is already running.",
        )
    if request.batch_size != 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "The isolated MPS trainer currently requires batch size 1. "
                "Use gradient accumulation for a larger effective batch."
            ),
        )
    if sidecar.get("training_device") != "mps":
        raise HTTPException(
            status_code=409,
            detail="The validated local LoRA trainer is not available on MPS.",
        )

    safe_dataset = str(request.dataset_id or "").strip()
    if (
        not safe_dataset
        or Path(safe_dataset).name != safe_dataset
        or not re.fullmatch(r"[A-Za-z0-9_-]+", safe_dataset)
    ):
        raise HTTPException(status_code=422, detail="Invalid dataset ID.")
    root = Path(ROOT_DIR).resolve()
    dataset_root = Path(LORA_DATASETS_DIR).resolve()
    dataset_dir = (dataset_root / safe_dataset).resolve()
    try:
        dataset_dir.relative_to(dataset_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid dataset path.") from exc
    if not (dataset_dir / "metadata.jsonl").is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Dataset {safe_dataset!r} was not found or is incomplete.",
        )

    safe_name = re.sub(
        r"[^a-z0-9_-]+",
        "_",
        str(request.name or "").casefold(),
    )[:32].strip("_-")
    if not safe_name:
        raise HTTPException(status_code=422, detail="Invalid adapter name.")
    adapter_id = (
        f"{safe_name}_{int(time.time())}_{secrets.token_hex(3)}"
    )
    experiment = (
        root
        / "training_sidecar_runtime"
        / "lora_experiments"
        / adapter_id
    ).resolve()
    if experiment.exists() or (Path(LORA_MODELS_DIR) / adapter_id).exists():
        raise HTTPException(
            status_code=409,
            detail="A LoRA experiment with this ID already exists.",
        )

    if project_manager.engine is not None:
        logger.info("Unloading TTS engine before isolated LoRA training.")
        project_manager.engine = None
        gc.collect()

    _reset_process_logs("lora_training")
    state.update(
        {
            "running": True,
            "stage": "queued",
            "adapter_id": adapter_id,
            "job_id": None,
            "result": None,
            "error": None,
            "failed_stage": None,
        }
    )
    _append_process_log(
        "lora_training",
        (
            f"[QUEUED] {request.name}: isolated MPS training, merge, "
            "8-bit MLX export, and experimental install."
        ),
    )
    payload = request.model_dump()
    background_tasks.add_task(
        _run_lora_product_pipeline,
        request_payload=payload,
        adapter_id=adapter_id,
        dataset_relative=dataset_dir.relative_to(root).as_posix(),
        experiment_relative=experiment.relative_to(root).as_posix(),
    )
    return {
        "status": "started",
        "adapter_id": adapter_id,
        "stage": "queued",
        "experimental": True,
        "production_assignment_supported": False,
    }

@app.get("/api/lora/models")
async def lora_list_models():
    """List all LoRA adapters (built-in + user-trained)."""
    capabilities = _current_voice_backend_capabilities()
    models = _load_builtin_lora_manifest() + _load_manifest(LORA_MODELS_MANIFEST)
    for m in models:
        is_builtin = m.get("builtin", False)
        is_downloaded = m.get("downloaded", True)  # user-trained are always downloaded
        m["training_supported"] = capabilities[
            "lora_training_supported"
        ]
        m["inference_supported"] = capabilities[
            "lora_inference_supported"
        ]
        m["capability_reason"] = capabilities["reason"]

        if not is_downloaded:
            m["preview_audio_url"] = None
            continue

        if is_builtin:
            adapter_dir = os.path.join(BUILTIN_LORA_DIR, m["id"])
            url_prefix = f"/builtin_lora/{m['id']}"
        else:
            adapter_dir = os.path.join(LORA_MODELS_DIR, m["id"])
            url_prefix = f"/lora_models/{m['id']}"
        preview_path = os.path.join(adapter_dir, "preview_sample.wav")
        m["preview_audio_url"] = f"{url_prefix}/preview_sample.wav" if os.path.exists(preview_path) else None
    return models

@app.delete("/api/lora/models/{adapter_id}")
async def lora_delete_model(adapter_id: str):
    """Delete a trained LoRA adapter. Built-in adapters cannot be deleted."""
    builtin = _load_builtin_lora_manifest()
    if any(m["id"] == adapter_id for m in builtin):
        raise HTTPException(status_code=403, detail="Built-in adapters cannot be deleted")
    manifest = _load_manifest(LORA_MODELS_MANIFEST)
    entry = next((m for m in manifest if m["id"] == adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Adapter not found")

    # Delete adapter directory
    adapter_dir = os.path.join(LORA_MODELS_DIR, adapter_id)
    if os.path.isdir(adapter_dir):
        shutil.rmtree(adapter_dir)

    # Remove from manifest
    manifest = [m for m in manifest if m["id"] != adapter_id]
    _save_manifest(LORA_MODELS_MANIFEST, manifest)

    logger.info(f"LoRA adapter deleted: {adapter_id}")
    return {"status": "deleted", "adapter_id": adapter_id}

@app.post("/api/lora/download/{adapter_id}")
async def lora_download_builtin(adapter_id: str):
    """Download a built-in LoRA adapter from HuggingFace."""
    _require_lora_capability("inference")
    manifest = fetch_builtin_manifest(BUILTIN_LORA_DIR)
    hf_name = adapter_id.replace("builtin_", "", 1)
    entry = next((e for e in manifest if e["id"] == hf_name or e["id"] == adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown built-in adapter: {adapter_id}")

    if is_adapter_downloaded(adapter_id, BUILTIN_LORA_DIR):
        return {"status": "already_downloaded", "adapter_id": adapter_id}

    try:
        download_builtin_adapter(adapter_id, BUILTIN_LORA_DIR)
        logger.info(f"Built-in adapter downloaded: {adapter_id}")
        return {"status": "downloaded", "adapter_id": adapter_id}
    except Exception as e:
        logger.error(f"Download failed for {adapter_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/lora/test")
async def lora_test_model(request: LoraTestRequest):
    """Generate test audio using a LoRA adapter (built-in or user-trained)."""
    _require_lora_capability("inference")
    # Check both manifests
    builtin = _load_builtin_lora_manifest()
    user_trained = _load_manifest(LORA_MODELS_MANIFEST)
    all_adapters = builtin + user_trained
    entry = next((m for m in all_adapters if m["id"] == request.adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Adapter not found")

    is_builtin = entry.get("builtin", False)
    if is_builtin:
        adapter_dir = os.path.join(BUILTIN_LORA_DIR, request.adapter_id)
        audio_url_prefix = f"/builtin_lora/{request.adapter_id}"
    else:
        adapter_dir = os.path.join(LORA_MODELS_DIR, request.adapter_id)
        audio_url_prefix = f"/lora_models/{request.adapter_id}"

    if not os.path.isdir(adapter_dir):
        detail = (
            "Built-in adapter is not installed. Download it explicitly before testing."
            if is_builtin
            else "Adapter files not found"
        )
        raise HTTPException(status_code=404, detail=detail)

    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    try:
        output_filename = f"test_{request.adapter_id}_{int(time.time())}.wav"
        output_path = os.path.join(adapter_dir, output_filename)

        voice_data = {
            "type": "lora",
            "adapter_id": request.adapter_id,
            "adapter_path": adapter_dir,
        }
        voice_config = {"_lora_test_": voice_data}
        engine.generate_voice(
            text=request.text,
            instruct_text=request.instruct or "",
            speaker="_lora_test_",
            voice_config=voice_config,
            output_path=output_path,
        )

        return {
            "status": "ok",
            "audio_url": f"{audio_url_prefix}/{output_filename}",
        }
    except Exception as e:
        logger.error(f"LoRA test generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

LORA_PREVIEW_TEXT = "The ancient library stood at the crossroads of two forgotten paths, its weathered stone walls covered in ivy that had been growing for centuries."

@app.post("/api/lora/preview/{adapter_id}")
async def lora_preview(adapter_id: str):
    """Generate or return cached preview audio for a LoRA adapter."""
    _require_lora_capability("inference")
    builtin = _load_builtin_lora_manifest()
    user_trained = _load_manifest(LORA_MODELS_MANIFEST)
    all_adapters = builtin + user_trained
    entry = next((m for m in all_adapters if m["id"] == adapter_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Adapter not found")

    is_builtin = entry.get("builtin", False)
    if is_builtin:
        adapter_dir = os.path.join(BUILTIN_LORA_DIR, adapter_id)
        url_prefix = f"/builtin_lora/{adapter_id}"
    else:
        adapter_dir = os.path.join(LORA_MODELS_DIR, adapter_id)
        url_prefix = f"/lora_models/{adapter_id}"

    if not os.path.isdir(adapter_dir):
        detail = (
            "Built-in adapter is not installed. Download it explicitly before previewing."
            if is_builtin
            else "Adapter files not found"
        )
        raise HTTPException(status_code=404, detail=detail)

    preview_path = os.path.join(adapter_dir, "preview_sample.wav")

    # Return cached if exists
    if os.path.exists(preview_path):
        return {"status": "cached", "audio_url": f"{url_prefix}/preview_sample.wav"}

    # Generate preview
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    try:
        voice_data = {
            "type": "lora",
            "adapter_id": adapter_id,
            "adapter_path": adapter_dir,
        }
        voice_config = {"_lora_preview_": voice_data}
        engine.generate_voice(
            text=LORA_PREVIEW_TEXT,
            instruct_text="",
            speaker="_lora_preview_",
            voice_config=voice_config,
            output_path=preview_path,
        )
        return {"status": "generated", "audio_url": f"{url_prefix}/preview_sample.wav"}
    except Exception as e:
        logger.error(f"LoRA preview generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

## ── Dataset Builder ──────────────────────────────────────────

def _load_builder_state(name):
    """Load project state from dataset builder working directory."""
    state_path = os.path.join(DATASET_BUILDER_DIR, name, "state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            # Ensure new fields exist for backward compat
            state.setdefault("description", "")
            state.setdefault("global_seed", "")
            state.setdefault("samples", [])
            return state
        except Exception:
            pass
    return {"description": "", "global_seed": "", "samples": []}

def _save_builder_state(name, state):
    """Save per-sample state to dataset builder working directory."""
    work_dir = os.path.join(DATASET_BUILDER_DIR, name)
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

@app.get("/api/dataset_builder/list")
async def dataset_builder_list():
    """List existing dataset builder projects."""
    projects = []
    if os.path.isdir(DATASET_BUILDER_DIR):
        for name in sorted(os.listdir(DATASET_BUILDER_DIR)):
            state_path = os.path.join(DATASET_BUILDER_DIR, name, "state.json")
            if os.path.isfile(state_path):
                state = _load_builder_state(name)
                samples = state.get("samples", [])
                projects.append({
                    "name": name,
                    "description": state.get("description", ""),
                    "sample_count": len(samples),
                    "done_count": sum(1 for s in samples if s.get("status") == "done"),
                })
    return projects

@app.post("/api/dataset_builder/create")
async def dataset_builder_create(request: DatasetBuilderCreateRequest):
    """Create a new dataset builder project."""
    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")
    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if os.path.exists(work_dir):
        raise HTTPException(status_code=400, detail=f"Project '{safe_name}' already exists")
    _save_builder_state(safe_name, {"description": "", "global_seed": "", "samples": []})
    return {"name": safe_name}

@app.post("/api/dataset_builder/update_meta")
async def dataset_builder_update_meta(request: DatasetBuilderUpdateMetaRequest):
    """Update project description and global seed without touching samples."""
    safe_name = _sanitize_name(request.name)
    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Project not found")
    state = _load_builder_state(safe_name)
    state["description"] = request.description
    state["global_seed"] = request.global_seed
    _save_builder_state(safe_name, state)
    return {"status": "ok"}

@app.post("/api/dataset_builder/update_rows")
async def dataset_builder_update_rows(request: DatasetBuilderUpdateRowsRequest):
    """Update row definitions, preserving existing generation status/audio."""
    safe_name = _sanitize_name(request.name)
    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Project not found")
    state = _load_builder_state(safe_name)
    existing = state.get("samples", [])
    # Merge: keep status/audio_url from existing samples where text unchanged
    new_samples = []
    for i, row in enumerate(request.rows):
        sample = {
            "emotion": row.get("emotion", ""),
            "text": row.get("text", "").strip(),
            "seed": row.get("seed", ""),
            "status": "pending",
            "audio_url": None,
        }
        if i < len(existing):
            old = existing[i]
            # Preserve generation state if text unchanged (trimmed comparison)
            if old.get("text", "").strip() == sample["text"]:
                sample["status"] = old.get("status", "pending")
                sample["audio_url"] = old.get("audio_url")
        new_samples.append(sample)
    state["samples"] = new_samples
    _save_builder_state(safe_name, state)
    return {"status": "ok", "sample_count": len(new_samples)}

@app.post("/api/dataset_builder/generate_sample")
async def dataset_builder_generate_sample(request: DatasetSampleGenRequest):
    """Generate a single dataset sample using VoiceDesign."""
    engine = project_manager.get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Failed to initialize TTS engine")

    work_dir = os.path.join(DATASET_BUILDER_DIR, request.dataset_name)
    os.makedirs(work_dir, exist_ok=True)

    try:
        wav_path, sr = engine.generate_voice_design(
            description=request.description,
            sample_text=request.text,
            seed=request.seed,
        )

        dest_filename = f"sample_{request.sample_index:03d}.wav"
        dest_path = os.path.join(work_dir, dest_filename)
        shutil.copy2(wav_path, dest_path)

        # Update state (cache-bust URL so browser loads fresh audio on regen)
        cache_bust = int(time.time())
        audio_url = f"/dataset_builder/{request.dataset_name}/{dest_filename}?t={cache_bust}"
        state = _load_builder_state(request.dataset_name)
        samples = state.get("samples", [])
        # Ensure list is large enough
        while len(samples) <= request.sample_index:
            samples.append({"status": "pending"})
        existing_sample = samples[request.sample_index] if request.sample_index < len(samples) else {}
        samples[request.sample_index] = {
            **existing_sample,
            "status": "done",
            "audio_url": audio_url,
            "text": request.text.strip(),
            "description": request.description,
        }
        state["samples"] = samples
        _save_builder_state(request.dataset_name, state)

        return {
            "status": "done",
            "sample_index": request.sample_index,
            "audio_url": audio_url,
        }
    except Exception as e:
        logger.error(f"Dataset builder sample generation failed: {e}")
        # Mark as error in state
        state = _load_builder_state(request.dataset_name)
        samples = state.get("samples", [])
        while len(samples) <= request.sample_index:
            samples.append({"status": "pending"})
        samples[request.sample_index] = {"status": "error", "error": str(e)}
        state["samples"] = samples
        _save_builder_state(request.dataset_name, state)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/dataset_builder/generate_batch")
async def dataset_builder_generate_batch(request: DatasetBatchGenRequest):
    """Batch generate dataset samples as a background task."""
    if process_state["dataset_builder"]["running"]:
        raise HTTPException(status_code=400, detail="Dataset generation already running")

    if not request.samples or len(request.samples) == 0:
        raise HTTPException(status_code=400, detail="No samples provided")

    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")

    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    os.makedirs(work_dir, exist_ok=True)
    root_desc = request.description.strip()

    # Determine which indices to generate
    if request.indices is not None:
        to_generate = request.indices
    else:
        to_generate = list(range(len(request.samples)))

    total = len(to_generate)

    # Snapshot request data for the thread (request object may not survive)
    samples_snapshot = [(s.emotion.strip(), s.text.strip()) for s in request.samples]
    global_seed = request.global_seed
    per_seeds = request.seeds

    def task():
        process_state["dataset_builder"]["running"] = True
        process_state["dataset_builder"]["cancel"] = False
        _reset_process_logs("dataset_builder")
        _append_process_log(
            "dataset_builder",
            f"Starting dataset {safe_name}: {total} sample(s) queued.",
        )

        engine = project_manager.get_engine()
        if not engine:
            _append_process_log(
                "dataset_builder",
                "Failed to initialize TTS engine.",
                level="error",
            )
            process_state["dataset_builder"]["running"] = False
            return

        state = _load_builder_state(safe_name)
        samples_state = state.get("samples", [])
        # Ensure list is large enough for all samples
        while len(samples_state) < len(samples_snapshot):
            samples_state.append({"status": "pending"})

        completed = 0
        for i, idx in enumerate(to_generate):
            if process_state["dataset_builder"]["cancel"]:
                _append_process_log(
                    "dataset_builder",
                    f"[CANCEL] Stopped at {completed}/{total}",
                    level="warning",
                )
                break

            emotion, text = samples_snapshot[idx]
            description = f"{root_desc}, {emotion}" if emotion else root_desc

            # Mark as generating (preserve existing fields like emotion, seed)
            existing_s = samples_state[idx] if idx < len(samples_state) else {}
            samples_state[idx] = {**existing_s, "status": "generating", "text": text, "emotion": emotion, "description": description}
            state["samples"] = samples_state
            _save_builder_state(safe_name, state)

            _append_process_log(
                "dataset_builder",
                f"[{i+1}/{total}] {('[' + emotion + '] ' if emotion else '')}\"{text[:60]}{'...' if len(text) > 60 else ''}\"",
                level="progress",
            )

            try:
                # Resolve seed: per-line > global > random
                seed = -1
                if per_seeds and idx < len(per_seeds) and per_seeds[idx] >= 0:
                    seed = per_seeds[idx]
                elif global_seed >= 0:
                    seed = global_seed

                wav_path, sr = engine.generate_voice_design(
                    description=description,
                    sample_text=text,
                    seed=seed,
                )
                dest_filename = f"sample_{idx:03d}.wav"
                dest_path = os.path.join(work_dir, dest_filename)
                shutil.copy2(wav_path, dest_path)

                samples_state[idx] = {
                    **samples_state[idx],
                    "status": "done",
                    "audio_url": f"/dataset_builder/{safe_name}/{dest_filename}?t={int(time.time())}",
                    "text": text,
                    "emotion": emotion,
                    "description": description,
                }
                completed += 1
            except Exception as e:
                logger.error(f"Dataset builder sample {idx} failed: {e}")
                _append_process_log(
                    "dataset_builder",
                    f"Sample {idx + 1} failed: {e}",
                    level="error",
                )
                samples_state[idx] = {**samples_state[idx], "status": "error", "error": str(e), "text": text, "emotion": emotion}

            state["samples"] = samples_state
            _save_builder_state(safe_name, state)

        _append_process_log(
            "dataset_builder",
            f"[DONE] Generated {completed}/{total} samples",
        )
        process_state["dataset_builder"]["running"] = False
        process_state["dataset_builder"]["cancel"] = False

    threading.Thread(target=task, daemon=True).start()
    return {"status": "started", "dataset_name": safe_name, "total": total}

@app.post("/api/dataset_builder/cancel")
async def dataset_builder_cancel():
    """Cancel ongoing batch dataset generation."""
    if process_state["dataset_builder"]["running"]:
        process_state["dataset_builder"]["cancel"] = True
        _append_process_log(
            "dataset_builder",
            "[CANCEL] Cancellation requested",
            level="warning",
        )
        return {"status": "cancelling"}
    return {"status": "not_running"}

@app.get("/api/dataset_builder/status/{name}")
async def dataset_builder_status(name: str):
    """Get per-sample generation status for a dataset builder project."""
    state = _load_builder_state(name)
    process = _current_process_status("dataset_builder")
    return {
        "description": state.get("description", ""),
        "global_seed": state.get("global_seed", ""),
        "samples": state.get("samples", []),
        "running": process["running"],
        "logs": process["logs"],
        "log_line_count": process.get("log_line_count", 0),
        "log_truncated": process.get("log_truncated", False),
        "log_updated_at": process.get("log_updated_at"),
    }

@app.post("/api/dataset_builder/save")
async def dataset_builder_save(request: DatasetSaveRequest):
    """Finalize dataset builder project as a training dataset."""
    safe_name = _sanitize_name(request.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")

    work_dir = os.path.join(DATASET_BUILDER_DIR, safe_name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Dataset builder project not found")

    state = _load_builder_state(safe_name)
    samples = state.get("samples", [])

    # Collect completed samples
    done_samples = [(i, s) for i, s in enumerate(samples) if s.get("status") == "done"]
    if not done_samples:
        raise HTTPException(status_code=400, detail="No completed samples to save")

    # Check ref_index is valid
    ref_idx = request.ref_index
    ref_sample = next((s for i, s in done_samples if i == ref_idx), None)
    if ref_sample is None:
        # Fall back to first completed sample
        ref_idx = done_samples[0][0]
        ref_sample = done_samples[0][1]

    # Create training dataset directory
    dataset_dir = os.path.join(LORA_DATASETS_DIR, safe_name)
    if os.path.exists(dataset_dir):
        raise HTTPException(status_code=400, detail=f"Dataset '{safe_name}' already exists in training datasets")

    os.makedirs(dataset_dir, exist_ok=True)

    try:
        metadata_lines = []
        for i, sample in done_samples:
            src_filename = f"sample_{i:03d}.wav"
            src_path = os.path.join(work_dir, src_filename)
            if not os.path.exists(src_path):
                continue

            dest_filename = f"sample_{i:03d}.wav"
            shutil.copy2(src_path, os.path.join(dataset_dir, dest_filename))

            metadata_lines.append(json.dumps({
                "audio_filepath": dest_filename,
                "text": sample.get("text", ""),
                "ref_audio": "ref.wav",
            }, ensure_ascii=False))

        # Copy ref sample and save its text for correct clone prompt alignment
        ref_src = os.path.join(work_dir, f"sample_{ref_idx:03d}.wav")
        if os.path.exists(ref_src):
            shutil.copy2(ref_src, os.path.join(dataset_dir, "ref.wav"))
        ref_text = ref_sample.get("text", "")
        with open(os.path.join(dataset_dir, "ref_text.txt"), "w", encoding="utf-8") as f:
            f.write(ref_text)

        # Write metadata
        with open(os.path.join(dataset_dir, "metadata.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(metadata_lines) + "\n")

        sample_count = len(metadata_lines)
        logger.info(f"Dataset saved: '{safe_name}' ({sample_count} samples, ref=sample_{ref_idx:03d})")

        return {
            "status": "saved",
            "dataset_id": safe_name,
            "sample_count": sample_count,
        }
    except Exception as e:
        # Clean up on failure
        if os.path.exists(dataset_dir):
            shutil.rmtree(dataset_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/dataset_builder/{name}")
async def dataset_builder_delete(name: str):
    """Discard a dataset builder working project."""
    work_dir = os.path.join(DATASET_BUILDER_DIR, name)
    if not os.path.exists(work_dir):
        raise HTTPException(status_code=404, detail="Dataset builder project not found")
    shutil.rmtree(work_dir, ignore_errors=True)
    logger.info(f"Dataset builder project discarded: {name}")
    return {"status": "deleted", "name": name}

# ── Preparer ─────────────────────────────────────────────────────────────────


def _preparer_output_path(filename: str) -> str:
    raw = str(filename or "").strip()
    safe = os.path.basename(raw)
    if (
        not safe
        or safe != raw
        or os.path.splitext(safe)[1].lower() != ".zip"
    ):
        raise HTTPException(
            status_code=422,
            detail="Output filename must be one project-local .zip filename.",
        )
    return os.path.join(PREPARER_OUTPUT_DIR, safe)


def _available_preparer_output_path(filename: str) -> str:
    output_path = _preparer_output_path(filename)
    temporary_path = output_path + ".tmp"
    if os.path.exists(output_path) or os.path.exists(temporary_path):
        raise HTTPException(
            status_code=409,
            detail=(
                "A prepared dataset with that output name already exists. "
                "Choose a new .zip filename; Alexandria will not overwrite it."
            ),
        )
    return output_path


def _preparer_upload_path(filename: str) -> tuple[str, str]:
    raw = os.path.basename(str(filename or "").strip())
    ext = os.path.splitext(raw)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTS | {".m4a"}:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported format. Use WAV, MP3, FLAC, OGG, or M4A."
            ),
        )
    safe_stem = _sanitize_name(os.path.splitext(raw)[0])
    if not safe_stem:
        raise HTTPException(status_code=400, detail="Invalid audio filename.")
    stored = f"preparer_{safe_stem}_{time.time_ns()}{ext}"
    return stored, os.path.join(UPLOADS_DIR, stored)


def _existing_preparer_upload(filename: str) -> str:
    raw = str(filename or "").strip()
    ext = os.path.splitext(raw)[1].lower()
    if (
        not raw
        or os.path.basename(raw) != raw
        or not raw.startswith("preparer_")
        or ext not in ALLOWED_AUDIO_EXTS | {".m4a"}
    ):
        raise HTTPException(status_code=422, detail="Invalid uploaded audio filename.")
    root = os.path.realpath(UPLOADS_DIR)
    target = os.path.realpath(os.path.join(UPLOADS_DIR, raw))
    if not target.startswith(root + os.sep):
        raise HTTPException(status_code=422, detail="Invalid uploaded audio path.")
    return target


async def _store_preparer_upload(file: UploadFile) -> tuple[str, str]:
    stored, destination = _preparer_upload_path(file.filename or "")
    try:
        async with aiofiles.open(destination, "wb") as handle:
            while chunk := await file.read(1024 * 1024):
                await handle.write(chunk)
        if os.path.getsize(destination) <= 0:
            raise HTTPException(
                status_code=400,
                detail="Uploaded audio file is empty.",
            )
        return stored, destination
    except Exception:
        try:
            os.remove(destination)
        except FileNotFoundError:
            pass
        raise


@app.post("/api/preparer/upload")
async def preparer_upload(file: UploadFile = File(...)):
    """Confine a batch-preparer source file under the project uploads root."""
    stored, _ = await _store_preparer_upload(file)
    return {
        "status": "uploaded",
        "filename": stored,
        "original_filename": os.path.basename(file.filename or stored),
    }


@app.delete("/api/preparer/upload/{filename}")
async def preparer_discard_upload(filename: str):
    """Remove one temporary preparer upload that was not assigned to a job."""
    path = _existing_preparer_upload(filename)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return {"status": "deleted", "filename": os.path.basename(path)}


@app.post("/api/preparer/start")
async def preparer_start(
    background_tasks: BackgroundTasks,
    config_json: str = Form(...),
    audio_file: UploadFile = File(...),
):
    """Upload audio and run the preparer to generate a voice training dataset."""
    if not os.path.exists(PREPARER_SCRIPT_PATH):
        raise HTTPException(
            status_code=503,
            detail="Preparer script not installed. Re-run the Alexandria Install action.",
        )
    if process_state["preparer"]["running"]:
        raise HTTPException(status_code=400, detail="Preparer is already running.")

    try:
        config = PreparerConfig(**json.loads(config_json))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid config: {e}")

    has_space, free_gb = check_disk_space(ROOT_DIR, 2.0)
    if not has_space:
        raise HTTPException(status_code=400, detail=f"Insufficient disk space ({free_gb} GB free, 2 GB required).")

    output_path = _available_preparer_output_path(config.output_filename)
    _, audio_path = await _store_preparer_upload(audio_file)
    state = process_state["preparer"]
    state.update(
        {
            "running": True,
            "logs": [],
            "cancel": False,
            "status": "running",
            "output_file": None,
            "process": None,
        }
    )

    def _run():
        cmd = [sys.executable, "-u", PREPARER_SCRIPT_PATH,
               "--audio", audio_path,
               "--output", output_path,
               "--lang", config.lang,
               "--min-confidence", str(config.min_confidence),
               "--min-snr", str(config.min_snr)]

        try:
            rc = _stream_subprocess_to_logs(cmd, BASE_DIR, state)

            if state.get("cancel"):
                state["status"] = "cancelled"
                state["logs"].append("Preparer cancelled.")
            elif rc == 0:
                state["status"] = "done"
                state["output_file"] = os.path.basename(output_path)
                state["logs"].append("Preparer completed successfully.")
            else:
                state["status"] = "failed"
                state["logs"].append(f"Preparer failed (exit code {rc}).")
        except Exception as exc:
            state["status"] = "failed"
            state["logs"].append(f"Preparer failed: {exc}")
            logger.exception("Audio preparer job failed")
        finally:
            if state.get("status") != "done":
                try:
                    os.remove(output_path + ".tmp")
                except FileNotFoundError:
                    pass
            try:
                os.remove(audio_path)
            except FileNotFoundError:
                pass
            state["running"] = False
            state["process"] = None

    background_tasks.add_task(_run)
    return {"status": "started"}


@app.post("/api/preparer/cancel")
async def preparer_cancel():
    state = process_state["preparer"]
    if not state["running"]:
        raise HTTPException(status_code=400, detail="No preparer is currently running.")
    proc = state.get("process")
    if proc:
        try:
            proc.terminate()
        except OSError:
            pass
    state["cancel"] = True
    return {"status": "cancel_requested"}


@app.get("/api/preparer/list")
async def preparer_list_outputs():
    """List completed dataset ZIP files available for download."""
    files = []
    if not os.path.exists(PREPARER_OUTPUT_DIR):
        return {"files": files}
    for fname in sorted(os.listdir(PREPARER_OUTPUT_DIR)):
        if not fname.endswith(".zip"):
            continue
        fpath = os.path.join(PREPARER_OUTPUT_DIR, fname)
        files.append({
            "filename": fname,
            "size_mb": round(os.path.getsize(fpath) / (1024 * 1024), 1),
            "modified": os.path.getmtime(fpath),
        })
    return {"files": files}


@app.get("/api/preparer/download/{filename:path}")
async def preparer_download(filename: str):
    """Download one completed project-local dataset ZIP."""
    try:
        file_path = _preparer_output_path(filename)
    except HTTPException as exc:
        raise HTTPException(status_code=400, detail="Invalid filename.") from exc
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        file_path,
        media_type="application/zip",
        filename=os.path.basename(file_path),
    )


@app.post("/api/preparer/batch/start")
async def preparer_batch_start(request: BatchPreparerRequest, background_tasks: BackgroundTasks):
    """Process validated temporary uploads sequentially through the preparer."""
    if not os.path.exists(PREPARER_SCRIPT_PATH):
        raise HTTPException(
            status_code=503,
            detail="Preparer script not installed. Re-run the Alexandria Install action.",
        )
    state = process_state["batch_preparer"]
    if state["running"]:
        raise HTTPException(status_code=400, detail="Batch preparer is already running.")
    if not request.tasks:
        raise HTTPException(status_code=422, detail="Batch preparer requires at least one audio file.")

    has_space, free_gb = check_disk_space(ROOT_DIR, 5.0)
    if not has_space:
        raise HTTPException(status_code=400, detail=f"Insufficient disk space ({free_gb} GB free, 5 GB recommended).")

    prepared_tasks = []
    output_names = set()
    try:
        for task in request.tasks:
            audio_path = _existing_preparer_upload(task.audio_filename)
            if not os.path.isfile(audio_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"Uploaded audio was not found: {task.audio_filename}",
                )
            output_path = _available_preparer_output_path(task.output_filename)
            output_key = os.path.normcase(os.path.basename(output_path))
            if output_key in output_names:
                raise HTTPException(
                    status_code=409,
                    detail="Each batch item must use a unique output .zip filename.",
                )
            output_names.add(output_key)
            prepared_tasks.append((task, audio_path, output_path))
    except Exception:
        for task in request.tasks:
            try:
                os.remove(_existing_preparer_upload(task.audio_filename))
            except (FileNotFoundError, HTTPException):
                pass
        raise

    state.update(
        {
            "running": True,
            "cancel": False,
            "process": None,
            "status": "running",
            "logs": [f"Starting batch of {len(prepared_tasks)} tasks..."],
            "tasks": [
                {"audio": task.audio_filename, "status": "pending"}
                for task, _audio_path, _output_path in prepared_tasks
            ],
            "current_task_idx": -1,
        }
    )

    def _run():
        try:
            for i, (task, audio_path, output_path) in enumerate(prepared_tasks):
                if state.get("cancel"):
                    break

                state["current_task_idx"] = i
                state["tasks"][i]["status"] = "running"
                state["logs"].append(
                    f"--- [{i + 1}/{len(prepared_tasks)}] {task.audio_filename} ---"
                )
                command = [
                    sys.executable,
                    "-u",
                    PREPARER_SCRIPT_PATH,
                    "--audio",
                    audio_path,
                    "--output",
                    output_path,
                    "--lang",
                    request.lang,
                    "--min-confidence",
                    str(request.min_confidence),
                    "--min-snr",
                    str(request.min_snr),
                ]

                try:
                    return_code = _stream_subprocess_to_logs(
                        command,
                        BASE_DIR,
                        state,
                        log_prefix=f"[{i + 1}] ",
                    )
                    if state.get("cancel"):
                        state["tasks"][i]["status"] = "cancelled"
                        break
                    if return_code == 0:
                        state["tasks"][i]["status"] = "done"
                        state["logs"].append(
                            f"[{i + 1}] Done: {task.audio_filename}"
                        )
                    else:
                        state["tasks"][i]["status"] = "failed"
                        state["logs"].append(
                            f"[{i + 1}] Failed (exit {return_code}): {task.audio_filename}"
                        )
                except Exception as exc:
                    state["tasks"][i]["status"] = "failed"
                    state["logs"].append(
                        f"[{i + 1}] Failed: {task.audio_filename}: {exc}"
                    )
                    logger.exception("Batch audio preparer task failed")
                finally:
                    if state["tasks"][i]["status"] != "done":
                        try:
                            os.remove(output_path + ".tmp")
                        except FileNotFoundError:
                            pass
                    try:
                        os.remove(audio_path)
                    except FileNotFoundError:
                        pass

            if state.get("cancel"):
                for task_state in state["tasks"]:
                    if task_state["status"] in {"pending", "running"}:
                        task_state["status"] = "cancelled"
                state["status"] = "cancelled"
                state["logs"].append("Batch cancelled.")
            elif any(item["status"] == "failed" for item in state["tasks"]):
                state["status"] = "completed_with_errors"
                state["logs"].append("Batch finished with one or more failures.")
            else:
                state["status"] = "done"
                state["logs"].append("Batch completed successfully.")
        except Exception as exc:
            state["status"] = "failed"
            state["logs"].append(f"Batch preparer failed: {exc}")
            logger.exception("Batch audio preparer failed")
        finally:
            for _task, audio_path, output_path in prepared_tasks:
                try:
                    os.remove(audio_path)
                except FileNotFoundError:
                    pass
                if state.get("status") != "done":
                    try:
                        os.remove(output_path + ".tmp")
                    except FileNotFoundError:
                        pass
            state["process"] = None
            state["current_task_idx"] = -1
            state["running"] = False

    background_tasks.add_task(_run)
    return {"status": "started", "task_count": len(prepared_tasks)}


@app.post("/api/preparer/batch/cancel")
async def preparer_batch_cancel():
    state = process_state["batch_preparer"]
    if not state["running"]:
        raise HTTPException(status_code=400, detail="No batch preparer is currently running.")
    state["cancel"] = True
    state["status"] = "cancelling"
    process = state.get("process")
    if process is not None:
        try:
            process.terminate()
        except OSError:
            pass
    return {"status": "cancel_requested"}


@app.on_event("startup")
async def initialize_runtime_project() -> None:
    global ACTIVE_PROJECT_ID, ACTIVE_PROJECT_STORAGE_KIND
    global LEGACY_PROJECT_ID, LEGACY_FLOW_SNAPSHOT
    if ACTIVE_PROJECT_ID:
        return
    try:
        legacy_flow = _current_project_flow_status()
        legacy_id = str(
            legacy_flow.get("project", {}).get("id") or ""
        ).strip()
        LEGACY_FLOW_SNAPSHOT = copy.deepcopy(legacy_flow)
        LEGACY_PROJECT_ID = legacy_id or None
        ACTIVE_PROJECT_ID = LEGACY_PROJECT_ID
        ACTIVE_PROJECT_STORAGE_KIND = "legacy_checkout"

        catalog = _project_catalog_payload()
        selected_id = str(
            catalog.get("last_selected_project_id") or ""
        ).strip()
        if not selected_id or selected_id == LEGACY_PROJECT_ID:
            return
        selected = next(
            (
                item
                for item in catalog.get("projects", [])
                if item.get("id") == selected_id
                and item.get("availability_state") == "available"
                and item.get("archive_state") != "archived"
            ),
            None,
        )
        if not isinstance(selected, dict):
            return
        root_path = str(
            selected.get("technical_details", {}).get("project_path")
            or ""
        ).strip()
        _activate_runtime_project(
            root_dir=root_path,
            project_id=selected_id,
            storage_kind=str(selected.get("storage_kind") or "managed"),
        )
        try:
            result = consume_pending_voice_import_queue(
                queue_path=Path(LEGACY_ROOT_DIR) / PENDING_VOICE_IMPORT_FILENAME,
                project_root=ROOT_DIR,
                project_id=str(ACTIVE_PROJECT_ID or selected_id),
                reusable_library_root=LEGACY_ROOT_DIR,
            )
            if result.get("status") == "applied":
                logger.info(
                    "pending_voice_imports_applied %s",
                    json.dumps(result, sort_keys=True),
                )
        except Exception as import_exc:
            logger.exception(
                "Pending voice imports could not be applied: %s",
                import_exc,
            )
    except Exception as exc:
        logger.exception(
            "Could not activate the last-selected project; using legacy checkout: %s",
            exc,
        )
        if LEGACY_PROJECT_ID:
            ACTIVE_PROJECT_ID = LEGACY_PROJECT_ID
            ACTIVE_PROJECT_STORAGE_KIND = "legacy_checkout"
    finally:
        try:
            transition_report = reconcile_audio_transitions(ROOT_DIR)
            if transition_report["actions"]:
                logger.info(
                    "audio_durable_transitions_reconciled %s",
                    json.dumps(transition_report, sort_keys=True),
                )
            if transition_report["unresolved_count"]:
                logger.error(
                    "audio_durable_transitions_unresolved %s",
                    json.dumps(transition_report, sort_keys=True),
                )
        except Exception as transition_exc:
            logger.exception(
                "Audio durable transition reconciliation failed: %s",
                transition_exc,
            )
        try:
            reconciled = reconcile_interrupted_audio_requests(ROOT_DIR)
            if reconciled:
                logger.info(
                    "audio_generation_requests_reconciled %s",
                    json.dumps(
                        [item["request_id"] for item in reconciled],
                        sort_keys=True,
                    ),
                )
        except Exception as lifecycle_exc:
            logger.exception(
                "Audio generation lifecycle reconciliation failed: %s",
                lifecycle_exc,
            )


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("ALEXANDRIA_HOST", "127.0.0.1")
    port = int(os.environ.get("ALEXANDRIA_PORT", "4200"))
    uvicorn.run(app, host=host, port=port, access_log=False)
