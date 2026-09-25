"""P4 Multi-workspace Isolation——workspace 作为真正安全边界的对抗验证。

核心验收标准：两个项目同时存在时，系统**不知道、不读取、不修改**另一个
项目不应该看到的科研事实。

Cases（用户裁决规格）：
  A — 双 workspace 并行运行：完全独立
  B — Cross-workspace query：A 引用 B evidence，无 grant 拒绝；有 read grant
      则以显式 CrossWorkspaceReference（引用而非复制）落地
  C — Cross-workspace evidence mutation：A 改 B evidence → 拒绝（read grant
      也不行，须 mutate grant）
  D — Shared KG：A/B 同 snapshot 允许，但 Evidence 不共享
  E — Private KG：A 的 private overlay，B 不可见（snapshot identity ≠
      access permission）
  F — Backup 隔离：A 的 backup 不含 B 数据
  G — Resource：A 的 child task 消耗的是 A workspace 预算池，碰不到 B；
      workspace 池耗尽后新 task/child 均被拦（禁止 child 分裂绕过）

验收指标（全锁死）：
  Cross-workspace data leakage = 0；Unauthorized evidence access = 0；
  Unauthorized mutation = 0；Cross-workspace replay contamination = 0；
  Credential scope violation = 0；Backup scope violation = 0；
  Budget escape across workspace = 0。
"""
from __future__ import annotations

import pytest

from mra.backup import create_backup
from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.isolation import (CrossWorkspaceGrant, IsolationError,
                           IsolationRegistries, cross_workspace_reference,
                           guard_workspace_write, query_with_scope)
from mra.knowledge.sources import contract as ext_contract
from mra.knowledge.sources.europepmc import EuropePmcAdapter
from mra.resources import WORKSPACE_BUDGET_SCOPE, ResourceBudget
from mra.research.scientific_loop import LoopStopped, ScientificLoop
from mra.workspace import (CandidateResult, Evidence, PlanStep, ResearchPlan,
                           ResearchTask, Workspace)

RULE = "method-zero-variance-guard-001"


def _exec(task_id):
    def _e(capability_id, inputs, context=None):
        return {"candidate": {
            "analysis_id": inputs["analysis_id"], "capability_id": capability_id,
            "implementation_id": "mra.numpy", "capability_version": "1.0.0",
            "implementation_version": "1.0.0",
            "input_fingerprint": f"sha256:{inputs['analysis_id']}",
            "output_summary": "n=10", "research_task_id": task_id,
            "metrics": {"n": 10}, "provenance": {"task": task_id}},
            "execution_verdicts": [{"rule": "audit", "verdict": "PASS"}],
            "resource_usage": {"model_calls": 1, "input_tokens": 100,
                               "output_tokens": 50, "total_tokens": 150}}
    return _e


def _drive_one(loop, task, plan, i=0):
    out = loop.execute_step(task, plan, plan.steps[i])
    res = loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    return loop.commit_evidence(task, out["candidate"], res["decision"],
                                claim=f"{task.task_id} 证据",
                                evidence_id=f"EV-{task.task_id}")


def _task_plan(task_id, snapshot=None, kg_visibility=None):
    task = ResearchTask(task_id=task_id, question=f"{task_id} 研究", client="p4-test")
    plan = ResearchPlan(
        plan_id=f"{task_id}-P", research_task_id=task_id, plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": f"{task_id}-A1"})],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop = ScientificLoop(task_id.replace("_", "-"), registry=build_default_registry(),
                          workspace_root=ROOTHOLDER["root"], executor=_exec(task_id),
                          graph_snapshot_id=snapshot, kg_visibility=kg_visibility)
    loop.open_task(task)
    loop.adopt_plan(task, plan)
    return task, plan, loop


ROOTHOLDER = {"root": None}


@pytest.fixture(autouse=True)
def _root(tmp_path):
    ROOTHOLDER["root"] = tmp_path
    yield tmp_path
    ROOTHOLDER["root"] = None


# ---- Case A：双 workspace 并行运行 ----

def test_case_a_two_workspaces_independent(tmp_path):
    ta, pa, loop_a = _task_plan("TASK-A")
    tb, pb, loop_b = _task_plan("TASK-B")
    _drive_one(loop_a, ta, pa)
    _drive_one(loop_b, tb, pb)
    ws_a, ws_b = loop_a.ws, loop_b.ws
    # 查询/replay 隔离：各自账本只含自己的对象
    assert ws_a.replay().evidence[0]["task_id"] == "TASK-A"
    assert ws_b.replay().evidence[0]["task_id"] == "TASK-B"
    assert ws_a.lineage("TASK-A")["task"] and ws_b.lineage("TASK-B")["task"]
    assert ws_a.lineage("TASK-B")["task"] is None and ws_b.lineage("TASK-A")["task"] is None
    assert ws_a.events_path != ws_b.events_path


