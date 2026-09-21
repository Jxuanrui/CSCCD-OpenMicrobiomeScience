from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mra.knowledge import KnowledgeStore
from mra.pep import AuditLedger, Decision, Pep
from mra.web import create_app


class FakeOpaClient:
    policy_revision = "web-test-revision"

    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def evaluate(self, **document: Any) -> Decision:
        principal_type = document["principal_type"]
        action = document["action"]
        allow = principal_type == "human"
        approval_required = principal_type == "agent" and action == "promote_result"
        if allow:
            reason = "ALLOW_HUMAN"
        elif approval_required:
            reason = "APPROVAL_REQUIRED_PROMOTE_RESULT"
        else:
            reason = "DENY_DEFAULT"
        return Decision(
            decision_id=f"web-decision-{next(self._ids)}",
            allow=allow,
            approval_required=approval_required,
            reason_codes=(reason,),
            constraints={},
            policy_revision=self.policy_revision,
        )


@pytest.fixture
def web_pep(tmp_path: Path) -> Pep:
    ledger = AuditLedger(tmp_path / "audit.db")
    instance = Pep(
        opa_client=FakeOpaClient(),
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
    )
    yield instance
    ledger.close()


@pytest.fixture
def client(web_pep: Pep) -> TestClient:
    with TestClient(create_app(web_pep)) as test_client:
        yield test_client


def add_pending(pep: Pep, request_id: str = "request-pending") -> None:
    pep.request_approval(request_id, "promote_result", "a" * 64)


def test_dashboard_shows_pending_count(client: TestClient, web_pep: Pep) -> None:
    add_pending(web_pep)

    response = client.get("/")

    assert response.status_code == 200
    assert "待审批" in response.text
    assert "<strong>1</strong>" in response.text


def test_approvals_lists_pending_request(client: TestClient, web_pep: Pep) -> None:
    add_pending(web_pep)

    response = client.get("/approvals")

    assert response.status_code == 200
    assert "request-pending" in response.text
    assert "promote_result" in response.text
    assert "hx-post" in response.text


def test_approve_updates_ledger_and_fragment(client: TestClient, web_pep: Pep) -> None:
    add_pending(web_pep)

    response = client.post(
        "/approvals/request-pending/approve",
        data={"label": "statistical-review"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert web_pep.ledger.get_approval_request("request-pending")["status"] == "approved"
    assert "暂无待审批请求" in response.text
    assert "statistical-review" in response.text


def test_approve_rejects_blank_label(client: TestClient, web_pep: Pep) -> None:
    add_pending(web_pep)

    response = client.post(
        "/approvals/request-pending/approve",
        data={"label": "   "},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "Approval label is required" in response.text
    assert web_pep.ledger.get_approval_request("request-pending")["status"] == "pending"


def test_audit_filters_by_action_and_decision(client: TestClient, web_pep: Pep) -> None:
    add_pending(web_pep, "visible-request")
    web_pep.evaluate(
        "agent",
        "manage_policy",
        {"type": "policy"},
        {"request_id": "hidden-request"},
    )

    response = client.get(
        "/audit",
        params={"action": "promote_result", "decision": "approval_required"},
    )

    assert response.status_code == 200
    assert "visible-request" in response.text
    assert "hidden-request" not in response.text


def test_permissions_renders_policy_scope(client: TestClient) -> None:
    response = client.get("/permissions")

    assert response.status_code == 200
    assert "granted_scope" in response.text
    assert "project01" in response.text
    assert "simulate_association" in response.text


def test_missing_run_returns_404(client: TestClient) -> None:
    response = client.get("/runs/does-not-exist")

    assert response.status_code == 404
    assert "Page not found" in response.text


def test_vendored_htmx_is_served(client: TestClient) -> None:
    response = client.get("/static/vendor/htmx.min.js")

    assert response.status_code == 200
    assert "htmx" in response.text


def test_knowledge_page_shows_disabled_state(client: TestClient) -> None:
    response = client.get("/knowledge")

    assert response.status_code == 200
    assert "知识库未启用" in response.text


def test_knowledge_pages_list_and_search_entries(web_pep: Pep, tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    try:
        entries = (
            ("first.yaml", "knowledge-web-001", "governance", "Web knowledge title"),
            ("second.yaml", "knowledge-web-002", "methods", "Another method note"),
        )
        for name, entry_id, package, title in entries:
            (tmp_path / name).write_text(
                "\n".join(
                    (
                        f"id: {entry_id}",
                        f"package: {package}",
                        f"title: {title}",
                        "version: 1",
                        "evidence_level: confirmed_rule",
                        "applicability: Web test scope",
                        "source:",
                        "  - kind: internal",
                        "    ref: web-test-source",
                        "    date: '2026-09-12'",
                        "approval:",
                        "  status: approved",
                        "  by: human",
                        "  revision: test-v1",
                        "content: Searchable web test content.",
                    )
                ),
                encoding="utf-8",
            )
            store.ingest(tmp_path / name)

        with TestClient(create_app(web_pep, knowledge=store)) as test_client:
            overview = test_client.get("/knowledge")
            search = test_client.get("/knowledge/search", params={"q": "Web knowledge"})

        assert overview.status_code == 200
        assert "knowledge-web-001" in overview.text
        assert "Web knowledge title" in overview.text
        assert "governance" in overview.text
        assert search.status_code == 200
        assert "Web knowledge title" in search.text
    finally:
        store.close()
