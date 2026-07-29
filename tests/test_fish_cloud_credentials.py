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

    def test_macos_replacement_uses_keychain_without_exposing_key(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append({"command": command, **kwargs})
            return Completed(returncode=0)

        with patch.object(credentials, "_keychain_available", return_value=True):
            status = credentials.replace_fish_api_key(
                "keychain-secret",
                runner=runner,
            )
        self.assertTrue(status.persistent)
        self.assertEqual(status.source, "keychain")
        self.assertIn("add-generic-password", calls[0]["command"])
        self.assertNotIn("keychain-secret", calls[0]["command"])
        self.assertEqual(calls[0]["input"], "keychain-secret\n")

    def test_keychain_read_failure_is_non_fatal(self) -> None:
        def runner(*_args, **_kwargs):
            raise ValueError("broken subprocess")

        with patch.object(credentials, "_keychain_available", return_value=True):
            self.assertEqual(credentials.get_fish_api_key(runner=runner), "")
            status = credentials.fish_credential_status(runner=runner)
        self.assertFalse(status.configured)
        self.assertEqual(status.source, "none")

    def test_invalid_key_and_mode_fail_closed(self) -> None:
        with self.assertRaises(credentials.FishCredentialError):
            credentials.replace_fish_api_key("\n")
        with self.assertRaises(credentials.FishCredentialError):
            credentials.apply_fish_api_key_update("unknown", "key")

    def test_clear_is_idempotent_when_keychain_item_is_missing(self) -> None:
        def runner(_command, **_kwargs):
            return Completed(returncode=44)

        with patch.object(credentials, "_keychain_available", return_value=True):
            status = credentials.clear_fish_api_key(runner=runner)
        self.assertFalse(status.configured)


if __name__ == "__main__":
    unittest.main()
