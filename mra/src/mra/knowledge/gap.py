"""Knowledge Gap Detector MVP（P1-1）。

从"会查知识"到"知道缺什么知识"：输入研究设计要素（实体 × 分析类型），
对每个要素做两类确定性探测——Local KG 覆盖、Method KB 覆盖——输出结构化
gap report 与逐项建议动作（query_local / consult_method_rules /
route_to_live / manual_design_review / accept_unknown），供 planner 在
设计阶段消费。

边界（MVP 声明）：
- 只探测与建议，不自动执行补齐（自动补齐闭环属 P1-2 planner 升级）；
- 对图谱与方法库只读；无任何写入路径；
- live 可达性不实测（不发起网络请求），miss 实体统一标记为 live 候选。
"""
from __future__ import annotations

from typing import Any

from ..kg.graph import KGGraph

ACTION_QUERY_LOCAL = "query_local"
ACTION_CONSULT_METHOD = "consult_method_rules"
ACTION_ROUTE_LIVE = "route_to_live"
ACTION_MANUAL_REVIEW = "manual_design_review"
ACTION_ACCEPT = "accept_unknown"


def detect_gaps(graph: KGGraph, entities: list[str], analysis_types: list[str],
                method_k: int = 3) -> dict[str, Any]:
    """对研究设计要素做知识覆盖探测，返回 gap report。"""
    entity_report: list[dict[str, Any]] = []
    for term in entities:
        hits = graph.resolve(term)
        if hits:
            entity_report.append({
                "term": term, "covered": True,
                "resolved": [{"id": n.id, "name": n.name, "category": n.category}
                             for n in hits[:3]],
                "knowledge_source": "LOCAL_KG",
                "recommended_action": ACTION_QUERY_LOCAL})
        else:
            entity_report.append({
                "term": term, "covered": False, "resolved": [],
                "knowledge_source": None,
                "gap": "local_kg_miss",
                "recommended_action": ACTION_ROUTE_LIVE})  # Router 降级通道已具备

    method_report: list[dict[str, Any]] = []
    for analysis in analysis_types:
        from .method_rules import search_method_rules

        def _probe(query: str) -> list[dict]:
            return [r for r in search_method_rules(query, k=method_k)
                    if r.get("structured")]

        # 整串查询（FTS 为 AND 语义，多词易因措辞差异漏检）→ 分片回退合并去重
        matched = _probe(analysis)
        if not matched:
            seen: set[str] = set()
            for frag in analysis.split():
                for r in _probe(frag):
                    if r["rule_id"] not in seen:
                        seen.add(r["rule_id"])
                        matched.append(r)
        if matched:
            method_report.append({
                "analysis_type": analysis, "covered": True,
                "n_rules": len(matched),
                "rule_ids": [r["rule_id"] for r in matched],
                "knowledge_source": "METHOD_KNOWLEDGE",
                "recommended_action": ACTION_CONSULT_METHOD})
        else:
            method_report.append({
                "analysis_type": analysis, "covered": False, "n_rules": 0,
                "knowledge_source": None,
                "gap": "no_validated_method_rule",
                "recommended_action": ACTION_MANUAL_REVIEW})  # 无验证规则：人工设计评审并事后沉淀新规则

    n_entity_gap = sum(1 for e in entity_report if not e["covered"])
    n_method_gap = sum(1 for m in method_report if not m["covered"])
    if n_entity_gap == 0 and n_method_gap == 0:
        overall = "no_gap_detected"
    elif n_method_gap == 0:
        overall = "entity_gaps_only"
    elif n_entity_gap == 0:
        overall = "method_gaps_only"
    else:
        overall = "mixed_gaps"
    return {"status": "OK", "source_type": "METHOD_KNOWLEDGE+LOCAL_KG_PROBE",
            "overall": overall,
            "n_entities": len(entity_report), "n_entity_gaps": n_entity_gap,
            "n_analysis_types": len(method_report), "n_method_gaps": n_method_gap,
            "entities": entity_report, "analysis_types": method_report,
            "note": "只探测不补齐；补齐动作由 planner 按 recommended_action 发起（P1-2）"}


__all__ = ["ACTION_ACCEPT", "ACTION_CONSULT_METHOD", "ACTION_MANUAL_REVIEW",
           "ACTION_QUERY_LOCAL", "ACTION_ROUTE_LIVE", "detect_gaps"]
