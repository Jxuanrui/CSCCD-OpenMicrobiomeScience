"""序4 规则 R 单测（冻结规则的机器守护：改规则必先改测试+CHANGELOG）。"""
import csv
import json
import gzip
from pathlib import Path

from kg_tool.repair_conflicts import repair_edges

NODES = [
    {"id": "NCBITaxon:1", "category": "Species", "name": "A"},
    {"id": "MESH:D003093", "category": "Disease", "name": "X"},
    {"id": "MESH:D006973", "category": "Disease", "name": "Y"},
]

def _setup(tmp_path, edges):
    d = tmp_path / "kg"
    d.mkdir()
    with open(d / "merged_nodes.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "category", "name"],
                           delimiter="\t")
        w.writeheader()
        w.writerows(NODES)
    cols = ["subject", "predicate", "object", "edge_id", "pmids", "polarity"]
    with open(d / "merged_edges.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t")
        w.writeheader()
        w.writerows(edges)
    return d


def _e(s, p, o, eid, pmids):
    return {"subject": s, "predicate": p, "object": o, "edge_id": eid,
            "pmids": pmids, "polarity": "positive"}


def test_strict_winner_relabeled_loser_only(tmp_path):
    # aggravates 3 篇 vs alleviates 1 篇 → alleviates 侧转 affects
    d = _setup(tmp_path, [
        _e("NCBITaxon:1", "aggravates", "MESH:D003093", "e1", "111;222"),
        _e("NCBITaxon:1", "aggravates", "MESH:D003093", "e2", "333"),
        _e("NCBITaxon:1", "alleviates", "MESH:D003093", "e3", "999"),
    ])
    rows, ledger = repair_edges(d / "merged_edges.tsv")
    by = {r["edge_id"]: r["predicate"] for r in rows}
    assert by == {"e1": "aggravates", "e2": "aggravates", "e3": "affects"}
    assert ledger == [{"microbe_taxid": "1", "disease_mesh": "D003093",
                       "pmid_aggravates": 3, "pmid_alleviates": 1,
                       "winner": "aggravates", "action": "loser_relabel",
                       "edges_relabeled": 1}]


def test_tie_both_neutral(tmp_path):
    d = _setup(tmp_path, [
        _e("NCBITaxon:1", "aggravates", "MESH:D006973", "e1", "111"),
        _e("NCBITaxon:1", "alleviates", "MESH:D006973", "e2", "222"),
    ])
    rows, ledger = repair_edges(d / "merged_edges.tsv")
    assert all(r["predicate"] == "affects" for r in rows)
    assert ledger[0]["winner"] == "unresolved_tie"
    assert ledger[0]["edges_relabeled"] == 2


def test_one_paper_many_edges_counts_once(tmp_path):
    # 同一篇 PMID 产 3 条 aggravates 边 vs alleviates 1 篇 → distinct 数 1:1 = 平票
    d = _setup(tmp_path, [
        _e("NCBITaxon:1", "aggravates", "MESH:D006973", "e1", "111"),
        _e("NCBITaxon:1", "aggravates", "MESH:D006973", "e2", "111"),
        _e("NCBITaxon:1", "aggravates", "MESH:D006973", "e3", "111"),
        _e("NCBITaxon:1", "alleviates", "MESH:D006973", "e4", "222"),
    ])
    rows, ledger = repair_edges(d / "merged_edges.tsv")
    assert ledger[0]["pmid_aggravates"] == 1
    assert ledger[0]["winner"] == "unresolved_tie"


def test_affects_edges_untouched_and_no_deletion(tmp_path):
    d = _setup(tmp_path, [
        _e("NCBITaxon:1", "affects", "MESH:D006973", "e0", "555"),
        _e("NCBITaxon:1", "aggravates", "MESH:D006973", "e1", "111;112"),
        _e("NCBITaxon:1", "alleviates", "MESH:D006973", "e2", "222;223;224"),
    ])
    rows, ledger = repair_edges(d / "merged_edges.tsv")
    assert len(rows) == 3                      # 禁删边
    assert {r["edge_id"]: r["predicate"] for r in rows}["e0"] == "affects"
    assert {r["edge_id"]: r["predicate"] for r in rows}["e2"] == "alleviates"
    assert ledger[0]["winner"] == "alleviates"
