from __future__ import annotations

import getpass
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

from macos_keychain import (
    MacOSKeychainError,
    available as macos_keychain_available,
    delete_generic_password,
    read_generic_password,
    write_generic_password,
)


FISH_KEYCHAIN_SERVICE = "com.alexandria.fish-audio"
FISH_KEY_ENVIRONMENT_NAMES = ("FISH_API_KEY", "FISH_AUDIO_API_KEY")
MAX_FISH_API_KEY_LENGTH = 1000


class FishCredentialError(RuntimeError):
    """Raised when a Fish credential cannot be validated or stored safely."""


@dataclass(frozen=True)
class FishCredentialStatus:
    configured: bool
    source: str
    persistent: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "source": self.source,
            "persistent": self.persistent,
        }


_LOCK = threading.RLock()
_SESSION_KEY: str | None = None


def _normalized_key(value: object) -> str:
    if not isinstance(value, str):
        raise FishCredentialError("Fish API key must be text.")
    key = value.strip()
    if not key:
        raise FishCredentialError("Fish API key must not be empty.")
    if len(key) > MAX_FISH_API_KEY_LENGTH:
        raise FishCredentialError(
            f"Fish API key exceeds {MAX_FISH_API_KEY_LENGTH} characters."
        )
    if any(character in key for character in ("\x00", "\r", "\n")):
        raise FishCredentialError("Fish API key contains unsupported characters.")
    return key


def _environment_key() -> str | None:
    for name in FISH_KEY_ENVIRONMENT_NAMES:
        value = os.environ.get(name)
        if value and value.strip():
            try:
                return _normalized_key(value)
            except FishCredentialError:
                return None
    return None


def _keychain_available() -> bool:
    return macos_keychain_available()


def _read_keychain(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    del runner  # Retained for backward-compatible callers and tests.
    if not _keychain_available():
        return None
    try:
        value = read_generic_password(
            FISH_KEYCHAIN_SERVICE,
            getpass.getuser(),
        )
    except (MacOSKeychainError, UnicodeDecodeError):
        # Credential discovery is a capability hint, not a reason for unrelated
        # Alexandria status or maintenance routes to fail.
        return None
    try:
        return _normalized_key(value) if value is not None else None
    except FishCredentialError:
        return None


def get_fish_api_key(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    environment = _environment_key()
    if environment:
        return environment
    with _LOCK:
        if _SESSION_KEY:
            return _SESSION_KEY
    keychain = _read_keychain(runner=runner)
    return keychain or ""


def fish_credential_status(
    *,
    check_keychain: bool = True,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> FishCredentialStatus:
    if _environment_key():
        return FishCredentialStatus(True, "environment", False)
    with _LOCK:
        if _SESSION_KEY:
            return FishCredentialStatus(True, "session", False)
    if check_keychain and _read_keychain(runner=runner):
        return FishCredentialStatus(True, "keychain", True)
    return FishCredentialStatus(False, "none", False)


def replace_fish_api_key(
    value: object,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> FishCredentialStatus:
    key = _normalized_key(value)
    if _keychain_available():
        del runner  # Native Keychain APIs avoid exposing the secret in argv.
        try:
            write_generic_password(
                FISH_KEYCHAIN_SERVICE,
                getpass.getuser(),
                key,
            )
            saved = _read_keychain()
        except MacOSKeychainError as exc:
            raise FishCredentialError(
                "Fish API key could not be saved in the macOS Keychain."
            ) from exc
        if saved != key:
            raise FishCredentialError(
                "Fish API key did not pass Keychain read-back verification."
            )
        with _LOCK:
            global _SESSION_KEY
            _SESSION_KEY = None
        return FishCredentialStatus(True, "keychain", True)
    with _LOCK:
        _SESSION_KEY = key
    return FishCredentialStatus(True, "session", False)


def clear_fish_api_key(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> FishCredentialStatus:
    with _LOCK:
        global _SESSION_KEY
        _SESSION_KEY = None
    if _keychain_available():
        del runner  # Native Keychain APIs avoid subprocess ambiguity.
        try:
            delete_generic_password(
                FISH_KEYCHAIN_SERVICE,
                getpass.getuser(),
            )
        except MacOSKeychainError as exc:
            raise FishCredentialError(
                "Fish API key could not be removed from the macOS Keychain."
            ) from exc
    return FishCredentialStatus(False, "none", False)


def apply_fish_api_key_update(
    mode: object,
    value: object = "",
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> FishCredentialStatus:
    normalized_mode = str(mode or "preserve").strip().casefold()
    if normalized_mode == "preserve":
        return fish_credential_status(runner=runner)
    if normalized_mode == "replace":
        return replace_fish_api_key(value, runner=runner)
    if normalized_mode == "clear":
        return clear_fish_api_key(runner=runner)
    raise FishCredentialError(
        "Fish API key mode must be preserve, replace, or clear."
    )
