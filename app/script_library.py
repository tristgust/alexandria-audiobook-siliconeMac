from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


VOICE_CONFIG_SUFFIX = ".voice_config.json"
METADATA_SUFFIX = ".meta.json"


def current_metadata_path(
    script_path: str | Path,
) -> str:
    target = Path(script_path)

    return str(
        target.with_name(
            f"{target.stem}{METADATA_SUFFIX}"
        )
    )


def _saved_paths(
    scripts_dir: str | Path,
    name: str,
) -> tuple[Path, Path, Path]:
    directory = Path(scripts_dir)

    return (
        directory / f"{name}.json",
        directory
        / f"{name}{VOICE_CONFIG_SUFFIX}",
        directory
        / f"{name}{METADATA_SUFFIX}",
    )


def is_primary_saved_script(
    filename: str,
) -> bool:
    return (
        filename.endswith(".json")
        and not filename.endswith(
            VOICE_CONFIG_SUFFIX
        )
        and not filename.endswith(
            METADATA_SUFFIX
        )
    )


def list_saved_script_records(
    scripts_dir: str | Path,
) -> list[dict[str, Any]]:
    directory = Path(scripts_dir)
    scripts = []

    if not directory.exists():
        return scripts

    for path in directory.iterdir():
        if (
            not path.is_file()
            or not is_primary_saved_script(
                path.name
            )
        ):
            continue

        name = path.name[:-5]
        _, voice_path, metadata_path = (
            _saved_paths(
                directory,
                name,
            )
        )

        scripts.append(
            {
                "name": name,
                "created": (
                    path.stat().st_mtime
                ),
                "has_voice_config": (
                    voice_path.exists()
                ),
                "has_metadata": (
                    metadata_path.exists()
                ),
            }
        )

    scripts.sort(
        key=lambda item: item["created"],
        reverse=True,
    )

    return scripts


def save_script_bundle(
    *,
    scripts_dir: str | Path,
    name: str,
    script_path: str | Path,
    voice_config_path: str | Path,
    metadata_path: str | Path,
) -> None:
    directory = Path(scripts_dir)
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    script_destination, voice_destination, (
        metadata_destination
    ) = _saved_paths(
        directory,
        name,
    )

    shutil.copy2(
        script_path,
        script_destination,
    )

    current_voice = Path(
        voice_config_path
    )

    if current_voice.exists():
        shutil.copy2(
            current_voice,
            voice_destination,
        )

    current_metadata = Path(
        metadata_path
    )

    if current_metadata.exists():
        shutil.copy2(
            current_metadata,
            metadata_destination,
        )
    else:
        try:
            metadata_destination.unlink()
        except FileNotFoundError:
            pass


def load_script_bundle(
    *,
    scripts_dir: str | Path,
    name: str,
    script_path: str | Path,
    voice_config_path: str | Path,
    metadata_path: str | Path,
    chunks_path: str | Path,
) -> None:
    source_script, source_voice, (
        source_metadata
    ) = _saved_paths(
        scripts_dir,
        name,
    )

    shutil.copy2(
        source_script,
        script_path,
    )

    if source_voice.exists():
        shutil.copy2(
            source_voice,
            voice_config_path,
        )

    current_metadata = Path(
        metadata_path
    )

    if source_metadata.exists():
        shutil.copy2(
            source_metadata,
            current_metadata,
        )
    else:
        try:
            current_metadata.unlink()
        except FileNotFoundError:
            pass

    try:
        Path(chunks_path).unlink()
    except FileNotFoundError:
        pass


def delete_script_bundle(
    *,
    scripts_dir: str | Path,
    name: str,
) -> None:
    script_path, voice_path, metadata_path = (
        _saved_paths(
            scripts_dir,
            name,
        )
    )

    script_path.unlink()

    for companion in (
        voice_path,
        metadata_path,
    ):
        try:
            companion.unlink()
        except FileNotFoundError:
            pass
