"""Research Workspace Schema v1（G4）——Harness Research Loop 的状态载体。

五类记录（pydantic，全部带时间与 provenance）：
- ResearchTask        研究任务（哪个客户端/模型、什么问题、状态）
- KnowledgeProvenance 知识获取记录（source_type 五分，收 Router/litread/method 输出）
- ToolExecution       工具执行记录（参数/输出摘要、治理账本锚点、verdicts）
- Evidence            证据记录（source_type 固定 CURRENT_STUDY，含方法规则引用与证伪状态）
- WorkspaceState      由事件流重放得到的工作区状态汇总

存储：append-only JSONL（<root>/<study_id>/events.jsonl），每行
{"seq","record_type","record","appended_at"}；root 缺省 var/workspace，
MRA_WORKSPACE_ROOT 可覆盖（课题部署可指向课题仓 AgentLab）。

回放：Workspace.replay() 从事件流重建 WorkspaceState——任何客户端、任何模型
产生的研究过程都可完整记录与回放（H5 验收第 4 条的载体）。

边界：本模块只写 workspace 事件流；不写 Local KG（铁律），不替代审计账本
（治理事件仍入 research.db，ToolExecution 以 governance_event_id 锚接）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SOURCE_TYPES = ("LOCAL_KG", "EXTERNAL_LIVE", "LITERATURE",
                "METHOD_KNOWLEDGE", "CURRENT_STUDY")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(obj: Any) -> str:
    """确定性摘要（canonical JSON → sha256），供参数/输出指纹。"""
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResearchTask(BaseModel):
    """G2 一等研究任务契约（v1.2 加性扩展；旧字段向后兼容）。

    铁律：research_question 不得被 Agent 静默改变——问题变更须显式产生
    revision/child task（parent_task_id 指回原任务）。
    """
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=3)
    question: str = Field(min_length=1)
    target: str = ""
    client: str = "unknown"          # zcode / claude-code / mcp / cli / human …
    model: str = ""                  # 空=人工/离线计划（Harness 不绑定模型）
    status: str = "open"             # open / done / abandoned
    # ---- G2 contract（可选，缺省兼容旧行为） ----
    objective: str = ""
    research_question: str = ""      # 缺省回填 question（同一语义）
    task_type: str = "exploratory_association"
    entities: list[str] = Field(default_factory=list)
    available_data: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)  # 如 forbid:EXTERNAL_WRITE
    requested_outputs: list[str] = Field(default_factory=list)
    current_stage: str = "created"
    parent_task_id: str | None = None
    created_by: str = "unknown"
    created_at: str = Field(default_factory=_now)

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("open", "done", "abandoned"):
            raise ValueError("status 须为 open/done/abandoned")
        return v


class KnowledgeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: str
    source_name: str = Field(min_length=1)
    source_id: str = ""
    retrieved_at: str
    query: str = ""
    external_ids: dict[str, str] = Field(default_factory=dict)  # pmid/doi/taxon…
    database_version: str | None = None
    raw_sha256: str | None = None
    evidence_status: str = ""

    @field_validator("source_type")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in SOURCE_TYPES:
            raise ValueError(f"source_type 须为 {SOURCE_TYPES} 之一")
        return v


class ToolExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str = Field(min_length=3)
    task_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_type: str = Field(min_length=1)   # r / python / knowledge / mcp / cli
    params_digest: str = ""
    output_digest: str = ""
    governance_event_id: str | None = None  # 审计账本锚点（mra.pep 账本）
    governance_verdicts: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=_now)
    finished_at: str | None = None
    status: str = "ok"                     # ok / error / denied

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("ok", "error", "denied"):
            raise ValueError("status 须为 ok/error/denied")
        return v


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=3)
    task_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    effect: dict[str, Any] = Field(default_factory=dict)   # rho/q/n/coef…
    analysis_version: str = ""                              # 如 atlas=v4(canonical)
    method_rules_applied: list[str] = Field(default_factory=list)
    source_type: str = "CURRENT_STUDY"                      # 证据域固定，不得伪装外部知识
    lineage: list[KnowledgeProvenance] = Field(default_factory=list)  # 依据的知识来源
    falsification: str = "none"   # none / sensitivity_passed / downgraded / refuted
    # ---- Evidence Governance 扩展（加性，v1.1）----
    candidate_id: str | None = None          # 溯源 CandidateResult.analysis_id
    governance: dict[str, Any] = Field(default_factory=dict)  # verdict/checks/actor
    canonical: bool = False                  # set_canonical 置位（需 supporting_lineage）
    supporting_lineage: list[str] = Field(default_factory=list)
    supersedes_seq: int | None = None        # 状态转换引用的前事件 seq（历史不覆盖）
    reason: str | None = None                # 转换理由（mutation 必填）
    created_at: str = Field(default_factory=_now)

    @field_validator("source_type")
    @classmethod
    def _fixed(cls, v: str) -> str:
        if v != "CURRENT_STUDY":
            raise ValueError("Evidence.source_type 固定为 CURRENT_STUDY（隔离铁律）")
        return v

    @field_validator("falsification")
    @classmethod
    def _fals(cls, v: str) -> str:
        if v not in ("none", "sensitivity_passed", "downgraded", "refuted"):
            raise ValueError("falsification 须为 none/sensitivity_passed/downgraded/refuted")
        return v


class CandidateResult(BaseModel):
    """计算结果候选——语义："工具算出了什么"，不是"系统已经相信什么"。

    CandidateResult != Evidence：它由 COMPUTE_ONLY 能力产生，必须经
    Scientific Governance Gate（evaluate_candidate）裁决后才允许经
    workspace.record_evidence 进入 Scientific Ledger。
    """
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(min_length=3)
    capability_id: str = Field(min_length=3)
    implementation_id: str = Field(min_length=3)
    capability_version: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=1)
    output_summary: str = Field(min_length=1)
    # ---- 通用信封（v1.2 泛化）：领域专属输出进 typed payload，不再顶层加字段 ----
    result_type: str = "association"          # association/atlas_scan/diversity/enrichment/...
    result_schema: str = ""                   # schema_ref（payload 结构自描述）
    result_payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)  # {kind,path,sha256}
    metrics: dict[str, Any] = Field(default_factory=dict)
    # 首用例字段（向后兼容，迁移期 optional 语义：空 dict=未提供）
    effect_estimate: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    assumptions_checked: list[str] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)  # {level, message}
    provenance: dict[str, Any] = Field(default_factory=dict)
    deterministic: bool = True
    tool_execution_id: str | None = None
    created_at: str = Field(default_factory=_now)

    @field_validator("warnings")
    @classmethod
    def _warn(cls, v):
        for w in v:
            if not isinstance(w, dict) or w.get("level") not in ("info", "blocking"):
                raise ValueError("warning 须为 {level: info|blocking, message}")
        return v


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=3)   # 只面向能力；禁止绑定 implementation
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)   # 依赖图（step_id）
    expected_output: str = ""


class ResearchPlan(BaseModel):
    """G2 一等研究计划（显式、版本化；Planning 与 Execution 分离的证据物）。

    修订为 append-only：Plan v2 以 supersedes_plan_id 指回 v1，历史不覆盖。
    """
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=3)
    research_task_id: str = Field(min_length=1)
    plan_version: int = Field(ge=1)
    supersedes_plan_id: str | None = None
    steps: list[PlanStep] = Field(min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    method_constraints: list[str] = Field(default_factory=list)   # METHOD_KNOWLEDGE rule_ids
    governance_requirements: list[str] = Field(default_factory=list)
    stopping_conditions: list[str] = Field(default_factory=list)
    fallback_paths: list[str] = Field(default_factory=list)
    created_by: str = "unknown"
    created_at: str = Field(default_factory=_now)

    @field_validator("steps")
    @classmethod
    def _steps(cls, v: list[PlanStep]) -> list[PlanStep]:
        ids = [s.step_id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id 重复")
        for s in v:
            unknown = set(s.depends_on) - set(ids)
            if unknown:
                raise ValueError(f"step {s.step_id} 依赖不存在的步骤 {unknown}")
        return v


class LoopEvent(BaseModel):
    """G2 循环事件：stage 迁移 / 四门裁决 / 计划采纳 / 终止（可重放的研究过程）。"""
    model_config = ConfigDict(extra="forbid")
    research_task_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)  # stage_entered|gate_verdict|plan_adopted|terminal
    stage: str = ""
    gate: str = ""                   # plan|execution|evidence|mutation
    verdict: str = ""
    detail: str = ""
    at: str = Field(default_factory=_now)


class GovernanceDecision(BaseModel):
    """Scientific Ledger 可验证的正式治理裁决（一等账本对象）。

    record_evidence 不再接受调用方自报 allow_evidence；必须提交 decision_id，
    由 Workspace 按账本六验（存在性/同一候选/指纹一致/allow/未失效/lineage）
    自行核验。canonical_eligible 只代表"允许进入 canonical 决策"。
    """
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(min_length=3)
    analysis_id: str = Field(min_length=3)
    candidate_event_seq: int = Field(ge=1)          # 候选在流中的事件 seq
    candidate_hash: str = Field(min_length=8)       # digest(CandidateResult record)
    policy_id: str = "scientific-governance"
    policy_version: str = "1.1.0"
    checks: list[dict[str, Any]] = Field(default_factory=list)
    allow_evidence: bool = False
    canonical_eligible: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    governance_event_id: str | None = None          # 外部治理账本锚（如 audit ledger）
    actor: str = "unknown"
    client: str = "unknown"
    model: str = ""
    created_at: str = Field(default_factory=_now)
    supersedes_decision_id: str | None = None       # 再裁决引用（旧决策由此失效）
    valid: bool = True                              # 被取代即置 False（新事件记录）


class WorkspaceState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    study_id: str
    n_events: int = 0
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_queries: int = 0
    tool_executions: int = 0
    candidate_results: int = 0
    governance_decisions: int = 0
    research_plans: int = 0
    loop_events: int = 0
    narrative_version: str = ""
    canonical_refs: dict[str, str] = Field(default_factory=dict)


_RECORD_TYPES = {"ResearchTask": ResearchTask, "KnowledgeProvenance": KnowledgeProvenance,
                 "ToolExecution": ToolExecution, "Evidence": Evidence,
                 "CandidateResult": CandidateResult,
                 "GovernanceDecision": GovernanceDecision,
                 "ResearchPlan": ResearchPlan, "LoopEvent": LoopEvent}


class Workspace:
    """append-only 事件流 + 回放重建。"""

    def __init__(self, study_id: str, root: Path | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", study_id):
            raise ValueError("study_id 仅允许字母数字_.-")
        root = Path(root) if root else Path(
            os.environ.get("MRA_WORKSPACE_ROOT",
                           Path(__file__).resolve().parents[2] / "var" / "workspace"))
        self.study_dir = root / study_id
        self.events_path = self.study_dir / "events.jsonl"

    def append(self, record: BaseModel) -> int:
        rtype = type(record).__name__
        if rtype not in _RECORD_TYPES:
            raise ValueError(f"不支持的记录类型 {rtype}")
        self.study_dir.mkdir(parents=True, exist_ok=True)
        seq = self._next_seq()
        line = {"seq": seq, "record_type": rtype,
                "record": record.model_dump(), "appended_at": _now()}
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return seq

    def events(self) -> list[dict]:
        if not self.events_path.is_file():
            return []
        out = []
        for raw in self.events_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                out.append(json.loads(raw))
        return out

    def replay(self) -> WorkspaceState:
        """从事件流重建状态（回放；Evidence 末条为准按 evidence_id 去重）。"""
        state = WorkspaceState(study_id=self.study_dir.name)
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for ev in self.events():
            state.n_events += 1
            rtype, rec = ev["record_type"], ev["record"]
            if rtype == "ResearchTask":
                state.tasks.append(rec)
            elif rtype == "KnowledgeProvenance":
                state.knowledge_queries += 1
            elif rtype == "ToolExecution":
                state.tool_executions += 1
            elif rtype == "CandidateResult":
                state.candidate_results += 1
            elif rtype == "GovernanceDecision":
                state.governance_decisions += 1
            elif rtype == "ResearchPlan":
                state.research_plans += 1
            elif rtype == "LoopEvent":
                state.loop_events += 1
            elif rtype == "Evidence":
                evidence_by_id[rec["evidence_id"]] = rec  # 后写覆盖=修订可追溯
        state.evidence = list(evidence_by_id.values())
        return state

    def _next_seq(self) -> int:
        events = self.events()
        return (events[-1]["seq"] + 1) if events else 1


__all__ = ["CandidateResult", "Evidence", "GovernanceDecision", "KnowledgeProvenance",
           "LoopEvent", "PlanStep", "ResearchPlan", "ResearchTask", "SOURCE_TYPES",
           "ToolExecution", "Workspace", "WorkspaceState", "digest"]
