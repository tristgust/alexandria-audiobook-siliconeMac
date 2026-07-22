"""Historical tests for the closed Codex app-server research spike.

These tests preserve the verified protocol evidence only. They are not an
active Alexandria release gate and should be excluded or discarded with the
prototype module during final clean-commit/release cleanup unless the user
explicitly reopens the direction.
"""

from __future__ import annotations

import json
import tempfile
import unittest

from pathlib import Path
from typing import Any, Mapping

from codex_app_server import (
    CodexAppServerClient,
    CodexAppServerProtocolError,
    CodexAppServerRateLimited,
    CodexAppServerToolAttempt,
)


class FakeTransport:
    def __init__(
        self,
        *,
        responses: Mapping[str, list[Any] | Any] | None = None,
        notifications: list[dict[str, Any]] | None = None,
    ) -> None:
        self.responses = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in (responses or {}).items()
        }
        self.notifications = list(notifications or [])
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.notices: list[tuple[str, dict[str, Any]]] = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        del timeout
        normalized = dict(params or {})
        self.requests.append((method, normalized))
        values = self.responses.get(method)
        if not values:
            raise AssertionError(f"Unexpected request: {method}")
        return values.pop(0)

    def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.notices.append((method, dict(params or {})))

    def next_notification(
        self,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        del timeout
        if not self.notifications:
            raise AssertionError("No fake notification remains")
        return self.notifications.pop(0)

    def close(self) -> None:
        self.closed = True


def initialized_responses(
    *,
    rate_limit_reached: bool = False,
) -> dict[str, list[Any] | Any]:
    return {
        "initialize": {
            "userAgent": "alexandria_audiobook/0.1.0",
            "codexHome": "/tmp/codex-home",
            "platformFamily": "unix",
            "platformOs": "macos",
        },
        "account/read": {
            "account": {
                "type": "chatgpt",
                "email": "private@example.com",
                "planType": "plus",
            },
            "requiresOpenaiAuth": True,
        },
        "account/rateLimits/read": {
            "rateLimits": {
                "primary": {
                    "usedPercent": 100 if rate_limit_reached else 10,
                    "windowDurationMins": 10080,
                    "resetsAt": 1785024631,
                },
                "secondary": None,
                "rateLimitReachedType": (
                    "rate_limit_reached"
                    if rate_limit_reached
                    else None
                ),
            }
        },
    }


class CodexAccountStatusTests(unittest.TestCase):
    def test_account_status_omits_identity_and_exposes_limits(self) -> None:
        transport = FakeTransport(
            responses=initialized_responses(rate_limit_reached=True)
        )
        client = CodexAppServerClient(transport=transport)

        status = client.account_status()
        payload = status.to_dict()

        self.assertTrue(status.authenticated)
        self.assertEqual(status.auth_mode, "chatgpt")
        self.assertEqual(status.plan_type, "plus")
        self.assertTrue(status.email_present)
        self.assertTrue(status.rate_limit_reached)
        self.assertEqual(
            status.rate_limit_reached_type,
            "rate_limit_reached",
        )
        self.assertNotIn("email", payload)
        self.assertNotIn("private@example.com", json.dumps(payload))
        self.assertEqual(
            transport.notices,
            [("initialized", {})],
        )

    def test_initialize_identifies_alexandria_and_opts_into_schema(self) -> None:
        transport = FakeTransport(
            responses={
                "initialize": {
                    "userAgent": "test",
                }
            }
        )
        client = CodexAppServerClient(
            transport=transport,
            client_version="2.3.4",
        )

        client.initialize()

        method, params = transport.requests[0]
        self.assertEqual(method, "initialize")
        self.assertEqual(
            params["clientInfo"],
            {
                "name": "alexandria_audiobook",
                "title": "Alexandria Audiobook Generator",
                "version": "2.3.4",
            },
        )
        self.assertTrue(params["capabilities"]["experimentalApi"])


class CodexThreadTests(unittest.TestCase):
    def test_ephemeral_thread_is_read_only_and_never_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                responses={
                    "initialize": {"userAgent": "test"},
                    "thread/start": {
                        "thread": {
                            "id": "thread-1",
                            "ephemeral": True,
                            "path": None,
                        }
                    },
                }
            )
            client = CodexAppServerClient(transport=transport)

            thread_id = client.start_ephemeral_thread(
                isolated_working_directory=directory,
            )

        self.assertEqual(thread_id, "thread-1")
        _, params = transport.requests[-1]
        self.assertTrue(params["ephemeral"])
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(params["permissions"], ":read-only")
        self.assertEqual(params["cwd"], str(Path(directory).resolve()))

    def test_persisted_thread_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                responses={
                    "initialize": {"userAgent": "test"},
                    "thread/start": {
                        "thread": {
                            "id": "thread-1",
                            "ephemeral": False,
                            "path": "/tmp/session.jsonl",
                        }
                    },
                }
            )
            client = CodexAppServerClient(transport=transport)

            with self.assertRaises(CodexAppServerProtocolError):
                client.start_ephemeral_thread(
                    isolated_working_directory=directory,
                )

    def test_structured_turn_carries_native_schema_and_no_tool_policy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = CodexAppServerClient(
                transport=FakeTransport()
            )
            params = client.build_structured_turn_params(
                thread_id="thread-1",
                messages=[
                    {
                        "role": "system",
                        "content": "Preserve source wording.",
                    },
                    {
                        "role": "user",
                        "content": "Create a voice persona.",
                    },
                ],
                contract="persona",
                isolated_working_directory=directory,
                effort="high",
            )

        prompt = params["input"][0]["text"]
        self.assertIn("[SYSTEM MESSAGE 1]", prompt)
        self.assertIn("[USER MESSAGE 2]", prompt)
        self.assertIn("Do not run commands", prompt)
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(params["permissions"], ":read-only")
        self.assertEqual(params["outputSchema"]["type"], "object")
        self.assertEqual(params["effort"], "high")

    def test_rate_limit_blocks_before_turn_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                responses=initialized_responses(rate_limit_reached=True)
            )
            client = CodexAppServerClient(transport=transport)

            with self.assertRaises(CodexAppServerRateLimited):
                client.start_structured_turn(
                    thread_id="thread-1",
                    messages=[
                        {
                            "role": "user",
                            "content": "Create a persona.",
                        }
                    ],
                    contract="persona",
                    isolated_working_directory=directory,
                )

        self.assertNotIn(
            "turn/start",
            [method for method, _ in transport.requests],
        )


