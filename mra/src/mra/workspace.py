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

from .resources import (WORKSPACE_BUDGET_SCOPE, ResourceBudget, ResourceUsage,
                        aggregate_usage, budget_verdict)

SOURCE_TYPES = ("LOCAL_KG", "EXTERNAL_LIVE", "LITERATURE",
                "METHOD_KNOWLEDGE", "CURRENT_STUDY")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(obj: Any) -> str:
    """确定性摘要（canonical JSON → sha256），供参数/输出指纹。"""
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Production-readiness（凭据纪律）：账本结构性拒绝疑似凭据字段。
# 精确键名匹配（大小写不敏感）——"api_key_used"/"n_tokens" 等业务字段不受影响。
_CREDENTIAL_KEY_NAMES = frozenset({
    "api_key", "apikey", "api_token", "token", "secret", "secret_key",
    "password", "passwd", "authorization", "credentials", "private_key",
    "access_token", "refresh_token", "client_secret", "bearer"})


def find_credential_keys(obj: Any, prefix: str = "") -> list[str]:
    """递归扫描疑似凭据键名（只看键名，不看值——避免误伤业务数据）。"""
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if str(k).lower() in _CREDENTIAL_KEY_NAMES:
                hits.append(path)
            hits.extend(find_credential_keys(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_credential_keys(v, f"{prefix}[{i}]"))
    return hits


def _group_usage(usages: list[dict], key: str) -> dict[str, dict]:
    """按 capability/model 等维度分组聚合用量。"""
    groups: dict[str, list[dict]] = {}
    for u in usages:
        groups.setdefault(u.get(key) or "(未标注)", []).append(u)
    return {k: aggregate_usage(v) for k, v in groups.items()}


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
    graph_snapshot_id: str = ""              # 结论所依据的 KG 快照（v1.1.0 科研可重复性）
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

    身份域（B4.1）：analysis_id 的正式 lookup 身份为 (research_task_id,
    analysis_id)——task scope 内结构性唯一（Workspace.append 硬拒重复），
    跨 task 允许同名（并发任务隔离）。空 research_task_id 为 legacy 无域
    条目（向后兼容，不参与唯一性强制）。
    """
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(min_length=3)
    capability_id: str = Field(min_length=3)
    implementation_id: str = Field(min_length=3)
    capability_version: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=1)
    output_summary: str = Field(min_length=1)
    research_task_id: str = ""              # task scope（B4.1；空=legacy 无域）
    graph_snapshot_id: str = ""             # 知识上下文（v1.1.0；空=未用图谱）
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


class CrossWorkspaceReference(BaseModel):
    """P4 显式跨库证据引用：引用而非复制，携带授权与来源 provenance。

    默认 Evidence 是 workspace-private；跨库使用的唯一合法形态是本记录
    （由 isolation.cross_workspace_reference 在 read grant 校验后落账）。
    """
    model_config = ConfigDict(extra="forbid")
    reference_id: str = Field(min_length=3)
    evidence_id: str = Field(min_length=1)
    source_workspace_id: str = Field(min_length=1)
    target_workspace_id: str = Field(min_length=1)
    approval: str = ""               # 批准者/理由（grant.authorization；避开凭据键名）
    provenance: str = ""             # 引用动机与上下文
    created_at: str = Field(default_factory=_now)


class ExternalWriteRecord(BaseModel):
    """P5 外部写生命周期事件（append-only；每次状态迁移一条，replay 取最新）。

    intent/authorization（decision_id+approval）/result（digest）/verification
    全随行——Case F：replay 只重建状态，永不重新执行真实写（transport 不在
    Workspace 内是结构性保证）。
    """
    model_config = ConfigDict(extra="forbid")
    write_id: str = Field(min_length=3)
    research_task_id: str = ""
    capability_id: str = Field(min_length=1)
    target_system: str = Field(min_length=1)
    write_type: str = Field(min_length=1)     # idempotent | queryable | irreversible
    idempotency_key: str = ""                  # Type A：账本派生，restart 稳定
    intent: dict[str, Any] = Field(default_factory=dict)
    decision_id: str = ""                      # GovernanceDecision（授权 lineage）
    approval: str = ""
    state: str = Field(min_length=1)           # external_write.LIFECYCLE
    payload_digest: str = ""
    result_digest: str = ""
    verification: str = ""
    error: str = ""
    note: str = ""
    created_at: str = Field(default_factory=_now)


class GovernanceDecision(BaseModel):
    """Scientific Ledger 可验证的正式治理裁决（一等账本对象）。

    record_evidence 不再接受调用方自报 allow_evidence；必须提交 decision_id，
    由 Workspace 按账本六验（存在性/同一候选/指纹一致/allow/未失效/lineage）
    自行核验。canonical_eligible 只代表"允许进入 canonical 决策"。

    身份域（B4.1）：research_task_id 非空时，候选解析按 (task, analysis_id)
    精确匹配（同 analysis_id 跨 task 不歧义）；同一候选允许多次合法再裁决
    （supersedes 链），但恢复重试不得产生第二个等价 decision（loop 层复用）。
    """
    model_config = ConfigDict(extra="forbid")
    decision_id: str = Field(min_length=3)
    analysis_id: str = Field(min_length=3)
    candidate_event_seq: int = Field(ge=1)          # 候选在流中的事件 seq
    candidate_hash: str = Field(min_length=8)       # digest(CandidateResult record)
    research_task_id: str = ""                      # task scope（B4.1；空=legacy）
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
    resource_usages: int = 0            # P1 计量记录数
    budgets: int = 0                     # P1 预算记录数
    cross_workspace_references: int = 0  # P4 显式跨库引用数
    external_write_records: int = 0       # P5 外部写生命周期事件数
    narrative_version: str = ""
    canonical_refs: dict[str, str] = Field(default_factory=dict)


_RECORD_TYPES = {"ResearchTask": ResearchTask, "KnowledgeProvenance": KnowledgeProvenance,
                 "ToolExecution": ToolExecution, "Evidence": Evidence,
                 "CandidateResult": CandidateResult,
                 "GovernanceDecision": GovernanceDecision,
                 "ResearchPlan": ResearchPlan, "LoopEvent": LoopEvent,
                 # P1 Budget/Resource Metering（v1.2.0 candidate 加性记录类型）
                 "ResourceUsage": ResourceUsage, "ResourceBudget": ResourceBudget,
                 # P4 Multi-workspace Isolation（加性记录类型）
                 "CrossWorkspaceReference": CrossWorkspaceReference,
                 # P5 EXTERNAL_WRITE Recovery（加性记录类型）
                 "ExternalWriteRecord": ExternalWriteRecord}


class Workspace:
    """append-only 事件流 + 回放重建。

    Durability（v1.1.0）：scientific commit acknowledgement ≈ durable commit——
    durable 模式（生产默认）下每次 append 先 flush 再 fsync，成功返回时事件
    已落盘；fsync/写入失败则回滚未确认尾部并抛出，replay 永远不会把未
    durable 确认的事件视为已提交。buffered 模式仅供批量导入/测试提速。
    """

    def __init__(self, study_id: str, root: Path | None = None,
                 durability: str | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", study_id):
            raise ValueError("study_id 仅允许字母数字_.-")
        root = Path(root) if root else Path(
            os.environ.get("MRA_WORKSPACE_ROOT",
                           Path(__file__).resolve().parents[2] / "var" / "workspace"))
        self.study_dir = root / study_id
        self.events_path = self.study_dir / "events.jsonl"
        self.durability = durability or os.environ.get(
            "MRA_LEDGER_DURABILITY", "durable")
        if self.durability not in ("durable", "buffered"):
            raise ValueError(f"未知 durability 模式 {self.durability}")

    def append(self, record: BaseModel) -> int:
        rtype = type(record).__name__
        if rtype not in _RECORD_TYPES:
            raise ValueError(f"不支持的记录类型 {rtype}")
        # Production-readiness 凭据纪律：任何账本记录不得携带疑似凭据字段
        leaked = find_credential_keys(record.model_dump())
        if leaked:
            raise ValueError(
                f"凭据纪律违例：{rtype} 携带疑似凭据字段 {leaked}——"
                f"secret 只经环境/secret provider 注入，永不入账本")
        self.study_dir.mkdir(parents=True, exist_ok=True)
        self._check_identity_invariants(record)
        seq = self._next_seq()
        line = {"seq": seq, "record_type": rtype,
                "record": record.model_dump(), "appended_at": _now()}
        start_size = self.events_path.stat().st_size if self.events_path.is_file() else 0
        try:
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                fh.flush()
                if self.durability == "durable":
                    os.fsync(fh.fileno())  # 返回成功 = 已落盘（非用户态 buffer）
        except OSError:
            self._rollback_tail(start_size)
            raise
        return seq

    def _rollback_tail(self, start_size: int) -> None:
        """写入/fsync 失败后回滚未确认尾部（best-effort）。

        回滚成功 → replay 看不到该事件（caller 收到异常，语义一致）；
        回滚失败（如磁盘满）→ 残留半行会被 fail-closed 检出，绝不静默视为已提交。
        """
        try:
            if self.events_path.is_file():
                with self.events_path.open("r+b") as fh:
                    fh.truncate(start_size)
        except OSError:
            pass

    def _check_identity_invariants(self, record: BaseModel) -> None:
        """B4.1 身份域铁律：task scope 内 analysis_id 结构性唯一。

        身份唯一性是 isolation invariant，由系统保证而非调用方约定——
        同一 (research_task_id, analysis_id) 的第二个 CandidateResult 直接
        拒绝（恢复重试应复用已有候选；非确定性重算应版本化 analysis_id）。
        legacy 无域条目（research_task_id 为空）不参与强制，向后兼容。
        """
        if type(record).__name__ != "CandidateResult":
            return
        scope = getattr(record, "research_task_id", "")
        if not scope:
            return
        for ev in self.events():
            if ev["record_type"] != "CandidateResult":
                continue
            other = ev["record"]
            if other.get("research_task_id", "") == scope and \
                    other["analysis_id"] == record.analysis_id:
                raise ValueError(
                    f"isolation invariant 违例：task {scope} 内 analysis_id "
                    f"'{record.analysis_id}' 已存在（seq {ev['seq']}）。"
                    f"恢复重试应复用已有候选，非确定性重算应版本化 analysis_id。")

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
            elif rtype == "ResourceUsage":
                state.resource_usages += 1
            elif rtype == "ResourceBudget":
                state.budgets += 1
            elif rtype == "CrossWorkspaceReference":
                state.cross_workspace_references += 1
            elif rtype == "ExternalWriteRecord":
                state.external_write_records += 1
            elif rtype == "Evidence":
                evidence_by_id[rec["evidence_id"]] = rec  # 后写覆盖=修订可追溯
        state.evidence = list(evidence_by_id.values())
        return state

    def _next_seq(self) -> int:
        events = self.events()
        return (events[-1]["seq"] + 1) if events else 1

    def lineage(self, research_task_id: str) -> dict[str, Any]:
        """按任务查全链血缘（只读报告层，Production-readiness observability 最小版）。

        回答"这个科研结论是怎么来的"：Task → Plan → 候选 → 裁决 → 证据
        （含 mutation）→ terminal，附版本随行（capability/implementation/
        model/client/policy）。legacy 无域候选按"被本任务裁决引用"回捞。
        """
        task = None
        plans: list[dict] = []
        candidates: list[dict] = []
        decisions: list[dict] = []
        evidence_events: list[dict] = []
        terminal: list[dict] = []
        for ev in self.events():
            rtype, rec = ev["record_type"], ev["record"]
            if rtype == "ResearchTask" and rec.get("task_id") == research_task_id:
                task = {"seq": ev["seq"], **rec}
            elif rtype == "ResearchPlan" and rec.get("research_task_id") == research_task_id:
                plans.append({"seq": ev["seq"], **rec})
            elif rtype == "LoopEvent" and rec.get("research_task_id") == research_task_id \
                    and rec.get("kind") == "terminal":
                terminal.append({"seq": ev["seq"], **rec})
            elif rtype == "Evidence" and rec.get("task_id") == research_task_id:
                evidence_events.append({"seq": ev["seq"], **rec})
        cand_ids = set()
        for ev in self.events():
            rtype, rec = ev["record_type"], ev["record"]
            if rtype != "GovernanceDecision":
                continue
            scoped = rec.get("research_task_id", "")
            referenced = any(e.get("candidate_id") == rec.get("analysis_id")
                             for e in evidence_events)
            if scoped == research_task_id or (not scoped and referenced):
                decisions.append({"seq": ev["seq"], **rec})
                cand_ids.add(rec.get("analysis_id"))
        for ev in self.events():
            rtype, rec = ev["record_type"], ev["record"]
            if rtype != "CandidateResult":
                continue
            scoped = rec.get("research_task_id", "")
            if scoped == research_task_id or (not scoped and rec["analysis_id"] in cand_ids):
                candidates.append({"seq": ev["seq"], **rec})
        mutations = [e for e in evidence_events if e.get("supersedes_seq") is not None
                     or e.get("reason")]
        # 证据按 evidence_id 呈现最新状态（mutation 后），与 replay 口径一致
        latest: dict[str, dict] = {}
        for e in evidence_events:
            latest[e["evidence_id"]] = e
        evidence = list(latest.values())
        return {
            "research_task_id": research_task_id,
            "workspace": self.study_dir.name,
            "task": task,
            "plans": plans,
            "candidates": [{"analysis_id": c["analysis_id"],
                            "capability_id": c["capability_id"],
                            "capability_version": c["capability_version"],
                            "implementation_id": c["implementation_id"],
                            "implementation_version": c["implementation_version"],
                            "input_fingerprint": c["input_fingerprint"],
                            "graph_snapshot_id": c.get("graph_snapshot_id", ""),
                            "deterministic": c.get("deterministic", True),
                            "seq": c["seq"]} for c in candidates],
            "decisions": [{"decision_id": d["decision_id"],
                           "analysis_id": d["analysis_id"],
                           "policy_id": d.get("policy_id"),
                           "policy_version": d.get("policy_version"),
                           "actor": d.get("actor"), "client": d.get("client"),
                           "model": d.get("model"),
                           "allow_evidence": d.get("allow_evidence"),
                           "supersedes_decision_id": d.get("supersedes_decision_id"),
                           "seq": d["seq"]} for d in decisions],
            "evidence": [{"evidence_id": e["evidence_id"], "claim": e["claim"],
                          "candidate_id": e.get("candidate_id"),
                          "graph_snapshot_id": e.get("graph_snapshot_id", ""),
                          "falsification": e.get("falsification", "none"),
                          "canonical": e.get("canonical", False),
                          "supersedes_seq": e.get("supersedes_seq"),
                          "reason": e.get("reason"), "seq": e["seq"]}
                         for e in evidence],
            "mutations": len(mutations),
            "terminal": terminal[-1] if terminal else None,
        }

    def resource_usage(self, research_task_id: str,
                       include_children: bool = True) -> dict[str, Any]:
        """按任务聚合资源用量 + 有效预算 + 门控判定（P1 observability 出口）。

        totals 永远由账本重建（restart/replay 后预算不重置的构造性保证）；
        预算继承：自有 → parent_task_id 链。
        P4：research_task_id == WORKSPACE_BUDGET_SCOPE 时返回 workspace 级
        汇总——全部 ResourceUsage 合计（child task 无法分裂绕过）+
        workspace 级预算（scope 哨兵声明的 ResourceBudget）。
        """
        events = self.events()
        if research_task_id == WORKSPACE_BUDGET_SCOPE:
            usages = [e["record"] for e in events
                      if e["record_type"] == "ResourceUsage"]
            totals = aggregate_usage(usages)
            own = [e["record"] for e in events
                   if e["record_type"] == "ResourceBudget"
                   and e["record"].get("research_task_id") == WORKSPACE_BUDGET_SCOPE]
            budget = None
            if own:
                superseded = {b.get("supersedes_budget_id") for b in own}
                latest = [b for b in own if b.get("budget_id") not in superseded]
                budget = (latest or own)[-1]
            return {"research_task_id": WORKSPACE_BUDGET_SCOPE,
                    "usage_scope": [self.study_dir.name],
                    "totals": totals,
                    "by_capability": _group_usage(usages, "capability_id"),
                    "by_model": _group_usage(usages, "model_id"),
                    "budget": budget, "budget_inherited_via": [],
                    "verdict": budget_verdict(budget, totals),
                    "reusable": {}}
        task_record = next((e["record"] for e in events
                            if e["record_type"] == "ResearchTask"
                            and e["record"].get("task_id") == research_task_id), None)
        # 计量范围：本任务（+子任务，若选择）
        scope_ids = {research_task_id}
        if include_children and task_record is not None:
            child_ids = {research_task_id}
            while True:
                more = {e["record"]["task_id"] for e in events
                        if e["record_type"] == "ResearchTask"
                        and e["record"].get("parent_task_id") in child_ids
                        and e["record"]["task_id"] not in scope_ids}
                if not more:
                    break
                scope_ids |= more
                child_ids = more
        usages = [e["record"] for e in events
                  if e["record_type"] == "ResourceUsage"
                  and e["record"].get("research_task_id") in scope_ids]
        totals = aggregate_usage(usages)
        # 有效预算：自有（最新未被取代）→ parent_task_id 链继承
        budget = None
        chain: list[str] = []
        tid: str | None = research_task_id
        while tid:
            chain.append(tid)
            own = [e["record"] for e in events
                   if e["record_type"] == "ResourceBudget"
                   and e["record"].get("research_task_id") == tid]
            if own:
                superseded = {b.get("supersedes_budget_id") for b in own}
                candidates = [b for b in own if b.get("budget_id") not in superseded]
                budget = (candidates or own)[-1]
                break
            tid = next((e["record"].get("parent_task_id") for e in events
                        if e["record_type"] == "ResearchTask"
                        and e["record"].get("task_id") == tid), None)
        return {"research_task_id": research_task_id,
                "usage_scope": sorted(scope_ids),
                "totals": totals,
                "by_capability": _group_usage(usages, "capability_id"),
                "by_model": _group_usage(usages, "model_id"),
                "budget": budget, "budget_inherited_via": chain if budget else [],
                "verdict": budget_verdict(budget, totals),
                "reusable": {"candidates": sum(
                    1 for e in events if e["record_type"] == "CandidateResult"
                    and e["record"].get("research_task_id") == research_task_id),
                    "evidence": sum(
                    1 for e in events if e["record_type"] == "Evidence"
                    and e["record"].get("task_id") == research_task_id)}}


__all__ = ["CandidateResult", "Evidence", "GovernanceDecision", "KnowledgeProvenance",
           "LoopEvent", "PlanStep", "ResearchPlan", "ResearchTask", "SOURCE_TYPES",
           "ToolExecution", "Workspace", "WorkspaceState", "digest",
           "find_credential_keys"]
