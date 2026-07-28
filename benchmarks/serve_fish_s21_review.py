#!/usr/bin/env python3
"""Serve the Fish S2.1 blind review with HTTP byte-range support.

Python's stock ``python -m http.server`` does not honor Range requests on the
versions used by this project. Firefox can leave later WAV controls waiting when
many players share that server. This server returns proper 206 responses and
keeps the review bound to localhost by default.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO

from fish_s21_blind_contract import DEFAULT_OUTPUT_ROOT

DEFAULT_REVIEW_ROOT = DEFAULT_OUTPUT_ROOT / "review"
_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
_COPY_CHUNK_BYTES = 1024 * 1024


class ReviewServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """Static-file handler with one-range byte serving and no cache persistence."""

    server_version = "AlexandriaFishReview/1.0"

    def __init__(self, *args: object, directory: str | None = None, **kwargs: object) -> None:
        self._byte_range: tuple[int, int] | None = None
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _parse_range(self, value: str, size: int) -> tuple[int, int] | None:
        match = _RANGE_PATTERN.fullmatch(value.strip())
        if not match:
            return None
        start_text, end_text = match.groups()
        if not start_text and not end_text:
            return None
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        else:
            suffix = int(end_text)
            if suffix <= 0:
                return None
            start = max(0, size - suffix)
            end = size - 1
        if start >= size or start < 0 or end < start:
            return None
        return start, min(end, size - 1)

    def _range_not_satisfiable(self, size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_head(self) -> BinaryIO | None:
        self._byte_range = None
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            source = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        try:
            info = os.fstat(source.fileno())
            size = int(info.st_size)
            range_header = self.headers.get("Range")
            selected = None
            if range_header:
                selected = self._parse_range(range_header, size)
                if selected is None:
                    source.close()
                    self._range_not_satisfiable(size)
                    return None
            self.send_response(HTTPStatus.PARTIAL_CONTENT if selected else HTTPStatus.OK)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Accept-Ranges", "bytes")
            if selected:
                start, end = selected
                self._byte_range = selected
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
            else:
                self.send_header("Content-Length", str(size))
            self.send_header("Last-Modified", self.date_time_string(info.st_mtime))
            self.end_headers()
            return source
        except Exception:
            source.close()
            raise

    def copyfile(self, source: BinaryIO, outputfile: BinaryIO) -> None:
        if self._byte_range is None:
            shutil.copyfileobj(source, outputfile, length=_COPY_CHUNK_BYTES)
            return
        start, end = self._byte_range
        source.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(_COPY_CHUNK_BYTES, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def build_server(review_root: Path, bind: str, port: int) -> ReviewServer:
    resolved = review_root.expanduser().resolve()
    required = (resolved / "index.html",)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Review package is incomplete: " + ", ".join(missing))
    handler = partial(RangeRequestHandler, directory=str(resolved))
    return ReviewServer((bind, port), handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-root", default=str(DEFAULT_REVIEW_ROOT))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--reviewer", default="tristan")
    args = parser.parse_args()

    review_root = Path(args.review_root).expanduser().resolve()
    server = build_server(review_root, args.bind, args.port)
    host, port = server.server_address[:2]
    print(f"Serving {review_root}")
    print(f"Open http://{host}:{port}/?reviewer={args.reviewer}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nReview server stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
