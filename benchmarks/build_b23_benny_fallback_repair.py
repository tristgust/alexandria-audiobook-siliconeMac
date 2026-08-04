#!/usr/bin/env python3
"""Build a minimal blind Benny fallback-reference comparison.

This is the Benny counterpart to Boundary 22's Doctor repair. It reuses the
two B18 sardonic-concern anchors and generates exactly three local Qwen
candidates from already strict-approved Benny references. The round performs
objective authored-text screening, exposes one best-or-none choice, and never
mutates production routing or the live project.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Any, Iterator, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import build_b22_doctor_fallback_repair as shared  # noqa: E402


ROUND_ID = "b23_benny_fallback_repair_20260804"
REQUEST_LABEL_PREFIX = "benny-fallback"
SAMPLE_ID_PREFIX = "BFR"
SEED = 130363
MAX_WORD_ERROR_RATE = 0.20
TARGET_TEXT = (
    "The only sight I want to see at the moment is the inside of a "
    "tumbler of whisky. Let’s go."
)
TARGET_INSTRUCTION = (
    "Sardonic concern with wry intelligence, quick emotional shifts, "
    "and guarded warmth."
)
DEFAULT_PROJECT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Alexandria"
    / "Projects"
    / "original-sin--e6286665"
)
DEFAULT_OUTPUT = Path("/Users/tristan/Downloads/b23_benny_fallback_repair_20260804")
B18_ROOT = (
    ROOT
    / ".omo"
    / "evidence"
    / "b18-multivoice-archetype-screen-20260803"
)
REFERENCE_SOURCE = B18_ROOT / "review" / "reference" / "ben_reference.wav"
REFERENCE_SHA256 = "c55f38318d12f590f0d42846602a66db8ace9417d7b27ea97e279ebd78b93356"


ANCHOR_SPECS: tuple[dict[str, str], ...] = (
    {
        "method": "b18_current_route_anchor",
        "source": str(B18_ROOT / "review" / "audio" / "BEN03.wav"),
        "sha256": "89f0bb4d675e7522f7f7eee607f09d31115b942b3982f354c6efca4fd3544586",
    },
    {
        "method": "b18_neutral_identity_anchor",
        "source": str(B18_ROOT / "review" / "audio" / "BEN05.wav"),
        "sha256": "e81dede5b91e3b239f8dfbc1edba0024f85bb63fa9bb018523e0b03ef09d46d8",
    },
)

REFERENCE_ROUTE_KEYS = (
    "benny_criminal_incredulous_concern",
    "benny_diary_buoyant_confidence",
    "approved_adaptation_04f10aa4c9c5a312",
)


class BennyFallbackRepairError(RuntimeError):
    pass


sha256_file = shared.sha256_file


def _benny_contract(project: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = shared._read_json(project / "voice_config.json", "Voice configuration")
    voice = config.get("BERNICE")
    if not isinstance(voice, dict):
        raise BennyFallbackRepairError("BERNICE Voice configuration is missing.")
    policy = voice.get("responsive_backend_routing")
    if not isinstance(policy, Mapping):
        raise BennyFallbackRepairError("BERNICE responsive routing is missing.")
    routes = policy.get("routes")
    if not isinstance(routes, Mapping):
        raise BennyFallbackRepairError("BERNICE route map is missing.")

    references: list[dict[str, Any]] = []
    for route_key in REFERENCE_ROUTE_KEYS:
        raw = routes.get(route_key)
        if not isinstance(raw, Mapping):
            raise BennyFallbackRepairError(f"Benny route is missing: {route_key}")
        if raw.get("backend") != "qwen3_instruction_controlled":
            raise BennyFallbackRepairError(
                f"Benny route is not a Qwen route: {route_key}"
            )
        if raw.get("approval_tier") != "strict" or not raw.get(
            "production_promotion_allowed"
        ):
            raise BennyFallbackRepairError(
                f"Benny route is not strict-approved: {route_key}"
            )
        if raw.get("effect_chain") is not None:
            raise BennyFallbackRepairError(
                f"Benny reference route unexpectedly uses processing: {route_key}"
            )
        source = shared._resolve_project_asset(
            project,
            raw.get("identity_audio"),
            f"Benny route {route_key}",
        )
        expected = str(raw.get("identity_audio_sha256") or "")
        shared._verify(source, expected, f"Benny route {route_key}")
        transcript = str(raw.get("identity_text") or "").strip()
        if not transcript:
            raise BennyFallbackRepairError(
                f"Benny route transcript is missing: {route_key}"
            )
        references.append(
            {
                "method": f"qwen_reference__{route_key}",
                "route_key": route_key,
                "audio_path": source,
                "audio_sha256": expected,
                "reference_text": transcript,
            }
        )
    return voice, references


def _write_benny_review_assets(review: Path) -> None:
    shared._write_review_assets_original(review)
    copied_reference = review / "reference" / "doctor_reference.mp3"
    benny_reference = review / "reference" / "benny_reference.wav"
    copied_reference.replace(benny_reference)
    html_path = review / "index.html"
    html = html_path.read_text(encoding="utf-8")
    replacements = {
        "Alexandria Doctor fallback repair": "Alexandria Benny fallback repair",
        "Alexandria · Boundary 22": "Alexandria · Boundary 23",
        "Doctor fallback repair": "Benny fallback repair",
        "Listen to the Doctor reference": "Listen to the Benny reference",
        "Doctor reference": "Benny reference",
        "reference/doctor_reference.mp3": "reference/benny_reference.wav",
    }
    for before, after in replacements.items():
        html = html.replace(before, after)
    html_path.write_text(html, encoding="utf-8")


@contextmanager
def _shared_contract() -> Iterator[None]:
    names = (
        "ROUND_ID",
        "REQUEST_LABEL_PREFIX",
        "SAMPLE_ID_PREFIX",
        "SEED",
        "MAX_WORD_ERROR_RATE",
        "TARGET_TEXT",
        "TARGET_INSTRUCTION",
        "REFERENCE_SOURCE",
        "REFERENCE_SHA256",
        "ANCHOR_SPECS",
        "REFERENCE_ROUTE_KEYS",
        "_doctor_contract",
        "_write_review_assets",
    )
    previous = {name: getattr(shared, name) for name in names}
    if not hasattr(shared, "_write_review_assets_original"):
        shared._write_review_assets_original = shared._write_review_assets
    try:
        shared.ROUND_ID = ROUND_ID
        shared.REQUEST_LABEL_PREFIX = REQUEST_LABEL_PREFIX
        shared.SAMPLE_ID_PREFIX = SAMPLE_ID_PREFIX
        shared.SEED = SEED
        shared.MAX_WORD_ERROR_RATE = MAX_WORD_ERROR_RATE
        shared.TARGET_TEXT = TARGET_TEXT
        shared.TARGET_INSTRUCTION = TARGET_INSTRUCTION
        shared.REFERENCE_SOURCE = REFERENCE_SOURCE
        shared.REFERENCE_SHA256 = REFERENCE_SHA256
        shared.ANCHOR_SPECS = ANCHOR_SPECS
        shared.REFERENCE_ROUTE_KEYS = REFERENCE_ROUTE_KEYS
        shared._doctor_contract = _benny_contract
        shared._write_review_assets = _write_benny_review_assets
        yield
    finally:
        for name, value in previous.items():
            setattr(shared, name, value)


def build_round(
    *,
    project_root: str | Path = DEFAULT_PROJECT,
    output_root: str | Path = DEFAULT_OUTPUT,
    replace: bool = False,
    generator: shared.Generator | None = None,
    evaluator: shared.Evaluator | None = None,
    verify_tracked_hashes: bool = True,
) -> dict[str, Any]:
    with _shared_contract():
        return shared.build_round(
            project_root=project_root,
            output_root=output_root,
            replace=replace,
            generator=generator,
            evaluator=evaluator,
            verify_tracked_hashes=verify_tracked_hashes,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build_round(
                project_root=args.project_root,
                output_root=args.output_root,
                replace=args.replace,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
