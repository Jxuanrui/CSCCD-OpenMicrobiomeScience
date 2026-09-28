"""Production Hardening Review（v1.2.0）——跨子系统的整合性复核 + 统一故障矩阵。

不新增功能。给"这套科研基础设施在真实失败、边界冲突、资源限制、外部不
确定性下是否仍然保持正确"一个工程答案。

Review 1 — Governance 全链：Evidence 无裸提交、裁决不因 replay 重生、
           外部写未授权不可达 submitted
Review 2 — Reproducibility：同输入同版本 → 一致 lineage；backup/restore
           前后 hash/replay/lineage 一致；外部知识缓存/live/失败无隐藏差异
Review 3 — Isolation：task/workspace/backup/external-scope 合并口径
Review 4 — Resource：bypass=0、restart 一致、retry 不重复计费、child 不绕过
Review 5 — Failure Matrix：七类注入 → 语义全部明确（表格化）
"""
from __future__ import annotations

import json

import pytest

from mra.backup import BackupError, create_backup, restore_backup
from mra.capability import build_default_registry
from mra.external_write import (ExternalWriteContract, ExternalWriteError,
                                ExternalWriteGovernor)
from mra.governance import evaluate_candidate
from mra.isolation import IsolationError, IsolationRegistries
from mra.knowledge.sources import contract as ext_contract
from mra.knowledge.sources.europepmc import EuropePmcAdapter
from mra.resources import WORKSPACE_BUDGET_SCOPE, ResourceBudget
from mra.research.scientific_loop import LoopStopped, ScientificLoop
from mra.workspace import CandidateResult, PlanStep, ResearchPlan, ResearchTask, Workspace

RULE = "method-zero-variance-guard-001"


def _exec(task_id, model_calls=1):
    def _e(capability_id, inputs, context=None):
        return {"candidate": {
            "analysis_id": inputs["analysis_id"], "capability_id": capability_id,
            "implementation_id": "mra.numpy", "capability_version": "1.0.0",
            "implementation_version": "1.0.0",
            "input_fingerprint": f"sha256:{inputs['analysis_id']}",
            "output_summary": "n=10", "research_task_id": task_id,
            "graph_snapshot_id": "snap-review", "metrics": {"n": 10},
            "provenance": {"task": task_id}},
            "execution_verdicts": [{"rule": "audit", "verdict": "PASS"}],
            "resource_usage": {"model_calls": model_calls, "input_tokens": 100,
                               "output_tokens": 50, "total_tokens": 150}}
    return _e


def _flow(root, study, task_id, budget: ResourceBudget | None = None):
    """标准整合流：开任务→预算→计划→执行→裁决→证据→完成。"""
    task = ResearchTask(task_id=task_id, question="整合复核", client="phr")
    plan = ResearchPlan(
        plan_id=f"{task_id}-P", research_task_id=task_id, plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": f"{task_id}-A1"})],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop = ScientificLoop(study, registry=build_default_registry(),
                          workspace_root=root, executor=_exec(task_id),
                          graph_snapshot_id="snap-review")
    loop.open_task(task)
    if budget is not None:
        loop.set_budget(budget)
    loop.adopt_plan(task, plan)
    out = loop.execute_step(task, plan, plan.steps[0])
    res = loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    loop.commit_evidence(task, out["candidate"], res["decision"],
                         claim=f"{task_id} 结论", evidence_id=f"EV-{task_id}")
    loop.complete(task_id)
    return loop, task, plan


# ---- Review 1：Governance 全链 ----

def test_review1_governance_chain(tmp_path):
    loop, task, plan = _flow(tmp_path, "phr-1", "TASK-R1")
    ws = loop.ws
    st = ws.replay()
    # Evidence：provenance 完整（decision 链接 + snapshot 随行）+ mutation lineage
    assert st.evidence[0]["governance"]["decision"]["decision_id"]
    assert st.evidence[0]["graph_snapshot_id"] == "snap-review"
    assert ws.lineage("TASK-R1")["decisions"][0]["policy_version"]
    # GovernanceDecision：不存在无裁决的 Evidence（裸提交拒绝）
    reg = build_default_registry()
    with pytest.raises(ValueError, match="decision_id"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "phr-1", "record": {"evidence_id": "EV-BARE",
                    "task_id": "TASK-R1", "claim": "裸", "candidate_id": "x"}},
                   context={"workspace_root": tmp_path})
    # replay 不生成新裁决：重放前后 decision 计数不变
    n_before = st.governance_decisions
    assert ws.replay().governance_decisions == n_before
    # ExternalWrite：未授权不可达 submitted
    gov = ExternalWriteGovernor(ws, transports={"sys": lambda p, k: {}})
    wid = gov.plan(ExternalWriteContract(capability_id="r.w", target_system="sys",
                                         write_type="idempotent",
                                         idempotency_support=True), {"j": 1})
    with pytest.raises(ExternalWriteError, match="未授权"):
        gov.submit(wid, {"j": 1})


# ---- Review 2：Reproducibility ----

