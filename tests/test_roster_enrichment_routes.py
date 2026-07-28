from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class RosterEnrichmentRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.before = copy.deepcopy(app_module.process_state["roster_enrichment"])
        app_module.process_state["roster_enrichment"].update(
            {
                "running": False,
                "logs": [],
                "cancel": False,
                "stage": "idle",
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
        )
        app_module.process_state["persona"]["running"] = False
        app_module.process_state["visual"]["running"] = False

    def tearDown(self) -> None:
        app_module.process_state["roster_enrichment"].clear()
        app_module.process_state["roster_enrichment"].update(self.before)
        app_module.process_state["persona"]["running"] = False
        app_module.process_state["visual"]["running"] = False

    def plan(self) -> dict:
        return {
            "schema_version": 1,
            "candidate_id": "structured_fixture",
            "draft_fingerprint": "d" * 64,
            "relationships_included": True,
            "options": {
                "create_designed_voice_profiles": True,
                "discover_visual_details": True,
            },
            "state": "ready",
            "approved_roster_fingerprint": "a" * 64,
            "steps": {},
            "plan_fingerprint": "p" * 64,
        }

    def test_start_requires_current_plan_and_approved_roster(self) -> None:
        plan = self.plan()
        approved = {
            "roster_fingerprint": "a" * 64,
            "entries": [{"id": "character_one"}, {"id": "character_two"}],
        }
        source = {"path": "/tmp/source.txt", "fingerprint": "s" * 64}
        with (
            patch.object(app_module, "load_roster_enrichment_plan", return_value=plan),
            patch.object(
                app_module,
                "_current_approved_visual_context",
                return_value=(source, "Source.", approved, None),
            ),
            patch.object(
                app_module,
                "inspect_visual_discovery_state",
                return_value={
                    "status": "absent",
                    "exists": False,
                    "character_ids": [],
                },
            ),
            patch.object(app_module, "_run_roster_enrichment") as runner,
        ):
            response = self.client.post(
                "/api/character_roster/enrichment/start",
                json={
                    "expected_plan_fingerprint": "p" * 64,
                    "expected_roster_fingerprint": "a" * 64,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["relationships_included"])
        self.assertEqual(response.json()["entry_count"], 2)
        runner.assert_called_once()
        kwargs = runner.call_args.kwargs
        self.assertEqual(kwargs["entry_ids"], ["character_one", "character_two"])
        self.assertEqual(kwargs["approved_roster_fingerprint"], "a" * 64)

    def test_stale_plan_is_rejected_before_background_work(self) -> None:
        with patch.object(
            app_module,
            "load_roster_enrichment_plan",
            return_value=self.plan(),
        ):
            response = self.client.post(
                "/api/character_roster/enrichment/start",
                json={
                    "expected_plan_fingerprint": "wrong",
                    "expected_roster_fingerprint": "a" * 64,
                },
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "stale_roster_enrichment_plan",
        )

    def test_orchestrator_runs_missing_designed_voices_before_visual_dossiers(self) -> None:
        plan = self.plan()
        updates = []
        commands = []

        def record_process(command, stage):
            commands.append((stage, command))
            return 0

        with (
            patch.object(app_module, "update_roster_enrichment_plan", side_effect=lambda **kwargs: updates.append(kwargs["changes"]) or {**plan, **kwargs["changes"]}),
            patch.object(app_module, "run_process", side_effect=record_process),
            patch.object(app_module, "_append_process_log"),
            patch.object(app_module, "_reset_process_logs"),
        ):
            app_module._run_roster_enrichment(
                plan=plan,
                source_path="/tmp/source.txt",
                entry_ids=["character_one", "character_two"],
                approved_roster_fingerprint="a" * 64,
            )
        self.assertEqual([item[0] for item in commands], ["persona", "visual"])
        self.assertIn("--new-only", commands[0][1])
        self.assertIn("--advanced", commands[0][1])
        self.assertEqual(commands[1][1].count("--entry-id"), 2)
        self.assertEqual(updates[-1]["state"], "complete")


if __name__ == "__main__":
    unittest.main()