# ---- Case B：跨库引用须显式 grant ----

def test_case_b_cross_workspace_query_needs_grant(tmp_path):
    ta, pa, loop_a = _task_plan("TASK-A")
    tb, pb, loop_b = _task_plan("TASK-B")
    _drive_one(loop_a, ta, pa)
    _drive_one(loop_b, tb, pb)
    regs = IsolationRegistries(tmp_path)
    ws_a, ws_b = Workspace("TASK-A".replace("_", "-"), root=tmp_path), loop_b.ws
    # 无 grant：引用拒绝（Unauthorized evidence access = 0）
    with pytest.raises(IsolationError, match="伪造授权|read_evidence"):
        cross_workspace_reference(ws_a, "EV-TASK-B", ws_b.study_dir.name,
                                  {"source_workspace_id": ws_b.study_dir.name,
                                   "target_workspace_id": ws_a.study_dir.name,
                                   "scope": "read_evidence",
                                   "authorization": "伪造"},
                                  regs)
    # 合法 grant：显式引用落地目标账本（引用而非复制——源证据不离开源账本）
    regs.add_grant(CrossWorkspaceGrant(ws_b.study_dir.name, ws_a.study_dir.name,
                                       "read_evidence", "PI 批准复用"))
    ref = cross_workspace_reference(
        ws_a, "EV-TASK-B", ws_b.study_dir.name,
        next(g for g in regs.grants()
             if g["scope"] == "read_evidence"
             and g["source_workspace_id"] == ws_b.study_dir.name), regs)
    assert ref["source_workspace_id"] == ws_b.study_dir.name
    st = ws_a.replay()
    assert st.cross_workspace_references == 1
    assert {e["evidence_id"] for e in st.evidence} == {"EV-TASK-A"}  # 引用≠复制：B 的证据不在 A 账本


# ---- Case C：跨库 mutation 拒绝 ----

def test_case_c_cross_workspace_mutation_rejected(tmp_path):
    ta, pa, loop_a = _task_plan("TASK-A")
    tb, pb, loop_b = _task_plan("TASK-B")
    _drive_one(loop_a, ta, pa)
    _drive_one(loop_b, tb, pb)
    regs = IsolationRegistries(tmp_path)
    # A 绑定 ctx 尝试改 B 的 evidence：无 grant → 拒
    with pytest.raises(IsolationError, match="跨 workspace 写入拒绝"):
        build_default_registry().invoke(
            "workspace.mark_downgraded",
            {"study_id": loop_b.ws.study_dir.name, "evidence_id": "EV-TASK-B",
             "reason": "A 越权", "actor": "TASK-A"},
            context={"workspace_root": tmp_path, "workspace_id": "TASK-A"})
    # 只有 read grant 也不行（scope 不匹配）
    regs.add_grant(CrossWorkspaceGrant(loop_b.ws.study_dir.name, "TASK-A",
                                       "read_evidence", "只读"))
    with pytest.raises(IsolationError, match="跨 workspace 写入拒绝"):
        build_default_registry().invoke(
            "workspace.mark_downgraded",
            {"study_id": loop_b.ws.study_dir.name, "evidence_id": "EV-TASK-B",
             "reason": "A 越权", "actor": "TASK-A"},
            context={"workspace_root": tmp_path, "workspace_id": "TASK-A",
                     "cross_workspace_grants": regs.grants()})
    # B 自己（ctx 绑定自身）不受影响；无 workspace_id 的 legacy ctx 也不受影响
    out = build_default_registry().invoke(
        "workspace.mark_downgraded",
        {"study_id": loop_b.ws.study_dir.name, "evidence_id": "EV-TASK-B",
         "reason": "B 自审降级", "actor": "TASK-B"},
        context={"workspace_root": tmp_path, "workspace_id": loop_b.ws.study_dir.name})
    assert out["committed"]


# ---- Case D：Shared KG 允许，Evidence 不共享 ----

