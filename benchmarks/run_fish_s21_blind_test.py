#!/usr/bin/env python3
"""Generate and package Alexandria's Fish S2.1 Pro calibration blind test.

The API credential is read only from FISH_API_KEY or FISH_AUDIO_API_KEY. It is
never written to receipts, manifests, logs, or the review package.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import shutil
import stat
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from fish_s21_blind_contract import (  # noqa: E402
    CONFIG_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_ROUND1_ROOT,
    blind_id,
    build_prompt,
    canonical_json,
    expected_counts,
    load_config,
    reference_tier_payloads,
    sample_fingerprint,
    sha256_bytes,
    sha256_file,
    sha256_value,
)

API_BASE = "https://api.fish.audio"
ASSET_ROOT = BENCHMARKS / "fish_s21_review_assets"
ASSET_FILES = ("index.html", "app.js", "styles.css")
PRIVATE_STATE_FILE = "private/fish-voice-models.json"
PRIVATE_SECRET_FILE = "private/blind-secret.txt"
PRIVATE_ANSWER_FILE = "private/answer-key.json"
PUBLIC_ROOT_NAME = "review"


class FishBlindRunError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GeneratedSample:
    fingerprint: str
    audio_path: Path
    audio_sha256: str
    duration_seconds: float
    answer: dict[str, Any]


class FishClient:
    def __init__(
        self,
        *,
        api_key: str,
        model_header: str,
        base_url: str = API_BASE,
        session: requests.Session | None = None,
        max_attempts: int = 4,
    ) -> None:
        if not api_key:
            raise FishBlindRunError("fish_api_key_missing", "Set FISH_API_KEY.")
        self._api_key = api_key
        self.model_header = model_header
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.max_attempts = max(1, int(max_attempts))

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _safe_detail(self, response: requests.Response) -> str:
        try:
            payload = response.json()
            value = payload.get("message") or payload.get("detail") or payload.get("status")
            detail = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        except Exception:
            detail = response.text[:500]
        return detail.replace(self._api_key, "[redacted]")

    def request(
        self,
        method: str,
        path: str,
        *,
        retryable: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        headers = {**self.headers, **dict(kwargs.pop("headers", {}))}
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=kwargs.pop("timeout", 180),
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_error = exc
                if not retryable or attempt >= self.max_attempts:
                    raise FishBlindRunError("fish_network_error", type(exc).__name__) from exc
                time.sleep(min(8.0, 0.75 * (2 ** (attempt - 1))))
                continue
            if response.status_code < 400:
                return response
            if response.status_code == 402:
                raise FishBlindRunError(
                    "fish_billing_or_quota",
                    self._safe_detail(response),
                )
            retry_status = response.status_code == 429 or response.status_code >= 500
            if retryable and retry_status and attempt < self.max_attempts:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 0.75 * (2 ** (attempt - 1))
                except ValueError:
                    delay = 0.75 * (2 ** (attempt - 1))
                time.sleep(min(15.0, max(0.25, delay)))
                continue
            raise FishBlindRunError(
                f"fish_http_{response.status_code}",
                self._safe_detail(response),
            )
        raise FishBlindRunError("fish_request_failed", type(last_error).__name__ if last_error else path)

    def list_owned_models(self, title: str) -> list[dict[str, Any]]:
        response = self.request(
            "GET",
            "/model",
            params={
                "self": "true",
                "title": title,
                "page_size": 100,
                "page_number": 1,
                "sort_by": "created_at",
            },
        )
        payload = response.json()
        return [
            dict(item)
            for item in payload.get("items", [])
            if isinstance(item, Mapping) and item.get("title") == title
        ]

    def create_voice_model(
        self,
        *,
        title: str,
        description: str,
        entries: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        data: list[tuple[str, str]] = [
            ("type", "tts"),
            ("title", title),
            ("train_mode", "fast"),
            ("visibility", "private"),
            ("description", description),
            ("enhance_audio_quality", "true"),
            ("generate_sample", "false"),
            ("tags", "alexandria-evaluation"),
            ("tags", "synthetic-reference"),
        ]
        files: list[tuple[str, tuple[str, Any, str]]] = []
        content_types = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
        }
        with ExitStack() as stack:
            for index, entry in enumerate(entries, start=1):
                path = Path(str(entry["audio_path"]))
                suffix = path.suffix.casefold()
                content_type = content_types.get(suffix)
                if content_type is None:
                    raise FishBlindRunError("fish_reference_format_unsupported", suffix or path.name)
                handle = stack.enter_context(path.open("rb"))
                files.append(("voices", (f"reference-{index}{suffix}", handle, content_type)))
                data.append(("texts", str(entry["text"])))
            response = self.request(
                "POST",
                "/model",
                data=data,
                files=files,
                timeout=180,
                retryable=False,
            )
        payload = response.json()
        if not str(payload.get("_id") or ""):
            raise FishBlindRunError("fish_voice_id_missing", title)
        return dict(payload)

    def delete_voice_model(self, model_id: str) -> None:
        self.request("DELETE", f"/model/{model_id}", retryable=False)

    def synthesize(self, *, text: str, reference_id: str, settings: Mapping[str, Any]) -> bytes:
        response = self.request(
            "POST",
            "/v1/tts",
            headers={
                "Content-Type": "application/json",
                "model": self.model_header,
            },
            json={
                "text": text,
                "reference_id": reference_id,
                "temperature": float(settings["temperature"]),
                "top_p": float(settings["top_p"]),
                "prosody": {
                    "speed": 1.0,
                    "volume": 0,
                    "normalize_loudness": True,
                },
                "normalize": True,
                "format": str(settings["format"]),
                "sample_rate": int(settings["sample_rate"]),
                "latency": str(settings["latency"]),
                "repetition_penalty": float(settings["repetition_penalty"]),
                "condition_on_previous_chunks": bool(
                    settings["condition_on_previous_chunks"]
                ),
                "chunk_length": 200,
                "max_new_tokens": 1024,
                "min_chunk_length": 50,
                "early_stop_threshold": 1,
            },
            timeout=240,
        )
        content = response.content
        if len(content) < 512:
            raise FishBlindRunError("fish_audio_too_small", str(len(content)))
        return content


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")


def audio_metadata(path: Path) -> dict[str, Any]:
    info = sf.info(path)
    if info.frames <= 0 or info.samplerate <= 0 or info.channels != 1:
        raise FishBlindRunError("invalid_audio_artifact", str(path))
    return {
        "duration_seconds": info.frames / info.samplerate,
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "format": str(info.format),
        "subtype": str(info.subtype),
    }


def load_or_create_secret(output_root: Path) -> str:
    path = output_root / PRIVATE_SECRET_FILE
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return value


def load_state(output_root: Path, round_id: str) -> dict[str, Any]:
    path = output_root / PRIVATE_STATE_FILE
    if not path.is_file():
        return {"schema_version": 1, "round_id": round_id, "models": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("round_id") != round_id or not isinstance(payload.get("models"), dict):
        raise FishBlindRunError("fish_state_round_mismatch", str(path))
    return payload


def ensure_models(
    client: FishClient,
    *,
    output_root: Path,
    config: Mapping[str, Any],
    tiers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    state = load_state(output_root, str(config["round_id"]))
    models: dict[str, dict[str, Any]] = dict(state.get("models", {}))
    for tier in tiers:
        fingerprint = sha256_value(
            {
                "tier": tier["key"],
                "entries": [
                    {"audio_sha256": row["audio_sha256"], "text_sha256": row["text_sha256"]}
                    for row in tier["entries"]
                ],
            }
        )
        title = f"Alexandria {config['round_id']} {tier['key']} {fingerprint[:10]}"
        existing = models.get(tier["key"])
        if (
            isinstance(existing, Mapping)
            and existing.get("reference_fingerprint") == fingerprint
            and str(existing.get("model_id") or "")
        ):
            continue
        remote = next(
            (
                item
                for item in client.list_owned_models(title)
                if item.get("state") != "failed" and item.get("visibility") == "private"
            ),
            None,
        )
        if remote is None:
            remote = client.create_voice_model(
                title=title,
                description=(
                    str(config.get("reference_description") or "").strip()
                    or (
                        "Alexandria Fish S2.1 Pro blind-test reference. Synthetic Qwen Ryan "
                        f"tier {tier['key']}; fingerprint {fingerprint}."
                    )
                ),
                entries=tier["entries"],
            )
        models[tier["key"]] = {
            "model_id": remote["_id"],
            "title": title,
            "state": remote.get("state"),
            "visibility": remote.get("visibility"),
            "reference_fingerprint": fingerprint,
            "reference_duration_seconds": tier["duration_seconds"],
            "reference_count": len(tier["entries"]),
        }
        state["models"] = models
        write_json(output_root / PRIVATE_STATE_FILE, state)
    return models


def receipt_matches(path: Path, fingerprint: str, audio_path: Path) -> bool:
    if not path.is_file() or not audio_path.is_file():
        return False
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        return (
            receipt.get("sample_fingerprint") == fingerprint
            and receipt.get("audio_sha256") == sha256_file(audio_path)
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def generate_fish_samples(
    client: FishClient,
    *,
    output_root: Path,
    config: Mapping[str, Any],
    tiers: list[dict[str, Any]],
    models: Mapping[str, Mapping[str, Any]],
    max_samples: int | None = None,
) -> list[GeneratedSample]:
    settings = config["generation"]
    generated: list[GeneratedSample] = []
    scheduled = 0
    for style in config["styles"]:
        for tier in tiers:
            model = models[tier["key"]]
            for mode in config["prompt_modes"]:
                prompt = build_prompt(style, str(mode["key"]))
                for repeat in range(1, int(settings["repeats"]) + 1):
                    if max_samples is not None and scheduled >= max_samples:
                        return generated
                    scheduled += 1
                    request_contract = {
                        "round_id": config["round_id"],
                        "provider": config["provider"],
                        "marketed_model": config["marketed_model"],
                        "api_model_header": config["api_model_header"],
                        "reference_tier": tier["key"],
                        "reference_fingerprint": model["reference_fingerprint"],
                        "style": style["key"],
                        "prompt_mode": mode["key"],
                        "prompt": prompt,
                        "repeat": repeat,
                        "settings": settings,
                    }
                    fingerprint = sample_fingerprint(request_contract)
                    directory = (
                        output_root
                        / "outputs/fish_s21_pro"
                        / str(tier["key"])
                        / str(style["key"])
                        / str(mode["key"])
                    )
                    audio_path = directory / f"repeat-{repeat}.wav"
                    receipt_path = directory / f"repeat-{repeat}.json"
                    if not receipt_matches(receipt_path, fingerprint, audio_path):
                        started = time.perf_counter()
                        audio = client.synthesize(
                            text=prompt,
                            reference_id=str(model["model_id"]),
                            settings=settings,
                        )
                        atomic_write(audio_path, audio)
                        metadata = audio_metadata(audio_path)
                        elapsed = time.perf_counter() - started
                        receipt = {
                            "schema_version": 1,
                            "sample_fingerprint": fingerprint,
                            "audio_sha256": sha256_file(audio_path),
                            "audio": metadata,
                            "generation_seconds": elapsed,
                            "real_time_factor": elapsed / metadata["duration_seconds"],
                            "provider": config["provider"],
                            "marketed_model": config["marketed_model"],
                            "api_model_header": config["api_model_header"],
                            "remote_reference_id": model["model_id"],
                            "reference_tier": tier["key"],
                            "reference_fingerprint": model["reference_fingerprint"],
                            "style": style["key"],
                            "prompt_mode": mode["key"],
                            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                            "repeat": repeat,
                            "settings": settings,
                        }
                        write_json(receipt_path, receipt)
                        time.sleep(float(settings.get("request_pause_seconds") or 0.0))
                    metadata = audio_metadata(audio_path)
                    generated.append(
                        GeneratedSample(
                            fingerprint=fingerprint,
                            audio_path=audio_path,
                            audio_sha256=sha256_file(audio_path),
                            duration_seconds=metadata["duration_seconds"],
                            answer={
                                "kind": "fish_cloud",
                                "provider": config["provider"],
                                "marketed_model": config["marketed_model"],
                                "api_model_header": config["api_model_header"],
                                "remote_reference_id": model["model_id"],
                                "reference_tier": tier["key"],
                                "reference_duration_seconds": tier["duration_seconds"],
                                "prompt_mode": mode["key"],
                                "style": style["key"],
                                "repeat": repeat,
                                "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                                "receipt": str(receipt_path.relative_to(output_root)),
                            },
                        )
                    )
    return generated


def load_answer_rows(round1_root: Path) -> list[dict[str, Any]]:
    root = round1_root / "review-round1-complete-final-answer-keys"
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "manifest.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows.extend(dict(row) for row in payload if isinstance(row, Mapping))
    return rows


def baseline_samples(
    *,
    round1_root: Path,
    config: Mapping[str, Any],
) -> list[GeneratedSample]:
    answers = load_answer_rows(round1_root)
    public_root = round1_root / "review-round1-complete-final"
    samples: list[GeneratedSample] = []
    for style in config["styles"]:
        for candidate in config["baseline_candidates"]:
            matches = [
                row
                for row in answers
                if row.get("model_key") == candidate["model_key"]
                and row.get("identity_key") == candidate["identity_key"]
                and row.get("style") == style["key"]
                and row.get("status") == "ready"
                and row.get("review_eligible") is True
            ]
            if len(matches) != 1:
                raise FishBlindRunError(
                    "baseline_candidate_ambiguous",
                    f"{candidate['model_key']} {candidate['identity_key']} {style['key']}: {len(matches)}",
                )
            row = matches[0]
            audio_path = (public_root / str(row["public_audio"])).resolve()
            try:
                audio_path.relative_to(public_root.resolve())
            except ValueError as exc:
                raise FishBlindRunError("baseline_audio_unsafe", str(audio_path)) from exc
            if row.get("public_audio_sha256") != sha256_file(audio_path):
                raise FishBlindRunError("baseline_audio_hash_changed", str(audio_path))
            fingerprint = sample_fingerprint(
                {
                    "round_id": config["round_id"],
                    "kind": "existing_baseline",
                    "source_round_id": "alexandria_multimodel_expressive_clone_round1_v1",
                    "source_sample_id": row["source_sample_id"],
                    "public_audio_sha256": row["public_audio_sha256"],
                }
            )
            metadata = audio_metadata(audio_path)
            samples.append(
                GeneratedSample(
                    fingerprint=fingerprint,
                    audio_path=audio_path,
                    audio_sha256=row["public_audio_sha256"],
                    duration_seconds=metadata["duration_seconds"],
                    answer={
                        "kind": "existing_baseline",
                        "source_round_id": "alexandria_multimodel_expressive_clone_round1_v1",
                        "source_sample_id": row["source_sample_id"],
                        "source_blind_id": row["sample_id"],
                        "model_key": row["model_key"],
                        "model_label": row["model_label"],
                        "identity_key": row["identity_key"],
                        "style": row["style"],
                        "control": row.get("control"),
                    },
                )
            )
    return samples


def _copy_assets(review_root: Path) -> None:
    for filename in ASSET_FILES:
        source = ASSET_ROOT / filename
        if not source.is_file():
            raise FishBlindRunError("review_asset_missing", str(source))
        shutil.copy2(source, review_root / filename)


def build_review_package(
    *,
    output_root: Path,
    round1_root: Path,
    config: Mapping[str, Any],
    tiers: list[dict[str, Any]],
    fish_samples: list[GeneratedSample],
    allow_partial: bool = False,
) -> dict[str, Any]:
    baselines = baseline_samples(round1_root=round1_root, config=config)
    expected = expected_counts(config)
    if not allow_partial and len(fish_samples) != expected["fish"]:
        raise FishBlindRunError(
            "fish_sample_count_incomplete",
            f"expected {expected['fish']}, found {len(fish_samples)}",
        )
    review_root = output_root / PUBLIC_ROOT_NAME
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "reference").mkdir(parents=True)
    _copy_assets(review_root)
    secret = load_or_create_secret(output_root)
    all_samples = [*baselines, *fish_samples]
    style_by_key = {str(row["key"]): row for row in config["styles"]}
    public_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    seen_blind_ids: set[str] = set()
    for sample in all_samples:
        identifier = blind_id(secret, sample.fingerprint)
        if identifier in seen_blind_ids:
            raise FishBlindRunError("blind_id_collision", identifier)
        seen_blind_ids.add(identifier)
        extension = sample.audio_path.suffix.casefold() or ".wav"
        target = review_root / "audio" / f"{identifier}{extension}"
        shutil.copy2(sample.audio_path, target)
        style_key = str(sample.answer["style"])
        public_rows.append(
            {
                "sample_id": identifier,
                "style": style_key,
                "audio": f"audio/{target.name}",
                "audio_sha256": sha256_file(target),
                "duration_seconds": sample.duration_seconds,
                "status": "ready",
            }
        )
        answer_rows.append(
            {
                "sample_id": identifier,
                "sample_fingerprint": sample.fingerprint,
                "audio_sha256": sample.audio_sha256,
                **sample.answer,
            }
        )
    for style_key in style_by_key:
        rows = [row for row in public_rows if row["style"] == style_key]
        seed = int(sha256_bytes(f"{secret}:{style_key}".encode("utf-8"))[:16], 16)
        random.Random(seed).shuffle(rows)
        for index, row in enumerate(rows, start=1):
            row["candidate_number"] = index
        public_rows = [row for row in public_rows if row["style"] != style_key] + rows
    public_rows.sort(key=lambda row: (list(style_by_key).index(row["style"]), row["candidate_number"]))
    review_entry = tiers[0]["entries"][0]
    review_reference_source = Path(str(review_entry["audio_path"]))
    review_reference = review_root / "reference" / "ryan-neutral.wav"
    shutil.copy2(review_reference_source, review_reference)
    public = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "title": "Expressive clone calibration blind review",
        "identity": {
            "label": config["identity"]["label"],
            "reference_audio": "reference/ryan-neutral.wav",
            "reference_text": review_entry["text"],
        },
        "styles": [
            {
                "key": row["key"],
                "label": row["label"],
                "group": row["group"],
                "target_text": row["target_text"],
                "requested_delivery": row["alexandria_instruction"],
            }
            for row in config["styles"]
        ],
        "samples": public_rows,
    }
    data_text = "window.FISH_S21_BLIND_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n"
    atomic_write(review_root / "data.js", data_text.encode("utf-8"))
    public_manifest = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "style_count": len(config["styles"]),
        "sample_count": len(public_rows),
        "samples_per_style": {
            key: sum(row["style"] == key for row in public_rows) for key in style_by_key
        },
        "reference_audio_sha256": sha256_file(review_reference),
        "data_sha256": sha256_file(review_root / "data.js"),
        "answer_key_separate": True,
    }
    write_json(review_root / "manifest.json", public_manifest)
    answer = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "config_sha256": sha256_value(config),
        "rows": sorted(answer_rows, key=lambda row: row["sample_id"]),
    }
    write_json(output_root / PRIVATE_ANSWER_FILE, answer)
    private_manifest = {
        **public_manifest,
        "baseline_sample_count": len(baselines),
        "fish_sample_count": len(fish_samples),
        "expected_counts": expected,
        "partial": len(fish_samples) != expected["fish"],
        "review_root": str(review_root),
        "answer_key": str(output_root / PRIVATE_ANSWER_FILE),
        "remote_credentials_persisted": False,
        "human_or_licensed_voice_uploaded": (
            config.get("identity", {}).get("source_kind") == "permitted_human_recording"
        ),
        "synthetic_reference_only": (
            config.get("identity", {}).get("source_kind") == "synthetic_qwen_custom_voice"
        ),
        "permission_confirmed_by_user": bool(
            config.get("identity", {}).get("permission_confirmed_by_user")
        ),
    }
    write_json(output_root / "manifest.json", private_manifest)
    return private_manifest


def delete_remote_models(client: FishClient, output_root: Path, round_id: str) -> dict[str, Any]:
    state = load_state(output_root, round_id)
    deleted: list[str] = []
    for model in state.get("models", {}).values():
        model_id = str(model.get("model_id") or "")
        if model_id:
            client.delete_voice_model(model_id)
            deleted.append(model_id)
    state["models"] = {}
    write_json(output_root / PRIVATE_STATE_FILE, state)
    return {"deleted_model_count": len(deleted)}


def find_existing_fish_samples(
    *,
    output_root: Path,
    config: Mapping[str, Any],
) -> list[GeneratedSample]:
    samples: list[GeneratedSample] = []
    for receipt_path in sorted((output_root / "outputs/fish_s21_pro").glob("**/*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        audio_path = receipt_path.with_suffix(".wav")
        if not audio_path.is_file() or receipt.get("audio_sha256") != sha256_file(audio_path):
            continue
        metadata = audio_metadata(audio_path)
        samples.append(
            GeneratedSample(
                fingerprint=receipt["sample_fingerprint"],
                audio_path=audio_path,
                audio_sha256=receipt["audio_sha256"],
                duration_seconds=metadata["duration_seconds"],
                answer={
                    "kind": "fish_cloud",
                    "provider": receipt["provider"],
                    "marketed_model": receipt["marketed_model"],
                    "api_model_header": receipt["api_model_header"],
                    "remote_reference_id": receipt["remote_reference_id"],
                    "reference_tier": receipt["reference_tier"],
                    "prompt_mode": receipt["prompt_mode"],
                    "style": receipt["style"],
                    "repeat": receipt["repeat"],
                    "prompt_sha256": receipt["prompt_sha256"],
                    "receipt": str(receipt_path.relative_to(output_root)),
                },
            )
        )
    return samples


def api_key_from_environment() -> str:
    return os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY") or ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--round1-root", default=str(DEFAULT_ROUND1_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--package-only", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--delete-remote-voices", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    output_root = Path(args.output_root).expanduser().resolve()
    round1_root = Path(args.round1_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    tiers = reference_tier_payloads(round1_root, config)
    key = api_key_from_environment()
    client: FishClient | None = None
    if args.delete_remote_voices or not args.package_only:
        client = FishClient(
            api_key=key,
            model_header=str(config["api_model_header"]),
            base_url=args.api_base,
            max_attempts=int(config["generation"]["max_attempts"]),
        )
    if args.delete_remote_voices:
        print(json.dumps(delete_remote_models(client, output_root, config["round_id"]), indent=2))
        return 0
    if args.package_only:
        fish_samples = find_existing_fish_samples(output_root=output_root, config=config)
    else:
        models = ensure_models(
            client,
            output_root=output_root,
            config=config,
            tiers=tiers,
        )
        fish_samples = generate_fish_samples(
            client,
            output_root=output_root,
            config=config,
            tiers=tiers,
            models=models,
            max_samples=args.max_samples,
        )
    manifest = build_review_package(
        output_root=output_root,
        round1_root=round1_root,
        config=config,
        tiers=tiers,
        fish_samples=fish_samples,
        allow_partial=args.allow_partial or args.max_samples is not None,
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
