"""Both real providers against a mocked HTTP layer: the real SDK builds and parses every request,
nothing leaves the machine."""

import json

import httpx2
import pytest

from stragent.config import LLMConfig
from stragent.llm.anthropic_provider import AnthropicProvider
from stragent.llm.openai_provider import OpenAICompatibleProvider
from stragent.llm.provider import LLMError, LLMUnavailableError, attempt_timeout, make_provider
from stragent.models import MemoSections

SECTIONS = MemoSections(
    verdict="v", pricing="p", occupancy_revenue="o", competition="c", risks="r",
    checks_before_buying="k",
)  # fmt: skip
KEY_ENV = "STRAGENT_TEST_LLM_KEY"


@pytest.fixture(autouse=True)
def test_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "test-key")


def mock_client(handler, seen):
    def record(request):
        seen.append(request)
        return handler(request)

    return httpx2.Client(transport=httpx2.MockTransport(record))


# --- anthropic ------------------------------------------------------------------------------


def anthropic_cfg(**changes):
    base = dict(provider="anthropic", model="claude-haiku-5-5", api_key_env=KEY_ENV, max_retries=0)
    return LLMConfig(**{**base, **changes})


def claude_reply(text, stop_reason="end_turn", status=200):
    body = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 450},
    }
    return lambda request: httpx2.Response(status, json=body)


def claude(handler, **cfg):
    seen = []
    provider = AnthropicProvider(anthropic_cfg(**cfg), http_client=mock_client(handler, seen))
    return provider, seen


def test_anthropic_success_and_request_shape():
    provider, seen = claude(claude_reply(SECTIONS.model_dump_json()))
    sections, usage = provider.generate("SYSTEM", "USER", MemoSections, timeout=30)
    assert sections == SECTIONS
    assert (usage.input_tokens, usage.output_tokens) == (1200, 450)
    (request,) = seen
    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert body["model"] == "claude-haiku-5-5" and body["system"] == "SYSTEM"
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert set(body["output_config"]["format"]["schema"]["properties"]) == set(
        MemoSections.model_fields
    )
    assert body["output_config"]["effort"] == "medium"
    # current Claude models reject sampling parameters: none may be sent
    assert not {"temperature", "top_p", "top_k"} & body.keys()


def test_anthropic_effort_can_be_left_to_the_model():
    provider, seen = claude(claude_reply(SECTIONS.model_dump_json()), effort=None)
    provider.generate("s", "u", MemoSections, timeout=30)
    assert "effort" not in json.loads(seen[0].content)["output_config"]


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (claude_reply("", stop_reason="refusal"), "declined"),
        (claude_reply('{"verdict": "cut', stop_reason="max_tokens"), "cut off"),
        (claude_reply('{"verdict": "not json'), "not a valid memo"),
        (claude_reply('{"verdict": "only one field"}'), "not a valid memo"),
        (claude_reply("{}", status=500), "HTTP 500"),
        (claude_reply("{}", status=529), "HTTP 529"),
        (claude_reply("{}", status=429), "HTTP 429"),
    ],
)
def test_anthropic_failures_are_llm_errors(handler, message):
    provider, _ = claude(handler)
    with pytest.raises(LLMError, match=message):
        provider.generate("s", "u", MemoSections, timeout=30)


def test_anthropic_timeout_is_an_llm_error():
    def slow(request):
        raise httpx2.ReadTimeout("too slow", request=request)

    provider, _ = claude(slow)
    with pytest.raises(LLMError, match="in time"):
        provider.generate("s", "u", MemoSections, timeout=30)


def test_anthropic_bad_key_is_unavailable():
    provider, _ = claude(claude_reply("{}", status=401))
    with pytest.raises(LLMUnavailableError):
        provider.generate("s", "u", MemoSections, timeout=30)