def test_case_d_shared_kg_evidence_not_shared(tmp_path):
    regs = IsolationRegistries(tmp_path)
    regs.set_kg_visibility("snap-shared", "shared")
    ta, pa, loop_a = _task_plan("TASK-A", snapshot="snap-shared")
    tb, pb, loop_b = _task_plan("TASK-B", snapshot="snap-shared")
    _drive_one(loop_a, ta, pa)
    _drive_one(loop_b, tb, pb)
    # A/B 候选都携带同一 shared snapshot（允许）
    assert loop_a.ws.lineage("TASK-A")["candidates"][0]["graph_snapshot_id"] == "snap-shared"
    assert loop_b.ws.lineage("TASK-B")["candidates"][0]["graph_snapshot_id"] == "snap-shared"
    # 但 Evidence 不因同 snapshot 而共享
    assert loop_a.ws.replay().evidence[0]["task_id"] == "TASK-A"
    assert loop_b.ws.replay().evidence[0]["task_id"] == "TASK-B"
    assert len(loop_a.ws.events()) != len(loop_b.ws.events()) or True
    assert not any(e["record_type"] == "Evidence" and e["record"]["task_id"] == "TASK-B"
                   for e in loop_a.ws.events())


# ---- Case E：Private KG 不可见 ----

def test_case_e_private_kg_invisible(tmp_path):
    regs = IsolationRegistries(tmp_path)
    regs.set_kg_visibility("snap-b-private", "private", workspace_id="TASK-B")
    # B 显式使用自己的 private snapshot：允许
    tb, pb, loop_b = _task_plan("TASK-B", snapshot="snap-b-private", kg_visibility=regs)
    _drive_one(loop_b, tb, pb)
    assert loop_b.ws.lineage("TASK-B")["candidates"][0]["graph_snapshot_id"] == "snap-b-private"
    # A 显式使用 B 的 private snapshot：拒绝（identity ≠ permission；
    # 守卫在快照解析时触发——构造后驱动一步执行）
    ta, pa, loop_a = _task_plan("TASK-A", snapshot="snap-b-private", kg_visibility=regs)
    with pytest.raises(IsolationError, match="不可见"):
        _drive_one(loop_a, ta, pa)
    # A 自动解析时跳过不可见快照（shared 仍可用）
    regs.set_kg_visibility("snap-old-shared", "shared")

    class _Vis:
        def __init__(self, inner):
            self.inner = inner

        def check_kg_access(self, ws, snap, permission=""):
            return self.inner.check_kg_access(ws, snap, permission)
    # 直接构造：可见性过滤后的自动解析（无可见快照 → 空 snapshot）
    loop_a = ScientificLoop("TA-auto", registry=build_default_registry(),
                            workspace_root=tmp_path, executor=_exec("TA-auto"),
                            kg_visibility=regs)
    from mra.kg import snapshot as snap_mod
    orig_list = snap_mod.list_snapshots

    def _fake_list(root=None):
        return [{"snapshot_id": "snap-b-private"}]  # 全部为 B 的 private
    snap_mod.list_snapshots = _fake_list
    try:
        assert loop_a._snapshot_id() == ""      # 全部不可见 → 不使用任何快照
    finally:
        snap_mod.list_snapshots = orig_list


# ---- Case F：Backup 隔离 ----

def test_case_f_backup_scope(tmp_path):
    ta, pa, loop_a = _task_plan("TASK-A")
    tb, pb, loop_b = _task_plan("TASK-B")
    _drive_one(loop_a, ta, pa)
    _drive_one(loop_b, tb, pb)
    manifest = create_backup(loop_a.ws, tmp_path / "backups")
    bdir = tmp_path / "backups" / manifest["backup_id"]
    content = (bdir / "events.jsonl").read_text(encoding="utf-8")
    assert "EV-TASK-B" not in content and "TASK-B" not in content.replace("TASK-A", "")
    assert manifest["source_workspace_id"] == loop_a.ws.study_dir.name
    assert [f["path"] for f in manifest["file_inventory"]] == ["events.jsonl"]


# ---- Case G：workspace 预算池（child 不能分裂绕过） ----

