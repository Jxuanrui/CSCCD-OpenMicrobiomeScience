"""KG 受控工具层（JSON Schema 描述，供 agent tool-calling / MCP 绑定）。

遵循 workflow/tools.py 先例：工具=纯函数+JSON Schema 描述；图实例由调用方注入
（agent 会话内单例，避免每次调用重复加载快照）。M2 接入 GovernanceAgent。
"""
from __future__ import annotations

from typing import Any, Callable

from .graph import KGGraph

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "kg_resolve": {
        "description": "把菌名/疾病名/代谢物名等解析为图谱标准实体（NCBITaxon/MeSH/ChEBI 等 ID）。返回最多 5 个候选。",
        "parameters": {
            "type": "object",
            "properties": {"term": {"type": "string", "description": "实体名或 ID"}},
            "required": ["term"],
        },
    },
    "kg_neighbors": {
        "description": ("查询某实体 k 跳内（默认 1 跳）的跨域关联邻居，每条路径携带证据"
                        "（evidence_tier A/B、pmids、years、confidence）。回答必须引用证据等级。"),
        "parameters": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "已解析的实体 ID 或名称"},
                "hops": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                "categories": {"type": "array", "items": {"type": "string"},
                               "description": "类别过滤，如 [\"Disease\",\"Metabolite\"]"},
            },
            "required": ["term"],
        },
    },
    "kg_edge_evidence": {
        "description": "查询两个实体之间是否存在边及全部证据属性（tier/pmids/years/confidence）。",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "object": {"type": "string"},
            },
            "required": ["subject", "object"],
        },
    },
}


def build_tool_functions(graph: KGGraph) -> dict[str, Callable[[dict], dict]]:
    """绑定图实例，返回 可供编排层直接调用的工具函数集。"""

    def kg_resolve(args: dict) -> dict:
        hits = graph.resolve(args["term"])[:5]
        return {"candidates": [{"id": n.id, "name": n.name, "category": n.category} for n in hits]}

    def kg_neighbors(args: dict) -> dict:
        hits = graph.resolve(args["term"])
        if not hits:
            return {"error": f"unresolved term: {args['term']}"}
        cats = set(args["categories"]) if args.get("categories") else None
        return graph.neighbors(hits[0].id, hops=int(args.get("hops", 1)), categories=cats)

    def kg_edge_evidence(args: dict) -> dict:
        edges = graph.edge_evidence(args["subject"], args["object"])
        return {"edges": [{"predicate": e.predicate, "evidence": e.evidence()} for e in edges]}

    return {"kg_resolve": kg_resolve, "kg_neighbors": kg_neighbors,
            "kg_edge_evidence": kg_edge_evidence}
