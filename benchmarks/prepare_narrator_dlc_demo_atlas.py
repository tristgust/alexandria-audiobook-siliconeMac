#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

ROUND_ID = "alexandria_narrator_dlc_demo_atlas_v1"

SOURCES = {
    "ultra_deluxe_voice_lines": {
        "title": "All Narrator Voice Lines from The Stanley Parable: Ultra Deluxe",
        "youtube_id": "zTA3kB9587o",
        "audio": "/Users/tristan/Library/Caches/Alexandria/NarratorYouTubeSources/zTA3kB9587o.m4a",
        "source_kind": "youtube_voice_line_compilation",
    },
    "demonstration": {
        "title": "The Stanley Parable Demonstration — no-commentary playthrough",
        "youtube_id": "gXu9aYwtnCk",
        "audio": "/Users/tristan/Library/Caches/Alexandria/NarratorYouTubeSources/gXu9aYwtnCk.m4a",
        "source_kind": "youtube_no_commentary_gameplay",
    },
    "letters_and_emails": {
        "title": "The Stanley Parable Responds to Your Letters and Emails",
        "youtube_id": "8MDu3xocHV0",
        "audio": "/Users/tristan/Library/Caches/Alexandria/NarratorYouTubeSources/8MDu3xocHV0.m4a",
        "source_kind": "official_crows_crows_crows_video",
    },
}

