import os
import re
import json
import hashlib
import threading
import shutil
import platform
from pathlib import Path

import numpy as np
import soundfile as sf
from pydub import AudioSegment

from hf_access import snapshot_download_with_public_fallback
from instruction_propagation import (
    InstructionPropagationError,
    build_instruction_propagation_contract,
    format_instruction_prompt,
    normalize_instruction,
    validate_instruction_propagation_contract,
)
from model_registry import is_registered_model, model_spec, resolve_model_path

DEFAULT_PAUSE_MS = 500  # Pause between different speakers
SAME_SPEAKER_PAUSE_MS = 250  # Shorter pause for same speaker continuing
PYTORCH_CUSTOM_MODEL = model_spec("pytorch_qwen_custom_voice").repo_id
PYTORCH_CLONE_MODEL = model_spec("pytorch_qwen_base").repo_id
PYTORCH_DESIGN_MODEL = model_spec("pytorch_qwen_voice_design").repo_id


def sanitize_filename(name):
    """Make a string safe for use in filenames"""
    name = re.sub(r'[^\w\-]', '_', name)
    return name.lower()


def combine_audio_with_pauses(audio_segments, speakers, pause_ms=DEFAULT_PAUSE_MS,
                              same_speaker_pause_ms=SAME_SPEAKER_PAUSE_MS,
                              pause_overrides=None):
    """Combine audio segments with pauses between them.

    Args:
        pause_overrides: Optional list aligned with audio_segments. Each entry is
            the pause (ms) to insert *after* that segment, or None to use the
            default speaker-change logic. The last entry is ignored.
    """
    if not audio_segments:
        return None

    combined = audio_segments[0]
    prev_speaker = speakers[0]

    for i, (segment, speaker) in enumerate(zip(audio_segments[1:], speakers[1:])):
        override = pause_overrides[i] if pause_overrides else None
        if override is not None:
            gap = AudioSegment.silent(duration=override)
        elif speaker == prev_speaker:
            gap = AudioSegment.silent(duration=same_speaker_pause_ms)
        else:
            gap = AudioSegment.silent(duration=pause_ms)
        combined += gap + segment
        prev_speaker = speaker

    return combined


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
            override = prev_chunk.get("pause_after")
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

    def __init__(self, config):
        tts_config = config.get("tts", {})
        self._mode = tts_config.get("mode", "external")
        self._url = tts_config.get("url", "http://127.0.0.1:7860")
        self._device = tts_config.get("device", "auto")
        self._compile_codec_enabled = tts_config.get("compile_codec", False)

        # Language setting (passed to Qwen3-TTS)
        self._language = tts_config.get("language", "English")

        # Sub-batching config
        self._sub_batch_enabled = tts_config.get("sub_batch_enabled", True)
        self._sub_batch_min_size = max(1, tts_config.get("sub_batch_min_size", 4))
        self._sub_batch_ratio = max(1.0, float(tts_config.get("sub_batch_ratio", 5)))
        self._sub_batch_max_items = int(tts_config.get("sub_batch_max_items", 0))  # 0 = auto

        # Lazy-loaded backends (guarded by _model_lock to prevent concurrent loads)
        self._model_lock = threading.Lock()
        self._local_custom_model = None
        self._local_clone_model = None
        self._local_design_model = None
        self._local_lora_model = None
        self._warmup_needed = True  # cleared after first batch warmup
        self._lora_adapter_path = None  # track which adapter is currently loaded
        self._gradio_client = None
        self._mlx_backend = None
        self._use_mlx = (
            self._mode == "local"
            and platform.system() == "Darwin"
            and platform.machine() == "arm64"
        )
        if self._use_mlx:
            print("Apple Silicon detected: Alexandria will use MLX-Audio.")

        # Clone prompt cache: speaker_name -> (ref_audio_path, reusable voice_clone_prompt)
        self._clone_prompt_cache = {}
        # LoRA clone prompt cache: adapter_path -> reusable voice_clone_prompt
        self._lora_prompt_cache = {}

    @property
    def mode(self):
        return self._mode

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
            self._local_custom_model = self._load_model(
                Qwen3TTSModel, PYTORCH_CUSTOM_MODEL, load_kwargs,
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
            self._local_clone_model = self._load_model(
                Qwen3TTSModel, PYTORCH_CLONE_MODEL, load_kwargs,
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
            self._local_design_model = self._load_model(
                Qwen3TTSModel, PYTORCH_DESIGN_MODEL, load_kwargs,
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

            # Unload previous adapter if switching
            if self._local_lora_model is not None:
                print(f"Unloading previous LoRA adapter ({self._lora_adapter_path})...")
                del self._local_lora_model
                self._local_lora_model = None
                self._lora_adapter_path = None
                self._lora_prompt_cache.clear()
                self._clear_gpu_cache()

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

            model = self._load_model(
                Qwen3TTSModel, PYTORCH_CLONE_MODEL, load_kwargs,
            )

            # Wrap the talker with the LoRA adapter
            model.model.talker = PeftModel.from_pretrained(
                model.model.talker,
                adapter_path,
            )
            model.model.talker.eval()

            if self._compile_codec_enabled:
                self._compile_codec(model)

            self._local_lora_model = model
            self._lora_adapter_path = adapter_path
            print(f"LoRA adapter loaded from {adapter_path}")
            return model

    def _init_mlx(self):
        """Load the persistent Apple-Silicon MLX backend on demand."""
        if self._mlx_backend is None:
            from mlx_backend import MLXBackend
            self._mlx_backend = MLXBackend(language=self._language)
        return self._mlx_backend

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

    def _get_clone_prompt(self, speaker, voice_config):
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

        # Check cache — invalidate if ref_audio changed
        if speaker in self._clone_prompt_cache:
            cached_path, cached_prompt = self._clone_prompt_cache[speaker]
            if cached_path == ref_audio_path:
                return cached_prompt
            print(f"Voice changed for '{speaker}', rebuilding clone prompt...")

        model = self._init_local_clone()

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
        self._clone_prompt_cache[speaker] = (ref_audio_path, prompt)
        print(f"Clone prompt cached for '{speaker}'.")
        return prompt

    # ── Core generation methods ──────────────────────────────────

    def generate_custom_voice(self, text, instruct_text, speaker, voice_config, output_path):
        """Generate audio using CustomVoice model. Returns True on success."""
        if self._use_mlx:
            voice_data = voice_config.get(speaker, {})
            voice = voice_data.get("voice", "Ryan")
            default_style = voice_data.get("default_style", "")
            instruct = instruct_text or default_style or "neutral"
            return self._init_mlx().generate_custom(
                text=text,
                instruct=instruct,
                voice=voice,
                output_path=output_path,
            )
        if self._mode == "local":
            return self._local_generate_custom(text, instruct_text, speaker, voice_config, output_path)
        else:
            return self._external_generate_custom(text, instruct_text, speaker, voice_config, output_path)

    def _resolve_reference_bank_voice_config(
        self,
        speaker,
        voice_config,
        instruct_text="",
    ):
        voice_data = voice_config.get(speaker, {})
        bank_path = voice_data.get("reference_bank_path")
        if not bank_path:
            return voice_config, None
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isabs(bank_path):
            bank_path = os.path.join(root_dir, bank_path)
        from expressive_reference_bank import select_reference_for_instruction
        selected = select_reference_for_instruction(
            bank_path=bank_path,
            instruction=instruct_text or "",
            project_root=root_dir,
        )
        effective_voice = dict(voice_data)
        effective_voice.update(
            {
                "ref_audio": selected["ref_audio"],
                "ref_text": selected["ref_text"],
                "selected_reference_style": selected["style_key"],
                "selected_reference_id": selected["reference_id"],
            }
        )
        effective_config = dict(voice_config)
        effective_config[speaker] = effective_voice
        print(
            "Expressive reference bank: "
            f"speaker='{speaker}', style='{selected['style_key']}', "
            f"reason='{selected['mapping_reason']}'"
        )
        return effective_config, selected

    def generate_clone_voice(
        self,
        text,
        speaker,
        voice_config,
        output_path,
        instruct_text="",
    ):
        """Generate audio using voice cloning. Returns True on success."""
        effective_config, _ = self._resolve_reference_bank_voice_config(
            speaker,
            voice_config,
            instruct_text,
        )
        if self._use_mlx:
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
            if clone_backend == "qwen3_instruction_controlled":
                instruction_parts = [
                    str(instruct_text or "").strip(),
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
                                str(instruct_text or "").encode("utf-8")
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
                return self._init_mlx().generate_instruction_controlled_clone(
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
                )
            if clone_backend == "voxcpm2_controlled":
                raise ValueError(
                    "The legacy VoxCPM2 clone does not provide reliable per-line "
                    "delivery control. Re-preview this Voice with the Qwen "
                    "instruction-controlled clone before generating production audio."
                )
            if clone_backend != "qwen3_base":
                raise ValueError(
                    f"Unsupported clone backend for '{speaker}': {clone_backend!r}."
                )
            return self._init_mlx().generate_clone(
                text=text,
                ref_audio=ref_audio,
                ref_text=ref_text,
                output_path=output_path,
            )
        if self._mode == "local":
            return self._local_generate_clone(
                text,
                speaker,
                effective_config,
                output_path,
            )
        else:
            return self._external_generate_clone(
                text,
                speaker,
                effective_config,
                output_path,
            )

    def generate_voice(self, text, instruct_text, speaker, voice_config, output_path):
        """Generate audio using the appropriate method based on voice type config."""
        voice_data = voice_config.get(speaker)
        if not voice_data:
            print(f"Warning: No voice configuration for '{speaker}'. Skipping.")
            return False

        voice_type = voice_data.get("type", "custom")

        if voice_type == "clone":
            return self.generate_clone_voice(
                text,
                speaker,
                voice_config,
                output_path,
                instruct_text=instruct_text,
            )
        elif voice_type in ("lora", "builtin_lora"):
            return self.generate_lora_voice(text, instruct_text, voice_data, output_path)
        elif voice_type == "design":
            return self.generate_design_voice(text, instruct_text, voice_data, output_path)
        else:
            return self.generate_custom_voice(text, instruct_text, speaker, voice_config, output_path)

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

        model = self._init_local_design()

        if seed >= 0:
            torch.manual_seed(seed)

        t_start = time.time()
        wavs, sr = model.generate_voice_design(
            text=sample_text,
            instruct=description,
            language=lang,
            non_streaming_mode=True,
            max_new_tokens=2048,
        )
        gen_time = time.time() - t_start

        if wavs is None or len(wavs) == 0:
            raise RuntimeError("VoiceDesign model returned no audio")

        audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
        duration = len(audio) / sr
        print(f"VoiceDesign: done in {gen_time:.1f}s -> {duration:.1f}s audio")

        # Save to previews directory
        previews_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "designed_voices", "previews")
        os.makedirs(previews_dir, exist_ok=True)

        filename = f"preview_{int(time.time() * 1000)}.wav"
        wav_path = os.path.join(previews_dir, filename)
        self._save_wav(audio, sr, wav_path)

        return wav_path, sr

    def generate_design_voice(self, text, instruct_text, voice_data, output_path):
        """Generate audio using VoiceDesign model with combined description + instruct.

        The voice_data 'description' field provides the base voice identity,
        and the per-line instruct_text is appended for delivery/emotion direction.
        """
        import shutil

        base_desc = (voice_data.get("description") or "").strip()
        instruct = (instruct_text or "").strip()

        if base_desc and instruct:
            description = f"{base_desc}, {instruct}"
        elif base_desc:
            description = base_desc
        elif instruct:
            description = instruct
        else:
            print("Warning: Design voice has no description or instruct. Using generic.")
            description = "A clear, natural speaking voice"

        wav_path, sr = self.generate_voice_design(description=description, sample_text=text)
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

            model = self._init_local_lora(adapter_path)

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

    def generate_batch(self, chunks, voice_config, output_dir, batch_seed=-1):
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

        if not chunks:
            return results

        # Reset torch.compile state to prevent progressive slowdown
        # from dynamo guard accumulation across batches
        if self._compile_codec_enabled:
            self._reset_compile_cache()

        # Separate chunks by voice type
        custom_chunks = []
        clone_chunks = []
        expressive_clone_chunks = []
        lora_chunks = []
        design_chunks = []

        for chunk in chunks:
            speaker = chunk.get("speaker")
            voice_data = voice_config.get(speaker, {})
            voice_type = voice_data.get("type", "custom")

            if voice_type == "clone":
                if (
                    voice_data.get("reference_bank_path")
                    or voice_data.get("clone_backend")
                    in {
                        "qwen3_instruction_controlled",
                        "voxcpm2_controlled",
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
            self._clear_gpu_cache()

        # Expressive reference-bank clones may select a different reference
        # per line, so process them individually and preserve the instruction.
        if expressive_clone_chunks:
            for chunk in expressive_clone_chunks:
                idx = chunk["index"]
                output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
                try:
                    success = self.generate_clone_voice(
                        chunk["text"],
                        chunk["speaker"],
                        voice_config,
                        output_path,
                        instruct_text=chunk.get("instruct", ""),
                    )
                    if success:
                        results["completed"].append(idx)
                    else:
                        results["failed"].append(
                            (idx, "Expressive clone generation failed")
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
                    try:
                        success = self.generate_clone_voice(
                            chunk["text"],
                            chunk["speaker"],
                            voice_config,
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
            self._clear_gpu_cache()

        # Process LoRA voice chunks. Exported Apple-Silicon checkpoints
        # carry per-line instruction context, so they remain sequential.
        if lora_chunks:
            if self._mode == "local" and not self._use_mlx:
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
                    voice_data = voice_config.get(speaker, {})
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
                voice_data = voice_config.get(speaker, {})
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
            default_style = voice_data.get("default_style", "")
            seed = int(voice_data.get("seed", -1))

            instruct = instruct_text if instruct_text else (default_style if default_style else "neutral")

            import time

            print(f"TTS [local] generating with instruct='{instruct}' for text='{text[:50]}...'")

            model = self._init_local_custom()

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

            prompt = self._get_clone_prompt(speaker, voice_config)
            model = self._init_local_clone()

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
            character_style = voice_data.get("character_style", "") or voice_data.get("default_style", "")

            instruct = instruct_text if instruct_text else "neutral"
            if character_style:
                instruct = f"{instruct} {character_style}"

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

        model = self._init_local_custom()

        # Warmup on first batch to pre-tune MIOpen/GPU solvers
        if self._warmup_needed:
            print("Running batch warmup generation...")
            self._warmup_model(model)
            self._warmup_needed = False

        # Clear stale GPU cache from any prior generation to avoid
        # fragmented VRAM blocking large batch allocations (ROCm especially).
        self._clear_gpu_cache()


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

        model = self._init_local_clone()

        # Warmup on first batch to pre-tune MIOpen/GPU solvers
        # Uses CustomVoice model (not Base) since warmup just needs to
        # exercise MIOpen/GPU solvers and wake the GPU from deep sleep.
        if self._warmup_needed:
            warmup_model = self._init_local_custom()
            print("Running batch warmup generation...")
            self._warmup_model(warmup_model)
            self._warmup_needed = False

        self._clear_gpu_cache()


        t_total_start = time.time()
        total_audio_duration = 0.0

        for speaker, group in speaker_groups.items():
            try:
                prompt = self._get_clone_prompt(speaker, voice_config)
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
            warmup_model = self._init_local_custom()
            print("Running batch warmup generation...")
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

                model = self._init_local_lora(adapter_path)

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
            default_style = voice_data.get("default_style", "")
            seed = int(voice_data.get("seed", -1))

            instruct = instruct_text if instruct_text else (default_style if default_style else "neutral")

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
                "Auto",
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
            try:
                success = self.generate_custom_voice(
                    chunk.get("text", ""),
                    chunk.get("instruct", ""),
                    chunk.get("speaker", ""),
                    voice_config,
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
