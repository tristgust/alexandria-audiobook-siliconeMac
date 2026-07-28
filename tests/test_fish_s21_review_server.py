from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from benchmarks.serve_fish_s21_review import build_server


class FishS21ReviewServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "index.html").write_text("<!doctype html><title>Review</title>", encoding="utf-8")
        (self.root / "data.js").write_text("window.FISH_S21_BLIND_DATA = {};", encoding="utf-8")
        (self.root / "manifest.json").write_text(json.dumps({"sample_count": 1}), encoding="utf-8")
        self.payload = bytes(range(256)) * 8
        (self.root / "sample.wav").write_bytes(self.payload)
        self.server = build_server(self.root, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def test_full_response_advertises_ranges_and_disables_cache(self) -> None:
        with closing(urlopen(f"{self.base}/sample.wav", timeout=5)) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.read(), self.payload)

    def test_explicit_range_returns_only_requested_bytes(self) -> None:
        request = Request(
            f"{self.base}/sample.wav",
            headers={"Range": "bytes=100-199"},
        )
        with closing(urlopen(request, timeout=5)) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], f"bytes 100-199/{len(self.payload)}")
            self.assertEqual(int(response.headers["Content-Length"]), 100)
            self.assertEqual(response.read(), self.payload[100:200])

    def test_suffix_range_returns_tail(self) -> None:
        request = Request(
            f"{self.base}/sample.wav",
            headers={"Range": "bytes=-32"},
        )
        with closing(urlopen(request, timeout=5)) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), self.payload[-32:])

    def test_invalid_range_returns_416(self) -> None:
        request = Request(
            f"{self.base}/sample.wav",
            headers={"Range": f"bytes={len(self.payload) + 100}-"},
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        error = raised.exception
        try:
            self.assertEqual(error.code, 416)
            self.assertEqual(
                error.headers["Content-Range"],
                f"bytes */{len(self.payload)}",
            )
        finally:
            error.close()

    def test_hub_root_requires_only_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "index.html").write_text(
                "<!doctype html><title>Review hub</title>",
                encoding="utf-8",
            )
            server = build_server(root, "127.0.0.1", 0)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
