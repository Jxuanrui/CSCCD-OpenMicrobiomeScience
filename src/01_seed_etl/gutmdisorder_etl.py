#!/usr/bin/env python3
"""将 gutMDisorder Literature-based Disorder_Health 表转换为本项目统一节点/边 TSV。
首批只接入文献型 Disorder vs Health 关联，不改原始数据。"""
from datetime import date
from pathlib import Path
import re
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data/sources/gutMDisorder/Common Resource/gutMDisorder_v3_Literature-based_Disorder_Health.xlsx"
OUT = ROOT / "data/seed"
TODAY = date.today().isoformat()
NODE_COLS = ["id", "name", "category", "aliases", "xrefs", "tax_rank"]
EDGE_COLS = [
    "subject", "predicate", "object", "source_type", "evidence_tier",
    "pmids", "years", "support_count", "confidence", "polarity", "last_updated"
]


def text(v):
    return "" if pd.isna(v) else str(v).strip()


def clean_id(v):
    return text(v).replace(".0", "")


def mesh_id(v):
    x = clean_id(v)
    if not x or x.lower() in {"nan", "none", "health", "d006262"}:
        return ""
    return x if x.startswith("MESH:") else f"MESH:{x}"


# Disorder_Health 表的复合条件清洗（人工抽检发现的词表问题）：
# "A;B" 双联命名与 "MESH:*;D012345" 复合 ID 做确定性拆分——只保留携带有效
# MeSH ID 的疾病侧；Health/Diet/Age 等非疾病概念一律剔除。
NON_DISEASE = {"health", "healthy", "diet", "age", "*", ""}


def clean_condition(dname, dids):
    """返回 (清洗后名称, MeSH ID)；复合条件选首个有效疾病侧，无可留部分返回空。"""
    dname, dids = text(dname), text(dids)
    if ";" not in dname and ";" not in dids and "," not in dids:
        mid = mesh_id(dids)
        return (dname, mid) if mid and dname.lower() not in NON_DISEASE else ("", "")
    nps = [x.strip() for x in dname.split(";") if x.strip()]
    ips = [x.strip() for x in re.split(r"[;,]", dids) if x.strip()]
    if len(nps) == len(ips):
        for n, v in zip(nps, ips):
            if n.lower() in NON_DISEASE:
                continue
            mid = mesh_id(v)
            if mid:
                return n, mid
        return "", ""
    # 长度不齐：MeSH 倒序名可能被分号拆开，整体视为单一概念取首个有效 ID。
    for v in ips:
        mid = mesh_id(v)
        if mid:
            return dname.replace(";", ", "), mid
    return "", ""


def add_node(nodes, id_, name, cat, xrefs="", rank=""):
    if not id_:
        return
    if id_ not in nodes:
        nodes[id_] = {
            "id": id_,
            "name": text(name) or id_,
            "category": cat,
            "aliases": "",
            "xrefs": xrefs,
            "tax_rank": rank,
        }


def add_edge(edges, subject, predicate, object_, pmid, year):
    if not subject or not object_ or not predicate:
        return
    key = (subject, predicate, object_)
    e = edges.setdefault(
        key,
        {
            "subject": subject,
            "predicate": predicate,
            "object": object_,
            "source_type": "curated",
            "evidence_tier": "A",
            "pmids": set(),
            "years": set(),
            "support_count": 0,
            "confidence": 1.0,
            "polarity": "",
            "last_updated": TODAY,
        },
    )
    if pmid:
        e["pmids"].add(pmid)
    if year:
        e["years"].add(year)
    e["support_count"] += 1


def main():
    lit = pd.read_excel(SRC, sheet_name="Literature")
    assoc = pd.read_excel(SRC, sheet_name="Association")
    lit = lit.set_index("Index", drop=False)
    nodes, edges = {}, {}

    for _, row in assoc.iterrows():
        idx = row.get("Index")
        if idx not in lit.index:
            continue
        lit_row = lit.loc[idx]
        if isinstance(lit_row, pd.DataFrame):
            lit_row = lit_row.iloc[0]

        tax = clean_id(row.get("GutMicrobiataNCBIID"))
        microbe_name = text(row.get("GutMicrobe"))
        rank = text(row.get("Classification")).lower()
        alteration = text(row.get("Alteration")).lower()
        pmid = clean_id(lit_row.get("PMID"))
        year = clean_id(lit_row.get("Year"))

        disease_name, disease_id = clean_condition(lit_row.get("Condition1"), lit_row.get("Condition1ID"))
        # Disorder vs Health 表中，Condition2 通常是 Health；只保留疾病侧。
        if not disease_id:
            disease_name, disease_id = clean_condition(lit_row.get("Condition2"), lit_row.get("Condition2ID"))
        if not disease_id:
            continue

        if not tax.isdigit():
            continue
        microbe_id = f"NCBITaxon:{tax}"
        add_node(nodes, microbe_id, microbe_name, "Microbe", rank=rank)
        add_node(nodes, disease_id, disease_name, "Disease")

        if "increase" in alteration:
            pred = "increases_abundance_in"
        elif "decrease" in alteration:
            pred = "decreases_abundance_in"
        else:
            continue
        add_edge(edges, microbe_id, pred, disease_id, pmid, year)

    for e in edges.values():
        e["pmids"] = "|".join(sorted(x for x in e["pmids"] if x))
        e["years"] = "|".join(sorted(x for x in e["years"] if x))
        e["support_count"] = int(e["support_count"])

    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(list(nodes.values()), columns=NODE_COLS).sort_values("id").to_csv(
        OUT / "gutmdisorder_nodes.tsv", sep="\t", index=False
    )
    pd.DataFrame(list(edges.values()), columns=EDGE_COLS).sort_values(
        ["subject", "predicate", "object"]
    ).to_csv(OUT / "gutmdisorder_edges.tsv", sep="\t", index=False)

    print(
        f"gutMDisorder Literature rows={len(assoc)}; "
        f"nodes={len(nodes)} edges={len(edges)}"
    )
    if nodes:
        print("node categories:", pd.DataFrame(nodes.values())["category"].value_counts().to_dict())
    if edges:
        print("predicates:", pd.DataFrame(edges.values())["predicate"].value_counts().to_dict())


if __name__ == "__main__":
    main()
