#!/usr/bin/env python3
"""Run one isolated CosyVoice 3 supplied-recording clone probe.

The runner supports both the official exact-transcript zero-shot identity path
and the instructed clone path. The official CosyVoice wrapper selects CUDA or
CPU only, so this benchmark applies an evaluation-only device-routing shim for
Apple Silicon. It can also route the speech-tokenizer ONNX graph through
CoreML. No upstream files, production voice assignments, or model checkpoints
are modified.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import math
import random
import resource
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch


DIRECTIONS: dict[str, str] = {
    "neutral": "Speak naturally with restrained suspense.",
    "urgent": "Speak urgently, with immediate pressure and clipped pacing.",
    "controlled_anger": (
        "Speak with controlled anger, holding the fury just beneath the surface."
    ),
    "fear": (
        "Speak in a terrified whisper, barely above a breath, as though someone "
        "is listening nearby."
    ),
    "grief": "Speak with quiet grief and a heavy, restrained sadness.",
    "excited": "Speak with unmistakable excitement and energetic emphasis.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--reference-audio", required=True)
    parser.add_argument("--reference-text")
    parser.add_argument("--text", required=True)
    parser.add_argument("--direction", required=True, choices=sorted(DIRECTIONS))
    parser.add_argument(
        "--mode",
        choices=("instruct", "zero_shot"),
        default="instruct",
    )
    parser.add_argument("--instruction")
    parser.add_argument("--persistent-description")
    parser.add_argument("--seed", type=int, default=1001)
    parser.add_argument("--device", choices=("cpu", "mps"), default="mps")
    parser.add_argument(
        "--speech-tokenizer-provider",
        choices=("cpu", "coreml"),
        default="cpu",
    )
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audio_metrics(path: Path, word_count: int) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / sample_rate
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "duration_seconds": duration,
        "sample_rate": int(sample_rate),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "words_per_second": word_count / duration if duration else None,
    }


def install_device_routing(device: str, provider: str) -> dict[str, Any]:
    import onnxruntime
    from cosyvoice.cli import frontend as frontend_module
    from cosyvoice.cli import model as model_module
    from cosyvoice.hifigan import generator as hift_module

    target_device = torch.device(device)
    original_model_init = model_module.CosyVoice3Model.__init__
    original_frontend_init = frontend_module.CosyVoiceFrontEnd.__init__

    def model_init(self, llm, flow, hift, fp16=False):
        original_model_init(self, llm, flow, hift, fp16)
        self.device = target_device
        self.llm_context = nullcontext()

    def frontend_init(
        self,
        get_tokenizer,
        feat_extractor,
        campplus_model,
        speech_tokenizer_model,
        spk2info="",
        allowed_special="all",
    ):
        original_frontend_init(
            self,
            get_tokenizer,
            feat_extractor,
            campplus_model,
            speech_tokenizer_model,
            spk2info,
            allowed_special,
        )
        self.device = target_device
        if provider == "coreml":
            available = onnxruntime.get_available_providers()
            if "CoreMLExecutionProvider" not in available:
                raise RuntimeError(
                    "CoreMLExecutionProvider was requested but is unavailable."
                )
            options = onnxruntime.SessionOptions()
            options.graph_optimization_level = (
                onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            options.intra_op_num_threads = 1
            self.speech_tokenizer_session = onnxruntime.InferenceSession(
                speech_tokenizer_model,
                sess_options=options,
                providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
            )

    model_module.CosyVoice3Model.__init__ = model_init
    frontend_module.CosyVoiceFrontEnd.__init__ = frontend_init

    hift_precision_patch = False
    if device == "mps":
        # Upstream forces the causal F0 predictor to float64 for precision. MPS
        # has no float64 support, so keep this localized calculation in float32.
        def mps_hift_inference(self, speech_feat, finalize=True):
            self.f0_predictor.to(torch.float32)
            f0 = self.f0_predictor(
                speech_feat.to(torch.float32),
                finalize=finalize,
            ).to(speech_feat)
            source = self.f0_upsamp(f0[:, None]).transpose(1, 2)
            source, _, _ = self.m_source(source)
            source = source.transpose(1, 2)
            if finalize:
                generated = self.decode(
                    x=speech_feat,
                    s=source,
                    finalize=finalize,
                )
            else:
                generated = self.decode(
                    x=speech_feat[
                        :, :, :-self.f0_predictor.condnet[0].causal_padding
                    ],
                    s=source,
                    finalize=finalize,
                )
            return generated, source

        hift_module.CausalHiFTGenerator.inference = mps_hift_inference

        # PyTorch 2.3's MPS backend lacks aten::unfold_backward, which is
        # reached by torch.istft even under inference_mode. Keep the neural
        # vocoder on MPS and transfer only its final tiny ISTFT tensors to CPU.
        def mps_cpu_istft(self, magnitude, phase):
            output_device = magnitude.device
            magnitude_cpu = torch.clip(
                magnitude.to(device="cpu", dtype=torch.float32),
                max=1e2,
            )
            phase_cpu = phase.to(device="cpu", dtype=torch.float32)
            real = magnitude_cpu * torch.cos(phase_cpu)
            imaginary = magnitude_cpu * torch.sin(phase_cpu)
            waveform = torch.istft(
                torch.complex(real, imaginary),
                self.istft_params["n_fft"],
                self.istft_params["hop_len"],
                self.istft_params["n_fft"],
                window=self.stft_window.to(device="cpu", dtype=torch.float32),
            )
            return waveform.to(output_device)

        hift_module.HiFTGenerator._istft = mps_cpu_istft
        hift_precision_patch = True

    return {
        "device": str(target_device),
        "speech_tokenizer_provider_requested": provider,
        "onnx_available_providers": onnxruntime.get_available_providers(),
        "mps_hift_float32_patch": hift_precision_patch,
        "mps_final_istft_cpu_patch": hift_precision_patch,
    }


def main() -> int:
    args = parse_args()
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS was requested but is unavailable.")

    model_dir = Path(args.model_dir).expanduser().resolve()
    reference_audio = Path(args.reference_audio).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not model_dir.is_dir():
        raise FileNotFoundError(model_dir)
    if not reference_audio.is_file():
        raise FileNotFoundError(reference_audio)
    output_dir.mkdir(parents=True, exist_ok=True)

    instruction = args.instruction or DIRECTIONS[args.direction]
    if args.persistent_description:
        instruction = (
            args.persistent_description.rstrip().rstrip(".")
            + ". Preserve this identity and vocal character. "
            + instruction.lstrip()
        )
    if "<|endofprompt|>" not in instruction:
        instruction = (
            "You are a helpful assistant. "
            + instruction.rstrip()
            + "<|endofprompt|>"
        )
    reference_text = (args.reference_text or "").strip()
    if args.mode == "zero_shot" and not reference_text:
        raise ValueError("--reference-text is required for --mode zero_shot")
    reference_prompt_text = reference_text
    if args.mode == "zero_shot" and "<|endofprompt|>" not in reference_prompt_text:
        reference_prompt_text = reference_prompt_text.rstrip() + "<|endofprompt|>"
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    # CosyVoice's optional WeText normalizer downloads ModelScope assets during
    # frontend construction. The benchmark uses simple English and explicitly
    # bypasses text normalization, so fail the optional import closed instead.
    sys.modules["wetext"] = None
    routing = install_device_routing(args.device, args.speech_tokenizer_provider)
    from cosyvoice.cli.cosyvoice import CosyVoice3

    load_started = time.perf_counter()
    model = CosyVoice3(
        str(model_dir),
        load_trt=False,
        load_vllm=False,
        fp16=False,
    )
    load_seconds = time.perf_counter() - load_started

    generation_started = time.perf_counter()
    chunks = []
    if args.mode == "zero_shot":
        generated = model.inference_zero_shot(
            args.text,
            reference_prompt_text,
            str(reference_audio),
            stream=False,
            speed=1.0,
            text_frontend=False,
        )
    else:
        generated = model.inference_instruct2(
            args.text,
            instruction,
            str(reference_audio),
            stream=False,
            speed=1.0,
            text_frontend=False,
        )
    for item in generated:
        chunks.append(item["tts_speech"].detach().cpu())
    generation_seconds = time.perf_counter() - generation_started
    if not chunks:
        raise RuntimeError("CosyVoice 3 returned no audio chunks.")

    audio = torch.cat(chunks, dim=1).squeeze(0).numpy().astype(np.float32)
    output_path = output_dir / f"{args.mode}_{args.direction}_{args.seed}.wav"
    sf.write(output_path, audio, model.sample_rate)
    metrics = audio_metrics(output_path, len(args.text.split()))

    result = {
        "schema_version": 1,
        "candidate": "cosyvoice3_0.5b_2512",
        "device": args.device,
        "routing": routing,
        "mode": args.mode,
        "direction": args.direction,
        "instruction": instruction if args.mode == "instruct" else None,
        "persistent_description_sha256": (
            sha256_text(args.persistent_description)
            if args.persistent_description
            else None
        ),
        "reference_text_sha256": (
            sha256_text(reference_text) if reference_text else None
        ),
        "reference_prompt_terminator_applied": (
            args.mode == "zero_shot"
            and reference_prompt_text != reference_text
        ),
        "seed": args.seed,
        "reference_kind": "supplied_recording_clone",
        "reference_audio_sha256": sha256_file(reference_audio),
        "target_text_sha256": sha256_text(args.text),
        "expected_text": args.text,
        "load_seconds": load_seconds,
        "generation_seconds": generation_seconds,
        "real_time_factor": generation_seconds / metrics["duration_seconds"],
        "peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**3),
        "audio": metrics,
        "output_file": output_path.name,
        "production_promotion_allowed": False,
        "manual_listening_required": True,
    }
    review = {
        "sample_id": (
            f"cosyvoice3_{args.mode}_{args.device}_{args.direction}_{args.seed}"
        ),
        "file": output_path.name,
        "mode": args.mode,
        "requested_direction": args.direction,
        "instruction": instruction if args.mode == "instruct" else None,
        "expected_text": args.text,
        "automatic_transcription_status": "unavailable",
        "automatic_transcript": None,
        "word_error_rate": None,
        "spoken_text_matches_expected": None,
        "narrator_identity_1_to_5": None,
        "delivery_adherence_1_to_5": None,
        "naturalness_1_to_5": None,
        "artifact_severity_1_to_5": None,
        "approve_for_candidate_comparison": None,
        "notes": "",
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (output_dir / "listening_review.json").write_text(
        json.dumps(review, indent=2) + "\n"
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
