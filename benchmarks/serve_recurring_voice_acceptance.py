#!/usr/bin/env python3
"""Serve a recurring-Voice acceptance pack with HTTP byte ranges."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import shutil


_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class RangeRequestHandler(SimpleHTTPRequestHandler):
    server_version = "AlexandriaRecurringVoiceReview/1.0"

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        self._range: tuple[int, int] | None = None
        translated = Path(self.translate_path(self.path))
        raw_range = self.headers.get("Range")
        if raw_range is None or not translated.is_file():
            return super().send_head()
        match = _RANGE.fullmatch(raw_range.strip())
        if match is None:
            self.send_error(416, "Invalid byte range")
            return None
        size = translated.stat().st_size
        first, last = match.groups()
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        elif last:
            suffix = int(last)
            start = max(0, size - suffix)
            end = size - 1
        else:
            self.send_error(416, "Invalid byte range")
            return None
        if start < 0 or start >= size or end < start:
            self.send_error(416, "Byte range is outside the file")
            return None
        end = min(end, size - 1)
        try:
            handle = translated.open("rb")
        except OSError:
            self.send_error(404, "File not found")
            return None
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(translated)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", self.date_time_string(translated.stat().st_mtime))
        self.end_headers()
        self._range = (start, end)
        return handle

    def copyfile(self, source, outputfile) -> None:
        selected = getattr(self, "_range", None)
        if selected is None:
            shutil.copyfileobj(source, outputfile)
            return
        start, end = selected
        source.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            block = source.read(min(64 * 1024, remaining))
            if not block:
                break
            outputfile.write(block)
            remaining -= len(block)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8881)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    review = root / "review" / "index.html"
    if not review.is_file():
        raise FileNotFoundError(review)
    handler = partial(RangeRequestHandler, directory=os.fspath(root))
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"http://{args.bind}:{args.port}/review/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