def test_anthropic_retries_come_from_config():
    provider, seen = claude(claude_reply("{}", status=500), max_retries=1)
    provider.client = provider.client.with_options(max_retries=1)
    # the SDK waits between retries; keep the test fast
    provider.client._calculate_retry_timeout = lambda *a, **k: 0  # type: ignore[method-assign]
    with pytest.raises(LLMError):
        provider.generate("s", "u", MemoSections, timeout=30)
    assert len(seen) == 2
    assert AnthropicProvider(anthropic_cfg(max_retries=3)).client.max_retries == 3


def test_missing_key_is_unavailable(monkeypatch):
    monkeypatch.delenv(KEY_ENV)
    with pytest.raises(LLMUnavailableError, match=KEY_ENV):
        make_provider(anthropic_cfg())
    with pytest.raises(LLMUnavailableError, match=KEY_ENV):
        make_provider(anthropic_cfg(provider="openai_compatible"))


def test_attempt_timeout_fits_retries_in_the_time_left():
    cfg = anthropic_cfg(timeout_seconds=60, max_retries=1)
    assert attempt_timeout(cfg, 200) == 60
    assert attempt_timeout(cfg, 50) == 25
    assert attempt_timeout(cfg, 0) == 1


# --- openai_compatible ----------------------------------------------------------------------


def openai_cfg(**changes):
    base = dict(
        provider="openai_compatible",
        model="llama3.2",
        base_url="http://localhost:11434/v1/",
        api_key_env="",
        max_retries=0,
    )
    return LLMConfig(**{**base, **changes})


def chat_reply(content, finish_reason="stop", refusal=None, status=200):
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "llama3.2",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 300, "total_tokens": 1200},
    }
    return lambda request: httpx2.Response(status, json=body)


def chat(handler, **cfg):
    seen = []
    provider = OpenAICompatibleProvider(openai_cfg(**cfg), http_client=mock_client(handler, seen))
    return provider, seen


def test_openai_compatible_success_and_request_shape():
    provider, seen = chat(chat_reply(SECTIONS.model_dump_json()))
    sections, usage = provider.generate("SYSTEM", "USER", MemoSections, timeout=30)
    assert sections == SECTIONS
    assert (usage.input_tokens, usage.output_tokens) == (900, 300)
    (request,) = seen
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == "llama3.2"
    assert body["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "USER"},
    ]
    assert body["response_format"]["type"] == "json_schema"
    assert "verdict" in body["response_format"]["json_schema"]["schema"]["properties"]


def test_openai_compatible_sends_the_configured_key():
    provider, seen = chat(chat_reply(SECTIONS.model_dump_json()), api_key_env=KEY_ENV, base_url="")
    provider.generate("s", "u", MemoSections, timeout=30)
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert seen[0].url.host == "api.openai.com"


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (chat_reply(None, refusal="I can't help with that."), "declined"),
        (chat_reply('{"verdict": "cut', finish_reason="length"), "cut off"),
        (chat_reply("x", finish_reason="content_filter"), "content filter"),
        (chat_reply('{"verdict": "not json'), "not a valid memo"),
        (chat_reply('{"verdict": "only one field"}'), "not a valid memo"),
        (chat_reply("{}", status=500), "HTTP 500"),
    ],
)
def test_openai_compatible_failures_are_llm_errors(handler, message):
    provider, _ = chat(handler)
    with pytest.raises(LLMError, match=message):
        provider.generate("s", "u", MemoSections, timeout=30)


def test_openai_compatible_unreachable_server_is_an_llm_error():
    def down(request):
        raise httpx2.ConnectError("connection refused", request=request)

    provider, _ = chat(down)
    with pytest.raises(LLMError, match="Could not reach http://localhost:11434"):
        provider.generate("s", "u", MemoSections, timeout=30)


def test_openai_compatible_bad_key_is_unavailable():
    provider, _ = chat(chat_reply("{}", status=401))
    with pytest.raises(LLMUnavailableError):
        provider.generate("s", "u", MemoSections, timeout=30)
