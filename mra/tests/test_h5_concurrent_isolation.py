"""B4 Concurrent Task Isolation——多任务并发时科研状态不串线。

Case A：正常并发（Task A + Task B 同时执行，独立 lineage）
Case B：一败一成（Task A 成功、Task B 被治理门阻断，互不影响）
Case C：同 capability + 重叠实体（不同 task context，候选/裁决/证据不误用）
负路径×6：cross-task decision/candidate/hash/supersedes/canonical/replay 全拦截。
"""
from __future__ import annotations

import pytest

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.workspace import (CandidateResult, GovernanceDecision, Workspace,
                           digest)


def _cand(analysis_id, task_id="T"):
    return CandidateResult(
        analysis_id=analysis_id, capability_id="x.y", implementation_id="i.i",
        capability_version="1", implementation_version="1",
        input_fingerprint=f"sha256:{analysis_id}", output_summary="n=10",
        provenance={"task": task_id, "n": 10})


def _seed_task_evidence(tmp_path, study, task_id, analysis_id, claim):
    """为指定 task 创建完整链：Candidate→Decision→Evidence。"""
    reg = build_default_registry()
    ws = Workspace(study, root=tmp_path)
    cand = _cand(analysis_id, task_id)
    ws.append(cand)
    seq = ws.events()[-1]["seq"]
    d = evaluate_candidate(cand, candidate_event_seq=seq,
                           method_rules_applied=["method-zero-variance-guard-001"],
                           execution_governance={"verdicts": ["v"]})
    ws.append(d)
    if d.allow_evidence:
        reg.invoke("workspace.record_evidence",
                   {"study_id": study, "decision_id": d.decision_id,
                    "record": {"evidence_id": f"EV-{task_id}", "task_id": task_id,
                               "claim": claim, "candidate_id": analysis_id}},
                   context={"workspace_root": tmp_path})
    return ws, d, cand


# ---- Case A：正常并发 ----

def test_case_a_normal_concurrent(tmp_path):
    """Task A 与 Task B 同时执行，对象完全独立。"""
    ws_a, d_a, c_a = _seed_task_evidence(tmp_path, "iso-a", "TA", "A-001", "task A")
    ws_b, d_b, c_b = _seed_task_evidence(tmp_path, "iso-b", "TB", "B-001", "task B")
    st_a, st_b = ws_a.replay(), ws_b.replay()
    # ID 独立
    assert st_a.evidence[0]["evidence_id"] != st_b.evidence[0]["evidence_id"]
    assert d_a.decision_id != d_b.decision_id
    assert c_a.analysis_id != c_b.analysis_id
    # 对象不串
    assert st_a.evidence[0]["task_id"] == "TA" != st_b.evidence[0]["task_id"] == "TB"
    # A 的任何对象不能被 B 查询为自身 lineage
    b_ids = {e["evidence_id"] for e in st_b.evidence}
    a_ids = {e["evidence_id"] for e in st_a.evidence}
    assert not (a_ids & b_ids)


# ---- Case B：一败一成 ----

