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
                       "year": "2024", "abstract": "Fake abstract about x.",
                       "doi": None, "source_type": "EXTERNAL_LIVE"}]


def test_search_and_read_uses_cache(tmp_path, monkeypatch):
    calls = {"fetch": 0}

    monkeypatch.setattr(litread, "pubmed_search", lambda q, max_results=20: ["1", "2"])
    monkeypatch.setattr(litread, "fetch_abstracts_raw",
                        lambda pmids: (calls.__setitem__("fetch", calls["fetch"] + 1),
                                       [{"pmid": p, "title": "t", "year": "2024",
                                         "abstract": "a", "doi": None,
                                         "source_type": "EXTERNAL_LIVE"} for p in pmids],
                                       "sha256:" + "b" * 64)[1:])
    monkeypatch.setattr(litread, "quickread_notes",
                        lambda papers, question, model_name=None, max_calls=4:
                        [{"key_findings": ["k"], "answer": "ans", "most_relevant_pmids": ["1"]}])
    r1 = litread.search_and_read("q1", "isr", max_results=5, cache_dir=tmp_path)
    r2 = litread.search_and_read("q1", "isr", max_results=5, cache_dir=tmp_path)
    assert r1["n_papers"] == 2
    assert {k: v for k, v in r2.items() if k != "from_cache"} == \
        {k: v for k, v in r1.items() if k != "from_cache"}  # 缓存回读等价（除命中标记）
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


# ---- P0-2 provenance 升级 ----
XML = (b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID>'
       b'<Article><ArticleTitle>T</ArticleTitle><Abstract><AbstractText>A</AbstractText></Abstract>'
       b'<ArticleDate><Year>2024</Year></ArticleDate></Article></MedlineCitation>'
       b'<PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/x</ArticleId>'
       b'</ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>')


def test_parse_pubmed_xml_doi_and_source_type():
    from mra.research.litread import parse_pubmed_xml
    papers = parse_pubmed_xml(XML)
    assert papers[0]["doi"] == "10.1/x"
    assert papers[0]["source_type"] == "EXTERNAL_LIVE"


def test_eutils_url_appends_api_key(monkeypatch):
    from mra.research import litread
    monkeypatch.setenv("NCBI_API_KEY", "k1")
    url = litread._eutils_url("esearch.fcgi", {"db": "pubmed"})
    assert "api_key=k1" in url
    monkeypatch.delenv("NCBI_API_KEY")
    assert "api_key" not in litread._eutils_url("esearch.fcgi", {"db": "pubmed"})


def test_provenance_block_and_cache_roundtrip(tmp_path, monkeypatch):
    from mra.research import litread
    monkeypatch.setattr(litread, "pubmed_search", lambda q, max_results=20: ["123"])
    monkeypatch.setattr(litread, "fetch_abstracts_raw",
                        lambda pmids: (litread.parse_pubmed_xml(XML), "sha256:" + "a" * 64))
    monkeypatch.setattr(litread, "quickread_notes", lambda *a, **k: [])
    r1 = litread.search_and_read("q", "qq", cache_dir=tmp_path)
    assert r1["from_cache"] is False
    p = r1["provenance"]
    assert p["source_type"] == "EXTERNAL_LIVE" and p["source_id"] == "ncbi-eutils-pubmed"
    assert p["retrieved_at"] and p["raw_response_sha256"].startswith("sha256:")
    r2 = litread.search_and_read("q", "qq", cache_dir=tmp_path)
    assert r2["from_cache"] is True
    assert r2["provenance"]["retrieved_at"] == p["retrieved_at"]  # 缓存保留原始时间
    assert r2["papers"][0]["source_type"] == "EXTERNAL_LIVE"


def test_legacy_cache_wrapped_with_external_live(tmp_path):
    import json
    from mra.research import litread
    key = litread.hash_key("q", "qq", 20) if hasattr(litread, "hash_key") else None
    # 直接构造旧版缓存（无 provenance 键）
    from pathlib import Path
    import hashlib
    k = hashlib.sha256("q|qq|20".encode()).hexdigest()[:16]
    (tmp_path / f"{k}.json").write_text(json.dumps(
        {"query": "q", "n_papers": 0, "papers": [], "notes": []}), encoding="utf-8")
    r = litread.search_and_read("q", "qq", cache_dir=tmp_path)
    assert r["from_cache"] is True
    assert r["provenance"]["source_type"] == "EXTERNAL_LIVE"
    assert "legacy" in r["provenance"]["retrieved_at"]
