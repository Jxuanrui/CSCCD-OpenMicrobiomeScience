#!/usr/bin/env python3
"""KEGG REST Pathway ETL：Gene→Pathway 边 + Pathway 节点（成熟轮子，不自建）。

人体宿主基因（NCBIGene）经 conv/hsa/ncbi-geneid 映射到 KEGG 基因，
再经 link/pathway/hsa 取全基因组基因-通路映射，筛出图中已有的 Gene。
KEGG API 学术使用免费，限速 3 req/s（本脚本仅 2 个请求）。

用法: python3 kegg_pathway_etl.py
输出: data/seed/kegg_pathway_nodes.tsv, data/seed/kegg_pathway_edges.tsv
"""
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "seed"
MERGED_NODES = ROOT / "data" / "merged" / "merged_nodes.tsv"
API = "https://rest.kegg.jp"
TODAY = date.today().isoformat()
session = requests.Session()
session.headers["User-Agent"] = "microbiome-kg-kegg/0.1 (academic research)"


def get_text(path):
    for attempt in range(4):
        r = session.get(API + path, timeout=60)
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1)); continue
        r.raise_for_status()
        return r.text
    raise RuntimeError(f"KEGG 限流: {path}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    nodes = pd.read_csv(MERGED_NODES, sep="\t").fillna("")
    genes = nodes[nodes["category"] == "Gene"]
    our_ncbi = {g.split(":", 1)[1]: r for g, r in
                zip(genes["id"], genes.to_dict("records"))}
    print(f"[in] 图中 Gene 节点 {len(our_ncbi)} 个（NCBIGene）")

    # 1) NCBIGene ↔ KEGG hsa 基因映射（实际格式：ncbi-geneid:N<TAB>hsa:N）
    conv = get_text("/conv/hsa/ncbi-geneid")
    ncbi_to_kegg = {}
    for line in conv.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ncbi, kegg_gene = parts[0].replace("ncbi-geneid:", ""), parts[1]
        if ncbi in our_ncbi:
            ncbi_to_kegg[ncbi] = kegg_gene
    print(f"[conv] 命中 KEGG 映射 {len(ncbi_to_kegg)} 个基因")

    # 2) 全基因组 gene→pathway 映射（单请求）
    link = get_text("/link/pathway/hsa")
    gene2pw = {}
    for line in link.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        gene, pw = parts[0], parts[1].replace("path:", "")
        gene2pw.setdefault(gene, []).append(pw)
    print(f"[link] KEGG hsa 基因-通路映射 {sum(len(v) for v in gene2pw.values())} 条")

    # 3) 图中基因的通路边
    edges, pw_ids = [], set()
    for ncbi, kegg_gene in ncbi_to_kegg.items():
        for pw in gene2pw.get(kegg_gene, []):
            edges.append({"subject": f"NCBIGene:{ncbi}", "predicate": "participates_in",
                          "object": f"KEGG:{pw}", "source_type": "curated", "evidence_tier": "A",
                          "pmids": "", "years": "", "support_count": 1,
                          "confidence": 1.0, "polarity": "neutral", "last_updated": TODAY})
            pw_ids.add(pw)
    print(f"[edges] 基中基因通路边 {len(edges)} 条，涉及通路 {len(pw_ids)} 个")

    # 4) 通路元数据（名称）
    pnodes = []
    if pw_ids:
        listing = get_text("/list/pathway/hsa")
        for line in listing.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[0] in pw_ids:
                name = re.sub(r"\s*- Homo sapiens.*$", "", parts[1])
                pnodes.append({"id": f"KEGG:{parts[0]}", "name": name, "category": "Pathway",
                               "aliases": "", "xrefs": "", "tax_rank": ""})
    print(f"[nodes] Pathway 节点 {len(pnodes)} 个（含名称）")

    pd.DataFrame(pnodes).to_csv(OUT / "kegg_pathway_nodes.tsv", sep="\t", index=False)
    pd.DataFrame(edges).to_csv(OUT / "kegg_pathway_edges.tsv", sep="\t", index=False)
    print(f"[out] -> {OUT}/kegg_pathway_nodes.tsv, kegg_pathway_edges.tsv")


if __name__ == "__main__":
    main()
