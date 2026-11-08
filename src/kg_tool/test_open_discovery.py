"""序2 规则单测（冻结规则的机器守护）。"""
import csv
from pathlib import Path

from kg_tool.open_discovery import candidates, load_graph, DEG_CAP

def _setup(tmp_path, nodes, edges):
    d = tmp_path / "g"
    d.mkdir()
    with open(d / "merged_nodes.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "category"], delimiter="\t")
        w.writeheader()
        for nid, c in nodes:
            w.writerow({"id": nid, "category": c})
    with open(d / "merged_edges.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["subject", "object", "pmids"],
                           delimiter="\t")
        w.writeheader()
        for s, o, p in edges:
            w.writerow({"subject": s, "object": o, "pmids": p})
    return d

N = [("D1", "Disease"), ("D2", "Disease"),
     ("M1", "Metabolite"), ("M2", "Metabolite"), ("M3", "Metabolite"),
     ("S1", "Species"), ("S2", "Species")]

def test_candidate_generation_and_direct_exclusion(tmp_path):
    # (D1,M1) 经 {S1,S2}（S2-D1 与 S2-M1 都是合法两腿）；(D1,M2) 经 S1
    # 注意：直接边指 A-C 本身（D1-M1/D1-M2），S2-D1 是腿不是直接边
    d = _setup(tmp_path, N, [
        ("S1", "D1", "1"), ("S1", "M1", "2;3"), ("S1", "M2", "4"),
        ("S2", "M1", "5"), ("S2", "D1", "6"),          # 直接边
    ])
    cat, adj, pm = load_graph(d)
    cand, direct, capped = candidates(cat, adj, pm, {"Disease"},
                                      {"Metabolite"}, {"Species", "Microbe"})
    assert ("D1", "M1") in cand and ("D1", "M2") in cand
    assert cand[("D1", "M1")]["mids"] == {"S1", "S2"}
    # 两腿并集：经S1={1(腿),2,3} ∪ 经S2={6(腿),5}
    assert cand[("D1", "M1")]["pmids"] == {"1", "2", "3", "5", "6"}
    assert direct == 0

def test_degree_cap_excludes_promiscuous_intermediate(tmp_path):
    # 中间体 S1 连 60 个疾病（>50）→ 被排除
    nodes = N + [(f"D{i}", "Disease") for i in range(60)]
    edges = [("S1", "M1", "1")] + [(f"D{i}", "S1", str(i)) for i in range(60)]
    d = _setup(tmp_path, nodes, edges)
    cat, adj, pm = load_graph(d)
    cand, direct, capped = candidates(cat, adj, pm, {"Disease"},
                                      {"Metabolite"}, {"Species", "Microbe"})
    assert "S1" in capped and len(capped) == 1
    assert cand == {}

def test_rank_order_frozen():
    from kg_tool.open_discovery import rank_and_write
    import io
    cand = {("D1", "M1"): {"mids": {"S1", "S2"}, "pmids": {"1"}},
            ("D1", "M2"): {"mids": {"S1"}, "pmids": {"2", "3", "4"}},
            ("D2", "M1"): {"mids": {"S1"}, "pmids": {"5"}}}
    buf = Path("/tmp") / "od_test.tsv"
    rows = rank_and_write(cand, buf, "b", "note")
    assert rows[0][2:4] == ("D1", "M1")            # 2 中间体居首
    assert rows[1][2:4] == ("D1", "M2")            # tie: pmids 3 > 1
    assert rows[2][2:4] == ("D2", "M1")
    buf.unlink()
