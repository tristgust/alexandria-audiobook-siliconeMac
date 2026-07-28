from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import BinaryIO


class QVoiceBinaryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class QVoiceReader:
    """Consume an untrusted little-endian QVCE stream with named boundaries."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle

    def exact(self, size: int, label: str) -> bytes:
        value = self._handle.read(size)
        if len(value) != size:
            raise QVoiceBinaryError(
                "qvoice_truncated",
                f"The .qvoice ended while reading {label}.",
            )
        return value

    def u32(self, label: str) -> int:
        return struct.unpack("<I", self.exact(4, label))[0]

    def f32_values(self, count: int, label: str) -> tuple[float, ...]:
        return struct.unpack(f"<{count}f", self.exact(count * 4, label))

    def i32_values(self, count: int, label: str) -> tuple[int, ...]:
        return struct.unpack(f"<{count}i", self.exact(count * 4, label))

    def marker(self) -> bytes | None:
        value = self._handle.read(4)
        if not value:
            return None
        if len(value) != 4:
            raise QVoiceBinaryError(
                "qvoice_truncated",
                "The .qvoice ended while reading a section marker.",
            )
        return value

    def skip(self, size: int, label: str) -> None:
        start = self._handle.tell()
        self._handle.seek(0, 2)
        end = self._handle.tell()
        if start + size > end:
            raise QVoiceBinaryError(
                "qvoice_truncated",
                f"The .qvoice ended while reading {label}.",
            )
        self._handle.seek(start + size)

    def tell(self) -> int:
        return self._handle.tell()


def fixed_text(value: bytes, label: str) -> str | None:
    raw = value.split(b"\x00", 1)[0]
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QVoiceBinaryError(
            "qvoice_invalid_text",
            f"The .qvoice {label} is not valid UTF-8.",
        ) from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
