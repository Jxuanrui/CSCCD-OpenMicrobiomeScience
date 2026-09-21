#!/usr/bin/env python3
"""
fetch_paper.py — 双路数据获取：PubTator3 优先，PDF 兜底

Usage:
    python3 fetch_paper.py <PMID>            # 输出 JSON 到 stdout
    python3 fetch_paper.py <PMID> --out dir  # 写入 dir/{PMID}_raw.json

返回结构:
{
  "pmid": "...",
  "source": "pubtator3" | "pubmed+pdf" | "pubmed_only",
  "meta": { title, journal, year, doi, authors, pmcid },
  "passages": [ { type, text } ],   # 全文段落（按顺序）
  "fulltext_available": true/false
}
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import argparse
import tempfile
from pathlib import Path

BASE_PUBTATOR = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"
BASE_EUTILS   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MicrobeScholar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ─── PubTator3 路径 ────────────────────────────────────────────────────────────

def fetch_pubtator3(pmid: str) -> dict | None:
    """尝试通过 PubTator3 biocjson 获取全文段落。返回 None 表示失败或无全文。"""
    url = f"{BASE_PUBTATOR}/publications/export/biocjson?pmids={pmid}&full=true"
    try:
        raw = _get(url)
        data = json.loads(raw)
        docs = data.get("PubTator3", [])
        if not docs:
            return None
        doc = docs[0]
        passages = doc.get("passages", [])
        if len(passages) < 5:          # 段落太少说明没有全文，只有摘要
            return None

        # 提取元数据
        infons = doc.get("infons", {})
        meta = {
            "pmid":    pmid,
            "pmcid":   infons.get("article-id_pmcid", ""),
            "doi":     infons.get("article-id_doi", ""),
            "title":   "",
            "journal": infons.get("journal", ""),
            "year":    infons.get("year", ""),
            "authors": infons.get("authors", ""),
        }
        # title 在第一个 passage 里
        for p in passages:
            if p.get("infons", {}).get("type") in ("front", "title"):
                meta["title"] = p.get("text", "")[:200]
                break

        # PubTator3 infons 经常缺 journal/year/doi，补充调用 PubMed efetch
        if not meta.get("journal") or not meta.get("year"):
            try:
                pubmed_meta = fetch_pubmed_meta(pmid)
                meta["journal"]  = meta.get("journal")  or pubmed_meta.get("journal", "")
                meta["year"]     = meta.get("year")      or pubmed_meta.get("year", "")
                meta["doi"]      = meta.get("doi")       or pubmed_meta.get("doi", "")
                meta["authors"]  = meta.get("authors")   or pubmed_meta.get("authors", "")
                if not meta["title"]:
                    meta["title"] = pubmed_meta.get("title", "")
            except Exception:
                pass

        structured = []
        for p in passages:
            ptype = p.get("infons", {}).get("type", "body")
            text  = p.get("text", "").strip()
            if text:
                structured.append({"type": ptype, "text": text})

        return {
            "pmid":               pmid,
            "source":             "pubtator3",
            "meta":               meta,
            "passages":           structured,
            "fulltext_available": True,
        }
    except Exception as e:
        print(f"[fetch_paper] PubTator3 error: {e}", file=sys.stderr)
        return None


# ─── PubMed 元数据路径 ─────────────────────────────────────────────────────────

def fetch_pubmed_meta(pmid: str) -> dict:
    """通过 PubMed efetch 获取摘要 + 元数据（XML 解析）。"""
    import xml.etree.ElementTree as ET

    url = (f"{BASE_EUTILS}/efetch.fcgi?"
           f"db=pubmed&id={pmid}&rettype=abstract&retmode=xml")
    try:
        raw  = _get(url)
        root = ET.fromstring(raw)
        art  = root.find(".//PubmedArticle/MedlineCitation/Article")
        if art is None:
            return {}

        title   = art.findtext("ArticleTitle", "")
        journal = art.findtext("Journal/Title", "")
        year    = (art.findtext("Journal/JournalIssue/PubDate/Year") or
                   art.findtext("Journal/JournalIssue/PubDate/MedlineDate", "")[:4])

        # 摘要段落
        abstract_texts = []
        for ab in art.findall(".//AbstractText"):
            label = ab.get("Label", "")
            text  = (ab.text or "").strip()
            if text:
                abstract_texts.append({"type": f"abstract_{label}".lower() if label else "abstract",
                                       "text": text})

        # 作者
        authors = []
        for a in art.findall(".//Author"):
            ln = a.findtext("LastName", "")
            fn = a.findtext("ForeName", "")
            if ln:
                authors.append(f"{ln} {fn}".strip())

        # DOI
        doi = ""
        for eid in root.findall(".//ArticleId"):
            if eid.get("IdType") == "doi":
                doi = eid.text or ""

        return {
            "title":   title,
            "journal": journal,
            "year":    year,
            "doi":     doi,
            "authors": ", ".join(authors[:6]) + (" et al." if len(authors) > 6 else ""),
            "abstract_passages": abstract_texts,
        }
    except Exception as e:
        print(f"[fetch_paper] PubMed efetch error: {e}", file=sys.stderr)
        return {}


# ─── PDF 兜底路径 ──────────────────────────────────────────────────────────────

def fetch_pdf_text(pmid: str, pmcid: str = "", doi: str = "") -> list[dict] | None:
    """尝试从 PMC 或 Unpaywall 获取 PDF 全文。返回段落列表或 None。"""
    try:
        import importlib
        if importlib.util.find_spec("pdfplumber") is None:
            print("[fetch_paper] pdfplumber not installed, skipping PDF path", file=sys.stderr)
            return None
        import pdfplumber
    except ImportError:
        return None

    pdf_url = None

    # 尝试 PMC PDF
    if pmcid:
        pmc_num = pmcid.replace("PMC", "")
        pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_num}/pdf/"

    # 尝试 Unpaywall（需要 DOI）
    if not pdf_url and doi:
        try:
            uw_url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email=microbe-scholar@research.local"
            uw_data = json.loads(_get(uw_url, timeout=10))
            best = uw_data.get("best_oa_location") or {}
            pdf_url = best.get("url_for_pdf", "")
        except Exception:
            pass

    if not pdf_url:
        return None

    try:
        raw_pdf = _get(pdf_url, timeout=30)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(raw_pdf)
            tmp_path = tmp.name

        passages = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages[:30]:       # 最多30页
                text = page.extract_text() or ""
                for para in text.split("\n\n"):
                    para = para.strip()
                    if len(para) > 60:        # 过滤页眉页脚碎片
                        passages.append({"type": "body", "text": para})

        Path(tmp_path).unlink(missing_ok=True)
        return passages if len(passages) > 5 else None

    except Exception as e:
        print(f"[fetch_paper] PDF fetch error: {e}", file=sys.stderr)
        return None


# ─── 主入口 ────────────────────────────────────────────────────────────────────

def fetch_paper(pmid: str) -> dict:
    pmid = pmid.strip()
    print(f"[fetch_paper] Fetching PMID {pmid}...", file=sys.stderr)

    # 路径 A：PubTator3 全文
    result = fetch_pubtator3(pmid)
    if result:
        print(f"[fetch_paper] PubTator3 OK — {len(result['passages'])} passages", file=sys.stderr)
        return result

    print("[fetch_paper] PubTator3 fulltext not available, falling back to PubMed + PDF", file=sys.stderr)

    # 路径 B：PubMed 元数据 + 摘要
    meta_raw = fetch_pubmed_meta(pmid)
    passages = list(meta_raw.pop("abstract_passages", []))
    meta = {k: v for k, v in meta_raw.items()}
    meta["pmid"]  = pmid
    meta["pmcid"] = ""

    # 路径 B+：尝试 PDF 补全
    pdf_passages = fetch_pdf_text(pmid, pmcid=meta.get("pmcid", ""), doi=meta.get("doi", ""))
    if pdf_passages:
        passages.extend(pdf_passages)
        source = "pubmed+pdf"
        fulltext = True
        print(f"[fetch_paper] PDF OK — {len(pdf_passages)} extra passages", file=sys.stderr)
    else:
        source   = "pubmed_only"
        fulltext = False
        print("[fetch_paper] PDF not available, abstract only", file=sys.stderr)

    return {
        "pmid":               pmid,
        "source":             source,
        "meta":               meta,
        "passages":           passages,
        "fulltext_available": fulltext,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pmid")
    parser.add_argument("--out", default="", help="Output directory (writes {pmid}_raw.json)")
    args = parser.parse_args()

    data = fetch_paper(args.pmid)

    if args.out:
        out_path = Path(args.out) / f"{args.pmid}_raw.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"[fetch_paper] Written to {out_path}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
