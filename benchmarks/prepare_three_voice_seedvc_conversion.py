#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Iterable

import numpy as np
import soundfile as sf

BENCHMARKS = Path(__file__).resolve().parent
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from prepare_three_voice_openvoice_conversion import (
    ASSET_ROOT,
    MODE_ORDER,
    TARGET_ORDER,
    ConversionError,
    audio_metrics,
    cosine_torch,
    donor_rows,
    fingerprint,
    identity_rows,
    load_audio,
    load_json,
    normalize_text,
    now_iso,
    pitch_shape_similarity,
    sha256_file,
    text_similarity,
    write_json,
)

ROUND_ID = "alexandria_three_voice_seedvc_conversion_v1"


def safe_autocast(torch_module: Any):
    original = torch_module.autocast

    def factory(device_type: str, *args: Any, **kwargs: Any):
        if device_type == "mps":
            return nullcontext()
        return original(device_type, *args, **kwargs)

    return original, factory


def style_embedding(wrapper: Any, path: Path, device: Any) -> Any:
    import librosa
    import torch

    audio = librosa.load(str(path), sr=16000, mono=True)[0]
    tensor = torch.tensor(audio, dtype=torch.float32, device=device).unsqueeze(0)
    lengths = torch.tensor([tensor.shape[-1]], dtype=torch.int32, device=device)
    with torch.no_grad():
        return wrapper.compute_style(tensor, lengths).detach()


def chunk_style_embeddings(wrapper: Any, path: Path, device: Any, output_root: Path, sample_id: str) -> list[Any]:
    audio, sample_rate = load_audio(path)
    result = []
    with tempfile.TemporaryDirectory(prefix=f"seedvc-chunks-{sample_id}-", dir=str(output_root)) as temporary:
        root = Path(temporary)
        for index, chunk in enumerate(np.array_split(audio, 3)):
            if len(chunk) < max(2048, int(sample_rate * 0.35)):
                continue
            chunk_path = root / f"chunk_{index}.wav"
            sf.write(chunk_path, chunk, sample_rate, subtype="PCM_16")
            result.append(style_embedding(wrapper, chunk_path, device))
    return result


def install_cfm_compatibility(wrapper: Any) -> None:
    """Bridge the launcher wrapper to the installed Seed-VC CFM revision."""
    original = wrapper.cfm.inference

    def inference(
        mu: Any,
        x_lens: Any,
        prompt: Any,
        style: Any,
        n_timesteps: int = 10,
        temperature: float = 1.0,
        inference_cfg_rate: Any = 0.5,
        **_ignored: Any,
    ) -> Any:
        rate = inference_cfg_rate
        if isinstance(rate, (int, float)):
            # This pure timbre path needs speaker guidance, not the AR text
            # guidance exposed by the newer two-value CFM interface.
            rate = [0.0, float(rate)]
        return original(
            mu,
            x_lens,
            prompt,
            style,
            n_timesteps=n_timesteps,
            temperature=temperature,
            inference_cfg_rate=rate,
        )

    wrapper.cfm.inference = inference


