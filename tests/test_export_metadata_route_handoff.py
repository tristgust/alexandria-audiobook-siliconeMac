from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from pydub import AudioSegment

import app as app_module
from project import ProjectManager


class ExportMetadataRouteHandoffTests(unittest.TestCase):
    def test_route_builds_downloadable_m4b_from_project_metadata_and_webp_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sources" / "human-nature.epub"
            source.parent.mkdir()
            cover_buffer = io.BytesIO()
            Image.new("RGB", (24, 32), color=(70, 35, 20)).save(
                cover_buffer,
                format="WEBP",
            )
            container = (
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
            )
            package = (
                '<package xmlns="http://www.idpf.org/2007/opf"><metadata>'
                '<meta name="cover" content="cover"/></metadata><manifest>'
                '<item id="cover" href="cover.webp" media-type="image/webp"/>'
                '</manifest></package>'
            )
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("META-INF/container.xml", container)
                archive.writestr("content.opf", package)
                archive.writestr("cover.webp", cover_buffer.getvalue())
            source_before = hashlib.sha256(source.read_bytes()).hexdigest()
            (root / "alexandria-project.json").write_text(
                json.dumps(
                    {
                        "source": {
                            "title": "Human Nature",
                            "author": "Paul Cornell",
                            "original_relative_path": "sources/human-nature.epub",
                        }
                    }
                ),
                encoding="utf-8",
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "tts": {
                            "pause_between_speakers_ms": 0,
                            "pause_same_speaker_ms": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            produce = {
                "summary": {"complete": True},
                "chunks": [
                    {
                        "chunk_id": "chunk:0",
                        "speaker": "NARRATOR",
                        "text": "Chapter One",
                        "duration_ms": 1000,
                    }
                ],
                "fingerprints": {"aggregate": "route-current"},
            }
            manager = ProjectManager(root, config_path=config_path)
            manager._load_chunks_with_audio = lambda: [
                (
                    {"speaker": "NARRATOR", "text": "Chapter One"},
                    AudioSegment.silent(duration=1000),
                )
            ]
            output = root / "audiobook.m4b"
            process_before = copy.deepcopy(app_module.process_state["export"])
            app_module.process_state["export"].update(
                {"running": False, "cancel": False, "logs": [], "result": None}
            )
            client = TestClient(app_module.app)
            try:
                with patch.multiple(
                    app_module,
                    ROOT_DIR=str(root),
                    CONFIG_PATH=str(config_path),
                    M4B_PATH=str(output),
                    project_manager=manager,
                    _current_produce_status=lambda: produce,
                ):
                    status = client.get("/api/export")
                    self.assertEqual(status.status_code, 200, status.text)
                    aggregate = status.json()
                    self.assertEqual(aggregate["metadata"]["title"], "Human Nature")
                    self.assertEqual(aggregate["metadata"]["author"], "Paul Cornell")
                    self.assertEqual(aggregate["cover"]["kind"], "source_epub")
                    request = {
                        "metadata": aggregate["metadata"],
                        "formats": ["m4b"],
                        "chapter_mode": "smart",
                    }
                    plan_response = client.post("/api/export/plan", json=request)
                    self.assertEqual(plan_response.status_code, 200, plan_response.text)
                    plan = plan_response.json()
                    request.update(
                        {
                            "plan_fingerprint": plan["plan_fingerprint"],
                            "dependency_fingerprint": plan["dependency_fingerprint"],
                        }
                    )

                    build = client.post("/api/export/build", json=request)
                    self.assertEqual(build.status_code, 200, build.text)
                    self.assertEqual(app_module.process_state["export"]["result"]["status"], "complete")
                    download = client.get("/api/audiobook_m4b")
                    self.assertEqual(download.status_code, 200, download.text)
                    self.assertEqual(download.content, output.read_bytes())
            finally:
                client.close()
                app_module.process_state["export"].clear()
                app_module.process_state["export"].update(process_before)

            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(probe.stdout)
            self.assertEqual(payload["format"]["tags"]["title"], "Human Nature")
            self.assertEqual(payload["format"]["tags"]["artist"], "Paul Cornell")
            picture = next(
                stream for stream in payload["streams"]
                if stream.get("disposition", {}).get("attached_pic") == 1
            )
            self.assertEqual(picture["codec_name"], "mjpeg")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_before)
            self.assertFalse((root / "m4b_cover.jpg").exists())

            evidence_text = os.environ.get("ALEXANDRIA_M4B_EVIDENCE_DIR", "").strip()
            if evidence_text:
                evidence = Path(evidence_text)
                shutil.copy2(output, evidence / "route-finished-human-nature.m4b")
                (evidence / "ffprobe-route-finished.json").write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )


if __name__ == "__main__":
    unittest.main()
