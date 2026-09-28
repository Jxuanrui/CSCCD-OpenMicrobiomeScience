from __future__ import annotations

import json
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import respx
import yaml
from openai import OpenAI
from pydantic import BaseModel

from mra.model_runtime.ark import ArkRuntime
from mra.model_runtime.base import ModelRuntimeError
from mra.model_runtime.types import (
    AttemptOutcome,
    Capability,
    Message,
    ModelRef,
    ModelRequest,
    ToolCall,
    ToolSpec,
)


ARK_URL = "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"
PRIMARY = ModelRef("ark", "doubao-test", "v1", "ark-cn-beijing")


class Finding(BaseModel):
    organism: str
    significant: bool


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-secret-never-log")
    monkeypatch.delenv("ARK_BASE_URL", raising=False)


@pytest.fixture
def ark_router(monkeypatch: pytest.MonkeyPatch):
    """Route the OpenAI SDK's httpx2 transport through respx without I/O."""

    def handle_request(
        _transport: httpx2.HTTPTransport, request: httpx2.Request
    ) -> httpx2.Response:
        proxied = httpx.Request(
            request.method,
            str(request.url),
            headers=request.headers.raw,
            content=request.read(),
            extensions=request.extensions,
        )
        try:
            response = router.handler(proxied)
        except httpx.TimeoutException as exc:
            error_type = getattr(httpx2, type(exc).__name__, httpx2.TimeoutException)
            raise error_type("mock transport timeout", request=request) from None
        except httpx.RequestError as exc:
            error_type = getattr(httpx2, type(exc).__name__, httpx2.RequestError)
            raise error_type("mock transport failure", request=request) from None
        return httpx2.Response(
            response.status_code,
            headers=response.headers.raw,
            content=response.read(),
            extensions=response.extensions,
            request=request,
        )

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", handle_request)
    with respx.mock(assert_all_called=True) as router:
        yield router


def write_capabilities(tmp_path: Path, models: list[dict[str, Any]] | None = None) -> Path:
    entries = models if models is not None else [
        {
            "provider": "ark",
            "model": PRIMARY.model,
            "version": PRIMARY.version,
            "endpoint": PRIMARY.endpoint,
            "capabilities": ["chat", "tool_calling", "structured_output"],
            "status": "verified",
        }
    ]
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "models": entries},
            sort_keys=False,
            allow_unicode=False,
        )
        ,
        encoding="utf-8",
    )
    return path


def write_pricing(tmp_path: Path, *, include_model: bool = True) -> Path:
    path = tmp_path / "pricing.yaml"
    prices = ""
    if include_model:
        prices = """
  - provider: ark
    model: doubao-test
    version: v1
    input: 2
    output: 8
    snapshot_at: '2026-09-10'
    source: test fixture
"""
    path.write_text(
        "schema_version: 1\ncurrency: CNY\nunit: per_million_tokens\nprices:"
        + (prices if prices else " []\n"),
        encoding="utf-8",
    )
    return path


def request(
    *,
    model: ModelRef = PRIMARY,
    messages: tuple[Message, ...] = (Message(role="user", content="hello"),),
    tools: tuple[ToolSpec, ...] = (),
    output_schema: type[BaseModel] | None = None,
    fallback_models: tuple[ModelRef, ...] = (),
    timeout_s: int = 7,
) -> ModelRequest:
    return ModelRequest(
        request_id="01JTESTREQUEST0000000000000",
        model=model,
        messages=messages,
        tools=tools,
        output_schema=output_schema,
        temperature=0,
        max_tokens=321,
        timeout_s=timeout_s,
        fallback_models=fallback_models,
    )


def completion(
    *,
    content: str | None = "done",
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    payload: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_789_000_000,
        "model": "doubao-test",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def error_response(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"message": f"provider error {status}", "type": "test_error"}},
    )


def runtime(tmp_path: Path, *, pricing: Path | None = None, client=None) -> ArkRuntime:
    return ArkRuntime(write_capabilities(tmp_path), pricing_path=pricing, client=client)