def load_wrapper(seedvc_app: Path, device: Any) -> Any:
    import torch
    import yaml
    from hydra.utils import instantiate
    from omegaconf import DictConfig

    config_path = seedvc_app / "configs" / "v2" / "vc_wrapper.yaml"
    if not config_path.is_file():
        raise ConversionError(f"Seed-VC config is missing: {config_path}")
    cfg = DictConfig(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    previous_cwd = Path.cwd()
    try:
        # Seed-VC resolves its checkpoint cache relative to the process working
        # directory. Keep that cache inside the isolated Seed-VC app rather
        # than polluting whichever Alexandria worktree launched the benchmark.
        os.chdir(seedvc_app)
        wrapper = instantiate(cfg)
        wrapper.load_checkpoints(ar_checkpoint_path=None, cfm_checkpoint_path=None)
    finally:
        os.chdir(previous_cwd)
    install_cfm_compatibility(wrapper)
    wrapper.to(device)
    wrapper.eval()
    return wrapper


def convert(args: argparse.Namespace) -> dict[str, Any]:
    seedvc_app = Path(args.seedvc_app).expanduser().resolve()
    identity_manifest = Path(args.identity_manifest).expanduser().resolve()
    donor_review = Path(args.donor_review).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    for path in (seedvc_app, identity_manifest, donor_review):
        if not path.exists():
            raise ConversionError(f"Required path is missing: {path}")

    sys.path.insert(0, str(seedvc_app))
    import torch

    requested_device = args.device
    if requested_device == "auto":
        requested_device = "mps" if torch.backends.mps.is_available() else "cpu"
    device = torch.device(requested_device)
    dtype = torch.float32
    output_root.mkdir(parents=True, exist_ok=True)
    generated_root = output_root / "generated"
    receipt_root = output_root / "generation-receipts"
    generated_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)

    targets = identity_rows(identity_manifest)
    donors = donor_rows(donor_review)
    original_autocast, patched_autocast = safe_autocast(torch)
    torch.autocast = patched_autocast
    try:
        wrapper = load_wrapper(seedvc_app, device)
        identity_metric_audio = {
            key: item["conditioning_audio"]
            for key, item in targets.items()
        }
        if args.identity_metric_map:
            metric_map_path = Path(args.identity_metric_map).expanduser().resolve()
            if not metric_map_path.is_file():
                raise ConversionError(f"Identity metric map is missing: {metric_map_path}")
            metric_payload = load_json(metric_map_path)
            for key, value in metric_payload.items():
                if key not in targets:
                    raise ConversionError(f"Unknown identity metric target: {key}")
                path = Path(str(value)).expanduser().resolve()
                if not path.is_file():
                    raise ConversionError(f"Identity metric audio is missing for {key}: {path}")
                identity_metric_audio[key] = path
        canonical_target_embeddings = {
            key: style_embedding(wrapper, identity_metric_audio[key], device)
            for key in targets
        }
        conversion_target_variants: dict[str, list[tuple[str, Path]]] = {
            key: [(args.target_anchor, item[f"{args.target_anchor}_audio"])]
            for key, item in targets.items()
        }
        if args.target_anchor_map:
            anchor_map_path = Path(args.target_anchor_map).expanduser().resolve()
            if not anchor_map_path.is_file():
                raise ConversionError(f"Target anchor map is missing: {anchor_map_path}")
            payload = load_json(anchor_map_path)
            for key, values in payload.items():
                if key not in targets or not isinstance(values, list) or not values:
                    raise ConversionError(f"Invalid target anchor map entry: {key}")
                variants: list[tuple[str, Path]] = []
                for item in values:
                    label = str(item.get("label") or "").strip()
                    path = Path(str(item.get("audio") or "")).expanduser().resolve()
                    if not label or not path.is_file():
                        raise ConversionError(f"Invalid target anchor variant for {key}: {item}")
                    variants.append((label, path))
                conversion_target_variants[key] = variants
        donor_embeddings = {
            key: style_embedding(wrapper, item["audio"], device)
            for key, item in donors.items()
        }
        donor_metrics = {
            key: audio_metrics(item["audio"], len(item["text"].split()))
            for key, item in donors.items()
        }

        targets_requested = tuple(value.strip() for value in args.targets.split(",") if value.strip())
        modes_requested = tuple(value.strip() for value in args.modes.split(",") if value.strip())
        if any(value not in TARGET_ORDER for value in targets_requested):
            raise ConversionError("Unknown target in --targets")
        if any(value not in MODE_ORDER for value in modes_requested):
            raise ConversionError("Unknown mode in --modes")

        records = []
        for target_key in targets_requested:
            target = targets[target_key]
            for anchor_label, anchor_audio in conversion_target_variants[target_key]:
                for mode in modes_requested:
                    donor = donors[mode]
                    sample_id = fingerprint(
                        {
                            "round": ROUND_ID,
                            "target": target_key,
                            "mode": mode,
                            "steps": args.diffusion_steps,
                            "cfg": args.inference_cfg_rate,
                            "seed": args.seed,
                            "target_anchor": anchor_label,
                            "target_anchor_sha256": sha256_file(anchor_audio),
                        }
                    )
                    output = generated_root / f"{sample_id}.wav"
                    receipt_path = receipt_root / f"{sample_id}.json"
                    if output.is_file() and receipt_path.is_file() and not args.force:
                        receipt = load_json(receipt_path)
                        if receipt.get("audio_sha256") == sha256_file(output):
                            records.append(receipt)
                            continue

                    torch.manual_seed(int(args.seed))
                    if device.type == "mps":
                        torch.mps.manual_seed(int(args.seed))
                    started = time.perf_counter()
                    converted = wrapper.convert_timbre(
                        source_audio_path=str(donor["audio"]),
                        target_audio_path=str(anchor_audio),
                        diffusion_steps=int(args.diffusion_steps),
                        length_adjust=1.0,
                        inference_cfg_rate=float(args.inference_cfg_rate),
                        use_sway_sampling=False,
                        use_amo_sampling=False,
                        device=device,
                        dtype=dtype,
                    )
                    waveform = np.asarray(converted, dtype=np.float32).reshape(-1)
                    if not waveform.size:
                        raise ConversionError(f"Seed-VC produced empty audio for {target_key}/{mode}")
                    sf.write(output, waveform, int(wrapper.sr), subtype="PCM_16")

                    output_embedding = style_embedding(wrapper, output, device)
                    thirds = chunk_style_embeddings(wrapper, output, device, output_root, sample_id)
                    whole_identity = cosine_torch(output_embedding, canonical_target_embeddings[target_key])
                    third_identity = [cosine_torch(value, canonical_target_embeddings[target_key]) for value in thirds]
                    donor_retention = cosine_torch(output_embedding, donor_embeddings[mode])
                    metrics = audio_metrics(output, len(donor["text"].split()))
                    shape_similarity = pitch_shape_similarity(metrics, donor_metrics[mode])
                    receipt = {
                    "schema_version": 1,
                    "round_id": ROUND_ID,
                    "sample_id": sample_id,
                    "candidate": "seed_vc_v2_timbre",
                    "target_key": target_key,
                    "target_label": target["label"],
                    "mode": mode,
                    "mode_label": donor["label"],
                    "expected_text": donor["text"],
                    "target_reference": str(target["conditioning_audio"]),
                    "target_reference_sha256": sha256_file(target["conditioning_audio"]),
                    "identity_metric_reference": str(identity_metric_audio[target_key]),
                    "identity_metric_sha256": sha256_file(identity_metric_audio[target_key]),
                    "target_anchor": anchor_label,
                    "target_anchor_reference": str(anchor_audio),
                    "target_anchor_sha256": sha256_file(anchor_audio),
                    "donor_audio": str(donor["audio"]),
                    "donor_audio_sha256": sha256_file(donor["audio"]),
                    "audio_path": str(output),
                    "audio_sha256": sha256_file(output),
                    "device": device.type,
                    "dtype": str(dtype),
                    "diffusion_steps": int(args.diffusion_steps),
                    "inference_cfg_rate": float(args.inference_cfg_rate),
                    "seed": int(args.seed),
                    "generation_seconds": round(time.perf_counter() - started, 4),
                    "whole_identity_cosine": round(whole_identity, 6),
                    "third_identity_cosines": [round(value, 6) for value in third_identity],
                    "minimum_third_identity_cosine": round(min(third_identity) if third_identity else whole_identity, 6),
                    "donor_timbre_retention_cosine": round(donor_retention, 6),
                    "pitch_shape_similarity_to_donor": round(shape_similarity, 6),
                    "audio_metrics": metrics,
                    "donor_metrics": donor_metrics[mode],
                    "manual_listening_required": True,
                    "production_promotion_allowed": False,
                }
                    write_json(receipt_path, receipt)
                    records.append(receipt)

        summary = {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "created_at": now_iso(),
            "seedvc_app": str(seedvc_app),
            "device": device.type,
            "diffusion_steps": int(args.diffusion_steps),
            "inference_cfg_rate": float(args.inference_cfg_rate),
            "seed": int(args.seed),
            "target_anchor": args.target_anchor,
            "sample_count": len(records),
            "samples": records,
            "production_promotion_allowed": False,
        }
        write_json(output_root / "generation-summary.json", summary)
        return {"sample_count": len(records), "summary": str(output_root / "generation-summary.json")}
    finally:
        torch.autocast = original_autocast
        if device.type == "mps":
            torch.mps.empty_cache()


