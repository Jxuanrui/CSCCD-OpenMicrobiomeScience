"""ModelRuntime 接口契约的类型定义（Q12 确认的草案 v0.1）。

设计依据：PLANNING.md 5.7 与 policies/reuse-reviews/2026-09-10-modelruntime-adapter-libs.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


@dataclass(frozen=True)
class ModelRef:
    """一个确定模型的完整身份。

    version 取 provider 侧版本/快照日期；provider 不给版本时用 "unversioned"。
    endpoint 是 base_url 的逻辑名（如 "ark-cn-beijing"），不存凭据。
    """

    provider: str
    model: str
    version: str
    endpoint: str


class Capability(str, Enum):
    """模型能力枚举。能力表由平台自维护（policies/model_capabilities.yaml，
    Git 版本化，按 ModelRef 登记），不做运行时探测（Reuse Review 第 3 节）。"""

    CHAT = "chat"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"  # 经 tool calling + Pydantic 校验实现
    STREAMING = "streaming"
    VISION = "vision"  # MVP 不用，预留


@dataclass(frozen=True)
class TraceTags:
    """留痕透传标签。本层不解释语义，仅原样写入审计事件。"""

    workflow_run_id: Optional[str] = None
    node: Optional[str] = None
    actor_role: Optional[str] = None


@dataclass(frozen=True)
class Message:
    """对话消息。role ∈ {system, user, assistant, tool}。"""

    role: str
    content: Optional[str] = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass(frozen=True)
class ToolCall:
    """模型发起的工具调用。arguments_json 为未解析的 JSON 字符串（执行与否由 PEP 决定，本层不执行）。"""

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolSpec:
    """工具声明。parameters_schema 由 Pydantic 模型生成的 JSON Schema。"""

    name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    """一次模型调用请求。request_id 用 ULID，全链路追踪。"""

    request_id: str
    model: ModelRef
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    output_schema: Optional[type[BaseModel]] = None  # 结构化输出契约
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    timeout_s: int = 60
    trace: TraceTags = field(default_factory=TraceTags)
    fallback_models: tuple[ModelRef, ...] = ()  # 须满足同一能力子集；MVP 为空


@dataclass(frozen=True)
class Usage:
    """token 用量：标准化值 + provider 原始值 + 缺失标志（Reuse Review 第 3 节）。"""

    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    raw: dict[str, Any] = field(default_factory=dict)
    missing: tuple[str, ...] = ()  # 如 ("cached_tokens", "reasoning_tokens")


class AttemptOutcome(str, Enum):
    """单次尝试结局。自动 fallback/重试仅限 TIMEOUT/RATE_LIMIT/TRANSIENT（5.7）。"""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"  # 5xx、连接错误等临时故障
    FATAL = "fatal"  # 鉴权失败、结构化校验失败、越权拒绝等，不重试不 fallback


@dataclass(frozen=True)
class AttemptRecord:
    """每次尝试独立留痕。ModelRuntime 逐次记录；底层 SDK 隐式重试必须关闭。"""

    model: ModelRef
    started_at: datetime
    ended_at: datetime
    outcome: AttemptOutcome
    error_class: Optional[str] = None  # 如 "structured_output_validation"


@dataclass(frozen=True)
class ModelResponse:
    """一次完整调用的最终结果。

    attempts ≥ 1；>1 说明发生过重试/fallback。
    final_model 为实际出结果的模型（fallback 后可能不同于请求模型）。
    structured 为 output_schema 校验后的实例；校验失败时本字段为 None 且
    末次 attempt 记 FATAL/"structured_output_validation"。
    cost_cny 按平台价格快照（policies/model_pricing.yaml）计算；快照缺失时为 None。
    """

    request_id: str
    content: Optional[str]
    tool_calls: tuple[ToolCall, ...]
    structured: Optional[BaseModel]
    usage: Usage
    cost_cny: Optional[Decimal]
    attempts: tuple[AttemptRecord, ...]
    final_model: ModelRef
