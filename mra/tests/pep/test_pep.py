from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from mra.pep import AuditLedger, CommandResult, OpaError, Pep, PepError

from .conftest import FakeOpaClient


def promote_context(request_id: str, subject_hash: str) -> dict[str, str]:
    return {"request_id": request_id, "subject_hash": subject_hash}


def approve_subject(pep: Pep, request_id: str, subject_hash: str) -> None:
    pep.request_approval(request_id, "promote_result", subject_hash)
    pep.approve(request_id, "stat_review")


def test_evaluate_fixes_principal_and_never_logs_credentials(
    pep: Pep, fake_opa: FakeOpaClient
) -> None:
    secret_ref = "vault://secret-token"
    decision = pep.evaluate(
        "agent",
        "execute_task",
        {"type": "task", "id": "simulate_association"},
        {
            "task_id": "simulate_association",
            "request_id": "request-1",
            "execution": {
                "network_mode": "deny",
                "credential_refs": [secret_ref],
            },
        },
    )

    assert decision.allow is False
    assert fake_opa.inputs[0]["principal_id"] == "mra-agent"
    assert "principal" not in fake_opa.inputs[0]["context"]
    audit = pep.query_audit(request_id="request-1")
    assert audit[0]["principal_id"] == "mra-agent"
    assert secret_ref not in json.dumps(audit)


def test_agent_promote_requires_then_accepts_trusted_receipt(
    pep: Pep, fake_opa: FakeOpaClient
) -> None:
    request_id = "request-approval"
    subject_hash = "a" * 64
    approve_subject(pep, request_id, subject_hash)
    assert fake_opa.inputs[0]["principal_type"] == "agent"
    assert fake_opa.inputs[0]["action"] == "promote_result"

    decision = pep.evaluate(
        "agent",
        "promote_result",
        {"type": "result", "id": request_id},
        promote_context(request_id, subject_hash),
    )

    assert decision.allow is True
    assert fake_opa.inputs[-1]["receipt"]["approver"] == {
        "type": "human",
        "label": "stat_review",
    }
    approval_rows = pep.query_audit(action="approve_request")
    assert len(approval_rows) == 1
    assert approval_rows[0]["principal_type"] == "human"


def test_receipt_is_bound_to_subject_hash(pep: Pep) -> None:
    request_id = "request-tamper"
    approve_subject(pep, request_id, "a" * 64)

    decision = pep.evaluate(
        "agent",
        "promote_result",
        {"type": "result", "id": request_id},
        promote_context(request_id, "b" * 64),
    )

    assert decision.allow is False
    assert decision.approval_required is True


def test_receipt_is_bound_to_action(pep: Pep) -> None:
    request_id = "request-wrong-action"
    subject_hash = "a" * 64
    pep.ledger.create_approval_request(
        request_id=request_id,
        action="export_data",
        subject_hash=subject_hash,
        policy_revision="test-revision",
    )
    pep.approve(request_id, "release")

    decision = pep.evaluate(
        "agent",
        "promote_result",
        {"type": "result", "id": request_id},
        promote_context(request_id, subject_hash),
    )

    assert decision.allow is False
    assert decision.approval_required is True


@pytest.mark.parametrize("action", ["export_data", "manage_policy"])
def test_agent_restricted_actions_are_denied(pep: Pep, action: str) -> None:
    resource_type = "data" if action == "export_data" else "policy"

    decision = pep.evaluate("agent", action, {"type": resource_type}, {})

    assert decision.allow is False
    assert decision.approval_required is False
    assert decision.reason_codes == ("DENY_AGENT_RESTRICTED_ACTION",)


def test_opa_failure_raises_pep_error(tmp_path: Path) -> None:
    class BrokenOpa:
        policy_revision = "test-revision"

        def evaluate(self, **kwargs: object) -> None:
            raise OpaError("OPA authorization failed closed.")

    ledger = AuditLedger(tmp_path / "audit.db")
    pep = Pep(opa_client=BrokenOpa(), ledger=ledger)
    try:
        with pytest.raises(PepError, match="failed closed"):
            pep.evaluate("agent", "manage_policy", {"type": "policy"}, {})
    finally:
        ledger.close()


