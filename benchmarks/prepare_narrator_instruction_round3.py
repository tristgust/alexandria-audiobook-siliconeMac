from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import soundfile as sf

from mlx_backend import MLXBackend


ROUND_ID = "alexandria_narrator_instruction_round3_v1"
ASSET_ROOT = Path(__file__).with_name("narrator_rescue_review_assets")
BASELINE_KEYS = (
    "qwen_lora_narrator_attention_r8",
    "indextts2",
    "voxcpm2",
)
NEW_CANDIDATE_KEY = "qwen_lora_narrator_context_per_record_r8"
CANDIDATE_LABELS = {
    "qwen_lora_narrator_attention_r8": "Qwen3-TTS Narrator identity-only Attention R8",
    "qwen_lora_narrator_context_per_record_r8": (
        "Qwen3-TTS Narrator per-record Attention R8"
    ),
    "indextts2": "IndexTTS2",
    "voxcpm2": "VoxCPM2",
}
CANDIDATE_STRATEGIES = {
    "qwen_lora_narrator_attention_r8": "identity_only_trained_adapter",
    "qwen_lora_narrator_context_per_record_r8": (
        "instruction_conditioned_trained_adapter"
    ),
    "indextts2": "controlled_clone",
    "voxcpm2": "controlled_clone",
}
REVIEW_FIELDS = [
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
    "flag_for_follow_up",
    "notes",
]