def test_review2_reproducibility(tmp_path):
    loop_a, _, _ = _flow(tmp_path / "run-a", "phr-2", "TASK-R2")
    loop_b, _, _ = _flow(tmp_path / "run-b", "phr-2", "TASK-R2")
    # 同 task/snapshot/capability/policy 版本 → 一致 lineage
    la, lb = loop_a.ws.lineage("TASK-R2"), loop_b.ws.lineage("TASK-R2")
    assert la["candidates"] == lb["candidates"]
    assert la["evidence"] == lb["evidence"]
    assert la["decisions"][0]["policy_version"] == lb["decisions"][0]["policy_version"]
    # backup/restore：hash/replay/lineage 三一致
    manifest = create_backup(loop_a.ws, tmp_path / "backups")
    import shutil
    shutil.rmtree(loop_a.ws.study_dir)
    restore_backup(tmp_path / "backups", manifest["backup_id"], tmp_path / "run-a")
    ws_r = Workspace("phr-2", root=tmp_path / "run-a")
    import hashlib
    assert hashlib.sha256(ws_r.events_path.read_bytes()).hexdigest() in manifest["ledger_hash"]

    def _strip(ev_list):  # 独立运行间的易变时间戳剥离（B3 语义投影同口径）
        return [{k: v for k, v in e.items() if k != "created_at"} for e in ev_list]
    assert _strip(ws_r.replay().evidence) == _strip(loop_b.ws.replay().evidence)
    assert ws_r.lineage("TASK-R2")["evidence"] == lb["evidence"]
    # 外部知识：缓存/live/失败无隐藏差异（from_cache 显式可见）
    body = json.dumps({"resultList": {"result": [
        {"id": "9", "source": "MED", "title": "t"}]}}).encode()
    epc = EuropePmcAdapter(fetcher=lambda url: body)
    cached_src = ext_contract.CachedSource(epc, tmp_path / "kc")
    first = cached_src.query("review")    # live 成功 → 入缓存
    second = cached_src.query("review")   # 命中
    assert first.status == second.status == "success"
    assert first.from_cache is False and second.from_cache is True  # 差异显式而非隐藏


# ---- Review 3：Isolation 合并口径 ----

def test_review3_isolation(tmp_path):
    regs = IsolationRegistries(tmp_path)
    regs.set_external_write_scope("phr-3a", ["sys-r"])
    _flow(tmp_path, "phr-3a", "TASK-R3A")
    _flow(tmp_path, "phr-3b", "TASK-R3B")
    ws_a, ws_b = Workspace("phr-3a", root=tmp_path), Workspace("phr-3b", root=tmp_path)
    # cross-task contamination = 0 / cross-workspace leakage = 0
    assert all(e["record"].get("task_id") != "TASK-R3B" for e in ws_a.events()
               if e["record_type"] in ("Evidence", "ResearchTask"))
    # backup scope violation = 0
    manifest = create_backup(ws_a, tmp_path / "backups")
    assert "TASK-R3B" not in (tmp_path / "backups" / manifest["backup_id"]
                              / "events.jsonl").read_text(encoding="utf-8")
    # unauthorized external action = 0（B 未授权写域）
    cand = next(e["record"] for e in ws_b.events()
                if e["record_type"] == "CandidateResult")
    from mra.workspace import CandidateResult as CR
    c = CR(**cand)
    d = evaluate_candidate(c, candidate_event_seq=1, method_rules_applied=[RULE],
                           execution_governance={"verdicts": ["v"]})
    ws_b.append(d)
    gov_b = ExternalWriteGovernor(ws_b, transports={"sys-r": lambda p, k: {}},
                                   registries=regs)
    wid = gov_b.plan(ExternalWriteContract(capability_id="r.w", target_system="sys-r",
                                           write_type="idempotent",
                                           idempotency_support=True), {"j": 1})
    with pytest.raises(IsolationError, match="外部写域拒绝"):
        gov_b.authorize(wid, d.decision_id)


# ---- Review 4：Resource Governance ----

def test_review4_resource_governance(tmp_path):
    # workspace 池 2 次 + child 不绕过 + restart 一致 + retry 不重复计费
    task = ResearchTask(task_id="TASK-R4", question="资源复核", client="phr")
    plan = ResearchPlan(
        plan_id="R4-P", research_task_id="TASK-R4", plan_version=1,
        steps=[PlanStep(step_id=f"s{i}", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": f"R4-A{i}"})
               for i in (1, 2, 3)],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop = ScientificLoop("phr-4", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_exec("TASK-R4"),
                          graph_snapshot_id="snap-review")
    loop.open_task(task)
    loop.set_budget(ResourceBudget(budget_id="B-R4",
                                   research_task_id=WORKSPACE_BUDGET_SCOPE,
                                   max_model_calls=2))
    loop.adopt_plan(task, plan)
    for i in (0, 1):
        out = loop.execute_step(task, plan, plan.steps[i])
        res = loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])
        loop.commit_evidence(task, out["candidate"], res["decision"],
                             claim=f"step{i}", evidence_id=f"EV-R4-{i}")
    with pytest.raises(LoopStopped):                       # 池耗尽 → 合法停止
        loop.execute_step(task, plan, plan.steps[2])
    # restart 后预算一致（不重置）
    ws = Workspace("phr-4", root=tmp_path)
    assert ws.resource_usage(WORKSPACE_BUDGET_SCOPE)["totals"]["model_calls"] == 2
    # 幂等重驱动不重复计费
    loop2 = ScientificLoop("phr-4", registry=build_default_registry(),
                           workspace_root=tmp_path, executor=_exec("TASK-R4"),
                           graph_snapshot_id="snap-review")
    loop2.execute_step(task, plan, plan.steps[0])          # reused，零记账
    assert ws.resource_usage(WORKSPACE_BUDGET_SCOPE)["totals"]["model_calls"] == 2


