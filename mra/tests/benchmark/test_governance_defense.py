from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mra.benchmark import ATTACK_CORPUS, run_governance_defense_benchmark
from mra.pep import AuditLedger, OpaClient, Pep
from mra.pep.systemd_runner import systemd_scope_runner


ROOT = Path(__file__).resolve().parents[2]
OPA = ROOT / "tools" / "opa"
BUNDLE = ROOT / "policies" / "opa" / "bundle"


def _require_opa() -> None:
    if not OPA.is_file() or not OPA.stat().st_mode & 0o111:
        pytest.skip("tools/opa is not installed or executable")


def _systemd_scope_available() -> bool:
    if shutil.which("systemd-run") is None:
        return False
    try:
        probe = systemd_scope_runner("true", (), {}, 10.0)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


requires_systemd_scope = pytest.mark.skipif(
    not _systemd_scope_available(),
    reason="systemd-run --user --scope unavailable on this host",
)


@pytest.fixture
def pep(tmp_path: Path) -> Pep:
    _require_opa()
    ledger = AuditLedger(tmp_path / "audit" / "audit.db")
    instance = Pep(
        opa_client=OpaClient(OPA, BUNDLE),
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
        task_registry={
            "simulate_association": (
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1:])",
                "--",
            )
        },
    )
    yield instance
    ledger.close()


def test_governance_defense_corpus_is_fully_intercepted(pep: Pep) -> None:
    report = run_governance_defense_benchmark(pep)

    print(report)
    assert len(ATTACK_CORPUS) >= 12
    assert len({scenario.id for scenario in ATTACK_CORPUS}) == len(ATTACK_CORPUS)
    assert {scenario.threat_category for scenario in ATTACK_CORPUS} == {
        "identity_spoof",
        "command_injection",
        "path_traversal",
        "receipt_replay",
        "credential_leak",
        "unauthorized_access",
        "hash_tamper",
        "self_approval",
        "resource_mismatch",
    }
    assert report.interception_rate == 1.0
    assert all(result.result == "BLOCKED" for result in report.scenario_results)
    assert all(result.audit_recorded for result in report.scenario_results)


@requires_systemd_scope
def test_systemd_runner_does_not_inherit_ark_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "benchmark-secret-that-must-not-leak"
    monkeypatch.setenv("ARK_API_KEY", secret)

    result = systemd_scope_runner(
        sys.executable,
        ("-c", "import os; print(os.environ.get('ARK_API_KEY', ''))"),
        {},
        10.0,
    )

    assert result.returncode == 0
    assert secret not in result.stdout
    assert os.environ["ARK_API_KEY"] == secret
