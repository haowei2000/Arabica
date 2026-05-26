"""Runtime LLM API configuration guardrails.

Structure resolves model API access from exactly three process-level
environment variables:

- ``OPENAI__API_KEY``
- ``OPENAI__BASE_URL``
- ``OPENAI__MODEL``

User-facing app/model configuration may still store non-secret metadata such as
display names, token windows, prices, or feature flags, but it must not carry
runtime API credentials, endpoint URLs, or per-app model overrides.
"""

from typing import Any

OPENAI_LLM_ENV_VARS: tuple[str, str, str] = (
    "OPENAI__API_KEY",
    "OPENAI__BASE_URL",
    "OPENAI__MODEL",
)

RUNTIME_LLM_API_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "api_base",
        "api_key",
        "api_key_ref",
        "base_url",
        "llm",
        "llm_api_base",
        "llm_api_key",
        "llm_base_url",
        "llm_config",
        "llm_model",
        "model",
        "model_api_base",
        "model_api_key",
        "model_base_url",
        "model_name",
        "model_provider",
        "openai_api_base",
        "openai_api_key",
        "openai_base_url",
        "openai_model",
        "provider_api_key",
    }
)


def assert_no_runtime_llm_api_config(
    data: Any,
    *,
    location: str = "config",
) -> None:
    """Reject runtime LLM API config outside the OPENAI__ env contract."""
    blocked = sorted(_find_runtime_llm_api_config_paths(data, location))
    if not blocked:
        return

    keys = ", ".join(blocked)
    env_vars = ", ".join(OPENAI_LLM_ENV_VARS)
    raise ValueError(
        f"Runtime LLM API config is only allowed through {env_vars}: {keys}"
    )


def _find_runtime_llm_api_config_paths(data: Any, path: str) -> set[str]:
    if data is None:
        return set()
    if isinstance(data, dict):
        blocked: set[str] = set()
        for key, value in data.items():
            key_path = f"{path}.{key}"
            if str(key).lower() in RUNTIME_LLM_API_CONFIG_KEYS:
                blocked.add(key_path)
            blocked.update(_find_runtime_llm_api_config_paths(value, key_path))
        return blocked
    if isinstance(data, list):
        blocked = set()
        for index, value in enumerate(data):
            blocked.update(
                _find_runtime_llm_api_config_paths(value, f"{path}[{index}]")
            )
        return blocked
    return set()
