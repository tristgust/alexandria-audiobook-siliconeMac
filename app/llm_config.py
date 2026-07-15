from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from llm_client import LLMClient


DEFAULT_BASE_URL = (
    "http://localhost:11434/v1"
)

DEFAULT_API_KEY = "local"

DEFAULT_MODEL_NAME = (
    "richardyoung/"
    "qwen3-14b-abliterated:Q8_0"
)

DEFAULT_BACKEND = "auto"
DEFAULT_CONTEXT_LENGTH = 40960
DEFAULT_KEEP_ALIVE = -1
DEFAULT_THINKING = False
DEFAULT_STRUCTURED_OUTPUT = True
DEFAULT_CORRECTIVE_RETRY = True
DEFAULT_TIMEOUT = 1800


TRUE_VALUES = frozenset(
    {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }
)

FALSE_VALUES = frozenset(
    {
        "0",
        "false",
        "no",
        "off",
        "disabled",
        "",
    }
)


def config_bool(
    value: Any,
    default: bool = False,
) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return default

    if isinstance(
        value,
        (int, float),
    ):
        return value != 0

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in TRUE_VALUES:
            return True

        if normalized in FALSE_VALUES:
            return False

    return default


def config_int(
    value: Any,
    default: int,
) -> int:
    try:
        return int(value)
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        return default


@dataclass(frozen=True)
class LLMRuntimeSettings:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = DEFAULT_API_KEY
    model_name: str = DEFAULT_MODEL_NAME
    backend: str = DEFAULT_BACKEND
    context_length: int = (
        DEFAULT_CONTEXT_LENGTH
    )
    keep_alive: int | str = (
        DEFAULT_KEEP_ALIVE
    )
    thinking: bool = DEFAULT_THINKING
    structured_output: bool = (
        DEFAULT_STRUCTURED_OUTPUT
    )
    corrective_retry: bool = (
        DEFAULT_CORRECTIVE_RETRY
    )
    timeout: int = DEFAULT_TIMEOUT

    def client_kwargs(
        self,
    ) -> dict[str, Any]:
        return asdict(self)


def runtime_settings_from_config(
    config: Mapping[str, Any] | None,
    *,
    default_model: str = (
        DEFAULT_MODEL_NAME
    ),
) -> LLMRuntimeSettings:
    if not isinstance(config, Mapping):
        config = {}

    llm_config = config.get(
        "llm",
        {},
    )

    if not isinstance(
        llm_config,
        Mapping,
    ):
        llm_config = {}

    return LLMRuntimeSettings(
        base_url=llm_config.get(
            "base_url",
            DEFAULT_BASE_URL,
        ),
        api_key=llm_config.get(
            "api_key",
            DEFAULT_API_KEY,
        ),
        model_name=llm_config.get(
            "model_name",
            default_model,
        ),
        backend=llm_config.get(
            "backend",
            DEFAULT_BACKEND,
        ),
        context_length=config_int(
            llm_config.get(
                "context_length",
                DEFAULT_CONTEXT_LENGTH,
            ),
            DEFAULT_CONTEXT_LENGTH,
        ),
        keep_alive=llm_config.get(
            "keep_alive",
            DEFAULT_KEEP_ALIVE,
        ),
        thinking=config_bool(
            llm_config.get(
                "thinking",
                DEFAULT_THINKING,
            ),
            DEFAULT_THINKING,
        ),
        structured_output=config_bool(
            llm_config.get(
                "structured_output",
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            DEFAULT_STRUCTURED_OUTPUT,
        ),
        corrective_retry=config_bool(
            llm_config.get(
                "corrective_retry",
                DEFAULT_CORRECTIVE_RETRY,
            ),
            DEFAULT_CORRECTIVE_RETRY,
        ),
        timeout=config_int(
            llm_config.get(
                "timeout",
                DEFAULT_TIMEOUT,
            ),
            DEFAULT_TIMEOUT,
        ),
    )


def build_runtime_client(
    config: Mapping[str, Any] | None,
    *,
    default_model: str = (
        DEFAULT_MODEL_NAME
    ),
) -> LLMClient:
    settings = runtime_settings_from_config(
        config,
        default_model=default_model,
    )

    return LLMClient(
        **settings.client_kwargs()
    )
