"""ARK chat-completions adapter with explicit retries and local policy snapshots."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import openai
import yaml

from mra.model_runtime.base import ModelRuntimeError
from mra.model_runtime.types import (
    AttemptOutcome,
    AttemptRecord,
    Capability,
    Message,
    ModelRef,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolSpec,
    Usage,
)


_STRUCTURED_TOOL = "submit_structured_output"


class _StructuredOutputError(Exception):
    """Internal marker; provider content and validation details are not exposed."""


class ArkRuntime:
    """Implement ModelRuntime using ARK's OpenAI-compatible synchronous API."""

    def __init__(
        self,
        capabilities_path: str | os.PathLike[str],
        pricing_path: str | os.PathLike[str] | None = None,
        client: Any = None,
    ) -> None:
        self._capabilities = self._load_entries(capabilities_path, "models")
        self._prices = (
            self._load_entries(pricing_path, "prices") if pricing_path is not None else []
        )
        if client is None:
            api_key = os.environ.get("ARK_API_KEY")
            if not api_key:
                raise ModelRuntimeError("ARK authentication is not configured (fatal).")
            try:
                client = openai.OpenAI(
                    base_url=os.environ.get(
                        "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
                    ),
                    api_key=api_key,
                    max_retries=0,
                )
            except Exception:
                raise ModelRuntimeError("ARK client initialization failed (fatal).") from None
        elif isinstance(client, openai.OpenAI):
            # An injected SDK client must not add invisible attempts of its own.
            client = client.with_options(max_retries=0)
        self._client = client

    @staticmethod
    def _load_entries(
        path: str | os.PathLike[str], key: str
    ) -> list[dict[str, Any]]:
        try:
            with Path(path).open(encoding="utf-8") as policy_file:
                document = yaml.safe_load(policy_file)
            if not isinstance(document, dict):
                raise ValueError
            entries = document.get(key, [])
            if not isinstance(entries, list) or not all(
                isinstance(entry, dict) for entry in entries
            ):
                raise ValueError
            return entries
        except (OSError, ValueError, yaml.YAMLError):
            raise ModelRuntimeError("ARK policy snapshot could not be loaded (fatal).") from None

    @staticmethod
    def _matching_entry(
        entries: list[dict[str, Any]], model: ModelRef
    ) -> dict[str, Any] | None:
        candidates = [
            entry
            for entry in entries
            if entry.get("provider") == model.provider and entry.get("model") == model.model
        ]
        for entry in candidates:
            if entry.get("version") == model.version:
                return entry
        for entry in candidates:
            if model.version == "unverified" or entry.get("version") == "unverified":
                return entry
        return None

    def capabilities(self, model: ModelRef) -> set[Capability]:
        """Read registered capabilities; unknown models deny by default (empty set)."""
        entry = self._matching_entry(self._capabilities, model)
        if entry is None:
            return set()
        try:
            return {Capability(value) for value in entry.get("capabilities", [])}
        except (TypeError, ValueError):
            raise ModelRuntimeError("ARK capability snapshot is invalid (fatal).") from None

    @staticmethod
    def _required_capabilities(req: ModelRequest) -> set[Capability]:
        required = {Capability.CHAT}
        if req.tools or req.output_schema is not None or any(
            message.tool_calls or message.role == "tool" for message in req.messages
        ):
            required.add(Capability.TOOL_CALLING)
        if req.output_schema is not None:
            required.add(Capability.STRUCTURED_OUTPUT)
        return required

    @staticmethod
    def _message(message: Message) -> dict[str, Any]:
        result: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id is not None:
            result["tool_call_id"] = message.tool_call_id
        if message.name is not None:
            result["name"] = message.name
        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_json},
                }
                for call in message.tool_calls
            ]
        return result

    @staticmethod
    def _tool(tool: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            },
        }

    def _request_args(self, req: ModelRequest, model: ModelRef) -> dict[str, Any]:
        args: dict[str, Any] = {
            "model": model.model,
            "messages": [self._message(message) for message in req.messages],
            "timeout": req.timeout_s,
        }
        if req.max_tokens is not None:
            args["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            args["temperature"] = req.temperature
        tools = [self._tool(tool) for tool in req.tools]
        if req.output_schema is not None:
            if any(tool.name == _STRUCTURED_TOOL for tool in req.tools):
                raise _StructuredOutputError
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": _STRUCTURED_TOOL,
                        "description": "Submit the structured response.",
                        "parameters": req.output_schema.model_json_schema(),
                    },
                }
            )
            args["tool_choice"] = {
                "type": "function",
                "function": {"name": _STRUCTURED_TOOL},
            }
        if tools:
            args["tools"] = tools
        return args

    @staticmethod
    def _usage(provider_usage: Any) -> Usage:
        if provider_usage is None:
            raw: dict[str, Any] = {}
        elif isinstance(provider_usage, dict):
            raw = dict(provider_usage)
        else:
            # Do not fabricate optional fields that the provider did not return.
            raw = provider_usage.model_dump(exclude_unset=True)
        fields = {
            "input_tokens": raw.get("prompt_tokens"),
            "output_tokens": raw.get("completion_tokens"),
            "total_tokens": raw.get("total_tokens"),
        }
        missing = [name for name, value in fields.items() if value is None]
        details = (
            (
                "prompt_tokens_details",
                {
                    "cached_tokens": "cached_tokens",
                    "cache_write_tokens": "cache_write_tokens",
                    "audio_tokens": "input_audio_tokens",
                    "image_tokens": "input_image_tokens",
                    "text_tokens": "input_text_tokens",
                },
            ),
            (
                "completion_tokens_details",
                {
                    "reasoning_tokens": "reasoning_tokens",
                    "audio_tokens": "output_audio_tokens",
                    "text_tokens": "output_text_tokens",
                    "accepted_prediction_tokens": "accepted_prediction_tokens",
                    "rejected_prediction_tokens": "rejected_prediction_tokens",
                },
            ),
        )
        for detail_key, names in details:
            reported = raw.get(detail_key) or {}
            missing.extend(name for field, name in names.items() if reported.get(field) is None)
        return Usage(**fields, raw=raw, missing=tuple(missing))

    def _cost(self, model: ModelRef, usage: Usage) -> Decimal | None:
        entry = self._matching_entry(self._prices, model)
        if entry is None or usage.input_tokens is None or usage.output_tokens is None:
            return None
        try:
            price_in = Decimal(str(entry["input"]))
            price_out = Decimal(str(entry["output"]))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            # 价格条目字段缺失/非法时不猜测成本，记 None（快照缺失同义）。
            return None
        return (
            Decimal(usage.input_tokens) * price_in
            + Decimal(usage.output_tokens) * price_out
        ) / Decimal(1_000_000)

    @staticmethod
    def _outcome(error: Exception) -> tuple[AttemptOutcome, str]:
        if isinstance(error, _StructuredOutputError):
            return AttemptOutcome.FATAL, "structured_output_validation"
        if isinstance(error, openai.APITimeoutError):
            return AttemptOutcome.TIMEOUT, "APITimeoutError"
        if isinstance(error, openai.RateLimitError):
            return AttemptOutcome.RATE_LIMIT, "RateLimitError"
        if isinstance(error, openai.APIConnectionError):
            return AttemptOutcome.TRANSIENT, "APIConnectionError"
        if isinstance(
            error, (openai.AuthenticationError, openai.PermissionDeniedError, openai.BadRequestError)
        ):
            return AttemptOutcome.FATAL, type(error).__name__
        if isinstance(error, openai.APIStatusError) and 500 <= error.status_code < 600:
            return AttemptOutcome.TRANSIENT, type(error).__name__
        return AttemptOutcome.FATAL, type(error).__name__

    def complete(self, req: ModelRequest) -> ModelResponse:
        """Call ARK with 1s/2s retries; fatal errors never retry or fall back."""
        attempts: list[AttemptRecord] = []
        required = self._required_capabilities(req)
        for model_index, model in enumerate((req.model, *req.fallback_models)):
            attempt_limit = 3 if model_index == 0 else 1
            for attempt_index in range(attempt_limit):
                started_at = datetime.now(timezone.utc)
                try:
                    if model.provider != "ark":
                        raise ModelRuntimeError("ARK cannot serve the requested provider (fatal).")
                    if not required.issubset(self.capabilities(model)):
                        raise ModelRuntimeError("Model lacks required capabilities (fatal).")
                    result = self._client.chat.completions.create(**self._request_args(req, model))
                    try:
                        message = result.choices[0].message
                        calls = tuple(
                            ToolCall(
                                id=call.id,
                                name=call.function.name,
                                arguments_json=call.function.arguments,
                            )
                            for call in (message.tool_calls or ())
                        )
                        structured = None
                        if req.output_schema is not None:
                            submissions = [call for call in calls if call.name == _STRUCTURED_TOOL]
                            if len(submissions) != 1:
                                raise ValueError
                            structured = req.output_schema.model_validate(
                                json.loads(submissions[0].arguments_json)
                            )
                    except Exception:
                        if req.output_schema is not None:
                            raise _StructuredOutputError from None
                        raise
                    usage = self._usage(result.usage)
                    cost = self._cost(model, usage)
                except Exception as error:
                    outcome, error_class = self._outcome(error)
                    attempts.append(
                        AttemptRecord(
                            model=model,
                            started_at=started_at,
                            ended_at=datetime.now(timezone.utc),
                            outcome=outcome,
                            error_class=error_class,
                        )
                    )
                    if outcome == AttemptOutcome.FATAL:
                        raise ModelRuntimeError(
                            "ARK request failed (fatal).", tuple(attempts)
                        ) from None
                    if attempt_index + 1 < attempt_limit:
                        time.sleep(2**attempt_index)
                    continue
                attempts.append(
                    AttemptRecord(
                        model=model,
                        started_at=started_at,
                        ended_at=datetime.now(timezone.utc),
                        outcome=AttemptOutcome.SUCCESS,
                    )
                )
                return ModelResponse(
                    request_id=req.request_id,
                    content=message.content,
                    tool_calls=calls,
                    structured=structured,
                    usage=usage,
                    cost_cny=cost,
                    attempts=tuple(attempts),
                    final_model=model,
                )
        raise ModelRuntimeError("ARK request failed after all attempts.", tuple(attempts))
