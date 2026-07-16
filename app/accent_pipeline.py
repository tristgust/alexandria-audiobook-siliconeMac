from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


ACCENT_DISABLED_PATTERN = re.compile(
    r"\[\s*accent\s*:\s*(?:none|off|disabled)\s*\]",
    flags=re.IGNORECASE,
)

ACCENT_PIPELINES: tuple[dict[str, Any], ...] = (
    {
        "language": "French",
        "label": "French",
        "patterns": [
            (
                r"\[\s*accent\s*:\s*"
                r"(?:french|occitan|southern\s+french|"
                r"français|francais)\s*\]"
            ),
            (
                r"\b(?:light|soft|strong|clear|audible|"
                r"restrained|subtle|slight|heavy|moderate)?\s*"
                r"(?:southern\s+)?french\s+accent\b"
            ),
            r"\boccitan(?:-influenced)?\s+accent\b",
            r"\bfrom\s+(?:occitanie|southern\s+france)\b",
            r"\bnative\s+french(?:[- ]speaking)?\b",
        ],
        "seed_text": (
            "Henri regarda les vieilles pierres du château, puis "
            "répondit calmement que nous partirions avant l’aube, "
            "quoi qu’il arrive."
        ),
        "native_instruction": (
            "Pour cet enregistrement de référence, crée exactement "
            "ce personnage comme un locuteur natif de français "
            "originaire de France. Conserve le timbre, l'âge, le "
            "genre, la hauteur, le rythme et l'émotion décrits. "
            "Il parle ici en français naturel avec l'identité "
            "régionale demandée."
        ),
    },
    {
        "language": "Spanish",
        "label": "Spanish",
        "patterns": [
            (
                r"\[\s*accent\s*:\s*"
                r"(?:spanish|castilian|mexican|"
                r"latin\s+american)\s*\]"
            ),
            (
                r"\b(?:spanish|castilian|mexican|"
                r"latin\s+american)\s+accent\b"
            ),
            r"\bnative\s+spanish(?:[- ]speaking)?\b",
        ],
        "seed_text": (
            "Enrique cruzó lentamente el valle rojo antes de que "
            "los jinetes llegaran al viejo puente en ruinas."
        ),
        "native_instruction": (
            "Para esta grabación de referencia, crea exactamente "
            "este personaje como hablante nativo de español. "
            "Conserva el timbre, la edad, el género, el tono, "
            "el ritmo y la emoción descritos."
        ),
    },
    {
        "language": "German",
        "label": "German",
        "patterns": [
            (
                r"\[\s*accent\s*:\s*"
                r"(?:german|austrian|swiss\s+german)\s*\]"
            ),
            (
                r"\b(?:german|austrian|swiss\s+german)"
                r"\s+accent\b"
            ),
            r"\bnative\s+german(?:[- ]speaking)?\b",
        ],
        "seed_text": (
            "Heinrich ging langsam durch das rote Tal, bevor die "
            "Reiter die alte verfallene Brücke erreichten."
        ),
        "native_instruction": (
            "Erzeuge für diese Referenzaufnahme genau diese Figur "
            "als deutschen Muttersprachler. Bewahre Stimmfarbe, "
            "Alter, Geschlecht, Tonhöhe, Tempo und die beschriebene "
            "Emotion."
        ),
    },
    {
        "language": "Italian",
        "label": "Italian",
        "patterns": [
            r"\[\s*accent\s*:\s*italian\s*\]",
            r"\bitalian\s+accent\b",
            r"\bnative\s+italian(?:[- ]speaking)?\b",
        ],
        "seed_text": (
            "Enrico attraversò lentamente la valle rossa prima che "
            "i cavalieri raggiungessero il vecchio ponte in rovina."
        ),
        "native_instruction": (
            "Per questa registrazione di riferimento, crea "
            "esattamente questo personaggio come madrelingua "
            "italiano. Mantieni timbro, età, genere, altezza, ritmo "
            "ed emozione descritti."
        ),
    },
    {
        "language": "Portuguese",
        "label": "Portuguese",
        "patterns": [
            (
                r"\[\s*accent\s*:\s*"
                r"(?:portuguese|brazilian)\s*\]"
            ),
            r"\b(?:portuguese|brazilian)\s+accent\b",
            r"\bnative\s+portuguese(?:[- ]speaking)?\b",
        ],
        "seed_text": (
            "Henrique atravessou lentamente o vale vermelho antes "
            "que os cavaleiros chegassem à velha ponte em ruínas."
        ),
        "native_instruction": (
            "Para esta gravação de referência, crie exatamente "
            "esta personagem como falante nativo de português. "
            "Preserve o timbre, a idade, o gênero, a altura, o "
            "ritmo e a emoção descritos."
        ),
    },
    {
        "language": "Russian",
        "label": "Russian",
        "patterns": [
            r"\[\s*accent\s*:\s*russian\s*\]",
            r"\brussian\s+accent\b",
            r"\bnative\s+russian(?:[- ]speaking)?\b",
        ],
        "seed_text": (
            "Генрих медленно пересёк красную долину, прежде чем "
            "всадники достигли старого разрушенного моста."
        ),
        "native_instruction": (
            "Для этой эталонной записи создай именно этого "
            "персонажа как носителя русского языка. Сохрани "
            "описанные тембр, возраст, пол, высоту голоса, ритм "
            "и эмоцию."
        ),
    },
)


