from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

import mlx.core as mx
import mlx.nn as nn


DESCRIPTOR_SCHEMA_VERSION = 1
DISK_RESERVE_BYTES = 16 * 1024**3
CONVERSION_OVERHEAD_BYTES = 768 * 1024**2


class CommunityQwenRuntimeError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_peft_files(source: Path) -> tuple[Path, ...]:
    return tuple(
        source / name
        for name in (
            "adapter_config.json",
            "adapter_model.safetensors",
            "speaker_embedding.safetensors",
            "tts_config.json",
        )
    )


def source_inventory(
    source: str | Path,
    *,
    paths: Iterable[Path] | None = None,
    include_hashes: bool = True,
) -> list[dict[str, Any]]:
    root = Path(source).expanduser().resolve()
    selected = list(paths) if paths is not None else [
        path for path in sorted(root.rglob("*")) if path.is_file()
    ]
    result = []
    for path in selected:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        logical = Path(os.path.abspath(candidate))
        try:
            relative = logical.relative_to(root)
        except ValueError as exc:
            raise CommunityQwenRuntimeError(
                f"Community Qwen source file is unsafe: {path}"
            ) from exc
        if not logical.is_file():
            raise CommunityQwenRuntimeError(
                f"Community Qwen source file is missing: {relative.as_posix()}"
            )
        stat = logical.stat()
        link_stat = logical.lstat()
        item = {
            "path": relative.as_posix(),
            "size_bytes": stat.st_size,
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "is_symlink": logical.is_symlink(),
            "link_mtime_ns": link_stat.st_mtime_ns,
            "link_ctime_ns": link_stat.st_ctime_ns,
        }
        if logical.is_symlink():
            item["link_target"] = os.readlink(logical)
        if include_hashes:
            item["sha256"] = sha256_file(logical)
        result.append(item)
    return result


