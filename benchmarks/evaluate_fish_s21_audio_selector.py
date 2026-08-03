#!/usr/bin/env python3
"""Evaluate automatic Fish S2.1 candidate selection against blind human scores."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import torch
from safetensors.torch import load_file
from transformers import (
    AutoFeatureExtractor,
    AutoModelForAudioClassification,
    ClapModel,
    ClapProcessor,
    Wav2Vec2Config,
    Wav2Vec2Model,
)

from fish_cloud_tts import (
    SpeakerSimilarityScorer,
    audio_features,
    delivery_score,
    quality_score,
)


CLAP_REVISION = "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
CLAP_REPO = "laion/clap-htsat-unfused"
DEFAULT_EVIDENCE = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-research-fish-s21-prompt-calibration/"
    ".omo/evidence/fish-s21-prompt-controls"
)
SER_REVISION = "b520c9c46a719e36e1b9a91cad2cb5d0668757d8"
SER_REPO = "ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition"
SMALL_SER_REVISION = "033a5751a5bbe5b0b67c2c71e6102c38de35a346"
SMALL_SER_REPO = "Dpngtm/wav2vec2-emotion-recognition"
DEFAULT_SER_SNAPSHOT = Path(
    "/Users/tristan/pinokio/cache/alexandria-evaluation/huggingface/hub/"
    "models--ehcalabres--wav2vec2-lg-xlsr-en-speech-emotion-recognition/"
    "snapshots/"
    + SER_REVISION
)
DEFAULT_CLAP_SNAPSHOT = Path(
    "/Users/tristan/pinokio/cache/alexandria-evaluation/huggingface/hub/"
    "models--laion--clap-htsat-unfused/snapshots/"
    + CLAP_REVISION
)

STYLE_TEXTS = {
    "neutral": [
        "A person speaking naturally and neutrally with clear diction and restrained expression.",
        "Calm ordinary narration with stable pacing and no strong emotion.",
        "A neutral spoken line, clear, natural, and emotionally restrained.",
    ],
    "grief": [
        "A person speaking with deep personal grief, pain held back, close to breaking down.",
        "A sorrowful voice carrying loss, restrained crying, and emotional pain.",
        "Grieving speech with sadness, fragility, and the effort not to cry.",
    ],
    "sarcastic": [
        "A person speaking with dry sarcasm, amused disbelief, and ironic emphasis.",
        "Understated sarcastic speech with dry wit and mocking disbelief.",
        "A sarcastic spoken line using ironic timing without broad comedy.",
    ],
    "fear": [
        "A person speaking with unmistakable fear, tight uneven breath, and nearby danger.",
        "Fearful speech with breath catching, tense caution, and immediate threat.",
        "A scared voice, alert to danger, with anxious uneven breathing.",
    ],
}

TARGET_TEXTS = {
    "neutral": "The envelope rested beside the lamp, exactly where she had left it.",
    "grief": "There was no goodbye, only the empty chair and the silence afterward.",
    "sarcastic": "Brilliant. Another flawless plan, and only three things are on fire.",
    "fear": "A floorboard creaked behind him, and suddenly he knew he was no longer alone.",
}

REFERENCES = {
    "ryan_synthetic": (
        Path(
            "/Users/tristan/.devspace/worktrees/"
            "alexandria-research-multimodel-voice-benchmark/"
            ".omo/evidence/b17-t05-multimodel-round1/references/ryan/"
            "ryan_neutral_anchor.wav"
        ),
        "The lantern stood on the table beside a stack of unopened letters.",
    ),
    "narrator": (
        Path(
            "/Users/tristan/.devspace/worktrees/"
            "alexandria-research-fish-s21-permitted-clones/"
            ".omo/evidence/fish-s21-permitted-clones/narrator/private/"
            "prepared-references/full_source.wav"
        ),
        "This is the story of a man named Stanley. Stanley worked for a company in a big building where he was employee number 427.",
    ),
    "benny": (
        Path(
            "/Users/tristan/.devspace/worktrees/"
            "alexandria-research-fish-s21-permitted-clones/"
            ".omo/evidence/fish-s21-permitted-clones/benny/private/"
            "prepared-references/full_source.wav"
        ),
        "Just the five of us against the might and money of Irving Braxietel. But we cannot keep running and hiding.",
    ),
    "doctor": (
        Path(
            "/Users/tristan/.devspace/worktrees/"
            "alexandria-research-fish-s21-permitted-clones/"
            ".omo/evidence/fish-s21-permitted-clones/doctor/private/"
            "prepared-references/full_source.wav"
        ),
        "The portal through which Hector Thomas entered this world, and the means by which he is supposed to leave it.",
    ),
}

SER_STYLE_WEIGHTS = {
    "neutral": {"neutral": 0.65, "calm": 0.35},
    "grief": {"sad": 1.0},
    "fear": {"fearful": 1.0},
    # RAVDESS does not contain sarcasm. This weak proxy is reported but must
    # not be treated as accepted production evidence without calibration.
    "sarcastic": {"disgust": 0.45, "happy": 0.35, "angry": 0.20},
}

PROMPT_PRIORS = {
    "neutral": {
        "simple_tag": 1.0,
        "untagged": 0.75,
        "full_alexandria_tag": 0.55,
        "rich_tag": 0.45,
    },
    "grief": {
        "full_alexandria_tag": 1.0,
        "rich_tag": 0.75,
        "untagged": 0.55,
        "simple_tag": 0.4,
    },
    "sarcastic": {
        "rich_tag": 1.0,
        "full_alexandria_tag": 0.75,
        "untagged": 0.5,
        "simple_tag": 0.4,
    },
    "fear": {
        "full_alexandria_tag": 1.0,
        "rich_tag": 0.8,
        "simple_tag": 0.55,
        "untagged": 0.25,
    },
}


def pooled_features(value: Any) -> torch.Tensor:
    pooled = getattr(value, "pooler_output", None)
    return pooled if pooled is not None else value


def normalized(vector: torch.Tensor) -> torch.Tensor:
    return vector / vector.norm(dim=-1, keepdim=True).clamp_min(1e-12)


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 2:
        return float("nan")
    return float(np.corrcoef(np.asarray(left), np.asarray(right))[0, 1])


def rank_correlation(left: list[float], right: list[float]) -> float:
    x = np.argsort(np.argsort(np.asarray(left)))
    y = np.argsort(np.argsort(np.asarray(right)))
    return correlation(x.tolist(), y.tolist())


class StandardEmotionModel:
    """Load a current Transformers audio-classification checkpoint."""

    def __init__(self, snapshot: Path, device: torch.device) -> None:
        self.snapshot = snapshot.resolve()
        self.device = device
        self.extractor = AutoFeatureExtractor.from_pretrained(
            self.snapshot,
            local_files_only=True,
        )
        self.model = AutoModelForAudioClassification.from_pretrained(
            self.snapshot,
            local_files_only=True,
        ).to(device).eval()
        self.labels = {
            int(index): str(label).casefold()
            for index, label in self.model.config.id2label.items()
        }

    def probabilities(self, path: Path) -> dict[str, float]:
        audio, _ = librosa.load(path, sr=16000, mono=True)
        inputs = self.extractor(
            audio,
            sampling_rate=16000,
            return_tensors="pt",
        )
        with torch.inference_mode():
            logits = self.model(
                **{key: value.to(self.device) for key, value in inputs.items()}
            ).logits
            values = torch.softmax(logits, dim=-1)[0].detach().cpu().tolist()
        return {
            self.labels[index]: float(value)
            for index, value in enumerate(values)
        }


class LegacyRavdessEmotionModel:
    """Load the original 2021 custom classifier head without random weights."""

    def __init__(self, snapshot: Path, device: torch.device) -> None:
        self.snapshot = snapshot.resolve()
        self.device = device
        self.config = Wav2Vec2Config.from_pretrained(
            self.snapshot,
            local_files_only=True,
        )
        state = load_file(str(self.snapshot / "model.safetensors"))
        self.backbone = Wav2Vec2Model(self.config)
        backbone_state = {
            key.removeprefix("wav2vec2."): value
            for key, value in state.items()
            if key.startswith("wav2vec2.")
        }
        missing, unexpected = self.backbone.load_state_dict(
            backbone_state,
            strict=False,
        )
        if missing or unexpected:
            raise RuntimeError(
                f"SER backbone mismatch: missing={missing}, unexpected={unexpected}"
            )
        dense_weight = state["classifier.dense.weight"]
        output_weight = state["classifier.output.weight"]
        self.dense = torch.nn.Linear(
            dense_weight.shape[1],
            dense_weight.shape[0],
        )
        self.output = torch.nn.Linear(
            output_weight.shape[1],
            output_weight.shape[0],
        )
        self.dense.load_state_dict(
            {
                "weight": dense_weight,
                "bias": state["classifier.dense.bias"],
            }
        )
        self.output.load_state_dict(
            {
                "weight": output_weight,
                "bias": state["classifier.output.bias"],
            }
        )
        self.extractor = AutoFeatureExtractor.from_pretrained(
            self.snapshot,
            local_files_only=True,
        )
        self.backbone.to(device).eval()
        self.dense.to(device).eval()
        self.output.to(device).eval()
        self.labels = {
            int(index): str(label)
            for index, label in self.config.id2label.items()
        }

    def probabilities(self, path: Path) -> dict[str, float]:
        audio, _ = librosa.load(path, sr=16000, mono=True)
        inputs = self.extractor(
            audio,
            sampling_rate=16000,
            return_tensors="pt",
        )
        with torch.inference_mode():
            hidden = self.backbone(
                **{key: value.to(self.device) for key, value in inputs.items()}
            ).last_hidden_state.mean(dim=1)
            logits = self.output(torch.tanh(self.dense(hidden)))
            values = torch.softmax(logits, dim=-1)[0].detach().cpu().tolist()
        return {
            self.labels[index]: float(value)
            for index, value in enumerate(values)
        }


def load_rows(evidence: Path, scores_path: Path) -> list[dict[str, Any]]:
    fixture = json.loads(scores_path.read_text(encoding="utf-8"))
    columns = list(fixture["columns"])
    scores = fixture["scores"]
    rows: list[dict[str, Any]] = []
    for identity in REFERENCES:
        answer = json.loads(
            (evidence / identity / "private/answer-key.json").read_text(
                encoding="utf-8"
            )
        )
        for source in answer["rows"]:
            if source.get("kind") != "fish_cloud":
                continue
            sample_id = source["sample_id"]
            values = scores.get(sample_id)
            if values is None:
                continue
            rating = dict(zip(columns, values))
            rows.append(
                {
                    **source,
                    **rating,
                    "audio_path": evidence / identity / "review/audio" / f"{sample_id}.wav",
                }
            )
    return rows


def text_embeddings(
    model: ClapModel,
    processor: ClapProcessor,
    device: torch.device,
) -> tuple[list[str], torch.Tensor]:
    styles = list(STYLE_TEXTS)
    vectors = []
    with torch.inference_mode():
        for style in styles:
            inputs = processor(
                text=STYLE_TEXTS[style],
                return_tensors="pt",
                padding=True,
            )
            features = pooled_features(
                model.get_text_features(
                    **{key: value.to(device) for key, value in inputs.items()}
                )
            )
            vectors.append(normalized(features).mean(dim=0))
    return styles, normalized(torch.stack(vectors))


def add_clap_scores(
    rows: list[dict[str, Any]],
    *,
    model: ClapModel,
    processor: ClapProcessor,
    device: torch.device,
    batch_size: int,
) -> None:
    styles, texts = text_embeddings(model, processor, device)
    style_index = {style: index for index, style in enumerate(styles)}
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        audio = [
            librosa.load(row["audio_path"], sr=48000, mono=True)[0]
            for row in batch
        ]
        inputs = processor(
            audio=audio,
            sampling_rate=48000,
            return_tensors="pt",
            padding=True,
        )
        with torch.inference_mode():
            features = pooled_features(
                model.get_audio_features(
                    **{key: value.to(device) for key, value in inputs.items()}
                )
            )
            similarities = normalized(features) @ texts.T
        values = similarities.detach().cpu().numpy()
        for row, vector in zip(batch, values):
            target = style_index[row["style"]]
            other = np.delete(vector, target)
            row["clap_target"] = float(vector[target])
            row["clap_margin"] = float(vector[target] - np.max(other))
            probabilities = np.exp(vector * 20 - np.max(vector * 20))
            probabilities = probabilities / probabilities.sum()
            row["clap_probability"] = float(probabilities[target])
            row["clap_vector"] = {
                style: float(vector[index]) for index, style in enumerate(styles)
            }


def add_acoustic_scores(rows: list[dict[str, Any]]) -> None:
    reference_features = {
        identity: audio_features(path, text)
        for identity, (path, text) in REFERENCES.items()
    }
    for row in rows:
        features = audio_features(row["audio_path"], TARGET_TEXTS[row["style"]])
        row.update(
            {
                "duration_seconds": features.duration_seconds,
                "words_per_second": features.words_per_second,
                "rms_mean": features.rms_mean,
                "rms_cv": features.rms_cv,
                "pitch_median_hz": features.pitch_median_hz,
                "pitch_cv": features.pitch_cv,
                "spectral_centroid_hz": features.spectral_centroid_hz,
                "silence_ratio": features.silence_ratio,
                "clipping_ratio": features.clipping_ratio,
            }
        )
        row["acoustic"] = delivery_score(
            row["style"],
            features,
            reference_features[row["identity"]],
        )
        row["quality"] = quality_score(features)
        row["prompt_prior"] = PROMPT_PRIORS[row["style"]].get(
            row["prompt_mode"],
            0.0,
        )


def add_identity_scores(rows: list[dict[str, Any]]) -> None:
    scorer = SpeakerSimilarityScorer()
    for index, row in enumerate(rows, start=1):
        reference = REFERENCES[row["identity"]][0]
        score, mode = scorer.score(reference, row["audio_path"])
        row["speaker_similarity"] = float(score)
        row["speaker_similarity_mode"] = mode
        if index % 16 == 0:
            print(
                json.dumps(
                    {
                        "identity_scored": index,
                        "identity_total": len(rows),
                    }
                ),
                flush=True,
            )


def add_ser_scores(
    rows: list[dict[str, Any]],
    *,
    model: LegacyRavdessEmotionModel | StandardEmotionModel,
) -> None:
    for row in rows:
        probabilities = model.probabilities(row["audio_path"])
        weights = SER_STYLE_WEIGHTS[row["style"]]
        target = sum(
            probabilities.get(label, 0.0) * weight
            for label, weight in weights.items()
        )
        other = max(
            value
            for label, value in probabilities.items()
            if label not in weights
        )
        row["ser_probabilities"] = probabilities
        row["ser_target"] = float(target)
        row["ser_margin"] = float(target - other)


def routed_repeat_summary(
    rows: list[dict[str, Any]],
    score_name: str,
    repeat_score,
) -> dict[str, Any]:
    """Simulate production: route prompt first, then choose one valid repeat."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["identity"], row["style"])].append(row)
    selections = []
    for (identity, style), candidates in sorted(groups.items()):
        prompt_order = sorted(
            {row["prompt_mode"] for row in candidates},
            key=lambda prompt: PROMPT_PRIORS[style].get(prompt, 0.0),
            reverse=True,
        )
        selected = None
        selected_prompt_rank = None
        for prompt_rank, prompt_mode in enumerate(prompt_order):
            eligible = [
                row
                for row in candidates
                if row["prompt_mode"] == prompt_mode
                and row["spoken_text_matches_expected"] is True
                and row["quality"] >= 0.65
                and (
                    row["speaker_similarity_mode"] != "mlx_qwen"
                    or row["speaker_similarity"] >= 0.78
                )
            ]
            if not eligible:
                continue
            selected = max(eligible, key=repeat_score)
            selected_prompt_rank = prompt_rank
            break
        if selected is None:
            continue
        selections.append(
            {
                "identity": identity,
                "style": style,
                "sample_id": selected["sample_id"],
                "prompt_mode": selected["prompt_mode"],
                "prompt_rank": selected_prompt_rank,
                "repeat": selected.get("repeat"),
                "score": float(repeat_score(selected)),
                "speaker_similarity": selected["speaker_similarity"],
                "identity_rating": selected["identity_1_to_5"],
                "delivery_rating": selected["delivery_1_to_5"],
                "naturalness_rating": selected["naturalness_1_to_5"],
                "approved": selected["approve_for_comparison"],
                "mode_clear": selected["requested_mode_is_clear"],
                "human_best_delivery": max(
                    float(candidate["delivery_1_to_5"])
                    for candidate in candidates
                    if candidate["spoken_text_matches_expected"] is True
                ),
            }
        )
    if not selections:
        raise RuntimeError(f"Routed selector {score_name!r} selected no rows")
    natural = [
        float(item["naturalness_rating"])
        for item in selections
        if item["naturalness_rating"] is not None
    ]
    return {
        "score": score_name,
        "group_count": len(selections),
        "fallback_prompt_rate": sum(
            int(item["prompt_rank"] > 0) for item in selections
        ) / len(selections),
        "approval_rate": sum(item["approved"] for item in selections)
        / len(selections),
        "mode_clear_rate": sum(item["mode_clear"] for item in selections)
        / len(selections),
        "identity_mean": sum(float(item["identity_rating"]) for item in selections)
        / len(selections),
        "delivery_mean": sum(float(item["delivery_rating"]) for item in selections)
        / len(selections),
        "naturalness_mean": sum(natural) / len(natural),
        "available_human_best_delivery_mean": sum(
            item["human_best_delivery"] for item in selections
        ) / len(selections),
        "selections": selections,
    }


