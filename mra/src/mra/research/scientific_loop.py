"""G2 Scientific Research Loop——最小科研任务状态机（runtime-independent）。

只实现科研任务状态、科研规划语义与治理约束；generic loop/dispatch/session/
streaming/retry/protocol/memory 全部归 upstream runtime（用户裁决边界）。

七阶段固定状态机（每步显式输入/输出/状态迁移，不靠提示词隐式表示进度）：
  ResearchTask → Gap Assessment → Knowledge Acquisition → Method Constraint
  Resolution → ResearchPlan → Governed Execution → Candidate/Evidence
  Evaluation → Workspace Update → terminal。

四门穿透（不是末端统一检查）：plan_gate → execute → execution_gate →
candidate → evidence_gate（账本六验） → state_mutation_gate（workspace.* 已内建）。

合法停止（"不能继续做"是合法科研结果而非 Agent failure）：
  task_completed / insufficient_data / unresolved_method_gap /
  blocking_governance / no_valid_capability / evidence_insufficient。

Planning 与 Execution 分离：plan 是显式版本化对象（ResearchPlan，append-only
修订）；执行只消费 plan，不得事后改写成"原本就计划如此"。
"""
from __future__ import annotations

from typing import Any, Callable

from ..capability import CapabilityRegistry, default_registry
from ..governance import evaluate_candidate
from ..resources import WORKSPACE_BUDGET_SCOPE, ResourceBudget, ResourceUsage
from ..workspace import (CandidateResult, LoopEvent, ResearchPlan,
                         ResearchTask, Workspace)

STAGES = ("gap_assessment", "knowledge_acquisition", "method_constraint_resolution",
          "planning", "governed_execution", "evidence_evaluation", "workspace_update")
TERMINAL_STATES = ("task_completed", "insufficient_data", "unresolved_method_gap",
                   "blocking_governance", "no_valid_capability", "evidence_insufficient",
                   "resource_budget_exhausted")  # P1：给定资源边界内无法继续=合法停止


class LoopStopped(Exception):
    """合法终止（携带 terminal 状态），不是失败。"""

    def __init__(self, state: str, detail: str = ""):
        super().__init__(f"terminal={state}: {detail}")
        self.state = state
        self.detail = detail


# ---------------- 四门 ----------------

def plan_gate(plan: ResearchPlan, registry: CapabilityRegistry) -> dict[str, Any]:
    """Plan Gate：步骤能力存在、只面向 capability_id、方法约束与停止条件在场。"""
    problems: list[str] = []
    known = set(registry.list_capabilities())
    for step in plan.steps:
        if step.capability_id not in known:
            problems.append(f"未知能力 {step.capability_id}")
        if "implementation" in str(step.inputs).lower() and "implementation_id" in step.inputs:
            problems.append(f"步骤 {step.step_id} 绑定了 implementation_id（禁止）")
    if not plan.method_constraints:
        problems.append("缺少 method_constraints（须引用 METHOD_KNOWLEDGE 规则）")
    if not plan.stopping_conditions:
        problems.append("缺少 stopping_conditions")
    return {"gate": "plan", "allow": not problems, "problems": problems}


