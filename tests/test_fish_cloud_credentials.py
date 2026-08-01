from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import fish_cloud_credentials as credentials


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FishCloudCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        credentials._SESSION_KEY = None

    def tearDown(self) -> None:
        credentials._SESSION_KEY = None

    def test_environment_key_has_priority_and_is_never_persistent(self) -> None:
        with patch.dict(os.environ, {"FISH_API_KEY": " environment-key "}, clear=False):
            status = credentials.fish_credential_status()
            self.assertEqual(credentials.get_fish_api_key(), "environment-key")
        self.assertTrue(status.configured)
        self.assertEqual(status.source, "environment")
        self.assertFalse(status.persistent)

    def test_non_macos_replacement_is_session_only(self) -> None:
        with patch.object(credentials, "_keychain_available", return_value=False):
            status = credentials.replace_fish_api_key("session-key")
            self.assertEqual(credentials.get_fish_api_key(), "session-key")
            cleared = credentials.clear_fish_api_key()
        self.assertEqual(status.source, "session")
        self.assertFalse(status.persistent)
        self.assertFalse(cleared.configured)

    def test_macos_replacement_uses_native_keychain_and_verifies_readback(self) -> None:
        saved = {}

        def write(service, account, value):
            saved[(service, account)] = value

        def read(service, account):
            return saved.get((service, account))

        with (
            patch.object(credentials, "_keychain_available", return_value=True),
            patch.object(credentials, "write_generic_password", side_effect=write) as writer,
            patch.object(credentials, "read_generic_password", side_effect=read),
        ):
            status = credentials.replace_fish_api_key("keychain-secret")
        self.assertTrue(status.persistent)
        self.assertEqual(status.source, "keychain")
        writer.assert_called_once()
        self.assertEqual(writer.call_args.args[2], "keychain-secret")

    def test_macos_replacement_fails_when_readback_does_not_match(self) -> None:
        with (
            patch.object(credentials, "_keychain_available", return_value=True),
            patch.object(credentials, "write_generic_password"),
            patch.object(credentials, "read_generic_password", return_value="wrong-key"),
        ):
            with self.assertRaisesRegex(
                credentials.FishCredentialError,
                "read-back verification",
            ):
                credentials.replace_fish_api_key("keychain-secret")

    def test_keychain_read_failure_is_non_fatal(self) -> None:
        with (
            patch.object(credentials, "_keychain_available", return_value=True),
            patch.object(
                credentials,
                "read_generic_password",
                side_effect=credentials.MacOSKeychainError("read", -1),
            ),
        ):
            self.assertEqual(credentials.get_fish_api_key(), "")
            status = credentials.fish_credential_status()
        self.assertFalse(status.configured)
        self.assertEqual(status.source, "none")

    def test_invalid_key_and_mode_fail_closed(self) -> None:
        with self.assertRaises(credentials.FishCredentialError):
            credentials.replace_fish_api_key("\n")
        with self.assertRaises(credentials.FishCredentialError):
            credentials.apply_fish_api_key_update("unknown", "key")

    def test_clear_is_idempotent_when_keychain_item_is_missing(self) -> None:
        with (
            patch.object(credentials, "_keychain_available", return_value=True),
            patch.object(credentials, "delete_generic_password") as delete,
        ):
            status = credentials.clear_fish_api_key()
        self.assertFalse(status.configured)
        delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
