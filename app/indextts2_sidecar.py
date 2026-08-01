from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import random
import sys
import time
import traceback
from typing import Any

import numpy as np
import torch

from responsive_voice_models import INDEXTTS2_MODEL_REVISION


INDEX_REVISION = INDEXTTS2_MODEL_REVISION


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _install_greedy(model: Any) -> None:
    original = model.gpt.inference_speech

    def greedy_inference(*args: Any, **kwargs: Any) -> Any:
        kwargs["do_sample"] = False
        kwargs["num_beams"] = 1
        return original(*args, **kwargs)

    model.gpt.inference_speech = greedy_inference


def _install_diffusion_steps(model: Any, diffusion_steps: int) -> None:
    if diffusion_steps < 1:
        raise ValueError("diffusion_steps must be positive")
    cfm = model.s2mel.models["cfm"]
    original = cfm.inference

    def inference_override(
        mu: Any,
        x_lens: Any,
        prompt: Any,
        style: Any,
        f0: Any,
        n_timesteps: Any,
        temperature: float = 1.0,
        inference_cfg_rate: float = 0.5,
    ) -> Any:
        return original(
            mu,
            x_lens,
            prompt,
            style,
            f0,
            diffusion_steps,
            temperature=temperature,
            inference_cfg_rate=inference_cfg_rate,
        )

    cfm.inference = inference_override


def _paths(cache_root: Path) -> tuple[Path, dict[str, str]]:
    source = cache_root / "source"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    model_dir = (
        cache_root
        / "huggingface/models--IndexTeam--IndexTTS-2/snapshots"
        / INDEX_REVISION
    )
    aux = cache_root / "aux-flat"
    aux_paths = {
        "w2v_bert": str(aux / "w2v-bert-2.0"),
        "semantic_codec": str(aux / "semantic_codec/model.safetensors"),
        "campplus": str(aux / "campplus_cn_common.bin"),
        "bigvgan": str(aux / "bigvgan"),
    }
    required = [source / "indextts/infer_v2.py", model_dir / "config.yaml"]
    required.extend(Path(value) for value in aux_paths.values())
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("IndexTTS2 runtime is incomplete: " + ", ".join(missing))
    return model_dir, aux_paths


def _load_model(cache_root: Path) -> Any:
    model_dir, aux_paths = _paths(cache_root)
    from indextts.infer_v2 import IndexTTS2

    torch.set_float32_matmul_precision("high")
    with redirect_stdout(sys.stderr):
        model = IndexTTS2(
            cfg_path=str(model_dir / "config.yaml"),
            model_dir=str(model_dir),
            use_fp16=False,
            device="mps",
            use_cuda_kernel=False,
            use_deepspeed=False,
            use_accel=False,
            use_torch_compile=False,
            aux_paths=aux_paths,
        )
    _install_greedy(model)
    _install_diffusion_steps(model, 8)
    return model


def _generate(model: Any, request: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("request_id") or "")
    text = str(request.get("text") or "").strip()
    identity = Path(str(request.get("identity_audio") or "")).expanduser().resolve()
    performance = Path(str(request.get("performance_audio") or "")).expanduser().resolve()
    destination = Path(str(request.get("output_path") or "")).expanduser().resolve()
    if not request_id or not text:
        raise ValueError("IndexTTS2 request requires request_id and text")
    if not identity.is_file() or not performance.is_file():
        raise FileNotFoundError(identity if not identity.is_file() else performance)
    strength = float(request.get("emotion_strength", 0.0))
    if not 0.0 <= strength <= 1.0:
        raise ValueError("emotion_strength must be within [0, 1]")
    diffusion_steps = int(request.get("diffusion_steps", 8))
    num_beams = int(request.get("num_beams", 1))
    if diffusion_steps != 8:
        raise ValueError("IndexTTS2 production routing is pinned to eight diffusion steps")
    if request.get("greedy") is not True or num_beams != 1:
        raise ValueError("IndexTTS2 production route requires greedy one-beam decoding")
    max_mel_tokens = int(request.get("max_mel_tokens", 600))
    seed = int(request.get("seed", 130363))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{os.getpid()}.partial.wav")
    partial.unlink(missing_ok=True)
    started = time.perf_counter()
    with torch.inference_mode(), redirect_stdout(sys.stderr):
        returned = model.infer(
            spk_audio_prompt=str(identity),
            text=text,
            output_path=str(partial),
            emo_audio_prompt=str(performance),
            emo_alpha=strength,
            use_random=False,
            verbose=False,
            num_beams=1,
            max_mel_tokens=max_mel_tokens,
        )
    elapsed = time.perf_counter() - started
    if not partial.is_file() or partial.stat().st_size < 512:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"IndexTTS2 produced no valid audio; returned {returned!r}")
    os.replace(partial, destination)
    return {
        "status": "ok",
        "request_id": request_id,
        "generation_seconds": elapsed,
        "output_path": str(destination),
        "size_bytes": destination.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", required=True)
    args = parser.parse_args()
    cache_root = Path(args.cache_root).expanduser().resolve()
    try:
        started = time.perf_counter()
        model = _load_model(cache_root)
        _emit(
            {
                "status": "ready",
                "model_revision": INDEX_REVISION,
                "load_seconds": time.perf_counter() - started,
            }
        )
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit({"status": "error", "error": str(exc)})
        return 1

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Sidecar request must be an object")
            if request.get("command") == "shutdown":
                return 0
            _emit(_generate(model, request))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            _emit(
                {
                    "status": "error",
                    "request_id": (
                        request.get("request_id")
                        if isinstance(locals().get("request"), dict)
                        else None
                    ),
                    "error": str(exc),
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