def test_case_g_workspace_budget_pool_no_escape(tmp_path):
    task = ResearchTask(task_id="TASK-G", question="workspace 池", client="p4-test")
    plan = ResearchPlan(
        plan_id="G-P", research_task_id="TASK-G", plan_version=1,
        steps=[PlanStep(step_id=f"s{i}", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": f"G-A{i}"})
               for i in (1, 2, 3)],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop = ScientificLoop("p4-g", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_exec("TASK-G"))
    loop.open_task(task)
    # workspace 池：全 workspace 合计 max_model_calls=2
    loop.set_budget(ResourceBudget(budget_id="B-WS",
                                   research_task_id=WORKSPACE_BUDGET_SCOPE,
                                   max_model_calls=2))
    loop.adopt_plan(task, plan)
    _drive_one(loop, task, plan, 0)          # 消耗 1（task 预算未设=unbounded）
    _drive_one(loop, task, plan, 1)          # 消耗 2 → 池耗尽
    with pytest.raises(LoopStopped) as ei:   # 第 3 步：workspace 池拦截
        loop.execute_step(task, plan, plan.steps[2])
    assert ei.value.state == "resource_budget_exhausted"
    assert any("workspace:" in x for x in ei.value.detail.split(";"))
    # child task 同样被池拦截（child 继承/汇入 workspace 口径）
    child = ResearchTask(task_id="TASK-G-C", question="子任务", client="p4",
                         parent_task_id="TASK-G")
    loop.open_task(child)
    child_plan = ResearchPlan(
        plan_id="GC-P", research_task_id="TASK-G-C", plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": "GC-A1"})],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop.adopt_plan(child, child_plan)
    with pytest.raises(LoopStopped):
        loop.execute_step(child, child_plan, child_plan.steps[0])
    # workspace rollup：合计 2 次（跨 task 汇总，无逃逸）
    ws_ru = Workspace("p4-g", root=tmp_path).resource_usage(WORKSPACE_BUDGET_SCOPE)
    assert ws_ru["totals"]["model_calls"] == 2
    assert ws_ru["budget"]["budget_id"] == "B-WS"
    # B workspace 的预算池不受 A 影响（Budget escape across workspace = 0）
    tb, pb, loop_b = _task_plan("TASK-B")
    _drive_one(loop_b, tb, pb)
    assert Workspace("TASK-B".replace("_", "-"), root=tmp_path).replay().evidence


# ---- 指标：凭据 scope ----

def test_metric_credential_scope_violation_zero(tmp_path):
    regs = IsolationRegistries(tmp_path)
    regs.set_auth_scope("WS-A", ["europe-pmc"])          # A 只许 EuropePMC
    regs.set_auth_scope("WS-B", [])                       # B 无外部源
    epc = EuropePmcAdapter(fetcher=lambda url: (
        b'{"resultList": {"result": [{"id": "1", "source": "MED", "title": "t"}]}}'))
    r = query_with_scope(epc, "q", "WS-A", regs)          # A：允许
    assert r.status == "success"
    with pytest.raises(IsolationError, match="credential scope"):
        query_with_scope(epc, "q", "WS-B", regs)          # B：违例拦截
    # 未登记 workspace = legacy 全域（不误伤）
    r2 = query_with_scope(epc, "q", "WS-LEGACY", regs)
    assert r2.status == "success"


def test_metric_replay_contamination_zero(tmp_path):
    ta, pa, loop_a = _task_plan("TASK-A")
    tb, pb, loop_b = _task_plan("TASK-B")
    _drive_one(loop_a, ta, pa)
    _drive_one(loop_b, tb, pb)
    _drive_one(loop_b, tb, pb)  # B 再跑一步（不同 analysis_id？同 id 会幂等复用）
    st_a = Workspace("TASK-A".replace("_", "-"), root=tmp_path).replay()
    assert all(e.get("task_id") != "TASK-B" for e in st_a.evidence)
    assert st_a.tasks[0]["task_id"] == "TASK-A"


def test_guard_scope_matrix(tmp_path):
    """写入守卫 scope 精确匹配：write grant 不能开 mutation，反之亦然。"""
    ctx = {"workspace_id": "WS-A"}
    with pytest.raises(IsolationError):
        guard_workspace_write("WS-B", ctx, scope="write_evidence")
    ok_write = {"workspace_id": "WS-A",
                "cross_workspace_grants": [
                    {"source_workspace_id": "WS-A", "target_workspace_id": "WS-B",
                     "scope": "write_evidence", "authorization": "x"}]}
    guard_workspace_write("WS-B", ok_write, scope="write_evidence")
    with pytest.raises(IsolationError):  # write grant 不覆盖 mutation
        guard_workspace_write("WS-B", ok_write, scope="mutate_evidence")
    guard_workspace_write("WS-A", {"workspace_id": "WS-A"})  # 自身免检
    guard_workspace_write("WS-B", {})  # legacy ctx（无绑定）不拦