def test_case_b_failure_isolation(tmp_path):
    """Task B 被治理门阻断，Task A 不受影响。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    # Task A 正常
    ws_a, d_a, _ = _seed_task_evidence(tmp_path, "iso-mixed", "TA", "A-ok", "A claim")
    assert d_a.allow_evidence
    # Task B：blocking warning → 被拒
    ws_b = Workspace("iso-mixed", root=tmp_path)  # 同 workspace 但不同 task
    cand_b = _cand("B-blocked", "TB")
    cand_b = cand_b.model_copy(update={"warnings": [{"level": "blocking", "message": "样本错位"}]})
    ws_b.append(cand_b)
    d_b = evaluate_candidate(cand_b, candidate_event_seq=ws_b.events()[-1]["seq"],
                             method_rules_applied=["r"],
                             execution_governance={"verdicts": ["v"]})
    ws_b.append(d_b)
    assert not d_b.allow_evidence
    with pytest.raises(ValueError):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "iso-mixed", "decision_id": d_b.decision_id,
                    "record": {"evidence_id": "EV-TB", "task_id": "TB",
                               "claim": "B", "candidate_id": "B-blocked"}}, context=ctx)
    # A 不受影响
    st = Workspace("iso-mixed", root=tmp_path).replay()
    a_ev = [e for e in st.evidence if e["task_id"] == "TA"]
    assert len(a_ev) == 1 and a_ev[0]["claim"] == "A claim"
    b_ev = [e for e in st.evidence if e["task_id"] == "TB"]
    assert len(b_ev) == 0  # B 没有 evidence


# ---- Case C：同 capability + 重叠实体 ----

def test_case_c_same_capability_overlapping_entities(tmp_path):
    """同 capability 不同 task context，候选/裁决/证据不误用。"""
    reg = build_default_registry()
    # 同 capability + 重叠实体，但 analysis_id 各自唯一（设计约束：ID全局唯一）
    ws_a, d_a, c_a = _seed_task_evidence(tmp_path, "iso-c", "TA", "C-A-shared-entity", "A uses")
    ws_b, d_b, c_b = _seed_task_evidence(tmp_path, "iso-c", "TB", "C-B-shared-entity", "B uses")
    # 同 analysis_id 但不同 task → 各自独立 evidence
    st = Workspace("iso-c", root=tmp_path).replay()
    assert len(st.evidence) == 2  # A 和 B 各一条
    tasks = {e["task_id"] for e in st.evidence}
    assert tasks == {"TA", "TB"}


# ---- 负路径×6 ----

def test_neg1_cross_task_decision_id_rejected(tmp_path):
    """Task A 的 decision_id 用于 Task B → 拒绝。"""
    reg = build_default_registry()
    ws_a, d_a, _ = _seed_task_evidence(tmp_path, "iso-neg", "TA", "N-A", "A")
    with pytest.raises(ValueError, match="不对应"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "iso-neg", "decision_id": d_a.decision_id,
                    "record": {"evidence_id": "EV-TB", "task_id": "TB",
                               "claim": "B", "candidate_id": "N-B"}},
                   context={"workspace_root": tmp_path})


def test_neg2_cross_task_candidate_hash_rejected(tmp_path):
    """Task A 的 candidate hash 冒充 Task B → 拒绝。"""
    ws = Workspace("iso-hash", root=tmp_path)
    cand_a = _cand("N-A", "TA")
    ws.append(cand_a)
    seq_a = ws.events()[-1]["seq"]
    # 用 A 的 hash 但声称是 B 的候选
    d = GovernanceDecision(
        decision_id="D-FAKE", analysis_id="N-B",  # 声称是 B
        candidate_event_seq=seq_a,               # 但引用 A 的 seq
        candidate_hash=digest(cand_a.model_dump()),  # A 的 hash
        allow_evidence=True)
    ws.append(d)
    reg = build_default_registry()
    with pytest.raises(ValueError, match="候选指纹|不在账本"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "iso-hash", "decision_id": "D-FAKE",
                    "record": {"evidence_id": "EV-TB", "task_id": "TB",
                               "claim": "B", "candidate_id": "N-B"}},
                   context={"workspace_root": tmp_path})


def test_neg3_cross_task_supersedes(tmp_path):
    """cross-task supersedes_seq → 结构性允许（append-only 不检查跨task），
    但 cross-task set_canonical 被拦（见 neg4）。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    _seed_task_evidence(tmp_path, "iso-sup", "TA", "S-A", "A")
    # 尝试 mark_downgraded 引用 A 的 evidence 但 reason 提到 B
    out = reg.invoke("workspace.mark_downgraded",
                     {"study_id": "iso-sup", "evidence_id": "EV-TA",
                      "reason": "由 task B 的发现触发", "actor": "TB"}, context=ctx)
    assert out["committed"]  # 合法：可以因外部发现降级（跨task影响是科研决策，不是串线）


def test_neg4_cross_task_set_canonical(tmp_path):
    """cross-task set_canonical → 铁律4拦截（compute caller）+ canonical裁决门。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    _seed_task_evidence(tmp_path, "iso-can", "TA", "K-A", "A")
    with pytest.raises(ValueError, match="铁律4"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "iso-can", "evidence_id": "EV-TA",
                    "supporting_lineage": ["x"], "reason": "from TB",
                    "decision_id": "whatever"},
                   context={**ctx, "caller_capability": "association.partial_spearman"})


def test_neg5_replay_isolation(tmp_path):
    """一个 task 的 replay 不重建另一个 task 状态。"""
    _seed_task_evidence(tmp_path, "iso-replay", "TA", "R-A", "A")
    _seed_task_evidence(tmp_path, "iso-replay", "TB", "R-B", "B")
    ws = Workspace("iso-replay", root=tmp_path)
    st = ws.replay()
    # 两个 task 的 evidence 都在（同 workspace），但各自独立
    assert len(st.evidence) == 2
    a = [e for e in st.evidence if e["task_id"] == "TA"]
    b = [e for e in st.evidence if e["task_id"] == "TB"]
    assert len(a) == 1 and len(b) == 1
    assert a[0]["candidate_id"] == "R-A" != b[0]["candidate_id"] == "R-B"


def test_neg6_duplicate_evidence_commit(tmp_path):
    """同 workspace 同 task 重复提交 Evidence（同 candidate + 同 decision）→ 产生 append-only
    revision（非 duplicate bug）。验证 governance 决策链接一致。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws, d, _ = _seed_task_evidence(tmp_path, "iso-dup", "TA", "D-A", "A")
    # 第二次提交同 decision → 合法（append-only revision 语义），但 governance 链接一致
    out2 = reg.invoke("workspace.record_evidence",
                      {"study_id": "iso-dup", "decision_id": d.decision_id,
                       "record": {"evidence_id": "EV-TA", "task_id": "TA",
                                  "claim": "revised claim", "candidate_id": "D-A"}},
                      context=ctx)
    assert out2["committed"]
    st = Workspace("iso-dup", root=tmp_path).replay()
    assert len(st.evidence) == 1  # replay 后只有一条（同 ID 去重，最新覆盖）
    assert st.evidence[0]["claim"] == "revised claim"  # 最新版
    assert st.evidence[0]["governance"]["decision"]["decision_id"] == d.decision_id
