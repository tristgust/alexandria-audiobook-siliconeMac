from __future__ import annotations

import unittest

from controlled_clone_approval import (
    CONTROLLED_CLONE_APPROVAL_TTL_SECONDS,
    ControlledCloneApprovalConflictError,
    clear_controlled_clone_approvals,
    confirm_controlled_clone_preview,
    consume_controlled_clone_approvals,
    register_controlled_clone_preview,
)


class ControlledCloneApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_controlled_clone_approvals()

    def tearDown(self) -> None:
        clear_controlled_clone_approvals()

    def register(self, **overrides):
        values = {
            "speaker": "DOCTOR",
            "preview_fingerprint": "p" * 64,
            "configuration_fingerprint": "c" * 64,
            "created_at_monotonic": 100.0,
        }
        values.update(overrides)
        return register_controlled_clone_preview(**values)

    def confirm(self, **overrides):
        values = {
            "speaker": "DOCTOR",
            "preview_fingerprint": "p" * 64,
            "configuration_fingerprint": "c" * 64,
            "confirmed_at_monotonic": 110.0,
        }
        values.update(overrides)
        return confirm_controlled_clone_preview(**values)

    def test_register_confirm_and_one_time_consume(self) -> None:
        registration = self.register()
        self.assertEqual(registration["speaker"], "DOCTOR")
        confirmation = self.confirm()
        token = confirmation["approval_token"]
        consume_controlled_clone_approvals(
            [
                {
                    "speaker": "DOCTOR",
                    "approval_token": token,
                    "configuration_fingerprint": "c" * 64,
                }
            ],
            consumed_at_monotonic=120.0,
        )
        with self.assertRaisesRegex(
            ControlledCloneApprovalConflictError,
            "Generate, listen through",
        ):
            consume_controlled_clone_approvals(
                [
                    {
                        "speaker": "DOCTOR",
                        "approval_token": token,
                        "configuration_fingerprint": "c" * 64,
                    }
                ],
                consumed_at_monotonic=121.0,
            )

    def test_confirmation_is_bound_to_speaker_and_configuration(self) -> None:
        self.register()
        with self.assertRaisesRegex(
            ControlledCloneApprovalConflictError,
            "different speaker",
        ):
            self.confirm(speaker="MASTER")

        clear_controlled_clone_approvals()
        self.register()
        with self.assertRaisesRegex(
            ControlledCloneApprovalConflictError,
            "settings changed",
        ):
            self.confirm(configuration_fingerprint="d" * 64)

    def test_consume_validates_entire_batch_before_removing_tokens(self) -> None:
        self.register(
            preview_fingerprint="a" * 64,
            configuration_fingerprint="1" * 64,
        )
        first = self.confirm(
            preview_fingerprint="a" * 64,
            configuration_fingerprint="1" * 64,
        )
        self.register(
            preview_fingerprint="b" * 64,
            configuration_fingerprint="2" * 64,
        )
        second = self.confirm(
            preview_fingerprint="b" * 64,
            configuration_fingerprint="2" * 64,
        )

        with self.assertRaisesRegex(
            ControlledCloneApprovalConflictError,
            "identity or settings changed",
        ):
            consume_controlled_clone_approvals(
                [
                    {
                        "speaker": "DOCTOR",
                        "approval_token": first["approval_token"],
                        "configuration_fingerprint": "1" * 64,
                    },
                    {
                        "speaker": "DOCTOR",
                        "approval_token": second["approval_token"],
                        "configuration_fingerprint": "9" * 64,
                    },
                ],
                consumed_at_monotonic=120.0,
            )

        consume_controlled_clone_approvals(
            [
                {
                    "speaker": "DOCTOR",
                    "approval_token": first["approval_token"],
                    "configuration_fingerprint": "1" * 64,
                }
            ],
            consumed_at_monotonic=121.0,
        )

    def test_pending_and_confirmed_receipts_expire(self) -> None:
        self.register(created_at_monotonic=0.0)
        with self.assertRaisesRegex(
            ControlledCloneApprovalConflictError,
            "Generate a new",
        ):
            self.confirm(
                confirmed_at_monotonic=(
                    CONTROLLED_CLONE_APPROVAL_TTL_SECONDS + 1.0
                )
            )

        self.register(created_at_monotonic=0.0)
        confirmation = self.confirm(confirmed_at_monotonic=1.0)
        with self.assertRaisesRegex(
            ControlledCloneApprovalConflictError,
            "Generate, listen through",
        ):
            consume_controlled_clone_approvals(
                [
                    {
                        "speaker": "DOCTOR",
                        "approval_token": confirmation["approval_token"],
                        "configuration_fingerprint": "c" * 64,
                    }
                ],
                consumed_at_monotonic=(
                    CONTROLLED_CLONE_APPROVAL_TTL_SECONDS + 2.0
                ),
            )


if __name__ == "__main__":
    unittest.main()
