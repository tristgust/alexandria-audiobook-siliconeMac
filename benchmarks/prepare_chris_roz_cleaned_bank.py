#!/usr/bin/env python3
"""Prepare the post-review Chris Cwej and Roz Forrester reference bank.

Run this script from the isolated cleanup environment with Alexandria's proven
scientific stack and the isolated SpeechBrain package on PYTHONPATH. It repairs
reviewed trim boundaries, applies the evidence-selected cleanup method, selects
the target stream for the one overlapping-voice clip, and validates text and
speaker identity before building the next-round reference bank.

This is research-only. It never mutates Alexandria Voice assignments or
production audio.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping

import mlx_whisper
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
import torch
from speechbrain.inference.classifiers import EncoderClassifier

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "benchmarks/chris_roz_cleanup_candidates.json"
SOURCE_CONFIG_PATH = ROOT / "benchmarks/chris_roz_reference_sources.json"
POSTREVIEW_PATH = ROOT / ".omo/evidence/chris-roz-postreview-v1/human-review-summary.json"
DEFAULT_OUTPUT_ROOT = ROOT / ".omo/evidence/chris-roz-cleanup-v1"
ECAPA_MODEL = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-audiobook.git-c2133ab9/.omo/evidence/"
    "cwej-roz-voice-evaluation/private/models/"
    "spkrec-ecapa-voxceleb-0f99f2d0"
)
SEED_ROOT = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-audiobook.git-c2133ab9/.omo/evidence/"
    "cwej-roz-voice-evaluation/private/retrieval/seeds"
)
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"


class CleanupError(RuntimeError):
    """Raised when a cleaned reference cannot be produced truthfully."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise CleanupError(f"Required JSON file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run(command: list[str], *, timeout: int = 300) -> None:
    subprocess.run(command, check=True, timeout=timeout)


def ffmpeg_extract(source: Path, target: Path, start: float, end: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )


def ffmpeg_normalize(source: Path, target: Path, sample_rate: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )


def source_paths(source_config: Mapping[str, Any]) -> dict[str, Path]:
    root = Path(str(source_config["source_root"])).expanduser().resolve()
    result = {
        str(row["key"]): (root / str(row["audio"])).resolve()
        for row in source_config["sources"]
    }
    online_root = Path(str(source_config["online_root"])).expanduser().resolve()
    for path in online_root.glob("*"):
        if path.is_file():
            result[path.name] = path.resolve()
    return result


def resolve_source(candidate: Mapping[str, Any], sources: Mapping[str, Path]) -> Path:
    if candidate.get("source_kind") == "online_audio":
        key = str(candidate["audio"])
    else:
        key = str(candidate["source_key"])
    source = sources.get(key)
    if source is None or not source.is_file():
        raise CleanupError(f"Source audio is missing for {candidate['id']}: {key}")
    return source


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(
        str(path),
        dtype="float32",
        always_2d=True,
    )
    mono = np.mean(audio, axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64))) if mono.size else 0.0
    clipping = float(np.mean(np.abs(mono) >= 0.995)) if mono.size else 0.0
    return {
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "frames": int(audio.shape[0]),
        "duration_seconds": float(audio.shape[0] / sample_rate),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-9)),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-9)),
        "clipping_fraction": clipping,
    }


_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
}


def normalized_words(text: str) -> list[str]:
    # Whisper may render the same spoken percentage as either "eighty percent"
    # or "80%". Normalize that orthographic difference without weakening the
    # comparison for ordinary words or larger number phrases.
    tokens = re.findall(r"[a-z0-9']+", text.casefold().replace("%", " percent"))
    return [_NUMBER_WORDS.get(token, token) for token in tokens]


def word_error_rate(expected: str, observed: str) -> float:
    left = normalized_words(expected)
    right = normalized_words(observed)
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for right_index, right_word in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_word != right_word),
                )
            )
        previous = current
    return previous[-1] / max(1, len(left))


