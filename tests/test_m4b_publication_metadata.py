from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pydub import AudioSegment

from project import ProjectManager


class M4bPublicationMetadataTests(unittest.TestCase):
    def test_merge_m4b_embeds_publication_tags_cover_and_chapter(self) -> None:
        self.assertIsNotNone(shutil.which("ffmpeg"), "ffmpeg is required")
        self.assertIsNotNone(shutil.which("ffprobe"), "ffprobe is required")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cover = root / "source-cover.jpg"
            Image.new("RGB", (24, 32), color=(40, 62, 89)).save(
                cover,
                format="JPEG",
            )
            manager = ProjectManager(root)
            manager._load_chunks_with_audio = lambda: [
                (
                    {
                        "speaker": "NARRATOR",
                        "text": "Chapter One",
                        "pause_after": 0,
                    },
                    AudioSegment.silent(duration=1000),
                )
            ]
            output = root / "human-nature.m4b"

            success, message = manager.merge_m4b(
                metadata={
                    "title": "Human Nature",
                    "author": "Paul Cornell",
                    "narrator": "Alexandria Narrator",
                    "year": "2026",
                    "description": "Verified publication metadata.",
                    "cover_path": str(cover),
                },
                output_path=output,
            )

            self.assertTrue(success, message)
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-show_chapters",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(probe.stdout)
            tags = payload["format"]["tags"]
            self.assertEqual(tags["title"], "Human Nature")
            self.assertEqual(tags["artist"], "Paul Cornell")
            self.assertEqual(tags["album_artist"], "Alexandria Narrator")
            self.assertEqual(tags["date"], "2026")
            self.assertEqual(tags["comment"], "Verified publication metadata.")
            self.assertEqual(tags["genre"], "Audiobook")
            self.assertTrue(
                any(
                    stream.get("codec_type") == "video"
                    and stream.get("disposition", {}).get("attached_pic") == 1
                    for stream in payload["streams"]
                )
            )
            self.assertEqual(payload["chapters"][0]["tags"]["title"], "Chapter One")

            evidence_text = os.environ.get("ALEXANDRIA_M4B_EVIDENCE_DIR", "").strip()
            if evidence_text:
                evidence = Path(evidence_text)
                evidence.mkdir(parents=True, exist_ok=True)
                shutil.copy2(output, evidence / "human-nature-metadata-cover.m4b")
                (evidence / "ffprobe-m4b.json").write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

    def test_merge_m4b_transcodes_webp_cover_to_attached_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cover = root / "source-cover.webp"
            Image.new("RGB", (24, 32), color=(90, 40, 30)).save(
                cover,
                format="WEBP",
            )
            manager = ProjectManager(root)
            manager._load_chunks_with_audio = lambda: [
                (
                    {"speaker": "NARRATOR", "text": "Chapter One"},
                    AudioSegment.silent(duration=1000),
                )
            ]
            output = root / "webp-cover.m4b"

            success, message = manager.merge_m4b(
                metadata={"title": "Human Nature", "cover_path": str(cover)},
                output_path=output,
            )

            self.assertTrue(success, message)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(probe.stdout)
            picture = next(
                stream for stream in payload["streams"]
                if stream.get("disposition", {}).get("attached_pic") == 1
            )
            self.assertEqual(picture["codec_name"], "mjpeg")
            evidence_text = os.environ.get("ALEXANDRIA_M4B_EVIDENCE_DIR", "").strip()
            if evidence_text:
                evidence = Path(evidence_text)
                shutil.copy2(output, evidence / "webp-cover-transcoded.m4b")
                (evidence / "ffprobe-webp.json").write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )


if __name__ == "__main__":
    unittest.main()
