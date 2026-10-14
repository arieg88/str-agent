"""OpenAI and OpenAI-compatible servers (Ollama, vLLM, hosted open models) through the openai SDK.

Uses chat.completions.parse(response_format=<Pydantic model>): the SDK sends the model's JSON
schema and validates the answer. Servers differ in how strictly they enforce a schema, so the
prompt also carries the schema, and anything that does not validate is an LLMError, never a
repaired memo.

No max token setting is sent: OpenAI's newer models take max_completion_tokens, Ollama documents
max_tokens. A truncated answer still fails cleanly (LengthFinishReasonError).
"""

from __future__ import annotations

import json
from typing import Any

import openai
import pydantic

from stragent.config import LLMConfig
from stragent.llm.provider import LLMError, LLMUnavailableError, T, api_key, attempt_timeout
from stragent.models import LLMUsage

# The SDK requires some key; a local server ignores it.
NO_KEY = "not-needed"


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, cfg: LLMConfig, http_client: Any = None) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self.client = openai.OpenAI(
            api_key=api_key(cfg) or NO_KEY,
            base_url=cfg.base_url or None,
            max_retries=cfg.max_retries,
            timeout=cfg.timeout_seconds,
            http_client=http_client,
        )

    def generate(
        self, system: str, user: str, schema: type[T], timeout: float
    ) -> tuple[T, LLMUsage]:
        where = self.cfg.base_url or "OpenAI"
        try:
            completion = self.client.with_options(
                timeout=attempt_timeout(self.cfg, timeout)
            ).chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=schema,
            )
        except openai.LengthFinishReasonError as exc:
            raise LLMError("The memo was cut off by the model's output limit.") from exc
        except openai.ContentFilterFinishReasonError as exc:
            raise LLMError("The provider's content filter stopped the memo.") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMUnavailableError(f"{where} rejected the API key: {exc.message}") from exc
        except openai.APITimeoutError as exc:
            raise LLMError(f"{where} did not answer in time.") from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"Could not reach {where}: {exc}") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"{where} returned HTTP {exc.status_code}: {exc.message}") from exc
        except (pydantic.ValidationError, json.JSONDecodeError) as exc:
            raise LLMError("The model's answer is not a valid memo.") from exc
        except openai.OpenAIError as exc:
            raise LLMError(f"{where} call failed: {exc}") from exc

        usage = LLMUsage(
            input_tokens=completion.usage.prompt_tokens if completion.usage else None,
            output_tokens=completion.usage.completion_tokens if completion.usage else None,
        )
        if not completion.choices:
            raise LLMError("The provider returned no answer.")
        message = completion.choices[0].message
        if message.refusal:
            raise LLMError(f"The model declined to write the memo: {message.refusal}")
        if message.parsed is None:
            raise LLMError("The model's answer is not a valid memo.")
        return message.parsed, usage
