#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from model_registry import model_cache_status, model_spec  # noqa: E402


ROUND_ID = "b24_local_fish_s2_pro_integration_20260804"
COMPLETED_AT = "2026-08-04T14:56:00Z"
TARGET_VOICE = "ROZ FORRESTER"
TARGET_ROUTE = "roz_dry_banter"
SELECTED_CANDIDATE = "roz_forrester__fish_s2_pro_local"
SELECTED_SAMPLE = "ROZ02"
HOSTED_SAMPLE = "ROZ04"
LOCAL_MODEL_KEY = "mlx_fish_s2_pro"
LICENSE_SCOPE = "noncommercial_research"
DEFAULT_PROJECT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Alexandria"
    / "Projects"
    / "original-sin--e6286665"
)
DEFAULT_B18_DECISION = (
    ROOT / "benchmarks" / "b18_multivoice_archetype_screen_20260803_decision.json"
)
DEFAULT_OUTPUT = (
    ROOT / "benchmarks" / "b24_local_fish_s2_pro_integration_20260804_decision.json"
)
EVIDENCE_PATH = ".omo/evidence/b24-local-fish-s2-pro-integration-20260804.json"
LOCAL_TAG = (
    "Dry banter and professional sarcasm with clipped timing, restrained "
    "impatience, and guarded amusement."
)


