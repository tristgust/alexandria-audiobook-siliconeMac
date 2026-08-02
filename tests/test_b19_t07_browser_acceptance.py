from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_artifacts import inspect_chunk_audio
from b19_t07_browser_acceptance import (
    REQUIRED_PROOF_KINDS,
    RunnerConfig,
    b19_t06_regression_commands,
    diagnostics_directory,
    isolated_server_environment,
    missing_full_proof,
    prepare_disposable_fixture,
    run_plan,
    write_accessibility_fixture_data,
)
from phase17e_api_harness import _copy_fixture
from project import ProjectManager


ROOT = Path(__file__).resolve().parents[1]


class BrowserAcceptanceTests(unittest.TestCase):
    def test_runtime_diagnostics_are_outside_the_validated_browser_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "browser"
            self.assertEqual(
                diagnostics_directory(artifacts),
                root / "task-09-integration" / "runtime",
            )
            self.assertNotEqual(diagnostics_directory(artifacts).parent, artifacts)

    def test_include_b19_t06_expands_to_all_required_regressions_on_one_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = RunnerConfig(
                artifacts=root / "browser",
                repo_root=ROOT,
                python=ROOT / "app/env/bin/python",
                manifest=ROOT / "tests/b19_t07_routes.json",
                include_b19_t06=True,
                fresh_only=True,
            )
            fixture_root = root / "fixture"
            commands = b19_t06_regression_commands(
                config, "http://127.0.0.1:41234/", fixture_root, root / "runtime"
            )
        self.assertEqual(
            tuple(command.name for command in commands),
            ("browser-acceptance", "viewport-integrity", "produce-nested-keyboard"),
        )
        flattened = "\n".join(" ".join(command.arguments) for command in commands)
        self.assertIn("b19_t06_browser_acceptance.py", flattened)
        self.assertIn("b19_t06_viewport_integrity.js", flattened)
        self.assertIn("b19_t06_produce_nested_keyboard.js", flattened)
        self.assertEqual(flattened.count("http://127.0.0.1:41234/"), 3)
        self.assertIn(str(fixture_root), flattened)

    def test_runner_consumes_the_234_case_contract_without_launching_it(self) -> None:
        plan = run_plan()
        self.assertEqual(plan.expected_case_count, 234)
        self.assertEqual(plan.probe_case.case_id, "baseline:project-home:390x844:default")

    def test_fixture_is_disposable_and_never_changes_protected_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = prepare_disposable_fixture(Path(temporary))
            self.assertTrue(fixture.protected_state_unchanged)
            self.assertEqual(fixture.hostile.aggregate_chunk_count, 5328)

    def test_missing_full_browser_ax_and_live_region_proof_is_red(self) -> None:
        result = missing_full_proof({"screenshot", "ax_tree", "focus_trace", "console_network_log", "identity"})
        self.assertEqual(result.status, "RED")
        self.assertEqual(result.missing, ("live_region",))
        self.assertNotIn("voiceover_action_log", REQUIRED_PROOF_KINDS)

    def test_complete_browser_only_proof_passes(self) -> None:
        self.assertEqual(missing_full_proof(set(REQUIRED_PROOF_KINDS)).status, "PASS")

    def test_server_environment_is_disposable_and_credential_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {"B19_TEST_API_KEY": "must-not-escape", "B19_TEST_VISIBLE": "yes"},
            clear=False,
        ):
            root = Path(temporary)
            environment = isolated_server_environment(root)
            self.assertNotIn("B19_TEST_API_KEY", environment)
            self.assertEqual(environment["B19_TEST_VISIBLE"], "yes")
            self.assertEqual(
                environment["ALEXANDRIA_DATA_ROOT"],
                str((root / "application-data").resolve()),
            )
            self.assertEqual(
                environment["ALEXANDRIA_LEGACY_ROOT_DIR"],
                str(root.resolve()),
            )

    def test_fixture_has_exact_synthetic_current_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_fixture(ROOT, root)
            write_accessibility_fixture_data(root)
            chunks = json.loads((root / "chunks.json").read_text(encoding="utf-8"))
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            chunk = chunks[0]
            self.assertEqual(state["project_id"], "fixture-project")
            self.assertEqual(chunk["id"], 0)
            audio_path = root / chunk["audio_path"]
            self.assertTrue(audio_path.is_file())
            self.assertEqual(chunk["status"], "done")
            self.assertEqual(chunk["audio_state"], "current")
            manager = ProjectManager(
                str(root),
                config_path=str(root / "app" / "config.json"),
            )
            voice_config = json.loads(
                (root / "voice_config.json").read_text(encoding="utf-8")
            )
            expected = manager._audio_binding(
                chunk,
                voice_config,
                resolved_speaker="BERNICE",
            )
            inspection = inspect_chunk_audio(
                root_dir=root,
                chunk=chunk,
                expected_fingerprint=expected,
            )
            self.assertEqual(inspection["state"], "current")
            self.assertTrue(inspection["ready"])
            self.assertEqual(len(chunks), 2)
            self.assertEqual(chunks[1]["id"], 1)
            self.assertEqual(chunks[1]["status"], "pending")
            self.assertIsNone(chunks[1]["audio_path"])