def inventory_fingerprint(items: Iterable[dict[str, Any]]) -> str:
    normalized = [
        {
            key: item[key]
            for key in (
                "path",
                "size_bytes",
                "sha256",
            )
            if key in item
        }
        for item in items
    ]
    return hashlib.sha256(
        json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def verify_source_inventory(
    source: str | Path,
    items: Iterable[dict[str, Any]],
) -> Path:
    root = Path(source).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise CommunityQwenRuntimeError(
            "The linked community Qwen source directory is unavailable."
        )
    for item in items:
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise CommunityQwenRuntimeError(
                "The linked community Qwen inventory contains an unsafe path."
            )
        path = root / relative
        if not path.is_file():
            raise CommunityQwenRuntimeError(
                f"The linked community Qwen source is missing {relative.as_posix()}."
            )
        stat = path.stat()
        link_stat = path.lstat()
        expected = (
            int(item.get("device", -1)),
            int(item.get("inode", -1)),
            int(item.get("size_bytes", -1)),
            int(item.get("mtime_ns", -1)),
            int(item.get("ctime_ns", -1)),
        )
        actual = (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )
        link_changed = (
            bool(item.get("is_symlink")) != path.is_symlink()
            or int(item.get("link_mtime_ns", -1)) != link_stat.st_mtime_ns
            or int(item.get("link_ctime_ns", -1)) != link_stat.st_ctime_ns
            or (
                path.is_symlink()
                and str(item.get("link_target") or "") != os.readlink(path)
            )
        )
        if actual != expected or link_changed:
            raise CommunityQwenRuntimeError(
                "The linked community Qwen source changed after import. "
                "Remove it from Alexandria and import the current files again."
            )
    return root


def write_descriptor(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    value = {
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        **payload,
    }
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def load_descriptor(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommunityQwenRuntimeError(
            "The installed community Qwen runtime descriptor is unreadable."
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise CommunityQwenRuntimeError(
            "The installed community Qwen runtime descriptor is unsupported."
        )
    return value


def resolve_descriptor_runtime(path: str | Path) -> tuple[dict[str, Any], Path]:
    descriptor_path = Path(path).expanduser().resolve()
    descriptor = load_descriptor(descriptor_path)
    runtime = str(descriptor.get("runtime") or "")
    if runtime == "mlx_peft_overlay":
        source = verify_source_inventory(
            str(descriptor.get("source_path") or ""),
            descriptor.get("source_inventory") or [],
        )
        return descriptor, source
    if runtime == "mlx_checkpoint":
        relative = Path(str(descriptor.get("model_path") or "mlx_model"))
        if relative.is_absolute() or ".." in relative.parts:
            raise CommunityQwenRuntimeError(
                "The installed MLX checkpoint path is unsafe."
            )
        model = (descriptor_path.parent / relative).resolve()
        if not model.is_relative_to(descriptor_path.parent) or not (
            model / "model.safetensors"
        ).is_file():
            raise CommunityQwenRuntimeError(
                "The installed MLX checkpoint is incomplete."
            )
        return descriptor, model
    raise CommunityQwenRuntimeError(
        f"Unsupported community Qwen runtime: {runtime or 'missing'}."
    )


class RuntimeLoraLinear(nn.Module):
    def __init__(
        self,
        base_layer: nn.Module,
        lora_a: mx.array,
        lora_b: mx.array,
        scale: float,
    ) -> None:
        super().__init__()
        self.base_layer = base_layer
        self.lora_a = lora_a
        self.lora_b = lora_b
        self.scale = float(scale)

    def __call__(self, value: mx.array) -> mx.array:
        base = self.base_layer(value)
        delta = (value @ self.lora_a.T) @ self.lora_b.T
        if delta.dtype != base.dtype:
            delta = delta.astype(base.dtype)
        return base + (delta * self.scale)


def _logical_linear_shape(module: nn.Module) -> tuple[int, int]:
    weight = getattr(module, "weight", None)
    if weight is None or len(weight.shape) != 2:
        raise CommunityQwenRuntimeError(
            "The PEFT adapter targets a non-linear MLX module."
        )
    output_dims = int(weight.shape[0])
    bits = getattr(module, "bits", None)
    input_dims = (
        int(weight.shape[1]) * 32 // int(bits)
        if bits is not None
        else int(weight.shape[1])
    )
    return output_dims, input_dims


def _adapter_module_key(key: str, suffix: str) -> str | None:
    marker = f".{suffix}"
    index = key.find(marker)
    if index < 0 or not key.endswith(".weight"):
        return None
    return key[:index]


def _resolve_target_module(
    adapter_key: str,
    modules: dict[str, nn.Module],
) -> tuple[str, nn.Module]:
    value = adapter_key
    for prefix in (
        "base_model.model.",
        "base_model.model.model.",
        "model.talker.",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    candidates = []
    if value.startswith("talker."):
        candidates.append(value)
    elif value.startswith("model."):
        candidates.append(f"talker.{value}")
    else:
        candidates.extend((f"talker.{value}", f"talker.model.{value}"))
    for candidate in candidates:
        module = modules.get(candidate)
        if module is not None:
            return candidate, module
    suffix_matches = [
        (name, module)
        for name, module in modules.items()
        if name.startswith("talker.") and name.endswith(value)
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    raise CommunityQwenRuntimeError(
        f"The PEFT adapter target does not match the MLX Qwen model: {adapter_key}"
    )


def _set_module(root: nn.Module, path: str, replacement: nn.Module) -> None:
    parts = path.split(".")
    owner: Any = root
    for part in parts[:-1]:
        owner = owner[int(part)] if part.isdigit() else getattr(owner, part)
    final = parts[-1]
    if final.isdigit():
        owner[int(final)] = replacement
    else:
        setattr(owner, final, replacement)


def _load_single_tensor(path: Path) -> mx.array:
    values = mx.load(str(path))
    if isinstance(values, mx.array):
        tensor = values
    elif isinstance(values, dict):
        preferred = (
            "speaker_embedding",
            "spk_embedding",
            "embedding",
            "weight",
        )
        tensor = next(
            (values[name] for name in preferred if name in values),
            None,
        )
        if tensor is None and len(values) == 1:
            tensor = next(iter(values.values()))
    else:
        tensor = None
    if tensor is None:
        raise CommunityQwenRuntimeError(
            f"No usable tensor was found in {path.name}."
        )
    return tensor.reshape(-1)


def apply_peft_speaker_bundle(model: nn.Module, source: str | Path) -> str:
    bundle = Path(source).expanduser().resolve()
    for required in _required_peft_files(bundle):
        if not required.is_file():
            raise CommunityQwenRuntimeError(
                f"The PEFT bundle is missing {required.name}."
            )
    try:
        adapter_config = json.loads(
            (bundle / "adapter_config.json").read_text(encoding="utf-8")
        )
        tts_config = json.loads(
            (bundle / "tts_config.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommunityQwenRuntimeError(
            "The PEFT bundle configuration is invalid."
        ) from exc
    if str(adapter_config.get("peft_type") or "LORA").upper() != "LORA":
        raise CommunityQwenRuntimeError("Only LoRA PEFT bundles are supported.")
    if adapter_config.get("use_dora"):
        raise CommunityQwenRuntimeError(
            "DoRA community bundles are not supported by the MLX overlay runtime."
        )
    rank = int(adapter_config.get("r") or 0)
    alpha = float(adapter_config.get("lora_alpha") or rank)
    if rank <= 0:
        raise CommunityQwenRuntimeError("The PEFT LoRA rank is invalid.")
    scale = alpha / (rank**0.5 if adapter_config.get("use_rslora") else rank)
    tensors = mx.load(str(bundle / "adapter_model.safetensors"))
    if not isinstance(tensors, dict):
        raise CommunityQwenRuntimeError("The PEFT adapter weights are invalid.")
    unsupported = [
        key for key in tensors
        if "lora_magnitude_vector" in key or "modules_to_save" in key
    ]
    if unsupported:
        raise CommunityQwenRuntimeError(
            "The PEFT bundle contains unsupported DoRA or modules-to-save weights."
        )
    groups: dict[str, dict[str, mx.array]] = {}
    for key, tensor in tensors.items():
        module_key = _adapter_module_key(key, "lora_A")
        side = "a"
        if module_key is None:
            module_key = _adapter_module_key(key, "lora_B")
            side = "b"
        if module_key is not None:
            groups.setdefault(module_key, {})[side] = tensor
    if not groups:
        raise CommunityQwenRuntimeError(
            "The PEFT bundle contains no LoRA A/B weight pairs."
        )
    modules = dict(model.named_modules())
    applied = 0
    for adapter_key, pair in groups.items():
        if set(pair) != {"a", "b"}:
            raise CommunityQwenRuntimeError(
                f"The PEFT adapter has an incomplete LoRA pair: {adapter_key}"
            )
        target_path, target = _resolve_target_module(adapter_key, modules)
        lora_a = pair["a"]
        lora_b = pair["b"]
        output_dims, input_dims = _logical_linear_shape(target)
        if tuple(lora_a.shape) != (rank, input_dims) or tuple(lora_b.shape) != (
            output_dims,
            rank,
        ):
            raise CommunityQwenRuntimeError(
                f"The PEFT dimensions do not match {target_path}."
            )
        _set_module(
            model,
            target_path,
            RuntimeLoraLinear(target, lora_a, lora_b, scale),
        )
        applied += 1
    speaker_ids = (
        tts_config.get("talker_config", {}).get("spk_id", {})
        if isinstance(tts_config, dict)
        else {}
    )
    if not isinstance(speaker_ids, dict) or len(speaker_ids) != 1:
        raise CommunityQwenRuntimeError(
            "A PEFT speaker bundle must define exactly one speaker ID."
        )
    speaker, raw_id = next(iter(speaker_ids.items()))
    speaker_id = int(raw_id)
    embedding_layer = model.talker.model.codec_embedding
    embedding = _load_single_tensor(bundle / "speaker_embedding.safetensors")
    weight = embedding_layer.weight
    if speaker_id < 0 or speaker_id >= int(weight.shape[0]):
        raise CommunityQwenRuntimeError(
            "The PEFT speaker ID is outside the CustomVoice embedding table."
        )
    if int(embedding.shape[0]) != int(weight.shape[1]):
        raise CommunityQwenRuntimeError(
            "The PEFT speaker embedding dimension does not match CustomVoice."
        )
    replacement = embedding.astype(weight.dtype).reshape(1, -1)
    embedding_layer.weight = mx.concatenate(
        (weight[:speaker_id], replacement, weight[speaker_id + 1 :]),
        axis=0,
    )
    model.config.talker_config.spk_id = {
        **dict(model.config.talker_config.spk_id or {}),
        str(speaker): speaker_id,
    }
    model.supported_speakers = list(model.config.talker_config.spk_id)
    mx.eval(model.parameters())
    if applied <= 0:
        raise CommunityQwenRuntimeError("No PEFT LoRA layers were applied.")
    return str(speaker)


def conversion_plan(
    source: str | Path,
    destination_parent: str | Path,
    *,
    q_bits: int = 8,
) -> dict[str, Any]:
    source_dir = Path(source).expanduser().resolve()
    destination = Path(destination_parent).expanduser().resolve()
    root_weights = [
        path
        for pattern in ("model*.safetensors", "pytorch_model*.bin")
        for path in source_dir.glob(pattern)
        if path.is_file()
    ]
    source_weight_bytes = sum(path.stat().st_size for path in root_weights)
    ratio = max(0.3, min(1.0, q_bits / 16.0))
    estimated_output = int(
        source_weight_bytes * ratio * 1.25 + CONVERSION_OVERHEAD_BYTES
    )
    disk_probe = destination
    while not disk_probe.exists() and disk_probe != disk_probe.parent:
        disk_probe = disk_probe.parent
    available = shutil.disk_usage(disk_probe).free
    required = estimated_output + DISK_RESERVE_BYTES
    return {
        "q_bits": q_bits,
        "source_weight_bytes": source_weight_bytes,
        "estimated_output_bytes": estimated_output,
        "reserved_free_bytes": DISK_RESERVE_BYTES,
        "required_free_bytes": required,
        "available_free_bytes": available,
        "allowed": available >= required,
    }


def _link_or_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def _conversion_source_view(source: Path, parent: Path) -> Path:
    view = parent / f".qwen-source-view.{uuid.uuid4().hex}"
    view.mkdir(parents=True)
    try:
        for child in source.iterdir():
            if child.name == "speech_tokenizer":
                continue
            os.symlink(
                str(child),
                str(view / child.name),
                target_is_directory=child.is_dir(),
            )
    except Exception:
        shutil.rmtree(view, ignore_errors=True)
        raise
    return view


def _copy_support_files(source: Path, destination: Path) -> dict[str, int]:
    linked = 0
    copied = 0
    excluded_root = {
        "model.safetensors",
        "model.safetensors.index.json",
        "pytorch_model.bin",
        "pytorch_model.bin.index.json",
    }
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if len(relative.parts) == 1 and (
            relative.name in excluded_root
            or relative.name.startswith("model-")
            or relative.name.startswith("pytorch_model-")
        ):
            continue
        mode = _link_or_copy(path, destination / relative)
        linked += mode == "hardlink"
        copied += mode == "copy"
    return {"hardlinked_files": linked, "copied_files": copied}


def convert_full_checkpoint_low_disk(
    *,
    source_dir: str | Path,
    output_dir: str | Path,
    q_bits: int = 8,
    q_group_size: int = 64,
) -> dict[str, Any]:
    from mlx.utils import tree_flatten
    from mlx_audio.tts.utils import load_model
    from mlx_audio.utils import load_config
    from mlx_lm.utils import quantize_model, save_config, save_model

    source = Path(source_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not source.is_dir():
        raise CommunityQwenRuntimeError(
            "The full CustomVoice checkpoint directory does not exist."
        )
    if output.exists():
        raise CommunityQwenRuntimeError(
            "The MLX conversion destination already exists."
        )
    if q_bits not in {4, 8} or q_group_size <= 0:
        raise CommunityQwenRuntimeError(
            "MLX conversion supports 4-bit or 8-bit quantization."
        )
    plan = conversion_plan(source, output.parent, q_bits=q_bits)
    if not plan["allowed"]:
        raise CommunityQwenRuntimeError(
            "MLX conversion was blocked to preserve free disk space. "
            f"It requires about {plan['required_free_bytes']} bytes free, but "
            f"only {plan['available_free_bytes']} bytes are available."
        )
    temporary = output.parent / f".{output.name}.converting.{uuid.uuid4().hex}"
    temporary.mkdir(parents=True)
    source_view = _conversion_source_view(source, output.parent)
    try:
        config = load_config(source_view)
        model = load_model(source_view, lazy=True, strict=True)
        weights = dict(tree_flatten(model.parameters()))
        configured_dtype = str(config.get("torch_dtype") or "").replace("torch.", "")
        if configured_dtype in {"float16", "bfloat16", "float32"}:
            dtype = getattr(mx, configured_dtype)
            weights = {key: value.astype(dtype) for key, value in weights.items()}

        def quantize_predicate(path: str, module: nn.Module) -> bool:
            model_predicate = getattr(
                model,
                "model_quant_predicate",
                lambda _path, _module: True,
            )
            return (
                hasattr(module, "weight")
                and module.weight.shape[-1] % 64 == 0
                and hasattr(module, "to_quantized")
                and model_predicate(path, module)
            )

        model.load_weights(list(weights.items()))
        _, quantized_config = quantize_model(
            model,
            config,
            q_group_size,
            q_bits,
            mode="affine",
            quant_predicate=quantize_predicate,
        )
        support = _copy_support_files(source, temporary)
        save_model(temporary, model, donate_model=True)
        save_config(quantized_config, config_path=temporary / "config.json")
        if not (temporary / "model.safetensors").is_file():
            raise CommunityQwenRuntimeError(
                "MLX conversion did not create model.safetensors."
            )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(source_view, ignore_errors=True)
    size_bytes = sum(
        path.stat().st_size for path in output.rglob("*") if path.is_file()
    )
    return {
        "status": "converted",
        "output_dir": str(output),
        "size_bytes": size_bytes,
        "plan": plan,
        **support,
    }


__all__ = [
    "CommunityQwenRuntimeError",
    "RuntimeLoraLinear",
    "apply_peft_speaker_bundle",
    "conversion_plan",
    "convert_full_checkpoint_low_disk",
    "inventory_fingerprint",
    "load_descriptor",
    "resolve_descriptor_runtime",
    "sha256_file",
    "source_inventory",
    "verify_source_inventory",
    "write_descriptor",
]
