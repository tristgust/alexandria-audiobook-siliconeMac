#!/usr/bin/env python3
"""Resumable Round 1 generator for cached MLX candidates.

Supported candidates in this runner:
- VoxCPM2 controllable cloning and native Rowan anchor cloning;
- Qwen3-TTS Base cloning plus built-in Aiden CustomVoice;
- Fish Audio S2 Pro cloning plus native Marlow anchor cloning;
- MOSS-TTS Local v1.5 after its exact model/tokenizer snapshots are cached.

Every sample has an immutable configuration fingerprint. Existing WAV/receipt
pairs are reused only when the fingerprint and audio hash still match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
SUPPORTED_MODELS = {"voxcpm2", "qwen3_tts", "fish_s2_pro", "moss_tts_local_v15"}


def disable_optional_sklearn() -> None:
    import transformers.utils as transformers_utils
    import transformers.utils.import_utils as import_utils

    unavailable = lambda: False
    import_utils.is_sklearn_available = unavailable
    transformers_utils.is_sklearn_available = unavailable


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audio_metrics(path: Path, text: str) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / int(sample_rate)
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "duration_seconds": duration,
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "words_per_second": len(text.split()) / duration if duration else None,
    }


def peak_rss_gib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**3)


def exact_snapshot(repo_id: str, revision: str) -> Path:
    roots = [
        Path.home() / ".cache" / "huggingface" / "hub",
        Path("/Users/tristan/pinokio/cache/HF_HOME/hub"),
    ]
    folder = "models--" + repo_id.replace("/", "--")
    for root in roots:
        snapshot = root / folder / "snapshots" / revision
        if snapshot.is_dir() and (snapshot / "config.json").is_file():
            return snapshot.resolve()
    raise FileNotFoundError(f"Exact cached snapshot missing: {repo_id}@{revision}")


def load_model(repo_id: str, revision: str):
    from mlx_audio.tts.utils import load_model as mlx_load_model
    from mlx_audio.utils import get_model_name_parts

    snapshot = exact_snapshot(repo_id, revision)
    return (
        mlx_load_model(
            snapshot,
            model_name_parts=get_model_name_parts(repo_id),
            strict=False,
        ),
        snapshot,
    )


def collect_results(model: Any, results: Any) -> tuple[np.ndarray, int]:
    arrays: list[np.ndarray] = []
    sample_rate = int(getattr(model, "sample_rate", 24000))
    for result in results:
        array = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        if len(array):
            arrays.append(array)
        if getattr(result, "sample_rate", None):
            sample_rate = int(result.sample_rate)
    if not arrays:
        raise RuntimeError("MLX candidate returned no audio.")
    return arrays[0] if len(arrays) == 1 else np.concatenate(arrays), sample_rate


def prepared_reference_wav(
    evidence_root: Path,
    source: Path,
    *,
    sample_rate: int,
) -> Path:
    try:
        info = sf.info(source)
    except Exception:
        info = None
    if (
        info is not None
        and info.format == "WAV"
        and int(info.channels) == 1
        and int(info.samplerate) == int(sample_rate)
        and int(info.frames) > 0
    ):
        return source
    cache = evidence_root / "prepared-references"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{sha256_file(source)}_{int(sample_rate)}hz.wav"
    if target.is_file():
        checked = sf.info(target)
        if (
            checked.format == "WAV"
            and int(checked.channels) == 1
            and int(checked.samplerate) == int(sample_rate)
            and int(checked.frames) > 0
        ):
            return target
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(int(sample_rate)),
            "-c:a",
            "pcm_f32le",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file():
        raise RuntimeError(completed.stderr[-2000:])
    return target


def resolve_reference(evidence_root: Path, sample: dict[str, Any]) -> tuple[Path | None, str | None]:
    reference = sample["reference"]
    file_value = reference.get("conditioning_file")
    transcript = reference.get("conditioning_transcript")
    if not file_value:
        return None, None
    path = (evidence_root / "references" / file_value).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path, str(transcript or "")


def sample_fingerprint(sample: dict[str, Any], model_contract: dict[str, Any]) -> str:
    relevant = {
        "round": "alexandria_multimodel_expressive_clone_round1_v1",
        "sample_id": sample["sample_id"],
        "model": model_contract,
        "identity_key": sample["identity_key"],
        "style": sample["style"],
        "target_text_sha256": sample["target_text_sha256"],
        "reference": {
            key: sample["reference"].get(key)
            for key in (
                "conditioning_sha256",
                "conditioning_transcript_sha256",
                "acted_emotion_reference_sha256",
            )
        },
        "control": sample["control"],
        "seed": sample["seed"],
    }
    return sha256_text(canonical_json(relevant))


def existing_result_valid(output: Path, receipt: Path, fingerprint: str) -> bool:
    if not output.is_file() or not receipt.is_file():
        return False
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        data.get("sample_fingerprint") == fingerprint
        and data.get("audio_sha256") == sha256_file(output)
    )


def acquire_sample_lock(
    output: Path,
    receipt: Path,
    fingerprint: str,
    *,
    timeout_seconds: float = 1800.0,
    stale_seconds: float = 3600.0,
) -> tuple[Path | None, bool]:
    """Acquire one sample lock, or wait for another process's valid result."""
    receipt.parent.mkdir(parents=True, exist_ok=True)
    lock = receipt.with_suffix(receipt.suffix + ".lock")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if existing_result_valid(output, receipt, fingerprint):
            return None, True
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_seconds:
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                return None, False
            time.sleep(1.0)
            continue
        try:
            os.write(
                descriptor,
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "sample_fingerprint": fingerprint,
                        "created_at_unix": time.time(),
                    }
                ).encode("utf-8"),
            )
        finally:
            os.close(descriptor)
        return lock, False


