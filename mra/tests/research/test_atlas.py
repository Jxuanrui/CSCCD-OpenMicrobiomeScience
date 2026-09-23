"""atlas 定级逻辑最小行为检查：同向=复制、反向=相反、缺边=独特候选。"""
from __future__ import annotations

import pandas as pd

from mra.kg.graph import KGGraph
from mra.kg.snapshot import create_snapshot
from mra.research.atlas import _numeric_columns, grade_hit

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


def _graph(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        NODES + "NCBITaxon:1\tBugOne\tMicrobe\t\t\tspecies\n"
        + "NCBITaxon:2\tBugTwo\tMicrobe\t\t\tspecies\n"
        + "NCBITaxon:3\tBugThree\tMicrobe\t\t\tspecies\n"
        + "LFS:FOOD:Fruit\tFruit\tFood\t\t\t\n", encoding="utf-8")
    (src / "merged_edges.tsv").write_text(
        EDGES
        + "NCBITaxon:1\tincreases_abundance_in\tLFS:FOOD:Fruit\tcurated\tA\t1\t2018\t1\t1.0\t\t2026-09-15\n"
        + "NCBITaxon:2\tdecreases_abundance_in\tLFS:FOOD:Fruit\tcurated\tA\t2\t2019\t1\t1.0\t\t2026-09-15\n",
        encoding="utf-8")
    return KGGraph(create_snapshot(source=src, root=tmp_path / "snaps", snapshot_id="atlas-fx"))


def test_grade_hit_directions(tmp_path, monkeypatch):
    from mra.research import datasources as ds
    monkeypatch.setattr(ds, "load_config", lambda: {
        "food_exposure_table": "food_groups",
        "food_node_hints": {"fruit_cup": "Fruit"}})
    g = _graph(tmp_path)
    assert grade_hit(g, "s__BugOne", "food_groups", "fruit_cup", rho=0.3) == "复制"      # 图谱↑ 我们+
    assert grade_hit(g, "s__BugTwo", "food_groups", "fruit_cup", rho=0.3) == "相反"      # 图谱↓ 我们+
    assert grade_hit(g, "s__BugThree", "food_groups", "fruit_cup", rho=-0.2) == "无该食物边（独特候选）"
    assert grade_hit(g, "s__Nobody", "food_groups", "fruit_cup", rho=0.1) == "图谱外"
    assert grade_hit(g, "s__BugOne", "dietary_patterns", "Pattern1", rho=0.2) == "暴露概念不在图（独特候选）"


def test_numeric_columns_drop_constant():
    """零方差守卫第三道闸：常数列（即使全为数值）不进入检验。"""
    frame = pd.DataFrame({"ok": [1.0, 2.0, 3.0], "zero": [0.0, 0.0, 0.0],
                          "txt": ["a", "b", "c"]})
    assert _numeric_columns(frame, None) == ["ok"]
    assert _numeric_columns(frame, ["ok", "zero"]) == ["ok"]
