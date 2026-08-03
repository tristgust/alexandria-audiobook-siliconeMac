from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from generate_script import fix_mojibake, split_into_chunks
from script_audit import audit_script_chunk, split_source_segments


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "app" / "static" / "index.html"
APPLE_REQUIREMENTS = ROOT / "app" / "requirements-apple-silicon.txt"
STANDARD_REQUIREMENTS = ROOT / "app" / "requirements.txt"
PREPARER_SCRIPT = ROOT / "app" / "alexandria_preparer.py"




class UserTestRuntimeRepairTests(unittest.TestCase):
    def test_system_stats_reports_cross_platform_compute(self) -> None:
        response = TestClient(app_module.app).get("/api/system/stats")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("compute", payload)
        self.assertIsInstance(payload["compute"], dict)
        self.assertIn("utilization_percent", payload["compute"])
        self.assertIn("system_memory_percent", payload["compute"])
        self.assertIn("process_rss_gb", payload["compute"])
        self.assertEqual(payload["compute"]["kind"], "system_cpu")
        self.assertEqual(payload["compute"]["scope"], "system")
        self.assertEqual(payload["platform"], payload["compute"]["platform"])

    def test_public_hugging_face_retry_does_not_disable_valid_tokens(self) -> None:
        app_source = (ROOT / "app" / "app.py").read_text(encoding="utf-8")
        mlx_source = (ROOT / "app" / "mlx_backend.py").read_text(
            encoding="utf-8"
        )
        hf_source = (ROOT / "app" / "hf_utils.py").read_text(encoding="utf-8")
        preparer_source = PREPARER_SCRIPT.read_text(encoding="utf-8")
        for text in (app_source, mlx_source, hf_source, preparer_source):
            self.assertNotIn("HF_HUB_DISABLE_IMPLICIT_TOKEN", text)
        self.assertIn("resolve_model_path", mlx_source)
        self.assertIn("model_registry", mlx_source)
        self.assertIn(
            "hf_hub_download_with_public_fallback",
            hf_source,
        )
        self.assertIn(
            "snapshot_download_with_public_fallback",
            preparer_source,
        )

    def test_ebook_replacement_artifacts_are_removed_consistently(self) -> None:
        source = (
            "Cover\n\n��\n\nPrologue\n\n"
            "The opening narration remains exact."
        )
        normalized = fix_mojibake(source)
        self.assertNotIn("\ufffd", normalized)
        self.assertIn("Cover", normalized)
        self.assertIn("Prologue", normalized)
        chunks = split_into_chunks(normalized, max_size=3000)
        self.assertEqual(len(chunks), 1)
        entries = [
            {
                "speaker": "NARRATOR",
                "text": "Cover\n\nPrologue\n\nThe opening narration remains exact.",
                "instruct": "Neutral narration.",
            }
        ]
        result = audit_script_chunk(chunks[0], entries)
        self.assertTrue(result.passed, result.to_dict())

    def test_oceanofpdf_watermark_lines_are_removed_from_working_source(self) -> None:
        source = (
            "OceanofPDF.com\n\n"
            "Prologue\n\n"
            "The authored opening remains exact.\n\n"
            "  OceanofPDF.com  \n\n"
            "Chapter One"
        )
        normalized = fix_mojibake(source)
        self.assertNotIn("OceanofPDF.com", normalized)
        self.assertIn("Prologue", normalized)
        self.assertIn("The authored opening remains exact.", normalized)
        self.assertIn("Chapter One", normalized)

    def test_chunking_does_not_split_open_ascii_dialogue(self) -> None:
        source = (
            "Introductory narration that nearly fills the first chunk.\n\n"
            "'This spoken passage begins before the nominal boundary.\n\n"
            "It continues across a paragraph and closes here.'\n\n"
            "Following narration."
        )
        chunks = split_into_chunks(source, max_size=95)
        self.assertGreater(len(chunks), 1)
        dialogue_chunk = next(
            chunk for chunk in chunks if "spoken passage begins" in chunk
        )
        self.assertIn("closes here.'", dialogue_chunk)
        for chunk in chunks:
            split_source_segments(chunk)
        self.assertEqual("\n\n".join(chunks), source)

    def test_replacement_markers_inside_authored_text_are_preserved(self) -> None:
        source = (
            "A co\ufffdoperate marker remains.\n\n"
            "A single standalone marker follows.\n\n\ufffd\n\n"
            "But a repeated image placeholder is removed.\n\n\ufffd\ufffd\ufffd\n\nEnd."
        )
        normalized = fix_mojibake(source)
        self.assertIn("co\ufffdoperate", normalized)
        self.assertIn("\n\n\ufffd\n\n", normalized)
        self.assertNotIn("\ufffd\ufffd", normalized)

    def test_preparer_dependencies_are_declared(self) -> None:
        apple = APPLE_REQUIREMENTS.read_text(encoding="utf-8")
        standard = STANDARD_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertIn("mlx-whisper==0.4.3", apple)
        self.assertIn("psutil==7.2.2", apple)
        self.assertIn("psutil==7.2.2", standard)
        self.assertTrue(PREPARER_SCRIPT.is_file())

    def test_preparer_upload_is_confined_and_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            uploads = Path(temporary) / "uploads"
            uploads.mkdir()
            with patch.object(app_module, "UPLOADS_DIR", str(uploads)):
                response = TestClient(app_module.app).post(
                    "/api/preparer/upload",
                    files={"file": ("owned voice.wav", b"RIFFfixture", "audio/wav")},
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                stored = payload["filename"]
                self.assertEqual(Path(stored).name, stored)
                self.assertTrue((uploads / stored).is_file())
                self.assertEqual((uploads / stored).read_bytes(), b"RIFFfixture")

    def test_preparer_rejects_traversal_output_name(self) -> None:
        with self.assertRaises(app_module.HTTPException) as caught:
            app_module._preparer_output_path("../outside.zip")
        self.assertEqual(caught.exception.status_code, 422)

    def test_preparer_rejects_existing_output_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploads = root / "uploads"
            outputs = root / "outputs"
            uploads.mkdir()
            outputs.mkdir()
            (outputs / "prepared.zip").write_bytes(b"existing")

            with (
                patch.object(app_module, "UPLOADS_DIR", str(uploads)),
                patch.object(app_module, "PREPARER_OUTPUT_DIR", str(outputs)),
                patch.object(
                    app_module,
                    "PREPARER_SCRIPT_PATH",
                    str(PREPARER_SCRIPT),
                ),
            ):
                response = TestClient(app_module.app).post(
                    "/api/preparer/start",
                    data={
                        "config_json": json.dumps(
                            {
                                "audio_filename": "owned.wav",
                                "output_filename": "prepared.zip",
                                "lang": "en",
                                "min_confidence": 0.85,
                                "min_snr": 25,
                            }
                        )
                    },
                    files={"audio_file": ("owned.wav", b"RIFFfixture", "audio/wav")},
                )

            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual((outputs / "prepared.zip").read_bytes(), b"existing")
            self.assertEqual(list(uploads.iterdir()), [])

    def test_preparer_start_uses_real_script_and_cleans_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploads = root / "uploads"
            outputs = root / "outputs"
            uploads.mkdir()
            outputs.mkdir()
            captured: list[list[str]] = []

            def fake_run(command, _cwd, _state, **_kwargs):
                captured.append(list(command))
                staged = Path(command[command.index("--output") + 1])
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(b"prepared-zip")
                return 0

            app_module.process_state["preparer"].update(
                {
                    "running": False,
                    "logs": [],
                    "cancel": False,
                    "process": None,
                    "status": "idle",
                    "output_file": None,
                    "background_job_id": None,
                }
            )
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "UPLOADS_DIR", str(uploads)),
                patch.object(app_module, "PREPARER_OUTPUT_DIR", str(outputs)),
                patch.object(
                    app_module,
                    "PREPARER_SCRIPT_PATH",
                    str(PREPARER_SCRIPT),
                ),
                patch.object(
                    app_module,
                    "_stream_subprocess_to_logs",
                    side_effect=fake_run,
                ),
            ):
                response = TestClient(app_module.app).post(
                    "/api/preparer/start",
                    data={
                        "config_json": json.dumps(
                            {
                                "audio_filename": "ignored.wav",
                                "output_filename": "prepared.zip",
                                "lang": "en",
                                "min_confidence": 0.85,
                                "min_snr": 25,
                            }
                        )
                    },
                    files={"audio_file": ("owned.wav", b"RIFFfixture", "audio/wav")},
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "started")
            self.assertEqual(len(captured), 1)
            self.assertIn(str(PREPARER_SCRIPT), captured[0])
            staged = Path(captured[0][captured[0].index("--output") + 1])
            self.assertEqual(staged.name, "prepared.zip")
            self.assertEqual(staged.parent.parent.name, ".staging")
            self.assertEqual((outputs / "prepared.zip").read_bytes(), b"prepared-zip")
            self.assertEqual(list(uploads.iterdir()), [])
            self.assertEqual(
                app_module.process_state["preparer"]["status"],
                "done",
            )
            self.assertEqual(
                app_module.process_state["preparer"]["output_file"],
                "prepared.zip",
            )

    def test_batch_preparer_validates_runs_and_cleans_all_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploads = root / "uploads"
            outputs = root / "outputs"
            uploads.mkdir()
            outputs.mkdir()

            def fake_run(command, _cwd, _state, **_kwargs):
                staged = Path(command[command.index("--output") + 1])
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(f"prepared-{staged.name}".encode("utf-8"))
                return 0

            app_module.process_state["batch_preparer"].update(
                {
                    "running": False,
                    "logs": [],
                    "cancel": False,
                    "process": None,
                    "status": "idle",
                    "tasks": [],
                    "current_task_idx": -1,
                    "background_job_id": None,
                }
            )
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "UPLOADS_DIR", str(uploads)),
                patch.object(app_module, "PREPARER_OUTPUT_DIR", str(outputs)),
                patch.object(
                    app_module,
                    "PREPARER_SCRIPT_PATH",
                    str(PREPARER_SCRIPT),
                ),
                patch.object(
                    app_module,
                    "_stream_subprocess_to_logs",
                    side_effect=fake_run,
                ) as run,
            ):
                client = TestClient(app_module.app)
                first = client.post(
                    "/api/preparer/upload",
                    files={"file": ("one.wav", b"RIFFone", "audio/wav")},
                ).json()["filename"]
                second = client.post(
                    "/api/preparer/upload",
                    files={"file": ("two.mp3", b"ID3two", "audio/mpeg")},
                ).json()["filename"]
                response = client.post(
                    "/api/preparer/batch/start",
                    json={
                        "tasks": [
                            {"audio_filename": first, "output_filename": "one.zip"},
                            {"audio_filename": second, "output_filename": "two.zip"},
                        ],
                        "lang": "en",
                        "min_confidence": 0.85,
                        "min_snr": 25,
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(list(uploads.iterdir()), [])
            state = app_module.process_state["batch_preparer"]
            self.assertFalse(state["running"])
            self.assertEqual(state["status"], "done")
            self.assertEqual(
                [item["status"] for item in state["tasks"]],
                ["done", "done"],
            )
            self.assertEqual((outputs / "one.zip").read_bytes(), b"prepared-one.zip")
            self.assertEqual((outputs / "two.zip").read_bytes(), b"prepared-two.zip")

    def test_preparer_cancel_after_staging_never_publishes_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploads = root / "uploads"
            outputs = root / "outputs"
            uploads.mkdir()
            outputs.mkdir()

            def fake_run(command, _cwd, state, **_kwargs):
                staged = Path(command[command.index("--output") + 1])
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(b"must-not-publish")
                state["cancel"] = True
                app_module._request_background_cancel("preparer")
                return 0

            app_module.process_state["preparer"].update(
                {
                    "running": False,
                    "logs": [],
                    "cancel": False,
                    "process": None,
                    "status": "idle",
                    "output_file": None,
                    "background_job_id": None,
                }
            )
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "UPLOADS_DIR", str(uploads)),
                patch.object(app_module, "PREPARER_OUTPUT_DIR", str(outputs)),
                patch.object(
                    app_module,
                    "PREPARER_SCRIPT_PATH",
                    str(PREPARER_SCRIPT),
                ),
                patch.object(
                    app_module,
                    "_stream_subprocess_to_logs",
                    side_effect=fake_run,
                ),
            ):
                client = TestClient(app_module.app)
                response = client.post(
                    "/api/preparer/start",
                    data={
                        "config_json": json.dumps(
                            {
                                "audio_filename": "ignored.wav",
                                "output_filename": "prepared.zip",
                                "lang": "en",
                                "min_confidence": 0.85,
                                "min_snr": 25,
                            }
                        )
                    },
                    files={"audio_file": ("owned.wav", b"RIFFfixture", "audio/wav")},
                )
                scheduler = client.get("/api/background-work").json()

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(app_module.process_state["preparer"]["status"], "cancelled")
            self.assertFalse((outputs / "prepared.zip").exists())
            self.assertEqual(list(outputs.iterdir()), [])
            self.assertEqual(scheduler["history"][0]["state"], "cancelled")

    def test_batch_validation_failure_removes_uploaded_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploads = root / "uploads"
            outputs = root / "outputs"
            uploads.mkdir()
            outputs.mkdir()
            (outputs / "existing.zip").write_bytes(b"existing")
            app_module.process_state["batch_preparer"]["running"] = False

            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "UPLOADS_DIR", str(uploads)),
                patch.object(app_module, "PREPARER_OUTPUT_DIR", str(outputs)),
                patch.object(
                    app_module,
                    "PREPARER_SCRIPT_PATH",
                    str(PREPARER_SCRIPT),
                ),
            ):
                client = TestClient(app_module.app)
                uploaded = client.post(
                    "/api/preparer/upload",
                    files={"file": ("owned.wav", b"RIFFowned", "audio/wav")},
                ).json()["filename"]
                response = client.post(
                    "/api/preparer/batch/start",
                    json={
                        "tasks": [
                            {
                                "audio_filename": uploaded,
                                "output_filename": "existing.zip",
                            }
                        ]
                    },
                )

            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(list(uploads.iterdir()), [])
            self.assertEqual((outputs / "existing.zip").read_bytes(), b"existing")

    def test_batch_cancel_terminates_active_subprocess(self) -> None:
        class FakeProcess:
            def __init__(self):
                self.terminated = False

            def terminate(self):
                self.terminated = True

        process = FakeProcess()
        state = app_module.process_state["batch_preparer"]
        state.update(
            {
                "running": True,
                "cancel": False,
                "process": process,
                "status": "running",
                "background_job_id": None,
            }
        )
        response = TestClient(app_module.app).post(
            "/api/preparer/batch/cancel",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(process.terminated)
        self.assertTrue(state["cancel"])
        self.assertEqual(state["status"], "cancelling")
        state.update(
            {
                "running": False,
                "cancel": False,
                "process": None,
                "status": "idle",
            }
        )


if __name__ == "__main__":
    unittest.main()