def load_16k(path: Path) -> torch.Tensor:
    audio, sample_rate = sf.read(
        str(path),
        dtype="float32",
        always_2d=True,
    )
    mono = np.mean(audio, axis=1)
    if sample_rate != 16000:
        mono = resample_poly(mono, 16000, sample_rate).astype(np.float32)
    return torch.from_numpy(mono)[None, :]


def normalized_embedding(model: EncoderClassifier, path: Path) -> np.ndarray:
    with torch.no_grad():
        vector = model.encode_batch(load_16k(path)).squeeze().cpu().numpy()
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    return vector / (np.linalg.norm(vector) + 1e-12)


def average_embeddings(vectors: Iterable[np.ndarray]) -> np.ndarray:
    values = list(vectors)
    if not values:
        raise CleanupError("No speaker seed embeddings were available.")
    result = np.mean(np.stack(values), axis=0)
    return result / (np.linalg.norm(result) + 1e-12)


class CleanupRuntime:
    def __init__(self, output_root: Path, sample_rate: int) -> None:
        self.output_root = output_root
        self.sample_rate = sample_rate
        self._enhancer: Any | None = None
        self._separator: Any | None = None
        self._speaker_model = EncoderClassifier.from_hparams(source=str(ECAPA_MODEL))
        self._speaker_seeds = {
            "chris": average_embeddings(
                normalized_embedding(self._speaker_model, path)
                for path in sorted((SEED_ROOT / "travis_oliver").glob("*.wav"))
            ),
            "roz": average_embeddings(
                normalized_embedding(self._speaker_model, path)
                for path in sorted((SEED_ROOT / "yasmin_bannerman").glob("*.wav"))
            ),
        }

    @property
    def enhancer(self) -> Any:
        if self._enhancer is None:
            from clearvoice import ClearVoice

            self._enhancer = ClearVoice(
                task="speech_enhancement",
                model_names=["MossFormer2_SE_48K"],
            )
        return self._enhancer

    @property
    def separator(self) -> Any:
        if self._separator is None:
            from clearvoice import ClearVoice

            self._separator = ClearVoice(
                task="speech_separation",
                model_names=["MossFormer2_SS_16K"],
            )
        return self._separator

    def speaker_cosine(self, identity: str, path: Path) -> float:
        vector = normalized_embedding(self._speaker_model, path)
        return float(np.dot(self._speaker_seeds[identity], vector))

    def demucs_vocals(self, candidate_id: str, source: Path, intermediate: Path) -> Path:
        root = intermediate / "demucs"
        track = source.stem
        expected = root / "htdemucs" / track / "vocals.wav"
        if not expected.is_file():
            run(
                [
                    sys.executable,
                    "-m",
                    "demucs",
                    "-n",
                    "htdemucs",
                    "--two-stems=vocals",
                    "-d",
                    "cpu",
                    "--shifts",
                    "0",
                    "-o",
                    str(root),
                    str(source),
                ]
            )
        if not expected.is_file():
            raise CleanupError(f"Demucs returned no vocal stem for {candidate_id}.")
        return expected

    def clearvoice_enhanced(self, candidate_id: str, source: Path, intermediate: Path) -> Path:
        target = intermediate / "clearvoice" / f"{candidate_id}.wav"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            output = self.enhancer(input_path=str(source), online_write=False)
            self.enhancer.write(output, output_path=str(target))
        if not target.is_file():
            raise CleanupError(f"ClearVoice returned no enhanced audio for {candidate_id}.")
        return target

    def separated_target(self, candidate_id: str, identity: str, source: Path, intermediate: Path) -> tuple[Path, list[dict[str, Any]]]:
        root = intermediate / "separation" / candidate_id
        first = root / "separated_s1.wav"
        second = root / "separated_s2.wav"
        if not first.is_file() or not second.is_file():
            root.mkdir(parents=True, exist_ok=True)
            output = self.separator(input_path=str(source), online_write=False)
            self.separator.write(output, output_path=str(root / "separated.wav"))
        streams = [path for path in (first, second) if path.is_file()]
        if len(streams) != 2:
            raise CleanupError(f"Speech separation returned {len(streams)} streams for {candidate_id}.")
        ranked = sorted(
            (
                {
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "speaker_cosine": self.speaker_cosine(identity, path),
                }
                for path in streams
            ),
            key=lambda row: row["speaker_cosine"],
            reverse=True,
        )
        if ranked[0]["speaker_cosine"] - ranked[1]["speaker_cosine"] < 0.15:
            raise CleanupError(
                f"Target speaker separation is ambiguous for {candidate_id}: {ranked}"
            )
        return Path(ranked[0]["path"]), ranked