def generate_voxcpm(
    model: Any,
    sample: dict[str, Any],
    reference_path: Path,
    evidence_root: Path,
) -> tuple[np.ndarray, int]:
    control = sample["control"]
    encode_rate = int(getattr(model, "_encode_sample_rate", 16000))
    normalized = prepared_reference_wav(
        evidence_root, reference_path, sample_rate=encode_rate
    )
    results = model.generate(
        text=sample["target_text"],
        ref_audio=str(normalized),
        ref_text=sample["reference"].get("conditioning_transcript"),
        instruct=control["instruct"],
        cfg_value=float(control["cfg_value"]),
        inference_timesteps=int(control["inference_timesteps"]),
        warmup_patches=int(control.get("warmup_patches", 1)),
        max_tokens=1800,
    )
    return collect_results(model, results)


def generate_qwen(
    base_model: Any,
    custom_model: Any,
    sample: dict[str, Any],
    reference_path: Path | None,
    reference_text: str | None,
) -> tuple[np.ndarray, int]:
    control = sample["control"]
    if sample["identity_key"] == "native_qwen_aiden":
        results = custom_model.generate(
            text=sample["target_text"],
            voice="Aiden",
            instruct=control["instruct"],
            lang_code="English",
            temperature=0.75,
            top_k=50,
            top_p=0.95,
            repetition_penalty=1.2,
            max_tokens=1800,
            verbose=False,
        )
    else:
        if reference_path is None or not reference_text:
            raise ValueError("Qwen Base clone requires reference audio and transcript.")
        results = base_model.generate(
            text=sample["target_text"],
            ref_audio=str(reference_path),
            ref_text=reference_text,
            lang_code="English",
            temperature=0.75,
            top_k=50,
            top_p=0.95,
            repetition_penalty=1.5,
            max_tokens=1800,
            verbose=False,
        )
    model = custom_model if sample["identity_key"] == "native_qwen_aiden" else base_model
    return collect_results(model, results)


