#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
VOICE_KEY = "NARRATOR"
REQUIRED_APPROVAL_BASIS = "operator_approved_after_listening"


class ReviewedDatasetError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewedDatasetError(f"{label} must be non-empty text.")
    return value.strip()


def _resolve_source(root: Path, value: Any, label: str) -> Path:
    relative = Path(_required_text(value, label))
    if relative.is_absolute() or ".." in relative.parts:
        raise ReviewedDatasetError(f"{label} must be project-relative.")
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ReviewedDatasetError(f"{label} is missing or escaped the project.")
    return source


def _split_map(route_keys: list[str]) -> dict[str, str]:
    if len(route_keys) < 4:
        raise ReviewedDatasetError(
            "At least four approved routes are required for train, validation, and test splits."
        )
    test_keys = {route_keys[-1]}
    validation_count = 2 if len(route_keys) >= 10 else 1
    validation_keys = set(route_keys[-1 - validation_count : -1])
    return {
        key: (
            "test"
            if key in test_keys
            else "validation"
            if key in validation_keys
            else "train"
        )
        for key in route_keys
    }


def prepare_dataset(
    *,
    source_root: str | Path,
    output_dir: str | Path,
    voice_key: str = VOICE_KEY,
) -> dict[str, Any]:
    root = Path(source_root).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    config_path = root / "voice_config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewedDatasetError(f"voice_config.json could not be read: {exc}") from exc
    voice = config.get(voice_key)
    if not isinstance(voice, dict):
        raise ReviewedDatasetError(f"Voice {voice_key!r} is unavailable.")
    routing = voice.get("experimental_prompt_routing")
    if not isinstance(routing, dict) or routing.get("production_promotion_allowed") is not True:
        raise ReviewedDatasetError(
            "The Voice has no production-approved prompt routing evidence."
        )
    raw_routes = routing.get("routes")
    if not isinstance(raw_routes, dict):
        raise ReviewedDatasetError("Prompt routing routes are unavailable.")

    approved: list[tuple[str, dict[str, Any]]] = []
    for route_key, route in sorted(raw_routes.items()):
        if not isinstance(route, dict):
            continue
        if (
            route.get("status") != "production_opt_in"
            or route.get("production_promotion_allowed") is not True
            or route.get("approval_basis") != REQUIRED_APPROVAL_BASIS
        ):
            continue
        keywords = route.get("instruction_keywords")
        if not isinstance(keywords, list) or not keywords:
            raise ReviewedDatasetError(
                f"Approved route {route_key!r} has no reviewed instruction keywords."
            )
        approved.append((str(route_key), route))
    route_keys = [key for key, _ in approved]
    split_by_route = _split_map(route_keys)

    reference_source = _resolve_source(
        root,
        voice.get("ref_audio"),
        f"{voice_key}.ref_audio",
    )
    reference_text = _required_text(
        voice.get("ref_text"),
        f"{voice_key}.ref_text",
    )

    if output.exists():
        shutil.rmtree(output)
    audio_dir = output / "audio"
    audio_dir.mkdir(parents=True)
    reference_name = "reference" + reference_source.suffix.casefold()
    shutil.copy2(reference_source, output / reference_name)

    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for index, (route_key, route) in enumerate(approved):
        source = _resolve_source(
            root,
            route.get("ref_audio"),
            f"Route {route_key}.ref_audio",
        )
        expected_sha = _required_text(
            route.get("ref_audio_sha256"),
            f"Route {route_key}.ref_audio_sha256",
        )
        actual_sha = sha256_file(source)
        if actual_sha != expected_sha:
            raise ReviewedDatasetError(
                f"Route {route_key!r} audio does not match its approved fingerprint."
            )
        filename = f"{index:03d}_{route_key}{source.suffix.casefold()}"
        shutil.copy2(source, audio_dir / filename)
        transcript = _required_text(
            route.get("ref_text"),
            f"Route {route_key}.ref_text",
        )
        keywords = [
            _required_text(value, f"Route {route_key}.instruction_keywords")
            for value in route["instruction_keywords"]
        ]
        row = {
            "audio_filepath": f"audio/{filename}",
            "text": transcript,
            "transcript": transcript,
            "instruction": keywords[0],
            "ref_audio": reference_name,
            "review_status": "approved",
            "split": split_by_route[route_key],
            "route_key": route_key,
            "reviewed_instruction_keywords": keywords,
            "approval_basis": route["approval_basis"],
            "operator_approved_at_utc": route.get("operator_approved_at_utc"),
            "evidence_round_id": routing.get("evidence_round_id"),
            "source_audio_sha256": actual_sha,
        }
        rows.append(row)
        sources.append(
            {
                "route_key": route_key,
                "project_relative_path": source.relative_to(root).as_posix(),
                "audio_sha256": actual_sha,
                "transcript_sha256": hashlib.sha256(
                    transcript.encode("utf-8")
                ).hexdigest(),
                "instruction": keywords[0],
                "split": split_by_route[route_key],
            }
        )

    metadata_path = output / "metadata.jsonl"
    metadata_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    manifest_core = {
        "schema_version": SCHEMA_VERSION,
        "task": "B18-T03",
        "voice_key": voice_key,
        "canonical_name": voice_key.title(),
        "source_voice_config_sha256": sha256_file(config_path),
        "reference_audio_sha256": sha256_file(output / reference_name),
        "reference_text_sha256": hashlib.sha256(
            reference_text.encode("utf-8")
        ).hexdigest(),
        "evidence_round_id": routing.get("evidence_round_id"),
        "approval_basis": REQUIRED_APPROVAL_BASIS,
        "user_training_authorized_at_local": "2026-08-03T12:29:00-05:00",
        "instruction_contract": "first operator-approved route keyword",
        "sample_count": len(rows),
        "split_counts": {
            split: sum(row["split"] == split for row in rows)
            for split in ("train", "validation", "test")
        },
        "sources": sources,
        "production_assignment_allowed": False,
    }
    manifest = {
        **manifest_core,
        "dataset_fingerprint": fingerprint(manifest_core),
        "metadata_sha256": sha256_file(metadata_path),
    }
    (output / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Prepare the reviewed Narrator dataset for B18-T03."
    )
    result.add_argument("--source-root", default=".")
    result.add_argument("--output-dir", required=True)
    result.add_argument("--voice-key", default=VOICE_KEY)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    value = prepare_dataset(
        source_root=args.source_root,
        output_dir=args.output_dir,
        voice_key=args.voice_key,
    )
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
