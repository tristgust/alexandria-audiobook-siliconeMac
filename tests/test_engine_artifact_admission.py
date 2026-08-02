from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import model_registry


class EngineArtifactAdmissionTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> dict:
        engine = model_registry.engine_record_payload("qwen3_base")
        artifacts = []
        for component in engine["components"]:
            for declaration in component["artifacts"]:
                relative = declaration["path"]
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if relative.endswith(".safetensors"):
                    header = json.dumps(
                        {"x": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}},
                        separators=(",", ":"),
                    ).encode("utf-8")
                    data = len(header).to_bytes(8, "little") + header + b"\0\0\0\0"
                else:
                    data = b"{}" if relative.endswith(".json") else b"fixture\n"
                path.write_bytes(data)
                artifacts.append(
                    {
                        "artifact_id": f'{component["component_id"]}:{relative}',
                        "component_id": component["component_id"],
                        "component_revision": component["revision"],
                        "component_build_id": component["build_id"],
                        "source_id": component["source_id"],
                        "role": declaration["role"],
                        "path": relative,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "runtime": component["runtime"],
                        "loader": component["loader"],
                        "serialization": declaration["serialization"],
                    }
                )
        return {
            "schema_version": 1,
            "engine_id": engine["engine_id"],
            "engine_revision": engine["engine_revision"],
            "record_fingerprint": model_registry.engine_record_fingerprint(engine),
            "artifacts": artifacts,
        }

    def test_complete_fixture_admits_atomically_offline(self) -> None:
        from engine_artifact_admission import admit_engine_artifacts

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            manifest = self._write_fixture(source)
            destination = root / "admitted"
            with patch("socket.socket", side_effect=AssertionError("network")):
                receipt = admit_engine_artifacts(manifest, source, destination)
            self.assertEqual(receipt["engine_id"], "qwen3_base")
            self.assertEqual(receipt["destination"], str(destination))
            self.assertRegex(receipt["manifest_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                receipt["artifacts"],
                sorted(
                    [
                        {
                            "path": item["path"],
                            "size": item["size"],
                            "sha256": item["sha256"],
                        }
                        for item in manifest["artifacts"]
                    ],
                    key=lambda item: item["path"],
                ),
            )
            self.assertEqual(
                sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()),
                sorted(item["path"] for item in manifest["artifacts"]),
            )
            self.assertEqual(list(root.glob(".admitted.admission-*")), [])

    def test_failure_matrix_cleans_staging_and_preserves_destination(self) -> None:
        from engine_artifact_admission import ArtifactAdmissionError, admit_engine_artifacts

        cases = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            baseline = self._write_fixture(source)
            expected_codes = {
                "missing": "missing_artifact",
                "altered": "size_mismatch",
                "unexpected": "unexpected_artifact",
                "partial": "missing_artifact",
                "interrupted": "interrupted",
                "stale_revision": "stale_revision",
                "incompatible_tokenizer": "incompatible_tokenizer",
                "incompatible_codec": "incompatible_codec",
                "incompatible_adapter": "incompatible_adapter",
                "incompatible_component": "incompatible_component",
                "incompatible_loader": "incompatible_loader",
                "unsafe_serialization": "unsafe_serialization",
                "unknown_field": "unknown_field",
                "duplicate_id": "duplicate_id",
            }
            for case in ("missing", "altered", "unexpected", "partial", "interrupted", "stale_revision", "incompatible_tokenizer", "incompatible_codec", "incompatible_adapter", "incompatible_component", "incompatible_loader", "unsafe_serialization", "unknown_field", "duplicate_id"):
                manifest = copy.deepcopy(baseline)
                fixture = root / f"source-{case}"
                fixture.mkdir()
                for path in source.rglob("*"):
                    if path.is_file():
                        target = fixture / path.relative_to(source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(path.read_bytes())
                interrupt = None
                if case in {"missing", "partial"}:
                    (fixture / manifest["artifacts"][-1]["path"]).unlink()
                elif case == "altered":
                    (fixture / manifest["artifacts"][0]["path"]).write_bytes(b"altered")
                elif case == "unexpected":
                    (fixture / "unexpected.bin").write_bytes(b"unexpected")
                elif case == "interrupted":
                    interrupt = 1
                elif case == "stale_revision":
                    manifest["engine_revision"] = "0" * 40
                elif case == "incompatible_tokenizer":
                    artifact = next(
                        item
                        for item in manifest["artifacts"]
                        if item["role"] == "tokenizer"
                    )
                    artifact["component_revision"] = "0" * 40
                elif case == "incompatible_codec":
                    artifact = next(
                        item
                        for item in manifest["artifacts"]
                        if item["role"] == "codec"
                    )
                    artifact["component_build_id"] = "0" * 64
                elif case == "incompatible_adapter":
                    manifest["artifacts"][0]["role"] = "adapter"
                elif case == "incompatible_component":
                    manifest["artifacts"][0]["source_id"] = "other/source"
                elif case == "incompatible_loader":
                    manifest["artifacts"][0]["loader"] = "other.loader"
                elif case == "unsafe_serialization":
                    manifest["artifacts"][0]["serialization"] = "pickle"
                elif case == "unknown_field":
                    manifest["unknown"] = True
                elif case == "duplicate_id":
                    manifest["artifacts"].append(copy.deepcopy(manifest["artifacts"][0]))
                source_hashes = {
                    path.relative_to(fixture).as_posix(): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in fixture.rglob("*")
                    if path.is_file()
                }
                destination = root / f"destination-{case}"
                sentinel = destination / "sentinel"
                if case != "interrupted":
                    destination.mkdir()
                    sentinel.write_bytes(b"preserve")
                with self.assertRaises(ArtifactAdmissionError) as caught:
                    admit_engine_artifacts(
                        manifest,
                        fixture,
                        destination,
                        interrupt_after_copy=interrupt,
                    )
                self.assertEqual(caught.exception.code, expected_codes[case])
                if case == "interrupted":
                    self.assertFalse(destination.exists())
                else:
                    self.assertEqual(sentinel.read_bytes(), b"preserve")
                self.assertEqual(
                    source_hashes,
                    {
                        path.relative_to(fixture).as_posix(): hashlib.sha256(
                            path.read_bytes()
                        ).hexdigest()
                        for path in fixture.rglob("*")
                        if path.is_file()
                    },
                )
                self.assertEqual(list(root.glob(f".{destination.name}.admission-*")), [])
                cases.append(case)
        print(" ".join(cases))

    def test_strict_manifest_path_runtime_and_deserialization_guards(self) -> None:
        from engine_artifact_admission import ArtifactAdmissionError, admit_engine_artifacts

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            manifest = self._write_fixture(source)
            mutations = (
                ("stale_record", lambda value: value.update(record_fingerprint="0" * 64)),
                ("incompatible_runtime", lambda value: value["artifacts"][0].update(runtime="other")),
                ("unsafe_path", lambda value: value["artifacts"][0].update(path="../escape")),
                ("unknown_field", lambda value: value["artifacts"][0].update(extra=True)),
            )
            for expected_code, mutate in mutations:
                with self.subTest(expected_code=expected_code):
                    changed = copy.deepcopy(manifest)
                    mutate(changed)
                    with self.assertRaises(ArtifactAdmissionError) as caught:
                        admit_engine_artifacts(changed, source, root / expected_code)
                    self.assertEqual(caught.exception.code, expected_code)

            symlink_source = root / "symlink-source"
            symlink_source.mkdir()
            symlink_manifest = self._write_fixture(symlink_source)
            linked = symlink_source / symlink_manifest["artifacts"][0]["path"]
            linked.unlink()
            os.symlink(source / manifest["artifacts"][0]["path"], linked)
            with self.assertRaises(ArtifactAdmissionError) as caught:
                admit_engine_artifacts(symlink_manifest, symlink_source, root / "symlink")
            self.assertEqual(caught.exception.code, "unsafe_path")

            unsafe_source = root / "unsafe-source"
            unsafe_source.mkdir()
            unsafe_manifest = self._write_fixture(unsafe_source)
            tensor = next(
                item
                for item in unsafe_manifest["artifacts"]
                if item["serialization"] == "safetensors"
            )
            tensor_path = unsafe_source / tensor["path"]
            header = json.dumps(
                {"x": {"dtype": "F32", "shape": [1], "data_offsets": [0, 400]}},
                separators=(",", ":"),
            ).encode("utf-8")
            unsafe_data = len(header).to_bytes(8, "little") + header + b"\0\0\0\0"
            tensor_path.write_bytes(unsafe_data)
            tensor["size"] = len(unsafe_data)
            tensor["sha256"] = hashlib.sha256(unsafe_data).hexdigest()
            with self.assertRaises(ArtifactAdmissionError) as caught:
                admit_engine_artifacts(unsafe_manifest, unsafe_source, root / "unsafe")
            self.assertEqual(caught.exception.code, "unsafe_serialization")

    def test_repeated_interruption_is_cleanup_safe(self) -> None:
        from engine_artifact_admission import ArtifactAdmissionError, admit_engine_artifacts

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            manifest = self._write_fixture(source)
            destination = root / "destination"
            for _ in range(2):
                with self.assertRaises(ArtifactAdmissionError):
                    admit_engine_artifacts(
                        manifest,
                        source,
                        destination,
                        interrupt_after_copy=1,
                    )
                self.assertEqual(list(root.glob(".destination.admission-*")), [])
            receipt = admit_engine_artifacts(manifest, source, destination)
            self.assertEqual(receipt["engine_id"], "qwen3_base")
            with self.assertRaises(ArtifactAdmissionError) as caught:
                admit_engine_artifacts(manifest, source, destination)
            self.assertEqual(caught.exception.code, "destination_collision")


if __name__ == "__main__":
    unittest.main()
