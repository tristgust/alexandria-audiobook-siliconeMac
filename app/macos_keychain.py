from __future__ import annotations

import ctypes
import platform
import threading
from dataclasses import dataclass
from typing import Final


SECURITY_FRAMEWORK: Final = (
    "/System/Library/Frameworks/Security.framework/Security"
)
CORE_FOUNDATION_FRAMEWORK: Final = (
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)
ERR_SEC_SUCCESS: Final = 0
ERR_SEC_DUPLICATE_ITEM: Final = -25299
ERR_SEC_ITEM_NOT_FOUND: Final = -25300


class MacOSKeychainError(RuntimeError):
    def __init__(self, operation: str, status: int):
        super().__init__(f"macOS Keychain {operation} failed with status {status}.")
        self.operation = operation
        self.status = status


@dataclass(frozen=True)
class _Frameworks:
    security: ctypes.CDLL
    core_foundation: ctypes.CDLL


_LOCK = threading.RLock()
_FRAMEWORKS: _Frameworks | None = None


def available() -> bool:
    return platform.system() == "Darwin"


def _load_frameworks() -> _Frameworks:
    global _FRAMEWORKS
    with _LOCK:
        if _FRAMEWORKS is not None:
            return _FRAMEWORKS
        if not available():
            raise MacOSKeychainError("availability check", -1)

        security = ctypes.CDLL(SECURITY_FRAMEWORK)
        core_foundation = ctypes.CDLL(CORE_FOUNDATION_FRAMEWORK)

        security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        security.SecKeychainFindGenericPassword.restype = ctypes.c_int32

        security.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        security.SecKeychainAddGenericPassword.restype = ctypes.c_int32

        security.SecKeychainItemModifyAttributesAndData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32

        security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
        security.SecKeychainItemDelete.restype = ctypes.c_int32

        security.SecKeychainItemFreeContent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        security.SecKeychainItemFreeContent.restype = ctypes.c_int32

        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        core_foundation.CFRelease.restype = None

        _FRAMEWORKS = _Frameworks(
            security=security,
            core_foundation=core_foundation,
        )
        return _FRAMEWORKS


def _encoded(value: str) -> tuple[bytes, ctypes.c_char_p]:
    raw = value.encode("utf-8")
    return raw, ctypes.c_char_p(raw)


def _find_item(
    service: str,
    account: str,
    *,
    include_password: bool,
) -> tuple[int, ctypes.c_void_p, bytes | None]:
    frameworks = _load_frameworks()
    service_raw, service_pointer = _encoded(service)
    account_raw, account_pointer = _encoded(account)
    item = ctypes.c_void_p()

    if not include_password:
        status = frameworks.security.SecKeychainFindGenericPassword(
            None,
            len(service_raw),
            service_pointer,
            len(account_raw),
            account_pointer,
            None,
            None,
            ctypes.byref(item),
        )
        return int(status), item, None

    password_length = ctypes.c_uint32()
    password_data = ctypes.c_void_p()
    status = frameworks.security.SecKeychainFindGenericPassword(
        None,
        len(service_raw),
        service_pointer,
        len(account_raw),
        account_pointer,
        ctypes.byref(password_length),
        ctypes.byref(password_data),
        ctypes.byref(item),
    )
    if status != ERR_SEC_SUCCESS:
        return int(status), item, None

    try:
        password = ctypes.string_at(password_data, password_length.value)
    finally:
        frameworks.security.SecKeychainItemFreeContent(None, password_data)
    return int(status), item, password


def _release(item: ctypes.c_void_p) -> None:
    if item and item.value:
        _load_frameworks().core_foundation.CFRelease(item)


def read_generic_password(service: str, account: str) -> str | None:
    status, item, password = _find_item(
        service,
        account,
        include_password=True,
    )
    try:
        if status == ERR_SEC_ITEM_NOT_FOUND:
            return None
        if status != ERR_SEC_SUCCESS:
            raise MacOSKeychainError("read", status)
        return (password or b"").decode("utf-8")
    finally:
        _release(item)


def write_generic_password(service: str, account: str, password: str) -> None:
    frameworks = _load_frameworks()
    password_raw, password_pointer = _encoded(password)
    status, item, _ = _find_item(
        service,
        account,
        include_password=False,
    )
    try:
        if status == ERR_SEC_SUCCESS:
            modified = frameworks.security.SecKeychainItemModifyAttributesAndData(
                item,
                None,
                len(password_raw),
                password_pointer,
            )
            if modified != ERR_SEC_SUCCESS:
                raise MacOSKeychainError("update", int(modified))
        elif status == ERR_SEC_ITEM_NOT_FOUND:
            service_raw, service_pointer = _encoded(service)
            account_raw, account_pointer = _encoded(account)
            added = frameworks.security.SecKeychainAddGenericPassword(
                None,
                len(service_raw),
                service_pointer,
                len(account_raw),
                account_pointer,
                len(password_raw),
                password_pointer,
                None,
            )
            if added == ERR_SEC_DUPLICATE_ITEM:
                write_generic_password(service, account, password)
                return
            if added != ERR_SEC_SUCCESS:
                raise MacOSKeychainError("create", int(added))
        else:
            raise MacOSKeychainError("find before write", status)
    finally:
        _release(item)

    saved = read_generic_password(service, account)
    if saved != password:
        raise MacOSKeychainError("read-back verification", -2)


def delete_generic_password(service: str, account: str) -> None:
    frameworks = _load_frameworks()
    status, item, _ = _find_item(
        service,
        account,
        include_password=False,
    )
    try:
        if status == ERR_SEC_ITEM_NOT_FOUND:
            return
        if status != ERR_SEC_SUCCESS:
            raise MacOSKeychainError("find before delete", status)
        deleted = frameworks.security.SecKeychainItemDelete(item)
        if deleted not in {ERR_SEC_SUCCESS, ERR_SEC_ITEM_NOT_FOUND}:
            raise MacOSKeychainError("delete", int(deleted))
    finally:
        _release(item)
