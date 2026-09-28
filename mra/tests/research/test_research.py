"""研究循环最小行为检查：ID 交集、R 沙箱真跑、预算熔断、离线端到端。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mra.kg.graph import KGGraph
from mra.kg.snapshot import create_snapshot
from mra.research.datasources import intersect_ids, top_features_by_prevalence
from mra.research.loop import ResearchContext, dispatch, run_session
from mra.research.rtools import RSCRIPT, run_partial_spearman
from mra.research.session import BudgetExceeded, Finding, ResearchSession

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    src = tmp_path_factory.mktemp("src")
    (src / "merged_nodes.tsv").write_text(
        NODES + "NCBITaxon:1\tBugOne\tMicrobe\t\t\tspecies\n"
        + "CHEBI:1\tButyrates\tMetabolite\t\t\t\n", encoding="utf-8")
    (src / "merged_edges.tsv").write_text(
        EDGES + "NCBITaxon:1\tproduces\tCHEBI:1\tcurated\tA\t1\t2018\t1\t1.0\t\t2026-09-15\n",
        encoding="utf-8")
    snap = create_snapshot(source=src, root=tmp_path_factory.mktemp("snaps"), snapshot_id="rs-fx")
    return KGGraph(snap)


def test_intersect_ids_and_prevalence():
    a = pd.DataFrame(index=["S1", "S2", "S3"])
    b = pd.DataFrame(index=["S2", "S3", "S4"])
    assert intersect_ids(a, b) == ["S2", "S3"]
    feats = pd.DataFrame({"common": [1, 1, 0, 1], "rare": [1, 0, 0, 0]})
    assert top_features_by_prevalence(feats, 1) == ["common"]


@pytest.mark.skipif(not RSCRIPT.is_file(), reason="microbiome_R 环境不可用")
def test_r_sandbox_recovers_injected_signal():
    rng = np.random.default_rng(7)
    n = 120
    cov = pd.DataFrame({"Age": rng.uniform(30, 80, n), "Gender": rng.integers(1, 3, n),
                        "Energy": rng.uniform(1500, 3500, n), "Batch": rng.integers(0, 3, n)})
    fiber = rng.uniform(0, 10, n) + cov["Energy"] * 0.001  # 与 Energy 弱相关
    bug = pd.Series(fiber * 0.5 + rng.normal(0, 0.2, n))   # 注入真信号
    noise = pd.Series(rng.normal(0, 1, n))
    features = pd.DataFrame({"Bug_signal": bug, "Bug_noise": noise})
    out = run_partial_spearman(fiber, features, cov)
    by_feature = out.set_index("feature")
    assert by_feature.loc["Bug_signal", "q"] < 0.05
    assert by_feature.loc["Bug_signal", "rho"] > 0.5
    assert by_feature.loc["Bug_noise", "q"] > 0.05
    assert (out["n"] == n).all()


def test_constant_exposure_rejected_before_r():
    """零方差守卫第一道闸：常数暴露在进 R 前即 ValueError（无 R 也可回归）。"""
    rng = np.random.default_rng(7)
    cov = pd.DataFrame({"Age": rng.uniform(30, 80, 40), "Batch": ["a", "b"] * 20})
    feats = pd.DataFrame({"f": rng.normal(0, 1, 40)})
    with pytest.raises(ValueError, match="常数"):
        run_partial_spearman(pd.Series([0.0] * 40, name="avocado"), feats, cov)


@pytest.mark.skipif(not RSCRIPT.is_file(), reason="microbiome_R 环境不可用")
def test_r_sandbox_guards_constant_columns():
    """零方差守卫 R 侧冗余：常数暴露 RuntimeError；常数特征行被剔除不出伪值。"""
    rng = np.random.default_rng(11)
    n = 60
    cov = pd.DataFrame({"Age": rng.uniform(30, 80, n), "Batch": ["a", "b"] * (n // 2)})
    feats = pd.DataFrame({"const": [3.0] * n, "real": rng.normal(0, 1, n)})
    out = run_partial_spearman(pd.Series(rng.normal(0, 1, n), name="exp"), feats, cov)
    assert "const" not in set(out["feature"])  # 常数特征整行剔除
    assert set(out["feature"]) == {"real"}
    # 常数暴露被 Python 前置守卫拦截（ValueError），R 侧 stop 为直调 R 的冗余防线
    with pytest.raises(ValueError, match="常数"):
        run_partial_spearman(pd.Series([0.0] * n, name="zero"), feats, cov)


def test_session_budget_and_persistence(tmp_path):
    s = ResearchSession(question="q", target="t", run_id="budget-test",
                        root=tmp_path, llm_call_cap=2)
    s.record_llm_call(); s.record_llm_call()
    with pytest.raises(BudgetExceeded):
        s.record_llm_call()
    assert s.state["status"] == "budget_stopped"
    loaded = ResearchSession.load(s.run_dir)
    assert loaded.state["llm_calls"] == 2 and loaded.state["status"] == "budget_stopped"


def test_offline_session_end_to_end(graph, tmp_path, monkeypatch):
    # 用合成小表替换真实数据源，验证派发器全链路（kg + 记录 + 收口）
    import mra.research.datasources as ds
    rng = np.random.default_rng(3)
    n = 60
    ids = [f"S{i}" for i in range(n)]
    monkeypatch.setattr(ds, "load_exposures", lambda name: pd.DataFrame(
        {"Pattern_test": pd.Series(rng.normal(0, 1, n), index=ids)}))
    feats = pd.DataFrame(
        {"BugOne|s__bug": pd.Series(rng.normal(0, 1, n), index=ids)},
        index=ids)
    monkeypatch.setattr(ds, "load_features", lambda name: feats)
    monkeypatch.setattr(ds, "load_metadata", lambda: pd.DataFrame(
        {"Age": rng.uniform(30, 80, n), "Gender": rng.integers(1, 3, n),
         "Energy_kcal_方案B": rng.uniform(1500, 3500, n), "Batch": ["B1"] * n}, index=ids))
    monkeypatch.setattr(ds, "top_features_by_prevalence",
                        lambda frame, max_features: list(frame.columns[:max_features]))
    monkeypatch.setattr(ds, "load_config", lambda: {
        "exposures": {"dietary_patterns": "-"}, "features": {"species": "-"}})
    monkeypatch.setattr(ds, "default_covariates",
                        lambda: ["Age", "Gender", "Energy_kcal_方案B", "Batch"])
    monkeypatch.setattr("mra.research.gate.run_gated_association",
                        lambda *a, **k: (pd.DataFrame(
                            [{"feature": "BugOne|s__bug", "rho": 0.4, "p": 0.01, "q": 0.02, "n": n}]),
                            [{"rule": "AUDIT-MULT", "verdict": "PASS", "details": {}}]))

    plan = [
        {"tool": "kg_neighbors", "args": {"term": "BugOne", "hops": 1}},
        {"tool": "r_association", "args": {"exposure": "Pattern_test", "features": "species",
                                           "max_features": 10}},
        {"tool": "record_finding", "args": {"claim": "合成链路验证", "evidence": {"q": 0.02}}},
        {"tool": "submit_report", "args": {"summary": "离线端到端通过"}},
    ]
    session = run_session("测试问题", "测试目标", graph, offline_plan=plan,
                          session=ResearchSession(question="测试问题", target="测试目标",
                                                  run_id="offline-e2e", root=tmp_path))
    assert session.state["status"] == "done"
    assert session.state["llm_calls"] == 0
    assert any(f["claim"] == "合成链路验证" for f in session.state["findings"])
    assert (session.run_dir / "assoc_dietary_patterns_Pattern_test_species.tsv").exists()


def test_budget_stopped_session_resumes(graph, tmp_path, monkeypatch):
    import pandas as pd

    from mra.research.session import ResearchSession

    s = ResearchSession(question="续跑问题", target="t", run_id="resume-test",
                        root=tmp_path, llm_call_cap=1)
    s.record_llm_call()
    with pytest.raises(BudgetExceeded):
        s.record_llm_call()
    assert s.state["status"] == "budget_stopped"

    import mra.research.datasources as ds
    monkeypatch.setattr(ds, "load_config", lambda: {"exposures": {"t": "-"}, "features": {"species": "-"}})
    session = run_session("续跑问题", "t", graph,
                          offline_plan=[{"tool": "submit_report", "args": {"summary": "续跑收口"}}],
                          session=s)
    assert session.state["status"] == "done"
    assert session.state["llm_calls"] == 1  # 预算计数延续
    assert session.state["iterations"] >= 1


def test_dispatch_rejects_unknown_action(graph, tmp_path):
    ctx = ResearchContext(graph, ResearchSession(question="q", target="t",
                                                 run_id="reject-test", root=tmp_path))
    out = dispatch({"tool": "drop_table"}, ctx)
    assert "未知动作" in out["error"]


def test_top_features_tie_break_is_deterministic():
    """流行度全并列时按 非零中位丰度↓→名称↑ 确定性破断（atlas top200 框架修复）。"""
    frame = pd.DataFrame(
        [[1.0, 5.0, 2.0], [1.0, 5.0, 2.0], [0.0, 5.0, 2.0]], columns=["b", "a", "c"])
    # 三列流行度 2/3,2/3,2/3 全并列；非零中位 a=5 > b=1.5 > c=2? a=5,c=2,b=1.5 → a,c,b
    assert top_features_by_prevalence(frame, 3) == ["a", "c", "b"]
    assert top_features_by_prevalence(frame, 2) == ["a", "c"]
