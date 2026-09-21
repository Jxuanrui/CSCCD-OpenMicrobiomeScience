"""ModelRuntime：模型可插拔层（PLANNING.md 5.7，Q12 确认的接口契约 v0.1）。

职责边界（5.7）：本层只负责请求/响应、工具调用、结构化输出、能力声明、
usage/cost、重试/fallback 记录、provider/模型版本记录。
不负责工作流状态、科研知识、统计语义、工具执行、权限判断。
"""

from mra.model_runtime.base import ModelRuntime, ModelRuntimeError
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
    TraceTags,
    Usage,
)

__all__ = [
    "AttemptOutcome",
    "AttemptRecord",
    "Capability",
    "Message",
    "ModelRef",
    "ModelRequest",
    "ModelResponse",
    "ModelRuntime",
    "ModelRuntimeError",
    "ToolCall",
    "ToolSpec",
    "TraceTags",
    "Usage",
]
