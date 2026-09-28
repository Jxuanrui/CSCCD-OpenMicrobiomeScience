from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from mra.pep import AuditLedger, Decision, Pep


class FakeOpaClient:
    """Small policy double matching the checked-in MVP bundle."""

    policy_revision = "test-revision"

    def __init__(self) -> None:
        self.inputs: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def evaluate(self, **document: Any) -> Decision:
        self.inputs.append(document)
        principal_type = document["principal_type"]
        action = document["action"]
        context = document["context"]
        receipt = document["receipt"]
        allow = False
        approval_required = False
        reason = "DENY_DEFAULT"
        constraints: dict[str, Any] = {}

        if principal_type == "human":
            allow = True
            reason = "ALLOW_HUMAN"
        elif action == "execute_task":
            execution = context.get("execution", {})
            allow = (
                context.get("task_id") in {"simulate_association", "quality_check"}
                and execution.get("network_mode") == "deny"
                and execution.get("credential_refs") == []
            )
            reason = "ALLOW_AGENT_EXECUTE_TASK" if allow else "DENY_DEFAULT"
            if allow:
                constraints = {"max_memory_mb": 512, "timeout_s": 300}
        elif action == "promote_result":
            valid_receipt = (
                isinstance(receipt, dict)
                and receipt.get("request_id") == context.get("request_id")
                and receipt.get("subject_hash") == context.get("subject_hash")
                and receipt.get("status") == "approved"
                and receipt.get("policy_revision") == self.policy_revision
                and receipt.get("approver", {}).get("type") == "human"
            )
            allow = valid_receipt
            approval_required = not valid_receipt
            reason = (
                "ALLOW_AGENT_PROMOTE_APPROVED"
                if allow
                else "APPROVAL_REQUIRED_PROMOTE_RESULT"
            )
        elif action in {"export_data", "manage_policy", "approve_request"}:
            reason = "DENY_AGENT_RESTRICTED_ACTION"

        return Decision(
            decision_id=f"decision-{next(self._ids)}",
            allow=allow,
            approval_required=approval_required,
            reason_codes=(reason,),
            constraints=constraints,
            policy_revision=self.policy_revision,
        )


@pytest.fixture
def fake_opa() -> FakeOpaClient:
    return FakeOpaClient()


@pytest.fixture
def ledger(tmp_path: Path) -> AuditLedger:
    instance = AuditLedger(tmp_path / "audit" / "audit.db")
    yield instance
    instance.close()


@pytest.fixture
def pep(tmp_path: Path, fake_opa: FakeOpaClient, ledger: AuditLedger) -> Pep:
    return Pep(
        opa_client=fake_opa,
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
        task_registry={"simulate_association": ("simulate",)},
    )
