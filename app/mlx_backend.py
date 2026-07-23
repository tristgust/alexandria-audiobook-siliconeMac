from __future__ import annotations

import os
import hashlib
import json
import re
import secrets
import threading
import time
import types
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf

from audio_processing import temporary_mono_wav
from delivery_prosody import apply_delivery_prosody
from instruction_propagation import format_instruction_prompt
from model_memory import ModelMemoryCoordinator
from model_registry import model_spec, resolve_model_path

try:
    from accent_pipeline import (
        accent_registry_dir as shared_accent_registry_dir,
        build_native_seed_instruction,
        detect_accent_pipeline,
        normalize_output_language,
        register_accent_preview as shared_register_accent_preview,
        resolve_accent_clone_reference as shared_resolve_accent_clone_reference,
        sha256_file as shared_sha256_file,
        split_clone_segments as shared_split_clone_segments,
    )
except ImportError:
    from .accent_pipeline import (
        accent_registry_dir as shared_accent_registry_dir,
        build_native_seed_instruction,
        detect_accent_pipeline,
        normalize_output_language,
        register_accent_preview as shared_register_accent_preview,
        resolve_accent_clone_reference as shared_resolve_accent_clone_reference,
        sha256_file as shared_sha256_file,
        split_clone_segments as shared_split_clone_segments,
    )



