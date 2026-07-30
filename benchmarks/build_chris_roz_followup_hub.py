#!/usr/bin/env python3
"""Build a public-only hub for the Chris/Roz follow-up reviews."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("/private/tmp/alexandria-chris-roz-followup-hub")
REVIEWS = [
    (
        "source-repair",
        ROOT / ".omo/evidence/chris-canonical-reference-repair-v1/review",
        "1. Chris source-reference cleanup",
        "Score eleven technically valid cleanup variants for dryness, Chris identity, and naturalness.",
        11,
    ),
    (
        "repair-validation",
        ROOT / ".omo/evidence/chris-reference-repair-pairwise-v1/review",
        "2. Old versus repaired clone outputs",
        "Twelve matched pairs. The only changed factor is the old or repaired Chris identity reference.",
        12,
    ),
    (
        "model-tiebreakers",
        ROOT / ".omo/evidence/chris-roz-pairwise-v1/review",
        "3. Model tie-breakers",
        "Four matched Fish-versus-Index pairs for Chris dry humour and Roz neutral authority.",
        4,
    ),
    (
        "urgency-controls",
        ROOT / ".omo/evidence/chris-urgency-control-review-v1/review",
        "4. Chris urgency controls",
        "Twelve clean-reference candidates testing stronger urgency strategies across Fish, VoxCPM2, and IndexTTS2.",
        12,
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output_root).expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    cards = []
    manifest_reviews = []
    for slug, source, label, description, count in REVIEWS:
        if not (source / "index.html").is_file():
            raise FileNotFoundError(source / "index.html")
        target = output / slug
        shutil.copytree(source, target)
        cards.append(
            f"<a class='card' href='{slug}/'><span class='count'>{count}</span><h2>{label}</h2><p>{description}</p><strong>Open review →</strong></a>"
        )
        manifest_reviews.append({"slug": slug, "label": label, "item_count": count, "path": str(target)})
    (output / "index.html").write_text(
        """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Alexandria Chris and Roz follow-ups</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29241e;background:#f2eee6}*{box-sizing:border-box}body{margin:0}main{max-width:980px;margin:auto;padding:42px 22px}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;color:#70685e}h1,h2{font-family:Georgia,serif}h1{font-size:38px;margin:.3rem 0}.intro{max-width:760px;line-height:1.6}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:30px}.card{position:relative;display:block;text-decoration:none;color:inherit;background:#fffdf8;border:1px solid #d5cdbf;border-radius:12px;padding:22px;min-height:210px}.card:hover{border-color:#315c55}.card h2{margin:10px 0}.card p{line-height:1.5}.count{display:inline-grid;place-items:center;min-width:34px;height:34px;border-radius:99px;background:#315c55;color:white;font-weight:750}@media(max-width:700px){.grid{grid-template-columns:1fr}h1{font-size:31px}}</style></head><body><main><p class='eyebrow'>Alexandria blind listening</p><h1>Chris and Roz follow-up reviews</h1><p class='intro'>Complete these in order. The first two resolve the echoed Chris canonical reference. The final two settle the remaining model and urgency questions. Each page autosaves and exports its own JSON.</p><div class='grid'>"""
        + "".join(cards)
        + "</div></main></body></html>",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "hub_id": "alexandria_chris_roz_followup_hub_v1",
        "public_assets_only": True,
        "total_decisions": sum(row[4] for row in REVIEWS),
        "reviews": manifest_reviews,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    private = [str(path) for path in output.rglob("*") if "private" in path.relative_to(output).parts]
    if private:
        raise ValueError(f"Private paths leaked into hub: {private}")
    print(json.dumps({"hub": str(output / 'index.html'), **manifest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
