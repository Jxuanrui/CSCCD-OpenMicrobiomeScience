from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mra.model_runtime import Message, ModelRef, ModelResponse, ToolCall, Usage
from mra.pep import AuditLedger, OpaClient
from mra.workflow import create_governed_pep
from mra.workflow.agent import GovernanceAgent


ROOT = Path(__file__).resolve().parents[2]
OPA = ROOT / "tools" / "opa"
BUNDLE = ROOT / "policies" / "opa" / "bundle"


class FakeRuntime:
    def __init__(self, script: list[list[tuple[str, dict]]]):
        self.script = script
        self.calls = 0

    def capabilities(self, model):
        return set()

    def complete(self, request):
        calls = self.script[self.calls] if self.calls < len(self.script) else []
        self.calls += 1
        return ModelResponse(
            request_id=request.request_id,
            content=None,
            tool_calls=tuple(
                ToolCall(str(index), name, json.dumps(args))
                for index, (name, args) in enumerate(calls)
            ),
            structured=None,
            usage=Usage(None, None, None),
            cost_cny=None,
            attempts=(),
            final_model=request.model,
        )


@pytest.fixture
def pep(tmp_path: Path):
    if not OPA.is_file() or not OPA.stat().st_mode & 0o111:
        pytest.skip("tools/opa is not installed or executable")
    ledger = AuditLedger(tmp_path / "audit.db")
    instance = create_governed_pep(
        opa_client=OpaClient(OPA, BUNDLE),
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
    )
    yield instance
    ledger.close()


def _agent(runtime, pep, max_iterations=8):
    return GovernanceAgent(runtime, pep, ModelRef("ark", "doubao-seed-2.0-lite", "unverified", "coding/v3"), max_iterations)


def test_complete_closure(pep):
    runtime = FakeRuntime([
        [("run_registered_task", {"task_id": "simulate_association"})],
        [("read_current_result", {})],
        [("audit_current_results", {})],
        [("propose_promotion", {})],
        [],
    ])
    result = _agent(runtime, pep).run("run, audit, propose")
    assert [row["tool"] for row in result.trajectory] == [
        "run_registered_task", "read_current_result", "audit_current_results", "propose_promotion"
    ]
    assert result.status == "WAITING_APPROVAL"
    assert result.proposal_request_id
    assert pep.ledger.get_approval_request(result.proposal_request_id)["status"] == "pending"


def test_fail_audit_blocks_proposal(pep):
    runtime = FakeRuntime([
        [("run_registered_task", {"task_id": "simulate_association"})],
        [("audit_current_results", {"alpha": 0.05})],
        [("propose_promotion", {})],
        [],
    ])
    agent = _agent(runtime, pep)
    def failing_audit(alpha):
        agent._audit_findings = ["RULE:FAIL:bad"]
        return agent._audit_findings[0]
    agent._audit_current = failing_audit
    result = agent.run("go")
    assert "拒绝提议晋升" in result.trajectory[-1]["outcome"]
    assert result.proposal_request_id is None


def test_unknown_tool_is_rejected(pep):
    result = _agent(FakeRuntime([[('evil_tool', {})], []]), pep).run("go")
    assert "未知工具名" in result.trajectory[0]["outcome"]


def test_immediate_completion(pep):
    result = _agent(FakeRuntime([[]]), pep).run("done")
    assert result.status == "COMPLETED"
    assert result.trajectory == []


def test_max_iterations(pep):
    result = _agent(FakeRuntime([[('read_current_result', {})], [('read_current_result', {})]]), pep, 2).run("loop")
    assert result.status == "MAX_ITERATIONS"


def test_audit_findings_are_recorded(pep):
    runtime = FakeRuntime([
        [("run_registered_task", {"task_id": "simulate_association"})],
        [("audit_current_results", {})],
        [],
    ])
    result = _agent(runtime, pep).run("audit")
    assert result.audit_findings
    assert all(value.count(":") >= 2 for value in result.audit_findings)


@pytest.mark.skipif(not os.environ.get("ARK_API_KEY"), reason="ARK_API_KEY is not configured")
def test_real_ark_navigation(pep, tmp_path, capsys):
    from mra.model_runtime.ark import ArkRuntime

    runtime = ArkRuntime(
        ROOT / "policies" / "model_capabilities.yaml",
        client=None,
    )
    model = ModelRef("ark", "doubao-seed-2.0-lite", "unverified", "coding/v3")
    result = GovernanceAgent(runtime, pep, model).run(
        "运行 simulate_association，读取并审计结果；无 FAIL 则提议晋升。"
    )
    print(json.dumps(result.trajectory, ensure_ascii=False, indent=2))
    assert result.status in {"COMPLETED", "WAITING_APPROVAL", "MAX_ITERATIONS"}
    assert any(
        row["tool"] == "run_registered_task" and row["outcome"] and "request_id" in row["outcome"]
        for row in result.trajectory
    )
