from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path
from typing import Any, Final

from engine_qualification import QualificationError, _digest, _entry_exists_at, _identifier, _open_directory_at, _write_exclusive_at, canonical_bytes, canonical_hash


SCHEMA_VERSION: Final = 1
RATING_FIELDS: Final = (
    "narrator_voice_identity",
    "text_fidelity",
    "delivery",
    "naturalness",
    "artifact",
)
_SOURCE_FIELDS: Final = {"expected_item_id", "subject_id", "record_fingerprint", "profile_hash", "audio_identity", "required_playback", "restriction_options"}
_MAPPING_FIELDS: Final = {"label", "expected_item_id", "subject_id", "record_fingerprint", "profile_hash", "audio_identity"}
_PUBLIC_ITEM_FIELDS: Final = {"label", "audio_identity", "required_playback", "restriction_options"}
_VOTE_FIELDS: Final = {"label", "playback_complete", *RATING_FIELDS, "restrictions", "notes", "included", "exclusion_reason"}


def _fail(code: str, message: str) -> None:
    raise QualificationError(code, message)


def _closed(value: Any, fields: set[str], code: str = "review_schema_mismatch") -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code, "Review object does not match its closed schema.")
    return value


def _private_identity(source: dict[str, Any]) -> dict[str, Any]:
    return {field: source[field] for field in ("expected_item_id", "subject_id", "record_fingerprint", "profile_hash", "audio_identity")}


def _label(seed: bytes, private: dict[str, Any]) -> str:
    return "sample-" + hmac.new(seed, canonical_bytes(private), hashlib.sha256).hexdigest()[:16]


def _audio_handle(seed: bytes, private: dict[str, Any]) -> str:
    return "audio-" + hmac.new(seed, b"audio:" + canonical_bytes(private), hashlib.sha256).hexdigest()[:16]


def _item_set_hash(private_items: list[dict[str, Any]]) -> str:
    return canonical_hash(sorted(private_items, key=canonical_bytes))


def build_review_package(items: list[dict[str, Any]], seed: str) -> dict[str, dict[str, Any]]:
    if not isinstance(seed, str) or not seed:
        _fail("invalid_review_seed", "Review seed must be non-empty text.")
    if not isinstance(items, list) or not items:
        _fail("empty_review_package", "A review package must contain items.")
    seed_bytes = seed.encode("utf-8")
    keyed: list[tuple[bytes, str, dict[str, Any], dict[str, Any]]] = []
    labels: set[str] = set()
    private_items: list[dict[str, Any]] = []
    for raw in items:
        source = _closed(raw, _SOURCE_FIELDS)
        private = _private_identity(source)
        _identifier(private["expected_item_id"], "expected review item ID")
        _identifier(private["subject_id"], "review subject ID")
        _digest(private["record_fingerprint"], "review record fingerprint")
        _digest(private["profile_hash"], "review profile hash")
        if not isinstance(source["audio_identity"], str) or not source["audio_identity"]:
            _fail("invalid_review_item", "Review audio identity must be non-empty text.")
        if not isinstance(source["required_playback"], bool) or not isinstance(source["restriction_options"], list) or len(source["restriction_options"]) != len(set(source["restriction_options"])):
            _fail("invalid_review_item", "Playback and restriction declarations are invalid.")
        for restriction in source["restriction_options"]:
            _identifier(restriction, "review restriction option")
        label = _label(seed_bytes, private)
        if label in labels:
            _fail("duplicate_review_item", "Review expected-item identities must be unique.")
        labels.add(label)
        private_items.append(private)
        order = hmac.new(seed_bytes, b"order:" + canonical_bytes(private), hashlib.sha256).digest()
        keyed.append((order, label, source, private))
    keyed.sort(key=lambda entry: (entry[0], entry[1]))
    item_set_hash = _item_set_hash(private_items)
    public_items = [
        {"label": label, "audio_identity": _audio_handle(seed_bytes, private), "required_playback": source["required_playback"], "restriction_options": list(source["restriction_options"])}
        for _, label, source, private in keyed
    ]
    labels_in_order = [item["label"] for item in public_items]
    seed_commitment = hashlib.sha256(seed_bytes).hexdigest()
    public = {
        "schema_version": SCHEMA_VERSION,
        "package_id": canonical_hash({"item_set_hash": item_set_hash, "seed_commitment": seed_commitment}),
        "item_set_hash": item_set_hash,
        "seed_commitment": seed_commitment,
        "rating_scale": {"minimum": 1, "maximum": 5, "fields": list(RATING_FIELDS)},
        "items": public_items,
        "incomplete_navigation": labels_in_order,
    }
    answer_key = {
        "schema_version": SCHEMA_VERSION,
        "package_hash": canonical_hash(public),
        "item_set_hash": item_set_hash,
        "seed_commitment": seed_commitment,
        "seed": seed,
        "mappings": [{"label": label, **private} for _, label, _, private in keyed],
    }
    template = build_review_result({"public": public, "answer_key": answer_key}, [], reviewer_id="unassigned", nonce="unassigned")
    return {"public": public, "answer_key": answer_key, "result_template": template}


