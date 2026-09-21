"""文献速读工具行为检查：XML 解析纯函数、缓存命中零重复调用、循环动作接线。"""
from __future__ import annotations

import json

from mra.research import litread
from mra.research.loop import ResearchContext, dispatch
from mra.research.session import ResearchSession

_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation><PMID>12345</PMID>
      <Article><ArticleTitle>Fake title &amp; study</ArticleTitle>
        <Abstract><AbstractText>Fake abstract about x.</AbstractText></Abstract>
        <Journal><ISSN>0000</ISSN></Journal>
      </Article>
      <DateCreated><Year>2024</Year></DateCreated>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


def test_parse_pubmed_xml_extracts_fields():
    papers = litread.parse_pubmed_xml(_XML)
    assert papers == [{"pmid": "12345", "title": "Fake title & study",
                       "year": "2024", "abstract": "Fake abstract about x."}]


def test_search_and_read_uses_cache(tmp_path, monkeypatch):
    calls = {"fetch": 0}

    monkeypatch.setattr(litread, "pubmed_search", lambda q, max_results=20: ["1", "2"])
    monkeypatch.setattr(litread, "fetch_abstracts",
                        lambda pmids: (calls.__setitem__("fetch", calls["fetch"] + 1),
                                       [{"pmid": p, "title": "t", "year": "2024",
                                         "abstract": "a"} for p in pmids])[1])
    monkeypatch.setattr(litread, "quickread_notes",
                        lambda papers, question, model_name=None, max_calls=4:
                        [{"key_findings": ["k"], "answer": "ans", "most_relevant_pmids": ["1"]}])
    r1 = litread.search_and_read("q1", "isr", max_results=5, cache_dir=tmp_path)
    r2 = litread.search_and_read("q1", "isr", max_results=5, cache_dir=tmp_path)
    assert r1["n_papers"] == 2 and r2 == r1
    assert calls["fetch"] == 1  # 第二次走缓存
    assert len(list(tmp_path.glob("*.json"))) == 1  # 单一缓存文件


def test_dispatch_lit_action(monkeypatch, tmp_path):
    from mra.kg.graph import KGGraph
    from mra.kg.snapshot import create_snapshot

    src = tmp_path / "src"
    src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        "id\tname\tcategory\taliases\txrefs\ttax_rank\nNCBITaxon:1\tB\tMicrobe\t\t\t\n",
        encoding="utf-8")
    (src / "merged_edges.tsv").write_text(
        "subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
        "support_count\tconfidence\tpolarity\tlast_updated\n", encoding="utf-8")
    graph = KGGraph(create_snapshot(source=src, root=tmp_path / "s", snapshot_id="lit"))

    monkeypatch.setattr("mra.research.litread.search_and_read",
                        lambda query, question, max_results=20, max_calls=4: {
                            "n_papers": 3, "papers": [],
                            "notes": [{"answer": "lit ans", "most_relevant_pmids": ["9"]}]})
    ctx = ResearchContext(graph, ResearchSession(question="q", target="t",
                                                 run_id="lit-dispatch", root=tmp_path))
    out = dispatch({"tool": "lit_search_read",
                    "args": {"query": "test query", "question": "test q"}}, ctx)
    assert out["n_papers"] == 3 and out["answers"] == ["lit ans"]
    assert out["relevant_pmids"] == ["9"]
