"""Knowledge Router MVP（P0-1）。

路由顺序：Method（显式 knowledge_type=method）→ Local KG → 本地 miss →
Literature（litread，source_type=LITERATURE，provenance 带 EXTERNAL_LIVE）→
live 不可用时显式失败（不静默降级为"无知识"）。

source_type 五分且互斥：LOCAL_KG / EXTERNAL_LIVE / LITERATURE /
METHOD_KNOWLEDGE / CURRENT_STUDY。CURRENT_STUDY 只能由研究循环显式产出
（AgentLab/evidence 记录），Router 不检索当前研究结果，更不存在回灌 Local KG
的路径——本模块对图谱只读（KGGraph 无写接口），curated ingestion 是唯一
升级通道（knowledge/store 状态机）。

初始覆盖知识类型：phage_host / gene_protein_function / taxonomy /
pathway_annotation / literature；外部生物数据库通道（UniProt/Ensembl 等）
为 P2（BioTool schema 参考），未接入前如实标注。
"""
from __future__ import annotations

from typing import Any

from ..kg.graph import KGGraph
from ..kg.tools import build_tool_functions

SOURCE_TYPES = ("LOCAL_KG", "EXTERNAL_LIVE", "LITERATURE",
                "METHOD_KNOWLEDGE", "CURRENT_STUDY")
KNOWLEDGE_TYPES = ("phage_host", "gene_protein_function", "taxonomy",
                   "pathway_annotation", "literature", "method", "auto")


def route(graph: KGGraph, term: str, question: str,
          knowledge_type: str = "auto", hops: int = 1,
          max_results: int = 10) -> dict[str, Any]:
    """按知识类型路由一次检索；返回统一信封（source_type/provenance/evidence_status）。"""
    if knowledge_type not in KNOWLEDGE_TYPES:
        return {"status": "BAD_REQUEST",
                "reason": f"knowledge_type 须为 {KNOWLEDGE_TYPES} 之一",
                "source_type": None}
    if knowledge_type == "method":
        from .method_rules import search_method_rules
        rules = search_method_rules(question or term, k=5)
        return {"status": "OK", "knowledge_source": "method_kb(var/knowledge)",
                "source_type": "METHOD_KNOWLEDGE",
                "evidence_status": "validated_rule" if rules else "no_match",
                "provenance": {"package": "methods", "n_rules": len(rules)},
                "rules": rules}

    tools = build_tool_functions(graph)
    resolved = tools["kg_resolve"]({"term": term})
    if resolved.get("candidates"):
        neighbors = tools["kg_neighbors"]({"term": term, "hops": hops})
        entries = list(neighbors.get("neighbors", {}).values())[:max_results]
        tiers = sorted({via.get("evidence", {}).get("evidence_tier", "")
                        for e in entries for via in e.get("via", [])})
        return {"status": "OK", "knowledge_source": "local_kg_snapshot",
                "source_type": "LOCAL_KG",
                "term": term, "resolved": resolved,
                "neighbors": entries,
                "counts_by_category": neighbors.get("counts_by_category", {}),
                "evidence_status": ("tier_" + "_".join(t for t in tiers if t)
                                    if tiers else "resolved_no_edges"),
                "provenance": {"graph": "local_kg", "snapshot": "latest",
                               "evidence_tiers": [t for t in tiers if t]}}

    # Local miss → Live Literature（唯一已接 live 通道）；实体名做检索词（PubMed
    # 英文实体检索友好），question 只用于速读阶段
    try:
        from ..research.litread import search_and_read
        result = search_and_read(term, question, max_results=max_results,
                                 max_calls=0)  # Router MVP 只取文献元数据不做LLM速读
        prov = result.get("provenance", {})
        return {"status": "OK", "knowledge_source": prov.get("source_id", "ncbi-eutils-pubmed"),
                "source_type": "LITERATURE",
                "evidence_status": "external_abstracts",
                "provenance": {**prov, "underlying": "EXTERNAL_LIVE",
                               "route": "local_kg_miss→literature"},
                "n_papers": result.get("n_papers", 0),
                "papers": result.get("papers", [])[:max_results]}
    except Exception as exc:  # noqa: BLE001 —— live 不可用必须显式暴露
        return {"status": "LIVE_UNAVAILABLE",
                "reason": f"{type(exc).__name__}: {exc}",
                "source_type": "EXTERNAL_LIVE",
                "evidence_status": "retrieval_failed",
                "provenance": {"route": "local_kg_miss→literature(failed)"},
                "note": "外部生物数据库通道（UniProt/Ensembl 等）为 P2 规划，未接入"}


__all__ = ["KNOWLEDGE_TYPES", "SOURCE_TYPES", "route"]
