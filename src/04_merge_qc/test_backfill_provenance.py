#!/usr/bin/env python3
"""Phase 2 provenance 回填回归测试（2026-09-30）：归因正确性/优先级/幂等/层边界。

覆盖：curated 回查命中与 unattributed 残留 / llm 分流 / 跨源重复边优先级 /
registry 值一致性 / 事实字段零改动 / 节点分层 / 幂等 / 五枚举边界哨兵。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill_provenance as bp  # noqa: E402
from knowledge_layer import (FORBIDDEN_IN_KG, KG_ALLOWED_LAYERS,  # noqa: E402
                             KNOWLEDGE_LAYERS, check_layers)


@pytest.fixture
def reg():
    return pd.DataFrame({
        "source_key": ["maier2018_st3", "bugsigdb_export", "glm_extract_v2"],
        "version": ["2018", "full_dump 2025", "v2.6"],
        "last_sync": ["2026-09-15", "2026-09-15", "2026-09-23"],
    }).set_index("source_key")


@pytest.fixture
def lookup():
    # 跨源重复：(s1,p,o1) 同时在 seed 与 bugsigdb —— 必须归属先到者 maier2018_st3
    return {
        ("S1", "sensitive_to", "O1"): "maier2018_st3",
        ("S2", "alleviates", "O2"): "bugsigdb_export",
    }


def _edges():
    return pd.DataFrame({
        "subject": ["S1", "S2", "S3", "S4"],
        "predicate": ["sensitive_to", "alleviates", "produces", "affects"],
        "object": ["O1", "O2", "O3", "O4"],
        "source_type": ["curated", "curated", "curated", "llm_extracted"],
        "curator": ["automated_pipeline"] * 4,
    })


def test_enrich_edges_attribution(reg, lookup):
    out = bp.enrich_edges(_edges(), reg, lookup)
    assert out["source_id"].tolist() == [
        "maier2018_st3",       # 跨源重复 → 先到者
        "bugsigdb_export",
        "unattributed",        # curated 但源文件差集 → 不猜归属
        "glm_extract_v2",      # llm 分流
    ]
    assert out["retrieved_at"].tolist() == [
        "2026-09-15", "2026-09-15", "", "2026-09-23"]
    assert out["version"].tolist() == ["2018", "full_dump 2025", "", "v2.6"]


def test_enrich_edges_fact_columns_untouched(reg, lookup):
    old = _edges()
    out = bp.enrich_edges(old, reg, lookup)
    for col in old.columns:  # 事实字段零改动
        assert (old[col] == out[col]).all()
    assert set(bp.NEW_EDGE_COLS) <= set(out.columns) - set(old.columns)


def test_enrich_edges_idempotent(reg, lookup):
    once = bp.enrich_edges(_edges(), reg, lookup)
    twice = bp.enrich_edges(once, reg, lookup)
    pd.testing.assert_frame_equal(once, twice)


def test_enrich_nodes_layering():
    nodes = pd.DataFrame({"id": ["N1", "N2"], "name": ["a", "b"]})
    out = bp.enrich_nodes(nodes, curated_ids={"N1"})
    assert out["knowledge_layer"].tolist() == [
        "local_kg_curated", "local_kg_llm_extracted"]


def test_knowledge_layer_enum_boundaries():
    assert KG_ALLOWED_LAYERS == {"local_kg_curated", "local_kg_llm_extracted"}
    assert len(KNOWLEDGE_LAYERS) == 5
    assert FORBIDDEN_IN_KG == {"live_knowledge", "research_evidence", "method_knowledge"}
    # 本图数据面出现研究证据/实时/方法层 → 必须报违规（层边界哨兵）
    assert check_layers(["research_evidence"], context="t")
    assert check_layers(["live_knowledge"], context="t")
    assert check_layers(["method_knowledge"], context="t")
    assert check_layers(["local_kg_curated", "local_kg_llm_extracted"], context="t") == []
    assert check_layers(["weather_forecast"], context="t")  # 非法值


def test_build_edge_lookup_priority(tmp_path, monkeypatch):
    # 源文件顺序决定先到先得（与 merge_qc concat 顺序一致）
    monkeypatch.setattr(bp, "SEED", tmp_path)
    (tmp_path / "seed_edges.tsv").write_text(
        "subject\tpredicate\tobject\nS\tp\tO\n", encoding="utf-8")
    (tmp_path / "bugsigdb_edges.tsv").write_text(
        "subject\tpredicate\tobject\nS\tp\tO\nS\tq\tO\n", encoding="utf-8")
    lookup = bp.build_edge_lookup()
    assert lookup[("S", "p", "O")] == "maier2018_st3"
    assert lookup[("S", "q", "O")] == "bugsigdb_export"


def test_build_node_lookup_union(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "SEED", tmp_path)
    (tmp_path / "seed_nodes.tsv").write_text("id\tname\nA\ta\n", encoding="utf-8")
    (tmp_path / "bugsigdb_nodes.tsv").write_text("id\tname\nB\tb\n", encoding="utf-8")
    assert bp.build_node_lookup() == {"A", "B"}