class Round3Error(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise Round3Error(f"Source file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def load_round2_data(round2_root: Path) -> dict[str, Any]:
    data_path = round2_root / "review" / "data.js"
    prefix = "window.ALEXANDRIA_NARRATOR_RESCUE_DATA = "
    text = data_path.read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise Round3Error("Round 2 public data has an unsupported format.")
    return json.loads(text[len(prefix) :].rstrip(";"))


def baseline_sources(round2_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    answer_key = read_json(round2_root / "answer-key.json")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in answer_key:
        key = row.get("candidate_key")
        style = row.get("style")
        if key not in BASELINE_KEYS or not style:
            continue
        sample_id = row["sample_id"]
        audio = round2_root / "review" / "audio" / f"{sample_id}.wav"
        result[(key, style)] = {**row, "audio_path": str(audio)}
    return result


def generate_new_samples(
    *,
    styles: list[dict[str, Any]],
    model_path: Path,
    destination: Path,
    force: bool,
) -> list[dict[str, Any]]:
    ref_audio = model_path / "ref_sample.wav"
    ref_text_path = model_path / "ref_sample.txt"
    if not ref_audio.is_file() or not ref_text_path.is_file():
        raise Round3Error("The exported model is missing its reference contract.")
    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    backend = MLXBackend(language="English")
    rows: list[dict[str, Any]] = []
    output_root = destination / "generated"
    receipt_root = destination / "generation-receipts"
    for style in styles:
        style_key = style["key"]
        output = output_root / f"{style_key}.wav"
        receipt_path = receipt_root / f"{style_key}.json"
        if output.is_file() and receipt_path.is_file() and not force:
            receipt = read_json(receipt_path)
            if receipt.get("audio_sha256") == sha256_file(output):
                rows.append(receipt)
                continue
        started = time.perf_counter()
        ok = backend.generate_merged_lora_clone(
            text=style["target_text"],
            ref_audio=str(ref_audio),
            ref_text=ref_text,
            instruct=style["instruction"],
            model_path=str(model_path),
            output_path=str(output),
            temperature=0.9,
            top_k=50,
            top_p=1.0,
            repetition_penalty=1.5,
            max_tokens=2000,
        )
        if not ok or not output.is_file():
            raise Round3Error(f"Generation failed for {style_key}.")
        info = sf.info(output)
        receipt = {
            "candidate_key": NEW_CANDIDATE_KEY,
            "candidate_label": CANDIDATE_LABELS[NEW_CANDIDATE_KEY],
            "strategy": CANDIDATE_STRATEGIES[NEW_CANDIDATE_KEY],
            "style": style_key,
            "target_text": style["target_text"],
            "instruction": style["instruction"],
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "sample_rate": info.samplerate,
            "channels": info.channels,
            "duration_seconds": round(info.duration, 4),
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "model_export_fingerprint": read_json(
                model_path / "mlx_export_manifest.json"
            )["export_fingerprint"],
        }
        write_json(receipt_path, receipt)
        rows.append(receipt)
    backend.release_models_manually()
    return rows


def package(
    *,
    round2_root: Path,
    model_path: Path,
    destination: Path,
    force_generation: bool,
) -> dict[str, Any]:
    round2_data = load_round2_data(round2_root)
    styles = round2_data["styles"]
    style_order = round2_data["style_order"]
    baselines = baseline_sources(round2_root)
    missing_baselines = [
        f"{candidate}/{style}"
        for style in style_order
        for candidate in BASELINE_KEYS
        if (candidate, style) not in baselines
    ]
    if missing_baselines:
        raise Round3Error(
            "Missing Round 2 baseline(s): " + ", ".join(missing_baselines)
        )

    destination.mkdir(parents=True, exist_ok=True)
    generated = generate_new_samples(
        styles=styles,
        model_path=model_path,
        destination=destination,
        force=force_generation,
    )
    generated_by_style = {row["style"]: row for row in generated}

    review_root = destination / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "reference-audio").mkdir(parents=True)
    for asset in ("index.html", "styles.css", "app.js"):
        copy_file(ASSET_ROOT / asset, review_root / asset)
    packaged_app = review_root / "app.js"
    packaged_app.write_text(
        packaged_app.read_text(encoding="utf-8")
        .replace(
            "alexandria:narrator-rescue:round2:",
            "alexandria:narrator-instruction:round3:",
        )
        .replace(
            "alexandria_narrator_rescue_round2_",
            "alexandria_narrator_instruction_round3_",
        ),
        encoding="utf-8",
    )
    copy_file(
        round2_root / "review" / "reference-audio" / "narrator-original.mp3",
        review_root / "reference-audio" / "narrator-original.mp3",
    )
    copy_file(
        round2_root
        / "review"
        / "reference-audio"
        / "narrator-conditioning.wav",
        review_root / "reference-audio" / "narrator-conditioning.wav",
    )

    answer_rows: list[dict[str, Any]] = []
    public_samples: list[dict[str, Any]] = []
    candidate_order = (*BASELINE_KEYS, NEW_CANDIDATE_KEY)
    for style in styles:
        style_key = style["key"]
        candidates: list[dict[str, Any]] = []
        for candidate_key in candidate_order:
            if candidate_key == NEW_CANDIDATE_KEY:
                source = generated_by_style[style_key]
                audio = Path(source["audio_path"])
                source_sample_id = None
            else:
                source = baselines[(candidate_key, style_key)]
                audio = Path(source["audio_path"])
                source_sample_id = source.get("sample_id")
            audio_hash = sha256_file(audio)
            sample_id = fingerprint(
                "|".join(
                    (
                        ROUND_ID,
                        candidate_key,
                        style_key,
                        audio_hash,
                    )
                )
            )
            candidates.append(
                {
                    "sample_id": sample_id,
                    "candidate_key": candidate_key,
                    "candidate_label": CANDIDATE_LABELS[candidate_key],
                    "strategy": CANDIDATE_STRATEGIES[candidate_key],
                    "style": style_key,
                    "audio": audio,
                    "audio_sha256": audio_hash,
                    "source_sample_id": source_sample_id,
                }
            )
        candidates.sort(
            key=lambda row: fingerprint(
                f"{ROUND_ID}|order|{style_key}|{row['sample_id']}",
                length=64,
            )
        )
        for candidate in candidates:
            target = review_root / "audio" / f"{candidate['sample_id']}.wav"
            copy_file(candidate["audio"], target)
            public_samples.append(
                {
                    "sample_id": candidate["sample_id"],
                    "style": style_key,
                    "style_label": style["label"],
                    "expected_identity": "Narrator",
                    "target_text": style["target_text"],
                    "requested_instruction": style["instruction"],
                    "audio": f"audio/{candidate['sample_id']}.wav",
                    "audio_sha256": candidate["audio_sha256"],
                    "status": "ready",
                }
            )
            answer_rows.append(
                {
                    "sample_id": candidate["sample_id"],
                    "candidate_key": candidate["candidate_key"],
                    "candidate_label": candidate["candidate_label"],
                    "strategy": candidate["strategy"],
                    "style": style_key,
                    "identity_key": "narrator",
                    "audio_sha256": candidate["audio_sha256"],
                    "source_round2_sample_id": candidate["source_sample_id"],
                }
            )

    public_data = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Alexandria Narrator Instruction Training — Blind Round 3",
        "identity": round2_data["identity"],
        "styles": styles,
        "style_order": style_order,
        "samples": public_samples,
        "review_fields": REVIEW_FIELDS,
        "candidate_count": len(public_samples),
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.ALEXANDRIA_NARRATOR_RESCUE_DATA = "
        + json.dumps(public_data, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    review_manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "review": "index.html",
        "identity_count": 1,
        "style_count": len(styles),
        "candidate_count": len(public_samples),
        "candidate_counts_by_style": {
            style: sum(row["style"] == style for row in public_samples)
            for style in style_order
        },
        "model_names_public": False,
        "answer_key_outside_review_root": True,
        "autosave": True,
        "partial_import_merge": True,
        "style_and_cumulative_exports": True,
        "keyboard_style_navigation": True,
        "production_promotion_allowed": False,
    }
    write_json(review_root / "manifest.json", review_manifest)
    write_json(destination / "answer-key.json", answer_rows)
    write_json(
        destination / "round3_internal_manifest.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "created_at": now_iso(),
            "round2_root": str(round2_root),
            "model_path": str(model_path),
            "model_export_fingerprint": read_json(
                model_path / "mlx_export_manifest.json"
            )["export_fingerprint"],
            "candidate_keys": list(candidate_order),
            "styles": style_order,
            "sample_count": len(public_samples),
        },
    )
    (review_root / "START_HERE.txt").write_text(
        "Alexandria Narrator Instruction Training — Blind Round 3\n"
        "=========================================================\n\n"
        "This is a four-way blind comparison using the same six lines as Round 2.\n"
        "Do not promote any candidate from this review alone.\n\n"
        "Terminal 1:\n"
        f"  cd \"{review_root}\"\n"
        "  python3 -m http.server 8772 --bind 127.0.0.1\n\n"
        "Terminal 2:\n"
        "  open \"http://127.0.0.1:8772/\"\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_samples),
        "candidate_counts_by_style": review_manifest[
            "candidate_counts_by_style"
        ],
        "answer_key": str(destination / "answer-key.json"),
    }


def validate(destination: Path) -> dict[str, Any]:
    review_root = destination / "review"
    data_path = review_root / "data.js"
    prefix = "window.ALEXANDRIA_NARRATOR_RESCUE_DATA = "
    text = data_path.read_text(encoding="utf-8").strip()
    data = json.loads(text[len(prefix) :].rstrip(";"))
    answer_key = read_json(destination / "answer-key.json")
    manifest = read_json(review_root / "manifest.json")
    if len(data["samples"]) != 24 or len(answer_key) != 24:
        raise Round3Error("Round 3 must contain exactly 24 samples.")
    if any(value != 4 for value in manifest["candidate_counts_by_style"].values()):
        raise Round3Error("Every style must contain exactly four candidates.")
    missing = []
    hash_errors = []
    sample_rates = set()
    channels = set()
    durations = []
    for row in data["samples"]:
        audio = review_root / row["audio"]
        if not audio.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != row["audio_sha256"]:
            hash_errors.append(row["sample_id"])
        info = sf.info(audio)
        sample_rates.add(info.samplerate)
        channels.add(info.channels)
        durations.append(info.duration)
    if missing or hash_errors:
        raise Round3Error(
            f"Review validation failed: missing={missing}, hashes={hash_errors}"
        )
    public_text = "\n".join(
        (review_root / name).read_text(encoding="utf-8", errors="ignore")
        for name in ("index.html", "styles.css", "app.js", "data.js", "manifest.json")
    )
    leaked = [
        label
        for label in CANDIDATE_LABELS.values()
        if label.casefold() in public_text.casefold()
    ]
    if leaked:
        raise Round3Error("Model label leakage: " + ", ".join(leaked))
    return {
        "round_id": ROUND_ID,
        "sample_count": len(data["samples"]),
        "style_count": len(data["styles"]),
        "candidate_counts": {
            key: sum(row["candidate_key"] == key for row in answer_key)
            for key in (*BASELINE_KEYS, NEW_CANDIDATE_KEY)
        },
        "sample_rates": sorted(sample_rates),
        "channels": sorted(channels),
        "minimum_duration_seconds": min(durations),
        "maximum_duration_seconds": max(durations),
        "model_name_leak_count": len(leaked),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the focused Narrator instruction-training Round 3 review."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--round2-root", required=True)
    build.add_argument("--model-path", required=True)
    build.add_argument("--destination", required=True)
    build.add_argument("--force-generation", action="store_true")
    check = sub.add_parser("validate")
    check.add_argument("--destination", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "build":
            result = package(
                round2_root=Path(args.round2_root).expanduser().resolve(),
                model_path=Path(args.model_path).expanduser().resolve(),
                destination=Path(args.destination).expanduser().resolve(),
                force_generation=bool(args.force_generation),
            )
        else:
            result = validate(Path(args.destination).expanduser().resolve())
    except (Round3Error, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
