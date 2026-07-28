from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TypeAlias
from unittest.mock import patch

import numpy as np


JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
ControlValue: TypeAlias = str | float | None
ConditionValue: TypeAlias = str | float | int
ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))


@dataclass(frozen=True, slots=True)
class _GenerationInput:
    text: str
    conditionals: dict[str, ConditionValue]
    controls: dict[str, ControlValue]


class _FakeTensor:
    def __init__(self, audio: np.ndarray) -> None:
        self._audio = audio

    def detach(self) -> _FakeTensor:
        return self

    def cpu(self) -> _FakeTensor:
        return self

    def numpy(self) -> np.ndarray:
        return self._audio


class _FakeChatterboxModel:
    sr = 24_000

    def __init__(self) -> None:
        self.conds: dict[str, ConditionValue] | None = None
        self.prepared_conditionals: list[dict[str, ConditionValue]] = []
        self.generation_inputs: list[_GenerationInput] = []

    def prepare_conditionals(self, reference: str, *, exaggeration: float) -> None:
        self.conds = {
            "reference": Path(reference).name,
            "exaggeration": exaggeration,
            "generation_count": 0,
        }
        self.prepared_conditionals.append(copy.deepcopy(self.conds))

    def generate(self, text: str, **controls: ControlValue) -> _FakeTensor:
        if self.conds is None:
            raise AssertionError("prepare_conditionals was not called")
        self.generation_inputs.append(
            _GenerationInput(text, copy.deepcopy(self.conds), controls)
        )
        generation_count = int(self.conds["generation_count"])
        audio = np.full(480, 0.125 + generation_count * 0.125, dtype=np.float32)
        self.conds["generation_count"] = generation_count + 1
        return _FakeTensor(audio)


class _FakeLease:
    def close(self) -> None:
        return None


class _FakeMps:
    def synchronize(self) -> None:
        return None

    def empty_cache(self) -> None:
        return None


class _FakeTorch:
    def __init__(self) -> None:
        self.mps = _FakeMps()

    def manual_seed(self, _seed: int) -> None:
        return None


