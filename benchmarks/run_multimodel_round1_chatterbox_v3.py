#!/usr/bin/env python3
"""Generate Round 1 Chatterbox Multilingual V3 samples on Apple Silicon.

Run with the isolated Chatterbox Python 3.11 environment and the pinned official
source tree on PYTHONPATH. This runner is evaluation-only. It loads V3 once,
uses the exact local snapshot, writes immutable per-sample receipts, and never
changes Alexandria production state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch

DEFAULT_EVIDENCE = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-audiobook.git-78fc5814/.omo/evidence/"
    "b17-t05-multimodel-round1"
)
DEFAULT_SOURCE = Path(
    "/Users/tristan/pinokio/cache/alexandria-evaluation/"
    "chatterbox-v3/source"
)
MODEL_REPO = "ResembleAI/chatterbox"
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
SOURCE_COMMIT = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
T3_MODEL = "v3"
ROUND_ID = "alexandria_multimodel_expressive_clone_round1_v1"


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


def repository_head(source: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise RuntimeError("Could not verify Chatterbox source commit.")
    return value


def exact_snapshot() -> Path:
    snapshot = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--ResembleAI--chatterbox"
        / "snapshots"
        / MODEL_REVISION
    ).resolve()
    required = (
        "ve.pt",
        "t3_mtl23ls_v3.safetensors",
        "s3gen.pt",
        "grapheme_mtl_merged_expanded_v1.json",
        "conds.pt",
        "Cangjie5_TC.json",
    )
    missing = [name for name in required if not (snapshot / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Chatterbox V3 snapshot is incomplete: {missing}")
    return snapshot


def load_v3(snapshot: Path):
    import perth
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    class NoopWatermarker:
        def apply_watermark(self, wav: Any, sample_rate: int) -> np.ndarray:
            del sample_rate
            return np.asarray(wav, dtype=np.float32)

    perth.PerthImplicitWatermarker = NoopWatermarker
    if not torch.backends.mps.is_available():
        raise RuntimeError("Chatterbox V3 Round 1 requires Apple-Silicon MPS.")
    device = torch.device("mps")
    original_load = torch.load

    def cpu_staged_load(*args: Any, **kwargs: Any):
        if kwargs.get("map_location") is None:
            kwargs["map_location"] = torch.device("cpu")
        return original_load(*args, **kwargs)

    torch.load = cpu_staged_load
    try:
        model = ChatterboxMultilingualTTS.from_local(
            snapshot,
            device,
            t3_model=T3_MODEL,
        )
    finally:
        torch.load = original_load
    return model, device


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


def sample_fingerprint(sample: dict[str, Any]) -> str:
    reference = sample["reference"]
    value = {
        "round_id": ROUND_ID,
        "sample_id": sample["sample_id"],
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "source_commit": SOURCE_COMMIT,
        "t3_model": T3_MODEL,
        "identity_key": sample["identity_key"],
        "style": sample["style"],
        "target_text_sha256": sample["target_text_sha256"],
        "reference_audio_sha256": reference.get("conditioning_sha256"),
        "control": sample["control"],
        "seed": sample["seed"],
        "runtime": {
            "device": "mps",
            "cpu_staged_checkpoint_load": True,
            "watermark_applied": False,
            "watermark_reason": "perth_backend_unavailable_on_macos",
            "temperature": 0.8,
            "repetition_penalty": 1.2,
            "min_p": 0.05,
            "top_p": 1.0,
        },
    }
    return sha256_text(canonical_json(value))


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
    timeout_seconds: float = 3600.0,
    stale_seconds: float = 7200.0,
) -> tuple[Path | None, bool]:
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
                payload = json.loads(lock.read_text(encoding="utf-8"))
                pid = int(payload.get("pid") or -1)
            except Exception:
                pid = -1
            alive = False
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if not alive or age > stale_seconds:
                lock.unlink(missing_ok=True)
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


def resolve_reference(evidence_root: Path, sample: dict[str, Any]) -> Path:
    value = sample["reference"].get("conditioning_file")
    if not value:
        raise ValueError(f"Chatterbox sample {sample['sample_id']} has no reference audio.")
    path = (evidence_root / "references" / value).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    expected = sample["reference"].get("conditioning_sha256")
    if expected and sha256_file(path) != expected:
        raise RuntimeError(f"Reference hash mismatch: {sample['sample_id']}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE))
    parser.add_argument("--group", action="append")
    parser.add_argument("--style", action="append")
    parser.add_argument("--identity", action="append")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    source_root = Path(args.source_root).expanduser().resolve()
    head = repository_head(source_root)
    if head != SOURCE_COMMIT:
        raise RuntimeError(
            f"Chatterbox source commit changed: expected {SOURCE_COMMIT}, found {head}."
        )
    manifest = json.loads(
        (evidence_root / "round1_internal_manifest.json").read_text(encoding="utf-8")
    )
    styles = set(args.style or [])
    identities = set(args.identity or [])
    samples = [
        item
        for item in manifest["sample_specs"]
        if item["model_key"] == "chatterbox_multilingual_v3"
        and item["status"] == "pending_generation"
        and (not args.group or item["group"] in set(args.group))
        and (not styles or item["style"] in styles)
        and (not identities or item["identity_key"] in identities)
    ]
    if args.max_samples is not None:
        samples = samples[: args.max_samples]
    if not samples:
        print(json.dumps({"model_key": "chatterbox_multilingual_v3", "sample_count": 0}))
        return 0

    snapshot = exact_snapshot()
    load_started = time.perf_counter()
    model, device = load_v3(snapshot)
    load_seconds = time.perf_counter() - load_started
    conditionals_cache: dict[str, Any] = {}

    completed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reused_count = 0
    for index, sample in enumerate(samples, start=1):
        output = evidence_root / sample["output_file"]
        receipt = evidence_root / sample["result_file"]
        fingerprint = sample_fingerprint(sample)
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
            reference = resolve_reference(evidence_root, sample)
            control = sample["control"]
            reference_key = str(
                sample["reference"].get("conditioning_sha256")
                or sha256_file(reference)
            )
            conditionals_cache_hit = reference_key in conditionals_cache
            conditioning_seconds = 0.0
            if conditionals_cache_hit:
                model.conds = conditionals_cache[reference_key]
            else:
                conditioning_started = time.perf_counter()
                model.prepare_conditionals(
                    str(reference),
                    exaggeration=float(control["exaggeration"]),
                )
                conditioning_seconds = time.perf_counter() - conditioning_started
                conditionals_cache[reference_key] = model.conds
            torch.manual_seed(int(sample["seed"]))
            started = time.perf_counter()
            wav = model.generate(
                sample["target_text"],
                language_id=str(control.get("language_id") or "en"),
                audio_prompt_path=None,
                exaggeration=float(control["exaggeration"]),
                cfg_weight=float(control["cfg_weight"]),
                temperature=0.8,
                repetition_penalty=1.2,
                min_p=0.05,
                top_p=1.0,
            )
            generation_seconds = time.perf_counter() - started
            audio = wav.detach().cpu().numpy().reshape(-1).astype(np.float32)
            sf.write(partial_output, audio, int(model.sr))
            metrics = audio_metrics(partial_output, sample["target_text"])
            record = {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "sample_id": sample["sample_id"],
                "blind_id": sample["blind_id"],
                "sample_fingerprint": fingerprint,
                "model_key": "chatterbox_multilingual_v3",
                "model_label": sample["model_label"],
                "model_repo": MODEL_REPO,
                "model_revision": MODEL_REVISION,
                "model_snapshot": str(snapshot),
                "source_repository": str(source_root),
                "source_commit": SOURCE_COMMIT,
                "t3_model": T3_MODEL,
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
                "conditioning_seconds": conditioning_seconds,
                "conditionals_cache_hit": conditionals_cache_hit,
                "generation_seconds": generation_seconds,
                "real_time_factor": generation_seconds / metrics["duration_seconds"],
                "peak_rss_gib": peak_rss_gib(),
                "audio_file": str(output.relative_to(evidence_root)),
                "audio_sha256": sha256_file(partial_output),
                "audio": metrics,
                "runtime_controls": {
                    "device": str(device),
                    "language_id": str(control.get("language_id") or "en"),
                    "exaggeration": float(control["exaggeration"]),
                    "cfg_weight": float(control["cfg_weight"]),
                    "temperature": 0.8,
                    "repetition_penalty": 1.2,
                    "min_p": 0.05,
                    "top_p": 1.0,
                    "semantic_instruction_directly_consumed": False,
                    "numeric_control_proxy": True,
                    "cpu_staged_checkpoint_load": True,
                    "watermark_applied": False,
                    "watermark_reason": "perth_backend_unavailable_on_macos",
                },
                "post_generation_prosody_applied": False,
                "manual_listening_required": True,
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
        "model_key": "chatterbox_multilingual_v3",
        "groups": sorted(set(args.group or [])),
        "requested_sample_count": len(samples),
        "complete_count": len(completed),
        "generated_count": len(completed) - reused_count,
        "reused_count": reused_count,
        "failure_count": len(failures),
        "failures": failures,
        "load_seconds": load_seconds,
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "source_commit": SOURCE_COMMIT,
        "t3_model": T3_MODEL,
        "watermark_applied": False,
        "production_promotion_allowed": False,
    }
    summaries = evidence_root / "generation-summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    group_slug = "-".join(sorted(set(args.group or [])))
    slug = "chatterbox_multilingual_v3" + (
        f"-{group_slug}" if group_slug else "-selected"
    )
    (summaries / f"{slug}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
