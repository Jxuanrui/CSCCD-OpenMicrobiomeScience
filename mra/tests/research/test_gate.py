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


def test_no_r_environment_contract(tmp_path, monkeypatch):
    """无 R 环境契约（监工 v3.0.4 归档条件）：R 检查在零方差守卫之后——
    常数暴露仍先入账 constant-exposure deny；正常暴露才报 R 不可用。"""
    from pathlib import Path as _P
    monkeypatch.setattr("mra.research.gate.RSCRIPT", _P("/nonexistent/Rscript"))
    exposure, features, cov = _toy_data()
    # 1) 常数暴露：零方差守卫先于 R 检查
    constant = pd.Series([0.0] * len(exposure), name="exp")
    with pytest.raises(AuditGateError, match="常数"):
        run_gated_association(constant, features, cov, run_id="gate-noR-const",
                              ledger_path=tmp_path / "a.db")
    conn = sqlite3.connect(tmp_path / "a.db")
    row = conn.execute("SELECT decision, reason_codes FROM audit_events").fetchone()
    assert row[0] == "deny" and "constant-exposure" in row[1]
    conn.close()
    # 2) 正常暴露：走到 R 检查报不可用
    with pytest.raises(AuditGateError, match="R 可执行文件不可用"):
        run_gated_association(exposure, features, cov, run_id="gate-noR-normal",
                              ledger_path=tmp_path / "b.db")


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


@pytest.mark.skipif(not RSCRIPT.is_file(), reason="断言 R 端报错文字，需 R（G3 复审 P2）")
def test_misaligned_exposure_blocked_at_gate(tmp_path):
    """真实门禁案例回归（2026-09-24 层间检验实测）：暴露向量若未随 complete-case
    过滤同步重排（1068 vs 1060），错位须在 R 端报错并被 gate 包装为 AuditGateError 拦截（断言其报错文字；AUDIT-BATCH-001 审计在 R 成功后另行执行），而非静默错位计算。"""
    exposure, features, cov = _toy_data()
    ids = list(cov.index[:-8])  # 模拟协变量侧 drop 8 例
    with pytest.raises(AuditGateError, match="exposure and batch must be non-empty vectors"):
        run_gated_association(exposure, features.loc[ids], cov.loc[ids],
                              run_id="gate-misalign", ledger_path=tmp_path / "a.db")