def test_audit_write_failure_blocks_command(
    pep: Pep, ledger: AuditLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[str] = []
    pep._run_command = lambda command, args, constraints, timeout: commands.append(
        command
    )

    def fail_write(event: object) -> None:
        raise PepError("ledger unavailable")

    monkeypatch.setattr(ledger, "record_event", fail_write)

    with pytest.raises(PepError, match="ledger unavailable"):
        pep.execute_task(
            "simulate_association",
            (),
            {"network_mode": "deny", "credential_refs": []},
            10,
        )
    assert commands == []


def test_execute_task_stages_digest_and_summary(
    tmp_path: Path, fake_opa: FakeOpaClient, ledger: AuditLedger
) -> None:
    seen: list[tuple[object, ...]] = []

    def runner(
        command: str, args: object, constraints: object, timeout: float
    ) -> CommandResult:
        seen.append((command, args, constraints, timeout))
        return CommandResult(stdout="analysis complete", exit_code=0)

    pep = Pep(
        opa_client=fake_opa,
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
        run_command=runner,
        task_registry={"simulate_association": ("simulate",)},
    )
    artifact = pep.execute_task(
        "simulate_association",
        ("--seed", "7"),
        {"network_mode": "deny", "credential_refs": []},
        500,
    )

    assert artifact.path.parent.name == artifact.request_id
    assert artifact.digest == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    assert json.loads(artifact.path.read_text()) == {
        "stdout_summary": "analysis complete",
        "exit_code": 0,
    }
    assert seen[0][3] == 300
    row = pep.query_audit(request_id=artifact.request_id)[0]
    assert row["output_digest"] == artifact.digest


def test_execute_task_rejects_unregistered_task_without_running(
    pep: Pep, fake_opa: FakeOpaClient
) -> None:
    pep._run_command = lambda *args: pytest.fail("unregistered task was executed")
    with pytest.raises(PepError, match="not registered"):
        pep.execute_task(
            "not-registered",
            extra_args=("--anything",),
            constraints={"network_mode": "deny", "credential_refs": []},
            timeout_s=10,
        )
    assert fake_opa.inputs[-1]["principal_type"] == "agent"
    assert fake_opa.inputs[-1]["action"] == "execute_task"


def test_side_effect_entrypoint_signatures_do_not_accept_untrusted_identity() -> None:
    execute_parameters = inspect.signature(Pep.execute_task).parameters
    promote_parameters = inspect.signature(Pep.promote_result).parameters
    assert "command" not in execute_parameters
    assert "principal_type" not in execute_parameters
    assert "principal_type" not in promote_parameters
    assert "receipt" not in promote_parameters


def test_promote_copies_from_hashed_file_descriptor(
    pep: Pep, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "staging" / "fd-request" / "result.json"
    source.parent.mkdir(parents=True)
    original = b"content before replacement"
    source.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    approve_subject(pep, "fd-request", digest)

    import mra.pep.pep as pep_module

    real_copy = pep_module.shutil.copyfileobj

    def replace_source_after_open(input_file: object, output_file: object) -> None:
        source.rename(source.with_suffix(".original"))
        source.write_bytes(b"attacker replacement")
        real_copy(input_file, output_file)

    monkeypatch.setattr(pep_module.shutil, "copyfileobj", replace_source_after_open)
    promoted = pep.promote_result("fd-request", digest, source)
    assert promoted.path.read_bytes() == original


def test_promote_result_rejects_hash_mismatch_and_copies_exact_match(
    pep: Pep, tmp_path: Path
) -> None:
    staging = tmp_path / "staging"
    bad_source = staging / "bad-request" / "result.json"
    bad_source.parent.mkdir(parents=True)
    bad_source.write_bytes(b"actual")
    wrong_digest = hashlib.sha256(b"different").hexdigest()
    approve_subject(pep, "bad-request", wrong_digest)

    with pytest.raises(PepError, match="hash does not match"):
        pep.promote_result("bad-request", wrong_digest, bad_source)
    assert not (tmp_path / "results" / "bad-request").exists()
    assert any(
        row["reason_codes"] == ["DENY_SUBJECT_HASH_MISMATCH"]
        for row in pep.query_audit(request_id="bad-request")
    )

    good_source = staging / "good-request" / "result.json"
    good_source.parent.mkdir(parents=True)
    good_source.write_bytes(b"approved content")
    good_digest = hashlib.sha256(good_source.read_bytes()).hexdigest()
    approve_subject(pep, "good-request", good_digest)
    artifact = pep.promote_result("good-request", good_digest, good_source)

    assert artifact.digest == good_digest
    assert artifact.path.read_bytes() == good_source.read_bytes()
    promotion_rows = [
        row
        for row in pep.query_audit(request_id="good-request")
        if row["action"] == "promote_result" and row["decision"] == "allow"
    ]
    assert promotion_rows[-1]["principal_type"] == "human"
    assert promotion_rows[-1]["output_digest"] == good_digest
