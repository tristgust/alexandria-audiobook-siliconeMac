from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class OllamaRuntimeError(RuntimeError):
    """Raised when Alexandria cannot complete a native Ollama request."""


@dataclass(frozen=True)
class OllamaChatResult:
    content: str
    thinking: str | None
    done_reason: str | None
    model: str
    total_duration: int
    load_duration: int
    prompt_eval_duration: int
    prompt_eval_count: int
    eval_duration: int
    eval_count: int
    raw: dict[str, Any]

    @property
    def total_seconds(self) -> float:
        return self.total_duration / 1_000_000_000

    @property
    def load_seconds(self) -> float:
        return self.load_duration / 1_000_000_000

    @property
    def prompt_seconds(self) -> float:
        return self.prompt_eval_duration / 1_000_000_000

    @property
    def generation_seconds(self) -> float:
        return self.eval_duration / 1_000_000_000

    @property
    def prompt_tokens_per_second(self) -> float | None:
        if self.prompt_eval_duration <= 0:
            return None

        return self.prompt_eval_count / self.prompt_seconds

    @property
    def output_tokens_per_second(self) -> float | None:
        if self.eval_duration <= 0:
            return None

        return self.eval_count / self.generation_seconds


def native_root_from_openai_base_url(
    base_url: str,
) -> str | None:
    """Convert a local Ollama OpenAI URL into its native server root.

    Examples:
        http://localhost:11434/v1 -> http://localhost:11434
        http://127.0.0.1:11434/v1/ -> http://127.0.0.1:11434
    """
    try:
        parsed = urllib.parse.urlparse(base_url)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    hostname = (parsed.hostname or "").lower()

    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        return None

    if parsed.port not in {None, 11434}:
        return None

    path = parsed.path.rstrip("/")

    if path not in {"", "/v1"}:
        return None

    host = parsed.hostname or "localhost"

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    port = f":{parsed.port}" if parsed.port else ""

    return f"{parsed.scheme}://{host}{port}"


def is_local_ollama_base_url(base_url: str) -> bool:
    return native_root_from_openai_base_url(base_url) is not None


def _decode_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""

    return body.strip() or str(exc)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise OllamaRuntimeError(
            f"Ollama HTTP {exc.code}: {_decode_http_error(exc)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise OllamaRuntimeError(
            f"Could not connect to Ollama at {url}: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise OllamaRuntimeError(
            f"Ollama request timed out after {timeout} seconds"
        ) from exc

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaRuntimeError(
            "Ollama returned a non-JSON response"
        ) from exc

    if not isinstance(value, dict):
        raise OllamaRuntimeError(
            "Ollama returned an unexpected response type"
        )

    if value.get("error"):
        raise OllamaRuntimeError(str(value["error"]))

    return value


def _get_json(
    url: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise OllamaRuntimeError(
            f"Ollama HTTP {exc.code}: {_decode_http_error(exc)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise OllamaRuntimeError(
            f"Could not connect to Ollama at {url}: {exc.reason}"
        ) from exc

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaRuntimeError(
            "Ollama returned a non-JSON status response"
        ) from exc

    if not isinstance(value, dict):
        raise OllamaRuntimeError(
            "Ollama returned an unexpected status response"
        )

    return value


def preload_model(
    *,
    native_root: str,
    model: str,
    context_length: int = 40960,
    keep_alive: int | str = -1,
    timeout: int = 1800,
) -> dict[str, Any]:
    options: dict[str, Any] = {}

    if context_length > 0:
        options["num_ctx"] = context_length

    payload: dict[str, Any] = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": keep_alive,
    }

    if options:
        payload["options"] = options

    return _post_json(
        f"{native_root.rstrip('/')}/api/generate",
        payload,
        timeout=timeout,
    )


def unload_model(
    *,
    native_root: str,
    model: str,
    timeout: int = 300,
) -> dict[str, Any]:
    return _post_json(
        f"{native_root.rstrip('/')}/api/generate",
        {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        },
        timeout=timeout,
    )


def chat_json(
    *,
    native_root: str,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any] | None,
    think: bool = False,
    keep_alive: int | str = -1,
    context_length: int = 40960,
    max_tokens: int = 4096,
    temperature: float = 0.6,
    top_p: float | None = 0.8,
    top_k: int | None = None,
    min_p: float | None = None,
    presence_penalty: float | None = None,
    seed: int | None = None,
    extra_options: dict[str, Any] | None = None,
    timeout: int = 1800,
) -> OllamaChatResult:
    options: dict[str, Any] = {
        "num_ctx": context_length,
        "num_predict": max_tokens,
        "temperature": temperature,
    }

    if top_p is not None:
        options["top_p"] = top_p

    if top_k not in {None, 0}:
        options["top_k"] = top_k

    if min_p not in {None, 0, 0.0}:
        options["min_p"] = min_p

    if presence_penalty not in {None, 0, 0.0}:
        options["presence_penalty"] = presence_penalty

    if seed is not None and seed >= 0:
        options["seed"] = seed

    if extra_options:
        options.update(
            {
                key: value
                for key, value in extra_options.items()
                if value is not None
            }
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "keep_alive": keep_alive,
        "options": options,
    }

    if schema is not None:
        payload["format"] = schema

    raw = _post_json(
        f"{native_root.rstrip('/')}/api/chat",
        payload,
        timeout=timeout,
    )

    message = raw.get("message")

    if not isinstance(message, dict):
        raise OllamaRuntimeError(
            "Ollama response did not contain an assistant message"
        )

    content = message.get("content", "")

    if not isinstance(content, str):
        raise OllamaRuntimeError(
            "Ollama assistant content was not a string"
        )

    thinking = message.get("thinking")

    if thinking is not None and not isinstance(thinking, str):
        thinking = str(thinking)

    return OllamaChatResult(
        content=content,
        thinking=thinking,
        done_reason=raw.get("done_reason"),
        model=str(raw.get("model") or model),
        total_duration=int(raw.get("total_duration") or 0),
        load_duration=int(raw.get("load_duration") or 0),
        prompt_eval_duration=int(
            raw.get("prompt_eval_duration") or 0
        ),
        prompt_eval_count=int(raw.get("prompt_eval_count") or 0),
        eval_duration=int(raw.get("eval_duration") or 0),
        eval_count=int(raw.get("eval_count") or 0),
        raw=raw,
    )


def get_running_models(
    *,
    native_root: str,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    raw = _get_json(
        f"{native_root.rstrip('/')}/api/ps",
        timeout=timeout,
    )

    models = raw.get("models", [])

    if not isinstance(models, list):
        raise OllamaRuntimeError(
            "Ollama status response has no model list"
        )

    return [
        model
        for model in models
        if isinstance(model, dict)
    ]
