#!/usr/bin/env python3
from __future__ import annotations

import argparse
import mimetypes
import os
import re
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeRequestHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args) -> None:
        return

    def translate_path(self, path: str) -> str:
        root = Path(self.directory or os.getcwd()).resolve()
        clean = unquote(urlsplit(path).path).lstrip("/")
        target = (root / clean).resolve()
        if target != root and root not in target.parents:
            return str(root / "__forbidden__")
        return str(target)

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            index = path / "index.html"
            if index.is_file():
                path = index
            else:
                return self.list_directory(str(path))
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        range_header = self.headers.get("Range")
        start = 0
        end = size - 1
        partial = False
        if range_header:
            match = RANGE_PATTERN.match(range_header.strip())
            if not match:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return None
            left, right = match.groups()
            if left:
                start = int(left)
                end = int(right) if right else end
            elif right:
                suffix = int(right)
                start = max(0, size - suffix)
            else:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return None
            if start >= size or start < 0 or end < start:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            end = min(end, size - 1)
            partial = True

        file = path.open("rb")
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Last-Modified", self.date_time_string(path.stat().st_mtime))
        self.end_headers()
        file.seek(start)
        self._range_remaining = end - start + 1
        return file

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        try:
            while remaining > 0:
                block = source.read(min(64 * 1024, remaining))
                if not block:
                    break
                try:
                    outputfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(block)
        finally:
            self._range_remaining = None


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        error = sys.exception()
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a review package with HTTP byte-range support.")
    parser.add_argument("--directory", default=".")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8782)
    args = parser.parse_args()
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Review directory does not exist: {root}")
    handler = lambda *values, **keywords: RangeRequestHandler(*values, directory=str(root), **keywords)
    server = QuietThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {root} at http://{args.bind}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