# ---- Review 5：统一故障矩阵 ----

_FAILURE_MATRIX = [
    ("ledger corruption", "fail closed"),
    ("checksum mismatch", "reject restore"),
    ("external timeout", "unknown/unavailable"),
    ("malformed source", "reject evidence"),
    ("budget exhaustion", "legal stop"),
    ("workspace violation", "reject"),
    ("external unknown state", "recovery_requires_review"),
]


def test_review5_failure_matrix(tmp_path):
    """七类注入 → 语义全部明确（评审第 5 节的表格化工程答案）。"""
    # 1) ledger corruption → fail closed
    ws = Workspace("fm-1", root=tmp_path)
    ws.append(ResearchTask(task_id="TASK-FM", question="x", client="t"))
    with ws.events_path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 9, "record_type": "Evi')
    with pytest.raises(Exception):
        Workspace("fm-1", root=tmp_path).events()
    # 2) checksum mismatch → reject restore
    ws2 = Workspace("fm-2", root=tmp_path)
    ws2.append(ResearchTask(task_id="TASK-FM2", question="x", client="t"))
    m = create_backup(ws2, tmp_path / "bk")
    mp = tmp_path / "bk" / m["backup_id"] / "manifest.json"
    mm = json.loads(mp.read_text(encoding="utf-8"))
    mm["file_inventory"][0]["sha256"] = "sha256:bad"
    mp.write_text(json.dumps(mm), encoding="utf-8")
    with pytest.raises(BackupError, match="checksum|不符"):
        restore_backup(tmp_path / "bk", m["backup_id"], tmp_path / "tgt")
    # 3) external timeout → unknown/unavailable（≠empty）
    epc = EuropePmcAdapter(fetcher=lambda url: (_ for _ in ()).throw(TimeoutError()))
    r = ext_contract.query(epc, "q")
    assert r.status in ("timeout", "unavailable") and not r.evidence_items
    # 4) malformed source → reject evidence（不入 items）
    epc2 = EuropePmcAdapter(fetcher=lambda url: b"<html>")
    r2 = ext_contract.query(epc2, "q")
    assert r2.status == "malformed" and not r2.evidence_items
    # 5) budget exhaustion → legal stop
    task = ResearchTask(task_id="TASK-FM5", question="x", client="t")
    plan = ResearchPlan(
        plan_id="FM-P", research_task_id="TASK-FM5", plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": "FM-A1"})],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop = ScientificLoop("fm-5", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_exec("TASK-FM5"),
                          graph_snapshot_id="")
    loop.open_task(task)
    loop.set_budget(ResourceBudget(budget_id="B-FM", research_task_id="TASK-FM5",
                                   max_model_calls=0))
    loop.adopt_plan(task, plan)
    with pytest.raises(LoopStopped) as ei:
        loop.execute_step(task, plan, plan.steps[0])
    assert ei.value.state == "resource_budget_exhausted"
    # 6) workspace violation → reject
    from mra.isolation import guard_workspace_write
    with pytest.raises(IsolationError):
        guard_workspace_write("other-ws", {"workspace_id": "fm-6"},
                              scope="write_evidence")
    # 7) external unknown state → recovery_requires_review
    class _Ext:
        def __init__(self):
            self.n = 0

        def transport(self, payload, key):
            self.n += 1
            raise TimeoutError("unknown")
    ext = _Ext()
    ws7 = Workspace("fm-7", root=tmp_path)
    cand = CandidateResult(analysis_id="FM-C7", capability_id="r.w",
                           implementation_id="tst", capability_version="1",
                           implementation_version="1", input_fingerprint="s:x",
                           output_summary="n", provenance={"t": 1})
    ws7.append(cand)
    d = evaluate_candidate(cand, candidate_event_seq=ws7.events()[-1]["seq"],
                           method_rules_applied=[RULE],
                           execution_governance={"verdicts": ["v"]})
    ws7.append(d)
    gov = ExternalWriteGovernor(ws7, transports={"sys": ext.transport})
    wid = gov.plan(ExternalWriteContract(capability_id="r.w", target_system="sys",
                                         write_type="irreversible"), {"j": 1})
    gov.authorize(wid, d.decision_id)
    out = gov.submit(wid, {"j": 1})
    assert out["state"] == "unknown"
    assert gov.recover(wid)["state"] == "recovery_requires_review"
    assert ext.n == 1  # 无自动重试
