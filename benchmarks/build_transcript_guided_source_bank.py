#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROUND_ID = "alexandria_transcript_guided_source_isolation_v1"

SOURCES = {
    "criminal_code": Path("/Users/tristan/Library/Caches/CloudKit/com.apple.bird/e2eed64c87cf8a2473b56deec49a2fb7ff87f9a5/MMCS/ClonedFiles/documentContent__25FB57D2-BD90-4B51-813A-EE42908B4F90_fileContent"),
    "hesitation_deviation": Path("/Users/tristan/Library/Caches/CloudKit/com.apple.bird/e2eed64c87cf8a2473b56deec49a2fb7ff87f9a5/MMCS/ClonedFiles/documentContent__46C4050A-2EF6-4A1D-B080-2A27964B86B6_fileContent"),
    "all_consuming_fire": Path("/Users/tristan/Library/Caches/CloudKit/com.apple.bird/e2eed64c87cf8a2473b56deec49a2fb7ff87f9a5/MMCS/ClonedFiles/documentContent__F55DF292-71E4-49EF-99D7-ECB84EF76485_fileContent"),
}

DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "clip_id": "benny_criminal_restrained_relief",
        "source_key": "criminal_code",
        "context_ids": ["criminal_code_01"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 2226.93,
        "transcript_end_seconds": 2229.57,
        "transcript": "But to our relief, they let him go.",
        "speaker_role": "Benny first-person narration",
        "primary_emotion": "Restrained relief",
        "secondary_emotion": "Guarded concern",
        "dramatic_function": "Narrative reassurance after danger",
        "intensity_1_to_5": 2,
        "selection_reason": "The complete sentence explicitly resolves danger with relief. The old seed landed later in factual exposition; the transcript shows this earlier sentence is the emotionally useful utterance.",
    },
    {
        "clip_id": "benny_criminal_moral_authority",
        "source_key": "criminal_code",
        "context_ids": ["criminal_code_02"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 4153.91,
        "transcript_end_seconds": 4163.73,
        "transcript": "The one which could wipe those parasite creatures out of existence. These things have controlled you since you first crawled out of the mud. Now it's up to you what happens to them.",
        "speaker_role": "Benny direct dialogue",
        "primary_emotion": "Grave authority",
        "secondary_emotion": "Moral uncertainty",
        "dramatic_function": "Controlled moral challenge",
        "intensity_1_to_5": 3,
        "selection_reason": "This is one uninterrupted Benny turn after another character asks 'Me?'. It frames a lethal choice with sober authority rather than anger.",
    },
    {
        "clip_id": "benny_criminal_incredulous_concern",
        "source_key": "criminal_code",
        "context_ids": ["criminal_code_03"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 2284.56,
        "transcript_end_seconds": 2291.42,
        "transcript": "Then they changed tack in a way he didn't understand. They'd started to accuse him of staging the break-in himself. Why would he have done that?",
        "speaker_role": "Benny first-person narration",
        "primary_emotion": "Wary concern",
        "secondary_emotion": "Incredulity",
        "dramatic_function": "Investigative tension",
        "intensity_1_to_5": 2,
        "selection_reason": "The rhetorical question completes a coherent escalation from procedural description into suspicious disbelief.",
    },
    {
        "clip_id": "benny_criminal_sardonic_concern",
        "source_key": "criminal_code",
        "context_ids": ["criminal_code_04"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 945.41,
        "transcript_end_seconds": 950.33,
        "transcript": "Which, given his distaste for weapons, you'd think would be his forte. But it wasn't going well.",
        "speaker_role": "Benny first-person narration",
        "primary_emotion": "Dry sarcasm",
        "secondary_emotion": "Anxious concern",
        "dramatic_function": "Sardonic commentary under pressure",
        "intensity_1_to_5": 2,
        "selection_reason": "The joke and its deflating final sentence belong together; the humor masks real concern about the Doctor's failure.",
    },
    {
        "clip_id": "benny_hesitation_baffled_protest",
        "source_key": "hesitation_deviation",
        "context_ids": ["hesitation_deviation_01"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 530.76,
        "transcript_end_seconds": 534.80,
        "transcript": "But Doctor, I began, that picture is indoors. There's no sky.",
        "speaker_role": "Benny direct dialogue within first-person narration",
        "primary_emotion": "Baffled disbelief",
        "secondary_emotion": "Dry irritation",
        "dramatic_function": "Conversational challenge",
        "intensity_1_to_5": 2,
        "selection_reason": "The transcript isolates Benny's objection between the shopkeeper and Doctor turns. It is a complete objection, not the Doctor's playful reply.",
    },
    {
        "clip_id": "benny_hesitation_grave_reflection",
        "source_key": "hesitation_deviation",
        "context_ids": ["hesitation_deviation_02"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 1203.32,
        "transcript_end_seconds": 1212.10,
        "transcript": "A race was dying. The whole planet wiped out. When you cross the Doctor, it happens sometimes. He never does it with guns, but with words.",
        "speaker_role": "Benny first-person narration",
        "primary_emotion": "Grave reflection",
        "secondary_emotion": "Fearful awe",
        "dramatic_function": "Ominous exposition",
        "intensity_1_to_5": 3,
        "selection_reason": "Four short sentences form one sober thought about the Doctor's destructive power. The delivery should be weighty, not merely neutral narration.",
    },
    {
        "clip_id": "benny_hesitation_cold_temptation",
        "source_key": "hesitation_deviation",
        "context_ids": ["hesitation_deviation_03"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 1730.32,
        "transcript_end_seconds": 1738.16,
        "transcript": "The Doctor looked up at me, desperate and pleading, truly frightened. It would be so easy to end it all here.",
        "speaker_role": "Benny first-person narration while infected",
        "primary_emotion": "Cold temptation",
        "secondary_emotion": "Dissociated menace",
        "dramatic_function": "Possessed internal threat",
        "intensity_1_to_5": 4,
        "selection_reason": "The first sentence establishes the Doctor's fear; the second reveals Benny's infected temptation. Isolating either sentence alone would remove the emotional reversal.",
    },
    {
        "clip_id": "benny_hesitation_fatalistic_dread",
        "source_key": "hesitation_deviation",
        "context_ids": ["hesitation_deviation_04", "hesitation_deviation_05", "hesitation_deviation_06"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 1272.46,
        "transcript_end_seconds": 1277.10,
        "transcript": "It wasn't bad luck that they'd found the Doctor. It was inevitable.",
        "speaker_role": "Benny first-person narration",
        "primary_emotion": "Dread",
        "secondary_emotion": "Fatalistic certainty",
        "dramatic_function": "Ominous realization",
        "intensity_1_to_5": 3,
        "selection_reason": "The duplicate seed windows all cover one scene. This two-sentence thought is the cleanest complete unit expressing inevitability and dread.",
    },
    {
        "clip_id": "benny_hesitation_fearful_vigilance",
        "source_key": "hesitation_deviation",
        "context_ids": ["hesitation_deviation_04", "hesitation_deviation_05", "hesitation_deviation_06"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 1291.30,
        "transcript_end_seconds": 1298.46,
        "transcript": "There was that noise on the air, the weird tugging whisper of that tumult of voices calling back and forth.",
        "speaker_role": "Benny first-person narration",
        "primary_emotion": "Fearful vigilance",
        "secondary_emotion": "Unease",
        "dramatic_function": "Suspenseful threat awareness",
        "intensity_1_to_5": 3,
        "selection_reason": "The sentence is self-contained sensory threat detection. It is distinct from the fatalistic realization and the later reassurance in the same scene.",
    },
    {
        "clip_id": "benny_hesitation_protective_reassurance",
        "source_key": "hesitation_deviation",
        "context_ids": ["hesitation_deviation_04", "hesitation_deviation_05", "hesitation_deviation_06"],
        "target": "benny",
        "target_label": "Benny",
        "transcript_start_seconds": 1303.18,
        "transcript_end_seconds": 1307.04,
        "transcript": "Wherever we land, it's fine, I told him.",
        "speaker_role": "Benny direct dialogue within first-person narration",
        "primary_emotion": "Protective reassurance",
        "secondary_emotion": "Strain",
        "dramatic_function": "Comfort under pressure",
        "intensity_1_to_5": 2,
        "selection_reason": "This is Benny's complete reply before the Doctor answers 'It's not fine.' The boundary deliberately excludes his response.",
    },
    {
        "clip_id": "doctor_acf_fond_reminiscence",
        "source_key": "all_consuming_fire",
        "context_ids": ["all_consuming_fire_03"],
        "target": "doctor",
        "target_label": "Doctor",
        "transcript_start_seconds": 3657.99,
        "transcript_end_seconds": 3667.25,
        "transcript": "I was fascinated, but my granddaughter wanted to travel on and she always could talk me round. Well, almost always.",
        "speaker_role": "Seventh Doctor direct dialogue",
        "primary_emotion": "Fond nostalgia",
        "secondary_emotion": "Playful self-deprecation",
        "dramatic_function": "Warm reminiscence",
        "intensity_1_to_5": 2,
        "selection_reason": "The reference to his granddaughter and the comic qualification identify this as the Doctor's own reflective turn, not the surrounding interrogators.",
    },
    {
        "clip_id": "doctor_acf_dismissive_contempt",
        "source_key": "all_consuming_fire",
        "context_ids": ["all_consuming_fire_04"],
        "target": "doctor",
        "target_label": "Doctor",
        "transcript_start_seconds": 5125.43,
        "transcript_end_seconds": 5129.51,
        "transcript": "Oh, just another potty little bully. Never mind. Forget it.",
        "speaker_role": "Seventh Doctor direct dialogue",
        "primary_emotion": "Dismissive contempt",
        "secondary_emotion": "Dry amusement",
        "dramatic_function": "Defiant comic put-down",
        "intensity_1_to_5": 3,
        "selection_reason": "The Doctor punctures the villain's 'I will be God' speech with a self-contained contemptuous dismissal.",
    },
    {
        "clip_id": "doctor_acf_emergency_command",
        "source_key": "all_consuming_fire",
        "context_ids": ["all_consuming_fire_04"],
        "target": "doctor",
        "target_label": "Doctor",
        "transcript_start_seconds": 5129.61,
        "transcript_end_seconds": 5135.95,
        "transcript": "The portal is open! By the left, quick, march!",
        "speaker_role": "Seventh Doctor direct dialogue",
        "primary_emotion": "Urgency",
        "secondary_emotion": "Command authority",
        "dramatic_function": "Emergency command",
        "intensity_1_to_5": 4,
        "selection_reason": "This follows directly from the Doctor's put-down and is a complete command turn. It is separated from the later villain dialogue.",
    },
    {
        "clip_id": "doctor_acf_playful_introduction",
        "source_key": "all_consuming_fire",
        "context_ids": ["all_consuming_fire_05"],
        "target": "doctor",
        "target_label": "Doctor",
        "transcript_start_seconds": 3449.13,
        "transcript_end_seconds": 3457.87,
        "transcript": "Hello, I'm the Doctor, and this is my friend John Watson. Well, Sherlock's friend John Watson, really, but I don't have one of my own available just now.",
        "speaker_role": "Seventh Doctor direct dialogue",
        "primary_emotion": "Playful eccentricity",
        "secondary_emotion": "Self-deprecating humor",
        "dramatic_function": "Comic social introduction",
        "intensity_1_to_5": 2,
        "selection_reason": "The self-identification proves the speaker, and the correction about Watson forms a complete comic introduction.",
    },
)

REJECTIONS: tuple[dict[str, Any], ...] = (
    {
        "context_ids": ["criminal_code_05"],
        "reason": "mixed_character_material",
        "detail": "The window contains narration, quoted Doctor dialogue, action narration, and only the one-word Benny shout 'Shield!'. No conditioning-length Benny utterance is cleanly isolated.",
    },
    {
        "context_ids": ["criminal_code_06"],
        "reason": "actor_interview_not_character",
        "detail": "The transcript discusses Bernice Summerfield as a fictional character. It is interview speech, not Benny performance.",
    },
    {
        "context_ids": ["hesitation_deviation_05", "hesitation_deviation_06"],
        "reason": "duplicate_contexts_consolidated",
        "detail": "These windows duplicate hesitation_deviation_04. Three distinct complete utterances were extracted once from the shared scene.",
    },
    {
        "context_ids": ["all_consuming_fire_01"],
        "reason": "wrong_speaker_watson_narration",
        "detail": "The transcript is Watson's first-person narration and contains no Doctor utterance in the seed region.",
    },
    {
        "context_ids": ["all_consuming_fire_02"],
        "reason": "speaker_ambiguous_mixed_cast",
        "detail": "The seed sits in Watson narration and transitions into several speakers. 'Good Lord!' cannot be assigned to the Doctor from transcript continuity with sufficient confidence.",
    },
    {
        "context_ids": ["all_consuming_fire_06"],
        "reason": "effects_heavy_and_speaker_ambiguous",
        "detail": "The seed is dominated by repeated laughter/effects. Later cell dialogue is not securely attributable to the Doctor from transcript continuity alone.",
    },
)


class TranscriptBankError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", value.casefold()))


def extract_clip(source: Path, output: Path, start: float, end: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    audio_start = max(0.0, start - 0.12)
    audio_end = end + 0.18
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{audio_start:.3f}",
            "-to",
            f"{audio_end:.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )


def build(args: argparse.Namespace) -> dict[str, Any]:
    contexts_path = Path(args.contexts).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not contexts_path.is_file():
        raise TranscriptBankError(f"Context transcript file is missing: {contexts_path}")
    context_payload = json.loads(contexts_path.read_text(encoding="utf-8"))
    contexts = {row["context_id"]: row for row in context_payload.get("contexts", [])}
    clips_root = output_root / "clips"
    clips_root.mkdir(parents=True, exist_ok=True)

    accepted = []
    for decision in DECISIONS:
        source = SOURCES[decision["source_key"]]
        if not source.is_file():
            raise TranscriptBankError(f"Source is missing: {source}")
        relevant_words = []
        for context_id in decision["context_ids"]:
            context = contexts.get(context_id)
            if context is None:
                raise TranscriptBankError(f"Decision references missing context: {context_id}")
            for word in context.get("words", []):
                if (
                    float(word["end_seconds"]) >= decision["transcript_start_seconds"] - 0.1
                    and float(word["start_seconds"]) <= decision["transcript_end_seconds"] + 0.1
                ):
                    relevant_words.append(word["text"])
        observed = normalize_text(" ".join(relevant_words))
        expected = normalize_text(decision["transcript"])
        if expected not in observed and observed not in expected:
            raise TranscriptBankError(
                f"Transcript mismatch for {decision['clip_id']}: expected={expected!r}, observed={observed!r}"
            )
        output = clips_root / f"{decision['clip_id']}.wav"
        extract_clip(
            source,
            output,
            float(decision["transcript_start_seconds"]),
            float(decision["transcript_end_seconds"]),
        )
        accepted.append(
            {
                **decision,
                "source_path": str(source.resolve()),
                "source_sha256": sha256_file(source),
                "audio_path": str(output.resolve()),
                "audio_sha256": sha256_file(output),
                "audio_start_seconds": round(max(0.0, float(decision["transcript_start_seconds"]) - 0.12), 3),
                "audio_end_seconds": round(float(decision["transcript_end_seconds"]) + 0.18, 3),
                "selection_status": "assistant_transcript_guided_candidate",
                "user_correction_required_before_bank_approval": True,
                "production_promotion_allowed": False,
            }
        )

    payload = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "selection_policy": context_payload.get("selection_policy"),
        "accepted_count": len(accepted),
        "rejected_context_count": sum(len(row["context_ids"]) for row in REJECTIONS),
        "accepted_candidates": accepted,
        "rejected_contexts": list(REJECTIONS),
        "production_promotion_allowed": False,
    }
    output = output_root / "transcript-guided-bank.json"
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"accepted_count": len(accepted), "output": str(output)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    bank = Path(args.bank).expanduser().resolve()
    if not bank.is_file():
        raise TranscriptBankError(f"Bank is missing: {bank}")
    payload = json.loads(bank.read_text(encoding="utf-8"))
    rows = payload.get("accepted_candidates")
    if not isinstance(rows, list) or len(rows) != len(DECISIONS):
        raise TranscriptBankError("Bank does not contain the complete transcript-guided candidate set.")
    failures = []
    for row in rows:
        audio = Path(row["audio_path"])
        if not row.get("transcript"):
            failures.append(f"{row.get('clip_id')}: missing transcript")
        if not row.get("selection_reason"):
            failures.append(f"{row.get('clip_id')}: missing transcript/scene decision reason")
        if not row.get("speaker_role"):
            failures.append(f"{row.get('clip_id')}: missing speaker role")
        if not row.get("primary_emotion") or not row.get("dramatic_function"):
            failures.append(f"{row.get('clip_id')}: missing assistant emotion judgment")
        if not audio.is_file():
            failures.append(f"{row.get('clip_id')}: audio missing")
        elif sha256_file(audio) != row.get("audio_sha256"):
            failures.append(f"{row.get('clip_id')}: audio hash mismatch")
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"{row.get('clip_id')}: production promotion enabled")
    if payload.get("selection_policy", {}).get("speaker_embedding_role") != "coarse_locator_only":
        failures.append("speaker embedding is not limited to coarse locator role")
    if failures:
        raise TranscriptBankError("; ".join(failures))
    return {"accepted_count": len(rows), "failure_count": 0}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract assistant-labeled source references from transcript-guided decisions.")
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--contexts", required=True)
    build_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--bank", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = build(args) if args.command == "build" else validate(args)
    except (TranscriptBankError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
