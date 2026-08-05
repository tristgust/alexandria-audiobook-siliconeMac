import atexit
import copy
import os
import re
import json
import hashlib
import secrets
import threading
import shutil
import platform
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
from pydub import AudioSegment

from audio_edge_safety import ensure_click_safe_fade_in
from audio_generation_provenance import resolve_audio_generation_provenance
from audio_processing import (
    prepare_generated_speech_audio,
    voice_design_max_tokens,
)
from audio_generation_lifecycle import (
    AudioGenerationLifecycleError,
    completed_segment_artifact,
    record_segment_completed,
    record_segment_failed,
    record_segment_started,
    segment_output_path,
    should_cancel as generation_should_cancel,
)
from synthesis_windows import (
    SynthesisWindowError,
    assemble_synthesis_segments,
    plan_synthesis_segments,
    resolve_synthesis_backend_id,
    synthesis_receipt_chunk_fields,
)
from hf_access import snapshot_download_with_public_fallback
from instruction_propagation import (
    InstructionPropagationError,
    build_instruction_propagation_contract,
    format_instruction_prompt,
    normalize_instruction,
    validate_instruction_propagation_contract,
)
from model_registry import (
    engine_ids_for_voice_method,
    engine_record_payload,
    is_registered_model,
    model_spec,
    resolve_model_path,
)
from model_memory import ModelMemoryCoordinator
from experimental_prompt_routing import (
    resolve_experimental_prompt_override,
    strip_prompt_route_tag,
)
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    resolve_recurring_voice_route,
)
from production_prompt_routes import stage_verified_responsive_voice_assets
from production_voice_evidence import resolve_production_voice_prompt
from responsive_voice_backend import (
    ResponsiveBackendUnavailable,
    ResponsiveVoiceBackend,
    ResponsiveVoiceBackendError,
)
from voice_effects import apply_voice_effect_chain
from fish_cloud_tts import (
    DEFAULT_FISH_MODEL,
    FishCloudBackend,
    FishCloudError,
    audio_features,
)
from fish_hybrid_policy import (
    fish_hybrid_decision,
    normalized_fish_hybrid_policy,
)
from fish_inline_cues import plan_fingerprint, validate_plan
from dialogue_continuity import effective_pause_after_ms

DEFAULT_PAUSE_MS = 500  # Pause between different speakers
SAME_SPEAKER_PAUSE_MS = 250  # Shorter pause for same speaker continuing
PYTORCH_CUSTOM_MODEL = model_spec("pytorch_qwen_custom_voice").repo_id
PYTORCH_CLONE_MODEL = model_spec("pytorch_qwen_base").repo_id
PYTORCH_DESIGN_MODEL = model_spec("pytorch_qwen_voice_design").repo_id
CONTROLLED_CLONE_BACKENDS = frozenset(
    engine_ids_for_voice_method("controlled_clone")
)
INSTRUCTION_CONTROLLED_BACKEND = engine_record_payload(
    "qwen3_instruction_controlled"
)["engine_id"]
LEGACY_CONTROLLED_BACKEND = engine_record_payload("voxcpm2_controlled")[
    "engine_id"
]


def sanitize_filename(name):
    """Make a string safe for use in filenames"""
    name = re.sub(r'[^\w\-]', '_', name)
    return name.lower()


def combine_audio_with_pauses(
    audio_segments,
    speakers,
    pause_ms=DEFAULT_PAUSE_MS,
    same_speaker_pause_ms=SAME_SPEAKER_PAUSE_MS,
    pause_overrides=None,
    progress_callback=None,
    cancel_check=None,
):
    """Combine click-safe audio segments with pauses in linear time.

    Args:
        pause_overrides: Optional list aligned with audio_segments. Each entry is
            the pause (ms) to insert *after* that segment, or None to use the
            default speaker-change logic. The last entry is ignored.
        progress_callback: Optional callback receiving ``(completed, total)``.
        cancel_check: Optional callable returning True when assembly should stop.
    """
    if not audio_segments:
        return None

    total = len(audio_segments)
    if len(speakers) != total:
        raise ValueError("speakers must align with audio_segments")
    if pause_overrides is not None and len(pause_overrides) != total:
        raise ValueError("pause_overrides must align with audio_segments")

    target_frame_rate = max(segment.frame_rate for segment in audio_segments)
    target_sample_width = max(segment.sample_width for segment in audio_segments)
    target_channels = max(segment.channels for segment in audio_segments)

    def normalized(segment):
        value = ensure_click_safe_fade_in(segment)
        if value.frame_rate != target_frame_rate:
            value = value.set_frame_rate(target_frame_rate)
        if value.channels != target_channels:
            value = value.set_channels(target_channels)
        if value.sample_width != target_sample_width:
            value = value.set_sample_width(target_sample_width)
        return value

    pieces = []
    first = None
    for index, segment in enumerate(audio_segments):
        if cancel_check and cancel_check():
            raise InterruptedError("Audio assembly cancelled")
        safe_segment = normalized(segment)
        if first is None:
            first = safe_segment
        pieces.append(safe_segment.raw_data)
        if index + 1 < total:
            override = pause_overrides[index] if pause_overrides else None
            if override is not None:
                gap_duration = int(override)
            elif speakers[index + 1] == speakers[index]:
                gap_duration = int(same_speaker_pause_ms)
            else:
                gap_duration = int(pause_ms)
            gap = AudioSegment.silent(
                duration=max(0, gap_duration),
                frame_rate=target_frame_rate,
            ).set_channels(target_channels).set_sample_width(target_sample_width)
            pieces.append(gap.raw_data)
        if progress_callback:
            progress_callback(index + 1, total)

    if cancel_check and cancel_check():
        raise InterruptedError("Audio assembly cancelled")
    return first._spawn(b"".join(pieces))


def compute_timeline(chunks_with_audio, pause_ms=DEFAULT_PAUSE_MS,
                     same_speaker_pause_ms=SAME_SPEAKER_PAUSE_MS):
    """Compute a timeline of (chunk, segment, abs_start_ms) tuples.

    Args:
        chunks_with_audio: list of (chunk_dict, AudioSegment) tuples.
            Each chunk_dict may have an optional 'pause_after' key (int ms)
            that overrides the default pause inserted after that chunk.
        pause_ms: Default pause between different speakers.
        same_speaker_pause_ms: Default pause when same speaker continues.

    Returns:
        list of (chunk_dict, AudioSegment, abs_start_ms) tuples.
    """
    timeline = []
    cursor_ms = 0
    prev_speaker = None
    prev_chunk = None

    for chunk, segment in chunks_with_audio:
        if prev_speaker is not None:
            override = effective_pause_after_ms(prev_chunk)
            if override is not None:
                gap = int(override)
            elif chunk["speaker"] == prev_speaker:
                gap = same_speaker_pause_ms
            else:
                gap = pause_ms
            cursor_ms += gap

        timeline.append((chunk, segment, cursor_ms))
        cursor_ms += len(segment)
        prev_speaker = chunk["speaker"]
        prev_chunk = chunk

    return timeline