def test_plain_completion_normalizes_usage_attempt_and_decimal_cost(
    tmp_path: Path, ark_router
) -> None:
    pricing = write_pricing(tmp_path)
    route = ark_router.post(ARK_URL).mock(
        return_value=httpx.Response(
            200,
            json=completion(
                content="answer",
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            ),
        )
    )

    result = runtime(tmp_path, pricing=pricing).complete(request())

    assert result.content == "answer"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50
    assert result.usage.total_tokens == 150
    assert result.usage.raw == {
        "completion_tokens": 50,
        "prompt_tokens": 100,
        "total_tokens": 150,
    }
    assert "cached_tokens" in result.usage.missing
    assert "reasoning_tokens" in result.usage.missing
    assert result.cost_cny == Decimal("0.0006")
    assert result.final_model == PRIMARY
    assert len(result.attempts) == 1
    assert result.attempts[0].outcome is AttemptOutcome.SUCCESS
    assert result.attempts[0].started_at.tzinfo is not None
    assert result.attempts[0].ended_at.tzinfo is not None
    assert result.attempts[0].started_at <= result.attempts[0].ended_at
    assert route.call_count == 1


def test_structured_output_forces_named_tool_and_validates_result(
    tmp_path: Path, ark_router
) -> None:
    route = ark_router.post(ARK_URL).mock(
        return_value=httpx.Response(
            200,
            json=completion(
                content=None,
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "submit_structured_output",
                            "arguments": '{"organism":"Akkermansia","significant":true}',
                        },
                    }
                ],
                usage={"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
            ),
        )
    )

    result = runtime(tmp_path).complete(request(output_schema=Finding))
    body = json.loads(route.calls[0].request.content)

    assert result.structured == Finding(organism="Akkermansia", significant=True)
    assert result.tool_calls[0].name == "submit_structured_output"
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_structured_output"},
    }
    submitted = next(
        tool for tool in body["tools"] if tool["function"]["name"] == "submit_structured_output"
    )
    assert submitted["function"]["parameters"]["required"] == ["organism", "significant"]


def test_structured_validation_failure_is_fatal_and_does_not_retry(
    tmp_path: Path, ark_router
) -> None:
    route = ark_router.post(ARK_URL).mock(
        return_value=httpx.Response(
            200,
            json=completion(
                content=None,
                tool_calls=[
                    {
                        "id": "call-bad",
                        "type": "function",
                        "function": {
                            "name": "submit_structured_output",
                            "arguments": '{"organism":42}',
                        },
                    }
                ],
            ),
        )
    )

    with pytest.raises(ModelRuntimeError) as raised:
        runtime(tmp_path).complete(request(output_schema=Finding))

    assert route.call_count == 1
    assert len(raised.value.attempts) == 1
    assert raised.value.attempts[0].outcome is AttemptOutcome.FATAL
    assert raised.value.attempts[0].error_class == "structured_output_validation"


def test_malformed_structured_tool_is_classified_as_validation_failure(
    tmp_path: Path, ark_router
) -> None:
    route = ark_router.post(ARK_URL).mock(
        return_value=httpx.Response(
            200,
            json=completion(
                content=None,
                tool_calls=[{"id": "call-malformed", "type": "function", "function": None}],
            ),
        )
    )

    with pytest.raises(ModelRuntimeError) as raised:
        runtime(tmp_path).complete(request(output_schema=Finding))

    assert route.call_count == 1
    assert raised.value.attempts[0].outcome is AttemptOutcome.FATAL
    assert raised.value.attempts[0].error_class == "structured_output_validation"


def test_rate_limit_retries_then_succeeds(
    tmp_path: Path, ark_router, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("mra.model_runtime.ark.time.sleep", sleeps.append)
    route = ark_router.post(ARK_URL).mock(
        side_effect=[
            error_response(429),
            httpx.Response(200, json=completion(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})),
        ]
    )

    result = runtime(tmp_path).complete(request())

    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.RATE_LIMIT,
        AttemptOutcome.SUCCESS,
    ]
    assert route.call_count == 2
    assert sleeps == [1]