# Every specification is transcript-led. Approximate windows locate the scene;
# expected_text decides the final utterance and word-timestamp boundaries.
SPECS: tuple[dict[str, Any], ...] = (
    {
        "clip_id": "narrator_ud_ecstatic_bucket_affection",
        "source": "ultra_deluxe_voice_lines",
        "window": (14596.0, 14610.5),
        "expected_text": "Finally, yes! The bucket! Yes, yes, yes! I love that bucket.",
        "primary_emotion": "Ecstatic affection",
        "secondary_emotion": "Possessive delight",
        "dramatic_function": "Overjoyed reunion with a cherished object",
        "intensity": 5,
        "source_scene": "Reassurance Bucket pickup",
    },
    {
        "clip_id": "narrator_ud_manic_victory",
        "source": "ultra_deluxe_voice_lines",
        "window": (14152.0, 14164.5),
        "expected_text": "The bucket is the exciting and captivating new content that I promised. I did it! I win! I made a sequel to The Stanley Parable!",
        "primary_emotion": "Manic triumph",
        "secondary_emotion": "Grandiose pride",
        "dramatic_function": "Unrestrained victory declaration",
        "intensity": 5,
        "source_scene": "Bucket New Content ending",
    },
    {
        "clip_id": "narrator_ud_explosive_indignation",
        "source": "ultra_deluxe_voice_lines",
        "window": (14259.0, 14274.5),
        "expected_text": "What quality assurance department signed off on this? I'm infuriated and I'm offended, and I intend to find these people on Twitter and hold them personally accountable.",
        "primary_emotion": "Explosive indignation",
        "secondary_emotion": "Personal offense",
        "dramatic_function": "Furious public condemnation",
        "intensity": 5,
        "source_scene": "New Content disappointment",
    },
    {
        "clip_id": "narrator_ud_shame_and_guilt",
        "source": "ultra_deluxe_voice_lines",
        "window": (14270.0, 14284.5),
        "expected_text": "It's my fault, Stanley. I built up too much anticipation around the new content, I'm afraid. It could never have lived up to such expectations.",
        "primary_emotion": "Guilt",
        "secondary_emotion": "Deflated shame",
        "dramatic_function": "Taking responsibility after failure",
        "intensity": 3,
        "source_scene": "New Content disappointment",
    },
    {
        "clip_id": "narrator_ud_warm_reconciliation",
        "source": "ultra_deluxe_voice_lines",
        "window": (14278.0, 14299.5),
        "expected_text": "If you're still with me, why don't we just reset the game, and we'll try to get back to what The Stanley Parable is really about. No frills. No gimmicks. Just you and me having a great time together like always. What do you say, friend?",
        "primary_emotion": "Hopeful reconciliation",
        "secondary_emotion": "Warm companionship",
        "dramatic_function": "Repairing a relationship after disappointment",
        "intensity": 3,
        "source_scene": "New Content disappointment",
    },
    {
        "clip_id": "narrator_ud_creative_insecurity",
        "source": "ultra_deluxe_voice_lines",
        "window": (4874.0, 4892.5),
        "expected_text": "Where did I mess up the joke? Should I have paused for longer? Or spoken quicker? Comedic timing is so difficult. I wish I were better at it.",
        "primary_emotion": "Creative insecurity",
        "secondary_emotion": "Embarrassment",
        "dramatic_function": "Self-conscious post-failure analysis",
        "intensity": 3,
        "source_scene": "Comedic Timing ending",
    },
    {
        "clip_id": "narrator_ud_petulant_hurt",
        "source": "ultra_deluxe_voice_lines",
        "window": (7968.0, 7981.0),
        "expected_text": "Oh, you don't want to see the cool surprise I made for you? Well, fine! You're a dork anyway, so who cares? Oh. Never mind, you're not a dork.",
        "primary_emotion": "Petulant hurt",
        "secondary_emotion": "Immediate remorse",
        "dramatic_function": "Childish rejection followed by backtracking",
        "intensity": 3,
        "source_scene": "Ignoring the Memory Zone vent",
    },
    {
        "clip_id": "narrator_ud_contemptuous_disbelief",
        "source": "ultra_deluxe_voice_lines",
        "window": (7516.0, 7534.0),
        "expected_text": "Are you hallucinating? This is a tractor! It's an enormous machine that tills the earth! I thought this was a gimme. How on earth did you manage to screw it up? Absolutely incredible!",
        "primary_emotion": "Contemptuous disbelief",
        "secondary_emotion": "Irritated astonishment",
        "dramatic_function": "Scolding an absurd failure",
        "intensity": 4,
        "source_scene": "What Is a Bucket quiz",
    },
    {
        "clip_id": "narrator_ud_bittersweet_nostalgia",
        "source": "ultra_deluxe_voice_lines",
        "window": (9176.0, 9187.0),
        "expected_text": "We were so innocent. We'll never be like that again, Stanley.",
        "primary_emotion": "Bittersweet nostalgia",
        "secondary_emotion": "Mourning lost innocence",
        "dramatic_function": "Remembering a simpler shared past",
        "intensity": 3,
        "source_scene": "Figurines Memory Zone",
    },
    {
        "clip_id": "narrator_ud_separation_panic",
        "source": "ultra_deluxe_voice_lines",
        "window": (8912.0, 8929.0),
        "expected_text": "No, no, no! I'm not done! I'm not ready to move on! Stop the loading screen! Isn't there some way we can stay here? Keep enjoying these figurines?",
        "primary_emotion": "Separation panic",
        "secondary_emotion": "Compulsive attachment",
        "dramatic_function": "Refusing an ending and loss",
        "intensity": 5,
        "source_scene": "Figurines ending",
    },
    {
        "clip_id": "narrator_ud_loneliness_confession",
        "source": "ultra_deluxe_voice_lines",
        "window": (9076.0, 9094.0),
        "expected_text": "Why did I invent Stanley? Was I lonely? Yes, perhaps that's it. Perhaps I needed to imagine I had companionship, and Stanley really did make for a wonderful companion.",
        "primary_emotion": "Loneliness",
        "secondary_emotion": "Vulnerable affection",
        "dramatic_function": "Confessing emotional dependence",
        "intensity": 4,
        "source_scene": "Figurines ending self-reflection",
    },
    {
        "clip_id": "narrator_skip_desperate_pleading",
        "source": "ultra_deluxe_voice_lines",
        "window": (11018.0, 11037.0),
        "expected_text": "But I will find a way, I promise you. Just need to not do anything. Don't press the skip button. Please, please, please do not press the skip button. Just wait here. Wait here for me.",
        "primary_emotion": "Desperate pleading",
        "secondary_emotion": "Panic",
        "dramatic_function": "Begging against imminent abandonment",
        "intensity": 5,
        "source_scene": "Skip Button ending",
    },
    {
        "clip_id": "narrator_skip_abandonment_terror",
        "source": "ultra_deluxe_voice_lines",
        "window": (11102.0, 11131.0),
        "expected_text": "Knowing that you're going to do it, and that I'm going to be stuck all alone, and then I had the power to prevent it all from happening, if only I'd held my tongue. It's all out of my control now. Just you. Just your decision as to exactly when you're going to make me suffer, to leave me all alone.",
        "primary_emotion": "Abandonment terror",
        "secondary_emotion": "Resentful helplessness",
        "dramatic_function": "Anticipating deliberate emotional harm",
        "intensity": 5,
        "source_scene": "Skip Button ending",
    },
    {
        "clip_id": "narrator_skip_lonely_deprivation",
        "source": "ultra_deluxe_voice_lines",
        "window": (11215.0, 11235.0),
        "expected_text": "I've been sitting here all that time. Just sitting here. Not a single person to speak with. And you'd think that that's just how it's always been, right? Me talking, and you saying nothing.",
        "primary_emotion": "Profound loneliness",
        "secondary_emotion": "Emotional deprivation",
        "dramatic_function": "Naming prolonged isolation",
        "intensity": 4,
        "source_scene": "Skip Button ending",
    },
    {
        "clip_id": "narrator_skip_desperate_surrender",
        "source": "ultra_deluxe_voice_lines",
        "window": (11252.0, 11278.0),
        "expected_text": "I needed to know that someone was listening. I needed there to be a vessel through which my words were moving. It was the vessel I needed, Stanley, not the outcomes, not the story. None of that matters anymore. I'll give it all up. I'll give up every branching path. I'll burn my story to the ground.",
        "primary_emotion": "Desperate surrender",
        "secondary_emotion": "Dependent pleading",
        "dramatic_function": "Abandoning everything to preserve connection",
        "intensity": 5,
        "source_scene": "Skip Button ending",
    },
    {
        "clip_id": "narrator_skip_regret_and_grief",
        "source": "ultra_deluxe_voice_lines",
        "window": (11425.0, 11450.0),
        "expected_text": "I felt nothing at all but regret for the longest time. Stanley, days, months, I lost it all in a blur of the deepest longing to undo the past.",
        "primary_emotion": "Grief-stricken regret",
        "secondary_emotion": "Longing",
        "dramatic_function": "Mourning an irreversible choice",
        "intensity": 4,
        "source_scene": "Skip Button ending",
    },
    {
        "clip_id": "narrator_skip_existential_dread",
        "source": "ultra_deluxe_voice_lines",
        "window": (11540.0, 11562.0),
        "expected_text": "I wish you to feel afraid, as I do. That perhaps one day this state of mind will consume you as well. Perhaps you will somehow, in some way, have to live as I do now.",
        "primary_emotion": "Existential dread",
        "secondary_emotion": "Bitter despair",
        "dramatic_function": "Projecting unbearable suffering onto another",
        "intensity": 5,
        "source_scene": "Skip Button ending",
    },
    {
        "clip_id": "narrator_demo_theatrical_anticipation",
        "source": "demonstration",
        "window": (88.0, 101.0),
        "expected_text": "A tease, just enough to leave you hungry for more! How exciting! Can't you just feel that nervous tension? The looming uncertainty?",
        "primary_emotion": "Theatrical anticipation",
        "secondary_emotion": "Playful excitement",
        "dramatic_function": "Hyping an audience before a reveal",
        "intensity": 4,
        "source_scene": "Demonstration introduction",
    },
    {
        "clip_id": "narrator_demo_panicked_failure",
        "source": "demonstration",
        "window": (846.0, 870.0),
        "expected_text": "What? No, no, no, no, no! It can't be over yet! You didn't see anything! Everything that was supposed to demonstrate why The Stanley Parable is a quality experience worth your time and money! No, no, no, no, no! We have to get out of here. We have to find something for you to do, anything!",
        "primary_emotion": "Panicked failure",
        "secondary_emotion": "Frantic desperation",
        "dramatic_function": "Scrambling after a public demonstration collapses",
        "intensity": 5,
        "source_scene": "Demonstration ending malfunction",
    },
    {
        "clip_id": "narrator_demo_warm_nostalgia",
        "source": "demonstration",
        "window": (1087.0, 1137.0),
        "expected_text": "That was lovely. No concerns about where it was all going. No confusion. Just a blank slate. Yes, that's what I want. It's all so fresh in my memory. They were such wonderful moments.",
        "primary_emotion": "Warm nostalgia",
        "secondary_emotion": "Relieved fondness",
        "dramatic_function": "Revisiting an uncomplicated shared experience",
        "intensity": 3,
        "source_scene": "Demonstration restart reflection",
    },
    {
        "clip_id": "narrator_demo_bitter_exasperation",
        "source": "demonstration",
        "window": (730.0, 745.0),
        "expected_text": "We need to get you out of here before you start forming impressions of The Stanley Parable based on whatever the hell this egg game is. We need to get up. We need to start over.",
        "primary_emotion": "Bitter exasperation",
        "secondary_emotion": "Urgent embarrassment",
        "dramatic_function": "Rejecting a humiliating derailment",
        "intensity": 4,
        "source_scene": "Demonstration egg game",
    },
    {
        "clip_id": "narrator_official_moved_by_vulnerability",
        "source": "letters_and_emails",
        "window": (244.0, 264.0),
        "expected_text": "Wow, I'm actually quite moved by the vulnerability of this letter. To have such naked faith in us to deliver a quality product by not releasing it at all.",
        "primary_emotion": "Moved tenderness",
        "secondary_emotion": "Dry irony",
        "dramatic_function": "Responding sincerely to exposed vulnerability",
        "intensity": 3,
        "source_scene": "Official letters and emails video",
    },
    {
        "clip_id": "narrator_official_rallying_determination",
        "source": "letters_and_emails",
        "window": (274.0, 307.0),
        "expected_text": "I've never felt so deeply connected to our fan base before. And yes, yes, I too can take a stand. I will give our beloved fans what they've been asking for, what they've demanded. I will be a champion for you, the people.",
        "primary_emotion": "Rallying determination",
        "secondary_emotion": "Grandiose solidarity",
        "dramatic_function": "Public vow to champion a cause",
        "intensity": 4,
        "source_scene": "Official letters and emails video",
    },
)


