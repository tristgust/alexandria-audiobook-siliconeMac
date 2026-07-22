from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from huggingface_hub.errors import (
    GatedRepoError,
    LocalEntryNotFoundError,
    RepositoryNotFoundError,
)

from hf_access import (
    HuggingFaceAccessError,
    cached_snapshot_status,
    hf_hub_download_with_public_fallback,
    resolve_cached_hf_file,
    resolve_cached_snapshot,
    snapshot_download_with_public_fallback,
)


REVISION = "a" * 40


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("GET", "https://huggingface.co/test/repo"),
    )


def _repository_error(status_code: int, message: str) -> RepositoryNotFoundError:
    return RepositoryNotFoundError(message, response=_response(status_code))


def _snapshot(root: Path, repo_id: str, revision: str = REVISION) -> Path:
    repository = root / ("models--" + repo_id.replace("/", "--"))
    target = repository / "snapshots" / revision
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    return target


class HuggingFaceAccessTests(unittest.TestCase):
    def test_public_snapshot_retries_anonymously_after_rejected_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cached = Path(temporary) / "snapshot"
            cached.mkdir()
            (cached / "config.json").write_text("{}", encoding="utf-8")
            rejected = _repository_error(
                401,
                "OAuth token signature verification failed",
            )
            with (
                patch("hf_access.resolve_cached_snapshot", return_value=None),
                patch(
                    "hf_access.snapshot_download",
                    side_effect=[rejected, str(cached)],
                ) as download,
            ):
                result = snapshot_download_with_public_fallback(
                    "mlx-community/VoxCPM2-4bit",
                    required_paths=("config.json",),
                )

        self.assertEqual(result, cached)
        self.assertEqual(download.call_count, 2)
        self.assertIsNone(download.call_args_list[0].kwargs["token"])
        self.assertIs(download.call_args_list[1].kwargs["token"], False)

    def test_valid_explicit_token_remains_available_for_private_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cached = Path(temporary) / "private"
            cached.mkdir()
            (cached / "config.json").write_text("{}", encoding="utf-8")
            with (
                patch("hf_access.resolve_cached_snapshot", return_value=None),
                patch(
                    "hf_access.snapshot_download",
                    return_value=str(cached),
                ) as download,
            ):
                result = snapshot_download_with_public_fallback(
                    "owner/private-model",
                    token="valid-explicit-token",
                    required_paths=("config.json",),
                )

        self.assertEqual(result, cached)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(
            download.call_args.kwargs["token"],
            "valid-explicit-token",
        )

    def test_private_access_failure_is_actionable_without_token_leak(self) -> None:
        rejected = _repository_error(401, "invalid token: secret-value")
        gated = GatedRepoError(
            "Access to model is restricted",
            response=_response(403),
        )
        with (
            patch("hf_access.resolve_cached_snapshot", return_value=None),
            patch(
                "hf_access.snapshot_download",
                side_effect=[rejected, gated],
            ),
        ):
            with self.assertRaises(HuggingFaceAccessError) as caught:
                snapshot_download_with_public_fallback(
                    "owner/gated-model",
                    token="secret-value",
                )

        self.assertEqual(
            caught.exception.code,
            "huggingface_private_access_required",
        )
        self.assertIn("valid token", str(caught.exception))
        self.assertIn("configured local", str(caught.exception))
        self.assertNotIn("secret-value", str(caught.exception))

    def test_nonexistent_repository_is_distinguished(self) -> None:
        missing = _repository_error(404, "Repository Not Found")
        with (
            patch("hf_access.resolve_cached_snapshot", return_value=None),
            patch("hf_access.snapshot_download", side_effect=missing),
        ):
            with self.assertRaises(HuggingFaceAccessError) as caught:
                snapshot_download_with_public_fallback("owner/missing-model")

        self.assertEqual(
            caught.exception.code,
            "huggingface_repository_not_found",
        )

    def test_network_failure_is_distinguished(self) -> None:
        with (
            patch("hf_access.resolve_cached_snapshot", return_value=None),
            patch(
                "hf_access.snapshot_download",
                side_effect=OSError("Network is unreachable"),
            ),
        ):
            with self.assertRaises(HuggingFaceAccessError) as caught:
                snapshot_download_with_public_fallback("owner/public-model")

        self.assertEqual(
            caught.exception.code,
            "huggingface_network_unavailable",
        )

    def test_exact_cached_revision_uses_direct_path_without_hub_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "shared"
            expected = _snapshot(shared, "mlx-community/cached-model")
            with (
                patch(
                    "hf_access.huggingface_cache_roots",
                    return_value=(shared,),
                ),
                patch("hf_access.snapshot_download") as download,
            ):
                result = resolve_cached_snapshot(
                    "mlx-community/cached-model",
                    revision=REVISION,
                    required_paths=("config.json",),
                )

        self.assertEqual(result, expected)
        download.assert_not_called()

    def test_active_cache_is_used_when_shared_cache_misses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            active = root / "active"
            expected = _snapshot(active, "mlx-community/cached-model")
            with (
                patch(
                    "hf_access.huggingface_cache_roots",
                    return_value=(shared, active),
                ),
                patch("hf_access.snapshot_download") as download,
            ):
                result = resolve_cached_snapshot(
                    "mlx-community/cached-model",
                    revision=REVISION,
                    required_paths=("config.json",),
                )

        self.assertEqual(result, expected)
        download.assert_not_called()

    def test_incomplete_exact_snapshot_is_not_reported_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "shared"
            target = _snapshot(shared, "mlx-community/cached-model")
            (target / "config.json").unlink()
            with (
                patch(
                    "hf_access.huggingface_cache_roots",
                    return_value=(shared,),
                ),
                patch(
                    "hf_access.snapshot_download",
                    side_effect=LocalEntryNotFoundError("missing"),
                ),
            ):
                status = cached_snapshot_status(
                    "mlx-community/cached-model",
                    revision=REVISION,
                    required_paths=("config.json",),
                )

        self.assertFalse(status["cached"])
        self.assertEqual(status["state"], "incomplete")
        self.assertEqual(status["snapshot_path"], str(target))
        self.assertEqual(status["missing_required_paths"], ["config.json"])
        self.assertEqual(status["file_count"], 0)
        self.assertEqual(status["size_bytes"], 0)

    def test_network_fallback_downloads_into_shared_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "shared"
            downloaded = Path(temporary) / "downloaded"
            downloaded.mkdir()
            (downloaded / "config.json").write_text("{}", encoding="utf-8")

            with (
                patch("hf_access.resolve_cached_snapshot", return_value=None),
                patch(
                    "hf_access.shared_huggingface_cache_dir",
                    return_value=shared,
                ),
                patch(
                    "hf_access.snapshot_download",
                    return_value=str(downloaded),
                ) as download,
            ):
                result = snapshot_download_with_public_fallback(
                    "mlx-community/new-model",
                    revision=REVISION,
                    required_paths=("config.json",),
                )

        self.assertEqual(result, downloaded)
        self.assertEqual(Path(download.call_args.kwargs["cache_dir"]), shared)
        self.assertEqual(download.call_args.kwargs["revision"], REVISION)
        self.assertNotIn("local_files_only", download.call_args.kwargs)

    def test_local_only_missing_snapshot_never_uses_network(self) -> None:
        with (
            patch("hf_access.resolve_cached_snapshot", return_value=None),
            patch("hf_access.snapshot_download") as download,
        ):
            with self.assertRaises(HuggingFaceAccessError) as caught:
                snapshot_download_with_public_fallback(
                    "mlx-community/missing-model",
                    local_files_only=True,
                )

        self.assertEqual(
            caught.exception.code,
            "huggingface_cached_snapshot_missing",
        )
        download.assert_not_called()

    def test_cached_file_uses_exact_revision_without_hub_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "shared"
            snapshot = _snapshot(shared, "owner/artifacts")
            manifest = snapshot / "manifest.json"
            manifest.write_text("[]", encoding="utf-8")
            with (
                patch(
                    "hf_access.huggingface_cache_roots",
                    return_value=(shared,),
                ),
                patch("hf_access.hf_hub_download") as download,
            ):
                result = resolve_cached_hf_file(
                    repo_id="owner/artifacts",
                    filename="manifest.json",
                    revision=REVISION,
                )

        self.assertEqual(result, manifest)
        download.assert_not_called()

    def test_file_download_uses_same_authenticated_then_anonymous_contract(self) -> None:
        rejected = _repository_error(
            401,
            "OAuth token signature verification failed",
        )
        with (
            patch("hf_access.resolve_cached_hf_file", return_value=None),
            patch(
                "hf_access.hf_hub_download",
                side_effect=[rejected, "/cache/manifest.json"],
            ) as download,
        ):
            result = hf_hub_download_with_public_fallback(
                repo_id="Finrandojin/Alexandria",
                filename="manifest.json",
                revision=REVISION,
            )

        self.assertEqual(result, "/cache/manifest.json")
        self.assertIsNone(download.call_args_list[0].kwargs["token"])
        self.assertIs(download.call_args_list[1].kwargs["token"], False)
        self.assertEqual(download.call_args.kwargs["revision"], REVISION)

    def test_local_only_missing_file_never_uses_network(self) -> None:
        with (
            patch("hf_access.resolve_cached_hf_file", return_value=None),
            patch("hf_access.hf_hub_download") as download,
        ):
            with self.assertRaises(HuggingFaceAccessError) as caught:
                hf_hub_download_with_public_fallback(
                    repo_id="owner/artifacts",
                    filename="manifest.json",
                    revision=REVISION,
                    local_files_only=True,
                )

        self.assertEqual(caught.exception.code, "huggingface_cached_file_missing")
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
