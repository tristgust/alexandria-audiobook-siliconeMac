from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from llm_schemas import validate_contract


class DynamicRosterRuntime:
    def __init__(
        self,
        *,
        fail_on_discovery_call: int | None = None,
        model_name: str = "qwen3.5:35b-mlx",
    ) -> None:
        self.model_name = model_name
        self.backend = "ollama-native"
        self.base_url = "http://localhost:11434/v1"
        self.thinking = False
        self.structured_output = True
        self.corrective_retry = True
        self.context_length = 40960
        self.keep_alive = -1
        self.timeout = 1800
        self.fail_on_discovery_call = fail_on_discovery_call
        self.discovery_calls = 0
        self.reconciliation_calls = 0
        self.contracts: list[str] = []

    def preload(self):
        return True, "preloaded"

    @staticmethod
    def _result(data: Any) -> SimpleNamespace:
        return SimpleNamespace(
            data=data,
            backend="ollama-native",
            validation_mode="direct",
            metrics={
                "prompt_tokens": 10,
                "output_tokens": 5,
                "prompt_tokens_per_second": 20.0,
                "output_tokens_per_second": 10.0,
                "done_reason": "stop",
            },
        )

    def complete_json(
        self,
        *,
        messages: list[dict[str, Any]],
        contract: str,
        **kwargs: Any,
    ) -> SimpleNamespace:
        del kwargs
        self.contracts.append(contract)
        user = "\n".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "user"
        )
        payload = json.loads(user)

        if contract == "roster_discovery":
            self.discovery_calls += 1
            if self.fail_on_discovery_call == self.discovery_calls:
                raise RuntimeError("simulated interruption")

            passage = payload["source_passage"]
            match = re.search(r"[A-Za-z]+", passage)

            if not match:
                data = {"entities": [], "warnings": []}
            else:
                quote = match.group(0)
                data = {
                    "entities": [
                        {
                            "identity_seed": "THE DOCTOR",
                            "canonical_name": "THE DOCTOR",
                            "display_name": "The Doctor",
                            "entity_kind": "character",
                            "speaking_status": "speaker",
                            "titles": ["Doctor"],
                            "aliases": [],
                            "nicknames": [],
                            "pronouns": [],
                            "species": [],
                            "relationships": [],
                            "voice_clues": [],
                            "sample_lines": [quote],
                            "confidence": 0.9,
                            "resolution_status": "resolved",
                            "unresolved_questions": [],
                            "evidence": [
                                {
                                    "quote": quote,
                                    "start_char": match.start(),
                                    "end_char": match.end(),
                                    "category": category,
                                    "confidence": 0.9,
                                    "basis": "explicit",
                                }
                                for category in (
                                    "name",
                                    "title",
                                    "speaking",
                                )
                            ],
                        }
                    ],
                    "warnings": [],
                }

            data = validate_contract(contract, data)

        elif contract == "roster_reconciliation":
            self.reconciliation_calls += 1
            observations = payload["observations"]
            groups: dict[str, list[str]] = {}

            for observation in observations:
                groups.setdefault(
                    observation["identity_seed"],
                    [],
                ).append(observation["observation_id"])

            entries = [
                {
                    "identity_seed": seed,
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "observation_ids": observation_ids,
                    "confidence": 0.9,
                    "resolution_status": "resolved",
                    "possible_duplicate_seeds": [],
                    "mistaken_merge_risk": False,
                    "unresolved_questions": [],
                }
                for seed, observation_ids in groups.items()
            ]
            data = validate_contract(
                contract,
                {
                    "entries": entries,
                    "duplicate_candidates": [],
                    "excluded_observation_ids": [],
                    "warnings": [],
                },
            )
        else:
            raise AssertionError(f"Unexpected contract: {contract}")

        return self._result(data)