def generate_fish(
    model: Any,
    sample: dict[str, Any],
    reference_path: Path,
    reference_text: str,
    evidence_root: Path,
) -> tuple[np.ndarray, int]:
    control = sample["control"]
    tag = control.get("inline_tag")
    text = sample["target_text"] if not tag else f"[{tag}] {sample['target_text']}"
    reference_rate = int(getattr(model, "_encode_sample_rate", 24000))
    normalized = prepared_reference_wav(
        evidence_root, reference_path, sample_rate=reference_rate
    )
    audio, rate = sf.read(normalized, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if int(rate) != reference_rate:
        raise RuntimeError("Prepared Fish reference has the wrong sample rate.")
    results = model.generate(
        text=text,
        ref_audio=mx.array(audio),
        ref_text=reference_text,
        instruct=control["instruct"],
        max_tokens=1400,
        temperature=float(control["temperature"]),
        top_p=float(control["top_p"]),
        top_k=int(control["top_k"]),
        verbose=False,
    )
    return collect_results(model, results)


def generate_moss(
    model: Any,
    sample: dict[str, Any],
    reference_path: Path,
    reference_text: str,
    tokenizer_snapshot: Path,
) -> tuple[np.ndarray, int]:
    control = sample["control"]
    results = model.generate(
        text=sample["target_text"],
        ref_audio=str(reference_path),
        ref_text=reference_text,
        mode="generation",
        instruction=control["instruction"],
        language=control["language"],
        max_tokens=4096,
        audio_tokenizer_source=str(tokenizer_snapshot),
        stream=False,
    )
    return collect_results(model, results)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--model", required=True, choices=sorted(SUPPORTED_MODELS))
    parser.add_argument("--group")
    parser.add_argument("--style", action="append")
    parser.add_argument("--identity", action="append")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()

    disable_optional_sklearn()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    manifest = json.loads(
        (evidence_root / "round1_internal_manifest.json").read_text(encoding="utf-8")
    )
    model_contract = next(
        item
        for item in manifest["model_contract"]["models"]
        if item["key"] == args.model
    )
    samples = [
        item
        for item in manifest["sample_specs"]
        if item["model_key"] == args.model
        and item["status"] == "pending_generation"
        and (not args.group or item["group"] == args.group)
        and (not args.style or item["style"] in set(args.style))
        and (not args.identity or item["identity_key"] in set(args.identity))
    ]
    if args.max_samples is not None:
        samples = samples[: args.max_samples]
    if not samples:
        print(json.dumps({"model": args.model, "sample_count": 0}, indent=2))
        return 0

    load_started = time.perf_counter()
    loaded: dict[str, Any] = {}
    snapshots: dict[str, str] = {}
    if args.model == "voxcpm2":
        loaded["main"], snapshot = load_model(
            "mlx-community/VoxCPM2-4bit",
            "dc9e5c187858da5f4a13dc4c247e297339216381",
        )
        snapshots["main"] = str(snapshot)
    elif args.model == "qwen3_tts":
        loaded["base"], base_snapshot = load_model(
            "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            "e7dd0585652209fa0d7783659aad4e8a324de11c",
        )
        snapshots["base"] = str(base_snapshot)
        if any(item["identity_key"] == "native_qwen_aiden" for item in samples):
            loaded["custom"], custom_snapshot = load_model(
                "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
                "41d3337e8b7f2843a75841595fc14e4b9a7a4b96",
            )
            snapshots["custom"] = str(custom_snapshot)
    elif args.model == "fish_s2_pro":
        loaded["main"], snapshot = load_model(
            "mlx-community/fish-audio-s2-pro",
            "eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
        )
        snapshots["main"] = str(snapshot)
    elif args.model == "moss_tts_local_v15":
        loaded["main"], snapshot = load_model(
            "OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5",
            "be7766a6735b98bd793f7c79fb720b4d0f5d13b8",
        )
        loaded["tokenizer_snapshot"] = exact_snapshot(
            "OpenMOSS-Team/MOSS-Audio-Tokenizer-v2",
            "f6e20e543b33d2c252a7ef71bdf8aa71e5ff9169",
        )
        snapshots["main"] = str(snapshot)
        snapshots["tokenizer"] = str(loaded["tokenizer_snapshot"])
    load_seconds = time.perf_counter() - load_started

    completed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reused_count = 0
    for index, sample in enumerate(samples, start=1):
        output = evidence_root / sample["output_file"]
        receipt = evidence_root / sample["result_file"]
        fingerprint = sample_fingerprint(sample, model_contract)
        if args.reuse_existing and existing_result_valid(output, receipt, fingerprint):
            reused_count += 1
            completed.append(json.loads(receipt.read_text(encoding="utf-8")))
            continue
        lock, became_ready = acquire_sample_lock(output, receipt, fingerprint)
        if became_ready:
            reused_count += 1
            completed.append(json.loads(receipt.read_text(encoding="utf-8")))
            continue
        if lock is None:
            failure = {
                "sample_id": sample["sample_id"],
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "error_type": "SampleLockTimeout",
                "error": "Timed out waiting for another process to finish this sample.",
            }
            failures.append(failure)
            print(json.dumps({"failure": failure}), flush=True)
            continue
        partial_output = output.with_name(
            output.stem + f".{os.getpid()}.partial" + output.suffix
        )
        partial_receipt = receipt.with_name(
            receipt.stem + f".{os.getpid()}.partial" + receipt.suffix
        )
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            partial_output.unlink(missing_ok=True)
            partial_receipt.unlink(missing_ok=True)
            reference_path, reference_text = resolve_reference(evidence_root, sample)
            mx.random.seed(int(sample["seed"]))
            started = time.perf_counter()
            if args.model == "voxcpm2":
                if reference_path is None:
                    raise ValueError("VoxCPM2 clone requires a reference.")
                audio, sample_rate = generate_voxcpm(
                    loaded["main"], sample, reference_path, evidence_root
                )
            elif args.model == "qwen3_tts":
                audio, sample_rate = generate_qwen(
                    loaded["base"],
                    loaded.get("custom"),
                    sample,
                    reference_path,
                    reference_text,
                )
            elif args.model == "fish_s2_pro":
                if reference_path is None or not reference_text:
                    raise ValueError("Fish cloning requires reference audio and text.")
                audio, sample_rate = generate_fish(
                    loaded["main"],
                    sample,
                    reference_path,
                    reference_text,
                    evidence_root,
                )
            else:
                if reference_path is None or not reference_text:
                    raise ValueError("MOSS cloning requires reference audio and text.")
                audio, sample_rate = generate_moss(
                    loaded["main"],
                    sample,
                    reference_path,
                    reference_text,
                    loaded["tokenizer_snapshot"],
                )
            generation_seconds = time.perf_counter() - started
            sf.write(partial_output, audio, sample_rate)
            metrics = audio_metrics(partial_output, sample["target_text"])
            record = {
                "schema_version": 1,
                "sample_id": sample["sample_id"],
                "blind_id": sample["blind_id"],
                "sample_fingerprint": fingerprint,
                "model_key": args.model,
                "model_label": sample["model_label"],
                "model_snapshots": snapshots,
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "group": sample["group"],
                "target_text_sha256": sample["target_text_sha256"],
                "reference_audio_sha256": sample["reference"].get(
                    "conditioning_sha256"
                ),
                "reference_text_sha256": sample["reference"].get(
                    "conditioning_transcript_sha256"
                ),
                "control": sample["control"],
                "seed": sample["seed"],
                "load_seconds_shared": load_seconds,
                "generation_seconds": generation_seconds,
                "real_time_factor": generation_seconds / metrics["duration_seconds"],
                "peak_rss_gib": peak_rss_gib(),
                "audio_file": str(output.relative_to(evidence_root)),
                "audio_sha256": sha256_file(partial_output),
                "audio": metrics,
                "post_generation_prosody_applied": False,
                "production_promotion_allowed": False,
            }
            partial_receipt.write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(partial_output, output)
            os.replace(partial_receipt, receipt)
            completed.append(record)
            print(
                json.dumps(
                    {
                        "index": index,
                        "count": len(samples),
                        "sample_id": sample["sample_id"],
                        "identity": sample["identity_key"],
                        "style": sample["style"],
                        "rtf": record["real_time_factor"],
                    }
                ),
                flush=True,
            )
        except BaseException as exc:
            failure = {
                "sample_id": sample["sample_id"],
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "error_type": type(exc).__name__,
                "error": str(exc)[:3000],
            }
            failures.append(failure)
            print(json.dumps({"failure": failure}), flush=True)
        finally:
            partial_output.unlink(missing_ok=True)
            partial_receipt.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)

    summary = {
        "schema_version": 1,
        "model_key": args.model,
        "group": args.group,
        "requested_sample_count": len(samples),
        "complete_count": len(completed),
        "generated_count": len(completed) - reused_count,
        "reused_count": reused_count,
        "failure_count": len(failures),
        "failures": failures,
        "load_seconds": load_seconds,
        "model_snapshots": snapshots,
        "production_promotion_allowed": False,
    }
    summaries = evidence_root / "generation-summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    slug = args.model + (f"-{args.group}" if args.group else "-selected")
    (summaries / f"{slug}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
