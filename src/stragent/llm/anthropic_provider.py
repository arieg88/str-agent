"""Claude through the anthropic SDK, with structured output (output_config.format).

messages.parse() validates the JSON before the stop reason can be checked, so a refusal or a
truncated answer would surface as a schema error. This calls messages.create() instead, checks
stop_reason first, then validates the text with the Pydantic schema.

No sampling parameters are sent: current Claude models reject non-default temperature/top_p.
"""

from __future__ import annotations

from typing import Any

import anthropic
import pydantic
from anthropic.types import OutputConfigParam

from stragent.config import LLMConfig
from stragent.llm.provider import LLMError, LLMUnavailableError, T, api_key, attempt_timeout
from stragent.models import LLMUsage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: LLMConfig, http_client: Any = None) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self.client = anthropic.Anthropic(
            api_key=api_key(cfg),
            base_url=cfg.base_url or None,
            max_retries=cfg.max_retries,
            timeout=cfg.timeout_seconds,
            http_client=http_client,
        )

    def generate(
        self, system: str, user: str, schema: type[T], timeout: float
    ) -> tuple[T, LLMUsage]:
        output_config: OutputConfigParam = {
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        }
        if self.cfg.effort:
            output_config["effort"] = self.cfg.effort
        try:
            response = self.client.with_options(
                timeout=attempt_timeout(self.cfg, timeout)
            ).messages.create(
                model=self.model,
                max_tokens=self.cfg.max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMUnavailableError(f"Anthropic rejected the API key: {exc.message}") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError("Anthropic did not answer in time.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach Anthropic: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic returned HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.AnthropicError as exc:
            raise LLMError(f"Anthropic call failed: {exc}") from exc

        usage = LLMUsage(
            input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
        )
        if response.stop_reason == "refusal":
            raise LLMError("The model declined to write the memo (stop_reason refusal).")
        if response.stop_reason == "max_tokens":
            raise LLMError(
                f"The memo was cut off at {self.cfg.max_output_tokens} output tokens "
                "(raise [llm] max_output_tokens)."
            )
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return schema.model_validate_json(text), usage
        except pydantic.ValidationError as exc:
            raise LLMError(
                f"The model's answer is not a valid memo: {exc.error_count()} errors."
            ) from exc