def transcript_measurement(path: Path, expected: str) -> dict[str, Any]:
    result = mlx_whisper.transcribe(
        str(path),
        path_or_hf_repo=WHISPER_MODEL,
        language="en",
        condition_on_previous_text=False,
        word_timestamps=False,
        verbose=False,
    )
    observed = str(result.get("text") or "").strip()
    return {
        "model": WHISPER_MODEL,
        "expected": expected,
        "observed": observed,
        "word_error_rate": word_error_rate(expected, observed),
        "exact_normalized_text": normalized_words(expected) == normalized_words(observed),
    }


def process_candidate(
    *,
    candidate: Mapping[str, Any],
    sources: Mapping[str, Path],
    runtime: CleanupRuntime,
    output_root: Path,
    config_fingerprint: str,
) -> dict[str, Any]:
    candidate_id = str(candidate["id"])
    receipt_path = output_root / "receipts" / f"{candidate_id}.json"
    source = resolve_source(candidate, sources)
    source_hash = sha256_file(source)
    contract = {
        "config_fingerprint": config_fingerprint,
        "candidate": candidate,
        "source_sha256": source_hash,
        "whisper_model": WHISPER_MODEL,
        "speaker_model": "speechbrain/spkrec-ecapa-voxceleb@0f99f2d0",
    }
    fingerprint = sha256_value(contract)
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        final = Path(str(receipt.get("final_path") or ""))
        raw = Path(str(receipt.get("raw_path") or ""))
        if (
            receipt.get("fingerprint") == fingerprint
            and final.is_file()
            and raw.is_file()
            and receipt.get("final_sha256") == sha256_file(final)
            and receipt.get("raw_sha256") == sha256_file(raw)
        ):
            return receipt

    raw = output_root / "private/raw" / f"{candidate_id}.wav"
    ffmpeg_extract(
        source,
        raw,
        float(candidate["start_seconds"]),
        float(candidate["end_seconds"]),
    )
    raw_normalized = output_root / "private/raw-normalized" / f"{candidate_id}.wav"
    ffmpeg_normalize(raw, raw_normalized, runtime.sample_rate)
    raw_cosine = runtime.speaker_cosine(str(candidate["identity"]), raw_normalized)
    method = str(candidate["cleanup_method"])
    intermediate_root = output_root / "private/intermediate" / candidate_id
    stream_ranking: list[dict[str, Any]] = []
    if method == "boundary_repaired_raw":
        prepared = raw
    elif method == "clearvoice_enhanced":
        prepared = runtime.clearvoice_enhanced(candidate_id, raw, intermediate_root)
    elif method == "demucs_vocals":
        prepared = runtime.demucs_vocals(candidate_id, raw, intermediate_root)
    elif method == "speech_separation_target":
        prepared, stream_ranking = runtime.separated_target(
            candidate_id,
            str(candidate["identity"]),
            raw,
            intermediate_root,
        )
    else:
        raise CleanupError(f"Unknown cleanup method for {candidate_id}: {method}")

    final = output_root / "cleaned" / f"{candidate_id}.wav"
    ffmpeg_normalize(prepared, final, runtime.sample_rate)
    final_cosine = runtime.speaker_cosine(str(candidate["identity"]), final)
    transcript = transcript_measurement(final, str(candidate["transcript"]))
    raw_metrics = audio_metrics(raw_normalized)
    final_metrics = audio_metrics(final)
    identity_delta = final_cosine - raw_cosine
    identity_pass = identity_delta >= -0.08
    text_pass = transcript["word_error_rate"] <= 0.08
    manual_review_required = (
        not identity_pass
        or not text_pass
        or "required" in str(candidate.get("transcript_status") or "")
    )
    receipt = {
        "schema_version": 1,
        "cleanup_id": "alexandria_chris_roz_cleanup_v1",
        "fingerprint": fingerprint,
        "candidate": dict(candidate),
        "source_path": str(source),
        "source_sha256": source_hash,
        "raw_path": str(raw_normalized),
        "raw_sha256": sha256_file(raw_normalized),
        "final_path": str(final),
        "final_sha256": sha256_file(final),
        "cleanup_method": method,
        "raw_metrics": raw_metrics,
        "final_metrics": final_metrics,
        "speaker": {
            "model": "speechbrain/spkrec-ecapa-voxceleb@0f99f2d0",
            "raw_cosine": raw_cosine,
            "final_cosine": final_cosine,
            "delta": identity_delta,
            "pass": identity_pass,
        },
        "transcript": transcript,
        "separation_streams": stream_ranking,
        "manual_review_required": manual_review_required,
        "production_promotion_allowed": False,
        "completed_at": utc_now(),
    }
    write_json(receipt_path, receipt)
    return receipt


