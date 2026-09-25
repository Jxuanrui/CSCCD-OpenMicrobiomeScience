"""P5 EXTERNAL_WRITE Recovery Framework——外部副作用的治理边界（准入框架）。

P5 不开放外部写。它建立**任何未来 EXTERNAL_WRITE capability 的准入标准**：
系统知道什么时候可以写、写失败后发生什么、什么时候绝不能自动重试。

第一原则：内部 Workspace 靠 ledger/replay/backup/restore 保证状态；外部世界
可能不可查询、不可回滚、部分成功、状态未知——**Workspace exactly-once 语义
不得外推**。

三分类恢复策略（固化）：
- **Type A idempotent**：外部系统支持 idempotency key——同 key 重复执行结果
  等价，允许 retry（key 从账本派生，restart 后稳定）。
- **Type B queryable**：不保证幂等但可查询——unknown 时**先 verify 外部状态
  再决定**，禁止 blind retry。
- **Type C irreversible/unknown**：无法确认是否成功/可回滚——**不自动重试**，
  进入 `recovery_requires_review`（人工处置后才可显式改判）。

生命周期（不是 success/failed 二值——unknown 是一等公民）：
  planned → authorized → submitted → acknowledged → verified → completed
  任意点可 → unknown；unknown → recovery_requires_review / 经恢复动作推进；
  failed（明确失败）→ submitted（新尝试）。
  recovery_requires_review 是吸收态：只经 `resolve_review`（人工）离开。

治理铁律：
- **默认不可执行**——必须先 authorized（GovernanceDecision 裁决 +
  workspace 外部写域登记，capability 注册存在 ≠ 允许写）；
- 每次状态迁移都是 append-only `ExternalWriteRecord`（intent/authorization/
  result/verification 全随行）；
- **replay 只重建状态，永不重新执行真实写**（transport 不在 Workspace 内）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .isolation import IsolationError, IsolationRegistries
from .workspace import Workspace, digest

WRITE_TYPES = ("idempotent", "queryable", "irreversible")
LIFECYCLE = ("planned", "authorized", "submitted", "acknowledged", "verified",
             "completed", "failed", "unknown", "recovery_requires_review")
_ALLOWED = {
    "planned": {"authorized"},
    "authorized": {"submitted"},
    "submitted": {"acknowledged", "failed", "unknown"},
    "acknowledged": {"verified", "completed", "unknown"},
    "verified": {"completed"},
    "failed": {"submitted"},            # 明确失败 → 允许新尝试
    # unknown→submitted 仅由 recover 的类型化路径（A 类幂等重试 / B 类
    # verify-failed 重试）触发；公共 submit() 只接受 authorized（盲重试=0）
    "unknown": {"recovery_requires_review", "acknowledged", "verified", "failed",
                "submitted", "unknown"},  # unknown→unknown=核验留痕
    # 吸收态：只经 resolve_review（人工署名）离开
    "recovery_requires_review": {"failed", "acknowledged", "completed", "verified"},
    "completed": set(),
}


class ExternalWriteError(RuntimeError):
    """外部写治理违例（未授权/盲重试/非法迁移/缺少恢复策略）。"""


class ExternalWriteContract(BaseModel):
    """EXTERNAL_WRITE capability 的准入声明（Missing recovery policy = 0）。"""

    model_config = ConfigDict(extra="forbid")
    capability_id: str = Field(min_length=3)
    target_system: str = Field(min_length=1)
    write_type: str                      # idempotent | queryable | irreversible
    idempotency_support: bool = False
    verification_support: bool = False
    rollback_support: bool = False
    side_effect_level: str = "EXTERNAL_WRITE"
    approval_requirement: str = "human"  # human | governed

    @field_validator("write_type")
    @classmethod
    def _wt(cls, v: str) -> str:
        if v not in WRITE_TYPES:
            raise ValueError(f"write_type 须为 {WRITE_TYPES} 之一")
        return v

    def __post_init__(self) -> None:  # pydantic v2 用 model_validator 更顺，这里手检
        pass

    def validate_coherence(self) -> "ExternalWriteContract":
        """类型-能力一致性：idempotent 须 idempotency_support；queryable 须
        verification_support——不一致即缺少可靠恢复策略，拒绝准入。"""
        if self.write_type == "idempotent" and not self.idempotency_support:
            raise ExternalWriteError(
                f"{self.capability_id}: write_type=idempotent 须声明 idempotency_support")
        if self.write_type == "queryable" and not self.verification_support:
            raise ExternalWriteError(
                f"{self.capability_id}: write_type=queryable 须声明 verification_support")
        if self.write_type == "irreversible" and (self.idempotency_support
                                                  or self.verification_support):
            raise ExternalWriteError(
                f"{self.capability_id}: irreversible 不得声明幂等/可查询（否则应归 A/B 类）")
        return self


class ExternalWriteGovernor:
    """外部写运行时治理：账本承载生命周期，transport 注入（可替换/可模拟）。

    transports: {target_system: callable(payload, idempotency_key) -> dict}
    verifiers:  {target_system: callable(payload, idempotency_key) -> str}
                （返回 "completed" | "failed" | "unknown"；仅 Type B 声明）
    registries: P4 IsolationRegistries（外部写域默认拒绝）。
    """

    def __init__(self, ws: Workspace, transports: dict[str, Callable] | None = None,
                 verifiers: dict[str, Callable] | None = None,
                 registries: IsolationRegistries | None = None):
        self.ws = ws
        self.transports = transports or {}
        self.verifiers = verifiers or {}
        self.registries = registries

    # ---- 账本读写 ----
    def _records(self, write_id: str) -> list[dict]:
        return [e["record"] for e in self.ws.events()
                if e["record_type"] == "ExternalWriteRecord"
                and e["record"].get("write_id") == write_id]

    def _latest(self, write_id: str) -> dict:
        recs = self._records(write_id)
        if not recs:
            raise ExternalWriteError(f"外部写 {write_id} 不在账本")
        return recs[-1]

    def _transition(self, prev: dict, state: str, **extra) -> dict:
        if state not in LIFECYCLE:
            raise ExternalWriteError(f"未知生命周期状态 {state}")
        if state not in _ALLOWED.get(prev["state"], set()):
            raise ExternalWriteError(
                f"非法生命周期迁移 {prev['state']} → {state}"
                f"（write {prev['write_id']}）")
        from .workspace import ExternalWriteRecord
        carry = ("research_task_id", "capability_id", "target_system", "write_type",
                 "idempotency_key", "decision_id", "approval", "payload_digest",
                 "result_digest", "verification", "error", "note")
        fields = {  # 既有事实全部前向携带；extra 显式覆盖（如 authorize 新 decision）
            **{k: prev.get(k) or "" for k in carry},
            "write_id": prev["write_id"],
            "intent": prev.get("intent", {}),
            "state": state, **extra}
        rec = ExternalWriteRecord(**fields)
        self.ws.append(rec)
        return rec.model_dump()

    # ---- 生命周期 ----
    def plan(self, contract: ExternalWriteContract, intent: dict,
             research_task_id: str = "") -> str:
        contract.validate_coherence()
        write_id = "XW-" + hashlib.sha256(
            f"{contract.capability_id}|{digest(intent)}".encode()).hexdigest()[:12]
        if self._records(write_id):
            raise ExternalWriteError(f"同 intent 外部写已存在 {write_id}（幂等计划）")
        from .workspace import ExternalWriteRecord
        self.ws.append(ExternalWriteRecord(
            write_id=write_id, research_task_id=research_task_id,
            capability_id=contract.capability_id,
            target_system=contract.target_system, write_type=contract.write_type,
            idempotency_key=self._derive_key(write_id, intent),
            intent=intent, state="planned"))
        return write_id

    @staticmethod
    def _derive_key(write_id: str, intent: dict) -> str:
        """幂等键从账本身份派生（restart 后稳定——Case B 的恢复基础）。"""
        return "idem-" + hashlib.sha256(
            f"{write_id}|{digest(intent)}".encode()).hexdigest()[:16]

    def authorize(self, write_id: str, decision_id: str, approval: str = "") -> dict:
        """授权 = GovernanceDecision 裁决 + workspace 外部写域双闸。"""
        prev = self._latest(write_id)
        if not decision_id:
            raise ExternalWriteError("authorize 须提供 decision_id（治理裁决）")
        decision = next((e["record"] for e in self.ws.events()
                         if e["record_type"] == "GovernanceDecision"
                         and e["record"]["decision_id"] == decision_id), None)
        if decision is None or not decision.get("allow_evidence"):
            raise ExternalWriteError(
                f"裁决 {decision_id} 不在账本或未允许——外部写默认不可执行")
        if self.registries is not None:
            self.registries.check_external_write_scope(
                self.ws.study_dir.name, prev["target_system"])
        return self._transition(prev, "authorized", decision_id=decision_id,
                                approval=approval or decision.get("actor", ""))

    def submit(self, write_id: str, payload: dict) -> dict:
        prev = self._latest(write_id)
        if prev["state"] != "authorized":
            raise ExternalWriteError(
                f"未授权外部写拒绝执行（state={prev['state']}）——"
                f"capability 注册存在 ≠ 允许写")
        transport = self.transports.get(prev["target_system"])
        if transport is None:
            raise ExternalWriteError(f"target {prev['target_system']} 无 transport")
        submitted = self._transition(prev, "submitted", payload_digest=digest(payload))
        try:
            result = transport(payload, prev.get("idempotency_key", ""))
        except Exception as exc:  # noqa: BLE001 —— 外部异常=状态未知，不猜成功
            return self._transition(submitted, "unknown", error=f"{type(exc).__name__}: {exc}")
        return self._transition(submitted, "acknowledged",
                                result_digest=digest(result))

    # ---- 恢复（三分类） ----
    def recover(self, write_id: str) -> dict:
        """unknown 状态的恢复决策：按契约类型走 A/B/C 策略。"""
        prev = self._latest(write_id)
        if prev["state"] != "unknown":
            raise ExternalWriteError(f"recover 仅作用于 unknown（当前 {prev['state']}）")
        wtype = prev["write_type"]
        if wtype == "idempotent":
            # Type A：同幂等键重试（外部去重，重复副作用=0）
            return self._retry_with_key(prev)
        if wtype == "queryable":
            # Type B：先 verify 再决定——verify 不可用/仍未知 → 不重试
            outcome = self._verify_external(prev)
            if outcome == "completed":
                return self._transition(prev, "acknowledged",
                                        verification="verified-completed")
            if outcome == "failed":
                return self._retry_with_key(prev, verification="verified-failed")
            return self._transition(prev, "unknown",
                                    verification="verify-unknown；禁止盲重试")
        # Type C：不自动重试，进入人工处置
        return self._transition(prev, "recovery_requires_review",
                                note="irreversible/unknown：自动重试被策略禁止")

    def _retry_with_key(self, prev: dict, **extra) -> dict:
        transport = self.transports.get(prev["target_system"])
        if transport is None:
            raise ExternalWriteError(f"target {prev['target_system']} 无 transport")
        submitted = self._transition(prev, "submitted", **extra)
        try:
            result = transport(prev.get("intent", {}), prev.get("idempotency_key", ""))
        except Exception as exc:  # noqa: BLE001
            return self._transition(submitted, "unknown", error=f"{type(exc).__name__}: {exc}")
        return self._transition(submitted, "acknowledged",
                                result_digest=digest(result))

    def _verify_external(self, prev: dict) -> str:
        verifier = self.verifiers.get(prev["target_system"])
        if verifier is None:
            return "unknown"  # 声明 queryable 却无 verifier → 保持 unknown，不盲试
        try:
            return str(verifier(prev.get("intent", {}), prev.get("idempotency_key", "")))
        except Exception:  # noqa: BLE001 —— verify 失败本身也是未知
            return "unknown"

    def verify(self, write_id: str) -> dict:
        """显式核销（acknowledged → verified → completed 主路径）。

        Type A（幂等）的核销 = 同键重发——幂等保证结果等价，任何成功回应
        即证明先前写入已持久；Type B 走声明的 verifier。
        """
        prev = self._latest(write_id)
        if prev["write_type"] == "idempotent":
            transport = self.transports.get(prev["target_system"])
            if transport is None:
                raise ExternalWriteError(f"target {prev['target_system']} 无 transport")
            try:
                transport(prev.get("intent", {}), prev.get("idempotency_key", ""))
            except Exception:
                raise ExternalWriteError("外部核验未完成（幂等键重发失败）")
            verified = self._transition(prev, "verified",
                                        verification="idempotent-resend")
            return self._transition(verified, "completed")
        outcome = self._verify_external(prev)
        if outcome != "completed":
            raise ExternalWriteError(f"外部核验未完成（{outcome}）")
        verified = self._transition(prev, "verified", verification="verified-completed")
        return self._transition(verified, "completed")

    def resolve_review(self, write_id: str, new_state: str,
                       reviewer: str, note: str = "") -> dict:
        """人工处置 recovery_requires_review（唯一离开该状态的路径）。"""
        prev = self._latest(write_id)
        if prev["state"] != "recovery_requires_review":
            raise ExternalWriteError("resolve_review 仅作用于 recovery_requires_review")
        if new_state not in ("failed", "acknowledged", "completed", "verified"):
            raise ExternalWriteError("人工改判目标须为 failed/acknowledged/completed/verified")
        if not reviewer:
            raise ExternalWriteError("人工处置必须署名 reviewer")
        return self._transition(prev, new_state,
                                approval=f"review:{reviewer}", note=note)

    def writes(self) -> list[dict]:
        """全部外部写的最新状态（replay 派生——只读，零执行）。"""
        latest: dict[str, dict] = {}
        for e in self.ws.events():
            if e["record_type"] == "ExternalWriteRecord":
                latest[e["record"]["write_id"]] = e["record"]
        return list(latest.values())


__all__ = ["ExternalWriteContract", "ExternalWriteError", "ExternalWriteGovernor",
           "LIFECYCLE", "WRITE_TYPES"]