def validate_review_package(public: Any, answer_key: Any) -> None:
    package = _closed(public, {"schema_version", "package_id", "item_set_hash", "seed_commitment", "rating_scale", "items", "incomplete_navigation"})
    key = _closed(answer_key, {"schema_version", "package_hash", "item_set_hash", "seed_commitment", "seed", "mappings"})
    if package["schema_version"] != SCHEMA_VERSION or key["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported_schema", "Review schema version is unsupported.")
    if key["package_hash"] != canonical_hash(package) or key["item_set_hash"] != package["item_set_hash"] or key["seed_commitment"] != package["seed_commitment"]:
        _fail("review_key_mismatch", "Answer key is not bound to the public package.")
    if not isinstance(key["seed"], str) or hashlib.sha256(key["seed"].encode("utf-8")).hexdigest() != package["seed_commitment"]:
        _fail("review_key_mismatch", "Answer key seed does not match its commitment.")
    scale = _closed(package["rating_scale"], {"minimum", "maximum", "fields"})
    if scale != {"minimum": 1, "maximum": 5, "fields": list(RATING_FIELDS)}:
        _fail("review_schema_mismatch", "Review rating scale is not the locked V1 scale.")
    forbidden = {"subject_id", "engine_id", "component_id", "source_identity", "expected_item_id", "record_fingerprint", "profile_hash", "seed"}
    if not isinstance(package["items"], list) or any(not isinstance(item, dict) for item in package["items"]):
        _fail("review_schema_mismatch", "Public review items must be objects.")
    if forbidden & set().union(*(set(item) for item in package["items"])):
        _fail("public_identity_leak", "Public review data contains private identity fields.")
    public_items = [_closed(item, _PUBLIC_ITEM_FIELDS) for item in package["items"]]
    mappings = [_closed(item, _MAPPING_FIELDS) for item in key["mappings"]]
    for item in public_items:
        if not isinstance(item["label"], str) or not item["label"].startswith("sample-"):
            _fail("review_key_mismatch", "Public review label is invalid.")
        if not isinstance(item["audio_identity"], str) or re.fullmatch(r"audio-[0-9a-f]{16}", item["audio_identity"]) is None:
            _fail("public_identity_leak", "Public review audio must use an opaque handle.")
        if not isinstance(item["required_playback"], bool) or not isinstance(item["restriction_options"], list) or len(item["restriction_options"]) != len(set(item["restriction_options"])):
            _fail("review_schema_mismatch", "Public playback and restriction declarations are invalid.")
        for restriction in item["restriction_options"]:
            _identifier(restriction, "review restriction option")
    for mapping in mappings:
        if not isinstance(mapping["label"], str) or not mapping["label"].startswith("sample-"):
            _fail("review_key_mismatch", "Answer-key review label is invalid.")
        _identifier(mapping["expected_item_id"], "expected review item ID")
        _identifier(mapping["subject_id"], "review subject ID")
        _digest(mapping["record_fingerprint"], "review record fingerprint")
        _digest(mapping["profile_hash"], "review profile hash")
        if not isinstance(mapping["audio_identity"], str) or not mapping["audio_identity"]:
            _fail("review_key_mismatch", "Answer-key audio identity is missing.")
    private_items = [{field: item[field] for field in _MAPPING_FIELDS if field != "label"} for item in mappings]
    if _item_set_hash(private_items) != package["item_set_hash"]:
        _fail("review_key_mismatch", "Answer-key item set is invalid.")
    seed_bytes = key["seed"].encode("utf-8")
    expected = sorted([(
        hmac.new(seed_bytes, b"order:" + canonical_bytes(private), hashlib.sha256).digest(),
        _label(seed_bytes, private),
    ) for private in private_items], key=lambda entry: (entry[0], entry[1]))
    expected_labels = [entry[1] for entry in expected]
    if [item["label"] for item in public_items] != expected_labels or [item["label"] for item in mappings] != expected_labels or package["incomplete_navigation"] != expected_labels or len(set(expected_labels)) != len(expected_labels):
        _fail("review_key_mismatch", "Review labels or deterministic order do not match the secret seed.")
    if [item["audio_identity"] for item in public_items] != [_audio_handle(seed_bytes, private) for _, private in zip(expected_labels, private_items, strict=True)]:
        _fail("public_identity_leak", "Public review audio handle does not match its private mapping.")