def human_score_map(postreview: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["logical_id"]): row
        for row in postreview["source_review"]["all_scored_candidates"]
    }


def canonical_rank(
    identity: str,
    candidate_ids: list[str],
    receipts: Mapping[str, Mapping[str, Any]],
    human_scores: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for candidate_id in candidate_ids:
        receipt = receipts[candidate_id]
        ratings = human_scores[candidate_id]["ratings"]
        rows.append(
            {
                "candidate_id": candidate_id,
                "final_path": receipt["final_path"],
                "final_sha256": receipt["final_sha256"],
                "transcript": receipt["candidate"]["transcript"],
                "speaker_cosine": receipt["speaker"]["final_cosine"],
                "word_error_rate": receipt["transcript"]["word_error_rate"],
                "human_identity": ratings.get("identity") or 0,
                "human_usefulness": ratings.get("usefulness") or 0,
                "manual_review_required": receipt["manual_review_required"],
            }
        )
    rows.sort(
        key=lambda row: (
            not row["manual_review_required"],
            row["human_identity"],
            row["human_usefulness"],
            -row["word_error_rate"],
            row["speaker_cosine"],
        ),
        reverse=True,
    )
    return rows


def build_reference_bank(
    config: Mapping[str, Any],
    selection: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
    postreview: Mapping[str, Any],
) -> dict[str, Any]:
    human_scores = human_score_map(postreview)
    by_id = {str(row["id"]): row for row in config["candidates"]}
    identities: dict[str, Any] = {}
    for identity, identity_selection in selection["identity_references"].items():
        clean_id = str(identity_selection["clean_actor_primary"])
        canonical_ids = list(map(str, identity_selection["canonical_candidates"]))
        ranked = canonical_rank(
            identity,
            canonical_ids,
            receipts,
            human_scores,
        )
        identities[identity] = {
            "clean_actor": {
                "candidate_id": clean_id,
                "audio_path": receipts[clean_id]["final_path"],
                "audio_sha256": receipts[clean_id]["final_sha256"],
                "transcript": by_id[clean_id]["transcript"],
                "manual_review_required": receipts[clean_id]["manual_review_required"],
            },
            "canonical_cleaned": ranked[0],
            "canonical_alternates": ranked[1:],
        }
    performance = {
        identity: [
            {
                "candidate_id": candidate_id,
                "roles": by_id[candidate_id]["roles"],
                "audio_path": receipts[candidate_id]["final_path"],
                "audio_sha256": receipts[candidate_id]["final_sha256"],
                "transcript": by_id[candidate_id]["transcript"],
                "manual_review_required": receipts[candidate_id]["manual_review_required"],
            }
            for candidate_id in candidate_ids
        ]
        for identity, candidate_ids in selection["performance_bank"].items()
    }
    return {
        "schema_version": 1,
        "bank_id": "alexandria_chris_roz_cleaned_reference_bank_v1",
        "generated_at": utc_now(),
        "identity_references": identities,
        "performance_bank": performance,
        "tnia_miller_included": False,
        "production_assignment_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--source-config", type=Path, default=SOURCE_CONFIG_PATH)
    parser.add_argument("--postreview", type=Path, default=POSTREVIEW_PATH)
    parser.add_argument("--selection", type=Path, default=ROOT / "benchmarks/chris_roz_postreview_selection.json")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--candidate")
    args = parser.parse_args()

    config = read_json(args.config.expanduser().resolve())
    source_config = read_json(args.source_config.expanduser().resolve())
    postreview = read_json(args.postreview.expanduser().resolve())
    selection = read_json(args.selection.expanduser().resolve())
    if config.get("schema_version") != 1:
        raise CleanupError("Unsupported cleanup configuration schema.")
    if selection.get("tnia_miller", {}).get("downstream_allowed") is not False:
        raise CleanupError("T'Nia downstream use must remain disabled.")
    if any(str(row["id"]).startswith("tnia_") for row in config["candidates"]):
        raise CleanupError("Cleanup configuration must not contain T'Nia candidates.")

    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    sources = source_paths(source_config)
    runtime = CleanupRuntime(output_root, int(config["policy"]["sample_rate"]))
    config_fingerprint = sha256_value(config)
    candidates = [
        row
        for row in config["candidates"]
        if args.candidate is None or row["id"] == args.candidate
    ]
    if not candidates:
        raise CleanupError(f"No matching cleanup candidate: {args.candidate}")

    receipts: dict[str, Mapping[str, Any]] = {}
    for index, candidate in enumerate(candidates, start=1):
        receipt = process_candidate(
            candidate=candidate,
            sources=sources,
            runtime=runtime,
            output_root=output_root,
            config_fingerprint=config_fingerprint,
        )
        receipts[str(candidate["id"])] = receipt
        print(
            f"[{index}/{len(candidates)}] {candidate['id']}: "
            f"{receipt['cleanup_method']} WER={receipt['transcript']['word_error_rate']:.3f} "
            f"speaker_delta={receipt['speaker']['delta']:+.3f}",
            flush=True,
        )

    if args.candidate is None:
        bank = build_reference_bank(
            config,
            selection,
            receipts,
            postreview,
        )
        bank_path = output_root / "reference-bank.json"
        write_json(bank_path, bank)
        manifest = {
            "schema_version": 1,
            "cleanup_id": config["cleanup_id"],
            "generated_at": utc_now(),
            "candidate_count": len(receipts),
            "methods": dict(sorted(defaultdict(int, {
                method: sum(1 for row in receipts.values() if row["cleanup_method"] == method)
                for method in {row["cleanup_method"] for row in receipts.values()}
            }).items())),
            "exact_text_count": sum(
                1 for row in receipts.values() if row["transcript"]["exact_normalized_text"]
            ),
            "identity_pass_count": sum(1 for row in receipts.values() if row["speaker"]["pass"]),
            "manual_review_required_count": sum(
                1 for row in receipts.values() if row["manual_review_required"]
            ),
            "reference_bank": str(bank_path),
            "tnia_miller_included": False,
            "production_mutation": False,
        }
        write_json(output_root / "manifest.json", manifest)
        print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
