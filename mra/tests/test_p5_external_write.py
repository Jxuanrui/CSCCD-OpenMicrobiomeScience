"""P5 EXTERNAL_WRITE Recovery Framework——外部副作用治理边界的对抗验证。

核心验收标准：系统知道**什么时候可以写、写失败后发生什么、什么时候绝不能
自动重试**。P5 是准入框架，不是开放写权限。

Cases（用户裁决规格）：
  A — Idempotent write：成功后 retry，外部状态不重复创建
  B — ACK 丢失：外部成功本地无返回 → restart → 同幂等键恢复，零重复副作用
  C — Queryable unknown：先 verify 再决定；verify 未知 → 不盲重试
  D — Irreversible unknown：进入 recovery_requires_review，自动重试被禁
  E — Permission boundary：A 可写 X、B 不可 → B 拒绝
  F — Backup/Replay：外部写事件记录 intent/authorization/result/verification；
      replay 只重建状态，零重新执行

验收指标（全锁死）：
  Unauthorized external write = 0；Blind retry on unknown state = 0；
  Duplicate external side effect = 0；Missing recovery policy = 0；
  Missing approval lineage = 0；Replay-triggered external execution = 0。
"""
from __future__ import annotations

import pytest

from mra.backup import create_backup, restore_backup
from mra.external_write import (ExternalWriteContract, ExternalWriteError,
                                ExternalWriteGovernor)
from mra.governance import evaluate_candidate
from mra.isolation import IsolationError, IsolationRegistries
from mra.workspace import CandidateResult, Workspace

RULE = "method-zero-variance-guard-001"


class FakeExternal:
    """模拟外部系统：按 idempotency_key 去重；可注入 ACK 丢失。"""

    def __init__(self, fail_first_submit=False):
        self.store: dict[str, dict] = {}
        self.created = 0          # 真实副作用计数（重复副作用指标的口径）
        self.calls = 0            # 传输调用计数（盲重试指标的口径）
        self.fail_first = fail_first_submit
        self.verified_state = "unknown"

    def transport(self, payload, idem_key):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            self.store[idem_key] = dict(payload)   # 副作用已发生…
            self.created += 1
            raise TimeoutError("connection dropped after write")  # …ACK 丢失
        if idem_key not in self.store:
            self.store[idem_key] = dict(payload)
            self.created += 1
            return {"status": "created"}
        return {"status": "already_exists"}

    def verifier(self, payload, idem_key):
        if idem_key in self.store:
            return self.verified_state
        return "failed"


def _decision(ws: Workspace) -> str:
    cand = CandidateResult(
        analysis_id="P5-C1", capability_id="demo.write",
        implementation_id="test", capability_version="1", implementation_version="1",
        input_fingerprint="sha256:p5", output_summary="n=1",
        provenance={"task": "P5"})
    ws.append(cand)
    d = evaluate_candidate(cand, candidate_event_seq=ws.events()[-1]["seq"],
                           method_rules_applied=[RULE],
                           execution_governance={"verdicts": ["v"]})
    ws.append(d)
    return d.decision_id


def _gov(tmp_path, study, ext, registries=None, verifiers=None):
    ws = Workspace(study, root=tmp_path)
    return ExternalWriteGovernor(
        ws, transports={"sys-x": ext.transport}, verifiers=verifiers or {},
        registries=registries), ws


_IDEM = ExternalWriteContract(capability_id="demo.submit", target_system="sys-x",
                              write_type="idempotent", idempotency_support=True)
_QUERY = ExternalWriteContract(capability_id="demo.push", target_system="sys-x",
                               write_type="queryable", verification_support=True)
_IRREV = ExternalWriteContract(capability_id="demo.publish", target_system="sys-x",
                               write_type="irreversible")


# ---- Case A：幂等写 + retry ----

def test_case_a_idempotent_retry_no_duplicate(tmp_path):
    ext = FakeExternal()
    gov, ws = _gov(tmp_path, "p5-a", ext)
    wid = gov.plan(_IDEM, {"job": "analyze"})
    gov.authorize(wid, _decision(ws), approval="PI 批准")
    out = gov.submit(wid, {"job": "analyze"})
    assert out["state"] == "acknowledged"
    # 构造 unknown → Type A 恢复：同幂等键重试
    prev = gov._latest(wid)
    gov._transition(prev, "unknown", error="simulated ack loss")
    retry = gov.recover(wid)
    assert retry["state"] == "acknowledged"
    assert ext.created == 1                      # 外部状态不重复创建
    assert ext.calls == 2                        # 传输发生了，但被 key 去重
    assert ext.store[prev["idempotency_key"]]["job"] == "analyze"
    gov.verify(wid)
    assert gov._latest(wid)["state"] == "completed"