def execution_gate(capability_id: str, registry: CapabilityRegistry,
                   task: ResearchTask, runtime_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execution Gate：能力可用 + 任务约束（如 forbid:EXTERNAL_WRITE）不违背。"""
    problems: list[str] = []
    try:
        impl = registry.resolve(capability_id)
    except KeyError as exc:
        return {"gate": "execution", "allow": False,
                "problems": [f"no_valid_capability: {exc}"]}
    if impl.availability != "available":
        problems.append(f"实现不可用（{impl.availability}）")
    for constraint in task.constraints:
        if constraint.startswith("forbid:"):
            forbidden = constraint.split(":", 1)[1]
            if impl.side_effect == forbidden:
                problems.append(f"任务约束禁止 {forbidden}，而能力 side_effect={impl.side_effect}")
        if constraint.startswith("require_governance:") and \
                impl.governance_level != constraint.split(":", 1)[1]:
            problems.append(f"治理等级不满足约束 {constraint}")
    if impl.side_effect == "EXTERNAL_WRITE" and impl.governance_level != "governed":
        problems.append("EXTERNAL_WRITE 未达 governed")
    return {"gate": "execution", "allow": not problems, "problems": problems}


def evidence_gate(candidate: CandidateResult, decision) -> dict[str, Any]:
    """Evidence Gate：裁决允许（账本六验在 record_evidence 内执行）。"""
    return {"gate": "evidence", "allow": bool(decision.allow_evidence),
            "problems": [] if decision.allow_evidence else decision.blocking_reasons}


# ---------------- 状态机 ----------------

class ScientificLoop:
    """确定性科研循环引擎（同一引擎可被任何 runtime 驱动：standalone/dsh/OpenCode）。"""

    def __init__(self, study_id: str, registry: CapabilityRegistry | None = None,
                 workspace_root=None, executor: Callable[..., dict] | None = None,
                 graph_snapshot_id: str | None = None,
                 kg_visibility=None):
        self.study_id = study_id
        self.registry = registry or default_registry()
        self.ws = Workspace(study_id, root=workspace_root)
        self._executor = executor  # 可注入执行器（默认 registry.invoke）
        self._graph_snapshot_id = graph_snapshot_id  # None=从 kg 快照解析；""=显式无图谱
        self._kg_visibility = kg_visibility  # P4：IsolationRegistries（None=不启用）

    def _snapshot_id(self) -> str:
        """本 loop 的知识上下文（KG 快照 id）——进候选与证据 provenance（v1.1.0）。

        P4：snapshot identity ≠ access permission——启用可见性注册表时，
        显式 override 须通过权限检查，自动解析只取当前 workspace 可见的
        最新快照（private/restricted 不可见则跳过）。
        """
        if self._graph_snapshot_id is not None:
            if self._kg_visibility is not None and not self._kg_visibility.check_kg_access(
                    self.study_id, self._graph_snapshot_id):
                from ..isolation import IsolationError
                raise IsolationError(
                    f"KG 快照 {self._graph_snapshot_id} 对 workspace "
                    f"{self.study_id} 不可见（private/restricted）")
            return self._graph_snapshot_id
        try:
            from ..kg.snapshot import latest_snapshot, list_snapshots
            if self._kg_visibility is not None:
                for snap in reversed(list_snapshots()):
                    if self._kg_visibility.check_kg_access(self.study_id,
                                                           snap["snapshot_id"]):
                        return snap["snapshot_id"]
                return ""
            return latest_snapshot().name
        except Exception:
            return ""

    def _emit(self, task_id: str, kind: str, **kw) -> None:
        self.ws.append(LoopEvent(research_task_id=task_id, kind=kind, **kw))

    def _stage(self, task_id: str, stage: str) -> None:
        self._emit(task_id, "stage_entered", stage=stage)

    def open_task(self, task: ResearchTask) -> None:
        self.ws.append(task)

    def assess_gaps(self, task: ResearchTask, entities: list[str],
                    analysis_types: list[str]) -> dict[str, Any]:
        self._stage(task.task_id, "gap_assessment")
        out = self.registry.invoke("gap.check",
                                   {"entities": entities, "analysis_types": analysis_types},
                                   context={"graph": self._graph()})
        if out.get("n_method_gaps", 0) and any(
                not m["covered"] for m in out.get("analysis_types", [])):
            method_gaps = [m["analysis_type"] for m in out["analysis_types"] if not m["covered"]]
            self._emit(task.task_id, "terminal", verdict="unresolved_method_gap",
                       detail=";".join(method_gaps))
            raise LoopStopped("unresolved_method_gap", ";".join(method_gaps))
        return out

    def acquire_knowledge(self, task: ResearchTask, term: str, question: str) -> dict[str, Any]:
        self._stage(task.task_id, "knowledge_acquisition")
        return self.registry.invoke("knowledge.route", {"term": term, "question": question},
                                    context={"graph": self._graph()})

    def resolve_method_constraints(self, task: ResearchTask,
                                   query: str) -> list[str]:
        self._stage(task.task_id, "method_constraint_resolution")
        out = self.registry.invoke("method.query", {"query": query, "k": 3})
        rules = [r["rule_id"] for r in out["rules"] if r.get("structured")]
        if not rules:
            self._emit(task.task_id, "terminal", verdict="unresolved_method_gap",
                       detail=f"无结构化规则命中: {query}")
            raise LoopStopped("unresolved_method_gap", query)
        return rules

    def adopt_plan(self, task: ResearchTask, plan: ResearchPlan) -> dict[str, Any]:
        # B4.1/B3 幂等重驱动：同 (plan_id, version) 已采纳 → 不重复入账、不重复事件
        for ev in self.ws.events():
            if ev["record_type"] == "ResearchPlan":
                p = ev["record"]
                if p["plan_id"] == plan.plan_id and \
                        p["plan_version"] == plan.plan_version:
                    return {"gate": "plan", "allow": True, "problems": [],
                            "reused": True}
        self._stage(task.task_id, "planning")
        verdict = plan_gate(plan, self.registry)
        self._emit(task.task_id, "gate_verdict", gate="plan",
                   verdict="allow" if verdict["allow"] else "block",
                   detail=";".join(verdict["problems"]))
        if not verdict["allow"]:
            self._emit(task.task_id, "terminal", verdict="blocking_governance",
                       detail="plan gate")
            raise LoopStopped("blocking_governance", ";".join(verdict["problems"]))
        self.ws.append(plan)
        self._emit(task.task_id, "plan_adopted", detail=f"{plan.plan_id}@v{plan.plan_version}")
        return verdict

    def execute_step(self, task: ResearchTask, plan: ResearchPlan, step,
                     runtime_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        # B4.1/B3 幂等重驱动：已完成 COMPUTE 步骤的重复执行不是新科研事件——
        # deterministic 候选直接复用（零新事件）；非确定性候选按 capability
        # contract 应版本化 analysis_id 后重算（Workspace 唯一性守卫会硬拒同名）。
        try:
            impl_probe = self.registry.resolve(step.capability_id)
        except KeyError:
            impl_probe = None
        if impl_probe is not None and impl_probe.side_effect == "COMPUTE_ONLY":
            existing = self._find_candidate(task.task_id,
                                            step.inputs.get("analysis_id") or "")
            if existing is not None and existing.deterministic:
                return {"kind": "candidate", "candidate": existing,
                        "reused": True, "verdicts": []}  # 幂等复用：零记账
        # P1 Pre-execution Budget Gate：耗尽后禁止开启新的高成本操作
        # （已完成步骤的幂等重驱动不受阻——那是免费恢复，不是新消耗）
        gate = self._usage_gate(task.task_id)
        if not gate["verdict"]["allow"]:
            exhausted = ";".join(gate["verdict"]["exhausted"])
            self._emit(task.task_id, "terminal", verdict="resource_budget_exhausted",
                       detail=exhausted)
            raise LoopStopped("resource_budget_exhausted", exhausted)
        self._stage(task.task_id, "governed_execution")
        verdict = execution_gate(step.capability_id, self.registry, task, runtime_ctx)
        self._emit(task.task_id, "gate_verdict", gate="execution",
                   verdict="allow" if verdict["allow"] else "block",
                   detail=f"{step.step_id}:{step.capability_id}")
        if not verdict["allow"]:
            if any("no_valid_capability" in p for p in verdict["problems"]):
                self._emit(task.task_id, "terminal", verdict="no_valid_capability",
                           detail=step.capability_id)
                raise LoopStopped("no_valid_capability", step.capability_id)
            self._emit(task.task_id, "terminal", verdict="blocking_governance",
                       detail=f"execution gate:{step.step_id}")
            raise LoopStopped("blocking_governance", ";".join(verdict["problems"]))
        impl = self.registry.resolve(step.capability_id)
        import time as _time
        t0 = _time.perf_counter()
        if impl.side_effect == "COMPUTE_ONLY":
            result = (self._executor or self.registry.invoke)(
                step.capability_id, dict(step.inputs), context=runtime_ctx or {})
            candidate = CandidateResult(**result["candidate"])
            updates: dict = {}
            if not candidate.research_task_id:  # B4.1：loop 产出一律 task 打标
                updates["research_task_id"] = task.task_id
            if not candidate.graph_snapshot_id:  # v1.1.0：知识上下文随行
                updates["graph_snapshot_id"] = self._snapshot_id()
            if updates:
                candidate = candidate.model_copy(update=updates)
            self.ws.append(candidate)
            self._emit_usage(task, plan, step, (_time.perf_counter() - t0) * 1000,
                             compute_only=True, result=result)
            return {"kind": "candidate", "candidate": candidate,
                    "verdicts": result.get("execution_verdicts", [])}
        result = (self._executor or self.registry.invoke)(
            step.capability_id, dict(step.inputs), context=runtime_ctx or {})
        self._emit_usage(task, plan, step, (_time.perf_counter() - t0) * 1000,
                         compute_only=False, result=result)
        return {"kind": "result", "result": result}

    def evaluate_and_commit(self, task: ResearchTask, candidate: CandidateResult,
                            rules: list[str], execution_verdicts: list[dict] | None = None,
                            sensitivity_status: str = "none_required",
                            actor: str = "loop") -> dict[str, Any]:
        # B4.1/B3 幂等重驱动：已有有效等价裁决（同候选指纹、未失效、未被取代）
        # → 直接复用，不产生第二个等价 decision。
        existing = self._find_valid_decision(candidate)
        if existing is not None:
            return {"decision": existing, "reused": True}
        self._stage(task.task_id, "evidence_evaluation")
        seq = self.ws.events()[-1]["seq"]
        decision = evaluate_candidate(
            candidate, candidate_event_seq=self._candidate_seq(candidate),
            method_rules_applied=rules, sensitivity_status=sensitivity_status,
            execution_governance={"verdicts": execution_verdicts or [{"rule": "loop", "verdict": "PASS"}]},
            actor=actor, client=task.client, model=task.model)
        if candidate.research_task_id and not decision.research_task_id:
            decision = decision.model_copy(
                update={"research_task_id": candidate.research_task_id})
        self.ws.append(decision)
        verdict = evidence_gate(candidate, decision)
        self._emit(task.task_id, "gate_verdict", gate="evidence",
                   verdict="allow" if verdict["allow"] else "block",
                   detail=decision.decision_id)
        if not verdict["allow"]:
            self._emit(task.task_id, "terminal", verdict="evidence_insufficient",
                       detail=";".join(decision.blocking_reasons))
            raise LoopStopped("evidence_insufficient", ";".join(decision.blocking_reasons))
        return {"decision": decision}

    def commit_evidence(self, task: ResearchTask, candidate: CandidateResult,
                        decision, claim: str,
                        evidence_id: str | None = None) -> dict[str, Any]:
        ev_id = evidence_id or f"EV-{task.task_id}"
        # B4.1/B3 幂等重驱动：同 (evidence_id, decision) 已提交 → 零新事件
        for ev in self.ws.events():
            if ev["record_type"] != "Evidence":
                continue
            rec = ev["record"]
            if rec.get("evidence_id") == ev_id and \
                    (rec.get("governance") or {}).get(
                        "decision", {}).get("decision_id") == decision.decision_id:
                return {"study_id": self.study_id, "committed": False,
                        "already_committed": True, "evidence_id": ev_id,
                        "event_seq": ev["seq"]}
        self._stage(task.task_id, "workspace_update")
        return self.registry.invoke("workspace.record_evidence",
            {"study_id": self.study_id,
             "record": {"evidence_id": ev_id, "task_id": task.task_id,
                        "claim": claim, "effect": candidate.metrics or candidate.effect_estimate,
                        "analysis_version": candidate.analysis_id,
                        "method_rules_applied": [], "candidate_id": candidate.analysis_id,
                        "graph_snapshot_id": self._snapshot_id()},
             "decision_id": decision.decision_id},
            context={"workspace_root": self._root(),
                     "workspace_id": self.study_id})  # P4：绑定 workspace 边界

    def complete(self, task_id: str, detail: str = "") -> None:
        self._emit(task_id, "terminal", verdict="task_completed", detail=detail)

    # ---- P1 Budget / Resource Metering ----
    def set_budget(self, budget: ResourceBudget, actor: str = "unknown") -> None:
        """挂接预算（runtime/governance metadata，不动 frozen ResearchTask）。

        防绕过：同 task 已有有效预算时，新预算必须以 supersedes_budget_id
        显式取代并携带 reason——禁止静默重置（plan revision/child task 不隐式
        获得新预算；child 无自有预算时沿 parent 链继承，见 resource_usage）。
        """
        ru = self.ws.resource_usage(budget.research_task_id)
        existing = ru.get("budget")
        if existing is not None and not budget.supersedes_budget_id:
            raise ValueError(
                f"task {budget.research_task_id} 已有有效预算 "
                f"{existing.get('budget_id')}——修订须显式 supersedes_budget_id + reason"
                f"（预算防绕过：禁止静默重置）")
        if existing is not None and budget.supersedes_budget_id != existing.get("budget_id"):
            raise ValueError(
                f"supersedes_budget_id 须指向当前有效预算 {existing.get('budget_id')}")
        self.ws.append(budget.model_copy(update={"set_by": budget.set_by or actor}))

    def record_usage(self, usage: ResourceUsage) -> int:
        """公开计量入口（runtime/planner/dsh adapter 的 model 调用也入同一账本）。"""
        return self.ws.append(usage)

    def _usage_gate(self, task_id: str) -> dict:
        """Pre-execution 门 + 当前 totals（账本重建，restart 后不重置）。

        P4：task 预算之上叠加 workspace 预算池（scope=__workspace__ 的
        ResourceBudget 约束全 workspace 合计）——child task 不能分裂绕过。
        """
        ru = self.ws.resource_usage(task_id)
        exhausted = list(ru["verdict"].get("exhausted") or [])
        ws_ru = self.ws.resource_usage(WORKSPACE_BUDGET_SCOPE)
        if ws_ru.get("budget") is not None and not ws_ru["verdict"]["allow"]:
            exhausted = [f"workspace:{x}" for x in ws_ru["verdict"]["exhausted"]]
            return {"verdict": {"allow": False, "exhausted": exhausted},
                    "totals": ru["totals"], "report": ru,
                    "workspace_totals": ws_ru["totals"]}
        return {"verdict": ru["verdict"], "totals": ru["totals"], "report": ru,
                "workspace_totals": ws_ru["totals"]}

    def _emit_usage(self, task: ResearchTask, plan: ResearchPlan, step,
                    wall_ms: float, compute_only: bool,
                    result: dict | None) -> None:
        """Post-execution accounting：真实执行才记账（幂等复用路径零记账）。"""
        ext = dict((result or {}).get("resource_usage") or {})
        if not ext and step.capability_id.startswith("literature."):
            cached = bool((result or {}).get("from_cache"))
            ext = {"external_api_calls": 0 if cached else 1,
                   "cache_hit_count": 1 if cached else 0,
                   "cache_miss_count": 0 if cached else 1}
        n_prior = sum(1 for e in self.ws.events()
                      if e["record_type"] == "ResourceUsage"
                      and e["record"].get("research_task_id") == task.task_id)
        self.ws.append(ResourceUsage(
            usage_id=f"RU-{task.task_id}-{n_prior + 1}",
            research_task_id=task.task_id, kind="execution",
            plan_id=plan.plan_id, step_id=step.step_id,
            capability_id=step.capability_id,
            execution_id=f"EX-{task.task_id}-{step.step_id}-{n_prior + 1}",
            model_id=str(ext.get("model_id", "")),
            provider=str(ext.get("provider", "")),
            model_calls=int(ext.get("model_calls", 0)),
            input_tokens=int(ext.get("input_tokens", 0)),
            output_tokens=int(ext.get("output_tokens", 0)),
            total_tokens=int(ext.get("total_tokens", 0)),
            external_api_calls=int(ext.get("external_api_calls", 0)),
            compute_duration_ms=round(wall_ms, 3) if compute_only else 0.0,
            wall_duration_ms=round(wall_ms, 3),
            retry_count=int(ext.get("retry_count", 0)),
            cache_hit_count=int(ext.get("cache_hit_count", 0)),
            cache_miss_count=int(ext.get("cache_miss_count", 0))))

    def budget_stop_summary(self, task: ResearchTask, plan: ResearchPlan) -> dict:
        """预算停止摘要：已完成/未完成步骤、停止原因、消耗、可复用产出。"""
        ru = self.ws.resource_usage(task.task_id)
        done = set()
        for e in self.ws.events():
            if e["record_type"] == "CandidateResult" and \
                    e["record"].get("research_task_id") == task.task_id:
                done.add(e["record"]["analysis_id"])
        steps = [{"step_id": s.step_id, "analysis_id": s.inputs.get("analysis_id", ""),
                  "completed": bool(s.inputs.get("analysis_id") and
                                    s.inputs.get("analysis_id") in done)}
                 for s in plan.steps]
        return {"stop_reason": "resource_budget_exhausted",
                "exhausted": ru["verdict"].get("exhausted", []),
                "completed_steps": [s["step_id"] for s in steps if s["completed"]],
                "pending_steps": [s["step_id"] for s in steps if not s["completed"]],
                "usage_totals": ru["totals"],
                "budget": ru["verdict"].get("budget"),
                "reusable": ru["reusable"]}

    # ---- 内部 ----
    def _find_candidate(self, task_id: str, analysis_id: str):
        """按 (task, analysis_id) 查已有候选（B4.1 身份域；恢复复用入口）。"""
        if not analysis_id:
            return None
        for ev in reversed(self.ws.events()):
            if ev["record_type"] != "CandidateResult":
                continue
            r = ev["record"]
            if r["analysis_id"] == analysis_id and \
                    r.get("research_task_id", "") == task_id:
                return CandidateResult(**r)
        return None

    def _find_valid_decision(self, candidate: CandidateResult):
        """查候选的现有有效裁决：同 analysis_id + 同候选指纹 + task 同域 +
        未失效 + 未被再裁决取代 → 复用（恢复重试不产生第二个等价 decision）。"""
        from ..workspace import GovernanceDecision, digest
        want_hash = digest(candidate.model_dump())
        events = self.ws.events()
        superseded = {e["record"].get("supersedes_decision_id")
                      for e in events if e["record_type"] == "GovernanceDecision"}
        for ev in reversed(events):
            if ev["record_type"] != "GovernanceDecision":
                continue
            d = ev["record"]
            if d["analysis_id"] != candidate.analysis_id or d["candidate_hash"] != want_hash:
                continue
            d_scope = d.get("research_task_id", "")
            if candidate.research_task_id and d_scope and \
                    d_scope != candidate.research_task_id:
                continue
            if not d.get("valid", True) or d["decision_id"] in superseded:
                continue
            return GovernanceDecision(**d)
        return None

    def _candidate_seq(self, candidate: CandidateResult) -> int:
        scope = getattr(candidate, "research_task_id", "")
        for ev in reversed(self.ws.events()):
            if ev["record_type"] == "CandidateResult" and \
                    ev["record"]["analysis_id"] == candidate.analysis_id:
                if scope and ev["record"].get("research_task_id", "") \
                        not in ("", scope):
                    continue  # 跨 task 同名候选不误取（B4.1 身份域）
                return ev["seq"]
        raise ValueError(f"候选 {candidate.analysis_id} 不在账本")

    def _graph(self):
        from ..kg.graph import KGGraph
        from ..kg.snapshot import latest_snapshot
        return KGGraph(latest_snapshot())

    def _root(self):
        return self.ws.study_dir.parent


__all__ = ["LoopStopped", "STAGES", "ScientificLoop", "TERMINAL_STATES",
           "evidence_gate", "execution_gate", "plan_gate"]