class LocalFishIntegrationDecisionError(RuntimeError):
    pass


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalFishIntegrationDecisionError(
            f"Could not read {label}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise LocalFishIntegrationDecisionError(f"{label} must contain an object.")
    return value


def _selected_review_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    speaker = (value.get("speaker_decisions") or {}).get(TARGET_VOICE)
    if not isinstance(speaker, Mapping):
        raise LocalFishIntegrationDecisionError("B18 has no Roz decision.")
    if (
        speaker.get("status") != "approved"
        or speaker.get("primary_method") != "fish_s2_pro_local"
        or speaker.get("primary_candidate_id") != SELECTED_CANDIDATE
    ):
        raise LocalFishIntegrationDecisionError(
            "B18 does not select local Fish S2 Pro for Roz."
        )
    candidates = value.get("candidate_decisions")
    if not isinstance(candidates, list):
        raise LocalFishIntegrationDecisionError("B18 has no candidate decisions.")
    selected = next(
        (
            item
            for item in candidates
            if isinstance(item, Mapping)
            and item.get("sample_id") == SELECTED_SAMPLE
            and item.get("candidate_id") == SELECTED_CANDIDATE
        ),
        None,
    )
    if not isinstance(selected, Mapping):
        raise LocalFishIntegrationDecisionError("The reviewed Roz local Fish candidate is missing.")
    ratings = selected.get("ratings")
    if not isinstance(ratings, Mapping) or not ratings.get("artifact_free"):
        raise LocalFishIntegrationDecisionError("The reviewed Roz local Fish candidate is not artifact-free.")
    if [ratings.get(key) for key in ("identity", "delivery", "naturalness")] != [5, 4, 5]:
        raise LocalFishIntegrationDecisionError("The reviewed Roz local Fish scores changed.")
    return {
        "speaker_decision": dict(speaker),
        "candidate_decision": dict(selected),
    }


def prepare_decision(
    *,
    project_root: str | Path,
    b18_decision_path: str | Path,
    model_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    project = Path(project_root).expanduser().resolve()
    b18_path = Path(b18_decision_path).expanduser().resolve()
    voice_config = _read_json(project / "voice_config.json", "Voice configuration")
    current_decisions = _read_json(
        project / "voice_route_listening_decisions.json",
        "Voice listening decisions",
    )
    b18 = _read_json(b18_path, "B18 listening decision")
    review = _selected_review_contract(b18)
    status = dict(model_status or model_cache_status(LOCAL_MODEL_KEY))
    spec = model_spec(LOCAL_MODEL_KEY)
    if not status.get("cached") or status.get("revision") != spec.revision:
        raise LocalFishIntegrationDecisionError(
            "The pinned local Fish S2 Pro snapshot is not complete."
        )

    voice = voice_config.get(TARGET_VOICE)
    if not isinstance(voice, Mapping):
        raise LocalFishIntegrationDecisionError("ROZ FORRESTER Voice is missing.")
    policy = voice.get("responsive_backend_routing")
    routes = policy.get("routes") if isinstance(policy, Mapping) else None
    route = routes.get(TARGET_ROUTE) if isinstance(routes, Mapping) else None
    if not isinstance(route, Mapping):
        raise LocalFishIntegrationDecisionError("Roz dry-banter route is missing.")
    if route.get("backend") != "fish_s2_pro_cloud":
        raise LocalFishIntegrationDecisionError(
            "Roz dry-banter is no longer on the reviewed hosted Fish route."
        )
    hosted_control = route.get("control")
    if (
        not isinstance(hosted_control, Mapping)
        or hosted_control.get("reference_mode") != "inline_zero_shot"
        or hosted_control.get("api_model_header") != "s2.1-pro-free"
    ):
        raise LocalFishIntegrationDecisionError(
            "Roz hosted Fish fallback contract changed."
        )

    project_document = copy.deepcopy(current_decisions)
    project_document.update(
        {
            "round_id": ROUND_ID,
            "completed_at": COMPLETED_AT,
            "review_sha256": str(b18["review_sha256"]),
            "answer_key_sha256": str(b18["answer_key_sha256"]),
            "evidence_path": EVIDENCE_PATH,
        }
    )
    project_document["decisions"][TARGET_VOICE] = {
        "status": "approved",
        "primary_method": "fish_s2_pro_local",
        "primary_candidate_id": SELECTED_CANDIDATE,
        "summary": (
            "Local Fish S2 Pro was the strongest reviewed Roz dry-banter take at "
            "5 identity, 4 delivery, and 5 naturalness. It is now the primary "
            "noncommercial specialist; hosted S2.1 Pro Free remains the immediate "
            "specialist fallback and Qwen remains the final fallback."
        ),
        "production_action": "replace_route",
        "preserve_prior_routes": True,
        "route_key": TARGET_ROUTE,
        "approval_tier": "restricted_user_accepted",
        "evidence_sample_ids": [SELECTED_SAMPLE, HOSTED_SAMPLE],
        "unresolved_requirements": [],
    }
    local_control = {
        "prompt_mode": "full_alexandria_tag",
        "tag": LOCAL_TAG,
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 50,
        "max_tokens": 500,
        "chunk_length": 300,
        "speed": 1.0,
        "license_scope": LICENSE_SCOPE,
        "hosted_fallback": copy.deepcopy(dict(hosted_control)),
    }
    update = {
        "voice": TARGET_VOICE,
        "route_key": TARGET_ROUTE,
        "expected_configuration_fingerprint": str(
            voice.get("responsive_backend_configuration_fingerprint") or ""
        ),
        "backend": "fish_s2_pro_local",
        "identity_audio": str(route["identity_audio"]),
        "identity_audio_sha256": str(route["identity_audio_sha256"]),
        "identity_text": str(route["identity_text"]),
        "control": local_control,
        "effect_chain": route.get("effect_chain"),
        "approval_tier": "restricted_user_accepted",
        "clear_performance_reference": True,
    }
    return {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "completed_at": COMPLETED_AT,
        "route_evidence_round_id": ROUND_ID,
        "license_scope": LICENSE_SCOPE,
        "selected_candidate_id": SELECTED_CANDIDATE,
        "selected_sample_id": SELECTED_SAMPLE,
        "source_review_sha256": str(b18["review_sha256"]),
        "source_answer_key_sha256": str(b18["answer_key_sha256"]),
        "local_model": {
            "key": spec.key,
            "repo_id": spec.repo_id,
            "revision": spec.revision,
            "snapshot_path": status.get("snapshot_path"),
        },
        "review_contract": review,
        "route_updates": [update],
        "project_decision_document": project_document,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT))
    parser.add_argument("--b18-decision", default=str(DEFAULT_B18_DECISION))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    result = prepare_decision(
        project_root=args.project_root,
        b18_decision_path=args.b18_decision,
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "voice": TARGET_VOICE,
                "route": TARGET_ROUTE,
                "backend": "fish_s2_pro_local",
                "hosted_fallback": "fish_s2_pro_cloud",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
