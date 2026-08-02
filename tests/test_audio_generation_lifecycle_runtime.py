from __future__ import annotations

import json
import hashlib
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
import audio_crash_reconciliation as crash_reconciliation

from audio_generation_lifecycle import (
    claim_request,
    finalize_request,
    load_request,
    normalize_request_manifest,
    prepare_request,
    reconcile_interrupted_requests,
    request_cancel,
    request_context,
)
from audio_crash_reconciliation import InjectedAudioCrash, reconcile_audio_transitions
from project import ProjectManager
from tts import TTSEngine


def write_speech(path: Path, text: str, *, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.8, len(text) * 0.05)
    count = max(1, round(duration * sample_rate))
    timeline = np.arange(count, dtype=np.float32) / sample_rate
    audio = 0.1 * np.sin(2.0 * np.pi * 7.0 * timeline)
    sf.write(path, audio, sample_rate, subtype="FLOAT")


def _raw_publication_invalidation_worker(result_queue) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "app").mkdir()
        (root / "app" / "config.json").write_text(
            json.dumps({"tts": {"mode": "local", "language": "English"}}),
            encoding="utf-8",
        )
        voice_config = {"NARRATOR": {"type": "custom", "voice": "Ryan"}}
        (root / "voice_config.json").write_text(
            json.dumps(voice_config),
            encoding="utf-8",
        )
        text = "A bounded concurrent generation and invalidation fixture."
        (root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "NARRATOR",
                        "text": text,
                        "instruct": "Calm.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        manager = ProjectManager(str(root))
        manager.engine = TTSEngine({"tts": {"mode": "local"}})

        def synthesize(
            segment_text,
            _instruct,
            _speaker,
            _config,
            output_path,
            **_kwargs,
        ):
            write_speech(Path(output_path), segment_text)
            return True

        with patch.object(
            manager.engine,
            "_generate_voice_unsegmented",
            side_effect=synthesize,
        ):
            success, _path = manager.generate_chunk_audio(0)
        if not success:
            result_queue.put({"setup": "failed"})
            return

        chunks = json.loads((root / "chunks.json").read_text(encoding="utf-8"))
        source_path = root / "concurrent-source.wav"
        write_speech(source_path, text)
        generation_at_registry = threading.Event()
        invalidation_has_chunks = threading.Event()
        original_chunks_lock = manager._chunks_lock
        original_register = ProjectManager._register_generated_take

        class ObservableChunksLock:
            def __enter__(self):
                original_chunks_lock.acquire()
                if threading.current_thread().name == "invalidation":
                    invalidation_has_chunks.set()
                return self

            def __exit__(self, _type, _value, _traceback):
                original_chunks_lock.release()

        manager._chunks_lock = ObservableChunksLock()

        def register_after_project_lock(self, **kwargs):
            generation_at_registry.set()
            invalidation_has_chunks.wait(0.25)
            return original_register(self, **kwargs)

        outcomes = {}

        def generate():
            try:
                outcomes["generation"] = manager._install_chunk_audio(
                    index=0,
                    chunk=chunks[0],
                    resolved_speaker="NARRATOR",
                    voice_config=voice_config,
                    source_path=str(source_path),
                    previous_audio_path=chunks[0]["audio_path"],
                    expected_text=text,
                    generation_context={
                        "request_id": "audio_request_concurrent",
                        "request_fingerprint": "concurrent-fingerprint",
                        "chunk_key": "chunk:0",
                    },
                )
            except BaseException as exc:
                outcomes["generation_error"] = repr(exc)

        def invalidate():
            try:
                outcomes["invalidation"] = manager.invalidate_chunk_audio(
                    [0],
                    operation_id="concurrent-invalidation",
                    reason="concurrent authored invalidation",
                )
            except BaseException as exc:
                outcomes["invalidation_error"] = repr(exc)

        with patch.object(
            ProjectManager,
            "_register_generated_take",
            new=register_after_project_lock,
        ):
            generation_thread = threading.Thread(target=generate, name="generation")
            generation_thread.start()
            if not generation_at_registry.wait(2.0):
                generation_thread.join(1.0)
                result_queue.put(
                    {
                        "orchestration": "generation lock not reached",
                        "outcomes": outcomes,
                    }
                )
                return
            invalidation_thread = threading.Thread(
                target=invalidate,
                name="invalidation",
            )
            invalidation_thread.start()
            generation_thread.join()
            invalidation_thread.join()

        first_reconciliation = reconcile_audio_transitions(root)
        second_reconciliation = reconcile_audio_transitions(root)
        final_chunks = json.loads(
            (root / "chunks.json").read_text(encoding="utf-8")
        )
        final_registry = json.loads(
            (root / "audio_takes.json").read_text(encoding="utf-8")
        )
        artifacts_valid = all(
            (root / take["artifact"]["relative_path"]).is_file()
            and hashlib.sha256(
                (root / take["artifact"]["relative_path"]).read_bytes()
            ).hexdigest()
            == take["artifact"]["sha256"]
            for take in final_registry["takes"].values()
        )
        result_queue.put(
            {
                "outcomes": outcomes,
                "chunks": final_chunks,
                "registry": final_registry,
                "artifacts_valid": artifacts_valid,
                "first_reconciliation": first_reconciliation,
                "second_reconciliation": second_reconciliation,
            }
        )


class AudioGenerationLifecycleRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"mode": "local", "language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        self.text = (
            "The first sentence is long enough to become one internal request. "
            "The second sentence also requires its own bounded synthesis window."
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "NARRATOR",
                        "text": self.text,
                        "instruct": "Calm.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.engine = TTSEngine({"tts": {"mode": "local"}})
        self.manager.engine = self.engine

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self):
        request_args = {
            "indices": [0],
            "mode": "parallel",
            "operation_mode": "missing_stale",
            "generation_seed": None,
            "plan_fingerprint": "plan-fixture",
            "chunks_fingerprint": "chunks-fixture",
        }
        manifest = self.manager.build_audio_generation_manifest(**request_args)
        prepared = prepare_request(
            self.root,
            manifest,
            operation_id="operation-fixture",
        )
        claimed = claim_request(
            self.root,
            prepared["record"]["request_id"],
            expected_request_fingerprint=prepared["record"]["request_fingerprint"],
        )
        normalized = normalize_request_manifest(manifest)
        chunk = normalized["chunks"][0]
        context = {
            **request_context(
                self.root,
                claimed["request_id"],
                claimed["owner_token"],
                chunk["chunk_key"],
            ),
            "manifest_request": request_args,
        }
        return prepared, claimed, context

    def test_raw_publication_and_invalidation_do_not_deadlock(self) -> None:
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_raw_publication_invalidation_worker,
            args=(result_queue,),
        )
        process.start()
        process.join(5.0)
        if process.is_alive():
            process.terminate()
            process.join(2.0)
            self.fail(
                "request-owned raw publication and invalidation deadlocked; "
                "the disposable worker was terminated"
            )
        self.assertEqual(process.exitcode, 0)
        result = result_queue.get(timeout=1.0)
        self.assertNotIn("setup", result)
        self.assertNotIn("orchestration", result)
        self.assertNotIn("generation_error", result["outcomes"])
        self.assertNotIn("invalidation_error", result["outcomes"])
        self.assertEqual(result["outcomes"]["invalidation"], [0])
        self.assertEqual(result["chunks"][0]["status"], "pending")
        self.assertIsNone(result["chunks"][0]["current_take_id"])
        self.assertEqual(len(result["registry"]["takes"]), 2)
        self.assertTrue(result["artifacts_valid"])
        self.assertEqual(
            result["chunks"][0]["take_registry_fingerprint"],
            result["registry"]["registry_fingerprint"],
        )
        self.assertIsNone(
            result["registry"]["chunks"]["chunk:0"]["current_take_id"]
        )
        self.assertIn(
            result["chunks"][0]["stale_audio_path"],
            {
                take["artifact"]["relative_path"]
                for take in result["registry"]["takes"].values()
            },
        )
        self.assertFalse(
            any(take["current"] for take in result["registry"]["takes"].values())
        )
        self.assertEqual(result["second_reconciliation"]["actions"], [])

    def _assert_raw_publication_crash_recovers(self, member: str) -> None:
        _prepared, running, context = self.prepare()
        calls = []

        def synthesize(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(segment_text)
            write_speech(Path(output_path), segment_text)
            return True

        original_write = crash_reconciliation.atomic_json_write
        crashed = False

        def crash_after_member(value, path):
            nonlocal crashed
            result = original_write(value, path)
            name = Path(path).name
            matches = (
                member == "registry"
                and name == "audio_takes.json"
                and any(
                    isinstance(take, dict) and take.get("current")
                    for take in (value.get("takes", {}) if isinstance(value, dict) else {}).values()
                )
            ) or (member == "chunks" and name == "chunks.json") or (
                member == "publication" and name.startswith("publication-")
            )
            if matches and not crashed:
                crashed = True
                raise InjectedAudioCrash(f"after-raw-publication-{member}")
            return result

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=synthesize,
        ), patch.object(
            crash_reconciliation,
            "atomic_json_write",
            side_effect=crash_after_member,
        ):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.generate_chunk_audio(
                    0,
                    generation_seed=None,
                    generation_context=context,
                )
        self.assertTrue(crashed)
        installed = [
            path
            for path in (self.root / "voicelines" / "takes").rglob("*.*")
            if path.is_file()
        ]
        self.assertEqual(len(installed), 1)
        installed_bytes = installed[0].read_bytes()
        installed_sha = hashlib.sha256(installed_bytes).hexdigest()

        reconcile_audio_transitions(self.root)
        first = reconcile_interrupted_requests(self.root)
        reconcile_audio_transitions(self.root)
        second = reconcile_interrupted_requests(self.root)

        terminal = load_request(self.root, running["request_id"])
        registry = json.loads((self.root / "audio_takes.json").read_text())
        chunks = json.loads((self.root / "chunks.json").read_text())
        self.assertEqual(terminal["state"], "succeeded")
        self.assertEqual(terminal["progress"]["chunk:0"]["state"], "completed")
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(registry["takes"]), 1)
        take = next(iter(registry["takes"].values()))
        self.assertTrue(take["current"])
        self.assertEqual(chunks[0]["current_take_id"], take["take_id"])
        self.assertEqual(take["generation"]["request_id"], running["request_id"])
        self.assertEqual(take["artifact"]["sha256"], installed_sha)
        self.assertEqual(installed[0].read_bytes(), installed_bytes)
        self.assertEqual(len(calls), 2)

    def test_raw_publication_recovers_after_registry_member(self) -> None:
        self._assert_raw_publication_crash_recovers("registry")

    def test_raw_publication_recovers_after_chunks_member(self) -> None:
        self._assert_raw_publication_crash_recovers("chunks")

    def test_raw_publication_recovers_after_publication_receipt_member(self) -> None:
        self._assert_raw_publication_crash_recovers("publication")

    def test_raw_install_return_is_already_request_owned_before_registration_begins(self) -> None:
        _prepared, running, context = self.prepare()
        calls = []

        def synthesize(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(segment_text)
            write_speech(Path(output_path), segment_text)
            return True

        def crash_before_registration(**_kwargs):
            raise InjectedAudioCrash("after-install-before-registration")

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=synthesize,
        ), patch.object(
            self.manager,
            "_register_generated_take",
            side_effect=crash_before_registration,
        ):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.generate_chunk_audio(
                    0,
                    generation_seed=None,
                    generation_context=context,
                )

        installed = [
            path
            for path in (self.root / "voicelines" / "takes").rglob("*.*")
            if path.is_file()
        ]
        self.assertEqual(len(installed), 1)
        installed_bytes = installed[0].read_bytes()

        reconcile_audio_transitions(self.root)
        first = reconcile_interrupted_requests(self.root)
        reconcile_audio_transitions(self.root)
        second = reconcile_interrupted_requests(self.root)

        terminal = load_request(self.root, running["request_id"])
        self.assertEqual(terminal["state"], "succeeded")
        self.assertEqual(terminal["progress"]["chunk:0"]["state"], "completed")
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        registry = json.loads((self.root / "audio_takes.json").read_text())
        chunks = json.loads((self.root / "chunks.json").read_text())
        self.assertEqual(len(registry["takes"]), 1)
        take = next(iter(registry["takes"].values()))
        self.assertTrue(take["current"])
        self.assertEqual(chunks[0]["current_take_id"], take["take_id"])
        self.assertEqual(take["generation"]["request_id"], running["request_id"])
        self.assertEqual(installed[0].read_bytes(), installed_bytes)
        self.assertEqual(len(calls), 2)

    def _assert_artifact_mutation_crash_rolls_back_without_orphan(
        self,
        *,
        after_mutation: bool,
    ) -> None:
        _prepared, running, context = self.prepare()
        calls = []

        def synthesize(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(segment_text)
            write_speech(Path(output_path), segment_text)
            return True

        patches = [
            patch.object(
                self.engine,
                "_generate_voice_unsegmented",
                side_effect=synthesize,
            )
        ]
        if after_mutation:
            import project as project_module

            original_install = project_module.install_generated_audio

            def crash_after_install(**kwargs):
                original_install(**kwargs)
                raise InjectedAudioCrash("immediately-after-artifact-mutation")

            patches.append(
                patch.object(
                    project_module,
                    "install_generated_audio",
                    side_effect=crash_after_install,
                )
            )
        else:
            patches.append(
                patch.dict(
                    "os.environ",
                    {
                        "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
                        "ALEXANDRIA_AUDIO_CRASH_POINT": (
                            "immutable_take_installation:before"
                        ),
                    },
                )
            )

        with patches[0], patches[1]:
            with self.assertRaises(InjectedAudioCrash):
                self.manager.generate_chunk_audio(
                    0,
                    generation_seed=None,
                    generation_context=context,
                )

        transition_report = reconcile_audio_transitions(self.root)
        first = reconcile_interrupted_requests(self.root)
        second_transition = reconcile_audio_transitions(self.root)
        second = reconcile_interrupted_requests(self.root)

        self.assertEqual(transition_report["rolled_back_count"], 1)
        self.assertEqual(transition_report["unresolved_count"], 0)
        self.assertEqual(second_transition["actions"], [])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(load_request(self.root, running["request_id"])["state"], "resumable")
        self.assertFalse(
            any(
                path.is_file()
                for path in (self.root / "voicelines" / "takes").rglob("*.*")
            )
        )
        self.assertFalse((self.root / "audio_takes.json").exists())
        self.assertEqual(len(calls), 2)

    def test_raw_publication_rolls_back_immediately_before_artifact_mutation(self) -> None:
        self._assert_artifact_mutation_crash_rolls_back_without_orphan(
            after_mutation=False,
        )

    def test_raw_publication_rolls_back_immediately_after_artifact_mutation(self) -> None:
        self._assert_artifact_mutation_crash_rolls_back_without_orphan(
            after_mutation=True,
        )

    def test_interrupted_request_reuses_completed_segment_and_publishes_once(self) -> None:
        _prepared, running, context = self.prepare()
        calls = []

        def first_attempt(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(("first", segment_text))
            if len(calls) == 2:
                return False
            write_speech(Path(output_path), segment_text)
            return True

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=first_attempt,
        ):
            success, _message = self.manager.generate_chunk_audio(
                0,
                generation_seed=None,
                generation_context=context,
            )
        self.assertFalse(success)
        interrupted = load_request(self.root, running["request_id"])
        segments = interrupted["progress"]["chunk:0"]["segments"]
        self.assertEqual(segments["segment_0000"]["state"], "completed")
        self.assertEqual(segments["segment_0001"]["state"], "failed")
        self.assertFalse(any((self.root / "voicelines").glob("voiceline_*")))

        reconcile_interrupted_requests(self.root)
        resumed = claim_request(
            self.root,
            running["request_id"],
            expected_request_fingerprint=running["request_fingerprint"],
        )
        request_args = context["manifest_request"]
        resumed_context = {
            **request_context(
                self.root,
                resumed["request_id"],
                resumed["owner_token"],
                "chunk:0",
            ),
            "manifest_request": request_args,
        }

        def second_attempt(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(("second", segment_text))
            write_speech(Path(output_path), segment_text)
            return True

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=second_attempt,
        ):
            success, path = self.manager.generate_chunk_audio(
                0,
                generation_seed=None,
                generation_context=resumed_context,
            )
        self.assertTrue(success)
        self.assertTrue((self.root / path).is_file())
        self.assertEqual(
            [label for label, _text in calls],
            ["first", "first", "second"],
        )
        completed = load_request(self.root, resumed["request_id"])
        self.assertEqual(completed["progress"]["chunk:0"]["state"], "completed")
        terminal = finalize_request(
            self.root,
            resumed["request_id"],
            resumed["owner_token"],
        )
        self.assertEqual(terminal["state"], "succeeded")
        self.assertEqual(terminal["terminal_summary"]["completed"], 1)
        installed = list((self.root / "voicelines" / "takes").rglob("*.*"))
        self.assertEqual(len([path for path in installed if path.is_file()]), 1)
        self.assertTrue((self.root / path).is_file())

    def test_startup_recovers_exact_publisher_result_before_lifecycle_receipt(self) -> None:
        # Given: the real publisher commits its Take and chunk, then the process
        # dies at the exact call boundary before the lifecycle receipt write.
        _prepared, running, context = self.prepare()
        calls = []

        def synthesize(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(segment_text)
            write_speech(Path(output_path), segment_text)
            return True

        import audio_generation_lifecycle as lifecycle

        original_write = lifecycle._write_record

        def crash_before_lifecycle_receipt(path, record, **kwargs):
            if kwargs.get("transition") == "lifecycle_receipt_publication":
                raise InjectedAudioCrash("publisher-returned-before-lifecycle-receipt")
            return original_write(path, record, **kwargs)

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=synthesize,
        ), patch.object(lifecycle, "_write_record", side_effect=crash_before_lifecycle_receipt):
            with self.assertRaises(InjectedAudioCrash):
                self.manager.generate_chunk_audio(
                    0,
                    generation_seed=None,
                    generation_context=context,
                )

        take_ids = {
            path.parent.name
            for path in (self.root / "voicelines" / "takes").rglob("take_*.*")
        }
        self.assertEqual(load_request(self.root, running["request_id"])["state"], "running")

        # When: normal startup reconciliation runs in production order twice.
        reconcile_audio_transitions(self.root)
        first = reconcile_interrupted_requests(self.root)
        after_first = load_request(self.root, running["request_id"])
        reconcile_audio_transitions(self.root)
        second = reconcile_interrupted_requests(self.root)

        # Then: the exact durable publication becomes terminal once; startup
        # neither dispatches synthesis nor creates/selects another Take.
        self.assertEqual(after_first["state"], "succeeded")
        self.assertEqual(after_first["progress"]["chunk:0"]["state"], "completed")
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(
            {
                path.parent.name
                for path in (self.root / "voicelines" / "takes").rglob("take_*.*")
            },
            take_ids,
        )
        self.assertEqual(len(calls), 2)

    def test_cancel_during_provider_call_prevents_segment_and_canonical_publication(self) -> None:
        _prepared, running, context = self.prepare()

        def cancel_before_return(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            write_speech(Path(output_path), segment_text)
            request_cancel(self.root, running["request_id"])
            return True

        with patch.object(
            self.engine,
            "_generate_voice_unsegmented",
            side_effect=cancel_before_return,
        ):
            success, message = self.manager.generate_chunk_audio(
                0,
                generation_seed=None,
                generation_context=context,
            )
        self.assertFalse(success)
        self.assertIn("cancel", message.casefold())
        self.assertFalse(any((self.root / "voicelines").glob("voiceline_*")))
        chunk = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(chunk["status"], "pending")
        self.assertIsNone(chunk["audio_path"])
        terminal = finalize_request(
            self.root,
            running["request_id"],
            running["owner_token"],
        )
        self.assertEqual(terminal["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
