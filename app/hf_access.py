from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, TypeVar

from huggingface_hub import constants
from huggingface_hub import hf_hub_download, snapshot_download
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    LocalEntryNotFoundError,
    OfflineModeIsEnabled,
    RepositoryNotFoundError,
)

try:
    from huggingface_hub.errors import IncompleteSnapshotError
except ImportError:
    class IncompleteSnapshotError(Exception):
        """Compatibility shim for huggingface_hub releases before 1.x."""

        pass


logger = logging.getLogger("AlexandriaUI")
T = TypeVar("T")
ALEXANDRIA_HF_CACHE_ENV = "ALEXANDRIA_HF_CACHE"


class HuggingFaceAccessError(RuntimeError):
    """Actionable Hub failure that never includes credential contents."""

    def __init__(self, code: str, message: str, *, repo_id: str):
        super().__init__(message)
        self.code = code
        self.repo_id = repo_id


def _status_code(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _error_text(error: BaseException) -> str:
    return str(error).casefold()


def _is_authentication_failure(error: BaseException) -> bool:
    if isinstance(error, GatedRepoError):
        return True
    if _status_code(error) in {401, 403}:
        return True
    text = _error_text(error)
    return any(
        marker in text
        for marker in (
            "oauth token signature verification failed",
            "invalid token",
            "invalid credentials",
            "authentication",
            "unauthorized",
            "forbidden",
        )
    )


def _is_network_failure(error: BaseException) -> bool:
    if isinstance(error, (OfflineModeIsEnabled, LocalEntryNotFoundError)):
        return True
    class_name = type(error).__name__.casefold()
    if any(
        marker in class_name
        for marker in ("timeout", "connecterror", "networkerror")
    ):
        return True
    text = _error_text(error)
    return any(
        marker in text
        for marker in (
            "offline mode",
            "network is unreachable",
            "connection refused",
            "connection reset",
            "temporary failure in name resolution",
            "name or service not known",
            "timed out",
        )
    )


def _repository_missing(error: BaseException) -> bool:
    return (
        isinstance(error, RepositoryNotFoundError)
        and not isinstance(error, GatedRepoError)
        and _status_code(error) == 404
    )


def _actionable_failure(
    *,
    repo_id: str,
    primary_error: BaseException,
    anonymous_error: BaseException | None = None,
) -> HuggingFaceAccessError:
    final_error = anonymous_error or primary_error

    if _is_network_failure(final_error):
        return HuggingFaceAccessError(
            "huggingface_network_unavailable",
            (
                f"Hugging Face could not be reached while loading '{repo_id}'. "
                "Check the network connection or use an already cached model."
            ),
            repo_id=repo_id,
        )

    if _repository_missing(final_error):
        return HuggingFaceAccessError(
            "huggingface_repository_not_found",
            (
                f"Hugging Face repository '{repo_id}' does not exist or is no "
                "longer publicly available."
            ),
            repo_id=repo_id,
        )

    if isinstance(final_error, GatedRepoError) or _is_authentication_failure(
        final_error
    ):
        rejected_local_token = _is_authentication_failure(primary_error)
        token_note = (
            " The configured local Hugging Face token was rejected."
            if rejected_local_token
            else ""
        )
        return HuggingFaceAccessError(
            "huggingface_private_access_required",
            (
                f"Hugging Face repository '{repo_id}' requires a valid token "
                f"with access to that private or gated repository.{token_note}"
            ),
            repo_id=repo_id,
        )

    if _is_authentication_failure(primary_error):
        return HuggingFaceAccessError(
            "huggingface_invalid_local_token",
            (
                "The configured local Hugging Face token was rejected, and "
                f"anonymous access to '{repo_id}' did not succeed."
            ),
            repo_id=repo_id,
        )

    if isinstance(final_error, HfHubHTTPError):
        status = _status_code(final_error)
        status_text = f" (HTTP {status})" if status is not None else ""
        return HuggingFaceAccessError(
            "huggingface_request_failed",
            f"Hugging Face could not load '{repo_id}'{status_text}.",
            repo_id=repo_id,
        )

    return HuggingFaceAccessError(
        "huggingface_request_failed",
        f"Hugging Face could not load '{repo_id}': {type(final_error).__name__}.",
        repo_id=repo_id,
    )


def shared_huggingface_cache_dir() -> Path:
    override = os.environ.get(
        ALEXANDRIA_HF_CACHE_ENV
    )
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
    ).resolve()


def huggingface_cache_roots(
    explicit_cache_dir: str | Path | None = None,
    *,
    include_fallback_roots: bool = True,
) -> tuple[Path, ...]:
    candidates = []
    if explicit_cache_dir is not None:
        candidates.append(
            Path(explicit_cache_dir)
            .expanduser()
            .resolve()
        )
    if explicit_cache_dir is None or include_fallback_roots:
        candidates.append(
            shared_huggingface_cache_dir()
        )
        candidates.append(
            Path(constants.HF_HUB_CACHE)
            .expanduser()
            .resolve()
        )

    unique = []
    seen = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(candidate)
    return tuple(unique)


