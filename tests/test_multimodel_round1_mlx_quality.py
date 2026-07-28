from __future__ import annotations

import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))


class MultimodelRound1MlxQualityTests(unittest.TestCase):
    def test_runner_imports_without_optional_mlx_audio_dependencies(self) -> None:
        # Given an environment without MLX and soundfile installed.
        sys.modules.pop("run_multimodel_round1_mlx", None)

        # When the compatibility runner is imported.
        runner = importlib.import_module("run_multimodel_round1_mlx")

        # Then import succeeds without loading a model runtime.
        self.assertTrue(callable(runner.main))
        self.assertTrue(callable(runner.generate_moss))

    def test_reference_resolution_rejects_paths_outside_evidence(self) -> None:
        # Given a sample that names a traversal path.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            evidence.mkdir()
            sample = {
                "sample_id": "sample-a",
                "reference": {"conditioning_file": "../../outside.wav"},
            }

            # When the reference path is resolved.
            with self.assertRaises(runner.ReferencePathError):
                runner.resolve_reference(evidence, sample)

    def test_release_cache_preserves_compatibility_patch_surface(self) -> None:
        # Given a fake MLX module assigned through the legacy runner surface.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        calls: list[str] = []
        fake_mx = type("FakeMlx", (), {"clear_cache": lambda self: calls.append("clear")})()

        # When the compatibility cache-release function runs.
        with patch.object(runner, "mx", fake_mx):
            runner.release_sample_mlx_cache()

        # Then the patched dependency is used without loading MLX.
        self.assertEqual(calls, ["clear"])

    def test_empty_generation_raises_typed_error(self) -> None:
        # Given a fake model that yields no audio chunks.
        runner = importlib.import_module("run_multimodel_round1_mlx")

        class EmptyModel:
            sample_rate = 24_000

        # When result collection sees no audio.
        with self.assertRaises(runner.NoAudioGeneratedError):
            runner.collect_results(EmptyModel(), [])

    def test_absolute_manifest_reference_inside_evidence_is_rejected(self) -> None:
        # Given an absolute manifest path that still points below evidence.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            evidence.mkdir()
            target = evidence / "outside-references.wav"
            target.write_bytes(b"not-a-real-audio-file")
            sample = {
                "sample_id": "sample-absolute",
                "reference": {"conditioning_file": str(target)},
            }

            # When the manifest reference is resolved.
            with self.assertRaises(runner.ReferencePathError):
                runner.resolve_reference(evidence, sample)

    def test_reference_leaf_symlink_inside_evidence_is_rejected(self) -> None:
        # Given a reference symlink whose target remains below evidence.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            references = evidence / "references"
            references.mkdir(parents=True)
            target = evidence / "reference-storage.wav"
            target.write_bytes(b"not-a-real-audio-file")
            (references / "voice.wav").symlink_to(target)
            sample = {
                "sample_id": "sample-leaf-link",
                "reference": {"conditioning_file": "voice.wav"},
            }

            # When the reference is resolved, then the leaf link is rejected.
            with self.assertRaises(runner.ReferencePathError):
                runner.resolve_reference(evidence, sample)

    def test_reference_ancestor_symlink_is_rejected(self) -> None:
        # Given a references directory symlink whose target remains below evidence.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            storage = evidence / "reference-storage"
            storage.mkdir(parents=True)
            (storage / "voice.wav").write_bytes(b"not-a-real-audio-file")
            (evidence / "references").symlink_to(storage, target_is_directory=True)
            sample = {
                "sample_id": "sample-ancestor-link",
                "reference": {"conditioning_file": "voice.wav"},
            }

            # When the reference is resolved, then the ancestor link is rejected.
            with self.assertRaises(runner.ReferencePathError):
                runner.resolve_reference(evidence, sample)

    def test_manifest_artifact_absolute_and_traversal_paths_are_rejected(self) -> None:
        # Given output/result names from a manifest.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            evidence.mkdir()
            cases = (
                str(evidence / "absolute.wav"),
                "../escaped.wav",
            )
            for output_file in cases:
                with self.subTest(output_file=output_file):
                    sample = {
                        "sample_id": "sample-artifact-path",
                        "output_file": output_file,
                        "result_file": "outputs/sample.json",
                    }
                    with self.assertRaises(runner.ArtifactPathError):
                        runner.artifact_paths_for_sample(evidence, sample)

    def test_manifest_symlink_is_rejected(self) -> None:
        # Given a manifest symlink whose target remains below evidence.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            evidence.mkdir()
            target = evidence / "manifest-storage.json"
            target.write_text("{}", encoding="utf-8")
            (evidence / "round1_internal_manifest.json").symlink_to(target)

            # When the manifest is read, then the symlink is rejected.
            with self.assertRaises(runner.ManifestPathError):
                runner.safe_read_json(
                    evidence,
                    "round1_internal_manifest.json",
                    kind="manifest",
                )

    def test_summary_symlink_ancestor_is_rejected(self) -> None:
        # Given a summary directory symlink to a directory below evidence.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            evidence = base / "evidence"
            outside = base / "summary-storage"
            evidence.mkdir()
            outside.mkdir()
            (evidence / "generation-summaries").symlink_to(
                outside, target_is_directory=True
            )

            # When a summary is written, then no symlink ancestor is followed.
            with self.assertRaises(runner.ArtifactPathError):
                runner.safe_write_json(
                    evidence,
                    "generation-summaries/sample.json",
                    {"ok": True},
                    kind="summary",
                )
            self.assertEqual(list(outside.iterdir()), [])

    def test_output_and_result_symlink_targets_are_rejected(self) -> None:
        # Given output and result entries that redirect to sentinels.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            evidence = base / "evidence"
            output_dir = evidence / "outputs"
            output_dir.mkdir(parents=True)
            outside_audio = base / "outside.wav"
            outside_receipt = base / "outside.json"
            outside_audio.write_bytes(b"audio-sentinel")
            outside_receipt.write_text("receipt-sentinel", encoding="utf-8")
            output = output_dir / "sample.wav"
            receipt = output_dir / "sample.json"
            output.symlink_to(outside_audio)
            receipt.symlink_to(outside_receipt)

            # When either artifact is written, then neither link is followed.
            with self.assertRaises(runner.ArtifactPathError):
                runner.write_audio_wav(
                    evidence,
                    output,
                    runner.np.zeros(16, dtype=runner.np.float32),
                    24_000,
                )
            with self.assertRaises(runner.ArtifactPathError):
                runner.safe_write_json(
                    evidence,
                    "outputs/sample.json",
                    {"ok": True},
                    kind="artifact",
                )
            self.assertEqual(outside_audio.read_bytes(), b"audio-sentinel")
            self.assertEqual(
                outside_receipt.read_text(encoding="utf-8"),
                "receipt-sentinel",
            )

    def test_moss_metadata_symlink_is_rejected(self) -> None:
        # Given a MOSS cache metadata symlink under the evidence root.
        runner = importlib.import_module("run_multimodel_round1_mlx")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            cache_path, metadata_path = runner.moss_reference_cache_paths(
                evidence,
                "a" * 64,
                12,
            )
            cache_path.parent.mkdir(parents=True)
            cache_path.write_bytes(b"cache")
            target = evidence / "metadata-storage.json"
            target.write_text("{}", encoding="utf-8")
            metadata_path.symlink_to(target)

            # When metadata is read, then the symlink is rejected before MLX use.
            with self.assertRaises(runner.ArtifactPathError):
                runner.load_moss_reference_codes(
                    cache_path,
                    metadata_path,
                    reference_sha256="a" * 64,
                    num_quantizers=12,
                    evidence_root=evidence,
                )


if __name__ == "__main__":
    unittest.main()
