"""Knowledge Gap Detector MVP（P1-1）行为检查：实体/方法双探测、建议动作、只读边界。"""
from __future__ import annotations

from pathlib import Path

from mra.kg.graph import KGGraph
from mra.kg.snapshot import create_snapshot
from mra.knowledge.gap import ACTION_QUERY_LOCAL, ACTION_ROUTE_LIVE, detect_gaps

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\years\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


def _graph(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        NODES + "NCBITaxon:1\tStreptococcus salivarius\tMicrobe\t\t\t\n", encoding="utf-8")
    (src / "merged_edges.tsv").write_text(EDGES, encoding="utf-8")
    return KGGraph(create_snapshot(source=src, root=tmp_path / "s", snapshot_id="gap-fx"))


def test_entity_coverage_probe(tmp_path):
    g = _graph(tmp_path)
    out = detect_gaps(g, entities=["Streptococcus salivarius", "NoSuch Bug X"],
                      analysis_types=[])
    assert out["overall"] == "entity_gaps_only"
    hit, miss = out["entities"]
    assert hit["covered"] and hit["recommended_action"] == ACTION_QUERY_LOCAL
    assert hit["knowledge_source"] == "LOCAL_KG"
    assert not miss["covered"] and miss["gap"] == "local_kg_miss"
    assert miss["recommended_action"] == ACTION_ROUTE_LIVE


def test_method_coverage_probe(tmp_path, monkeypatch):
    g = _graph(tmp_path)
    monkeypatch.setattr(
        "mra.knowledge.method_rules.search_method_rules",
        lambda q, k=3, db_path=None: (
            [{"rule_id": "method-zero-variance-guard-001", "structured": True}]
            if "零方差" in q else []))
    out = detect_gaps(g, entities=["Streptococcus salivarius"],
                      analysis_types=["零方差 常数列", "从未验证的全新分析XYZ"])
    cov, gap = out["analysis_types"]
    assert cov["covered"] and cov["rule_ids"] == ["method-zero-variance-guard-001"]
    assert not gap["covered"] and gap["recommended_action"] == "manual_design_review"
    assert out["overall"] == "method_gaps_only"


def test_no_gap_and_mixed(tmp_path):
    g = _graph(tmp_path)
    out = detect_gaps(g, entities=[], analysis_types=[])
    assert out["overall"] == "no_gap_detected"
    out2 = detect_gaps(g, entities=["Unknown"], analysis_types=["未知方法"])
    assert out2["overall"] == "mixed_gaps"


def test_dispatch_gap_check(monkeypatch):
    from mra.research.loop import ResearchContext, dispatch

    class FakeSession:
        pass

    ctx = ResearchContext.__new__(ResearchContext)
    ctx.session = FakeSession()
    ctx.graph = None  # detect_gaps 已被替换，不触图
    monkeypatch.setattr("mra.knowledge.gap.detect_gaps",
                        lambda graph, entities, analysis_types: {"status": "OK",
                                                                 "overall": "no_gap_detected"})
    result = dispatch({"tool": "gap_check", "args": {"entities": ["A"],
                                                     "analysis_types": ["B"]}}, ctx)
    assert result["overall"] == "no_gap_detected"