_COMMIT_REVISION = re.compile(r"^[0-9a-f]{40}$")


def _repository_cache_dir(root: Path, repo_id: str) -> Path:
    parts = repo_id.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid Hugging Face repository ID: {repo_id!r}.")
    return root / ("models--" + "--".join(parts))


def _resolved_revision(repository: Path, revision: str | None) -> str | None:
    if revision and _COMMIT_REVISION.fullmatch(revision):
        return revision
    reference = str(revision or "main").strip()
    if not reference or reference.startswith(('/', '\\')) or ".." in Path(reference).parts:
        return None
    ref_path = repository / "refs" / reference
    try:
        value = ref_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if _COMMIT_REVISION.fullmatch(value) else None


def _snapshot_inventory(
    snapshot: Path,
    required_paths: tuple[str, ...] | list[str] | None,
) -> dict[str, Any]:
    required = tuple(required_paths or ())
    missing_required = [
        name
        for name in required
        if not (snapshot / name).is_file()
    ]
    broken_symlinks: list[str] = []
    file_count = 0
    size_bytes = 0
    if snapshot.is_dir():
        try:
            for path in snapshot.rglob("*"):
                if path.is_symlink() and not path.exists():
                    broken_symlinks.append(
                        path.relative_to(snapshot).as_posix()
                    )
                    continue
                if path.is_file():
                    file_count += 1
                    size_bytes += path.stat().st_size
        except OSError:
            broken_symlinks.append("<unreadable snapshot>")
    if not snapshot.is_dir():
        state = "missing"
    elif missing_required or broken_symlinks or file_count == 0:
        state = "incomplete"
    else:
        state = "cached"
    return {
        "state": state,
        "cached": state == "cached",
        "snapshot_path": str(snapshot) if snapshot.exists() else None,
        "required_paths": list(required),
        "missing_required_paths": missing_required,
        "broken_symlinks": broken_symlinks,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def _snapshot_complete(
    snapshot: Path,
    required_paths: tuple[str, ...] | list[str] | None,
) -> bool:
    return _snapshot_inventory(snapshot, required_paths)["cached"]


def _direct_cached_snapshot(
    *,
    root: Path,
    repo_id: str,
    revision: str | None,
    required_paths: tuple[str, ...] | list[str] | None,
) -> Path | None:
    repository = _repository_cache_dir(root, repo_id)
    resolved_revision = _resolved_revision(repository, revision)
    if resolved_revision is None:
        return None
    snapshot = repository / "snapshots" / resolved_revision
    return snapshot if _snapshot_complete(snapshot, required_paths) else None


def cached_snapshot_status(
    repo_id: str,
    *,
    revision: str | None = None,
    cache_dir: str | Path | None = None,
    required_paths: tuple[str, ...] | list[str] | None = None,
    include_fallback_roots: bool = True,
) -> dict[str, Any]:
    roots = huggingface_cache_roots(
        cache_dir,
        include_fallback_roots=include_fallback_roots,
    )
    incomplete: dict[str, Any] | None = None
    for root in roots:
        repository = _repository_cache_dir(root, repo_id)
        resolved_revision = _resolved_revision(repository, revision)
        if resolved_revision is None:
            continue
        snapshot = repository / "snapshots" / resolved_revision
        inventory = {
            **_snapshot_inventory(snapshot, required_paths),
            "cache_root": str(root),
            "revision": resolved_revision,
        }
        if inventory["cached"]:
            return inventory
        if inventory["state"] == "incomplete" and incomplete is None:
            incomplete = inventory
    if incomplete is not None:
        return incomplete
    return {
        "state": "missing",
        "cached": False,
        "snapshot_path": None,
        "cache_root": str(roots[0]) if roots else None,
        "revision": revision,
        "required_paths": list(required_paths or ()),
        "missing_required_paths": list(required_paths or ()),
        "broken_symlinks": [],
        "file_count": 0,
        "size_bytes": 0,
    }


def resolve_cached_snapshot(
    repo_id: str,
    *,
    revision: str | None = None,
    allow_patterns: list[str] | str | None = None,
    ignore_patterns: list[str] | str | None = None,
    cache_dir: str | Path | None = None,
    required_paths: tuple[str, ...] | list[str] | None = None,
    include_fallback_roots: bool = True,
) -> Path | None:
    roots = huggingface_cache_roots(
        cache_dir,
        include_fallback_roots=include_fallback_roots,
    )
    for root in roots:
        direct = _direct_cached_snapshot(
            root=root,
            repo_id=repo_id,
            revision=revision,
            required_paths=required_paths,
        )
        if direct is not None:
            logger.info(
                "Using cached Hugging Face snapshot for %s@%s: %s",
                repo_id,
                direct.name,
                direct,
            )
            return direct

    for root in roots:
        try:
            result = snapshot_download(
                repo_id=repo_id,
                revision=revision,
                cache_dir=root,
                local_files_only=True,
                allow_patterns=allow_patterns,
                ignore_patterns=ignore_patterns,
                token=False,
            )
        except (
            LocalEntryNotFoundError,
            IncompleteSnapshotError,
            OfflineModeIsEnabled,
            FileNotFoundError,
        ):
            continue

        resolved = Path(result)
        if _snapshot_complete(resolved, required_paths):
            logger.info(
                "Using cached Hugging Face snapshot for %s: %s",
                repo_id,
                resolved,
            )
            return resolved
    return None


def resolve_cached_hf_file(
    *,
    repo_id: str,
    filename: str,
    revision: str | None = None,
    cache_dir: str | Path | None = None,
) -> Path | None:
    safe_name = Path(filename)
    if safe_name.is_absolute() or ".." in safe_name.parts:
        raise ValueError(f"Invalid Hugging Face filename: {filename!r}.")
    for root in huggingface_cache_roots(cache_dir):
        snapshot = _direct_cached_snapshot(
            root=root,
            repo_id=repo_id,
            revision=revision,
            required_paths=(filename,),
        )
        if snapshot is not None:
            target = snapshot / safe_name
            if target.is_file():
                logger.info(
                    "Using cached Hugging Face file for %s@%s: %s",
                    repo_id,
                    snapshot.name,
                    target,
                )
                return target
    return None


def _call_with_public_retry(
    *,
    repo_id: str,
    token: bool | str | None,
    operation: Callable[[bool | str | None], T],
) -> T:
    """Use configured credentials, then retry a public repository anonymously."""
    try:
        return operation(token)
    except Exception as primary_error:
        if token is False or not _is_authentication_failure(primary_error):
            raise _actionable_failure(
                repo_id=repo_id,
                primary_error=primary_error,
            ) from primary_error

        try:
            result = operation(False)
        except Exception as anonymous_error:
            raise _actionable_failure(
                repo_id=repo_id,
                primary_error=primary_error,
                anonymous_error=anonymous_error,
            ) from anonymous_error

        logger.warning(
            "Hugging Face authentication was rejected for public repository %s; "
            "the request succeeded anonymously.",
            repo_id,
        )
        return result


def hf_hub_download_with_public_fallback(
    *,
    repo_id: str,
    filename: str,
    token: bool | str | None = None,
    **kwargs: Any,
) -> str:
    options = dict(kwargs)
    explicit_cache_dir = options.get("cache_dir")
    revision = options.get("revision")
    force_download = bool(options.get("force_download", False))
    requested_local_only = bool(options.get("local_files_only", False))

    if not force_download:
        cached = resolve_cached_hf_file(
            repo_id=repo_id,
            filename=filename,
            revision=revision,
            cache_dir=explicit_cache_dir,
        )
        if cached is not None:
            return str(cached)

    if requested_local_only:
        raise HuggingFaceAccessError(
            "huggingface_cached_file_missing",
            (
                f"No cached copy of '{filename}' from '{repo_id}' exists in "
                "Alexandria's shared or active Hugging Face caches."
            ),
            repo_id=repo_id,
        )

    if explicit_cache_dir is None:
        options["cache_dir"] = shared_huggingface_cache_dir()
    options.pop("local_files_only", None)
    return _call_with_public_retry(
        repo_id=repo_id,
        token=token,
        operation=lambda resolved_token: hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            token=resolved_token,
            **options,
        ),
    )


