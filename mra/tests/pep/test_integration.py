from __future__ import annotations

from pathlib import Path

import pytest

from mra.pep import AuditLedger, OpaClient, Pep


ROOT = Path(__file__).resolve().parents[2]
OPA = ROOT / "tools" / "opa"
BUNDLE = ROOT / "policies" / "opa" / "bundle"


@pytest.mark.skipif(not OPA.exists(), reason="tools/opa is not installed")
def test_real_bundle_requires_approval_for_agent_promotion(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.db")
    pep = Pep(
        opa_client=OpaClient(OPA, BUNDLE),
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
    )
    try:
        decision = pep.evaluate(
            "agent",
            "promote_result",
            {"type": "result", "id": "request-real-opa"},
            {
                "request_id": "request-real-opa",
                "subject_hash": "a" * 64,
            },
        )
    finally:
        ledger.close()

    assert decision.allow is False
    assert decision.approval_required is True
    assert decision.reason_codes == ("APPROVAL_REQUIRED_PROMOTE_RESULT",)