# ---- Case B：ACK 丢失 + restart ----

def test_case_b_ack_loss_restart_recovery(tmp_path):
    ext = FakeExternal(fail_first_submit=True)
    gov1, ws1 = _gov(tmp_path, "p5-b", ext)
    wid = gov1.plan(_IDEM, {"job": "upload"})
    gov1.authorize(wid, _decision(ws1), approval="PI")
    out = gov1.submit(wid, {"job": "upload"})
    assert out["state"] == "unknown"             # 外部成功但本地未收到返回
    assert ext.created == 1
    del gov1, ws1                                 # restart（进程死亡）
    # 新 governor 从账本恢复：幂等键稳定（账本派生）
    gov2, ws2 = _gov(tmp_path, "p5-b", ext)
    prev = gov2._latest(wid)
    assert prev["state"] == "unknown"
    recovered = gov2.recover(wid)                 # Type A：同 key 重试
    assert recovered["state"] == "acknowledged"
    assert ext.created == 1                       # 零重复副作用
    assert ext.calls == 2                         # 第二次被外部去重
    gov2.verify(wid)                              # 核销完成
    assert gov2._latest(wid)["state"] == "completed"


# ---- Case C：queryable unknown——先查再决定 ----

def test_case_c_queryable_verify_before_retry(tmp_path):
    ext = FakeExternal()
    ext.verified_state = "unknown"                # 先查不到确定状态
    gov, ws = _gov(tmp_path, "p5-c", ext,
                   verifiers={"sys-x": ext.verifier})
    wid = gov.plan(_QUERY, {"push": "data"})
    gov.authorize(wid, _decision(ws))
    out = gov.submit(wid, {"push": "data"})
    assert out["state"] == "acknowledged"
    # 构造 unknown：显式把状态打到 unknown（模拟提交后状态未知）
    prev = gov._latest(wid)
    unknown = gov._transition(prev, "unknown", error="simulated")
    assert unknown["state"] == "unknown"
    calls_before = ext.calls
    r1 = gov.recover(wid)                         # verify 返回 unknown → 不盲重试
    assert r1["state"] == "unknown" and "禁止盲重试" in r1["verification"]
    assert ext.calls == calls_before              # Blind retry = 0
    # verify 明确 failed → 允许重试
    ext.verified_state = "failed"
    r2 = gov.recover(wid)
    assert r2["state"] in ("acknowledged", "unknown")
    assert ext.calls == calls_before + 1          # 重试发生（经 verify 裁定）
    # verify 明确 completed → 直接核销，不再传输
    prev = gov._latest(wid)
    if prev["state"] == "unknown":
        ext.verified_state = "completed"
        r3 = gov.recover(wid)
        assert r3["state"] == "acknowledged"


# ---- Case D：irreversible unknown ----

def test_case_d_irreversible_requires_review(tmp_path):
    ext = FakeExternal()
    gov, ws = _gov(tmp_path, "p5-d", ext)
    wid = gov.plan(_IRREV, {"publish": "paper-v1"})
    gov.authorize(wid, _decision(ws))
    assert gov.submit(wid, {"publish": "paper-v1"})["state"] == "acknowledged"
    prev = gov._latest(wid)
    unknown = gov._transition(prev, "unknown", error="state unknown")
    assert unknown["state"] == "unknown"
    calls_before = ext.calls
    r = gov.recover(wid)
    assert r["state"] == "recovery_requires_review"   # 不自动重试
    assert ext.calls == calls_before                   # 零传输
    with pytest.raises(ExternalWriteError, match="recover 仅作用于"):
        gov.recover(wid)                              # 吸收态：自动路径全封死
    # 人工处置：缺署名拒绝 → 显式改判（署名 reviewer）
    with pytest.raises(ExternalWriteError, match="署名"):
        gov.resolve_review(wid, "completed", reviewer="")
    resolved = gov.resolve_review(wid, "failed", reviewer="PI-张",
                                  note="人工确认为失败，放弃发布")
    assert resolved["state"] == "failed"
    assert "review:PI-张" in resolved["approval"]


# ---- Case E：权限边界 ----

