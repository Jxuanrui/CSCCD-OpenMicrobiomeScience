"""ModelRuntime 窄接口（Protocol）。

行为规则（Q12 确认，实现类必须遵守）：

1. 重试/fallback 由 ModelRuntime 统一执行，逐次记入 ModelResponse.attempts；
   底层 SDK 的隐式重试必须关闭（防双层重试导致成本失真/事件缺口）。
2. 自动 fallback 仅限 AttemptOutcome 的 TIMEOUT/RATE_LIMIT/TRANSIENT；
   备用模型须来自 ModelRequest.fallback_models 且在能力表中满足请求所需
   能力子集；FATAL（含结构化校验失败、鉴权失败、越权拒绝）不重试不 fallback。
3. 结构化输出 = tool calling + Pydantic 校验；校验失败记 FATAL/
   "structured_output_validation"，计入回归门指标。
4. 职责边界（5.7）：不管工作流状态、不含科研知识、不做统计语义、
   不执行工具、不做权限判断；TraceTags 仅透传留痕。
   TraceTags 的持久化由调用方（PEP/审计层）按 request_id 关联完成，
   本层不回传 trace（ModelResponse/AttemptRecord 不含 trace 字段）。
5. 凭据从环境变量/OS keyring 读取，不进请求对象、不进日志。

MVP 不暴露 stream() 与多模态输入；v1 再进入接口。
"""

from __future__ import annotations

from typing import Protocol

from mra.model_runtime.types import (
    AttemptRecord,
    Capability,
    ModelRef,
    ModelRequest,
    ModelResponse,
)


class ModelRuntimeError(Exception):
    """调用最终失败（FATAL，或重试耗尽）时抛出。

    attempts 携带本次调用全部尝试记录（重试/fallback 审计不断链）。
    message 不得包含凭据等敏感信息。
    """

    def __init__(self, message: str, attempts: tuple[AttemptRecord, ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


class ModelRuntime(Protocol):
    """模型可插拔层的窄接口。适配器（如 ARK）实现本 Protocol。"""

    def capabilities(self, model: ModelRef) -> set[Capability]:
        """返回能力表中该模型的能力集合（查表，不探测）。"""
        ...

    def complete(self, req: ModelRequest) -> ModelResponse:
        """执行一次完整调用（含内部重试/fallback 记录），返回最终结果。

        最终失败时抛 ModelRuntimeError（携带 attempts）。
        """
        ...