def build_review_result(package: dict[str, dict[str, Any]], votes: list[dict[str, Any]], *, reviewer_id: str = "fixture_reviewer", nonce: str = "fixture_nonce") -> dict[str, Any]:
    public = package["public"]
    key = package["answer_key"]
    validate_review_package(public, key)
    known = [item["label"] for item in public["items"]]
    public_by_label = {item["label"]: item for item in public["items"]}
    _identifier(reviewer_id, "reviewer ID")
    if not isinstance(nonce, str) or not nonce:
        _fail("invalid_reviewer_identity", "Reviewer identity and nonce are required.")
    seen: set[str] = set()
    for raw in votes:
        vote = _closed(raw, _VOTE_FIELDS)
        if vote["label"] not in known or vote["label"] in seen:
            _fail("invalid_review_vote_set", "Review vote label is unknown or duplicated.")
        seen.add(vote["label"])
        for field in RATING_FIELDS:
            rating = vote[field]
            if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
                _fail("invalid_review_rating", "Review ratings must be integers from one through five.")
        if not isinstance(vote["playback_complete"], bool) or (public_by_label[vote["label"]]["required_playback"] and not vote["playback_complete"]):
            _fail("incomplete_playback", "Votes requiring full playback must carry that evidence.")
        if not isinstance(vote["included"], bool) or (not vote["included"] and not vote["exclusion_reason"]) or (vote["included"] and vote["exclusion_reason"] is not None):
            _fail("invalid_review_inclusion", "Excluded votes require a reason.")
        if not isinstance(vote["restrictions"], list) or len(vote["restrictions"]) != len(set(vote["restrictions"])):
            _fail("invalid_review_restrictions", "Review restrictions must be ordered unique IDs.")
        for restriction in vote["restrictions"]:
            _identifier(restriction, "review restriction")
            if restriction not in public_by_label[vote["label"]]["restriction_options"]:
                _fail("invalid_review_restrictions", "Review restriction is not allowlisted for the item.")
        if vote["exclusion_reason"] is not None and not isinstance(vote["exclusion_reason"], str):
            _fail("invalid_review_inclusion", "Review exclusion reason must be text or null.")
        if not isinstance(vote["notes"], str) or len(vote["notes"]) > 4000:
            _fail("invalid_review_notes", "Notes must be inert bounded text data.")
    if [vote["label"] for vote in votes] != [label for label in known if label in seen]:
        _fail("invalid_review_vote_set", "Review votes must preserve public package order.")
    incomplete = [label for label in known if label not in seen]
    unsigned = {"schema_version": SCHEMA_VERSION, "package_hash": canonical_hash(public), "answer_key_hash": canonical_hash(key), "item_set_hash": public["item_set_hash"], "reviewer_id": reviewer_id, "nonce": nonce, "votes": votes, "incomplete_labels": incomplete}
    return {**unsigned, "result_hash": canonical_hash(unsigned)}


