from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias


FIXTURE_AUDIO_NAME: Final = "selected-chunk.wav"
HOSTILE_FRAGMENT: Final = "<script>alert('inert')</script>&\"'\u202e \u05e9\u05dc\u05d5\u05dd \u6f22\u5b57 e\u0301 \U0001f642"
JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
FIXTURE_COUNTS: Final = {
    "chapters": 48,
    "script_rows": 120,
    "visible_produce_rows": 150,
    "aggregate_chunks": 5328,
    "characters": 128,
    "selected_chunk_takes": 64,
    "validation_issues": 257,
}


@dataclass(frozen=True, slots=True)
class HostileFixtureManifest:
    root: Path
    deterministic_sha256: str
    aggregate_chunk_count: int
    catalog_root: Path
    config_root: Path
    fixture_audio: Path


@dataclass(frozen=True, slots=True)
class FixtureRootError(Exception):
    root: Path

    def __str__(self) -> str:
        return f"fixture root must be an empty directory: {self.root}"


def _exact_field(name: str) -> str:
    prefix = f"{name} {HOSTILE_FRAGMENT} "
    return prefix + ("x" * (512 - len(prefix)))


def _fixture_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise FixtureRootError(root) from error
    return candidate


def _write_json(path: Path, value: JsonValue) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _content_sha(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_hostile_fixture(root: Path) -> HostileFixtureManifest:
    resolved_root = root.resolve()
    if not resolved_root.is_dir() or any(resolved_root.iterdir()):
        raise FixtureRootError(resolved_root)
    catalog_root = _fixture_path(resolved_root, "catalog")
    config_root = _fixture_path(resolved_root, "config")
    audio_path = _fixture_path(resolved_root, f"fixture-audio/{FIXTURE_AUDIO_NAME}")
    title = _exact_field("title")
    author = _exact_field("author")
    character = _exact_field("character")
    voice = _exact_field("voice")
    chapter = _exact_field("chapter")
    label = _exact_field("label")
    error = _exact_field("error")
    unbroken_segment = "u" * 256
    path_prefix = f"fixture/{unbroken_segment}/"
    long_path = path_prefix + ("p" * (1024 - len(path_prefix)))
    _write_json(catalog_root / "project.json", {
        "title": title,
        "author": author,
        "source_path": long_path,
        "cover": None,
        "portrait": None,
        "optional_metadata": None,
        "swedish_labels": ["Återställningsinställningar", "Förhandsgranska kapitel", "Spara och fortsätt"],
    })
    _write_json(catalog_root / "chapters.json", [
        {"id": index + 1, "title": chapter} for index in range(FIXTURE_COUNTS["chapters"])
    ])
    _write_json(catalog_root / "script_rows.json", [
        {"id": index + 1, "text": f"{HOSTILE_FRAGMENT} script row {index + 1}"}
        for index in range(FIXTURE_COUNTS["script_rows"])
    ])
    _write_json(catalog_root / "produce_rows.json", [
        {"id": index + 1, "label": label} for index in range(FIXTURE_COUNTS["visible_produce_rows"])
    ])
    _write_json(catalog_root / "chunks.json", [
        {"id": index + 1, "chapter": (index // 111) + 1, "text": HOSTILE_FRAGMENT}
        for index in range(FIXTURE_COUNTS["aggregate_chunks"])
    ])
    _write_json(catalog_root / "characters.json", [
        {"id": f"character-{index + 1}", "name": character, "voice": voice}
        for index in range(FIXTURE_COUNTS["characters"])
    ])
    _write_json(catalog_root / "selected_chunk_takes.json", [
        {"id": index + 1, "chunk_id": 1, "audio": f"fixture-audio/{FIXTURE_AUDIO_NAME}"}
        for index in range(FIXTURE_COUNTS["selected_chunk_takes"])
    ])
    _write_json(catalog_root / "validation_issues.json", [
        {"id": index + 1, "message": error} for index in range(FIXTURE_COUNTS["validation_issues"])
    ])
    _write_json(config_root / "fixture-config.json", {"catalog_root": "catalog", "fixture_audio_only": True})
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt fixture-audio-only")
    deterministic_sha256 = _content_sha(resolved_root)
    manifest_path = _fixture_path(resolved_root, "hostile-fixture-manifest.json")
    _write_json(manifest_path, {
        "deterministic_sha256": deterministic_sha256,
        "counts": FIXTURE_COUNTS,
        "exact_codepoint_length": 512,
        "long_path_codepoint_length": len(long_path),
        "unbroken_segment_codepoint_length": len(unbroken_segment),
        "catalog_root": "catalog",
        "config_root": "config",
        "fixture_audio": f"fixture-audio/{FIXTURE_AUDIO_NAME}",
    })
    return HostileFixtureManifest(
        root=resolved_root,
        deterministic_sha256=deterministic_sha256,
        aggregate_chunk_count=FIXTURE_COUNTS["aggregate_chunks"],
        catalog_root=catalog_root,
        config_root=config_root,
        fixture_audio=audio_path,
    )
