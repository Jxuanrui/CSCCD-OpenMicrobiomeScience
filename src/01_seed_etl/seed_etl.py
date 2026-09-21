#!/usr/bin/env python3
"""种子图谱 ETL：curated 数据库 → 统一 nodes/edges TSV（列名与 LinkML schema 一致）。

用法:
  python3 seed_etl.py --source maier          # Maier 2018 药物筛选（自动下载已缓存于 data/raw/）
  python3 seed_etl.py --source gutmgene       # 需手动将官网下载文件放至 data/raw/manual/gutmgene.xlsx
  python3 seed_etl.py --source gutmdisorder   # 需手动放置 data/raw/manual/gutmdisorder.xlsx
  python3 seed_etl.py --source all

输出: data/seed/seed_nodes.tsv, data/seed/seed_edges.tsv（确定性重建，幂等）
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW, SEED = ROOT / "data" / "raw", ROOT / "data" / "seed"
SEED.mkdir(parents=True, exist_ok=True)
TODAY = date.today().isoformat()
MAIER_PMID = "29555994"


# ---------------------------------------------------------------- 归一化工具
class TaxonomyIndex:
    """本地 NCBI taxonomy names.dmp 名称→taxid 索引。"""

    def __init__(self, names_path: Path):
        self.by_name = {}
        keep = {"scientific name", "synonym", "equivalent name", "genbank synonym"}
        with open(names_path, encoding="utf-8") as f:
            for line in f:
                parts = [p.strip().strip("|").strip() for p in line.split("\t|\t")]
                if len(parts) >= 4 and parts[3] in keep:
                    self.by_name.setdefault(parts[1].lower(), parts[0])

    @staticmethod
    def _clean(name: str) -> str:
        name = re.sub(r"\s*\([^)]*\)\s*", " ", name)  # 去掉菌株号 (NT5021) 等
        name = re.sub(r"\b(sp\.|spp\.|str\.?)\b.*", "", name)
        return re.sub(r"\s+", " ", name).strip().lower()

    def resolve(self, name: str):
        for cand in (self._clean(name), " ".join(self._clean(name).split()[:2])):
            if cand in self.by_name:
                return self.by_name[cand]
        return None


class RestCache:
    """带磁盘缓存的 REST 归一化客户端（MeSH / RxNorm）。"""

    def __init__(self, cache_file: Path):
        self.cache_file = cache_file
        self.cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}

    def get(self, key: str, fetch):
        if key not in self.cache:
            try:
                self.cache[key] = fetch(key)
            except Exception:
                self.cache[key] = None
            self.cache_file.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0))
            time.sleep(0.35)  # NIH 限速礼貌间隔
        return self.cache[key]


def mesh_id(sess, cache: RestCache, label: str):
    def fetch(lbl):
        r = sess.get("https://id.nlm.nih.gov/mesh/lookup/descriptor",
                     params={"label": lbl, "limit": 1}, timeout=15)
        hits = r.json()
        return "MESH:" + hits[0].rsplit("/", 1)[-1] if hits else None
    return cache.get(label.lower(), fetch)


def rxnorm_id(sess, cache: RestCache, name: str):
    def fetch(n):
        r = sess.get("https://rxnav.nlm.nih.gov/REST/rxcui.json", params={"name": n}, timeout=15)
        ids = r.json().get("idGroup", {}).get("rxnormId") or []
        return "RXNORM:" + ids[0] if ids else None
    return cache.get(name.lower(), fetch)


# ---------------------------------------------------------------- 图积累器
class Graph:
    def __init__(self):
        self.nodes, self.edges = {}, {}

    def node(self, id_, name, category, aliases=None, xrefs=None, tax_rank=None):
        if id_ not in self.nodes:
            self.nodes[id_] = dict(id=id_, name=name, category=category,
                                   aliases="|".join(aliases or []), xrefs="|".join(xrefs or []),
                                   tax_rank=tax_rank or "")

    def edge(self, subject, predicate, object_, source_type, tier, pmids, years,
             support_count, confidence, polarity=""):
        key = (subject, predicate, object_)
        if key not in self.edges:
            self.edges[key] = dict(subject=subject, predicate=predicate, object=object_,
                                   source_type=source_type, evidence_tier=tier,
                                   pmids="|".join(pmids), years="|".join(years),
                                   support_count=support_count, confidence=confidence,
                                   polarity=polarity, last_updated=TODAY)


# ---------------------------------------------------------------- Maier 2018
def build_maier(g: Graph, tax: TaxonomyIndex, sess, mesh_cache, rx_cache):
    """ST3a: 药物(行) × 菌株(列) 调整后 p 值；p<0.05 → Microbe -[sensitive_to]-> Drug (Tier A)."""
    xlsx = RAW / "maier2018_st3.xlsx"
    if not xlsx.exists():
        sys.exit("缺少 data/raw/maier2018_st3.xlsx（下载脚本见 README 数据获取记录）")
    df = pd.read_excel(xlsx, sheet_name="S3a. Adjusted p-values")
    strain_cols = [c for c in df.columns if "(" in c]  # 形如 'Akkermansia muciniphila (NT5021)'
    n_edge = n_drug = n_strain = 0
    strain_ids = {}
    for col in strain_cols:
        clean = re.sub(r"\s*\([^)]*\)\s*$", "", col)
        tx = tax.resolve(clean)
        if not tx:
            print(f"  [warn] 菌名未归一: {clean}", file=sys.stderr)
            continue
        nid = f"NCBITaxon:{tx}"
        strain_ids[col] = nid
        g.node(nid, clean, "Microbe")
        n_strain += 1
    for _, row in df.iterrows():
        drug = str(row.get("chemical_name", "")).strip()
        if not drug or drug == "nan":
            continue
        rid = "LFS:DRUG:" + re.sub(r"\W+", "_", drug)
        g.node(rid, drug, "Drug")
        n_drug += 1
        for col, nid in strain_ids.items():
            v = row[col]
            try:
                p = float(v)
            except (TypeError, ValueError):
                continue
            if p < 0.05:
                g.edge(nid, "sensitive_to", rid, "curated", "A",
                       [MAIER_PMID], ["2018"], 1, 1.0)
                n_edge += 1
    print(f"  Maier: {n_strain} 株菌, {n_drug} 药物, {n_edge} 条 sensitive_to (p<0.05)")


# ---------------------------------------------------------------- 手动通道
def build_manual(g: Graph, tax, sess, mesh_cache, fname, label_cols, pred_map):
    """通用 xlsx 消费器：用户手动下载官网数据放 data/raw/manual/ 后即可接入。"""
    path = RAW / "manual" / fname
    if not path.exists():
        print(f"  [skip] {fname} 不存在（官网从本服务器不可达时，请手动下载放置）")
        return
    df = pd.read_excel(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    print(f"  {fname}: {len(df)} 行, 列={list(df.columns)}（解析规则待按实际列名补全）")


# ---------------------------------------------------------------- 输出
def write_tsv(g: Graph):
    nodes = pd.DataFrame(sorted(g.nodes.values(), key=lambda n: n["id"]))
    edges = pd.DataFrame(sorted(g.edges.values(), key=lambda e: (e["subject"], e["predicate"], e["object"])))
    nodes.to_csv(SEED / "seed_nodes.tsv", sep="\t", index=False)
    edges.to_csv(SEED / "seed_edges.tsv", sep="\t", index=False)
    print(f"[out] nodes={len(nodes)} edges={len(edges)} -> data/seed/")
    std = nodes.category.isin(["Microbe", "Disease", "Metabolite", "Gene"])
    ok = nodes[std].id.str.startswith(("NCBITaxon:", "MESH:", "CHEBI:", "NCBIGene:", "RXNORM:"))
    print(f"[qc] 标准ID归一率(可归一类): {ok.sum()}/{std.sum()} = {ok.mean() if std.any() else 0:.1%}")
    print(nodes.category.value_counts().to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="maier", choices=["maier", "gutmgene", "gutmdisorder", "all"])
    args = ap.parse_args()

    tax = TaxonomyIndex(RAW / "names.dmp")
    sess = requests.Session()
    sess.headers["User-Agent"] = "microbiome-kg-etl/0.1 (academic research)"
    mesh_cache = RestCache(RAW / "mesh_cache.json")
    rx_cache = RestCache(RAW / "rxnorm_cache.json")
    g = Graph()

    if args.source in ("maier", "all"):
        build_maier(g, tax, sess, mesh_cache, rx_cache)
    if args.source in ("gutmgene", "all"):
        build_manual(g, tax, sess, mesh_cache, "gutmgene.xlsx", None, None)
    if args.source in ("gutmdisorder", "all"):
        build_manual(g, tax, sess, mesh_cache, "gutmdisorder.xlsx", None, None)
    write_tsv(g)


if __name__ == "__main__":
    main()
