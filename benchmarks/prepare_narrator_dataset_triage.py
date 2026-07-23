from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

from alexandria_preparer import prepare_dataset


ROUND_ID = "alexandria_narrator_dataset_triage_v1"
DEFAULT_SEGMENT_SECONDS = 900.0
DEFAULT_SHORTLIST_SIZE = 60
REVIEW_ASSET_ROOT = Path(__file__).with_name("narrator_dataset_triage_assets")
SUPPORTED_RESULTS_STATUS = {"accepted", "rejected", "pending"}
CATEGORY_LABELS = {
    "neutral": "Neutral / ordinary",
    "dry_amused": "Dry / amused / sarcastic",
    "tense_urgent": "Tense / urgent",
    "forceful_angry": "Forceful / emphatic",
    "quiet_restrained": "Quiet / restrained",
    "vulnerable_sad": "Vulnerable / sad",
    "animated_surprised": "Animated / surprised",
}
CATEGORY_INSTRUCTIONS = {
    "neutral": "Natural, clear narration with an even pace and restrained expression.",
    "dry_amused": "Dry, mildly amused narration with deliberate pacing and understated sarcasm.",
    "tense_urgent": "Tense, urgent narration with tightened pacing and controlled concern.",
    "forceful_angry": "Forceful, emphatic delivery with clear diction and contained intensity.",
    "quiet_restrained": "Quiet, restrained narration with close, deliberate delivery and minimal projection.",
    "vulnerable_sad": "Vulnerable, subdued narration carrying sadness without melodrama.",
    "animated_surprised": "Animated, surprised narration with lively emphasis and clear articulation.",
}
CATEGORY_QUOTAS = {
    "neutral": 16,
    "dry_amused": 10,
    "tense_urgent": 10,
    "forceful_angry": 8,
    "quiet_restrained": 8,
    "vulnerable_sad": 4,
    "animated_surprised": 4,
}


class TriageError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceClip:
    source_key: str
    source_label: str
    source_path: Path
    audio_path: Path
    text: str
    duration_seconds: float
    transcript_confidence: float
    snr_db: float
    source_start_seconds: float | None
    source_end_seconds: float | None
    source_segment_index: int | None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalized_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(text).casefold()))


def text_tokens(text: str) -> list[str]:
    return normalized_text(text).split()


def ffprobe_duration(path: Path) -> float:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise TriageError(process.stderr.strip() or f"ffprobe failed for {path}")
    try:
        return float(process.stdout.strip())
    except ValueError as exc:
        raise TriageError(f"Could not parse duration for {path}") from exc


def run_checked(command: list[str]) -> None:
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        raise TriageError(process.stderr.strip() or "Command failed: " + " ".join(command))


def segment_plan(source: Path, segment_seconds: float) -> list[dict[str, Any]]:
    duration = ffprobe_duration(source)
    count = max(1, int(math.ceil(duration / segment_seconds)))
    return [
        {
            "index": index,
            "start_seconds": round(index * segment_seconds, 3),
            "duration_seconds": round(min(segment_seconds, duration - index * segment_seconds), 3),
        }
        for index in range(count)
    ]


