from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mlx_backend import MLXBackend  # noqa: E402
from model_registry import model_spec  # noqa: E402
from tts import TTSEngine  # noqa: E402
from voice_backend_capabilities import build_voice_backend_capabilities  # noqa: E402


SCHEMA_VERSION = 1
STANDARD_BACKEND = "qwen3_base"
CONTROLLED_BACKEND = "qwen3_instruction_controlled"
LEGACY_BACKEND = "voxcpm2_controlled"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def line_number(path: Path, needle: str) -> int | None:
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return index
    return None


class RecordingBackend:
    def __init__(self) -> None:
        self.standard_calls: list[dict[str, Any]] = []
        self.controlled_calls: list[dict[str, Any]] = []

    def generate_clone(self, **kwargs: Any) -> bool:
        self.standard_calls.append(dict(kwargs))
        return True

    def generate_instruction_controlled_clone(self, **kwargs: Any) -> bool:
        self.controlled_calls.append(dict(kwargs))
        return True


class TraceTokenizer:
    def encode(self, text: str) -> list[int]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [int(value) + 1 for value in digest[:4]]


class TraceEmbeddings:
    def __call__(self, ids: mx.array) -> mx.array:
        values = ids.astype(mx.float32)
        return mx.stack([values, values * 0.01], axis=-1)


class TraceTalker:
    def text_projection(self, values: mx.array) -> mx.array:
        return values

    def get_text_embeddings(self) -> TraceEmbeddings:
        return TraceEmbeddings()


class TraceQwenModel:
    def __init__(self) -> None:
        self.tokenizer = TraceTokenizer()
        self.talker = TraceTalker()

    def _prepare_icl_generation_inputs(self, *_args: Any, **_kwargs: Any):
        return (
            mx.zeros((1, 3, 2), dtype=mx.float32),
            mx.zeros((1, 1, 2), dtype=mx.float32),
            mx.zeros((1, 1, 2), dtype=mx.float32),
            mx.zeros((1, 1, 1), dtype=mx.float32),
        )


def _array_hash(value: mx.array) -> str:
    return sha256_bytes(np.asarray(value, dtype=np.float32).tobytes())


def embedding_trace() -> dict[str, Any]:
    model = TraceQwenModel()
    MLXBackend._enable_qwen_icl_instruction(model)
    traces: dict[str, Any] = {}
    cases = {
        "none": None,
        "neutral": "Natural, clear, conversational delivery.",
        "contrasting": "Urgent, clipped, forceful warning without shouting.",
    }
    for key, instruction in cases.items():
        model._alexandria_icl_instruction = instruction
        prepared = model._prepare_icl_generation_inputs("Target text.")[0]
        instruction_token_count = max(0, prepared.shape[1] - 3)
        instruction_slice = prepared[:, :instruction_token_count, :]
        original_slice = prepared[:, instruction_token_count:, :]
        traces[key] = {
            "instruction_present": instruction is not None,
            "instruction_sha256": sha256_text(instruction) if instruction else None,
            "prefill_shape": list(prepared.shape),
            "instruction_token_count": instruction_token_count,
            "instruction_embedding_sha256": (
                _array_hash(instruction_slice) if instruction_token_count else None
            ),
            "original_icl_embedding_sha256": _array_hash(original_slice),
        }
    model._alexandria_icl_instruction = None
    return {
        "ordering": "instruction_embedding_then_original_icl_prefill",
        "exactly_once": (
            traces["neutral"]["instruction_token_count"] == 4
            and traces["contrasting"]["instruction_token_count"] == 4
            and traces["none"]["instruction_token_count"] == 0
        ),
        "contrasting_embeddings_differ": (
            traces["neutral"]["instruction_embedding_sha256"]
            != traces["contrasting"]["instruction_embedding_sha256"]
        ),
        "original_icl_prefill_unchanged": len(
            {
                item["original_icl_embedding_sha256"]
                for item in traces.values()
            }
        )
        == 1,
        "cases": traces,
    }


