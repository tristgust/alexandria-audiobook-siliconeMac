import os
import copy
import json
import inspect
import queue
import shutil
import subprocess
import tempfile
import threading
import zipfile
import io
import re
import time
import logging
from pathlib import Path
import audio_crash_reconciliation as crash_reconciliation
from audio_artifacts import (
    AudioArtifactError,
    atomic_export_audio_segment,
    audio_binding_fingerprint,
    confined_audio_path,
    install_generated_audio,
    install_verified_audio,
    require_current_project_audio,
    sha256_file,
)
from audio_mastering import (
    AudioMasteringError,
    build_mastering_plan,
    create_mastered_candidate,
    mastering_dependency_fingerprint,
    normalize_mastering_settings,
)
from approved_audio import (
    active_approved_audio_lock,
    approved_audio_binding_fingerprint,
    clear_approved_audio_fields,
    require_regeneration_unlocked,
)
from audio_failure import normalize_audio_failure
from audio_generation_lifecycle import (
    AudioGenerationLifecycleError,
    normalize_request_manifest,
    publication_recovery_path,
    publication_recovery_receipt,
    publish_chunk as publish_generation_chunk,
    record_chunk_failed as record_generation_chunk_failed,
    record_chunk_started as record_generation_chunk_started,
)
from audio_takes import (
    AudioTakeError,
    apply_cleanup as apply_audio_take_cleanup,
    apply_delete as apply_audio_take_delete,
    build_take_record,
    cleanup_impact as audio_take_cleanup_impact,
    chunk_key as audio_take_chunk_key,
    delete_impact as audio_take_delete_impact,
    load_registry as load_audio_take_registry,
    new_take_id,
    promote_take as promote_registered_audio_take,
    public_chunk_takes,
    prepare_invalidation_registry,
    plan_take_registration,
    register_rendition as register_audio_take_rendition,
    register_take,
    registry_view as audio_take_registry_view,
    registry_path as audio_take_registry_path,
    set_take_kept,
    set_final_listen_pause as set_audio_take_final_listen_pause,
    set_final_listen_pin as set_audio_take_final_listen_pin,
    take_directory,
    take_filename_base,
    take_chunk_audio_fields,
    undo_operation as undo_audio_take_operation,
)
from chapter_assembly import (
    build_chapters as build_chapter_markers,
    create_processed_rendition,
    source_order_fingerprint as chapter_source_order_fingerprint,
)
from backend_render_plan import application_record as backend_render_plan_application_record
from audio_generation_provenance import resolve_audio_generation_provenance
from audio_generation_policy import (
    apply_generation_seed_to_voice_config,
    generation_seed_chunk_fields,
    generation_seed_synthesis_binding,
    persisted_generation_seed_resolution,
    resolve_generation_seed,
    voice_supports_deterministic_seed,
)
from audio_synthesis_config import synthesis_binding_config
from experimental_prompt_routing import (
    experimental_prompt_chunk_fields,
    resolve_experimental_prompt_override,
)
from recurring_voice_routing import (
    recurring_voice_chunk_fields,
    resolve_recurring_voice_route,
)
from pronunciation_registry import (
    load_pronunciation_registry,
    pronunciation_chunk_fields,
    resolve_pronunciation_request,
)
from dialogue_continuity import (
    effective_delivery_instruction,
    effective_pause_after_ms,
    resolve_spoken_continuity,
)
from fish_cloud_tts import fish_cloud_chunk_reset_fields
from synthesis_windows import (
    plan_synthesis_segments,
    resolve_synthesis_backend_id,
    synthesis_receipt_reset_fields,
)
from generation_state import fingerprint_value
from model_memory import ModelMemoryCoordinator
from audio_crash_reconciliation import (
    apply_audio_transition,
    audio_mutation_guard,
    audio_project_lock,
)
from utils import atomic_json_write
from voice_aliases import VoiceAliasError, resolve_voice_alias
from tts import (
    TTSEngine,
    combine_audio_with_pauses,
    compute_timeline,
    sanitize_filename,
    DEFAULT_PAUSE_MS,
    SAME_SPEAKER_PAUSE_MS
)
from pydub import AudioSegment

MAX_CHUNK_CHARS = 500


def _engine_generation_provenance(engine, voice_data):
    resolver = getattr(engine, "generation_provenance", None)
    if callable(resolver):
        return resolver(voice_data)
    return resolve_audio_generation_provenance(
        voice_data,
        mode=str(getattr(engine, "mode", "local") or "local"),
        use_mlx=bool(getattr(engine, "_use_mlx", False)),
        source="generation",
        external_url=getattr(engine, "_url", None),
    )


def get_speaker(entry):
    """Get speaker from entry, checking both 'speaker' and 'type' fields."""
    return entry.get("speaker") or entry.get("type") or ""


def _is_structural_text(text):
    """Check if text is a title, chapter heading, dedication, or other structural fragment."""
    stripped = text.strip()
    if not stripped:
        return True
    # Very short and not a full sentence (no sentence-ending punctuation)
    if len(stripped) < 80 and not stripped[-1] in '.!?':
        return True
    return False


def _make_chunk(speaker, text, instruct, pause_after=None):
    """Build a chunk dict, omitting pause_after when None for clean JSON."""
    chunk = {"speaker": speaker, "text": text, "instruct": instruct}
    if pause_after is not None:
        chunk["pause_after"] = pause_after
    return chunk


def group_into_chunks(script_entries, max_chars=MAX_CHUNK_CHARS):
    """Group consecutive entries by same speaker into chunks up to max_chars"""
    if not script_entries:
        return []

    chunks = []
    current_speaker = get_speaker(script_entries[0])
    current_text = script_entries[0].get("text", "")
    current_instruct = script_entries[0].get("instruct", "")
    current_pause_after = script_entries[0].get("pause_after")

    for entry in script_entries[1:]:
        speaker = get_speaker(entry)
        text = entry.get("text", "")
        instruct = entry.get("instruct", "")

        # Don't merge structural text (titles, chapter headings, dedications)
        if (speaker == current_speaker and instruct == current_instruct
                and not _is_structural_text(current_text)
                and not _is_structural_text(text)):
            combined = current_text + " " + text
            if len(combined) <= max_chars:
                current_text = combined
                # Last merged entry's pause_after wins
                current_pause_after = entry.get("pause_after", current_pause_after)
            else:
                chunks.append(_make_chunk(current_speaker, current_text, current_instruct, current_pause_after))
                current_text = text
                current_instruct = instruct
                current_pause_after = entry.get("pause_after")
        else:
            chunks.append(_make_chunk(current_speaker, current_text, current_instruct, current_pause_after))
            current_speaker = speaker
            current_text = text
            current_instruct = instruct
            current_pause_after = entry.get("pause_after")

    # Don't forget the last chunk
    chunks.append(_make_chunk(current_speaker, current_text, current_instruct, current_pause_after))

    return chunks

logger = logging.getLogger(__name__)