def package(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    summary = load_json(output_root / "generation-summary.json")
    if not whisper_model.is_dir():
        raise ConversionError(f"Whisper model is missing: {whisper_model}")
    import mlx_whisper

    analyzed = []
    for row in summary["samples"]:
        asr = mlx_whisper.transcribe(
            row["audio_path"],
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(asr.get("text") or "").strip()
        similarity = text_similarity(row["expected_text"], transcript)
        expected_words = normalize_text(row["expected_text"]).split()
        actual_words = normalize_text(transcript).split()
        final_word_matches = bool(expected_words and actual_words and expected_words[-1] == actual_words[-1])
        duration_ratio = row["audio_metrics"]["duration_seconds"] / max(row["donor_metrics"]["duration_seconds"], 0.01)
        hard_pass = (
            similarity >= 0.92
            and final_word_matches
            and row["whole_identity_cosine"] >= float(args.identity_floor)
            and row["minimum_third_identity_cosine"] >= float(args.third_identity_floor)
            and row["pitch_shape_similarity_to_donor"] >= 0.55
            and not row["audio_metrics"]["pitch_trajectory_anomaly"]
            and row["audio_metrics"]["clipping_fraction"] < 0.001
            and 0.82 <= duration_ratio <= 1.18
        )
        selection_score = (
            float(row["whole_identity_cosine"]) * 4.0
            + float(row["minimum_third_identity_cosine"]) * 3.0
            + float(row["pitch_shape_similarity_to_donor"]) * 2.0
            + similarity * 2.0
            + (0.75 if hard_pass else -0.5)
            - abs(duration_ratio - 1.0)
        )
        analyzed.append(
            {
                **row,
                "automatic_transcript": transcript,
                "text_similarity": round(similarity, 6),
                "final_word_matches": final_word_matches,
                "duration_ratio_to_donor": round(duration_ratio, 6),
                "technical_pass": hard_pass,
                "selection_score": round(selection_score, 6),
            }
        )

    winners = []
    for target_key in TARGET_ORDER:
        target_candidates = [row for row in analyzed if row["target_key"] == target_key]
        if not target_candidates:
            continue
        for mode in MODE_ORDER:
            candidates = [row for row in target_candidates if row["mode"] == mode]
            if not candidates:
                continue
            passing = [row for row in candidates if row["technical_pass"]]
            winners.append(max(passing or candidates, key=lambda row: row["selection_score"]))

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "targets").mkdir(parents=True)
    (review_root / "donors").mkdir(parents=True)
    public_rows = []
    copied_targets: set[str] = set()
    copied_donors: set[str] = set()
    for ordinal, row in enumerate(winners, 1):
        target_name = f"{row['target_key']}.wav"
        donor_name = f"{row['mode']}.wav"
        audio_name = f"{row['sample_id']}.wav"
        if target_name not in copied_targets:
            shutil.copy2(row["target_reference"], review_root / "targets" / target_name)
            copied_targets.add(target_name)
        if donor_name not in copied_donors:
            shutil.copy2(row["donor_audio"], review_root / "donors" / donor_name)
            copied_donors.add(donor_name)
        shutil.copy2(row["audio_path"], review_root / "audio" / audio_name)
        public_rows.append(
            {
                "sample_id": row["sample_id"],
                "ordinal": ordinal,
                "target_key": row["target_key"],
                "target_label": row["target_label"],
                "mode": row["mode"],
                "mode_label": row["mode_label"],
                "expected_text": row["expected_text"],
                "target_audio": f"targets/{target_name}",
                "donor_audio": f"donors/{donor_name}",
                "converted_audio": f"audio/{audio_name}",
                "technical_pass": row["technical_pass"],
                "automatic_transcript": row["automatic_transcript"],
            }
        )

    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Three-voice Seed-VC performance conversion proof",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "target_order": TARGET_ORDER,
        "mode_order": MODE_ORDER,
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.THREE_VOICE_OPENVOICE_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    write_json(
        review_root / "manifest.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "candidate_count": len(public_rows),
            "target_count": len(TARGET_ORDER),
            "mode_count": len(MODE_ORDER),
            "answer_key_outside_review_root": True,
            "production_promotion_allowed": False,
        },
    )
    write_json(output_root / "answer-key.json", winners)
    write_json(
        output_root / "analysis.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "sample_count": len(analyzed),
            "technical_pass_count": sum(row["technical_pass"] for row in analyzed),
            "winner_technical_pass_count": sum(row["technical_pass"] for row in winners),
            "samples": analyzed,
            "winners": [row["sample_id"] for row in winners],
        },
    )
    (output_root / "START_HERE.txt").write_text(
        "Three-voice Seed-VC performance conversion proof\n"
        "===============================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8777 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8777/\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "technical_pass_count": sum(row["technical_pass"] for row in winners),
        "analyzed_count": len(analyzed),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.THREE_VOICE_OPENVOICE_DATA = "
    data_text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    data = json.loads(data_text[len(prefix) :].rstrip(";"))
    answers = {row["sample_id"]: row for row in load_json(output_root / "answer-key.json")}
    missing = []
    hash_errors = []
    for row in data["rows"]:
        audio = review_root / row["converted_audio"]
        target = review_root / row["target_audio"]
        donor = review_root / row["donor_audio"]
        if not audio.is_file() or not target.is_file() or not donor.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != answers[row["sample_id"]]["audio_sha256"]:
            hash_errors.append(row["sample_id"])
    if missing or hash_errors:
        raise ConversionError(f"Validation failed: missing={missing}, hash_errors={hash_errors}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(data["rows"]),
        "missing_count": len(missing),
        "hash_error_count": len(hash_errors),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded three-character Seed-VC timbre conversion proof.")
    sub = parser.add_subparsers(dest="command", required=True)
    convert_parser = sub.add_parser("convert")
    convert_parser.add_argument("--seedvc-app", required=True)
    convert_parser.add_argument("--identity-manifest", required=True)
    convert_parser.add_argument("--donor-review", required=True)
    convert_parser.add_argument("--output-root", required=True)
    convert_parser.add_argument("--device", default="auto", choices=("auto", "mps", "cpu"))
    convert_parser.add_argument("--diffusion-steps", type=int, default=20)
    convert_parser.add_argument("--inference-cfg-rate", type=float, default=0.7)
    convert_parser.add_argument("--seed", type=int, default=20260724)
    convert_parser.add_argument(
        "--target-anchor",
        choices=("conditioning", "source"),
        default="conditioning",
    )
    convert_parser.add_argument(
        "--target-anchor-map",
        help="Optional JSON mapping target keys to labeled custom anchor audio files.",
    )
    convert_parser.add_argument(
        "--identity-metric-map",
        help="Optional JSON mapping target keys to identity-bank audio used only for scoring.",
    )
    convert_parser.add_argument("--targets", default=",".join(TARGET_ORDER))
    convert_parser.add_argument("--modes", default=",".join(MODE_ORDER))
    convert_parser.add_argument("--force", action="store_true")
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--output-root", required=True)
    package_parser.add_argument("--whisper-model", required=True)
    package_parser.add_argument("--identity-floor", type=float, default=0.75)
    package_parser.add_argument("--third-identity-floor", type=float, default=0.68)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "convert":
            result = convert(args)
        elif args.command == "package":
            result = package(args)
        else:
            result = validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
