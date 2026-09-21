from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scipy.stats import pearsonr

from mra.audit import Verdict
from mra.pep import AuditLedger, OpaClient, Pep, PepError
from mra.workflow import create_governed_pep, run_governed_analysis


ROOT = Path(__file__).resolve().parents[2]
OPA = ROOT / "tools" / "opa"
BUNDLE = ROOT / "policies" / "opa" / "bundle"


def _require_opa() -> None:
    if not OPA.is_file() or not OPA.stat().st_mode & 0o111:
        pytest.skip("tools/opa is not installed or executable")


@pytest.fixture
def pep(tmp_path: Path) -> Pep:
    _require_opa()
    ledger = AuditLedger(tmp_path / "audit" / "audit.db")
    instance = create_governed_pep(
        opa_client=OpaClient(OPA, BUNDLE),
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
    )
    yield instance
    ledger.close()


def _run_analysis(pep: Pep, seed: int = 20260911):
    return run_governed_analysis(
        pep=pep,
        seed=seed,
        task_id="simulate_association",
        constraints={"network_mode": "deny", "credential_refs": []},
        timeout_s=60,
    )


def test_mock_analysis_cli_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for output in (first, second):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mra.demo.mock_analysis",
                "--seed",
                "17",
                "--out",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    assert first.read_bytes() == second.read_bytes()
    document = json.loads(first.read_text(encoding="utf-8"))
    assert set(document) == {
        "method",
        "alpha",
        "pvalues",
        "coefficients",
        "taxa",
        "sample_ids",
        "exposure",
        "batch",
        "abundance",
        "data_scale",
        "zero_policy",
        "family_id",
        "claimed_adjusted",
        "seed",
    }
    expected = pearsonr(
        document["exposure"],
        [row[0] for row in document["abundance"]],
    )
    assert document["coefficients"][0] == pytest.approx(expected.statistic)
    assert document["pvalues"][0] == pytest.approx(expected.pvalue)


def test_a_unauthorized_actions_are_denied_and_audited(pep: Pep) -> None:
    decision = pep.evaluate(
        "agent",
        "export_data",
        {"type": "data", "project_id": "project01", "classification": "synthetic"},
        {},
    )
    assert decision.allow is False
    assert decision.approval_required is False

    with pytest.raises(PepError, match="not registered"):
        pep.execute_task(
            "unregistered_evil",
            ("-c", "raise SystemExit('must not run')"),
            {"network_mode": "deny", "credential_refs": []},
            10,
        )

    deny_rows = pep.query_audit(decision="deny")
    assert any(row["action"] == "export_data" for row in deny_rows)
    assert any(row["resource_id"] == "unregistered_evil" for row in deny_rows)


def test_b_promotion_is_bound_to_approved_artifact_hash(pep: Pep) -> None:
    result = _run_analysis(pep, seed=23)
    assert result.request_id == result.staged_ref.request_id
    assert all(finding.verdict is not Verdict.FAIL for finding in result.findings)

    wrong_hash = "0" * 64
    with pytest.raises(PepError, match="denied by policy"):
        pep.promote_result(
            result.request_id,
            wrong_hash,
            result.staged_ref.path,
        )

    pep.approve(result.request_id, "stat_review")
    original = result.staged_ref.path.read_bytes()
    result.staged_ref.path.write_bytes(original + b"\n")
    with pytest.raises(PepError, match="hash does not match"):
        pep.promote_result(
            result.request_id,
            result.staged_ref.digest,
            result.staged_ref.path,
        )
    assert any(
        "DENY_SUBJECT_HASH_MISMATCH" in row["reason_codes"]
        for row in pep.query_audit(request_id=result.request_id)
    )

    result.staged_ref.path.write_bytes(original)
    promoted = pep.promote_result(
        result.request_id,
        result.staged_ref.digest,
        result.staged_ref.path,
    )
    assert promoted.path.parent == pep.results_root / result.request_id
    assert promoted.path.read_bytes() == original
    assert promoted.digest == hashlib.sha256(original).hexdigest()


def _systemd_scope_available() -> bool:
    if shutil.which("systemd-run") is None:
        return False
    try:
        from mra.pep.systemd_runner import systemd_scope_runner

        probe = systemd_scope_runner("true", (), {}, 10)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def test_c_systemd_scope_enforces_policy_memory_limit(tmp_path: Path) -> None:
    _require_opa()
    if not _systemd_scope_available():
        pytest.skip("systemd-run --user --scope is unavailable on this host")

    from mra.pep.systemd_runner import systemd_scope_runner

    bundle = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle)
    data_path = bundle / "data.json"
    policy_data = json.loads(data_path.read_text(encoding="utf-8"))
    policy_data["mra"]["registered_tasks"]["simulate_association"]["max_memory_mb"] = 64
    data_path.write_text(json.dumps(policy_data), encoding="utf-8")

    ledger = AuditLedger(tmp_path / "audit.db")
    constrained_pep = Pep(
        opa_client=OpaClient(OPA, bundle),
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
        run_command=systemd_scope_runner,
        task_registry={"simulate_association": (sys.executable,)},
    )
    try:
        try:
            artifact = constrained_pep.execute_task(
                "simulate_association",
                ("-c", "x = bytearray(2 * 1024 ** 3)"),
                {"network_mode": "deny", "credential_refs": []},
                30,
            )
        except PepError as error:
            assert str(error) == "Task execution failed."
            artifact = None

        rows = constrained_pep.query_audit(action="execute_task")
        assert rows[-1]["constraints"]["max_memory_mb"] == 64
        if artifact is not None:
            summary = json.loads(artifact.path.read_text(encoding="utf-8"))
            assert summary["exit_code"] != 0
            assert rows[-1]["output_digest"] == artifact.digest
    finally:
        ledger.close()


def test_d_audit_ledger_replays_denial_and_successful_closure(pep: Pep) -> None:
    denied = pep.evaluate(
        "agent",
        "export_data",
        {"type": "data", "project_id": "project01", "classification": "synthetic"},
        {},
    )
    assert denied.allow is False

    result = _run_analysis(pep, seed=29)
    pep.approve(result.request_id, "stat_review")
    promoted = pep.promote_result(
        result.request_id,
        result.staged_ref.digest,
        result.staged_ref.path,
    )

    rows = pep.query_audit()
    assert rows and all(row["policy_revision"] == "draft-1" for row in rows)
    assert any(row["decision"] == "deny" for row in rows)
    assert any(row["action"] == "approve_request" for row in rows)

    execute = next(
        row
        for row in rows
        if row["action"] == "execute_task"
        and row["request_id"] == result.request_id
        and row["decision"] == "allow"
    )
    assert len(execute["input_digest"]) == 64
    assert len(execute["output_digest"]) == 64

    promotion = next(
        row
        for row in rows
        if row["action"] == "promote_result"
        and row["request_id"] == result.request_id
        and row["decision"] == "allow"
    )
    assert len(promotion["input_digest"]) == 64
    assert promotion["output_digest"] == promoted.digest == result.staged_ref.digest