class AtlasError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold().replace("branching", "branching"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transcribe_window(audio: Path, start: float, end: float, whisper_model: Path) -> dict[str, Any]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=str(whisper_model),
        language="en",
        word_timestamps=True,
        condition_on_previous_text=False,
        clip_timestamps=f"{start},{end}",
        verbose=False,
    )
    words: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            token = str(word.get("word") or "").strip()
            normalized = normalize_words(token)
            if not normalized:
                continue
            words.append(
                {
                    "word": token,
                    "normalized": normalized[0],
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                }
            )
    return {"text": str(result.get("text") or "").strip(), "words": words}


def best_word_span(words: list[dict[str, Any]], expected_text: str) -> tuple[int, int, float]:
    expected = normalize_words(expected_text)
    actual = [word["normalized"] for word in words]
    if not expected or not actual:
        raise AtlasError("Expected or transcribed word sequence is empty")
    best = (-1, -1, -1.0)
    low = max(1, int(len(expected) * 0.72))
    high = min(len(actual), int(len(expected) * 1.32) + 2)
    for size in range(low, high + 1):
        for start in range(0, len(actual) - size + 1):
            end = start + size
            ratio = difflib.SequenceMatcher(None, expected, actual[start:end]).ratio()
            # Slightly prefer spans close to the requested word count.
            ratio -= abs(size - len(expected)) / max(len(expected), 1) * 0.04
            if ratio > best[2]:
                best = (start, end, ratio)
    if best[2] < 0.72:
        raise AtlasError(f"Transcript match is too weak: {best[2]:.3f} for {expected_text!r}")
    return best


