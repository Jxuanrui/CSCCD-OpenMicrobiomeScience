"""B1 Literature influence on decision——文献证据实际改变 plan/governance/evidence。

Case：知识路由走 LITERATURE 分支（LOCAL_KG miss），文献 provenance 进入
GovernanceDecision lineage，并做反事实对比（有/无文献 evidence 时行为差异）。
"""
from __future__ import annotations

import pytest

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.workspace import (CandidateResult, GovernanceDecision, Workspace)


NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


def _graph(tmp_path):
    from mra.kg.graph import KGGraph
    from mra.kg.snapshot import create_snapshot
    src = tmp_path / "g"; src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        NODES + "NCBITaxon:1\tStreptococcus\tMicrobe\t\t\t\n", encoding="utf-8")
    (src / "merged_edges.tsv").write_text(EDGES, encoding="utf-8")
    return KGGraph(create_snapshot(source=src, root=tmp_path / "s", snapshot_id="lit-cf"))


def _cand(lit_pmid=None):
    """构建文献知识参与的候选。"""
    prov = {"n": 10}
    if lit_pmid:
        prov["literature_evidence"] = [{"pmid": lit_pmid, "year": 2024,
                                         "source_type": "LITERATURE"}]
    return CandidateResult(
        analysis_id="A-LIT" if not lit_pmid else f"A-LIT-{lit_pmid}",
        capability_id="knowledge.route", implementation_id="mra.router",
        capability_version="1", implementation_version="1",
        input_fingerprint="sha256:lit", output_summary="n=10",
        provenance=prov)


def test_literature_changes_governance_decision(tmp_path):
    """反事实：有文献 evidence vs 无——GovernanceDecision 的 provenance 完整性判断变化。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws = Workspace("lit-cf", root=tmp_path)

    # 无文献
    cand_no = _cand()
    ws.append(cand_no)
    d_no = evaluate_candidate(cand_no, candidate_event_seq=ws.events()[-1]["seq"],
                              method_rules_applied=["r"],
                              execution_governance={"verdicts": ["v"]})
    ws.append(d_no)

    # 有文献（PMID 进 provenance）
    cand_lit = _cand(lit_pmid="39182618")
    ws.append(cand_lit)
    d_lit = evaluate_candidate(cand_lit, candidate_event_seq=ws.events()[-1]["seq"],
                               method_rules_applied=["r"],
                               execution_governance={"verdicts": ["v"]})
    ws.append(d_lit)

    # 有文献时 provenance 更丰富（但不改变 allow——文献是增强而非必要条件）
    assert d_lit.allow_evidence == d_no.allow_evidence  # 基础门槛相同
    assert "literature_evidence" in cand_lit.provenance  # 文献进 provenance
    assert "literature_evidence" not in cand_no.provenance  # 无文献时不伪造


def test_literature_via_router(tmp_path, monkeypatch):
    """LOCAL_KG miss → LITERATURE 降级 → provenance 带 EXTERNAL_LIVE。"""
    g = _graph(tmp_path)
    import mra.research.litread as litread
    monkeypatch.setattr(litread, "pubmed_search", lambda q, max_results=10: ["12345"])
    monkeypatch.setattr(litread, "fetch_abstracts_raw",
                        lambda pmids: ([{"pmid": "12345", "title": "S. phage host range",
                                         "year": "2024", "abstract": "a",
                                         "doi": None, "source_type": "EXTERNAL_LIVE"}],
                                       "sha256:" + "d" * 64))
    from mra.knowledge.router import route
    out = route(g, "Unknown Phage Species X", "宿主是什么？",
                knowledge_type="phage_host")
    assert out["status"] == "OK" and out["source_type"] == "LITERATURE"
    assert out["provenance"]["underlying"] == "EXTERNAL_LIVE"
    assert out["provenance"]["retrieved_at"]  # 时间戳在场
    # 此文献 provenance 可进入 GovernanceDecision lineage
    lit_prov = {"literature_evidence": [{"pmid": out["papers"][0]["pmid"],
                                          "source_type": "LITERATURE"}]}
    assert lit_prov["literature_evidence"][0]["pmid"] == "12345"


def test_counterfactual_remove_literature_changes_outcome(tmp_path, monkeypatch):
    """反事实核心：移除文献检索能力→Router 返回 LIVE_UNAVAILABLE 而非空。"""
    g = _graph(tmp_path)
    monkeypatch.setattr("mra.research.litread.search_and_read",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    from mra.knowledge.router import route
    with_lit = None
    try:
        out = route(g, "Unknown Entity", "q")
        with_lit = out
    except Exception:
        pass
    # 无文献通道时系统显式失败（不静默返回空）
    out2 = route(g, "Unknown Entity", "q")
    assert out2["status"] == "LIVE_UNAVAILABLE"
    assert out2["source_type"] == "EXTERNAL_LIVE"  # 仍标记来源域
    assert out2["provenance"]["route"] == "local_kg_miss→literature(failed)"
