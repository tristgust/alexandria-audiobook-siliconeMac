from __future__ import annotations

import json
import re
import time

from dataclasses import dataclass
from typing import Any

from llm_schemas import (
    ContractValidationError,
    get_schema,
    validate_contract,
)
from ollama_runtime import (
    OllamaChatResult,
    chat_json,
    get_running_models,
    native_root_from_openai_base_url,
    preload_model,
    unload_model,
)
from llm_telemetry import (
    record_llm_failure,
    record_llm_request,
)


@dataclass(frozen=True)
class CompletionResult:
    data: Any
    content: str
    backend: str
    contract: str
    validation_mode: str
    metrics: dict[str, Any]
    raw_response: Any


def parse_json_content(content: str) -> Any:
    """Parse one JSON value from an LLM response.

    Handles thinking tags, fenced JSON, and harmless surrounding prose.
    """
    if not isinstance(content, str):
        raise ContractValidationError(
            "LLM response content was not a string"
        )

    text = content.strip()

    if not text:
        raise ContractValidationError(
            "LLM response content was empty"
        )

    text = re.sub(
        r"<think>[\s\S]*?</think>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<thinking>[\s\S]*?</thinking>",
        "",
        text,
        flags=re.IGNORECASE,
    )

    fenced = re.search(
        r"```(?:json)?\s*([\s\S]*?)```",
        text,
        flags=re.IGNORECASE,
    )

    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    starts = [
        index
        for index in (
            text.find("{"),
            text.find("["),
        )
        if index >= 0
    ]

    if not starts:
        raise ContractValidationError(
            "No JSON object or array was found in the response"
        )

    start = min(starts)
    decoder = json.JSONDecoder()

    try:
        value, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ContractValidationError(
            f"Response contained invalid JSON: {exc}"
        ) from exc

    return value