class ProjectManager:
    def __init__(self, root_dir, *, config_path=None):
        self.root_dir = str(Path(root_dir).expanduser().resolve())
        self.script_path = os.path.join(self.root_dir, "annotated_script.json")
        self.chunks_path = os.path.join(self.root_dir, "chunks.json")
        self.voicelines_dir = os.path.join(self.root_dir, "voicelines")
        self.voice_config_path = os.path.join(self.root_dir, "voice_config.json")
        self.pronunciation_registry_path = os.path.join(
            self.root_dir,
            "pronunciation_registry.json",
        )
        self.config_path = str(
            Path(config_path).expanduser().resolve()
            if config_path
            else Path(self.root_dir, "app", "config.json").resolve()
        )

        # Ensure voicelines dir exists
        os.makedirs(self.voicelines_dir, exist_ok=True)

        self.engine = None
        self.model_residency = ModelMemoryCoordinator()
        self._engine_lock = threading.Lock()
        self._chunks_lock = threading.RLock()  # Thread-safe file writes

    def get_engine(self):
        if self.engine:
            return self.engine

        with self._engine_lock:
            if self.engine:
                return self.engine

            # Load config once for the single shared engine. Parallel chunk
            # workers must never race two model-owning TTSEngine instances.
            config = {}
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                except Exception:
                    pass

            try:
                parameters = inspect.signature(TTSEngine).parameters
                kwargs = (
                    {"model_residency": self.model_residency}
                    if "model_residency" in parameters
                    or any(
                        item.kind == inspect.Parameter.VAR_KEYWORD
                        for item in parameters.values()
                    )
                    else {}
                )
                self.engine = TTSEngine(config, **kwargs)
                print(f"TTS engine initialized (mode={self.engine.mode})")
                return self.engine
            except Exception as e:
                print(f"Failed to initialize TTS engine: {e}")
                return None

    def _load_tts_config(self):
        """Load TTS config section from config.json for pause defaults."""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f).get("tts", {})
        except Exception:
            return {}

    @staticmethod
    def _pronunciation_engine_id(voice_data):
        voice_data = voice_data if isinstance(voice_data, dict) else {}
        return str(
            voice_data.get("clone_backend")
            or voice_data.get("type")
            or "default"
        ).strip()

    def _resolve_chunk_pronunciation(
        self,
        *,
        index,
        chunk,
        speaker,
        resolved_speaker,
        voice_data,
    ):
        tts_config = self._load_tts_config()
        language = (
            voice_data.get("language")
            if isinstance(voice_data, dict)
            else None
        ) or tts_config.get("language")
        return resolve_pronunciation_request(
            registry=load_pronunciation_registry(self.root_dir),
            chunk_index=index,
            text=str(chunk.get("text") or ""),
            speaker=str(speaker or ""),
            resolved_speaker=str(resolved_speaker or ""),
            voice_data=voice_data,
            language=language,
            engine_id=self._pronunciation_engine_id(voice_data),
            supports_phonetic_hint=False,
        )

    def _chunk_with_pronunciation(
        self,
        *,
        chunks,
        index,
        chunk,
        voice_config,
        resolved_speaker=None,
    ):
        speaker = str(chunk.get("speaker") or "")
        resolved = resolved_speaker or self._resolve_alias(speaker, voice_config)
        voice_data = voice_config.get(resolved, {})
        resolution = self._resolve_chunk_pronunciation(
            index=index,
            chunk=chunk,
            speaker=speaker,
            resolved_speaker=resolved,
            voice_data=voice_data,
        )
        updated = {
            **chunk,
            **pronunciation_chunk_fields(resolution),
        }
        return updated, resolution

    def _synthesis_config(self, voice_data=None):
        """Return settings that bind this Voice to synthesized chunk audio."""
        return synthesis_binding_config(
            self._load_tts_config(),
            voice_data=voice_data,
        )

    def build_audio_generation_manifest(
        self,
        indices,
        *,
        mode="parallel",
        operation_mode=None,
        generation_seed=None,
        plan_fingerprint=None,
        chunks_fingerprint=None,
    ):
        chunks = self.load_chunks()
        selected = sorted({int(value) for value in indices})
        invalid = [index for index in selected if not 0 <= index < len(chunks)]
        if invalid:
            raise ValueError(f"Unknown chunk indices: {invalid[:10]}")
        voice_config = {}
        if os.path.exists(self.voice_config_path):
            with open(self.voice_config_path, "r", encoding="utf-8") as handle:
                voice_config = json.load(handle)
        if not isinstance(voice_config, dict):
            raise ValueError("voice_config.json must contain a JSON object.")
        engine = self.get_engine()
        if engine is None:
            raise ValueError("TTS engine not initialized")
        tts_config = self._load_tts_config()
        manifest_chunks = []
        for index in selected:
            chunk = chunks[index]
            require_regeneration_unlocked(chunk)
            if not str(chunk.get("text") or "").strip():
                continue
            generation_chunk, continuity = self._chunk_with_spoken_continuity(
                chunks,
                index,
                bind=True,
            )
            speaker = str(chunk.get("speaker") or "")
            resolved = self._resolve_alias(speaker, voice_config)
            voice_data = voice_config.get(resolved, {})
            pronunciation = self._resolve_chunk_pronunciation(
                index=index,
                chunk=generation_chunk,
                speaker=speaker,
                resolved_speaker=resolved,
                voice_data=voice_data,
            )
            generation_chunk.update(pronunciation_chunk_fields(pronunciation))
            synthesis_text = str(pronunciation.get("synthesis_text") or "")
            backend_id = resolve_synthesis_backend_id(
                voice_data,
                mode=str(getattr(engine, "mode", "local") or "local"),
                use_mlx=bool(getattr(engine, "_use_mlx", False)),
            )
            segment_plan = plan_synthesis_segments(
                synthesis_text,
                backend_id=backend_id,
            )
            chunk_key = f"chunk:{chunk.get('id', index)}"
            dependency_payload = {
                "contract": "alexandria_audio_generation_chunk_request_v1",
                "chunk_key": chunk_key,
                "index": index,
                "generation_chunk": {
                    key: copy.deepcopy(generation_chunk.get(key))
                    for key in (
                        "speaker",
                        "text",
                        "instruct",
                        "pause_after",
                        "effective_instruct",
                        "effective_fish_instruct",
                        "spoken_continuity",
                        "spoken_continuity_binding_enabled",
                        "backend_render_plan_fingerprint",
                        "backend_render_plan_binding_enabled",
                        "qwen_render_instruction",
                        "fish_render_instruction",
                        "fish_render_plan",
                        "pronunciation_chunk_entry_fingerprint",
                        "pronunciation_request_fingerprint",
                        "pronunciation_synthesis_text_sha256",
                        "pronunciation_decisions",
                    )
                    if key in generation_chunk
                },
                "resolved_speaker": resolved,
                "voice_data": voice_data,
                "tts_config": tts_config,
                "generation_seed": generation_seed,
                "pronunciation_request_fingerprint": pronunciation["receipt"].get(
                    "request_fingerprint"
                ),
                "synthesis_backend_id": backend_id,
                "segment_plan_fingerprint": segment_plan["plan_fingerprint"],
                "spoken_continuity": continuity,
            }
            chunk_dependency = fingerprint_value(dependency_payload)
            manifest_chunks.append(
                {
                    "chunk_key": chunk_key,
                    "index": index,
                    "chunk_id": copy.deepcopy(chunk.get("id", index)),
                    "dependency_fingerprint": chunk_dependency,
                    "segment_plan_fingerprint": segment_plan["plan_fingerprint"],
                    "segments": [
                        {
                            "segment_id": segment["segment_id"],
                            "segment_index": segment["segment_index"],
                            "source_start": segment["source_start"],
                            "source_end": segment["source_end"],
                            "generation_text_sha256": segment[
                                "generation_text_sha256"
                            ],
                            "dependency_fingerprint": segment[
                                "dependency_fingerprint"
                            ],
                        }
                        for segment in segment_plan["segments"]
                    ],
                }
            )
        if not manifest_chunks:
            raise ValueError("No non-empty, regeneration-eligible chunks were selected.")
        request_dependency = fingerprint_value(
            {
                "contract": "alexandria_audio_generation_request_v1",
                "mode": mode,
                "operation_mode": operation_mode,
                "generation_seed": generation_seed,
                "plan_fingerprint": plan_fingerprint,
                "chunks_fingerprint": chunks_fingerprint,
                "voice_config_fingerprint": fingerprint_value(voice_config),
                "tts_config_fingerprint": fingerprint_value(tts_config),
                "chunks": manifest_chunks,
            }
        )
        return {
            "mode": str(mode),
            "operation_mode": str(operation_mode or "legacy_batch"),
            "generation_seed": generation_seed,
            "plan_fingerprint": plan_fingerprint,
            "chunks_fingerprint": chunks_fingerprint,
            "dependency_fingerprint": request_dependency,
            "chunks": manifest_chunks,
        }

    def _current_audio_generation_identity(self, generation_context):
        request = generation_context.get("manifest_request")
        if not isinstance(request, dict):
            raise AudioGenerationLifecycleError(
                "audio_request_context_invalid",
                "Audio generation context has no request manifest arguments.",
            )
        manifest = self.build_audio_generation_manifest(
            request["indices"],
            mode=request.get("mode", "parallel"),
            operation_mode=request.get("operation_mode"),
            generation_seed=request.get("generation_seed"),
            plan_fingerprint=request.get("plan_fingerprint"),
            chunks_fingerprint=request.get("chunks_fingerprint"),
        )
        manifest["execution"] = copy.deepcopy(
            dict(request.get("execution") or {})
        )
        normalized = normalize_request_manifest(manifest)
        chunk_key = generation_context["chunk_key"]
        chunk = next(
            (
                item
                for item in normalized["chunks"]
                if item["chunk_key"] == chunk_key
            ),
            None,
        )
        if chunk is None:
            raise AudioGenerationLifecycleError(
                "audio_request_dependency_changed",
                f"{chunk_key} is no longer part of the generation request.",
            )
        return normalized["request_fingerprint"], chunk["dependency_fingerprint"]

    @staticmethod
    def _engine_supports_generation_seed(
        engine,
        voice_data,
        *,
        batch=False,
        shared_seed=False,
    ):
        method = getattr(engine, "supports_generation_seed", None)
        if callable(method):
            return bool(
                method(
                    voice_data,
                    batch=batch,
                    shared_seed=shared_seed,
                )
            )
        return voice_supports_deterministic_seed(voice_data)

    @staticmethod
    def _engine_generation_provenance(engine, voice_data):
        return _engine_generation_provenance(engine, voice_data)

    def _generation_seed_resolution(
        self,
        *,
        chunk,
        voice_config,
        resolved_speaker,
        explicit_seed=None,
        seed_supported=None,
    ):
        tts_config = self._load_tts_config()
        return resolve_generation_seed(
            chunk=chunk,
            resolved_speaker=resolved_speaker,
            voice_config=voice_config,
            synthesis_config=self._synthesis_config(
                voice_config.get(resolved_speaker, {})
            ),
            explicit_seed=explicit_seed,
            deterministic_enabled=bool(
                tts_config.get("deterministic_seed_enabled", True)
            ),
            deterministic_base_seed=tts_config.get(
                "deterministic_seed_base"
            ),
            seed_supported=seed_supported,
        )

    @staticmethod
    def _chunk_with_spoken_continuity(chunks, index, *, bind=False):
        chunk = dict(chunks[index])
        continuity = resolve_spoken_continuity(chunks, index)
        if continuity is not None or chunk.get("spoken_continuity_applied") is not None:
            chunk["spoken_continuity"] = continuity
        if bind and continuity is not None:
            chunk["spoken_continuity_binding_enabled"] = True
            if chunk.get("backend_render_plan_fingerprint"):
                chunk["backend_render_plan_binding_enabled"] = True
        qwen_instruction = (
            chunk.get("qwen_render_instruction")
            or chunk.get("instruct", "")
        )
        fish_instruction = (
            chunk.get("fish_render_instruction")
            or qwen_instruction
        )
        chunk["effective_instruct"] = effective_delivery_instruction(
            qwen_instruction,
            continuity,
        )
        chunk["effective_fish_instruct"] = effective_delivery_instruction(
            fish_instruction,
            continuity,
        )
        return chunk, continuity

    def _audio_binding(
        self,
        chunk,
        voice_config,
        resolved_speaker=None,
        seed_resolution=None,
    ):
        approved = approved_audio_binding_fingerprint(chunk)
        if approved is not None:
            return approved
        resolved = resolved_speaker or self._resolve_alias(
            chunk.get("speaker", ""),
            voice_config,
        )
        synthesis = self._synthesis_config(
            voice_config.get(resolved, {})
        )
        if seed_resolution is None:
            seed_resolution = persisted_generation_seed_resolution(chunk)
        if seed_resolution is not None:
            synthesis.update(
                generation_seed_synthesis_binding(seed_resolution)
            )
        return audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker=resolved,
            voice_config=voice_config,
            synthesis_config=synthesis,
        )

    def _mark_audio_generation_started(
        self,
        index,
        chunk,
        seed_resolution=None,
        prompt_resolution=None,
        responsive_resolution=None,
        pronunciation_resolution=None,
    ):
        previous = chunk.get("audio_path") or chunk.get("stale_audio_path")
        seed_fields = (
            generation_seed_chunk_fields(seed_resolution)
            if seed_resolution is not None
            else {}
        )
        prompt_fields = experimental_prompt_chunk_fields(prompt_resolution)
        responsive_fields = recurring_voice_chunk_fields(responsive_resolution)
        pronunciation_fields = (
            pronunciation_chunk_fields(pronunciation_resolution)
            if pronunciation_resolution is not None
            else {
                "pronunciation_registry_fingerprint": None,
                "pronunciation_chunk_entry_fingerprint": None,
                "pronunciation_request_fingerprint": None,
                "pronunciation_synthesis_text_sha256": None,
                "pronunciation_applied_count": None,
                "pronunciation_bypassed_count": None,
                "pronunciation_decisions": None,
            }
        )
        return self._update_chunk_fields(
            index,
            status="generating",
            audio_path=None,
            audio_state="stale" if previous else "generating",
            stale_audio_path=previous,
            audio_fingerprint=None,
            audio_sha256=None,
            audio_size_bytes=None,
            audio_duration_ms=None,
            audio_format=None,
            generation_provenance=None,
            generated_at_utc=None,
            error=None,
            error_code=None,
            **fish_cloud_chunk_reset_fields(),
            **seed_fields,
            **prompt_fields,
            **responsive_fields,
            **pronunciation_fields,
            **synthesis_receipt_reset_fields(),
        )

    def _mark_audio_generation_failed(self, index, error, *, start=False):
        """Persist a safe failure alongside the non-current audio state."""
        if start:
            chunks = self.load_chunks()
            if 0 <= index < len(chunks):
                self._mark_audio_generation_started(index, chunks[index])
        failure = normalize_audio_failure(error)
        self._update_chunk_fields(
            index,
            status="error",
            audio_state="failed",
            error=failure.message,
            error_code=failure.code,
        )
        return failure

    def _mark_audio_generation_cancelled(self, index):
        chunks = self.load_chunks()
        if not 0 <= index < len(chunks):
            return None
        chunk = chunks[index]
        previous = chunk.get("stale_audio_path") or chunk.get("audio_path")
        return self._update_chunk_fields(
            index,
            status="pending",
            audio_path=None,
            audio_state="stale" if previous else "pending",
            stale_audio_path=previous,
            audio_fingerprint=None,
            audio_sha256=None,
            audio_size_bytes=None,
            audio_duration_ms=None,
            audio_format=None,
            error=None,
            error_code=None,
            **synthesis_receipt_reset_fields(),
        )

    def _mark_batch_audio_generation_failed(self, chunks, indices, error):
        """Apply one normalized terminal failure to a batch in memory."""
        failure = normalize_audio_failure(error)
        for index in indices:
            if not (0 <= index < len(chunks)):
                continue
            chunk = chunks[index]
            previous = chunk.get("audio_path") or chunk.get("stale_audio_path")
            chunk.update(
                {
                    "status": "error",
                    "audio_path": None,
                    "audio_state": "failed",
                    "stale_audio_path": previous,
                    "audio_fingerprint": None,
                    "audio_sha256": None,
                    "audio_size_bytes": None,
                    "audio_duration_ms": None,
                    "audio_format": None,
                    "error": failure.message,
                    "error_code": failure.code,
                    **synthesis_receipt_reset_fields(),
                }
            )
        return failure

    @staticmethod
    def _validated_batch_result(batch_results, batch_indices):
        """Validate the complete provider result before interpreting it."""
        if not isinstance(batch_results, dict):
            raise ValueError("Batch provider returned malformed results.")
        completed = batch_results.get("completed")
        failed = batch_results.get("failed")
        if not isinstance(completed, list) or not isinstance(failed, list):
            raise ValueError("Batch provider returned malformed results.")

        expected = set(batch_indices)
        completed_indices = []
        for index in completed:
            if type(index) is not int or index not in expected:
                raise ValueError("Batch provider returned malformed results.")
            completed_indices.append(index)

        failed_entries = []
        failed_indices = []
        for entry in failed:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError("Batch provider returned malformed results.")
            index, reason = entry
            if type(index) is not int or index not in expected:
                raise ValueError("Batch provider returned malformed results.")
            failed_entries.append((index, reason))
            failed_indices.append(index)

        completed_set = set(completed_indices)
        failed_set = set(failed_indices)
        if (
            len(completed_set) != len(completed_indices)
            or len(failed_set) != len(failed_indices)
            or completed_set & failed_set
            or completed_set | failed_set != expected
        ):
            raise ValueError("Batch provider returned malformed results.")
        return completed_indices, failed_entries

    def _install_chunk_audio(
        self,
        *,
        index,
        chunk,
        resolved_speaker,
        voice_config,
        source_path,
        previous_audio_path,
        seed_resolution=None,
        expected_text=None,
        take_id=None,
        artifact_fields=None,
        generation_context=None,
    ):
        resolved_take_id = take_id or new_take_id(kind="raw")
        chunk_key_value = audio_take_chunk_key(chunk, index)
        destination_dir = take_directory(
            self.root_dir,
            chunk_key_value,
        )
        filename_base = take_filename_base(resolved_take_id)
        watched = [
            (destination_dir / f"{filename_base}.{suffix}").resolve().relative_to(Path(self.root_dir).resolve()).as_posix()
            for suffix in ("mp3", "wav")
        ]
        if isinstance(generation_context, dict):
            recovery_path = publication_recovery_path(
                self.root_dir,
                generation_context["request_id"],
                generation_context["chunk_key"],
            )
            watched.extend(
                [
                    audio_take_registry_path(self.root_dir).name,
                    Path(self.chunks_path).name,
                    recovery_path.relative_to(
                        Path(self.root_dir).resolve()
                    ).as_posix(),
                ]
            )
        with audio_mutation_guard(
            self.root_dir,
            transition="immutable_take_installation",
            operation_id=f"take-install-{resolved_take_id}",
            watched_paths=watched,
        ) as transition:
            artifact = install_generated_audio(
                root_dir=self.root_dir,
                voicelines_dir=destination_dir,
                source_audio_path=source_path,
                filename_base=filename_base,
                binding_fingerprint=self._audio_binding(
                    chunk, voice_config, resolved_speaker, seed_resolution=seed_resolution
                ),
                previous_audio_path=previous_audio_path,
                text=str(expected_text if expected_text is not None else chunk.get("text") or ""),
                before_commit=lambda canonical, content: transition[
                    "prepare_binary_write"
                ](
                    canonical.relative_to(Path(self.root_dir).resolve()).as_posix(),
                    content,
                ),
            ) | {"take_id": resolved_take_id, "take_chunk_key": chunk_key_value}
            artifact.update(copy.deepcopy(dict(artifact_fields or {})))
            transition["required_artifacts"] = {
                artifact["audio_path"]: artifact["audio_sha256"]
            }
            if isinstance(generation_context, dict):
                type(self)._register_generated_take(
                    self,
                    index=index,
                    chunk={**chunk, **artifact},
                    resolved_speaker=resolved_speaker,
                    voice_config=voice_config,
                    artifact=artifact,
                    generation_context=generation_context,
                    transition_outcome=transition,
                )
                artifact["_publication_committed"] = True
        return artifact

    def _register_generated_take(
        self,
        *,
        index,
        chunk,
        resolved_speaker,
        voice_config,
        artifact,
        generation_context=None,
        transition_outcome=None,
    ):
        if artifact.pop("_publication_committed", False):
            return None, None
        take_id = str(artifact["take_id"])
        chunk_key_value = str(artifact["take_chunk_key"])
        seam_receipt = copy.deepcopy(artifact.get("synthesis_seam_receipt"))
        original_sample_count = (
            artifact.get("synthesis_final_sample_count")
            or artifact.get("audio_sample_count")
        )
        original_sample_rate = (
            artifact.get("synthesis_sample_rate")
            or artifact.get("audio_sample_rate")
        )
        chunk_audio_fields = {
            key: copy.deepcopy(value)
            for key, value in artifact.items()
            if key not in {
                "take_id",
                "take_chunk_key",
                "current_take_id",
                "take_registry_fingerprint",
                "take_record_fingerprint",
            }
        }
        record = build_take_record(
            take_id=take_id,
            chunk_key_value=chunk_key_value,
            chunk_index=index,
            kind="raw",
            source_take_id=None,
            root_take_id=take_id,
            artifact={
                "relative_path": artifact.get("audio_path"),
                "sha256": artifact.get("audio_sha256"),
                "size_bytes": artifact.get("audio_size_bytes"),
                "duration_ms": artifact.get("audio_duration_ms"),
                "format": artifact.get("audio_format"),
                "sample_rate": original_sample_rate,
                "sample_count": original_sample_count,
                "channels": artifact.get("audio_channels"),
                "installed_sample_rate": artifact.get("audio_sample_rate"),
                "installed_sample_count": artifact.get("audio_sample_count"),
                "installed_sample_width": artifact.get("audio_sample_width"),
            },
            authored={
                "text": str(chunk.get("text") or ""),
                "text_fingerprint": fingerprint_value(
                    str(chunk.get("text") or "")
                ),
                "speaker": str(chunk.get("speaker") or ""),
                "resolved_speaker": resolved_speaker,
                "direction": str(chunk.get("instruct") or ""),
                "effective_direction": str(
                    artifact.get("spoken_continuity_effective_instruct")
                    or chunk.get("effective_instruct")
                    or chunk.get("instruct")
                    or ""
                ),
                "pause_after_ms": effective_pause_after_ms(chunk),
            },
            voice={
                "resolved_speaker": resolved_speaker,
                "configuration": copy.deepcopy(
                    voice_config.get(resolved_speaker, {})
                ),
                "binding_fingerprint": artifact.get("audio_fingerprint"),
                "experimental_prompt": {
                    key: copy.deepcopy(value)
                    for key, value in artifact.items()
                    if key.startswith("experimental_prompt_")
                },
                "responsive_route": {
                    key: copy.deepcopy(value)
                    for key, value in artifact.items()
                    if key.startswith("responsive_voice_")
                },
            },
            generation={
                "request_id": (
                    generation_context.get("request_id")
                    if isinstance(generation_context, dict)
                    else None
                ),
                "request_fingerprint": (
                    generation_context.get("request_fingerprint")
                    if isinstance(generation_context, dict)
                    else None
                ),
                "audio_fingerprint": artifact.get("audio_fingerprint"),
                "seed": artifact.get("generation_seed"),
                "seed_source": artifact.get("generation_seed_source"),
                "seed_basis": artifact.get("generation_seed_basis"),
                "provenance": copy.deepcopy(
                    artifact.get("generation_provenance")
                ),
                "synthesis_settings": self._synthesis_config(
                    voice_config.get(resolved_speaker, {})
                ),
                "backend_render_plan": copy.deepcopy(
                    artifact.get("backend_render_plan_applied")
                ),
                "pronunciation_decisions": copy.deepcopy(
                    artifact.get("pronunciation_decisions")
                ),
                "chunk_audio_fields": chunk_audio_fields,
            },
            synthesis={
                "window_backend": artifact.get("synthesis_window_backend"),
                "window_declaration_fingerprint": artifact.get(
                    "synthesis_window_declaration_fingerprint"
                ),
                "segment_plan_fingerprint": artifact.get(
                    "synthesis_segment_plan_fingerprint"
                ),
                "segment_dependency_fingerprint": artifact.get(
                    "synthesis_segment_dependency_fingerprint"
                ),
                "segment_count": artifact.get("synthesis_segment_count"),
                "segment_backend_metadata": copy.deepcopy(
                    artifact.get("synthesis_segment_backend_metadata")
                ),
                "seam_receipt": seam_receipt,
                "seam_receipt_fingerprint": artifact.get(
                    "synthesis_seam_receipt_fingerprint"
                ),
                "original_sample_count": original_sample_count,
                "sample_rate": original_sample_rate,
            },
            review={
                "state": artifact.get("listening_state") or "unreviewed",
                "review_required": bool(artifact.get("review_required")),
                "listening_required": bool(artifact.get("listening_required")),
            },
        )
        if isinstance(generation_context, dict):
            with audio_project_lock(self.root_dir), self._chunks_lock:
                chunks = self.load_chunks()
                registered, registry = plan_take_registration(
                    self.root_dir,
                    chunks=chunks,
                    record=record,
                )
                artifact.update(
                    {
                        "current_take_id": registered["take_id"],
                        "take_record_fingerprint": registered[
                            "record_fingerprint"
                        ],
                        "take_registry_fingerprint": registry[
                            "registry_fingerprint"
                        ],
                        "stale_audio_path": None,
                    }
                )
                published_artifact = copy.deepcopy(artifact)
                published_artifact.pop("take_id", None)
                published_artifact.pop("take_chunk_key", None)
                receipt = publication_recovery_receipt(
                    generation_context["request_id"],
                    generation_context["request_fingerprint"],
                    generation_context["chunk_key"],
                    published_artifact,
                )
                chunks[index].update(
                    status="done",
                    error=None,
                    error_code=None,
                    **published_artifact,
                )
                recovery_path = publication_recovery_path(
                    self.root_dir,
                    generation_context["request_id"],
                    generation_context["chunk_key"],
                )
                json_writes = {
                    audio_take_registry_path(self.root_dir).name: registry,
                    Path(self.chunks_path).name: chunks,
                    recovery_path.relative_to(
                        Path(self.root_dir).resolve()
                    ).as_posix(): receipt,
                }
                if transition_outcome is not None:
                    transition_outcome["required_artifacts"] = {
                        artifact["audio_path"]: artifact["audio_sha256"]
                    }
                    transition_outcome["prepare_json_writes"](json_writes)
                    for relative, value in sorted(json_writes.items()):
                        crash_reconciliation.atomic_json_write(
                            value,
                            Path(self.root_dir) / relative,
                        )
                else:
                    apply_audio_transition(
                        self.root_dir,
                        transition="lifecycle_receipt_publication",
                        operation_id=(
                            "publication-result-" + fingerprint_value(receipt)[:24]
                        ),
                        json_writes=json_writes,
                        required_artifacts={
                            artifact["audio_path"]: artifact["audio_sha256"]
                        },
                    )
                return registered, registry
        registered, registry = register_take(
            self.root_dir,
            chunks=self.load_chunks(),
            record=record,
        )
        artifact.update(
            {
                "current_take_id": registered["take_id"],
                "take_record_fingerprint": registered[
                    "record_fingerprint"
                ],
                "take_registry_fingerprint": registry[
                    "registry_fingerprint"
                ],
                "stale_audio_path": None,
            }
        )
        return registered, registry

    @staticmethod
    def _remove_generated_temp(path: str | Path) -> None:
        temporary = Path(path)
        for attempt in range(3):
            try:
                temporary.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
        print(f"Warning: Could not delete temp file {temporary}")

    def load_chunks(self):
        if os.path.exists(self.chunks_path):
            try:
                with open(self.chunks_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"WARNING: chunks.json is corrupted ({e}). Regenerating from script...")
                os.remove(self.chunks_path)

        # If no chunks (or corrupted), generate from script
        if os.path.exists(self.script_path):
            try:
                with open(self.script_path, "r", encoding="utf-8") as f:
                    script = json.load(f)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"WARNING: annotated_script.json is also corrupted ({e}). Starting with empty chunks.")
                return []

            chunks = group_into_chunks(script)

            # Initialize chunk status
            for i, chunk in enumerate(chunks):
                chunk["id"] = i
                chunk["status"] = "pending" # pending, generating, done, error
                chunk["audio_path"] = None

            self.save_chunks(chunks)
            return chunks

        return []

    def _resolve_alias(self, speaker, voice_config):
        """Resolve a speaker to one validated independent production voice."""
        if not speaker:
            return speaker
        resolution = resolve_voice_alias(speaker, voice_config)
        if resolution.is_alias:
            logger.info(
                "Resolved voice alias %s",
                " -> ".join(resolution.chain),
            )
        return resolution.resolved_target

    def save_chunks(self, chunks):
        with audio_project_lock(self.root_dir), self._chunks_lock:
            operation_id = f"chunks-{fingerprint_value(chunks)[:24]}"
            apply_audio_transition(
                self.root_dir,
                transition="chunks_metadata",
                operation_id=operation_id,
                json_writes={Path(self.chunks_path).name: chunks},
            )

    def _update_chunk_fields(self, index, **fields):
        """Atomically update fields on a single chunk (thread-safe read-modify-write).

        Unlike load_chunks() + modify + save_chunks(), this holds the lock for the
        entire read-modify-write cycle, preventing concurrent threads from
        overwriting each other's updates.
        """
        with audio_project_lock(self.root_dir), self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            if not (0 <= index < len(chunks)):
                return None
            chunks[index].update(fields)
            atomic_json_write(chunks, self.chunks_path)
            return chunks[index]

    def insert_chunk(self, after_index):
        """Insert an empty chunk after the given index. Returns the new chunk list."""
        with audio_project_lock(self.root_dir), self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            if not (0 <= after_index < len(chunks)):
                return None

            # Copy speaker from the row we're splitting from
            source = chunks[after_index]
            new_chunk = {
                "id": after_index + 1,
                "speaker": source.get("speaker", "NARRATOR"),
                "text": "",
                "instruct": "",
                "status": "pending",
                "audio_path": None
            }
            chunks.insert(after_index + 1, new_chunk)

            # Re-number all IDs
            for i, chunk in enumerate(chunks):
                chunk["id"] = i

            atomic_json_write(chunks, self.chunks_path)
            return chunks

    def delete_chunk(self, index):
        """Delete a chunk at the given index. Returns (deleted_chunk, updated_chunks) or None."""
        with audio_project_lock(self.root_dir), self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            if not (0 <= index < len(chunks)):
                return None
            if len(chunks) <= 1:
                return None  # don't allow deleting the last chunk

            deleted = chunks.pop(index)

            # Re-number all IDs
            for i, chunk in enumerate(chunks):
                chunk["id"] = i

            atomic_json_write(chunks, self.chunks_path)
            return deleted, chunks

    def restore_chunk(self, at_index, chunk_data):
        """Re-insert a chunk at a specific index. Returns the updated chunk list."""
        with audio_project_lock(self.root_dir), self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return None
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)

            at_index = max(0, min(at_index, len(chunks)))
            chunks.insert(at_index, chunk_data)

            # Re-number all IDs
            for i, chunk in enumerate(chunks):
                chunk["id"] = i

            atomic_json_write(chunks, self.chunks_path)
            return chunks

    def update_chunk(self, index, data):
        with audio_project_lock(self.root_dir), self._chunks_lock:
            return self._update_chunk_locked(index, data)

    def _update_chunk_locked(self, index, data):
        chunks = self.load_chunks()
        if 0 <= index < len(chunks):
            before_chunks = copy.deepcopy(chunks)
            chunk = chunks[index]
            # Update fields
            if "text" in data: chunk["text"] = data["text"]
            if "instruct" in data: chunk["instruct"] = data["instruct"]
            if "speaker" in data: chunk["speaker"] = data["speaker"]

            # pause_after: set or clear (None removes the key)
            if "pause_after" in data:
                if data["pause_after"] is not None:
                    chunk["pause_after"] = max(0, int(data["pause_after"]))
                else:
                    chunk.pop("pause_after", None)

            # Any synthesis-relevant edit invalidates the prior audio immediately.
            # Persisted Takes remain immutable and are deselected rather than
            # moved or deleted. Legacy audio remains available through the
            # compatibility stale pointer.
            if "text" in data or "instruct" in data or "speaker" in data:
                previous = chunk.get("audio_path") or chunk.get("stale_audio_path")
                take_plan = prepare_invalidation_registry(
                    self.root_dir,
                    before_chunks,
                    invalidations=[
                        {
                            "chunk_id": before_chunks[index].get("id", index),
                            "audio_path": previous,
                            "reason": "authored chunk changed",
                        }
                    ],
                )
                clear_approved_audio_fields(chunk)
                chunk.update(
                    {
                        "status": "pending",
                        "audio_path": None,
                        "audio_state": "stale" if previous else "pending",
                        "stale_audio_path": previous,
                        "audio_fingerprint": None,
                        "audio_sha256": None,
                        "audio_size_bytes": None,
                        "audio_duration_ms": None,
                        "audio_format": None,
                        "error": None,
                        "error_code": None,
                        "generation_seed": None,
                        "generation_seed_source": None,
                        "generation_seed_basis": None,
                        "audio_research_only": False,
                        "audio_production_prompt_approved": False,
                        "experimental_prompt_route": None,
                        "experimental_prompt_role": None,
                        "experimental_prompt_mapping_reason": None,
                        "experimental_prompt_evidence_round_id": None,
                        "experimental_prompt_routing_fingerprint": None,
                        "experimental_prompt_reference_sha256": None,
                        "responsive_voice_route": None,
                        "responsive_voice_backend": None,
                        "responsive_voice_fallback_backend": None,
                        "responsive_voice_used_backend": None,
                        "responsive_voice_fallback_used": False,
                        "responsive_voice_backend_error": None,
                        "responsive_voice_specialist_attempt_count": None,
                        "responsive_voice_repair_strategy": None,
                        "responsive_voice_text_verification": None,
                        "responsive_voice_mapping_reason": None,
                        "responsive_voice_evidence_round_id": None,
                        "responsive_voice_routing_fingerprint": None,
                        "responsive_voice_effect_chain": None,
                        "responsive_voice_effect_receipt": None,
                        "responsive_voice_approval_tier": None,
                        "production_promotion_allowed": False,
                        **fish_cloud_chunk_reset_fields(),
                        **synthesis_receipt_reset_fields(),
                    }
                )
                if take_plan.get("changed"):
                    chunk["current_take_id"] = None
                    chunk["take_record_fingerprint"] = None
                    chunk["take_registry_fingerprint"] = take_plan[
                        "registry_fingerprint"
                    ]
                writes = {Path(self.chunks_path).name: chunks}
                if take_plan.get("changed"):
                    writes[audio_take_registry_path(self.root_dir).name] = take_plan["registry"]
                operation_id = (
                    "authored-invalidation-"
                    + fingerprint_value({"index": index, "writes": writes})[:24]
                )
                apply_audio_transition(
                    self.root_dir,
                    transition="invalidation",
                    operation_id=operation_id,
                    json_writes=writes,
                )
            else:
                self.save_chunks(chunks)
            return chunk
        return None

    def invalidate_chunk_audio(self, indices, *, operation_id, reason):
        """Mark selected generated chunks stale without altering authored content."""
        selected = sorted({int(value) for value in indices})
        with audio_project_lock(self.root_dir), self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return []
            with open(self.chunks_path, "r", encoding="utf-8") as handle:
                chunks = json.load(handle)
            before_chunks = copy.deepcopy(chunks)
            invalid = [index for index in selected if not 0 <= index < len(chunks)]
            if invalid:
                raise ValueError(f"Unknown chunk indices: {invalid[:10]}")
            changed = []
            invalidations = []
            for index in selected:
                chunk = chunks[index]
                require_regeneration_unlocked(chunk)
                if chunk.get("status") == "generating":
                    raise ValueError(
                        f"Chunk {index} is generating and cannot be invalidated."
                    )
                previous = chunk.get("audio_path") or chunk.get("stale_audio_path")
                if not previous:
                    continue
                invalidations.append(
                    {
                        "chunk_id": chunk.get("id", index),
                        "audio_path": previous,
                        "reason": str(reason),
                    }
                )
                chunk.update(
                    {
                        "status": "pending",
                        "audio_path": None,
                        "audio_state": "stale",
                        "stale_audio_path": previous,
                        "audio_fingerprint": None,
                        "audio_sha256": None,
                        "audio_size_bytes": None,
                        "audio_duration_ms": None,
                        "audio_format": None,
                        "error": None,
                        "error_code": None,
                        "invalidated_by_operation": operation_id,
                        "audio_invalidation_reason": str(reason),
                        **synthesis_receipt_reset_fields(),
                    }
                )
                changed.append(index)
            if changed:
                take_plan = prepare_invalidation_registry(
                    self.root_dir,
                    before_chunks,
                    invalidations=invalidations,
                )
                if take_plan.get("changed"):
                    for index in changed:
                        chunks[index]["current_take_id"] = None
                        chunks[index]["take_record_fingerprint"] = None
                        chunks[index]["take_registry_fingerprint"] = take_plan[
                            "registry_fingerprint"
                        ]
                writes = {Path(self.chunks_path).name: chunks}
                if take_plan.get("changed"):
                    writes[
                        audio_take_registry_path(self.root_dir).name
                    ] = take_plan["registry"]
                apply_audio_transition(
                    self.root_dir,
                    transition="invalidation",
                    operation_id=str(operation_id),
                    json_writes=writes,
                )
            return changed

    def rebind_chunk_audio(self, indices, *, operation_id, reason):
        """Migrate verified current audio to the active dependency fingerprint."""
        selected = sorted({int(value) for value in indices})
        with audio_project_lock(self.root_dir), self._chunks_lock:
            if not os.path.exists(self.chunks_path):
                return []
            with open(self.chunks_path, "r", encoding="utf-8") as handle:
                chunks = json.load(handle)
            with open(self.voice_config_path, "r", encoding="utf-8") as handle:
                voice_config = json.load(handle)
            invalid = [index for index in selected if not 0 <= index < len(chunks)]
            if invalid:
                raise ValueError(f"Unknown chunk indices: {invalid[:10]}")
            changed = []
            for index in selected:
                chunk = chunks[index]
                relative = chunk.get("audio_path")
                if (
                    chunk.get("status") != "done"
                    or chunk.get("audio_state") != "current"
                    or not relative
                ):
                    raise ValueError(
                        f"Chunk {index} has no current audio available for rebinding."
                    )
                path = (Path(self.root_dir) / str(relative)).resolve()
                try:
                    path.relative_to(Path(self.root_dir).resolve())
                except ValueError as exc:
                    raise ValueError(f"Chunk {index} audio path is unsafe.") from exc
                if not path.is_file():
                    raise ValueError(f"Chunk {index} audio file is missing.")
                recorded_hash = chunk.get("audio_sha256")
                if not recorded_hash or sha256_file(path) != recorded_hash:
                    raise ValueError(f"Chunk {index} audio hash does not match its record.")
                resolved = self._resolve_alias(chunk.get("speaker", ""), voice_config)
                pronunciation_chunk, pronunciation_resolution = (
                    self._chunk_with_pronunciation(
                        chunks=chunks,
                        index=index,
                        chunk=chunk,
                        voice_config=voice_config,
                        resolved_speaker=resolved,
                    )
                )
                current_pronunciation_fingerprint = (
                    pronunciation_resolution["receipt"][
                        "request_fingerprint"
                    ]
                )
                recorded_pronunciation_fingerprint = str(
                    chunk.get("pronunciation_request_fingerprint") or ""
                ).strip()
                if (
                    recorded_pronunciation_fingerprint
                    and recorded_pronunciation_fingerprint
                    != current_pronunciation_fingerprint
                ):
                    raise ValueError(
                        f"Chunk {index} pronunciation changed and requires regeneration."
                    )
                expected = self._audio_binding(
                    pronunciation_chunk,
                    voice_config,
                    resolved_speaker=resolved,
                )
                chunk.update(
                    {
                        "audio_fingerprint": expected,
                        **pronunciation_chunk_fields(
                            pronunciation_resolution
                        ),
                        "audio_rebound_by_operation": operation_id,
                        "audio_rebound_reason": str(reason),
                        "audio_rebound_at_utc": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(),
                        ),
                    }
                )
                changed.append(index)
            if changed:
                atomic_json_write(chunks, self.chunks_path)
            return changed

    def audio_take_status(self, index):
        chunks = self.load_chunks()
        return public_chunk_takes(
            self.root_dir,
            chunks,
            index=int(index),
        )

    @staticmethod
    def _final_listen_source_order(chunks):
        return chapter_source_order_fingerprint(chunks)

    def _require_final_listen_source_order(
        self,
        chunks,
        expected_source_order_fingerprint,
    ):
        current = self._final_listen_source_order(chunks)
        if current != str(expected_source_order_fingerprint):
            raise AudioTakeError(
                "audio_take_final_listen_order_changed",
                "Canonical Script order changed after Final Listen was reviewed.",
                context={"current_source_order_fingerprint": current},
            )
        return current

    def set_final_listen_pin(
        self,
        index,
        *,
        take_id,
        pinned,
        expected_registry_fingerprint,
        expected_record_fingerprint,
        expected_source_order_fingerprint,
    ):
        chunks = self.load_chunks()
        index = int(index)
        source_order = self._require_final_listen_source_order(
            chunks,
            expected_source_order_fingerprint,
        )
        result = set_audio_take_final_listen_pin(
            self.root_dir,
            chunks=chunks,
            chunks_path=self.chunks_path,
            index=index,
            take_id=str(take_id),
            pinned=bool(pinned),
            expected_registry_fingerprint=str(
                expected_registry_fingerprint
            ),
            expected_record_fingerprint=str(
                expected_record_fingerprint
            ),
            source_order_fingerprint=source_order,
        )
        result["source_order_fingerprint"] = source_order
        return result

    def set_final_listen_pause(
        self,
        index,
        *,
        take_id,
        pause_after_ms,
        expected_registry_fingerprint,
        expected_record_fingerprint,
        expected_source_order_fingerprint,
    ):
        chunks = self.load_chunks()
        index = int(index)
        source_order = self._require_final_listen_source_order(
            chunks,
            expected_source_order_fingerprint,
        )
        result = set_audio_take_final_listen_pause(
            self.root_dir,
            chunks=chunks,
            chunks_path=self.chunks_path,
            index=index,
            take_id=str(take_id),
            pause_after_ms=(
                None if pause_after_ms is None else int(pause_after_ms)
            ),
            expected_registry_fingerprint=str(
                expected_registry_fingerprint
            ),
            expected_record_fingerprint=str(
                expected_record_fingerprint
            ),
        )
        result["source_order_fingerprint"] = source_order
        return result

    def create_final_listen_rendition(
        self,
        index,
        *,
        source_take_id,
        expected_source_sha256,
        expected_registry_fingerprint,
        expected_source_record_fingerprint,
        expected_source_order_fingerprint,
        operation,
        settings,
    ):
        index = int(index)
        root = Path(self.root_dir).resolve()
        with audio_project_lock(root), self._chunks_lock:
            chunks = self.load_chunks()
            source_order = self._require_final_listen_source_order(
                chunks,
                expected_source_order_fingerprint,
            )
            if not 0 <= index < len(chunks):
                raise AudioTakeError(
                    "audio_take_chunk_missing",
                    "The requested chunk no longer exists.",
                )
            registry = audio_take_registry_view(root, chunks)
            if registry["registry_fingerprint"] != str(
                expected_registry_fingerprint
            ):
                raise AudioTakeError(
                    "audio_take_registry_changed",
                    "Audio Takes changed after this Final Listen action was reviewed.",
                    context={
                        "current_registry_fingerprint": registry[
                            "registry_fingerprint"
                        ]
                    },
                )
            key = audio_take_chunk_key(chunks[index], index)
            source = registry["takes"].get(str(source_take_id))
            if (
                not isinstance(source, dict)
                or source.get("chunk_key") != key
            ):
                raise AudioTakeError(
                    "audio_take_missing",
                    "The source Take no longer exists for this chunk.",
                )
            if source.get("record_fingerprint") != str(
                expected_source_record_fingerprint
            ):
                raise AudioTakeError(
                    "audio_take_changed",
                    "The source Take changed after this Final Listen action was reviewed.",
                )
            if (registry["chunks"].get(key) or {}).get(
                "current_take_id"
            ) != source["take_id"]:
                raise AudioTakeError(
                    "audio_take_final_listen_not_current",
                    "Final Listen processing starts only from the current Take.",
                )
            source_relative = str(
                (source.get("artifact") or {}).get("relative_path") or ""
            )
            source_path = confined_audio_path(root, source_relative)
            expected_sha = str(expected_source_sha256).casefold()
            if (
                not source_path.is_file()
                or source["artifact"].get("sha256") != expected_sha
                or sha256_file(source_path) != expected_sha
            ):
                raise AudioTakeError(
                    "audio_take_artifact_mismatch",
                    "The source Take audio is missing or changed.",
                )
            with tempfile.TemporaryDirectory(
                prefix=".final-listen-",
                dir=root,
            ) as temporary:
                candidate = Path(temporary) / "rendition.wav"
                processing = create_processed_rendition(
                    source_audio_path=source_path,
                    output_path=candidate,
                    operation=str(operation),
                    settings=copy.deepcopy(dict(settings or {})),
                )
                result = self.register_audio_rendition(
                    index,
                    source_take_id=source["take_id"],
                    source_audio_path=candidate,
                    expected_source_sha256=processing["output_sha256"],
                    expected_registry_fingerprint=registry[
                        "registry_fingerprint"
                    ],
                    expected_source_record_fingerprint=source[
                        "record_fingerprint"
                    ],
                    processing=processing,
                    review={
                        "state": "needs_listening",
                        "review_required": True,
                        "listening_required": True,
                        "final_listen_operation": processing["operation"],
                    },
                )
            result["source_order_fingerprint"] = source_order
            result["processing"] = processing
            return result

    def _mastering_context(self, index, source_take_id):
        chunks = self.load_chunks()
        index = int(index)
        if not 0 <= index < len(chunks):
            raise AudioTakeError(
                "audio_take_chunk_missing",
                "The requested chunk no longer exists.",
            )
        source_order = self._final_listen_source_order(chunks)
        registry = audio_take_registry_view(self.root_dir, chunks)
        key = audio_take_chunk_key(chunks[index], index)
        source = registry["takes"].get(str(source_take_id))
        if not isinstance(source, dict) or source.get("chunk_key") != key:
            raise AudioTakeError(
                "audio_take_missing",
                "The source Take no longer exists for this chunk.",
            )
        entry = registry["chunks"].get(key) or {}
        current = entry.get("current_take_id") == source["take_id"]
        review = source.get("review") or {}
        pinned = review.get("final_listen_pinned") is True
        pin_order = str(
            review.get("final_listen_source_order_fingerprint") or ""
        )
        artifact = source.get("artifact") or {}
        source_relative = str(artifact.get("relative_path") or "")
        source_path = confined_audio_path(self.root_dir, source_relative)
        source_sha256 = str(artifact.get("sha256") or "").casefold()
        return {
            "chunks": chunks,
            "index": index,
            "chunk_key": key,
            "source_order_fingerprint": source_order,
            "registry": registry,
            "source": source,
            "current": current,
            "pinned": pinned,
            "pin_source_order_fingerprint": pin_order,
            "source_path": source_path,
            "source_sha256": source_sha256,
        }

    def build_publication_mastering_plan(
        self,
        index,
        *,
        source_take_id,
        expected_source_sha256,
        expected_registry_fingerprint,
        expected_source_record_fingerprint,
        expected_source_order_fingerprint,
        settings,
        provenance=None,
    ):
        context = self._mastering_context(index, source_take_id)
        source = context["source"]
        if context["registry"]["registry_fingerprint"] != str(
            expected_registry_fingerprint
        ):
            raise AudioTakeError(
                "audio_take_registry_changed",
                "Audio Takes changed after mastering was reviewed.",
                context={
                    "current_registry_fingerprint": context["registry"][
                        "registry_fingerprint"
                    ]
                },
            )
        if source.get("record_fingerprint") != str(
            expected_source_record_fingerprint
        ):
            raise AudioTakeError(
                "audio_take_changed",
                "The source Take changed after mastering was reviewed.",
            )
        expected_sha = str(expected_source_sha256).casefold()
        if (
            context["source_sha256"] != expected_sha
            or not context["source_path"].is_file()
            or sha256_file(context["source_path"]) != expected_sha
        ):
            raise AudioTakeError(
                "audio_take_artifact_mismatch",
                "The source Take audio is missing or changed.",
            )
        if context["source_order_fingerprint"] != str(
            expected_source_order_fingerprint
        ):
            raise AudioTakeError(
                "audio_take_final_listen_order_changed",
                "Canonical Script order changed after mastering was reviewed.",
                context={
                    "current_source_order_fingerprint": context[
                        "source_order_fingerprint"
                    ]
                },
            )
        if not context["current"]:
            raise AudioTakeError(
                "audio_take_final_listen_not_current",
                "Publication mastering starts only from the current Take.",
            )
        if not context["pinned"]:
            raise AudioMasteringError(
                "audio_mastering_final_listen_required",
                "Pin the current Take after Final Listen before mastering it.",
            )
        if context["pin_source_order_fingerprint"] != context[
            "source_order_fingerprint"
        ]:
            raise AudioMasteringError(
                "audio_mastering_final_listen_stale",
                "The Final Listen pin belongs to an older canonical Script order.",
            )
        plan = build_mastering_plan(
            take=source,
            registry_fingerprint=context["registry"]["registry_fingerprint"],
            source_order_fingerprint=context["source_order_fingerprint"],
            settings=copy.deepcopy(dict(settings or {})),
            provenance=(
                copy.deepcopy(dict(provenance))
                if isinstance(provenance, dict)
                else None
            ),
        )
        return {
            **plan,
            "chunk_key": context["chunk_key"],
            "chunk_index": context["index"],
        }

    def publication_mastering_dependency(
        self,
        index,
        *,
        source_take_id,
        settings,
    ):
        normalized = normalize_mastering_settings(
            copy.deepcopy(dict(settings or {}))
        )
        try:
            context = self._mastering_context(index, source_take_id)
        except (AudioTakeError, AudioArtifactError):
            return fingerprint_value(
                {
                    "contract": "alexandria_publication_mastering_dependency_v1",
                    "status": "source_unavailable",
                    "index": int(index),
                    "take_id": str(source_take_id),
                    "settings_fingerprint": normalized[
                        "settings_fingerprint"
                    ],
                }
            )
        source = context["source"]
        return mastering_dependency_fingerprint(
            take_id=source["take_id"],
            record_fingerprint=str(source.get("record_fingerprint") or ""),
            source_sha256=context["source_sha256"],
            registry_fingerprint=context["registry"]["registry_fingerprint"],
            source_order_fingerprint=context["source_order_fingerprint"],
            settings_fingerprint=normalized["settings_fingerprint"],
        )

    def prepare_publication_mastering_candidate(
        self,
        index,
        *,
        plan,
        output_path,
        cancel_check=None,
        progress_callback=None,
    ):
        current = self.build_publication_mastering_plan(
            index,
            source_take_id=plan["take_id"],
            expected_source_sha256=plan["source_sha256"],
            expected_registry_fingerprint=plan["registry_fingerprint"],
            expected_source_record_fingerprint=plan["record_fingerprint"],
            expected_source_order_fingerprint=plan[
                "source_order_fingerprint"
            ],
            settings=plan["settings"],
            provenance=plan.get("provenance"),
        )
        if current["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise AudioMasteringError(
                "audio_mastering_plan_changed",
                "The mastering plan changed before processing began.",
            )
        context = self._mastering_context(index, plan["take_id"])
        return create_mastered_candidate(
            source_audio_path=context["source_path"],
            output_path=output_path,
            settings=plan["settings"],
            provenance=plan.get("provenance"),
            cancel_check=cancel_check,
            progress_callback=progress_callback,
        )

    def publish_publication_mastering_candidate(
        self,
        index,
        *,
        plan,
        candidate_path,
        processing,
        mastering_job_id=None,
    ):
        current = self.build_publication_mastering_plan(
            index,
            source_take_id=plan["take_id"],
            expected_source_sha256=plan["source_sha256"],
            expected_registry_fingerprint=plan["registry_fingerprint"],
            expected_source_record_fingerprint=plan["record_fingerprint"],
            expected_source_order_fingerprint=plan[
                "source_order_fingerprint"
            ],
            settings=plan["settings"],
            provenance=plan.get("provenance"),
        )
        if current["dependency_fingerprint"] != plan[
            "dependency_fingerprint"
        ]:
            raise AudioMasteringError(
                "audio_mastering_dependencies_changed",
                "Mastering dependencies changed before publication.",
            )
        processing_value = copy.deepcopy(dict(processing or {}))
        processing_value.update(
            {
                "mastering_plan_fingerprint": plan["plan_fingerprint"],
                "mastering_dependency_fingerprint": plan[
                    "dependency_fingerprint"
                ],
                "mastering_job_id": str(mastering_job_id or "") or None,
                "publication_state": "published_child_rendition",
            }
        )
        processing_value["processing_fingerprint"] = fingerprint_value(
            {
                key: value
                for key, value in processing_value.items()
                if key != "processing_fingerprint"
            }
        )
        result = self.register_audio_rendition(
            int(index),
            source_take_id=plan["take_id"],
            source_audio_path=candidate_path,
            expected_source_sha256=processing_value["output_sha256"],
            expected_registry_fingerprint=plan["registry_fingerprint"],
            expected_source_record_fingerprint=plan["record_fingerprint"],
            processing=processing_value,
            review={
                "state": "needs_listening",
                "review_required": True,
                "listening_required": True,
                "final_listen_operation": "publication_mastering",
                "mastering_provenance": copy.deepcopy(
                    processing_value.get("provenance") or {}
                ),
            },
        )
        result["processing"] = processing_value
        result["source_order_fingerprint"] = plan[
            "source_order_fingerprint"
        ]
        return result

    def _take_compatible_audio_promotion(
        self,
        *,
        chunks,
        index,
        take,
    ):
        voice_config = {}
        if os.path.exists(self.voice_config_path):
            with open(self.voice_config_path, "r", encoding="utf-8") as handle:
                voice_config = json.load(handle)
        current_chunk, _continuity = self._chunk_with_spoken_continuity(
            chunks,
            index,
            bind=True,
        )
        resolved = self._resolve_alias(
            current_chunk.get("speaker", ""),
            voice_config,
        )
        current_chunk, _pronunciation = self._chunk_with_pronunciation(
            chunks=chunks,
            index=index,
            chunk=current_chunk,
            voice_config=voice_config,
            resolved_speaker=resolved,
        )
        invalid_record_fingerprint = fingerprint_value(
            {"invalid_audio_take_compatibility_record": True}
        )
        generation = take.get("generation")
        if generation is None:
            generation = {}
        if not isinstance(generation, dict):
            return invalid_record_fingerprint, {}
        has_stored_fields = "chunk_audio_fields" in generation
        raw_stored_fields = generation.get("chunk_audio_fields")
        if has_stored_fields and (
            not isinstance(raw_stored_fields, dict) or not raw_stored_fields
        ):
            return invalid_record_fingerprint, {}
        stored_fields = (
            copy.deepcopy(raw_stored_fields) if has_stored_fields else {}
        )
        candidate = {
            **current_chunk,
            **stored_fields,
            "id": current_chunk.get("id", index),
            "speaker": current_chunk.get("speaker", ""),
            "text": current_chunk.get("text", ""),
            "instruct": current_chunk.get("instruct", ""),
        }
        provenance = generation.get("provenance")
        if provenance is None:
            provenance = {}
        take_voice = take.get("voice")
        artifact = take.get("artifact")
        authored = take.get("authored")
        review = take.get("review")
        processing = take.get("processing")
        if not all(
            isinstance(value, dict)
            for value in (provenance, take_voice, artifact, authored, review, processing)
        ):
            return invalid_record_fingerprint, {}
        approved_lock = provenance.get("approved_audio_lock")
        approved_origin = provenance.get("approved_audio_origin")
        if any(
            value is not None and not isinstance(value, dict)
            for value in (
                approved_lock,
                approved_origin,
                take_voice.get("approved_audio_lock"),
                take_voice.get("approved_audio_origin"),
            )
        ):
            return invalid_record_fingerprint, {}
        artifact_sha = str(artifact.get("sha256") or "")
        source_hashes = {
            artifact_sha,
            str(generation.get("source_audio_sha256") or ""),
            str(
                approved_lock.get("source_audio_sha256")
                if isinstance(approved_lock, dict)
                else ""
            ),
            str(
                approved_origin.get("source_audio_sha256")
                if isinstance(approved_origin, dict)
                else ""
            ),
        }
        current_effective_direction = str(
            current_chunk.get("effective_instruct")
            or current_chunk.get("instruct")
            or ""
        )
        current_fish_direction = str(
            current_chunk.get("effective_fish_instruct")
            or current_effective_direction
        )
        approved_lock_is_active = (
            isinstance(approved_lock, dict)
            and type(approved_lock.get("schema_version")) is int
            and approved_lock["schema_version"] == 1
            and active_approved_audio_lock(
                {**candidate, "approved_audio_lock": approved_lock}
            )
            == approved_lock
        )
        origin_text_fields = (
            "promotion_id",
            "manifest_path",
            "candidate_id",
            "direct_placement_tier",
            "source_audio_path",
            "source_audio_sha256",
            "installed_at_utc",
        )
        origin_lock_identity_fields = (
            "promotion_id",
            "candidate_id",
            "source_round_id",
            "direct_placement_tier",
            "source_audio_sha256",
            "installed_at_utc",
        )
        approved_origin_is_valid = (
            isinstance(approved_origin, dict)
            and type(approved_origin.get("schema_version")) is int
            and approved_origin["schema_version"] == 1
            and all(
                type(approved_origin.get(field)) is str
                and bool(approved_origin[field].strip())
                for field in origin_text_fields
            )
            and "source_round_id" in approved_origin
            and (
                approved_origin["source_round_id"] is None
                or (
                    type(approved_origin["source_round_id"]) is str
                    and bool(approved_origin["source_round_id"].strip())
                )
            )
            and type(approved_origin.get("reference_bank_eligible")) is bool
            and isinstance(approved_lock, dict)
            and all(
                approved_origin.get(field) == approved_lock.get(field)
                for field in origin_lock_identity_fields
            )
        )
        accepted_without_chunk_fields = (
            not has_stored_fields
            and provenance.get("operation")
            == "materialize_and_detach_approved_audio"
            and approved_lock_is_active
            and approved_origin_is_valid
            and processing.get("operation")
            == "materialize_and_detach_approved_audio"
            and review.get("state") == "approved"
            and take.get("legacy") is False
            and approved_lock == take_voice.get("approved_audio_lock")
            and approved_origin == take_voice.get("approved_audio_origin")
            and len(source_hashes) == 1
            and bool(artifact_sha)
            and approved_lock.get("binding_fingerprint")
            == generation.get("audio_fingerprint")
            and take_voice.get("binding_fingerprint")
            == generation.get("audio_fingerprint")
            and resolved == take_voice.get("resolved_speaker")
            and fingerprint_value(voice_config.get(resolved, {}))
            == take_voice.get("configuration_fingerprint")
            and voice_config.get(resolved, {}) == take_voice.get("configuration")
            and not current_chunk.get("pronunciation_request_fingerprint")
            and authored.get("effective_direction")
            == current_effective_direction
            and current_fish_direction == current_effective_direction
        )
        promotion_chunk_fields = {}
        if accepted_without_chunk_fields:
            promotion_chunk_fields = {
                "approved_audio_lock": copy.deepcopy(approved_lock),
                "approved_audio_origin": copy.deepcopy(approved_origin),
            }
            candidate.update(promotion_chunk_fields)
        return (
            self._audio_binding(
                candidate,
                voice_config,
                resolved_speaker=resolved,
            ),
            promotion_chunk_fields,
        )

    def _take_compatible_audio_fingerprint(
        self,
        *,
        chunks,
        index,
        take,
    ):
        fingerprint, _promotion_chunk_fields = (
            self._take_compatible_audio_promotion(
                chunks=chunks,
                index=index,
                take=take,
            )
        )
        return fingerprint

    def promote_audio_take(
        self,
        index,
        *,
        take_id,
        expected_registry_fingerprint,
        expected_record_fingerprint,
    ):
        chunks = self.load_chunks()
        index = int(index)
        if not 0 <= index < len(chunks):
            raise ValueError("The requested chunk does not exist.")
        registry = audio_take_registry_view(self.root_dir, chunks)
        take = registry["takes"].get(str(take_id))
        if not isinstance(take, dict):
            raise ValueError("The requested Take does not exist.")
        expected_audio_fingerprint, promotion_chunk_fields = (
            self._take_compatible_audio_promotion(
                chunks=chunks,
                index=index,
                take=take,
            )
        )
        return promote_registered_audio_take(
            self.root_dir,
            chunks=chunks,
            chunks_path=self.chunks_path,
            index=index,
            take_id=str(take_id),
            expected_registry_fingerprint=str(
                expected_registry_fingerprint
            ),
            expected_record_fingerprint=str(
                expected_record_fingerprint
            ),
            expected_audio_fingerprint=expected_audio_fingerprint,
            promotion_chunk_fields=promotion_chunk_fields,
        )

    def set_audio_take_kept(
        self,
        index,
        *,
        take_id,
        kept,
        expected_registry_fingerprint,
        expected_record_fingerprint,
    ):
        chunks = self.load_chunks()
        index = int(index)
        if not 0 <= index < len(chunks):
            raise ValueError("The requested chunk does not exist.")
        key = audio_take_chunk_key(chunks[index], index)
        take, registry = set_take_kept(
            self.root_dir,
            chunks=chunks,
            chunk_key_value=key,
            take_id=str(take_id),
            kept=bool(kept),
            expected_registry_fingerprint=str(
                expected_registry_fingerprint
            ),
            expected_record_fingerprint=str(
                expected_record_fingerprint
            ),
        )
        return {
            "status": "updated",
            "take": take,
            "registry_fingerprint": registry[
                "registry_fingerprint"
            ],
        }

    def audio_take_delete_impact(self, index, *, take_id):
        chunks = self.load_chunks()
        index = int(index)
        if not 0 <= index < len(chunks):
            raise ValueError("The requested chunk does not exist.")
        return audio_take_delete_impact(
            self.root_dir,
            chunks=chunks,
            chunk_key_value=audio_take_chunk_key(
                chunks[index],
                index,
            ),
            take_id=str(take_id),
        )

    def delete_audio_take(
        self,
        index,
        *,
        take_id,
        expected_impact_fingerprint,
    ):
        impact = self.audio_take_delete_impact(
            index,
            take_id=take_id,
        )
        return apply_audio_take_delete(
            self.root_dir,
            chunks=self.load_chunks(),
            impact=impact,
            expected_impact_fingerprint=str(
                expected_impact_fingerprint
            ),
        )

    def audio_take_cleanup_impact(
        self,
        *,
        older_than_days,
        reclaim_at_least_bytes=0,
    ):
        return audio_take_cleanup_impact(
            self.root_dir,
            chunks=self.load_chunks(),
            older_than_days=int(older_than_days),
            reclaim_at_least_bytes=int(reclaim_at_least_bytes),
        )

    def cleanup_audio_takes(
        self,
        *,
        older_than_days,
        reclaim_at_least_bytes,
        expected_impact_fingerprint,
    ):
        impact = self.audio_take_cleanup_impact(
            older_than_days=older_than_days,
            reclaim_at_least_bytes=reclaim_at_least_bytes,
        )
        return apply_audio_take_cleanup(
            self.root_dir,
            chunks=self.load_chunks(),
            impact=impact,
            expected_impact_fingerprint=str(
                expected_impact_fingerprint
            ),
        )

    def undo_audio_take_operation(
        self,
        *,
        operation_id,
        expected_registry_fingerprint,
    ):
        return undo_audio_take_operation(
            self.root_dir,
            operation_id=str(operation_id),
            expected_registry_fingerprint=str(
                expected_registry_fingerprint
            ),
        )

    def register_audio_rendition(
        self,
        index,
        *,
        source_take_id,
        source_audio_path,
        expected_source_sha256,
        expected_registry_fingerprint,
        expected_source_record_fingerprint,
        processing,
        review=None,
        created_at_utc=None,
    ):
        chunks = self.load_chunks()
        index = int(index)
        if not 0 <= index < len(chunks):
            raise ValueError("The requested chunk does not exist.")
        registry = audio_take_registry_view(
            self.root_dir,
            chunks,
        )
        source = registry["takes"].get(str(source_take_id))
        if not isinstance(source, dict):
            raise ValueError("The source Take does not exist.")
        expected_audio_fingerprint = self._take_compatible_audio_fingerprint(
            chunks=chunks,
            index=index,
            take=source,
        )
        return register_audio_take_rendition(
            self.root_dir,
            chunks=chunks,
            chunks_path=self.chunks_path,
            index=index,
            source_take_id=str(source_take_id),
            source_audio_path=source_audio_path,
            expected_source_sha256=str(expected_source_sha256),
            expected_registry_fingerprint=str(
                expected_registry_fingerprint
            ),
            expected_source_record_fingerprint=str(
                expected_source_record_fingerprint
            ),
            expected_audio_fingerprint=expected_audio_fingerprint,
            processing=copy.deepcopy(dict(processing or {})),
            review=(
                copy.deepcopy(dict(review))
                if isinstance(review, dict)
                else None
            ),
            created_at_utc=created_at_utc,
        )

    def generate_chunk_audio(
        self,
        index,
        generation_seed=None,
        generation_context=None,
    ):
        chunks = self.load_chunks()
        if not (0 <= index < len(chunks)):
            return False, "Invalid chunk index"

        chunk = chunks[index]
        try:
            require_regeneration_unlocked(chunk)
        except Exception as exc:
            return False, str(exc)
        generation_chunk, spoken_continuity = self._chunk_with_spoken_continuity(
            chunks,
            index,
            bind=True,
        )

        # Validate and resolve the production voice before invalidating current
        # audio or initializing a model. Invalid legacy aliases remain file-pure.
        temp_path = os.path.join(self.root_dir, f"temp_chunk_{index}.wav")
        canonical_speaker = None
        pronunciation_resolution = None
        try:
            voice_config = {}
            if os.path.exists(self.voice_config_path):
                with open(self.voice_config_path, "r", encoding="utf-8") as f:
                    voice_config = json.load(f)
            speaker = chunk["speaker"]
            canonical_speaker = self._resolve_alias(speaker, voice_config)
            engine = self.get_engine()
            if not engine:
                failure = self._mark_audio_generation_failed(
                    index,
                    "TTS engine not initialized",
                    start=True,
                )
                return False, failure.message
            voice_data = voice_config.get(canonical_speaker, {})
            generation_provenance = self._engine_generation_provenance(
                engine,
                voice_data,
            )
            seed_resolution = self._generation_seed_resolution(
                chunk=generation_chunk,
                voice_config=voice_config,
                resolved_speaker=canonical_speaker,
                explicit_seed=generation_seed,
                seed_supported=self._engine_supports_generation_seed(
                    engine,
                    voice_data,
                    batch=False,
                ),
            )
            effective_voice_config = apply_generation_seed_to_voice_config(
                voice_config,
                resolved_speaker=canonical_speaker,
                resolution=seed_resolution,
            )
            prompt_resolution = resolve_experimental_prompt_override(
                voice_data=voice_config.get(canonical_speaker, {}),
                instruction=generation_chunk.get("effective_instruct", ""),
                project_root=self.root_dir,
            )
            responsive_resolution = resolve_recurring_voice_route(
                voice_data=voice_config.get(canonical_speaker, {}),
                instruction=generation_chunk.get("effective_instruct", ""),
                project_root=self.root_dir,
                verify_audio=True,
            )
            pronunciation_resolution = self._resolve_chunk_pronunciation(
                index=index,
                chunk=generation_chunk,
                speaker=speaker,
                resolved_speaker=canonical_speaker,
                voice_data=voice_data,
            )
            generation_chunk.update(
                pronunciation_chunk_fields(pronunciation_resolution)
            )
            if (
                pronunciation_resolution["receipt"]["applied_count"]
                and generation_chunk.get("fish_render_plan") is not None
            ):
                generation_chunk["fish_render_plan"] = None
                generation_chunk[
                    "pronunciation_fish_inline_plan_bypassed_reason"
                ] = "pronunciation_changed_plan_text"
        except VoiceAliasError as e:
            # Alias validation is deliberately file-pure for imported legacy
            # projects: return the safe authored validation message without
            # rewriting the chunk or deleting a still-auditable file.
            return False, str(e)
        except Exception as e:
            failure = self._mark_audio_generation_failed(index, e, start=True)
            return False, failure.message

        previous_audio_path = chunk.get("audio_path") or chunk.get("stale_audio_path")
        if isinstance(generation_context, dict):
            try:
                record_generation_chunk_started(
                    generation_context["project_root"],
                    generation_context["request_id"],
                    generation_context["owner_token"],
                    generation_context["chunk_key"],
                )
            except AudioGenerationLifecycleError as exc:
                return False, str(exc)
        self._mark_audio_generation_started(
            index,
            chunk,
            seed_resolution=seed_resolution,
            prompt_resolution=prompt_resolution,
            responsive_resolution=responsive_resolution,
            pronunciation_resolution=pronunciation_resolution,
        )

        try:
            if canonical_speaker != speaker:
                print(f"Resolving alias: '{speaker}' -> '{canonical_speaker}'")
            text = pronunciation_resolution["synthesis_text"]
            instruct = generation_chunk.get("effective_instruct", "")

            print(
                f"Generating chunk {index}: speaker={speaker}, "
                f"instruct='{instruct}', text='{text[:50]}...'"
            )

            # The TTS engine writes a non-canonical source file. Installation
            # validates and atomically replaces the canonical production file.
            generation_kwargs = {}
            fish_render_plan = generation_chunk.get("fish_render_plan")
            fish_instruction = generation_chunk.get("effective_fish_instruct", instruct)
            try:
                parameters = inspect.signature(engine.generate_voice).parameters
            except (TypeError, ValueError):
                parameters = {}
            if fish_render_plan is not None and "fish_render_plan" in parameters:
                generation_kwargs["fish_render_plan"] = fish_render_plan
            if "fish_instruction" in parameters:
                generation_kwargs["fish_instruction"] = fish_instruction
            if (
                isinstance(generation_context, dict)
                and "generation_context" in parameters
            ):
                generation_kwargs["generation_context"] = generation_context
            success = engine.generate_voice(
                text,
                instruct,
                canonical_speaker,
                effective_voice_config,
                temp_path,
                **generation_kwargs,
            )
            responsive_receipt = None
            consume_receipt = getattr(
                engine,
                "consume_responsive_generation_receipt",
                None,
            )
            if callable(consume_receipt):
                responsive_receipt = consume_receipt()

            if not success:
                error = "Generation failed"
                failure = self._mark_audio_generation_failed(index, error)
                if isinstance(generation_context, dict):
                    try:
                        record_generation_chunk_failed(
                            generation_context["project_root"],
                            generation_context["request_id"],
                            generation_context["owner_token"],
                            generation_context["chunk_key"],
                            error=failure.message,
                        )
                    except AudioGenerationLifecycleError:
                        pass
                return False, failure.message

            def publish():
                generation_metadata = {}
                metadata_reader = getattr(engine, "pop_generation_metadata", None)
                if callable(metadata_reader):
                    generation_metadata = metadata_reader(temp_path)
                actual_provenance = generation_metadata.pop(
                    "generation_provenance",
                    None,
                ) or generation_provenance
                binding_chunk = {**generation_chunk, **generation_metadata}
                artifact_fields = copy.deepcopy(generation_metadata)
                artifact_fields["audio_fingerprint"] = self._audio_binding(
                    binding_chunk, voice_config, canonical_speaker, seed_resolution=seed_resolution
                )
                artifact_fields.update(recurring_voice_chunk_fields(responsive_resolution))
                if isinstance(responsive_receipt, dict):
                    artifact_fields.update(responsive_receipt)
                artifact_fields.update(
                    {
                        **experimental_prompt_chunk_fields(prompt_resolution),
                        "spoken_continuity_applied": spoken_continuity,
                        "spoken_continuity_effective_instruct": instruct,
                        "backend_render_plan_applied": (
                            backend_render_plan_application_record(generation_chunk)
                        ),
                        "backend_render_plan_effective_qwen_instruction": instruct,
                        "backend_render_plan_effective_fish_instruction": fish_instruction,
                        **pronunciation_chunk_fields(pronunciation_resolution),
                        "pronunciation_fish_inline_plan_bypassed_reason": (
                            generation_chunk.get(
                                "pronunciation_fish_inline_plan_bypassed_reason"
                            )
                        ),
                        "generation_provenance": actual_provenance or None,
                        "generated_at_utc": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(),
                        ),
                    }
                )
                artifact = self._install_chunk_audio(
                    index=index,
                    chunk=generation_chunk,
                    resolved_speaker=canonical_speaker,
                    voice_config=voice_config,
                    source_path=temp_path,
                    previous_audio_path=previous_audio_path,
                    seed_resolution=seed_resolution,
                    expected_text=text,
                    artifact_fields=artifact_fields,
                    generation_context=generation_context,
                )
                self._register_generated_take(
                    index=index,
                    chunk={**generation_chunk, **artifact},
                    resolved_speaker=canonical_speaker,
                    voice_config=voice_config,
                    artifact=artifact,
                    generation_context=generation_context,
                )
                artifact.pop("take_id", None)
                artifact.pop("take_chunk_key", None)
                if not isinstance(generation_context, dict):
                    self._update_chunk_fields(
                        index,
                        status="done",
                        error=None,
                        error_code=None,
                        **artifact,
                    )
                return artifact

            if isinstance(generation_context, dict):
                current_request_fingerprint, current_chunk_dependency = (
                    self._current_audio_generation_identity(generation_context)
                )
                artifact, _request = publish_generation_chunk(
                    generation_context["project_root"],
                    generation_context["request_id"],
                    generation_context["owner_token"],
                    generation_context["chunk_key"],
                    current_request_fingerprint=current_request_fingerprint,
                    current_chunk_dependency_fingerprint=current_chunk_dependency,
                    publisher=publish,
                )
            else:
                artifact = publish()

            return True, artifact["audio_path"]

        except Exception as e:
            if isinstance(e, AudioGenerationLifecycleError) and e.code in {
                "audio_request_cancelled",
                "audio_request_owner_stale",
                "audio_request_dependency_changed",
                "audio_request_not_running",
            }:
                try:
                    self._mark_audio_generation_cancelled(index)
                except Exception:
                    pass
                return False, str(e)
            try:
                self._mark_audio_generation_failed(index, e)
            except Exception as update_err:
                print(
                    f"Warning: Failed to update chunk {index} status to error: "
                    f"{update_err}"
                )
            if isinstance(generation_context, dict):
                try:
                    record_generation_chunk_failed(
                        generation_context["project_root"],
                        generation_context["request_id"],
                        generation_context["owner_token"],
                        generation_context["chunk_key"],
                        error=str(e),
                    )
                except AudioGenerationLifecycleError:
                    pass
            return False, normalize_audio_failure(e).message
        finally:
            self._remove_generated_temp(temp_path)

    def _load_pause_defaults(self):
        """Return (pause_between_speakers_ms, pause_same_speaker_ms) from config."""
        tts_cfg = self._load_tts_config()
        return (
            tts_cfg.get("pause_between_speakers_ms", DEFAULT_PAUSE_MS),
            tts_cfg.get("pause_same_speaker_ms", SAME_SPEAKER_PAUSE_MS),
        )

    def _load_chunks_with_audio(self, progress_callback=None, cancel_check=None):
        """Load only audio bound to the current chunk and voice configuration."""
        raw_chunks = self.load_chunks()
        voice_config = {}
        if os.path.exists(self.voice_config_path):
            with open(self.voice_config_path, "r", encoding="utf-8") as f:
                voice_config = json.load(f)
        chunks = []
        for index in range(len(raw_chunks)):
            chunk, _ = self._chunk_with_spoken_continuity(raw_chunks, index)
            chunk, _ = self._chunk_with_pronunciation(
                chunks=raw_chunks,
                index=index,
                chunk=chunk,
                voice_config=voice_config,
            )
            chunks.append(chunk)

        def expected_fingerprint(chunk):
            return self._audio_binding(chunk, voice_config)

        return require_current_project_audio(
            root_dir=self.root_dir,
            chunks=chunks,
            expected_fingerprint=expected_fingerprint,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

    def _confined_export_target(self, output_path, default_name):
        root = Path(self.root_dir).expanduser().resolve()
        target = (
            Path(output_path).expanduser().resolve()
            if output_path is not None
            else root / default_name
        )
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise AudioArtifactError(
                "unsafe_export_path",
                f"Export target escaped the project root: {target}.",
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def merge_audio(self, output_path=None):
        try:
            chunks_with_audio = self._load_chunks_with_audio()
        except AudioArtifactError as exc:
            return False, str(exc)

        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)

        # Build final audio from timeline
        audio_segments = [seg for _, seg, _ in timeline]
        speakers = [chunk["speaker"] for chunk, _, _ in timeline]
        pause_overrides = [
            effective_pause_after_ms(chunk) for chunk, _, _ in timeline
        ]

        final_audio = combine_audio_with_pauses(
            audio_segments, speakers, pause_ms, same_speaker_pause_ms, pause_overrides
        )
        output_filename = "cloned_audiobook.mp3"
        try:
            target = self._confined_export_target(output_path, output_filename)
            relative_target = target.relative_to(Path(self.root_dir).resolve()).as_posix()
            join_id = f"join-{fingerprint_value({'target': relative_target, 'chunks': [chunk.get('audio_sha256') for chunk, _segment, _offset in timeline]})[:24]}"
            with audio_mutation_guard(
                self.root_dir,
                transition="join",
                operation_id=join_id,
                watched_paths=[relative_target],
            ) as transition:
                atomic_export_audio_segment(
                    segment=final_audio,
                    target_path=target,
                    audio_format="mp3",
                )
                transition["required_artifacts"] = {
                    relative_target: sha256_file(target)
                }
        except AudioArtifactError as exc:
            return False, str(exc)

        return True, (
            output_filename if output_path is None else str(target)
        )

    def export_audacity(self, output_path=None):
        """Export project as an Audacity-compatible zip with per-speaker WAV tracks,
        a LOF file for auto-import, and a labels file for chunk annotations."""
        try:
            chunks_with_audio = self._load_chunks_with_audio()
        except AudioArtifactError as exc:
            return False, str(exc)

        # Phase 1 — Compute timeline
        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)

        if not timeline:
            return False, "No audio segments found"

        # Total duration = last chunk's start + its length
        last_chunk, last_seg, last_start = timeline[-1]
        total_duration_ms = last_start + len(last_seg)

        # Phase 2 — Build per-speaker WAV tracks
        speakers_ordered = []
        seen = set()
        for chunk, segment, start_ms in timeline:
            if chunk["speaker"] not in seen:
                speakers_ordered.append(chunk["speaker"])
                seen.add(chunk["speaker"])

        speaker_tracks = {}
        for speaker in speakers_ordered:
            track_cursor = 0
            track = AudioSegment.empty()

            for chunk, segment, start_ms in timeline:
                if chunk["speaker"] != speaker:
                    continue
                # Insert silence gap from current track position to this chunk's start
                gap = start_ms - track_cursor
                if gap > 0:
                    track += AudioSegment.silent(duration=gap)
                track += segment
                track_cursor = start_ms + len(segment)

            # Pad to total duration so all tracks are equal length
            remaining = total_duration_ms - track_cursor
            if remaining > 0:
                track += AudioSegment.silent(duration=remaining)

            speaker_tracks[speaker] = track

        # Phase 3 — Build LOF and labels content
        lof_lines = []
        for speaker in speakers_ordered:
            safe_name = sanitize_filename(speaker)
            lof_lines.append(f'file "{safe_name}.wav"')
        lof_content = "\n".join(lof_lines) + "\n"

        label_lines = []
        for chunk, segment, start_ms in timeline:
            start_sec = start_ms / 1000.0
            end_sec = (start_ms + len(segment)) / 1000.0
            text_preview = chunk.get("text", "")[:80]
            label = f"[{chunk['speaker']}] {text_preview}"
            label_lines.append(f"{start_sec:.6f}\t{end_sec:.6f}\t{label}")
        labels_content = "\n".join(label_lines) + "\n"

        # Phase 4 — Write and verify a temporary archive, then replace the
        # canonical export atomically so a failed export cannot corrupt it.
        try:
            zip_target = self._confined_export_target(
                output_path,
                "audacity_export.zip",
            )
        except AudioArtifactError as exc:
            return False, str(exc)
        descriptor, temporary_zip = tempfile.mkstemp(
            prefix=".audacity_export.",
            suffix=".zip",
            dir=zip_target.parent,
        )
        os.close(descriptor)
        try:
            with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("project.lof", lof_content)
                zf.writestr("labels.txt", labels_content)

                for speaker in speakers_ordered:
                    safe_name = sanitize_filename(speaker)
                    with io.BytesIO() as wav_buffer:
                        speaker_tracks[speaker].export(wav_buffer, format="wav")
                        zf.writestr(f"{safe_name}.wav", wav_buffer.getvalue())

            with zipfile.ZipFile(temporary_zip, "r") as verification:
                corrupt_member = verification.testzip()
                if corrupt_member is not None:
                    return False, f"Audacity export contains a corrupt file: {corrupt_member}"
                required = {"project.lof", "labels.txt"}
                if not required.issubset(verification.namelist()):
                    return False, "Audacity export is missing required project files"
            os.replace(temporary_zip, zip_target)
        except Exception as exc:
            return False, f"Audacity export failed: {exc}"
        finally:
            try:
                os.remove(temporary_zip)
            except FileNotFoundError:
                pass

        return True, str(zip_target)

    def merge_m4b(
        self,
        per_chunk_chapters=False,
        metadata=None,
        output_path=None,
        cancel_check=None,
        progress_callback=None,
    ):
        """Merge audio chunks into an M4B audiobook with chapter markers.

        Args:
            per_chunk_chapters: If True, each chunk is a chapter. If False,
                detect chapter headings and group chunks into sections.
            metadata: Optional dict with keys: title, author, narrator, year,
                description, cover_path (absolute path to cover image).

        Returns:
            tuple: (success: bool, message: str)
        """
        metadata = metadata or {}

        def report(
            phase,
            label,
            *,
            completed_count=0,
            total_count=0,
            overall_percent=None,
            message=None,
        ):
            if progress_callback:
                progress_callback(
                    {
                        "phase": phase,
                        "phase_label": label,
                        "completed_count": int(completed_count or 0),
                        "total_count": int(total_count or 0),
                        "overall_percent": overall_percent,
                        "progress_message": message or label,
                    }
                )

        def loading_progress(completed, total, index, chunk):
            report(
                "loading_audio",
                "Loading production audio",
                completed_count=completed,
                total_count=total,
                overall_percent=5 + (45 * completed / max(1, total)),
                message=(
                    f"Loaded {completed:,} of {total:,} chunks · "
                    f"{chunk.get('speaker') or 'Unknown speaker'}"
                ),
            )

        report(
            "loading_audio",
            "Loading production audio",
            overall_percent=5,
            message="Opening and verifying current chunk audio.",
        )
        try:
            loader = self._load_chunks_with_audio
            try:
                loader_parameters = inspect.signature(loader).parameters
            except (TypeError, ValueError):
                loader_parameters = {}
            loader_kwargs = {}
            if "progress_callback" in loader_parameters:
                loader_kwargs["progress_callback"] = loading_progress
            if "cancel_check" in loader_parameters:
                loader_kwargs["cancel_check"] = cancel_check
            chunks_with_audio = loader(**loader_kwargs)
        except AudioArtifactError as exc:
            return False, str(exc)

        if cancel_check and cancel_check():
            return False, "M4B export cancelled"

        # Phase 1 — Compute timeline
        report(
            "planning_timeline",
            "Planning chapters and timing",
            completed_count=0,
            total_count=1,
            overall_percent=51,
            message="Calculating pauses, duration, and chapter boundaries.",
        )
        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)

        if not timeline:
            return False, "No audio segments found"
        total_duration_ms = max(1, timeline[-1][2] + len(timeline[-1][1]))

        # Phase 2 — Build chapters
        chapters = self._build_m4b_chapters(timeline, per_chunk_chapters)
        print(f"  M4B: {len(chapters)} chapters")
        report(
            "planning_timeline",
            "Planning chapters and timing",
            completed_count=1,
            total_count=1,
            overall_percent=53,
            message=f"Prepared {len(chapters):,} chapter markers.",
        )

        # Phase 3 — Combine audio and export to temp WAV
        audio_segments = [seg for _, seg, _ in timeline]
        speakers = [chunk["speaker"] for chunk, _, _ in timeline]
        pause_overrides = [
            effective_pause_after_ms(chunk) for chunk, _, _ in timeline
        ]

        def assembly_progress(completed, total):
            report(
                "assembling_audio",
                "Assembling audiobook timeline",
                completed_count=completed,
                total_count=total,
                overall_percent=53 + (12 * completed / max(1, total)),
                message=f"Assembled {completed:,} of {total:,} chunks.",
            )

        try:
            final_audio = combine_audio_with_pauses(
                audio_segments,
                speakers,
                pause_ms,
                same_speaker_pause_ms,
                pause_overrides,
                progress_callback=assembly_progress,
                cancel_check=cancel_check,
            )
        except InterruptedError:
            return False, "M4B export cancelled"

        try:
            output_target = self._confined_export_target(
                output_path,
                "audiobook.m4b",
            )
        except AudioArtifactError as exc:
            return False, str(exc)
        temp_wav_handle, temp_wav = tempfile.mkstemp(
            prefix=".m4b-combined.",
            suffix=".wav",
            dir=output_target.parent,
        )
        os.close(temp_wav_handle)
        meta_handle, meta_path = tempfile.mkstemp(
            prefix=".m4b-meta.",
            suffix=".txt",
            dir=output_target.parent,
        )
        os.close(meta_handle)
        descriptor, temporary_output = tempfile.mkstemp(
            prefix=".audiobook.",
            suffix=".m4b",
            dir=output_target.parent,
        )
        os.close(descriptor)
        os.remove(temporary_output)

        try:
            report(
                "writing_intermediate",
                "Writing intermediate audio",
                overall_percent=66,
                message="Writing the assembled timeline for final encoding.",
            )
            with open(temp_wav, "wb") as wav_output:
                final_audio.export(wav_output, format="wav")
            report(
                "writing_intermediate",
                "Writing intermediate audio",
                completed_count=1,
                total_count=1,
                overall_percent=69,
                message="Intermediate audio is ready.",
            )

            # Phase 4 — Write FFmpeg metadata file with book metadata
            meta_lines = [";FFMETADATA1"]
            meta_lines.append(f"title={self._escape_ffmeta(metadata.get('title') or 'Audiobook')}")
            meta_lines.append(f"artist={self._escape_ffmeta(metadata.get('author') or '')}")
            meta_lines.append(f"album_artist={self._escape_ffmeta(metadata.get('narrator') or '')}")
            meta_lines.append(f"date={self._escape_ffmeta(metadata.get('year') or '')}")
            meta_lines.append(f"comment={self._escape_ffmeta(metadata.get('description') or '')}")
            meta_lines.append("genre=Audiobook")
            meta_lines.append("")
            for title, start_ms, end_ms in chapters:
                safe_title = self._escape_ffmeta(title)
                meta_lines.append("[CHAPTER]")
                meta_lines.append("TIMEBASE=1/1000")
                meta_lines.append(f"START={start_ms}")
                meta_lines.append(f"END={end_ms}")
                meta_lines.append(f"title={safe_title}")
                meta_lines.append("")

            with open(meta_path, "w", encoding="utf-8") as f:
                f.write("\n".join(meta_lines))

            # Phase 5 — FFmpeg: WAV + chapters → M4B (AAC)
            cover_path = metadata.get("cover_path") or ""
            has_cover = cover_path and os.path.exists(cover_path)

            report(
                "encoding_m4b",
                "Encoding M4B audiobook",
                completed_count=0,
                total_count=total_duration_ms,
                overall_percent=70,
                message="Encoding AAC audio and embedding chapters and cover art.",
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-progress",
                "pipe:1",
                "-i",
                temp_wav,
            ]
            if has_cover:
                cmd += ["-i", cover_path]
            cmd += ["-i", meta_path, "-map_metadata", "2" if has_cover else "1"]
            # Map audio stream
            cmd += ["-map", "0:a"]
            if has_cover:
                # Normalize browser-supported covers to an M4B-compatible picture.
                cmd += [
                    "-map", "1:v:0",
                    "-c:v", "mjpeg",
                    "-disposition:v:0", "attached_pic",
                ]
            cmd += [
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                temporary_output,
            ]
            with tempfile.TemporaryFile(mode="w+b") as ffmpeg_error:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=ffmpeg_error,
                )
                progress_queue = queue.Queue()

                def read_ffmpeg_progress():
                    try:
                        for raw_line in iter(process.stdout.readline, b""):
                            progress_queue.put(
                                raw_line.decode("utf-8", errors="replace").strip()
                            )
                    finally:
                        progress_queue.put(None)

                progress_reader = threading.Thread(
                    target=read_ffmpeg_progress,
                    daemon=True,
                )
                progress_reader.start()
                stream_finished = False
                encoded_ms = 0
                while process.poll() is None or not stream_finished:
                    if cancel_check and cancel_check():
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                        progress_reader.join(timeout=1)
                        if process.stdout is not None:
                            process.stdout.close()
                        return False, "M4B export cancelled"
                    try:
                        line = progress_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if line is None:
                        stream_finished = True
                        continue
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key in {"out_time_us", "out_time_ms"}:
                        try:
                            encoded_ms = max(encoded_ms, int(value) // 1000)
                        except ValueError:
                            continue
                        report(
                            "encoding_m4b",
                            "Encoding M4B audiobook",
                            completed_count=min(encoded_ms, total_duration_ms),
                            total_count=total_duration_ms,
                            overall_percent=(
                                70
                                + 24
                                * min(1.0, encoded_ms / max(1, total_duration_ms))
                            ),
                            message=(
                                f"Encoded {min(encoded_ms, total_duration_ms) / 1000 / 60:.1f} "
                                f"of {total_duration_ms / 1000 / 60:.1f} minutes."
                            ),
                        )
                    elif key == "progress" and value == "end":
                        encoded_ms = total_duration_ms
                progress_reader.join(timeout=1)
                if process.stdout is not None:
                    process.stdout.close()
                return_code = process.returncode
                ffmpeg_error.seek(0)
                stderr = ffmpeg_error.read().decode("utf-8", errors="replace")
            if return_code != 0:
                print(f"FFmpeg stderr: {stderr[-500:]}")
                return False, f"FFmpeg failed (exit {return_code})"
            if cancel_check and cancel_check():
                return False, "M4B export cancelled"
            if not os.path.exists(temporary_output) or os.path.getsize(temporary_output) <= 0:
                return False, "FFmpeg produced no M4B output"
            report(
                "validating_output",
                "Validating finished audiobook",
                completed_count=0,
                total_count=1,
                overall_percent=96,
                message="Checking the finished file, duration, and container metadata.",
            )
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    temporary_output,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            try:
                duration_seconds = float(probe.stdout.strip())
            except (TypeError, ValueError):
                duration_seconds = 0.0
            if probe.returncode != 0 or duration_seconds <= 0:
                detail = probe.stderr.strip() or "no positive duration was reported"
                return False, f"Generated M4B failed metadata validation: {detail}"
            if cancel_check and cancel_check():
                return False, "M4B export cancelled"
            report(
                "validating_output",
                "Validating finished audiobook",
                completed_count=1,
                total_count=1,
                overall_percent=98,
                message="The M4B passed container and duration validation.",
            )
            os.replace(temporary_output, output_target)
            report(
                "finalizing_export",
                "Finalizing Export",
                completed_count=1,
                total_count=1,
                overall_percent=99,
                message="Preparing the verified M4B for commit.",
            )

        except Exception as exc:
            return False, f"M4B export failed: {exc}"
        finally:
            for tmp in [temp_wav, meta_path, temporary_output]:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

        return True, (
            "audiobook.m4b" if output_path is None else str(output_target)
        )

    @staticmethod
    def _escape_ffmeta(text):
        """Escape special characters for FFmpeg metadata format."""
        text = text.replace("\\", "\\\\")
        text = text.replace("=", "\\=")
        text = text.replace(";", "\\;")
        text = text.replace("#", "\\#")
        text = text.replace("\n", " ")
        return text

    def _build_m4b_chapters(self, timeline, per_chunk_chapters):
        """Build M4B markers through the shared chapter-assembly authority."""
        rows = []
        for index, (chunk, segment, start_ms) in enumerate(timeline):
            end_ms = start_ms + len(segment)
            if index + 1 < len(timeline):
                next_start = int(timeline[index + 1][2])
                pause_after = max(0, next_start - end_ms)
            else:
                pause_after = 0
            rows.append(
                {
                    "chunk_id": f"chunk:{chunk.get('id', index)}",
                    "speaker": chunk.get("speaker", "UNKNOWN"),
                    "text": chunk.get("text", ""),
                    "duration_ms": len(segment),
                    "pause_after_ms": pause_after,
                }
            )
        markers = build_chapter_markers(
            rows,
            config={},
            mode="per_chunk" if per_chunk_chapters else "smart",
        )
        return [
            (item["name"], item["start_ms"], item["end_ms"])
            for item in markers
        ]

    def generate_chunks_parallel(
        self,
        indices,
        max_workers=2,
        progress_callback=None,
        cancel_check=None,
        generation_seed=None,
        generation_contexts=None,
    ):
        """Generate multiple chunks in parallel using ThreadPoolExecutor.

        Uses individual TTS API calls with per-speaker voice settings.

        Args:
            indices: List of chunk indices to generate
            max_workers: Number of concurrent TTS workers
            progress_callback: Optional callback(completed, failed, total) for progress updates
            cancel_check: Optional callable returning True when cancellation is requested

        Returns:
            dict with 'completed', 'failed', and 'cancelled' keys
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {"completed": [], "failed": [], "cancelled": 0}

        # Filter out empty-text chunks
        chunks = self.load_chunks()
        if chunks:
            indices = [i for i in indices if 0 <= i < len(chunks) and chunks[i].get("text", "").strip()]
            locked = []
            unlocked = []
            for index in indices:
                try:
                    require_regeneration_unlocked(chunks[index])
                except Exception as exc:
                    locked.append((index, str(exc)))
                else:
                    unlocked.append(index)
            results["failed"].extend(locked)
            indices = unlocked

        total = len(indices)

        if total == 0:
            return results

        print(f"Starting parallel generation of {total} chunks with {max_workers} workers...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.generate_chunk_audio,
                    idx,
                    generation_seed,
                    (
                        generation_contexts.get(idx)
                        if isinstance(generation_contexts, dict)
                        else None
                    ),
                ): idx
                for idx in indices
            }

            cancelled = False
            for future in as_completed(futures):
                if cancel_check and cancel_check():
                    cancelled = True
                    print("[CANCEL] Cancellation requested — stopping parallel generation")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                idx = futures[future]
                try:
                    success, msg = future.result()
                    if success:
                        results["completed"].append(idx)
                        print(f"Chunk {idx} completed: {msg}")
                    else:
                        results["failed"].append((idx, msg))
                        print(f"Chunk {idx} failed: {msg}")
                except Exception as e:
                    failure = self._mark_audio_generation_failed(idx, e, start=True)
                    results["failed"].append((idx, failure.message))
                    print(f"Chunk {idx} error: {failure.message}")

                if progress_callback:
                    progress_callback(len(results["completed"]), len(results["failed"]), total)

            # Reset remaining "generating" chunks to "pending"
            if cancelled:
                done_indices = set(results["completed"]) | {idx for idx, _ in results["failed"]}
                chunks = self.load_chunks()
                if chunks:
                    for idx in indices:
                        if idx not in done_indices and 0 <= idx < len(chunks) and chunks[idx].get("status") == "generating":
                            chunks[idx]["status"] = "pending"
                            results["cancelled"] += 1
                    self.save_chunks(chunks)

        print(f"Parallel generation complete: {len(results['completed'])} succeeded, "
              f"{len(results['failed'])} failed, {results['cancelled']} cancelled")
        return results

    def _group_indices_by_voice_type(self, indices, chunks, voice_config):
        """Reorder indices so chunks with the same voice type are contiguous.

        Grouping key matches how tts.py routes batches:
        - "custom" for custom voices (all batched together)
        - "clone:{speaker}" for clone voices (batched per speaker)
        - "lora:{adapter}" for LoRA voices (batched per adapter)
        - "design" for voice design (always sequential)

        Within each group, original order is preserved.
        """
        from collections import OrderedDict
        groups = OrderedDict()

        for idx in indices:
            if not (0 <= idx < len(chunks)):
                groups.setdefault("custom", []).append(idx)
                continue
            speaker = chunks[idx].get("speaker", "")
            # Resolve alias before grouping so alias groups collate with their canonical speaker
            canonical = self._resolve_alias(speaker, voice_config)
            voice_data = voice_config.get(canonical, {})
            voice_type = voice_data.get("type", "custom")

            if voice_type == "clone":
                key = f"clone:{canonical}"
            elif voice_type in ("lora", "builtin_lora"):
                adapter_id = voice_data.get("adapter_id", "")
                key = f"lora:{adapter_id}"
            elif voice_type == "design":
                key = "design"
            else:
                key = "custom"

            groups.setdefault(key, []).append(idx)

        reordered = []
        for key, group_indices in groups.items():
            print(f"  Voice group '{key}': {len(group_indices)} chunks")
            reordered.extend(group_indices)

        return reordered

    def generate_chunks_batch(self, indices, batch_seed=-1, batch_size=4, progress_callback=None,
                               batch_group_by_type=False, cancel_check=None,
                               generation_contexts=None):
        """Generate multiple chunks using batch TTS API with a single seed.

        Args:
            indices: List of chunk indices to generate
            batch_seed: Single seed for all generations (-1 for random)
            batch_size: Number of chunks per batch request
            progress_callback: Optional callback(completed, failed, total) for progress updates
            batch_group_by_type: Group indices by voice type before batching for
                GPU efficiency. When False, indices are batched in sequential order.
            cancel_check: Optional callable returning True when cancellation is requested

        Returns:
            dict with 'completed', 'failed', and 'cancelled' keys
        """
        results = {"completed": [], "failed": [], "cancelled": 0}

        # Load chunks and voice config
        chunks = self.load_chunks()

        # Filter out empty-text chunks
        if chunks:
            indices = [i for i in indices if 0 <= i < len(chunks) and chunks[i].get("text", "").strip()]
            locked = []
            unlocked = []
            for index in indices:
                try:
                    require_regeneration_unlocked(chunks[index])
                except Exception as exc:
                    locked.append((index, str(exc)))
                else:
                    unlocked.append(index)
            results["failed"].extend(locked)
            indices = unlocked

        generation_chunks = {}
        spoken_continuities = {}
        for idx in indices:
            generation_chunk, continuity = self._chunk_with_spoken_continuity(
                chunks,
                idx,
                bind=True,
            )
            generation_chunks[idx] = generation_chunk
            spoken_continuities[idx] = continuity

        total = len(indices)

        if total == 0:
            return results

        print(f"Starting batch generation of {total} chunks (batch_size={batch_size}, seed={batch_seed}, "
              f"group_by_type={batch_group_by_type})...")
        voice_config = {}
        try:
            if os.path.exists(self.voice_config_path):
                with open(self.voice_config_path, "r", encoding="utf-8") as f:
                    voice_config = json.load(f)
        except Exception as e:
            failure = self._mark_batch_audio_generation_failed(chunks, indices, e)
            self.save_chunks(chunks)
            for idx in indices:
                results["failed"].append((idx, failure.message))
            return results

        # Validate every used alias before changing chunk state or initializing
        # a model. Invalid legacy configurations remain file-pure.
        resolved_speakers = {}
        seed_resolutions = {}
        prompt_resolutions = {}
        responsive_resolutions = {}
        pronunciation_resolutions = {}
        generation_provenances = {}
        try:
            for idx in indices:
                speaker = chunks[idx].get("speaker", "")
                resolved_speakers[idx] = self._resolve_alias(
                    speaker,
                    voice_config,
                )
        except VoiceAliasError as e:
            for idx in indices:
                results["failed"].append((idx, str(e)))
            return results
        except Exception as e:
            failure = self._mark_batch_audio_generation_failed(chunks, indices, e)
            self.save_chunks(chunks)
            for idx in indices:
                results["failed"].append((idx, failure.message))
            return results

        # Get the engine object only after alias validation. Models remain
        # lazy, but an unavailable backend is still a persisted failure.
        try:
            engine = self.get_engine()
        except Exception as e:
            failure = self._mark_batch_audio_generation_failed(chunks, indices, e)
            self.save_chunks(chunks)
            for idx in indices:
                results["failed"].append((idx, failure.message))
            return results
        if not engine:
            failure = self._mark_batch_audio_generation_failed(
                chunks,
                indices,
                "TTS engine not initialized",
            )
            self.save_chunks(chunks)
            for idx in indices:
                results["failed"].append((idx, failure.message))
            return results

        try:
            explicit_seed = batch_seed if batch_seed is not None and batch_seed >= 0 else None
            for idx in indices:
                voice_data = voice_config.get(resolved_speakers[idx], {})
                generation_provenances[idx] = self._engine_generation_provenance(
                    engine,
                    voice_data,
                )
                seed_resolutions[idx] = self._generation_seed_resolution(
                    chunk=generation_chunks[idx],
                    voice_config=voice_config,
                    resolved_speaker=resolved_speakers[idx],
                    explicit_seed=explicit_seed,
                    seed_supported=self._engine_supports_generation_seed(
                        engine,
                        voice_data,
                        batch=True,
                        shared_seed=explicit_seed is not None,
                    ),
                )
                prompt_resolutions[idx] = resolve_experimental_prompt_override(
                    voice_data=voice_config.get(resolved_speakers[idx], {}),
                    instruction=generation_chunks[idx].get("effective_instruct", ""),
                    project_root=self.root_dir,
                )
                responsive_resolutions[idx] = resolve_recurring_voice_route(
                    voice_data=voice_config.get(resolved_speakers[idx], {}),
                    instruction=generation_chunks[idx].get("effective_instruct", ""),
                    project_root=self.root_dir,
                    verify_audio=True,
                )
                pronunciation_resolutions[idx] = (
                    self._resolve_chunk_pronunciation(
                        index=idx,
                        chunk=generation_chunks[idx],
                        speaker=chunks[idx].get("speaker", ""),
                        resolved_speaker=resolved_speakers[idx],
                        voice_data=voice_data,
                    )
                )
                generation_chunks[idx].update(
                    pronunciation_chunk_fields(
                        pronunciation_resolutions[idx]
                    )
                )
                if (
                    pronunciation_resolutions[idx]["receipt"][
                        "applied_count"
                    ]
                    and generation_chunks[idx].get("fish_render_plan")
                    is not None
                ):
                    generation_chunks[idx]["fish_render_plan"] = None
                    generation_chunks[idx][
                        "pronunciation_fish_inline_plan_bypassed_reason"
                    ] = "pronunciation_changed_plan_text"
        except VoiceAliasError as e:
            for idx in indices:
                results["failed"].append((idx, str(e)))
            return results
        except Exception as e:
            failure = self._mark_batch_audio_generation_failed(chunks, indices, e)
            self.save_chunks(chunks)
            for idx in indices:
                results["failed"].append((idx, failure.message))
            return results

        # Mark every selected chunk non-current before generation begins.
        # The prior file may remain on disk until a validated replacement is
        # installed, but it is no longer eligible for final output.
        previous_audio_paths = {}
        for idx in indices:
            if 0 <= idx < len(chunks):
                previous = chunks[idx].get("audio_path") or chunks[idx].get("stale_audio_path")
                previous_audio_paths[idx] = previous
                chunks[idx].update(
                    {
                        "status": "generating",
                        "audio_path": None,
                        "audio_state": "stale" if previous else "generating",
                        "stale_audio_path": previous,
                        "audio_fingerprint": None,
                        "audio_sha256": None,
                        "audio_size_bytes": None,
                        "audio_duration_ms": None,
                        "audio_format": None,
                        "generation_provenance": None,
                        "generated_at_utc": None,
                        "error": None,
                        "error_code": None,
                        **fish_cloud_chunk_reset_fields(),
                        **generation_seed_chunk_fields(seed_resolutions[idx]),
                        **experimental_prompt_chunk_fields(
                            prompt_resolutions[idx]
                        ),
                        **recurring_voice_chunk_fields(
                            responsive_resolutions[idx]
                        ),
                        **pronunciation_chunk_fields(
                            pronunciation_resolutions[idx]
                        ),
                        **synthesis_receipt_reset_fields(),
                    }
                )
        self.save_chunks(chunks)

        # Optionally reorder indices so same voice-type chunks are contiguous.
        # This produces larger homogeneous batches (e.g. all custom voices
        # together) instead of fragmenting each batch across voice types.
        if batch_group_by_type:
            indices = self._group_indices_by_voice_type(indices, chunks, voice_config)

        # Split indices into batches
        batches = [indices[i:i + batch_size] for i in range(0, len(indices), batch_size)]
        print(f"Processing {len(batches)} batches...")

        cancelled = False
        for batch_num, batch_indices in enumerate(batches):
            if cancel_check and cancel_check():
                cancelled = True
                print(f"[CANCEL] Cancellation requested before batch {batch_num + 1}")
                break

            print(f"Batch {batch_num + 1}/{len(batches)}: {len(batch_indices)} chunks")

            # Build batch request data
            batch_chunks = []
            for idx in batch_indices:
                if 0 <= idx < len(chunks):
                    chunk = generation_chunks[idx]
                    # Resolve aliases so batch uses canonical speaker config
                    canonical = resolved_speakers[idx]
                    batch_chunks.append({
                        "index": idx,
                        "text": pronunciation_resolutions[idx][
                            "synthesis_text"
                        ],
                        "instruct": chunk.get("effective_instruct", ""),
                        "fish_instruction": chunk.get(
                            "effective_fish_instruct",
                            chunk.get("effective_instruct", ""),
                        ),
                        "speaker": canonical,
                        "generation_seed": seed_resolutions[idx].get("seed"),
                        "fish_render_plan": chunk.get("fish_render_plan"),
                    })

            # Call batch TTS with single seed. A raised backend exception must
            # not strand the selected rows in `generating`.
            try:
                batch_generation_kwargs = {}
                try:
                    batch_parameters = inspect.signature(
                        engine.generate_batch
                    ).parameters
                except (TypeError, ValueError):
                    batch_parameters = {}
                if (
                    isinstance(generation_contexts, dict)
                    and "generation_contexts" in batch_parameters
                ):
                    batch_generation_kwargs["generation_contexts"] = (
                        generation_contexts
                    )
                batch_results = engine.generate_batch(
                    batch_chunks,
                    voice_config,
                    self.root_dir,
                    batch_seed,
                    **batch_generation_kwargs,
                )
            except Exception as e:
                chunks = self.load_chunks()
                failure = self._mark_batch_audio_generation_failed(
                    chunks,
                    batch_indices,
                    e,
                )
                for idx in batch_indices:
                    if 0 <= idx < len(chunks):
                        results["failed"].append((idx, failure.message))
                    self._remove_generated_temp(
                        os.path.join(self.root_dir, f"temp_batch_{idx}.wav")
                    )
                self.save_chunks(chunks)
                if progress_callback:
                    progress_callback(
                        len(results["completed"]),
                        len(results["failed"]),
                        total,
                    )
                continue

            try:
                completed_indices, failed_entries = self._validated_batch_result(
                    batch_results,
                    batch_indices,
                )
                responsive_receipts = (
                    batch_results.get("responsive_receipts", {})
                    if isinstance(batch_results, dict)
                    else {}
                )
            except Exception as e:
                chunks = self.load_chunks()
                failure = self._mark_batch_audio_generation_failed(
                    chunks,
                    batch_indices,
                    e,
                )
                self.save_chunks(chunks)
                for idx in batch_indices:
                    results["failed"].append((idx, failure.message))
                    self._remove_generated_temp(
                        os.path.join(self.root_dir, f"temp_batch_{idx}.wav")
                    )
                if progress_callback:
                    progress_callback(
                        len(results["completed"]),
                        len(results["failed"]),
                        total,
                    )
                continue

            # Process completed chunks - convert to MP3 and update status
            chunks = self.load_chunks()  # Reload for each batch

            for idx in completed_indices:
                if not (0 <= idx < len(chunks)):
                    print(f"Chunk {idx} skipped: index out of range (chunks changed during generation?)")
                    results["failed"].append((idx, "Index out of range after reload"))
                    continue

                temp_path = os.path.join(self.root_dir, f"temp_batch_{idx}.wav")

                if not os.path.exists(temp_path):
                    failure = normalize_audio_failure("Temp audio file not found")
                    results["failed"].append((idx, failure.message))
                    chunks[idx].update(
                        {
                            "status": "error",
                            "audio_state": "failed",
                            "error": failure.message,
                            "error_code": failure.code,
                        }
                    )
                    continue

                try:
                    chunk = chunks[idx]
                    generation_chunk = generation_chunks[idx]
                    metadata_reader = getattr(
                        engine,
                        "pop_generation_metadata",
                        None,
                    )
                    generation_metadata = (
                        metadata_reader(temp_path)
                        if callable(metadata_reader)
                        else {}
                    )
                    actual_provenance = generation_metadata.pop(
                        "generation_provenance",
                        None,
                    ) or generation_provenances[idx]
                    binding_chunk = {**generation_chunk, **generation_metadata}
                    artifact_fields = copy.deepcopy(generation_metadata)
                    artifact_fields["audio_fingerprint"] = self._audio_binding(
                        binding_chunk,
                        voice_config,
                        resolved_speaker=resolved_speakers[idx],
                        seed_resolution=seed_resolutions[idx],
                    )
                    artifact_fields.update(
                        recurring_voice_chunk_fields(
                            responsive_resolutions[idx]
                        )
                    )
                    responsive_receipt = responsive_receipts.get(idx)
                    if isinstance(responsive_receipt, dict):
                        artifact_fields.update(responsive_receipt)
                    artifact_fields.update(
                        {
                            **experimental_prompt_chunk_fields(
                                prompt_resolutions[idx]
                            ),
                            "spoken_continuity_applied": spoken_continuities[idx],
                            "spoken_continuity_effective_instruct": generation_chunk.get(
                                "effective_instruct",
                                "",
                            ),
                            "backend_render_plan_applied": (
                                backend_render_plan_application_record(generation_chunk)
                            ),
                            "backend_render_plan_effective_qwen_instruction": (
                                generation_chunk.get("effective_instruct", "")
                            ),
                            "backend_render_plan_effective_fish_instruction": (
                                generation_chunk.get(
                                    "effective_fish_instruct",
                                    generation_chunk.get("effective_instruct", ""),
                                )
                            ),
                            **pronunciation_chunk_fields(
                                pronunciation_resolutions[idx]
                            ),
                            "pronunciation_fish_inline_plan_bypassed_reason": (
                                generation_chunk.get(
                                    "pronunciation_fish_inline_plan_bypassed_reason"
                                )
                            ),
                            "generation_provenance": actual_provenance or None,
                            "generated_at_utc": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(),
                            ),
                        }
                    )
                    generation_context = (
                        generation_contexts.get(idx)
                        if isinstance(generation_contexts, dict)
                        else None
                    )
                    artifact = self._install_chunk_audio(
                        index=idx,
                        chunk=generation_chunk,
                        resolved_speaker=resolved_speakers[idx],
                        voice_config=voice_config,
                        source_path=temp_path,
                        previous_audio_path=previous_audio_paths.get(idx),
                        seed_resolution=seed_resolutions[idx],
                        expected_text=pronunciation_resolutions[idx][
                            "synthesis_text"
                        ],
                        artifact_fields=artifact_fields,
                        generation_context=generation_context,
                    )
                    self._register_generated_take(
                        index=idx,
                        chunk={**generation_chunk, **artifact},
                        resolved_speaker=resolved_speakers[idx],
                        voice_config=voice_config,
                        artifact=artifact,
                        generation_context=generation_context,
                    )
                    artifact.pop("take_id", None)
                    artifact.pop("take_chunk_key", None)
                    chunks[idx].update(
                        {
                            "status": "done",
                            "error": None,
                            "error_code": None,
                            **artifact,
                        }
                    )
                    results["completed"].append(idx)
                    print(f"Chunk {idx} completed: {artifact['audio_path']}")

                except Exception as e:
                    failure = normalize_audio_failure(e)
                    print(f"Error processing chunk {idx}: {failure.message}")
                    results["failed"].append((idx, failure.message))
                    chunks[idx].update(
                        {
                            "status": "error",
                            "audio_state": "failed",
                            "error": failure.message,
                            "error_code": failure.code,
                        }
                    )
                finally:
                    self._remove_generated_temp(temp_path)

            for idx, error in failed_entries:
                failure = normalize_audio_failure(error)
                if 0 <= idx < len(chunks):
                    chunks[idx].update(
                        {
                            "status": "error",
                            "audio_state": "failed",
                            "error": failure.message,
                            "error_code": failure.code,
                        }
                    )
                results["failed"].append((idx, failure.message))

            self.save_chunks(chunks)

            if progress_callback:
                progress_callback(len(results["completed"]), len(results["failed"]), total)

        # Reset remaining "generating" chunks to "pending" on cancel or completion
        done_indices = set(results["completed"]) | {idx for idx, _ in results["failed"]}
        chunks = self.load_chunks()
        if chunks:
            for idx in indices:
                if idx not in done_indices and 0 <= idx < len(chunks) and chunks[idx].get("status") == "generating":
                    chunks[idx]["status"] = "pending"
                    chunks[idx]["audio_state"] = (
                        "stale" if chunks[idx].get("stale_audio_path") else "pending"
                    )
                    results["cancelled"] += 1
            if results["cancelled"]:
                self.save_chunks(chunks)

        print(f"Batch generation complete: {len(results['completed'])} succeeded, "
              f"{len(results['failed'])} failed, {results['cancelled']} cancelled")
        return results
