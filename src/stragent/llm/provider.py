"""The provider layer: one small interface, one class per API style.

The memo flow, the validator and the cache only ever see `Provider`. Which vendor ran is
decided by config/settings.toml ([llm] provider, model, base_url, api_key_env).
"""

from __future__ import annotations

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

from stragent.config import LLMConfig
from stragent.models import LLMUsage

T = TypeVar("T", bound=BaseModel)


class LLMUnavailableError(RuntimeError):
    """No provider configured, or no usable key (HTTP 503, code llm_unavailable)."""


class LLMError(RuntimeError):
    """The provider failed: timeout, HTTP error, refusal, truncated or malformed output
    (HTTP 502, code llm_error)."""


class Provider(Protocol):
    name: str
    model: str

    def generate(
        self, system: str, user: str, schema: type[T], timeout: float
    ) -> tuple[T, LLMUsage]:
        """One structured completion. `timeout` is the time left for this call in seconds,
        SDK retries included. Raises LLMError or LLMUnavailableError, nothing else."""
        ...


def attempt_timeout(cfg: LLMConfig, time_left: float) -> float:
    """Per-HTTP-attempt timeout so that the attempt and its retries fit in time_left."""
    return max(1.0, min(cfg.timeout_seconds, time_left / (cfg.max_retries + 1)))


def api_key(cfg: LLMConfig) -> str | None:
    """The key from the env var named in config; None when api_key_env is empty (local server)."""
    if not cfg.api_key_env:
        return None
    key = os.environ.get(cfg.api_key_env, "").strip()
    if not key:
        raise LLMUnavailableError(
            f"{cfg.api_key_env} is not set. Put it in .env (see .env.example) or the environment, "
            "or choose another provider in config/settings.toml [llm]."
        )
    return key


def make_provider(cfg: LLMConfig) -> Provider:
    if cfg.provider == "anthropic":
        from stragent.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if cfg.provider == "openai_compatible":
        from stragent.llm.openai_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(cfg)
    raise LLMUnavailableError(f"Unknown [llm] provider '{cfg.provider}'.")
