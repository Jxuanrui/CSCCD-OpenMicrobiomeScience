from __future__ import annotations

from mra.pep import AuditEvent, AuditLedger


def event(event_id: str, *, action: str = "execute_task") -> AuditEvent:
    return AuditEvent(
        id=event_id,
        ts=f"2026-09-11T00:00:0{event_id[-1]}Z",
        principal_type="agent",
        principal_id="fixed-agent",
        action=action,
        resource_kind="task",
        resource_id="simulate_association",
        request_id="request-1",
        subject_hash=None,
        decision="allow",
        approval_required=False,
        reason_codes=("ALLOW_TEST",),
        constraints={"timeout_s": 10},
        policy_revision="test-revision",
        input_digest="a" * 64,
    )


def test_ledger_uses_wal_and_queries_normalized_rows(ledger: AuditLedger) -> None:
    ledger.record_event(event("event-1"))
    ledger.record_event(event("event-2", action="read_projection"))
    ledger.set_output_digest("event-1", "b" * 64)

    rows = ledger.query_audit(action="execute_task", approval_required=False)

    assert len(rows) == 1
    assert rows[0]["reason_codes"] == ["ALLOW_TEST"]
    assert rows[0]["constraints"] == {"timeout_s": 10}
    assert rows[0]["output_digest"] == "b" * 64
    mode = ledger._connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_approval_receipt_is_only_available_after_approval(
    ledger: AuditLedger,
) -> None:
    ledger.create_approval_request(
        request_id="request-1",
        action="promote_result",
        subject_hash="c" * 64,
        policy_revision="test-revision",
    )
    assert ledger.get_receipt("request-1") is None
    approval_event = AuditEvent(
        **{
            **event("event-3", action="approve_request").__dict__,
            "principal_type": "human",
            "principal_id": "fixed-human",
            "resource_kind": "request",
            "resource_id": "request-1",
            "subject_hash": "c" * 64,
        }
    )
    ledger.approve_request(
        request_id="request-1",
        approver_id="fixed-human",
        label="stat_review",
        approved_at="2026-09-11T00:00:03Z",
        policy_revision="test-revision",
        event=approval_event,
    )

    receipt = ledger.get_receipt("request-1")
    assert receipt is not None
    assert receipt.as_opa_input()["approver"] == {
        "type": "human",
        "label": "stat_review",
    }
    assert ledger.query_audit(action="approve_request")[0]["principal_type"] == "human"
