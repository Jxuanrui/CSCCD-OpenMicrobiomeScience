"""执行闸门行为检查：审计规则抓错、真实 R 过闸、账本留痕。"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from mra.research.gate import AuditGateError, _audit_verdicts, run_gated_association
from mra.research.rtools import RSCRIPT


def _toy_data(n=80, seed=5):
    rng = np.random.default_rng(seed)
    exposure = pd.Series(rng.normal(0, 1, n), name="exp")
    features = pd.DataFrame({"f1": exposure * 0.5 + rng.normal(0, .3, n),
                             "f2": rng.normal(0, 1, n)})
    cov = pd.DataFrame({"Age": rng.uniform(30, 80, n), "Gender": rng.integers(1, 3, n),
                        "Energy": rng.uniform(1500, 3500, n), "Batch": [f"B{i % 3}" for i in range(n)]})
    return exposure, features, cov


def test_audit_mult_catches_tampered_q():
    exposure, features, cov = _toy_data()
    result = pd.DataFrame({"feature": ["f1", "f2"], "rho": [.5, .1],
                           "p": [1e-4, 1e-4], "q": [0.99, 0.99], "n": [80, 80]})  # q 与 p 不符
    verdicts = _audit_verdicts(result, exposure, cov)
    mult = next(v for v in verdicts if v["rule"].startswith("AUDIT-MULT"))
    assert mult["verdict"] == "FAIL"


def test_audit_rules_pass_on_honest_result():
    exposure, features, cov = _toy_data()
    result = pd.DataFrame({"feature": ["f1", "f2"], "rho": [.5, .1],
                           "p": [1e-4, .6], "q": [2e-4, .6], "n": [80, 80]})  # BH(两值)=1e-4*2
    verdicts = _audit_verdicts(result, exposure, cov)
    mult = next(v for v in verdicts if v["rule"].startswith("AUDIT-MULT"))
    assert mult["verdict"] != "FAIL"


@pytest.mark.skipif(not RSCRIPT.is_file(), reason="microbiome_R 环境不可用")
def test_gated_association_end_to_end(tmp_path):
    exposure, features, cov = _toy_data()
    result, verdicts = run_gated_association(exposure, features, cov, run_id="gate-e2e",
                                             ledger_path=tmp_path / "a.db")
    assert set(result.columns) >= {"feature", "rho", "p", "q", "n"}
    assert all(v["verdict"] != "FAIL" for v in verdicts)
    conn = sqlite3.connect(tmp_path / "a.db")
    n_events = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert n_events == 1  # 执行事件已入账


def test_constant_exposure_denied_at_gate(tmp_path):
    """零方差守卫第二道闸：常数暴露在 R 执行前被拒，账本记 deny（无 R 也可回归）。"""
    exposure, features, cov = _toy_data()
    constant = pd.Series([0.0] * len(exposure), name="exp")
    with pytest.raises(AuditGateError, match="常数"):
        run_gated_association(constant, features, cov, run_id="gate-const",
                              ledger_path=tmp_path / "a.db")
    conn = sqlite3.connect(tmp_path / "a.db")
    row = conn.execute(
        "SELECT decision, reason_codes FROM audit_events").fetchone()
    assert row[0] == "deny"
    assert "constant-exposure" in row[1]
