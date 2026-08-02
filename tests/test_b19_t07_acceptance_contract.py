from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from b19_t07_acceptance_contract import expected_cases, load_manifest, validate_evidence

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "b19_t07_routes.json"
ROUTE_OWNER = ROOT / "app" / "static" / "navigation_routes.js"
ACCEPTED_BASE_SHA = "8f2e98bde6376caa7b3690c0f50f78ee592a1197"


class B19T07ArtifactContractTests(unittest.TestCase):
    def test_pins_current_route_owner_before_manifest_exists(self) -> None:
        source = ROUTE_OWNER.read_text(encoding="utf-8")
        for marker in (
            "projects: route('projects', 'Project Home')",
            "script: route('script', 'Script', 'project')",
            "cast: route('cast', 'Characters', 'project')",
            "produce: route('produce', 'Produce', 'project')",
            "export: route('export', 'Export', 'project')",
            "voices: route('voices', 'Voices')",
            "settings: route('settings', 'Settings')",
            "'more/maintenance': route('more', 'Maintenance', 'global', 'maintenance')",
            "'more/help-center': route('more', 'Help Center', 'global', 'help-center')",
        ):
            self.assertIn(marker, source)

    def test_requires_the_dedicated_manifest(self) -> None:
        self.assertTrue(MANIFEST.is_file(), "B19-T07 manifest is missing")

    def test_manifest_has_exact_release_matrix(self) -> None:
        manifest = load_manifest(MANIFEST)
        self.assertEqual(manifest["expected_case_count"], 234)
        self.assertEqual(
            manifest["viewports"],
            [
                {"id": "390x844", "width": 390, "height": 844},
                {"id": "768x1024", "width": 768, "height": 1024},
                {"id": "1024x768", "width": 1024, "height": 768},
                {"id": "1536x1024", "width": 1536, "height": 1024},
            ],
        )
        self.assertEqual(manifest["regression_only_viewports"], ["1440x1000"])
        self.assertEqual(
            manifest["physical_keys"],
            ["Tab", "Shift+Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Enter", "Space", "Escape"],
        )
        self.assertEqual([step["key"] for step in manifest["physical_key_steps"]], manifest["physical_keys"])
        self.assertEqual(
            manifest["required_artifacts"],
            ["screenshot", "ax_tree", "focus_trace", "live_region", "console_network_log", "identity"],
        )
        surfaces = manifest["surfaces"]
        self.assertEqual(len(surfaces), 13)
        self.assertEqual(sum(surface["kind"] == "route" for surface in surfaces), 9)
        self.assertEqual(sum(surface["kind"] == "scenario" for surface in surfaces), 4)
        self.assertEqual(len(expected_cases(manifest)), 234)

    def test_rejects_legacy_voiceover_artifact_requirement(self) -> None:
        manifest = copy.deepcopy(load_manifest(MANIFEST))
        manifest["required_artifacts"] = ["screenshot", "ax_tree", "focus_trace", "console_network_log", "voiceover_action_log", "identity"]
        with self.assertRaises(ValueError):
            expected_cases(manifest)

    def test_accepts_complete_current_run_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = validate_evidence(self._write_complete_evidence(Path(temporary)))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.expected_case_count, 234)
        self.assertEqual(result.observed_case_count, 234)

    def test_rejects_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            matrix = self._read_matrix(evidence_dir)
            matrix["cases"][0]["artifacts"].pop()
            self._write_matrix(evidence_dir, matrix)
            result = validate_evidence(evidence_dir)
        self.assertIn("missing_artifact", result.errors)

    def test_rejects_duplicate_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            matrix = self._read_matrix(evidence_dir)
            matrix["cases"].append(copy.deepcopy(matrix["cases"][0]))
            self._write_matrix(evidence_dir, matrix)
            result = validate_evidence(evidence_dir)
        self.assertIn("duplicate_artifact", result.errors)

    def test_rejects_stale_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            matrix = self._read_matrix(evidence_dir)
            matrix["cases"][0]["captured_at"] = "2026-08-01T11:59:59+00:00"
            self._write_matrix(evidence_dir, matrix)
            result = validate_evidence(evidence_dir)
        self.assertIn("stale_artifact", result.errors)

    def test_rejects_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            artifact = evidence_dir / self._read_matrix(evidence_dir)["cases"][0]["artifacts"][0]["path"]
            artifact.write_text("changed", encoding="utf-8")
            result = validate_evidence(evidence_dir)
        self.assertIn("sha_mismatch", result.errors)

    def test_rejects_url_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            matrix = self._read_matrix(evidence_dir)
            matrix["cases"][0]["final_url"] = "#/wrong"
            self._write_matrix(evidence_dir, matrix)
            result = validate_evidence(evidence_dir)
        self.assertIn("url_mismatch", result.errors)

    def test_rejects_route_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            matrix = self._read_matrix(evidence_dir)
            matrix["cases"][0]["body_route_path"] = "wrong"
            self._write_matrix(evidence_dir, matrix)
            result = validate_evidence(evidence_dir)
        self.assertIn("route_mismatch", result.errors)

    def test_rejects_malformed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            matrix = self._read_matrix(evidence_dir)
            matrix["cases"][0]["artifacts"][0]["sha256"] = "not-a-sha"
            self._write_matrix(evidence_dir, matrix)
            result = validate_evidence(evidence_dir)
        self.assertIn("malformed_artifact", result.errors)

    def test_rejects_unexpected_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = self._write_complete_evidence(Path(temporary))
            (evidence_dir / "untracked.txt").write_text("unexpected", encoding="utf-8")
            result = validate_evidence(evidence_dir)
        self.assertIn("unexpected_artifact", result.errors)

    def _write_complete_evidence(self, evidence_dir: Path) -> Path:
        manifest = load_manifest(MANIFEST)
        cases: list[dict[str, object]] = []
        for expectation in expected_cases(manifest):
            artifacts: list[dict[str, str]] = []
            for kind in manifest["required_artifacts"]:
                relative = f"artifacts/{expectation.case_id}/{kind}.txt"
                path = evidence_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{expectation.case_id}:{kind}", encoding="utf-8")
                artifacts.append({
                    "kind": kind,
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                })
            cases.append({
                "case_id": expectation.case_id,
                "mode": expectation.mode,
                "surface": expectation.surface,
                "viewport": expectation.viewport,
                "profile": expectation.profile,
                "run_id": "b19-t07-contract-fixture",
                "base_sha": ACCEPTED_BASE_SHA,
                "final_sha": ACCEPTED_BASE_SHA,
                "captured_at": "2026-08-01T12:00:01+00:00",
                "requested_url": expectation.requested_url,
                "final_url": expectation.requested_url,
                "body_destination": expectation.destination,
                "body_route_path": expectation.route_path,
                "route_owner": expectation.route_owner,
                "artifacts": artifacts,
            })
        (evidence_dir / "run.json").write_text(json.dumps({
            "run_id": "b19-t07-contract-fixture",
            "base_sha": ACCEPTED_BASE_SHA,
            "final_sha": ACCEPTED_BASE_SHA,
            "started_at": "2026-08-01T12:00:00+00:00",
            "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        }, sort_keys=True), encoding="utf-8")
        self._write_matrix(evidence_dir, {"cases": cases})
        return evidence_dir

    @staticmethod
    def _read_matrix(evidence_dir: Path) -> dict[str, object]:
        return json.loads((evidence_dir / "matrix.json").read_text(encoding="utf-8"))

    @staticmethod
    def _write_matrix(evidence_dir: Path, matrix: dict[str, object]) -> None:
        (evidence_dir / "matrix.json").write_text(json.dumps(matrix, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
