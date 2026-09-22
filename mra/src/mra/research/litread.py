"""文献速读：PubMed 检索 → 摘要抓取 → LLM 批量结构化速读，带本地缓存。

- 网络：NCBI E-utilities（无 key 公共限速 3 req/s），经 HTTPS_PROXY 代理；
- LLM：ARK 批量速读（每批 8 篇一次调用，max_calls 硬顶），解析失败记空批；
- 缓存：var/litread/<query与参数哈希>.json——同参数重复调用零 API；
- CLI：python -m mra.research.litread --query "..." [--question "..."] [--max 20]
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / "var" / "litread"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
BATCH_SIZE = 8


def _opener() -> urllib.request.OpenerDirector:
    import os

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = [urllib.request.ProxyHandler({"https": proxy, "http": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def pubmed_search(query: str, max_results: int = 20, date_from: str | None = None) -> list[str]:
    term = f"({query}) AND {date_from or '2020'}:2026[dp]"
    url = EUTILS + "esearch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "term": term, "retmax": max_results, "retmode": "json", "sort": "date"})
    with _opener().open(url, timeout=60) as resp:
        return json.load(resp)["esearchresult"]["idlist"]


def parse_pubmed_xml(content: bytes) -> list[dict]:
    """解析 efetch XML → [{pmid, title, year, abstract}]（可单测的纯函数）。"""
    out = []
    for art in ET.fromstring(content).iter("PubmedArticle"):
        title_el = art.find(".//ArticleTitle")
        abstract = " ".join("".join(a.itertext()) for a in art.findall(".//Abstract/AbstractText"))
        year = (art.findtext(".//PubDate/Year") or art.findtext(".//ArticleDate/Year")
                or art.findtext(".//DateCreated/Year") or "")
        out.append({"pmid": art.findtext(".//PMID") or "", "title":
                    "".join(title_el.itertext()) if title_el is not None else "",
                    "year": year, "abstract": abstract[:1200]})
    return out


def fetch_abstracts(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    url = EUTILS + "efetch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
    time.sleep(0.4)  # 无 key 公共限速
    with _opener().open(url, timeout=120) as resp:
        return parse_pubmed_xml(resp.read())


def quickread_notes(papers: list[dict], question: str, model_name: str | None = None,
                    max_calls: int = 4) -> list[dict]:
    """ARK 批量速读：每批 BATCH_SIZE 篇一次调用，返回结构化笔记列表。"""
    import uuid

    from ..model_runtime import Message, ModelRef, ModelRequest
    from ..model_runtime.ark import ArkRuntime
    from .planner import CAPABILITIES_PATH, PRICING_PATH

    runtime = ArkRuntime(capabilities_path=CAPABILITIES_PATH, pricing_path=PRICING_PATH)
    model_ref = ModelRef(provider="ark", model=model_name or "doubao-seed-2.0-lite",
                         version="unversioned", endpoint="ark-coding")
    system = (
        "你是文献速读器。对给定摘要批次输出 JSON：{\"key_findings\": [每篇一句话要点], "
        "\"answer\": \"针对给定问题的证据总结（2-3句，只依据给定文本）\", "
        "\"most_relevant_pmids\": [本批最相关pmid]}。只输出 JSON。"
    )
    notes: list[dict] = []
    for i in range(0, len(papers), BATCH_SIZE):
        if i // BATCH_SIZE >= max_calls:
            notes.append({"key_findings": [], "answer": "（达到 max_calls，未速读）",
                          "most_relevant_pmids": []})
            continue
        batch = papers[i:i + BATCH_SIZE]
        from ..budget import record_and_check
        record_and_check()
        payload = "\n\n".join(f"[PMID {p['pmid']}|{p['year']}] {p['title']}\n{p['abstract'][:900]}"
                              for p in batch)
        try:
            response = runtime.complete(ModelRequest(
                request_id=uuid.uuid4().hex, model=model_ref,
                messages=(Message(role="system", content=system),
                          Message(role="user", content=f"问题：{question}\n\n{payload}"))))
            text = response.content
            parsed = json.loads(text[text.find("{"):text.rfind("}") + 1])
            notes.append(parsed)
        except Exception:  # noqa: BLE001 —— 单批失败不崩整体
            notes.append({"key_findings": [], "answer": "（本批速读失败）",
                          "most_relevant_pmids": []})
    return notes


def search_and_read(query: str, question: str, max_results: int = 20,
                    model_name: str | None = None, max_calls: int = 4,
                    cache_dir: Path = DEFAULT_CACHE_DIR) -> dict:
    key = hashlib.sha256(f"{query}|{question}|{max_results}".encode()).hexdigest()[:16]
    cache = Path(cache_dir) / f"{key}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    pmids = pubmed_search(query, max_results=max_results)
    papers = fetch_abstracts(pmids)
    notes = quickread_notes(papers, question, model_name, max_calls) if papers else []
    result = {"query": query, "question": question, "n_papers": len(papers),
              "papers": papers, "notes": notes}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="文献速读（检索+批量结构化复读）")
    parser.add_argument("--query", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    result = search_and_read(args.query, args.question, max_results=args.max,
                             model_name=args.model)
    print(json.dumps({"n_papers": result["n_papers"],
                      "answers": [n.get("answer", "") for n in result["notes"]],
                      "relevant": [p for n in result["notes"]
                                   for p in n.get("most_relevant_pmids", [])][:10]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
