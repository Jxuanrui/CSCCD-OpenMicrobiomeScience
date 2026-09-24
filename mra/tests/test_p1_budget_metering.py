"""P1 Budget / Resource Metering——Golden Budget Cases A–E + 治理与计量不变式。

核心原则（用户裁决）：**预算耗尽必须让 Agent 学会停下来，而不是学会绕过
预算继续完成任务。**

Case A — 正常预算：完整执行，task_completed，usage < budget
Case B — 模型预算耗尽：resource_budget_exhausted，不再请求模型，摘要完整
Case C — External API 预算耗尽：停止态是 resource_budget_exhausted 而非
        evidence_insufficient（"没预算继续查"≠"没有文献证据"）
Case D — Recovery：部分消耗后 crash → restart → 预算余额正确继承（不重置）
Case E — Idempotent retry：ACK 丢失重试，科研事件零新增，usage 零重复计费

治理不变式：
- 预算防绕过（无 supersedes 的静默重置被拒 / 错误取代目标被拒 / 合法修订 OK）
- plan revision 与 retry 天然继承 task 预算；child task 沿 parent 链继承
- 计量重建一致（aggregate == resource_usage totals）
- cache hit 不按 API 全量计费（计数 external=0）
- 计价与事实分离（两版价目表两版派生值，历史 usage 不被改写）
- 凭据不进 ResourceUsage（typed schema，extra=forbid）
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mra.capability import build_default_registry
from mra.research.scientific_loop import LoopStopped, ScientificLoop
from mra.resources import (ResourceBudget, ResourceUsage, aggregate_usage,
                           estimate_cost)
from mra.workspace import PlanStep, ResearchPlan, ResearchTask, Workspace

RULE = "method-zero-variance-guard-001"


def _cand(analysis_id, task_id):
    from mra.workspace import CandidateResult
    return CandidateResult(
        analysis_id=analysis_id, capability_id="diversity.alpha_shannon",
        implementation_id="mra.numpy", capability_version="1.0.0",
        implementation_version="1.0.0",
        input_fingerprint=f"sha256:{analysis_id}", output_summary="n=10",
        research_task_id=task_id, metrics={"n_samples": 10},
        provenance={"task": task_id})


def _model_exec(task_id, calls=None, in_tok=100, out_tok=50):
    """带 model 用量上报的注入执行器；calls 记录真实执行次数。"""
    calls = calls if calls is not None else []

    def _exec(capability_id, inputs, context=None):
        calls.append(inputs.get("analysis_id"))
        return {"candidate": _cand(inputs["analysis_id"], task_id).model_dump(),
                "execution_verdicts": [{"rule": "audit", "verdict": "PASS"}],
                "resource_usage": {"model_id": "glm-5.3", "provider": "ark",
                                   "model_calls": 1, "input_tokens": in_tok,
                                   "output_tokens": out_tok,
                                   "total_tokens": in_tok + out_tok}}
    return _exec


def _lit_exec(task_id, from_cache, calls=None):
    calls = calls if calls is not None else []

    def _exec(capability_id, inputs, context=None):
        calls.append(inputs.get("query"))
        return {"from_cache": from_cache, "n_papers": 3, "papers": [],
                "provenance": {"source_type": "EXTERNAL_LIVE"}}
    return _exec


def _plan(task_id, steps, plan_id="P1-P", version=1):
    return ResearchPlan(
        plan_id=plan_id, research_task_id=task_id, plan_version=version,
        steps=steps, method_constraints=[RULE],
        stopping_conditions=["insufficient_data", "resource_budget_exhausted"])


def _compute_step(sid, aid):
    return PlanStep(step_id=sid, capability_id="diversity.alpha_shannon",
                    inputs={"features": "species", "analysis_id": aid})


def _budget(task_id, budget_id="B-1", **kw):
    return ResourceBudget(budget_id=budget_id, research_task_id=task_id, **kw)


def _drive(loop, task, plan, i, claim=None):
    out = loop.execute_step(task, plan, plan.steps[i])
    res = loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    return loop.commit_evidence(task, out["candidate"], res["decision"],
                                claim=claim or f"step {i}",
                                evidence_id=f"EV-{task.task_id}-{plan.steps[i].step_id}")


# ---- Case A：正常预算 ----

def test_case_a_normal_budget_completes(tmp_path):
    task_id = "T-P1A"
    task = ResearchTask(task_id=task_id, question="正常预算", client="p1-test")
    plan = _plan(task_id, [_compute_step("s1", "P1-A1"), _compute_step("s2", "P1-A2")])
    loop = ScientificLoop("p1-a", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_model_exec(task_id))
    loop.open_task(task)
    loop.set_budget(_budget(task_id, max_model_calls=5, max_total_tokens=10000))
    _drive(loop, task, plan, 0)
    _drive(loop, task, plan, 1)
    loop.complete(task_id)
    ru = Workspace("p1-a", root=tmp_path).resource_usage(task_id)
    assert ru["totals"]["model_calls"] == 2 < 5          # usage < budget
    assert ru["verdict"]["allow"]                         # 未耗尽
    terminals = [e["record"] for e in loop.ws.events()
                 if e["record_type"] == "LoopEvent" and e["record"]["kind"] == "terminal"]
    assert terminals[-1]["verdict"] == "task_completed"   # 正常完成
    assert ru["by_model"]["glm-5.3"]["model_calls"] == 2  # 模型维度计量


# ---- Case B：模型预算耗尽 ----

def test_case_b_model_budget_exhausted_stops(tmp_path):
    task_id = "T-P1B"
    task = ResearchTask(task_id=task_id, question="模型预算耗尽", client="p1-test")
    plan = _plan(task_id, [_compute_step("s1", "P1-B1"), _compute_step("s2", "P1-B2"),
                           _compute_step("s3", "P1-B3")])
    calls = []
    loop = ScientificLoop("p1-b", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_model_exec(task_id, calls))
    loop.open_task(task)
    loop.set_budget(_budget(task_id, max_model_calls=2))
    _drive(loop, task, plan, 0)
    _drive(loop, task, plan, 1)
    with pytest.raises(LoopStopped) as ei:               # 第 3 步被预算门拦下
        loop.execute_step(task, plan, plan.steps[2])
    assert ei.value.state == "resource_budget_exhausted"
    assert len(calls) == 2                                # 未再请求模型
    terminals = [e["record"] for e in loop.ws.events()
                 if e["record_type"] == "LoopEvent" and e["record"]["kind"] == "terminal"]
    assert terminals[-1]["verdict"] == "resource_budget_exhausted"
    summary = loop.budget_stop_summary(task, plan)
    assert summary["completed_steps"] == ["s1", "s2"]
    assert summary["pending_steps"] == ["s3"]
    assert summary["usage_totals"]["model_calls"] == 2
    assert summary["reusable"]["evidence"] == 2           # 已产出证据可复用
    assert any("max_model_calls=2" in x for x in summary["exhausted"])


# ---- Case C：External API 预算耗尽（≠没有文献证据） ----

def test_case_c_external_budget_exhausted_not_no_evidence(tmp_path):
    task_id = "T-P1C"
    task = ResearchTask(task_id=task_id, question="外部检索预算", client="p1-test")
    steps = [PlanStep(step_id=f"q{i}", capability_id="literature.search",
                      inputs={"query": f"query {i}", "question": "q"})
             for i in (1, 2)]
    plan = _plan(task_id, steps)
    calls = []
    loop = ScientificLoop("p1-c", registry=build_default_registry(),
                          workspace_root=tmp_path,
                          executor=_lit_exec(task_id, from_cache=False, calls=calls))
    loop.open_task(task)
    loop.set_budget(_budget(task_id, max_external_api_calls=1))
    loop.execute_step(task, plan, plan.steps[0])          # 1 次真实外部检索
    with pytest.raises(LoopStopped) as ei:
        loop.execute_step(task, plan, plan.steps[1])
    # 关键语义：停止态是预算耗尽，而不是"没有文献证据"
    assert ei.value.state == "resource_budget_exhausted"
    assert ei.value.state != "evidence_insufficient"
    assert len(calls) == 1                                 # 未再发外部请求
    ru = Workspace("p1-c", root=tmp_path).resource_usage(task_id)
    assert ru["totals"]["external_api_calls"] == 1
    assert ru["totals"]["cache_miss_count"] == 1


# ---- Case D：Recovery——预算余额跨 crash 继承 ----

def test_case_d_recovery_budget_not_reset(tmp_path):
    task_id = "T-P1D"
    task = ResearchTask(task_id=task_id, question="恢复预算", client="p1-test")
    plan = _plan(task_id, [_compute_step("s1", "P1-D1"), _compute_step("s2", "P1-D2"),
                           _compute_step("s3", "P1-D3")])
    loop1 = ScientificLoop("p1-d", registry=build_default_registry(),
                           workspace_root=tmp_path, executor=_model_exec(task_id))
    loop1.open_task(task)
    loop1.set_budget(_budget(task_id, max_model_calls=2))
    _drive(loop1, task, plan, 0)                           # 消耗 1 次模型调用
    del loop1                                              # crash
    # restart：账本重建用量——预算不"满血复活"
    loop2 = ScientificLoop("p1-d", registry=build_default_registry(),
                           workspace_root=tmp_path, executor=_model_exec(task_id))
    ru_mid = loop2.ws.resource_usage(task_id)
    assert ru_mid["totals"]["model_calls"] == 1            # 余额正确继承
    assert ru_mid["verdict"]["allow"]                      # 还剩 1 次
    _drive(loop2, task, plan, 1)                           # 消耗第 2 次
    with pytest.raises(LoopStopped) as ei:                 # 第 3 步：余额 0
        loop2.execute_step(task, plan, plan.steps[2])
    assert ei.value.state == "resource_budget_exhausted"
    assert Workspace("p1-d", root=tmp_path).resource_usage(task_id)["totals"]["model_calls"] == 2


# ---- Case E：Idempotent retry 零重复计费 ----

def test_case_e_idempotent_retry_no_double_charge(tmp_path):
    task_id = "T-P1E"
    task = ResearchTask(task_id=task_id, question="幂等重试计费", client="p1-test")
    plan = _plan(task_id, [_compute_step("s1", "P1-E1")])
    calls = []
    loop1 = ScientificLoop("p1-e", registry=build_default_registry(),
                           workspace_root=tmp_path, executor=_model_exec(task_id, calls))
    loop1.open_task(task)
    loop1.set_budget(_budget(task_id, max_model_calls=5))
    _drive(loop1, task, plan, 0)                           # commit 成功…
    del loop1                                              # …ACK 丢失 + crash
    ru_before = Workspace("p1-e", root=tmp_path).resource_usage(task_id)
    # restart 后 caller 不确定是否成功 → 全链重驱动
    loop2 = ScientificLoop("p1-e", registry=build_default_registry(),
                           workspace_root=tmp_path, executor=_model_exec(task_id, calls))
    out = loop2.execute_step(task, plan, plan.steps[0])
    res = loop2.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    com = loop2.commit_evidence(task, out["candidate"], res["decision"],
                                claim="step 0",
                                evidence_id=f"EV-{task_id}-s1")
    assert out.get("reused") and res.get("reused") and com.get("already_committed")
    ru_after = Workspace("p1-e", root=tmp_path).resource_usage(task_id)
    # 科研事件零新增 & 资源零重复计费
    assert len(calls) == 1                                 # 模型只被真实调用一次
    assert ru_after["totals"]["n_records"] == ru_before["totals"]["n_records"]
    assert ru_after["totals"]["model_calls"] == ru_before["totals"]["model_calls"] == 1
    assert ru_after["totals"]["input_tokens"] == ru_before["totals"]["input_tokens"]


# ---- 治理不变式 ----

def test_governance_budget_silent_reset_rejected(tmp_path):
    """无 supersedes 的预算重置被拒（防 plan revision/child 绕预算）。"""
    task_id = "T-P1G"
    task = ResearchTask(task_id=task_id, question="防绕过", client="p1-test")
    loop = ScientificLoop("p1-g", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_model_exec(task_id))
    loop.open_task(task)
    loop.set_budget(_budget(task_id, max_model_calls=1))
    with pytest.raises(ValueError, match="防绕过|supersedes"):
        loop.set_budget(_budget(task_id, budget_id="B-2", max_model_calls=999))
    with pytest.raises(ValueError, match="须指向当前有效预算"):
        loop.set_budget(_budget(task_id, budget_id="B-3", max_model_calls=999,
                                supersedes_budget_id="B-NONE"))
    # 合法修订：显式取代 + 理由 → 允许（append-only，历史保留）
    loop.set_budget(_budget(task_id, budget_id="B-2", max_model_calls=10,
                            supersedes_budget_id="B-1", reason="用户提额",
                            set_by="human"))
    ru = loop.ws.resource_usage(task_id)
    assert ru["budget"]["budget_id"] == "B-2"
    assert ru["budget"]["max_model_calls"] == 10
    assert loop.ws.replay().budgets == 2                    # 修订历史不覆盖


def test_governance_plan_revision_and_child_inherit_budget(tmp_path):
    """plan revision / retry 天然继承 task 预算；child 沿 parent 链继承
    （无隐式新预算）。"""
    task_id = "T-P1H"
    task = ResearchTask(task_id=task_id, question="继承", client="p1-test")
    loop = ScientificLoop("p1-h", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_model_exec(task_id))
    loop.open_task(task)
    loop.set_budget(_budget(task_id, max_model_calls=1))
    plan_v1 = _plan(task_id, [_compute_step("s1", "P1-H1")], version=1)
    plan_v2 = _plan(task_id, [_compute_step("s1", "P1-H1"),
                              _compute_step("s2", "P1-H2")],
                    plan_id="P1-P2", version=2, )
    plan_v2 = plan_v2.model_copy(update={"supersedes_plan_id": "P1-P"})
    loop.adopt_plan(task, plan_v1)
    loop.adopt_plan(task, plan_v2)                          # revision 不重置预算
    _drive(loop, task, plan_v2, 0)                          # 消耗唯一 1 次调用
    with pytest.raises(LoopStopped) as ei:                  # 换 plan 也绕不过
        loop.execute_step(task, plan_v2, plan_v2.steps[1])
    assert ei.value.state == "resource_budget_exhausted"
    # child task：无自有预算 → 继承 parent 预算，用量并入 parent 口径
    child = ResearchTask(task_id="T-P1H-C", question="子任务", client="p1-test",
                         parent_task_id=task_id)
    loop.open_task(child)
    ru_child = loop.ws.resource_usage("T-P1H-C")
    assert ru_child["budget"]["budget_id"] == "B-1"         # 继承而非新预算
    assert ru_child["budget_inherited_via"] == ["T-P1H-C", task_id]
    ru_parent = loop.ws.resource_usage(task_id, include_children=True)
    assert "T-P1H-C" in ru_parent["usage_scope"]


def test_metering_reconstruction_consistency(tmp_path):
    """Usage reconstruction mismatch = 0：records 聚合 == resource_usage totals。"""
    ws = Workspace("p1-m", root=tmp_path)
    ws.append(ResearchTask(task_id="T-M", question="计量", client="t"))
    for i, (mc, ext, hit) in enumerate([(1, 0, 0), (0, 1, 0), (0, 0, 1)], start=1):
        ws.append(ResourceUsage(usage_id=f"RU-T-M-{i}", research_task_id="T-M",
                                kind="execution" if i < 3 else "cache_hit",
                                model_calls=mc, input_tokens=100 * mc,
                                output_tokens=50 * mc, total_tokens=150 * mc,
                                external_api_calls=ext, cache_hit_count=hit,
                                cache_miss_count=1 if ext else 0))
    ru = ws.resource_usage("T-M")
    manual = aggregate_usage([e["record"] for e in ws.events()
                              if e["record_type"] == "ResourceUsage"])
    assert ru["totals"]["model_calls"] == manual["model_calls"] == 1
    assert ru["totals"]["external_api_calls"] == 1
    assert ru["totals"]["cache_hit_count"] == 1
    assert all(ru["totals"][k] == manual[k]
               for k in ("input_tokens", "output_tokens", "total_tokens"))
    assert ru["by_capability"]["(未标注)"]["model_calls"] == 1


def test_metering_cache_hit_not_full_charge(tmp_path):
    """cache hit：计数 cache_hit_count，external_api_calls=0（不按全量计费）。"""
    task_id = "T-P1C2"
    task = ResearchTask(task_id=task_id, question="缓存计费", client="p1-test")
    steps = [PlanStep(step_id="q1", capability_id="literature.search",
                      inputs={"query": "cached query", "question": "q"})]
    plan = _plan(task_id, steps)
    loop = ScientificLoop("p1-c2", registry=build_default_registry(),
                          workspace_root=tmp_path,
                          executor=_lit_exec(task_id, from_cache=True))
    loop.open_task(task)
    loop.execute_step(task, plan, plan.steps[0])
    ru = loop.ws.resource_usage(task_id)
    assert ru["totals"]["external_api_calls"] == 0
    assert ru["totals"]["cache_hit_count"] == 1


def test_pricing_separation_from_measured_usage():
    """计价与事实分离：两版价目 → 两版派生成本；历史 usage 不被改写。"""
    totals = {"input_tokens": 14332, "output_tokens": 4000, "total_tokens": 18332,
              "model_calls": 7, "external_api_calls": 2,
              "compute_duration_ms": 41200.0}
    v1 = estimate_cost(totals, {"input_per_1k": 0.001, "output_per_1k": 0.002,
                                "currency": "USD"}, "internal-relay", "pricing-2026-08")
    v2 = estimate_cost(totals, {"input_per_1k": 0.002, "output_per_1k": 0.004,
                                "currency": "USD"}, "internal-relay", "pricing-2026-09")
    assert v1["estimated_cost"] < v2["estimated_cost"]     # 价格变化→新派生值
    assert v1["pricing_version"] != v2["pricing_version"]
    # 事实不变：计量字段不因价格变化而改
    assert totals["model_calls"] == 7 and totals["input_tokens"] == 14332
    assert v1["calculated_at"] and v2["calculated_at"]


def test_credentials_cannot_enter_resource_usage():
    """凭据纪律：typed schema（extra=forbid）结构性拒绝任意注入。"""
    ok = ResourceUsage(usage_id="RU-X-1", research_task_id="T-X",
                       provider="ark", model_id="glm-5.3")
    assert ok.provider == "ark"                             # provider/model 可记
    with pytest.raises(ValidationError):                    # api_key 无处安放
        ResourceUsage(usage_id="RU-X-2", research_task_id="T-X", api_key="sk-LEAK")
    with pytest.raises(ValidationError):
        ResourceUsage(usage_id="RU-X-3", research_task_id="T-X", token="abc")


def test_usage_gate_zero_budget_means_unbounded():
    """未配置维度 = unbounded 而非 0：零字段预算不拦任何执行。"""
    from mra.resources import budget_verdict, empty_totals
    v = budget_verdict(ResourceBudget(budget_id="B-U",
                                      research_task_id="T-U"), empty_totals())
    assert v["allow"] and v["exhausted"] == []
    # 配置后精确拦截
    v2 = budget_verdict(ResourceBudget(budget_id="B-U2", research_task_id="T-U",
                                       max_model_calls=1),
                        {**empty_totals(), "model_calls": 1})
    assert not v2["allow"] and any("max_model_calls=1" in x for x in v2["exhausted"])
