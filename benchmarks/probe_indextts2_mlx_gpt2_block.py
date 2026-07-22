#!/usr/bin/env python3
"""Prove one trained IndexTTS2 GPT-2 block maps from PyTorch to MLX.

Run `export-pytorch` in the pinned IndexTTS2 environment, then run `verify-mlx`
in Alexandria's MLX environment against the same NPZ bundle. This probe loads
no network assets and does not modify any model checkpoint.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


MODEL_DIM = 1280
HEADS = 20
LAYERS = 24
MAX_SEQUENCE = 2417
VOCAB_SIZE = 8194
BLOCK_PREFIX = "gpt.h.0."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("export-pytorch", "verify-mlx"))
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--result")
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument("--sequence-length", type=int, default=7)
    return parser.parse_args()


def export_pytorch(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    from transformers import GPT2Config
    from indextts.gpt.transformers_gpt2 import GPT2Block

    checkpoint = Path(args.checkpoint).expanduser().resolve()
    bundle = Path(args.bundle).expanduser().resolve()
    state = torch.load(
        checkpoint,
        map_location="cpu",
        weights_only=False,
        mmap=True,
    )
    block_state = {
        key.removeprefix(BLOCK_PREFIX): value.detach().cpu()
        for key, value in state.items()
        if key.startswith(BLOCK_PREFIX)
    }
    if len(block_state) != 12:
        raise RuntimeError(
            f"Expected 12 first-block tensors, found {len(block_state)}."
        )

    config = GPT2Config(
        vocab_size=VOCAB_SIZE,
        n_positions=MAX_SEQUENCE,
        n_ctx=MAX_SEQUENCE,
        n_embd=MODEL_DIM,
        n_layer=LAYERS,
        n_head=HEADS,
        resid_pdrop=0.0,
        embd_pdrop=0.0,
        attn_pdrop=0.0,
        use_cache=True,
    )
    block = GPT2Block(config, layer_idx=0)
    block.load_state_dict(block_state, strict=True)
    block.eval()

    rng = np.random.default_rng(args.seed)
    input_array = rng.standard_normal(
        (1, args.sequence_length, MODEL_DIM),
        dtype=np.float32,
    )
    tensor = torch.from_numpy(input_array)
    with torch.inference_mode():
        output = block(
            tensor,
            layer_past=None,
            attention_mask=None,
            head_mask=None,
            use_cache=False,
            output_attentions=False,
        )[0]

    arrays: dict[str, np.ndarray] = {
        "input": input_array,
        "pytorch_output": output.detach().cpu().numpy().astype(np.float32),
    }
    for key, value in block_state.items():
        arrays[f"weight::{key}"] = value.numpy().astype(np.float32)
    bundle.parent.mkdir(parents=True, exist_ok=True)
    np.savez(bundle, **arrays)
    result = {
        "schema_version": 1,
        "stage": "export-pytorch",
        "checkpoint": str(checkpoint),
        "bundle": str(bundle),
        "seed": args.seed,
        "sequence_length": args.sequence_length,
        "input_shape": list(input_array.shape),
        "output_shape": list(output.shape),
        "weight_count": len(block_state),
        "production_promotion_allowed": False,
    }
    print(json.dumps(result, indent=2))
    return result


def verify_mlx(args: argparse.Namespace) -> dict[str, Any]:
    import mlx.core as mx
    import mlx.nn as nn

    class Attention(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.head_dim = MODEL_DIM // HEADS
            self.scale = self.head_dim**-0.5
            self.c_attn = nn.Linear(MODEL_DIM, 3 * MODEL_DIM, bias=True)
            self.c_proj = nn.Linear(MODEL_DIM, MODEL_DIM, bias=True)

        def __call__(self, x: mx.array, mask: Any = "causal") -> mx.array:
            batch, length, _ = x.shape
            query, key, value = mx.split(self.c_attn(x), 3, axis=-1)
            query = query.reshape(batch, length, HEADS, -1).transpose(0, 2, 1, 3)
            key = key.reshape(batch, length, HEADS, -1).transpose(0, 2, 1, 3)
            value = value.reshape(batch, length, HEADS, -1).transpose(0, 2, 1, 3)
            output = mx.fast.scaled_dot_product_attention(
                query,
                key,
                value,
                scale=self.scale,
                mask=mask,
            )
            output = output.transpose(0, 2, 1, 3).reshape(batch, length, -1)
            return self.c_proj(output)

    class MLP(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.c_fc = nn.Linear(MODEL_DIM, 4 * MODEL_DIM, bias=True)
            self.c_proj = nn.Linear(4 * MODEL_DIM, MODEL_DIM, bias=True)

        def __call__(self, x: mx.array) -> mx.array:
            return self.c_proj(nn.gelu_approx(self.c_fc(x)))

    class TransformerBlock(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.ln_1 = nn.LayerNorm(MODEL_DIM, eps=1e-5)
            self.attn = Attention()
            self.ln_2 = nn.LayerNorm(MODEL_DIM, eps=1e-5)
            self.mlp = MLP()

        def __call__(self, x: mx.array) -> mx.array:
            hidden = x + self.attn(self.ln_1(x), mask="causal")
            return hidden + self.mlp(self.ln_2(hidden))

    bundle = Path(args.bundle).expanduser().resolve()
    data = np.load(bundle)
    block = TransformerBlock()

    direct = {
        "ln_1.weight",
        "ln_1.bias",
        "attn.c_attn.bias",
        "attn.c_proj.bias",
        "ln_2.weight",
        "ln_2.bias",
        "mlp.c_fc.bias",
        "mlp.c_proj.bias",
    }
    transpose = {
        "attn.c_attn.weight",
        "attn.c_proj.weight",
        "mlp.c_fc.weight",
        "mlp.c_proj.weight",
    }
    weights = []
    for key in sorted(direct | transpose):
        value = data[f"weight::{key}"]
        if key in transpose:
            value = value.T
        weights.append((key, mx.array(value)))
    block.load_weights(weights, strict=True)

    input_array = data["input"].astype(np.float32)
    expected = data["pytorch_output"].astype(np.float32)
    inputs = mx.array(input_array)
    actual_mx = block(inputs)
    mx.eval(actual_mx)
    actual = np.asarray(actual_mx, dtype=np.float32)

    difference = actual - expected
    max_absolute_error = float(np.max(np.abs(difference)))
    mean_absolute_error = float(np.mean(np.abs(difference)))
    root_mean_square_error = float(np.sqrt(np.mean(difference * difference)))
    expected_flat = expected.reshape(-1).astype(np.float64)
    actual_flat = actual.reshape(-1).astype(np.float64)
    cosine_similarity = float(
        np.dot(expected_flat, actual_flat)
        / (np.linalg.norm(expected_flat) * np.linalg.norm(actual_flat))
    )
    result = {
        "schema_version": 1,
        "stage": "verify-mlx",
        "bundle": str(bundle),
        "shape": list(actual.shape),
        "max_absolute_error": max_absolute_error,
        "mean_absolute_error": mean_absolute_error,
        "root_mean_square_error": root_mean_square_error,
        "cosine_similarity": cosine_similarity,
        "parity_passed": (
            max_absolute_error < 5e-4
            and mean_absolute_error < 5e-5
            and cosine_similarity > 0.9999999
        ),
        "production_promotion_allowed": False,
    }
    result_path = Path(args.result).expanduser().resolve() if args.result else None
    if result_path is not None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["parity_passed"]:
        raise SystemExit(2)
    return result


def main() -> int:
    args = parse_args()
    if args.stage == "export-pytorch":
        export_pytorch(args)
    else:
        verify_mlx(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