def request_trace() -> dict[str, Any]:
    backend = RecordingBackend()
    engine = TTSEngine({"tts": {"mode": "local"}})
    engine._use_mlx = True
    engine._mlx_backend = backend
    reference = "/tmp/alexandria-trace-reference.wav"
    reference_text = "Exact synthetic reference transcript."
    style = "Preserve the supplied identity and accent."
    directions = {
        "neutral": "Natural, clear, conversational delivery.",
        "contrasting": "Urgent, clipped, forceful warning without shouting.",
    }
    with tempfile.TemporaryDirectory(prefix="alexandria-instruction-trace-") as temporary:
        output_root = Path(temporary)
        engine.generate_voice(
            "The gate is open.",
            directions["contrasting"],
            "STANDARD",
            {
                "STANDARD": {
                    "type": "clone",
                    "clone_backend": STANDARD_BACKEND,
                    "ref_audio": reference,
                    "ref_text": reference_text,
                }
            },
            str(output_root / "standard.wav"),
        )
        for key, direction in directions.items():
            engine.generate_voice(
                "The gate is open.",
                direction,
                "CONTROLLED",
                {
                    "CONTROLLED": {
                        "type": "clone",
                        "clone_backend": CONTROLLED_BACKEND,
                        "ref_audio": reference,
                        "ref_text": reference_text,
                        "character_style": style,
                        "seed": "314159",
                    }
                },
                str(output_root / f"controlled-{key}.wav"),
            )
        legacy_error = None
        try:
            engine.generate_voice(
                "The gate is open.",
                directions["contrasting"],
                "LEGACY",
                {
                    "LEGACY": {
                        "type": "clone",
                        "clone_backend": LEGACY_BACKEND,
                        "ref_audio": reference,
                        "ref_text": reference_text,
                    }
                },
                str(output_root / "legacy.wav"),
            )
        except ValueError as exc:
            legacy_error = str(exc)

    controlled = []
    for key, call in zip(directions, backend.controlled_calls):
        combined = str(call.get("instruct") or "")
        controlled.append(
            {
                "case": key,
                "line_instruction_sha256": sha256_text(directions[key]),
                "combined_instruction_sha256": sha256_text(combined),
                "combined_instruction_length": len(combined),
                "contains_line_instruction": directions[key] in combined,
                "contains_identity_constraint": style in combined,
                "request_label": call.get("request_label"),
                "seed": call.get("seed"),
            }
        )
    standard_call = backend.standard_calls[0]
    return {
        "standard_clone": {
            "backend": STANDARD_BACKEND,
            "line_instruction_supplied_to_tts": True,
            "instruction_forwarded_to_backend": "instruct" in standard_call,
            "call_keys": sorted(standard_call),
        },
        "controlled_clone": {
            "backend": CONTROLLED_BACKEND,
            "request_count": len(controlled),
            "requests": controlled,
            "contrasting_combined_instructions_differ": (
                len({item["combined_instruction_sha256"] for item in controlled})
                == len(controlled)
            ),
        },
        "legacy_clone": {
            "backend": LEGACY_BACKEND,
            "production_blocked": legacy_error is not None,
            "error_sha256": sha256_text(legacy_error) if legacy_error else None,
        },
    }


def active_voice_assignments(root: Path) -> dict[str, Any]:
    config_path = root / "voice_config.json"
    if not config_path.is_file():
        return {
            "available": False,
            "reason": "voice_config_missing",
            "backend_counts": {},
            "voices": [],
        }
    value = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("voice_config.json must contain an object.")
    voices = []
    counts: dict[str, int] = {}
    for speaker, raw in sorted(value.items()):
        if not isinstance(raw, dict) or raw.get("type") != "clone":
            continue
        backend = str(raw.get("clone_backend") or STANDARD_BACKEND)
        counts[backend] = counts.get(backend, 0) + 1
        reference_text = str(raw.get("ref_text") or "")
        reference_path_text = str(raw.get("ref_audio") or "")
        reference_path = Path(reference_path_text).expanduser()
        if reference_path_text and not reference_path.is_absolute():
            reference_path = (root / reference_path).resolve()
        voices.append(
            {
                "speaker": speaker,
                "backend": backend,
                "reference_audio_exists": reference_path.is_file(),
                "reference_audio_sha256": (
                    sha256_file(reference_path) if reference_path.is_file() else None
                ),
                "reference_text_sha256": (
                    sha256_text(reference_text) if reference_text else None
                ),
                "reference_text_length": len(reference_text),
                "character_style_present": bool(
                    str(raw.get("character_style") or raw.get("default_style") or "").strip()
                ),
                "controlled_approval_fingerprint_present": bool(
                    raw.get("controlled_clone_configuration_fingerprint")
                ),
            }
        )
    return {
        "available": True,
        "voice_count": len(voices),
        "backend_counts": counts,
        "voices": voices,
    }