class CodexStructuredResultTests(unittest.TestCase):
    def test_completed_persona_is_natively_validated(self) -> None:
        transport = FakeTransport(
            responses={
                "turn/interrupt": {},
            },
            notifications=[
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "id": "item-1",
                            "text": json.dumps(
                                {
                                    "description": (
                                        "A mature British baritone."
                                    ),
                                    "ref_text": (
                                        "The matter is not settled."
                                    ),
                                }
                            ),
                        }
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "id": "turn-1",
                            "status": "completed",
                            "error": None,
                        }
                    },
                },
            ],
        )
        client = CodexAppServerClient(transport=transport)

        result = client.collect_structured_turn(
            thread_id="thread-1",
            turn_id="turn-1",
            contract="persona",
        )

        self.assertEqual(result.backend, "codex-app-server")
        self.assertEqual(
            result.data["ref_text"],
            "The matter is not settled.",
        )
        self.assertTrue(result.metrics["native_schema_validation"])
        self.assertFalse(result.metrics["tool_attempted"])

    def test_tool_item_interrupts_and_fails_closed(self) -> None:
        transport = FakeTransport(
            responses={
                "turn/interrupt": {},
            },
            notifications=[
                {
                    "method": "item/started",
                    "params": {
                        "item": {
                            "type": "commandExecution",
                            "id": "tool-1",
                            "command": "cat source.txt",
                        }
                    },
                }
            ],
        )
        client = CodexAppServerClient(transport=transport)

        with self.assertRaises(CodexAppServerToolAttempt):
            client.collect_structured_turn(
                thread_id="thread-1",
                turn_id="turn-1",
                contract="persona",
            )

        self.assertEqual(
            transport.requests[-1],
            (
                "turn/interrupt",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                },
            ),
        )

    def test_failed_turn_reports_backend_error(self) -> None:
        transport = FakeTransport(
            notifications=[
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "id": "turn-1",
                            "status": "failed",
                            "error": {
                                "message": "usage limit reached",
                            },
                        }
                    },
                }
            ]
        )
        client = CodexAppServerClient(transport=transport)

        with self.assertRaisesRegex(
            CodexAppServerProtocolError,
            "usage limit reached",
        ):
            client.collect_structured_turn(
                thread_id="thread-1",
                turn_id="turn-1",
                contract="persona",
            )

    def test_fenced_json_is_tolerated_before_native_validation(self) -> None:
        transport = FakeTransport(
            notifications=[
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "id": "item-1",
                            "text": (
                                "```json\n"
                                '{"description":"Voice","ref_text":"Hi."}'
                                "\n```"
                            ),
                        }
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "id": "turn-1",
                            "status": {"type": "completed"},
                        }
                    },
                },
            ]
        )
        client = CodexAppServerClient(transport=transport)

        result = client.collect_structured_turn(
            thread_id="thread-1",
            turn_id="turn-1",
            contract="persona",
        )

        self.assertEqual(result.data["description"], "Voice")

    def test_complete_json_runs_ephemeral_structured_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            responses = initialized_responses(rate_limit_reached=False)
            responses.update(
                {
                    "thread/start": {
                        "thread": {
                            "id": "thread-1",
                            "ephemeral": True,
                            "path": None,
                        }
                    },
                    "turn/start": {
                        "turn": {
                            "id": "turn-1",
                            "status": "inProgress",
                        }
                    },
                }
            )
            transport = FakeTransport(
                responses=responses,
                notifications=[
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "agentMessage",
                                "id": "item-1",
                                "text": (
                                    '{"description":"Voice",'
                                    '"ref_text":"Hello."}'
                                ),
                            }
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "turn": {
                                "id": "turn-1",
                                "status": "completed",
                            }
                        },
                    },
                ],
            )
            client = CodexAppServerClient(transport=transport)

            result = client.complete_json(
                messages=[
                    {
                        "role": "user",
                        "content": "Create a persona.",
                    }
                ],
                contract="persona",
                isolated_working_directory=directory,
            )

        self.assertEqual(result.data["ref_text"], "Hello.")
        self.assertEqual(
            [method for method, _ in transport.requests],
            [
                "initialize",
                "thread/start",
                "account/read",
                "account/rateLimits/read",
                "turn/start",
            ],
        )


if __name__ == "__main__":
    unittest.main()
