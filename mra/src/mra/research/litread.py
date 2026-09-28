"""文献速读：PubMed 检索 → 摘要抓取 → LLM 批量结构化速读，带本地缓存与完整 provenance。

- 网络：NCBI E-utilities（无 key 公共限速 3 req/s；设 NCBI_API_KEY 提至 10 req/s），
  经 HTTPS_PROXY 代理；单次失败重试 1 次；
- LLM：ARK 批量速读（每批 8 篇一次调用，max_calls 硬顶），解析失败记空批；
- provenance（P0-2）：每条结果携带 source_type=EXTERNAL_LIVE / source_name /
  source_id / retrieved_at / query / database / raw_response_sha256 / from_cache；
  缓存命中保留原始 retrieved_at。**查询与缓存不改变其 EXTERNAL_LIVE 性质——
  本模块没有任何写入 Local KG 的路径**（升级为 LOCAL 知识只能走 knowledge/
  的 curated ingestion 流程）；
- 缓存：var/litread/<query与参数哈希>.json——同参数重复调用零 API；
- CLI：python -m mra.research.litread --query "..." [--question "..."] [--max 20]
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / "var" / "litread"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
BATCH_SIZE = 8
SOURCE_TYPE = "EXTERNAL_LIVE"
SOURCE_NAME = "NCBI PubMed E-utilities"
SOURCE_ID = "ncbi-eutils-pubmed"


def _opener() -> urllib.request.OpenerDirector:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = [urllib.request.ProxyHandler({"https": proxy, "http": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def _eutils_url(endpoint: str, params: dict) -> str:
    """构造 eutils URL；NCBI_API_KEY 存在时自动附加（限速 3→10 req/s）。"""
    if os.environ.get("NCBI_API_KEY"):
        params = {**params, "api_key": os.environ["NCBI_API_KEY"]}
    return EUTILS + endpoint + "?" + urllib.parse.urlencode(params)


def _fetch(url: str, timeout: float) -> bytes:
    """带 1 次重试的 GET（URLError/超时类；4xx/5xx 不重试）。"""
    for attempt in (1, 2):
        try:
            with _opener().open(url, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(1.0)


def pubmed_search(query: str, max_results: int = 20, date_from: str | None = None) -> list[str]:
    term = f"({query}) AND {date_from or '2020'}:2026[dp]"
    raw = _fetch(_eutils_url("esearch.fcgi", {
        "db": "pubmed", "term": term, "retmax": max_results,
        "retmode": "json", "sort": "date"}), timeout=60)
    return json.loads(raw)["esearchresult"]["idlist"]


def parse_pubmed_xml(content: bytes) -> list[dict]:
    """解析 efetch XML → [{pmid, title, year, abstract, doi, source_type}]（可单测纯函数）。"""
    out = []
    for art in ET.fromstring(content).iter("PubmedArticle"):
        title_el = art.find(".//ArticleTitle")
        abstract = " ".join("".join(a.itertext()) for a in art.findall(".//Abstract/AbstractText"))
        year = (art.findtext(".//PubDate/Year") or art.findtext(".//ArticleDate/Year")
                or art.findtext(".//DateCreated/Year") or "")
        doi = ""
        for aid in art.findall(".//ArticleId"):
            if aid.get("IdType") == "doi":
                doi = (aid.text or "").strip()
        out.append({"pmid": art.findtext(".//PMID") or "", "title":
                    "".join(title_el.itertext()) if title_el is not None else "",
                    "year": year, "abstract": abstract[:1200],
                    "doi": doi or None, "source_type": SOURCE_TYPE})
    return out


def fetch_abstracts(pmids: list[str]) -> list[dict]:
    papers, _ = fetch_abstracts_raw(pmids)
    return papers


def fetch_abstracts_raw(pmids: list[str]) -> tuple[list[dict], str]:
    """返回 (papers, 原始 efetch XML 的 sha256)——provenance 用。"""
    if not pmids:
        return [], ""
    time.sleep(0.4)  # 无 key 公共限速（有 key 时间隔亦可满足 10 req/s）
    raw = _fetch(_eutils_url("efetch.fcgi", {
        "db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}), timeout=120)
    return parse_pubmed_xml(raw), hashlib.sha256(raw).hexdigest()


def quickread_notes(papers: list[dict], question: str, model_name: str | None = None,
                    max_calls: int = 4) -> list[dict]:
    """ARK 批量速读：每批 BATCH_SIZE 篇一次调用，返回结构化笔记列表。"""
    import uuid

    from ..model_runtime import Message, ModelRef, ModelRequest
    from ..model_runtime.ark import ArkRuntime
    from .planner import CAPABILITIES_PATH, PRICING_PATH

    runtime = None  # 惰性构造：max_calls=0（Router 纯元数据检索）不触碰 LLM 凭据
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
        if runtime is None:
            runtime = ArkRuntime(capabilities_path=CAPABILITIES_PATH, pricing_path=PRICING_PATH)
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
        result = json.loads(cache.read_text(encoding="utf-8"))
        prov = result.get("provenance")
        if prov is None:  # 旧版缓存无 provenance——标记 legacy，保持 EXTERNAL_LIVE 语义
            result["provenance"] = {
                "source_type": SOURCE_TYPE, "source_name": SOURCE_NAME,
                "source_id": SOURCE_ID, "retrieved_at": "unknown (legacy cache)",
                "query": query, "database": "pubmed", "api": "esearch+efetch",
                "database_version": None, "raw_response_sha256": None,
                "note": "升级前缓存，元数据不全；如需完整 provenance 请换参数重查"}
        result["from_cache"] = True
        result["cache_key"] = key
        return result
    pmids = pubmed_search(query, max_results=max_results)
    papers, raw_sha = fetch_abstracts_raw(pmids)
    notes = quickread_notes(papers, question, model_name, max_calls) if papers else []
    result = {"query": query, "question": question, "n_papers": len(papers),
              "papers": papers, "notes": notes, "from_cache": False, "cache_key": key,
              "provenance": {
                  "source_type": SOURCE_TYPE, "source_name": SOURCE_NAME,
                  "source_id": SOURCE_ID,
                  "retrieved_at": datetime.now(timezone.utc).isoformat(),
                  "query": query, "question": question,
                  "database": "pubmed", "api": "esearch+efetch (retmode json/xml)",
                  "database_version": None,  # eutils 不返回版本号；可获得时回填
                  "api_key_used": bool(os.environ.get("NCBI_API_KEY")),
                  "raw_response_sha256": raw_sha or None,
                  "n_pmids": len(pmids)}}
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
