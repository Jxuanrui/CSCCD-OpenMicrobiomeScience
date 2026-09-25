"""P3 Backup / Restore Automation——Scientific State Snapshot 的等价恢复验证。

核心问题不是"能不能复制文件"，而是：**恢复后的系统是否仍然是同一个科研
系统**（Task lineage / Evidence provenance / Ledger sequence / Replay result
全部一致）。

Cases（用户裁决规格）：
  A — 正常恢复：backup → delete workspace → restore → lineage/head/replay 一致
  B — 损坏 backup：篡改 ledger / manifest / checksum → 拒绝恢复，目标零改动
  C — schema mismatch：旧版兼容（1.1→1.2 加性）→ compatible；未知版 →
      migration_required，禁止静默升级
  D — 恢复后继续科研：新 task/新事件 seq 顺延，历史零污染
  E — backup 后发生新事件：恢复显式回到备份时间点（head=100 语义），
      不混入未来事件
  增量链：full@N → 新事件 → incremental(N→M) → 恢复链 head=M；段损坏拒绝

验收指标（全锁死）：
  Restore lineage mismatch = 0；Restore replay mismatch = 0；
  Corrupted backup accepted = 0；Secret leakage in backup = 0；
  Sequence collision after restore = 0；Historical evidence mutation = 0。
"""
from __future__ import annotations

import json

import pytest

from mra.backup import (BackupError, LEDGER_SCHEMA_VERSION,
                        check_schema_compatibility, create_backup,
                        restore_backup)
from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.workspace import (CandidateResult, LoopEvent, ResourceUsage,  # noqa: F401
                           Workspace)
from mra.workspace import Evidence, PlanStep, ResearchPlan, ResearchTask
from mra.workspace import ResourceBudget

RULE = "method-zero-variance-guard-001"