def selection_summary(
    rows: list[dict[str, Any]],
    score_name: str,
    score_fn,
) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["identity"], row["style"])].append(row)
    selections = []
    for (identity, style), candidates in sorted(groups.items()):
        selected = max(candidates, key=score_fn)
        selections.append(
            {
                "identity": identity,
                "style": style,
                "sample_id": selected["sample_id"],
                "prompt_mode": selected["prompt_mode"],
                "repeat": selected.get("repeat"),
                "score": float(score_fn(selected)),
                "identity_rating": selected["identity_1_to_5"],
                "delivery_rating": selected["delivery_1_to_5"],
                "naturalness_rating": selected["naturalness_1_to_5"],
                "approved": selected["approve_for_comparison"],
                "mode_clear": selected["requested_mode_is_clear"],
                "human_best_delivery": max(
                    float(candidate["delivery_1_to_5"])
                    for candidate in candidates
                ),
            }
        )
    return {
        "score": score_name,
        "group_count": len(selections),
        "approval_rate": sum(item["approved"] for item in selections)
        / len(selections),
        "mode_clear_rate": sum(item["mode_clear"] for item in selections)
        / len(selections),
        "identity_mean": sum(float(item["identity_rating"]) for item in selections)
        / len(selections),
        "delivery_mean": sum(float(item["delivery_rating"]) for item in selections)
        / len(selections),
        "naturalness_mean": sum(
            float(item["naturalness_rating"])
            for item in selections
            if item["naturalness_rating"] is not None
        ) / sum(item["naturalness_rating"] is not None for item in selections),
        "available_human_best_delivery_mean": sum(
            item["human_best_delivery"] for item in selections
        ) / len(selections),
        "selections": selections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument(
        "--scores",
        type=Path,
        default=Path(__file__).with_name(
            "fish_s21_prompt_control_human_scores.json"
        ),
    )
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_CLAP_SNAPSHOT)
    parser.add_argument(
        "--ser-snapshot",
        type=Path,
        default=DEFAULT_SER_SNAPSHOT,
    )
    parser.add_argument(
        "--ser-loader",
        choices=("legacy", "standard"),
        default="legacy",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows = load_rows(args.evidence.resolve(), args.scores.resolve())
    if len(rows) != 128:
        raise RuntimeError(f"Expected 128 Fish rows, found {len(rows)}")
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    processor = ClapProcessor.from_pretrained(
        args.snapshot,
        local_files_only=True,
    )
    model = ClapModel.from_pretrained(
        args.snapshot,
        local_files_only=True,
    ).to(device).eval()
    add_clap_scores(
        rows,
        model=model,
        processor=processor,
        device=device,
        batch_size=max(1, args.batch_size),
    )
    add_acoustic_scores(rows)
    add_identity_scores(rows)
    ser_model = (
        LegacyRavdessEmotionModel(args.ser_snapshot, device)
        if args.ser_loader == "legacy"
        else StandardEmotionModel(args.ser_snapshot, device)
    )
    add_ser_scores(rows, model=ser_model)

    human_delivery = [float(row["delivery_1_to_5"]) for row in rows]
    metrics = {}
    for key in (
        "clap_target",
        "clap_margin",
        "clap_probability",
        "acoustic",
        "ser_target",
        "ser_margin",
        "speaker_similarity",
    ):
        values = [float(row[key]) for row in rows]
        metrics[key] = {
            "pearson_delivery": correlation(values, human_delivery),
            "spearman_delivery": rank_correlation(values, human_delivery),
        }

    selectors = [
        selection_summary(rows, "clap_target", lambda row: row["clap_target"]),
        selection_summary(rows, "clap_margin", lambda row: row["clap_margin"]),
        selection_summary(
            rows,
            "clap_probability",
            lambda row: row["clap_probability"],
        ),
        selection_summary(rows, "acoustic", lambda row: row["acoustic"]),
        selection_summary(rows, "ser_target", lambda row: row["ser_target"]),
        selection_summary(rows, "ser_margin", lambda row: row["ser_margin"]),
        selection_summary(
            rows,
            "speaker_similarity",
            lambda row: row["speaker_similarity"],
        ),
        selection_summary(
            rows,
            "prompt_prior",
            lambda row: row["prompt_prior"],
        ),
    ]
    routed = [
        routed_repeat_summary(
            rows,
            "routed_identity",
            lambda row: row["speaker_similarity"],
        ),
        routed_repeat_summary(
            rows,
            "routed_identity_quality",
            lambda row: row["speaker_similarity"] * 0.85 + row["quality"] * 0.15,
        ),
        routed_repeat_summary(
            rows,
            "routed_identity_ser",
            lambda row: row["speaker_similarity"] * 0.8 + row["ser_target"] * 0.2,
        ),
        routed_repeat_summary(
            rows,
            "routed_identity_ser_quality",
            lambda row: (
                row["speaker_similarity"] * 0.72
                + row["ser_target"] * 0.18
                + row["quality"] * 0.10
            ),
        ),
        routed_repeat_summary(
            rows,
            "routed_clap_ser_identity",
            lambda row: (
                row["speaker_similarity"] * 0.65
                + row["ser_target"] * 0.15
                + row["clap_margin"] * 0.15
                + row["quality"] * 0.05
            ),
        ),
    ]
    grid = []
    for identity_weight in (0.2, 0.5, 0.8, 1.0):
        for clap_weight in (0.0, 0.2):
            for ser_weight in (0.0, 0.2):
                for prior_weight in (0.1, 0.2, 0.3, 0.5):
                    name = (
                        f"identity:{identity_weight}+clap:{clap_weight}+"
                        f"ser:{ser_weight}+prior:{prior_weight}"
                    )
                    summary = selection_summary(
                        rows,
                        name,
                        lambda row, iw=identity_weight, cw=clap_weight, sw=ser_weight, pw=prior_weight: (
                            row["speaker_similarity"] * iw
                            + row["clap_margin"] * cw
                            + row["ser_target"] * sw
                            + row["prompt_prior"] * pw
                        ),
                    )
                    grid.append(summary)
    grid.sort(
        key=lambda item: (
            item["approval_rate"],
            item["mode_clear_rate"],
            item["identity_mean"],
            item["delivery_mean"],
        ),
        reverse=True,
    )
    report = {
        "schema_version": 1,
        "clap": {
            "repo": CLAP_REPO,
            "revision": CLAP_REVISION,
            "snapshot": str(args.snapshot.resolve()),
            "device": str(device),
        },
        "ser": {
            "repo": SER_REPO if args.ser_loader == "legacy" else SMALL_SER_REPO,
            "revision": SER_REVISION if args.ser_loader == "legacy" else SMALL_SER_REVISION,
            "snapshot": str(args.ser_snapshot.resolve()),
            "labels": ser_model.labels,
            "device": str(device),
        },
        "sample_count": len(rows),
        "metrics": metrics,
        "baseline_selectors": selectors,
        "routed_repeat_selectors": routed,
        "top_grid": grid[:10],
        "calibration_rows": [
            {
                key: row.get(key)
                for key in (
                    "sample_id",
                    "identity",
                    "style",
                    "prompt_mode",
                    "repeat",
                    "identity_1_to_5",
                    "delivery_1_to_5",
                    "naturalness_1_to_5",
                    "artifact_severity_1_to_5",
                    "spoken_text_matches_expected",
                    "requested_mode_is_clear",
                    "approve_for_comparison",
                    "clap_target",
                    "clap_margin",
                    "clap_probability",
                    "acoustic",
                    "quality",
                    "duration_seconds",
                    "words_per_second",
                    "rms_mean",
                    "rms_cv",
                    "pitch_median_hz",
                    "pitch_cv",
                    "spectral_centroid_hz",
                    "silence_ratio",
                    "clipping_ratio",
                    "ser_target",
                    "ser_margin",
                    "speaker_similarity",
                    "speaker_similarity_mode",
                    "prompt_prior",
                )
            }
            for row in rows
        ],
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