def rewrite_segment_zip(
    archive_path: Path,
    *,
    source: Path,
    source_sha256: str,
    segment_index: int,
    start_seconds: float,
    duration_seconds: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="alexandria-segment-rewrite-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(root)
        metadata_path = root / "metadata.jsonl"
        rows = []
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("source_start_seconds") is not None:
                row["source_start_seconds"] = round(
                    float(row["source_start_seconds"]) + start_seconds,
                    3,
                )
            if row.get("source_end_seconds") is not None:
                row["source_end_seconds"] = round(
                    float(row["source_end_seconds"]) + start_seconds,
                    3,
                )
            row["source_media"] = source.name
            row["source_media_sha256"] = source_sha256
            row["source_chunk_index"] = segment_index
            rows.append(row)
        metadata_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        manifest_path = root / "preparation_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "original_source_media": source.name,
                "original_source_media_sha256": source_sha256,
                "source_chunk_index": segment_index,
                "source_chunk_start_seconds": start_seconds,
                "source_chunk_duration_seconds": duration_seconds,
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_zip = archive_path.with_suffix(".zip.rewrite")
        with zipfile.ZipFile(
            temporary_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(root.iterdir(), key=lambda item: item.name):
                if path.is_file():
                    archive.write(path, arcname=path.name)
        os.replace(temporary_zip, archive_path)
    return {
        "segment_index": segment_index,
        "start_seconds": start_seconds,
        "duration_seconds": duration_seconds,
        "sample_count": len(rows),
        "archive": str(archive_path),
        "archive_sha256": sha256_file(archive_path),
    }


def prepare_segment(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not source.is_file():
        raise TriageError(f"Source media not found: {source}")
    plan = segment_plan(source, float(args.segment_seconds))
    if args.index < 0 or args.index >= len(plan):
        raise TriageError(f"Segment index must be from 0 to {len(plan) - 1}.")
    spec = plan[args.index]
    segment_dir = output_root / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    output = segment_dir / f"segment_{args.index:02d}.zip"
    receipt = segment_dir / f"segment_{args.index:02d}.json"
    if output.is_file() and receipt.is_file() and not args.force:
        existing = json.loads(receipt.read_text(encoding="utf-8"))
        if existing.get("archive_sha256") == sha256_file(output):
            return {**existing, "reused": True}
    if output.exists():
        output.unlink()
    source_sha = sha256_file(source)
    with tempfile.TemporaryDirectory(prefix=f"alexandria-narrator-{args.index:02d}-") as temporary:
        segment_audio = Path(temporary) / f"segment_{args.index:02d}.wav"
        run_checked(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(spec["start_seconds"]),
                "-t",
                str(spec["duration_seconds"]),
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(segment_audio),
            ]
        )
        if args.verbose:
            prepare_dataset(
                audio_path=segment_audio,
                output_path=output,
                language=args.language,
                min_confidence=float(args.min_confidence),
                min_snr=float(args.min_snr),
                model=args.model,
            )
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                prepare_dataset(
                    audio_path=segment_audio,
                    output_path=output,
                    language=args.language,
                    min_confidence=float(args.min_confidence),
                    min_snr=float(args.min_snr),
                    model=args.model,
                )
    result = rewrite_segment_zip(
        output,
        source=source,
        source_sha256=source_sha,
        segment_index=args.index,
        start_seconds=float(spec["start_seconds"]),
        duration_seconds=float(spec["duration_seconds"]),
    )
    receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def extract_dataset_zip(archive_path: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)
    return destination


def load_dataset_dir(path: Path, *, source_key: str, source_label: str) -> list[SourceClip]:
    metadata = path / "metadata.jsonl"
    if not metadata.is_file():
        raise TriageError(f"metadata.jsonl not found: {metadata}")
    rows: list[SourceClip] = []
    for line_number, line in enumerate(metadata.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        audio_name = item.get("audio_filepath") or item.get("audio") or item.get("audio_path")
        audio = (path / str(audio_name)).resolve()
        if not audio.is_file():
            continue
        text = str(item.get("text") or item.get("transcript") or "").strip()
        if not text:
            continue
        try:
            duration = float(item.get("duration_seconds") or sf.info(audio).duration)
        except Exception:
            continue
        rows.append(
            SourceClip(
                source_key=source_key,
                source_label=source_label,
                source_path=path,
                audio_path=audio,
                text=text,
                duration_seconds=duration,
                transcript_confidence=float(item.get("transcript_confidence") or 0.0),
                snr_db=float(item.get("snr_db") or 0.0),
                source_start_seconds=(
                    float(item["source_start_seconds"])
                    if item.get("source_start_seconds") is not None
                    else None
                ),
                source_end_seconds=(
                    float(item["source_end_seconds"])
                    if item.get("source_end_seconds") is not None
                    else None
                ),
                source_segment_index=(
                    int(item["source_segment_index"])
                    if item.get("source_segment_index") is not None
                    else None
                ),
            )
        )
    return rows


def frame_rms(audio: np.ndarray, frame: int = 1024, hop: int = 512) -> np.ndarray:
    if len(audio) < frame:
        return np.array([float(np.sqrt(np.mean(np.square(audio))))], dtype=np.float32)
    values = []
    for start in range(0, len(audio) - frame + 1, hop):
        chunk = audio[start : start + frame]
        values.append(float(np.sqrt(np.mean(np.square(chunk)))))
    return np.asarray(values, dtype=np.float32)


def audio_features(path: Path, words: int, duration: float) -> dict[str, float]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if mono.size == 0:
        raise TriageError(f"Empty audio: {path}")
    rms = float(np.sqrt(np.mean(np.square(mono))))
    peak = float(np.max(np.abs(mono)))
    frames = frame_rms(mono)
    voiced = frames[frames > max(1e-4, np.percentile(frames, 20))]
    if voiced.size:
        low = float(np.percentile(voiced, 20))
        high = float(np.percentile(voiced, 90))
        dynamic_db = 20.0 * math.log10(max(high, 1e-6) / max(low, 1e-6))
    else:
        dynamic_db = 0.0
    quiet_ratio = float(np.mean(frames < max(0.004, np.percentile(frames, 30))))
    zero_crossing = float(np.mean(np.abs(np.diff(np.signbit(mono)))))
    return {
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-8)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-8)),
        "dynamic_db": dynamic_db,
        "quiet_ratio": quiet_ratio,
        "zero_crossing_rate": zero_crossing,
        "words_per_second": words / max(duration, 0.01),
        "sample_rate": float(sample_rate),
    }


