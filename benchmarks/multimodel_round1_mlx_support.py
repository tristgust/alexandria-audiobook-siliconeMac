"""Optional-runtime and audio support for the Round 1 MLX runner."""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import resource
import subprocess
from typing import Any
import wave

from multimodel_round1_mlx_dependencies import (
    MOSS_TOKENIZER_REVISION,
    ArtifactPathError,
    GenerationInputError,
    InvalidAudioError,
    MlxDependencyError,
    MlxRunnerError,
    ManifestPathError,
    ModelSnapshotError,
    NoAudioGeneratedError,
    PreparedReferenceError,
    ReferencePathError,
    mx,
    np,
    require_mlx,
    require_soundfile,
    sf,
)
from multimodel_round1_mlx_paths import (
    contained_artifact_path,
    reference_path,
    safe_hash_file,
    safe_stat_file,
    safe_write_bytes,
)
from multimodel_round1_paths import (
    ContainedPath,
    PathSafetyError,
    safe_file_stat,
    safe_read_bytes,
)


# Keep these private aliases for existing benchmark helpers and evaluator imports.
_require_mlx = require_mlx
_require_soundfile = require_soundfile


def disable_optional_sklearn() -> None:
    """Prevent optional sklearn imports from changing the offline runtime."""

    import transformers.utils as transformers_utils
    import transformers.utils.import_utils as import_utils

    unavailable = lambda: False
    import_utils.is_sklearn_available = unavailable
    transformers_utils.is_sklearn_available = unavailable


def audio_metrics(
    path: Path,
    text: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    audio, sample_rate = read_audio(path, root=root, always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / int(sample_rate)
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "duration_seconds": duration,
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "words_per_second": len(text.split()) / duration if duration else None,
    }


def read_audio(
    path: Path,
    *,
    root: Path | None = None,
    always_2d: bool = False,
) -> tuple[np.ndarray, int]:
    """Read a contained audio artifact through an in-memory safe descriptor."""

    target = contained_artifact_path(root or path.parent, path, kind="metadata")
    return _require_soundfile().read(
        io.BytesIO(safe_read_bytes(target)),
        dtype="float32",
        always_2d=always_2d,
    )


def write_audio_wav(
    evidence_root: Path,
    path: Path,
    audio: np.ndarray,
    sample_rate: int,
) -> Path:
    """Encode audio in memory and atomically write a contained WAV artifact."""

    buffer = io.BytesIO()
    _require_soundfile().write(buffer, audio, sample_rate, format="WAV")
    return safe_write_bytes(evidence_root, path, buffer.getvalue())


def peak_rss_gib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**3)


def release_sample_mlx_cache(mlx_module: Any | None = None) -> None:
    """Release unused MLX allocations after one sample."""

    (mlx_module or _require_mlx()).clear_cache()


def exact_snapshot(repo_id: str, revision: str) -> Path:
    roots = (
        Path.home() / ".cache" / "huggingface" / "hub",
        Path("/Users/tristan/pinokio/cache/HF_HOME/hub"),
    )
    folder = "models--" + repo_id.replace("/", "--")
    for root in roots:
        snapshot = root / folder / "snapshots" / revision
        if snapshot.is_dir() and (snapshot / "config.json").is_file():
            return snapshot.resolve()
    raise ModelSnapshotError(repo_id, revision)


def load_model(repo_id: str, revision: str) -> tuple[Any, Path]:
    """Load one exact cached MLX model only when explicitly requested."""

    try:
        from mlx_audio.tts.utils import load_model as mlx_load_model
        from mlx_audio.utils import get_model_name_parts
    except ModuleNotFoundError as exc:
        raise MlxDependencyError(exc.name or "mlx_audio") from exc
    snapshot = exact_snapshot(repo_id, revision)
    return (
        mlx_load_model(
            snapshot,
            model_name_parts=get_model_name_parts(repo_id),
            strict=False,
        ),
        snapshot,
    )


def collect_results(model: Any, results: Any) -> tuple[np.ndarray, int]:
    arrays: list[np.ndarray] = []
    sample_rate = int(getattr(model, "sample_rate", 24000))
    for result in results:
        array = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        if len(array):
            arrays.append(array)
        if getattr(result, "sample_rate", None):
            sample_rate = int(result.sample_rate)
    if not arrays:
        raise NoAudioGeneratedError(type(model).__name__)
    return arrays[0] if len(arrays) == 1 else np.concatenate(arrays), sample_rate


def prepared_reference_wav(
    evidence_root: Path,
    source: Path,
    *,
    sample_rate: int,
) -> Path:
    soundfile = _require_soundfile()
    source_target = reference_target(evidence_root, source)
    source_payload = safe_read_bytes(source_target)
    try:
        info = soundfile.info(io.BytesIO(source_payload))
    except (OSError, ValueError, wave.Error, PathSafetyError):
        info = None
    if (
        info is not None
        and info.format == "WAV"
        and int(info.channels) == 1
        and int(info.samplerate) == int(sample_rate)
        and int(info.frames) > 0
    ):
        return source
    cache = evidence_root / "prepared-references"
    target = cache / f"{sha256_file(source, root=evidence_root)}_{int(sample_rate)}hz.wav"
    if safe_stat_file(evidence_root, target, allow_missing=True):
        checked = soundfile.info(
            io.BytesIO(safe_read_bytes(contained_artifact_path(evidence_root, target)))
        )
        if (
            checked.format == "WAV"
            and int(checked.channels) == 1
            and int(checked.samplerate) == int(sample_rate)
            and int(checked.frames) > 0
        ):
            return target
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            str(int(sample_rate)),
            "-c:a",
            "pcm_f32le",
            "-f",
            "wav",
            "pipe:1",
        ],
        capture_output=True,
        input=source_payload,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        detail = (
            completed.stderr.decode("utf-8", "replace")[-2000:]
            if completed.stderr
            else ""
        )
        raise PreparedReferenceError(
            source,
            sample_rate,
            detail or "ffmpeg did not create the target",
        )
    safe_write_bytes(evidence_root, target, completed.stdout)
    return target


def resolve_reference(
    evidence_root: Path,
    sample: dict[str, Any],
) -> tuple[Path | None, str | None]:
    reference = sample["reference"]
    file_value = reference.get("conditioning_file")
    transcript = reference.get("conditioning_transcript")
    if not file_value:
        return None, None
    if Path(str(file_value)).is_absolute():
        raise ReferencePathError(Path(str(file_value)))
    target = reference_target(evidence_root, Path(str(file_value)))
    return target.literal, str(transcript or "")


def reference_target(evidence_root: Path, source: Path) -> ContainedPath:
    """Return a regular reference file after a no-follow descriptor check."""

    try:
        target = (
            contained_artifact_path(evidence_root, source, kind="metadata")
            if source.is_absolute()
            else reference_path(evidence_root, str(source))
        )
    except ArtifactPathError as exc:
        raise ReferencePathError(source) from exc
    try:
        safe_file_stat(target)
    except (PathSafetyError, FileNotFoundError) as exc:
        raise ReferencePathError(target.literal) from exc
    return target


def sha256_file(path: Path, *, root: Path | None = None) -> str:
    return safe_hash_file(root, path)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