def test_three_timeouts_are_reported_and_exhaust_retries(
    tmp_path: Path, ark_router, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mra.model_runtime.ark.time.sleep", lambda _seconds: None)
    route = ark_router.post(ARK_URL).mock(
        side_effect=[httpx.ReadTimeout("slow") for _ in range(3)]
    )

    with pytest.raises(ModelRuntimeError) as raised:
        runtime(tmp_path).complete(request())

    assert route.call_count == 3
    assert [attempt.outcome for attempt in raised.value.attempts] == [
        AttemptOutcome.TIMEOUT,
        AttemptOutcome.TIMEOUT,
        AttemptOutcome.TIMEOUT,
    ]


def test_server_and_connection_failures_retry_with_one_then_two_seconds(
    tmp_path: Path, ark_router, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("mra.model_runtime.ark.time.sleep", sleeps.append)
    route = ark_router.post(ARK_URL).mock(
        side_effect=[
            error_response(503),
            httpx.ConnectError("disconnected"),
            httpx.Response(200, json=completion()),
        ]
    )

    result = runtime(tmp_path).complete(request())

    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.TRANSIENT,
        AttemptOutcome.TRANSIENT,
        AttemptOutcome.SUCCESS,
    ]
    assert route.call_count == 3
    assert sleeps == [1, 2]


@pytest.mark.parametrize("status", [400, 403])
def test_non_retryable_client_errors_are_fatal_without_fallback(
    status: int, tmp_path: Path, ark_router
) -> None:
    fallback = ModelRef("ark", "doubao-fallback", "v1", "ark-cn-beijing")
    capabilities = write_capabilities(
        tmp_path,
        models=[
            {"provider": "ark", "model": PRIMARY.model, "version": "v1", "endpoint": PRIMARY.endpoint, "capabilities": ["chat"]},
            {"provider": "ark", "model": fallback.model, "version": "v1", "endpoint": fallback.endpoint, "capabilities": ["chat"]},
        ],
    )
    route = ark_router.post(ARK_URL).mock(return_value=error_response(status))

    with pytest.raises(ModelRuntimeError) as raised:
        ArkRuntime(capabilities).complete(request(fallback_models=(fallback,)))

    assert route.call_count == 1
    assert [attempt.outcome for attempt in raised.value.attempts] == [AttemptOutcome.FATAL]


def test_unauthorized_is_fatal_once_and_secret_never_reaches_error(
    tmp_path: Path, ark_router
) -> None:
    secret = "test-secret-never-log"
    route = ark_router.post(ARK_URL).mock(
        return_value=httpx.Response(
            401,
            json={"error": {"message": f"echoed {secret}", "type": "authentication_error"}},
        )
    )

    try:
        runtime(tmp_path).complete(request())
    except ModelRuntimeError as exc:
        rendered = f"{exc}\n{traceback.format_exc()}"
        assert len(exc.attempts) == 1
        assert exc.attempts[0].outcome is AttemptOutcome.FATAL
    else:
        pytest.fail("401 should raise ModelRuntimeError")

    assert route.call_count == 1
    assert secret not in rendered


@pytest.mark.parametrize(
    ("include_price", "provider_usage"),
    [
        (False, {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}),
        (True, None),
    ],
)
def test_missing_pricing_or_usage_never_guesses_cost(
    include_price: bool,
    provider_usage: dict[str, int] | None,
    tmp_path: Path,
    ark_router,
) -> None:
    ark_router.post(ARK_URL).mock(
        return_value=httpx.Response(200, json=completion(usage=provider_usage))
    )

    result = runtime(
        tmp_path, pricing=write_pricing(tmp_path, include_model=include_price)
    ).complete(request())

    assert result.cost_cny is None
    if provider_usage is None:
        assert result.usage.input_tokens is None
        assert result.usage.output_tokens is None
        assert result.usage.total_tokens is None


def test_capability_lookup_matches_full_identity_and_keeps_providers_isolated(
    tmp_path: Path,
) -> None:
    capabilities = write_capabilities(
        tmp_path,
        models=[
            {"provider": "ark", "model": "same", "version": "unverified", "endpoint": "ark-cn-beijing", "capabilities": ["chat"]},
            {"provider": "ark", "model": "same", "version": "v1", "endpoint": "ark-cn-beijing", "capabilities": ["chat", "tool_calling"]},
            {"provider": "other", "model": "same", "version": "v1", "endpoint": "ark-cn-beijing", "capabilities": ["structured_output"]},
        ],
    )
    adapter = ArkRuntime(capabilities)

    assert adapter.capabilities(ModelRef("ark", "same", "unverified", "ark-cn-beijing")) == {Capability.CHAT}
    assert adapter.capabilities(ModelRef("ark", "same", "v1", "ark-cn-beijing")) == {
        Capability.CHAT,
        Capability.TOOL_CALLING,
    }
    assert adapter.capabilities(ModelRef("other", "same", "v1", "ark-cn-beijing")) == {
        Capability.STRUCTURED_OUTPUT
    }
    assert adapter.capabilities(ModelRef("ark", "same", "v2", "ark-cn-beijing")) == {
        Capability.CHAT
    }
    assert adapter.capabilities(ModelRef("ark", "unknown", "v1", "ark-cn-beijing")) == set()


def test_fallback_starts_after_three_primary_attempts_and_gets_one_attempt(
    tmp_path: Path, ark_router, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mra.model_runtime.ark.time.sleep", lambda _seconds: None)
    fallback = ModelRef("ark", "doubao-fallback", "v1", "ark-cn-beijing")
    capabilities = write_capabilities(
        tmp_path,
        models=[
            {"provider": "ark", "model": PRIMARY.model, "version": "v1", "endpoint": PRIMARY.endpoint, "capabilities": ["chat"]},
            {"provider": "ark", "model": fallback.model, "version": "v1", "endpoint": fallback.endpoint, "capabilities": ["chat"]},
        ],
    )
    route = ark_router.post(ARK_URL).mock(
        side_effect=[error_response(500), error_response(502), error_response(503), httpx.Response(200, json=completion(content="fallback"))]
    )

    result = ArkRuntime(capabilities).complete(request(fallback_models=(fallback,)))
    sent_models = [json.loads(call.request.content)["model"] for call in route.calls]

    assert sent_models == [PRIMARY.model, PRIMARY.model, PRIMARY.model, fallback.model]
    assert result.final_model == fallback
    assert len(result.attempts) == 4


def test_fallback_missing_structured_capability_is_fatal_without_request(
    tmp_path: Path, ark_router, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mra.model_runtime.ark.time.sleep", lambda _seconds: None)
    fallback = ModelRef("ark", "chat-only", "v1", "ark-cn-beijing")
    capabilities = write_capabilities(
        tmp_path,
        models=[
            {"provider": "ark", "model": PRIMARY.model, "version": "v1", "endpoint": PRIMARY.endpoint, "capabilities": ["chat", "tool_calling", "structured_output"]},
            {"provider": "ark", "model": fallback.model, "version": "v1", "endpoint": fallback.endpoint, "capabilities": ["chat"]},
        ],
    )
    route = ark_router.post(ARK_URL).mock(
        side_effect=[error_response(500), error_response(500), error_response(500)]
    )

    with pytest.raises(ModelRuntimeError) as raised:
        ArkRuntime(capabilities).complete(
            request(output_schema=Finding, fallback_models=(fallback,))
        )

    assert route.call_count == 3
    assert raised.value.attempts[-1].outcome is AttemptOutcome.FATAL
    assert all(attempt.model != fallback for attempt in raised.value.attempts[:-1])


def test_missing_api_key_fails_before_any_network_request(
    tmp_path: Path, ark_router, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    with pytest.raises(ModelRuntimeError):
        adapter = runtime(tmp_path)
        adapter.complete(request())

    assert not ark_router.calls


def test_injected_client_does_not_require_environment_key(
    tmp_path: Path, ark_router, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    client = OpenAI(
        api_key="injected-client-key",
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        max_retries=0,
    )
    route = ark_router.post(ARK_URL).mock(return_value=httpx.Response(200, json=completion()))

    result = runtime(tmp_path, client=client).complete(request())

    assert result.content == "done"
    assert route.call_count == 1


def test_messages_tools_and_generation_options_map_to_provider_payload(
    tmp_path: Path, ark_router
) -> None:
    tool = ToolSpec(
        name="lookup",
        description="Look up a record",
        parameters_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    )
    messages = (
        Message(role="system", content="be precise"),
        Message(role="user", content="find it"),
        Message(
            role="assistant",
            content=None,
            tool_calls=(ToolCall("call-7", "lookup", '{"id":"x"}'),),
        ),
        Message(role="tool", content="found", tool_call_id="call-7", name="lookup"),
    )
    route = ark_router.post(ARK_URL).mock(return_value=httpx.Response(200, json=completion()))

    runtime(tmp_path).complete(request(messages=messages, tools=(tool,), timeout_s=11))
    body = json.loads(route.calls[0].request.content)

    assert body["temperature"] == 0
    assert body["max_tokens"] == 321
    assert body["messages"][2]["tool_calls"][0]["function"] == {
        "name": "lookup",
        "arguments": '{"id":"x"}',
    }
    assert body["messages"][3]["tool_call_id"] == "call-7"
    assert body["tools"][0]["function"]["parameters"] == tool.parameters_schema
    assert route.calls[0].request.extensions["timeout"]["read"] == 11