def _populate(ws: Workspace, task_id="T-P3", n_evidence=1) -> list[int]:
    """写入覆盖全部记录类型的科研状态，返回事件 seq 列表。"""
    reg = build_default_registry()
    ws.append(ResearchTask(task_id=task_id, question="备份恢复验证", client="p3-test"))
    plan = ResearchPlan(
        plan_id="P3-P", research_task_id=task_id, plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": "P3-A1"})],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    ws.append(plan)
    for i in range(1, n_evidence + 1):
        aid = "P3-A1" if n_evidence == 1 else f"P3-A{i}"
        cand = CandidateResult(
            analysis_id=aid, capability_id="diversity.alpha_shannon",
            implementation_id="mra.numpy", capability_version="1.0.0",
            implementation_version="1.0.0", input_fingerprint=f"sha256:{aid}",
            output_summary="n=10", research_task_id=task_id,
            graph_snapshot_id="2026-09-24-v2", metrics={"n": 10},
            provenance={"task": task_id})
        ws.append(cand)
        d = evaluate_candidate(cand, candidate_event_seq=ws.events()[-1]["seq"],
                               method_rules_applied=[RULE],
                               execution_governance={"verdicts": ["v"]})
        ws.append(d.model_copy(update={"research_task_id": task_id}))
        reg.invoke("workspace.record_evidence",
                   {"study_id": ws.study_dir.name, "decision_id": d.decision_id,
                    "record": {"evidence_id": f"EV-{task_id}-{i}", "task_id": task_id,
                               "claim": f"备份前证据 {i}", "candidate_id": aid,
                               "graph_snapshot_id": "2026-09-24-v2"}},
                   context={"workspace_root": ws.study_dir.parent})
    ws.append(ResourceBudget(budget_id="B-P3", research_task_id=task_id,
                             max_model_calls=10))
    ws.append(ResourceUsage(usage_id=f"RU-{task_id}-1", research_task_id=task_id,
                            model_calls=1, input_tokens=100))
    ws.append(LoopEvent(research_task_id=task_id, kind="terminal",
                        verdict="task_completed"))
    return [e["seq"] for e in ws.events()]


def _semantic_state(ws: Workspace, task_id="T-P3") -> dict:
    """恢复等价性的判定口径：事件投影 + replay 摘要 + lineage。"""
    projection = [{"seq": e["seq"], "record_type": e["record_type"],
                   "record": e["record"]} for e in ws.events()]
    st = ws.replay()
    return {"projection": projection, "n_events": st.n_events,
            "evidence": [(e["evidence_id"], e["claim"]) for e in st.evidence],
            "lineage": ws.lineage(task_id)}


# ---- Case A：正常恢复 ----

def test_case_a_backup_delete_restore_equivalent(tmp_path):
    ws = Workspace("p3-a", root=tmp_path)
    seqs = _populate(ws)
    state_before = _semantic_state(ws)
    manifest = create_backup(ws, tmp_path / "backups")
    assert manifest["ledger_head_seq"] == seqs[-1]
    assert manifest["schema_version"] == LEDGER_SCHEMA_VERSION
    assert manifest["graph_snapshot_id"] == "2026-09-24-v2"    # provenance 随行
    assert manifest["policy_version"]                           # 治理版本随行
    assert manifest["capability_registry_version"].startswith("sha256:")
    import shutil
    shutil.rmtree(ws.study_dir)                                 # 灾难：工作区删除
    out = restore_backup(tmp_path / "backups", manifest["backup_id"], tmp_path)
    assert out["head_seq"] == seqs[-1] and out["schema_compatibility"] == "compatible"
    ws2 = Workspace("p3-a", root=tmp_path)
    state_after = _semantic_state(ws2)
    # Restore lineage/replay mismatch = 0：逐事件（含 provenance）完全一致
    assert state_after["projection"] == state_before["projection"]
    assert state_after["evidence"] == state_before["evidence"]
    assert state_after["lineage"]["candidates"] == state_before["lineage"]["candidates"]
    assert state_after["lineage"]["evidence"] == state_before["lineage"]["evidence"]


# ---- Case B：损坏 backup 拒绝恢复 ----

def test_case_b_corrupted_backup_rejected_target_untouched(tmp_path):
    ws = Workspace("p3-b", root=tmp_path)
    _populate(ws)
    manifest = create_backup(ws, tmp_path / "backups")
    state_before = _semantic_state(ws)
    bdir = tmp_path / "backups" / manifest["backup_id"]
    originals = {name: (bdir / name).read_text(encoding="utf-8")
                 for name in ("events.jsonl", "manifest.json")}
    for tamper in ("ledger", "manifest", "checksum"):
        # 每轮从原始备份内容开始：先验证可恢复，再篡改，再验证拒绝
        restore_backup(tmp_path / "backups", manifest["backup_id"],
                       tmp_path, study_id="p3-restore")
        if tamper == "ledger":
            p = bdir / "events.jsonl"
            lines = p.read_text(encoding="utf-8").splitlines()
            rec = json.loads(lines[-1])
            rec["record"]["verdict"] = "TAMPERED"
            lines[-1] = json.dumps(rec, ensure_ascii=False)
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif tamper == "manifest":
            p = bdir / "manifest.json"
            m = json.loads(p.read_text(encoding="utf-8"))
            m["ledger_hash"] = "sha256:deadbeef"
            p.write_text(json.dumps(m), encoding="utf-8")
        else:
            p = bdir / "manifest.json"
            m = json.loads(p.read_text(encoding="utf-8"))
            m["file_inventory"][0]["sha256"] = "sha256:deadbeef"
            p.write_text(json.dumps(m), encoding="utf-8")
        with pytest.raises(BackupError):
            restore_backup(tmp_path / "backups", manifest["backup_id"],
                           tmp_path, study_id="p3-restore")
        # 目标工作区（真实研究状态）零改动
        assert _semantic_state(Workspace("p3-b", root=tmp_path)) == state_before
        # 还原备份内容，供下一轮
        for name, content in originals.items():
            (bdir / name).write_text(content, encoding="utf-8")


# ---- Case C：schema 兼容判定 ----

def test_case_c_schema_compatibility_verdicts(tmp_path):
    ws = Workspace("p3-c", root=tmp_path)
    _populate(ws)
    manifest = create_backup(ws, tmp_path / "backups")
    # 同版 → compatible
    assert check_schema_compatibility(LEDGER_SCHEMA_VERSION) == "compatible"
    # 已知旧版（1.1→1.2 加性演进）→ compatible
    assert check_schema_compatibility("1.1") == "compatible"
    # 未知/未来版 → migration_required，恢复拒绝（禁止静默升级）
    assert check_schema_compatibility("0.9") == "migration_required"
    mpath = tmp_path / "backups" / manifest["backup_id"] / "manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["schema_version"] = "0.9"
    mpath.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(BackupError, match="migration_required"):
        restore_backup(tmp_path / "backups", manifest["backup_id"], tmp_path,
                       study_id="p3-c-restored")


# ---- Case D：恢复后继续科研 ----

def test_case_d_continue_research_after_restore(tmp_path):
    ws = Workspace("p3-d", root=tmp_path)
    seqs = _populate(ws)
    head = seqs[-1]
    prefix_before = [e["seq"] for e in ws.events()]
    prefix_records_before = [e["record"] for e in ws.events()]
    manifest = create_backup(ws, tmp_path / "backups")
    import shutil
    shutil.rmtree(ws.study_dir)
    restore_backup(tmp_path / "backups", manifest["backup_id"], tmp_path)
    # 新任务 + 新事件：seq 顺延（Sequence collision after restore = 0）
    ws2 = Workspace("p3-d", root=tmp_path)
    ws2.append(ResearchTask(task_id="T-P3-NEW", question="恢复后新任务",
                            client="p3-test"))
    new_seq = ws2.events()[-1]["seq"]
    assert new_seq == head + 1
    # Historical evidence mutation after restore = 0：历史前缀逐事件不变
    events_now = ws2.events()
    assert [e["seq"] for e in events_now][:len(prefix_before)] == prefix_before
    assert [e["record"] for e in events_now][:len(prefix_before)] == prefix_records_before
    lin = ws2.lineage("T-P3")
    assert lin["task"] and len(lin["evidence"]) >= 1               # 历史 lineage 完好
    assert ws2.lineage("T-P3-NEW")["task"]["task_id"] == "T-P3-NEW"  # 新任务不串线


# ---- Case E：时间点恢复语义 ----

def test_case_e_point_in_time_no_future_events(tmp_path):
    ws = Workspace("p3-e", root=tmp_path)
    seqs = _populate(ws)
    backup_head = seqs[-1]
    manifest = create_backup(ws, tmp_path / "backups")
    # 备份后继续发生事件（head → head+k）
    for i in range(3):
        ws.append(ResearchTask(task_id=f"T-P3-POST{i}", question="备份后事件",
                               client="p3-test"))
    assert ws.events()[-1]["seq"] == backup_head + 3
    # 恢复到备份时间点：不混入未来事件
    out = restore_backup(tmp_path / "backups", manifest["backup_id"], tmp_path)
    assert out["head_seq"] == backup_head
    ws2 = Workspace("p3-e", root=tmp_path)
    assert ws2.events()[-1]["seq"] == backup_head
    assert not any(e["record"].get("task_id", "").startswith("T-P3-POST")
                   for e in ws2.events() if e["record_type"] == "ResearchTask")
    # 恢复后新事件从 head+1 继续编号
    ws2.append(ResearchTask(task_id="T-P3-AFTER", question="恢复后", client="t"))
    assert ws2.events()[-1]["seq"] == backup_head + 1


# ---- 增量备份链 ----

def test_incremental_chain_restore(tmp_path):
    ws = Workspace("p3-inc", root=tmp_path)
    _populate(ws)
    base = create_backup(ws, tmp_path / "backups", backup_id="base-1")
    base_head = base["ledger_head_seq"]
    for i in range(2):  # 备份后新事件
        ws.append(ResearchTask(task_id=f"T-INC-{i}", question="增量段", client="t"))
    inc = create_backup(ws, tmp_path / "backups", backup_id="inc-1",
                        kind="incremental", base_backup_id="base-1",
                        incremental_from_seq=base_head)
    assert inc["ledger_head_seq"] == base_head + 2
    import shutil
    shutil.rmtree(ws.study_dir)
    out = restore_backup(tmp_path / "backups", "inc-1", tmp_path)   # 增量恢复
    assert out["head_seq"] == base_head + 2
    ws2 = Workspace("p3-inc", root=tmp_path)
    assert any(e["record"].get("task_id") == "T-INC-1"
               for e in ws2.events() if e["record_type"] == "ResearchTask")
    # 段损坏 → 拒绝（checksum）
    seg = tmp_path / "backups" / "inc-1" / "events.jsonl"
    lines = seg.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    rec["record"]["question"] = "TAMPERED"
    lines[-1] = json.dumps(rec, ensure_ascii=False)
    seg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(BackupError):
        restore_backup(tmp_path / "backups", "inc-1", tmp_path,
                       study_id="p3-inc-check")


# ---- 指标：备份内容边界与凭据纪律 ----

def test_metric_backup_content_boundary_and_secret_leak_zero(tmp_path):
    ws = Workspace("p3-sec", root=tmp_path)
    _populate(ws)
    # 工作区目录里的临时缓存/无关文件不得进入备份
    (ws.study_dir / "cache.tmp").write_text("runtime junk", encoding="utf-8")
    manifest = create_backup(ws, tmp_path / "backups")
    bdir = tmp_path / "backups" / manifest["backup_id"]
    assert sorted(p.name for p in bdir.iterdir()) == ["events.jsonl", "manifest.json"]
    assert [f["path"] for f in manifest["file_inventory"]] == ["events.jsonl"]
    # 账本含凭据 → 创建备份即拒绝（Secret leakage in backup = 0）
    ws_bad = Workspace("p3-sec2", root=tmp_path)
    ws_bad.append(ResearchTask(task_id="T-SEC", question="x", client="t"))
    with ws_bad.events_path.open("a", encoding="utf-8") as fh:  # 绕过 append 守卫
        fh.write(json.dumps({"seq": 2, "record_type": "Evidence",
                             "record": {"evidence_id": "EV-SEC", "task_id": "T-SEC",
                                        "claim": "x", "api_key": "sk-LEAK"},
                             "appended_at": "2026-09-25T00:00:00"}) + "\n")
    with pytest.raises(BackupError, match="凭据"):
        create_backup(ws_bad, tmp_path / "backups2")


def test_metric_corrupted_backup_accepted_zero_and_fail_closed(tmp_path):
    """B 已覆盖三类篡改；本用例补 staging 校验失败时既有目标零污染。"""
    ws = Workspace("p3-fc", root=tmp_path)
    _populate(ws)
    state = _semantic_state(ws)
    manifest = create_backup(ws, tmp_path / "backups")
    # 篡改 manifest 的 replay_summary（checksum 不变路径之外的语义字段）
    mpath = tmp_path / "backups" / manifest["backup_id"] / "manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["replay_summary"]["evidence"] = 999            # 语义字段被篡改
    mpath.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(BackupError):
        restore_backup(tmp_path / "backups", manifest["backup_id"], tmp_path,
                       study_id="p3-fc")             # 恢复到既有工作区位置
    assert _semantic_state(Workspace("p3-fc", root=tmp_path)) == state  # 零污染
    # 目录中不留 staging 残骸
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".restore-")]
