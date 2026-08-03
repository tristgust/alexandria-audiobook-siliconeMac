from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from automation_api import (
    AutomationApiError,
    authorize_automation_request,
    cleanup_staged_files,
    consume_review_ticket,
    create_review_ticket,
    finish_consumed_operation,
    load_automation_credential,
    new_staging_path,
    provision_automation_credential,
    public_automation_credential,
    staged_file_record,
    verify_staged_file,
)


class AutomationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.credential_path = self.root / "credential.json"
        self.token = "t" * 64
        self.credential = provision_automation_credential(
            path=self.credential_path,
            token=self.token,
            credential_id="credential_fixture",
            scopes={
                "automation:discover",
                "state:read",
                "tasks:export",
                "tasks:import",
                "operations:produce",
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def authorize(self, *, scopes=(), **overrides):
        arguments = {
            "client_host": "127.0.0.1",
            "host_header": "127.0.0.1:4201",
            "origin_header": None,
            "authorization_header": f"Bearer {self.token}",
            "required_scopes": scopes,
            "credential_path": self.credential_path,
            "forwarded_headers_present": False,
        }
        arguments.update(overrides)
        return authorize_automation_request(**arguments)

    def test_provisioning_uses_private_permissions_and_never_exposes_token_publicly(self) -> None:
        mode = os.stat(self.credential_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        loaded, path = load_automation_credential(self.credential_path)
        self.assertEqual(path, self.credential_path.resolve())
        self.assertEqual(loaded["token"], self.token)
        public = public_automation_credential(self.credential_path)
        self.assertNotIn("token", public)
        self.assertEqual(public["browser_origin_policy"], "rejected")
        self.assertEqual(public["network_policy"], "loopback_only")

    def test_authorization_checks_loopback_host_origin_forwarding_auth_and_scope(self) -> None:
        principal = self.authorize(scopes={"state:read"})
        self.assertEqual(principal.credential_id, "credential_fixture")
        with self.assertRaisesRegex(AutomationApiError, "loopback") as remote:
            self.authorize(client_host="192.0.2.10")
        self.assertEqual(remote.exception.status_code, 403)
        with self.assertRaisesRegex(AutomationApiError, "Host") as host:
            self.authorize(host_header="example.com")
        self.assertEqual(host.exception.code, "automation_host_rejected")
        with self.assertRaisesRegex(AutomationApiError, "Browser-origin") as origin:
            self.authorize(origin_header="http://127.0.0.1:3000")
        self.assertEqual(origin.exception.code, "automation_browser_origin_rejected")
        with self.assertRaisesRegex(AutomationApiError, "forwarded") as forwarded:
            self.authorize(forwarded_headers_present=True)
        self.assertEqual(
            forwarded.exception.code,
            "automation_forwarded_request_rejected",
        )
        with self.assertRaisesRegex(AutomationApiError, "invalid") as invalid:
            self.authorize(authorization_header="Bearer wrong")
        self.assertEqual(invalid.exception.status_code, 401)
        with self.assertRaisesRegex(AutomationApiError, "required scope") as missing:
            self.authorize(scopes={"operations:export"})
        self.assertEqual(missing.exception.status_code, 403)

    def test_host_and_loopback_validation_precede_authentication(self) -> None:
        with self.assertRaises(AutomationApiError) as remote:
            self.authorize(
                client_host="198.51.100.7",
                authorization_header="Bearer wrong",
            )
        self.assertEqual(remote.exception.code, "automation_loopback_required")
        with self.assertRaises(AutomationApiError) as host:
            self.authorize(
                host_header="attacker.invalid",
                authorization_header="Bearer wrong",
            )
        self.assertEqual(host.exception.code, "automation_host_rejected")

    def test_review_ticket_is_body_bound_one_time_and_idempotency_rejects_replay(self) -> None:
        principal = self.authorize(scopes={"operations:produce"})
        request = {
            "mode": "selected",
            "selected_chunk_ids": ["chunk:7"],
            "plan_fingerprint": "a" * 64,
        }
        review = create_review_ticket(
            principal=principal,
            operation="produce_generation",
            required_scope="operations:produce",
            request_payload=request,
            reviewed_payload={"safe_to_execute": True, "indices": [7]},
        )
        changed = {**request, "selected_chunk_ids": ["chunk:8"]}
        with self.assertRaisesRegex(AutomationApiError, "changed after review"):
            consume_review_ticket(
                principal=principal,
                review_token=review["review_token"],
                idempotency_key="produce-generation-0001",
                operation="produce_generation",
                required_scope="operations:produce",
                request_payload=changed,
            )
        consumed = consume_review_ticket(
            principal=principal,
            review_token=review["review_token"],
            idempotency_key="produce-generation-0001",
            operation="produce_generation",
            required_scope="operations:produce",
            request_payload=request,
        )
        finish_consumed_operation(
            consumed,
            status="succeeded",
            result={"status": "accepted"},
        )
        with self.assertRaisesRegex(AutomationApiError, "already consumed"):
            consume_review_ticket(
                principal=principal,
                review_token=review["review_token"],
                idempotency_key="produce-generation-0002",
                operation="produce_generation",
                required_scope="operations:produce",
                request_payload=request,
            )
        second = create_review_ticket(
            principal=principal,
            operation="produce_generation",
            required_scope="operations:produce",
            request_payload=request,
            reviewed_payload={"safe_to_execute": True},
        )
        with self.assertRaisesRegex(AutomationApiError, "already used"):
            consume_review_ticket(
                principal=principal,
                review_token=second["review_token"],
                idempotency_key="produce-generation-0001",
                operation="produce_generation",
                required_scope="operations:produce",
                request_payload=request,
            )

    def test_review_token_is_bound_to_credential_and_operation_scope(self) -> None:
        principal = self.authorize(scopes={"tasks:export"})
        review = create_review_ticket(
            principal=principal,
            operation="task_bundle_export",
            required_scope="tasks:export",
            request_payload={"task_type": "script_review"},
            reviewed_payload={"task_type": "script_review"},
        )
        with self.assertRaisesRegex(AutomationApiError, "does not authorize"):
            consume_review_ticket(
                principal=principal,
                review_token=review["review_token"],
                idempotency_key="task-export-review-0001",
                operation="produce_generation",
                required_scope="tasks:export",
                request_payload={"task_type": "script_review"},
            )

    def test_staged_file_is_private_hash_bound_and_cleanup_is_exact(self) -> None:
        principal = self.authorize(scopes={"tasks:import"})
        staged = new_staging_path(principal=principal, suffix=".json")
        staged.write_bytes(b'{"schema_version":2}')
        staged.chmod(0o600)
        record = staged_file_record(staged, original_name="completed.json")
        self.assertEqual(verify_staged_file(record), staged)
        staged.write_bytes(b"changed")
        with self.assertRaisesRegex(AutomationApiError, "changed after review"):
            verify_staged_file(record)
        cleanup_staged_files([record])
        self.assertFalse(staged.exists())

    def test_concurrent_review_consumption_allows_exactly_one_winner(self) -> None:
        principal = self.authorize(scopes={"operations:produce"})
        payload = {"mode": "selected", "selected_chunk_ids": ["chunk:7"]}
        review = create_review_ticket(
            principal=principal,
            operation="produce_generation",
            required_scope="operations:produce",
            request_payload=payload,
            reviewed_payload={"safe_to_execute": True},
        )
        barrier = threading.Barrier(2)

        def consume(index: int) -> str:
            barrier.wait()
            try:
                consume_review_ticket(
                    principal=principal,
                    review_token=review["review_token"],
                    idempotency_key=f"concurrent-review-key-{index:04d}",
                    operation="produce_generation",
                    required_scope="operations:produce",
                    request_payload=payload,
                )
                return "accepted"
            except AutomationApiError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, (1, 2)))
        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(results.count("automation_review_replay_rejected"), 1)

    def test_authentication_failures_are_rate_limited_per_loopback_client(self) -> None:
        for _index in range(8):
            with self.assertRaises(AutomationApiError) as failure:
                authorize_automation_request(
                    client_host="127.0.0.23",
                    host_header="127.0.0.1",
                    origin_header=None,
                    authorization_header="Bearer invalid-token",
                    credential_path=self.credential_path,
                )
            self.assertEqual(
                failure.exception.code,
                "automation_authentication_invalid",
            )
        with self.assertRaises(AutomationApiError) as limited:
            authorize_automation_request(
                client_host="127.0.0.23",
                host_header="127.0.0.1",
                origin_header=None,
                authorization_header="Bearer invalid-token",
                credential_path=self.credential_path,
            )
        self.assertEqual(
            limited.exception.code,
            "automation_authentication_rate_limited",
        )
        self.assertEqual(limited.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