def has_terminal_punctuation(text: str) -> bool:
    return bool(re.search(r"[.!?…](?:[\"'”’)]*)$", text.strip()))


def boundary_quality(text: str) -> float:
    value = text.strip()
    score = 0.0
    if value and value[0].isupper():
        score += 1.0
    else:
        score -= 1.5
    if has_terminal_punctuation(value):
        score += 1.5
    elif value.endswith((",", ";", ":", "-")):
        score -= 1.5
    else:
        score -= 1.25
    tokens = text_tokens(value)
    if tokens and tokens[0] in {"and", "but", "or", "because", "while", "which", "that", "to"}:
        score -= 0.6
    if len(tokens) < 4:
        score -= 1.0
    return score


def quality_score(clip: SourceClip) -> float:
    duration_preference = max(0.0, 1.0 - abs(clip.duration_seconds - 4.5) / 8.0)
    return (
        clip.transcript_confidence * 4.0
        + min(max(clip.snr_db, 0.0), 60.0) / 30.0
        + duration_preference
        + boundary_quality(clip.text)
    )


def likely_duplicate(left: SourceClip, right: SourceClip) -> bool:
    left_norm = normalized_text(left.text)
    right_norm = normalized_text(right.text)
    if left_norm == right_norm:
        return True
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if shorter and shorter in longer and len(shorter) / max(1, len(longer)) >= 0.58:
        return True
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    if overlap < 0.78:
        return False
    return SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.86


