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
    # 按去前缀后的本地部分校验：空或 "*"（占位符）一律拒绝——
    # 修复 459 条 MESH:* 畸形边根因（监工 C2，2026-10-10）
    local = x[5:] if x.upper().startswith("MESH:") else x
    if not local or local == "*":
        return ""
    return x if x.upper().startswith("MESH:") else f"MESH:{x}"


# Disorder_Health 表的复合条件清洗（人工抽检发现的词表问题）：
# "A;B" 双联命名与 "MESH:*;D012345" 复合 ID 做确定性拆分——只保留携带有效
# MeSH ID 的疾病侧；Health/Diet/Age 等非疾病概念一律剔除。
NON_DISEASE = {"health", "healthy", "diet", "age", "*", ""}

# MeSH 树号缓存（关口③整改 2026-10-10）：星号行改挂的目标必须是疾病类概念
#（C=Conditions 或 F=精神障碍树；D=药物/E=手术/G=饮食/Z=地理等一律拒绝）。
import pandas as _pd
_TREE_CACHE = {}
_tc_path = Path(__file__).resolve().parents[2] / "data/seed/mesh_tree_cache.tsv"
if _tc_path.exists():
    for _r in _pd.read_csv(_tc_path, sep="\t").fillna("").itertuples(index=False):
        _TREE_CACHE[_r.uid] = str(_r.trees)

def _is_disease_concept(mid: str) -> bool:
    """MESH:Dxxxx 依树号判定是否疾病类（C/F 树；无缓存条目保守放行——仅源数据覆盖核验）。"""
    uid = mid[5:] if mid.startswith("MESH:") else mid
    trees = _TREE_CACHE.get(uid)
    if trees is None:
        return True
    return any(t.startswith(("C", "F")) for t in trees.split(";") if t)


def clean_condition(dname, dids):
    """返回 (清洗后名称, MeSH ID)；复合条件选首个有效疾病侧，无可留部分返回空。

    星号行（ID 列含 "*"，此前产出 MESH:* 被删除的对象）的任何重解析目标
    必须通过 _is_disease_concept 校验——关口③抽检实证改挂曾混入
    Patients/Cholesterol(0.2%)/Protease Inhibitors/Autoantibodies 等非疾病概念。
    """
    dname, dids = text(dname), text(dids)
    star_row = "*" in dids
    if ";" not in dname and ";" not in dids and "," not in dids:
        mid = mesh_id(dids)
        ok = mid and dname.lower() not in NON_DISEASE
        if ok and star_row and not _is_disease_concept(mid):
            return "", ""
        return (dname, mid) if ok else ("", "")
    nps = [x.strip() for x in dname.split(";") if x.strip()]
    ips = [x.strip() for x in re.split(r"[;,]", dids) if x.strip()]
    if len(nps) == len(ips):
        for n, v in zip(nps, ips):
            if n.lower() in NON_DISEASE:
                continue
            mid = mesh_id(v)
            if mid:
                if star_row and not _is_disease_concept(mid):
                    continue
                return n, mid
        return "", ""
    # 长度不齐：整体名与首个有效 ID 无法保证对应（关口③抽检实证混入
    # Patients/Cholesterol(0.2%, wt/wt)/Protease Inhibitors 等非疾病概念，
    # 2026-10-10 监工 P0-2 裁定改为拒绝——宁缺勿错配）。
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
