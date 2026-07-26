import os
import json
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
from audio_artifacts import (
    AudioArtifactError,
    atomic_export_audio_segment,
    audio_binding_fingerprint,
    install_generated_audio,
    require_current_project_audio,
)
from audio_generation_policy import (
    apply_generation_seed_to_voice_config,
    generation_seed_chunk_fields,
    generation_seed_synthesis_binding,
    persisted_generation_seed_resolution,
    resolve_generation_seed,
    voice_supports_deterministic_seed,
)
from experimental_prompt_routing import (
    experimental_prompt_chunk_fields,
    resolve_experimental_prompt_override,
)
from utils import atomic_json_write
from voice_aliases import resolve_voice_alias
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
        self.config_path = str(
            Path(config_path).expanduser().resolve()
            if config_path
            else Path(self.root_dir, "app", "config.json").resolve()
        )

        # Ensure voicelines dir exists
        os.makedirs(self.voicelines_dir, exist_ok=True)

        self.engine = None
        self._engine_lock = threading.Lock()
        self._chunks_lock = threading.Lock()  # Thread-safe file writes

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
                self.engine = TTSEngine(config)
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

    def _synthesis_config(self):
        """Return only settings that can change synthesized chunk audio."""
        config = dict(self._load_tts_config())
        config.pop("pause_between_speakers_ms", None)
        config.pop("pause_same_speaker_ms", None)
        return config

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
            synthesis_config=self._synthesis_config(),
            explicit_seed=explicit_seed,
            deterministic_enabled=bool(
                tts_config.get("deterministic_seed_enabled", True)
            ),
            deterministic_base_seed=tts_config.get(
                "deterministic_seed_base"
            ),
            seed_supported=seed_supported,
        )

    def _audio_binding(
        self,
        chunk,
        voice_config,
        resolved_speaker=None,
        seed_resolution=None,
    ):
        resolved = resolved_speaker or self._resolve_alias(
            chunk.get("speaker", ""),
            voice_config,
        )
        synthesis = self._synthesis_config()
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
    ):
        previous = chunk.get("audio_path") or chunk.get("stale_audio_path")
        seed_fields = (
            generation_seed_chunk_fields(seed_resolution)
            if seed_resolution is not None
            else {}
        )
        prompt_fields = experimental_prompt_chunk_fields(prompt_resolution)
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
            **seed_fields,
            **prompt_fields,
        )

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
    ):
        filename_base = (
            f"voiceline_{index + 1:04d}_"
            f"{sanitize_filename(resolved_speaker)}"
        )
        return install_generated_audio(
            root_dir=self.root_dir,
            voicelines_dir=self.voicelines_dir,
            source_audio_path=source_path,
            filename_base=filename_base,
            binding_fingerprint=self._audio_binding(
                chunk,
                voice_config,
                resolved_speaker,
                seed_resolution=seed_resolution,
            ),
            previous_audio_path=previous_audio_path,
        )

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
        with self._chunks_lock:
            atomic_json_write(chunks, self.chunks_path)

    def _update_chunk_fields(self, index, **fields):
        """Atomically update fields on a single chunk (thread-safe read-modify-write).

        Unlike load_chunks() + modify + save_chunks(), this holds the lock for the
        entire read-modify-write cycle, preventing concurrent threads from
        overwriting each other's updates.
        """
        with self._chunks_lock:
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
        with self._chunks_lock:
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
        with self._chunks_lock:
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
        with self._chunks_lock:
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
        chunks = self.load_chunks()
        if 0 <= index < len(chunks):
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
            # The old file is retained only as a stale path until a validated
            # replacement succeeds, then the installer removes it.
            if "text" in data or "instruct" in data or "speaker" in data:
                previous = chunk.get("audio_path") or chunk.get("stale_audio_path")
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
                        "generation_seed": None,
                        "generation_seed_source": None,
                        "generation_seed_basis": None,
                        "audio_research_only": False,
                        "experimental_prompt_route": None,
                        "experimental_prompt_role": None,
                        "experimental_prompt_evidence_round_id": None,
                        "production_promotion_allowed": False,
                    }
                )

            self.save_chunks(chunks)
            return chunk
        return None

    def generate_chunk_audio(self, index, generation_seed=None):
        chunks = self.load_chunks()
        if not (0 <= index < len(chunks)):
            return False, "Invalid chunk index"

        chunk = chunks[index]

        # Validate and resolve the production voice before invalidating current
        # audio or initializing a model. Invalid legacy aliases remain file-pure.
        try:
            voice_config = {}
            if os.path.exists(self.voice_config_path):
                with open(self.voice_config_path, "r", encoding="utf-8") as f:
                    voice_config = json.load(f)
            speaker = chunk["speaker"]
            canonical_speaker = self._resolve_alias(speaker, voice_config)
            engine = self.get_engine()
            if not engine:
                return False, "TTS engine not initialized"
            voice_data = voice_config.get(canonical_speaker, {})
            seed_resolution = self._generation_seed_resolution(
                chunk=chunk,
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
                instruction=chunk.get("instruct", ""),
                project_root=self.root_dir,
            )
        except Exception as e:
            return False, str(e)

        previous_audio_path = chunk.get("audio_path") or chunk.get("stale_audio_path")
        self._mark_audio_generation_started(
            index,
            chunk,
            seed_resolution=seed_resolution,
            prompt_resolution=prompt_resolution,
        )

        try:
            if canonical_speaker != speaker:
                print(f"Resolving alias: '{speaker}' -> '{canonical_speaker}'")
            text = chunk["text"]
            instruct = chunk.get("instruct", "")

            print(
                f"Generating chunk {index}: speaker={speaker}, "
                f"instruct='{instruct}', text='{text[:50]}...'"
            )

            # The TTS engine writes a non-canonical source file. Installation
            # validates and atomically replaces the canonical production file.
            temp_path = os.path.join(self.root_dir, f"temp_chunk_{index}.wav")
            success = engine.generate_voice(
                text,
                instruct,
                canonical_speaker,
                effective_voice_config,
                temp_path,
            )

            if not success:
                self._update_chunk_fields(
                    index,
                    status="error",
                    audio_state="failed",
                )
                return False, "Generation failed"

            artifact = self._install_chunk_audio(
                index=index,
                chunk=chunk,
                resolved_speaker=canonical_speaker,
                voice_config=voice_config,
                source_path=temp_path,
                previous_audio_path=previous_audio_path,
                seed_resolution=seed_resolution,
            )
            artifact.update(
                experimental_prompt_chunk_fields(prompt_resolution)
            )
            self._update_chunk_fields(index, status="done", **artifact)

            if os.path.exists(temp_path):
                for attempt in range(3):
                    try:
                        os.remove(temp_path)
                        break
                    except OSError:
                        if attempt < 2:
                            time.sleep(0.1 * (attempt + 1))
                        else:
                            print(f"Warning: Could not delete temp file {temp_path}")

            return True, artifact["audio_path"]

        except Exception as e:
            try:
                self._update_chunk_fields(
                    index,
                    status="error",
                    audio_state="failed",
                )
            except Exception as update_err:
                print(
                    f"Warning: Failed to update chunk {index} status to error: "
                    f"{update_err}"
                )
            return False, str(e)

    def _load_pause_defaults(self):
        """Return (pause_between_speakers_ms, pause_same_speaker_ms) from config."""
        tts_cfg = self._load_tts_config()
        return (
            tts_cfg.get("pause_between_speakers_ms", DEFAULT_PAUSE_MS),
            tts_cfg.get("pause_same_speaker_ms", SAME_SPEAKER_PAUSE_MS),
        )

    def _load_chunks_with_audio(self):
        """Load only audio bound to the current chunk and voice configuration."""
        chunks = self.load_chunks()
        voice_config = {}
        if os.path.exists(self.voice_config_path):
            with open(self.voice_config_path, "r", encoding="utf-8") as f:
                voice_config = json.load(f)

        def expected_fingerprint(chunk):
            return self._audio_binding(chunk, voice_config)

        return require_current_project_audio(
            root_dir=self.root_dir,
            chunks=chunks,
            expected_fingerprint=expected_fingerprint,
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
        pause_overrides = [chunk.get("pause_after") for chunk, _, _ in timeline]

        final_audio = combine_audio_with_pauses(
            audio_segments, speakers, pause_ms, same_speaker_pause_ms, pause_overrides
        )
        output_filename = "cloned_audiobook.mp3"
        try:
            target = self._confined_export_target(output_path, output_filename)
            atomic_export_audio_segment(
                segment=final_audio,
                target_path=target,
                audio_format="mp3",
            )
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
        try:
            chunks_with_audio = self._load_chunks_with_audio()
        except AudioArtifactError as exc:
            return False, str(exc)

        # Phase 1 — Compute timeline
        pause_ms, same_speaker_pause_ms = self._load_pause_defaults()
        timeline = compute_timeline(chunks_with_audio, pause_ms, same_speaker_pause_ms)

        if not timeline:
            return False, "No audio segments found"

        # Phase 2 — Build chapters
        chapters = self._build_m4b_chapters(timeline, per_chunk_chapters)
        print(f"  M4B: {len(chapters)} chapters")

        # Phase 3 — Combine audio and export to temp WAV
        audio_segments = [seg for _, seg, _ in timeline]
        speakers = [chunk["speaker"] for chunk, _, _ in timeline]
        pause_overrides = [chunk.get("pause_after") for chunk, _, _ in timeline]
        final_audio = combine_audio_with_pauses(
            audio_segments, speakers, pause_ms, same_speaker_pause_ms, pause_overrides
        )

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
            with open(temp_wav, "wb") as wav_output:
                final_audio.export(wav_output, format="wav")

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

            cmd = ["ffmpeg", "-y", "-i", temp_wav]
            if has_cover:
                cmd += ["-i", cover_path]
            cmd += ["-i", meta_path, "-map_metadata", "2" if has_cover else "1"]
            # Map audio stream
            cmd += ["-map", "0:a"]
            if has_cover:
                # Map cover as attached picture
                cmd += ["-map", "1:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
            cmd += [
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                temporary_output
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                print(f"FFmpeg stderr: {result.stderr[-500:]}")
                return False, f"FFmpeg failed (exit {result.returncode})"
            if not os.path.exists(temporary_output) or os.path.getsize(temporary_output) <= 0:
                return False, "FFmpeg produced no M4B output"
            try:
                verified = AudioSegment.from_file(temporary_output)
            except Exception as exc:
                return False, f"Generated M4B could not be decoded: {exc}"
            if len(verified) <= 0:
                return False, "Generated M4B has zero duration"
            os.replace(temporary_output, output_target)

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

    # Regex for detecting chapter/section headings in chunk text
    _HEADING_RE = re.compile(
        r'^(chapter|part|book|volume|prologue|epilogue|introduction|conclusion|act|section)\b',
        re.IGNORECASE
    )

    def _build_m4b_chapters(self, timeline, per_chunk_chapters):
        """Build chapter list from timeline entries.

        Returns:
            list of (title, start_ms, end_ms) tuples
        """
        if per_chunk_chapters:
            chapters = []
            for chunk, segment, start_ms in timeline:
                end_ms = start_ms + len(segment)
                text_preview = chunk.get("text", "")[:80]
                title = f"[{chunk['speaker']}] {text_preview}"
                chapters.append((title, start_ms, end_ms))
            return chapters

        # Smart grouping: detect chapter headings
        heading_indices = []
        for i, (chunk, segment, start_ms) in enumerate(timeline):
            text = chunk.get("text", "").strip()
            # Short structural text (likely a heading) or starts with heading keyword
            if self._HEADING_RE.match(text):
                heading_indices.append(i)
            elif len(text) < 80 and '"' not in text and text and self._HEADING_RE.search(text):
                heading_indices.append(i)

        # If no headings detected, fall back to per-chunk
        if not heading_indices:
            print("  M4B: No chapter headings detected, falling back to per-chunk chapters")
            return self._build_m4b_chapters(timeline, per_chunk_chapters=True)

        chapters = []

        # Pre-heading chunks → "Introduction"
        if heading_indices[0] > 0:
            start_ms = timeline[0][2]
            last_before = heading_indices[0] - 1
            end_ms = timeline[last_before][2] + len(timeline[last_before][1])
            chapters.append(("Introduction", start_ms, end_ms))

        # Each heading starts a chapter that runs until the next heading
        for idx, head_i in enumerate(heading_indices):
            title = timeline[head_i][0].get("text", "").strip()
            # Truncate long titles
            if len(title) > 120:
                title = title[:117] + "..."

            start_ms = timeline[head_i][2]

            # End = start of next heading, or end of last chunk
            if idx + 1 < len(heading_indices):
                next_head_i = heading_indices[idx + 1]
                last_in_group = next_head_i - 1
            else:
                last_in_group = len(timeline) - 1

            end_ms = timeline[last_in_group][2] + len(timeline[last_in_group][1])
            chapters.append((title, start_ms, end_ms))

        return chapters

    def generate_chunks_parallel(
        self,
        indices,
        max_workers=2,
        progress_callback=None,
        cancel_check=None,
        generation_seed=None,
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
                    results["failed"].append((idx, str(e)))
                    print(f"Chunk {idx} error: {e}")

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
                               batch_group_by_type=False, cancel_check=None):
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

        total = len(indices)

        if total == 0:
            return results

        print(f"Starting batch generation of {total} chunks (batch_size={batch_size}, seed={batch_seed}, "
              f"group_by_type={batch_group_by_type})...")
        voice_config = {}
        if os.path.exists(self.voice_config_path):
            with open(self.voice_config_path, "r", encoding="utf-8") as f:
                voice_config = json.load(f)

        # Get the engine object before resolving seed capability. Models remain
        # lazy, but the active backend determines whether a seed is meaningful.
        engine = self.get_engine()
        if not engine:
            for idx in indices:
                results["failed"].append((idx, "TTS engine not initialized"))
            return results

        # Validate and resolve every used alias before changing chunk state or
        # initializing a model. This makes invalid legacy configurations file-pure.
        resolved_speakers = {}
        seed_resolutions = {}
        prompt_resolutions = {}
        try:
            explicit_seed = batch_seed if batch_seed is not None and batch_seed >= 0 else None
            for idx in indices:
                speaker = chunks[idx].get("speaker", "")
                resolved_speakers[idx] = self._resolve_alias(
                    speaker,
                    voice_config,
                )
                voice_data = voice_config.get(resolved_speakers[idx], {})
                seed_resolutions[idx] = self._generation_seed_resolution(
                    chunk=chunks[idx],
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
                    instruction=chunks[idx].get("instruct", ""),
                    project_root=self.root_dir,
                )
        except Exception as e:
            for idx in indices:
                results["failed"].append((idx, str(e)))
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
                        **generation_seed_chunk_fields(seed_resolutions[idx]),
                        **experimental_prompt_chunk_fields(
                            prompt_resolutions[idx]
                        ),
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
                    chunk = chunks[idx]
                    # Resolve aliases so batch uses canonical speaker config
                    canonical = resolved_speakers[idx]
                    batch_chunks.append({
                        "index": idx,
                        "text": chunk.get("text", ""),
                        "instruct": chunk.get("instruct", ""),
                        "speaker": canonical,
                        "generation_seed": seed_resolutions[idx].get("seed"),
                    })

            # Call batch TTS with single seed
            batch_results = engine.generate_batch(batch_chunks, voice_config, self.root_dir, batch_seed)

            # Process completed chunks - convert to MP3 and update status
            chunks = self.load_chunks()  # Reload for each batch

            for idx in batch_results["completed"]:
                if not (0 <= idx < len(chunks)):
                    print(f"Chunk {idx} skipped: index out of range (chunks changed during generation?)")
                    results["failed"].append((idx, "Index out of range after reload"))
                    continue

                temp_path = os.path.join(self.root_dir, f"temp_batch_{idx}.wav")

                if not os.path.exists(temp_path):
                    results["failed"].append((idx, "Temp audio file not found"))
                    chunks[idx]["status"] = "error"
                    chunks[idx]["audio_state"] = "failed"
                    continue

                try:
                    chunk = chunks[idx]
                    artifact = self._install_chunk_audio(
                        index=idx,
                        chunk=chunk,
                        resolved_speaker=resolved_speakers[idx],
                        voice_config=voice_config,
                        source_path=temp_path,
                        previous_audio_path=previous_audio_paths.get(idx),
                        seed_resolution=seed_resolutions[idx],
                    )
                    artifact.update(
                        experimental_prompt_chunk_fields(
                            prompt_resolutions[idx]
                        )
                    )
                    chunks[idx].update({"status": "done", **artifact})
                    results["completed"].append(idx)
                    print(f"Chunk {idx} completed: {artifact['audio_path']}")

                    if os.path.exists(temp_path):
                        for attempt in range(3):
                            try:
                                os.remove(temp_path)
                                break
                            except OSError:
                                if attempt < 2:
                                    time.sleep(0.1 * (attempt + 1))
                                else:
                                    print(f"Warning: Could not delete temp file {temp_path}")

                except Exception as e:
                    print(f"Error processing chunk {idx}: {e}")
                    results["failed"].append((idx, str(e)))
                    chunks[idx]["status"] = "error"
                    chunks[idx]["audio_state"] = "failed"

            for idx, error in batch_results["failed"]:
                if 0 <= idx < len(chunks):
                    chunks[idx]["status"] = "error"
                    chunks[idx]["audio_state"] = "failed"
                results["failed"].append((idx, error))

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
