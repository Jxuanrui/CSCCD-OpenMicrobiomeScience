#!/usr/bin/env python3
"""MeSH descriptor 解析与 normalize_mesh（方案第四节，确定性零 LLM）。"""
import xml.etree.ElementTree as ET
from pathlib import Path
import json

MESH_FILE = Path(__file__).resolve().parents[2] / "data/sources/MeSH/desc2026"

_index = None

def _build_index():
    """一次性解析 MeSH XML，建 descriptor ID → (preferred_name, synonyms, tree_numbers) 索引。"""
    global _index
    if _index is not None:
        return _index
    _index = {}
    for event, elem in ET.iterparse(str(MESH_FILE), events=("end",)):
        if elem.tag != "DescriptorRecord":
            continue
        ui = elem.findtext("DescriptorUI", "")
        name = elem.findtext("DescriptorName/String", "")
        # MeSH 2026 格式：同义词在 ConceptList/Concept/TermList/Term/String
        synonyms = set()
        for concept in elem.findall(".//Concept"):
            for term in concept.findall(".//Term"):
                ts = term.findtext("String", "")
                if ts and ts != name:
                    synonyms.add(ts)
        synonyms = sorted(synonyms)
        trees = [tn.text for tn in elem.findall(".//TreeNumberList/TreeNumber") if tn.text]
        _index[ui] = {"preferred_name": name, "synonyms": synonyms, "tree_numbers": trees}
        elem.clear()  # 释放内存
    return _index

def normalize_mesh(object_raw: str) -> dict:
    """统一接口：object_raw（MeSH ID 或名称）→ 规范化记录。解析失败返回 resolved=False。"""
    idx = _build_index()
    raw = object_raw.strip()
    # 直接 UID 匹配（D#### / MESH:D####）
    uid = raw.replace("MESH:", "").replace("MeSH:", "")
    if uid in idx:
        d = idx[uid]
        return {"raw": raw, "mesh_id": uid, "preferred_name": d["preferred_name"],
                "synonyms": d["synonyms"][:10], "tree_numbers": d["tree_numbers"],
                "resolved": True}
    # 名称匹配（含同义词；折叠逗号差异——supp_bg P0-4 冻结回归发现的 3 条漂移根因：
    # 倒序名无逗号形式如 "Liver Diseases Alcoholic" 匹配不上标准名 "Liver Diseases, Alcoholic"）
    def _fold(s: str) -> str:
        return " ".join(s.lower().replace(",", " ").split())
    raw_fold = _fold(raw)
    for uid, d in idx.items():
        if _fold(d["preferred_name"]) == raw_fold:
            return {"raw": raw, "mesh_id": uid, "preferred_name": d["preferred_name"],
                    "synonyms": d["synonyms"][:10], "tree_numbers": d["tree_numbers"],
                    "resolved": True}
        if any(_fold(s) == raw_fold for s in d["synonyms"]):
            return {"raw": raw, "mesh_id": uid, "preferred_name": d["preferred_name"],
                    "synonyms": d["synonyms"][:10], "tree_numbers": d["tree_numbers"],
                    "resolved": True}
    return {"raw": raw, "resolved": False, "reason": "not_found_in_mesh_2026"}

if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        print(json.dumps(normalize_mesh(arg), ensure_ascii=False, indent=1))
