from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Mapping

from llm_config import (
    DEFAULT_MODEL_NAME,
    build_runtime_client,
)


def metric_rate(
    value: Any,
) -> str:
    if value is None:
        return "n/a"

    try:
        return (
            f"{float(value):.2f} tok/s"
        )
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        return "n/a"


def print_llm_metrics(
    label: str,
    result: Any,
) -> None:
    metrics = result.metrics

    print(
        f"  {label}: "
        f"backend={result.backend}, "
        f"validation={result.validation_mode}"
    )

    print(
        "  "
        f"prompt="
        f"{metrics.get('prompt_tokens', 'n/a')} "
        "tokens @ "
        f"{metric_rate(metrics.get('prompt_tokens_per_second'))}; "
        f"output="
        f"{metrics.get('output_tokens', 'n/a')} "
        "tokens @ "
        f"{metric_rate(metrics.get('output_tokens_per_second'))}"
    )


def completion_result_response(
    result: Any,
) -> SimpleNamespace:
    content = json.dumps(
        result.data,
        ensure_ascii=False,
    )

    message = SimpleNamespace(
        content=content,
        reasoning=None,
    )

    choice = SimpleNamespace(
        message=message,
        finish_reason=(
            result.metrics.get(
                "done_reason"
            )
            or "stop"
        ),
    )

    usage = SimpleNamespace(
        prompt_tokens=result.metrics.get(
            "prompt_tokens"
        ),
        completion_tokens=result.metrics.get(
            "output_tokens"
        ),
    )

    return SimpleNamespace(
        choices=[choice],
        usage=usage,
        alexandria_metrics=dict(result.metrics),
        alexandria_validation_mode=result.validation_mode,
        alexandria_backend=result.backend,
    )


class PersonaOpenAIAdapter:
    def __init__(
        self,
        runtime_client: Any,
    ) -> None:
        self.runtime_client = runtime_client

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
            )
        )

    @staticmethod
    def contract_for_messages(
        messages: list[dict[str, Any]],
    ) -> str:
        user_content = "\n".join(
            str(
                message.get(
                    "content",
                    "",
                )
            )
            for message in messages
            if message.get("role") == "user"
        )

        if (
            "Speakers to analyze:"
            in user_content
        ):
            return "alias"

        if (
            "Allowed speaker labels:"
            in user_content
            and "Script batch:"
            in user_content
        ):
            return "advanced_discovery"

        return "persona"

    def _create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 400,
        top_p: float | None = None,
        presence_penalty: float | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        del model
        del kwargs

        contract = self.contract_for_messages(
            messages
        )

        result = (
            self.runtime_client.complete_json(
                messages=messages,
                contract=contract,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                presence_penalty=(
                    presence_penalty
                ),
            )
        )

        print_llm_metrics(
            contract.replace(
                "_",
                " ",
            ).title(),
            result,
        )

        return completion_result_response(
            result
        )


class ScriptOpenAIAdapter:
    def __init__(
        self,
        runtime_client: Any,
        legacy_client: Any = None,
    ) -> None:
        self.runtime_client = runtime_client
        self.legacy_client = legacy_client
        self._warned_banned_tokens = False

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
            )
        )

    def _create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.6,
        top_p: float = 0.8,
        presence_penalty: float = 0.0,
        max_tokens: int = 4096,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        if self.legacy_client is not None:
            legacy_kwargs = {
                "model": getattr(
                    self.runtime_client,
                    "model_name",
                    model,
                ),
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p,
                "presence_penalty": (
                    presence_penalty
                ),
                "max_tokens": max_tokens,
            }

            if extra_body:
                legacy_kwargs[
                    "extra_body"
                ] = extra_body

            legacy_kwargs.update(kwargs)

            return (
                self.legacy_client
                .chat
                .completions
                .create(**legacy_kwargs)
            )

        options = dict(
            extra_body or {}
        )

        top_k = options.get("top_k")
        min_p = options.get("min_p")
        banned_tokens = options.get(
            "banned_tokens"
        )

        if (
            banned_tokens
            and not self._warned_banned_tokens
        ):
            print(
                "  WARNING: Native Ollama does not expose "
                "Alexandria's banned_tokens option; "
                "the configured list is ignored."
            )

            self._warned_banned_tokens = True

        result = (
            self.runtime_client.complete_json(
                messages=messages,
                contract="script",
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                top_k=top_k,
                min_p=min_p,
                presence_penalty=(
                    presence_penalty
                ),
                extra_body=extra_body,
            )
        )

        print_llm_metrics(
            "Structured response",
            result,
        )

        return completion_result_response(
            result
        )


def create_legacy_openai_client(
    runtime_client: Any,
) -> Any:
    from openai import OpenAI

    return OpenAI(
        base_url=runtime_client.base_url,
        api_key=runtime_client.api_key,
    )


def build_persona_client(
    config: Mapping[str, Any] | None,
    *,
    default_model: str = (
        DEFAULT_MODEL_NAME
    ),
    stage: str = "persona",
) -> tuple[Any, PersonaOpenAIAdapter]:
    runtime_client = build_runtime_client(
        config,
        default_model=default_model,
        stage=stage,
    )

    adapter = PersonaOpenAIAdapter(
        runtime_client
    )

    return runtime_client, adapter


def build_script_client(
    config: Mapping[str, Any] | None,
    *,
    default_model: str = (
        DEFAULT_MODEL_NAME
    ),
    stage: str = "script",
) -> tuple[Any, ScriptOpenAIAdapter]:
    runtime_client = build_runtime_client(
        config,
        default_model=default_model,
        stage=stage,
    )

    legacy_client = None

    if (
        runtime_client.backend
        == "openai-compatible"
    ):
        legacy_client = (
            create_legacy_openai_client(
                runtime_client
            )
        )

    adapter = ScriptOpenAIAdapter(
        runtime_client,
        legacy_client=legacy_client,
    )

    return runtime_client, adapter


def build_roster_client(
    config: Mapping[str, Any] | None,
    *,
    default_model: str = DEFAULT_MODEL_NAME,
    stage: str = "roster",
) -> Any:
    """Build the shared runtime used by roster and evidence stages."""
    return build_runtime_client(
        config,
        default_model=default_model,
        stage=stage,
    )


def build_review_client(
    base_url: str,
    api_key: str,
    model_name: str,
    llm_config: Mapping[str, Any] | None,
) -> tuple[ScriptOpenAIAdapter, Any]:
    effective_llm = dict(
        llm_config
        if isinstance(
            llm_config,
            Mapping,
        )
        else {}
    )

    effective_llm.update(
        {
            "base_url": base_url,
            "api_key": api_key,
            "model_name": model_name,
        }
    )

    runtime_client, adapter = (
        build_script_client(
            {
                "llm": effective_llm,
            },
            default_model=model_name,
            stage="review",
        )
    )

    if (
        runtime_client.backend
        == "ollama-native"
    ):
        runtime_client.preload()

    return adapter, runtime_client