def extract_audio(source: Path, start: float, end: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        check=True,
    )
    audio, sample_rate = sf.read(temporary, dtype="float32", always_2d=True)
    temporary.unlink(missing_ok=True)
    mono = audio.mean(axis=1, dtype=np.float32)
    if mono.size < sample_rate * 0.45:
        raise AtlasError(f"Extracted clip is too short: {output}")
    peak = float(np.max(np.abs(mono)))
    if peak > 0:
        mono *= min(1.0, 0.78 / peak)
    sf.write(output, mono, 24000, subtype="PCM_16")


def build(args: argparse.Namespace) -> dict[str, Any]:
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not whisper_model.is_dir():
        raise AtlasError(f"Whisper model is missing: {whisper_model}")
    clips_root = output_root / "clips"
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}

    selected_ids = set(args.clip_id or [])
    selected_specs = [spec for spec in SPECS if not selected_ids or spec["clip_id"] in selected_ids]
    if selected_ids:
        unknown = sorted(selected_ids - {spec["clip_id"] for spec in SPECS})
        if unknown:
            raise AtlasError(f"Unknown clip IDs: {unknown}")

    receipts_root = output_root / "receipts"
    receipts_root.mkdir(parents=True, exist_ok=True)
    for spec in selected_specs:
        source_meta = SOURCES[spec["source"]]
        source = Path(source_meta["audio"]).expanduser().resolve()
        try:
            if not source.is_file():
                raise AtlasError(f"Source audio is missing: {source}")
            source_hashes.setdefault(spec["source"], sha256_file(source))
            context = transcribe_window(source, spec["window"][0], spec["window"][1], whisper_model)
            start_index, end_index, match = best_word_span(context["words"], spec["expected_text"])
            selected = context["words"][start_index:end_index]
            # Padding avoids clipped consonants while remaining short enough to
            # exclude neighboring lines in a voice-line compilation.
            clip_start = max(spec["window"][0], selected[0]["start"] - 0.12)
            clip_end = min(spec["window"][1], selected[-1]["end"] + 0.30)
            output = clips_root / f"{spec['clip_id']}.wav"
            extract_audio(source, clip_start, clip_end, output)
            verification = transcribe_window(output, 0.0, clip_end - clip_start, whisper_model)
            verification_ratio = difflib.SequenceMatcher(
                None,
                normalize_words(spec["expected_text"]),
                normalize_words(verification["text"]),
            ).ratio()
            row = {
                    **spec,
                    "target": "narrator",
                    "target_label": "Narrator",
                    "source_title": source_meta["title"],
                    "source_kind": source_meta["source_kind"],
                    "youtube_id": source_meta["youtube_id"],
                    "source_audio": str(source),
                    "source_audio_sha256": source_hashes[spec["source"]],
                    "context_transcript": context["text"],
                    "selected_start_seconds": round(clip_start, 3),
                    "selected_end_seconds": round(clip_end, 3),
                    "selected_duration_seconds": round(clip_end - clip_start, 3),
                    "selection_match": round(match, 6),
                    "verification_transcript": verification["text"],
                    "verification_similarity": round(verification_ratio, 6),
                    "audio_path": str(output),
                    "audio_sha256": sha256_file(output),
                    "assistant_label_status": "prefilled_for_user_correction",
                    "production_promotion_allowed": False,
                }
            rows.append(row)
            (receipts_root / f"{spec['clip_id']}.json").write_text(
                json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except Exception as exc:  # preserve every failed decision in evidence
            failures.append(
                {
                    "clip_id": spec["clip_id"],
                    "source": spec["source"],
                    "window": list(spec["window"]),
                    "expected_text": spec["expected_text"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    output_root.mkdir(parents=True, exist_ok=True)
    if args.assemble:
        rows = []
        for spec in SPECS:
            receipt = receipts_root / f"{spec['clip_id']}.json"
            if receipt.is_file():
                rows.append(json.loads(receipt.read_text(encoding="utf-8")))
        failures_path = output_root / "failures.json"
        persisted_failures = json.loads(failures_path.read_text(encoding="utf-8")) if failures_path.is_file() else []
        failures = persisted_failures + failures
    elif failures:
        failures_path = output_root / "failures.json"
        persisted = json.loads(failures_path.read_text(encoding="utf-8")) if failures_path.is_file() else []
        by_id = {item["clip_id"]: item for item in persisted}
        by_id.update({item["clip_id"]: item for item in failures})
        failures_path.write_text(json.dumps(list(by_id.values()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    payload = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "candidate_count": len(rows),
        "failure_count": len(failures),
        "source_count": len({row["source"] for row in rows}),
        "rows": rows,
        "failures": failures,
        "transcript_first_selection": True,
        "production_promotion_allowed": False,
    }
    path = output_root / "narrator-dlc-demo-atlas.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if failures and not args.allow_failures:
        raise AtlasError(f"{len(failures)} candidate extractions failed; see {path}")
    return {"candidate_count": len(rows), "failure_count": len(failures), "output": str(path)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.output_root).expanduser().resolve()
    path = root / "narrator-dlc-demo-atlas.json"
    if not path.is_file():
        raise AtlasError(f"Atlas is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    failures: list[str] = []
    seen: set[str] = set()
    for row in rows:
        clip_id = row.get("clip_id")
        if clip_id in seen:
            failures.append(f"duplicate:{clip_id}")
        seen.add(clip_id)
        audio = Path(row.get("audio_path") or "")
        if not audio.is_file():
            failures.append(f"missing:{clip_id}")
            continue
        if sha256_file(audio) != row.get("audio_sha256"):
            failures.append(f"hash:{clip_id}")
        info = sf.info(audio)
        if info.samplerate != 24000 or info.channels != 1 or info.subtype != "PCM_16":
            failures.append(f"format:{clip_id}")
        if float(row.get("verification_similarity") or 0) < 0.72:
            failures.append(f"transcript:{clip_id}")
    if failures:
        raise AtlasError(f"Atlas validation failed: {failures}")
    return {
        "candidate_count": len(rows),
        "failure_count": len(payload.get("failures") or []),
        "validation_failure_count": len(failures),
        "atlas": str(path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a transcript-guided Narrator DLC/demo performance atlas.")
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--whisper-model", required=True)
    build_parser.add_argument("--output-root", required=True)
    build_parser.add_argument("--allow-failures", action="store_true")
    build_parser.add_argument("--clip-id", action="append")
    build_parser.add_argument("--assemble", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = build(args) if args.command == "build" else validate(args)
    except AtlasError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