def _load_runner() -> ModuleType:
    path = BENCHMARKS / "run_multimodel_round1_chatterbox_v3.py"
    spec = importlib.util.spec_from_file_location("chatterbox_cache_runner", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load Chatterbox runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample(sample_id: str, output_name: str) -> dict[str, JsonValue]:
    return {
        "sample_id": sample_id,
        "blind_id": f"blind-{sample_id}",
        "model_key": "chatterbox_multilingual_v3",
        "model_label": "Chatterbox Multilingual V3",
        "status": "pending_generation",
        "group": "baseline_positive",
        "style": "neutral",
        "identity_key": "fixture_voice",
        "target_text": "The same deterministic fixture line.",
        "target_text_sha256": "fixture-text-sha256",
        "output_file": f"outputs/{output_name}.wav",
        "result_file": f"outputs/{output_name}.json",
        "reference": {
            "conditioning_file": "fixture/reference.wav",
            "conditioning_sha256": "fixture-reference-sha256",
            "conditioning_transcript_sha256": "fixture-transcript-sha256",
        },
        "control": {
            "language_id": "en",
            "exaggeration": 0.5,
            "cfg_weight": 0.5,
        },
        "seed": 17,
    }


def _write_manifest(root: Path, samples: list[dict[str, JsonValue]]) -> None:
    (root / "round1_internal_manifest.json").write_text(
        json.dumps({"sample_specs": samples}), encoding="utf-8"
    )


def _write_reference(root: Path) -> None:
    reference = root / "references" / "fixture" / "reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"fixture-reference")


def _run_runner(
    runner: ModuleType, evidence_root: Path, model: _FakeChatterboxModel
) -> int:
    argv = [
        str(BENCHMARKS / "run_multimodel_round1_chatterbox_v3.py"),
        "--evidence-root",
        str(evidence_root),
        "--source-root",
        str(evidence_root / "source"),
    ]
    with (
        patch.object(runner.sys, "argv", argv),
        patch.dict(os.environ, {}, clear=False),
        patch.object(runner, "repository_head", return_value=runner.SOURCE_COMMIT),
        patch.object(runner, "exact_snapshot", return_value=evidence_root / "snapshot"),
        patch.object(runner, "validate_sample_references", return_value=None),
        patch.object(runner, "require_disk_headroom", return_value={"ok": True}),
        patch.object(runner, "acquire_metal_lock", return_value=_FakeLease()),
        patch.object(runner, "load_v3", return_value=(model, "fixture-device", _FakeTorch())),
        redirect_stdout(io.StringIO()),
    ):
        return runner.main()


class ChatterboxConditionalsCacheTests(unittest.TestCase):
    def test_runner_reprepares_repeated_reference_before_generation(self) -> None:
        # Given two identical generation inputs sharing one reference and a fake
        # whose generation mutates the currently prepared conditionals.
        runner = _load_runner()
        model = _FakeChatterboxModel()
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            samples = [_sample("fixture-first", "first"), _sample("fixture-second", "second")]
            _write_reference(evidence_root)
            _write_manifest(evidence_root, samples)

            # When the real runner entry point processes the first cache-key miss
            # followed by the repeated-reference cache-key hit candidate.
            exit_code = _run_runner(runner, evidence_root, model)

            first_bytes = (evidence_root / "outputs" / "first.wav").read_bytes()
            second_bytes = (evidence_root / "outputs" / "second.wav").read_bytes()
            receipts = [
                json.loads((evidence_root / "outputs" / f"{name}.json").read_text())
                for name in ("first", "second")
            ]

        # Then both paths prepare the same conditionals, pass the same generation
        # inputs, and publish byte-identical fixture WAVs without full-cond reuse.
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(model.prepared_conditionals), 2)
        self.assertEqual(model.prepared_conditionals[0], model.prepared_conditionals[1])
        self.assertEqual(model.generation_inputs[0], model.generation_inputs[1])
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(
            [receipt["conditionals_cache_key_seen_before"] for receipt in receipts],
            [False, True],
        )
        self.assertEqual(
            [receipt["conditionals_cache_hit"] for receipt in receipts],
            [False, False],
        )
        self.assertEqual(
            [receipt["conditionals_cache_reuse_policy"] for receipt in receipts],
            [runner.FULL_CONDITIONALS_REUSE_POLICY] * 2,
        )

    def test_legacy_cache_hits_are_marked_for_revalidation(self) -> None:
        runner = _load_runner()

        self.assertEqual(
            runner.legacy_cache_revalidation_status(True),
            "requires_revalidation",
        )
        self.assertEqual(runner.legacy_cache_revalidation_status(False), "not_flagged")
        self.assertEqual(runner.legacy_cache_revalidation_status(None), "not_flagged")

    def test_runner_rejects_traversal_artifact_paths(self) -> None:
        runner = _load_runner()
        cases = (("output_file", "../escaped.wav"), ("result_file", "../escaped.json"))
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                base, model = Path(directory), _FakeChatterboxModel()
                evidence_root = base / "evidence"
                evidence_root.mkdir()
                sample = _sample(f"fixture-{field}-traversal", "unused")
                sample[field] = value
                _write_reference(evidence_root)
                _write_manifest(evidence_root, [sample])

                exit_code = _run_runner(runner, evidence_root, model)

                self.assertEqual(exit_code, 1)
                self.assertFalse((base / Path(value).name).exists())

    def test_runner_rejects_symlink_artifacts(self) -> None:
        runner = _load_runner()
        for field, suffix in (("output_file", "wav"), ("result_file", "json")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                base, model = Path(directory), _FakeChatterboxModel()
                evidence_root, outside = base / "evidence", base / f"outside.{suffix}"
                evidence_root.mkdir()
                outside.write_bytes(b"sentinel")
                target = evidence_root / "outputs" / f"linked.{suffix}"
                target.parent.mkdir()
                target.symlink_to(outside)
                sample = _sample(f"fixture-{field}-link", "unused")
                sample[field] = f"outputs/linked.{suffix}"
                _write_reference(evidence_root)
                _write_manifest(evidence_root, [sample])

                exit_code = _run_runner(runner, evidence_root, model)

                self.assertEqual(exit_code, 1)
                self.assertEqual(outside.read_bytes(), b"sentinel")

    def test_runner_rejects_symlink_reference(self) -> None:
        runner = _load_runner()
        model = _FakeChatterboxModel()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            evidence_root = base / "evidence"
            evidence_root.mkdir()
            outside = base / "outside.wav"
            outside.write_bytes(b"outside-reference")
            reference = evidence_root / "references" / "fixture" / "reference.wav"
            reference.parent.mkdir(parents=True)
            reference.symlink_to(outside)
            _write_manifest(evidence_root, [_sample("fixture-reference-link", "linked")])

            exit_code = _run_runner(runner, evidence_root, model)

            self.assertEqual(exit_code, 1)
            self.assertEqual(model.prepared_conditionals, [])

    def test_runner_rejects_symlink_manifest(self) -> None:
        runner = _load_runner()
        model = _FakeChatterboxModel()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            evidence_root = base / "evidence"
            evidence_root.mkdir()
            outside_manifest = base / "manifest.json"
            outside_manifest.write_text(
                json.dumps({"sample_specs": [_sample("fixture-manifest-link", "linked")]}),
                encoding="utf-8",
            )
            (evidence_root / "round1_internal_manifest.json").symlink_to(outside_manifest)
            _write_reference(evidence_root)

            with self.assertRaises(OSError):
                _run_runner(runner, evidence_root, model)


if __name__ == "__main__":
    unittest.main()
