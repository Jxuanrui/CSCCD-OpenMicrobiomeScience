#!/usr/bin/env python3
"""PubTator3 多疾病域文献获取：esearch → BiocJSON export → 合并去重。

各疾病域检索式与菌群词 AND 组合，按 PMID 去重后与既有语料合并，输出唯一规范
语料 data/pubtator/articles.jsonl（每行一篇 BiocJSON 记录）。实体标准 ID 由
PubTator3 AIONER 归一，后续 LLM 关系分类直接复用。
"""
import argparse, json, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "pubtator"
API = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"
MICRO = '("gut microbiota" OR "gut microbiome" OR "intestinal microbiota" OR microbiome OR microbiota)'
DOMAINS = {
    "ibd": '("inflammatory bowel disease" OR Crohn* OR "ulcerative colitis")',
    "colorectal_cancer": '("colorectal cancer" OR "colorectal carcinoma" OR "colon cancer")',
    "metabolic": '(obesity OR "type 2 diabetes" OR "metabolic syndrome" OR insulin resistance)',
    "liver": '("non-alcoholic fatty liver" OR NAFLD OR "liver cirrhosis" OR hepatitis)',
    "neuro": '("gut-brain axis" OR Parkinson OR Alzheimer OR depression OR autism)',
    "cardiovascular": '("cardiovascular disease" OR atherosclerosis OR hypertension)',
    "autoimmune": '(arthritis OR "lupus erythematosus" OR psoriasis OR "multiple sclerosis")',
    "infection": '("Clostridioides difficile" OR "enteric infection" OR sepsis OR dysbiosis)',
    "metabolite": '("short chain fatty acids" OR "short-chain fatty acids" OR "bile acid" OR tryptophan)',
    "food_fiber": '("dietary fiber" OR prebiotic OR inulin OR "beta-glucan" OR "resistant starch" OR pectin)',
    "food_pattern": '("Mediterranean diet" OR "dietary pattern" OR "high-fat diet" OR "western diet")',
    "food_intervention": '("probiotic intervention" OR synbiotic OR "fermented food" OR "functional food")',
}


def esearch(session, query, retmax):
    # NCBI 对并发/突发请求限流（429），串行调用并指数退避。
    for attempt in range(6):
        r = session.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                        params={"db": "pubmed", "retmode": "json", "term": f"{query} AND {MICRO}",
                                "retmax": retmax, "sort": "pub date"}, timeout=30)
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()["esearchresult"]["idlist"]
    raise RuntimeError(f"esearch 限流重试失败: {query[:60]}")


def export_batch(session, pmids):
    for attempt in range(5):
        try:
            r = session.post(f"{API}/publications/export/biocjson",
                             json={"pmids": pmids}, timeout=90)
            r.raise_for_status()
            return r.json().get("PubTator3", [])
        except requests.RequestException:
            if attempt == 4: raise
            time.sleep(2 ** attempt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-domain", type=int, default=150, help="每个疾病域拉取的 PMID 上限")
    ap.add_argument("--pmids", help="按指定 PMID 清单拉取（JSON 数组文件，优先于疾病域检索；"
                                    "用于并入外部高质量语料如 MicrobeScholar）")
    ap.add_argument("--batch-size", type=int, default=50)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "articles.jsonl"
    session = requests.Session()
    session.headers["User-Agent"] = "microbiome-kg-pubtator/0.2 (academic research)"

    # 与既有语料合并（当前 articles.jsonl 或旧 ibd_articles.jsonl）
    records, existing = [], {}
    for prev in (out, OUT / "ibd_articles.jsonl"):
        if prev.exists():
            for line in prev.open(encoding="utf-8"):
                rec = json.loads(line)
                existing[str(rec.get("id", ""))] = rec
            print(f"[merge] 既有语料 {prev.name} {len(existing)} 篇", flush=True)

    if args.pmids:
        uniq = sorted({str(x) for x in json.loads(Path(args.pmids).read_text(encoding="utf-8"))})
        print(f"[pmids] 指定清单 {len(uniq)} 篇", flush=True)
    else:
        # 串行 esearch 各疾病域（避免 NCBI 429 限流）
        pmids, domain_stats = [], {}
        for d, q in DOMAINS.items():
            ids = esearch(session, q, args.per_domain)
            domain_stats[d] = len(ids)
            pmids.extend(ids)
            time.sleep(1.0)
        seen, uniq = set(), []
        for p in pmids:
            if p not in seen:
                seen.add(p); uniq.append(p)
        print(f"[search] 各域命中 {domain_stats}；去重后 {len(uniq)} 篇", flush=True)

    fetched = 0
    for i in range(0, len(uniq), args.batch_size):
        batch = export_batch(session, uniq[i:i + args.batch_size])
        for rec in batch:
            existing[str(rec.get("id", ""))] = rec
        fetched += len(batch)
        print(f"  PubTator: {min(i+args.batch_size,len(uniq))}/{len(uniq)} | 累计 {fetched} 篇", flush=True)
        time.sleep(0.5)
    with out.open("w", encoding="utf-8") as f:
        for rec in existing.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[out] 合并去重后 {len(existing)} 篇 → {out}", flush=True)


if __name__ == "__main__": main()
