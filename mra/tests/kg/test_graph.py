"""KG 快照与图检索的最小行为检查（Ponytail：退化即失败）。"""
from __future__ import annotations

import csv
import json

import pytest

from mra.kg.graph import KGGraph, _split_multi
from mra.kg.snapshot import create_snapshot, latest_snapshot, list_snapshots
from mra.kg.tools import TOOL_SPECS, build_tool_functions

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


@pytest.fixture
def snap_dir(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        NODES
        + "NCBITaxon:216851\tFaecalibacterium prausnitzii\tMicrobe\tF. prausnitzii\t\tspecies\n"
        + "CHEBI:28817\tButyrates\tMetabolite\tbutyrate\t\t\n"
        + "MESH:D003093\tColitis, Ulcerative\tDisease\tUC\t\t\n",
        encoding="utf-8",
    )
    (src / "merged_edges.tsv").write_text(
        EDGES
        + "NCBITaxon:216851\tproduces\tCHEBI:28817\tllm_extracted\tB\t111;222\t2020;2021\t2\t0.9\t\t2026-09-16\n"
        + "CHEBI:28817\taffects\tMESH:D003093\tcurated\tA\t333|444\t2018|2019\t2\t1.0\tnegative\t2026-09-15\n",
        encoding="utf-8",
    )
    return create_snapshot(source=src, root=tmp_path / "snaps", snapshot_id="test-001")


def test_snapshot_manifest_counts_and_immutability(snap_dir, tmp_path):
    (manifest := json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8")))
    assert manifest["files"]["merged_nodes.tsv"]["data_lines"] == 3
    assert manifest["files"]["merged_edges.tsv"]["data_lines"] == 2
    assert manifest["snapshot_id"] == "test-001"
    snaps = list_snapshots(root=snap_dir.parent)
    assert [s["snapshot_id"] for s in snaps] == ["test-001"]
    assert latest_snapshot(root=snap_dir.parent).name == "test-001"
    with pytest.raises(FileExistsError):
        create_snapshot(source=tmp_path / "src", root=snap_dir.parent, snapshot_id="test-001")


def test_multi_value_split_accepts_both_separators():
    assert _split_multi("111;222") == ["111", "222"]
    assert _split_multi("333|444") == ["333", "444"]


def test_resolve_by_id_name_alias(snap_dir):
    g = KGGraph(snap_dir)
    assert [n.id for n in g.resolve("NCBITaxon:216851")] == ["NCBITaxon:216851"]
    assert [n.id for n in g.resolve("f. prausnitzii")] == ["NCBITaxon:216851"]  # 别名、大小写
    assert [n.id for n in g.resolve("uc")] == ["MESH:D003093"]
    assert g.resolve("不存在的实体") == []


def test_neighbors_two_hop_with_evidence(snap_dir):
    g = KGGraph(snap_dir)
    out = g.neighbors("NCBITaxon:216851", hops=2)
    names = {v["node"]["name"] for v in out["neighbors"].values()}
    assert {"Butyrates", "Colitis, Ulcerative"} <= names
    via = out["neighbors"]["CHEBI:28817"]["via"]
    assert via[0]["evidence"]["evidence_tier"] == "B"
    assert via[0]["evidence"]["pmids"] == ["111", "222"]
    filtered = g.neighbors("NCBITaxon:216851", hops=2, categories={"Disease"})
    assert set(filtered["neighbors"]) == {"MESH:D003093"}


def test_edge_evidence_and_years(snap_dir):
    g = KGGraph(snap_dir)
    edges = g.edge_evidence("NCBITaxon:216851", "CHEBI:28817")
    assert edges[0].earliest_year == 2020
    assert edges[0].years == [2020, 2021]
    assert g.edge_evidence("CHEBI:28817", "NCBITaxon:216851")  # 无向兜底命中


def test_tools_bind_graph_and_answer(snap_dir):
    tools = build_tool_functions(KGGraph(snap_dir))
    assert set(tools) == set(TOOL_SPECS)
    assert tools["kg_resolve"]({"term": "butyrate"})["candidates"][0]["id"] == "CHEBI:28817"
    out = tools["kg_neighbors"]({"term": "F. prausnitzii", "hops": 2})
    assert "Colitis, Ulcerative" in {v["node"]["name"] for v in out["neighbors"].values()}
    assert tools["kg_edge_evidence"]({"subject": "CHEBI:28817", "object": "MESH:D003093"})["edges"]
