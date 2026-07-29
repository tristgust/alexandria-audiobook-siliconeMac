from __future__ import annotations

import getpass
import os
import platform
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable


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
    return platform.system() == "Darwin" and os.path.isfile("/usr/bin/security")


def _run_security(
    arguments: list[str],
    *,
    input_text: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        ["/usr/bin/security", *arguments],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _read_keychain(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    if not _keychain_available():
        return None
    try:
        completed = _run_security(
            [
                "find-generic-password",
                "-s",
                FISH_KEYCHAIN_SERVICE,
                "-a",
                getpass.getuser(),
                "-w",
            ],
            runner=runner,
        )
    except Exception:
        # Credential discovery is a capability hint, not a reason for unrelated
        # Alexandria status or maintenance routes to fail.
        return None
    if completed.returncode != 0:
        return None
    try:
        return _normalized_key(completed.stdout)
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
        completed = _run_security(
            [
                "add-generic-password",
                "-U",
                "-s",
                FISH_KEYCHAIN_SERVICE,
                "-a",
                getpass.getuser(),
                # macOS security documents passing a value to -w as insecure.
                # Leaving -w last prompts on stdin and keeps the secret out of
                # the process argument list.
                "-w",
            ],
            input_text=f"{key}\n",
            runner=runner,
        )
        if completed.returncode != 0:
            raise FishCredentialError(
                "Fish API key could not be saved in the macOS Keychain."
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
        completed = _run_security(
            [
                "delete-generic-password",
                "-s",
                FISH_KEYCHAIN_SERVICE,
                "-a",
                getpass.getuser(),
            ],
            runner=runner,
        )
        # Security returns 44 when the item does not exist. Clearing an already
        # empty keychain entry is still a successful, idempotent operation.
        if completed.returncode not in {0, 44}:
            raise FishCredentialError(
                "Fish API key could not be removed from the macOS Keychain."
            )
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
