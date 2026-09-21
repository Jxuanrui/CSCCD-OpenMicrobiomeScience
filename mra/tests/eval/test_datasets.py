"""评测出题器最小行为检查：负例必须真不存在、时序切分必须按最早年份。"""
from __future__ import annotations

import csv
import json

import pytest

from mra.eval.datasets import build_replay, build_temporal, replay_questions, temporal_split
from mra.eval.litqa2 import microbiome_subset
from mra.kg.graph import KGGraph
from mra.kg.snapshot import create_snapshot

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    src = tmp_path_factory.mktemp("src")
    (src / "merged_nodes.tsv").write_text(
        NODES
        + "NCBITaxon:1\tBugOne\tMicrobe\t\t\tspecies\n"
        + "NCBITaxon:2\tBugTwo\tMicrobe\t\t\tspecies\n"
        + "CHEBI:1\tMetaboliteOne\tMetabolite\t\t\t\n"
        + "CHEBI:2\tMetaboliteTwo\tMetabolite\t\t\t\n"
        + "MESH:1\tDiseaseOne\tDisease\t\t\t\n",
        encoding="utf-8",
    )
    (src / "merged_edges.tsv").write_text(
        EDGES
        + "NCBITaxon:1\tproduces\tCHEBI:1\tcurated\tA\t10\t2018\t1\t1.0\t\t2026-09-15\n"
        + "NCBITaxon:2\tproduces\tCHEBI:2\tllm_extracted\tB\t20;21\t2023;2024\t2\t0.9\t\t2026-09-16\n"
        + "NCBITaxon:1\talleviates\tMESH:1\tcurated\tA\t\t\t1\t1.0\t\t2026-09-15\n",  # 无年份
        encoding="utf-8",
    )
    snap = create_snapshot(source=src, root=tmp_path_factory.mktemp("snaps"), snapshot_id="eval-fixture")
    return KGGraph(snap)


def test_replay_negatives_do_not_exist_in_graph(graph):
    qs = replay_questions(graph, n_negative=2, seed=7)
    positives = [q for q in qs if q["answer"]]
    negatives = [q for q in qs if not q["answer"]]
    assert len(positives) == 3  # A/B 边全部为正例（含无年份边）
    assert len(negatives) == 2
    for q in negatives:
        assert not graph.edge_evidence(q["subject"], q["object"])
        assert graph.nodes[q["object"]].category == graph.nodes[
            [p for p in positives if p["subject"] == q["subject"]][0]["object"]].category


def test_replay_deterministic(graph):
    assert replay_questions(graph, n_negative=2, seed=7) == replay_questions(graph, n_negative=2, seed=7)


def test_temporal_split_by_earliest_year(graph):
    split = temporal_split(graph, cutoff_year=2022)
    past_ids = [(e.subject, e.object) for e in split["past"]]
    held_ids = [(e.subject, e.object) for e in split["heldout"]]
    assert past_ids == [("NCBITaxon:1", "CHEBI:1")]        # 2018
    assert held_ids == [("NCBITaxon:2", "CHEBI:2")]        # 最早 2023 > 2022
    assert len(split["undated"]) == 1                       # 无年份 curated 边排除


def test_build_outputs_writable(graph, tmp_path):
    replay_path = build_replay(graph, out_path=tmp_path / "replay.jsonl", n_negative=1)
    assert sum(1 for _ in open(replay_path)) == 4
    result = build_temporal(graph, cutoff_year=2022, out_dir=tmp_path / "t2022")
    assert (tmp_path / "t2022" / "heldout_questions.jsonl").exists()
    rows = list(csv.DictReader(open(tmp_path / "t2022" / "past_edges.tsv"), delimiter="\t"))
    assert len(rows) == 1 and rows[0]["subject"] == "NCBITaxon:1"
    assert result["heldout_edges"] == 1 and result["undated_edges"] == 1
    assert json.loads((tmp_path / "t2022" / "stats.json").read_text())["cutoff_year"] == 2022


def test_litqa2_microbiome_subset_filter():
    rows = [
        {"question": "What gut bacterium produces butyrate?", "answer": "F"},
        {"question": "Which kinase phosphorylates p53?", "answer": "A"},
        {"question": "Metagenomic sequencing depth question", "answer": "B"},
    ]
    subset = microbiome_subset(rows)
    assert [r["question"][:12] for r in subset] == ["What gut bac", "Metagenomic "]