class TTSEngine:
    """TTS engine supporting local (qwen-tts) and external (Gradio) backends.

    Mode is determined by config["tts"]["mode"]:
      - "local": Loads Qwen3TTSModel directly. No external server needed.
      - "external": Connects via Gradio client to a running TTS server.

    Models and clients are lazily initialized on first use.
    """

    def __init__(
        self,
        config,
        *,
        model_residency: ModelMemoryCoordinator | None = None,
    ):
        tts_config = config.get("tts", {})
        apple_silicon = (
            platform.system() == "Darwin" and platform.machine() == "arm64"
        )
        default_mode = "local" if apple_silicon else "external"
        self._mode = tts_config.get("mode", default_mode)
        self._url = tts_config.get("url", "http://127.0.0.1:7860")
        self._device = tts_config.get("device", "auto")
        self._compile_codec_enabled = tts_config.get("compile_codec", False)
        self._fish_cloud_enabled = bool(
            tts_config.get("fish_cloud_enabled", False)
        )
        self._fish_model = str(
            tts_config.get("fish_model", DEFAULT_FISH_MODEL)
        )
        self._fish_candidate_count = int(
            tts_config.get("fish_candidate_count", 2)
        )
        self._fish_difficult_candidate_count = int(
            tts_config.get("fish_difficult_candidate_count", 6)
        )
        self._fish_text_wer_limit = float(
            tts_config.get("fish_text_wer_limit", 0.08)
        )
        self._fish_timeout_seconds = int(
            tts_config.get("fish_timeout_seconds", 240)
        )

        # Language setting (passed to Qwen3-TTS)
        self._language = tts_config.get("language", "English")

        # Sub-batching config
        self._sub_batch_enabled = tts_config.get("sub_batch_enabled", True)
        self._sub_batch_min_size = max(1, tts_config.get("sub_batch_min_size", 4))
        self._sub_batch_ratio = max(1.0, float(tts_config.get("sub_batch_ratio", 5)))
        self._sub_batch_max_items = int(tts_config.get("sub_batch_max_items", 0))  # 0 = auto

        # Lazy-loaded backends (guarded by _model_lock to prevent concurrent loads)
        self._model_lock = threading.RLock()
        self._model_residency = model_residency or ModelMemoryCoordinator()
        self._local_custom_model = None
        self._local_clone_model = None
        self._local_design_model = None
        self._local_lora_model = None
        self._warmup_needed = True  # cleared after first batch warmup
        self._lora_adapter_path = None  # track which adapter is currently loaded
        self._gradio_client = None
        self._mlx_backend = None
        self._responsive_voice_backend = None
        self._responsive_generation_state = threading.local()
        self._fish_backend = None
        self._generation_metadata = {}
        self._generation_metadata_lock = threading.RLock()
        self._use_mlx = (
            self._mode == "local"
            and apple_silicon
        )
        if self._use_mlx:
            print("Apple Silicon detected: Alexandria will use MLX-Audio.")

        # Clone prompt cache: speaker_name -> (dependency fingerprint, reusable prompt)
        self._clone_prompt_cache = {}
        # LoRA clone prompt cache: adapter_path -> reusable voice_clone_prompt
        self._lora_prompt_cache = {}

    @property
    def mode(self):
        return self._mode

    @property
    def model_residency(self) -> ModelMemoryCoordinator:
        return self._model_residency

    @contextmanager
    def _pytorch_model_job(
        self,
        component_id,
        prepare,
        *,
        label,
    ):
        with self._model_residency.prepared_job(
            (component_id,),
            prepare,
            label=label,
        ) as model:
            yield model

    def generation_provenance(self, voice_data, *, source="generation"):
        return resolve_audio_generation_provenance(
            voice_data,
            mode=self._mode,
            use_mlx=self._use_mlx,
            source=source,
            fish_model=self._fish_model,
            external_url=self._url if self._mode != "local" else None,
        )

    def _init_fish(self):
        if not self._fish_cloud_enabled:
            raise FishCloudError(
                "fish_cloud_disabled",
                "Fish Audio is disabled in Speech settings.",
            )
        if self._fish_backend is None:
            with self._model_lock:
                if self._fish_backend is None:
                    self._fish_backend = FishCloudBackend(
                        model=self._fish_model,
                        candidate_count=self._fish_candidate_count,
                        difficult_candidate_count=(
                            self._fish_difficult_candidate_count
                        ),
                        text_wer_limit=self._fish_text_wer_limit,
                        timeout_seconds=self._fish_timeout_seconds,
                    )
        return self._fish_backend

    def _record_generation_metadata(self, output_path, metadata):
        key = str(Path(output_path).expanduser().resolve())
        with self._generation_metadata_lock:
            self._generation_metadata[key] = dict(metadata or {})

    def pop_generation_metadata(self, output_path):
        key = str(Path(output_path).expanduser().resolve())
        with self._generation_metadata_lock:
            return self._generation_metadata.pop(key, {})

    def _has_generation_metadata(self, output_path):
        key = str(Path(output_path).expanduser().resolve())
        with self._generation_metadata_lock:
            return key in self._generation_metadata

    def _peek_generation_metadata(self, output_path):
        key = str(Path(output_path).expanduser().resolve())
        with self._generation_metadata_lock:
            return copy.deepcopy(self._generation_metadata.get(key, {}))

    def _fish_generation_settings(self, voice_data):
        return {
            "temperature": voice_data.get("fish_temperature", 0.7),
            "top_p": voice_data.get("fish_top_p", 0.7),
            "repetition_penalty": voice_data.get(
                "fish_repetition_penalty",
                1.2,
            ),
            "latency": voice_data.get("fish_latency", "normal"),
        }

    def _generate_with_fish(
        self,
        *,
        text,
        instruction,
        speaker,
        ref_audio,
        ref_text,
        output_path,
        voice_data,
        route_mode,
        route_reason,
        render_plan=None,
        require_delivery_evidence=True,
        allow_text_mismatch=False,
        max_candidates=None,
        minimum_delivery_score=None,
        minimum_instruction_delivery_score=None,
        return_result=False,
    ):
        result = self._init_fish().generate(
            text=text,
            instruction=instruction,
            speaker=speaker,
            reference_audio=ref_audio,
            reference_text=ref_text,
            output_path=output_path,
            settings=self._fish_generation_settings(voice_data),
            render_plan=render_plan,
            require_delivery_evidence=require_delivery_evidence,
            allow_text_mismatch=allow_text_mismatch,
            max_candidates=max_candidates,
        )
        if (
            minimum_delivery_score is not None
            and result.selected.delivery_score < float(minimum_delivery_score)
        ):
            Path(output_path).unlink(missing_ok=True)
            raise FishCloudError(
                "fish_audition_delivery_too_flat",
                (
                    f"Fish {result.style} audition delivery scored "
                    f"{result.selected.delivery_score:.3f}; required "
                    f"{float(minimum_delivery_score):.3f}."
                ),
            )
        if (
            minimum_instruction_delivery_score is not None
            and result.selected.instruction_delivery_score
            < float(minimum_instruction_delivery_score)
        ):
            Path(output_path).unlink(missing_ok=True)
            raise FishCloudError(
                "fish_audition_instruction_not_expressed",
                (
                    f"Fish {result.style} audition instruction scored "
                    f"{result.selected.instruction_delivery_score:.3f}; required "
                    f"{float(minimum_instruction_delivery_score):.3f}."
                ),
            )
        fish_voice = dict(voice_data)
        fish_voice["clone_backend"] = "fish_s21_cloud"
        metadata = result.metadata()
        if render_plan is not None:
            normalized_plan = validate_plan(text, render_plan)
            metadata.update(
                {
                    "cloud_render_plan_fingerprint": plan_fingerprint(
                        normalized_plan
                    ),
                    "cloud_inline_cue_count": len(normalized_plan.cues),
                }
            )
        metadata.update(
            {
                "cloud_model": self._fish_model,
                "fish_route_mode": route_mode,
                "fish_route_reason": route_reason,
                "fish_hybrid_attempted": route_mode == "hybrid",
                "fish_hybrid_fallback_used": False,
                "fish_delivery_evidence_required": bool(
                    require_delivery_evidence
                ),
                "generation_provenance": self.generation_provenance(
                    fish_voice,
                    source="generation",
                ),
            }
        )
        self._record_generation_metadata(output_path, metadata)
        print(
            "Fish S2.1 auto-selection: "
            + json.dumps(
                {
                    "speaker": speaker,
                    "route_mode": route_mode,
                    "route_reason": route_reason,
                    "style": result.style,
                    "selected_prompt": result.selected.prompt_key,
                    "candidate_count": len(result.candidates),
                    "word_error_rate": result.selected.word_error_rate,
                    "identity_score": result.selected.identity_score,
                    "delivery_score": result.selected.delivery_score,
                    "instruction_delivery_score": getattr(
                        result.selected,
                        "instruction_delivery_score",
                        None,
                    ),
                },
                sort_keys=True,
            )
        )
        return result if return_result else True

    def _record_fish_hybrid_fallback(
        self,
        *,
        output_path,
        voice_data,
        route,
        route_reason,
        error,
    ):
        self._record_generation_metadata(
            output_path,
            {
                "fish_hybrid_attempted": True,
                "fish_hybrid_fallback_used": True,
                "fish_hybrid_style_route": route.style,
                "fish_hybrid_route_reason": route_reason,
                "fish_hybrid_fallback_error_code": getattr(error, "code", None),
                "fish_hybrid_fallback_reason": str(error),
                "generation_provenance": self.generation_provenance(
                    voice_data,
                    source="generation",
                ),
            },
        )

    @staticmethod
    def _concat_audio(wav):
        """Concatenate audio array(s) into a single numpy array."""
        if isinstance(wav, list):
            return np.concatenate(wav) if len(wav) > 1 else wav[0]
        return wav

    @staticmethod
    def _clear_gpu_cache():
        """Free GPU memory: garbage-collect Python objects, then clear CUDA cache."""
        import gc
        gc.collect()
        import torch
        torch.cuda.empty_cache()

    @staticmethod
    def _reset_compile_cache():
        """Reset torch.compile dynamo state to prevent guard accumulation.

        torch.compile(dynamic=True) accumulates shape guards across calls.
        With varying batch sizes and sequence lengths, the guard list grows
        and CPU-side guard evaluation becomes a bottleneck, causing
        progressive throughput degradation.  Resetting clears all in-memory
        guards; the next call pays a one-time recompilation cost (fast due
        to inductor disk cache) but prevents the slowdown from compounding.

        Only applied on ROCm (AMD GPUs). On NVIDIA, max-autotune mode
        re-benchmarks all kernel variants after each reset, and the
        benchmarking cost scales with tensor size — causing worse slowdown
        than the guard accumulation it prevents.
        """
        import torch
        if not (hasattr(torch.version, "hip") and torch.version.hip):
            return  # skip on NVIDIA/CPU — recompilation cost outweighs benefit
        torch._dynamo.reset()

    def _estimate_max_batch_size(self, model, clone_prompt_tokens=0,
                                ref_text_chars=0, max_text_chars=0,
                                max_new_tokens=2048):
        """Estimate how many sequences fit in free VRAM based on KV cache math.

        Uses the talker's architecture (num_layers, num_kv_heads, head_dim) to
        calculate KV cache bytes per token, then estimates total tokens per
        sequence from clone prompt size + text length + max generation length.

        Returns max batch size (>= 1).  Falls back to a large default on CPU
        or if the model config is inaccessible.
        """
        import torch
        if not torch.cuda.is_available():
            return 9999

        try:
            config = model.model.talker.config
            num_layers = config.num_hidden_layers
            num_kv_heads = config.num_key_value_heads
            head_dim = config.hidden_size // config.num_attention_heads
        except AttributeError:
            return 9999  # can't read config, skip estimation

        dtype_bytes = 2  # bf16
        kv_per_token = num_layers * 2 * num_kv_heads * head_dim * dtype_bytes

        # Total tokens per sequence (worst case: padded to longest + full generation)
        overhead = 10  # role tokens + prefix + special tokens
        ref_text_tokens = ref_text_chars // 3 if ref_text_chars else 0
        text_tokens = max_text_chars // 3 if max_text_chars else 0
        total_tokens = overhead + clone_prompt_tokens + ref_text_tokens + text_tokens + max_new_tokens

        # Overhead factor covers prefill activations, codec, allocator fragmentation
        OVERHEAD_FACTOR = 2.0
        mem_per_seq = total_tokens * kv_per_token * OVERHEAD_FACTOR

        # Available = driver-level free + PyTorch reserved-but-unallocated
        free_driver, _ = torch.cuda.mem_get_info()
        reserved_unused = torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
        free_total = free_driver + reserved_unused

        budget = int(free_total * 0.8)
        max_batch = max(1, budget // mem_per_seq)

        print(f"VRAM estimate: {free_total / 1e9:.1f}GB free, "
              f"{total_tokens} tok/seq ({clone_prompt_tokens} prompt + "
              f"{ref_text_tokens + text_tokens} text + {max_new_tokens} gen), "
              f"{mem_per_seq / 1e6:.0f}MB/seq -> max_batch={max_batch}")

        return max_batch

    def _build_sub_batches(self, texts, max_items=None):
        """Split sorted-by-length texts into sub-batches.

        Splits on three criteria (checked in order):
        1. VRAM item limit: when max_items is set (from _estimate_max_batch_size)
        2. Length ratio: when longest/shortest > sub_batch_ratio
        3. Minimum size: ratio splits only happen after sub_batch_min_size items

        Returns list of (start, end) index tuples.
        """
        if not self._sub_batch_enabled or len(texts) <= 1:
            return [(0, len(texts))]

        # Manual cap overrides VRAM estimate when set (take the stricter of the two)
        if self._sub_batch_max_items > 0:
            max_items = min(max_items, self._sub_batch_max_items) if max_items else self._sub_batch_max_items

        sub_batches = []
        batch_start = 0

        for i in range(1, len(texts)):
            shortest = max(len(texts[batch_start]), 1)
            should_split = False

            # VRAM-estimated item limit (highest priority — based on actual
            # free GPU memory and per-sequence KV cache cost)
            if max_items is not None and (i - batch_start) >= max_items:
                should_split = True
            # Ratio split: large length disparity wastes padding —
            # only split after min_size items to preserve parallelism
            elif (i - batch_start) >= self._sub_batch_min_size:
                if len(texts[i]) > self._sub_batch_ratio * shortest:
                    should_split = True

            if should_split:
                sub_batches.append((batch_start, i))
                batch_start = i

        sub_batches.append((batch_start, len(texts)))
        return sub_batches

    # ── Lazy initialization ──────────────────────────────────────

    def _warmup_model(self, model):
        """Run a short warmup generation to pre-tune MIOpen/GPU solvers.

        First generation after model load is ~2x slower due to MIOpen autotuning.
        This warmup pays that cost upfront so real generations run at full speed.
        """
        import time
        t0 = time.time()
        try:
            model.generate_custom_voice(
                text="The ancient library stood at the crossroads of two forgotten paths, its weathered stone walls covered in ivy that had been growing for centuries.",
                language=self._language,
                speaker="serena",
                instruct="neutral",
                non_streaming_mode=True,
                max_new_tokens=2048,
            )
            print(f"Warmup done in {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"Warmup failed (non-fatal): {e}")

    def _resolve_device(self):
        """Resolve 'auto' device to the best available."""
        if self._device != "auto":
            return self._device

        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"


    def _enable_rocm_optimizations(self):
        """Apply ROCm-specific optimizations. No-op on NVIDIA/CPU.

        1. FLASH_ATTENTION_TRITON_AMD_ENABLE: Lets qwen_tts whisper encoder
           use native flash attention via Triton AMD backend.
        2. MIOPEN_FIND_MODE=2: Forces MIOpen to use fast-find instead of
           exhaustive search, avoiding workspace allocation failures that
           cause fallback to slow GEMM algorithms.
        3. MIOPEN_LOG_LEVEL=4: Suppress noisy MIOpen workspace warnings.
        4. triton_key shim: Bridges pytorch-triton-rocm's get_cache_key()
           to the triton_key() that PyTorch's inductor expects.
        """
        try:
            import torch
            if not (hasattr(torch.version, "hip") and torch.version.hip):
                return  # not ROCm
        except ImportError:
            return

        # MIOpen: use fast-find to avoid workspace allocation failures
        os.environ.setdefault("MIOPEN_FIND_MODE", "2")
        # Suppress MIOpen workspace warnings
        os.environ.setdefault("MIOPEN_LOG_LEVEL", "4")

        # Flash attention via Triton AMD backend
        os.environ.setdefault("FLASH_ATTENTION_TRITON_AMD_ENABLE", "TRUE")

        # Fix triton_key compatibility for torch.compile on ROCm
        try:
            from triton.compiler import compiler as triton_compiler
            if not hasattr(triton_compiler, "triton_key"):
                import triton
                triton_compiler.triton_key = lambda: f"pytorch-triton-rocm-{triton.__version__}"
        except ImportError:
            pass

        # Correct under-reported GPU properties on consumer RDNA2/3.
        # ROCm reports half the CU count and warp size 32 instead of 64,
        # causing PyTorch to under-schedule work on RX 6000/7000 GPUs.
        self._patch_rdna_device_properties(torch)


    @staticmethod
    def _patch_rdna_device_properties(torch):
        """Monkey-patch torch.cuda.get_device_properties to report correct
        CU count and wavefront size for consumer RDNA2/3 GPUs.

        ROCm exposes these GPUs with half CU count and warp_size=32
        (matching the CDNA/MI convention). The actual hardware has the
        full CU count and native wavefront64. Under-reporting causes
        PyTorch to generate smaller kernel launches.

        Based on AMD-GPU-BOOST (github.com/Painter3000/AMD-GPU-BOOST).
        """
        if hasattr(torch.cuda, '_rdna_props_patched'):
            return

        # Known RDNA GPU corrections: {name_substring: (true_CUs, true_warp)}
        _rdna_corrections = {
            "7900 XTX": (96, 64),
            "7900 XT":  (84, 64),
            "7900 GRE": (80, 64),
            "7800 XT":  (60, 64),
            "7700 XT":  (54, 64),
            "7600":     (32, 64),
            "6950 XT":  (80, 64),
            "6900 XT":  (80, 64),
            "6800 XT":  (72, 64),
            "6800":     (60, 64),
            "6750 XT":  (40, 64),
            "6700 XT":  (40, 64),
            "6700":     (36, 64),
            "6650 XT":  (32, 64),
            "6600 XT":  (32, 64),
            "6600":     (28, 64),
        }

        original_fn = torch.cuda.get_device_properties
        _cache = {}

        def _patched_get_device_properties(device=None):
            if device is None:
                device = torch.cuda.current_device()
            key = int(device) if not isinstance(device, int) else device

            if key in _cache:
                return _cache[key]

            props = original_fn(device)

            # Find matching correction
            correction = None
            for substr, vals in _rdna_corrections.items():
                if substr in props.name:
                    correction = vals
                    break

            if correction:
                from types import SimpleNamespace
                true_cus, true_warp = correction
                patched = SimpleNamespace()
                for attr in dir(props):
                    if not attr.startswith('_'):
                        try:
                            setattr(patched, attr, getattr(props, attr))
                        except (AttributeError, RuntimeError):
                            pass
                patched.multi_processor_count = true_cus
                patched.warp_size = true_warp
                old_threads = props.multi_processor_count * props.warp_size
                new_threads = true_cus * true_warp
                print(f"  [RDNA fix] {props.name}: CUs {props.multi_processor_count}->{true_cus}, "
                      f"warp {props.warp_size}->{true_warp}, "
                      f"threads {old_threads}->{new_threads}")
                _cache[key] = patched
                return patched

            _cache[key] = props
            return props

        torch.cuda.get_device_properties = _patched_get_device_properties
        torch.cuda._rdna_props_patched = True

    def _compile_codec(self, model):
        """Apply torch.compile to the audio codec for faster decoding.

        The codec decoder has 136 attention modules and many small ops that
        benefit enormously from compilation.  Profiling shows the codec is
        47% of single-gen time and 85% of batch time uncompiled.  With
        torch.compile (dynamic=True, max-autotune), batch throughput
        improves from ~1.3x to ~4.3x real-time and single generation
        drops from ~14s to ~9s.

        max-autotune mode benchmarks GPU kernels to pick the fastest and
        handles varying batch sizes gracefully (unlike reduce-overhead
        which uses CUDA graphs that break on shape changes).
        """
        import torch
        try:
            codec = model.model.speech_tokenizer.model
            model.model.speech_tokenizer.model = torch.compile(
                codec, mode="max-autotune", dynamic=True,
            )
            print("Codec compiled with torch.compile (dynamic=True).")
        except Exception as e:
            print(f"Codec compilation skipped (non-fatal): {e}")

    @staticmethod
    def _resolve_local_model_path(model_id):
        """Resolve one complete local snapshot before model initialization."""
        candidate = Path(str(model_id)).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        if is_registered_model(model_id):
            return str(resolve_model_path(model_id, local_files_only=True))
        return str(
            snapshot_download_with_public_fallback(
                model_id,
                local_files_only=True,
            )
        )

    @staticmethod
    def _load_model(model_cls, model_id, load_kwargs):
        """Load from the resolved snapshot without a second implicit Hub request."""
        local_path = TTSEngine._resolve_local_model_path(model_id)
        print(f"  Loading from local snapshot: {local_path}")
        options = dict(load_kwargs)
        options["local_files_only"] = True
        return model_cls.from_pretrained(local_path, **options)

    @staticmethod
    def _synchronize_torch_device() -> None:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            return
        mps = getattr(torch, "mps", None)
        synchronize = getattr(mps, "synchronize", None)
        if callable(synchronize):
            synchronize()

    def _release_local_model(self, attribute: str) -> bool:
        with self._model_lock:
            released = getattr(self, attribute, None) is not None
            setattr(self, attribute, None)
            if attribute == "_local_lora_model":
                self._lora_adapter_path = None
                self._lora_prompt_cache.clear()
        self._clear_gpu_cache()
        return released

    def _load_local_resident(
        self,
        *,
        slot_id: str,
        component_id: str,
        engine_id: str,
        attribute: str,
        device: str,
        load_callback,
        adapter_revision: str | None = None,
    ):
        return self._model_residency.load_resident(
            slot_id=slot_id,
            component_id=component_id,
            load_callback=lambda: self._model_residency.run_with_oom_retry(
                component_id,
                load_callback,
                lambda: False,
            ),
            install_callback=lambda model: setattr(self, attribute, model),
            release_callback=lambda: self._release_local_model(attribute),
            synchronize_callback=self._synchronize_torch_device,
            engine_id=engine_id,
            device=device,
            adapter_revision=adapter_revision,
        )

    def _init_local_custom(self):
        """Load Qwen3-TTS CustomVoice model on demand."""
        if self._local_custom_model is not None:
            return self._local_custom_model

        with self._model_lock:
            if self._local_custom_model is not None:
                return self._local_custom_model

            self._enable_rocm_optimizations()

            import torch
            from qwen_tts import Qwen3TTSModel

            device = self._resolve_device()
            dtype = torch.bfloat16 if "cuda" in device else torch.float32

            print(f"Loading Qwen3-TTS CustomVoice model on {device} ({dtype})...")
            load_kwargs = {"dtype": dtype}
            if device != "cpu":
                load_kwargs["device_map"] = device
            self._load_local_resident(
                slot_id="pytorch:custom",
                component_id="pytorch_qwen_custom_voice",
                engine_id="qwen3_custom",
                attribute="_local_custom_model",
                device=device,
                load_callback=lambda: self._load_model(
                    Qwen3TTSModel,
                    PYTORCH_CUSTOM_MODEL,
                    load_kwargs,
                ),
            )
            if self._compile_codec_enabled:
                self._compile_codec(self._local_custom_model)
            print("CustomVoice model loaded.")
            return self._local_custom_model

    def _init_local_clone(self):
        """Load Qwen3-TTS Base model (for voice cloning) on demand."""
        if self._local_clone_model is not None:
            return self._local_clone_model

        with self._model_lock:
            if self._local_clone_model is not None:
                return self._local_clone_model

            self._enable_rocm_optimizations()

            import torch
            from qwen_tts import Qwen3TTSModel

            device = self._resolve_device()
            dtype = torch.bfloat16 if "cuda" in device else torch.float32

            print(f"Loading Qwen3-TTS Base model (voice cloning) on {device} ({dtype})...")
            load_kwargs = {"dtype": dtype}
            if device != "cpu":
                load_kwargs["device_map"] = device
            self._load_local_resident(
                slot_id="pytorch:clone",
                component_id="pytorch_qwen_base",
                engine_id="qwen3_base",
                attribute="_local_clone_model",
                device=device,
                load_callback=lambda: self._load_model(
                    Qwen3TTSModel,
                    PYTORCH_CLONE_MODEL,
                    load_kwargs,
                ),
            )
            if self._compile_codec_enabled:
                self._compile_codec(self._local_clone_model)
            print("Base model (voice cloning) loaded.")
            return self._local_clone_model

    def _init_local_design(self):
        """Load Qwen3-TTS VoiceDesign model on demand."""
        if self._local_design_model is not None:
            return self._local_design_model

        with self._model_lock:
            if self._local_design_model is not None:
                return self._local_design_model

            self._enable_rocm_optimizations()

            import torch
            from qwen_tts import Qwen3TTSModel

            device = self._resolve_device()
            dtype = torch.bfloat16 if "cuda" in device else torch.float32

            print(f"Loading Qwen3-TTS VoiceDesign model on {device} ({dtype})...")
            load_kwargs = {"dtype": dtype}
            if device != "cpu":
                load_kwargs["device_map"] = device
            self._load_local_resident(
                slot_id="pytorch:design",
                component_id="pytorch_qwen_voice_design",
                engine_id="qwen3_voice_design",
                attribute="_local_design_model",
                device=device,
                load_callback=lambda: self._load_model(
                    Qwen3TTSModel,
                    PYTORCH_DESIGN_MODEL,
                    load_kwargs,
                ),
            )
            if self._compile_codec_enabled:
                self._compile_codec(self._local_design_model)
            print("VoiceDesign model loaded.")
            return self._local_design_model

    def _init_local_lora(self, adapter_path):
        """Load Qwen3-TTS Base model with a LoRA adapter on demand.

        Caches the model; if a different adapter is requested the old one
        is unloaded first to free VRAM.
        """
        if self._local_lora_model is not None and self._lora_adapter_path == adapter_path:
            return self._local_lora_model

        with self._model_lock:
            if self._local_lora_model is not None and self._lora_adapter_path == adapter_path:
                return self._local_lora_model

            self._enable_rocm_optimizations()

            import torch
            from qwen_tts import Qwen3TTSModel
            from peft import PeftModel

            device = self._resolve_device()
            dtype = torch.bfloat16 if "cuda" in device else torch.float32

            print(f"Loading Qwen3-TTS Base model + LoRA adapter on {device} ({dtype})...")
            load_kwargs = {"dtype": dtype}
            if device != "cpu":
                load_kwargs["device_map"] = device

            def load_lora_model():
                model = self._load_model(
                    Qwen3TTSModel,
                    PYTORCH_CLONE_MODEL,
                    load_kwargs,
                )
                model.model.talker = PeftModel.from_pretrained(
                    model.model.talker,
                    adapter_path,
                )
                model.model.talker.eval()
                if self._compile_codec_enabled:
                    self._compile_codec(model)
                return model

            self._load_local_resident(
                slot_id="pytorch:lora",
                component_id="pytorch_qwen_base",
                engine_id="qwen3_lora",
                attribute="_local_lora_model",
                device=device,
                load_callback=load_lora_model,
                adapter_revision=hashlib.sha256(
                    str(Path(adapter_path).expanduser().resolve()).encode("utf-8")
                ).hexdigest(),
            )
            self._lora_adapter_path = adapter_path
            print(f"LoRA adapter loaded from {adapter_path}")
            return self._local_lora_model

    def _init_mlx(self):
        """Load the persistent Apple-Silicon MLX backend on demand."""
        if self._mlx_backend is None:
            from mlx_backend import MLXBackend
            self._mlx_backend = MLXBackend(
                language=self._language,
                model_residency=self._model_residency,
            )
        return self._mlx_backend

    def _init_responsive_voice_backend(self):
        """Load model-specific recurring-voice adapters on demand."""
        if self._responsive_voice_backend is None:
            self._responsive_voice_backend = ResponsiveVoiceBackend(
                model_residency=self._model_residency,
            )
            atexit.register(self._responsive_voice_backend.close)
        return self._responsive_voice_backend

    def consume_responsive_generation_receipt(self):
        receipt = getattr(self._responsive_generation_state, "receipt", None)
        self._responsive_generation_state.receipt = None
        return dict(receipt) if isinstance(receipt, dict) else None

    def _init_external(self):
        """Create Gradio client on demand."""
        if self._gradio_client is not None:
            return self._gradio_client

        from gradio_client import Client

        print(f"Connecting to TTS server at {self._url}...")
        self._gradio_client = Client(self._url)
        print("Connected to external TTS server.")
        return self._gradio_client

    # ── Clone prompt cache (local mode) ──────────────────────────

    def _get_clone_prompt(self, speaker, voice_config, *, model=None):
        """Get or create a cached voice clone prompt for a speaker."""
        voice_data = voice_config.get(speaker, {})
        ref_audio_path = voice_data.get("ref_audio")
        ref_text = voice_data.get("ref_text")

        if not ref_audio_path or not ref_text:
            raise ValueError(f"Clone voice for '{speaker}' missing ref_audio or ref_text")
        # Resolve relative paths against project root (parent of app/)
        if not os.path.isabs(ref_audio_path):
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ref_audio_path = os.path.join(root_dir, ref_audio_path)
        if not os.path.exists(ref_audio_path):
            raise FileNotFoundError(f"Reference audio not found for '{speaker}': {ref_audio_path}")

        audio_sha256 = voice_data.get("ref_audio_sha256")
        if not audio_sha256:
            audio_sha256 = hashlib.sha256(
                Path(ref_audio_path).read_bytes()
            ).hexdigest()
        cache_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "ref_audio": ref_audio_path,
                    "ref_audio_sha256": audio_sha256,
                    "ref_text": ref_text,
                    "production_voice_evidence_fingerprint": voice_data.get(
                        "production_voice_evidence_fingerprint"
                    ),
                    "production_voice_prompt_fingerprint": voice_data.get(
                        "production_voice_prompt_fingerprint"
                    ),
                    "production_voice_preprocessing_fingerprint": voice_data.get(
                        "production_voice_preprocessing_fingerprint"
                    ),
                    "production_voice_pronunciation_fingerprint": voice_data.get(
                        "production_voice_pronunciation_fingerprint"
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        # Check cache — invalidate when any prompt dependency changes.
        if speaker in self._clone_prompt_cache:
            cached_fingerprint, cached_prompt = self._clone_prompt_cache[speaker]
            if cached_fingerprint == cache_fingerprint:
                return cached_prompt
            print(f"Voice prompt changed for '{speaker}', rebuilding clone prompt...")

        if model is None:
            with self._pytorch_model_job(
                "pytorch_qwen_base",
                self._init_local_clone,
                label="PyTorch Base clone prompt",
            ) as resident_model:
                return self._get_clone_prompt(
                    speaker,
                    voice_config,
                    model=resident_model,
                )

        # Load reference audio as numpy array
        audio_array, sample_rate = sf.read(ref_audio_path)
        # Ensure mono
        if audio_array.ndim > 1:
            audio_array = audio_array.mean(axis=1)

        print(f"Creating clone prompt for '{speaker}'...")
        prompt = model.create_voice_clone_prompt(
            ref_audio=(audio_array, sample_rate),
            ref_text=ref_text,
        )
        self._clone_prompt_cache[speaker] = (cache_fingerprint, prompt)
        print(f"Clone prompt cached for '{speaker}'.")
        return prompt

    # ── Core generation methods ──────────────────────────────────

    @staticmethod
    def _custom_voice_instruction(voice_data, instruct_text=""):
        """Keep the persistent Voice identity while applying the line direction."""
        persistent = str(
            (voice_data or {}).get("character_style")
            or (voice_data or {}).get("default_style")
            or (voice_data or {}).get("description")
            or ""
        ).strip()
        line_direction = str(instruct_text or "").strip()
        return " ".join(
            part for part in (persistent, line_direction) if part
        ) or "neutral"

    def generate_custom_voice(self, text, instruct_text, speaker, voice_config, output_path):
        """Generate audio using CustomVoice model. Returns True on success."""
        if self._use_mlx:
            voice_data = voice_config.get(speaker, {})
            voice = voice_data.get("voice", "Ryan")
            instruct = self._custom_voice_instruction(voice_data, instruct_text)
            backend = self._init_mlx()
            success = backend.generate_custom(
                text=text,
                instruct=instruct,
                voice=voice,
                output_path=output_path,
            )
            if success:
                metadata_reader = getattr(
                    backend,
                    "pop_generation_metadata",
                    None,
                )
                if callable(metadata_reader):
                    metadata = metadata_reader(output_path)
                    if metadata:
                        self._record_generation_metadata(output_path, metadata)
            return success
        if self._mode == "local":
            return self._local_generate_custom(text, instruct_text, speaker, voice_config, output_path)
        else:
            return self._external_generate_custom(text, instruct_text, speaker, voice_config, output_path)

    def supports_generation_seed(
        self,
        voice_data,
        *,
        batch=False,
        shared_seed=False,
    ):
        voice_type = str((voice_data or {}).get("type") or "custom")
        if voice_type == "community_qvoice":
            return self._use_mlx
        if voice_type == "clone":
            clone_backend = (voice_data or {}).get("clone_backend")
            if (voice_data or {}).get("fish_hybrid_enabled"):
                return False
            if clone_backend == "fish_s21_cloud":
                return False
            if clone_backend in {
                INSTRUCTION_CONTROLLED_BACKEND,
                ROUTED_CLONE_BACKEND,
            }:
                return True
            if self._use_mlx:
                return False
            if batch and self._mode == "local":
                return False
            return True
        if voice_type == "custom":
            if self._use_mlx:
                return False
            if batch and self._mode == "local":
                return bool(shared_seed)
            return True
        return voice_type in {"design", "lora", "builtin_lora"}

    @staticmethod
    def _voice_config_with_generation_seed(
        voice_config,
        speaker,
        seed,
    ):
        try:
            resolved_seed = int(seed)
        except (TypeError, ValueError):
            resolved_seed = -1
        if resolved_seed < 0:
            return voice_config
        effective = dict(voice_config)
        voice_data = dict(effective.get(speaker, {}))
        voice_data["seed"] = resolved_seed
        effective[speaker] = voice_data
        return effective

    def _resolve_reference_bank_voice_config(
        self,
        speaker,
        voice_config,
        instruct_text="",
        project_root=None,
    ):
        voice_data = voice_config.get(speaker, {})
        if not isinstance(voice_data, dict):
            voice_data = {}
        root_dir = os.path.abspath(
            project_root
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        effective_voice = dict(voice_data)
        selected = None
        production_prompt = None
        evidence_path = voice_data.get("production_voice_evidence_path")
        if evidence_path:
            if (
                voice_data.get("clone_backend")
                != INSTRUCTION_CONTROLLED_BACKEND
            ):
                raise ValueError(
                    "Production Voice evidence currently requires the Qwen "
                    "instruction-controlled clone backend."
                )
            production_prompt = resolve_production_voice_prompt(
                evidence_set_path=evidence_path,
                project_root=root_dir,
                instruction=instruct_text or "",
                backend=str(
                    voice_data.get("clone_backend")
                    or INSTRUCTION_CONTROLLED_BACKEND
                ),
                language=str(
                    voice_data.get("production_voice_language")
                    or self._language
                    or "English"
                ),
                persistent_style=str(
                    voice_data.get("character_style")
                    or voice_data.get("default_style")
                    or ""
                ),
                pronunciation_resolution=(
                    voice_data.get("pronunciation_resolution")
                    if isinstance(
                        voice_data.get("pronunciation_resolution"),
                        dict,
                    )
                    else None
                ),
                expected_evidence_fingerprint=(
                    voice_data.get("production_voice_evidence_fingerprint")
                ),
            )
            effective_voice.update(
                {
                    "ref_audio": production_prompt["ref_audio"],
                    "ref_audio_sha256": production_prompt[
                        "ref_audio_sha256"
                    ],
                    "ref_text": production_prompt["ref_text"],
                    "selected_production_voice_sample_id": production_prompt[
                        "sample_id"
                    ],
                    "production_voice_evidence_fingerprint": production_prompt[
                        "evidence_set_fingerprint"
                    ],
                    "production_voice_prompt_fingerprint": production_prompt[
                        "prompt_fingerprint"
                    ],
                    "production_voice_dependency_fingerprint": production_prompt[
                        "dependency_fingerprint"
                    ],
                    "production_voice_preprocessing_fingerprint": production_prompt[
                        "preprocessing_fingerprint"
                    ],
                    "production_voice_pronunciation_fingerprint": production_prompt[
                        "pronunciation_fingerprint"
                    ],
                    "production_voice_prompt_instruction": production_prompt[
                        "instruction"
                    ],
                }
            )
            print(
                "Production Voice evidence: "
                f"speaker='{speaker}', sample='{production_prompt['sample_id']}', "
                f"reason='{production_prompt['selection_reason']}'"
            )
        bank_path = voice_data.get("reference_bank_path")
        if bank_path and production_prompt is None:
            if not os.path.isabs(bank_path):
                bank_path = os.path.join(root_dir, bank_path)
            from expressive_reference_bank import select_reference_for_instruction
            selected = select_reference_for_instruction(
                bank_path=bank_path,
                instruction=instruct_text or "",
                project_root=root_dir,
            )
            effective_voice.update(
                {
                    "ref_audio": selected["ref_audio"],
                    "ref_text": selected["ref_text"],
                    "selected_reference_style": selected["style_key"],
                    "selected_reference_id": selected["reference_id"],
                }
            )
            print(
                "Expressive reference bank: "
                f"speaker='{speaker}', style='{selected['style_key']}', "
                f"reason='{selected['mapping_reason']}'"
            )

        override = None
        if production_prompt is None:
            override = resolve_experimental_prompt_override(
                voice_data=voice_data,
                instruction=instruct_text or "",
                project_root=root_dir,
            )
        if override is not None:
            effective_voice.update(
                {
                    "ref_audio": override["ref_audio"],
                    "ref_text": override["ref_text"],
                    "selected_prompt_route": override["route_key"],
                    "selected_prompt_role": override["prompt_role"],
                    "selected_prompt_evidence_round_id": override[
                        "evidence_round_id"
                    ],
                    "selected_prompt_routing_fingerprint": override[
                        "routing_fingerprint"
                    ],
                }
            )
            print(
                "Experimental prompt route: "
                f"speaker='{speaker}', route='{override['route_key']}', "
                f"role='{override['prompt_role']}'"
            )

        if effective_voice == voice_data:
            return voice_config, None
        effective_config = dict(voice_config)
        effective_config[speaker] = effective_voice
        return effective_config, {
            "production_voice": production_prompt,
            "reference_bank": selected,
            "experimental_prompt": override,
        }

    def generate_clone_voice(
        self,
        text,
        speaker,
        voice_config,
        output_path,
        instruct_text="",
        fish_render_plan=None,
        fish_instruction=None,
    ):
        """Generate audio using voice cloning. Returns True on success."""
        self._responsive_generation_state.receipt = None
        project_root = os.path.dirname(os.path.abspath(output_path))
        effective_config, selection = self._resolve_reference_bank_voice_config(
            speaker,
            voice_config,
            instruct_text,
            project_root=project_root,
        )
        production_selection = (
            selection.get("production_voice")
            if isinstance(selection, dict)
            else None
        )
        if isinstance(production_selection, dict):
            self._responsive_generation_state.receipt = {
                "production_voice_evidence_used": True,
                "production_voice_sample_id": production_selection["sample_id"],
                "production_voice_evidence_fingerprint": production_selection[
                    "evidence_set_fingerprint"
                ],
                "production_voice_prompt_fingerprint": production_selection[
                    "prompt_fingerprint"
                ],
                "production_voice_dependency_fingerprint": production_selection[
                    "dependency_fingerprint"
                ],
                "production_voice_selection_reason": production_selection[
                    "selection_reason"
                ],
                "production_voice_advisory_identity_used": False,
            }
        clean_instruct_text = strip_prompt_route_tag(instruct_text)
        clean_fish_instruction = strip_prompt_route_tag(
            fish_instruction if fish_instruction is not None else instruct_text
        )
        source_voice_data = voice_config.get(speaker, {})
        selected_effect_chain = None
        source_clone_backend = str(
            source_voice_data.get("clone_backend") or "qwen3_base"
        )
        if source_clone_backend == ROUTED_CLONE_BACKEND:
            route = resolve_recurring_voice_route(
                voice_data=source_voice_data,
                instruction=instruct_text or "",
                project_root=project_root,
                verify_audio=True,
            )
            if route is None:
                raise ValueError(
                    f"Responsive recurring Voice for '{speaker}' has no active route."
                )
            try:
                configured_seed = int(source_voice_data.get("seed", -1))
            except (TypeError, ValueError):
                configured_seed = -1
            if configured_seed < 0:
                configured_seed = 130363
            selected_backend = str(route["backend"])
            selected_effect_chain = route.get("effect_chain")
            backend_error = None
            if selected_backend == INSTRUCTION_CONTROLLED_BACKEND:
                routed_voice = dict(source_voice_data)
                routed_voice.update(
                    {
                        "clone_backend": selected_backend,
                        "ref_audio": (
                            route.get("performance_audio_path")
                            or route["identity_audio_path"]
                        ),
                        "ref_text": (
                            route.get("performance_text")
                            or route["identity_text"]
                        ),
                    }
                )
                effective_config = dict(effective_config)
                effective_config[speaker] = routed_voice
                self._responsive_generation_state.receipt = {
                    "responsive_voice_used_backend": selected_backend,
                    "responsive_voice_fallback_used": False,
                    "responsive_voice_backend_error": None,
                    "responsive_voice_specialist_attempt_count": 1,
                    "responsive_voice_repair_strategy": "reviewed_qwen_route",
                    "responsive_voice_text_verification": None,
                    "responsive_voice_effect_chain": selected_effect_chain,
                    "responsive_voice_effect_receipt": None,
                    "responsive_voice_approval_tier": route.get("approval_tier"),
                }
            else:
                responsive = self._init_responsive_voice_backend()
                if responsive.backend_available(selected_backend):
                    try:
                        print(
                            "Responsive recurring Voice route: "
                            + json.dumps(
                                {
                                    "speaker": speaker,
                                    "route": route["route_key"],
                                    "backend": selected_backend,
                                    "mapping_reason": route["mapping_reason"],
                                    "evidence_round_id": route["evidence_round_id"],
                                    "seed": configured_seed,
                                },
                                sort_keys=True,
                            )
                        )
                        specialist_receipt = responsive.generate(
                            route=route,
                            text=text,
                            output_path=output_path,
                            seed=configured_seed,
                        )
                        effect_receipt = apply_voice_effect_chain(
                            output_path,
                            selected_effect_chain,
                        )
                        actual_backend = (
                            specialist_receipt.get("used_backend", selected_backend)
                            if isinstance(specialist_receipt, dict)
                            else selected_backend
                        )
                        internal_fallback_used = bool(
                            specialist_receipt.get("fallback_used", False)
                            if isinstance(specialist_receipt, dict)
                            else False
                        )
                        internal_backend_error = (
                            specialist_receipt.get("primary_backend_error")
                            if isinstance(specialist_receipt, dict)
                            else None
                        )
                        self._responsive_generation_state.receipt = {
                            "responsive_voice_used_backend": actual_backend,
                            "responsive_voice_fallback_used": internal_fallback_used,
                            "responsive_voice_backend_error": internal_backend_error,
                            "responsive_voice_specialist_attempt_count": (
                                specialist_receipt.get("attempt_count")
                                if isinstance(specialist_receipt, dict)
                                else 1
                            ),
                            "responsive_voice_repair_strategy": (
                                specialist_receipt.get("repair_strategy")
                                if isinstance(specialist_receipt, dict)
                                else "direct"
                            ),
                            "responsive_voice_text_verification": (
                                specialist_receipt.get("text_verification")
                                if isinstance(specialist_receipt, dict)
                                else None
                            ),
                            "responsive_voice_effect_chain": selected_effect_chain,
                            "responsive_voice_effect_receipt": effect_receipt,
                            "responsive_voice_approval_tier": route.get("approval_tier"),
                        }
                        return True
                    except (
                        ResponsiveBackendUnavailable,
                        ResponsiveVoiceBackendError,
                    ) as exc:
                        backend_error = str(exc)
                        print(
                            f"Responsive backend {selected_backend!r} failed for "
                            f"'{speaker}'; using {route['fallback_backend']}: {exc}"
                        )
                else:
                    backend_error = (
                        f"Responsive backend {selected_backend!r} is unavailable."
                    )
                    print(
                        f"Responsive backend {selected_backend!r} is unavailable for "
                        f"'{speaker}'; using {route['fallback_backend']}."
                    )
                self._responsive_generation_state.receipt = {
                    "responsive_voice_used_backend": route["fallback_backend"],
                    "responsive_voice_fallback_used": True,
                    "responsive_voice_backend_error": backend_error,
                    "responsive_voice_specialist_attempt_count": None,
                    "responsive_voice_repair_strategy": "qwen_fallback",
                    "responsive_voice_text_verification": None,
                    "responsive_voice_effect_chain": selected_effect_chain,
                    "responsive_voice_effect_receipt": None,
                    "responsive_voice_approval_tier": route.get("approval_tier"),
                }
                fallback_voice = dict(source_voice_data)
                fallback_voice.update(
                    {
                        "clone_backend": route["fallback_backend"],
                        "ref_audio": route["identity_audio_path"],
                        "ref_text": route["identity_text"],
                    }
                )
                effective_config = dict(effective_config)
                effective_config[speaker] = fallback_voice
        voice_data = effective_config.get(speaker, {})
        ref_audio = voice_data.get("ref_audio")
        ref_text = voice_data.get("ref_text")
        if not ref_audio or not ref_text:
            raise ValueError(
                f"Clone voice for '{speaker}' requires ref_audio and ref_text."
            )
        if not os.path.isabs(ref_audio):
            project_root = os.path.dirname(os.path.abspath(output_path))
            ref_audio = os.path.join(project_root, ref_audio)
        clone_backend = voice_data.get("clone_backend", "qwen3_base")
        approved_prompt_selected = bool(
            isinstance(selection, dict)
            and selection.get("experimental_prompt") is not None
        )
        hybrid = fish_hybrid_decision(
            voice_data=voice_data,
            text=text,
            instruction=clean_fish_instruction,
            approved_prompt_selected=approved_prompt_selected,
        )
        fish_only = clone_backend == "fish_s21_cloud"
        fallback_error = None
        if fish_only or hybrid.use_fish:
            route_mode = "exclusive" if fish_only else "hybrid"
            route_reason = "voice_backend" if fish_only else hybrid.reason
            try:
                return self._generate_with_fish(
                    text=text,
                    instruction=clean_fish_instruction,
                    speaker=speaker,
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    output_path=output_path,
                    voice_data=voice_data,
                    route_mode=route_mode,
                    route_reason=route_reason,
                    render_plan=fish_render_plan,
                )
            except Exception as exc:
                policy = normalized_fish_hybrid_policy(voice_data)
                if fish_only or not policy["fallback_to_local"]:
                    raise
                fallback_error = exc
                print(
                    "Fish hybrid fallback: "
                    + json.dumps(
                        {
                            "speaker": speaker,
                            "style": hybrid.route.style,
                            "route_reason": hybrid.reason,
                            "error_code": getattr(exc, "code", None),
                            "error": str(exc),
                        },
                        sort_keys=True,
                    )
                )

        def finish_local(success):
            if success:
                effect_receipt = apply_voice_effect_chain(
                    output_path,
                    selected_effect_chain,
                )
                responsive_receipt = getattr(
                    self._responsive_generation_state,
                    "receipt",
                    None,
                )
                if isinstance(responsive_receipt, dict):
                    responsive_receipt[
                        "responsive_voice_effect_receipt"
                    ] = effect_receipt
                    self._responsive_generation_state.receipt = responsive_receipt
            if success and fallback_error is not None:
                self._record_fish_hybrid_fallback(
                    output_path=output_path,
                    voice_data=voice_data,
                    route=hybrid.route,
                    route_reason=hybrid.reason,
                    error=fallback_error,
                )
            return success

        if self._use_mlx:
            if clone_backend == INSTRUCTION_CONTROLLED_BACKEND:
                instruction = str(
                    voice_data.get("production_voice_prompt_instruction")
                    or ""
                ).strip()
                if not instruction:
                    instruction_parts = [
                        str(clean_instruct_text or "").strip(),
                        str(
                            voice_data.get("character_style")
                            or voice_data.get("default_style")
                            or ""
                        ).strip(),
                    ]
                    instruction = " ".join(
                        part for part in instruction_parts if part
                    ) or "Natural, clear delivery."
                try:
                    configured_seed = int(voice_data.get("seed", -1))
                except (TypeError, ValueError):
                    configured_seed = -1
                print(
                    "Controlled clone route: "
                    + json.dumps(
                        {
                            "speaker": speaker,
                            "line_instruction_sha256": hashlib.sha256(
                                str(clean_instruct_text or "").encode("utf-8")
                            ).hexdigest()[:16],
                            "persistent_style_sha256": hashlib.sha256(
                                str(
                                    voice_data.get("character_style")
                                    or voice_data.get("default_style")
                                    or ""
                                ).encode("utf-8")
                            ).hexdigest()[:16],
                            "reference_text_sha256": hashlib.sha256(
                                str(ref_text).encode("utf-8")
                            ).hexdigest()[:16],
                            "seed": (
                                configured_seed
                                if configured_seed >= 0
                                else "random"
                            ),
                        },
                        sort_keys=True,
                    )
                )
                return finish_local(self._init_mlx().generate_instruction_controlled_clone(
                    text=text,
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    instruct=instruction,
                    output_path=output_path,
                    temperature=voice_data.get(
                        "instruction_clone_temperature",
                        0.75,
                    ),
                    top_k=voice_data.get("instruction_clone_top_k", 50),
                    top_p=voice_data.get("instruction_clone_top_p", 0.95),
                    repetition_penalty=voice_data.get(
                        "instruction_clone_repetition_penalty",
                        1.5,
                    ),
                    max_tokens=voice_data.get(
                        "instruction_clone_max_tokens",
                        2000,
                    ),
                    seed=configured_seed,
                    request_label=speaker,
                ))
            if clone_backend == LEGACY_CONTROLLED_BACKEND:
                raise ValueError(
                    "The legacy VoxCPM2 clone does not provide reliable per-line "
                    "delivery control. Re-preview this Voice with the Qwen "
                    "instruction-controlled clone before generating production audio."
                )
            if clone_backend != "qwen3_base":
                raise ValueError(
                    f"Unsupported clone backend for '{speaker}': {clone_backend!r}."
                )
            backend = self._init_mlx()
            success = finish_local(backend.generate_clone(
                text=text,
                ref_audio=ref_audio,
                ref_text=ref_text,
                output_path=output_path,
            ))
            if success:
                metadata_reader = getattr(
                    backend,
                    "pop_generation_metadata",
                    None,
                )
                if callable(metadata_reader):
                    metadata = metadata_reader(output_path)
                    if metadata:
                        self._record_generation_metadata(output_path, metadata)
            return success
        if self._mode == "local":
            return finish_local(self._local_generate_clone(
                text,
                speaker,
                effective_config,
                output_path,
            ))
        else:
            return finish_local(self._external_generate_clone(
                text,
                speaker,
                effective_config,
                output_path,
            ))

    def _generate_voice_unsegmented(
        self,
        text,
        instruct_text,
        speaker,
        voice_config,
        output_path,
        fish_render_plan=None,
        fish_instruction=None,
    ):
        """Generate audio using the appropriate method based on voice type config."""
        voice_data = voice_config.get(speaker)
        if not voice_data:
            print(f"Warning: No voice configuration for '{speaker}'. Skipping.")
            return False

        voice_type = voice_data.get("type", "custom")

        if voice_type == "sound_effect":
            raise ValueError(
                "Sound effect generation requires a configured non-speech backend."
            )
        if voice_type == "community_qvoice":
            return self.generate_community_qvoice(
                text,
                instruct_text,
                speaker,
                voice_data,
                output_path,
            )
        if voice_type == "clone":
            return self.generate_clone_voice(
                text,
                speaker,
                voice_config,
                output_path,
                instruct_text=instruct_text,
                fish_render_plan=fish_render_plan,
                fish_instruction=fish_instruction,
            )
        elif voice_type in ("lora", "builtin_lora"):
            return self.generate_lora_voice(text, instruct_text, voice_data, output_path)
        elif voice_type == "design":
            return self.generate_design_voice(text, instruct_text, voice_data, output_path)
        else:
            return self.generate_custom_voice(text, instruct_text, speaker, voice_config, output_path)

    @staticmethod
    def _read_segment_waveform(path):
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise SynthesisWindowError(
                "synthesis_segment_output_missing",
                f"Internal synthesis segment did not produce audio: {source.name}.",
            )
        try:
            audio, sample_rate = sf.read(
                source,
                dtype="float32",
                always_2d=True,
            )
        except Exception as exc:
            raise SynthesisWindowError(
                "synthesis_segment_output_invalid",
                f"Internal synthesis segment could not be decoded: {source.name}.",
            ) from exc
        waveform = np.mean(audio, axis=1, dtype=np.float32)
        return waveform, int(sample_rate)

    @staticmethod
    def _atomic_write_segment_join(output_path, audio, sample_rate):
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(
            f".{target.name}.joined-{secrets.token_hex(6)}.tmp.wav"
        )
        try:
            sf.write(
                temporary,
                np.asarray(audio, dtype=np.float32).reshape(-1),
                int(sample_rate),
                subtype="FLOAT",
            )
            info = sf.info(temporary)
            if (
                int(info.frames) != len(audio)
                or int(info.samplerate) != int(sample_rate)
                or int(info.channels) != 1
            ):
                raise SynthesisWindowError(
                    "synthesis_joined_file_validation_failed",
                    "The joined synthesis file did not preserve its exact sample contract.",
                )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _synthesis_backend_id(self, voice_data):
        return resolve_synthesis_backend_id(
            voice_data,
            mode=self._mode,
            use_mlx=self._use_mlx,
        )

    def _single_output_synthesis_metadata(
        self,
        *,
        text,
        backend_id,
        output_path,
        dependency_fingerprint=None,
    ):
        waveform, sample_rate = self._read_segment_waveform(output_path)
        plan = plan_synthesis_segments(
            text,
            backend_id=backend_id,
            dependency_fingerprint=dependency_fingerprint,
            max_chars=max(1, len(str(text or "")) or 1),
            max_words=max(1, len(str(text or "").split()) or 1),
        )
        if len(plan["segments"]) != 1:
            raise SynthesisWindowError(
                "synthesis_native_batch_plan_invalid",
                "A native one-window batch output resolved to multiple segments.",
            )
        joined, sample_rate, receipt = assemble_synthesis_segments(
            plan,
            [
                {
                    "segment_id": plan["segments"][0]["segment_id"],
                    "audio": waveform,
                    "sample_rate": sample_rate,
                }
            ],
        )
        self._atomic_write_segment_join(output_path, joined, sample_rate)
        return synthesis_receipt_chunk_fields(receipt)

    @staticmethod
    def _common_segment_metadata(segment_metadata):
        values = [
            item.get("metadata")
            for item in segment_metadata
            if isinstance(item.get("metadata"), dict)
        ]
        if not values:
            return {}
        if len(values) == 1:
            return copy.deepcopy(values[0])
        excluded_prefixes = (
            "synthesis_",
        )
        common = {}
        keys = set.intersection(*(set(value) for value in values))
        for key in sorted(keys):
            if key.startswith(excluded_prefixes):
                continue
            candidate = values[0][key]
            if all(value[key] == candidate for value in values[1:]):
                common[key] = copy.deepcopy(candidate)
        return common

    def generate_voice(
        self,
        text,
        instruct_text,
        speaker,
        voice_config,
        output_path,
        fish_render_plan=None,
        fish_instruction=None,
        generation_context=None,
    ):
        """Generate one complete request through the authoritative window contract."""
        voice_data = voice_config.get(speaker)
        if not isinstance(voice_data, dict):
            print(f"Warning: No voice configuration for '{speaker}'. Skipping.")
            return False
        backend_id = self._synthesis_backend_id(voice_data)
        plan = plan_synthesis_segments(
            str(text or ""),
            backend_id=backend_id,
        )
        if not plan["segments"]:
            return False

        lifecycle = (
            copy.deepcopy(dict(generation_context))
            if isinstance(generation_context, dict)
            else None
        )
        if lifecycle and generation_should_cancel(
            lifecycle["project_root"],
            lifecycle["request_id"],
            lifecycle["owner_token"],
        ):
            raise AudioGenerationLifecycleError(
                "audio_request_cancelled",
                "Audio generation request was cancelled before segment dispatch.",
            )

        segment_results = []
        segment_metadata = []
        segment_paths = []
        target = Path(output_path).expanduser().resolve()
        responsive_assets_staged = False
        stage_responsive_assets = bool(
            lifecycle
            and voice_data.get("type") == "clone"
            and (
                isinstance(voice_data.get("experimental_prompt_routing"), dict)
                or isinstance(voice_data.get("responsive_backend_routing"), dict)
            )
        )
        inline_plan_bypass = None
        if len(plan["segments"]) > 1 and fish_render_plan is not None:
            inline_plan_bypass = "internal_segmentation_changed_plan_text"
        try:
            for segment in plan["segments"]:
                stored = None
                if lifecycle:
                    stored = completed_segment_artifact(
                        lifecycle["project_root"],
                        lifecycle["request_id"],
                        lifecycle["chunk_key"],
                        segment["segment_id"],
                        expected_dependency_fingerprint=segment[
                            "dependency_fingerprint"
                        ],
                    )
                if stored is not None:
                    segment_path = Path(stored["path"])
                    waveform, sample_rate = self._read_segment_waveform(
                        segment_path
                    )
                    metadata = copy.deepcopy(stored.get("metadata") or {})
                else:
                    if lifecycle:
                        if generation_should_cancel(
                            lifecycle["project_root"],
                            lifecycle["request_id"],
                            lifecycle["owner_token"],
                        ):
                            raise AudioGenerationLifecycleError(
                                "audio_request_cancelled",
                                "Audio generation request was cancelled before segment dispatch.",
                            )
                        record_segment_started(
                            lifecycle["project_root"],
                            lifecycle["request_id"],
                            lifecycle["owner_token"],
                            lifecycle["chunk_key"],
                            segment["segment_id"],
                            expected_dependency_fingerprint=segment[
                                "dependency_fingerprint"
                            ],
                        )
                        persistent_segment_path = segment_output_path(
                            lifecycle["project_root"],
                            lifecycle["request_id"],
                            lifecycle["chunk_key"],
                            segment["segment_id"],
                        )
                        if stage_responsive_assets and not responsive_assets_staged:
                            stage_verified_responsive_voice_assets(
                                source_project_root=lifecycle["project_root"],
                                destination_root=persistent_segment_path.parent,
                                voice_name=speaker,
                            )
                            responsive_assets_staged = True
                        segment_path = persistent_segment_path.with_name(
                            f".{persistent_segment_path.name}.provider-"
                            f"{secrets.token_hex(6)}.tmp.wav"
                        )
                        segment_paths.append(segment_path)
                    else:
                        segment_path = target.with_name(
                            f".{target.stem}.{segment['segment_id']}.tmp.wav"
                        )
                        segment_paths.append(segment_path)
                    segment_path.unlink(missing_ok=True)
                    try:
                        success = self._generate_voice_unsegmented(
                            segment["generation_text"],
                            instruct_text,
                            speaker,
                            voice_config,
                            str(segment_path),
                            fish_render_plan=(
                                None if inline_plan_bypass else fish_render_plan
                            ),
                            fish_instruction=fish_instruction,
                        )
                        if not success:
                            if lifecycle:
                                record_segment_failed(
                                    lifecycle["project_root"],
                                    lifecycle["request_id"],
                                    lifecycle["owner_token"],
                                    lifecycle["chunk_key"],
                                    segment["segment_id"],
                                    error="Segment provider returned no audio.",
                                )
                            return False
                        waveform, sample_rate = self._read_segment_waveform(
                            segment_path
                        )
                        metadata = self.pop_generation_metadata(segment_path)
                        if lifecycle:
                            persistent_segment_path.parent.mkdir(
                                parents=True,
                                exist_ok=True,
                            )
                            os.replace(segment_path, persistent_segment_path)
                            record_segment_completed(
                                lifecycle["project_root"],
                                lifecycle["request_id"],
                                lifecycle["owner_token"],
                                lifecycle["chunk_key"],
                                segment["segment_id"],
                                expected_dependency_fingerprint=segment[
                                    "dependency_fingerprint"
                                ],
                                artifact_path=persistent_segment_path,
                                sample_rate=sample_rate,
                                sample_count=len(waveform),
                                metadata=metadata,
                            )
                    except Exception as exc:
                        if lifecycle:
                            try:
                                record_segment_failed(
                                    lifecycle["project_root"],
                                    lifecycle["request_id"],
                                    lifecycle["owner_token"],
                                    lifecycle["chunk_key"],
                                    segment["segment_id"],
                                    error=str(exc),
                                )
                            except AudioGenerationLifecycleError:
                                pass
                        raise
                segment_results.append(
                    {
                        "segment_id": segment["segment_id"],
                        "audio": waveform,
                        "sample_rate": sample_rate,
                    }
                )
                segment_metadata.append(
                    {
                        "segment_id": segment["segment_id"],
                        "metadata": metadata,
                    }
                )

            if lifecycle and generation_should_cancel(
                lifecycle["project_root"],
                lifecycle["request_id"],
                lifecycle["owner_token"],
            ):
                raise AudioGenerationLifecycleError(
                    "audio_request_cancelled",
                    "Audio generation request was cancelled before final join.",
                )

            joined, sample_rate, receipt = assemble_synthesis_segments(
                plan,
                segment_results,
            )
            self._atomic_write_segment_join(output_path, joined, sample_rate)
            metadata = {
                **self._common_segment_metadata(segment_metadata),
                **synthesis_receipt_chunk_fields(receipt),
                "synthesis_segment_backend_metadata": segment_metadata,
                "synthesis_fish_inline_plan_bypassed_reason": inline_plan_bypass,
            }
            self._record_generation_metadata(output_path, metadata)
            return True
        finally:
            for path in segment_paths:
                path.unlink(missing_ok=True)

    def generate_community_qvoice(
        self,
        text,
        instruct_text,
        speaker,
        voice_data,
        output_path,
    ):
        if not self._use_mlx:
            raise ValueError(
                "Community Qwen Voices require Alexandria's Apple-Silicon MLX backend."
            )
        approval = str(
            voice_data.get("community_pack_approval_fingerprint") or ""
        ).strip()
        if not approval:
            raise ValueError(
                f"Community Qwen Voice for '{speaker}' has no listening approval."
            )
        relative = Path(str(voice_data.get("community_pack_path") or ""))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(
                f"Community Qwen Voice for '{speaker}' has an unsafe pack path."
            )
        output = Path(output_path).expanduser().resolve()
        pack_path = None
        for root in (output.parent, *output.parents):
            candidate = (root / relative).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                pack_path = candidate
                break
        if pack_path is None:
            raise ValueError(
                f"Community Qwen Voice pack for '{speaker}' is missing."
            )
        line_direction = strip_prompt_route_tag(instruct_text).strip()
        persistent = str(
            voice_data.get("character_style")
            or voice_data.get("description")
            or ""
        ).strip()
        instruction = " ".join(
            part for part in (persistent, line_direction) if part
        ) or "Natural, clear delivery."
        try:
            seed = int(voice_data.get("seed", -1))
        except (TypeError, ValueError):
            seed = -1
        return self._init_mlx().generate_community_qwen_pack(
            text=text,
            pack_path=str(pack_path),
            family=str(
                voice_data.get("community_pack_family") or "qvoice_graft"
            ),
            expected_sha256=str(voice_data.get("community_pack_sha256") or ""),
            approval_fingerprint=approval,
            instruct=instruction,
            language=str(self._language or "English"),
            output_path=output_path,
            seed=seed,
            request_label=speaker,
        )

    # ── Voice design generation ──────────────────────────────────

    def generate_voice_design(self, description, sample_text, language=None, seed=-1):
        """Generate a voice from a text description using the VoiceDesign model.

        Args:
            description: Natural language description of the desired voice
            sample_text: Text to synthesize with the designed voice
            language: Language code (defaults to engine's configured language)
            seed: Random seed (-1 for random, >= 0 for reproducible)

        Returns:
            (wav_path, sample_rate) on success

        Raises:
            RuntimeError: If generation fails
        """
        if self._use_mlx:
            return self._init_mlx().generate_design_preview(
                description=description,
                sample_text=sample_text,
                seed=seed,
            )

        import time
        import tempfile
        import torch

        lang = language or self._language
        print(f"VoiceDesign: generating preview for description='{description[:80]}...'"
              f"{f', seed={seed}' if seed >= 0 else ''}")

        with self._pytorch_model_job(
            "pytorch_qwen_voice_design",
            self._init_local_design,
            label="PyTorch VoiceDesign synthesis",
        ) as model:
            if seed >= 0:
                torch.manual_seed(seed)

            t_start = time.time()
            wavs, sr = model.generate_voice_design(
                text=sample_text,
                instruct=description,
                language=lang,
                non_streaming_mode=True,
                max_new_tokens=voice_design_max_tokens(sample_text),
            )
        gen_time = time.time() - t_start

        if wavs is None or len(wavs) == 0:
            raise RuntimeError("VoiceDesign model returned no audio")

        audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
        audio = prepare_generated_speech_audio(audio, sr, sample_text)
        duration = len(audio) / sr
        print(f"VoiceDesign: done in {gen_time:.1f}s -> {duration:.1f}s audio")

        # Save to previews directory
        previews_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "designed_voices", "previews")
        os.makedirs(previews_dir, exist_ok=True)

        filename = f"preview_{int(time.time() * 1000)}.wav"
        wav_path = os.path.join(previews_dir, filename)
        self._save_wav(audio, sr, wav_path)

        return wav_path, sr

    def generate_voice_design_range_preview(
        self,
        *,
        description,
        persona_context,
        sample_text,
        output_dir,
        language=None,
        seed=-1,
        force_regenerate=False,
    ):
        """Design one neutral identity, then audition its delivery through Fish.

        Every lane uses the exact same neutral VoiceDesign reference recording
        and transcript. Fish changes only the line delivery. This keeps the
        perceived actor identity fixed across baseline, happy, sad, and angry.
        """
        import tempfile

        voice_definition = str(description or "").strip()
        if not voice_definition:
            raise ValueError("Designed Voice definition is required.")
        identity_text = str(sample_text or "").strip()
        if not identity_text:
            raise ValueError("Designed Voice identity text is required.")
        persona = str(persona_context or "").strip()
        # Qwen VoiceDesign is most reliable with a short anatomy-only prompt.
        # Persona, emotion, cadence, and performance instructions belong to Fish
        # downstream and must not be mixed into the physical identity request.
        identity_instruction = voice_definition
        try:
            configured_seed = int(seed)
        except (TypeError, ValueError):
            configured_seed = -1
        stable_seed = (
            configured_seed
            if configured_seed >= 0
            else int.from_bytes(
                hashlib.sha256(identity_instruction.encode("utf-8")).digest()[:4],
                "big",
            )
            & 0x7FFFFFFF
        )
        preview_dir = Path(output_dir).expanduser().resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "schema_version": 7,
                    "pipeline": "shared_identity_concise_lane_range_audition",
                    "description": voice_definition,
                    "persona_context": persona,
                    "sample_text": identity_text,
                    "language": str(language or "").strip(),
                    "voice_design_seed": stable_seed,
                    "fish_model": getattr(self, "_fish_model", DEFAULT_FISH_MODEL),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        preview_key = preview_fingerprint[:20]
        seed_path = preview_dir / f"voice_design_identity_{preview_key}.wav"
        montage_path = preview_dir / f"voice_design_fish_range_{preview_key}.wav"
        session_dir = preview_dir / "voice_design_range_sessions" / preview_key
        metadata_path = session_dir / "metadata.json"
        cached = self._load_voice_design_range_session(
            metadata_path=metadata_path,
            expected_fingerprint=preview_fingerprint,
            seed_path=seed_path,
            montage_path=montage_path,
        )
        if cached is not None and not force_regenerate:
            return {
                **cached,
                "status": "cached",
                "audio_path": str(montage_path),
                "identity_seed_path": str(seed_path),
            }

        next_revision = (
            int(cached.get("revision", 0)) + 1
            if cached is not None and force_regenerate
            else 0
        )
        sequence = [
            {
                "id": "baseline",
                "label": "Baseline",
                "reference_text": identity_text,
                "text": identity_text,
                "instruction": (
                    "Natural, neutral delivery with clear diction."
                    + (f" Character performance context: {persona}" if persona else "")
                ),
            },
            {
                "id": "happy",
                "label": "Happy",
                "reference_text": identity_text,
                "text": "You're here!",
                "instruction": (
                    "Openly happy, bright, warm, and delighted."
                    + (f" Character performance context: {persona}" if persona else "")
                ),
            },
            {
                "id": "sad",
                "label": "Sad",
                "reference_text": identity_text,
                "text": "It still hurts.",
                "instruction": (
                    "Quietly sad, vulnerable, restrained, and reflective."
                    + (f" Character performance context: {persona}" if persona else "")
                ),
            },
            {
                "id": "angry",
                "label": "Angry",
                "reference_text": identity_text,
                "text": "You betrayed me!",
                "instruction": (
                    "Controlled but unmistakable anger, intense and accusatory."
                    + (f" Character performance context: {persona}" if persona else "")
                ),
            },
        ]
        try:
            with tempfile.TemporaryDirectory(
                prefix="voice-design-fish-range-",
                dir=preview_dir,
            ) as temporary_dir:
                temporary_root = Path(temporary_dir)
                staged_session = temporary_root / "session"
                staged_session.mkdir()
                staged_identity = temporary_root / seed_path.name
                staged_montage = temporary_root / montage_path.name
                reference_instruction = identity_instruction
                generated_reference, sample_rate = self.generate_voice_design(
                    description=reference_instruction,
                    sample_text=identity_text,
                    language=language,
                    seed=stable_seed,
                )
                generated_reference_path = Path(
                    generated_reference
                ).expanduser().resolve()
                shared_reference = staged_session / "reference_identity.wav"
                shutil.copy2(generated_reference_path, shared_reference)
                if generated_reference_path != shared_reference:
                    generated_reference_path.unlink(missing_ok=True)
                shutil.copy2(shared_reference, staged_identity)
                for item in sequence:
                    item["reference_path"] = str(shared_reference)
                    item["reference_identity_score"] = 1.0
                    item["reference_identity_mode"] = "shared_neutral_identity"

                def apply_fish_result(item, fish_result):
                    item["style"] = fish_result.style
                    item["selected_prompt"] = fish_result.selected.prompt_key
                    item["delivery_score"] = round(
                        fish_result.selected.delivery_score,
                        6,
                    )
                    item["instruction_delivery_score"] = round(
                        fish_result.selected.instruction_delivery_score,
                        6,
                    )
                    item["identity_score"] = round(
                        fish_result.selected.identity_score,
                        6,
                    )
                    item["text_validation_passed"] = bool(
                        getattr(fish_result.selected, "text_passed", True)
                    )
                    item["word_error_rate"] = round(
                        float(getattr(fish_result.selected, "word_error_rate", 0.0)),
                        6,
                    )
                    item["acoustic_features"] = {
                        "duration_seconds": round(
                            fish_result.selected.features.duration_seconds,
                            6,
                        ),
                        "words_per_second": round(
                            fish_result.selected.features.words_per_second,
                            6,
                        ),
                        "rms_mean": round(
                            fish_result.selected.features.rms_mean,
                            6,
                        ),
                        "rms_cv": round(
                            fish_result.selected.features.rms_cv,
                            6,
                        ),
                        "pitch_cv": round(
                            fish_result.selected.features.pitch_cv,
                            6,
                        ),
                        "silence_ratio": round(
                            fish_result.selected.features.silence_ratio,
                            6,
                        ),
                    }

                baseline_item = sequence[0]
                baseline_features = audio_features(shared_reference, identity_text)
                baseline_segment = staged_session / "segment_baseline.wav"
                shutil.copy2(shared_reference, baseline_segment)
                baseline_item.update(
                    {
                        "style": "neutral",
                        "selected_prompt": "voice_design_identity",
                        "delivery_score": 1.0,
                        "instruction_delivery_score": 1.0,
                        "identity_score": 1.0,
                        "text_validation_passed": True,
                        "word_error_rate": 0.0,
                        "acoustic_features": {
                            "duration_seconds": round(
                                baseline_features.duration_seconds,
                                6,
                            ),
                            "words_per_second": round(
                                baseline_features.words_per_second,
                                6,
                            ),
                            "rms_mean": round(baseline_features.rms_mean, 6),
                            "rms_cv": round(baseline_features.rms_cv, 6),
                            "pitch_cv": round(baseline_features.pitch_cv, 6),
                            "silence_ratio": round(
                                baseline_features.silence_ratio,
                                6,
                            ),
                        },
                    }
                )

                segment_paths = {"baseline": baseline_segment}
                for item in sequence[1:]:
                    segment_path = staged_session / f"segment_{item['id']}.wav"
                    segment_paths[item["id"]] = segment_path
                    fish_result = self._generate_with_fish(
                        text=item["text"],
                        instruction={
                            "happy": "Happy.",
                            "sad": "Sad.",
                            "angry": "Angry.",
                        }[item["id"]],
                        speaker="Designed Voice audition",
                        ref_audio=item["reference_path"],
                        ref_text=item["reference_text"],
                        output_path=str(segment_path),
                        voice_data={},
                        route_mode="voice_design_identity_seed",
                        route_reason=f"audition:{item['id']}",
                        require_delivery_evidence=False,
                        allow_text_mismatch=True,
                        max_candidates=2,
                        return_result=True,
                    )
                    apply_fish_result(item, fish_result)

                variance_warnings = self._voice_design_range_variance(sequence)
                text_warnings = [
                    {
                        "code": "audition_text_unverified",
                        "lane": item["id"],
                        "label": item["label"],
                        "message": (
                            f"Automatic transcription could not verify the {item['label'].lower()} "
                            "audition. Listen before saving it."
                        ),
                    }
                    for item in sequence[1:]
                    if item.get("text_validation_passed") is False
                ]
                warnings = [*variance_warnings, *text_warnings]

                combined = AudioSegment.empty()
                for index, item in enumerate(sequence):
                    if index:
                        combined += AudioSegment.silent(duration=900)
                    with segment_paths[item["id"]].open("rb") as segment_file:
                        combined += AudioSegment.from_file(segment_file, format="wav")
                export_handle = combined.export(staged_montage, format="wav")
                export_handle.close()
                session_metadata = {
                    "status": (
                        "regenerated_all" if force_regenerate else "generated"
                    ),
                    "identity_seed_text": identity_text,
                    "sample_rate": int(sample_rate),
                    "voice_design_seed": stable_seed,
                    "delivery_backend": "voice_design_identity_plus_fish_s21_cloud",
                    "sequence": [
                        {
                            key: value
                            for key, value in item.items()
                            if key != "reference_path"
                        }
                        for item in sequence
                    ],
                    "warnings": warnings,
                    "all_lanes_distinct": not variance_warnings,
                    "preview_fingerprint": preview_fingerprint,
                    "revision": next_revision,
                    "regeneration_counts": {
                        "happy": 0,
                        "sad": 0,
                        "angry": 0,
                    },
                    "full_regeneration": bool(force_regenerate),
                }
                (staged_session / "metadata.json").write_text(
                    json.dumps(session_metadata, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                session_dir.parent.mkdir(parents=True, exist_ok=True)
                backup_session = temporary_root / "backup-session"
                backup_identity = temporary_root / "backup-identity.wav"
                backup_montage = temporary_root / "backup-montage.wav"
                try:
                    if session_dir.exists():
                        os.replace(session_dir, backup_session)
                    if seed_path.exists():
                        os.replace(seed_path, backup_identity)
                    if montage_path.exists():
                        os.replace(montage_path, backup_montage)
                    os.replace(staged_session, session_dir)
                    os.replace(staged_identity, seed_path)
                    os.replace(staged_montage, montage_path)
                except Exception:
                    shutil.rmtree(session_dir, ignore_errors=True)
                    seed_path.unlink(missing_ok=True)
                    montage_path.unlink(missing_ok=True)
                    if backup_session.exists():
                        os.replace(backup_session, session_dir)
                    if backup_identity.exists():
                        os.replace(backup_identity, seed_path)
                    if backup_montage.exists():
                        os.replace(backup_montage, montage_path)
                    raise
        except Exception:
            raise

        for item in sequence:
            item.pop("reference_path", None)

        return {
            "status": "regenerated_all" if force_regenerate else "generated",
            "audio_path": str(montage_path),
            "identity_seed_path": str(seed_path),
            "identity_seed_text": identity_text,
            "sample_rate": int(sample_rate),
            "voice_design_seed": stable_seed,
            "delivery_backend": "voice_design_identity_plus_fish_s21_cloud",
            "sequence": sequence,
            "warnings": warnings,
            "all_lanes_distinct": not variance_warnings,
            "preview_fingerprint": preview_fingerprint,
            "revision": next_revision,
            "full_regeneration": bool(force_regenerate),
        }

    @staticmethod
    def _load_voice_design_range_session(
        *,
        metadata_path,
        expected_fingerprint,
        seed_path,
        montage_path,
    ):
        if not (metadata_path.is_file() and seed_path.is_file() and montage_path.is_file()):
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if metadata.get("preview_fingerprint") != expected_fingerprint:
            return None
        sequence = metadata.get("sequence")
        if not isinstance(sequence, list) or [item.get("id") for item in sequence] != [
            "baseline",
            "happy",
            "sad",
            "angry",
        ]:
            return None
        session_dir = metadata_path.parent
        required = [
            session_dir / "reference_identity.wav",
            *(session_dir / f"segment_{lane}.wav" for lane in (
                "baseline", "happy", "sad", "angry"
            )),
        ]
        if not all(path.is_file() for path in required):
            return None
        return metadata

    @staticmethod
    def _voice_design_range_variance(sequence, *, emotional_gates=None):
        baseline = sequence[0]["acoustic_features"]
        baseline_wps = max(float(baseline["words_per_second"]), 1e-6)
        baseline_rms = max(float(baseline["rms_mean"]), 1e-6)
        baseline_rms_cv = max(float(baseline["rms_cv"]), 1e-6)
        baseline_silence = float(baseline["silence_ratio"])
        gates = emotional_gates or {
            "happy": lambda item: {
                "faster_than_baseline": (
                    item["acoustic_features"]["words_per_second"]
                    >= baseline_wps * 1.10
                ),
                "more_energy_than_baseline": (
                    item["acoustic_features"]["rms_mean"]
                    >= baseline_rms * 1.05
                ),
                "instruction_expressed": (
                    item["instruction_delivery_score"] >= 0.50
                ),
            },
            "sad": lambda item: {
                "slower_than_baseline": (
                    item["acoustic_features"]["words_per_second"]
                    <= baseline_wps * 0.90
                ),
                "quieter_than_baseline": (
                    item["acoustic_features"]["rms_mean"]
                    <= baseline_rms * 0.95
                ),
                "more_silence_than_baseline": (
                    item["acoustic_features"]["silence_ratio"]
                    >= baseline_silence + 0.04
                ),
            },
            "angry": lambda item: {
                "more_energy_than_baseline": (
                    item["acoustic_features"]["rms_mean"]
                    >= baseline_rms * 1.05
                ),
                "more_energy_variation_than_baseline": (
                    item["acoustic_features"]["rms_cv"]
                    >= baseline_rms_cv * 1.10
                ),
                "less_silence_than_baseline": (
                    item["acoustic_features"]["silence_ratio"]
                    <= baseline_silence - 0.02
                ),
                "instruction_expressed": (
                    item["instruction_delivery_score"] >= 0.45
                ),
            },
        }
        warnings = []
        for item in sequence[1:]:
            evidence = gates[item["id"]](item)
            count = sum(evidence.values())
            item["variance_evidence"] = evidence
            item["variance_evidence_count"] = count
            item["variance_status"] = "distinct" if count >= 2 else "subtle"
            if count < 2:
                warnings.append(
                    {
                        "code": "audition_lane_subtle",
                        "lane": item["id"],
                        "label": item["label"],
                        "message": (
                            f"The {item['label'].lower()} delivery is valid speech "
                            "but remains relatively close to neutral."
                        ),
                    }
                )
        return warnings

    @staticmethod
    def _assemble_voice_design_range_montage(
        *,
        session_dir,
        output_path,
        segment_overrides=None,
    ):
        overrides = dict(segment_overrides or {})
        combined = AudioSegment.empty()
        for index, lane in enumerate(("baseline", "happy", "sad", "angry")):
            if index:
                combined += AudioSegment.silent(duration=900)
            segment_path = Path(
                overrides.get(lane, session_dir / f"segment_{lane}.wav")
            )
            with segment_path.open("rb") as segment_file:
                combined += AudioSegment.from_file(segment_file, format="wav")
        export_handle = combined.export(output_path, format="wav")
        export_handle.close()

    def regenerate_voice_design_range_lane(
        self,
        *,
        preview_fingerprint,
        lane,
        output_dir,
    ):
        import tempfile

        fingerprint = str(preview_fingerprint or "").strip().lower()
        lane_id = str(lane or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise ValueError("Designed Voice audition fingerprint is invalid.")
        if lane_id not in {"happy", "sad", "angry"}:
            raise ValueError("Choose Happy, Sad, or Angry to regenerate.")
        preview_dir = Path(output_dir).expanduser().resolve()
        preview_key = fingerprint[:20]
        seed_path = preview_dir / f"voice_design_identity_{preview_key}.wav"
        montage_path = preview_dir / f"voice_design_fish_range_{preview_key}.wav"
        session_dir = preview_dir / "voice_design_range_sessions" / preview_key
        metadata_path = session_dir / "metadata.json"
        metadata = self._load_voice_design_range_session(
            metadata_path=metadata_path,
            expected_fingerprint=fingerprint,
            seed_path=seed_path,
            montage_path=montage_path,
        )
        if metadata is None:
            raise FileNotFoundError(
                "The audition session is unavailable. Generate the four-part audition again."
            )
        sequence = metadata["sequence"]
        item = next(entry for entry in sequence if entry["id"] == lane_id)
        reference_path = session_dir / "reference_identity.wav"
        final_segment = session_dir / f"segment_{lane_id}.wav"

        def apply_result(result):
            item["style"] = result.style
            item["selected_prompt"] = result.selected.prompt_key
            item["delivery_score"] = round(result.selected.delivery_score, 6)
            item["instruction_delivery_score"] = round(
                result.selected.instruction_delivery_score,
                6,
            )
            item["identity_score"] = round(result.selected.identity_score, 6)
            item["text_validation_passed"] = bool(
                getattr(result.selected, "text_passed", True)
            )
            item["word_error_rate"] = round(
                float(getattr(result.selected, "word_error_rate", 0.0)),
                6,
            )
            item["acoustic_features"] = {
                "duration_seconds": round(result.selected.features.duration_seconds, 6),
                "words_per_second": round(result.selected.features.words_per_second, 6),
                "rms_mean": round(result.selected.features.rms_mean, 6),
                "rms_cv": round(result.selected.features.rms_cv, 6),
                "pitch_cv": round(result.selected.features.pitch_cv, 6),
                "silence_ratio": round(result.selected.features.silence_ratio, 6),
            }

        with tempfile.TemporaryDirectory(
            prefix=f"voice-design-{lane_id}-regenerate-",
            dir=session_dir,
        ) as temporary_dir:
            temporary_root = Path(temporary_dir)
            staged_segment = temporary_root / final_segment.name
            staged_montage = temporary_root / montage_path.name
            staged_metadata = temporary_root / "metadata.json"
            backup_segment = temporary_root / f"backup-{final_segment.name}"
            backup_montage = temporary_root / f"backup-{montage_path.name}"
            backup_metadata = temporary_root / "backup-metadata.json"
            shutil.copy2(final_segment, backup_segment)
            shutil.copy2(montage_path, backup_montage)
            shutil.copy2(metadata_path, backup_metadata)
            fish_result = self._generate_with_fish(
                text=item["text"],
                instruction={
                    "happy": "Happy.",
                    "sad": "Sad.",
                    "angry": "Angry.",
                }[lane_id],
                speaker="Designed Voice audition",
                ref_audio=str(reference_path),
                ref_text=item["reference_text"],
                output_path=str(staged_segment),
                voice_data={},
                route_mode="voice_design_identity_seed",
                route_reason=f"audition:{lane_id}:manual_regeneration",
                require_delivery_evidence=False,
                allow_text_mismatch=True,
                max_candidates=2,
                return_result=True,
            )
            apply_result(fish_result)
            variance_warnings = self._voice_design_range_variance(sequence)
            text_warnings = [
                {
                    "code": "audition_text_unverified",
                    "lane": entry["id"],
                    "label": entry["label"],
                    "message": (
                        f"Automatic transcription could not verify the {entry['label'].lower()} "
                        "audition. Listen before saving it."
                    ),
                }
                for entry in sequence[1:]
                if entry.get("text_validation_passed") is False
            ]
            warnings = [*variance_warnings, *text_warnings]
            self._assemble_voice_design_range_montage(
                session_dir=session_dir,
                output_path=staged_montage,
                segment_overrides={lane_id: staged_segment},
            )
            counts = dict(metadata.get("regeneration_counts") or {})
            counts[lane_id] = int(counts.get(lane_id, 0)) + 1
            revision = int(metadata.get("revision", 0)) + 1
            metadata.update(
                {
                    "status": "regenerated",
                    "sequence": sequence,
                    "warnings": warnings,
                    "all_lanes_distinct": not variance_warnings,
                    "revision": revision,
                    "regeneration_counts": counts,
                    "regenerated_lane": lane_id,
                }
            )
            staged_metadata.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            try:
                os.replace(staged_segment, final_segment)
                os.replace(staged_montage, montage_path)
                os.replace(staged_metadata, metadata_path)
            except Exception:
                shutil.copy2(backup_segment, final_segment)
                shutil.copy2(backup_montage, montage_path)
                shutil.copy2(backup_metadata, metadata_path)
                raise

        return {
            **metadata,
            "audio_path": str(montage_path),
            "identity_seed_path": str(seed_path),
        }

    def generate_design_voice(self, text, instruct_text, voice_data, output_path):
        """Generate audio using VoiceDesign model with combined description + instruct.

        The voice_data 'description' field provides the base voice identity,
        and the per-line instruct_text is appended for delivery/emotion direction.
        """
        import shutil

        base_desc = (voice_data.get("description") or "").strip()
        instruct = strip_prompt_route_tag(instruct_text).strip()

        if base_desc and instruct:
            description = f"{base_desc}, {instruct}"
        elif base_desc:
            description = base_desc
        elif instruct:
            description = instruct
        else:
            print("Warning: Design voice has no description or instruct. Using generic.")
            description = "A clear, natural speaking voice"

        try:
            seed = int(voice_data.get("seed", -1))
        except (TypeError, ValueError):
            seed = -1
        wav_path, sr = self.generate_voice_design(
            description=description,
            sample_text=text,
            seed=seed,
        )
        shutil.copy2(wav_path, output_path)
        return True

    # ── LoRA voice generation ────────────────────────────────────

    def _lora_instruction_propagation(self, voice_data, search_roots):
        raw = voice_data.get("instruction_propagation")
        if raw is None:
            for root in search_roots:
                if not root:
                    continue
                directory = Path(root)
                candidates = (
                    directory / "training_meta.json",
                    directory / "mlx_export_manifest.json",
                    directory.parent / "training_meta.json",
                    directory.parent / "mlx_export_manifest.json",
                )
                for candidate in candidates:
                    if not candidate.is_file():
                        continue
                    try:
                        value = json.loads(
                            candidate.read_text(encoding="utf-8")
                        )
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict) and value.get(
                        "instruction_propagation"
                    ) is not None:
                        raw = value["instruction_propagation"]
                        break
                if raw is not None:
                    break
        try:
            return (
                validate_instruction_propagation_contract(raw)
                if raw is not None
                else build_instruction_propagation_contract(
                    mode="identity_only",
                    samples=[],
                )
            )
        except InstructionPropagationError as exc:
            raise ValueError(
                f"The LoRA instruction propagation contract is invalid: {exc}"
            ) from exc

    def _lora_instruction_text(self, instruct_text, voice_data, propagation):
        instruct_parts = [
            str(instruct_text or "").strip(),
            str(
                voice_data.get("character_style")
                or voice_data.get("default_style")
                or ""
            ).strip(),
        ]
        combined = " ".join(part for part in instruct_parts if part)
        try:
            return normalize_instruction(
                combined,
                required=propagation[
                    "instruction_required_at_inference"
                ],
            )
        except InstructionPropagationError as exc:
            raise ValueError(str(exc)) from exc

    def _mlx_generate_merged_lora(
        self,
        text,
        instruct_text,
        voice_data,
        output_path,
    ):
        root_dir = os.path.dirname(os.path.dirname(__file__))
        configured_model = voice_data.get("mlx_model_path")
        adapter_path = voice_data.get("adapter_path")
        candidates = []
        if configured_model:
            candidates.append(configured_model)
        if adapter_path:
            candidates.extend(
                [
                    os.path.join(adapter_path, "mlx_model"),
                    adapter_path,
                ]
            )
        model_path = None
        for candidate in candidates:
            resolved = candidate
            if not os.path.isabs(resolved):
                resolved = os.path.join(root_dir, resolved)
            if os.path.isdir(resolved) and os.path.isfile(
                os.path.join(resolved, "model.safetensors")
            ):
                model_path = resolved
                break
        if model_path is None:
            raise FileNotFoundError(
                "The LoRA voice has no exported MLX model. Run the isolated "
                "merge and MLX export validation first."
            )

        ref_audio = voice_data.get("ref_audio")
        ref_text = voice_data.get("ref_text")
        if not ref_audio:
            for candidate in (
                os.path.join(model_path, "ref_sample.wav"),
                os.path.join(os.path.dirname(model_path), "ref_sample.wav"),
            ):
                if os.path.isfile(candidate):
                    ref_audio = candidate
                    break
        if not ref_text:
            for candidate in (
                os.path.join(model_path, "ref_sample.txt"),
                os.path.join(os.path.dirname(model_path), "ref_sample.txt"),
            ):
                if os.path.isfile(candidate):
                    ref_text = Path(candidate).read_text(
                        encoding="utf-8"
                    ).strip()
                    break
        if not ref_audio or not ref_text:
            raise ValueError(
                "The exported LoRA voice requires reference audio and its "
                "exact transcript."
            )
        if not os.path.isabs(ref_audio):
            ref_audio = os.path.join(root_dir, ref_audio)
        propagation = self._lora_instruction_propagation(
            voice_data,
            [model_path, os.path.dirname(model_path)],
        )
        instruct = self._lora_instruction_text(
            instruct_text,
            voice_data,
            propagation,
        )
        return self._init_mlx().generate_merged_lora_clone(
            text=text,
            ref_audio=ref_audio,
            ref_text=ref_text,
            instruct=instruct,
            model_path=model_path,
            output_path=output_path,
            temperature=voice_data.get("lora_mlx_temperature", 0.9),
            top_k=voice_data.get("lora_mlx_top_k", 50),
            top_p=voice_data.get("lora_mlx_top_p", 1.0),
            repetition_penalty=voice_data.get(
                "lora_mlx_repetition_penalty",
                1.5,
            ),
            max_tokens=voice_data.get("lora_mlx_max_tokens", 2000),
        )

    def generate_lora_voice(self, text, instruct_text, voice_data, output_path):
        """Generate audio using a LoRA-finetuned Base model.

        The adapter directory must contain:
          - PEFT adapter weights (adapter_model.safetensors / adapter_config.json)
          - ref_sample.wav (reference audio for voice cloning prompt)
          - training_meta.json (with ref_sample_text)

        The LoRA weights refine voice identity beyond what the reference alone provides.
        """
        if self._use_mlx:
            try:
                return self._mlx_generate_merged_lora(
                    text,
                    instruct_text,
                    voice_data,
                    output_path,
                )
            except Exception as exc:
                import traceback

                print(f"Error generating merged MLX LoRA voice: {exc}")
                traceback.print_exc()
                return False

        try:
            import torch
            import time

            adapter_path = voice_data.get("adapter_path")
            if not adapter_path:
                print(f"Error: No adapter_path in voice_data")
                return False

            # Resolve relative paths against project root
            if not os.path.isabs(adapter_path):
                root_dir = os.path.dirname(os.path.dirname(__file__))
                adapter_path = os.path.join(root_dir, adapter_path)

            if not os.path.isdir(adapter_path):
                adapter_id = os.path.basename(adapter_path)
                if adapter_id.startswith("builtin_"):
                    print(
                        "Error: Built-in adapter is not installed. "
                        "Download it explicitly before synthesis: "
                        f"{adapter_id}"
                    )
                else:
                    print(f"Error: LoRA adapter path not found: {adapter_path}")
                return False

            # Load reference audio and text from adapter directory
            ref_wav_path = os.path.join(adapter_path, "ref_sample.wav")
            meta_path = os.path.join(adapter_path, "training_meta.json")

            if not os.path.exists(ref_wav_path):
                print(f"Error: ref_sample.wav not found in {adapter_path}")
                return False
            if not os.path.exists(meta_path):
                print(f"Error: training_meta.json not found in {adapter_path}")
                return False

            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            ref_text = meta.get("ref_sample_text", "")
            if not ref_text:
                print(f"Error: ref_sample_text missing from training_meta.json")
                return False

            print(f"TTS [local lora] generating for adapter={os.path.basename(adapter_path)}, "
                  f"text='{text[:50]}...'")

            with self._pytorch_model_job(
                "pytorch_qwen_base",
                lambda: self._init_local_lora(adapter_path),
                label="PyTorch LoRA synthesis",
            ) as model:
                # Build or reuse voice clone prompt for this adapter
                if adapter_path not in self._lora_prompt_cache:
                    audio_array, sample_rate = sf.read(ref_wav_path)
                    if audio_array.ndim > 1:
                        audio_array = audio_array.mean(axis=1)
                    print(f"Creating clone prompt for LoRA adapter...")
                    prompt = model.create_voice_clone_prompt(
                        ref_audio=(audio_array, sample_rate),
                        ref_text=ref_text,
                        x_vector_only_mode=True,
                    )
                    self._lora_prompt_cache[adapter_path] = prompt
                    print(f"Clone prompt cached for LoRA adapter.")

                prompt = self._lora_prompt_cache[adapter_path]

                # Use the same instruction contract and formatter as training/MLX.
                propagation = self._lora_instruction_propagation(
                    voice_data,
                    [adapter_path],
                )
                instruct = self._lora_instruction_text(
                    instruct_text,
                    voice_data,
                    propagation,
                )
                gen_extra = {}
                if instruct:
                    gen_extra["instruct_ids"] = model._tokenize_texts(
                        [format_instruction_prompt(instruct)]
                    )

                t_start = time.time()
                wavs, sr = model.generate_voice_clone(
                    text=text,
                    voice_clone_prompt=prompt,
                    non_streaming_mode=True,
                    max_new_tokens=2048,
                    **gen_extra,
                )
            gen_time = time.time() - t_start

            if wavs is None or len(wavs) == 0:
                print(f"Error: No audio generated for: '{text[:50]}...'")
                return False

            audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
            duration = len(audio) / sr
            rtf = duration / gen_time if gen_time > 0 else 0
            print(f"TTS [local lora] done: {gen_time:.1f}s -> {duration:.1f}s audio ({rtf:.2f}x real-time)")
            self._save_wav(audio, sr, output_path)
            return True

        except Exception as e:
            import traceback
            print(f"Error generating LoRA voice: {e}")
            traceback.print_exc()
            return False

    # ── Batch generation ─────────────────────────────────────────

    def generate_batch(
        self,
        chunks,
        voice_config,
        output_dir,
        batch_seed=-1,
        generation_contexts=None,
    ):
        """Generate multiple audio files.

        Local mode: uses native list-based batch API for custom voices.
        External mode: sequential individual calls.

        Args:
            chunks: List of dicts with 'text', 'instruct', 'speaker', 'index' keys
            voice_config: Voice configuration dict
            output_dir: Directory to save output files
            batch_seed: Single seed for all generations (-1 for random)

        Returns:
            dict with 'completed' (list of indices) and 'failed' (list of (index, error) tuples)
        """
        results = {"completed": [], "failed": []}
        responsive_receipts = {}

        if not chunks:
            return results

        original_chunks = list(chunks)
        lifecycle_contexts = (
            generation_contexts
            if isinstance(generation_contexts, dict)
            else {}
        )
        backend_ids = {}
        segment_plans = {}
        native_lifecycle = {}
        native_provider_paths = {}
        native_batch_chunks = []
        for chunk in original_chunks:
            idx = chunk["index"]
            speaker = chunk.get("speaker")
            voice_data = voice_config.get(speaker, {})
            backend_id = self._synthesis_backend_id(voice_data)
            backend_ids[idx] = backend_id
            plan = plan_synthesis_segments(
                str(chunk.get("text") or ""),
                backend_id=backend_id,
            )
            segment_plans[idx] = plan
            if len(plan["segments"]) <= 1:
                context = lifecycle_contexts.get(idx)
                if isinstance(context, dict):
                    segment = plan["segments"][0]
                    stored = completed_segment_artifact(
                        context["project_root"],
                        context["request_id"],
                        context["chunk_key"],
                        segment["segment_id"],
                        expected_dependency_fingerprint=segment[
                            "dependency_fingerprint"
                        ],
                    )
                    output_path = Path(output_dir) / f"temp_batch_{idx}.wav"
                    if stored is not None:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(stored["path"], output_path)
                        metadata = copy.deepcopy(stored.get("metadata") or {})
                        if metadata:
                            self._record_generation_metadata(output_path, metadata)
                        results["completed"].append(idx)
                        continue
                    if generation_should_cancel(
                        context["project_root"],
                        context["request_id"],
                        context["owner_token"],
                    ):
                        results["failed"].append(
                            (idx, "Audio generation request was cancelled.")
                        )
                        continue
                    try:
                        record_segment_started(
                            context["project_root"],
                            context["request_id"],
                            context["owner_token"],
                            context["chunk_key"],
                            segment["segment_id"],
                            expected_dependency_fingerprint=segment[
                                "dependency_fingerprint"
                            ],
                        )
                    except AudioGenerationLifecycleError as exc:
                        results["failed"].append((idx, str(exc)))
                        continue
                    native_lifecycle[idx] = (context, segment)
                    if voice_data.get("type") == "clone" and (
                        isinstance(
                            voice_data.get("experimental_prompt_routing"),
                            dict,
                        )
                        or isinstance(
                            voice_data.get("responsive_backend_routing"),
                            dict,
                        )
                    ):
                        persistent = segment_output_path(
                            context["project_root"],
                            context["request_id"],
                            context["chunk_key"],
                            segment["segment_id"],
                        )
                        try:
                            stage_verified_responsive_voice_assets(
                                source_project_root=context["project_root"],
                                destination_root=persistent.parent,
                                voice_name=speaker,
                            )
                        except Exception as exc:
                            try:
                                record_segment_failed(
                                    context["project_root"],
                                    context["request_id"],
                                    context["owner_token"],
                                    context["chunk_key"],
                                    segment["segment_id"],
                                    error=str(exc),
                                )
                            except AudioGenerationLifecycleError:
                                pass
                            native_lifecycle.pop(idx, None)
                            results["failed"].append((idx, str(exc)))
                            continue
                        native_provider_paths[idx] = persistent.with_name(
                            f".{persistent.name}.provider-"
                            f"{secrets.token_hex(6)}.tmp.wav"
                        )
                Path(output_dir, f"temp_batch_{idx}.wav").unlink(
                    missing_ok=True
                )
                native_batch_chunks.append(chunk)
                continue
            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
            try:
                chunk_config = self._voice_config_with_generation_seed(
                    voice_config,
                    speaker,
                    chunk.get("generation_seed", -1),
                )
                success = self.generate_voice(
                    chunk.get("text", ""),
                    chunk.get("instruct", ""),
                    speaker,
                    chunk_config,
                    output_path,
                    fish_render_plan=chunk.get("fish_render_plan"),
                    fish_instruction=chunk.get("fish_instruction"),
                    generation_context=lifecycle_contexts.get(idx),
                )
                receipt = self.consume_responsive_generation_receipt()
                if receipt is not None:
                    responsive_receipts[idx] = receipt
                if success:
                    results["completed"].append(idx)
                else:
                    results["failed"].append(
                        (idx, "Segmented synthesis generation failed")
                    )
            except Exception as exc:
                results["failed"].append((idx, str(exc)))
        chunks = native_batch_chunks
        if not chunks:
            if responsive_receipts:
                results["responsive_receipts"] = responsive_receipts
            return results

        # Reset torch.compile state to prevent progressive slowdown
        # from dynamo guard accumulation across batches
        if self._compile_codec_enabled:
            self._reset_compile_cache()

        # Separate chunks by voice type
        custom_chunks = []
        clone_chunks = []
        expressive_clone_chunks = []
        community_qvoice_chunks = []
        lora_chunks = []
        design_chunks = []

        for chunk in chunks:
            speaker = chunk.get("speaker")
            voice_data = voice_config.get(speaker, {})
            voice_type = voice_data.get("type", "custom")

            if voice_type == "community_qvoice":
                community_qvoice_chunks.append(chunk)
            elif voice_type == "clone":
                if (
                    voice_data.get("reference_bank_path")
                    or voice_data.get("clone_backend")
                    in {
                        *CONTROLLED_CLONE_BACKENDS,
                        "fish_s21_cloud",
                        ROUTED_CLONE_BACKEND,
                    }
                ):
                    expressive_clone_chunks.append(chunk)
                else:
                    clone_chunks.append(chunk)
            elif voice_type in ("lora", "builtin_lora"):
                lora_chunks.append(chunk)
            elif voice_type == "design":
                design_chunks.append(chunk)
            else:
                custom_chunks.append(chunk)

        # Process custom voice chunks
        if custom_chunks:
            if self._use_mlx:
                batch_results = self._init_mlx().generate_custom_batch(
                    custom_chunks, voice_config, output_dir
                )
            elif self._mode == "local":
                batch_results = self._local_batch_custom(custom_chunks, voice_config, output_dir, batch_seed)
            else:
                batch_results = self._sequential_custom(custom_chunks, voice_config, output_dir, batch_seed)
            results["completed"].extend(batch_results["completed"])
            results["failed"].extend(batch_results["failed"])
            for idx, metadata in (batch_results.get("generation_metadata") or {}).items():
                self._record_generation_metadata(
                    os.path.join(output_dir, f"temp_batch_{idx}.wav"),
                    metadata,
                )
            self._clear_gpu_cache()

        # Expressive reference-bank clones may select a different reference
        # per line, so process them individually and preserve the instruction.
        if expressive_clone_chunks:
            for chunk in expressive_clone_chunks:
                idx = chunk["index"]
                output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                provider_path = native_provider_paths.get(idx)
                generation_output_path = (
                    str(provider_path) if provider_path is not None else output_path
                )
                try:
                    chunk_config = self._voice_config_with_generation_seed(
                        voice_config,
                        chunk["speaker"],
                        chunk.get("generation_seed", -1),
                    )
                    success = self.generate_clone_voice(
                        chunk["text"],
                        chunk["speaker"],
                        chunk_config,
                        generation_output_path,
                        instruct_text=chunk.get("instruct", ""),
                        fish_render_plan=chunk.get("fish_render_plan"),
                        fish_instruction=chunk.get("fish_instruction"),
                    )
                    receipt = self.consume_responsive_generation_receipt()
                    if receipt is not None:
                        responsive_receipts[idx] = receipt
                    if success:
                        if provider_path is not None:
                            os.replace(provider_path, output_path)
                        results["completed"].append(idx)
                    else:
                        results["failed"].append(
                            (idx, "Expressive clone generation failed")
                        )
                except Exception as exc:
                    results["failed"].append((idx, str(exc)))
                finally:
                    if provider_path is not None:
                        provider_path.unlink(missing_ok=True)
            self._clear_gpu_cache()

        if community_qvoice_chunks:
            for chunk in community_qvoice_chunks:
                idx = chunk["index"]
                output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                try:
                    chunk_config = self._voice_config_with_generation_seed(
                        voice_config,
                        chunk["speaker"],
                        chunk.get("generation_seed", -1),
                    )
                    success = self.generate_voice(
                        chunk["text"],
                        chunk.get("instruct", ""),
                        chunk["speaker"],
                        chunk_config,
                        output_path,
                    )
                    if success:
                        results["completed"].append(idx)
                    else:
                        results["failed"].append(
                            (idx, "Community Qwen Voice generation failed")
                        )
                except Exception as exc:
                    results["failed"].append((idx, str(exc)))
            self._clear_gpu_cache()

        # Process ordinary clone voice chunks (batched by speaker in local mode)
        if clone_chunks:
            if self._use_mlx:
                batch_results = self._init_mlx().generate_clone_batch(
                    clone_chunks, voice_config, output_dir
                )
            elif self._mode == "local":
                batch_results = self._local_batch_clone(clone_chunks, voice_config, output_dir)
            else:
                batch_results = {"completed": [], "failed": []}
                for chunk in clone_chunks:
                    idx = chunk["index"]
                    output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                    chunk_config = self._voice_config_with_generation_seed(
                        voice_config,
                        chunk["speaker"],
                        chunk.get("generation_seed", -1),
                    )
                    try:
                        success = self.generate_clone_voice(
                            chunk["text"],
                            chunk["speaker"],
                            chunk_config,
                            output_path,
                            instruct_text=chunk.get("instruct", ""),
                        )
                        if success:
                            batch_results["completed"].append(idx)
                        else:
                            batch_results["failed"].append((idx, "Clone voice generation failed"))
                    except Exception as e:
                        batch_results["failed"].append((idx, str(e)))
            results["completed"].extend(batch_results["completed"])
            results["failed"].extend(batch_results["failed"])
            for idx, metadata in (batch_results.get("generation_metadata") or {}).items():
                self._record_generation_metadata(
                    os.path.join(output_dir, f"temp_batch_{idx}.wav"),
                    metadata,
                )
            self._clear_gpu_cache()

        # Process LoRA voice chunks. Exported Apple-Silicon checkpoints
        # carry per-line instruction context, so they remain sequential.
        if lora_chunks:
            seeded_lora = any(
                chunk.get("generation_seed") is not None
                and int(chunk.get("generation_seed")) >= 0
                for chunk in lora_chunks
            )
            if (
                self._mode == "local"
                and not self._use_mlx
                and not seeded_lora
            ):
                batch_results = self._local_batch_lora(
                    lora_chunks,
                    voice_config,
                    output_dir,
                )
            else:
                batch_results = {"completed": [], "failed": []}
                for chunk in lora_chunks:
                    idx = chunk["index"]
                    output_path = os.path.join(
                        output_dir,
                        f"temp_batch_{idx}.wav",
                    )
                    speaker = chunk.get("speaker")
                    chunk_config = self._voice_config_with_generation_seed(
                        voice_config,
                        speaker,
                        chunk.get("generation_seed", -1),
                    )
                    voice_data = chunk_config.get(speaker, {})
                    try:
                        success = self.generate_lora_voice(
                            text=chunk["text"],
                            instruct_text=chunk.get("instruct", ""),
                            voice_data=voice_data,
                            output_path=output_path,
                        )
                        if success:
                            batch_results["completed"].append(idx)
                        else:
                            batch_results["failed"].append(
                                (idx, "LoRA voice generation failed")
                            )
                    except Exception as e:
                        batch_results["failed"].append((idx, str(e)))
            results["completed"].extend(batch_results["completed"])
            results["failed"].extend(batch_results["failed"])
            self._clear_gpu_cache()

        # Process design voice chunks (sequential — each line has unique description)
        if design_chunks:
            for chunk in design_chunks:
                idx = chunk["index"]
                output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                speaker = chunk.get("speaker")
                chunk_config = self._voice_config_with_generation_seed(
                    voice_config,
                    speaker,
                    chunk.get("generation_seed", -1),
                )
                voice_data = chunk_config.get(speaker, {})
                try:
                    success = self.generate_design_voice(
                        text=chunk["text"],
                        instruct_text=chunk.get("instruct", ""),
                        voice_data=voice_data,
                        output_path=output_path,
                    )
                    if success:
                        results["completed"].append(idx)
                    else:
                        results["failed"].append((idx, "Design voice generation failed"))
                except Exception as e:
                    results["failed"].append((idx, str(e)))

        if responsive_receipts:
            results["responsive_receipts"] = responsive_receipts

        chunk_by_index = {chunk["index"]: chunk for chunk in original_chunks}
        for idx in list(results["completed"]):
            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
            if self._has_generation_metadata(output_path):
                metadata = self._peek_generation_metadata(output_path)
            else:
                chunk = chunk_by_index[idx]
                try:
                    metadata = self._single_output_synthesis_metadata(
                        text=str(chunk.get("text") or ""),
                        backend_id=backend_ids[idx],
                        output_path=output_path,
                    )
                except Exception as exc:
                    results["completed"].remove(idx)
                    results["failed"].append((idx, str(exc)))
                    continue
                self._record_generation_metadata(output_path, metadata)
            lifecycle = native_lifecycle.get(idx)
            if lifecycle is not None:
                context, segment = lifecycle
                try:
                    if generation_should_cancel(
                        context["project_root"],
                        context["request_id"],
                        context["owner_token"],
                    ):
                        raise AudioGenerationLifecycleError(
                            "audio_request_cancelled",
                            "Audio generation request was cancelled before segment publication.",
                        )
                    persistent = segment_output_path(
                        context["project_root"],
                        context["request_id"],
                        context["chunk_key"],
                        segment["segment_id"],
                    )
                    shutil.copy2(output_path, persistent)
                    waveform, sample_rate = self._read_segment_waveform(persistent)
                    record_segment_completed(
                        context["project_root"],
                        context["request_id"],
                        context["owner_token"],
                        context["chunk_key"],
                        segment["segment_id"],
                        expected_dependency_fingerprint=segment[
                            "dependency_fingerprint"
                        ],
                        artifact_path=persistent,
                        sample_rate=sample_rate,
                        sample_count=len(waveform),
                        metadata=metadata,
                    )
                except Exception as exc:
                    results["completed"].remove(idx)
                    results["failed"].append((idx, str(exc)))
                    Path(output_path).unlink(missing_ok=True)

        failed_by_index = {
            int(index): str(error)
            for index, error in results["failed"]
            if isinstance(index, int)
        }
        for idx, (context, segment) in native_lifecycle.items():
            if idx not in failed_by_index:
                continue
            try:
                record_segment_failed(
                    context["project_root"],
                    context["request_id"],
                    context["owner_token"],
                    context["chunk_key"],
                    segment["segment_id"],
                    error=failed_by_index[idx],
                )
            except AudioGenerationLifecycleError:
                pass
        return results

    # ── Connection test ──────────────────────────────────────────

    # ── Local backend methods ────────────────────────────────────

    def _local_generate_custom(self, text, instruct_text, speaker, voice_config, output_path):
        """Generate custom voice audio using local Qwen3-TTS model."""
        try:
            import torch

            voice_data = voice_config.get(speaker)
            if not voice_data:
                print(f"Warning: No voice configuration for '{speaker}'. Skipping.")
                return False

            voice = voice_data.get("voice", "Ryan")
            seed = int(voice_data.get("seed", -1))
            instruct = self._custom_voice_instruction(voice_data, instruct_text)

            import time

            print(f"TTS [local] generating with instruct='{instruct}' for text='{text[:50]}...'")

            with self._pytorch_model_job(
                "pytorch_qwen_custom_voice",
                self._init_local_custom,
                label="PyTorch CustomVoice synthesis",
            ) as model:
                if seed >= 0:
                    torch.manual_seed(seed)

                t_start = time.time()
                wavs, sr = model.generate_custom_voice(
                    text=text,
                    language=self._language,
                    speaker=voice,
                    instruct=instruct,
                    non_streaming_mode=True,
                    max_new_tokens=2048,
                )
            gen_time = time.time() - t_start

            if wavs is None or len(wavs) == 0:
                print(f"Error: No audio generated for: '{text[:50]}...'")
                return False

            # wavs is a list of numpy arrays; concatenate them
            audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
            duration = len(audio) / sr
            rtf = duration / gen_time if gen_time > 0 else 0
            print(f"TTS [local] done: {gen_time:.1f}s -> {duration:.1f}s audio ({rtf:.2f}x real-time)")
            self._save_wav(audio, sr, output_path)
            return True

        except Exception as e:
            import traceback
            print(f"Error generating custom voice for '{speaker}': {e}")
            traceback.print_exc()
            return False

    def _local_generate_clone(self, text, speaker, voice_config, output_path):
        """Generate voice-cloned audio using local Qwen3-TTS Base model."""
        try:
            import torch

            voice_data = voice_config.get(speaker)
            if not voice_data:
                print(f"Warning: No voice configuration for '{speaker}'. Skipping.")
                return False

            seed = int(voice_data.get("seed", -1))

            import time

            print(f"TTS [local clone] generating for speaker='{speaker}', text='{text[:50]}...'")

            with self._pytorch_model_job(
                "pytorch_qwen_base",
                self._init_local_clone,
                label="PyTorch Base clone synthesis",
            ) as model:
                prompt = self._get_clone_prompt(
                    speaker,
                    voice_config,
                    model=model,
                )

                if seed >= 0:
                    torch.manual_seed(seed)

                t_start = time.time()
                wavs, sr = model.generate_voice_clone(
                    text=text,
                    voice_clone_prompt=prompt,
                    non_streaming_mode=True,
                    max_new_tokens=2048,
                )
            gen_time = time.time() - t_start

            if wavs is None or len(wavs) == 0:
                print(f"Error: No audio generated for: '{text[:50]}...'")
                return False

            audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
            duration = len(audio) / sr
            rtf = duration / gen_time if gen_time > 0 else 0
            print(f"TTS [local clone] done: {gen_time:.1f}s -> {duration:.1f}s audio ({rtf:.2f}x real-time)")
            self._save_wav(audio, sr, output_path)
            return True

        except Exception as e:
            import traceback
            print(f"Error generating clone voice for '{speaker}': {e}")
            traceback.print_exc()
            return False

    def _local_batch_custom(self, chunks, voice_config, output_dir, batch_seed=-1):
        """Batch generate custom voice using native list API with sub-batching.

        Autoregressive batch generation runs for as long as the longest sequence.
        Shorter sequences waste compute on padding. To minimize this, chunks are
        sorted by text length and split into sub-batches when the length ratio
        exceeds the configured threshold. Sub-batching can be disabled entirely
        via config, in which case everything runs as one batch.
        """
        import torch
        import time

        results = {"completed": [], "failed": []}

        texts = []
        speakers = []
        instructs = []
        indices = []

        for chunk in chunks:
            idx = chunk["index"]
            text = chunk.get("text", "")
            instruct_text = chunk.get("instruct", "")
            speaker_name = chunk.get("speaker", "")

            voice_data = voice_config.get(speaker_name, {})
            voice = voice_data.get("voice", "Ryan")
            instruct = self._custom_voice_instruction(voice_data, instruct_text)

            texts.append(text)
            speakers.append(voice)
            instructs.append(instruct)
            indices.append(idx)

        total_text_chars = sum(len(t) for t in texts)

        # Sort by text length to group similar-length chunks together.
        # This reduces wasted padding during autoregressive generation
        # (the LLM runs until ALL sequences finish, so short chunks
        # waste compute waiting for long ones).
        sort_order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        texts = [texts[i] for i in sort_order]
        speakers = [speakers[i] for i in sort_order]
        instructs = [instructs[i] for i in sort_order]
        indices = [indices[i] for i in sort_order]

        # Warmup on first batch to pre-tune MIOpen/GPU solvers
        if self._warmup_needed:
            print("Running batch warmup generation...")
            with self._pytorch_model_job(
                "pytorch_qwen_custom_voice",
                self._init_local_custom,
                label="PyTorch CustomVoice warmup",
            ) as model:
                self._warmup_model(model)
            self._warmup_needed = False

        # Clear stale GPU cache from any prior generation to avoid
        # fragmented VRAM blocking large batch allocations (ROCm especially).
        self._clear_gpu_cache()


        with self._pytorch_model_job(
            "pytorch_qwen_custom_voice",
            self._init_local_custom,
            label="PyTorch CustomVoice batch planning",
        ) as model:
            max_items = self._estimate_max_batch_size(
                model, max_text_chars=len(texts[-1]),
            )
        sub_batches = self._build_sub_batches(texts, max_items=max_items)

        print(f"Batch [local]: generating {len(texts)} chunks ({total_text_chars} chars) "
              f"in {len(sub_batches)} sub-batch(es)...")

        t_total_start = time.time()
        total_audio_duration = 0.0

        for sb_idx, (start, end) in enumerate(sub_batches):
            sb_texts = texts[start:end]
            sb_speakers = speakers[start:end]
            sb_instructs = instructs[start:end]
            sb_indices = indices[start:end]
            sb_chars = sum(len(t) for t in sb_texts)

            print(f"  Sub-batch {sb_idx+1}/{len(sub_batches)}: {len(sb_texts)} chunks "
                  f"({sb_chars} chars, {len(sb_texts[0])}-{len(sb_texts[-1])} chars/chunk)")

            try:
                if batch_seed >= 0:
                    torch.manual_seed(batch_seed)

                with self._pytorch_model_job(
                    "pytorch_qwen_custom_voice",
                    self._init_local_custom,
                    label="PyTorch CustomVoice batch synthesis",
                ) as model:
                    t_start = time.time()
                    wavs_list, sr = model.generate_custom_voice(
                        text=sb_texts,
                        language=[self._language] * len(sb_texts),
                        speaker=sb_speakers,
                        instruct=sb_instructs,
                        non_streaming_mode=True,
                        max_new_tokens=2048,
                    )
                gen_time = time.time() - t_start

                if wavs_list is None:
                    for idx in sb_indices:
                        results["failed"].append((idx, "Batch returned None"))
                    continue

                sb_audio_duration = 0.0
                for i, (wav, idx) in enumerate(zip(wavs_list, sb_indices)):
                    try:
                        output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                        audio = self._concat_audio(wav)
                        self._save_wav(audio, sr, output_path)
                        results["completed"].append(idx)
                        duration = len(audio) / sr
                        sb_audio_duration += duration
                        print(f"    Chunk {idx} saved: {os.path.getsize(output_path)} bytes ({duration:.1f}s audio)")
                    except Exception as e:
                        print(f"    Error saving chunk {idx}: {e}")
                        results["failed"].append((idx, str(e)))

                total_audio_duration += sb_audio_duration
                sb_rtf = sb_audio_duration / gen_time if gen_time > 0 else 0
                print(f"  Sub-batch {sb_idx+1} done: {gen_time:.1f}s -> {sb_audio_duration:.1f}s audio ({sb_rtf:.2f}x RT)")

            except Exception as e:
                print(f"  Sub-batch {sb_idx+1} failed: {e}")
                for idx in sb_indices:
                    results["failed"].append((idx, f"Batch error: {e}"))

            # Free GPU memory between sub-batches to prevent VRAM exhaustion
            self._clear_gpu_cache()

        total_time = time.time() - t_total_start
        rtf = total_audio_duration / total_time if total_time > 0 else 0
        print(f"Batch total: {total_time:.1f}s -> {total_audio_duration:.1f}s audio ({rtf:.2f}x real-time)")



        return results

    def _local_batch_clone(self, chunks, voice_config, output_dir):
        """Batch generate clone voices, grouped by speaker.

        Chunks sharing the same speaker (same reference audio) are batched
        together through generate_voice_clone(text=[list], ...).
        Sub-batching by text length is applied within each speaker group.
        """
        import torch
        import time

        results = {"completed": [], "failed": []}

        # Group chunks by speaker
        speaker_groups = {}
        for chunk in chunks:
            speaker = chunk.get("speaker", "")
            speaker_groups.setdefault(speaker, []).append(chunk)

        # Warmup on first batch to pre-tune MIOpen/GPU solvers
        # Uses CustomVoice model (not Base) since warmup just needs to
        # exercise MIOpen/GPU solvers and wake the GPU from deep sleep.
        if self._warmup_needed:
            print("Running batch warmup generation...")
            with self._pytorch_model_job(
                "pytorch_qwen_custom_voice",
                self._init_local_custom,
                label="PyTorch CustomVoice warmup",
            ) as warmup_model:
                self._warmup_model(warmup_model)
            self._warmup_needed = False

        self._clear_gpu_cache()


        t_total_start = time.time()
        total_audio_duration = 0.0

        for speaker, group in speaker_groups.items():
            try:
                with self._pytorch_model_job(
                    "pytorch_qwen_base",
                    self._init_local_clone,
                    label="PyTorch Base clone prompt",
                ):
                    prompt = self._get_clone_prompt(
                        speaker,
                        voice_config,
                        model=model,
                    )
            except Exception as e:
                print(f"  Error building clone prompt for '{speaker}': {e}")
                for chunk in group:
                    results["failed"].append((chunk["index"], str(e)))
                continue

            texts = [c["text"] for c in group]
            indices = [c["index"] for c in group]

            # Sort by text length for sub-batching efficiency
            sort_order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
            texts = [texts[i] for i in sort_order]
            indices = [indices[i] for i in sort_order]

            # Estimate max batch size from VRAM + clone prompt overhead
            clone_tokens = prompt[0].ref_code.shape[0] if prompt[0].ref_code is not None else 0
            ref_text_chars = len(prompt[0].ref_text) if prompt[0].ref_text else 0
            with self._pytorch_model_job(
                "pytorch_qwen_base",
                self._init_local_clone,
                label="PyTorch Base clone batch planning",
            ) as model:
                max_items = self._estimate_max_batch_size(
                    model, clone_tokens, ref_text_chars, len(texts[-1]),
                )
            sub_batches = self._build_sub_batches(texts, max_items=max_items)

            print(f"Batch [clone] speaker='{speaker}': {len(texts)} chunks "
                  f"in {len(sub_batches)} sub-batch(es)")

            for sb_idx, (start, end) in enumerate(sub_batches):
                sb_texts = texts[start:end]
                sb_indices = indices[start:end]

                print(f"  Sub-batch {sb_idx+1}/{len(sub_batches)}: {len(sb_texts)} chunks "
                      f"({len(sb_texts[0])}-{len(sb_texts[-1])} chars/chunk)")

                try:
                    with self._pytorch_model_job(
                        "pytorch_qwen_base",
                        self._init_local_clone,
                        label="PyTorch Base clone batch synthesis",
                    ) as model:
                        t_start = time.time()
                        wavs_list, sr = model.generate_voice_clone(
                            text=sb_texts,
                            voice_clone_prompt=prompt,
                            non_streaming_mode=True,
                            max_new_tokens=2048,
                        )
                    gen_time = time.time() - t_start

                    if wavs_list is None:
                        for idx in sb_indices:
                            results["failed"].append((idx, "Batch returned None"))
                        continue

                    sb_audio_duration = 0.0
                    for wav, idx in zip(wavs_list, sb_indices):
                        try:
                            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                            audio = self._concat_audio(wav)
                            self._save_wav(audio, sr, output_path)
                            results["completed"].append(idx)
                            duration = len(audio) / sr
                            sb_audio_duration += duration
                        except Exception as e:
                            print(f"    Error saving chunk {idx}: {e}")
                            results["failed"].append((idx, str(e)))

                    total_audio_duration += sb_audio_duration
                    sb_rtf = sb_audio_duration / gen_time if gen_time > 0 else 0
                    print(f"  Sub-batch {sb_idx+1} done: {gen_time:.1f}s -> {sb_audio_duration:.1f}s audio ({sb_rtf:.2f}x RT)")

                except Exception as e:
                    print(f"  Sub-batch {sb_idx+1} failed: {e}")
                    for idx in sb_indices:
                        results["failed"].append((idx, f"Batch error: {e}"))

                self._clear_gpu_cache()

        total_time = time.time() - t_total_start
        rtf = total_audio_duration / total_time if total_time > 0 else 0
        print(f"Batch [clone] total: {total_time:.1f}s -> {total_audio_duration:.1f}s audio ({rtf:.2f}x real-time)")



        return results

    def _local_batch_lora(self, chunks, voice_config, output_dir):
        """Batch generate LoRA voices, grouped by adapter.

        Chunks sharing the same adapter are batched together through
        generate_voice_clone(text=[list], instruct_ids=[list], ...).
        Sub-batching by text length is applied within each adapter group.
        """
        import torch
        import time

        results = {"completed": [], "failed": []}
        root_dir = os.path.dirname(os.path.dirname(__file__))

        # Group chunks by adapter_path (resolved to absolute)
        adapter_groups = {}  # adapter_path -> (voice_data, [chunks])
        for chunk in chunks:
            speaker = chunk.get("speaker", "")
            voice_data = voice_config.get(speaker, {})
            adapter_path = voice_data.get("adapter_path", "")

            if not adapter_path:
                results["failed"].append((chunk["index"], "No adapter_path"))
                continue

            if not os.path.isabs(adapter_path):
                adapter_path = os.path.join(root_dir, adapter_path)

            if adapter_path not in adapter_groups:
                adapter_groups[adapter_path] = (voice_data, [])
            adapter_groups[adapter_path][1].append(chunk)

        self._clear_gpu_cache()


        # Warmup on first batch to pre-tune MIOpen/GPU solvers
        # Uses CustomVoice model (not Base) since warmup just needs to
        # exercise MIOpen/GPU solvers and wake the GPU from deep sleep.
        if self._warmup_needed:
            print("Running batch warmup generation...")
            with self._pytorch_model_job(
                "pytorch_qwen_custom_voice",
                self._init_local_custom,
                label="PyTorch CustomVoice warmup",
            ) as warmup_model:
                self._warmup_model(warmup_model)
            self._warmup_needed = False

        t_total_start = time.time()
        total_audio_duration = 0.0

        for adapter_path, (voice_data, group) in adapter_groups.items():
            if not os.path.isdir(adapter_path):
                print(f"  Error: adapter path not found: {adapter_path}")
                for chunk in group:
                    results["failed"].append((chunk["index"], f"Adapter not found: {adapter_path}"))
                continue

            # Load adapter and build/get clone prompt
            try:
                ref_wav_path = os.path.join(adapter_path, "ref_sample.wav")
                meta_path = os.path.join(adapter_path, "training_meta.json")
                if not os.path.exists(ref_wav_path) or not os.path.exists(meta_path):
                    raise FileNotFoundError(f"Missing ref_sample.wav or training_meta.json in {adapter_path}")

                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                ref_text = meta.get("ref_sample_text", "")
                if not ref_text:
                    raise ValueError("ref_sample_text missing from training_meta.json")

                with self._pytorch_model_job(
                    "pytorch_qwen_base",
                    lambda: self._init_local_lora(adapter_path),
                    label="PyTorch LoRA clone prompt",
                ) as model:
                    if adapter_path not in self._lora_prompt_cache:
                        audio_array, sample_rate = sf.read(ref_wav_path)
                        if audio_array.ndim > 1:
                            audio_array = audio_array.mean(axis=1)
                        print(f"Creating clone prompt for LoRA adapter...")
                        prompt = model.create_voice_clone_prompt(
                            ref_audio=(audio_array, sample_rate),
                            ref_text=ref_text,
                            x_vector_only_mode=True,
                        )
                        self._lora_prompt_cache[adapter_path] = prompt
                        print(f"Clone prompt cached for LoRA adapter.")

                prompt = self._lora_prompt_cache[adapter_path]
            except Exception as e:
                print(f"  Error loading LoRA adapter {os.path.basename(adapter_path)}: {e}")
                for chunk in group:
                    results["failed"].append((chunk["index"], str(e)))
                continue

            character_style = voice_data.get("character_style", "") or voice_data.get("default_style", "")

            texts = [c["text"] for c in group]
            instructs_raw = [c.get("instruct", "") for c in group]
            indices = [c["index"] for c in group]

            # Sort by text length
            sort_order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
            texts = [texts[i] for i in sort_order]
            instructs_raw = [instructs_raw[i] for i in sort_order]
            indices = [indices[i] for i in sort_order]

            # Estimate max batch size from VRAM + clone prompt overhead
            clone_tokens = prompt[0].ref_code.shape[0] if prompt[0].ref_code is not None else 0
            ref_text_chars = len(prompt[0].ref_text) if prompt[0].ref_text else 0
            with self._pytorch_model_job(
                "pytorch_qwen_base",
                lambda: self._init_local_lora(adapter_path),
                label="PyTorch LoRA batch planning",
            ) as model:
                max_items = self._estimate_max_batch_size(
                    model, clone_tokens, ref_text_chars, len(texts[-1]),
                )
            sub_batches = self._build_sub_batches(texts, max_items=max_items)

            print(f"Batch [lora] adapter='{os.path.basename(adapter_path)}': {len(texts)} chunks "
                  f"in {len(sub_batches)} sub-batch(es)")

            for sb_idx, (start, end) in enumerate(sub_batches):
                sb_texts = texts[start:end]
                sb_instructs = instructs_raw[start:end]
                sb_indices = indices[start:end]

                print(f"  Sub-batch {sb_idx+1}/{len(sub_batches)}: {len(sb_texts)} chunks "
                      f"({len(sb_texts[0])}-{len(sb_texts[-1])} chars/chunk)")

                try:
                    with self._pytorch_model_job(
                        "pytorch_qwen_base",
                        lambda: self._init_local_lora(adapter_path),
                        label="PyTorch LoRA batch synthesis",
                    ) as model:
                        # Build instruct_ids list for this sub-batch
                        instruct_ids = []
                        for inst in sb_instructs:
                            instruct = inst or ""
                            if character_style:
                                instruct = f"{instruct} {character_style}".strip()
                            if instruct:
                                instruct_formatted = f"<|im_start|>user\n{instruct}<|im_end|>\n"
                                instruct_ids.append(model._tokenize_texts([instruct_formatted])[0])
                            else:
                                instruct_ids.append(None)

                        gen_extra = {}
                        if any(iid is not None for iid in instruct_ids):
                            gen_extra["instruct_ids"] = instruct_ids

                        t_start = time.time()
                        wavs_list, sr = model.generate_voice_clone(
                            text=sb_texts,
                            voice_clone_prompt=prompt,
                            non_streaming_mode=True,
                            max_new_tokens=2048,
                            **gen_extra,
                        )
                    gen_time = time.time() - t_start

                    if wavs_list is None:
                        for idx in sb_indices:
                            results["failed"].append((idx, "Batch returned None"))
                        continue

                    sb_audio_duration = 0.0
                    for wav, idx in zip(wavs_list, sb_indices):
                        try:
                            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                            audio = self._concat_audio(wav)
                            self._save_wav(audio, sr, output_path)
                            results["completed"].append(idx)
                            duration = len(audio) / sr
                            sb_audio_duration += duration
                        except Exception as e:
                            print(f"    Error saving chunk {idx}: {e}")
                            results["failed"].append((idx, str(e)))

                    total_audio_duration += sb_audio_duration
                    sb_rtf = sb_audio_duration / gen_time if gen_time > 0 else 0
                    print(f"  Sub-batch {sb_idx+1} done: {gen_time:.1f}s -> {sb_audio_duration:.1f}s audio ({sb_rtf:.2f}x RT)")

                except Exception as e:
                    print(f"  Sub-batch {sb_idx+1} failed: {e}")
                    for idx in sb_indices:
                        results["failed"].append((idx, f"Batch error: {e}"))

                self._clear_gpu_cache()

        total_time = time.time() - t_total_start
        rtf = total_audio_duration / total_time if total_time > 0 else 0
        print(f"Batch [lora] total: {total_time:.1f}s -> {total_audio_duration:.1f}s audio ({rtf:.2f}x real-time)")



        return results

    # ── External backend methods ─────────────────────────────────

    def _external_generate_custom(self, text, instruct_text, speaker, voice_config, output_path):
        """Generate custom voice audio via external Gradio server."""
        try:
            voice_data = voice_config.get(speaker)
            if not voice_data:
                print(f"Warning: No voice configuration for '{speaker}'. Skipping.")
                return False

            voice = voice_data.get("voice", "Ryan")
            seed = int(voice_data.get("seed", -1))
            instruct = self._custom_voice_instruction(voice_data, instruct_text)

            print(f"TTS [external] generating with instruct='{instruct}' for text='{text[:50]}...'")

            client = self._init_external()

            result = client.predict(
                text=text,
                language=self._language,
                speaker=voice,
                instruct=instruct,
                model_size="1.7B",
                seed=seed,
                api_name="/generate_custom_voice"
            )

            generated_audio_filepath = result[0]
            if not generated_audio_filepath or not os.path.exists(generated_audio_filepath):
                print(f"Error: No audio file generated for: '{text[:50]}...'")
                return False

            if os.path.getsize(generated_audio_filepath) == 0:
                print(f"Error: Generated audio file is empty for: '{text[:50]}...'")
                return False

            shutil.copy(generated_audio_filepath, output_path)
            return True

        except Exception as e:
            import traceback
            print(f"Error generating custom voice for '{speaker}': {e}")
            traceback.print_exc()
            return False

    def _external_generate_clone(self, text, speaker, voice_config, output_path):
        """Generate voice-cloned audio via external Gradio server."""
        try:
            from gradio_client import handle_file

            voice_data = voice_config.get(speaker)
            if not voice_data:
                print(f"Warning: No voice configuration for '{speaker}'. Skipping.")
                return False

            ref_audio = voice_data.get("ref_audio")
            ref_text = voice_data.get("ref_text")
            seed = int(voice_data.get("seed", -1))

            if not ref_audio or not ref_text:
                print(f"Warning: Clone voice for '{speaker}' missing ref_audio or ref_text. Skipping.")
                return False

            # Resolve relative paths against project root
            if not os.path.isabs(ref_audio):
                root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ref_audio = os.path.join(root_dir, ref_audio)

            if not os.path.exists(ref_audio):
                print(f"Warning: Reference audio not found for '{speaker}': {ref_audio}")
                return False

            client = self._init_external()

            result = client.predict(
                handle_file(ref_audio),
                ref_text,
                text,
                self._language,
                False,       # use_xvector_only
                "1.7B",
                200,         # max_chunk_chars
                0,           # chunk_gap
                seed,
                api_name="/generate_voice_clone"
            )

            generated_audio_filepath = result[0]
            if not generated_audio_filepath or not os.path.exists(generated_audio_filepath):
                print(f"Error: No audio file generated for: '{text[:50]}...'")
                return False

            if os.path.getsize(generated_audio_filepath) == 0:
                print(f"Error: Generated audio file is empty for: '{text[:50]}...'")
                return False

            shutil.copy(generated_audio_filepath, output_path)
            return True

        except Exception as e:
            import traceback
            print(f"Error generating clone voice for '{speaker}': {e}")
            traceback.print_exc()
            return False

    def _sequential_custom(self, chunks, voice_config, output_dir, batch_seed=-1):
        """Sequential custom voice generation for external mode (no native batch)."""
        results = {"completed": [], "failed": []}

        for chunk in chunks:
            idx = chunk["index"]
            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
            speaker = chunk.get("speaker", "")
            chunk_config = self._voice_config_with_generation_seed(
                voice_config,
                speaker,
                chunk.get("generation_seed", -1),
            )
            try:
                success = self.generate_custom_voice(
                    chunk.get("text", ""),
                    chunk.get("instruct", ""),
                    speaker,
                    chunk_config,
                    output_path,
                )
                if success:
                    results["completed"].append(idx)
                    print(f"Batch chunk {idx} saved: {os.path.getsize(output_path)} bytes")
                else:
                    results["failed"].append((idx, "Custom voice generation failed"))
            except Exception as e:
                results["failed"].append((idx, str(e)))

        return results

    # ── Utility ──────────────────────────────────────────────────

    @staticmethod
    def _save_wav(audio_array, sample_rate, output_path):
        """Save a numpy audio array as a WAV file."""
        # Ensure numpy array
        if not isinstance(audio_array, np.ndarray):
            audio_array = np.array(audio_array)
        # Flatten if needed
        if audio_array.ndim > 1:
            audio_array = audio_array.flatten()
        sf.write(output_path, audio_array, sample_rate)