class LLMClient:
    """Shared Alexandria LLM interface.

    Native Ollama receives structured schemas and explicit thinking control.
    Other OpenAI-compatible servers continue through the OpenAI client.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        backend: str = "auto",
        context_length: int = 40960,
        keep_alive: int | str = -1,
        thinking: bool = False,
        structured_output: bool = True,
        corrective_retry: bool = True,
        timeout: int = 1800,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.backend_preference = backend
        self.context_length = context_length
        self.keep_alive = keep_alive
        self.thinking = thinking
        self.structured_output = structured_output
        self.corrective_retry = corrective_retry
        self.timeout = timeout

        detected_root = native_root_from_openai_base_url(
            base_url
        )

        if backend == "ollama":
            if detected_root is None:
                raise ValueError(
                    "Native Ollama was requested, but base_url does "
                    "not point to local Ollama"
                )

            self.native_root = detected_root
            self.backend = "ollama-native"

        elif backend == "openai":
            self.native_root = None
            self.backend = "openai-compatible"

        elif backend == "auto":
            self.native_root = detected_root
            self.backend = (
                "ollama-native"
                if detected_root
                else "openai-compatible"
            )

        else:
            raise ValueError(
                "backend must be 'auto', 'ollama', or 'openai'"
            )

        self._openai_client: Any = None
        self.last_preload_result: (
            dict[str, Any] | None
        ) = None
        self.last_unload_result: (
            dict[str, Any] | None
        ) = None

    def _get_openai_client(self) -> Any:
        if self._openai_client is None:
            from openai import OpenAI

            self._openai_client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
            )

        return self._openai_client

    @staticmethod
    def _canonical_model_name(
        value: Any,
    ) -> str:
        if not isinstance(value, str):
            return ""

        normalized = value.strip()

        if normalized.endswith(":latest"):
            return normalized[:-7]

        return normalized

    def _find_running_model(
        self,
        models: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        requested = self._canonical_model_name(
            self.model_name
        )

        for model in models:
            candidates = (
                model.get("name"),
                model.get("model"),
            )

            if any(
                self._canonical_model_name(
                    candidate
                )
                == requested
                for candidate in candidates
            ):
                return model

        return None

    @staticmethod
    def _processor_placement(
        model: dict[str, Any] | None,
    ) -> str | None:
        if not isinstance(model, dict):
            return None

        size = model.get("size")
        size_vram = model.get("size_vram")

        if not isinstance(size_vram, (int, float)):
            return None

        if size_vram <= 0:
            return "cpu"

        if (
            isinstance(size, (int, float))
            and size > 0
            and size_vram < size * 0.95
        ):
            return "mixed"

        return "gpu"

    def preload(self) -> tuple[bool, str]:
        self.last_preload_result = None

        if self.native_root is None:
            return (
                False,
                "Preload skipped for non-Ollama backend",
            )

        try:
            result = preload_model(
                native_root=self.native_root,
                model=self.model_name,
                context_length=self.context_length,
                keep_alive=self.keep_alive,
                timeout=self.timeout,
            )
        except Exception as exc:
            return False, f"Ollama preload failed: {exc}"

        self.last_preload_result = result
        done_reason = result.get("done_reason")

        return (
            True,
            (
                f"Preloaded {self.model_name}"
                + (
                    f" ({done_reason})"
                    if done_reason
                    else ""
                )
            ),
        )

    def unload(self) -> tuple[bool, str]:
        self.last_unload_result = None

        if self.native_root is None:
            return (
                False,
                "Unload skipped for non-Ollama backend",
            )

        try:
            result = unload_model(
                native_root=self.native_root,
                model=self.model_name,
                timeout=min(
                    self.timeout,
                    300,
                ),
            )
        except Exception as exc:
            return False, f"Ollama unload failed: {exc}"

        self.last_unload_result = result
        done_reason = result.get("done_reason")

        return (
            True,
            (
                f"Unloaded {self.model_name}"
                + (
                    f" ({done_reason})"
                    if done_reason
                    else ""
                )
            ),
        )

    def running_models(self) -> list[dict[str, Any]]:
        if self.native_root is None:
            return []

        return get_running_models(
            native_root=self.native_root
        )

    def status(self) -> dict[str, Any]:
        supports_lifecycle = (
            self.native_root is not None
        )

        result: dict[str, Any] = {
            "model_name": self.model_name,
            "base_url": self.base_url,
            "backend_preference": (
                self.backend_preference
            ),
            "backend": self.backend,
            "native_ollama": (
                self.backend == "ollama-native"
            ),
            "supports_lifecycle": (
                supports_lifecycle
            ),
            "context_length": self.context_length,
            "keep_alive": self.keep_alive,
            "thinking": self.thinking,
            "structured_output": (
                self.structured_output
            ),
            "corrective_retry": (
                self.corrective_retry
            ),
            "timeout": self.timeout,
            "loaded": None,
            "warm": None,
            "processor_placement": None,
            "active_model": None,
            "running_models": [],
            "status_error": None,
        }

        if not supports_lifecycle:
            return result

        try:
            models = self.running_models()
        except Exception as exc:
            result["status_error"] = str(exc)
            return result

        active_model = self._find_running_model(
            models
        )

        result.update(
            {
                "loaded": active_model is not None,
                "warm": active_model is not None,
                "processor_placement": (
                    self._processor_placement(
                        active_model
                    )
                ),
                "active_model": active_model,
                "running_models": models,
            }
        )

        return result

    @staticmethod
    def _metrics_from_ollama(
        result: OllamaChatResult,
    ) -> dict[str, Any]:
        return {
            "model": result.model,
            "done_reason": result.done_reason,
            "thinking_present": bool(
                result.thinking
                and result.thinking.strip()
            ),
            "total_seconds": result.total_seconds,
            "load_seconds": result.load_seconds,
            "prompt_seconds": result.prompt_seconds,
            "prompt_tokens": result.prompt_eval_count,
            "prompt_tokens_per_second": (
                result.prompt_tokens_per_second
            ),
            "generation_seconds": (
                result.generation_seconds
            ),
            "output_tokens": result.eval_count,
            "output_tokens_per_second": (
                result.output_tokens_per_second
            ),
        }

    def _parse_and_validate(
        self,
        *,
        contract: str,
        content: str,
    ) -> Any:
        parsed = parse_json_content(content)
        return validate_contract(contract, parsed)

    @staticmethod
    def _corrective_message(
        *,
        contract: str,
        schema: dict[str, Any],
        validation_error: Exception,
    ) -> str:
        roster_guidance = ""
        if contract == "roster_discovery":
            roster_guidance = (
                "\n\nRoster discovery correction requirements:\n"
                '- The top level must be exactly {"entities": [...], '
                '"warnings": []}. Never use a roster_discovery wrapper.\n'
                "- Every entity must include every field required by the "
                "schema; never use entity_id in place of identity_seed.\n"
                "- Return compact one-line JSON without indentation or "
                "repeated whitespace.\n"
                "- Use empty arrays for unsupported optional fields.\n"
                "- Every optional claim field is always a JSON array of "
                "strings; never a bare string, object, or null.\n"
                "- Entity confidence and every evidence confidence must "
                "each be an unquoted finite JSON number from 0.0 through "
                "1.0; never use strings, labels, NaN, or Infinity.\n"
                "- Include at most one sample line per entity.\n"
                "- sample_lines must be exactly [] or [one exact source "
                "string].\n"
                "- Include no redundant evidence records; retain one exact "
                "evidence record for every category required by each "
                "populated claim.\n"
                "- Do not omit a materially distinct supported entity only "
                "to shorten the response.\n"
                "- Evidence start_char and end_char must be JSON integers "
                "with 0 <= start_char < end_char <= the supplied passage's "
                "Unicode code-point length.\n"
                "- For every nonempty exact evidence quote, end_char must "
                "equal start_char plus the exact quote's Unicode code-point "
                "length."
            )
        return (
            "Your previous response violated the required "
            f"{contract} JSON contract.\n\n"
            "Validation error:\n"
            f"{validation_error}\n\n"
            "Return ONLY corrected JSON matching this exact "
            "schema. Do not explain the correction and do not "
            "include markdown:"
            f"{roster_guidance}\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )

    def _complete_native(
        self,
        *,
        messages: list[dict[str, str]],
        contract: str,
        temperature: float,
        max_tokens: int,
        top_p: float | None,
        top_k: int | None,
        min_p: float | None,
        presence_penalty: float | None,
        seed: int | None,
        extra_options: dict[str, Any] | None,
    ) -> CompletionResult:
        if self.native_root is None:
            raise RuntimeError(
                "Native Ollama root is unavailable"
            )

        schema = (
            get_schema(contract)
            if self.structured_output
            else None
        )

        first = chat_json(
            native_root=self.native_root,
            model=self.model_name,
            messages=messages,
            schema=schema,
            think=self.thinking,
            keep_alive=self.keep_alive,
            context_length=self.context_length,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            presence_penalty=presence_penalty,
            seed=seed,
            extra_options=extra_options,
            timeout=self.timeout,
        )

        first_validation_started = time.perf_counter()
        try:
            data = self._parse_and_validate(
                contract=contract,
                content=first.content,
            )
            first_validation_seconds = (
                time.perf_counter()
                - first_validation_started
            )
            metrics = self._metrics_from_ollama(first)
            metrics["schema_validation_seconds"] = (
                first_validation_seconds
            )
            metrics["corrective_retry_count"] = 0

            return CompletionResult(
                data=data,
                content=first.content,
                backend=self.backend,
                contract=contract,
                validation_mode="direct",
                metrics=metrics,
                raw_response=first.raw,
            )

        except Exception as first_error:
            first_validation_seconds = (
                time.perf_counter()
                - first_validation_started
            )
            if not self.corrective_retry:
                raise ContractValidationError(
                    f"Initial {contract} response failed: "
                    f"{first_error}"
                ) from first_error

            correction_messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": first.content,
                },
                {
                    "role": "user",
                    "content": self._corrective_message(
                        contract=contract,
                        schema=get_schema(contract),
                        validation_error=first_error,
                    ),
                },
            ]

            second = chat_json(
                native_root=self.native_root,
                model=self.model_name,
                messages=correction_messages,
                schema=schema,
                think=self.thinking,
                keep_alive=self.keep_alive,
                context_length=self.context_length,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                min_p=min_p,
                presence_penalty=presence_penalty,
                seed=seed,
                extra_options=extra_options,
                timeout=self.timeout,
            )

            second_validation_started = time.perf_counter()
            try:
                data = self._parse_and_validate(
                    contract=contract,
                    content=second.content,
                )
            except Exception as second_error:
                raise ContractValidationError(
                    f"Initial {contract} response failed: "
                    f"{first_error}; corrective retry failed: "
                    f"{second_error}"
                ) from second_error

            second_validation_seconds = (
                time.perf_counter()
                - second_validation_started
            )
            first_metrics = self._metrics_from_ollama(first)
            metrics = self._metrics_from_ollama(second)
            for key in (
                "total_seconds",
                "load_seconds",
                "prompt_seconds",
                "generation_seconds",
                "prompt_tokens",
                "output_tokens",
            ):
                metrics[key] = (
                    first_metrics.get(key, 0)
                    + metrics.get(key, 0)
                )
            if metrics["prompt_seconds"] > 0:
                metrics["prompt_tokens_per_second"] = (
                    metrics["prompt_tokens"]
                    / metrics["prompt_seconds"]
                )
            if metrics["generation_seconds"] > 0:
                metrics["output_tokens_per_second"] = (
                    metrics["output_tokens"]
                    / metrics["generation_seconds"]
                )
            metrics["initial_validation_error"] = str(
                first_error
            )
            metrics["schema_validation_seconds"] = (
                first_validation_seconds
                + second_validation_seconds
            )
            metrics["corrective_retry_count"] = 1

            return CompletionResult(
                data=data,
                content=second.content,
                backend=self.backend,
                contract=contract,
                validation_mode="corrective_retry",
                metrics=metrics,
                raw_response={
                    "initial": first.raw,
                    "corrected": second.raw,
                },
            )

    def _complete_openai(
        self,
        *,
        messages: list[dict[str, str]],
        contract: str,
        temperature: float,
        max_tokens: int,
        top_p: float | None,
        presence_penalty: float | None,
        extra_body: dict[str, Any] | None,
    ) -> CompletionResult:
        client = self._get_openai_client()

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if top_p is not None:
            kwargs["top_p"] = top_p

        if presence_penalty is not None:
            kwargs["presence_penalty"] = (
                presence_penalty
            )

        if extra_body:
            kwargs["extra_body"] = extra_body

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        content = choice.message.content or ""

        validation_started = time.perf_counter()
        data = self._parse_and_validate(
            contract=contract,
            content=content,
        )
        schema_validation_seconds = (
            time.perf_counter()
            - validation_started
        )

        usage = getattr(response, "usage", None)

        metrics = {
            "model": self.model_name,
            "done_reason": getattr(
                choice,
                "finish_reason",
                None,
            ),
            "thinking_present": bool(
                getattr(
                    choice.message,
                    "reasoning",
                    None,
                )
            ),
            "prompt_tokens": getattr(
                usage,
                "prompt_tokens",
                None,
            ),
            "output_tokens": getattr(
                usage,
                "completion_tokens",
                None,
            ),
            "schema_validation_seconds": (
                schema_validation_seconds
            ),
            "corrective_retry_count": 0,
        }

        return CompletionResult(
            data=data,
            content=content,
            backend=self.backend,
            contract=contract,
            validation_mode="direct",
            metrics=metrics,
            raw_response=response,
        )

    def complete_json(
        self,
        *,
        messages: list[dict[str, str]],
        contract: str,
        temperature: float,
        max_tokens: int,
        top_p: float | None = 0.8,
        top_k: int | None = None,
        min_p: float | None = None,
        presence_penalty: float | None = None,
        seed: int | None = None,
        extra_options: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> CompletionResult:
        # Validate the contract name before making a request.
        get_schema(contract)

        started = time.perf_counter()

        try:
            if self.backend == "ollama-native":
                result = self._complete_native(
                    messages=messages,
                    contract=contract,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    top_k=top_k,
                    min_p=min_p,
                    presence_penalty=presence_penalty,
                    seed=seed,
                    extra_options=extra_options,
                )
            else:
                result = self._complete_openai(
                    messages=messages,
                    contract=contract,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    presence_penalty=presence_penalty,
                    extra_body=extra_body,
                )
        except Exception as exc:
            elapsed = (
                time.perf_counter()
                - started
            )

            record_llm_failure(
                model_name=self.model_name,
                contract=contract,
                backend=self.backend,
                request_elapsed_seconds=elapsed,
                error=str(exc),
                thinking=self.thinking,
                structured_output=(
                    self.structured_output
                ),
                corrective_retry=(
                    self.corrective_retry
                ),
            )

            raise

        elapsed = time.perf_counter() - started
        result.metrics["request_wall_seconds"] = elapsed

        record_llm_request(
            model_name=self.model_name,
            contract=contract,
            backend=result.backend,
            validation_mode=(
                result.validation_mode
            ),
            metrics=result.metrics,
            request_elapsed_seconds=elapsed,
            thinking=self.thinking,
            structured_output=(
                self.structured_output
            ),
            corrective_retry=(
                self.corrective_retry
            ),
        )

        return result
