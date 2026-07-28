from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from multimodel_round1_public_audio import (  # noqa: E402
    PublicAudioError,
    sanitize_public_audio,
    verify_public_audio,
)


def run_tool(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def decoded_sha256(path: Path) -> str:
    completed = run_tool(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s64le",
            "-fflags",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]
    )
    return completed.stdout.strip().removeprefix("SHA256=")


def metadata_tags(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    completed = run_tool(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags:stream_tags",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(completed.stdout)
    return (
        payload.get("format", {}).get("tags", {}),
        [stream.get("tags", {}) for stream in payload.get("streams", [])],
    )


class PublicAudioSanitizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.base = self.root / "base.wav"
        with wave.open(str(self.base), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8_000)
            samples = (800, -800, 400, -400) * 200
            handle.writeframes(
                b"".join(value.to_bytes(2, "little", signed=True) for value in samples)
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def tagged_audio(
        self,
        suffix: str,
        *,
        name: str = "derricksjones-7thDoctorSpeeches-model-source",
        tags: dict[str, str] | None = None,
    ) -> Path:
        target = self.root / f"{name}{suffix}"
        metadata = tags or {
            "artist": "derricksjones",
            "title": "7thDoctorSpeeches",
            "comment": "/private/model/vendor/source.wav",
        }
        codec = "copy" if suffix == ".wav" else "libmp3lame"
        arguments = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(self.base),
            "-map",
            "0:a:0",
            "-c:a",
            codec,
        ]
        for key, value in metadata.items():
            arguments.extend(("-metadata", f"{key}={value}"))
        run_tool([*arguments, str(target)])
        return target

    def test_tagged_wav_is_stream_copied_without_public_metadata(self) -> None:
        # Given a WAV whose tags and source name expose private provenance.
        source = self.tagged_audio(".wav")
        target = self.root / "public" / "blind-reference.wav"
        target.parent.mkdir()

        # When the container is sanitized for public review.
        artifact = sanitize_public_audio(source, target)

        # Then tags vanish and the decoded payload is exactly equivalent.
        self.assertEqual(metadata_tags(target), ({}, [{}]))
        self.assertEqual(decoded_sha256(source), decoded_sha256(target))
        self.assertEqual(artifact.decoded_sha256, decoded_sha256(source))
        self.assertEqual(artifact.sha256, hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(artifact.public_name, "blind-reference.wav")
        self.assertEqual(artifact.codec_name, "pcm_s16le")
        self.assertEqual(artifact.size_bytes, target.stat().st_size)
        lowered = target.read_bytes().lower()
        for private in (b"derricksjones", b"7thdoctorspeeches", b"model/vendor/source"):
            self.assertNotIn(private, lowered)
        self.assertEqual(verify_public_audio(target), artifact)

    def test_same_source_and_public_name_produce_identical_bytes_and_hash(self) -> None:
        # Given one tagged source and two equivalent publication destinations.
        source = self.tagged_audio(".wav")
        first = self.root / "first" / "reference.wav"
        second = self.root / "second" / "reference.wav"
        first.parent.mkdir()
        second.parent.mkdir()

        # When both destinations are sanitized independently.
        first_artifact = sanitize_public_audio(source, first)
        second_artifact = sanitize_public_audio(source, second)

        # Then final names, hashes, and container bytes are deterministic.
        self.assertEqual(first_artifact.public_name, second_artifact.public_name)
        self.assertEqual(first_artifact.sha256, second_artifact.sha256)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_mp3_reference_preserves_decoded_audio_without_provenance_metadata(self) -> None:
        # Given a tagged MP3 reference container.
        source = self.tagged_audio(".mp3")
        target = self.root / "public" / "blind-original.mp3"
        target.parent.mkdir()

        # When it is stream-copied through the public sanitizer.
        artifact = sanitize_public_audio(source, target)

        # Then gapless decoded audio survives and provenance metadata does not.
        self.assertEqual(decoded_sha256(source), decoded_sha256(target))
        self.assertIn(
            metadata_tags(target),
            (({}, [{}]), ({}, [{"encoder": "Lavf"}])),
        )
        self.assertEqual(artifact.codec_name, "mp3")
        lowered = target.read_bytes().lower()
        for private in (b"derricksjones", b"7thdoctorspeeches", b"model/vendor/source"):
            self.assertNotIn(private, lowered)

    def test_verifier_rejects_forbidden_and_unexpected_metadata(self) -> None:
        # Given containers with forbidden provenance and an unapproved tag.
        forbidden = self.tagged_audio(".wav")
        unexpected = self.tagged_audio(
            ".wav", name="unexpected", tags={"genre": "Internal workflow"}
        )

        # When/then either reaches the public verification boundary, it is rejected.
        with self.assertRaises(PublicAudioError) as forbidden_error:
            verify_public_audio(forbidden)
        self.assertEqual(forbidden_error.exception.code, "metadata_forbidden")
        with self.assertRaises(PublicAudioError) as unexpected_error:
            verify_public_audio(unexpected)
        self.assertEqual(unexpected_error.exception.code, "metadata_unexpected")

    def test_source_ancestor_target_and_target_ancestor_symlinks_are_rejected(self) -> None:
        # Given a valid source plus symlinks at every untrusted path position.
        source = self.tagged_audio(".wav")
        source_link = self.root / "source-link.wav"
        source_link.symlink_to(source)
        ancestor_link = self.root / "source-ancestor"
        ancestor_link.symlink_to(self.root, target_is_directory=True)
        public = self.root / "public"
        public.mkdir()
        victim = self.root / "victim.wav"
        victim.write_bytes(b"victim")
        target_link = public / "target-link.wav"
        target_link.symlink_to(victim)
        real_target_parent = self.root / "real-target"
        real_target_parent.mkdir()
        target_ancestor = self.root / "target-ancestor"
        target_ancestor.symlink_to(real_target_parent, target_is_directory=True)
        cases = (
            (source_link, public / "from-source-link.wav"),
            (ancestor_link / source.name, public / "from-ancestor-link.wav"),
            (source, target_link),
            (source, target_ancestor / "through-target-ancestor.wav"),
        )

        # When/then sanitization sees any symlink, it fails closed.
        for candidate_source, candidate_target in cases:
            with self.subTest(source=candidate_source, target=candidate_target):
                with self.assertRaises(PublicAudioError) as raised:
                    sanitize_public_audio(candidate_source, candidate_target)
                self.assertEqual(raised.exception.code, "path_symlink")
        self.assertEqual(victim.read_bytes(), b"victim")

    def test_external_guard_can_veto_atomic_publication(self) -> None:
        # Given a shared guard that detects a race at the publication boundary.
        source = self.tagged_audio(".wav")
        target = self.root / "public" / "existing.wav"
        target.parent.mkdir()
        target.write_bytes(b"previous-publication")
        calls: list[tuple[Path, bool]] = []

        class SharedSafetyVeto(Exception):
            pass

        def veto(path: Path, *, allow_missing_leaf: bool) -> None:
            calls.append((path, allow_missing_leaf))
            if len(calls) == 3:
                raise SharedSafetyVeto("shared safety veto")

        # When the guard vetoes immediately before the atomic replace.
        with self.assertRaisesRegex(SharedSafetyVeto, "shared safety veto"):
            sanitize_public_audio(source, target, path_guard=veto)

        # Then the prior target remains and the private temporary file is removed.
        self.assertEqual(target.read_bytes(), b"previous-publication")
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.partial")), [])


if __name__ == "__main__":
    unittest.main()