class MLXBackend:
    """Persistent Qwen3-TTS backend for Apple Silicon via MLX-Audio."""

    CUSTOM_MODEL = model_spec("mlx_custom_voice").repo_id
    CLONE_MODEL = model_spec("mlx_clone").repo_id
    DESIGN_MODEL = model_spec("mlx_voice_design").repo_id
    EXPRESSIVE_CLONE_MODEL = model_spec("mlx_controlled_clone").repo_id

    def __init__(self, language: str = "English"):
        self.language = language or "English"
        self._models = {}
        self._model_lock = threading.RLock()
        self._generation_lock = threading.RLock()
        self._external_models = {}
        self._external_model_lock = threading.RLock()
        self._memory = ModelMemoryCoordinator()

    @staticmethod
    def _disable_unused_transformers_sklearn() -> None:
        """Prevent optional sklearn generation helpers from loading in TTS.

        MLX-Audio uses Transformers only for tokenizers on these paths. Newer
        Transformers releases otherwise import optional candidate-generation
        helpers when scikit-learn happens to be installed, which needlessly
        pulls the full SciPy sparse stack into local TTS model initialization.
        """
        import importlib

        import transformers.utils as transformers_utils
        import transformers.utils.import_utils as import_utils

        unavailable = lambda: False
        import_utils.is_sklearn_available = unavailable
        transformers_utils.is_sklearn_available = unavailable
        # Materialize the lazy generation helper while the optional sklearn
        # probe is disabled. Otherwise a later tokenizer import can still
        # enter the broken local SciPy binary even though assisted generation
        # is not used by Alexandria's TTS paths.
        importlib.import_module("transformers.generation.candidate_generator")

    @staticmethod
    def _load_repository_model(model_id: str):
        MLXBackend._disable_unused_transformers_sklearn()
        from mlx_audio.tts.utils import load_model
        from mlx_audio.utils import get_model_name_parts

        model_path = resolve_model_path(model_id)
        return load_model(
            model_path,
            model_name_parts=get_model_name_parts(model_id),
        )

    def _model(self, kind: str):
        model_ids = {
            "custom": self.CUSTOM_MODEL,
            "clone": self.CLONE_MODEL,
            "design": self.DESIGN_MODEL,
            "expressive_clone": self.EXPRESSIVE_CLONE_MODEL,
        }
        model_keys = {
            "custom": "mlx_custom_voice",
            "clone": "mlx_clone",
            "design": "mlx_voice_design",
            "expressive_clone": "mlx_controlled_clone",
        }
        if kind in self._models:
            return self._models[kind]
        with self._model_lock:
            if kind not in self._models:
                model_id = model_ids[kind]

                def load():
                    print(f"MLX: loading {kind} model: {model_id}")
                    started = time.perf_counter()
                    loaded = self._load_repository_model(model_id)
                    print(
                        f"MLX: {kind} model loaded in "
                        f"{time.perf_counter() - started:.2f}s"
                    )
                    return loaded

                self._models[kind] = self._memory.run_with_oom_retry(
                    model_keys[kind],
                    load,
                    self.release_models,
                )
            return self._models[kind]

    def release_models(self) -> bool:
        """Release all idle MLX model objects and allocator cache."""
        with self._model_lock, self._external_model_lock:
            released = bool(self._models or self._external_models)
            self._models.clear()
            self._external_models.clear()
        clear_cache = getattr(mx, "clear_cache", None)
        if callable(clear_cache):
            clear_cache()
        return released

    def release_models_if_idle(self) -> dict:
        return self._memory.release_if_idle(self.release_models)

    def release_models_manually(self) -> dict:
        return self._memory.release(self.release_models, reason="manual")

    @staticmethod
    def _enable_qwen_icl_instruction(model) -> None:
        if getattr(model, "_alexandria_icl_instruction_enabled", False):
            return
        original = getattr(model, "_prepare_icl_generation_inputs", None)
        if original is None:
            raise RuntimeError(
                "The exported MLX model does not expose Qwen ICL clone inputs."
            )

        def patched(self, *args, **kwargs):
            input_embeds, trailing, pad, ref_codes = original(
                *args,
                **kwargs,
            )
            instruct = getattr(self, "_alexandria_icl_instruction", None)
            if instruct:
                formatted = format_instruction_prompt(instruct)
                instruct_ids = mx.array(
                    self.tokenizer.encode(formatted)
                )[None, :]
                instruct_embed = self.talker.text_projection(
                    self.talker.get_text_embeddings()(instruct_ids)
                )
                input_embeds = mx.concatenate(
                    [instruct_embed, input_embeds],
                    axis=1,
                )
            return input_embeds, trailing, pad, ref_codes

        model._prepare_icl_generation_inputs = types.MethodType(
            patched,
            model,
        )
        model._alexandria_icl_instruction = None
        model._alexandria_icl_instruction_enabled = True

    def _external_qwen_model(self, model_path: str):
        resolved = Path(model_path).expanduser().resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(
                f"Exported MLX model does not exist: {resolved}"
            )
        key = str(resolved)
        if key not in self._external_models:
            self._disable_unused_transformers_sklearn()
            from mlx_audio.tts.utils import load_model

            print(f"MLX: loading exported Qwen model: {resolved}")
            started = time.perf_counter()
            model = load_model(key)
            self._enable_qwen_icl_instruction(model)
            self._external_models[key] = model
            print(
                "MLX: exported Qwen model loaded in "
                f"{time.perf_counter() - started:.2f}s"
            )
        return self._external_models[key]

    @staticmethod
    def _collect_audio(model, results):
        arrays = []
        for result in results:
            mx.eval(result.audio)
            arrays.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
        if not arrays:
            raise RuntimeError("MLX-Audio returned no audio.")
        audio = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
        sample_rate = int(getattr(model, "sample_rate", 24000))
        return audio, sample_rate

    @staticmethod
    def _save(audio, sample_rate: int, output_path: str):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(output_path, audio, sample_rate)

    @staticmethod
    def _sha256_file(path: str) -> str:
        return shared_sha256_file(path)


    def _accent_registry_dir(self):
        root = Path(__file__).resolve().parent.parent
        return shared_accent_registry_dir(root)


    def _register_accent_preview(
        self,
        preview_audio_path: str,
        native_seed_audio: str,
        native_seed_text: str,
        native_language: str,
        preview_text: str,
    ):
        root = Path(__file__).resolve().parent.parent
        return shared_register_accent_preview(
            root=root,
            preview_audio_path=preview_audio_path,
            native_seed_audio=native_seed_audio,
            native_seed_text=native_seed_text,
            native_language=native_language,
            preview_text=preview_text,
        )


    def _resolve_accent_clone_reference(
        self,
        ref_audio: str,
        ref_text: str,
    ):
        root = Path(__file__).resolve().parent.parent
        return shared_resolve_accent_clone_reference(
            root=root,
            ref_audio=ref_audio,
            ref_text=ref_text,
            warning=print,
        )


    @staticmethod
    def _split_clone_segments(
        text: str,
        max_words: int = 14,
    ):
        return shared_split_clone_segments(
            text,
            max_words=max_words,
        )


    def generate_custom(self, text: str, instruct: str, voice: str, output_path: str) -> bool:
        with self._memory.job(), self._generation_lock:
            model = self._model("custom")
            kwargs = {"voice": voice, "lang_code": self.language}
            if instruct:
                kwargs["instruct"] = instruct
            started = time.perf_counter()
            results = list(model.generate(text, **kwargs))
            audio, sample_rate = self._collect_audio(model, results)
            self._save(audio, sample_rate, output_path)
        print(
            f"MLX custom: {time.perf_counter() - started:.2f}s "
            f"for {len(audio) / sample_rate:.2f}s audio"
        )
        return True


    def generate_clone(
        self,
        text: str,
        ref_audio: str,
        ref_text: str,
        output_path: str,
    ) -> bool:
        effective_ref_audio, effective_ref_text, accent_meta = (
            self._resolve_accent_clone_reference(ref_audio, ref_text)
        )

        with self._memory.job(), self._generation_lock:
            model = self._model("clone")
            started = time.perf_counter()
            output_language = self.language

            segments = [text]
            accent_reset = accent_meta is not None
            if accent_reset:
                segments = self._split_clone_segments(text, max_words=14) or [text]

            collected = []
            sample_rate = int(getattr(model, "sample_rate", 24000))
            pause = np.zeros(int(sample_rate * 0.10), dtype=np.float32)

            with temporary_mono_wav(
                effective_ref_audio,
                sample_rate=sample_rate,
            ) as prepared_reference:
                for index, segment in enumerate(segments):
                    results = list(
                        model.generate(
                            segment,
                            ref_audio=str(prepared_reference),
                            ref_text=effective_ref_text,
                            lang_code=output_language,
                        )
                    )
                    audio, sample_rate = self._collect_audio(model, results)
                    collected.append(audio)
                    if accent_reset and index < len(segments) - 1:
                        collected.append(pause.copy())

            final_audio = collected[0] if len(collected) == 1 else np.concatenate(collected)
            self._save(final_audio, sample_rate, output_path)

        mode = " accent-reset clone" if accent_reset else " clone"
        print(
            f"MLX{mode}: {time.perf_counter() - started:.2f}s "
            f"for {len(final_audio) / sample_rate:.2f}s audio "
            f"({len(segments)} segment(s))"
        )
        return True

    def generate_instruction_controlled_clone(
        self,
        text: str,
        ref_audio: str,
        ref_text: str,
        instruct: str,
        output_path: str,
        *,
        temperature: float = 0.75,
        top_k: int = 50,
        top_p: float = 0.95,
        repetition_penalty: float = 1.5,
        max_tokens: int = 2000,
        seed: int | str = -1,
        request_label: str | None = None,
    ) -> bool:
        """Run Qwen Base ICL cloning with an explicit instruction embedding.

        MLX-Audio's ordinary Qwen Base clone path deliberately ignores
        ``instruct``. Alexandria patches the model's ICL prefill so the exact
        per-line instruction is represented once, ahead of the reference and
        target embeddings. This is the only clone path that may be presented
        as instruction-controlled after comparison and listening approval.
        """
        reference = Path(ref_audio).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError(
                f"Instruction-controlled clone reference does not exist: {reference}"
            )
        reference_text = str(ref_text or "").strip()
        if not reference_text:
            raise ValueError(
                "Instruction-controlled cloning requires the exact reference transcript."
            )
        delivery = str(instruct or "").strip()
        if not delivery:
            raise ValueError(
                "Instruction-controlled cloning requires a delivery instruction."
            )
        resolved_temperature = min(2.0, max(0.05, float(temperature)))
        resolved_top_k = max(1, int(top_k))
        resolved_top_p = min(1.0, max(0.05, float(top_p)))
        resolved_repetition = max(1.5, float(repetition_penalty))
        resolved_max_tokens = max(128, int(max_tokens))
        try:
            configured_seed = int(seed)
        except (TypeError, ValueError):
            configured_seed = -1
        runtime_seed = (
            configured_seed
            if configured_seed >= 0
            else secrets.randbits(31)
        )
        diagnostic = {
            "label": str(request_label or "preview"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "reference_text_sha256": hashlib.sha256(
                reference_text.encode("utf-8")
            ).hexdigest(),
            "instruction_sha256": hashlib.sha256(
                delivery.encode("utf-8")
            ).hexdigest(),
            "temperature": resolved_temperature,
            "top_k": resolved_top_k,
            "top_p": resolved_top_p,
            "repetition_penalty": resolved_repetition,
            "max_tokens": resolved_max_tokens,
            "seed": configured_seed if configured_seed >= 0 else None,
            "runtime_seed": runtime_seed,
        }
        request_fingerprint = hashlib.sha256(
            json.dumps(
                diagnostic,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        queued_at = time.perf_counter()
        print(
            "MLX instruction clone request: "
            + json.dumps(
                {
                    "request": request_fingerprint,
                    "label": diagnostic["label"],
                    "instruction_sha256": diagnostic["instruction_sha256"][:16],
                    "temperature": resolved_temperature,
                    "top_k": resolved_top_k,
                    "top_p": resolved_top_p,
                    "max_tokens": resolved_max_tokens,
                    "seed": configured_seed if configured_seed >= 0 else "random",
                    "runtime_seed": runtime_seed,
                },
                sort_keys=True,
            )
        )

        with self._memory.job(), self._generation_lock:
            queue_wait = time.perf_counter() - queued_at
            model = self._model("clone")
            self._enable_qwen_icl_instruction(model)
            model._alexandria_icl_instruction = delivery
            mx.random.seed(runtime_seed)
            started = time.perf_counter()
            try:
                with temporary_mono_wav(
                    reference,
                    sample_rate=int(getattr(model, "sample_rate", 24000)),
                ) as prepared_reference:
                    results = list(
                        model.generate(
                            text=text,
                            ref_audio=str(prepared_reference),
                            ref_text=reference_text,
                            lang_code=self.language,
                            temperature=resolved_temperature,
                            top_k=resolved_top_k,
                            top_p=resolved_top_p,
                            repetition_penalty=resolved_repetition,
                            max_tokens=resolved_max_tokens,
                        )
                    )
            finally:
                model._alexandria_icl_instruction = None
            audio, sample_rate = self._collect_audio(model, results)
            self._save(audio, sample_rate, output_path)
            model_elapsed = time.perf_counter() - started
            prosody = apply_delivery_prosody(
                audio_path=output_path,
                text=text,
                instruction=delivery,
            )
            elapsed = time.perf_counter() - started

        duration = float(sf.info(output_path).duration)
        print(
            "MLX instruction clone complete: "
            + json.dumps(
                {
                    "request": request_fingerprint,
                    "queue_wait_seconds": round(queue_wait, 3),
                    "model_generation_seconds": round(model_elapsed, 3),
                    "generation_seconds": round(elapsed, 3),
                    "audio_duration_seconds": round(duration, 3),
                    "real_time_factor": round(
                        elapsed / duration if duration > 0 else 0.0,
                        3,
                    ),
                    "prosody": prosody,
                },
                sort_keys=True,
            )
        )
        return True

    def generate_expressive_clone(
        self,
        text: str,
        ref_audio: str,
        ref_text: str,
        instruct: str,
        output_path: str,
        *,
        cfg_value: float = 2.0,
        inference_timesteps: int = 10,
        max_tokens: int = 2000,
        seed: int | str = -1,
        request_label: str | None = None,
    ) -> bool:
        """Clone a supplied voice while applying a delivery instruction.

        VoxCPM2 models and MLX random state are process-shared. Alexandria
        serializes every MLX synthesis method so another worker cannot perturb
        this request. A saved non-negative Voice seed is honored; ``-1``
        preserves random variation.
        """
        reference = Path(ref_audio).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError(
                f"Expressive clone reference audio does not exist: {reference}"
            )
        prompt = (instruct or "Natural, clear delivery.").strip()
        resolved_cfg = max(2.0, float(cfg_value))
        resolved_steps = max(1, int(inference_timesteps))
        resolved_max_tokens = max(128, int(max_tokens))
        try:
            resolved_seed = int(seed)
        except (TypeError, ValueError):
            resolved_seed = -1

        runtime_seed = (
            resolved_seed
            if resolved_seed >= 0
            else secrets.randbits(31)
        )
        reference_stat = reference.stat()
        diagnostic_payload = {
            "label": str(request_label or "preview"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "reference_text_sha256": hashlib.sha256(
                ref_text.encode("utf-8")
            ).hexdigest(),
            "instruction_sha256": hashlib.sha256(
                prompt.encode("utf-8")
            ).hexdigest(),
            "reference_size_bytes": int(reference_stat.st_size),
            "reference_mtime_ns": int(reference_stat.st_mtime_ns),
            "cfg_value": resolved_cfg,
            "inference_timesteps": resolved_steps,
            "max_tokens": resolved_max_tokens,
            "seed": resolved_seed if resolved_seed >= 0 else None,
            "runtime_seed": runtime_seed,
        }
        request_fingerprint = hashlib.sha256(
            json.dumps(
                diagnostic_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        queued_at = time.perf_counter()
        print(
            "MLX controlled clone request: "
            + json.dumps(
                {
                    "request": request_fingerprint,
                    "label": diagnostic_payload["label"],
                    "instruction_sha256": diagnostic_payload[
                        "instruction_sha256"
                    ][:16],
                    "reference_size_bytes": diagnostic_payload[
                        "reference_size_bytes"
                    ],
                    "cfg_value": resolved_cfg,
                    "inference_timesteps": resolved_steps,
                    "max_tokens": resolved_max_tokens,
                    "seed": resolved_seed if resolved_seed >= 0 else "random",
                    "runtime_seed": runtime_seed,
                },
                sort_keys=True,
            )
        )

        with self._memory.job(), self._generation_lock:
            queue_wait = time.perf_counter() - queued_at
            model = self._model("expressive_clone")
            mx.random.seed(runtime_seed)
            started = time.perf_counter()
            encode_sample_rate = int(
                getattr(model, "_encode_sample_rate", 16000)
            )
            with temporary_mono_wav(
                reference,
                sample_rate=encode_sample_rate,
            ) as prepared_reference:
                results = list(
                    model.generate(
                        text=text,
                        ref_audio=str(prepared_reference),
                        ref_text=ref_text,
                        instruct=prompt,
                        cfg_value=resolved_cfg,
                        inference_timesteps=resolved_steps,
                        max_tokens=resolved_max_tokens,
                    )
                )
            audio, sample_rate = self._collect_audio(model, results)
            self._save(audio, sample_rate, output_path)
            elapsed = time.perf_counter() - started

        duration = len(audio) / sample_rate
        rtf = elapsed / duration if duration > 0 else 0.0
        print(
            "MLX controlled clone complete: "
            + json.dumps(
                {
                    "request": request_fingerprint,
                    "queue_wait_seconds": round(queue_wait, 3),
                    "generation_seconds": round(elapsed, 3),
                    "audio_duration_seconds": round(duration, 3),
                    "real_time_factor": round(rtf, 3),
                },
                sort_keys=True,
            )
        )
        return True

    def generate_merged_lora_clone(
        self,
        *,
        text: str,
        ref_audio: str,
        ref_text: str,
        instruct: str,
        model_path: str,
        output_path: str,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        repetition_penalty: float = 1.5,
        max_tokens: int = 2000,
        seed: int = -1,
    ) -> bool:
        with self._memory.job(), self._generation_lock:
            return self._generate_merged_lora_clone_locked(
                text=text,
                ref_audio=ref_audio,
                ref_text=ref_text,
                instruct=instruct,
                model_path=model_path,
                output_path=output_path,
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                max_tokens=max_tokens,
                seed=seed,
            )

    def _generate_merged_lora_clone_locked(
        self,
        *,
        text: str,
        ref_audio: str,
        ref_text: str,
        instruct: str,
        model_path: str,
        output_path: str,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        repetition_penalty: float = 1.5,
        max_tokens: int = 2000,
        seed: int = -1,
    ) -> bool:
        """Run a merged LoRA/SFT Qwen checkpoint through fast MLX ICL.

        The exported model contains merged Talker weights. The supplied
        reference clip remains the identity prompt, while Alexandria prepends
        the per-line instruction embedding using the same ordering as the
        official PyTorch generator.
        """
        reference = Path(ref_audio).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError(
                f"Merged-model reference audio does not exist: {reference}"
            )
        reference_text = str(ref_text or "").strip()
        if not reference_text:
            raise ValueError(
                "Merged-model cloning requires the exact reference transcript."
            )
        delivery = str(instruct or "").strip()
        model = self._external_qwen_model(model_path)
        with self._external_model_lock:
            model._alexandria_icl_instruction = delivery or None
            started = time.perf_counter()
            try:
                if seed is not None and int(seed) >= 0:
                    mx.random.seed(int(seed))
                with temporary_mono_wav(
                    reference,
                    sample_rate=int(getattr(model, "sample_rate", 24000)),
                ) as prepared_reference:
                    results = list(
                        model.generate(
                            text=text,
                            ref_audio=str(prepared_reference),
                            ref_text=reference_text,
                            instruct=delivery or None,
                            lang_code=self.language,
                            temperature=float(temperature),
                            top_k=int(top_k),
                            top_p=float(top_p),
                            repetition_penalty=max(
                                1.5,
                                float(repetition_penalty),
                            ),
                            max_tokens=max(128, int(max_tokens)),
                        )
                    )
            finally:
                model._alexandria_icl_instruction = None
        audio, sample_rate = self._collect_audio(model, results)
        self._save(audio, sample_rate, output_path)
        elapsed = time.perf_counter() - started
        duration = len(audio) / sample_rate
        rtf = elapsed / duration if duration > 0 else 0.0
        print(
            f"MLX merged LoRA clone: {elapsed:.2f}s for "
            f"{duration:.2f}s audio (RTF {rtf:.2f})"
        )
        return True

    @staticmethod
    def _accent_pipeline_for(
        description: str,
    ):
        return detect_accent_pipeline(
            description
        )


    def generate_design_preview(
        self,
        description: str,
        sample_text: str,
        seed: int = -1,
    ):
        with self._memory.job(), self._generation_lock:
            return self._generate_design_preview_locked(
                description,
                sample_text,
                seed,
            )

    def _generate_design_preview_locked(
        self,
        description: str,
        sample_text: str,
        seed: int = -1,
    ):
        pipeline = self._accent_pipeline_for(description)
        root = Path(__file__).resolve().parent.parent
        preview_dir = root / "designed_voices" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        if pipeline is None:
            model = self._model("design")
            if seed is not None and int(seed) >= 0:
                mx.random.seed(int(seed))
            started = time.perf_counter()
            results = list(
                model.generate(
                    sample_text,
                    instruct=description,
                    lang_code=self.language,
                )
            )
            audio, sample_rate = self._collect_audio(model, results)
            output_path = preview_dir / f"mlx_preview_{time.time_ns()}.wav"
            self._save(audio, sample_rate, str(output_path))
            print(
                f"MLX VoiceDesign: {time.perf_counter() - started:.2f}s "
                f"for {len(audio) / sample_rate:.2f}s audio"
            )
            return str(output_path), sample_rate

        # Accent-aware path:
        # 1. Design the requested character while speaking the accent's native language.
        # 2. Clone that native reference into the user's English preview sentence.
        # The saved English preview remains compatible with Alexandria's existing
        # designed-voice save workflow and becomes the character's clone reference.
        if seed is not None and int(seed) >= 0:
            mx.random.seed(int(seed))

        label = pipeline["label"]
        native_language = pipeline["language"]
        native_text = pipeline["seed_text"]
        native_instruction = (
            build_native_seed_instruction(
                description,
                pipeline,
            )
        )

        print(
            f"MLX VoiceDesign accent pipeline: {label} native reference -> "
            f"{self.language or 'English'} preview clone"
        )
        started = time.perf_counter()

        design_model = self._model("design")
        native_results = list(
            design_model.generate(
                native_text,
                instruct=native_instruction,
                lang_code=native_language,
            )
        )
        native_audio, native_sample_rate = self._collect_audio(design_model, native_results)

        seed_dir = root / "designed_voices" / "accent_seeds"
        seed_dir.mkdir(parents=True, exist_ok=True)
        seed_path = seed_dir / f"{label.lower()}_seed_{time.time_ns()}.wav"
        self._save(native_audio, native_sample_rate, str(seed_path))

        clone_model = self._model("clone")
        output_language = (
            normalize_output_language(
                self.language
            )
        )

        clone_results = list(
            clone_model.generate(
                sample_text,
                ref_audio=str(seed_path),
                ref_text=native_text,
                lang_code=output_language,
            )
        )
        preview_audio, preview_sample_rate = self._collect_audio(clone_model, clone_results)
        output_path = preview_dir / f"mlx_accent_preview_{time.time_ns()}.wav"
        self._save(preview_audio, preview_sample_rate, str(output_path))
        self._register_accent_preview(
            preview_audio_path=str(output_path),
            native_seed_audio=str(seed_path),
            native_seed_text=native_text,
            native_language=native_language,
            preview_text=sample_text,
        )

        print(
            f"MLX VoiceDesign accent pipeline complete: "
            f"{time.perf_counter() - started:.2f}s total, "
            f"{len(preview_audio) / preview_sample_rate:.2f}s preview audio"
        )
        return str(output_path), preview_sample_rate

    def generate_custom_batch(self, chunks, voice_config, output_dir: str):
        completed = []
        failed = []
        for chunk in chunks:
            idx = chunk["index"]
            speaker = chunk.get("speaker", "")
            config = voice_config.get(speaker, {})
            voice = config.get("voice", "Ryan")
            instruct = chunk.get("instruct", "") or config.get("default_style", "") or "neutral"
            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
            try:
                self.generate_custom(chunk.get("text", ""), instruct, voice, output_path)
                completed.append(idx)
            except Exception as exc:
                failed.append((idx, str(exc)))
        return {"completed": completed, "failed": failed}

    def generate_clone_batch(self, chunks, voice_config, output_dir: str):
        completed = []
        failed = []
        root = Path(__file__).resolve().parent.parent
        for chunk in chunks:
            idx = chunk["index"]
            speaker = chunk.get("speaker", "")
            config = voice_config.get(speaker, {})
            ref_audio = config.get("ref_audio")
            ref_text = config.get("ref_text")
            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
            try:
                if not ref_audio or not ref_text:
                    raise ValueError(
                        f"Clone voice for '{speaker}' requires ref_audio and ref_text."
                    )
                ref_path = Path(ref_audio)
                if not ref_path.is_absolute():
                    ref_path = root / ref_path
                if not ref_path.exists():
                    raise FileNotFoundError(
                        f"Reference audio not found for '{speaker}': {ref_path}"
                    )
                self.generate_clone(
                    chunk.get("text", ""),
                    str(ref_path),
                    ref_text,
                    output_path,
                )
                completed.append(idx)
            except Exception as exc:
                failed.append((idx, str(exc)))
        return {"completed": completed, "failed": failed}
