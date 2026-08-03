from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app/app.py"
CORE = ROOT / "app/automation_api.py"
DOC = ROOT / "docs/automation-api.md"


class AutomationInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = APP.read_text(encoding="utf-8")
        cls.core = CORE.read_text(encoding="utf-8")
        cls.doc = DOC.read_text(encoding="utf-8")

    def test_rest_surface_is_narrow_and_mcp_remains_deferred(self) -> None:
        required = (
            '/api/automation/capabilities',
            '/api/automation/state',
            '/api/automation/blockers',
            '/api/automation/work',
            '/api/automation/tasks/export/review',
            '/api/automation/tasks/import/review',
            '/api/automation/operations/produce/review',
            '/api/automation/operations/export/review',
        )
        for route in required:
            self.assertIn(route, self.app)
        for forbidden in (
            '/api/automation/shell',
            '/api/automation/exec',
            '/api/automation/files',
            '/api/automation/fetch',
            '/api/automation/mcp',
        ):
            self.assertNotIn(forbidden, self.app)
        self.assertIn('"enabled": False', self.app)
        self.assertIn('deferred_no_rest_capability_gap', self.app)

    def test_security_contract_rejects_browser_query_forwarded_and_remote_access(self) -> None:
        for contract in (
            'automation_loopback_required',
            'automation_host_rejected',
            'automation_browser_origin_rejected',
            'automation_forwarded_request_rejected',
            'automation_storage_inside_project',
            'Authorization: Bearer',
            'X-Alexandria-Review-Token',
            'Idempotency-Key',
        ):
            self.assertIn(contract, self.core + self.doc + self.app)
        self.assertNotRegex(
            self.app,
            r'Query\([^\n]*(token|api_key|authorization)',
        )
        self.assertNotIn('localStorage', self.core)
        self.assertNotIn('sessionStorage', self.core)

    def test_credentials_reviews_and_idempotency_are_private_and_secret_redacted(self) -> None:
        for marker in (
            '0o600',
            '0o700',
            'secrets.compare_digest',
            'token_fingerprint',
            'automation_review_replay_rejected',
            'automation_idempotency_replay_rejected',
            'AUTH_FAILURE_WINDOW_SECONDS',
            'fcntl.flock',
        ):
            self.assertIn(marker, self.core)
        self.assertIn('"secret_output": False', self.core)
        self.assertNotIn('"token": created["token"]', self.core)
        self.assertIn('project_name', self.app)
        self.assertIn('filesystem_paths', self.app)
        self.assertIn('task_payloads', self.app)

    def test_native_authorities_and_task_bundle_priority_are_explicit(self) -> None:
        for native in (
            '_current_produce_plan',
            '_execute_produce_plan',
            '_current_export_plan',
            'execute_export_plan',
            'create_stored_task_bundle',
            'inspect_completed_task_upload_payload',
            'import_completed_task',
            'cancel_background_work_job',
        ):
            self.assertIn(native, self.app)
        self.assertIn('Task Bundles remain the primary', self.doc)
        self.assertIn('portable ChatGPT workflow', self.doc)
        self.assertIn('project_mutated": False', self.app)

    def test_core_and_routes_compile(self) -> None:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "py_compile",
                str(APP),
                str(CORE),
                str(ROOT / "app/external_workflows.py"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