def test_case_e_permission_boundary(tmp_path):
    regs = IsolationRegistries(tmp_path)
    regs.set_external_write_scope("p5-e-a", ["sys-x"])   # A 允许写 X
    # B 未登记 → 默认拒绝
    ext = FakeExternal()
    gov_a, ws_a = _gov(tmp_path, "p5-e-a", ext, registries=regs)
    wid_a = gov_a.plan(_IDEM, {"job": "a"})
    gov_a.authorize(wid_a, _decision(ws_a))              # A：双闸通过
    assert gov_a.submit(wid_a, {"job": "a"})["state"] == "acknowledged"

    gov_b, ws_b = _gov(tmp_path, "p5-e-b", ext, registries=regs)
    wid_b = gov_b.plan(_IDEM, {"job": "b"})
    with pytest.raises(IsolationError, match="外部写域拒绝"):
        gov_b.authorize(wid_b, _decision(ws_b))          # B：拒绝
    with pytest.raises(ExternalWriteError, match="未授权"):
        gov_b.submit(wid_b, {"job": "b"})                # 未授权不可执行


# ---- Case F：Backup / Replay 零重执行 ----

def test_case_f_backup_replay_no_reexecution(tmp_path):
    ext = FakeExternal()
    gov, ws = _gov(tmp_path, "p5-f", ext)
    wid = gov.plan(_IDEM, {"job": "archive"}, research_task_id="TASK-F")
    gov.authorize(wid, _decision(ws), approval="PI 批准")
    gov.submit(wid, {"job": "archive"})
    gov.verify(wid)
    final = gov._latest(wid)
    assert final["state"] == "completed"
    # 记录完整性：intent/authorization/result/verification 全随行
    assert final["intent"] == {"job": "archive"}
    assert final["decision_id"] and final["result_digest"]
    assert final["verification"] in ("verified-completed", "idempotent-resend")
    manifest = create_backup(ws, tmp_path / "backups")
    calls_before = ext.calls
    # restore 到全新 root + 新 governor：只重建状态，零重新执行
    restore_backup(tmp_path / "backups", manifest["backup_id"],
                   tmp_path / "restored")
    ws2 = Workspace("p5-f", root=tmp_path / "restored")
    gov2 = ExternalWriteGovernor(ws2, transports={"sys-x": ext.transport})
    writes = gov2.writes()
    assert len(writes) == 1 and writes[0]["state"] == "completed"
    assert writes[0]["write_id"] == wid
    assert writes[0]["intent"] == {"job": "archive"}
    assert ext.calls == calls_before                # Replay-triggered execution = 0
    assert ws2.replay().external_write_records >= 5  # 生命周期全程可审计


# ---- 指标：契约与治理 ----

def test_metric_missing_recovery_policy_zero():
    """类型-能力不一致 = 缺少可靠恢复策略 → 拒绝准入。"""
    with pytest.raises(ExternalWriteError, match="idempotency_support"):
        ExternalWriteContract(capability_id="x.y", target_system="s",
                              write_type="idempotent").validate_coherence()
    with pytest.raises(ExternalWriteError, match="verification_support"):
        ExternalWriteContract(capability_id="x.y", target_system="s",
                              write_type="queryable").validate_coherence()
    with pytest.raises(ExternalWriteError, match="irreversible"):
        ExternalWriteContract(capability_id="x.y", target_system="s",
                              write_type="irreversible",
                              idempotency_support=True).validate_coherence()
    with pytest.raises(Exception):
        ExternalWriteContract(capability_id="x.y", target_system="s",
                              write_type="mystical")     # 类型集封闭


def test_metric_unauthorized_and_missing_approval_lineage_zero(tmp_path):
    ext = FakeExternal()
    gov, ws = _gov(tmp_path, "p5-m", ext)
    wid = gov.plan(_IDEM, {"job": "m"})
    # 未授权直接 submit → 拒绝（Unauthorized external write = 0）
    with pytest.raises(ExternalWriteError, match="未授权"):
        gov.submit(wid, {"job": "m"})
    # 假 decision_id → 拒绝（Missing approval lineage = 0）
    with pytest.raises(ExternalWriteError, match="裁决"):
        gov.authorize(wid, "GD-FAKE-000")
    # 生命周期非法迁移
    prev = gov._latest(wid)
    with pytest.raises(ExternalWriteError, match="非法生命周期迁移"):
        gov._transition(prev, "completed")
    with pytest.raises(ExternalWriteError, match="同 intent"):
        gov.plan(_IDEM, {"job": "m"})               # 同 intent 重复计划拒绝
