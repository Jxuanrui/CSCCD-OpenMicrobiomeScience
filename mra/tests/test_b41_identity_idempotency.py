"""B4.1 Identity & Idempotency Hardening——恢复重试不得被误认为新的科研事件。

原则：same scientific command + retry = same scientific state；
只有新 GovernanceDecision / 新科学内容 / 新 rationale / 显式 revision intent
才允许新增 append-only 事件。

幂等 Case（用户指定四条）：
  1. record_evidence 成功后重复提交同一 decision → Evidence 数量不增加
  2. commit 成功但 ACK 丢失，restart 后重试 → Evidence 数量不增加
  3. 同 CandidateResult + 新 GovernanceDecision → 合法新治理事件
  4. 同 Evidence + 新 downgrade rationale → 合法 revision

身份域 Case：
  5. task scope 内重复 analysis_id → Workspace.append 结构性硬拒（系统保证，
     非调用方约定）
  6. 跨 task 同名 analysis_id → 合法并存，scoped 裁决精确解析、无域裁决
     拒绝歧义解析
  7. legacy 无域条目向后兼容
  8. 同义 mutation 重复请求（同状态+同理由）→ already_applied 不新增事件
"""
from __future__ import annotations

import pytest

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.workspace import (CandidateResult, GovernanceDecision, Workspace,
                           digest)

RULE = "method-zero-variance-guard-001"


def _cand(analysis_id, task_id=""):
    return CandidateResult(
        analysis_id=analysis_id, capability_id="diversity.alpha_shannon",
        implementation_id="mra.numpy", capability_version="1.0.0",
        implementation_version="1.0.0",
        input_fingerprint=f"sha256:{analysis_id}", output_summary="n=10",
        research_task_id=task_id,
        metrics={"n_samples": 10}, provenance={"task": task_id or "t"})


def _seed(root, study, analysis_id="B41-A1", task_id="T-B41",
          scoped=True) -> tuple[Workspace, dict, dict]:
    """推进到 候选+裁决 已入账，返回 (ws, candidate记录, decision记录)。"""
    reg = build_default_registry()
    ws = Workspace(study, root=root)
    cand = _cand(analysis_id, task_id if scoped else "")
    ws.append(cand)
    d = evaluate_candidate(cand, candidate_event_seq=ws.events()[-1]["seq"],
                           method_rules_applied=[RULE],
                           execution_governance={"verdicts": ["v"]},
                           client="b41-test")
    if scoped:
        d = d.model_copy(update={"research_task_id": task_id})
    ws.append(d)
    return ws, cand.model_dump(), d.model_dump()


def _evidence_payload(decision, analysis_id="B41-A1", task_id="T-B41",
                      claim="B4.1 幂等验证", study="b41"):
    return {"study_id": study, "decision_id": decision["decision_id"],
            "record": {"evidence_id": f"EV-{task_id}", "task_id": task_id,
                       "claim": claim, "candidate_id": analysis_id,
                       "effect": {"rho": 0.4}}}


def _evidence_events(ws):
    return [e for e in ws.events() if e["record_type"] == "Evidence"]


# ---- 幂等 Case 1：同 decision 重复提交 ----

def test_idem1_retry_same_decision_no_new_evidence(tmp_path):
    reg = build_default_registry()
    ws, _, decision = _seed(tmp_path, "b41")
    payload = _evidence_payload(decision)
    out1 = reg.invoke("workspace.record_evidence", payload,
                      context={"workspace_root": tmp_path})
    assert out1["committed"] and not out1["already_committed"]
    out2 = reg.invoke("workspace.record_evidence", payload,
                      context={"workspace_root": tmp_path})
    assert not out2["committed"] and out2["already_committed"]  # retry ≠ 新事件
    committed_seq = _evidence_events(Workspace("b41", root=tmp_path))[0]["seq"]
    assert out2["event_seq"] == committed_seq  # 返回的是既有事件引用
    assert len(_evidence_events(Workspace("b41", root=tmp_path))) == 1
    st = Workspace("b41", root=tmp_path).replay()
    assert len(st.evidence) == 1


# ---- 幂等 Case 2：ACK 丢失 + restart 后重试 ----

def test_idem2_ack_loss_restart_retry_no_new_evidence(tmp_path):
    reg = build_default_registry()
    ws, _, decision = _seed(tmp_path, "b41")
    payload = _evidence_payload(decision)
    reg.invoke("workspace.record_evidence", payload,
               context={"workspace_root": tmp_path})  # commit 成功…
    del reg, ws, decision, payload                     # …ACK 丢失 + 进程死亡
    # restart：全新对象只读恢复账本，caller 不确定上次是否成功 → 重试同一命令
    events_before = _evidence_events(Workspace("b41", root=tmp_path))
    decision_r = next(e["record"] for e in Workspace("b41", root=tmp_path).events()
                      if e["record_type"] == "GovernanceDecision")
    retry = {"study_id": "b41", "decision_id": decision_r["decision_id"],
             "record": {"evidence_id": "EV-T-B41", "task_id": "T-B41",
                        "claim": "B4.1 幂等验证", "candidate_id": "B41-A1",
                        "effect": {"rho": 0.4}}}
    out = build_default_registry().invoke(
        "workspace.record_evidence", retry, context={"workspace_root": tmp_path})
    assert out["already_committed"] and not out["committed"]
    events_after = _evidence_events(Workspace("b41", root=tmp_path))
    assert len(events_after) == len(events_before)  # Evidence 数量不增加


