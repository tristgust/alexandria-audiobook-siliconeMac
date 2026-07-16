from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from character_visuals import PROFILE_BUCKETS
from llm_schemas import validate_contract


class DynamicVisualRuntime:
    def __init__(
        self,
        *,
        fail_on_discovery_call: int | None = None,
        model_name: str = "qwen3.5:35b-mlx",
    ) -> None:
        self.model_name = model_name
        self.backend = "ollama-native"
        self.thinking = False
        self.structured_output = True
        self.corrective_retry = True
        self.context_length = 40960
        self.fail_on_discovery_call = (
            fail_on_discovery_call
        )
        self.discovery_calls = 0
        self.reconciliation_calls = 0
        self.contracts: list[str] = []

    def preload(self):
        return True, "preloaded"

    @staticmethod
    def _user(messages: list[dict[str, Any]]) -> str:
        return "\n".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "user"
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
        user = self._user(messages)

        if contract == "visual_discovery":
            self.discovery_calls += 1
            if (
                self.fail_on_discovery_call
                == self.discovery_calls
            ):
                raise RuntimeError("simulated visual interruption")
            characters = json.loads(
                user.split(
                    "APPROVED CHARACTERS:\n",
                    1,
                )[1].split("\n\nPASSAGE ", 1)[0]
            )
            passage = user.split(
                "SOURCE PASSAGE START\n",
                1,
            )[1].split(
                "\nSOURCE PASSAGE END",
                1,
            )[0]
            observations = []

            for character in characters:
                display = character["display_name"]
                if "Doctor" in display and "battered hat" in passage:
                    quote = "battered hat"
                    observations.append(
                        {
                            "character_id": character[
                                "character_id"
                            ],
                            "category": (
                                "accessories_weapons_equipment"
                            ),
                            "detail": "a battered hat",
                            "scope": "stable",
                            "certainty": 0.95,
                            "basis": "explicit",
                            "quote": quote,
                            "start_char": passage.index(quote),
                            "end_char": (
                                passage.index(quote) + len(quote)
                            ),
                        }
                    )

                if "Roz" in display and "dark hair" in passage:
                    quote = "dark hair"
                    observations.append(
                        {
                            "character_id": character[
                                "character_id"
                            ],
                            "category": "hair",
                            "detail": "dark hair",
                            "scope": "stable",
                            "certainty": 0.9,
                            "basis": "explicit",
                            "quote": quote,
                            "start_char": passage.index(quote),
                            "end_char": (
                                passage.index(quote) + len(quote)
                            ),
                        }
                    )

                if "Roz" in display and "red cloak" in passage:
                    quote = "red cloak"
                    observations.append(
                        {
                            "character_id": character[
                                "character_id"
                            ],
                            "category": "clothing",
                            "detail": "a red cloak",
                            "scope": "scene_specific",
                            "certainty": 0.85,
                            "basis": "explicit",
                            "quote": quote,
                            "start_char": passage.index(quote),
                            "end_char": (
                                passage.index(quote) + len(quote)
                            ),
                        }
                    )

            data = validate_contract(
                contract,
                {
                    "observations": observations,
                    "warnings": [],
                },
            )

        elif contract == "visual_reconciliation":
            self.reconciliation_calls += 1
            character_map = json.loads(
                user.split(
                    "APPROVED CHARACTERS:\n",
                    1,
                )[1].split(
                    "\n\nVALIDATED OBSERVATIONS:\n",
                    1,
                )[0]
            )
            observations = json.loads(
                user.split(
                    "\n\nVALIDATED OBSERVATIONS:\n",
                    1,
                )[1]
            )
            characters = []

            for character_id in character_map:
                profile = {
                    bucket: []
                    for bucket in PROFILE_BUCKETS
                }
                variants = []
                own = [
                    observation
                    for observation in observations
                    if observation["character_id"]
                    == character_id
                ]

                for observation in own:
                    if observation["scope"] == "stable":
                        profile[observation["category"]].append(
                            {
                                "detail": observation["detail"],
                                "certainty": observation[
                                    "certainty"
                                ],
                                "observation_ids": [
                                    observation["observation_id"]
                                ],
                            }
                        )
                    else:
                        variants.append(
                            {
                                "label": (
                                    observation["scope"]
                                    .replace("_", " ")
                                    .title()
                                ),
                                "scope": observation["scope"],
                                "details": [
                                    observation["detail"]
                                ],
                                "observation_ids": [
                                    observation["observation_id"]
                                ],
                            }
                        )

                characters.append(
                    {
                        "character_id": character_id,
                        "profile": profile,
                        "variants": variants,
                        "conflicts": [],
                        "unknowns": [],
                    }
                )

            data = validate_contract(
                contract,
                {
                    "characters": characters,
                    "warnings": [],
                },
            )
        else:
            raise AssertionError(
                f"Unexpected contract: {contract}"
            )

        return SimpleNamespace(
            data=data,
            content=json.dumps(data),
            backend=self.backend,
            validation_mode="direct",
            metrics={},
        )