def evidence_comparison(root: Path, assignments: dict[str, Any]) -> dict[str, Any]:
    legacy_path = root / "benchmarks/results/20260717T031401Z_voxcpm2_controlled_clone.json"
    qwen_path = root / "benchmarks/results/20260721T145449Z_expressive_clone_baseline_smoke.json"
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    qwen = json.loads(qwen_path.read_text(encoding="utf-8"))
    historical_reference_hash = legacy.get("reference_audio_sha256")
    matching_current = [
        item["speaker"]
        for item in assignments.get("voices", [])
        if item.get("reference_audio_sha256") == historical_reference_hash
    ]
    qwen_result = (qwen.get("candidate_results") or {}).get(
        "qwen_icl_patch_baseline", {}
    )
    return {
        "legacy_voxcpm2": {
            "evidence_file": str(legacy_path.relative_to(root)),
            "backend": legacy.get("backend"),
            "model": legacy.get("model"),
            "reference_audio_sha256": historical_reference_hash,
            "matching_current_speakers": matching_current,
            "neutral_audio_sha256": (
                (legacy.get("measurements") or {}).get("neutral") or {}
            ).get("audio_sha256"),
            "contrasting_audio_sha256": (
                (legacy.get("measurements") or {}).get("expressive") or {}
            ).get("audio_sha256"),
            "outputs_differ": (
                ((legacy.get("measurements") or {}).get("neutral") or {}).get(
                    "audio_sha256"
                )
                != ((legacy.get("measurements") or {}).get("expressive") or {}).get(
                    "audio_sha256"
                )
            ),
            "current_production_support": False,
        },
        "qwen_icl_patch": {
            "evidence_file": str(qwen_path.relative_to(root)),
            "model": qwen_result.get("model"),
            "revision": qwen_result.get("revision"),
            "measurement_count": qwen_result.get("measurement_count"),
            "error_count": qwen_result.get("error_count"),
            "post_generation_prosody_applied": qwen_result.get(
                "post_generation_prosody_applied"
            ),
            "delivery_adherence_accepted": qwen_result.get(
                "delivery_adherence_accepted"
            ),
            "comparison_only": qwen_result.get("comparison_only"),
            "manual_listening_status": (
                qwen.get("listening_review") or {}
            ).get("status"),
        },
    }


def source_position_proof(root: Path) -> dict[str, Any]:
    tts_path = root / "app/tts.py"
    mlx_path = root / "app/mlx_backend.py"
    installed_mlx_path = (
        root
        / "app/env/lib/python3.10/site-packages/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py"
    )
    official_path = (
        root
        / "app/env/lib/python3.10/site-packages/qwen_tts/core/models/modeling_qwen3_tts.py"
    )
    paths = [tts_path, mlx_path, installed_mlx_path, official_path]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    return {
        "tts_combines_line_and_identity": {
            "path": str(tts_path.relative_to(root)),
            "line": line_number(tts_path, 'if clone_backend == "qwen3_instruction_controlled"'),
            "call_line": line_number(tts_path, "generate_instruction_controlled_clone("),
            "source_sha256": sha256_file(tts_path),
        },
        "mlx_sets_request_local_instruction": {
            "path": str(mlx_path.relative_to(root)),
            "set_line": line_number(
                mlx_path, "model._alexandria_icl_instruction = delivery"
            ),
            "prepend_line": line_number(mlx_path, "[instruct_embed, input_embeds]"),
            "clear_line": line_number(
                mlx_path, "model._alexandria_icl_instruction = None"
            ),
            "post_processing_line": line_number(
                mlx_path, "prosody = apply_delivery_prosody("
            ),
            "source_sha256": sha256_file(mlx_path),
        },
        "installed_mlx_base_drops_public_instruct_for_icl": {
            "path": str(installed_mlx_path.relative_to(root)),
            "public_instruct_argument_line": line_number(
                installed_mlx_path, "instruct: Optional[str] = None,"
            ),
            "icl_call_line": line_number(installed_mlx_path, "yield from self._generate_icl("),
            "icl_signature_line": line_number(
                installed_mlx_path, "def _prepare_icl_generation_inputs("
            ),
            "source_sha256": sha256_file(installed_mlx_path),
        },
        "official_pytorch_orders_instruction_before_tts_prompt": {
            "path": str(official_path.relative_to(root)),
            "instruction_append_line": line_number(
                official_path, "talker_input_embeds[index].append(self.talker.text_projection("
            ),
            "tts_prompt_append_line": line_number(
                official_path, "talker_input_embeds[index].append(talker_input_embed)"
            ),
            "source_sha256": sha256_file(official_path),
        },
    }