# ---- 幂等 Case 3：同候选 + 新 GovernanceDecision → 合法新治理事件 ----

def test_idem3_same_candidate_new_decision_legal(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws, cand_record, d1 = _seed(tmp_path, "b41")
    reg.invoke("workspace.record_evidence", _evidence_payload(d1), context=ctx)
    # 再裁决：新 decision（引用新规则集），supersedes d1
    from mra.workspace import CandidateResult as CR
    cand = CR(**cand_record)
    seq = next(e["seq"] for e in ws.events() if e["record_type"] == "CandidateResult")
    d2 = evaluate_candidate(cand, candidate_event_seq=seq,
                            method_rules_applied=["method-multiple-testing-001"],
                            execution_governance={"verdicts": ["v"]},
                            decision_id="GD-B41-RE",
                            supersedes_decision_id=d1["decision_id"])
    ws.append(d2.model_copy(update={"research_task_id": "T-B41"}))
    # 同内容 record + 新 decision_id → 不是幂等命中，允许新 Evidence 事件
    payload = _evidence_payload(d2.model_dump())
    out = reg.invoke("workspace.record_evidence", payload, context=ctx)
    assert out["committed"] and not out["already_committed"]
    st = Workspace("b41", root=tmp_path).replay()
    assert len(st.evidence) == 1  # 同 evidence_id replay 去重，最新版生效
    assert st.evidence[0]["governance"]["decision"]["decision_id"] == "GD-B41-RE"
    decisions = [e["record"] for e in ws.events()
                 if e["record_type"] == "GovernanceDecision"]
    assert len(decisions) == 2  # 两个治理事件并存（d1 被 d2 取代但历史保留）
    # 旧 decision 已失效：再用 d1 提交 → 拦截
    with pytest.raises(ValueError, match="已失效"):
        reg.invoke("workspace.record_evidence", _evidence_payload(d1), context=ctx)


# ---- 幂等 Case 4：同 Evidence + 新 downgrade rationale → 合法 revision ----

def test_idem4_same_evidence_new_rationale_legal(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws, _, decision = _seed(tmp_path, "b41")
    reg.invoke("workspace.record_evidence", _evidence_payload(decision), context=ctx)
    # 第一次 downgrade（新 rationale）→ 合法
    out1 = reg.invoke("workspace.mark_downgraded",
                      {"study_id": "b41", "evidence_id": "EV-T-B41",
                       "reason": "敏感性分析不稳健", "actor": "T-B41"}, context=ctx)
    assert out1["committed"] and not out1["already_applied"]
    # 同 rationale 重复请求（恢复重试）→ 不是新事件
    out2 = reg.invoke("workspace.mark_downgraded",
                      {"study_id": "b41", "evidence_id": "EV-T-B41",
                       "reason": "敏感性分析不稳健", "actor": "T-B41"}, context=ctx)
    assert not out2["committed"] and out2["already_applied"]
    # 新 rationale（新证伪证据出现）→ 合法新 mutation
    out3 = reg.invoke("workspace.mark_refuted",
                      {"study_id": "b41", "evidence_id": "EV-T-B41",
                       "reason": "特异性对照复测推翻", "actor": "T-B41"}, context=ctx)
    assert out3["committed"]
    st = Workspace("b41", root=tmp_path).replay()
    assert st.evidence[0]["falsification"] == "refuted"
    assert len(_evidence_events(Workspace("b41", root=tmp_path))) == 3  # 原始+降级+推翻


# ---- 身份域 Case 5：task scope 内重复 analysis_id 结构性硬拒 ----

def test_scope5_duplicate_scoped_analysis_id_rejected(tmp_path):
    ws = Workspace("b41-s5", root=tmp_path)
    ws.append(_cand("SHARED-A", "TA"))
    with pytest.raises(ValueError, match="isolation invariant"):
        ws.append(_cand("SHARED-A", "TA"))  # 同 task 同名 → 系统硬拒（非调用方约定）


# ---- 身份域 Case 6：跨 task 同名合法 + scoped 精确解析 + 无域拒绝歧义 ----

def test_scope6_cross_task_same_id_scoped_resolution(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws = Workspace("b41-s6", root=tmp_path)
    c_a = _cand("SHARED-B", "TA")   # 不同内容，同名 analysis_id，不同 task
    c_b = CandidateResult(**{**_cand("SHARED-B", "TB").model_dump(),
                             "output_summary": "n=20",
                             "input_fingerprint": "sha256:TB-B"})
    ws.append(c_a)
    ws.append(c_b)  # 跨 task 同名 → 合法（隔离不变式只约束 scope 内）
    from mra.governance import evaluate_candidate as ev
    d_a = ev(c_a, candidate_event_seq=1, method_rules_applied=[RULE],
             execution_governance={"verdicts": ["v"]})
    d_a = d_a.model_copy(update={"research_task_id": "TA"})
    ws.append(d_a)
    # scoped 裁决 → 精确解析 TA 的候选，提交成功
    out = reg.invoke("workspace.record_evidence",
                     {"study_id": "b41-s6", "decision_id": d_a.decision_id,
                      "record": {"evidence_id": "EV-TA", "task_id": "TA",
                                 "claim": "TA 侧", "candidate_id": "SHARED-B"}},
                     context=ctx)
    assert out["committed"]
    # 无域裁决 + 跨 task 同名候选 → 拒绝歧义解析
    d_legacy = GovernanceDecision(
        decision_id="D-LEGACY", analysis_id="SHARED-B", candidate_event_seq=1,
        candidate_hash=digest(c_a.model_dump()), allow_evidence=True)
    ws.append(d_legacy)
    with pytest.raises(ValueError, match="歧义解析"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b41-s6", "decision_id": "D-LEGACY",
                    "record": {"evidence_id": "EV-LEG", "task_id": "TA",
                               "claim": "无域裁决", "candidate_id": "SHARED-B"}},
                   context=ctx)


# ---- 身份域 Case 7：legacy 无域条目向后兼容 ----

def test_scope7_legacy_unscoped_still_works(tmp_path):
    reg = build_default_registry()
    ws, cand_record, decision = _seed(tmp_path, "b41-s7", scoped=False)
    assert cand_record["research_task_id"] == ""
    out = reg.invoke("workspace.record_evidence",
                     _evidence_payload(decision, task_id="T-L", study="b41-s7"),
                     context={"workspace_root": tmp_path})
    assert out["committed"]
    # legacy 候选可重复 append（无域不参与唯一性强制，历史行为不变）
    ws2 = Workspace("b41-s7b", root=tmp_path)
    ws2.append(_cand("LEG", ""))
    ws2.append(_cand("LEG", ""))  # 不抛
    assert Workspace("b41-s7b", root=tmp_path).replay().candidate_results == 2


# ---- 身份域 Case 8：loop 层重驱动去重（execute/evaluate 零新事件） ----

def test_scope8_loop_reexecute_reuses_candidate(tmp_path):
    from mra.research.scientific_loop import ScientificLoop
    from mra.workspace import PlanStep, ResearchPlan, ResearchTask

    def _exec(capability_id, inputs, context=None):
        return {"candidate": _cand(inputs["analysis_id"], "T-B41").model_dump(),
                "execution_verdicts": [{"rule": "audit", "verdict": "PASS"}]}

    loop = ScientificLoop("b41-s8", registry=build_default_registry(),
                          workspace_root=tmp_path, executor=_exec)
    task = ResearchTask(task_id="T-B41", question="loop 幂等重驱动",
                        client="b41-test")
    plan = ResearchPlan(
        plan_id="B41-P", research_task_id="T-B41", plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": "B41-L1"})],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop.open_task(task)
    loop.adopt_plan(task, plan)
    out1 = loop.execute_step(task, plan, plan.steps[0])
    assert not out1.get("reused") and out1["candidate"].deterministic
    assert out1["candidate"].research_task_id == "T-B41"  # loop 产出 task 打标
    n_events = len(loop.ws.events())
    # 崩溃重启后重驱动同一步：复用候选、复用计划，零新事件
    loop2 = ScientificLoop("b41-s8", registry=build_default_registry(),
                           workspace_root=tmp_path, executor=_exec)
    plan2 = ResearchPlan(**{**plan.model_dump()})  # 同 (plan_id, version)
    v = loop2.adopt_plan(task, plan2)
    assert v.get("reused")
    out2 = loop2.execute_step(task, plan2, plan2.steps[0])
    assert out2.get("reused") and \
        out2["candidate"].analysis_id == out1["candidate"].analysis_id
    assert len(loop2.ws.events()) == n_events  # 零新增（retry ≠ 新科研事件）
    # 裁决重驱动同样复用
    r1 = loop.evaluate_and_commit(task, out1["candidate"], rules=[RULE])
    n2 = len(loop2.ws.events())
    r2 = loop2.evaluate_and_commit(task, out2["candidate"], rules=[RULE])
    assert r2.get("reused") and r2["decision"].decision_id == r1["decision"].decision_id
    assert len(loop2.ws.events()) == n2
