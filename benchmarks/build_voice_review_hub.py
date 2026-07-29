#!/usr/bin/env python3
"""Build a public-only localhost hub for the current voice-review round."""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path
from typing import Any

DEFAULT_SERVER_ROOT = Path("/private/tmp/alexandria-voice-review-server-root")
DEFAULT_SOURCE_REVIEW = Path("/private/tmp/alexandria-chris-roz-final-reference-review-v2")
DEFAULT_FISH_REVIEW = Path("/private/tmp/alexandria-fish-preferred-router-retest-v1")
HUB_DIRECTORY = "alexandria-voice-review-hub"


def load_manifest(root: Path, label: str) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    review_path = root / "review/index.html"
    if not manifest_path.is_file() or not review_path.is_file():
        raise FileNotFoundError(f"{label} review package is incomplete: {root}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} manifest must be a JSON object.")
    return payload


def copy_public_review(source_root: Path, target: Path) -> None:
    source = source_root / "review"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    forbidden = [path for path in target.rglob("*") if path.name == "private"]
    if forbidden:
        raise ValueError(f"Public review copy unexpectedly contains private paths: {forbidden}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-root", type=Path, default=DEFAULT_SERVER_ROOT)
    parser.add_argument("--source-review", type=Path, default=DEFAULT_SOURCE_REVIEW)
    parser.add_argument("--fish-review", type=Path, default=DEFAULT_FISH_REVIEW)
    args = parser.parse_args()

    server_root = args.server_root.expanduser().resolve()
    hub_root = server_root / HUB_DIRECTORY
    source_root = args.source_review.expanduser().resolve()
    fish_root = args.fish_review.expanduser().resolve()
    source = load_manifest(source_root, "Chris/Roz source-selection")
    fish = load_manifest(fish_root, "Fish preferred-router")

    source_count = int(source.get("candidate_count") or 0)
    fish_count = int(fish.get("sample_count") or fish.get("candidate_count") or 24)
    if source_count != 45:
        raise ValueError(f"Consolidated source review must contain 45 candidates, found {source_count}.")
    if fish_count != 24:
        raise ValueError(f"Preferred-router Fish review must contain 24 samples, found {fish_count}.")

    if server_root.exists():
        shutil.rmtree(server_root)
    hub_root.mkdir(parents=True, exist_ok=True)
    copy_public_review(source_root, hub_root / "source-review")
    copy_public_review(fish_root, hub_root / "fish-review")

    source_href = f"/{HUB_DIRECTORY}/source-review/"
    fish_href = f"/{HUB_DIRECTORY}/fish-review/"
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alexandria voice reviews</title>
<style>
:root{{font-family:Inter,system-ui,sans-serif;background:#f2eee6;color:#29241e}}*{{box-sizing:border-box}}body{{max-width:880px;margin:auto;padding:48px 24px}}h1{{font:600 38px/1.1 Georgia,serif;margin:.25rem 0 1rem}}p{{line-height:1.55;color:#675f55}}.cards{{display:grid;gap:14px;margin-top:28px}}a{{display:block;background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:20px;color:inherit;text-decoration:none}}a:hover,a:focus-visible{{border-color:#315c55;outline:2px solid #315c55;outline-offset:2px}}strong{{display:block;font-size:18px;margin-bottom:7px}}span{{color:#6b6358}}.status{{margin-top:26px;padding:14px 16px;border-left:4px solid #315c55;background:#e7ece9}}.count{{font-variant-numeric:tabular-nums;font-weight:700;color:#315c55}}@media(max-width:560px){{body{{padding:32px 16px}}h1{{font-size:32px}}}}
</style></head><body>
<p>Alexandria evaluation</p><h1>Voice review hub</h1>
<p>Complete the consolidated source-selection review first, then the Fish preferred-router retest. The source review combines the prior WavLM shortlist with only the non-duplicative ECAPA/Whisper-large-v3 finalists, so you will not need to repeat this review later.</p>
<div class="cards">
<a href="{html.escape(source_href)}"><strong>1. Chris and Roz consolidated source selection</strong><span><span class="count">{source_count}</span> blind candidates covering actor identity, canonical character delivery, and the optional T'Nia Miller style layer.</span></a>
<a href="{html.escape(fish_href)}"><strong>2. Fish preferred-router retest</strong><span><span class="count">{fish_count}</span> exact-text samples for Narrator, Benny, and Doctor using their preferred prompt-routing policies.</span></a>
</div>
<div class="status"><strong>Use this hub for the full test session</strong><span>Both reviews autosave independently and export separate JSON files. Only public review assets are copied into this server root; private answer keys remain outside it.</span></div>
</body></html>"""
    (hub_root / "index.html").write_text(document, encoding="utf-8")
    (server_root / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><meta http-equiv='refresh' content='0;url=/{HUB_DIRECTORY}/'><title>Alexandria voice reviews</title><a href='/{HUB_DIRECTORY}/'>Open the voice review hub</a>",
        encoding="utf-8",
    )

    manifest = {
        "schema_version": 1,
        "hub_id": "alexandria_voice_review_hub_20260729_v2",
        "server_root": str(server_root),
        "hub_root": str(hub_root),
        "source_review": {
            "source_root": str(source_root),
            "public_copy": str(hub_root / "source-review"),
            "href": source_href,
            "round_id": source.get("round_id"),
            "candidate_count": source_count,
        },
        "fish_review": {
            "source_root": str(fish_root),
            "public_copy": str(hub_root / "fish-review"),
            "href": fish_href,
            "round_id": fish.get("round_id"),
            "sample_count": fish_count,
        },
        "range_capable_server_required": True,
        "public_assets_only": True,
        "review_order": ["source_review", "fish_review"],
    }
    (hub_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    private_paths = [
        path
        for path in server_root.rglob("*")
        if "private" in path.relative_to(server_root).parts
    ]
    if private_paths:
        raise ValueError(f"Private paths leaked into the public server root: {private_paths}")
    print(json.dumps({"hub": str(hub_root / "index.html"), **manifest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
