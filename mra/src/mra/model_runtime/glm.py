"""GLM 中转适配器（OpenAI 兼容接口，作为 ARK 的 fallback 运行时）。

环境变量：GLM_API_KEY 必需；GLM_BASE_URL（默认图谱侧中转地址）、GLM_MODEL
（默认 glm-5.3-flash，便宜档）。行为规则与 ArkRuntime 对齐：SDK max_retries=0，
重试/切换由上层（planner fallback）显式管理，凭据不落日志。
"""
from __future__ import annotations

import os
from typing import Any

import openai

from .base import ModelRuntimeError
from .types import Message, ModelRef, ModelRequest, ModelResponse

DEFAULT_BASE_URL = "https://ai.shimiaocheng.top/v1"
DEFAULT_MODEL = "glm-5.3-flash"


class GlmRuntime:
    """Implement ModelRuntime via GLM relay's OpenAI-compatible API."""

    def __init__(self, client: Any = None) -> None:
        if client is None:
            api_key = os.environ.get("GLM_API_KEY")
            if not api_key:
                raise ModelRuntimeError("GLM fallback is not configured (fatal).")
            try:
                client = openai.OpenAI(
                    base_url=os.environ.get("GLM_BASE_URL", DEFAULT_BASE_URL),
                    api_key=api_key,
                    max_retries=0,
                )
            except Exception:
                raise ModelRuntimeError("GLM client initialization failed (fatal).") from None
        self._client = client

    def complete(self, req: ModelRequest) -> ModelResponse:  # noqa: D102 - 与 Ark 同构
        model = req.model.model if req.model.provider == "glm" else os.environ.get(
            "GLM_MODEL", DEFAULT_MODEL)
        messages = [{"role": m.role, "content": m.content} for m in req.messages]
        try:
            resp = self._client.chat.completions.create(
                model=model, messages=messages, temperature=0.2)
        except Exception as exc:
            raise ModelRuntimeError(f"glm call failed: {type(exc).__name__}") from exc
        choice = resp.choices[0]
        from datetime import datetime, timezone
        from decimal import Decimal

        from .types import AttemptRecord, Usage
        now = datetime.now(timezone.utc)
        glm_ref = ModelRef(provider="glm", model=model, version="unversioned", endpoint="glm-relay")
        return ModelResponse(
            request_id=req.request_id,
            content=choice.message.content or "",
            tool_calls=(),
            structured=None,
            usage=Usage(input_tokens=getattr(resp.usage, "prompt_tokens", None),
                        output_tokens=getattr(resp.usage, "completion_tokens", None),
                        total_tokens=getattr(resp.usage, "total_tokens", None)),
            cost_cny=None,
            attempts=(AttemptRecord(model=glm_ref, started_at=now, ended_at=now, outcome="ok"),),
            final_model=glm_ref,
        )


def glm_available() -> bool:
    return bool(os.environ.get("GLM_API_KEY"))


def fallback_complete(req: ModelRequest) -> ModelResponse:
    """把 ARK 请求转投 GLM 中转（模型名切换为 GLM 档）。"""
    req = ModelRequest(
        request_id=req.request_id,
        model=ModelRef(provider="glm", model=os.environ.get("GLM_MODEL", DEFAULT_MODEL),
                       version="unverified", endpoint="glm-relay"),
        messages=req.messages,
        tools=req.tools,
    )
    return GlmRuntime().complete(req)