def _copy_pipeline(
    pipeline: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "language": str(pipeline["language"]),
        "label": str(pipeline["label"]),
        "patterns": list(pipeline["patterns"]),
        "seed_text": str(pipeline["seed_text"]),
        "native_instruction": str(
            pipeline["native_instruction"]
        ),
    }


def detect_accent_pipeline(
    description: str,
) -> dict[str, Any] | None:
    # Return native-language seed data for a requested accent.
    text = str(description or "").strip()

    if ACCENT_DISABLED_PATTERN.search(text):
        return None

    for pipeline in ACCENT_PIPELINES:
        if any(
            re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )
            for pattern in pipeline["patterns"]
        ):
            return _copy_pipeline(pipeline)

    return None


def build_native_seed_instruction(
    description: str,
    pipeline: Mapping[str, Any],
) -> str:
    # Build the hidden native-language VoiceDesign instruction.
    return (
        f"{str(description or '').strip()}\n\n"
        f"{pipeline['native_instruction']}\n"
        "For this hidden reference recording, speak the supplied "
        "text in the native language, not in English. Preserve "
        "every requested character trait."
    )


def normalize_output_language(
    language: str | None,
) -> str:
    # Resolve the language used by the preview clone stage.
    value = str(language or "English").strip()

    if not value or value.casefold() == "auto":
        return "English"

    return value


def sha256_file(
    path: str | Path,
) -> str:
    digest = hashlib.sha256()

    with Path(path).open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def accent_registry_dir(
    root: str | Path,
) -> Path:
    registry_dir = (
        Path(root)
        / "designed_voices"
        / "accent_registry"
    )
    registry_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    return registry_dir


def register_accent_preview(
    *,
    root: str | Path,
    preview_audio_path: str | Path,
    native_seed_audio: str | Path,
    native_seed_text: str,
    native_language: str,
    preview_text: str,
) -> Path:
    # Register an English preview with its hidden native seed.
    root_path = Path(root)
    native_seed_path = Path(native_seed_audio)

    try:
        if native_seed_path.is_absolute():
            native_seed_store = str(
                native_seed_path.relative_to(
                    root_path
                )
            )
        else:
            native_seed_store = str(
                native_seed_path
            )
    except (ValueError, OSError):
        native_seed_store = str(
            native_seed_path
        )

    record = {
        "marker": "accent preview registry",
        "native_seed_audio": native_seed_store,
        "native_seed_text": native_seed_text,
        "native_language": native_language,
        "preview_text": preview_text,
    }

    digest = sha256_file(
        preview_audio_path
    )
    registry_path = (
        accent_registry_dir(root_path)
        / f"{digest}.json"
    )
    registry_path.write_text(
        json.dumps(
            record,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return registry_path


def resolve_accent_clone_reference(
    *,
    root: str | Path,
    ref_audio: str | Path,
    ref_text: str,
    warning: Callable[[str], None] | None = print,
) -> tuple[str, str, dict[str, Any] | None]:
    # Recover the hidden native seed for a saved accent preview.
    ref_audio_value = str(
        ref_audio
    )

    try:
        digest = sha256_file(
            ref_audio_value
        )
    except (OSError, ValueError):
        return (
            ref_audio_value,
            ref_text,
            None,
        )

    registry_path = (
        accent_registry_dir(root)
        / f"{digest}.json"
    )

    if not registry_path.exists():
        return (
            ref_audio_value,
            ref_text,
            None,
        )

    try:
        meta = json.loads(
            registry_path.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        if warning is not None:
            warning(
                "MLX accent registry warning: "
                f"could not read {registry_path}: {exc}"
            )

        return (
            ref_audio_value,
            ref_text,
            None,
        )

    root_path = Path(root)
    native_seed_audio = str(
        meta.get(
            "native_seed_audio",
            "",
        )
    )
    seed_path = Path(
        native_seed_audio
    )

    if not seed_path.is_absolute():
        seed_path = (
            root_path
            / native_seed_audio
        )

    if not seed_path.exists():
        if warning is not None:
            warning(
                "MLX accent registry warning: "
                f"native seed missing for "
                f"{ref_audio_value}: {seed_path}"
            )

        return (
            ref_audio_value,
            ref_text,
            None,
        )

    native_seed_text = (
        meta.get(
            "native_seed_text"
        )
        or ref_text
    )

    return (
        str(seed_path),
        str(native_seed_text),
        meta,
    )


def split_clone_segments(
    text: str,
    max_words: int = 14,
) -> list[str]:
    # Split clone text so accent conditioning is refreshed.
    clean = re.sub(
        r"\s+",
        " ",
        str(text or "").strip(),
    )

    if not clean:
        return []

    rough_parts = re.split(
        (
            r"(?<=[.!?;:])\s+|"
            r"(?<=,)\s+|"
            r"(?<=—)\s+|"
            r"(?<=-)\s+"
        ),
        clean,
    )
    segments: list[str] = []

    def add_chunk(
        chunk: str,
    ) -> None:
        value = chunk.strip()

        if not value:
            return

        words = value.split()

        if len(words) <= max_words:
            segments.append(value)
            return

        current: list[str] = []

        for word in words:
            current.append(word)

            if len(current) >= max_words:
                segments.append(
                    " ".join(current)
                )
                current = []

        if current:
            if (
                segments
                and len(current) < 4
            ):
                segments[-1] = (
                    segments[-1]
                    + " "
                    + " ".join(current)
                )
            else:
                segments.append(
                    " ".join(current)
                )

    for part in rough_parts:
        add_chunk(part)

    return [
        segment
        for segment in segments
        if segment.strip()
    ]
