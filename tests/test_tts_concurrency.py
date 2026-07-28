from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from mlx_backend import MLXBackend
from project import ProjectManager


class SharedEngineConcurrencyTests(unittest.TestCase):
    def test_project_manager_initializes_one_engine_for_parallel_callers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = ProjectManager(temporary)
            created: list[object] = []
            results: list[object] = []
            errors: list[BaseException] = []
            start = threading.Barrier(3)

            class SlowEngine:
                mode = "local"

                def __init__(self, _config) -> None:
                    created.append(self)
                    time.sleep(0.08)

            def worker() -> None:
                try:
                    start.wait(timeout=2)
                    results.append(manager.get_engine())
                except BaseException as exc:  # pragma: no cover - diagnostic
                    errors.append(exc)

            with patch("project.TTSEngine", SlowEngine):
                threads = [threading.Thread(target=worker) for _ in range(2)]
                for thread in threads:
                    thread.start()
                start.wait(timeout=2)
                for thread in threads:
                    thread.join(timeout=3)

            self.assertEqual(errors, [])
            self.assertEqual(len(created), 1)
            self.assertEqual(len(results), 2)
            self.assertIs(results[0], results[1])
            self.assertIs(manager.engine, results[0])


class MLXControlledCloneConcurrencyTests(unittest.TestCase):
    @staticmethod
    def _write_reference(path: Path) -> None:
        timeline = np.arange(24000, dtype=np.float32) / 24000
        waveform = 0.1 * np.sin(2.0 * np.pi * 180.0 * timeline)
        sf.write(path, waveform, 24000)

    def test_model_loader_initializes_each_shared_model_once(self) -> None:
        backend = MLXBackend()
        loaded_model = object()
        calls: list[str] = []
        results: list[object] = []
        errors: list[BaseException] = []
        start = threading.Barrier(3)

        def slow_load(model_id: str):
            calls.append(model_id)
            time.sleep(0.08)
            return loaded_model

        def worker() -> None:
            try:
                start.wait(timeout=2)
                results.append(backend._model("expressive_clone"))
            except BaseException as exc:  # pragma: no cover - diagnostic
                errors.append(exc)

        with patch.object(backend, "_load_repository_model", side_effect=slow_load):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=3)

        self.assertEqual(errors, [])
        self.assertEqual(calls, [backend.EXPRESSIVE_CLONE_MODEL])
        self.assertEqual(results, [loaded_model, loaded_model])

    def test_controlled_clone_serializes_shared_model_and_honors_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            self._write_reference(reference)
            backend = MLXBackend()
            active_lock = threading.Lock()
            active = 0
            max_active = 0
            entered: list[str] = []
            errors: list[BaseException] = []
            start = threading.Barrier(3)

            class FakeVoxCPM2:
                _encode_sample_rate = 16000
                sample_rate = 48000

                def generate(self, **kwargs):
                    nonlocal active, max_active
                    with active_lock:
                        active += 1
                        max_active = max(max_active, active)
                        entered.append(kwargs["text"])
                    time.sleep(0.08)
                    with active_lock:
                        active -= 1
                    return [object()]

            backend._models["expressive_clone"] = FakeVoxCPM2()

            def worker(label: str, seed: int) -> None:
                try:
                    start.wait(timeout=2)
                    backend.generate_expressive_clone(
                        text=f"Line {label}",
                        ref_audio=str(reference),
                        ref_text="Exact reference transcript.",
                        instruct=f"Delivery {label}",
                        output_path=str(root / f"{label}.wav"),
                        seed=seed,
                        request_label=label,
                    )
                except BaseException as exc:  # pragma: no cover - diagnostic
                    errors.append(exc)

            with (
                patch.object(
                    backend,
                    "_collect_audio",
                    return_value=(np.zeros(4800, dtype=np.float32), 48000),
                ),
                patch("mlx_backend.mx.random.seed") as seed_mock,
            ):
                threads = [
                    threading.Thread(target=worker, args=("A", 17)),
                    threading.Thread(target=worker, args=("B", 23)),
                ]
                for thread in threads:
                    thread.start()
                start.wait(timeout=2)
                for thread in threads:
                    thread.join(timeout=4)

            self.assertEqual(errors, [])
            self.assertEqual(max_active, 1)
            self.assertCountEqual(entered, ["Line A", "Line B"])
            self.assertCountEqual(
                [call.args[0] for call in seed_mock.call_args_list],
                [17, 23],
            )
            self.assertTrue((root / "A.wav").is_file())
            self.assertTrue((root / "B.wav").is_file())

    def test_random_controlled_clone_reseeds_after_fixed_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            self._write_reference(reference)
            backend = MLXBackend()

            class FakeVoxCPM2:
                _encode_sample_rate = 16000
                sample_rate = 48000

                def generate(self, **_kwargs):
                    return [object()]

            backend._models["expressive_clone"] = FakeVoxCPM2()
            with (
                patch.object(
                    backend,
                    "_collect_audio",
                    return_value=(np.zeros(4800, dtype=np.float32), 48000),
                ),
                patch("mlx_backend.mx.random.seed") as seed_mock,
                patch("mlx_backend.secrets.randbits", return_value=777),
            ):
                backend.generate_expressive_clone(
                    text="Fixed line.",
                    ref_audio=str(reference),
                    ref_text="Exact reference transcript.",
                    instruct="Fixed delivery.",
                    output_path=str(root / "fixed.wav"),
                    seed=99,
                    request_label="fixed",
                )
                backend.generate_expressive_clone(
                    text="Random line.",
                    ref_audio=str(reference),
                    ref_text="Exact reference transcript.",
                    instruct="Random delivery.",
                    output_path=str(root / "random.wav"),
                    seed=-1,
                    request_label="random",
                )

            self.assertEqual(
                [call.args[0] for call in seed_mock.call_args_list],
                [99, 777],
            )

    def test_fixed_seed_controlled_clone_cannot_overlap_other_mlx_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            self._write_reference(reference)
            backend = MLXBackend()
            active_lock = threading.Lock()
            active = max_active = 0
            entered: list[str] = []
            errors: list[BaseException] = []
            start = threading.Barrier(3)
            voiced_audio, _ = sf.read(reference, dtype="float32")
            custom_collections = 0

            class SlowModel:
                _encode_sample_rate = 16000
                sample_rate = 48000

                def __init__(self, label: str) -> None:
                    self.label = label

                def generate(self, *_args, **_kwargs):
                    nonlocal active, max_active
                    with active_lock:
                        active += 1
                        max_active = max(max_active, active)
                        entered.append(self.label)
                    time.sleep(0.08)
                    with active_lock:
                        active -= 1
                    return [self.label]

            backend._models["custom"] = SlowModel("custom")
            backend._models["expressive_clone"] = SlowModel("controlled")

            def collect_audio(_model, results):
                nonlocal custom_collections
                is_custom = results[0] == "custom"
                custom_collections += int(is_custom)
                if is_custom and custom_collections == 1:
                    return voiced_audio[:4800], 48000
                return voiced_audio, 48000

            def custom_worker() -> None:
                try:
                    start.wait(timeout=2)
                    backend.generate_custom(
                        "Standard line.", "Neutral delivery.", "Ryan", str(root / "custom.wav")
                    )
                except BaseException as exc:  # pragma: no cover - diagnostic
                    errors.append(exc)

            def controlled_worker() -> None:
                try:
                    start.wait(timeout=2)
                    backend.generate_expressive_clone(
                        text="Controlled line.", ref_audio=str(reference),
                        ref_text="Exact reference transcript.",
                        instruct="Strongly controlled delivery.",
                        output_path=str(root / "controlled.wav"), seed=41,
                        request_label="DOCTOR",
                    )
                except BaseException as exc:  # pragma: no cover - diagnostic
                    errors.append(exc)

            with (
                patch.object(backend, "_collect_audio", side_effect=collect_audio),
                patch("mlx_backend.secrets.randbits", return_value=700),
                patch("mlx_backend.mx.random.seed") as seed_mock,
            ):
                threads = [threading.Thread(target=worker)
                           for worker in (custom_worker, controlled_worker)]
                for thread in threads:
                    thread.start()
                start.wait(timeout=2)
                for thread in threads:
                    thread.join(timeout=4)

            self.assertEqual(errors, [])
            self.assertEqual(max_active, 1)
            self.assertCountEqual(entered, ["custom", "custom", "controlled"])
            self.assertCountEqual(
                [call.args[0] for call in seed_mock.call_args_list], [700, 701, 41]
            )
            self.assertTrue((root / "custom.wav").is_file())
            self.assertTrue((root / "controlled.wav").is_file())


if __name__ == "__main__":
    unittest.main()