def build_trace(root: Path) -> dict[str, Any]:
    root = root.resolve()
    assignments = active_voice_assignments(root)
    request = request_trace()
    embedding = embedding_trace()
    evidence = evidence_comparison(root, assignments)
    capabilities = build_voice_backend_capabilities(root_dir=root).get(
        "expressive_clone", {}
    )
    backend_counts = assignments.get("backend_counts", {})
    standard_count = int(backend_counts.get(STANDARD_BACKEND, 0))
    controlled_count = int(backend_counts.get(CONTROLLED_BACKEND, 0))
    legacy_count = int(backend_counts.get(LEGACY_BACKEND, 0))
    shared_path_intact = bool(
        request["controlled_clone"]["request_count"] == 2
        and all(
            item["contains_line_instruction"]
            and item["contains_identity_constraint"]
            for item in request["controlled_clone"]["requests"]
        )
        and embedding["exactly_once"]
        and embedding["contrasting_embeddings_differ"]
        and embedding["original_icl_prefill_unchanged"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_kind": "instruction_control_regression_trace",
        "environment": {
            "python": sys.version,
            "mlx_audio_version": package_version("mlx-audio"),
            "qwen_tts_version": package_version("qwen-tts"),
            "transformers_version": package_version("transformers"),
            "qwen_clone_model": model_spec("mlx_clone").as_dict(),
            "legacy_controlled_model": model_spec("mlx_controlled_clone").as_dict(),
        },
        "source_position_proof": source_position_proof(root),
        "request_trace": request,
        "embedding_trace": embedding,
        "active_assignments": assignments,
        "capability_contract": {
            key: capabilities.get(key)
            for key in (
                "supported",
                "experimental_preview_available",
                "status",
                "backend",
                "legacy_backend",
                "legacy_backend_supported",
                "per_line_instruction_supported",
                "instruction_channel_present",
                "production_default",
                "preview_and_manual_review_required",
                "warning",
            )
        },
        "evidence_comparison": evidence,
        "classification": {
            "shared_request_path_drops_instruction": not shared_path_intact,
            "shared_request_path_intact": shared_path_intact,
            "active_controlled_assignment_count": controlled_count,
            "active_standard_instruction_inert_assignment_count": standard_count,
            "active_legacy_blocked_assignment_count": legacy_count,
            "primary_cause": "configuration_and_backend_policy_specific",
            "reference_specific_for_historical_doctor_voice": not bool(
                evidence["legacy_voxcpm2"]["matching_current_speakers"]
            ),
            "model_directionality_accepted": bool(capabilities.get("supported")),
            "production_acoustic_difference_confounded_by_post_processing": True,
            "required_follow_up": (
                "Keep standard clones labelled instruction-inert; require an exact "
                "Qwen preview/listen receipt before controlled assignment; complete "
                "same-corpus no/neutral/contrasting acoustic and blinded listening "
                "acceptance before claiming model-level delivery adherence."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_trace(Path(args.root))
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["classification"], sort_keys=True))


if __name__ == "__main__":
    main()
