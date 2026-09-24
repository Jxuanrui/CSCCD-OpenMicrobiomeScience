"""P1 Budget / Resource Metering——资源计量与预算治理（production governance）。

设计裁决（用户 2026-09-24 spec）：
- **计量 ≠ 成本追踪**：compute / external retrieval / retries / runtime / cache
  与 LLM tokens 同为一等计量维度；estimated_cost 只是派生指标。
- **事实与派生分离**：Measured Usage（tokens/calls/时长）是事实；
  Estimated Cost 依 pricing table（source/version/date）派生——未来价格变化
  不得反向改写历史 usage。
- **不破坏 frozen ResearchTask v1**：预算经 ledger 记录（ResourceBudget）
  挂接 task scope，不改 ResearchTask 顶层语义。
- **幂等与计费一致但不混同**：真正重执行→增 usage；幂等 retry（Workspace
  commit 重提交）→零增；cache hit→计数但不按全量计费。
- **防绕过**：预算绑 research_task_id——plan revision/retry 天然继承；
  child task 无自有预算时沿 parent_task_id 链继承（保守默认，不能靠派生
  任务拿新预算）。
- **凭据纪律**：只记 provider/model/auth_scope，绝不记 api_key/token/secret
  （Workspace.append 级扫描继续覆盖）。

ResourceUsage / ResourceBudget 均为 Workspace 账本记录类型（append-only、
durable、replay 可重建——restart 后预算不重置由构造保证）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 计量维度（事实）：聚合口径的唯一权威列表
MEASURED_FIELDS = ("model_calls", "input_tokens", "output_tokens", "total_tokens",
                   "external_api_calls", "compute_duration_ms", "wall_duration_ms",
                   "retry_count", "cache_hit_count", "cache_miss_count")
# 预算维度：ResourceBudget 字段名 → totals 键（时长字段以 ms 计）
BUDGET_TO_TOTALS = {
    "max_model_calls": "model_calls",
    "max_input_tokens": "input_tokens",
    "max_output_tokens": "output_tokens",
    "max_total_tokens": "total_tokens",
    "max_external_api_calls": "external_api_calls",
    "max_compute_time_ms": "compute_duration_ms",
    "max_wall_time_ms": "wall_duration_ms",
    "max_retry_count": "retry_count",
    "max_estimated_cost": "estimated_cost",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResourceUsage(BaseModel):
    """一等计量对象：一次真实执行的资源消耗事实（+ 派生成本）。

    kind 语义：execution=真实执行（计费）；cache_hit=缓存命中（计数不计
    API 费）；idempotent_retry 默认**不产生记录**（幂等重提交零增计费，
    与 B4.1 幂等语义对齐——如需审计可显式记 kind=idempotent_retry 且全零）。
    """
    model_config = ConfigDict(extra="forbid")
    usage_id: str = Field(min_length=3)
    research_task_id: str = Field(min_length=1)
    kind: str = "execution"            # execution | cache_hit
    plan_id: str = ""
    step_id: str = ""
    capability_id: str = ""
    execution_id: str = ""
    model_id: str = ""
    provider: str = ""                 # 凭据安全：只记 provider，不记 key/token
    # ---- Measured usage（事实） ----
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    external_api_calls: int = Field(default=0, ge=0)
    compute_duration_ms: float = Field(default=0, ge=0)
    wall_duration_ms: float = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    cache_hit_count: int = Field(default=0, ge=0)
    cache_miss_count: int = Field(default=0, ge=0)
    # ---- Estimated cost（派生；与事实分离） ----
    estimated_cost: float | None = None
    currency: str = ""
    pricing_source: str = ""
    pricing_version: str = ""
    calculated_at: str = ""
    measured_at: str = Field(default_factory=_now)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("execution", "cache_hit", "idempotent_retry"):
            raise ValueError("kind 须为 execution/cache_hit/idempotent_retry")
        return v


class ResourceBudget(BaseModel):
    """预算约束（绑 research_task_id；未配置 = unbounded 而非 0）。

    修订 append-only：新预算记录以 supersedes_budget_id 指回旧记录；
    同 task 无授权理由的重复预算记录会被 Workspace 拒绝（防绕过）。
    """
    model_config = ConfigDict(extra="forbid")
    budget_id: str = Field(min_length=3)
    research_task_id: str = Field(min_length=1)
    max_model_calls: int | None = Field(default=None, ge=0)
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)
    max_total_tokens: int | None = Field(default=None, ge=0)
    max_external_api_calls: int | None = Field(default=None, ge=0)
    max_compute_time_ms: float | None = Field(default=None, ge=0)
    max_wall_time_ms: float | None = Field(default=None, ge=0)
    max_retry_count: int | None = Field(default=None, ge=0)
    max_estimated_cost: float | None = Field(default=None, ge=0)
    supersedes_budget_id: str | None = None
    reason: str = ""                   # 修订必填（防静默改预算）
    set_by: str = "unknown"
    created_at: str = Field(default_factory=_now)


def empty_totals() -> dict[str, Any]:
    return {**{f: 0 for f in MEASURED_FIELDS}, "estimated_cost": 0.0}


def aggregate_usage(usages: list[dict]) -> dict[str, Any]:
    """聚合用量（事实求和；estimated_cost 仅同币种求和，记录 pricing 版本集）。"""
    totals = empty_totals()
    currencies: set[str] = set()
    pricing_versions: set[str] = set()
    n_records = 0
    for u in usages:
        n_records += 1
        for f in MEASURED_FIELDS:
            totals[f] += u.get(f, 0) or 0
        cost = u.get("estimated_cost")
        if cost is not None:
            totals["estimated_cost"] += cost
            currencies.add(u.get("currency") or "USD")
        if u.get("pricing_version"):
            pricing_versions.add(u["pricing_version"])
    totals["n_records"] = n_records
    totals["currencies"] = sorted(currencies)
    totals["pricing_versions"] = sorted(pricing_versions)
    return totals


def budget_verdict(budget: ResourceBudget | dict | None,
                   totals: dict[str, Any]) -> dict[str, Any]:
    """Pre-execution 门：任一配置维度已耗尽 → allow=False（未配置=unbounded）。"""
    if budget is None:
        return {"allow": True, "exhausted": [], "budget": None}
    b = budget.model_dump() if isinstance(budget, ResourceBudget) else dict(budget)
    exhausted = []
    for max_field, totals_key in BUDGET_TO_TOTALS.items():
        limit = b.get(max_field)
        if limit is None:  # unbounded
            continue
        spent = totals.get(totals_key, 0) or 0
        if spent >= limit:
            exhausted.append(f"{max_field}={limit} (spent {spent})")
    return {"allow": not exhausted, "exhausted": exhausted,
            "budget": {"research_task_id": b.get("research_task_id", ""),
                       **{k: b.get(k) for k in BUDGET_TO_TOTALS}}}


def estimate_cost(totals: dict[str, Any], pricing: dict[str, Any],
                  pricing_source: str, pricing_version: str) -> dict[str, Any]:
    """派生成本（与事实分离）：按 pricing table 计算，返回可附着到 usage 的
    成本块。定价键：input_per_1k / output_per_1k / model_call_flat /
    external_call_flat / compute_per_s（currency 必填）。
    历史已记录的 usage 永不因价格变化被改写——重算只产生新的派生值。"""
    cost = 0.0
    cost += (totals.get("input_tokens", 0) or 0) / 1000 * float(pricing.get("input_per_1k", 0))
    cost += (totals.get("output_tokens", 0) or 0) / 1000 * float(pricing.get("output_per_1k", 0))
    cost += (totals.get("model_calls", 0) or 0) * float(pricing.get("model_call_flat", 0))
    cost += (totals.get("external_api_calls", 0) or 0) * float(pricing.get("external_call_flat", 0))
    cost += (totals.get("compute_duration_ms", 0) or 0) / 1000 * float(pricing.get("compute_per_s", 0))
    return {"estimated_cost": round(cost, 6),
            "currency": pricing.get("currency", "USD"),
            "pricing_source": pricing_source,
            "pricing_version": pricing_version,
            "calculated_at": _now()}


__all__ = ["BUDGET_TO_TOTALS", "MEASURED_FIELDS", "ResourceBudget", "ResourceUsage",
           "aggregate_usage", "budget_verdict", "empty_totals", "estimate_cost"]