def validate_review_result(public: Any, answer_key: Any, result: Any) -> dict[str, Any]:
    validate_review_package(public, answer_key)
    value = _closed(result, {"schema_version", "package_hash", "answer_key_hash", "item_set_hash", "reviewer_id", "nonce", "votes", "incomplete_labels", "result_hash"})
    rebuilt = build_review_result({"public": public, "answer_key": answer_key}, value["votes"], reviewer_id=value["reviewer_id"], nonce=value["nonce"])
    if value != rebuilt:
        _fail("tampered_review", "Review result or immutable linkage is invalid.")
    return value


def _publish_directory(root: Path, artifacts: tuple[tuple[str, bytes], ...], publication_hash: str, *, interrupt_at: str | None = None) -> None:
    if root.name in {"", ".", ".."} or root.is_symlink() or root.parent.is_symlink():
        _fail("review_destination_not_empty", "Review package destination must be a caller-owned empty directory.")
    root.parent.mkdir(parents=True, exist_ok=True)
    try:
        parent = os.open(root.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise QualificationError("review_destination_not_empty", "Review package parent must be a safe directory.") from exc
    pending_name = f".pending-{root.name}-{publication_hash}"
    pending_owned = False
    try:
        if _entry_exists_at(parent, root.name):
            try:
                destination = _open_directory_at(parent, root.name)
            except OSError as exc:
                raise QualificationError("review_destination_not_empty", "Review package destination must be an empty directory.") from exc
            try:
                if os.listdir(destination):
                    _fail("review_destination_not_empty", "Review package destination must be empty.")
            finally:
                os.close(destination)
        if _entry_exists_at(parent, pending_name):
            _fail("review_destination_not_empty", "Review package pending destination already exists.")
        os.mkdir(pending_name, 0o700, dir_fd=parent)
        pending_owned = True
        directory = _open_directory_at(parent, pending_name)
        try:
            for name, payload in artifacts:
                _write_exclusive_at(directory, name, payload)
            os.fsync(directory)
        finally:
            os.close(directory)
        if interrupt_at == "before_rename":
            _fail("review_publication_interrupted", "Review package publication interrupted before rename.")
        if _entry_exists_at(parent, root.name):
            os.rmdir(root.name, dir_fd=parent)
        os.rename(pending_name, root.name, src_dir_fd=parent, dst_dir_fd=parent)
        pending_owned = False
        os.fsync(parent)
    finally:
        try:
            if pending_owned and _entry_exists_at(parent, pending_name):
                directory = _open_directory_at(parent, pending_name)
                try:
                    entries = set(os.listdir(directory))
                    expected = {name for name, _ in artifacts}
                    if not entries <= expected:
                        _fail("review_destination_not_empty", "Review pending directory contains foreign entries.")
                    for name in entries:
                        os.unlink(name, dir_fd=directory)
                    os.fsync(directory)
                finally:
                    os.close(directory)
                os.rmdir(pending_name, dir_fd=parent)
                os.fsync(parent)
        finally:
            os.close(parent)


def publish_review_package(public_output_dir: str | Path, answer_key_output_dir: str | Path, package: dict[str, dict[str, Any]], *, interrupt_at: str | None = None) -> None:
    _closed(package, {"public", "answer_key", "result_template"})
    validate_review_package(package["public"], package["answer_key"])
    validate_review_result(package["public"], package["answer_key"], package["result_template"])
    public_root = Path(public_output_dir)
    key_root = Path(answer_key_output_dir)
    public_absolute = Path(os.path.abspath(public_root))
    key_absolute = Path(os.path.abspath(key_root))
    if public_absolute == key_absolute or public_absolute.parent == key_absolute.parent or public_absolute in key_absolute.parents or key_absolute in public_absolute.parents:
        _fail("review_key_destination_unsafe", "Answer key requires a separate controlled destination outside the public package neighborhood.")
    package_hash = canonical_hash(package["public"])
    _publish_directory(key_root, (("answer-key.json", canonical_bytes(package["answer_key"])),), package_hash)
    _publish_directory(public_root, (("public.json", canonical_bytes(package["public"])), ("result-template.json", canonical_bytes(package["result_template"]))), package_hash, interrupt_at=interrupt_at)