def snapshot_download_with_public_fallback(
    repo_id: str,
    *,
    token: bool | str | None = None,
    required_paths: tuple[str, ...] | list[str] | None = None,
    **kwargs: Any,
) -> Path:
    options = dict(kwargs)
    explicit_cache_dir = options.get(
        "cache_dir"
    )
    force_download = bool(
        options.get("force_download", False)
    )
    include_fallback_roots = bool(
        options.pop("include_fallback_roots", True)
    )
    requested_local_only = bool(
        options.get("local_files_only", False)
    )

    if not force_download:
        cached = resolve_cached_snapshot(
            repo_id,
            revision=options.get("revision"),
            allow_patterns=options.get(
                "allow_patterns"
            ),
            ignore_patterns=options.get(
                "ignore_patterns"
            ),
            cache_dir=explicit_cache_dir,
            required_paths=required_paths,
            include_fallback_roots=include_fallback_roots,
        )
        if cached is not None:
            return cached

    if requested_local_only:
        raise HuggingFaceAccessError(
            "huggingface_cached_snapshot_missing",
            (
                f"No complete cached snapshot of '{repo_id}' exists in "
                "Alexandria's shared or active Hugging Face caches."
            ),
            repo_id=repo_id,
        )

    if explicit_cache_dir is None:
        options["cache_dir"] = (
            shared_huggingface_cache_dir()
        )
    options.pop("local_files_only", None)

    result = _call_with_public_retry(
        repo_id=repo_id,
        token=token,
        operation=lambda resolved_token: snapshot_download(
            repo_id=repo_id,
            token=resolved_token,
            **options,
        ),
    )
    resolved = Path(result)
    if not _snapshot_complete(resolved, required_paths):
        raise HuggingFaceAccessError(
            "huggingface_incomplete_snapshot",
            (
                f"The downloaded snapshot of '{repo_id}' is incomplete or "
                "missing required Alexandria model files."
            ),
            repo_id=repo_id,
        )
    return resolved
