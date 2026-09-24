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
from ..workspace import (CandidateResult, LoopEvent, ResearchPlan,
                         ResearchTask, Workspace)

STAGES = ("gap_assessment", "knowledge_acquisition", "method_constraint_resolution",
          "planning", "governed_execution", "evidence_evaluation", "workspace_update")
TERMINAL_STATES = ("task_completed", "insufficient_data", "unresolved_method_gap",
                   "blocking_governance", "no_valid_capability", "evidence_insufficient")


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
                 workspace_root=None, executor: Callable[..., dict] | None = None):
        self.study_id = study_id
        self.registry = registry or default_registry()
        self.ws = Workspace(study_id, root=workspace_root)
        self._executor = executor  # 可注入执行器（默认 registry.invoke）

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
        if impl.side_effect == "COMPUTE_ONLY":
            result = (self._executor or self.registry.invoke)(
                step.capability_id, dict(step.inputs), context=runtime_ctx or {})
            candidate = CandidateResult(**result["candidate"])
            self.ws.append(candidate)
            return {"kind": "candidate", "candidate": candidate,
                    "verdicts": result.get("execution_verdicts", [])}
        result = (self._executor or self.registry.invoke)(
            step.capability_id, dict(step.inputs), context=runtime_ctx or {})
        return {"kind": "result", "result": result}

    def evaluate_and_commit(self, task: ResearchTask, candidate: CandidateResult,
                            rules: list[str], execution_verdicts: list[dict] | None = None,
                            sensitivity_status: str = "none_required",
                            actor: str = "loop") -> dict[str, Any]:
        self._stage(task.task_id, "evidence_evaluation")
        seq = self.ws.events()[-1]["seq"]
        decision = evaluate_candidate(
            candidate, candidate_event_seq=self._candidate_seq(candidate),
            method_rules_applied=rules, sensitivity_status=sensitivity_status,
            execution_governance={"verdicts": execution_verdicts or [{"rule": "loop", "verdict": "PASS"}]},
            actor=actor, client=task.client, model=task.model)
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
                        decision, claim: str) -> dict[str, Any]:
        self._stage(task.task_id, "workspace_update")
        return self.registry.invoke("workspace.record_evidence",
            {"study_id": self.study_id,
             "record": {"evidence_id": f"EV-{task.task_id}", "task_id": task.task_id,
                        "claim": claim, "effect": candidate.metrics or candidate.effect_estimate,
                        "analysis_version": candidate.analysis_id,
                        "method_rules_applied": [], "candidate_id": candidate.analysis_id},
             "decision_id": decision.decision_id},
            context={"workspace_root": self._root()})

    def complete(self, task_id: str, detail: str = "") -> None:
        self._emit(task_id, "terminal", verdict="task_completed", detail=detail)

    # ---- 内部 ----
    def _candidate_seq(self, candidate: CandidateResult) -> int:
        for ev in reversed(self.ws.events()):
            if ev["record_type"] == "CandidateResult" and \
                    ev["record"]["analysis_id"] == candidate.analysis_id:
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