def deduplicate(clips: list[SourceClip]) -> tuple[list[SourceClip], list[dict[str, Any]]]:
    selected: list[SourceClip] = []
    duplicates: list[dict[str, Any]] = []
    exact: dict[str, int] = {}
    buckets: dict[tuple[str, str, int], list[int]] = {}
    for clip in sorted(clips, key=quality_score, reverse=True):
        norm = normalized_text(clip.text)
        if not norm:
            continue
        if norm in exact:
            kept = selected[exact[norm]]
            duplicates.append({"dropped": str(clip.audio_path), "kept": str(kept.audio_path), "reason": "exact_text"})
            continue
        tokens = norm.split()
        key = (tokens[0], tokens[-1], len(tokens) // 4)
        duplicate_index = None
        for index in buckets.get(key, []):
            if likely_duplicate(clip, selected[index]):
                duplicate_index = index
                break
        if duplicate_index is not None:
            kept = selected[duplicate_index]
            duplicates.append({"dropped": str(clip.audio_path), "kept": str(kept.audio_path), "reason": "near_text"})
            continue
        index = len(selected)
        selected.append(clip)
        exact[norm] = index
        buckets.setdefault(key, []).append(index)
    return selected, duplicates


def suggest_category(clip: SourceClip, features: dict[str, float]) -> str:
    text = normalized_text(clip.text)
    words = set(text.split())
    urgent = {"quick", "hurry", "wait", "stop", "danger", "escape", "run", "please", "help"}
    vulnerable_phrases = (
        "all alone",
        "goodbye",
        "i'm sorry",
        "i am sorry",
        "hurt me",
        "afraid",
        "lonely",
        "empty room",
        "no one was there",
    )
    amused = {"funny", "ridiculous", "clever", "brilliant", "wonderful", "obviously", "clearly", "course", "dear", "amusing"}
    forceful_phrases = (
        "stop this",
        "listen to me",
        "i refuse",
        "enough of",
        "how dare",
        "you ruined",
        "you've ruined",
        "do not",
        "don't you",
        "i will not",
    )
    if any(phrase in text for phrase in vulnerable_phrases) and features["words_per_second"] < 2.9:
        return "vulnerable_sad"
    if features["quiet_ratio"] > 0.42 and features["words_per_second"] < 3.0:
        return "quiet_restrained"
    if amused & words or any(phrase in text for phrase in ("of course", "well done", "how amusing", "what a surprise")):
        return "dry_amused"
    if any(phrase in text for phrase in forceful_phrases):
        return "forceful_angry"
    if ("?" in clip.text and features["dynamic_db"] > 7.5) or (
        "!" in clip.text and features["dynamic_db"] <= 8.5
    ):
        return "animated_surprised"
    if urgent & words or features["words_per_second"] > 3.65:
        return "tense_urgent"
    if "!" in clip.text and features["dynamic_db"] > 8.5:
        return "forceful_angry"
    return "neutral"


def eligible(clip: SourceClip) -> tuple[bool, list[str]]:
    reasons = []
    words = text_tokens(clip.text)
    first_alpha = next((character for character in clip.text.strip() if character.isalpha()), "")
    if clip.transcript_confidence < 0.93:
        reasons.append("low_confidence")
    if clip.snr_db < 30.0:
        reasons.append("low_snr")
    if clip.duration_seconds < 1.8:
        reasons.append("too_short")
    if clip.duration_seconds > 10.0:
        reasons.append("too_long")
    if len(words) < 4:
        reasons.append("too_few_words")
    if len(words) > 36:
        reasons.append("too_many_words")
    if (
        not first_alpha
        or not first_alpha.isupper()
        or not has_terminal_punctuation(clip.text)
        or boundary_quality(clip.text) < 0
    ):
        reasons.append("likely_fragment")
    return not reasons, reasons


def choose_shortlist(rows: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict[str, Any]]] = {key: [] for key in CATEGORY_LABELS}
    for row in rows:
        by_category[row["category"]].append(row)
    for values in by_category.values():
        values.sort(key=lambda item: item["quality_score"], reverse=True)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    scale = target / sum(CATEGORY_QUOTAS.values())
    for category, quota in CATEGORY_QUOTAS.items():
        count = max(1, int(round(quota * scale)))
        for row in by_category[category][:count]:
            selected.append(row)
            selected_ids.add(row["sample_id"])
    if len(selected) < target:
        remaining = sorted(
            (row for row in rows if row["sample_id"] not in selected_ids),
            key=lambda item: item["quality_score"],
            reverse=True,
        )
        selected.extend(remaining[: target - len(selected)])
    return sorted(
        selected[:target],
        key=lambda item: (
            list(CATEGORY_LABELS).index(item["category"]),
            -item["quality_score"],
        ),
    )


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def assemble(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    source_cache = output_root / "source-cache"
    source_cache.mkdir(parents=True, exist_ok=True)
    all_clips: list[SourceClip] = []
    source_manifest = []
    old_dataset = Path(args.old_dataset).expanduser().resolve()
    if old_dataset.is_dir():
        rows = load_dataset_dir(old_dataset, source_key="pilot_source", source_label="Existing 22-minute source")
        all_clips.extend(rows)
        source_manifest.append({"key": "pilot_source", "path": str(old_dataset), "clip_count": len(rows)})
    segment_archives = sorted((output_root / "segments").glob("segment_*.zip"))
    if not segment_archives:
        raise TriageError("No prepared full-video segment ZIPs were found.")
    for archive in segment_archives:
        key = archive.stem
        extracted = extract_dataset_zip(archive, source_cache / key)
        rows = load_dataset_dir(extracted, source_key=key, source_label="Full 82-minute video")
        all_clips.extend(rows)
        source_manifest.append({"key": key, "path": str(archive), "sha256": sha256_file(archive), "clip_count": len(rows)})
    deduped, duplicates = deduplicate(all_clips)
    rejected = []
    candidates = []
    for clip in deduped:
        ok, reasons = eligible(clip)
        if not ok:
            rejected.append({"audio": str(clip.audio_path), "text": clip.text, "reasons": reasons})
            continue
        words = len(text_tokens(clip.text))
        features = audio_features(clip.audio_path, words, clip.duration_seconds)
        category = suggest_category(clip, features)
        audio_sha = sha256_file(clip.audio_path)
        sample_id = fingerprint(
            {
                "source": clip.source_key,
                "audio_sha256": audio_sha,
                "text": normalized_text(clip.text),
            }
        )
        candidates.append(
            {
                "sample_id": sample_id,
                "source_key": clip.source_key,
                "source_label": clip.source_label,
                "source_audio_path": str(clip.audio_path),
                "text": clip.text,
                "suggested_instruction": CATEGORY_INSTRUCTIONS[category],
                "category": category,
                "category_label": CATEGORY_LABELS[category],
                "duration_seconds": round(clip.duration_seconds, 3),
                "transcript_confidence": round(clip.transcript_confidence, 4),
                "snr_db": round(clip.snr_db, 2),
                "source_start_seconds": clip.source_start_seconds,
                "source_end_seconds": clip.source_end_seconds,
                "audio_sha256": audio_sha,
                "quality_score": round(quality_score(clip), 4),
                "features": {key: round(value, 4) for key, value in features.items()},
            }
        )
    shortlist = choose_shortlist(candidates, int(args.shortlist_size))
    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    public_rows = []
    private_rows = []
    for index, row in enumerate(shortlist):
        filename = f"{row['sample_id']}.wav"
        target = review_root / "audio" / filename
        link_or_copy(Path(row["source_audio_path"]), target)
        public_rows.append(
            {
                "sample_id": row["sample_id"],
                "ordinal": index + 1,
                "audio_url": f"audio/{filename}",
                "text": row["text"],
                "suggested_instruction": row["suggested_instruction"],
                "category": row["category"],
                "category_label": row["category_label"],
                "source_label": row["source_label"],
                "duration_seconds": row["duration_seconds"],
                "transcript_confidence": row["transcript_confidence"],
                "snr_db": row["snr_db"],
                "source_start_seconds": row["source_start_seconds"],
                "features": row["features"],
            }
        )
        private_rows.append({**row, "source_audio_path": str(target)})
    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(REVIEW_ASSET_ROOT / asset, review_root / asset)
    public_data = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "category_labels": CATEGORY_LABELS,
        "rows": public_rows,
    }
    (review_root / "data.js").write_text(
        "window.NARRATOR_TRIAGE_DATA = " + json.dumps(public_data, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "sources": source_manifest,
        "source_clip_count": len(all_clips),
        "deduplicated_clip_count": len(deduped),
        "duplicate_count": len(duplicates),
        "automatic_rejection_count": len(rejected),
        "eligible_candidate_count": len(candidates),
        "shortlist_count": len(shortlist),
        "category_counts": {
            key: sum(row["category"] == key for row in shortlist) for key in CATEGORY_LABELS
        },
        "rows": private_rows,
    }
    (output_root / "triage-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "deduplication-audit.json").write_text(
        json.dumps(duplicates, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "automatic-rejections.json").write_text(
        json.dumps(rejected, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if source_cache.exists():
        shutil.rmtree(source_cache)
    (output_root / "START_HERE.txt").write_text(
        "Alexandria Narrator Dataset Triage\n"
        "===================================\n\n"
        f"Shortlist: {len(shortlist)} clips from {len(all_clips)} prepared source clips.\n"
        "The source recordings and original datasets were not modified.\n\n"
        "Open Terminal 1:\n"
        f"  cd \"{review_root}\"\n"
        "  python3 -m http.server 8770 --bind 127.0.0.1\n\n"
        "Open Terminal 2:\n"
        "  open \"http://127.0.0.1:8770/\"\n\n"
        "Review rules:\n"
        "  - Accept only when the transcript exactly matches the audio.\n"
        "  - Keep or edit the delivery instruction so it describes what is actually heard.\n"
        "  - Reject clipped, noisy, duplicated, mixed-speaker, or misleading samples.\n"
        "  - Export the cumulative review JSON when finished.\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "source_clip_count": len(all_clips),
        "deduplicated_clip_count": len(deduped),
        "duplicate_count": len(duplicates),
        "eligible_candidate_count": len(candidates),
        "shortlist_count": len(shortlist),
        "category_counts": manifest["category_counts"],
    }


def load_results(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("round_id") != ROUND_ID:
        raise TriageError("The review export belongs to a different round.")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise TriageError("The review export rows field is invalid.")
    return payload


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    manifest = json.loads((output_root / "triage-manifest.json").read_text(encoding="utf-8"))
    source_rows = {row["sample_id"]: row for row in manifest["rows"]}
    results = load_results(Path(args.results).expanduser().resolve())
    accepted = []
    for result in results["rows"]:
        sample_id = result.get("sample_id")
        source = source_rows.get(sample_id)
        if source is None:
            raise TriageError(f"Unknown sample in results: {sample_id}")
        status = str(result.get("status") or "pending")
        if status not in SUPPORTED_RESULTS_STATUS:
            raise TriageError(f"Invalid review status for {sample_id}: {status}")
        if status != "accepted":
            continue
        transcript = str(result.get("transcript") or "").strip()
        instruction = str(result.get("instruction") or "").strip()
        if not result.get("transcript_confirmed") or not transcript:
            raise TriageError(f"Accepted sample {sample_id} needs a confirmed transcript.")
        if not instruction:
            raise TriageError(f"Accepted sample {sample_id} needs an instruction.")
        accepted.append({**source, "review": result, "transcript": transcript, "instruction": instruction})
    if len(accepted) < int(args.minimum_accepted):
        raise TriageError(
            f"Only {len(accepted)} clips were accepted; at least {args.minimum_accepted} are required."
        )
    reference = max(
        accepted,
        key=lambda item: (
            item["category"] == "neutral",
            item["quality_score"],
        ),
    )
    output_zip = Path(args.output_zip).expanduser().resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists() and not args.force:
        raise TriageError(f"Output already exists: {output_zip}")
    with tempfile.TemporaryDirectory(prefix="alexandria-reviewed-narrator-") as temporary:
        root = Path(temporary)
        ref_name = "ref.wav"
        ref_text = reference["transcript"]
        shutil.copy2(reference["source_audio_path"], root / ref_name)
        (root / "ref_text.txt").write_text(ref_text, encoding="utf-8")
        metadata_rows = []
        for index, item in enumerate(accepted):
            filename = f"sample_{index:04d}.wav"
            shutil.copy2(item["source_audio_path"], root / filename)
            metadata_rows.append(
                {
                    "audio_filepath": filename,
                    "text": item["transcript"],
                    "instruction": item["instruction"],
                    "ref_audio": ref_name,
                    "review_status": "accepted",
                    "duration_seconds": item["duration_seconds"],
                    "transcript_confidence": item["transcript_confidence"],
                    "snr_db": item["snr_db"],
                    "source_label": item["source_label"],
                    "source_start_seconds": item["source_start_seconds"],
                    "source_audio_sha256": item["audio_sha256"],
                    "triage_sample_id": item["sample_id"],
                    "delivery_category": item["category"],
                }
            )
        (root / "metadata.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in metadata_rows),
            encoding="utf-8",
        )
        package_manifest = {
            "schema_version": 1,
            "dataset_id": args.dataset_id,
            "created_at": now_iso(),
            "source_round_id": ROUND_ID,
            "instruction_mode": "per_record",
            "accepted_count": len(accepted),
            "duration_seconds": round(sum(item["duration_seconds"] for item in accepted), 3),
            "category_counts": {
                key: sum(item["category"] == key for item in accepted) for key in CATEGORY_LABELS
            },
            "reference_sample_id": reference["sample_id"],
            "reference_text": ref_text,
            "review_export_sha256": sha256_file(Path(args.results).expanduser().resolve()),
        }
        (root / "preparation_manifest.json").write_text(
            json.dumps(package_manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_zip = output_zip.with_suffix(output_zip.suffix + ".tmp")
        with zipfile.ZipFile(
            temporary_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(root.iterdir(), key=lambda item: item.name):
                archive.write(path, arcname=path.name)
        os.replace(temporary_zip, output_zip)
    return {
        "output_zip": str(output_zip),
        "sha256": sha256_file(output_zip),
        "accepted_count": len(accepted),
        "duration_minutes": round(sum(item["duration_seconds"] for item in accepted) / 60.0, 3),
        "category_counts": package_manifest["category_counts"],
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    manifest = json.loads((output_root / "triage-manifest.json").read_text(encoding="utf-8"))
    review_root = output_root / "review"
    missing = []
    hash_errors = []
    external_sources = []
    for row in manifest["rows"]:
        audio = review_root / "audio" / f"{row['sample_id']}.wav"
        if not audio.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != row["audio_sha256"]:
            hash_errors.append(row["sample_id"])
        source_audio = Path(row["source_audio_path"]).expanduser().resolve()
        try:
            source_audio.relative_to(review_root.resolve())
        except ValueError:
            external_sources.append(row["sample_id"])
    if missing or hash_errors or external_sources:
        raise TriageError(
            "Review validation failed: "
            f"missing={missing}, hash_errors={hash_errors}, external_sources={external_sources}"
        )
    return {
        "round_id": manifest["round_id"],
        "shortlist_count": manifest["shortlist_count"],
        "missing_audio_count": len(missing),
        "hash_error_count": len(hash_errors),
        "external_source_count": len(external_sources),
        "category_counts": manifest["category_counts"],
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and review real Narrator audio for instruction-conditioned LoRA training.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--source", required=True)
    plan.add_argument("--segment-seconds", type=float, default=DEFAULT_SEGMENT_SECONDS)

    segment = subparsers.add_parser("segment")
    segment.add_argument("--source", required=True)
    segment.add_argument("--output-root", required=True)
    segment.add_argument("--index", type=int, required=True)
    segment.add_argument("--segment-seconds", type=float, default=DEFAULT_SEGMENT_SECONDS)
    segment.add_argument("--language", default="en")
    segment.add_argument("--min-confidence", type=float, default=0.85)
    segment.add_argument("--min-snr", type=float, default=25.0)
    segment.add_argument("--model", required=True)
    segment.add_argument("--force", action="store_true")
    segment.add_argument("--verbose", action="store_true")

    assemble_parser = subparsers.add_parser("assemble")
    assemble_parser.add_argument("--output-root", required=True)
    assemble_parser.add_argument("--old-dataset", required=True)
    assemble_parser.add_argument("--shortlist-size", type=int, default=DEFAULT_SHORTLIST_SIZE)

    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--output-root", required=True)
    finalize_parser.add_argument("--results", required=True)
    finalize_parser.add_argument("--output-zip", required=True)
    finalize_parser.add_argument("--dataset-id", default="narrator_instruction_reviewed_v1")
    finalize_parser.add_argument("--minimum-accepted", type=int, default=24)
    finalize_parser.add_argument("--force", action="store_true")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "plan":
            result = segment_plan(Path(args.source).expanduser().resolve(), args.segment_seconds)
        elif args.command == "segment":
            result = prepare_segment(args)
        elif args.command == "assemble":
            result = assemble(args)
        elif args.command == "finalize":
            result = finalize(args)
        elif args.command == "validate":
            result = validate(args)
        else:
            raise TriageError(f"Unsupported command: {args.command}")
    except (TriageError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
