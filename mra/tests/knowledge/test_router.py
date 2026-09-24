"""Knowledge Router MVP（P0-1）行为检查：本地优先/miss降级/方法通道/live失败显式化。"""
from __future__ import annotations

from pathlib import Path

from mra.kg.graph import KGGraph
from mra.kg.snapshot import create_snapshot
from mra.knowledge.router import SOURCE_TYPES, route

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


def _graph(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        NODES + "NCBITaxon:1\tStreptococcus salivarius\tMicrobe\t\t\t\n"
        + "CHEBI:1\tButyrate\tMetabolite\t\t\t\n", encoding="utf-8")
    (src / "merged_edges.tsv").write_text(
        EDGES + "NCBITaxon:1\tproduces\tCHEBI:1\tcurated\tA\t1\t2018\t1\t1.0\t\t2026-09-15\n",
        encoding="utf-8")
    return KGGraph(create_snapshot(source=src, root=tmp_path / "s", snapshot_id="router-fx"))


def test_local_hit_returns_local_kg_with_tier(tmp_path):
    g = _graph(tmp_path)
    out = route(g, "Streptococcus salivarius", "产什么代谢物？")
    assert out["status"] == "OK" and out["source_type"] == "LOCAL_KG"
    assert out["evidence_status"].startswith("tier_")
    assert out["provenance"]["evidence_tiers"] == ["A"]
    assert out["counts_by_category"]


def test_local_miss_falls_through_to_literature(tmp_path, monkeypatch):
    g = _graph(tmp_path)
    import mra.research.litread as litread
    monkeypatch.setattr(litread, "pubmed_search", lambda q, max_results=10: ["9"])
    monkeypatch.setattr(litread, "fetch_abstracts_raw",
                        lambda pmids: ([{"pmid": "9", "title": "t", "year": "2024",
                                         "abstract": "a", "doi": None,
                                         "source_type": "EXTERNAL_LIVE"}], "sha256:" + "c" * 64))
    out = route(g, "Totally Unknown Entity", "它的噬菌体宿主关系？",
                knowledge_type="phage_host")
    assert out["status"] == "OK" and out["source_type"] == "LITERATURE"
    assert out["provenance"]["underlying"] == "EXTERNAL_LIVE"
    assert out["provenance"]["source_type"] == "EXTERNAL_LIVE"
    assert out["provenance"]["retrieved_at"]
    assert out["papers"][0]["pmid"] == "9"


def test_live_unavailable_is_explicit(tmp_path, monkeypatch):
    g = _graph(tmp_path)
    def boom(*a, **k):
        raise RuntimeError("no ARK key")
    monkeypatch.setattr("mra.research.litread.search_and_read", boom)
    out = route(g, "Unknown Entity X", "question")
    assert out["status"] == "LIVE_UNAVAILABLE"
    assert out["source_type"] == "EXTERNAL_LIVE"  # 失败也标明来源域，不冒充本地
    assert "P2" in out["note"]


def test_method_channel(tmp_path, monkeypatch):
    g = _graph(tmp_path)
    monkeypatch.setattr("mra.knowledge.method_rules.search_method_rules",
                        lambda q, k=5, db_path=None: [{"rule_id": "method-x-001"}])
    out = route(g, "method", "零方差", knowledge_type="method")
    assert out["source_type"] == "METHOD_KNOWLEDGE" and out["rules"][0]["rule_id"] == "method-x-001"


def test_source_types_contract():
    assert set(SOURCE_TYPES) == {"LOCAL_KG", "EXTERNAL_LIVE", "LITERATURE",
                                 "METHOD_KNOWLEDGE", "CURRENT_STUDY"}
