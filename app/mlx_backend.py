from __future__ import annotations

import os
import hashlib
import json
import re
import time
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model


class MLXBackend:
    """Persistent Qwen3-TTS backend for Apple Silicon via MLX-Audio."""

    CUSTOM_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
    CLONE_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    DESIGN_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"

    def __init__(self, language: str = "English"):
        self.language = language or "English"
        self._models = {}

    def _model(self, kind: str):
        model_ids = {
            "custom": self.CUSTOM_MODEL,
            "clone": self.CLONE_MODEL,
            "design": self.DESIGN_MODEL,
        }
        if kind not in self._models:
            model_id = model_ids[kind]
            print(f"MLX: loading {kind} model: {model_id}")
            started = time.perf_counter()
            self._models[kind] = load_model(model_id)
            print(f"MLX: {kind} model loaded in {time.perf_counter() - started:.2f}s")
        return self._models[kind]

    @staticmethod
    def _collect_audio(model, results):
        arrays = []
        for result in results:
            mx.eval(result.audio)
            arrays.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
        if not arrays:
            raise RuntimeError("MLX-Audio returned no audio.")
        audio = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
        sample_rate = int(getattr(model, "sample_rate", 24000))
        return audio, sample_rate

    @staticmethod
    def _save(audio, sample_rate: int, output_path: str):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(output_path, audio, sample_rate)

    @staticmethod
    def _sha256_file(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _accent_registry_dir(self):
        root = Path(__file__).resolve().parent.parent
        registry_dir = root / "designed_voices" / "accent_registry"
        registry_dir.mkdir(parents=True, exist_ok=True)
        return registry_dir

    def _register_accent_preview(
        self,
        preview_audio_path: str,
        native_seed_audio: str,
        native_seed_text: str,
        native_language: str,
        preview_text: str,
    ):
        """Register an accent preview so future clones can recover the native seed."""
        root = Path(__file__).resolve().parent.parent
        native_seed_path = Path(native_seed_audio)
        try:
            if native_seed_path.is_absolute():
                native_seed_store = str(native_seed_path.relative_to(root))
            else:
                native_seed_store = str(native_seed_path)
        except Exception:
            native_seed_store = str(native_seed_audio)

        record = {
            "marker": "accent preview registry",
            "native_seed_audio": native_seed_store,
            "native_seed_text": native_seed_text,
            "native_language": native_language,
            "preview_text": preview_text,
        }
        digest = self._sha256_file(preview_audio_path)
        registry_path = self._accent_registry_dir() / f"{digest}.json"
        registry_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return registry_path

    def _resolve_accent_clone_reference(self, ref_audio: str, ref_text: str):
        """If ref_audio is a saved accent preview, recover its hidden native seed."""
        try:
            digest = self._sha256_file(ref_audio)
        except Exception:
            return ref_audio, ref_text, None

        registry_path = self._accent_registry_dir() / f"{digest}.json"
        if not registry_path.exists():
            return ref_audio, ref_text, None

        try:
            meta = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"MLX accent registry warning: could not read {registry_path}: {exc}")
            return ref_audio, ref_text, None

        root = Path(__file__).resolve().parent.parent
        native_seed_audio = meta.get("native_seed_audio", "")
        seed_path = Path(native_seed_audio)
        if not seed_path.is_absolute():
            seed_path = root / native_seed_audio

        if not seed_path.exists():
            print(
                f"MLX accent registry warning: native seed missing for {ref_audio}: {seed_path}"
            )
            return ref_audio, ref_text, None

        native_seed_text = meta.get("native_seed_text") or ref_text
        return str(seed_path), native_seed_text, meta

    @staticmethod
    def _split_clone_segments(text: str, max_words: int = 14):
        """Split longer clone text so accent conditioning is refreshed repeatedly."""
        clean = re.sub(r"\s+", " ", (text or "").strip())
        if not clean:
            return []

        # First prefer natural punctuation boundaries.
        rough_parts = re.split(r"(?<=[.!?;:])\s+|(?<=,)\s+|(?<=—)\s+|(?<=-)\s+", clean)
        segments = []

        def add_chunk(chunk: str):
            chunk = chunk.strip()
            if not chunk:
                return
            words = chunk.split()
            if len(words) <= max_words:
                segments.append(chunk)
                return

            current = []
            for word in words:
                current.append(word)
                if len(current) >= max_words:
                    segments.append(" ".join(current))
                    current = []
            if current:
                if segments and len(current) < 4:
                    segments[-1] = segments[-1] + " " + " ".join(current)
                else:
                    segments.append(" ".join(current))

        for part in rough_parts:
            add_chunk(part)

        return [segment for segment in segments if segment.strip()]

    def generate_custom(self, text: str, instruct: str, voice: str, output_path: str) -> bool:
        model = self._model("custom")
        kwargs = {"voice": voice, "lang_code": self.language}
        if instruct:
            kwargs["instruct"] = instruct
        started = time.perf_counter()
        results = list(model.generate(text, **kwargs))
        audio, sample_rate = self._collect_audio(model, results)
        self._save(audio, sample_rate, output_path)
        print(
            f"MLX custom: {time.perf_counter() - started:.2f}s "
            f"for {len(audio) / sample_rate:.2f}s audio"
        )
        return True


    def generate_clone(
        self,
        text: str,
        ref_audio: str,
        ref_text: str,
        output_path: str,
    ) -> bool:
        effective_ref_audio, effective_ref_text, accent_meta = (
            self._resolve_accent_clone_reference(ref_audio, ref_text)
        )

        model = self._model("clone")
        started = time.perf_counter()
        output_language = self.language

        segments = [text]
        accent_reset = accent_meta is not None
        if accent_reset:
            segments = self._split_clone_segments(text, max_words=14) or [text]

        collected = []
        sample_rate = int(getattr(model, "sample_rate", 24000))
        pause = np.zeros(int(sample_rate * 0.10), dtype=np.float32)

        for index, segment in enumerate(segments):
            results = list(
                model.generate(
                    segment,
                    ref_audio=effective_ref_audio,
                    ref_text=effective_ref_text,
                    lang_code=output_language,
                )
            )
            audio, sample_rate = self._collect_audio(model, results)
            collected.append(audio)
            if accent_reset and index < len(segments) - 1:
                collected.append(pause.copy())

        final_audio = collected[0] if len(collected) == 1 else np.concatenate(collected)
        self._save(final_audio, sample_rate, output_path)

        mode = " accent-reset clone" if accent_reset else " clone"
        print(
            f"MLX{mode}: {time.perf_counter() - started:.2f}s "
            f"for {len(final_audio) / sample_rate:.2f}s audio "
            f"({len(segments)} segment(s))"
        )
        return True

    @staticmethod
    def _accent_pipeline_for(description: str):
        """Return native-language seed data for a requested non-English accent."""
        text = (description or "").strip()
        lower = text.lower()

        # Explicit escape hatch for ordinary VoiceDesign.
        if re.search(r"\[\s*accent\s*:\s*(?:none|off|disabled)\s*\]", lower):
            return None

        pipelines = [
            {
                "language": "French",
                "label": "French",
                "patterns": [
                    r"\[\s*accent\s*:\s*(?:french|occitan|southern\s+french|français|francais)\s*\]",
                    r"\b(?:light|soft|strong|clear|audible|restrained|subtle|slight|heavy|moderate)?\s*(?:southern\s+)?french\s+accent\b",
                    r"\boccitan(?:-influenced)?\s+accent\b",
                    r"\bfrom\s+(?:occitanie|southern\s+france)\b",
                    r"\bnative\s+french(?:[- ]speaking)?\b",
                ],
                "seed_text": (
                    "Henri regarda les vieilles pierres du château, puis répondit calmement "
                    "que nous partirions avant l’aube, quoi qu’il arrive."
                ),
                "native_instruction": (
                    "Pour cet enregistrement de référence, crée exactement ce personnage "
                    "comme un locuteur natif de français originaire de France. Conserve le "
                    "timbre, l'âge, le genre, la hauteur, le rythme et l'émotion décrits. "
                    "Il parle ici en français naturel avec l'identité régionale demandée."
                ),
            },
            {
                "language": "Spanish",
                "label": "Spanish",
                "patterns": [
                    r"\[\s*accent\s*:\s*(?:spanish|castilian|mexican|latin\s+american)\s*\]",
                    r"\b(?:spanish|castilian|mexican|latin\s+american)\s+accent\b",
                    r"\bnative\s+spanish(?:[- ]speaking)?\b",
                ],
                "seed_text": (
                    "Enrique cruzó lentamente el valle rojo antes de que los jinetes "
                    "llegaran al viejo puente en ruinas."
                ),
                "native_instruction": (
                    "Para esta grabación de referencia, crea exactamente este personaje "
                    "como hablante nativo de español. Conserva el timbre, la edad, el género, "
                    "el tono, el ritmo y la emoción descritos."
                ),
            },
            {
                "language": "German",
                "label": "German",
                "patterns": [
                    r"\[\s*accent\s*:\s*(?:german|austrian|swiss\s+german)\s*\]",
                    r"\b(?:german|austrian|swiss\s+german)\s+accent\b",
                    r"\bnative\s+german(?:[- ]speaking)?\b",
                ],
                "seed_text": (
                    "Heinrich ging langsam durch das rote Tal, bevor die Reiter die alte "
                    "verfallene Brücke erreichten."
                ),
                "native_instruction": (
                    "Erzeuge für diese Referenzaufnahme genau diese Figur als deutschen "
                    "Muttersprachler. Bewahre Stimmfarbe, Alter, Geschlecht, Tonhöhe, Tempo "
                    "und die beschriebene Emotion."
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
                    "Enrico attraversò lentamente la valle rossa prima che i cavalieri "
                    "raggiungessero il vecchio ponte in rovina."
                ),
                "native_instruction": (
                    "Per questa registrazione di riferimento, crea esattamente questo "
                    "personaggio come madrelingua italiano. Mantieni timbro, età, genere, "
                    "altezza, ritmo ed emozione descritti."
                ),
            },
            {
                "language": "Portuguese",
                "label": "Portuguese",
                "patterns": [
                    r"\[\s*accent\s*:\s*(?:portuguese|brazilian)\s*\]",
                    r"\b(?:portuguese|brazilian)\s+accent\b",
                    r"\bnative\s+portuguese(?:[- ]speaking)?\b",
                ],
                "seed_text": (
                    "Henrique atravessou lentamente o vale vermelho antes que os cavaleiros "
                    "chegassem à velha ponte em ruínas."
                ),
                "native_instruction": (
                    "Para esta gravação de referência, crie exatamente esta personagem como "
                    "falante nativo de português. Preserve o timbre, a idade, o gênero, a "
                    "altura, o ritmo e a emoção descritos."
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
                    "Генрих медленно пересёк красную долину, прежде чем всадники достигли "
                    "старого разрушенного моста."
                ),
                "native_instruction": (
                    "Для этой эталонной записи создай именно этого персонажа как носителя "
                    "русского языка. Сохрани описанные тембр, возраст, пол, высоту голоса, "
                    "ритм и эмоцию."
                ),
            },
        ]

        for pipeline in pipelines:
            if any(re.search(pattern, lower, flags=re.IGNORECASE) for pattern in pipeline["patterns"]):
                return pipeline
        return None

    def generate_design_preview(
        self,
        description: str,
        sample_text: str,
        seed: int = -1,
    ):
        pipeline = self._accent_pipeline_for(description)
        root = Path(__file__).resolve().parent.parent
        preview_dir = root / "designed_voices" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        if pipeline is None:
            model = self._model("design")
            if seed is not None and int(seed) >= 0:
                mx.random.seed(int(seed))
            started = time.perf_counter()
            results = list(
                model.generate(
                    sample_text,
                    instruct=description,
                    lang_code=self.language,
                )
            )
            audio, sample_rate = self._collect_audio(model, results)
            output_path = preview_dir / f"mlx_preview_{time.time_ns()}.wav"
            self._save(audio, sample_rate, str(output_path))
            print(
                f"MLX VoiceDesign: {time.perf_counter() - started:.2f}s "
                f"for {len(audio) / sample_rate:.2f}s audio"
            )
            return str(output_path), sample_rate

        # Accent-aware path:
        # 1. Design the requested character while speaking the accent's native language.
        # 2. Clone that native reference into the user's English preview sentence.
        # The saved English preview remains compatible with Alexandria's existing
        # designed-voice save workflow and becomes the character's clone reference.
        if seed is not None and int(seed) >= 0:
            mx.random.seed(int(seed))

        label = pipeline["label"]
        native_language = pipeline["language"]
        native_text = pipeline["seed_text"]
        native_instruction = (
            f"{description.strip()}\n\n"
            f"{pipeline['native_instruction']}\n"
            "For this hidden reference recording, speak the supplied text in the native "
            "language, not in English. Preserve every requested character trait."
        )

        print(
            f"MLX VoiceDesign accent pipeline: {label} native reference -> "
            f"{self.language or 'English'} preview clone"
        )
        started = time.perf_counter()

        design_model = self._model("design")
        native_results = list(
            design_model.generate(
                native_text,
                instruct=native_instruction,
                lang_code=native_language,
            )
        )
        native_audio, native_sample_rate = self._collect_audio(design_model, native_results)

        seed_dir = root / "designed_voices" / "accent_seeds"
        seed_dir.mkdir(parents=True, exist_ok=True)
        seed_path = seed_dir / f"{label.lower()}_seed_{time.time_ns()}.wav"
        self._save(native_audio, native_sample_rate, str(seed_path))

        clone_model = self._model("clone")
        output_language = self.language or "English"
        if str(output_language).strip().lower() == "auto":
            output_language = "English"

        clone_results = list(
            clone_model.generate(
                sample_text,
                ref_audio=str(seed_path),
                ref_text=native_text,
                lang_code=output_language,
            )
        )
        preview_audio, preview_sample_rate = self._collect_audio(clone_model, clone_results)
        output_path = preview_dir / f"mlx_accent_preview_{time.time_ns()}.wav"
        self._save(preview_audio, preview_sample_rate, str(output_path))
        self._register_accent_preview(
            preview_audio_path=str(output_path),
            native_seed_audio=str(seed_path),
            native_seed_text=native_text,
            native_language=native_language,
            preview_text=sample_text,
        )

        print(
            f"MLX VoiceDesign accent pipeline complete: "
            f"{time.perf_counter() - started:.2f}s total, "
            f"{len(preview_audio) / preview_sample_rate:.2f}s preview audio"
        )
        return str(output_path), preview_sample_rate

    def generate_custom_batch(self, chunks, voice_config, output_dir: str):
        completed = []
        failed = []
        for chunk in chunks:
            idx = chunk["index"]
            speaker = chunk.get("speaker", "")
            config = voice_config.get(speaker, {})
            voice = config.get("voice", "Ryan")
            instruct = chunk.get("instruct", "") or config.get("default_style", "") or "neutral"
            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
            try:
                self.generate_custom(chunk.get("text", ""), instruct, voice, output_path)
                completed.append(idx)
            except Exception as exc:
                failed.append((idx, str(exc)))
        return {"completed": completed, "failed": failed}

    def generate_clone_batch(self, chunks, voice_config, output_dir: str):
        completed = []
        failed = []
        root = Path(__file__).resolve().parent.parent
        for chunk in chunks:
            idx = chunk["index"]
            speaker = chunk.get("speaker", "")
            config = voice_config.get(speaker, {})
            ref_audio = config.get("ref_audio")
            ref_text = config.get("ref_text")
            output_path = os.path.join(output_dir, f"temp_batch_{idx}.wav")
            try:
                if not ref_audio or not ref_text:
                    raise ValueError(
                        f"Clone voice for '{speaker}' requires ref_audio and ref_text."
                    )
                ref_path = Path(ref_audio)
                if not ref_path.is_absolute():
                    ref_path = root / ref_path
                if not ref_path.exists():
                    raise FileNotFoundError(
                        f"Reference audio not found for '{speaker}': {ref_path}"
                    )
                self.generate_clone(
                    chunk.get("text", ""),
                    str(ref_path),
                    ref_text,
                    output_path,
                )
                completed.append(idx)
            except Exception as exc:
                failed.append((idx, str(exc)))
        return {"completed": completed, "failed": failed}
